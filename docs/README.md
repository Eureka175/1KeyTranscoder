# docs/ — 文档分类索引

> **我要找什么？** → 先看下面这张表。

| 我想… | 去哪里 |
|---|---|
| **搞清楚代码到底怎么跑**（端到端数据流） | [`design/architecture.md`](design/architecture.md) ★ 新维护者从这里开始 |
| 看**当前该怎么用**这个工具 | 根 [`../README.md`](../README.md) |
| 看**评估汇总与决策记录** | [[`FINAL_REPORT.md`](FINAL_REPORT.md)](FINAL_REPORT.md)（注意其头部状态横幅） |
| 查**某后端为什么这样选、参数怎么定的** | [`evaluation/`](evaluation/)（先看该目录索引表里的"状态"列） |
| 查**某个子系统的设计**（硬件后端 / channel-sync / 4:2:2） | [`design/`](design/) |
| 看**硬件解码 integration 的测试矩阵与最终判定** | [`hardware-decode/`](hardware-decode/) ★ 主交付物是 [`integration-test-matrix.md`](hardware-decode/integration-test-matrix.md) |
| 找**厂商官方文档 / 上游源码 / GPU 能力探测** | [`reference/README.md`](reference/README.md) |
| 查**已封存的历史调查**（硬件解码 Phase 1 等） | [`../olddocs/docs/`](../olddocs/docs/) ★ **先读 [`../olddocs/README.md`](../olddocs/README.md) 的状态标注** |
| 看**测试素材清单与冻结基线** | [`fixtures/a7m5_channel_sync_fixtures.md`](fixtures/a7m5_channel_sync_fixtures.md) |
| 看**历史代码快照与废弃脚本** | 根 [`../olddocs/README.md`](../olddocs/README.md) |

> **分类规则**：**design** = 项目自身设计/实施文档；**evaluation** = 本项目的
> 评估与调研报告；**reference** = 第三方一手资料存档（按厂商分目）；
> **archive** = 已封存文档，已迁至根目录 [`../olddocs/`](../olddocs/README.md)（唯一归档区）。
> 配置 JSON（nvenc.json 等）是运行时配置，不属于文档，留在根目录；
> `metadata_forensics/` 是取证数据目录，`work/` 是实验产物，均不入档。
>
> ⚠️ **2026-09 文档审计补注**：本索引与若干评估文档此前存在**事实性错误与
> 过期结论**（目录文件数、默认后端、AV1 路由策略等）。已逐项更正，并在
> 受影响文档头部加了状态横幅，指明"哪几条被推翻、现行依据是哪一份"。
> 原文一律保留，未做删改——历史结论被推翻是研究过程的正常部分。

## 项目文件夹结构（整体架构）

```
F:\1KeyTranscoder\
├── 1kt.py                  主入口（CLI + 编排；硬件批量逻辑在 core/batch_hw.py）
├── watchfolder.py          轮询批处理入口（转调 1kt.py）
├── start.bat               双击启动
├── VERSION                 版本号唯一来源（0.6.2）
├── LICENSE                 LGPL-3.0-or-later
│
├── core/                   ★ 运行时核心（19 个 .py）：config / probe / postprobe /
│                             paths / scaling / source_classifier / batch_hw /
│                             channel_sync / sync_estimate / mp4_channel_sync /
│                             sync_fix / logging_utils / dashboard / dashboard_ui /
│                             models / versions / version / color
├── encoders/               ★ 编码后端（8 个 .py）：nvencc / qsvencc / x265 /
│                             svtav1 / caps / hw(plan_initial_format) / base
├── preservation/           ★ 元数据保留（16 个 .py）：pipeline / sony / dji / gpac /
│                             isobmf / validate / checker / selfcheck / quality /
│                             colour / gyroflow / backends / audio_sync / models /
│                             poc_video
├── tests/                  自动化测试（full_autotest / run_selfcheck / sony_selfcheck）
│                             + fixtures/channel_sync（137 段冻结基线 CSV）
├── release/                发布工具：build_release.py / verify_package.py
│
├── *.json                  档位配置（nvenc / nvenc_av1 / qsv / qsv_aligned /
│                             qsv_av1 / x265 + scaling / svtav1 + scaling /
│                             vce[预留未接]）—— 运行时配置，非文档
│
├── docs/                   📚 文档（见下）
├── tools/                  ⚠️ 自带工具链 —— gitignored，**只有这一份**
│   │                         （详见根 README §依赖 的警告框）
│   ├── ffmpeg.exe / ffprobe.exe      9.0.1 gyan full
│   ├── NVEncC_9.31_x64/              shipped 版（r4047，CUDA 11.8）
│   ├── QSVEncC_8.26_x64/             shipped 版（r4504）
│   ├── GPAC/                         MP4Box 等容器工具
│   └── avhw/                         ★ 硬解研究用的**补丁版**二进制（非 shipped）
│       ├── NVEncC_9.31_avhw/         `9.31 (r1)` CUDA 13.1
│       │                             sha256 dcf6d7a63143c777…7c8be4b
│       └── QSVEncC_8.26_avhw/        `8.26 (r4504)` 自建
├── testsets/               测试素材（1063 文件 / 97.5 GB）—— gitignored
├── work/                   实验产物与阶段证据（gitignored）
├── dist/                   发布产物：v0.6.1 zip + sha256 + manifest（0.54 GB）
├── olddocs/                历史代码快照与废弃脚本（详见 olddocs/README.md）
├── metadata_forensics/     取证数据（18 文件 / 2.7 MB，被 design 文档引用）
└── logs/                   运行时日志输出（gitignored）
```

