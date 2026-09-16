# 显式音频计划 `--audio-plan` 使用指南

> **这份文档面向使用者, 不是开发者。** 它只讲"想做什么 → 写什么"。
> 内部设计、不变量与 reason code 契约见
> [`design/architecture.md`](design/architecture.md) §6.1/§6.6,
> 版本变更见 [`release_notes_v0.8.0.md`](release_notes_v0.8.0.md)。

---

## 0. 一分钟上手

```powershell
# 1) 写一个计划文件 (最小可用: 只保留第 1 条音频流)
@'
{
  "version": 1,
  "channels": { "select": ["source:s1:c0"] }
}
'@ | Set-Content -Encoding UTF8 plan.json

# 2) 带上它跑一次
python 1kt.py --input D:\in --output D:\out --encoder nvenc --audio-plan plan.json --headless
```

不带 `--audio-plan` 时, 一切照旧 —— 见下一节。

---

## 1. `--audio-plan` 是什么

它是"**这次输出请按我写的规则处理音频**"的显式开关。

* 不指定 → 1KeyTranscoder 走**原有默认音频路径** (`-map 0` + `-c:a copy`,
  音频原样拷进输出容器), 不会自动启用任何复杂处理;
* 指定 → 由你的 JSON 决定"保留哪些声道 / 什么顺序 / 什么编码 / 要不要对齐 /
  要不要并入同目录的外挂录音";
* 计划文件里**只有选择与编码意图**, 没有"走哪条代码路径"这类内部概念。
  内部怎么执行 (stream copy / PCM 路由 / 混音) 由程序自己判断。

计划文件里的声道身份就是程序实际探到的那套字符串:

```text
<来源>:s<流序号>:c<声道序号>
  来源       = 主视频文件固定叫 source; 外挂音频用它自己的文件名
   流序号    = 音频流在容器里的 stream index (视频文件通常是 1,2,3…)
     声道序号 = 该流内第几个声道 (0 开始)
```

例: `source:s1:c0` = 主视频第 1 条音频流的第 0 个声道;
`clip_01.wav:s0:c0` = 外挂文件 `clip_01.wav` 的第 0 条流的第 0 个声道
(纯音频文件通常就是 `s0`)。

> 想知道实际身份是什么, 先不带 `--audio-plan` 跑一次, 日志/报告里的
> 音轨清单就是权威; 也可以用 `ffprobe -show_streams` 对照。

---

## 2. 默认行为 (不给 `--audio-plan`)

```text
音频: -map 0 + -c:a copy —— 所有音频流原样保留, 不重编码, 不改顺序
视频: 与以前完全一致 (NVENC / QSV / x265 / SVT-AV1 各自的原路径)
```

**空计划也等于没启用**: 如果计划文件里只有 `encode` 而没有 `select` /
`exclude` / `map` / `external` / `alignment` / `mapping`, 程序把它当作
"未启用", 走的还是默认路径 (连"视频专用命令派生"都不会发生)。

---

## 3. 支持的后端

| `--encoder` | 视频 | `--audio-plan` |
|---|---|---|
| `x265` | 软件 HEVC | ✅ 支持 |
| `svtav1` | 软件 AV1 | ✅ 支持 |
| `nvenc` | NVIDIA HEVC | ✅ 支持 |
| `qsv` | Intel HEVC | ✅ 支持 |
| `nvenc-av1` | NVIDIA AV1 | ✅ 已接入, 但本机实测该编码器**无法创建** → 见 §11 限制 |
| `qsv-av1` | Intel AV1 | ✅ 支持 (本机实测通过) |
| Sony XAVC 素材 | 保留管线 | ❌ 未实现: 该文件会**明确失败**, 不会静默忽略计划 |
| DJI 素材 | 保留管线 | ❌ 同上 |

**没有** `--audio-plan` 时, 上面所有后端的行为都与以前完全一致。

硬件后端 (NVENC / QSV) 上音频不是交给硬件编码器处理的: 视频由硬件编码器产出
**纯视频**文件, 音频由独立的音频后端处理, 最后统一组装。`--hw-decode` 也不受
影响 (实测硬件解码仍然走 `avhw`)。

---

## 4. 计划文件结构

```json
{
  "version": 1,
  "encode": { "format": "aac", "bitrate": "192k" },
  "channels": {
    "select": ["source:s1:c0", "source:s2:c0"],
    "map": ["source:s2:c0", "source:s1:c0"]
  },
  "alignment": "auto",
  "sync": { "reference": "source:s1:c0" },
  "mapping": { "mode": "source" },
  "external": {},
  "note": "给我自己看的备注"
}
```

