#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remux: source video stream copy + 4 aligned mono PCM tracks -> MP4.

No video re-encode and no video retiming: the video stream is copied bit-exactly.
Audio is the time-varying aligned PCM (24-bit / 48 kHz, one mono track per source
audio stream, in the source order).

Verification (all in the JSON report):
  * video streamhash (packet payload hash) identical to source
  * video packet count and dts/pts sequence identical (proves no retiming / no VFR)
  * frame count, avg_frame_rate, r_frame_rate identical
  * 4 audio tracks, pcm_s24be, 48000 Hz, mono, duration_ts == source duration_ts
  * output duration within one video frame of the source
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
FFMPEG = la.FFMPEG
FFPROBE = la.FFPROBE

CLIPS = {
    "20260919_C1305": la.REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    "20260919_C1306": la.REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def streamhash(path: Path, stream: str) -> str:
    r = run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-map", stream, "-c", "copy", "-f", "streamhash",
             "-hash", "sha256", "-"])
    return r.stdout.strip()


def video_packets(path: Path) -> list[tuple[str, str]]:
    r = run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,dts_time", "-of", "csv=p=0",
             str(path)])
    rows = []
    for line in r.stdout.splitlines():
        parts = [p for p in line.split(",") if p]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def probe(path: Path) -> dict:
    r = run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)])
    return json.loads(r.stdout or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", default="")
    ap.add_argument("--tv2", action="store_true",
                    help="use aligned_tv2 (round-2 refined) audio")
    args = ap.parse_args()
    stems = [args.stem] if args.stem else list(CLIPS)
    for stem in stems:
        src = CLIPS[stem]
        src_wav = OUT / ("aligned_tv2" if args.tv2 else "aligned_tv") / stem
        suffix = "_tv2.wav" if args.tv2 else "_tv.wav"
        dst = OUT / "deliver" / f"{stem}_4ch_aligned.mp4"
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y",
               "-i", str(src)]
        for i in range(4):
            cmd += ["-i", str(src_wav / f"ch{i}{suffix}")]
        cmd += ["-map", "0:v:0"]
        for i in range(4):
            cmd += ["-map", f"{i + 1}:a"]
        cmd += ["-map_metadata", "0",
                "-c:v", "copy", "-c:a", "pcm_s24be",
                "-movflags", "+faststart", str(dst)]
        r = run(cmd)
        if r.returncode != 0:
            print(f"{stem}: remux failed: {r.stderr[-500:]}")
            continue
        # ---- verify
        src_probe = probe(src)
        out_probe = probe(dst)
        src_v = [s for s in src_probe["streams"] if s["codec_type"] == "video"]
        out_v = [s for s in out_probe["streams"] if s["codec_type"] == "video"]
        src_a = [s for s in src_probe["streams"] if s["codec_type"] == "audio"]
        out_a = [s for s in out_probe["streams"] if s["codec_type"] == "audio"]
        hs_src = streamhash(src, "0:v:0")
        hs_out = streamhash(dst, "0:v:0")
        pk_src = video_packets(src)
        pk_out = video_packets(dst)
        report = {
            "stem": stem, "output": str(dst),
            "output_bytes": dst.stat().st_size,
            "video_streamhash_src": hs_src,
            "video_streamhash_out": hs_out,
            "video_bit_exact": hs_src == hs_out,
            "video_packets_src": len(pk_src),
            "video_packets_out": len(pk_out),
            "video_packets_identical": pk_src == pk_out,
            "video_frames_src": src_v[0].get("nb_frames"),
            "video_frames_out": out_v[0].get("nb_frames"),
            "avg_frame_rate_src": src_v[0].get("avg_frame_rate"),
            "avg_frame_rate_out": out_v[0].get("avg_frame_rate"),
            "r_frame_rate_src": src_v[0].get("r_frame_rate"),
            "r_frame_rate_out": out_v[0].get("r_frame_rate"),
            "audio_tracks_src": len(src_a),
            "audio_tracks_out": len(out_a),
            "audio_out": [
                {"codec": s.get("codec_name"), "codec_tag": s.get("codec_tag_string"),
                 "sample_rate": s.get("sample_rate"),
                 "channels": s.get("channels"),
                 "duration_ts": s.get("duration_ts"),
                 "bits_per_sample": s.get("bits_per_sample")} for s in out_a],
            "audio_duration_ts_src": sorted({int(s["duration_ts"]) for s in src_a}),
            "duration_src": src_probe["format"].get("duration"),
            "duration_out": out_probe["format"].get("duration"),
        }
        (OUT / f"remux{('_v2' if args.tv2 else '')}_{stem}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n=== {stem}: {dst.name} ({report['output_bytes'] / 2**30:.2f} GiB)")
        for k in ("video_bit_exact", "video_packets_identical"):
            print(f"  {k}: {report[k]}")
        print(f"  video packets {report['video_packets_src']} -> "
              f"{report['video_packets_out']}; frames "
              f"{report['video_frames_src']} -> {report['video_frames_out']}")
        print(f"  frame rate {report['avg_frame_rate_src']} -> "
              f"{report['avg_frame_rate_out']}")
        print(f"  audio tracks {report['audio_tracks_src']} -> "
              f"{report['audio_tracks_out']}; "
              f"dur_ts src {report['audio_duration_ts_src']}")
        for s in report["audio_out"]:
            print(f"    {s['codec']}/{s['codec_tag']} {s['sample_rate']}Hz "
                  f"{s['channels']}ch {s['bits_per_sample']}bit dur_ts={s['duration_ts']}")
        print(f"  duration {report['duration_src']} -> {report['duration_out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
