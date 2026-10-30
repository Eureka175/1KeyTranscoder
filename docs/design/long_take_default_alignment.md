# 任务书（已定稿）：≥3CH 默认自动对齐 + 恒定优先 + 长镜头时变后备（不加开关、只改音频路线）

> **状态**：**已定稿，待实施**（用户 2026-10-29 已拍板核心口径，见 §0）
> **日期**：2026-10-29
> **范围**：默认音频对齐行为（`--channel-sync` 与 `--channel-sync-transparent` 两条入口）
> **约束（用户给定）**：
> 1. **不新增 `--long-align` 之类开关**，不改用户命令行；默认算法要能处理长镜头；
> 2. **只做音频路线**；视频编码、元数据还原/容器细节不在本任务范围；
> 3. **复用程序内置 API**，不把研究脚本搬进生产（不造第二套轮子）；
> 4. **1kt.py 保持洁净**：入口只允许极薄分派，算法/流程都在 `core/`；
> 5. **注意多线程/多进程并行度**（窗口测量、施加、二次精化都要有明确预算）。

---

## 0. 已定口径（用户拍板，2026-10-29）

| 编号 | 决策 | 内容 |
|---|---|---|
| **A** | **≥3CH 自动开启对齐（仅 PCM，上限 8）** | **所有布局一视同仁拆成独立 CH 计数；3–8 个 PCM 声道时 channel-sync 自动开启，不管入口/场景**；**一切对齐只接受 PCM 流**。多声道/立体声流用现有拆声道能力展开；**全通道参与**时输出**保持源音频结构**（流条数/声道数/布局不变）。`--channel-sync` 保留为兼容参数。**>8 声道不自动对齐**（只允许 `--audio-plan alignment=enabled`） |
| **A2** | **透明入口独立** | `--channel-sync-transparent` 仍是**独立开关**：开启后**不重新编码视频**、**忽略 encoder/视频编码相关参数**；它只决定“透明预处理/重封装”这条输出路径，不决定是否做对齐（对齐由 A 自动开启） |
| **A3** | **`--audio-plan` 的对齐开关（自动逻辑完全让位）** | 走 `--audio-plan` 时**自动开启完全让位**，一切按 plan 输入：`alignment` 就是“是否需要对齐”的开关。`enabled` 时**任意 CH 数都跑对齐（含 1CH/2CH/>8）**，但**只允许 PCM**；**只检查参与对齐的流**——其中任一条不是 PCM 就**显式拒绝并退出**（不做解码/重编码降级）。`disabled` 不对齐；`auto` 保持既有分层 |
| **A4** | **默认锚点顺序与回退** | 默认锚点 **CH1 → CH2 → … → CH8**（0-based 0..7），依次尝试；**当前锚点置信度/帧数低于门即换下一个**（沿用现有 `min_confidence`/`min_usable_frames` 门）。其它可选回退判据（可用 target 数、相关强度）在 S0/S1 一并实测比较后回填 |
| **A5** | **混合 PCM+压缩：整文件不对齐（进程不退出）** | **未指定 `--audio-plan`：参与集合 = 全部输入音频流，全部检查**——只要有 AAC/Opus 等非 PCM → **整文件不自动对齐**、音频原样输出、记录 `mixed_pcm_compressed_not_aligned`，**进程不退出**；**指定 `--audio-plan`：只检查 plan 指定的流**（参与对齐的流），集合内非 PCM 才显式拒绝（§4.9） |
| **A6** | **结构无法无损还原：报错 + 原样输出** | 全通道参与且对齐完成后，若输出**无法无损还原源的流条数/声道数/布局** → **报错**（文件级稳定错误码）并**原样输出未对齐音频**；不交付结构被改写的成品。**实现前先做结构还原预测试**（§11 S0/S3） |
| **B** | **恒定优先；任一轨不恒定则整文件时变；时变失败回退无对齐** | 所有可对齐文件**先走现有恒定偏移测量**。**任一健康 target 判为不恒定**（`non_constant` 或复检残差否定 `recheck_residual`）→ **整文件时变**（不保留部分整数修正）；**时变失败时不保留任何整数修正，回退到无对齐的原始音频**。全部恒定/已对齐才用整数移位，且**不做窗级测量** |
| **C** | **stereo 组绑定** | **相关性极高的两条轨默认绑定为一组（stereo 组），整组用同一个偏移/曲线**；组内相对偏移保持源样（如 CH2 相对 CH1 的 −4 采样）。最多 8 轨；**5.1/7.1 等布局不按语义处理，均视作独立 CH** 再做绑定 |
| **D** | **计划口径** | 非固定路径使用 **`linear` + `nearest-centre`**（30 s 窗 / 10 s 跳、1 s 网格） |
| **E** | **极性** | 时变路径内**自动做极性翻转**（投票门见 §4.5），组级一起翻 |
| **F** | **残差精化** | **第二轮残差默认开启**（最多 2 轮） |
| **G** | **验收判据** | **曲线层优先**：先与 K 的冻结曲线逐窗对比，再比 K restored 音频（§9） |

> 由这些决策引出的行为变化，已记录而非隐式发生：
> 1. **不再需要 `--channel-sync` 才对齐**：只要 probe 判定 3–8 个 PCM 声道，各路径默认对齐。
>    此前“不传 `--channel-sync` 就不动音频”的行为被取消；这会改变大量既有转码产物
>    （仅音频轨），需要按 §8.2 重新建立回归口径。
> 2. **所有布局一视同仁拆 CH**：mono / stereo / 5.1 / 7.1 都拆成独立声道计数；
>    **不按 5.1/7.1 语义处理**。≥3 个 PCM 声道才自动对齐（2 声道立体声仍不自动对齐）。
> 3. **默认锚点从 CH3 改为 CH1**：`anchor_candidates` 默认由 `[2,3,0,1]` 改为
>    `[0,1,2,3,4,5,6,7]`；CH1 置信度/帧数低于门就换 CH2…；所有依赖锚点的数值都可能变
>    （预期行为变更，回归单列 `anchor_effect`）。
> 4. **任一轨不恒定 = 整文件时变**；时变失败**回退到无对齐原始音频**（不保留
>    部分整数修正），状态与“未做对齐”一致。
> 5. **`--audio-plan` 完全让位**：计划存在时不叠加自动开启；`alignment=enabled`
>    从“压缩可 warning+解码重编码”改为**只允许 PCM，参与流含非 PCM 显式拒绝退出**；
>    >8 声道也允许（plan 自己负责结构）。
> 6. **结构保持只在“全通道参与”时承诺**；若无法无损还原源结构 → **报错 + 原样输出**
>    （不交付结构被改写的成品）。实现前先做**结构还原预测试**（1 stereo + 1 mono、
>    5.1 全通道等），结果写进 S0 记录；不支持的组合按 A6 处理。
> 7. **混合 PCM+压缩的检查范围**：**未指定 `--audio-plan` → 全部输入音频流都检查**
>    （混有压缩即整文件不对齐/原样输出/进程不退出）；**指定 plan → 只检查 plan 指定的流**，
>    集合内非 PCM 才显式拒绝。
> 8. **两条目标素材的 CH1/CH2 是已知 stereo 组**（|CC|≈0.98、恒定 −4 采样），
>    绑定检测必须把它们识别为同一组；这是验收硬检查项。
> 9. **Sony/DJI 保留管线与硬件批处理都支持“只换音频、不改视频”**，按同一口径自动开启，
>    不设例外（S0 只做接线核对，不是新能力开发）。
> 10. `--channel-sync-transparent` 与对齐**解耦**：只决定“不重编码视频的透明输出”，
>     对齐按 A 自动发生；视频编码参数在该模式下被忽略（既有行为）。

---

## 1. 背景与问题（已实测）

用户对以下两条素材执行了现有默认命令：

```powershell
python 1kt.py --input "F:\Pictures\2026\202609\20260919舞团" ^
    --output "F:\Pictures\2026\202609\20260919舞团_new" ^
    --channel-sync-transparent
```

结果（真实日志，2026-10-29）：

| 文件 | 时长 | 现状 | 关键证据 |
|---|---|---|---|
| `20260919_C1305.MP4` | 80.1 min | `measure_failed`，输出=源文件拷贝 | CH1/CH2/CH4 全 `non_constant`；实测位移 14.8 / 11.1 / 8.7 ms，单次整数移位 23.8 / 23.9 / 5.5 ms |
| `20260919_C1306.MP4` | 69.4 min | `measure_failed`，输出=源文件拷贝 | CH1/CH2 `non_constant`；CH4 按材料性放行后，三窗复检最差 1.636 ms > 门 0.2708 ms，退回 |

根因不是阈值：

- 生产帧几何 200 ms / 100 ms 在 30 s 计划窗内平均只有 **0.048 个可用帧**
  （`docs/audio-sync-p4/long-take-windowed-prototype.md` §前提）；
- 窗底限覆盖率：C1305 CH1→CH2 **21/478 (4.4 %)**、CH3→CH4 **0/478**；
  C1306 17/414 (4.1 %)、0/414（`P4.12-STATUS.md` §4.2）；
- 真实偏移在片内确实变化（C1305 CH4 前 40 min 约 500 采样/10.4 ms；C1306 CH3
  22–30 min 约 131 采样/2.5 ms 的事件；`long-take-alignment-final.md` §1/§10.1），
  固定整数移位在物理上修不了。

已有研究结论（同一批素材，已验证）：

- 直接对 **30 s 窗**相关可把中位残差降到 **0–1 采样**，强相关通道（CH4）p90 1–4；
  CH1/CH2 仍有约 100 采样尾巴，属窗口级测量模糊性；
