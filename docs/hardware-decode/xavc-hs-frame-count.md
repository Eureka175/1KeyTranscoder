# Sony XAVC 硬解帧数异常 — NVEncC / QSVEncC 汇总

> **本文件是什么。** 把仓库里关于 **Sony XAVC 素材上 rigaya 硬件解码（`--avhw`）
> 静默丢帧**的全部已知内容汇总成一份自足文档；只读汇总，不修改任何代码/测试/`VERSION`/git。
> 每条结论都带来源路径，**无来源的结论一律写成"仓库内无证据"**。
>
> **两类材料的权威顺序**（下文凡有冲突处都按此标注）：
> * **integration session（新，权威"实际交付了什么"）** = [`README.md`](README.md)、
>   [`final-report.md`](final-report.md)、[`integration-test-matrix.md`](integration-test-matrix.md)、
>   [`toolchain-provenance.json`](toolchain-provenance.json)、[`patches/`](patches/)、
>   以及机器可读产物 `work/avhw_integration/`。
> * **research 归档（旧，权威"根因是怎么查出来的"）** = [`olddocs/docs/hardware-decode/`](../../olddocs/docs/hardware-decode/README.md)。

## 目录

[1 摘要](#1-摘要--一句话结论) ·
[2 症状](#2-症状) ·
[3 受影响素材矩阵](#3-受影响素材矩阵) ·
[4 可预测性 / 公式](#4-可预测性--公式) ·
[5 根因](#5-根因) ·
[6 为什么 ffmpeg 的 QSV/NVDEC 不受影响](#6-为什么-ffmpeg-的-qsvnvdec-不受影响) ·
[7 证据链](#7-证据链) ·
[8 上游补丁](#8-上游补丁) ·
[9 生产侧如何兜底](#9-生产侧如何兜底) ·
[10 已知限制 / 未证明项](#10-已知限制--未证明项) ·
[11 复现方法](#11-复现方法) ·
[12 证据索引](#12-证据索引) ·
[13 来源之间的矛盾与不确定项](#13-来源之间的矛盾与不确定项)

---

## 1. 摘要 / 一句话结论

**缺陷是什么。** rigaya 两个硬件编码前端 **NVEncC `--avhw`（reader `avcuvid`）** 与
**QSVEncC `--avhw`（reader `avqsv`）** 把"**第一个喂给解码器的 packet 的 PTS**"当成
"**应当呈现的第一帧的 PTS**"，于是丢掉了**presentation order 上排在第一个 keyframe
之前的那些 picture（leading pictures）**。Sony XAVC 恰好是这个假设不成立的流形态，
而 **DJI / x265 / synthetic 控制组一个不丢**。

**命中范围按工具分开陈述**（两者不同，不要合并成一句）：

| 工具 | 151 个 Sony 文件的结局 |
|---|---|
| **NVEncC `--avhw`** | **151/151 全部丢帧** —— 146 × XAVC HS **−3**，5 × XAVC S H.264 4:2:2 **−2** |
| **QSVEncC `--avhw`** | **146/146 XAVC HS 丢 −3**；另外 5 个 XAVC S H.264 4:2:2 是 **capability refusal**（`rc = −31`、无 reader、无输出文件），**不是丢帧**，故 QSV 侧可测的丢帧是 146/146 而非 151/151 |

归档里那句 "drops frames on **151/151** Sony files" 指的是 **rigaya `--avhw` reader
这一族**（`olddocs/docs/hardware-decode/README.md` §"Conclusion table"，同处自注
"297/297 measured runs (NVEncC 151/151, QSVEncC 146/146)"）；引用时必须带上这句限定，
否则会把 QSV 的 5 次**响亮拒绝**误读成静默丢帧。
来源：`olddocs/docs/hardware-decode/research-conclusion.md` §1–§2、
`olddocs/docs/hardware-decode/root-cause.md` RC-3、
`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §0。

**它不是什么（同样重要，逐条都有测量背书）：** ① 不是 FFmpeg 的 QSV / NVDEC 解码器缺陷
（同一颗硅上 FFmpeg 逐帧位相同：NVDEC 151/151、QSV 146/146 —— `nvdec.md` §6.3、`qsv.md` §5.3）；
② 不是共享 reader / demuxer（`rgy_input_avcodec.cpp/.h`）丢包，因为该文件给 `--avsw` 与
`--avhw` 共用而缺陷只在 `--avhw` 出现（`rigaya-avhw-analysis.md` §"Why the reader's keyframe
gate is a decoy"）；③ 不是 `FramePosList::setPocAndFix` 造成的**帧数**丢失（两个 reader 上
剪枝完全相同，而 `--avsw` 一帧不丢 —— `nvencc-second-path-analysis.md` §4.2、§7）；
④ 不是 edit list / `ctts` / B 帧 / tail-flush / 重复 / 重排（逐条被测量驳回 —— `root-cause.md`
RC-4、RC-8）；⑤ 不是 1KeyTranscoder 生产路径的当前缺陷（默认 `--hw-decode off` = `--avsw` 软解，
与 v0.6.2 逐字节一致 —— `docs/design/architecture.md` §8.1）。

---

## 2. 症状

对**任何**一个 Sony XAVC 文件执行 `--avhw` 编码，得到的是一个**看起来完全正常**的产物：

```
NVEncC  9.31 --avhw -c hevc --output-depth 10 --cqp 23
  -> encoded 27 frames        # 容器里是 30 个 sample
  rc = 0                      # 退出码 0
  （无任何 error / warning 文本）
产物可正常播放，ffprobe 可读，moov 完整
```

来源：`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §2.1（30 → 27，"both counts
are confirmed from the delivered file"）、`docs/hardware-decode/README.md` §"设计上必须知道的三件事"。

**三个"看起来是证据、其实不是"的东西**（矩阵把它们写成硬约束，`integration-test-matrix.md` §0）：
① `exit code == 0` 不是正确性证据；② "播放器能播放"不是（丢 3 帧的 MP4 完全可播放）；
③ "帧数相等"也不是 —— 丢帧是**连续头部截断**，纯计数可以过、画面已经错位。

**量级：丢弃量恒定、与片长无关，等于 leading picture 数。** 完整逐素材表见 §3.2；
一句话：**XAVC HS 丢 3**（30/90/120/150/360 帧的五个片段分别得到 27/87/117/147/357）、
**XAVC S H.264 4:2:2 丢 2**（195 → 193）、**DJI / x265 / synthetic / Sony-in-MKV 控制组丢 0**。
NVEncC 与 QSVEncC 完全一致，唯一例外是 QSV 在 H.264 4:2:2 上是 **capability refusal**（无输出）。
来源：`olddocs/docs/hardware-decode/root-cause.md` RC-2、`divergence.md` §7.3、
`nvencc-avhw-experiment.md` §4.1/§4.3、`docs/hardware-decode/final-report.md` §6 C 类。

> **注意区分两类"少帧"**（`olddocs/docs/hardware-decode/qsvencc-avhw-experiment.md` §2）：
> **`capability_refusal` 不是 `frame drop`** —— QSV 的 H.264 4:2:2 是**响亮拒绝**
> （`rc = −31`、无 reader、无输出文件），不是静默丢弃。

---

## 3. 受影响素材矩阵

### 3.1 语料规模与容器结构（实测）

| 项目 | 值 | 来源 |
|---|---|---|
| Sony 文件数 | **151**（另 30 个非 Sony 候选被排除、保留作负对照） | `olddocs/docs/hardware-decode/corpus.md` §3.2、`olddocs/docs/hardware-decode/test-results/summary.json` |
| 容器帧总数 / 总体积 | **188,475** 帧 / **64.87 GiB**（69,649,716,522 B） | `olddocs/docs/hardware-decode/README.md` §"Conclusion table"、`summary.json` |
| 分辨率 / 位深 / 类型 | 全部 3840×2160、10-bit、LongGOP | 同上 |
| 识别方式 | **从不看文件名**：`major_brand/compatible_brands` 含 `XAVC`（151/151）+ `rtmd` 数据轨（151/151）+ NonRealTimeMeta sidecar `manufacturer="Sony"`（137 个有 sidecar） | `olddocs/docs/hardware-decode/corpus.md` §3.1–§3.3 |
| `stts` delta / `ctts` / `stss` | delta 恒 `1001`（151/151）；`ctts` 全部存在（151/151，最大 offset 5005 / 3003）；`stss` **从不缺失** ⇒ 全部 LongGOP | `olddocs/docs/hardware-decode/corpus.md` §3.4、`summary.json` `corpus.distribution` |
| edit list | **151/151 都有**，`elst.media_time` = 2002（146）/ 1001（5），非 0 | 同上 |
| **第一个 keyframe 前呈现的 picture 数** | **3**（146 文件）/ **2**（5 文件） | 同上 |

### 3.2 逐素材类别

| 素材 | codec / profile | pixel fmt | 分辨率 | fps | GOP | 容器 | leading pictures | NVEncC `--avhw` 丢 | QSVEncC `--avhw` 丢 |
|---|---|---|---|---|---|---|---|---|---|
| **XAVC HS**（Group A，146 文件） | HEVC Main 10 @L5.1（`HEVC_3840_2160_M10P@L51HT`） | `yuv420p10le` | 3840×2160 | 60000/1001（59.94p） | 60（1.0 s） | MP4（`hvc1`），另有 MKV 变体 | **3** | **3** | **3** |
| **XAVC S**（Group B，5 文件） | H.264 High 4:2:2 @L5.1 | `yuv422p10le` | 3840×2160 | 30000/1001（29.97p） | 30（1.0 s） | MP4（`avc1`） | **2** | **2** | **拒绝**（`avqsv: codec h264(yuv422p10le) unable to decode by qsv.`，无 reader/无输出） |
| DJI（负对照，非 Sony） | HEVC Main 10 | `yuv420p10le` | 3840×2160 / 3840×2880 | — | — | MP4（无 edit list、无 `ctts`） | **0** | 0 | 0 |
| x265 重编码（控制） | HEVC Main 10 | `yuv420p10le` | — | — | — | MP4（有 2002 edit list + `ctts`） | **0** | 0 | 0 |
| synthetic `testsrc2`（控制） | HEVC | — | — | — | — | MP4 | **0** | 0 | 0 |
| Sony 流拷贝 → MKV（控制） | HEVC | — | — | — | — | **MKV，无 edit list** | 3 | **3**（→ 证明 edit list 不是触发器） | — |

来源：`olddocs/docs/hardware-decode/corpus.md` §3.3、§3.4、§3.6；
`olddocs/docs/hardware-decode/root-cause.md` RC-3、RC-4；
`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §4.1、§4.3；
`docs/hardware-decode/toolchain-provenance.json` `known_refusals`。

### 3.3 明确**从未**检出的素材（不得推断其行为）

XAVC S-I / All-I（`stss` 缺失场景）、8-bit XAVC、1080p / 非 4K、4:2:2 HEVC（XAVC HS 422）、
HLG / PQ HDR、损坏/截断的 Sony 容器、多 GPU 主机与 `--split-enc` 并行编码
（全程单 GPU；`--device 1` 直接失败 `Invalid Device Id = 1`）、QSVEncC 8.27–8.30、
NVEncC 9.31 以外的任何 revision —— **全部 `未验证`**。
来源：`olddocs/docs/hardware-decode/corpus.md` §3.5、`research-conclusion.md` §3.3 与 §5
（open items 1/5/6）、`final-report.md` §10。

---

## 4. 可预测性 / 公式

**预测式（从容器 box 直接算出，无需解码）：**

```
pictures_before_first_keyframe
    = |{ i : PTS[i] < PTS[first sync sample] }|          # 即 leading pictures 数

其中  DTS[i] = cumsum(stts)[i]                            # decode time
      PTS[i] = DTS[i] + ctts[i]                           # composition time

被测量的结果：
    avhw_frames = container_samples − pictures_before_first_keyframe
```

来源：`olddocs/docs/hardware-decode/root-cause.md` RC-3（公式与验证表）；
实现 `work/hwdecode/mp4struct.py::leading_picture_analysis`（引用自同一处）。

**验证范围与命中率：** NVEncC `--avhw` 实测 **151** 个文件、预测正确 **151/151（100 %）**；
QSVEncC `--avhw` 实测 **146** 个文件、预测正确 **146/146（100 %）**；两侧预测错误均为 **0**；
QSV 侧 **5** 次能力拒绝（全部 H.264 4:2:2）。实测丢帧分布 `{3: 146, 2: 5}`（NVEncC）/
`{3: 146}`（QSV）；reader 身份实测 `avcuvid` 151/151、`avqsv` 146/146
⇒ **297 / 297 个成功测量的运行全部命中**（脚本共发起 302 次运行，另 5 次是 QSV 拒绝）。
来源：`olddocs/docs/hardware-decode/root-cause.md` RC-3（"Full-corpus validation … 302 runs"、
"Validated on 297 / 297"）、`olddocs/docs/hardware-decode/test-results/summary.json`
`rigaya.predictor_validation`。

**关键性质 —— 它是"流形态"属性，不是 codec 属性，也不是容器属性：**
① **不是 codec 属性**：HEVC 与 H.264 都中招，只是常数不同（3 / 2）；
② **不是容器属性**：同一个 Sony 码流重封成 **MKV（完全无 edit list）照样丢 3**（`root-cause.md`
RC-4 变体 V2），而 x265 重编码**保留 2002 edit list 与 `ctts` 却丢 0**（V4，控制变量实验）；
③ **不是片长/内容属性**：30 → 10 170 帧的片段丢帧量恒定（`root-cause.md` RC-2）；
④ **源码侧独立推导**得到同一条公式：`targetStart = size − 1` 使丢弃数恒等于 leading picture 数
（`rigaya-avhw-analysis.md` §"Exact trigger"，标 `Confirmed`）。

> **边界（必须随引用带上）：** "任何**第一个 IRAP 在呈现顺序上不是第一个**的码流都会受影响"
> 这一句是**从机制推出的推断**，与全部实测一致，但**不是**在所有 Sony 素材上测过的测量结论。
> 来源：`olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` C2/P5 与 C3/N3。

---

## 5. 根因

两个前端**规则相同、代码不同**。它们共同实现的错误规则是：
> 把 `PTS(第一个被喂入的 packet)` 当作硬解输出的起点，丢掉所有在它之前呈现的 picture。
> 来源：`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §4。

### 5.1 NVEncC 9.31 —— 输出级过滤（已打补丁）

| 项 | 内容 |
|---|---|
| 站点 | `NVEncCore/NVEncPipeline.h`，`class PipelineTaskNVDecode`，`getOutputFrame()` |
| 上游 | `rigaya/NVEnc` tag **9.31**，commit `2cb9d810c045202548b98ff130b12bc764eb39ea` |
| 锚点状态 | `m_hwDecFirstPts`，在 `NVEncPipeline.h:1550`（归档另作 `:1548-1552`）由**第一个喂入的 packet** 的 PTS 赋值 |
| 丢弃点 | 归档引用 `NVEncPipeline.h:1665-1667`（drop loop `m_dec->frameQueue()->releaseFrame(...)`）；同文件 `:1679-1681` 是**第二条（死）头部丢弃循环** |
| 机制 | 输出循环缓冲 `dispInfoList` 直到出现 `dispInfo.timestamp >= m_hwDecFirstPts`；此刻 `targetStart = size − 1`，游标 `istart` 被推过前 `size−1` 项并 `releaseFrame()` 掉，末尾 emission loop（`:1685`）从 `istart` 开始，因此**永不发射**这些 picture |
| 为什么 Sony 触发 | decode order 第一个 packet 是 IRAP（`Set packet #0, pts 3003`），而呈现顺序前 3 张 picture 的 PTS 是 `0 / 1001 / 2002`，**都小于 3003** |

来源：`olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` §"Relevant source path"、
§"The drop site"、§"Exact trigger"；`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §3.1；
`olddocs/docs/hardware-decode/nvencc-patch.md` §3。**补丁本身见 §8.1。**

### 5.2 QSVEncC 8.26 —— 管线准入过滤（已打补丁）

| 项 | 内容 |
|---|---|
| 站点 | `QSVPipeline/qsv_pipeline_ctrl.h`，`class PipelineTaskMFXDecode`，`sendBitstream()` |
| 上游 | `rigaya/QSVEnc` commit `b14c9652b34b998351516cc63c86eb990c01c7c8`（= **8.26 (r4504)**） |
| 锚点状态 | `m_firstPts`，在 `:1066-1069` 由**第一个喂给 MFX 的 bitstream** 的 timeStamp 赋值；计数状态 `m_decFrameOutCount` **只在真正发射时**自增（`:1157`） |
| 原始条件 | `(!m_gotFirstPts \|\| m_firstPts <= surfDecOutTimestamp \|\| m_decFrameOutCount > 0)` |
| 机制 | MFX 按显示顺序输出，前 3 张（`ts 0 / 1001 / 2002`）都 `< m_firstPts (3003)` ⇒ 逐个被拒；而 `m_decFrameOutCount` 在拒绝期间**保持 0**，所以 `\|\| m_decFrameOutCount > 0` 这条逃生子句**永远不触发** —— 这正是"静态阅读以为最多丢 1 帧"错在哪里 |
| 实测账本 | `container 30 → reader packets 30 → FramePosList 28 → MFX 输出 30 → 被准入过滤拒 3 → 接受 27 → encoded 27` |

来源：`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §0、§2、§3.1、§5；
`olddocs/docs/hardware-decode/qsvencc-patch.md` §1、§2.1。**补丁本身见 §8.1。**

### 5.3 第三个站点：`FramePosList::setPocAndFix`（**未打补丁，且必须与上面分开**）

| 项 | 内容 |
|---|---|
| 站点 | `NVEncCore/rgy_input_avcodec.h:654-671`，`class FramePosList`，`setPocAndFix()`；`m_firstKeyframePts` 在 `:320-322` 设置。**NVEnc / QSVEnc / VCEEnc 三工具共享该文件** |
| 它做什么 | 把 `pts < m_firstKeyframePts` **且 `m_nextFixNumIndex <= 16`**（防 PTS wraparound）的**表项**从 frame-position list 里 `m_list.pop()` 掉 |
| 对 Sony 的实测效果 | 剪掉 `3` 条（XAVC HS）/ `2` 条（H.264 4:2:2）/ `0` 条（所有控制组）—— 与丢帧数**数值相同**，极易混淆 |
| **为什么它不是丢帧原因** | 剪枝在 `--avsw` 与 `--avhw` 上**完全相同**（`--log-framelist` 逐字节相同，`trim=3` 两侧一致），而 `--avsw` 一帧不丢。它是**表**缺陷，不是**投递**缺陷 |
| 它实际损害什么 | ① 那几帧的"原始时长"查询；② telecine flags；③ reader 自报（`N frames, End of file` / `--log-framelist` 在 XAVC 上系统性少报） |
| 处置 | **不改代码**。作为独立低危问题报上游；消费侧缓解 = **永不把 reader 自报当帧数**（已落实为生产设计） |

来源：`olddocs/docs/hardware-decode/nvencc-second-path-analysis.md` §1、§2、§4.2、§5、§6；
`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §7；
`olddocs/docs/hardware-decode/research-conclusion.md` RC-3；`docs/hardware-decode/final-report.md` §9。

### 5.4 哪些是"读源码"，哪些是"从行为推断"

| 主张 | 证据形态 | 标签 |
|---|---|---|
| 解码器把**每一张** picture 都产出了 | NVEncC 自身 trace 回调计数 `DecPictureDisplay` 30/30、195/195、360/360、105/105 | `已确认`（读工具日志） |
| 下游 `PipelineTaskCheckPTS` 看到的第一帧就是 IRAP（`orig_pts 3003`） | 工具 trace | `已确认`（读工具日志） |
| NVEncC 丢弃点是 `getOutputFrame()` 的 release loop（`:1665-1667`） | 读 9.31 源码 + 循环算术模型重现全部实测计数 | `已确认`（源码 + 模型），**但**：归档明确记录 `NVEncC64.exe` 里 FFmpeg 静态链接、无符号服务器，**未用断点实地确认**（`rigaya-avhw-analysis.md` §"Root cause" caveat） |
| QSVEncC 丢弃点是 `sendBitstream()` 的准入条件 | **从 pinned 源码构建并插桩**，`RGY_AVHW_TRACE` 逐帧打印 accept/reject | `已确认`（构建 + 插桩实测），比 NVEncC 一侧更强 |
| 共享 reader **不是**原因 | `rgy_input.cpp:629-692` 对 `--avhw` 和 `--avsw` 都构造同一个 `RGYInputAvcodec`；关键帧闸门 `:3405` 在所有 fixture 上都是 `offset 0` | `已确认`（源码 + 日志） |
| NVEncC 侧缺陷在 QSVEncC/VCEEncC 也存在 | 归档 R7 最初只是**预测**（"the same class of defect **likely** exists"），随后在 QSVEncC 上被独立定位并实测 | NVEncC 侧原为 `推断`，QSVEncC 侧已 `已确认`；**VCEEncC 仓库内无证据** |

来源：`olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` §"Root cause" caveat 与
C2/P2/P3；`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §3.1；
`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §1、§2.1。

---

## 6. 为什么 ffmpeg 的 QSV/NVDEC 不受影响

**是测量，不是推断。** 同一套 Sony 语料，FFmpeg 的硬件解码路径逐帧位相同于软解：

| 路径 | 结果 | 说明 |
|---|---|---|
| `qsv_native`（`-hwaccel qsv -hwaccel_output_format qsv -c:v hevc_qsv` + `hwdownload,format=p010le`） | **146 / 146 PASS**，`sequence_identical = true`，`all_sequences_bit_identical: true` | 5 个失败全部是 H.264 High 4:2:2 10-bit 的 **capability** 失败：`Error querying IO surface: unsupported (-3)`，`rc = 69`，0 帧，**响亮**，不是静默丢弃 |
| `nvdec_cuvid`（`-hwaccel cuda -c:v hevc_cuvid`） | **151 / 151 PASS**，零 `FRAME_DROP` / `FRAME_DUPLICATE` / `FRAME_REORDER` / `PTS_ERROR` / `FLUSH_LOSS` | 覆盖两种 codec、两种 chroma（含 QSV 没有的 H.264 4:2:2 10-bit 5/5） |
| 对照：rigaya `--avhw` | **151 / 151 全部丢帧** | 同一颗硅、同一个素材 |

来源：`olddocs/docs/hardware-decode/qsv.md` §5.3–§5.5、`§5.7`；
`olddocs/docs/hardware-decode/nvdec.md` §6.3、§6.4、§6.7；
`olddocs/docs/hardware-decode/divergence.md` §7.2、§7.4；
`olddocs/docs/hardware-decode/test-results/summary.json` `qsv` / `nvdec` 段。

**机制上的差异**（源码级对照，见 `olddocs/docs/hardware-decode/rigaya-avhw-analysis.md`
§"Why FFmpeg succeeds"）：

| 方面 | FFmpeg `-hwaccel` | rigaya `--avhw` |
|---|---|---|
| 帧投递 | `avcodec_send_packet` / `avcodec_receive_frame`，drain 到 EOF | decode thread → `cuvidParseVideoData` → display callback → `FrameQueue` |
| 首帧处理 | **无** —— 返回的每一帧都转发 | `getOutputFrame()` 丢弃所有低于第一个 packet PTS 的帧 |
| edit list | 仅作**时间戳平移**（`-ignore_editlist 1` 只把 `first_pts` 0 → 2002，帧数仍 360） | 不是触发器（已复核） |
| leading pictures | 解码并**发射** | 解码后被**下游丢弃** |

**决定性不对称：** FFmpeg **不存在**任何一个"把解码帧 PTS 与某个 first-PTS 比较、低了就丢"
的阶段；rigaya 有，而且该阶段**只在硬件路径上可达**。

> **方法学陷阱（归档专门记录）：** `-hwaccel cuda` 会打印
> `Stream #0:0 -> #0:0 (hevc (native) -> rawvideo (native))`，**看起来像软解**。
> 用 `-loglevel debug` 才能看到 `Selecting decoder 'hevc' because of requested hwaccel method cuda`
> → `Format cuda requires hwaccel hevc_nvdec initialisation` → `Loaded lib: nvcuvid.dll`。
> **解码器身份一律从这些行判定，绝不从 stream-mapping 字符串判定。**
> 来源：`olddocs/docs/hardware-decode/nvdec.md` §6.2、`root-cause.md` RC-6。

---

## 7. 证据链

### 7.1 判定方法（每个 C 用例必须六方对账）

| # | 来源 | 取得方式 | 可信度 |
|---|---|---|---|
| 1 | `container_expected` | **自己按字节解析** `moov/trak/mdia/minf/stbl/stsz`（`tests/hwdecode/probe.py`；`encoders/integrity.py::container_video_samples`）；非 ISOBMFF（MKV）才退回 libavformat 包计数 `ffprobe-packets` | **参考真值** |
| 2 | `reader_reported` | 工具日志 `N frames, End of file` | **不可信** —— XAVC 上因 `FramePosList::setPocAndFix` 系统性少报；只记录，不判定 |
| 3 | `encoder_input` | 工具日志 `encoded N frames` | 编码器自述 |
| 4 | `output_stream` | 产物容器自身的 sample table | 产物自述 |
| 5 | `independent_decoded` | `ffprobe -count_frames` | 独立实现 |
| 6 | `independent_decoded_alt` | FFmpeg `-progress` 帧计数 | 第二个独立实现 |
| +7 | `fingerprint` | 逐帧有序签名（mean Y/U/V、mean\|dY\|、首尾采样、4×4 KB chunk digest） | 顺序与内容证据 |

判定规则：**1 为真值**；3/4/5/6 任一不等即 FAIL；2 永不参与判定；内容一致性由 7 单独判定。
来源：`docs/hardware-decode/integration-test-matrix.md` §0.5、§C.0；
`docs/hardware-decode/final-report.md` §6 C.1。

### 7.2 逐帧指纹 / SHA-256 / PTS / keyframe 的具体实现

| 层级 | 方法 | 代码位置 |
|---|---|---|
| 历史（research）：exact tier | `decoded frame → scale=64:64:flags=bilinear → format=gray → showinfo → rawvideo`，每帧 4096 字节 → **sha1**；tolerant tier = 8×8 block means（`sig8`，128 hex） | `work/hwdecode/fingerprint.py`（引用自 `divergence.md` §7.1） |
| 历史（research）：rigaya A/B | 同一工具、同一输出配置（`-c raw --output-depth 10 --output-csp yuv420`），唯一变量是 reader；**组内字节精确**比较才可用 | `root-cause.md` RC-2 方法注 |
| integration：有序内容指纹 | `frame_signatures()`：逐帧 `[meanY, meanU, meanV, mean|dY|, Y[0], Y[-1], sha256(4×4KB chunks)[:16]]`，流式解码、**不落盘 raw**；`digest()` 对签名序列再取 sha256 | `encoders/integrity.py:496-539` |
| integration：包序列与 keyframe | `packet_manifest`：全序列 `(pts, dts, flags, size)` + keyframe 索引序列 + `manifest_sha` | `work/avhw_integration/results/results.json`（如 `HD-C01.evidence.packets`） |
| integration：产物字节 | 输出文件 sha256 | `results.json`（如 `HD-C01.evidence.sha256_patched_hw` = `ef621733631480ad…`，与 `sha256_patched_sw` 相同） |
| QSVEncC 侧（research） | 逐帧 **SHA-256** of raw output，两侧几何与 VPP 链相同 ⇒ patched `--avhw` vs `--avsw` **30/30 identical**；shipped `--avhw` `test[0]` 映射到 `ref[3]` | `qsvencc-root-cause.md` §6.1 |

> **跨管线比较必须用 tolerant tier。** rigaya 会做
> `yv12(10bit) → p010 → yv12(10bit)` 往返，luma 偏移 ±1 code：实测 25/30 完全相等、
> **30/30 在 2 个 code 以内**。只用字节精确 tier 会得出"每一帧都不同"的**假结论**。
> 来源：`olddocs/docs/hardware-decode/divergence.md` §7.1、`root-cause.md` RC-2 方法注。

### 7.3 原始证据落盘位置

| 内容 | 路径 |
|---|---|
| integration 83 条用例的完整证据（计数、指纹 digest、PTS/keyframe 清单、sha256、日志引用） | `work/avhw_integration/results/results.json`（83 条） |
| 扁平表 | `work/avhw_integration/results/results.csv`（84 行含表头） |
| 机器可读闸门 | `work/avhw_integration/results/summary.json`（83 PASS / 0 FAIL / P0 61/61，`READY FOR REVIEW`） |
| `--frames N` 三段 contract 表 | `work/avhw_integration/results/frames_contract.json` |
| 软件 golden baseline（6 个 fixture，独立重跑逐字节重新 derive） | `work/avhw_integration/baseline/*.json`（`_stability.json` + 6 条） |
| **每次编码的原始工具日志（判定证据）** | `work/avhw_integration/logs/*.log`（456 个文件，约 1 MiB） |
| 生成的 control fixtures | `work/avhw_integration/fixtures/`（`A_sony_copy.mp4`、`B_sony_copy.mkv`、`C_x265_reencode.mp4`、`D_synthetic_testsrc2.mp4`、`fixtures-report.json`） |
| 编码产物 | `work/avhw_integration/runs/` |
| research 侧机器可读结果 | `olddocs/docs/hardware-decode/test-results/{summary,corpus,comparison,qsv-results,nvdec-results,software-ground-truth}.json` |
| 原始 trace（research） | `work/hwdecode/raw/*`、`work/hwdecode2/raw/*`、`work/qsvavhw/trace/*.trace.txt`（引用自各归档文档） |

> **⚠️ 上表里 research 侧的 `work/*` 路径在当前 checkout 中已不存在** ——
> `work/` 一直是 gitignored，那些 research 分支的工作树与 harness 早已不在。
> 本机实测：`work/hwdecode/`、`work/hwdecode2/`、`work/qsvavhw/` **均不存在**
> （`work/hwdecode/fingerprint.py`、§7.2 引用的 `work/hwdecode/mp4struct.py` 同理）。
> 它们是**归档文档记录的历史位置**，不是可打开的现行证据。
> 现行可直接打开的证据只有三处：
> ① `work/avhw_integration/`（integration 侧，results / summary / frames_contract /
> baseline 7 文件 / logs 456 个顶层文件约 1.07 MiB / fixtures 5 / runs 433）；
> ② `olddocs/docs/hardware-decode/test-results/`（research 侧 6 个 JSON）；
> ③ `docs/hardware-decode/toolchain-provenance.json` 与 `patches/`。
> 引用 §7.2 的历史方法时，请把它当作**方法描述**引用，不要当作可复核的数据路径。

> **实测细节：** 正常编码时两个 reader **都不打印** `N frames, End of file`，
> 因此 `reader_reported` 字段实际为 `null`（`HD-C01.evidence.patched_hw.counts.reader_reported = null`）。
> 这是设计事实而非缺陷 —— 它本来就不被用作参考。来源：`final-report.md` §6 C.1。

---

## 8. 上游补丁

### 8.1 两个补丁做什么

| | NVEncC | QSVEncC |
|---|---|---|
| 补丁文件 | `docs/hardware-decode/patches/0001-nvencc-avhw-keep-leading-pictures.patch`（2802 B） | `docs/hardware-decode/patches/0001-qsvencc-avhw-keep-leading-pictures.patch`（2255 B） |
| **已提交版本的 sha256（本仓库实测）** | `2321ed72d21ca3483c755e082964606d942f0a90e4b7a4acf9428ba305134299` | `adb4137439bbcfdaacee33c267c967d443cb78f0c0dcd29b4569f0492a6c85f9` |
| 范围 | +18 行、2 文件、0 删除（`NVEncCore/NVEncPipeline.h` +11、`NVEncCore/rgy_input.h` +7） | +15 行、1 行替换、2 文件（`QSVPipeline/qsv_pipeline_ctrl.h` +10/−1、`QSVPipeline/rgy_input.h` +6） |
| 做法 | 无 seek 时 `break` 在过滤器之前，让缓冲的 picture 由原有 emission loop 按显示顺序发射；**有 seek 时原过滤器逐字运行** | 给准入条件加 `!bSeeked \|\|` 守卫；新增只读 accessor `getSeekParam()` 把已有 `--seek` 参数暴露给 pipeline |
| 语义变化 | 仅"无 seek"路径；`git apply --check` 对 pristine 9.31 exit 0 | 仅"无 seek"路径；`git apply --check` 对 pristine `b14c965` exit 0 |

来源：`docs/hardware-decode/patches/`（两个文件本体，本文件已实际读取；sha256 与字节数由
本机 `Get-FileHash` / `Get-Item` 复核）、`docs/hardware-decode/toolchain-provenance.json`
`backends.*.patch`、`olddocs/docs/hardware-decode/nvencc-patch.md` §3 与 §5.1、
`olddocs/docs/hardware-decode/qsvencc-patch.md` §1、§2.1–§2.2。

### 8.2 before / after 数字与验证广度

| 素材 | 容器 | leading | NVEncC stock → patched | QSVEncC stock → patched |
|---|---|---|---|---|
| Sony XAVC HS 30 f | 30 | 3 | **27 → 30** | **27 → 30** |
| Sony XAVC HS 330 f | 330 | 3 | **327 → 330** | — |
| Sony XAVC HS 360 f（C0886） | 360 | 3 | **357 → 360** | **357 → 360** |
| Sony XAVC HS 10 170 f | 10 170 | 3 | **10 167 → 10 170** | — |
| Sony H.264 4:2:2 195 f | 195 | 2 | **193 → 195** | refusal → refusal（不变） |
| DJI / x265 / synthetic / Sony-in-MKV（控制） | 105 / 30 / 60 | 0 | = 容器（不变） | = 容器（不变） |

| 验证维度 | NVEncC | QSVEncC |
|---|---|---|
| 计数一致性 | 8 案例 × 2 reader，**13 断言 × 16 记录**，`failures: []` | 10 案例 A/B 矩阵 |
| 逐帧指纹 | patched `--avhw` vs `--avsw`：**30/30 与 330/330 逐帧 mean Y/U/V 相同，最大偏差 0.0000**；输出 **sha256 相同**（`6336165c02a8…`） | patched `--avhw` vs `--avsw`：**30/30 逐帧 SHA-256 相同** |
| seek / trim 回归 | **17/17 组合与 stock 逐字节相同** | seek 3/3、trim 3/3 与 stock 相同 |
| 长程 | 3 000 / 6 000 / 10 000 / 10 170 全量；重建二进制复测输出 hash 全部一致 | 仓库内**无** QSVEncC 长程 / 多流 soak（`research-conclusion.md` open item 12：未执行） |
| 控制组 | DJI / x265 / synthetic / Sony-in-MKV：不变 | DJI 105/297、x265 30、synthetic 60：不变 |
| 4:2:2 capability | — | **仍是拒绝**；补丁既不改变它，也无法把拒绝变成解码 |

来源：`olddocs/docs/hardware-decode/nvencc-patch.md` §4.1–§4.4、
`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §6.0–§6.6、
`olddocs/docs/hardware-decode/research-conclusion.md` §1。

### 8.3 边界声明（引用补丁结论时**必须**同时带上）

* **QSVEncC 补丁：** *Runtime-proven on QSVEncC 8.26 pinned revision; not yet a general
  claim for later releases.* **8.27–8.30 存在且未检验。**
* **NVEncC 补丁：** 仅在 **9.31**（`2cb9d810c045202548b98ff130b12bc764eb39ea`）上验证；
  其他 revision 必须重新定位并重新验证。
* **两个 patched binary 都是 research build，不得分发**（`release/build_release.py` 的
  `TOOL_DIRS` 白名单不含 `tools/avhw/`，`docs/` 也排除在包外）⇒ 发布安装中
  **`--hw-decode auto` 以 `not_proven` 降级软解、`require` 明确失败**，这是设计行为。
* **跨 binary 性能不可比**（CUDA 13.1 / MSVC 14.51 vs CUDA 11.8 / MSVC 14.44）：
  跨 binary 只比**正确性**（帧数 / PTS / keyframe / 指纹 / 字节）。
* **"fixed" 的含义** = *fixed at source with a tracked patch file*，**不是**已修复在任何
  rigaya 正式发布版里；**上游尚未合并**。
* `FramePosList::setPocAndFix` **未被研究、未被修复、未被打包**进任何一个补丁。

来源：`docs/hardware-decode/README.md` §"边界声明"、`final-report.md` §9、
`olddocs/docs/hardware-decode/research-conclusion.md` §3.3 与 §7、`docs/design/architecture.md` §8.5。

### 8.4 补丁 sha256 的两个世代（**不是矛盾，是被自解释过的更正**）

| 世代 | NVEncC | QSVEncC | 说明 |
|---|---|---|---|
| research/编写期（CRLF） | `53084fa8…fe6dfc`（2832 B） | `5621ebb0…8e53d98`（2285 B） | `nvencc-patch.md` §5.1、`research-conclusion.md` RC-1/RC-2、`qsvencc-patch.md` §1 |
| **v0.7.0 提交后（LF，权威）** | **`2321ed72…5134299`（2802 B）** | **`adb41374…2a6c85f9`（2255 B）** | `toolchain-provenance.json` `sha256_note`：`core.autocrlf=true` 在 commit 时把文件重编码为 LF，导致记录的 hash 永远匹配不上 committed blob、`harness provenance` 在干净 checkout 上失败；**补丁内容未变** |

**本机复核：** 磁盘上两个 patch 文件的 sha256 与字节数 = **LF 世代**（见 §8.1）。
引用请用 LF 值；任一 research 文档里的旧值属于编写期形态。完整值见 §8.1 与 §12。

---

## 9. 生产侧如何兜底

### 9.1 CLI 与默认值

```text
--hw-decode off|auto|require        默认 off   （1kt.py:1267-1282）
--hw-decode-verify                  opt-in 追加 ordered fingerprint 校验（1kt.py:1283-1291）
```

| 策略 | 行为 |
|---|---|
| `off`（**默认**） | 软解。与 v0.6.2 **逐字节一致**，不改变任何默认 |
| `auto` | 命中 runtime-proven 白名单才用硬件 reader，否则软解。**每次降级都出声**：WARNING + reason code 进主日志 / per-file 日志 / report |
| `require` | 必须硬件解码，不可用即 `require_unmet` 报错，**绝不静默降级** |

来源：`1kt.py:1267-1291`（已读）、`encoders/hwdecode.py:42-47`、`core/batch_hw.py:480-483`、
`docs/design/architecture.md` §8.1。

### 9.2 路由：`not_proven` 降级路径

`encoders/hwdecode.py::route_decode()`（`:135-227`，已读）按顺序判定：

1. `policy == off` → `policy_off`，软解，**不出声**；
2. 请求带时间 seek → `seek_not_equivalent`（`require` 下为 `require_unmet`）；
3. 命中 `REFUSED` → `capability_refused`（`require` 下为 `require_unmet`）；
4. 本 run 的 memo 里已记录不可用 → `reader_unavailable`；
5. 不在 `PROVEN` 白名单 → **`not_proven`** →
   `"{codec} {chroma}/{depth}bit is not in the runtime-proven allowlist for {backend};
   not activating an unverified hardware-decode path"`，软解 + WARNING；
6. 否则 `proven_combination` → 硬件。

白名单是一个**闭集**，每行必须引用一次实测（`encoders/hwdecode.py:90-97`）：

| backend | codec | chroma | depth | 依据 |
|---|---|---|---|---|
| nvenc | hevc | 4:2:0 | 10 | research：N−3 → N（30/330/10170 帧），与 `--avsw` 字节相同 |
| nvenc | h264 | 4:2:2 | 10 | research：N−2 → N（195 帧），与 `--avsw` 字节相同 |
| qsv | hevc | 4:2:0 | 10 | research：N−3 → N，逐帧 SHA-256 与 `--avsw` 相同 |

`REFUSED`（`:101-105`）：`("qsv","h264","4:2:2",10)` —— 日志要说 "refused"，不是 "failed"。

**硬解 binary 绑定**（`encoders/hwdecode.py:336-347`、`:377-428`，已读）：
硬件解码**只**使用按 sha256 校验过的 patched build
（nvenc `dcf6d7a6…7c8be4b`、qsv `f5df83f1…f8804d`；本机 `Get-FileHash` 复核一致）；
`policy=off` 时不做任何 override；build 缺失或 hash 不符 → `not_proven` + 说明，
**绝不静默改用 shipped build**。原因写在源码注释里：用 shipped build 是"安全但无用"——
每份结果都会被闸门拒绝，功能看似开启实则从未运行。

### 9.3 帧完整性闸门（`encoders/integrity.py`）

闸门存在的理由就是这个缺陷本身：**stock 硬件 reader 在 Sony 上只吐 `N−3` 帧，`rc = 0`、
无错误文本、文件完全可播放**。只看"进程成功了吗"的闸门会放它过去 —— 事实上过去就放过去了。

| 级别 | 触发 | 检查内容 | 代价 |
|---|---|---|---|
| `count` | 硬件解码开启时**恒开** | ① `rc == 0` 且产物存在非空；② **reader 身份断言**；③ 五方对账；④ reader 自报只记录不判定 | 一次容器解析 + 两次**输出**解码 |
| `sequence` | `--hw-decode-verify` 显式开启 | 硬件与软件结果**逐帧有序指纹比对**（抓"计数不变但画面错序/替换/重复"） | 多一次完整编码 |

**reader 身份断言**（`encoders/integrity.py:321-329`，已读）：工具日志 `Input Info` 里的
reader 必须等于请求值 —— 请求 `avhw` 却构造出 `avsw` 是 **silent software fallback**，判 FAIL：
`"silent software fallback must not be recorded as a hardware pass"`。
期望值表见 `core/batch_hw.py:189-192`：`nvencc:{avhw:avcuvid, avsw:avsw}`、
`qsvencc:{avhw:avqsv, avsw:avsw}`。

**对账的硬规则**（`encoders/integrity.py:343-384`，已读）：以
`container_video_samples(source)`（自己解 `stsz`；MKV 退回 `ffprobe-packets`）为**参考真值**，
`encoder_input` / `output_stream` / `independent_decoded` 任一不等即 `count_mismatch`；
`reader_reported` 只在失败消息里作括注并注明 `"which is not trusted as a reference"`。

**失败动作**（`core/batch_hw.py:543-560`，已读）：丢弃硬件产物（`safe_unlink`）+
WARNING + reason code → 同一格式档软解重跑；回退次数上限 `MAX_HW_DECODE_FALLBACKS = 1`
（`core/batch_hw.py:197`），超出即 FATAL，**不会无限重试**。

### 9.4 未随发布包出货 ⇒ `auto` 在发布安装里退化为软解

`tools/avhw/` 与 `docs/` 都不进发布包（`release/build_release.py` 白名单制；
`core/config.py:256-274` 让 `find_hw_tool` **确定性排除** `tools/avhw/`，
否则默认路径会随目录 glob 排序变化，而两个 build 只在**行为**上不同 —— 误换后要到丢帧才发现）。
因此发布安装中：

```text
--hw-decode auto    -> not_proven -> 软解   （设计行为）
--hw-decode require -> 明确失败              （设计行为）
```

来源：`docs/hardware-decode/README.md` §"边界声明"、`final-report.md` §7.4/§9、
`docs/design/architecture.md` §8.5、`encoders/hwdecode.py:317-334`、`core/config.py:256-274`。

### 9.5 生产侧已实测能"抓住"这类缺陷（这是本次 integration 的核心）

| 测试 | 做法 | 结果 |
|---|---|---|
| **HD-C09** | 把**真实 stock binary**（已知丢 3 帧）喂给**真实 production 闸门** | 闸门判 `count_mismatch`（360 vs 357）→ 丢弃产物 → 软解重跑 → exact |
| **HD-C10** | 构造**帧数不变但画面错序**的产物 | 计数闸门放行（360 == 360）→ **sequence 闸门**抓到 `sequence_mismatch`（index 0） |
| HD-C11 | 端到端注入 | 丢弃 sha `5879568cd667`，交付 sha `ef6217336314` —— **错误产物从未被交付** |
| HD-F08 | 回退后的软件输出 | 与直接软解 sha 相同（`ef6217336314`） |

来源：`docs/hardware-decode/final-report.md` §1、§6 C 类 / F 类；
`work/avhw_integration/results/results.json`（HD-C09/C10/C11/F08 记录）。

---

## 10. 已知限制 / 未证明项

| # | 项 | 标签 | 依据 |
|---|---|---|---|
| 1 | Sony XAVC 上 rigaya `--avhw` 丢帧数 = leading picture 数 | `已确认` | `root-cause.md` RC-3；`summary.json` predictor 100 % |
| 2 | 两个丢弃点（NVEncC `getOutputFrame()` release loop / QSVEncC `sendBitstream()` 准入条件） | `已确认`；NVEncC 侧由源码 + 循环模型 + 解码器/下游 trace 三方一致，但**未用断点实地确认**；QSVEncC 侧由 pinned 源码构建 + `RGY_AVHW_TRACE` 插桩逐帧实测，更强 | `rigaya-avhw-analysis.md` §"Root cause" caveat；`qsvencc-root-cause.md` §2.1 |
| 3 | 两个前端**规则相同、代码不同**、补丁**不可互换** | `已确认` | `qsvencc-root-cause.md` §4；`qsvencc-patch.md` §1.1 |
| 4 | `FramePosList::setPocAndFix` 改的是**表项**，不改变交付帧 | `已确认` | `nvencc-second-path-analysis.md` §4–§7 |
| 5 | edit list / `ctts` / B 帧不是触发器 | `已确认`（V2/V4 控制变量） | `root-cause.md` RC-4 |
| 6 | "第一个 IRAP 不在呈现顺序首位的**任何**码流都受影响" | **`推断`** —— 由机制推出，与全部实测一致，未在全部媒体上测量 | `rigaya-avhw-analysis.md` C2/P5、C3/N3 |
| 7 | All-I / XAVC S-I / 8-bit / 1080p / 4:2:2 HEVC / HDR / VFR 的行为 | **`未验证`** —— 环境里没有这类素材 | `corpus.md` §3.5；`research-conclusion.md` §3.3 |
| 8 | QSVEncC 8.27–8.30、以及 `9.31` / `b14c965` 以外任何 rigaya revision | **`未验证`** | `toolchain-provenance.json` `claim_boundary`；`research-conclusion.md` §3.3 |
| 9 | VCEEncC 是否有同一缺陷 | **仓库内无证据**（仅 "likely" 的预测，且从未被检验） | `rigaya-avhw-analysis.md` R7 |
| 10 | `--seek` 上两个 reader 为何选到**不同画面**（帧数与 PTS 相同） | **未解释**；已被 `seek_not_equivalent` 守卫覆盖，production 不用 seek，实际影响为零 | `final-report.md` §8.2、§11 |
| 11 | `--frames N` 三段语义（`N ≤ leading` → 整流；`leading < N < container` → `N − leading`；`N ≥ container` → 钳制到 EOF） | `已确认`；**两个 reader 共有行为**（三 regime 上 hw 与 sw 逐字节相同），与硬解缺陷无关，不得与 full-input integrity 混淆 | `final-report.md` §8.4；`frames_contract.json` |
| 12 | 显式 `--seek 0` 与"不传 seek"不可区分（两个补丁的谓词都是 `seek > 0`） | `已确认`（从"未观测"变成"已测事实"，HD-D06） | `research-conclusion.md` open item 3；`final-report.md` §6 D-06 |
| 13 | patched NVEncC `--avhw` **不再遵守 `AV_PKT_FLAG_DISCARD`**（合成 fixture E/F 27 → 30） | `已确认` **行为变化**，属于采纳决策的一部分；在被测语料上那些是真画面，恢复它们是正确的 | `research-conclusion.md` open item 4；`nvencc-second-path-analysis.md` §5 |
| 14 | QSVEncC 长程 / 多流 soak | **未执行** | `research-conclusion.md` open item 12 |
| 15 | 多 GPU / `--device` 选择、parallel / `--split-enc` | **未验证**（单 GPU 主机全程） | `research-conclusion.md` open items 5、6 |
| 16 | 一次不可复现的 `0xC0000005`（10 170 帧 NVEncC 运行，1/6 次，从未复现；所有完成的运行都帧精确） | 观测 `已确认`，机制 `未验证` | `research-conclusion.md` open item 8 |
| 17 | 4-way 4K 并发塌到 8.01 fps 的**机制**（VRAM vs NVENC session 争用） | **`推断`** —— 该次运行 VRAM 采样失败，**无 VRAM 遥测**；不得把任一说法当作已确认根因。integration 侧测到 118.61 fps，但档位/片长不同，**与 8.01 fps 不可比** | `research-conclusion.md` §3.2；`final-report.md` §10 第 8 条 |
| 18 | QSV H.264 4:2:2 10-bit 限制源于**硅**还是 VPL/MSDK 栈 | **不可分辨**（换机交叉核对过，未解决） | `research-conclusion.md` open item 11 |
| 19 | 硬件解码的收益 | 实测 **2.5–2.7× 更少 CPU/帧**；单任务吞吐**不更快**（同 binary 配对 `--avsw` 140.9 s vs patched `--avhw` 158.4 s，约慢 11 %）；"NVENC-bound" 为 `推断` | `research-conclusion.md` §3.2 |

---

## 11. 复现方法

### 11.1 走 harness（推荐；把 `docs/hardware-decode/README.md` 的流程原样抄下）

```powershell
cd F:\1KeyTranscoder

# 1. 先确认工具链身份（任何不符 = FAIL，不是 warning）
python -m tests.hwdecode.harness provenance

# 2. 确认文档与矩阵没有漂移
python -m tests.hwdecode.harness check-matrix

# 3. 建/刷新生成的 control fixtures 并盘点语料
python -m tests.hwdecode.inventory

# 4. 按 phase 执行（顺序不可打乱，见矩阵 §L）
python -m tests.hwdecode.harness run --phase 1     # A toolchain + B routing
python -m tests.hwdecode.harness run --phase 2     # C integrity + D temporal
python -m tests.hwdecode.harness run --phase 3     # E preservation + F fallback
python -m tests.hwdecode.harness run --phase 4     # G features + H resume
python -m tests.hwdecode.harness run --phase 5     # I concurrency
python -m tests.hwdecode.harness run --phase 6     # J corpus / long-run + K baseline

# 5. 最终闸门（必须在其它测试之后运行）
python -m tests.hwdecode.harness run HD-J06
python -m tests.hwdecode.harness summary
```

`--deep` 打开各长测试的完整变体（更慢）；`--repeat N` 对 P0 用例重复 N 次。
来源：`docs/hardware-decode/README.md` §"怎么跑"、`docs/hardware-decode/final-report.md` §12。

### 11.2 端到端手工冒烟（真实 CLI）

```powershell
python 1kt.py --input <dir> --output <dir> --check basic --headless `
              --encoder nvenc --hw-decode auto --preset hq
```

日志中应出现（格式见 `final-report.md` §7.5）：

```text
[HWDEC] decode tool: ...\tools\avhw\NVEncC_9.31_avhw\NVEncC64.exe
        (patched build verified by sha256 dcf6d7a63143c777)
DECODE_ROUTE | backend=nvenc codec=hevc 4:2:0/10bit policy=auto -> HARDWARE
        (reader=avhw, reason=proven_combination)
INTEGRITY | integrity gate [OK] reason=integrity_ok
        counts={tool_rc:0, reader_identity:'avcuvid', encoder_input:360, ...}
```

既有项目回归：`python tests/full_autotest.py --level full`（集成时 248 PASS / 0 FAIL / 570.3 s —
`final-report.md` §3）。

### 11.3 最小复现（research 归档的原始形态）

```powershell
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'   # 30 container frames

& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avsw -c hevc --output-depth 10 --cqp 23 -o b_avsw.mp4
#   -> encoded 30 frames
& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avhw -c hevc --output-depth 10 --cqp 23 -o b_avhw.mp4
#   -> encoded 27 frames          <-- the defect

# 计数必须从**交付文件**取，不能只信进度行：
& tools\ffprobe.exe -v error -select_streams v:0 -count_frames `
    -show_entries stream=nb_read_frames -of default=nw=1 b_avhw.mp4
#   -> nb_read_frames=27      (b_avsw.mp4 -> 30)
```

命令与输出来自 `olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §2.1；该处还记录重复性：
连续 10 次 `--avhw` 全部返回 27、5 次 `--avsw` 全部返回 30 —— **亏损是确定性的，不是统计性的**。

> **本机实测备注（本次汇总时验证）：** 在当前 checkout 上跑上面的 NVEncC 命令得到
> `cuInit error:0x64 (CUDA_ERROR_NO_DEVICE)` / `Failed to initialize CUDA.` / `rc=1` ——
> 本会话环境**无可用 CUDA 设备**，故 §11.3 的最小复现**未能在本机重跑**，其数字全部来自归档。
> 相对的，`ffprobe -count_frames` 可用（对 `testsets\20260904\20260904_C1193.MP4` 实测
> `nb_read_frames=90`，与归档 RC-2 表的 90 一致），且 QSVEncC 的 4:2:2 拒绝可在无 GPU 下复现（§11.4）。

### 11.4 只复现"响亮拒绝"（无需 GPU）

```powershell
& tools\avhw\QSVEncC_8.26_avhw\QSVEncC64.exe `
    -i testsets\a7m4_4k30p_264_hi422p_xavcs\C9037.MP4 `
    --avhw -c raw --output-res 64x64 -o NUL
# -> avqsv: codec h264(yuv422p10le) unable to decode by qsv.
#    QSVEncC.exe finished with error!      rc = -31
```

本会话实测输出与 `docs/hardware-decode/toolchain-provenance.json` `known_refusals` 逐字一致，
**证明补丁不改变拒绝行为**。

### 11.5 怎么读证据

83 条用例的完整判定证据（计数、指纹 digest、packet manifest、sha256）在
`work/avhw_integration/results/results.json` —— 按 `test_id`（如 `HD-C01`）查记录里的 `evidence` 对象；
机器可读闸门 `results/summary.json`；扁平表 `results/results.csv`；每次编码的原始工具日志
`work/avhw_integration/logs/*.log`（reader 身份在 `Input Info` 行）；`--frames N` 的
requested vs presented 见 `results/frames_contract.json`；期望值与矩阵定义见
`docs/hardware-decode/integration-test-matrix.md` 与 `tests/hwdecode/matrix.json`。

**判读三条铁律**（`docs/hardware-decode/README.md` §"设计上必须知道的三件事"）：
① `exit code = 0` 不是正确性证据；② **reader 身份必须从工具日志读，不能从命令行推断**；
③ 默认路径必须解析到 shipped build，硬件解码必须解析到 patched build。

---

## 12. 证据索引

| 主张 | 支撑文件（+ 定位） |
|---|---|
| 症状：`rc = 0`、无报错、完全可播放，但少 3 帧 | `docs/hardware-decode/README.md` §"设计上必须知道的三件事"；`docs/hardware-decode/integration-test-matrix.md` §0；`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §2.1 |
| 语料规模 151 / 188,475 帧 / 64.87 GiB；逐素材 codec / profile / 分辨率 / fps / GOP / 容器 / 容器结构 | `olddocs/docs/hardware-decode/README.md` §"Conclusion table"；`olddocs/docs/hardware-decode/corpus.md` §3.2–§3.4；`olddocs/docs/hardware-decode/test-results/summary.json` `corpus` |
| XAVC HS −3 / XAVC S H.264 4:2:2 −2 / 控制组 0 | `olddocs/docs/hardware-decode/root-cause.md` RC-2/RC-3；`olddocs/docs/hardware-decode/divergence.md` §7.3；`docs/hardware-decode/final-report.md` §6 C 类 |
| 从未检出的素材类别 | `olddocs/docs/hardware-decode/corpus.md` §3.5；`research-conclusion.md` §3.3、§5 |
| 预测式 `container_samples − pictures_before_first_keyframe`，297/297 | `olddocs/docs/hardware-decode/root-cause.md` RC-3；`olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` §"Exact trigger" |
| 预测式是"流形态"而非 codec/容器属性（MKV 也丢、x265 不丢） | `olddocs/docs/hardware-decode/root-cause.md` RC-4（V1–V6） |
| NVEncC 根因：文件 / 类 / 函数 / 锚点 / 丢弃点 | `olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` §"Relevant source path"、"The drop site"；`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §3.1 |
| QSVEncC 根因：文件 / 类 / 函数 / 条件 / 账本 / 插桩 | `olddocs/docs/hardware-decode/qsvencc-root-cause.md` §0、§2、§3.1、§5、§6 |
| "规则相同、代码不同、补丁不可互换" | `olddocs/docs/hardware-decode/qsvencc-root-cause.md` §4；`olddocs/docs/hardware-decode/qsvencc-patch.md` §1.1 |
| `FramePosList::setPocAndFix` 是独立表缺陷，不改变交付帧 | `olddocs/docs/hardware-decode/nvencc-second-path-analysis.md` §1、§4.2、§6、§7；`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §7；`research-conclusion.md` RC-3 |
| FFmpeg QSV 146/146、NVDEC 151/151 逐帧位相同；"native 标签不代表软解" | `olddocs/docs/hardware-decode/qsv.md` §5.3、§5.5；`olddocs/docs/hardware-decode/nvdec.md` §6.2–§6.4；`divergence.md` §7.2；`root-cause.md` RC-6 |
| ffmpeg 与 rigaya 的首帧处理差异（机制对照） | `olddocs/docs/hardware-decode/rigaya-avhw-analysis.md` §"Why FFmpeg succeeds" |
| 指纹方法（exact sha1 / tolerant sig8、64×64 gray）与跨管线 ±1 注意 | `olddocs/docs/hardware-decode/divergence.md` §7.1；`olddocs/docs/hardware-decode/root-cause.md` RC-2 方法注 |
| 生产闸门指纹实现（7 元素签名 + digest） | `encoders/integrity.py:496-539` |
| 六方对账口径 | `docs/hardware-decode/integration-test-matrix.md` §0.5、§C.0；`docs/hardware-decode/final-report.md` §6 C.1 |
| 原始日志 / 结果 / baseline / fixtures 位置 | `docs/hardware-decode/README.md` §"产物"；`docs/hardware-decode/final-report.md` §4.5、§13；`work/avhw_integration/` |
| 补丁内容、范围（+18/0、+15/−1）、sha256、before/after、验证广度 | `docs/hardware-decode/patches/*.patch`；`docs/hardware-decode/toolchain-provenance.json` `backends.*.patch`、`sha256_note`；`olddocs/docs/hardware-decode/nvencc-patch.md` §4–§5；`olddocs/docs/hardware-decode/qsvencc-root-cause.md` §6 |
| 边界声明（8.26 pinned / 9.31 / research build 不可分发） | `docs/hardware-decode/README.md` §"边界声明"；`docs/hardware-decode/final-report.md` §9；`docs/design/architecture.md` §8.5 |
| `--hw-decode off\|auto\|require` 默认 `off`；`not_proven` 降级路径与 reason codes | `1kt.py:1267-1291`；`encoders/hwdecode.py:52-105`、`:135-227`；`docs/design/architecture.md` §8.1 |
| count / sequence 两级闸门、reader 身份断言、对账真值、失败动作与回退上限 | `encoders/integrity.py:285-384`、`:396-453`；`core/batch_hw.py:189-192`、`:197`、`:525-560`；`final-report.md` §7.2 |
| 硬解 binary 按 sha256 绑定、`tools/avhw` 被默认路径排除、发布安装中 `auto` 退化软解 | `encoders/hwdecode.py:336-347`、`:377-428`；`core/config.py:256-274`；`docs/design/architecture.md` §8.4–§8.5 |
| 闸门能抓住真实 stock 丢帧与计数不变的错序；矩阵总判定 83 PASS / P0 61/61 | `docs/hardware-decode/final-report.md` §1、§3、§6 HD-C09/C10/C11/F08；`work/avhw_integration/results/{results.json,summary.json}` |
| `--seek` 不等价；`--frames N` 三段 contract；收益数字 | `docs/hardware-decode/final-report.md` §8.2、§8.4；`docs/hardware-decode/integration-test-matrix.md` §D.3；`work/avhw_integration/results/frames_contract.json`；`olddocs/docs/hardware-decode/research-conclusion.md` §3.2 |
| 复现命令 | `docs/hardware-decode/README.md` §"怎么跑"；`docs/hardware-decode/final-report.md` §12；`olddocs/docs/hardware-decode/nvencc-avhw-experiment.md` §2.1 |

---

## 13. 来源之间的矛盾与不确定项

汇总时发现的**互相冲突 / 表述含糊 / 无支撑**之处，列出以便调用方决定。

| # | 现象 | 两侧来源 | 处理建议 |
|---|---|---|---|
| 1 | **补丁 sha256 有两个世代** | research：`53084fa8…` / `5621ebb0…`；integration：`2321ed72…` / `adb41374…`（`toolchain-provenance.json`） | **不是矛盾**：`sha256_note` 自解释为 `core.autocrlf=true` 的 CRLF→LF 重编码。**本机实测磁盘文件 = LF 世代**，引用统一用 LF 值（§8.1、§8.4） |
| 2 | **丢帧站点行号不一致（NVEncC）** | `root-cause.md` 顶部更正标 `NVEncPipeline.h:1665-1667`；`nvencc-avhw-experiment.md` §3.1 引 `:1646-1681`；`rigaya-avhw-analysis.md` 引 `getOutputFrame()` 的 `1585-1727` / 原始 `1599-1684`；**补丁 hunk 头是 `@@ -1653,6 +1653,17 @@`** | 属**同一处的不同引用粒度**，不是矛盾。hunk 头是唯一能与补丁互相校验的锚点 |
| 3 | **QSV 站点行号不一致** | `qsvencc-root-cause.md` §3.1 与 `research-conclusion.md` RC-2 写 `qsv_pipeline_ctrl.h:1136-1138`；`qsvencc-patch.md` §2.1 写 `:1066-1069` / `:1157`；**补丁 hunk 头是 `@@ -1261,9 +1261,17 @@`** | **无法在本仓库内判定哪个是 pinned revision 的真实行号**（上游源码未随包出货，`work/_worktrees/1KT-qsv` 已不存在）。以**补丁 hunk** 为准，引用时同时标注 |
| 4 | **`FramePosList::setPocAndFix` 行号** | `rgy_input_avcodec.h:654-671`（两处一致）；但 `m_firstKeyframePts` 一处写 `:321`、一处写 `:320-322`；`add()` 在 `:324-328` | 小差异，按"±1 行"处理 |
| 5 | **文档里的函数名写错** | `docs/design/architecture.md:1476` 写 `encoders/hwdecode.py::decide_route()`；同文件 `:1394` 与其他文档写 `route_decode`；**实际代码是 `route_decode`**（`encoders/hwdecode.py:135`） | 文档笔误，以代码为准 |
| 6 | **H.264 4:2:2 在 QSV 上：静默回退 vs 响亮拒绝** | `docs/design/hardware_backend_design.md` §5.2 表注写"QSVEncC 请求硬解 → **静默回退 avsw**"（同文档 §5.6 又说该静默替换"在本调查中从未出现"）；`qsv.md` §5.4、`toolchain-provenance.json` `known_refusals`、`final-report.md` §6 HD-F02 均为**响亮拒绝** `rc=−31` | 该设计文档**早于 Phase 2**；本会话用仓库里的 patched QSVEncC 8.26 实测得到**响亮拒绝**（§11.4），与 integration 侧一致。**"静默回退"不应再被引用** |
| 7 | **旧设计文档把根因判在共享 reader 层** | `docs/design/hardware_backend_design.md:214-215`："rigaya 共享 reader 层对 Sony 结构（ctts/priming）的时间戳处理缺陷"；Phase 2 明确**共享 reader 不是原因**，站点在两个 vendor-specific pipeline task | 旧结论已被同仓库更新的归档推翻（`rigaya-avhw-analysis.md` C2/P1、`olddocs/.../README.md` Phase 2 更正）。以 Phase 2 为准 |
| 8 | **"302 次运行" vs 语料 151** | `root-cause.md` RC-3："302 runs"、"Validated on 297 / 297"；`summary.json`：nvenc measured 151 + qsv measured 146 = 297，refused 5 | 一致：302 = 297 + 5 次 QSV 能力拒绝。引用"302"须说明其中 5 次不可测 |
| 9 | **标题性把 H.264 也写作"3-Frame Loss"** | `qsvencc-root-cause.md` §7 对照表标题为 "Sony 3-Frame Loss"，但同文档 §0 与语料数据均为 XAVC HS = 3、H.264 4:2:2 = 2 | 属标题简化，数据一致。引用 H.264 必须写 **−2** |
| 10 | **归档里的补丁文件名与仓库内不一致** | 两处归档文档都叫 `0001-avhw-keep-leading-pictures.patch`；**committed 文件名是** `0001-nvencc-avhw-keep-leading-pictures.patch` / `0001-qsvencc-avhw-keep-leading-pictures.patch` | 归档里的是 gitignored `work/` 下的工作名；权威名见 `toolchain-provenance.json` `patch.file` |
| 11 | **`reader_reported` 被列为"不可信"但实测恒为 null** | `final-report.md` §6 C.1 自述："正常编码时两个 reader 都不打印 `N frames, End of file`" ⇒ 字段为 null；而 `HD-C01` 的 `evidence` 里确为 `"reader_reported": null` | 不是矛盾，是**自带说明**。含义是：该字段在 integration 侧**未产生证据**；"reader 自报少报"的实际证据来自 research 侧（`--log-framelist`，见 `nvencc-second-path-analysis.md` §4.1） |
| 12 | **"corpus-wide 不变式尚未检查"** | `research-conclusion.md` §4 B.4："the invariant becomes the simple `avhw == container`; **it has not been checked corpus-wide**" | 与 integration 的 HD-C04/J-03 不冲突但**覆盖面不同**：integration 是抽样（≥12 + 30 片），不是 151 片全量。引用"全语料已证"须限定 |
| 13 | **两侧吞吐数字不可比** | `research-conclusion.md` §3.2：单任务配对 `--avsw` 140.9 s vs patched `--avhw` 158.4 s（约慢 11 %）；4-way 4K 塌到 **8.01 fps**。`final-report.md` §10 第 8 条：integration 侧 4-way 4K 实测 **118.61 fps**（`qvbr-26` 快档、360 帧 vs 生产 HQ 档、长片段） | 两个来源都**自带"不可比"声明**。引用任一时不得跨来源比较，也不得把 118.61 当作"8.01 已被修复" |

**仓库内无证据的项**（本次汇总未在任何文件中找到支撑，故不在正文作断言）：
① VCEEncC 的实测行为；② patched 二进制在发布包中的任何启用方式；③ 8.27–8.30 的代码差异内容；
④ `--seek` 上两个 reader 选到不同画面的机制；⑤ integration 侧对 151 片 Sony 语料的全量
`avhw == container` 复算。
