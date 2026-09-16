# v0.8.0 发布说明 — 音频输出接入生产管线 + 格式感知 + 外挂音频

> **状态**：**已发布**（tag `v0.8.0`；`VERSION` = `0.8.0`）
> **基线**：`v0.7.1`（tag `v0.7.1`，commit `e613b07`）
> **范围**：
> * 任意 reference 的**恒定**样本偏移矫正（第 1–10 节）；
> * Phase 4A 选择性 MP4 音频保留（第 11–17 节）；
> * Phase 4B 音频编码 + 最终输出编排（第 18–26 节）；
> * Phase 4C 音频输出接入生产入口（第 27–34 节）；
> * Phase 5 格式感知 alignment + 编码继承 + 外挂音频（第 35–47 节）。
>
> drift correction / resampling / loudness / 自动多文件同步 / P5
> **均未实现**；CLI 只新增 `--audio-plan` 一个参数（v0.8.0 没有新增任何参数）。
>
> ⚠️ **文件名历史**：本文件在 v0.8.0 发布前叫 `release_notes_next.md`（"下一
> 开发周期，未分配版本号"）。随 tag `v0.8.0` 改名为现在这个名字并冻结为
> **v0.8.0 的发布说明** —— 内容没有改写，只是终于知道自己叫什么。
> v0.7.1 的发布说明在 [`release_notes_v0.7.1.md`](release_notes_v0.7.1.md)，
> 已随 tag `v0.7.1` 冻结，**本文件不改写其任何语义**。

---

## 1. 本周期解决什么

v0.7.1 的 PCM 链路能**消费**已经存在的 offset，但不产出 offset：

```text
v0.7.1:  AudioChannel.sync.offset_samples  ->  AudioTimeline  ->  render
              ▲
              └── 只能由外部 channel_sync 报告 / 调用方手工填写
```

文件级 `--channel-sync` 只在**单个文件内**工作，且锚点由配置项
`anchor_candidates`（默认 `[CH3, CH4, CH1, CH2]`）的**候选回退顺序**决定 ——
那是"文件内锚点回退"，不是"调用方指定任意参考"。

本周期补上这一层：

```text
本周期:  reference AudioChannel  ->  跨来源 delay 估计  ->  per-target offset
                                          ▲
                                          └── 任意 AudioChannel 都可当 reference
```

## 2. 核心原则

```text
Reference is data, not topology.
```

**reference 是"这次同步任务选出来的一路 `AudioChannel`"，不是 camera /
recorder / stream 0 / `sources[0]` / 任何预设路径。**

因此：

```text
camera -> recorder          只是 reference="camera:s0:c0" 的一个实例
recorder:s2:c1 当 reference 同样合法, 且必须给出数学上一致的结果
```

模型里**不存在** `reference_source_type` / `reference_path` /
`reference_stream` 这类字段 —— reference 的身份就是既有的

```text
channel_id = "{source_id}:s{stream_index}:c{channel_index}"
```

## 3. 数据流

```text
AudioPlan  (只有被 selection 保留的声道可参与)
    ↓
SyncPlan                      reference_channel_id + target_channel_ids
    ↓  逐 target 调**既有** core.sync_estimate.estimate_pair
SyncEstimate / AudioSyncResult
    ↓  apply_sync_result()  ->  AudioChannel.sync
AudioTimeline                 **唯一** render alignment 权威
    ↓
AudioRouter / AudioMixer  ->  WavExporter
```

同步层只决定"target 在 timeline 上的位置"；真正取样由 PCM 层负责。
**没有第二个 offset 真相源**：offset 只有一处被解释，就是
`core/audio_timeline.resolve_timeline()`。

## 4. 复用而非重写

| 关注点 | 既有实现 | 本周期 |
|---|---|---|
| 时差估计 | `core/sync_estimate.estimate_pair`（两阶段 GCC-PHAT + 相位斜率精估 + 轨迹恒定性分类） | **原样复用**（`delay > 0 = 目标晚到` 语义不变） |
| 质量门阈值 | `core/channel_sync.DEFAULTS` | **同一套标定值**，不另立一套 |
| 整数移位 | `core/sync_fix.shift_stream`（`out[n] = in[n + rint(delay)]`） | 不重复实现；本层只产出 offset |
| PCM 解码 | `core/audio_pcm.AudioPCMReader`（canonical float32） | **同一份样本**，不建第二套解码路径 |
| 文件级锚点管线 | `core/channel_sync.run_channel_sync` | **未改动**，与本周期并存 |

`core/channel_sync.py` / `sync_fix.py` / `sync_estimate.py` /
`mp4_channel_sync.py` 在本周期**零改动**。

## 5. offset 符号（沿用既有实测语义）

```text
core/channel_sync : delay > 0 = 目标轨比锚点晚到
core/sync_fix     : out[n] = in[n + shift], shift = rint(delay)
core/audio_timeline: timeline_sample = source_sample - offset_samples   (同向)
```

因此 **offset 就是 `estimate_pair` 的 `delay_samples`，不做任何符号翻转**：

| fixture | 测量 | 写入 `sync.offset_samples` | 结果 |
|---|---|---|---|
| reference 内容 @9600，target 晚到 60（@9660） | `+60` | `+60` | target 内容落到 timeline 9600 == reference ✓ |
| target 早到 47 | `−47` | `−47` | 前移后与 reference 对齐 ✓ |

取整为 IEEE-754 round-half-to-even（与 `int(np.rint(...))` 同语义）；
分数部分只记录不插值。

