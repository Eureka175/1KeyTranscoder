#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trajectory scan on the RAW PCM: every 5 minutes, brute-force full-band
correlation at 12 kHz (+-200 ms) for pairs CH1-CH2, CH1-CH3, CH1-CH4, CH3-CH4.
Shows whether the delay is constant, linear-drifting, or wandering (content-driven).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402


def peaks(a12, b12, search=2400, top=3):
    n = min(len(a12), len(b12))
    a = a12[:n].astype(np.float64)
    b = b12[:n].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
    cand = np.concatenate([surf[:search + 1], surf[-search:]])
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    order = np.argsort(-np.abs(cand))[:top]
    out = []
    for idx in order:
        m = int(idx) if idx <= search else int(idx) - (2 * search + 1)
        out.append((m, round(float(abs(cand[idx]) / den), 3)))
    return out


for stem in ("20260919_C1305", "20260919_C1306"):
    print(f"\n=== {stem} raw trajectory (12k full band, delay vs CH1)")
    dec = [la.decim_full(stem, i) for i in range(4)]
    n = len(dec[0])
    win = la.WIN_S   # seconds
    hop = 300.0      # 5 min
    t = 0.0
    dur_s = n / (la.SR / la.DEC)
    while t + win <= dur_s:
        s = int(t * la.SR / la.DEC)
        w = int(win * la.SR / la.DEC)
        row = []
        for (i, j) in ((0, 1), (0, 2), (0, 3), (2, 3)):
            p = peaks(dec[i][s:s + w], dec[j][s:s + w])
            # 12k lag -> 48k samples
            row.append(f"{i}-{j}: {p[0][0] * la.DEC:+6d}({p[0][1]:.2f})")
        print(f"  t={t / 60:5.1f}min  " + "  ".join(row))
        t += hop
