# v0.7.1 发布说明 — 音频模型与 PCM 处理（Phase 1 + Phase 2 + Phase 3A + Phase 3B）

> **状态**：Phase 1（音频中间模型）与 Phase 2（多来源 + 选择 + 通道映射 +
> 可执行 AudioPlan/AudioMapSpec）均已完成。
> **默认音频路径与输出行为未改变** —— 全部为内部能力层，不新增 CLI、
> 不改变 MP4 音频处理方式（§21：`AudioPlan = None` 仍走旧路径）。
> **基线**：`v0.7.0`（已于 2026-09-14 并入 `main`，见 §0）。

---

## 0. 基线整合（2026-09-14）

`v0.7.0`（hardware-decode integration）此前不在 `main` 的祖先链上 ——
它是一条独立线的 tag，`main` 反而多出两个 licensing commit。本轮已把两者
合并为**统一开发基线**：

```text
                        ┌── 015bee8 licensing
                        ├── 62c5504 licensing
                        ├── b933c18 feat(audio): add audio track model for v0.7.1
main (now) ─────────────┤
                        └── merge: integrate v0.7.0 hardware decode into main
                                 │
v0.7.0 (1a41fff) ────────────────┘   ← 因此 v0.7.0 ∈ ancestors(main)
```

合并后 `main` 同时具备三份内容：**v0.7.0 hardware decode**、
**licensing commits**、**v0.7.1 Audio Model Phase 1**。三条互不冲突：

| 关注点 | 冲突情况 |
|---|---|
| `1kt.py` | 无冲突（v0.7.1 未改 `1kt.py`；v0.7.0 只加 `--hw-decode` 旗标与接线） |
| `core/` | 无冲突（v0.7.1 是纯新增模块；v0.7.0 只改 `batch_hw.py` / `config.py`） |
| `tests/` | 无冲突（v0.7.1 改 `tests/full_autotest.py`；v0.7.0 新增 `tests/hwdecode/`，两者独立） |
| `encoders/` | 无冲突（v0.7.0 新增 `hwdecode.py`/`integrity.py` 并让读者可参数化，默认值仍是 `avsw`） |
| `probe` | 无冲突（v0.7.0 未改 `core/probe.py`） |
| 文档 | 3 处文本冲突：`README.md`、`docs/README.md`、`VERSION` —— 逐项人工合并（保留两侧内容，`VERSION` 保持 `0.7.1`） |

### 0.1 一并修正的既有缺陷（非本轮引入）

`v0.7.0` 里 `docs/hardware-decode/toolchain-provenance.json` 记录的补丁
hash/字节数与**实际提交的补丁文件**不符，导致 `harness provenance`
在干净检出上必然失败。根因：补丁在 `core.autocrlf=true` 下于**提交时**被
重新编码为 LF，而 provenance 记录的是提交前的 CRLF 形态。

处理方式（**不改补丁、不改策略、不改 harness**）：

* 把两条记录的 `sha256` / `bytes` 更正为**提交后的规范 LF 形态**
  （`= git show v0.7.0:<file>` 的字节），并保留原值于 `sha256_note` 供追溯；
* 新增 `.gitattributes`（`*.patch text eol=lf`），使补丁字节跨检出稳定；
* 校验补丁**内容**仍与记录的 `scope` 完全一致：nvenc `+18/-0` / 2 文件、
  qsv `+15/-1` / 2 文件。

修正后 `harness provenance` 通过，`HD-A01/A02/A08` 恢复 PASS。

---

## 1. Phase 1 — 音频中间模型

v0.7.1 的目标不是增加最终输出能力，而是建立一个**稳定、可测试、可扩展的
内部音频数据模型**，使后续处理链能够明确区分"流 / 声道 / 轨道 / 输出轨道"。

```text
输入文件
    ↓
FFprobe / Audio Probe          core.probe.probe_source()
    ↓
Audio Stream                   core.audio_models.AudioStream
    ↓
Audio Track                    AudioTrackBuilder / AudioTrack
    ↓
Channel Track                  AudioChannel（逐声道，身份独立）
    ↓
Channel Sync                   core.channel_sync（既有算法，未改动）
    ↓
Audio Plan                     AudioPlan
    ↓
后续 Preserve / Select / Mix / WAV Export      ← Phase 2，未实现
```

职责划分（严格遵循"数据模型"与"执行逻辑"分离）：

| 层 | 模块 | 职责 |
|---|---|---|
| Probe | `core/probe.py` + `core/audio_probe.py` | FFprobe JSON → 原始 stream 信息 → `AudioStream` |
| Model | `core/audio_models.py` | `AudioStream` / `AudioChannel` / `AudioTrack` / `AudioPlan` / `AudioSyncResult` |
| Sync | `core/channel_sync.py` | 同步分析（**未改动**）；模型只**读取**其报告 |
| Processing | （不存在） | Phase 2: `AudioSelector` / `AudioMapper` / `AudioMixer` / `WaveExporter` |

`core/audio_models.py` **不导入项目内任何模块**（与 `core/models.py` 的依赖
规则一致），因此可被任意层安全复用，也不会因为音频模型引入新的第三方依赖
（纯标准库；`core/channel_sync` 的 numpy/scipy 可选依赖关系不变）。

---

## 2. 数据模型

### 2.1 AudioStream（§4.1）

一条 FFprobe 识别出来的原始音频流：

| 字段 | 说明 |
|---|---|
| `stream_index` | 容器 stream index（ffprobe 的 `index`） |
| `codec_name` / `codec_long_name` / `codec_tag_string` | 编码格式事实 |
| `sample_rate` / `sample_format` | 缺失时 `0` / `unknown`（**不猜位深**） |
| `channel_count` / `channel_layout` | `channel_layout` **可能为空** |
| `duration_sec` / `bit_rate` / `start_time_sec` | 可能缺失或不可靠，缺失即 `None` |
| `language` / `title` / `metadata` | 来自 `stream_tags`，可能整体缺失 |
| `disposition` | ffprobe disposition dict（`default` 等） |
| `audio_position` | 该流在音频流序列中的 0-based 位置 = `-map 0:a:N` = channel_sync 报告的 `stream` 字段 |
| `raw` | ffprobe 原始 dict（只读副本，供既有代码复用） |

派生属性：`channel_names` / `layout_verified` / `layout_source` /
`is_default` / `is_mono` / `duration` / `channels()`。

