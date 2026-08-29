# 1KeyTranscoder 硬件编码后端（NVEncC / QSVEncC）与 Sony 元数据保留管线 —— 设计与实测报告

> 状态：设计与实测完成（v1.0，含硬件后端 × 保留管线全链测试结果）
> 日期：2026-08（测试会话）
> 适用代码基线：1KeyTranscoder v0.3.0-beta（`main` @ `85840c5`）
> 测试机器：Windows 11（26200），Intel Core Ultra 9 285H（16C/16T），
> NVIDIA GeForce RTX 5070 Laptop（8 GB，driver 596.36），
> Intel Arc 140T 核显（16 GB，driver 32.0.101.8974）

---

## 1. 背景与目标

1KeyTranscoder 目前的视频编码后端只有 libx265（FFmpeg），NVENC/QSV/VCE
在 `encoders/base.py` 中仅定义了协议、在 `nvenc.json` / `qsv.json` /
`vce.json` 中已备好档位参数。本报告覆盖：

- **硬件后端落地**：以 NVEncC（rigaya）为第一后端、QSVEncC 为辅，复用
  已定稿的 `nvenc.json` / `qsv.json` 参数（不重新标定）；
- **元数据保留兼容性**：硬件后端替换 x265 后，Sony 保留管线
  （MP4Box 重建 → 结构校验 → Gyroflow 消费端校验）必须继续全绿；
- **落盘最小化**：削除中间产物冗余写入与成功后的无限残留；
- **老硬件兼容**：解码/编码能力探测 + 分级回退（软解 → 10bit420 →
  8bit420），目标吞吐区间 0.5–3x 实时。

所有结论均以本机实测为准；测试脚本与产物位于 `work/hw_preserve/` 与
`work/nvenc_smoke/`、`work/qsv_smoke/`。

---

## 2. 结论摘要

| # | 结论 | 证据 |
|---|------|------|
| 1 | rigaya 硬解 reader（NVEncC avcuvid/avhw、QSVEncC avqsv）对 Sony elst+priming 素材**稳定丢帧**（A7M5 360→357，A7M4 195→193），`:noedit`、`--avsync forcecfr/vfr`、`--offset-video-dts-advance` 全部无效 | 5.2 矩阵 |
| 2 | **`--avsw` 软解在两工具上均帧精确**（360/360、195/195），是保留路径的强制默认 reader | 5.2 |
| 3 | 软解吞吐：NVEncC 4K60 10bit420 ≈ 1.7x、10bit422 ≈ 2.3–2.4x；QSVEncC ≈ 1.3x（420）、1.0x（422 直编） | 5.3/5.4 |
| 4 | 4:2:2 HEVC 直编：NVENC 支持且无速度惩罚（2.4x）；QSV 支持但为慢速路径（1.0x），**QSV 上走 420 转换反而更快（2.0x）** | 5.4 |
| 5 | 直读（`--video-track`）+ MP4Box strip 回退均可行；strip 时序逐字节精确 | 5.2/6 |
| 6 | 能力探测无 JSON 出口（`--check-features-json` 不存在），需文本解析 + 缓存 | 4.5 |
| 7 | 硬件后端 × 保留管线全链 5/5 用例通过（A7M5×2 后端、A7M4×2 后端、29s 长样），A7M4 经 stts 时长补丁后 41/0/0 与 x265 基线一致，Gyroflow 全 PASS | 6 |
| 8 | 落盘最小化方案确定：成功后 GC + 去 NHML + hash 即删 + MP4Box 趟数合并 | 7 |

---

## 3. 现状架构回顾

### 3.1 两条编码路径

```
非 Sony 源 (经典路径):
  probe/分类/缩放 -> X265Backend.build_command (全流复制+主视频编码)
                  -> run_ffmpeg -> <basename>.MP4

Sony XAVC 源 (rtmd 数据流检测, 保留路径):
  probe -> GPAC demux (bundle) -> encode_video 回调 -> encoded.mov
        -> MP4Box -new (视频 + 逐轨音频容器复制 + 逐轨 rtmd 容器复制)
        -> reconstruct (payload 校验 / tref-cdsc / nrtm meta / brands)
        -> flatten -> uuid 字节补丁 -> validate.compare -> Gyroflow
        -> <basename>.MP4
```

### 3.2 后端接口事实

