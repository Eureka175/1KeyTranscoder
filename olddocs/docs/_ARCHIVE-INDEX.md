# olddocs/docs/ — 归档文档状态索引

> **本文件是从 `docs/archive/README.md` 迁移过来的。** 归档区已于 2026-09-12
> 合并进 `olddocs/`，成为项目**唯一**归档位置；总览（含代码类归档与 4 份已取代
> 的评估/集成文档）见上一级 **[`../README.md`](../README.md)**。
>
> 本文件保留的价值：§2.1 的 **hardware-decode 逐份状态表**（16 项，比总览更细）。

> **归档区是"已完成/已封存"文档的存放处，不是垃圾场。** 这里的东西**结论仍然
> 有效**、可以引用、可以追溯，只是**不再随代码演进更新**。
>
> 活跃文档在 `docs/design/`、`docs/evaluation/` 等分类目录；
> 历史**代码**快照在本目录的 `../backup/`。

---

## 1. 为什么要有这个区

本项目有一条容易踩的坑：**同一份调查分多个阶段推进，后面阶段推翻前面阶段的
结论**。如果新旧文档平铺在同一个目录里，读者按目录顺序读，会读到已被推翻的
结论，而且读不出它被推翻了。

归档区用**状态索引**解决这个问题：每份归档文档都标注
**当前 / 部分过期 / 已收口**，以及"现在该读哪一份"。

---

## 2. 归档内容

### 2.1 `hardware-decode/` — P0-A 硬件解码调研（**已封存**）

**一句话状态**：Phase 1 调查 + Phase 2 源码考古 + 运行时验证**全部完成**，
四条 research 分支已收口待合并，**生产集成尚未开始**。

| 文档 | 阶段 | 状态 | 说明 |
|---|---|---|---|
| `hardware-decode/README.md` | Phase 1 | ⚠️ **部分过期** | 调查总纲与可行性判定。三方结论、headline findings、结论表**仍然成立**；但"Recommended fix = 永不用 rigaya `--avhw`、改走 FFmpeg"已被 Phase 2 推翻（详见 §2.2） |
| `hardware-decode/investigation.md` | Phase 1 | ✅ 当前（作为记录） | 完整结构化报告（12 节），含 §10 现有缺陷清单 P1–P8。缺陷清单仍是待办依据 |
| `hardware-decode/corpus.md` | Phase 1 | ✅ 当前 | Sony 语料构成（151 文件 / 188,475 帧 / 64.87 GiB / 两种模式） |
| `hardware-decode/ground-truth.md` | Phase 1 | ✅ 当前 | 软解基准如何建立与验证 |
| `hardware-decode/qsv.md` | Phase 1 | ✅ 当前 | QSV 逐项结果与吞吐 |
| `hardware-decode/nvdec.md` | Phase 1 | ✅ 当前 | NVDEC 逐项结果与吞吐 |
| `hardware-decode/divergence.md` | Phase 1 | ✅ 当前 | 逐帧指纹与首次分歧分析 |
| `hardware-decode/root-cause.md` | Phase 1 | ⚠️ **部分过期** | 根因分析框架（Observation/Evidence/Hypothesis/…）。RC-1/RC-2 把丢帧归因于"rigaya reader layer"，RC-9 标记确切源码行为 `Unconfirmed` —— **Phase 2 已修正为厂商特有 pipeline task，且两处均已运行时验证** |
| `hardware-decode/design.md` | Phase 1→2 | 📌 **待集成使用** | 目标解码器架构设计。**集成 session 需要它**，归档原因只是"Phase 2 未执行" |
| `hardware-decode/implementation-plan.md` | Phase 1→2 | 📌 **待集成使用** | Phase 2 计划：§17.1 架构问题 P1–P8、§17.3 解码器 API、§17.4 完整性三层、§17.5 回退契约、§17.9 步骤 S1–S10。**集成 session 的起点清单** |
| `hardware-decode/test-results/*.json` | Phase 1 | ✅ 当前 | 机器可读结果 6 份（comparison / corpus / nvdec / qsv / software-ground-truth / summary） |

