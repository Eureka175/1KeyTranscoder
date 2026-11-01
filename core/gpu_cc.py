#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional CUDA/cuFFT implementation of the window coarse-CC peak.

Only the **window coarse correlation** of `measure_window_delays` is moved to
the GPU: the 60 s / 12 kHz window FFT pair (rfft*conj(rfft) -> irfft) is 36x
faster in GPU float64 than CPU (measured 2.0 ms vs 74 ms per 1.44 M-point
window on an RTX 5070 Laptop), while the integer argmax / confidence math is
unchanged.  The caller passes the same float32 coarse caches once
(`prepare`), so no per-window host->device transfer is needed.

Parity: cuFFT float64 differs from CPU pocketfft at the ~1e-15 level.  The
production window records round `coarse_cc` / `confidence` to 6 decimals and
use integer delays, so the resulting curves are expected (and tested) to be
bit-identical; the caller must verify this on the acceptance material before
enabling the option.  This module is optional: without CuPy / CUDA it reports
`available() == False` and the caller keeps the CPU path.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any

import numpy as np
from scipy import fft as _fft

_CUPY: Any = None
_GPU_TRIED = False


def _import_cupy() -> Any:
    global _CUPY, _GPU_TRIED
    if _GPU_TRIED:
        return _CUPY
    _GPU_TRIED = True
    # This dev machine has a non-ASCII user path which breaks NVRTC's
    # source/include path handling; an ASCII junction to site-packages fixes it
    # without touching the CUDA install.  Import through it FIRST so cupy's
    # JIT include paths are ASCII too.
    try:
        junc = r"C:\sp_ascii"
        if os.path.isdir(junc) and junc not in sys.path:
            sys.path.insert(0, junc)
    except Exception:                                  # noqa: BLE001
        pass
    try:
        import cupy as cp  # noqa: F401
        _CUPY = cp
        return cp
    except Exception:                                  # noqa: BLE001
        _CUPY = None
        return None


def available() -> bool:
    cp = _import_cupy()
    if cp is None:
        return False
    try:
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:                                  # noqa: BLE001
        return False


class _Prepared:
    __slots__ = ("a", "b")

    def __init__(self, a: Any, b: Any) -> None:
        self.a = a
        self.b = b


def prepare(a12: np.ndarray, b12: np.ndarray) -> _Prepared | None:
    """Upload the two full coarse caches as float32 (freed by release())."""
    cp = _import_cupy()
    if cp is None:
        return None
    try:
        with cp.cuda.Device(0):
            return _Prepared(cp.asarray(a12, dtype=cp.float32),
                             cp.asarray(b12, dtype=cp.float32))
    except Exception:                                  # noqa: BLE001
        return None


def residual_coarse(a12: np.ndarray, b12: np.ndarray, *,
                    dec: int, search48: int) -> float | None:
    """GPU equivalent of the residual window's FFT coarse peak.

    Mirrors `long_take.measure_residual_curve.measure_one`: nfft =
    2**ceil(log2(2*n12)), rfft*conj -> irfft, |cand|/den on +-search lags,
    returns -m*dec.  The caller still runs the CPU float64 integer refinement
    and CC threshold, so only the coarse seed can be affected by cuFFT
    rounding (validated bit-identical on the acceptance material).
    """
    cp = _CUPY
    if cp is None:
        return None
    try:
        n12 = int(min(a12.shape[0], b12.shape[0]))
        if n12 < 16:
            return None
        nfft = int(1 << int(np.ceil(np.log2(2 * n12))))
        with cp.cuda.Device(0):
            a = cp.asarray(a12[:n12], dtype=cp.float64)
            b = cp.asarray(b12[:n12], dtype=cp.float64)
            A = cp.fft.rfft(a - a.mean(), nfft)
            B = cp.fft.rfft(b - b.mean(), nfft)
            surf = cp.fft.irfft(A * cp.conj(B), nfft)
            den = (float(cp.linalg.norm(a).item())
                   * float(cp.linalg.norm(b).item()) + 1e-12)
            radius12 = max(4, search48 // dec)
            search = radius12 + 2
            head = surf[:search + 1]
            tail = surf[nfft - search:nfft]
            cand = cp.abs(cp.concatenate([head, tail]) / den)
            m = np.concatenate([np.arange(0, search + 1),
                                np.arange(-search, 0)]).astype(np.int64)
            i = int(cp.argmax(cand).item())
            return float(-m[i]) * dec
    except Exception:                                  # noqa: BLE001
        return None


def release(prep: _Prepared | None) -> None:
    cp = _CUPY
    if prep is None or cp is None:
        return
    try:
        cp.get_default_memory_pool().free_all_blocks()
    except Exception:                                  # noqa: BLE001
        pass


def coarse_cc_peak(prep: _Prepared, start_c: int, count_c: int,
                   lag_lo: int, lag_hi: int, q: int) -> tuple | None:
    """GPU equivalent of `sync_estimate._coarse_cc_peak` (float64 math)."""
    cp = _CUPY
    if cp is None or prep is None:
        return None
    try:
        n = int(min(prep.a.shape[0], prep.b.shape[0]) - start_c)
        if count_c < n:
            n = int(count_c)
        if n < 16:
            return None
        with cp.cuda.Device(0):
            a = prep.a[start_c:start_c + n].astype(cp.float64)
            b = prep.b[start_c:start_c + n].astype(cp.float64)
            a = a - a.mean()
            b = b - b.mean()
            na = float(cp.linalg.norm(a).item())
            nb = float(cp.linalg.norm(b).item())
            if na < 1e-12 or nb < 1e-12:
                return None
            nfft = int(_fft.next_fast_len(2 * n - 1, real=True))
            prod = cp.fft.rfft(a, nfft) * cp.conj(cp.fft.rfft(b, nfft))
            surf = cp.fft.irfft(prod, nfft)
            m_lo = max(-(n - 1), int(math.ceil(-lag_hi / q)))
            m_hi = min(n - 1, int(math.floor(-lag_lo / q)))
            if m_lo > m_hi:
                return None
            idx = np.arange(m_lo, m_hi + 1, dtype=np.int64)
            # numpy negative indices wrap; cupy does not -> normalise
            idx_mod = np.where(idx < 0, idx + nfft, idx)
            vals = surf[cp.asarray(idx_mod)]
            k = int(cp.argmax(cp.abs(vals)).item())
            val = float(vals[k].item())
            signed = float(val / (na * nb))
            return float(-int(idx[k]) * q), signed, abs(signed)
    except Exception:                                  # noqa: BLE001
        return None