- `EncoderBackend.build_video_command()` 收到的是**原始源路径**，"
  剥离音频/元数据"由后端在命令内部实现（x265 用 `-map 0:v:0 -an -sn -dn`）。
  实测证据：源 6 流（1 video + 4 audio + 1 rtmd）→ `encoded.mov` 恰 1 流
  → 交付物 6 流，编码器全程只见纯视频帧。
- `run_ffmpeg()` 解析 FFmpeg 的 `frame=` 进度行；`count_frames()`
  （ffprobe `-count_packets`）实现 1:1 帧数/帧率硬校验。
- 保留管线经 `run_sony_pipeline(encode_video=...)` 注入编码回调，
  **天然后端无关** —— 这是硬件后端落地的最小侵入点。

### 3.3 容器规则（已验证，勿回归）

- 编码中间体必须是 MOV/ISOBMFF（MKV 毫秒量化破坏 1001/60000 对齐）；
- movie timescale 取**源 mvhd**（A7M5 60000 / A7M4 90000）并线程化到每个
  MP4Box 改写命令；
- rtmd/音频必须容器级直接复制（`-add src#<trackID>`），**禁用 NHML 导入**
  （600-tick 截断：360360@60000→360300）；
- `#audio` 只复制第一条音频轨，必须逐轨 `#<id>`；
- `-flat` 之后才能做 uuid 字节补丁（moov 必须在 mdat 后），brands 在
  `-flat` 之后设置。

---

## 4. 硬件后端总体设计

### 4.1 后端协议扩展

现状协议产出 FFmpeg argv。硬件后端需要三类扩展：

1. **argv 构造**：NVEncC/QSVEncC 各自 CLI（`-i/--video-track/--avsw/-c/-f/-o`）；
2. **进程执行与进度解析**：进度行格式不同（NVEncC/QSVEncC：
   `[xx.x%] N frames: F fps, B kbps, ...`），把"执行+解析"抽象为
   backend 的 runner（`run_encoder(cmd, raw_log, total_frames) -> rc`）；
3. **视频流选择**：`build_video_command` 目前只收 `src_info`，需增补
   视频轨序号/track id（由 MP4Box `-diso` 探测，见 4.2）。

后端选择挂在配置顶层 `"encoder"` 字段（`x265.json`/`nvenc.json`/
`qsv.json` 均已具备），或 `--encoder` CLI；日志中的 `EFFECTIVE_X265`
泛化为 `EFFECTIVE_<BACKEND>`，CSV schema 不变。

### 4.2 输入处理：直读 → strip 回退

```
1. MP4Box -diso 探测: 取 handler=vide 的首个视频轨 (track id / 视频轨序号)
2. 直读:  <工具> -i <原始源> --video-track <视频轨序号> --avsw ...
   (rtmd 数据轨与非视频流被 reader 忽略 — 实测 NVEncC/QSVEncC 均成立)
3. reader 失败 (读不了容器/超长流 ff 异常) ->
   MP4Box strip: -new stripped.mov -add <源>#<视频轨ID>
   (原生轨复制, 时序逐字节精确, E16 已验证) -> 重试编码
4. strip 文件用完即删 (落盘最小化); 失败保留供断点续跑
```

`--video-track` 语义 = "第 N 条视频轨（按分辨率排序）"，不是容器全局
track id —— 对多视频流源（DJI 封面图）反而更安全。

### 4.3 解码策略：avsw 强制默认 + 1:1 校验闸门

- **默认 `--avsw`（软解）**。硬解 reader 丢帧（5.2 矩阵），且与素材
  长度无关（6s 样片即复现）；
- 可选优化（实施时按需启用）：先 avhw，`count_frames` 1:1 校验不过则
  自动重试 avsw —— 现有 `encode_video` 的帧数/帧率校验天然是闸门；
- 老卡（Blackwell 前）NVDec 与 QSV 均无 4:2:2 HEVC 硬解，软解本来就是
  唯一路径，本策略同时覆盖。

### 4.4 编码格式与降级策略

按后端分化的 4:2:2 策略：

| 源格式 | NVEncC | QSVEncC |
|---|---|---|
| 10bit 4:2:0 | `--output-depth 10`（p010） | `--output-depth 10`（p010） |
| 10bit 4:2:2 | **`--output-csp yuv422` 直编**（2.4x，无色度损失） | **默认转 420**（2.0x；直编仅 1.0x，经济上不划算） |
| 8bit 4:2:2 | `--output-csp yuv422`（8bit 输出） | 默认转 420 |

通用降级链（能力探测不通过时逐级降）：源 chroma/depth →
**10bit 4:2:0** → **8bit 4:2:0**。每次降级是保真度事件：
- 必须写入 report.json（如 `video.chroma_downgrade`）、postprobe CSV
  与 per-file 日志；
- UHQ/HQ 档是否允许降级做成策略开关（默认"降级则显著告警"）。

### 4.5 能力探测与缓存

- NVEncC：`--check-features`（文本表，列 NVENC 各 codec 支持的
  chroma/depth）；QSVEncC：`--check-hw`（QSV 可用性）+ `--check-features`
  （另有 `--check-features-html`）。**两个工具都没有 JSON 出口**
  （`--check-features-json` 实测不存在，rc=1）。
- 缓存 JSON（如 `<output>/.1ktwork/caps/<backend>_caps.json`），
  **键 = 工具版本 + 驱动版本 + 设备名**（三者任一变化即失效重测）；
- 解码能力没有干净的枚举（NVDec/QSV 解码格式矩阵不完整），解码侧
  用"失败驱动 + memoize"，不依赖静态表。

### 4.6 失败分类与回退状态机

NVEncC/QSVEncC 失败时 rc 均为 1，必须按 **stderr 模式**分类，每层只
回退自己那一级，各层设重试上限（默认 1 次）：

```
读不了容器 / demux 失败   -> MP4Box strip -> 重试同一 reader
   (模式: "Failed to open" / "avcodec" / "Could not find" 等)
解码失败 (hw 不支持该格式)-> avhw -> avsw
   (模式: "Decoder" / "not supported" / "Failed to initialize")
   注意: QSVEncC 对不支持的格式会静默回退 avsw (Input Info 显示),
        不能依赖报错; NVEncC cuvid 可能"成功但丢帧" —— 都靠 1:1 校验兜底
编码格式不支持            -> 10bit420 -> 8bit420 (每次降级显式记录)
输出完整性                -> ffprobe nb_read_packets 校验 (生产 _encoded_ok
   已有, 曾捕获 NVEncC avsw 8bit 输出的 moov 异常)
```

---

## 5. 实测数据（冒烟矩阵）

### 5.1 环境

| 组件 | 版本 |
|---|---|
| NVEncC | 9.31 (r4047)，NVENC API v13.1，CUDA 11.8 |
| QSVEncC | 8.26 (r4504)，Intel Media SDK v2.16 |
| FFmpeg / ffprobe | 8.0.1 full build（gyan.dev，WinGet） |
| GPAC / MP4Box | 26.02-rev0-g118e60a9-master |
| Gyroflow | D:\Gyroflow-windows64 |
| Python | 3.14.7 |

测试源：A7M5 4K60 XAVC HS 10bit420（`20260823_C0886.MP4`，360 帧，
elst media_time=2002，rtmd 360 样本）；A7M4 4K30 XAVC S h264
**10bit 4:2:2**（`C9037.MP4`，195 帧，yuv422p10le）；A7M5 29s 长样
（`车内高晃动适中噪点.MP4`，1740 帧）。

### 5.2 帧精确性矩阵（A7M5，360 帧基准）

| 路径 | 结果 |
|---|---|
| x265/FFmpeg 基线（生产管线） | **360/360 ✅** |
| NVEncC 直读默认（avcuvid） | 357/360 ❌ |
| NVEncC `--avhw` | 357/360 ❌ |
| NVEncC `-b 0`（排除 B 帧因素） | 357/360 ❌ |
| NVEncC `--offset-video-dts-advance` | 357/360 ❌（且 mvhd ts 变 1000、elst 负 media_time） |
| NVEncC MP4Box strip 后硬解 | 357/360 ❌ |
| NVEncC strip `:noedit` 后硬解 | 357/360 ❌ |
| NVEncC `--avsync forcecfr` / `vfr` | 357/360 ❌ |
| NVEncC **`--avsw`** | **360/360 ✅** |
| QSVEncC 默认（avqsv） | 357/360 ❌ |
| QSVEncC strip 后 avqsv | 357/360 ❌ |
| QSVEncC avqsv + `--avsync forcecfr` | 357/360 ❌ |
| QSVEncC **`--avsw`** | **360/360 ✅** |