- 推荐口径 **30 s 窗 / 10 s 跳**，`linear` + `nearest-centre`
  （`long-take-windowed-prototype.md` §结果/§建议）；
- 逐窗实现应落在 `core/audio_sync_tv_exec` 接口上，但**测量必须换成窗直接相关**，
  `aggregate_frames_to_windows` 在这类素材上得不到证据（同上建议 3）；
- 最终长镜头交付（漂移跟踪 + 第二轮 5 min 块残差 + 极性修正）在强相干块上：
  C1305 CH1–CH3 中位 2 / 最大 36 采样，CH1–CH4 中位 10 / 最大 49；
  C1306 CH1–CH3 中位 1.5 / 最大 134，CH1–CH4 中位 1 / 最大 29
  （`long-take-alignment-final.md` §4）。

---

## 2. 目标与非目标

### 2.1 目标

1. **所有布局一视同仁拆 CH；3–8 个 PCM 声道默认自动对齐**；`--channel-sync` 只作兼容；
2. `--channel-sync-transparent` 保持独立：只决定“不重编码视频的透明输出”；
3. **恒定偏移永远先测**；**任一健康轨不恒定**（`non_constant`/`recheck_residual`）
   → **整文件走时变**；**时变失败回退无对齐原始音频**；全部恒定才用整数移位；
4. **默认锚点 CH1→CH8**；CH1 置信度/帧数低于门就依次回退；
5. `--audio-plan` 完全让位：`alignment=enabled` **任意 CH（含 >8）、仅 PCM、只查参与流**，
   非 PCM 显式拒绝；
6. 高相关双轨**默认绑定 stereo 组**（最多 8 轨），整组偏移；5.1/7.1 均视作独立 CH；
7. **输出保持源音频结构**——仅全通道参与时承诺；**无法无损还原 → 报错 + 原样输出**；
8. **混合 PCM+压缩 → 整文件不对齐**，音频原样输出（默认路径）；
9. 时变路径全部走内置 `audio_sync_tv_exec` 计划/执行/复测，不复制研究脚本；
10. 结果继续复用现有第 7/8 步输出链路（音频轨编码 + 透明/经典重封装），
    不新增任何 CLI 参数；Sony/DJI/硬件路径按同一口径接线，不做例外；
11. 处理过程有明确并行度与内存预算。

### 2.2 非目标

- 不改视频编码、不重编码视频、不改 `--check`、不改容器/元数据还原逻辑；
- 全部恒定的文件不引入分数重采样；整数移位路径保持逐字节不变；
- 不新增任何 CLI 开关；
- 不把 `tests/regression/` 的研究脚本整体搬进生产；只复用其**已验证口径**，
  实现放 `core/` 并用内置 API 落地；
- 默认路径不对齐 <3 个 PCM 声道（如 2CH 立体声）、44.1 kHz、**混合 PCM+压缩文件**；
  **唯一例外**是 `--audio-plan alignment=enabled`（任意 CH，但参与流仅 PCM）；
- 不按 5.1/7.1 语义做布局处理（全部按独立 CH 对齐，输出时按源布局重组；
  无法无损重组则报错+原样输出）；
- 不改变普通 `--channel-sync-tv`（显式 opt-in）的调用契约。

---

## 3. 现有架构与复用清单

### 3.1 现有默认流程（`core/channel_sync.run_channel_sync`）

```text
probe：音频流数/声道数/布局/是否 PCM
→ 若音频流含非 PCM（PCM+压缩混合）→ **整文件不自动对齐，音频原样输出**（稳定原因）
→ (自动) **所有布局统一拆成独立 CH**（mono/stereo/5.1/7.1 都按独立声道处理）
→ 若 PCM 声道数 3–8 → channel-sync 自动开启；<3 或 >8 不自动（>8 只走 audio-plan）
→ eligible / 解码 raw / 轨道健康
→ stereo 组绑定检测（默认；两条目标素材 CH1/CH2 必须成组，最多 8 轨）
→ 锚点回退：CH1 → CH2 → ... → CH8；当前锚点置信度/帧数低于门即换下一个
→ 【恒定优先】estimate_pair：200 ms 帧 / 100 ms 跳的帧轨迹
→ 逐轨恒定/置信/范围/证据门 + 材料性
→ 全部恒定/已对齐：fixed 轨整数移位 + 复检 → 全通道重组回源结构 → 第 7/8 步（不做窗级测量）
→ 任一轨 non_constant/recheck_residual：整文件转【时变】30 s 窗测量 → audio_sync_tv_exec → 全通道重组回源结构 → 第 7/8 步
→ 时变失败：回退无对齐原始音频（不保留任何整数修正）
→ 全通道重组若无法无损还原源结构：**报错 + 原样输出未对齐音频**
→ 透明模式：remux + 元数据恢复；回退原样：输出=源拷贝
```

### 3.2 内置 API 复用表

| 环节 | 复用的现有 API | 说明 |
|---|---|---|
| PCM 解码 | `core.channel_sync._decode_stream()` | 单流 ffmpeg 解码到 raw f32/f64；已有 storage/precision 约定 |
| 轨道健康 / 静音 | `core.channel_sync._track_health()` | 分块扫描，固定内存 |
| **stereo 组检测** | `core.sync_estimate.sample_pair_snr()` + `core.channel_sync.group_by_snr()` / `group_anchor_of()` | **直接复用 P4.12 已上线实现**：抽帧相关强度、并查集建组、组内锚点；不新造聚类、不新造阈值 |
| 帧级测量（现路径） | `core.sync_estimate.estimate_pair()` | 保留不动；**恒定优先**路径使用（所有可对齐文件都先走这里） |
| 单窗相关原语 | `core.sync_estimate.gcc_phat_int()` / `phase_slope_fine()` | 新测量函数只做“窗切分 + 调用原语 + 多估计器共识” |
| 恒定移位 | `core.sync_fix.shift_stream()` | 恒定可行时使用；与现有行为逐字节相同 |
| 逐窗计划/状态 | `core.audio_sync_tv.build_correction_plan()` / `derive_states()` | 计划构造与状态传播全部复用 |
| 逐窗执行 | `core.audio_sync_tv_exec.measurements_from_records()` / `request_time_varying()` / `build_time_varying_plan()` / `execute_time_varying()` / `run_time_varying()` | 施加 `out[k]=in[k+S[join(k)]]`；原子写 `.part`；内置独立复测 |
| 复检 | `core.sync_fix.recheck_residual()` / `recheck_residual_windows()` | 固定轨沿用；窗级由内置 `verify_applied_shift()` 负责 |
| 音频轨编码/中间文件 | `run_channel_sync` 第 7 步现有代码 | 时变/后备只把最终 raw 写回 `fixed_raw[i]`，第 7 步原样复用 |
| 透明重封装 | `core.channel_sync.remux_transparent_audio()` + `preservation.transparent_restore.restore_after_transparent_remux` | 已抽取的现有容器链路；本任务不改其行为 |
| **所有布局拆声道** | `core.audio_plan` 的通道选择 + `effective_mapping()`；`core.audio_output_structure` 的 `mapping`（`independent`/`grouped`） | 所有布局（mono/stereo/5.1/7.1）统一拆成独立 CH；复用既有拆声道/重组能力，不新写 |
| **`--audio-plan` 对齐执行** | `production.output.apply_alignment()` + `core.audio_format.resolve_alignment()` + `core.audio_sync` | 只改 `alignment=enabled` 的准入：PCM-only、非 PCM 显式拒绝（§4.9）；估计/施加不重写 |
| 报告 | `run_channel_sync` 的 JSON 结构 | 只增可选 `long_take` / `stereo_groups` 块，不改既有键 |

### 3.3 研究分支已有成果的复用边界

