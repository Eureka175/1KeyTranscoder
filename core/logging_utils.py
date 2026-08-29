"""Logging and CSV reporting utilities.

Loggers (total.log + console, per-file logs) and all CSV field lists /
row builders. The four pre-existing CSV field lists are unchanged; the
scaling audit adds a NEW file (scaling.csv) instead of modifying them.
"""

from __future__ import annotations

import csv
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .models import EffectiveParams
from .probe import _parse_float

_CSV_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Loggers
# ---------------------------------------------------------------------------

def setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("x265_archive")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def build_file_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger(f"x265_archive.file.{path}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def append_csv(
    path: Path,
    fieldnames: list[str],
    row: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    exists = path.exists() and path.stat().st_size > 0

    with _CSV_LOCK:
        with path.open(
            "a",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            if not exists:
                writer.writeheader()

            writer.writerow({
                key: "" if row.get(key) is None else row.get(key)
                for key in fieldnames
            })


def append_rows(
    path: Path,
    fields: list[str],
    rows: list[dict[str, Any]],
) -> None:
    for row in rows:
        append_csv(path, fields, row)


def format_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 2):.3f}"


def format_sec(seconds: float) -> str:
    return f"{seconds:.3f}"


# ---------------------------------------------------------------------------
# CSV field lists (the four pre-existing lists are unchanged)
# ---------------------------------------------------------------------------

PRE_CSV_FIELDS = [
    "timestamp", "preset",
    "source", "source_name", "source_ext", "source_dir",
    "source_size_bytes", "source_size_mb", "source_size_gib",
    "source_size_gb",
    "container_bitrate_kbps", "container_bitrate_mbps",
    "video_bitrate_kbps", "video_bitrate_mbps",
    "format_name", "format_long_name", "format_start_time",
    "probe_score", "file_mtime", "file_ctime",
    "codec", "codec_long_name", "profile",
    "codec_tag_string", "codec_tag",
    "width", "height", "resolution", "pix_fmt",
    "fps", "r_frame_rate", "avg_frame_rate",
    "duration_sec", "duration_min",
    "total_frames_est",
    "time_base", "start_time",
    "field_order", "color_range", "color_space",
    "color_transfer", "color_primaries",
    "bits_per_raw_sample", "bits_per_coded_sample",
    "stream_count", "video_streams", "audio_streams",
    "subtitle_streams", "data_streams", "attachment_streams",
    "audio_codecs", "subtitle_codecs",
    "data_codecs", "attachment_codecs",
]

STREAM_CSV_FIELDS = [
    "timestamp", "preset", "source", "source_name",
    "stream_index", "codec_type", "codec_name", "codec_long_name",
    "profile", "codec_tag_string", "codec_tag",
    "width", "height", "pix_fmt",
    "r_frame_rate", "avg_frame_rate",
    "time_base", "start_time", "duration",
    "nb_frames", "bit_rate_bps", "bit_rate_kbps",
    "channels", "sample_rate", "sample_fmt",
    "channel_layout",
    "bits_per_raw_sample", "bits_per_coded_sample",
]

POST_CSV_FIELDS = [
    "timestamp", "preset",
    "source", "output", "status",
    "elapsed_sec",
    "source_size_bytes", "source_size_mb", "source_size_gib",
    "output_size_bytes", "output_size_mb", "output_size_gib",
    "output_source_ratio", "space_saved_ratio",
    "source_fps", "source_duration_sec", "source_frames_est",
    "output_fps", "output_duration_sec", "output_frames_est",
    "output_width", "output_height", "output_pix_fmt",
    "output_codec", "output_profile",
    "output_r_frame_rate", "output_avg_frame_rate",
    "output_stream_count", "output_video_streams",
    "output_audio_streams", "output_subtitle_streams",
    "output_data_streams", "output_attachment_streams",
    "output_audio_codecs", "output_subtitle_codecs",
    "output_data_codecs", "output_attachment_codecs",
    "error",
]

POST_STREAM_CSV_FIELDS = [
    "timestamp", "preset", "source", "output",
    "stream_index", "codec_type", "codec_name", "codec_long_name",
    "profile", "codec_tag_string", "codec_tag",
    "width", "height", "pix_fmt",
    "r_frame_rate", "avg_frame_rate",
    "time_base", "start_time", "duration",
    "nb_frames", "bit_rate_bps", "bit_rate_kbps",
    "channels", "sample_rate", "sample_fmt",
    "channel_layout",
    "bits_per_raw_sample", "bits_per_coded_sample",
]

