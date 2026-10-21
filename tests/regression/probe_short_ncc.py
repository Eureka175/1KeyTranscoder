#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Are CH1/CH2 of the failing short clips correlated with anything?

The geometry sweep showed zero usable frames for them under five different
geometries, which points at the material rather than the parameters. This checks
it directly with normalised cross-correlation over 2 s segments: a correlated pair
shows a clear peak (0.3-0.7 on this corpus), unrelated material stays near 0.02.

Reported per pair: the best |NCC| peak and the lag at that peak, plus how stable
the lag is across segments (a consistent lag means real shared content even if
the grouping ratio is low).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

CACHE = Path(r"E:\1kt_scratch\pcm")
SR = 48000
SEG = int(SR * 2.0)
MAX_LAG = int(SR * 0.08)
CLIPS = ["20260904_C1180", "20260904_C1202", "20260903_C1162",
         "20260904_C1197", "20260904_C1211"]


def ncc(a: np.ndarray, b: np.ndarray) -> tuple[float, int]:
    a = a - a.mean()
    b = b - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0, 0
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    cc = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    cc = np.concatenate([cc[-MAX_LAG:], cc[:MAX_LAG + 1]]) / (na * nb)
    i = int(np.argmax(np.abs(cc)))
    return float(abs(cc[i])), int(i - MAX_LAG)


def main() -> int:
    for clip in CLIPS:
        d = CACHE / clip
        if not d.is_dir():
            print(f"{clip}: 无缓存")
            continue
        paths = sorted(d.glob("ch*.f32"))
        arrs = [np.memmap(p, dtype=np.float32, mode="r") for p in paths]
        n = min(len(a) for a in arrs)
        print(f"\n=== {clip}  {len(arrs)} 通道  {n / SR:.2f}s")
        starts = [int(n * f) for f in (0.06, 0.3, 0.55, 0.8)]
        for i in range(len(arrs)):
            for j in range(i + 1, len(arrs)):
                peaks, lags = [], []
                for s in starts:
                    if s + SEG > n:
                        continue
                    p, l = ncc(np.asarray(arrs[i][s:s + SEG]),
                               np.asarray(arrs[j][s:s + SEG]))
                    peaks.append(p)
                    lags.append(l)
                if not peaks:
                    continue
                spread = max(lags) - min(lags)
                flag = ("相关" if np.median(peaks) >= 0.3 else
                        ("弱相关" if np.median(peaks) >= 0.1 else "不相关"))
                print(f"  CH{i + 1}-CH{j + 1}: NCC 中位 {np.median(peaks):.3f} "
                      f"最大 {max(peaks):.3f}  lag中位 {int(np.median(lags)):>6}  "
                      f"lag离散 {spread:>4}  -> {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
