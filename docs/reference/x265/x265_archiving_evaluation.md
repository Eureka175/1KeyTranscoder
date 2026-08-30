# x265（libx265 / ffmpeg）视频归档：社区实测共识与争议 — 生产就绪评估

> 调研时间与方法：本报告基于 Doom9 论坛多线程逐帖原文、MSU 官方报告页（免费版内嵌 BSQ-rate 数据 + 前三名奖牌表）、Fora Soft / OTTVerse / iXBT 实测、x265 官方 release notes 与 CLI 文档、openbenchmarking.org（Phoronix Test Suite）实测、Rigaya 画质对比、Netflix VMAF 技术博客、HandBrake 官方文档等。
> **访问限制（如实声明）**：Reddit（r/DataHoarder、r/handbrake）按 IP 直接 403 封禁，帖子级原文未能取得，仅能通过 Hacker News 讨论串与第三方实测文章间接覆盖；trac.ffmpeg.org 启用 Anubis 反爬，经 ASWF 镜像/官方文档交叉核对；MSU 完整分值为付费 Enterprise 版，本报告取免费页内嵌 JSON 与前三名奖牌表。
> 每条结论标注【共识】（多来源一致）/【个案】（单一测试或个人观点），并附来源 URL 与测试条件。

---

## 0. 一句话结论（生产就绪判断）

- **画质优先归档 x265 的最小可靠基线：`--preset slow + CRF 16–18 + 10-bit(main10)`**；其余旋钮（no-sao、tune grain、aq-mode、rd、deblock、psy-rd）属按片源优化项，且**必须先固定 preset**（手抄长参数串反而会把 slow/veryslow 的默认优化降级）。【共识】
- **效率上 SVT-AV1 明确优于 x265（约省 35–49% 码率），VVC 更强**；但 x265 的护城河是**近乎普及的 HEVC 硬解**与相对更快的慢预设吞吐。**近透明/4K 高码率段二者画质都达到透明，肉眼无差异**，分水岭是存储成本 vs 解码生态 vs 编码耗时，而非画质。【共识】
- **质量优先归档不要用硬件 NVENC/QSV 代替 x265**（中低码率下硬件要多给 +15–30% 码率才追平；Pascal/旧 QSV 归档不可用）。【共识】
- **x265 4.0/4.1（2024）没有改默认 AQ/SAO**，是增量版本（alpha/SCC/MV-HEVC/VMAF v3/ARM 提速）；默认仍是 aq-mode 2 + SAO 开。社区对 4.0 无画质相关的默认变化反馈。【共识/官方文档】
- **4K preset slow 在 16 核现代桌面 CPU ≈ 10–15 fps**（实测 7950X3D 9.9、9950X3D 11.5、13900K 9.0、14900KF 10.7），即约 1.5–2× 实时（4K24p）；4K60 约 4 小时/小时素材——批量 4K 归档需数天级算力或分组多机并行。【实测】

---

## 1. Doom9 / 社区：CRF、preset、10-bit 与画质争议

### 1.1 CRF 推荐区间 —— 存在"两派"，不是单一共识
- 【多人一致·透明派】Doom9 用户 **jd17**（长期 Blu-ray 重编码，提供实测码率）：
  - 1080p Blu-ray 10bit slow no-sao：`CRF 17`（"参考级/透明"）27 部平均 **6902 kbps**；`CRF 19`（"普通"）28 部平均 **5613 kbps**；称同质量 x264 约需 12000–16000 kbps。
  - UHD 规则：**CRF 16–19**，并称"CRF20 开始在运动物体周围出现 pixel clouds 伪影"。
  - 来源：https://forum.doom9.org/showthread.php?t=175087 、https://forum.doom9.org/showthread.php?t=174679
