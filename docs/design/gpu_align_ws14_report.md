# WS14 报告 — CUDA FP32/FP64 初测与架构重写启动

> 日期：2026-11-01
> 分支：`feature/gpu-align-arch`（从 `feature/v1.0-async` 切出）
> 版本：VERSION 保持 `0.9.0`（不改为 v1.0.0）
> 设计：`docs/design/gpu_align_architecture.md`

## 1. 环境

| 项 | 值 |
|---|---|
| GPU | NVIDIA GeForce RTX 5070 Laptop，8 GiB，驱动 616.92 |
| CUDA | toolkit headers `C:\CUDA12`；CuPy 14.2.0（CUDA 12.9 runtime） |
| CPU | Intel Core Ultra 9 285H，16 核 |
| Python | 3.11.9 / numpy 2.4.6 / scipy 1.17.1 |
| 数据 | `work/long_take/accept/src_audio` 的 C1306 48 kHz f32 解码通道 |

**环境问题**：CuPy NVRTC 无法直接处理非 ASCII 用户路径；必须通过
`C:\sp_ascii` junction 导入 CuPy，并设置 `CUDA_PATH=C:\CUDA12`。新引擎
`backend.py` 会内置这条规则。

## 2. 初始微基准（300 s 数据；64 帧批）

命令：

```
python work/perf/ws14/bench_gpu_align.py --seconds 300 --frames 64 \
  --json K:\1kt_work\perf\ws14\bench_initial.json
```

### 2.1 窗级 coarse CC（12 kHz，60 s 窗，±250 ms）

| 实现 | 单窗 wall | 相对 CPU | 结果 |
|---|---:|---:|---|
| CPU `_coarse_cc_peak`（scipy float64） | **80.5 ms** | 1.00× | lag=0, cc=0.9214612208 |
| GPU FP64（cuFFT float64） | **6.98 ms** | **11.5×** | lag/cc 与 CPU 一致（~1e-15） |
| GPU FP32（cuFFT float32） | **1.13 ms** | **70.9×** | lag 相同；cc 差 **6.36e-08** |

### 2.2 帧级 GCC-PHAT coarse（64 帧，200 ms 帧 / 100 ms hop / ±80 ms）

| 实现 | 64 帧 wall | 相对 CPU |
|---|---:|---:|
| CPU `gcc_phat_int` 循环 | **21.7 ms** | 1.00× |
| GPU FP64 batched surface | **1.97 ms** | **11.0×** |
| GPU FP32 batched surface | **1.17 ms** | **18.5×** |

- FP32 与 FP64 的 argmax **64/64 相同**；
- surface 平均绝对差 7.3e-09，最大 5.8e-08；
- 该批次很小（nfft=4500），主要受 Python/传输/内核启动限制。

### 2.3 帧级 GCC-PHAT 全速率二阶段（63 帧，9600 样本帧）

| 实现 | wall |
|---|---:|
| GPU FP64 | 2.96 ms |
| GPU FP32 | 2.83 ms |

全速率批次小、nfft≈19200，FP32 相对 FP64 几乎无收益（**仅 -4%**）；
说明该阶段需要更大 batch 或把 surface→峰逻辑整体 GPU 化才有意义。

## 3. 初步结论

1. **FP64 GPU 已显著快于 CPU**：窗级 ~11.5×，帧级 batch ~11×；与之前
   `gpu_cc` 的"单窗 36×"差异来自本次包含 host/启动/归一化开销。
2. **FP32 在窗级有巨大潜力（~70×）**，但报告字段不逐位一致（cc 差 6e-8），
   必须采用 hybrid（FP32 搜峰 + FP64 复核报告值）或接受 fp32 模式；
3. **FP32 在帧级小 batch 收益有限（18.5× vs 11×）**；全速率阶段几乎无收益，
   不能作为架构重写的唯一依据；
4. 架构重写重点应是 **批处理/流水线/显存与回退统一**，而不是盲目 FP32。

## 4. 下一步（按架构文档 M2–M5）

1. 实现 `core/gpu_align/{backend,precision,window_cc,frame_gcc,session}.py`；
2. 用真实窗口/帧抽样验证 fp64/fp32/hybrid parity；
3. 集成窗级路径，跑 C1306 片段与端到端短测；
4. 若 FP32 hybrid 无法保持报告逐位一致，则只保留 fp64 FP64 + fp32 实验；
5. 每个节点更新本报告/架构文档并 push。
---

## 5. M2/M3 里程碑结果（2026-11-01 17:15）

### 5.1 统一架构落地