| 键 | 作用 | 缺省 |
|---|---|---|
| `version` | 格式版本, 目前只能是 `1` | `1` |
| `encode` | 输出编码: `format` (`aac` / `opus` / `pcm` / `flac`), `bitrate` (`"128k"`, 只能给有损格式主观指定), `sample_rate` / `channel_count` (只做校验, 不会重采样) | 按来源继承 (§6) |
| `channels.select` | 只保留这些声道 (顺序 = 你写的顺序) | 全选 |
| `channels.exclude` | 排除这些声道 | 无 |
| `channels.map` | 显式指定输出顺序 (**必须是 select 的同一集合**, 只换顺序) | 按 select 顺序 |
| `alignment` | `auto` / `enabled` / `disabled` —— 是否允许做延迟对齐 | `auto` |
| `sync.reference` | 对齐的**参考声道** (必须显式写, 程序从不猜) | 无 → 不做对齐 |
| `mapping` | 输出流结构: `source` / `independent` / `grouped` + `group_size` | `source` |
| `external` | 发现同目录下的外挂录音并并入 | 不启用 |
| `note` | 自由文本, 只进日志 | 无 |

**未知的键或未知取值一律报错** (不会静默忽略你写错的东西)。

---

## 5. 选择 / 排除 / 排序

```json
{
  "version": 1,
  "channels": {
    "select": ["source:s1:c0", "source:s2:c0", "clip_01.wav:s0:c0"],
    "map":    ["clip_01.wav:s0:c0", "source:s1:c0", "source:s2:c0"]
  }
}
```

* `select` 决定**要哪些声道**, 你写的顺序就是默认输出顺序;
* `exclude` 从当前选择里去掉若干声道 (与 `select` 同时写时, 先 select 再 exclude);
* `map` 只重新指定**输出顺序**, 集合必须与 `select` **完全一致** (上面三个换三个);
  想增删声道请改 `select`, 重复写同一个声道会被拒绝。

只想"删掉两条不需要的轨":

```json
{ "version": 1, "channels": { "exclude": ["source:s3:c0", "source:s4:c0"] } }
```

---

## 6. 编码: 继承、手动覆盖、什么时候才会真的重编码

规则一句话: **你不写 `encode` 时, 尽量保持来源的原样**。

| 输入 | 不写 `encode` 时 |
|---|---|
| PCM (WAV / 摄影机 PCM 轨) | 输出 **PCM** (不会偷偷转成 AAC) |
| AAC | 保持 **AAC**, 并尽量继承来源码率 |
| Opus | 保持 **Opus**, 并尽量继承来源码率 |
| 其它压缩格式 | 退回默认编码器并**在日志里说明** (不静默转码) |

手动指定就按你写的来:

```json
{ "version": 1,
  "channels": { "select": ["source:s1:c0", "source:s2:c0"] },
  "encode": { "format": "opus", "bitrate": "96k" } }
```

* `aac` 可选 `128k` / `192k` / `256k` …; `opus` 可选 `64k` / `96k` / `128k` …
* `pcm` / `flac` 是**无损**格式, 给它们写 `bitrate` 会**直接报错**
  (而不是默默丢掉这个参数);
* 采样率永远跟随素材 (本项目不做重采样), 写一个不同的 `sample_rate` 会被拒绝。

### ⚠️ 什么时候"手动 codec"不会生效

程序只重编码**必须重建**的音频流; 能整条原样搬走的流会保持原样 (更快、也没有
多余损失)。所以:

```text
4 条独立 mono 流, 你选其中 2 条           -> 每条都是完整流 -> 原样 copy
                                             (这里写 "format": "opus" 不会生效!)
1 条 4CH 流, 你选其中 2 个声道            -> 必须重建 -> 按你写的 codec 编码
2 个 mono 文件被 mapping 合成一条 stereo  -> 必须重建 -> 按你写的 codec 编码
```

日志里会有一行说明哪几条流是 copy、哪几条是按什么 codec 编码的。
想要"整轨转码"时, 只要让选择不是"完整流的自然顺序"即可 (例如改顺序、取子集,
或用 `mapping` 重新组合)。

---

## 7. Alignment (延迟对齐)

### 默认规则

```text
PCM 输入 (WAV / 摄影机 PCM)   -> 默认**允许**对齐
AAC / Opus / 其它压缩          -> 默认**不动它** (不解码、不重编码)
```

"允许"不等于"会自动对齐": 程序**从不自动猜参考声道**。必须自己写:

```json
{ "version": 1,
  "channels": { "select": ["clip_ref.wav:s0:c0", "clip_tgt.wav:s0:c0"] },
  "alignment": "enabled",
  "sync": { "reference": "clip_ref.wav:s0:c0" } }
```

### 压缩音频也能对齐, 但要付出重编码的代价

显式要求压缩音频对齐时, 程序会:

```text
打印 warning (明确告诉你这条流会被解码 + 重新编码)
  -> 解码成 PCM
  -> 在样本级做对齐
  -> 按**来源 codec** 重新编码 (AAC 还是 AAC, Opus 还是 Opus)
```

