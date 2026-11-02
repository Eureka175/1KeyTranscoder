# GPU Pipeline v2 架构（新架构开发分支）

> **分支**：`feature/gpu-pipeline-v2`
> **基线**：`feature/gpu-align-arch`（已合并 `feature/gpu-frame-estimation`）
> **基线提交**：`653de2a`
> **版本**：VERSION 保持 **0.9.0**；不合并 main、不发布 v1.0.0
> **目标**：从“GPU 协处理器”改为“GPU 常驻流水线”，先提高利用率，再谈速度。

## 1. 为什么需要新架构

现有分支已经实现：

- `core/gpu_align` 包：backend/precision/window/frame/residual/session/parity；
- 窗级 `surface_windows()`：单 target 内全部窗口 surface 预计算；
- residual coarse seed GPU；
- 默认 fp32 + CUDA，CLI/env 可切 fp64/off；
- 帧级实验 batch/hybrid（opt-in，完整入口不达标）。

但完整入口仍然：

- CPU-only 约 207 s；GPU fast 约 265–272 s，**GPU 路径反而更慢**；
- 单次窗口测量 GPU 活跃只占小部分；long-take 内 measure 约 49 s；
- 每个 unit 各自创建 session/engine、重复上传 coarse 缓存；
- GPU 调用后同步下载，CPU 决策期间 GPU 空闲；
- peak/continuity/residual/apply 仍在 CPU；
- 帧级还叠加 CPU 进程池，与 GPU 争资源。

**现有分支是“组件级 GPU 化”，不是完整执行后端。**

## 2. 设计目标

| 指标 | 当前 | 目标 |
|---|---|---|
| GPU active time / wall | 个位数到 <15% | **>50%** |
| 完整 C1306 入门 | GPU 比 CPU 慢 28% | **≤ CPU，最好 1.3×+** |
| session 创建 | 每 measure 一次 | **每 run 一次** |
| coarse 缓存上传 | 每 target 一次 | **每 channel 一次** |
| target 合批 | 无 | **多 target 同批** |
| CPU/GPU overlap | 无 | **双缓冲流水线** |
| 输出 | — | **audio MOV sha256 与 CPU 逐字节一致** |

非目标（本阶段不做）：

- 不先做 kernel 微优化、bank/FFT plan 调优；
- 不改默认音频决策语义；
- 不推 v1.0 / 不合并 main；
- 不把帧级 experimental 直接设为默认。

## 3. 目标架构

```
RunContext (per channel_sync)
  ├── GpuSession (persistent, 1 per run)
  ├── CoarseCacheRegistry (per channel, f32/f64, refcount)
  ├── BatchScheduler
  │     ├── enqueue(window/frame/residual jobs)
  │     ├── multi-target batch assembly
  │     └── budget/backpressure
  ├── StreamPipeline
  │     ├── stream A: batch N upload/compute
  │     ├── stream B: batch N+1 prefetch/compute
  │     └── event handoff
  ├── Decision CPU worker
  │     ├── peak/confidence/continuity
  │     └── curve/quality gates
  └── ResultStore (host arrays, output compat)
```

核心原则：

1. **一个 run 一个 GPU session**，所有 target/阶段复用；
2. **coarse 缓存只上传一次**，按 channel 缓存；
3. **多 target 合并成 batch**，不让 GPU 空转；
4. **GPU 算下一批时 CPU 处理上一批**；
5. **决策仍由 CPU/状态机负责**，但和 GPU 计算重叠；
6. 所有新路径必须有 CPU fallback 和输出 parity 门。

## 4. 模块规划

| 模块 | 职责 |
|---|---|
| `core/gpu_align/runtime.py` | RunContext、session 生命周期、缓存 registry |
| `core/gpu_align/scheduler.py` | 多 target 合批、预算、背压 |
| `core/gpu_align/stream_pipeline.py` | 双 stream/事件、prefetch、overlap |
| `core/gpu_align/window_batch.py` | 窗级 surface + 峰候选批量输出 |
| `core/gpu_align/peaks.py` | CPU 向量化/GPU 峰、置信度、极性 |
| `core/gpu_align/residual_batch.py` | residual 全流程批量 |
| `core/gpu_align/frame_batch.py` | 帧级 batch 正式集成（基于现有 experiment 重构） |
| `core/gpu_align/observe.py` | GPU active time、kernel gap、transfer bytes、fallback 统计 |

现有 `window_cc/frame_gcc/residual_cc/session/parity` 保留为底层 kernel；
`measure_window_delays` / `long_take` 逐步改为调用 RunContext。

## 5. 分阶段计划

### P0：度量与基线（1 天）

- 加 CUDA events / BatchStats 汇总：GPU active、kernel gap、host transfer；
- 完整 C1306 一次时间线：stereo、frame、window、residual、apply；
- 记录 CPU/GPU 频率、温度、进程数；
- 验收：能输出“每阶段 wall / GPU active / gap / bytes”。

### P1：持久 session + 缓存复用（2–3 天）

- `RunContext` 贯穿 `channel_sync`；
- `measure_window_delays` 接受外部 session/coarse cache；
- 每个 channel 只 decimate/upload 一次；
- 保持输出逐字节一致；
- 验收：session 创建次数、上传字节显著下降，输出同 CPU。

### P2：多 target 合批 + 双缓冲（3–5 天）

- 把 anchor→所有 target 的窗请求合并；
- stream A/B 重叠 GPU 计算与 CPU 决策；
- batch budget/backpressure；
- 验收：GPU active >40%，完整入口不超过 CPU；输出一致。

### P3：GPU/向量化决策与 residual（3–5 天）

- peak/confidence/continuity 批量向量化；
- residual 全程 batch；
- 验收：GPU active >50%；完整入口 < CPU；parity 门。

### P4：帧级正式集成与默认评估

- 帧级 surface 共用 RunContext；
- 只对长轨迹启用；
- 完整 L3/final_accept、C1305/C1306 哈希；
- 再决定是否默认。

## 6. 风险与门

| 风险 | 处理 |
|---|---|
| 输出不一致 | 每阶段 CPU 参考对照；不达标立即回退 |
| GPU 显存/预算 | batch 自适应；512/1024/2048 MB 档位 |
| 线程/进程与 CUDA 上下文 | 单 GPU 主进程；CPU 决策 worker 不持有 CUDA |
| Windows/NVRTC 路径 | 保留 ASCII temp、回退 CPU |
| 早期 120.5 s 假基线 | 只用 3 次中位数 + 阶段计时 |

## 7. 基线说明

早期 120.5 s 已被证明**不可复现**：重启后旧提交 `ad8b2c1`、当前分支、
CPU-only 对照分别约 265.8 s / 265–272 s / 207 s，输出哈希一致。
新架构不再以 120.5 s 为目标，而以“CPU-only 实测基线 + GPU 利用率”为准。