新增 `core/gpu_align/`：

- `backend.py`：CuPy/CUDA 探测、ASCII 路径、VRAM 预算、OOM/回退；
- `precision.py`：`fp32`（FP32，推荐）/`fp64`（FP64）/`hybrid`（预留）；
- `window_cc.py`：**批量窗级 coarse CC** + `surface_windows()` 一次算全窗口
  相关面，CPU 只做连续性峰值选择；
- `frame_gcc.py`：批量 GCC-PHAT surface（CPU 峰逻辑保持）；
- `refine.py`：GPU 整数 lag 精化（单窗，实测不划算，保留为对照）；
- `residual_cc.py`：残差 coarse 引擎；
- `session.py`：生命周期/预算/统计；
- `parity.py`：CPU vs GPU 窗口/表面 parity 合同；
- 单元测试 `tests/selftest/suites/gpu_align.py`（无 GPU 也可跑）。

### 5.2 真实 C1306 长镜头单对（60 s 窗 / 10 s hop，411 窗）

| 模式 | wall | 相对 CPU | 窗口记录差异 |
|---|---:|---:|---|
| CPU 参考 | **36.7 s** | 1.00× | — |
| GPU `fp64`（FP64） | **14.3 s** | **2.57×** | 411/411 **完全一致** |
| GPU `fp32`（FP32，第一次调用，含 JIT/prefetch） | **11.3 s** | **3.29×** | — |
| GPU `fp32`（同进程第二次，稳态） | **3.23 s** | **11.6×** | delay/valid/polarity **全同**；`coarse_cc`/`confidence` 最大差 **1.0e-6** |

`KTS_GPU_ALIGN=fp32` 差异汇总（411 窗）：

- delay 数值差：0 窗；max |Δdelay| = 0.0
- valid 差异：0；polarity 差异：0
- `coarse_cc` max |Δ| = 1.0e-6；`confidence` max |Δ| = 1.0e-6
- initial lag 一致（-1），initial cc 差 0

用户决策：**不要求 FP64 同精度，不差太多即可**。因此 `KTS_GPU_ALIGN=1`
默认走 fp32；`fp64` 仅用于对照/验收。

### 5.3 帧级 GCC-PHAT surface

64 帧 coarse batch（200 ms 帧 / 100 ms hop / ±80 ms）：

| 实现 | surface wall | 相对 CPU |
|---|---:|---:|
| CPU `gcc_phat_int` 循环 | 24.0 ms | 1.00× |
| GPU FP64 batch | 7.7 ms | **3.12×** |
| GPU FP32 batch | 6.7 ms | **3.57×** |

argmax 64/64 相同；但 CPU 峰/置信度逻辑仍占大头，帧级**整条路径**尚未
证明有收益，暂不接入生产。

### 5.4 精化步骤

`_refine_window_peak`（2 s 段、±8 整数点积）在 Windows 上 CPU 约 0.09 s
/25 窗；GPU 单窗调用 0.18 s，**更慢**（kernel 启动/host 读主导）。因此
fp64 路径的 2.57× 被精化限制；当前不做 GPU 精化，后续可评估批量
两遍法（coarse 全窗 → 批量精化 → 连续性复核）。

### 5.5 结论（M2/M3）

- 窗级粗相关架构可行：FP64 fp64 2.57×、FP32 fp32 稳态可达 11.6×；
- fp32 仅报告尾部差 1e-6，delay/valid/polarity 全同，符合用户"不差太多"；
- 帧级/精化路径尚不划算，保持 CPU；程序默认开启 fp32；
- 下一步：集成短测、fallback/确定性、最终 audio MOV 对照；
- VERSION 仍为 `0.9.0`，不发布 v1.0.0。
---

## 6. 端到端 long-take 与精度结论（M4，2026-11-01 17:40）

> 用户决策：**不需要与 FP64 同精度，差得不多就行**。因此 fp32 可作为
> 可选快速路径，但端到端仍以实际收益和最终 raw 一致性判定。

### 6.1 隔离源文件、完整 `align_time_varying`（C1306 4ch，3 targets）

每次模式使用独立源副本，避免 in-place 施加互相污染：

| 模式 | wall | 相对 CPU | 最终 raw |
|---|---:|---:|---|
| CPU（窗口 + 残差全 CPU） | **115.1 s** | 1.00× | baseline |
| fp64（FP64 窗口 + FP64 residual；prewarm + 并行 unit） | **47.1 s** | **2.45×** | 与 CPU **sha256 全同** |
| fp32（FP32 窗口 + FP32 residual；prewarm + unit 串行） | **57.4 s** | **2.01×** | 与 CPU **sha256 全同** |

