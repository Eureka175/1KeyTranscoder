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
    record("gpu_align.policy fp64",
           resolve_policy("fp64").compute_dtype == "float64")
    record("gpu_align.policy fp32",
           resolve_policy("fp32").compute_dtype == "float32")
    record("gpu_align.policy hybrid",
           resolve_policy("hybrid").precision is Precision.HYBRID)
    record("gpu_align.policy off-like defaults fp32",
           resolve_policy("0").compute_dtype == "float32")

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
    record("gpu_align.parity fp64",
           compare_window_peaks(cpu, gpu).ok)
    record("gpu_align.parity lag mismatch",
           not compare_window_peaks(cpu, [WindowPeak(5, 0.5, 0.5),
                                          gpu[1]]).ok)
    record("gpu_align.parity fp32 cc tolerance",
           compare_window_peaks(
               cpu, [WindowPeak(3, 0.5000001, 0.5000001), gpu[1]],
               cc_tol=1e-5).ok)
    record("gpu_align.parity fp32 cc rejection",
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
               AlignGpuSession.create(mode="fp32") is None)

def l1_gpu_pipeline_runtime() -> None:
    """P0/P1 contracts: run metrics, coarse cache and session lifecycle."""
    from pathlib import Path

    import numpy as np

    from core.gpu_align.observe import (
        begin_metrics, current_metrics, end_metrics,
    )
    from core.gpu_align.runtime import begin_run, current_run, end_run

    section("L1 gpu_pipeline runtime/observability")

    m = begin_metrics("fp32")
    record("gpu_pipeline.metrics current", current_metrics() is m)
    m.record_batch({"kernel": "window_cc", "gpu_ms": 2.5,
                    "fallback": False})
    m.record_batch({"kernel": "residual_cc", "gpu_ms": 1.5,
                    "fallback": True})
    m.record_stage("window_measure", 0.25)
    m.record_session(created=True)
    m.record_session(created=False)
    s = end_metrics(wall_s=1.0)
    record("gpu_pipeline.metrics batches", s["batches"] == 2)
    record("gpu_pipeline.metrics fallback", s["fallback_batches"] == 1)
    record("gpu_pipeline.metrics gpu_active",
           abs(s["gpu_active_s"] - 0.004) < 1e-9)
    record("gpu_pipeline.metrics session counts",
           s["sessions_created"] == 1 and s["sessions_reused"] == 1)
    record("gpu_pipeline.metrics cleared", current_metrics() is None)

    ctx = begin_run("0")
    record("gpu_pipeline.run current", current_run() is ctx)
    key = ctx.coarse_key(Path("a.raw"), sample_rate=48000,
                          coarse_rate=12000, storage_dtype="f32")
    arr = ctx.coarse_get_or_build(key, lambda: np.zeros(8, dtype=np.float32))
    arr2 = ctx.coarse_get_or_build(key, lambda: np.ones(8, dtype=np.float32))
    record("gpu_pipeline.coarse same array", arr is arr2)
    record("gpu_pipeline.coarse hit",
           ctx.metrics.coarse_hits == 1
           and ctx.metrics.coarse_misses == 1)
    record("gpu_pipeline.off session is none", ctx.session() is None)
    summary = end_run(wall_s=1.0)
    record("gpu_pipeline.run end", current_run() is None
           and summary is not None)
    record("gpu_pipeline.run off summary",
           summary["sessions_created"] == 0)
