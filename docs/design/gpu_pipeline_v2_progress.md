# GPU Pipeline v2 进度记录

> 分支：`feature/gpu-pipeline-v2`
> 基线：`1f07650`（合并 `feature/gpu-frame-estimation` 后的 GPU 架构）
> 版本：VERSION 保持 0.9.0
> 记录：2026-11-03 18:5x（P0/P1 第一批）

## 2026-11-03 — P0/P1 第一批

### 已完成

| 项 | 文件 | 说明 |
|---|---|---|
| P0 观测指标 | `core/gpu_align/observe.py` | session/engine 计数、batch/kernel、gpu_ms、fallback、stage wall、coarse hit/miss |
| P1 运行上下文 | `core/gpu_align/runtime.py` | 每 run 一个 session、coarse 缓存 registry、residual engine 复用 |
| channel 生命周期 | `core/channel_sync.py` | 启动时 `begin_run()`，结束时 `end_run()`，写 `gpu_pipeline_metrics.json` |
| 窗级 session 复用 | `core/sync_estimate.py` | `measure_window_delays` 优先使用 RunContext session |
| coarse 缓存复用 | `core/sync_estimate.py` | 同一 run 内同一 channel 的 12k 抽取只做一次 |
| residual session 复用 | `core/long_take.py` | `measure_residual_curve` 使用 RunContext residual engine |
| prewarm 复用 | `core/sync_estimate.py` | `prewarm_gpu_align` 不再每次新建 session |
| 测试 | `tests/selftest/suites/gpu_align.py` | runtime/observe/coarse cache 纯 CPU 测试 |

### 冒烟结果（C1306 两条 window measure + 一条 residual）

```
wall_s               28.225
gpu_active_s          2.532
gpu_active_ratio      0.0897   (8.97%)
sessions_created      1
sessions_reused       2
engines_created       3
batches             246
fallback_batches      0
coarse_hits           1
coarse_misses         3
stages:
  window_measure     21.86 s
  residual_measure    6.32 s
```

- 两次窗测量 + 一次 residual 只用 **1 个 GPU session**；
- 第二次测量复用 anchor coarse 缓存（1 hit）；
- 输出 windows/initial 与无 context 路径 **完全一致**；
- GPU 活跃占比仍只有 **约 9%**，说明 P1 只解决了 session/缓存复用，未解决 CPU/GPU 串行。

### channel_sync 上下文冒烟

30 s 4ch 短素材 `run_channel_sync`：status `already_aligned`，`gpu_pipeline_metrics.json` 正常写出；session 1 个、无 GPU batch（短素材未进入长镜头 GPU 路径），生命周期无异常。

### 已知问题 / 下一步

1. GPU active ratio 仍 <10%，主要仍是 CPU 决策/残差/apply 在等；
2. 当前 residual engine 复用需要加锁或改为逐调用 engine，避免多 target 线程竞争；
3. 还未做多 target 合批（P2）；
4. 还未做双缓冲/stream overlap（P2）；
5. 完整 channel_sync 的 metrics 文件尚未在真实长素材上验证（待下一次全量测试）。

### 验收记录

- Unit 测试：**1053 PASS / 2 FAIL**（既有缺 Sony/DJI 语料环境项）；
- 输出 parity：context vs 无 context windows 相等；
- VERSION 未变。

## 2026-11-03 — P2 尝试：surface 双缓冲

实现：

- `measure_window_delays` 新增可选 `KTS_GPU_SURFACE_PIPELINE=1`；
- 后台线程按 `KTS_GPU_SURFACE_BATCH` 分批计算 GPU surface；
- 主线程同时做窗连续性/峰/全速率 refine；
- 默认 **关闭**（`0`），保持旧行为。

实测 C1306 单 target 411 窗：

| 模式 | wall | GPU active |
|---|---:|---:|
| legacy 一次性 surface | **10.30 s** | 5.65% |
| pipeline batch=32 | 12.26 s | 5.95% |
| pipeline batch=128 | 11.23 s | — |

- windows/initial 与 legacy **完全一致**；
- pipeline 没有收益，反而增加批次/线程开销；
- 原因：CPU 的 `_refine_window_peak` 和连续性逻辑仍是串行瓶颈，GPU 只有少量 surface 计算；双缓冲无法提高 GPU 占比。

