# 1KeyTranscoder v0.9.22 prerelease

> 2026-11-23 · 硬件解码完整性校验改为默认异步（队列 1，不占 `--jobs` 编码槽位）；
> `--no-metadata` 跳过该检查；缩放输出在 `--check full` 下按交付尺寸做 PSNR/SSIM。

## 1. 异步硬件解码完整性校验（默认开启）

此前完整性校验是编码后的内联步骤：独立软件解码整段输出。89 分钟 4K 片段
曾出现“编码已完成、目录不再更新、看起来卡死”的现象；旧实现还是单线程
`ffprobe`，超时 2 小时，长片可能被判失败后丢弃产物。

本版：

- 完整性解码改为 `-threads 0`（自动多线程）；同片段实测由约 149 s 降到
  约 21 s（约 7 倍）；
- 超时放宽到 6 小时；
- 每 30 秒输出一次 `[INTEGRITY] still software-decoding output` 心跳；
- **默认开启**，覆盖 classic、Sony 保留、DJI 保留三条硬件路径；
- `--no-async-integrity` 可退回旧的同步校验；
- `--jobs N` 只限制真正编码的进程数；等待校验的 worker 不占编码槽位；
- 校验队列深度 1；额外 worker 只等待校验；
- 开始下一文件编码前检查输出盘空闲空间，低于
  `KTS_ASYNC_DISK_RESERVE_GB`（默认 10 GB）时暂缓；
- 硬件校验失败后仍删除产物并回退 `--avsw` 重编；软件替代产物再做一次
  container + independent-decode 复核。

DJI 渐进式连接：当前组编码产物完成后，下一组即可开始编码；joined 源保留
到校验/交付结束，供 `verify_encode()` 读取源容器帧数。

### `--no-metadata` 语义

`--no-metadata` 现在**完全跳过**硬件解码完整性校验，并明确记录：

```text
[INTEGRITY] skipped | --no-metadata | ... | integrity=skipped
[HWDEC] ... hardware_integrity=SKIPPED
```

这是为 metadata-free 批处理提供的快速路径；需要完整性保证时不要使用
`--no-metadata`。

## 2. 缩放后的检查

- 帧数/样本数完整性检查与缩放无关：仍比较 source/output 的 sample 数量；
- Sony/DJI 结构检查使用 `expected_video_size` 作为目标尺寸；
- 修复 DJI `run_dji_check()` 缺少 `expected_video_size` 参数导致的
  `NameError`；
- `--check full` 的 PSNR/SSIM 在源/成品分辨率不同时不再 SKIP：源参考先用
  Lanczos 缩放到成品尺寸，再计算指标；报告写 `reference_scaled=true`；
- 修复 classic 硬件路径 `--check full` 质量门为不可达死代码的问题；现在
  classic/Sony/DJI 三条硬件路径的 full 质量抽样都会执行。

check 等级含义不变：

| 等级 | 检查内容 |
|---|---|
| `basic` | 核心轨道/元数据/帧数；无 PSNR/SSIM |
| `advanced` | basic + 完整结构 + Gyroflow；无 PSNR/SSIM |
| `full` | advanced + 按交付尺寸计算的 PSNR/SSIM |

`full` 的质量抽样仍是确定性 1-in-10、默认只覆盖 ≤60 s 片段；未抽中或超长
会明确 `SKIP`。需要强制全检时把配置里的 `quality_check.sample_rate` 设为 1。

## 3. 验证

- 单元自测：1137 PASS / 3 FAIL；3 项为环境/素材缺失（真实 Sony 语料 ×2、
  multihw 旧提示文案），与本版改动无关；
- 3 组 2 s DJI 合成集：
  - `--no-metadata`：每组 `integrity=SKIPPED`，输出正常；
  - classic + 完整性：异步释放、队列 1、全部 PASS；
  - DJI 保留 + `--jobs 1`：第 2 组在第 1 组校验期间开始编码，
    `PRESERVED=15 / MODIFIED=0 / MISSING=0`；
- DJI 保留 + 缩放 + `--check full`：结构检查 `PRESERVED=26 / MODIFIED=0 /
  MISSING=0`，Gyroflow PASS；
- 缩放质量直接测试：3840×2880 → 960×720，`reference_scaled=true`，
  PSNR 43.25 dB / SSIM 0.9835 / PASS。

## 4. 升级提示

- 默认异步完整性：如需旧行为，显式 `--no-async-integrity`；
- 使用 `--no-metadata` 时不再做硬件完整性校验，日志有 WARNING；
- 输出目录建议预留额外空间，异步队列允许短时 pending 产物堆积。
