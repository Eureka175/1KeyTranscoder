# 性能优化 — 工作状态（长程自主任务）

> 最后更新：2026-11-01
> **分支**：`perf/audio-metadata`（从 `main` `5d08370` 切出；发布 tag `v0.8.11` = `4840dd8` 不动）
> **原始代码备份**：`work/perf/backup_v0.8.11/`（core/long_take.py、channel_sync.py、
> sync_estimate.py、audio_format.py、preservation/{fast_restore,gpac,transparent_restore}.py、
> production/output.py、1kt.py）
> **计划**：`docs/design/audio_metadata_perf_plan.md`

## 规则（用户约束）

1. 不允许开 subagent；全部自己完成。
2. **F 盘除项目目录 `F:\1KeyTranscoder` 外禁止写入**；临时/测试产物写 `K:` 或 `I:`。
3. 长程自主：每阶段输出成果/阻碍/下一步，然后继续。
4. 大改先开 branch + 备份原始代码（已做）。
5. 可用 CUDA 加速；N 卡消费级 FP64 弱 —— 优化中避免/少用 FP64。
6. 保持验收零回归（曲线 0/0/0、音频容差、确定性、fast-restore 矩阵/50 GiB E2E）。
7. **尽量减小磁盘写入次数/最小化落盘的中间文件**（生产 metadata/透明重封装尤其）；
   **显存与内存占用量**也要做必要优化（两者都是硬指标之一）。

## 环境

