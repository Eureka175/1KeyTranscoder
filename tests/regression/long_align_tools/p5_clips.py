#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P5: five random short clips for manual spot check.

Requirements from the user:
  * random positions
  * cut on the ORIGINAL video GOP boundary (keyframe start)
  * NO video re-encode (stream copy) and no long-range re-encode anywhere
  * total duration <= 60 s
  * keep metadata like before

Clips are cut from the aligned deliverables (video copied bit-exactly, the four
aligned PCM audio tracks included). For each clip the script proves the video is
bit-exact: it cuts the same time range from the source and compares the video
streamhash; it also checks packet counts, frame rate, audio track layout and that
the clip starts on a keyframe.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
FFMPEG = la.FFMPEG
FFPROBE = la.FFPROBE
CLIP_DIR = OUT / "clips"
DELIVER = {
    "20260919_C1305": OUT / "deliver" / "20260919_C1305_4ch_aligned.mp4",
    "20260919_C1306": OUT / "deliver" / "20260919_C1306_4ch_aligned.mp4",
}
SOURCE = {
    "20260919_C1305": la.REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    "20260919_C1306": la.REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
}
CLIP_S = 10.0
TOTAL_S = 60.0
SEED = 20261024


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def keyframes(path: Path) -> list[float]:
    r = run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0",
             str(path)])
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[-1]:
            try:
                out.append(float(parts[0]))
            except ValueError:
                pass
    return sorted(out)


def streamhash(path: Path, stream: str) -> str:
    r = run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-map", stream, "-c", "copy", "-f", "streamhash",
             "-hash", "sha256", "-"])
    return r.stdout.strip()


def probe(path: Path) -> dict:
    r = run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)])
    return json.loads(r.stdout or "{}")


def cut(src: Path, start: float, dur: float, dst: Path,
        video_only: bool = False) -> int:
    cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y",
           "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", str(src),
           "-map_metadata", "0"]
    if video_only:
        cmd += ["-map", "0:v:0", "-c", "copy", "-an", "-f", "mp4", str(dst)]
    else:
        cmd += ["-map", "0", "-c", "copy", "-movflags", "+faststart",
                "-f", "mp4", str(dst)]
    return run(cmd).returncode


def main() -> int:
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    plan = [
        ("20260919_C1305", 3, 0),
        ("20260919_C1306", 2, 3),
    ]
    total = 0.0
    clips = []
    for stem, count, _ in plan:
        d = DELIVER[stem]
        if not d.is_file():
            print(f"缺交付文件: {d}")
            continue
        kfs = keyframes(d)
        print(f"{stem}: {len(kfs)} keyframes")
        # 随机挑互不重叠的起点
        picks = []
        tries = 0
        while len(picks) < count and tries < 500:
            tries += 1
            t = rng.choice(kfs)
            if any(abs(t - p) < 60.0 for p in picks):
                continue
            if t + CLIP_S > kfs[-1] + 1.0:
                continue
            picks.append(round(t, 3))
        picks.sort()
        for k, t in enumerate(picks):
            name = f"{stem}_gopclip{k + 1}_{int(t)}s.mp4"
            dst = CLIP_DIR / name
            rc = cut(d, t, CLIP_S, dst)
            if rc != 0 or not dst.is_file():
                print(f"  切失败: {t}s")
                continue
            ref = CLIP_DIR / f"_ref_{stem}_{int(t)}.mp4"
            cut(SOURCE[stem], t, CLIP_S, ref, video_only=True)
            hs_src = streamhash(ref, "0:v:0")
            hs_clip = streamhash(dst, "0:v:0")
            info = probe(dst)
            v = [s for s in info["streams"] if s["codec_type"] == "video"]
            a = [s for s in info["streams"] if s["codec_type"] == "audio"]
            dur = float(info["format"]["duration"])
            # 首帧是否关键帧起点
            r = run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "packet=flags", "-of", "csv=p=0", str(dst)])
            first_flags = (r.stdout.splitlines() or [""])[0]
            entry = {
                "clip": name, "stem": stem, "start_s": t, "duration_s": dur,
                "video_bit_exact": hs_src == hs_clip,
                "video_streamhash": hs_clip[:16],
                "first_packet_keyframe": "K" in first_flags,
                "video_frames": v[0].get("nb_frames") if v else None,
                "avg_frame_rate": v[0].get("avg_frame_rate") if v else None,
                "audio_tracks": len(a),
                "audio": [{"codec": s.get("codec_name"),
                           "rate": s.get("sample_rate"),
                           "ch": s.get("channels"),
                           "bits": s.get("bits_per_sample"),
                           "dur_ts": s.get("duration_ts")} for s in a],
                "bytes": dst.stat().st_size,
            }
            ref.unlink(missing_ok=True)
            clips.append(entry)
            total += dur
            print(f"  {name}: {dur:.2f}s video_exact={entry['video_bit_exact']} "
                  f"keyframe={entry['first_packet_keyframe']} "
                  f"audio={entry['audio_tracks']}tracks")
    report = {"clips": clips, "total_duration_s": round(total, 3),
              "total_under_60s": total <= 60.0,
              "all_video_bit_exact": all(c["video_bit_exact"] for c in clips),
              "all_start_on_keyframe": all(c["first_packet_keyframe"] for c in clips),
              "all_audio_4ch": all(c["audio_tracks"] == 4 for c in clips),
              "seed": SEED}
    (OUT / "p5_clips.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n总时长 {total:.2f}s <=60s: {report['total_under_60s']}")
    print(f"视频全部 bit-exact: {report['all_video_bit_exact']}; "
          f"全部从关键帧开始: {report['all_start_on_keyframe']}; "
          f"全部 4 轨音频: {report['all_audio_4ch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
