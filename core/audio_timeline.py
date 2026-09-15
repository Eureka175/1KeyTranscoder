"""Audio Timeline / Render Policy (v0.7.1 Phase 3A) — 统一时间轴层。

本模块是 Phase 3A 的**唯一**时长/EOF 权威:

    AudioPlan  +  每源可用样本数
            ↓
    AudioTimeline   (sample_rate / render window / 逐声道 timeline 归一)
            ↓
    PCM Reader → Routing → (Phase 3B: Mixing) → WAV Export

硬要求 (§Phase 3A 时间轴规范):

* `PCMReader` **不得**决定 render duration;
* `WavExporter` **不得**决定 EOF / 输出长度;
* Mixer (Phase 3B) **不得**自己定义 short-source policy;
* 三者都只消费本模块算出的 `AudioTimeline`。

输出时长不是"谁先 EOF 就结束", 而是由 `AudioPlan` 依据明确规则确定:

    RenderDurationPolicy.DERIVED + RenderPolicy.UNION
        render window = 全部参与输出来源的 timeline 并集
        短 source -> 缺失区间补 **确定性静音 0.0** (不循环, 不重复末样本)
        长 source -> 不因其它 source EOF 而截断

缺失数据一律 `float32 0.0`; 禁止 NaN, 禁止循环, 禁止复制最后一个样本。

统一采样率: 参与同一个 render 的来源必须同采样率, 否则
`audio_sample_rate_mismatch` —— **不偷偷 resample** (resampling 属后续阶段)。

--------------------------------------------------------------------------
offset 方向 (实测确定, 不得按字段名猜)
--------------------------------------------------------------------------
`AudioSyncResult.offset_samples` 直接来自 `core.channel_sync` 报告的
`shift_samples` (= `rint(delay_samples)`), 语义是**观测到的轨间时差**:
`delay > 0` = 目标轨比锚点**晚到**。现有正式实现
`core/sync_fix.py::shift_stream` 的修正动作是:

    out[n] = in[n + shift]          # delay > 0 = 前移, 越界补零

实测 (4×mono 合成素材, CH2 晚到 960 样本, 进程内 `run_channel_sync`):

    报告: stream=1 delay_samples=+960 shift_samples=960
    修正后 vs 修正前互相关 lag = -960   (即 after[n] == before[n + 960])

因此把这样一个"晚到 offset"折算到统一 timeline 上是**前移**:

    timeline_sample = source_sample - offset_samples       # +960 -> 内容位于 t-960

这是本项目**唯一**的 offset → timeline 换算入口 (`source_to_timeline`),
reader / routing / mixer / exporter 一律只消费它, 不允许各自解释。
`tests/full_autotest.py` 的 Phase 3A 用例把该方向钉进回归 (端到端
impulse: 晚到 960 的轨 @1960 与锚轨 @1000 对齐到同一 timeline 位置 1000)。

⚠️ 只应用**已存在**的固定整数样本 offset。本模块**不做**任何 delay 估计
(禁止重新实现 GCC-PHAT), 也**不修** `core/channel_sync.py` 的算法。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .audio_models import (
    AudioChannel,
    AudioPlan,
    AudioStream,
    SyncStatus,
)
from .audio_plan import effective_mapping

__all__ = [
    "AudioRenderError",
    "AudioTimeline",
    "ChannelTimeline",
    "DurationMode",
    "PCM_CHUNK_FRAMES_DEFAULT",
    "RenderPolicy",
    "SourceAvailability",
    "SyncApplication",
    "apply_plan_durations",
    "availability_from_reader",
    "declared_samples_of_stream",
    "render_timeline",
    "resolve_timeline",
    "samples_from_seconds",
    "seconds_from_samples",
    "source_to_timeline",
    "timeline_to_source",
    "REASON_AUDIO_DURATION_UNKNOWN",
    "REASON_AUDIO_TIMELINE_INVALID",
    "REASON_AUDIO_NEGATIVE_RENDER_DURATION",
    "REASON_AUDIO_SAMPLE_RATE_MISMATCH",
    "REASON_AUDIO_SYNC_OFFSET_INVALID",
    "REASON_AUDIO_DURATION_METADATA_MISMATCH",
    "REASON_AUDIO_DURATION_EXPLICIT_INVALID",
]

# 默认读块大小 (frames)。96 kHz / 4CH 下 ≈ 0.34 s ≈ 5.2 MB float32 —— 与
# 工程既有 `channel_sync.DEFAULTS["fix_chunk_seconds"]` 同量级, 但以**样本数**
# 表达, 便于 chunk invariance 测试直接改变它。
PCM_CHUNK_FRAMES_DEFAULT = 16384


# ---------------------------------------------------------------------------
# 稳定 reason codes (§时间轴错误策略)
# ---------------------------------------------------------------------------

REASON_AUDIO_SAMPLE_RATE_MISMATCH = "audio_sample_rate_mismatch"
REASON_AUDIO_TIMELINE_INVALID = "audio_timeline_invalid"
REASON_AUDIO_NEGATIVE_RENDER_DURATION = "audio_negative_render_duration"
REASON_AUDIO_SYNC_OFFSET_INVALID = "audio_sync_offset_invalid"
REASON_AUDIO_DURATION_UNKNOWN = "audio_duration_unknown"
REASON_AUDIO_DURATION_METADATA_MISMATCH = "audio_duration_metadata_mismatch"
REASON_AUDIO_DURATION_EXPLICIT_INVALID = "audio_duration_explicit_invalid"


class AudioRenderError(ValueError):
    """时间轴/render 期错误。携带稳定 `reason`, 便于逐项判定与日志比对。

    音频时间轴错误比"程序直接失败"更危险 (成品里听不出来), 因此本层
    **宁可拒绝**也不"尽可能输出一个结果"。
    """

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


# ---------------------------------------------------------------------------
# 采样数 / 秒 换算 (round-half-away-from-zero, 与 numpy.rint 区分开)
# ---------------------------------------------------------------------------


def samples_from_seconds(seconds: float, sample_rate: int) -> int:
    """秒 -> 样本数 (四舍五入, half away from zero; 0.5-eps 不塌到 0)。

    浮点 duration × 采样率会带来 ±1 样本级别的表示误差; 统一走这一个
    换算入口, 保证 timeline 与 WAV header 的样本数只有一个来源。
    """
    if not sample_rate or sample_rate <= 0:
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID,
            f"invalid sample_rate: {sample_rate!r}",
        )
    value = float(seconds) * float(sample_rate)
    if value != value or value in (float("inf"), float("-inf")):
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID, f"invalid seconds: {seconds!r}"
        )
    return int(math.floor(value + 0.5)) if value >= 0 else -int(
        math.floor(-value + 0.5)
    )


def seconds_from_samples(samples: int, sample_rate: int) -> float:
    """样本数 -> 秒 (仅用于报告/日志; 内部一律以样本数为准)。"""
    if not sample_rate or sample_rate <= 0:
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID,
            f"invalid sample_rate: {sample_rate!r}",
        )
    return float(samples) / float(sample_rate)


# ---------------------------------------------------------------------------
# offset 统一换算 (唯一入口)
# ---------------------------------------------------------------------------


class SyncApplication(str, Enum):
    """已测量的同步 offset 该如何被 PCM 层消费。

    存在的意义: 同一份 `AudioSyncResult` 在两种输入下含义不同 ——

    ==============  ==================================================
    取值             语义
    ==============  ==================================================
    APPLY           输入是**未修正**的源 (原始流 / 源文件), 因此必须
                    应用 offset (`timeline = source - offset`)
    NONE            输入已是 channel_sync 的**修正后**产物 (shift 已经
                    烧进样本), 再应用一次就是**二次移位** —— 必须禁止
    ==============  ==================================================

    本阶段生产路径不消费 channel_sync 产物, 但模型里 `AudioSyncResult.source`
    可能标出 "channel_sync_report"; 显式声明可避免将来重复修正这个坑。
    """

    APPLY = "apply"
    NONE = "none"


def _offset_samples(sync: Any) -> float | None:
    """从 AudioSyncResult / AudioChannelRef / dict 取 offset_samples。"""
    value = getattr(sync, "offset_samples", None)
    if value is None and isinstance(sync, Mapping):
        value = sync.get("sync_offset_samples", sync.get("offset_samples"))
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _sync_status(sync: Any) -> str:
    raw = getattr(sync, "status", None)
    if raw is None and isinstance(sync, Mapping):
        raw = sync.get("sync_status", sync.get("status"))
    if isinstance(raw, SyncStatus):
        return raw.value
    return str(raw or SyncStatus.NOT_PROCESSED.value)


def _effective_offset(
    sync: Any, application: SyncApplication | str,
) -> tuple[int, float | None]:
    """(整数样本 offset, 原始浮点 offset) —— 只应用已存在的固定整数 offset。

    规则:
      * 只有 `status == success` 且带 offset 才应用 (其余状态按"无修正"处理);
      * `SyncApplication.NONE` -> 一律 0;
      * 浮点 offset 取整用 `rint` 语义 (= `core/sync_fix.shift_stream`), 但
        报告里保留原浮点值, 分数部分作为 residual 记录, **不做**分数修正。
    """
    raw = _offset_samples(sync)
    mode = (
        application.value if isinstance(application, SyncApplication)
        else str(application or SyncApplication.APPLY.value)
    )
    if raw is None or mode != SyncApplication.APPLY.value:
        return 0, raw
    if _sync_status(sync) != SyncStatus.SUCCESS.value:
        # 未测量/失败/已对齐(offset 无意义) -> 不施加任何移位
        return 0, raw
    return int(round(raw)), raw


def source_to_timeline(
    source_sample: int | float, offset_samples: int | float = 0,
) -> int:
    """源样本下标 -> 统一 timeline 样本下标 (**唯一**换算入口)。

        timeline_sample = source_sample - offset_samples
                        = source_sample + shift(delay)

    `offset_samples` 就是 `core.channel_sync` 报告的 `shift_samples`
    (= `rint(delay_samples)`, 正 = 目标轨**晚到**), 语义统一为
    "该 source/channel 在输出 timeline 上被前移的样本数" —— 与
    `core/sync_fix.shift_stream` 的 `out[n] = in[n + shift]` 完全同向。

    方向**不是**从字段名推的, 而是实测确定的 (`work/` 下的一次性验证 +
    `tests/full_autotest.py` Phase 3A 用例):

      * 4×mono 素材, CH2 内容整体晚到 960 样本;
      * `run_channel_sync` 报告 `stream=1 delay_samples=+960 shift_samples=960`;
      * 修正后音频与修正前的互相关 lag = **-960** (即 `after[n] = before[n+960]`),
        晚到的 960 样本被前移掉。

    因此: `offset > 0` (晚到) => 内容在 timeline 上**前移** offset 个样本。
    供 reader / routing / mixer / exporter 共用, 不允许各处自行解释。
    """
    return int(source_sample) - int(offset_samples)


def timeline_to_source(
    timeline_sample: int | float, offset_samples: int | float = 0,
) -> int:
    """统一 timeline 样本下标 -> 源样本下标 (`source_to_timeline` 的逆)。"""
    return int(timeline_sample) + int(offset_samples)


# ---------------------------------------------------------------------------
# Render policy
# ---------------------------------------------------------------------------


class RenderPolicy(str, Enum):
    """多条来源时间轴如何合成一个 render window。

    ================  ==================================================
    取值               语义
    ================  ==================================================
    UNION             **Phase 3A 唯一启用**。取全部参与源 timeline 并集;
                      短 source 缺失区间确定性补静音
    INTERSECTION      只输出所有源都存在的共同区间 (**预留, 未实现**)
    EXPLICIT          由调用方给出 start/end (与 `DurationMode.EXPLICIT`
                      配套; 不做隐式推断)
    ================  ==================================================
    """

    UNION = "union"
    INTERSECTION = "intersection"
    EXPLICIT = "explicit"

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> "RenderPolicy | Any":
        """任意输入 -> 成员 (未知值回退 default, 不抛异常)。"""
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return default
        text = str(value).strip().lower()
        for member in cls:
            if member.value == text or member.name.lower() == text:
                return member
        return default


#: Phase 3A **实际实现**的 render policy。其余取值只能被显式拒绝,
#: 不允许"看起来支持" (INTERSECTION 留待后续阶段, 见模块 docstring)。
_IMPLEMENTED_POLICIES = (RenderPolicy.UNION, RenderPolicy.EXPLICIT)


class DurationMode(str, Enum):
    """render duration 的**来源** (决定是否接受推导)。"""

    DERIVED = "derived"     # 由参与输出来源的 timeline 按 policy 推导
    EXPLICIT = "explicit"   # AudioPlan / 调用方明确给出
    METADATA = "metadata"   # 来自 AudioSource / AudioStream 的 duration 事实


# ---------------------------------------------------------------------------
# 可用样本数
# ---------------------------------------------------------------------------


@dataclass
class SourceAvailability:
    """一个 (source, stream) 的样本可用性事实 (不持有 PCM)。

    ==================  ==============================================
    字段                 语义
    ==================  ==============================================
    declared_samples    规划期**声明**的长度: `nb_frames` / WAV header /
                        `duration × sample_rate`。用于**规划 render window**
    actual_samples      实际解码得到的样本数 (reader 填)。**最终事实**,
                        用于钳制读取并检测 metadata mismatch
    ==================  ==============================================

    §Source 长度比 metadata 更可靠: 二者不一致时**不静默相信 metadata**,
    以 actual 为准, 并记录 `audio_duration_metadata_mismatch`。
    """

    source_id: str
    stream_index: int
    declared_samples: int | None = None
    actual_samples: int | None = None
    declared_source: str | None = None   # nb_frames / duration / wav_header / ...
    notes: list[str] = field(default_factory=list)

    @property
    def effective_samples(self) -> int | None:
        """最终事实: 优先实际解码数, 否则声明数, 都没有 -> None。"""
        if self.actual_samples is not None:
            return max(0, int(self.actual_samples))
        if self.declared_samples is not None:
            return max(0, int(self.declared_samples))
        return None

    @property
    def mismatch(self) -> int | None:
        """(actual - declared); 无两者之一时 None。"""
        if self.actual_samples is None or self.declared_samples is None:
            return None
        return int(self.actual_samples) - int(self.declared_samples)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source_id": self.source_id,
            "stream_index": int(self.stream_index),
        }
        if self.declared_samples is not None:
            data["declared_samples"] = int(self.declared_samples)
        if self.actual_samples is not None:
            data["actual_samples"] = int(self.actual_samples)
        if self.declared_source is not None:
            data["declared_source"] = self.declared_source
        if self.notes:
            data["notes"] = list(self.notes)
        return data


def declared_samples_of_stream(
    stream: AudioStream, sample_rate: int,
) -> tuple[int | None, str | None]:
    """模型/raw 事实 -> (声明样本数, 来源标签)。**不读文件**。

    优先级 (均是"声明", 不是最终事实):

      1. raw `nb_frames` (仅当 raw 里没有 codec_type 之外的容器级歧义时;
         ffprobe 对音频流的 nb_frames 通常就是样本数)
      2. `duration_sec × sample_rate`

    ⚠️ 刻意**不用** `bit_rate`/文件大小反推 duration (§禁止隐式猜测)。
    """
    raw = stream.raw if isinstance(stream.raw, Mapping) else {}
    frames = raw.get("nb_frames")
    try:
        counted = int(str(frames)) if frames not in (None, "") else None
    except (TypeError, ValueError):
        counted = None
    if counted is not None and counted > 0:
        return counted, "nb_frames"
    if stream.duration_sec is not None and sample_rate > 0:
        return samples_from_seconds(stream.duration_sec, sample_rate), "duration"
    return None, None


def apply_plan_durations(
    plan: AudioPlan,
    *,
    sample_rate: int | None = None,
    declared_samples: Mapping[tuple[str, int], int] | None = None,
) -> dict[tuple[str, int], SourceAvailability]:
    """计划里全部 (source, stream) 的声明可用性 (规划期, 不解码)。"""
    out: dict[tuple[str, int], SourceAvailability] = {}
    for source in plan.sources:
        for stream in source.streams:
            rate = int(sample_rate or stream.sample_rate or 0)
            explicit = None
            if declared_samples is not None:
                explicit = declared_samples.get(
                    (source.source_id, stream.stream_index)
                )
            if explicit is not None:
                out[(source.source_id, stream.stream_index)] = SourceAvailability(
                    source_id=source.source_id,
                    stream_index=stream.stream_index,
                    declared_samples=int(explicit),
                    declared_source="caller",
                )
                continue
            count, origin = declared_samples_of_stream(stream, rate)
            out[(source.source_id, stream.stream_index)] = SourceAvailability(
                source_id=source.source_id,
                stream_index=stream.stream_index,
                declared_samples=count,
                declared_source=origin,
            )
    return out


# ---------------------------------------------------------------------------
# 逐声道 timeline 归一
# ---------------------------------------------------------------------------


@dataclass
class ChannelTimeline:
    """一个**参与输出**的源声道在统一 timeline 上的位置 (§统一处理函数)。

        timeline_sample = source_sample - offset_samples

    完整区间 (按声明长度)  = [timeline_start, timeline_end)
    实际可用区间 (按实际解码数) = [available_start, available_end)
    render 区间           = 二者与 render window 的交集
    """

    channel_id: str
    source_id: str
    stream_index: int
    channel_index: int
    audio_position: int | None = None
    sample_rate: int = 0
    offset_samples: int = 0
    raw_offset_samples: float | None = None
    sync_status: str = SyncStatus.NOT_PROCESSED.value
    sync_application: str = SyncApplication.APPLY.value
    declared_samples: int | None = None
    actual_samples: int | None = None
    availability_source: str | None = None
    warnings: list[str] = field(default_factory=list)

    # -- 几何 -------------------------------------------------------------

    @property
    def effective_samples(self) -> int | None:
        if self.actual_samples is not None:
            return max(0, int(self.actual_samples))
        if self.declared_samples is not None:
            return max(0, int(self.declared_samples))
        return None

    @property
    def residual_samples(self) -> float:
        """浮点 offset 的分数部分 (报告用; **不修正**, 无分数插值)。"""
        if self.raw_offset_samples is None:
            return 0.0
        return float(self.raw_offset_samples) - float(self.offset_samples)

    def _bounds(self, length: int | None) -> tuple[int, int] | None:
        if length is None:
            return None
        start = source_to_timeline(0, self.offset_samples)
        return start, start + int(length)

    @property
    def timeline_bounds(self) -> tuple[int, int] | None:
        """该声道在统一 timeline 上的完整区间。

        **以 `effective_samples` 为准** —— 解码前它是声明值, 解码后它是实际
        解码样本数 (最终事实)。这样"声明 metadata 错误"不会决定输出长度:
        `refresh_from_reader()` 一旦写入 actual, 派生窗口随即按实际重算。
        """
        return self._bounds(self.effective_samples)

    @property
    def available_bounds(self) -> tuple[int, int] | None:
        """按实际解码数的真实可用区间 (尚未解码时退化为声明区间)。"""
        return self._bounds(self.effective_samples)

    @property
    def timeline_start(self) -> int | None:
        bounds = self.timeline_bounds
        return None if bounds is None else bounds[0]

    @property
    def timeline_end(self) -> int | None:
        bounds = self.timeline_bounds
        return None if bounds is None else bounds[1]

    def planned_window(
        self, start_sample: int, end_sample: int,
    ) -> tuple[int, int | None]:
        """render window 内该声道的**源样本**读取区间 (source 侧下标)。

        返回 `(read_start_source, read_count)`:

          * `read_start_source` 可能为负 (window 起点落在内容之前) —— 调用方
            必须先补 `-read_start_source` 个静音;
          * `read_count` 为 None 表示长度未知 (回退声明长度);
          * 尾部越界由调用方补静音 (EOF = silence, 不循环)。

        §不循环 / §不复制末样本: 本函数只给出区间, 越界一律静音。
        """
        length = self.effective_samples
        start_source = timeline_to_source(start_sample, self.offset_samples)
        count: int | None = None
        if length is not None:
            count = max(0, int(length) - max(0, start_source))
        return start_source, count

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "channel_id": self.channel_id,
            "source_id": self.source_id,
            "stream_index": int(self.stream_index),
            "channel_index": int(self.channel_index),
            "sample_rate": int(self.sample_rate),
            "offset_samples": int(self.offset_samples),
            "sync_status": self.sync_status,
        }
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.raw_offset_samples is not None:
            data["raw_offset_samples"] = self.raw_offset_samples
        if self.residual_samples:
            data["residual_samples"] = self.residual_samples
        if self.sync_application != SyncApplication.APPLY.value:
            data["sync_application"] = self.sync_application
        if self.declared_samples is not None:
            data["declared_samples"] = int(self.declared_samples)
        if self.actual_samples is not None:
            data["actual_samples"] = int(self.actual_samples)
        if self.availability_source is not None:
            data["availability_source"] = self.availability_source
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


# ---------------------------------------------------------------------------
# AudioTimeline
# ---------------------------------------------------------------------------


@dataclass
class AudioTimeline:
    """统一输出时间轴 —— Phase 3A 时长/EOF 的唯一权威。

    输出最终样本数严格 = `frame_count`; 没有任何下游模块可以改它:
    WAV header 的 `data_size` / `riff_size` 必须与
    `frame_count × output_channel_count × bytes_per_sample` 一致。
    """

    sample_rate: int
    start_sample: int = 0
    end_sample: int = 0
    duration_mode: DurationMode = DurationMode.DERIVED
    render_policy: RenderPolicy = RenderPolicy.UNION
    explicit: bool = False
    channels: list[ChannelTimeline] = field(default_factory=list)
    # output_index -> channel_id (有效映射顺序; 输出声道身份)
    output_channel_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- 事实 -------------------------------------------------------------

    @property
    def frame_count(self) -> int:
        """render 输出帧数 (严格等于最终写出的样本数)。"""
        return max(0, int(self.end_sample) - int(self.start_sample))

    @property
    def duration_seconds(self) -> float:
        return seconds_from_samples(self.frame_count, self.sample_rate)

    @property
    def output_channels(self) -> int:
        return len(self.output_channel_ids)

    @property
    def duration_seconds_from_start(self) -> float:
        return seconds_from_samples(self.end_sample, self.sample_rate)

    def channel(self, channel_id: str) -> ChannelTimeline | None:
        want = str(channel_id)
        return next((c for c in self.channels if c.channel_id == want), None)

    def channel_offsets(self) -> dict[str, int]:
        return {c.channel_id: int(c.offset_samples) for c in self.channels}

    def clips_before(self, channel_id: str) -> bool:
        """该声道是否在 window 起点前就有内容 (需要 trim)。"""
        ct = self.channel(channel_id)
        if ct is None:
            return False
        start = ct.timeline_start
        return start is not None and start < self.start_sample

    def pads_after(self, channel_id: str) -> bool:
        """该声道是否在 window 结束前就 EOF (需要补静音)。"""
        ct = self.channel(channel_id)
        if ct is None:
            return False
        end = ct.timeline_end
        return end is None or end < self.end_sample

    def output_channel_id(self, output_index: int) -> str | None:
        if 0 <= output_index < len(self.output_channel_ids):
            return self.output_channel_ids[output_index]
        return None

    def refresh_actuals(
        self, availability: Mapping[tuple[str, int], Any] | None,
    ) -> list[dict[str, Any]]:
        """用**实际解码样本数** (最终事实) 更新逐声道可用性 + mismatch 记录。

        只更新 `actual_samples` / `availability_source` / `warnings`, **不改**
        render window —— window 是规划期由声明 duration 定下的契约, 输出样本数
        必须严格等于 `frame_count`。实际素材短于声明区间时, 缺口按 §EOF 策略
        补静音, 同时把 `audio_duration_metadata_mismatch` 记下来。

        返回 mismatch 问题清单 (调用方决定落日志/报告)。
        """
        mismatches: list[dict[str, Any]] = []
        if not availability:
            return mismatches
        for ct in self.channels:
            got = availability.get((ct.source_id, ct.stream_index))
            if got is None:
                continue
            actual = getattr(got, "actual_samples", None)
            declared = getattr(got, "declared_samples", None)
            origin = getattr(got, "declared_source", None)
            if isinstance(got, Mapping):
                actual = got.get("actual_samples", actual)
                declared = got.get("declared_samples", declared)
                origin = got.get("declared_source", origin)
            if actual is None:
                continue
            ct.actual_samples = int(actual)
            if declared is not None and ct.declared_samples is None:
                ct.declared_samples = int(declared)
                ct.availability_source = origin
            delta = (
                int(actual) - int(ct.declared_samples)
                if ct.declared_samples is not None else None
            )
            if delta is not None and abs(delta) > 0:
                ct.warnings.append(
                    f"audio_duration_metadata_mismatch: decoded {actual} "
                    f"samples but metadata declares {ct.declared_samples} "
                    f"({delta:+d}); actual decoded EOF is authoritative"
                )
                mismatches.append({
                    "reason": REASON_AUDIO_DURATION_METADATA_MISMATCH,
                    "channel_id": ct.channel_id,
                    "source_id": ct.source_id,
                    "stream_index": int(ct.stream_index),
                    "declared_samples": int(ct.declared_samples),
                    "actual_samples": int(actual),
                    "delta_samples": int(delta),
                    "detail": ct.warnings[-1],
                })
            bounds = ct.available_bounds
            if bounds is not None and bounds[1] < self.end_sample:
                note = (
                    f"{ct.channel_id}: available material ends at timeline "
                    f"{bounds[1]} < render end {self.end_sample} — "
                    "remaining output padded with deterministic silence"
                )
                if note not in self.warnings:
                    self.warnings.append(note)
        return mismatches

    def refresh_from_reader(
        self,
        reader: Any,
        *,
        plan: AudioPlan | None = None,
        tolerance_samples: int = 1,
        sync_application: SyncApplication | str | None = None,
    ) -> list[dict[str, Any]]:
        """解码后校正 timeline: 实际解码 EOF 才是最终事实 (§Source 更可靠)。

        * 更新逐声道 `actual_samples` 并记录 `audio_duration_metadata_mismatch`;
        * **派生窗口** (`duration_mode=DERIVED`) 时用实际样本数**重算**
          render window —— 声明 metadata 错误不得决定输出长度;
        * **显式窗口** (EXPLICIT, 调用方指定) 保持不动, 缺口按 §EOF 补静音。

        `sync_application` 必须与首次解析时一致 (默认沿用本 timeline 的
        offset 使用情况), 否则重算会改变 offset 语义。
        """
        availability = (
            reader.availability()
            if callable(getattr(reader, "availability", None)) else reader
        )
        mismatches = self.refresh_actuals(availability)
        if self.duration_mode is not DurationMode.DERIVED or plan is None:
            return mismatches
        if not any(
            abs(int(m.get("delta_samples") or 0)) > int(tolerance_samples)
            for m in mismatches
        ):
            return mismatches
        mode = (
            sync_application
            if sync_application is not None
            else (
                SyncApplication.APPLY
                if any(c.offset_samples for c in self.channels)
                else SyncApplication.NONE
            )
        )
        try:
            refreshed = resolve_timeline(
                plan,
                availability=availability,
                render_policy=self.render_policy,
                sync_application=mode,
                sample_rate=self.sample_rate,
            )
        except AudioRenderError as exc:      # 重算失败 -> 保留原窗口 (确定性)
            self.warnings.append(
                f"timeline re-resolution after decode failed ({exc}); "
                "keeping the declared render window"
            )
            return mismatches
        for old, new in zip(self.channels, refreshed.channels):
            # 重算不得丢掉 mismatch 记录 (report 依赖它)
            new.warnings = list(old.warnings) + [
                w for w in new.warnings if w not in old.warnings
            ]
        refreshed.warnings = list(self.warnings) + [
            w for w in refreshed.warnings if w not in self.warnings
        ]
        refreshed.notes.append(
            "render window re-derived from actual decoded sample counts "
            "(metadata declared a different duration)"
        )
        self.start_sample = refreshed.start_sample
        self.end_sample = refreshed.end_sample
        self.channels = refreshed.channels
        self.output_channel_ids = refreshed.output_channel_ids
        self.notes = refreshed.notes
        self.warnings = refreshed.warnings
        return mismatches

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "sample_rate": int(self.sample_rate),
            "start_sample": int(self.start_sample),
            "end_sample": int(self.end_sample),
            "duration_samples": int(self.frame_count),
            "duration_seconds": self.duration_seconds,
            "duration_mode": self.duration_mode.value,
            "render_policy": self.render_policy.value,
            "explicit": bool(self.explicit),
            "output_channels": int(self.output_channels),
            "output_channel_ids": list(self.output_channel_ids),
            "channels": [c.to_dict() for c in self.channels],
        }
        if self.notes:
            data["notes"] = list(self.notes)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        return data

    def summary(self) -> dict[str, Any]:
        """紧凑摘要 (日志/测试/报告用)。"""
        return {
            "sample_rate": int(self.sample_rate),
            "start_sample": int(self.start_sample),
            "end_sample": int(self.end_sample),
            "duration_samples": int(self.frame_count),
            "duration_seconds": round(self.duration_seconds, 6),
            "duration_mode": self.duration_mode.value,
            "render_policy": self.render_policy.value,
            "output_channels": int(self.output_channels),
            "offsets": self.channel_offsets(),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# 解析: AudioPlan + 可用性 -> AudioTimeline
# ---------------------------------------------------------------------------


def _mapping_refs(plan: AudioPlan) -> list[dict[str, Any]]:
    """有效映射 (显式优先, 否则由选择顺序派生) —— 复用 Phase 2 定义。"""
    return [dict(e) for e in effective_mapping(plan)]


def _used_sources(plan: AudioPlan, refs: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for entry in refs:
        sid = str(entry.get("source_id") or "")
        if sid and sid not in out:
            out.append(sid)
    return out


def _channel_lookup(plan: AudioPlan) -> dict[str, AudioChannel]:
    return {c.id: c for c in plan.all_channels()}


def _resolve_availability(
    plan: AudioPlan,
    source_id: str,
    stream_index: int,
    availability: Mapping[tuple[str, int], Any] | None,
    sample_rate: int,
) -> SourceAvailability:
    """(source, stream) -> SourceAvailability (缺失时回退模型声明)。"""
    if availability:
        got = availability.get((source_id, stream_index))
        if got is not None:
            if isinstance(got, SourceAvailability):
                return got
            if isinstance(got, Mapping):
                return SourceAvailability(
                    source_id=source_id,
                    stream_index=int(stream_index),
                    declared_samples=got.get("declared_samples"),
                    actual_samples=got.get("actual_samples"),
                    declared_source=got.get("declared_source"),
                )
            # 直接给样本数
            try:
                return SourceAvailability(
                    source_id=source_id,
                    stream_index=int(stream_index),
                    declared_samples=int(got),
                    declared_source="caller",
                )
            except (TypeError, ValueError):
                pass
    source = plan.source(source_id)
    stream = source.stream(stream_index) if source is not None else None
    if stream is None:
        return SourceAvailability(
            source_id=source_id, stream_index=int(stream_index)
        )
    count, origin = declared_samples_of_stream(stream, sample_rate)
    return SourceAvailability(
        source_id=source_id,
        stream_index=int(stream_index),
        declared_samples=count,
        declared_source=origin,
    )


def _explicit_window(
    plan: AudioPlan,
    sample_rate: int,
    start_seconds: float | None,
    duration_seconds: float | None,
    end_seconds: float | None,
) -> tuple[int, int, DurationMode, bool]:
    """显式 start/end/duration -> window 样本数 (AudioPlan 字段优先)。"""
    plan_start = start_seconds
    plan_duration = duration_seconds
    if plan_start is None and plan.output_sample_rate is not None:
        # AudioPlan 没有 start 字段; output_sample_rate 只是重采样预留, 不参与。
        plan_start = None
    base_start = 0 if plan_start is None else samples_from_seconds(
        float(plan_start), sample_rate
    )
    if plan_duration is not None:
        frames = samples_from_seconds(float(plan_duration), sample_rate)
        return base_start, base_start + frames, DurationMode.EXPLICIT, True
    if end_seconds is not None:
        end = samples_from_seconds(float(end_seconds), sample_rate)
        return base_start, end, DurationMode.EXPLICIT, True
    raise AudioRenderError(
        REASON_AUDIO_DURATION_EXPLICIT_INVALID,
        "RenderPolicy.EXPLICIT requires an explicit duration or end",
    )


def resolve_timeline(
    plan: AudioPlan,
    *,
    availability: Mapping[tuple[str, int], Any] | None = None,
    render_policy: RenderPolicy | str = RenderPolicy.UNION,
    explicit_duration_seconds: float | None = None,
    explicit_start_seconds: float | None = None,
    explicit_end_seconds: float | None = None,
    sync_application: SyncApplication | str = SyncApplication.APPLY,
    sample_rate: int | None = None,
) -> AudioTimeline:
    """AudioPlan -> AudioTimeline (时长/EOF/offset 归一, **不读 PCM**)。

    解析顺序 (§Render Duration 默认策略):

      1. 显式 duration (`explicit_duration_seconds` / `explicit_end_seconds`
         / `RenderPolicy.EXPLICIT`);
      2. `AudioSource` / `AudioStream` 的声明 duration (用于**规划** window);
      3. 由参与输出的源 timeline 按 `render_policy` 推导 (Phase 3A: UNION);
      4. 仍无法确定 -> `audio_duration_unknown` (拒绝, 不猜)。

    失败一律抛 `AudioRenderError` (不"尽可能输出一个结果")。
    """
    policy = (
        render_policy if isinstance(render_policy, RenderPolicy)
        else RenderPolicy.coerce(render_policy, RenderPolicy.UNION)
    )
    apply_mode = (
        sync_application if isinstance(sync_application, SyncApplication)
        else SyncApplication(str(sync_application or SyncApplication.APPLY.value))
    )

    refs = _mapping_refs(plan)
    if not refs:
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID,
            "audio plan has no effective mapping (nothing to render)",
        )
    used = _used_sources(plan, refs)
    by_id = _channel_lookup(plan)

    # -- 采样率: 参与 render 的来源必须一致 (不 resample) ------------------
    rates: dict[str, int] = {}
    for source_id in used:
        source = plan.source(source_id)
        stream_rates = sorted({
            int(s.sample_rate or 0) for s in
            (source.streams if source is not None else [])
        })
        nonzero = [r for r in stream_rates if r > 0]
        rate = int(sample_rate or 0)
        if not rate:
            rate = nonzero[0] if len(nonzero) == 1 else 0
        if not rate:
            rate = next(
                (int(c.sample_rate or 0) for c in plan.all_channels()
                 if c.source_id == source_id),
                0,
            )
        rates[source_id] = rate

    unknown_rate = [s for s, r in rates.items() if r <= 0]
    if unknown_rate:
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID,
            f"unknown sample rate for source(s): {unknown_rate}",
        )
    distinct = sorted({r for r in rates.values()})
    if len(distinct) > 1:
        detail = ", ".join(f"{s}={rates[s]}" for s in used)
        raise AudioRenderError(
            REASON_AUDIO_SAMPLE_RATE_MISMATCH,
            f"participating sources must share one sample rate "
            f"(no resampling in this phase): {detail}",
        )
    rate = distinct[0]

    # -- 逐声道 timeline 归一 --------------------------------------------
    channels: list[ChannelTimeline] = []
    output_ids: list[str] = []
    for output_index, entry in enumerate(refs):
        cid = str(entry.get("source_channel_id") or "")
        channel = by_id.get(cid)
        source_id = str(
            entry.get("source_id")
            or (channel.source_id if channel is not None else "")
        )
        stream_index = int(
            entry.get("stream_index")
            if entry.get("stream_index") is not None
            else (channel.stream_index if channel is not None else 0)
        )
        channel_index = int(
            entry.get("channel_index")
            if entry.get("channel_index") is not None
            else (channel.channel_index if channel is not None else 0)
        )
        output_ids.append(cid)
        sync = channel.sync if channel is not None else None
        offset, raw_offset = _effective_offset(sync, apply_mode)
        avail = _resolve_availability(
            plan, source_id, stream_index, availability, rate
        )
        source = plan.source(source_id)
        stream = source.stream(stream_index) if source is not None else None
        ct = ChannelTimeline(
            channel_id=cid,
            source_id=source_id,
            stream_index=stream_index,
            channel_index=channel_index,
            audio_position=(
                stream.audio_position if stream is not None else None
            ) if (stream is not None and stream.audio_position is not None)
            else (
                entry.get("audio_position")
                if entry.get("audio_position") is not None else None
            ),
            sample_rate=rate,
            offset_samples=offset,
            raw_offset_samples=raw_offset,
            sync_status=_sync_status(sync),
            sync_application=(
                apply_mode.value if offset else SyncApplication.NONE.value
            ),
            declared_samples=avail.declared_samples,
            actual_samples=avail.actual_samples,
            availability_source=avail.declared_source,
        )
        if abs(ct.residual_samples) > 0.0:
            ct.warnings.append(
                f"fractional offset {ct.residual_samples:+.4f} samples "
                "kept as residual (integer shift only, no interpolation)"
            )
        if offset == 0 and raw_offset is not None \
                and _sync_status(sync) == SyncStatus.SUCCESS.value:
            ct.warnings.append(
                f"sync offset {raw_offset:+.4f} rounds to 0 samples"
            )
        channels.append(ct)

    # -- render window ----------------------------------------------------
    explicit = explicit_duration_seconds is not None \
        or explicit_end_seconds is not None \
        or policy is RenderPolicy.EXPLICIT
    if policy not in _IMPLEMENTED_POLICIES:
        raise AudioRenderError(
            REASON_AUDIO_TIMELINE_INVALID,
            f"render policy {policy.value!r} is reserved but not implemented "
            f"in this phase (implemented: "
            f"{[p.value for p in _IMPLEMENTED_POLICIES]})",
        )

    notes: list[str] = []
    warnings: list[str] = []
    if explicit:
        start_sample, end_sample, mode, flag = _explicit_window(
            plan, rate, explicit_start_seconds,
            explicit_duration_seconds, explicit_end_seconds,
        )
    else:
        bounds = [
            b for b in (c.timeline_bounds for c in channels) if b is not None
        ]
        missing = [c.channel_id for c in channels if c.timeline_bounds is None]
        if missing and not bounds:
            raise AudioRenderError(
                REASON_AUDIO_DURATION_UNKNOWN,
                f"no duration available for any output channel "
                f"(missing: {missing[:6]}{'…' if len(missing) > 6 else ''})",
                location=missing[0] if missing else None,
            )
        if missing:
            warnings.append(
                f"{len(missing)} output channel(s) without declared duration "
                f"({missing[:6]}{'…' if len(missing) > 6 else ''}) — "
                "render window derived from the remaining channels"
            )
        if policy is RenderPolicy.UNION:
            start_sample = min(b[0] for b in bounds)
            end_sample = max(b[1] for b in bounds)
        else:  # INTERSECTION (预留; 上面已拒绝, 这里仅保持穷尽性)
            start_sample = max(b[0] for b in bounds)
            end_sample = min(b[1] for b in bounds)
        mode = DurationMode.DERIVED
        flag = False
        notes.append(
            f"render window derived from {len(bounds)} declared channel "
            f"timeline(s) via {policy.value.upper()}"
        )

    if end_sample < start_sample:
        raise AudioRenderError(
            REASON_AUDIO_NEGATIVE_RENDER_DURATION,
            f"render window is empty/negative: start={start_sample} "
            f"end={end_sample}",
        )
    if end_sample < 0:
        raise AudioRenderError(
            REASON_AUDIO_NEGATIVE_RENDER_DURATION,
            f"render window ends before timeline origin: end={end_sample}",
        )

    timeline = AudioTimeline(
        sample_rate=rate,
        start_sample=int(start_sample),
        end_sample=int(end_sample),
        duration_mode=mode,
        render_policy=policy,
        explicit=bool(flag),
        channels=channels,
        output_channel_ids=output_ids,
        notes=notes,
        warnings=warnings,
    )
    return timeline


# 便捷别名 (与项目既有 build_* / *_of 风格一致)
render_timeline = resolve_timeline


def availability_from_reader(
    reader: Any, sample_rate: int,
) -> dict[tuple[str, int], SourceAvailability]:
    """从 PCM Reader 收集实际解码样本数 (§actual decoded EOF = 最终事实)。

    reader 需提供 `describe()` -> [{source_id, stream_index,
    declared_samples, actual_samples}, …] (见 core.audio_pcm)。
    """
    out: dict[tuple[str, int], SourceAvailability] = {}
    describe = getattr(reader, "describe", None)
    if not callable(describe):
        return out
    for row in describe():
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("source_id")), int(row.get("stream_index") or 0))
        out[key] = SourceAvailability(
            source_id=key[0],
            stream_index=key[1],
            declared_samples=row.get("declared_samples"),
            actual_samples=row.get("actual_samples"),
            declared_source=row.get("declared_source"),
        )
    return out


def duration_mismatch_issues(
    timeline: AudioTimeline, tolerance_samples: int = 1,
) -> list[dict[str, Any]]:
    """timeline 里所有"声明 vs 实际"不一致 (稳定 reason code)。

    不修改 timeline; 调用方决定是 warning 还是 error。Phase 3A 的处理是
    **以 actual 为准 + 记录问题** (绝不静默相信 metadata)。
    """
    issues: list[dict[str, Any]] = []
    for ct in timeline.channels:
        if ct.declared_samples is None or ct.actual_samples is None:
            continue
        delta = int(ct.actual_samples) - int(ct.declared_samples)
        if abs(delta) <= int(tolerance_samples):
            continue
        issues.append({
            "reason": REASON_AUDIO_DURATION_METADATA_MISMATCH,
            "channel_id": ct.channel_id,
            "source_id": ct.source_id,
            "stream_index": int(ct.stream_index),
            "declared_samples": int(ct.declared_samples),
            "actual_samples": int(ct.actual_samples),
            "delta_samples": delta,
            "detail": (
                f"{ct.channel_id}: decoded {ct.actual_samples} samples but "
                f"metadata declares {ct.declared_samples} "
                f"({delta:+d}) — actual decoded EOF is authoritative"
            ),
        })
    return issues