# Scaling / classification / effective-parameter audit CSV. NEW file
# (scaling.csv); none of the lists above were modified.
SCALING_CSV_FIELDS = [
    "timestamp", "preset", "source", "source_name",
    "source_class", "normalized_ob_bpp", "ob_kbps",
    "spatial_factor", "temporal_factor", "pixel_rate_factor",
    "effective_rc_lookahead", "effective_gop_lookahead",
    "effective_min_keyint", "effective_merange",
    "effective_vbv_maxrate", "effective_vbv_bufsize",
]


# ---------------------------------------------------------------------------
# CSV row builders
# ---------------------------------------------------------------------------

def make_summary_csv_row(
    src: Path,
    summary: dict[str, Any],
    preset: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset": preset,
        "source": str(src),
        "source_name": src.name,
        "source_ext": src.suffix.lower(),
        "source_dir": str(src.parent),

        "source_size_bytes": summary["source_size_bytes"],
        "source_size_mb": f"{summary['source_size_mb']:.3f}",
        "source_size_gib": f"{summary['source_size_gib']:.6f}",
        "source_size_gb": f"{summary['source_size_gb']:.6f}",

        "container_bitrate_kbps": (
            f"{summary['container_bitrate_kbps']:.3f}"
        ),
        "container_bitrate_mbps": (
            f"{summary['container_bitrate_mbps']:.6f}"
        ),
        "video_bitrate_kbps": (
            f"{summary['video_bitrate_kbps']:.3f}"
        ),
        "video_bitrate_mbps": (
            f"{summary['video_bitrate_mbps']:.6f}"
        ),

        "format_name": summary["format_name"],
        "format_long_name": summary["format_long_name"],
        "format_start_time": summary["format_start_time"],
        "probe_score": summary["probe_score"],
        "file_mtime": summary["file_mtime"],
        "file_ctime": summary["file_ctime"],

        "codec": summary["codec"],
        "codec_long_name": summary["codec_long_name"],
        "profile": summary["profile"],
        "codec_tag_string": summary["codec_tag_string"],
        "codec_tag": summary["codec_tag"],
        "width": summary["width"],
        "height": summary["height"],
        "resolution": summary["resolution"],
        "pix_fmt": summary["pix_fmt"],
        "fps": f"{summary['fps']:.9f}",
        "r_frame_rate": summary["r_frame_rate"],
        "avg_frame_rate": summary["avg_frame_rate"],
        "duration_sec": f"{summary['duration']:.6f}",
        "duration_min": f"{summary['duration'] / 60.0:.6f}",
        "total_frames_est": summary["total_frames"],
        "time_base": summary["time_base"],
        "start_time": summary["start_time"],
        "field_order": summary["field_order"],
        "color_range": summary["color_range"],
        "color_space": summary["color_space"],
        "color_transfer": summary["color_transfer"],
        "color_primaries": summary["color_primaries"],
        "bits_per_raw_sample": summary["bits_per_raw_sample"],
        "bits_per_coded_sample": summary["bits_per_coded_sample"],

        "stream_count": summary["stream_count"],
        "video_streams": summary["video_streams"],
        "audio_streams": summary["audio_streams"],
        "subtitle_streams": summary["subtitle_streams"],
        "data_streams": summary["data_streams"],
        "attachment_streams": summary["attachment_streams"],
        "audio_codecs": summary["audio_codecs"],
        "subtitle_codecs": summary["subtitle_codecs"],
        "data_codecs": summary["data_codecs"],
        "attachment_codecs": summary["attachment_codecs"],
    }
    return row


