#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU pipeline observability (P0).

Tracks the facts that decide whether the GPU is actually used as a pipeline:
session/engine creation counts, batch kernel time, fallback batches, cache hits,
and per-stage wall time.  This module is deliberately free of CuPy imports so
it can be used by CPU-only tests and by the fallback path.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

__all__ = ["GpuRunMetrics", "begin_metrics", "end_metrics", "current_metrics"]


@dataclass
class GpuRunMetrics:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    mode: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    sessions_created: int = 0
    sessions_reused: int = 0
    session_failures: int = 0
    engines_created: int = 0
    engine_reused: int = 0

    batches: int = 0
    gpu_ms: float = 0.0
    fallback_batches: int = 0
    kernels: Counter = field(default_factory=Counter)
    stages: dict[str, float] = field(default_factory=dict)

    coarse_hits: int = 0
    coarse_misses: int = 0
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def record_batch(self, stats: Any) -> None:
        """Record one engine:gpu_align.BatchStats (or compatible dict)."""
        if stats is None:
            return
        if hasattr(stats, "as_dict"):
            d = stats.as_dict()
        elif isinstance(stats, dict):
            d = stats
        else:
            return
        self.batches += 1
        self.gpu_ms += float(d.get("gpu_ms", 0.0) or 0.0)
        if d.get("fallback"):
            self.fallback_batches += 1
        self.kernels[str(d.get("kernel", "?"))] += 1

    def record_stage(self, name: str, wall_s: float) -> None:
        self.stages[name] = round(
            self.stages.get(name, 0.0) + float(wall_s), 6)

    def record_session(self, created: bool, ok: bool = True) -> None:
        if created and ok:
            self.sessions_created += 1
        elif created and not ok:
            self.session_failures += 1
        else:
            self.sessions_reused += 1

    def record_engine(self, created: bool = True) -> None:
        if created:
            self.engines_created += 1
        else:
            self.engine_reused += 1

    def record_coarse(self, hit: bool) -> None:
        if hit:
            self.coarse_hits += 1
        else:
            self.coarse_misses += 1

    def summary(self, *, wall_s: float | None = None) -> dict[str, Any]:
        end = self.ended_at or time.time()
        total = (end - self.started_at) if wall_s is None else float(wall_s)
        gpu_active = self.gpu_ms / 1000.0
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "wall_s": round(total, 3),
            "gpu_active_s": round(gpu_active, 3),
            "gpu_active_ratio": (round(gpu_active / total, 4)
                                 if total > 0 else 0.0),
            "sessions_created": self.sessions_created,
            "sessions_reused": self.sessions_reused,
            "session_failures": self.session_failures,
            "engines_created": self.engines_created,
            "engine_reused": self.engine_reused,
            "batches": self.batches,
            "fallback_batches": self.fallback_batches,
            "kernels": dict(self.kernels),
            "stages": dict(self.stages),
            "coarse_hits": self.coarse_hits,
            "coarse_misses": self.coarse_misses,
            "notes": list(self.notes),
        }


_LOCK = threading.Lock()
_ACTIVE: GpuRunMetrics | None = None


def begin_metrics(mode: str = "") -> GpuRunMetrics:
    """Start/replace the process-wide run metrics (single run per process)."""
    global _ACTIVE
    with _LOCK:
        _ACTIVE = GpuRunMetrics(mode=str(mode))
        return _ACTIVE


def end_metrics(*, wall_s: float | None = None
                ) -> dict[str, Any] | None:
    global _ACTIVE
    with _LOCK:
        m, _ACTIVE = _ACTIVE, None
    if m is None:
        return None
    m.ended_at = time.time()
    return m.summary(wall_s=wall_s)


def current_metrics() -> GpuRunMetrics | None:
    with _LOCK:
        return _ACTIVE
