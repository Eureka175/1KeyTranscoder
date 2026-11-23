# Canon R6 V（XF-HEVC 4:2:2 10-bit）兼容性调研

调研对象是两条真实素材，位于 `F:\CANON_R6V_OG_7K2997P_4CH_XFVC`。
**本次是只读调研**：未修改任何生产代码、硬解策略、默认 backend、
SourceClassifier、AudioPlan、Channel Sync、preservation、x265 scaling、
`VERSION` 或 release tag。仓库基线：
`feature/hardware-codec-policy-redesign @ ba54ad3`。

---

## 1. 素材

| | 素材 1 | 素材 2 |
|---|---|---|
| 文件 | `A_0013C699H260510_003337W5_CANON.MP4` | `A_0013C700H260510_003349NQ_CANON.MP4` |
| 大小 | 323,904,650 B | 1,411,158,014 B |
| SHA-256 | `0383003f…eb8e11a3` | `1a31c8d3…16ebe4970` |
| 时长 / 帧数 | 5.138 s / 154 | 22.890 s / 686 |
| sidecar | `*.CPF` 63,452 B（两条内容逐字节相同） | 同左 |

视频：**HEVC Rext**（SPS `general_profile_idc=4`）、**4:2:2**
（`chroma_format_idc=2`）、**10-bit**、6912×4608、30000/1001、
`hvc1`、level 183、`pc`/bt709、**Long-GOP IPB**（GOP 恒 15）。

> **目录名不可用于推断。** `OG` 会让人以为是 intra-only，实测是 Long-GOP
> 且有 B 帧；`7K` 与 6912 只是接近；`4CH` 指的是四条通道，不是一条四声道流。

音频：**4 × 单声道** `pcm_s24be` / `ipcm` / 48 kHz / 24-bit。
（不是 1×4CH，也不是 2×2CH。）

data 流：`tmcd`（时间码 `02:19:00;11`）+ `cndm`（Canon 专有 metadata）。
**没有** Sony 的 `rtmd`/`nrtm`/`PROF`/`USMT`，也没有 DJI 的 `djmd`。
无 chapters。

---

## 2. 四条主要结论

### 2.1 FFmpeg 路径：已证明逐帧位相同 ✅

产品 `x265`/`svtav1` 后端走的 FFmpeg 解码路径，在两条素材上都与软解
**逐帧位相同**（canonical `yuv422p10le`，无损 FFV1 中间件，全帧 SHA-256）：

- A0013C699：24/24 帧，序列摘要 `ca3f4027…`
- A0013C700：24/24 帧，序列摘要 `d091b6bd…`
- 硬解比软解快约 **35%**

项目自己的 `plan_decode` 已经能把这条路探出来并产出可直接执行的 argv
（`hevc_cuvid` + `scale_cuda=format=yuv422p10le,hwdownload,format=yuv422p10le`）。

### 2.2 rigaya 两个前端都静默丢 2 帧 ⚠️

| 前端 | reader | A0013C699 | A0013C700 |
|---|---|---|---|
| NVEncC `--avhw` | `avcuvid` | **152**（容器 154） | **684**（容器 686） |
| QSVEncC `--avhw` | `avqsv` | **152**（容器 154） | **684**（容器 686） |
| 各自的 `--avsw` | `avsw` | 154 ✅ | 686 ✅ |

两边 `rc=0`、无错误文本、产物可播，且丢帧写进产物容器（`stsz` 也是 152/684）。

**这是本次最强的证据**：两个独立前端、两套独立代码、两颗独立 GPU
（NVIDIA NVDEC / Intel QSV），丢的却是一模一样的 2 帧；而 FFmpeg 的
`hevc_cuvid` 在同一颗 NVIDIA 硅上**一帧不丢、逐帧位相同**。
所以这既不是 NVDEC 的缺陷，也不是 QSV 硬件的缺陷，而是 **rigaya 两个前端
共有的 reader 语义问题**（首 DTS 为负 + 2 个前导 B 帧 → 前导图像永不发射），
与项目自身文档 `xavc-hs-frame-count.md` 的 RC-1 完全同形。

### 2.3 端到端：最终产物是对的 ✅（但路径不是）

`--jobs 1` 单条素材真实转码（HQ preset）：

