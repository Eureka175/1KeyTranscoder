"""SVT-AV1 backend: parameter mapping, serialization, FFmpeg command.

All SVT-AV1-specific knowledge lives here:

- PARAM_MAP: svtav1.json profile keys -> SVT-AV1 parameter names
- format_svt_value(): fixed-value formatting (bool/FR*/AUTO)
- build_svt_params(): EffectiveParams -> -svtav1-params string
- build_command(): full FFmpeg argv for one source file

The backend receives already-resolved EffectiveParams from
core.scaling. It does NOT calculate scaling factors, classify sources,
or apply bitrate rules. AV1 policy: uniform 4:2:0 output (SVT-AV1's
only chroma formats); the caller emits the 4:2:2-downgrade warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import EffectiveParams, SourceInfo
from core.scaling import ceil_expression

from .base import EncoderBackend

# svtav1.json profile key -> SVT-AV1 configuration parameter name.
# Verified against SVT-AV1 Encoder Lib v4.2.0 (smoke-encode bisect).
# Unmapped keys are intentionally absent (preset-controlled internals,
# no SVT equivalent, or defaults chosen): bframes (hierarchical alt-ref
# via pred-struct/hierarchical-levels), min-keyint/scenecut keyframes
# (SVT does NOT insert scene-cut keyframes), cutree (TPL always on),
# psy-rd (tune 0 + enable-qm + ac-bias), sao (cdef+restoration),
# deblock offsets (enable-dlf/sharpness), me/merange/ref (preset).
PARAM_MAP = {
    "keyint": "keyint",
    "scd": "scd",
    "irefresh_type": "irefresh-type",
    "lookahead": "lookahead",
    "tune": "tune",
    "enable_tf": "enable-tf",
    "enable_overlays": "enable-overlays",
    "enable_qm": "enable-qm",
    "qm_min": "qm-min",
    "aq_mode": "aq-mode",
    "enable_variance_boost": "enable-variance-boost",
    "vbv_maxrate": "mbr",
    "film_grain": "film-grain",
    "film_grain_denoise": "film-grain-denoise",
    "enable_cdef": "enable-cdef",
    "enable_restoration": "enable-restoration",
    "enable_dlf": "enable-dlf",
    "pred_struct": "pred-struct",
    "fast_decode": "fast-decode",
    "sharpness": "sharpness",
    "qp_scale_compress_strength": "qp-scale-compress-strength",
    "ac_bias": "ac-bias",
    "max_tx_size": "max-tx-size",
}


def format_svt_value(key: str, value: Any, fps: float) -> str | None:
    """Format a fixed (unscaled) base-profile value as an SVT-AV1
    parameter. FPS-scaled parameters are resolved by core.scaling;
    this is only the fixed-value formatter."""
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if text.upper() == "AUTO":
            return None
        if text.upper().replace(" ", "").startswith("FR*"):
            # FR* is only meaningful for fps-scaled rules; evaluate it
            # uncapped rather than leaking the raw expression through.
            return str(ceil_expression(text, fps))
        return text

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)

    if isinstance(value, float):
        return f"{value:g}"

    return str(value)


def build_svt_params(effective: EffectiveParams) -> tuple[str, dict[str, Any]]:
    """Serialize pre-computed EffectiveParams into the -svtav1-params
    string. No scaling happens here."""
    params = [
        f"{svt_key}={value}" for svt_key, value in effective.values.items()
    ]
    return ":".join(params), dict(effective.values)


def av1_pix_fmt(src_info: SourceInfo) -> str:
    """Uniform 4:2:0 output; keep the source bit depth (8 -> yuv420p,
    10 -> yuv420p10le). SVT-AV1 supports only these two formats."""
    return "yuv420p10le" if src_info.bit_depth > 8 else "yuv420p"


def build_command(
    ffmpeg: Path,
    src: Path,
    part_dst: Path,
    profile: dict[str, Any],
    effective: EffectiveParams,
    video_stream_count: int,
    src_info: SourceInfo,
) -> tuple[list[str], dict[str, Any]]:
    svt_params, effective_dict = build_svt_params(effective)

    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-stats",
        "-y",
        "-i", str(src),

        # Preserve all streams.
        "-map", "0",
        "-map_metadata", "0",
        "-map_chapters", "0",

        # Only primary video stream is encoded.
        "-c:v:0", "libsvtav1",
        "-preset", str(profile["preset"]),
        "-crf", str(profile["crf"]),
        "-pix_fmt", av1_pix_fmt(src_info),
    ]

    # Any additional video stream (DJI attached picture, cover image, etc.)
    # is copied rather than encoded as a second SVT-AV1 stream.
    for stream_index in range(1, max(1, video_stream_count)):
        cmd += [
            f"-c:v:{stream_index}",
            "copy",
        ]

    cmd += [
        "-c:a", "copy",
        "-c:s", "copy",
        "-c:d", "copy",
        "-c:t", "copy",
        "-fps_mode", "passthrough",
        "-svtav1-params", svt_params,
        "-movflags", "+use_metadata_tags",
        str(part_dst),
    ]

    return cmd, effective_dict


def build_video_command(
    ffmpeg: Path,
    src: Path,
    out_mov: Path,
    profile: dict[str, Any],
    effective: EffectiveParams,
    src_info: SourceInfo,
) -> tuple[list[str], dict[str, Any]]:
    """Video-only intermediate MOV for the preservation container rebuild.

    Same encoder parameters as build_command(), but only the primary
    video stream: audio is container-copied from the source by the
    container backend, and the camera metadata tracks cannot pass
    through FFmpeg at all (they are re-added from the preservation
    bundle by MP4Box).

    MOV, not MKV: GPAC's MKV reader quantizes timestamps to
    milliseconds, breaking exact 1001/60000 rtmd alignment.
    """
    svt_params, effective_dict = build_svt_params(effective)

    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-stats",
        "-y",
        "-i", str(src),

        "-map", "0:v:0",
        "-c:v", "libsvtav1",
        "-preset", str(profile["preset"]),
        "-crf", str(profile["crf"]),
        "-pix_fmt", av1_pix_fmt(src_info),

        "-an", "-sn", "-dn",
        "-fps_mode", "passthrough",
        "-svtav1-params", svt_params,
        "-tag:v", "av01",
        str(out_mov),
    ]

    return cmd, effective_dict


class SvtAv1Backend(EncoderBackend):
    """SVT-AV1 (libsvtav1) implementation of the encoder-backend
    contract."""

    name = "svtav1"
    param_order = PARAM_MAP
    format_fixed = staticmethod(format_svt_value)

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
        return build_command(
            ffmpeg,
            src,
            part_dst,
            profile,
            effective,
            video_stream_count,
            src_info,
        )

    def build_video_command(
        self,
        ffmpeg: Path,
        src: Path,
        out_mov: Path,
        profile: dict[str, Any],
        effective: EffectiveParams,
        src_info: SourceInfo,
    ) -> tuple[list[str], dict[str, Any]]:
        """Video-only intermediate for the metadata-preservation
        container rebuild (Sony pipeline)."""
        return build_video_command(
            ffmpeg,
            src,
            out_mov,
            profile,
            effective,
            src_info,
        )