A7M4（195 帧基准）：NVEncC cuvid 硬解 **193/195 ❌**（丢 2 帧且不报错）；
NVEncC `--avsw` 195/195 ✅；QSVEncC 请求硬解 → **静默回退 avsw**
（无 h264 10bit422 硬解），195/195 ✅。

结论：丢帧与 B 帧、elst、容器长度均无关，是 rigaya 共享 reader 层对
Sony 结构（ctts/priming）的时间戳处理缺陷；**avsw 是帧精确的唯一路径**。

### 5.3 速度基准（4K60 HEVC 10bit 4:2:0，avsw，真实 29s 索尼源）

| 后端 | 吞吐 | 倍率 |
|---|---|---|
| NVEncC（RTX 5070，默认档） | 100.5 fps | 1.68x |
| QSVEncC（Arc 140T，默认档） | 79.6 fps | 1.33x |

注：早期 6s 短样测得的 64 fps 是启动开销假象（reader/编码器初始化
摊在 360 帧上），30s 级样本才反映稳态。NVEncC `--preset quality
--tune uhq`（nvenc.json HQ 档）短样吞吐 26.65 fps（含启动），稳态见
第 6 节长样用例。

### 5.4 4:2:2（合成 36s 4K60 HEVC 10bit 4:2:2，2160 帧）

| 后端 | 路径 | 吞吐 | 输出 |
|---|---|---|---|
| NVEncC | `--output-csp yuv422` 直编 | **142.6 fps（2.4x）** | Rext / yuv422p10le ✅ |
| NVEncC | 默认转 420 | 139.5 fps（2.3x） | yuv420p10le |
| QSVEncC | `--output-csp yuv422` 直编 | 59.9 fps（1.0x）⚠️ | Rext / yuv422p10le ✅ |
| QSVEncC | 默认转 420 | 117.1 fps（2.0x） | yuv420p10le |

### 5.5 能力表要点

**RTX 5070 NVENC**：HEVC 10bit / 4:2:2 / 4:4:4 编入格式全部支持；
H.264 4:2:2(10bit) 也在列（但 cuvid 硬解丢帧，见 5.2）。

**Arc 140T QSV**：HEVC 10bit ✅（CBR/VBR/QVBR/CQP/ICQ）；**LA 全 x**
（无 lookahead —— 注意 `qsv.json` 的 `lookahead` 项在该硬件上不可用，
LA 类能力需探测后降级或按机型跳过）；H.264 10bit 全 x（无硬编硬解）；
FadeDetect / Adaptive_I/B / WeightP/B / B_Pyramid / ManyBframes ✅；
SAO / Trellis / TSkip x（qsv.json 未依赖 ✅）。

### 5.6 异常记录

1. **NVEncC avsw + h264 10bit422 + 默认 8bit 输出**：一次出现 rc=0 但
   输出无 moov（ffprobe 拒读，MP4Box 可读）；`--output-depth 10` 复测
   正常。生产 `_encoded_ok()`（ffprobe 包数校验）恰好能捕获此类输出
   完整性缺陷 → 保留并作为硬闸门。
2. **QSVEncC 硬解不支持格式静默回退 avsw**（不报错）—— backend 必须
   显式指定 reader 并验证，不能依赖工具报错分类。
3. **NVEncC cuvid 对 h264 10bit422 "成功但丢帧"**（193/195）—— 比报错
   更危险，1:1 帧数校验是唯一可靠防线。
4. **`--preset quality` 在 NVEncC 9.31 可接受**（帮助文本仅列
   slower..draft，实测为兼容别名）；`--tune uhq` 合法。
5. `--check-features-json` 不存在（rc=1）；`--offset-video-dts-advance`
   不可用（见 5.2）。

### 5.7 参数面验证

- `nvenc.json` HQ 核心面（preset/tune/qvbr/max-bitrate/vbv-bufsize/
  aq/aq-strength/aq-temporal/lookahead/lookahead-level/bframes/bref-mode/
  ref/tf-level/nonrefp/mv-precision/chroma-qp-offset/gop-len/aud/
  repeat-headers/pic-struct）全部被 NVEncC 9.31 直接接受（保留管线
  用例以该参数面运行，见第 6 节）；`atc_sei/split_enc/parallel/
  output_buf/cuda_schedule/avoid_idle_clock/avhw` 属 AV1/并行类选项，
  未参与保留测试。
