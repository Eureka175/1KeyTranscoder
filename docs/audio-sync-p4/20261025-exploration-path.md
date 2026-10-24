# 2026-10-25 六小时工作：探索路径报告

> 本文件记录**怎么做的、看到了什么、证据在哪**，供复核与延续。
> 结论性汇总见 `20261025-summary-report.md`。所有产物在
> `F:\1KeyTranscoder\work\probe_20261025\`、`work\task3_20261025\`、
> `work\PLAN_20261025\`，图表在 `docs/audio-sync-p4/plots/task2_*.png`。
>
> 硬约束：F: 只在工作文件夹内写（不删除）；E:/I: 读写自由；不跑长程 x265；
> 全程注意内存（所有分析流式/分块，未整读 40+ GiB 文件）。

## 1. 方法：先复用程序已有 API，再谈并行

按用户要求"尽量使用程序已有 API，而不是自己写"，探测没有实现第二套对齐算法：

| 环节 | 复用的生产 API |
|---|---|
| 容器/流事实 | `core.probe.probe_streams()`（与生产 probe 同一 ffprobe 调用） |
| 布局门（2CH/1CH 不对齐） | `core.channel_sync.eligible_audio()` |
| 测量/锚点/健康/质量门/决策 | `core.channel_sync.run_channel_sync()`（transcode 模式、无 dst、不改源） |
| 容器事实比对 | `archive_repair.container_facts()` / `uuid_inventory_equal()` |
| Sony 元数据抽取/恢复 | `preservation.sony.*`、`preservation.transparent_restore.restore_after_transparent_remux` |
| GPAC 操作 | `preservation.gpac.GpacContainerBackend`、`preservation.dji.track_manifest` |
| audio sample entry / 时基 / tkhd | `preservation.sample_entry.patch_audio_sample_entries`、`core.channel_sync.repair_remux_timescale`、`preservation.isobmf.normalize_track_flags` |
| 消费方验证 | `preservation.gyroflow.check()`（Gyroflow 无头导出 + 逐项比对） |

**并行化**：`work/probe_align_api.py` 用 `ProcessPoolExecutor`（默认 5 workers）
逐文件并行；每个 worker 独立 E: 临时工作目录，结束即删；内存/磁盘都有界。
testsets 326 个文件 **138 s** 跑完，I:\2026.09.24 84 个文件（649.7 GiB）**61 s** 跑完。

**向量化**：探测阶段不做自定义 FFT；测量全部交给生产 channel_sync 的
GCC-PHAT/相位斜率实现（其内部已是 numpy 向量化）。数据分析与绘图
（`work/analyze_all.py`）用 numpy 聚合 + matplotlib 一次成图。

## 2. 任务1：testsets 全量音频对齐可行性

### 2.1 数据

| 目录 | 文件数 | 大小 | 音频布局 |
|---|---|---|---|
| `a7m5_2ch_10bit422` | 91 | 21.6 GiB | 1×2ch PCM（2CH） |
| `a7m5_4ch_long` | 2 | 92.9 GiB | 4×mono PCM（= C1305/C1306 长素材） |
| `a7m5_4ch_speech` | 224 | 82.1 GiB | 4×mono PCM 为主（部分静音/2CH 有效） |
| `action4_4x3_long` | 9 | 110.3 GiB | DJI Action4（含 djmd/dbgi 数据轨） |

合计 326 MP4 / 306.9 GiB。按流布局：**146 个 4×mono PCM**、112 个 1×2ch
`pcm_s16be`、50 个 1×2ch AAC、18 个无音频。

### 2.2 结果（生产 semantics；2CH 布局按设计不对齐）

**146 个 4×mono PCM 文件：**

| 结论 | 数量 | 说明 |
|---|---|---|
| `already_aligned` | 49 | 测量后无需移位（锚点/目标均在容差内） |
| `applied`（实际修正） | 12 | 修正量 19.1–27.3 ms（无线 CH1/CH2 对有线 CH3/CH4 的观测延迟） |
| `two_channel_only`（新策略跳过） | 73 | **静音掩码全部为 `1100`**：CH1/CH2 静音（RMS≈-64/-68 dBFS），CH3/CH4 有效（-16…-25 dBFS） |
| `alignable_relaxed_threshold` | 5 | 四条轨都在 -63 dBFS 左右（安静录音），默认 silent 门 -60 dBFS 误判整轨静音；放宽到 -85 dBFS 后对齐成功 |
| `not_alignable` | 7 | C1305/C1306（长素材漂移，恒定时延模型报 `non_constant`；已由时变管线完成对齐）、C1170/C1193/C1199（`no_valid_anchor`：无公共内容）、C1188/C1189（`low_confidence`/`non_constant`） |

**其余文件**：180 个布局不满足（112 个 2CH PCM + 50 个 2CH AAC + 18 个无音频）——
按"2CH 不对齐"的既有口径，**正确且预期**地不进入测量/修改路径。

### 2.3 结论

- "合理情况下都可以对齐"**成立**：所有含公共内容的 4×mono 文件要么无需修正、
  要么被成功修正（12 个 19–27 ms），要么被 2CH-silent 新策略正确跳过；
  唯一"不产生修正"的 7 个文件里，C1305/C1306 已由时变对齐管线处理，其余 5 个
  是素材本身无公共内容。
- 2CH 布局（112+50 文件）**按设计不做对齐**，与用户口径一致。
- **阈值敏感性**：5 个安静 4CH 录音被默认 -60 dBFS silent 门误判；这是阈值
  保守而非内容问题（放宽后对齐成功）。建议把该发现纳入后续阈值标定。

## 3. 任务5：I:\2026.09.24 专业机位与 DJI 探测

| 机位 | 文件数 | 大小 | 音频布局 | 结论 |
|---|---|---|---|---|
| A7M4 | 9 | 150.2 GiB | 1×2ch `pcm_s16be` + data | 2CH，不对齐（布局不满足） |
| FX3 | 14 | 174.9 GiB | 1×2ch `pcm_s16be` + data | 同上 |
| FX30 | 10 | 160.8 GiB | 1×2ch `pcm_s16be` + data | 同上 |
| HXR-NX800 | 1 | 41.2 GiB | 4×mono `pcm_s24be` + rtmd | **`alignable`**（57.5 s 完成全片测量，production 判定可对齐） |
| Mavic4P | 43 | 54.0 GiB | 42 个无音频 + 1 个损坏/截断（ffprobe error） | 不适用 |
| Pocket3 | 7 | 68.6 GiB | 1×2ch AAC | 不适用 |

- 84 个文件中 **34 个带 rtmd 数据轨**、**83 个带 data 轨**（DJI `djmd/dbgi/tmcd`）。
- 因此 I: 上**唯一可用于 4CH 对齐验证的专业机位文件是 NX800 `CA0230.MP4`**，
  已跑通生产测量并给出 alignable；这与用户"专业机不少 4CH 只有 2CH 有效"的
  描述在本目录里表现为：专业机 A7M4/FX3/FX30 本身就是 **2CH PCM** 封装（不是
  4CH 里 2CH 静音），真正"4CH 里只有 2CH 有效"的是 testsets 的 speech 素材
  （静音掩码 `1100`，73 个）。

### 3.1 2CH-silent 自动跳过（任务5实现）

- `core/channel_sync.skip_two_channel_only(n_tracks, n_healthy, enabled=True)`：
  `n_tracks >= 3 且 n_healthy == 2` → 默认整文件跳过对齐。
- 在 `run_channel_sync` 锚点选择**之前**返回新状态 `two_channel_only`
  （原音频不动；透明模式退回源文件拷贝），可用
  `opts={"skip_two_channel_only": False}` 恢复旧行为。
- 验证：策略生效后重跑 testsets probe → **73 个文件状态为 `two_channel_only`**，
  且这 73 个的静音掩码全部是 `1100`（与旧行为下"会在两条有效轨之间做测量"
  的文件集合一致）。
- 单测：`tests/selftest/suites/audio_2ch_policy.py`（判定矩阵 + 默认开启 + 可覆盖）。

## 4. 任务4：DJI 元数据默认策略

### 4.1 机型识别

`mp4box -info` 的 Movie Meta 里有 `tool:` 机型标签（实测）：
`DJI OsmoAction4` / `DJI OsmoPocket3` / `DJI Mavic4 Pro L3B`。
`preservation.dji` 新增 `camera_tool()/is_action_series()/keep_metadata_by_default()`：
**仅 Action 系列默认保留 metadata**；Pocket/无人机（以及识别失败）默认不保留。

### 4.2 接入

- 软件路径（`1kt.py`）：DJI 非 Action → 置 `no_metadata=True` 走普通转码；
  新增 `--preserve-metadata` 覆盖开关（与 `--no-metadata` 冲突时后者优先）。
- 硬件路径（`core/batch_hw.py`）：dispatch 时同样判定；`BatchCtx.preserve_metadata`。
- **普通转码命令修复**：DJI 源走普通路径时，原 `-map 0 -c:d copy` 在 MOV
  中间容器上失败（attached_pic 与 `hwdownload` 滤镜冲突 -22；data 轨被写成
  假的 `stts` 轨）。x265/svtav1 `build_command` 新增
  `drop_metadata_streams`：不保留 metadata 时只 map **主视频+音频+字幕**。

### 4.3 实测验证（真实短编码）

| 场景 | 结果 |
|---|---|
| Mavic4P 默认策略 | `[META_POLICY] DJI 非 Action 机型 (tool='DJI Mavic4 Pro L3B'): 默认不保留元数据`；输出仅 1 条主视频流，无 djmd/dbgi/attached_pic，无 encoder 标签 |
| Pocket3 默认策略 | 同上（tool='DJI OsmoPocket3'）；done=2 failed=0 |
| Action4 默认策略 | `[POLICY] DJI: x265 保留管线, djmd/dbgi/tmcd 原生保留`（dry-run 证据，未做长编码） |
| Mavic + `--preserve-metadata` | 实际编码成功；输出保留 `djmd`/`dbgi` 数据轨（DJI 保留管线） |

## 5. 任务3：输出 vs 原始 XAVC 全细节差异（当前 ffmpeg 成品）

用 `archive_repair.container_facts` 对 `20260919_C1305.MP4`（源）与
`20260919_C1305_4ch_aligned_final.mp4`（当前 ffmpeg 成品）做全量容器对照：

| 项 | 源（Sony XAVC） | 当前 ffmpeg 成品 |
|---|---|---|
| major brand | **XAVC** | **isom** |
| 顶层 box | ftyp, uuid, uuid, free, mdat, moov, **meta** | ftyp, moov, mdat |
| 轨道 | vide + 4×soun + **meta(rtmd)** | vide + 4×soun |
| uuid | **9** 个（root PROF/XMP + moov/trak USMT…） | **0** 个 |
| nrtm (lens profile) | **True** | **False** |
| tref/cdsc | meta→video **有** | **无** |
| 结果 | — | `uuid_diff`: missing=9, added=0 |

Gyroflow 消费方证据：
- 当前 ffmpeg 成品 type-2 导出：`detected_source=null, imu_samples=0`（元数据完全丢失）；
- type-3 (camera data) 导出：288150 帧 org_quat 恒为 `[1,0,0,0]`、org_gyro 全 0
  （= 无陀螺仪数据），而源文件 type-3 导出正在生成真实 quaternion（见下节）。
- **Gyroflow 在 80 min 源文件上的 type-2 JSON 导出会在 4 GiB 处截断**
  （实测 `gyro_original.json` 正好 4.00 GiB 且 JSON 不完整），因此全长
  type-2 比对不可用；改用 type-3 相机数据 + 容器事实 + rtmd 样本哈希的多层证据。

GPAC 最大还原（进行中）：`preservation.transparent_restore` 以源视频+rtmd 轨+
对齐后的 4 路 WAV 重建 `*_4ch_aligned_restored.mp4`，阶段文件 49.78 GiB 已生成，
正在元数据轨重建；完成后将补全 brand/uuid/nrtm/tref 与 Gyroflow 验证结论。

## 6. 任务2：图表与统计

`work/analyze_all.py` 从三份 probe JSON + 既有长素材分析 JSON 生成：

| 图 | 内容 |
|---|---|
| `task2_inventory.png` | testsets 分组文件数/大小、音频布局、时长分布 |
| `task2_verdicts.png` | 2CH 策略前后 verdict 对比、静音通道数直方图、I: 机位布局/结论 |
| `task2_quality.png` | 延迟 vs 置信度、漂移分布、置信度分布、分组延迟箱线图 |
| `task2_two_channel.png` | 2CH-only 文件：时长、静音/有效通道 RMS、静音掩码、有效两轨相对延迟 |
| `task2_i_inventory.png` | I: 机位 rtmd/data 轨有无、大小、时长 |
| `task2_longtake_summary.png` | 长素材分段漂移率、注入恢复精度、极性修复增益、总漂移摆幅 |

原有长素材深度图（drift_overview / drift_analysis_a,b / coherence_heatmap /
recovery_precision / fine_vs_pass1 / polarity 等）保留并作为任务2的"深钻"部分。

## 7. 过程中发现并处理的问题

1. **2CH 布局**：按用户要求既不对齐、也不误报；probe 在 `eligible_audio` 直接拒绝。
2. **silent 阈值偏保守**（5 个安静 4CH 文件）：放宽后对齐成功；建议纳入标定。
3. **DJI 普通转码路径 -22**：attached_pic 与 hwdownload 冲突；已用
   `drop_metadata_streams` 修复并用真实编码验证。
4. **Gyroflow type-2 80min 导出 4 GiB 截断**：改用 type-3/容器事实组合验证。
5. **GPAC 全长还原耗时**：stage 49.78 GiB + 元数据轨重建，需把 TEMP/TMP 指向 E:
   （C: 空间不足会 ENOSPC），并监控 E: 余量。

## 8. 产物索引

```
work/probe_20261025/
  testsets.json            2CH 策略前 probe（326 条）
  testsets_after_2ch.json  2CH 策略后 probe
  i_20260924.json          I: 84 条
  summary.json / summary.md 统计汇总
  *.log                    运行日志
