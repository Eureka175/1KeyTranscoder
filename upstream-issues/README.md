# upstream-issues — 可直接提交给 rigaya 的 issue 材料

两个独立的 bug report，**自包含**：不引用本仓库任何文件、路径或内部术语，
每份都是「标题 + 正文 + 完整修改代码块」，复制粘贴即可提交。

| 文件 | 对应仓库 | 提交地址 |
|---|---|---|
| `nvencc.md` | `rigaya/NVEnc` | https://github.com/rigaya/NVEnc/issues/new |
| `qsvencc.md` | `rigaya/QSVEnc` | https://github.com/rigaya/QSVEnc/issues/new |
| `reproducer/` | — | 复现素材包（原视频 / 合成视频 / 生成代码 / 实测数据），见其 `README.md` |

两份 issue 正文里写"可以私下提供样片"，对应的就是 `reproducer/` 这个文件夹。
**建议首选附件是 `reproducer/generated/A_sony_copy.mp4`（10.6 MB，低于 GitHub 网页
附件 25 MB 上限，真实 XAVC 码流，已剥掉全部音轨）**；不想公开相机画面就用
`reproducer/synthetic/xavc-shape-synthetic.mp4`（291 KB，纯合成，形状一致）。

## 怎么用

1. 打开对应的 `issues/new` 页面。
2. **标题** = 文件第一行去掉开头的 `# `。
   例：`--avhw silently drops open-GOP leading pictures (Sony XAVC: N−3 for HEVC, N−2 for H.264 4:2:2)`
3. **正文** = 该文件从第二行起的**全部内容**（GitHub 会把它渲染成 markdown，
   首行会显示为 H1 标题，属正常）。
4. 代码块是 **before / 找这段 / 替换为** 的形式，不再用 diff —— 维护者可以直接
   复制，不需要理解 unified diff 的行号语义。

## 代码注释的语言规则（重要）

代码块里的注释分两类，**故意不完全统一**：

| 类别 | 处理 | 为什么 |
|---|---|---|
| **我们新增的注释** | **全英文** | 就是要给你和维护者看懂。长度刻意与原来日文注释**逐行对齐**，这样文档里"18 added lines / 15 added lines"这类行数声明仍然成立 |
| **上游原有的注释**（出现在「找到这段」的代码块里） | **保留日文原文，一字不改** | 这几行是用来**文本搜索定位**的。翻译了就对不上源文件，维护者会找不到位置 |

保留的日文原文共 5 处，对应的英文意思都紧跟在代码块下面的引用块里：

| 文件 | 日文注释（保留） | 英文意思 |
|---|---|---|
| `nvencc.md` | `// OpenGOP等でキーフレームより前のフレームのptsで出てくることがあるのを調整` | adjust for frames that may arrive with a pts earlier than the keyframe's, as happens with OpenGOP |
| `qsvencc.md` | `// 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する` | the first frame may carry data from before the fed frame because of an OpenGOP B-frame, so in that case ignore the frame |

如果你希望**一个日文字符都不留**，那不是改注释、而是要改结构：「找到这段」的代码块只能保留代码行（去掉注释行），然后改用文字描述位置（例如"在 `getOutputFrame()` 里，`m_gotFrameAfterFirstPts` 那个提前 break 之后、原有的 OpenGOP 注释之前"）。那样定位精度会下降，但能做到纯英文。要的话我改。

## 提交前请自行确认/修改的四处

1. **驱动版本。** 两份文档里写的是 `616.56`（测量当时的值）。你现在这台机器
   实测报的是 `616.92`。若重新测过，改 Environment 表里那一行。
2. **样片名。** `nvencc.md` 的复现段写的是 `20260903_C1170.MP4`（30 帧 XAVC HS）。
   若你要换成别的样片，改文件名并把「30 samples / 3 leading pictures」一起改对。
3. **要不要附样片。** 两份文档都写了"可以私下提供"。若愿意公开，把样片传到
   issue 里并在复现段补链接；**不要**附任何自己编译的二进制。
4. **QSV 那边不写行号是刻意的。** 仓库里对丢帧站点的行号有三种互相矛盾的说法
   （`1136-1138` / `1066-1069`+`1157` / patch hunk `1261`），上游源码没有随包出货，
   无法判定真值。所以 `qsvencc.md` 只给**类名 + 函数名 + 完整条件表达式**，
   让维护者按代码定位。这是有意的，不要"补"上行号。

## 为什么是两份而不是一份

两个工具的缺陷**规则相同、代码不同**：
NVEncC 在 `NVEncCore/NVEncPipeline.h::PipelineTaskNVDecode::getOutputFrame()`，
QSVEncC 在 `QSVPipeline/qsv_pipeline_ctrl.h::PipelineTaskMFXDecode::sendBitstream()`。
两个文件各自是 vendor 私有的，**补丁不可互换**，只有共用的 reader
（`rgy_input_avcodec.cpp/.h`）是逐字节相同的——而 reader **不是**缺陷所在。
分成两个 issue，避免维护者把其中一个当成另一个的重复。

## 还没做的一件事

`FramePosList::setPocAndFix()` 是**第三个**独立缺陷，位于三工具共用的
`rgy_input_avcodec.h`，会让 reader 自报帧数（`N frames, End of file` /
`--log-framelist`）少报。它**不导致丢帧**（`--avsw` 上同样触发但一帧不少），
所以两份 issue 里都只作为"相关但独立、未打包进本次修复"提了一句。

要单独提的话需要先决定提到哪个 repo（NVEncC / QSVEncC / VCEEncC 三处共用该文件），
定了我再出一份同样格式的自包含版本。