```
decode route -> HARDWARE (reader=avhw, reason=native_hw_integrity_ok)
Input Buffers  CUDA, 46 frames
cuvid: Failed cuvidCreateDecoder 2 (CUDA_ERROR_OUT_OF_MEMORY)
encoded 0 frames                                    <- rc=1
integrity gate [FAILED] reason=decode_failed
-> 丢弃产物，用 --avsw 重跑
最终 final_status=ok
交付产物：154/154 帧，4 条音轨，tmcd 保留
```

**并且交付产物与软件参照在全部 154 帧上逐帧位相同**（`framehash` SHA-256，
154/154，无不同帧）。字节级 SHA-256 不同只来自容器封装（MP4 vs MOV）。

所以结论是：**产物正确，但代价是每次都要先付一次失败的 `--avhw` 尝试**。

⚠️ 注意区分两件事：这次 `--avhw` 是因**显存不足**失败的（rc=1，0 帧），
所以门禁的**计数对账**在那次运行里没有被触发。而 2.2 节的丢帧是在显存足够的
手工测试里测到的（rc=0，静默）。在显存更大的机器上，`--avhw` 很可能跑完并
**静默丢 2 帧**，那时才会走 `count_mismatch` 这条分支。

### 2.4 FFmpeg + QSV 解码：设备能力拒绝 ⛔

`hevc_qsv` **不能**处理这条素材 —— 但不是丢帧，而是硬件帧上下文直接创建失败：

```
[AVHWFramesContext] Unsupported pixel format
```

4 条候选传输路径全部失败（2 条 `Invalid output format` rc=-22、
2 条 `Unsupported pixel format` rc=-40）。项目对此的判定是
`capability_refused`，`plan_decode(qsv_first)` 正确回退到 cuda。

⚠️ 一个陷阱：`probe_acceptance(qsv, hevc, 4:2:2, 10)` 报
`accepted (2 frames)` —— 那是**假阳性**，2 帧可由软件解码满足，
硬件 surface 从未分配成功。不能把这条当成 QSV 可用。

### 2.5 项目策略不覆盖 Canon ⚠️

- 分类正确：`source_kind_from_streams` → `non_sony`，
  **不会**误判为 Sony（无 `rtmd`）
- 但项目把 RC-1 丢帧缺陷的保护**只绑在 Sony 分支**上，
  所以 Canon 走 `--avhw`，不受该保护
- 项目自己的文档把触发条件写成**流形态**而非品牌属性：
  `docs/hardware-decode/xavc-hs-frame-count.md` 明确写"预测式是流形态而非
  codec/容器属性"，控制组含 DJI / x265 / synthetic / Sony-in-MKV

Canon XF-HEVC 正好落在该形态里：**首 DTS 为负（−0.033367）**，
显示序在首个 IDR 之前有 **2 个前导 B 帧**。

### 2.6 资源：这台 8 GB 显存机器上 NVENC 路径不可用

| 路径 | 专用显存峰值 | 系统内存峰值 | 结局 |
|---|---|---|---|
| NVEncC `--avhw`（HQ） | **7846 / 8151 MiB (96%)** | 25.7 / 32.3 GB | OOM，rc=1，0 帧 |
| QSVEncC `--avhw` | **0 MiB**（iGPU 共享内存） | 21.1 / 32.3 GB | 跑完，但丢 2 帧 |

单条 6912×4608 10-bit 4:2:2 素材就贴满 8 GB 显存：单帧 121.5 MiB ×
46 个 CUDA 输入 buffer（HQ preset 的 `--output-buf 64` + `--lookahead 32`）
≈ **5.46 GiB**。`--jobs auto`（2 workers）那一次直接打到显存剩 218 MiB、
系统内存剩 6 MB，被迫手动终止 —— **`--jobs 2` 必然失败**。
详见 `resource-usage.json`。

---

## 3. 顺带发现的项目缺陷

