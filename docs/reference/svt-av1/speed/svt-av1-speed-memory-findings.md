# SVT-AV1 速度/吞吐与内存占用 —— 事实依据汇总

- 汇总时间：2026-08-29
- 范围：仅"速度与吞吐、内存占用"主题（不含质量/码率控制细节）
- 目标版本背景：SVT-AV1 官方主线 v4.2.0（2026-07-14），preset -1~13，--lp 0~6

> 说明：本文件每条数据都标注来源 URL 与测试条件。原始抓取网页正文保存在本目录下同名文件中（文件名内含来源域），每条原始文件头部均含来源 URL 与抓取时间。未检索到可靠数据的项目明确标注"未检索到可靠数据"。

---

## 1. preset 0-6 在 4K (2160p) 10bit 的实测 fps

### 1.1 结论
- **未检索到**"preset 0~6 × 4K × 10bit"三者同时满足的、带完整测试条件的社区实测 fps 数值表（Reddit / Doom9 / GitHub / 论坛 / 博客均未搜到可直接引用的成表数据）。
- 能检索到的最接近数据分三类，见下：
  1) 4K **8-bit** preset 6 的实测（hwcooling，但版本很旧 v0.8.7）；
  2) 1080p **8/10-bit** preset 6~12 的成表实测（GitLab issue #1979，Ryzen 5950X）；
  3) 1080p 8-bit preset 2~12 的成表实测（OTTVerse，AWS c5.9xlarge）。
- 官方文档明确：10-bit 相对 8-bit 的编码性能损失很小，仅在很快的 preset（11-13）才较明显（见 1.4）。

### 1.2 关键数据

**(a) 4K 8-bit preset 6（历史版本 v0.8.7，作参考）**
- 测试条件：park_joy_2160p50.y4m（4096×2160、8bit、50fps、500 帧），`ffmpeg -c:v libsvtav1 -rc 0 -qp 55 -preset 6`，SVT-AV1 Encoder Lib v0.8.7-61-g685afb2d（2021 年版本）。
- 结果（编码耗时，秒，越低越好）：
  - AMD Ryzen 9 7950X (16C/32T)：87.3 s ≈ **5.73 fps**
  - Intel Core i9-13900K (24C/32T)：78.8 s ≈ **6.35 fps**
  - Intel Core i7-13700K (16C/24T)：95.8 s ≈ **5.22 fps**
  - Intel Core i9-12900K (16C/24T)：109.4 s ≈ **4.57 fps**
- 来源：https://www.hwcooling.net/en/ryzen-9-7950x-amds-elite-cpu-beats-but-also-doesnt-beat-core-i9-review/25/ （原始 iframe 图数据：`hwcooling-7950x-svtav1-graph.html`）

**(b) 1080p60 8-bit / 10-bit，preset 6~12（8-bit 源）**
- 测试条件：AMD Ryzen 9 5950X (16C/32T)，1920×1080 60fps，8-bit 源，`tune=0`，CRF 14，SVT-AV1 v1.2.x（Windows，VS2022 Release）。表中 fps 为"最后一次正常提交"数据（10-bit 后续曾出现回归，见 1.4 备注）。
  | Preset | 8-bit fps | 10-bit fps |
  |---|---|---|
  | 6 | 15.526 | 14.408 |
  | 7 | 30.525 | 26.88 |
  | 8 | 66.703 | 58.094 |
  | 9 | 100.052 | 78.304 |
  | 10 | 128.642 | 100.055 |
  | 11 | 138.536 | 112.561 |
  | 12 | 180.088 | 138.528 |
- 来源：https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/1979 （原始文件 `gitlab-svt-av1-10bit-regression-1979.html`）

**(c) 1080p50 8-bit，preset 2~12（偶数 preset，CRF 26）**
- 测试条件：Parkjoy 1080p50（中高运动、大量纹理），SVT-AV1 v1.6.0，AWS EC2 c5.9xlarge（36 vCPU），`ffmpeg -c:v libsvtav1 -crf 26 -preset N`，8-bit。
  | Preset | fps | VMAF | PSNR(dB) | 文件大小(MB) |
  |---|---|---|---|---|
  | 2 | 0.5 | 99.709 | 39.283 | 75 |
  | 4 | 3.4 | 99.679 | 38.727 | 75 |
  | 6 | 9.4 | 99.565 | 38.439 | 75 |
  | 8 | 25 | 99.506 | 37.965 | 81 |
  | 10 | 48 | 99.446 | 37.677 | 86 |
  | 12 | 62 | 99.289 | 36.816 | 92 |
