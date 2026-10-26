# 归档修复入口 `archive_repair.py`

**入口**：仓库根目录的 `archive_repair.py`（与 `watchfolder.py` 同级，不进包）。
**定位**：把**已经产出**的成品当作独立对象来检查与修复，不要求源素材还在手边。

```
python archive_repair.py check  --input <文件或目录> [--json DIR] [--groups] [--accurate]
python archive_repair.py repair --input <文件或目录> --output <暂存目录> [--groups] [--accurate]
                                [--in-place --backup DIR --yes]
```

`--ext` 过滤扩展名（默认 `.mp4,.MP4,.mov,.MOV`），`--limit N` 限制文件数，
递归遍历目录。

---

## 1 能修什么

| 类别 | 做什么 | 怎么判定做对了 |
|---|---|---|
| **音频延迟重修** | 多轨单声道 PCM 的轨间偏移重新测量，按 **整数样本** 移位 | 成品上**重新测量**一遍，所有可测轨 `already_aligned`；同时 `preservation.validate.compare` 报 `MODIFIED=0 MISSING=0` |
| **元数据写入位置修复** | 品牌 / `tref/cdsc` / `nrtm` / 厂商 uuid 是否还在原来的结构位置；若重封装把 moov 挪到末尾而源是 moov-first，则恢复布局 | 修复前后 **uuid 多重集**（context + guid + payload sha）逐项相等，`ftyp` major、`tref/cdsc`、`nrtm` 三项相等；不等就**不交成品** |
| **时长/timescale 一致性** | `mvhd` / `tkhd` / `elst` 时长与 timescale 对齐（`repair_remux_timescale`，产品既有步骤） | `mvhd seconds` 与容器时长差 ≤ 2 ms（`CTR-06`） |
| **格式规范性检查** | 见下表 `CTR-*` / `FMT-*` | 逐项 PASS / FAIL / SKIP |

**不做的事**：不重新编码视频；不猜测不可测的偏移；不因为"检查项都过"就声称音频
已对齐（对齐只有实测那一条证据）。

## 2 检查项

| ID | 类别 | 判定标准 | 失败意味着 |
|---|---|---|---|
| `AUD-01` | 音频 | 布局可对齐（≥3 条单声道 PCM、同采样率、线性 PCM） | 不适用，记 SKIP |
| `AUD-02` | 音频 | 生产入口 `run_channel_sync` 的逐轨测量结果 | 无可测轨记 `UNMEASURABLE` |
| `AUD-03` | 音频 | `status == already_aligned` | `applied` → **FAIL**（有轨需要修）；`measure_failed` 等 → `UNMEASURABLE` |
| `CTR-01` | 容器 | `ftyp` major brand 存在 | 容器结构坏了 |
| `CTR-02` | 容器 | 有数据轨则必须有 `tref/cdsc` | 元数据关联丢失 |
| `CTR-03` | 容器 | 有 `meta` 则应有 `nrtm` | WARN（镜头档案缺失） |
| `CTR-04` | 容器 | uuid 清单（context/guid/payload） | 记录用 |
| `CTR-05` | 容器 | moov 在 mdat 之前 | WARN（非 faststart） |
| `CTR-06` | 容器 | `mvhd` 时长 == 容器时长 | timescale 不一致 |
| `CTR-07` | 容器 | 音频 sample entry 不是 `in24` | WARN（Sony LPCM 实为 `ipcm`，`in24` 会让下游判 MODIFIED） |
| `FMT-01` | 格式 | 容器时长 vs 各流时长（≤50 ms） | WARN |
| `FMT-02` | 格式 | 无零/缺失时长流 | **FAIL** |
| `FMT-03` | 格式 | 轨道顺序 video → audio → data | WARN |
| `FMT-04` | 格式 | 每条流解码 1 s 无报错 | **FAIL** |
| `FMT-05` | 格式 | `moov` 里 `trak` 数 == ffprobe 流数 | **FAIL** |

## 3 判定词汇（不允许含糊）

`PASS` / `FAIL` / `WARN` / `SKIP` / `UNMEASURABLE` / `FIXED`(修复侧 `REPAIRED`)。
`SKIP` 与 `UNMEASURABLE` **不计入通过**：静音轨测不出偏移，就如实写成测不出，
而不是当成"已对齐"。

