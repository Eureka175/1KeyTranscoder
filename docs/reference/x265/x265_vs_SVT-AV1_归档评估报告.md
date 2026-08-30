# x265 (HEVC) vs SVT-AV1 等编码器：权威基准证据报告
**用途：生产级视频归档（质量优先）选型评估** · 基于 MSU 2022–2025、Netflix VMAF、Streaming Media 2024 及第三方实测

---

## 〇、一句话结论

- **码率效率（同等质量下谁更省码率）**：在 MSU 2022–2025 全部客观指标（VMAF/PSNR/SSIM）下，**SVT-AV1 稳定优于 x265，约节省 35%–49% 码率**；VVC（VVenC/Tencent266）更优；x265 被官方用作"基准 100"，但几乎从未进入慢速/中速档前三名，只稳定地排在第 3 名左右（Fast 档）。
- **4K 高码率/近透明归档**：**没有发现任何一项权威测试显示 x265 在 4K 高码率下质量反超 SVT-AV1**。SVT-AV1 用更少码率即可达到同一 VMAF；但两者都达到"透明"（VMAF≈95）后，肉眼差异趋近于零——此时真正的分水岭是**编码/解码复杂度、硬件解码覆盖率和长期可解码性**，而这些是 HEVC/x265 的强项。
- **归档建议**：若"每 GB 存储费 + 可接受的离线编码时长"是主约束，选 **SVT-AV1（10-bit）**；若"长期兼容性/硬解覆盖/编码吞吐"是主约束，选 **x265（10-bit，veryslow/slower 档）** 仍完全合理。两者在近透明质量下都能满足归档，差异主要在成本与生态，而非画面质量。

---

## 一、MSU 2022–2025：x265 的排名与码率效率（研究问题 1）

### 1.1 关键前提：MSU 如何计分（务必先理解，避免误读）

- MSU 采用 **BSQ-rate**（增强版 BD-rate，见 Zvezdakova et al. 2020）计分：以 **Reference x265 = 100 为基准**，数值**越低越好**，表示"达到同等质量所需的码率占 x265 码率的百分比"。
  - 例：SVT-AV1 得分 51.4 → 只需 x265 约 **51.4%** 的码率即达到同等质量，即**节省约 48.6% 码率**。
  - 反向验证：x264 得分 204.8 → 需要 x265 约 2.05 倍的码率，即 x265 比 x264 省约 51%（符合业界"HEVC 比 AVC 省约 50%"的常识）。
- 报告原文："*The BSQ-rate compares the area under the quality-bitrate curve of a codec to that of a reference codec (Reference x265).*" → 这是**全码率区间的面积平均**，不是单纯高码率点。
- 排名只按质量计分，**不含编码速度**；得分相差 <1% 视为并列（主观分 <5% 并列）。

### 1.2 MSU 2023-2024 第 4 部分（4K 10-bit）——归档最相关

**来源**：https://www.compression.ru/video/codec_comparison/2023/4k_report.html
**测试条件**：4K 10-bit，15 条序列（付费版；免费版 1 条），CPU Intel Core i7-12700K，Slow=1fps / Medium=5fps / Fast=30fps。

| 编码器（Slow, 1fps） | YUV-SSIM | YUV-PSNR | Y-VMAF | Y-VMAF-NEG |
|---|---|---|---|---|
| Tencent266 v0.3.0 (VVC) | 39.3 | 42.4 | 32.3 | 43.9 |
| Tencent TXAV1 (AV1) | 46.4 | 50.3 | 39.4 | 52.5 |
| **SVT-AV1 v1.8.0 (AV1)** | **51.4** | **54.6** | **64.8** | **64.7** |
| aom v3.8.0 (AV1) | 59.0 | 63.9 | 79.7 | 78.7 |
| VVenC v1.12.0 (VVC) | 59.4 | 64.3 | 81.5 | 80.0 |
| Tencent V265 (HEVC) | 63.6 | 65.0 | 47.8 | 63.1 |
| **Reference x265 3.5+1（基准）** | **100** | **100** | **100** | **100** |
| SVT-HEVC v1.5.1 | 135.3 | 116.1 | 139.5 | 137.7 |
| x264 0.164.x | 204.8 | 212.2 | 223.1 | 222.6 |

**读数**：4K 10-bit 慢速档，SVT-AV1 只需 x265 的 **51%–65% 码率**（省 **35%–49%**）。VVC 系（Tencent266/Tencent TVC/VVenC）更强；x264 需 2 倍以上码率。

### 1.3 MSU 2025 第 2/3 部分（FullHD Objective & Subjective）

**来源**：https://www.compression.ru/video/codec_comparison/2025/main_report.html
**测试条件**：FullHD 1080p，50+ 条序列（付费版），Slow/Medium/Fast 三档。

