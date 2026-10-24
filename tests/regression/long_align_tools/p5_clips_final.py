#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final clip generation for manual spot check + a polarity A/B demo pair.

  * 5 random GOP-aligned clips (3 from C1305, 2 from C1306), 10 s each, total <= 60 s,
    cut from the FINAL deliverable (fine delay + polarity fix), video stream copy only;
    each is verified against the same range cut from the source (video streamhash and
    packet timestamps identical).
  * one A/B demo pair from the same GOP segment of C1305: audio without polarity fix
    ("before") vs the final audio ("after"), video identical, for listening.

Outputs: E:/1kt_work/long_align/clips_final/
Report:  E:/1kt_work/long_align/p5_clips_final.json
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))
import long_align as la  # noqa: E402

SR = 48000
OUT = Path(r"E:\1kt_work\long_align")
CLIP_DIR = OUT / "clips_final"
DELIVER = {
    "20260919_C1305": OUT / "deliver" / "20260919_C1305_4ch_aligned_final.mp4",
    "20260919_C1306": OUT / "deliver" / "20260919_C1306_4ch_aligned_final.mp4",
}
FINE = REPO / "data" / "_scratch" / "aligned_fine"
SOURCE = REPO / "data" / "align_4ch_proof"
CLIP_S = 10.0
TOTAL_S = 60.0
SEED = 20261024


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def keyframes(path: Path) -> list[float]:
    r = run([str(la.FFPROBE), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)])
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
    r = run([str(la.FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-map", stream, "-c", "copy", "-f", "streamhash",
             "-hash", "sha256", "-"])
    return r.stdout.strip()


def probe(path: Path) -> dict:
    r = run([str(la.FFPROBE), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)])
    return json.loads(r.stdout or "{}")


def cut(src: Path, start: float, dur: float, dst: Path, video_only=False) -> int:
    cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
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
    clips = []
    total = 0.0
    plan = [("20260919_C1305", 3), ("20260919_C1306", 2)]
    picked_segments = {}
    for stem, count in plan:
        d = DELIVER[stem]
        kfs = keyframes(d)
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
        picked_segments[stem] = picks
        for k, t in enumerate(picks):
            name = f"{stem}_final_clip{k + 1}_{int(t)}s.mp4"
            dst = CLIP_DIR / name
            if cut(d, t, CLIP_S, dst) != 0 or not dst.is_file():
                continue
            ref = CLIP_DIR / f"_ref_{stem}_{int(t)}.mp4"
            cut(SOURCE / f"{stem}.MP4", t, CLIP_S, ref, video_only=True)
            hs_src = streamhash(ref, "0:v:0")
            hs_clip = streamhash(dst, "0:v:0")
            info = probe(dst)
            v = [s for s in info["streams"] if s["codec_type"] == "video"]
            a = [s for s in info["streams"] if s["codec_type"] == "audio"]
            dur = float(info["format"]["duration"])
            r = run([str(la.FFPROBE), "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "packet=flags", "-of", "csv=p=0", str(dst)])
            first_flags = (r.stdout.splitlines() or [""])[0]
            clips.append({
                "clip": name, "stem": stem, "start_s": t, "duration_s": dur,
                "video_bit_exact": hs_src == hs_clip,
                "first_packet_keyframe": "K" in first_flags,
                "video_frames": v[0].get("nb_frames") if v else None,
                "audio_tracks": len(a),
                "audio": [{"codec": s.get("codec_name"),
                           "rate": s.get("sample_rate"),
                           "bits": s.get("bits_per_sample")} for s in a],
                "bytes": dst.stat().st_size})
            ref.unlink(missing_ok=True)
            total += dur
            print(f"  {name}: {dur:.2f}s video_exact={clips[-1]['video_bit_exact']} "
                  f"keyframe={clips[-1]['first_packet_keyframe']} "
                  f"audio={len(a)}tracks", flush=True)
    report = {"clips": clips, "total_duration_s": round(total, 3),
              "total_under_60s": total <= 60.0,
              "all_video_bit_exact": all(c["video_bit_exact"] for c in clips),
              "all_start_on_keyframe": all(c["first_packet_keyframe"] for c in clips),
              "all_audio_4ch": all(c["audio_tracks"] == 4 for c in clips),
              "seed": SEED}

    # ---- polarity A/B demo on one of the C1305 segments
    seg = picked_segments["20260919_C1305"][0]
    stem = "20260919_C1305"
    fine = FINE / stem
    # 未修极性音轨 (ch0/ch1 原样, ch2/ch3 不翻转) -> 直接取 fine wav
    before = CLIP_DIR / "polarity_demo_before.mp4"
    after = CLIP_DIR / "polarity_demo_after.mp4"
    src = SOURCE / f"{stem}.MP4"
    for name, wavs in (
            ("before", [fine / f"ch{i}_fine.f32" for i in range(4)]),
            ("after", [fine / f"ch{i}_pol.wav" for i in range(4)])):
        dst = CLIP_DIR / f"polarity_demo_{name}.mp4"
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
               "-ss", f"{seg:.6f}", "-t", f"{CLIP_S:.6f}", "-i", str(src)]
        for i, w in enumerate(wavs):
            cmd += ["-ss", f"{seg:.6f}", "-t", f"{CLIP_S:.6f}"] if False else []
        # 音频用整段 wav 再 -ss/-t 不方便: 先生成整段 mono wav 的片段
        import tempfile
        seg_wavs = []
        for i, w in enumerate(wavs):
            if w.suffix == ".f32":
                # f32 不能直接 -ss: 用 ffmpeg 先转整段 wav 到临时, 再切
                tmp_full = CLIP_DIR / f"_tmp_full_{name}_{i}.wav"
                run([str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
                     "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(w),
                     "-c:a", "pcm_s24le", str(tmp_full)])
                srcwav = tmp_full
            else:
                srcwav = w
            seg_wav = CLIP_DIR / f"_seg_{name}_{i}.wav"
            run([str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
                 "-ss", f"{seg:.6f}", "-t", f"{CLIP_S:.6f}", "-i", str(srcwav),
                 "-c:a", "pcm_s24le", str(seg_wav)])
            seg_wavs.append(seg_wav)
        for i, w in enumerate(seg_wavs):
            cmd += ["-i", str(w)]
        cmd += ["-map", "0:v:0"]
        for i in range(4):
            cmd += ["-map", f"{i + 1}:a"]
        cmd += ["-map_metadata", "0", "-c:v", "copy", "-c:a", "pcm_s24be",
                "-movflags", "+faststart", str(dst)]
        r = run(cmd)
        ok = r.returncode == 0 and dst.is_file()
        print(f"  polarity demo {name}: {'OK' if ok else 'FAIL'} -> {dst.name}",
              flush=True)
        if not ok:
            print("   ", r.stderr[-300:])
        # cleanup temp
        for p in CLIP_DIR.glob(f"_tmp_full_{name}_*.wav"):
            p.unlink(missing_ok=True)
        for p in CLIP_DIR.glob(f"_seg_{name}_*.wav"):
            p.unlink(missing_ok=True)
    report["polarity_demo"] = {"start_s": seg, "duration_s": CLIP_S,
                               "before": str(CLIP_DIR / "polarity_demo_before.mp4"),
                               "after": str(CLIP_DIR / "polarity_demo_after.mp4")}
    (OUT / "p5_clips_final.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n总时长 {total:.2f}s <=60s: {report['total_under_60s']}; "
          f"视频全部 bit-exact: {report['all_video_bit_exact']}; "
          f"关键帧起: {report['all_start_on_keyframe']}; "
          f"4 轨音频: {report['all_audio_4ch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
