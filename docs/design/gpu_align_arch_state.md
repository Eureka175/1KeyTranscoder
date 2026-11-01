# GPU 音频对齐架构 — 状态与移交（2026-11-01）

> **分支**：`feature/gpu-align-arch`（从 `feature/v1.0-async` 切出）
> **版本**：VERSION 保持 **0.9.0**；程序默认开启 **fp32 + CUDA 音频对齐**（CUDA 不可用时自动回退 CPU）。`--gpu-align off` 可关闭。
> **目标**：CUDA FP32/FP64 性能验证 + 整体音频对齐 GPU 优化架构重写。
> **报告**：[gpu_align_ws14_report.md](gpu_align_ws14_report.md)、[gpu_frame_batch_report.md](gpu_frame_batch_report.md)；
> **设计**：[gpu_align_architecture.md](gpu_align_architecture.md)。
> **帧级批处理**：实验 opt-in，默认关闭；单对 hybrid 约 1.2×，完整入口未通过性能门。

## 1. 当前提交

| 提交 | 内容 |
|---|---|
| `b450de6` | 架构设计 + FP32/FP64 初始微基准 |
| `70a9301` | `core/gpu_align` 包 + 窗级路径 opt-in |
| `fe4bce8` | long-take 线程/预热策略、C1305/C1306 端到端 |
| `6aa2db5` | tracked 基准、文档索引、C1305 |
| `8c59d7f` | fallback/availability 单元测试 |
| `c731a50` | fp64 并行 / fp32 串行策略 |
| `9c9b19a` | GPU residual 接入 long-take |
| `7efc88a` | C1305 residual 后 62.7 s（2.10×） |
| `5f03f85` | stereo 检测前 prewarm |
| `49fb0e1` | 完整 channel_sync C1306 208.8 → 123.1 s（1.70×） |
| `d66ee7d` | 完整 C1306 fp32 120.5 s；C1305 fp64 204.7 s |
| `ad8b2c1` | 完整 C1305 fp32 199.6 s（1.51×） |
| `bd8d116` | v1.0 计划补 GPU 分支记录 |
| `d8355db` | no-CUDA fallback / final_accept_v2 PASS / 默认 smoke |
| `bd6a1a8` | 8 MB 预算与确定性压力测试 |
| `75b937d` | C1305 residual 4808 点 parity |
| `c0193e3` | `--gpu-align {off,fp32,fp64}` CLI |

## 2. 实现范围

`core/gpu_align/`：

- `backend.py`：CuPy/CUDA 探测、ASCII 路径、显存预算、OOM/回退；
- `precision.py`：`fp32`（FP32，推荐性能）/`fp64`（FP64）/`hybrid` 预留；
- `window_cc.py`：批量窗级粗相关；`surface_windows()` 一次算全窗口表面，
  CPU 保持连续性/阈值决策；
- `frame_gcc.py`：批量 GCC-PHAT surface（CPU 峰逻辑保留）；
- `residual_cc.py`：残差 coarse seed；
- `refine.py`：GPU 单窗精化（实测比 CPU 慢，保留对照）；
- `session.py` / `parity.py` / `types.py`；
- 集成：`sync_estimate.measure_window_delays`（窗级）、
  `long_take.measure_residual_curve`（残差）、
  `long_take.align_time_varying`（GPU unit 拓扑）、
  `channel_sync`（stereo 检测 prewarm）。

CLI / 环境开关：

```
--gpu-align {off,fp32,fp64}   # 默认 fp32；off 强制 CPU
```


```
KTS_GPU_ALIGN=fp32|fp64|0      # 默认 fp32；0=off
KTS_GPU_ALIGN_BUDGET_MB=2048     # 显存预算上限
```

## 3. 实测结果（摘要）

> 单元测试：**1041 PASS / 2 FAIL**（既有缺 Sony/DJI 语料环境项）。

