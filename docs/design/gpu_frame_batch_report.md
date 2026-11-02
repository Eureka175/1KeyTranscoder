# 帧级 estimate_pair GPU 批处理（实验分支报告）

> **分支**：`feature/gpu-frame-estimation`（从 `feature/gpu-align-arch` 切出）
> **版本**：VERSION 保持 **0.9.0**
> **状态**：实现完成，**默认关闭**；单对测量有收益，但完整 channel_sync
> 未通过性能门，暂不进入生产默认。

## 1. 实现内容

| 模块/入口 | 内容 |
|---|---|
| `core/gpu_align/frame_est.py` | 两阶段帧级估计：8 kHz 全文件 coarse 缓存 + GPU 批量 GCC-PHAT surface；全速率第二级 surface 批处理 |
| `GpuFrameEstimator` | 单进程 GPU 批量 estimator；保留精确 CPU 峰逻辑版本 |
| `_peak_batch_windows` | 向量化峰/置信度近似（延迟用精确 argmax，置信度用高度/次峰/近似 prominence） |
| `estimate_frames_hybrid` | CPU 进程池 + GPU 批量**并发**处理不同帧区间；运行级 coarse 缓存复用 |
| `sync_estimate.estimate_pair` | 新增 `gpu_frames`/`KTS_GPU_FRAME_BATCH` 开关；任何异常自动回退 CPU |

开关：

```
KTS_GPU_FRAME_BATCH=0|1          # 默认 0；1 启用 GPU 帧级 batch
KTS_GPU_FRAME_HYBRID=0|1         # 默认 1；workers>1 时 CPU+GPU 分区间并发
KTS_GPU_FRAME_PEAK=fast|exact    # 默认 fast；exact 用原 CPU 峰逻辑
```

## 2. C1306 单对实测（41645 帧，69 min）

| 方案 | wall | 相对 CPU12 |
|---|---:|---:|
| CPU 12 进程（生产） | 17.3 s | 1.00× |
| GPU 单进程，exact 峰 | 55.4 s | 0.31× |
| GPU 单进程，fast 峰 | 34.0 s | 0.65× |
| CPU+GPU hybrid，coarse 缓存冷 | 24–25 s | ~0.7× |
| CPU+GPU hybrid，coarse 缓存热（隔离测试） | **15.97 s** | **1.26×** |
| CPU+GPU hybrid，接入后首对（冷缓存） | 13.0 s | 0.85× |
| CPU+GPU hybrid，接入后热缓存 | **6.30 s** | **1.76×** |

C1305 单对（48072 帧，80 min）：

| 方案 | wall | 相对 CPU12 |
|---|---:|---:|
| CPU 12 进程 | 12.13 s | 1.00× |
| hybrid 首对（冷缓存） | 15.27 s | 0.79× |
| hybrid 热缓存 | **9.18 s** | **1.32×** |

- delay 48071/48072 相同；全局 delay 相同；usable 9583 vs 9584。

Parity（hybrid vs CPU12）：

- 41645 帧中 **41644 帧 delay 完全相同**；
- 1 帧因置信度近似跨过 `min_confidence` 阈值变为 nan（usable 4971 vs 4972）；
- 全局 `delay_samples`、`fine_delay_samples` 完全相同；
- fast 峰置信度与 CPU 最大差约 **0.30**，主要来自 prominence 近似；
- 最终输出尚未做完整 channel_sync 验收。

## 3. 完整 channel_sync 结果

在 C1306 上启用 `KTS_GPU_FRAME_BATCH=1`（hybrid）后，完整
`run_channel_sync` 在 **~5 分钟内未完成**（日志停留在 stereo/帧阶段），
远超已有 fp32 基线 120.5 s，已终止。**未进入生产默认**。

原因判断：

1. 初始版本 hybrid 被 `_estimate_pair_parallel` 提前截走（已修复为
   `_hybrid` 时跳过 CPU 并行）；修复后单对稳定 1.76×，但完整入口仍慢，
   说明还有阶段级资源争用/内存压力；
2. 完整入口包含多对估计、宽窗复测和换参考阶梯，cold coarse build 被重复
   放大；
3. 向量化置信度与 CPU 有差异，不能直接作为默认行为。

## 3.1 frame-off 回归（同分支）

在同一分支显式 `KTS_GPU_FRAME_BATCH=0` 跑完整 C1306 channel_sync：

- 状态 `applied`，4 条 audio MOV sha256 **与已有 fp32 基线完全一致**；
- 当前机器 wall **320 s**，而早期基线记录为 120.5 s；本次未复现早期
  wall，需要进一步区分环境/长镜头阶段波动与代码回归；
- 因此本次只确认**功能一致**，暂不据此判定 full channel 的精确性能。

## 4. 结论

- **GPU 帧级单进程**：不可行（0.31–0.65×）；
- **CPU+GPU hybrid 单对**：有约 **1.2×** 收益，但完整入口不稳定/更慢；
- 因此保留为 **opt-in 实验路径**，默认仍走 CPU 帧级估计；
- 不建议在 v1.0 默认启用；需要先解决进程/GPU 资源调度和完整入口验证。

## 5. 下一步

1. 用**独立 GPU 进程 + CPU 进程池固定分区**（避免 GIL/线程池饿死），
   或限制 CPU 池到 `cores-2`，保证 GPU 主线程；
2. 只对长轨迹启用 hybrid；短轨迹保持 CPU；
3. 置信度仅对阈值附近帧用精确 CPU 峰逻辑，其余用向量化，降低 usable 偏差；
4. 完整 C1306/C1305 channel_sync 对照最终 audio MOV sha256；
5. 达标后再决定是否默认开启。

