#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a smooth per-channel shift trajectory and apply it with audio resampling.

Trajectory source: track_<stem>.json (continuity tracker, 60 s / 10 s windows).
The tracker still picks up content-driven jumps, so the trajectory is built from
2-minute weighted-median anchors (weight = |CC|), then linearly interpolated.
Edges hold the nearest anchor; a hard guard rejects |shift| > 4800 samples (100 ms).

Apply: out[k] = interp(x, k + s(k)) with s(k) the interpolated shift in samples.
This is a fractional resampler (audio resampling is allowed; video is untouched).
Channel 2 (CH2) is the constant -4 partner of the anchor and is handled as a constant.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000
MAX_SHIFT = 4800
BIN_S = 120.0
MIN_CC = 0.08
ANCHOR = 0


def build_curve(stem: str, target: int, n: int) -> dict:
    data = json.loads((OUT / f"track_{stem}.json").read_text(encoding="utf-8"))
    rec = data[f"{stem}|{target}"]
    rows = [r for r in rec["trajectory"] if r["accepted"] and r["cc"] >= MIN_CC]
    if not rows:
        return {"ok": False, "reason": "no_points"}
    # 2 分钟分箱, |CC| 加权中位数作为锚点
    bins: dict[int, list] = {}
    for r in rows:
        b = int(r["t_min"] * 60.0 // BIN_S)
        bins.setdefault(b, []).append(r)
    anchors = []
    for b in sorted(bins):
        rs = sorted(bins[b], key=lambda r: (r["lag"], -r["cc"]))
        w = np.array([r["cc"] for r in rs])
        ys = np.array([r["lag"] for r in rs], dtype=np.float64)
        order = np.argsort(ys)
        ys, w = ys[order], w[order]
        cum = np.cumsum(w)
        med = float(ys[np.searchsorted(cum, cum[-1] / 2.0)])
        anchors.append([b * BIN_S, med, float(w.max())])
    if len(anchors) < 2:
        return {"ok": False, "reason": "too_few_anchors", "n": len(anchors)}
    t = np.array([a[0] for a in anchors], dtype=np.float64) * SR
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    # 简单抗跳变: 与上一锚点差 > 120 采样时用上一锚点限幅 (保留缓慢漂移)
    for k in range(1, len(y)):
        if abs(y[k] - y[k - 1]) > 120:
            y[k] = y[k - 1] + float(np.sign(y[k] - y[k - 1]) * 120.0)
    dur = n / SR
    grid = np.arange(0, n, 48000, dtype=np.float64)      # 每 1 s 一个点
    s = np.interp(grid, t, y)
    if np.abs(s).max() > MAX_SHIFT:
        return {"ok": False, "reason": "shift_out_of_range",
                "max_shift": float(np.abs(s).max())}
    return {"ok": True, "grid_t": grid.tolist(), "grid_s": s.tolist(),
            "anchors": anchors, "duration_s": dur,
            "max_shift": float(np.abs(s).max())}


def apply_channel(stem: str, i: int, curve: dict, n: int, outdir: Path) -> dict:
    src = np.memmap(la.pcm_path(stem, i), dtype=np.float32, mode="r")
    grid_t = np.asarray(curve["grid_t"], dtype=np.float64)
    grid_s = np.asarray(curve["grid_s"], dtype=np.float64)
    fh = (outdir / f"ch{i}_tv.f32").open("wb")
    CH = 5_000_000
    for s0 in range(0, n, CH):
        s1 = min(n, s0 + CH)
        idx = np.arange(s0, s1, dtype=np.float64)
        shift = np.interp(idx, grid_t, grid_s)
        pos = idx + shift
        # 越界处必须补 0 静音 (需求), 不是夹到边缘
        invalid = (pos < 0.0) | (pos > n - 1.0)
        np.clip(pos, 0.0, n - 1.0, out=pos)
        block = np.interp(pos, np.arange(s0, s1, dtype=np.float64),
                          np.asarray(src[s0:s1], dtype=np.float64))
        block[invalid] = 0.0
        fh.write(block.astype(np.float32).tobytes())
    fh.close()
    del src
    return {"stream": i, "out": str(outdir / f"ch{i}_tv.f32")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    stem = args.stem
    n = la.n_samples(stem)
    outdir = OUT / "aligned_tv" / stem
    outdir.mkdir(parents=True, exist_ok=True)
    meta = {"stem": stem, "n": n, "channels": {}}
    # CH1 anchor: shift 0 (copy raw)
    src = np.memmap(la.pcm_path(stem, 0), dtype=np.float32, mode="r")
    with (outdir / "ch0_tv.f32").open("wb") as fh:
        CH = 5_000_000
        for s0 in range(0, n, CH):
            fh.write(np.asarray(src[s0:s0 + CH], dtype=np.float32).tobytes())
    del src
    meta["channels"]["0"] = {"shift": "constant 0"}
    # CH2: 紧耦合对, 恒定 -4 (来自窗级/整段一致估计)
    src = np.memmap(la.pcm_path(stem, 1), dtype=np.float32, mode="r")
    out = np.zeros(n, dtype=np.float32)
    out[4:] = np.asarray(src[:n - 4])
    out.astype(np.float32).tofile(outdir / "ch1_tv.f32")
    del src, out
    meta["channels"]["1"] = {"shift": "constant -4 samples"}
    # CH3 / CH4: 时变曲线
    for i in (2, 3):
        curve = build_curve(stem, i, n)
        if not curve.get("ok"):
            print(f"CH{i + 1}: curve failed {curve}")
            meta["channels"][str(i)] = {"ok": False, "reason": curve.get("reason")}
            continue
        apply_channel(stem, i, curve, n, outdir)
        meta["channels"][str(i)] = {
            "ok": True, "max_shift": curve["max_shift"],
            "duration_s": curve["duration_s"],
            "anchors": curve["anchors"],
            "grid_t": curve["grid_t"], "grid_s": curve["grid_s"],
        }
        print(f"CH{i + 1}: time-varying applied, max|shift|={curve['max_shift']:.0f} "
              f"samples ({curve['max_shift'] / SR * 1000:.1f} ms)")
    # 4ch wav
    raw = np.memmap(outdir / "tmp_inter.f32", dtype=np.float32, mode="w+",
                    shape=(n, 4))
    for i, nm in enumerate(("ch0_tv.f32", "ch1_tv.f32", "ch2_tv.f32", "ch3_tv.f32")):
        if not (outdir / nm).is_file():
            continue
        m = np.memmap(outdir / nm, dtype=np.float32, mode="r")
        CH = 5_000_000
        for s0 in range(0, n, CH):
            raw[s0:s0 + CH, i] = np.asarray(m[s0:s0 + CH])
        del m
    raw.flush()
    del raw
    import subprocess
    cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
           "-f", "f32le", "-ar", str(SR), "-ac", "4",
           "-i", str(outdir / "tmp_inter.f32"),
           "-c:a", "pcm_s24le", str(outdir / "aligned_4ch_tv.wav")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr[-300:])
    (outdir / "tmp_inter.f32").unlink()
    # per-channel 24-bit wav (remux 用)
    import subprocess as _sp
    for i in range(4):
        f = outdir / f"ch{i}_tv.f32"
        if not f.is_file():
            continue
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
               "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(f),
               "-c:a", "pcm_s24le", str(outdir / f"ch{i}_tv.wav")]
        r = _sp.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"wav ch{i} failed: {r.stderr[-200:]}")
    (OUT / f"applytv_{stem}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写 {outdir / 'aligned_4ch_tv.wav'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
