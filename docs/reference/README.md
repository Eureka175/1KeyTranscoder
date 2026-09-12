# docs/reference/ — 第三方一手资料存档

> **本目录是"外部资料"存档，不是本项目的研究成果。** 这里放的是厂商官方文档、
> 上游源码片段、GPU 能力探测报告、社区实测抓取等**别人写的东西**（都是我们自己
> 抓下来存证的）。本项目自己的分析与结论在 `docs/evaluation/`、`docs/design/`
> 与归档区 [`olddocs/docs/`](../../olddocs/docs/)。
>
> 目的只有一个：**让项目文档里的引用可追溯**。评估报告说"MSU 测得同画质省
> 35–49% 码率"，依据就在这里的某个文件里，而不是一个外部链接。

---

## 1. 命名与权威性约定（重要，先读这段）

同一份上游文档在本目录里**可能有多份副本**，因为它们来自不同的抓取批次、
带不同的来源标注。判断"该信哪一份"按下面的优先级：

| 形态 | 例子 | 定位 |
|---|---|---|
| **项目归档名**（放在 `<厂商>/` 根下） | `svt-av1/SVT-AV1_Parameters.md` | ✅ **权威副本**。项目文档引用的是这个名字 |
| 上游原名（放在 `official/`、`src/`） | `svt-av1/official/Parameters.md` | 保留上游原始目录结构。**与项目归档名内容相同的精确副本已在 2026-09 梳理时清理** |
| **带来源 URL 的抓取件**（`templates/`、`speed/`） | `templates/svt-av1-parameters-official.md` | ⚠️ **不是冗余**：首行带 `# Source: <URL>`，是"从哪个 URL 抓的"的证据。差异仅此一行，保留 |

**所以：查参数、查默认值 → 用项目归档名；查"这份资料是哪来的" → 用带 Source 的抓取件。**

---

## 2. 目录总览

| 目录 | 文件数 | 内容 | 对应项目文档 |
|---|---|---|---|
| `svt-av1/` | 109 | SVT-AV1 官方文档 + PSY fork + 社区归档实践 + 速度/质量基准 | `evaluation/av1_calibration.md`、`olddocs/docs/svt_av1_archival_assessment.md` |
| `x265/` | 78 | x265 官方 CLI/preset/releasenotes + 社区归档调研（70 份抓取） | `evaluation/x265_production_assessment.md` |
| `qsv/` | 18 | QSVEncC 官方选项 + 逐 GPU 能力探测 + 关键 issue 全文 | `evaluation/hevc_implementation_assessment.md`、`olddocs/docs/hardware-decode/qsv.md` |
| `nvenc/` | 12 | NVEncC 官方选项 + 各代 GPU 能力探测 + NVIDIA/媒体实测存档 | `evaluation/hevc_implementation_assessment.md`、`olddocs/docs/hardware-decode/nvdec.md` |
| `vce/` | 7 | VCEEncC 官方选项/Readme + AMF 编码 API + RDNA3 探测 | 根 `README.md`（VCE 预留） |
| `misc/` | 2 | 零散网络调研存档 | — |

---

## 3. svt-av1/ — SVT-AV1（AOMedia）

| 路径 | 说明 |
|---|---|
| **项目归档副本（权威）** | |
| `SVT-AV1_Parameters.md` | ✅ 官方参数文档（master = v4.2.0）。**查参数用这份** |
| `SVT-AV1_CHANGELOG.md` | ✅ 版本历史 |
| `SVT-AV1_CommonQuestions.md` | ✅ 官方常见问题 |
| `SVT-AV1_archival_tuning_report.md` | v4.2.0 归档调参调研（官方文档 + community 全带 URL） |
| `archival-feasibility-report.md` + `primary-source-findings.md` | 主线归档可行性主报告（六节带来源）+ 源码级一手核实 |
| `official/` | 上游原始目录结构（README / Encoder User Guide / Ffmpeg / System-Requirements）。精确重复的 Parameters / Film-Grain 附录已清理，**以根下归档副本为准** |
| `src/` | **源码级一手证据**：`enc_settings.c`、`grainSynthesis.c`、`definitions.h`。精确重复的 CHANGELOG / CommonQuestions 已清理 |
| `psy/` | SVT-AV1-PSY fork 调研（24 份）+ 来源索引 `00-sources-index.md` |
| `community/` | 社区归档实践调研（22 份：Doom9 / r·AV1 / DataHoarder）+ 主报告 `svt-av1-archival-community-report.md` |
| `quality/` `speed/` | 主线调研抓取：质量对比、速度/内存基准（`speed/svt-av1-speed-memory-findings.md` 是汇总） |
| `templates/` | 带来源 URL 的抓取件（归档模板、编码指南、issue 全文） |
| `windows/` | Windows 分发现状（ffmpeg 构建、HandBrake、StaxRip、av1an 等） |

