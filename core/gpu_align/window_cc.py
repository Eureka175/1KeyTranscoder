#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batched window-level coarse cross-correlation on the GPU.

This is the first production-quality block of the rewritten architecture: it
replaces per-window `sync_estimate._coarse_cc_peak` calls with one batched
cuFFT invocation per equal-length group, while keeping the caller's decision
logic (continuity, thresholds, report rounding) unchanged.

Parity:
  * EXACT keeps coarse caches in float64; delays are identical and CC values
    differ from CPU pocketfft at ~1e-15.
  * FAST stores/calculates float32; delays are expected equal but CC values
    differ at ~1e-7 and must not be used where report bit-parity is required.
"""
from __future__ import annotations

import functools
import math
import os
import time
from typing import Any, Iterable

import numpy as np
from scipy import fft as _fft

from .backend import GpuBackend, GpuOutOfMemory
from .precision import Policy, Precision, resolve_policy
from .types import BatchStats, WindowPeak

__all__ = ["WindowCCEngine"]


def _serialized(method):
    """Run a GPU section under the backend's single execution lock."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.backend.lock:
            return method(self, *args, **kwargs)
    return wrapper


class WindowCCEngine:
    """Upload coarse caches once; correlate many windows in batches."""

    def __init__(self, backend: GpuBackend, ref12: np.ndarray,
                 tgt12: np.ndarray, *, policy: Policy | None = None,
                 budget_bytes: int | None = None) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        # cuFFT plan selection can depend on the plan-cache state left by other
        # engines (e.g. a residual FFT created before this window engine).
        # For a strict deterministic contract, optionally start every window
        # engine from an empty plan cache.
        if os.environ.get("KTS_GPU_FFT_PLAN_FRESH", "1").strip().lower() \
                not in ("", "0", "off", "false", "no"):
            try:
                self.cp.fft.config.get_plan_cache().clear()
            except Exception:                          # noqa: BLE001
                pass
        storage = (self.cp.float32 if self.policy.precision is Precision.FP32
                   else self.cp.float64)
        self._storage_is_fp32 = self.policy.precision is Precision.FP32
        # Keep host copies for slicing; upload only the current batch.  The
        # coarse caches themselves can be large (C1305 4ch ~1.2 GB f64), so a
        # full upload plus batch is unnecessary VRAM pressure.
        self._ref_host = np.asarray(ref12, dtype=np.float64 if not self._storage_is_fp32 else np.float32)
        self._tgt_host = np.asarray(tgt12, dtype=self._ref_host.dtype)
        self.stats: list[BatchStats] = []

    @property
    def n(self) -> int:
        return int(min(self._ref_host.shape[0], self._tgt_host.shape[0]))

    # ------------------------------------------------------------------
    def _batch_size(self, n: int, nfft: int) -> int:
        # rfft complex (nfft/2+1) + surface (nfft) + batch matrices; use a
        # conservative 64 bytes/element for float64, 32 for float32.
        per = 64 if not self._storage_is_fp32 else 32
        est = max(1, self.budget_bytes // max(1, (nfft + 2 * n) * per))
        return int(min(64, max(1, est)))

    def _correlate_group(self, items: list[tuple[int, int, int, int]],
                         n: int) -> list[WindowPeak | None]:
        """items: (start_c, count_c, lag_lo, lag_hi); equal n."""
        cp = self.cp
        compute = (cp.float32 if self._storage_is_fp32 else cp.float64)
        nfft = int(_fft.next_fast_len(2 * n - 1, real=True))
        starts = np.asarray([it[0] for it in items], dtype=np.int64)
        # Build (B,n) host matrices first; the coarse cache slices are small
        # compared with the FFT result and this keeps cupy advanced indexing
        # out of the hot path.
        ref = np.empty((len(items), n), dtype=np.float32 if self._storage_is_fp32
                       else np.float64)
        tgt = np.empty_like(ref)
        for i, s in enumerate(starts):
            ref[i] = self._ref_host[s:s + n]
            tgt[i] = self._tgt_host[s:s + n]
        batch = self._batch_size(n, nfft)
        out: list[WindowPeak | None] = []
        for lo in range(0, len(items), batch):
            chunk = items[lo:lo + batch]
            r = cp.asarray(ref[lo:lo + len(chunk)], dtype=compute)
            t = cp.asarray(tgt[lo:lo + len(chunk)], dtype=compute)
            t0 = time.perf_counter()
            r = r - r.mean(axis=1, keepdims=True)
            t = t - t.mean(axis=1, keepdims=True)
            na = cp.linalg.norm(r, axis=1)
            nb = cp.linalg.norm(t, axis=1)
            g = (cp.fft.rfft(r, nfft, axis=1) *
                 cp.conj(cp.fft.rfft(t, nfft, axis=1)))
            surf = cp.fft.irfft(g, nfft, axis=1)
            # keep the per-row search slices on the GPU; only those transfer
            den = (na * nb)
            for j, (s, count, lag_lo, lag_hi) in enumerate(chunk):
                m_lo = max(-(n - 1), int(math.ceil(-lag_hi / 1)))
                m_hi = min(n - 1, int(math.floor(-lag_lo / 1)))
                if m_lo > m_hi:
                    out.append(None)
                    continue
                # q is applied by the caller's coarse coordinates; here
                # lag_lo/lag_hi are already coarse lags, so q=1 internally.
                idx = np.arange(m_lo, m_hi + 1, dtype=np.int64)
                idx_mod = np.where(idx < 0, idx + nfft, idx)
                vals = cp.asnumpy(surf[j][cp.asarray(idx_mod)])
                if vals.size == 0:
                    out.append(None)
                    continue
                k = int(np.argmax(np.abs(vals)))
                d = float(den[j].item()) if hasattr(den[j], "item") else float(den[j])
                if d < 1e-12:
                    out.append(None)
                    continue
                signed = float(vals[k]) / d
                out.append(WindowPeak(int(-int(idx[k])), signed, abs(signed)))
            gpu_ms = (time.perf_counter() - t0) * 1000.0
            self.stats.append(BatchStats(
                kernel="window_cc", dtype=np.dtype(compute).name,
                batch=len(chunk), nfft=nfft, items=len(chunk),
                gpu_ms=gpu_ms,
            ))
        return out

    @_serialized
    def surface_windows(self, requests: Iterable[tuple[int, int]],
                        *, lag_lo_c: int, lag_hi_c: int
                        ) -> tuple[np.ndarray, int, int, np.ndarray]:
        """Batched normalized correlation windows over a common lag range.

        This is the architecture's key API: the caller computes *all* window
        surfaces once with a fixed search range, then performs continuity /
        threshold peak selection on the CPU.  It avoids one GPU launch per
        window while keeping the precise peak-selection semantics outside the
        engine.

        Returns (windows, m_lo, m_hi, valid):
          * windows[i] = normalized surf[m_lo..m_hi] for request i;
          * lag for index m is -m (coarse samples); multiply by q outside.
        """
        reqs = [(int(s), int(c)) for s, c in requests]
        if not reqs:
            return np.empty((0, 0)), 0, 0, np.zeros(0, dtype=bool)
        cp = self.cp
        compute = cp.float32 if self._storage_is_fp32 else cp.float64
        groups: dict[int, list[int]] = {}
        for i, (s, count) in enumerate(reqs):
            n = min(int(count), self.n - s)
            groups.setdefault(max(0, n), []).append(i)
        out: list[np.ndarray] = [None] * len(reqs)            # type: ignore[list-item]
        valids: list[np.ndarray] = [None] * len(reqs)         # type: ignore[list-item]
        m_lo = m_hi = 0
        for n, idxs in groups.items():
            if n < 16:
                for i in idxs:
                    out[i] = np.empty(0, dtype=np.float64)
                    valids[i] = np.zeros(1, dtype=bool)
                continue
            m_lo = max(-(n - 1), int(lag_lo_c))
            m_hi = min(n - 1, int(lag_hi_c))
            if m_lo > m_hi:
                for i in idxs:
                    out[i] = np.empty(0, dtype=np.float64)
                    valids[i] = np.zeros(1, dtype=bool)
                continue
            width = m_hi - m_lo + 1
            nfft = int(_fft.next_fast_len(2 * n - 1, real=True))
            batch = self._batch_size(n, nfft)
            for lo in range(0, len(idxs), batch):
                chunk = idxs[lo:lo + batch]
                ref = np.empty((len(chunk), n), dtype=self._ref_host.dtype)
                tgt = np.empty_like(ref)
                for j, i in enumerate(chunk):
                    s = reqs[i][0]
                    ref[j] = self._ref_host[s:s + n]
                    tgt[j] = self._tgt_host[s:s + n]
                r = cp.asarray(ref, dtype=compute)
                tg = cp.asarray(tgt, dtype=compute)
                t0 = time.perf_counter()
                r = r - r.mean(axis=1, keepdims=True)
                tg = tg - tg.mean(axis=1, keepdims=True)
                na = cp.linalg.norm(r, axis=1)
                nb = cp.linalg.norm(tg, axis=1)
                g = (cp.fft.rfft(r, nfft, axis=1) *
                     cp.conj(cp.fft.rfft(tg, nfft, axis=1)))
                surf = cp.fft.irfft(g, nfft, axis=1)
                den = na * nb
                # Negative lag m maps to nfft+m; a Python negative slice
                # start would mean "from the end", which is only correct for
                # m<0 when the stop is also negative -- gather explicitly.
                if m_lo < 0:
                    parts = []
                    if m_hi < 0:
                        parts.append(surf[:, nfft + m_lo:nfft + m_hi + 1])
                    else:
                        parts.append(surf[:, nfft + m_lo:nfft])
                        parts.append(surf[:, 0:m_hi + 1])
                    crop_gpu = cp.concatenate(parts, axis=1) if len(parts) > 1 else parts[0]
                else:
                    crop_gpu = surf[:, m_lo:m_hi + 1]
                crop = cp.asnumpy(crop_gpu)
                d = cp.asnumpy(den)
                valid = d >= 1e-12
                crop = np.divide(crop, np.where(valid, d, 1.0)[:, None])
                for j, i in enumerate(chunk):
                    out[i] = crop[j]
                    valids[i] = np.asarray([valid[j]], dtype=bool)
                self.stats.append(BatchStats(
                    kernel="window_cc_surface",
                    dtype=np.dtype(compute).name,
                    batch=len(chunk), nfft=nfft, items=len(chunk),
                    gpu_ms=(time.perf_counter() - t0) * 1000.0,
                ))
        return (np.stack(out) if out and len({a.shape for a in out}) == 1
                else np.empty((0, 0)), m_lo, m_hi,
                np.concatenate(valids) if valids else np.zeros(0, bool))

    def peaks_from_windows(self, windows: np.ndarray, valid: np.ndarray,
                           *, m_lo: int, lag_lo_c: int, lag_hi_c: int
                           ) -> list[WindowPeak | None]:
        """CPU peak selection over precomputed normalized windows.

        Mirrors `sync_estimate._coarse_cc_peak`'s argmax/|CC| rule; the caller
        converts coarse lag to full-rate samples with q.
        """
        out: list[WindowPeak | None] = []
        for row, ok in zip(windows, valid):
            if not bool(ok) or row.size == 0:
                out.append(None)
                continue
            # row index 0 corresponds to m = m_lo; valid m range is
            # intersection with [lag_lo_c, lag_hi_c].
            a = max(0, int(lag_lo_c) - m_lo)
            b = min(row.size - 1, int(lag_hi_c) - m_lo)
            if a > b:
                out.append(None)
                continue
            vals = row[a:b + 1]
            k = int(np.argmax(np.abs(vals)))
            m = m_lo + a + k
            signed = float(vals[k])
            out.append(WindowPeak(int(-m), signed, abs(signed)))
        return out

    @_serialized
    def batch_peaks(self, requests: Iterable[tuple[int, int, int, int]]
                    ) -> list[WindowPeak | None]:
        """requests: (start_c, count_c, lag_lo_samples, lag_hi_samples).

        lag_lo/lag_hi are in **full-rate samples**, matching
        `sync_estimate._coarse_cc_peak`; they are converted to coarse lags via
        the caller-provided q only for search bounds.  Here q is 1 and the
        caller passes coarse lags, so the production adapter must divide.
        """
        reqs = list(requests)
        if not reqs:
            return []
        results: list[WindowPeak | None] = [None] * len(reqs)
        groups: dict[int, list[int]] = {}
        for i, (_s, count, _lo, _hi) in enumerate(reqs):
            groups.setdefault(int(count), []).append(i)
        for count, idxs in groups.items():
            n = min(int(count), self.n)
            if n < 16:
                continue
            items = [(int(reqs[i][0]), count, int(reqs[i][2]), int(reqs[i][3]))
                     for i in idxs]
            got = self._correlate_group(items, n)
            for i, g in zip(idxs, got):
                results[i] = g
        return results

    def batch_peaks_q(self, requests: Iterable[tuple[int, int, int, int]],
                      q: int) -> list[WindowPeak | None]:
        """Production adapter: lag bounds are full-rate samples; q decimation.

        (Internally the peak index is in coarse samples; multiply by q.)
        """
        reqs = [(s, c, int(math.ceil(lo / q)), int(math.floor(hi / q)))
                for s, c, lo, hi in requests]
        got = self.batch_peaks(reqs)
        return [None if g is None else WindowPeak(
            int(g.lag * q), g.signed_cc, g.abs_cc) for g in got]

    def warmup(self) -> None:
        """Compile CuPy elementwise kernels / warm the FFT plan once."""
        n = min(128, self.n)
        if n >= 16:
            try:
                # Both APIs are used by measure_window_delays; compile the
                # crop/concat path too or the first real measurement pays
                # the JIT cost (tens of seconds on this machine).
                self.batch_peaks([(0, n, -8, 8)])
                self.surface_windows([(0, n)], lag_lo_c=-8, lag_hi_c=8)
            except Exception:                          # noqa: BLE001
                pass

    def close(self) -> None:
        try:
            del self._ref_host
            del self._tgt_host
        except Exception:                                  # noqa: BLE001
            pass
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass

