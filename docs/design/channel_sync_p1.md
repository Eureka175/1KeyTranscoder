# `--channel-sync` P1 设计文档 (algo 2.3.0-p1)

> 任务书: `.dsh-drop/…/channel_sync_phase1_prompt_v3.md`（P1 最终验收清单 §23）
>
> 状态: 已实现并全量回归（L1/L2/L3）。本文件是 P1 的正式设计记录。

## 1. 定位

P1 把 `--channel-sync` 从"可工作"提升为**可放心用于真实 Sony/DJI 多轨
素材预处理**的生产功能。核心原则：**正确性优先、默认整数移位、轨道级
安全降级、透明路径优先、避免无必要的重采样和滤波**。

> **宁可单轨少修，也不要错修；但不能因为某一轨无信号或无法可靠测量，
> 就放弃其它已经得到充分验证的健康轨道。**

P1 只做"文件内时间对齐"。"设备固有 latency 标定"、"麦克风空间位置补偿"、
"漂移分类与 resample"、"fractional delay 高精度修正"均保留到后续独立阶段。

## 2. 代码结构

| 文件 | 职责 |
|---|---|
| `core/sync_estimate.py` | 两阶段 GCC-PHAT（8kHz 粗扫 + 全速率精测）+ 相位斜率加权 LS 精估 + 帧轨迹统计 + 恒定性二分 + 边缘堆积判定 |
| `core/sync_fix.py` | 纯整数样本流式移位（有界窗口读取 + 普通文件句柄分块写，尾补零，样本值不变）+ 修后复检（局部 GCC + 相位斜率） |
| `core/channel_sync.py` | 集成层：`DEFAULTS` / `effective_opts` / `eligible_audio` / `run_channel_sync` / `repair_remux_timescale` |
| `core/mp4_channel_sync.py` | **vendored（ChronoSync 1.x, MIT），P1 未修改**；保留作回滚/对照/历史算法参考，P1 主路径不再引用 |

`preservation/` 未修改（P1 约束）；transparent 重封装复用既有 GPAC 后端
（`preservation/gpac.py` + `preservation/isobmf.py`）机制，按轨引用源文件
以保留 data/meta 轨。**新增辅助 `repair_remux_timescale()`（集成层）修复
既有 classic 重封装路径的 mvhd timescale 失配缺陷（见 §9），未改动
`preservation/` 本身。**

## 3. 数据与精度约定

- 方向: `delay > 0` = 目标轨比锚点**晚到** → 修正 = 整体前移 delay
  （估计/修正/复检/JSON/日志全链路一致）。
- 估计数学全程 float64; 中间 raw 存储:
  `pcm_s16/s24 (le/be) → f32le`, `pcm_s32 (le/be) → f64le`,
  `pcm_f32 (le/be) → f32le`。
- float PCM 不 clamp 到 [-1,1]（允许 |x|>1.0 的合法过 0dBFS 录音）;
  仅检测 NaN/Inf; 整数移位不改变样本值。
- 输入读取为**有界窗口流**（`RawStream`：`seek`+`read`，1 MiB 块 + LRU
  读缓存，每流固定约 4 MiB；**禁 `np.fromfile()` 全量加载，也不使用
  `np.memmap` 整文件映射** —— v0.6.0 的整轨 memmap 会让每条轨的每个被触碰
  页常驻工作集，10 min/4ch/48k 下每轨 +110 MB，见 §10.1）;
  FFT 尺寸固定（`next_fast_len`）、整数帧位、无 RNG → 同输入两次运行
  JSON 逐字节一致（L3 C19 验证）。
- **语义**: 报告的 delay 是"当前文件内 target 相对 reference 的观测到达
  时差"（可含电子/无线链路延迟 + 录音链路差 + 麦克风物理位置的声学传播
  差），不自动等价于设备 latency；P1 不实现设备延迟数据库与声学传播差
  建模，也不根据 delay 大小推断麦克风位置。

## 4. 输入资格（`eligible_audio`，文件级）