- `qsv.json` HQ 核心面（icq/bframes/ref/max-bitrate/vbv-bufsize/aud/
  gop-len）接受 ✅；`lookahead` → QSVEncC `--la-depth`（Arc 无 LA，
  见 5.5）。

---

## 6. 元数据保留全链测试（硬件后端注入）

> 数据来自 `work/hw_preserve/hw_preserve_test.py`：以 NVEncC/QSVEncC 替换
> x265 注入生产管线 `run_sony_pipeline`（MP4Box 重建 → validate →
> Gyroflow），参数面 = nvenc.json / qsv.json HQ 档核心项 + avsw +
> `--mux-option use_editlist:0`。

### 6.1 测试矩阵（最终结果）

| 用例 | 源 | 后端 | 原始 | 修正后（stts 补丁） |
|---|---|---|---|---|
| a7m5_nvencc | A7M5 4K60 XAVC HS 10bit420（360帧） | NVEncC HQ | 40/1/0 ✅（整数毫秒时长，无截断） | 40/1/0 ✅ PASS |
| a7m5_qsvencc | 同上 | QSVEncC HQ | 40/1/0 ✅ | 40/1/0 ✅ PASS |
| a7m4_nvencc_422 | A7M4 4K30 XAVC S h264 10bit422（195帧） | NVEncC HQ + 422直编 | 34/7/0 ❌ | **41/0/0** ✅ PASS |
| a7m4_qsvencc | 同上 | QSVEncC HQ（转420） | 34/7/0 ❌（截断方向相反：+0.5ms） | **41/0/0** ✅ PASS |
| a7m5_long_nvencc | A7M5 29s 4K60（1740帧） | NVEncC HQ | 39/1/0 ✅ | 39/1/0 ✅ PASS |

（x265 基线：A7M5 40/1/0、A7M4 **41/0/0**、全 PASS。唯一的
MODIFIED 项为已知非关键的 `nrtm.lens_profile.item_type`
00000000→mime，GPAC `-add-item` 固有限制。A7M4 补丁后与基线
逐项一致：0 MODIFIED。）

**结论：硬件后端 × 保留管线全链可用**，前提是管线 validate 之前对
rigaya 产物执行 stts 基准时长补丁（6.3 v4）。

### 6.2 根因：rigaya mp4 muxer 的毫秒级 edit list 量化

A7M4 的 7 个 MODIFIED 全是同一根因：**所有轨时长 585585 → 585540
@90000，恰短 45 ticks = 0.5ms**。box 级解剖：

| 中间体 | mvhd | video mdhd | video elst |
|---|---|---|---|
| x265/FFmpeg（基线，干净） | 30000/195195 | 30000/195195 | (195195, 2002) 全精确 |
| NVEncC mp4（默认，采用） | **1000**/6507 | 120000/784784 | (6506, 8008) ← **毫秒量化** |
| QSVEncC mp4（默认，采用） | **1000**/6507 | 120000/788788 | (6507, 16016) ← 同上 |
| NVEncC + `use_editlist:0`（否定） | 1000/6507 | 120000/780780 | 无 elst → GPAC 导入不一致 |

机理：rigaya 经 libavformat mp4 muxer 输出，mvhd 时基固定 1000（毫秒），
elst 段时长只能以整数毫秒表达。A7M4 源 195×1001/30000 = 6.5065s →
6506/6507ms，误差 0.5ms；GPAC 导入时以 elst 段时长为准计算轨时长
（6506ms → 585540@90000），并把该值传播到全部轨 tkhd/elst 与 mvhd。
A7M5 两源恰好是整数毫秒时长（6.006s、29.029s），故首轮侥幸通过——
**任何非整数毫秒时长（59.94/29.97fps 的常见长度）都会中招，属潜伏缺陷**。
Gyroflow 对 0.5ms 容差内放行，但 validate 的 rtmd.track_duration/
rtmd.elst 判定为 critical → structural_success=False。

### 6.3 修正路线（实测演进）

- **修正 v1（否定）`--mux-option use_editlist:0`**：去掉毫秒级 elst。A7M4
  仍截断（GPAC 导入时经中间体 mvhd 1000 时基量化），A7M5 反而恶化
  （GPAC 对无 elst 轨的导入不一致：视频轨时长错到 1449448@60000 = 4x
  错值，且 elst 不再合成）。GPAC 26.02 的无 elst 导入路径不可依赖。
