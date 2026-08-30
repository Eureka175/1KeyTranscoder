# x265（ffmpeg / HandBrake 的 libx265）用于「视频存档」的社区共识调研报告

## 调研方法与来源可及性说明（务必先读）

- 主要一手来源为 **Doom9 论坛 HEVC 板块**（已直接抓取并逐帖阅读原始帖子文本），外加 Hacker News 上引述 DataHoarder/压制圈生态的讨论作为旁证，以及 VideoHelp 的 x265 软件评价页。
- **Reddit 直接抓取失败**：`www.reddit.com`、`old.reddit.com`、`api.reddit.com` 均返回 **403（按 IP 封禁）**；Pushshift 存档 API（pullpush.io）返回 **429 限流**。因此 r/DataHoarder、r/handbrake 的原文未能逐条抓取，只能以下述方式间接覆盖：
  - Hacker News 讨论串 `item?id=19090636`（主题即「x265 3.0 发布」，其中大量内容直接讨论数据囤积/压制场景的 x265 透明档编码）；
  - 通过搜索引到 r/DataHoarder 的 Lemmy 镜像，但镜像实例带 JS 工作量证明墙（haphash），无法脚本化抓取。
  - **结论：Reddit 的「原始帖子级」证据缺失，本报告如实标注，不用二手转述冒充 Reddit 原文。**
- **VideoHelp 论坛**搜索页需要登录态（vBulletin 表单），无法匿名检索到具体存档帖；改用其公开的 x265 Encoder 用户评价页（28 条用户评论）作为 VideoHelp 社区的「个体口碑」样本。
- 所有日期为该帖/该楼层的发帖时间。所有「广泛共识 / 多人一致 / 个人观点」标签基于我能核验到的发言密度与是否存在明显分歧。

---

## 问题 1：透明 / 近透明存档的 CRF 推荐区间

**总体结论：存在一条清晰的「两派」分界线，不是单一共识。**

- 【多人一致·档案级「透明」派】Doom9 用户 jd17（长期做 Blu-ray 重编码，提供了实测码率统计）把自己的 1080p Blu-ray 归档分为两档：
  - 「参考级/透明」档：`--preset slow --crf 17 --no-sao --output-depth 10`，27 部片平均 **6902 kbit/s**（去掉 1 部 12.2 Mbps 的高噪点片后 6699 kbit/s，最低 3777 kbit/s）；
  - 「普通」档：`--preset slow --crf 19 --no-sao --output-depth 10`，28 部片平均 **5613 kbit/s**（去掉 2 部 14.3/19 Mbps 的极端噪点片后 4766 kbit/s，最低 2572 kbit/s）；
  - 他明确称这些是「transparent or near transparent」并估算同质量 x264 约需 12000–16000 kbit/s。
  - 来源：https://forum.doom9.org/showthread.php?t=175087 （第 23 楼，2017-12-12）；测试条件：1080p Blu-ray 源、x265 10bit、slow、no-sao。
- 【个人观点·同派】jd17 在 UHD 线程给出六条经验规则，其中第 4 条是 **「CRF 取值在 16–19 之间」**，并称「CRF18 和 19 也很好；**CRF20 开始在运动物体周围出现小伪影（"pixel clouds"）**」；同时承认在 65" 电视 3 米观看距离下「CRF17/18 其实有点过头」，但为「不留遗憾」仍选低 CRF。
  - 来源：https://forum.doom9.org/showthread.php?t=174679 （2017-08 前后）；条件：2160p / 10bit / BT.2020 HDR，HandBrake 便利封装。
- 【个人观点·同派】Doom9 用户 Winston_Smith_101：「1080p 源我用 `--crf 18 --preset veryslow --output-depth 10 --rskip --no-sao`，视觉上近乎无损；2160p 用 CRF 19–20 应该就够。」
  - 来源：同 t=174679 线程；条件：1080p 源、veryslow、10bit、no-sao。
- 【多人一致·「够用/务实」派】Doom9 用户 excellentswordfight 对 UHD 的实测结论与上述低 CRF 派**明显冲突**：
  - 他的 UHD 存档基线是 `--preset slow --crf 22`，称「在 55" OLED、2.5 米观看距离下对未拍胶片的内容是视觉透明的，10–20 Mbps（24/25p）」；
  - 用 Tears of Steel 无损源实测：「CRF19 产出约 40 Mbps，已属过度；**源在 CRF20 附近达到透明**；CRF22 文件只有一半大小、播放时透明」；「有时 crf18+tune grain 与 crf22 我看不出差别，但文件大 5 倍」；
  - 他同时给出 1080p 的建议起点：「`--preset slow --crf 19`，再按内容调」。
  - 来源：https://forum.doom9.org/showthread.php?t=174679 ；另见 https://forum.doom9.org/showthread.php?p=1871517 （VMAF 对比帖，2019，其中他重申「preset slow + crf19 是我的基线」）。
- 【专家观点·重要修正】benwaggoner（Amazon Prime Video 首席视频专家、Doom9 版主）：「绝对透明（absolutely transparent）场景 x264 仍更省心，除非不计时间；**『几乎透明（almost transparent）』场景 x265 才是碾压**」；1080p「大多数内容 5–8 Mbps 即接近透明，高噪点内容需要更高码率+通常要 grain 调校」。他还提醒 VMAF 的训练目标「就在『几乎透明』下方一点」，VMAF≈99 只能算「勉强接近透明」，不适合直接当「存档透明」的判据。
  - 来源：https://forum.doom9.org/showthread.php?p=1871517 （Blue_MiSfit 即 benwaggoner 的另一账号/角色，2019-04）。
- 【多人一致·CRF 映射】x265 与 x264 的 CRF 不是同一刻度：microchip8（ffx264/ffhevc 作者）：「**x264 CRF 18 ≈ x265 CRF 20（medium preset）**」；「x265 同码率比 x264 高约 30–35% 效率」；他自己用「x265 CRF 21 + qcomp 0.7 + 高 psy-rd/psy-rdoq」，与「x264 CRF 18 几乎最高设置」分不出差别。
  - 来源：https://forum.doom9.org/showthread.php?p=1927073 （2020-10）。
- 【个人观点·边界反例】HN 用户 stordoff（HandBrake）：x265 **Slow + RF16** 的成品「看片时反而觉得源像有问题」，且「RF16 已经几乎和原文件一样大，RF14 不如留原盘」；「Slow 8 小时 → Slower 近 2 天」。
  - 来源：https://news.ycombinator.com/item?id=19090636 （2019-02）。

> 小结：**「透明存档 = CRF 16–18、近透明 = CRF 19–22」这个常见说法在 Doom9 大体成立，但必须标注分歧**——「像素级透明/不留遗憾」派主张 16–19（且 20 开始现伪影），而「正常观看距离够用」派主张 19–22（且认为 <18 是体积暴增的过度投入）。二者差异主要来自「评判标准（贴屏逐帧 vs 正常距离播放）」，而非编码器行为。

---

## 问题 2：preset slow / medium / veryslow 的边际收益

**总体结论：社区对「slow/slower 是甜点、veryslow 收益很小但耗时暴增」有较强共识，但「甜点具体在 slow 还是 slower」略有分歧；没有找到社区自制的「每档 preset 码率节省百分比」成表数据。**

- 【专家观点·多帖重复】benwaggoner（三条独立发言高度一致）：
  - 「**veryslow 和 placebo 只带来很小的增量收益，代价是大量编码时间；slower 才是大部分 HEVC 优质工具真正生效的地方，slower↔veryslow↔placebo 的画质调校非常接近**。」（2021-02）
  - 「对典型连续调的电影/视频内容，**veryslow 与 slower 看起来几乎没差别**……`slow→slower` 可能看得出差别，而 `slower→placebo` 极少看得出。」（2021-02）
  - 「x265 提高 preset **更多时候是提升同码率下的画质，而不是降低码率**；有时更慢 preset 在 CRF 下反而码率和画质同时上升。」
  - 来源：https://forum.doom9.org/showthread.php?t=182400 、https://forum.doom9.org/showthread.php?t=182350
- 【个人观点·同向】jd17：「**slow 是质量/速度的甜点**；也有不少人把 slower 当甜点（认为它几乎吃满 x265 的效率潜力）。……medium 看起来不错，fast 尚可，再快就不建议。」「slow（及更慢）能保留明显更多细节。」
  - 来源：https://forum.doom9.org/showthread.php?t=175087
- 【个人观点·异议】Asmodian（Doom9 老用户）：「我从不觉得用 medium 代替更慢 preset 值得，**我本人一律用 veryslow**，placebo 对我都偏慢。」（即「甜点」对他而言在 veryslow 一侧。）
  - 来源：https://forum.doom9.org/showthread.php?t=174679 （2017-08）
- 【个人观点·直接印证「veryslow 不省码率」】VMAF 对比帖中某用户：「**VerySlow 只是更慢，分数不更高、也不省 bit**」；并给出参考「libvpx-vp9 CRF 35–36 ≈ x265 CRF 20」。
  - 来源：https://forum.doom9.org/showthread.php?p=1871517
- 【可用的耗时实测（不是码率节省表）】：
  - jd17（UHD/1080p，10bit）：`CRF17 medium + grain` 7.55 fps → `CRF19 slow + grain` 2.57 fps（**约 3 倍耗时**）→ `CRF15 slow + no-sao` 3.72 fps；另：`CRF17 medium` 3.59 fps → `CRF18 slow` 1.52 fps。
    - 来源：https://forum.doom9.org/showthread.php?t=174679
  - stordoff（HandBrake，电影长片）：「Slow 约 8 小时 → Slower 近 2 天」（约 5–6 倍）。
    - 来源：https://news.ycombinator.com/item?id=19090636
  - HD MOVIE SOURCE（UHD-BD 高码率实验）：加 `tskip/rect/amp/hme` 等额外工具后，「一部电影从 2 天 → 20+ 天」（约 10 倍），这属于超预设之外的极端调参，不直接等同 preset 档位。
    - 来源：https://forum.doom9.org/showthread.php?t=184399

> 小结：**「slow 是甜点、veryslow 边际收益很小但时间成本大增」被 benwaggoner 等多位资深用户反复表达，属于较强共识**；但具体「甜点」在 slow 还是 slower 有分歧（jd17=slow、benwaggoner 倾向 slower、Asmodian=veryslow）。**没有找到社区自制、有同一源同指标对照的「每档 preset 码率节省百分比」表格**——用户问题里设想的「veryslow 只比 slow 省 1–2% 却慢 2–3 倍」这类精确数字，本次未能在所抓来源中找到对应的实测表，只能给上面的耗时倍数与定性结论。

---

## 问题 3：8-bit 源用 10-bit x265 编码（抗 banding）

**总体结论：社区对「10-bit 能减少 banding、值得在兼容性允许时使用」有广泛共识；对「是否还有显著码率节省」的共识较弱（多位称 HEVC 上 10-bit 收益远不如 x264 时代）。未找到 8-bit vs 10-bit 的实测 VMAF/PSNR 数值或「省 10–20% 码率」的成表数据。**

- 【广泛共识·减 banding】多位用户一致：10-bit 明显减少 banding，尤其动漫/暗部渐变。
  - RanmaCanada：「10 bit 编码有助消除 banding，**动漫上尤其明显**，对暗部（blacks）也有帮助。」
  - jd17：「我**绝不会用 8-bit 编码 x265**。同 CRF 同设置下，10-bit 的渐变好得多；8-bit 里我一直看到难看的 banding，**即使降到 CRF13、即使加 --tune grain** 也还有。」
  - DJ Bobo：「从 8-bit 源做 10-bit 编码，banding 显著减少；**且同 CRF 下 10-bit 文件反而更小**（这违反直觉，我认为是 x265 8-bit 模式行为不佳——DVD 原盘完美渐变，8-bit 模式在 1500 kbps 下却严重 banding，而 CRF18 只给 700 kbps，荒唐）。」
  - excellentswordfight：「早期测试里不只是 banding，main10 全画面都有提升」（附 8bit/10bit 对比截图）。
  - 来源：https://forum.doom9.org/showthread.php?t=186813 （2026 起楼，含 2017 年 jd17 在 t=175087 中的同类发言）；测试条件：多数为 1080p / 8-bit 源、软件 x265、同 CRF 对比。
- 【理论解释·单人】Z2697：「只有源依赖抖动（dithering）、去掉抖动才会显著 banding 时帮助最大；若源基本不依赖抖动、或码率足够高，10-bit 帮助有限。」
  - 来源：https://forum.doom9.org/showthread.php?t=186813
- 【多人一致·但收益不如 x264 时代】关于「10-bit 的编码效率收益」：
  - GeoffreyA：「x264 时代 10-bit 效率提升显著；**x265 上这个收益不那么明显**。」
  - microchip8（ffx264/ffhevc 作者，纠正）：「x265 里真正有明显收益的是 **12-bit**（不是 10-bit）；我多年前用 12-bit 压一个 10-bit 都吃力的源，改善挺大，但兼容性差，之后没再碰。」
  - Asmodian：「x265 上 10-bit 收益不如 x264 显著，但 10-bit 仍更高效。」（引用 Ateme/x264 10-bit 带宽论文）
  - benwaggoner：「**10-bit 在 HEVC 中的画质影响小于 H.264**；只有当确认所有目标设备都支持时才用 10-bit（Smart TV 之外的移动设备并不都支持）。」
  - 来源：https://forum.doom9.org/showthread.php?t=175087 、https://forum.doom9.org/showthread.php?t=186813 、https://forum.doom9.org/showthread.php?p=1927073
- 【明确缺口】用户问题中设想的「10-bit 可省 10–20% 码率 / banding 少 10–20%」这类**实测 VMAF/PSNR 或码率百分比，本次在所抓社区帖中未找到**；能找到的是「x264 10-bit 历史上被普遍引用有约 20% 效率优势」的背景，以及「x265 上 10-bit 收益弱于 x264」的多人说法。如实标注：**缺实测数据。**

---

## 问题 4：x265 的质量争议（细节糊化/蜡感、暗部、噪点保留 vs x264 / SVT-AV1）

**总体结论：这些争议在社区里真实且长期存在，但多数源于「低码率/被饿码」场景与「SAO 默认开启」；在足够码率+关闭/收窄 SAO 后，多数争议可缓解。**

### 4.1 「x265 画面偏软/糊、蜡感/塑料感」

- 【广泛共识·现象本身】「x265 画面偏软（soft/notorious for a soft image）」是被反复提及的口碑。代表人物发言：
  - HD MOVIE SOURCE：「x265 以画面软著称」（发帖动机就是找增锐设置）；并直言「**SAO 太aggressive地软，高码率 4K UHD BD 上我完全不用 SAO**」。
    - 来源：https://forum.doom9.org/showthread.php?t=184399
  - VideoHelp 用户评论（多条独立）：「h265 仍会洗掉细节（washes out detail）/过度平滑（oversmooths）」「像蒙了一层膜（film over it），别人称之为 washed out」。
    - 来源：https://www.videohelp.com/software/x265-Encoder/reviews （Steve G 2017、Gaz 2020、Paulo 2018、Baldrick 2017 等）
  - **关键机制解释**：Doom9 用户 Boulder：「x264 与 x265 的区别在于——**码率被饿时，前者产生块效应（blocking），后者是模糊（blur）**，而块效应通常没那么扎眼。」（这是解释「x265 看起来比 x264 糊」的最常被引用的机制性说法。）
    - 来源：https://forum.doom9.org/showthread.php?p=1947403 （2021-07，grainy 对比帖）
- 【分歧·SAO 是否该关】这是最典型的分歧点，两派都有：
  - 「关 SAO」派：jd17：「`--sao` = 模糊、平滑；`--no-sao` = 细节、保留噪点、更接近源（论坛里有人开玩笑把 SAO 译作 smooth all objects）。」
  - 「别全关 SAO」派（较新版本观点）：BuccoBruce：「**别把 SAO 完全关掉**……现在不是 2015 年了，多数场景它不再摧毁细节；我用 `--limit-sao --selective-sao 1 --sao-non-deblock` 收窄而非关闭。」benwaggoner：「**`--selective-sao 2` 才是你该用的（且本应是 x265 默认）**；SAO 的价值 99% 只在 I/P 帧，用到 B/b 帧只是拖慢速度。」
  - Arhu（另一派/反例）：「我的结论相反：`--no-sao` = 脏、有毛边、人工噪点；`--sao` = 更忠实还原源；有说法是 <2.5 版本 no-sao 更好、之后未必。」
    - 来源：https://forum.doom9.org/showthread.php?t=175087 、https://forum.doom9.org/showthread.php?t=184399

