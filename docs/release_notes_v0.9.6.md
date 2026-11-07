# v0.9.6 发布说明 — 硬件缩放进入产品

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.6`；`VERSION` = `0.9.6`）  
> **基线**：`v0.9.4`  
> **日期**：2026-11-07

## 本版内容

- NVENC / QSV 硬件缩放进入产品：
  - `--scale WxH`（别名 `--resize WxH`）；
  - `--resize-algo`，默认 **spline36**；
  - NVENC：`--vpp-resize algo=... --output-res WxH`；
  - QSV：同上并加 `--vpp-resize-mode quality`。
- 支持经典、Sony 保留和 DJI 保留硬件路径。
- Sony/DJI 结构校验使用目标尺寸，不再把缩放误报为 MODIFIED。
- 软件后端、`--repair`、`--channel-sync-transparent` 明确拒绝 `--scale`。
- 工具不支持 `--vpp-resize` / `--output-res` 时预检失败，不输出未缩放文件。
- README 重写：默认英文，提供简体中文版本。

## 验证

- Unit：1070 PASS / 2 FAIL（Sony 语料缺失）。
- Toolchain：1091 PASS / 2 FAIL（Sony 语料缺失）。
- 真实 Sony 139 秒 4K→1080p：
  - NVENC：37/37 PRESERVED；
  - QSV：37/37 PRESERVED；
  - `rtmd` 保留。
- 无 `--scale` 时输出 3840×2160，原行为不变。
- 硬件缩放矩阵与长测见
  [`design/hardware_scaling_validation.md`](design/hardware_scaling_validation.md)。

## 包

```text
1KeyTranscoder-v0.9.6-win64-selfcontained.zip
```

包内同时包含：

- `README.md`（English）
- `README.zh-CN.md`（简体中文）

## 已知限制

- HDR 缩放尚未验证。
- DJI 保留管线的缩放尚未跑完整真实素材。
- 缩放输出在 `--check full` 下不计算源/成品 PSNR/SSIM（分辨率不同）。

**完整变更记录**：`git log v0.9.4..v0.9.6`。