**未知布局不丢流**：`channel_layout` 缺失或无法识别时按
`C0/C1/…` positional 命名，`layout_source` 如实标注
（`layout` / `channel_count` / `layout_missing` / `layout_unknown` /
`layout_count_mismatch`），`layout_verified=False`。声名与声道数不符时
**不按错位命名**（同样退化为 positional）。

### 2.2 AudioChannel（§5）

统一抽象"1 个 4-channel 流"与"4 个 mono 流"：

```text
Stream #2 / Channel 0..3        →  (s2,c0) (s2,c1) (s2,c2) (s2,c3)
Stream #2,#3,#4,#5 / Channel 0  →  (s2,c0) (s3,c0) (s4,c0) (s5,c0)
```

字段：`stream_index` / `channel_index` / `channel_count` /
`source_channel_name`（可确定时如 `FL`/`FC`，否则 `None`）/ `sample_rate` /
`sample_format` / `enabled` / `role` / `role_reason` / `sync`。
`id` = `"s{stream}c{channel}"`（可追溯原始 stream/channel）。

### 2.3 AudioTrack（§6）

参与输出决策的基本对象：

```text
track_id, source_stream_index, channels[], channel_indices[], channel_count,
sample_rate, sample_format, layout, layout_verified, enabled, role,
sync_status, sync_offset_samples, sync_offset_ms, metadata, source_ids[]
```

三种构建形态：

| 输入 | per_stream（默认） | per_channel |
|---|---|---|
| 1×4CH 流 | 1 track / `[0,1,2,3]` | 4 track / 各 `[0]` |
| 1×mono 流 | 1 track / `[0]` | 1 track / `[0]` |
| 4×mono 流 | 4 track / 各 `[0]` | 4 track / 各 `[0]` |
| 2×stereo 流 | 2 track / 各 `[0,1]` | 4 track / 各 `[0]` |

**4CH 约束**：默认 per-stream 建 1 个 track，但 `AudioChannel` 逐声道独立保留，
`select_channels([2])` / `without_channels([1,2])` 可安全取子集 —— 因此
"4CH 流塌缩成不可再分的 1 个对象"在结构上不可能发生（见测试
`audio.track.4CH 不得塌缩成不可再分的 1 个对象`）。

### 2.4 AudioPlan（§8）

声明式描述"本次任务准备如何处理音频"，**不执行任何逻辑**：

```text
input_tracks, selected_tracks, preserve_original,
output_sample_rate, output_sample_format,
mix_mode?, channel_map?, wav_outputs?      ← Phase 2 预留，本阶段只存不解释
```

`AudioPlan.from_tracks()` 默认全选 + `preserve_original=True`，即"旧行为"的
模型表达（`is_default`）。`select()` 忽略未知 `track_id`。

### 2.5 AudioSyncResult（§7）

同步结果**不改身份**（只写 `sync` 字段）：

| 字段 | 说明 |
|---|---|
| `status` | `not_processed` / `not_applicable` / `already_aligned` / `success` / `low_confidence` / `non_constant` / `out_of_range` / `recheck_failed` / `failed` |
| `offset_samples` / `offset_ms` | `success` 时取实际**移位量** `shift_samples`（缺失时退化为测量延迟） |
| `quality` | channel_sync 的 `confidence` |
| `anchor` | 锚点标识（如 `stream:2`） |
| `reason` | 轨道级 `decision`/`reason` 原文（如 `low_confidence`） |
| `drift_ppm` / `constant` / `polarity` / `usable_frames` / `rms_dbfs` | 诊断字段 |
| `algo_version` / `source` / `warnings` | 溯源与告警 |

状态映射（`ChannelSyncReport.resolve`，**只读既有报告，不重跑算法**）：

| channel_sync 报告 | 模型 status |
|---|---|
| `decision=anchor` / `already_aligned` | `already_aligned` |
| `decision=fixed` | `success` |
| `reason=low_confidence` / `insufficient_frames` | `low_confidence` |
| `reason=non_constant` | `non_constant` |
| `reason=out_of_range` | `out_of_range` |
| `reason=recheck_residual` | `recheck_failed` |
| 文件级 `not_eligible` / `tool_missing` | `not_applicable` |
| 文件级 `measure_failed` / `verify_failed` 及其它轨道级失败 | `failed` |

Track 级字段是**派生缓存**：`AudioTrack.set_sync()` → `refresh_sync()` 从声道
重算（全部 success 才 `success`；任一未处理即 `not_processed`），因此不存在
"track 说 success 而声道说 failed"的双真相。

---

## 3. 序列化（§十三）

所有模型提供 `to_dict()` / `from_dict()`：

- 稳定、JSON-compatible、不含不可序列化对象（无 Path / numpy / callable）；
- 字段明确，`None` 与空容器被省略，enum 输出稳定字符串；
- `to_dict()` 返回**深拷贝**，外部修改不回写模型；
- `from_dict()` 对未知键/未知 enum 值/未来版本号一律安全回退，不抛异常。

示例（channel）：

```json
{
  "stream_index": 2,
  "channel_index": 0,
  "sample_rate": 48000,
  "sample_format": "s32",
  "enabled": true,
  "sync": {"status": "success", "offset_samples": 960, "offset_ms": 20.0}
}
```

`AudioPlan.to_dict()` 顶层带 `model_version`（当前 `1`），供日志、调试与未来
Collector 判断 schema。

---

## 4. 与既有 channel_sync 的连接

- `core/channel_sync.py` / `sync_estimate.py` / `sync_fix.py` / `mp4_channel_sync.py`
  **零改动**：算法、阈值、决策门、报告 schema 全部照旧。
- 模型侧新增**读取侧**入口：

```python
from core.audio_models import ChannelSyncReport, apply_channel_sync_report
from core.audio_probe import audio_probe_of

probe = audio_probe_of(raw_streams)        # 或 audio_probe_from_file(ffprobe, src)
plan = probe.plan()                        # 默认全选 + preserve_original
apply_channel_sync_report(plan, report)    # report = run_channel_sync(...) 的返回 dict
```

- 关联键是 `AudioStream.audio_position` ↔ 报告的 `stream` 字段，因此
  **4 条 mono 流会被分别对应到 4 行**，不会因容器 index 与音频序号不同而错配
  （real A7M5 素材：容器 index 1..4 ↔ 音频序号 0..3）。
