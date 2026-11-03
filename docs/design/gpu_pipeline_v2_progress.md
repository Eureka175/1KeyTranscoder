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