- **修正 v2（否定）`-add ...:noedit`**：触发 GPAC 时基重标定缺陷
  （timescale 缩放但 mdhd 时长原值写入，视频轨 26.3s 错值）。
- **修正 v3（否定）`--avsync vfr`**：仍写毫秒级截断 elst。
- **修正 v4（采用）末端字节补丁**：保留 rigaya 默认输出，在管线 validate
  之前从 **stts 求和**（GPAC 样本表永远精确）重算每轨呈现时长：
  `isobmf.patch_track_durations(final, movie_ts, from_stts=True)`（新增
  from_stts 模式；原 mdhd 基模式保留给 NHML 场景）+ 新增
  `isobmf.patch_movie_duration(final)`（mvhd = max(tkhd)，validate 也
  比对 movie_duration）。纯 4/8 字节就地补丁，无 box 尺寸/偏移变化。
  实测 A7M4：6 处 585540→585585 + mvhd 同值 → 复验 **41/0/0**，与
  x265 基线一致；音频/rtmd 轨同样适用。
- **生产落地**：`encoders/nvencc.py`/`qsvencc.py` 输出保持默认（不碰
  use_editlist）；`preservation/pipeline.py` 在 `compare()` 之前对硬件
  后端产物无条件执行 v4 补丁（值已精确时为 no-op，天然幂等）。

### 6.4 其余观察

- A7M4 的 4:2:2 直编（NVEncC `--output-csp yuv422`）与 420 转换（QSV）
  在保留管线上均正常 mux/校验——视频轨 chroma 变化不影响 rtmd 对齐；
- NVEncC quality+uhq 档 4K60 编码吞吐 ≈ 25 fps（0.42x，29s 长样稳态），
  印证 0.5-3x 区间下限的构成：quality 档以速度为代价换质量；
- 29s 长样全链（含 Gyroflow 58058 IMU 样本比对）85.3s，其中编码 69s；
- 硬件后端用例中 GPAC "carried over" 部分 uuid box（A7M5 5/9、A7M4
  2/6），uuid 补丁只插入缺失项——管线现有逻辑对此天然兼容。

---

## 7. 落盘最小化设计

### 7.1 现状（逐任务写放大）

- rtmd 载荷（19456 B/帧，30min 4K60 ≈ 2.1 GB/份）被全量落盘 **5 份**：
  extract `samples.bin`、extract NHML `.media`、reconstruct 校验、
  validate original、validate final；
- 容器级全文件重写 **8 趟**（mux_new 1 + reconstruct 4 + flatten 1 +
  brand 1 + uuid 补丁 1）；
- 成功后 `.1ktwork/<job>` 永久保留（≈3–4× 终体积/任务）。

### 7.2 措施（按投入产出排序）

| # | 措施 | 收益 | 风险 |
|---|---|---|---|
| 1 | 交付成功后 GC `.1ktwork`（保留 report.json 入 logs，`--keep-work` 开关；失败任务保留） | 残留 4×→0 | 极低（交付后 resume 靠 dst 跳过） |
| 2 | 删除 NHML dump（重建不使用；manifest 复用逻辑容错旧字段） | 省 1 份载荷 | 低 |
| 3 | `samples.bin` 哈希即删（hash 在 manifest；validate 增 `known_facts` 复用 original 侧哈希） | 5 份→2 份（均瞬态） | 低-中（保持 hash 来源链） |
| 4 | 合并 MP4Box 重写趟数（reconstruct 4 命令并 1 调用；flatten+brand 合并） | 8 趟→3-4 趟 | **中：必须重跑 timing matrix + A7M4/A7M5 全链 + Gyroflow 回归** |
| 5 | 经典路径已最小；可选 `-stats_period` 缩小 ffmpeg 日志 | 微 | 无 |

GC 只允许发生在"交付 + postprobe 成功"之后；strip 回退的中间文件
（4.2）同样用完即删、失败保留。

---

## 8. 已知问题与风险清单

