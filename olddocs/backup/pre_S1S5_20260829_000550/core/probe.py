"""ffprobe probing (metadata-only) and the SourceInfo adapter.

probe_source() is preserved byte-for-byte from the original
x265_archive.py: ONE metadata pass per file, no -show_frames, no
-show_packets, no GOP analysis. The CSV layer depends on its exact
(summary, streams) return structure.

    probe_source() -> (summary dict, raw stream list)
    build_source_info() -> SourceInfo   (adapter)
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .models import SourceInfo, StreamBrief


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _parse_float(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "N/A") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_int(value: Any) -> int:
    try:
        return int(value) if value not in (None, "", "N/A") else 0
    except (TypeError, ValueError):
        return 0


def _parse_ratio(value: Any) -> float:
    if value in (None, "", "0/0", "N/A"):
        return 0.0

    try:
        text = str(value)
        if "/" in text:
            num, den = text.split("/", 1)
            denominator = float(den)
            if denominator == 0:
                return 0.0
            return float(num) / denominator
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _codec_summary(streams: list[dict[str, Any]]) -> str:
    return ";".join(
        f"{st.get('index', '')}:{st.get('codec_name', '')}"
        for st in streams
    )


def _pix_fmt_bit_depth(pix_fmt: str, bits_per_raw_sample: int) -> int:
    if bits_per_raw_sample > 0:
        return bits_per_raw_sample
    m = re.search(r"p(\d+)(?:le|be)?$", pix_fmt)
    if m:
        return int(m.group(1))
    return 8 if pix_fmt else 0


def _pix_fmt_chroma(pix_fmt: str) -> str:
    if "420" in pix_fmt:
        return "4:2:0"
    if "422" in pix_fmt:
        return "4:2:2"
    if "444" in pix_fmt:
        return "4:4:4"
    if pix_fmt.startswith("gray"):
        return "mono"
    return ""


def probe_source(
    ffprobe: Path,
    src: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Detailed source probe.

    The stream list is separately returned so the CSV can contain a
    full one-row-per-stream table.
    """
    cmd = [
        str(ffprobe),
        "-v", "error",
        "-show_entries",
        (
            "format="
            "format_name,format_long_name,duration,size,bit_rate,"
            "start_time,probe_score:"
            "stream="
            "index,codec_type,codec_name,codec_long_name,profile,"
            "codec_tag_string,codec_tag,width,height,pix_fmt,"
            "color_range,color_space,color_transfer,color_primaries,"
            "field_order,r_frame_rate,avg_frame_rate,time_base,start_time,"
            "duration,bit_rate,nb_frames,channels,sample_rate,sample_fmt,"
            "channel_layout,bits_per_raw_sample,bits_per_coded_sample:"
            "disposition"
        ),
        "-of", "json",
        str(src),
    ]

    result = run_capture(cmd)

    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed ({result.returncode}): "
            f"{result.stderr.strip() or 'no stderr'}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Cannot parse ffprobe JSON: {exc}"
        ) from exc

    streams = payload.get("streams", [])
    fmt = payload.get("format", {})

    if not streams:
        raise RuntimeError("ffprobe found no streams.")

    video = next(
        (st for st in streams if st.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise RuntimeError("No video stream found.")

    duration = _parse_float(fmt.get("duration"))
    if duration <= 0:
        duration = _parse_float(video.get("duration"))

    fps = _parse_ratio(
        video.get("avg_frame_rate") or video.get("r_frame_rate")
    )

    total_frames = _parse_int(video.get("nb_frames"))
    if total_frames <= 0 and duration > 0 and fps > 0:
        total_frames = int(math.ceil(duration * fps))

    size_bytes = _parse_int(fmt.get("size"))
    if size_bytes <= 0:
        try:
            size_bytes = src.stat().st_size
        except OSError:
            size_bytes = 0

    container_bitrate = _parse_float(fmt.get("bit_rate"))
    if container_bitrate <= 0 and size_bytes > 0 and duration > 0:
        container_bitrate = size_bytes * 8.0 / duration

    video_bitrate = _parse_float(video.get("bit_rate"))

    audio = [st for st in streams if st.get("codec_type") == "audio"]
    subtitles = [st for st in streams if st.get("codec_type") == "subtitle"]
    data = [st for st in streams if st.get("codec_type") == "data"]
    attachments = [st for st in streams if st.get("codec_type") == "attachment"]

    try:
        stat = src.stat()
        mtime = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(stat.st_mtime),
        )
        ctime = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(stat.st_ctime),
        )
    except OSError:
        mtime = ""
        ctime = ""

    summary = {
        "source_size_bytes": size_bytes,
        "source_size_mb": size_bytes / (1024 ** 2),
        "source_size_gib": size_bytes / (1024 ** 3),
        "source_size_gb": size_bytes / 1_000_000_000,
        "container_bitrate_bps": container_bitrate,
        "container_bitrate_kbps": container_bitrate / 1000.0,
        "container_bitrate_mbps": container_bitrate / 1_000_000.0,

        "format_name": str(fmt.get("format_name") or ""),
        "format_long_name": str(fmt.get("format_long_name") or ""),
        "format_start_time": str(fmt.get("start_time") or ""),
        "probe_score": _parse_int(fmt.get("probe_score")),
        "file_mtime": mtime,
        "file_ctime": ctime,

        "fps": fps,
        "duration": duration,
        "total_frames": total_frames,
        "width": _parse_int(video.get("width")),
        "height": _parse_int(video.get("height")),
        "resolution": (
            f"{_parse_int(video.get('width'))}x"
            f"{_parse_int(video.get('height'))}"
        ),
        "pix_fmt": str(video.get("pix_fmt") or ""),
        "codec": str(video.get("codec_name") or ""),
        "codec_long_name": str(video.get("codec_long_name") or ""),
        "profile": str(video.get("profile") or ""),
        "codec_tag_string": str(video.get("codec_tag_string") or ""),
        "codec_tag": str(video.get("codec_tag") or ""),
        "video_bitrate_bps": video_bitrate,
        "video_bitrate_kbps": video_bitrate / 1000.0,
        "video_bitrate_mbps": video_bitrate / 1_000_000.0,
        "r_frame_rate": str(video.get("r_frame_rate") or ""),
        "avg_frame_rate": str(video.get("avg_frame_rate") or ""),
        "time_base": str(video.get("time_base") or ""),
        "start_time": str(video.get("start_time") or ""),
        "field_order": str(video.get("field_order") or ""),
        "color_range": str(video.get("color_range") or ""),
        "color_space": str(video.get("color_space") or ""),
        "color_transfer": str(video.get("color_transfer") or ""),
        "color_primaries": str(video.get("color_primaries") or ""),
        "bits_per_raw_sample": _parse_int(
            video.get("bits_per_raw_sample")
        ),
        "bits_per_coded_sample": _parse_int(
            video.get("bits_per_coded_sample")
        ),

        "stream_count": len(streams),
        "video_streams": sum(
            1 for st in streams if st.get("codec_type") == "video"
        ),
        "audio_streams": len(audio),
        "subtitle_streams": len(subtitles),
        "data_streams": len(data),
        "attachment_streams": len(attachments),

        "audio_codecs": _codec_summary(audio),
        "subtitle_codecs": _codec_summary(subtitles),
        "data_codecs": _codec_summary(data),
        "attachment_codecs": _codec_summary(attachments),
    }

    return summary, streams


def build_source_info(
    path: Path,
    summary: dict[str, Any],
    streams: list[dict[str, Any]],
) -> SourceInfo:
    """Adapter: probe_source() output -> typed SourceInfo."""
    pix_fmt = summary["pix_fmt"]
    return SourceInfo(
        path=path,
        size_bytes=summary["source_size_bytes"],
        duration_sec=summary["duration"],
        width=summary["width"],
        height=summary["height"],
        fps=summary["fps"],
        r_frame_rate=summary["r_frame_rate"],
        avg_frame_rate=summary["avg_frame_rate"],
        codec=summary["codec"],
        profile=summary["profile"],
        pix_fmt=pix_fmt,
        bit_depth=_pix_fmt_bit_depth(
            pix_fmt,
            summary["bits_per_raw_sample"],
        ),
        chroma=_pix_fmt_chroma(pix_fmt),
        ob_kbps=summary["container_bitrate_kbps"],
        video_bitrate_kbps=summary["video_bitrate_kbps"],
        video_stream_count=summary["video_streams"],
        stream_info=tuple(
            StreamBrief(
                index=_parse_int(st.get("index")),
                codec_type=str(st.get("codec_type") or ""),
                codec_name=str(st.get("codec_name") or ""),
            )
            for st in streams
        ),
        raw_summary=summary,
        raw_streams=tuple(streams),
    )