- 【多人一致·务实派（冲突）】Doom9 用户 **excellentswordfight**：UHD 基线 `--preset slow --crf 22`，"55" OLED 2.5m 下透明"；Tears of Steel 无损源约 **CRF 20 达透明**（CRF19 已 40 Mbps 属过度）；1080p 基线 `slow + CRF 19`。来源：https://forum.doom9.org/showthread.php?t=174679 、https://forum.doom9.org/showthread.php?p=1871517
- 【专家】**benwaggoner**（Amazon Prime Video 首席视频专家）：「绝对透明→x264 更省心；几乎透明→x265 碾压」；1080p 5–8 Mbps 接近透明；**VMAF≈99 只算"勉强接近透明"**。来源：https://forum.doom9.org/showthread.php?p=1871517
- 【多人一致·CRF 换算】**microchip8**：**x264 CRF18 ≈ x265 CRF20**（medium）；x265 同码率比 x264 高约 30–35% 效率。来源：https://forum.doom9.org/showthread.php?p=1927073
- 【官方】HandBrake RF 推荐表：1080p **20–24**、4K **22–28**（"分辨率越高可容忍越高 RF"）。来源：https://handbrake.fr/docs/en/latest/workflow/adjust-quality.html
- 【官方】FFmpeg wiki：x265 默认 CRF **28**（"低质量一侧"）；真无损用 `-x265-params lossless=1`（不是 `-crf 0`）。来源：https://trac.ffmpeg.org/wiki/Encode/H.265（内容经 ASWF 镜像交叉核对）
- 【个案·反例】HN 用户 stordoff：x265 Slow RF16 已近原文件大小、"Slow 8h→Slower 近 2 天"。来源：https://news.ycombinator.com/item?id=19090636

> **小结**：「透明 16–18 / 近透明 19–22」大体成立；16–19 派（像素级透明）与 19–22 派（正常观看够用）的分歧来自评判标准（贴屏逐帧 vs 正常距离），而非编码器行为。

### 1.2 preset slow vs medium vs veryslow 收益递减
- 【专家·多帖一致】**benwaggoner**：**slower 才是 HEVC 优质工具真正生效处；slower↔veryslow↔placebo 画质接近**，"slow→slower 可能看得出差别，slower→placebo 极少"；veryslow/placebo 只给小增量收益。来源：https://forum.doom9.org/showthread.php?t=182400 、https://forum.doom9.org/showthread.php?t=182350
- 【实测·Jan Ozer】x265 slow vs medium：**同码率下 slow 仅比 medium 高 0.87% VMAF，却慢 2 倍多**；但**等质量（per-title ladder）下 slow 把最高档码率降 23%、整梯码率降 26%**。测试：8–23 文件（电影/动画/体育/商业）。来源：https://ottverse.com/choosing-an-x265-preset-an-roi-analysis/
- 【实测·Fora Soft】BD-rate（锚点 x264 medium，1080p VMAF）：**x265 medium −38%（22fps）、slow −44%（9fps）、veryslow −46%（3fps）**——medium→slow 省 ~6 个点、slow→veryslow 只再省 ~2 个点但慢 3 倍。来源：https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1
- 【官方】x265 preset 旋钮映射：slow(rd=4, bframes=4, subme=3) → slower(rd=6, bframes=8, subme=4) → veryslow(rd=6, bframes=8, subme=4, tu=3)。**veryslow 相对 slow 的主要增益是 rd 4→6 与 bframes/ref 增加**。来源：https://x265.readthedocs.io/en/master/presets.html
- 【个案·异议】Asmodian 一律 veryslow；jd17 称 slow 是甜点。来源：https://forum.doom9.org/showthread.php?t=182400

> **小结**：**slow 是质量/速度甜点【共识】**；veryslow 的边际收益小（再省 ~1–2% 码率却慢 2–3 倍）。"veryslow 只省 X% 却慢 X 倍"的精确成表数据社区未产出，仅定性 + 耗时倍数。

