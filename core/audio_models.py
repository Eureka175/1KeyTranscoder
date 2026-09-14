"""Audio track model (v0.7.1 Phase 1) — 统一音频中间模型.

本模块只描述**数据**，不含任何执行逻辑：不构造 ffmpeg 命令、不解码、
不做混音、不做重采样。分层职责（v0.7.1 Phase 1 §9）:

    core.probe / core.audio_probe   Probe 层: FFprobe JSON -> AudioStream
    core.audio_models (本模块)      Model 层: AudioStream/Channel/Track/Plan
    core.channel_sync               Sync 层 (既有, 未改动): 只产出报告 dict
    (Phase 2) AudioSelector/Mapper/Mixer/WaveExporter  Processing 层

层级关系::

    AudioStream  —— FFprobe 识别出的原始音频流 (含未解析的原始 dict)
        └── AudioChannel —— 流内单个声道; 亦可是"独立 mono 流"的声道 0
                └── AudioSyncResult —— 该声道的同步状态/结果

    AudioTrack   —— 真正参与输出决策的基本对象 (stream/channel 的选取结果)
    AudioPlan    —— 本次任务最终准备如何处理音频 (声明式, 不执行)

四个概念严格区分, 不得混用:

    stream      容器里的音频流 (ffprobe index)
    channel     流内声道的物理位置 (0-based)
    track       被选中的 (stream, channel 集合) —— 输出决策的基本对象
    output track 输出容器里的音轨 —— 属 Phase 2, 本阶段不表达

必备不变量 (v0.7.1 Phase 1 §7):
    同步处理**不得破坏** AudioStream / AudioChannel 身份 —— 任何 track
    经同步后仍能追溯回原始 (stream_index, channel_index)。

依赖: 仅 Python 标准库。`core.audio_models` **不导入项目内任何模块**
(与 core/models.py 的依赖规则一致), 因此可被任意层安全复用。
所有模型均可 `to_dict()` / `from_dict()` 往返, JSON-compatible,
不依赖 object repr, enum 均有稳定字符串表示。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "AUDIO_MODEL_VERSION",
    "AudioChannel",
    "AudioPlan",
    "AudioRole",
    "AudioSampleFormat",
    "AudioStream",
    "AudioSyncResult",
    "AudioTrack",
    "AudioTrackBuilder",
    "ChannelSyncReport",
    "SyncStatus",
    "TrackBuildMode",
    "apply_channel_sync_report",
    "build_audio_streams",
    "build_plan",
    "coerce_audio_stream",
    "container_stream_index",
    "normalize_channel_layout",
]

# 模型版本: 序列化结构变更时递增 (供 Collector / 日志消费者判断 schema)。
AUDIO_MODEL_VERSION = 1


# ---------------------------------------------------------------------------
# 稳定字符串枚举
# ---------------------------------------------------------------------------


class _StrEnum(str, Enum):
    """str 子类枚举: `str(member)` 即稳定字符串, JSON 直接可序列化。"""

    def __str__(self) -> str:          # pragma: no cover - 平凡
        return str(self.value)

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> Any:
        """任意输入 -> 枚举成员; 未知值回退 default (不抛异常)。

        未知值**不得**导致崩溃 (future schema / 手写 JSON 都合法):
        调用方按需保留原文, 模型层一律回退到安全默认。
        """
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return default
        text = str(value).strip().lower()
        for member in cls:
            if member.value == text or member.name.lower() == text:
                return member
        return default


class AudioSampleFormat(_StrEnum):
    """采样格式标签。

    取 **ffprobe/ffmpeg 原样名字** (s32/s16/s24/flt/dbl/u8/u16/u24/u32),
    不发明第二套命名 —— 未知/缺失一律 UNKNOWN, 绝不猜测位深。
    """

    UNKNOWN = "unknown"
    U8 = "u8"
    S16 = "s16"
    S24 = "s24"
    S32 = "s32"
    FLT = "flt"
    DBL = "dbl"
    U16 = "u16"
    U24 = "u24"
    U32 = "u32"


class AudioRole(_StrEnum):
    """声道/音轨角色 —— **可扩展枚举, 本阶段不做任何自动推断**。

    §5 明确要求: 不得根据猜测给真实素材赋予角色。当前项目没有任何
    可靠的 role 判据 (无线麦 CH1/CH2 只是 channel_sync 的锚点优先级,
    不是角色), 因此一切来自探测器的事实一律 UNKNOWN。角色只能由
    上层显式设置 (Phase 2 的选择器/用户配置)。
    """

    UNKNOWN = "unknown"
    CAMERA_LEFT = "camera_left"
    CAMERA_RIGHT = "camera_right"
    EXTERNAL_MIC = "external_mic"
    REFERENCE = "reference"
    OTHER = "other"


class SyncStatus(_StrEnum):
    """声道同步状态 (v0.7.1 Phase 1 §7)。

    语义映射到既有 core/channel_sync.py 的逐轨决策, 不改动算法:

    ===================  ==============================================
    本枚举                channel_sync 报告来源
    ===================  ==============================================
    NOT_PROCESSED        尚未运行同步 (无报告 / 无该行)
    NOT_APPLICABLE       文件级 not_eligible / tool_missing
    ALREADY_ALIGNED      decision=already_aligned 或已对齐的锚点轨
    SUCCESS              decision=fixed 且 delay 可用
    LOW_CONFIDENCE       reason ∈ low_confidence / insufficient_frames
    NON_CONSTANT         reason=non_constant
    OUT_OF_RANGE         reason=out_of_range
    RECHECK_FAILED       reason=recheck_residual
    FAILED               其余轨道级失败 (解码失败 / silent_track /
                         non_finite / no_valid_anchor / 未知 reason)
    ===================  ==============================================
    """

    NOT_PROCESSED = "not_processed"
    NOT_APPLICABLE = "not_applicable"
    ALREADY_ALIGNED = "already_aligned"
    SUCCESS = "success"
    LOW_CONFIDENCE = "low_confidence"
    NON_CONSTANT = "non_constant"
    OUT_OF_RANGE = "out_of_range"
    RECHECK_FAILED = "recheck_failed"
    FAILED = "failed"


class TrackBuildMode(_StrEnum):
    """stream -> track 的映射模式 (§5/§6/§10)。"""

    # 每条流一个 track, channels = 该流全部声道 (4CH 流 -> 1 track / 4 channel)
    PER_STREAM = "per_stream"
    # 每条流的每个声道一个 track (4CH 流 -> 4 个 track, 每个 1 channel)
    PER_CHANNEL = "per_channel"


# ---------------------------------------------------------------------------
# 序列化小工具 (JSON-compatible; 不含任何不可序列化对象)
# ---------------------------------------------------------------------------


def _clean(obj: Any) -> Any:
    """递归剔除 None 与空容器, 让 to_dict() 输出紧凑且字段明确。"""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            cleaned = _clean(value)
            if cleaned is None:
                continue
            if isinstance(cleaned, (dict, list)) and not cleaned:
                continue
            out[str(key)] = cleaned
        return out
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    return obj


def _opt_float(value: Any) -> float | None:
    """任意输入 -> float | None (NaN/inf -> None; 坏值不抛异常)。"""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _int_or(value: Any, default: int = 0) -> int:
    out = _opt_int(value)
    return default if out is None else out


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _fmt_sec(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------------------
# 声道布局
# ---------------------------------------------------------------------------

# ffprobe 缺失 channel_layout 时的退化布局名 (按声道数); 仅作**名字**用,
# 不改变声道语义 —— 未知布局一律 positional 命名。
_COUNT_LAYOUT_NAMES: dict[int, str] = {1: "mono", 2: "stereo"}

# ffmpeg 标准布局 -> 声道名 (与 ffmpeg channel_layout 表一致)。
_LAYOUT_CHANNELS: dict[str, tuple[str, ...]] = {
    "mono": ("FC",),
    "stereo": ("FL", "FR"),
    "2.1": ("FL", "FR", "LFE"),
    "3.0": ("FL", "FR", "FC"),
    "3.0(back)": ("FL", "FR", "BC"),
    "3.1": ("FL", "FR", "FC", "LFE"),
    "4.0": ("FL", "FR", "FC", "BC"),
    "quad": ("FL", "FR", "BL", "BR"),
    "quad(side)": ("FL", "FR", "SL", "SR"),
    "4.1": ("FL", "FR", "FC", "LFE", "BC"),
    "5.0": ("FL", "FR", "FC", "BL", "BR"),
    "5.0(side)": ("FL", "FR", "FC", "SL", "SR"),
    "5.1": ("FL", "FR", "FC", "LFE", "BL", "BR"),
    "5.1(side)": ("FL", "FR", "FC", "LFE", "SL", "SR"),
    "6.0": ("FL", "FR", "FC", "BC", "SL", "SR"),
    "6.0(front)": ("FL", "FR", "FLC", "FRC", "FL", "FR"),
    "6.1": ("FL", "FR", "FC", "LFE", "BC", "SL", "SR"),
    "6.1(back)": ("FL", "FR", "FC", "LFE", "BL", "BR", "BC"),
    "6.1(front)": ("FL", "FR", "LFE", "BC", "SL", "SR", "FC"),
    "7.0": ("FL", "FR", "FC", "BL", "BR", "SL", "SR"),
    "7.0(front)": ("FL", "FR", "FC", "FLC", "FRC", "SL", "SR"),
    "7.1": ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"),
    "7.1(wide)": ("FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC"),
    "7.1(wide-side)": ("FL", "FR", "FC", "LFE", "FLC", "FRC", "SL", "SR"),
    "7.1(top)": ("FL", "FR", "FC", "LFE", "BL", "BR", "TFL", "TFR"),
    "hexadecagonal": tuple(f"C{i + 1}" for i in range(16)),
    "downmix": ("DL", "DR"),
    "22.2": (
        "FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC",
        "BC", "LFE2", "SiL", "SiR", "TpFL", "TpFR", "TpFC", "TpC",
        "TpBL", "TpBR", "TpSiL", "TpSiR", "TpBC", "BtFC", "BtFL", "BtFR",
    ),
    "ambisonic": ("FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC"),
}


def normalize_channel_layout(value: Any) -> str:
    """ffprobe 的 channel_layout -> 规范化小写字符串; 缺失/未知返回 ""。

    纯规范化, **不猜测**: "4.0" 保持 "4.0", "" 保持 "" (调用方据此走
    positional 命名)。未知字符串原样小写返回 (允许模型存在未知 layout)。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() in ("N/A", "UNKNOWN"):
        return ""
    return text.lower()


def _layout_channel_names(layout: str) -> tuple[str, ...] | None:
    """布局 -> 标准声道名元组; 未知布局返回 None。"""
    if not layout:
        return None
    return _LAYOUT_CHANNELS.get(layout)


def _positional_names(count: int) -> tuple[str, ...]:
    """未知/不可靠布局的稳定声道命名: C0/C1/C2/… (不冒充标准声道语义)。"""
    return tuple(f"C{i}" for i in range(max(0, count)))


def _resolve_channel_names(
    layout: str, channel_count: int,
) -> tuple[tuple[str, ...], str, bool]:
    """-> (声道名元组, 有效性说明, 是否可靠)。

    判定顺序:
      1. channel_count == 1 -> "mono" 的名字可靠 (单声道是事实, 与
         layout 是否被写出无关; ffprobe 对 mono 常省略 channel_layout)
      2. 有 layout 且声道数匹配标准表 -> 标准名 (可靠)
      3. 有 layout 但表内未知 -> positional (不可靠)
      4. 有 layout 且表内已知但声道数不符 -> positional (不可靠, 防错位)
      5. 无 layout -> 2ch 用 stereo 的名字 (声道数即事实);
         其余 -> positional (不可靠)
    """
    if channel_count == 1:
        return _LAYOUT_CHANNELS["mono"], "channel_count", True
    named = _layout_channel_names(layout)
    if layout:
        if named is None:
            return _positional_names(channel_count), "layout_unknown", False
        if len(named) != channel_count:
            return _positional_names(channel_count), "layout_count_mismatch", False
        return named, "layout", True
    if channel_count in _COUNT_LAYOUT_NAMES:
        return _LAYOUT_CHANNELS[_COUNT_LAYOUT_NAMES[channel_count]], "channel_count", True
    return _positional_names(channel_count), "layout_missing", False


# ---------------------------------------------------------------------------
# 同步结果
# ---------------------------------------------------------------------------


