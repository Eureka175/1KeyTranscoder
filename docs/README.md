# docs/ — 文档分类索引

> 本目录存放 1KeyTranscoder 的全部文档（根目录只保留 `README.md`）。
> 分类规则：**design = 项目自身设计/实施文档；evaluation = 本项目的评估与
> 调研报告；reference = 第三方一手资料存档（按厂商分目）；misc = 网络调研
> 碎片存档。** 配置 JSON（nvenc.json 等）是运行时配置，不属于文档，留在
> 根目录；`metadata_forensics/` 是取证数据目录，`work/` 是实验产物，均不入档。
> 历史代码快照与废弃脚本在根目录 **`olddocs/`**（详见 olddocs/README.md）。
>
> ★ **`FINAL_REPORT.md`** — 最终报告：四份评估的汇总结论、决策记录与路线图。

## 📁 design/ — 设计文档（分类：项目设计）

| 文件 | 说明 |
|---|---|
| `hardware_backend_design.md` | 硬件后端（NVEncC/QSVEncC）设计定稿，含踩坑结论（5.x 节） |
| `implementation_report.md` | 实施报告：降级链与回退路径的完整故障演练记录（§15 DJI 专线） |
| `INTEGRATION_REPORT.md` | 集成报告（早期版本整合记录） |
| `hevc_422_rext_compatibility.md` | HEVC 4:2:2 Rext 输出形态、播放兼容矩阵与归档建议 |

## 📁 evaluation/ — 评估报告（分类：评估与调研）

| 文件 | 说明 |
|---|---|
| `av1_feasibility_report.md` | AV1 实现可行性总报告（含 ★XAVC 合规边界决策） |
| `av1_hw_tuning_guide.md` | AV1 硬件后端调参指南：NVENC/QSV/VCE 支持度矩阵 + HEVC→AV1 逐键参数翻译表 + 预设 JSON 草案 |
| `hevc_implementation_assessment.md` | **HEVC 实现生产就绪度评估（重写版）**：全量代码重读 + 官方文档/社区实测调研 + 本机复测，判定"有条件生产就绪"与上线条件 |
| `x265_production_assessment.md` | **x265 实现生产就绪评估（重写版）**：官方文档逐参数核查 + 本机实证 + 生产判定与条件清单 |
| `svt_av1_archival_assessment.md` | **SVT-AV1（含 PSY fork）归档可行性评估**：主线/PSY 关系、归档调参、质量与吞吐、集成定位 |

## 📁 reference/ — 参考资料（分类：第三方一手资料存档）

### reference/svt-av1/ — SVT-AV1 官方文档与调研存档（AOMedia）

| 文件/子目录 | 说明 |
|---|---|
| `SVT-AV1_Parameters.md` | 官方参数文档（master = v4.2.0） |
| `SVT-AV1_CHANGELOG.md` | 版本历史 |
| `SVT-AV1_CommonQuestions.md` | 官方常见问题 |
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