- 来源：https://ottverse.com/analysis-of-svt-av1-presets-and-crf-values/ （原始文件 `ottverse-svt-av1-presets-crf.html`）

**(d) 1080p 动画源（CRF 22，v2.2，12 核）**
- 测试条件：Ryzen 9 5900X (12C/24T)，某动画源（分辨率未注明，通常为 1080p），SVT-AV1 v2.2，`--crf 22`。
  | Preset | fps | VMAF | kbps |
  |---|---|---|---|
  | 3 | 13.17 | 97.45 | 2367 |
  | 4 | 20.47 | 97.42 | 2397 |
  | 5 | 40.18 | 97.34 | 2581 |
  | 6 | 53.14 | 97.27 | 2638 |
  | 8 | 58.12 | 97.24 | 2701 |
  | 10 | 66.42 | 96.93 | 2861 |
  | 12 | 71.01 | 96.45 | 3076 |
- 来源：https://299792458m.blogspot.com/2024/08/22.html （原始文件 `blog-299792458m-svtav1-2.2.html`）

### 1.3 官方：4K 10-bit 相对提速（非绝对值）
- SVT-AV1 v2.3.0 CHANGELOG（2024-10-28），与 v2.2 对比，AWS Graviton4（ARM），**`--lp 1`**，Bosphorus 2160p 高比特深度（=4K 10bit）：
  preset 0=1.18x、1=1.19x、2=1.16x、3=1.27x、4=1.33x、5=1.27x、6=1.33x、7=1.35x、8=1.82x、9=1.95x、10=1.40x、11=1.35x。
- 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md （原始文件 `svt-av1-changelog.md`）

### 1.4 8-bit vs 10-bit 速度差异
- 官方（CommonQuestions.md「8 or 10-bit Encoding」）：10-bit 结果文件约大 ~5%；**编码性能损失很小**，仅在非常快的 preset（11-13）减速较明显；10-bit 解码可能比 8-bit 更耗算力。
- 社区实测（colinmckellar，2024-03，VMAF 对齐的实测，硬件未注明，源为 8-bit、转 10-bit 输出）——"10-bit 比 8-bit 慢多少"，按 preset：
  - S3 慢 120%、S4 慢 140%、S5 慢 60%、S6 慢 60%、S7 慢 80%、S8 慢 100%、S9 慢 120%、S10/S11/S12 慢 40%。
  - 注意：该博客为 VMAF 对齐比较，结论是"10-bit 用更快 preset 即可达到 8-bit 更低 preset 的画质"，与官方"相同 CRF 下损失很小"口径不同，需区分比较基准。
  - 来源：https://colinmckellar.com/2024/03/11/svtav1-10-bit/ （原始文件 `colinmckellar-svtav1-10bit.html`）
- GitLab issue #1979 亦记录：v1.2.1 曾出现 Windows 下 10-bit 编码回归（10-bit fps 一度掉到约 1/3），后作为 bug 处理；说明 10-bit 路径历史上存在平台相关性能波动。

---

## 2. 归档常用 preset 4 vs 6 的收益差异

### 2.1 结论
- 社区实测（两处独立来源）一致：**preset 6 比 preset 4 快约 2.6~2.8 倍**，而质量/体积损失很小（同 CRF 下文件大小几乎相同、VMAF 差 <0.12、PSNR 差 <0.3dB；动画源码率约 +10%）。
- 官方文档把 preset 4~6 归为"家庭爱好者常用、效率与耗时平衡"区间；官方未给出 4 vs 6 的单一 BD-rate 数值（未检索到）。

### 2.2 关键数据
- OTTVerse（v1.6.0，c5.9xlarge 36vCPU，Parkjoy 1080p50，CRF 26）：
  - preset 4 = 3.4 fps；preset 6 = 9.4 fps → **preset 6 约快 2.76 倍**。
  - 同 CRF=26：两者文件大小都是 75 MB；VMAF 99.679 vs 99.565（差 0.11）；PSNR 38.727 vs 38.439（差 0.29dB）；SSIM 0.977 vs 0.975。
  - 文章结论：preset 6 的大小/码率/客观指标与 preset 2 接近，却快 20 倍；建议质量-速度甜点在 preset 6~8。
  - 来源：https://ottverse.com/analysis-of-svt-av1-presets-and-crf-values/