| 条件 | 行为 |
|---|---|
| 音频流 ≥ 3 | 否则 `not_eligible`（1ch/2ch 布局默认不做对齐 — 用户决定） |
| 每流 `channels == 1` | 否则 `not_eligible`（立体声内部相位关系不可破坏） |
| codec ∈ 线性 PCM 八类 {s16/s24/s32/f32 × le/be} | 否则 `not_eligible`（压缩/非线性如 aac/alaw 一律拒）。**用户决定: 大小端无所谓都支持**（实测 A7M5 XAVC-S 为大端 s24be）；任务书 v3 原列小端四类，经真实素材标定放宽 |
| 全部采样率相同 | 否则 `not_eligible` |
| 采样率 ∈ {48000, 96000} | 否则 `not_eligible`（**44.1k 显式拒绝**, 不 silently 跑旧算法） |
| 每轨时长 ≥ `min_audio_seconds` | 轨道级不可测（`insufficient_frames`）; 不影响其它轨 |

## 5. 轨道级健康与决策（部分成功语义）

每轨独立判定（`decision ∈ {anchor, fixed, already_aligned, untouched}`）：

| reason | 含义 | 行为 |
|---|---|---|
| `silent_track` | 整轨 RMS < `silent_rms_dbfs`(-60) | untouched, 不阻止其它轨 |
| `non_finite` | 解码后 NaN/Inf | untouched |
| `insufficient_frames` | 有信号的帧本身就太少（稀疏内容/素材过短; 窄窗+宽窗均不足） | untouched |
| `low_confidence` | 有信号帧但相关性/置信度不足（含窄窗+宽窗均不可测; 相位精估退化） | untouched |
| `non_constant` | MAD / 极差 / 漂移 ppm 三门任一不过 | untouched |
| `out_of_range` | 真实时差超搜索窗（宽窗复测确认） | untouched |
| `recheck_residual` | 修正后复检残差 ≥ `verify_max_ms`(0.05ms) | **仅该轨回退** untouched |
| （无） | \|delay\| < `aligned_max_ms`(0.05ms) | already_aligned |
| （无） | 其余 | fixed（整数移位） |

- 只有"无有效锚点 / 全部健康 target 均不可靠 / 解码或回编码失败"才升级
  为文件级失败（`measure_failed`/`verify_failed`），此时整文件原音频不动。
- **合法**: CH1 silent → unchanged; CH2 fixed; CH3 anchor; CH4 aligned。
- **不合法**: 猜一个 delay 强修低置信轨；或因为 CH4 复检失败回滚 CH2。

## 6. 估计与修正

### 6.1 两阶段 GCC-PHAT（`sync_estimate.estimate_pair`）
1. 8kHz 粗扫: 分块 FIR 抽取（±16 coarse 样本余量抗暂态，与全量 decimate
   bit 级一致），200ms 帧 / 100ms hop，±`search_window_ms` 内取峰;
2. 全速率精测: 按粗延迟把搜索收窄到 ±8 样本，逐帧整数延迟;
3. 全局延迟 = 合格帧中位数; 置信度 = 合格帧置信度中位数
   （合成沿用 vendored 1.x 公式: 高度/次峰比/prominence 加权）;
4. 相位斜率精估: 中部 `anchor_segment_seconds`(30s) 锚段，整数补偿后
   带内加权 LS（权重 = |G(f)|），输出 `fine_delay_samples/fine_delay_ms/
   fine_fit_r2` — **仅作诊断读数与复检依据，不驱动修正**;
5. 反相检测: 锚段白化相关面 ±64 样本窗正/负峰 1.2 倍规则，仅记录。

### 6.2 恒定性（P1 二分）
`constant` ⇔ MAD ≤ `mad_max_ms`(1.0) **且** 极差 ≤ 同值 **且**
漂移门不过。极差门覆盖"跳变后中位数退化"情形；`step_min_ms`(3.0) 仅作
诊断警告（跳变/漂移细分属二期）。

**漂移门 = ppm 门 + 材料性门（真实素材标定）**:
`|drift_ppm| > constant_max_ppm`(5.0) **且** 全片预测漂移
`|drift_total_ms| > drift_min_ms`(0.1) 才判 non_constant。

标定依据（testsets 20260903+04, 137 段 A7M5 实测）:
- 整数样本量化噪声会让**已对齐轨**的偶然拟合斜率落在 5–30 ppm，而极差
  仅 2–11 样本（0.04–0.23 ms）— 单看 ppm 会误判为"非恒定"，掩盖"该轨
  本来已对齐/本可修正"的事实;
- 真实慢漂移轨的斜率同样在 39–49 ppm，但预测漂移达 0.42–1.9 ms
  （C1154 CH1 轨迹 1202→1235 样本单调；C1159 CH2 968→1058 / 42 s
  ≈ 49 ppm）— 远超 0.1 ms 门，仍被正确拦下（P1 不做 resample，
  漂移轨保持原音频 + 明确 reason）。
- 该门使"恒定但含量化噪声"的轨可正常进入修正，同时不放松对真实漂移
  的拦截；`traj_drift_ms` 写入报告供后续标定。

### 6.3 超窗判定（out_of_range）
窄窗无可测帧（或轨迹证据覆盖率 < `min_usable_fraction`(0.6)，大洞时
恒定性不可靠）→ 宽窗（`wide_search_ms`(250.0)，≥3× 窄窗）复测:
- 宽窗测出且 |delay| > 窄窗 → `out_of_range`;
- 宽窗测出且在窄窗内（窄窗边缘置信不足的合法近边缘时差）→ **采用宽窗
  估计**继续常规门;
- 宽窗仍不可靠 → `insufficient_frames` / `low_confidence`。
另对窄窗合格帧做边缘堆积检查（≥搜索窗-0.5 样本的比例 > 50% → 宽窗确认）。

### 6.4 修正（`sync_fix.shift_stream`）
`shift_samples = int(np.rint(delay_samples))`; `out[n] = in[n + shift]`,
正数前移、越界补零、输出样本数 == 输入、分块（`fix_chunk_seconds`=60s）
memmap 搬移、样本值逐位不变、无滤波/无插值/无块间状态。
48k 下最大量化残差 0.5 sample ≈ 10.4µs; 96k ≈ 5.2µs。

### 6.5 复检（`sync_fix.recheck_residual`）
修后流 vs 锚流: 锚段 ±5ms 局部 GCC 整数粗扫 + 相位斜率精估（退化时回退
整数值）。门 `verify_max_ms`=0.05ms ≈ 2.4 样本 @48k — 整数移位的量化
残差（≤0.5 样本）远在门内，门只拦明显超差（区分"修正动作: 整数"与
"测量结果: 相位斜率"）。

## 7. 锚点回退

候选顺序 `anchor_candidates = [2, 3, 0, 1]`（CH3 > CH4 > CH1 > CH2），
仅**健康轨**可当选；当选条件 = 与至少一条其它健康轨得到有效估计
（usable ≥ `min_usable_frames` 且 conf ≥ `min_confidence`）。CH3 只是
默认优先级最高，不是语义上的真值时间基准。全部失败 →
`measure_failed / no_valid_anchor`。报告记录实际命中的 `anchor_stream`。

## 8. transparent 模式

CLI `--channel-sync-transparent`（隐含 `--channel-sync`）; 1kt.py 顶层
独立流程（不依赖编码器配置）。行为:
- 视频 + 全部非音频（data/meta）流 **stream copy**（GPAC `-new` 按
  `src#video` / `src#track_id` 引用源轨）; 仅 `fixed` 音轨重新生成;
  `untouched` 轨保持原始内容（stream copy）;
- 全部已对齐 → 输出 = 源文件字节级拷贝（SHA256 相同，L3 C18 验证）;
- 部分成功 → 某一轨失败不阻止其它健康轨同步（L3 C17 验证）;
- 文件级失败 → 输出 = 源文件原样拷贝（音频原样，reason 保留）;
- 时长修复同 classic（§9）。

transcode 模式输出: `audio_files`（全轨有序清单: fixed 轨 = 移位后按源
codec 回编码; 其余轨 = 源轨 stream copy）+ `fixed_files`（实际移位子集）;
宿主（Sony `audio_sources` / DJI `audio_sources` / classic remux）用
`audio_files` 整体替换音轨，untouched 轨 bit-exact 保留。

## 9. 既有缺陷修复: classic 重封装 mvhd timescale 失配

`preservation/audio_sync.remux_replace_audio` 以**源文件** mvhd timescale
（rigaya 中间件 = 1000）调用时长修复，而 GPAC 26.02 `-new` 输出 mvhd
timescale 实为 3000（不采纳 `-timescale 1000`）→ tkhd/elst 被写成 1/3
时长（8s 文件 → 2.667s），音频解码被 elst 截断。P1 约束不修改
`preservation/`，故在集成层新增 `repair_remux_timescale()`：从**输出文件
自身**读取真实 mvhd timescale 后重跑同一修复（stts 始终精确，是可靠
事实来源），接入全部 classic 重封装调用点与 transparent 重封装。

### 9.1 音频中间文件 sample entry 失配（真实素材短集成实测发现）

真实 A7M5 素材（XAVC-S LPCM）音轨的 sample entry 是 **`ipcm`**
（`ffprobe codec_tag_string`）。P1 的音频中间文件原先以 `-f mov` 写出，
而 ffmpeg 的 MOV muxer 对 PCM 一律写 QuickTime 条目（`in24` / `sowt` /
`twos` / `in32` / `fl32`）→ 源音轨被替换后容器里变成 `in24`，触
`preservation/validate.py` 中 `critical_modified` 的 `audio.tracks` 关键项
→ 整个文件 `--check basic` 失败、rc=1、**不产出文件**。

实测影响面：**只要 channel-sync 真正修正了某条轨就会触发**（137 段中
11 段 `applied` 全部命中）；未修正的文件因不替换音轨而正常，所以此前
137 段**算法级**扫描（不经过转码管线）看不到它。既有合成用例用的是
`pcm_s24le`（MOV 下本也是 `in24`），因此 L1/L3 断言也覆盖不到 —
这是只有"真实素材 + 完整封装管线"才能暴露的集成缺陷。

修复（满足 P1 约束：**不修改 `preservation/`**）：在 `core/channel_sync.py`
新增 `_muxer_for_entry()`，**按源轨自身的 sample entry 选择能复现同一条目
的 muxer**：

| 源 sample entry | muxer | 产物条目 |
|---|---|---|
| `ipcm` / `fpcm`（ISO/Sony XAVC-S） | `-f mp4` | `ipcm` / `fpcm` |
| `in24` / `in32` / `sowt` / `twos` / `fl32` / 未知 | `-f mov`（原行为） | 同源 |

两处写盘点（fixed 轨回编码、untouched 轨 stream copy）均已接入；文件名与
调用方签名未改动。

验证（真实素材）：`20260904_C1183` / `20260904_C1209` 修复前 rc=1 且无
产出，修复后 rc=0；输出音轨 sample entry 与源逐轨一致（`ipcm`×4）；输出
复测显示被修正轨残差 0.0 ms 且 `untouched` 轨逐值不变。回归钉：
`l1_channel_sync_p1` 增加 3 条 `_muxer_for_entry` 断言。

## 10. P1 新算法 vs vendored 1.x 对照

| 维度 | vendored 1.x (`core/mp4_channel_sync.py`) | P1 (`sync_estimate` + `sync_fix`) |
|---|---|---|
| 测量 | 全长单次 GCC-PHAT + 三窗恒定性 | 两阶段（8k 粗扫 + 全速率精测）分帧轨迹 |
| 亚样本 | 抛物线插值 | 相位斜率加权 LS（诊断/复检用） |
| 修正 | 整数 + 窗 sinc 分数移位（滤波） | **纯整数移位**（无滤波/无插值, 样本值不变） |
| 复检 | 全长 GCC 残差 | 锚段局部 GCC + 相位斜率（退化回退整数） |
| 参考 | 固定 CH3 | 候选回退 [CH3, CH4, CH1, CH2] |
| 轨道语义 | 整文件全有或全无 | **轨道级部分成功**（silent/non_finite/低置信/非恒定/超窗/复检差 → untouched） |
| 超窗 | 无概念 | 宽窗复测 → `out_of_range` |
| 输入边界 | 宽松 | 48k/96k 限定、44.1k 显式拒绝、小端 PCM 四类、逐轨最短时长 |
| 内存 | `np.fromfile` 全量加载 | **有界窗口流**（`RawStream`，每流约 4 MiB 读缓存）——v0.6.0 曾用整轨 memmap，实测**随时长线性增长**（10 min/4ch/48k 每轨 +110 MB），v0.6.1 修复为与时长无关（§10.1） |
| 性能 | — | 10min/4ch/48k 全流程（解码+估计+修正+回编码）实测 ~31s；算法 benchmark（无重编码）Peak RSS 205–215 MB / 46.9–52.7 s（v0.6.1 实测） |

### 10.1 内存（v0.6.1 流式修复）

