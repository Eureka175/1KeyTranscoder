# olddocs/ — 历史档案存档（项目唯一归档区）

> **本目录是项目唯一的归档位置。** 存放已退出活跃开发的代码与文档，不参与
> 构建/运行，仅供考古与决策追溯。
>
> * **不要从这里导入代码**；需要旧实现时以 git 历史为准
>   （tag: `pre_S1S5` / `pre_ui` / `v0.2.0-alpha` / `v0.3.0-beta`）。
> * **归档 ≠ 作废。** 这里很多结论仍然有效、可以引用，只是不再随代码演进更新。
>   逐份状态见 §2 与 §3。
> * 活跃文档在 `docs/`（分类：design / evaluation / reference / fixtures）。

---

## 1. 目录结构

```
olddocs/
├── README.md              本文件（唯一归档索引）
├── backup/                历史代码快照（pre_S1S5 / pre_ui_1kt）
├── sony_poc.py            早期 Sony 元数据保留独立 POC
├── x265_archive.py        早期 x265 单档批处理编排器
└── docs/                  ★ 已归档的文档
    ├── _ARCHIVE-INDEX.md      归档状态索引（原有，含逐份状态标注）
    ├── hardware-decode/       P0-A 硬件解码调研（Phase 1，16 项）
    ├── av1_feasibility_report.md          ❌ 已过期
    ├── svt_av1_archival_assessment.md     ⚠️ 部分过期
    ├── av1_hw_tuning_guide.md             ⚠️ 部分过期
    └── INTEGRATION_REPORT.md              ⚠️ 部分过期
```

---

## 2. 代码类归档

| 路径 | 内容 | 归档原因 |
|---|---|---|
| `backup/` | 各阶段全量代码快照（pre_S1S5 20260829 / pre_ui_1kt）与原始 README | S1–S5 与 UI 重构前的历史基线；git tag 已覆盖，目录冗余 |
| `x265_archive.py` | 早期 x265 单档批处理编排器 | 已被 `1kt.py` 完全取代；核心模块无任何导入 |
| `sony_poc.py` | Sony 元数据保留独立 POC | 已并入 `preservation/` 保留管线；`preservation/__init__.py` 文档注释曾指向此处 |

## 3. 文档类归档（`docs/`）

> 详细状态索引见 **[`docs/_ARCHIVE-INDEX.md`](docs/_ARCHIVE-INDEX.md)**。
> 这里只给"为什么进来"和"现在该读哪一份"。

### 3.1 `hardware-decode/` — P0-A 硬件解码调研（Phase 1，16 项）

**状态**：Phase 1 调查 + Phase 2 源码考古 + 运行时验证**全部完成**，四条 research
分支已收口待合并，**生产集成尚未开始**。

| 文档 | 状态 | 说明 |
|---|---|---|
| `README.md` | ⚠️ **部分过期** | 调查总纲。三方结论、headline findings 仍成立；但"永不用 rigaya `--avhw`、改走 FFmpeg"的建议**已被 Phase 2 推翻** |
| `root-cause.md` | ⚠️ **部分过期** | RC-1/RC-2 把丢帧归因于 "rigaya reader layer"，RC-9 标记确切源码行 `Unconfirmed` —— Phase 2 已修正为厂商特有 pipeline task，且两处均已运行时验证 |
| `investigation.md` | ✅ 当前（作为记录） | 完整结构化报告（12 节），含 §10 缺陷清单 P1–P8，仍是待办依据 |
| `corpus.md` `ground-truth.md` `qsv.md` `nvdec.md` `divergence.md` `test-results/` | ✅ 当前 | 语料、软解基准、逐后端结果、逐帧指纹、机器可读结果 |
| `design.md` `implementation-plan.md` | 📌 **待集成使用** | 目标解码器架构与 Phase 2 计划（§17.x）。**集成 session 的起点清单**，归档仅因"Phase 2 未执行" |
| `static-analysis.md` | ✅ 当前 | 生产树全部视频解码点的静态分析，"硬件解码不可达"的原始证据 |

