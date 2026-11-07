# 硬件加速缩放可行性研究

> 状态：**研究 + 已进入产品实现（2026-11-07）**  
> 产品 CLI：`--resize WxH`（精确尺寸，不修正长宽比）、`--scale FACTOR`（长边倍率，保持长宽比）、`--resize-algo`（默认 `lanczos`，仅 bilinear/bicubic/lanczos）。  
> VCE：**不做入口**。  
> 产品算法收窄为 bilinear/bicubic/lanczos，默认 **lanczos**；算法对比见
> [`hardware_scaling_validation.md`](hardware_scaling_validation.md)。

## 1. 目标

评估在“保持现有转码语义”的前提下，能否把输出分辨率缩放放到硬件路径中，减少
CPU 缩放和额外的显存/内存往返。

本轮不添加 CLI、不改编码命令、不改配置。本文只记录证据、约束、集成点和测试计划。

## 2. 当前架构的现状

1. **源分辨率是隐式输出尺寸**
   - 目前没有 `--scale` / `--output-size`，输出宽高等于源宽高；
   - `SourceInfo.width/height` 只用于分类、缩放参数和日志，不用于改分辨率。

2. **两条编码路径**
   - x265 / SVT-AV1：FFmpeg 软解码/软编码；如要缩放，FFmpeg 的 `scale` 是软件
     filter，不属于“硬件加速缩放”；
   - NVENC / QSV：rigaya 的 `NVEncC64` / `QSVEncC64` 直接读取源文件，内部
     `--vpp-resize` 是硬件 VPP 路径，不经过本项目写像素。

3. **硬件路径没有输出尺寸参数**
   - `encoders/nvencc.py` / `encoders/qsvencc.py` 的 `command()` 只接收源、
     输出、profile、色度、位深等；
   - `core/batch_hw.py` 的格式规划只处理 4:2:2 / 10-bit 的降级，不处理尺寸。

## 3. 工具能力证据（本机实测帮助）

本机工具版本：

- `NVEncC_9.31_x64/NVEncC64.exe`
- `QSVEncC_8.26_x64/QSVEncC64.exe`

两者都提供 `--vpp-resize` 选择插值算法（例如 `algo=bilinear`），
输出目标尺寸由 `--output-res <W>x<H>` 指定：

```text
--vpp-resize algo=bilinear
--output-res 1920x1080
```

> 注意：`width=`/`height=` 不是 `--vpp-resize` 的参数（本轮实机验证已纠正）；
> 它们属于其他 VPP 选项。必须使用 `--output-res` 指定目标尺寸。

QSV 另提供 `--vpp-resize-mode auto|lowpower|quality`；本轮矩阵使用
`quality`。NVENC 没有 `--vpp-resize-mode`。

因此：

- **NVENC 可以做单进程内硬件缩放 + 硬件编码，无需 CPU 回读；**
- **QSV 同样可以做 VPP 缩放 + 编码；**
- VCE 无入口，不在本次范围。

FFmpeg 侧另有：

- `scale_cuda`（CUDA 帧，`interp_algo=bilinear`）；
- `scale_qsv` / `vpp_qsv`；
- 但 x265/SVT-AV1 是软件编码，使用这些 filter 需要 hwupload/hwdownload，
  只适合作为“硬件解码 + CPU 编码”的中间路径，不是当前硬件后端的主路径。

## 4. 建议的集成点（未来）

### 4.1 显式输入

新增显式参数，不做隐式默认缩放：

```text
--scale WxH
或
--scale-width N --scale-height N
```

约束：

- 宽高都必须是偶数（4:2:0 chroma）；
- 不支持只给一边后猜另一边，除非显式 `--scale-fit`；
- 默认算法 `bilinear`；
- VCE 不识别这些参数；
- 不与 `--no-downgrade` 冲突：尺寸缩放不是格式降级。

### 4.2 NVENC

在 `NvencBackend.command()` 的参数追加：