## 4 三条设计约束

1. **默认不动原件。** `repair` 只写 `--output` 暂存目录。写回原件必须
   `--in-place --backup DIR --yes` 三件齐备；写回前先把原件复制到 backup。
2. **元数据不齐就不交成品。** 重封装后 uuid/`ftyp`/`tref`/`nrtm` 任一项与输入
   不同，该文件退回"源文件原样拷贝"并记为失败。宁可交出诚实的拷贝，也不交出
   一个看着像成品、元数据被剥光的文件。
3. **检查独立于被检查的代码。** 结构检查用本文件自带的 box 遍历器，不调
   `preservation.isobmf`（那是写这些 box 的库）。修复路径当然走产品入口，
   但成品另由 `preservation.validate.compare` 独立比对。

## 5 布局修复为什么是安全的

`MP4Box -inter` 会重新交织，但**不重编码**。在 `20260904_C1183` 上实测：

| 检查 | 结果 |
|---|---|
| 逐流 packet streamhash（6 条流） | 全部 **SAME** |
| 容器时长 | 完全相同 |
| `preservation.validate.compare` | **43 PRESERVED / 0 MODIFIED / 0 MISSING** |
| Gyroflow | PASS（相同 IMU 样本数与相机型号） |
| 顶层 box 顺序 | `ftyp,mdata,free,meta,moov` → `ftyp,meta,moov,mdat,free`（恢复 moov-first） |

## 6 已知限制

* **不可测的轨救不回来。** 静音轨（`silent_track`）、无有效锚点
  （`no_valid_anchor`）、非恒定（`non_constant`）的轨只能如实报告，不能修。
  归档 667 件里有 20 件属于这一类。
* **`AUD` 在 `check` 下会写临时文件。** 它调生产入口 `run_channel_sync`，
  后者在 `SCRATCH` 下产出 `audio_*.mov`；结束时删除，**源文件只被读取**。
  `SCRATCH` 可用环境变量 `ARCHIVE_REPAIR_SCRATCH` 指定。
* **一次跑一件。** 没有并行；单片耗时从 1 s（已对齐）到几十秒（需重封装）。
* **精确模式（`--accurate`）的 tier 2 / 退化路径** 在工具里会正常工作，但
  目前没有真实素材触发过（见回归文档 §19.6）。

## 7 与一次性脚本的关系

`tests/regression/repair_archive.py` 是 0.8.7 归档修复**那一次**的执行器
（从已测好的 arm 里挑 `applied` 文件、逐条记录、生成 `_APPLY-BACK.ps1`）。
`archive_repair.py` 是可复用的正式入口：自带测量、自带检查、自带布局修复，
不需要先跑一遍 arm。两者共用同一套底层（`run_channel_sync` +
`transparent_restore` + `validate.compare`）。

## 8 2026-10-26 升级：一键 repair 各类错误 + 文件级并行

### 8.1 一键修的范围

`repair` 现在对每个文件按顺序尝试：

1. **音频偏移重修**（原有）——多轨单声道 PCM 的整数样本移位，成品上重新测量复核。
2. **元数据位置/布局恢复**（原有）——brand / `tref` / `nrtm` / uuid 逐项与输入比对。
3. **容器级一键修复**（新增，不需要源文件）：
   - `CTR-09` tkhd flags / alternate_group 归一化（Premiere 多音轨只显示一条的修复）；
   - `CTR-08` PCM sample entry 自相矛盾时以 `pcmC` 为准校正 `samplesize`/每包字节数
     (`normalise_audio_entry_bit_depth`)；
   - `CTR-06`/时长时基：按 `stts` 重建 tkhd/elst/mvhd 时长
     (`repair_remux_timescale`)；
   - 可选 `--fix-layout`：moov-last → moov-first（`MP4Box -inter`，整文件重交织，默认关）。
4. **无需音频修复但容器有可修问题**的文件也会产出成品（`status=container_fixed`），
   只有真正无改动才 NOOP 且不落盘。

新增/修正的检查：

- `CTR-02` 只对 **Sony `rtmd`** 要求 `tref/cdsc`；DJI `djmd/dbgi/tmcd` 不再误报 FAIL。
- `CTR-07/08` 只对线性 PCM 条目做位深/包长检查；AAC/Opus 条目记 SKIP。
- 新增 `CTR-09` tkhd flags/alternate_group 检查。
- `.part.mov` 等半成品：`CTR-05` 记 SKIP、整体 `PARTIAL`，repair 跳过。
- 无 moov/零填充坏文件：整体 `UNREADABLE`，repair 如实报告、不中断批次。

判定词汇新增：`PARTIAL`（半成品）、`UNREADABLE`（不可解析）；
修复侧 `container_fixed` 只有在复检无 `FAIL` 时才算 `REPAIRED`。

### 8.2 并行化

`check` 与 `repair` 都新增 `--jobs N`（默认 `min(4, CPU/2)`；16 核机器默认 4）：

- 按文件用 `ProcessPoolExecutor` 并行；
- 每个 worker 独立 `scratch/pid<pid>` 目录，避免音频中间文件互删；
- 并行时把 BLAS/OpenMP 线程固定为 1，避免进程×线程过度订阅；
- 进度按完成顺序打印，报告仍按输入顺序汇总。

实测（I:\20260830，前 12 文件）：`check` jobs=1 **143.2 s** → jobs=4 **52.9 s（2.7×）**；
`repair` 8 文件 jobs=4 **44.0 s**。

### 8.3 实测（I: 根目录 YYYYMMDD 文件）

| 类 | 样例 | 结果 |
|---|---|---|
| DJI 容器类 | `I:\20260807` 7 文件 | 修复前 5 FAIL/2 WARN（CTR-02/08/09），一键修复后 7 个 **REPAIRED → 复检 7 PASS** |
| 4CH 音频偏移 | `I:\20260904\20260904_C1179.MP4` | REPAIRED（CH1+1174/CH2+934），复检 **PASS** |
| 2CH 有效 + 容器问题 | `I:\20260829` 4 文件 | 修复前 FAILED（无输出分支 bug）；修好后 **4 REPAIRED → 4 PASS** |
| `.part` 半成品 | `I:\20260819\...fast100x.part.mov` | **PARTIAL**，跳过、不产出 |
| 无 moov 坏文件 | `I:\20260901\20260901_C1033.MP4` | **UNREADABLE**，跳过、不中断批次 |
| 全量批处理 | I: 18 个 YYYYMMDD 目录（669 文件） | 后台运行中；已完成目录 0 FAILED（详见总报告） |

### 8.4 已知限制

- 不可测的轨（静音 / no_valid_anchor / 非恒定）仍然不能修，AUD-04 记 WARN。
- 无 moov 的零填充文件无法恢复，只能报 `UNREADABLE`。
- `--fix-layout` 会触发整文件重交织，最慢，默认关闭。
- 大批量仍建议把 `ARCHIVE_REPAIR_SCRATCH` 指到大容量盘；每个 worker 独立子目录。

---

## 9 2026-10-26 第二次升级：自主批处理 + 合入主程序（v0.8.8）

这一节回答一个问题：**修复程序能不能自己把一整批文件修完并证明修对了，不需要人守在旁边？**
答案是能，但前提是把"修对"变成程序自己的判据，而不是操作者的肉眼复检。

### 9.1 一条命令跑完全批（不再需要人工逐个目录调用）

```bat
python 1kt.py --repair --input I:\ --output G:\1kt_repair_v3_20261026 ^
    --repair-dirs "^\d{8}$" --jobs 8
```

- `--repair-dirs REGEX`：只遍历输入根下的**一级子目录名完全匹配**的目录。
  整盘输入时必须给，否则 `_pre_repair_backup`、`compact`、`repaired` 等
  非本轮对象会被一起扫进来。
- `--jobs N`：按文件并行（每个 worker 独立 `scratch/pid<pid>`；BLAS/OpenMP
  固定单线程）。
- `--repair-resume`：断点续跑。每条成功记录在完成时立刻追加到输出目录的
  `_progress.jsonl`，重启后只重跑未完成/失败的文件（成品不存在或上次失败
  的文件不会被跳过）。
