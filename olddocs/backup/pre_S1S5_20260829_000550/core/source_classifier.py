"""Metadata-only source-efficiency classification.

No frame-type statistics, no ffprobe -show_frames. For H.264/HEVC-like
sources the normalized OB metric is only a heuristic efficiency
indicator, not GOP detection:

    normalized_ob = OB_bits_per_sec / (width * height * fps)

(bits per pixel-frame).

INTRA_LIKE is a future-capable class: it currently triggers only on
intra-only codec families (e.g. ProRes/DNxHD, configurable) because no
genuine camera-originated XAVC S-I / All-I calibration source exists
yet. H.264/HEVC sources are never classified INTRA_LIKE in this phase.
"""

from __future__ import annotations

from typing import Any

from .models import SourceClassification, SourceInfo


class SourceClassifier:
    def __init__(self, scaling_config: dict[str, Any]) -> None:
        cfg = scaling_config.get("classification", {})
        self.intra_like_codecs = {
            str(c).lower()
            for c in cfg.get("intra_like_codecs", [])
        }
        thresholds = cfg.get("thresholds", {})
        self.low_max = float(thresholds.get("low_max", 0.12))
        self.high_min = float(thresholds.get("high_min", 0.25))

    @staticmethod
    def normalized_ob(src: SourceInfo) -> float:
        if src.width <= 0 or src.height <= 0 or src.fps <= 0:
            return 0.0
        return (src.ob_kbps * 1000.0) / (
            src.width * src.height * src.fps
        )

    def classify(self, src: SourceInfo) -> SourceClassification:
        norm = self.normalized_ob(src)

        if src.codec.lower() in self.intra_like_codecs:
            return SourceClassification(
                "INTRA_LIKE",
                norm,
                f"codec '{src.codec}' matched intra_like_codecs",
            )

        if norm <= 0.0:
            return SourceClassification(
                "NORMAL_LONG_GOP",
                norm,
                "fallback: unknown width/height/fps or OB",
            )
        if norm < self.low_max:
            return SourceClassification(
                "LOW_BITRATE_LONG_GOP",
                norm,
                f"normalized_ob {norm:.6f} < low_max {self.low_max}",
            )
        if norm >= self.high_min:
            return SourceClassification(
                "HIGH_BITRATE_LONG_GOP",
                norm,
                f"normalized_ob {norm:.6f} >= high_min {self.high_min}",
            )
        return SourceClassification(
            "NORMAL_LONG_GOP",
            norm,
            f"normalized_ob {norm:.6f} within "
            f"[{self.low_max}, {self.high_min})",
        )