- `AudioProbeResult.tracks_for_sync()` 使用 per-stream 映射（channel_sync 的报告
  是逐音频流的）。
- 同步后 `AudioTrack.source_ids` 仍为原始 `s{stream}c{channel}` 列表。

---

## 5. 默认生产路径：**unchanged**

| 检查项 | v0.7.0 | v0.7.1 |
|---|---|---|
| x265/SVT-AV1 `build_command` | `-map 0` + `-c:a copy` | **同左（未改动）** |
| Sony/DJI 保留管线音频 | GPAC 从源容器复制 | **同左（未改动）** |
| `--channel-sync` 行为 | 算法与阈值不变 | **同左（未改动）** |
| 新增 CLI | — | **无** |
| 硬件解码（v0.7.0 并入） | 默认 `off` = 软解 | **同左**：`off` 时 `reader="avsw"`，既不走白名单路由也不跑完整性闸门，argv 形态与 v0.6.2 一致（矩阵 HD-B05 断言） |
| 新依赖 | — | **无（纯标准库）** |
| `probe_source()` 返回 | `(summary, streams)` | 同签名；stream dict **仅新增 `tags` 键**（`-show_entries` 增加 `stream_tags`）。CSV 字段是显式白名单，既有键与数值不变 |

即：`AudioPlan = None`（模型未被调用）时，行为与 v0.7.0 完全一致；
`--hw-decode off`（默认）时，行为与本轮合并前的 `main` 完全一致。新模型是
**内部能力**：Phase 2 已把它扩展到"选择 + 通道映射 + 可执行规格"，
但仍**没有任何调用方在生产路径上执行它**（§8 起）。

---

## 6. 测试

新增用例（`tests/full_autotest.py`）：

| 组 | 用例数 | 内容 |
|---|---|---|
| L1 `audio model v0.7.1`（新增套件） | 52 断言 | T1 mono / T2 stereo / T3 4CH / T4 4×mono / T5 2×stereo / T6 unknown layout（含未知布局字符串、布局与声道数不符）/ T7 metadata 缺失与存在 / T8 sample format 缺失与未知；非音频流忽略与 `audio_position`；track 映射（per_stream / per_channel / 单通道选择 / 剔除通道 / 4×mono / 2×stereo）；role 不自动推断；plan 默认/选择/预留字段；同步集成（success+offset_samples/offset_ms、anchor、low_confidence、non_constant、文件级状态、未知 reason）；序列化往返与深拷贝、未知 enum/未知键安全回退 |
| L3 `audio model probe v0.7.1`（新增套件） | 15 断言 | 真实 ffmpeg 合成素材 + 真实 ffprobe：单流 4CH（`channel_layout=4.0`）、4×mono、2×stereo；真实 A7M5 4CH 素材（`channel_layout` 缺失的 4×mono PCM）；与 `eligible_audio` 生产判定一致性（4×mono 可对齐 / 2ch 与 4CH 仍被拒）；同步报告按音频序号回填 4 条 track 且身份可追溯 |

既有测试全部保留并回归通过。本轮（基线整合后）实测：

| 测试面 | 命令 | 结果 |
|---|---|---|
| L1 unit | `python tests/full_autotest.py --level unit` | **230 PASS / 0 FAIL** |
| L1+L2+L3 full | `python tests/full_autotest.py --level full` | **315 PASS / 0 FAIL** |
| 音频模型 L1 | 套件 `audio model v0.7.1` | 52/52 PASS |
| 音频模型 L3 | 套件 `audio model probe v0.7.1` | 15/15 PASS |
| 硬件解码 provenance | `python -m tests.hwdecode.harness provenance` | **ok = True**（二进制 ×4 + 补丁 ×2） |
| 硬件解码矩阵漂移 | `python -m tests.hwdecode.harness check-matrix` | 83 用例全部有实现 |
| 硬件解码矩阵 | `python -m tests.hwdecode.harness run --phase 1..6` | 见 §0 与 `work/avhw_integration/results/summary.json` |

报告落盘 `work/autotest/autotest_report.{json,md}`（`full_autotest`）与
`work/avhw_integration/results/summary.json`（硬件解码矩阵）。

---

## 7. Phase 1 阶段**未**实现（现已由 Phase 2 补足一部分）

```text
4CH → stereo / mono / 4CH 混音, weighted mixing
gain / limiter / compressor / clipping protection
sample-rate conversion / resampling / drift correction
WAVE 导出与命名
MP4 音轨选择、通道映射、mux 变更、audio codec 变更
DAW 式音频编辑
```

也**未修改**：视频缩放、x265 缩放规则、channel-sync 算法。至于硬件解码 ——
本轮只把 v0.7.0 的既有实现**并入 `main`**，未重新设计：架构、runtime
白名单、fallback 策略、完整性闸门、binary provenance、seek 拒绝、
`_encoded_ok()`、NVEncC/QSV pipeline 补丁与测试 harness 全部保持原样
（唯一改动是 §0.1 的 provenance 元数据更正与 `.gitattributes`）。
`AudioPlan` 的 `mix_mode` / `channel_map` / `wav_outputs` 仅为预留字段，
本阶段只保存取值，不解释、不执行。

---

# Phase 2 — AudioSource + Selection + Channel Mapping

> 目标：在 Phase 1 的模型上补足**多来源音频**，并实现**音频选择**与
> **通道映射**。仍然只生成"可 dry-run 的执行规格"，**不执行 ffmpeg**，
> **不新增 CLI**。

## 8. 本阶段做了什么

```text
AudioSource                    一个物理音频来源 (media / wav / external)
    ↓
AudioStream                    来源内的一条音频流 (source_id + stream_index)
    ↓
AudioChannel                   流内可独立寻址的声道 (三维身份)
    ↓
AudioTrack                     逻辑输入轨 (不限定 mono; 4CH 流 -> 1 track / 4 channel)
    ↓
Selection                      哪些 source channel 被保留
    ↓
Channel Mapping                已选声道按什么顺序进入输出
    ↓
AudioPlan                      声明式计划 (sources/selected_channels/channel_mapping/output_tracks)
    ↓
AudioMapSpec                   可 dry-run 的执行规格 (无 PCM / 无 ffmpeg / JSON)
```

