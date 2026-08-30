# SVT-AV1 归档可行性 — 一手来源调研记录（primary source findings）

> 抓取时间：2026-08（会话内）
> 抓取方式：GitLab API（project id 24327400, master 分支）+ 官方文档 raw 文件
> 说明：本文记录从官方仓库源码与文档直接核实的事实，供归档报告引用。非网络二手信息。

## 1. 版本与发布

- 官方 GitLab 最新 tag：**v4.2.0**（2026-07-14），往前 v4.1.0(2026-03-23)、v4.0.1、v4.0.0(2026-01-13/24)、v3.1.x、v3.0.x。
- 官方 Releases 页面（GitLab `/releases`）**所有版本均无二进制附件（assets: NONE）**，即官方不提供 Windows exe / 预编译二进制，需 ffmpeg 内置或第三方构建。来源：https://gitlab.com/api/v4/projects/24327400/releases

## 2. 心理视觉特性整合（work item #2269）

官方 work item **#2269 "Psychovisual Feature Implementations"**（state=**opened**，2025-05-20 创建，最后更新 2026-02-14）作为 SVT-AV1-PSY 特性回迁主线的 meta-issue。
来源：https://gitlab.com/AOMediaCodec/SVT-AV1/-/work_items/2269

### 已合并进主线（v3.0 → v4.x，[x] 项）
- Variance boost + variance octile（MR !2195）
- 可选 varboost 曲线（MR !2357）
- Tune 4（静态图，原"Still Picture"，MR !2489）
- `--sharpness`（MR !2346）
- `--qp-scale-compress-strength`（MR !2461）
- `--enable-dlf 2`（MR !2468）
- `--frame-luma-bias` → 现 `--luminance-qp-bias`（MR !2348）
- Adaptive film grain synthesis（MR !2347、!2496）
- 时域滤波强度控制（adaptive tf / tf-strength，MR !2352）
- Chroma QM min/max 控制（MR !2442）
- 奇数宽高支持（MR !2350）、降低最小宽高（MR !2356）
- AC energy bias（"psy-rd"，MR !2513）
- 小数 CRF 步进（MR !2503）、CRF 范围扩展到 70（MR !2522）
- 增强屏幕内容检测 `--scm 3`（MR !2494）

### 尚未合并（未完成项）
- Transform type bias（"spy-rd"）—— 未合并
- SVT-AV1-PSYEX：`complex-hvs` CLI 开关 —— 未合并
- SVT-AV1-HDR：PQ 优化 varboost 曲线、Tune Grain —— 未合并；不同时域层 AC bias 权重（MR !2513 open）
- SVT-AV1-Essential：场景检测、FFMS2 集成 —— 未合并

### 结论
PSY 的**绝大多数心理视觉特性已在 v3.0~v4.x 期间并入主线**（tune 0 的感知质量、AC bias/psy-rd、variance boost、自适应 grain 等）。剩余未合并的是少数边缘特性（spy-rd 变换类型偏置、HDR 专用曲线、tune grain、场景检测）。对"归档"影响最大的是：AC bias（可保留纹理/颗粒）、variance boost、tune 0/VQ 与新增 tune 5=VMAF。

## 3. 4:0:0（单色）与 12-bit 支持现状（源码核实）

- `Source/Lib/Codec/definitions.h`（原 EbDefinitions.h，第 873-877 行）的注释是 **AV1 规范（Profile 定义）**，不是编码器能力：
  - Profile 0：8-bit 和 10-bit 4:2:0 和 **4:0:0** only
  - Profile 1：8/10-bit 4:4:4
  - Profile 2：8/10-bit 4:2:2；**12-bit** 4:0:0、4:2:2、4:4:4
- 但 **编码器实际校验（`Source/Lib/Globals/enc_settings.c` 第 454-468 行）** 明确限制：
  - `SVT_EFFECTIVE_BIT_DEPTH != 8 && != 10` → 报错 "Encoder Bit Depth shall be only 8 or 10"
  - `encoder_color_format != EB_YUV420` → 报错 "Only support 420 now"
- 即：**主线上编码器仍仅支持 8/10-bit yuv420**。`--color-format` 虽能解析 `mono`/`400`（EB_YUV400），但会被校验拒绝。
- 佐证 issue：
  - **#1463 "Support for 4:0:0"**（opened，2020-09-02，最后更新 2023-01-18）：用于灰度视频和 AVIF alpha，尚未实现。https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/1463
  - **#2153 "Loading 12-Bit images"**（opened，2024-02-14）：12-bit 输入加载问题，尚未实现。https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/2153

## 4. film-grain 官方说明（v4.2.0 文档）

- `--film-grain` 范围 0-50，默认 0（关）。来源：Parameters.md
- `--film-grain-denoise` 范围 0/1，**默认 0**，语义："0 = 不 denoise 源，仅在帧头写入 grain 参数；1 = 按 film-grain 参数强度对源做 denoise"。来源：Parameters.md
  - 注意：旧版 CommonQuestions（编译于 v2.2.0）描述的是"总是先 denoise"的经典流程，与新版默认（denoise=0）有出入，以 v4.2.0 Parameters.md 为准。
