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
from .types import BatchStats, SurfaceBatch, WindowPeak
from .window_cc import WindowCCEngine

__all__ = [
    "GpuBackend", "GpuOutOfMemory", "GpuUnavailable", "gpu_available",
    "get_backend", "Precision", "Policy", "resolve_policy",
    "WindowCCEngine", "FrameGccEngine", "ResidualCCEngine",
    "RefineEngine",
    "AlignGpuSession", "session_from_env", "WindowPeak", "SurfaceBatch",
    "BatchStats", "ParityResult", "compare_window_peaks", "compare_surfaces",
]

__version__ = "0.1.0"
