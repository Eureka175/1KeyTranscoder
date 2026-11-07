# 硬件缩放验证矩阵与长测报告

> 日期：2026-11-07  
> 范围：NVENC HEVC / QSV HEVC，`--vpp-resize algo=bilinear` + `--output-res`  
> 结论：**NVENC 硬件缩放可用；QSV 功能可用但 bilinear 画质明显偏低，暂不建议作为默认路径。**  
> 状态：仅验证，不包含产品 CLI 实现。

## 1. 验证环境

| 项 | 值 |
|---|---|
| GPU | NVIDIA RTX 5070 Laptop / Intel Arc 140T |
| NVEncC | 9.31 (r4047) |
| QSVEncC | 8.26 (r4504) |
| 解码 | `--avsw` 软件解码（本次隔离硬件缩放变量） |
| 缩放算法 | bilinear |
| QSV 模式 | `--vpp-resize-mode quality` |
| 源文件 | 复制到 `F:\_1kt_hwscale_test\src`；**未写入** `F:\Pictures\2026` |
| 目标尺寸 | 3840×2160（native 控制）、2560×1440、1920×1080、1280×720 |

### 关键语法纠正

工具实际语法不是 `--vpp-resize width=...`：

```text
--vpp-resize algo=bilinear
--output-res 1920x1080
```

`width=` / `height=` 不属于 `--vpp-resize`；本轮先用错误语法得到
`Unknown param "width"`，已改用 `--output-res`，全部矩阵随后成功。

## 2. 源素材（工作盘副本）

| 场景 | 分辨率/帧率 | 像素格式 | 时长 | 用途 |
|---|---|---:|---:|---|
| `sdr_4k30_8bit` | 3840×2160@29.97 | yuv420p | 7.0 s | 矩阵 |
| `sdr_4k60_422_10bit` | 3840×2160@59.94 | yuv422p10le | 3.5 s | 矩阵 |
| `sdr_4k60_10bit` | 3840×2160@59.94 | yuv420p10le | 139.1 s | 矩阵 |
| 长测源 | 3840×2160@59.94 | yuv420p10le | **533.9 s** | 长测 |

## 3. 功能矩阵：24/24 通过

每个源 × 两个编码器 × 四个输出尺寸，共 **24 次编码**。

全部通过：

- 编码 rc=0；
- 输出分辨率与目标一致；
- 完整解码无错误；
- 4:2:2 / 10-bit 输入可以编码为 4:2:0 10-bit；
- 有源 VUI 的素材保留 `bt709` primaries/transfer/matrix；
- 所有输出均为 10-bit HEVC（8-bit 源按 main10 profile 输出 10-bit）。

### 3.1 VMAF（相对软件 bilinear 缩放参考）

| 源 | 编码器 | native | 1440p | 1080p | 720p |
|---|---|---:|---:|---:|---:|
| 4K30 8-bit | NVENC | 99.18 | 98.67 | 98.57 | 98.03 |
| 4K60 4:2:2 10-bit | NVENC | 96.80 | 96.81 | 96.89 | 96.79 |
| 4K60 10-bit | NVENC | 94.93 | 95.45 | 95.53 | 95.37 |
| 4K30 8-bit | QSV | 97.08 | 85.82 | 84.84 | 74.61 |
| 4K60 4:2:2 10-bit | QSV | 94.27 | 89.41 | 88.22 | 79.96 |
| 4K60 10-bit | QSV | 93.01 | 89.74 | 89.35 | 84.37 |

### 3.2 结论

**NVENC**：

- 所有目标尺寸 VMAF 94.9–99.2；
- 缩放后与 native 编码接近，部分场景略高/略低；
- 质量可接受，功能路径成立。

**QSV**：

