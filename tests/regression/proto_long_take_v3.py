#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prototype v3: why does CH1/CH2 keep a ~100-sample residual tail?

v1 facts (E:\\1kt_work\\proto_long_take\\proto_long_take.json):
    linear / nearest-centre   p50 0-1 sample on all six channels
                              p90 1-4 on CH4, but 100-124 on CH1/CH2
    single global shift       p90 138-368

v2 tried to flag "ambiguous" windows with a peak-prominence rule and rejected
95 % of them, which is itself informative: the correlation surface on this
material is broad (music bed), so a discrete runner-up peak is not a usable
ambiguity signal.

v3 therefore keeps v1's estimator (plain normalised cross-correlation, calibrated
exact) and asks the complementary question: is the tail caused by *weak*
windows whose 100 ms-scale correlation is broad enough to put the peak hundreds
of samples off? It measures once per pair, then rebuilds the trajectory and
re-measures the residual for a sweep of peak thresholds (0.10 / 0.25 / 0.40 /
0.60), keeping every per-window value so the answer does not need another run.

A window is "trusted" at threshold th when its measured peak >= th. Everything
else is filled by linear interpolation between trusted windows (D2 = linear) with
the value attached at the window centre (D5 = nearest-centre), which is the
configuration that won in v1.
"""
from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

PCM_CACHE = Path(r"E:\1kt_scratch\pcm")
OUT = Path(r"E:\1kt_work\proto_long_take")

SR = 48000
DEC = 4
WIN_S = 30.0
HOP_S = 10.0
SEARCH_MS = 250.0
FINE_RADIUS = 8
THRESHOLDS = (0.10, 0.25, 0.40, 0.60)

CLIPS = [
    (REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4", 2),
    (REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4", 2),
]


def load_channels(stem: str, n: int = 4) -> list[np.memmap]:
    return [np.memmap(PCM_CACHE / stem / f"ch{i}.f32", dtype=np.float32, mode="r")
            for i in range(n)]


def window_delay(ref: np.ndarray, tgt: np.ndarray) -> tuple[float, float]:
    """v1 estimator verbatim: plain CC coarse (pooled) then fine dot-product."""
    sr_c = SR // DEC
    search_c = int(round(SEARCH_MS / 1000.0 * sr_c))
    r = np.asarray(ref, dtype=np.float64)[: (ref.size // DEC) * DEC].reshape(-1, DEC).mean(axis=1)
    g = np.asarray(tgt, dtype=np.float64)[: (tgt.size // DEC) * DEC].reshape(-1, DEC).mean(axis=1)
    n = min(r.size, g.size)
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    A = np.fft.rfft(r[:n] - r[:n].mean(), nfft)
    B = np.fft.rfft(g[:n] - g[:n].mean(), nfft)
    surf = np.fft.irfft(A * np.conj(B), nfft)
    cand = np.concatenate([surf[:search_c + 1], surf[-search_c:]])
    idx = int(np.argmax(np.abs(cand)))
    m_c = idx if idx <= search_c else idx - (2 * search_c + 1)
    coarse = m_c * DEC
    seg = int(2.0 * SR)
    c0 = ref.size // 2 - seg // 2
    a = np.asarray(ref[c0:c0 + seg], dtype=np.float64)
    a = a - a.mean()
    center = -int(round(coarse))
    best = (0.0, 0.0)
    for m in range(center - FINE_RADIUS, center + FINE_RADIUS + 1):
        b0 = c0 + m
        if b0 < 0 or b0 + seg > tgt.size:
            continue
        b = np.asarray(tgt[b0:b0 + seg], dtype=np.float64)
        b = b - b.mean()
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom < 1e-9:
            continue
        val = float(np.dot(a, b) / denom)
        if abs(val) > abs(best[1]):
            best = (float(m), val)
    if best[0] == 0.0 and best[1] == 0.0:
        return float("nan"), 0.0
    return float(best[0]), abs(best[1])


def calibrate() -> None:
    rng = np.random.default_rng(7)
    x = rng.standard_normal(SR * 40).astype(np.float32)
    d, q = window_delay(x, np.concatenate([np.zeros(123, np.float32), x[:-123]]))
    assert abs(d - 123) <= 1.0, f"delay sign: {d}"
    d2, _ = window_delay(x, np.concatenate([x[200:], np.zeros(200, np.float32)]))
    assert abs(d2 + 200) <= 1.0, f"reverse sign: {d2}"
    print(f"  calibration ok: +123 -> {d:.2f}, -200 -> {d2:.2f}")


def measure_windows(channels, anchor):
    n = channels[0].shape[0]
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    starts = list(range(0, max(0, n - win) + 1, hop))
    direct = {}
    for i in range(len(channels)):
        if i == anchor:
            continue
        direct[i] = [window_delay(channels[anchor][s:s + win],
                                  channels[i][s:s + win]) for s in starts]
        good = sum(1 for d, q in direct[i] if np.isfinite(d) and q >= 0.10)
        print(f"    CH{anchor + 1}->CH{i + 1}: {good}/{len(starts)} peak>=0.10",
              flush=True)
    return {"starts": starts, "win": win, "hop": hop, "direct": direct, "n": n}


def evaluate(channels, anchor_arr, starts, win, n, vals, th):
    pts = [(s + win / 2.0, d) for s, (d, q) in zip(starts, vals)
           if np.isfinite(d) and q >= th]
    if not pts:
        return [], 0
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    rows = []
    for w, s in enumerate(starts):
        pos = np.arange(s, s + win, dtype=np.float64)
        shift = np.interp(pos, xs, ys)
        src = np.asarray(channels[s:s + win], dtype=np.float64)
        posi = pos + shift
        np.clip(posi, 0, n - 1, out=posi)
        y = np.interp(posi, np.arange(s, s + win, dtype=np.float64), src)
        d, q = window_delay(np.asarray(anchor_arr[s:s + win]), y)
        nearest = float(np.min(np.abs(xs - (s + win / 2.0))))
        rows.append({"w": w, "res": d if np.isfinite(d) and q >= 0.10 else None,
                     "res_any": d if np.isfinite(d) else None,
                     "q": q, "nearest_trusted_s": nearest,
                     "shift_mid": float(shift[win // 2]),
                     "raw_d": vals[w][0] if np.isfinite(vals[w][0]) else None,
                     "raw_q": vals[w][1]})
    return rows, len(pts)


def summarise(vals):
    vals = [abs(float(v)) for v in vals if v is not None and np.isfinite(v)]
    if not vals:
        return {"n": 0}
    a = np.asarray(vals)
    return {"n": int(a.size), "median": float(np.median(a)),
            "p90": float(np.percentile(a, 90)), "max": float(np.max(a)),
            "le1": int((a <= 1).sum()), "frac_le1": float((a <= 1).mean())}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("calibration")
    calibrate()
    everything = []
    for clip, anchor in CLIPS:
        stem = clip.stem
        print(f"\n=== {stem} (anchor CH{anchor + 1})")
        channels = load_channels(stem)
        n = channels[0].shape[0]
        meas = measure_windows(channels, anchor)
        win, starts = meas["win"], meas["starts"]
        for i, vals in meas["direct"].items():
            res = {"clip": stem, "target": i, "anchor": anchor,
                   "n_windows": len(starts),
                   "raw": summarise([d for d, q in vals if np.isfinite(d) and q >= 0.10]),
                   "peak_p50": float(np.median([q for d, q in vals if np.isfinite(d)]))}
            best_rows = None
            for th in THRESHOLDS:
                rows, npts = evaluate(channels[i], channels[anchor], starts, win, n, vals, th)
                key = f"th{th:.2f}"
                res[key] = {"trusted": npts,
                            "residual": summarise([r["res"] for r in rows]),
                            "in_gap": summarise([r["res"] for r in rows
                                                 if r["res"] is not None
                                                 and r["nearest_trusted_s"] > win / 2 + HOP_S]),
                            "covered": summarise([r["res"] for r in rows
                                                  if r["res"] is not None
                                                  and r["nearest_trusted_s"] <= win / 2 + HOP_S])}
                if best_rows is None:
                    best_rows = rows
                print(f"    CH{i + 1} {key}: trusted={npts} "
                      f"p50={res[key]['residual'].get('median')} "
                      f"p90={res[key]['residual'].get('p90')} "
                      f"<=1 {res[key]['residual'].get('le1')}/{res[key]['residual'].get('n')} "
                      f"| covered p90={res[key]['covered'].get('p90')} "
                      f"gap n={res[key]['in_gap'].get('n')} p90={res[key]['in_gap'].get('p90')}")
                if th == 0.40:
                    res["rows_th040"] = rows
            res["raw_pairs"] = [[r[0] if np.isfinite(r[0]) else None, r[1]]
                                for r in vals]
            everything.append(res)
            gc.collect()
        (OUT / "proto_long_take_v3.json").write_text(
            json.dumps(everything, ensure_ascii=False, indent=1), encoding="utf-8")
        del channels
        gc.collect()
    print(f"\n已写 {OUT / 'proto_long_take_v3.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