新增模块 `core/audio_plan.py`（规划层：选择 / 映射 / 校验 / 规格），
`core/audio_models.py` 扩展出 `AudioSource` / `AudioTiming` / `AudioSourceBuilder`，
`core/audio_probe.py` 增加来源维度。

### 8.1 AudioSource（§3）

```text
source_id, source_type(media|wav|external), path, display_name,
streams[], metadata, timing, input_index
```

* **WAV 不是另一套模型**：`source_type` 只影响读取方式；media 与 wav 产出的
  都是同一套 `AudioStream` / `AudioChannel`（`AudioSourceType` 一个枚举区分）。
* `input_index` 记录该来源在 ffmpeg 输入序列（`-i` 顺序）中的位置 ——
  规划 map/filtergraph 规格时必须知道"用第几个输入"，而不是从路径猜。
* `AudioSourceBuilder` 组装多来源；重复 `source_id` **明确报错**，不静默覆盖。

### 8.2 身份升级为三维（§4/§5/§14）

| 维度 | 字段 | 说明 |
|---|---|---|
| 来源 | `source_id` | `camera` / `wav01` / … |
| 流 | `stream_index` | **容器** index（ffprobe 的 `index`） |
| 音频序号 | `audio_position` | 该来源内 0-based 音频序号 = `-map N:a:M` = channel_sync 报告的 `stream` 字段 |
| 声道 | `channel_index` | 流内物理位置，不做重命名 |

`AudioChannel.id` = `"{source_id}:s{stream_index}:c{channel_index}"`
（如 `camera:s2:c0`），`AudioTrack.track_id` 在多来源时前缀 `"{source_id}:"`。

**跨来源同名不碰撞**：`camera:s0:c0` 与 `recorder:s0:c0` 是两个不同声道。
`_parse_channel_id` 兼容 canonical 与紧凑（`camera:s2c0`）两种写法，
并从右往左切分以容纳含冒号的 `source_id`（`cam:left:s1:c3`）。

`audio_position` 与 `stream_index` 继续严格区分（§14）：真实 A7M5 素材上
容器 index `1..4` ↔ 音频序号 `0..3`，测试断言不变。

### 8.3 外挂 WAV 的时间轴（§6）

`AudioTiming(start_offset_sec, duration_sec, time_base, start_time_sec,
shares_timeline, note)` 只**记录**时间轴事实：`media` 默认
`shares_timeline=True`，外挂来源默认 `False`，其余一律留 `None`（不推断）。

> ⚠️ **本阶段不实现任何跨文件同步算法**，尤其**没有**把 WAV 与
> `camera.mp4` 的时间偏移塞进 `core/channel_sync.py`。channel_sync 继续只
> 负责它原有的职责（容器内多声道之间的既有同步分析与结果记录）。

### 8.4 Selection（§7/§15）

```python
p = AudioPlanner(plan)
p.select_all()
p.select_sources("camera", "recorder")
p.select_tracks("camera:a2c0-1-2-3")
p.select_channels("camera:s2:c2", "recorder:s0:c0")   # 顺序即选择顺序
p.exclude_channels("camera:s2:c1")
```

* 每次操作都**返回新的 AudioPlan**，且 **`AudioSource` / `AudioStream` /
  `AudioChannel` 的原始身份完全不变**（`source_id`、`stream_index`、
  `audio_position`、`channel_index`、`sync`、`metadata` 全部原样保留）。
* `selected_tracks` 是 `selected_channels` 的**聚合视图**（"这条 track 有
  声道被选中"），两者不允许长期不一致。
* 未选中的声道**不会被删除**：`plan.all_channels()` 始终返回全部输入声道。

### 8.5 Channel Mapping（§8/§11/§16）

```python
p.map_channels("camera:s2:c2", "recorder:s0:c0", "camera:s2:c0")
p.map_channel("camera:s2:c1", 0)     # 位置互换语义
p.reorder("camera:s2:c2", "camera:s2:c0", "camera:s2:c3", "camera:s2:c1")
```

* 输出 index 恒为 `0..N-1`（连续、唯一），不接受任意 index 列表。
* **Selection ≠ Mapping**：选择决定"保留什么"，映射决定"按什么顺序输出"。
  `camera:s2:c0 + camera:s2:c2` 的选择结果**只是两个独立源声道**，
  绝不会自动产生任何 PCM 合成。
* **Mixing 完全禁止**：任何 "N 源声道 -> 1 输出声道" 都返回
  `audio_mix_not_supported`（详见 §8.7）。本模块**不存在**增益/求和/归一化
  代码路径 —— 拒绝是结构性的。

### 8.6 AudioOutputTrack 语义修正（§9）

`AudioOutputTrack` **不是**"一个 source channel"，而是**最终输出中的一个
逻辑音频单元**，可以承载：

| 承载 | 例 | 策略 |
|---|---|---|
| 一整条 source track | 4CH 流整流保留 | `stream_copy` |
| 一个被选中的 source channel | `camera:s2:c2` -> output 0 | `channel_filter` |
| 一个被显式映射的源声道组 | 4CH 重排后的多通道输出 | `channel_filter` |
| 多个源声道合成到 1 个输出 | `0.5*CH1 + 0.5*CH2` | `mixing` -> **拒绝** |

`AudioOutputTrack.source_ids` 给出逐声道的来源身份（`§18` 的追溯链），
`sync_of(output_index)` 给出该输出声道继承的同步结果。

### 8.7 AudioMapSpec（§12/§13）

```python
spec = build_map_spec(plan)          # 不抛异常, 失败时 executable=False + errors
spec.executable, spec.strategy, spec.full_stream_copy
spec.operations                      # 每个输出段的策略与身份
spec.trace(0)                        # output 0 -> camera:s2:c2 -> +960 samples
```

职责边界（硬要求，逐条实现）：

* **不持有 PCM**、**不执行 ffmpeg**（模块不 import subprocess，不拼 argv）；
* JSON-compatible（`to_dict()`/`from_dict()` 往返）；
* 每个输出声道都带完整 `AudioChannelRef`（source/stream/channel/audio_position/sync）；
* output index 稳定（0..N-1，validation 保证）；
* **不含任何 mixer 参数**（增益/权重/求和字段在该结构里不存在）；
* 可以 dry-run 检查（校验失败不抛异常，返回 `errors`）。