def make_stream_rows(
    src: Path,
    streams: list[dict[str, Any]],
    preset: str,
) -> list[dict[str, Any]]:
    rows = []

    for st in streams:
        rows.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "preset": preset,
            "source": str(src),
            "source_name": src.name,

            "stream_index": st.get("index", ""),
            "codec_type": st.get("codec_type", ""),
            "codec_name": st.get("codec_name", ""),
            "codec_long_name": st.get("codec_long_name", ""),
            "profile": st.get("profile", ""),
            "codec_tag_string": st.get("codec_tag_string", ""),
            "codec_tag": st.get("codec_tag", ""),
            "width": st.get("width", ""),
            "height": st.get("height", ""),
            "pix_fmt": st.get("pix_fmt", ""),
            "r_frame_rate": st.get("r_frame_rate", ""),
            "avg_frame_rate": st.get("avg_frame_rate", ""),
            "time_base": st.get("time_base", ""),
            "start_time": st.get("start_time", ""),
            "duration": st.get("duration", ""),
            "nb_frames": st.get("nb_frames", ""),
            "bit_rate_bps": st.get("bit_rate", ""),
            "bit_rate_kbps": (
                f"{_parse_float(st.get('bit_rate')) / 1000.0:.3f}"
                if st.get("bit_rate") not in (None, "", "N/A")
                else ""
            ),
            "channels": st.get("channels", ""),
            "sample_rate": st.get("sample_rate", ""),
            "sample_fmt": st.get("sample_fmt", ""),
            "channel_layout": st.get("channel_layout", ""),
            "bits_per_raw_sample": st.get("bits_per_raw_sample", ""),
            "bits_per_coded_sample": st.get("bits_per_coded_sample", ""),
        })

    return rows


def stream_rows_from_raw(
    streams: list[dict[str, Any]],
    src: Path,
    preset: str,
    output: Path | None = None,
) -> list[dict[str, Any]]:
    rows = []

    for st in streams:
        rows.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "preset": preset,
            "source": str(src),
            "source_name": src.name,
            "output": str(output) if output else "",
            "stream_index": st.get("index", ""),
            "codec_type": st.get("codec_type", ""),
            "codec_name": st.get("codec_name", ""),
            "codec_long_name": st.get("codec_long_name", ""),
            "profile": st.get("profile", ""),
            "codec_tag_string": st.get("codec_tag_string", ""),
            "codec_tag": st.get("codec_tag", ""),
            "width": st.get("width", ""),
            "height": st.get("height", ""),
            "pix_fmt": st.get("pix_fmt", ""),
            "r_frame_rate": st.get("r_frame_rate", ""),
            "avg_frame_rate": st.get("avg_frame_rate", ""),
            "time_base": st.get("time_base", ""),
            "start_time": st.get("start_time", ""),
            "duration": st.get("duration", ""),
            "nb_frames": st.get("nb_frames", ""),
            "bit_rate_bps": st.get("bit_rate", ""),
            "bit_rate_kbps": (
                f"{_parse_float(st.get('bit_rate')) / 1000.0:.3f}"
                if st.get("bit_rate") not in (
                    None,
                    "",
                    "N/A",
                )
                else ""
            ),
            "channels": st.get("channels", ""),
            "sample_rate": st.get("sample_rate", ""),
            "sample_fmt": st.get("sample_fmt", ""),
            "channel_layout": st.get("channel_layout", ""),
            "bits_per_raw_sample": st.get(
                "bits_per_raw_sample",
                "",
            ),
            "bits_per_coded_sample": st.get(
                "bits_per_coded_sample",
                "",
            ),
        })

    return rows


def make_scaling_csv_row(
    src: Path,
    preset: str,
    effective: EffectiveParams,
) -> dict[str, Any]:
    v = effective.values
    vbv_audit = effective.audit.get("vbv-maxrate", {})
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset": preset,
        "source": str(src),
        "source_name": src.name,

        "source_class": effective.source_class,
        "normalized_ob_bpp": f"{effective.normalized_ob:.6f}",
        "ob_kbps": f"{vbv_audit.get('ob_kbps', '')}",

        "spatial_factor": f"{effective.spatial_factor:.6f}",
        "temporal_factor": f"{effective.temporal_factor:.6f}",
        "pixel_rate_factor": f"{effective.pixel_rate_factor:.6f}",

        "effective_rc_lookahead": v.get("rc-lookahead", ""),
        "effective_gop_lookahead": v.get("gop-lookahead", ""),
        "effective_min_keyint": v.get("min-keyint", ""),
        "effective_merange": v.get("merange", ""),
        "effective_vbv_maxrate": v.get("vbv-maxrate", ""),
        "effective_vbv_bufsize": v.get("vbv-bufsize", ""),
    }
