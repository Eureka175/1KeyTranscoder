# 音频对齐 GPU 优化架构（重写草案）

> **状态**：设计/原型（2026-11-01，分支 `feature/gpu-align-arch`）
> **目标**：把当前分散的 `core/gpu_cc.py` + 原型 `gpu_frames.py` 重写为统一的
> GPU 对齐引擎，支持 **FP64 精确** 与 **FP32 快速** 两种精度策略、批处理、
> 显存预算、CPU 回退和可验证的 parity 合同。
> **硬约束**：默认关闭；不改变 v0.9.0 默认输出；`VERSION` 保持 `0.9.0`。

## 1. 现状与问题

当前生产/原型中的 GPU 代码是"点状"的：

| 位置 | 覆盖范围 | 精度 | 问题 |
|---|---|---|---|
| `core/gpu_cc.py` | 长镜头窗级 coarse CC；channel-sync 的 residual coarse 种子 | FP64 | 每个窗口 Python 调用、逐窗同步；只覆盖窗级；无批量；无资源策略 |
| `perf/frame-batch-gpu:core/gpu_frames.py` | 帧级 GCC-PHAT surface（原型） | FP64 | 未集成；无 FP32；无统一引擎 |
| `long_take.measure_residual_curve` | 残差窗 | CPU + 可选 GPU 种子 | 与窗口/帧路径各自为政 |
| `sync_estimate._coarse_cc_peak` | CPU 基准 | FP64 | 无 GPU 回退接口 |

共同缺陷：

1. **无统一精度策略**：只有 FP64；消费级 GPU FP64 吞吐低，FP32 收益未标定；
2. **无批处理**：逐窗口/逐帧同步调用，Python 与 host/device 往返占主导；
3. **无显存预算**：全片 coarse 缓存 + 多线程 FFT 缓冲可能叠加爆显存；
4. **无统一 parity 合同**：各路径各自 round/验证，FP32 差异无法归因；
5. **无统一回退**：CuPy 不可用/显存不足/OOM 时各调用点自己处理，行为不一致。

## 2. 目标架构

```
core/gpu_align/
    __init__.py         公开 API 与 available()
    backend.py          CuPy 导入/设备上下文/显存预算/事件计时/OOM 分类
    precision.py        Precision 枚举与策略（exact / fast / hybrid）
    types.py            结果数据类（WindowPeak, FrameSurface, BatchStats）
    window_cc.py        窗级粗相关批处理引擎
    frame_gcc.py        帧级 GCC-PHAT surface 批处理引擎
    residual_cc.py      残差窗粗相关批处理引擎
    session.py          AlignGpuSession：prepare/execute/release 生命周期
    parity.py           与 CPU 参考的 parity 检查、报告 round 合同
```

### 2.1 层职责

| 层 | 职责 | 不做什么 |
|---|---|---|
| `backend` | 设备探测、上下文、显存查询/预算、异步事件、错误分类 | 不实现算法 |
| `precision` | 输入/FFT/输出 dtype 选择；混合精度规则 | 不决定业务阈值 |
| `window_cc` / `frame_gcc` / `residual_cc` | 纯批处理 kernel 封装；输入输出 numpy/GPU 数组；可复用显存池 | 不做连续性/决策 |
| `session` | 缓存上传/分块/批大小/生命周期；为调用方提供高层 API | 不改变调用方决策逻辑 |
| `parity` | 与 CPU 参考对比、报告字段 round 规则、验收门 | 不修改生产结果 |

### 2.2 精度策略

| 模式 | 计算 dtype | 报告 dtype | 用途 | 验收 |
|---|---|---|---|---|
| `fast`（**推荐**，`KTS_GPU_ALIGN=1/fast`） | FP32 粗相关 | FP32 结果 → 既有 round | 性能优先 | 用户决策：**不要求与 FP64 同精度，不差太多即可**；实测 delay/valid/polarity 全同，仅 cc/confidence 差 ≤1e-6 |
| `exact`（`KTS_GPU_ALIGN=exact/fp64`） | FP64 | FP64 | 验收/对照 | 与 CPU 结果逐位或按既有 round 合同一致 |
| `hybrid` | FP32 搜峰 + FP64 复核 | FP64 | 预留 | 本轮未证明优于 fast，暂不推荐 |

**hybrid 规则（草案）**：

- 窗级：FP32 批量求 surface/候选峰 → 对每个窗的候选 lag，在 **FP64** 上
  重算该窗的归一化 CC/峰（或仅对 |CC| 接近 `min_cc`/round 边界的窗重算）；
  报告只采用 FP64 值。
- 帧级：FP32 批量 surface → 取候选 lag；对 confidence 接近
  `min_confidence` 的帧、或前后帧连续性窗口，FP64 复核；其余帧的
  delay 由 FP32 argmax 给出，confidence 由 FP64 复核或标记。
- 残差：同窗级。
- 若 runtime 超过纯 FP64 的 1.2× 或报告不逐位一致，hybrid 不进入默认。

### 2.3 批处理与显存

- `WindowCCEngine`：一次上传 ref/tgt coarse 缓存；`batch_windows` 按
  `max_batch_bytes` 切批，每批一次 batched rfft/irfft。