> 各目录文件数按 **git 跟踪的 `.py` 文件**计（2026-09 实测）。此前版本标注的
> 54/24/46 与实际不符，已更正。**端到端管线与实际模块职责见
> [`design/architecture.md`](design/architecture.md)。**

### 是否进入正式发布包（`release/build_release.py` allowlist）

| 目录 / 文件 | 入包 | 说明 |
|---|---|---|
| `1kt.py` `watchfolder.py` `start.bat` `README.md` `LICENSE` `VERSION` | ✅ | 必需条目（缺失即拒绝构建） |
| `core/` `encoders/` `preservation/` `tests/` | ✅ | 全部 `.py`（排除 `__pycache__`/.pyc） |
| `*.json`（档位配置） | ✅ | `TOP_GLOBS = ("*.json",)` |
| `tools/`（ffmpeg / NVEncC / QSVEncC / GPAC） | ✅ | **白名单制**：`TOOL_FILES = tools/ffmpeg.exe, tools/ffprobe.exe`；`TOOL_DIRS = tools/NVEncC_9.31_x64, tools/QSVEncC_8.26_x64, tools/GPAC`。**不在白名单的目录一律不入包**——`tools/avhw/`（补丁版研究二进制）因此自动排除 |
| `docs/` `olddocs/` `logs/` `work/` `testsets/` `dist/` `release/` `metadata_forensics/` | ❌ | **明确排除**，见 manifest `excluded_by_design` |

> 即：**文档、实验产物、测试素材、发布工具本身都不进包**，
> 包内只含运行时必需内容 + 自带工具链。改动的具体清单见
> `dist/release-manifest.json` 的 `files`（455 条记录）。

## 📁 [design/](design/) — 设计文档（分类：项目设计）

| 文件 | 说明 |
|---|---|
| **[`architecture.md`](design/architecture.md)** | **★ 端到端架构总览（当前版本）**：两个入口与分支、后端解析四级优先级、三级降级梯、三条源管线、保留管线的幂等/续跑、channel-sync、`--check` 三级、必须保持的不变量、逐文件地图。**新维护者从这里开始读。** |
| [`hardware_backend_design.md`](design/hardware_backend_design.md) | 硬件后端（NVEncC/QSVEncC）设计定稿，含踩坑结论（5.x 节）。**§4.5/§9/§10.2/附录 B 部分已被后续代码推翻**（默认后端、已删除的 `--auto-downgrade`、控制台询问），以 [`architecture.md`](design/architecture.md) 为准；§5/§6/§7 实测矩阵仍有效 |
| [`implementation_report.md`](design/implementation_report.md) | 实施报告：降级链与回退路径的故障演练记录（§15 DJI 专线）。**§1/§4/§11 的模块名与开关已过时**；§12–§15 与现行代码一致 |
| [`hevc_422_rext_compatibility.md`](design/hevc_422_rext_compatibility.md) | HEVC 4:2:2 Rext 输出形态、播放兼容矩阵与归档建议 |
| [`channel_sync_p1.md`](design/channel_sync_p1.md) | **`--channel-sync` P1 设计文档**（algo 2.3.0-p1）：算法、阈值、轨道级降级、fixture 标定、测试矩阵。与 `core/channel_sync.py::DEFAULTS` 逐键一致 |
| ~~`INTEGRATION_REPORT.md`~~ | 已移入归档：见 [`../olddocs/docs/INTEGRATION_REPORT.md`](../olddocs/docs/INTEGRATION_REPORT.md)（⚠️ 部分过期：§A 模块清单已被 [`architecture.md`](design/architecture.md) 取代，§D 时序结论仍有效） |

