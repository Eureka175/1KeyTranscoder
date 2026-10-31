# 透明模式 50 GiB 容器产物：正确的快速 remux（fast restore）

> 2026-10-31 调查 + 实现 + 真实 50 GiB 验证。
> 相关：`preservation/fast_restore.py`（生产模块）、
> `preservation/transparent_restore.py`（接入点）、
> `docs/audio-sync-p4/scripts/fast_restore.py`（研究原型）、
> `docs/audio-sync-p4/20261025-exploration-path.md` §5.1–§5.3（研究结论）。

## 1. 问题（为什么标准做法不可行）

透明模式输出 = 源视频/非音频流 **stream copy** + 修正后的音频轨 + 容器层
元数据恢复。音频部分重封装本身是一次 `MP4Box -new`（写一遍 50 GiB，实测
**258.7 s / 49.78 GiB**，可接受）。

问题在"元数据恢复"：`preservation/transparent_restore` 的标准流程把恢复拆成
多次 GPAC 操作（`-ref` tref → meta → `-flat` → `-brand` → uuid 插入），
**每一步都整文件重写**。研究阶段实测 `-ref` 一步 17 分钟推进不到 1 GiB
（GPAC 对超大文件的随机写），50 GiB 上不可接受。

## 2. 调查结论：stage 只缺容器层，一次顺序拷贝即可补齐

`gpac.mux_new` 产出的 stage 已经是合法 MP4（video + 修正音频 + rtmd/meta 轨，
payload 全部正确），缺的完全是容器层：

| 缺失项 | 来源 |
|---|---|
| `ftyp` 品牌（XAVC + compatible brands） | 源 ftyp（32 B）|
| 厂商 `uuid` box：root PROF/XMP、moov USMT、per-trak USMT（共 9 个） | 源文件/`SonyPreservationBackend.extract` 的 bundle |
| 文件级 `meta` box（`nrtm` 镜头档案 + Lens profile item + XML，idat 自包含） | 源 meta box（逐字节）|
| meta 轨 `tref/cdsc`（→ 视频轨） | 源 tref（需按 stage 的 track id 重映射）|
| 音频 sample entry（MP4 `ipcm`，源 86 B） | 源条目（GPAC 中间 .mov 写的是 QuickTime `in24`，114 B）|

所以正确做法 = **字节级重建**：`ftyp(源) + root-uuid(源) + mdat(stage 流式拷贝)
+ 重建 moov(stage moov + 插入 uuid/tref + 换回源音频条目 + 按新 mdat 位置
平移 stco/co64) + 其余 stage root box + meta(源)`，一次顺序读写完成，
**不调用任何 GPAC 重写**。

研究原型 `docs/audio-sync-p4/scripts/fast_restore.py` 已在真实短 Sony 素材
（C1093，3 s）上验证与标准流程"消费方等价"。本次把它生产化时补了原型没有的
健壮性：不假设 root box 顺序、tref 的 track id 按 source→stage 映射、所有
chunk offset 做范围校验、只丢弃 size=0 的 padding、失败自动回退标准流程。

## 3. 实现（生产）

- 新模块 `preservation/fast_restore.py`
  - `fast_restore_from_stage(source, stage, bundle, bundle_dir, dst, log)`：
    单次顺序读写的重建；返回详细 report（uuid/tref/entry 插入数、mdat 位移、
    chunk offset 校验数等）。
  - `verify_fast_restore_structure(source, dst)`：廉价结构校验（ftyp、uuid
    multiset、文件级 meta、音频 sample entry、track id、tref），只读 moov。
  - `FAST_RESTORE_MIN_BYTES = 2 GiB`。
- 接入 `preservation/transparent_restore.restore_after_transparent_remux`：
  - `stage >= 2 GiB` 自动走 fast 路径；失败/结构校验不通过 → 记日志并回退到
    原标准 GPAC 流程（语义不变）。
  - 测试/诊断钩子 `set_fast_restore_mode(True/False/None)`（不改变注入钩子的
    位置签名，channel_sync 仍按 6 个位置参数调用）。
  - 快速路径不做 dur patching：stage moov 的时长本来就正确，集成层随后会跑
    `repair_remux_timescale`；早期实现用"猜测的 movie timescale"补时长会把
    视频/rtmd 时长改错 +1001 tick（已在 clip 测试中抓到并修掉）。

## 4. 验证

### 4.1 真实短 Sony clip（与标准流程 + 源三方对比）