- 动画源（v2.2，Ryzen 5900X，CRF 22）：
  - preset 4 = 20.47 fps；preset 6 = 53.14 fps → **preset 6 约快 2.6 倍**；VMAF 97.42 vs 97.27；码率 2397 vs 2638 kbps（**+10%**）。
  - 来源：https://299792458m.blogspot.com/2024/08/22.html
- 官方预设语义（CommonQuestions.md）：preset 4 与 6 的差异主要在——4 开启 Wedge/Difference-weighted/Distance-weighted 预测（6 关闭）、Global Motion Compensation（5 起关闭）、max reference frame 7 vs 5、4 含 Self-Guided(SG) 恢复滤波（5 起关闭）。这些是 preset 4 更慢但压缩略优的来源。
  - 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md
- 未检索到：官方或第三方针对 preset 4 vs 6 的正式 BD-rate/speed（如 "-x% BD-rate @ 2.6x speed"）单点数值。

---

## 3. 内存占用（各 preset 与 --lp 组合）

### 3.1 结论
- 官方把内存直接与 **--lp（LevelOfParallelism）与分辨率**绑定，而不是直接与 preset 绑定：**--lp 越高 → 线程越多 + 并行处理的 picture 越多 → fps 越高但内存越大**。
- **CRF 模式下 --lp ≥ 4 会额外并行处理 mini-gop，官方原文"much higher memory"**。low-delay 模式一次只处理一帧，不会额外分配 picture。
- "--lp 数值 ≠ 线程数"：官方明确 --lp 只是并行度等级，实际线程数与内存由代码 `load_default_buffer_configuration_settings` 决定（0=按机器核数自动）。
- 因此"preset 越低内存越高"**不成立**（preset 主要影响 CPU 耗时）；但 preset 0~4 的 max reference frame = 7（preset 5~9 为 5，10 为 2），会带来轻微参考帧缓冲差异，量级很小。
- 16GB / 32GB 是否够用：**未检索到权威的"preset×--lp×4K"GB 表**。社区证据（Av1an 5 并发 720p preset2 10bit ≈ <12GB，见下）表明单路 4K 默认设置在 16GB 上通常可行，但 4K + CRF + --lp≥4 会显著增加内存，需实测；多 worker（av1an chunked）时内存按 worker 数叠加。

### 3.2 关键数据 / 官方说明
- 官方 Parameters.md（Appendix A.1 Thread management）：
  - `--lp` 范围 [0,6]，默认 0（按核数自动）。"higher levels will create more threads and process more pictures in parallel, leading to greater fps but **larger memory use**"。
  - "In CRF mode, **levels 4 and higher** will process extra mini-gops in parallel as well, leading to higher speed, but **much higher memory**"。
  - `--pin` 可把执行固定到前 N 核；taskset 示例：`taskset --cpu-list 0-3 ... --lp 3` 与 4-7 各跑一路。
  - 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md
- 官方 CHANGELOG 内存相关条目（相对量，非 GB）：
  - v2.0.0（2024-03-13）："Memory savings of 20-35% for **LP 8 mode in preset M6 and below** and 1-5% in other modes / presets"。
  - v4.1 / v4.2.0：多次"Reduced runtime memory usage for RTC mode"、"Optimized memory footprint for RTC mode with small resolutions"。
  - 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/CHANGELOG.md
- 社区实测（内存 GB，唯一找到的带数字案例）：
  - Av1an issue #1019：32GB 机器，**5 个 worker** 并发跑 SVT-AV1-PSY v3.0.2、`--preset 2 --input-depth 10 --crf 18`（10-bit）、720p 源，"RAM usage never over 12GB total"（回归前的正常值）。→ 该场景单 worker 约 <2.4GB（720p preset2 10bit）。
  - 来源：https://github.com/rust-av/Av1an/issues/1019 （原始文件 `av1an-issue-1019-oom.html`）
- 未检索到：官方"各 preset×各 --lp 下的 GB 数值表"、以及"4K 10bit preset 0-6 各档精确内存"。

---

## 4. 多核扩展性 与 av1an chunked 并行

