# v0.9.8 发布说明 — lanczos 默认与 AV1 data 轨修复

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.8`；`VERSION` = `0.9.8`）  
> **基线**：`v0.9.7`  
> **日期**：2026-11-07

## 本版内容

- 缩放算法收窄为 **bilinear / bicubic / lanczos**；
- **全部路径默认 lanczos**：NVENC、QSV、x265、SVT-AV1；
- 移除 `spline16/spline36/spline64/lanczos3/lanczos4` 选项；
- 修复 classic AV1 在带 Sony `stts` data 轨的 MP4 源上写入失败：
  - 原因：FFmpeg MP4 muxer 无法为 `codec=none` 的 data 流建立 sample entry；
  - 修复：classic AV1 丢弃 data/attached 流，保留视频、音频、字幕；
  - 详见 [`design/av1_stts_data_track.md`](design/av1_stts_data_track.md)。

## 验证

- Unit：1083 PASS / 2 FAIL（Sony 语料缺失）。
- Toolchain：1104 PASS / 2 FAIL（Sony 语料缺失）。
- 默认缩放参数实测：
  - NVENC：`--vpp-resize algo=lanczos`；
  - x265：`scale_cuda` 使用 lanczos；
  - AV1：4K 4:2:0 8-bit → 1920×1080 成功；
  - 原有的 `--resize` / `--scale` 尺寸行为不变。

## 包

```text
1KeyTranscoder-v0.9.8-win64-selfcontained.zip
```

## 已知限制

- HDR 缩放尚未验证。
- DJI 保留管线的缩放尚未跑完整真实素材。
- x265/SVT-AV1 的硬件缩放算法受 GPU 滤镜支持范围限制；本版仅保留三种算法。

**完整变更记录**：`git log v0.9.7..v0.9.8`。