- 失败文件自动**串行**重试（精确模式，1→2→5 样本分级 + 施加后复检）；
  一次性失败不再让整批中断。
- 结束写三份总账：`_summary.json`（机器读）、`_summary.md`（人读）、
  `archive-repair-report.json/md`（逐文件检查项与决策）。
- 退出码：只有真正的 `FAILED` 返回 1；`PARTIAL`（`.part` 半成品）与
  `UNREADABLE`（零填充/无 moov）是输入本身的限制，如实列进总账但不让
  批处理循环报错。

### 9.2 成人品硬闸门（"修对了"由程序自己证明）

2026-10-26 的并发批处理实测暴露了一个必须正视的问题：**并行路径在高负载下
偶发产出损坏的成品**。两份实测样本：

| 现象 | 结构检查 | 只有哪个检查能发现 |
|---|---|---|
| `20260830_C0912` 视频样本被写坏（解码报 `Invalid NAL unit size`） | `moov`/时长/tkhd 全部正常；`validate.compare` 仍 `structural_success=True`，只多一条 `nrtm.lens_profile` MODIFIED | 逐流解码探针（`FMT-04`） |
| `20260830_C0951` 拷贝不完整（成品 216 MB vs 源 304 MB） | 容器结构可读 | 文件大小 + 逐包载荷 |

因此 `repair` 在交付前现在强制通过两道闸门（**不是可选项**）：

1. **`validate.compare` 无 MODIFIED / MISSING**：brand、tref、nrtm、uuid、
   rtmd 载荷等 43 项逐项与源相同；任一不同 → 判失败，不交成品。
2. **视频载荷逐包相同（streamhash）**：`ffmpeg -map 0:v:0 -c copy
   -f streamhash sha256` 对比源与成品。容器级修复只做字节级拷贝 + 等长
   字段改写，所以还额外要求**成品文件大小 == 源文件大小**。

闸门失败的文件进入串行重试；重试仍失败就如实报 `FAILED`（附错误），
**绝不静默交出一个"看着像成品"的坏文件**。

### 9.3 最新稳定算法 = 生产工作点 + 分组锚点（默认开）

修复侧的默认参数现在与生产 `start.bat` 对齐：

| 项 | 默认 | 依据 |
|---|---|---|
| `production_opts()`（materiality_k=3 / verify_max_rel=0.2 / recheck_windows=3 / verify_agree_samples=20 / track_reference=True） | 开 | v0.8.7 生产工作点 |
| 分组锚点 `group_anchor_enabled` | **开**（`--no-groups` 可关） | `start.bat` 的 `GROUPS=1`；全语料/留出/归档三重验证只增不减 |
| 精确模式 `channel_sync_accurate` | 关（失败重试时自动打开） | 产品决定：1→2→5 样本分级由施加后复检验收，属 opt-in |
| 音频残留迭代（`repair_residual_audio`，最多 2 轮） | 开 | 首轮宽窗采纳后仍有 1–20 样本残差时自动再修（实测 C0925 +20、C1167 +1144、C1207 +1136） |
| 容器级一键修复（tkhd/sample entry/时长） | 开 | 见 §8.1 |

### 9.4 合入主程序 + 版本

- `archive_repair.py` 的能力现在通过主程序入口暴露：
  `1kt.py --repair ...`（同样的 `--input/--output`，新增 `--repair-*` 选项）。
  批处理链只需要调用一个程序，不再依赖"记得去跑旁路脚本"。
- `VERSION` **0.8.7 → 0.8.8**；`1kt.py --version` 输出 `1KeyTranscoder 0.8.8`。
- 新增的自测（`tests/selftest/suites/hardware_audio.py`）把这组 CLI 参数
  手工登记进白名单，并断言 `--repair` 真的分派到 `archive_repair.run_repair`，
  防止将来变成空开关。

### 9.5 一条真实编码测试（非长程）

改主程序后跑了一次**短素材真实编码**（不跑长程测试）：

| 项 | 值 |
|---|---|
| 输入 | `20260903_C1090.MP4`（3840×2160 10-bit 4:2:2，11.0 s，256.1 MB） |
| 命令 | `1kt.py --input … --output … --encoder x265 --preset hq --jobs 1 --check basic --headless` |
| 结果 | `done=1 skipped=0 failed=0`；`PRESERVED=37 MODIFIED=0 MISSING=0 structural_success=True`；耗时 384.6 s |
| 成品 | 80.0 MB；再用最新 `check` 复检 **PASS**（容器/音频/格式全部通过） |