**FFmpeg mapping 的策略区分（§13）**：

| 情况 | 判定 | 策略 |
|---|---|---|
| 完整 stream 原样保留 | 输出段恰为某条流的**完整且自然顺序**全通道 | `stream_copy`（可 `-map` + `-c:a copy`） |
| 只取 stream 内部分 channel / 重排 | 其余情况 | `channel_filter`（需要声道过滤） |

注意 k 条 mono 流的任意重排**仍然是** `stream_copy`（每条 mono 流被整体
保留，只是 `-map` 顺序变了）；只有"从一条多声道流里取子集/重排"才需要
声道过滤。本阶段只生成**策略分类与身份**，不生成 filtergraph 字符串、
不实现任何采样级 DSP。

### 8.8 Validation（§17）

`validate_selection()` + `validate_mapping()` -> 稳定 reason 列表；
`validate_plan(plan)` 返回去重后的 reason 字符串；`build_map_spec(plan)`
在不抛异常的前提下给出 `errors`（含 `detail` 与 `location`）。
优先顺序：**引用有效性 -> 与 selection 一致 -> 结构 -> Mixing**，
因此不会出现"映射了未选中的声道却报混音"这类误导性 reason。

| 规则 | reason code |
|---|---|
| V1 来源存在 | `audio_source_not_found` |
| V2 流存在 | `audio_stream_not_found` |
| V3 声道存在（含 id 格式非法） | `audio_channel_not_found` |
| V4 选择集身份不重复 | `audio_source_duplicate` |
| V5 output index 连续且唯一 | `audio_mapping_output_order` |
| V6 mapping 的声道必须已被 selection 选中 | `audio_mapping_not_selected` |
| V7 duplicate mapping（同一源声道占两个输出） | `audio_mapping_output_order`（复制语义未定义 -> 拒绝） |
| V8 Mixing | `audio_mix_not_supported` |

V8 的两个触发形态：**同一 output index 被不同源声道占用**；以及
**选择集比输出集大**（N 源 -> 更少输出，等价于合成）。两种都由
`map_channels()` 与 `validate_mapping()` 一致拒绝。

### 8.9 AudioPlan 扩展（§10）

| 字段 | 状态 |
|---|---|
| `sources` | **新增**：参与本次规划的 `AudioSource` 列表 |
| `selected_channels` | **新增**：逐声道选择集（`AudioChannel.id`，保持选择顺序） |
| `channel_mapping` | **新增**：显式映射条目（`output_index` + 完整身份 + sync） |
| `mapping_kind` | **新增**：`derived`（按选择顺序派生）/ `explicit`（调用方指定顺序） |
| `output_tracks` | **新增**：输出单元描述（Phase 2 只描述，不执行） |
| `notes` | **新增**：非致命问题（validation warning 通道） |
| `input_tracks` / `selected_tracks` / `preserve_original` / `output_sample_rate` / `output_sample_format` | **保留**（Phase 1 语义不变） |
| `mix_mode` / `channel_map` / `wav_outputs` | **保留**为后续预留（本阶段只存不解释，且 `mix_mode` 非空即判 Mixing 拒绝） |

`is_default` 现在同时要求 `mapping_kind == "derived"` 且 selection 等于全部
声道 —— 默认计划因此可被生产路径稳定识别为"不进 filtergraph"。

---

## 9. Phase 2 实测

| 测试面 | 命令 | 结果 |
|---|---|---|
| L1 unit | `python tests/full_autotest.py --level unit` | 见 §10 提交时实测 |
| 音频来源/选择/映射 L1 | 套件 `audio select/map v0.7.1` | 78 断言全通过 |
| 音频来源/选择/映射 L3 | 套件 `audio select/map v0.7.1` | 15 断言全通过 |
| 真实 A7M5 4CH + 外挂 WAV | 同上（testsets + ffmpeg 生成 WAV） | 多来源计划/跨来源选择/重排/sync 追溯全通过 |

真实素材用法（L3 用例）：

```text
testsets/a7m5_4k60p_265_10bit420_150m_xavchs_4ch/*.MP4  → camera (4×mono PCM)
ffmpeg 生成 deterministic WAV: ext_mono.wav / ext_stereo.wav / ext_4ch.wav
                                                          → wav_mono / wav_stereo / wav_4ch
```

断言覆盖：4CH / 2×2CH / 4×mono / 外挂 mono·stereo·4CH WAV / 多来源混合；
跨来源选择与重排（`wav_stereo:s0:c0` + `camera:s3:c0` ->
`[1, 0]` 输入顺序）；4×mono 重排 `(4,2,1,3)`；同步状态经选择+映射后
`output 0 -> camera:s3:c0 -> +963 samples` 完整可追溯。

---

## 10. 本阶段**未**实现（明确边界）

```text
Mixing（sample-level 合成 / weighted mixing / 增益 / 求和）
WAV 导出与命名
Selective MP4 retention（音轨选择进容器）
Resampling / sample-rate conversion
Drift correction（漂移校正）
新 CLI（--audio-tracks / --audio-map / --audio-source 均未开放）
DAW 式音频编辑 / timeline editor / clip system
自动跨文件同步（外挂 WAV 与主容器的时间对齐）
```

也**未修改**：视频缩放、x265 缩放规则、`core/channel_sync.py`（算法与阈值
一字未改）、Sony/DJI 保留管线、硬件解码路径。`AudioPlan` 的 `mix_mode` /
`channel_map` / `wav_outputs` 仍为预留字段；`AudioMapSpec` 只给出策略分类
与身份，**不生成 filtergraph 字符串、不执行 ffmpeg**。

---

# Phase 3A — PCM Routing + WAV Export

> Phase 3 分两段：**3A = PCM Reader + 无混音路由 + WAV 导出**（本节），
> **3B = PCM Mixing**（见文末 §21，只在 3A 全绿后才开始）。

## 11. 新增分层

```
AudioSource / AudioStream / AudioChannel      ← Phase 1/2 模型（未改）
        ↓  Channel Sync Result（只读既有报告）
AudioPlan                                     ← Phase 2 规划（未改）
        ↓
core/audio_timeline.py   AudioTimeline        ← **唯一**时长/EOF/offset 权威
        ↓
core/audio_pcm.py        AudioPCMReader       ← ffmpeg 解码 → canonical float32
        ↓
core/audio_route.py      AudioRouter          ← 纯样本搬运（**无相加**）
        ↓
core/audio_wav.py        WavExporter          ← RIFF / PCM16·24·32 / float32
        ↓
core/audio_process.py    AudioOutputSpec      ← 声明式输出描述 + 处理图编排
```

`AudioMapSpec` 仍然只描述 `-map` 层面的策略；PCM 处理走上面这条**新的**
执行链，两者不互相替代。

## 12. AudioTimeline / RenderPolicy（时长权威）

**输出时长不是"谁先 EOF 就结束"**，而是由 `AudioPlan` 按明确规则确定。
`PCMReader` / `AudioRouter` / `WavExporter` / （Phase 3B 的）Mixer 都
**不得**自行决定 EOF 或输出长度。

| 概念 | 说明 |
|---|---|
| `AudioTimeline` | `sample_rate` / `start_sample` / `end_sample` / `frame_count` / `duration_seconds` + 逐声道 `ChannelTimeline` |
| `RenderPolicy.UNION` | **Phase 3A 唯一启用**：render window = 参与输出的源 timeline **并集** |
| `RenderPolicy.INTERSECTION` / `EXPLICIT`+`INTERSECTION` | **预留**；显式给出时会以 `audio_timeline_invalid` 拒绝，不假装支持 |
| `DurationMode` | `derived`（并集推导）/ `explicit`（调用方给出）/ `metadata` |
| `SyncApplication` | `APPLY`（输入是未修正源，需应用 offset）/ `NONE`（输入已是修正产物，防止二次移位） |

解析顺序：显式 duration → `AudioSource`/`AudioStream` 的 duration 事实 →
按 policy 从参与源推导 → 仍未知则 `audio_duration_unknown` **拒绝**。
**不**用 `file_size / bit_rate` 猜 duration。

### 12.1 EOF / 短 source 策略（确定性）

```text
camera   0..60min
recorder 0..58min
UNION → render 0..60min
recorder: 58min..60min = float32 静音 0.0（继续输出, 不终止整个 render）
camera  : 不因 recorder EOF 而截断
```

* EOF = 缺失区间补 **`float32 0.0`**（不是 NaN，**不**循环，**不**复制最后一个样本）；
* 输出样本数**严格**等于 `frame_count`（sample-accurate）；
* `chunk_frames` 只影响分块，**不得**影响结果（回归已钉 `byte-identical`）；
* 多源 timeline **overlap 允许**，但不会因此自动 mixing（输出声道仍各自独立）。

### 12.2 采样率与 offset 方向

* 参与同一 render 的来源必须**同采样率**，否则 `audio_sample_rate_mismatch`
  拒绝 —— **不偷偷 resample**（resampling 属后续阶段）；
* `offset_samples` 的语义与方向由**实测**确定，不按字段名猜：

  ```text
  4×mono 素材 CH2 内容晚到 960 样本
  channel_sync 报告: delay_samples=+960  shift_samples=960
  修正后音频 vs 修正前互相关 lag = -960   (after[n] == before[n + 960])
  ⇒ 统一换算:  timeline_sample = source_sample - offset_samples
  ```

  `core/audio_timeline.source_to_timeline()` 是**唯一**换算入口；只有
  `status == success` 的固定整数 offset 会被应用，浮点分数部分只记录为
  residual（**不做**分数插值）。**不重新估计 delay**（不重写 GCC-PHAT）。

## 13. PCM Reader（canonical float32）

`core/audio_pcm.AudioPCMReader`：

* 只消费既有 `AudioStream` 模型，**不新建 ffprobe parser**；压缩音频（AAC/Opus…）
  一律交给 ffmpeg 解码；
* 解码落 raw 临时文件后按块读取（与 `channel_sync` 同策略，避免 Windows 大流量管道），
  峰值内存 ≈ 一个 chunk，与素材时长无关；
* **canonical 内部格式 = float32**，线性 PCM 归一化到 `[-1, 1]`：
  s16 ×2⁻¹⁵ / s24 ×2⁻²³ / s32 ×2⁻³¹ / f32 原样；`sample_rate` 与声道数仍由
  source model 提供，**不猜测**；
* ⚠️ 解码命令行带 `-af channelmap=0|1|…|N-1`：ffmpeg 默认会按**声明布局**
  重排/下混（实测 4 声道 + `quad` 布局会把 FC/BC 折进 BL/BR），
  identity channelmap 强制"按位置逐声道复制"，**不插值、不混合、不改增益**；
* `actual_samples`（实际解码样本数）是 **EOF 最终事实**；与声明值
  （`nb_frames` / WAV header / `duration × rate`）不一致时记录
  `audio_duration_metadata_mismatch`，并让**派生**窗口按实际重算
  （显式 duration 不受影响，缺口补静音）。

## 14. Channel Routing

`core/audio_route.AudioRouter`：**一个输出声道 = 一个源声道**，纯样本搬运。

```text
camera:s0:c2 -> out0
recorder:s0:c0 -> out1
camera:s0:c0 -> out2
```

* 同时读取多个 source，按统一 timeline 产出固定数量输出帧；
* 与块划分无关：每块只读它需要的那段源区间（按流合并区间），**不重复 decode**；
* **不存在**任何样本级相加路径；源声道重复占用同一输出/多输出在到达 router 前
  就被拒绝（`audio_mapping_output_order`；Phase 2 契约未放宽）。

## 15. WAV 导出

`core/audio_wav.WavExporter` + `read_wav`（round-trip 校验用）：

| 格式 | 实现 |
|---|---|
| `pcm16` / `pcm24` / `pcm32` | 整数，`rint(x × 2^(bits-1))` 后裁剪 |
| `float32` | IEEE float 原样（**允许 over-range**，不自动 normalize） |

* 头部**按实际写出的字节数回填**（`RIFF` 大小 / `fmt` / `data` 大小 /
  `byte_rate` / `block_align` / `bits_per_sample`）；写入样本数与声明不符
  → `audio_wav_write_failed` 且**删除半成品**；
* 3 声道及以上写 `WAVE_FORMAT_EXTENSIBLE`（含 channel mask），1/2 声道写经典
  头（兼容性最佳）；24-bit / IEEE float 标准库 `wave` 表达不了，因此自带小型
  writer（纯标准库 + numpy），**不引入任何音频框架**；