## 6. 实测：reference 置换不变量

4 路共享伪噪声，延迟 `0 / +60 / −47 / +83`；**每个 channel 各当一次
reference**，全部 sample-exact：

| reference | A | B | C | D |
|---|---|---|---|---|
| A | 0 | +60 | −47 | +83 |
| B | −60 | 0 | −107 | +23 |
| C | +47 | +107 | 0 | +130 |
| D | −83 | −23 | −130 | 0 |

不变量（全部为正式断言）：

```text
反对称:   off(A|B) == -off(B|A)
坐标平移: off(C|B) == off(C|A) - off(B|A)
          ≡ off(C|A) + off(A|B)        (由反对称等价 —— 两种写法已核对同值)
```

reference 自身 offset **恒为 0**，且**不写入** `AudioChannel.sync`
（它不会被二次修正）。

## 7. 测试

| 测试面 | 套件 | 结果 |
|---|---|---|
| 任意 reference / 置换不变量 / source 顺序 / 跨来源 / selection / 采样率 / timeline 落点 | `audio sync (arbitrary reference)`（L1） | **31 断言全通过** |
| 真实 A7M5 4×mono + 外挂 4CH WAV，任意一路当 reference | `audio sync (arbitrary reference)`（L3） | **5 断言全通过** |
| 全量 L1 | `--level unit` | **421 PASS / 0 FAIL** |
| 全量 L3 | `--level full` | **545 PASS / 0 FAIL**（unit 421 + toolchain 16 + full 108） |

覆盖的确定性要点：

```text
恒定偏移 0/1/7/60/1000/-1/-7/-60/-1000   全部 sample-exact
reference 置换 A/B/C/D 各当一次            全部成功, 反对称 + 平移成立
source 排列 (A,B,C)/(C,A,B)/(B,C,A)        值完全一致
首来源/首流不会被隐式当成 reference         反例已构造
camera<->recorder<->external 六向            全部 sample-exact
同一 stream 内 c0/c1 独立                  静音 c0 不被当成 c1 的结果
4CH / 2CH单流 / 4×mono                     任意一路可当 reference
48k reference + 44.1k target               sync_sample_rate_mismatch
采样率未知                                  sync_unsupported_format
reference/target 被 selection 排除           reference_not_selected / target_not_selected
4CH 同步后落点                              starts=[9683]*4 (同步前 [9600,9660,9553,9683])
```

## 8. 新增 reason codes（稳定契约）

```text
reference_missing              reference_not_selected
target_not_selected            reference_equals_target
sync_channel_not_found         sync_duplicate_target
sync_sample_rate_mismatch      sync_unsupported_format
sync_insufficient_signal       sync_estimation_failed
```

与 v0.7.1 的 `audio_*` 系列**不重复**；全部以模块常量导出
（`core/audio_sync.py`），不使用裸字符串。

## 9. 明确未实现（本周期边界）

```text
drift correction（漂移只检出并报告 drift_ppm / constant, 不修正）
resampling（采样率不一致直接拒绝, 不偷偷转换）
time-stretch / 变速 / 插值 / 分数样本修正
loudness normalization / LUFS / AGC / limiter / compressor / EQ /
降噪 / 频谱处理
音频编码 / PCM 写回编码音轨（Phase 4B）
新 CLI（--audio-* 全部未开放）
多 bus 同时渲染
```

**默认路径不变**：`AudioPlan = None` 仍走 `-map 0` + `-c:a copy`；
没有显式 sync plan 时**不做任何同步**，不存在自动启用。

## 10. 已知边界与后续

* 本周期只支持**恒定**整数样本偏移；真实素材中观测到的慢漂移
  （`non_constant`，39–76 ppm）目前只被检出并拒绝/延后，不做任何补偿。
* 一次同步任务要求 reference 与全部 target **共享采样率**。
* `reference` 必须**自身可测**：若 reference 是静音轨或相关性不足，
  全部 target 会以 `sync_insufficient_signal` 失败 —— 这是正确行为
  （宁可不给结果，也不给一个看起来对齐了的结果）。
* 真实素材实测（A7M5 4×mono）四路音频本身全静音，因此以 camera 轨作
  reference 时必然 `sync_insufficient_signal`；外挂 WAV 可正常作
  reference 并对其余可测声道估计。

---

# Phase 4A — 选择性 MP4 音频保留

## 11. 目标与结论

把已稳定的 `AudioPlan` / `AudioMapSpec` / `AudioTimeline` 链路接到**最终
容器输出**上：用户可以选择性保留 / 重排 / 舍弃原始 MP4 音频，而**不**强制
重新编码音频。

**与视频完全解耦**：音频的选择与保留是独立决定。`core/audio_execution.py`
与 `core/audio_retention.py` 不 import `encoders/` / `preservation/` /
`core.batch_hw`，API 里没有任何视频参数，也不构造 `-c:v`。回归以 AST 检查
import 集合与公开参数名来钉住这一点。

```text
Input MP4
 ├─ Video ──────────────→ output (本层不参与决定)
 └─ Audio ── select/copy ┐
                         ├→ output MP4
External WAV（未来）─────┘
```

## 12. 执行图判定：`AudioExecutionPath`

v0.7.1 里"路由 vs 混音"藏在 `run_audio_render()` 内部。Phase 4A 把它提出来
成为**唯一**的显式概念：

```text
AudioPlan
    ↓  resolve_audio_execution_path()      core/audio_execution.py
NONE / STREAM_COPY / PCM_ROUTE / PCM_MIX
```

| path | 判据 | 由谁执行 |
|---|---|---|
| `NONE` | 无计划 / 无选中声道 | 生产默认 `-map 0` + `-c:a copy`（不经本层） |
| `STREAM_COPY` | 输出恰为若干条**完整源流的自然顺序** | `-map` + `-c:a copy` |
| `PCM_ROUTE` | 源声道的**子集 / 重排** | `core.audio_route`（PCM） |
| `PCM_MIX` | 需要样本级合成 | `core.audio_mix`（PCM） |

* **判定只有一份实现**：`core.audio_process` 的私有 `_resolve_mix_bus()`
  已删除，改为 import `core.audio_execution.mix_intent_bus()`；结构校验复用
  `validate_render_plan()`。优先级（显式 bus → `plan.mix_buses` →
  `plan.mix_mode`）与抽离前逐条一致。
* 回调用 `run_audio_render()` 真跑三种 plan，断言"显式判定 == 实际选中
  的图"（路由 / 混音逐例一致），因此抽离不会与实现分叉。
* `STREAM_COPY` 的判据**比 `AudioMapSpec.strategy` 更严格**：`-map` 的位置
  语义使然（见 §13）。

## 13. 选择性保留：`AudioRetentionSpec`

```text
AudioPlan (+ AudioTimeline)
    ↓  build_audio_retention()             core/audio_retention.py
AudioRetentionSpec
    ├─ default_plan=True  ->  arguments() == []     （默认路径: 什么都不做）
    ├─ no_audio=True      ->  ["-an"]
    ├─ 全部 COPY          ->  ["-map", sel, …, "-c:a", "copy"]
    └─ 需要 PCM           ->  arguments() 抛异常     （Phase 4B）
```

* 选择器一律 `"<input>:a:<audio_position>"`。**输入前缀必须显式写出**：
  `-map a:0` 在 ffmpeg 里不是"第一个输入的第 0 条音频"（缺输入前缀时按流
  索引解释）。`input_index` 为 `None` 明确落成 `0`。
* `stream_index`（容器索引）与 `audio_position`（`-map 0:a:N` 的 N）严格
  区分，**绝不**用前者顶替后者。
* **顺序权威是 `AudioTimeline.output_channel_ids`**：与本层从
  `AudioMapSpec` 推出的顺序不一致即 `audio_retention_order_mismatch` 并
  拒绝 —— 不允许两个顺序真相。
* COPY 单元必须构成输出的**前缀**，否则纯 `-map` 拼接会给出错误音轨顺序。
* `MIXING` 是**转交信号**（交回 PCM 图），不是"计划非法"：无 `-map`、
  `executable=False`。
* 身份仍是 `source → stream → channel`，**不引入** `mp4_audio_index`。

## 14. 默认路径不变（最硬的回归）

```text
AudioPlan = None  ->  default_plan == True  ->  arguments() == []
                  ->  既有 `-map 0` + `-c:a copy` 原样保留
```

"不变"靠**什么都不做**保证：本层不重拼默认 argv。默认规格的
`stream_copyable` 为 `False`（没有输出单元），避免被误读成"保留成功了"。

## 15. 测试

| 级别 | 用例数 | 内容 |
|---|---|---|
| L1 `audio retention/mp4 v0.8 (Phase 4A)` | 36 | 选择器构造、四条路径、身份、顺序权威、视频解耦、报告契约 |
| L3 `audio retention/mp4 v0.8 (Phase 4A)` | 26 | 真实 ffmpeg + ffprobe：产物音轨数量 / 顺序 / codec / 逐样本 |

总计 `--level unit` **457 PASS / 0 FAIL**、`--level full` **607 PASS /
0 FAIL**（重构前基线 421 / 545）。

L3 的关键证据（全部实测）：

```text
真实素材         1 video + 4×mono PCM, 容器 index 1..4 / audio_position 0..3
全保留           4 条音轨, codec pcm_s16le 原样（未重编码）
删除 s3          输出 3 条; 逐样本 == 源的第 0/1/3 条（顺序 + 内容都对）
reorder [2,0,3,1] 输出 4 条; out0==src2, out1==src0, out2==src3, out3==src1
4CH 取单声道      拒绝 stream copy（PCM_ROUTE, 由既有 PCM 图承担）
多声道整流        1 条 -map 0:a:0 + -c:a copy
真实 A7M5 4×mono  ["-map","0:a:0",…,"0:a:3","-c:a","copy"]; 删 2 条 -> (1,3)
默认（无计划）    arguments() == [] 且 -map 0 + -c:a copy 仍保留 4 条
视频重编码        同一音频规格的音轨集合不变（音频决定与视频策略无关）
2×2CH 交错顺序    每条流声道不相邻 -> PCM_ROUTE（拒绝 -map）
```

## 16. 本阶段新增的 reason codes（稳定契约）

```text
audio_retention_source_missing             (core/audio_retention)
audio_retention_selector_unknown
audio_retention_order_mismatch
audio_retention_channel_filter_unsupported
audio_retention_execution_invalid
```

`core/audio_execution.py` 不新增 reason code —— 它的 `issues` 直接携带
`validate_render_plan()` / `validate_mix_bus()` 的既有稳定 reason，避免同一
件事有两个名字。

## 17. 明确未实现（Phase 4A 边界）

```text
音频编码（由 Phase 4B 补上, 见第 18 节起）
声道过滤 filtergraph
码率 / 质量参数
MP4 mux 的完整实现（含 GPAC 路径）
新 CLI（仍为库级能力, 生产默认路径不增加开关）
drift correction / resampling（沿用上一周期边界）
```

---

# Phase 4B — 音频编码 + 最终输出编排

## 18. 目标与结论

第一次允许:

```text
PCM  ->  audio encoder  ->  encoded audio  ->  final output composition
```

**视频编码没有被碰**: 视频产物由调用方给出, Composer 只做 stream copy。
默认生产路径完全不变 —— 新增 audio encoder **不会**让
`AudioPlan = None` 自动重编码音频。

## 19. 四层边界（硬性架构约束）

```text
Audio Domain                        Video Domain
core/audio_encode.py                encoders/ 1kt.py
core/audio_*.py                     preservation/ core/batch_hw.py
AudioPlan / execution path          解码 / 编码 / copy
timeline / route / mix / encode     分辨率 / fps / pixel format
        │                                    │
        │ EncodedAudioOutput                 │ VideoOutputArtifact
        ▼                                    ▼
   ┌────────────────────────────────────────────────┐
   │ OutputComposer      core/output_compose.py     │
   │ 不属于任何一方; 只认识两个**契约**               │
   │ stream mapping / ordering / container / metadata│
   └────────────────────────────────────────────────┘
                        ↓
                   final container
```

依赖严格单向: `Composer -> {AudioOutput, VideoOutput} -> ffmpeg`。

## 20. Audio Domain：`core/audio_encode.py`

职责**很窄** —— 只做 `PCM -> encoded audio`:

```text
AudioPlan
   ↓ run_audio_render()      (既有; routing / mixing / timeline 全在这里)
AudioTimeline + rendered WAV
   ↓ encode_audio()          (本模块; 只编码)
EncodedAudioOutput
```

它**不**读 `AudioPlan`、**不**做 routing/mixing、**不**决定输出顺序、
**不**发明 duration、**不**找 sync reference、**不**碰视频、**不**决定容器
策略。PCM 复用既有渲染 (不重新实现"同一张图只换节点")。

* **采样率**: 来自 `AudioTimeline`; 显式指定不一致 -> 拒绝
  (`audio_encode_sample_rate_mismatch`)，**不偷偷 resample**。
* **声道数与布局**: 来自最终 PCM 输出。ffmpeg 命令里**不传**
  `-ar` / `-ac` / `-channel_layout` —— 中间 WAV 自描述
  (`WAVE_FORMAT_EXTENSIBLE` + channel mask)，因此布局来自实际输出，而不是
  "把输入布局复制到输出"（routing / mixing 之后输入布局可能已经不代表输出）。
* **格式表是显式的**（AAC / PCM / FLAC），不是 codec framework: 没有能力
  探测、没有 fallback 链；参数只有 `format / sample_rate / channel_count /
  bitrate / extra_args`，**没有** preset / quality / loudness / dynamics。
* **临时产物生命周期在本层收口**: 中间渲染 WAV 默认在 `finally` 清理
  （异常路径也不留垃圾），`keep_intermediate=True` 才保留。

## 21. Output Contract

两个域各自产出同一个最小形状: **一个已落盘的文件 + 它的容器身份**。

| 产物 | 表达 | 说明 |
|---|---|---|
| `EncodedAudioOutput` | `path` + `codec` + `sample_rate` + `channel_count` + `expected_frames` + `channel_ids` | 音频域交付的编码音轨 |
| `VideoOutputArtifact` | `path` + `container`（+ `stream`） | 视频域交付的已编码/已复制视频 |

契约里**没有**编码参数: 怎么编码是各自域的事。

`expected_frames` 来自 `AudioTimeline`（**权威**），`probed_frames` 是 ffprobe
实测。AAC 的 priming/padding 会造成少量帧差（实测 48000 -> 49152/49153），
该差异被**记录为 warning** 而不是被忽略；`timeline` 始终是时长权威。

## 22. Composer：`core/output_compose.py`

只做容器层面四件事: **stream mapping / stream ordering / container output /
metadata policy**。

```text
-input <video>             # VideoOutputArtifact
-input <encoded audio> …   # EncodedAudioOutput, 顺序 = 容器音轨顺序
-map 0:v:0 -c:v copy       # 视频一律 stream copy, 无任何视频编码参数
-map 1:a:0 -c:a copy …     # 音频一律 stream copy, 顺序 = 传入顺序
```

* 音频**顺序权威 = 传入顺序**（即 `AudioTimeline.output_channel_ids` 经
  `EncodedAudioOutput.channel_ids` 传递过来）；Composer 不重新判断顺序。
* **不**判断 PCM_ROUTE / PCM_MIX —— 那是 `resolve_audio_execution_path()`
  的唯一权威。
* **不**找 reference、**不**算 offset。
* **不**截断 / **不**循环: 命令里没有 `-shortest`；音频比视频长时容器取二者
  较长。实测: 3s 音频 + 1s 视频 -> 容器 3.0000s，音频 3.0000s。
* 已知边界: 只映射**主**视频流；次视频流（DJI 附加封面图等）属于视频域的
  容器策略（`preservation/` 的 MP4Box 重建路径）。

## 23. 解耦如何被强制

回归里有一条架构断言，每次 `--level unit` 都会跑:

```text
Audio -> Video   音频模块 import {encoders, preservation, batch_hw, …} == 0
Video -> Audio   视频模块 import {core.audio_encode, core.audio_execution,
                  core.audio_retention, core.audio_process,
                  core.audio_timeline, core.audio_mix, core.audio_pcm} == 0
Composer         import 任何音频算法模块 == 0
Audio API        公开参数名里出现 {profile, crf, preset, pix_fmt, fps} == 0
```

⚠️ 精确性: 按**精确模块路径**匹配（`preservation/audio_sync.py` 是既有 GPAC
helper，与 `core.audio_sync` 无关），且禁止集只含**音频域模块** —— 既有模块
不在其列，本阶段不重构它们。

## 24. 测试

| 级别 | 用例数 | 内容 |
|---|---|---|
| L1 `audio encode/compose v0.8 (Phase 4B)` | 29 | 格式表、采样率/声道数校验、契约事实、编排失败路径、架构审计 |
| L3 `audio encode/compose v0.8 (Phase 4B)` | 42 | 真实 encode->decode 逐样本、四态路径、最终编排、视频身份回归、时长策略、sync 端到端、临时产物生命周期 |

L3 的关键证据（全部实测）:

```text
四态路径          none / stream_copy / pcm_route / pcm_mix 全部判定正确
route -> PCM     4CH 取 2 声道 -> PCM_ROUTE; 无损 encode->decode 逐样本一致
mix -> PCM       输出身份 = mixN; gain 0.5/0.25 保留 (peak=0.0924)
AAC              48000 -> 49152 帧 (delta 记录为 warning); 真正解码验证
STREAM_COPY      仍走 -map + -c:a copy (未强制重编码)
编排 (a)(b)(c)   video copy + {copy, route, mix} 音频 -> 单一音轨 MP4
编排 (d)         两条音频 -> 两条音轨, 顺序 = 传入顺序 (按主频验证)
sync 端到端 ⚠️   4CH 各延迟 -> estimate/apply -> encode -> 容器:
                 4 个声道全部落到 timeline 位置 9683 (= base + max(delay)),
                 编码后逐样本一致, 帧数 48130 == timeline
视频身份 ⚠️      四种音频处理下 video basic-stream sha256 完全一致
                 源 = 8ef5fefe558a3eb0 (copy/route/mix/multi/sync 全等)
视频属性         codec / 宽 / 高 / 帧率 与视频产物一致
时长策略         3s 音频 + 1s 视频 -> 容器 3.0000s, 无 -shortest, 不截断
生命周期         中间 WAV 默认清理; keep_intermediate 保留; 失败路径也清理
```

总计 `--level unit` **486 PASS / 0 FAIL**、`--level full` **678 PASS /
0 FAIL**（本阶段前基线 457 / 607；重构前 421 / 545）。

## 25. 本阶段新增的 reason codes（稳定契约）

```text
audio_encode_format_unsupported           (core/audio_encode)
audio_encode_render_failed
audio_encode_failed
audio_encode_no_output
audio_encode_sample_rate_mismatch
audio_encode_verify_failed

output_compose_no_video                   (core/output_compose)
output_compose_audio_missing
output_compose_failed
output_compose_verify_failed
```

## 26. 明确未实现（Phase 4B 边界）

```text
drift correction
resampling
loudness / LUFS / AGC / limiter / compressor / EQ
多 codec 策略框架 / bitrate 框架 / quality preset 框架
新 CLI（除 Phase 4C 的单个 --audio-plan 之外, 仍为库级能力）
次视频流的容器策略（交给 preservation/）
Phase 5 的任何内容
```

---

# Phase 4C — 音频输出接入生产入口

## 27. 解决的问题

Phase 4A/4B 交付了完整音频输出能力, 但生产入口 `1kt.py` 完全触达不到 ——
`core.audio_*` 在生产代码里**零调用者**。Phase 4C 把它接进真实生产管线,
同时不破坏默认路径。

## 28. 生产图（平行分支, 不是串行）

```text
CLI
 ├───────────────┐
 ▼               ▼
Video Pipeline   Audio Planning (--audio-plan)
(既有编码)        │
 │          AudioExecutionPath
 │            ├─ NONE        -> 什么都不做
 │            ├─ STREAM_COPY -> 整流保留选择器
 │            └─ PCM_ROUTE/MIX -> 渲染 + 编码
 │                    ↓
 │              EncodedAudioOutput
 ▼                    │
VideoOutputArtifact ──┘
 └────────┬───────────┘
          ▼
    OutputComposer
          ↓
    final container
```

## 29. CLI：只新增一个参数

```text
--audio-plan <json 文件>          (默认不启用)
```

计划文件形状 (只有这些键; 未知键一律报错):

```json
{
  "version": 1,
  "encode": { "format": "aac" },
  "channels": {
    "select":  ["source:s1:c0", "source:s2:c0"],
    "exclude": [],
    "map":     ["source:s2:c0", "source:s1:c0"]
  },
  "note": "无线麦 -> AAC"
}
```

* 声道身份用**既有** `AudioChannel.id` (`"<source>:s<stream>:c<channel>"`),
  与真实 ffprobe 事实一一对应; 不引入第二套身份;
* `map` 必须与 `select` 是同一集合 (只排序, 不增删);
* 选择为空 -> 默认计划 -> 执行图 `NONE` -> 等于不启用;
* 仅经典软件路径 (x265 / svtav1) 支持; 硬件后端 / Sony / DJI **明确报错**;
* 与 `--channel-sync` 同时给出**明确报错**。

CLI 不出现 `PCM_ROUTE` / `PCM_MIX` / `AudioTimeline` /
`AudioExecutionPath` / `AudioRetentionSpec` 等内部词 —— 用户表达"要什么",
不表达"走哪条代码路径"。

## 30. 为什么 `1kt.py` 不 import 音频模块