## 📄 发布说明（分类：项目自身文档）

| 文件 | 说明 |
|---|---|
| [`release_notes_v0.6.1.md`](release_notes_v0.6.1.md) | **v0.6.1 发布说明**：Channel Sync P1 / AV1 mainline / AV1 色彩保真 / 流式内存修复；含验证矩阵、实测性能与已知限制 |

> 阶段验证报告的正式副本已归档在 `work/docs/` 与 `work/releases/`
> （`work/` 按项目约定不入文档目录、不入发布包）：
> `work/docs/channel_sync/stage12_memory_validation.md`（Stage 1.2 内存修复验证，
> 含 600 s 长程表）、`work/docs/channel_sync/memory_audit.md`（逐阶段内存归因）、
> `work/releases/v0.6.1_release_validation.md`（正式发布验证记录）。

## 📁 归档区 → 已迁至 `olddocs/docs/`

> **本目录不再设 archive/ 子目录。** 项目**唯一归档位置**是根目录
> [`../olddocs/`](../olddocs/README.md)：`olddocs/docs/` 放已归档**文档**，
> `olddocs/backup/` 放历史**代码**快照。这样避免"两处归档区"再次出现。
>
> **归档区不是垃圾场：里面的结论仍然有效、可以引用，只是不再随代码演进更新。**
>
> | 归档内容 | 位置 | 状态提示 |
> |---|---|---|
> | **★ 硬件解码最终交叉结论** | [`../olddocs/docs/hardware-decode/research-conclusion.md`](../olddocs/docs/hardware-decode/research-conclusion.md) | **进入 hardware-decode integration 前先读这一份** |
> | **端到端基准（S9）** | [`../olddocs/docs/hardware-decode/e2e-benchmark.md`](../olddocs/docs/hardware-decode/e2e-benchmark.md) | §0.1 已观测/推断未证/未测三者分离；§8.1 集成行动指南 |
> | **NVEncC 补丁溯源** | [`../olddocs/docs/hardware-decode/nvencc-patch.md`](../olddocs/docs/hardware-decode/nvencc-patch.md) | 集成候选；含 clean-apply 证明与集成前置条件 |
> | **QSVEncC 补丁溯源** | [`../olddocs/docs/hardware-decode/qsvencc-patch.md`](../olddocs/docs/hardware-decode/qsvencc-patch.md) | ⚠️ 仅对 pinned 8.26 生效，非通用声明 |
> | P0-A 硬件解码调研（Phase 1） | [`../olddocs/docs/hardware-decode/`](../olddocs/docs/hardware-decode/) | ⚠️ 个别文档部分过期：`README.md`/`root-cause.md` 中"丢帧在 rigaya reader layer / 确切源码行 Unconfirmed"已被 Phase 2 修正；"永不用 rigaya `--avhw`"建议已被推翻 |
> | 已取代的 AV1 三份评估 + 集成报告 | [`../olddocs/docs/`](../olddocs/docs/) | ❌/⚠️ 见 [`../olddocs/README.md`](../olddocs/README.md) §3.2 |
> | 逐份状态索引 | [`../olddocs/docs/_ARCHIVE-INDEX.md`](../olddocs/docs/_ARCHIVE-INDEX.md) | — |
>
> **读归档文档前先读状态标注**，否则会读到已被推翻的结论。
> 2026-09-12：四条 research 分支已全部并入 `main`，Phase 2 文档随迁入本归档区。

