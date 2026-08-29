"""Post-encode probing and CSV logging (shared by all backend paths).

Extracted from the main program so hardware and x265 paths report
identically.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .logging_utils import (
    POST_CSV_FIELDS,
    append_csv,
    append_rows,
    format_mb,
    stream_rows_from_raw,
)
from .probe import probe_source


def postprobe_and_log(
    *,
    src: Path,
    dst: Path,
    preset: str,
    elapsed: float,
    source_summary: dict[str, Any],
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> str:
    post_status = "done"
    error_text = ""

    try:
        output_summary, output_streams = probe_source(ffprobe, dst)
    except Exception as exc:
        output_summary = {}
        output_streams = []
        post_status = "done_postprobe_failed"
        error_text = str(exc)

        logger.error("[POST-PROBE-FAIL] %s | %s", dst, exc)
        file_logger.exception("POSTPROBE FAILED")
    else:
        source_size = source_summary["source_size_bytes"]
        output_size = output_summary["source_size_bytes"]

        ratio = output_size / source_size if source_size > 0 else 0.0

        logger.info(
            "[DONE] elapsed=%.3f sec | "
            "source=%.3f MB -> output=%.3f MB | ratio=%.3f%%",
            elapsed,
            source_size / (1024 ** 2),
            output_size / (1024 ** 2),
            ratio * 100.0,
        )

        file_logger.info(
            "DONE | elapsed=%.3f sec | source=%.3f MB | "
            "output=%.3f MB | ratio=%.6f | saved=%.6f",
            elapsed,
            source_size / (1024 ** 2),
            output_size / (1024 ** 2),
            ratio,
            1.0 - ratio,
        )

    source_size = source_summary["source_size_bytes"]
    output_size = (
        int(output_summary.get("source_size_bytes", 0))
        if output_summary
        else (dst.stat().st_size if dst.is_file() else 0)
    )

    ratio = output_size / source_size if source_size > 0 else 0.0

    post_row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset": preset,
        "source": str(src),
        "output": str(dst),
        "status": post_status,
        "elapsed_sec": f"{elapsed:.3f}",

        "source_size_bytes": source_size,
        "source_size_mb": format_mb(source_size),
        "source_size_gib": f"{source_size / (1024 ** 3):.6f}",

        "output_size_bytes": output_size,
        "output_size_mb": format_mb(output_size),
        "output_size_gib": f"{output_size / (1024 ** 3):.6f}",
        "output_source_ratio": f"{ratio:.6f}",
        "space_saved_ratio": f"{1.0 - ratio:.6f}",

        "source_fps": f"{source_summary['fps']:.9f}",
        "source_duration_sec": f"{source_summary['duration']:.6f}",
        "source_frames_est": source_summary["total_frames"],

        "output_fps": f"{output_summary.get('fps', 0.0):.9f}",
        "output_duration_sec": f"{output_summary.get('duration', 0.0):.6f}",
        "output_frames_est": output_summary.get("total_frames", 0),

        "output_width": output_summary.get("width", 0),
        "output_height": output_summary.get("height", 0),
        "output_pix_fmt": output_summary.get("pix_fmt", ""),
        "output_codec": output_summary.get("codec", ""),
        "output_profile": output_summary.get("profile", ""),
        "output_r_frame_rate": output_summary.get("r_frame_rate", ""),
        "output_avg_frame_rate": output_summary.get("avg_frame_rate", ""),
        "output_stream_count": output_summary.get("stream_count", 0),
        "output_video_streams": output_summary.get("video_streams", 0),
        "output_audio_streams": output_summary.get("audio_streams", 0),
        "output_subtitle_streams": output_summary.get("subtitle_streams", 0),
        "output_data_streams": output_summary.get("data_streams", 0),
        "output_attachment_streams": output_summary.get(
            "attachment_streams", 0,
        ),
        "output_audio_codecs": output_summary.get("audio_codecs", ""),
        "output_subtitle_codecs": output_summary.get("subtitle_codecs", ""),
        "output_data_codecs": output_summary.get("data_codecs", ""),
        "output_attachment_codecs": output_summary.get(
            "attachment_codecs", "",
        ),
        "error": error_text,
    }

    append_csv(postprobe_csv, POST_CSV_FIELDS, post_row)

    if output_streams:
        append_rows(
            postprobe_stream_csv,
            POST_CSV_FIELDS,
            [
                {
                    **row,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "preset": preset,
                    "source": str(src),
                    "source_name": src.name,
                    "output": str(dst),
                }
                for row in stream_rows_from_raw(
                    output_streams, src, preset, output=dst
                )
            ],
        )

    return post_status
