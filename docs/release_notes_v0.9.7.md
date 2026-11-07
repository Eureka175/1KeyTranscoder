# v0.9.7 发布说明 — 缩放语义修正与 x265 硬件缩放

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.7`；`VERSION` = `0.9.7`）  
> **基线**：`v0.9.6`  
> **日期**：2026-11-07

## 本版内容

- 缩放参数拆分：
  - `--resize WxH`：精确输出分辨率，不修正长宽比；
  - `--scale FACTOR`：按长边倍率缩放并保持长宽比，例如 `0.5` / `2` / `0.25`；
  - 两者互斥；`--resize-algo` 默认 **spline36**。
- x265 / SVT-AV1 支持缩放：
  - 默认硬件解码；
  - 在解码路径使用 `scale_cuda`（CUDA）或 `vpp_qsv`（QSV，HQ 模式）；
  - 无硬件解码时回退 FFmpeg CPU 缩放。
- Sony / DJI 保留路径的缩放尺寸校验同步适配。
- README 英文/简体中文同步更新。

## 验证

- Unit：1082 PASS / 2 FAIL（Sony 语料缺失）。
- Toolchain：1103 PASS / 2 FAIL（Sony 语料缺失）。
- x265 硬件解码缩放实测：
  - 4K 4:2:0 8-bit → 1920×1080；
  - 4K 4:2:2 10-bit → 1920×1080，保持 4:2:2 10-bit；
  - Sony C1515 139 秒 4K→1080p：**37/37 PRESERVED**，`rtmd` 保留。
- NVENC 实测：
  - `--scale 0.25`：3840×2160 → 960×540；
  - `--resize 1600x900`：3840×2160 → 1600×900（不修正长宽比）。
- x265 硬件解码器：`hevc_cuvid`（CUDA）实际使用。

## 包

```text
1KeyTranscoder-v0.9.7-win64-selfcontained.zip
```

## 已知限制

- x265/SVT-AV1 的 CUDA 硬件缩放只支持 bilinear/bicubic/lanczos；
  `spline*` 在硬件路径映射为 lanczos。QSV 硬件路径使用 HQ 模式。
- 需要精确 spline36 时，无硬件解码的 CPU 回退路径使用 FFmpeg `spline`。
- HDR 缩放尚未验证。
- DJI 保留管线的缩放尚未跑完整真实素材。

**完整变更记录**：`git log v0.9.6..v0.9.7`。
