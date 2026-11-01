#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Result types for the GPU alignment engines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["WindowPeak", "SurfaceBatch", "BatchStats"]


@dataclass(frozen=True)
class WindowPeak:
    """One coarse window's peak (same semantics as sync_estimate._coarse_cc_peak)."""

    lag: int
    signed_cc: float
    abs_cc: float

    def tuple(self) -> tuple[int, float, float]:
        return self.lag, self.signed_cc, self.abs_cc


@dataclass
class SurfaceBatch:
    """Batched GCC-PHAT correlation windows (m_lo..m_hi) for B frames."""

    windows: np.ndarray                 # (B, W) float64
    nfft: int
    valid: np.ndarray                   # (B,) bool
    m_lo: int
    m_hi: int
    lags: np.ndarray | None = None      # (B,) argmax lag when requested

    def __post_init__(self) -> None:
        self.windows = np.asarray(self.windows, dtype=np.float64)
        self.valid = np.asarray(self.valid, dtype=bool)


@dataclass
class BatchStats:
    """Observability record for one GPU batch."""

    kernel: str
    dtype: str
    batch: int
    nfft: int
    items: int = 0
    gpu_ms: float = 0.0
    peer_ms: float = 0.0
    fallback: bool = False
    fallback_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "kernel": self.kernel,
            "dtype": self.dtype,
            "batch": self.batch,
            "nfft": self.nfft,
            "items": self.items,
            "gpu_ms": round(self.gpu_ms, 3),
            "peer_ms": round(self.peer_ms, 3),
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
        }
        d.update(self.extra)
        return d