* 命名集中在 `AudioOutputSpec` / `default_wav_name()`：
  `<source>_<track/channel>_<mapping>.wav`（如 `camera_s2c2.wav`、
  `multi_mix.wav`），同名冲突自动 `-2` 后缀，**不覆盖**；
* **WAV 与 Mixing 解耦**：exporter 只负责写文件，不实现任何 DSP。

## 16. 处理图与输出描述

`core/audio_process.py`：

* `AudioOutputSpec`：`output kind / sample rate / sample format / channel count /
  source plan / destination`（Phase 4 的 MP4 集成预留）；
* `run_audio_render(plan, ffmpeg=…, work_dir=…, output_dir|output_path=…,
  sample_format=…, chunk_frames=…, render_policy=…, …)`：
  预解码 → timeline → 路由 → 写 WAV，返回 `AudioRenderResult`（JSON 友好）；
* 失败时 `ok=False` 且**不产出文件**（校验/时间轴问题绝不"尽可能输出"）；
* `reader_factory` 可替换读取后端（测试用管道读取器验证结果不依赖传输层）。

## 17. 新增 reason codes（稳定契约）

```text
audio_sample_rate_mismatch          audio_timeline_invalid
audio_negative_render_duration      audio_sync_offset_invalid
audio_duration_unknown              audio_duration_metadata_mismatch
audio_duration_explicit_invalid     audio_decode_failed
audio_pcm_format_unsupported        audio_pcm_short_read
audio_output_invalid                audio_route_offset_unknown
audio_wav_write_failed              audio_mix_invalid
```

（Phase 2 的 `audio_*_not_found` / `audio_mapping_*` / `audio_mix_not_supported`
语义不变。）

## 18. Phase 3A 实测

| 测试面 | 套件 | 结果 |
|---|---|---|
| 时间轴 / RenderPolicy / EOF（T1–T8 + 采样率 + duration） | `audio timeline v0.7.1` | 15 断言全通过 |
| offset 方向（impulse 正/负/零 + `shift_stream` 实测钉向） | `audio sync offset v0.7.1` | 7 断言全通过 |
| 通道路由（T2–T10 + T3b + T12） | `audio route v0.7.1` | 13 断言全通过 |
| WAV 导出（4 格式往返 + header 精确 + 命名） | `audio wav export v0.7.1` | 9 断言全通过 |
| chunk invariance + 内存上界 | `audio chunk invariance v0.7.1` | 9 断言全通过 |
| 真实 A7M5 4×mono + 外挂 WAV 端到端 | `audio pcm/wav v0.7.1`（L3） | 12 断言全通过 |
| 全量 L1 | `--level unit` | **364 PASS / 0 FAIL** |
| 全量 L3 | `--level full` | **477 PASS / 0 FAIL**（unit 364 + toolchain 16 + full 97） |

确定性验证手段：impulse fixture（逐样本位置断言）、`byte-identical` 哈希比对
（chunk 1/7/256/1024/4096）、`read_wav` round-trip（sample rate / 声道 /
位深 / 样本数 / 样本值 / 时长）、与 ffmpeg 直读 PCM 的逐样本一致性。

真实素材：

```text
testsets/a7m5_4k60p_265_10bit420_150m_xavchs_4ch/*.MP4
    4 条独立 mono 流（容器 index 1..4 / audio_position 0..3, pcm_s24be）
    ⚠️ 实测该素材音频**全静音**（4 流全 0）—— 用例因此断言结构一致
       （逐样本等于 ffmpeg 直读、声道不串位）而不是"有非零内容"
ffmpeg 生成的 deterministic 外挂 WAV: ext_mono / ext_stereo / ext_4ch
外加 s16/s24/s32/f32 正弦素材 -> canonical float32 逐样本一致
```

## 19. 默认生产路径回归

* `AudioPlan = None` 依然是 `-map 0` + `-c:a copy`（Sony/DJI 仍是 GPAC 音频复制）；
* 新链路**只有显式调用** `run_audio_render()` 才会解码/写 WAV；
  CLI 未新增任何音频开关；
* `core/channel_sync.py`、`encoders/hwdecode.py`、`encoders/integrity.py`、
  视频缩放与 x265 缩放规则：**零改动**（`git status` 仅新增音频模块 +
  测试文件）。

## 20. Phase 3A **未**实现（明确边界）

```text
PCM Mixing（N 源声道 -> 1 输出声道的样本级合成 / 增益）
Selective MP4 retention（音轨选择进容器）
Audio codec integration（aac/opus/… 编码与 mux）
Automatic external-source synchronization（自动跨文件时间对齐）
Drift correction / time-stretch / pitch shift
Resampling（不同采样率一律拒绝）
compressor / EQ / reverb / limiter / normalizer / noise reduction / AGC /
spectral processing
新 CLI（--audio-* 全部未开放）
DAW 式编辑 / timeline editor / clip system
```

`AudioPlan.mix_mode` 仍为预留：`build_audio_map_spec()` 继续以
`audio_mix_not_supported` 拒绝（`-map` 规格无法表达样本级合成）。

---

# Phase 3B — PCM Mixing（N→1 + gain + peak/clipping）

## 21. 新增分层

```
AudioTimeline (P3A, 时长/EOF/offset 权威 —— 未改)
        ↓
core/audio_mix.py
    MixBus  ──► MixSink ──► MixGain (source channel + 线性 gain)
        ↓
    AudioMixer:  sum = Σ (sample × gain)   ← float32 累加
        ↓
core/audio_wav.WavExporter   ← **不实现** mixing, 只写文件
```

`core/audio_process.run_audio_render()` 在同一张图上只换一个节点：

| 计划状态 | 图 | 节点 |
|---|---|---|
| 无 `mix_buses` 且 `mix_mode is None` | **routing** | `AudioRouter`（P3A，1 输出声道 = 1 源声道） |
| `plan.mix_buses` 非空 / `mix_mode` 非空 | **mixing** | `AudioMixer`（P3B，Σ sample × gain） |

## 22. Mix API

```python
from core.audio_mix import MixBusBuilder, MixGain, MixSink, ClipPolicy

bus = MixBusBuilder(plan).sum_all(              # N -> 1
    ["camera:s0:c0", "recorder:s0:c0"],
    gains={"camera:s0:c0": 0.5, "recorder:s0:c0": 0.5},
)
// 多输出单元 (每个输出一个 sink)
bus.sinks = [
    MixSink(output_index=0, channel_id="mix0",
            inputs=[MixGain("cam:s0:c0", 0.5), MixGain("rec:s0:c0", 0.5)]),
    MixSink(output_index=1, channel_id="mix1",
            inputs=[MixGain("cam:s0:c1", 1.0)]),
]
res = run_audio_render(plan, ffmpeg=..., work_dir=..., output_path=...,
                       mix_bus=bus, clip_policy=ClipPolicy.DETECT)
res.mix_bus.trace(0)          # output 0 -> [camera:s0:c0 ×0.5, recorder:s0:c0 ×0.5]
res.mix_stats.to_dict()       # peak / clip_count / peak_inputs / …
```

* `source selection → mapping → mix bus` 链条清晰：**增益只存在于 `MixBus`
  的输入项**，源声道身份与 `sync` 完全不动（`MixGain.channel_id` 就是
  `AudioChannel.id`）；
* `plan.mix_buses`（**新增模型字段**，JSON-compatible）可承载显式混音定义；
  `plan.mix_mode` 非空且无显式 bus 时回退为"全部选中声道求和成一个输出"；
* `AudioPlan.is_default` 现在也要求 `mix_buses` 为空。

## 23. 数值处理

* **float32 累加**：`sum = Σ (sample × gain)`；`_mix_block` 逐块独立，无跨块状态
  → 结果与 `chunk_frames` 无关（回归钉 `byte-identical`）；
* 检波统计（`MixStats`）：`peak` / `peak_frame` / `peak_channel` /
  `peak_inputs`（**峰值可分解到逐输入贡献**，Σ contribution = peak）/
  `clip_count` / `clip_count_by_channel` / `per_output_peak` /
  `format_clip_count`（超出整数输出满量程的样本数）;
* **不自动 normalize**：`1.0 + 1.0 = 2.0` 会被**保留**并计为 clipping 事件；
  非峰值样本不会被整体缩放（回归断言"仅 1 个非零样本"）。

| `ClipPolicy` | 行为 |
|---|---|
| `DETECT`（默认） | 只统计，数据原样保留（float32 输出因此可 over-range） |
| `HARD_CLIP` | 显式裁剪到 `[-1, 1]`，计 `hard_clipped` |
| `ERROR` | 出现 `|sum| > 1` 即抛 `audio_mix_clipping`，**不产出文件** |

**没有** `AUTO_NORMALIZE`：那会让数据行为不可预测。

## 24. Gain

线性增益（`db_to_linear()` / `linear_to_db()`）：`0 dB = 1.0`、
`−6 dB ≈ 0.5012`、`+6 dB ≈ 1.9953`。**不做** loudness normalization / LUFS /
compressor / limiter / AGC。增益必须有限且非负（负增益 → `audio_mix_invalid`）。

## 25. EOF / offset 在混音中的一致性

* 混音**不重新定义** render duration / EOF：render window 仍来自
  `AudioTimeline`（UNION）；某个输入先 EOF 时, 该项按 §EOF 策略贡献
  **静音 0.0**，输出继续（回归: 1000 帧长的 + 400 帧短的混音输出仍是
  1000 帧, 短的后 600 帧等同静音）；
* offset 在混音路径上**同样**只经 `source_to_timeline()` 换算：
  晚到 60 样本的轨被前移 60 后与另一轨在同一 timeline 位置求和
  （回归: impulse 落点 timeline 1000, 幅度 `0.5 + 0.5 = 1.0`）；
* 混音输出声道的 `ChannelTimeline` 身份是 `mix{N}`，span 取各输入区间的
  **交集**（`mix_intersection`），逐输入区间保留在 warnings 里便于追溯。

## 26. Phase 3B 实测

| 测试面 | 套件 | 结果 |
|---|---|---|
| gain 标度 / N→1 / 相消 / overflow / policy / 多 bus / 短 source / offset / 校验 / JSON | `audio mix v0.7.1` | 19 断言全通过 |
| 混音 chunk invariance + 图等价 + 传输层无关 | `audio mix invariance v0.7.1` | 4 断言全通过 |
| 真实 A7M5 + 外挂 4CH WAV 多来源混音 | `audio mix v0.7.1`（L3） | 6 断言全通过 |
| 全量 L1 | `--level unit` | **388 PASS / 0 FAIL** |
| 全量 L3 | `--level full` | **507 PASS / 0 FAIL**（unit 388 + toolchain 16 + full 103） |

确定性断言要点:

```text
1.0×0.5 + 1.0×0.5   = 1.0        (sample-exact)
1.0×0.5 + (−1.0)×0.5 = 0.0        (相位相消, 精确 0)
1.0 + 1.0            = 2.0        (detect 保留 + clip_count=1, 无自动归一化)
1.0 + 1.0 (hard_clip)= 1.0        (hard_clipped=1)
1.0 + 1.0 (error)    → audio_mix_clipping, 不产出文件
0.5×(cam) + 0.5×(rec) = 0.375     (两路独立 bus)
混音图 1:1 == 路由图             (逐样本一致, 含重排)
混音 chunk 7/256/1024/4096       (byte-identical)
ffmpeg 管道读 == span 文件读      (byte-identical)
```

## 27. Phase 3B **未**实现（明确边界）

```text
Selective MP4 retention（音轨选择进容器）
Audio codec integration（aac/opus/… 编码与 mux）
Automatic external-source synchronization / 漂移校正
Resampling（采样率不一致仍直接拒绝）
loudness normalization / LUFS / AGC / limiter / compressor / EQ /
reverb / noise reduction / spectral processing / time-stretch / pitch shift
新 CLI（--audio-* 全部未开放）
多 bus 同时渲染（当前一次渲染只消费第一个 MixBus）
```

默认生产路径**仍然**不受影响：`AudioPlan = None` → `-map 0` + `-c:a copy`；
混音只在显式传入 `mix_bus` / 设置 `plan.mix_buses` 时才会执行。

## 28. Phase 3B 之后仍未完成的总体清单

```text
WAV 导出 ✅（P3A）
PCM Routing ✅（P3A）
PCM Mixing ✅（P3B）
Selective MP4 retention ❌
音频编码 + mux 进 MP4 ❌
自动跨文件时间轴对齐 ❌
漂移校正 ❌
```


