#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 tests for the GPU alignment architecture (no CUDA required).

The GPU kernels themselves are exercised by the optional benchmark harness;
these tests cover the pure policy/parity/peak-selection contracts so a broken
CPU fallback or report rule is caught in `--level unit`.
"""
from __future__ import annotations

import numpy as np

from ..paths import record
from ..paths import section

__all__ = ["l1_gpu_align"]


def l1_gpu_align() -> None:
    from core.gpu_align import (
        Precision, compare_window_peaks, resolve_policy,
    )
    from core.gpu_align.parity import compare_surfaces
    from core.gpu_align.types import SurfaceBatch, WindowPeak

    section("L1 gpu_align policy/parity")
    record("gpu_align.policy exact",
           resolve_policy("exact").compute_dtype == "float64")
    record("gpu_align.policy fast",
           resolve_policy("fast").compute_dtype == "float32")
    record("gpu_align.policy hybrid",
           resolve_policy("hybrid").precision is Precision.HYBRID)
    record("gpu_align.policy off-like no error",
           resolve_policy("0").compute_dtype == "float64")

    # Pure peak-selection contract: negative m maps to NFFT wrap, but the
    # window array is already cropped; index arithmetic must stay in range.
    row = np.asarray([0.1, 0.9, -0.2, 0.3, 0.4])
    eng = object.__new__(__import__("core.gpu_align.window_cc",
                                    fromlist=["WindowCCEngine"]
                                    ).WindowCCEngine)
    peaks = eng.peaks_from_windows(
        row[None, :], np.asarray([True]), m_lo=-2,
        lag_lo_c=-2, lag_hi_c=2)
    record("gpu_align.peaks best index/lag",
           peaks[0] is not None and peaks[0].lag == -(-1),
           f"peak={peaks[0]}")
    record("gpu_align.peaks empty search",
           eng.peaks_from_windows(
               row[None, :], np.asarray([True]), m_lo=-2,
               lag_lo_c=5, lag_hi_c=6)[0] is None)
    record("gpu_align.peaks invalid window",
           eng.peaks_from_windows(
               row[None, :], np.asarray([False]), m_lo=-2,
               lag_lo_c=-2, lag_hi_c=2)[0] is None)

    cpu = [(3, 0.5, 0.5), (4, -0.25, 0.25)]
    gpu = [WindowPeak(3, 0.5, 0.5), WindowPeak(4, -0.25, 0.25)]
    record("gpu_align.parity exact",
           compare_window_peaks(cpu, gpu).ok)
    record("gpu_align.parity lag mismatch",
           not compare_window_peaks(cpu, [WindowPeak(5, 0.5, 0.5),
                                          gpu[1]]).ok)
    record("gpu_align.parity fast cc tolerance",
           compare_window_peaks(
               cpu, [WindowPeak(3, 0.5000001, 0.5000001), gpu[1]],
               cc_tol=1e-5).ok)
    record("gpu_align.parity fast cc rejection",
           not compare_window_peaks(
               cpu, [WindowPeak(3, 0.5001, 0.5001), gpu[1]],
               cc_tol=1e-5).ok)

    sa = SurfaceBatch(np.zeros((2, 3)), 16, np.asarray([True, False]), 0, 2)
    sb = SurfaceBatch(np.zeros((2, 3)), 16, np.asarray([True, False]), 0, 2)
    record("gpu_align.surface parity", compare_surfaces(sa, sb).ok)
    record("gpu_align.surface mismatch",
           not compare_surfaces(sa, SurfaceBatch(
               np.ones((2, 3)), 16, np.asarray([True, False]), 0, 2)).ok)

def l1_gpu_align_fallback() -> None:
    """GPU-off / unavailable paths must be explicit, not silent failures."""
    from core.gpu_align import AlignGpuSession, gpu_available
    from core.gpu_align.session import session_from_env

    section("L1 gpu_align fallback/availability")
    record("gpu_align.off mode returns no session",
           session_from_env(mode="0") is None)
    record("gpu_align.off literal is None",
           AlignGpuSession.create(mode="off") is None)
    record("gpu_align.available is bool",
           isinstance(gpu_available(), bool))
    # If the environment truly has no CUDA, creating an enabled session must
    # still return None instead of raising; when CUDA exists this is a no-op.
    if not gpu_available():
        record("gpu_align.no CUDA -> None session",
               AlignGpuSession.create(mode="fast") is None)

