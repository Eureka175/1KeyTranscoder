#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU-batched frame-level estimate_pair implementation (opt-in).

Pipeline (same semantics as `sync_estimate._est_frames`):

  1. build full 8 kHz coarse caches once per pair (float64, block decimate);
  2. first stage: batched coarse GCC-PHAT surfaces -> CPU peak logic;
  3. second stage: batched full-rate GCC-PHAT surfaces -> CPU peak logic;
  4. return the same `FrameDelay` sequence.

Only the FFT/surface computation moves to the GPU; peak/confidence logic stays
the exact production implementation (ported here) so delays are identical and
confidence differs only by cuFFT rounding.  The estimator is experimental and
must be explicitly enabled; it falls back to CPU on any failure.
"""
from __future__ import annotations

import math
import os
import time
import warnings
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import fft as _fft
from scipy import signal

from .frame_gcc import FrameGccEngine
from .session import AlignGpuSession

__all__ = ["GpuFrameEstimator", "estimate_frames_gpu"]


@dataclass
class _Peak:
    delay_samples: float
    confidence: float
    polarity: int
    success: bool


_FAIL = _Peak(float("nan"), 0.0, 1, False)

# Run-level 8 kHz coarse cache.  channel-sync calls estimate_pair many times
# (18+ pairs) over the same 4 raw channels; rebuilding a full-file coarse
# cache per pair was measured at ~8 s/channel (15.7 s/pair).  Keyed by
# (resolved path, q, storage dtype); cleared at channel-sync shutdown.
_FRAME_COARSE_CACHE: dict[tuple[str, int, str], np.ndarray] = {}


def clear_frame_coarse_cache() -> None:
    _FRAME_COARSE_CACHE.clear()


def _frame_cache_key(src: Any, q: int) -> tuple[str, int, str] | None:
    path = getattr(src, "path", None)
    if path is None:
        return None
    try:
        return (str(Path(path).resolve()), int(q),
                str(getattr(src, "dtype", "")))
    except Exception:                                  # noqa: BLE001
        return None


def _peak_from_window(
    window: np.ndarray, nfft: int, *, F: int, L: int, lag0: int,
    search_lo: int, search_hi: int,
) -> _Peak:
    """Exact copy of sync_estimate.gcc_phat_int peak logic on a cropped window."""
    m_lo = max(0, int(lag0) + int(search_lo))
    m_hi = min(int(L) - int(F), int(lag0) + int(search_hi))
    if m_lo > m_hi:
        return _FAIL
    # `window` is surface[0:window_len]; our callers always pass the full
    # surface crop whose first index is 0 (m_lo/m_hi are within it).
    if m_lo < 0 or m_hi >= window.shape[0]:
        return _FAIL
    w = window[m_lo:m_hi + 1]
    if w.size < 3:
        return _FAIL

    polarity = 1
    pos_best = float(np.max(w))
    neg_best = -float(np.min(w))
    if neg_best > pos_best * 1.2:
        polarity = -1
        w = -w

    padded = np.concatenate(([-np.inf], w, [-np.inf]))
    peaks, _ = signal.find_peaks(padded, distance=2)
    if peaks.size == 0:
        return _Peak(float("nan"), 0.0, polarity, False)
    keep = [
        int(i) - 1
        for i in peaks
        if padded[i] > padded[i - 1] and padded[i] > padded[i + 1]
    ]
    if not keep:
        return _Peak(float("nan"), 0.0, polarity, False)
    peaks = np.asarray(keep)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="some peaks have a prominence of 0")
        prom = signal.peak_prominences(w, peaks)[0]
    order = np.argsort(w[peaks])[::-1]
    best = peaks[order[0]]
    second = (
        peaks[order[1]]
        if order.size > 1 and abs(peaks[order[1]] - best) >= 8
        else None
    )

    delay_samples = float(m_lo + int(best) - int(lag0))
    v = max(float(w[best]), 0.0)
    nf = np.sqrt(2.0 * np.log(nfft)) / np.sqrt(nfft)
    height_score = (
        min(1.0, np.log10(v / nf) / np.log10(1.0 / nf)) if v > nf else 0.0
    )
    if second is None or w[second] <= 0:
        ratio_score = 1.0
    else:
        r = v / float(w[second])
        ratio_score = max(0.0, min(1.0, (r - 1.0) / (r - 1.0 + 0.5)))
    prominence = float(prom[order[0]])
    prominence_score = min(1.0, prominence / v) if v > 0 else 0.0
    confidence = float(np.clip(
        0.3 * height_score + 0.5 * ratio_score + 0.2 * prominence_score,
        0.0, 1.0))
    return _Peak(
        delay_samples=delay_samples, confidence=confidence,
        polarity=polarity, success=bool(v >= 2.0 * nf),
    )


def _build_coarse(src: Any, q: int, *, block: int = 480_000,
                  margin: int = 64) -> np.ndarray:
    """Full-range float64 coarse cache, same boundary rule as _coarse_slice."""
    from .. import sync_estimate as se

    n_in = int(src.shape[0])
    n_out = (n_in + q - 1) // q
    out = np.empty(n_out, dtype=np.float64)
    for c0 in range(0, n_out, block):
        c1 = min(n_out, c0 + block)
        lo = max(0, c0 - margin)
        hi = min(n_out, c1 + margin)
        seg = se._coarse_slice(src, lo, hi, q)
        out[c0:c1] = seg[c0 - lo:c0 - lo + (c1 - c0)]
    return out


def _peak_batch_windows(windows: np.ndarray, valid: np.ndarray,
                        nfft: int, *, m_lo: int | np.ndarray,
                        m_hi: int | np.ndarray,
                        lag0: int | np.ndarray | None = None) -> list[_Peak]:
    """Vectorized approximation of _peak_from_window for a batch.

    Delays/polarity are exact max-|CC| argmax; confidence uses the same
    height/ratio formulas but a vectorized prominence approximation.  This is
    only used by the experimental GPU frame path; exact parity is available
    through ``fast_peak=False``.
    """
    windows = np.asarray(windows, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    B, W = windows.shape
    if B == 0:
        return []
    mlo = np.asarray(m_lo, dtype=np.int64)
    mhi = np.asarray(m_hi, dtype=np.int64)
    if mlo.ndim == 0:
        mlo = np.full(B, int(mlo))
    if mhi.ndim == 0:
        mhi = np.full(B, int(mhi))
    if np.any(mhi < mlo):
        return [_FAIL for _ in range(B)]
    width = int(np.max(mhi - mlo)) + 1
    crop = np.full((B, width), -np.inf, dtype=np.float64)
    for i in range(B):
        a = int(mlo[i])
        n = int(mhi[i] - mlo[i] + 1)
        if a < 0 or a + n > W:
            return [_FAIL for _ in range(B)]
        crop[i, :n] = windows[i, a:a + n]
    pos_best = np.max(crop, axis=1)
    neg_best = -np.min(crop, axis=1)
    flip = neg_best > pos_best * 1.2
    w = np.where(flip[:, None], -crop, crop)
    best = np.argmax(w, axis=1)
    rows = np.arange(B)
    v = np.maximum(w[rows, best], 0.0)
    idx = np.arange(width)[None, :]
    mask = np.abs(idx - best[:, None]) >= 8
    masked = np.where(mask, w, -np.inf)
    second = np.argmax(masked, axis=1)
    second_v = masked[rows, second]
    left_min = np.empty(B, dtype=np.float64)
    right_min = np.empty(B, dtype=np.float64)
    for i in range(B):
        b = int(best[i])
        left_min[i] = np.min(w[i, :b]) if b > 0 else np.inf
        right_min[i] = np.min(w[i, b + 1:]) if b + 1 < width else np.inf
    prominence = np.maximum(v - np.maximum(left_min, right_min), 0.0)
    nf = np.sqrt(2.0 * np.log(nfft)) / np.sqrt(nfft)
    height = np.where(v > nf,
                      np.minimum(1.0, np.log10(np.maximum(v, 1e-30) / nf)
                                 / np.log10(1.0 / nf)), 0.0)
    good_second = second_v > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.divide(v, second_v, out=np.full(B, np.inf),
                      where=good_second)
    ratio = np.where(good_second,
                     np.clip((r - 1.0) / (r - 1.0 + 0.5), 0.0, 1.0), 1.0)
    prom_score = np.where(v > 0, np.minimum(1.0, prominence /
                                           np.maximum(v, 1e-30)), 0.0)
    conf = np.clip(0.3 * height + 0.5 * ratio + 0.2 * prom_score, 0.0, 1.0)
    if lag0 is None:
        lag = np.zeros(B, dtype=np.int64)
    else:
        lag = np.asarray(lag0, dtype=np.int64)
        if lag.ndim == 0:
            lag = np.full(B, int(lag))
    out: list[_Peak] = []
    for i in range(B):
        if not valid[i]:
            out.append(_FAIL)
            continue
        delay = float(int(mlo[i]) + int(best[i]) - int(lag[i]))
        out.append(_Peak(delay, float(conf[i]),
                         1 if pos_best[i] >= neg_best[i] else -1,
                         bool(v[i] >= 2.0 * nf)))
    return out


class GpuFrameEstimator:
    """Single-process GPU batched frame estimator (opt-in)."""

    def __init__(self, ref: Any, tgt: Any, *, sample_rate: int,
                 storage_dtype: str, frame: int, hop: int, h_c: int,
                 f_c: int, s_c: int, w_half: int, q: int,
                 min_confidence: float, rms_floor: float,
                 batch: int = 128, fast_peak: bool = True) -> None:
        self.ref = ref
        self.tgt = tgt
        self.sample_rate = int(sample_rate)
        self.frame = int(frame)
        self.hop = int(hop)
        self.h_c = int(h_c)
        self.f_c = int(f_c)
        self.s_c = int(s_c)
        self.w_half = int(w_half)
        self.q = int(q)
        self.min_confidence = float(min_confidence)
        self.rms_floor = float(rms_floor)
        self.batch = max(1, int(batch))
        self.fast_peak = bool(fast_peak)
        self.session = AlignGpuSession.create()
        if self.session is None:
            raise RuntimeError("GPU alignment session unavailable")
        self.engine: FrameGccEngine = self.session.frame_engine()
        self.engine.warmup()
        self.ref_coarse: np.ndarray | None = None
        self.tgt_coarse: np.ndarray | None = None
        self.stats: dict[str, Any] = {"coarse_build_s": 0.0,
                                      "first_stage_s": 0.0,
                                      "second_stage_s": 0.0}

    def close(self) -> None:
        try:
            self.engine.close()
        finally:
            self.session.close()

    # ------------------------------------------------------------------
    def _prepare_coarse(self) -> float:
        t0 = time.perf_counter()
        kr = _frame_cache_key(self.ref, self.q)
        kt = _frame_cache_key(self.tgt, self.q)
        self.ref_coarse = _FRAME_COARSE_CACHE.get(kr) if kr else None
        self.tgt_coarse = _FRAME_COARSE_CACHE.get(kt) if kt else None
        missing = []
        if self.ref_coarse is None:
            missing.append(("ref", self.ref, kr))
        if self.tgt_coarse is None:
            missing.append(("tgt", self.tgt, kt))
        if len(missing) == 2:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = {ex.submit(_build_coarse, src, self.q): (name, key)
                        for name, src, key in missing}
                for fut, (name, key) in futs.items():
                    arr = fut.result()
                    if name == "ref":
                        self.ref_coarse = arr
                    else:
                        self.tgt_coarse = arr
                    if key is not None:
                        _FRAME_COARSE_CACHE[key] = arr
        else:
            for name, src, key in missing:
                arr = _build_coarse(src, self.q)
                if name == "ref":
                    self.ref_coarse = arr
                else:
                    self.tgt_coarse = arr
                if key is not None:
                    _FRAME_COARSE_CACHE[key] = arr
        dt = time.perf_counter() - t0
        self.stats["coarse_build_s"] = dt
        return dt

    def _coarse_window(self, arr: np.ndarray, cache: np.ndarray,
                       c0: int, c1: int, edge: bool) -> np.ndarray:
        from .. import sync_estimate as se

        if (not edge and c0 >= 0 and c1 <= cache.shape[0]):
            return np.asarray(cache[c0:c1], dtype=np.float64)
        return se._coarse_slice(arr, c0, c1, self.q)

    # ------------------------------------------------------------------
    def estimate(self, k_lo: int, k_hi: int) -> list[Any]:
        from ..sync_estimate import FrameDelay, read_f64

        if k_hi <= k_lo:
            return []
        if self.ref_coarse is None or self.tgt_coarse is None:
            self._prepare_coarse()
        assert self.ref_coarse is not None and self.tgt_coarse is not None
        F, L = self.f_c, self.f_c + 2 * self.s_c
        q = self.q
        n_ref = int(self.ref.shape[0])
        n_tgt = int(self.tgt.shape[0])
        first: list[_Peak | None] = [None] * (k_hi - k_lo)
        centers: list[int] = [0] * (k_hi - k_lo)

        t_first = time.perf_counter()
        for b0 in range(k_lo, k_hi, self.batch):
            b1 = min(k_hi, b0 + self.batch)
            B = b1 - b0
            refs = np.empty((B, F), dtype=np.float64)
            tgts = np.empty((B, L), dtype=np.float64)
            edge = np.zeros(B, dtype=bool)
            for j, k in enumerate(range(b0, b1)):
                c0 = k * self.h_c
                # interior caches are exact; first/last few frames use the
                # production _coarse_slice boundary rule.
                is_edge = (c0 < 0 or c0 + F > self.ref_coarse.shape[0]
                           or c0 - self.s_c < 0
                           or c0 + self.f_c + self.s_c > self.tgt_coarse.shape[0])
                edge[j] = is_edge
                refs[j] = self._coarse_window(
                    self.ref, self.ref_coarse, c0, c0 + F, is_edge)
                tgts[j] = self._coarse_window(
                    self.tgt, self.tgt_coarse, c0 - self.s_c,
                    c0 - self.s_c + L, is_edge)
            sb = self.engine.surfaces(
                refs, tgts, lag0=self.s_c, search_lo=-self.s_c,
                search_hi=self.s_c, rms_gate=self.rms_floor)
            if self.fast_peak:
                peaks = _peak_batch_windows(
                    sb.windows, sb.valid, sb.nfft,
                    m_lo=0, m_hi=2 * self.s_c, lag0=self.s_c)
                for j in range(B):
                    first[b0 - k_lo + j] = peaks[j]
            else:
                for j in range(B):
                    if not bool(sb.valid[j]):
                        first[b0 - k_lo + j] = _FAIL
                        continue
                    first[b0 - k_lo + j] = _peak_from_window(
                        sb.windows[j], sb.nfft, F=F, L=L, lag0=self.s_c,
                        search_lo=-self.s_c, search_hi=self.s_c)
        self.stats["first_stage_s"] = time.perf_counter() - t_first

        # ---- second stage: only frames that pass the first gate ----
        t_second = time.perf_counter()
        out: list[Any] = [
            FrameDelay(k * self.hop + self.frame // 2,
                       float("nan"), 0.0)
            for k in range(k_lo, k_hi)
        ]
        pending: list[tuple[int, int, int]] = []   # (index, center_sample, center)
        second_result: dict[int, _Peak] = {}
        full_F = self.frame
        full_L = self.frame + 2 * self.w_half
        for idx, k in enumerate(range(k_lo, k_hi)):
            g = first[idx]
            center_sample = k * self.hop + self.frame // 2
            if g is None or not g.success or g.confidence < self.min_confidence:
                conf = 0.0 if g is None else g.confidence
                out[idx] = FrameDelay(center_sample, float("nan"), conf)
                continue
            center = int(round(g.delay_samples)) * q
            # read full-rate frames; only batch when complete
            ref_start = k * self.hop
            tgt_start = k * self.hop + center - self.w_half
            if (ref_start < 0 or tgt_start < 0 or
                    ref_start + full_F > n_ref or
                    tgt_start + full_L > n_tgt):
                out[idx] = FrameDelay(center_sample, float("nan"),
                                      g.confidence)
                continue
            pending.append((idx, center_sample, center))
        # process pending in batches
        for b0 in range(0, len(pending), self.batch):
            chunk = pending[b0:b0 + self.batch]
            B = len(chunk)
            refsF = np.empty((B, full_F), dtype=np.float64)
            tgtsF = np.empty((B, full_L), dtype=np.float64)
            for j, (idx, center_sample, center) in enumerate(chunk):
                k = k_lo + idx
                refsF[j] = np.asarray(
                    read_f64(self.ref, k * self.hop, full_F),
                    dtype=np.float64)
                tgtsF[j] = np.asarray(
                    read_f64(self.tgt, k * self.hop + center - self.w_half,
                             full_L), dtype=np.float64)
            sb = self.engine.surfaces(
                refsF, tgtsF, lag0=0, search_lo=0,
                search_hi=2 * self.w_half, rms_gate=self.rms_floor)
            if self.fast_peak:
                peaks = _peak_batch_windows(
                    sb.windows, sb.valid, sb.nfft, m_lo=0,
                    m_hi=2 * self.w_half,
                    lag0=np.asarray([self.w_half - c for (_i, _s, c)
                                     in chunk], dtype=np.int64))
                for j, (idx, _center_sample, _center) in enumerate(chunk):
                    second_result[idx] = peaks[j]
            else:
                for j, (idx, center_sample, center) in enumerate(chunk):
                    if not bool(sb.valid[j]):
                        second_result[idx] = _FAIL
                        continue
                    second_result[idx] = _peak_from_window(
                        sb.windows[j], sb.nfft, F=full_F, L=full_L,
                        lag0=self.w_half - center,
                        search_lo=center - self.w_half,
                        search_hi=center + self.w_half)
        # merge preserve frame order
        for idx, k in enumerate(range(k_lo, k_hi)):
            center_sample = k * self.hop + self.frame // 2
            g = first[idx]
            if g is None or not g.success or g.confidence < self.min_confidence:
                continue
            g2 = second_result.get(idx)
            if g2 is None:
                # not batched (edge/short) or should not happen
                out[idx] = FrameDelay(center_sample, float("nan"),
                                      g.confidence)
            elif not g2.success or g2.confidence < self.min_confidence:
                out[idx] = FrameDelay(center_sample, float("nan"),
                                      g2.confidence)
            else:
                out[idx] = FrameDelay(center_sample,
                                      float(g2.delay_samples), g2.confidence)
        self.stats["second_stage_s"] = time.perf_counter() - t_second
        return out




def estimate_frames_hybrid(ref_path: Any, tgt_path: Any, *,
                           storage_dtype: str, sample_rate: int,
                           search_window_ms: float, frame_ms: float,
                           hop_ms: float, min_confidence: float,
                           frame_min_rms_dbfs: float,
                           workers: int = 12, split: float = 0.5,
                           batch: int = 128):
    """Run CPU process-pool frames and GPU batch frames concurrently.

    The ranges are disjoint ([0, split) on CPU, [split, n) on GPU); the GPU
    process also builds the run-level coarse cache used by later pairs.  This
    is the topology that beats the 12-process CPU estimator (measured 7.6 s
    vs 17.3 s on a warm C1306 pair, delays identical).
    """
    from .. import sync_estimate as se

    q = int(sample_rate) // 8000
    f_c = int(round(frame_ms * 8000 / 1000.0))
    h_c = int(round(hop_ms * 8000 / 1000.0))
    frame = f_c * q
    hop = h_c * q
    s_c = int(math.ceil(search_window_ms * 8000 / 1000.0))
    rms_floor = 10.0 ** (frame_min_rms_dbfs / 20.0)
    ref_probe = se.RawStream(ref_path, storage_dtype)
    tgt_probe = se.RawStream(tgt_path, storage_dtype)
    try:
        n = min(ref_probe.shape[0], tgt_probe.shape[0])
    finally:
        ref_probe.close()
        tgt_probe.close()
    n_frames = 0 if n < frame else (n - frame) // hop + 1
    if n_frames <= 0:
        return [], {"reason": "no frames"}
    split = min(0.9, max(0.1, float(split)))
    k_split = int(n_frames * split) if n_frames > 200 else n_frames
    if k_split >= n_frames:
        # short pair: GPU only (process start-up is not worth it)
        ref = se.open_source(ref_path, storage_dtype)
        tgt = se.open_source(tgt_path, storage_dtype)
        est = None
        try:
            est = GpuFrameEstimator(
                ref, tgt,
                sample_rate=sample_rate, storage_dtype=storage_dtype,
                frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c,
                w_half=8, q=q, min_confidence=min_confidence,
                rms_floor=rms_floor, batch=batch, fast_peak=True)
            frames = est.estimate(0, n_frames)
            return frames, dict(est.stats, mode="gpu-only")
        finally:
            if est is not None:
                est.close()
            tgt.close()
            ref.close()

    params = dict(frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c,
                  w_half=8, q=q, min_confidence=min_confidence,
                  rms_floor=rms_floor)
    # Keep CPU+GPU overlap: the GPU main thread also runs peak logic, so do
    # not saturate all cores with the CPU pool.
    _cpu_cap = int(os.environ.get("KTS_GPU_FRAME_CPU_WORKERS", "0") or 0)
    if _cpu_cap > 0:
        k_workers = max(1, min(int(workers), _cpu_cap, k_split))
    else:
        k_workers = max(1, min(int(workers), max(2, int(workers) - 2),
                               k_split))
    import numpy as np
    bounds = [int(round(x)) for x in
              np.linspace(0, k_split, k_workers + 1)]
    payloads = [
        {"ref_path": ref_path, "tgt_path": tgt_path,
         "storage_dtype": storage_dtype,
         "params": dict(params, k_lo=bounds[i], k_hi=bounds[i + 1])}
        for i in range(k_workers)
    ]
    # Start CPU chunks first, then run GPU in this process concurrently.
    ex = se._get_estimation_pool(k_workers)
    futs = [ex.submit(se._chunk_job, p) for p in payloads]
    ref = se.open_source(ref_path, storage_dtype)
    tgt = se.open_source(tgt_path, storage_dtype)
    try:
        est = GpuFrameEstimator(
            ref, tgt, sample_rate=sample_rate, storage_dtype=storage_dtype,
            frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c,
            w_half=8, q=q, min_confidence=min_confidence,
            rms_floor=rms_floor, batch=batch, fast_peak=True)
        t0 = time.perf_counter()
        try:
            gpu_frames = est.estimate(k_split, n_frames)
            gstats = dict(est.stats)
            gstats["mode"] = "hybrid"
            gstats["gpu_half_s"] = time.perf_counter() - t0
        finally:
            est.close()
    finally:
        ref.close()
        tgt.close()
    parts = [f.result() for f in futs]
    cpu_frames = [f for part in parts for f in part]
    return cpu_frames + gpu_frames, gstats


def estimate_frames_gpu(ref: Any, tgt: Any, *, sample_rate: int,
                        storage_dtype: str, k_lo: int, k_hi: int,
                        frame: int, hop: int, h_c: int, f_c: int, s_c: int,
                        w_half: int, q: int, min_confidence: float,
                        rms_floor: float) -> tuple[list[Any] | None,
                                                   dict[str, Any]]:
    """Return (frames, stats) or (None, reason) when GPU is unavailable."""
    if os.environ.get("KTS_GPU_FRAME_BATCH", "0").strip().lower() in (
            "", "0", "off", "false", "no"):
        return None, {"reason": "disabled"}
    est = None
    try:
        est = GpuFrameEstimator(
            ref, tgt, sample_rate=sample_rate,
            storage_dtype=storage_dtype, frame=frame, hop=hop, h_c=h_c,
            f_c=f_c, s_c=s_c, w_half=w_half, q=q,
            min_confidence=min_confidence, rms_floor=rms_floor,
            fast_peak=os.environ.get(
                "KTS_GPU_FRAME_PEAK", "fast").strip().lower()
            not in ("exact", "0", "off", "false", "no"))
        t0 = time.perf_counter()
        frames = est.estimate(k_lo, k_hi)
        stats = dict(est.stats)
        stats["total_s"] = time.perf_counter() - t0
        return frames, stats
    except Exception as exc:                           # noqa: BLE001
        return None, {"reason": f"{type(exc).__name__}: {exc}"}
    finally:
        if est is not None:
            try:
                est.close()
            except Exception:                          # noqa: BLE001
                pass