| 项 | 状态 | 说明 |
|---|---|---|
| 硬解 reader 丢帧 | 无解（工具层） | avsw 强制默认规避；NVEncC 升级后重测 |
| rigaya 中间体 mvhd 1000 毫秒级 elst 截断（0.5ms） | 已修复（工具：stts 基准补丁） | `isobmf.patch_track_durations(from_stts=True)` + `patch_movie_duration`，阶段2接入管线 validate 之前，幂等 |
| `nrtm.lens_profile.item_type` 00000000→mime | 已知非关键 MODIFIED | GPAC `-add-item` 固有限制；可选 isobmf 窄补丁清零 |
| QSV `qsv.json` lookahead 在 Arc 无 LA | 机型差异 | 能力探测后降级/跳过 |
| 经典路径（非 Sony）切硬件后端会丢字幕/附件/data 流（DJI covr/tmcd） | 决策待定 | 保留 x265 或接受丢失需拍板 |
| NVEncC avsw 8bit 输出偶发 moov 异常 | 已由 `_encoded_ok` 兜底 | 生产强制 10bit 输出时未复现 |
| x265 后端去留 | 决策待定 | 建议保留作无 GPU 回退 |
| `x265_scaling.json` 数值 | 按用户定稿处理 | 本报告不涉及标定 |

---

## 9. 实施路线图（最终定稿版）

```
S1  lens item_type 补丁 + 接入管线              → A7M5/A7M4 全 41/0/0
S2  落盘 GC(--keep-work)/去NHML/hash即删 + warning 框架
    + 能力探测缓存(工作目录, 每次启动查询存盘)
S3  NVENC/QSV 后端(Sony 路径) + stts 时长补丁入管线
    + 三段式降级链(见 10.2)                    → 硬件矩阵 5/5 = 41/0/0
S4  经典路径切硬件单趟(--audio-copy) + strip 回退
S5  MP4Box 趟数合并(8→4) + 全链回归 + 时序复验
```

每阶段验收标准：A7M5/A7M4 全链 validate 无 critical
MISSING/MODIFIED + Gyroflow PASS；降级链三档各一次真实触发验证
（含 FATAL 路径与 WARNING 落盘审计）；`--dry-run` 与 CSV 输出保持兼容。

---

## 10. 最终定稿（决策版，覆盖第 4 节中的早期草案）

### 10.1 六条总目标

1. A7M5 检查全通：lens `item_type` 补丁后全矩阵 **41/0/0**，无任何
   MODIFIED。
2. 硬件编码后端整合：NVEncC（主）+ QSVEncC（辅）覆盖 Sony 保留路径与
   非 Sony 经典路径，消费已定稿的 `nvenc.json`/`qsv.json`。
3. **硬件编码永不回退软件**：编码失败 → 格式降级链（源格式 →
   10bit420 → 8bit420），每级 WARNING；全失败 → 该文件 failed +
   完整 log + 报错退出（批处理继续，退出码非零）。无软件编码回退；
   x265 仅 `--encoder x265` 手动选项，永不自动选择。
4. **解码恒软解（`--avsw`）**：所有硬件路径 reader 固定软解，根除
   硬解丢帧/格式不支持/静默回退三类问题（5.2 矩阵）。
5. 落盘最小化默认开启：GC（`--keep-work` 测试开关）+ 去 NHML +
   hash 即删 + MP4Box 趟数合并；非 Sony 单趟直出。
6. 非 Sony = 普通素材：一律丢 metadata（仅视频+音频；第二视频流/
   字幕/附件/data 流丢弃；音频编码 muxer 不支持 → 跳过该轨 + WARNING
   不失败任务）。DJI 专线不在本期，作为独立路径预留。

### 10.2 三段式降级链（最终）

