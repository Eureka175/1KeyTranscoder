#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the delay trajectory of each channel vs CH1 from the existing window
data and test the drifting-clock model d(t) = a + b*t.

Uses only windows_<stem>.json (already measured on the RAW pcm): pairs 0-1, 0-2, 0-3
carry exactly the anchor-relative trajectories. Robust line fit (Theil-Sen) plus
residual statistics decide whether the channel is:
  * constant  (|b| small, residual <= 1-2 samples)  -> integer shift
  * drifting  (linear, residual small)              -> resample
  * other     (non-linear)                          -> report
"""
import json
from pathlib import Path

import numpy as np

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000


def theil_sen(t: np.ndarray, y: np.ndarray, n_pairs: int = 20000,
             seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(t)
    if n * (n - 1) // 2 <= n_pairs:
        idx = np.array([(i, j) for i in range(n) for j in range(i + 1, n)])
    else:
        idx = np.column_stack([rng.integers(0, n, n_pairs),
                               rng.integers(0, n, n_pairs)])
    dt = t[idx[:, 1]] - t[idx[:, 0]]
    ok = np.abs(dt) > 1.0
    slopes = (y[idx[ok, 1]] - y[idx[ok, 0]]) / dt[ok]
    b = float(np.median(slopes))
    a = float(np.median(y - b * t))
    return a, b


for stem in ("20260919_C1305", "20260919_C1306"):
    raw = json.loads((OUT / f"windows_{stem}.json").read_text(encoding="utf-8"))
    per = {}
    for r in raw:
        per.setdefault(f"{r['pair'][0]}-{r['pair'][1]}", []).append(r)
    print(f"\n=== {stem}: delay trajectory vs CH1 (pair 0-i), linear drift test")
    for key in ("0-1", "0-2", "0-3"):
        rows = sorted(per[key], key=lambda r: r["window"])
        t = np.array([r["start_s"] * SR for r in rows if r["delay"] is not None])
        y = np.array([r["delay"] for r in rows if r["delay"] is not None])
        q = np.array([r["quality"] for r in rows if r["delay"] is not None])
        if len(t) < 10:
            continue
        a, b = theil_sen(t, y)
        res = y - (a + b * t)
        # 只保留离拟合线 200 采样内的点再看残差 (抗相关歧义)
        inl = np.abs(res) < 200
        a2, b2 = theil_sen(t[inl], y[inl])
        res2 = y - (a2 + b2 * t)
        inl2 = np.abs(res2) < 100
        print(f"  {key}: n={len(t)}")
        print(f"    raw fit      intercept={a:+.1f} slope={b * 1e6:.2f} ppm "
              f"resid_max={np.abs(res).max():.0f} inliers(|r|<200)={inl.sum()}")
        print(f"    inlier fit   intercept={a2:+.1f} slope={b2 * 1e6:.2f} ppm "
              f"resid p50={np.median(np.abs(res2)):.1f} p90={np.percentile(np.abs(res2), 90):.1f} "
              f"inliers(|r|<100)={inl2.sum()}/{len(t)}")
        print(f"    drift over take: {b2 * t[-1]:+.0f} samples "
              f"({b2 * t[-1] / SR * 1000:+.1f} ms)  total {t[-1] / SR / 60:.1f} min")