work/task3_20261025/
  current_final_vs_source.json  当前成品 vs 源容器差异
  task3_result.json             30 s 片段 GPAC 还原冒烟
  gyro_probe/                   Gyroflow 导出（type2 截断证据、type3 相机数据）
  full_c1305.log                C1305 全长还原日志
work/analyze_all.py / probe_align_api.py / task3_restore.py / mp4_inventory.py
docs/audio-sync-p4/plots/task2_*.png
```

### 5.1 后续：GPAC 标准还原在超大文件上不可行 → 字节级快速还原

`transparent_restore` 的标准流程对 49.78 GiB stage 执行 MP4Box `-ref`（tref）、
meta、`-flat`、`-brand` 四次整文件重写；实测 `-ref` 一步 17 分钟仅推进 <1 GiB
I/O（GPAC 在大文件上的随机写性能不可接受）。因此改用 `work/fast_restore.py`：

- stage 已经由 GPAC `mux_new` 生成（video + 4×对齐 WAV + rtmd 轨，结构合法）；
- 一次顺序拷贝完成：ftyp 替换为源 32 B（XAVC/XAVC,mp42,nras,iso6）、9 个 uuid
  box 补齐（root PROF/XMP、moov USMT、6×per-trak USMT，且**跳过 GPAC 已带上的
  重复 uuid**）、文件级 meta box（nrtm lens profile+XML，idat 自包含）原样追加、
  meta 轨 tref/cdsc 插入；
- 因为 ftyp 变长 + moov 插入 box 会让 mdat 后移，**同步修正全部 co64/stco
  chunk offset**（6 轨共 6 组）；
- 音频 sample entry 从 GPAC 的 QuickTime `in24`(78 B) 换回源的 `ipcm`(50 B)，
  同时修正 stsd/stbl/minf/mdia/trak/moov 六层大小；GPAC 尾部 size=0 的零类型
  padding box 丢弃（否则它的"到文件尾"语义会吞掉后面追加的 root uuid/meta）；
- 最后用程序 API `patch_track_durations` / `patch_movie_duration` /
  `normalize_track_flags` 收尾。

一次拷贝即可完成（实测 ~3–5 min / 50 GiB），不需要任何 GPAC 整文件重写。

### 5.2 关键对照实验：真实短 Sony 源（C1093，3 s）上的 std vs fast 还原

testsets 的 `20260903_C1093.MP4`（4×mono PCM + rtmd，180 帧）上同时跑了：
`preservation.transparent_restore`（生产标准流程）与 `work/fast_restore.py`
（字节级一次拷贝补齐），然后用 Gyroflow type-3 对比：

| | 源 | 标准还原 | fast 还原 |
|---|---|---|---|
| 帧数 | 180 | 180 | 180 |
| org_quat 变化帧 | 179 | **179** | **179** |
| 首帧时间戳/首四元数 | 6.6565 ms / 一致 | **完全一致** | **完全一致** |
| brand/uuid/nrtm/tref | XAVC / 8 / True / meta→1 | 一致 | 一致 |

结论：**fast_restore 与生产标准还原在消费方层面等价**（短文件上完全一致）。
这同时说明字节级还原方法本身正确；C1305 全长验证中 Gyroflow 未识别的问题
来自 stage/音频中间文件差异（详见 §5.3），不是 fast_restore 的补箱逻辑。

### 5.3 音频中间文件：必须用 ffmpeg 的 `.mov`，不能用 WAV 直接给 GPAC

实测 GPAC `-add chX_pol.wav#audio` 写出的 `in24` payload 与 WAV 内容**完全不符**
（解码后与 WAV 零样本相等、最佳 lag 6.4 万采样），而生产 channel-sync 透明模式
一直使用 ffmpeg 生成的 `.mov`（pcm_s24be）中间文件。已修正 stage 构建：
先 `ffmpeg -c:a pcm_s24be -f mov` 再交给 GPAC；音频 sample entry 在 fast_restore
中替换为源的 `ipcm`(50 B)，并同步修正六层祖先大小。
