#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parity checks between CPU reference and GPU alignment kernels."""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from .types import SurfaceBatch, WindowPeak

__all__ = ["compare_window_peaks", "compare_surfaces", "ParityResult"]


def _as_peak(x: Any) -> WindowPeak | None:
    if x is None:
        return None
    if isinstance(x, WindowPeak):
        return x
    if isinstance(x, (tuple, list)) and len(x) >= 3:
        return WindowPeak(int(x[0]), float(x[1]), float(x[2]))
    return None


class ParityResult(dict):
    """dict with a convenient ``ok`` property."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def compare_window_peaks(cpu: Sequence[Any], gpu: Sequence[Any], *,
                         cc_tol: float = 1e-9,
                         lag_exact: bool = True) -> ParityResult:
    if len(cpu) != len(gpu):
        return ParityResult(ok=False, reason="length mismatch",
                            cpu=len(cpu), gpu=len(gpu))
    n = len(cpu)
    lag_bad = 0
    none_bad = 0
    max_cc_diff = 0.0
    for a, b in zip(cpu, gpu):
        pa, pb = _as_peak(a), _as_peak(b)
        if (pa is None) != (pb is None):
            none_bad += 1
            continue
        if pa is None:
            continue
        if lag_exact and int(pa.lag) != int(pb.lag):
            lag_bad += 1
        max_cc_diff = max(max_cc_diff,
                          abs(pa.signed_cc - pb.signed_cc),
                          abs(pa.abs_cc - pb.abs_cc))
    ok = none_bad == 0 and (lag_bad == 0 if lag_exact else True) \
        and max_cc_diff <= cc_tol
    return ParityResult(
        ok=ok, n=n, lag_bad=lag_bad, none_bad=none_bad,
        max_cc_diff=max_cc_diff, cc_tol=cc_tol,
    )


def compare_surfaces(cpu: SurfaceBatch, gpu: SurfaceBatch, *,
                     abs_tol: float = 1e-10) -> ParityResult:
    a = np.asarray(cpu.windows, dtype=np.float64)
    b = np.asarray(gpu.windows, dtype=np.float64)
    if a.shape != b.shape:
        return ParityResult(ok=False, reason="shape mismatch",
                            cpu=list(a.shape), gpu=list(b.shape))
    d = np.abs(a - b)
    valid_bad = int(np.count_nonzero(cpu.valid != gpu.valid))
    return ParityResult(
        ok=bool(d.max() <= abs_tol and valid_bad == 0),
        n=int(a.shape[0]), shape=list(a.shape), max_abs_diff=float(d.max()),
        valid_bad=valid_bad, abs_tol=abs_tol,
    )
