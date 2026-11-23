# Canon R6 V（XF-HEVC 4:2:2 10-bit）兼容性测试矩阵

本文件记录**实际执行过**的测试及其判定。判定口径与项目
`docs/hardware-decode/integration-test-matrix.md` 一致：

- **PASS** — 有实测证据支持，且证据强度足以支撑结论
- **FAIL** — 实测不通过
- **WARN** — 观察到偏离但不构成失败
- **PENDING** — 尚未验证（明确写出缺什么）
- **KNOWN LIMITATION** — 已知限制，不作为失败
- **BLOCKED** — 环境不允许执行

> 素材身份锚点：A0013C699 `0383003f…`（154 帧）、A0013C700 `1a31c8d3…`（686 帧）。
> 任何结论都必须能通过这些 SHA-256 回到原始素材。

---

## 0. 判定纪律（本次遵守的口径）

项目文档已经把三条"看起来像证据、其实不是"的东西写成硬约束，本次沿用：

1. `exit code == 0` **不是**正确性证据 —— 本次丢帧缺陷正是 rc=0；
2. "播放器能播放" **不是** —— 丢 2 帧的文件完全可播；
3. "帧数相等" **不是** —— 丢帧是连续头部截断，计数可能过而画面已错位。

因此本次尽量做到**逐帧位相同**（canonical 像素格式 + 无损中间件 + 全帧哈希），
而不是停在计数层。

---

## 1. 素材身份与媒体属性

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| ID-01 | 素材目录文件数 = 2 MP4 + 2 CPF | PASS | 目录清单 |
| ID-02 | A0013C699 SHA-256 = `0383003f…`，323,904,650 B | PASS | `media-manifest.json` |
| ID-03 | A0013C700 SHA-256 = `1a31c8d3…`，1,411,158,014 B | PASS | `media-manifest.json` |
| ID-04 | 两条 CPF 内容逐字节相同 | PASS | SHA-256 均为 `beb04983…` |
| ID-05 | codec = hevc，tag = hvc1，profile = Rext | PASS | ffprobe |
| ID-06 | SPS `general_profile_idc = 4`（Range Extensions） | PASS | `trace_headers` 位流 |
| ID-07 | SPS `chroma_format_idc = 2`（4:2:2） | PASS | `trace_headers` |
| ID-08 | SPS `bit_depth_luma/chroma_minus8 = 2`（10-bit） | PASS | `trace_headers` |
| ID-09 | 6912×4608，30000/1001，SAR 1:1，DAR 3:2 | PASS | ffprobe |
| ID-10 | progressive（`general_frame_only_constraint_flag=1`） | PASS | `trace_headers` |
| ID-11 | 容器样本数 = 154 / 686 | PASS | `stsz` |
| ID-12 | **Long-GOP IPB**（有 B 帧），GOP 恒 = 15 | PASS | 帧类型统计 |
| ID-13 | 目录名 `OG` 与实际形态不符，不得用于推断 | PASS | 见 `media-manifest.json` note |
| ID-14 | 首 DTS 为负（−0.033367） | PASS | 包序 |
| ID-15 | 首个同步样本之前有 **2** 个前导图像 | PASS | 显示序 |
| ID-16 | PTS 严格递增，无断层 | PASS | 帧时间戳 |

---