## 3.2 P0 完整入口阶段 A/B（同机、同素材、连续运行）

C1306 69 min，4ch，`KTS_GPU_ALIGN=fp32`：

### frame-off

| 阶段 | wall |
|---|---:|
| 帧级估计（含 stereo/换参考） | 112 s |
| long-take（窗口+残差+极性+apply） | 184 s |
| **总计** | **296 s** |

- 状态 `applied`；4 条 audio MOV sha256 与已有 fp32 基线**完全一致**；
- 早期基线 120.5 s 本次未复现；当前机器 long-take 阶段明显偏慢。

### frame-hybrid

- 13:33:58 开始帧级阶段；
- 13:35:32 完成 stereo；
- 13:37:22 才进入换参考轨迹判定；
- 13:39:47 仍在换参考/帧阶段，**>6 min 未完成**，已终止；
- 相对 frame-off 的 112 s 帧阶段，hybrid **>349 s**，不可接受。

结论：**完整入口 frame hybrid 不达标**，帧级路径保持 opt-in；
P0 同时显示当前 long-take 阶段（184 s）才是最大头，下一步优化重点应转向
long-take/窗口+残差，而不是继续默认化帧级。

## 3.3 P1 long-take profiling（同素材）

直接在隔离的 C1306 4ch raw 副本上跑 `align_time_varying`（`KTS_GPU_ALIGN=fp32`）：

| 项目 | wall |
|---|---:|
| 完整 long-take（3 units） | **55.2 s** |
| 其中 target_1 / target_2 / target_3 | 20.9 / 26.7 / 26.6 s |
| 预创建 12 进程 idle 池后再跑 | **52.6 s**（idle pool 无影响） |
| 单 target 窗测量 | 10–13 s/411 窗 |
| 单 target residual | 6.1 s |

单独测试的 long-take 约 55 s，但 channel_sync 内嵌 long-take 实测约
171–184 s，约为直接测试的 3 倍。已排除：

- 12 进程估计池 idle 残留（52.6 s 与 55.2 s 基本一致）；
- 算法本身（直接测试稳定 52–55 s）；
- 单 target 窗/残差（34 s + 18 s，合计约 52 s）。

最可能原因是 channel_sync 前置 112 s 帧估计后的整机热/频率/上下文状态，
或 GPU/CPU 资源争用；需要温度/频率和逐 unit 计时确认。

## 3.4 用户空闲状态下重测（2026-11-02 下午）

用户确认上一轮测试期间仍在用电脑；本轮在其未使用电脑时重测 C1306 4ch。

### 完整 channel_sync frame-off

- wall **256.4 s**，状态 `applied`；
- 4 条 audio MOV sha256 **与已有基线完全一致**；
- 早期 120.5 s 基线仍未复现。

### 完整 channel_sync frame-hybrid

- 帧阶段开始后 **>10 min** 仍未进入 long-take；已终止，不达标。

### 直接 long-take（同素材、单进程）

- 完整 long-take wall **211.6 s**（上一轮 55.2 s）；
- 3 次 window measure：**45–49 s/次**；
- 3 次 round-2 residual：**36–46 s/次**；
- reference apply：约 6–7 s/次；
- 同一窗口测量单独调用：**14.4 s**；带 `ref_decimated` + long-take 参数：**17.5 s**。

即：同一 GPU 窗口测量在 long-take 内部约 3 倍慢，独立调用正常。
GPU 计算基准（4096² fp32 matmul ×10）约 0.093 s，GPU 可用、时钟正常；
暂不能归因为 GPU 降频，下一步需在 long-take 内记录 `gpu_align.used`/stats
和每个 unit 的 measure 线程/进程状态。

## 3.5 早期 120.5 s 基线不可复现（关键结论）

用**同一个早期脚本** `work/perf/ws14/test_channel_sync_fast.py`
（`KTS_GPU_ALIGN=fast`、同源文件、同输出目录约定）在当前空闲机器重跑：

| 项目 | 早期 | 本次 |
|---|---:|---:|
| channel_sync wall | 120.5 s | **285.2 s** |
| status | applied | applied |
| 4 条 audio MOV sha256 | 基线 | **与早期完全一致** |

同时：

- 当前分支默认帧级路径为 CPU（`KTS_GPU_FRAME_BATCH` 未设）；
- 直接 long-take：早期 55.2 s → 本次 211.6 s；
- 但单独窗口测量 14–17 s、GPU matmul 正常；
- 同脚本、同代码路径、同输出哈希，wall 差 2.4×。

因此 **120.5 s 不是稳定可复现的基线**，更可能是早期运行时的
机器/缓存/GPU-CPU 电源/后台负载状态造成的离群值；不能作为当前
验收基线。需要一次受控的、重复 3 次的阶段级基准来重新建立基线。

## 3.6 硬盘假设验证（F: vs K:）

为排除 K: 盘/外置 NVMe 吞吐导致 early 120 s → 当前 256–285 s：

- K: 顺序写 **342 MB/s**、顺序读 **925 MB/s**；
- 同一 C1306 4ch long-take，输入/输出放到 F:（内部盘）：
  **198.3 s**；K: 同条件 **211.6 s**；
- 差异约 **6%**，F: 没有出现 120 s 级速度；
- long-take 内 measure 45–49 s/次的问题在 F: 同样存在。

结论：**硬盘不是主因**。F: 只能解释个位数百分比，不能解释
2.4× 的早期/当前差异。