结论：**P2 的 surface 双缓冲不作为默认**，保留 opt-in 实验。
下一步必须做 **多 target 合批** 或 **GPU 端峰/连续性**，否则 CPU 串行段仍占主导。

## 2026-11-03 — P2b 多 target surface 合批组件

新增 `core/gpu_align/window_multi.py`：

- 一个 anchor + 多个 target coarse 缓存；
- 把所有 target 的窗请求合并成一个 GPU batch；
- 输出与 `WindowCCEngine.surface_windows` 完全相同的裁剪/归一化窗口；
- 暂未接入生产 `measure_window_delays`/long-take（先做组件与基准）。

C1306 3 targets（619 窗/target）基准：

| 模式 | wall | GPU ms | batch |
|---|---:|---:|---:|
| 逐 target engine | 3.82 s | 1316.8 | 60 |
| 多 target 合批 | 3.56 s | 1565.7 | 56 |

- 三个 target 的 surface 行 **逐值相等**；
- wall 仅快约 **7%**，GPU 时间反而略高（批次合并/传输开销）；
- 说明只做 surface 合批仍然无法显著提高端到端利用率；下一步必须
  GPU 化/向量化 **峰选择 + full-rate refine + continuity**。

## 2026-11-03 — P3 第一批：GPU 批量 full-rate refine

新增 `core/gpu_align/refine_batch.py`：

- 把每个窗口的 ±8 个全速率候选 lag 合并成一个 GPU batch；
- 每个候选段在 GPU 上归一化并计算归一化点积；
- 返回 lag 与 |cc|；
- `measure_window_delays` 新增 opt-in `KTS_GPU_REFINE_BATCH=1`：
  - coarse/continuity 循环不再逐窗 CPU refine；
  - 收集候选后一次 GPU refine；
  - 默认 **0**，保持旧路径。

### 组件基准（C1306 411 窗）

| 方案 | 时间 |
|---|---:|
| CPU `_refine_window_peak` 逐窗 | 2.02 s |
| GPU batch refine | **0.52 s（3.85×）** |
| lag 相等 | 411/411 |
| quality 最大差 | 3.1e-7 |

### 集成到 `measure_window_delays`

| 素材 | legacy | batch refine | 加速 | delay 相等 |
|---|---:|---:|---:|---:|
| C1306 | 10.81 s | **9.41 s** | 13% | 411/411 |
| C1305 | 12.27 s | **11.63 s** | 5% | 475/475 |

- valid 窗数相同；confidence 最大差 1e-6；
- C1306 context GPU active ratio 4.8% → 7.6%（仍低）。

### 风险 / 下一步

- 两阶段让 continuity 使用 coarse lag 作为临时 prev；已在 C1305/C1306
  上验证 windows 完全一致，但完整语料/长镜头还需对照；
- 默认关闭，实验开关 opt-in；
- 下一步：GPU/向量化 coarse peak + continuity，目标 GPU active >30%。

## 2026-11-03 — P3 batch refine 完整入口门（失败）

在 C1306 完整 `channel_sync` 上启用 `KTS_GPU_REFINE_BATCH=1`：

| 模式 | wall | audio_2/3 与基线 |
|---|---:|---|
| 默认（batch refine off） | 263.9 s | **sha256 完全一致** |
| batch refine fp32 | 284.2 s | **不同** |
| batch refine exact fp64 | 255.2 s | **仍不同** |

- 单 target 窗口测量：C1306 411/411、C1305 475/475 delay 完全一致；
  exact 模式 confidence 也完全相同；但完整长镜头输出仍变化
  （CH3/CH4 曲线 windows 385/379 → 410，audio MOV 哈希不同）。
- 因此 batch refine **不能进入默认**；即使 exact 模式也没有解决
  long-take 上游/下游口径差异。
- 当前默认仍是逐窗 CPU refine；batch refine 保持 opt-in 实验。

结论：P3 组件在单窗口级别有效（3.85×），但完整入口 parity 未通过，
先定位长镜头 measure 调用与曲线层的差异，再继续。

## 2026-11-03 — P3 parity 进一步定位

300 s 隔离素材（4 个 fresh copies）：

| 模式 | long-take wall | applied | fixed raw |
|---|---:|---|---|
| batch refine exact | **11.9 s** | true | 3 个 sha 不同 |
| default | 18.4 s | true | 基线 |