| 成果 | 位置 | 怎么复用 | 不能怎么复用 |
|---|---|---|---|
| 测量/施加口径与参数 | `long-take-alignment-final.md` §2/§4/§10–§12、`long-take-windowed-prototype.md` | 作为设计口径（§4/§5）与验收阈值来源 | 不能当成“默认算法已实现” |
| **冻结逐窗曲线**（1 s 网格，4808 点/轨） | `K:\1kt_work\long_align\applytv2_20260919_C1305.json` / `_C1306.json`；`applytv_*`、`refine_curve_*`、`track_*` | **第一验收 oracle**：换算到 CH3 参考后逐窗比对；也用于标定容差 | 不能在生产代码里按素材名读取（把答案写死） |
| **K restored 成品**（49.78/43.12 GiB） | `K:\1kt_work\long_align\deliver\*_4ch_aligned_restored.mp4` | **第二验收 oracle**：比通道间相对滞后与 5 min 块残差；可抽 2–5 min 4CH 片段做真实夹具 | 不能直接当默认算法输出（只覆盖这两条） |
| 研究已知答案（常量/线性漂移/极性/>100 ms 拒绝） | `tests/regression/long_align_tools/p4_tests.py`、`proto_long_take_*.py` | 转成 `tests/selftest/` 合成单测 | 不能在 unit 测试依赖 E:/F: 硬编码路径 |
| 研究脚本本体 | `tests/regression/long_align.py`、`long_align_tools/*.py` | 仅作算法参考（口径已逐项写入本任务书） | 不能被 `core/` import：`solve_final.py` 按素材名硬编码观测值、`apply_tv.py` 固定 CH1 锚点/CH2=−4、路径写死 `E:\`/`data\` |

**关键换算**：冻结曲线以 **CH1 为锚点**，本任务默认锚点也是 **CH1**（可直接对比）；
若实际运行因 CH1 失败回退到 CH2/CH3…，先换算到“相对 CH1”：
`s_i^CH1(t) = s_i(t) − s_0(t)`（CH1 自身为 0）。音频对比只看通道间相对滞后。

> 结论：**复用口径、冻结曲线和成品作为验收证据/测试夹具；不复用其脚本实现与固定解。**

### 3.4 现有 API 速查（实施时直接调用，不重写）

> 以下都是仓库内**已存在**的生产 API；本任务只加薄封装/参数，不复制实现。
> `bool`/`str` 等类型遵循各函数现有签名；未列出的参数用默认值。

#### 文件级入口 / 选项

| API | 签名与用途 |
|---|---|
| `core.channel_sync.run_channel_sync` | `run_channel_sync(*, source: Path, ffmpeg: Path, work_dir: Path, streams: list[dict], opts: dict \| None = None, log=print, transparent: bool \| None = None, dst: Path \| None = None, gpac: Any \| None = None, restore_metadata: MetadataRestoreHook \| None = None) -> dict`；文件级主入口，`opts` 见下 |
| `core.channel_sync.production_opts` | `production_opts(overrides: Mapping \| None = None) -> dict`：生产工作点 `materiality_k=3 / verify_max_rel=0.2 / recheck_windows=3 / verify_agree_samples=20 / track_reference=True` |
| `core.channel_sync.effective_opts` | `effective_opts(cfg: dict \| None) -> dict`：`DEFAULTS` + 覆盖归一（含 `anchor_candidates`、`frame_ms/hop_ms`、`time_varying`、`tv_policy/tv_coordinate`、`group_*`） |
| `core.channel_sync.eligible_audio` | `eligible_audio(streams) -> (bool, reason)`；当前要求**每条流都是 mono PCM 48/96 kHz**（多声道 PCM 需先拆，见下） |
| `core.channel_sync._decode_stream` | `_decode_stream(ffmpeg, source, stream_index, sample_rate, out_raw, storage_dtype) -> (ok, err)`：单流 ffmpeg 解码到 raw f32/f64；内部复用（不要复制命令） |
| `core.channel_sync._track_health` | `_track_health(arr, np) -> (all_finite, rms_dbfs)`：分块健康扫描 |
| `core.channel_sync._muxer_for_entry` / `_copy_stream` | `_muxer_for_entry(sample_entry) -> "mp4"\|"mov"`；`_copy_stream(ffmpeg, source, stream_index, out_file, muxer) -> (ok, err)` |
| `core.channel_sync.remux_transparent_audio` | `remux_transparent_audio(*, source, dst, audio_files: list[Path], work_dir, gpac, restore_metadata, streams, log) -> (Path \| None, bool)`：**透明重封装唯一入口**（源轨顺序、GPAC/Sony 元数据恢复、timescale/sample-entry/tkhd 修正、失败回退源拷贝） |
| `core.channel_sync.repair_remux_timescale` | `repair_remux_timescale(path, log=print)` |
| `core.channel_sync.group_by_snr` | `group_by_snr(snr: Mapping[(i,j), dict], tracks, *, threshold: float, min_samples: float) -> list[list[int]]`：纯函数，显式门 |
| `core.channel_sync.group_anchor_of` | `group_anchor_of(members, candidates) -> int`：按 `anchor_candidates` 顺序取组锚点 |
| `core.channel_sync.skip_two_channel_only` | `skip_two_channel_only(n_tracks, n_healthy, enabled=True) -> bool` |

#### 测量 / 复检

| API | 签名与用途 |
|---|---|
| `core.sync_estimate.estimate_pair` | `estimate_pair(ref_src, tgt_src, *, sample_rate, search_window_ms, frame_ms, hop_ms, anchor_segment_seconds, min_confidence, storage_dtype="f32", coarse_rate=8000, fine_phase_band_hz=(200,8000), min_usable_frames=5, frame_min_rms_dbfs=-50, ref_index=-1, tgt_index=-1, workers=1) -> PairEstimate`；`PairEstimate` 字段：`delay_samples/delay_ms/confidence/usable_frames/frames/fine_delay_ms/fine_fit_r2/polarity` |
| `core.sync_estimate.sample_pair_snr` | `sample_pair_snr(ref, tgt, *, sample_rate, frame_ms, hop_ms, search_window_ms, coarse_rate=8000, frame_min_rms_dbfs=-50, samples=200, seed=...) -> {"samples","frames_total","median","p25","p75"}`：建组强度，**不返回延迟** |
| `core.sync_estimate.gcc_phat_int` | `gcc_phat_int(ref, tgt, *, lag0, search_lo, search_hi, rms_gate=...) -> _GccOut(delay, confidence, polarity, success)`：窗级相关原语 |
| `core.sync_estimate.phase_slope_fine` | `phase_slope_fine(ref_seg, tgt_seg, *, sample_rate, band_hz) -> (delta, r2, polarity)`：亚样本精估 |
| `core.sync_estimate.summarize_trajectory` / `classify_constant` / `measured_displacement_ms` | 帧轨迹统计、恒定性判定、稳健位移；口径见 docstring |
| `core.sync_estimate.open_source` | `open_source(src, storage_dtype="f32") -> RawStream \| ndarray`：有界窗口读，不整文件 memmap |
| `core.sync_fix.shift_stream` | `shift_stream(src, dst, *, delay_samples, sample_rate, storage_dtype, chunk_seconds)`：整数前移 `out[n]=in[n+rint(delay)]` |
| `core.sync_fix.recheck_residual` / `recheck_residual_windows` | 修后残差复检（单窗/多窗） |

#### 时变计划 / 执行（本任务的核心复用面）

| API | 签名与用途 |
|---|---|
| `core.audio_sync_tv_exec.request_time_varying` | `request_time_varying(*, enabled=True, policy, coordinate, window_samples=None, hop_samples=None, search_samples=None, chunk_samples=480000, require_evidence_gate=False) -> TimeVaryingRequest`；**policy/coordinate 无默认值**（缺即拒绝） |
| `core.audio_sync_tv_exec.measurements_from_records` | `measurements_from_records(records: Sequence[Mapping], *, n_samples, hop_samples) -> list[dict]`；records=`{valid, delay_samples, confidence}`；缺 `valid` 报错 |
| `core.audio_sync_tv_exec.build_time_varying_plan` | `build_time_varying_plan(source, *, request, measurements=None, frames=None, context=None) -> TimeVaryingPlan` |
| `core.audio_sync_tv_exec.execute_time_varying` | `execute_time_varying(source, dst, *, plan, storage_dtype="f32", verify=True) -> TimeVaryingSyncResult`；原子写 `.part` |
| `core.audio_sync_tv_exec.run_time_varying` | `run_time_varying(source, dst, *, request, measurements=None, frames=None, context=None, storage_dtype="f32", verify=True) -> TimeVaryingSyncResult`：**一次性生产调用面** |
| `core.audio_sync_tv.build_correction_plan` | `build_correction_plan(measurements=None, *, resolved=None, shifts=None, n_samples, window_samples=..., hop_samples=..., policy=..., coordinate=..., search_samples=..., context=None, version=...) -> CorrectionPlan` |
| `core.audio_sync_tv.apply_shift_plan` | 低层施加（`run_time_varying` 内部使用；一般不要直接调） |
| 常量 | `audio_sync_tv.DEFAULT_WINDOW_SAMPLES/HOP/SEARCH`；`audio_sync_tv_exec` 的 `TimeVaryingRefused`、`TimeVaryingSyncResult`（`executed/reason/n_windows/state_counts/verification/evidence`） |

#### `--audio-plan` / 拆声道 / 输出结构

| API | 签名与用途 |
|---|---|
| `production.output.load_audio_plan_request` | `load_audio_plan_request(path) -> AudioRequest`：解析 `--audio-plan` JSON |
| `production.output.resolve_source_audio_plan` | `resolve_source_audio_plan(ffprobe, source, request, *, source_id="source", external=True) -> AudioPlan`：探源 + 外挂 + 应用选择（1kt 只调这一层） |
| `production.output.apply_alignment` | `apply_alignment(plan: AudioPlan, reference: str, *, ffmpeg, work_dir=None, requested=None, log=None) -> AlignmentOutcome`：**alignment 唯一执行入口**（resolve_alignment → plan_from_plan → estimate_sync → apply_sync_result） |
| `core.audio_format.resolve_alignment` | `resolve_alignment(plan, requested=AUTO, *, channel_ids=None) -> AlignmentDecision`：格式感知准入（PCM/压缩、warning、reason）；本任务只改 `enabled` 的 PCM-only 准入 |
| `core.audio_format.is_pcm_codec` / `classify_codec` / `format_of_stream` | 判断 PCM/压缩格式，用于“非 PCM 显式拒绝” |
| `core.audio_sync.plan_from_plan` | `plan_from_plan(plan: AudioPlan, sync: SyncPlan) -> SyncPlan`：显式 reference → SyncPlan |
| `core.audio_sync.estimate_sync` | `estimate_sync(plan, sync, *, reader=None, ffmpeg=None, work_dir=None, chunk_frames=0, parameters=None) -> SyncEstimateResult`（内部调 `sync_estimate.estimate_pair`） |
| `core.audio_sync.apply_sync_result` | `apply_sync_result(plan, result) -> int`：写 `AudioChannel.sync`（唯一 offset 入口） |
| `core.audio_plan.select_channels` / `exclude_channels` / `map_channels` / `map_channel` | 选择/重排声道（拆声道基础操作），返回新 `AudioPlan` |
| `core.audio_plan.effective_mapping` | `effective_mapping(plan) -> list[dict]`：输出声道顺序与来源 |
| `core.audio_plan.build_audio_map_spec` / `require_audio_map_spec` | 计划 → 可执行 map spec（校验/执行前置） |
| `core.audio_output_structure.parse_mapping_policy` / `resolve_mapping_policy` / `build_output_structure` | `source/independent/grouped` 输出流结构解析与构建；**保持源结构/重新交织**复用这里 |
| `core.audio_plan.source_plan` | `source_plan(sources, *, mode=TrackBuildMode.PER_STREAM, select_all_channels=True) -> AudioPlan`：多来源默认计划 |

#### 容器 / 校验（只读复用）

| API | 用途 |
|---|---|
| `preservation.transparent_restore.restore_after_transparent_remux` | `(source, adds, dst, work_dir, gpac, log) -> stage \| None`：Sony 元数据恢复钩子，注入 `run_channel_sync`/`remux_transparent_audio` |
| `preservation.dji.track_manifest` | `(gpac, source) -> (movie_ts, tracks)`：源轨清单/顺序 |
| `preservation.isobmf.normalize_track_flags` / `patch_track_durations` | tkhd flags/alternate_group、时长修正 |
| `preservation.sample_entry.patch_audio_sample_entries` | PCM sample entry 校正 |
| `archive_repair.probe` / `video_payload_equal` / `container_facts` | 探测、视频载荷等价、容器事实（验收/比对用） |

**调用示例（伪代码）**：

```python
# 1) 恒定时变目标：把 1 s 曲线变成计划记录并施加
records = tve.measurements_from_records(
    [{"valid": True, "delay_samples": s(t), "confidence": c(t)} for t in grid],
    n_samples=n, hop_samples=48_000,
)
req = tve.request_time_varying(
    enabled=True, policy="linear", coordinate="nearest-centre",
    window_samples=1_440_000, hop_samples=48_000,
    require_evidence_gate=True,
)
res = tve.run_time_varying(fixed_raw_i, aligned_f32_i, request=req,
                           measurements=records, verify=True)