P1 最初用 `np.memmap` 做"分块流式"读取，并把它当作低内存实现——**这是错的**。
映射本身不占内存（实测增量 +0.0 MB），但每个被触碰的 file-backed 页都计入
进程 WorkingSet 并长期保留，而实现确实顺序走完了每一条轨的每一个字节：

| 代码路径 | v0.6.0 实测内存后果 |
|---|---|
| `channel_sync._track_health()` 分块扫描整轨算 RMS/有限性 | **每轨 +110 MB**（单轨 raw = 28,800,760 样本 × 4 B = 109.9 MB；4 轨 +440 MB） |
| `estimate_pair()` 5999 帧逐帧读完两轨 | 确立整轨常驻 |
| `shift_stream()` `np.memmap(mode="w+")` 写整轨 | 输出整条文件脏页常驻（峰值 +36 MB） |
| `recheck_residual()` 读 30 s 锚段（读取范围本身正确） | 叠加锚段 FFT ~+45 MB（有界） |

阶段探针实测峰值 **719.0 MB**，其中约 **440 MB（61%）来自整轨映射页驻留**，
与 60→600 s 的线性增长完全吻合（151.6 → 605.4 MB）。

v0.6.1 以 `RawStream`（`seek`+`read`，1 MiB 对齐块 + 4 槽 LRU，每流约 4 MiB）
替换整文件映射，`shift_stream()` 输出改普通文件句柄，并把
`estimate_pair`/`recheck_residual` 拆为「所有权包装 + 纯计算」。

**逐位一致**：块寻址落在与 memmap 相同的文件偏移、dtype 映射未变、
`_track_health` 的分块单位（`range(0, n, 1<<20)`，单位=样本）与 f32→f64
转换点未变，故 RMS 平方和归约顺序一致。新旧差分 49 用例 46 项完全相同
（余 3 项为同类型异常文案与绝对临时路径），137 段真实素材逐文件报告 JSON
**137/137 字节相同**。

**结果**：600 s/4ch/48k 峰值 RSS 719.0 → **205–215 MB**，
`_track_health` 每轨 **+110 MB → +3.9 MB**，且 RSS 不再随时长增长
（60/150/300/450/600 s = 208.1/228.8/208.5/264.6/209.3 MB）。
详细审计与验证见 `work/channel_sync_memory_audit.md`、
`work/stage12_memory_validation.md`。

## 11. DEFAULTS（全部为初值, 待真实素材标定）

```python
DEFAULTS = {
    "channel_sync_transparent": False,
    "min_audio_streams": 3,
    "supported_sample_rates": [48000, 96000],
    "supported_codecs": ["pcm_s16le", "pcm_s16be", "pcm_s24le",
                         "pcm_s24be", "pcm_s32le", "pcm_s32be",
                         "pcm_f32le", "pcm_f32be"],
    "min_audio_seconds": 2.0,
    "silent_rms_dbfs": -60.0,
    "search_window_ms": 80.0,
    "wide_search_ms": 250.0,
    "frame_ms": 200.0, "hop_ms": 100.0, "coarse_rate": 8000,
    "anchor_segment_seconds": 30.0,
    "min_confidence": 0.3, "min_usable_frames": 5,
    "min_usable_fraction": 0.6,
    "fine_phase_band_hz": [200.0, 8000.0],
    "frame_min_rms_dbfs": -50.0,
    "mad_max_ms": 1.0, "step_min_ms": 3.0, "constant_max_ppm": 5.0,
    "drift_min_ms": 0.1,       # 漂移材料性门 (真实素材标定)
    "verify_max_ms": 0.05, "aligned_max_ms": 0.05,
    "fix_chunk_seconds": 60.0,
    "anchor_candidates": [2, 3, 0, 1],
    "max_lag_seconds": None,   # 旧键兼容 -> search_window_ms
    "algo_version": "2.3.0-p1",
}
```
可经档位 JSON 的 `channel_sync` 节覆盖（`effective_opts` 归一）。

## 12. JSON 报告（`channel_sync_<stem>.json`）

顶层: `status / detail / source / algo_version / anchor_stream /
sync_mode(transcode|transparent) / result_scope(file|partial) /
channels / audio_files / fixed_files / output_file / file_copied /
log_dir`（`reference_stream` 为兼容旧键 = anchor）。
逐轨: `stream / delay_ms / delay_samples / confidence / polarity /
drift_ppm / constant / warnings / decision / reason / fine_delay_ms /
shift_samples / fractional_part_samples / storage_dtype /
traj_mad_ms / traj_spread_samples / traj_drift_ms / usable_frames /
rms_dbfs`（`rms_dbfs` / `traj_drift_ms` 为真实素材标定新增的诊断字段）。

## 13. 测试矩阵（tests/full_autotest.py）

- **L1 `channel-sync P1`（26 断言, 纯逻辑无外部工具）**: DEFAULTS v3 /
  eligible 边界（44.1k/96k/混合采样率/大小端 PCM 接受/非 PCM 拒绝） / shift_stream
  正负整数与分数 rint、样本值不变、有界窗口流读取 / 48k+96k 整数与分数精估 /
  复检（整数残差 <0.05ms、0.4 样本残余检出）/ 中途 50ms 跳变与 10ppm
  漂移 → non_constant / 100ms 超窗（窄窗不可测+宽窗测出）/ 反相 /
  bit 级确定性。
  **v0.6.1 新增内存回归 3 条**：`p1.路径输入为有界窗口流 (非整文件映射)`、
  `p1.窗口读取与 ndarray 切片一致`、`p1.越界窗口自动裁剪`、
  `p1.64MB 整轨扫描后工作集增量有界 (<= 32 MB)`（旧整轨 memmap 实现会 +64 MB）。
- **L3 `channel-sync P1 E2E`（20 断言）**:
  C15 转码轨道级部分成功（CH1 silent untouched + CH2/CH4 fixed,
  scope=partial, 输出复测 aligned）; C16 对抗中途 50ms 跳变（CH2
  non_constant untouched, CH1/CH4 独立同步）; C17 transparent
  （视频 streamhash MD5 与源一致、untouched 轨 f32 逐字节一致、
  4 音轨 1 视频、复测 aligned）; C18 transparent 全对齐 → SHA256 与源
  一致; C19 同输入两次 JSON 逐字节一致; C20 10min/4ch/48k 单线程
  全流程 <120s（实测 ~31s）、RSS 增量 <512MB（实测 ~38MB）;
  C21 NaN/Inf 轨 untouched(non_finite) 且其它轨独立同步; C22 过短轨
  untouched(insufficient_frames); C23 transparent 非资格（2ch+视频）
  与纯音频探测回退 → 输出 = 源文件字节级拷贝; C24 极低 SNR（独立噪声
  轨）untouched 且其它轨同步; C25 全不相关 → measure_failed /
  no_valid_anchor; C26 复检门（verify_max_ms=0.005 + CH4 分数延迟
  1223.4 样本）→ 仅 CH4 回退 recheck_residual, CH2 照常 fixed。
- **L3 `channel-sync P1 算法级`（9 断言, 纯音频算法、不经过任何视频转码
  管线, 可单独执行）**: C27 负延迟端到端（CH1 早到 900 样本 → shift
  -900, 修正轨复测残差 0.00）; C28 96kHz 整数延迟; C29 s32le 源 →
  f64 存储管线; C30 f32le 源 + >0dBFS 内容（峰值 1.65）不钳位、整数
  移位后样本逐位不变; C31 近窗缘延迟（68.75ms）正常修正; C32 稀疏语音
  覆盖率门（min_usable_fraction=0.95）确定性触发宽窗采纳, 修正阶段
  使用采纳估计（回归: 修正阶段误用窄窗 NaN delay 的崩溃缺陷）。
- 既有 L1 `channel-sync`（vendored 算法包断言）与 L3 C14（转码端到端）
  保持并适配 P1 输入边界（C14 生成器改用 `pcm_s24le`）。
- 零回归: `--channel-sync` 关闭时输出与主线行为一致（C1–C13 不变）。

## 14. A7M5 真实素材 fixture 与真实数据标定

固定清单与测试说明见 `docs/fixtures/a7m5_channel_sync_fixtures.md`
（原始大文件不提交仓库, 仅路径引用; 覆盖空 CH1/CH2、不同物理位置、
CH3/CH4 高相关、已对齐、不同固定 delay、低相关等 10 类场景）。

### 14.1 真实素材扫描（testsets 20260903 + 20260904）