`1kt.py` 只 import `production.output`; 计划解析 (`resolve_source_audio_plan`)、
请求加载 (`load_audio_plan_request`)、编排 (`produce_audio_output`) 全部收在
该边界之后。因此 `1kt.py` 里**没有**任何 `core.audio_*`, 也没有
`AudioMixer` / `AudioPCMReader` / `AudioTimeline` 这类内部类型名 —— 由架构
断言逐条钉住 (含**函数内** import, 它们同样是耦合)。

## 31. 视频专用命令派生

生产软件路径的命令是 `-map 0` + `-c:a copy` 一次成型; Composer 需要独立视频
产物。做法是**派生**: 只去掉音频 token (`-map 0`, `-c:a/-c:s/-c:d/-c:t`)
并补 `-an`, **不重写任何编码参数**。回归用视频基本流 sha256 证明"视频没变"。

## 32. 测试

| 级别 | 用例数 | 内容 |
|---|---|---|
| L1 `audio production output v0.8 (Phase 4C)` | 25 | 命令派生、请求解析、`applied` 语义、架构审计 |
| L3 `audio production output v0.8 (Phase 4C)` | 25 | **真实 `1kt.py`**: 默认路径、整流保留、PCM route、PCM mix、sync、视频身份、失败路径 |

L3 的关键证据（全部实测, 生产入口 `python 1kt.py … --encoder x265`）:

```text
T1 默认路径        rc=0, 4 条音轨保持不变, 视频基准 hash 可得
T2 STREAM_COPY     选 2 条 / 重排 -> 输出 2 条音轨, 逐条可解码
T3 PCM_ROUTE       4CH 取 2 声道 -> 输出 2 声道, 可解码 48000 帧
T4 PCM_MIX         path=pcm_mix, 身份 mixN, gain 保留 (peak=0.0924)
T5 sync            估计 -> 应用 -> 编码 -> 容器, 4 条音轨逐条可解码
T6 视频身份 ⚠️     同素材下基本流 sha256 与默认路径**完全一致**
                   6a242623cae1a097 (default / keep / mix / sync 全等)
                   4CH 素材另有独立基准 147b7a7dac12d803 (加计划后不变)
T7 计划非法        选不存在的声道 / 未知键 -> 明确失败, 不产出半成品
T8 空选择计划      rc=0, 与默认路径一致 (4 轨), 视频 hash 不变
T9 路径冲突        硬件后端 / 与 --channel-sync 同用 -> rc=2 明确报错
```

总计 `--level unit` **511 PASS / 0 FAIL**、`--level full` **703 PASS /
0 FAIL**（本阶段前基线 486 / 678；重构前 421 / 545）。

## 33. 本阶段新增的 reason codes（稳定契约）

```text
audio_request_missing                     (core/audio_request)
audio_request_parse
audio_request_version
audio_request_invalid
audio_request_selection

production_audio_plan_invalid             (production/output)
production_audio_retain_invalid
production_audio_selector_mismatch
production_verify_failed
```

## 34. 明确未实现（Phase 4C 边界）

```text
drift correction / resampling / loudness / LUFS / AGC / limiter / compressor
自动多文件同步
多 codec 能力框架 / bitrate 框架 / quality preset 框架
硬件后端与 Sony/DJI 保留管线上的音频计划（明确报错, 未实现）
次视频流的容器策略（交给 preservation/）
Phase 5 的任何内容
```

---

# Phase 5 — 格式感知 alignment + 编码继承 + 外挂音频

## 35. 解决的问题

Phase 4C 之后音频已经能进生产输出，但三件事仍然靠人肉约定：

```text
1. 输入是 PCM 还是 compressed?   没有人显式判定 —— 对齐与编码全靠调用方自觉
2. 输出该用什么 codec / 码率?     AudioFormatSpec 默认 AAC, 于是 PCM 输入也会被
                                 悄悄转成 AAC（§6 明确禁止这件事）
3. 摄影机旁边的独立录音文件?      必须手工写进计划文件, 没有任何发现规则
```

Phase 5 把这三件事变成**确定性规则 + 独立可测的模块**：

```text
core/audio_format.py            输入格式分类 + alignment 策略 + 输出编码决议
core/audio_external.py          外挂音频发现（文件名规则）+ 并入既有 AudioSource
core/audio_output_structure.py  有效映射 -> 输出流结构（几条流, 各含哪些声道）
```

三个模块都只读 `AudioPlan` / `AudioStream` / `effective_mapping()` 与既有的
`output_tracks()`：**不**读 PCM、**不**拼 argv、**不**起进程、**不**认识
`encoders/` 与 `preservation/`（AST 断言钉住）。视频域与元数据后端**一行未改**。

## 36. 输入格式分类

判据只有一条：ffprobe 的 `codec_name`。

```text
pcm_*（pcm_s16le / pcm_s24le / pcm_s32le / pcm_f32le / pcm_f64le / pcm_s24be …）-> PCM
其它一切 audio codec（aac / opus / flac / mp3 / vorbis / ac3 / eac3 / dts …）    -> COMPRESSED
未知或缺失 codec                                                                  -> COMPRESSED（保守）
```

没有第二套 codec probe：外挂文件走 `core.probe.probe_streams()`，与
`probe_source()` **共用同一个 `-show_entries` 常量**，因此外挂来源的 raw stream
形状与容器来源逐字段一致（唯一区别是前者允许没有视频流）。

## 37. alignment 策略

`resolve_alignment()` 是纯函数，判定表穷举：

