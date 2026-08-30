# SVT-AV1（含 PSY fork）归档可行性评估报告

> 评估日期：2026-08。方法：官方文档核查（Parameters.md v4.2.0 等已存档
> docs/reference/svt-av1/）+ 本机实证（4K60 真实素材基准）+ 三方联网调研
> （主线演进 / PSY 现状 / 社区归档实践）。
>
> **结论前置**：SVT-AV1 在"非 XAVC 经典路径"的归档上
> **技术上可行且压缩率显著优于 x265**（MSU：同画质约省 35-49% 码率），
> 但受三大约束：①编码输出仅 4:2:0（XAVC-S 4:2:2 源必须降色度）；
> ②归档级 preset（2-4）的吞吐在 4K60 仅 0.1-0.5x 实时（本机）；
> ③PSY fork 已停更，长期归档应优先主线（其适用特性已全部并入主线 4.x）。

---

## 1. 主线与 PSY 的关系速览

| 项目 | 版本/状态 | 归档相关要点 |
|---|---|---|
| **SVT-AV1（主线）** | v4.2.0（2026-07-14） | **v4.0（2026-01）已完成 PSY 全部适用特性移植**（tune vq/iq、AC Bias=psy-rd、variance boost、adaptive film grain、TF strength 等）；color-format 仅 yuv420（8/10bit）；官方 Releases 无 Windows exe |
| **SVT-AV1-PSY** | **已停止维护**：v3.0.2 "Supernova"（2025-04-20，最后大版本） | 作者宣布移植主线后收缩独立维护；Windows 无一手二进制、未随 ffmpeg 内置 |
| 继任 fork | SVT-AV1-HDR（Julio，活跃、官方推荐继任）；PSYEX（被搁置）；Essential（QoL fork） | 仅 HDR fork 仍被社区用于颗粒感/纹理极致需求，长期归档不建议依赖 |

## 2. 主线归档调参（官方 + 教程已核要点）

### 2.0 主线演进（据官方 CHANGELOG，docs/reference/svt-av1/src/CHANGELOG.md）

- **3.0（2025-05）**：wave 1 PSY 特性移植（variance boost/sharpness/
  adaptive film grain/TF strength 等）
- **4.0/4.1（2026-01 前后）**：wave 2——**AC Bias（=PSY 的 psy-rd，!2513）、
  Tune IQ（!2489）、quarter-step CRF（!2503）、transform 限制（!2507）、
  `--scm 3`（!2494）、adaptive film grain 可开关（!2496）**；条目均显式
  标注 "(SVT-AV1-PSY, !MR)"
- **4.2.0（2026-07-14）**：**新增 TUNE-VMAF 模式（目标 ~15% VMAF BD-rate
  改善、最小 PSNR 损失）**、`--cqp`、`--hbd-mds`、`--enable-kf-tf`、raw
  OBU 输出、RA 档位 M3-M5 调优、seek A/V 同步修复
- **注意**：4.2 的 TUNE-VMAF 与 Tune IQ 是**不同 tune**，标定时勿混淆

### 2.1 参数要点

- preset：13~7 实时/快速；**6~4 常规；3~1 高压缩**；-1 Research（官方默认 8）
- tune 0-5（0=VQ 默认、1=PSNR、2=SSIM、3=IQ、4=MS-SSIM、5=VMAF，另见
  TUNE-VMAF 模式）；归档建议 tune 0（VQ）或按验收指标选择
- CRF 1-70（默认 35；**4.1 起 quarter-step 0.25 步进**）；CQP/--rc 0-2
- **film-grain 0-50**：AV1 特色颗粒合成（环外后处理，见 §6）；4.1 起
  adaptive film grain 可开关
- keyint：官方指导家用 5-10s、hobbyist=帧率×10（≤300）、取 mini-GOP(16)
  倍数+1；lookahead 自动；`--lp` 并行度
- **film-grain 官方取值指导**：8（正常）→ 10-15（噪多）→ 4-6（动画）；
  `film-grain-denoise` 默认 0
- 坑：10bit y4m 管道需 `-strict -1`；raw 直导 `--input-depth` 必须匹配
  （否则绿屏）；`--sharpness` 实测常适得其反（默认 0）；`--enable-tf` 与
  `--enable-overlays` 互斥；`--enable-qm` 省码率但略降质（低 CRF 更明显）