> **这些文档没有经过重写。** 归档只做了两件事：搬到归档区、在本文档登记状态。
> Phase 1 的 `Unconfirmed`/`Confirmed` 分级**按原文保留**，不追改——历史结论
> 被推翻是研究过程的正常部分，掩盖它比留下它更糟。

### 2.2 硬件解码：现在该读哪一份

```
要判断"能不能上硬件解码"
   → research/hwdecode-e2e-benchmark 分支的
     docs/hardware-decode/research-conclusion.md   ★ 唯一入口，先读这份

要看"根因到底是什么"
   → nvencc-patch.md  /  qsvencc-patch.md（各 research 分支）
   → 根因考古：rigaya-avhw-analysis.md（research/rigaya-avhw-source）

要看"Phase 1 当初查了什么"
   → 本目录的 investigation.md / root-cause.md（注意 status 列）

要开始做集成
   → 本目录的 design.md + implementation-plan.md
```

Phase 1 → Phase 2 的关键修正（一句话版）：

| Phase 1 结论 | Phase 2 修正 |
|---|---|
| 丢帧在 "rigaya reader layer" | 在**厂商特有的 pipeline task**（NVEncC `PipelineTaskNVDecode::getOutputFrame()` / QSVEncC `PipelineTaskMFXDecode::sendBitstream()`）；**共享 reader 已排除** |
| 确切源码行 `Unconfirmed` | **已定位并运行时验证**（两处补丁） |
| 建议：永不用 rigaya `--avhw`，改走 FFmpeg `-hwaccel` | **已推翻**：patched rigaya 路径正确且 CPU 省 2.5–2.7×，是集成候选；FFmpeg 保留为独立正确性/回退路径 |
| 硬件解码收益"不是吞吐" | **确认并量化**：收益是 **CPU 余量**，不是单任务提速（同二进制单路反而慢 ~11%） |

> ✅ **已解决（2026-09-12）。** 四条 research 分支已全部并入 `main`，Phase 2 文档
> 现就在本目录（`research-conclusion.md`、`e2e-benchmark.md`、两份 `*-patch.md`
> 等），无需再去分支上找。它们内部原有的
> `docs/hardware-decode/research-conclusion.md` 路径在两次搬迁后已失效，
> **正文引用请以本目录相对路径为准**。
> 归档后的分支只留 tag：`archived/rigaya-avhw-source`、`archived/rigaya-nvencc-avhw`、
> `archived/rigaya-qsvencc-avhw`、`archived/hwdecode-e2e-benchmark`。

---

## 3. 不在归档区的东西

| 内容 | 位置 | 原因 |
|---|---|---|
| 历史**代码**快照、废弃脚本 | `olddocs/` | 是代码不是文档；已由 git tag 覆盖 |
| 实验产物与阶段证据 | `work/`（gitignored） | 不入库、不入发布包 |
| 第三方一手资料 | `docs/reference/` | 是**外部**存档，不是本项目的历史 |
| 发布说明 | `docs/release_notes_*.md` | 随版本发布，长期有效 |

---

## 4. 归档规则（后续维护请遵守）

1. **只移不重写。** 归档一份文档时，保留其内容与原有的置信度标记。
   如结论已被推翻，**在本文档登记修正**，不要篡改原文。
2. **登记状态。** 每份归档文档必须在 §2 表格里有一行，标注
   ✅ 当前 / ⚠️ 部分过期 / 📌 待后续使用。
3. **保持自包含。** 优先整目录迁移（内部相对链接自动保持有效）；
   迁移后运行链接检查，修掉指向旧位置的引用。
4. **给出"该读哪一份"。** 有后继文档时，必须写出后继文档的路径。
5. **不要为归档而归档。** 一份文档只要还有活跃维护者，就留在分类目录里。