2ch 诊断（1 target）：CPU 76.7 s；fp32 39.0 s（**1.97×**）；
fp64 45.1 s（1.70×）。

unit 拓扑对照（C1306，3 targets，无 GPU residual）：fp64 并行
**71.3 s** vs 强制串行 **88.8 s**；fp32 串行 **81.7 s**。因此策略为
**fp64 保留并行 + prewarm，fp32 串行**。

### 6.3 GPU residual 接入

`long_take.measure_residual_curve` 在 `KTS_GPU_ALIGN` 下使用新的
`ResidualCCEngine`（同一公式、同一 coarse seed 语义）：

| 项 | CPU | fp64 | fp32 |
|---|---:|---:|---:|
| C1306 ch0/ch1 residual wall | **17.0 s** | **7.1 s（2.4×）** | **7.3 s（2.3×）** |
| 1 s 网格曲线 | 4165 点 | **4165/4165 完全相同** | **4165/4165 完全相同** |
| summary（ok/n_blocks/median/max） | baseline | 完全相同 | 完全相同 |

residual 的 fp64 与 fp32 在本对素材上都与 CPU **逐点相同**；这是端到端
从 71.3 → 47.1 s（fp64）/81.7 → 57.4 s（fp32）的主要原因。

C1305 ch0/ch1 复验：CPU 18.9 s；fp64 7.9 s / fp32 7.6 s；1 s 网格
4808/4808 点相同（fp64 与 fp32 均相同）。

### 6.2 关键修复

- **GPU measure units 串行化**：`align_time_varying` 原来对 3 个 target
  并行测量；GPU 设备共享时并行只增加 context/FFT-plan 竞争。检测
  `KTS_GPU_ALIGN` 后改为：主线程 prewarm + target 顺序测量（下游 apply
  仍按原逻辑）。
- **主线程 prewarm**：`sync_estimate.prewarm_gpu_align()` 在 spawn 前编译
  CuPy 内核/FFT plan，避免每个 worker 重复 JIT。
- **WindowCCEngine.warmup** 增加 `surface_windows` 小批量预热，覆盖
  crop/concat 路径。

### 6.3 精度/收益结论

- fp32 与 CPU：411 窗 delay/valid/polarity 全同；`coarse_cc`/`confidence`
  max |Δ| = **1e-6**；最终 raw sha **一致**。
- fp64 与 CPU：411/411 窗口记录**完全一致**；最终 raw sha **一致**。
- residual 的 fp64/fp32 曲线均与 CPU 4165/4165 点相同（该对素材）。
- **本机 end-to-end fp64 快于 fp32**（2.45× vs 2.01×）：fp32 的收益被
  CPU refine/下游 apply 吃掉，且 fp64 的内核/FFT plan 已在前面调用中预热。
  因此当前推荐：
  - **程序默认：fp32**（CUDA 可用时）；fp64 作为可选对照/保守路径；
  - `--gpu-align off` 可强制 CPU；CUDA 不可用时自动回退；
  - 不再追求 FP32 与 FP64 逐位同精度。

### 6.4 未完成

- 帧级整路径（CPU 峰逻辑）仍无收益；
- `_refine_window_peak` 单窗 GPU 较 CPU 慢，需批量两遍法；
- C1305/C1306 的最终 audio MOV/final_accept 尚未用新架构全量重跑；
- 程序默认 fp32；`--gpu-align off` 可关闭；VERSION 0.9.0 不变。
---

## 7. C1305 复验（M5）

同一隔离副本方法，C1305 4ch / 3 targets：

| 模式 | wall | 相对 CPU | 最终 raw |
|---|---:|---:|---|
| CPU | **131.8 s** | 1.00× | baseline |
| fp64（窗口 + residual GPU） | **62.7 s** | **2.10×** | 与 CPU **sha256 全同** |

C1305 的最终 3 条 raw 与 CPU **逐字节一致**；residual 接入后收益从
1.20× 提升到 **2.10×**。两个素材共同证明：

- fp64 GPU 路径可作为保守对照；
- fp32 路径在 C1306 上最终 raw 同样一致；
- 端到端收益受下游 CPU（apply/残差/写盘）限制，下一步应优化这些环节，
  而不是继续堆 FFT 吞吐。
---

## 8. 可重复的 tracked 基准

新增 `tests/benchmark_gpu_align.py`（开发基准，不纳入 autotest）：

