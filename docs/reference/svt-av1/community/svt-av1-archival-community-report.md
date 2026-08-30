# SVT-AV1 视频归档社区实践调研报告（Doom9 / r/AV1 / r/DataHoarder / VideoHelp / AV1 Discord 生态）

> 用途：为「1KeyTranscoder」归档可行性报告提供实测证据。
> 方法：抓取 Doom9 论坛帖、r/AV1、r/DataHoarder（含 arctic-shift/pullpush 归档检索）、ab-av1/av1an/SVT-AV1 官方文档与 issue，并交叉核对。
> 约定：每条结论标注【共识】（多方独立复现/多人一致）或【个案】（单用户、单一测试、单帖）。原始文档已存于本目录（见文末清单）。
> 基准（已确认，不重查）：MSU 2023-24 4K 10bit Slow 中 SVT-AV1 只需 x265 的 51–65% 码率；iXBT 2026 4K HDR 近透明段 x265 slow 在 40/20 Mbps 的 VMAF 略高于 SVT-AV1 preset5。

---

## 0. 一句话结论

社区对「SVT-AV1 做归档」的真实共识是**有条件的推荐**：

- **默认推荐路径**：10-bit、preset 4（质量优先）或 preset 5（平衡）、CRF 18–24（1080p 近透明）、`film-grain` 按源颗粒 8–20 且 `film-grain-denoise=0`，从 remux/BD 高质量源一次编码，保留原始介质。【共识】
- **关键保留**：film-grain 合成（FGS）不是「安全的默认开启项」——它是环外后处理，解码端（电视/播放器/软解）实现不一，会干扰 VMAF，且对「视觉无损归档」本质做不到。【共识】
- **明确分工**：重颗粒 + 求最小体积 → AV1+FGS；绝对细节保真/近透明 → x265 或高码率；吞吐/功耗优先 → 硬件 AV1（Intel Arc / NVENC），但硬件编码以 2–3 倍体积换速度。【共识】

---

## 1. 归档级 preset / CRF / 10bit / grain 共识

### 1.1 preset 取舍（速度 vs 压缩率）

