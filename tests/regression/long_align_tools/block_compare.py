#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare constant-shift vs time-varying alignment with 5-min block correlation.

A 5-minute block gives ~14M samples of integration, so its correlation peak is far
more robust than a 60 s window (the 60 s estimator is the ambiguous one; it is only
used to *find* the shift, not to judge it). For each 5-min block we print the
full-band 48 kHz peak lag and |CC| for pairs 0-2, 0-3, 2-3:
    |lag| small  -> block aligned
    |CC|  high   -> the block actually contains common content (so the lag is meaningful)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000
BLOCK_S = 300.0
MAX_LAG = 2400


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
    return -m, float(abs(cand[idx]) / den)     # delay = -m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    for mode, d in (("constant", OUT / "aligned"), ("time-varying", OUT / "aligned_tv")):
        base = d / args.stem
        names = {i: base / (f"ch{i}.f32" if mode == "constant" else f"ch{i}_tv.f32")
                 for i in range(4)}
        if not all(p.is_file() for p in names.values()):
            print(f"{mode}: 缺文件, 跳过")
            continue
        ch = {i: np.memmap(p, dtype=np.float32, mode="r") for i, p in names.items()}
        n = min(len(x) for x in ch.values())
        print(f"\n=== {args.stem} [{mode}] 5-min block peaks")
        t = 0.0
        while t + BLOCK_S <= n / SR:
            s = int(t * SR)
            w = int(BLOCK_S * SR)
            parts = []
            for (i, j) in ((0, 2), (0, 3), (2, 3)):
                lag, cc = peak(ch[i][s:s + w], ch[j][s:s + w])
                parts.append(f"{i}-{j}: {lag:+5d}({cc:.2f})")
            print(f"  t={t / 60:5.1f}min  " + "  ".join(parts))
            t += BLOCK_S
        del ch
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