| 项 | 值 |
|---|---|
| CPU | Intel Core Ultra 9 285H，16 物理核 / 16 逻辑核 |
| GPU | NVIDIA RTX 5070 Laptop，8 GiB，驱动 616.92 |
| CUDA | toolkit 13.1（nvcc 可用）；pip 可下载 `cupy-cuda12x` |
| Python | 3.11.9；numpy 2.4.6、scipy 1.17.1；无 cupy/torch/numba（可装） |
| 磁盘 | F: 项目盘（仅项目目录可写）剩余 ~255 GB；I: 358 GB；K: 543 GB |
| 临时目录 | `K:\1kt_work\perf\`（所有测试/中间产物） |

## 基线（v0.8.11 实测，来自验收记录）

| 指标 | C1305（80.1 min） | C1306（69.4 min） |
|---|---|---|
| 音频域整文件 | 689.9 s | 389.1 s |
| long-take 附加 | 231.0 s | 193.8 s |
| RSS 主/树 | 775 MB / 1470 MB | 同量级 |
| 透明 50 GiB：stage mux | 258.7 s | 43 GiB 时 162.9 s |
| 透明 50 GiB：fast restore | 99.1 s | 43 GiB 时 57.8 s |
| 透明 50 GiB E2E | 12.8 min | — |

目标：C1305 ≤450 s、C1306 ≤260 s、long-take ≤130 s、50 GiB E2E ≤9 min、
RSS 主 ≤550 MB/树 ≤1.1 GB，正确性零回归。

## 阶段日志

### WS0 — 准备（完成）

- [x] 新 branch `perf/audio-metadata`；原始代码备份到 `work/perf/backup_v0.8.11/`。
- [x] 环境探测：CPU/GPU/CUDA/包/磁盘（见上）。
- [x] 临时目录 `K:\1kt_work\perf\`。
- [x] 状态文档（本文件）。
- 备注：目标（goal）系统提示 "model cannot resume a paused goal"，本任务改用本文件
  作为事实上的长程状态记录；不影响执行。

### WS1 — profile 基线（完成，2026-11-01）

C1306 基线（timing 模式，`K:\1kt_work\perf\ws1\c1306_timing.json`）：

| 阶段/函数 | 累计耗时（嵌套，wall 归因见备注） |
|---|---|
| **wall（整文件音频域）** | **296.3 s**（baseline 记录 389.1 s 含验收解码/写盘，`final_accept` 口径） |
| long-take `align_time_varying` | 159.8 s（内含 2 次窗测量 ≈96 s + 残差 34 + 施加 26.6 + 极性 5.5 + 反转 4） |
| 窗级测量 `measure_window_delays` | 4 次共 191 s（stereo 检测 2 次 + long-take 2 次，串行排在主线程） |
| stereo 组绑定检测 | 52.5 s（内部候选对已线程并行） |
| 帧级估计 `estimate_pair` | 18 次共 64.8 s（进程池 12 workers，串行 18 对） |
| 窗测量内部（cProfile） | `_coarse_cc_peak` 69.8 s（r2c 35.1 + c2r 18.2 s）、coarse_decimate 13.2 s、refine 6.0 s |
| `reference_interp_write` | 4 次 26.6 s（interp 7.4、write 7.1） |
| 残差测量 | `measure_residual_curve` 2 次 34.2 s（resample_poly 6.2 s + FFT） |
| 解码 | 4 通道并行 13.5–17 s |

结论：**窗级相关测量（~150 s wall）与帧级估计（~65 s）是两大头**；残差/施加/极性约 72 s。

### WS2 — 音频并行优化（第一轮完成，2026-11-01）

改动（`core/channel_sync.py`、`core/long_take.py`）：

1. **stereo 组绑定检测与锚点估计并行**：检测（只读 decoded raw）放到后台
   daemon 线程，锚点估计照常走进程池，主路径前 join；失败依旧回落为无绑定组。
2. **long-take 窗级测量并行**：各 target 的 60 s/10 s 连续性测量互相独立 →
   线程池并行；锚点 coarse 缓存只 `coarse_decimate` 一次并在各线程共享
   （`measure_window_delays(ref_decimated=...)`；数组只读，结果与重算逐位相同）。
3. 外部注入的自定义 measure 保持串行（不改变其线程安全假设）。

结果（同一 C1306 素材）：

| 指标 | baseline | WS2 后 | 变化 |
|---|---|---|---|
| **wall** | 296.3 s | **253.9 s** | **-14.3%** |
| long-take `align_time_varying` | 159.8 s | 125.1 s | -21.7% |
| stereo 检测 | 52.5 s | 64.1 s（与估计并行，被 CPU 争用拉长） | — |
| 帧级估计 | 64.8 s | 70.4 s | — |
| 残差/施加/极性（串行部分） | 72 s | 72 s | 未动 |

**正确性**：`work/perf/parity_check.py` 对比 baseline manifest（4 个 audio mov +
4 个 round1/round2 raw 的 sha256 + report 语义）→ **PARITY OK（逐位一致）**；
unit **1020 PASS / 2 FAIL**（仅环境项）。

### WS2b — 待做（下一轮）

- 每个 target 的**下游流水线**（曲线→round1 施加→残差→round2→极性）按 unit
  并行（预计再省 ~30 s）。
- 残差测量：60 s 窗重叠 resample_poly 目前每窗重复重采样；评估整文件预抽取
  （需先证明与逐窗口径逐位一致）→ 预计残差 34 s → ~15 s。
- 解码/健康扫描/估计阶段的内存与 CPU 再平衡；RSS 当前量级待测。
- CUDA：先评估能否在**不改结果**的前提下用于 coarse 抽取/FFT；FP64 避免。

### WS2 补充：磁盘写入/中间文件与内存（新要求，规划）

音频侧当前中间文件：decoded_*.f32（4×923 MB）→ fixed_*.f32 / round1/round2
（每 target 每轮 763 MB）→ audio_*.mov（4×572 MB）。计划：
- 合并 round1+round2 为**单次输出写**（流式两阶段插值，避免 round1 落盘）；
- 残差测量所需的 pass1 用流式虚拟输入（不落 round1 文件）；
- 评估 `decode` 后直接以 memmap/缓存复用，避免 fixed 再写一份；
- 目标：音频中间写盘从 ~11 GB 降到 ~4–6 GB（decoded + 最终 mov）。
- 元数据侧：见 WS4 —— **GPAC mux 后原地修补 moov/元数据**（不再写 stage +
  再整文件复制），目标 50 GiB 透明路径只剩 1 次 GPAC 写 + 小 moov 补丁；
  这是"最小化落盘中间文件"的核心。

## 下一步

1. 完成 WS1（timing + cProfile）。
2. 按热点实现 WS2（音频计算：FFT/中值/clamp/插值向量化 + 并行）。
3. WS3（I/O/趟数）→ WS4（元数据）→ WS5（fast-only 评估）→ WS6（长测试验收）。

## 关键文件

- 计划：`docs/design/audio_metadata_perf_plan.md`
- 生产代码：`core/long_take.py`、`core/channel_sync.py`、`core/sync_estimate.py`、
  `preservation/fast_restore.py`、`preservation/transparent_restore.py`
- 验收脚本（work/，可复用）：`work/long_take/s6/final_accept_v2.py`、
  `work/long_take/s6/full_align_only.py`、`work/long_take/s7/fast_restore_matrix.py`