| 编码器（Slow, 1fps） | YUV-SSIM | YUV-PSNR | Y-VMAF | Y-VMAF-NEG | YUV-Subjective* |
|---|---|---|---|---|---|
| SVT-AV1 | 53.3 | 60.9 | 63.4 | 65.2 | 125.4 |
| VVenC | 52.1 | 63.4 | 60.2 | 60.4 | 100.0 |
| aom | 55.2 | 63.2 | 68.4 | 69.0 | 129.6 |
| Tencent V265 | 56.5 | 61.8 | 45.9 | 60.1 | 110.8 |
| **Reference x265（基准）** | **100** | **100** | **100** | **100** | **201.5** |
| rav1e | 103.6 | 107.7 | 127.7 | 123.2 | — |
| SVT-HEVC | 137.7 | 134.2 | 146.3 | 145.9 | 289.3 |
| x264 | 261.6 | 270.3 | 230.5 | 247.5 | — |

\* 主观分以 **VVenC=100** 为基准（四项客观指标仍以 x265=100 为基准）。主观上 x265 需 VVenC 约 2 倍码率（201.5），SVT-AV1 约 1.25 倍（125.4）。

**读数**：FullHD 与 4K 结论一致——SVT-AV1 比 x265 省约 **35%–47%** 码率；VVC 与强 AV1 编码器继续领跑；x264/rav1e/SVT-HEVC 均明显弱于 x265。

### 1.4 MSU 2022 第 6 部分（4K）——奖牌排名

**来源**：https://www.compression.ru/video/codec_comparison/2022/4k_report.html
（免费版只给每项指标每档的**前三名**，无完整分值。）

- 五项指标（YUV-SSIM / YUV-PSNR / YUV-VMAF / Y-VMAF / Y-VMAF-NEG）的 **Slow、Medium 档**，前三名全部被 VVC（Tencent266/Tencent TVC/VVenC/TencentAVS3）占据，**x265 未进前三**。
- 五项指标的 **Fast（30fps）档**，前三名稳定为：**1st Tencent TXAV1、2nd SVT-AV1、3rd Reference x265**。
- 即：**在 2022 4K 中 x265 仅在 Fast 档拿到第 3 名，慢速/中速档排不进前三**；SVT-AV1 稳定在其之前。

### 1.5 排名结论（Q1 直接回答）

- **x265 在 MSU 2022–2025 中的定位**：官方"基准 100"，是**中游偏上**的软件编码器——稳定优于 x264（省约 50%–60%）、SVT-HEVC（MSU 测到 SVT-HEVC 反比 x265 差 16%–46%）、rav1e、kvazaar、uvg266；但**明显落后于 SVT-AV1（省 35%–49%）、aom、以及全部 VVC 编码器（Tencent266、VVenC、BILIVVC 等）**。
- **SVT-AV1 相对 x265 的码率效率**（跨 2023-2024 4K 与 2025 FullHD，客观指标）：**约省 35%–49% 码率**，VMAF 口径约省 **35%–37%**，SSIM/PSNR 口径约省 **39%–49%**。
- **主观口径差距更小**：2025 FullHD 主观分中 SVT-AV1(125.4) 相对 x265(201.5) 仅省约 38%，且主观上 VVC（VVenC=100）才是第一——说明客观指标会放大 AV1 优势，肉眼差异略小。

> ⚠️ 一致性说明：上述 BSQ-rate 是**全码率区间平均**，主要体现中低码率优势。见第三节对高码率段的专门讨论。

---

## 二、高码率 / 近透明 4K 归档：SVT-AV1 是否赢 x265（研究问题 2）

**直接结论：在可查的权威测试中，没有任何一项显示 x265 在 4K 高码率下质量反超 SVT-AV1；SVT-AV1 用更少码率即可达到同一目标质量。但"近透明"区间二者都无可见伪影，边际差距缩小。**

证据与限定：

1. **MSU 4K 10-bit（BSQ-rate）**：SVT-AV1 需 x265 51%–65% 码率达到同等 VMAF/SSIM/PSNR。这是**全码率平均**，但即便只看"高质量预设 + 4K 10-bit"这一最接近归档的条件，结论仍是 SVT-AV1 更省码率。
2. **第三方实测（Fora Soft，BD-rate，VMAF 0.6.1，1080p，锚点=x264 medium）**：x265 slow = −44%、x265 veryslow = −46%；SVT-AV1 p6 = −55%、p4 = −57%、p2 = −58%。原文明确："*x265 slow (−44% at 9fps) is beaten by SVT-AV1 preset 6 (−55% at 11fps), which is more efficient AND faster.*" 即**在最慢、质量最高的预设下，SVT-AV1 仍领先 x265 约 11–13 个百分点**。
3. **高码率饱和的诚实边界**：码率越高，各编码器越接近"透明"（VMAF 饱和趋近 100），效率差距自然收窄。因此在"两者都透明"的近透明区，**画面质量上分不出高下**，差异转化为：达到透明所需码率（AV1 更低）、编码耗时（AV1 最高预设更慢）、解码生态（HEVC 更稳）。**把"近透明时 x265 更优"当作事实是不成立的，也没有来源支持。**