- **编码能力仅 4:2:0 8/10bit**：源码 enc_settings.c 明确 "Only support
  420 now"、"Bit Depth only 8 or 10"——**4:0:0 与 12-bit 均未实现**（issue
  #1463/#2153；definitions.h 里 Profile 0 含 4:0:0 是规范定义，非编码
  能力）→ 4:2:2 归档需 libaom（慢）或色度降级

### 2.2 社区/官方归档模板共识（6 套，均带来源）

| 来源 | 模板 | 定位 |
|---|---|---|
| SVT-AV1 官方（个人档） | `preset 5 crf 32 g240 yuv420p10le tune 0 film-grain 8` | 官方个人使用基线 |
| SVT-AV1 官方（VOD） | `preset 2 crf 25 g24` | 官方点播档 |
| JET "Sane Base" | `preset 4 enable-variance-boost 1 tf-strength 1 sharpness 1 crf 25 grain 4 luma-bias 25` | 社区均衡档 |
| dvaupel（近无损） | `crf 20 preset 3 keyint 240 10bit` | 近无损发烧档 |
| dev.to 归档 | `CRF 20-25 preset 6` | 通用归档 |
| Doom9 HandBrake | `CRF 26 preset 5 enable-tf 0 grain 8 denoise 0` | 消费转码档 |

**共识提炼：preset 4-6、CRF 18-24、10bit、grain 8-20（denoise 0）、
keyint 5-10s。**（本项目建议从官方个人档 + JET Sane Base 出发做档位标定）

## 3. PSY 版本评估（定稿）

### 3.1 生死状态（决定性事实）

- **PSY 已正式停更**：v3.0.2 "Supernova"（2025-04-20）是作者明确宣布的最后
  大版本；psy-ex 仓库现指认 **SVT-AV1-HDR 为官方推荐继任者**（PSYEX 也被
  作者搁置以优先 HDR）。
- **主线已吸收 PSY**：SVT-AV1 3.0（2025-05，wave 1）→ 4.0（2026-01，
  wave 2/完成）。官方 4.0 changelog："完成对所有适用 PSY 特性的移植"
  （`--tune vq`/`--tune iq`，含 AC Bias=psy-rd）。→ **归档首选主线 4.x**，
  无需依赖 PSY。
- 仍未被主线吸收的 PSY 系特性（官方 work item #2269 标记 [ ] / not
  planned）：sharp-tx/spy-rd（=`--tx-bias`）、complex-hvs、noise-norm-
  strength、noise-adaptive-filtering、Tune 3（主观 SSIM，官方明确不移植）、
  -2/-3 preset、Dolby Vision/HDR10+ 支持。

### 3.2 质量增益的性质

- PSY 的设计哲学是**"用指标换主观"**（其 Tune 3 官方描述即 "harms metric
  performance in exchange for better visual fidelity"）——不存在 PSY 在
  VMAF/SSIM 上全面领先主线的结论；增益在**高码率/颗粒/纹理/暗部**的
  主观感知层，低码率下收益有限且有伪影风险（官方自述 spy-rd "more
  chances of banding and blocking"）。
- fork 间实测（Doom9，Julio 首发）：4K HDR 中重度 film grain 样片，HDR
  fork 的 grain tune 可比质量下仅需 PSY 3.0.2 的 **56.6% 体积**（6 vs
  10.6 Mb/s），且多数测试者偏好 HDR 的颗粒保留。
- **无第三方严格 BD-rate 头对头**（PSY vs 主线同 CRF/preset）公开数据。

### 3.3 兼容性

- PSY 输出是**标准 AV1**（所有特性在率失真决策层、无非标准语法），
  dav1d/播放器/NLE 正常解码；film-grain 沿用官方合成机制（差异只是默认值
  ：`film-grain-denoise 0`、adaptive-film-grain，均已并入主线）。
- 色度同样**仅 yuv420**（8/10bit）；PSYEX/HDR 的 `--hbd-mds` 只改内部
  精度不改输出位深。

### 3.4 归档风险清单（依赖停更 fork 做数十年归档）

1. 特性冻结无维护（v3.0.2 后无修复；已知 8-bit psy-rd bug 等无人跟进）
2. 与主线分叉持续扩大（基于 2.3.x/3.0 时代代码；主线已 4.x 且 AV2 已发布）
3. Windows 二进制不可复现（仅社区构建、曾崩溃/需 mimalloc 注入/杀软误报，
   数十年后无法重建同款构建复压）
4. 未并入主线的特性无上游兜底（sharp-tx/spy-rd 等缺陷主线不修）
5. 许可存疑（社区质疑 psy-rd 概念取自 GPL 的 x264/x265 后被以 BSD 重标，
   未证实但属长期不确定性）