- 源码警告（enc_settings.c 759-763）：**preset > 6 时用 film-grain 会产生显著计算开销**（"significant compute overhead"），仅建议调试用。
- 算法：Wiener 去噪 → 平块噪声模型估计（AR 模型）→ 帧头信令参数 → 解码端合成。参考帧不含 grain（grain 在输出前加回），以提升压缩效率。来源：Appendix-Film-Grain-Synthesis.md
- `--adaptive-film-grain` 默认开启（1），按分辨率自适应 grain 块大小，改善 grain 一致性。

## 5. 官方对 keyint/GOP、preset、10bit、grain 的推荐（归档相关）

来源：官方 Docs/Ffmpeg.md、CommonQuestions.md、Parameters.md、user guide

- **preset**：官方"personal use / HTPC"示例用 **preset 5**；"presets 4-6 是质量与时间的好平衡"；VOD 示例用 **preset 2**（最高效率）。CommonQuestions：preset 1-3 极高效率（时间不重要时），4-6 家用平衡，7-13 快速/实时。
- **CRF**：1080p 起点 **crf=30**（Ffmpeg.md）；官方个人使用示例 crf=32；VOD 示例 crf=25。CRF 范围 1-70（比 x264/x265 数值高才等价）。
- **keyint/GOP**：VOD 常用 ~1 秒（如 24fps→keyint 24）；家用偏好 5-10 秒；hobbyist 经验法则 = 帧率×10 且 ≤300；`--keyint -2` 默认约 5 秒。**keyint 建议取 mini-gop 大小（默认 16）的倍数 +1**，避免破坏 mini-GOP。`--keyint -1` = 无限（仅 CRF），利于效率但 seek 变慢。GOP 越大效率越高但 seek/容错越差。
- **10bit**：官方推荐 10bit 编码（yuv420p10le）——更少色带/伪影、更准确，码率增量很小；8bit→10bit 可提升保真；SVT-AV1 的 10bit 编码性能损失很小（除 preset 11-13）。
- **film-grain 值**（官方，Ffmpeg.md）：正常噪点实拍 **8**；噪点很多 **10-15**；2D 动画 **4-6**（旧文档说 4）；有颗粒动画可到 10。无噪点源可不启用。
- **tune**：tune=0（VQ）主观质量、更锐利、高心理视觉保真；tune=1（PSNR）客观指标。

## 6. 官方归档相关示例命令（Ffmpeg.md 原文）

个人使用/媒体库：
```
ffmpeg -i infile.mkv -c:v libsvtav1 -preset 5 -crf 32 -g 240 -pix_fmt yuv420p10le -svtav1-params tune=0:film-grain=8 -c:a copy outfile.mkv
```
VOD（单场景）：
```
ffmpeg -i infile.mkv -c:v libsvtav1 -preset 2 -crf 25 -g 24 -pix_fmt yuv420p10le -svtav1-params tune=0:film-grain=8 -c:a copy outfile.mkv
```

## 7. 内存/线程官方说明

来源：System-Requirements.md、Parameters.md 附录 A.1、CommonQuestions.md

- 内存主要取决于 `--lp`（level of parallelism）、分辨率、位深、`--lookahead`、`--hierarchical-levels`；RAM 不足会在编码前报错（System-Requirements.md）。
- `--lp` 越高线程与并行画面越多 → fps 越高、内存越大。CRF 模式下 lp≥4 会额外并行多个 mini-GOP，速度更高但内存显著增加（Parameters.md）。
- 默认 `--lp 0` = 按机器核数自动选择。
- 官方：1080p、preset 4-6 默认配置下，约 **16 核** 是高效利用甜点，更高核数增量收益下降；分辨率越高线程能力越强；preset 0-3 依赖性强、并行 CPU 利用率较低（CommonQuestions）。
- 8K/16K 仍是 WIP（enc_settings.c 有警告）。

## 8. 其它源码核实细节

- `--hbd-mds`（4.2.0 新增，high bit depth mode decision）：-1 默认/0 全 8bit MD/1 全 10bit MD/2 混合；仅 10bit 输入可用（8bit 下 1/2 会报错）。对 10bit 归档画质相关。
- `--max-tx-size 32`：限制 64pt 变换，避免高频细节模糊（Parameters.md 附录 B 明确说明 64pt 变换会抹掉高频噪点状纹理，PSNR/SSIM 不易察觉）。
- `--ac-bias`（psy-rd）默认 0.0，中值 1.0-1.5 保纹理/复杂运动锐度，高值 4-6 配合关 TF/CDEF 可大幅改善颗粒/噪点保留。
- `--enable-qm` 默认 0；qm-min/qm-max（0-15，15 全平坦）；低 qm level 通常降低码率、画质略降，低 CRF 下码率降低更明显（Parameters.md）。
