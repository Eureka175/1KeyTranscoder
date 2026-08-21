"""Minimal encoder-backend boundary.

Only x265 is implemented now. NVENC/QSV/VCE backends can be added later
by implementing this protocol against the same core models
(SourceInfo / SourceClassification / EffectiveParams) without touching
x265-specific code or the orchestration layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from core.models import EffectiveParams


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
    ) -> tuple[list[str], dict[str, Any]]:
        """
        Build the full FFmpeg command for one source file.

        `effective` is already fully resolved by core.scaling; backends
        must NOT recalculate scaling, classification, or bitrate rules.
        Returns (argv, effective-parameter dict for logging).
        """
        ...
