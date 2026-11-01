"""GPU-batched GCC-PHAT surfaces for frame estimation (T2.2 prototype).

Only the correlation surface (float64) is computed on the GPU; peak selection /
confidence stays in the existing CPU code (`_gcc_peak_from_surface`).  This is
the building block for the single-process batched estimator.

Not wired into the production path; `channel_sync.gpu_cc` only covers window
level correlation, frame estimation remains CPU in v0.9.0.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import fft as _fft

__all__ = ["available", "surface_windows"]


def _cp() -> Any:
    from . import gpu_cc

    return gpu_cc._import_cupy()


def available() -> bool:
    cp = _cp()
    if cp is None:
        return False
    try:
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:                                  # noqa: BLE001
        return False


def surface_windows(
    ref2d: np.ndarray,
    tgt2d: np.ndarray,
    *,
    lag0: int,
    search_lo: int,
    search_hi: int,
    rms_gate: float,
) -> tuple[np.ndarray, int, np.ndarray] | None:
    """Batched GCC-PHAT window surfaces.

    Returns ``(windows, nfft, valid)`` where windows[i] corresponds to the CPU
    ``_gcc_peak_from_surface`` window slice (m_lo..m_hi) for frame i; invalid
    (silent/too-short) rows are marked in ``valid``.
    """
    cp = _cp()
    if cp is None or ref2d.ndim != 2 or tgt2d.ndim != 2:
        return None
    B, F = int(ref2d.shape[0]), int(ref2d.shape[1])
    L = int(tgt2d.shape[1])
    if B == 0 or F == 0 or L < F or int(tgt2d.shape[0]) != B:
        return None
    m_lo = max(0, int(lag0) + int(search_lo))
    m_hi = min(L - F, int(lag0) + int(search_hi))
    if m_lo > m_hi or m_hi - m_lo + 1 < 3:
        return None
    nfft = int(_fft.next_fast_len(F + L - 1, real=True))
    try:
        with cp.cuda.Device(0):
            ref = cp.asarray(ref2d, dtype=cp.float64)
            tgt = cp.asarray(tgt2d, dtype=cp.float64)
            ref = ref - ref.mean(axis=1, keepdims=True)
            tgt = tgt - tgt.mean(axis=1, keepdims=True)
            r_ref = cp.sqrt(cp.mean(ref * ref, axis=1))
            r_tgt = cp.sqrt(cp.mean(tgt * tgt, axis=1))
            valid = (r_ref >= rms_gate) & (r_tgt >= rms_gate)
            safe_r = cp.where(valid, r_ref, 1.0)
            safe_t = cp.where(valid, r_tgt, 1.0)
            ref = ref / safe_r[:, None]
            tgt = tgt / safe_t[:, None]
            g = cp.conj(cp.fft.rfft(ref, nfft, axis=1)) * \
                cp.fft.rfft(tgt, nfft, axis=1)
            gmax = cp.max(cp.abs(g), axis=1, keepdims=True)
            g = g / (cp.abs(g) + 1e-3 * gmax + 1e-12)
            surf = cp.fft.irfft(g, nfft, axis=1)
            windows = cp.asnumpy(surf[:, m_lo:m_hi + 1])
            return windows, nfft, cp.asnumpy(valid)
    except Exception:                                  # noqa: BLE001
        return None
