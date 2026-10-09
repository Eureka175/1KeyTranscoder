# docs/ — 文档分类索引

> **我要找什么？** → 先看下面这张表。

| 我想… | 去哪里 |
|---|---|
| **搞清楚代码到底怎么跑**（端到端数据流） | [`design/architecture.md`](design/architecture.md) ★ 新维护者从这里开始 |
| 看**当前该怎么用**这个工具 | 根 [`../README.md`](../README.md) |
| 看**评估汇总与决策记录** | [[`FINAL_REPORT.md`](FINAL_REPORT.md)](FINAL_REPORT.md)（注意其头部状态横幅） |
| 查**某后端为什么这样选、参数怎么定的** | [`evaluation/`](evaluation/)（先看该目录索引表里的"状态"列） |
| 查**某个子系统的设计**（硬件后端 / channel-sync / 4:2:2） | [`design/`](design/) |
| 搞清楚 **x265 四个档位为什么这样设计**（生产目标 / 计算剪枝 / NVENC 锚点） | [`x265-profile-design.md`](x265-profile-design.md) ★ 档位设计方法论 |
| 看**硬件解码 integration 的测试矩阵与最终判定** | [`hardware-decode/`](hardware-decode/) ★ 主交付物是 [`integration-test-matrix.md`](hardware-decode/integration-test-matrix.md) |
| 找**厂商官方文档 / 上游源码 / GPU 能力探测** | [`reference/README.md`](reference/README.md) |
| 查**已封存的历史调查**（硬件解码 Phase 1 等） | [`../olddocs/docs/`](../olddocs/docs/) ★ **先读 [`../olddocs/README.md`](../olddocs/README.md) 的状态标注** |
| 看**测试素材清单与冻结基线** | [`fixtures/a7m5_channel_sync_fixtures.md`](fixtures/a7m5_channel_sync_fixtures.md) |
| 查**音频声道同步（channel-sync）研究线和当前结论** | [`audio-sync-p3/P3-RESEARCH-OVERVIEW.md`](audio-sync-p3/P3-RESEARCH-OVERVIEW.md) ★ 入口；⚠️ **先读该目录的 `README.md` 状态横幅——它是冻结的历史文档，不是入口** |
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
├── VERSION                 版本号唯一来源（0.8.0）
├── LICENSE                 GNU LGPL v3 正文（本项目许可；GitHub 由它识别）
├── NOTICE                  项目版权 + SPDX: LGPL-3.0-or-later + 文本索引
├── licenses/GPL-3.0.txt    GNU GPL v3 全文（LGPL-3.0 并入引用；不放在
│                             LICENSE/COPYING 名下以免干扰许可识别）
│
├── core/                   ★ 运行时核心（37 个 .py）：config / probe / postprobe /
│                             paths / scaling / source_classifier / batch_hw /
│                             channel_sync / sync_estimate / mp4_channel_sync /
│                             sync_fix / audio_models / audio_plan / audio_probe /
│                             audio_timeline / audio_pcm / audio_route /
│                             audio_wav / audio_process / audio_mix /
│                             audio_execution / audio_retention / audio_encode /
│                             audio_request / output_compose / audio_format /
│                             audio_external / audio_output_structure /
│                             logging_utils / dashboard / dashboard_ui /
│                             models / versions / version / color
├── encoders/               ★ 编码后端（10 个 .py）：nvencc / qsvencc / x265 /
│                             svtav1 / hwdecode(硬件解码策略) / integrity(完整性闸门) /
│                             caps / hw(plan_initial_format) / base
├── preservation/           ★ 元数据保留（16 个 .py）：pipeline / sony / dji / gpac /
│                             isobmf / validate / checker / selfcheck / quality /
│                             colour / gyroflow / backends / audio_sync / models /
│                             poc_video
├── production/             ★ 跨域编排（2 个 .py）：output —— 音频产物 + 视频产物
│                             → 最终容器；既不属于音频域也不属于视频域
├── tests/                  自动化测试（full_autotest / run_selfcheck / sony_selfcheck）
│                             + selftest/（runner / fixtures / assertions / suites /
│                             reporting，v0.7.1 起的分层测试实现）
│                             + hwdecode/（v0.7.0 硬件解码集成测试矩阵与 harness）
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
| `1kt.py` `watchfolder.py` `start.bat` `README.md` `LICENSE` `NOTICE` `licenses/GPL-3.0.txt` `VERSION` | ✅ | 必需条目（缺失即拒绝构建） |
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
| **[`x265-profile-design.md`](x265-profile-design.md)** | **★ x265 档位设计方法论**（设计文档，按约定存放于 `docs/` 根）：为何单个 profile 不够、speed/quality/size 三维约束、UHQ 质量上限与参考基线、HQ 的选择性计算剪枝、FAST 的单位时间压缩效率、SMALL 的存储效率、**FAST ⇄ SMALL 相互校准**、NVENC UHQ 作为**外部现实锚点**、真实 Sony 4K60 10-bit 4:2:0 LongGOP corpus 驱动、边际收益决策逻辑、四个档位的 lineage。**附录 A 逐键抄录 `x265.json` / `x265_scaling.json` 当前值** |
| [`channel_sync_p1.md`](design/channel_sync_p1.md) | **`--channel-sync` P1 设计文档**（algo 2.3.0-p1）：算法、阈值、轨道级降级、fixture 标定、测试矩阵。与 `core/channel_sync.py::DEFAULTS` 逐键一致 |
| ~~`INTEGRATION_REPORT.md`~~ | 已移入归档：见 [`../olddocs/docs/INTEGRATION_REPORT.md`](../olddocs/docs/INTEGRATION_REPORT.md)（⚠️ 部分过期：§A 模块清单已被 [`architecture.md`](design/architecture.md) 取代，§D 时序结论仍有效） |

## 📄 发布说明（分类：项目自身文档）

| 文件 | 说明 |
|---|---|
| [`release_notes_next.md`](release_notes_next.md) | **下一开发周期（尚未发布，未分配版本号）**：**硬件 Video Encode Backend（NVENC / QSV）接入 `--audio-plan`** —— 视频侧 `VideoHandoff` 单向接手契约、软硬件共用一个音频实现、视频基本流 sha256 与默认路径逐字节一致、`--hw-decode` 仍走硬件、Sony/DJI 逐文件明确拒绝、实测已知限制（nvenc-av1 编码器无法创建）。另含本次新增的用户文档 |
| [`audio_plan.md`](audio_plan.md) | **`--audio-plan` 用户指南（面向使用者）**：一分钟上手、默认行为、各后端支持表、计划文件结构、选择/排除/排序、编码继承与"什么时候不会重编码"、alignment（含压缩音频规则）、mapping（source / independent / grouped）、外挂音频命名与排序规则、硬件后端用法、可直接复制的常见组合、限制与排错。文档里每个 JSON 示例都由回归保证能被真实 schema 解析 |
| [`release_notes_v0.7.1.md`](release_notes_v0.7.1.md) | **v0.7.1 发布说明（音频模型 Phase 1 + Phase 2 + Phase 3A/3B）**：`AudioStream`/`AudioChannel`/`AudioTrack`/`AudioPlan` 数据模型、`AudioSource`/Selection/Channel Mapping/`AudioMapSpec`、**`AudioTimeline`（时长·EOF·offset 唯一权威）/ `AudioPCMReader`（canonical float32）/ `AudioRouter` / `WavExporter` / `AudioMixer`（N→1 + gain + peak/clipping）/ `AudioOutputSpec`**、与 channel-sync 的只读连接、序列化 schema、新增测试矩阵、**默认音频路径 unchanged** 的证据与"未实现"清单。⚠️ **已随 tag `v0.7.1` 冻结**，不再改写 |
| [`release_notes_v0.8.0.md`](release_notes_v0.8.0.md) | **v0.8.0 发布说明（当前版本）**：任意 reference 的音频延迟矫正、Phase 4A 选择性 MP4 音频保留、Phase 4B 音频编码 + 输出编排、Phase 4C 接入生产入口、**Phase 5 格式感知 alignment + 编码继承 + 外挂音频发现与 mapping**。含实测证据（compressed 显式 alignment 的真实 offset 与互相关残差、4/6/8CH × mapping 矩阵、视频基本流 sha256 不变）、新增 reason code 契约与"未实现"清单。✅ **已随 tag `v0.8.0` 冻结**（发布前名为 `release_notes_next.md`） |
| `../docs/hardware-decode/` | **v0.7.0 hardware-decode integration 交付物**（本轮已并入 `main`，`v0.7.0 ∈ ancestors(main)`）：[`README.md`](hardware-decode/README.md) 入口、[`integration-test-matrix.md`](hardware-decode/integration-test-matrix.md) 测试矩阵、[`final-report.md`](hardware-decode/final-report.md) 最终判定、`patches/` 与 `toolchain-provenance.json` |
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

## 📁 [audio-sync-p3/](audio-sync-p3/) — 音频声道同步研究线（分类：调研 / 证据归档）

**P1 常量偏移矫正已上线且未被 P3 改动；P3 是时变延迟 `delay(t)` 的研究线，
且 P3 从未启用任何东西。** 入口是
[`audio-sync-p3/P3-RESEARCH-OVERVIEW.md`](audio-sync-p3/P3-RESEARCH-OVERVIEW.md)。

| 文件 | 说明 | 状态 |
|---|---|---|
| [`P3-RESEARCH-OVERVIEW.md`](audio-sync-p3/P3-RESEARCH-OVERVIEW.md) | **★ 唯一入口**：P3 目的、P1 基线、P3.1–P3.12 每阶段 goal/status/一句话结论/报告位置、当前结论、已验证 vs 未验证、生产状态、P4 下一步 | ✅ 权威（当前状态） |
| [`RESEARCH-SUMMARY.md`](audio-sync-p3/RESEARCH-SUMMARY.md) | 维护者五分钟版：**被证明的 / 被推翻的 / 仍未知的 / 生产安全的 / 明确推迟的** | ✅ 权威（摘要） |
| [`PRODUCTION-HANDOFF.md`](audio-sync-p3/PRODUCTION-HANDOFF.md) | 已安全 / 研究已验证 / **未获生产批准**三类，逐项列出阻塞条件 | ✅ 权威 |
| [`OPEN-DECISIONS.md`](audio-sync-p3/OPEN-DECISIONS.md) | 未决事项登记册（D1–D7）＋"**已决定、不开放**"清单。**不含推荐、不含排序** | ✅ 权威 |
| [`P4-HANDOFF.md`](audio-sync-p3/P4-HANDOFF.md) | P3 已冻结；P4 应先做策略决策而非先写代码；以及 P4 **不应重做**的清单 | ✅ 权威 |
| [`README.md`](audio-sync-p3/README.md) | ⚠️ **冻结的历史文档，不是入口**：原始 P3 阶段的进行中报告，状态行 `P3 NOT JUSTIFIED` 已被后续阶段超越。**原文未改**，只加状态横幅 | ⚠️ 历史 |
| [`PRODUCTION-REQUIREMENTS.md`](audio-sync-p3/PRODUCTION-REQUIREMENTS.md) | P3.9 写成的生产需求（逐条有出处）。**§5/§6 有三句被 P3.10/P3.11 超越**（"从未把 plan 应用到真实音频并重测"等），已在头部横幅逐条标注。**原文未改** | ⚠️ 部分过期 |
| [`FINAL-RESEARCH-SUMMARY.md`](audio-sync-p3/FINAL-RESEARCH-SUMMARY.md) | 逐阶段研究纪要，**只覆盖 P1–P3.8**，不含 P3.9–P3.12 | ⚠️ 覆盖到 P3.8 |
| `audio-sync-p3/p3.10-provenance.md`、`p3.11-provenance.md`、`p3.12-provenance.md`、`provenance.md` | 各阶段 identity + 逐文件 SHA-256 + 复现命令 | ✅ 权威（溯源） |
| [`HANDOFF-CHECKLIST.md`](audio-sync-p3/HANDOFF-CHECKLIST.md) | 12 条要求 + 集成者必须自己复核的事项（§3.3 "`core/` 未被修改"**不是无条件成立的**） | ✅ 权威 |
| [`p3.7-discrepancy-audit.md`](audio-sync-p3/p3.7-discrepancy-audit.md) | **★ P4.5 P3.7 数字冲突取证**：`known_truth.py` 报 `current` 状态序列 `1/22`，文档记 `132/132` —— 根因确认为**口径冲突**：定义 **1a**（期望由**测量掩码**导出，独立 oracle，`current`=**1/22**、全体 **111/132**）vs 定义 **1b**（期望由**策略自述掩码**导出，因引擎定义 `state == derive_states(resolved)` 而**恒真**，**132/132**）。`22` 是单策略行、`132` 是 6 策略 × 22 配对，**不是同一指标**。**修正读法**：`current` 三项检查在 22 例中失败 21 例，把 **195** 个被拒窗口标成 `TRUSTED`（故 `0 fake TRUSTED` 不成立）；**性质不变**（语义过度声称，非算法错误），**幅度**比文档所述更大。两种统计**都保留并标注**；**21 个 FAIL 未删除**；`D2` 仍 `DEFERRED`。含逐案例导出与独立 verifier | ✅ 权威（勘误 + 取证） |
| 其余 `measurement/validity/trajectory/long-duration/correction/…` | 各专题设计文档，属 P3.1–P3.8 时期 | ⚠️ 见各自内容 |

> **引用审计结论（2026-10 cleanup）**：本目录历史文档**一律未改写**。已发现并
> 以**头部横幅**方式（不改原文）标注的索引问题见
> [`P3-RESEARCH-OVERVIEW.md`](audio-sync-p3/P3-RESEARCH-OVERVIEW.md)
> 的"Historical documents that no longer state current status"一节，
> 其中包括两处**至今未被任何文档调和**的矛盾（P3.2 的 `NOT VALIDATED` 与
> P3.10/P3.11 实测到的大幅中位数改善）。**该矛盾被记录为未决项，未被"修好"。**

## 📁 [audio-sync-p4/](audio-sync-p4/) — 音频同步**生产化**（分类：生产工程 / 策略待批）

**P3 是研究线（已冻结）；P4 是生产化线。P4 实现了一个逐窗时变修正的生产模块，
它默认 OFF，且没有被任何生产模块 import。**

> **P4.1–P4.4 之后的当前状态**（这一段是索引里的现状说明，不是历史结论）：
>
> ```text
> P4.4 起：验证过的 FIXED-DELAY 子集已进入生产（--channel-sync / --audio-plan，均为 opt-in）
>          由唯一入口 core.audio_models.correction_eligibility() 判定
>          逐窗时变路径仍然 OFF（无生产模块 import，有测试断言）
>          默认路径未变（实测：0 次 PCM 调用）
>          drift correction / resampling 未启用；算法改动 = 0
>          D2 = DEFERRED、D3 = NOT ESTABLISHED、D5 = DEFERRED
> ```
>
> 读 **`production-contract.md`** 拿当前契约；读 **`known-limitations.md`**
> 拿未绿清单。上面那句"它默认 OFF"对**逐窗时变模块**仍然成立。

| 文档 | 内容 | 状态 |
|---|---|---|
| [`README.md`](audio-sync-p4/README.md) | **★ 入口**：当前状态块（P4.1–P4.4、启用范围、生产历史）+ P4 原文（保留未改）、状态表、怎么跑验证 | ✅ 权威（当前状态） |
| [`production-contract.md`](audio-sync-p4/production-contract.md) | **★ 生产契约（唯一规范入口）**：启用范围、九个状态的真实语义、`correction_eligibility()`、`offset_samples` vs `raw_offset_samples`、证据传播与**安全终止点**、fail-closed、默认路径、仍未决项 | ✅ 权威（契约） |
| [`known-limitations.md`](audio-sync-p4/known-limitations.md) | **未绿清单（单一入口）**：21 个 FAIL / BLOCKED / XFAIL / NOT ESTABLISHED / KNOWN LIMITATION，以及仓库中不存在的证据 | ✅ 权威（限制） |
| [`evidence-freeze.md`](audio-sync-p4/evidence-freeze.md) | P3 证据冻结：哪些数字可以引用、出自哪个 artifact；含 **10 处 P3 摘要与自身明细表不符**的登记 | ✅ 权威 |
| [`policy-matrix.md`](audio-sync-p4/policy-matrix.md) | 缺口策略 / 坐标合同 / 启用与否 **三张完整证据矩阵**（真实 P3 数据，不排序、不推荐） | ✅ 权威 |
| [`coordinate-decision.md`](audio-sync-p4/coordinate-decision.md) | 三个问题分开回答（测量位置 / 施加位置 / 输出样本坐标），真实样本坐标下的首个分歧点 | ✅ 权威 |
| [`gap-policy-decision.md`](audio-sync-p4/gap-policy-decision.md) | `TRUSTED` / `UNRESOLVED_GAP` / `REENTRY` 的精确语义；hold / 不修正 / 插值 的差别 | ✅ 权威 |
| [`residual-acceptance.md`](audio-sync-p4/residual-acceptance.md) | 误差预算（含出处）；**结构门 A1–A8 + N1–N6**；阈值为何**未建立**、需要什么实验才能建立 | ✅ 权威 |
| [`implementation.md`](audio-sync-p4/implementation.md) | 生产 API 与实现；为什么默认 OFF 是**结构性保证**而非开关 | ✅ 权威 |
| [`validation.md`](audio-sync-p4/validation.md) | 全部验证记录：**30/30 逐字节复现 P3.11 修正后 PCM**、180 例回归、内存/性能/长跑；**末尾追加 P4.1–P4.4 汇总**（每个套件的当前状态、命令、以及"资源阻塞 ≠ 代码失败"的区分） | ✅ 权威 |
| [`production-readiness.md`](audio-sync-p4/production-readiness.md) | **逐项状态矩阵**（只有状态词，无分数） | ✅ 权威 |
| [`policy-recommendation.md`](audio-sync-p4/policy-recommendation.md) | **工程建议 vs 产品决定**严格分离；D1/D2/D3/D5/D6/D8 全部标 `REQUIRED` | ✅ 权威 |
| [`provenance.md`](audio-sync-p4/provenance.md) | 分支 / 提交 / 实现哈希 / 语料哈希 / 复现锚点 / 未改动文件复核 | ✅ 权威（溯源） |
| [`static-audit.md`](audio-sync-p4/static-audit.md) | 静态审计：禁用模式、静默异常、依赖方向、内存边界 | ✅ 权威 |
| [`p4.1-provenance.md`](audio-sync-p4/p4.1-provenance.md) | P4.1 审计溯源：被审计源文件哈希、fixture、复现命令 | ✅ 权威（溯源） |
| [`P4-FINAL-REPORT.md`](audio-sync-p4/P4-FINAL-REPORT.md) | 22 节最终报告 | ✅ 权威 |
| [`downstream-audit.md`](audio-sync-p4/downstream-audit.md) | **P4.1 / D6 下游消费者审计**：`UNRESOLVED_GAP` 能否活着抵达消费者。**结论 `BLOCKED`** —— 逐窗状态在生产模型里**没有表示**，而且同一种失效在 shipped 代码里**已经存在**（track 粒度） | ✅ 权威（审计） |
| [`d1-uncertainty-contract.md`](audio-sync-p4/d1-uncertainty-contract.md) | **D1 不确定性生产契约（规范；§9 记录 P4.3 的实现）**：状态词汇、序列化、日志、消费者可见性；最小补丁设计（**feature-gated by construction**）。**策略中立**。§1–§8 保留原文未改 | ✅ 权威（契约） |
| [`p4.2-evidence-integrity.md`](audio-sync-p4/p4.2-evidence-integrity.md) | **P4.2 证据完整性修复**（**本仓库首个有意改变生产行为的阶段**）：复现 → 根因 → 修正采纳门 → 证据字段 → P1 137 语料审计 → 真实语料审计 → D6 复验 → 缺陷家族静态审计 → 全量回归。**状态 `COMPLETE`** | ✅ 权威（修复记录） |
| [`p4.2-provenance.md`](audio-sync-p4/p4.2-provenance.md) | P4.2 溯源：改动了什么（2 个生产文件，前后哈希）与**证明没动什么**（9 个用户文件、冻结 fixture、同步算法） | ✅ 权威（溯源） |
| [`p4.3-production-semantics.md`](audio-sync-p4/p4.3-production-semantics.md) | **P4.3 生产语义收口**：D1 证据契约**已实现**（证据随值抵达消费者）；D2 缺口策略 / D5 坐标合同 `DEFERRED` 且**实现默认值与语义契约分为两个字段**；D8 启用状态正式定义。含**决策编号三向冲突**的调和表、P2 路径同类缺陷的修复（前后**同 harness 实测**）、P1 548 行重放（0 结论变化）。**算法改动 = 0** | ✅ 权威（契约与决定） |
| [`p4.3-provenance.md`](audio-sync-p4/p4.3-provenance.md) | P4.3 溯源：base/head、改动文件与前后哈希、fixture 哈希、测试计数、D1 修订、D2/D5/D8 状态 | ✅ 权威（溯源） |
| [`p4.4-production-enablement.md`](audio-sync-p4/p4.4-production-enablement.md) | **P4.4 生产启用**：验证过的 **fixed-delay** 子集进入生产（P1 `--channel-sync` / P2 `--audio-plan`），由**唯一**的 `correction_eligibility()` 判定；"不合格却带可用数值"**不可表示**。逐状态的支持/不支持表、fail-closed 行为、默认路径实测（**0 次 PCM 调用**）、真实素材验证（正/负/零偏移 + 多声道）。**D2/D3/D5 保持 `DEFERRED`/`NOT ESTABLISHED`**；drift 与 resample 未启用 | ✅ 权威（启用契约） |
| [`p4.4-provenance.md`](audio-sync-p4/p4.4-provenance.md) | P4.4 溯源：改动文件与哈希、**证明未改**算法文件（`sync_estimate` / `sync_fix` / `audio_sync_tv`）、fixture 校验、9 个用户文件状态 | ✅ 权威（溯源） |
| [`production-contract.md`](audio-sync-p4/production-contract.md) | **★ 生产契约（唯一规范入口）**：三条路径的启用状态；九个 `SyncStatus` 与其真实语义；`correction_eligibility()` 的职责与必要条件；`offset_samples` / `raw_offset_samples` 的区别；证据如何传播、**在哪里安全终止**（`ChannelTimeline`）；不合格时的 fail-closed 行为；默认路径保护；**D2/D3/D5 仍未决**。状态名与 reason code 全部从代码读出 | ✅ 权威（契约） |
| [`known-limitations.md`](audio-sync-p4/known-limitations.md) | **未绿清单（单一入口）**：P3.7 `known_truth.py` 的 **21 个 FAIL（既有；P4.5 已查明为口径冲突，FAIL 保留不删）**；`test_coordinates.py` 的 **BLOCKED（机器内存）**及"资源阻塞 ≠ 代码失败"的证据；**T5.4b XFAIL**；D2/D3/D5；已启用路径的已知代价；仓库中不存在的证据及其替代。§7 行尾缺陷已标 `RESOLVED` | ✅ 权威（限制） |
| [`documentation-provenance.md`](audio-sync-p4/documentation-provenance.md) | **本次文档整合的来源记录**：新建/修改了哪些文档、历史语义是否保留、从 `work/` 晋升了什么、什么被有意排除及原因、`.gitignore` 审查结论、**建议但未创建的文件及理由**、canonical 映射 | ✅ 权威（文档溯源） |
| [`p4.5-baseline-integrity.md`](audio-sync-p4/p4.5-baseline-integrity.md) | **P4.5 Part A — 冻结 P1 基线跨平台完整性（`FIXED`）**：验收哈希原先取**检出字节**的 SHA-256，因此依赖行尾（Windows CRLF `36242616062FFEC2…` vs GitHub LF `7B1FB2DCAEECBA4B…`），在干净 LF checkout 上审计 `ABORT exit 4`。新增版本化契约 **`sha256-lf-normalized-v1`**（`raw_byte_sha256` 仅供溯源 / `canonical_content_sha256` 才是验收），两种行尾归一到 **`7B1FB2DCAEECBA4B…`**；真实内容改动**仍然**失败。**fixture 未改**（内容 0 / 行数 0 / 字段 0）；历史 raw 哈希**原样保留**；fail-closed 保留 | ✅ 权威（修复记录） |

