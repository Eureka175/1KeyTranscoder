#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-target window surface engine (P2b).

One anchor and several target coarse caches share a single batched cuFFT
call.  The output has the same cropped/normalized window semantics as
``WindowCCEngine.surface_windows``; callers still do continuity/peak
selection on the CPU.

This is the GPU-side building block for batching anchor->target window
requests; the production adapter is wired in a later step.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

import numpy as np
from scipy import fft as _fft

from .backend import GpuBackend
from .precision import Policy, Precision, resolve_policy
from .types import BatchStats

__all__ = ["MultiWindowEngine"]


class MultiWindowEngine:
    """Batch window correlation across multiple target channels."""

    def __init__(self, backend: GpuBackend, ref12: np.ndarray,
                 targets: Mapping[str, np.ndarray], *,
                 policy: Policy | None = None,
                 budget_bytes: int | None = None) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        self._is_fp32 = self.policy.precision is Precision.FP32
        dtype = np.float32 if self._is_fp32 else np.float64
        self._ref = np.asarray(ref12, dtype=dtype)
        self._targets = {str(k): np.asarray(v, dtype=dtype)
                         for k, v in targets.items()}
        self.stats: list[BatchStats] = []

    def _batch_size(self, n: int, nfft: int) -> int:
        per = 32 if self._is_fp32 else 64
        est = max(1, self.budget_bytes // max(1, (nfft + 2 * n) * per))
        return int(min(128, max(1, est)))

    def surface_windows(
        self, requests: Iterable[tuple[str, int, int]], *,
        lag_lo_c: int, lag_hi_c: int,
    ) -> dict[str, tuple[np.ndarray, np.ndarray, int, int]]:
        """Return ``{target: (windows, valid, m_lo, m_hi)}``.

        requests: (target_key, start_c, count_c).  Widgets are grouped by
        count; all requests in a count group share one GPU batch.
        """
        reqs = [(str(k), int(s), int(c)) for k, s, c in requests]
        out: dict[str, list[np.ndarray | None]] = {}
        valids: dict[str, list[np.ndarray | None]] = {}
        for key in self._targets:
            out[key] = []
            valids[key] = []
        grouped: dict[int, list[tuple[str, int, int]]] = {}
        for item in reqs:
            grouped.setdefault(int(item[2]), []).append(item)
        m_lo = m_hi = 0
        cp = self.cp
        compute = cp.float32 if self._is_fp32 else cp.float64
        for count, items in grouped.items():
            n = int(count)
            if n < 16:
                for k, _s, _c in items:
                    out[k].append(None)
                    valids[k].append(None)
                continue
            m_lo = max(-(n - 1), int(lag_lo_c))
            m_hi = min(n - 1, int(lag_hi_c))
            if m_lo > m_hi:
                for k, _s, _c in items:
                    out[k].append(None)
                    valids[k].append(None)
                continue
            nfft = int(_fft.next_fast_len(2 * n - 1, real=True))
            batch = self._batch_size(n, nfft)
            for lo in range(0, len(items), batch):
                chunk = items[lo:lo + batch]
                ref = np.empty((len(chunk), n), dtype=self._ref.dtype)
                tgt = np.empty_like(ref)
                for j, (key, s, _count) in enumerate(chunk):
                    ref[j] = self._ref[s:s + n]
                    tgt[j] = self._targets[key][s:s + n]
                t0 = time.perf_counter()
                r = cp.asarray(ref, dtype=compute)
                t = cp.asarray(tgt, dtype=compute)
                r = r - r.mean(axis=1, keepdims=True)
                t = t - t.mean(axis=1, keepdims=True)
                na = cp.linalg.norm(r, axis=1)
                nb = cp.linalg.norm(t, axis=1)
                g = (cp.fft.rfft(r, nfft, axis=1) *
                     cp.conj(cp.fft.rfft(t, nfft, axis=1)))
                surf = cp.fft.irfft(g, nfft, axis=1)
                den = na * nb
                if m_lo < 0:
                    parts = []
                    if m_hi < 0:
                        parts.append(surf[:, nfft + m_lo:nfft + m_hi + 1])
                    else:
                        parts.append(surf[:, nfft + m_lo:nfft])
                        parts.append(surf[:, 0:m_hi + 1])
                    crop_gpu = (cp.concatenate(parts, axis=1)
                                if len(parts) > 1 else parts[0])
                else:
                    crop_gpu = surf[:, m_lo:m_hi + 1]
                crop = cp.asnumpy(crop_gpu)
                d = cp.asnumpy(den)
                valid = d >= 1e-12
                crop = np.divide(crop, np.where(valid, d, 1.0)[:, None])
                for j, (key, _s, _c) in enumerate(chunk):
                    out[key].append(crop[j])
                    valids[key].append(np.asarray([valid[j]], dtype=bool))
                self.stats.append(BatchStats(
                    kernel="window_multi_surface",
                    dtype=np.dtype(compute).name,
                    batch=len(chunk), nfft=nfft, items=len(chunk),
                    gpu_ms=(time.perf_counter() - t0) * 1000.0,
                ))
        result: dict[str, tuple[np.ndarray, np.ndarray, int, int]] = {}
        for key in self._targets:
            rows = out[key]
            if not rows or any(r is None for r in rows):
                result[key] = (np.empty((0, 0)),
                               np.zeros(0, dtype=bool), m_lo, m_hi)
                continue
            widths = {r.shape[0] for r in rows}
            if len(widths) != 1:
                result[key] = (np.empty((0, 0)),
                               np.zeros(0, dtype=bool), m_lo, m_hi)
                continue
            result[key] = (np.stack(rows), np.concatenate(valids[key]),
                           m_lo, m_hi)
        return result

    def warmup(self) -> None:
        try:
            n = min(128, int(self._ref.shape[0]))
            key = next(iter(self._targets))
            self.surface_windows([(key, 0, n)], lag_lo_c=-8, lag_hi_c=8)
        except Exception:                                  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass
