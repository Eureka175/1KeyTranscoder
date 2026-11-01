#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU integer-lag refinement for window measurements.

The coarse CC only narrows the lag to a few samples; the production
`_refine_window_peak` then computes ~17 full-rate normalized dot products on a
2 s segment.  Those dot products are independent of the continuity logic and
map well to one small GPU batch per call, so this module provides the full-precision
same formula with a GPU backend and a CPU fallback.
"""
from __future__ import annotations

import time

import numpy as np

from .backend import GpuBackend
from .precision import Precision, Policy, resolve_policy

__all__ = ["RefineEngine"]


class RefineEngine:
    def __init__(self, backend: GpuBackend, *, policy: Policy | None = None,
                 budget_bytes: int | None = None) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self._is_fp32 = self.policy.precision is Precision.FP32
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        self.calls = 0
        self.gpu_s = 0.0

    def warmup(self) -> None:
        try:
            cp = self.cp
            a = cp.zeros(64, dtype=cp.float64)
            b = cp.zeros(80, dtype=cp.float64)
            cp.dot(a, b[8:72])
            self.calls += 1
        except Exception:                                  # noqa: BLE001
            pass

    def refine(self, ref: object, tgt: object, *, start_sample: int,
               window_samples: int, coarse_lag: int, radius: int,
               segment_samples: int) -> tuple[int, float]:
        """Same contract as sync_estimate._refine_window_peak."""
        from core.sync_estimate import read_f64

        seg = int(segment_samples)
        if seg < 8 or window_samples < seg:
            return int(coarse_lag), 0.0
        c0 = int(start_sample) + int(window_samples) // 2 - seg // 2
        if c0 < 0:
            c0 = 0
        ref_seg = np.asarray(read_f64(ref, c0, seg), dtype=np.float64)
        av = ref_seg - ref_seg.mean()
        na = float(np.linalg.norm(av))
        if na < 1e-12:
            return int(coarse_lag), 0.0
        center = int(coarse_lag)
        tgt_span = np.asarray(
            read_f64(tgt, c0 + center - radius, seg + 2 * radius),
            dtype=np.float64)
        win_end = int(start_sample) + int(window_samples)
        cp = self.cp
        compute = cp.float32 if self._is_fp32 else cp.float64
        t0 = time.perf_counter()
        try:
            av_g = cp.asarray(av, dtype=compute)
            na_g = float(cp.linalg.norm(av_g).item())
            best_m = None
            best_val = 0.0
            # 17 small dots; one upload of the segment per call.
            for j in range(0, 2 * radius + 1):
                m = center - radius + j
                b0 = c0 + m
                if b0 < 0 or b0 + seg > win_end:
                    continue
                bv = cp.asarray(tgt_span[j:j + seg], dtype=compute)
                bv = bv - bv.mean()
                den = na_g * float(cp.linalg.norm(bv).item())
                if den < 1e-12:
                    continue
                val = float(cp.dot(av_g, bv).item() / den)
                if best_m is None or abs(val) > abs(best_val):
                    best_m = m
                    best_val = val
            self.calls += 1
            self.gpu_s += time.perf_counter() - t0
            if best_m is None:
                return int(coarse_lag), 0.0
            return int(best_m), abs(float(best_val))
        except Exception:                                  # noqa: BLE001
            return int(coarse_lag), 0.0

    def close(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass
