# Hardware Codec Policy & Execution Pipeline Redesign — 规划书

> ## ⚠️ 实施状态（此区块由实施 session 添加，正文保持不变）
>
> **本规划书已被实施。** 实施分支 `feature/hardware-codec-policy-redesign`，
> 从 `fc183b6` 起，最新提交见该分支。
>
> **维护者的后续指令覆盖了本文的两项设计**，正文中对应段落仍按原样保留，
> 但**以下两项不再是当前策略**（详见 §9.2 与 §19 的 `SUPERSEDED` 标注）：
>
> | 原设计 | 现状 |
> |---|---|
> | §9.2 / OD-1：硬件解码设为默认，需要 production benchmark 前置 | **已被覆盖**：所有输入类别、所有编码后端统一硬件优先，**不再以 benchmark 为前置条件** |
> | §9.2.1 / OD-2：普通 Sony 模式默认不得硬解 | **已被覆盖**：Sony 不再因身份被排除在硬解候选之外 |
> | 所有后端统一"FFmpeg 硬解 + 帧传递到任意编码器" | **已被覆盖**：解码路径按后端分家。rigaya 编码器用**自己的** `--avhw` 读端；FFmpeg 硬解只服务于 FFmpeg 自己的编码器（x265/SVT-AV1） |
>
> 现行策略一句话：**所有视频输入统一硬件优先解码；Sony 保留管线照常运行，
> 但不再单纯因 Sony 身份强制软解。** 性能事实仍据实记录，不宣称普遍加速。
>
> 现行架构（**解码器选择与编码器选择是两个独立决策**）：
>
> ```
> x265 / svtav1   source -> FFmpeg NVDEC/QSV -> hwdownload + 像素转换
>                        -> FFmpeg libx265/libsvtav1 -> output
>
> nvenc / qsv     source -> NVEncC/QSVEncC --avhw（原生读端） -> 编码器 -> output
>                 不可用时 -> 同一个编码器的 --avsw（仍由它自己读文件）
> ```
>
> **不存在** `source -> FFmpeg NVDEC/QSV -> hwdownload -> Y4M -> NVEncC/QSVEncC stdin`
> 这条路。它在实施过程中曾短暂存在（提交 `eaf8034`），随后按架构决定整体移除。
>
> **实施状态摘要**（完整报告见实施 session 的输出）：
>
> | 项 | 状态 |
> |---|---|
> | FFmpeg 硬解规划器 + 逐设备能力探测 + 传输链探测 | **已实现** |
> | x265/SVT-AV1 路径消费 DecodePlan | **已实现** |
> | Sony 4:2:0 保留 + FFmpeg 硬解端到端 | **通过**（PRESERVED=37 / MODIFIED=0） |
> | 移除 3 个旧开关 + `--preset all`，双侧 V080_BASELINE | **已实现** |
> | `--no-metadata` | **已实现并端到端验证** |
> | 每 job `.1ktwork` 清理 | **已实现** |
> | `--jobs auto` 默认 + 上限校验 + multihw 冲突 | **已实现** |
> | FFmpeg → Y4M → rigaya stdin 管道 | **已移除**（`ffmpeg_pipe_args` / `apply_stdin_pipe` / `run_hw_tool(decode_pipe=)` 三者都已删除，无残留调用点） |
> | rigaya 原生 `--avhw` 路由恢复 | **已实现**（`hw_encode_with_fallback` 按读者选择可执行文件；`--avhw` 用 patched 构建、`--avsw` 用装船构建） |
> | rigaya 原生 `--avhw` 实际运行 | **BLOCKED**：`tools/avhw/` 下的 patched 构建缺失，且本机无 MSVC / oneVPL / rigaya 源码，无法重建；解析结果 `not_proven` → `--avsw` |
> | 完整性闸门（丢产物 + 有界重试 `--avsw`） | **已实现并单测钉住**（37 例 `native_decode` 套件） |
> | 软件路径并发接入 | **PENDING**（需 x265 线程标定，§14.6） |
> | 多设备资源预算（每设备 VRAM） | **PENDING** |
> | hwdecode 83 用例矩阵 | **BLOCKED**（`tools/avhw/` 缺失，provenance 失败：2 failure） |
> | Sony 4:2:2 保留校验 | **既有缺陷，非本次回归**（FFmpeg 中间体 mvhd timescale=1000 的毫秒量化；30 帧 @59.94p = 500.5ms 无法表示） |
>
> ---
>
> **文档类型**：独立规划书（planning-only，撰写时）。
>
> **基线**：分支 `research/audio-sync-p3-production-semantics`
> @ `2b34d41589d448439e835e1cf289962b5bc95fe2`，工作树含 8 个未提交修改 + 4 个未跟踪目录。
> 详见 §1.2——**下一 session 必须重新确认基线，不得直接沿用本文的 commit 号**。
>
> **证据标记**（每项重要结论都带一个）：
>
> | 标记 | 含义 |
> |---|---|
> | **CONFIRMED** | 当前代码或历史实测**直接**支持，已引用 `file:line` 或实测命令 |
> | **INFERRED** | 根据现有实现**推断**，仍需验证 |
> | **UNKNOWN** | 仓库中没有足够证据 |
> | **PROPOSED** | 本次建议的新设计，尚未实现 |
> | **OPEN DECISION** | 未解决的技术/产品选择，**必须由人决定**，本文不替你决定 |
>
> **本 session 的实测**（全部为只读操作，脚本落在 gitignored 的
> `work/plan_probe/`，不进入仓库）：`work/plan_probe/compare.py`、
> `caps_probe.py`、`ab_decode_test.py`。§5 的所有数字都来自这些运行。

---

## 目录

