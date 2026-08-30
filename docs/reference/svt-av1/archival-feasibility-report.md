# SVT-AV1（AOMedia 官方主线）视频归档可行性 — 事实调研报告

> 调研时间：2026-08（会话内）｜基线：官方主线 v4.2.0（2026-07-14，GitLab tag，无二进制附件）
> 方法：GitLab 仓库源码/文档 + GitLab API + web_search + 社区原始帖抓取（Doom9 / r/AV1 / r/DataHoarder / Reddit 归档镜像 / 指南博客）。
> 原始文档已落盘于 `docs/reference/svt-av1/`（official / src / speed / windows / templates / community / psy / quality 子目录，共 60+ 份）。

---

## 0. 结论速览（TL;DR）

1. **可用且成熟**：v3.0→v4.x 已将 SVT-AV1-PSY 绝大多数心理视觉特性（AC-bias/psy-rd、variance boost、自适应 film-grain、tune 0/5 等）并入主线（官方 work item #2269 仍 open，核心归档项已落地）。
2. **格式硬边界**：编码器**仍只支持 8/10-bit yuv420**（4:0:0、4:2:2、4:4:4、12-bit 均不支持编码）；源码校验 `enc_settings.c` 明确 "Only support 420 now"、"Encoder Bit Depth shall be only 8 or 10"。definitions.h 里 "Profile 0 含 4:0:0 / Profile 2 含 12-bit" 是**AV1 规范定义，非编码能力**。
3. **码率节省**：vs x265 归档档约省 **35–49%** 码率（MSU 2023-24 4K 10bit Slow：SVT-AV1 只需 x265 的 51–65% 码率）；Netflix 生产环境 AV1 比 HEVC 省约 1/3 带宽。但**近透明/高码率细节保真 x265 仍略优**。
4. **速度代价**：preset 4–6 是归档甜点；preset 6 比 4 快约 2.6–2.8×，质量损失极小。4K 10bit preset 0–6 权威 fps 表不存在，需自测。
5. **Windows 可用**：官方无 exe；gyan（ffmpeg 9.0.1）/ BtbN（pin SVT-AV1 到 2026-08-08 master 提交，≥v4.2.0）内置 libsvtav1 跟进及时；av1an v0.5.2 是 chunked 并行事实标准。
6. **归档模板共识**：10-bit + preset 4/5 + CRF 18–24 + `film-grain 8–20`(denoise=0) + keyint≈10s；重颗粒才用 FGS，细节敏感素材建议 x265。
7. **已知坑**：FGS 是"环外后处理"且解码端实现不一（非安全默认）；SVT-AV1 不插场景关键帧；`--enable-qm` 省码率但略降质；`--sharpness` 1–2 保守提效。

---

## 1. 归档相关新特性演进（v3.x → v4.x）

### 1.1 心理视觉特性整合（官方 work item #2269）
- 官方 meta-issue **#2269 "Psychovisual Feature Implementations"**（state=**opened**，2025-05-20 建，2026-02-14 更新）是 PSY 特性回迁主线追踪。来源：https://gitlab.com/AOMediaCodec/SVT-AV1/-/work_items/2269
- **已并入主线（v3.0~v4.x）**：AC energy bias（`--ac-bias`，即 psy-rd，MR !2513）、Variance boost + octile（MR !2195/!2357）、自适应 film-grain（MR !2347/!2496）、`--sharpness`（MR !2346）、`--qp-scale-compress-strength`（MR !2461）、`--enable-dlf 2`（MR !2468）、`--luminance-qp-bias`（MR !2348）、TF 强度控制（MR !2352）、Chroma QM（MR !2442）、小数 CRF/CRF 扩到 70（MR !2503/!2522）、`--scm 3`（MR !2494）。v4.2.0 新增 **tune 5=VMAF**、`--hbd-mds`。
- **尚未合并**：transform type bias（spy-rd）、PSYEX complex-hvs、SVT-AV1-HDR 的 PQ varboost/Tune Grain、场景检测（SVT-AV1-Essential）。
- **归档解读**：`--ac-bias`（保纹理/颗粒，等价 x265 psy-rd）、variance boost、自适应 grain 三项对归档最有用，已无需第三方 fork。

