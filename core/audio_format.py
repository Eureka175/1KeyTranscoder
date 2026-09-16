"""Audio Format Policy (v0.8.0) — 输入格式分类 + 默认 alignment + 输出编码决议。

本模块回答三个**纯策略**问题, 不读 PCM、不写文件、不拼 argv、不跑进程:

    1. 这个输入是 uncompressed (PCM) 还是 compressed?      (§3)
    2. 当前 alignment 该不该启用? 需要 warning 吗?           (§4/§5/§40/§41)
    3. 输出该用什么 codec / bitrate?                          (§6–§10/§35)

分层位置
--------

    Probe (core.probe / core.audio_probe)
        ↓  AudioStream.codec_name / bit_rate
    **本模块**  ← 格式事实 -> 明确策略
        ↓  AlignmentDecision / OutputFormatDecision
    AudioPlan / AudioExecutionPath / AudioTimeline / Render / Encode

这里没有任何算法: 判定全部是**查表 + 显式优先级**, 因此可在单测里用合成
`AudioStream` 完全覆盖, 也不需要 ffmpeg。

格式分类 (§3)
-------------

    PCM codec        -> UNCOMPRESSED
    其它 audio codec -> COMPRESSED

判据只有一条: **ffmpeg 的 codec 名**, 不引入第二套 codec probe, 也不看
`sample_fmt` / bit depth 之类的推测信号。

优先级链 (§8/§27)
-----------------

    manual override  >  source-derived defaults  >  encoder default

"manual" 与 "没写" 必须区分得开, 所以本模块用 `ExplicitFormat` 显式记录
用户**拧过哪些旋钮** —— 否则 `{"format": "aac"}` 与 `{"bitrate": "128k"}`
里的 format 就无法区分"用户要 AAC"和"用户没说, 默认 AAC"。

不做的事
--------

* 不 resample: 采样率永远来自 `AudioTimeline` (只做一致性校验);
* 不做 loudness / AGC / dynamics / preset / quality / VBR 策略框架;
* 不建 codec 能力数据库: 支持的输出格式就是 `AudioEncodeFormat` 那张显式表;
* 不做 drift 检测或修正。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .audio_encode import AudioEncodeFormat, AudioFormatSpec
from .audio_models import AudioPlan, AudioStream

__all__ = [
    "AUDIO_FORMAT_VERSION",
    "AlignmentDecision",
    "AudioAlignmentPolicy",
    "AudioInputFormat",
    "ExplicitFormat",
    "OutputFormatDecision",
    "REASON_ALIGNMENT_NO_CHANNELS",
    "REASON_FORMAT_BITRATE_NOT_APPLICABLE",
    "WARNING_ALIGNMENT_COMPRESSED",
    "WARNING_FORMAT_INHERIT_UNAVAILABLE",
    "classify_codec",
    "format_of_stream",
    "is_pcm_codec",
    "plan_formats",
    "resolve_alignment",
    "resolve_output_format",
    "source_format_of",
]

#: 策略模块自身的版本 (与音频模型版本独立; 报告/日志据此比对)。
AUDIO_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# 1. 输入格式分类 (§3)
# ---------------------------------------------------------------------------


class AudioInputFormat(str, Enum):
    """输入音频的**格式类别** —— 只有两种, 不猜第三种。

    * `PCM`        —— 未压缩; 默认可以进入 alignment (样本级搬运无损);
    * `COMPRESSED` —— 压缩 (含有损与无损压缩); 默认原样保留, 不为了
                      alignment 重新编码。

    未知/缺失 codec 一律归入 `COMPRESSED`: 对"我们认不出来的东西"采取保守
    策略 (不主动解码 + 重编码), 且这一归类会以 warning 形式出现在报告里,
    而不是静默。
    """

    PCM = "pcm"
    COMPRESSED = "compressed"

    @property
    def is_pcm(self) -> bool:
        return self is AudioInputFormat.PCM


#: ffmpeg 的 PCM 家族一律以 `pcm_` 开头 —— 这是**唯一**的分类判据。
PCM_CODEC_PREFIX = "pcm_"

#: 少数不带 `pcm_` 前缀但确为未压缩 PCM 的 codec 名 (显式表, 不是前缀规则
#: 的替代品)。扩充它是有意的动作, 不是运行时猜测。
PCM_CODECS = frozenset({"ipcm"})


def is_pcm_codec(codec_name: Any) -> bool:
    """`codec_name` 是否属于未压缩 PCM。"""
    text = str(codec_name or "").strip().lower()
    if not text:
        return False
    return text.startswith(PCM_CODEC_PREFIX) or text in PCM_CODECS


def classify_codec(codec_name: Any) -> AudioInputFormat:
    """codec 名 -> `AudioInputFormat` (§3 的唯一判定)。"""
    return (
        AudioInputFormat.PCM if is_pcm_codec(codec_name)
        else AudioInputFormat.COMPRESSED
    )


def format_of_stream(stream: Any) -> AudioInputFormat:
    """`AudioStream` (或任何带 `codec_name` 的对象 / dict) -> 格式类别。"""
    if stream is None:
        return AudioInputFormat.COMPRESSED
    if isinstance(stream, AudioStream):
        return classify_codec(stream.codec_name)
    if isinstance(stream, Mapping):
        return classify_codec(stream.get("codec_name"))
    return classify_codec(getattr(stream, "codec_name", ""))


def source_format_of(source: Any) -> AudioInputFormat:
    """一个 `AudioSource` 的格式类别。

    多流来源: **任一条流是 compressed 就是 compressed** —— 保守方向
    (compressed 是有否决权的那一侧, 因为它决定"要不要 decode + re-encode")。
    没有流 (不可知) 同样归 COMPRESSED。
    """
    streams = list(getattr(source, "streams", None) or [])
    if not streams:
        return AudioInputFormat.COMPRESSED
    for stream in streams:
        if format_of_stream(stream) is AudioInputFormat.COMPRESSED:
            return AudioInputFormat.COMPRESSED
    return AudioInputFormat.PCM


def _participating_channels(
    plan: AudioPlan, channel_ids: Sequence[str] | None,
) -> list[Any]:
    """参与判定的声道: 显式给出优先, 否则用**有效映射** (输出顺序)。"""
    from .audio_plan import effective_mapping

    if channel_ids is not None:
        out = []
        for cid in channel_ids:
            channel = plan.channel(str(cid))
            if channel is not None:
                out.append(channel)
        return out
    out = []
    for entry in effective_mapping(plan):
        channel = plan.channel(str(entry.get("source_channel_id") or ""))
        if channel is not None:
            out.append(channel)
    return out


@dataclass(frozen=True)
class _StreamFacts:
    """一条流上**与格式判定有关**的事实 (codec 名 + 码率)。

    只存在于本模块内部: 它把"从哪读这两个字段"收成一处, 于是
    "计划里有 AudioSource" 与 "计划只有 input_tracks (Phase 1 形态)"
    两条路径给出同一个答案 —— 否则后者的 PCM 会被误判成 compressed。
    """

    codec_name: str = ""
    bit_rate: int | None = None
    channel_count: int = 0
    known: bool = False


def _stream_facts(plan: AudioPlan, channel: Any) -> _StreamFacts:
    source = plan.source(channel.source_id)
    if source is not None:
        stream = source.stream(channel.stream_index)
        if stream is not None:
            return _StreamFacts(
                codec_name=str(stream.codec_name or ""),
                bit_rate=stream.bit_rate,
                channel_count=int(stream.channel_count or 0),
                known=True,
            )
    for track in plan.input_tracks:
        if track.source_id != channel.source_id:
            continue
        if int(track.source_stream_index) != int(channel.stream_index):
            continue
        meta = track.metadata or {}
        return _StreamFacts(
            codec_name=str(meta.get("codec_name") or ""),
            bit_rate=_opt_bitrate(meta.get("bit_rate")),
            channel_count=int(track.channel_count or 0),
            known=True,
        )
    return _StreamFacts(known=False)


def _opt_bitrate(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def plan_formats(
    plan: AudioPlan, *, channel_ids: Sequence[str] | None = None,
) -> dict[str, AudioInputFormat]:
    """参与输出的**来源** -> 格式类别 (顺序 = 输出顺序首次出现)。

    只报告**真的参与输出**的来源: 被 selection 排除的声道不参与判定,
    否则"选了一条 PCM 就等于整份计划都是 PCM"。

    一个来源内部混装 PCM 与 compressed 时, 该来源整体判为 `COMPRESSED` ——
    与 `source_format_of()` 同一个保守方向 (compressed 是有否决权的那侧),
    因此不会因为"先看到 PCM 流"就把整个来源当成未压缩。
    """
    out: dict[str, AudioInputFormat] = {}
    for channel in _participating_channels(plan, channel_ids):
        source = plan.source(channel.source_id)
        if source is not None:
            found = source_format_of(source)
        else:
            found = classify_codec(_stream_facts(plan, channel).codec_name)
        if out.get(channel.source_id) is AudioInputFormat.COMPRESSED:
            continue
        out[channel.source_id] = found
    return out


# ---------------------------------------------------------------------------
# 2. 默认 alignment 策略 (§4/§5/§40/§41)
# ---------------------------------------------------------------------------


class AudioAlignmentPolicy(str, Enum):
    """用户对 alignment 的**意图** (不是执行图, 也不是 reference 选择)。

    * `AUTO`     —— 默认: 按输入格式决定 (PCM 允许, compressed 不允许);
    * `ENABLED`  —— 显式要求 alignment (compressed 会 warning + 解码重编码);
    * `DISABLED` —— 显式关闭。

    ⚠️ `ENABLED`/`AUTO` 只说"**允许进入** alignment pipeline", **不代表**
    自动猜 reference。reference 仍然只能由 `SyncPlan.reference_channel_id`
    显式给出 (§40), 本模块不提供任何隐式 reference 规则。
    """

    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"

    @classmethod
    def coerce(
        cls, value: Any, default: Any = None,
    ) -> "AudioAlignmentPolicy | Any":
        if value is None or value == "":
            return default
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return default


#: §5 要求的 warning 原文 (compressed 输入被显式要求 alignment 时)。
WARNING_ALIGNMENT_COMPRESSED = (
    "Audio alignment requested for compressed input. "
    "The stream must be decoded to PCM and re-encoded afterward."
)

#: AUTO 模式下 compressed 输入被跳过时记录的原因 (不是错误)。
NOTE_ALIGNMENT_SKIPPED_COMPRESSED = (
    "compressed input keeps its stream as-is by default: no decode and no "
    "re-encode for alignment (ask for it explicitly to force the PCM path)"
)

REASON_ALIGNMENT_NO_CHANNELS = "audio_alignment_no_channels"

_REASON_PCM_DEFAULT = "alignment_default_pcm"
_REASON_COMPRESSED_DEFAULT = "alignment_default_compressed"
_REASON_PCM_MIXED_DEFAULT = "alignment_default_mixed"
_REASON_PCM_EXPLICIT = "alignment_enabled_pcm"
_REASON_COMPRESSED_EXPLICIT = "alignment_enabled_compressed"
_REASON_DISABLED = "alignment_disabled_by_request"
_REASON_NO_CHANNELS = REASON_ALIGNMENT_NO_CHANNELS


@dataclass
class AlignmentDecision:
    """"本次要不要走 alignment"的完整结论 (JSON-compatible)。

    `enabled=True` 只表示**允许进入** alignment pipeline; 是否真的产生
    offset 取决于 `SyncPlan.reference_channel_id` 是否被显式给出
    (见 `core.audio_sync`)。本结构因此额外报告 `reference_required`。
    """

    requested: str = AudioAlignmentPolicy.AUTO.value
    enabled: bool = False
    reason: str = ""
    formats: dict[str, str] = field(default_factory=dict)
    compressed_sources: list[str] = field(default_factory=list)
    requires_decode: bool = False
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def any_compressed(self) -> bool:
        return bool(self.compressed_sources)

    @property
    def reference_required(self) -> bool:
        """alignment 是否仍然需要一个显式 reference 才会生效。"""
        return bool(self.enabled)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "requested": self.requested,
            "enabled": bool(self.enabled),
            "reason": self.reason,
            "formats": dict(self.formats),
            "requires_decode": bool(self.requires_decode),
            "reference_required": bool(self.reference_required),
            "compressed_sources": list(self.compressed_sources),
        }
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "alignment": ("enabled" if self.enabled else "disabled"),
            "reason": self.reason,
            "formats": dict(self.formats),
            "decode_required": bool(self.requires_decode),
            "warnings": list(self.warnings),
        }


def resolve_alignment(
    plan: AudioPlan | None,
    requested: AudioAlignmentPolicy | str | None = AudioAlignmentPolicy.AUTO,
    *,
    channel_ids: Sequence[str] | None = None,
) -> AlignmentDecision:
    """格式感知的 alignment 决议 (§4/§5)。

    判定是**穷举的**, 没有"其它情况":

    | requested | 参与来源格式 | enabled | reason |
    |---|---|---|---|
    | `disabled` | 任意 | False | `alignment_disabled_by_request` |
    | `enabled`  | 全 PCM | True | `alignment_enabled_pcm` |
    | `enabled`  | 含 compressed (含混合) | True + warning | `alignment_enabled_compressed` |
    | `auto`     | 全 PCM | True | `alignment_default_pcm` |
    | `auto`     | 含 compressed | False | `alignment_default_compressed` |

    混合 (PCM + compressed) 在 `auto` 下是 **disabled**: compressed 一侧有
    否决权, 因为启用它就意味着"必须解码 + 重编码", 而默认路径不做这件事。
    `enabled` 下混合同样 warning (§41)。
    """
    policy = AudioAlignmentPolicy.coerce(
        requested, AudioAlignmentPolicy.AUTO
    ) or AudioAlignmentPolicy.AUTO
    formats = plan_formats(plan, channel_ids=channel_ids) if plan else {}
    rendered = {sid: fmt.value for sid, fmt in formats.items()}
    compressed = sorted(
        sid for sid, fmt in formats.items() if fmt is AudioInputFormat.COMPRESSED
    )
    decision = AlignmentDecision(
        requested=policy.value,
        formats=rendered,
        compressed_sources=compressed,
    )

    if not formats:
        # 没有参与输出的来源 -> 没有东西可以对齐 (不是错误: 计划可能为空)。
        decision.enabled = False
        decision.reason = _REASON_NO_CHANNELS
        decision.notes.append(
            "no source participates in the output: nothing to align"
        )
        return decision

    if policy is AudioAlignmentPolicy.DISABLED:
        decision.enabled = False
        decision.reason = _REASON_DISABLED
        decision.notes.append(
            "alignment disabled by the request; offsets are not estimated or "
            "applied (and no stream is decoded for alignment)"
        )
        return decision

    if policy is AudioAlignmentPolicy.ENABLED:
        decision.enabled = True
        if compressed:
            decision.reason = _REASON_COMPRESSED_EXPLICIT
            decision.requires_decode = True
            decision.warnings.append(WARNING_ALIGNMENT_COMPRESSED)
            decision.notes.append(
                "compressed source(s) "
                f"{compressed} take the decode -> PCM -> align -> re-encode "
                "path (never a fake timestamp shift on the compressed packets)"
            )
        else:
            decision.reason = _REASON_PCM_EXPLICIT
            decision.notes.append(
                "PCM source(s): alignment runs on the sample timeline, "
                "no re-encode is required by the alignment itself"
            )
        decision.notes.append(
            "an explicit reference channel is still required: alignment "
            "never guesses one"
        )
        return decision

    # AUTO
    if compressed:
        decision.enabled = False
        decision.reason = _REASON_COMPRESSED_DEFAULT
        decision.notes.append(NOTE_ALIGNMENT_SKIPPED_COMPRESSED)
        if any(fmt is AudioInputFormat.PCM for fmt in formats.values()):
            decision.reason = _REASON_PCM_MIXED_DEFAULT
            decision.notes.append(
                "mixed PCM + compressed input: alignment stays off because "
                "enabling it would re-encode the compressed side"
            )
        return decision

    decision.enabled = True
    decision.reason = _REASON_PCM_DEFAULT
    decision.notes.append(
        "PCM input: alignment is enabled by default (an explicit reference "
        "is still required; none is guessed)"
    )
    return decision


# ---------------------------------------------------------------------------
# 3. 输出编码决议 (§6–§10/§35)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplicitFormat:
    """用户**显式拧过**哪些编码旋钮 (区分"没写"与"写成了默认值")。"""

    format: bool = False
    bitrate: bool = False
    sample_rate: bool = False
    channel_count: bool = False

    @property
    def any_set(self) -> bool:
        return bool(
            self.format or self.bitrate or self.sample_rate
            or self.channel_count
        )

    @classmethod
    def all_set(cls) -> "ExplicitFormat":
        """把整份 spec 当作手写覆盖 (调用方直接传 `AudioFormatSpec` 时)。"""
        return cls(True, True, True, True)

    @classmethod
    def coerce(cls, value: Any) -> "ExplicitFormat":
        if isinstance(value, ExplicitFormat):
            return value
        if value is None:
            return cls()
        if isinstance(value, Mapping):
            return cls(
                format=bool(value.get("format")),
                bitrate=bool(value.get("bitrate")),
                sample_rate=bool(value.get("sample_rate")),
                channel_count=bool(value.get("channel_count")),
            )
        raise TypeError(
            "explicit format must be ExplicitFormat / mapping / None, got "
            f"{type(value).__name__}"
        )


#: 输入 codec -> 可继承的输出格式。**只列我们能真正编码的**; 表里没有的
#: codec 一律退回 encoder default 并记录 warning (不猜、不静默转码)。
FORMAT_FOR_CODEC: dict[str, AudioEncodeFormat] = {
    "aac": AudioEncodeFormat.AAC,
    "opus": AudioEncodeFormat.OPUS,
    "flac": AudioEncodeFormat.FLAC,
}

REASON_FORMAT_BITRATE_NOT_APPLICABLE = "audio_format_bitrate_not_applicable"
WARNING_FORMAT_INHERIT_UNAVAILABLE = "audio_format_inherit_unavailable"

_ORIGIN_MANUAL = "manual"
_ORIGIN_SOURCE = "source"
_ORIGIN_ENCODER = "encoder_default"
_ORIGIN_NONE = "none"


@dataclass
class OutputFormatDecision:
    """"输出用什么编码"的完整结论 (JSON-compatible)。"""

    spec: AudioFormatSpec = field(default_factory=AudioFormatSpec)
    source_codec: str = ""
    source_format: str = AudioInputFormat.COMPRESSED.value
    codec_origin: str = _ORIGIN_ENCODER
    bitrate_origin: str = _ORIGIN_NONE
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def codec(self) -> str:
        return self.spec.format.value

    @property
    def reasons(self) -> list[str]:
        return [str(e.get("reason") or "") for e in self.errors]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "spec": self.spec.to_dict(),
            "codec": self.spec.format.value,
            "codec_origin": self.codec_origin,
            "bitrate_origin": self.bitrate_origin,
            "ok": bool(self.ok),
        }
        if self.source_codec:
            data["source_codec"] = self.source_codec
            data["source_format"] = self.source_format
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "codec": self.spec.format.value,
            "bitrate": self.spec.bitrate,
            "codec_origin": self.codec_origin,
            "bitrate_origin": self.bitrate_origin,
            "reasons": self.reasons,
        }


def _first_stream(plan: AudioPlan, channel_ids: Sequence[str] | None) -> _StreamFacts:
    """参与输出的**第一条**流 (输出顺序) —— 继承的判定基准。"""
    for channel in _participating_channels(plan, channel_ids):
        facts = _stream_facts(plan, channel)
        if facts.known:
            return facts
    return _StreamFacts()


def _matching_streams(
    plan: AudioPlan, codec: str, channel_ids: Sequence[str] | None,
) -> list[_StreamFacts]:
    """参与输出、且 codec 与 `codec` 相同的流 (顺序 = 输出顺序)。"""
    out: list[_StreamFacts] = []
    seen: set[tuple[str, int]] = set()
    for channel in _participating_channels(plan, channel_ids):
        key = (channel.source_id, int(channel.stream_index))
        if key in seen:
            continue
        seen.add(key)
        facts = _stream_facts(plan, channel)
        if not facts.known:
            continue
        if facts.codec_name.strip().lower() == codec:
            out.append(facts)
    return out


def format_bitrate(bit_rate: Any) -> str:
    """bits/s -> ffmpeg `-b:a` 取值 (整千用 `192k`, 否则用精确数值)。"""
    try:
        value = int(bit_rate)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value % 1000 == 0:
        return f"{value // 1000}k"
    return str(value)


def resolve_output_format(
    plan: AudioPlan | None,
    *,
    requested: AudioFormatSpec | str | None = None,
    explicit: ExplicitFormat | Mapping[str, Any] | None = None,
    channel_ids: Sequence[str] | None = None,
) -> OutputFormatDecision:
    """输入格式 + 用户意图 -> 输出编码规格 (§6–§10/§35)。

    优先级 (§8): **manual > source-derived > encoder default**。

    * codec: 用户显式给了 `format` -> 用它; 否则由输入 codec 继承
      (`aac`->AAC, `opus`->Opus, `flac`->FLAC, `pcm_*`->PCM); 继承不到
      (表里没有的 codec) -> encoder default 并记 warning;
    * bitrate: 用户显式给了 -> 用它; 否则**仅当输出 codec 与输入 codec
      一致**时继承输入 bitrate (`source AAC 192k -> AAC 192k`), 否则留空
      (不同 codec 的码率不具可比性, 不把 192k 硬搬到 Opus 上);
    * sample_rate / channel_count: 永远来自 `AudioTimeline` —— 这里只保留
      用户显式值用于**校验**(由 `AudioFormatSpec.validate()` 拒绝不一致),
      本模块不做任何 resample。

    PCM (以及 FLAC) **不允许** bitrate (§10): 显式指定即拒绝
    (`audio_format_bitrate_not_applicable`), 不是静默忽略。
    """
    spec = AudioFormatSpec.coerce(requested)
    marks = ExplicitFormat.coerce(explicit)
    decision = OutputFormatDecision()

    if plan is None:
        decision.spec = spec
        decision.codec_origin = (
            _ORIGIN_MANUAL if marks.format else _ORIGIN_ENCODER
        )
        decision.bitrate_origin = (
            _ORIGIN_MANUAL if (marks.bitrate and spec.bitrate)
            else (_ORIGIN_NONE if not spec.bitrate else _ORIGIN_MANUAL)
        )
        return _validate_bitrate(decision)

    source_stream = _first_stream(plan, channel_ids)
    source_codec = str(source_stream.codec_name or "")
    source_format = classify_codec(source_codec)
    decision.source_codec = source_codec
    decision.source_format = source_format.value

    # ---- codec -----------------------------------------------------------
    if marks.format:
        decision.spec = spec
        decision.codec_origin = _ORIGIN_MANUAL
    else:
        inherited = FORMAT_FOR_CODEC.get(source_codec.strip().lower())
        if inherited is None and source_format is AudioInputFormat.PCM:
            inherited = AudioEncodeFormat.PCM
        if inherited is not None:
            decision.spec = AudioFormatSpec(
                format=inherited,
                sample_rate=spec.sample_rate,
                channel_count=spec.channel_count,
                bitrate=spec.bitrate,
                extra_args=list(spec.extra_args),
            )
            decision.codec_origin = _ORIGIN_SOURCE
            decision.notes.append(
                f"output codec inherited from the source: "
                f"{source_codec or 'unknown'} -> {inherited.value}"
            )
        else:
            decision.spec = spec
            decision.codec_origin = _ORIGIN_ENCODER
            decision.warnings.append(
                f"{WARNING_FORMAT_INHERIT_UNAVAILABLE}: source codec "
                f"{source_codec or 'unknown'!r} has no inheritable encoder in "
                f"this build; falling back to the encoder default "
                f"({spec.format.value})"
            )

    # ---- bitrate ---------------------------------------------------------
    bitrate = spec.bitrate if marks.bitrate else ""
    origin = _ORIGIN_MANUAL if (marks.bitrate and bitrate) else _ORIGIN_NONE
    if not bitrate and decision.spec.format.accepts_bitrate:
        candidates = _matching_streams(
            plan, decision.spec.format.encoder_codec, channel_ids
        )
        if decision.codec_origin == _ORIGIN_SOURCE or marks.format:
            for stream in candidates:
                text = format_bitrate(stream.bit_rate)
                if text:
                    bitrate = text
                    origin = _ORIGIN_SOURCE
                    decision.notes.append(
                        f"bitrate inherited from the source stream: {text}"
                    )
                    break
    decision.spec = AudioFormatSpec(
        format=decision.spec.format,
        sample_rate=decision.spec.sample_rate,
        channel_count=decision.spec.channel_count,
        bitrate=bitrate,
        extra_args=list(decision.spec.extra_args),
    )
    decision.bitrate_origin = origin
    if not decision.spec.format.accepts_bitrate and bitrate:
        decision.notes.append(
            f"bitrate {bitrate} dropped: "
            f"{decision.spec.format.value} is lossless"
        )
    return _validate_bitrate(decision)


def _validate_bitrate(decision: OutputFormatDecision) -> OutputFormatDecision:
    """lossless 输出不得带 bitrate (§10: PCM 显式指定即拒绝)。"""
    spec = decision.spec
    if spec.bitrate and not spec.format.accepts_bitrate:
        decision.errors.append({
            "reason": REASON_FORMAT_BITRATE_NOT_APPLICABLE,
            "detail": (
                f"bitrate {spec.bitrate!r} is not applicable to "
                f"{spec.format.value} ({spec.format.encoder}): the format is "
                "lossless, so a bitrate cannot be honoured — remove the "
                "bitrate or choose a lossy format"
            ),
        })
    return decision


def describe_formats(
    formats: Mapping[str, AudioInputFormat] | Iterable[tuple[str, Any]],
) -> str:
    """一行可读摘要 (日志/测试用, 稳定格式)。"""
    items = (
        formats.items() if isinstance(formats, Mapping) else formats
    )
    parts = [
        f"{sid}={getattr(fmt, 'value', fmt)}" for sid, fmt in items
    ]
    return "formats: " + (", ".join(parts) if parts else "(none)")
