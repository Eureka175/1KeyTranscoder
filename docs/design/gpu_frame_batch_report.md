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
