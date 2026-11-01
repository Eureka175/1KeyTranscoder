#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batched frame-level GCC-PHAT surfaces.

The engine returns the same correlation *window* that
`sync_estimate.gcc_phat_int` slices out (`m_lo..m_hi`), so the existing CPU
peak/confidence code can consume it unchanged.  That keeps the rewrite focused
on the FFT/vectorization hot path and lets parity be tested before touching
peak logic.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
from scipy import fft as _fft

from .backend import GpuBackend
from .precision import Policy, Precision, resolve_policy
from .types import BatchStats, SurfaceBatch

__all__ = ["FrameGccEngine"]


class FrameGccEngine:
    def __init__(self, backend: GpuBackend, *, policy: Policy | None = None,
                 budget_bytes: int | None = None) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        self._is_fp32 = self.policy.precision is Precision.FP32
        self.stats: list[BatchStats] = []

    def _batch_size(self, nfft: int, width: int) -> int:
        per = 40 if self._is_fp32 else 80
        return int(min(256, max(1, self.budget_bytes // max(1, nfft * per))))

    def surfaces(self, ref2d: np.ndarray, tgt2d: np.ndarray, *,
                 lag0: int, search_lo: int, search_hi: int,
                 rms_gate: float = 1e-12) -> SurfaceBatch:
        """Compute batched PHAT surfaces for rows of (B,F) / (B,L).

        Returns all B windows in one ``SurfaceBatch`` (chunked internally).
        """
        ref2d = np.asarray(ref2d)
        tgt2d = np.asarray(tgt2d)
        if ref2d.ndim != 2 or tgt2d.ndim != 2 or ref2d.shape[0] != tgt2d.shape[0]:
            raise ValueError("ref2d/tgt2d must be (B,F)/(B,L) with equal B")
        B, F = int(ref2d.shape[0]), int(ref2d.shape[1])
        L = int(tgt2d.shape[1])
        if F == 0 or L < F:
            raise ValueError("need L >= F > 0")
        m_lo = max(0, int(lag0) + int(search_lo))
        m_hi = min(L - F, int(lag0) + int(search_hi))
        if m_lo > m_hi or m_hi - m_lo + 1 < 3:
            raise ValueError("empty search window")
        width = m_hi - m_lo + 1
        nfft = int(_fft.next_fast_len(F + L - 1, real=True))
        cp = self.cp
        compute = cp.float32 if self._is_fp32 else cp.float64
        batch = self._batch_size(nfft, width)
        out = np.empty((B, width), dtype=np.float64)
        valid_all = np.zeros(B, dtype=bool)
        for lo in range(0, B, batch):
            hi = min(B, lo + batch)
            r = cp.asarray(ref2d[lo:hi], dtype=compute)
            t = cp.asarray(tgt2d[lo:hi], dtype=compute)
            t0 = time.perf_counter()
            r = r - r.mean(axis=1, keepdims=True)
            t = t - t.mean(axis=1, keepdims=True)
            rr = cp.sqrt(cp.mean(r * r, axis=1))
            rt = cp.sqrt(cp.mean(t * t, axis=1))
            valid = (rr >= rms_gate) & (rt >= rms_gate)
            safe_r = cp.where(valid, rr, 1)
            safe_t = cp.where(valid, rt, 1)
            r = r / safe_r[:, None]
            t = t / safe_t[:, None]
            g = (cp.conj(cp.fft.rfft(r, nfft, axis=1)) *
                 cp.fft.rfft(t, nfft, axis=1))
            gmax = cp.max(cp.abs(g), axis=1, keepdims=True)
            g = g / (cp.abs(g) + 1e-3 * gmax + 1e-12)
            surf = cp.fft.irfft(g, nfft, axis=1)
            out[lo:hi] = cp.asnumpy(surf[:, m_lo:m_hi + 1])
            valid_all[lo:hi] = cp.asnumpy(valid)
            self.stats.append(BatchStats(
                kernel="frame_gcc_surface",
                dtype=np.dtype(compute).name,
                batch=hi - lo, nfft=nfft, items=hi - lo,
                gpu_ms=(time.perf_counter() - t0) * 1000.0,
            ))
        return SurfaceBatch(out, nfft, valid_all, m_lo, m_hi)

    def warmup(self) -> None:
        """Compile elementwise kernels / warm cuFFT with a tiny batch."""
        try:
            ref = np.zeros((1, 64), dtype=np.float32)
            tgt = np.zeros((1, 80), dtype=np.float32)
            self.surfaces(ref, tgt, lag0=0, search_lo=-8, search_hi=8)
        except Exception:                              # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass
