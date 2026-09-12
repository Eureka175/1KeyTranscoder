# docs/ — 文档分类索引

> 本目录存放 1KeyTranscoder 的全部文档（根目录只保留 `README.md`）。
> 分类规则：**design = 项目自身设计/实施文档；evaluation = 本项目的评估与
> 调研报告；reference = 第三方一手资料存档（按厂商分目）；archive = 已封存
> 文档（结论仍有效，但不再随代码更新）。** 配置 JSON（nvenc.json 等）是运行时
> 配置，不属于文档，留在根目录；`metadata_forensics/` 是取证数据目录，
> `work/` 是实验产物，均不入档。
> 历史代码快照与废弃脚本在根目录 **`olddocs/`**（详见 olddocs/README.md）。
>
> ★ **想先了解"代码到底怎么跑"** → [`design/architecture.md`](design/architecture.md)
> ★ **`FINAL_REPORT.md`** — 最终报告：四份评估的汇总结论、决策记录与路线图
>   （**§1/§2/§5/§7 已被 v0.6.2 实现取代**，见其头部状态横幅）
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
├── tools/                  自带工具链（ffmpeg/ffprobe 9.0.1、NVEncC 9.31、
│                             QSVEncC 8.26、VCEEncC 9.12、GPAC）—— 1.34 GB，gitignored
├── testsets/               测试素材（1063 文件 / 97.5 GB）—— gitignored
├── work/                   实验产物与阶段证据（gitignored；含 _worktrees/ 研究分支工作树）
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
| `tools/`（ffmpeg / NVEncC / QSVEncC / GPAC） | ✅ | 仅 `TOOL_FILES` + `TOOL_DIRS` 白名单；**`tools/VCEEncC_9.12_x64` 不入包** |
| `docs/` `olddocs/` `logs/` `work/` `testsets/` `dist/` `release/` `metadata_forensics/` | ❌ | **明确排除**，见 manifest `excluded_by_design` |

> 即：**文档、实验产物、测试素材、发布工具本身都不进包**，
> 包内只含运行时必需内容 + 自带工具链。改动的具体清单见
> `dist/release-manifest.json` 的 `files`（455 条记录）。

## 📁 design/ — 设计文档（分类：项目设计）

| 文件 | 说明 |
|---|---|
| **`architecture.md`** | **★ 端到端架构总览（当前版本）**：两个入口与分支、后端解析四级优先级、三级降级梯、三条源管线、保留管线的幂等/续跑、channel-sync、`--check` 三级、必须保持的不变量、逐文件地图。**新维护者从这里开始读。** |
| `hardware_backend_design.md` | 硬件后端（NVEncC/QSVEncC）设计定稿，含踩坑结论（5.x 节）。**§4.5/§9/§10.2/附录 B 部分已被后续代码推翻**（默认后端、已删除的 `--auto-downgrade`、控制台询问），以 `architecture.md` 为准；§5/§6/§7 实测矩阵仍有效 |
| `implementation_report.md` | 实施报告：降级链与回退路径的故障演练记录（§15 DJI 专线）。**§1/§4/§11 的模块名与开关已过时**；§12–§15 与现行代码一致 |
| `INTEGRATION_REPORT.md` | 集成报告（早期版本整合记录）。**§A 模块清单为 2026-08-28 快照，已过时**；§D 的 GPAC-native 时序结论仍是现行实现依据 |
| `hevc_422_rext_compatibility.md` | HEVC 4:2:2 Rext 输出形态、播放兼容矩阵与归档建议 |
| `channel_sync_p1.md` | **`--channel-sync` P1 设计文档**（algo 2.3.0-p1）：算法、阈值、轨道级降级、fixture 标定、测试矩阵。与 `core/channel_sync.py::DEFAULTS` 逐键一致 |

## 📄 发布说明（分类：项目自身文档）

| 文件 | 说明 |
|---|---|
| `release_notes_v0.6.1.md` | **v0.6.1 发布说明**：Channel Sync P1 / AV1 mainline / AV1 色彩保真 / 流式内存修复；含验证矩阵、实测性能与已知限制 |

> 阶段验证报告的正式副本已归档在 `work/docs/` 与 `work/releases/`
> （`work/` 按项目约定不入文档目录、不入发布包）：
> `work/docs/channel_sync/stage12_memory_validation.md`（Stage 1.2 内存修复验证，
> 含 600 s 长程表）、`work/docs/channel_sync/memory_audit.md`（逐阶段内存归因）、
> `work/releases/v0.6.1_release_validation.md`（正式发布验证记录）。

## 📁 archive/ — 归档区（分类：已封存文档）

> **归档区不是垃圾场：这里的结论仍然有效、可以引用，只是不再随代码演进更新。**
> 入口与逐份状态标注见 [`archive/README.md`](archive/README.md)。

| 目录 | 说明 |
|---|---|
| `archive/hardware-decode/` | **P0-A 硬件解码调研（已封存）**：Phase 1 调查 11 份 + 机器可读结果 6 份。**注意个别文档部分过期**——`README.md`/`root-cause.md` 中"丢帧在 rigaya reader layer / 确切源码行 Unconfirmed"已被 Phase 2 修正；`README.md` 的"永不用 rigaya `--avhw`"建议已被推翻。**逐份状态见 `archive/README.md` §2.1** |

> 历史**代码**快照与废弃脚本不放这里，在根目录 `olddocs/`。

## 📁 evaluation/ — 评估报告（分类：评估与调研）

| 文件 | 说明 | 状态 |
|---|---|---|
| `av1_calibration.md` | **★AV1 档位标定报告（2026-08-31 实测定案）**：SVT-AV1 四档 + 硬件 QVBR/ICQ 重标定，VMAF/XPSNR 矩阵与定案依据。数值与档位 JSON 逐键一致 | ✅ **权威（数值）** |
| `av1_implementation_assessment.md` | **AV1 三后端（svtav1/nvenc-av1/qsv-av1）实现评估**：Sony/DJI 保留管线（不打 XAVC tag）+ 端到端实测 + 标定状态 | ✅ **权威（路由/管线）** |
| `hevc_implementation_assessment.md` | HEVC 实现生产就绪度评估（重写版）：全量代码重读 + 官方文档/社区实测调研 + 本机复测，判定"有条件生产就绪"与上线条件 | ⚠️ 部分过期（§9 AV1 衔接段已被推翻） |
| `x265_production_assessment.md` | x265 实现生产就绪评估（重写版）：官方文档逐参数核查 + 本机实证 + 生产判定与条件清单 | ⚠️ 部分过期（P0 有 2 项已完成未回填） |
| `av1_feasibility_report.md` | AV1 实现可行性总报告 | ❌ **已过期**：核心路由决策被反向实现 |
| `av1_hw_tuning_guide.md` | AV1 硬件后端调参指南：NVENC/QSV/VCE 支持度矩阵 + HEVC→AV1 逐键参数翻译表 | ⚠️ 部分过期（支持度/翻译表有效，预设草案作废） |
| `svt_av1_archival_assessment.md` | SVT-AV1（含 PSY fork）归档可行性评估：主线/PSY 关系、归档调参、质量与吞吐 | ⚠️ 部分过期（调参有效，§集成定位失效） |

> 上表的"状态"列是 2026-09 审计补注。**过期文档的头部已加状态横幅**，说明
> 哪几条被推翻、现行依据是哪一份——这样按目录顺序读也不会读到已失效的结论。

## 📁 fixtures/ — 测试素材清单（分类：项目自身文档）

| 文件 | 说明 |
|---|---|
| `a7m5_channel_sync_fixtures.md` | **A7M5 真实素材 channel-sync fixture 清单**：10 类场景（空 CH1/CH2、不同物理位置、已对齐、不同固定 delay、低相关等）；原始大文件不提交仓库，仅路径引用；冻结基线与标定依据见 §15 与 `tests/fixtures/channel_sync/` |

## 📁 reference/ — 参考资料（分类：第三方一手资料存档）

### reference/svt-av1/ — SVT-AV1 官方文档与调研存档（AOMedia）

| 文件/子目录 | 说明 |
|---|---|
| `SVT-AV1_Parameters.md` | 官方参数文档（master = v4.2.0） |
| `SVT-AV1_CHANGELOG.md` | 版本历史 |
| `SVT-AV1_CommonQuestions.md` | 官方常见问题 |
| `SVT-AV1_archival_tuning_report.md` | **v4.2.0 归档调参调研**（官方文档 + community 全带 URL：CRF 刻度/preset 甜点/tune/enable-qm/ac-bias/film-grain/mbr 等） |
| `psy/`（24 份） | **SVT-AV1-PSY fork 调研**原始抓取 + 来源索引（00-sources-index.md） |
| `community/`（22 份） | **社区归档实践调研**：Doom9/r·AV1/DataHoarder 帖 + 主报告 svt-av1-archival-community-report.md |
| `official/`（6 份） | 官方文档抓取（Parameters/Encoder User Guide/FGS 附录/FFmpeg 说明等） |
| `archival-feasibility-report.md` + `primary-source-findings.md` | **主线归档可行性主报告**（六节带来源）+ 源码级一手核实记录 |
| `speed/svt-av1-speed-memory-findings.md` | 速度/内存调研汇总（preset 曲线、内存规律） |
| `quality/` `speed/` `src/` `templates/` `windows/`（55 份） | 主线调研抓取：质量对比、速度基准、源码（CHANGELOG/grainSynthesis.c 等）、归档模板、Windows 分发现状 |

### reference/x265/ — x265 官方文档（readthedocs 缓存）

| 文件 | 说明 |
|---|---|
| `x265_cli.txt` | 官方 CLI 参数文档原文 |
| `x265_presets.txt` | 官方 preset 定义原文 |
| `x265_releasenotes.txt` | 发布说明（默认值演变） |
| `x265_lossless.txt` | 官方近无损/无损说明 |
| `hevc_levels.txt` | HEVC level/tier 码率与 CPB 对照 |
| `x265_archiving_evaluation.md` | 社区归档实测调研主报告（Doom9/MSU/硬件对比/模板，带来源 URL） |
| `x265_archiving_consensus_report.md` | Doom9/社区共识全文（子报告 1） |
| `x265_vs_SVT-AV1_归档评估报告.md` | MSU/Netflix/AV1 基准全文（子报告 2） |
| `community/`（70 个文件） | 社区调研**原始抓取存档**（doom9/msu/netflix/forasoft/ixbt 等网页与文本） |

### reference/nvenc/ — NVIDIA NVEncC（rigaya）

| 文件 | 说明 |
|---|---|
| `NVEncC_Options.en.md` | 官方选项文档（英文） |
| `local_NVEncC_Options.ja.md` | 本机 9.31 版选项文档（日文） |
| `ReleaseNotes.md` | NVEnc 发布说明 |
| `gpu_rtx4090.txt` 等 6 份 `gpu_*.txt` | 各代 GPU 能力特性探测（Ada/Blackwell 桌面+笔记本） |
| `nvidia_ada_av1.txt` | NVIDIA Ada AV1 官方博客存档 |
| `nvidia_sdk13_blackwell.txt` | NVENC SDK 13/Blackwell 资料存档 |
| `ithome_toms.txt` | Tom's Hardware AV1 vs HEVC 实测转载存档 |

### reference/qsv/ — Intel QSVEnc（rigaya）

| 文件 | 说明 |
|---|---|
| `QSVEncC_Options.en.md` | 官方选项文档（英文） |
| `QSVEnc_Readme.md` | 能力矩阵 Readme |
| `QSVEnc_DG2_Arc_A380_Win.txt` 等 3 份 | 逐 GPU AV1 FF 能力 o/x 探测表 |
| `issue_87_*.{txt,json}`、`issue_96_*`、`issue_253_*` | 关键 issue（驱动门槛/ICQ 刻度/时间戳 bug）全文存档 |
| `vq_results*.html`、`a310_bframes.html` | rigaya 官方画质基准站（VQ 曲线 + B 帧专项） |

### reference/vce/ — AMD VCEEnc（rigaya）

| 文件 | 说明 |
|---|---|
| `VCEEncC_Options.en.md` / `VCEEncC_Options.ja.md` | 官方选项文档（英/日） |
| `VCEEnc_Readme.en.md` / `Readme.ja.md` | 官方 Readme（英/日） |
| `VCEEnc_readme.txt` | Readme 纯文本版 |
| `rx7900xt.txt` | RDNA3 能力特性探测 |
| `AMF_Video_Encode_API.md` | AMD Media Framework 编码 API 文档 |

### reference/misc/ — 网络调研碎片（分类：杂项存档）

| 文件 | 说明 |
|---|---|
| `obs_av1_benchmark_ja.txt` | OBS 三厂 AV1 基准与推荐设置文章存档（日文） |
| `techpowerup_rdna4_bframes.txt` | TechPowerUp：RDNA4 AV1 B 帧支持报道存档 |