**Phase 1 → Phase 2 的关键修正**（一句话版）：

| Phase 1 结论 | Phase 2 修正 |
|---|---|
| 丢帧在 "rigaya reader layer" | 在**厂商特有的 pipeline task**（NVEncC `PipelineTaskNVDecode::getOutputFrame()` / QSVEncC `PipelineTaskMFXDecode::sendBitstream()`）；共享 reader 已排除 |
| 确切源码行 `Unconfirmed` | **已定位并运行时验证**（两处补丁） |
| 建议永不用 rigaya `--avhw` | **已推翻**：patched rigaya 路径正确且 CPU 省 2.5–2.7×，是集成候选 |
| 收益"不是吞吐" | 确认并量化：收益是 **CPU 余量**，不是单任务提速 |

**Phase 2 文档（2026-09-12 已全部并入 `main`，现就在本目录）**：

| 文档 | 内容 |
|---|---|
| `research-conclusion.md` | ★ **四条 research 分支的最终交叉结论**：已验证状态表、三个独立根因、工程建议与结论边界、集成前置条件、开放事项。**进入 integration 前先读这一份** |
| `e2e-benchmark.md` | S9 端到端基准；§0.1 已观测/推断未证/未测三者分离；§8.1 集成行动指南 |
| `nvencc-patch.md` / `nvencc-avhw-experiment.md` | NVEncC 补丁溯源（含 clean-apply 证明）与实验记录 |
| `qsvencc-patch.md` / `qsvencc-root-cause.md` / `qsvencc-avhw-experiment.md` | QSVEncC 补丁溯源、根因定位与行为记录 |
| `rigaya-avhw-analysis.md` / `nvencc-second-path-analysis.md` | 源码考古与 `setPocAndFix` 分离分析 |

> 这四份文档原先只存在于四个 research 分支（`rigaya-avhw-source`、
> `rigaya-nvencc-avhw`、`rigaya-qsvencc-avhw`、`hwdecode-e2e-benchmark`），
> 合并时它们内部写的 `docs/hardware-decode/research-conclusion.md` 路径已失效；
> 本文档与 `_ARCHIVE-INDEX.md` 里保留了这段迁移说明，**正文引用以本目录相对路径为准**。

### 3.2 已被取代的评估 / 集成文档

| 文档 | 状态 | 被谁取代 |
|---|---|---|
| `av1_feasibility_report.md` | ❌ **已过期** | 核心路由决策被反向实现：文档称"AV1 不进 Sony 保留管线"，实际 Sony 源走 AV1 保留管线（保留 rtmd/nrtm/uuid，仅不打 XAVC tag）。现行依据：`docs/evaluation/av1_implementation_assessment.md` + `av1_calibration.md` |
| `svt_av1_archival_assessment.md` | ⚠️ **部分过期** | 归档调参结论仍有效；§集成定位三项失效（`--encoder svt-av1`→`svtav1`、`svt_av1.json`→`svtav1.json`、"不进保留管线"→实际进入） |
| `av1_hw_tuning_guide.md` | ⚠️ **部分过期** | ①支持度矩阵与②逐键翻译表仍有效；③预设 JSON 草案作废（草案走 CQP，实际走 QVBR/ICQ） |
| `INTEGRATION_REPORT.md` | ⚠️ **部分过期** | §A 模块清单为 2026-08-28 快照，已被 [`docs/design/architecture.md`](../docs/design/architecture.md) 取代；**§D 的 GPAC-native 时序结论仍是现行实现依据**（`preservation/pipeline.py:22-36` 与其逐条一致） |

> **未归档但标注为"部分过期"的两份评估仍在 `docs/evaluation/`**：
> `hevc_implementation_assessment.md`（§9 AV1 衔接段失效）与
> `x265_production_assessment.md`（P0 有 2 项已完成未回填）。它们的主体结论
> （生产就绪判定与条件清单）仍被引用且无可替代，故留在活跃目录并带状态标注。