- `FrameSurfaceEngine`：输入 `(B,F)`/`(B,L)` 批；按 `B` 与 nfft 估算显存，
  自动降批；支持 pinned host buffer 复用（后续）。
- `session` 维护 CuPy memory pool；`release()` 释放；OOM 自动降批一次，
  仍失败则回退 CPU 并标记 `fallback_reason`。
- 显存门限：默认 `min(2 GiB, free*0.6)`；可通过环境变量覆盖；不超过 2 GiB。
- 不把全片 48k 数组上传；只上传 coarse 缓存/批窗口；粗 cache 以 FP32
  存储，计算时按精度策略 cast。

### 2.4 回退与可观测性

- `available()` / `session.available` 检查 CuPy、设备、免费显存。
- 每次批处理记录 `BatchStats`：dtype、batch、nfft、GPU ms、上传/下载 ms、
  OOM、fallback。
- 生产调用点只需 `if session and session.available: ... else CPU`；
  任何不一致都回退 CPU（opt-in 路径）。
- 环境开关（只读，不改 CLI）：
  - `KTS_GPU_ALIGN=0|1|fast|exact`（默认 0；1/fast = FP32 推荐路径）；
  - `KTS_GPU_ALIGN_DTYPE=fp64|fp32`（兼容旧实验）；
  - `KTS_GPU_ALIGN_BUDGET_MB`（默认 2048）。

## 3. 集成点（分阶段）

| 阶段 | 集成 | 风险 | 门 |
|---|---|---|---|
| A | `sync_estimate.measure_window_delays` 的 `_coarse_peak` 改走 `WindowCCEngine`（opt-in） | 中 | 窗记录与 CPU 等价；C1306 长镜头 wall 下降 |
| B | `long_take.measure_residual_curve` 的 coarse seed 改走 `ResidualEngine` | 低 | 残差曲线/最终 MOV 不变 |
| C | 帧级 `_est_frames` 的 GCC-PHAT surface 批处理（opt-in；CPU 峰逻辑先保持） | 高 | 逐帧 delay/confidence 一致；否则停 |
| D | 多通道/多窗批处理与流水线（GPU 队列 + 主线程决策） | 高 | 总 wall/显存/确定性 |

## 4. Parity 合同

### 4.1 必须逐位/严格一致

- 最终 audio MOV sha256（与 v0.9.0 CPU 基准）；
- `final_accept_v2`（C1305/C1306 曲线 + 音频层）；
- 生产报告中的整数 delay、`coarse_cc` / `confidence` 的既有 round 字段
  （`exact`/`hybrid` 必须；`fast` 允许记录差异，但需报告）；
- 轨迹/曲线决策（valid、极性、min_cc、连续性）。

### 4.2 允许容差（fast 模式，仅性能评估）

| 字段 | 容差 |
|---|---|
| 整数 delay | 0（不允许翻转） |
| 窗 coarse_cc | ≤1e-6（报告 round 后允许边界差） |
| confidence | ≤1e-6 |
| 最终输出 | 必须 sha256 一致（否则 fast 仅作为实验） |

### 4.3 测试分层

1. 合成信号（已知延迟、极性、静音、周期信号）逐字段对比；
2. 真实 C1305/C1306 片段窗口/帧，抽样 200–1000 个，记录 max diff /
   mismatch 分类；
3. 最终 MOV sha + final_accept_v2；
4. OOM/无 GPU/显存不足回退测试；
5. 确定性与重复运行。

## 5. 里程碑与报告

| 节点 | 产出 |
|---|---|
| M1 | 本设计 + 初始 FP32/FP64 微基准报告 |
| M2 | `core/gpu_align` 后端/窗级/帧级引擎 + 单元测试 |
| M3 | 真实素材 parity/性能报告（exact/fast/hybrid） |
| M4 | opt-in 集成到窗级路径；短测 + 回退测试 |
| M5 | 架构结论（go/no-go、默认值建议、剩余风险） |

## 6. 非目标

- 不重写 CPU 决策/连续性/报告逻辑；
- 不默认开启 GPU；
- 不改变 VERSION / 不发布 v1.0.0；
- 不在本轮完成多机/多 GPU。

---

## 7. 实施状态（2026-11-01）

- 后端 / 精度 / 窗级 surface / 帧级 surface / 残差 / 精化 / session /
  parity / 单元测试均已落地（`core/gpu_align/`）。
- 窗级路径已 opt-in 集成到 `sync_estimate.measure_window_delays`
  （`KTS_GPU_ALIGN=fast|exact`，默认关闭）。
- 端到端：C1306 CPU 115.1 s → fast 81.7 s / exact 70.0 s；C1305
  CPU 131.8 s → exact 109.7 s；两者最终 raw sha256 与 CPU 一致。
- 帧级整路径与 GPU 单窗精化尚无收益，保留 CPU；残差引擎已实现但未接入
  生产 residual 流程。
- 已知限制：fast 在端到端不总比 exact 快（下游 CPU/refine 占比）；
  `surface_windows` 对不等长窗口只支持同组返回，生产长镜头窗口等长；
  默认不启用、VERSION 保持 0.9.0。
