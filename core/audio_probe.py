"""Audio probe adapter (v0.7.1 Phase 1) — Probe 层.

职责单一: **FFprobe JSON -> AudioStream 模型**。不碰算法, 不构造命令,
不读文件 (调用方把已有的 streams 传进来), 因此可在单测里用合成 dict 直接
覆盖 T1–T8 全部输入形态。

    core.probe.probe_source()       -> (summary, raw streams)   [既有, 未改语义]
    core.audio_probe.build_audio_streams()  -> [AudioStream]   [本模块]
    core.audio_probe.audio_probe_of()       -> AudioProbeResult [本模块]

`probe_source()` 自 v0.7.1 起额外请求 `stream_tags` (language/title/…),
但只**新增** `tags` 键, 既有键与 CSV 字段白名单完全不变 —— 旧路径行为
不受影响 (§14)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .audio_models import (
    AUDIO_MODEL_VERSION,
    AudioPlan,
    AudioSource,
    AudioSourceType,
    AudioStream,
    AudioTrack,
    AudioTrackBuilder,
    DEFAULT_SOURCE_ID,
    TrackBuildMode,
    build_audio_streams,
    build_plan,
    build_source,
)

__all__ = [
    "AudioProbeResult",
    "audio_probe_external",
    "audio_probe_from_file",
    "audio_probe_of",
    "audio_streams_from_probe",
    "build_audio_plan",
    "build_audio_streams",
    "build_audio_streams_from_raw",
    "build_audio_tracks",
    "build_audio_tracks_from_probe",
    "probe_has_audio",
]


@dataclass
class AudioProbeResult:
    """一次探测得到的音频视图 (纯数据容器, 无执行逻辑)。

    v0.7.1 Phase 2: 可选地带 `source` (AudioSource) 与 `audio_plan`
    (AudioPlan) —— 单来源探测因此也能直接进入 source-aware 的
    selection / mapping 流程, 而不必让调用方手工拼装模型。
    """

    streams: list[AudioStream] = field(default_factory=list)
    model_version: int = AUDIO_MODEL_VERSION
    source_id: str = DEFAULT_SOURCE_ID
    source_type: AudioSourceType = AudioSourceType.MEDIA
    path: str | None = None
    source: AudioSource | None = None
    audio_plan: AudioPlan | None = None

    # -- 来源 -------------------------------------------------------------

    def to_source(self) -> AudioSource:
        """把本次探测结果包装成 AudioSource (同 id 已存在则原样返回)。"""
        if self.source is not None:
            return self.source
        for stream in self.streams:
            stream.source_id = self.source_id
        self.source = build_source(
            self.source_id,
            self.streams,
            source_type=self.source_type,
            path=self.path,
        )
        return self.source

    @property
    def stream_indices(self) -> list[int]:
        return [s.stream_index for s in self.streams]

    @property
    def stream_count(self) -> int:
        return len(self.streams)

    @property
    def channel_count(self) -> int:
        return sum(s.channel_count for s in self.streams)

    @property
    def is_multi_mono(self) -> bool:
        """是否"多条独立单声道流"布局 (channel_sync 的适用形态)。"""
        return (
            len(self.streams) > 1
            and all(s.channel_count == 1 for s in self.streams)
        )

    @property
    def sample_rates(self) -> list[int]:
        """去重升序的采样率集合 (含 0 = 不可知; 不猜测)。"""
        return sorted({s.sample_rate for s in self.streams})

    @property
    def layouts(self) -> list[str]:
        return [s.channel_layout for s in self.streams]

    @property
    def has_unknown_layout(self) -> bool:
        """是否存在未能可靠判定布局的流 (§10 情况 F 的正常状态)。"""
        return any(not s.layout_verified for s in self.streams)

    def builder(self) -> AudioTrackBuilder:
        return AudioTrackBuilder(self.streams, source_id=self.source_id)

    def plan(
        self,
        mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
        **kwargs: Any,
    ) -> AudioPlan:
        """单来源计划 (全选 + 保留原始); 带 sources, 可直接进 planning 层。"""
        plan = build_plan(
            self.streams, mode=mode, source_id=self.source_id, **kwargs
        )
        plan.sources = [self.to_source()]
        return plan

    def tracks(
        self,
        mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    ) -> list[AudioTrack]:
        return build_audio_tracks(
            self.streams, mode=mode, source_id=self.source_id
        )

    def tracks_for_sync(self) -> list[AudioTrack]:
        """channel_sync 的报告是**逐音频流**的, 因此这里用 per-stream 映射。"""
        return build_audio_tracks(
            self.streams,
            mode=TrackBuildMode.PER_STREAM,
            source_id=self.source_id,
        )

    def summary(self) -> dict[str, Any]:
        """紧凑的调试/日志摘要 (JSON-compatible)。"""
        return {
            "model_version": self.model_version,
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "audio_stream_count": self.stream_count,
            "audio_channel_count": self.channel_count,
            "sample_rates": self.sample_rates,
            "layouts": self.layouts,
            "has_unknown_layout": self.has_unknown_layout,
            "is_multi_mono": self.is_multi_mono,
            "streams": [
                {
                    "stream_index": s.stream_index,
                    "audio_position": s.audio_position,
                    "codec_name": s.codec_name,
                    "channel_count": s.channel_count,
                    "channel_layout": s.channel_layout,
                    "layout_source": s.layout_source,
                    "sample_rate": s.sample_rate,
                    "sample_format": s.sample_format.value,
                }
                for s in self.streams
            ],
        }


def _streams_of(payload: Any) -> list[Any]:
    """接受 raw stream 列表 / probe_source() 元组 / {"streams": [...]} 三种输入。"""
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        raw = payload.get("streams")
        return list(raw) if isinstance(raw, list) else []
    if isinstance(payload, tuple):
        # probe_source() 的 (summary dict, raw stream list) 结构
        if len(payload) == 2 and isinstance(payload[1], list):
            return list(payload[1])
        for item in payload:
            if isinstance(item, list):
                return list(item)
        return []
    if isinstance(payload, list):
        return list(payload)
    return []


def build_audio_streams_from_raw(
    streams: Iterable[Any],
) -> list[AudioStream]:
    """raw stream 列表 -> AudioStream 列表 (非音频流被忽略)。"""
    return build_audio_streams(streams)


def audio_streams_from_probe(
    payload: Any,
) -> list[AudioStream]:
    """probe_source() 结果 / {"streams": [...]} / raw 列表 -> AudioStream。"""
    return build_audio_streams(_streams_of(payload))


def probe_has_audio(payload: Any) -> bool:
    return bool(audio_streams_from_probe(payload))


def build_audio_tracks(
    streams: Iterable[Any],
    *,
    mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    source_id: str = DEFAULT_SOURCE_ID,
) -> list[AudioTrack]:
    """AudioStream / raw stream (可混合) -> AudioTrack 列表。"""
    return AudioTrackBuilder(list(streams), source_id=source_id).tracks(mode)


def build_audio_tracks_from_probe(
    payload: Any,
    *,
    mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    source_id: str = DEFAULT_SOURCE_ID,
) -> list[AudioTrack]:
    """probe_source() 结果 -> AudioTrack 列表。"""
    return build_audio_tracks(
        audio_streams_from_probe(payload), mode=mode, source_id=source_id
    )


def build_audio_plan(
    payload: Any,
    *,
    mode: TrackBuildMode | str = TrackBuildMode.PER_STREAM,
    select_all: bool = True,
    source_id: str = DEFAULT_SOURCE_ID,
    source_type: AudioSourceType | str = AudioSourceType.MEDIA,
    path: Any = None,
    **kwargs: Any,
) -> AudioPlan:
    """probe_source() 结果 -> AudioPlan (默认全选 + 保留原始 = 旧行为)。

    给出 `source_id` / `path` 时计划会带上对应的 AudioSource, 从而可以直接
    进入 Phase 2 的 selection / mapping (多来源用
    `core.audio_plan.source_plan`)。
    """
    streams = audio_streams_from_probe(payload)
    plan = build_plan(
        streams,
        mode=mode,
        select_all=select_all,
        source_id=source_id,
        **kwargs,
    )
    if source_id != DEFAULT_SOURCE_ID or path is not None:
        plan.sources = [
            build_source(
                source_id,
                streams,
                source_type=source_type,
                path=path,
            )
        ]
    return plan


def audio_probe_of(
    payload: Any = None,
    *,
    streams: Iterable[Any] | None = None,
    source_id: str = DEFAULT_SOURCE_ID,
    source_type: AudioSourceType | str = AudioSourceType.MEDIA,
    path: Any = None,
) -> AudioProbeResult:
    """建 AudioProbeResult; 传 probe_source() 结果或 raw stream 列表皆可。"""
    source = payload if payload is not None else streams
    return AudioProbeResult(
        streams=build_audio_streams(
            _streams_of(source), source_id=source_id
        ),
        source_id=str(source_id),
        source_type=(
            AudioSourceType.coerce(source_type, AudioSourceType.MEDIA)
            or AudioSourceType.MEDIA
        ),
        path=str(path) if path is not None else None,
    )


def audio_probe_from_file(
    ffprobe: Path,
    src: Path,
    *,
    source_id: str | None = None,
    source_type: AudioSourceType | str = AudioSourceType.MEDIA,
) -> AudioProbeResult:
    """真实探测入口 (走既有 core.probe.probe_source, 不新增 ffprobe 调用)。

    仅作便捷包装: 需要探针的调用方本来就要拿 summary/streams; 该函数
    不缓存、不复制探测逻辑, 也不改变 probe_source 的返回结构。

    `source_id` 缺省时用文件名主干 (来源身份可读), 显式传入优先。
    """
    from .probe import probe_source

    _summary, streams = probe_source(ffprobe, src)
    resolved_id = str(source_id) if source_id else _default_source_id(src)
    return AudioProbeResult(
        streams=build_audio_streams(streams, source_id=resolved_id),
        source_id=resolved_id,
        source_type=(
            AudioSourceType.coerce(source_type, AudioSourceType.MEDIA)
            or AudioSourceType.MEDIA
        ),
        path=str(src),
    )


def _default_source_id(path: Path) -> str:
    """文件名主干 -> source_id; 空则退回 "default" (不臆造内容)。"""
    stem = Path(path).stem.strip()
    return stem or DEFAULT_SOURCE_ID


def audio_probe_external(
    ffprobe: Path,
    src: Path,
    *,
    source_id: str | None = None,
    source_type: AudioSourceType | str = AudioSourceType.EXTERNAL,
) -> AudioProbeResult:
    """外挂音频文件探测入口 (v0.8.0) —— **可以没有视频流**。

    与 `audio_probe_from_file()` 的唯一区别是底层 ffprobe 入口:
    `probe_source()` 对没有视频流的文件直接报 "No video stream found."
    (它是**视频**生产探针, 其 summary 的每个消费者都需要视频事实), 而
    外挂 WAV/AAC/Opus 本来就没有视频流。

    因此本函数走 `core.probe.probe_streams()` —— 同一个 `-show_entries`
    字段表、同一套 raw stream 结构, 只是不要求视频流。**没有第二套
    codec probe, 也没有第二套流字典形状**。

    `source_id` 缺省时用**文件名主干**; 外挂来源的完整身份由调用方决定
    (见 `core.audio_external`, 它用带扩展名的文件名以避免同名不同格式
    的两个文件碰撞)。
    """
    from .probe import probe_streams

    _fmt, streams = probe_streams(ffprobe, src)
    resolved_id = str(source_id) if source_id else _default_source_id(src)
    return AudioProbeResult(
        streams=build_audio_streams(streams, source_id=resolved_id),
        source_id=resolved_id,
        source_type=(
            AudioSourceType.coerce(source_type, AudioSourceType.EXTERNAL)
            or AudioSourceType.EXTERNAL
        ),
        path=str(src),
    )