工具: `work/sweep_real_sync.py`（进程内 `run_channel_sync`，纯音频算法，
不经转码管线；源文件只读）→ `work/real_data_sync/summary.csv` +
每文件完整 JSON；分析: `work/analyze_real_sync.py` → `analysis.md`。

全部 4ch 素材 **137 段**（Sony A7M5，音频为 **pcm_s24be** 大端四轨单声道
48kHz）。标定后结果:

| 项 | 标定前 | 标定后 |
|---|---|---|
| 已对齐 (already_aligned) | 94 | 111 |
| 测量失败 (measure_failed) | 33 | 15 |
| 实际修正 (applied) | 10 | 11 |
| non_constant 轨数 | 71 | 52 |
| low_confidence / insufficient_frames 轨数 | 0 / 52 | 43 / 9 |

- **修正正确性复验**: 11/11 修正轨用算法复测 vs 锚轨，残差 **0.00 样本**；
  修正量分布 905–1222 样本 = **18.9–25.5 ms**（与用户实测无线麦延迟
  量级 19.7–29.5 ms 一致）。
- **剩余 non_constant 均为真实慢漂移**（spread 29–104 样本 = 0.6–2.2 ms，
  ppm 34–76，轨迹单调；如 C1154 CH1 1202→1235、C1159 CH2 968→1058/42s
  ≈49 ppm）→ P1 不做 resample，正确保持原音频。
- **被拒修的"有内容"轨确为弱相关/不相关**（C1157 CH1 vs CH3 全窗最大
  归一化相关仅 0.13–0.46 且峰值 lag 不一致 −51…−65 ms；C1173 ≈0.09–0.14）
  → reason 记为 `low_confidence`（有信号帧但相关性不足），与
  `insufficient_frames`（有信号的帧本身就太少）区分。
- **measure_failed 细分**: 四轨全静音 5 段（正确拒修）+ 有内容但无有效
  锚点 3 段 + 目标轨全部不可靠 7 段。
- 结论: 真实素材上"已对齐轨不被误修、真实延迟轨被正确修正、漂移轨与
  弱相关轨安全拒修"三条均成立；相对素材内部延迟量级 0.4–2 ms 的二阶
  漂移（P2 范围）不处理。

### 14.2 由真实素材驱动的标定改动

1. **codec 白名单**: 用户决定大小端都支持（线性 PCM 八类），唯一排除
   采样率 44.1kHz — A7M5 XAVC-S LPCM 实为大端 s24be，原小端四类会导致
   全部真实素材 `not_eligible`。
2. **漂移材料性门 `drift_min_ms`(0.1)**: 见 §6.2 — 量化噪声斜率（5–30 ppm）
   不再误判已对齐轨为 non_constant。
3. **reason 区分**: 有信号帧但相关性不足 → `low_confidence`；有信号的帧
   太少 → `insufficient_frames`（见 §5）。
4. **报告新增诊断字段** `rms_dbfs` / `traj_drift_ms`。

## 15. Real-world calibration baseline（真实素材冻结基线）

Dataset: **137 real Sony A7M5 4CH clips**（`testsets/20260903` +
`testsets/20260904`，4K60 XAVC-S MP4/MOV；音频 4×mono **pcm_s24be**
48kHz）。冻结基线工件：`tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`
（逐轨 548 行，纯文本，不含媒体）+ 同目录 `.md`（字段映射与空值约定）
+ `a7m5_real_137_analysis.md`（分析报告）。

| 项 | 值 |
|---|---|
| 素材 | 137 段 A7M5 4CH（枚举 164 个候选；正确排除 27 段非 A7M5：17 段无音频 DJI Air3S、9 段 2ch AAC、1 段无音频 MP4） |
| PCM | **8 linear PCM variants**: s16 / s24 / s32 / f32 × little-endian + **big-endian** |
| Sample rate | **48 / 96 kHz**；**44.1 reject** |
| 逐轨行数 | 548（137 × 4），全部 `pcm_s24be` / 48000 / 4 streams / entry **`ipcm`** |
| `drift_min_ms` | **0.1 ms**（材料性门，见 §6.2） |
| 固定延迟修正 | **11/11 successful，residual 0.00 sample** |
| 修正量 | 9 段 905–1222 samples = **18.9–25.5 ms @ 48 kHz**；另 2 段 −37/−39 samples = −0.77/−0.81 ms。**完整范围 37–1222 samples = 0.77–25.46 ms** |
| real drift（正确拒修） | **39–76 ppm**，总漂移 **0.4–2 ms** |
| 文件级结果 | already_aligned 111 / applied 11 / measure_failed 15 |
| 锚点分布 | CH3 500 轨 / CH1 8 轨 / 无锚点 40 轨（**无 CH4 锚点样本**） |

