# GPU 音频对齐架构 — 状态与移交（2026-11-01）

> **分支**：`feature/gpu-align-arch`（从 `feature/v1.0-async` 切出）
> **版本**：VERSION 保持 **0.9.0**；GPU 路径 **opt-in**，默认全关。
> **目标**：CUDA FP32/FP64 性能验证 + 整体音频对齐 GPU 优化架构重写。
> **报告**：[gpu_align_ws14_report.md](gpu_align_ws14_report.md)；
> **设计**：[gpu_align_architecture.md](gpu_align_architecture.md)。

## 1. 当前提交

| 提交 | 内容 |
|---|---|
| `b450de6` | 架构设计 + FP32/FP64 初始微基准 |
| `70a9301` | `core/gpu_align` 包 + 窗级路径 opt-in |
| `fe4bce8` | long-take 线程/预热策略、C1305/C1306 端到端 |
| `6aa2db5` | tracked 基准、文档索引、C1305 |
| `8c59d7f` | fallback/availability 单元测试 |
| `c731a50` | exact 并行 / fast 串行策略 |
| `9c9b19a` | GPU residual 接入 long-take |
| `7efc88a` | C1305 residual 后 62.7 s（2.10×） |
| `5f03f85` | stereo 检测前 prewarm |
| `49fb0e1` | 完整 channel_sync C1306 208.8 → 123.1 s（1.70×） |

## 2. 实现范围

`core/gpu_align/`：

- `backend.py`：CuPy/CUDA 探测、ASCII 路径、显存预算、OOM/回退；
- `precision.py`：`fast`（FP32，推荐性能）/`exact`（FP64）/`hybrid` 预留；
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

`
--gpu-align {off,fast,exact}   # 新增 opt-in；默认不指定 = CPU
`


```
KTS_GPU_ALIGN=0|1|fast|exact     # 默认 0；1 映射 fast
KTS_GPU_ALIGN_BUDGET_MB=2048     # 显存预算上限
```

## 3. 实测结果（摘要）

| 测试 | CPU | exact | fast | 输出一致性 |
|---|---:|---:|---:|---|
| 窗级 20 窗（tracked bench） | 1.706 s | 0.209 s（8.1×） | 0.092 s（18.6×） | exact maxΔcc 8.9e-16；fast 4.3e-7 |
| 帧 surface 64 帧 | 0.023 s | 0.0118 s（1.95×） | 0.0116 s（1.99×） | argmax 64/64 |
| residual（C1306 ch0/1） | 17.0 s | 7.1 s（2.4×） | 7.3 s（2.3×） | 4165/4165 点相同（exact 且 fast） |
| long-take C1306（3 targets） | 115.1 s | **47.1 s（2.45×）** | **57.4 s（2.01×）** | 最终 raw sha256 全同 |
| long-take C1305（3 targets） | 131.8 s | **62.7 s（2.10×）** | — | 最终 raw sha256 全同 |
| 完整 channel_sync C1306 | 208.8 s | **123.1 s（1.70×）** | **120.5 s（1.73×）** | 4 条 audio MOV sha256 全同 |
| 完整 channel_sync C1305 | 300.9 s | **204.7 s（1.47×）** | **199.6 s（1.51×）** | 4 条 audio MOV sha256 全同 |

- FP32 的窗级/残差结果在本次真实素材上与 FP64/CPU 逐点或 ≤1e-6 一致；
  用户已明确不要求 FP64 同精度。
- **端到端 exact 快于 fast**：fast 的窗口收益被 CPU refine/下游 apply 吃掉，
  且 exact 内核/FFT plan 已预热。因此当前推荐 exact opt-in；fast 保留实验。
- 帧级整路径、GPU 单窗精化目前无收益；**不接入**，保留 CPU。
- 完整 channel_sync 仍是 CPU 帧估计/立体声检测为瓶颈；GPU 只加速窗级与
  residual。fast 在完整 C1306 入口略快于 exact（120.5 vs 123.1），但
  long-take 单独测试 exact 更快；两者最终 audio MOV 均与 CPU 相同。

## 4. 已知限制/风险

1. GPU 路径默认关闭；没有改变默认输出与 CLI 契约。
2. CuPy 依赖本机 `C:\sp_ascii`/`C:\CUDA12` 工作环境；`backend` 内置探测和
   回退，但目标机若 CUDA 版本不同需验证。
3. `surface_windows` 对不等长窗口只返回同组；生产长镜头窗口等长，边界
   按 `n_windows` 截断。
4. fast 与 exact 的窗口记录在报告尾部可能差 1e-6（用户接受）；最终
   audio MOV 在两个素材上均相同。
5. 尚未把 GPU 帧估计/相位斜率/apply 写入；也尚未跑完整 L3、final_accept_v2
   的新架构全量复验。

## 5. 下一步建议

1. 在目标机（普通 Windows 用户目录/不同 CUDA）跑一次 `KTS_GPU_ALIGN=exact`
   短素材冒烟，确认 CuPy 导入/回退；
2. 对完整 C1305/C1306 做一次 final_accept_v2 + 透明 50 GiB 定向复验（默认
   路径未动，可选抽样）；
3. 若要让 fast 真正优于 exact，先 GPU 化 `_refine_window_peak`/下游 apply，
   否则保持 exact 推荐；
4. 决定是否把 `core/gpu_align` 合并回 v1.0 集成分支；合并前补 GPU 帧级
   no-go 报告和低配机 fallback 测试；
5. VERSION 仍为 0.9.0，不发布 v1.0.0。
## 6. 回退/验收补充

- `CUDA_VISIBLE_DEVICES=-1` + `KTS_GPU_ALIGN=exact`：`used=false`，411 窗
  测量完成，纯 CPU 44.1 s，无异常；
- `final_accept_v2` 重跑 **OVERALL: PASS**；
- 默认 `KTS_GPU_ALIGN=0` 的 Sony 单文件短测：`done=1`、
  `PRESERVED=37`；GPU 关闭时默认行为不变；
- 预算 8 MB 下 fast 411 窗完成且两次记录相同；VERSION 仍 0.9.0。
