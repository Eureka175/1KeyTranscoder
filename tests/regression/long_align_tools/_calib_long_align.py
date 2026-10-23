#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calibration: inject known constant delays into synthetic audio, recover exactly."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

rng = np.random.default_rng(1)
a = rng.standard_normal(la.SR * 60).astype(np.float32)
for shift in (977, -1234, 1, -1, 4800, -4800):
    if shift > 0:
        b = np.concatenate([np.zeros(shift, np.float32), a[:-shift]])
    elif shift < 0:
        k = -shift
        b = np.concatenate([a[k:], np.zeros(k, np.float32)])
    else:
        b = a.copy()
    est = la.window_estimates(a, b)
    print("inject {:+d} -> {} methods={} q={:.1f}".format(
        shift, est["delay"], est["methods"], est["quality"]))