| ID | 缺陷 | 位置 |
|---|---|---|
| DEF-01 | `frame_signatures` 对 4:2:2 抛 `IndexError` —— 色度平面按 4:2:0 算 `(w//2)*(h//2)*2`，4:2:2 下越界 | `encoders/integrity.py:505` |
| DEF-02 | 因此逐帧检查 `verify_sequence` 对这类素材不可用；上层 `verify_encode` 只有计数级，看不到"计数相同但画面错位" | `encoders/integrity.py` |
| DEF-03 | 解码失败原因不可诊断：默认不保留工作目录时只剩 `decode_failed: tool exited rc=1`，真因（OOM）在已删日志里 | `core/batch_hw.py` |
| DEF-04 | `--jobs auto` 的容量推导未纳入分辨率/色度采样；研究语料只有 4K 4:2:0（单帧 15.2 MiB，是本发明材的 1/8） | `1kt.py` / `core/batch_hw.py` |
| DEF-05 | `plan_decode` 接受某条路径后，**被拒路径的原因不进日志**：`plan.detail` 只写 cuda 被接受，QSV 为何被拒（`Unsupported pixel format`）丢失 | `encoders/hwdecode_ffmpeg.py` `plan_decode` 的 `rejections` |
| DEF-06 | `probe_acceptance(qsv, hevc, 4:2:2, 10)` 报 `accepted (2 frames)` 是**假阳性** —— 2 帧可由软件解码满足，硬件 surface 从未分配。单看这条会误判 QSV 可用（`resolve_transfer` 兜住了，但两段信息分散） | `encoders/hwdecode_ffmpeg.py` `probe_acceptance` |

### metadata 保留

源 7 条流 → 交付产物 6 条：

- ✅ **保留**：video、4 × audio（mono 结构不变）、`tmcd`
- ❌ **丢弃**：`cndm`（Canon 专有 metadata）
- 音频被 rigaya 改写 `pcm_s24be` → `pcm_s24le`（`ipcm` → `in24`），
  声道数/采样率/位深/轨道数均不变

`cndm` 的丢弃与 `core/batch_hw.py:1292` 的
`POLICY | non-Sony source: metadata dropped by policy` 一致，
**属既有设计**，不是硬件解码引入的。是否应当保留 Canon metadata
属于产品决策，本次不建议直接改。

---

## 4. 产物索引

| 文件 | 内容 |
|---|---|
| `media-manifest.json` | 素材身份、SPS 字段、GOP、包序、音频与 data 流 |
| `software-decode-results.json` | 全片软解参考（154/154、686/686、0 错误） |
| `hardware-decode-results.json` | 各硬件路径结果、planner 决策、根因 |
| `frame-comparison.json` | 逐帧位相同比对（含**明确写出没做什么**） |
| `audio-stream-analysis.json` | 4×mono 拓扑、默认路径、字节序改写 |
| `metadata-analysis.json` | `tmcd` 保留 / `cndm` 丢弃、Sony 结构缺失 |
| `resource-usage.json` | 显存/内存曲线与 OOM 根因算式 |
| `qsv-and-software-paths.json` | QSV（ffmpeg 侧拒绝原因 + QSVEncC 丢帧）与软件路径 |
| `test-matrix.md` | 全部测试的 PASS/FAIL/PENDING 判定 |
| **`xfhevc-structure/`** | **XF-HEVC 编码结构调研报告（容器/轨道/位流/metadata 四层 + 与 XAVC 20 项逐项对照）** |

> **编码结构专项报告**：如果你想了解「XF-HEVC 到底是什么结构、和 XAVC 差在哪」，
> 直接看 `xfhevc-structure/README.md`。它把容器层（`ftyp`/`moov` 位置/品牌）、
> 轨道层（7 轨采样表、`hvc1`/`ipcm`/`tmcd`/`cndm`）、HEVC 位流层
> （`hvcC`、SPS/PPS/VPS 全字段、tiles 而非 WPP）、Canon 私有 metadata 层
> （moov 级 uuid `{85C0B687-…}`、`CN*` box、KLV 编码的 `cndm`）
> 以及**与 XAVC 的 20 项逐项对照**都拆开了，并解释了为什么 rigaya 的
> 丢帧缺陷对两者同源。

生成脚本（只读，只写本目录）：`work/canon_r6v/make_report.py`、
`make_report2.py`。诊断与采样工具：`work/canon_r6v/why_qsv.py`、
`resource_monitor.py`、`exact_frames.py`（后者已改用 ffmpeg 内建
`framehash`，比 Python 逐帧哈希快一个数量级）。

---

## 5. 复现