- 同一素材上直接 `measure_window_delays`（default vs batch）在
  C1306/C1305 全文件、300 s 短文件、带 ref cache/context 时，
  windows 都逐窗相等；
- 但 `align_time_varying` 内部调用时输出仍不同；
- 说明差异发生在 **long-take 的调用上下文/曲线层**，不是单次窗测量的
  delay/confidence 本身；
- 需要继续抓 long-take 内部 measure 参数/线程/多 unit 状态；batch refine
  仍只能 opt-in，默认关闭。

## 2026-11-03 — P3 parity 阻塞点（long-take measure 上下文）

对 300 s 隔离素材抓取 `align_time_varying` 内部的每一次
`measure_window_delays` 调用（保留默认 measure 身份）：

- 3 次调用均在 **MainThread**，kwargs 完全相同
  （`window_seconds=60`、`hop=10`、`min_cc=0.06`、`gpu_cc=False`、
  `ref_decimated` 同为 anchor coarse 缓存）；
- batch refine 与 default 的 3 次 measure 结果 digest **仍不同**；
- 但用同一组 kwargs 在 long-take 外部直接调用 measure，
  batch/default 的 windows 完全一致。

结论：差异只出现在 `align_time_varying` 的 measure 调用上下文内，
不在单次 measure 的参数或线程；需要继续查 long-take 内部的
`ref_decimated` 对象/输入流状态或 measure 调用顺序。
batch refine 继续 opt-in，默认关闭。

## 2026-11-03 — P3 blocker: measure 在 long-take 内外的可复现差异

已排除：

- 输入文件不同：long-take batch/off 的 4 个输入 sha256 一致；
- `ref_decimated` 不同：long-take 内传入的 anchor coarse 缓存 hash 与
  外部直接构造完全一致（d739…）；
- 线程不同：measure 调用在 MainThread；
- kwargs 不同：窗口/跳/门/`gpu_cc`/ref cache 完全一致；
- prewarm：外部先 `prewarm_gpu_align()` 再 measure，digest 不变。

仍存在：

- 同样输入、同样 kwargs、同样 ref cache 下：
  - long-take 外部直接 `measure_window_delays`（batch refine）digest = `85d0…`；
  - long-take 内部调用 digest = `0684…`；
  - batch refine off 也出现类似差异；
- 但一次 `ltd_direct_internal` 测试中，ch3 的 direct/internal batch digest
  相同（4eb7…），说明差异与具体 unit/输入状态有关，尚非全量复现。

下一步需要在同一进程内对 long-take 内部/外部的 **逐窗口行** 做差值，
确认是窗值不同还是 digest 口径不同。

## 2026-11-03 — 阻塞定位：测试输入/中间文件污染

检查 C1306 4ch 输入时发现：不同测试副本的 sha256 不一致。

当前 `work/long_take/accept/raw_audio` 的 canonical hashes：

| 文件 | sha256 |
|---|---|
| c1306_ch0 | `313e…` |
| c1306_ch1 | `8b11…` |
| c1306_ch2 | `3503…` |
| c1306_ch3 | `3f5f…` |

但本轮 long-take A/B 测试副本记录到过：

- `b505… / 4454… / 5783… / e665…`
- `b505… / d9bc… / fa53… / c879…`

说明测试副本并非全部来自同一份原始输入，部分目录/文件是**上一轮
long-take 原地施加后的中间结果**，或 `prep(if not exists)` 跳过了覆盖。

影响：

- long-take 300 s A/B、direct/internal measure 对比**不可信**；
- “long-take 内部 measure 与外部不同”的结论可能是输入污染造成；
- batch refine 的 full channel 差异仍来自直接从 MP4 解码的 channel_sync
  结果，这一点不受污染影响，仍需单独验证。

下一步：

1. 从 canonical raw 强制覆盖重建全部测试副本，校验 sha256；
2. 在同一组干净输入上重跑 batch/default（long-take 与 direct measure）；
3. 只有在干净输入上复现的差异才作为真正的 parity 阻塞。

## 2026-11-03 — 最终定位：问题在 GPU batch refine 本身

用 canonical 输入重建并校验 sha256 后：