- native 质量正常（93.0–97.1）；
- 一旦缩放，VMAF 明显下降：1440p 85.8–89.7，1080p 84.8–89.3，720p 74.6–84.4；
- 4K30 8-bit 的 720p 只有 **74.6**，属于不可接受的画质下降；
- 已确认 `--vpp-resize-mode quality` 不能消除该差异；
- 这是 QSV VPP resize 的实测结果，不是指标/时间戳造成的：已用 frame-index 对齐（`settb` + `setpts=N`）重新测量。

因此：

> **不建议在本阶段启用 QSV 硬件缩放。**  
> 应把 QSV 缩放留作后续研究项；NVENC 可进入产品实现。

## 4. 长测：完整 8.9 分钟 4K60 → 1080p

| 编码器 | wall | 输出大小 | 时长 | 帧数（packets） | 解码 |
|---|---:|---:|---:|---:|---|
| NVENC | **516.4 s** | 780,269,364 B (744 MiB) | 533.9 s | 32,002 | PASS |
| QSV | **601.4 s** | 551,943,656 B (526 MiB) | 533.9 s | 32,002 | PASS |

- 两次都完成完整 533.9 s 源，没有崩溃/超时；
- 输出分辨率 1920×1080，10-bit HEVC；
- 帧率 60000/1001，帧数 32,002，与源一致；
- 色彩元数据 `bt709` 保留；
- 长测前 10 s VMAF：
  - NVENC：**94.37**
  - QSV：**78.30**
- QSV 墙钟比 NVENC 慢约 **16%**，且质量明显更低。

## 5. 重要风险

1. **QSV bilinear 画质不可接受**
   - QSV bilinear 相对高质量缩放 VMAF 只有 70–86；
   - 但 QSV **bicubic/lanczos3** 可到 92–96，需后续补 bicubic 长测；
   - 不建议把 QSV bilinear 作为默认。

2. **QSV 输出时间戳可能是 VFR**
   - 4K30 源本身 VFR；QSV VPP 输出时间戳出现间隔；
   - 质量度量已改为 frame-index 对齐；
   - 产品集成时必须明确输出时间线（CFR/VFR）、`elst`、容器时长和检查器行为。

3. **色彩/HDR 尚未覆盖**
   - 本批源均为 bt709 SDR；
   - HLG/PQ 素材的 VPP 色彩处理、主显示/HDR 元数据尚未验证。

4. **算法矩阵只覆盖部分源/尺寸**
   - 覆盖 4K30 8-bit、4K60 10-bit、1080p/720p；
   - 未覆盖 4:2:2、4K→1440p、HDR、`--avhw`；
   - 算法结论仍需在这些组合上复验。

5. **软件 vs 硬件缩放语义**
   - 尺寸矩阵的 VMAF 参考是 FFmpeg 软件 bilinear；
   - 算法矩阵提供“同名软件核贴合度”和“相对 lanczos 高质量参考”两个口径；
   - 未来产品缩放需要明确与软件 `scale` 的像素级/相位差异接受度。

## 6. 建议

1. **产品实现先做 NVENC**：
   - 显式 `--scale WxH`（或等价输出尺寸参数）；
   - **默认 `--vpp-resize algo=bicubic`**（见 §8 算法矩阵）；
   - `--output-res WxH`；
   - 不默认缩放，保持当前无缩放行为。
2. **QSV**：bilinear 不可用；**bicubic / lanczos3 可作为候选**，
   但当前长测仍是 bilinear，需补一次 bicubic 长测后再决定是否接入。
3. **不要默认 bilinear**：除非常宽/低成本优先且接受明显软化。
4. **VCE 保持无入口**；
5. 做 HDR/10-bit/4:2:2 和 `--avhw` 路径的后续验证；
6. 处理缩放后的 `tkhd`、Sony/DJI 元数据、时间线和动态 VBV 归一化；
7. 本报告不改变 v0.9.4 包内容。

## 7. 产物