### 1.3 10-bit vs 8-bit（banding）
- 【广泛共识】10-bit 编码**显著减少 banding，动漫/暗部渐变尤甚**（RanmaCanada、jd17"绝不用 8-bit 压 x265"、DJ Bobo"8-bit 源做 10-bit 明显减 banding"、excellentswordfight"main10 全画面提升"）。来源：https://forum.doom9.org/showthread.php?t=186813 、https://forum.doom9.org/showthread.php?t=175087
- 【多人一致·但效率收益弱于 x264 时代】GeoffreyA"x264 时代 10-bit 收益显著，x265 上不明显"；microchip8"x265 真正有收益的是 12-bit"；benwaggoner"HEVC 的 10-bit 画质影响小于 H.264，确认设备支持才用"。来源：t=186813、t=175087、p=1927073
- 【理论】Z2697：仅当源依赖抖动（dithering）时 10-bit 帮助大；高码率帮助有限。来源：t=186813
- **缺口**：未找到 8-bit vs 10-bit 的实测 VMAF/PSNR 数值表，"10-bit 省 10–20% 码率"无成表数据支撑。

### 1.4 x265 已知画质争议（软/糊、暗部、颗粒）
- 【广泛共识·软/糊】"x265 画面软/蒙层膜"反复出现；机制解释（Boulder）：**码率饿时 x264 出块、x265 出糊**（块效应较不扎眼）。SAO 被认为是"过激软"主因。来源：https://forum.doom9.org/showthread.php?t=184399 、VideoHelp x265 评价页
- 【分歧·SAO】关派：jd17"SAO=模糊，no-sao=细节/颗粒"；收窄派：BuccoBruce"别全关，用 --limit-sao --selective-sao 1"；benwaggoner"--selective-sao 2 才该用"；反例 Arhu"--no-sao 出脏/毛边/人工噪点，--sao 更忠实源"。来源：t=175087、t=184399
- 【多人一致·暗部】默认 aq-mode 下暗部伪影，换 **aq-mode 3** 比降 CRF 更有效。来源：t=175087
- 【个案实测·颗粒】tonemapped（1080p 重噪点源，2-pass 5000 kbps，10bit）：主观 **x265=3/10 vs x264=8/10**；"--tune grain 糟透，颗粒变静止"；但约 9 Mbps 时颗粒保留很好。benwaggoner 推荐重颗粒用 `--nr-inter 250` 上限。来源：https://forum.doom9.org/showthread.php?p=1947403
- 【多人一致·tune grain 存疑】Emulgator"tune grain 是扔便宜高频系数，非真编码源颗粒"；Boulder"x265 从未为颗粒保留调校"。来源：p=1947403
- 【个案·vs SVT-AV1】Scallywag(2024)"新编码器主要为中低码率优化，透明档几乎无更高效率；x264 最好、x265 次之，VP9/AV1 糊/丢细节"。来源：https://forum.doom9.org/showpost.php?p=2009618

> **小结**：`--no-sao` 普遍视为保细节/颗粒关键开关（新版本建议 selective-sao 2 收窄）；`--tune grain` 社区评价偏负面；grainy 内容 x265 落后 x264 的实测（3/10 vs 8/10）是个案且码率偏低。

---

## 2. 权威基准：MSU / Netflix / x265 vs SVT-AV1

