# v0.9.12 发布说明 — GPU 对齐无设备死锁修复

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.12`；`VERSION` = `0.9.12`）  
> **基线**：`v0.9.11`  
> **日期**：2026-11-13

## 修复

### 无 CUDA 设备时 channel-sync 残留任务死锁

当：

- `KTS_GPU_ALIGN` 默认开启（`fp32`）；
- 机器没有可用 CUDA 设备；
- long-take 路径调用 `GpuRunContext.residual_peak()`

时，GPU residual dispatcher 拿不到 session 就直接退出，但已排队的
`Future` 永远不会被完成，调用线程永久等待。

修复：

1. `residual_peak()` 在 session 不可用时立即返回 `None`，不再排队；
2. dispatcher 启动时 session 不可用则标记错误并 drain 队列；
3. 异常退出路径同样 drain，保证调用者不会永久阻塞。

这使无 GPU 环境自动回退 CPU，不再卡在 channel-sync long-take。

## 验证

- 无 GPU 模拟（`CUDA_VISIBLE_DEVICES=-1`、`KTS_GPU_ALIGN=fp32`）：
  - 4 声道自动 channel-sync 完成；
  - 编码 `done=1`；
  - 不再卡死。
- NVIDIA RTX 5070 接回后：
  - 4 声道 GPU channel-sync 完成；
  - `channel-sync: applied (3/4 channels fixed)`。

## 包

```text
1KeyTranscoder-v0.9.12-win64-selfcontained.zip
```
