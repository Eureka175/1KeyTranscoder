#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batched full-rate refine engine (P3 experiment).

Replaces the per-window CPU Python loop in ``_refine_window_peak`` with one
GPU batch: for each request the ref segment is normalized once and all
(2*radius+1) target shifts are evaluated as a batched matrix operation.
Continuity remains a CPU state machine; this engine is for callers that
already have candidate lag centers (and can batch windows/units).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .backend import GpuBackend
from .precision import Precision, Policy, resolve_policy
from .types import BatchStats

__all__ = ["RefineRequest", "RefineBatchEngine"]


@dataclass
class RefineRequest:
    ref_seg: np.ndarray          # (seg,) full-rate
    tgt_span: np.ndarray         # (seg + 2*radius,)
    center: int                  # candidate coarse lag center (full-rate)
    radius: int
    c0: int                      # absolute ref segment start
    window_end: int              # absolute window end
    seg: int


class RefineBatchEngine:
    def __init__(self, backend: GpuBackend, *,
                 policy: Policy | None = None,
                 budget_bytes: int | None = None,
                 exact: bool = False) -> None:
        self.backend = backend
        self.cp = backend.cp
        self.policy = policy or resolve_policy()
        self._is_fp32 = (self.policy.precision is Precision.FP32
                         and not exact)
        self.budget_bytes = int(budget_bytes or backend.budget_bytes())
        self.stats: list[BatchStats] = []

    def _batch_size(self, seg: int, width: int) -> int:
        per = 8 if self._is_fp32 else 16
        return int(min(64, max(
            1, self.budget_bytes // max(1, (seg + width) * per))))

    def refine(self, requests: list[RefineRequest]) -> list[tuple[int, float]]:
        """Return (lag, |cc|) per request; invalid => (center, 0.0)."""
        out: list[tuple[int, float]] = [
            (int(r.center), 0.0) for r in requests]
        if not requests:
            return out
        cp = self.cp
        compute = cp.float32 if self._is_fp32 else cp.float64
        # group by (seg, radius) to keep common matrices
        groups: dict[tuple[int, int], list[int]] = {}
        for i, r in enumerate(requests):
            groups.setdefault((int(r.seg), int(r.radius)), []).append(i)
        for (seg, radius), idxs in groups.items():
            width = 2 * radius + 1
            batch = self._batch_size(seg, width)
            for lo in range(0, len(idxs), batch):
                chunk = idxs[lo:lo + batch]
                B = len(chunk)
                refs = np.empty((B, seg), dtype=np.float32 if self._is_fp32
                                else np.float64)
                tgts = np.empty((B, seg + 2 * radius),
                                dtype=refs.dtype)
                centers = np.empty(B, dtype=np.int64)
                c0s = np.empty(B, dtype=np.int64)
                ends = np.empty(B, dtype=np.int64)
                for j, i in enumerate(chunk):
                    r = requests[i]
                    refs[j] = r.ref_seg
                    tgts[j] = r.tgt_span
                    centers[j] = int(r.center)
                    c0s[j] = int(r.c0)
                    ends[j] = int(r.window_end)
                t0 = time.perf_counter()
                r = cp.asarray(refs, dtype=compute)
                t = cp.asarray(tgts, dtype=compute)
                r = r - r.mean(axis=1, keepdims=True)
                na = cp.linalg.norm(r, axis=1)
                # Candidate value matrix (B, 2r+1)
                vals = cp.empty((B, width), dtype=compute)
                ok = cp.zeros((B, width), dtype=cp.bool_)
                for k in range(width):
                    bv = t[:, k:k + seg]
                    bv = bv - bv.mean(axis=1, keepdims=True)
                    nb = cp.linalg.norm(bv, axis=1)
                    den = na * nb
                    v = cp.sum(r * bv, axis=1) / cp.maximum(den, 1e-12)
                    vals[:, k] = v
                    # absolute target start for this candidate; validity is
                    # computed on host and combined with the GPU norm mask.
                    start_abs = c0s + centers - radius + k
                    good_host = ((start_abs >= 0)
                                 & (start_abs + seg <= ends))
                    ok[:, k] = cp.asarray(good_host) & (den >= 1e-12)
                # invalidate
                vals = cp.where(ok, cp.abs(vals), -1.0)
                best = cp.argmax(vals, axis=1)
                best_v = cp.max(vals, axis=1)
                best_np = cp.asnumpy(best)
                v_np = cp.asnumpy(best_v)
                for j, i in enumerate(chunk):
                    if float(v_np[j]) < 0:
                        out[i] = (int(requests[i].center), 0.0)
                    else:
                        lag = (int(centers[j]) - radius
                               + int(best_np[j]))
                        out[i] = (lag, float(v_np[j]))
                self.stats.append(BatchStats(
                    kernel="refine_batch", dtype=np.dtype(compute).name,
                    batch=B, nfft=seg, items=B,
                    gpu_ms=(time.perf_counter() - t0) * 1000.0))
        return out

    def close(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass
