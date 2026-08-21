"""Typed internal models and shared domain constants.

These types are the data contract between the pipeline layers:

    core.probe             -> SourceInfo
    core.source_classifier -> SourceClassification
    core.scaling           -> EffectiveParams (ScalingContext + report)
    encoders.*             -> consume EffectiveParams

Dependency rule: this module imports nothing from the project. No module
in core/ or encoders/ may import x265_archive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRESETS = ("UHQ", "HQ", "SMALL", "FAST")

# Source-efficiency classes produced by SourceClassifier.
SOURCE_CLASSES = (
    "HIGH_BITRATE_LONG_GOP",
    "NORMAL_LONG_GOP",
    "LOW_BITRATE_LONG_GOP",
    "INTRA_LIKE",
)


@dataclass(frozen=True)
class StreamBrief:
    index: int
    codec_type: str
    codec_name: str


@dataclass(frozen=True)
class SourceInfo:
    """
    Unified internal source representation consumed by the classifier
    and the scaling engine. Built by the adapter in core.probe on top
    of probe_source() output; the raw probe dicts are retained so
    backward-compatible CSV reporting keeps working.
    """
    path: Path
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    fps: float
    r_frame_rate: str
    avg_frame_rate: str
    codec: str
    profile: str
    pix_fmt: str
    bit_depth: int
    chroma: str
    ob_kbps: float
    video_bitrate_kbps: float
    video_stream_count: int
    stream_info: tuple[StreamBrief, ...]
    raw_summary: dict[str, Any] = field(
        default_factory=dict, compare=False,
    )
    raw_streams: tuple[dict[str, Any], ...] = field(
        default=(), compare=False,
    )


@dataclass(frozen=True)
class SourceClassification:
    """Result of metadata-only source-efficiency classification."""
    source_class: str
    normalized_ob: float
    evidence: str


@dataclass(frozen=True)
class ScalingContext:
    """Generic source factors relative to the 4K60 reference."""
    reference_width: int
    reference_height: int
    reference_fps: float
    spatial_factor: float
    temporal_factor: float
    pixel_rate_factor: float
    source_class: str
    normalized_ob: float


@dataclass
class EffectiveParams:
    """
    Final, fully-resolved encoder parameters plus the scaling report
    (`audit`) describing how every scaled value was produced. Command
    construction consumes `values` verbatim and never recalculates.
    """
    values: dict[str, str]
    audit: dict[str, dict[str, Any]]
    context: ScalingContext

    # Convenience accessors so reporting code does not have to reach
    # into the context explicitly.
    @property
    def source_class(self) -> str:
        return self.context.source_class

    @property
    def normalized_ob(self) -> float:
        return self.context.normalized_ob

    @property
    def spatial_factor(self) -> float:
        return self.context.spatial_factor

    @property
    def temporal_factor(self) -> float:
        return self.context.temporal_factor

    @property
    def pixel_rate_factor(self) -> float:
        return self.context.pixel_rate_factor
