#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continuity-constrained drift tracker for the long takes.

Facts established before this script:
  * CH1<->CH2 is constant (-4 samples, |CC| 0.98) -> they act as one reference.
  * CH3 and CH4 drift slowly relative to CH1/2 (C1305 CH4: ~500 samples over the
    first 40 min then stable; C1306 CH3: ~116 samples over 60 min). A constant shift
    cannot bind all four, so the delay must be tracked over time.
  * per-window raw correlation is ambiguous (content changes switch the dominant
    shared component), so the tracker must follow continuity: at each window search
    only near the predicted lag, and only accept windows whose peak is distinct.

Output: per-channel trajectory (t, lag, cc) + a smoothed shift curve sampled every
hop, written to E:\\1kt_work\\long_align\\track_<stem>.json.
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
WIN_S = 60.0
HOP_S = 10.0
SEARCH0 = 3000              # first-window search, 48k samples (62.5 ms)
RADIUS_BASE = 96            # continuity radius at 48k samples (2 ms)
RADIUS_PER_MIN = 8          # extra radius per minute (drift allowance)
MIN_CC = 0.06


def cc_peak_12k(a12: np.ndarray, b12: np.ndarray, center48: int,
                radius48: int) -> tuple[int, float]:
    """Best |CC| lag (48k samples) inside +-radius48 around center48."""
    n = min(len(a12), len(b12))
    a = a12[:n].astype(np.float64)
    b = b12[:n].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    lo12 = int((center48 - radius48) / la.DEC)
    hi12 = int((center48 + radius48) / la.DEC)
    search = max(abs(lo12), abs(hi12)) + 2
    cand = np.concatenate([surf[:search + 1], surf[-search:]])
    # 允许的 12k 索引区间 (映射到 signed)
    best = (None, 0.0)
    for idx in range(len(cand)):
        m = idx if idx <= search else idx - (2 * search + 1)
        lag48 = -m * la.DEC
        if lo12 <= -m <= hi12 or True:
            if not (center48 - radius48 <= lag48 <= center48 + radius48):
                continue
        cc = abs(cand[idx]) / den
        if best[0] is None or cc > best[1]:
            best = (lag48, cc)
    if best[0] is None:
        return int(center48), 0.0
    return int(best[0]), float(best[1])


def refine48(stem: str, i: int, j: int, s: int, coarse: int,
             radius: int = 8) -> tuple[int, float]:
    a = la.load_ch(stem, j)
    b = la.load_ch(stem, i)
    aa = np.asarray(a[s:s + la.WIN], dtype=np.float32)
    bb = np.asarray(b[s:s + la.WIN], dtype=np.float32)
    return la.refine_48k(aa, bb, coarse, radius=radius)


def track(stem: str, target: int, ref: int = 0) -> dict:
    dec = {}
    for i in (ref, target):
        dec[i] = la.decim_full(stem, i)
    n12 = min(len(dec[ref]), len(dec[target]))
    dur_s = n12 / (SR / la.DEC)
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    n_win = max(0, (int(dur_s * SR) - win) // hop + 1)
    # 初始 lag: 前 120 s 全搜索 (48k 粗 + 精化)
    i0 = int(120 * SR / la.DEC)
    a12 = dec[ref][:i0]
    b12 = dec[target][:i0]
    lag0, cc0 = cc_peak_12k(a12, b12, 0, SEARCH0)
    lag0, q0 = refine48(stem, target, ref, 0, lag0, radius=12)
    print(f"  {stem} CH{target + 1} vs CH1: initial lag {lag0:+d} "
          f"(cc {cc0:.2f}, refine q {q0:.3f})", flush=True)
    traj = []
    prev = lag0
    for w in range(n_win):
        s = w * hop
        t_min = s / SR / 60.0
        radius = int(RADIUS_BASE + RADIUS_PER_MIN * t_min)
        s12 = int(s / la.DEC)
        w12 = int(win / la.DEC)
        if s12 + w12 > n12:
            break
        lag, cc = cc_peak_12k(dec[ref][s12:s12 + w12],
                              dec[target][s12:s12 + w12], prev, radius)
        acc = cc >= MIN_CC
        if acc:
            lag, q = refine48(stem, target, ref, s, lag, radius=8)
            cc = min(cc, 1.0) * max(q, 0.0)
            prev = lag
        traj.append({"w": w, "t_min": round(t_min, 3), "lag": int(lag),
                     "cc": round(float(cc), 4), "accepted": bool(acc),
                     "pred": int(prev) if acc else None})
        if w % 50 == 0:
            print(f"    w={w} t={t_min:.1f}min lag={lag:+d} cc={cc:.3f} "
                  f"acc={acc}", flush=True)
    accepted = [x for x in traj if x["accepted"]]
    print(f"  {stem} CH{target + 1}: {len(accepted)}/{len(traj)} accepted")
    return {"stem": stem, "target": target, "ref": ref,
            "win_s": WIN_S, "hop_s": HOP_S, "n_win": n_win,
            "trajectory": traj}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--targets", default="1,2,3")
    args = ap.parse_args()
    out = {}
    for t in [int(x) for x in args.targets.split(",")]:
        key = f"{args.stem}|{t}"
        out[key] = track(args.stem, t)
    path = OUT / f"track_{args.stem}.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"已写 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
