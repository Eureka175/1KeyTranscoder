#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batched residual coarse-correlation seeds (long-take residual windows)."""
from __future__ import annotations

import functools
import math
import time

import numpy as np
from scipy import fft as _fft

from .backend import GpuBackend
from .precision import Precision, Policy, resolve_policy
from .types import BatchStats

__all__ = ["ResidualCCEngine"]


def _serialized(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.backend.lock:
            return method(self, *args, **kwargs)
    return wrapper


class ResidualCCEngine:
    """GPU equivalent of long_take.measure_residual_curve's coarse seed."""

    def __init__(self, backend: GpuBackend, *, policy: Policy | None = None,
                 budget_bytes: int | None = None) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self._is_fp32 = self.policy.precision is Precision.FP32
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        self.stats: list[BatchStats] = []

    @_serialized
    def peak(self, a12: np.ndarray, b12: np.ndarray, *, dec: int,
             search48: int) -> float | None:
        cp = self.cp
        n12 = int(min(a12.shape[0], b12.shape[0]))
        if n12 < 16:
            return None
        nfft = int(1 << int(math.ceil(math.log2(2 * n12))))
        compute = cp.float32 if self._is_fp32 else cp.float64
        try:
            t0 = time.perf_counter()
            a = cp.asarray(a12[:n12], dtype=compute)
            b = cp.asarray(b12[:n12], dtype=compute)
            a = a - a.mean()
            b = b - b.mean()
            A = cp.fft.rfft(a, nfft)
            B = cp.fft.rfft(b, nfft)
            surf = cp.fft.irfft(A * cp.conj(B), nfft)
            den = float(cp.linalg.norm(a).item()) * \
                float(cp.linalg.norm(b).item()) + 1e-12
            radius12 = max(4, search48 // dec)
            search = radius12 + 2
            head = surf[:search + 1]
            tail = surf[nfft - search:nfft]
            cand = cp.abs(cp.concatenate([head, tail]) / den)
            m = np.concatenate([np.arange(0, search + 1),
                                np.arange(-search, 0)]).astype(np.int64)
            i = int(cp.argmax(cand).item())
            self.stats.append(BatchStats(
                kernel="residual_cc", dtype=np.dtype(compute).name,
                batch=1, nfft=nfft, items=1,
                gpu_ms=(time.perf_counter() - t0) * 1000.0))
            return float(-m[i]) * dec
        except Exception:                                  # noqa: BLE001
            return None

    def close(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass
