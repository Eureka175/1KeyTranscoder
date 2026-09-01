"""Minimal encoder-backend boundary.

Only x265 is implemented now. NVENC/QSV/VCE backends can be added later
by implementing this protocol against the same core models
(SourceInfo / SourceClassification / EffectiveParams) without touching
x265-specific code or the orchestration layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from core.models import EffectiveParams, SourceInfo


_CHROMA_BASE = {
    "4:2:0": "yuv420p",
    "4:2:2": "yuv422p",
    "4:4:4": "yuv444p",
    "mono": "gray",
}


def ffmpeg_pix_fmt(src: SourceInfo) -> str:
    """
    Map SourceInfo bit depth + chroma to an FFmpeg pixel-format name.

    This is deliberately codec-agnostic: it uses the decoded chroma
    subsampling and bit depth, not the source codec (h264/hevc/etc.).
    Unknown/unsupported combinations fall back to the legacy default
    yuv420p10le so existing behavior is preserved.
    """
    if src.bit_depth <= 0 or src.chroma not in _CHROMA_BASE:
        return "yuv420p10le"

    base = _CHROMA_BASE[src.chroma]
    if src.bit_depth > 8:
        return f"{base}{src.bit_depth}le"
    return base


class EncoderBackend(Protocol):
    """Contract every encoder backend must satisfy."""

    name: str

    # Encoder parameter namespace: profile JSON key -> parameter name,
    # in canonical order. Consumed by core.scaling.ScalingEngine.
    param_order: dict[str, str]

    def format_fixed(
        self,
        key: str,
        value: Any,
        fps: float,
    ) -> str | None:
        """Format a fixed (unscaled) value in this encoder's syntax."""
        ...

    def build_command(
        self,
        ffmpeg: Path,
        src: Path,
        part_dst: Path,
        profile: dict[str, Any],
        effective: EffectiveParams,
        video_stream_count: int,
        src_info: SourceInfo,
    ) -> tuple[list[str], dict[str, Any]]:
        """
        Build the full FFmpeg command for one source file.

        `effective` is already fully resolved by core.scaling; backends
        must NOT recalculate scaling, classification, or bitrate rules.
        `src_info` supplies the original source format for encoder-specific
        decisions such as output pixel format.
        Returns (argv, effective-parameter dict for logging).
        """
        ...
