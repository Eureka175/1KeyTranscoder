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