> 这些文档**原文一律保留、未做删改**，只在头部加了状态横幅，说明哪几条被
> 推翻、现行依据是哪一份。历史结论被推翻是研究过程的正常部分，掩盖它比留下它更糟。

---

## 4. 归档规则（后续维护请遵守）

1. **归档位置唯一**：所有历史文档进 `olddocs/docs/`，不要再新建第二处归档区。
2. **只移不重写**：保留原文与其原有的置信度/结论标记。结论若已被推翻，
   **在本 README 与 `docs/_ARCHIVE-INDEX.md` 登记修正**，不要篡改原文。
3. **登记状态**：每份归档文档必须在上表或索引里有一行，标注
   ✅当前 / ⚠️部分过期 / ❌已过期 / 📌待后续使用。
4. **给出后继**：有替代文档时，必须写出替代文档的路径。
5. **优先整目录迁移**：内部相对链接自动保持有效；迁移后跑一次链接检查。
6. **不要为归档而归档**：只要还有活跃维护者，文档就留在 `docs/` 分类目录里。

---

## 5. 已归档的分支（`archived/*` tag）

2026-09-12：**所有分支已并入 `main`，仓库只剩 `main` 一个分支。**
原分支内容 100% 进入 main（逐个文件 blob 哈希核对一致，见下表），
分支本身以 **annotated tag** 保留，可随时 `git checkout` 查看：

| 归档 tag | 原分支 | 原 HEAD | 内容 |
|---|---|---|---|
| `archived/rigaya-avhw-source` | `research/rigaya-avhw-source` | `e0eacb2` | 源码考古：丢弃 Sony 先行图像的确切源码位置、`setPocAndFix` 分离分析 |
| `archived/rigaya-nvencc-avhw` | `research/rigaya-nvencc-avhw` | `7b69242` | NVEncC 补丁 + 运行时验证 + 溯源记录 |
| `archived/rigaya-qsvencc-avhw` | `research/rigaya-qsvencc-avhw` | `1686f73` | QSVEncC 根因 + 运行时验证 + 溯源记录 |
| `archived/hwdecode-e2e-benchmark` | `research/hwdecode-e2e-benchmark` | `c5f22b7` | 端到端基准 + 跨分支最终结论 |
| `archived/av1` | `av1` | `31ecd08` | AV1 独立开发线（已合并） |
| `archived/pre-av1-main-merge` | `backup/pre-av1-main-merge` | `988b9ba` | AV1 合并前的备份点 |
| `archived/v0.3.0-beta` | `backup/v0.3.0-beta` | `b7443a1` | v0.3.0-beta 历史点 |

```bash
# 查看某个归档分支的完整历史
git log --oneline archived/rigaya-nvencc-avhw
# 看某个文件在该分支上的版本
git show archived/rigaya-nvencc-avhw:docs/hardware-decode/nvencc-patch.md
# 临时检出（只读查看）
git checkout -b tmp archived/rigaya-qsvencc-avhw
```

**四个 research 工作树已移除。** 它们工作区里**未被 git 跟踪**的复现材料
（harness 脚本、patch 文件、实测 JSON）已保留在
`work/_worktrees_evidence/<工作树名>/`（18.3 MB / 278 文件），
避免"分支合并了但复现方法丢了"。

> **注意**：Phase 2 文档内部写有 `docs/hardware-decode/research-conclusion.md`
> 这类旧路径，在经历 `docs/hardware-decode/` → `docs/archive/` → `olddocs/docs/`
> 两次搬迁后已失效。**正文引用以本目录相对路径为准。**

---

归档时间：2026-09-01 首次（av1 分支，main 同步）；2026-09-12 合并
`docs/archive/` 与本目录、并入 4 份已取代的评估/集成文档，成为唯一归档区；
同日四条 research 分支与三条历史分支并入 main，仅以 `archived/*` tag 保留。