实测证据: 给一条晚到 480 样本的 Opus 外挂录音做对齐, 估计出的偏移正是
**480** 样本, 对齐后两路内容互相关残差为 **0**。为什么必须真的解码: 同一个
480 样本的延迟, 在 AAC 上量出来是 **1504** (480 + AAC 解码器固有的 1024 样本
priming) —— 只改压缩包时间戳是补不上这 1024 的。

### `alignment` 的三种取值

| 值 | 含义 |
|---|---|
| `auto` (默认) | PCM 允许对齐; 压缩音频保持原样 |
| `enabled` | 强制允许对齐 (压缩音频会 warning + 解码重编码) |
| `disabled` | 完全不做对齐 (不估计、不应用、不解码) |

没有 `sync.reference` 时, 即使写了 `enabled` 也不会有任何样本被移动。

---

## 8. Mapping (输出流结构)

`mapping` 决定"**最终输出几条音频流, 每条里有哪几个声道**"。它只作用于
**外挂音频**; 原视频自己的音频结构保持原样。

| `mode` | 含义 | 例 |
|---|---|---|
| `source` (默认) | 跟随每个输入来源自己的流结构 | 4 个 mono 文件 → 4 条 mono 流; 1 个 stereo 文件 → 1 条 stereo 流 |
| `independent` | 一个声道 = 一条输出流 | 4CH 外挂 → 4 条 mono |
| `grouped` + `group_size` | 连续 N 个声道 = 一条输出流 (**跨文件继续成组**) | 4CH + `group_size: 2` → 2 条 stereo |

```json
{ "version": 1, "external": {}, "mapping": { "mode": "grouped", "group_size": 2 } }
```

几个实际结果 (都已实测):

```text
4CH 外挂 + independent     -> 4 × mono
4CH 外挂 + grouped(2)      -> 2 × stereo
6CH 外挂 + grouped(2)      -> 3 × stereo
8CH 外挂 + grouped(2)      -> 4 × stereo   (不会塌成一条 8CH)
3CH 外挂 + grouped(2)      -> 2CH + 1CH    (剩下的 1 个声道保留, 不丢也不复制)
A.wav+B.wav+C.wav+D.wav + grouped(2) -> (A+B), (C+D)
```

**任何声道都不会被静默丢弃**: 输出声道总数必须等于你的选择数, 做不到就报错。

---

## 9. 外挂音频 (同目录自动发现)

视频旁边单独录的音, 只要命名规范, 不用手工列出:

```text
D:\in\
├── clip001.MP4          <- 视频
├── clip001.wav          <- 会并入
├── clip001_01.wav       <- 会并入
├── clip001-02.wav       <- 会并入
├── clip001_rec.wav      <- 会并入
├── clip001abc.wav       <- 不会 (主干后面必须直接是结束、"-" 或 "_")
├── clip00.wav           <- 不会
└── other.wav            <- 不会
```

启用方式 (只写这一行即可):

```json
{ "version": 1, "external": {} }
```

规则细节:

* **只扫描视频所在的那一个目录**, 只考虑音频扩展名 (`.wav` / `.flac` / `.aac` /
  `.m4a` / `.opus` / `.mp3` …), 同名视频不会被当成音频;
* 文件名主干必须**精确匹配**视频主干 (大小写不敏感), 后面接 `-` / `_` 或直接结束;
* 顺序是确定的: 先"与视频同名"的文件, 再按数字序号 (数字按**数值**比较,
  所以 `_2` 在 `_10` 前面), 最后是其它后缀; 完全同名不同格式 (`.wav` / `.aac`)
  的多个文件**都会被并入**;
* 前导零不影响排序 (`_1` / `_01` / `_001` 视为同一序号, 再用完整文件名决定先后);
* 外挂文件在输出里排在**原视频音频之后**, 顺序 = 上面的发现顺序;
* 匹配到但**读不出来**的文件会让该文件失败 (不静默跳过);
* **没有匹配到任何文件不报错**, 视频照常用自己的音频。

想确认并入了哪些文件: 看该文件的 per-file 日志 (`AUDIO_OUTPUT` 一行)。

---

## 10. 硬件后端上的 `--audio-plan` (NVENC / QSV)

```text
                    +-----------------+
   video ---------> | NVENC / QSV     | ---> 纯视频产物 ---+
                    +-----------------+                    |
                                                           v
   音频计划 -------> 音频后端 ---> 音频产物 -----------> 组装 ---> 最终 MP4
```

* **视频仍然是硬件编码** (`--encoder nvenc` / `qsv`), 视频基本流逐比特不变 ——
  实测: 加不加 `--audio-plan`, 同一素材的 HEVC 基本流 sha256 完全相同;
* **音频独立处理**: `--hw-decode auto` 依旧走硬件解码 (实测日志里仍是
  `-> HARDWARE (reader=avhw, reason=proven_combination)`), 音频计划不会把
  你踢回软解;
* 硬件编码器不认识音频计划 —— 这条边界由回归的 AST 断言长期钉住。

Sony XAVC / DJI 素材: 这两条保留管线有自己的一套音频处理, 目前**不支持**
`--audio-plan`。带计划跑到这类文件时**该文件明确失败**并给出原因
(不会"忽略计划照常输出")。用法是: 这类素材不要带 `--audio-plan`。

`--audio-plan` 与 `--channel-sync` / `--channel-sync-transparent` 不能同时用
(两者都在决定最终音频), 同时给出会直接退出并提示。

---

## 11. 常见组合 (可直接复制)

**A. 删除两条不需要的轨**

```json
{ "version": 1, "channels": { "exclude": ["source:s3:c0", "source:s4:c0"] } }
```

**B. 只留无线麦两路, 转成 Opus 96k**

```json
{
  "version": 1,
  "channels": { "select": ["source:s3:c0", "source:s4:c0"] },
  "encode": { "format": "opus", "bitrate": "96k" }
}
```

> 这两条若各自是完整 mono 流, 会被原样 copy (§6 的说明); 想强制转码请让选择
> 变成"非完整流自然顺序", 例如取一条 4CH 流里的两个声道。

**C. 用一条外挂录音对齐摄影机音频**

```json
{
  "version": 1,
  "external": {},
  "alignment": "enabled",
  "sync": { "reference": "source:s1:c0" }
}
```

**D. 摄影机音频 + 两个外挂 mono, 每个外挂声道一条独立流**

```json
{ "version": 1, "external": {}, "mapping": { "mode": "independent" } }
```

**E. 8 声道外挂录音拆成 4 条 stereo**

```json
{ "version": 1, "external": {}, "mapping": { "mode": "grouped", "group_size": 2 } }
```

**F. 全部转成 AAC 192k (适用于需要重编码的选择)**

```json
{
  "version": 1,
  "channels": { "select": ["source:s1:c0", "source:s1:c1"] },
  "encode": { "format": "aac", "bitrate": "192k" }
}
```

---

## 12. 限制与未实现

以下都是**当前实际状态**, 不是"以后可能"的清单:

```text
漂移校正 (drift correction)   未实现 —— 只处理恒定偏移; 缓慢漂移会被检测出来并
                              保持原样, 不做修正
重采样 (resampling)           未实现 —— 参与同一次输出的音频采样率必须一致, 否则报错
响度处理 (loudness / LUFS /   未实现 —— 没有归一化 / AGC / 限幅 / 压缩
  AGC / limiter / compressor)
自动多文件同步                未实现 —— 外挂音频只按文件名规则发现, 不做内容匹配
Sony / DJI 保留管线           不支持 --audio-plan (该文件明确失败)
多 mixer sink 输出结构        不支持 (混音图只产出一条流)
```

另外两条与用法相关的事实:

* 一旦对齐真的移动了样本, 本次输出的每条音频流都会重新渲染一次
  (整条搬字节表达不了样本级平移), 因此**原本可以 copy 的流这时也会被重编码**;
* 外挂同名不同格式的文件 (例如同时有 `clip001.wav` 和 `clip001.aac`) 会被
  **一起并入**, 需要去掉其中一个时用 `channels.exclude`。

### 环境相关限制 (本机实测, 与 `--audio-plan` 无关)

```text
nvenc-av1 : 编码器无法创建 (NVEncC: "Error on nvEncInitializeEncoder: 8
            (Invalid Level.)", AV1 main @ Level 6.1)。加不加 --audio-plan
            结果相同 —— 属于 AV1 profile/驱动的既有限制, 不是音频接入引入的。
qsv-av1   : 正常 (实测 av1 视频 + 计划音频输出成功)。
```

---

## 13. 出问题时看什么

| 现象 | 位置 / 含义 |
|---|---|
| 计划文件写错 (未知键 / 未知取值 / map 与 select 不一致) | 启动直接退出 (rc=2), stderr 说明哪个键错了 |
| 计划里的声道不存在 | 该文件失败, 日志 `AUDIO PLAN REJECTED`, 不产出半成品 |
| `AUDIO-FAIL` 开头 | 音频阶段的明确失败 (对齐失败 / 保留层拒绝 / 组装失败), 该文件不交付 |
| 想知道实际走了哪条音频路径 | 日志 `audio execution path: ...` 与 `AUDIO_OUTPUT \| ...` |
| 想知道哪条流是 copy、哪条是重编码 | 同一行的 per-file 日志说明 |
| 怀疑"计划没生效" | 先确认没有写成空计划 (§2); 再确认选择是不是完整流的自然顺序 (§6) |