# 2) audio-plan 对齐（唯一执行入口）
plan = resolve_source_audio_plan(ffprobe, source, request)
outcome = apply_alignment(plan, reference="source:s0:c0",
                          ffmpeg=ffmpeg, work_dir=work,
                          requested=request.alignment)

# 3) 透明重封装（唯一容器入口）
out, copied = remux_transparent_audio(
    source=source, dst=dst, audio_files=audio_files, work_dir=work,
    gpac=gpac, restore_metadata=restore_after_transparent_remux,
    streams=streams, log=log)
```

---

## 4. 方案

### 4.1 默认路径：自动开启、恒定优先、失败才时变（无开关）

**入口**：

- **自动开启（仅 PCM，3–8 声道）**：probe 后检查参与集合是否全为 PCM——
  **未指定 `--audio-plan` 时参与集合 = 全部输入音频流**（混有压缩 → 整文件不自动对齐、
  音频原样输出、`mixed_pcm_compressed_not_aligned`、**进程不退出**）；
  **指定 plan 时只检查 plan 指定的流**。集合全 PCM 时**所有布局统一拆成独立 CH**
  （mono/stereo/5.1/7.1 一视同仁）；PCM 声道数 3–8 → 各入口默认执行对齐；**>8 不自动**；
  <3（如单个 stereo）不自动。`--channel-sync` 只保留兼容。
- `--channel-sync-transparent` 独立：只决定“不重编码视频 + 透明重封装”的输出路径，
  忽略 encoder/视频编码参数；对齐本身按上一条自动开启。
- `--audio-plan` **完全让位**：计划存在时不再叠加自动逻辑，一切都按 plan：
  `alignment=enabled` 时**任意 CH（含 >8）**、仅 PCM、**只检查参与对齐的流**，
  非 PCM → 显式拒绝退出；`disabled` 不对齐；`auto` 保持既有分层（见 §4.9）。

**流程**（`run_channel_sync` 完成解码/健康/stereo 绑定之后）：

```python
# 所有布局已统一拆为独立 CH；锚点按 CH1→CH2→…→CH8 依次尝试
fixed_result = constant_offset_path(anchor_candidates=[0,1,...,n-1])
non_constant = any(track.reason in {non_constant, recheck_residual}
                   for track in healthy if track != anchor)
if non_constant:
    tv_result = window_level_time_varying_path(...)   # 整文件时变
    if tv_result.applied:
        return tv_result
    return unaligned_result()      # ← 时变失败：回退无对齐原始音频
if fixed_result is not file_level_failure:
    return fixed_result            # ← 全部恒定/已对齐，不做窗测量
