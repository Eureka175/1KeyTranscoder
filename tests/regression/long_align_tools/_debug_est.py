#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug: why does the verify estimator report 9-sample median residual on an
already aligned pair while a brute-force full-band CC finds the peak at 0?"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

ROOT = Path(r"E:\1kt_work\long_align\aligned")
stem = "20260919_C1305"
ch = [np.memmap(ROOT / stem / f"ch{i}.f32", dtype=np.float32, mode="r")
      for i in range(4)]
n = len(ch[0])
for s0 in (0, n // 3, 2 * n // 3):
    a = np.asarray(ch[0][s0:s0 + la.WIN])
    b = np.asarray(ch[2][s0:s0 + la.WIN])
    est = la.window_estimates(a, b)
    print(f"window {s0 / la.SR:.0f}s: window_estimates d={est['delay']} "
          f"methods={est['methods']} cands={est['cands']}")
    # brute force at 48k full band
    def bf(a, b, maxlag=2400):
        aa = a.astype(np.float64) - a.mean()
        bb = b.astype(np.float64) - b.mean()
        n = min(len(aa), len(bb))
        aa, bb = aa[:n], bb[:n]
        nfft = int(1 << int(np.ceil(np.log2(2 * n))))
        s = np.fft.irfft(np.fft.rfft(aa, nfft) * np.conj(np.fft.rfft(bb, nfft)), nfft)
        cand = np.concatenate([s[:maxlag + 1], s[-maxlag:]])
        den = np.linalg.norm(aa) * np.linalg.norm(bb) + 1e-12
        idx = int(np.argmax(np.abs(cand)))
        m = idx if idx <= maxlag else idx - (2 * maxlag + 1)
        return m, float(abs(cand[idx]) / den)
    print("   brute force 48k:", bf(a, b))
    # 12k full-band
    a12 = la.decimate(a, 4)
    b12 = la.decimate(b, 4)
    n12 = min(len(a12), len(b12))
    nfft = int(1 << int(np.ceil(np.log2(2 * n12))))
    A = np.fft.rfft(a12[:n12] - a12[:n12].mean(), nfft)
    B = np.fft.rfft(b12[:n12] - b12[:n12].mean(), nfft)
    surf = np.fft.irfft(A * np.conj(B), nfft)
    m, prom = la.signed_peak(surf, la.SEARCH12)
    print(f"   12k full: m={m} delay_coarse={-m * la.DEC} prom={prom:.2f}")