### 9.6 全量批处理实测（I: 18 个 YYYYMMDD 目录，669 文件）

运行命令（后台整批，运行期间只读日志、不打断）：

```bat
python 1kt.py --repair --input I:\ --output G:\1kt_repair_v3_20261026 ^
    --repair-dirs "^\d{8}$" --jobs 8
```

| 指标 | 值 |
|---|---|
| 输入 | 18 个 `YYYYMMDD` 目录，669 个媒体文件，94.5 GB |
| 结果 | **REPAIRED 661 / PARTIAL 2 / UNREADABLE 6 / FAILED 0** |
| 实际改音频的文件 | 45（其余 616 只需容器修复） |
| 耗时 | **1467.8 s（24.5 min）**，jobs=8，含成人品载荷校验 |
| 成品 | 661 个文件，93.7 GB（暂存，未写回 `I:\`） |
| 首轮失败 → 闸门发现 | **7 个**，全部由成人品闸门在交付前拦下：2 个 `validate.compare` 不一致（C0913 MODIFIED=16/MISSING=1、C0923 MODIFIED=1）、5 个 sample-entry 与源不一致；**串行精确重试 7/7 REPAIRED** |
| 闸门覆盖 | applied 45/45 视频载荷相等；container_fixed 616/616 大小相等且视频载荷相等 |
| 残差音频追加轮 | 3 个文件（C0925 1 轮、C1167 2 轮、C1204 1 轮）→ 最终全部 `already_aligned` |
| 已知不可修 | 2 个 `.part` 半成品（PARTIAL，不产出）；6 个零填充/无 moov 文件（UNREADABLE，如实报告） |
| 独立复检 | 结果见 §9.7 |

**这次实测证明了两件事**：

1. 并发路径确实会在高负载下偶发产出损坏成品（7/45 个需要音频重封装的文
   件在首轮被闸门拦下），但**闸门 + 串行重试把它变成了可自动收敛的失败**，
   最终 0 FAILED。
2. 失败文件串行重试全部成功，说明"并发只做发现、串行负责收口"的分层是
   有效的。**没有静默交付任何坏文件**。

### 9.7 独立复检（成品上重新测量/解码，不复用修复记录）

命令：`archive_repair.py check --input G:\1kt_repair_v3_20261026 --jobs 8`
（661 个成品逐个重新 probe + 容器检查 + 音频重新测量 + 解码探针）。

| 结果 | 数量 | 说明 |
|---|---|---|
| **PASS** | **640** | 容器/格式/音频/解码全部通过 |
| **WARN** | **21** | 全部是 `AUD-04`：质量门拒绝且仍带 ≥5 样本偏移的轨（`non_constant` / `low_confidence`），属 §8.4 的已知不可修类；不是本次修复引入 |
| FAIL / UNREADABLE | **0 / 0** | — |

与修复前的账目交叉核对一致：输入 669 = 成品 661 + `.part` 2（不产出）+
零填充 6（不可解析）；每个 `YYYYMMDD` 目录的输入/成品数逐目录相同
（含 `A7M5` 等子目录，例如 20260901：229 产出 / 235 输入 = 6 不可解析）。

### 9.8 已知限制（本次新增）

- **并发路径仍会偶发损坏**：8 并发下实测 45 个需音频重封装的文件里有 7 个
  首轮产出被闸门判坏（视频载荷/元数据/大小不一致）。程序会串行重试并
  重新验收；本次 7/7 成功，没有 FAILED。若未来某批的重试也失败，该文件会
  以 `FAILED` 明确列出，**不会静默交付**。
- 因此大批量建议保留 `--jobs 8` 的吞吐，但把"串行收口"视为修复程序的
  正式组成部分（它已内置，不需要人工介入）。
- 成人品载荷校验（视频 streamhash）会让每文件多读一遍源与成品：
  669 文件 / 94.5 GB 的实测耗时 24.5 min（不含复检）。


