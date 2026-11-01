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
   必须采用 hybrid（FP32 搜峰 + FP64 复核报告值）或接受 fast 模式；
3. **FP32 在帧级小 batch 收益有限（18.5× vs 11×）**；全速率阶段几乎无收益，
   不能作为架构重写的唯一依据；
4. 架构重写重点应是 **批处理/流水线/显存与回退统一**，而不是盲目 FP32。

## 4. 下一步（按架构文档 M2–M5）

1. 实现 `core/gpu_align/{backend,precision,window_cc,frame_gcc,session}.py`；
2. 用真实窗口/帧抽样验证 exact/fast/hybrid parity；
3. opt-in 集成窗级路径，跑 C1306 片段与端到端短测；
4. 若 FP32 hybrid 无法保持报告逐位一致，则只保留 exact FP64 + fast 实验；
5. 每个节点更新本报告/架构文档并 push。