```powershell
cd F:\1KeyTranscoder

# 媒体属性 + 分类/路由（无解码，分钟级）
python work\canon_r6v\offline_suite.py --stage fast

# 软解 vs 硬解 逐帧位相同（认素材，10-20 分钟）
python work\canon_r6v\offline_suite.py --stage decode

# 单并发端到端 + 资源监控
python work\canon_r6v\resource_monitor.py --seconds 2100 --out peak.csv   # 另开一个窗口
python 1kt.py --input <单条素材目录> --output <输出目录> --encoder nvenc --preset hq --jobs 1 --headless
```

复现 `avhw` 的 OOM：把产品日志里的 `COMMAND` 行原样执行即可，
日志中 `Input Buffers CUDA, 46 frames` 与
`cuvid: Failed cuvidCreateDecoder 2 (CUDA_ERROR_OUT_OF_MEMORY)` 是判据。

---

## 6. 已知限制 / 尚未验证

**没有做的事，明确列出，不要当成已证明：**

1. **A0013C700（686 帧）未做端到端** —— 端到端只测了 A0013C699。
   （A0013C700 的 rigaya 丢帧已在全片手工测试中确认：684 vs 686。）
2. **QSV 后端（`--encoder qsv`）未做端到端** —— 只测了 QSVEncC 原生 reader。
3. **avhw/avsw 的逐帧指纹定位未做** —— 只做到帧数层（154→152、686→684）。
   "丢的恰好是显示序第 0、1 帧"是**基于结构的推断**（负 DTS + 2 个前导
   B 帧 + 项目公式 2==2 吻合），不是指纹实测。标记 **PENDING**。
4. **FFmpeg 硬解逐帧比对窗口只有 24 帧**（全片 154/686）。
   唯一的**全片**位相同证明是端到端交付产物 vs 软件参照的 154/154。
5. **交付容器里的 color metadata 与 stream disposition 未核对**。
6. **CPF sidecar 未解析**（二进制，未提取出可读文本），
   不清楚其中是否含需要保留的信息。
7. **`--avhw` 与 `--avsw` 的音频结果未做逐路径对比。**
8. **本机 GPU 只有 8 GB 显存** —— 2.6 节的 OOM 结论是**本机**事实，
   不能外推到显存更大的机器。在 24 GB 显存上 `--avhw` 很可能跑完，
   届时才会暴露 2.2 节的静默丢帧。
9. **QSVEncC 资源采样只有 12 个样本、间隔 0.7 s，峰值可能被低估**；
   且 Intel iGPU 无独立显存计数器，只能报进程工作集合。
10. 资源峰值是 2 秒采样，**真实峰值应 ≥ 记录值**。

---

## 7. 如果后续要改生产代码（仅建议，本次未实施）

需要授权后才可开始。最小范围与回归测试：

| 项 | 内容 |
|---|---|
| 文件 | `encoders/integrity.py` |
| 逻辑 | `frame_signatures` 的色度平面尺寸按 `pix_fmt` 分支计算（4:2:0 / 4:2:2 / 4:4:4），而不是硬编码 4:2:0 |
| 复现条件 | 任意 4:2:2 素材调用 `frame_signatures(w,h,pix_fmt="yuv422p10le")` → `IndexError` |
| 实验证据 | 两条 Canon 素材均复现；见 `test-matrix.md` DEF-01/02 |
| 最小修复范围 | 仅 `frame_signatures` 与 `_iter_decoded` 的 `fsz` 计算 |
| 回归测试 | 对 4:2:0 / 4:2:2 / 4:4:4 各造一个 fixture，断言帧数与签名不抛异常且 4:2:0 结果与旧实现一致 |

| 项 | 内容 |
|---|---|
| 文件 | `encoders/hwdecode.py`（策略）+ `core/batch_hw.py`（容量） |
| 逻辑 | 是否把"首个同步样本之前存在前导图像"作为**独立于品牌的**路由依据，以及 `--jobs auto` 是否把分辨率/色度采样纳入容量推导 |
| 复现条件 | Canon 素材 + nvenc + 显存不足（`--jobs 1` 仍 OOM）；或显存足够时观察静默丢帧 |
| 实验证据 | 2.2 / 2.3 节，`resource-usage.json`、`hardware-decode-results.json` |
| 最小修复范围 | **待定** —— 这是策略决策，不是缺陷修复；建议先补 QSV 与 A0013C700 的证据再定 |
| 回归测试 | Sony 必须仍然 `avsw`；DJI 必须仍然 `avhw`；不可因新增条件让任一既有断言反转 |