> **一句话状态**：固定偏移 `VALIDATED`；时变测量 `VALIDATED`；真实 PCM 施加
> `VALIDATED`；六通道对 E2E `VALIDATED`；**缺口策略 `DEFERRED`、坐标合同
> `DEFERRED`、残差阈值 `NOT ESTABLISHED`、生产特性 `OFF`**。
> 总体：**READY FOR PRODUCTION POLICY** —— 即"实现完成、等待产品策略批准"。
>
> **P4.1（D6 下游审计）补一句**：`UNRESOLVED_GAP` **无法**活着抵达生产消费者，
> 而且同一种"有数字就等于已测量"的失效**已经在 shipped 代码里存在**（track 粒度：
> `channel_sync` 算出覆盖率、用它把门、然后采纳一个**不再复核覆盖率**的回退分支，
> 写出 `decision="fixed", reason=None`）。因此 **D6 = `BLOCKED`**。
>
> **P4.2 已修复该缺陷（状态 `COMPLETE`）**：采纳门现在作用于**被采纳的那个估计**，
> 并在写入 `fixed` 前再做一次最终证据门；证据（可用帧 / 总帧 / 覆盖率 / 置信度）
> 附加到每一行。效果经三条独立证据确认：源码审计（51 模块，0 未复核）、
> 35 例证据完整性回归、以及真实语料（六对里五对从 `SUCCESS` 改为
> `LOW_CONFIDENCE`，而覆盖率确实达标的那一对 74.4 % 仍然成功）。
> **P1 算法未改**（`sync_estimate.py` / `sync_fix.py` 哈希与 P3 一致），
> 改的是**分类**，不是修正。
>
> **P4.3（生产语义收口，状态进行中）再补一句**：P4.2 之后剩下的问题不再是
> "有没有证据门绕过"，而是"证据语义正确之后，生产契约是什么"。
>
> * **D1 证据契约 = 已决定并实现**：`AudioSyncResult` 现在携带
>   `usable_frames` / `total_frames` / `coverage`，并向上贯通到
>   `AudioTrack` / `AudioChannelRef` / `AlignmentOutcome`；`None` 表示
>   "未陈述"而**不是** 0，因此**历史记录与外部输入不因新增字段而改变结论**
>   （冻结的 P1 137 语料 **548 行重放、0 处结论变化**）。
> * **同类缺陷的第二处实例被找到并修复**：`core/audio_sync.py`（P2 对齐路径）
>   的质量门**从来没有覆盖率这一项**（文档却声称与文件级管线同一套），于是
>   coverage 20.5 % 的估计会被发布为 `success` 并写进 offsets。修复前后由
>   **同一个 harness 分别对 `97e1504` 抽取树与当前树实测**：改前
>   `success/offset=480/applied=1`，改后 `failed/无 offset/applied=0`，
>   而对照组（覆盖率 94.9 %）在两次运行中都成功。
> * **D2 缺口策略 / D5 坐标合同 = `DEFERRED`**，并且**实现默认值**与**语义契约**
>   在代码里是两个字段（`core/audio_sync_tv.DECISION_STATUS`），有测试保证二者
>   不会被混为一谈。D2 不被决定的理由是 P4 的实测结论：**没有一列占优**，
>   区分各候选的那些行取决于缺口内不连续点的位置 —— 而那**不可观测**。
> * **D8 启用状态 = 正式定义**：默认路径不变；`core/audio_sync_tv.py` 仍然
>   **不被任何生产模块 import**（结构性 OFF，不是开关）；启用 `D8-C` 未取。
> * **算法改动 = 0**：`sync_estimate.py` / `sync_fix.py` / GCC-PHAT / P1 修正
>   数学 / 六对 E2E 修正机制全部未动。
>
> **P4.4（生产启用）最后一补**：把"能不能修"这个问题从**每个 consumer 各自
> 判断**收敛成**唯一入口** `core.audio_models.correction_eligibility()`。
>
> * **验证过的 fixed-delay 子集进入生产**：P1（`--channel-sync`）与 P2
>   （`--audio-plan` + 显式 `sync.reference`）两条 opt-in 路径都由该入口管辖。
>   在此之前三条消费路径各自只看 `status`，**没有一条看证据**。
> * **不变量是结构性的**：不合格的判定**不携带 offset**（`offset_samples =
>   None`），"测出来了但不采用"的值仍可读 `raw_offset_samples` 供报告陈述。
>   `FixedDelayPlan.offsets()` 只含合格条目。U1 的
>   "数字存在 == 测量存在" 因此在类型层面不可表示。
> * **默认路径实测未变**：把所有 PCM 入口打桩后跑无 opt-in 的生产调用，
>   `run_channel_sync` / `estimate_sync` / `apply_sync_result` /
>   `resolve_timeline` / `AudioPCMReader` 全部 **0 次调用**，0.035 ms，未触碰
>   任何文件。
> * **真实素材验证**：注入 +1200 / −600 / 0 样本的四声道 A7M5 素材，实际施加
>   的移位与注入值逐条相符（符号与大小），修正后由 harness **自写的**互相关
>   独立复测残差为 **0 样本**。
> * **time-varying / 逐窗路径仍未启用**：`CorrectionPlan` 的构造函数要求
>   `policy=` 与 `coordinate=`，接进生产等于顺手决定 D2 与 D5 —— 因此本阶段
>   用**固定延迟计划**（无 policy、无 coordinate）满足链路中的计划一步。
> * **D2 = `DEFERRED`、D3 = `NOT ESTABLISHED`、D5 = `DEFERRED` 全部保持不变**；
>   drift correction 与 resampling 未启用。
>
> **P4 没有替产品做决定，也没有改动任何默认行为。** 模块 `core/audio_sync_tv.py`
> 与套件 `tests/audio_sync_p4/` 都是 tracked 代码，且**不 import `work/`**，
> 因此它不受 P3 D7（实现存放在 gitignored `work/`）的影响。

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