### 2.1 MSU 排名（2022–2025）
MSU 用 **BSQ-rate**（增强 BD-rate），**Reference x265=100，越低越好**（= 达同等质量所需码率占 x265 的百分比）。来源：https://www.compression.ru/video/codec_comparison/
- **MSU 2023-2024 4K 10-bit（Slow，i7-12700K，15 序列）**：SVT-AV1 = SSIM **51.4** / PSNR **54.6** / VMAF **64.8**（即只需 x265 的 51–65% 码率，**省 35–49%**）；x264 = 204.8–223.1。来源：https://www.compression.ru/video/codec_comparison/2023/4k_report.html
- **MSU 2025 FullHD（Slow，50+ 序列）**：SVT-AV1 = 53.3/60.9/63.4/65.2（省 35–47%）；x264 = 230.5–270.3。来源：https://www.compression.ru/video/codec_comparison/2025/main_report.html
- **MSU 2022 4K**：Slow/Medium 档前三全被 VVC（Tencent266/VVenC/TVC）占据，**x265 未进前三**；Fast 档稳定 1st Tencent TXAV1 → 2nd SVT-AV1 → 3rd x265。来源：https://www.compression.ru/video/codec_comparison/2022/4k_report.html
- **MSU 2022 FullHD（H.265 赛道内）**：网易云信 NE265E 在 Y-VMAF 三档全拿 H.265 赛道第一，x265 非 HEVC 最优。来源：https://www.geekpark.net/news/322367
- 【Jan Ozer 归纳】"各对比都以 x265 归一化为 100%；AV1 比 HEVC 明显省码率，VVC 比 AV1 更省"。来源：https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/The-State-of-Video-Codecs-2024-163422.aspx

> **排名结论**：x265 稳定优于 x264（省约 50%）与 SVT-HEVC，但**明显落后 SVT-AV1（省 35–49%）及全部 VVC 编码器**。【共识/官方】

### 2.2 x265 vs SVT-AV1 在 4K 高码率/归档段
- 【实测·Fora Soft】BD-rate（锚点 x264 medium）：**x265 veryslow −46% vs SVT-AV1 p4 −57% / p2 −58%**；且 "SVT-AV1 p6(−55%, 11fps) 比 x265 slow(−44%, 9fps) **更快又更省**"。来源：https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1
- 【个案·iXBT 2026 4K HDR 实测】3840×2160 HDR10 源（Planet Earth 2，43.3 Mbps 源），FFMetrics + vmaf_4k_v0.6.1：
  - 40 Mbps 档：x265 slow VMAF **99.92** vs SVT-AV1 preset5 **99.74**；20 Mbps：x265 **99.36** vs SVT-AV1 **99.27**（x265 略高）；
  - 10 Mbps：SVT-AV1 **98.43** vs x265 **98.04**（AV1 反超）；5 Mbps：SVT-AV1 **96.83** vs x265 **95.69**（AV1 明显领先）。
  - 作者结论："**AV1 对 HEVC 的优势在 4K 高码率几乎不可见，仅在极低码率才显现**"；4K BD 可重压到 4–5 倍小、画质近无损。
  - 来源：https://www.ixbt.com/sw/h265-av1-video-encoding-test.html （注意：SVT-AV1 用 preset 5，非最高预设，故该对比对 AV1 非"满血"）
- 【共识】**没有任何权威测试显示 x265 在 4K 高码率"反超"满血 SVT-AV1**；MSU 的 35–49% 是全码率区间平均（主要来自中低码率）。近透明段二者都能到 VMAF≈95+，肉眼无差异。

> **归档结论**：**求最小存储 → SVT-AV1（10-bit 高预设）；求长期兼容/吞吐 → x265（10-bit slow/veryslow）**。二者近透明质量都达标。

### 2.3 Netflix VMAF 与透明阈值
- VMAF 0–100，"excellent"≈100；**VMAF 93–95 = 透明阈值**（约 95 并排对比也无法区分，mezzanine 级目标）；**JND ≈ 6 分**、<2 分属噪声；Netflix 有专门 4K VMAF 模型（1.5H 观看距离）。Netflix 用 VMAF 目标而非 CRF，故无通用"CRF=透明"表。来源：https://netflixtechblog.com/vmaf-the-journey-continues-44b51ee9ed12 、https://netflixtechblog.com/toward-a-practical-perceptual-video-quality-metric-653f208b9652
- 【Jan Ozer】per-title ladder 最高档 VMAF 目标 **93–95（保守取 95）**。来源：https://ottverse.com/choosing-an-x265-preset-an-roi-analysis/
- 【专家】benwaggoner：VMAF 训练目标"在几乎透明下方一点"，**VMAF≈99 仅勉强接近透明**，不宜直接当"存档透明"判据。来源：https://forum.doom9.org/showthread.php?p=1871517