6. 无 AOM 一致性认证（输出合规可解码，但无法用同一工具复现）
7. 调参倾向与归档目标冲突（锐化/关 CDEF/关 restoration 是感知优化而非
   中性保真，易在重编码链上累积伪影）

### 3.5 勘误（常见混淆）

PSY **不存在** `psy-trellis`、`aq-mode 4`（PSY 的 aq-mode 仅 0-2）、
`sparks`、`scenecut-aware-qp`——这些是 x265 概念或误记；场景检测是
SVT-AV1-Essential 的 `--scd`（未并入主线）。

### 3.6 PSY 结论

**归档不用 PSY**。其适用特性已全部进主线 4.x；对颗粒/纹理有极致要求时
可考虑 SVT-AV1-HDR（活跃、官方推荐继任），但必须接受个人维护 fork 的
生命周期风险，且建议同时保存主线编码副本兜底。

## 4. 质量对比（权威基准 + 社区实测）

- **MSU 2023-24 4K 10bit Slow**：SVT-AV1 仅需 x265 的 **51-65% 码率**
  （SSIM/PSNR/VMAF 三指标口径）；x264 需 204-223%
- **iXBT 2026 4K HDR 近透明段**：x265 slow 在 40/20Mbps 略高于 SVT-AV1
  preset5（99.92 vs 99.74）——近透明高码率段两者都达标，SVT 用较快的
  preset 未全开；社区共识与之一致：**近透明/细节敏感场景 x265 slow 更稳，
  AV1 的优势在体积敏感 + 重颗粒（FGS）场景**
- **vs 硬件 AV1（社区实测，Oppenheimer 1080p）**：同 VMAF 档 NVENC AV1
  体积是 SVT-AV1 的 **2-3 倍**（速度 ~40x）——社区共识：NVENC 是流媒体
  工具不是归档工具（与 av1_hw_tuning_guide.md 的"NVENC AV1 ≈ NVENC
  HEVC"结论互证）
- 归档语义提醒：**"省 35-49%" 是相对 x265 的同画质口径**；在近透明段
  两者绝对质量都够，SVT 的优势体现为更小的体积；在重颗粒素材上
  FGS 合成让 AV1 反超

## 5. 速度与吞吐（本机实证 + 社区数据）

本机（Core Ultra 9 285H 16 线程；**前台有 PS/LR 负载的下限值**，ffmpeg
9.0.1 内置 libsvtav1）：

| preset | 4K60 实测 fps | 相对实时(60p) |
|---|---|---|
| 8 | 27.9 | 0.46x |
| 6 | 11.4 | 0.19x |
| 4 | 7.3 | 0.12x |

- 社区实测（dvaupel）：preset 3→8 耗时 **781s→109s** 而体积几乎不变——
  **preset 4/5 是归档共识甜点**，更低 preset 的压缩率增益被速度代价稀释
- **preset 6 比 4 快约 2.6-2.8×、同 CRF 下体积几乎不变、VMAF 差 <0.12**
  （OTTVerse + 动画源两处独立实测）——**归档性价比甜点**，追求极致保真
  才下探 2-3
- 参照数据（无权威"4K 10bit 全曲线"公开表，需自测）：4K 8bit preset6
  旧版约 5-6 fps（7950X/13900K）；1080p50 preset 2/4/6 = 0.5/3.4/9.4 fps
  （c5.9xlarge）
- 内存：随 `--lp` 与分辨率增长、**不随 preset 增长**；CRF 下 lp≥4
  "much higher memory"；唯一案例 av1an 5worker/720p/preset2 <12GB——
  4K 高并发需实测（本机 32GB 是约束）
- 归档档（preset 4-6）4K60 为 5-8x 实时（本机下限值）；吞吐定位与 x265
  同量级——都是"夜间批量/小素材集"路径

## 6. 归档场景适配

- **film-grain 合成（关键机制事实）**：FGS 是**环外后处理**（SVT-AV1 官方
  Appendix Film-Grain-Synthesis）；共识取值 8（正常）→ 10-20（较重）→
  26-40（重颗粒），`film-grain-denoise=0` 被广泛推荐（内置去噪"粗暴"）；
  preset>6 不宜开 grain。**红线：Netflix 官方要求计算 VMAF 时关闭合成
  颗粒**（vmaf#1192）——本项目做指标验收必须先剥离 FGS 再比对。
  **设备兼容：benwaggoner 明确"很多设备不支持 FGS 或有 bug，不是安全
  默认项"**——归档若开 grain，消费端设备需可控，否则关 FGS 以高码率
  保颗粒