## 2. 音频与 metadata 结构

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| AU-01 | 音频流数 = 4（不是 1、不是 2） | PASS | ffprobe |
| AU-02 | 拓扑 = **4 × mono**，不是 1×4CH / 2×2CH | PASS | 每流 channels=1 |
| AU-03 | codec = `pcm_s24be`，tag = `ipcm`，48 kHz，24-bit | PASS | ffprobe |
| AU-04 | 4 条音轨 start_time 均 0.000000，时长与视频差 < 1 样本 | PASS | ffprobe |
| AU-05 | 不得据目录名 `4CH` 推断为单条四声道流 | PASS | 同上 |
| MD-01 | data 流 = `tmcd` + `cndm`，无 chapters | PASS | ffprobe |
| MD-02 | **无** Sony `rtmd`/`nrtm`/`PROF`/`USMT` | PASS | 分类器 data tag |
| MD-03 | 无 DJI `djmd`/`dbgi` | PASS | 分类器 data tag |
| MD-04 | 项目策略：非 Sony → metadata dropped by policy | PASS | `core/batch_hw.py:1292`，真实运行日志 |
| MD-05 | 产物保留 `tmcd` | PASS | 产物流表 |
| MD-06 | 产物 **丢失** `cndm`（7 流 → 6 流） | FAIL（但属既有设计） | 产物流表 |
| MD-07 | 产物中 color_range / color_space 是否与源一致 | **PENDING** | 未核对 |
| MD-08 | 产物 stream disposition 是否一致 | **PENDING** | 未核对 |

---

## 3. 项目分类与解码路由（代码 + 真实探针）

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| CL-01 | `source_kind_from_streams` → `non_sony` | PASS | `route_*.json` |
| CL-02 | **不误判为 Sony**（`is_sony_source` = False） | PASS | 无 `rtmd` |
| CL-03 | 不误判为 DJI | PASS | 无 `djmd` |
| CL-04 | `route_decode(auto, nvenc, hevc 4:2:2 10bit, non_sony)` → `avhw` | PASS | 真实运行日志 |
| CL-05 | reason = `native_hw_integrity_ok`（不在 allowlist 但照走硬件） | PASS | 真实运行日志 |
| CL-06 | Sony 分支 → `avsw` / `sony_hardware_decode_safety` | PASS | 逻辑网格 |
| CL-07 | 请求时间寻道时拒绝硬件 | PASS | 逻辑网格 |
| CL-08 | `policy=off` → `avsw`；`require` 在能力拒绝上报 `require_unmet` | PASS | 逻辑网格 |
| CL-09 | `qsv/h264/4:2:2/10` 仍在 REFUSED 表 | PASS | 代码 |
| CL-10 | `nvenc/hevc/4:2:2/10` **不在** PROVEN 也不在 REFUSED | PASS | 代码：落进"未证实但照走"区 |
| CL-11 | **是否因 Canon 而触发 Sony 保护** | **FAIL（本调查核心）** | 保护只绑 Sony 分支 |

---

## 4. FFmpeg 解码路径（x265 / svtav1 后端）

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| FF-01 | `build_caps` 探到 cuda[0] 与 qsv[0] 均 measured | PASS | `ffmpeg_caps.json` |
| FF-02 | `plan_decode(cuda_first)` → `hevc_cuvid` + `scale_cuda` transfer | PASS | 项目自身 planner 输出 |
| FF-03 | planner 产出的 argv 可直接执行（rc=0） | PASS | 真实解码 |
| FF-04 | 软解 154/154、686/686，0 解码错误 | PASS | 全片软解 |
| FF-05 | 硬解（cuvid）24/24 帧与软解**逐帧位相同**（两条） | PASS | `frame-comparison.json` |
| FF-06 | 硬解比软解快约 35% | PASS | 40 帧窗口计时 |
| FF-07 | QSV 取得可用 transfer | **PENDING** | `caps.transfers` 无 qsv 条目 |
| FF-08 | `plan_decode(qsv_first)` 的实际落点 | **PENDING** | 未单独验证 |

---

