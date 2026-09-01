"""POC video/audio backends.

VideoBackend = FFmpeg + libx265, forced -preset ultrafast, intermediate
MKV on the filesystem (no pipes). Quality is irrelevant in this phase;
only the container/metadata pipeline is being validated.

AudioBackend = copy. No re-encode, no sample-rate conversion, no codec
profiles. The PCM track is imported straight from the source container
by MP4Box during reconstruction, so there is no audio intermediate file;
note() just records the intent for auditability.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _probe_stream(ffprobe: Path, path: Path, codec_type: str) -> dict[str, Any]:
    cmd = [
        str(ffprobe), "-v", "error", "-count_packets",
        "-select_streams", codec_type,
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,"
        "nb_frames,nb_read_packets,duration,pix_fmt,color_space,"
        "color_transfer,color_primaries,color_range",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr[-500:]}")
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"no {codec_type} stream in {path}")
    return streams[0]


class FFmpegUltrafastVideoBackend:
    def __init__(self, ffmpeg: Path, ffprobe: Path) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def encode(self, source: Path, out_mov: Path) -> Path:
        """Encode to MOV (mux path) + remux-copy to MKV (inspection).

        Why MOV for the mux path: GPAC's MKV reader normalizes
        timestamps at millisecond precision (verified on GPAC 26.02:
        1001/60000 deltas come back as 960/1020 patterns, even with
        -video_track_timescale and :fps=), which breaks exact rtmd
        alignment. An ffmpeg MOV keeps the source's exact 1001/60000
        deltas (and even the elst MediaTime=2002 offset), and an
        ISOBMFF->ISOBMFF MP4Box import preserves them. The MKV is a
        byte-identical-bitstream remux for stage-boundary inspection.
        Returns the MKV path.
        """
        out_mov.parent.mkdir(parents=True, exist_ok=True)
        src_v = _probe_stream(self.ffprobe, source, "v:0")

        cmd = [
            str(self.ffmpeg), "-y", "-hide_banner",
            "-i", str(source),
            "-map", "0:v:0",
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-crf", "28",
            "-tag:v", "hvc1",
            "-an", "-sn", "-dn",
            str(out_mov),
        ]
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg x265 encode failed (rc={proc.returncode}):\n"
                f"{(proc.stdout or '')[-2000:]}"
            )

        # hard 1:1 timeline requirement: same frame count, same frame rate.
        out_v = _probe_stream(self.ffprobe, out_mov, "v:0")
        src_frames = int(
            src_v.get("nb_read_packets") or src_v.get("nb_frames") or 0
        )
        out_frames = int(
            out_v.get("nb_read_packets") or out_v.get("nb_frames") or 0
        )
        if src_frames and out_frames != src_frames:
            raise RuntimeError(
                f"frame count mismatch: source {src_frames} vs "
                f"encoded {out_frames}"
            )
        if (
            src_v.get("avg_frame_rate")
            and out_v.get("avg_frame_rate") != src_v.get("avg_frame_rate")
        ):
            raise RuntimeError(
                f"frame rate mismatch: source {src_v.get('avg_frame_rate')} "
                f"vs encoded {out_v.get('avg_frame_rate')}"
            )

        out_mkv = out_mov.with_suffix(".mkv")
        proc = subprocess.run(
            [
                str(self.ffmpeg), "-y", "-v", "error",
                "-i", str(out_mov), "-c", "copy", str(out_mkv),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"MKV remux failed (rc={proc.returncode}):\n"
                f"{(proc.stdout or '')[-1000:]}"
            )
        return out_mkv


class CopyAudioBackend:
    """No-op placeholder: audio is container-copied from the source by
    the container backend during reconstruction (`-add src#audio`)."""

    def note(self, source: Path, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "README.json").write_text(
            json.dumps(
                {
                    "mode": "copy",
                    "detail": (
                        "PCM track imported directly from the source "
                        "container by MP4Box (-add <src>#audio). "
                        "No re-encode, no sample-rate conversion."
                    ),
                    "source": str(source),
                },
                indent=2, ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