### 4.2 暗部/阴影问题

- 【多人一致】暗部伪影被多次报告，常见对策是 `--aq-mode 3`：
  - Arhu：「默认 aq-mode 1 即使在低 CRF 下，暗部也常出现伪影（像蚊噪/振铃）；**换成 aq-mode 3 比降 CRF 更有效**。」
  - 官方文档（被原帖引用）：「aq-mode 3：自动方差+偏向暗部，**建议用于 8-bit 或低码率 10-bit 编码，以防 banding/块效应**。」
  - RanmaCanada：「10-bit 对暗部（blacks）也有帮助。」
    - 来源：https://forum.doom9.org/showthread.php?t=175087 、https://forum.doom9.org/showthread.php?t=186813

### 4.3 噪点/胶片颗粒保留 vs x264（及「--tune grain 是否有效」）

- 【个人实测·最重要的一条】tonemapped（Doom9）做了严格的同码率对照：**1080p、极重噪点源（源约 17 Mbps、8-bit）、2-pass @ 5000 kbps、x265 一律 10-bit**，主观打分（满分 10）：
  - **x265 软件 = 3/10，x264 软件 = 8/10**（NVENC 硬件 P/T 两代各 6/10、7/10）；
  - 他对 `--tune grain` 的评价：「**我第一个试的就是它，结果糟透了（atrocious）——颗粒变成静止的（static），比 medium 默认还差**。」并称「x265 的颗粒看起来是静止的，tune grain 似乎起反作用」；
  - 但同一人补充：「**约 9 Mbps 时 x265 颗粒保留很好，3.5 Mbps 轻颗粒也 OK**；只是『高噪点+低码率』组合崩。」并给出现实存档例：Charmed 剧集用 CRF21 + no-sao 压到约 1.2GB/集（原约 7GB），「约 90% 画质、1/7 体积」。
    - 来源：https://forum.doom9.org/showthread.php?p=1947403 （2021-07）
- 【专家观点·给具体参数】benwaggoner 在同上帖给出「重度颗粒内容」的推荐起点：`--preset slower -F 2 --aq-mode 1 --aq-strength 0.5 --cutree 0 --ipratio 1.2 --pbratio 1.1 --qpstep 1 --sao 0 --psy-rd 2.0 --psy-rdoq 5.0 --recursion-skip 0 --nr-intra 100 --nr-inter 250`；并警告 `--nr-inter` 对 1080p **高于约 250 就会产生「冻结颗粒」**，`--tune grain` 再叠加 `--nr-inter 2000` 必然「冻颗粒」。
    - 来源：同上
- 【多人一致·对 --tune grain 的怀疑】Emulgator：「tune grain 的意思是允许编码器扔进一些算力便宜的高频系数，**并非真正编码源里已有的颗粒**；很多 Blu-ray 重编码的『形变颗粒』就这么来的。」Boulder：「**x265 从没为颗粒保留做过调校**，多年来社区一直在争，但开发者从未把这点当高优先级……最有能力的人早就走了。」
    - 来源：同上
- 【旁证·「透明档才值得 x265」的演化史】HN 讨论（2019，x265 3.0 发布）：
  - kristofferR：「**直到最近 x265 才真正能用于透明档**；此前版本会抹掉胶片颗粒/数字噪点，导致同码率下画质还不如 x264。」「非 4K SDR 内容对压制者没有切换 x265 的动力——约 20% 码率节省 vs 10 倍编码时间、兼容性问题，不划算。」「除非是专家级编码者或不介意不透明，否则留在 x264。」
  - ksec：「PSNR/SSIM 做好只是 10% 的活，**把胶片颗粒做对要花非常久**……数字清洁过的内容用 x265 相对 x264 可省约 30%。」
  - 这是**个人观点但被多人附和**，且清晰点出「透明档」这一特殊场景。
    - 来源：https://news.ycombinator.com/item?id=19090636

