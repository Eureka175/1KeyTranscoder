#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified GPU audio-alignment architecture (opt-in).

Public entry point.  Importing this package must be safe on machines without
CUDA: CuPy is imported lazily by ``backend``.
"""
from __future__ import annotations

from .backend import (
    GpuBackend,
    GpuOutOfMemory,
    GpuUnavailable,
    get_backend,
    gpu_available,
)
from .frame_gcc import FrameGccEngine
from .parity import ParityResult, compare_surfaces, compare_window_peaks
from .precision import Policy, Precision, resolve_policy
from .refine import RefineEngine
from .residual_cc import ResidualCCEngine
from .session import AlignGpuSession, session_from_env
from .observe import (
    GpuRunMetrics, begin_metrics, current_metrics, end_metrics,
)
from .runtime import GpuRunContext, begin_run, current_run, end_run
from .types import BatchStats, SurfaceBatch, WindowPeak
from .window_cc import WindowCCEngine
from .window_multi import MultiWindowEngine

__all__ = [
    "GpuBackend", "GpuOutOfMemory", "GpuUnavailable", "gpu_available",
    "get_backend", "Precision", "Policy", "resolve_policy",
    "WindowCCEngine", "MultiWindowEngine", "FrameGccEngine", "ResidualCCEngine",
    "RefineEngine",
    "AlignGpuSession", "session_from_env", "WindowPeak", "SurfaceBatch",
    "BatchStats", "ParityResult", "compare_window_peaks", "compare_surfaces",
    "GpuRunMetrics", "GpuRunContext", "begin_run", "end_run",
    "current_run", "begin_metrics", "end_metrics", "current_metrics",
]

__version__ = "0.1.0"