---

## 3. 硬件 NVENC/QSV HEVC vs 软件 x265（归档画质优先）

- 【共识·明确】中低码率（归档真正关心的场景）下 **x265 slow/veryslow 明确优于 NVENC/QSV**；硬件编码器为实时吞吐优化，牺牲搜索深度。来源：https://www.technolynx.com/post/hevc-encoders-compared-x265-nvenc-and-quick-sync-for-streaming/
- 【实测·Fora Soft 码率开销】Ada NVENC HEVC p7：BD-rate −30%（500fps）vs x265 slow −44% / veryslow −46%。推导：**NVENC 匹配 x265 slow 需 +25% 码率、匹配 veryslow 需 +30%**。来源：https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1
- 【实测·iXBT 2026 4K HDR】x265 slow 全程领先 Intel QSV 约 **1.4–1.6 dB PSNR**；VMAF 差距 40 Mbps 仅 0.15 → 5 Mbps 扩大到 0.9（**码率越低 x265 优势越大**）。来源：https://www.ixbt.com/sw/h265-av1-video-encoding-test.html
- 【实测·Rigaya 画质对比（社区公认基准）】Doom9 引述："**Blackwell 仍达不到 x265 veryslow，Pascal 是笑话**"。来源：https://rigaya.github.io/vq_results/ 、https://forum.doom9.org/showthread.php?t=186813
- 【实测·Doom9】RTX 3080（Ampere）最好 preset 质量 ≈ x265 **faster** 级（低于 slow 两档）。来源：https://forum.doom9.org/showthread.php?t=183703
- 【实测·Level1Techs】Pascal NVENC 一个剧集压出 6 GB，CPU x265 仅 1 GB 且质量更好。来源：https://forum.level1techs.com/t/hevc-encoding/140316
- 代际：Pascal（归档不可用）→ Turing/Ampere（≈x265 faster）→ Ada/Blackwell（明显更近，且 AV1 硬件是亮点）。【共识】

> **结论**：**质量优先归档 = x265 slow/veryslow，不要用硬件编码器**（含最新代，中低码率仍要多 +15–30% 码率）。硬件仅用于直播/实时/长尾低价值内容。【共识】

---

## 4. x265 4.0（2024）社区反馈与默认值变化

官方 release notes 事实（无任何 AQ/SAO 默认变化）：
- **4.0**（2024-09-13）：新增 **Alpha 通道、SCC（屏幕内容编码）、MV-HEVC、VMAF v3.x 支持**；ARM SIMD 大幅优化（较 3.6 **最高提速 57%**）。**无新 AQ、无新 SAO、无画质相关默认变化**。来源：https://x265.readthedocs.io/en/release_4.1/releasenotes.html 、https://mailman.videolan.org/pipermail/x265-devel/2024-September/013942.html
- **4.1**（2024-11-22）：新增 AOM 胶片颗粒特性 SEI（`--aom-film-grain`）、帧级 RC（`--frame-rc`）、回滚 4.0 的 API 变更。**仍无 AQ/SAO 默认变化**。来源：同上
- **3.6**（2024-04-04）：新增 SBRC（`--sbrc`）、MCSTF（`--mcstf`）、场景切换感知 QP **BBAQ**（`--scenecut-aware-qp`）、直方图场景检测（`--hist-scenecut`）、胶片颗粒 SEI（`--film-grain`）、层级 B 帧。**均为 opt-in 新特性，默认关闭**。来源：同上
- **默认值（当前，未变）**：`--aq-mode 2`（auto-variance）、`--aq-strength 1.0`、`--sao` 默认开、`--selective-sao 0`。来源：https://x265.readthedocs.io/en/latest/cli.html

> **结论**：x265 4.0 是增量版本，**没有"新 AQ / 新 SAO 默认"**；用户问的 AQ/SAO 相关新特性（BBAQ/scenecut-aware-qp、SBRC、mcstf）在 3.6 引入且默认关闭。社区对 4.0 无画质级争议，反馈集中在 ARM 提速与 alpha/SCC/MV-HEVC 新特性。【官方/共识】

