#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score candidate solutions by |coherence| at the predicted lag (polarity-aware).

Polarity matters here: cross-group pairs correlate *negatively* at their best lag,
so the alignment penalty must use |CC| and the sign must be reported separately.
Candidates are proper shift vectors (anchor CH0 = 0); relative delay d(i->j) must
equal s_j - s_i.
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


def corr_at(a12: np.ndarray, b12: np.ndarray, lag: int) -> float:
    n = min(len(a12), len(b12))
    if lag >= 0:
        a = a12[: n - lag] if lag else a12[:n]
        b = b12[lag:n]
    else:
        a = a12[-lag:n]
        b = b12[: n + lag]
    if len(a) < 12000:
        return 0.0
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    a -= a.mean()
    b -= b.mean()
    den = np.linalg.norm(a) * np.linalg.norm(b)
    if den < 1e-9:
        return 0.0
    return float(np.dot(a, b) / den)


def score(stem: str, shifts: list[int], n_win: int = 24,
          search: int = 6) -> dict:
    dec = [la.decim_full(stem, i) for i in range(4)]
    n = min(len(x) for x in dec)
    win = int(la.WIN_S * la.SR / la.DEC)
    starts = np.linspace(0, max(0, n - win), n_win).astype(np.int64)
    total = 0.0
    per = {}
    for i in range(4):
        for j in range(i + 1, 4):
            lag12 = int(round((shifts[j] - shifts[i]) / la.DEC))
            abs_at, signed_at, best_abs, best_lag, signs = [], [], [], [], []
            for s in starts:
                a = dec[i][s:s + win]
                b = dec[j][s:s + win]
                c0 = corr_at(a, b, lag12)
                abs_at.append(abs(c0))
                signed_at.append(c0)
                bv = -1.0
                bl = lag12
                for L in range(lag12 - search, lag12 + search + 1):
                    c = corr_at(a, b, L)
                    if abs(c) > abs(bv):
                        bv, bl = c, L
                best_abs.append(abs(bv))
                best_lag.append(bl - lag12)
                signs.append(np.sign(bv))
            med_abs = float(np.median(abs_at))
            med_signed = float(np.median(signed_at))
            per[f"{i}-{j}"] = {
                "lag12": lag12,
                "abs_med_at_pred": round(med_abs, 4),
                "signed_med_at_pred": round(med_signed, 4),
                "abs_med_best_pm6": round(float(np.median(best_abs)), 4),
                "lag_offset_med": int(np.median(best_lag)),
                "sign_consistency": round(float(np.mean(np.asarray(signs) > 0)), 3),
            }
            total += med_abs
    return {"stem": stem, "shifts": shifts, "score": round(total, 4), "per_pair": per}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--solution", action="append", required=True)
    ap.add_argument("--n-win", type=int, default=24)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    results = []
    for spec in args.solution:
        name, rest = spec.split(":", 1)
        shifts = [int(x) for x in rest.split(",")]
        r = score(args.stem, shifts, args.n_win)
        r["name"] = name
        results.append(r)
        print(f"{name:>8s} shifts={shifts} score={r['score']:.4f}")
        for k, v in r["per_pair"].items():
            print(f"    {k}: lag12={v['lag12']:>5d} |cc|@pred={v['abs_med_at_pred']:.4f} "
                  f"signed={v['signed_med_at_pred']:+.3f} "
                  f"|cc|best={v['abs_med_best_pm6']:.4f} off={v['lag_offset_med']:+d} "
                  f"sign+={v['sign_consistency']:.2f}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