```text
--vpp-resize algo=bilinear
--output-res <W>x<H>
```

需要验证：

- `--avhw` 读取路径下 VPP resize 是否保持帧完整性；
- 4:2:2 / 10-bit 输入在 VPP 后的输出格式；
- `--output-csp yuv422` 与 resize 的组合；
- Sony `--avsw` 预防性软件解码路径是否仍适用；
- VUI/颜色/HDR 元数据是否随缩放保持。

### 4.3 QSV

```text
--vpp-resize algo=bilinear
--vpp-resize-mode quality   （或 lowpower，需要实测）
--output-res <W>x<H>
```

QSV 的 `--vpp-resize-mode` 会影响质量/功耗，需要单独标定。

### 4.4 软编码路径

如果未来也要支持 x265/SVT-AV1 的输出缩放：

- 优先使用 FFmpeg 软件 `scale`（确定性、无硬件依赖）；
- 或 `scale_cuda` / `scale_qsv` + 显式 hwupload/hwdownload；
- 不应把硬件缩放强行塞进当前 x265 命令生成器。

## 5. 关键设计问题

### 5.1 码率/VBV 与输出尺寸

当前 NVENC 动态 VBV 按 **源 OB** 计算。若输出缩小：

- 直接沿用源 OB 的 maxrate 会偏高，可能浪费码率；
- 需要决定是否按输出像素率重新归一化，例如：

```text
target_ob = source_ob * (out_px / src_px)
```

- 但这会改变“NVENC 跟随 x265”的现有语义；
- 建议作为缩放实现的独立决策，不混入本次 NVENC 动态 VBV 改动。

### 5.2 帧完整性

硬件缩放不能绕过现有的 `encoders.integrity.verify_encode`：

- 缩放后的帧率、帧数、时长必须与源一致；
- Sony 路径的 `rtmd` 时间线仍需保持；
- 任何 `--avhw` 结果仍受帧完整性门控制。

### 5.3 元数据

Sony/DJI 保留管线要在缩放后继续保留：

- `nrtm` / `djmd` / `dbgi` / `tmcd`；
- `tkhd` 宽高、`elst`、`stts`；
- 色彩原数据（primaries/transfer/matrix/range）。

缩放会改变 `tkhd` 尺寸，必须由容器重建阶段正确处理，不能只改视频 ES。

### 5.4 性能

预期：

- NVENC/QSV 的 VPP resize 在 GPU 内完成，比“解码到 CPU → 软件缩放 →
  上传编码”便宜；
- 但硬件 VPP 仍占 GPU 时间，可能降低编码吞吐；
- 需要实测 4K→1080p、4K→1440p、8K→4K 等组合；
- 需要测 `--jobs` 并发下的显存/GPU 占用。

## 6. 风险

1. 4:2:2 / 10-bit 输入在 VPP resize 后可能不被编码器接受；
2. 硬件 reader（`--avhw`）与 VPP 的组合可能触发已知的 Sony 时间线丢帧问题；
3. 非整数比例缩放会产生不同的色度采样/相位，需要与软件 `scale` 做画质对比；
4. HDR/HLG/PQ 元数据可能不会被 VPP 自动继承；
5. QSV `--vpp-resize-mode` 的 lowpower/quality 行为需要实机标定；
6. 输出尺寸变化会影响容器 `tkhd`、Sony/DJI 元数据重建、检查器；
7. 缩放后码率/VBV 若仍按源 OB，会偏离目标质量。

## 7. 结论

**技术上可行**：

- NVENC：单进程内 `--vpp-resize` + 硬件编码，改造成本最低；
- QSV：同样支持，需额外处理 `--vpp-resize-mode`；
- 实测默认算法推荐 **bicubic**（不是 bilinear）；质量优先可用 **lanczos3**；
  bilinear 在 QSV 上画质明显偏低，只适合最低成本/接受软化的场景；