> **`Parameters.md` 的多个版本怎么读**：根下 `SVT-AV1_Parameters.md` 与
> `official/Parameters.md` 曾完全一致（哈希相同），后者已清理。`templates/` 与
> `speed/` 下的同名文件**内容相同但首行带 Source URL**，保留作为抓取出处证据。

## 4. x265/ — x265 官方文档与社区调研

| 路径 | 说明 |
|---|---|
| `x265_cli.txt` | ✅ 官方 CLI 参数文档原文 |
| `x265_presets.txt` | ✅ 官方 preset 定义原文 |
| `x265_releasenotes.txt` | 发布说明（默认值演变） |
| `x265_lossless.txt` | 官方近无损/无损说明 |
| `hevc_levels.txt` | HEVC level/tier 码率与 CPB 对照 |
| `x265_archiving_evaluation.md` | 社区归档实测调研主报告（带来源 URL） |
| `x265_archiving_consensus_report.md` | Doom9/社区共识全文（子报告 1） |
| `x265_vs_SVT-AV1_归档评估报告.md` | MSU/Netflix/AV1 基准全文（子报告 2） |
| `community/`（70 份） | 社区调研**原始抓取存档**（doom9 / msu / netflix / forasoft / ixbt 等），含 `extract*.py` 抓取脚本 |

## 5. qsv/ — Intel QSVEnc（rigaya）

| 路径 | 说明 |
|---|---|
| `QSVEncC_Options.en.md` | ✅ 官方选项文档（英文）。**查参数用这份** |
| `QSVEnc_Readme.md` | 上游能力矩阵 Readme（其相对链接指向未归档的 `GPUFeatures/`，属上游固有断链） |
| `QSVEnc_DG2_Arc_A380_Win.txt`、`QSVEnc_BMG_Arc_B580_Win.txt`、`QSVEnc_ARL_u5_245K_Win.txt` | ✅ 逐 GPU AV1/HEVC 能力 o/x 探测表 |
| `issue_87_*.{txt,json}` | 驱动门槛 issue 全文 |
| `issue_96_*` | ICQ 刻度 issue 全文 |
| `issue_253_*` | 时间戳 bug issue 全文 |
| `vq_results*.html`、`a310_bframes.html` | rigaya 官方画质基准站（VQ 曲线 + B 帧专项） |

## 6. nvenc/ — NVIDIA NVEncC（rigaya）

| 路径 | 说明 |
|---|---|
| `NVEncC_Options.en.md` | ✅ 官方选项文档（英文） |
| `local_NVEncC_Options.ja.md` | 本机 9.31 版选项文档（日文） |
| `ReleaseNotes.md` | NVEnc 发布说明 |
| `gpu_rtx4090.txt`、`gpu_rtx5090.txt`、`gpu_rtx4080.txt`、`gpu_rtx5070ti.txt`、`gpu_RTX5060.txt`、`gpu_rtx4060_mobile_linux.txt` | 各代 GPU 能力特性探测（Ada / Blackwell，桌面 + 笔记本） |
| `nvidia_ada_av1.txt` | NVIDIA Ada AV1 官方博客存档 |
| `nvidia_sdk13_blackwell.txt` | NVENC SDK 13 / Blackwell 资料存档 |
| `ithome_toms.txt` | Tom's Hardware AV1 vs HEVC 实测转载 |

## 7. vce/ — AMD VCEEnc（rigaya）

| 路径 | 说明 |
|---|---|
| `VCEEncC_Options.en.md` / `.ja.md` | 官方选项文档（英/日） |
| `VCEEnc_Readme.en.md` / `Readme.ja.md` / `VCEEnc_readme.txt` | 官方 Readme（三种形态） |
| `rx7900xt.txt` | RDNA3 能力特性探测 |
| `AMF_Video_Encode_API.md` | AMD Media Framework 编码 API 文档 |

> VCE 后端在本项目中**仍未接线**（`vce.json` 已备），这些资料是为将来扩展留的。

## 8. misc/ — 零散存档

| 文件 | 说明 |
|---|---|
| `obs_av1_benchmark_ja.txt` | OBS 三厂 AV1 基准与推荐设置（日文） |
| `techpowerup_rdna4_bframes.txt` | TechPowerUp：RDNA4 AV1 B 帧支持报道 |

---

## 9. 维护约定

1. **抓取件一律保留首行的 `# Source: <URL>`**（如果原来有）。那是出处证据，
   不要为了让文件"看起来整齐"而抹掉。
2. **新增资料优先放进对应厂商目录**，用上游原名；只有当项目文档需要引用时，
   才在厂商目录下再放一份**项目归档名**的权威副本，并在此索引登记。
3. **不要在这里写项目自己的结论**。结论放 `docs/evaluation/`。
4. **上游 README 类文件的相对链接天然会断**（指向未归档的兄弟目录），这是
   存档的固有代价，不是缺陷——已在上面 §5 标注一处为例。不要为此改写上游原文。