| § | 内容 |
|---|---|
| [1](#1-当前仓库状态与基线) | 当前仓库状态与基线 |
| [2](#2-当前执行架构) | 当前执行架构 |
| [3](#3-当前硬件能力探测链) | 当前硬件能力探测链 |
| [4](#4-当前-ffmpegx265-路径) | 当前 FFmpeg/x265 路径 |
| [5](#5-ffmpeg-硬件解码--libx265-可行性实测本-session-的核心新证据) | **FFmpeg 硬解 + libx265 可行性实测（核心新证据）** |
| [6](#6-当前-nvenc--qsv-路径) | 当前 NVENC / QSV 路径 |
| [7](#7-历史-sony-解码问题及其证据) | 历史 Sony 解码问题及其证据 |
| [8](#8-三个独立概念policy--decoder--encoder) | **三个独立概念：Policy / Decoder / Encoder** |
| [9](#9-新的默认解码策略) | 新的默认解码策略 |
| [10](#10-新的编码器优先级) | 新的编码器优先级 |
| [11](#11---no-metadata) | `--no-metadata` |
| [12](#12---experimental-multihw) | `--experimental-multihw` |
| [13](#13-多-gpu-横向扩展) | 多 GPU 横向扩展 |
| [14](#14-jobsauto-与资源调度) | `jobs=auto` 与资源调度 |
| [15](#15-旧-cli-switch-移除清单) | 旧 CLI switch 移除清单 |
| [16](#16-1ktwork-生命周期) | `.1ktwork` 生命周期 |
| [17](#17-测试矩阵) | 测试矩阵 |
| [18](#18-代码修改范围) | 代码修改范围 |
| [19](#19-风险与未决事项) | 风险与未决事项 |
| [20](#20-后续实施顺序) | 后续实施顺序 |
| [21](#21-发布兼容性) | 发布兼容性 |
| [22](#22-p4-当前状态与隔离声明) | P4 当前状态与隔离声明 |
| [23](#23-给下一个-session-的实施要求) | 给下一个 session 的实施要求 |

---

# 1. 当前仓库状态与基线

## 1.1 检查方法

```powershell
git rev-parse HEAD ; git branch -a ; git tag --list ; git log -20
git status --short ; git diff --stat ; git rev-list --left-right --count origin/main...main
```

## 1.2 实测结果（CONFIRMED）

| 项 | 值 | 证据 |
|---|---|---|
| **仓库根** | `F:\1KeyTranscoder`（= 当前工作目录） | `git rev-parse --show-toplevel` |
| **remote** | `https://github.com/Eureka175/1KeyTranscoder.git` | `git remote -v` |
| **当前分支** | `research/audio-sync-p3-production-semantics` | `git rev-parse --abbrev-ref HEAD` |
| **当前分支 HEAD（撰写时基线）** | `2b34d41589d448439e835e1cf289962b5bc95fe2` | `docs(audio-sync): close P4 engineering status`，2026-10-12 13:13:01 +0800 |
| **`main` HEAD** | **`97d587651afe1c1eb838a97cd95953b53b2b4e19`**；本地 `main`、`origin/main`、`origin/HEAD` **三者同为 `97d5876`** | `git for-each-ref refs/heads/main refs/remotes/origin/main refs/remotes/origin/HEAD` → 三者一致，committer date 2026-09-16 |
| **`main` 与当前分支的关系** | **`main` 是当前分支的祖先**；当前分支领先 `main` **89 个提交**，`main` 上无独有提交 | `git merge-base --is-ancestor 97d5876 HEAD` → exit 0；`git rev-list --left-right --count main...HEAD` → `0  89` |
| **`VERSION` @ HEAD / @ main** | `0.8.0` | `git show main:VERSION` |
| **`VERSION` @ 工作树** | **`0.8.1`**（未提交） | `git diff VERSION` |
| **`git describe`** | 撰写时基线 `2b34d41` 为 `v0.8.0-92-g2b34d41`（v0.8.0 之后 92 个提交）<br>**注意**：该计数**包含 `main` 上 `v0.8.0` 之后的 4 个提交**，因此不等于"研究线产出 92 个提交" | `git describe --tags 2b34d41`、`git rev-list --count v0.8.0..main` → `4` |
| **最新正式 tag** | `v0.8.0` = `c5ae9b1`（2026-09-16）；`v0.8.0` 之后**没有更高 tag** | `git tag --list`、`git log -1 v0.8.0` |
| **tag 总数 / 归档 tag** | **34**；含 `archived/*`、`post_*`、`pre_*` 历史 tag | `git tag --list \| Measure-Object` |
| **`v0.8.0` 与 `main` 的关系** | `v0.8.0` 是 `main` 的祖先；`main` 在 `v0.8.0` 之上还有 4 个提交 | `git merge-base --is-ancestor v0.8.0 main` → 0；`git rev-list --count v0.8.0..main` → `4` |
| **fetch 状态** | 远端**无新提交**；但 2 个 tag 会被覆盖（`v0.3.0-beta`、`v0.6.0` "would clobber existing tag"） | `git fetch --all --tags --dry-run` |
| **本地领先远端** | 当前分支领先 `origin/research/audio-sync-p3-production-semantics` **1 个提交** | `git rev-list --left-right --count` → `0  1` |

> **⚠️ 勘误（本表初版的一处错误，已修正 —— 请勿沿用任何二手摘要）**
>
> 本表初版把 **`main` HEAD 写成了 `2b34d41`**，并声称它与 `origin/main` 一致。
> **这是错的**：`2b34d41` 是**当前研究分支自己的 HEAD**，不是 `main`。
> 撰写时，早期一次 `git log -1` 是在当前分支上执行的，其输出被误抄进了
> "`main` HEAD" 一行。
>
> **正确事实**：`main` = **`97d5876`**（2026-09-16），
> 本地 `main` / `origin/main` / `origin/HEAD` **三者一致**；
> **`main` 是当前分支的祖先**，研究分支领先 `main` **89** 个提交
> （其中 88 个是 `2b34d41` 之前的研究线产出，第 89 个是本规划书的提交）。
>
> **为什么必须记住这件事**：本文的 §23.1 要求下一个 session
> "从当前稳定基线创建新分支"。若按错误版本以为 `main == 2b34d41`，
> 就会把一个**并不存在的关系**当成事实，并可能把研究分支的内容误当主线基线。
> **`main` 落在 2026-09-16，比研究分支落后 89 个提交**，
> 因此"稳定基线"到底是 `main` 还是研究分支 HEAD，**是一个必须由维护者
> 明确回答的问题**（见 §23.1 步骤 2 的补充要求）。
>
> 本表其余各行（分支名、`VERSION`、tag、未提交内容、远端领先数）
> 均已用独立命令复核无误。

### 1.2.1 未提交的在工作树内容（**关键**）

`git status --short` 显示 **8 个已修改文件 + 4 个未跟踪路径**，且 `VERSION` 已被改成 `0.8.1`：

```text
 M VERSION                            |   2 +-
 M core/batch_hw.py                   |  62 +++-
 M preservation/checker.py            |  28 ++-
 M preservation/gpac.py               | 462 ++++++++++++++++++++++++++--
 M preservation/validate.py           |  49 ++-
 M tests/selftest/runner/engine.py    |   6 +-
 M tests/selftest/suites/core.py      |  89 ++++++
 M tests/selftest/suites/toolchain.py | 154 ++++++++++
?? docs/deploy/                        （含 v0.8.1-gyroflow-fix.md）
?? docs/hardware-decode/xavc-hs-frame-count.md
?? docs/x265-calibration/
?? upstream-issues/
```

**这批改动与本次规划直接相关**（CONFIRMED，`git diff core/batch_hw.py`）：

* `place_final()` 新增 `meta_source` 参数（`core/batch_hw.py:1914` 区域），在容器重封装后
  **断言文件级 `meta`（Sony `nrtm`）与 `tref/cdsc` 存活**，丢失即抛错并删除 staged 文件；
* 新增 `_meta_manifest(work_dir)`（`core/batch_hw.py:2035` 区域）读
  `work_dir/metadata/manifest.json`；
* `encode_one_sony_hw` 的 Gyroflow 判定从 `status != "PASS"` 改为 `status == "FAIL"`
  （`SKIPPED` 不再阻断交付）。

> **⚠️ 基线风险（CONFIRMED）**：`VERSION` 已是 `0.8.1`、工作树有一批未提交的容器/元数据修复。
> 本文档的所有代码行号引用都以
> **`2b34d41` 的已提交内容 + 上述未提交修改**这一具体状态为准。
> **下一 session 必须在动手前重新执行 §1.1 并确认这三件事**：
> ① 那 8 个文件是否已被提交/合并；
> ② `VERSION` 到底是 `0.8.0` 还是 `0.8.1`；
> ③ `docs/hardware-decode/xavc-hs-frame-count.md` 是否已跟踪。
> 若任一不符，本文的行号引用需要重新核对（**不要盲信行号**）。

### 1.3 相关分支

| 分支 | 说明 | 证据 |
|---|---|---|
| `main` | 主线，`v0.8.0`+92 | `git branch -a` |
| `feature/hardware-decode-integration` | v0.7.0 硬件解码 integration 的产出分支（**已并入 main**） | `docs/hardware-decode/final-report.md` §2 |
| `research/audio-sync-p3-production-semantics` | **当前分支**，P3/P4 音频同步研究线 | 见 §22 |
| `feature/audio-sync-p2-drift`、`feature/audio-sync-timevarying-correction`、`research/audio-sync-p3-*`（6 个） | 音频研究历史分支 | `git branch -a` |
| `feature/x265-calibration-collector`、`fix/hardware-container-format` | 其它特性分支 | `git branch -a` |

### 1.4 检查过的文件是否存在

| 规划书要求阅读的路径 | 实际状态 |
|---|---|
| `1kt.py` | ✅ 2255 行 |
| `encoders/x265.py` | ✅ 300 行 |
| `encoders/nvencc.py` | ✅ |
| `encoders/qsvencc.py` | ✅ |
| `encoders/svtav1.py` | ✅ |
| `encoders/hwdecode.py` | ✅ 428 行 |
| `encoders/integrity.py` | ✅ 539 行 |
| `encoders/caps.py` | ✅ 215 行 |
| `core/batch_hw.py` | ✅ 2463 行 |
| `core/config.py`、`core/probe.py`、`core/paths.py` | ✅ |
| `preservation/pipeline.py`、`sony.py`、`dji.py` | ✅ |
| `release/build_release.py` | ✅ |
| `docs/README.md` | ✅ |
| `docs/hardware-decode/README.md`、`final-report.md`、`integration-test-matrix.md`、`toolchain-provenance.json` | ✅ 全部存在 |
| `olddocs/docs/hardware-decode/research-conclusion.md`、`rigaya-avhw-analysis.md` | ✅ 存在（另有 17 个归档文档） |
| **`docs/plans/`** | ❌ **不存在** —— 本规划书是它的第一个成员（见 §1.5） |
| `encoders/hw.py` | ✅ 13444 B（**规划 prompt 未列出，但它是能力规划的核心**） |
| **`--no-metadata`** | ❌ **仓库中完全不存在**（全仓 grep 确认）→ §11 是纯新增功能 |

### 1.5 文档组织约定（决定本规划书放哪）

* `docs/` 下**没有** `plans/` 目录。现有子目录按**主题/阶段**组织：
  `design/`（架构与设计）、`hardware-decode/`（integration 交付物）、
  `audio-sync-p2|p3|p4/`（研究线）、`deploy/`、`evaluation/`、`fixtures/`、
  `reference/`、`x265-calibration/`。
* 命名约定：**kebab-case `.md`**，研究线文档带阶段前缀（`p4.9a-...`）。
* **PROPOSED**：新建 `docs/plans/`，本文件命名为
  `docs/plans/hardware-codec-policy-redesign.md`（与 prompt 建议一致，符合 kebab-case 约定）。
  这是一个**新增目录**，理由：本规划书是跨领域的策略重构，不属于 `design/`
  （已冻结的架构描述）也不属于 `hardware-decode/`（已完成的 integration 档案）。
  **OPEN DECISION**：若维护者偏好扁平结构，可改为
  `docs/design/hardware-codec-policy-redesign.md`；这会影响后续规划文档的存放约定。

---

# 2. 当前执行架构

## 2.1 实测入口分支（CONFIRMED）

`1kt.py:1667 main()` 在解析参数后先做二选一分支：

```text
main()                                   1kt.py:1667
├── --channel-sync-transparent  →  transparent_main()      1kt.py:1473
│                                   （跳过视频编码，视频 stream copy）
└── 否则                        →  正常转码管线
```

**两个分支不共享编码逻辑**（`docs/design/architecture.md` §1）。
本规划书**只涉及正常转码管线**；`transparent_main()` 与视频编解码无关
（它是纯音频预处理），**不在本次范围**。

## 2.2 当前正常管线的完整数据流（CONFIRMED，逐行核对）

```text
1kt.py:1668   args = parse_args()
              │
1kt.py:1671   input_root / output_root 解析 + 校验（output 不得在 input 内）
              │
1kt.py:1687   ffmpeg / ffprobe / gpac / gyroflow 工具链解析
              │
1kt.py:1715   ┌─ 后端解析（四种来源互斥，priority 高→低）
              │    1. --config <file>          → JSON 的 "encoder" 字段     1715-1734
              │    2. --encoder <name>          → 显式                     1736
              │    3. --experimental-multihw    → 自行探测 → "nvenc" 占位   1738-1742
              │    4. 都没给                    → resolve_default_backend()  1744-1748
              │
1kt.py:1762   is_hardware = encoder_name in ("nvenc","qsv","nvenc-av1","qsv-av1")
              │
1kt.py:1770   ┌─ --experimental-multihw 二次校验与探测                      1770-1794
              │    · args.encoder 非 nvenc/qsv → ValueError（硬冲突）
              │    · _hw_encoders_available() 探测
              │    · 无可用户后端 → ValueError("requires at least one ...")
              │    · multihw_backends[0] 成为主后端；is_hardware = True
              │
1kt.py:1799   ┌─ 软件后端构造
              │    x265    → load_config + scaling + X265Backend()          1799-1807
              │    svtav1  → load_svtav1_config + SvtAv1Backend()           1808-1816
              │
1kt.py:1821   output_root.mkdir() ; logs_root = input_root.parent / "logs"
              │
1kt.py:1891   work_root = output_root / ".1ktwork"
1kt.py:1892   logger = setup_logger(...)
              │
1kt.py:1906   ┌─ 硬件后端构造
              │    multihw → hw_backend_for("nvenc") + hw_backend_for("qsv")  1923-1924
              │    否则   → hw_backend_for(encoder_name)                     1926
              │      └─ core/batch_hw.py:310 hw_backend_for()
              │           · find_hw_tool()           → 编码 tool
              │           · resolve_decode_tool()    → 硬解 tool（仅 policy != off）
              │           · probe_backend(tool, kind) → --check-features
              │           · caps.save(work_root/"caps")
              │
1kt.py:1929   collect_versions() → env_versions.json / .csv
              │
1kt.py:1986   --jobs 校验（"1" | "auto" | int()）
              │
1kt.py:2036   for preset in requested:            ← 可选多档位外层循环
                 │
                 ├─ 【软件路径】is_hardware == False                       2040-2175
                 │     for src in sources:             ← **严格串行，忽略 --jobs**
                 │       prepare_source()          → 探测 + 分类 + 缩放
                 │       ├─ rtmd 数据流存在 → encode_one_sony()      1kt.py:686
                 │       ├─ is_dji_source()  → encode_one_dji_x265() 1kt.py:913
                 │       └─ 否则             → encode_one()          1kt.py:481
                 │       place_final()             → 容器交付
                 │
                 └─ 【硬件路径】is_hardware == True                        2177-2229
                       ctx = BatchCtx(...)                              2178
                       ├─ multihw + ≥2 后端 → run_multihw_pool()        2216
                       ├─ multihw + 1 后端  → run_hw_pool(jobs_value)   2223
                       └─ 普通              → run_hw_pool(jobs_value)   2227
                             │
                             core/batch_hw.py:2101 process_file_hw()
                               ├─ dst 已存在非空 → "skipped"                2120
                               ├─ prepare_source_hw()                       2127
                               ├─ audio_plan 拒绝（Sony/DJI）               2158
                               ├─ is_sony_source() → encode_one_sony_hw()   894
                               ├─ is_dji_source()  → encode_one_dji_hw()    1463
                               └─ 否则             → encode_one_hw_classic() 1152
```

## 2.3 硬件路径内部的统一降级梯（CONFIRMED）

三个 `encode_one_*_hw` **都**最终进入同一个函数：

```text
core/batch_hw.py:397  hw_encode_with_fallback()
│
├─ 1. caps = backend.caps                                    434
├─ 2. planned, needs_downgrade = plan_initial_format(...)     438   ← encoders/hw.py:368
│      · AV1        → 恒 4:2:0（任何非 4:2:0 源均降采样 + 警告）
│      · QSV + 4:2:2 → 恒转 4:2:0（Arc 直编 4:2:2 是慢路径）
│      · NVENC + 4:2:2 → 能力允许则保留
│      · depth>8 但不支持 → 4:2:0/10
├─ 3. --no-downgrade 且 needs_downgrade → RuntimeError（跳过该文件）442
├─ 4. rungs = downgrade_ladder(*planned) ∩ supports(caps)     455   ← encoders/caps.py:205
│      · rung0 = planned，rung1 = 4:2:0/10，rung2 = 4:2:0/8
├─ 5. route = route_decode(policy=hw_decode, ...)             466   ← encoders/hwdecode.py:135
│      · policy off  → avsw，不出声
│      · seek 请求   → seek_not_equivalent → avsw
│      · REFUSED 命中 → capability_refused → avsw
│      · memo 命中    → reader_unavailable → avsw
│      · 不在 PROVEN  → not_proven → avsw
│      · 命中 PROVEN  → avhw
├─ 6. require 未满足 → RuntimeError                            480-483
│
└─ 7. while rung_idx < len(rungs):                            494
       ├─ backend.command(..., reader=reader)                 496
       ├─ run_hw_tool() → rc, elapsed                         521   ← encoders/hw.py
       ├─ if reader == "avhw":  → verify_encode() 完整性闸门   527   ← encoders/integrity.py:285
       │     · 失败 → safe_unlink(output) + memoize + 软解重跑同一 rung
       │     · hw_fallbacks > MAX_HW_DECODE_FALLBACKS(=1) → FATAL  561
       ├─ 成功判定：rc==0 && 产物存在非空 &&（do_frame_check 时帧数 1:1）579
       ├─ cls = classify_failure(tail)                        613   ← encoders/hw.py:240
       │     · "reader"      → strip_video_audio() 兜底一次    615   ← batch_hw.py:244
       │     · "environment" → 立即 FATAL，不降级              637
       │     · "format"      → rung_idx += 1                   654
       └─ 全 rung 失败 → FATAL                                 662
```

## 2.4 硬件路径 vs 软件路径的能力差异（CONFIRMED）

| 维度 | 软件路径（x265/svtav1） | 硬件路径（nvenc/qsv） |
|---|---|---|
| 并发 | **严格串行**，`--jobs` 完全无效（`1kt.py:2043`） | `run_hw_pool` / `run_multihw_pool` |
| 编码前能力探测 | 无（只做缩放/分类） | ✅ `caps.probe_backend()` |
| 解码器选择 | **无选择**：FFmpeg 默认软解 | ✅ `route_decode()`（但白名单 3 项） |
| 帧数对账 | 无（`encode_one` 只查产物非空） | 硬解时五方对账；软解时看 `do_frame_check` |
| 输出验证 | `--check` 三级（Sony/DJI 保留管线） | 同上 + 完整性闸门 |

> **这是本次重构要同时触碰的两个世界**：目标架构要求 FFmpeg 路径也具备
> **编码前探测 + 独立解码器选择**（§8、§9），而今天 FFmpeg 路径**两样都没有**。

---

# 3. 当前硬件能力探测链

## 3.1 真实顺序（CONFIRMED，逐行核对；与"输入探测→编码器探测→解码器探测"的理想顺序**不同**）

```text
【进程启动阶段】1kt.py main()
  │
  ├─ A. 自动选择探测（仅当未给 --encoder 且未给 --config 且非 multihw）
  │     resolve_default_backend()                        1kt.py:1445
  │       └─ _hw_encoders_available()                   1kt.py:1405
  │            for name in ("nvenc", "qsv"):            1kt.py:1401 AUTOSELECT_ORDER
  │              find_hw_tool(script_dir, exe_name)     → 工具是否存在
  │              probe_backend(tool, kind)              → --check-features   ★ 唯一真实探测
  │              caps.codecs["hevc"] 必须 bit10/422/444/bit10_422 之一 → 否则视为不可用
  │
  ├─ B. 硬解工具解析（仅当 --hw-decode != off）
  │     core/batch_hw.py:310 hw_backend_for()
  │       ├─ find_hw_tool()                             ← **确定性排除 tools/avhw/**
  │       └─ resolve_decode_tool(kind, policy)           encoders/hwdecode.py:377
  │            · explicit 路径优先
  │            · 否则 tools/avhw/... 的 PATCHED_BUILD，**必须 sha256 命中**（336-347）
  │            · 缺失或 hash 不符 → usable=False + not_proven
  │
  └─ C. 编码能力探测（每个后端一次）
        probe_backend(tool, kind)                      encoders/caps.py:150
          ├─ run_tool(tool, "--check-features")        encoders/caps.py:156
          ├─ raw 为空或不含 "Codec:" → return None     157-158
          ├─ _parse_nvenc() / _parse_qsv()             82 / 105
          ├─ _env_info() → device / driver             128
          └─ caps.save(work_root/"caps")               367  ← 写 <tool>_caps.json + _caps_raw.txt
```

**★ 关键事实：能力探测发生在"任何输入被探测之前"，且**与输入无关**。**
`probe_backend()` 只跑 `--check-features`（工具级、机器级），**不接触任何媒体文件**。

## 3.2 每个文件的运行时顺序（CONFIRMED）

```text
process_file_hw()                                   core/batch_hw.py:2101
  │
  ├─ 1. 输出已存在？ → skipped                       2120
  ├─ 2. prepare_source_hw()                          2127
  │      └─ probe_source(ffprobe, src)               core/probe.py
  │           → codec / profile / pix_fmt / width / height / fps /
  │             bit_depth / chroma / total_frames / audio_streams /
  │             data streams / colour
  │      └─ build_source_info()                      → SourceInfo
  ├─ 3. is_sony_source(streams) / is_dji_source()    1753 / 1762
  └─ 4. encode_one_*_hw()
         └─ hw_encode_with_fallback()
              ├─ plan_initial_format(caps, kind, chroma, depth, codec)   ← 能力 ∩ 输入
              ├─ route_decode(policy, backend, codec, chroma, depth)     ← 输入形态
              ├─ backend.command(source, output, profile, chroma, depth,
              │                  vfr, audio_copy, color, reader, container)
              └─ run_hw_tool(cmd, raw_log, total_frames, label)
```

## 3.3 现有探测体系的四个结构性缺口（CONFIRMED）

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| **G1** | **解码能力完全没有被建模** | `encoders/caps.py:11-13` 模块 docstring：<br>"Decode-side capabilities are **NOT** modeled: hardware encode paths always decode with `--avsw` (software), so decode capability is irrelevant by design." | 硬解能力只靠 `hwdecode.PROVEN` 这个**三行硬编码白名单**，不是探测结果 |
| **G2** | **能力缓存只写不读** | `caps.save()` 有写（`caps.py:52`）；全仓 grep `_caps.json` / `load_caps` / `from_json` → **零读取路径**。`BackendCaps` 没有反序列化器 | `--check-features` **每次运行都重跑**（每次数秒）；`docs/design/implementation_report.md:79` 声称"键 = 工具版本+驱动+设备"，**代码中不存在该缓存键** |
| **G3** | **FFmpeg 能力从未被探测** | 全仓 grep：没有任何代码查询 `ffmpeg -hwaccels` / `-decoders` / `-init_hw_device` | FFmpeg 路径的解码器选择**完全没有依据** |
| **G4** | **探测失败只有一条降级路径** | `caps=None` → `BackendCaps(codecs={"hevc": CodecCaps()})`（全 False）+ WARNING（`batch_hw.py:358-366`）。`subprocess.TimeoutExpired`（`caps.py:62-72`, `timeout=120`）**未被捕获** | 探测超时会让整个进程崩掉；"探测失败"与"能力确认为否"在数据结构上**不可区分** |

> **规划含义**：prompt 要求"不得直接重写另一套探测器"。现有探测器是
> `encoders/caps.py`（编码侧，rigaya `--check-features` 文本解析）。
> 本次**应当扩展它**（加解码侧 + 加 FFmpeg 侧），而不是新建第二个探测模块。

## 3.4 现有探测与执行的耦合

**CONFIRMED**：自动选择与执行**用同一个探针**，这是有意的设计：

* `1kt.py:1408-1412`（`_hw_encoders_available` docstring）："A backend counts as
  available only when its rigaya tool exists *and* `--check-features` probes
  successfully and reports an HEVC section — **the same probe the run itself uses**,
  so auto-selection and execution cannot disagree."
* **代价（CONFIRMED）**：`--experimental-multihw` 路径下 `--check-features` 会被
  重复执行（`1kt.py:1779` 一次 + `1923/1924` 各一次）；`1kt.py:1739-1741` 的注释明确
  承认这一点并做了规避（multihw 时跳过默认自动选择以省一次探测）。
* **⚠️ INFERRED 的隐患**：`multihw` 用一个探测结果（`_hw_encoders_available`）
  决定可用后端，但 `hw_backend_for` 内部**再探测一次**（`caps.probe_backend`）。
  两次探测之间工具/驱动不会变化，所以今天不构成缺陷；但引入缓存（G2 修复）后，
  必须保证**探测结果与执行命令来自同一次探测快照**，否则会重新引入
  "自动选中的 ≠ 跑得起来的"。

---

# 4. 当前 FFmpeg/x265 路径

## 4.1 命令构建（CONFIRMED）

`encoders/x265.py:157 build_command()` — 完整交付路径：

```python
cmd = [
    ffmpeg, "-hide_banner", "-nostdin", "-stats", "-y",
    "-i", src,
    "-map", "0",                    # 全部流
    "-map_metadata", "0",
    "-map_chapters", "0",
    "-c:v:0", "libx265",
    "-preset", profile["preset"],
    "-crf", profile["crf"],
    "-pix_fmt", ffmpeg_pix_fmt(src_info),
    # 第 2..N 条视频流（DJI 附加图/封面）一律 copy
    "-c:a", "copy", "-c:s", "copy", "-c:d", "copy", "-c:t", "copy",
    "-fps_mode", "passthrough",
    "-x265-params", x265_params,
    "-movflags", "+use_metadata_tags",
    part_dst,
]
```

`encoders/x265.py:210 build_video_command()` — Sony 保留管线的**视频专用**中间体：
`-map 0:v:0`、`-an -sn -dn`、`-tag:v hvc1`、输出 `encoded.mov`。

## 4.2 像素格式映射（CONFIRMED）

`encoders/base.py:25 ffmpeg_pix_fmt()`：

```python
_CHROMA_BASE = {"4:2:0": "yuv420p", "4:2:2": "yuv422p",
                "4:4:4": "yuv444p", "mono": "gray"}

def ffmpeg_pix_fmt(src):
    if src.bit_depth <= 0 or src.chroma not in _CHROMA_BASE:
        return "yuv420p10le"                 # 保守兜底
    base = _CHROMA_BASE[src.chroma]
    return f"{base}{src.bit_depth}le" if src.bit_depth > 8 else base
```

**实测验证（CONFIRMED）**：Sony XAVC S H.264 4:2:2 10-bit 源（`C9037.MP4`）的
`src_info.chroma` 为 `"4:2:2"`、`bit_depth` 为 `10` → `yuv422p10le`，**这是正确的**
（`ffprobe` 报 `yuv422p10le`）。libx265 支持该格式，实测编码成功（§5.3）。

> **⚠️ 一个必须记录的边界（CONFIRMED）**：`_CHROMA_BASE` 支持 `4:4:4`，但
> **x265 的 4:4:4 与 4:2:2 需要 rext 编译支持**（`docs/design/hevc_422_rext_compatibility.md`）。
> 本机 `libx265` 实测**接受** 4:2:2 10-bit，但规划书不据此宣称 4:4:4 可用 —— 见 §19 未决项。

## 4.3 关键事实：**FFmpeg 路径今天完全没有硬件解码，而且没有任何解码器决策**

| 事实 | 证据 | 标记 |
|---|---|---|
| `build_command` / `build_video_command` 的 argv 中**不含任何** `-hwaccel`、`-c:v <decoder>`、`-init_hw_device`、`-vf hwdownload` | `encoders/x265.py:168-205`、`:231-250`（已读全函数） | CONFIRMED |
| `route_decode()` 的调用者只有 `core/batch_hw.py:466`，即**只在 rigaya 硬件编码路径内** | 全仓 grep `route_decode` → `encoders/hwdecode.py:135`（定义）、`core/batch_hw.py:55-58`（import）、`core/batch_hw.py:466`（唯一调用）、`tests/hwdecode/harness.py` | CONFIRMED |
| 因此 `--encoder x265` 时，**`--hw-decode` 是一个完全无效的参数**（被解析、被记入日志、被传入 `BatchCtx`，但软件路径 `1kt.py:2040-2175` 从不读它） | `1kt.py:2196 hw_decode=args.hw_decode` 位于 `is_hardware` 分支内（`1kt.py:2177` 起） | CONFIRMED |
| 软件路径的 `--jobs` **同样无效** | `1kt.py:2043 for src in sources:` 串行循环，注释 `2041-2042`："x265 path (sequential; ...)" | CONFIRMED |

> **这直接回答了 prompt 的核心质询**："不应把硬件解码能力限制在 NVEncC/QSVEncC
> 两条编码路径中"——**今天恰恰就是这样限制的，而且是硬限制**：
> 解码器选择机制在物理上位于 `core/batch_hw.py` 内部，FFmpeg 路径够不着。

## 4.4 `--encoder x265` 的自动选择语义（CONFIRMED）

`1kt.py:1799-1807`：一旦 `encoder_name == "x265"`，构造
`SourceClassifier` + `ScalingEngine` + `X265Backend()`，
**没有任何解码器相关构造**。

`1kt.py:23`（模块 docstring）：`x265 legacy path (manual --encoder x265 only; never auto-selected)`
—— **INFERRED 但它与代码不完全一致**：`resolve_default_backend()`
（`1kt.py:1445-1466`）在**没有硬件后端可用时会自动返回 `"x265"`**（`1kt.py:1462-1466`）。
所以"永不自动选中"只在"存在可用硬件后端"时成立。这是一处**文档与代码的措辞偏差**，
规划书按代码为准（自动选择可以落到 x265，见 §10）。

---

# 5. FFmpeg 硬件解码 + libx265 可行性实测（本 session 的核心新证据）

> 本节全部为**本 session 在本机实测**。脚本在 gitignored 的 `work/plan_probe/`，
> 未修改任何仓库文件。命令与原始输出在 §5.7 复现。

## 5.1 工具链身份（CONFIRMED）

| 项 | 值 |
|---|---|
| `tools/ffmpeg.exe` | `ffmpeg version 9.0.1-full_build-www.gyan.dev`，gcc 16.1.0 (MSYS2) |
| 关键配置项 | `--enable-cuda-llvm --enable-cuvid --enable-ffnvcodec --enable-nvdec --enable-nvenc --enable-libvpl --enable-d3d11va --enable-dxva2 --enable-libx265` |
| **发布包是否包含它** | ✅ **包含**。`release/build_release.py:71-76`：`TOOL_FILES = ("tools/ffmpeg.exe", "tools/ffprobe.exe")`；`TOOL_DIRS` 额外含 `tools/NVEncC_9.31_x64`、`tools/QSVEncC_8.26_x64`、`tools/GPAC`。**`tools/avhw/` 不在白名单内** |
| `-hwaccels` | `cuda vaapi dxva2 qsv d3d11va opencl vulkan d3d12va amf` |
| 硬解器（已注册） | `hevc_cuvid`、`h264_cuvid`、`av1_cuvid`、`hevc_qsv`、`h264_qsv`、`av1_qsv`（另有 amf/vulkan/d3d12va 家族） |
| 硬编码器（对照） | `hevc_nvenc`、`av1_nvenc`、`hevc_qsv`、`av1_qsv`、`libx265`、`libsvtav1` |
| 设备可初始化 | ✅ `-init_hw_device cuda=cu:0` 与 `-init_hw_device qsv=qsv:0` 均成功；`hwupload_cuda` / `hwdownload` / `scale_cuda` / `scale_qsv` / `vpp_qsv` 过滤器均可用 |

> **★ 这是本次重构最重要的基础设施事实（CONFIRMED）**：
> **FFmpeg 的 NVDEC/QSV 硬解能力在发布安装中真实可用**，
> 而 rigaya 硬解在发布安装中**必然不可用**（patched binary 被排除，
> `docs/hardware-decode/README.md` §"边界声明"、`final-report.md` §9）。
> 两者不是同一个能力，其可用性边界**完全相反**。

## 5.2 硬件 frame 传输：必须知道的格式约束（CONFIRMED，本 session 实测）

**`hwdownload` 只接受硬件 surface 自身的系统内存格式。** 在 10-bit 4:2:0 HEVC 上实测：

| `hwdownload,format=` | cuda (NVDEC) | qsv |
|---|---|---|
| `p010le` | ✅ **OK** | ✅ **OK** |
| `yuv420p10le` | ❌ `Invalid output format yuv420p10le for hwframe` | ❌ 同 |
| `nv12` | ❌ | ❌ |
| `yuv420p` | ❌ | ❌ |
| `p210le` / `yuv422p10le` / `nv16` / `p410le` | ❌ | ❌ |

**结论（CONFIRMED）→ 这决定了解码→x265 的**必然形状**：

```text
硬解输出 surface（p010le 类）
    ↓  hwdownload,format=p010le          ← 强制，无法绕过
CPU 系统内存帧（p010le）
    ↓  format=<x265 需要的格式>          ← 一次 CPU 侧 swscale 转换
libx265
```

**`libx265` 不接受 `p010le`**，只接受 planar（`yuv420p10le` / `yuv422p10le` …）。
因此**软解路径比硬解路径少一次格式转换**：

* 软解：`hevc(AVFrame yuv420p10le) → libx265`（0 次额外转换）
* 硬解：`hevc_qsv(surface) → hwdownload(p010le) → format=yuv420p10le → libx265`（1 次额外转换）

> **★ 这条推翻了"硬解必然更快"的直觉，并被下面的实测证实。**
> prompt §5.2 要求不得预设性能收益——**现在有数字了**。

## 5.3 保真度实测：硬解与软解**逐帧位相同**（CONFIRMED）

### A. Sony XAVC HS（HEVC Main 10 4:2:0 10-bit，3840×2160，59.94p，30 帧）

源：`testsets/20260903/A7M5/20260903_C1170.MP4`

| 解码路径 | 解码帧数 | 原始 `p010le` 流 MD5 | 与软解相同？ |
|---|---|---|---|
| 软件（`hevc`） | 30 | `10a223491275ee31e6fb885cf626485d` | — 基准 |
| `-hwaccel cuda` + `hevc_cuvid` | 30 | `10a223491275ee31e6fb885cf626485d` | ✅ **完全相同** |
| `-hwaccel qsv` + `hevc_qsv` | 30 | `10a223491275ee31e6fb885cf626485d` | ✅ **完全相同** |

（软解 `format=p010le` 转出的对照 MD5 = `1dee19babbb6fb4879ffb79c4491f827`，
与三条路径的 `hwdownload` 链不同，**因为转换矩阵不同**；三条 `hwdownload` 路径
彼此**逐字节相同**，这才是要比较的对象。）

### B. Sony XAVC S（H.264 High 4:2:2 10-bit，3840×2160，29.97p，195 帧）

源：`testsets/a7m4_4k30p_264_hi422p_xavcs/C9037.MP4`

| 解码路径 | 结果 | 原始 `p210le` MD5 | 帧数 |
|---|---|---|---|
| 软件（`h264`） | rc=0 | `ad5308eab75a964f5e39464aefd7d5ca` | 195 |
| `-hwaccel cuda` + `h264_cuvid` | rc=0 | `ad5308eab75a964f5e39464aefd7d5ca` | 195 |
| `-hwaccel qsv` + `h264_qsv` | **rc=69 失败** | — | 0 |

### C. 端到端「解码 → libx265」的产品级验证（4:2:2 10-bit，全 195 帧）

| 路径 | rc | wall | 产物字节 | 产物视频基本流 SHA-256 |
|---|---|---|---|---|
| 软解 → libx265（`-pix_fmt yuv422p10le`） | 0 | **19.56 s** | 5 926 999 | `f0159014e508e830dfaa24fab951881b7890afa6852d9d00715e39f420a52be3` |
| NVDEC → libx265（`hwdownload,format=p210le,format=yuv422p10le`） | 0 | **23.22 s** | 5 926 999 | `f0159014e508e830dfaa24fab951881b7890afa6852d9d00715e39f420a52be3` |

> **硬解产出的文件与软解逐字节相同的视频基本流，但慢了 18.7 %。**

## 5.4 拒绝矩阵：哪些格式硬解器**不接受**（CONFIRMED）

| 源 | codec / pix_fmt | NVDEC (`*_cuvid`) | QSV (`*_qsv`) |
|---|---|---|---|
| Sony XAVC HS（146+ 文件族） | HEVC Main10 `yuv420p10le` | ✅ accept | ✅ accept |
| Sony XAVC S A7M4 | H.264 High 4:2:2 `yuv422p10le` | ✅ accept | ❌ **rc=69** |
| Sony XAVC S A7M5 | H.264 High 4:2:2 `yuv422p10le` | ✅ accept | ❌ **rc=69** |
| DJI（Air3S / Action 4） | HEVC Main10 `yuv420p10le` | ✅ accept | ✅ accept |

**QSV 拒绝的精确文本（CONFIRMED，用于错误分类器）**：

```text
[h264_qsv @ ...] Error querying IO surface: unsupported (-3)
[vist#0:0/h264 @ ...] [dec:h264_qsv @ ...] Error submitting packet to decoder: Function not implemented
```

> **★ 这与 rigaya 侧的实测**完全同源**（CONFIRMED）**：
> `docs/hardware-decode/xavc-hs-frame-count.md` §6 记录 QSVEncC
> `--avhw` 在同样 5 个 H.264 4:2:2 文件上是
> `Error querying IO surface: unsupported (-3)`, `rc=69`, 0 帧。
> FFmpeg `h264_qsv` 与 QSVEncC `avqsv` 都构建在 Intel oneVPL/MSDK 之上，
> **这是同一颗硅的限制**，不是 FFmpeg 的缺陷。规划书据此把 QSV 的
> H.264 4:2:2 视为**结构性不可用**（`REFUSED` 级别），而不是"待测"。

## 5.5 性能实测：硬解 + x265 **没有吞吐收益**（CONFIRMED）

| 源 | 帧数 | 软解 wall | NVDEC wall | QSV wall | delta（NVDEC vs 软解） |
|---|---|---|---|---|---|
| Sony XAVC HS HEVC 4:2:0 10-bit | 30 | 4.67 s | **2.55 s** | 8.34 s | −45 %（⚠️ 30 帧样本，见下） |
| DJI HEVC 4:2:0 10-bit | 16 | 2.14 s | 2.33 s | 3.89 s | +9 % |
| DJI HEVC 4:2:0 4:3 | 105 | 14.61 s | 14.86 s | 14.55 s | +2 % |
| **Sony XAVC S H.264 4:2:2 10-bit** | **195** | **19.56 s** | **23.22 s** | n/a（拒绝） | **+18.7 %** |

> **⚠️ 30 帧与 16 帧的行不可用**：进程启动/设备初始化/文件系统缓存噪声在这个量级上
> 占主导（同一台机器上 30 帧软解 4.67 s vs 2.17 s 的纯解码波动就有 2 倍）。
> **只有 105 帧与 195 帧两行有参考价值**，结论是 **±2 % 到 +19 %，即"没有收益，可能更慢"**。
>
> 这与归档的既有结论**方向一致**（`docs/hardware-decode/xavc-hs-frame-count.md` §10 第 19 条，
> 引用 `research-conclusion.md` §3.2）：
> **"实测 2.5–2.7× 更少 CPU/帧；单任务吞吐**不更快**
> （同 binary 配对 `--avsw` 140.9 s vs patched `--avhw` 158.4 s，约慢 11 %）"**。
> 本 session 在**完全不同**的技术栈（FFmpeg + libx265，而非 rigaya --avhw + NVENC）
> 上测到**同一量级同一方向**的结论。这是两个独立的观察支持同一个判断。

## 5.6 由此得到的可行性结论

| 命题 | 判定 | 依据 |
|---|---|---|
| FFmpeg 硬解 + libx265 **技术上可行** | ✅ **CONFIRMED** | §5.3：rc=0、帧数精确、位相同 |
| 硬解在**发布安装中可用**（不需要 patched binary） | ✅ **CONFIRMED** | §5.1：`tools/ffmpeg.exe` 在白名单内 |
| 硬解不存在 rigaya 的 leading-picture 丢帧缺陷 | ✅ **CONFIRMED** | §5.3：MD5 逐字节相同；与 `xavc-hs-frame-count.md` §6 的归档测量一致 |
| 10-bit 4:2:0 / 10-bit 4:2:2 均可不降位深、不降色度通过 | ✅ **CONFIRMED** | §5.2、§5.3 |
| 硬解 + x265 **更快** | ❌ **被证伪（在实测的 4 个源上）** | §5.5：±2 % ~ +19 % |
| 硬解降低 CPU 占用 | **INFERRED**（本 session 未测 CPU time） | 归档测到 2.5–2.7× 更少 CPU/帧（rigaya）；FFmpeg 路径未测 |
| QSV 对 H.264 4:2:2 不可用 | ✅ **CONFIRMED** | §5.4 |
| AV1 / 8-bit / 1080p / VFR / HEVC 4:2:2 的硬解行为 | **UNKNOWN** | 本机语料中没有这些形态；归档同样标记为未验证 |

> **因此"减少实际处理时间"这个目标不能靠硬解本身达成。**
> 真正的收益来源必须另行寻找，候选（全部为 PROPOSED，需实测）：
> ① 多任务并发（§14）——今天的 `--jobs` 对软件路径**完全无效**，这是最大的未开发空间；
> ② 把 `--jobs` 扩展到软件路径，让 x265 的多核并行与多文件并行叠加；
> ③ 硬解释放出的 CPU 周期在**并发**场景下才可能转化为吞吐
>    （单任务不更快，但多任务时 CPU 不再是瓶颈）。
> **OPEN DECISION**：是否接受"硬解在单任务上可能更慢"这个代价换取
> 并发场景下的吞吐与更低的 CPU 占用。

## 5.7 复现方法

```powershell
cd F:\1KeyTranscoder

# A. 保真度：三路解码的原始流 MD5 应完全相同
$ff = "tools\ffmpeg.exe"; $src = "testsets\20260903\A7M5\20260903_C1170.MP4"
& $ff -v error -i $src -vf format=p010le -fps_mode passthrough -f rawvideo -pix_fmt p010le -f md5 -    # 软解对照
& $ff -v error -init_hw_device cuda=cu:0 -hwaccel cuda -hwaccel_device cu -hwaccel_output_format cuda `
      -i $src -vf hwdownload,format=p010le -fps_mode passthrough -f rawvideo -pix_fmt p010le -f md5 -
& $ff -v error -init_hw_device qsv=qsv:0 -hwaccel qsv -hwaccel_device qsv -hwaccel_output_format qsv `
      -i $src -vf hwdownload,format=p010le -fps_mode passthrough -f rawvideo -pix_fmt p010le -f md5 -

# B. 端到端（§5.3-C）
python work\plan_probe\compare.py "testsets\a7m4_4k30p_264_hi422p_xavcs\C9037.MP4::x422"

# C. hwdownload 格式约束（§5.2）
foreach ($f in 'p010le','yuv420p10le','nv12','yuv420p') {
  & $ff -v error -init_hw_device cuda=cu:0 -hwaccel cuda -hwaccel_output_format cuda -i $src `
        -vf "hwdownload,format=$f" -frames:v 1 -f rawvideo -y NUL
  "cuda $f -> exit $LASTEXITCODE"     # 只有 p010le 是 0
}
```

> **下一 session 必须重新运行 A/B/C 并比对数字**。本机是笔记本（有既定热漂移，
> `final-report.md` §J.1 明确警告过跨 session 绝对值不可比），
> **性能数字必须重新测，正确性数字（MD5、帧数）可以要求完全一致**。

---

# 6. 当前 NVENC / QSV 路径

## 6.1 命令构建（CONFIRMED）

`encoders/nvencc.py:160` 与 `encoders/qsvencc.py:147` 的 `command()` **结构完全相同**，
只有 `PARAM_MAP` 不同：

```python
args, skipped, notes = self.build_args(profile, chroma, depth, vfr, color, reader=reader)
cmd = [str(self.tool), "-i", str(source), *args]
if audio_copy:
    cmd += ["--audio-copy"]
cmd += ["-f", container, "-o", str(output)]
```

`build_args` 的两个关键点（`encoders/nvencc.py:135-143`、`encoders/qsvencc.py:120-128`）：

```python
args = [
    f"--{reader}",            # ← "avsw" 或 "avhw"，**永远显式传入**
    "--video-track", "1",
    "-c", self.codec,          # "hevc" | "av1"
    "--output-depth", str(depth),
    *args,
]
```

**`--reader` 永远显式**（`encoders/nvencc.py:128-133` docstring）：
> "It is always passed explicitly: 'let the tool choose' is not an option, because
> NVEncC's default reader and its silent fallbacks are exactly what the integrity gate
> has to be able to distinguish."

## 6.2 **没有设备选择参数**（CONFIRMED）

| 事实 | 证据 |
|---|---|
| `NvencBackend.command()` / `QsvBackend.command()` **不发出任何** `--device` / `--gpu` / `--adapter` | 两个 `command()` 与 `build_args()` 已通读；`PARAM_MAP`（`nvencc.py:48-84`、`qsvencc.py:41-89`）中无设备键 |
| 全仓 grep `--device` 的生产代码命中：**0** | 唯一命中在 `tests/hwdecode/harness.py:2542`（测试）与 `encoders/hwdecode.py:293-294`（匹配**错误文本** `"invalid device id"`） |
| `BackendCaps.device` 是**单数**且只用于显示 | `encoders/caps.py:36-43`：`device: str = ""`、`driver: str = ""`；`_env_info()`（`:128-147`）的正则 `r"#\d+:\s*([^\r\n]+?)\s*\[([^\]]*)\]\s*$"` 抓 `#0:` 行并**丢弃索引** |
| 唯一的多 GPU 枚举在 WMI 里，且**只写报告** | `core/versions.py:100-121 _gpu_drivers()`：`Get-CimInstance Win32_VideoController` → `[{gpu, driver_version, driver_date}]`；消费点只有 `1kt.py:1954-1962` 的日志与 `env_versions.json` |

> **★ 这是 §13 多 GPU 横向扩展的硬阻塞（CONFIRMED）**：
> TODAY 无法把某个 job 指定给某张 GPU —— rigaya 侧**没有设备选择通路**。
> `docs/hardware-decode/final-report.md` §10 第 6 条同样记录"多 GPU 设备选择未验证"。
> 归档 `xavc-hs-frame-count.md` §3.3 记录了 `--device 1` 直接失败
> （`Invalid Device Id = 1`，研究时是**单 GPU 主机**）。

## 6.3 完整性闸门（CONFIRMED，`encoders/integrity.py`）

`verify_encode()`（`encoders/integrity.py:285`）在 `reader == "avhw"` 时才被调用
（`core/batch_hw.py:527`）。检查项按顺序：

| # | 检查 | 代码位置 | 失败 reason |
|---|---|---|---|
| 1 | `rc == 0` 且产物存在非空 | `integrity.py` 内 | `decode_failed` 类 |
| 2 | **reader 身份断言**：工具日志 `Input Info <reader>:` 必须等于请求值 | `integrity.py:238` `reader_identity_from_log`、断言在 `:321-329` | `reader_unavailable` |
| 3 | **五方帧数对账**，以 `container_video_samples(source)` 为真值 | `integrity.py:111`（自解 `stsz`）、`:343-378` | `count_mismatch` |
| 4 | reader 自报帧数**只记录不判定** | `integrity.py:256` | — |

reader 身份期望值表（`core/batch_hw.py:186-192`）：

```python
READER_IDENTITY = {
    "nvencc":  {"avhw": "avcuvid", "avsw": "avsw"},
    "qsvencc": {"avhw": "avqsv",   "avsw": "avsw"},
}
```

失败动作（`core/batch_hw.py:543-576`）：`safe_unlink(output)` + WARNING +
`memoize_unavailable()` → `reader = "avsw"` → **`continue`，重跑同一 rung**。
回退上限 `MAX_HW_DECODE_FALLBACKS = 1`（`core/batch_hw.py:197`），
超出 → `RuntimeError`（`batch_hw.py:561-566`）。

> **INFERRED（高置信）**：该上限在实践中**不可达**。因为每次回退都把
> `reader` 置为 `"avsw"`（`batch_hw.py:567`），而整个闸门块被
> `if reader == "avhw":`（`batch_hw.py:527`）守护，所以"第二次失败"不可能发生。
> 它是防御性的，不是活跃逻辑。规划书不改它。

## 6.4 `verify_sequence` 是死代码（CONFIRMED）

| 事实 | 证据 |
|---|---|
| `--hw-decode-verify` 被解析（`1kt.py:1334-1342`），**help 文本承诺** "在硬件解码之上追加 ordered fingerprint 校验：同时跑软件解码，逐帧比较画面序列" | `1kt.py:1337-1341` |
| **`args.hw_decode_verify` 在全仓生产代码中被读取的次数：0** | grep `hw_decode_verify` → `1kt.py:1335`（定义）、`tests/selftest/suites/hardware_audio.py:323`（CLI 白名单字符串）、`encoders/integrity.py:15`/`:411`（docstring 提及）。**无任何 `args.hw_decode_verify` 读取** |
| `verify_sequence()` 定义于 `encoders/integrity.py:396`，**唯一调用者在 `tests/hwdecode/harness.py`** | grep `verify_sequence` |

> **这是一个"文档承诺了但从未实现"的功能（CONFIRMED）**。
> `docs/hardware-decode/final-report.md` §11 第 6 条把它列为 P2 未决项
> （"`--hw-decode-verify`（sequence gate）作为可选生产开关的成本评估"），
> 但 §6 C 类把它记为 PASS（`HD-C12`）——**那是通过 harness 直接调用
> `verify_sequence()`，不是通过 CLI**。规划书据此把"移除 `--hw-decode-verify`"
> 定义为**移除一个从未生效的 CLI 表面**，而不是移除一项已交付能力（§15）。

## 6.5 硬件经典路径的两个额外缺陷（CONFIRMED，本 session 发现）

### 缺陷 H-1：经典硬件路径的 PSNR/SSIM 交付闸门是**不可达死代码**

`core/batch_hw.py:1255-1284` 原文：

```python
1255:    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
1256:        logger.error("[FAIL] encoder produced no output | %s", src)
1257:        file_logger.error("FAILED | encoder produced no output")
1258:        safe_unlink(part_dst)
1259:        return "failed"                     # ← 无条件返回
1260:
1261:    # PSNR/SSIM quality sample (check=full) gates delivery: a corrupted
1262:    # encode must never land in the output tree.    if check_level == "full" and ffmpeg is not None:
1263:        from preservation.quality import run_quality_sample      # ← 缩进 8，等于 1255 的 if 体
...
1284:            return "failed"
```

第 1262 行把 `if check_level == "full" and ffmpeg is not None:` **吞进了注释内部**
（注释末行与 `if` 之间没有换行）。AST 验证（本 session 执行）：

```text
If at line 1255  .. end 1284   test=BoolOp(op=Or(), values=[UnaryOp(op=Not(), ...)])
ENCLOSES 1265 -> If at 1255..1284
```

→ 1263-1284 **整体成为 `if not part_dst.is_file()...` 的语句体，位于其无条件
`return "failed"`（1259）之后**，因此**永不执行**。`ast.parse` 不报错，
语法检查抓不到。

**影响（CONFIRMED）**：经典硬件路径（非 Sony/非 DJI）在 `--check full` 下
**不会**运行 `run_quality_sample()`。而该路径的
`do_frame_check=False`（`core/batch_hw.py:1241`），
所以**经典硬件路径的全部输出验证 = "rc==0 且文件非空"**。

> **规划含义**：这不属于本次 prompt 的授权范围（prompt 明确"不得修改编码器参数"、
> 且测试计划只覆盖指定矩阵），但它是一个**必须记录的既有缺陷**，
> 并且**直接影响本次重构的可测性**：任何新的"输出验证"设计
> 都必须绕过这段死代码，而不是复用它。见 §18-C（建议：作为独立缺陷单独立项）。

### 缺陷 H-2：`classify_reader_failure()` 的分类结果被丢弃

`core/batch_hw.py:544`：

```python
_cls, _detail = classify_reader_failure(log_text)
```

`_cls` / `_detail` **在此后从未被读取**（全仓 grep 确认）。因此
`encoders/hwdecode.py:254` 精心实现的三类区分
（`startup_failed` / `capability_refused` / `decode_failed`）
**没有进入任何决策**：一个 DLL 加载失败（`0xC0000135`）在梯子上
只会走 `classify_failure("")` → `"format"` → 逐级降格 → 最终
`"[FATAL] all format rungs failed"`。

> prompt §4.3 明确要求区分
> "工具不可用 / 不支持输入 / 探测失败 / 初始化失败 / 编码出错"。
> **今天这个区分在 rigaya 梯子上是存在的但未被使用**。规划书把它列为
> §10 的必要修复项，而不是新设计。

---

# 7. 历史 Sony 解码问题及其证据

> 本节是**引用**，不是新发现。所有数字来自归档文档，**必须连边界声明一起引用**
> （`docs/hardware-decode/xavc-hs-frame-count.md` §8.3）。

## 7.1 缺陷本体（CONFIRMED，归档实测）

**rigaya 两个硬件编码前端的 `--avhw` 读者**（NVEncC `avcuvid`、QSVEncC `avqsv`）
把"第一个喂给解码器的 packet 的 PTS"当成"应当呈现的第一帧的 PTS"，
于是丢掉 presentation order 上排在第一个 keyframe 之前的 **leading pictures**。

**预测式（从容器 box 直接算出，无需解码）**：

```text
DTS[i] = cumsum(stts)[i]
PTS[i] = DTS[i] + ctts[i]
pictures_before_first_keyframe = |{ i : PTS[i] < PTS[first sync sample] }|
avhw_frames = container_samples − pictures_before_first_keyframe
```

验证广度（CONFIRMED）：NVEncC 151/151 文件预测正确；QSVEncC 146/146；
**共 297/297 个成功运行全部命中，0 个预测错误**。

| 素材 | leading | NVEncC `--avhw` | QSVEncC `--avhw` |
|---|---|---|---|
| Sony XAVC HS（HEVC 4:2:0 10-bit，146 文件） | 3 | 丢 3（146/146） | 丢 3（146/146） |
| Sony XAVC S（H.264 4:2:2 10-bit，5 文件） | 2 | 丢 2（5/5） | **capability refusal**（`rc=-31`，无 reader，无输出） |
| DJI / x265 / synthetic / Sony-in-MKV（负对照） | 0 | 丢 0 | 丢 0 |

**为什么危险（CONFIRMED）**：产物是 `rc = 0`、无任何错误文本、完全可播放的 MP4。
归档原文（`docs/hardware-decode/README.md` §"设计上必须知道的三件事"）：
> **`exit code = 0` 不是正确性证据。** 已证：stock `--avhw` 在 Sony 上丢 3 帧、
> `rc = 0`、无任何报错、文件完全可播放。

## 7.2 **关键区分：FFmpeg 的硬解不受此缺陷影响**（CONFIRMED，归档测量 + 本 session 复现）

归档 `docs/hardware-decode/xavc-hs-frame-count.md` §6 的原始测量：

| 路径 | 结果 |
|---|---|
| `qsv_native`（`-hwaccel qsv -hwaccel_output_format qsv -c:v hevc_qsv` + `hwdownload,format=p010le`） | **146/146 PASS**，`sequence_identical = true`；5 个失败全是 H.264 4:2:2 的 capability 失败（`unsupported (-3)`, `rc=69`, 0 帧，**响亮**） |
| `nvdec_cuvid`（`-hwaccel cuda -c:v hevc_cuvid`） | **151/151 PASS**，零 FRAME_DROP / DUPLICATE / REORDER / PTS_ERROR / FLUSH_LOSS，**含 QSV 没有的 H.264 4:2:2 10-bit（5/5）** |
| 对照：rigaya `--avhw` | 151/151 **全部丢帧** |

机制差异（源码级，归档 `rigaya-avhw-analysis.md` §"Why FFmpeg succeeds"）：

| 方面 | FFmpeg `-hwaccel` | rigaya `--avhw` |
|---|---|---|
| 首帧处理 | **无** —— 返回的每一帧都转发 | `getOutputFrame()` 丢弃所有低于第一个 packet PTS 的帧 |
| leading pictures | 解码并**发射** | 解码后被**下游丢弃** |

> **本 session 独立复现（§5.3）**：三条路径的原始流 MD5 **完全相同**。
> 归档的"bit-identical"与本 session 的"MD5 相同"是两个独立测量，指向同一结论。

## 7.3 为什么 patched binary 无法进发布包（CONFIRMED）

| 事实 | 证据 |
|---|---|
| patched binary 存在于本机 `tools/avhw/NVEncC_9.31_avhw/`、`tools/avhw/QSVEncC_8.26_avhw/` | 本 session `Test-Path tools/avhw` → `True`；`encoders/hwdecode.py:336-347 PATCHED_BUILD` |
| 它们是 **research build**（avs/vpy reader 关闭、CUDA MSBuild shim、QSVEncC 的 OneVPL 单独构建） | `final-report.md` §9 |
| `release/build_release.py` 的 `TOOL_DIRS` **不含** `tools/avhw/` | `release/build_release.py:72-76`（已读） |
| `core/config.py:272-275` **确定性排除** `tools/avhw/`，防 glob 排序误选 | `if "avhw" not in {part.lower() for part in p.parts}` |
| 补丁绑定版本：NVEncC 仅 9.31（`2cb9d810`）；QSVEncC **仅 pinned 8.26**（8.27–8.30 未检验） | `final-report.md` §9、`toolchain-provenance.json` `claim_boundary` |
| 因此发布安装中 `--hw-decode auto` → `not_proven` 降级软解；`require` → 明确失败 | `docs/hardware-decode/README.md` §"边界声明"、`architecture.md` §8.5 |

**这是"设计行为，不是缺陷"**（归档原文），但它意味着：
**今天唯一可用的硬解通路在发布安装中=0 条。**

## 7.4 归档的未决项（与本规划直接相关）

`docs/hardware-decode/final-report.md` §11：

| # | 项 | 级别 |
|---|---|---|
| 4 | **多 GPU / `--device` 选择验证** | P2 |
| 5 | 扩展 allowlist：8-bit / 1080p / VFR / HEVC 4:2:2 素材的硬解验证（**需要素材**） | P2 |
| 6 | `--hw-decode-verify`（sequence gate）作为可选生产开关的成本评估 | P2 |
| 7 | 4-way 4K 并发上限与其机制（VRAM vs NVENC session 争用，**仍不可分**） | P2 |
| **9** | **是否把 hardware decode 设为默认** —— "**需要矩阵全绿 + production benchmark 之后再决定**" | **决策** |

> **★ 本节最重要的一句（CONFIRMED）**：归档把
> **"是否把 hardware decode 设为默认"明确列为尚未做出的产品决策**，
> 并给出了前置条件（矩阵全绿 + production benchmark）。
> 本次 prompt 要求改变默认解码策略（§9），**因此本规划书必须在 §19
> 显式记录这次策略变更与上述前置条件的关系**，并给出它所需的证据。
> 规划书不替维护者宣布这个决策已经做出。

---

# 8. 三个独立概念：Policy / Decoder / Encoder

## 8.1 为什么必须拆开（CONFIRMED 的现状反例）

prompt 要求不得把三者混成一个 `hardware mode` 布尔值。**今天就存在混用的实证**：

| 混用 | 证据 |
|---|---|
| `--encoder x265` 事实上**关掉**了硬解 | `route_decode()` 只在 `core/batch_hw.py` 内被调用（§4.3），软件路径够不着 |
| `--hw-decode off` 事实上**只有硬件编码路径**才读 | `1kt.py:2196` 在 `is_hardware` 分支内 |
| 后端选择同时决定**编码器**与**是否存在解码器选择** | `is_hardware` 一个布尔同时控制两件事（`1kt.py:1762`、`1906`、`2040`、`2177`） |
| `--experimental-multihw` 同时隐含"探测""后端集合""调度""is_hardware=True" | `1kt.py:1770-1794` |
| `--no-metadata`（要求的新参数）会**同时**改变 metadata policy、decoder selection、encoder scheduling | §11 —— 这正是 prompt 警告的耦合 |

## 8.2 PROPOSED 的三层模型

```text
                        ┌─────────────────────────────┐
   ffprobe 源探测  ───► │  Source classification      │  已有：core/probe.py
   (codec/chroma/       │  · camera family            │  + is_sony_source / is_dji_source
    depth/res/fps/      │    (sony / dji / generic)   │  + core/source_classifier.py（效率）
    streams/colour)     │  · format triple            │
                        │    (codec, chroma, depth)   │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │  1. METADATA POLICY         │  ← 新模型（§11）
                        │   preserve | no_metadata    │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │  2. DECODER SELECTION       │  ← 新模型（§9）
                        │   substrate: sw | ffmpeg_hw | rigaya_hw
                        │   device / pixel-format /   │
                        │   transfer chain            │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │  3. PROCESSING PIPELINE     │  已有
                        │   scaling / colour / channel│
                        │   -sync / preservation      │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │  4. ENCODER SELECTION       │  ← 新模型（§10）
                        │   target codec (hevc|av1)   │
                        │   backend (nvenc|qsv|x265|  │
                        │            svtav1)          │
                        │   + explicit pin wins       │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │  5. OUTPUT VALIDATION       │  ← 新模型（§9.4）
                        │   validity | count | seq    │
                        └─────────────────────────────┘
```

**三条硬规则（PROPOSED）**：

| 规则 | 内容 |
|---|---|
| **R1 单向依赖** | 上层**不能**反向决定下层。`--encoder x265` 属于第 4 层，**不得**影响第 2 层。 |
| **R2 独立记录** | 三层各自的决策必须**分别**写进 per-file log 与 report：<br>`META_POLICY | ...`、`DECODE_ROUTE | ...`、`ENCODE_BACKEND | ...`。今天只有 `DECODE_ROUTE`（`core/batch_hw.py:475`）。 |
| **R3 冲突要报错，不要静默取胜** | 若用户显式指定与某层策略冲突（如 multihw + `--encoder`），**在任何正式编码开始前退出**（§12.2）。 |

## 8.3 解码器与编码器必须独立决策 —— 具体到本仓库

| 反例（prompt 明令禁止） | 今天的状态 | 目标 |
|---|---|---|
| `NVDEC decode` 强制输出必须由 NVENC 编码 | **今天恰恰如此**：`reader="avhw"` 只能出现在 rigaya 后端里（`encoders/nvencc.py:139`、`qsvencc.py:124`） | 解耦：解码substrate 与编码 backend 独立选择 |
| `--encoder x265` 自动关闭硬解 | **今天恰恰如此**（§4.3） | `--encoder x265` 只表示用 x265 编码 |
| 硬件可用就自动把 Sony 改成硬解 | 今天不会（默认 `off`） | 保持不会（§9.2） |

---

# 9. 新的默认解码策略

> ## ⚠️ SUPERSEDED —— 本节 §§9.2 与 §9.2.1 已不再代表当前策略
>
> 维护者在实施阶段明确指示：**所有视频解码路径都必须优先使用硬件解码**，
> 适用于全部输入类别（Sony XAVC HS / XAVC S、DJI、其它相机、普通视频）
> 与全部编码后端（NVENC / QSV / x265 / 其它）。
>
> 具体覆盖：
>
> * **§9.2 的"Sony 源 → 默认软解"被撤销。** 不得因 `is_sony_source()`
>   返回真就跳过硬解候选。
> * **§9.2.1 的"Sony 普通模式不得自动硬解"被撤销。** Sony 保留管线照常
>   运行（rtmd/nrtm/uuid 抽取与重建、stts 时长修复、`validate.compare`
>   全部保留），但不再因 Sony 身份强制软解。
> * **OD-1 的"production benchmark 是改默认的硬前置"被撤销。** 硬件优先
>   是维护者已作出的决定，不再以 benchmark 为门槛。性能仍**据实记录**：
>   实施期实测表明硬解 + x265 单任务**并非普遍更快**（详见 §5.5 与实施报告），
>   这一点不得被改写。
>
> 本节正文按原样保留，作为"撰写时如何判断"的记录。**现行策略以本区块为准。**
>
> 实施结果：Sony 4:2:0 素材在真实 CLI 上以 NVDEC 硬解跑通完整保留管线
> （`PRESERVED=37 MODIFIED=0 MISSING=0`）；4:2:2 素材经 `scale_cuda`
> 传输链同样逐字节保真，色度与位深未被降级。

> **本节是本次重构的核心策略变更。** 所有默认值变更都是 **PROPOSED**，
> 并且**部分与归档的既有产品决策相冲突**（§7.4 第 9 条）——冲突已在
> §19 显式记录为 `OPEN DECISION`，本文不替维护者宣布决策已完成。

## 9.1 解码 substrate 的三个层级（PROPOSED）

今天只有一个"读者"概念（`avsw` / `avhw`），且两个值都是 rigaya 的。
新模型需要**三个** substrate：

| substrate | 实现 | 可用性 | 保真度 | 证据 |
|---|---|---|---|---|
| `sw` | FFmpeg 软解 / rigaya `--avsw` | 恒可用 | 基准 | §4.1 |
| **`ffmpeg_hw`** | FFmpeg `-hwaccel cuda hevc_cuvid` 或 `-hwaccel qsv hevc_qsv` + `hwdownload` | **发布安装可用**（`tools/ffmpeg.exe` 在白名单） | **与软解逐帧位相同** | §5.1、§5.3 |
| `rigaya_hw` | rigaya `--avhw`（需 patched build + sha256 绑定） | 仅开发树（`tools/avhw/` 不进包） | 补丁后与软解相同；**未补丁丢 leading pictures** | §7.1、§7.3 |

**这是本次规划最重要的结构性发现（CONFIRMED）**：
`ffmpeg_hw` 在**所有四个维度**上都优于 `rigaya_hw`
（可用性、可分发、无需 hash 绑定、格式覆盖更广——NVDEC 接受 QSV 拒绝的 H.264 4:2:2）。

> **PROPOSED 结论**：新架构应以 **`ffmpeg_hw` 为硬件解码的主通路**，
> 把 `rigaya_hw` 降级为"仅 NVENC/QSV 编码后端内部的既有能力"，
> **不**再作为新功能的载体。
> **OPEN DECISION**：是否**删除** `rigaya_hw` 通路（连同 `encoders/hwdecode.py`
> 的 `PROVEN`/`PATCHED_BUILD` 与 `tools/avhw/` 的开发依赖）？
> 论据双向：① 它已交付、已测 83 案例、是 `--experimental-multihw` 今天唯一硬解来源；
> ② 它不可发布、需维护 sha256 绑定、格式覆盖更窄、且与 `ffmpeg_hw` 功能重叠。
> **建议**：保留但**不扩展**，并在 §19 把它记为"substrate 合并"候选。

## 9.2 普通模式（`--no-metadata` 未启用）

| 源类别 | 默认解码 | 依据 | 标记 |
|---|---|---|---|
| **Sony 源**（`is_sony_source()` 为真，即存在 `rtmd` 数据流） | **软解** | prompt §4.1 明文要求；且保留管线有既有的安全边界（§9.2.1） | PROPOSED（策略）／CONFIRMED（现状一致） |
| **非 Sony 源**（DJI / 其它相机 / generic video） | **硬件解码优先**（`ffmpeg_hw`），不可用则软解 | prompt §4.1 | PROPOSED |

**"硬件解码优先"的定义（PROPOSED，必须精确，避免退化成"失败后重试"）**：

```text
编码开始前：
  1. 探测源形态 (codec, chroma, depth, w, h, fps)
  2. 探测可用解码 substrate（§9.5 的能力探测，进程启动时一次）
  3. 在 (substrate, codec, chroma, depth, device) 上做**兼容性判定**
  4. 命中 → 一次性构造含硬解的完整命令
     不命中 → **不构造硬解命令**，直接构造软解命令（不是"先试硬解再降级"）
```

> **这直接落实了 prompt 的架构原则**：
> "不要把'硬件解码优先'实现为运行时反复失败、再逐层回退的循环。
> 先用已有 capability probing 选择解码器、编码器和像素格式，再构造命令；
> 只有探测不能覆盖的运行时失败，才进入明确且有限的错误处理路径。"

### 9.2.1 Sony 普通模式的安全边界（CONFIRMED —— 必须保留）

prompt 明文："普通 Sony 模式必须继续遵守当前 Sony preservation 所需的安全边界。
不得因为 NVENC/QSV 编码器可用，就自动将普通 Sony 素材改为硬件解码。"

**今天 Sony 保留管线的安全边界是（CONFIRMED）**：

| 边界 | 位置 | 与解码的关系 |
|---|---|---|
| 视频中间体必须是**精确帧** | `preservation/pipeline.py:175-209 _encoded_ok()`：产物存在非空 + `nb_read_packets > 0` | 硬解若丢帧，rtmd/video 时长失配 |
| rtmd 轨**原生容器拷贝**，不重编码 | `preservation/pipeline.py:215-238`（`gpac.mux_new` 一次性带全部轨） | 视频帧数变化 → 时长失配 |
| `validate.compare` 检时间线漂移并 **FAIL** | `preservation/pipeline.py:366-388`、`preservation/validate.py:116` | 帧数差异会被抓 |
| 硬编中间体的 stts 时长修复 | `preservation/pipeline.py:312-320`（`fix_hw_timing`） | 与解码无关 |

**INFERRED（重要）**：既然 `ffmpeg_hw` 与软解**逐帧位相同**（§5.3），
它在**技术上不会**破坏上述边界。但 prompt 明确禁止自动切换 Sony 普通模式，
**因此规划书按 prompt 的字面要求执行**，并把技术事实记录在此供决策参考。

> **OPEN DECISION（Sony + `ffmpeg_hw`）**：
> Sony 普通模式是否允许**显式**选择 `ffmpeg_hw`（例如新增
> `--decode ffmpeg-hw` 之类的独立开关，而**不是**改变默认）？
> * 选项 A（**建议**）：不允许。普通 Sony 恒软解；Sony 的硬解只在 `--no-metadata`
>   或 `--experimental-multihw` 下发生。**好处**：与 prompt 字面一致，
>   保留管线零风险。
> * 选项 B：允许显式 opt-in，默认仍软解。需要额外证明
>   `preservation/validate.py::compare` 在硬解中间体上不产生假 FAIL。
> 本文不替维护者选择。

## 9.3 `--no-metadata` 模式

见 §11 —— 独立成节，因为它是**同时**改变三层策略的开关，需要完整的
preservation 绕过审计。

## 9.4 输出验证分级（PROPOSED）—— 取代"帧数一致"作为默认门槛

prompt §7 明确：**不再把"硬件解码输出帧数必须与软件解码严格一致"
作为所有硬件解码路径的默认验收门槛。**

**但本 session 的实测（§5.3）改变了这个问题的形状**：
对 `ffmpeg_hw`，帧数一致**是可达成的**，不是"已知存在差异"。
因此规划书提出**按 substrate 分级**，而不是全局放弃帧数检查：

| 级别 | 检查内容 | `sw` | `ffmpeg_hw` | `rigaya_hw`（未补丁） |
|---|---|---|---|---|
| **L0 有效性** | ① 进程 `rc == 0`；② 产物存在且非空；③ ffprobe 能读到 ≥1 视频包；④ demuxer 不报 partial/truncated | **必做** | **必做** | **必做** |
| **L1 计数** | 产物视频包数 == 源容器 `stsz` 样本数 | 可选（见下） | **PROPOSED：必做**（实测可达） | **PROPOSED：默认关闭**（已知恒定差异） |
| **L2 序列** | 逐帧有序指纹比对（硬解 vs 软解） | n/a | **显式诊断工具**（不是默认） | 显式诊断工具 |

**分级理由（必须写清，否则会被误读为"放宽门槛"）**：

* **L0 永不放弃**。prompt 明文："不得将'不保留 metadata'解释为'可以忽略任何运行错误'"。
  L0 是"进程失败 / 输出缺失 / 输出损坏 / 不支持输入被静默当成功"的唯一拦截点。
* **L1 对 `ffmpeg_hw` 保留，因为它是免费的**：实测帧数精确一致，
  且 `_encoded_ok()` 已经在做同类检查（`core/batch_hw.py:1781-1831`，
  含 `expected=` 精确比对）——**复用既有实现，不新建探测器**。
* **L1 对 `rigaya_hw` 默认关闭，因为差异是已知且恒定的**：
  XAVC HS 恒 −3，XAVC S H.264 4:2:2 恒 −2（§7.1），
  且**预测式可算**。保留"必做"等于让所有 Sony 硬解必然被拒——
  **这正是 prompt 警告的"\u53ea改变 `--hw-decode` 默认值，却保留旧 gate
  导致所有 Sony 硬解都被拒绝"**。
* **L2 从"生产默认"降为"显式诊断"**：`verify_sequence()` 保留其实现与
  `tests/hwdecode` 的调用（§17 的 C 类），但不再挂在任何生产 CLI 上（§15）。

**PROPOSED 的 `rigaya_hw` 替代检查（不假装帧数一致，但也不放过真实故障）**：

```text
对 rigaya_hw：
  ① L0 全做
  ② **reader 身份断言保留**（avhw 请求不得静默变成 avsw）
     —— 这是身份检查，不是帧数检查，没有理由放弃
  ③ 帧数**不要求相等**，但要求符合已知形态：
     实测帧数 == 容器样本数 − leading_pictures(source)
     其中 leading_pictures 由 §7.1 的预测式从 stts/ctts/stss 算出
     （复用 encoders/integrity.py::container_video_samples 的 box 解析思路）
  ④ 偏差既不是 0 也不等于预测值 → **FAIL**（这是真实故障，不是已知缺陷）
  ⑤ 帧数与预测值一致时，在日志与 report 中**明确标注**
     "known leading-picture deficit: −3 (predicted)"，绝不写成"帧数一致"
```

> **★ 这条是本节的关键（PROPOSED）**：它把"放弃帧数门槛"变成了
> **"用已知缺陷的预测式替代盲目的相等于门槛"**。既不再拒绝所有 Sony 硬解，
> 又不接受任意丢帧。**这是本文对 prompt §7 的具体落实方案**，
> 并且它是**可测的**（预测式在归档里 297/297 命中，§7.1）。

**明确禁止的行为（prompt §7 明文，本文重申为实施约束）**：

```text
✗ 伪造帧数           ✗ 补帧或复制帧掩盖问题
✗ 忽略进程失败       ✗ 忽略输出文件缺失或损坏
✗ 把不支持的输入静默当成成功
```

## 9.5 编码前解码能力探测（PROPOSED）—— 扩展现有探测器，不新建

**prompt 要求**："不得直接重写另一套探测器。"

**PROPOSED 方案**：在 `encoders/caps.py` **内部扩展**，新增 FFmpeg 侧探测，
复用既有的数据模型与缓存机制：

| 新增 | 复用 |
|---|---|
| `probe_ffmpeg_hwaccels(ffmpeg)` → `frozenset` of `-hwaccels` 输出 | 复用 `caps.run_tool()`（`caps.py:62`） |
| `probe_ffmpeg_decoders(ffmpeg)` → 已注册的 hw decoder 集合 | 同上 |
| **`probe_decoder_acceptance(ffmpeg, device, codec, pix_fmt, fixture)`** → 真实尝试解码一个小 fixture | 新增（见下） |
| `FfmpegDecodeCaps` dataclass（device 列表、per-device surface formats、per-(codec,pix_fmt) 接受/拒绝） | 仿 `BackendCaps`（`caps.py:36`） |
| 写 `<output>/.1ktwork/caps/ffmpeg_decode_caps.json` + raw | 复用 `BackendCaps.save()` 模式（`caps.py:52`） |

**能力探测必须区分三种"否"（prompt §10 要求）**：

| 状态 | 含义 | 数据表示 | 决策 |
|---|---|---|---|
| **`measured`** | 探测成功，得到确定结果 | `True` / `False` + 证据（命令与输出） | 按结果决策 |
| **`unavailable`** | 该资源在本机**不存在**（无 GPU / 工具缺失） | `None` + 原因 | 视为不可用，**不伪造为 False** |
| **`probe_failed`** | 探测本身失败（超时、崩溃、输出不可解析） | `None` + 异常文本 | **不当作"不支持"**；降级到软解并**出声**，且**不得**缓存 |

> **prompt §10 明文**："资源采集必须区分 measured value / unavailable / probe failure。
> 无法可靠获取的资源值为 `N/A`，不能伪造为零。"
> 现有 `probe_backend()` 在探测失败时返回 `None`（`caps.py:158`），
> 调用方把它变成"保守能力"（`batch_hw.py:358-366`）——
> **这是 `probe_failed` 与 `unavailable` 的混淆**，必须在扩展时修正。
> **注意**：修正会改变 `caps=None` 的既有降级行为的**语义表述**（不改变结果），
> 属于 §18-C 的低风险项。

**探测的粒度与代价（PROPOSED）**：

* **进程启动时一次**（每 substrate × 每 device × 每 codec family）。
* 探测方式：**对每个 (decoder, codec, pix_fmt) 组合，用一个最小的内嵌 fixture
  真实解码 1 帧**，而不是靠 `-decoders` 列表推断。
  理由（CONFIRMED）：§5.4 显示 `hevc_qsv` **被注册**但对 `yuv422p10le` **拒绝**——
  **注册 ≠ 可用**。这与归档的教训完全一致
  （`docs/hardware-decode/README.md` §"设计上必须知道的三件事"第 2 条：
  "reader 身份必须从工具日志读，不能从命令行推断"——同理，
  **解码能力必须从真实解码读，不能从 `-decoders` 列表推断**）。
* fixture：用 FFmpeg `lavfi` 生成（如 `testsrc2` + `libx265` 编成目标 pix_fmt），
  或直接复用 `tests/hwdecode/fixtures.py` 的生成器
  （`GEN_C_X265` / `GEN_D_SYNTH` 已是 10-bit 4:2:0）。
  生成成本一次性，结果可缓存（见 G2 修复）。

> **⚠️ 能力探测 ≠ 输入可解（prompt §6.1 明文要求区分）**：
> 探测只能证明**探测范围内**的能力。规划书要求在日志与 report 中
> 用两个不同的词：
> `probe`（启动期能力结论）与 `attempt`（本文件的实际解码结果）。
> **绝不把"探测通过"写成"该输入解码成功"。**

## 9.6 运行时失败处理（PROPOSED）—— 明确、有限、不循环

prompt 明文禁止 `NVDEC fails → retry NVDEC forever`。

```text
硬解命令已构造并执行后的失败分类（PROPOSED）：

A. 设备初始化失败（device init failed）
   → 标记该 (substrate, device) 在**本次运行内**不可用（memo）
   → 该文件改用软解**重跑一次**
   → 不重试硬解（memo 阻止后续文件再试同一设备）

B. hardware frame transfer 失败（hwdownload / format 失败）
   → 视为 (substrate, device, codec, chroma, depth) 的**能力结论被推翻**
   → 该组合在本次运行内降级（memo）
   → 该文件软解重跑一次

C. 解码中途失败（rc != 0，非设备类错误）
   → 不重试同一硬解；软解重跑一次
   → 记录 reason code

D. 完整性检查失败（帧数/序列）
   → 丢弃硬解产物（safe_unlink）
   → 软解重跑一次

统一约束：
  · 每个文件**最多一次**硬解尝试 + **最多一次**软解重跑（硬上限，可配置但不得无限）
  · 复用既有 `MAX_HW_DECODE_FALLBACKS = 1` 的思想（core/batch_hw.py:197）
    并把它从"只能触发一次"改为"每个 (substrate,device,combo) 计数"
  · 任何失败路径**必须**确保**部分产物不作为最终文件交付**
    （今天已有：core/batch_hw.py:557 safe_unlink(output)）
  · 软解重跑**失败** → 该文件 failed（记录进 failed_files.json），不第三次尝试
```

> **与今天的差异（CONFIRMED）**：今天 `hw_fallbacks` 是**每文件**的计数器，
> 但 memo（`memoize_unavailable`，`encoders/hwdecode.py:230-249`）只在
> `capability_refused` / `device_unavailable` 两类上生效。
> 新模型要求 A/B 两类**也**进入 memo，否则 N 个文件会各自重复踩同一个坏设备。
> **这是"不允许无限重试"的实质要求**：今天的上限是"每文件 1 次"，
> 但**跨文件的重复踩坑没有被阻止**（除上述两类）。

## 9.7 恢复 `--seek` / `--trim` 的语义边界（PROPOSED）

今天：`route_decode(seek_requested=True)` 直接拒绝硬解
（`encoders/hwdecode.py:180-192`，reason `seek_not_equivalent`），
且**生产代码从不传入 `seek_requested`**（`core/batch_hw.py:466-474` 没有该参数，
三个调用点 `:974`、`:1235`、`:1560` 都不传）—— **INFERRED：该守卫今天是死代码**。

| 项 | 今天 | 新架构 |
|---|---|---|
| rigaya `--avhw` + seek | 不等价（同帧数同 PTS，不同画面，两侧确定性） | **保留拒绝**（CONFIRMED 的实测事实） |
| `ffmpeg_hw` + seek | **UNKNOWN**（从未测过） | **必须先测**：FFmpeg 的 `-ss` 在 hwaccel 下与软解是否等价？ |
| `--trim` | 读者等价（归档实测逐字节相同） | 保留 |

> **新增测试需求**：§17-C 增加"`ffmpeg_hw` + `-ss` 等价性"用例。
> 在测出结论之前，**新架构对 seek 一律走软解**（保守，与今天一致）。

---

# 10. 新的编码器优先级

## 10.1 优先级顺序（prompt §4.3 明文）

```text
普通自动选择：
    NVENC  →  QSV  →  x265（软件）
```

**现状（CONFIRMED）**：这**已经是**今天的默认行为（v0.6.2 起）：

* `1kt.py:1401` `AUTOSELECT_ORDER = ("nvenc", "qsv")`
* `1kt.py:1402` `HW_AUTOSELECT_FALLBACK = "x265"`
* `1kt.py:1405 _hw_encoders_available()` 真实探测每个后端
* `1kt.py:1445 resolve_default_backend()` 依序返回第一个可用者

> **因此 §10 的"新优先级"实际上是"保持既有行为并修正三个缺陷"**，
> 不是重新设计。**三个缺陷如下。**

## 10.2 缺陷 E-1：AV1 与 HEVC 的 codec 混用（PROPOSED 修复）

**prompt §4.4 明文**："不能因 AV1 后端不可用，就擅自把 AV1 改成 HEVC；
除非当前项目已经明确实现了相应行为且规划有证据支持。"

**现状（CONFIRMED）**：自动选择**只考虑 HEVC**，且 AV1 被刻意排除：

* `1kt.py:1399-1400` 注释：`# AV1 entries are deliberately absent: auto-selection must not silently change the codec.`
* `1kt.py:1433-1438`：只检查 `caps.codecs.get("hevc")`
* `tests/selftest/suites/cli.py:63`（区域）：`v062.autoselect 不含 AV1 (不静默改编码格式)`

**当前行为等价于**：**自动选择恒产出 HEVC**；AV1 必须由用户显式指定
（`--encoder nvenc-av1` / `qsv-av1` / `svtav1` 或 `--config` 的 `encoder` 字段）。

**PROPOSED 保持这一行为，并把它写成显式契约**：

```text
目标 codec 的决定权 100% 在用户（--encoder / --config），
自动选择只在**同一 codec** 的可用后端之间决策：
   目标 hevc → { nvenc, qsv, x265 }        按 NVENC → QSV → x265
   目标 av1  → { nvenc-av1, qsv-av1, svtav1 }  ← 见下
```

**AV1 的自动选择位置是 `OPEN DECISION`**：

* 今天：AV1 **永不**自动选择（必须显式）。
* prompt §4.3 的优先级表只写了 NVENC→QSV→x265（即 HEVC 族）。
* prompt §4.4 要求"同一目标 codec 的自动选择，应优先在兼容该 codec 的后端之间决策"。
  → 这暗示 AV1 也应有自动选择（`nvenc-av1` → `qsv-av1` → `svtav1`）。
* **但**（CONFIRMED 的既有证据）：`tests/selftest/suites/hardware_audio.py:595-599`
  记录 `v08hw.av1 NVENC AV1 与计划无关地失败 (KNOWN LIMITATION, 非本阶段引入)`
  —— **本机 `nvenc-av1` 无法创建编码器**。
* **建议（PROPOSED）**：保持"AV1 必须显式指定"。理由：① 有实测证据表明
  `nvenc-av1` 在本机不可用，自动选择 AV1 会立刻踩坑；② 改变 codec 是
  **输出语义变更**，风险远高于后端变更；③ 保持现状不违反 §4.3（它只约束 HEVC 族）。
  **但这是建议，不是决定。**

## 10.3 缺陷 E-2：失败原因未分类，降级梯无法按原因决策（PROPOSED 修复）

prompt §4.3 要求明确区分：

```text
① 未安装或工具不可用
② 编码器不支持输入格式
③ 编码器能力探测失败
④ 编码器初始化失败
⑤ 编码过程发生错误
```

**现状对照（CONFIRMED）**：

| prompt 要求区分 | 今天的对应物 | 状态 |
|---|---|---|
| ① 工具不可用 | `core/config.py:283-287` `raise FileNotFoundError`；`_hw_encoders_available` catch 住（`1kt.py:1421-1424`） | ✅ 已区分，但**只在自动选择里**；`hw_backend_for` 的 `find_hw_tool`（`batch_hw.py:334`）**未被 try 包裹** → 用户显式指定缺失工具会抛未捕获异常 |
| ② 不支持输入格式 | `plan_initial_format()`（`encoders/hw.py:368`）返回 `needs_downgrade` | ✅ 已区分，且**前置**（编码前），符合 prompt 要求 |
| ③ 探测失败 | `probe_backend()` 返回 `None` → 保守能力 + WARNING（`batch_hw.py:358-366`） | ⚠️ **与"能力确认为否"不可区分**（§3.3-G4）；且 `TimeoutExpired` 未捕获 |
| ④ 初始化失败 | `encoders/hwdecode.py:273-279` `R_STARTUP_FAILED`（loader failure rc 0xC0000135 等）、`:280-289` 空日志 | ⚠️ **已实现但结果被丢弃**（§6.5-H2，`core/batch_hw.py:544`） |
| ⑤ 编码过程错误 | `encoders/hw.py:240 classify_failure()` → `reader` / `environment` / `format` | ✅ 已区分且**被使用**（`batch_hw.py:613-661`） |

**PROPOSED 修复清单**：

1. **接上 ④**：`core/batch_hw.py:544` 的 `_cls` / `_detail` 必须进入决策：
   `startup_failed` → 视为**环境级**失败（与 `environment` 同级），
   **不逐级降格**（降格毫无意义：工具根本起不来，换色度/位深不会改变）。
   `capability_refused` → 进入 memo（阻止后续文件重踩）。
   `decode_failed` → 走既有 `format` 降格路径。
2. **拆分 ③**：`probe_backend()` 的返回值从 `BackendCaps | None` 改为
   三态（`measured` / `unavailable` / `probe_failed`），并捕获 `TimeoutExpired`。
3. **包裹 ①**：`hw_backend_for` 内的 `find_hw_tool()` 需要明确失败语义
   （用户显式 `--encoder nvenc` 但工具缺失 → 清晰的 FATAL 消息 + 退出码，
   而不是 traceback）。

## 10.4 缺陷 E-3：显式指定被自动选择覆盖的风险（PROPOSED 加固）

**prompt 明文**：
```text
--encoder qsv   不能自动变成 NVENC
--encoder x265  不能自动变成 QSV
```

**现状（CONFIRMED）**：今天**没有这个缺陷**，但**没有测试钉住它**，
且存在一处结构脆弱点：

| 事实 | 证据 |
|---|---|
| 用户给 `--encoder X` 时 `resolve_default_backend()` **完全不被调用** | `1kt.py:1736-1737`：`if args.encoder: encoder_name = args.encoder`（在 `else` 分支之前） |
| 故不存在覆盖 | CONFIRMED |
| **但**：`--encoder X --config Y`（Y 的 `encoder` 字段 ≠ X）会 **ValueError** | `1kt.py:1729-1733` |
| **但**：`--experimental-multihw --encoder qsv` 时，`args.encoder` 只被用来"pin 主后端"，**qsv 仍可能被 NVENC 队列分走文件** | `1kt.py:1792-1793` `if not (args.encoder and args.encoder in multihw_backends): encoder_name = multihw_backends[0]`；`run_multihw_pool` 的静态路由（`batch_hw.py:2418-2430`）**完全不知道**用户 pin 了谁 |
| 测试覆盖 | `tests/selftest/suites/cli.py` 有 `v062.autoselect 顺序 = NVENC -> QSV`（`:60`）但**没有**"显式指定不被覆盖"的用例 |

**PROPOSED**：新增 R3 守护 + 测试（§17-A 最后两行）。
并且 **PROPOSED（配合 §12.2）**：`--experimental-multihw` 与 `--encoder` 变成**硬冲突**，
从而消除上表最后一行的模糊性。

## 10.5 探测与执行必须一致（保留既有设计，PROPOSED 强化）

**CONFIRMED 的既有设计**（`1kt.py:1408-1412`）：自动选择与执行共用同一个探针。

**PROPOSED 强化**：引入能力缓存（§3.3-G2 修复）后，必须保证
**同一次运行的自动选择与执行命令来自同一个 `CapsSnapshot`**：

```text
启动时：  snapshot = probe_all(...)          ← 一次
          ├─ snapshot.ffmpeg_hw              → 解码 substrate
          ├─ snapshot.nvenc / .qsv           → 编码能力
          └─ 自动选择读 snapshot
逐文件：  执行命令读 snapshot（同一个对象）
          **不得**重新探测
```

> 这同时消除了 §3.4 记录的"`--check-features` 被跑 2–3 次"的浪费，
> 且不引入"自动选中的 ≠ 跑得起来的"风险。

---

# 11. `--no-metadata`

> **`--no-metadata` 在仓库中完全不存在（CONFIRMED）**。全仓 grep
> `no-metadata` / `no_metadata` → **0 命中**。本节是纯新增功能设计。

## 11.1 语义定义（prompt §4.2，PROPOSED 落实）

```text
--no-metadata
    → ALL source types（Sony / DJI / 其它相机 / generic video）
      → 硬件解码优先
    → 明确不保留其策略范围内的源 metadata
```

## 11.2 **必须先审计 preservation 路径**（prompt §4.2 明文要求）

这是本节最重要的工作，因为"跳过 preservation"会绕过**一条极长的依赖链**。

### A. Sony 保留管线实际做了什么（CONFIRMED，`preservation/pipeline.py` 全读）

```text
run_sony_pipeline(source, work_dir, encode_video, gpac, ffprobe, ...)
│
├─ 0. 续跑判定：final/output.mov 存在 && report.json 存在 &&
│     report["structural_success"] is True → 直接返回缓存报告   pipeline.py:114-129
├─ 1. **抽取 metadata bundle**（GPAC demux）                    pipeline.py:131-160
│     SonyPreservationBackend.extract(source, bundle_dir)       preservation/sony.py:185
│       → work_dir/metadata/{manifest.json, tracks/, boxes/, verify/}
│       → PreservationBundle{tracks, audio_tracks, boxes, nrtm,
│                            movie_timescale, video_timescale, brands}
│     **续跑复用规则**（pipeline.py:137-149）：
│       manifest 存在 && movie_timescale>0 && （无音频 || audio_tracks 非空）
├─ 2. movie_timescale 线程化到 gpac（GPAC 每次重写都会重置它）pipeline.py:162-168
├─ 3. **视频编码**（注入的回调）                                  pipeline.py:170-210
│     encoded_mov = work_dir/video/encoded.mov
│     _encoded_ok() 校验（ffprobe nb_read_packets > 0）
├─ 4. `CopyAudioBackend().note(source, work_dir/"audio")`        pipeline.py:213
├─ 5. **GPAC 一次性重建**：-new 携带 视频 + 每条源音轨 + 每条 metadata 轨
│     （**原生 ISOBMFF 轨拷贝**，精确源时序，不用 NHML）           pipeline.py:215-238
├─ 6. `sony.reconstruct(bundle, bundle_dir, stage)`              pipeline.py:241
│       → 载荷校验 / tref / cdsc / nrtm meta / brands
├─ 7. `gpac.flatten(stage)` + `gpac.set_brand(...)`              pipeline.py:249-282
│       HEVC → 恢复 XAVC brand；AV1 → 打 av01 brand（策略）      pipeline.py:250-282
├─ 8. **uuid box 字节级插入**（GPAC 26.02 无法创建 vendor uuid）  pipeline.py:284-302
├─ 9. `fix_hw_timing` → stts 时长修复（仅硬件中间体）             pipeline.py:304-320
├─ 10. meta item_type 字节补丁（Sony 00000000 vs GPAC 'mime'）    pipeline.py:322-343
├─ 11. AV1 colour 一致性（仅 codec==av1）                         pipeline.py:345-361
├─ 12. `checker.run_checks(...)` → **validate.compare**          pipeline.py:363-388
├─ 13. 写 `work_dir/report.json`                                 pipeline.py:390-394
```

### B. **`--no-metadata` 会绕过什么**（PROPOSED 审计结论）

| 步骤 | `--no-metadata` 下 | 依据 |
|---|---|---|
| 1 metadata bundle 抽取 | **跳过** | 策略范围内不保留 |
| 2 movie_timescale 线程化 | **跳过**（无意义） | 无 bundle |
| 3 视频编码 | **保留**（这是核心工作） | — |
| 4 音频 note | **跳过**（保留管线专用） | 音频走既有 FFmpeg `-map 0 -c:a copy` |
| 5 GPAC 重建 + metadata 轨拷贝 | **跳过** | rtmd/djmd 轨不再拷贝 |
| 6 `reconstruct`（载荷校验/tref/cdsc/nrtm） | **跳过** | — |
| 7 XAVC brand 恢复 | **跳过** | 见 §11.4 OPEN DECISION |
| 8 uuid box 插入 | **跳过** | — |
| 9 `fix_hw_timing` | **跳过** | 该修复是为 GPAC 导入硬件中间体服务的 |
| 10 meta item_type 补丁 | **跳过** | — |
| 11 AV1 colour 一致性 | **跳过** | — |
| 12 `validate.compare` | **跳过** | 见 §11.3 —— 这是最需要小心的一个 |
| 13 `report.json` | **保留，但内容改变** | §16.7 |

> **⚠️ 这批"跳过"与未提交的工作树改动直接冲突（CONFIRMED）**：
> `place_final()` 的新 `meta_source` 参数与
> `assert_file_level_meta` / `restore_track_refs` 断言（`git diff core/batch_hw.py`）
> 是**为保留管线服务的**。`--no-metadata` 路径**不得**触发这些断言
> （它本来就没有 meta 可保），否则会全部失败。
> 实施时 `_meta_manifest(work_dir)` 会返回 `None`（`work_dir/metadata/` 不存在），
> 因此 `place_final` 的行为是安全的（`core/batch_hw.py:2035-2044` 的
> `None` 语义 = 合法的 no-op）。**但必须加测试钉住这一点。**

### C. 哪些**基本输出验证仍然必须执行**（prompt §4.2 明文："不得将'不保留
metadata'解释为'可以忽略任何运行错误'"）（PROPOSED）

```text
--no-metadata 下**必须保留**的检查：
  ① 进程 rc == 0
  ② 产物存在、非空、ffprobe 可读、视频包数 > 0        （L0，§9.4）
  ③ demuxer 无 partial/truncated 报错                  （复用 _PARTIAL_READ_PATTERNS）
  ④ 输入不被静默当作成功（探测失败 → failed，不是 done）
  ⑤ 失败产物不得留在交付路径                            （safe_unlink + place_final 的原子改名）
  ⑥ 音频流不丢失（既不重编码也不丢轨）                   ← 见 §11.5 风险
  ⑦ 帧数：按 §9.4 分级（ffmpeg_hw → L1 必做）
  ⑧ 每文件的决策日志完整性（META_POLICY / DECODE_ROUTE / ENCODE_BACKEND）
```

**明确可以跳过的**（因为它们是 metadata 保留的专属验证）：

```text
✗ rtmd/djmd 载荷 sha256 比对
✗ nrtm 镜头配置比对 / meta item_type
✗ uuid box 清单比对
✗ tref/cdsc 轨引用比对
✗ Gyroflow 消费端逐帧四元数比对
✗ XAVC brand 一致性
✗ movie timescale 精确性
```

## 11.3 `--no-metadata` 与 `check_level` 的交互（`OPEN DECISION`）

今天 `--check basic|advanced|full` 的语义**按源类别定义**
（`docs/design/architecture.md` §7）：

| 级别 | Sony | DJI |
|---|---|---|
| `basic`（默认） | 时间线/轨清单/rtmd 载荷 sha256 + 时序 + tref + timecode | 轨道清单 + djmd/dbgi/tmcd 载荷 sha256/大小/样本数 + 音频流 + **帧数** |
| `advanced` | `basic` + 完整结构校验 + Gyroflow | `basic` + Gyroflow |
| `full` | `advanced` + 详细自检（64 项） | `advanced` + 逐轨时基/时长/载荷首尾字节 |

**`--no-metadata` 下 `--check` 应该做什么？** 三条候选：

| 选项 | 行为 | 评估 |
|---|---|---|
| **A（建议）** | `--no-metadata` 使 `--check` 的**保留类检查全部失效**，只剩 §11.2-C 的基本输出验证。日志明确写出"check level X downgraded to output-validity only (--no-metadata)" | 语义清晰，与"不保留 metadata"一致；**风险**：用户以为 `--check full` 仍在做深度校验 |
| B | `--no-metadata` **拒绝**非 basic 的 `--check`（参数冲突，rc=2） | 最保守，但会让"全自动批量 + --check full"的组合不可用 |
| C | `--check` 保持不变，只是保留类项目标记为 `SKIPPED`（不是 PASS） | 最保守的报告语义，但会产生大量 SKIPPED 噪声 |

**本文不替维护者选择。** 建议 A，因为 `full` 的 64 项自检
（`preservation/selfcheck.py`）**本身就是保留验证**，在 `--no-metadata` 下无对象。

## 11.4 `--no-metadata` 与容器/brand（`OPEN DECISION`）

今天 Sony 路径**恢复 XAVC brand**（`preservation/pipeline.py:266-282`），
DJI 路径类似。`--no-metadata` 下：

* 输出仍由 `place_final()` 保证容器合规（MOV→MP4 无损重封装，`core/batch_hw.py:1914`）。
* **但 brand 呢？** 一个不再携带 rtmd 的文件打上 `XAVC` brand 是否合法？
  **INFERRED**：不合法 —— XAVC 规范要求 rtmd/NRTM 的存在
  （`preservation/pipeline.py:250-259` 的 AV1 注释即按此逻辑拒绝打 XAVC tag）。
* **候选**：① 不设 brand（让 muxer 默认）；② 设通用 brand（`isom`/`mp42`）。
* **本文不决定。** 建议 ①（不额外设 brand），并把该决定写进 `--no-metadata` 的用户文档。

## 11.5 `--no-metadata` 的三个具体风险（PROPOSED，必须验证）

| # | 风险 | 说明 | 缓解 |
|---|---|---|---|
| **R-1** | **音频流/音轨丢失** | 今天 Sony/DJI 路径用 GPAC **逐轨原生拷贝**音频（`preservation/pipeline.py:215-238`），这条链**不做**任何音频处理。`--no-metadata` 改走 FFmpeg `-map 0`（`encoders/x265.py:177`）+ `-c:a copy`。**两条链的音频保真行为不同**（GPAC 容器拷贝 vs FFmpeg stream copy） | 增加"音频流清单 + 编码参数 + 基本流 sha256 在两条路径上一致"的验证用例（§17-B）。**这是本功能最大的回归风险。** |
| **R-2** | **DJI `--check` 的帧数检查被一并跳过** | DJI `basic` 本来就检查帧数（上表），`--no-metadata` 跳到保留路径外后该检查消失 | 由 §9.4 的 L1（`ffmpeg_hw` 必做）覆盖 |
| **R-3** | **`--no-metadata` 与 `--channel-sync` 的交互** | 今天 Sony 路径把 channel-sync 修正后的音频作为 `audio_sources` 传入重建（`preservation/pipeline.py:222-231`）。跳到 FFmpeg 路径后，channel-sync 的实现点变成 `encode_one` 的既有路径（`1kt.py:622`） | **必须验证 channel-sync 在 `--no-metadata` + Sony 上仍然生效**；prompt §13 要求"通过硬件编解码重构绕过 channel-sync"是禁止的 |

> **★ R-3 是隔离要求的直接体现**：prompt §13 明文
> "不得通过硬件编解码重构绕过 channel-sync"。
> `--no-metadata` 改变了音频的**执行位置**（GPAC 重建 → FFmpeg 命令），
> 因此必须在测试矩阵里显式证明 channel-sync 的结果不变。

## 11.6 `--no-metadata` 与其它参数的冲突（PROPOSED）

| 组合 | 处理 | 理由 |
|---|---|---|
| `--no-metadata` + `--experimental-multihw` | **允许**（multihw 隐含 `--no-metadata`，见 §12.1） | 一致的语义 |
| `--no-metadata` + `--retry-list` | 允许 | 正交 |
| `--no-metadata` + `--channel-sync` | **PROPOSED 允许**，但必须验证 R-3 | 正交功能，但执行点移动了 |
| `--no-metadata` + `--channel-sync-transparent` | 允许（transparent 不编码视频，无 metadata 可言） | `transparent_main` 本就不做保留 |
| `--no-metadata` + `--encoder x265` | **允许，且硬解策略仍生效**（prompt §12-B 明文要求） | 这是 §8 三层独立性最重要的测试点 |
| `--no-metadata` + `--preset all` | 随 `--preset all` 一并移除（§15） | — |

---

# 12. `--experimental-multihw`

## 12.1 目标行为（prompt §8）

```text
--experimental-multihw
    ↓ 隐含 --no-metadata
    ↓ 所有素材优先硬件解码
    ↓ 硬件专用编码调度
    ↓ 横向扩展到多个可用 GPU
```

## 12.2 参数冲突（prompt §8.1，**改变既有行为**）

prompt 明文："只要用户显式指定 `--encoder`，就报错退出。"

**这与今天的代码**不一致**（CONFIRMED）**：今天 `--experimental-multihw --encoder qsv`
是**合法**的（`1kt.py:1771-1776` 只拒绝**非** nvenc/qsv 的 `--encoder`）：

```python
1771:  if args.encoder and args.encoder not in ("nvenc", "qsv"):
1772:      raise ValueError(
1773:          f"--experimental-multihw manages HEVC NVENC+QSV itself; "
1774:          f"--encoder {args.encoder} conflicts with it "
1775:          f"(drop --encoder to let it probe the backends)."
1776:      )
```

**PROPOSED 新语义**：

```text
--experimental-multihw --encoder <任何值>   →  参数冲突，rc=2，退出
错误信息必须：
  ① 指出冲突的两个参数名
  ② 说明 multihw 自行负责探测/选择/调度硬件编码后端
  ③ 建议的修正（去掉 --encoder）
  ④ **在任何正式编码开始之前**（今天的检查已在 parse 之后的早期，符合要求）
```

**受影响的既有测试（CONFIRMED，必须同步修改）**：
* `tests/selftest/suites/hardware_audio.py:567`
  `extra = ("--no-hw-autoselect",) if encoder in ("x265", "svtav1") else ()`
  —— 该处**不**与 multihw 组合，不受影响。
* `tests/hwdecode/harness.py:3310`：`["--hw-decode", "auto", "--no-downgrade"]` —— 不涉及 multihw。
* **全仓 grep 未发现任何测试用 `--experimental-multihw` + `--encoder` 组合**
  → 这是一个**测试覆盖空白**，同时意味着改动风险低。

> **⚠️ 与 `--preset all` 的交互（CONFIRMED）**：`--preset all` 会让
> `run_multihw_pool` 对**每个档位**重新跑一遍全部源（外层 `for preset in requested`
> 在 `1kt.py:2036`，multihw 分支在 `2136+`）。移除 `--preset all` 顺带消除
> 这个"多档位 × 多后端"的组合爆炸。

## 12.3 无可用硬件编码后端（prompt §8.2）

**prompt 明文**：
```text
ERROR: no usable hardware encoder
EXIT
不回退 x265
```

**现状（CONFIRMED）**：`1kt.py:1780-1785` **已经是**这个行为：

```python
1780:  if not multihw_backends:
1781:      raise ValueError(
1782:          "--experimental-multihw requires at least one usable "
1783:          "HEVC hardware backend (NVENC or QSV); none was found. "
1784:          "Run `1kt.py --check-features` to inspect this machine."
1785:      )
```

`raise ValueError` → 被 `1kt.py:1817-1819` 捕获 → `print(f"[FATAL] {exc}")` + `return 2`。
**满足"不回退 x265"且"在任何编码之前退出"。**

**PROPOSED 微调**：错误文本应**明确**说出 `no usable hardware encoder`
（prompt 给的措辞），并**列出探测过的后端与各自的失败原因**
（工具缺失 / 探测失败 / 无 HEVC 能力），对应 §10.3 的原因分类。

## 12.4 单后端降级（现状保留，PROPOSED）

**CONFIRMED**：今天 `len(multihw_backends) == 1` 时**告警并退化为单后端池**
（`1kt.py:1786-1789`、`1914-1922`、`2219-2225`）：

```python
1787:  print(f"  [WARN] only {multihw_backends[0]} is available — "
1788:        f"multihw degrades to a single-backend pool "
1789:        f"(quality-consistency caveat no longer applies)")
```

**PROPOSED 保留**（它与 §12.3 不矛盾：§12.3 是"**一个都没有**"，这里是"只有一个"）。
**OPEN DECISION**：单后端时是否仍应报错？本文建议保留降级，
因为"只有一个可用后端"不是配置错误，而是机器事实；且降级路径今天已实现且被测试。

## 12.5 硬件专用调度（现状 + PROPOSED 修正）

**今天 `run_multihw_pool()`（`core/batch_hw.py:2403-2463`）的真实行为（CONFIRMED）**：

```python
2418:  for src in sources:
2421:      summary, _streams = probe_source(ctx.ffprobe, src)      ← **重复探测**
2422:      info = build_source_info(src, summary, _streams)
2426:      if chroma == "4:2:2":
2427:          nvenc_sources.append(src)          ← 4:2:2 恒给 NVENC
2428:      else:
2429:          (qsv_sources if flip else nvenc_sources).append(src)   ← 严格轮转
2430:          flip = not flip
2433:  ctrl_n = AdaptiveJobs(logger, start=2)
2434:  ctrl_q = AdaptiveJobs(logger, start=1)
2437:  while nvenc_sources or qsv_sources:
2439:      for _ in range(min(ctrl_n.current, len(nvenc_sources))): ...
2441:      for _ in range(min(ctrl_q.current, len(qsv_sources))): ...
2446:      with ThreadPoolExecutor(max_workers=len(wave)) as ex: ...
```

**四个 CONFIRMED 缺陷**：

| # | 缺陷 | 证据 |
|---|---|---|
| **M-1** | **`--jobs` 被完全忽略** | `1kt.py:2216-2218` 调用 `run_multihw_pool(ctx, backend, backend_qsv, sources, logger)` —— **不传 `jobs_value`**；`run_multihw_pool` 签名（`batch_hw.py:2403-2409`）也没有该参数 |
| **M-2** | **没有全局并发上限** | 两个 `AdaptiveJobs` 各自的 `cap = max(2, min(8, cpu_count))`（`batch_hw.py:2310`），wave 大小 = `ctrl_n.current + ctrl_q.current`，**最坏 16 个并发编码** |
| **M-3** | **重复探测** | `batch_hw.py:2421` 探测一次决定路由，`process_file_hw` 内部 `prepare_source_hw`（`batch_hw.py:2127`）**再探测一次** |
| **M-4** | **静态路由不理解"能力"与"设备"** | 4:2:2→NVENC 是硬编码（`batch_hw.py:2426`）；非 4:2:2 严格轮转；`AVAILABLE` 但"对某格式不可用"的后端仍会收到该格式的文件（依赖 `plan_initial_format` 事后降格，产生无谓的重编码） |

**PROPOSED 重设计**：

```text
run_multihw_pool_v2(ctx, backends: list[BackendHandle], sources, logger, budget)
│
├─ 1. 探测一次（复用 §3.4 的 CapsSnapshot），per-file 只读快照
├─ 2. 对每个文件做**编码前后端选择**（不是运行时轮转）：
│       candidates = [b for b in backends if b.can_encode(codec, chroma, depth)]
│       若 candidates 为空 → 该文件 failed（明确原因），不静默降级 codec
│       否则按 §10 优先级 + 设备负载选择
├─ 3. **设备为单位的队列**（§13）：每个 device 一个队列 + 一个并发预算
├─ 4. 全局预算由 §14 的 ResourceBudget 给出（内存/显存/设备）
└─ 5. wave 从**有预算的**设备队列取任务；无预算 → 等待（不新建任务）
```

> **★ prompt §8.4 明文**："多硬件模式仍需服从 `jobs=auto` 的资源约束，
> 但无可用硬件时必须退出。不能把 `jobs=auto` 解释为允许无限创建并发任务。"
> **今天 M-1 + M-2 恰恰违反这一条**：multihw 既忽略 `--jobs`，又没有上限。

## 12.6 `--experimental-multihw` 的质量一致性声明（现状保留）

**CONFIRMED**：今天有 `EXPERIMENTAL_BANNER`（`1kt.py:1908`）与
"quality consistency NOT guaranteed" 的 WARNING（`1kt.py:1910-1913`），
且单后端时明确声明该 caveat 不适用（`1917-1922`）。
**PROPOSED 保留**——这是诚实的能力声明，不应删。

---

# 13. 多 GPU 横向扩展

## 13.1 **现状：完全不存在（CONFIRMED）**

| 需要的通路 | 今天的状态 | 证据 |
|---|---|---|
| 设备枚举 | 仅 WMI 报告（`core/versions.py:100-121`），**不用于任何决策** | 全函数已读 |
| 每设备能力 | `BackendCaps.device` 是**单数字符串**，`_env_info()` 丢弃 `#N` 索引 | `encoders/caps.py:36-43`、`128-147` |
| NVEncC 设备选择参数 | **不发出**（`command()` / `build_args()` 无 `--device`） | `encoders/nvencc.py:135-189` 已读全 |
| QSVEncC 设备选择参数 | **不发出** | `encoders/qsvencc.py:120-174` 已读全 |
| 设备与任务的调度 | **无** | `run_hw_pool` / `run_multihw_pool` 按**后端**分队列，不按设备 |
| 失败后的资源释放与任务状态 | 每文件独立（`process_file_hw` 返回状态字符串 + `record_failure`） | `core/batch_hw.py:2270-2289` |

**归档侧同样未验证（CONFIRMED）**：`docs/hardware-decode/final-report.md` §10 第 6 条
"单 GPU 主机 —— 多 GPU 设备选择未验证"；`xavc-hs-frame-count.md` §3.3 记录
`--device 1` 直接失败（`Invalid Device Id = 1`），研究全程单 GPU。

## 13.2 **关键未知（UNKNOWN → 必须验证的第一件事）**

> **`NVEncC` / `QSVEncC` 是否支持选择 GPU？参数名是什么？**

`encoders/hwdecode.py:293-294` 的错误分类器匹配 `"invalid device id"`，
这**暗示** rigaya 工具有某种 `--device` 参数（归档也引用了 `--device 1`），
但：

* 全仓**没有**任何生产代码发出该参数（CONFIRMED）；
* 本机 `tools/` 下有 `docs/reference/nvenc/NVEncC_Options.en.md` 与
  `docs/reference/qsv/QSVEncC_Options.en.md`（CONFIRMED 存在，
  且 `docs/reference/` 下 grep `--device` 有命中）——
  **这应当是验证该参数的权威来源**；
* **本 session 未读这两份参考文档的设备章节**（超出只读侦察的优先顺序）。

**PROPOSED 的第一步**（§20 阶段 0）：

```powershell
# 1. 从 shipped 工具的 help 读权威参数名
tools\NVEncC_9.31_x64\NVEncC64.exe --help | Select-String -Pattern 'device|gpu|adapter'
tools\QSVEncC_8.26_x64\QSVEncC64.exe --help | Select-String -Pattern 'device|gpu|adapter'
# 2. 读参考文档
docs\reference\nvenc\NVEncC_Options.en.md
docs\reference\qsv\QSVEncC_Options.en.md
# 3. 真实枚举本机 GPU
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv
```

**本机 GPU（来源：`docs/hardware-decode/final-report.md` §2，CONFIRMED 的历史记录）**：
NVIDIA RTX 5070 Laptop 8 GB（driver 616.56）+ Intel Arc 140T（32.0.101.8974）。
→ **本机是多 GPU 主机**（1 张 NVIDIA + 1 个 Intel iGPU），
因此"多 GPU 横向扩展"的**单机验证在本机可行**，
但"多张 NVIDIA"**不可行**（只有 1 张 NVIDIA）。

## 13.3 PROPOSED 的设备模型

```text
Device = (substrate, device_index, kind)
   kind ∈ { nvidia, intel }
   例：(ffmpeg_hw, 0, nvidia) = NVDEC on GPU 0
       (ffmpeg_hw, 0, intel)  = QSV on Intel iGPU
       (rigaya_hw, 0, nvidia) = NVEncC --device 0   ← 参数名待验证
       (rigaya_hw, 0, intel)  = QSVEncC --device 0  ← 参数名待验证
```

```text
Job queue（按设备）
    │
    ├─ Device A (nvidia 0)   queue_A  budget_A
    ├─ Device B (intel 0)    queue_B  budget_B
    └─ Device N ...

调度规则（PROPOSED）：
  1. 每个 job 在**编码前**确定其 device 需求：
       解码 substrate：由 §9 策略 + 源格式决定
       编码 backend：由 §10 优先级 + 能力 + 设备可用性决定
  2. 同一 job 的解码 device 与编码 device **可以不同**
       （例如 NVDEC 解码 + QSV 编码）—— 这是三层独立性的直接结果
  3. 每个 device 有独立并发预算（§14），预算来自该设备的显存/会话限制
  4. 任务失败 → 释放该 device 的槽位 + 记录状态（不得泄漏槽位）
  5. 无可用 device → 按 §12.3（multihw 下）或 §10（普通模式下落到 x265）
```

## 13.4 `OPEN DECISION`：多 GPU 的投入顺序

| 选项 | 内容 | 评估 |
|---|---|---|
| **A（建议）** | **只做 FFmpeg 侧的多设备**（`-hwaccel_device` 已确认可用，§5.1）<br>rigaya 侧的设备选择**先验证参数存在性再决定** | FFmpeg 侧零未知；rigaya 侧有真实未知（§13.2） |
| B | 同时做 rigaya 侧（需先验证 `--device` 语义、`Invalid Device Id` 行为、每设备 `--check-features`） | 收益是 multihw 的横向扩展；成本是 double 的验证工作 |
| C | 完全不做多 GPU（本机只有 1 张 NVIDIA，横向扩展收益有限） | 最省，但与 prompt §8.3 要求不符 |

**本文建议 A**：先交付 FFmpeg 侧的多设备（它同时解决了 §13.1 的全部四个缺口在
`ffmpeg_hw` substrate 上的问题），把 rigaya 侧作为**独立的后续阶段**。
**prompt 明文要求"不得因为检测到多张 GPU 就假定已支持任意数量的设备"**
—— 选项 A 正是按此执行：**先证明 1 个设备可用，再证明第 2 个，
绝不从"N 张卡存在"推出"N 张卡可用"**。

## 13.5 每设备能力的独立探测（PROPOSED）

prompt 要求审查"每设备能力 / 不同设备的编码限制"。
`--check-features` 的输出**天然是单设备的**（`_env_info()` 只抓 `#0:` 行）。
PROPOSED：

```text
对每个 (device) 独立执行编码能力探测：
    NVEncC  --device <i> --check-features
    QSVEncC --device <i> --check-features
    FFmpeg  -init_hw_device cuda=cu:<i> / qsv=qsv:<i>  + 一次最小探测解码
并把结果存成 **per-device** 的能力快照（而不是每 backend 一份）。
```

> **⚠️ 代价（CONFIRMED 的既有测量）**：`--check-features` 每次"花费数秒"
> （`1kt.py:1740-1741` 注释）。N 个设备 × 2 个后端 = 2N 次探测。
> **这是 §3.3-G2（缓存）从"优化"升级为"必要"的原因**：
> 多设备下必须缓存，否则启动开销线性增长。

---

# 14. `jobs=auto` 与资源调度

## 14.1 现状（CONFIRMED，逐条）

| 项 | 今天的状态 | 证据 |
|---|---|---|
| **默认值** | `--jobs` `default="1"` | `1kt.py:1278` |
| 合法值 | 字符串 `"1"`、`"auto"`、或任何 `int()` 接受的值（**含 `0`、负数、前导空格、`+4`、`007`**） | `1kt.py:1987-1993`；**无正数校验、无上限** |
| 非法值 | `int()` 抛 `ValueError` → `print("[FATAL] ...")` + `return 2` | `1kt.py:1990-1993` |
| 软件路径 | **完全无效**（严格串行） | `1kt.py:2043` |
| 硬件路径 `N` | `ThreadPoolExecutor(max_workers=N)`，**一次性提交全部** | `core/batch_hw.py:2374-2381` |
| 硬件路径 `auto` | 波次调度：`AdaptiveJobs(start=2)` | `core/batch_hw.py:2383-2400` |
| multihw | **`--jobs` 被忽略**（§12.5-M1） | `1kt.py:2216-2218` |
| **`AdaptiveJobs` 的唯一输入** | **每波聚合编码 fps** | `core/batch_hw.py:2292-2349` |
| **`AdaptiveJobs.cap`** | `max(2, min(8, os.cpu_count() or 4))` → **只有 CPU 核数** | `core/batch_hw.py:2310` |
| **内存 / 显存感知** | **完全没有** | 全仓 grep（见下） |

**内存/显存遥测的全仓 grep 结果（CONFIRMED）**：

| 命中 | 内容 | 是否生产代码 |
|---|---|---|
| `tests/hwdecode/runners.py:350` | `nvidia-smi --query-gpu=utilization.gpu,memory.used` | ❌ 测试专用 |
| `tests/hwdecode/runners.py:327` | `tasklist /FI IMAGENAME eq ...`（工作集采样） | ❌ 测试专用 |
| `tests/hwdecode/runners.py:305` | 注释："the project has **no psutil dependency**" | ❌ |
| `tests/selftest/paths.py:80-119` | `ctypes` + `psapi.GetProcessMemoryInfo` 读工作集 | ❌ 测试专用 |
| `core/versions.py:100-121` | WMI `Win32_VideoController` → 只写报告 | ⚠️ 生产，但**不参与调度** |
| `1kt.py:1381`、`core/dashboard.py:6`、`core/dashboard_ui.py:3` | "nvidia-smi style" —— **纯注释/文案** | ❌ |
| `1kt.py:1301` | `--log-level debug` 的 help 声称"含完整命令行/阶段耗时/**逐阶段内存**" | ⚠️ **该内存日志在代码中不存在** |

> **CONFIRMED 结论**：
> ① **生产代码中零内存/零显存遥测**；② 调度器的唯一机器输入是 `os.cpu_count()`；
> ③ `psutil` **不是**依赖（测试注释明确说明）；④ `--log-level debug` 的
> "逐阶段内存"是**未实现的 help 承诺**（与 §6.4 的 `--hw-decode-verify` 同类）。

## 14.2 硬约束：**不得引入新依赖**（PROPOSED）

`tests/hwdecode/runners.py:305` 明确记录项目"has no psutil dependency"，
且仓库**没有** `requirements.txt` / `pyproject.toml` / `setup.py`（顶层）
—— 引入 `psutil` 会是一个新的分发决策。

**PROPOSED：用已有手段采集，全部走 `subprocess` / `ctypes`（标准库）**：

| 资源 | 采集方式 | 可用性 | CONFIRMED 依据 |
|---|---|---|---|
| **系统可用内存** | `ctypes` → `GlobalMemoryStatusEx`（`MEMORYSTATUSEX.ullAvailPhys`） | Windows（项目只支持 Windows：`start.bat`、`psapi` 用法、GPAC 路径硬编码） | 项目已在本机用 `ctypes`+`psapi`（`tests/selftest/paths.py:80-119`），**同一手法可复用** |
| **NVIDIA 显存** | `nvidia-smi --query-gpu=index,memory.total,memory.used --format=csv,noheader,nounits` | 有 NVIDIA 驱动时 | 测试代码已在用（`runners.py:350`） |
| **Intel iGPU 显存** | **UNKNOWN** | — | **仓库内无证据**；iGPU 共享系统内存，可能无需独立预算 |
| **GPU 利用率** | `nvidia-smi --query-gpu=utilization.gpu` | 有 NVIDIA 驱动时 | 同上 |
| **每设备编码会话余量** | **UNKNOWN** | — | 归档记录了"NVENC session 争用"是**推断**（`xavc-hs-frame-count.md` §10 第 17 条："VRAM vs NVENC session 争用，**无 VRAM 遥测**，不得把任一说法当作已确认根因"） |
| CPU 核数 | `os.cpu_count()` | 恒可用 | `core/batch_hw.py:2310` |

## 14.3 **三态资源值**（prompt §10 明文要求，PROPOSED）

```python
@dataclass(frozen=True)
class ResourceSample:
    """One resource reading, with the three states kept distinct."""
    value: float | None      # None => not measurable here
    state: str               # "measured" | "unavailable" | "probe_failed"
    detail: str = ""         # probe_failed: the error text. unavailable: why absent.

    # PROHIBITED by design: any code that does `value or 0` / `value or default`
    # on this field.  A missing reading is NOT zero.
```

**实施约束（PROPOSED）**：

```text
· unavailable   → 该资源**不参与**预算计算；日志/报告写 "N/A"
· probe_failed  → 该资源**不参与**预算计算；**出声**（WARNING + reason）；
                  并且**不得**把它当作 0，也**不得**当作"无限"
· measured      → 参与预算
· 报告字段：`"vram_mib": null`（不是 0）—— 与归档的既有口径一致
  （tests/hwdecode/runners.py:400-402 "null means the platform did not report"）
```

> **这条与归档的既有口径完全一致（CONFIRMED）**：
> `docs/hardware-decode/final-report.md` §J.1 遥测口径栏：
> "用 `tasklist` + `nvidia-smi` 采样；**平台未报告时记 null，不记 0**"。
> 规划书直接沿用这个已被项目采纳的口径。

## 14.4 PROPOSED 的调度模型

```text
ResourceBudget
  ├─ system_memory:   ResourceSample    (bytes available)
  ├─ devices:         list[DeviceBudget]
  │     └─ DeviceBudget(device, vram: ResourceSample, sessions: ResourceSample|None,
  │                     max_parallel: int, running: int)
  └─ cpu_cores:       int

PerJobCost（每个 job 的**预估**占用，用于准入判定）
  ├─ decoder:  substrate → 估算显存/系统内存（PROPOSED：小常数 + 标定）
  ├─ encoder:  backend   → 估算显存（NVENC 会话 + 帧缓冲）与系统内存
  └─ pipeline: 缩放/保留管线的系统内存占用

准入规则（PROPOSED，prompt §10 的 6 条目标逐条落实）
  1. 初始并发 = f(设备能力, 任务类型)                    ← 目标 1
       · 每设备初始并行 = min(device.max_parallel, ceil(cpu_cores / 2))
       · 初始值必须**保守**（从 1 或 2 起），不猜高
  2. 并发上限受可用内存与显存约束                        ← 目标 2
       · 若某设备 vram 为 measured：sum(PerJobCost.vram) + margin ≤ vram_available
       · 若为 unavailable / probe_failed：**不加显存约束**，但**降低**该设备的初始并行
         （保守默认，并在日志写明"no VRAM telemetry; using conservative default"）
  3. 资源压力上升 → **停止增加**任务                      ← 目标 3
       · 派生指标：每波峰值内存/显存、聚合 fps 变化
       · 压力信号出现 → 冻结当前并行数（不收缩、不增长）
  4. 必要时**减少后续**派发                              ← 目标 4
       · 波次之间降低下一波大小（**不**收缩到 0，**不**杀运行中任务）
  5. **不因普通资源波动强杀正在运行的编码任务**            ← 目标 5
       · 严禁：任何基于资源阈值的 kill/terminate
       · 资源压力只影响**派发决策**，不影响**运行中的进程**
       · 唯一的 kill 通路是用户中断（Ctrl-C）与超时（若将来引入）
  6. 失败任务不得留下被误认为成功的最终文件               ← 目标 6
       · 复用既有的 place_final 原子改名（core/batch_hw.py:1914）
         + safe_unlink（失败路径）
       · 新增：失败时**必须**释放该设备的并发槽位（不得泄漏 → 否则预算单调衰减）
```

**多 GPU 必须以设备为单位（prompt §10 明文，PROPOSED）**：

```text
✗ 全局一个 jobs 数字
✓ 每设备一个 (max_parallel, running, budget)
✓ job 准入判定针对**它将要使用的那个 device**
✓ 报告里每设备单独列 vram/利用率/并发
```

## 14.5 `--jobs` 默认值改为 `auto`（PROPOSED，**需同步改测试**）

prompt §10："全局默认：`--jobs auto`"。

**PROPOSED**：

| 项 | 今天 | 目标 |
|---|---|---|
| parser default | `"1"`（`1kt.py:1278`） | `"auto"` |
| help 文本 | "1(默认, 单线程) \| N \| auto" | 更新为 "auto(默认) \| 1 \| N" |
| 校验 | 只做 `int()` | **加上限与正数校验**：`1 ≤ N ≤ 合理上限`（上限需决定，见下） |
| 软件路径 | **忽略** `--jobs` | **PROPOSED：接入**（§14.6） |
| multihw | **忽略** | **PROPOSED：接入**（§12.5） |
| 日志 | `logger.info("Jobs: %s", jobs_value)` | 增加"每设备并发 + 预算来源"的日志行 |

**`OPEN DECISION`：`--jobs N` 的上限是多少？**
今天 `int("256")` 会被直接传给 `ThreadPoolExecutor(max_workers=256)`（CONFIRMED）。
候选：① 不设上限（信任用户，但 `AdaptiveJobs.cap` 仍是 8，行为不一致）；
② 上限 = `min(32, cpu_count * 2)`；③ 上限 = 各设备 `max_parallel` 之和。
**建议 ②**（与 `AdaptiveJobs.cap` 的量级一致，且防止 N 与 auto 行为分叉）。
**本文不决定具体数字。**

**⚠️ 必须同步修改的测试（CONFIRMED）**：
`tests/selftest/suites/hardware_audio.py:323-332` 用一个**命名增量**规则
**钉住了 v0.8.0 的全部 CLI 参数**：

```python
323:  V080_BASELINE = { ..., "--jobs", "--hw-decode", "--hw-decode-verify",
324:                    "--no-hw-autoselect", "--headless", "--retry-list",
325:                    "--keep-work", ... }
330:  record("v08hw.cli 除 P4.6 的时变 opt-in 三个参数外, 没有其它新增 CLI 参数",
331:         args_now - P46_ADDED <= V080_BASELINE,
332:         f"unexpected: {sorted(args_now - P46_ADDED - V080_BASELINE)}")
```

该文件自己的注释（`:301-311`）说明这个设计意图：
"so a future phase that adds an argument must edit this list and cannot slip one in silently"。

> **★ 结论（CONFIRMED）**：**任何 CLI 参数的新增或移除，都必须在同一个改动里
> 编辑 `V080_BASELINE`，否则 `--level unit` 立刻变红。**
> 这适用于 §15 的全部移除项与 `--no-metadata` 的新增。**这是实施阶段的第一个拦路石。**

## 14.6 软件路径接入并发（PROPOSED，**本规划的性能核心**）

**动机（CONFIRMED）**：§5.5 表明硬解**不提供**单任务吞吐收益。
而软件路径今天**严格串行**（`1kt.py:2043`），`--jobs` 完全无效。
这是**最大的一块未开发吞吐**，且已有全部基础设施
（`run_hw_pool` 的波次算法、`DashboardStatus` 的线程安全、
`core/logging_utils.py:21 _CSV_LOCK` 保护 CSV 追加）。

**PROPOSED**：

```text
为软件路径引入与硬件路径同构的池：
  run_sw_pool(ctx, sources, logger, budget)
    · 每个 job：prepare_source → encode_one_* → place_final
    · 并发由 §14.4 的 ResourceBudget 决定
    · x265 本身是多线程的 → 每 job 需要限制 x265 线程数
      （否则 N 个 job × 全核 = 过度订阅）
```

**⚠️ 必须解决的耦合（PROPOSED，prompt §10 明文）**：
"解码器/编码器组合"必须进入资源模型。x265 的线程数是关键未知量：

| 未知 | 状态 |
|---|---|
| x265 在本机默认使用多少线程？ | **UNKNOWN** —— `x265.json` 有 `lookahead_threads`（`encoders/x265.py:83`）但未读其值 |
| x265 是否支持 `--pools` / `frame-threads` 限制？ | **INFERRED 支持**（x265 标准参数），但**仓库未使用** |
| 并发 N 个 x265 的最优 N？ | **UNKNOWN** —— 需实测 |

**PROPOSED 的实测计划（§17-C 新增用例）**：
对 `N ∈ {1, 2, 3, 4, 6}` 与 `x265 pools ∈ {默认, 半核, 1}` 的组合，
在固定素材上测聚合吞吐，产出**本机标定表**。
**这必须在实施前完成**，否则 `jobs=auto` 会基于猜测调参 ——
而 prompt 明确否定"无写死预算表"式的假自适应
（`1kt.py:1281` 现有 help：`auto(波次实测聚合吞吐动态调整工作数, **无写死预算表**)`）。

> **★ 这是本规划书对"目标不是只追求日志里出现 NVDEC/QSV 字样"的正面回答**：
> 减少实际处理时间的**主要**手段是并发，不是解码 substrate。
> 硬解的价值（更少 CPU/帧）只在**并发**场景下转化为吞吐。

---

# 15. 旧 CLI switch 移除清单

> **prompt 明文**："本次不增加一套新的兼容开关体系。"
> "这是明确的目标，不要默认保留旧开关并增加 deprecation warning 来替代移除。"
>
> **因此本节的每一项都是"直接移除"，不是"弃用"。**
> 但每一项都先给出**完整移除范围**（prompt 要求）。

## 15.1 移除范围的调查方法（CONFIRMED）

对每个开关执行了四类扫描：

| 扫描 | 方法 |
|---|---|
| parser 定义 | `Select-String -Path 1kt.py -Pattern 'add_argument'` + 逐段落读 |
| 内部调用点 | 全仓 grep 参数名（`hw_decode` / `no_hw_autoselect` / `args.preset` / `multiple_presets`） |
| 文档引用 | 全仓 grep `*.md`（排除 `dist/`、`build/`、`dist-deploy/`、`olddocs/backup/`） |
| 测试引用 | 全仓 grep `tests/**/*.py` |
| 脚本/打包引用 | `start.bat`、`watchfolder.py`、`release/build_release.py`、`release/verify_package.py` |
| 外部依赖 | `watchfolder.py` 的透传参数集；`--retry-list` 生成的 `failed_files.json` |

## 15.2 `--hw-decode`

| 项 | 内容 |
|---|---|
| **parser 定义** | `1kt.py:1318-1333`，`choices=("off","auto","require")`，`default="off"` |
| **动作** | **移除** |

### 完整移除范围

**A. Parser**
```text
1kt.py:1318-1333   整块 add_argument("--hw-decode", ...) 删除
```

**B. 内部调用点（全部位于硬件路径）**

| 位置 | 用途 | 处置 |
|---|---|---|
| `1kt.py:2196` | `BatchCtx(hw_decode=args.hw_decode, ...)` | 删除该 kwarg |
| `core/batch_hw.py:332` | `decode_policy = getattr(args, "hw_decode", HW_DECODE_OFF)` | 改为从新的解码策略对象取（§9） |
| `core/batch_hw.py:335` | `if decode_policy != HW_DECODE_OFF:` → `resolve_decode_tool` | **保留逻辑**，入口改为新策略 |
| `core/batch_hw.py:349-354` | `setattr(args, "_hw_decode_tool_reason", ...)` | 迁移到新策略对象（**不要**继续往 argparse.Namespace 上挂私有属性） |
| `core/batch_hw.py:417-418` | `hw_encode_with_fallback(hw_decode=..., hw_decode_memo=...)` | 替换为新解码策略参数 |
| `core/batch_hw.py:428-432` | docstring 提及 `hw_decode` | 重写 |
| `core/batch_hw.py:465-467` | `memo = hw_decode_memo ...` / `route_decode(policy=hw_decode,...)` | 迁移 |
| `core/batch_hw.py:482` | `if ... and hw_decode == HW_DECODE_REQUIRE` | 见 §15.2 末的 `OPEN DECISION` |
| `core/batch_hw.py:913-914` | `encode_one_sony_hw(hw_decode=..., hw_decode_memo=...)` | 同上 |
| `core/batch_hw.py:982` | 转发 | 同上 |
| `core/batch_hw.py:1168-1169` | `encode_one_hw_classic(...)` | 同上 |
| `core/batch_hw.py:1243` | 转发 | 同上 |
| `core/batch_hw.py:1482-1483` | `encode_one_dji_hw(...)` | 同上 |
| `core/batch_hw.py:1568` | 转发 | 同上 |
| `core/batch_hw.py:2068`、`2071-2072` | `BatchCtx` 字段 `hw_decode`、`hw_decode_memo` | 替换 |
| `core/batch_hw.py:2192-2193`、`2228-2229`、`2261-2262` | ctx → 三个编码函数的转发 | 同上 |
| `core/batch_hw.py:8`、`321` | 模块/函数 docstring 提及 `--hw-decode` | 重写 |
| `encoders/nvencc.py:11` | docstring 提及 `--hw-decode` | 重写 |
| `encoders/hwdecode.py:331-332` | docstring 提及 `--hw-decode auto/require` | 重写 |
| `core/config.py:258` | docstring 提及 `--hw-decode` 路径 | 重写 |

**C. 底层实现：保留还是删除？**（prompt 明文要求审查）

| 符号 | 位置 | 是否被 `--hw-decode` 独占 | 处置 |
|---|---|---|---|
| `hwdecode.route_decode` | `:135` | 否 —— `--experimental-multihw` 与普通硬件路径都需要解码决策 | **保留并扩展**（§9） |
| `hwdecode.memoize_unavailable` | `:230` | 否 | **保留并扩展**（§9.6） |
| `hwdecode.classify_reader_failure` | `:254` | 否 —— **今天已被调用**（`batch_hw.py:544`），只是结果被丢弃 | **保留并接上**（§10.3） |
| `hwdecode.resolve_decode_tool` | `:377` | 是（rigaya 硬解工具解析） | **保留**（rigaya substrate 仍需它，见 §9.1） |
| `hwdecode.PATCHED_BUILD` | `:336` | 是 | **保留**（同上） |
| `hwdecode.decode_tool_is_stock` | `:423` | 是；**生产代码零调用**（仅 `tests/hwdecode/harness.py:825`） | **保留**（诊断价值）；**OPEN**：是否删除 |
| `hwdecode.is_hardware_unavailable` | `:313` | 是；**生产代码零调用**（仅 `harness.py:786`） | 同 |
| `hwdecode.PROVEN` / `REFUSED` | `:90` / `:101` | 是 | **保留但需扩充**为 FFmpeg 侧的接受/拒绝表（§9.5） |
| `POLICY_OFF/AUTO/REQUIRE` | `:44-47` | 是 | **替换**为新的策略枚举 |
| `integrity.verify_encode` | `:285` | **否**：只被 `batch_hw.py:527`（`reader=="avhw"` 分支）调用 | **保留并降级**（§9.4：L0 保留，L1 按 substrate 分级） |
| `integrity.verify_sequence` | `:396` | 是；**唯一调用者在 harness**（§6.4） | **保留为显式诊断工具**（prompt 允许："可能保留为显式诊断工具"） |
| `integrity.container_video_samples` | `:111` | 否 | **保留** —— 它是帧数真值的唯一来源 |
| `integrity.independent_decoded_frames` | `:192` | 否 | **保留** |
| `integrity.frame_signatures` / `digest` | `:496` / `:536` | 是（供 `verify_sequence`） | **保留** |
| `integrity.reader_identity_from_log` / `encoder_input_frames` | `:238` / `:251` | 否 | **保留** |
| `integrity._moov_bytes` / `_iter_boxes` / `_find` | `:82` / `:50` / `:69` | 否 | **保留** |
| `core/batch_hw.py:MAX_HW_DECODE_FALLBACKS` | `:197` | 否 | **保留**（新模型沿用它作为硬上限，§9.6） |

**D. 测试引用**

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/selftest/suites/hardware_audio.py:323` | `V080_BASELINE` 集合**包含** `"--hw-decode"` | **必须修改**（从基线集合移除） |
| `tests/selftest/suites/hardware_audio.py:531`、`534` | 真实调用 `--hw-decode auto`（**selftest 中唯一的硬解行为测试**） | **必须重写**为新的解码策略入口 |
| `tests/selftest/suites/hardware_audio.py:535-539` | 断言 `"-> HARDWARE" in text_def and "-> HARDWARE" in text_plan` | **重写**断言（新日志格式，§8.2-R2） |
| `tests/hwdecode/harness.py:3272` | `--encoder nvenc --hw-decode auto` | **重写**（HD-H01） |
| `tests/hwdecode/harness.py:3310`、`3313`、`3324` | `--hw-decode auto [--no-downgrade]` | **重写**（HD-H02） |
| `tests/hwdecode/harness.py:3347` | `--encoder nvenc --hw-decode auto` | **重写**（HD-H03） |
| `tests/hwdecode/matrix.json` + `integration-test-matrix.md` | HD-B01..B10、HD-H01..H03 的 `expected`/`verification` 文本提到该开关 | **必须同步**（`python -m tests.hwdecode.harness check-matrix` 会因 drift 报错） |

**E. 文档引用（需同步修改，按影响排序）**

| 文件 | 行/处数 | 处置 |
|---|---|---|
| `docs/design/architecture.md` | 78、1358、1385、1394、1565-1573、1592、1614、1629-1630、1638、1703、1713、1793、1843、1848 | §8 整节重写；§2.1 表格第 3 行重写 |
| `README.md` | 50、101、107、148、417-452 | §"Hardware Decode" 整节重写 |
| `docs/hardware-decode/README.md` | 75 | 边界声明重写 |
| `docs/hardware-decode/final-report.md` | 77、211、485、491、654、673、694、737、768 | **历史报告，不改语义**；加一句"该开关已于 v0.9.0 移除，见 docs/plans/…" |
| `docs/hardware-decode/integration-test-matrix.md` | 多处 | 同上（历史档案） |
| `docs/hardware-decode/xavc-hs-frame-count.md` | 62、401、428-429、479 | 同上 |
| `docs/FINAL_REPORT.md` | 13 | 重写 |
| `docs/release_notes_next.md` | 89、115、124 | 重写（该文件是"下一周期"说明） |
| `docs/release_notes_v0.7.1.md` | 2 处 | **历史发布说明，不改** |
| `docs/release_notes_v0.6.1.md` | 1 处 | **不改** |
| `docs/audio_plan.md` | 2 处 | 重写相关段落 |
| `docs/design/implementation_report.md` | 2 处 | 重写 |
| `docs/design/hevc_422_rext_compatibility.md` | 2 处 | 检查后重写 |
| `docs/evaluation/hevc_implementation_assessment.md` | 2 处 | 同上 |
| `docs/x265-profile-design.md` | 1 处 | 同上 |
| `docs/README.md` | 1 处 | 索引更新 |
| `docs/reference/**` | 各 2 处 | **上游工具参考文档，不改** |

**F. BAT / PowerShell / 打包 / 外部依赖**

| 文件 | 是否引用 | 结论 |
|---|---|---|
| `start.bat` | ❌ 只透传 `--encoder --preset --check --jobs --interval`（`:32`） | **无影响** |
| `watchfolder.py` | ❌ 只透传 `--encoder --preset --check --jobs --headless --experimental-multihw --dry-run`（`:56-64`） | **无影响** |
| `release/build_release.py` | ❌ grep `hw-decode` → 0 命中 | **无影响** |
| `release/verify_package.py` | ❌ 同上 | **无影响** |
| `failed_files.json`（`--retry-list`） | ❌ 只存 source 路径 | **无影响** |

> **✅ CONFIRMED：`--hw-decode` 无外部调用依赖**（无 BAT/PS1/watchfolder/打包管线引用）。
> **唯一的外部依赖是操作者习惯与既有文档。**

**G. `OPEN DECISION`：`require` 语义的替代品**

移除 `--hw-decode` 会丢掉一个**真实能力**：`require` = "必须硬解，不可用就报错，
绝不静默降级"（`encoders/hwdecode.py:26-28`）。按 §9 的新策略：

| 场景 | 新策略下的硬解 | 是否保留"强制"语义？ |
|---|---|---|
| Sony 普通模式 | 软解（策略规定） | 无法强制硬解 → **能力丢失** |
| 非 Sony 普通模式 | 硬解优先，不可用则软解 | 无法要求"不可用就失败" → **能力丢失** |
| `--no-metadata` | 全部硬解优先 | 同上 |
| `--experimental-multihw` | 无可用硬件**编码**后端即退出 | **部分覆盖**（但那是编码侧，不是解码侧） |

**候选**：
* **A**：接受能力丢失。理由：发布安装里 rigaya 硬解本就不可用，
  `require` 的实际使用面很窄；且 prompt 明确要求移除该开关，
  未提及要保留 require。
* **B**：新增一个**独立**的强制开关（例如 `--require-hw-decode`）。
  **风险**：prompt §9 明文"本次不增加一套新的兼容开关体系"——
  一个单一的新开关是否属于"体系"需要判断。
* **C**：把"强制硬解"挂到 `--no-metadata` / `--experimental-multihw` 上
  （即这两个模式下如果硬解不可用就报错）。
  **风险**：改变了这两个模式的语义（它们应当是"优先"，不是"必须"）。

**本文不替维护者选择。建议 A**（最小改动、与 prompt 一致），
但必须**在用户文档里明确写出"强制硬解能力已移除"**，不能静默消失。

## 15.3 `--hw-decode-verify`

| 项 | 内容 |
|---|---|
| **parser 定义** | `1kt.py:1334-1342`，`action="store_true"` |
| **动作** | **移除** |

### 完整移除范围

| 类别 | 位置 | 处置 |
|---|---|---|
| Parser | `1kt.py:1334-1342` | 删除整块 |
| **内部调用点** | **零** —— `args.hw_decode_verify` 在全仓生产代码中**从未被读取**（§6.4 CONFIRMED） | — |
| 文档 | `README.md:107`、`434`；`docs/design/architecture.md:1592`；`docs/hardware-decode/final-report.md:485`、`694`；`docs/hardware-decode/xavc-hs-frame-count.md:429`、`479` | 修改/加历史注记 |
| 实现 docstring | `encoders/integrity.py:15`、`:411` | 重写（把"opt-in via --hw-decode-verify"改为"diagnostic; not wired to any CLI"） |
| 测试 | `tests/selftest/suites/hardware_audio.py:323`（`V080_BASELINE`） | **必须修改** |
| 测试（行为） | **无** —— 没有任何测试通过 CLI 触发过 sequence gate（§6.4） | — |
| 底层实现 | `integrity.verify_sequence`（`:396`）、`frame_signatures`（`:496`）、`digest`（`:536`） | **保留为显式诊断工具**（prompt 明文允许）。它仍被 `tests/hwdecode/harness.py` 调用（HD-C10/C12），**不得删除** |

> **★ 明确记录（CONFIRMED）**：移除这个开关**不删任何已交付能力**，
> 因为它**从未生效**。help 文本承诺的逐帧指纹比对需通过
> `python -m tests.hwdecode.harness run HD-C12` 才能触发。
> **这一事实必须在 release notes 中写明**，否则会被误读为"删功能"。

## 15.4 `--no-hw-autoselect`

| 项 | 内容 |
|---|---|
| **parser 定义** | `1kt.py:1343-1351`，`action="store_true"` |
| **动作** | **移除** |

### 完整移除范围

| 类别 | 位置 | 处置 |
|---|---|---|
| Parser | `1kt.py:1343-1351` | 删除 |
| 读取点 | `1kt.py:1459-1461`（`if args.no_hw_autoselect: ... return HW_AUTOSELECT_FALLBACK`）、`1kt.py:1451`（docstring） | 删除分支；`resolve_default_backend` 简化为"探测 → 优先级 → x265" |
| 常量 | `1kt.py:1402` `HW_AUTOSELECT_FALLBACK = "x265"` | **保留**（仍用作兜底） |
| 测试（行为） | `tests/selftest/suites/cli.py:70-74`：<br>`no_hw_autoselect = True` → `record("v062.--no-hw-autoselect 固定 x265", got == "x265", ...)`<br>`record("v062.--no-hw-autoselect 不探测硬件", any("disabled" in m ...))` | **必须删除或改写**（依赖 duck-typed `_Ns` 命名空间属性 → 会 `AttributeError`） |
| 测试（CLI 白名单） | `tests/selftest/suites/hardware_audio.py:324`（`V080_BASELINE`） | **必须修改** |
| 测试（真实调用） | `tests/selftest/suites/hardware_audio.py:567`<br>`extra = ("--no-hw-autoselect",) if encoder in ("x265","svtav1") else ()` | **必须修改**（改为显式 `--encoder x265` 已足够） |
| 测试（真实调用） | `tests/selftest/suites/production_output.py:482`、`576`、`716`、`822` | **必须修改**（4 处；它们同时给 `--encoder x265`，见下） |
| 测试（真实调用） | `tests/selftest/suites/audio_external.py:540`、`tests/selftest/suites/audio_format.py:816` | **必须修改**（同时给 `--encoder x265`） |
| 文档 | `README.md:50`；`docs/design/architecture.md:68`；`docs/FINAL_REPORT.md:13` | 重写 |

> **✅ CONFIRMED：`--no-hw-autoselect` 的 6 个真实调用点全部同时给出了 `--encoder x265`**
> （例如 `production_output.py:822`：`"--encoder", "x265", "--no-hw-autoselect"`）。
> 因此这些测试的**行为意图**（强制走 x265）在移除后**由 `--encoder x265` 完全承接**
> —— **移除是安全的**，只需删除多余参数。
> **唯一需要重写而非删除的是 `cli.py:70-74`**，因为它是**对 `--no-hw-autoselect`
> 本身的行为断言**。

## 15.5 `--preset all`

> **注意**：这不是一个独立开关，而是 `--preset` 的一个**取值**。

| 项 | 内容 |
|---|---|
| **parser 定义** | `1kt.py:1139-1145`：`choices=[p.lower() for p in PRESETS] + ["all"]`，`default="hq"` |
| **动作** | **从 choices 中移除 `"all"`**（`--preset` 本身保留） |

### 完整移除范围

| 类别 | 位置 | 处置 |
|---|---|---|
| Parser choices | `1kt.py:1142` `+ ["all"]` | 删除 |
| help | `1kt.py:1144` `"UHQ/HQ/SMALL/FAST/all (case-insensitive). Default: HQ."` | 改为 `"UHQ/HQ/SMALL/FAST (case-insensitive). Default: HQ."` |
| **展开逻辑** | `1kt.py:1967-1972`<br>`requested = list(PRESETS) if args.preset == "all" else [args.preset.upper()]`<br>`multiple_presets = len(requested) > 1` | 简化为 `requested = [args.preset.upper()]`；`multiple_presets = False` |
| **下游依赖 `multiple_presets`** | `1kt.py:2045` → `output_path_for(..., multiple_presets)`<br>`1kt.py:2193` → `BatchCtx(multiple_presets=multiple_presets)`<br>`core/paths.py:76`（参数）、`:92`（`if multiple_presets:` 决定输出是否插一层 `<PRESET>/` 目录）<br>`core/batch_hw.py:2068`（字段）、`:2111`（`ctx.preset, ctx.multiple_presets`） | **两种处置**：<br>**(a)** 保留 `multiple_presets` 参数与逻辑（恒 `False`）→ 最小改动、便于将来恢复<br>**(b)** 彻底删除该参数并简化 `paths.py:92` 的分支<br>**PROPOSED 选 (a)**（低风险，且 `paths.py` 的输出布局逻辑是**续跑兼容性**的一部分，见 §21） |
| **多档位外层循环** | `1kt.py:2036 for preset in requested:` | **保留**（单档位时循环一次，行为等价） |
| 测试 | **全仓无任何测试引用 `--preset all`（CONFIRMED）**。`tests/selftest/suites/cli.py:34` 的 `("ALL","all")` 是**大小写测试**，输入是值 `ALL`（不是 CLI 组合），断言 `args.preset == "all"` | **⚠️ 该用例会失败**：移除 `"all"` choice 后 `parse_args()` 对 `--preset ALL` 抛 `SystemExit`。**必须修改** `cli.py:33-39` 的用例表 |
| 文档 | `README.md:250`（`--preset all` 示例）、`docs/design/architecture.md`（若提及） | 修改 |
| `watchfolder.py` | ❌ `ap.add_argument("--preset", default="hq")` **无 choices 限制**（`:36`） | **无影响**（但会把 `all` 透传给 `1kt.py` → 届时被 argparse 拒绝，这是正确行为） |
| `start.bat` | ❌ `PRESET=hq` | **无影响** |

> **⚠️ 覆盖空白（CONFIRMED）**：`--preset all` 的**多档位 fan-out 行为没有任何测试**。
> 因此移除它**不会**让既有测试变红（除 `cli.py` 的 choices 用例），
> 但也**无法证明**移除后没有回归。§17 因此增加一条"单档位输出布局不变"的用例。

## 15.6 `--experimental-multihw`（**保留**）

prompt 明文："`--experimental-multihw` 本身继续保留。"
**处置：保留，但按 §12.2 把 `--encoder` 变成硬冲突。**

## 15.7 `--encoder`（**保留**）

prompt 明文："`--encoder` 继续保留，普通模式下用于显式固定编码器；
但与 `--experimental-multihw` 冲突。"
**处置：保留；新增与 multihw 的硬冲突检查（§12.2）。**

## 15.8 汇总表

| 开关 | 动作 | 生产代码调用点 | 测试改动 | 文档改动 | 外部依赖 |
|---|---|---|---|---|---|
| `--hw-decode` | **移除** | 24 处（`1kt.py` 1 + `batch_hw.py` 21 + docstring 2）+ 3 个模块 docstring | 8 处（2 文件）+ matrix.json/矩阵文档 | 9 个文件（历史报告不改语义） | **无** |
| `--hw-decode-verify` | **移除** | **0 处读取** | 1 处（白名单） | 6 个文件 | **无** |
| `--no-hw-autoselect` | **移除** | 2 处（`1kt.py:1451`、`1459-1461`） | 7 处（4 文件） | 3 个文件 | **无** |
| `--preset all` | **移除取值** | 2 处（`1kt.py:1969`、`1972`）+ `multiple_presets` 7 处 | 1 处（`cli.py:33-39`） | 2 个文件 | **无** |
| `--experimental-multihw` | **保留**（行为收紧） | — | 建议新增 | §12 相关 | `watchfolder.py:45,63-64` 透传 |
| `--encoder` | **保留** | — | 建议新增冲突用例 | — | `watchfolder.py:33,56`；`start.bat:13` |

### 15.8.1 移除的净效果

**移除前**（`1kt.py` 的 CLI 表面，`--version`/`--input`/`--output` 等必填项除外）：
`--preset`(5 取值) `--encoder` `--config` `--scaling-config` `--tool-nvencc`
`--tool-qsvencc` `--ffmpeg` `--ffprobe` `--gpac-dir` `--gyroflow` `--channel-sync`
`--channel-sync-transparent` `--channel-sync-tv` `--channel-sync-tv-policy`
`--channel-sync-tv-coordinate` `--audio-plan` `--check` `--jobs`
`--experimental-multihw` `--log-level` `--verbose` `--fresh-log` **`--hw-decode`**
**`--hw-decode-verify`** **`--no-hw-autoselect`** `--headless` `--retry-list`
`--keep-work` `--no-downgrade` `--dry-run`

**移除后**：减少 3 个开关 + 1 个取值；**新增 1 个**（`--no-metadata`）。
净减少 2 个开关。

> **prompt 明文**："不要删除仍被其他必要功能复用的底层实现。"
> §15.2-C 已逐符号审查；**没有任何底层实现被删除**
> （`verify_sequence`/`resolve_decode_tool`/`PROVEN`/`frame_signatures`
> 全部保留，只是不再挂在被移除的 CLI 表面上）。

---

# 16. `.1ktwork` 生命周期

> **prompt 明文**："每个 job 完成后立即清理"。
> "不得通过删除整个 `.1ktwork` 根目录来实现逐任务清理。
> 不得为了清理而破坏 resume、retry、preservation manifest
> 或音频处理的临时文件生命周期。"

## 16.1 现状（CONFIRMED，本 session 逐路径审计）

### A. 构造点（2 处）

```python
1kt.py:1494    work_root = output_root / ".1ktwork"     # transparent_main
1kt.py:1891    work_root = output_root / ".1ktwork"     # main
```

### B. `.1ktwork` 下的全部路径

| 相对路径 | 创建者 | 行 |
|---|---|---|
| **`caps/<tool>_caps.json`**、**`caps/<tool>_caps_raw.txt`** | `hw_backend_for()` → `caps.save()` | `core/batch_hw.py:357`、`:367`；`encoders/caps.py:46-58` |
| `<job>/video/encoded.{mov,mp4}` | 编码器（`encode_one_sony_hw` / `_dji_hw` / 软件路径） | `core/batch_hw.py:932-934`、`:1509-1511`；`1kt.py:723-725`、`:953-955` |
| `<job>/video/stripped.mov` | `strip_video_audio()` | `core/batch_hw.py:251-252` |
| `<job>/final/output.mov` | 保留管线重建 | `preservation/pipeline.py:112`；`preservation/dji.py:268` |
| `<job>/final/stage.mov` | Sony 重建中间体 | `preservation/pipeline.py:218` |
| `<job>/final/container-remux<suffix>` | `place_final()` | `core/batch_hw.py:1995` |
| `<job>/metadata/`（+`tracks/`、`boxes/`、`verify/`、`manifest.json`） | `SonyPreservationBackend.extract()` | `preservation/sony.py:186-188`、`:239`、`:374`、`:423-424` |
| `<job>/quality/`（+`quality_<stem>.json`、`quality_psnr.csv`） | `run_quality_sample()` | `preservation/quality.py:179`、`:207`、`:358` |
| `<job>/validate/`（+`rtmd_samples.bin`、`lens_profile.bin`、`meta.xml`、`original/`、`final/`） | `preservation/validate.py` | `:69`、`:86`、`:96`、`:101`、`:135`、`:137` |
| `<job>/channel_sync/` | `run_channel_sync()` | `core/channel_sync.py:567` |
| `<job>/channel_sync/channel_sync_<stem>.json` | `core/channel_sync.py:613` |
| `<job>/channel_sync/decoded_<i>.<dt>`、`fixed_<i>.<dt>` | `core/channel_sync.py:745-746`（**已在 `finally` 里 unlink**，`:1319-1326`） |
| `<job>/channel_sync/audio_<i>.mov` | `core/channel_sync.py:1213`（**刻意保留**，是返回值的一部分） |
| `<job>/channel_sync/audio_pcm/` | `pcm_tmp_dir()` | `core/audio_pcm.py:137-142` |
| `<job>/audio/` + `<stem>.audio<i>.<ext>` | `CopyAudioBackend.note()` / `_encode_group()` | `preservation/pipeline.py:213`；`production/output.py:928`、`:1053` |
| **`<job>/report.json`** | 保留管线 | `preservation/pipeline.py:391`；`preservation/dji.py:345` |

### C. 清理通路（CONFIRMED）

**全仓**只有 **一个** 清理函数、**两个** 调用点：

```text
core/batch_hw.py:268  cleanup_work_dir()
  · keep_work=True → 记日志后 return（不删）
  · report.json 存在 → copy2 到 preserve_reports/<job_id>.json
     （失败 → 只写 per-file 日志的 WARNING，**然后照样 rmtree**，报告永久丢失）
  · shutil.rmtree(work_dir)，3 次尝试，每次间隔 1.0 s
  · 3 次全失败 → emit_warning("GC failed, work dir retained") 后 return（不失败该 job）

调用点（仅 2 处，均紧邻 return "done"）：
  core/batch_hw.py:1140    encode_one_sony_hw
  core/batch_hw.py:1741    encode_one_dji_hw
```

**全仓 `shutil.rmtree` 的生产代码命中（CONFIRMED）**：

| 位置 | 目标 |
|---|---|
| `core/batch_hw.py:296` | **`work_dir`**（= `work_root/<job_id>`）—— 唯一的清理 |
| `core/audio_pcm.py:81` | `work_dir/"audio_pcm"`（`cleanup_tmp()`）—— **全仓零调用点** |
| `release/verify_package.py:203` | `extract_root`（发布工具） |
| `release/build_release.py:320` | `stage`（发布工具） |

**整个 `.1ktwork` 根目录从未被删除（CONFIRMED）** —— 全仓 grep
`rmtree(work_root)` / `rmtree(output_root / ".1ktwork")` → **0 命中**。
→ **`caps/` 永久累积**（每次运行每后端一对文件）。

### D. **泄漏清单（CONFIRMED，本 session 审计的核心发现）**

| # | 路径 | 成功时 | 失败时 |
|---|---|---|---|
| 1 | **`encode_one_hw_classic`**（`batch_hw.py:1152-1375`） | **泄漏**（函数内无 `cleanup_work_dir`，且签名**没有** `preserve_reports` / `keep_work` 参数，`BatchCtx` 分派（`:2246-2278`）也不传） | **泄漏** |
| 2 | **`_finish_with_handoff`**（`batch_hw.py:1378-1460`） | **泄漏**（4 个出口全泄漏） | **泄漏** |
| 3 | `encode_one_sony_hw` | ✅ 清理（`:1140`） | ❌ **泄漏**（`:1059`、`:1076`、`:1087`、`:1105` 四个 failure return） |
| 4 | `encode_one_dji_hw` | ✅ 清理（`:1741`） | ❌ **泄漏**（`:1678`、`:1691`、`:1706`） |
| 5 | `encode_one_sony`（软件，`1kt.py:686`） | **泄漏** | **泄漏** |
| 6 | `encode_one_dji_x265`（软件，`1kt.py:913`） | **泄漏** | **泄漏** |
| 7 | `encode_one`（软件经典，`1kt.py:481`） | **泄漏**（`quality/`、`audio/`、`channel_sync/`、`final/`） | **泄漏** |
| 8 | `transparent_main`（`1kt.py:1473-1660`） | **泄漏**（`work_root/<job>/channel_sync/`） | **泄漏** |
| 9 | **`<output_root>/.1ktwork/caps/`** | **永不删除** | **永不删除** |

**中断（Ctrl-C）泄漏（CONFIRMED）**：

| 事实 | 证据 |
|---|---|
| **全仓无 `atexit`** | grep `atexit` → 0 命中 |
| **全仓无 `signal.signal`** | grep `signal.` → 只有 SciPy DSP 调用 |
| **`run_hw_pool` / `run_multihw_pool` 无 try/except/finally** | `core/batch_hw.py:2352-2400`、`:2403-2463` 已全读；`core/batch_hw.py` 的**唯一** `finally` 在 `:1901`（`remux_container_lossless` 内恢复 GPAC timescale） |
| 仅有的两个 `except KeyboardInterrupt` 都**只杀子进程、不删文件** | `1kt.py:183`（`run_ffmpeg`）、`encoders/hw.py:218`（`run_hw_tool`） |
| `main()` **没有** `try` 包裹 `run_hw_pool(...)` | `1kt.py:2223-2229` |

### E. `--keep-work` 的实际作用域（CONFIRMED）

| 事实 | 证据 |
|---|---|
| 定义 `1kt.py:1365-1368`，help："成功后保留 .1ktwork 工作目录 (默认删除)" | — |
| 只在 `cleanup_work_dir` 的 `:278-280` 生效 | grep `keep_work` → 12 处生产命中，实际生效仅此 1 处 |
| → **在经典硬件路径、三条经典软件路径、`encode_one`、`transparent_main` 上完全无效** | 那些路径既不接收也不调用它 |
| → help 文本的"(默认删除)"在这些路径上是**假的** | — |

### F. 临时文件落在 `.1ktwork` 之外

| 路径 | 创建者 | 清理 |
|---|---|---|
| `<dst.stem>.part.mov` / `.part.mp4`（**输出树内**） | `1kt.py:2051-2052`；`core/batch_hw.py:1196-1199` | 失败时 `safe_unlink`；**成功且需要跨容器重封装时保留**（`tests/selftest/suites/hardware.py:748-752` 把它断言为**预期行为**） |
| `<part>.synced.mov` | `core/batch_hw.py:1321`；`1kt.py:633` | **无清理**（异常路径遗留） |
| `<part>.ffmpeg.log` / `<part>.nvencc.log` / `<part>.qsvencc.log` | `1kt.py:540`、`:747`、`:988`；`core/batch_hw.py:515` | **硬件路径不清理**（只在下一次运行开始时 `unlink` 旧的，`:516-520`） |
| `%TEMP%\1kt_meta_*` / `1kt_metav_*` | `preservation/gpac.py:609`、`:676`（`tempfile.TemporaryDirectory`） | ✅ **唯一有保证的清理**（context manager，含 BaseException） |
| `<job>/channel_sync/<target>.<rand>.part` | `core/audio_sync_tv_exec.py:1128-1130` | ✅ `os.replace` 或 `unlink(missing_ok=True)`，含 `except BaseException`（`:1189-1191`） |
| `%TEMP%\audio_pcm` | `core/audio_pcm.py:139`（`base is None` 时） | **永不清理** |
| `<output_dir>/quality/`（`encode_one` 无 `work_dir` 时） | `1kt.py:571` | **无清理** |

### G. 文档与代码的偏差（CONFIRMED）

| 文档声称 | 代码事实 |
|---|---|
| `docs/design/hardware_backend_design.md:373`："交付成功后 GC `.1ktwork` … 失败任务保留" | **只对 Sony/DJI 成立**；经典硬件路径**成功也不 GC** |
| `docs/design/implementation_report.md:23`："全成功后 `.1ktwork` 仅剩 `caps/`" | 对**全 Sony/DJI 批次**成立；含任意非 Sony/非 DJI 文件时**不成立** |
| `docs/design/implementation_report.md:79`：caps 缓存"键 = 工具版本+驱动+设备" | **代码中不存在缓存读取**（§3.3-G2） |
| `docs/release_notes_v0.6.1.md:151`："`--channel-sync-transparent` 成功路径保留 `.1ktwork/`" | 成立，且实际上是**永远保留**（无清理通路） |

## 16.2 PROPOSED 设计：逐 job 结构化清理

### A. 核心原则

```text
1. 清理的**单位是 job**，不是批次。
2. 清理的**触发点是 job 生命周期结束**（成功 / 失败 / dry-run / 中断），
   不是"批次结束"。
3. **绝不删除 `.1ktwork` 根目录**（prompt 明文禁止）。
   根目录下可能有：共享 caps 缓存、**仍在运行**的其它 job。
4. 清理前必须完成**持久化**（报告/日志/诊断落盘）。
5. `--keep-work` 是**唯一**的抑制开关，且必须在**所有**路径上生效。
```

### B. PROPOSED 实现形状

```python
# 新增（位置待定，建议 core/paths.py 或新的 core/workdir.py）
@contextlib.contextmanager
def job_workdir(*, work_root: Path, job_id: str, keep: bool,
                preserve_reports: Path | None,
                logger, file_logger,
                retain_on_failure: bool = False):
    """One job's scratch directory, with a guaranteed end-of-job decision.

    Guarantees:
      * the directory is removed exactly once, on EVERY exit path
        (return, exception, KeyboardInterrupt, dry-run);
      * `report.json` is persisted to preserve_reports BEFORE removal;
      * `.1ktwork` itself is NEVER touched;
      * shared state (caps/) is NEVER touched;
      * `keep` suppresses removal (and is reported).
    """
```

**为什么用 context manager（PROPOSED 理由）**：

* prompt 要求覆盖 **9 种情形**：成功 / 编码失败 / 能力探测失败 /
  preservation 失败 / 输出验证失败 / retry / dry-run / 用户中断 / `--keep-work`。
  这 9 种在今天的代码里散落在 **30+ 个 `return` 语句**上
  （`encode_one_sony_hw` 就有 6 个 return）。**逐个加清理调用必然会漏**。
* `with` 块对 `BaseException` 同样生效 → **中断也被覆盖**（今天完全没有覆盖）。
* 与既有风格一致：`preservation/gpac.py:609` 已用
  `tempfile.TemporaryDirectory`；`core/audio_sync_tv_exec.py:1189-1191`
  已有 `except BaseException` 的正确范式。

### C. 每个情形的行为（PROPOSED，逐条对应 prompt 的 9 项）

| 情形 | 行为 | 依据 |
|---|---|---|
| **成功** | 持久化 `report.json` → 删除 job 目录 | prompt 主目标 |
| **编码失败** | 持久化失败报告（若有）→ 删除 job 目录 + 保留 `logs_root/failed_details/<job_id>_<stamp>.txt`（已有，`batch_hw.py:834-837`） | 诊断信息已在 `.1ktwork` 之外 |
| **能力探测失败** | 探测阶段的失败发生在 job 目录创建之前 → 无目录可删；**但**必须确保**不创建**空目录 | 现状：`work_dir` 只在需要时被真正的写入操作创建（`strip_video_audio` 的 mkdir、`run_quality_sample` 的 mkdir） |
| **preservation 失败** | 删除 job 目录；`report.json`（若已写出，含 `structural_success=false`）持久化 | `preservation/pipeline.py:390-394` 只在最后写报告 → 中途失败无报告，**这是既有限制**，`OPEN DECISION` 见 §16.4 |
| **输出验证失败** | 同"编码失败" | — |
| **retry** | **retry 会重建** job 目录。删除是安全的 —— 但见 §16.4 的 resume 冲突 | — |
| **dry-run** | **不创建目录**（今天已经如此：三个 dry-run 早退都在任何 mkdir 之前） | `batch_hw.py:960-966`、`:1543-1550`、`:1218-1228` |
| **用户中断** | `with` 的退出路径 → 删除（除非 `--keep-work`） | 今天**完全无覆盖** |
| **`--keep-work`** | 抑制删除，并在 run log + per-file log 记录 `KEEP-WORK | <path>` | 今天只在 Sony/DJI 生效 |

### D. **禁止事项**（prompt 明文，写成实施约束）

```text
✗ 删除 .1ktwork 根目录
✗ 删除 .1ktwork/caps/（共享能力缓存）
✗ 删除任何**不是本 job 的** `<job_id>` 目录
✗ 在报告/诊断持久化之前删除
✗ 破坏 resume（见 §16.4）
✗ 破坏 preservation manifest 的生命周期（见 §16.4）
✗ 破坏音频处理的临时文件生命周期（见 §16.4）
✗ 在并发场景下让一个 job 的清理影响另一个 job
```

### E. 共享资源与并发的具体约束（PROPOSED）

| 资源 | 生命周期 | 谁可以删 |
|---|---|---|
| `work_root/caps/` | **进程级**（理想情况下跨进程也可复用，§3.3-G2） | **无人删除**（除非显式 `--clear-caps`，本文不提议新增） |
| `work_root/<job_id>/` | **job 级** | 只有该 job 自己的 `with` 块 |
| `work_root/<job_id>/channel_sync/` | job 级 | 同上（`channel_sync` 内部已自管 `decoded_*`/`fixed_*`，`:1319-1326`） |
| `<output_root>/<...>.part.mov` | job 级但**在输出树** | 该 job 自己（失败时 `safe_unlink`；成功时按既有语义保留或改名） |
| `preserve_reports/<job_id>.json` | **跨批次持久** | **谁都不删** |
| `logs_root/**` | 跨批次持久 | 谁都不删 |

> **并发安全性（PROPOSED 要求）**：
> `job_id_for(src)` 是 `sha1(str(src))[:8]` + sanitised stem（`core/paths.py:121-124`），
> **同一源在同一路径下 job_id 稳定且唯一**。因此两个并发 job 不会撞同一个目录
> **除非同一源文件被重复提交**（`--retry-list` 与 `discover_sources` 不重叠，
> 且 `discover_sources` 是 `sorted(set)` 语义 → 不会重复）。
> **PROPOSED 加固**：在多硬件/多设备模式下，若两个设备被分到同一 job_id
> （理论上不可能，但**必须断言**），清理会互相破坏 →
> **新增一条断言**：`job_id → device` 的映射在同一波内必须唯一。

## 16.3 `caps/` 与"共享 capability cache"（prompt 明文要求考虑）

prompt §11 明确提到"共享 capability cache"。现状与 PROPOSED：

| 项 | 今天 | PROPOSED |
|---|---|---|
| 缓存内容 | 只写不读（§3.3-G2） | **实现读取**（§3.3），键 = `(tool, tool_version, driver, device)` |
| 位置 | `work_root/caps/` | **保持** |
| 生命周期 | 永久累积 | **永久保留**（跨批次有效的机器事实；且 §13 的多设备会需要 2N 份） |
| 失效条件 | 无 | ① 工具 `--version` 变化；② driver 变化；③ device 集合变化；④ 显式 `--refresh-caps`（**OPEN DECISION**：是否需要） |
| 并发写 | 多 job 同时 `caps.save()`（同一批次的多个 job 不会写 caps；caps 在启动时写一次） | **保持不变**：caps 只在启动期由 `hw_backend_for` 写 |
| 清理 | — | **明确禁止**被 job 级清理触碰 |

> **⚠️ 一个必须记录的现状细节（CONFIRMED）**：caps 写入发生在
> `hw_backend_for()`（`core/batch_hw.py:367`），即**启动期**，且**每个后端一次**。
> 在 multihw 下 `hw_backend_for` 被调用**两次**（`1kt.py:1923-1924`），
> 因此写 2 对文件。**PROPOSED**：统一到 `CapsSnapshot` 的持久化，
> 路径仍为 `work_root/caps/`。

## 16.4 **冲突与 `OPEN DECISION`（必须由人决定）**

### 冲突 1：resume 需要中间体，清理会删掉它（**最严重**）

**CONFIRMED**：DJI 路径**刻意**跨运行复用中间体：

```python
core/batch_hw.py:1583-1599   读取 work_dir/final/output.mov + report.json 判断续跑
core/batch_hw.py:1604        if not _encoded_ok(ffprobe, encoded_mov, expected=want):
core/batch_hw.py:1610        ... raise RuntimeError("video intermediate unreadable or incomplete")
```

Sony 路径同理（`preservation/pipeline.py:114-129`、`:137-149`、`:198-209`）。

**而今天的 DJI 成功路径会 `cleanup_work_dir`（`:1741`）→ 删掉这些中间体**。
即：**DJI 的 resume 能力在成功路径上是失效的**（成功后无中间体可复用），
只在**失败**时才有中间体——但失败路径**恰恰不清理**（泄漏）。
**这是一个自相矛盾的现状（CONFIRMED）**。

| 选项 | 内容 | 评估 |
|---|---|---|
| **A** | 逐 job 清理**无条件**执行；**接受 resume 只在"失败未清理"这一窗口内有效**，并把 resume 明确降级为"崩溃恢复"而非"重跑加速" | 与 prompt 的"立即清理"最一致；**代价**：`--retry-list` 重跑会全量重编码 |
| **B** | 成功后清理，但**保留**一个可续跑的**最小集**（`report.json` + 一个"已完成"标记），使 `dst` 已存在时由 `process_file_hw` 的 skip 分支（`batch_hw.py:2120`）生效 | 实际上 **A 已经实现这个效果**：成功的产物在 `dst`，重跑会 skip。所以 B 与 A 无差别 |
| **C** | 新增 `--resume-work` 开关，显式请求保留中间体 | 与"不增加新兼容开关"的 prompt 精神冲突（但这是一个**新功能开关**，不是兼容开关） |

> **本文建议 A**，理由是 **`process_file_hw` 的 skip 分支已经提供了真正的
> "重跑加速"**（产物存在即跳过，`core/batch_hw.py:2120-2125`），
> 中间体复用只对"上次失败"的场景有价值 —— 而那种场景下**必须重编码**
> （`_encoded_ok` 的存在恰恰是为了不信任残留中间体）。
> **但这是建议，不是决定。** `OPEN DECISION`。

### 冲突 2：preservation manifest 的生命周期

**CONFIRMED**：`work_dir/metadata/manifest.json` 是 Sony 抽取的 bundle，
它使**续跑时跳过 GPAC demux**（`preservation/pipeline.py:137-149`）。

* 逐 job 清理会删掉它 → 下次运行重新抽取（`sony.extract()`，耗时但正确）。
* **PROPOSED**：接受的代价；manifest 是**中间产物**，不是报告。
  `report.json` 才是需要跨批次存在的（已有 `preserve_reports/` 通路）。

### 冲突 3：音频处理临时文件的生命周期

**CONFIRMED**：

| 路径 | 自管清理？ |
|---|---|
| `<job>/channel_sync/decoded_*`、`fixed_*` | ✅ `core/channel_sync.py:1319-1326` 的 `finally` 里 unlink |
| `<job>/channel_sync/audio_<i>.mov` | ❌ 刻意保留（是返回值） |
| `<job>/channel_sync/audio_pcm/` | ❌ 目录由调用方负责（`core/audio_pcm.py:245-247` 明确写了这一点） |
| `<job>/audio/<stem>.audio<i>.<ext>` | ❌ `production/output.py:1164-1168 _cleanup()` **只在失败时**调用（`:685`、`:701`、`:705`、`:715`），**成功时保留** |
| `<job>/quality/` | ❌ 无清理 |

> **PROPOSED 约束（prompt 明文）**："不得为了清理而破坏…音频处理的临时文件
> 生命周期。"→ 逐 job 清理**只删除整个 `<job>` 目录**，
> **不改变**上述任何模块内部的临时文件语义。
> 因此 `production/output.py::_cleanup()` 的"仅失败"行为**保持不变**，
> `channel_sync` 的 `finally` **保持不变**。
> **PROPOSED 的净效果是"把逐模块的零散清理统一收敛到 job 边界"**，
> 而不是重写它们。

### 冲突 4：`report.json` 的持久化时机

**CONFIRMED**：`preservation/pipeline.py:390-394` 在**最后一步**写 `report.json`。
中途失败（例如 `sony.reconstruct` 抛错）→ **无报告**。

**PROPOSED（可选，属独立改进）**：在关键步骤后**增量落盘**
（至少写 `{"structural_success": false, "stage": "...", "error": "..."}`），
使失败也有报告可持久化。
**这会改变 `pipeline.py:114-129` 的续跑判定**（它读 `structural_success`），
所以**必须小心**：增量写必须保持 `structural_success` 的既有语义
（只有真成功才写 `True`），否则会把失败运行误判为完成。
**§18-C 列出该改动为"建议但不属本次范围"。**

### 冲突 5：`preserve_reports` 的写入目录

**CONFIRMED**：`preserve_reports = logs_root / "preserve_reports"`
（`1kt.py:1831`），`logs_root = input_root.parent / "logs"`（`1kt.py:1822`）。
`preserve_reports` 目录**只在 `cleanup_work_dir` 内惰性创建**（`batch_hw.py:284`）
→ 全经典批次不会创建它。**PROPOSED 保持**（与续跑无关，只是懒创建）。

## 16.5 `--keep-work` 的 PROPOSED 统一语义

| 项 | PROPOSED |
|---|---|
| 语义 | "**所有** job 结束后保留其 `.1ktwork/<job_id>/`" |
| 生效范围 | **全部**路径（硬件三类 + 软件三类 + `encode_one` + `transparent_main`） |
| 日志 | 每个 job 一行 `KEEP-WORK | <path>`（今天只有 Sony/DJI 有） |
| help 文本 | 修正为与实现一致（见 §16.1-E：今天在多数路径上不成立） |
| 与 caps 的关系 | **无关**（caps 永远保留） |

## 16.6 清理失败的语义（PROPOSED）

**现状（CONFIRMED）**：`rmtree` 3 次失败 → `emit_warning("GC failed, work dir retained")`
→ **不影响 job 状态**（job 已返回 `"done"`）。

**PROPOSED 保持"不因清理失败而让 job 失败"**，但增加：

```text
· 清理失败必须出现在 **run log**（今天已如此，emit_warning 写主日志）
· 每个 job 的清理结果写入报告："work_dir_removed": true | false
· 批次结束时汇总一行："GC | removed=N retained=M"（M>0 时 WARNING）
· 报告复制失败（今天的 batch_hw.py:290-293）**必须升级**：
    今天"报告复制失败 → 仍然 rmtree → 报告永久丢失"是**数据丢失**
    PROPOSED：报告复制失败 → **保留** job 目录 + WARNING + 计入 retained
    （**理由**：prompt 要求"Required reports/logs are persisted"之后才清理）
```

> **★ 这一条是对 prompt 明文"Required reports/logs are persisted →
> Job A temporary directory is cleaned"的严格落实**：
> **持久化失败就不允许清理**。今天的顺序是"复制失败也照删"，与 prompt 相反。

## 16.7 诊断信息的归宿（PROPOSED）

会被逐 job 清理删掉的东西，必须**先**落到跨批次位置：

| 信息 | 今天的位置 | PROPOSED |
|---|---|---|
| preservation 报告 | `<job>/report.json` → `preserve_reports/<job_id>.json` | **保持** |
| per-file 日志 | `<logs>/files/<rel>/<stem>_<PRESET>.log`（**已在 `.1ktwork` 之外**，`core/paths.py:105-118`） | **保持** |
| 失败详情 | `<logs>/failed_details/<job_id>_<stamp>.txt`（`batch_hw.py:834-837`） | **保持** |
| 失败清单 | `<logs>/failed_files.json`（`1kt.py:1832`） | **保持** |
| 编码器原始日志 | `<job>/video/encoded.mov.<label>.log`（**硬件路径**）或 `<part>.<label>.log`（**经典路径，在输出树**） | **PROPOSED**：硬件路径的在清理前复制到 `<logs>/encode_logs/<job_id>.<label>.log`（**仅当 job 失败**，避免正常批次产生大量日志） |
| 能力探测原始输出 | `work_root/caps/<tool>_caps_raw.txt` | **保持**（永不清理） |
| 决策记录（三层） | 只有 `DECODE_ROUTE`（`batch_hw.py:475`） | **PROPOSED**：三层决策进 per-file log + `report.json`（§8.2-R2） |

---

# 17. 测试矩阵

> **prompt 明文**："测试应优先复用已有 fixture 和测试框架，不要创建第二套测试系统。"
> **prompt 明文**："本 session 只制定测试计划，不执行实际功能修改。"

## 17.1 现有测试系统（CONFIRMED，必须先读懂再规划）

**四个系统，无 pytest，无 CI。**

| 系统 | 入口 | 规模 | 适用 |
|---|---|---|---|
| **A. `tests/selftest/`** | `python tests/full_autotest.py --level {unit\|toolchain\|full\|all}`<br>（`tests/full_autotest.py` 是 `tests/selftest/runner/cli.py` 的兼容 shim；`python -m tests.selftest` **未接线**——没有 `__main__.py`） | `--level toolchain` = **651 PASS / 0 FAIL**（628 unit + 23 toolchain）；`--level full` = 859 | 单元 + 真实管线集成 |
| **B. `tests/hwdecode/`** | `python -m tests.hwdecode.harness {provenance\|check-matrix\|list\|run\|summary}` | `matrix.json` **83 用例**（P0 61 / P1 21 / P2 1），A–K 11 类 | 硬件解码矩阵（需真实 GPU + patched binary） |
| **C. `tests/audio_sync_*`** | 各文件的 `main()`（**standalone 脚本，不是 pytest**） | P4 侧 180+ cases | P4 音频（**本次不得触碰**，§22） |
| **D. `tests/run_selfcheck.py` / `sony_selfcheck.py`** | 直接驱动真实 `1kt.py` | — | 手工自检 |

**注册机制（CONFIRMED）**：
* A 的 suite 发现是 `tests/selftest/runner/engine.py:34-48 build_registry()` 里的
  **硬编码 dict**：`unit` = 31 项（`:51-90`）、`toolchain` = 2 项（`:91-94`）、
  `full` = 15 项（`:95-122`）。**新增 suite = 加 import + 加一个 tuple。**
* A 的记录 API：`tests/selftest/paths.py:54-73`
  `record(name: str, ok: bool, detail: str = "", level: str = "") -> None`。
  惯例：suite 内定义 `def expect(case, cond, detail): record(f"l3.{case}", cond, detail)`。
* B 的用例注册需要**四处同步**：`@impl("name")`（`harness.py:300-307`）+
  `matrix.json` 条目 + `integration-test-matrix.md` 的 `HD-Xnn` 行 +
  `PHASES`（`harness.py:44-51`）里已存在的类别字母。
  `python -m tests.hwdecode.harness check-matrix`（`harness.py:4131-4156`）
  **强制检查这三者的漂移**。

**三个必须知道的结构性约束（CONFIRMED）**：

| # | 约束 | 后果 |
|---|---|---|
| **T-1** | **`tests/selftest/suites/hardware_audio.py:315-332` 把 v0.8.0 的 CLI 参数集钉死**（`V080_BASELINE` / `P46_ADDED` 的命名增量规则） | **任何 CLI 参数增删都必须在同一改动里编辑该文件**，否则 `--level unit` 立刻红 |
| **T-2** | **没有"缺 GPU 就 skip"的机制**。全仓无 `pytest.mark.skipif` / `unittest.skipUnless` / "no GPU → SKIP" 分支（`skipif\|import pytest` 全仓 grep → **0 命中**） | 无 GPU 主机上 `--level toolchain`/`full` 会**变红而不是跳过**。新用例必须注册在 `full` 层，并接受这个约定 |
| **T-3** | **`tests/fixtures/channel_sync/a7m5_real_137_baseline.csv` 是 hash 钉死的冻结基线**（`baseline_identity.py:78-81` 的 `EXPECTED_CANONICAL_CONTENT_SHA256`；`EXPECTED_DATA_ROWS = 548`），且 `HD-G06` 用 `git diff --name-only main...HEAD -- core/channel_sync.py core/sync_estimate.py tests/fixtures/channel_sync/` 守护它 | **本次重构不得触碰 `core/channel_sync.py`、`core/sync_estimate.py`、`tests/fixtures/channel_sync/`**（§22） |

**已有的相关 fixture（CONFIRMED，应复用）**：

| fixture | 位置 | 内容 | 本次用途 |
|---|---|---|---|
| `_make_av()` | `tests/selftest/fixtures/audio.py:152-184` | `video(h264) + N mono PCM s16le`，1280×720 | 音频交互测试 |
| `_make_av_10bit()` | `tests/selftest/fixtures/audio.py:322-341` | **`testsrc2` + `libx265 -pix_fmt yuv420p10le` + mono PCM** | **硬件解码用例的现成素材** |
| `_video_elementary_hash()` | `tests/selftest/fixtures/audio.py:307-319` | 视频基本流 sha256（`-c copy -f hash -hash sha256 -`） | **"视频域未被改动"的机器证据 —— 直接复用** |
| `_stage_inputs()` | `tests/selftest/fixtures/media.py:18-68` | 真实 Sony `C9037.MP4`（4:2:2 10-bit）+ 真实 DJI + 截断 + 尾部垃圾注入 | 真实素材用例 |
| `GEN_A_COPY` / `GEN_B_MKV` / `GEN_C_X265` / `GEN_D_SYNTH` | `tests/hwdecode/fixtures.py:149-222` | Sony 流拷贝(MP4) / MKV / x265 重编码 / testsrc2，均 10-bit | 硬解保真用例的对照素材 |
| `leading_picture_count` / `leading_picture_scan` | `tests/hwdecode/probe.py:576-629` | **leading picture 计算（§9.4 的预测式）已有实现** | **直接复用**，不要重写 |
| `container_video_samples` | `encoders/integrity.py:111` | 自解 `stsz` 的帧数真值 | L1 计数检查 |

> **★ 结论**：**不需要新建任何测试系统**。
> 新增用例分别落在 A 的 `unit`/`full` 层与 B 的 matrix 里。
> **本节新增的"跨 substrate 保真度"用例最适合放 B**（它已有 fixture、
> 已有 probe、已有 fixture 生成器、已有漂移检查）。

## 17.2 A. 普通模式（prompt §12-A）

| # | 目标行为 | 层 | 素材 | 断言 | 复用 |
|---|---|---|---|---|---|
| A-1 | Sony：**软件解码** + 自动编码器 | `full` | 真实 `C9037.MP4` | 日志含 `DECODE_ROUTE ... SW`；编码后端 ∈ {nvenc,qsv,x265}；产物有效（L0）；帧数 == 源 | `fixtures/media.py::_stage_inputs` + `_video_elementary_hash` |
| A-2 | Sony：软件解码 + **显式 NVENC** | `full` | 同上 | `--encoder nvenc`；`DECODE_ROUTE` 仍为 SW；后端 == nvenc | 同上 |
| A-3 | Sony：软件解码 + **显式 QSV** | `full` | 同上 | 同上，后端 == qsv | 同上 |
| A-4 | Sony：软件解码 + **显式 x265** | `full` | 同上 | 同上，后端 == x265；**且不经过硬件路径** | 同上 |
| A-5 | 非 Sony：**硬件解码** + 自动编码器 | `full` | `_make_av_10bit()` 或 `GEN_D_SYNTH` | `DECODE_ROUTE ... HW (substrate=ffmpeg_hw)`；产物基本流 sha256 与软解路径**相同** | `_make_av_10bit` + `_video_elementary_hash` |
| A-6 | 非 Sony：硬件解码 + **显式 x265** | `full` | 同上 | **★ 核心用例**：`--encoder x265` **不关闭硬解**；`DECODE_ROUTE` 为 HW | 同上 |
| A-7 | **NVENC 不可用时选 QSV** | `unit` | 打桩探测结果 | `resolve_default_backend()` → `"qsv"` | `tests/selftest/suites/cli.py` 的 `_Ns` 模式（`:69-71`） |
| A-8 | **NVENC/QSV 均不可用时选 x265** | `unit` | 打桩 | → `"x265"` | 同上 |
| A-9 | **显式编码器不被自动选择覆盖** | `unit` | 对每个 `--encoder X` 调用 | 返回 == X；且 `resolve_default_backend` **未被调用** | 同上（**新增**，今天无此用例） |
| A-10 | 显式编码器 + 缺失工具 → 明确 FATAL（不是 traceback） | `unit` | 打桩 `find_hw_tool` 抛 `FileNotFoundError` | rc=2 + 可读消息 | §10.3-③ |
| A-11 | **单档位输出布局不变**（替代已移除的 `--preset all`） | `unit` | 直接调 `output_path_for(..., multiple_presets=False)` | 路径 == 既有布局（无 `<PRESET>/` 层） | `core/paths.py:71-102` |

> **A-7/A-8 的现状（CONFIRMED）**：`tests/selftest/suites/cli.py:60-63` 已有
> `v062.autoselect 顺序 = NVENC -> QSV` / `兜底 = x265` / `不含 AV1` 三个用例，
> 但它们断言的是**常量**（`AUTOSELECT_ORDER`），不是**打桩后的行为**。
> **A-7/A-8 是新增的行为级用例。**

## 17.3 B. `--no-metadata`（prompt §12-B）

| # | 目标行为 | 层 | 素材 | 断言 |
|---|---|---|---|---|
| B-1 | **Sony 使用硬件解码** | `full` | 真实 Sony `C9037.MP4` 或 `20260903_C1170.MP4` | `DECODE_ROUTE ... HW`；**且不进入 preservation 管线**（日志无 `[PRESERVE]`） |
| B-2 | **DJI 使用硬件解码** | `full` | 真实 DJI `DJI_20260830095031_0009_D.MP4` | `DECODE_ROUTE ... HW`；无 DJI 重建步骤 |
| B-3 | **其它素材使用硬件解码** | `full` | `_make_av_10bit()` | 同上 |
| B-4 | **显式 x265 编码仍保留硬件解码策略** | `full` | `_make_av_10bit()` | `--no-metadata --encoder x265` → `DECODE_ROUTE ... HW`（**§8 三层独立性的关键证明**） |
| B-5 | **metadata preservation 路径按设计跳过** | `full` | 真实 Sony | 日志**必须**出现明确的跳过记录（`META_POLICY \| no_metadata`）；**不得静默** |
| B-6 | **不再因硬解与软解帧数不同而自动拒绝该路径** | `unit` + `full` | 打桩一个"帧数 = 源 − 3"的产物 | 在 `rigaya_hw` + `--no-metadata` 下**不被拒绝**，但报告标注 `known leading-picture deficit: −3 (predicted)`（§9.4） |
| B-7 | **进程失败仍被正确识别** | `unit` | 打桩 rc != 0 | → `failed` + `failed_files.json` 有条目 |
| B-8 | **无效输出仍被正确识别** | `unit` | ① 产物不存在 ② 产物 0 字节 ③ 产物被截断（复用 `fixtures/media.py:53-59` 的截断注入） | 三种都 → `failed`；**绝不 `done`** |
| B-9 | **★ 音频流不丢失（§11.5-R1）** | `full` | 带 4CH PCM 的真实 Sony（`testsets/20260903/A7M5/*.MP4`） | 音轨数、编码参数、音频基本流 sha256 在 `--no-metadata` 与默认路径上**一致** |
| B-10 | **★ channel-sync 未被绕过（§11.5-R3）** | `full` | `_make_av()` 多流 PCM + channel-sync 选项 | `--no-metadata --channel-sync` 的同步结果与默认路径**一致**（决策 JSON 逐字节） |

> **B-9 / B-10 是本功能最重要的两条**，因为 §11.5 的两条风险都指向音频，
> 而 prompt §13 明文禁止"通过硬件编解码重构绕过 channel-sync"。

## 17.4 C. FFmpeg + x265（prompt §12-C）

| # | 目标行为 | 层 | 素材 | 断言 |
|---|---|---|---|---|
| C-1 | **NVDEC → libx265** | `full` | 10-bit 4:2:0 HEVC | rc=0；帧数 == 源；产物基本流 sha256 == 软解路径（**本 session 已验证 §5.3-C**） |
| C-2 | **QSV decode → libx265** | `full` | 10-bit 4:2:0 HEVC | 同上 |
| C-3 | **software decode → libx265** | `full` | 同上 | 基准 |
| C-4 | **不支持的像素格式** | `unit` | 打桩：`hwdownload` 拒绝 | 编码前即判定软解（**不构造硬解命令**）；日志含明确 reason |
| C-5 | **10-bit 4:2:0** | `full` | `_make_av_10bit()` / `20260903_C1170.MP4` | 帧数精确；**不降位深**（产物 `pix_fmt == yuv420p10le`） |
| C-6 | **10-bit 4:2:2** | `full` | 真实 `C9037.MP4`（4:2:2 10-bit） | NVDEC **接受**（§5.4）；QSV **拒绝** → 明确降级到软解或 NVDEC；**不降色度**（产物 `pix_fmt == yuv422p10le`） |
| C-7 | **HDR / colour metadata** | `full` | **需要构造**（本机语料无 HDR） | `color_primaries`/`transfer`/`space` 在输出中保留；**OPEN DECISION**：素材来源（§19） |
| C-8 | **设备初始化失败** | `unit` | 打桩 `-init_hw_device` 失败 | 该 (substrate,device) 进入 memo；本文件软解重跑**一次**；后续文件**不再尝试**同一设备（§9.6-A） |
| C-9 | **hardware frame transfer 失败** | `unit` | 打桩 `hwdownload` 在运行时失败 | 同上（§9.6-B） |
| C-10 | **编码输出验证** | `unit` | 打桩：产物存在但帧数不符 | L1 拒绝（`ffmpeg_hw` 下 L1 必做，§9.4） |
| C-11 | **★ 跨 substrate 位相同性（回归金标准）** | `full` | Sony XAVC HS + XAVC S + DJI 三类 | 三种解码路径的中间产物**逐字节相同**——**这是本规划书最强的回归断言**，且本 session 已证明它在 SONY XAVC HS 与 XAVC S 上成立（§5.3） |
| C-12 | `--seek` 下 `ffmpeg_hw` 与软解是否等价 | `full` | 真实 Sony | **UNKNOWN → 必须先测**（§9.7）。测出结论前，实现必须对 seek 走软解 |

> **C-11 是本次规划最重要的新增测试**：它把"硬解没有引入偏差"变成
> **一个可以逐字节判定的机器事实**，而不是"看起来没问题"。
> 它也直接替代了 §9.4 中被降级的"帧数一致"门槛 —— **帧数一致是弱断言，
> 位相同才是强断言**，而本 session 证明后者**是可达成的**。

## 17.5 D. `--experimental-multihw`（prompt §12-D）

| # | 目标行为 | 层 | 断言 |
|---|---|---|---|
| D-1 | **单 NVIDIA GPU** | `full` | 探测到 1 个可用后端 → 单后端池 + 既有 WARNING（`1kt.py:1917-1922`）；全部 job 成功；资源槽位归零 |
| D-2 | **多 NVIDIA GPU** | **本机不可测**（只有 1 张 NVIDIA，§13.2） | **必须标注为不可在本机验证**；需外部机器或明确记为 `UNKNOWN` |
| D-3 | **NVIDIA + Intel** | `full` | **本机可测**（RTX 5070 + Arc 140T）。断言：两个设备类各收到任务；每设备并发不超预算；**任务不串设备** |
| D-4 | **只有 Intel** | `unit`（打桩：NVIDIA 不可用） | QSV 单后端池；无 x265 回退 |
| D-5 | **没有可用硬件编码器** | `unit` | **rc=2 + 错误信息含 `no usable hardware encoder`**；**不回退 x265**（§12.3） |
| D-6 | **显式 `--encoder` 冲突** | `unit` | `--experimental-multihw --encoder {nvenc,qsv,x265}` 三者均 rc=2 + 消息指出冲突参数（§12.2） |
| D-7 | **GPU 资源不足** | `unit` | 打桩：vram 为 measured 且不足以容纳第 2 个 job → **不派发**第 2 个；**不杀**运行中的 job（§14.4-5） |
| D-8 | **任务失败后资源释放** | `unit` | 打桩：job 抛异常 → 该 device 的 `running` 计数**归零**（不泄漏） |
| D-9 | **并发任务互不影响** | `full` | N 个并发 job 的 reader 身份/日志文件互不共享（复用 `final-report.md` HD-I02 的方法） |
| D-10 | **`--jobs` 在 multihw 下生效**（§12.5-M1 修复） | `unit` + `full` | 记录实际并发峰值 ≤ 预算 |

> **D-2 与 D-3 的诚实边界（CONFIRMED）**：本机只有 1 张 NVIDIA
> （`docs/hardware-decode/final-report.md` §2 记录 RTX 5070 Laptop 8 GB + Intel Arc 140T）。
> **"多 NVIDIA"在本机不可验证**；prompt 明文"不得因为检测到多张 GPU
> 就假定已支持任意数量的设备" —— 本机连"检测到多张 NVIDIA"都不成立。
> **D-2 必须写成 `SKIP (no multi-NVIDIA host available)` 或由维护者提供机器**。

## 17.6 E. `.1ktwork`（prompt §12-E）

| # | 目标行为 | 层 | 断言 |
|---|---|---|---|
| E-1 | **成功后立即清理** | `full` | `--no-metadata` 跑一个非 Sony/DJI 文件（今天会泄漏，§16.1-D-1）→ `<job_id>/` **不存在**；`caps/` **存在** |
| E-2 | **失败后按策略清理** | `full` + `unit` | 注入编码失败（复用截断注入）→ `<job_id>/` 不存在；`failed_files.json` 有条目；**`preserve_reports/<job_id>.json` 不存在**（无报告）或存在（有报告） |
| E-3 | **`--keep-work`** | `full` | 同上但带 `--keep-work` → `<job_id>/` **存在**；日志含 `KEEP-WORK` |
| E-4 | **retry/resume** | `full` | ① 成功 → 重跑 → 走 `skipped` 分支（`batch_hw.py:2120`）；② `--retry-list` 重跑 → 全量重编码（§16.4 冲突 1 的选项 A 语义） |
| E-5 | **并发任务** | `full` | N 个并发 job → **每个** job 目录都被清理；**无一泄漏**；`caps/` 计数不变 |
| E-6 | **共享 cache** | `unit` + `full` | `caps/` 在任何路径下**都不被删除**；两次运行后 caps 文件数不增长（若实现缓存读取，§3.3-G2） |
| E-7 | **中断** | `full` | 在编码中途发 Ctrl-C → **该 job 目录被清理**（今天：全部泄漏，§16.1-D） |
| E-8 | **报告保存失败** | `unit` | 打桩 `copy2` 抛 `OSError` → **job 目录被保留** + WARNING + 计入 `retained`（§16.6，与今天"照删"相反） |
| E-9 | **`transparent_main` 也清理** | `full` | `--channel-sync-transparent` 跑完 → `.1ktwork/` 下无残留（§16.1-D-8） |
| E-10 | **`.1ktwork` 根目录从不被删除** | `unit`（静态检查）+ `full` | 静态：grep 断言无 `rmtree(work_root)`；运行时：批次结束后 `caps/` 仍在 |

## 17.7 执行顺序与既有回归

**PROPOSED 的执行顺序**（每阶段结束都必须跑既有回归）：

```powershell
# 0. 基线（改动前，记录为对照）
python tests\full_autotest.py --level unit
python tests\full_autotest.py --level toolchain

# 1. 每阶段增量
python tests\full_autotest.py --level full            # ~10-15 min

# 2. 硬件解码矩阵（改动会触及它，必须跑）
python -m tests.hwdecode.harness provenance           # 任何不符 = FAIL
python -m tests.hwdecode.harness check-matrix         # 文档/JSON/impl 漂移
python -m tests.hwdecode.harness run --phase 1        # A toolchain + B routing
python -m tests.hwdecode.harness run --phase 2        # C integrity + D temporal
python -m tests.hwdecode.harness run --phase 3        # E preservation + F fallback
python -m tests.hwdecode.harness run --phase 4        # G features + H resume
python -m tests.hwdecode.harness run --phase 5        # I concurrency
python -m tests.hwdecode.harness run --phase 6        # J corpus + K baseline
python -m tests.hwdecode.harness summary
```

**⚠️ 已知的既有失败（CONFIRMED，本 session 读取 `work/avhw_integration/results/summary.json`）**：
```text
total 83 / PASS 81 / FAIL 2 / BLOCKED 0 / SKIP 0
FAIL: HD-G02  "stream 1: decision 'untouched' != baseline 'fixed'"
FAIL: HD-G06  "channel-sync artefacts changed vs main: ['core/channel_sync.py']"
```
→ **基线不是全绿**。新 session **必须**先记录这两个 FAIL 为**既有状态**，
不得把它们算作本次重构引入的回归，**也不得**通过修改
`core/channel_sync.py` 或冻结基线去"修复"它们（§22 禁止）。

**既有回归基线（`README.md:469-474`，CONFIRMED）**：
```text
L1 unit           590 PASS / 0 FAIL
L3 --level full   859 PASS / 0 FAIL   (unit 590 + toolchain 16 + full 253)
```
**⚠️ 与本 session 观察到的 `toolchain` 报告不一致（CONFIRMED）**：
`work/autotest/autotest_report.json`（2026-10-11 19:37:25）记录
`{'PASS': 651, 'FAIL': 0}`。**数字已经漂移**（590/859 → 628/651）。
→ **新 session 必须重新建立自己的基线，不得沿用本文或 README 的数字。**

## 17.8 本机可测性矩阵（诚实边界）

| 需求 | 本机可测？ | 依据 |
|---|---|---|
| FFmpeg NVDEC（1 张 NVIDIA） | ✅ | §5 已测 |
| FFmpeg QSV（Intel Arc 140T） | ✅ | §5 已测 |
| 多 NVIDIA | ❌ | 只有 1 张 NVIDIA |
| NVIDIA + Intel（异构多设备） | ✅ | §13.2 |
| 只有 Intel（打桩禁用 NVIDIA） | ✅ | 打桩 |
| 无任何硬件编码器 | ✅ | 打桩 |
| HDR 素材 | ❌ **缺素材** | 本机语料无 HDR（归档 `xavc-hs-frame-count.md` §3.3 同样标记未验证） |
| 8-bit XAVC / All-I / 1080p / VFR | ❌ **缺素材** | 同上 |
| AV1 硬解 | ⚠️ `nvenc-av1` 在本机**无法创建编码器**（`hardware_audio.py:595-599`）；`av1_cuvid`/`av1_qsv` 解码器已注册但**未测** | — |
| 多 GPU 显存遥测 | ⚠️ 仅 NVIDIA 可采（`nvidia-smi`）；Intel iGPU 显存**无已知通路** | §14.2 |

---

# 18. 代码修改范围

> 本节按**文件**列出改动，并标注风险与归属。
> 所有改动都应在**新的实现分支**上进行（§23），**不得**直接改 `main`。

## 18.1 A 类：本次重构的核心（必须做）

| 文件 | 改动 | 规模估计 | 风险 |
|---|---|---|---|
| **`Core/paths.py`**（或新增 `core/workdir.py`） | 新增 `job_workdir()` context manager（§16.2-B） | +80 行 | 低 |
| **`core/batch_hw.py`** | ① 三个 `encode_one_*_hw` 接入 `job_workdir`；② `encode_one_hw_classic` / `_finish_with_handoff` 补上清理；③ 接上 `classify_reader_failure` 的结果（§10.3）；④ multihw 池接入 budget + 设备队列（§12.5）；⑤ **修复 H-1 死代码**（§6.5）—— 见下方 ⚠️ | **大**（-100/+400 行量级） | **高** |
| **`encoders/caps.py`** | ① 三态探测结果（`measured`/`unavailable`/`probe_failed`）；② FFmpeg 解码能力探测（§9.5）；③ **实现缓存读取**（§3.3-G2）；④ 捕获 `TimeoutExpired` | +250 行 | 中 |
| **`encoders/hwdecode.py`** | ① 策略枚举替换（`POLICY_*` → 新模型）；② `PROVEN`/`REFUSED` 扩充为 substrate 感知；③ memo 扩展到 device-init / transfer 失败（§9.6） | +150/-60 行 | 中 |
| **新增 `encoders/ffmpeg_hw.py`** | FFmpeg 解码 substrate 的**命令片段构造** + 兼容性判定 + 像素格式/传输链计算（§9.5、§5.2） | +300 行 | 中 |
| **`encoders/x265.py`** | `build_command()` / `build_video_command()` 接受一个**解码前缀**（`decode_args`），并追加 `-vf` 链 | +40 行 | **中高**（触碰交付命令） |
| **`1kt.py`** | ① 移除 4 个旧 switch（§15）；② 新增 `--no-metadata`；③ 三层策略对象构造与传递；④ `--jobs` 默认改 `auto` + 校验上限；⑤ 软件路径接入并发池（§14.6）；⑥ multihw × `--encoder` 硬冲突 | **大**（-80/+250 行） | **高** |
| **`core/batch_hw.py`（`BatchCtx`）** | 字段重构：`hw_decode`/`hw_decode_memo` → 新的解码策略 + `CapsSnapshot` + `ResourceBudget` | +30/-10 行 | 中 |
| **新增 `core/resources.py`** | `ResourceSample`（三态）+ 内存/显存采集（`ctypes` + `nvidia-smi`）+ `ResourceBudget` | +250 行 | 中 |

### ⚠️ 关于 H-1 死代码的归属（重要）

`core/batch_hw.py:1261-1284` 的死代码（§6.5）**位于本次必然要重写的函数内**。
两种处置：

| 选项 | 内容 |
|---|---|
| **A（建议）** | **本次一并修复**（把 `if` 拆出来），因为不修就无法在经典硬件路径上做"输出验证"（§9.4 的 L1）。**但这会让本次改动承担一个与 prompt 无关的缺陷修复**，需要在 commit 中单独成一条并说明。 |
| B | 本次**不动**它，另开一个缺陷单。**代价**：§9.4 的输出验证在经典硬件路径上无处安放。 |

**本文建议 A，但把它作为独立的 commit**，并在 commit message 里写明这是
**既有缺陷**（不是本次重构引入）。

## 18.2 B 类：配套（必须做，但范围小）

| 文件 | 改动 | 说明 |
|---|---|---|
| `core/config.py` | ① docstring 更新；② 若多 GPU 落地，`find_hw_tool` 需支持 per-device 工具解析 | — |
| `core/probe.py` | 若新增格式判定（HDR/colour 属性）需要更细的探测字段 | 谨慎：它是所有路径的入口 |
| `core/dashboard.py` / `dashboard_ui.py` | `meta` 增加 `decode_substrate` / `devices` / `jobs_budget`；rows 增加 `decode` 字段 | 低风险 |
| `core/versions.py` | `_gpu_drivers()` 的输出接入资源模型（device 枚举） | 低风险 |
| `preservation/pipeline.py` | ① `--no-metadata` 下的绕过入口（或由调用方决定不进入该函数）；② docstring | **谨慎** —— 保留管线是 P4 隔离区之外但高度敏感 |
| `preservation/checker.py` / `validate.py` | 若 §11.3 选 C，需要 `SKIPPED` 状态的报告语义 | `validate.py` 已在工作树中被修改 |
| `encoders/nvencc.py` / `qsvencc.py` | 若多 GPU 落地：`command()` 增加 device 参数 | **需先验证参数名**（§13.2） |
| `encoders/base.py` | 若新增解码前缀契约，`EncoderBackend` Protocol 需要扩展 | — |
| `watchfolder.py` | 透传 `--no-metadata`（可选） | 低风险 |
| `release/build_release.py` | 若新增测试素材或文档，检查白名单 | **不改变 `tools/avhw/` 的排除** |

## 18.3 C 类：建议但不属于本次范围（列出以免被顺手做掉）

| 项 | 位置 | 理由 |
|---|---|---|
| 增量落盘 preservation 报告 | `preservation/pipeline.py:390-394` | 会改变续跑判定语义（§16.4 冲突 4） |
| `--log-level debug` 的"逐阶段内存"日志 | `1kt.py:1301`（help 未实现） | 独立缺陷；且与 §14 的资源采集可能合并 |
| `audio_pcm.cleanup_tmp()` 的调用点 | `core/audio_pcm.py:77-81`（零调用） | 由 §16 的 job 级清理覆盖；不必单独接 |
| `%TEMP%\audio_pcm` 的清理 | `core/audio_pcm.py:139` | 独立缺陷 |
| `<part>.synced.mov` / `<part>.*.log` 的输出树残留 | `batch_hw.py:1321` 等 | 独立缺陷（属"输出树卫生"，与 `.1ktwork` 不同） |
| `encoders/hw.py::ask_fallback_new_console` 死代码 | `encoders/hw.py:254`（零调用） | 独立清理 |
| `_SKIPPED_ALWAYS` 未被引用 | `nvencc.py:93-96`、`qsvencc.py:91` | 独立清理 |
| `BatchCtx.warnings_total` 未被读写 | `batch_hw.py:2086` | 独立清理 |
| `progress_cb` 死参数 | `batch_hw.py:415`（声明后从未引用） | 独立清理 |

## 18.4 D 类：**禁止触碰**

| 路径 | 理由 |
|---|---|
| `core/channel_sync.py` | `HD-G06` 冻结；prompt §13 禁止绕过 channel-sync |
| `core/sync_estimate.py`、`core/sync_fix.py` | P1/P4 算法，冻结 |
| `core/audio_*.py`（15 个文件） | prompt 明文"不修改音频功能" |
| `core/audio_sync_tv.py`、`audio_sync_tv_exec.py` | P4，冻结 |
| `tests/fixtures/channel_sync/**` | hash 钉死的冻结基线（`baseline_identity.py:78-81`） |
| `tests/audio_sync_p4/**`、`tests/audio_sync_p311/**`、`tests/audio_sync_p37/**`、`tests/audio_sync_acquisition/**` | P4 fixtures 与结论 |
| `preservation/audio_sync.py` | P4 集成点 |
| `docs/audio-sync-p2|p3|p4/**` | P4 研究结论 |
| `tools/avhw/**` | patched research binary（hash 钉死） |
| 任何已发布的 tag | prompt §15 明文 |
| `main` 分支 | prompt §15 明文 |

---

# 19. 风险与未决事项

## 19.1 `OPEN DECISION` 汇总（**必须由人决定，本文不替维护者选择**）

| # | 决策 | 选项 | 本文建议 | 阻塞什么 |
|---|---|---|---|---|
| **OD-1** **SUPERSEDED** | ~~是否把硬解设为默认~~（与 `final-report.md` §11 第 9 条"需要矩阵全绿 + production benchmark 之后再决定"的关系） | ① 按 prompt 执行（改默认）② 先满足归档的前置条件再改 | ~~**② 的前置条件必须满足，然后执行 ①**~~ **已由维护者指令覆盖：统一硬件优先，不再以 benchmark 为前置**。见 §9.2 | — |
| **OD-2** **SUPERSEDED** | ~~Sony 普通模式是否允许显式 `ffmpeg_hw`~~ | ~~A 不允许（建议）／B 允许 opt-in，默认仍软解~~ | **已覆盖：Sony 不再因身份被排除在硬解候选之外**。见 §9.2.1 | — |
| **OD-3** | **`require` 硬解语义的替代品** | A 接受能力丢失／B 新增独立强制开关／C 挂到 `--no-metadata`/multihw | **A** + 文档明确写出能力移除 | §15.2-G |
| **OD-4** | **`--no-metadata` 下 `--check` 的语义** | A 降级为 output-validity only／B 拒绝非 basic／C 标记 SKIPPED | **A** | §11.3 |
| **OD-5** | **`--no-metadata` 下容器 brand** | ① 不设 brand／② 通用 brand | **①** | §11.4 |
| **OD-6** | **`--jobs N` 的上限** | ① 无上限／② `min(32, cpu*2)`／③ 各设备 `max_parallel` 之和 | **②** | §14.5 |
| **OD-7** | **resume 与逐 job 清理的冲突** | A 无条件清理（接受 resume 降级为崩溃恢复）／C 新增 `--resume-work` | **A**（因为 `dst` 存在即 skip 已提供重跑加速） | §16.4 冲突 1 |
| **OD-8** | **rigaya_hw substrate 是否合并/删除** | 保留但不扩展（建议）／删除 | **保留但不扩展** | §9.1 |
| **OD-9** | **多 GPU 的投入顺序** | A 只做 FFmpeg 侧（建议）／B 同时做 rigaya 侧／C 不做 | **A** | §13.4 |
| **OD-10** | **AV1 是否进入自动选择** | 保持"必须显式"（建议）／按 §4.4 加 `nvenc-av1→qsv-av1→svtav1` | **保持必须显式**（理由：`nvenc-av1` 本机不可用 + codec 变更是输出语义变更） | §10.2 |
| **OD-11** | **capability cache 是否需要 `--refresh-caps`** | 需要／不需要（按版本+驱动+设备自动失效即够） | **不需要**（自动失效足够） | §16.3 |
| **OD-12** | **文档目录** `docs/plans/` 是否新建 | 新建（建议）／并入 `docs/design/` | **新建** | §1.5 |
| **OD-13** | **H-1 死代码是否本次一并修复** | A 一并修（建议，独立 commit）／B 另开缺陷单 | **A，独立 commit** | §18.1 |
| **OD-14** | **HDR 素材从哪来**（§17.4-C-7 无法在本机测） | 维护者提供／跳过该用例／只做单元级 colour 元数据透传测试 | **只做单元级**，并在报告中标注 HDR 硬解为 `UNKNOWN` | §17.8 |
| **OD-15** | **多 NVIDIA 主机从哪来**（§17.5-D-2） | 维护者提供／标记 SKIP | **标记 SKIP**，不得假装通过 | §17.5 |

## 19.2 OD-1 的详细论证（**本节最重要**）

**冲突（CONFIRMED）**：

| 来源 | 立场 |
|---|---|
| **prompt §4.1** | "non-Sony source → **hardware decode by default**"（明确要求改默认） |
| **`docs/hardware-decode/final-report.md` §11 第 9 条** | "**是否把 hardware decode 设为默认** —— 需要**矩阵全绿 + production benchmark 之后**再决定"（决策项，未决） |
| **本 session 实测 §5.5** | 硬解 + x265 在 4 个源上 **±2 % ~ +19 %**，即**不能从性能角度支持改默认** |

**结论（PROPOSED）**：prompt 的策略要求可以执行，但**必须先把
`final-report.md` §11-9 的前置条件补齐**，否则会凭空声称"性能更好"：

```text
改默认之前必须交付的两份证据（PROPOSED）：

证据 1 —— "矩阵全绿"
  · `python -m tests.hwdecode.harness summary` 的 final_status == "READY FOR REVIEW"
  · 当前是 FAIL 2（HD-G02 / HD-G06）→ **必须先定性这两个 FAIL**
    （它们是否与硬解无关？本 session 读到的原因分别是
     "channel-sync decision 与基线不一致" 与 "core/channel_sync.py 相对 main 有改动"
     —— **都属于音频域，与硬解无关**，但必须由维护者确认这一判断）

证据 2 —— production benchmark
  · 在**生产档位**（HQ 而非本 session 的 veryfast/CRF28）与
    **生产素材长度**（不是 16/30 帧）上，测：
      (a) 单任务 wall time：软解 vs ffmpeg_hw（**必须诚实报告可能更慢**）
      (b) 并发 N 下的聚合吞吐：软解 vs ffmpeg_hw（这才是硬解可能赢的地方）
      (c) CPU time / peak RSS
  · ⚠️ 归档明确警告（`final-report.md` §J.1）：
    "wall clock 与 fps 来自有既定漂移的笔记本主机，跨 session 绝对值不可比"
    → benchmark 必须在**同一 session 内**做 A/B，并重复 ≥3 次
```

> **prompt 原话**："最终目标是减少实际处理时间，同时保持项目要求的输出行为，
> 而不是只追求日志中出现了 NVDEC/QSV 字样。"
> **本节是对这句话的落实**：在证明"减少实际处理时间"之前，
> 不把"硬解优先"当成已经赢了。

**如果不满足前置条件就改默认，会发生什么（INFERRED）**：
`--no-metadata` 与 multihw 下所有素材都走硬解，而硬解**可能更慢**，
且这些路径**不再有 rigaya 版的帧数门槛**（§9.4）。
→ 用户得到的是"更慢 + 检查更少"的默认行为变化。**这是最坏的结果。**

## 19.3 技术风险清单

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| **R-1** | 触碰 `encoders/x265.py` 的交付命令导致既有输出变化 | 中 | **高** | 用 C-11（跨 substrate 位相同性）作为硬门禁；每个 commit 跑 `--level full` |
| **R-2** | `--no-metadata` 改变音频执行位置导致音轨/同步回归 | **中高** | **高** | B-9 / B-10 两条专项用例；**这是本功能最大的风险**（§11.5-R1/R3） |
| **R-3** | CLI 移除导致 `V080_BASELINE` 变红，被误当作重构失败 | **高** | 低 | §14.5 已明确：必须同改动编辑 `hardware_audio.py:315-332` |
| **R-4** | 清理逻辑误删 resume/preservation/音频临时文件 | 中 | **高** | §16.2-D 的禁止清单；E-1..E-10 全矩阵 |
| **R-5** | 多设备调度导致并发超预算（今天 multihw 最坏 16 并发） | 中 | 中 | §12.5-M2 + D-10 用例 |
| **R-6** | 能力缓存读取引入"自动选中的 ≠ 跑得起来的" | 中 | **高** | §10.5：自动选择与执行必须读**同一 `CapsSnapshot`** |
| **R-7** | `hwdownload` 的格式约束在别的 GPU/驱动上不同 | 中 | 中 | 探测必须**真实解码 1 帧**（§9.5），不得硬编码 `p010le` |
| **R-8** | QSV 的 H.264 4:2:2 拒绝行为在别的 Intel 代次上不同 | 低 | 低 | 探测驱动，不硬编码（§5.4 的实测只用于**本机**的初始值） |
| **R-9** | 性能 benchmark 在笔记本上被热漂移污染 | **高** | 中 | 同 session A/B + ≥3 次重复 + 记录 min/mean/max（复用归档 HD-I01 的方法） |
| **R-10** | 未提交的工作树改动（§1.2.1）与本次改动冲突 | **中高** | **高** | §23：新 session 必须先确认那 8 个文件的归属，再基于**已提交**的基线开工 |
| **R-11** | `--jobs auto` 接入软件路径后，x265 线程数 × 文件并发过度订阅 | **中高** | 中 | §14.6 必须先做本机标定实测 |
| **R-12** | 移除 `--hw-decode require` 后操作者的强制语义静默消失 | 中 | 中 | OD-3；release notes 必须明写 |

## 19.4 未决的技术未知（`UNKNOWN`，必须实测才能推进）

| # | 未知 | 阻塞什么 | 实测方法 |
|---|---|---|---|
| **U-1** | **NVEncC/QSVEncC 是否支持选择 GPU，参数名是什么** | §13 全部 | §13.2 的三条命令 |
| **U-2** | `ffmpeg_hw` 下 `-ss`（seek）是否与软解等价 | §9.7 | A/B + 位相同性 |
| **U-3** | Intel iGPU 显存如何采集 | §14.2 | 无已知通路；可能需要放弃 iGPU 显存预算 |
| **U-4** | NVENC 会话数上限（"session 争用"是否真实） | §14.4 | 归档记录的争用是**推断**（`xavc-hs-frame-count.md` §10-17，**无 VRAM 遥测**） |
| **U-5** | x265 默认线程数 / 是否支持 `--pools` 限制 | §14.6 | 读 `x265.json` + 实测 |
| **U-6** | 并发 N 个 x265 的最优 N | §14.6 | §17 的标定矩阵 |
| **U-7** | 硬解在**生产档位**（HQ）下的性能 | OD-1 | §19.2 证据 2 |
| **U-8** | 8-bit / 1080p / VFR / HDR / HEVC 4:2:2 的硬解行为 | §17 C-5..C-7 | **缺素材**（§17.8） |
| **U-9** | AV1 硬解（`av1_cuvid` / `av1_qsv`）是否可用 | §10.2 | 本机未测 |
| **U-10** | `preservation/validate.py::compare` 在硬解中间体上是否产生假 FAIL | OD-2 | 只在选 OD-2 选项 B 时才需要 |

---

# 20. 后续实施顺序

> **prompt 明文**：下一 session 必须"先执行自己的 implementation readiness review，
> 然后按规划实施"。

## 20.1 阶段 0：Implementation Readiness Review（**动手前必做，不写代码**）

| # | 动作 | 产出 | 阻塞 |
|---|---|---|---|
| 0.1 | 重新执行 §1.1 的全部 git 命令 | 更新后的基线表 | **全部** |
| 0.2 | 确认 §1.2.1 的 8 个未提交文件 + `VERSION 0.8.1` 的归属（已提交？合并？） | 决定基线 commit | **全部** |
| 0.3 | 建立**自己的**回归基线（§17.7）：`--level unit`、`--level toolchain`、记录 PASS/FAIL 数字 | `work/<新 session>/baseline.json` | 全部回归判定 |
| 0.4 | 跑 `python -m tests.hwdecode.harness summary`，确认 HD-G02/HD-G06 的失败状态未变 | 记录为既有状态 | 回归判定 |
| 0.5 | 实测 **U-1**（rigaya GPU 选择参数） | `--device` 参数名与语义 | §13 |
| 0.6 | 实测 **U-2**（`ffmpeg_hw` + `-ss` 等价性） | 结论 | §9.7 |
| 0.7 | 实测 **U-5 / U-6**（x265 线程与并发标定） | 本机标定表 | §14.6 |
| 0.8 | 交付 **OD-1 的证据 2**（production benchmark A/B） | benchmark 报告 | **改默认这个动作** |
| 0.9 | 就 §19.1 的 15 个 `OPEN DECISION` 取得维护者答复 | 决策记录 | 对应章节 |
| 0.10 | 从**稳定基线**创建新的实现分支（§23），**不碰 `main`、不碰任何 tag** | 分支 | — |

> **0.8 是硬前置**：没有 benchmark，**不得**改 `--no-metadata` 与普通模式的
> 默认解码策略（§19.2）。其余阶段可以在 0.8 之前推进。

## 20.2 实施阶段（依赖顺序，每阶段独立可回滚）

```text
阶段 1 — 基础设施（无行为变更，可单独合并）
  1.1  core/workdir.py::job_workdir()（§16.2-B），**先只在一个路径上接线**（DJI），
       并加 E-1/E-2/E-3 用例
  1.2  把 job_workdir 推广到三条硬件路径 + 三条软件路径 + transparent_main
  1.3  修 §16.6 的"报告复制失败 → 保留目录"
  1.4  输出：E-1..E-10 全绿；`--level full` 无回归
  ⚠️ 风险：这是唯一**不依赖任何 OPEN DECISION** 的阶段 → **建议第一个做**

阶段 2 — 能力探测扩展（无行为变更）
  2.1  caps.py 三态结果 + TimeoutExpired（§10.3-②）
  2.2  caps.py FFmpeg 解码能力探测（§9.5）
  2.3  caps.py 缓存读取（§3.3-G2）+ CapsSnapshot（§10.5）
  2.4  真实"解码 1 帧"的接受性探测（**不硬编码 p010le**）
  2.5  输出：新增 unit 用例通过；启动日志出现 ffmpeg 解码能力快照
  ⚠️ 依赖：无

阶段 3 — FFmpeg 解码 substrate（**核心，但默认仍关**）
  3.1  新增 encoders/ffmpeg_hw.py（命令片段 + 兼容性判定 + 传输链）
  3.2  encoders/x265.py 接受 decode_args + -vf 链
  3.3  接入一个**内部开关**（不暴露 CLI）或直接由 --no-metadata 驱动
  3.4  输出：C-1..C-12 全绿；**C-11（位相同性）是本阶段的门禁**
  ⚠️ 依赖：阶段 2；R-1 风险最高 → 每个 commit 跑 --level full

阶段 4 — CLI 清理（§15）
  4.1  同一改动内：移除 4 个旧 switch + 编辑 V080_BASELINE（§14.5 / T-1）
  4.2  重写 hardware_audio.py:531-539 的硬解用例
  4.3  重写 tests/hwdecode/harness.py 的 H01..H03 + matrix.json + 矩阵文档
  4.4  更新 9 个文档文件；历史报告只加历史注记
  4.5  输出：`--level unit`/`toolchain`/`full` 全绿；
       `python -m tests.hwdecode.harness check-matrix` 通过
  ⚠️ 依赖：阶段 3（否则没有替代的解码策略入口）
  ⚠️ 风险 R-3：必须与 T-1 同改动

阶段 5 — --no-metadata（§11）
  5.1  preservation 绕过入口（明确记录 META_POLICY，不静默）
  5.2  OD-4 / OD-5 的决策落实
  5.3  **B-9（音频不丢）/ B-10（channel-sync 未绕过）是门禁**
  5.4  输出：B-1..B-10 全绿
  ⚠️ 依赖：阶段 3（需要硬解 substrate）；**OD-1 的证据 2**
  ⚠️ 风险 R-2：本功能最大风险

阶段 6 — 编码器优先级修正（§10）
  6.1  接上 classify_reader_failure（§10.3-①）
  6.2  hw_backend_for 的工具缺失明确失败（§10.3-③）
  6.3  A-9（显式不被覆盖）用例
  6.4  输出：A-1..A-11 全绿
  ⚠️ 依赖：阶段 2

阶段 7 — 资源调度与 jobs=auto（§14）
  7.1  core/resources.py（三态采样）
  7.2  ResourceBudget + 准入规则（§14.4）
  7.3  `--jobs` 默认改 auto + 上限校验（OD-6）
  7.4  软件路径接入并发池（§14.6，依赖 0.7 的标定）
  7.5  输出：E-5 / D-7 / D-8 / D-10 全绿；并发压测无超预算
  ⚠️ 依赖：阶段 2（CapsSnapshot）与 0.7（标定）

阶段 8 — multihw 重设计（§12）
  8.1  `--encoder` 硬冲突（§12.2）
  8.2  设备队列 + 预算（§12.5）
  8.3  无可用硬件的错误文本（§12.3）
  8.4  输出：D-1 / D-3..D-10 全绿；D-2 标记 SKIP
  ⚠️ 依赖：阶段 7

阶段 9 — 多 GPU 横向扩展（§13，**建议只做 FFmpeg 侧**）
  9.1  依赖 0.5（U-1 的实测结论）决定 rigaya 侧是否纳入
  9.2  per-device 能力探测（§13.5）
  9.3  per-device 调度
  9.4  输出：D-3（NVIDIA + Intel）全绿；D-2 SKIP
  ⚠️ 依赖：阶段 8 + U-1

阶段 10 — 文档与发布（§21）
  10.1 README / architecture.md / release notes 重写
  10.2 docs/plans/ 中更新本文件的状态（哪些 OPEN DECISION 已决）
  10.3 release/build_release.py 白名单复核（**不得**纳入 tools/avhw/）
```

## 20.3 顺序的论证（为什么不是别的顺序）

| 决策 | 理由 |
|---|---|
| **阶段 1（`.1ktwork`）最先** | 它是唯一**零 `OPEN DECISION` 依赖**、零行为风险、且是 prompt 的独立目标。它也只碰 `core/workdir.py` + 若干调用点，**不与解码/编码改动耦合**，最适合先落地建立信心与回归基线 |
| **阶段 2（探测）第二** | 阶段 3/5/6/7/8 全部依赖能力快照；且阶段 2 本身**不改变任何行为**（只加探测与缓存） |
| **阶段 3（FFmpeg substrate）在 CLI 清理之前** | 否则阶段 4 移除 `--hw-decode` 后**没有替代的解码策略入口**，中间态无法测试 |
| **阶段 5（`--no-metadata`）在 CLI 清理之后** | `--no-metadata` 是**新增** CLI；而阶段 4 要编辑 `V080_BASELINE`。两者若同阶段会互相干扰（T-1）。分开做，各自单独编辑一次 |
| **阶段 7（jobs）在 multihw 之前** | multihw **必须**服从 `jobs=auto` 的资源约束（prompt §8.4）→ 预算模型必须先存在 |
| **阶段 9（多 GPU）最后** | 它依赖 U-1（未实测的 rigaya 参数）与阶段 8 的设备队列；且本机只有 1 张 NVIDIA，验证能力有限 |

## 20.4 每个阶段的"完成定义"（DoD）

```text
所有阶段共同的 DoD：
  □ `python tests\full_autotest.py --level unit`      全绿（含被修改的 V080_BASELINE）
  □ `python tests\full_autotest.py --level toolchain` 全绿
  □ `python tests\full_autotest.py --level full`      全绿
  □ `python -m tests.hwdecode.harness provenance`     OK
  □ `python -m tests.hwdecode.harness check-matrix`   OK
  □ `python -m tests.hwdecode.harness summary`        与阶段 0.4 的既有 FAIL 状态**无新增**
  □ 本阶段指定的新用例全绿
  □ 每个 commit 的 message 说明"改了什么 / 为何 / 证据"
  □ 未触碰 §18.4 的 D 类路径（用 git diff 自查）
```

---

# 21. 发布兼容性

## 21.1 版本与语义（PROPOSED）

| 项 | 内容 |
|---|---|
| **当前发布版本** | `v0.8.0`（tag）；工作树 `VERSION` 已是 `0.8.1`（未提交，§1.2.1） |
| **本次改动的性质** | **破坏性 CLI 变更**（移除 3 个开关 + 1 个取值）+ 默认行为变更（解码策略、`--jobs`）+ 新增 1 个开关 |
| **PROPOSED 版本** | **`v0.9.0`**（minor bump，因为默认行为与 CLI 表面都变了）<br>**OPEN DECISION**：若维护者认为"移除 CLI 开关"属 major，则为 `v1.0.0`。**本文不决定。** |
| `VERSION` 文件 | 在**实现分支**上改动；**不得**修改任何已发布 tag |

## 21.2 破坏性变更清单（必须写进 release notes）

| # | 变更 | 用户可见后果 | 迁移动作 |
|---|---|---|---|
| **BC-1** | `--hw-decode` 被移除 | 使用该开关的脚本/命令**立即报错**（argparse rc=2） | 普通模式默认语义已按源类别决定（§9.2），多数场景**无需替代**；`require` 语义见 §15.2-G |
| **BC-2** | `--hw-decode-verify` 被移除 | 同上 | **无替代**（该功能从未通过 CLI 生效，§6.4） |
| **BC-3** | `--no-hw-autoselect` 被移除 | 同上 | 改用 `--encoder x265`（6 个既有测试点已证明这是等价意图，§15.4） |
| **BC-4** | `--preset all` 被移除 | 同上 | 为每个档位**分别运行一次**（用 `--preset hq` / `--preset uhq` 等）<br>**⚠️ 输出布局**：多档位模式的输出会插一层 `<PRESET>/` 目录（`core/paths.py:92`）。单档位不插。**因此从 `--preset all` 迁移到多次单档位运行，输出路径与 `--preset all` 的产物路径一致**（因为每次都是单档位）。**这一点必须验证**（§17-A-11） |
| **BC-5** | **普通模式的默认解码策略改变** | 非 Sony 源默认走硬解（若 OD-1 批准） | 无（行为变化，非接口变化）；但**必须**在 release notes 里给出 benchmark 数字 |
| **BC-6** | **`--jobs` 默认从 `1` 变为 `auto`** | 默认并发 > 1 → **峰值内存/显存上升**、单文件 wall time 可能上升、总批次时间下降 | 需要单线程确定性行为的用户显式给 `--jobs 1` |
| **BC-7** | **`.1ktwork` 逐 job 清理** | 失败后不再有残留中间体可供人工检查 | 使用 `--keep-work` |
| **BC-8** | **multihw + `--encoder` 成为硬冲突** | `--experimental-multihw --encoder nvenc` 从合法变为 rc=2 | 去掉 `--encoder` |
| **BC-9** | 新增 `--no-metadata` | 无（新增） | — |

## 21.3 发布包与工具链（CONFIRMED 的边界，必须重申）

| 事实 | 来源 | 本次是否改变 |
|---|---|---|
| `tools/avhw/` **不进发布包** | `release/build_release.py:72-76 TOOL_DIRS` | ❌ **不改变** |
| `docs/`、`olddocs/`、`work/`、`testsets/` 不进包 | 同上 `:359-361` | ❌ 不改变 |
| `tools/ffmpeg.exe` + `tools/ffprobe.exe` 进包 | 同上 `:71` | ❌ 不改变 |
| `tools/NVEncC_9.31_x64`、`tools/QSVEncC_8.26_x64`、`tools/GPAC` 进包 | 同上 `:72-76` | ❌ 不改变 |
| 因此**发布安装中 rigaya 硬解不可用、FFmpeg 硬解可用** | §5.1、§7.3 | → **本次重构的可用性收益正是从这里来**（§9.1） |

> **★ 发布兼容性的关键结论**：
> 新架构（`ffmpeg_hw` 为主通路）在**发布安装中真实可用**，
> 而旧架构（`rigaya_hw`）**在发布安装中完全不可用**。
> 这是本次重构**对最终用户最直接的价值**，且它**不需要** patched binary。

## 21.4 与既有文档的关系（PROPOSED）

| 文档 | 处置 |
|---|---|
| `docs/design/architecture.md` | §8 整节重写；在文件头加"v0.9.0 起解码策略见 docs/plans/…" |
| `README.md` | §"Hardware Decode" 整节重写；§"Features" 的对应行更新；§"Roadmap"（`:656`）更新 |
| `docs/hardware-decode/**` | **历史档案，不改语义**。每个文件加一行历史注记：<br>"`--hw-decode` 已于 v0.9.0 移除；见 `docs/plans/hardware-codec-policy-redesign.md`" |
| `docs/release_notes_v0.6.1.md` / `v0.7.1.md` / `v0.8.0.md` | **不改**（已随 tag 冻结） |
| `docs/release_notes_next.md` | 重写为"下一周期"（本次改动的说明） |
| **新增** `docs/release_notes_v0.9.0.md` | 发布时创建（含 §21.2 的 9 条破坏性变更 + benchmark 数字） |
| `docs/plans/hardware-codec-policy-redesign.md` | **本文件**；实施后更新状态栏（哪些 OPEN DECISION 已决、哪些阶段已完成） |

## 21.5 回滚策略（PROPOSED）

| 场景 | 回滚动作 |
|---|---|
| 阶段 3（FFmpeg substrate）出问题 | 该阶段**默认关闭**（由 `--no-metadata` 驱动），因此**关闭 `--no-metadata` 即回滚**，无需 revert |
| 阶段 5（`--no-metadata`）出问题 | 同上 |
| 阶段 7（`jobs=auto`）出问题 | `--jobs 1` 恢复串行；或 revert 该 commit（它不触碰解码/编码） |
| 阶段 4（CLI 移除）出问题 | **不可部分回滚**（CLI 表面已变）→ 必须 revert 整个阶段 4 的 commit |
| 阶段 1（`.1ktwork`）出问题 | revert 该 commit；或 `--keep-work` 作为临时缓解 |
| **整体** | 实现分支独立于 `main`（§23），**未合并前零风险** |

> **PROPOSED 的排序建议据此强化**：把**可回滚的**阶段（1、2、3、5、7）
> 尽量前置，把**难以回滚的**阶段 4（CLI 移除）放在明确需要它的位置（阶段 4）。
> §20.3 的顺序已满足这一点。

---

# 22. P4 当前状态与隔离声明

## 22.1 P4 的真实进度（CONFIRMED，本 session 核对仓库文档，不沿用旧摘要）

**分支**：`research/audio-sync-p3-production-semantics`（= 当前分支）
**HEAD**：`2b34d41` — `docs(audio-sync): close P4 engineering status`（2026-10-12 13:13:01 +0800）

`docs/audio-sync-p4/` 下 **38 个文档**，阶段 P4.1–P4.11 全部有记录。
最终判定（`P4-FINAL-REPORT.md` / 最后一个 commit 的 message，CONFIRMED）：

```text
Closure verdict: P4 = ENGINEERING CLOSED / REAL-WORLD VALIDATION BLOCKED.
No status moved: D2 DEFERRED, D3 NOT ESTABLISHED, D5 DEFERRED,
production geometry/defaults/algorithm UNCHANGED.
```

| 项 | 状态 | 证据 |
|---|---|---|
| P4.9A / P4.9B 短素材观测与证据语义 | **完成**（411 cases → PASS 381 / FAIL 30；R1 15 cases PASS） | `p4.9a-short-clip-revalidation.md`、`p4.9b-residual-calibration.md` |
| P4.10 真实 Sony corpus 调查 | **完成，结论是 BLOCKED** | `p4.10-real-source-qualification.md`：`REAL_GROUND_TRUTH = BLOCKED`；**G0 27 / G1 166 / G2 0 / G3 0**——机器上**没有任何**素材带独立延迟真值 |
| P4.11 acquisition tooling | **完成**（tracking `tests/audio_sync_acquisition/`，含 `make_fixtures.py` 已跟踪） | `p4.11-acquisition-tooling.md`、最后一个 commit |
| 独立真实无线延迟 Ground Truth | **未完成（BLOCKED）** | 需要**操作者录制的 16 个 take**（只有人能给） |
| **D3 residual acceptance** | **NOT ESTABLISHED** | `residual-acceptance.md`："This document does **not** invent a threshold." |
| **D2 gap policy** | **DEFERRED**（有版本化的实现默认） | `gap-policy-decision.md`、`p4.3-production-semantics.md` |
| **D5 coordinate contract** | **DEFERRED**（同上） | `coordinate-decision.md` |
| D1 uncertainty contract | **DECIDED AND IMPLEMENTED** | `p4.3-production-semantics.md` |
| 时变同步生产路径 | 实现完成、**opt-in、默认 OFF、未接线** | `implementation.md`：`algorithm version 2.4.0-p4`、`production call sites: none` |

**与 prompt §13 给出的历史摘要的差异（必须记录）**：

| prompt 的历史摘要 | 仓库当前证据 | 判定 |
|---|---|---|
| "P4.9A/B：短素材观测与证据语义研究" | ✅ 一致 | 确认 |
| "P4.10：真实 Sony corpus 调查" | ✅ 一致（且结论是 **BLOCKED**，不只是"调查"） | 确认+强化 |
| "P4.11：acquisition tooling 与操作准备" | ✅ 一致（且**已跟踪进仓库**，不再只在 gitignored `work/` 下） | 确认 |
| "独立真实无线延迟 Ground Truth 尚未完成" | ✅ 一致（**G2=0 / G3=0**） | 确认 |
| "D3 residual acceptance 尚未建立" | ✅ 一致（`NOT ESTABLISHED`） | 确认 |
| "D2 gap policy 与 D5 coordinate contract 仍有待明确" | ✅ 一致（均 `DEFERRED`） | 确认 |
| （prompt 未提及） | **P4 = ENGINEERING CLOSED** | **新增信息** |

> **结论（CONFIRMED）**：prompt 提供的历史摘要**与仓库一致**，
> 且未发现被夸大的进度。唯一需要补充的是：
> **P4 的工程部分已正式关闭（engineering closed），
> 未完成的全部是"真实世界验证"，而它被一个只有操作者能解开的条件阻塞**。

## 22.2 隔离声明（prompt §13 明文，本文逐条落实为实施约束）

**本次硬件编解码规划/实施不得**：

```text
✗ 终止 P4
✗ 修改 P4 算法
✗ 删除 P4 fixtures
✗ 修改 P4 结果
✗ 把 P4 的 acquisition work 混入本次规划
✗ 通过硬件编解码重构绕过 channel-sync
```

**落实为 §18.4 的 D 类禁止触碰清单，以及三条测试级防护**：

| 防护 | 机制 | 证据 |
|---|---|---|
| **P-1** | `HD-G06` 用 `git diff --name-only main...HEAD -- core/channel_sync.py core/sync_estimate.py tests/fixtures/channel_sync/` **在每次矩阵运行时检查** | `tests/hwdecode/harness.py:3117-3128` |
| **P-2** | `tests/fixtures/channel_sync/a7m5_real_137_baseline.csv` 的**内容 hash 钉死**（`7B1FB2DC…464B`、548 数据行） | `tests/audio_sync_p4/baseline_identity.py:78-103` |
| **P-3** | `tests/audio_sync_p4/test_production_semantics.py` 有**架构不变量**测试（例如 `SANCTIONED_TV_IMPORTERS = {"audio_sync_tv_exec.py", "channel_sync.py"}`，`preservation` **刻意不在列表内**） | `:950`、`:756` |

> **⚠️ 一个必须交给维护者的既有事实（CONFIRMED）**：
> `HD-G06` **当前是 FAIL**：
> `"channel-sync artefacts changed vs main: ['core/channel_sync.py']"`。
> 即**当前分支上的 `core/channel_sync.py` 与 `main` 不同**。
> 这是 P4.2 的**有意改动**（`p4.2-evidence-integrity.md`：生产文件改动 2 个 ——
> `core/channel_sync.py`、`core/audio_models.py`），不是缺陷。
> **但本次重构不得在这个既有的差异上再叠加任何改动**（§18.4）。

## 22.3 两个工作流的边界（PROPOSED）

```text
工作流 A：硬件编解码策略重构（本次）           工作流 B：音频同步 P4（继续推进）
├─ 分支：新建的 feature 分支（§23）            ├─ 分支：research/audio-sync-p3-production-semantics
├─ 基线：main（或维护者指定的稳定点）           ├─ 分支状态：已 push，未合并，未 tag
├─ 触碰：1kt.py / core/batch_hw.py /          ├─ 触碰：core/audio_*.py / core/channel_sync.py /
│        encoders/** / core/{caps,config,     │        core/sync_estimate.py / tests/audio_sync_*
│        paths,probe,resources}.py /          │
│        preservation/pipeline.py（受限）      │
└─ 不触碰：见 §18.4                            └─ 不触碰：1kt.py / core/batch_hw.py / encoders/**

共享的**只读**依赖（双方都不得修改）：
  · tests/fixtures/channel_sync/a7m5_real_137_baseline.csv（hash 钉死）
  · tools/avhw/**（hash 钉死）
```

**唯一的交互点（PROPOSED）**：`--no-metadata` 改变了音频的**执行位置**
（§11.5-R3）。因此：

* 工作流 A 的 B-9 / B-10 两条用例**必须**证明 channel-sync 结果不变；
* 若 B-10 失败，**工作流 A 必须自行调整**（例如让 `--no-metadata` 的
  Sony 路径仍走 channel-sync 的既有实现点），**不得**要求修改 `channel_sync.py`。

---

# 23. 给下一个 session 的实施要求

> **prompt 明文**："这份规划书将交给另一个全新的 Agent session。
> 因此必须能够独立使用。"

## 23.1 强制步骤（顺序不可打乱）

```text
1. 重新检查 GitHub 仓库当前状态
   → 执行 §1.1 的全部命令，与 §1.2 的表逐项比对
   → **不要**相信本文的任何 commit 号或行号，直到你亲自验证过
   → 特别注意 §1.2.1 的 8 个未提交文件与 VERSION=0.8.1

2. 从当前稳定基线创建一个**新的独立分支**
   → 建议命名：feature/hardware-codec-policy-redesign
   → **不直接修改 main**（prompt §15 明文）
   → **不修改现有 release tags**（prompt §15 明文）
   → 若 §1.2.1 的未提交改动未归属，**先向维护者确认**再开工
   → **★ 必须先确认"稳定基线"指哪一个，这是不可自行假设的产品问题**：
       · `main` = `97d5876`（2026-09-16），**比研究分支落后 89 个提交**
       · 研究分支 HEAD = `fe6c84e`（含本规划书），**领先 `main` 89 个提交**，
         且带有 P4 的既有改动与 §1.2.1 的未提交工作
       · 两者差别巨大：从 `main` 起分支 = 本次重构**不包含** P4 的
         `core/channel_sync.py` 改动与最新 `preservation/*` 修复；
         从研究分支起分支 = 会把这些一并带入
       · 本文**不替维护者决定**（见 §1.2 的勘误说明）。未确认前不要开工。

3. 先阅读这份规划书与它引用的文档
   → 本文件 §1–§23
   → docs/hardware-decode/README.md、final-report.md、xavc-hs-frame-count.md
   → docs/design/architecture.md §2/§8
   → docs/audio-sync-p4/README.md（了解隔离边界）

4. 先执行自己的 implementation readiness review
   → §20.1 的 0.1–0.10 十项
   → **0.8（production benchmark）是改默认解码策略的硬前置**

5. 然后按 §20.2 的阶段 1 → 10 实施
   → 每个阶段结束跑 §20.4 的 DoD
```

## 23.2 实施时必须遵守的硬约束

| # | 约束 | 来源 |
|---|---|---|
| C-1 | **不修改音频功能**（`AudioPlan`/`AudioTimeline`/PCM routing/PCM mixing/audio encode/mux/channel-sync 算法/P4 time-varying sync/P4 fixtures/P4 acquisition tooling/P4 conclusions） | prompt 二 |
| C-2 | **不把三层（metadata policy / decoder / encoder）混成一个布尔值** | prompt 三 |
| C-3 | **不要在运行时反复失败再逐层回退**；先用能力探测选好再构造命令 | prompt 建议 |
| C-4 | **不重写另一套能力探测器**；扩展 `encoders/caps.py` | prompt 六 |
| C-5 | **不无限重试**；不回退已失败的硬解 substrate | prompt 五-C |
| C-6 | **不伪造帧数 / 不补帧 / 不忽略进程失败 / 不忽略产物缺失或损坏 / 不把不支持静默当成功** | prompt 七 |
| C-7 | **不把"不保留 metadata"解释为"可忽略运行错误"** | prompt 四-4.2 |
| C-8 | **不删除被其它必要功能复用的底层实现** | prompt 九 |
| C-9 | **不通过删除整个 `.1ktwork` 根目录实现逐任务清理** | prompt 十一 |
| C-10 | **不为了清理而破坏 resume / retry / preservation manifest / 音频临时文件生命周期** | prompt 十一 |
| C-11 | **不把 `jobs=auto` 解释为可无限创建并发**；无可用硬件时 multihw 必须退出 | prompt 八-8.4 |
| C-12 | **不新建第二套测试系统** | prompt 十二 |
| C-13 | **不假定多 GPU 支持**；不得从"N 张卡存在"推出"N 张卡可用" | prompt 八-8.3 |
| C-14 | **不把已探测能力写成"该输入解码成功"** | prompt 六-6.1 |
| C-15 | 所有未解决的技术选择必须写成 `OPEN DECISION`，**不得自行编造用户尚未决定的行为** | prompt 十四 |

## 23.3 本文档的已知弱点（供下一 session 修正）

| # | 弱点 | 影响 | 建议 |
|---|---|---|---|
| W-1 | 所有 `file:LINE` 引用基于 `2b34d41` + §1.2.1 的未提交状态 | 行号可能漂移 | **以符号名（函数名/参数名）为准**，行号只作提示 |
| W-2 | §5.5 的性能数字来自**短片段**（16/30/105/195 帧）与 `veryfast`/`CRF28`，**不是生产档位** | 不能作为改默认的依据 | §20.1-0.8 必须重做 benchmark |
| W-3 | FFmpeg 侧的 **CPU time / peak RSS 未测** | 无法量化"更少 CPU/帧"是否在 FFmpeg 路径上成立 | 阶段 2 加 CPU/内存遥测 |
| W-4 | `NVEncC`/`QSVEncC` 的 GPU 选择参数**未验证**（U-1） | §13 的 rigaya 侧全部悬空 | 阶段 0.5 实测 |
| W-5 | HDR / 8-bit / 1080p / VFR / AV1 硬解**未测** | §17 的 C-5..C-7 部分不可执行 | 需要素材（OD-14） |
| W-6 | 本 session **未读** `docs/reference/nvenc/NVEncC_Options.en.md` 与 `qsv/QSVEncC_Options.en.md` 的设备章节 | U-1 的相关信息可能已存在 | 阶段 0.5 优先读 |
| W-7 | §14.6 的"软件路径并发"依赖 x265 线程标定（U-5/U-6，未做） | 无法给出初始并发数 | 阶段 0.7 实测 |
| W-8 | `--no-metadata` 的音频行为**只在设计层分析过**，未实测 | R-2 是最高风险 | 阶段 5 的 B-9/B-10 是硬门禁 |
| W-9 | 本文的 Apache/工具版本证据来自**本机**（ffmpeg 9.0.1 / NVEncC 9.31 / QSVEncC 8.26 / RTX 5070 Laptop / Arc 140T） | 换机后 §5 的结论需重测 | §5.7 的复现命令必须重跑 |

## 23.4 实施完成后的交付物清单（PROPOSED）

```text
□ 新分支（未合并 main，未动任何 tag）
□ 生产代码改动（§18.1 + §18.2）
□ 测试改动（§17 的 A/B/C/D/E 五组）
□ 文档改动（§21.4）
□ 更新后的本文件（状态栏：已完成的阶段、已决的 OPEN DECISION、
  实施中发现的与本文不符之处）
□ 一份 benchmark 报告（§19.2 证据 2）—— **改默认策略的前提**
□ 一份"与本文的偏差记录"（实施中若发现本文的任何 CONFIRMED 结论有误，
  必须写明，不得静默绕过）
```

---

# 附录 A：本文的取证记录

| 证据 | 方法 | 位置 |
|---|---|---|
| 仓库基线 | `git rev-parse` / `git branch -a` / `git tag --list` / `git log -20` / `git status` / `git diff --stat` | §1.2 |
| 未提交改动的内容 | `git diff core/batch_hw.py` / `git diff VERSION` | §1.2.1 |
| FFmpeg 工具链身份 | `ffmpeg -version` / `-hwaccels` / `-decoders` / `-encoders` / `-filters` / `-init_hw_device list` | §5.1 |
| `hwdownload` 格式约束 | 8 种格式 × 2 设备的穷举 | §5.2 |
| 三路解码保真度 | 逐路 `-f md5` 的原始流摘要 | §5.3 |
| 端到端 4:2:2 路由 | 全 195 帧 decode→libx265 + 产物基本流 SHA-256 | §5.3-C |
| 拒绝矩阵 | 5 个源 × 2 设备 | §5.4 |
| 性能 A/B | 4 个源 × 3 条路径的 wall time | §5.5 |
| 读写 AST 验证 | `ast.parse` + `ast.walk` 确认 `batch_hw.py:1263-1284` 的不可达性 | §6.5-H1 |
| `.1ktwork` 生命周期 | 逐文件通读 + 全仓 grep `rmtree` / `atexit` / `signal` / `keep_work` | §16.1 |
| 测试基础设施 | 通读 `tests/selftest/runner/engine.py`、`paths.py`、`harness.py` CLI、`matrix.json` | §17.1 |
| CLI 引用范围 | 全仓（排除 `dist/`、`build/`、`olddocs/backup/`）grep 四类开关 | §15 |
| P4 状态 | 通读 `docs/audio-sync-p4/` 的 README + P4-FINAL-REPORT + 最后 commit 的 body | §22.1 |

**本 session 的只读脚本**（位于 gitignored 的 `work/plan_probe/`，**不进入仓库**）：
`ab_decode_test.py`、`caps_probe.py`、`compare.py`。

**本 session 未做的事（明确声明）**：
* 未修改任何生产代码 / CLI / 配置 / 测试 / release 文件；
* 未创建实现分支；
* 未推送任何内容；
* 未运行任何**会修改仓库**的命令（全部为 `git` 只读子命令与对 `work/` 的写入）；
* 未删除或修改任何既有研究成果。

---

# 附录 B：`OPEN DECISION` 索引

| ID | 决策 | 章节 |
|---|---|---|
| OD-1 | 是否把硬解设为默认（与归档前置条件的关系） | §9.2、§19.2 |
| OD-2 | Sony 普通模式是否允许显式 `ffmpeg_hw` | §9.2.1 |
| OD-3 | `require` 硬解语义的替代品 | §15.2-G |
| OD-4 | `--no-metadata` 下 `--check` 的语义 | §11.3 |
| OD-5 | `--no-metadata` 下容器 brand | §11.4 |
| OD-6 | `--jobs N` 的上限 | §14.5 |
| OD-7 | resume 与逐 job 清理的冲突 | §16.4 |
| OD-8 | `rigaya_hw` substrate 是否合并/删除 | §9.1 |
| OD-9 | 多 GPU 的投入顺序 | §13.4 |
| OD-10 | AV1 是否进入自动选择 | §10.2 |
| OD-11 | 是否需要 `--refresh-caps` | §16.3 |
| OD-12 | `docs/plans/` 是否新建 | §1.5 |
| OD-13 | H-1 死代码是否本次一并修复 | §18.1 |
| OD-14 | HDR 素材从哪来 | §17.8 |
| OD-15 | 多 NVIDIA 主机从哪来 | §17.5 |
| OD-16 | 移除 `--preset all` 后 `multiple_presets` 参数是否彻底删除 | §15.5 |
| OD-17 | 本次改动的版本号（`v0.9.0` 还是 `v1.0.0`） | §21.1 |

---

*本文档为 planning-only 交付物。它不修改任何生产行为，也不代表任何
`OPEN DECISION` 已经做出。实施必须由独立 session 在独立分支上进行。*