**边界声明**：

```text
P1 handles fixed / approximately constant delay.
P1 does not resample drift.
```

上表 39–76 ppm / 0.4–2 ms 的真实持续漂移属 **P2 范围**：P1 不处理并原样
保留该轨音频（宁可保持原音频，也不做错误修正）。

**可复现性**：基线由 `work/sweep_real_sync.py` + `analyze_real_sync.py`
重跑一遍并与首轮逐字段比对后冻结 — 548 行全部字段 **0 差异**、
`analysis.md` 逐行一致（仅 `summary.csv` 的 `dur_s` 墙钟列不同），
11/11 修正轨复测残差仍为 0.00 样本。注意 `reason` 是**诊断口径**（一个
文件可同时出现多种 reason），`already_aligned / applied / measure_failed`
才是互斥的最终状态层级，两者不可混在一起求和。

### 15.1 短素材端到端集成验收（production pipeline）

用同一批真实素材做完整封装管线的短素材集成验收
（`work/p1_short_integration.py`；7 个 `--channel-sync` 用例 + 5 个
`--channel-sync-transparent` 用例，**173 断言全绿**）：

| 用例 | 素材 | 覆盖点 |
|---|---|---|
| already_aligned (低置信) | C1157 | CH1/CH2 low_confidence → untouched |
| already_aligned (静音 CH1/CH2) | C1088 | 静音轨不参与估计、不制造虚假 delay；健康轨仍被评估 |
| applied + **锚点回退** | C1183 | CH3/CH4 均 low_confidence → **回退到 CH1** 成功修正 CH2 |
| applied (anchor=CH3) | C1209 | 观测轨间时差 +19.917 ms 被修正；同文件 CH2 真实漂移（24.844 ms、203 ppm）正确拒修 |
| non_constant | C1154 | 真实慢漂移（41.5 ppm）→ untouched |
| insufficient_frames | C1215 | 有信号但可用帧不足 → untouched |
| 文件级失败 (全静音) | C1083 | 无有效锚点 → 全部音轨原样 |
| transparent ×5 | 同上 | 全对齐→**SHA256 与源字节级一致**；applied→视频/非音频 **stream copy**（逐流 MD5 相同）且仅 fixed 轨改变；文件级失败→源文件原样拷贝 |

逐项断言：容器结构（音轨数/顺序/采样率/codec/时长/几何与帧率）、
Sony 元数据保留（rtmd/nrtm、`--check basic` 无 FAIL）、管线日志中的
`anchor` / `status` / 每轨实测 delay 与算法级参考报告一致、`fixed` 行的
shift 符号与量级同 delay 一致且复检残差 ≤ `verify_max_ms`，以及
**对最终输出独立复测**：`fixed` 轨 → 0.0 ms、`anchor`/`already_aligned`
轨 → 0.0 ms、`untouched` 轨逐值不变（未被静默改动）。

本次集成验收暴露并修复了 1 个真实缺陷（see **§9.1**：`ipcm` sample
entry 失配导致 applied 文件 rc=1、无产出），并新增 3 条 L1 回归钉。

> 真实素材的覆盖缺口（据实记录，未伪造通过）：137 段中
> **不存在**"某轨静音 + 同文件另一轨被修正"的组合（11 段 applied 全部
> 无静音轨），也**不存在** anchor=CH4 的样本。这两种组合由合成用例
> `l3_channel_sync_p1`（C15–C26）覆盖，见 §13。

## 16. 明确未实现（P1 边界, 勿顺手实现）

锚点完整评分 / 轨迹三分类（step/ramp）/ 漂移 `resample` / 全两两估计 /
设备 latency 数据库 / 声学传播差建模 / LUFS·包络·分频带降级 / 44.1kHz /
时间戳域修正 / fractional sinc 生产模式 / 修改 `preservation/` /
修改 vendored 模块 / 新增第三方依赖。