```
启动: 能力查询(--check-features, --codec hevc) → 解析
      → <output>/.1ktwork/caps/<backend>_caps.json + 原始文本
      解析失败 → 保守能力(8bit420) + WARNING

单文件:
 probe (ffprobe + MP4Box diso) → VFR 检测(avg vs r_frame_rate)
  ├─ VFR → WARNING + --avsync forcecfr (最近有理速率 CFR, 时长保真)
  └─ 格式规划: 源 chroma/depth × 能力矩阵
       ├─ 能力判定不可编码 → 【直接降级编码】, 无提示,
       │     WARNING + 三处记录(log/CSV/report);
       │     --no-downgrade 时改为跳过该文件
       └─ 能力判定可编码 → 直编
  └─ encode 尝试 (--avsw + --video-track 1)
       ├─ 读不了容器 → MP4Box strip(视频+音频) → 重试一次
       │     (strip 再失败 → 该文件 failed)
       ├─ 编码失败(格式/编解码类) → 【新开控制台显示错误 log 并询问】:
       │     主控制台保持进度干净; 辅助控制台 60s 无输入 → 自动回退;
       │     N → 中止(该文件 failed); 决策经 <work>/fallback_decision.txt 回传
       │     → 降级梯重试: 源格式 → 10bit420 → 8bit420
       │       (每级 WARNING + 记录, 已试层级跳过, 重试不再询问)
       │     全失败 → [FATAL] 该文件 failed, 完整 log
       ├─ 环境类失败(磁盘满/权限) → 立即 FATAL, 不降级重试
       └─ 成功 → Sony: 1:1 帧数/fps 校验 → MP4Box 重建 → stts 时长补丁
                    → lens item_type 补丁 → validate → Gyroflow
               经典: 输出有效性校验 → 交付
```

配套开关：`--auto-downgrade`（免窗口直接回退，批处理）、
`--no-downgrade`（遇降级跳过文件）、`--keep-work`、`--dry-run`
（不等待、只打印降级计划与 WARNING 预览）。无交互会话（无控制台/
CI）按"无反应"处理。

### 10.3 VFR 规范化的实现歧义（已裁定，可改）

"整数 CFR"采用**最近有理速率 CFR**：`--avsync forcecfr`（rigaya 原生，
实测存在），29.97 → 30000/1001，时长/音画保真、音频照常 copy、
Sony 1:1 校验通过。字面整数（29.97→30）会引入 0.1% 变速
（10 分钟 ≈0.6s 漂移），需重编音频才可接受，不做默认。

### 10.4 其余定稿项

- 能力缓存：每次启动查询存盘（免跨运行失效管理），键 = 工具版本 +
  驱动 + 设备名，写入 JSON 头部备查。
- GC：交付 + postprobe 成功后；report.json → `logs/preserve_reports/`；
  失败任务永不 GC；删除失败重试 3 次后 WARNING 不失败任务。
- 经典路径无 1:1 闸门（VFR 素材不误杀）；Sony 路径保留。
- 失败分类：格式/编解码类 → 降级梯；环境类 → 立即 FATAL；
  读容器类 → strip 回退。
- 非 Sony 日志头显式策略声明（"non-Sony: metadata dropped by policy"），
  避免误判为 bug。

---

## 附录 A：复现命令

```powershell
# 帧精确性矩阵 (NVEncC)
$n='F:\1KeyTranscoder\tools\NVEncC_9.31_x64\NVEncC64.exe'
$src='F:\1KeyTranscoder\testsets\a7m5_4k60p_265_10bit420_150m_xavchs_4ch\20260823_C0886.MP4'
& $n -i $src --video-track 1 --avsw -c hevc --qvbr 26 --output-depth 10 -f mp4 -o out.mp4

# MP4Box strip (回退路径)
& 'C:\Program Files\GPAC\mp4box.exe' -new stripped.mov -add "$src#video"

# 保留管线全链矩阵
python work/hw_preserve/hw_preserve_test.py            # 全部用例
python work/hw_preserve/hw_preserve_test.py a7m5_nvencc  # 单用例

# 能力探测 (无 JSON 出口, 需文本解析)
& $n --check-features
& 'F:\1KeyTranscoder\tools\QSVEncC_8.26_x64\QSVEncC64.exe' --check-hw
```

## 附录 B：关键代码位置索引

| 关注点 | 位置 |
|---|---|
| 后端协议 | `encoders/base.py` |
| x265 命令构造（剥离语义） | `encoders/x265.py::build_video_command` |
| 保留管线编排（encode_video 注入点） | `preservation/pipeline.py::run_sony_pipeline` |
| MP4Box 封装 | `preservation/gpac.py` |
| 结构校验 | `preservation/validate.py::compare` |
| Gyroflow 校验 | `preservation/gyroflow.py::check` |
| 帧数 1:1 校验 | `1keytransc.py::count_frames`（测试脚本内同名实现） |
| 时序矩阵（E1-E18） | `work/timing/timing_matrix.py`、`metadata_forensics/timing_investigation.md` |
| 本报告测试 | `work/hw_preserve/hw_preserve_test.py`、`work/nvenc_smoke/`、`work/qsv_smoke/` |