```
python tests/benchmark_gpu_align.py --seconds 300 --windows 20 --frames 64 \
  --json K:\1kt_work\perf\ws14\tracked_bench.json
```

300 s C1306 数据、20 个 60 s 窗、64 帧的结果：

| 项 | CPU | fp64 | fp32 |
|---|---:|---:|---:|
| 窗级粗相关（20 窗） | 1.706 s | **0.209 s（8.1×）** | **0.092 s（18.6×）** |
| 窗级 parity | — | max \|Δcc\|=8.9e-16 | max \|Δcc\|=4.3e-7 |
| 帧级 surface（64 帧） | 0.023 s | **0.0118 s（1.95×）** | **0.0116 s（1.99×）** |
| 帧级 argmax 一致 | — | 64/64 | 64/64 |

帧级整路径仍需 CPU 峰逻辑，因此该表只说明 surface 阶段的加速，不能
直接换算成 `estimate_pair` 的端到端收益。
---

## 9. 完整 channel_sync 生产入口验证（C1306）

使用真实 C1306 源文件（43 GB）与 `channel_sync.run_channel_sync` 的完整
生产路径（stereo 组检测 + 帧级估计 + long-take 窗级/残差 + 最终音频
编码），不是只测 `align_time_varying`：

| 模式 | wall | 相对 CPU | 最终 4 条 audio MOV |
|---|---:|---:|---|
| CPU | **208.8 s** | 1.00× | baseline |
| fp64（窗口 + residual GPU） | **123.1 s** | **1.70×** | **sha256 全部相同** |
| fp32（FP32 窗口 + residual；serial units） | **120.5 s** | **1.73×** | **sha256 全部相同** |

- 帧级估计仍为 CPU（`estimate_pair` 12 进程池），stereo 检测也是 CPU；
  收益完全来自窗级 coarse CC 与 residual seed 的 GPU 化；
- 4 条音频 MOV 的 sha256 **逐条相同**，说明整条 aligned 音频路径输出
  一致；
- **fp32 在完整入口上略快于 fp64（120.5 vs 123.1）**，与 long-take
  单独测试（fp32 57.4 vs fp64 47.1）不同；完整入口中 stereo/frame CPU
  阶段占比较大，fp32 的窗口+residual 收益能体现；
- 这组数据（208.8 s）与 v0.9.0 的音频域 C1306 基线（约 204–217 s）同一
  量级，可视为可比的完整入口结果。
---

## 10. 完整 channel_sync C1305

| 模式 | wall | 相对 CPU | 最终 4 条 audio MOV |
|---|---:|---:|---|
| CPU | **300.9 s** | 1.00× | baseline |
| fp64（窗口 + residual GPU） | **204.7 s** | **1.47×** | **sha256 全部相同** |
| fp32（FP32 窗口 + residual） | **199.6 s** | **1.51×** | **sha256 全部相同** |

C1305 的完整入口收益低于 C1306（1.70–1.73×），因为 C1305 的
stereo/frame/apply 等 CPU 占比更高；fp32 199.6 s 略优于 fp64 204.7 s，
4 条最终 audio MOV 均与 CPU 逐字节一致。
---

## 11. 回退与最终验收

- **无 CUDA 回退**：设置 `CUDA_VISIBLE_DEVICES=-1`、`KTS_GPU_ALIGN=fp64`
  直接跑 `measure_window_delays`：`gpu_align.used=false`，411 窗测量完成，
  wall 44.1 s（纯 CPU），无异常。说明 CUDA 不可用时不会中断生产路径。
- **final_accept_v2**（C1305/C1306 曲线+音频层）：GPU 工作后重跑
  **OVERALL: PASS**（使用既有验收产物；C1305/C1306 上 fp32/fp64 的最终音频与 CPU 相同）。
- **默认路径短测**：`KTS_GPU_ALIGN=0` 的 Sony 单文件 `1kt.py`：
  `done=1`、`PRESERVED=37 MODIFIED=0 MISSING=0`；GPU 不启用时行为不变。
- **显存预算压力**：`KTS_GPU_ALIGN_BUDGET_MB=8` 下 fp32 仍完成 411 窗，两次窗口记录相同（deterministic），wall 10.6 s；说明批大小降级路径可用。
- **CLI 入口**：4ch 30 s 素材 `--gpu-align off` vs `--gpu-align fp64`：
  两者均 done=1、PRESERVED=36，视频/音频/解码 120 帧完全一致；确认
  `--gpu-align` 映射到 `KTS_GPU_ALIGN` 的链路可用。
- **VERSION** 仍为 `0.9.0`；未发布 v1.0.0。