### 1.2 4:0:0 与 12-bit 现状（源码核实）
- **4:0:0 不支持编码**：`Source/Lib/Globals/enc_settings.c` L465-468 `if (encoder_color_format != EB_YUV420) → "Only support 420 now"`；issue **#1463 "Support for 4:0:0"**（opened 2020，未实现）。https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/1463
- **12-bit 不支持编码**：enc_settings.c L454-457 只允许 8/10-bit；issue **#2153 "Loading 12-Bit images"**（opened 2024，未解决）。社区实测 `-pix_fmt yuv420p12le` 被 ffmpeg 拒绝、静默按 10-bit 编码。https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/2153 、https://colinmckellar.com/2024/03/11/svtav1-10-bit/
- `definitions.h` L873-876 注释（"Profile 0: 8/10-bit 4:2:0 and 4:0:0；Profile 2: 12-bit..."）是 **AV1 规范 Profile 定义**，非编码器能力。

### 1.3 film-grain 官方建议（归档）
- `--film-grain` 0-50（默认 0）；`--film-grain-denoise` 0/1（**默认 0**=不 denoise 源、仅帧头信令；1=按 film-grain 强度 denoise 源）。来源：Parameters.md
- 官方取值：正常噪点 **8**；噪多 **10-15**；2D 动画 **4-6**；无噪点源可关。来源：Docs/Ffmpeg.md
- 源码警告：**preset > 6 用 film-grain 计算开销显著**（enc_settings.c L759-763）。
- 算法：Wiener 去噪→AR 噪声模型估计→帧头信令→解码端合成；参考帧不含 grain。来源：Appendix-Film-Grain-Synthesis.md

### 1.4 chunked / long-GOP / keyint
- **SVT-AV1 不在场景切换插关键帧**（`--scd` 只检测不插 KF；官方定性"非 bug"）→ 场景级切分需 av1an/第三方。来源：CommonQuestions.md
- keyint：VOD ~1s；家用 5-10s；hobbyist 经验=帧率×10（≤300）；默认 `-2`≈5s；`-1`=无限（仅 CRF）。**keyint 建议取 mini-GOP(默认16)倍数+1**。来源：CommonQuestions.md、user guide

---

## 2. 官方/权威质量对比

### 2.1 vs x265（归档档）
- **MSU 2023-24（4K 10-bit Slow 档）**：SVT-AV1 所需码率仅为 **x265 的 51–65%**（即省 35–49%）。来源（官网 main_report 直接抓取遇 502，经社区报告列为已确认基线）：https://www.compression.ru/video/codec_comparison/2023/main_report.html ；综述 https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/The-State-of-Video-Codecs-2024-163422.aspx
- **iXBT 2026（4K HDR 近透明档）**：x265 slow 在 40/20 Mbps 的 VMAF **略高于** SVT-AV1 preset 5 —— 高码率/近透明细节保真 x265 仍略优。
- **社区共识**：高码率/细节敏感素材 x265 更稳；**重颗粒 + 低码率场景 AV1+FGS 反转占优**（可压到 2–5 Mbps 且观感更好）。来源：https://www.reddit.com/r/AV1/comments/17me48j/ 、https://www.reddit.com/r/AV1/comments/1e0zgji/

### 2.2 Netflix 生产环境（权威，2025-12）
- AV1 占 30% VOD 观看；**AV1 会话带宽比 AVC 与 HEVC 均低约 1/3（33%）**；缓冲中断少 45%；**平均 VMAF 比 AVC 高 4.3 分、比 HEVC 高 0.9 分**。来源：Netflix Tech Blog（摘要 https://www.streamingmediablog.com/2025/12/netflix-av1.html ）
- 条件说明：为 Netflix 生产（per-title 优化+硬解），非 SVT preset 2-6 直接离线测试，代表"AV1 vs HEVC"权威上限。

### 2.3 vs libaom（aomenc）
- 共识：aomenc 是参考质量实现、极慢，**SVT-AV1 快约一个数量级**，归档/生产均选 SVT-AV1。生产项目迁移证据：ComfyUI（https://github.com/Comfy-Org/ComfyUI/pull/7736 ）、immich（https://github.com/immich-app/immich/pull/13389 ）均从 aom 改用 svt-av1（preset 6）。精确"倍数"未在本次可靠量化（aomenc 极少用于归档实测）。

