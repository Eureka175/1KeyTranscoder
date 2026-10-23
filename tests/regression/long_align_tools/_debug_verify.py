#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug: measure raw cross-correlation peaks on the aligned PCM to see whether the
verify-stage residual scatter is real misalignment or estimator ambiguity."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

ROOT = Path(r"E:\1kt_work\long_align\aligned")


def cc_surface(a: np.ndarray, b: np.ndarray, max_lag: int = 2400) -> list[tuple]:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    A = np.fft.rfft(a, nfft)
    B = np.fft.rfft(b, nfft)
    surf = np.fft.irfft(A * np.conj(B), nfft)
    cand = np.concatenate([surf[:max_lag + 1], surf[-max_lag:]])
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    order = np.argsort(-np.abs(cand))[:6]
    out = []
    for idx in order:
        m = int(idx) if idx <= max_lag else int(idx) - (2 * max_lag + 1)
        out.append((m, round(float(abs(cand[idx]) / den), 4)))
    return out


for stem in ("20260919_C1305", "20260919_C1306"):
    print(f"\n=== {stem} aligned")
    ch = [np.memmap(ROOT / stem / f"ch{i}.f32", dtype=np.float32, mode="r")
          for i in range(4)]
    n = len(ch[0])
    for s0 in (0, n // 3, 2 * n // 3):
        print(f"  window @ {s0 / la.SR:.0f}s")
        for (i, j) in [(0, 1), (0, 2), (0, 3), (2, 3)]:
            peaks = cc_surface(np.asarray(ch[i][s0:s0 + la.WIN]),
                               np.asarray(ch[j][s0:s0 + la.WIN]))
            print(f"    {i}-{j}: " + "  ".join(f"{m:+d}:{v:.3f}" for m, v in peaks[:4]))
