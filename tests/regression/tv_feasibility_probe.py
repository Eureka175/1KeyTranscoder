#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feasibility probe for per-window (time-varying) correction on archive clips.

The question this answers with measurements, not opinion:

  1. **Can the offset be measured per window on these clips?** The production
     time-varying geometry is 30 s window / 10 s hop, which is designed for long
     takes. A 6.5 s archive clip is shorter than one window, so the geometry has
     to shrink; the probe sweeps several geometries and reports how many windows
     reach `min_frames` usable frames.
  2. **What does the offset actually look like across the clip?** A slow drift
     can be corrected by piecewise integer shifts; a step jump cannot be hidden
     by them. The probe prints the per-window median delay and the largest
     step between adjacent windows (in samples), because that step is what a
     splice would have to absorb.
  3. **How much of the error would a piecewise correction remove?** Residual if
     each window is corrected by its own median vs. the single global shift the
     production path applies today.

Run:  python tests/regression/tv_feasibility_probe.py [max_files]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
OUT = REPO / "work" / "regression_r087" / "out"
SCRATCH = Path(r"E:\1kt_scratch\tv_probe")
SR = 48000
MIN_FRAMES = 3
GEOMETRIES = [(1.0, 0.5), (1.5, 0.5), (2.0, 0.5), (3.0, 1.0)]


def decode(path: Path, idx: int, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(path),
         "-map", f"0:a:{idx}", "-vn", "-sn", "-dn",
         "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SR),
         str(out)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 0


def measure(path: Path, anchor: int, tgt: int, tag: str):
    from core.channel_sync import effective_opts
    from core.sync_estimate import estimate_pair, open_source

    eff = effective_opts(None)
    w = SCRATCH / tag
    if w.exists():
        shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True, exist_ok=True)
    ra, rb = w / "a.f32", w / "b.f32"
    if not (decode(path, anchor, ra) and decode(path, tgt, rb)):
        return None
    a = open_source(ra, "f32")
    b = open_source(rb, "f32")
    try:
        e = estimate_pair(
            a, b, sample_rate=SR,
            search_window_ms=eff["search_window_ms"],
            frame_ms=eff["frame_ms"], hop_ms=eff["hop_ms"],
            anchor_segment_seconds=eff["anchor_segment_seconds"],
            min_confidence=eff["min_confidence"],
            coarse_rate=eff["coarse_rate"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
            min_usable_frames=eff["min_usable_frames"],
            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
            ref_index=anchor, tgt_index=tgt)
        n = min(a.shape[0], b.shape[0])
    finally:
        for s in (a, b):
            c = getattr(s, "close", None)
            if callable(c):
                c()
    return e, n


def window_track(e, n: int, window_s: float, hop_s: float) -> dict:
    """Aggregate frame measurements into plan windows (production convention:
    frames are laid out by hop, one window covers win/hop frame slots)."""
    hop = int(round(hop_s * SR))
    win = int(round(window_s * SR))
    per_win = max(1, win // hop)
    n_win = max(0, (n - win) // hop + 1) if n >= win else 0
    frames = e.frames
    med: list[float | None] = []
    used: list[int] = []
    for k in range(n_win):
        lo, hi = k * per_win, (k + 1) * per_win
        vals = [f.delay_samples for f in frames[lo:hi]
                if getattr(f, "usable", True)
                and f.delay_samples == f.delay_samples]
        used.append(len(vals))
        med.append(sorted(vals)[len(vals) // 2] if vals else None)
    good = [m for m in med if m is not None]
    steps = [abs(good[i + 1] - good[i]) for i in range(len(good) - 1)]
    return {"window_s": window_s, "hop_s": hop_s, "required": n_win,
            "reached": len(good), "frames_per_window": used,
            "median": med,
            "max_step": max(steps) if steps else 0.0,
            "span": (max(good) - min(good)) if good else 0.0}


def pick_cases(limit: int) -> list[dict]:
    arm = json.loads((OUT / "ARCH_on5.json").read_text(encoding="utf-8"))
    cases = []
    for r in arm:
        p = Path(r.get("file") or "")
        name = r.get("name") or p.name
        for c in r.get("channels") or []:
            if c.get("decision") != "untouched":
                continue
            d = c.get("delay_ms")
            if not isinstance(d, (int, float)) or abs(d) < 1000 * 5 / SR:
                continue
            if not p.is_file() or "_superseded" in str(p):
                continue
            if p.stat().st_size > 300 * 1024 ** 2:
                continue
            cases.append({"path": p, "name": name,
                          "anchor": r.get("anchor"),
                          "tgt": c.get("stream"),
                          "reason": c.get("reason"),
                          "delay_ms": d,
                          "frames": c.get("usable_frames")})
            break
        if len(cases) >= limit:
            break
    return cases


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    cases = pick_cases(limit)
    print(f"样本 {len(cases)} 件\n")
    results = []
    for i, c in enumerate(cases, 1):
        anchor = c["anchor"]
        if anchor is None or anchor == c["tgt"]:
            print(f"[{i}] {c['name']}: 锚点不可用, 跳过")
            continue
        print(f"[{i}/{len(cases)}] {c['name']} 目标 CH{c['tgt'] + 1} "
              f"锚点 CH{anchor + 1} 偏差 {c['delay_ms']:+.2f}ms "
              f"({c['reason']}, {c['frames']} 帧)")
        got = measure(c["path"], anchor, c["tgt"], f"c{i}")
        if got is None:
            print("    解码失败")
            continue
        e, n = got
        dur = n / SR
        print(f"    时长 {dur:.2f}s, 总帧 {len(e.frames)}, "
              f"全局偏差 {e.delay_samples * 1000 / SR:+.3f}ms "
              f"({e.delay_samples:+.1f} 采样)")
        rec = {"name": c["name"], "target": c["tgt"], "anchor": anchor,
               "duration_s": round(dur, 2), "reason": c["reason"],
               "global_delay_samples": round(float(e.delay_samples), 2),
               "geometries": []}
        for win_s, hop_s in GEOMETRIES:
            if win_s * SR > n:
                continue
            t = window_track(e, n, win_s, hop_s)
            t["yield_pct"] = (100.0 * t["reached"] / t["required"]
                              if t["required"] else 0.0)
            rec["geometries"].append(t)
            meds = [None if m is None else round(m, 1) for m in t["median"]]
            print(f"    几何 {win_s:.1f}s/{hop_s:.1f}s: "
                  f"窗 {t['reached']}/{t['required']} "
                  f"({t['yield_pct']:.0f}%), 最大跳变 {t['max_step']:.1f} 采样, "
                  f"全片跨度 {t['span']:.1f} 采样")
            print(f"      每窗中位: {meds}")
        results.append(rec)
    (OUT / "tv_feasibility_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {OUT / 'tv_feasibility_probe.json'}")

    best = []
    for r in results:
        for g in r["geometries"]:
            if g["yield_pct"] >= 100.0:
                best.append((r["name"], g["window_s"], g["hop_s"],
                             g["max_step"], g["span"]))
    print(f"\n全覆盖几何组合数: {len(best)}")
    for b in best[:10]:
        print(f"  {b[0]} 窗{b[1]}s/跳{b[2]}s 最大跳变={b[3]:.1f} 跨度={b[4]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