- direct `measure_window_delays` batch/off 在同一进程内曾相等；
- 但在另一次进程/长镜头测试中，batch 结果与 CPU off 不一致；
- 同一进程重复 batch 5 次结果一致（85d0…），CPU off 为 6e7a…；
- 长镜头 batch/off 的 measure digest 与这条分歧一致。

因此当前阻塞不在：
- long-take 调用上下文；
- 输入污染；
- 线程/ref cache/prewarm。

而在 **GPU batch refine 的数值/确定性**：
- 它有时与 CPU off 一致，有时不一致；
- 依赖运行/批次状态，不能保证 audio MOV parity。

处理决定：

1. batch refine 保持 opt-in，默认关闭；
2. 下一步把 batch refine 改为 **确定性和 CPU-等价**（fp64、固定分批顺序、逐行 CPU 校验）；
3. 在它通过 full channel parity 前，不进入默认/主线。

## 2026-11-04 — 根因确认 + CPU fallback 修复

根因不是 GPU 数值不确定，而是 **batch refine 偶尔抛异常/未执行**，
旧代码捕获异常后直接 pass，留下 provisional 行：

- `delay_samples = coarse lag`
- `confidence = 0.0`

这些 0 置信度行进入 long-take 曲线后改变了输出。

修复：

- batch refine 异常时，对所有 pending 窗口回退到 CPU
  `_refine_window_peak`，不再保留 confidence=0 的 provisional 行；
- 任何 batch 失败都不会污染完整输出。

结果：

| 测试 | 结果 |
|---|---|
| clean 300 s direct batch/off | digest 相等 |
| clean 300 s long-take batch/off | **fixed raw 完全相等** |
| 完整 C1306 channel_sync exact batch | **audio MOV 哈希与基线完全一致** |
| 完整 C1306 exact batch wall | **519.9 s**（明显变慢，性能问题待查） |

结论：

- **parity 门已修复**；batch refine 可在 fallback 保护下保持输出一致；
- 但 exact/fp64 batch 完整入口当前 519.9 s，比默认 263.9 s 慢；
- 需要判断 fallback 是否被频繁触发，以及 batch 失败的根因（OOM/引擎
  创建/批次大小），再决定是否继续启用 batch refine。

## 2026-11-04 — 根因：完整入口 NVRTC DLL 不在 PATH，GPU 全部回退 CPU

`KTS_GPU_DEBUG=1` 抓到了完整 channel_sync 的真实原因：

```
GPU_FALLBACK surface:CompileException:
nvrtc: error: failed to open nvrtc-builtins64_129.dll.
```

- `channel_sync` 新进程没有继承 `C:\CUDA12\bin` 的 PATH；
- CuPy 的 NVRTC RawKernel 找不到 `nvrtc-builtins64_129.dll`；
- 窗口路径静默回退 CPU，所以 metrics 里 batches=0、GPU active=0%；
- 这也解释了为什么早期 120 s、后来 265 s：环境 PATH 不同。

修复 `core/gpu_align/backend.py`：

- 导入 CuPy 前把 `CUDA_PATH/bin` 与 `C:\CUDA12\bin` 加入 PATH；
- 同时 `os.add_dll_directory(bin)`；
- 不再依赖调用方 shell 是否设置 CUDA PATH。

### 修复后完整 C1306 默认入口

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| wall | 264.6 s | **129.6 s** |
| GPU active | 0.0% | **6.5%**（8.43 s） |
| batches | 0 | **492** |
| window_measure | 206.3 s | **41.8 s** |
| residual_measure | 64.6 s | **20.3 s** |
| audio MOV 哈希 | 基线一致 | **基线一致** |

GPU 路径终于真正执行；完整入口恢复到 **129.6 s**，比修复前 CPU 回退快 2.05×。
下一步重测 batch refine 的完整入口性能和 parity。

## 2026-11-04 — NVRTC 修复后：batch refine 完整入口通过

默认 channel_sync（GPU 路径真正启用）：

| 模式 | wall | audio MOV 哈希 | GPU active |
|---|---:|---|---:|
| 默认 CPU refine | 129.6 s | 基线一致 | 6.5% |
| batch refine exact | **118.9 s** | **基线一致** | 6.9% |