### 2.4 vs 硬件 AV1（NVENC / QSV）
- **NVENC（Oppenheimer 1080p BD 实测，r/AV1）**：同体积下 NVENC VMAF 略高但需 **~40× 快**；同 VMAF 档 NVENC 体积是 SVT 的 **2–3×**。社区共识"NVENC 从来不是归档工具，是流媒体卸负载方案"；<2000 kbps@1080p 以下 SVT 大幅反超；硬件编码器为刷 VMAF 调优、牺牲真实画质。来源：https://www.reddit.com/r/AV1/comments/18l0k07/
- MSU 2023-2024 Part 5 为硬件 4K 10-bit 专测（http://www.compression.ru/compression.ru/video/codec_comparison/2023/4k_hardware_report.html ）

---

## 3. 速度与吞吐

### 3.1 preset 0-6 @ 4K fps
- **权威/社区"4K 10bit preset 0-6" fps 成表不存在**（Phoronix/openbenchmarking 抓取被 403/503）。最接近参考：
  - 4K 8-bit preset 6（旧版 v0.8.7，park_joy 4096×2160，-qp55）：Ryzen 9 7950X ≈**5.7 fps**、i9-13900K ≈**6.4 fps**。https://www.hwcooling.net/en/ryzen-9-7950x-amds-elite-cpu-beats-but-also-doesnt-beat-core-i9-review/25/
  - 1080p50 8-bit（AWS c5.9xlarge 36vCPU，CRF26，v1.6.0）：preset 2=**0.5**、4=**3.4**、6=**9.4**、8=25、10=48、12=62 fps。https://ottverse.com/analysis-of-svt-av1-presets-and-crf-values/
  - 1080p60（Ryzen 5950X，CRF14）：preset 6 8-bit **15.5** / 10-bit **14.4** fps。https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/1979
- **10-bit vs 8-bit**：官方称 10-bit 编码损失小（仅 preset 11-13 明显）；社区实测 10-bit 慢：S4 **140%**、S6 **60%**、S8 100%。https://colinmckellar.com/2024/03/11/svtav1-10-bit/
- **注意**：v4.x 相对 v0.8.7/v1.6 已多轮提速（官方 v2.3 CHANGELOG 4K-10bit preset4/6 相对 v2.2 提速 1.33×），4K 实际 fps 应高于上表旧值，需自测。

### 3.2 preset 4 vs 6 收益
- 两处独立实测一致：**preset 6 比 4 快约 2.6–2.8×，同 CRF 下体积几乎不变、质量损失极小**。
  - OTTVerse（1080p Parkjoy CRF26）：4=3.4 fps vs 6=9.4 fps（2.76×）；文件均 75MB；VMAF 99.679 vs 99.565；PSNR 38.727 vs 38.439。https://ottverse.com/analysis-of-svt-av1-presets-and-crf-values/
  - 动画源（v2.2，Ryzen 5900X，CRF22）：4=20.47 fps vs 6=53.14 fps（2.6×）；VMAF 97.42 vs 97.27；码率 2397 vs 2638 kbps（+10%）。https://299792458m.blogspot.com/2024/08/22.html
- **归档结论**：追求最大保真且时间充裕→preset 2-3；平衡→preset 4；时间敏感→preset 6（性价比最高）。官方无 4vs6 单一 BD-rate 数值。

### 3.3 内存占用
- **内存随 `--lp` 与分辨率增长，不随 preset 直接增长**（"preset 越低内存越高"不成立）。官方：`--lp` 越高线程/并行画面越多→fps 越高、内存越大；**CRF 下 lp≥4 并行多个 mini-GOP→"much higher memory"**。来源：Parameters.md 附录 A.1
- 官方无"preset×lp×分辨率 GB 表"。唯一带 GB 案例：av1an 5 worker 跑 SVT-AV1-PSY v3.0.2（720p 10bit preset2 CRF18）"RAM 全程 <12GB"（≈<2.4GB/worker）。https://github.com/rust-av/Av1an/issues/1019
- **16/32GB 判断**：单路 4K 默认设置 16GB 通常可行；4K+CRF+lp≥4 会显著升高；av1an 多 worker 内存按 worker 数叠加。**精确 GB 表需实测**。

### 3.4 多核扩展 & chunked
- 官方：单进程约 **16 核**（1080p）是高效利用甜点，4K 更高；preset 0-3 依赖性强、并行 CPU 利用率低。来源：CommonQuestions.md
- av1an 按场景切块多进程并行可突破（自称"Hyper-scalable"，96 核跑满）。https://github.com/master-of-zen/Av1an

---

## 4. Windows 使用现状

- **官方无 Windows exe**：GitLab Releases 全部版本 assets=NONE（https://gitlab.com/AOMediaCodec/SVT-AV1/-/releases ）。
- **gyan.dev ffmpeg**：最新 release **9.0.1**（2026-08-12），内置 libsvtav1。https://github.com/GyanD/codexffmpeg/releases/tag/9.0.1
- **BtbN FFmpeg-Builds**：每日自动构建（2026-08-28），win x86_64/arm64，master/9.0/8.1 分支；**SVT-AV1 pin 到提交 fb0ed7e5（2026-08-08，>v4.2.0）**，即跟进非常及时。https://github.com/BtbN/FFmpeg-Builds/releases/latest 、脚本 https://raw.githubusercontent.com/BtbN/FFmpeg-Builds/master/scripts.d/50-svtav1.sh
- **ffmpeg 兼容**：官方说明 ffmpeg ≥5.1.0 才完整支持 `-svtav1-params` 透传。来源：Docs/Ffmpeg.md
- **第三方 Windows 二进制**：github.com/AOMediaCodec/SVT-AV1-Binaries 已 404（不存在/已移除）；活跃社区 Windows 二进制为 **Patman86/SVT-AV1-Mods-by-Patman**、StaxRip（v2.52.5）捆绑、NotEnoughAV1Encodes(NEAV1E v2.1.7)、FastFlix(6.2.1)、HandBrake 等 GUI 封装。
- **av1an**：rust-av/Av1an 最新 **v0.5.2**（2026-01-04），chunked 并行 + `--target-quality`（VMAF/SSIMULACRA2/Butteraugli/XPSNR）是 Windows 归档并行的事实标准。https://github.com/rust-av/Av1an/releases/latest

---

## 5. 归档模板共识（≥5 套，注明来源）

**定位：SVT-AV1 完美透明（transparent）被公认做不到（即便 CRF 12），归档现实目标是"近无损/高保真"。**

| # | 来源 | 参数组合 | 定位 |
|---|---|---|---|
| 1 | 官方 Docs/Ffmpeg.md（个人/媒体库） | `-preset 5 -crf 32 -g 240 -pix_fmt yuv420p10le -svtav1-params tune=0:film-grain=8` | 官方个人档平衡 |
| 2 | 官方 Docs/Ffmpeg.md（VOD 单场景） | `-preset 2 -crf 25 -g 24 yuv420p10le tune=0:film-grain=8` | 官方高保真 |
| 3 | JET 编码指南 "Sane Base" | `--preset 4 --enable-variance-boost 1 --tf-strength 1 --sharpness 1 --tile-columns 1 --crf 25 --film-grain 4 --luminance-qp-bias 25` | 高视觉吸引力（非透明） |
| 4 | dvaupel 指南/Reddit r/AV1（颗粒电影"近无损"） | `--crf 20 --preset 3 --irefresh-type 1 --keyint 240 --input-depth 10 --enable-overlays 1 --enable-tf 0 --enable-restoration 0 --film-grain <N>` | 高码率/BD 级近无损 |
| 5 | dev.to 归档档 | CRF 20-25（标"视觉无损"）+ `-preset 6 -pix_fmt yuv420p10le` | 归档/母版档 |
| 6 | Doom9 用户（HandBrake 实拍电影） | AV1 10-bit、CRF 26、Preset 5、`enable-tf=0:film-grain-denoise=0:film-grain=8:mbr=10m`（动画 grain 8→4） | 近透明/高保真 |