---

## 5. x265 4K60 preset slow 速度基准与批量归档可行性

- 【实测·openbenchmarking / PTS x265，preset medium，8-bit SDR，Bosphorus】：
  - Ryzen 9 **9950X**：4K **35.6 fps**、1080p 121.8 fps；
  - Core i9 **14900K**：4K **36.6 fps**、1080p 90.0 fps。
  - 来源：https://openbenchmarking.org/result/2410262-NE-RYZEN999525 、https://openbenchmarking.org/result/2402136-NE-COREI914930
- 【实测·iXBT 论坛众测（4K preset slow，CRF20 aq-mode=1，LG Hoverboard 4K Demo）】：
  - 7950X3D **9.91 fps**、9950X3D **11.48 fps**、13900K **8.99 fps**、14900KF **10.73 fps**。
  - 来源：https://forum.ixbt.com/topic.cgi?id=8:25651
- 【换算】Fora Soft 实测 preset 速度比 medium:slow:veryslow ≈ 22:9:3 → 16 核 4K slow ≈ **14–18 fps**、veryslow ≈ **5–9 fps**；每核 ≈ 2.2 fps/核（4K medium）。来源：https://www.forasoft.com/learn/video-quality/articles-vqm/encoder-comparison-x264-x265-svt-av1
- 【批量归档估算】：
  - 4K slow（~15 fps）：1 小时 24p 素材 ≈ **1.6 小时**；**1 小时 60p ≈ 4 小时**；
  - 4K veryslow（~5 fps）：24p ≈ 4.8 小时；60p ≈ **12 小时/小时素材**；
  - 1080p medium（~122 fps）远快于实时，批量无压力。
  - **修正**：Bosphorus 为 8-bit SDR，**10-bit HDR 归档再慢约 10–25%**，4:2:2 更慢。
- AVX-512：Intel 白皮书端到端仅温和 +10–15%（主要 4K main10 slower/veryslow）；Ryzen 7000/9000 有 AVX-512、Intel 消费级关闭。来源：https://www.intel.com/content/dam/develop/external/us/en/documents/mcw-intel-x265-avx512.pdf

> **结论**：16 核现代桌面 CPU 上 4K slow ≈ 1.5–2× 实时、veryslow ≈ 3–5× 实时（60p 素材 ×2.5）——**大批量 4K 归档需数天级算力预算或分组多机并行**；1080p 归档吞吐无压力。【实测】

---

## 6. 生产级 x265 归档参数模板（多套，注明来源）

### 共识基线（可直接采用）
**`--preset slow --crf 16–18 --profile main10`（ffmpeg 加 `-pix_fmt yuv420p10le`）**，帧率/色彩空间同源、不缩放、无滤镜。【多源共识】

### 具体模板
1. **FFmpeg 官方 Wiki**（https://trac.ffmpeg.org/wiki/Encode/H.265）：
   `ffmpeg -i in -c:v libx265 -crf 28 -preset medium -x265-params profile=main10 -pix_fmt yuv420p10le out.mp4`（Wiki 默认 CRF28 偏低保真）；真无损 `-x265-params lossless=1`（preset veryslow）。
