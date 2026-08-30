# 1KeyTranscoder · AV1 实现可行性评估报告

> 评估日期：2026-08（会话实证 + 联网调研）
> 结论前置：技术链路**可行**（本机 NVENC/QSV/SVT 三条路径均已端到端实测）。
> 但经 XAVC 标准合规性复核后**调整定位**：
> **AV1 不默认集成 Sony 元数据保留管线**——XAVC 标准只定义 H.264/HEVC，
> 保留 XAVC brand 的 AV1 文件是伪标准产物，NLE/Catalyst/机身消费链不认；
> AV1 默认仅服务"经典路径"（非 Sony 素材，元数据本就按策略丢弃），
> XAVC 素材保持 HEVC 后端；"AV1+rtmd"仅作剥离 brand 的实验模式预留。
> 核心约束不变：**所有可行 AV1 编码路径只有 4:2:0**（8/10-bit）。

---

## 1. TL;DR

| 路径 | 工具 | 可用性（本机实测） | 4K60 实测吞吐 | 压缩效率 | 4:2:2 | 建议角色 |
|---|---|---|---|---|---|---|
| **A. NVENC AV1** | tools/NVEncC_9.31（已内置） | ✅ 实测通过 | **73 fps（>实时）** | 好（~20-30% 优于 HEVC） | ❌ 仅 4:2:0 | **非 XAVC 素材首选** |
| **B. QSV AV1** | tools/QSVEncC_8.26（已内置） | ✅ 实测通过 | 实时级 | 好 | ❌ 仅 4:2:0 | 非 XAVC 双后端/回退 |
| **C. SVT-AV1 软件** | tools/ffmpeg.exe 内置 libsvtav1 | ✅ 实测通过 | preset 8/6/4 = 27.9 / 11.4 / 7.3 fps | **最优** | ❌ v4.2.0 仅 4:2:0 | 非 XAVC 手动高压缩档 |
| D. VCE AV1 | VCEEncC（已内置） | 本机无 AMD GPU | — | 好 | ❌ 仅 4:2:0 | 预留 |
| E. libaom | ffmpeg 内置 libaom-av1 | 未实测 | 极慢（数倍于 SVT） | 最优 | ✅ Profile 2 支持 | 不推荐批量归档 |

收益量化：AV1 同画质比 HEVC 省约 20-30% 码率（比 H.264 省 40-50%）。
对 150Mbps XAVC-HS 4K60 素材，同等画质下体积可从 HEVC 的约 1/4 压到约 1/5。
代价：4:2:2 源必须降采样色度；软件路径 4K60 仅 0.12-0.46x 实时。
**注意：以上收益只对"经典路径"（非 Sony 素材）成立——XAVC 素材的默认
归档路径仍为 HEVC（见下一节决策）。**

---

## ★ XAVC 标准合规边界：AV1 与 metadata 的集成决策（定位修正）

**用户结论复核通过，原"默认集成"方案已废弃。** 依据：

1. **XAVC 标准只定义 H.264/HEVC**（[Wikipedia/XAVC](https://en.wikipedia.org/wiki/XAVC)：
   XAVC 的编解码器为 MPEG-4 AVC/H.264 与 H.265/HEVC）。保留管线会把源文件的
   major_brand（XAVC）+ 兼容 brand 写回输出——若视频换成 AV1，产物就是
   **"自称 XAVC 的非 XAVC 文件"**，违反标准模式，按 brand 解析的消费端
   （机身、Catalyst、NLE 插件链）会直接误判。
2. **NLE 生态对 AV1 仍是早期支持**：Premiere 近年版本才开始支持 av01
   导入（GPU 辅助时间线播放逐步改善，[av01 支持演进](https://lifestyle.assist-all.co.jp/av01codec-guide-comparison-optimization/)）；
   Resolve 19/20 有 AV1 解码/导出（依赖平台与 GPU）；Final Cut/macOS 侧
   更弱；**Sony Catalyst Browse 走 XAVC 生态，不认 AV1**（2026.1 仍以
   XAVC 为主，[Catalyst Browse 2026.1](https://www.warp2search.net/story/sony-catalyst-browse-20261-released/)）。
   对"归档→NLE/Gyroflow 消费"工作流，AV1 输出会制造兼容障碍。
3. rtmd/Gyroflow 消费链的"标准模式"是 XAVC 容器语义的一部分；保留 rtmd
   却换 AV1、同时剥 brand，属于自定义容器，只有 Gyroflow（ffmpeg 解码）类
   工具能消费，NLE 与机内回读均不可用。

**修订后的集成策略：**

| 素材类型 | AV1 后端行为（默认） | 说明 |
|---|---|---|
| 非 Sony（无 rtmd） | ✅ 走经典路径，元数据按既有策略丢弃 | AV1 收益吃满，无标准冲突 |
| Sony XAVC（有 rtmd） | ❌ **不保留元数据**：默认路由到经典路径（元数据丢弃 + 显著 WARNING），或直接跳过（策略二选一，建议前者） | 保住 XAVC 标准模式与消费链 |
| Sony XAVC（有 rtmd） | 🧪 实验开关：AV1 + 保留 rtmd 但**剥离 XAVC brand**（改为 isom），三重 WARNING + 文档声明"非 XAVC 标准文件" | 仅 Gyroflow 类单一消费场景 |
| 所有 Sony 素材 | HEVC 后端（nvenc/qsv/x265）仍是**唯一默认保留路径** | 不变 |

**结论：AV1 在本项目中的定位从"候选默认编码"降级为"非 XAVC 素材的
备用后端 + 实验档"；Sony 元数据保留管线的默认 codec 恒为 HEVC。**
这同时缩小了 P1 范围：无需为 Sony 管线做 av01 断言泛化（经典路径不触发
validate/selfcheck 的 hevc 断言），泛化仅作为实验模式的前置保留。

---

## 2. 参考资料分析

### 2.1 `iavoe.github.io/av1-web-tutorial` —— 重要更正

该教程是**以 HTML 发布的桌面端 SVT-AV1 编码原理教程**（约 1MB 单页），
**不是浏览器端 WebCodecs 教程**：全文 0 命中 `WebCodecs/VideoEncoder/
MediaRecorder/wasm/WebRTC`，主体是 SVT-AV1（297 次提及）+ ffmpeg +
AviSynth/VapourSynth。可复用的干货：

- 流程总览：前瞻 → 分帧 → 粗分块 → 帧间/帧内预测 → 细分块 → 变换 →
  量化 → 熵编码 → 环路滤镜（AV1 特有的 CDEF / 还原滤波 / 胶片颗粒合成 /
  参考缩放 / 超分环路）。
- 参数结论：
  - `--preset 13~7` 实时/快速，`6~4` 常规，`3~1` 高压缩，`-1` Research；
  - **`--keyint` 推荐取 mini-GOP（2^层级数）倍数 +1**，且 <300 利于硬解；
  - `film-grain` 合成是 AV1 特色（低码率保颗粒质感）；
  - `--sharpness` 实测常适得其反（默认 0 即可）；
  - `--enable-tf` 与 `--enable-overlays` 互斥；`--enable-qm` 搭配 tune 选择；
  - 10bit y4m 管道需 `-strict -1`；raw/YUV 直导必须与 `--input-depth` 一致（否则绿屏）。
- 附录给出 VMAF / XPSNR / SSIMULACRA / Butteraugli 校验方法，可直接用于
  本项目 AV1 档位的画质验收。

### 2.2 `SVT-AV1/Docs/Parameters.md`（master = v4.2.0，2026-07-14）

本地已存档于 `docs/reference/svt-av1/`：`SVT-AV1_Parameters.md` /
`SVT-AV1_CHANGELOG.md` / `SVT-AV1_CommonQuestions.md`（抓取自官方 GitLab）。

- **色彩格式（决定性）**：`--color-format` 官方注明 *"only yuv420 is
  supported"*，`--input-depth` 8/10。即 **SVT-AV1 4.2.0 不能编码 4:2:2/4:4:4**，
  输出恒为 Profile 0 (Main)。要保 4:2:2 只能上 libaom（Profile 2），速度不可接受。
- 速率控制：`--rc 0` = CRF（`--crf` 1-70，默认 35，等价 aq-mode 2）或 CQP；
  `--rc 1` VBR；`--rc 2` CBR。归档建议 CRF。
- `--tune` 0-5（0=VQ 默认、1=PSNR、2=SSIM、3=IQ、4=MS-SSIM、5=VMAF）；
  归档建议 `tune 0`。
- `--keyint` 默认 -2（≈5s）、`--lookahead` 自动、`film-grain` 0-50、
  `--lp` 并行度（v3.0 起取代 LogicalProcessors）。
- 归档参数组合（官方文档+教程共同支持）：
  - 画质优先：`preset 2-4 + CRF 18-24 + tune 0 + yuv420p10le`
  - 体积优先：`preset 4-6 + CRF 26-30 + film-grain 12-15 + enable-qm 1`
  - 速度优先：`preset 7-10 + tune 1 + fast-decode 1 + tile-columns`
- Windows 二进制：官方只发源码；本项目**已内置的 gyan.dev ffmpeg 9.0.1
  full build 自带 libsvtav1**，无需新增任何依赖（实测
  `Supported pixel formats: yuv420p yuv420p10le`）。

### 2.3 硬件与生态结论（联网调研，检索时点 2026 年中）

**代际支持矩阵**（来源：rigaya GPUFeatures、NVIDIA 官方矩阵、厂商公告）：

| 厂商 | AV1 硬编起点 | AV1 硬编格式 | 本机 |
|---|---|---|---|
| NVIDIA NVENC | Ada Lovelace（RTX 40）+；RTX 30 仅解码 | 4:2:0 8/10bit（nv12/yv12/yv12(10bit)） | ✅ RTX 5070 Laptop（Blackwell） |
| Intel QSV | Arc Alchemist/Battlemage、Meteor Lake/Arrow Lake+；Tiger Lake 仅解码 | 4:2:0 8/10bit | ✅ Arc 140T（Arrow Lake） |
| AMD AMF | RDNA3/VCN4（RX 7000）+；RDNA4/VCN5 增强（B 帧） | 4:2:0 8/10bit（NV12/P010） | ❌ 本机无 AMD GPU |

**关键生态事实**：
- 三大硬件 AV1 编码器**全部仅 4:2:2/4:4:4 之外的 4:2:0**；AV1 High/Professional
  profile 无任何硬件编码实现。4:2:2 源（Sony XAVC）硬编 AV1 必须色度降采样。
- rigaya 工具：**不存在 SVTAV1EncC 这类独立 CLI**（其 SVT-AV1 产物是
  AviUtl 插件 svtAV1guiEx）；软件 AV1 走官方 `SvtAv1EncApp`/ffmpeg libsvtav1。
  NVEncC 最新 9.33（本机 9.31）、QSVEncC 8.28（本机 8.26）、VCEEncC 9.14
  （本机 9.12）——建议实施前顺手升级 tools/ 下三个工具。
- QSV AV1 注意：Arc 上 AV1 只走 **LP/FF（低功耗固定功能）路径**，PG 路径
  全 x；实际可用 RC 仅 **CBR/VBR/CQP/ICQ**（AVBR/QVBR/LA 系列均为 x）。
  早期驱动有 AV1 时间戳/稳定性 bug（QSVEnc #87/#96、vpl-gpu-rt #253），
  可用 `--function-mode FF`/`--fixed-func` 显式固定。
- NVENC AV1 RC：CQP/CBR/CBRHQ/VBR/VBRHQ（+qvbr/multipass 旗标），HDR
  元数据旗标 `--max-cll`/`--master-display`。
- av1an v0.5.2：分块并行框架（可多实例并行 + `--target-quality` VMAF 目标
  质量模式）——若未来认真做 SVT 软件档的批量提速可引入，非必需。
- SVT-AV1-PSY v3.0.2（2025-04）已声明停止大版本演进、特性逐步回归官方
  主线（官方 v4.2.0 已整合心理视觉特性），**不建议依赖 fork**。
- 解码：dav1d 1.5.x 支持 AV1 全部特性（含 4:2:2/4:4:4、8/10/12bit）软解；
  ffmpeg 默认集成。但**浏览器对 AV1 4:2:2/4:4:4 基本不支持**（硬解也普遍
  只做 4:2:0）——4:2:0 Main 10 输出对分发/播放最安全。
- 软件编 4:2:2 AV1 唯一选择 = libaom（SVT-AV1 源码 EbDefinitions.h 明确
  仅 Profile 0 4:2:0/4:0:0），速度对批量归档不可行，仅理论保留。

---

## 3. 本机实证（`work/av1_feasibility/`）

### 3.1 工具链能力探测（全部为真实命令输出）

| 工具 | AV1 编码 | 10-bit | 4:2:2 | 备注 |
|---|---|---|---|---|
| NVEncC 9.31 / RTX 5070 Laptop | ✅ | ✅ 8/10 | ❌ | RC: CQP/CBR/VBR（help 另列 AV1 qvbr 0-63）；B 帧上限 31、B Ref Mode 7、最大 8192×8192 |
| QSVEncC 8.26 / Arc 140T | ✅ | ✅（CBR/VBR/CQP/ICQ 全列 o） | ❌ | `--profile main/high/pro`、`--tier main/high`、tile-row/col；AV1 仅 LP/FF 路径，QVBR/LA 系列为 x |
| VCEEncC 9.12 | —（本机无 AMD GPU，探测无输出） | — | — | RDNA3+ 支持 AV1（预留） |
| ffmpeg 9.0.1 (gyan full) | ✅ libsvtav1 / libaom-av1 / librav1e / av1_nvenc / av1_qsv / av1_amf / av1_mf / av1_d3d12va / av1_vaapi / av1_vulkan | ✅ | ❌（libsvtav1 仅 420p/p10le） | 解码器含 libdav1d |
| GPAC 26.02 (MP4Box) | ✅ av01 读/写封装（实测） | ✅ | ✅（封装与采样无关） | 本项目容器重建核心 |

NVEncC AV1 关键旗标（`--help` 实测）：`--profile main|high`、
`--tier main|high`、`--level auto/2/2.1/.../6.1`、`--output-depth 8|10`、
`--cqp/--qvbr/--vbr-quality`（AV1 为 0-63）、`--aq/--aq-strength/--aq-temporal`、
`--lookahead 1-32`、`--bframes`（AV1 ≤31）、`--bref-mode`、`--ref`、
`--gop-len`、AV1 专属 `--tile-columns/--tile-rows`、`--refs-forward/--refs-backward`、
`--part-size-min/max`、`--bitstream-padding`。

### 3.2 端到端链路实测（全部通过）

1. **NVEncC AV1**（`--avsw -c av1 --output-depth 10 --cqp 26 -f mp4`）→
   MP4Box `-new x.mov -add x.mp4#video` → ffprobe：`codec_name=av1,
   codec_tag_string=av01, profile=Main, pix_fmt=yuv420p10le` ✅
2. **ffmpeg libsvtav1**（`-preset 6 -crf 32 -pix_fmt yuv420p10le -f mp4`）→
   同上重封装 ✅
3. **QSVEncC AV1**（`--avsw -c av1 --output-depth 10 --cqp 26 -f mp4`）✅
4. **libdav1d 解码**重封装后的 av01 MOV ✅（`-c:v libdav1d -f null -` 无错）

即：现有管线中"视频-only 中间文件 + MP4Box 重建容器 + stts 时长修复
（`isobmf.py` 无任何 hvc1/hev1/codec 假设，机制完全 codec 无关）"的骨架
对 AV1 **零结构性障碍**。

### 3.3 性能基准（本机，4K60 10-bit 4:2:0 测试片段，240 帧）

| 编码器 | 参数 | 耗时 | fps | 相对实时(60p) |
|---|---|---|---|---|
| NVEncC AV1（硬） | cqp 30 | 3.3s | **73.0** | 1.22x |
| libsvtav1 preset 8 | crf 32 | 8.6s | 27.9 | 0.46x |
| libsvtav1 preset 6 | crf 32 | 21s | 11.4 | 0.19x |
| libsvtav1 preset 4 | crf 32 | 33s | 7.3 | 0.12x |

结论：硬件路径完全满足批量归档吞吐；软件路径 4K60 在 16 线程 Arrow Lake
上约 5-8 倍实时，适合"手动高压缩档"或 `--jobs 1` 夜间批量，不适合默认档。

---

## 4. 约束与权衡（归档场景逐条）

0. **XAVC 标准合规（决策性，见上节）**：XAVC 只定义 H.264/HEVC；AV1 不得
   进入 Sony 保留管线的默认路径。AV1 后端遇 Sony 源默认走经典路径（丢元数据
   +WARNING）。"AV1+rtmd"仅实验模式（剥 brand）。
1. **4:2:2 → 4:2:0 强制降级（技术性）**：testsets 实测 a7m4/a7m5 XAVC-S
   均为 `yuv422p10le`（h264 High 4:2:2）。AV1 全路径（含硬件、SVT、rav1e）
   无 4:2:2，只能走现有降级梯（4:2:2→4:2:0 10bit，显著 WARNING + 三处记录，
   `--no-downgrade` 可拒绝）。XAVC-HS（yuv420p10le）无此损失。
   **对色度细节敏感的专业素材，HEVC 4:2:2 仍是保真上限**——建议 AV1 作为
   可选档位而非默认替换。
2. **消费端兼容性**：AV1 4:2:0 Main 10 流 dav1d 全解（实测）；主流播放器/
   浏览器对 av01 4:2:0 支持成熟（Chrome/Edge/Firefox 硬解自 2020-2022 起
   普及）。但 **Sony 相机机身不识 AV1**——回传卡内不可用，仅作归档/网盘用途。
3. **软件路径速度**：见 3.3；SVT preset 6 压缩率比 x265 好 20-30% 但 4K60
   仅 0.19x 实时。
4. **码率控制差异**：NVENC AV1 = CQP/CBR/VBR（QVBR 待实测）；QSV AV1 =
   CBR/VBR/CQP/ICQ；SVT = CRF。现有 UHQ 档的 QVBR 思路在 NVENC AV1 上
   需改 CQP 或 VBR（等价质量点需重标定）。
5. **film-grain**：仅软件路径有（SVT `film-grain`/`--enable-tf` 类工具）；
   硬件 AV1 无颗粒合成。高噪点素材（项目 testsets 中有"夜间高噪点"样例）
   用硬件 AV1 时需保留足够码率。
6. **Gyroflow 消费端校验**：Gyroflow 内部经 ffmpeg 解码（dav1d 可解 av01），
   预期可用，但列为上线前必测项。

---

## 5. 代码集成改动点（逐文件）

### 5.1 核心：能力探测 `encoders/caps.py`

- 现仅解析 `H.264/AVC`、`H.265/HEVC` 段（`_parse_nvenc`/`_parse_qsv`），
  且 `supports()`/`downgrade_ladder()`/`probe_backend()` 以 `"hevc"` 为键。
- 改动：新增 `_parse_av1()`（nvenc 的 `Codec: AV1` 块：RC Modes/4:2:2/4:4:4
  行；qsv 的 `Codec: AV1 FF` 块：`10bit depth` 行），codec 键增加 `"av1"`，
  `supports(caps, codec, chroma, depth)` 参数化；AV1 恒 `csp_422=False`。

### 5.2 后端类 `encoders/nvencc.py` / `encoders/qsvencc.py`

- `build_args()` 中 `"-c", "hevc"` 硬编码 → 参数化 `codec`（默认 hevc 保持
  行为不变；新增 `NvencAv1Backend`/`QsvAv1Backend` 薄子类或实例参数）。
- `--output-csp yuv422` 分支对 AV1 永不触发（恒 4:2:0）。
- PARAM_MAP 复用：不存在的旗标由 `known_flags()` 白名单自动跳过+WARNING
  （如 HEVC 的 `aud/repeat-headers/pic-struct/tf-level/mv-precision` 在 AV1
  预设 JSON 中直接不写即可；AV1 新增 `tile-columns/tile-rows/refs-*` 键）。

### 5.3 注册与调度 `core/batch_hw.py`

- `hw_backend_for()`（L189）：`encoder_name → kind/exe` 映射增加
  `"nvenc-av1"→("nvencc","NVEncC64.exe")`、`"qsv-av1"→("qsvencc","QSVEncC64.exe")`。
- `plan_initial_format()`（`encoders/hw.py` L313）：AV1 策略 =
  "4:2:2 一律计划 4:2:0 10bit + 降级标记"（同 QSV 策略，WARNING 措辞说明原因）。
- 自适应调度/双后端：`--experimental-multihw` 结构可平移到 nvenc/qsv 的
  AV1 变体，零新机制。

### 5.4 入口 `1kt.py`

- `--encoder choices`（L584）加 `"nvenc-av1","qsv-av1"`（可选 `"svt-av1"`）。
- JSON 映射表（L703-705）加 `"nvenc-av1": "nvenc_av1.json"` 等。
- `is_hardware`（L734）加新名；后端名进日志/CSV/看板（已走 `backend.name`）。
- 新增实验开关（默认关）：`--av1-keep-rtmd-experimental`（AV1 后端遇 Sony 源
  时保留 rtmd 但剥离 XAVC brand + 三重 WARNING）。

### 5.4b 素材路由门控（本次定位修正的核心改动，`core/batch_hw.py`）

- `process_file_hw` 分发处（L978 `is_sony_source`）：AV1 后端遇 Sony 源 →
  默认走 `encode_one_hw_classic`（元数据按策略丢弃）+ 显著 WARNING
  （"AV1 与 XAVC 标准不兼容，元数据不保留；XAVC 归档请用 hevc 后端"）；
  仅当实验开关开启时进入新实验分支（AV1+rtmd+剥 brand）。
- 该门控同时豁免了 P1 对 preservation 校验断言的强依赖（经典路径不触发
  validate/selfcheck 的 hevc 断言）。

### 5.5 校验断言泛化（降级为可选，仅实验模式需要）

- `preservation/validate.py` L228-237：`video.codec` 项 `codec_name=="hevc"`
  → 接受 `{"hevc","av1"}`（并修正文案 "libx265 intermediate" → 按后端）。
- `preservation/selfcheck.py` L225-234：`stsd ∈ ("hvc1","hev1")` → 加 `"av01"`；
  L257-265：`codec_name=="hevc"` → 加 `"av1"`。
- `preservation/checker.py`：无 codec 假设，零改动（已 grep 确认）。
- `preservation/isobmf.py`：无 codec 假设，零改动（已 grep 确认）。
- **按新定位，默认 AV1 路径（经典路径）不触发这些断言**；仅当实验模式
  （AV1+rtmd）上线时才必须做。

### 5.6 预设 JSON（新增）

- `nvenc_av1.json`：UHQ/HQ/SMALL/FAST，`"codec":"av1"`，profile main、
  tier main、level 6.1、output_depth 10、CQP 标定（初始可用 NVEncC 的
  `--vbr-quality/--qvbr` 0-63 或 CQP）+ aq/lookahead/bframes/bref/tile。
- `qsv_av1.json`：ICQ/QVBR 路线 → 实际以 **CBR/VBR/CQP/ICQ** 为准（Arc 的
  AV1 FF 特性表显示 QVBR/LA 系列为 x），建议 UHQ/HQ 用 ICQ、FAST 用 CQP，
  并固定 `--function-mode FF` 规避 LP/PG 切换的驱动 bug。
- （可选）`svt_av1.json` + `encoders/svtav1.py`：完全照 `x265.py`/X265Backend
  的 ffmpeg 命令构造模式，`-c:v libsvtav1 -preset N -crf M -svtav1-params
  "tune=0:keyint=10s:..."`，输出 MOV，`fix_hw_timing=False`（ffmpeg 中间件
  无 stts 修复需求）。CQP/CRF 标定用 3.3 基准 + 附录校验指标。

### 5.7 文档

README 依赖表/目录结构、`docs/design/hardware_backend_design.md` 补 AV1 章节、
`docs/design/implementation_report.md` 补演练记录。

---

## 6. 预设 JSON 草案（nvenc_av1.json，待标定）

```jsonc
{
  "encoder": "nvenc-av1",
  "codec": "av1",
  "profile": {
    "UHQ":  { "preset": "quality", "profile": "main", "tier": "main",
              "level": "6.1", "output_depth": 10, "cqp": 22,
              "aq": true, "aq_strength": 6, "aq_temporal": true,
              "lookahead": 32, "bframes": 8, "bref_mode": "hierarchical",
              "ref": 6, "gop_len": 0, "tile_columns": 2 }
    // HQ/SMALL/FAST 同构递减；aud/repeat-headers/pic-struct/tf-level 等
    // HEVC 专属键一律不写（known_flags 白名单机制兜底）
  }
}
```

---

## 7. 建议路线图（按 XAVC 边界修订）

| 阶段 | 内容 | 工作量 | 前置 |
|---|---|---|---|
| **P1（推荐）** | NVENC AV1 后端**仅经典路径**（5.1-5.4 + 5.4b 门控 + 5.6 预设标定，不含 5.5 断言泛化）；XAVC 素材默认保持 HEVC 后端 | 小（~半天） | 无 |
| **P2** | QSV AV1 同构 + `--experimental-multihw` 扩展（AV1 双后端并行） | 小 | P1 |
| **P3（可选）** | SVT-AV1 软件档（`--encoder svt-av1`，非 Sony 素材高压缩） | 小 | P1 |
| **P4（实验，不推荐默认）** | `--av1-keep-rtmd-experimental`：AV1+rtmd 保留但剥离 XAVC brand + 5.5 断言泛化 + Gyroflow 实测；文档明示"非 XAVC 标准文件、NLE/机身不保证" | 中 | P1 |
| **P5（预留）** | VCE AV1（AMD RDNA3+ 机器）；libaom 4:2:2 实验档 | 中 | — |

## 8. 风险清单

- **XAVC brand 合规**：任何"AV1 视频 + XAVC brand"的组合都是伪标准文件；
  默认路径已规避（AV1 不碰 Sony 保留管线）；实验模式必须剥 brand 并显式声明。
- NVENC AV1 的 lookahead/B 帧上限在笔记本 SKU 上可能与桌面不同 → 靠
  known_flags + 运行时降级梯兜底（机制已存在）。
- 4:2:2 素材降 4:2:0 是**不可逆的保真损失**，默认档若切 AV1 必须显式告知
  用户（README + 运行时 WARNING 已具备）。
- AV1 档位码率标定不能照搬 HEVC 的 QVBR 数值（AV1 用 0-63 标尺）→ 用
  测试集 + VMAF/SSIMULACRA 重标。
- 硬件 AV1 无 film-grain 合成，高噪点素材体积收益打折。
- QSV AV1 仅 LP/FF 路径且驱动历史上有时间戳/稳定性 bug（QSVEnc #87/#96）：
  实施时升级 QSVEncC 至 8.28+、显式 `--function-mode FF`，并在 P2 用
  testsets 全链回归。
- `fix_hw_timing` 的 stts 修复对 av01 中间件未经真实 Sony 全链验证 →
  P1 用 testsets 的 a7m5 4K60 样例跑完整保留管线（含 Gyroflow full check）。
- 工具版本：本机 NVEncC 9.31/QSVEncC 8.26 较最新 9.33/8.28 落后，升级属
  低风险顺手项（白名单机制对新旧旗标均兜底）。

---

## 附录 A. 浏览器/Web 端 AV1（供参考，非本项目主路径）

用户提供的教程并非 WebCodecs 内容（见 2.1）。若未来需要网页端编码器：

- Chrome/Edge：WebCodecs `VideoEncoder` 支持 `av01.*`（[codec 串参考
  webcodecsfundamentals.org](https://webcodecsfundamentals.org/codecs/av01.0.15H.10.html)，
  支持情况见 [caniuse WebCodecs](https://caniuse.com/webcodecs)）；
  有 GPU 硬编时走硬编，无则 libaom 软回退（4:2:0，Chromium media 侧启用
  AV1 编码的提交可查 chromium.googlesource.com）。
- Firefox 130+ 有 WebCodecs，但 VideoEncoder 编码支持有限；
  Safari 至今只有 VideoDecoder，无 VideoEncoder。
- 本项目为 Windows CLI 批量归档工具，Web 端编码与现有"元数据保留管线"
  （GPAC/rtmd/uuid 字节级修补）无法打通，不建议作为实现路径；
  若做，只适合"无元数据的快速压小件"场景。

## 附录 B. 本地存档与产物

- 文档抓取：`SVT-AV1_Parameters.md`、`SVT-AV1_CHANGELOG.md`、
  `SVT-AV1_CommonQuestions.md`（docs/reference/svt-av1/）。
- 实证产物：`work/av1_feasibility/`（hw_av1.mp4/mov、sw_av1.mp4/mov、
  qsv_av1.mp4、4K60 基准文件与日志）。