| requested | 参与来源格式 | enabled | reason |
|---|---|---|---|
| `disabled` | 任意 | False | `alignment_disabled_by_request` |
| `enabled` | 全 PCM | True | `alignment_enabled_pcm` |
| `enabled` | 含 compressed（含混合） | True + **warning** | `alignment_enabled_compressed` |
| `auto` | 全 PCM | True | `alignment_default_pcm` |
| `auto` | 含 compressed | False | `alignment_default_compressed` / `_mixed` |

* **`enabled` ≠ 自动猜 reference**。reference 仍然只能由
  `SyncPlan.reference_channel_id` 显式给出；没有 reference 时 alignment 只是
  "允许进入图"，不产生 offset，也不改动计划。
* compressed 被显式要求 alignment 时给出 warning 原文，并走
  **decode → PCM → align → 重编码**；禁止在压缩包上"伪造时间戳平移"。
* 混合（PCM + compressed）在 `auto` 下 DISABLED：compressed 一侧有否决权。

## 38. 输出编码的优先级链

```text
manual override  >  source-derived defaults  >  encoder default
```

* 请求解析时记录 `ExplicitFormat`（用户真的拧过哪些旋钮），因此 `{"encode":
  {"bitrate": "128k"}}` 里的 format 是"没说"，而不是"要 AAC"。
* **PCM 输入默认输出 PCM**；compressed 保持 source codec + source bitrate；
  `pcm_*`→PCM、`aac`→AAC、`opus`→Opus、`flac`→FLAC；表里没有的 codec 退回
  encoder default 并记 `audio_format_inherit_unavailable` warning。
* codec 继承**按输出流**做：一条 AAC 外挂录音与一条 PCM 源在同一份输出里各自
  保持自己的 codec。
* **lossless 输出不允许 bitrate**：PCM/FLAC 带 `-b:a` 明确拒绝
  （`audio_format_bitrate_not_applicable`）。
* 采样率永远来自 `AudioTimeline`，**不偷偷 resample**。

## 39. 外挂音频发现

```text
audio filename stem 必须以 video stem 精确开头（case-insensitive）
且 video stem 之后的第一个字符必须是 结束 / "-" / "_"
只扫描视频所在的**那一个**目录, 只考虑音频扩展名白名单（不含 .mp4/.mov）
```

排序完全确定：

```text
rank 0  主干完全相同          clip001.wav
rank 1  纯数字序号（归一化）  clip001_1.wav / clip001_01.wav / clip001_001.wav
rank 2  其它合法后缀          clip001-rec.wav / clip001_audio.wav
最终 tie-break = 完整文件名的 case-insensitive lexical order
```

* `_1` / `_01` / `_001` 的序号都归一化为 1，但**仍然是不同候选**；顺序只由
  文件名决定，与文件系统枚举顺序无关。
* **多候选全部纳入**；**没有候选不报错**，视频照常用自己的音频。
* 外挂来源的身份就是**带扩展名的文件名**，并且一进来就是普通的
  `AudioSource` / `AudioStream` —— 没有第二套模型。

## 40. 输出流结构

```text
主来源声道    -> 按既有输出单元切分（相邻且同一条源流 = 一条输出流）
外挂来源声道  -> 按结构策略切分 (manual > input source mapping > default)
```

```text
SOURCE (默认)  跟随每个输入来源自身的流结构     -> 4 个 mono 文件 = 4 × mono
INDEPENDENT    一个声道 = 一条输出流            -> 4CH 外挂 = 4 × mono
GROUPED(n)     连续 n 声道 = 一条输出流, 跨文件继续成组
                                               -> 4CH + 2 = 2 × stereo
                                               -> 8CH + 2 = 4 × stereo
                                               -> 3CH + 2 = 2CH + 1CH（剩余保留）
```

**不变量**：`Σ len(group) == len(effective_mapping)`，做不到即报
`audio_grouping_channel_lost`，绝不"部分成功然后偷偷丢声道"。主来源的输出结构
**不被本模块改变** —— 这是"mapping 默认保持"的结构性保证。

## 41. alignment 与输出结构的关系（本阶段最重要的一条）

一旦真的产生了**非零 offset**，整份输出必须共用**同一份 render window**：

```text
-map + stream copy  = 把源字节原样搬进容器, 没有"时间原点"可言
带 offset 的声道    = 按统一 timeline 重新取样, window 起点可能不是 0
                      （一条 target 前移 480 样本 -> union window 从 -480 开始）
```

两者混在同一个容器里就是两条流各有各的原点，表现出来恰好是"对齐没生效"。
因此一旦有非零 offset：放弃 stream copy、所有输出流共用
`core.audio_encode.shared_render_window()`（向 `AudioTimeline` 要一份 window）
渲染、**组数与每组声道保持不变**。同理 `_stream_copyable()` 现在把"带非零已
应用 offset"也算作不可 copy。

## 42. AAC 容器从 ADTS 改为 MP4 家族（正确性修正）

```text
PCM -> AAC(ADTS) -> 解码    实测内容整体后移 1024 样本（编码器 priming, ADTS 无法携带）
PCM -> AAC(MP4)  -> 解码    实测内容位置与输入一致（priming 写进 edit list）
PCM -> Opus(Ogg) -> 解码    实测一致（pre-skip）
```

alignment 是样本级操作；若重编码这一步自己就把内容挪 1024 样本，"重编码对齐"
就没有意义。同时 `_probe_audio()` 不再把 `nb_frames` 当样本数：MP4 家族的音频流
报的是 **packet 数**，因此现在以 `duration × sample_rate` 为权威样本数。

