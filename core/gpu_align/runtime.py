#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run-level GPU runtime context (P1).

One GPU session and one coarse-cache registry per channel_sync run.  The
context is process-wide because channel_sync processes one file at a time;
``--jobs`` concurrency uses separate processes.  Direct callers that do not
begin a run keep the old per-call session behaviour.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .observe import GpuRunMetrics, begin_metrics, end_metrics, current_metrics
from .session import AlignGpuSession

__all__ = ["GpuRunContext", "begin_run", "end_run", "current_run"]


@dataclass
class GpuRunContext:
    mode: str
    metrics: GpuRunMetrics
    _session: AlignGpuSession | None = None
    _session_tried: bool = False
    _residual_engine: Any = None
    coarse: dict[tuple, np.ndarray] = field(default_factory=dict)
    engines: list[Any] = field(default_factory=list)
    closed: bool = False

    # ------------------------------------------------------------------
    def session(self) -> AlignGpuSession | None:
        if self._session_tried:
            if self._session is not None:
                self.metrics.record_session(created=False)
            return self._session
        self._session_tried = True
        try:
            self._session = AlignGpuSession.create(mode=self.mode)
        except Exception:                                  # noqa: BLE001
            self._session = None
        if self._session is None:
            self.metrics.record_session(created=True, ok=False)
        else:
            self.metrics.record_session(created=True, ok=True)
        return self._session

    def residual_engine(self) -> Any:
        # One engine per residual call: long-take may call residual windows
        # from a thread pool; a shared mutable engine/stats list is unsafe.
        sess = self.session()
        if sess is None:
            return None
        from .residual_cc import ResidualCCEngine
        eng = ResidualCCEngine(sess.backend, policy=sess.policy)
        self.metrics.record_engine(created=True)
        self.engines.append(eng)
        return eng

    # -- coarse cache --------------------------------------------------
    @staticmethod
    def coarse_key(src: Any, *, sample_rate: int, coarse_rate: int,
                   storage_dtype: str) -> tuple | None:
        if isinstance(src, (str, Path)):
            path = src
        else:
            path = getattr(src, "path", None)
        if path is not None:
            try:
                return ("path", str(path.resolve()), int(sample_rate),
                        int(coarse_rate), str(storage_dtype))
            except Exception:                              # noqa: BLE001
                return None
        if isinstance(src, np.ndarray):
            return ("array", int(src.__array_interface__["data"][0]),
                    int(src.shape[0]), int(sample_rate), int(coarse_rate),
                    str(storage_dtype))
        return None

    def coarse_get_or_build(self, key: tuple | None,
                            builder: Callable[[], np.ndarray]
                            ) -> np.ndarray:
        if key is not None:
            got = self.coarse.get(key)
            if got is not None:
                self.metrics.record_coarse(hit=True)
                return got
        arr = builder()
        if key is not None:
            self.coarse[key] = arr
        self.metrics.record_coarse(hit=False)
        return arr

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for eng in reversed(self.engines):
            try:
                eng.close()
            except Exception:                              # noqa: BLE001
                pass
        self.engines.clear()
        self._residual_engine = None
        self.coarse.clear()
        if self._session is not None:
            try:
                self._session.close()
            except Exception:                              # noqa: BLE001
                pass
            self._session = None


_LOCK = threading.Lock()
_ACTIVE: GpuRunContext | None = None


def begin_run(mode: str | None = None) -> GpuRunContext:
    """Begin a new run context; replaces any stale context."""
    global _ACTIVE
    if mode is None:
        mode = os.environ.get("KTS_GPU_ALIGN", "0")
    mode = str(mode or "").strip().lower()
    metrics = begin_metrics(mode)
    ctx = GpuRunContext(mode=mode, metrics=metrics)
    with _LOCK:
        old, _ACTIVE = _ACTIVE, ctx
    if old is not None:
        old.close()
    return ctx


def current_run() -> GpuRunContext | None:
    with _LOCK:
        return _ACTIVE


def end_run(*, wall_s: float | None = None) -> dict[str, Any] | None:
    global _ACTIVE
    with _LOCK:
        ctx, _ACTIVE = _ACTIVE, None
    if ctx is None:
        return None
    ctx.close()
    return end_metrics(wall_s=wall_s)
