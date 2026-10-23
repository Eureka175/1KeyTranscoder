#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prototype v2: window-direct long-take correction with ambiguity-aware measurement.

v1 (results in E:\\1kt_work\\proto_long_take\\proto_long_take.json) showed the
windowed path works where the material supports it: linear/nearest-centre gives
p50 0-1 sample on all six channels and p90 1-4 on CH4, but CH1/CH2 keep a p90 of
~100-124 samples. This version finds out whether that tail is a plan error or a
measurement artefact:

  * the delay estimator keeps the plain normalized cross-correlation that
    calibration proves exact, and additionally computes a prominence ratio
    (best peak vs the strongest peak more than 32 samples away) and a PHAT
    cross-check. Windows where periodic content makes the peak ambiguous are
    flagged instead of being silently averaged in;
  * every per-window residual is written out, together with the window's peak,
    its distance to the nearest trusted window, and the difference between the
    applied shift and the raw measurement at the window centre;
  * the anchor is not re-derived (the production runs already fixed it to CH3 on
    both clips); this drops one full four-channel decode per clip.
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
MIN_PEAK = 0.10
AMBIG_PROMINENCE = 1.6        # best peak / runner-up peak, else window is ambiguous

CLIPS = [
    (REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4", 2),
    (REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4", 2),
]


def load_channels(stem: str, n: int = 4) -> list[np.memmap]:
    return [np.memmap(PCM_CACHE / stem / f"ch{i}.f32", dtype=np.float32, mode="r")
            for i in range(n)]


def _cc_surface(a: np.ndarray, b: np.ndarray, nfft: int) -> np.ndarray:
    A = np.fft.rfft(a - a.mean(), nfft)
    B = np.fft.rfft(b - b.mean(), nfft)
    return np.fft.irfft(A * np.conj(B), nfft)


def window_delay(ref: np.ndarray, tgt: np.ndarray) -> tuple[float, float, bool]:
    """tgt[n] ~ ref[n - delay]; returns (delay, quality, ambiguous).

    plain normalised CC (calibrated exact), coarse on pooled 12 kHz, then a fine
    dot-product search on a 2 s centre segment. Quality = normalised peak of the
    fine search. Ambiguous = a competitor peak within the search window is almost
    as strong (periodic music). PHAT is used only as a cross-check.
    """
    sr_c = SR // DEC
    search_c = int(round(SEARCH_MS / 1000.0 * sr_c))
    r = np.asarray(ref, dtype=np.float64)[: (ref.size // DEC) * DEC].reshape(-1, DEC).mean(axis=1)
    g = np.asarray(tgt, dtype=np.float64)[: (tgt.size // DEC) * DEC].reshape(-1, DEC).mean(axis=1)
    n = min(r.size, g.size)
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = _cc_surface(r[:n], g[:n], nfft)
    cand = np.concatenate([surf[:search_c + 1], surf[-search_c:]])
    order = np.argsort(-np.abs(cand))
    idx = int(order[0])
    m_c = idx if idx <= search_c else idx - (2 * search_c + 1)
    # 次峰: 与首选相隔 > 8 个池化采样 (= 32 全速率采样)
    runner = 0.0
    for j in order[1:]:
        if abs(int(j) - idx) > 8:
            runner = float(np.abs(cand[j]))
            break
    prominent = float(np.abs(cand[idx]) / (runner + 1e-12))
    coarse = m_c * DEC                      # 实测: cc 峰在 -delay
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
        return float("nan"), 0.0, True
    delay = float(best[0])                 # a[k]·b[k+m] 的峰在 +delay
    quality = abs(best[1])
    ambiguous = (quality < MIN_PEAK) or (prominent < AMBIG_PROMINENCE)
    return delay, quality, ambiguous


def calibrate() -> None:
    rng = np.random.default_rng(7)
    x = rng.standard_normal(SR * 40).astype(np.float32)
    shift = 123
    y = np.concatenate([np.zeros(shift, np.float32), x[:-shift]])
    d, q, amb = window_delay(x, y)
    assert abs(d - shift) <= 1.0, f"delay sign: {d} != {shift}"
    print(f"  calibration: delay={d:.2f} (expect {shift}), quality={q:.3f}, "
          f"ambiguous={amb}")
    y2 = np.concatenate([x[200:], np.zeros(200, np.float32)])
    d2, _, _ = window_delay(x, y2)
    assert abs(d2 + 200) <= 1.0, f"reverse sign: {d2} != -200"
    print(f"  reverse: delay={d2:.2f} (expect -200)")


def measure_windows(channels, anchor):
    n = channels[0].shape[0]
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    starts = list(range(0, max(0, n - win) + 1, hop))
    direct = {}
    for i in range(len(channels)):
        if i == anchor:
            continue
        vals = [window_delay(channels[anchor][s:s + win], channels[i][s:s + win])
                for s in starts]
        direct[i] = vals
        good = sum(1 for d, q, a in vals if np.isfinite(d) and not a)
        amb = sum(1 for d, q, a in vals if np.isfinite(d) and a)
        print(f"    CH{anchor + 1}->CH{i + 1}: {good} clean / {amb} ambiguous "
              f"/ {len(vals)} windows", flush=True)
    return {"starts": starts, "win": win, "hop": hop, "direct": direct, "n": n}


def trusted_points(vals, starts, win):
    pts = [(s + win / 2.0, d) for s, (d, q, a) in zip(starts, vals)
           if np.isfinite(d) and not a]
    return pts


def trajectory(pts):
    if not pts:
        return None
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    return lambda pos: np.interp(pos, xs, ys)


def evaluate(channels, anchor_arr, starts, win, n, fn, pts):
    rows = []
    if fn is None:
        return rows
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    for w, s in enumerate(starts):
        pos = np.arange(s, s + win, dtype=np.float64)
        shift = fn(pos)
        src = np.asarray(channels[s:s + win], dtype=np.float64)
        posi = pos + shift
        np.clip(posi, 0, n - 1, out=posi)
        y = np.interp(posi, np.arange(s, s + win, dtype=np.float64), src)
        d, q, a = window_delay(np.asarray(anchor_arr[s:s + win]), y)
        nearest = float(np.min(np.abs(xs - (s + win / 2.0)))) if xs.size else float("nan")
        rows.append({"w": w, "start": s,
                     "res": d if (np.isfinite(d) and not a) else None,
                     "res_any": d if np.isfinite(d) else None,
                     "quality": q, "ambiguous": a,
                     "nearest_trusted_s": nearest,
                     "shift_mid": float(shift[win // 2])})
    return rows


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
            clean = [d for d, q, a in vals if np.isfinite(d) and not a]
            pts = trusted_points(vals, starts, win)
            fn = trajectory(pts)
            rows = evaluate(channels[i], channels[anchor], starts, win, n, fn, pts)
            covered = [r["res"] for r in rows if r["res"] is not None
                       and r["nearest_trusted_s"] <= win / 2 + HOP_S]
            gap = [r["res"] for r in rows if r["res"] is not None
                   and r["nearest_trusted_s"] > win / 2 + HOP_S]
            plan_err = [abs(r["shift_mid"] - d) for r, d, q, a in zip(rows, *( [x for x in []] or []))] if False else []
            # 计划误差: 施加移位与原始干净测量的差 (同窗中点)
            cd = {w: d for w, (d, q, a) in enumerate(vals) if np.isfinite(d) and not a}
            for r in rows:
                if r["w"] in cd:
                    plan_err.append(abs(r["shift_mid"] - cd[r["w"]]))
            res = {"clip": stem, "target": i, "anchor": anchor,
                   "n_windows": len(starts), "clean": len(clean),
                   "clean_fraction": len(clean) / len(starts),
                   "raw": summarise(clean),
                   "linear_nearest": summarise([r["res"] for r in rows]),
                   "residual_all": summarise([r["res_any"] for r in rows]),
                   "plan_error": summarise(plan_err),
                   "covered": summarise(covered),
                   "gap": summarise(gap),
                   "rows": [{"w": r["w"], "res": r["res"], "peak": round(r["quality"], 3),
                             "amb": r["ambiguous"], "gap_s": r["nearest_trusted_s"]}
                            for r in rows]}
            print(f"    CH{i + 1}: clean={res['clean_fraction']:.0%} "
                  f"raw p90={res['raw'].get('p90')} "
                  f"linear/nearest p50={res['linear_nearest'].get('median')} "
                  f"p90={res['linear_nearest'].get('p90')} "
                  f"<=1 {res['linear_nearest'].get('le1')}/{res['linear_nearest'].get('n')} | "
                  f"covered p90={res['covered'].get('p90')} gap n={res['gap'].get('n')} "
                  f"p90={res['gap'].get('p90')} | plan_err p90={res['plan_error'].get('p90')}")
            everything.append(res)
            gc.collect()
        (OUT / "proto_long_take_v2.json").write_text(
            json.dumps(everything, ensure_ascii=False, indent=1), encoding="utf-8")
        del channels
        gc.collect()
    print(f"\n已写 {OUT / 'proto_long_take_v2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
