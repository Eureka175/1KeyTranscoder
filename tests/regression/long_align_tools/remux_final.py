#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final deliverable: source video (stream copy) + 4CH audio with
  * fine delay correction (round-3 residual pass, aligned_fine/*.f32), and
  * polarity repair (flip CH3/CH4 as detected in polarity.json).

Per take:
  1. per-channel mono 24-bit WAV from the fine f32, with the polarity flip applied
     (audio re-encode only; video is never touched);
  2. ffmpeg remux: -map 0:v:0 -c:v copy + 4 mono PCM tracks pcm_s24be;
  3. verify: video streamhash packet-identical, packet count/dts/pts sequence
     identical, frame count/rate unchanged, 4 audio tracks PCM 24-bit 48 kHz mono,
     per-track duration_ts == source, decoded audio md5 == polarity WAV md5.

Output: E:/1kt_work/long_align/deliver/<stem>_4ch_aligned_final.mp4
Report: E:/1kt_work/long_align/remux_final.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))
import long_align as la  # noqa: E402

SR = 48000
FINE = REPO / "data" / "_scratch" / "aligned_fine"
POL = REPO / "data" / "_scratch" / "polarity.json"
SRC = REPO / "data" / "align_4ch_proof"
OUT = Path(r"E:\1kt_work\long_align\deliver")
CLIPS = ["20260919_C1305", "20260919_C1306"]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def streamhash(path: Path, stream: str) -> str:
    r = run([str(la.FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-map", stream, "-c", "copy", "-f", "streamhash",
             "-hash", "sha256", "-"])
    return r.stdout.strip()


def packets(path: Path) -> list[tuple[str, str]]:
    r = run([str(la.FFPROBE), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,dts_time", "-of", "csv=p=0",
             str(path)])
    rows = []
    for line in r.stdout.splitlines():
        parts = [p for p in line.split(",") if p]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def probe(path: Path) -> dict:
    r = run([str(la.FFPROBE), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)])
    return json.loads(r.stdout or "{}")


def audio_md5(path: Path, idx: int) -> str:
    r = run([str(la.FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-map", f"0:a:{idx}", "-f", "md5", "-"])
    return r.stdout.strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    flips = {stem: {int(k): bool(v) for k, v in (json.loads(
        POL.read_text(encoding="utf-8"))[stem]["flip"]).items()}
        for stem in CLIPS}
    report = {}
    for stem in CLIPS:
        fine_dir = FINE / stem
        src = SRC / f"{stem}.MP4"
        dst = OUT / f"{stem}_4ch_aligned_final.mp4"
        print(f"\n=== {stem}: building mono polarity-corrected WAVs", flush=True)
        wavs = []
        for i in range(4):
            f32 = fine_dir / f"ch{i}_fine.f32"
            wav = fine_dir / f"ch{i}_pol.wav"
            cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
                   "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(f32)]
            if flips[stem].get(i):
                cmd += ["-af", "aeval=val(0)*-1:c=same"]
            cmd += ["-c:a", "pcm_s24le", str(wav)]
            r = run(cmd)
            if r.returncode != 0:
                raise SystemExit(f"wav {stem} ch{i} failed: {r.stderr[-300:]}")
            wavs.append(wav)
            print(f"  ch{i}: {'INVERTED' if flips[stem].get(i) else 'as-is'}", flush=True)
        print(f"  remuxing video stream copy -> {dst.name}", flush=True)
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(src)]
        for w in wavs:
            cmd += ["-i", str(w)]
        cmd += ["-map", "0:v:0"]
        for i in range(4):
            cmd += ["-map", f"{i + 1}:a"]
        cmd += ["-map_metadata", "0", "-c:v", "copy", "-c:a", "pcm_s24be",
                "-movflags", "+faststart", str(dst)]
        r = run(cmd)
        if r.returncode != 0:
            raise SystemExit(f"remux failed: {r.stderr[-500:]}")
        # ---- verification
        src_probe, out_probe = probe(src), probe(dst)
        src_v = [s for s in src_probe["streams"] if s["codec_type"] == "video"]
        out_v = [s for s in out_probe["streams"] if s["codec_type"] == "video"]
        src_a = [s for s in src_probe["streams"] if s["codec_type"] == "audio"]
        out_a = [s for s in out_probe["streams"] if s["codec_type"] == "audio"]
        hs_src = streamhash(src, "0:v:0")
        hs_out = streamhash(dst, "0:v:0")
        pk_src, pk_out = packets(src), packets(dst)
        md5_ok = all(audio_md5(dst, i) == audio_md5(wavs[i], 0) for i in range(4))
        rep = {
            "stem": stem, "output": str(dst),
            "output_gib": round(dst.stat().st_size / 2**30, 2),
            "flip": {str(k): v for k, v in flips[stem].items()},
            "video_bit_exact": hs_src == hs_out,
            "video_packets_identical": pk_src == pk_out,
            "video_packets": [len(pk_src), len(pk_out)],
            "video_frames": [src_v[0].get("nb_frames"), out_v[0].get("nb_frames")],
            "avg_frame_rate": [src_v[0].get("avg_frame_rate"),
                               out_v[0].get("avg_frame_rate")],
            "r_frame_rate": [src_v[0].get("r_frame_rate"),
                             out_v[0].get("r_frame_rate")],
            "audio_tracks": [len(src_a), len(out_a)],
            "audio": [{"codec": s.get("codec_name"),
                       "tag": s.get("codec_tag_string"),
                       "rate": s.get("sample_rate"),
                       "ch": s.get("channels"),
                       "bits": s.get("bits_per_sample"),
                       "dur_ts": s.get("duration_ts")} for s in out_a],
            "audio_duration_ts_src": sorted({int(s["duration_ts"]) for s in src_a}),
            "audio_md5_matches_wavs": md5_ok,
            "duration_s": [src_probe["format"].get("duration"),
                           out_probe["format"].get("duration")],
        }
        report[stem] = rep
        print(f"  video_bit_exact={rep['video_bit_exact']} "
              f"packets_identical={rep['video_packets_identical']} "
              f"audio_md5_ok={rep['audio_md5_matches_wavs']} "
              f"size={rep['output_gib']} GiB", flush=True)
        for s in rep["audio"]:
            print(f"    {s['codec']}/{s['tag']} {s['rate']}Hz {s['ch']}ch "
                  f"{s['bits']}bit dur_ts={s['dur_ts']}", flush=True)
        print(f"  duration {rep['duration_s']}", flush=True)
    (Path(r"E:\1kt_work\long_align") / "remux_final.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写 E:/1kt_work/long_align/remux_final.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