## 5. rigaya 解码路径（nvenc / qsv 后端）

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| RG-01 | `--avhw` reader identity = `avcuvid` | PASS | 工具自报 |
| RG-02 | `--avsw` reader identity = `avsw` | PASS | 工具自报 |
| RG-03 | A0013C699：容器 154，`--avsw` 154 帧 | PASS | 全片编码 |
| RG-04 | A0013C699：`--avhw` **152** 帧，rc=0，无错误文本 | **FAIL** | 全片编码 + 产物 stsz |
| RG-05 | A0013C700：容器 686，`--avsw` 686 帧 | PASS | 全片编码 |
| RG-06 | A0013C700：`--avhw` **684** 帧，rc=0 | **FAIL** | 全片编码 |
| RG-07 | 丢弃量 == 前导图像数 == 2（项目公式） | PASS | 2 == 2 |
| RG-08 | 丢帧发生在交付产物里（产物 stsz 也是 152） | PASS | 产物容器 |
| RG-09 | `verify_encode` 能否识别该丢失 | PASS（逻辑） | 计数级对账会判 count_mismatch |
| RG-10 | 门禁拒绝后是否真的删除产物并 `--avsw` 重跑 | **PENDING** | 待产品端到端确认 |
| RG-11 | avhw vs avsw 产物的**逐帧**指纹定位 | **PENDING** | 只做到帧数层 |
| RG-12 | QSVEncC `--avhw` 对同一素材的行为 | **FAIL — 同样丢 2 帧** | 见 §5.1 |

### 5.1 QSVEncC（`avqsv` reader）—— 与 NVEncC 同缺陷

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| QS-01 | QSVEncC `--avhw` 能力：接受该素材 | PASS | `Input Info avqsv: H.265/HEVC, 6912x4608` |
| QS-02 | A0013C699：`--avsw` 154 帧 | PASS | 全片 |
| QS-03 | A0013C699：`--avhw` **152** 帧，rc=0，无错误文本 | **FAIL** | 全片 |
| QS-04 | A0013C699：产物容器样本数 avhw=152 / avsw=154 | FAIL | 丢帧写进产物 |
| QS-05 | A0013C700：`--avhw` 684 vs `--avsw` 686 | **FAIL** | 全片 |
| QS-06 | QSVEncC `--avsw` 帧数正确（154/686） | PASS | 全片 |

**意义**：NVEncC(`avcuvid`) 与 QSVEncC(`avqsv`) 是**两个独立前端、两套独立代码、
两颗独立 GPU**，却在这两条素材上丢了**完全相同**的 2 帧；而 FFmpeg 的
`hevc_cuvid` 在同一颗 NVIDIA 硅上逐帧位相同、一帧不丢。这排除了"NVDEC 缺陷"
与"QSV 硬件缺陷"，指向 **rigaya 两个前端共有的 reader 语义问题** ——
与项目自身文档 `xavc-hs-frame-count.md` 的 RC-1 结论一致（规则相同、代码不同）。

### 5.2 FFmpeg + QSV（`hevc_qsv`）—— 设备能力拒绝

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| QF-01 | `probe_acceptance(qsv, hevc, 4:2:2, 10)` 报接受 | 假阳性（需注意） | 报 `accepted (2 frames)`，但那 2 帧可由软件满足 |
| QF-02 | `resolve_transfer` 能否下载 QSV surface | **FAIL** | `[AVHWFramesContext] Unsupported pixel format` |
| QF-03 | 4 条候选路径全部失败 | PASS（已穷举） | 2 条 `Invalid output format`(rc=-22)、2 条 `Unsupported pixel format`(rc=-40) |
| QF-04 | 归类为 `capability_refused` 而非丢帧 | PASS | 失败在 hwframes 创建阶段 |
| QF-05 | planner 正确回退到 cuda | PASS | `plan_decode(qsv_first)` → `hevc_cuvid` |
| QF-06 | 拒绝原因是否进日志 | **FAIL** | `plan.detail` 只写 cuda 被接受，QSV 被拒原因丢失 |

诊断脚本：`work/canon_r6v/why_qsv.py`。

### 5.3 资源对比

| 路径 | 专用显存峰值 | 系统内存峰值 | 结局 |
|---|---|---|---|
| NVEncC `--avhw`（HQ preset） | **7846 / 8151 MiB (96%)** | 25.7 / 32.3 GB | `CUDA_ERROR_OUT_OF_MEMORY`，rc=1，0 帧 |
| QSVEncC `--avhw` | **0 MiB**（iGPU 用系统内存） | 21.1 / 32.3 GB | 跑完，但丢 2 帧 |

QSVEncC 不碰专用显存，所以**没有** NVENC 那个 OOM 问题；
代价是进程工作集合峰值 8.8 GB。在这台机器上，QSV 路径受资源限制的程度
明显低于 NVENC 路径。

---

## 6. 音频与 metadata 经真实路径

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| RT-01 | 产物保留 4 条音轨（未丢失/合并/重排） | PASS | 产物流表 |
| RT-02 | 声道数 1→1、采样率 48k→48k、位深 24→24 不变 | PASS | 产物流表 |
| RT-03 | 采样格式被改写 `pcm_s24be` → `pcm_s24le` | WARN | rigaya 日志 |
| RT-04 | 该改写是否到最终交付容器 | **PENDING** | 只验证到中间件 |
| RT-05 | `--avhw` 与 `--avsw` 音频结果差异 | **PENDING** | 未对比 |

---

## 7. 产品端到端（`--jobs 1`，单条素材 A0013C699）

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| E2E-01 | `--dry-run` 正确识别两条素材（154/686、4:2:2/10、无降级） | PASS | dry-run 日志 |
| E2E-02 | 真实转码正确路由到 `--avhw` | PASS | `cli_out1` 日志 |
| E2E-03 | **交付产物帧数 = 154 = 源容器样本数** | **PASS** | ffprobe `nb_read_frames=154` |
| E2E-04 | 交付产物保留 4:2:2 / 10-bit / 6912×4608 / 29.97 | PASS | `pix_fmt=yuv422p10le`, `profile=Rext` |
| E2E-05 | 门禁识别 avhw 失败并丢弃产物、`--avsw` 重跑 | PASS | `software_fallback=True`, `discarded=1` |
| E2E-06 | 最终状态 `final_status=ok`，产出可用文件 | PASS | 27.14 MB, ratio 8.79% |
| E2E-07 | 交付产物保留 4 条单声道音轨 | PASS | 4 × `pcm_s24le`/mono/48k/24bit |
| E2E-08 | 交付产物保留 `tmcd` | PASS | 流表 idx=5 |
| E2E-09 | 交付产物丢失 `cndm` | FAIL（既有设计） | 源流表 idx=6 有 `cndm`，产物流表无 |
| E2E-10 | avhw 失败原因是否可诊断 | **FAIL** | 只见 `tool exited rc=1`，真因在已删日志里 |
| E2E-11 | A0013C700（686 帧）端到端 | **PENDING** | 未跑 |
| E2E-12 | QSV 后端端到端 | **PENDING** | 未跑 |
| E2E-13 | **交付产物与软件参照逐帧位相同（全 154 帧）** | **PASS** | `framehash` SHA-256，154/154，首个不同帧 = 无 |
| E2E-14 | 交付产物字节 SHA-256 与参照不同 | PASS（容器差异，非像素差异） | MP4 vs MOV 封装 |

E2E-13 的方法：两条文件都解码到 canonical `yuv422p10le`，用 ffmpeg 内建
`framehash -hash sha256` 逐帧取哈希后比对。字节级 SHA-256 不同
（`ea8c2ea3…` vs `fafcf585…`）**只来自容器封装**（交付为 MP4、参照为 MOV），
像素完全一致。这是本次唯一覆盖**全片全部帧**的位相同证明。

### 7.1 avhw 失败的真实根因（已手工复现）

项目日志只给出 `decode_failed — tool exited rc=1`。用同一条 argv 手工复现后拿到真因：

```
Input Buffers  CUDA, 46 frames
cuvid: Failed cuvidCreateDecoder 2 (CUDA_ERROR_OUT_OF_MEMORY)
encoded 0 frames
```