### 4.1 结论
- 官方明确：SVT-AV1 为多核扩展而设计，但**默认只用不降低画质的线程技术（不用 tile 并行做默认并行化）**；`--lp 1` 与 `--lp n` 在默认 CRF 配置下输出相同。
- 官方经验值（CommonQuestions）：**1080p、preset 4~6、默认配置下约能高效利用 ~16 核**；核心数再多，额外核心带来的增速递减（"drops off"）；**分辨率越高，线程扩展能力越高（4K 甜点核数高于 16）**；**preset 0~3 依赖多、并行度更低**。
- 突破手段：官方推荐用**第三方按场景切块的并行工具**（av1an）来进一步并行；av1an 通过"多进程并发编码"提升 CPU 利用率（README 自称"Hyper-scalable"，示例图 96 核跑满）。
- 高核数平台（如 Threadripper 多 CCD）存在利用率天花板：即使换更强的编码器，多 CCD + SMT 也会带来调度损耗（HandBrake 讨论，见下，x265 语境但机制通用）。

### 4.2 关键数据 / 说明
- 官方 CommonQuestions.md「Threading and Efficiency」：
  - "Anecdotally, SVT-AV1 is able to fairly efficiently use about **16 processor cores** when encoding **1080p** video on a preset in the **4-6** range using the default configuration."
  - "When using high core-count systems, SVT-AV1's ability to fully utilize all available threads **drops off** and additional cores provide less incremental encoding speed."
  - "**As resolution increases, threading capabilities go up as well.**"
  - "the highest quality presets (**0-3**) use features that have a lot of dependencies and may lead to **lower parallel CPU usage**."
  - 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/CommonQuestions.md
- 官方 Parameters.md：`--pin` + `taskset` 把不同编码进程钉在不同核上（示例 2 路各 4 核、`--lp 3`），并注明"若 CPU 未跑满可提高 --lp（内存随之增加）"。
  - 来源：https://github.com/AOMediaCodec/SVT-AV1/blob/master/Docs/Parameters.md
- av1an：
  - README："increase your encoding speed and improve cpu utilization by **running multiple encoder processes in parallel**"；"**Hyper-scalable** video encoding"；首页图展示"av1an fully utilizing a 96-core CPU for video encoding"。
  - 机制：按场景切块（chunk）后，多 worker 各自跑一个 SvtAv1EncApp 进程 → 绕过单进程 ~16 核（1080p）扩展上限；内存 = 每 worker 编码器内存 × worker 数（见第 3 节 12GB/5worker 案例）。
  - 来源：https://github.com/master-of-zen/Av1an （原始文件 `av1an-readme.md`）
- 高核数天花板旁证（HandBrake Discussion #7311，x265 语境，机制通用）：
  - AMD Ryzen Threadripper 9960X（24 核）跑 4K 2160p60 x265 仅 ~50% CPU；对比 i9-14900K 可达 100%。原因提及"multi-CCD architecture challenges"、SMT 线程对视频编码"typically doesn't help much"；即便如此 9960X 仍比 14900K 快 ~30%。维护者建议"可切到 SVT-AV1"以获得更好扩展。
  - 来源：https://github.com/HandBrake/HandBrake/discussions/7311 （原始文件 `handbrake-threadripper-50pct.json`）

---

## 附：本目录原始抓取文件清单
| 文件 | 来源 |
|---|---|
| svt-av1-common-questions.md | 官方 CommonQuestions.md（GitHub raw） |
| svt-av1-parameters.md | 官方 Parameters.md（GitHub raw） |
| svt-av1-changelog.md | 官方 CHANGELOG.md（GitHub raw） |
| ottverse-svt-av1-presets-crf.html | OTTVerse preset/CRF 分析 |
| gitlab-svt-av1-10bit-regression-1979.html | GitLab issue #1979（10-bit 回归 + fps 表） |
| colinmckellar-svtav1-10bit.html | colinmckellar 10-bit 减速表 |
| blog-299792458m-svtav1-2.2.html | 日文博客 v2.2 preset fps（5900X 动画） |
| hwcooling-7950x-page25.html / -graph.html | hwcooling 7950X 4K preset6 编码耗时 |
| av1an-readme.md | Av1an README |
| av1an-issue-1019-oom.html | Av1an OOM issue（内存 GB 案例） |
| handbrake-threadripper-50pct.json | HandBrake Threadripper 扩展性讨论 |
| av1-pocket-guide.md | 社区《Tenets of AV1 Encoding》指南 |
| nijaru-mead-benchmark.patch | （AI 生成项目文档，未作为可信数据采用） |
