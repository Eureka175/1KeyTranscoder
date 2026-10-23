#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance measurement: 5-minute block residual statistics for round 1 (tv) and
round 2 (tv2) alignment. Uses blocks with |CC| >= 0.25 only, so the numbers speak
about content where alignment is actually observable.
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
BLOCK_S = 300.0
HOP_S = 150.0
MAX_LAG = 2400
MIN_CC = 0.25


def peak(a, b):
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    a -= a.mean()
    b -= b.mean()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
    cand = np.concatenate([surf[:MAX_LAG + 1], surf[-MAX_LAG:]])
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    idx = int(np.argmax(np.abs(cand)))
    m = idx if idx <= MAX_LAG else idx - (2 * MAX_LAG + 1)
    return -m, float(abs(cand[idx]) / den)


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    a = np.asarray(vals)
    return {"n": int(a.size), "median": float(np.median(a)),
            "p90": float(np.percentile(a, 90)), "max": float(np.max(a)),
            "le1": int((a <= 1).sum()), "le2": int((a <= 2).sum()),
            "le5": int((a <= 5).sum())}


def measure(mode: str, stem: str) -> dict:
    if mode == "tv":
        root = OUT / "aligned_tv" / stem
        names = {i: root / f"ch{i}_tv.f32" for i in range(4)}
    else:
        root = OUT / "aligned_tv2" / stem
        names = {i: root / f"ch{i}_tv2.f32" for i in range(4)}
    ch = {i: np.memmap(p, dtype=np.float32, mode="r") for i, p in names.items()}
    n = min(len(x) for x in ch.values())
    block = int(BLOCK_S * SR)
    hop = int(HOP_S * SR)
    out = {}
    for (i, j) in ((0, 2), (0, 3), (2, 3)):
        abslags, ccs = [], []
        t = 0
        while t + block <= n:
            lag, cc = peak(ch[i][t:t + block], ch[j][t:t + block])
            if cc >= MIN_CC:
                abslags.append(abs(lag))
                ccs.append(cc)
            t += hop
        out[f"{i}-{j}"] = {"stats": stats(abslags),
                           "mean_cc": round(float(np.mean(ccs)), 3) if ccs else None}
    del ch
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    report = {"stem": args.stem}
    for mode in ("tv", "tv2"):
        report[mode] = measure(mode, args.stem)
        print(f"\n=== {args.stem} [{mode}] 5-min block residuals (|CC|>=0.25)")
        for pair, v in report[mode].items():
            s = v["stats"]
            print(f"  {pair}: n={s.get('n')} median={s.get('median')} "
                  f"p90={s.get('p90')} max={s.get('max')} "
                  f"<=1:{s.get('le1')} <=2:{s.get('le2')} <=5:{s.get('le5')} "
                  f"meanCC={v['mean_cc']}")
    (OUT / f"verify_blocks_{args.stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
