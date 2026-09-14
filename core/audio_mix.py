"""PCM Mixing (v0.7.1 Phase 3B) — N 源声道 -> 1 输出声道。

    输入 (canonical float32, 来自 AudioPCMReader)
            ↓  gain (线性)
    sum = Σ (sample × gain)          ← **float32 累加**
            ↓
    one output channel

分层与边界 (硬要求):

* **不重新定义 EOF**: render duration / 短 source 补静音策略仍然只由
  `core/audio_timeline.AudioTimeline` 决定 (P3B 只消费它);
* **不碰 WAV exporter**: mixing 与导出解耦 —— `Routing only -> WAV` 与
  `Mixing -> WAV` 是同一张图上换节点 (`core.audio_process.run_audio_render`);
* **不把 mixer 参数塞回 `AudioChannel`**: 增益只存在于 `MixBus` 的输入项,
  源声道身份与 sync 完全不动;
* **不是 `-map` 层面能表达的东西**: `core/audio_plan.build_audio_map_spec()`
  继续以 `audio_mix_not_supported` 拒绝 `mix_mode` (那条路只描述 ffmpeg
  argv); 本模块是 PCM 层的实现。

数值约定:

* 内部一律 **float32** 累加, 增益为**线性**系数 (0 dB = 1.0,
  −6 dB ≈ 0.5012, +6 dB ≈ 1.9953);
* **不自动 normalize / 不做 loudness / 不做 AGC / 不做 limiter / compressor**;
* 超过满量程一律**如实记录**并按明确的 `ClipPolicy` 处理:

  ============  ==================================================
  取值           行为
  ============  ==================================================
  DETECT        只统计 (**默认**): 数据原样保留 (float32 输出因此可 over-range)
  HARD_CLIP     显式硬裁剪到 [-1, 1]
  ERROR         出现 > 1.0 的样本即抛 `audio_mix_clipping`
  ============  ==================================================

  没有 "AUTO_NORMALIZE" —— 那会让数据行为不可预测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from .audio_models import AudioPlan
from .audio_pcm import CANONICAL_PCM_DTYPE
from .audio_timeline import (
    AudioRenderError,
    AudioTimeline,
    ChannelTimeline,
    resolve_timeline,
)
from .audio_wav import WavFormat

__all__ = [
    "AudioMixer",
    "ClipPolicy",
    "MixBus",
    "MixBusBuilder",
    "MixGain",
    "MixSink",
    "MixStats",
    "REASON_AUDIO_MIX_CLIPPING",
    "REASON_AUDIO_MIX_INVALID",
    "build_mix_buses",
    "db_to_linear",
    "linear_to_db",
    "mix_buses_of",
    "mix_channel_timeline",
    "mixing_timeline",
    "resolve_mix_timeline",
    "validate_mix_bus",
]

REASON_AUDIO_MIX_CLIPPING = "audio_mix_clipping"
REASON_AUDIO_MIX_INVALID = "audio_mix_invalid"


def db_to_linear(db: float) -> float:
    """分贝 -> 线性增益 (0 dB = 1.0)。**不做**任何响度标准化。"""
    return float(10.0 ** (float(db) / 20.0))


def linear_to_db(gain: float) -> float:
    """线性增益 -> 分贝 (仅用于报告; gain <= 0 返回 -inf)。"""
    value = float(gain)
    if value <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(value)


class ClipPolicy(str, Enum):
    """超范围样本的处理策略 (三选一, **没有**"自动归一化")。"""

    DETECT = "detect"          # 只统计, 数据原样保留 (默认)
    HARD_CLIP = "hard_clip"    # 显式裁剪到 [-1, 1]
    ERROR = "error"            # 出现 > 1.0 即抛 audio_mix_clipping

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> Any:
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return default
        text = str(value).strip().lower().replace("-", "_")
        table = {
            "detect": cls.DETECT, "none": cls.DETECT, "keep": cls.DETECT,
            "hard_clip": cls.HARD_CLIP, "clip": cls.HARD_CLIP,
            "error": cls.ERROR, "raise": cls.ERROR,
        }
        return table.get(text, default)


@dataclass(frozen=True)
class MixGain:
    """一条**输入**: 源声道 + 线性增益。

    `channel_id` 与 `AudioChannel.id` 完全一致 (`camera:s0:c2`), 因此输出
    声道的追溯链完整: output -> MixBus -> camera:s0:c2 -> sync。
    """

    channel_id: str
    gain: float = 1.0

    @property
    def gain_db(self) -> float:
        return linear_to_db(self.gain)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "channel_id": self.channel_id,
            "gain": float(self.gain),
        }
        if self.gain > 0:
            data["gain_db"] = round(self.gain_db, 4)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MixGain":
        raw = data.get("gain")
        if raw is None and data.get("gain_db") is not None:
            raw = db_to_linear(float(data["gain_db"]))
        return cls(
            channel_id=str(data.get("channel_id") or ""),
            gain=float(1.0 if raw is None else raw),
        )


@dataclass
class MixSink:
    """**一个输出声道**的混音定义 (N 个输入 -> 1 个输出)。

        Output 0
            input camera:s0:c0   gain=0.5
            input recorder:s0:c0 gain=0.5
    """

    output_index: int
    inputs: list[MixGain] = field(default_factory=list)
    channel_id: str = ""
    note: str | None = None

    @property
    def input_count(self) -> int:
        return len(self.inputs)

    @property
    def is_mixing(self) -> bool:
        return len(self.inputs) > 1

    @property
    def unity_gain(self) -> bool:
        return all(g.gain == 1.0 for g in self.inputs)

    @property
    def gain_sum(self) -> float:
        return float(sum(g.gain for g in self.inputs))

    def source_ids(self) -> list[str]:
        out: list[str] = []
        for item in self.inputs:
            sid = item.channel_id.split(":", 1)[0]
            if sid not in out:
                out.append(sid)
        return out

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "output_index": int(self.output_index),
            "inputs": [g.to_dict() for g in self.inputs],
        }
        if self.channel_id:
            data["channel_id"] = self.channel_id
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MixSink":
        return cls(
            output_index=int(data.get("output_index") or 0),
            inputs=[
                MixGain.from_dict(g)
                for g in (data.get("inputs") or [])
                if isinstance(g, Mapping)
            ],
            channel_id=str(data.get("channel_id") or ""),
            note=(str(data["note"]) if data.get("note") is not None else None),
        )


@dataclass
class MixBus:
    """一次混音渲染的完整定义 (JSON-compatible, 无 PCM、无可执行参数)。

    `mix_mode` 保留 Phase 2 的语义标签 (`"sum"` 等) 只作说明; 真正的输入与
    增益在 `sinks` 里。**没有** mixer 参数会被写回源声道模型。
    """

    sinks: list[MixSink] = field(default_factory=list)
    mix_mode: str = "sum"
    sample_rate: int = 0
    policy: ClipPolicy = ClipPolicy.DETECT
    notes: list[str] = field(default_factory=list)

    @property
    def output_channels(self) -> int:
        return len(self.sinks)

    @property
    def is_mixing(self) -> bool:
        return any(s.is_mixing for s in self.sinks)

    @property
    def input_count(self) -> int:
        return sum(s.input_count for s in self.sinks)

    def sink(self, output_index: int) -> MixSink | None:
        if 0 <= output_index < len(self.sinks):
            return self.sinks[output_index]
        return None

    def channel_ids(self) -> list[str]:
        return [g.channel_id for s in self.sinks for g in s.inputs]

    def trace(self, output_index: int) -> dict[str, Any] | None:
        """输出声道 -> 逐输入 (源声道 + 增益) 的完整追溯。"""
        sink = self.sink(output_index)
        if sink is None:
            return None
        return {
            "output_index": int(output_index),
            "mix_mode": self.mix_mode,
            "is_mixing": bool(sink.is_mixing),
            "inputs": [
                {"channel_id": g.channel_id, "gain": float(g.gain),
                 "gain_db": (round(g.gain_db, 4) if g.gain > 0 else None)}
                for g in sink.inputs
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mix_mode": self.mix_mode,
            "sinks": [s.to_dict() for s in self.sinks],
        }
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        if self.policy is not ClipPolicy.DETECT:
            data["policy"] = self.policy.value
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MixBus":
        return cls(
            sinks=[
                MixSink.from_dict(s)
                for s in (data.get("sinks") or [])
                if isinstance(s, Mapping)
            ],
            mix_mode=str(data.get("mix_mode") or "sum"),
            sample_rate=int(data.get("sample_rate") or 0),
            policy=ClipPolicy.coerce(
                data.get("policy"), ClipPolicy.DETECT
            ) or ClipPolicy.DETECT,
            notes=[str(n) for n in (data.get("notes") or [])],
        )


class MixBusBuilder:
    """在 `AudioPlan` 上构造混音定义 (**不修改源声道模型**)。"""

    def __init__(self, plan: AudioPlan) -> None:
        self.plan = plan

    def bus(self, mix_mode: str = "sum") -> MixBus:
        return MixBus(mix_mode=mix_mode)

    def sum_all(
        self,
        channel_ids: Sequence[str],
        *,
        output_index: int = 0,
        gains: Mapping[str, float] | Sequence[float] | None = None,
        mix_mode: str = "sum",
    ) -> MixBus:
        """把给定源声道全部求和进**一个**输出声道。

        `gains` 未给时每路 1.0 (**不做** 1/N 自动衰减 —— 要防削顶请显式给
        增益或选择明确的 `ClipPolicy`)。
        """
        ids = [str(c) for c in channel_ids]
        if isinstance(gains, Mapping):
            resolved = [float(gains.get(cid, 1.0)) for cid in ids]
        elif gains is not None:
            values = [float(v) for v in gains]
            if len(values) != len(ids):
                raise AudioRenderError(
                    REASON_AUDIO_MIX_INVALID,
                    f"gains length {len(values)} != inputs {len(ids)}",
                )
            resolved = values
        else:
            resolved = [1.0] * len(ids)
        sink = MixSink(
            output_index=int(output_index),
            inputs=[
                MixGain(channel_id=cid, gain=g)
                for cid, g in zip(ids, resolved)
            ],
            channel_id=f"mix{int(output_index)}",
        )
        return MixBus(sinks=[sink], mix_mode=mix_mode)

    def one_to_one(
        self, channel_ids: Sequence[str], *, mix_mode: str = "sum",
    ) -> MixBus:
        """每个源声道各自一个输出声道 (单输入 sink, gain=1.0)。

        不是"多路求和", 但复用同一套 MixEngine —— 用来验证
        `Mixing 图` 与 `Routing 图` 在相同输入下逐样本一致。
        """
        bus = MixBus(mix_mode=mix_mode)
        for index, cid in enumerate(channel_ids):
            bus.sinks.append(MixSink(
                output_index=index,
                inputs=[MixGain(channel_id=str(cid), gain=1.0)],
            ))
        return bus


def mix_buses_of(plan: AudioPlan) -> list[MixBus]:
    """`AudioPlan.mix_buses` -> MixBus 列表 (JSON dict 也接受)。"""
    out: list[MixBus] = []
    for raw in getattr(plan, "mix_buses", None) or []:
        if isinstance(raw, MixBus):
            out.append(raw)
        elif isinstance(raw, MixSink):
            out.append(MixBus(sinks=[raw]))
        elif isinstance(raw, Mapping):
            if "sinks" in raw:
                out.append(MixBus.from_dict(raw))
            else:
                out.append(MixBus(sinks=[MixSink.from_dict(raw)]))
    return out


def validate_mix_bus(plan: AudioPlan, bus: MixBus) -> list[dict[str, Any]]:
    """混音规格校验 (稳定 reason code; 空 = 通过)。

    检查: 输出 index 连续唯一 / 输入非空 / 输入声道存在 / 增益有限且非负。
    """
    issues: list[dict[str, Any]] = []
    known = {c.id for c in plan.all_channels()}
    indices = [int(s.output_index) for s in bus.sinks]
    if sorted(indices) != list(range(len(bus.sinks))):
        issues.append({
            "reason": REASON_AUDIO_MIX_INVALID,
            "detail": (
                f"mix output indices must be contiguous "
                f"0..{len(bus.sinks) - 1}, got {sorted(indices)}"
            ),
        })
    for sink in bus.sinks:
        if not sink.inputs:
            issues.append({
                "reason": REASON_AUDIO_MIX_INVALID,
                "detail": f"mix sink {sink.output_index} has no inputs",
            })
        for item in sink.inputs:
            if item.channel_id not in known:
                issues.append({
                    "reason": "audio_channel_not_found",
                    "detail": (
                        f"mix sink {sink.output_index} refers to unknown "
                        f"channel {item.channel_id!r}"
                    ),
                    "location": item.channel_id,
                })
            gain = float(item.gain)
            if gain != gain or gain in (float("inf"), float("-inf")) \
                    or gain < 0:
                issues.append({
                    "reason": REASON_AUDIO_MIX_INVALID,
                    "detail": (
                        f"mix gain for {item.channel_id!r} must be a finite "
                        f"non-negative number, got {item.gain!r}"
                    ),
                    "location": item.channel_id,
                })
    return issues


def mix_channel_timeline(
    timeline: AudioTimeline, sink: MixSink,
) -> ChannelTimeline:
    """输出声道在统一 timeline 上的位置 = 各输入 timeline 的**交集**。

    混音需要每一项都有数据才能真正相加, 因此混音输出声道的 span 取输入
    区间的交集。⚠️ 这**不改变** render window: `start_sample` / `end_sample`
    仍由 `AudioTimeline` (UNION 并集) 决定, Mixer 不重新定义 EOF; 交集之外
    的输出样本由 timeline 的静音策略负责 (短 source -> 0.0)。

    返回的 `ChannelTimeline` 身份是 `mix{output_index}` (**不是**任何源声道),
    逐输入的区间信息保留在 `warnings` 里便于追溯。
    """
    parts = [timeline.channel(item.channel_id) for item in sink.inputs]
    parts = [p for p in parts if p is not None]
    if not parts:
        raise AudioRenderError(
            REASON_AUDIO_MIX_INVALID,
            f"mix sink {sink.output_index} has no resolvable input channels",
        )
    starts = [p.timeline_start for p in parts if p.timeline_start is not None]
    ends = [p.timeline_end for p in parts if p.timeline_end is not None]
    start = max(starts) if starts else None
    end = min(ends) if ends else None
    mixed = ChannelTimeline(
        channel_id=sink.channel_id or f"mix{int(sink.output_index)}",
        source_id="mix",
        stream_index=-1,
        channel_index=int(sink.output_index),
        audio_position=None,
        sample_rate=int(timeline.sample_rate),
        offset_samples=0,
        raw_offset_samples=None,
        sync_status="mixed",
        sync_application="none",
        declared_samples=(
            None if (start is None or end is None)
            else max(0, int(end) - int(start))
        ),
        actual_samples=None,
        availability_source="mix_intersection",
    )
    mixed.warnings.append(
        "mix_intersection: "
        + ", ".join(
            f"{p.channel_id}[{p.timeline_start},{p.timeline_end})"
            for p in parts
        )
    )
    return mixed


def mixing_timeline(
    plan: AudioPlan, bus: MixBus, *, base: AudioTimeline,
) -> AudioTimeline:
    """在既有 timeline 上换掉输出声道: 逐 sink 生成 mix 输出声道。

    render window、采样率与 render policy **原样保留** —— 时长权威仍然
    只有 `AudioTimeline`。
    """
    channels = [mix_channel_timeline(base, sink) for sink in bus.sinks]
    return AudioTimeline(
        sample_rate=int(base.sample_rate),
        start_sample=int(base.start_sample),
        end_sample=int(base.end_sample),
        duration_mode=base.duration_mode,
        render_policy=base.render_policy,
        explicit=base.explicit,
        channels=channels,
        output_channel_ids=[c.channel_id for c in channels],
        notes=list(base.notes) + [
            f"mixing: {len(channels)} output channel(s) ({bus.mix_mode}) "
            f"from {bus.input_count} input(s)"
        ],
        warnings=list(base.warnings),
    )


def resolve_mix_timeline(
    plan: AudioPlan,
    reader: Any,
    bus: MixBus,
    *,
    base: AudioTimeline | None = None,
    **timeline_kwargs: Any,
) -> AudioTimeline:
    """先按 Phase 3A 规则解析 timeline, 再换成混音输出声道。"""
    if base is None:
        base = resolve_timeline(
            plan, availability=reader.availability(), **timeline_kwargs
        )
        base.refresh_from_reader(reader, plan=plan)
    return mixing_timeline(plan, bus, base=base)


# ---------------------------------------------------------------------------
# MixEngine
# ---------------------------------------------------------------------------


@dataclass
class MixStats:
    """一次混音渲染的检波事实 (deterministic, 可断言)。"""

    outputs: int = 0
    frames: int = 0
    inputs: int = 0
    peak: float = 0.0                     # max |sum| (归一化/裁剪之前)
    peak_channel: int | None = None       # 峰值所在输出声道
    peak_frame: int | None = None         # 峰值所在 timeline 样本
    peak_inputs: list[dict[str, Any]] = field(default_factory=list)
    clip_count: int = 0                   # |sum| > 1.0 的样本数
    clip_count_by_channel: list[int] = field(default_factory=list)
    clip_limit: float = 1.0
    format_clip_count: int = 0            # 超出输出格式满量程的样本数
    format_clip_limit: float = 1.0
    hard_clipped: int = 0                 # 实际被硬裁剪改写的样本数
    policy: str = ClipPolicy.DETECT.value
    per_output_peak: list[float] = field(default_factory=list)

    @property
    def clipped(self) -> bool:
        return self.clip_count > 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "outputs": int(self.outputs),
            "frames": int(self.frames),
            "inputs": int(self.inputs),
            "peak": float(self.peak),
            "clip_count": int(self.clip_count),
            "clip_count_by_channel": list(self.clip_count_by_channel),
            "clip_limit": float(self.clip_limit),
            "format_clip_count": int(self.format_clip_count),
            "format_clip_limit": float(self.format_clip_limit),
            "hard_clipped": int(self.hard_clipped),
            "policy": self.policy,
            "per_output_peak": [float(p) for p in self.per_output_peak],
        }
        if self.peak_channel is not None:
            data["peak_channel"] = int(self.peak_channel)
        if self.peak_frame is not None:
            data["peak_frame"] = int(self.peak_frame)
        if self.peak_inputs:
            data["peak_inputs"] = [dict(x) for x in self.peak_inputs]
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "outputs": int(self.outputs),
            "frames": int(self.frames),
            "peak": round(float(self.peak), 6),
            "clip_count": int(self.clip_count),
            "policy": self.policy,
        }


class AudioMixer:
    """按 `MixBus` 逐块渲染: 每个输出声道 = Σ(输入 × gain)。

    与 `AudioRouter` **同接口** (`frames(chunk_frames=…)` 产出
    `(block, faithful)`), 因此 `core.audio_process.run_audio_render` 可以在
    同一张图上换节点 —— WAV 导出完全不需要知道自己在写路由结果还是混音结果。

    reader 需提供 `read_frames(stream_id, channel_index=…, start=…, count=…)`
    (**以请求区间自身为原点**、越界补静音的读取), 与
    `core.audio_pcm.AudioPCMReader` / 测试用管道读取器一致。

    结果与 chunk 划分无关: 每块只依赖 (起点, 长度) 与逐输入的一次读取,
    不保留跨块样本状态。
    """

    def __init__(
        self,
        reader: Any,
        bus: MixBus,
        timeline: AudioTimeline,
        *,
        source_timeline: AudioTimeline | None = None,
        clip_policy: ClipPolicy | str | None = None,
        output_format: WavFormat | str | None = None,
    ) -> None:
        self.reader = reader
        self.bus = bus
        self.timeline = timeline
        # 混音 timeline 的声道身份是 `mixN` (输出单元), **输入**的身份仍在
        # 未混音的 base timeline 上 —— 逐输入几何一律查这张表。
        self.source_timeline = source_timeline or timeline
        self.policy = (
            clip_policy if isinstance(clip_policy, ClipPolicy)
            else ClipPolicy.coerce(
                clip_policy if clip_policy is not None else bus.policy,
                ClipPolicy.DETECT,
            )
        ) or ClipPolicy.DETECT
        self.output_format = (
            WavFormat.coerce(output_format) if output_format else None
        )
        limit = (
            self.output_format.clip_limit() if self.output_format is not None
            else 1.0
        )
        self.stats = MixStats(
            outputs=len(bus.sinks),
            inputs=bus.input_count,
            policy=self.policy.value,
            clip_count_by_channel=[0] * len(bus.sinks),
            per_output_peak=[0.0] * len(bus.sinks),
            clip_limit=1.0,
            format_clip_limit=limit,
        )
        self._ids = [
            s.channel_id or f"mix{int(s.output_index)}" for s in bus.sinks
        ]

    # -- 事实 -------------------------------------------------------------

    @property
    def clip_limit(self) -> float:
        """混音规范化满量程边界 (恒为 1.0)。"""
        return 1.0

    def output_channel_ids(self) -> list[str]:
        return list(self._ids)

    # -- 渲染 -------------------------------------------------------------

    def frames(self, *, chunk_frames: int) -> Any:
        """产出 `(block, faithful)`; block 形状 `(frames, sinks)` float32。"""
        size = max(1, int(chunk_frames))
        total = int(self.timeline.frame_count)
        pos = int(self.timeline.start_sample)
        end = pos + total
        while pos < end:
            take = min(size, end - pos)
            yield self._mix_block(pos, take)
            pos += take

    def _mix_block(
        self, start: int, count: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        out = np.zeros(
            (count, len(self.bus.sinks)), dtype=CANONICAL_PCM_DTYPE
        )
        faithful = np.zeros((count, len(self.bus.sinks)), dtype=bool)
        cache: dict[tuple[str, int], np.ndarray] = {}
        block_peak = 0.0
        block_peak_at: tuple[int, int] | None = None

        for output_index, sink in enumerate(self.bus.sinks):
            acc = np.zeros(count, dtype=CANONICAL_PCM_DTYPE)
            any_data = np.zeros(count, dtype=bool)
            for item in sink.inputs:
                ct = self.source_timeline.channel(item.channel_id)
                if ct is None:
                    continue
                offset = int(ct.offset_samples)
                length = ct.effective_samples
                win_start = start
                win_end = start + count
                # timeline -> source ; 再与本块几何求交 (一次偏移, 不叠加)
                src_start = win_start + offset
                src_end = win_end + offset
                if length is not None:
                    src_start = max(src_start, 0)
                    src_end = min(src_end, int(length))
                if src_end <= src_start:
                    continue
                stream_id = f"{ct.source_id}:s{ct.stream_index}"
                key = (stream_id, int(ct.channel_index))
                data = cache.get(key)
                if data is None:
                    data = self.reader.read_frames(
                        stream_id,
                        channel_index=int(ct.channel_index),
                        start=src_start,
                        count=src_end - src_start,
                    )
                    cache[key] = data
                dst_pos = src_start - offset - start
                cursor = 0
                if dst_pos < 0:
                    cursor = -dst_pos
                    dst_pos = 0
                usable = min(data.shape[0] - cursor, count - dst_pos)
                if usable <= 0:
                    continue
                gain = np.float32(item.gain)
                if gain != 1.0:
                    acc[dst_pos: dst_pos + usable] += (
                        data[cursor: cursor + usable] * gain
                    ).astype(CANONICAL_PCM_DTYPE)
                else:
                    acc[dst_pos: dst_pos + usable] += data[
                        cursor: cursor + usable
                    ]
                any_data[dst_pos: dst_pos + usable] = True

            abs_acc = np.abs(acc)
            if acc.size:
                local = float(abs_acc.max())
                if local > self.stats.per_output_peak[output_index]:
                    self.stats.per_output_peak[output_index] = local
                if local > block_peak:
                    block_peak = local
                    block_peak_at = (int(np.argmax(abs_acc)), output_index)

            over = int(np.count_nonzero(abs_acc > self.clip_limit))
            if over:
                self.stats.clip_count += over
                self.stats.clip_count_by_channel[output_index] += over
                if self.policy is ClipPolicy.ERROR:
                    raise AudioRenderError(
                        REASON_AUDIO_MIX_CLIPPING,
                        f"mix output {output_index} "
                        f"({self._ids[output_index]}) produced {over} "
                        f"sample(s) beyond |{self.clip_limit:.1f}| "
                        f"(peak {float(abs_acc.max()):.6f}, policy=error)",
                        location=self._ids[output_index],
                    )
            if self.policy is ClipPolicy.HARD_CLIP:
                before = over
                np.clip(acc, -1.0, 1.0, out=acc)
                self.stats.hard_clipped += before
            if self.output_format is not None \
                    and self.output_format.clip_limit() < 1.0:
                limit = self.output_format.clip_limit()
                self.stats.format_clip_count += int(
                    np.count_nonzero(abs_acc >= limit)
                )
            out[:, output_index] = acc
            faithful[:, output_index] = any_data
            self.stats.frames += count

        if block_peak_at is not None and block_peak > self.stats.peak:
            frame = start + block_peak_at[0]
            self.stats.peak = float(block_peak)
            self.stats.peak_channel = block_peak_at[1]
            self.stats.peak_frame = int(frame)
            self.stats.peak_inputs = self._peak_inputs(
                block_peak_at[1], frame
            )
        return out, faithful

    def _peak_inputs(
        self, output_index: int, frame: int,
    ) -> list[dict[str, Any]]:
        """峰值样本的逐输入贡献分解 (Σ contributions = peak)。"""
        sink = self.bus.sink(output_index)
        if sink is None:
            return []
        rows: list[dict[str, Any]] = []
        for item in sink.inputs:
            ct = self.source_timeline.channel(item.channel_id)
            if ct is None:
                continue
            src = frame + int(ct.offset_samples)
            length = ct.effective_samples
            value = 0.0
            if src >= 0 and (length is None or src < int(length)):
                block = self.reader.read_frames(
                    f"{ct.source_id}:s{ct.stream_index}",
                    channel_index=int(ct.channel_index),
                    start=src,
                    count=1,
                )
                if block.shape[0]:
                    value = float(block[0])
            rows.append({
                "channel_id": item.channel_id,
                "gain": float(item.gain),
                "sample": value,
                "contribution": value * float(item.gain),
            })
        return rows

    # -- 便捷入口 ---------------------------------------------------------

    def render_to(self, sink_callable: Any, *, chunk_frames: int) -> MixStats:
        """逐块喂给 `sink_callable(block, faithful)`, 返回检波统计。"""
        for block, faithful in self.frames(chunk_frames=chunk_frames):
            sink_callable(block, faithful)
        return self.stats


def build_mix_buses(
    plan: AudioPlan,
    *,
    mix_mode: str = "sum",
    gains: Mapping[str, float] | Sequence[float] | None = None,
    collapse: bool = True,
) -> list[MixBus]:
    """从计划推导混音定义 (无显式 `plan.mix_buses` 时的回退路径)。

    * 计划已有 `mix_buses` -> 原样返回 (显式优先);
    * 否则 `collapse=True` 把**全部选中声道**求和进一个输出声道
      (即 `mix_mode="sum"` 的 N->1 语义);
    * `collapse=False` 每个选中声道各自一个输出声道 (1:1, 仍走同一引擎)。
    """
    explicit = mix_buses_of(plan)
    if explicit:
        return explicit
    ids = list(plan.selected_channels)
    if not ids:
        return []
    builder = MixBusBuilder(plan)
    if collapse:
        bus = builder.sum_all(ids, gains=gains, mix_mode=mix_mode)
    else:
        bus = builder.one_to_one(ids, mix_mode=mix_mode)
    mode = getattr(plan, "mix_mode", None)
    if mode:
        bus.mix_mode = str(mode)
    return [bus]