---

## 三、Netflix VMAF 与"透明"质量（研究问题 3）

- **VMAF 量表**：0–100，越高越好，100 = 与源在给定观看条件下不可区分。Netflix 文档把主观 AC 评分映射到 VMAF 尺度："bad"≈20、"excellent"≈100（即 0–100 近似线性对应主观感知）。
- **质量分带（行业共识，多来源一致）**：
  - **90–100 = 优秀（Excellent）/ 近透明**；80–90 = 良好；70–80 = 尚可到良好；60 以下多数观众感到明显压缩；50 以下差；20 以下严重失真。
  - **VMAF ≈ 93–95 被普遍作为"透明阈值"**：约 95 分时，多数观众在并排对比下也无法区分编码与源。这是 mezzanine/母带级 QC 的常用目标。
- **Netflix 的"可察觉差异"判据**：**约 6 个 VMAF 分**是多数观众开始察觉质量变化的阈值；**<2 分基本属噪声、可忽略**。
- **Netflix 的 4K 模型**：在 "VMAF: The Journey Continues" 中新增 **4K VMAF 模型**（4K 电视、观看距离 1.5H，即能感知 4K 细节的最远距离），说明其 4K 质量评估不能直接套用 1080p 默认模型。
- **关于"CRF/码率 ↔ VMAF 95+"**：Netflix **不以 CRF 而是以 VMAF 目标做 per-title 码率阶梯**，未发布"某 CRF = 透明"的通用表——因为 VMAF 与码率的对应**强依赖内容**（胶片颗粒、运动、分辨率、是否 10-bit/HDR）。因此无法给出一个放之四海皆准的"CRF 值"；但可确定的是：**在同等目标 VMAF（如 95）下，4K 10-bit 场景中 SVT-AV1 所需码率显著低于 x265**（对应第一节的 35%–49% 差距）。
- **直接来源**：Netflix 博客 https://netflixtechblog.com/vmaf-the-journey-continues-44b51ee9ed12 （原始页被 Cloudflare 拦截，镜像：readkong.com/page/vmaf-the-journey-continues-9365425）；配套解读 https://www.forasoft.com/learn/video-quality/articles-vqm/vmaf-explained 、https://liveapi.com/blog/vmaf/ 、https://streaminglearningcenter.com/encoding/best-practices-for-netflixs-vmaf-metric.html 。

---

## 四、归档场景的行业共识 + 复杂度与硬解（研究问题 4）

### 4.1 效率共识
- **共识（多来源一致）**：质量优先的离线/归档编码，若只论"达到某一 VMAF/SSIM 所需码率"，**SVT-AV1（10-bit，高预设）> x265（10-bit）**，且 **VVC（VVenC/Tencent266）> SVT-AV1**。AV1 优势主要在中低码率，高码率段缩小，但未见反超。

### 4.2 编码/解码复杂度（实测，1080p，Fora Soft）
| 配置 | BD-rate（vs x264 medium） | 编码速度 |
|---|---|---|
| x265 medium | −38% | 22 fps |
| x265 slow | −44% | 9 fps |
| x265 veryslow | −46% | 3 fps |
| SVT-AV1 p8 | −51% | 30 fps |
| SVT-AV1 p6 | −55% | 11 fps |
| SVT-AV1 p4 | −57% | 4.5 fps |
| SVT-AV1 p2 | −58% | 1.2 fps |
| 硬件 AV1（NVENC） | ≈−40% | ≈100× 软件慢速 |

- **读点**：SVT-AV1 p6 已经**既更快又更省码率**于 x265 slow；但 SVT-AV1 冲顶（p2）时比 x265 veryslow 还慢约 2.5 倍（1.2 vs 3 fps）。对一次性离线归档，编码时长通常可接受。
- **解码复杂度**：AV1 软解对老设备较重（耗电、发热）；HEVC 软解更轻、且硬解普及率高得多。

### 4.3 硬件解码覆盖率（归档长期可读性的关键）
- **HEVC/x265**：硬件解码**近乎普及**——2014 年后主流 GPU、2015 年后绝大多数电视/机顶盒、2017 年后几乎所有手机 SoC 均支持 8/10-bit HEVC 硬解。
- **AV1**：硬解只覆盖较新设备。**Apple 直到 2023 年才在 iPhone 15 Pro/Pro Max 加入 AV1 硬解**（Streaming Media 2024 确认）；此前 iOS 全线仅软解。大量存量设备（旧手机、旧电视盒子、老显卡）**能硬解 HEVC 但不能硬解 AV1**。
- **生态冷数据**：Streaming Media 2024 引用 Bitmovin 开发者报告，AV1 实际使用率不升反降（在用 14%→8%，计划 42%→32%）；生产分发中 **H.264 仍占绝对主导、HEVC 次之**，AV1/VVC 部署谨慎。
- **归档含义**：AV1 压缩率更高，但"20 年后随手找台设备都能硬解"的把握，**HEVC 明显更强**。

