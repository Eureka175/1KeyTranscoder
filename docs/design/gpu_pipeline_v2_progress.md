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

