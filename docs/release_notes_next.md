# 下一开发周期 — 任意 reference 延迟矫正 + Phase 4A 选择性 MP4 音频保留

> **状态**：已实现（**尚未发布**，未分配版本号；不属于 v0.7.1）
> **基线**：`v0.7.1`（tag `v0.7.1`，commit `e613b07`）
> **范围**：
> * 任意 reference 的**恒定**样本偏移矫正（第 1–10 节）；
> * Phase 4A 选择性 MP4 音频保留（第 11 节）。
>
> 漂移校正 / resampling / time-stretch / **音频编码** / PCM 写回 / 新 CLI
> **均未实现**。
>
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
音频编码（AAC / Opus / …）
PCM 写回编码音轨（routed packet 的 encode + mux write-back）
声道过滤 filtergraph
码率 / 质量参数
MP4 mux 的完整实现（含 GPAC 路径）
新 CLI（仍为库级能力, 生产默认路径不增加开关）
drift correction / resampling（沿用上一周期边界）
```