## 43. 请求 schema 的加法式扩展（version 仍为 1）

```json
{
  "version": 1,
  "encode":    { "format": "opus", "bitrate": "96k" },
  "channels":  { "select": [...], "exclude": [...], "map": [...] },
  "alignment": "auto" | "enabled" | "disabled",
  "sync":      { "reference": "<channel_id>" },
  "mapping":   { "mode": "source" | "independent" | "grouped", "group_size": 2 },
  "external":  {},
  "note":      "…"
}
```

四个新键都是**可选**的：旧文件语义完全不变，因此版本号仍是 1（版本号守的是
不兼容变更）。未知键依然一律拒绝。**CLI 没有新增参数** —— `--audio-plan` 仍是
唯一的音频入口。

## 44. 测试

| 套件 | 级别 | 断言数 | 覆盖 |
|---|---|---|---|
| `audio format/alignment v0.8 (Phase 5)` | L1 | 40 | 格式分类表 / alignment 决策表（含 compressed warning 原文）/ 编码优先级链 / PCM+bitrate 拒绝 / 策略层架构审计 |
| `audio format/alignment v0.8 (Phase 5)` | L3 | 33 | 真实 ffmpeg：PCM 默认允许对齐、compressed 默认原样保留、AAC/Opus 显式对齐（真实 480 样本延迟 + 互相关残差 0）、手动 bitrate 真的到达编码器、只给 bitrate 也明确拒绝、视频基本流 sha256 不变 |
| `audio external v0.8 (Phase 5)` | L1 | 36 | §18 文件名规则表 / natural sort / 前导零 / 确定性 tie-break / 多候选 / 无候选 / §36 Case A–D / 奇数剩余 / 声道守恒 / 策略解析 / 架构审计 |
| `audio external v0.8 (Phase 5)` | L3 | 19 | 真实 WAV/AAC/Opus 被发现并入 / 真实 1kt.py 端到端 / 4-6-8-3CH × mapping 矩阵 / 声道守恒 / 外挂 PCM WAV 作为 alignment 目标（真实 480 样本延迟 + 残差 0）/ 视频 hash 不变 |

实测证据（可直接复查）：

```text
PCM + auto                    alignment_default_pcm, applied=0（不猜 reference）
AAC + auto                    alignment_default_compressed, 无 warning, 流原样 copy
Opus 目标 + 显式 alignment     offset=480（注入 480）, 重编码后 lag=0
AAC 目标 + 显式 alignment      offset=1504（注入 480 + AAC 解码 priming 1024）, lag=0
4CH 外挂 + independent         4 × mono
4CH 外挂 + grouped(2)          2 × stereo
6CH / 8CH + grouped(2)         3 × stereo / 4 × stereo
3CH + grouped(2)               2CH + 1CH（不丢不复制）
视频基本流 sha256               每种音频处理下都与默认路径一致
```

## 45. 本阶段新增的 reason / warning 契约

```text
audio_alignment_no_channels
audio_format_bitrate_not_applicable
audio_format_inherit_unavailable              (warning)
audio_mapping_policy_invalid
audio_grouping_channel_lost
external_video_stem_empty
external_directory_missing / external_directory_unreadable
external_probe_failed
production_alignment_failed
production_group_input_unknown
```

## 46. 明确未实现（Phase 5 边界）

```text
drift correction / resampling / loudness / LUFS / AGC / limiter / compressor
自动多文件同步（外挂音频只按文件名规则发现, 不做内容匹配）
quality / preset / VBR 策略框架, codec 能力数据库
多 mixer sink 的输出结构（混音图仍然只产出一条流）
硬件后端与 Sony/DJI 保留管线上的音频计划（仍然明确报错）
P5
```

## 47. 版本与发布

```text
VERSION                     0.7.1 -> 0.8.0
tag                         v0.8.0（v0.7.0 / v0.7.1-rc1 / v0.7.1 均未改动）
package                     release/build_release.py（既有打包脚本, 未引入新系统）
                            新增顶层目录 production/ 进包（此前漏掉会让 1kt.py 起不来）
```

功能版本而不是补丁版本：这是**一整块能力**（格式感知 alignment、compressed
重编码、外挂音频发现与 mapping），不是 bugfix 集合。

回归计数（v0.8.0 冻结值，`python tests\full_autotest.py --level full`）：

```text
unit       590 PASS / 0 FAIL
toolchain   16 PASS / 0 FAIL
full       253 PASS / 0 FAIL
--------------------------------
合计       859 PASS / 0 FAIL
```

相对 Phase 4C 基线（unit 511 / full 728）新增 **131** 条断言，**0** 条 FAIL。
唯一"名字变了"的用例是 `p4b.format 只有 3 个显式格式` → `只有 4 个显式格式`
（本阶段按 §9 加入 `OPUS`），它同时被**加强**：现在还会断言 Opus 的
encoder/container/suffix 以及"只有有损格式接受 bitrate"。其余 727 条断言
逐条仍在且全部通过（由 `work/tools/p5_reconcile.py` 对
`work/_after/report_p4c_final.json` 逐项比对得出）。

> ⚠️ 发布顺序说明：tag `v0.8.0` 指向 `3b4cf17`（打包用的那个 commit，当时
> full = 856 PASS / 0 FAIL）。随后在 `main` 上补了 3 条"外挂 PCM 作为 alignment
> 目标"的生产路径断言（full = 859 PASS / 0 FAIL），tag 未移动 —— 发布包记录的
> commit 就是它被构建时的那个 commit。