| 测试 | CPU | fp64 | fp32 | 输出一致性 |
|---|---:|---:|---:|---|
| 窗级 20 窗（tracked bench） | 1.706 s | 0.209 s（8.1×） | 0.092 s（18.6×） | fp64 maxΔcc 8.9e-16；fp32 4.3e-7 |
| 帧 surface 64 帧 | 0.023 s | 0.0118 s（1.95×） | 0.0116 s（1.99×） | argmax 64/64 |
| residual（C1306 ch0/1） | 17.0 s | 7.1 s（2.4×） | 7.3 s（2.3×） | 4165/4165 点相同（fp64 且 fp32） |
| long-take C1306（3 targets） | 115.1 s | **47.1 s（2.45×）** | **57.4 s（2.01×）** | 最终 raw sha256 全同 |
| long-take C1305（3 targets） | 131.8 s | **62.7 s（2.10×）** | — | 最终 raw sha256 全同 |
| 完整 channel_sync C1306 | 208.8 s | **123.1 s（1.70×）** | **120.5 s（1.73×）** | 4 条 audio MOV sha256 全同 |
| 完整 channel_sync C1305 | 300.9 s | **204.7 s（1.47×）** | **199.6 s（1.51×）** | 4 条 audio MOV sha256 全同 |

- FP32 的窗级/残差结果在本次真实素材上与 FP64/CPU 逐点或 ≤1e-6 一致；
  用户已明确不要求 FP64 同精度。
- **端到端 fp64 快于 fp32**：fp32 的窗口收益被 CPU refine/下游 apply 吃掉，
  且 fp64 内核/FFT plan 已预热。程序默认采用 fp32；fp64 保留为可选对照。
- 帧级整路径、GPU 单窗精化目前无收益；**不接入**，保留 CPU。
- 完整 channel_sync 仍是 CPU 帧估计/立体声检测为瓶颈；GPU 只加速窗级与
  residual。fp32 在完整 C1306 入口略快于 fp64（120.5 vs 123.1），但
  long-take 单独测试 fp64 更快；两者最终 audio MOV 均与 CPU 相同。

## 4. 已知限制/风险

1. 程序默认 fp32；CUDA 不可用/显存不足时自动回退 CPU。
2. CuPy 依赖本机 `C:\sp_ascii`/`C:\CUDA12` 工作环境；`backend` 内置探测和
   回退，但目标机若 CUDA 版本不同需验证。
3. `surface_windows` 对不等长窗口只返回同组；生产长镜头窗口等长，边界
   按 `n_windows` 截断。
4. fp32 与 fp64 的窗口记录在报告尾部可能差 1e-6（用户接受）；最终
   audio MOV 在两个素材上均相同。
5. 尚未把 GPU 帧估计/相位斜率/apply 写入；也尚未跑完整 L3、final_accept_v2
   的新架构全量复验。

## 5. 下一步建议

1. 在目标机（普通 Windows 用户目录/不同 CUDA）跑一次 `KTS_GPU_ALIGN=fp64`
   短素材冒烟，确认 CuPy 导入/回退；
2. 对完整 C1305/C1306 做一次 final_accept_v2 + 透明 50 GiB 定向复验（默认
   路径未动，可选抽样）；
3. 若要让 fp32 真正优于 fp64，先 GPU 化 `_refine_window_peak`/下游 apply，
   否则保持 fp64 推荐；
4. 决定是否把 `core/gpu_align` 合并回 v1.0 集成分支；合并前补 GPU 帧级
   no-go 报告和低配机 fallback 测试；
5. VERSION 仍为 0.9.0，不发布 v1.0.0。
## 6. 回退/验收补充

- `CUDA_VISIBLE_DEVICES=-1` + `KTS_GPU_ALIGN=fp64`：`used=false`，411 窗
  测量完成，纯 CPU 44.1 s，无异常；
- `final_accept_v2` 重跑 **OVERALL: PASS**；
- 默认 `KTS_GPU_ALIGN=0` 的 Sony 单文件短测：`done=1`、
  `PRESERVED=37`；GPU 关闭时默认行为不变；
- 预算 8 MB 下 fp32 411 窗完成且两次记录相同；VERSION 仍 0.9.0。
## 7. 显存占用（RTX 5070 Laptop 8 GiB）

| 预算上限 | 峰值占用 | 批 | 说明 |
|---:|---:|---:|---|
| 512 MiB | 420 MiB | 5 窗 | 保守，wall 32 s |
| 1024 MiB | 780 MiB | 11 窗 | 推荐低显存折中，wall 20 s |
| 2048 MiB | 1470 MiB | 23 窗 | 当前默认上限，wall 25 s |

residual 增量约 64 MiB；默认预算 `min(2048, free×0.6)`；OOM/无 CUDA
自动回退 CPU。