```text
F:\_1kt_hwscale_test\
├─ matrix_results.json      # 24 次 bilinear 尺寸矩阵
├─ algo_results.json        # 44 次算法对比
├─ long_results.json        # 2 次长测（bilinear）
├─ enc\  enc_algo\          # 输出
├─ long\                    # 长测输出
├─ metrics\  metrics_algo\  # VMAF/SSIM/PSNR
├─ logs\  logs_algo\        # 每次编码日志
└─ src\                     # 测试副本（不在源目录）
```

## 8. 算法对比：bilinear / bicubic / spline / lanczos

在 4K30 8-bit 和 4K60 10-bit 两个源上，比较 7 个 NVENC 算法和 4 个
QSV 算法，目标为 1080p/720p。共 **44 次编码，全部成功**。

`VMAF_lz` 表示相对 **软件 lanczos 高质量缩放** 的 VMAF（越高越接近高质量
缩放）；`VMAF_match` 表示相对同名软件缩放核的贴合度。

### 8.1 NVENC，1080p

| 算法 | 4K30 8-bit VMAF_lz | 4K60 10-bit VMAF_lz | 8-bit wall |
|---|---:|---:|---:|
| bilinear | 92.03 | 91.83 | 6.90 s |
| bicubic | **97.96** | 94.90 | 3.68 s |
| spline16 | 97.96 | 94.92 | 3.68 s |
| spline36 | **98.08** | **94.97** | 3.77 s |
| spline64 | 98.04 | 94.96 | 3.70 s |
| lanczos3 | **98.44** | **95.18** | 3.74 s |
| lanczos4 | 98.18 | 95.07 | 3.58 s |

### 8.2 QSV，1080p

| 算法 | 4K30 8-bit VMAF_lz | 4K60 10-bit VMAF_lz | 8-bit wall |
|---|---:|---:|---:|
| bilinear | **79.63** | **86.07** | 16.82 s |
| bicubic | **95.81** | 92.89 | **8.40 s** |
| spline36 | **95.96** | **92.97** | 17.52 s |
| lanczos3 | **96.34** | **93.16** | 10.23 s |

720p 结论相同：

- NVENC bilinear 约 92.26 / 92.31，其他算法 97.3–98.0 / 94.9–95.2；
- QSV bilinear 约 70.25 / 81.68，bicubic 95.35 / 92.80，
  spline36 95.60 / 92.87，lanczos3 96.02 / 93.07。

### 8.3 解读

- **bilinear**：
  - NVENC 明显偏软，相对高质量缩放的 VMAF 低约 3–6；
  - QSV 的 bilinear 是异常低点（70–86），不建议使用；
  - 只有“最低成本/接受软化”时才有意义。
- **bicubic**：
  - NVENC/QSV 都显著优于 bilinear；
  - QSV bicubic 在 1080p/720p 都接近/达到 92–96；
  - QSV 速度通常还是最快（8-bit 1080p 8.4 s）；
  - 振铃风险低于 lanczos，适合作为默认。
- **spline16/36/64**：
  - NVENC 与 bicubic 接近，spline36 略高；
  - QSV spline36 画质略高于 bicubic，但 1080p 墙钟约 17.5 s，
    明显慢于 bicubic 8.4 s；
  - spline64 在 10-bit 上更慢，收益很小。
- **lanczos3/4**：
  - VMAF_lz 最高；
  - lanczos3 在 QSV 720p 约 16.3 s，bicubic 约 8.3 s；
  - 更锐，但高压缩/低码率下振铃/过冲风险也更高；
  - 适合作为“质量优先”选项，不建议无条件默认。

### 8.4 推荐

| 用途 | 推荐 |
|---|---|
| 默认 | **bicubic** |
| 质量优先/细节优先 | **lanczos3** |
| 介于两者之间 | spline36 |
| 软件/低成本/接受软化 | bilinear（NVENC only；QSV 不用） |
| spline16/64 | 无明确收益，不建议默认 |


本次没有修改产品代码，没有重新打包。