- **10bit**：AV1 Profile0 8-bit 解码覆盖 ≈91.5%（2026，1M+ 设备口径）、
  10-bit 解码 ≈91%；dav1d 软解全支持。输出 10bit 4:2:0 是合理归档基线
- **4:2:2 约束**：XAVC-S（yuv422p10le）源必须降色度——与 AV1 总评估
  （av1_feasibility_report.md）一致，4:2:2 保真需求留给 x265
- **XAVC 边界**：SVT-AV1 输出同样不进入 Sony 保留管线（仅经典路径）
- **NLE 兼容**：Premiere 近年版本支持 av01 导入但导出仍是长期 feature
  request；Resolve 19+ 有硬件 AV1 编码——归档消费端可用但弱于 HEVC
- **长期归档反面清单**（社区 12 例：细节抹除/偏色/输出花屏/长编码崩溃/
  低质量源越转越大等）：无"打不开/无法长期保存"的格式级硬失败，但共识
  仍是"保留原介质 + 高质量源，勿压缩已压缩内容"

## 7. 本项目集成定位

- 定位：**经典路径（非 Sony）软件高压缩档**（`--encoder svt-av1`），与
  x265 档并列的软件选项；不进入 XAVC 保留管线
- 集成成本：仿 x265 后端（encoders/svtav1.py + svt_av1.json），ScalingEngine
  与门控机制复用（av1_feasibility_report.md §5 已列）
- 参数翻译锚点：x265 档的 CRF/档位梯度 → SVT 的 preset/tune/crf/film-grain
  （映射表待标定）
- 可选加速：av1an chunked 并行（v0.5.2，`--target-quality` VMAF/SSIMULACRA2
  按场景找 CRF）。**坑：SVT-AV1 不插场景关键帧（必须由 av1an 做 scenecut）、
  vmaf_target<90 会 overshoot、长编码易崩溃、chunk 音频不同步**——仅在
  CPU 多核且素材长时段的场景有收益，本项目可选接入而非默认

## 8. 结论与建议（定稿）

**归档用主线 SVT-AV1 4.x，不用 PSY**（PSY 已停更、适用特性已全部并入
主线；SVT-AV1-HDR 仅作为颗粒极致需求的个人维护备选，需保存主线副本
兜底）。

档位建议（经典路径软件档候选，落地前以 testsets 标定）：
- **归档基线**：`preset 4/5 + CRF 18-24 + 10bit(yuv420p10le) + tune 0`
- **噪点素材**：grain 8-20 + `film-grain-denoise 0`（消费端设备可控时）；
  否则关 FGS 以高码率保颗粒
- **验收红线**：VMAF/SSIMULACRA 比对前剥离合成颗粒（Netflix 要求）

风险清单：
- 4:2:2 源必须降色度（XAVC-S 素材的保真损失）
- 4K60 吞吐 0.1-0.5x 实时（本机）——仅夜间批量/小素材集
- FGS 设备兼容非安全默认；AV1 不支持隔行
- 近透明/细节敏感场景 x265 slow 仍更稳——**SVT-AV1 档与 x265 档是
  场景互补而非替代**（体积敏感+重颗粒 → SVT；细节敏感 → x265）

## 附录 A. 证据与存档

- 官方文档：docs/reference/svt-av1/（根目录 Parameters/CHANGELOG/
  CommonQuestions + official/ 6 份 + src/ 源码含 CHANGELOG/grainSynthesis.c）
- PSY 调研：docs/reference/svt-av1/psy/（24 份 + 00-sources-index.md 索引）
- 社区实践调研：docs/reference/svt-av1/community/（22 份 + 主报告
  svt-av1-archival-community-report.md）
- 主线调研：docs/reference/svt-av1/{quality,speed,templates,windows}/（55 份）
  + 主报告 archival-feasibility-report.md + 一手核实 primary-source-findings.md
  + 速度/内存汇总 svt-av1-speed-memory-findings.md
- **诚实性缺口**（调研标注，供复核）：MSU 官网页抓取 502（51-65% 经社区
  报告确认，建议回访官网复核）；"4K 10bit preset0-6 fps"与"preset4vs6
  官方 BD-rate"无权威公开表（需自测）；libaom 精确速度倍数未可靠量化
- 本机实证：work/av1_feasibility/（4K60 基准、编码+封装+解码链路）
- 关联文档：av1_feasibility_report.md、av1_hw_tuning_guide.md、
  x265_production_assessment.md、FINAL_REPORT.md
