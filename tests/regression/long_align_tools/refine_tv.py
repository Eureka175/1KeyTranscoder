#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Residual refinement round: measure 5-minute block residuals on the TV-aligned
audio, build a smooth correction curve, and apply s_total(t) = s_tv(t) + residual(t).

Why 5-minute blocks: a 5-min block integrates ~14M samples, so its peak lock is far
more reliable than a 60 s window (the 60 s estimator is the ambiguous one). The
correction curve is built from blocks with |CC| >= 0.25, linearly interpolated, and
clamped to +-4800 samples total shift.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000
BLOCK_S = 300.0
HOP_S = 150.0        # 块之间 50% 重叠, 用于插值平滑
MAX_LAG = 2400
MIN_CC = 0.25
MAX_SHIFT = 4800


def peak(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
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


def measure(stem: str, target: int, root: Path) -> list[dict]:
    a = np.memmap(root / stem / "ch0_tv.f32", dtype=np.float32, mode="r")
    b = np.memmap(root / stem / f"ch{target}_tv.f32", dtype=np.float32, mode="r")
    n = min(len(a), len(b))
    block = int(BLOCK_S * SR)
    hop = int(HOP_S * SR)
    rows = []
    t = 0
    while t + block <= n:
        lag, cc = peak(a[t:t + block], b[t:t + block])
        rows.append({"t_s": t / SR, "lag": int(lag), "cc": round(cc, 4)})
        t += hop
    del a, b
    return rows


def build_curve(rows: list[dict], n: int) -> dict:
    good = [r for r in rows if r["cc"] >= MIN_CC]
    if len(good) < 3:
        return {"ok": False, "reason": "too_few_blocks", "n": len(good),
                "rows": rows}
    t = np.array([r["t_s"] * SR for r in good], dtype=np.float64)
    y = np.array([r["lag"] for r in good], dtype=np.float64)
    w = np.array([r["cc"] for r in good], dtype=np.float64)
    # 加权中位数平滑: 相邻 3 块的局部位移
    ys = y.copy()
    for k in range(len(y)):
        lo, hi = max(0, k - 1), min(len(y), k + 2)
        seg = list(zip(y[lo:hi], w[lo:hi]))
        seg.sort()
        tot = sum(x[1] for x in seg)
        acc = 0.0
        for v, ww in seg:
            acc += ww
            if acc >= tot / 2:
                ys[k] = v
                break
    grid = np.arange(0, n, 48000, dtype=np.float64)
    s = np.interp(grid, t, ys)
    if np.abs(s).max() > MAX_SHIFT:
        return {"ok": False, "reason": "out_of_range",
                "max": float(np.abs(s).max())}
    return {"ok": True, "grid_t": grid.tolist(), "grid_s": s.tolist(),
            "n_blocks": len(good), "max_residual": float(np.abs(ys).max()),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    stem = args.stem
    root = OUT / "aligned_tv"
    n = (root / stem / "ch0_tv.f32").stat().st_size // 4
    curves = {}
    for target in (2, 3):
        print(f"{stem} CH{target + 1}: measuring block residuals ...", flush=True)
        rows = measure(stem, target, root)
        curve = build_curve(rows, n)
        curves[str(target)] = curve
        if curve.get("ok"):
            print(f"  blocks={len(rows)} used={curve['n_blocks']} "
                  f"max|residual|={curve['max_residual']:.1f}")
        else:
            print(f"  curve failed: {curve.get('reason')}")
    (OUT / f"refine_curve_{stem}.json").write_text(
        json.dumps(curves, ensure_ascii=False), encoding="utf-8")
    print(f"已写 {OUT / f'refine_curve_{stem}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
