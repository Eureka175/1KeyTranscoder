#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CUDA/CPU benchmark for the optional GPU audio-alignment architecture.

Development benchmark, not part of full_autotest.  It measures:
  1. batched window coarse-CC (surface API) against the CPU reference;
  2. batched frame GCC-PHAT surfaces against the CPU loop.

Usage:
  python tests/benchmark_gpu_align.py --seconds 300 --windows 20 --frames 64
  python tests/benchmark_gpu_align.py --json K:\\...\\gpu_bench.json

Real decoded channels default to work/long_take/accept/src_audio; the script
always opens them read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CUDA_PATH", r"C:\CUDA12")

from core import sync_estimate as se  # noqa: E402
from core.gpu_align import AlignGpuSession, WindowCCEngine  # noqa: E402
from core.gpu_align.parity import compare_window_peaks  # noqa: E402


def _load(path, seconds):
    a = np.memmap(path, dtype=np.float32, mode="r")
    return np.asarray(a[: min(a.shape[0], int(seconds * 48000))])


def _timed(fn, repeat=3):
    fn()
    t0 = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - t0) / repeat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir",
                    default=str(ROOT / "work/long_take/accept/src_audio"))
    ap.add_argument("--ref", default="c1306_src0.f32")
    ap.add_argument("--tgt", default="c1306_src1.f32")
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--windows", type=int, default=20)
    ap.add_argument("--frames", type=int, default=64)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    d = Path(args.data_dir)
    a = _load(d / args.ref, args.seconds)
    b = _load(d / args.tgt, args.seconds)
    out = {"seconds": args.seconds, "windows": args.windows,
           "frames": args.frames}

    # ---- window coarse CC (12k) ----
    q = 4
    a12 = se.coarse_decimate(a, sample_rate=48000, coarse_rate=12000)
    b12 = se.coarse_decimate(b, sample_rate=48000, coarse_rate=12000)
    count = 60 * 12000
    starts = [(i * 5 * 12000, count) for i in range(args.windows)]
    lag = 3000
    cpu = [se._coarse_cc_peak(a12, b12, s, c, -lag, lag, q)
           for s, c in starts]
    cpu_t = _timed(lambda: [se._coarse_cc_peak(a12, b12, s, c, -lag, lag, q)
                            for s, c in starts], repeat=1)
    out["window_cpu_s"] = cpu_t
    for mode in ("fp64", "fp32"):
        sess = AlignGpuSession.create(mode=mode)
        if sess is None:
            out[mode] = {"available": False}
            continue
        eng = WindowCCEngine(sess.backend, a12, b12, policy=sess.policy)
        eng.warmup()
        t0 = time.perf_counter()
        w, m_lo, m_hi, valid = eng.surface_windows(
            starts, lag_lo_c=-lag // q, lag_hi_c=lag // q)
        dt = time.perf_counter() - t0
        gpu = eng.peaks_from_windows(
            w, valid, m_lo=m_lo, lag_lo_c=-lag // q, lag_hi_c=lag // q)
        gpu = [None if p is None else (p.lag * q, p.signed_cc, p.abs_cc)
               for p in gpu]
        parity = compare_window_peaks(
            cpu, gpu, cc_tol=1e-9 if mode == "fp64" else 1e-5)
        out[mode] = {"wall_s": dt, "speedup": cpu_t / dt,
                     "parity": dict(parity),
                     "gpu_stats": [s.as_dict() for s in eng.stats]}
        eng.close()
        sess.close()

    # ---- frame GCC-PHAT surfaces (8k coarse) ----
    a8 = se.coarse_decimate(a, sample_rate=48000, coarse_rate=8000)
    b8 = se.coarse_decimate(b, sample_rate=48000, coarse_rate=8000)
    f_c, h_c, s_c = 1600, 800, 640
    B = args.frames

    def _take(arr, lo, n):
        o = np.zeros(n, dtype=arr.dtype)
        x = max(0, lo)
        y = min(arr.size, lo + n)
        if y > x:
            o[x - lo:x - lo + (y - x)] = arr[x:y]
        return o

    refs = np.stack([_take(a8, i * h_c, f_c) for i in range(B)])
    tgts = np.stack([_take(b8, i * h_c - s_c, f_c + 2 * s_c)
                     for i in range(B)])
    rms = 10.0 ** (-50.0 / 20.0)
    t0 = time.perf_counter()
    cpu_frames = [se.gcc_phat_int(refs[i], tgts[i], lag0=s_c,
                                  search_lo=-s_c, search_hi=s_c,
                                  rms_gate=rms) for i in range(B)]
    cpu_ft = time.perf_counter() - t0
    out["frame_cpu_s"] = cpu_ft
    for mode in ("fp64", "fp32"):
        sess = AlignGpuSession.create(mode=mode)
        if sess is None:
            continue
        eng = sess.frame_engine()
        eng.warmup()
        t0 = time.perf_counter()
        sb = eng.surfaces(refs, tgts, lag0=s_c, search_lo=-s_c,
                          search_hi=s_c, rms_gate=rms)
        dt = time.perf_counter() - t0
        lags = [sb.m_lo + int(np.argmax(np.abs(sb.windows[i]))) - s_c
                for i in range(B)]
        cpu_lags = [float(g.delay_samples) for g in cpu_frames]
        out[mode + "_frame"] = {
            "wall_s": dt, "speedup": cpu_ft / dt,
            "nfft": sb.nfft, "lag_equal": sum(
                1 for x, y in zip(cpu_lags, lags) if x == y),
            "frames": B,
            "gpu_stats": [s.as_dict() for s in eng.stats],
        }
        eng.close()
        sess.close()

    print(json.dumps(out, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
