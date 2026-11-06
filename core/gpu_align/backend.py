#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU alignment architecture — backend/device management.

The package is optional: without CuPy/CUDA every engine reports unavailable
and callers keep the CPU path.  Device selection, the ASCII-path workaround for
CuPy's NVRTC, VRAM budgeting and OOM classification live here so the algorithm
modules stay pure.
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "GpuBackend", "GpuUnavailable", "GpuOutOfMemory", "gpu_available",
    "get_backend",
]


class GpuUnavailable(RuntimeError):
    """No usable CUDA device / CuPy import failed."""


class GpuOutOfMemory(RuntimeError):
    """A batch exceeded the device budget (caller may reduce batch/fallback)."""


_CUPY: Any = None
_TRIED = False
_LOCK = threading.Lock()


def _import_cupy() -> Any:
    global _CUPY, _TRIED
    if _TRIED:
        return _CUPY
    with _LOCK:
        if _TRIED:
            return _CUPY
        _TRIED = True
        # Non-ASCII user paths break NVRTC's include resolution on this dev
        # machine; the ASCII junction makes CuPy's *own* header path ASCII too.
        junc = r"C:\sp_ascii"
        try:
            if os.path.isdir(junc) and junc not in sys.path:
                sys.path.insert(0, junc)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            # This dev machine's toolkit lives at C:\CUDA12; other machines
            # normally set CUDA_PATH themselves.  Only default when present.
            if os.path.isdir(r"C:\CUDA12"):
                os.environ.setdefault("CUDA_PATH", r"C:\CUDA12")
            # NVRTC (used by CuPy RawKernel) loads nvrtc-builtins64_*.dll
            # from the CUDA bin directory.  A fresh process (e.g. the
            # channel-sync regression harness) may not inherit that PATH,
            # which previously made the window path silently fall back to
            # CPU.  Add both CUDA_PATH/bin and the dev toolkit bin.
            for _cuda_root in (os.environ.get("CUDA_PATH"),
                               r"C:\CUDA12"):
                if not _cuda_root:
                    continue
                _bin = os.path.join(_cuda_root, "bin")
                if not os.path.isdir(_bin):
                    continue
                _parts = os.environ.get("PATH", "").split(os.pathsep)
                if _bin not in _parts:
                    os.environ["PATH"] = _bin + os.pathsep + os.environ.get(
                        "PATH", "")
                try:
                    os.add_dll_directory(_bin)
                except Exception:                      # noqa: BLE001
                    pass
            import cupy as cp  # noqa: F401
            _CUPY = cp
        except Exception:                                  # noqa: BLE001
            _CUPY = None
        return _CUPY


def gpu_available() -> bool:
    cp = _import_cupy()
    if cp is None:
        return False
    try:
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:                                      # noqa: BLE001
        return False


def _free_mem_bytes(cp: Any) -> int:
    try:
        free, _total = cp.cuda.runtime.memGetInfo()
        return int(free)
    except Exception:                                      # noqa: BLE001
        return 0


@dataclass
class GpuBackend:
    """Thin, testable wrapper around a CUDA device."""

    cp: Any
    device_id: int = 0
    name: str = ""
    total_mem: int = 0
    _context: Any = None
    _closed: bool = False
    # One execution lock per device.  CuPy calls from several Python threads
    # against the same session are not deterministic; every engine takes this
    # RLock around its GPU section.  RLock keeps nested engine calls safe.
    lock: Any = field(default_factory=threading.RLock,
                      repr=False, compare=False)

    @classmethod
    def create(cls, device_id: int = 0, budget_mb: int | None = None) -> "GpuBackend":
        cp = _import_cupy()
        if cp is None:
            raise GpuUnavailable("CuPy is not importable")
        try:
            if cp.cuda.runtime.getDeviceCount() <= device_id:
                raise GpuUnavailable(f"device {device_id} not present")
            props = cp.cuda.runtime.getDeviceProperties(device_id)
            name = props["name"]
            if isinstance(name, bytes):
                name = name.decode(errors="replace")
            ctx = cp.cuda.Device(device_id)
            ctx.use()
            total = int(cp.cuda.runtime.getDeviceProperties(device_id)["totalGlobalMem"])
            b = cls(cp=cp, device_id=device_id, name=name,
                    total_mem=total, _context=ctx)
            b._budget_override = budget_mb
            atexit.register(b.release)
            return b
        except GpuUnavailable:
            raise
        except Exception as exc:                           # noqa: BLE001
            raise GpuUnavailable(f"CUDA device init failed: {exc}") from exc

    # ---- memory policy ---------------------------------------------------
    def budget_bytes(self, default_mb: int = 2048) -> int:
        env = os.environ.get("KTS_GPU_ALIGN_BUDGET_MB")
        mb = int(env) if env and env.isdigit() else default_mb
        override = getattr(self, "_budget_override", None)
        if override:
            mb = int(override)
        free = _free_mem_bytes(self.cp)
        if free <= 0:
            return mb * 1024 * 1024
        return max(64 * 1024 * 1024, min(mb * 1024 * 1024, free * 3 // 5))

    def check_budget(self, need_bytes: int) -> None:
        budget = self.budget_bytes()
        if need_bytes > budget:
            raise GpuOutOfMemory(
                f"batch needs {need_bytes / 2**20:.1f} MiB > "
                f"budget {budget / 2**20:.1f} MiB")

    def sync(self) -> None:
        try:
            self.cp.cuda.Device(self.device_id).synchronize()
        except Exception:                                  # noqa: BLE001
            pass

    def free_pool(self) -> None:
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:                                  # noqa: BLE001
            pass

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.free_pool()
        except Exception:                                  # noqa: BLE001
            pass

    def info(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "total_mem": self.total_mem,
            "free_mem": _free_mem_bytes(self.cp),
            "budget": self.budget_bytes(),
        }


_BACKEND: GpuBackend | None = None


def get_backend(device_id: int = 0) -> GpuBackend | None:
    """Return a process-wide backend (None when CUDA is unavailable)."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    if not gpu_available():
        return None
    try:
        _BACKEND = GpuBackend.create(device_id)
    except GpuUnavailable:
        return None
    return _BACKEND