- 【共识】preset 分区被社区反复引用：**0–3 追求最大压缩率（VOD/归档）、4–6 平衡、7–10 实时**；且「preset 4 以下编码速度骤降」。来源：[Tenets of AV1 Encoding（cynthia2006 gist）](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)。
- 【共识】粗略对应关系：**preset 8 ≈ x265 medium，preset 6 ≈ x265 slow**。来源：[SVT-AV1 Encoding Guide（dvaupel gist）](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)。
- 【实测/个案】dvaupel 用 30 秒 1080p24 电影片段（带 grain 合成）实测：preset 3=781s、4=340s、5=231s、6=146s、7=115s、8=109s，**文件大小几乎不变（10.4–10.9 MB）**——即在 grain 合成参与下 preset 主要买时间、不买体积；作者结论「合理区间 4–8，6 是好起点，<4 很少值得」。[同源](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)
- 【共识】归档（非实时）主流落在 **preset 4 / 5**：
  - Doom9 归档党开场即问「preset 5 还是 4 适合 movie archiving？4 耗时约是 5 的两倍」，跟帖「preset 4 at 5000kb/s 效果绝佳，4 明显好于 5，5 也不错」；另一用户最终稳定在 HandBrake「AV1 10-bit、CRF 26、preset 5」。[Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)
  - r/AV1 大量归档命令都用 `-preset 4`（如 [crf 20 preset 4 g240 10bit 归档测试](https://www.reddit.com/r/AV1/comments/16kjcie/)、[家庭视频归档](https://www.reddit.com/r/AV1/comments/1dxkzgn/)）；更极致者上 preset 3（[BD 重编码](https://www.reddit.com/r/AV1/comments/1jg1846/)、[SVT vs NVENC 帖建议](https://www.reddit.com/r/AV1/comments/18l0k07/)）。
- 【个案】[Level1Techs《My format shifting journey》](https://forum.level1techs.com/t/my-format-shifting-journey-av1-h265-cpu-encoding-complete-pending-new-av1-encoders/236071) 系长期 CPU 归档实践帖，与上述 preset 4–6 选择一致（未抓全文，仅作索引）。

### 1.2 CRF 透明区间

- 【共识】CRF 语义：SVT-AV1 的 CRF 默认 `--rc 0` 恒质量模式，**CRF 30 大致等价 x265 CRF 21** 是社区常用换算起点。[dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95)
- 【共识】HD 起点 CRF 30–35、4K 再降。[cynthia2006](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695)
- 【共识】**1080p「近透明/肉眼不可区分」区间集中在 CRF 18–24（preset 4–5 + 10-bit）**：
  - crf 20 preset 4：VMAF 无明显下降、与源不可区分（除非暂停放大）。[r/AV1](https://www.reddit.com/r/AV1/comments/16kjcie/)
  - crf 24 preset 4：VMAF 94–98.5（家庭视频）。[r/AV1](https://www.reddit.com/r/AV1/comments/1dxkzgn/)
  - crf 19 preset 3（BD）、crf 18 preset 4（Oppenheimer 对比）也被多次用作「高质量档」。[r/AV1](https://www.reddit.com/r/AV1/comments/1jg1846/) / [r/AV1](https://www.reddit.com/r/AV1/comments/18l0k07/)
- 【个案·争议】透明标准的认知差异很大：Doom9 用户 birdie 反驳「CRF 26 是彻底 blurfest，只配编码动画；透明编码=细节 100% 匹配，svt-av1 即使 CRF 12 也做不到」；而 ShortKatz 坚持「我的 CRF 26 不是 blurfest，差异轻微到可接受」。[Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)
- 【个案】手机家庭视频（iPhone 源，已较低质量）用户用 SVT-AV1-PSY preset 2 tune 3 film-grain=8，发现要到 CRF 40–50 才落在 6 Mbps，且「CRF 40 与 50 视觉无差」，对「你们怎么都 CRF 20+」表示不解。[r/AV1](https://www.reddit.com/r/AV1/comments/1iqvlb0/)

### 1.3 10-bit 是否标配

- 【共识】**是**。即便 8-bit 源也普遍 `-pix_fmt yuv420p10le` 编码以抑制 banding/量化误差；「10-bit 不代表 HDR，也不会显著增大文件」被明确写入指南。[cynthia2006](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695) 且几乎所有 r/AV1 归档命令行均带 10-bit。
- 【共识·注意】10-bit 的代价在**解码端**而非编码端：早版本（v0.9）「10-bit 播放在某些场景卡顿」，且 2026 年设备实测 **10-bit AV1 解码覆盖 ≈91%，但 10-bit 硬件编码仅 ≈8%**（能解不能编）。[dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95) / [WebCodecs 2026 设备数据](https://webcodecsfundamentals.org/datasets/codec-analysis-2026/)
- 【个案】[「Blurry videos only with 10-bit SVT encoder」](https://www.reddit.com/r/AV1/comments/1d7ysen/)：某用户 10-bit 输出比 8-bit 更糊/更发白，换 8-bit 反好转（个案，疑与色彩/重采样配置有关）。

---

## 2. film-grain 合成（关键）

### 2.1 机制与共识：它是「环外后处理」，不是编解码环内工具

- 【共识】FGS 两步：编码端 Wiener 去噪 + 在平坦块估计噪声模型参数（AR 模型），解码端按参数 + 高斯噪声模板重新合成颗粒。官方明确「去噪后的图用于编码统计与 PSNR 计算，颗粒合成被视为编码器之外的过程」。[SVT-AV1 Appendix-Film-Grain-Synthesis](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Appendix-Film-Grain-Synthesis.md)
- 【共识】因此 FGS 是**规范化后处理**，解码器「可以完全解码但不加回颗粒」——Doom9 专家 benwaggoner 多次强调：「有很多 AV1 解码设备不支持 FGS 或实现有 bug；不是此刻安全的『始终开启』功能」；「有的设备解码了但不加回颗粒，有的暂停时颗粒会随机变化」；「颗粒尺寸是按显示分辨率而非内容渲染（已知缺陷）」。结论：「**AV1 与 AV1+FGS 几乎可视为不同 codec，需要不同的编码**」；「若我不能指望 FGS 被使用，就直接不用 FGS 编码，码率更高但外观可预测」。[Doom9 t=184502](https://forum.doom9.org/showthread.php?t=184502) / [Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)

### 2.2 参数共识（film-grain 值）

- 【共识】官方默认推荐：**live-action 正常颗粒 film-grain≈8；更噪的 10–15**（SVT-AV1 CommonQuestions，被 Doom9 帖直接引用）。[Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159) / [SVT-AV1 CommonQuestions](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/CommonQuestions.md)
- 【共识】社区经验区间：Doom9 用户「我通常停在 10–20」；r/AV1 用户 NekoTrix「**1–6 轻微、7–15 明显、极重颗粒再往上**」。[Doom9 t=185686](https://forum.doom9.org/showthread.php?t=185686) / [r/AV1](https://www.reddit.com/r/AV1/comments/18l0k07/)
- 【共识】更细的分级（aomenc 口径，被广泛转发）：**零颗粒 4–8、轻微 8–14、中等 14–26、重颗粒 >26**；并主张「即使无颗粒素材也可用 4–8 的 grain 抑制 banding」。[shssoichiro gist](https://gist.github.com/shssoichiro/a46ff01db70243c1719479f6518ea34d)
- 【个案】重颗粒片实测：Blade Runner（原盘 64GB）用 film-grain=40 压到 15GB；现代片用 20、老片用 40 的分层做法。[r/AV1](https://www.reddit.com/r/AV1/comments/14333es/)；70s 恐怖片用户困惑「有人说别超 8、有人说 20+ 也行」。[r/AV1](https://www.reddit.com/r/AV1/comments/14q8urv/)

### 2.3 film-grain 与 denoise 的交互（关键坑）

- 【共识】`film-grain-denoise` 默认开启，且**去噪强度随 film-grain 值增强**——「把 film-grain 设太高或开启 denoise 可能删除精细纹理」。官方/指南/issue 一致警告。[cynthia2006](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695) / [ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)
- 【共识·参数澄清】`film-grain-denoise=0` **不是关掉 FGS**，而是「编码原始图而非去噪图」；去噪强度仍由 film-grain 值控制；关闭后源噪点会被正常压缩（码率上升）。[Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)
- 【共识】**内部去噪「很粗暴」**，多人实测认为 `film-grain-denoise=0` + 外部去噪（nlmeans/hqdn3d）质量更好，但外部去噪极慢（nlmeans 全开可到 0.6 fps，比编码还慢）。[ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139) / [r/AV1](https://www.reddit.com/r/AV1/comments/18l0k07/)
- 【共识·注意】SVT-AV1 会打印警告「**preset >6 不建议使用 film-grain**（产生显著计算开销）」——即 grain 合成 + preset 4/5 才是效率合理区，preset 6 以上 grain 计算占比过高。[ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)
- 【实测/个案】film-grain 的编码减速：`film-grain=15` 使 fps 17→9，且 CPU 整体占用下降（计算瓶颈在 grain 分析）。[r/AV1](https://www.reddit.com/r/AV1/comments/1dqaond/)

### 2.4 grain 合成 vs 高码率保颗粒（文件大小 / 主观 / VMAF）

- 【共识】方法论红线：**计算 VMAF 前应关闭合成颗粒**——Netflix 官方回复「合成颗粒会干扰 VMAF，我们建议计算 VMAF 时禁用它」（vmaf#1192）；ab-av1 团队据此实现 `-export_side_data film_grain` 剥离再测。用「开 FGS 的 VMAF」对比会系统性低估，无法公正评价。[Netflix vmaf#1192](https://github.com/Netflix/vmaf/issues/1192#issuecomment-1654243689) / [ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)
- 【共识】码率收益上限：AV1 综述论文「重颗粒内容 FGS 可省至多 50% 码率」；benwaggoner「重颗粒可使所需码率比同内容无颗粒翻倍以上」。[ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139) / [Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)
- 【实测/个案】mr44er 在 1080p 颗粒素材（pixabay，preset 6）的系统对比结论：`film-grain=10/20` 比「无 grain」更小且更接近原片；`film-grain-denoise=0` 反使体积上升（counterproductive）；最终「任意合成颗粒都显得更锐、体积更小」，推荐 `film-grain-denoise=0 + 轻度外部去噪(hqdn3d 时域)` 组合。[ab-av1#139 实测段](https://github.com/alexheretic/ab-av1/issues/139)
- 【个案】r/AV1 用户的折中表述：「grain 10/20/30 在沙发距离观感差异不大，但文件体积差异巨大」；「编码出来的真颗粒 vs 合成颗粒——合成显得更锐」。[ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)

### 2.5 硬件解码兼容性（电视/播放器）

- 【共识】FGS 在解码端**实现不一**：部分设备「解码 AV1 但不加回颗粒」、部分「暂停时颗粒随机变化」；FGS 解码「CPU 开销出乎意料地高」（软解 dav1d 场景）。[Doom9 t=184502](https://forum.doom9.org/showthread.php?t=184502) / [ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)
- 【共识】Apple 生态是 AV1（含 FGS）硬解短板：Apple 无软件解码器、老 Apple Silicon 与全部 Intel Mac 无 AV1 硬解（Safari 仅 ~24%/33% 覆盖）。[WebCodecs 2026](https://webcodecsfundamentals.org/datasets/codec-analysis-2026/)
- 【个案·正面】Apple M1 起的视频解码器（AVD）被逆向确认**内置专用 film-grain synthesis 电路**——高端平台对 FGS 有硬解兜底。[r/AV1](https://www.reddit.com/r/AV1/comments/1794yx1/)
- 【共识·风险提示】若归档流可能被不支持/有 bug 的 FGS 解码器播放，最稳妥是**不用 FGS、直接高码率保颗粒**；否则「去掉颗粒层会彻底改变观感（源被去噪以适配 FGS）」。[Doom9 t=185159](https://forum.doom9.org/showthread.php?t=185159)

---

## 3. SVT-AV1 vs x265 vs 硬件 AV1（NVENC）归档对比

### 3.1 SVT-AV1 vs NVENC AV1（最直接实测）

- 【实测/个案】[SVT vs NVENC（Oppenheimer 1080p BD，源 42GB，VMAF harmonic mean）](https://www.reddit.com/r/AV1/comments/18l0k07/)：

| 编码器 | 设置 | 体积 | 耗时 | VMAF |
|---|---|---|---|---|
| SVT-AV1 | preset 4, crf 18 | 4.3 GB | 8 h | 94.2% |
| SVT-AV1 | preset 8, crf 20 | 3.8 GB | 0.5 h | 93.1% |
| NVENC | slowest, CQ 15 | 4.3 GB | 13 min | 94.8% |
| NVENC | slowest, CQ 18 | 14.3 GB | 12 min | 94.9% |
| NVENC | slowest, CQ 30 | 3.6 GB | 12 min | 93.4% |

  即**同体积下 NVENC 略高 VMAF 但需要比 SVT 快 ~40×**；同一 VMAF 档 NVENC 体积是 SVT 的 2–3×。
- 【共识】定性结论（该帖高赞评论 + 多个独立测试）：「**NVenc 从来不是归档/制作工具**，是流媒体卸负载方案」；「低码率下 SVT 完胜 NVenc——约 2000 kbps@1080p 以下 SVT 开始大幅反超」；「NVENC p7+multipass 满血也打不过 SVT preset 13（体积匹配时）」；「硬件编码器为刷 VMAF 而调优，以牺牲真实画质为代价，不适合评价」。多位用户建议归档「用你能承受的最慢软编 preset」。[r/AV1](https://www.reddit.com/r/AV1/comments/18l0k07/)
- 【个案·较新】[什么值得买（RTX 50）《AV1 软编一定比硬编画质好？…一个开关翻盘》](https://post.smzdm.com/p/apqxomp0/) 显示较新 NVENC 在特定开关下接近软编（仅标题索引，未抓全文，谨慎引用）。

### 3.2 SVT-AV1 vs x265（归档场景）

- 【共识】高码率/近透明区间 **x265 细节保真仍略优**，与 iXBT/给定基线一致；多名用户描述「AV1 抹细节/抹糊纹理、尤其运动场景，x265 能解决」——[「Is it possible to preserve more detail at high bitrates」](https://www.reddit.com/r/AV1/comments/1gvhqjm/)、[「AV1 detail loss vs x265 (v1.7 worse than v1.5)」GitLab#2116](https://www.reddit.com/r/AV1/comments/17me48j/)、[「Svt-av1-psy refine edges but details loss」](https://www.reddit.com/r/AV1/comments/1ffnjts/)。这与「AV1 低码率/重颗粒（配 FGS）才显著占优」的定位一致。
- 【共识】重颗粒场景 AV1+FGS 反转：同一用户也承认「重颗粒 10 Mbps 都不够看，AV1 denoise+grain 能压到 2–5 Mbps 且观感更好」。[r/AV1](https://www.reddit.com/r/AV1/comments/17me48j/)
- 【个案·反例】[DataHoarder 批量转换实测](https://www.reddit.com/r/DataHoarder/comments/1iat7pd/)（4K 13GB H264 源）：CPU x265 竟比 CPU SVT-AV1 更小（1.27GB vs 2.75GB，preset 设置不同，个案，提示 preset/参数未对齐时结论易误导）。
- 【共识】「grain 保真 vs 体积」的两难被直接提出：**[「Anyway to preserve the film's actual grain structure in AV1」](https://www.reddit.com/r/AV1/comments/1e0zgji/)（13 票）** ——「如果我不希望真颗粒被抹掉/替换，是否该直接留在 x265？」是社区对 AV1 归档的最高频保留意见。

### 3.3 「存储成本 vs 解码生态 vs 编码耗时」权衡共识

- 【共识】DataHoarder 存在清晰两派，且都被高票认可：
  - **质量/压缩率派**：CPU SVT preset 6、CRF 30 的「[Squishing your library to AV1 is worth it](https://www.reddit.com/r/DataHoarder/comments/1mn72yv/)」（1152 票），回收到 ~1TB 且「看不出差别」。
  - **吞吐/功耗派**：Intel Arc 硬件 AV1 的「[Tdarr saved me 132TB](https://www.reddit.com/r/DataHoarder/comments/1lq49o4/)」（1539 票），220TB→88TB，靠 GPU 一年 24/7 跑完。
- 【共识】硬件编码的定位「**牺牲压缩换速度，质量（CQP 下）大致不损失**」被独立论证；软件编码在**固定低码率**下才明显赢，固定质量（CQP）下硬件只是文件更大。[DataHoarder](https://www.reddit.com/r/DataHoarder/comments/1oje5m9/)
- 【个案】动漫特例：SVT-AV1 preset 4 RF19 把动漫 BDremux 一集 7GB→300MB，而 NVENC slowest 只能到 1.7GB——静态内容上软编压缩优势被放大。[DataHoarder](https://www.reddit.com/r/DataHoarder/comments/1r5igc6/)
- 【共识】解码生态兜底：**AV1+HEVC 覆盖 99.73% 解码会话**；纯 AV1 若遇 Safari/旧设备需回退。[WebCodecs 2026](https://webcodecsfundamentals.org/datasets/codec-analysis-2026/)

---

## 4. chunked / 并行归档（av1an、segment、faststart）

### 4.1 av1an 与 --target-quality

- 【共识】av1an 是分段并行的事实标准；`--target-quality`（支持 **VMAF / SSIMULACRA2 / Butteraugli / XPSNR**）「按场景自动找 CRF」同时达到：场景间视觉一致、复杂段给足码率、简单段省码率。[Av1an TargetQuality 官方文档](https://github.com/rust-av/Av1an/blob/master/site/src/Features/TargetQuality.md)
- 【共识·推荐】`--target-quality 95`（VMAF）是常见归档档；社区同时推荐 **SSIMULACRA2 / XPSNR 优先于 VMAF**（VMAF 可被锐化/对比度滤镜「作弊」、硬件编码器专为刷 VMAF 调优）。[r/AV1](https://www.reddit.com/r/AV1/comments/18l0k07/)
- 【坑·实测】av1an 早期 `--vmaf_target` **在 90 分以下显著 overshoot**（90–98 区间较准）。[r/AV1《Accuracy of Av1an's --vmaf_target》](https://www.reddit.com/r/AV1/comments/gziu25/)

### 4.2 场景切分与关键帧（核心坑）

- 【共识】**SVT-AV1 不在场景切换处插关键帧（即便 `scd`）**，社区明示「若要在场景变化处插关键帧，需第三方切分工具（av1an）」。指南原文 + 社区独立讨论一致。[cynthia2006](https://gist.github.com/cynthia2006/4ea651a74b0f09e7ea519cfa5f33c695) / [r/AV1《Can we stitch AV1 together between keyframes》](https://www.reddit.com/r/AV1/comments/146x1g5/)

### 4.3 批处理其他坑

- 【坑/个案】chunked 编码音频同步问题（Neav1e chunk 模式「Audio desync」）。[r/AV1](https://www.reddit.com/r/AV1/comments/12u2phe/)
- 【坑/个案】长编码崩溃丢成果（「3 天编码被 ffmpeg 崩溃废掉」）——av1an 的断点续传/worker 容错价值在此。[r/AV1](https://www.reddit.com/r/AV1/comments/158oapa/)
- 【坑/个案】[「Corruptions in output files」](https://www.reddit.com/r/AV1/comments/1gc6br9/)：单进程 crf25/preset4/film-grain36 输出出现灰屏/花屏段。
- 【共识】**faststart 与「归档」关系不大**：它是 MP4 的 moov 前置标志，服务网页渐进式下载/流式首帧；社区归档普遍用 MKV 容器，未见把 faststart 当作归档最佳实践的共识——它属于「交付/网页」而非「归档」参数。未检索到 faststart 作为 AV1 归档批处理坑点的实质讨论。

---

## 5. 长期归档关注点

### 5.1 解码器演化 / 硬件解码成熟度

- 【共识·数据】AV1 Profile 0 8-bit 解码覆盖 **≈91.5%**（1M+ 真实设备，2026）；**10-bit 解码同样 ≈91%，但 10-bit 编码仅 ≈8%**（能解不能编）；**AV1+HEVC 覆盖 99.73%**；Safari 是唯一短板（macOS ~24% / iOS ~33%），因 Apple 无软件解码器、老设备无硬解。[WebCodecs 2026](https://webcodecsfundamentals.org/datasets/codec-analysis-2026/)
- 【共识·担忧】旧 HTPC/NAS 无 AV1 硬解只能软解（dav1d），CPU 负担重；FGS 解码额外吃 CPU。社区直呼「最讨厌的就是为硬件加速一直追赶」。[DataHoarder《playing catchup on hardware acceleration》](https://www.reddit.com/r/DataHoarder/comments/kxxzoj/) / [ab-av1#139](https://github.com/alexheretic/ab-av1/issues/139)

### 5.2 NLE（Premiere / Resolve）兼容性

- 【共识】**导入/解码**：Premiere 近年版本已支持 AV1 导入（GPU 辅助时间线更流畅）；**编码导出**长期是 feature request（Adobe 社区「Please add AV1 encoding support. Other free software support it for more than a year.」）。[minitool《Importing AV1 in Premiere》](https://moviemaker.minitool.com/news/premiere-pro-av1.html) / [Adobe 社区](https://community.adobe.com/t5/premiere-pro-ideas/support-av1-video-encoding-and-decoding/idc-p/14492911)
- 【共识】DaVinci Resolve 19 起加入 AV1 编码（依赖 AMD/Intel/NVIDIA 硬件编码）；免费版 Resolve 的 AV1 支持受限（Linux 上 MP4/AV1 支持见 Arch wiki 记录）。[Blackmagic 论坛](https://forum.blackmagicdesign.com/viewtopic.php?p=1075133) / [Intel 页面](https://www.intel.com/content/www/us/en/products/docs/discrete-gpus/arc/creator/partners/davinci-resolve-studio.html)
- 【共识·结论】SVT-AV1 流**不是 NLE 编辑中间格式**：社区共识是归档用 AV1 播放/收藏，编辑仍以 ProRes/DNxHD/H.264 中间片工作。

### 5.3 转码世代损失与「数十年」担忧

- 【共识】DataHoarder 反复强调「**不要压缩已压缩的内容**」与世代损失；重编码应从 remux/BD 高质量源起步并保留原始介质。[DataHoarder](https://www.reddit.com/r/DataHoarder/comments/16q5jm6/) / [r/AV1](https://www.reddit.com/r/AV1/comments/1ahhtw8/)
- 【共识·反完美主义】「最佳 codec 逐视频而变」——多年的多站测试结论是「没有普适最佳」，只能按视频逐一比较。[DataHoarder](https://www.reddit.com/r/DataHoarder/comments/1u3rdpj/)
- 【共识·悔改案例】「Help a noob decide which file should I keep」：用户后悔当年为省空间下 320p/128kbps，如今不可逆，反哺出「归档宁大勿损」心态。[DataHoarder](https://www.reddit.com/r/DataHoarder/comments/1qx6j3s/)
- 【共识】**AV1 不支持隔行内容**——模拟磁带/隔行源归档需先去隔行（或保留 H.264 隔行流）。[r/AV1《Can AV1 do anything for my interlaced content》](https://www.reddit.com/r/AV1/comments/ro3fvo/)
- 【个案·格式长期性】有用户选择 **FFV1/MKV**（无损、错误鲁棒）作为「数十年级」归档，AV1 仅作消费副本——体现「无损归档格式 vs 高效消费格式」分层。[DataHoarder《What format is best for long-term》](https://www.reddit.com/r/DataHoarder/comments/1lvebmu/)

---

## 6. 反面案例（失败 / 后悔 / 画质问题记录）

| # | 案例 | 症状 | 性质 |
|---|---|---|---|
| 1 | [「AV1 Removes All Details?」](https://www.reddit.com/r/AV1/comments/16j81v5/) | 「我遇过最差 codec，脸部像抹了 14 磅粉底」，SVT/AOM preset 6 RF 22 | 个案（参数/期望问题） |
| 2 | [「Svt-av1-psy refine edges but details loss」](https://www.reddit.com/r/AV1/comments/1ffnjts/) | 「清晰纹理全没了，x265 能解决」，preset 5 crf 25 tune 3 | 个案 |
| 3 | [「AV1 detail loss vs x265 (v1.7 worse than v1.5)」](https://www.reddit.com/r/AV1/comments/17me48j/) | 细节损失、尤其运动场景「washed up」，GitLab issue #2116 | 个案（跨版本回归） |
| 4 | [「Corruptions in output files」](https://www.reddit.com/r/AV1/comments/1gc6br9/) | crf25/preset4/film-grain36 输出灰屏/花屏/跳帧 | 个案（编码器 bug） |
| 5 | [「Need help archiving old TV show」](https://www.reddit.com/r/DataHoarder/comments/1n74m7y/) | CRF30/preset5/film-grain25/denoise0 下「脸部软、油、塑料感」 | 个案（低质量源 + 高 CRF） |
| 6 | [「Yellow tint on AV1 encodes」](https://www.reddit.com/r/AV1/comments/1ed6179/) / [「Color shift encoding in AV1」](https://www.reddit.com/r/AV1/comments/1k462me/) | film-grain/10-bit 下轻微偏色/黄绿偏移 | 个案（色彩管理） |
| 7 | [「Banding in dark areas」](https://www.reddit.com/r/AV1/comments/1jd8ofc/) | CRF35/preset2 暗部渐变 banding | 个案（高 CRF + 8-bit 源） |
| 8 | [「Blurry videos only with 10-bit SVT」](https://www.reddit.com/r/AV1/comments/1d7ysen/) | 10-bit 输出比 8-bit 更糊/发白 | 个案（配置） |
| 9 | [「ffmpeg crash (svt-av1 related?)」](https://www.reddit.com/r/AV1/comments/158oapa/) | 3 天编码因崩溃报废 | 个案（稳定性） |
| 10 | [「File sizes identical with/without film-grain」](https://www.reddit.com/r/AV1/comments/1fn6rj0/) | 某版本后 grain 不再减小体积（困惑） | 个案（版本行为变化） |
| 11 | [「Does it make sense to bulk convert all video files」](https://www.reddit.com/r/DataHoarder/comments/1iat7pd/) | 旧/低分辨率格式转 AV1 常「输出比输入还大」 | 共识倾向（低质量源勿转） |
| 12 | 8-bit 源强转 10-bit 播放「stuttering」 | 解码性能问题 | 个案（旧硬件）[dvaupel](https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95) |

> 反面案例整体模式：问题集中在（a）高 CRF/高 preset 下的细节抹除、（b）film-grain 带来的偏色/体积异常/输出损坏、（c）低质量源二次压缩、（d）旧设备解码。**没有发现「SVT-AV1 归档后文件打不开/无法长期保存」这类容器/格式层面的硬失败**。

---

## 7. 对 1KeyTranscoder 的可落地结论

1. **归档档建议**（非 XAVC 经典路径）：`-preset 4`（质量）或 `5`（平衡）、`-crf 18–24`、`-pix_fmt yuv420p10le`、`keyint≈240`（24fps 的 10 秒）、`tune 0`。
2. **grain 策略**：检测到明显颗粒且**解码端可控（现代设备）**才开 `film-grain=8–20 + film-grain-denoise=0`；否则**关闭 FGS、直接适当降 CRF 保颗粒**，避免「去噪后无颗粒回补」的观感崩坏。做 VMAF 验收时必须剥离合成颗粒再测。
3. **与 x265 的分工**：近透明/细节敏感素材（本项目 iXBT 基线）x265 slow 仍是更稳选择；AV1 用于「体积敏感 + 重颗粒 + 可接受环外颗粒」的场景。
4. **长期归档兜底**：保留原始介质 + 高质量源；不把 AV1 当唯一「数十年」格式（可考虑 FFV1/MKV 无损主档 + AV1 消费副本分层）；明确 AV1 不支持隔行。
5. **并行批处理**：用 av1an（场景切分 + `--target-quality`）弥补 SVT-AV1 不插场景关键帧的短板；注意 VMAF<90 时 target-quality 的 overshoot，优先 SSIMULACRA2。

---

## 附：原始文档清单（本目录 `docs/reference/svt-av1/community/`）

- `doom9_svtav1_settings_recommend.html/.txt` — Doom9 t=185159（preset/CRF/透明争议 + FGS 兼容性）
- `doom9_svtav1_filmgrain_model.html/.txt` — Doom9 t=184502（FGS 环外后处理机制）
- `doom9_filmgrain_value.html/.txt` — Doom9 t=185686（film-grain 取值）
- `doom9_svtav1_hdr.html/.txt` — Doom9 t=186319（SVT-AV1-HDR/PSY fork，Tune 3 grain）
- `svtav1_encoding_guide_dvaupel.md` — SVT-AV1 编码指南（preset 实测表、CRF 换算）
- `tenets_svtav1_cynthia2006.md` — AV1 编码要义（preset 分区/10bit/场景切分）
- `shssoichiro_grain_synth_advice.md` — 颗粒分级表 + aomenc 设置
- `svtav1_appendix_film_grain.md` — SVT-AV1 官方 FGS 附录
- `abav1_issue139_grain_vmaf.txt` — grain vs VMAF 完整讨论 + mr44er 实测数据
- `av1an_target_quality.md` — av1an --target-quality 官方文档
- `reddit_av1_archive.txt` / `reddit_av1_grain.txt` — r/AV1 归档/颗粒帖（含分数、URL、正文）
- `reddit_datahoarder_av1.txt` — r/DataHoarder AV1 相关帖（100 篇）
- `reddit_svt_vs_nvenc_post.txt` / `reddit_svt_vs_nvenc_comments.txt` — SVT vs NVENC 实测 + 评论
- `webcodecs_device_support.html/.txt` — 2026 年 1M+ 设备 AV1/HEVC 支持数据

（本次调研未修改任何项目根目录的 .json / README.md / .py 文件。）