### 4.4 归档选型建议（质量优先）
1. **若目标 = 最小存储占用、可接受离线慢编码** → **SVT-AV1（10-bit，preset 2–4，含 film-grain 决策）**，效率最优；建议同时保留可硬解的交付副本。
2. **若目标 = 长期兼容 + 编码吞吐 + 硬解把握** → **x265（10-bit，slower/veryslow）**，质量近透明、生态最稳，效率劣势（多 35%–49% 码率）由存储/算力成本权衡。
3. **折中**：归档母本用 x265 10-bit 高预设保证兼容；派生交付/长期冷存储副本用 SVT-AV1 10-bit 压缩。
4. 无论选哪个，归档场景务必 **10-bit（防 banding）**、用 **慢预设**、并**实测目标内容的 VMAF（用对应分辨率模型）≥ 93–95** 作为验收线，而非凭 CRF 拍脑袋。

---

## 五、共识 vs 单项研究（诚实分级）

| 结论 | 置信度 | 依据类型 |
|---|---|---|
| SVT-AV1 码率效率 > x265（省 35%–49%） | **强共识** | MSU 2023-24 4K、MSU 2025 FullHD 官方数据 + Fora Soft 独立实测，方向一致 |
| VVC（VVenC/Tencent266）> SVT-AV1 > x265 | **强共识** | 上述同一批 MSU 数据 |
| x265 > x264（省约 50%）、> SVT-HEVC/rav1e | **强共识** | MSU 数据 |
| 4K 高码率下 x265 反超 SVT-AV1 | **无来源支持（不成立）** | 未找到任何权威测试支持；属误传 |
| VMAF 93–95 = 透明阈值；JND ≈ 6 分 | **中等共识**（次级来源归纳 Netflix 文档） | Fora Soft / LiveAPI / SLC 对 Netflix 文档的转述 |
| Netflix 4K VMAF 模型（1.5H） | **单一权威来源** | Netflix 博客原文 |
| 硬解：HEVC 普及 >> AV1 | **强共识** | Streaming Media 2024（含 Apple 2023 时间点）+ 业界常识 |
| AV1 生态采用率下滑（14%→8%） | **单一调查来源** | Bitmovin 报告，经 Streaming Media 2024 转引 |

---

## 六、来源清单

1. MSU Video Codecs Comparison 2023-2024 Part 4（4K 10-bit）：https://www.compression.ru/video/codec_comparison/2023/4k_report.html
2. MSU Video Codecs Comparison 2025 Part 2/3（FullHD Objective & Subjective）：https://www.compression.ru/video/codec_comparison/2025/main_report.html
3. MSU 2023-2024 Part 1/2（FullHD）：https://www.compression.ru/video/codec_comparison/2023/main_report.html
4. MSU 2022 Part 6（4K）：https://www.compression.ru/video/codec_comparison/2022/4k_report.html
5. MSU 2022 Part 4（FullHD）：https://www.compression.ru/video/codec_comparison/2022/main_report.html
6. The State of Video Codecs 2024（Streaming Media，Jan Ozer）：https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/The-State-of-Video-Codecs-2024-163422.aspx
7. Netflix：VMAF: The Journey Continues：https://netflixtechblog.com/vmaf-the-journey-continues-44b51ee9ed12
8. Fora Soft：x264 vs x265 vs SVT-AV1 vs Hardware（BD-rate 实测）：https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1
9. Fora Soft：VMAF Explained（量表与 JND）：https://www.forasoft.com/learn/video-quality/articles-vqm/vmaf-explained
10. LiveAPI：What Is VMAF（透明阈值 93–95）：https://liveapi.com/blog/vmaf/
11. Streaming Learning Center：Best Practices for Netflix's VMAF Metric：https://streaminglearningcenter.com/encoding/best-practices-for-netflixs-vmaf-metric.html
12. BSQ-rate 方法论文：A. Zvezdakova et al., "BSQ-rate: a new approach for video-codec performance comparison…", 2020（MSU 报告内引用）

> 抓取说明：compression.ru 需带浏览器 User-Agent 访问（否则 406）；netflixtechblog.com 被 Cloudflare 拦截，正文经 readkong.com 镜像核对。MSU 完整分值（Enterprise 版）为付费内容，本报告数据取自免费版页面内嵌的 BSQ-rate JSON 与前三名奖牌表，数字可复核。