**是显存不足，不是解码错误、不是丢帧、不是能力拒绝。**

- 单帧 10-bit 4:2:2 = 121.5 MiB；46 个 CUDA 输入 buffer ≈ **5.46 GiB**
- 加上 NVDEC/NVENC surface 池，8 GB 显存装不下 → `cuvidCreateDecoder` 直接失败
- 46 这个数字来自产品 HQ preset 的 `--output-buf 64` + `--lookahead 32`
- 我早先手工测试用 `--preset performance`（无 lookahead），日志是
  `Input Buffers CUDA, 9 frames` ≈ 1.07 GiB，所以能过 —— 这解释了两次测试的矛盾

### 7.2 资源剖面（`--jobs 1`）

| 指标 | 值 |
|---|---|
| 显存峰值 | **7846 / 8151 MiB（96.3%）** |
| 系统内存峰值 | 25679 / 32253 MB |
| 进程工作集合峰值 | 16491 MB |
| 显存高位持续 | 19s–77s（锁定 7841–7846 MiB） |

对照 `--jobs auto`（2 workers）那一次：显存仅剩 218 MiB、系统内存仅剩 6 MB，**手动终止**。

**结论：单条 6912×4608 10-bit 4:2:2 素材就已经贴满这块 8 GB GPU。`--jobs 2` 必然失败，不是调参问题。**
详见 `resource-usage.json`。

---

## 8. 项目自身缺陷（顺带发现）

| ID | 测试 | 判定 | 证据 |
|---|---|---|---|
| DEF-01 | `encoders.integrity.frame_signatures` 对 4:2:2 抛 `IndexError` | **FAIL** | 两条素材均复现 |
| DEF-02 | 根因：色度平面按 4:2:0 算 `(w//2)*(h//2)*2`，4:2:2 下越界 | PASS（已定位） | `encoders/integrity.py:505` |
| DEF-03 | 即逐帧检查 `verify_sequence` 对此类素材不可用 | **FAIL** | 而上层 `verify_encode` 是计数级 |
| DEF-04 | 离线测试套件 `--frames` 限制会让丢帧检测失效 | FAIL（我的工具缺陷） | 四组均报 118 |

---

## 9. 汇总

**四条独立路径的结局（全部实测）**

| 路径 | 结果 |
|---|---|
| FFmpeg 软解（`hevc` native） | ✅ 154/154、686/686，0 错误 |
| FFmpeg 硬解（`hevc_cuvid`） | ✅ 逐帧位相同（24 帧窗口，两条素材），快约 35% |
| FFmpeg 硬解（`hevc_qsv`） | ⛔ 设备能力拒绝（`[AVHWFramesContext] Unsupported pixel format`） |
| NVEncC `--avhw`（`avcuvid`） | ⚠️ 静默丢 2 帧；且本机显存不足 OOM（rc=1，0 帧） |
| QSVEncC `--avhw`（`avqsv`） | ⚠️ 静默丢 2 帧（152/154、684/686） |
| NVEncC / QSVEncC `--avsw` | ✅ 154/154、686/686 |
| 产品端到端（nvenc, `--jobs 1`） | ✅ 交付 154/154，**全片逐帧与软件参照位相同** |

**一句话结论**：两条 Canon 素材在 **FFmpeg 路径**上已被逐帧位相同证明兼容；
在 **rigaya 两个前端的 `--avhw`** 上都会静默丢 2 帧（这是本次最重要的发现，
且与品牌无关，是流形态问题）；产品端到端虽然在 NVENC 路径上因**本机 8 GB
显存不足**触发 OOM，但完整性门禁正确拦下并回退 `--avsw`，
**最终交付产物在全部 154 帧上与软件参照逐帧位相同**。

**仍然缺的证据**：A0013C700 的端到端、QSV 后端的端到端、
avhw/avsw 的逐帧指纹定位（现为基于结构的推断）、
交付容器里的 color metadata 与 stream disposition。
