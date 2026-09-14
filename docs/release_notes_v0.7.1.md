# v0.7.1 发布说明 — 音频轨道模型（Phase 1）

> **状态**：Phase 1 完成。**默认音频路径与输出行为未改变** —— 本版本是
> 纯内部能力层，不新增 CLI、不改变 MP4 音频处理方式。
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

## 1. 这次做了什么

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
**内部能力**，Phase 2 才会逐步接入输出决策。

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

## 7. 本阶段**未**实现（Phase 2 及以后）

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