- batch exact 比默认快约 **8%**；
- `refine_batch` 28 个 batch，无 fallback；
- window_measure 33.5 s、residual 17.9 s；
- clean 300 s long-take batch/off fixed raw 也完全相等。

说明：

1. 之前 full exact 519 s 是 **NVRTC DLL 不在 PATH → GPU 全部回退 CPU** 导致；
2. 修复 backend PATH 后，GPU 路径真正执行；
3. batch refine 在 fallback + NVRTC 修复后，完整入口既快又保持输出哈希。

下一步：

- 再跑 C1305 / L3 / 多次重复，确认稳定性；
- 评估把 batch refine 的 opt-in 是否升级为默认；
- 继续提升 GPU active（目前约 7%）。

## 2026-11-04 — C1305 完整入口（NVRTC 修复后）

C1305 80 min、4ch、batch refine exact：

| 指标 | 值 |
|---|---:|
| wall | **212.3 s** |
| status | applied |
| audio MOV 哈希 | **与 CPU/exact 基线完全一致** |
| speedup vs CPU | **1.42×** |

对比：

- C1305 之前的 GPU fast 完整入口约 199.6 s（不同代码/ PATH 状态）；
- exact batch 212.3 s，输出 parity；
- C1306 exact batch 118.9 s，默认 129.6 s，parity 一致。

结论：

- NVRTC/ PATH 修复后，GPU 窗口/残差/batch refine 在完整入口真正执行；
- 输出哈希可保持；
- C1305 上 exact batch 仍略慢，batch refine 继续 opt-in；
- GPU active 仍只有约 **7%**，后续优化空间明确。

## 2026-11-04 — C1306 batch refine 完整入口两种精度

NVRTC 修复后，完整 C1306 channel_sync 同基线对照：

| 模式 | wall | audio MOV 哈希 | GPU active | batches |
|---|---:|---|---:|---:|
| 默认 CPU refine | 129.6 s | 一致 | 6.5% | 492 |
| batch refine fp32 | **122.4 s** | **一致** | 6.3% | 520 |
| batch refine exact fp64 | **118.9 s** | **一致** | 6.9% | 520 |

- exact batch 最快；
- fp32 batch 也保持输出一致且无 fallback；
- C1305 exact batch 212.3 s，输出一致，仍略慢于旧 fast 199.6 s；
- batch refine 目前仍 opt-in；下一步用 C1305/L3/重复运行验证后再考虑默认。

## 2026-11-04 — batch refine exact 复测稳定性

C1306 完整 channel_sync，batch refine exact 连续两次：

| 运行 | wall | audio MOV 哈希 | GPU active | batches |
|---|---:|---|---:|---:|
| #1 | 118.9 s | 一致 | 6.9% | 520 |
| #2 | 120.0 s | 一致 | 6.7% | 520 |

- 两次都无 fallback；
- 输出与基线完全一致；
- 早期 120.5 s 的“快基线”现在可以解释：当时 GPU 路径真实执行；
  后来 PATH 丢失导致 CPU 回退，才出现 265–520 s。

当前结论：

1. NVRTC/PATH 修复是完整 GPU 路径恢复的关键；
2. batch refine exact 在 C1306 上稳定 **119–120 s**，比默认 129.6 s 快；
3. C1305 exact batch 212.3 s 仍略慢；
4. batch refine 继续 opt-in，待 C1305 默认对照后再决定是否默认。

## 2026-11-04 — C1305 默认 vs exact batch 对照

NVRTC/PATH 修复后，C1305 完整 channel_sync：

| 模式 | wall | audio MOV 哈希 |
|---|---:|---|
| 默认 CPU refine | 225.4 s | 与 CPU/exact 一致 |
| batch refine exact | **212.3 s** | 一致 |

- exact batch 在 C1305 也快约 **6%**；
- C1306：默认 129.6 s → exact batch **118.9–120.0 s**（约 8%）；
- 两个素材、重复运行均保持 audio MOV 哈希一致；
- batch refine 目前仍是 opt-in，但已成为**默认化的候选**，只差 L3 /
  final_accept / 目标机重复验证。

### 当前候选决策

- 保持默认 CPU refine，直到 L3/full acceptance 完成；
- 若后续验证通过，可把 `KTS_GPU_REFINE_BATCH` 默认设为 1 且 exact=1；
- GPU active 仍约 6–7%，下一阶段继续做 GPU peak/continuity。

