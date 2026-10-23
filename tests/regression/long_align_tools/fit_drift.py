#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fit a robust linear drift model per channel from the tracker output.

Model: lag(t) = a + b*t  (t in samples at 48 kHz). A channel with a linear trend is
an independently-clocked source; b is its rate offset (ppm). RANSAC rejects the
content-ambiguity jumps that the continuity tracker still picked up, and the
residual distribution says whether the linear model is enough.

Writes drift_<stem>.json: per target {a, b, ppm, residual stats, coverage}.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000


def ransac_line(t: np.ndarray, y: np.ndarray, w: np.ndarray,
                thresh: float = 3.0, iters: int = 4000,
                seed: int = 0) -> tuple[float, float, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(t)
    best = (0, 0.0, np.zeros(n, dtype=bool))
    for _ in range(iters):
        i, j = rng.integers(0, n, 2)
        if abs(t[i] - t[j]) < 1.0:
            continue
        b = (y[i] - y[j]) / (t[i] - t[j])
        a = y[i] - b * t[i]
        r = np.abs(y - (a + b * t))
        inl = r <= thresh
        score = float(w[inl].sum())
        ninl = int(inl.sum())
        if ninl >= 3 and score > best[0]:
            best = (score, a, b, inl)
    if best[0] == 0:
        return float(np.median(y)), 0.0, np.ones(n, dtype=bool)
    _, a, b, inl = best
    # 用内点做最小二乘, 再算残差
    W = w[inl][:, None]
    A = np.column_stack([np.ones(inl.sum()), t[inl]])
    sol, *_ = np.linalg.lstsq(A * W, y[inl] * w[inl], rcond=None)
    a, b = float(sol[0]), float(sol[1])
    return a, b, inl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    data = json.loads((OUT / f"track_{args.stem}.json").read_text(encoding="utf-8"))
    result = {"stem": args.stem, "channels": {}}
    for key, rec in data.items():
        target = rec["target"]
        rows = [r for r in rec["trajectory"] if r["accepted"] and r["cc"] >= 0.12]
        if len(rows) < 10:
            print(f"CH{target + 1}: too few good points ({len(rows)})")
            result["channels"][str(target)] = {"ok": False,
                                               "reason": "too_few_points",
                                               "n": len(rows)}
            continue
        t = np.array([r["t_min"] * 60.0 * SR for r in rows], dtype=np.float64)
        y = np.array([r["lag"] for r in rows], dtype=np.float64)
        w = np.array([r["cc"] for r in rows], dtype=np.float64)
        a, b, inl = ransac_line(t, y, w)
        res = y - (a + b * t)
        res_in = res[inl]
        dur_s = rec["n_win"] * rec["hop_s"] if rec["n_win"] else 0.0
        total = b * dur_s * SR
        info = {
            "ok": True,
            "n_points": int(len(rows)),
            "n_inliers": int(inl.sum()),
            "a_samples": round(a, 2),
            "b_ppm": round(b * 1e6, 3),
            "drift_over_take_samples": round(total, 1),
            "drift_over_take_ms": round(total / SR * 1000.0, 3),
            "residual_p50": round(float(np.median(np.abs(res_in))), 2),
            "residual_p90": round(float(np.percentile(np.abs(res_in), 90)), 2),
            "residual_max": round(float(np.max(np.abs(res_in))), 2),
            "take_duration_s": round(dur_s, 1),
        }
        result["channels"][str(target)] = info
        print(f"CH{target + 1}: a={info['a_samples']:+.1f} "
              f"b={info['b_ppm']:+.3f} ppm -> drift {info['drift_over_take_ms']:+.2f} ms "
              f"| resid p50={info['residual_p50']} p90={info['residual_p90']} "
              f"max={info['residual_max']} | inliers {info['n_inliers']}/{info['n_points']}")
    (OUT / f"drift_{args.stem}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写 {OUT / f'drift_{args.stem}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