return fixed_result                # ← 其它文件级失败：现状安全语义
```

- **锚点**：默认 `anchor_candidates = [0,1,2,3,4,5,6,7]`；**当前锚点置信度或可用帧数
  低于门**（`min_confidence`/`min_usable_frames`）即换下一个；该回退判据在 S1 与
  “无任何可测 target/相关强度低”等候选判据**实测比较后回填**（用户要求测试其他可能性）。
- **“整体时变”判据**：任一健康 target 的 `reason` 为 `non_constant` **或**
  `recheck_residual` → 整文件走时变；已 fixed 的轨**不再保留**整数修正。
- **“恒定可行”判据**：没有 `non_constant`/`recheck_residual`，且 `fixed_ok` 非空或
  `already_aligned`（部分 `low_confidence` untouched 不因“不恒定”触发整体时变）。
- **时变失败回退**：**回退到无对齐的原始音频**（不写任何整数修正、不混用）；
  透明模式下即输出=源文件拷贝；状态标记 `time_varying_failed` + 原因。
- **输出结构**：仅当**全通道参与**时才承诺保持源结构（流条数/声道数/布局不变）——
  默认自动路径即全通道参与，拆声道对齐后重新交织回源结构；`--audio-plan` 只选部分
  声道时按 plan/mapping 决定输出结构，不承诺还原源结构。
- **结构还原失败**：全通道参与但无法无损还原源结构（如不支持的布局/流组合）→
  **报错（文件级稳定错误码）+ 原样输出未对齐音频**；不交付结构被改写的成品。
- 报告增加 `long_take` 块（`path=constant|time_varying|unaligned`/`reason`/`coverage`/
  `residual`/`policy`/`coordinate`/`rounds`/`timings`/`stereo_groups`），命令 help 不新增。
- **并行/成本**：恒定测量永远先跑（80 min 约 20–40 s），窗测量只在出现不恒定轨时发生。

### 4.2 stereo 组绑定（默认，先于测量）

**不新造检测器**：直接复用 P4.12 已实现并回归过的分组机制（`sample_pair_snr`
→ `group_by_snr` → `group_anchor_of`），判据数值全部取自已有探索文档的实测分布。
**已知真值**：两条目标素材的 **CH1/CH2 是一个绑定的 stereo 组**（|CC|≈0.98、
恒定 −4 采样、100 % 同号）；验收要求 `stereo_groups` 必须包含 `{CH1,CH2}`，
否则视为绑定失败（即使延迟数值碰巧正确也不算通过）。

1. **强度**：对每个健康轨对调用 `sync_estimate.sample_pair_snr()`
   （200 抽样帧、固定种子、与生产粗扫同几何）；
2. **建组**：`channel_sync.group_by_snr()`，阈值用**生产默认 `1.3`**
   （P4.12 实测：组内中位 2.96 / p25 1.58，跨组中位 0.90 / max 1.30；
   见 `docs/audio-sync-p4/p4.12-grouped-anchor.md` §2）；
3. **组内锚点**：`group_anchor_of()`，沿用 `anchor_candidates` 顺序
   （单组四轨时与生产主锚点一致，P4.12 §4 的结构性等价）；
4. **stereo 绑定确认**：只对**恰好 2 成员**、且成员全部尚未被 fixed/aligned
   的组做刚性确认，判据来自同一批素材的探索文档实测，不引入新假设：
   - **高相关**：60 s 窗 / 30 s 跳的带符号相关中位 `|CC| ≥ 0.90`
     （`long-take-alignment-final.md` §13：同源对 CH1↔CH2 为 **+0.982，
     100 % 窗为正**，全片 |CC| 0.93–0.98；跨组对 0.43–0.61 —— 0.90 落在
     两个已测分布之间）；
   - **刚性**：全片 lag 的 `p90 − p10 ≤ 5 采样`（0.104 ms）；
     `docs/audio-sync-p2/investigation.md` §: CH1↔CH2 是**唯一的刚性对**，
     span **0.021–0.104 ms**、零移动事件；移动对 span 大 1–2 个数量级；
   - **符号一致**：同号窗比例 ≥ 0.95（实测 CH1↔CH2 为 100 %）；
   任一条不满足 → **不绑定**，按两条独立轨处理（仍可同属一个测量组、共享锚点）；
5. **整组偏移**：代表 = `group_anchor_of()`；
   - 恒定优先路径（可行时）：测代表相对主锚点的整数移位，**组内两条施加同一移位**；
   - 时变路径：测代表相对主锚点的 1 s 曲线，**组内两条施加同一曲线**；
   - 组内相对偏移（如 CH2 相对 CH1 的 −4 采样）是源内容的一部分，**不再动它**，
     因此“整组偏移”后组内关系保持源样；
6. **冲突/链式**：只接受两两不重叠的 2 成员组（4 轨最多 2 组）；出现 3 成员
   连通分量时**不绑定**（超出“一对”的定义，退回独立测量）；
7. **报告**：`stereo_groups` 记录成员、组锚点、SNR/中位 `|CC|`/lag span/符号一致、
   组内 delta、采用代表；绑定是**默认行为**，但必须可追溯。

> 这套判据的“新”只在于**把它用于默认路径**；检测机制、阈值语义与实测分布都来自
> 既有探索文档（P4.12 分组锚点、P2 刚性对调查、长镜头交付 §13 极性/相关），
> 不引入第二套聚类算法。

### 4.3 非固定（时变）测量（恒定不可行时才执行）

对每个“组代表 / 未绑定轨 in 健康轨 vs 锚点”，按 **30 s 窗 / 10 s 跳** 直接测窗：

0. 源 PCM 复用 `run_channel_sync` **已解码的 `decoded[i]` raw**（f32/f64），
   只额外生成 12 kHz 池化缓存；不重新 ffmpeg 解码；
1. 12 kHz 池化粗扫 ±250 ms（复用 `gcc_phat_int` 原语/等价参数）；
2. 全速率 2 s 段 ±8 采样精化（`phase_slope_fine` 口径）；
3. 每窗 5 个估计器：全带 + 3 子带（20–1k / 1–3.5k / 3.5–6k）+ 起音包络；
4. ≥2 估计器在 ±2 采样内一致才采纳，取簇中位与最小置信；
5. 连续性跟踪：每个 10 s 跳只在前一 lag 的 `±(96 + 8×t_min)` 内搜索，弱峰不更新；
6. 2 min `|CC|` 加权中位锚点 → 1 s 网格线性插值 → `|shift| ≤ 4800` 硬门；
   **建锚点前必须过滤 `|CC| < 0.08` 的窗**（研究 `apply_tv.build_curve` 口径；
   C1305 62–80 min 安静段落追踪器会一致抖动，cc 0.004–0.09、lag 700–1560，
   不过滤时 2 min 中位锚点被垃圾窗带偏 — 实测 65–70 min 与 K pass1 差 260 采样）；
7. 输出每 target 一条 `{grid_t, grid_s, coverage, per_window_quality}` 与逐窗证据。

**锚点**（时变后备路径）：沿用 `anchor_candidates`（默认 **CH1→CH2→…→CH8**，0-based 0..7）；
**当前锚点置信度/可用帧数低于门即换下一个**（与固定路径同一判据；其它候选判据如
“无任何可测 target/相关强度低”在 S1 实测比较后回填）。
全部候选不通过 → `no_valid_anchor` + 回退无对齐原始音频。
若锚点本身属于某个 stereo 组，组整体 shift 固定为 0。

### 4.4 计划与施加（全部走内置接口）

对每个时变 target（组代表或未绑定轨）：

1. 1 s 网格曲线 → `audio_sync_tv_exec.measurements_from_records()`
   的记录 `{valid, delay_samples, confidence}`；几何：
   `hop_samples = 48_000`（1 s）、`window_samples = 48_000`（见下修订）；
2. `request_time_varying(enabled=True, policy="hold", coordinate="latest-start",
   window_samples=…, hop_samples=…, require_evidence_gate=True)`；
   **修订（2026-10-29）**：`linear` 被内置 plan 标为 offline-only、流式生产路径
   必须拒绝，故用 `hold`；1 s 稠密无缺口曲线上 `hold` 与 `linear` 逐位相同。
   `nearest-centre` 的独立复测是 O(n×窗数) 朴素 Python（80 min ≈22 min/轨），
   `latest-start` 在 window==hop 时与它逐位等价（~0.1 s/60 s）；窗口必须
   `window_samples == hop_samples`，否则 30 s 窗 + 1 s 跳会把前 15 s 曲线拉偏；
3. `run_time_varying(src_raw, dst_raw, request=…, measurements=records)`——
   分块、原子写、独立复测；拒绝码进报告；
4. 若 1 s hop 被 plan 拒绝（几何约束），退回 10 s hop 同曲线并记录
   `plan_geometry_fallback="hop=10s"`；
5. **组绑定**：组代表执行一次；组内另一条用**同一 records/request** 再执行一次
   （其 `delta_ij` 已含在源样本里，因此整组同偏移即保持组内关系）。

**为什么 1 s hop**：内置 plan 的移位是“每窗一个整数”，无法直接表达 1 s 线性
插值；hop=1 s 时逐样本移位是 1 s 阶梯，慢漂移下与插值等价（<0.01 采样），
从而不改执行器即可复用其施加/复测。

### 4.5 第二轮残差精化（默认开启，最多 2 轮）

> **修订（2026-10-30，真实 80 min 素材验收后）**：测量窗从"5 min 块 / 50 % 重叠"
> 改为 **60 s 窗 / 20 s 跳**，搜索从 ±256 改为 **±80 采样**，加 **5 min 滑动中位
> 平滑 + 离局部中位 >30 采样窗口丢弃 + |r|≤100**（即 K 研究 round-3 fine pass
> 口径，含 12 kHz `resample_poly` 粗扫 + 2 s 段 ±8 精化）。原因：5 min 长块/宽
> 搜索在音乐基底上会选到 ±200 采样的伪峰（C1305 实测块残差 −236/−220/−208，
> CC 仅 0.26–0.51），把正确曲线拉坏；改用研究口径后合成注入 7 采样精确恢复。
> **"5 min"保留为残差曲线的平滑跨度，不再是测量窗长。**
>
> **追加修订（2026-10-30 晚，目标：与 K 成品同条件）**：残差在
> **分数插值重建的 pass-1** 上测量（`render_fractional_pass1`，逐样本线性
> 移位、越界补零，与 K `interp_write` 同义），而不是在生产 round-1 的**整数
> hold** 产物上测。原因：K 的 pass-1 是分数插值音频，亚采样差会让同一 fine
> 算法在弱内容窗测出不同残差（实测曲线差 median 4.8–7 / p90 12–35）。
> 生产最终施加仍用内置整数执行器 —— 只改变"测残差的参考音频"，不改变施加
> 安全语义；重建失败时退回整数 pass-1。详见 `long_take_impl_checkpoint.md`。

1. round-1 曲线用分数插值重建一份 pass-1 音频（只用于测量），在其上按
   **60 s 窗 / 20 s 跳** 测残差（仅 `|CC| ≥ 0.20`；搜索 ±80 采样；极性无关
   |CC| 峰）；
2. 残差曲线 **5 min 滑动中位平滑**（1 min 跳，离局部中位 >30 采样的窗丢弃，
   `|r| ≤ 100`）→ 1 s 网格；
3. `s_total(t) = s_round1(t) + s_residual(t)`，对**原始 PCM** 再跑一次
   `run_time_varying`（不从已处理音频二次重采样）；
4. 组绑定：残差在组代表上测，`s_total` 同时施加到组内两条；
5. 只在 ≥3 个有效窗且平滑后残差中位 ≥1 采样时执行；否则跳过并记录原因；
6. 总 `|shift| ≤ 4800`；最多 2 轮。

### 4.6 极性（默认自动）

1. round-2 输出按 60 s 窗 / 30 s 跳取零延迟附近带符号相关峰（±16 采样）；
2. **按组/按轨投票**：组级用代表与锚点（或跨组代表对）的票；未绑定轨各自投票；
3. 负票 ≥ 0.8 且中位 `|CC| ≥ 0.25` → 翻转（组则整组一起翻，保持组内极性一致）；
4. 报告 `polarity.votes/flip/consistent`；普通固定路径极性语义不变（仅诊断）。

### 4.7 输出与状态

- 时变成功：最终 raw 写入 `fixed_raw[i]` 约定位置，`fixed_ok` 加入对应轨，
  **第 7 步原样复用**（按源 codec 回编码）与第 8 步（透明重封装/经典路径）；
- 报告行：`decision="fixed"`、`time_varying={policy,coordinate,statistics,verify}`、
  `long_take=true`；组内另一条附 `stereo_group={...}` 与相同 shift/曲线；
- 文件状态与正常 fixed 一致 `applied`；失败保留 `measure_failed` +
  `long_take.reason`（`coverage_insufficient` / `plan_refused` / `no_improvement`）。

### 4.8 与现有 TV 开关的关系

- 恒定优先/时变后备使用**内部具名常量** profile，不要求用户传 `--channel-sync-tv*`；
- 用户显式传 TV 开关时，走既有显式路径（不被后备改写）；
- 普通文件的 D2/D5 契约不变（显式调用才有时变）。

### 4.9 `--audio-plan` 入口的对齐开关（仅 PCM，非 PCM 显式拒绝）

**复用现有字段，不新增 CLI 参数**：`--audio-plan` 的 `alignment`
（`auto|enabled|disabled`）就是“是否需要对齐”的开关；`sync.reference` 仍必须显式给出
（程序从不猜参考）。

| 取值 | 语义（本任务后的口径） |
|---|---|
| `enabled` | **强制对齐**：**任意 CH 数（1CH/2CH/>8）**都跑；**参与对齐的流必须全部是 PCM**；只要有一条不是 PCM → **显式拒绝、非零退出**（`AUDIO PLAN REJECTED`，不产出半成品），**不再** warning+解码+重编码 |
| `disabled` | 完全不对齐（不估计、不施加） |
| `auto`（默认） | 保持既有分层：PCM 允许对齐、压缩音频原样保留；不因本任务改变 |

- **拆声道**：不新写。所有布局（mono/stereo/5.1/7.1）统一按独立 CH 处理；单条多声道
  PCM 用现有 `core.audio_plan` 的通道选择 + `mapping`（`independent`/`grouped`）展开；
  对齐执行沿用既有 `production.output.apply_alignment`，本任务只改 `enabled` 的
  **准入校验**（participating-only PCM + 显式拒绝）。
- **结构**：`alignment=enabled` 时若 plan 选择**全通道参与**，可沿用 `mapping` 还原源
  结构；只选部分声道时按 plan/mapping 输出，**不承诺**源结构还原。
- **与自动开启的关系**：走 `--audio-plan` 时以 plan 为准，**不再叠加** ≥3CH 自动开启
  逻辑；未写 `alignment` 时按 `auto`。
- **检查范围**：**未指定 `--audio-plan` → 默认路径检查全部输入音频流**（A5，混有压缩即
  整文件不对齐）；**指定 plan → 只检查 plan 指定的流**（参与对齐的流），集合内非 PCM
  才显式拒绝。
- **拒绝时机**：在打开/解码任何音频前完成校验并退出；错误信息需指出哪条流不是 PCM。
- **测试**：`enabled` + 全 PCM（1CH/2CH/4CH/8CH）→ 对齐；参与流含 AAC/Opus → 拒绝；
  `enabled` + >8 CH → 允许；`disabled` → 不动；`auto` 行为与旧版逐字节一致。

---

## 5. 并行度与资源预算（重点）

### 5.1 现状

| 阶段 | 现状并行度 | 瓶颈 |
|---|---|---|
| 解码 | 逐轨串行 ffmpeg（上游完成）；时变路径复用 `decoded[i]` | 无重复解码 |
| 帧级固定测量 | `estimate_pair(workers=N)` 进程池，仅 ≥2000 帧启用；`N=max(2,min(12,cpu-1))` | 与 `--jobs` 无关，多文件时可能超订 |
| 固定移位 | `shift_stream` 分块单线程 | 可接受 |
| TV 施加 | `execute_time_varying` 分块单进程 | 逐 target 串行 |
| 二次精化 | 无（新功能） | 需并行块测量 |
| 重封装 | GPAC 单进程 | 不在本任务范围 |

### 5.2 目标并行设计

1. **stereo 绑定检测**：N×(N−1)/2 对（N≤8，最多 28 对）抽帧可并行
   （`ThreadPool`，子进程/IO 少，主要是 numpy）；
2. **窗测量**：`ProcessPoolExecutor`，任务 = `(target, window_start)` 或窗段；
   worker 数
   `workers_per_file = max(1, min(12, (cpu_count - 1) // max(1, file_jobs)))`，
   `file_jobs = --jobs`，保证 `file_jobs × workers_per_file ≤ cpu_count − 1`；
3. **只读缓存**：每文件一次生成 12 kHz 池化缓存（4×约 231 MB），worker 有界窗口
   读取；不整文件 memmap 全速率 PCM（沿用 P1 的 `RawStream` 内存规则）；
4. **施加**：每组/target 输出独立，`max_workers=min(3, workers)` 进程池；
   进程内由内置 `execute_time_varying` 分块写 `.part`；
5. **二次精化**：块测量任务 `(target, block_start)` 并行；
6. **确定性**：并行只改“算在哪”，任务输入与归并顺序固定；同输入两次运行报告
   逐字节一致（沿用 P1 要求）；组绑定结果与 worker 数无关。

### 5.3 预算（验收时实测并回填）

**实测回填（2026-10-30，C1305 80.1 min / C1306 69.4 min，均为 4×mono 48 kHz，
`run_channel_sync(transparent=False)`，含恒定测量/复检/时变/编码/解码，不含容器
重封装）：**

| 项 | 目标（80 min / 4CH / 48 kHz） | 实测 |
|---|---|---|
| 12 kHz 缓存 | ≤ 1 min | ~10–15 s/target（`coarse_decimate` 池化） |
| stereo 绑定检测 | ≤ 1 min | ~2–3.5 min（2 个候选对：真对 + CH3↔CH4 被拒；候选对已并行，仍超预算 → S5 剩余：12k 缓存跨候选复用） |
| 窗级测量（2–3 targets × ~478 窗，连续性顺序） | ≤ 8 min | 2 targets 合计 **167–168 s**（含 round-1 施加、残差、极性；见 `long_take.timings`） |
| round-1 施加 | ≤ 3 min | 含在上述 167 s 内（1 s 稠密曲线 hold/latest-start，~0.1 s/60 s verify） |
| round-2 残差 + 施加 | ≤ 8 min | 残差窗并行后 ~15 s/target（80 min ≈239 窗、4 线程） |
| 极性检测 | ≤ 2 min | ~5–10 s/target（123–125 窗投票） |
| **时变/后备总附加** | **≤ 20 min/文件** | **C1305 167 s / C1306 168 s** ✓ |
| 整文件（不含容器） | — | C1305 **410.8 s** / C1306 **280.1 s**（含解码 4×923 MB、恒定测量+换参考重测、时变、编码、解码；`full_align_only.py`） |
| 峰值内存 RSS | ≤ 3 GB/文件 | **实测（C1305，psutil 0.5 s 采样整棵进程树）**：主进程峰值 **743 MB**、进程树峰值（含 ffmpeg + 12 个帧级估计子进程）**1473 MB** ✓ |

---

## 6. 代码改动清单（拟定）

| 文件 | 改动 | 说明 |
|---|---|---|
| `core/sync_estimate.py` | 新增 `measure_window_delays()`（窗级多估计器共识）与并行包装 | 复用现有 GCC/相位原语；纯函数可单测 |
| `core/sync_estimate.py` | 新增 `stereo_bind_candidates()`（复用 `sample_pair_snr`）或直接在 `channel_sync` 用既有 `group_by_snr` | 高门 + 窗口级确认；不新造聚类 |
| `core/long_take.py`（新） | 时变编排：12k 缓存、窗测量、1 s 曲线、内置 plan/执行、残差第二轮、极性、报告 | 只做编排与适配，不实现施加/复测 |
| `core/channel_sync.py` | ① 判“参与对齐的流集合是否全 PCM”（默认集合=全部音频流；混合 → 整文件不对齐/原样输出/**进程不退出**）；② stereo 组检测接入（3–8 轨）；③ 默认 `anchor_candidates = [0..7]`；④ **恒定优先**；⑤ 任一轨 `non_constant`/`recheck_residual` → 整文件时变；⑥ 时变失败回退无对齐；⑦ 全通道重组，失败 → 报错+原样输出；⑧ 报告 `long_take`/`stereo_groups` | 默认路径唯一入口；无 CLI 改动 |
| `core/channel_sync.py` | 具名常量 `LONG_TAKE_PROFILE`（几何、policy/coordinate、绑定门、轮数、上限）；不含时长分档 | 产品口径集中、可单测 |
| `core/channel_sync.py` / `core/audio_plan.py` / `core/audio_output_structure.py` | 所有布局拆 CH → 对齐 → **按源结构无损重组**；无法无损还原 → 文件级错误 + 原样输出 | 拆/合都复用既有能力，不新写 |
| `core/audio_sync_tv_exec.py` | 无（若 1 s hop 有几何约束，仅加已验证的构造辅助/文档） | 保温 |
| `core/audio_format.py` / `production/output.py` | `--audio-plan alignment=enabled`：改为**PCM-only 准入**；**只检查参与对齐的流**，含非 PCM → 记录稳定错误码并**显式拒绝退出**（不再 warning+解码重编码） | 只改准入校验；估计/施加沿用 `apply_alignment`/`audio_sync` |
| `core/batch_hw.py` | 硬件批处理入口的自动开启逻辑与 `--audio-plan` 让位 | 与软件路径同一口径 |
| `1kt.py` | **只做薄改动**：默认对齐从 `args.channel_sync` 改为“无 `--audio-plan` 即自动”；`--audio-plan` 存在时完全按 plan；`--channel-sync-transparent` 仍独立；help 更新一句 | 洁净性：不在 1kt 写算法 |
| `tests/selftest/suites/channel_sync.py`（或新 suite） | 绑定/锚点回退 CH1→CH8/恒定优先/整文件时变/时变失败回退无对齐/结构保持/窗测量/并行确定性；合成 3–8 轨长短片段（**不传 `--channel-sync`**）端到端 | 纳入 `--level unit` |
| `tests/selftest/suites/*`（audio-plan 套件） | `alignment=enabled` 的 1CH/2CH/4CH/8CH 全 PCM 通过；参与流含 AAC/Opus 显式拒绝；`disabled`/`auto` 不变；`--audio-plan` 存在时不再自动开启 | 与既有 audio-plan 契约套件合并 |
| `docs/design/*`、`docs/audio-sync-p4/known-limitations.md` | 记录默认行为变更与已定口径 | 实施后更新 |

**复用优先**：不复制 `tests/regression/` 的实现；只把其测量口径写成
`core/sync_estimate.py` 的窗级函数，其余全部走 §3.2 的内置 API。

---

## 7. 决策记录与剩余待定

### 7.1 已定（用户 2026-10-29）

| 编号 | 决策 |
|---|---|
| **A** | **所有布局一视同仁拆 CH；3–8 个 PCM 声道**自动开启对齐；`--channel-sync` 仅兼容；**只接受 PCM**；**全通道参与**才保持源音频结构；>8 只允许 audio-plan；5.1/7.1 视作独立 CH |
| **A2** | `--channel-sync-transparent` 独立：不重编码视频、忽略 encoder/视频参数；不决定是否对齐 |
| **A3** | `--audio-plan` 存在时自动逻辑**完全让位**；`alignment=enabled` **任意 CH（含 >8）**、仅 PCM、**只检查参与对齐的流**，非 PCM → **显式拒绝退出** |
| **A4** | 默认锚点 **CH1→CH2→…→CH8**；**当前锚点置信度/帧数低于门即回退**，其它候选判据实测比较后回填 |
| **B** | **恒定偏移永远先测**；**任一轨不恒定（non_constant/recheck_residual）→ 整文件时变**；全部恒定才用整数移位且不做窗测量；**时变失败回退无对齐原始音频** |
| **C** | 高相关双轨默认绑定 stereo 组（最多 8 轨）、整组偏移、组内相对偏移保持源样；两条目标素材 CH1/CH2 必须识别为组 |
| **D** | 时变计划 `linear` + `nearest-centre`；30 s 窗 / 10 s 跳 / 1 s 网格 |
| **E** | 时变路径内自动极性翻转（组级） |
| **F** | 残差第二轮默认开启（≤2 轮） |
| **G** | 验收曲线层优先，K restored 音频第二 |

### 7.2 仍待用户确认（不阻塞实现，先按推荐）

| 项 | 推荐 | 备选 |
|---|---|---|
| 验收输出/中间产物落盘 | `K:\1kt_work\long_take_accept\`，保留 JSON/最终音频，删大 f32 | `F:\1KeyTranscoder\work\` / 跑完即清 |
| 提交/推送 | 本地 `main` 提交（含任务书/代码/测试/文档），**先不 push** | 直接 push / 开 feature 分支 |
| A1 回归语料不可用时 | 用可用留出集/合成集跑“可分类差异”回归，报告如实写“A1 未执行” | 恢复 A1 语料再验收 |
| 性能预算 | 时变/后备附加 ≤20 min/文件 | ≤10 / ≤40 min |
| `--audio-plan` 与自动开启的关系 | 走 `--audio-plan` 时**以 plan 的 `alignment` 为准**：`enabled` 强制对齐（PCM-only），`disabled` 不对齐，`auto` 按既有分层；不再叠加 ≥3CH 自动逻辑 | 改为两种入口同时存在时 FATAL |
| 是否需要关闭默认自动对齐 | 按“不管情况”**不提供 CLI 关闭**；如后续确需，走 config 的非 CLI 键 | 保留一个隐藏兼容开关 |
| 绑定判据 | 按探索文档：P4.12 分组阈值 1.3 + 组内 `|CC|≥0.90`/lag span ≤5 采样/同号 ≥95 %（§4.2）；实施时用统一语料回填实测 | 实施时按语料重新标定 |

> 若实现中发现这些推荐值导致误绑定或误触发，我会先记录数据再改，不静默调门。

---

## 8. 验收标准

### 8.1 真实素材（必须）

执行**与用户完全相同的命令**（不加任何新参数）：

```powershell
python 1kt.py --input <dir> --output <dir> --channel-sync-transparent
```

通过条件：

1. 两文件均不再 `[SYNC-FAIL] ... all measurable targets failed`；状态 `applied`，
   报告含 `long_take` 与 `stereo_groups` 块；
2. **绑定硬检查**：两文件的 `stereo_groups` 都必须包含 **{CH1,CH2}**（已知真值），
   且报告中的组内 |CC|/lag span/同号比例满足 §4.2 的门；
3. 输出 4 条音频轨、样本数与源相等（`duration_ts` 不变）；
4. **曲线层**（优先，见 §9）：与 K 的 `applytv2_*.json` 对比（默认都以 CH1 为锚点，
   可直接比；若实际锚点回退则先换算到相对 CH1），满足 §9 容差；
5. **音频层**：与 K `_restored.mp4` 的通道间相对滞后满足 §9 容差；极性一致；
   逐轨样本数相等；不要求绝对波形逐样本相等；
6. 报告给出每轨/每组曲线覆盖、每轮残差、绑定结果、运行时/worker 数/峰值内存。

### 8.2 回归（必须）

> 默认自动开启会改变大量既有输出的音频轨，因此**旧的“默认零差异”不再是正确判据**。
> 回归目标改为“**差异可分类、每一条差异都能归因**”。

1. `python tests/full_autotest.py --level unit` 全绿（现有 2 条环境性失败除外）；
2. **A1 基线重跑 + 差异分类**（226 片段/584 轨；语料不可用则留出集并如实记录）：
   - `class same`：决策与输出逐位相同；
   - `class newly_aligned`：此前不传 `--channel-sync` 不会对齐、现在自动对齐
     （预期新增的默认行为）；
   - `class anchor_effect`：默认锚点从 CH3 改为 CH1 导致的数值差异
     （预期行为变更；需抽样核对差异方向/幅度合理）；
   - `class binding_effect`：同一 stereo 组改用整组同一移位造成的差异
     （只允许出现在绑定组内，且组内相对偏移不变）；
   - `class whole_file_tv`：任一轨 `non_constant`/`recheck_residual` 导致整文件转时变，
     原先的整数修正不再保留（预期的新行为）；
   - `class unaligned_fallback`：整文件时变失败，输出回退到**无对齐原始音频**
     （不允许再出现部分整数修正）；
   - `class mixed_audio_skip`：混合 PCM+压缩文件**整文件未对齐**、音频原样输出
     （预期新行为）；
   - `class structure_restore_error`：全通道重组无法无损还原源结构 → **报错 + 原样输出**
     （不允许输出结构被改写的成品）；
   - `class audio_plan_enabled_reject`：`--audio-plan alignment=enabled` 且**参与流**含
     非 PCM 的用例由“warning+重编码”变为**显式拒绝**（预期的新行为，必须断言退出码/错误码）；
   逐类计数并抽样人工复核；**不允许出现无法归类的差异**；
3. **视频/容器不变**：所有差异只允许在音频轨；视频 streamhash、轨道数、时长、
   容器事实（brand/uuid/nrtm/tref/tkhd）与旧路径一致；
4. 默认路径 2CH/1CH、44.1 kHz、非 PCM、2CH-silent 跳过等既有边界不变；
   仅 `--audio-plan alignment=enabled` 例外（允许任意 CH，但仅 PCM）。
5. `--audio-plan` 的 `disabled`/`auto` 行为与旧版逐字节一致；`enabled` 的 PCM-only
   准入按 §4.9 单独测试；
6. 同一输入两次运行报告逐字节一致（并行确定性）。

### 8.3 性能

实测并回填 §5.3；超预算先归因（worker/缓存/IO），**不允许**放宽证据门或绑定门
来“达标”。

---

## 9. 与 K 研究结果的比对方法（曲线层优先）

比较脚本（放 `tests/regression/`，不进生产）：

1. **曲线层（第一判据）**：本方案输出的每轨 1 s 网格曲线 vs K 的
   `applytv2_<stem>.json`。K 与本方案默认都以 **CH1** 为锚点，可直接比较；
   若实际运行因 CH1 失败回退到其它锚点，先换算到相对 CH1：
   `s_i^CH1(t) = s_i(t) − s_0(t)`（两侧同式；CH1 自身为 0）。
   - **stereo 组期望**：CH1/CH2 绑定后整组同偏移；其相对 CH1 的曲线应一致，
     且源内 −4 采样关系保持不变（对比相对滞后，而非绝对 shift）。
   - 指标：`median |Δ|`、`p90`、`max`、`≤2/≤5 采样` 点比例；按 5 min 分段报告。
   - **容差**：`median |Δ| ≤ 1` 采样、`p90 ≤ 5`、`max ≤ 50`、`≥90 % 点 ≤5` 采样。
2. **音频层（第二判据）**：从 K `_restored.mp4` 与本次输出各解 4 条 mono f32；
   统一以 CH1 为参考，按 5 min 块（50 % 重叠，`|CC| ≥ 0.25`）测相对滞后：
   - `median(|lag_ours − lag_K|) ≤ 2` 采样；`p90 ≤ 10`；`≥90 % 块 ≤5` 采样；
   - 极性翻转后跨组带符号相关为正（与 K 一致）；
   - 四轨样本数相等。
3. 输出 JSON + 短 markdown 报告；不修改任何生产文件。

> **K oracle 版本**（用户暂未确定，实施时定）：K 上 `applytv2_*.json` 是第二轮曲线，
> `_restored.mp4` 可能含第三轮 fine pass。S6 先做“从 `_restored.mp4` 音频反推最终
> 相对滞后”与 `applytv2` 曲线的一致性检查：
> - 若两者一致（差异在测量噪声内）→ 用 `applytv2` 作为曲线 oracle；
> - 若不一致 → 以 `_restored.mp4` 音频反推的曲线为准，并在报告中注明版本。
> 无论哪种，报告都必须写明“对比的是第几轮/是否从成品反推”。

> 为什么要曲线层优先：两条 80 min 文件每次都做完整音频比对成本高，且残差统计
> 会掩盖局部偏差；先比 1 s 曲线能在几分钟内定位“哪一段/哪一对”不对。

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| stereo 绑定误判（相似内容但非同一对） | 整组偏移把两条独立轨绑死，错修 | 按探索文档四重门：P4.12 分组阈值 1.3 + 组内 `|CC| ≥0.90` + lag span ≤5 采样 + 同号 ≥95 %；绑定结果进报告；单测合成反例（独立噪声/移动对不得绑定） |
| 恒定优先被误用于“有轨不恒定”的文件 | 本应整体时变却保留了部分整数修正 | 判据明确：任一 `non_constant`/`recheck_residual` → 整文件时变；只有全部恒定才走整数移位；报告 `path=constant/time_varying` 可核对 |
| 整文件时变失败 | 是否混用/保留部分整数修正 | **回退无对齐原始音频**（不写任何整数修正）；报告记录 `path=unaligned`+原因 |
| 默认锚点 CH3→CH1 的连锁影响 | 大量既有文件的数值变化 | 单列 `class anchor_effect`；抽样核对差异方向/幅度；不允许出现无法解释的大跳变 |
| 3–8 轨/所有布局拆 CH 的通用性 | 现有代码只在 3–4 条 mono 流上验证过 | S0 审计 N 轨循环/锚点/分组/TV/拆分与重组；单测合成 3/5/8 轨与 stereo/5.1 拆合；>8 只走 audio-plan |
| 源结构保持（拆声道后重新交织） | 输出结构被改成多条 mono 或丢失流布局 | **只承诺全通道参与**；输出前按源流条数/声道数/布局校验；**无法无损还原 → 报错 + 原样输出**；复用 audio_output_structure |
| 混合 PCM+压缩文件 | 误对齐 PCM 子集或误拒绝 | 默认路径先判“全 PCM”；混有压缩 → **整文件不对齐、原样输出**（`mixed_pcm_compressed_not_aligned`）；只有 audio-plan `enabled` 才报错 |
| `--audio-plan alignment=enabled` 改为拒绝非 PCM（只查参与流） | 既有“压缩音频对齐”用例失效 | 用户明确要求；测试断言退出码/错误码；`auto`/`disabled` 保持旧行为 |
| 窗级测量弱相关通道 ~100 采样尾巴 | CH1/CH2 对齐不完美 | 如实报告 per-pair 残差；不承诺 ≤1 采样；绑定后 CH2 跟随 CH1，不再单独放大尾巴 |
| 1 s hop 计划被内置几何拒绝/过慢 | 退回 10 s hop | 先验证 plan 合法性；退回时记录并按 10 s 阶梯重新验收 |
| 自动开启导致默认输出大面积变化 | 既有回归基线失效、用户意外 | 默认自动是本任务的目标；按 §8.2 做“差异可分类”回归，每类抽样复核，不能归类即失败 |
| 并行超订 | 多文件吞吐下降 | `workers_per_file` 按 `--jobs` 分配；总 worker ≤ cpu−1；报告实际值 |
| 双精度/插值差异导致与 K 不逐样本一致 | 比对“失败” | 只比相对滞后/残差/曲线（§9），不比绝对波形 |
| 极性误翻转 | 反相 | 负票 ≥0.8 + 中位 `|CC| ≥0.25` 双门；组内一致才整组翻；不一致则记录不翻 |
| 第二轮把噪声写回 | 质量下降 | 仅在 ≥3 强相干块且残差中位 ≥1 采样时执行；总轮数 ≤2；失败保留 round-1 |
| 并行确定性 | 回归不稳 | 固定任务顺序与落盘顺序；两次运行报告逐字节一致作为测试 |

---

## 11. 实施步骤

1. **S0 入口/契约审计 + 结构还原预测试**：列出所有入口（经典 x265/svtav1、硬件 nvenc/qsv、Sony/DJI 保留管线、透明模式、`--audio-plan`）当前 channel-sync 的接入点与音频替换接线；这些入口都支持“只换音频、不改视频”，本任务只接线；
   **同时先做结构还原预测试**（用户要求“自己预先测”）：用 `audio_plan.source_plan/select_all`
   构造 1 stereo + 1 mono、5.1 全通道、2×stereo 等计划，走 `build_audio_map_spec` /
   `build_output_structure` / `output_tracks`，确认能否无损还原源流条数/声道数/布局；
   结果写入 S0 记录——不支持的组合按 A6（报错+原样输出）；
2. **S1 绑定 + 锚点判据 + 窗测量原语**：`stereo_bind`（复用 `sample_pair_snr`/`group_by_snr`，
   支持 3–8 轨）+ `measure_window_delays()` + **锚点回退判据对比实验**
   （置信度/帧数门 vs 可用 target 数 vs 相关强度），合成单测（常量/线性漂移/反例拒绝/
   独立噪声不绑定/3–8 轨/布局统一拆 CH）；在两条文件的 5 min 片段上校准，确认 CH1/CH2 成组；
3. **S2 曲线与计划**：1 s 网格 → records → `run_time_varying`；合成 120 s 素材端到端
   （plan 合法性、hop 退回、确定性）；
4. **S3 默认路径**：自动开启（3–8 PCM、所有布局拆 CH）+ **混合 PCM+压缩→整文件不对齐**
   + **锚点 CH1→CH8** + **恒定优先** + **任一轨不恒定→整文件时变** + 组绑定 +
   **时变失败回退无对齐** + **结构无法无损还原→报错+原样输出**；合成 3/4/8 轨
   长/短片段（不传 `--channel-sync`）验证
   `path=constant|time_varying|unaligned|mixed_skip|structure_error`、stereo 组与
   “全通道参与时源结构保持”；
5. **S3b audio-plan 开关**：`alignment=enabled` 的 **participating-only** PCM 准入与
   “非 PCM 显式拒绝”；1CH/2CH/4CH/8CH/>8CH PCM 通过、参与流含 AAC/Opus 拒绝、
   `disabled`/`auto` 旧行为不变、plan 存在时自动逻辑不启动；部分选择时不承诺源结构；
6. **S4 第二轮 + 极性**：按 §4.5/§4.6 接入，合成已知漂移/反相/组绑定验证；
7. **S5 并行**：落 §5.2，测 worker 扩展性（1/4/8/12）与峰值内存；
8. **S6 真实验收**：两条目标文件跑默认命令；按 §9 先曲线层、再音频层出报告；
9. **S7 回归与文档**：unit/A1 差异分类/普通片段回归；更新设计文档与 known-limitations。

每个 S 阶段都必须有“可复现命令 + 数字”，不允许只写“已优化”。

---

## 12. 仍开放/需要确认的问题

**已由用户拍板（不再开放）**：3–8 个 PCM 声道自动；所有布局一视同仁拆 CH、只接受 PCM；
混合 PCM+压缩 → **整文件不对齐/原样输出**；结构无法无损还原 → **报错 + 原样输出**；
>8 只走 audio-plan；audio-plan 只查参与流的 PCM 准入；整文件时变失败回退无对齐原始音频；
仅全通道参与时保持源结构；`--audio-plan` 时自动开启完全让位；默认锚点 CH1→CH8
（置信度/帧数低于门回退，并测试其它判据）；`linear`+`nearest-centre`；自动极性；
第二轮默认开；曲线层优先；Sony/DJI/硬件路径支持只换音频。

**仍需确认/审计（不阻塞实现，按推荐执行）**

1. **多源流布局的结构还原**：S0 预测试（用户已要求先测）会给出 1 stereo+1 mono、5.1、
   2×stereo 等组合能否无损还原；不能还原时按 **A6 报错+原样输出**（已定）。
2. **K 曲线 oracle 版本**：你暂未确定；S6 用“`_restored.mp4` 音频反推曲线 vs `applytv2`”
   一致性检查自行确定，报告写明版本（§9）。
3. **绑定判据泛化**：P4.12 阈值 1.3 + `|CC|≥0.90` + lag span ≤5 采样 + 同号 ≥95 %
   在 3–8 轨全部语料上是否过绑/漏绑；实施时用统一语料回填，若落在门附近先记录再改。
4. **1 s hop 计划**：30 s 窗/1 s 跳（4808 窗）是否被 `build_time_varying_plan` 接受、
   `verify_applied_shift` 成本；不行退 10 s hop 或先修执行器几何约束——需实测后定。
5. **性能预算**：3–8 轨/80 min 的时变附加是否仍按 ≤20 min/文件（target 增多会线性增加）。
6. **A1 回归语料**：226 片段是否完整可用；不可用时用留出集/合成集，报告如实标注。
7. **交付细节**：验收中间产物放 `K:\1kt_work\long_take_accept` 还是 F 盘 work；
   本地提交后是否 push；VERSION/release notes 是否随本任务更新。

---

## 13. 实施状态（2026-10-30）

> 本节是**实施后的状态回填**，不改变上面已批准的需求与决策；逐条证据、
> 数字、复现命令与 open item 的完整记录在
> `docs/design/long_take_impl_checkpoint.md`（问题与决策记录）和
> `docs/audio-sync-p4/known-limitations.md` §14。

**已完成**：默认自动开启（恒定优先 → 整文件时变后备）、stereo 组绑定、
时变计划/施加（全部走内置 `audio_sync_tv_exec`，hold + latest-start + 1 s 网格）、
round-2 残差、极性、报告块（`stereo_groups` / `long_take`/ `channels[].source_*`）、
`--audio-plan` 让位语义、多声道 PCM 布局（A6：逐声道拆、按源流重新交织、
结构保持）、GPAC 临时目录重定向、S5 部分并行（残差窗 / 绑定候选对）。

**实施中对本文档口径的修订**（均已在上文对应小节就地标注）：
- §4.3：建锚点前必须过滤 `|CC| < 0.08`（研究 `apply_tv` 口径），且锚点时间
  用 2 min 箱起点（与 K pass1 逐位一致——round-1 与 K pass1 实测 0/0/0）。
- §4.4：`policy=hold`、`coordinate=latest-start`、`window_samples == hop_samples`
  （`linear` 为 offline-only；`nearest-centre` 的独立复测是 O(n×窗数)）。
- §4.5：残差测量改为 60 s 窗 / 20 s 跳 / ±80 搜索 / |CC|≥0.20 / 5 min 加权
  中位平滑 / 离局部中位 >30 采样丢弃 / |r|≤100（研究 round-3 口径）。
- §4.6：极性翻转后，输出（成品音频）极性应与锚点一致；报告里
  `targets[].polarity.flip=true` 表示已翻。

**未完成/待决**：K oracle 的 C1305 restored 与 K 研究 fine 曲线不一致（疑似
旧全局解误封装，见 known-limitations §14.3）；任务书 §9 曲线/音频容差低于 K
自身的内容歧义噪声地板（§14.2）；A1 回归语料不可用（§14.4）；透明模式在
50 GiB 文件上的容器恢复受 GPAC 多轮整文件重写限制（§14.4）。