### 4.4 与 SVT-AV1 的对比（透明/保真视角）

- 【个人观点·但直接命中问题】Scallywag（Doom9，2024-11）《In terms of fidelity all newer codecs are worse than x264!》：「我测了几个月，结论是**新编码器主要为中低码率优化，在透明档几乎/完全没有更高效率**。透明档下 x264 最好、x265 次之，其他编码器（VP9/AV1、可能还有 H266）画面糊、丢细节；**我也测过 SVT-AV1-PSY，问题依然存在**。」（1080p 为主）
  - 来源：https://forum.doom9.org/showpost.php?p=2009618 （2024-11）
- 【缺口】社区内「SVT-AV1 vs x265 在存档透明档」的**成表实测（同码率 VMAF/SSIM/主观）本次未找到**；能定位到的是上面的个体定性结论。如实标注。

### 4.5 「--no-sao / --tune grain / --psy-rd 是否解决问题」的直接回答

- `--no-sao`：**被普遍视为保留细节/颗粒的关键开关**（jd17、benwaggoner 的透明档起点 `--crf 18 --no-sao`、HD MOVIE SOURCE、Stereodude `-F 1 --no-sao` 等）；但较新版本观点建议用 `--selective-sao 2` 或 `--limit-sao` 收窄而非彻底关闭（benwaggoner、BuccoBruce）。→ **部分解决，且已是默认共识动作。**
- `--tune grain`：**社区评价偏负面**（tonemapped「颗粒静止/糟透」、Emulgator「并非真编码颗粒」、Boulder「x265 从未为颗粒调校」）；jd17 的替代结论是「`CRF17 slow no-sao` ≈ `CRF17 medium grain`，前者文件更小、后者快一倍」。→ **不推荐作为首选，多数人改用手动参数。**
- `--psy-rd / --psy-rdoq`：**分歧**。microchip8：「**高 psy-rd/psy-rdoq 几乎能彻底消除 banding，我从未见它毁细节/锐度**（最多让画面更『静态』）。」HD MOVIE SOURCE 则相反：「我不喜欢 RD/RDOQ 的一切，让画面太『数字感』，我把它俩都设 0。」→ **有用但对『数字感』接受度因人而异。**

---

## 附：来源清单（URL）

1. Doom9《x265 and preset influence with CRF ?》 https://forum.doom9.org/showthread.php?t=175087
2. Doom9《X265 Veryslow no better than slow???》 https://forum.doom9.org/showthread.php?t=182400
3. Doom9《x265 10 bit encoding from 8 bit source》 https://forum.doom9.org/showthread.php?t=186813
4. Doom9《x265 Sharpness and Detail (Best Settings?)》 https://forum.doom9.org/showthread.php?t=184399
5. Doom9《"Visually lossless" encoding for UHD sources》 https://forum.doom9.org/showthread.php?t=174679
6. Doom9《X265 vs x264 CRF values mapping》 https://forum.doom9.org/showthread.php?p=1927073
7. Doom9《Best x265 software and time/quality settings?》 https://forum.doom9.org/showthread.php?t=182350
8. Doom9《VMAF-comparison: x265 vs other encoders》 https://forum.doom9.org/showthread.php?p=1871517
9. Doom9《Updated (Grainy Source): x264 vs x265 vs NVENC vs QSV [2-pass @ 5000kbps]》 https://forum.doom9.org/showthread.php?p=1947403
10. Doom9《In terms of fidelity all newer codecs are worse than x264!》 https://forum.doom9.org/showpost.php?p=2009618
11. Hacker News《X265 3.0 released》讨论（引述 DataHoarder/压制圈透明档观点） https://news.ycombinator.com/item?id=19090636
12. VideoHelp《x265 Encoder user reviews》 https://www.videohelp.com/software/x265-Encoder/reviews

（Doom9 的 `p=...` 深层链接会直接落到对应帖/楼层；同一线程的不同楼层在正文中已标注发言者与日期。）
