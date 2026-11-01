#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""High-level session for the GPU alignment architecture (opt-in)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .backend import GpuBackend, get_backend
from .frame_gcc import FrameGccEngine
from .precision import Policy, Precision, resolve_policy
from .refine import RefineEngine
from .residual_cc import ResidualCCEngine
from .types import BatchStats
from .window_cc import WindowCCEngine

__all__ = ["AlignGpuSession", "session_from_env"]


@dataclass
class AlignGpuSession:
    backend: GpuBackend
    policy: Policy
    stats: list[BatchStats] = field(default_factory=list)
    _closed: bool = False

    @classmethod
    def create(cls, *, mode: str | None = None, device_id: int = 0,
               budget_mb: int | None = None) -> "AlignGpuSession | None":
        mode = mode if mode is not None else os.environ.get("KTS_GPU_ALIGN", "0")
        if mode.strip().lower() in ("0", "off", "false", "no", ""):
            return None
        try:
            policy = resolve_policy(mode)
        except ValueError:
            return None
        backend = get_backend(device_id)
        if backend is None:
            return None
        if budget_mb is not None:
            backend._budget_override = int(budget_mb)
        return cls(backend=backend, policy=policy)

    @property
    def available(self) -> bool:
        return not self._closed and self.backend is not None

    def window_engine(self, ref12: np.ndarray, tgt12: np.ndarray,
                      *, budget_bytes: int | None = None) -> WindowCCEngine:
        return WindowCCEngine(self.backend, ref12, tgt12,
                              policy=self.policy, budget_bytes=budget_bytes)

    def frame_engine(self) -> FrameGccEngine:
        return FrameGccEngine(self.backend, policy=self.policy)

    def residual_engine(self) -> ResidualCCEngine:
        return ResidualCCEngine(self.backend, policy=self.policy)

    def refine_engine(self) -> RefineEngine:
        return RefineEngine(self.backend, policy=self.policy)

    def drain_stats(self) -> list[dict[str, Any]]:
        out = [s.as_dict() for s in self.stats]
        self.stats.clear()
        return out

    def close(self) -> None:
        self._closed = True
        try:
            self.backend.release()
        except Exception:                                  # noqa: BLE001
            pass


def session_from_env(*, mode: str | None = None) -> AlignGpuSession | None:
    return AlignGpuSession.create(mode=mode)