## 📁 [evaluation/](evaluation/) — 评估报告（分类：评估与调研）

| 文件 | 说明 | 状态 |
|---|---|---|
| [`av1_calibration.md`](evaluation/av1_calibration.md) | **★AV1 档位标定报告（2026-08-31 实测定案）**：SVT-AV1 四档 + 硬件 QVBR/ICQ 重标定，VMAF/XPSNR 矩阵与定案依据。数值与档位 JSON 逐键一致 | ✅ **权威（数值）** |
| [`av1_implementation_assessment.md`](evaluation/av1_implementation_assessment.md) | **AV1 三后端（svtav1/nvenc-av1/qsv-av1）实现评估**：Sony/DJI 保留管线（不打 XAVC tag）+ 端到端实测 + 标定状态 | ✅ **权威（路由/管线）** |
| [`hevc_implementation_assessment.md`](evaluation/hevc_implementation_assessment.md) | HEVC 实现生产就绪度评估（重写版）：全量代码重读 + 官方文档/社区实测调研 + 本机复测，判定"有条件生产就绪"与上线条件 | ⚠️ 部分过期（§9 AV1 衔接段已被推翻） |
| [`x265_production_assessment.md`](evaluation/x265_production_assessment.md) | x265 实现生产就绪评估（重写版）：官方文档逐参数核查 + 本机实证 + 生产判定与条件清单 | ⚠️ 部分过期（P0 有 2 项已完成未回填） |

> **已移入归档的评估文档**（2026-09-12）：`av1_feasibility_report.md`（❌ 已过期）、
> `svt_av1_archival_assessment.md`、`av1_hw_tuning_guide.md`（各 ⚠️ 部分过期）。
> 位置 [`../olddocs/docs/`](../olddocs/docs/)，状态说明见
> [`../olddocs/README.md`](../olddocs/README.md) §3.2。**其原文头部仍保留状态横幅。**

## 📁 [fixtures/](fixtures/) — 测试素材清单（分类：项目自身文档）

| 文件 | 说明 |
|---|---|
| [`a7m5_channel_sync_fixtures.md`](fixtures/a7m5_channel_sync_fixtures.md) | **A7M5 真实素材 channel-sync fixture 清单**：10 类场景（空 CH1/CH2、不同物理位置、已对齐、不同固定 delay、低相关等）；原始大文件不提交仓库，仅路径引用；冻结基线与标定依据见 §15 与 `tests/fixtures/channel_sync/` |

## 📁 [reference/](reference/) — 参考资料（分类：第三方一手资料存档）

厂商官方文档、上游源码片段、GPU 能力探测报告、社区实测抓取等**外部一手资料**，
按厂商分目。目的是让项目文档里的引用可追溯。

| 目录 | 文件数 | 内容 |
|---|---|---|
| `reference/svt-av1/` | 109 | SVT-AV1 官方文档 + PSY fork + 社区归档实践 + 速度/质量基准 |
| `reference/x265/` | 78 | x265 官方 CLI/preset/releasenotes + 社区调研抓取 |
| `reference/qsv/` | 18 | QSVEncC 官方选项 + 逐 GPU 能力探测 + 关键 issue 全文 |
| `reference/nvenc/` | 12 | NVEncC 官方选项 + 各代 GPU 能力探测 + NVIDIA/媒体实测 |
| `reference/vce/` | 7 | VCEEncC 官方选项/Readme + AMF 编码 API + RDNA3 探测 |
| `reference/misc/` | 2 | 零散网络调研存档 |

> ★ **逐份清单、命名与权威性约定（同一文档有多份副本时该信哪一份）见
> [`reference/README.md`](reference/README.md)。**
>
> 本索引不再重复 reference/ 的细节——此前两处各维护一份清单，已合并到一处，
> 避免再次出现"两处不一致"。
>
> 2026-09 梳理时清理了 4 个**内容完全相同的精确重复文件**（`official/Parameters.md`、
> `official/Appendix-Film-Grain-Synthesis.md`、`src/CHANGELOG.md`、
> `src/CommonQuestions.md`），权威副本保留在 `svt-av1/` 根下。
> 带 `# Source: <URL>` 首行的抓取件**未动**——那是出处证据，不是冗余。