**共识要点**：
- preset 归档集中 **4-6**（甜点），极致保真下探 **2-3**；preset 8≈x265 medium、6≈x265 slow（dvaupel 指南）。
- CRF：1080p 近透明区间 **18-24**；CRF 30 ≈ x265 CRF 21（社区换算起点）。
- 10-bit 几乎全员推荐（即便 8-bit 源）；**解码覆盖≈91% 但 10-bit 硬编仅≈8%**（能解不能编）。
- film-grain：正常 8 / 噪多 10-15 / 动画 4；社区实测常用 **10-20**；**denoise=0 共识保持**。
- keyint：家庭归档 **5-10s**（24fps→`-g 240`）；`-1` 无限仅 CRF。
- 来源：https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Ffmpeg.md 、https://jaded-encoding-thaumaturgy.github.io/JET-guide/master/encoding/svtav1/ 、https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95 、https://dev.to/javidjamae/ffmpeg-av1-encoding-svt-av1-vs-libaom-av1-guide-2lnc 、https://forum.doom9.org/showthread.php?t=185159 、https://www.reddit.com/r/AV1/comments/16kjcie/

---

## 6. 已知问题 / 坑（归档场景）

- **FGS × 低码率/高 CRF**：`film-grain-denoise=0` 且值过高 → **噪点堆积**；降噪会误删极细细节（细小粒子/皮肤纹理）；`--adaptive-film-grain`（默认开）在部分源**噪点帧有/无交替闪烁**（issue #2298，8/10-bit 皆然，暗场尤甚）。来源：https://gitlab.com/AOMediaCodec/SVT-AV1/-/work_items/2298 、CommonQuestions.md
- **FGS 非"安全默认"**：它是**环外后处理**，解码端实现不一（部分设备不解 grain、暂停时 grain 随机变化）；benwaggoner "AV1 与 AV1+FGS 几乎可视为不同 codec"；grain 尺寸按显示分辨率而非内容渲染（规格缺陷）。若解码端不可控→**关 FGS、高码率保颗粒**。来源：https://forum.doom9.org/showthread.php?t=184502
- **10-bit grain**：未检索到 10-bit 专属 grain bug（闪烁 issue #2298 明确 8/10-bit 皆发生）；10-bit 普遍推荐以减 banding。早期 issue #1880（2022 v1.0rc1）preset 12/6 FGS 坏帧属历史 bug。
- **`--enable-qm`**：默认 0；开启倾向省码率、质量略降（低 CRF 时降幅更明显）；社区建议配低 `qm-min`（如 0）且 chroma-qm-min 高于 luma；**追求绝对保真慎用**。来源：Parameters.md、https://forum.doom9.org/showthread.php?t=185686
- **`--sharpness`**：默认 0；社区升到 **1-2** 保守提效+提升感知质量，更高可能降效率；**负值副作用未检索到可靠实测**。来源：JET 指南、Parameters.md
- **`--max-tx-size 32`**：64pt 变换会抹掉高频噪点状纹理（官方附录 B），追求纹理保真可限 32。
- **`--ac-bias`**：中值 1.0-1.5 保纹理/复杂运动；高值 4-6 + 关 TF/CDEF 大幅改善颗粒保留。
- **场景切换不插 KF**：需 av1an 切块；`--keyint -1` 无限 GOP 的 seek 问题（selur 实证 ffplay seek 失败）；StaxRip 分块 seek 定位异常（keyint 10s + 开放 GOP 叠加）。
- **暗场码率饥饿**：`--luminance-qp-bias`（20-25）改善，PQ(HDR) 源勿用。
- **其他**：AV1 不支持隔行；低质量源二次压缩常"输出比输入还大"；播放端 HDR/tone-mapping 会造成"伪编码器伪影"（Doom9 t=186767），归档 QA 需排除播放链。

---

## 附：数据可信度与缺口

- **高可信**：官方 GitLab 源码/文档（enc_settings.c、definitions.h、Parameters/Ffmpeg/CommonQuestions/附录）、Netflix Tech Blog、gyan/BtbN/av1an 官方 release 页、GitLab API（releases/issues/commits）。
- **中可信**：MSU 2023-24（官网页抓取遇 502，其"51-65% 码率"经社区报告列为已确认基线，建议引用时回访官网确认）、OTTVerse/dvaupel/colinmckellar 实测、Doom9/r/AV1 亲测（含主观分歧）。
- **低可信/仅线索**：CSDN 中文综述（数字疑有夸大，勿直接引用）、GitCode AI 博客。
- **明确缺口**：(1) 4K 10bit preset 0-6 权威 fps 表不存在；(2) preset 4 vs 6 无官方 BD-rate 单点；(3) 内存无官方"preset×lp×4K GB 表"；(4) MSU 官网原始表格与 libaom 精确速度倍数未可靠量化。