- 不建议走 FFmpeg `scale_cuda` 作为主路径。

**建议的落地顺序**（未来）：

1. 只做 NVENC `--scale`，默认 bicubic；
2. 加完整性/元数据/画质回归；
3. 再评估 QSV（bicubic/lanczos3，需补长测）；
4. VCE 保持无入口；
5. 缩放与动态 VBV 的码率归一化单独设计。

本轮不实现任何缩放代码。

> 更新（2026-11-07）：上述“本轮不实现”指研究阶段。产品实现随后已落地：
> `--resize WxH` + `--scale FACTOR` + `--resize-algo`（默认 lanczos），NVENC/QSV/x265/SVT-AV1；
> 详见 §8 与 [`hardware_scaling_validation.md`](hardware_scaling_validation.md)。

## 8. 实测验证结果（2026-11-07）

验证矩阵与长测已完成，详见
[`hardware_scaling_validation.md`](hardware_scaling_validation.md)。

关键结论：

- **NVENC**：24 次 bilinear 尺寸矩阵全部通过；4K30/4K60、8/10-bit、
  4:2:2 到 4:2:0 均正常；算法矩阵进一步显示 bicubic/spline/lanczos3
  明显优于 bilinear；
- **QSV**：bilinear 功能通过但画质明显下降（相对高质量参考 VMAF 70–86）；
  **bicubic/lanczos3 可提升到 92–96**，算法层面有可用性，但长测仍是
  bilinear，需补 bicubic 长测后再判断；
- 长测 8.9 分钟 4K60→1080p：NVENC 516.4 s、QSV 601.4 s，均完成，
  帧数 32,002、时长 533.9 s、解码 PASS；QSV 10 秒样本 VMAF 78.30，
  NVENC 94.37；
- QSV 输出可能出现 VFR 时间戳，产品集成前必须处理时间线/容器语义；
- 本轮未覆盖 HDR；算法矩阵覆盖 bilinear/bicubic/spline16/36/64/lanczos3/4
  （NVENC）和 bilinear/bicubic/spline36/lanczos3（QSV）。

## 9. 产品实现（2026-11-07）

已落地：

- CLI：
  - `--resize WxH`（精确分辨率，不修正长宽比）与 `--scale FACTOR`（长边倍率，保持长宽比），显式 opt-in；
  - `--resize-algo`，默认 **lanczos**（仅 bilinear/bicubic/lanczos）；
- 后端命令：
  - NVENC：`--vpp-resize algo=<algo> --output-res WxH`；
  - QSV：额外 `--vpp-resize-mode quality`；
- 两后端 `supports_resize` 预检；若工具不声明 `--vpp-resize` /
  `--output-res`，直接拒绝，不生成未缩放文件；
- x265/SVT-AV1 走 FFmpeg 硬件解码缩放（`scale_cuda` / `vpp_qsv`；无硬件解码时
  使用 CPU 缩放）；
- `--repair`、`--channel-sync-transparent` 明确拒绝缩放；
- 缩放参数贯穿：
  - 经典硬件路径；
  - Sony 保留管线；
  - DJI 保留管线；
  - 硬件降级 ladder（格式降级时仍保持目标尺寸）；
- 保留验证：
  - Sony `validate.compare` / DJI `stream_facts` / full selfcheck 接受
    `expected_video_size`，把目标尺寸而不是源尺寸作为期望值；
  - 因此 Sony/DJI 缩放输出仍可通过 37/37 结构检查；
- `--check full` 的 PSNR/SSIM 源-成品抽样在分辨率不同时显式 SKIP
  （该抽样不适用于缩放输出），不改用虚假指标。

实机验证：

- NVENC/QSV 产品命令均成功输出 1920×1080；
- Sony C1515 139s 4K60→1080p：**37/37 PRESERVED**，rtmd 保留；
- 默认不缩放路径的命令与旧行为一致（无 vpp/output-res 参数）。