@dataclass
class AudioSyncResult:
    """单个 AudioTrack 的同步状态与结果 (§7)。

    由 core.channel_sync 的**报告 dict** 填充 (字段名与 §十三 示例一致),
    本类不含算法: 不测量、不修正、不重采样。
    """

    status: SyncStatus = SyncStatus.NOT_PROCESSED
    offset_samples: float | None = None
    offset_ms: float | None = None
    quality: float | None = None          # confidence [0, 1]
    anchor: str | None = None             # 锚点标识, 如 "stream:2" / "a2"
    reason: str | None = None             # 轨道级 decision / reason 原文
    drift_ppm: float | None = None
    constant: bool | None = None
    polarity: int | None = None
    usable_frames: int | None = None
    rms_dbfs: float | None = None
    algo_version: str | None = None
    # 同步**应用方式**: 本阶段模型只记录"结果从哪来", 不执行修正。
    # 取值如 "channel_sync_report" / None (未处理)。
    source: str | None = None
    warnings: list[str] = field(default_factory=list)

    # -- 便捷判定 ---------------------------------------------------------

    @property
    def processed(self) -> bool:
        """是否已有测量结论 (不论成功与否)。"""
        return self.status is not SyncStatus.NOT_PROCESSED

    @property
    def applied(self) -> bool:
        """是否发生了实际修正 (offset 为整数样本移位量)。"""
        return self.status is SyncStatus.SUCCESS and self.offset_samples is not None

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status.value}
        if self.offset_samples is not None:
            data["offset_samples"] = self.offset_samples
        if self.offset_ms is not None:
            data["offset_ms"] = self.offset_ms
        if self.quality is not None:
            data["quality"] = self.quality
        if self.anchor is not None:
            data["anchor"] = self.anchor
        if self.reason is not None:
            data["reason"] = self.reason
        if self.drift_ppm is not None:
            data["drift_ppm"] = self.drift_ppm
        if self.constant is not None:
            data["constant"] = bool(self.constant)
        if self.polarity is not None:
            data["polarity"] = int(self.polarity)
        if self.usable_frames is not None:
            data["usable_frames"] = int(self.usable_frames)
        if self.rms_dbfs is not None:
            data["rms_dbfs"] = self.rms_dbfs
        if self.algo_version is not None:
            data["algo_version"] = self.algo_version
        if self.source is not None:
            data["source"] = self.source
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "AudioSyncResult":
        if not data:
            return cls()
        raw_warnings = data.get("warnings") or []
        return cls(
            status=SyncStatus.coerce(
                data.get("status"), SyncStatus.NOT_PROCESSED
            ),
            offset_samples=_opt_float(data.get("offset_samples")),
            offset_ms=_opt_float(data.get("offset_ms")),
            quality=_opt_float(data.get("quality")),
            anchor=_opt_str(data.get("anchor")),
            reason=_opt_str(data.get("reason")),
            drift_ppm=_opt_float(data.get("drift_ppm")),
            constant=(
                bool(data["constant"]) if data.get("constant") is not None
                else None
            ),
            polarity=_opt_int(data.get("polarity")),
            usable_frames=_opt_int(data.get("usable_frames")),
            rms_dbfs=_opt_float(data.get("rms_dbfs")),
            algo_version=_opt_str(data.get("algo_version")),
            source=_opt_str(data.get("source")),
            warnings=[str(w) for w in raw_warnings],
        )


# ---------------------------------------------------------------------------
# AudioChannel
# ---------------------------------------------------------------------------


@dataclass
class AudioChannel:
    """流内单个声道的身份 + 采样事实 + 同步状态。

    §5 的统一抽象: "1 个 4-channel 流" 与 "4 个 mono 流" 在上层都是
    AudioChannel(stream_index, channel_index) 的集合 —— 前者是
    (S, 0..3), 后者是 (S0,0) (S1,0) (S2,0) (S3,0)。身份永不被同步改写。
    """

    stream_index: int
    channel_index: int
    channel_count: int = 1                 # 所属流的声道总数 (身份的一部分)
    source_channel_name: str | None = None  # "FL" / "FC"; 未知布局 -> None
    layout: str = ""
    layout_verified: bool = False          # 声道名是否来自可信布局
    sample_rate: int = 0
    sample_format: AudioSampleFormat = AudioSampleFormat.UNKNOWN
    enabled: bool = True
    role: AudioRole = AudioRole.UNKNOWN
    # §5: role 不做自动推断。任何显式赋予都必须写明理由, 便于审计。
    role_reason: str | None = None
    sync: AudioSyncResult = field(default_factory=AudioSyncResult)

    @property
    def id(self) -> str:
        """稳定标识: "s{stream}c{channel}" —— 可追溯原始 stream/channel。"""
        return f"s{self.stream_index}c{self.channel_index}"

    @property
    def is_mono_stream(self) -> bool:
        return self.channel_count == 1

    def set_role(self, role: AudioRole, reason: str) -> None:
        """显式赋角色 (唯一入口); reason 必填以杜绝无据推断。"""
        self.role = AudioRole.coerce(role, AudioRole.UNKNOWN) or AudioRole.UNKNOWN
        self.role_reason = str(reason)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stream_index": int(self.stream_index),
            "channel_index": int(self.channel_index),
            "sample_rate": int(self.sample_rate),
            "sample_format": self.sample_format.value,
            "enabled": bool(self.enabled),
        }
        if self.channel_count != 1:
            data["channel_count"] = int(self.channel_count)
        if self.source_channel_name is not None:
            data["source_channel_name"] = self.source_channel_name
        if self.layout:
            data["layout"] = self.layout
        if self.layout_verified:
            data["layout_verified"] = True
        if self.role is not AudioRole.UNKNOWN:
            data["role"] = self.role.value
        if self.role_reason is not None:
            data["role_reason"] = self.role_reason
        if self.sync.status is not SyncStatus.NOT_PROCESSED:
            data["sync"] = self.sync.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioChannel":
        return cls(
            stream_index=_int_or(data.get("stream_index")),
            channel_index=_int_or(data.get("channel_index")),
            channel_count=_int_or(data.get("channel_count"), 1) or 1,
            source_channel_name=_opt_str(data.get("source_channel_name")),
            layout=normalize_channel_layout(data.get("layout")),
            layout_verified=bool(data.get("layout_verified", False)),
            sample_rate=_int_or(data.get("sample_rate")),
            sample_format=AudioSampleFormat.coerce(
                data.get("sample_format"), AudioSampleFormat.UNKNOWN
            ),
            enabled=bool(data.get("enabled", True)),
            role=AudioRole.coerce(data.get("role"), AudioRole.UNKNOWN),
            role_reason=_opt_str(data.get("role_reason")),
            sync=AudioSyncResult.from_dict(data.get("sync")),
        )


# ---------------------------------------------------------------------------
# AudioStream
# ---------------------------------------------------------------------------


@dataclass
class AudioStream:
    """FFprobe 识别出的原始音频流 (§4.1)。

    **不假设任何字段存在**: sample_rate / sample_format / channel_count /
    duration 都可能缺失 (0 / UNKNOWN); channel_layout 可能为空; metadata
    可能为空; bit_rate 与 duration 常不可靠 (报告侧标注, 不参与计算)。
    `raw` 保留 ffprobe 原始 dict, 供下游与既有代码 (channel_sync 等) 复用。
    """

    stream_index: int
    codec_name: str = ""
    codec_long_name: str = ""
    sample_rate: int = 0
    sample_format: AudioSampleFormat = AudioSampleFormat.UNKNOWN
    channel_count: int = 0
    channel_layout: str = ""
    duration_sec: float | None = None
    bit_rate: int | None = None
    language: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    disposition: dict[str, int] = field(default_factory=dict)
    codec_tag_string: str = ""
    start_time_sec: float | None = None
    # 该流在"音频流序列"中的 0-based 位置 —— 与 ffmpeg `-map 0:a:N` 以及
    # core.channel_sync 报告里的 `stream` 字段同义 (容器 index ≠ 音频序号)。
    audio_position: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # -- 派生事实 ---------------------------------------------------------

    @property
    def layout_verified(self) -> bool:
        """声道名/布局是否可信 (来自标准布局表或明确的声道数事实)。"""
        return _resolve_channel_names(
            self.channel_layout, self.channel_count
        )[2]

    @property
    def channel_names(self) -> tuple[str, ...]:
        """逐声道名; 未知布局退化为 C0/C1/… (不丢失流)。"""
        return _resolve_channel_names(
            self.channel_layout, self.channel_count
        )[0]

    @property
    def layout_source(self) -> str:
        """布局判定来源: layout / channel_count / layout_missing /
        layout_unknown / layout_count_mismatch。"""
        return _resolve_channel_names(
            self.channel_layout, self.channel_count
        )[1]

    @property
    def is_default(self) -> bool:
        return bool(self.disposition.get("default", 0))

    @property
    def is_mono(self) -> bool:
        return self.channel_count == 1

    @property
    def duration(self) -> str | None:
        """秒 -> 字符串 (与既有 probe 输出风格一致, JSON 友好)。"""
        return _fmt_sec(self.duration_sec)

    # -- 构造 -------------------------------------------------------------

    def channels(self) -> list[AudioChannel]:
        """展开为 AudioChannel 列表 (声道数为 0 时返回空列表)。"""
        names = self.channel_names
        return [
            AudioChannel(
                stream_index=self.stream_index,
                channel_index=index,
                channel_count=max(1, self.channel_count),
                source_channel_name=(
                    names[index] if index < len(names) else None
                ),
                layout=self.channel_layout,
                layout_verified=self.layout_verified,
                sample_rate=self.sample_rate,
                sample_format=self.sample_format,
            )
            for index in range(max(0, self.channel_count))
        ]

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stream_index": int(self.stream_index),
            "codec_name": self.codec_name,
            "codec_long_name": self.codec_long_name,
            "sample_rate": int(self.sample_rate),
            "sample_format": self.sample_format.value,
            "channel_count": int(self.channel_count),
            "channel_layout": self.channel_layout,
            "layout_source": self.layout_source,
        }
        if self.duration_sec is not None:
            data["duration_sec"] = self.duration_sec
        if self.bit_rate is not None:
            data["bit_rate"] = int(self.bit_rate)
        if self.language is not None:
            data["language"] = self.language
        if self.title is not None:
            data["title"] = self.title
        if self.metadata:
            data["metadata"] = copy.deepcopy(self.metadata)
        if self.disposition:
            data["disposition"] = {
                str(k): int(v) for k, v in self.disposition.items()
            }
        if self.codec_tag_string:
            data["codec_tag_string"] = self.codec_tag_string
        if self.start_time_sec is not None:
            data["start_time_sec"] = self.start_time_sec
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.raw:
            data["raw"] = copy.deepcopy(self.raw)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioStream":
        raw_meta = data.get("metadata") or {}
        raw_disp = data.get("disposition") or {}
        return cls(
            stream_index=_int_or(data.get("stream_index")),
            codec_name=str(data.get("codec_name") or ""),
            codec_long_name=str(data.get("codec_long_name") or ""),
            sample_rate=_int_or(data.get("sample_rate")),
            sample_format=AudioSampleFormat.coerce(
                data.get("sample_format"), AudioSampleFormat.UNKNOWN
            ),
            channel_count=_int_or(data.get("channel_count")),
            channel_layout=normalize_channel_layout(data.get("channel_layout")),
            duration_sec=_opt_float(data.get("duration_sec")),
            bit_rate=_opt_int(data.get("bit_rate")),
            language=_opt_str(data.get("language")),
            title=_opt_str(data.get("title")),
            metadata=dict(raw_meta),
            disposition={str(k): _int_or(v) for k, v in raw_disp.items()},
            codec_tag_string=str(data.get("codec_tag_string") or ""),
            start_time_sec=_opt_float(data.get("start_time_sec")),
            audio_position=_opt_int(data.get("audio_position")),
            raw=dict(data.get("raw") or {}),
        )


# ---------------------------------------------------------------------------
# AudioTrack
# ---------------------------------------------------------------------------


@dataclass
class AudioTrack:
    """真正参与输出决策的基本对象 (§6)。

    track 是 **(stream, channel 集合)** 的选取结果, 不是流本身:

        4CH 流        -> AudioTrack(channel_indices=[0, 1, 2, 3])
        mono 流       -> AudioTrack(channel_indices=[0])
        4 条 mono 流  -> 4 个 AudioTrack, 各自 channel_indices=[0]

    `channels` 保留逐声道的 AudioChannel (含同步状态), 因此"4CH 流不能
    错误塌缩成 1 个不可再分的对象"这一约束由结构保证: 选择单通道只需
    按 channel_index 取子集。
    """

    track_id: str
    source_stream_index: int
    channels: list[AudioChannel] = field(default_factory=list)
    sample_rate: int = 0
    sample_format: AudioSampleFormat = AudioSampleFormat.UNKNOWN
    layout: str = ""
    layout_verified: bool = False
    enabled: bool = True
    role: AudioRole = AudioRole.UNKNOWN
    role_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sync_status: SyncStatus = SyncStatus.NOT_PROCESSED
    sync_offset_samples: float | None = None
    sync_offset_ms: float | None = None

    # -- 身份 / 事实 ------------------------------------------------------

    @property
    def channel_indices(self) -> list[int]:
        """被选中的声道位置 (0-based), 稳定升序。"""
        return [ch.channel_index for ch in self.channels]

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def source_ids(self) -> list[str]:
        """逐声道原始身份 "s{stream}c{channel}" —— 同步后仍可追溯 (§7)。"""
        return [ch.id for ch in self.channels]

    @property
    def channel_names(self) -> list[str | None]:
        return [ch.source_channel_name for ch in self.channels]

    @property
    def codec_name(self) -> str:
        value = self.metadata.get("codec_name")
        return str(value) if value else ""

    @property
    def language(self) -> str | None:
        value = self.metadata.get("language")
        return str(value) if value else None

    @property
    def title(self) -> str | None:
        value = self.metadata.get("title")
        return str(value) if value else None

    @property
    def is_mono(self) -> bool:
        return self.channel_count == 1

    # -- 同步代理 (真相在 AudioChannel.sync) ------------------------------
    #
    # Track 级字段是**派生缓存**: 任何一次 refresh_sync() 都从声道重算,
    # 因此不存在"track 说 success 而声道说 failed"的双真相。

    @property
    def sync(self) -> AudioSyncResult:
        """代表声道的同步结果 (取第一个声道的; 空声道 -> NOT_PROCESSED)。"""
        if not self.channels:
            return AudioSyncResult(status=self.sync_status)
        return self.channels[0].sync

    def refresh_sync(self) -> "AudioTrack":
        """按"最保守"规则汇总声道同步状态到 track 级字段。

        track 是由多个声道组成的整体: 只有当**所有**声道都成功修正时,
        track 才是 SUCCESS (任一未处理 -> NOT_PROCESSED)。偏移取首个
        已成功声道的值 (同轨同流同移位, 这是 channel_sync 的实际语义)。
        """
        statuses = [ch.sync.status for ch in self.channels]
        if not statuses:
            self.sync_status = SyncStatus.NOT_PROCESSED
            self.sync_offset_samples = None
            self.sync_offset_ms = None
            return self
        if all(s is SyncStatus.NOT_PROCESSED for s in statuses):
            self.sync_status = SyncStatus.NOT_PROCESSED
            self.sync_offset_samples = None
            self.sync_offset_ms = None
            return self
        if any(s is SyncStatus.NOT_PROCESSED for s in statuses):
            self.sync_status = SyncStatus.NOT_PROCESSED
        elif all(s is SyncStatus.SUCCESS for s in statuses):
            self.sync_status = SyncStatus.SUCCESS
        else:
            # 混合: 取第一个非成功状态作为代表 (保守)
            self.sync_status = next(
                s for s in statuses if s is not SyncStatus.SUCCESS
            )
        done = next(
            (ch.sync for ch in self.channels if ch.sync.applied),
            self.channels[0].sync,
        )
        self.sync_offset_samples = done.offset_samples
        self.sync_offset_ms = done.offset_ms
        return self

    def set_sync(self, result: AudioSyncResult) -> "AudioTrack":
        """把同一个同步结果写入本 track 的**全部**声道 (再汇总到 track 级)。

        这是"同步不改身份"的实现: 只写 sync 字段, stream_index /
        channel_index 原样不动。
        """
        for channel in self.channels:
            channel.sync = replace(result, warnings=list(result.warnings))
        return self.refresh_sync()

    # -- 选择器 (Phase 2 的输入侧最小能力, 不实现任何 DSP) ----------------

    def select_channels(self, indices: Sequence[int]) -> "AudioTrack":
        """按声道位置取子集 (4CH -> 单通道), 身份与同步状态原样保留。"""
        wanted = {int(i) for i in indices}
        picked = [ch for ch in self.channels if ch.channel_index in wanted]
        out = replace(
            self,
            channels=picked,
            metadata=copy.deepcopy(self.metadata),
        )
        return out.refresh_sync()

    def without_channels(self, indices: Sequence[int]) -> "AudioTrack":
        """按声道位置剔除 (enabled=False 的另一种表达: 不参与输出)。"""
        drop = {int(i) for i in indices}
        return self.select_channels(
            [ch.channel_index for ch in self.channels
             if ch.channel_index not in drop]
        )

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "track_id": self.track_id,
            "source_stream_index": int(self.source_stream_index),
            "channel_indices": self.channel_indices,
            "channel_count": self.channel_count,
            "sample_rate": int(self.sample_rate),
            "sample_format": self.sample_format.value,
            "layout": self.layout,
            "enabled": bool(self.enabled),
            "role": self.role.value,
            "sync_status": self.sync_status.value,
        }
        names = self.channel_names
        if any(name is not None for name in names):
            data["channel_names"] = names
        if self.layout_verified:
            data["layout_verified"] = True
        if self.role_reason is not None:
            data["role_reason"] = self.role_reason
        if self.sync_offset_samples is not None:
            data["sync_offset_samples"] = self.sync_offset_samples
        if self.sync_offset_ms is not None:
            data["sync_offset_ms"] = self.sync_offset_ms
        if self.metadata:
            data["metadata"] = copy.deepcopy(self.metadata)
        data["channels"] = [ch.to_dict() for ch in self.channels]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioTrack":
        raw_channels = data.get("channels") or []
        channels = [
            AudioChannel.from_dict(ch)
            for ch in raw_channels
            if isinstance(ch, Mapping)
        ]
        names = data.get("channel_names") or []
        # channel_names 存在但通道级名字缺失时回填 (旧 schema 兼容)
        if names and channels:
            for index, channel in enumerate(channels):
                if (
                    channel.source_channel_name is None
                    and index < len(names)
                    and names[index] is not None
                ):
                    channel.source_channel_name = str(names[index])
        meta = data.get("metadata") or {}
        track = cls(
            track_id=str(data.get("track_id") or ""),
            source_stream_index=_int_or(data.get("source_stream_index")),
            channels=channels,
            sample_rate=_int_or(data.get("sample_rate")),
            sample_format=AudioSampleFormat.coerce(
                data.get("sample_format"), AudioSampleFormat.UNKNOWN
            ),
            layout=normalize_channel_layout(data.get("layout")),
            layout_verified=bool(data.get("layout_verified", False)),
            enabled=bool(data.get("enabled", True)),
            role=AudioRole.coerce(data.get("role"), AudioRole.UNKNOWN),
            role_reason=_opt_str(data.get("role_reason")),
            metadata=dict(meta),
            sync_status=SyncStatus.coerce(
                data.get("sync_status"), SyncStatus.NOT_PROCESSED
            ),
            sync_offset_samples=_opt_float(data.get("sync_offset_samples")),
            sync_offset_ms=_opt_float(data.get("sync_offset_ms")),
        )
        if not track.track_id:
            track.track_id = _default_track_id(
                track.source_stream_index, track.channel_indices
            )
        return track


def _default_track_id(
    stream_index: int, channel_indices: Sequence[int],
) -> str:
    """"a{stream}" 或 "a{stream}c{i}-j" (稳定、可读、可追溯原始流)。"""
    if not channel_indices:
        return f"a{stream_index}"
    return f"a{stream_index}c" + "-".join(
        str(i) for i in channel_indices
    )


# ---------------------------------------------------------------------------
# AudioPlan
# ---------------------------------------------------------------------------


@dataclass
class AudioPlan:
    """本次任务最终准备如何处理音频 (§8) —— **声明式描述, 不执行**。

    它是"数据模型"与"执行逻辑"的分界线: AudioTrack -> AudioPlan ->
    (Phase 2) AudioProcessor。本阶段不含任何 ffmpeg 命令构造。
    """

    input_tracks: list[AudioTrack] = field(default_factory=list)
    selected_tracks: list[str] = field(default_factory=list)
    preserve_original: bool = True
    output_sample_rate: int | None = None
    output_sample_format: AudioSampleFormat | None = None
    # -- 以下为 Phase 2 预留: 本阶段只保存取值, 不解释、不执行 --
    mix_mode: str | None = None
    channel_map: list[dict[str, Any]] = field(default_factory=list)
    wav_outputs: list[dict[str, Any]] = field(default_factory=list)
    model_version: int = AUDIO_MODEL_VERSION

    # -- 查询 -------------------------------------------------------------

    def track(self, track_id: str) -> AudioTrack | None:
        return next(
            (t for t in self.input_tracks if t.track_id == track_id), None
        )

    def selected(self) -> list[AudioTrack]:
        ids = set(self.selected_tracks)
        return [t for t in self.input_tracks if t.track_id in ids]

    @property
    def output_channel_count(self) -> int:
        return sum(t.channel_count for t in self.selected())

    @property
    def is_default(self) -> bool:
        """默认计划 = 全选 + 保留原始 (即"默认生产路径不变"的模型表达)。"""
        return (
            self.preserve_original
            and self.selected_tracks == [t.track_id for t in self.input_tracks]
            and self.mix_mode is None
            and not self.channel_map
            and not self.wav_outputs
            and self.output_sample_rate is None
            and self.output_sample_format is None
        )

    # -- 构造 -------------------------------------------------------------

    @classmethod
    def from_tracks(
        cls,
        tracks: Iterable[AudioTrack],
        *,
        select_all: bool = True,
        **kwargs: Any,
    ) -> "AudioPlan":
        """由 track 列表建计划; 默认全选 + 保留原始。"""
        items = list(tracks)
        plan = cls(
            input_tracks=items,
            selected_tracks=[t.track_id for t in items] if select_all else [],
            **kwargs,
        )
        return plan

    def select(self, track_ids: Iterable[str]) -> "AudioPlan":
        """设置选择集 (只接受已知 track_id, 静默忽略未知 id)。"""
        known = {t.track_id for t in self.input_tracks}
        self.selected_tracks = [i for i in track_ids if i in known]
        return self

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model_version": int(self.model_version),
            "preserve_original": bool(self.preserve_original),
            "selected_tracks": list(self.selected_tracks),
            "input_tracks": [t.to_dict() for t in self.input_tracks],
        }
        if self.output_sample_rate is not None:
            data["output_sample_rate"] = int(self.output_sample_rate)
        if self.output_sample_format is not None:
            data["output_sample_format"] = self.output_sample_format.value
        if self.mix_mode is not None:
            data["mix_mode"] = self.mix_mode
        if self.channel_map:
            data["channel_map"] = copy.deepcopy(self.channel_map)
        if self.wav_outputs:
            data["wav_outputs"] = copy.deepcopy(self.wav_outputs)
        return _clean(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioPlan":
        raw_out_format = data.get("output_sample_format")
        return cls(
            input_tracks=[
                AudioTrack.from_dict(t)
                for t in (data.get("input_tracks") or [])
                if isinstance(t, Mapping)
            ],
            selected_tracks=[str(i) for i in (data.get("selected_tracks") or [])],
            preserve_original=bool(data.get("preserve_original", True)),
            output_sample_rate=_opt_int(data.get("output_sample_rate")),
            output_sample_format=(
                AudioSampleFormat.coerce(raw_out_format)
                if raw_out_format else None
            ),
            mix_mode=_opt_str(data.get("mix_mode")),
            channel_map=[
                dict(m) for m in (data.get("channel_map") or [])
                if isinstance(m, Mapping)
            ],
            wav_outputs=[
                dict(w) for w in (data.get("wav_outputs") or [])
                if isinstance(w, Mapping)
            ],
            model_version=_int_or(
                data.get("model_version"), AUDIO_MODEL_VERSION
            ) or AUDIO_MODEL_VERSION,
        )


# ---------------------------------------------------------------------------
# Builder: raw streams -> 模型
# ---------------------------------------------------------------------------


def coerce_audio_stream(
    stream: Any, audio_position: int | None = None,
) -> AudioStream | None:
    """单条 raw stream dict -> AudioStream; 非音频流返回 None。

    对缺失字段一律给安全默认 (0 / UNKNOWN / ""), **绝不臆造**:
    channel_layout 缺失不影响建模, channel_count 已知即可 (§10 情况 F)。
    """
    if isinstance(stream, AudioStream):
        return stream
    if not isinstance(stream, Mapping):
        return None
    if str(stream.get("codec_type") or "").lower() != "audio":
        return None
    tags = stream.get("tags") or {}
    if not isinstance(tags, Mapping):
        tags = {}
    disposition = stream.get("disposition") or {}
    if not isinstance(disposition, Mapping):
        disposition = {}
    language = tags.get("language") or stream.get("language")
    title = tags.get("title") or stream.get("title")
    return AudioStream(
        stream_index=_int_or(stream.get("index"), -1),
        codec_name=str(stream.get("codec_name") or ""),
        codec_long_name=str(stream.get("codec_long_name") or ""),
        sample_rate=_int_or(stream.get("sample_rate")),
        sample_format=AudioSampleFormat.coerce(
            stream.get("sample_fmt"), AudioSampleFormat.UNKNOWN
        ),
        channel_count=_int_or(
            stream.get("channels")
            if stream.get("channels") is not None
            else stream.get("channel_count")
        ),
        channel_layout=normalize_channel_layout(stream.get("channel_layout")),
        duration_sec=_opt_float(stream.get("duration")),
        bit_rate=_opt_int(stream.get("bit_rate")),
        language=_opt_str(language),
        title=_opt_str(title),
        metadata={str(k): v for k, v in tags.items()},
        disposition={str(k): _int_or(v) for k, v in disposition.items()},
        codec_tag_string=str(stream.get("codec_tag_string") or ""),
        start_time_sec=_opt_float(stream.get("start_time")),
        audio_position=audio_position,
        raw=dict(stream),
    )


def build_audio_streams(
    streams: Iterable[Any],
) -> list[AudioStream]:
    """raw stream 列表 -> AudioStream 列表 (保持容器顺序)。

    同时标注每条流的 `audio_position` (0-based 音频序号), 使模型可以直接
    与 core.channel_sync 报告的 `stream` 字段对齐。非音频流被忽略。
    """
    out: list[AudioStream] = []
    for stream in streams:
        model = coerce_audio_stream(stream, audio_position=len(out))
        if model is not None:
            out.append(model)
    return out


def container_stream_index(streams: Iterable[Any]) -> list[int]:
    """音频流的容器 stream index, 顺序即 `audio_position` 顺序。

    即 `container_stream_index(streams)[position] == 容器 index` ——
    core.channel_sync 报告的 `stream` 字段靠它映射回容器 index。
    """
    return [s.stream_index for s in build_audio_streams(streams)]


def _track_metadata(stream: AudioStream) -> dict[str, Any]:
    """track 级 metadata: 源流事实的**只读副本** + 少量派生便利键。"""
    meta: dict[str, Any] = {
        "codec_name": stream.codec_name,
        "codec_long_name": stream.codec_long_name,
        "codec_tag_string": stream.codec_tag_string,
        "audio_position": stream.audio_position,
        "layout_source": stream.layout_source,
        "duration_sec": stream.duration_sec,
        "bit_rate": stream.bit_rate,
        "start_time_sec": stream.start_time_sec,
        "language": stream.language,
        "title": stream.title,
        "default": stream.is_default,
    }
    if stream.metadata:
        meta["tags"] = copy.deepcopy(stream.metadata)
    if stream.disposition:
        meta["disposition"] = {
            str(k): int(v) for k, v in stream.disposition.items()
        }
    return _clean(meta)


class AudioTrackBuilder:
    """Probe 层到 Model 层的适配器: AudioStream -> AudioChannel/AudioTrack/Plan。

    Probe 层只负责"FFprobe JSON -> 原始 stream 信息"; 本类负责"原始信息 ->
    结构化 track/plan"。两者之间没有第三个巨型对象, 执行逻辑一律不在模型里。
    """

    def __init__(
        self,
        streams: Iterable[Any],
        *,
        model_version: int = AUDIO_MODEL_VERSION,
    ) -> None:
        self.streams: list[AudioStream] = build_audio_streams(streams)
        self.model_version = model_version

    # -- 基础 -------------------------------------------------------------

    def __iter__(self) -> Iterator[AudioStream]:
        return iter(self.streams)

    def __len__(self) -> int:
        return len(self.streams)

    @property
    def stream_indices(self) -> list[int]:
        return [s.stream_index for s in self.streams]

    def stream(self, stream_index: int) -> AudioStream | None:
        return next(
            (s for s in self.streams if s.stream_index == stream_index), None
        )

    def channels(self) -> list[AudioChannel]:
        """全部流的全部声道, 顺序 = (stream 顺序, channel 顺序)。"""
        out: list[AudioChannel] = []
        for stream in self.streams:
            out.extend(stream.channels())
        return out

    # -- track 构造 -------------------------------------------------------

    def tracks_per_stream(self) -> list[AudioTrack]:
        """每条流一个 track, channels = 该流全部声道。

        4CH 流 -> 1 track / channels [0,1,2,3]; 4×mono 流 -> 4 track。
        "1 track" 不代表不可再分: `select_channels` 仍可取单通道。
        """
        tracks: list[AudioTrack] = []
        for stream in self.streams:
            channels = stream.channels()
            tracks.append(
                AudioTrack(
                    track_id=_default_track_id(
                        stream.stream_index,
                        [ch.channel_index for ch in channels],
                    ),
                    source_stream_index=stream.stream_index,
                    channels=channels,
                    sample_rate=stream.sample_rate,
                    sample_format=stream.sample_format,
                    layout=stream.channel_layout,
                    layout_verified=stream.layout_verified,
                    enabled=True,
                    role=AudioRole.UNKNOWN,
                    metadata=_track_metadata(stream),
                ).refresh_sync()
            )
        return tracks

    def tracks_per_channel(self) -> list[AudioTrack]:
        """每条流的每个声道一个 track (4CH 流 -> 4 个单通道 track)。"""
        tracks: list[AudioTrack] = []
        for stream in self.streams:
            for channel in stream.channels():
                tracks.append(
                    AudioTrack(
                        track_id=_default_track_id(
                            stream.stream_index, [channel.channel_index]
                        ),
                        source_stream_index=stream.stream_index,
                        channels=[channel],
                        sample_rate=stream.sample_rate,
                        sample_format=stream.sample_format,
                        layout=stream.channel_layout,
                        layout_verified=stream.layout_verified,
                        enabled=True,
                        role=AudioRole.UNKNOWN,
                        metadata=_track_metadata(stream),
                    ).refresh_sync()
                )
        return tracks

    def tracks(self, mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM) -> list[AudioTrack]:
        resolved = TrackBuildMode.coerce(mode, TrackBuildMode.PER_STREAM)
        if resolved is TrackBuildMode.PER_CHANNEL:
            return self.tracks_per_channel()
        return self.tracks_per_stream()

    # -- plan 构造 --------------------------------------------------------

    def plan(
        self,
        mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
        *,
        select_all: bool = True,
        **kwargs: Any,
    ) -> AudioPlan:
        """默认计划: 全 track 选中 + preserve_original=True —— 即旧行为。"""
        plan = AudioPlan.from_tracks(
            self.tracks(mode), select_all=select_all, **kwargs
        )
        plan.model_version = self.model_version
        return plan


def build_plan(
    streams: Iterable[Any],
    *,
    mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    select_all: bool = True,
    **kwargs: Any,
) -> AudioPlan:
    """便捷入口: raw streams -> AudioPlan (默认 = 保留原始全选)。"""
    return AudioTrackBuilder(streams).plan(
        mode, select_all=select_all, **kwargs
    )


# ---------------------------------------------------------------------------
# 与既有 core.channel_sync 的**读取侧**连接 (§7)
# ---------------------------------------------------------------------------

_SYNC_REASON_STATUS: dict[str, SyncStatus] = {
    "low_confidence": SyncStatus.LOW_CONFIDENCE,
    "insufficient_frames": SyncStatus.LOW_CONFIDENCE,
    "non_constant": SyncStatus.NON_CONSTANT,
    "out_of_range": SyncStatus.OUT_OF_RANGE,
    "recheck_residual": SyncStatus.RECHECK_FAILED,
    "already_aligned": SyncStatus.ALREADY_ALIGNED,
}

# 文件级状态 -> 全部轨道的状态 (尚未逐轨测量)
_FILE_LEVEL_STATUS: dict[str, SyncStatus] = {
    "not_eligible": SyncStatus.NOT_APPLICABLE,
    "tool_missing": SyncStatus.NOT_APPLICABLE,
    "measure_failed": SyncStatus.FAILED,
    "verify_failed": SyncStatus.FAILED,
}


class ChannelSyncReport:
    """core.channel_sync.run_channel_sync() 报告的**只读**视图。

    只做解析与字段归一, 不复制同步算法。`stream` 字段是**音频流序号**
    (0-based, 等同 `-map 0:a:N`), 由 `apply_to()` 借 AudioStream.
    audio_position 映射回容器 stream_index —— 绝不靠猜。
    """

    def __init__(self, report: Mapping[str, Any] | None) -> None:
        self.report: dict[str, Any] = dict(report or {})
        self.status: str = str(self.report.get("status") or "")
        self.algo_version: str | None = _opt_str(
            self.report.get("algo_version")
        )
        self.sync_mode: str | None = _opt_str(self.report.get("sync_mode"))
        anchor = self.report.get("anchor_stream")
        self.anchor_stream: int | None = _opt_int(anchor)
        self.detail: str | None = _opt_str(self.report.get("detail"))
        rows = self.report.get("channels") or []
        self.channels: list[dict[str, Any]] = [
            dict(r) for r in rows if isinstance(r, Mapping)
        ]

    @property
    def eligible(self) -> bool:
        return self.status not in ("not_eligible", "tool_missing")

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def row(self, audio_position: int) -> dict[str, Any] | None:
        return next(
            (r for r in self.channels
             if _opt_int(r.get("stream")) == audio_position),
            None,
        )

    def resolve(self, row: Mapping[str, Any] | None) -> AudioSyncResult:
        """报告行 -> AudioSyncResult (字段名与 §十三 示例一致)。"""
        if row is None:
            file_status = _FILE_LEVEL_STATUS.get(self.status)
            if file_status is None:
                return AudioSyncResult(
                    status=SyncStatus.NOT_PROCESSED,
                    reason=self.detail,
                    algo_version=self.algo_version,
                )
            return AudioSyncResult(
                status=file_status,
                reason=self.detail or self.status,
                algo_version=self.algo_version,
                source="channel_sync_report",
            )
        decision = str(row.get("decision") or "")
        reason = _opt_str(row.get("reason"))
        status = self._row_status(decision, reason)
        offset_samples = _opt_float(row.get("delay_samples"))
        offset_ms = _opt_float(row.get("delay_ms"))
        if status is SyncStatus.SUCCESS:
            # 实际移位量优先 (P1 为整数样本移位); 缺失时退化为测量延迟。
            shift = _opt_float(row.get("shift_samples"))
            if shift is not None:
                offset_samples = shift
        anchor_id = (
            f"stream:{self.anchor_stream}"
            if self.anchor_stream is not None else None
        )
        warnings = row.get("warnings") or []
        return AudioSyncResult(
            status=status,
            offset_samples=offset_samples,
            offset_ms=offset_ms,
            quality=_opt_float(row.get("confidence")),
            anchor=anchor_id,
            reason=reason or decision or None,
            drift_ppm=_opt_float(row.get("drift_ppm")),
            constant=(
                bool(row["constant"]) if row.get("constant") is not None
                else None
            ),
            polarity=_opt_int(row.get("polarity")),
            usable_frames=_opt_int(row.get("usable_frames")),
            rms_dbfs=_opt_float(row.get("rms_dbfs")),
            algo_version=self.algo_version,
            source="channel_sync_report",
            warnings=[str(w) for w in warnings],
        )

    def _row_status(self, decision: str, reason: str | None) -> SyncStatus:
        if reason in _SYNC_REASON_STATUS:
            return _SYNC_REASON_STATUS[reason]
        if decision == "fixed":
            return SyncStatus.SUCCESS
        if decision == "already_aligned":
            return SyncStatus.ALREADY_ALIGNED
        if decision == "anchor":
            return SyncStatus.ALREADY_ALIGNED
        if decision == "untouched":
            # 未修正但已测量 (健康门未过) -> 归入 FAILED 的宽口径
            return SyncStatus.FAILED
        file_status = _FILE_LEVEL_STATUS.get(self.status)
        if file_status is not None:
            return file_status
        return SyncStatus.NOT_PROCESSED

    def apply_to(self, tracks: Iterable[AudioTrack]) -> list[AudioTrack]:
        """把同步结果写入 track (只写 sync, 不改 stream/channel 身份)。

        track -> 报告的关联键是 `AudioStream.audio_position` (取自
        track.metadata["audio_position"]), 因此 4 条 mono 流会被分别
        对应到 4 行, 不会被错误地合并成一行。
        """
        out = list(tracks)
        for track in out:
            position = _opt_int(track.metadata.get("audio_position"))
            if position is None:
                # 无 position 信息 -> 保守: 只在文件级状态上留痕
                if self.status in _FILE_LEVEL_STATUS:
                    track.set_sync(AudioSyncResult(
                        status=_FILE_LEVEL_STATUS[self.status],
                        reason=self.detail or self.status,
                        algo_version=self.algo_version,
                        source="channel_sync_report",
                    ))
                continue
            track.set_sync(self.resolve(self.row(position)))
        return out


def apply_channel_sync_report(
    plan: AudioPlan,
    report: Mapping[str, Any] | None,
) -> AudioPlan:
    """把 channel_sync 报告写进 AudioPlan 的全部 track (原地 + 返回)。"""
    ChannelSyncReport(report).apply_to(plan.input_tracks)
    return plan
