# v0.8.8 发布说明 — 归档修复合入主程序 + 自主批处理

> **状态**：**已发布**（本地 tag `v0.8.8`；`VERSION` = `0.8.8`）
> **基线**：`feature/hardware-codec-policy-redesign`，上一版本 `0.8.7`
> **日期**：2026-10-26

本版本只做一件事：把**已经验证过的归档修复能力**变成主程序的一等入口，
并让它能**无人值守地跑完整批**。默认转码路径的行为没有改变。

---

## 1 修复能力合入主程序

```bat
python 1kt.py --repair --input I:\ --output G:\1kt_repair_v3_20261026 ^
    --repair-dirs "^\d{8}$" --jobs 8
```

- `1kt.py --repair` 与 `--input/--output` 复用同一套参数/日志/退出码；
  修复不重新编码视频（视频 stream copy），只做音频整数样本移位、元数据
  位置恢复、容器规范修复。
- 原有独立入口 `archive_repair.py check|repair` 保留（单文件、脚本、CI 用）。
- 新增 `--repair-*` 选项：`--repair-dirs`（一级子目录正则）、
  `--repair-json`、`--repair-scratch`、`--repair-resume`、
  `--repair-retry-failed N`、`--repair-limit`、`--repair-fix-layout`、
  `--repair-no-container-fix`、`--repair-no-groups`、`--repair-accurate`、
  `--repair-opt K=V`。

## 2 最新稳定算法（默认值）

| 项 | 默认 | 说明 |
|---|---|---|
| 生产工作点 `production_opts()` | 开 | `materiality_k=3` / `verify_max_rel=0.2` / `recheck_windows=3` / `verify_agree_samples=20` / `track_reference=True`（v0.8.7 定标） |
| 分组锚点 `group_anchor_enabled` | **开** | 与生产 `start.bat` 的 `GROUPS=1` 一致；`--no-groups` 可关 |
| 精确模式 `channel_sync_accurate` | 关（失败重试时自动开） | 1→2→5 样本分级，由施加后复检验收 |
| 残差音频追加轮 | 开（≤2 轮） | 首轮后仍有 1–20 样本残差时自动再修 |
| 容器级一键修复 | 开 | tkhd flags/alternate_group、PCM sample entry、时长时基；`--fix-layout` 可选恢复 moov-first |

## 3 自主批处理（不需要人守着）

- **整盘一条命令**：`--repair-dirs` 过滤一级目录，递归保持子目录结构。
- **断点续跑**：`_progress.jsonl` 逐文件落盘；`--repair-resume` 只补未完成。
- **失败自动收口**：首轮 `FAILED` 文件自动用精确模式**串行**重试；仍失败
  才报 `FAILED`（附错误），批次永不因单文件中断。
- **总账**：`_summary.json` / `_summary.md` + 逐文件 `archive-repair-report.json/md`。
- **退出码**：只有真正 `FAILED` 返回 1；`PARTIAL` / `UNREADABLE` 如实记录，
  不让批处理循环误报。

## 4 成人品硬闸门（"修对了"由程序证明）

2026-10-26 并发实测暴露：并行路径在高负载下会偶发产出损坏成品。因此交付前
强制通过三道校验，不通过即判失败并进入串行重试：

1. `validate.compare` **无 MODIFIED / MISSING**（brand/tref/nrtm/uuid/rtmd 等 43 项）；
2. **视频载荷逐包 streamhash 与源相同**；
3. 容器级修复路径额外要求**成品大小 == 源大小**（字节级拷贝 + 等长改写）。

## 5 实测

### 5.1 全量修复批处理（I: 18 个 `YYYYMMDD` 目录，669 文件 / 94.5 GB）

| 指标 | 值 |
|---|---|
| 结果 | **REPAIRED 661 / PARTIAL 2 / UNREADABLE 6 / FAILED 0** |
| 耗时 | 1467.8 s（24.5 min，jobs=8，含成人品载荷校验） |
| 首轮被闸门拦下 | 7 个（2 个 validate 不一致、5 个 sample-entry 不一致）→ **串行重试 7/7 REPAIRED** |
| 闸门覆盖 | applied 45/45 视频载荷相等；container_fixed 616/616 大小与视频载荷相等 |
| 残差追加轮 | 3 个文件（C0925 1 轮、C1167 2 轮、C1204 1 轮）→ 最终 `already_aligned` |
| 独立复检 | **640 PASS / 21 WARN（全部 AUD-04）/ 0 FAIL / 0 UNREADABLE** |

### 5.2 一条真实短编码测试（改主程序后）

`20260903_C1090.MP4`（3840×2160 10-bit 4:2:2，11.0 s）→ x265 HQ：
`done=1 skipped=0 failed=0`，`PRESERVED=37 MODIFIED=0 MISSING=0`，
384.6 s；成品再用最新 checker 复检 **PASS**。

### 5.3 自测

- `python tests\full_autotest.py --level unit`：**979 PASS / 2 FAIL**（截至
  2026-10-29，含 §8 新增的 13 条 `p12d.*` 断言），两个失败均为素材已按要求
  移出 `F:` 导致的环境性失败（`cls.real` / 语料缺失），与本次改动无关。
- 新增/更新自测：`tests/selftest/suites/hardware_audio.py` 手工登记全部
  `--repair-*` 参数并断言 `--repair` 真的分派到 `archive_repair.run_repair`；
  `tests/selftest/suites/archive_repair.py` 覆盖容器/音频/PARTIAL/UNREADABLE 判定。

## 6 已知限制

- **并发路径仍会偶发损坏**：8 并发下 45 个需音频重封装的文件里 7 个首轮被
  闸门判坏；串行重试全部修复。若重试也失败，该文件会明确列为 `FAILED`，
  **绝不静默交付**。
- 不可测的轨（静音 / `no_valid_anchor` / `non_constant`）不能修，`AUD-04` WARN。
- 无 moov / 零填充文件只能报 `UNREADABLE`；`.part` 半成品报 `PARTIAL`。
- 成人品载荷校验使每文件多读一遍源与成品：669 文件 / 94.5 GB 实测 24.5 min。
- `--fix-layout` 仍默认关闭（MP4Box 整文件重交织最慢）。

## 7 版本

```
VERSION        0.8.7 -> 0.8.8
tag            v0.8.8
1kt.py --version  ->  "1KeyTranscoder 0.8.8"
```

## 8 Post-RC1 修复（2026-10-29）

RC1（`v0.8.8-rc1`，`012662b`）之后主分支上的两处**透明模式**缺陷。默认转码
路径、修复模式行为均未改变。

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | `--channel-sync-transparent` **静默忽略** `--channel-sync-tv` / `-tv-policy` / `-tv-coordinate`（覆盖逻辑只写在 `main()` 里；透明入口在 main 分流之前就 return） | 抽出 `_apply_time_varying_cli`，编码入口与透明入口共用；透明模式打印生效值 `CHANNEL-SYNC TIME-VARYING`；缺 policy/coordinate 现在按契约拒绝（此前会悄悄退回固定延迟） |
| 2 | 透明批量汇总把 `measure_failed` 计入 `done`，一边打 `[SYNC-FAIL]` 一边写 `failed=0` | 新增 `sync_skipped` / `sync_failed` 计数与汇总字段；`failed` 只表示进程级失败；看板 `detail` 写明同步失败原因；退出码不变（文件级失败仍产出可用的源文件拷贝） |

验证（2026-10-29）：

- `--level unit`：**979 PASS / 2 FAIL**（两个环境性失败同 §5.3）；新增
  `p12d.*` 13 条断言，覆盖覆盖函数行为、两个入口 AST 级共用、终态分类。
- 6 s 合成 4CH 片段实测：只给 `--channel-sync-tv`（缺 policy/coordinate）→
  `tv_not_configured` + `sync_failed=1`；`hold`/`latest-start` → 三条轨逐窗
  执行（`windows=1, TRUSTED`），证明透明路径确实吃到了开关。
- `docs/audio-sync-p4/known-limitations.md` 已登记"透明 + TV 无独立视频回归"
  这一已知限制。

若以 `v0.8.8` 正式发布，tag 必须在**包含本节提交**的 HEAD 上重打（当前本地
tag `v0.8.8` = `3d3dcac`，不含本节修复）。
