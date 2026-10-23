#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final constant-shift solve using the band-consensus observations.

Per-band analysis (work/_band_analysis.py) showed which estimators carry the
cross-group evidence:
    full band / band0 (20-1000 Hz) / onset-envelope agree on the cross-group delays
    band1 (1-3.5 kHz) is systematically biased (+150 samples) on cross-group pairs
    band2 is usable only for the near-identical pair
The long-baseline chunk clusters (refine_*.json) confirm the same values with
80-minute integration.

This script writes solve_<stem>.json in the format long_align.stage_apply expects.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

OUT = Path(r"E:\1kt_work\long_align")

OBS = {
    "20260919_C1305": [
        # (i, j, d(i->j), weight)
        (0, 1, -4, 10.0),
        (0, 2, 1014, 8.0),
        (0, 3, 1222, 6.0),
        (1, 2, 1019, 5.0),
        (1, 3, 1227, 5.0),
        (2, 3, 210, 8.0),
    ],
    "20260919_C1306": [
        (0, 1, -4, 10.0),
        (0, 2, 1120, 8.0),
        (0, 3, 1256, 6.0),
        (1, 2, 1124, 5.0),
        (1, 3, 1256, 5.0),
        (2, 3, 136, 8.0),
    ],
}
ANCHOR = 0
MAX_SHIFT = 4800


def solve(stem: str, obs) -> dict:
    idx = [c for c in range(4) if c != ANCHOR]
    pos = {c: k for k, c in enumerate(idx)}
    rows, rhs, wts = [], [], []
    for i, j, d, w in obs:
        row = np.zeros(len(idx))
        if i != ANCHOR:
            row[pos[i]] -= 1.0
        if j != ANCHOR:
            row[pos[j]] += 1.0
        rows.append(row)
        rhs.append(float(d))
        wts.append(float(w))
    A = np.asarray(rows)
    b = np.asarray(rhs)
    w = np.asarray(wts)
    sol, *_ = np.linalg.lstsq(A * w[:, None], b * w, rcond=None)
    shifts = {ANCHOR: 0}
    for c in idx:
        shifts[c] = int(np.rint(sol[pos[c]]))
    resid = {}
    for i, j, d, w in obs:
        resid[f"{i}-{j}"] = {"measured": d, "predicted": shifts[j] - shifts[i],
                             "residual": int(shifts[j] - shifts[i] - d),
                             "weight": w}
    if any(abs(s) > MAX_SHIFT for s in shifts.values()):
        raise SystemExit(f"{stem}: shift exceeds {MAX_SHIFT}")
    cost = float(np.sum([r["weight"] * abs(r["residual"]) ** 2
                         for r in resid.values()]))
    return {"stem": stem, "anchor": ANCHOR,
            "shifts": {str(k): v for k, v in sorted(shifts.items())},
            "residuals": resid, "cost": cost,
            "max_abs_residual": max(abs(r["residual"]) for r in resid.values()),
            "warnings": ["cross-group pairs are polarity-inverted (|CC| 0.15-0.48, "
                         "signed CC negative); time alignment only, polarity kept as-is"]}


def main() -> int:
    for stem, obs in OBS.items():
        sol = solve(stem, obs)
        (OUT / f"solve_{stem}.json").write_text(
            json.dumps(sol, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{stem}: shifts={sol['shifts']} cost={sol['cost']:.1f} "
              f"max|resid|={sol['max_abs_residual']}")
        for k, r in sol["residuals"].items():
            print(f"   {k}: measured={r['measured']:>6d} predicted={r['predicted']:>6d} "
                  f"resid={r['residual']:>+4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