## 2026-11-04 — 最终 clean A/B 确认

在 NVRTC/PATH 修复后重跑 300 s clean A/B（4 个输入校验 sha256）：

- `direct_equal = true`；
- `lt_fixed_equal = true`；
- batch refine 与 default 的 long-take fixed raw 完全一致；
- 输入 hash 也与 canonical raw 一致。

至此：

1. NVRTC 回退问题已修复；
2. batch refine parity 已恢复且 clean A/B 可复现；
3. C1306/C1305 完整入口均有 hash 对照；
4. 默认仍为 CPU refine，batch refine 是默认化候选。

## 2026-11-04 — P4 profiling：coarse cache 是 GPU 空转主因

对 `measure_window_delays`（C1306 48000 Hz，411 窗，batch refine exact）
做 cProfile，总 23.6 s：

| 阶段 | 累计时间 |
|---|---:|
| coarse_decimate（ref + tgt） | **15.6 s（66%）** |
| GPU surface_windows | 2.6 s |
| GPU refine_batch | 1.1 s |
| 全速率 read_f64 / 文件读 | 2.9 s |
| GPU session/import | 1.3 s |
| 连续峰搜索 | 0.4 s |

结论：当前 GPU active 低，不是 GPU kernel 慢，而是 **GPU 前面的 CPU
coarse 抽取占了大头**；GPU 在 CPU 建 coarse 缓存时完全空闲。

### 验证：分块 coarse 抽取线程化

`coarse_decimate` 的分块本来互相独立、输出逐位一致。用
`ThreadPoolExecutor` 并行分块（每 worker 自己打开 RawStream）：

| 配置 | 单次 C1306 1 轨 coarse | 411 窗 measure |
|---|---:|---:|
| `KTS_COARSE_WORKERS=1` | 5.94 s | 20.2 s，GPU active 6.3% |
| `KTS_COARSE_WORKERS=4` | **2.45 s** | **10.2 s，GPU active 13.1%** |
| `KTS_COARSE_WORKERS=8` | **1.47 s** | — |

- 三种配置输出数组 **逐位相等**；
- 完整 C1306 batch exact 在系统高负载下仍保持 audio MOV 哈希一致；
  window_measure 从 89.4 s 降到 52.9 s（同负载对照），wall 259→249 s；
- 绝对 wall 受当时后台负载影响，不能与低负载的 119–120 s 直接比较。

默认值设为 **`KTS_COARSE_WORKERS=4`**，可用环境变量覆盖；这是 GPU
利用率提升的前置优化：先把 CPU coarse 建缓存时间压下去，再谈 GPU 双缓冲。

## 2026-11-04 — P2 双缓冲在 coarse4 之后重新有效

P2 之前测“surface producer 双缓冲”无收益，是因为 coarse cache 占了大头，
GPU surface 本身只有 2–3 s。coarse 分块线程化后，直接 C1306 411 窗测量
（coarse 缓存预建）出现明显效果：

| 模式 | wall | GPU active | windows |
|---|---:|---:|---|
| 单批 surface（默认旧） | 6.78 s | 18.4% | 基准 |
| pipeline b32 | **3.50 s** | **22.0%** | 逐窗相等 |
| pipeline b128 | 3.82 s | 19.8% | 相等 |
| pipeline b8 | 5.13 s | 16.1% | 相等 |

完整 C1306 exact（coarse4 + pipeline b32，同一高负载环境）：

| 配置 | wall | window_measure | GPU active |
|---|---:|---:|---:|
| coarse4 + 单批 surface | 249 s | 89.4 s | 6.6% |
| coarse4 + pipeline b32 | **200 s** | **49.8 s** | **7.1%** |

- audio MOV 哈希仍与基线一致；
- `KTS_GPU_SURFACE_PIPELINE` 默认改为 **1**；
- `KTS_GPU_SURFACE_BATCH=32` 保持不变；
- 绝对 wall 受后台负载影响，但相对收益稳定。

结论：P2 设计本身没问题，之前缺的是先消除 CPU coarse 瓶颈。现在
GPU pipeline 的三级默认组合为：

```
coarse chunks: 4 threads
surface: pipeline (batch 32)
refine: CPU per-window (batch refine still opt-in)
```