素材：`K:\1kt_work\long_align\clips_refined\20260919_C1305_restored_clip_1446s.mp4`
（107 MB，1×HEVC + 4×mono pcm_s24be/ipcm + rtmd，XAVC，9 uuid，nrtm meta）。

| 检查 | 结果 |
|---|---|
| 结构 vs 源（ftyp/uuid/meta/音频条目/track ids/tref） | **一致** |
| 视频 payload streamhash（源 / 标准 / fast） | **三者相同** |
| 4 条音频 payload streamhash | **三者相同** |
| rtmd data payload streamhash | **三者相同** |
| 流签名（codec/channels/duration_ts） vs 源 | **一致**（含 ipcm 条目）|
| 标准 GPAC 流程的已知差异 | 音频条目留在 `in24`、nrtm meta 被重编码成 3391 B（源 11304 B）→ fast 路径更接近源 |

脚本：`work/long_take/s7/fast_restore_equiv.py`（报告
`F:\1kt_temp\fast_restore_equiv\report.json`，结论 FAILED: none）。

### 4.2 真实 50 GiB C1305（本次关键验证）

固定音频 = 生产音频域输出的最终对齐 4 声道（`raw_audio/c1305_ch{0..3}.f32`，
已验证与 K 交付音频 5 min 块 0/0/0）。

| 步骤 | 耗时 | 速率 |
|---|---|---|
| 固定音频 → 4×mono MOV | ~20 s | — |
| Sony 元数据 bundle 抽取 | 36.4 s | — |
| **GPAC stage mux（一次 50 GiB 写）** | **258.7 s** | 0.19 GiB/s |
| **fast restore（一次 50 GiB 读 + 50 GiB 写）** | **99.1 s** | **0.50 GiB/s**（合计 I/O ~1.08 GiB/s）|

校验（脚本 `work/long_take/s7/fast_restore_50g.py`，结果见
`work/long_take/accept/fast_restore_50g.json`）：

- 结构 vs 源：ftyp/uuid×9/文件级 meta 11304 B/track ids/tref **全部一致**；
  mdat 位移 −4,291,240 B（stage 的 moov 在 mdat 前，重建后移到 mdat 后），
  61,453 个 chunk offset 全部落在新 mdat 内（范围校验通过）。
- 视频 payload（完整 streamhash，源 vs 输出）：**一致**。
- rtmd data payload（完整 streamhash）：**一致**。
- 4 条音频 payload：输出轨与固定音频 MOV **逐包一致**；解码后与对齐 raw f32
  差 < PCM24 量化误差。
- 流签名（codec/tag/channels/duration_ts）= 源（`ipcm` 条目已恢复）。

对照：研究阶段的标准 GPAC `-ref` 一步 **17 min 推进 <1 GiB**；本实现 50 GiB
**99 秒**完成，且不需要多轮。

## 5. 使用与限制

- 自动：透明模式 + Sony 源（rtmd）+ stage ≥ 2 GiB 时启用；日志会输出
  `fast-restore: one-pass rebuild ...` 与
  `channel-sync: fast restore metadata verified ...`。
- 手动对照/测试：`preservation.transparent_restore.set_fast_restore_mode(True)`
  / `False`（None=自动）。**不新增 CLI 开关**（遵守任务书"无新 CLI"）。
- 失败安全：fast 异常或结构校验失败 → 回退标准 GPAC 流程；两者都失败 →
  透明模式回退源文件拷贝（既有语义）。
- 磁盘：运行期需要 stage + 输出两份 ~50 GiB（与标准流程相同的峰值；
  标准流程还会产生 GPAC 临时拷贝）。GPAC 临时目录仍固定在媒体卷
  （`preservation/gpac.py` 的 `.gpactmp`）。
- 限制：fast 路径只适用于"stage 已含目标轨 + 源有可抽取的 Sony bundle"的
  场景；非 Sony 源不走该路径。full payload 校验（完整视频 hash）耗时且只在
  验收脚本里做，生产只做结构校验（毫秒级，只读 moov）。

### 4.3 真实用户命令端到端（最终验证，2026-10-31 23:30）

用**生产入口的原样命令**跑 50 GiB C1305：

```powershell
python 1kt.py --input <dir with 20260919_C1305.MP4> --output <out> \
    --channel-sync-transparent --headless --jobs 1 --keep-work
```

结果（日志 `work/long_take/accept/transparent_e2e_50g.log`）：

- 总耗时约 **12.8 min**（23:30:33 → 23:43:20），其中音频分析/决策 + 时变
  对齐约 7 min、元数据抽取 28 s、stage mux + fast restore 合计约 4.3 min、
  恢复后的 Sony checker 约 1 min；
- `[SYNC-OK] ... transparent remux (scope=file, fixed=2, remux)`；
- `[CHECK] level=basic PRESERVED=37 MODIFIED=0 MISSING=0
  structural_success=True`（Sony 元数据 37 项全部保留）；
- 输出 `20260919_C1305.MP4` 49.78 GiB；channel-sync report：`status=applied`、
  `path=time_varying`、`apply_engine=reference`、决策
  anchor/already_aligned/fixed/fixed；
- 独立复核脚本 `work/long_take/s7/verify_transparent_e2e_50g.py`：20 项检查
  **全部 PASS（FAILED: none）**——结构 vs 源一致（ftyp/9 uuid/11304 B meta/
  tref/`ipcm` 条目/track ids）、视频首 10 s streamhash 一致、rtmd payload
  逐包一致、4 条音频解码后与对齐 raw f32 **逐样本相等（diff=0）**。

结论：不调用 GPAC 做**元数据恢复的整文件重写**是可行的；但 GPAC 仍然用于
**生成 stage**（一次 `mux_new`，约 4 min / 50 GiB）与**抽取元数据 bundle**
（只读）。要完全去掉 GPAC 需要自写 muxer + 纯 Python nrtm 解析，属于另一个
量级的工作，当前没有必要。

### 4.4 更宽矩阵验证（2026-11-01 00:00，7 个真实源全 PASS）

脚本 `work/long_take/s7/fast_restore_matrix.py`，报告
`work/long_take/accept/fast_restore_matrix.json`。覆盖：

| 源 | 特点 | 源大小 | 输出 | 总耗时 | 结果 |
|---|---|---|---|---|---|
| K clips_refined C1305 clip | XAVC restored，**flattened stage**（moov 在 mdat 后） | 0.10 GiB | 0.10 GiB | 3.8 s | PASS（16/16） |
| K clips_refined C1306 clip | 另一 take，meta 12464 B | 0.11 GiB | 0.11 GiB | 1.9 s | PASS（16/16） |
| I hard_cases C0911 | 原始 `isom` 布局：meta 在 moov 前、无 root/moov uuid、`iods`、mdat 最后 | 0.87 GiB | 0.87 GiB | 7.4 s | PASS（16/16） |
| I hard_cases C0912 | 同上布局 | 0.40 GiB | 0.40 GiB | 6.5 s | PASS（16/16） |
| I hard_cases C0915 | 同上布局 | 0.16 GiB | 0.16 GiB | 2.8 s | PASS（16/16） |
| I hard_cases C0916 | 同上布局（接近门槛） | 0.10 GiB | 0.10 GiB | 2.1 s | PASS（16/16） |
| C1306 全长 69.4 min | 43.12 GiB stage，生产对齐音频 | **43.13 GiB** | 43.12 GiB | 338.8 s（stage 162.9 s + fast 57.8 s） | PASS（16/16） |
| C1305 全长 80.1 min | 真实 1kt 透明命令 E2E | 49.82 GiB | 49.78 GiB | 12.8 min | PASS（20/20，见 4.3） |

每个小源验证 16 项：结构 vs 源（ftyp/uuid 多重集/文件级 meta/音频
sample entry/track ids/tref）、视频**完整** streamhash、rtmd 完整 streamhash、
4 条音频 payload 与固定 MOV 逐包一致、解码音频与源逐样本相等、流签名/时长
与源一致。C1306 全长另验证：解码音频与生产对齐 raw **逐样本相等（diff=0）**。

**不适用源的判定**（负向/边界）：
- `clips_final` 的两个"已对齐但未恢复元数据"文件没有 rtmd 流、没有 meta 轨
  tref → 矩阵判为 **不适用（skip）**；`fast_restore_from_stage` 也新增了前置
  检查（无 uuid/meta/tref 时抛错让调用方回退），不会产出缺元数据的假成品。
- 把裸 stage 交给 `verify_fast_restore_structure` → 正确报 5 类问题
  （ftyp/meta/uuid/音频条目/tref），结构校验能拦住"没恢复就交付"。
- stage 在 mdat 前还是后（flattened）都能正确重建（动态布局，co64/stco 按
  新 mdat 位置平移并做范围校验）。

人工验证文件已整理到 `F:\1kt_temp\fast_restore_matrix\deliverables\`
（README + 8 个成品：6 个小源 + 2 个全长，附 matrix_report.json）。
