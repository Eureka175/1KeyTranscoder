"""x265 backend: parameter mapping, serialization, FFmpeg command.

All x265-specific knowledge lives here:

- PARAM_MAP: x265.json profile keys -> x265 parameter names
- format_x265_value(): fixed-value formatting (bool/list/deblock/AUTO)
- build_x265_params(): EffectiveParams -> -x265-params string
- build_command(): full FFmpeg argv for one source file

The backend receives already-resolved EffectiveParams from
core.scaling. It does NOT calculate scaling factors, classify sources,
or apply bitrate rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import EffectiveParams
from core.scaling import ceil_expression

from .base import EncoderBackend

PARAM_MAP = {
    "rd": "rd",
    "rdoq_level": "rdoq-level",
    "ref": "ref",
    "bframes": "bframes",
    "keyint": "keyint",
    "min_keyint": "min-keyint",
    "scenecut": "scenecut",
    "hist_scenecut": "hist-scenecut",
    "fades": "fades",
    "b_intra": "b-intra",
    "b_adapt": "b-adapt",
    "bframe_bias": "bframe-bias",
    "open_gop": "open-gop",
    "qcomp": "qcomp",
    "qblur": "qblur",
    "qpstep": "qpstep",
    "ipratio": "ipratio",
    "pbratio": "pbratio",
    "const_vbv": "const-vbv",
    "vbv_maxrate": "vbv-maxrate",
    "vbv_bufsize": "vbv-bufsize",
    "qpmin": "qpmin",
    "qpmax": "qpmax",
    "me": "me",
    "subme": "subme",
    "merange": "merange",
    "max_merge": "max-merge",
    "weightb": "weightb",
    "b_pyramid": "b-pyramid",
    "aq_mode": "aq-mode",
    "aq_strength": "aq-strength",
    "qg_size": "qg-size",
    "aq_motion": "aq-motion",
    "cutree": "cutree",
    "cbqpoffs": "cbqpoffs",
    "crqpoffs": "crqpoffs",
    "ctu": "ctu",
    "min_cu_size": "min-cu-size",
    "rect": "rect",
    "amp": "amp",
    "limit_tu": "limit-tu",
    "tu_intra_depth": "tu-intra-depth",
    "tu_inter_depth": "tu-inter-depth",
    "rdpenalty": "rdpenalty",
    "tskip": "tskip",
    "tskip_fast": "tskip-fast",
    "psy_rd": "psy-rd",
    "psy_rdoq": "psy-rdoq",
    "dynamic_rd": "dynamic-rd",
    "rskip": "rskip",
    "early_skip": "early-skip",
    "fast_intra": "fast-intra",
    "splitrd_skip": "splitrd-skip",
    "limit_modes": "limit-modes",
    "limit_refs": "limit-refs",
    "rc_lookahead": "rc-lookahead",
    "gop_lookahead": "gop-lookahead",
    "lookahead_threads": "lookahead-threads",
    "deblock": "deblock",
    "sao": "sao",
    "limit_sao": "limit-sao",
    "no_strong_intra_smoothing": "no-strong-intra-smoothing",
    "threaded_me": "threaded-me",
    "high_tier": "high-tier",
    "level_idc": "level-idc",
    "aud": "aud",
    "repeat_headers": "repeat-headers",
    "hrd": "hrd",
    "info": "info",
    "weightp": "weightp",
}


def format_x265_value(
    key: str,
    value: Any,
    fps: float,
) -> str | None:
    """
    Format a fixed (unscaled) base-profile value as an x265 parameter.
    FPS-scaled and spatially-scaled parameters are resolved by
    core.scaling.ScalingEngine; this is only the fixed-value formatter
    handed to the engine as a callback.
    """
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

    if key == "deblock":
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError(
                    "deblock must contain exactly two values."
                )
            return f"{value[0]},{value[1]}"
        return str(value)

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)

    if isinstance(value, float):
        return f"{value:g}"

    return str(value)


def build_x265_params(
    effective: EffectiveParams,
) -> tuple[str, dict[str, Any]]:
    """
    Serialize pre-computed EffectiveParams into the -x265-params string.
    No scaling happens here; EffectiveParams is built beforehand.
    """
    params = [
        f"{xkey}={value}" for xkey, value in effective.values.items()
    ]
    return ":".join(params), dict(effective.values)


def build_command(
    ffmpeg: Path,
    src: Path,
    part_dst: Path,
    profile: dict[str, Any],
    effective: EffectiveParams,
    video_stream_count: int,
) -> tuple[list[str], dict[str, Any]]:
    # Effective parameters are final: only serialization happens here.
    x265_params, effective_dict = build_x265_params(effective)

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
        "-c:v:0", "libx265",
        "-preset", str(profile["preset"]),
        "-crf", str(profile["crf"]),
        "-pix_fmt", "yuv420p10le",
    ]

    # Any additional video stream (DJI attached picture, cover image, etc.)
    # is copied rather than encoded as a second libx265 stream.
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
        "-x265-params", x265_params,
        "-movflags", "+use_metadata_tags",
        str(part_dst),
    ]

    return cmd, effective_dict


class X265Backend(EncoderBackend):
    """x265/libx265 implementation of the encoder-backend contract."""

    name = "x265"
    param_order = PARAM_MAP
    format_fixed = staticmethod(format_x265_value)

    def build_command(
        self,
        ffmpeg: Path,
        src: Path,
        part_dst: Path,
        profile: dict[str, Any],
        effective: EffectiveParams,
        video_stream_count: int,
    ) -> tuple[list[str], dict[str, Any]]:
        return build_command(
            ffmpeg,
            src,
            part_dst,
            profile,
            effective,
            video_stream_count,
        )
