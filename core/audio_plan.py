"""Audio selection + channel mapping + executable map spec (v0.7.1 Phase 2).

本模块在 Phase 1 的数据模型之上补上**决策层**, 但**不执行任何东西**:

    AudioSource* -> AudioStream* -> AudioChannel* -> AudioTrack*
        -> Selection -> Channel Mapping -> AudioPlan -> AudioMapSpec
        -> (后续阶段) FFmpeg argv

三个概念严格分离, 代码里也必须是三段不同的东西:

    Selection   哪些 source channel 被保留         -> AudioPlan.selected_channels
    Mapping     已选声道按什么顺序进入输出          -> AudioPlan.channel_mapping
    Mixing      多个源声道是否做 sample 级运算      -> **本阶段完全禁止**

禁止依据: 任何 "N 个源声道 -> 1 个输出声道" 的映射都意味着样本级合成,
validation 一律以 `audio_mix_not_supported` 拒绝 (V8)。本模块**没有**任何
增益 / 求和 / 归一化代码路径 —— 拒绝是结构性的, 不是运行时特判。

数据与执行分离:
    * AudioMapSpec 不持有 PCM、不执行 ffmpeg、JSON-compatible、可 dry-run;
    * spec 只描述"哪个输出的哪个声道来自哪个来源的哪个声道", 外加一个
      **执行策略提示** (整流 copy / 声道过滤), 供后续阶段生成 argv;
    * 本阶段**不实现** complex filtergraph 的采样级 DSP。

同步 (channel_sync) 不受影响: 本模块只**读取** channel 上的 sync 状态并把它
原样带到输出单元上 (§18), 既不测量也不修改。跨媒体 (外挂 WAV) 的时间对齐
同样不在本阶段 —— 见 AudioTiming 的注释。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .audio_models import (
    AudioChannel,
    AudioPlan,
    AudioSource,
    AudioSourceBuilder,
    AudioSourceType,
    AudioSyncResult,
    DEFAULT_SOURCE_ID,
    SyncStatus,
    TrackBuildMode,
)

__all__ = [
    "AudioChannelRef",
    "AudioMapOperation",
    "AudioMapSpec",
    "AudioMapStrategy",
    "AudioOutputTrack",
    "AudioPlanError",
    "AudioPlanner",
    "AudioSourceRef",
    "AudioValidationError",
    "AudioValidationIssue",
    "AudioValidationSeverity",
    "REASON_AUDIO_CHANNEL_NOT_FOUND",
    "REASON_AUDIO_MAPPING_INCOMPLETE",
    "REASON_AUDIO_MAPPING_NOT_SELECTED",
    "REASON_AUDIO_MAPPING_OUTPUT_INDEX",
    "REASON_AUDIO_MAPPING_OUTPUT_ORDER",
    "REASON_AUDIO_MIX_NOT_SUPPORTED",
    "REASON_AUDIO_SOURCE_DUPLICATE",
    "REASON_AUDIO_SOURCE_NOT_FOUND",
    "REASON_AUDIO_STREAM_NOT_FOUND",
    "build_audio_map_spec",
    "build_map_spec",
    "channel_ref",
    "exclude_channels",
    "map_channel",
    "map_channels",
    "output_tracks",
    "parse_channel_ref",
    "require_audio_map_spec",
    "select_all",
    "select_channels",
    "select_sources",
    "select_tracks",
    "source_plan",
    "validate_mapping",
    "validate_plan",
    "validate_selection",
]


# ---------------------------------------------------------------------------
# 稳定 reason codes (§17)
# ---------------------------------------------------------------------------
#
# 这些字符串是**契约**: 日志、报告、测试与后续 CLI 都按它们判定, 不允许
# 随手改字面值 (会破坏跨版本可比性)。

REASON_AUDIO_SOURCE_NOT_FOUND = "audio_source_not_found"          # V1
REASON_AUDIO_STREAM_NOT_FOUND = "audio_stream_not_found"          # V2
REASON_AUDIO_CHANNEL_NOT_FOUND = "audio_channel_not_found"        # V3
REASON_AUDIO_SOURCE_DUPLICATE = "audio_source_duplicate"          # V4
REASON_AUDIO_MAPPING_OUTPUT_INDEX = "audio_mapping_output_index"  # V5
REASON_AUDIO_MAPPING_NOT_SELECTED = "audio_mapping_not_selected"  # V6
REASON_AUDIO_MAPPING_OUTPUT_ORDER = "audio_mapping_output_order"  # V5 (顺序)
REASON_AUDIO_MAPPING_INCOMPLETE = "audio_mapping_incomplete"      # V5 (缺项)
REASON_AUDIO_MIX_NOT_SUPPORTED = "audio_mix_not_supported"        # V7/V8


class AudioPlanError(ValueError):
    """规划期错误。携带稳定 `reason` 与定位信息, 便于逐项判断。"""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        location: str | None = None,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.location = location

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"reason": self.reason, "detail": self.detail}
        if self.location is not None:
            data["location"] = self.location
        return data


class AudioValidationError(AudioPlanError):
    """validation 失败 (= AudioPlanError 的同义子类, 便于捕获区分)。"""


# ---------------------------------------------------------------------------
# 通道引用: "camera:s0:c2" <-> (source_id, stream_index, channel_index)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioSourceRef:
    """一个物理来源的引用 (identity-only, 不带 PCM/模型对象)。"""

    source_id: str
    source_type: str = AudioSourceType.MEDIA.value
    path: str | None = None
    display_name: str | None = None
    input_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source_id": self.source_id,
            "source_type": self.source_type,
        }
        if self.path is not None:
            data["path"] = self.path
        if self.display_name is not None:
            data["display_name"] = self.display_name
        if self.input_index is not None:
            data["input_index"] = self.input_index
        return data


@dataclass(frozen=True)
class AudioChannelRef:
    """一个**源声道**的完整引用 (§5/§18)。

    这是 selection / mapping 的对外引用单位, 也是 report 里的追溯锚点:
    输出声道 -> AudioChannelRef -> (source, stream, channel) + sync。
    """

    source_id: str
    stream_index: int
    channel_index: int
    audio_position: int | None = None
    source_channel_name: str | None = None
    channel_count: int = 1
    sample_rate: int = 0
    sample_format: str = "unknown"
    sync_status: str = "not_processed"
    sync_offset_samples: float | None = None
    sync_offset_ms: float | None = None
    source_type: str = AudioSourceType.MEDIA.value

    @property
    def channel_id(self) -> str:
        """全局声道身份: "camera:s2:c0" (与 AudioChannel.id 完全一致)。"""
        return f"{self.source_id}:s{self.stream_index}:c{self.channel_index}"

    @property
    def stream_id(self) -> str:
        return f"{self.source_id}:s{self.stream_index}"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "channel_id": self.channel_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "stream_index": int(self.stream_index),
            "channel_index": int(self.channel_index),
            "sync_status": self.sync_status,
        }
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.source_channel_name is not None:
            data["source_channel_name"] = self.source_channel_name
        if self.channel_count != 1:
            data["channel_count"] = int(self.channel_count)
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        if self.sample_format != "unknown":
            data["sample_format"] = self.sample_format
        if self.sync_offset_samples is not None:
            data["sync_offset_samples"] = self.sync_offset_samples
        if self.sync_offset_ms is not None:
            data["sync_offset_ms"] = self.sync_offset_ms
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioChannelRef":
        parsed = _parse_channel_id(data.get("channel_id")) or {}
        return cls(
            source_id=str(
                data.get("source_id")
                or parsed.get("source_id")
                or DEFAULT_SOURCE_ID
            ),
            stream_index=int(
                data.get("stream_index", parsed.get("stream_index", 0))
            ),
            channel_index=int(
                data.get("channel_index", parsed.get("channel_index", 0))
            ),
            audio_position=_opt_int(data.get("audio_position")),
            source_channel_name=_opt_str(data.get("source_channel_name")),
            channel_count=_opt_int(data.get("channel_count")) or 1,
            sample_rate=_opt_int(data.get("sample_rate")) or 0,
            sample_format=str(data.get("sample_format") or "unknown"),
            sync_status=str(data.get("sync_status") or "not_processed"),
            sync_offset_samples=_opt_float(data.get("sync_offset_samples")),
            sync_offset_ms=_opt_float(data.get("sync_offset_ms")),
            source_type=str(
                data.get("source_type") or AudioSourceType.MEDIA.value
            ),
        )


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


_CHANNEL_ID_RE = re.compile(r"^s(\d+):?c(\d+)$")

# ffprobe 风格的声道名 (C0/C1/…) 不作为身份: 它们只是**布局未知时的显示名**,
# 与 channel_index 一一对应但可能重名, 因此解析身份只认 sN:cM 数字形式。


def _parse_channel_id(channel_id: Any) -> dict[str, Any] | None:
    r"""解析 "camera:s2:c0" -> {source_id, stream_index, channel_index}。

    兼容两种尾部写法 (都是同一身份的合法表示):

        "camera:s2:c0"   canonical (AudioChannel.id 的输出格式)
        "camera:s2c0"    紧凑形式

    `source_id` 里允许出现冒号 (例如 "cam:left"), 因此按"来源最长优先"逐段
    尝试切分 —— 尽量把冒号留给 source_id, 避免把 `cam:left:s1:c3` 解析成
    来源 `cam`。无法解析返回 None (调用方报 not_found, **不猜**)。
    """
    if channel_id is None:
        return None
    text = str(channel_id).strip()
    if not text:
        return None
    parts = text.split(":")
    for split in range(len(parts) - 1, 0, -1):
        source = ":".join(parts[:split])
        match = _CHANNEL_ID_RE.match(":".join(parts[split:]))
        if match is None or not source:
            continue
        return {
            "source_id": source,
            "stream_index": int(match.group(1)),
            "channel_index": int(match.group(2)),
        }
    return None


def parse_channel_ref(channel_id: str) -> AudioChannelRef:
    """仅凭身份字符串构造 AudioChannelRef (无 sync/采样事实)。

    用于"只有 id"的场景 (日志回放、报告解析); 需要完整事实时用
    `channel_ref(plan, ...)`。
    """
    parsed = _parse_channel_id(channel_id)
    if parsed is None:
        raise AudioValidationError(
            REASON_AUDIO_CHANNEL_NOT_FOUND,
            f"malformed channel id: {channel_id!r} "
            "(expected 'source:sN:cM')",
            location=str(channel_id),
        )
    return AudioChannelRef(
        source_id=parsed["source_id"],
        stream_index=parsed["stream_index"],
        channel_index=parsed["channel_index"],
    )


def channel_ref(
    plan: AudioPlan,
    channel: AudioChannel | AudioChannelRef | str,
) -> AudioChannelRef:
    """把 AudioChannel / id 字符串 -> AudioChannelRef (带 sync, §18)。"""
    if isinstance(channel, AudioChannelRef):
        return channel
    if isinstance(channel, AudioChannel):
        return AudioChannelRef(
            source_id=channel.source_id,
            stream_index=channel.stream_index,
            channel_index=channel.channel_index,
            audio_position=_channel_audio_position(plan, channel),
            source_channel_name=channel.source_channel_name,
            channel_count=channel.channel_count,
            sample_rate=channel.sample_rate,
            sample_format=channel.sample_format.value,
            sync_status=channel.sync.status.value,
            sync_offset_samples=channel.sync.offset_samples,
            sync_offset_ms=channel.sync.offset_ms,
            source_type=_source_type(plan, channel.source_id),
        )
    found = plan.channel(str(channel))
    if found is None:
        raise AudioValidationError(
            REASON_AUDIO_CHANNEL_NOT_FOUND,
            f"no such audio channel in plan: {channel!r}",
            location=str(channel),
        )
    return channel_ref(plan, found)


def _channel_audio_position(plan: AudioPlan, channel: AudioChannel) -> int | None:
    source = plan.source(channel.source_id)
    if source is not None:
        stream = source.stream(channel.stream_index)
        if stream is not None:
            return stream.audio_position
    for track in plan.input_tracks:
        if track.source_id != channel.source_id:
            continue
        if track.source_stream_index != channel.stream_index:
            continue
        value = track.metadata.get("audio_position")
        parsed = _opt_int(value)
        if parsed is not None:
            return parsed
    return None


def _source_type(plan: AudioPlan, source_id: str) -> str:
    source = plan.source(source_id)
    if source is None:
        return AudioSourceType.MEDIA.value
    return source.source_type.value


# ---------------------------------------------------------------------------
# 输出单元 (§9)
# ---------------------------------------------------------------------------


class AudioMapStrategy(str, Enum):
    """执行策略提示 —— 不是 ffmpeg 命令, 只是"该用哪种手段"的分类。

    `STREAM_COPY`  : 整条源流原样保留 (可 `-map` + `-c:a copy`)
    `CHANNEL_FILTER`: 只取流内部分声道 / 重排 (需要声道过滤 filter)
    `MIXING`       : 出现 N 源 -> 1 输出, **本阶段一律拒绝**
    """

    STREAM_COPY = "stream_copy"
    CHANNEL_FILTER = "channel_filter"
    MIXING = "mixing"


class AudioValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class AudioMapOperation:
    """一个输出单元的执行规格 (identity-only, 不含 PCM)。

    一个 operation 对应**一个输入的一条流**上的一段连续输出;
    整流原样保留用 `STREAM_COPY`, 部分取用/重排用 `CHANNEL_FILTER`。
    """

    strategy: AudioMapStrategy
    output_indices: list[int] = field(default_factory=list)
    source_id: str = DEFAULT_SOURCE_ID
    stream_index: int = 0
    audio_position: int | None = None
    input_index: int | None = None
    channel_indices: list[int] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    stream_channel_count: int = 0

    @property
    def output_count(self) -> int:
        return len(self.output_indices)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "strategy": self.strategy.value,
            "output_indices": list(self.output_indices),
            "source_id": self.source_id,
            "stream_index": int(self.stream_index),
            "channel_indices": list(self.channel_indices),
            "channel_ids": list(self.channel_ids),
        }
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.input_index is not None:
            data["input_index"] = int(self.input_index)
        if self.stream_channel_count:
            data["stream_channel_count"] = int(self.stream_channel_count)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioMapOperation":
        return cls(
            strategy=AudioMapStrategy(
                str(data.get("strategy") or AudioMapStrategy.CHANNEL_FILTER.value)
            ),
            output_indices=[int(i) for i in (data.get("output_indices") or [])],
            source_id=str(data.get("source_id") or DEFAULT_SOURCE_ID),
            stream_index=int(data.get("stream_index") or 0),
            audio_position=_opt_int(data.get("audio_position")),
            input_index=_opt_int(data.get("input_index")),
            channel_indices=[int(i) for i in (data.get("channel_indices") or [])],
            channel_ids=[str(i) for i in (data.get("channel_ids") or [])],
            stream_channel_count=int(data.get("stream_channel_count") or 0),
        )


@dataclass
class AudioOutputTrack:
    """最终输出中的一个**逻辑音频单元** (§9)。

    语义**不是**"一个 source channel": 它可以承载

    * 一条完整的源 track (整流保留)      -> assignments = 该流全部声道
    * 一个被选中的源声道 (单通道输出)    -> assignments = 1 条
    * 一个被显式映射的源声道组 (重排后的多通道输出)

    一旦出现 "N 个源声道 -> 1 个输出声道" 且需要样本合成, 那就是 Mixing,
    validation 必须拒绝 —— 本类只是描述, 不判断; 判断在 `validate_mapping`
    与 `build_audio_map_spec` 里, 拒绝理由是 `audio_mix_not_supported`。
    """

    output_track_id: str
    assignments: list[AudioChannelRef] = field(default_factory=list)
    strategy: AudioMapStrategy = AudioMapStrategy.CHANNEL_FILTER
    sample_rate: int = 0
    sample_format: str = "unknown"
    layout: str = ""
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- 身份 / 事实 ------------------------------------------------------

    @property
    def output_indices(self) -> list[int]:
        return [i for i in range(len(self.assignments))]

    @property
    def channel_count(self) -> int:
        return len(self.assignments)

    @property
    def source_ids(self) -> list[str]:
        """逐声道来源身份 —— §18 的追溯链 (输出 -> camera:s0:c2 -> +960)。"""
        return [a.channel_id for a in self.assignments]

    @property
    def is_mono(self) -> bool:
        return self.channel_count == 1

    @property
    def is_multi_source(self) -> bool:
        return len({a.source_id for a in self.assignments}) > 1

    @property
    def is_mixing(self) -> bool:
        """是否要求把多个源声道合成到一个输出声道 (本阶段禁止)。"""
        return self.strategy is AudioMapStrategy.MIXING

    def channel_refs_at(self, output_index: int) -> list[AudioChannelRef]:
        """某个输出声道背后的源声道列表 (>1 即 Mixing)。"""
        if 0 <= output_index < len(self.assignments):
            return [self.assignments[output_index]]
        return []

    def sync_of(self, output_index: int) -> AudioSyncResult:
        """输出声道继承的同步结果 (来自其唯一源声道; 无则 NOT_PROCESSED)。"""
        refs = self.channel_refs_at(output_index)
        if not refs:
            return AudioSyncResult()
        ref = refs[0]
        return AudioSyncResult(
            status=SyncStatus.coerce(ref.sync_status, SyncStatus.NOT_PROCESSED),
            offset_samples=ref.sync_offset_samples,
            offset_ms=ref.sync_offset_ms,
        )

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "output_track_id": self.output_track_id,
            "channel_count": int(self.channel_count),
            "strategy": self.strategy.value,
            "enabled": bool(self.enabled),
            "assignments": [a.to_dict() for a in self.assignments],
        }
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        if self.sample_format != "unknown":
            data["sample_format"] = self.sample_format
        if self.layout:
            data["layout"] = self.layout
        if self.metadata:
            data["metadata"] = copy.deepcopy(self.metadata)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioOutputTrack":
        return cls(
            output_track_id=str(data.get("output_track_id") or ""),
            assignments=[
                AudioChannelRef.from_dict(a)
                for a in (data.get("assignments") or [])
                if isinstance(a, Mapping)
            ],
            strategy=AudioMapStrategy(
                str(data.get("strategy") or AudioMapStrategy.CHANNEL_FILTER.value)
            ),
            sample_rate=_opt_int(data.get("sample_rate")) or 0,
            sample_format=str(data.get("sample_format") or "unknown"),
            layout=str(data.get("layout") or ""),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata") or {}),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


# ---------------------------------------------------------------------------
# 执行规格 (§12/§13)
# ---------------------------------------------------------------------------


@dataclass
class AudioMapSpec:
    """AudioPlan -> AudioMapSpec: **可 dry-run 的执行规格**。

    职责边界 (硬要求):

    * 不持有 PCM;
    * 不执行 ffmpeg (不 import subprocess, 不拼 argv);
    * JSON-compatible (纯 dict/list/标量/字符串枚举);
    * source/channel 身份完整 (每项都带 `AudioChannelRef`);
    * output index 稳定 (0..N-1 连续, validation 保证);
    * 不含任何 mixer 参数 (增益/权重/求和字段在本结构里**不存在**)。

    本阶段不生成 filtergraph 字符串: `operations` 只给出**策略分类**与身份,
    供后续阶段生成 `-map` / 声道过滤 argv。
    """

    sources: list[AudioSourceRef] = field(default_factory=list)
    assignments: list[AudioChannelRef] = field(default_factory=list)
    operations: list[AudioMapOperation] = field(default_factory=list)
    tracks: list[AudioOutputTrack] = field(default_factory=list)
    strategy: AudioMapStrategy = AudioMapStrategy.CHANNEL_FILTER
    preserve_original: bool = True
    executable: bool = False
    full_stream_copy: bool = False
    mapping_kind: str = "derived"
    model_version: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- 事实 -------------------------------------------------------------

    @property
    def output_channels(self) -> int:
        return len(self.assignments)

    @property
    def is_multi_source(self) -> bool:
        return len({a.source_id for a in self.assignments}) > 1

    @property
    def ok(self) -> bool:
        return self.executable and not self.errors

    @property
    def source_ids(self) -> list[str]:
        return [s.source_id for s in self.sources]

    def assignment(self, output_index: int) -> AudioChannelRef | None:
        if 0 <= output_index < len(self.assignments):
            return self.assignments[output_index]
        return None

    def trace(self, output_index: int) -> dict[str, Any] | None:
        """输出声道 -> 源身份 -> sync 的完整追溯 (§18, 便于报告/测试)。"""
        ref = self.assignment(output_index)
        if ref is None:
            return None
        return {
            "output_index": int(output_index),
            "channel_id": ref.channel_id,
            "source_id": ref.source_id,
            "stream_index": ref.stream_index,
            "channel_index": ref.channel_index,
            "audio_position": ref.audio_position,
            "sync_status": ref.sync_status,
            "sync_offset_samples": ref.sync_offset_samples,
            "sync_offset_ms": ref.sync_offset_ms,
        }

    def summary(self) -> dict[str, Any]:
        """紧凑的 dry-run 摘要 (日志/测试用, JSON-compatible)。"""
        return {
            "executable": self.executable,
            "strategy": self.strategy.value,
            "full_stream_copy": self.full_stream_copy,
            "mapping_kind": self.mapping_kind,
            "output_channels": self.output_channels,
            "sources": [s.source_id for s in self.sources],
            "operations": [o.to_dict() for o in self.operations],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model_version": int(self.model_version),
            "executable": bool(self.executable),
            "strategy": self.strategy.value,
            "full_stream_copy": bool(self.full_stream_copy),
            "mapping_kind": self.mapping_kind,
            "preserve_original": bool(self.preserve_original),
            "output_channels": int(self.output_channels),
            "sources": [s.to_dict() for s in self.sources],
            "assignments": [a.to_dict() for a in self.assignments],
            "operations": [o.to_dict() for o in self.operations],
            "tracks": [t.to_dict() for t in self.tracks],
        }
        if self.errors:
            data["errors"] = copy.deepcopy(self.errors)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioMapSpec":
        return cls(
            sources=[
                AudioSourceRef(
                    source_id=str(s.get("source_id") or DEFAULT_SOURCE_ID),
                    source_type=str(s.get("source_type") or "media"),
                    path=_opt_str(s.get("path")),
                    display_name=_opt_str(s.get("display_name")),
                    input_index=_opt_int(s.get("input_index")),
                )
                for s in (data.get("sources") or [])
                if isinstance(s, Mapping)
            ],
            assignments=[
                AudioChannelRef.from_dict(a)
                for a in (data.get("assignments") or [])
                if isinstance(a, Mapping)
            ],
            operations=[
                AudioMapOperation.from_dict(o)
                for o in (data.get("operations") or [])
                if isinstance(o, Mapping)
            ],
            tracks=[
                AudioOutputTrack.from_dict(t)
                for t in (data.get("tracks") or [])
                if isinstance(t, Mapping)
            ],
            strategy=AudioMapStrategy(
                str(data.get("strategy") or AudioMapStrategy.CHANNEL_FILTER.value)
            ),
            preserve_original=bool(data.get("preserve_original", True)),
            executable=bool(data.get("executable", False)),
            full_stream_copy=bool(data.get("full_stream_copy", False)),
            mapping_kind=str(data.get("mapping_kind") or "derived"),
            model_version=_opt_int(data.get("model_version")) or 0,
            errors=[
                dict(e) for e in (data.get("errors") or [])
                if isinstance(e, Mapping)
            ],
            warnings=[str(w) for w in (data.get("warnings") or [])],
        )


# ---------------------------------------------------------------------------
# Selection (§7/§15) —— source-aware, 不可变入参 / 新计划出参
# ---------------------------------------------------------------------------


class AudioPlanner:
    """在 AudioPlan 上做选择与映射。**每次操作都返回新的 AudioPlan**。

    §15 要求"优先产生新的规划结果, 而不是不可逆地破坏原模型", 因此:

    * 本类**深拷贝**输入计划的所有权 (sources / tracks / channels);
    * 选择与映射都是纯数据操作, 不触碰 AudioSource / AudioStream /
      AudioChannel 的原始身份 (source_id / stream_index / audio_position /
      channel_index / sync / metadata 全部原样保留);
    * 返回的新 AudioPlan 与入参无共享可变状态。
    """

    def __init__(self, plan: AudioPlan | None = None) -> None:
        self.plan = plan if plan is not None else AudioPlan()

    # -- 内部 -------------------------------------------------------------

    def _known_ids(self) -> dict[str, AudioChannel]:
        return {c.id: c for c in self.plan.all_channels()}

    def _resolve_id(self, raw: Any) -> str | None:
        """把任意合法写法归一到 canonical `AudioChannel.id`。

        接受 canonical ("camera:s2:c0") 与紧凑 ("camera:s2c0") 两种形式 ——
        两者是同一身份, 归一后再查表, 避免"同一个声道两种写法查不到"。
        """
        key = str(raw)
        if key in self._known_ids():
            return key
        parsed = _parse_channel_id(key)
        if parsed is None:
            return None
        canonical = (
            f"{parsed['source_id']}:s{parsed['stream_index']}"
            f":c{parsed['channel_index']}"
        )
        return canonical if canonical in self._known_ids() else None

    def _require_known(
        self, channel_ids: Iterable[str],
    ) -> list[AudioChannel]:
        """解析并校验 id; 失败时给出**最精确**的 reason code。

        与 `validate_plan` 的分类保持一致 (来源 -> 流 -> 声道), 这样
        planner 抛出的 reason 与 dry-run spec 报出的 reason 是同一套。
        """
        known = self._known_ids()
        sources = {s.source_id for s in self.plan.sources}
        sources |= {t.source_id for t in self.plan.input_tracks}
        out: list[AudioChannel] = []
        for raw in channel_ids:
            key = self._resolve_id(raw)
            if key is None:
                parsed = _parse_channel_id(str(raw))
                if parsed is None:
                    raise AudioValidationError(
                        REASON_AUDIO_CHANNEL_NOT_FOUND,
                        f"malformed channel id: {raw!r} "
                        "(expected 'source:sN:cM')",
                        location=str(raw),
                    )
                if parsed["source_id"] not in sources:
                    raise AudioValidationError(
                        REASON_AUDIO_SOURCE_NOT_FOUND,
                        f"unknown audio source in {raw!r}",
                        location=str(raw),
                    )
                if (parsed["source_id"], parsed["stream_index"]) \
                        not in _known_stream_ids(self.plan):
                    raise AudioValidationError(
                        REASON_AUDIO_STREAM_NOT_FOUND,
                        f"no audio stream {parsed['stream_index']} in source "
                        f"{parsed['source_id']!r}",
                        location=str(raw),
                    )
                raise AudioValidationError(
                    REASON_AUDIO_CHANNEL_NOT_FOUND,
                    f"unknown audio channel: {raw!r}",
                    location=str(raw),
                )
            out.append(known[key])
        return out

    def _mark_explicit(self, kind: str = "explicit") -> None:
        self.plan.mapping_kind = kind

    # -- selection --------------------------------------------------------

    def select_all(self) -> AudioPlan:
        """全选: 全部输入的每个声道, 保持自然顺序 (即默认计划语义)。"""
        return self.select_channels(
            *[c.id for c in self.plan.all_channels()], reset_mapping=True
        )

    def select_sources(self, *source_ids: str) -> AudioPlan:
        """按来源选择: 命中的来源的全部声道 (跨来源可混用)。"""
        wanted = [str(s) for s in source_ids]
        known_ids = {s.source_id for s in self.plan.sources}
        known_ids |= {t.source_id for t in self.plan.input_tracks}
        for sid in wanted:
            if sid not in known_ids:
                raise AudioValidationError(
                    REASON_AUDIO_SOURCE_NOT_FOUND,
                    f"unknown audio source: {sid!r}",
                    location=sid,
                )
        return self.select_channels(
            *[c.id for c in self.plan.all_channels()
              if c.source_id in wanted],
            reset_mapping=True,
        )

    def select_tracks(self, *track_ids: str) -> AudioPlan:
        """按 track 选择: 整个 AudioTrack 的全部声道。"""
        wanted = [str(t) for t in track_ids]
        tracks = {t.track_id: t for t in self.plan.input_tracks}
        for tid in wanted:
            if tid not in tracks:
                raise AudioValidationError(
                    REASON_AUDIO_CHANNEL_NOT_FOUND,
                    f"unknown audio track: {tid!r}",
                    location=tid,
                )
        ids: list[str] = []
        for tid in wanted:
            ids.extend(c.id for c in tracks[tid].channels)
        return self.select_channels(*ids, reset_mapping=True)

    def select_channels(
        self,
        *channel_ids: str,
        reset_mapping: bool = True,
    ) -> AudioPlan:
        """按声道选择 (§7): 接受全局身份 "camera:s0:c2"。

        顺序即选择顺序。`reset_mapping=True` 时把映射重置为"按选择顺序
        顺序输出"(derived) —— 这是最朴素的语义, 不隐含任何合成。
        """
        channels = self._require_known(channel_ids)
        self.plan.selected_channels = [c.id for c in channels]
        self._resync_selected_tracks()
        self.plan.output_tracks = []
        if reset_mapping:
            self.plan.channel_mapping = []
            self.plan.mapping_kind = "derived"
        return self.plan

    def exclude_channels(self, *channel_ids: str) -> AudioPlan:
        """排除若干声道, 其余保持自然顺序。

        这不是"负向选择后重排": 剩余声道的相对顺序仍是源顺序, 因此
        结果仍是简单保留语义 (不引入任何样本运算)。
        """
        drop = {str(c) for c in channel_ids}
        keep = [c.id for c in self.plan.all_channels() if c.id not in drop]
        was_explicit = self.plan.mapping_kind == "explicit"
        plan = self.select_channels(*keep, reset_mapping=True)
        if was_explicit:
            # 显式映射在被剔除后已失效: 明确重算而不是留着一个指向
            # 不存在声道的陈旧映射。
            self.plan.channel_mapping = _mapping_entries(plan, keep)
            self.plan.mapping_kind = (
                "derived"
                if _is_identity_over_selection(plan, keep)
                else "explicit"
            )
            if self.plan.mapping_kind == "derived":
                self.plan.channel_mapping = []
        return self.plan

    def _resync_selected_tracks(self) -> None:
        """track 级选择是 channel 级选择的**聚合视图**, 不允许长期不一致。

        这是"Selection != Mapping"的一种体现: selected_tracks 只表示
        "这条 track 有声道被选中", 输出顺序由 mapping 决定。
        """
        chosen = set(self.plan.selected_channels)
        self.plan.selected_tracks = [
            t.track_id for t in self.plan.input_tracks
            if any(c.id in chosen for c in t.channels)
        ]

    # -- mapping ----------------------------------------------------------

    def map_channel(self, channel_id: str, output_index: int) -> AudioPlan:
        """单条映射: 把某个已选声道放到指定输出位置 (**位置互换语义**)。

        * 该声道原本占着另一个位置 -> 两者**互换**, 其余位置不动
          (这样 `map_channel(c1, 0)` 得到的正是"c1 在前、c0 在后");
        * 该声道原本不在映射里 -> 只能追加到末尾 (index == len),
          否则会留下空洞 (V5), 明确报 `audio_mapping_output_index`。
        """
        idx = int(output_index)
        if idx < 0:
            raise AudioValidationError(
                REASON_AUDIO_MAPPING_OUTPUT_INDEX,
                f"negative output index: {idx}",
                location=str(idx),
            )
        channel = self._require_known([channel_id])[0]
        if channel.id not in set(self.plan.selected_channels):
            raise AudioValidationError(
                REASON_AUDIO_MAPPING_NOT_SELECTED,
                f"channel {channel.id!r} is not selected; "
                "select it before mapping",
                location=channel.id,
            )
        # 以"有效映射"(显式或派生) 为基准, 避免把当前顺序误当成选择顺序
        current = [
            str(e.get("source_channel_id") or "")
            for e in _effective_mapping(self.plan)
        ]
        if idx > len(current):
            raise AudioValidationError(
                REASON_AUDIO_MAPPING_OUTPUT_INDEX,
                f"output index {idx} would leave a hole "
                f"(contiguous indices 0..{len(current)})",
                location=str(idx),
            )
        if idx == len(current):
            current.append(channel.id)
        else:
            occupant = current.index(channel.id) if channel.id in current else None
            if occupant is None:
                raise AudioValidationError(
                    REASON_AUDIO_MAPPING_OUTPUT_INDEX,
                    f"channel {channel.id!r} is not mapped yet, so it can "
                    f"only be appended at index {len(current)}",
                    location=str(idx),
                )
            current[idx], current[occupant] = (
                current[occupant], current[idx],
            )
        return self.map_channels(*current)

    def map_channels(
        self, *channel_ids: str, mapping_kind: str | None = None,
    ) -> AudioPlan:
        """完整映射 (§16): 按给定顺序把已选声道分配给 output 0..N-1。

        规则:
          * 每个 id 必须**已被 selection 选中** (V6), 否则
            `audio_mapping_not_selected`;
          * 同一个源声道不得占据两个输出位置 (V7), 否则
            `audio_mapping_output_order`;
          * 输出 index 恒为 0..N-1 (V5), 本 API 不接受任意 index 列表。
        """
        channels = self._require_known(channel_ids)
        selected = set(self.plan.selected_channels)
        for channel in channels:
            if channel.id not in selected:
                raise AudioValidationError(
                    REASON_AUDIO_MAPPING_NOT_SELECTED,
                    f"channel {channel.id!r} is not selected; "
                    "select it before mapping",
                    location=channel.id,
                )
        ids = [c.id for c in channels]
        # V8: 映射比选择少 -> 就是把多个源声道压进更少的输出, 即样本级合成。
        # 本阶段**没有**混音实现, 因此必须拒绝而不是静默丢弃。
        if len(ids) < len(selected):
            raise AudioValidationError(
                REASON_AUDIO_MIX_NOT_SUPPORTED,
                f"{len(selected)} channel(s) selected but {len(ids)} output "
                "channel(s) requested — collapsing N sources into fewer "
                "outputs requires sample-level mixing, which is not "
                "implemented in v0.7.1 Phase 2",
            )
        seen: set[str] = set()
        for cid in ids:
            if cid in seen:
                raise AudioValidationError(
                    REASON_AUDIO_MAPPING_OUTPUT_ORDER,
                    f"duplicate mapping: {cid!r} occupies more than one "
                    "output channel (copy semantics are not defined)",
                    location=cid,
                )
            seen.add(cid)
        self.plan.channel_mapping = _mapping_entries(self.plan, ids)
        # mapping_kind 记录的是**取得方式**, 不是"是否走了快路径":
        #   "derived"  = 由 selection 顺序派生 (调用方没指定输出顺序)
        #   "explicit" = 调用方显式指定了输出顺序
        # 是否可以用 `-map` + copy 由 spec 的 `full_stream_copy` 独立判定 ——
        # 那是"是否恰好整流自然顺序"的结构事实, 与 mapping_kind 无关。
        self.plan.mapping_kind = mapping_kind or "explicit"
        self.plan.output_tracks = []
        return self.plan

    def reorder(self, *channel_ids: str) -> AudioPlan:
        """4CH 重排等价的语义糖 (与 map_channels 同一实现, 同一校验)。"""
        return self.map_channels(*channel_ids)

    def reset_mapping(self) -> AudioPlan:
        """丢弃显式映射, 回到"按当前选择顺序输出"。"""
        self.plan.channel_mapping = []
        self.plan.mapping_kind = "derived"
        self.plan.output_tracks = []
        return self.plan

    # -- 输出 -------------------------------------------------------------

    def output_tracks(self) -> list[AudioOutputTrack]:
        """把当前 plan 编译为输出单元列表 (纯描述, 不执行)。"""
        return _build_output_tracks(self.plan)

    def map_spec(self) -> AudioMapSpec:
        """编译为可 dry-run 的执行规格 (含 validation 结果)。"""
        return build_audio_map_spec(self.plan)


# ---------------------------------------------------------------------------
# 内部: 映射条目与身份判定
# ---------------------------------------------------------------------------


def _mapping_entries(
    plan: AudioPlan, channel_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """channel id 序列 -> 显式映射条目 (含完整身份, 顺序即 output index)。"""
    by_id = {c.id: c for c in plan.all_channels()}
    entries: list[dict[str, Any]] = []
    for index, cid in enumerate(channel_ids):
        channel = by_id.get(_canonical_channel_id(cid) or str(cid))
        if channel is None:
            continue
        stream = None
        source = plan.source(channel.source_id)
        if source is not None:
            stream = source.stream(channel.stream_index)
        entries.append({
            "output_index": index,
            "source_channel_id": channel.id,
            "source_id": channel.source_id,
            "stream_index": channel.stream_index,
            "channel_index": channel.channel_index,
            "source_channel_name": channel.source_channel_name,
            "audio_position": (
                stream.audio_position if stream is not None
                else _channel_audio_position(plan, channel)
            ),
            "sync_status": channel.sync.status.value,
            "sync_offset_samples": channel.sync.offset_samples,
            "sync_offset_ms": channel.sync.offset_ms,
        })
    return entries


def _is_identity_over_selection(
    plan: AudioPlan, channel_ids: Sequence[str],
) -> bool:
    """该映射是否恰为"当前选择的自然顺序" —— 是则无需冻结为显式映射。

    判定标准 (§13 情况 1 的入口条件): 每个声道都属于一个**完整保留**的流,
    且流内声道按自然顺序、各流之间按来源/流顺序连续排列。满足它就是
    "整流原样保留", 生产路径可以走 `-map` + `copy`。
    """
    entries = _effective_mapping(plan, channel_ids)
    return _is_full_stream_identity(plan, entries)


def _effective_mapping(
    plan: AudioPlan, channel_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """有效映射 = 显式 mapping 优先, 否则由选择顺序派生 (derived)。"""
    if plan.channel_mapping:
        entries = [
            dict(e) for e in plan.channel_mapping
            if isinstance(e, Mapping)
        ]
        entries.sort(key=lambda e: _opt_int(e.get("output_index")) or 0)
        return entries
    ids = list(channel_ids if channel_ids is not None
               else plan.selected_channels)
    return _mapping_entries(plan, ids)


def _stream_signature(plan: AudioPlan, source_id: str, stream_index: int):
    """(input_index, audio_position, stream_channel_count) —— 执行所需事实。"""
    source = plan.source(source_id)
    stream = source.stream(stream_index) if source is not None else None
    return (
        source.input_index if source is not None else None,
        stream.audio_position if stream is not None else None,
        stream.channel_count if stream is not None else 0,
    )


def _is_full_stream_identity(
    plan: AudioPlan, entries: Sequence[Mapping[str, Any]],
) -> bool:
    """entries 是否恰好是"若干条完整流的自然顺序拼接"。"""
    if not entries:
        return False
    groups: list[tuple[str, int, list[int]]] = []
    for entry in entries:
        source_id = str(entry.get("source_id") or DEFAULT_SOURCE_ID)
        stream_index = _opt_int(entry.get("stream_index")) or 0
        channel_index = _opt_int(entry.get("channel_index")) or 0
        if groups and groups[-1][0] == source_id \
                and groups[-1][1] == stream_index:
            groups[-1][2].append(channel_index)
        else:
            groups.append((source_id, stream_index, [channel_index]))
    for source_id, stream_index, channels in groups:
        _input, _pos, total = _stream_signature(plan, source_id, stream_index)
        expected = list(range(total)) if total > 0 else []
        if channels != expected or not expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Validation (§17)
# ---------------------------------------------------------------------------


@dataclass
class AudioValidationIssue:
    reason: str
    detail: str
    severity: AudioValidationSeverity = AudioValidationSeverity.ERROR
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reason": self.reason,
            "detail": self.detail,
            "severity": self.severity.value,
        }
        if self.location is not None:
            data["location"] = self.location
        return data


def _known_source_ids(plan: AudioPlan) -> set[str]:
    ids = {s.source_id for s in plan.sources}
    ids |= {t.source_id for t in plan.input_tracks}
    for track in plan.input_tracks:
        ids |= {c.source_id for c in track.channels}
    return ids


def _known_stream_ids(plan: AudioPlan) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for source in plan.sources:
        for stream in source.streams:
            out.add((source.source_id, stream.stream_index))
    for track in plan.input_tracks:
        out.add((track.source_id, track.source_stream_index))
    return out


def _known_channel_ids(plan: AudioPlan) -> set[str]:
    return {c.id for c in plan.all_channels()}


def _canonical_channel_id(raw: Any) -> str | None:
    """任意合法写法 -> canonical `AudioChannel.id`; 无法解析返回 None。"""
    parsed = _parse_channel_id(raw)
    if parsed is None:
        return None
    return (
        f"{parsed['source_id']}:s{parsed['stream_index']}"
        f":c{parsed['channel_index']}"
    )


def validate_selection(plan: AudioPlan) -> list[AudioValidationIssue]:
    """校验选择集本身: 来源/流/声道存在性 + 身份不重复 (V1..V4)。"""
    issues: list[AudioValidationIssue] = []
    sources = _known_source_ids(plan)
    streams = _known_stream_ids(plan)
    channels = _known_channel_ids(plan)

    seen: set[str] = set()
    for raw in plan.selected_channels:
        cid = str(raw)
        parsed = _parse_channel_id(cid)
        if parsed is None:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_CHANNEL_NOT_FOUND,
                f"malformed channel id: {cid!r} (expected 'source:sN:cM')",
                location=cid,
            ))
            continue
        if parsed["source_id"] not in sources:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_SOURCE_NOT_FOUND,
                f"no such audio source: {parsed['source_id']!r}",
                location=cid,
            ))
            continue
        key = (parsed["source_id"], parsed["stream_index"])
        if key not in streams:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_STREAM_NOT_FOUND,
                f"no audio stream {parsed['stream_index']} in source "
                f"{parsed['source_id']!r}",
                location=cid,
            ))
            continue
        canonical = _canonical_channel_id(cid)
        if canonical not in channels:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_CHANNEL_NOT_FOUND,
                f"no audio channel {parsed['channel_index']} in "
                f"{parsed['source_id']}:s{parsed['stream_index']}",
                location=cid,
            ))
            continue
        if canonical in seen:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_SOURCE_DUPLICATE,
                f"channel {canonical!r} appears twice in the selection",
                location=cid,
            ))
        seen.add(canonical)
    return issues


def validate_mapping(plan: AudioPlan) -> list[AudioValidationIssue]:
    """校验映射: 引用有效性 -> 与 selection 一致 -> 结构 -> 无 Mixing。

    顺序即优先级。任何"引用了不存在的东西"或"映射了未选择的声道"都是
    **规划错误**, 必须先于结构性判断报出 (否则会出现"映射没选中的声道却
    报混音"这种误导性 reason)。稳态情况下, 一个输出声道恰好对应一个源
    声道 —— 因此 V8 的触发形态只有两种, 且都能明确定位:

        同一 output index 被**不同**源声道占用   (结构性 Mixing)
        选择集比映射集大 (N 源 -> 更少输出)      (等价于合成)

    两种都返回 `audio_mix_not_supported` (§17 V8)。

    检查项与 §17 一一对应:

        V1 audio_source_not_found    映射引用了不存在的来源
        V2 audio_stream_not_found    映射引用了不存在的流
        V3 audio_channel_not_found   映射引用了不存在的声道 / id 格式非法
        V5 audio_mapping_output_order  output index 非 0..N-1 连续且唯一,
                                      或同一源声道占了两个输出 (复制语义未定义)
        V6 audio_mapping_not_selected 映射了没有被 selection 选中的声道
        V7 (同上, duplicate mapping 归入 V5 的 output_order)
        V8 audio_mix_not_supported   N 源声道 -> 1 输出声道
    """
    issues: list[AudioValidationIssue] = []
    entries = _effective_mapping(plan)
    if not entries:
        if plan.selected_channels:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_MAPPING_INCOMPLETE,
                f"{len(plan.selected_channels)} channel(s) selected but the "
                "mapping is empty",
            ))
        return issues

    sources = _known_source_ids(plan)
    streams = _known_stream_ids(plan)
    channels = _known_channel_ids(plan)
    selected_canonical = {c.id for c in plan.selected_channel_objects()}

    # --- 第一遍: 引用有效性 + V6 (规划错误必须最先报) ---------------------
    resolved: list[tuple[int, str]] = []
    for entry in entries:
        index = _opt_int(entry.get("output_index")) or 0
        cid = str(entry.get("source_channel_id") or "")
        parsed = _parse_channel_id(cid)
        if parsed is None:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_CHANNEL_NOT_FOUND,
                f"malformed source_channel_id: {cid!r} "
                "(expected 'source:sN:cM')",
                location=cid,
            ))
            continue
        if parsed["source_id"] not in sources:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_SOURCE_NOT_FOUND,
                f"mapping refers to unknown source {parsed['source_id']!r}",
                location=cid,
            ))
            continue
        if (parsed["source_id"], parsed["stream_index"]) not in streams:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_STREAM_NOT_FOUND,
                f"mapping refers to unknown stream "
                f"{parsed['source_id']}:s{parsed['stream_index']}",
                location=cid,
            ))
            continue
        canonical = (
            f"{parsed['source_id']}:s{parsed['stream_index']}"
            f":c{parsed['channel_index']}"
        )
        if canonical not in channels:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_CHANNEL_NOT_FOUND,
                f"mapping refers to unknown channel {canonical!r}",
                location=cid,
            ))
            continue
        if canonical not in selected_canonical:
            # 声道确实存在, 但没被 selection 选中 -> 规划错误 (V6),
            # 必须先于任何结构性判断报出。
            issues.append(AudioValidationIssue(
                REASON_AUDIO_MAPPING_NOT_SELECTED,
                f"channel {canonical!r} is mapped but not selected",
                location=cid,
            ))
            continue
        resolved.append((index, canonical))
    if issues:
        return issues

    # --- 第二遍: 结构性检查 --------------------------------------------
    # V8: 一个 output index 被多个**不同**源声道占用 = "N 源 -> 1 输出"。
    fan_in: dict[int, set[str]] = {}
    for index, canonical in resolved:
        fan_in.setdefault(index, set()).add(canonical)
    for index in sorted(fan_in):
        feeds = fan_in[index]
        if len(feeds) > 1:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_MIX_NOT_SUPPORTED,
                f"output index {index} is fed by {len(feeds)} source channels "
                f"({sorted(feeds)}) — that is sample-level mixing, not a "
                "channel mapping; mixing is not implemented in v0.7.1 Phase 2",
            ))

    # V5: output index 必须连续且唯一
    indices = [index for index, _c in resolved]
    if sorted(indices) != list(range(len(resolved))):
        issues.append(AudioValidationIssue(
            REASON_AUDIO_MAPPING_OUTPUT_ORDER,
            f"output indices must be contiguous 0..{len(resolved) - 1}, "
            f"got {sorted(indices)}",
        ))

    # V7: 同一源声道占两个输出 (复制语义未定义 -> 拒绝)
    placements: dict[str, list[int]] = {}
    for index, canonical in resolved:
        placements.setdefault(canonical, []).append(index)
    for canonical, where in placements.items():
        if len(where) > 1:
            issues.append(AudioValidationIssue(
                REASON_AUDIO_MAPPING_OUTPUT_ORDER,
                f"channel {canonical!r} is mapped to {len(where)} output "
                f"channels {sorted(where)} (copy semantics are not defined)",
                location=canonical,
            ))

    # V8 (第二种形态): 选择集比输出集大 -> 把 N 个源压进更少的输出。
    if selected_canonical and len(selected_canonical) > len(resolved):
        issues.append(AudioValidationIssue(
            REASON_AUDIO_MIX_NOT_SUPPORTED,
            f"{len(selected_canonical)} channel(s) selected but only "
            f"{len(resolved)} output channel(s) mapped — collapsing N sources "
            "into fewer outputs requires sample-level mixing",
        ))
    if plan.mix_mode is not None:
        issues.append(AudioValidationIssue(
            REASON_AUDIO_MIX_NOT_SUPPORTED,
            f"mix_mode={plan.mix_mode!r} requests sample-level combination; "
            "mixing is not implemented in v0.7.1 Phase 2",
        ))
    return issues


def validate_plan(plan: AudioPlan) -> list[str]:
    """完整校验, 返回**稳定 reason 字符串**列表 (空 = 通过)。

    这是给调用方/report 用的简易接口; 需要细节时用
    `AudioMapSpec.errors` (含 detail 与 location)。
    """
    issues = validate_selection(plan) + validate_mapping(plan)
    out: list[str] = []
    for issue in issues:
        if issue.reason not in out:
            out.append(issue.reason)
    return out


def _raise_first(issues: Sequence[AudioValidationIssue]) -> None:
    if not issues:
        return
    first = issues[0]
    raise AudioValidationError(
        first.reason, first.detail, location=first.location
    )


# ---------------------------------------------------------------------------
# 输出单元编译 + 执行规格 (§9/§12/§13)
# ---------------------------------------------------------------------------


def _build_output_tracks(plan: AudioPlan) -> list[AudioOutputTrack]:
    """按有效映射把输出声道分组成 AudioOutputTrack (§9)。

    分组规则 (纯结构, 不含任何 DSP):

      * 连续输出段若来自**同一条源流**, 归入同一个输出单元;
      * 该段恰为该流完整且自然顺序 -> `STREAM_COPY` (整流保留);
      * 否则 -> `CHANNEL_FILTER` (取子集或重排)。

    本阶段不会产生 `MIXING` 单元: 一个输出声道只有一个源声道。
    """
    entries = _effective_mapping(plan)
    if not entries:
        return []
    by_id = {c.id: c for c in plan.all_channels()}

    groups: list[list[Mapping[str, Any]]] = []
    for entry in entries:
        key = (
            str(entry.get("source_id") or DEFAULT_SOURCE_ID),
            _opt_int(entry.get("stream_index")) or 0,
        )
        if groups:
            prev = groups[-1][-1]
            prev_key = (
                str(prev.get("source_id") or DEFAULT_SOURCE_ID),
                _opt_int(prev.get("stream_index")) or 0,
            )
            if prev_key == key:
                groups[-1].append(entry)
                continue
        groups.append([entry])

    tracks: list[AudioOutputTrack] = []
    for ordinal, group in enumerate(groups):
        source_id = str(group[0].get("source_id") or DEFAULT_SOURCE_ID)
        stream_index = _opt_int(group[0].get("stream_index")) or 0
        refs: list[AudioChannelRef] = []
        for entry in group:
            cid = str(entry.get("source_channel_id") or "")
            channel = by_id.get(cid)
            if channel is not None:
                refs.append(channel_ref(plan, channel))
            else:
                refs.append(AudioChannelRef.from_dict(entry))
        strategy = (
            AudioMapStrategy.STREAM_COPY
            if _group_is_full_stream(plan, group)
            else AudioMapStrategy.CHANNEL_FILTER
        )
        first = group[0]
        tracks.append(AudioOutputTrack(
            output_track_id=f"out{ordinal}",
            assignments=refs,
            strategy=strategy,
            sample_rate=_opt_int(first.get("sample_rate")) or (
                refs[0].sample_rate if refs else 0
            ),
            sample_format=(
                refs[0].sample_format if refs else "unknown"
            ),
            layout=_layout_for(plan, source_id, stream_index, group),
            metadata={
                "source_id": source_id,
                "stream_index": stream_index,
                "audio_position": _opt_int(first.get("audio_position")),
                "output_indices": [
                    _opt_int(e.get("output_index")) or 0 for e in group
                ],
                "source_channel_ids": [
                    str(e.get("source_channel_id") or "") for e in group
                ],
            },
        ))
    return tracks


def _group_is_full_stream(
    plan: AudioPlan, group: Sequence[Mapping[str, Any]],
) -> bool:
    """该输出段是否恰为"整条流的自然顺序全通道"。"""
    source_id = str(group[0].get("source_id") or DEFAULT_SOURCE_ID)
    stream_index = _opt_int(group[0].get("stream_index")) or 0
    _input, _pos, total = _stream_signature(plan, source_id, stream_index)
    if total <= 0:
        return False
    indices = [_opt_int(e.get("channel_index")) or 0 for e in group]
    return indices == list(range(total))


def _layout_for(
    plan: AudioPlan,
    source_id: str,
    stream_index: int,
    group: Sequence[Mapping[str, Any]],
) -> str:
    """输出单元的原生布局: 只在"整流保留"时可信, 取子集/重排后留空。

    取子集或重排后原布局语义已不成立 (例如 4.0 取出 2 个声道) —— 此时
    **不臆造**新布局名, 留空由后续阶段决定。
    """
    if not _group_is_full_stream(plan, group):
        return ""
    source = plan.source(source_id)
    stream = source.stream(stream_index) if source is not None else None
    if stream is None:
        return ""
    return stream.channel_layout


def _operations_for(
    plan: AudioPlan, entries: Sequence[Mapping[str, Any]],
) -> list[AudioMapOperation]:
    """输出段 -> 执行操作 (§13 情况 1/2)。

    整条流原样保留优先走 `STREAM_COPY` (等价于 `-map` + copy);
    只取流内部分声道或需要重排时走 `CHANNEL_FILTER`。
    """
    operations: list[AudioMapOperation] = []
    for track in _build_output_tracks(plan):
        group_indices = [int(i) for i in track.metadata["output_indices"]]
        source_id = str(track.metadata["source_id"])
        stream_index = int(track.metadata["stream_index"])
        input_index, audio_position, total = _stream_signature(
            plan, source_id, stream_index
        )
        operations.append(AudioMapOperation(
            strategy=track.strategy,
            output_indices=group_indices,
            source_id=source_id,
            stream_index=stream_index,
            audio_position=audio_position,
            input_index=input_index,
            channel_indices=[a.channel_index for a in track.assignments],
            channel_ids=[a.channel_id for a in track.assignments],
            stream_channel_count=total,
        ))
    return operations


def build_audio_map_spec(plan: AudioPlan) -> AudioMapSpec:
    """AudioPlan -> AudioMapSpec (dry-run 安全, 不执行任何东西)。

    校验失败时**不抛异常**, 而是返回一个 `executable=False` 且带
    `errors` 的 spec —— 这样调用方可以先 dry-run 看问题, 也便于报告落盘。
    需要"失败即抛"的语义时用 `require_audio_map_spec`。
    """
    issues = validate_selection(plan) + validate_mapping(plan)
    errors = [i.to_dict() for i in issues
              if i.severity is AudioValidationSeverity.ERROR]
    warnings = [i.detail for i in issues
                if i.severity is AudioValidationSeverity.WARNING]

    entries = _effective_mapping(plan)
    operations = _operations_for(plan, entries) if not errors else []
    full_copy = bool(not errors and entries and _is_full_stream_identity(
        plan, entries
    ))
    strategy = (
        AudioMapStrategy.MIXING
        if any(e.get("reason") == REASON_AUDIO_MIX_NOT_SUPPORTED
               for e in errors)
        else (
            AudioMapStrategy.STREAM_COPY if full_copy
            else AudioMapStrategy.CHANNEL_FILTER
        )
    )

    # 只保留**被选中/被映射**的来源 (未参与输出的来源不进 spec)。
    used_sources = {str(e.get("source_id") or DEFAULT_SOURCE_ID)
                    for e in entries}
    sources = [
        AudioSourceRef(
            source_id=s.source_id,
            source_type=s.source_type.value,
            path=s.path,
            display_name=s.display_name,
            input_index=s.input_index,
        )
        for s in plan.sources if s.source_id in used_sources
    ]
    if not sources:
        seen: list[AudioSourceRef] = []
        for entry in entries:
            sid = str(entry.get("source_id") or DEFAULT_SOURCE_ID)
            if any(s.source_id == sid for s in seen):
                continue
            source = plan.source(sid)
            seen.append(AudioSourceRef(
                source_id=sid,
                source_type=(
                    source.source_type.value if source is not None
                    else AudioSourceType.MEDIA.value
                ),
                path=source.path if source is not None else None,
                display_name=source.display_name if source is not None else None,
                input_index=(
                    source.input_index if source is not None else None
                ),
            ))
        sources = seen

    assignments: list[AudioChannelRef] = []
    by_id = {c.id: c for c in plan.all_channels()}
    for entry in entries:
        cid = str(entry.get("source_channel_id") or "")
        channel = by_id.get(cid)
        if channel is not None:
            assignments.append(channel_ref(plan, channel))
        else:
            assignments.append(AudioChannelRef.from_dict(entry))

    return AudioMapSpec(
        sources=sources,
        assignments=assignments,
        operations=operations,
        tracks=_build_output_tracks(plan) if not errors else [],
        strategy=strategy,
        preserve_original=plan.preserve_original,
        executable=not errors and bool(entries),
        full_stream_copy=full_copy,
        mapping_kind=plan.mapping_kind,
        model_version=plan.model_version,
        errors=errors,
        warnings=warnings,
    )


def require_audio_map_spec(plan: AudioPlan) -> AudioMapSpec:
    """同 `build_audio_map_spec`, 但校验失败时抛 `AudioValidationError`。"""
    spec = build_audio_map_spec(plan)
    if spec.errors:
        first = spec.errors[0]
        raise AudioValidationError(
            str(first.get("reason") or REASON_AUDIO_MAPPING_INCOMPLETE),
            str(first.get("detail") or "audio plan validation failed"),
            location=_opt_str(first.get("location")),
        )
    return spec


# ---------------------------------------------------------------------------
# 便捷入口 (与 Phase 1 的 build_plan 风格一致)
# ---------------------------------------------------------------------------


def build_map_spec(plan: AudioPlan) -> AudioMapSpec:
    """模块级入口: AudioPlan -> AudioMapSpec。"""
    return build_audio_map_spec(plan)


def select_all(plan: AudioPlan) -> AudioPlan:
    return AudioPlanner(plan).select_all()


def select_sources(plan: AudioPlan, *source_ids: str) -> AudioPlan:
    return AudioPlanner(plan).select_sources(*source_ids)


def select_tracks(plan: AudioPlan, *track_ids: str) -> AudioPlan:
    return AudioPlanner(plan).select_tracks(*track_ids)


def select_channels(plan: AudioPlan, *channel_ids: str) -> AudioPlan:
    return AudioPlanner(plan).select_channels(*channel_ids)


def exclude_channels(plan: AudioPlan, *channel_ids: str) -> AudioPlan:
    return AudioPlanner(plan).exclude_channels(*channel_ids)


def map_channels(plan: AudioPlan, *channel_ids: str) -> AudioPlan:
    return AudioPlanner(plan).map_channels(*channel_ids)


def map_channel(plan: AudioPlan, channel_id: str, output_index: int) -> AudioPlan:
    return AudioPlanner(plan).map_channel(channel_id, output_index)


def output_tracks(plan: AudioPlan) -> list[AudioOutputTrack]:
    return _build_output_tracks(plan)


def source_plan(
    sources: Iterable[Any],
    *,
    mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    select_all_channels: bool = True,
) -> AudioPlan:
    """便捷入口: 多个来源 -> 默认 AudioPlan (全来源全选 + 保留原始)。

    `sources` 接受 AudioSource / `(source_id, raw_streams)` /
    `{"source_id": ..., "streams": ...}` 三种形态 (与
    `core.audio_models.build_multi_source_plan` 同语义, 此处提供规划层入口,
    避免调用方为了建计划而 import 两个模块)。
    """
    builder = AudioSourceBuilder()
    for item in sources:
        if isinstance(item, AudioSource):
            builder.sources.append(item)
            continue
        if isinstance(item, Mapping):
            builder.add(
                str(item.get("source_id") or item.get("id") or ""),
                item.get("streams") or [],
                source_type=item.get("source_type", AudioSourceType.MEDIA),
                path=item.get("path"),
                display_name=item.get("display_name"),
                metadata=item.get("metadata"),
                timing=item.get("timing"),
                input_index=item.get("input_index"),
            )
            continue
        if isinstance(item, (tuple, list)) and len(item) == 2:
            builder.add(str(item[0]), item[1])
            continue
        raise TypeError(
            "audio source must be AudioSource / (source_id, streams) / "
            f"mapping, got {type(item).__name__}"
        )
    return builder.plan(mode, select_all=select_all_channels)