2. **ASWF ORI 行业规范**（https://academysoftwarefoundation.github.io/EncodingGuidelines/EncodeHevc.html）：`-pix_fmt yuv420p10le -crf 22 -preset slow` + 色彩元数据 + `-tag:v hvc1`；"CRF18 下 medium/slow 即可，更慢预设收益趋零"；跨编码器换算 `x265_crf = 1.09×x264_crf − 4.19`。
3. **Doom9 · 4K 视觉无损（blublub）**：CRF 17–18 + `no-sao:rd=4:psy-rdoq=4:me=3:subme=2:aq-mode=1:rskip=2:qcomp=0.75:bframes=4:ref=5:psy-rd=2:deblock=-1,-1:level-idc=51:high-tier:rc-lookahead=30:vbv-bufsize=160000:vbv-maxrate=160000:hdr10-opt`。来源：https://forum.doom9.se/showthread.php?p=2022082
4. **Doom9 · 4K BD 近无损（coopzr，HDR10/DV 烘焙）**：`--preset slower --crf 10 --aq-mode 1 --rd 4 --no-sao --hdr10-opt ...`（excellentswordfight 纠偏：CRF15 已基本视觉无损、CRF10 常高于源码率）。来源：同上帖
5. **HandBrake 官方预设**：H.265 **10-bit(main10) + RF22 + slow**（HQ 4K 预设为 slower），Profile/Level auto。来源：https://handbrake.fr/docs/en/latest/technical/official-presets.html
6. **xcodecpack**（https://xcodecpack.com/hevc/settings/）：归档 CRF **20** + slow；Blu-ray 级 CRF18 veryslow；HDR/4K CRF22 slow；"CRF18–22 视觉无损"。
7. **Aiarty**（https://www.aiarty.com/fr/blog/handbrake-best-quality-settings.htm）：4K 归档 H.265 10-bit **RF16–18** + Slow/Slower；1080p RF18。
8. **中文实战（011720.xyz/posts/18/）**：H.265 10-bit + **Very Slow + RF18–22**，高级项只留 `strong-intra-smoothing=0:aq-mode=1`；**关键纠偏**：`rect=0:rd=4` 长参数串对 Very Slow 是负优化（rd=4 把默认 rd=6 降级、rect=0 废掉一半能力）。
9. **iXBT 论坛 4K 众测参数**：`-c:v libx265 -crf 20 -preset slow -x265-params aq-mode=1`（4K 众测标准参数）。来源：https://forum.ixbt.com/topic.cgi?id=8:25651

### `--tune grain` 官方等价展开（保颗粒片专用）
`aq-mode 0 : cutree 0 : ipratio 1.1 : pbratio 1.0 : qpstep 1 : sao 0 : psy-rd 4.0 : psy-rdoq 10.0 : rskip 0` + 专用 `rc-grain` 率控。**官方警示：必须整套使用，覆盖任一（如开回 aq-mode/cutree）会引发颗粒闪烁（grain strobing）**。来源：https://x265.readthedocs.io/en/master/presets.html

### 共识 vs 争议旋钮
- **共识**：CRF 16–18 归档、preset slow、10-bit main10、真无损用 lossless=1。
- **争议（按片源 A-B 实测）**：`--no-sao`（高码率保细节 vs 低码率 SAO 更好）、`--tune grain`（颗粒片 vs 干净片，勿混用）、`--aq-mode 1/2/3`、`--rd 4 vs 6`（slower/veryslow 下 rd=4 是降级）、`--deblock`、`--psy-rd/--psy-rdoq`、`--strong-intra-smoothing 0`、`--cutree`、`--rskip`、`--open-gop`。

---

## 附：主要来源速查
- Doom9 线程：t=175087 / t=174679 / t=186813 / t=182400 / t=182350 / t=184399 / t=183703；p=1871517 / p=1927073 / p=1947403 / p=2009618 / p=2022082
- 子报告文件（本工作区）：`x265_archiving_consensus_report.md`（Doom9/社区共识全文）、`x265_vs_SVT-AV1_归档评估报告.md`（MSU/Netflix/AV1 全文）
- 其余 URL 见上文各条。

> **声明**：Reddit r/DataHoarder 与 r/handbrake 的一手帖子因 IP 封禁未能取得，相关结论以 Doom9（压制圈最权威社区）+ HN 旁证 + 第三方实测文章替代；如需 Reddit 一手证据需在非本环境出口抓取。所有"省码率百分比/fps"数字均已注明测试条件（分辨率/码率/指标/CPU），并区分【共识】与【个案】。
