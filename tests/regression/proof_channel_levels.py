#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify per-channel signal presence on the 4ch-proof originals, from cache.

The claim under test is "all four channels carry valid audio". That is a cheap
question once the channels are decoded, and `E:\\1kt_scratch\\pcm` already holds
one f32 file per (clip, channel) from earlier runs -- same stem, same audio.

For each channel this reports level (RMS dBFS), the fraction of 200 ms frames
below the estimator's own silence floor (-50 dBFS), and the peak. A channel that
is genuinely valid should be well above the floor in most frames; if instead a
refused track shows healthy levels everywhere, then the refusal cannot be blamed
on missing audio data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

CACHE = Path(r"E:\1kt_scratch\pcm")
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
SR = 48000
FRAME = int(SR * 0.2)
FLOOR = -50.0


def channel_stats(p: Path) -> dict | None:
    x = np.memmap(p, dtype=np.float32, mode="r")
    n = len(x)
    if n < FRAME:
        return None
    k = n // FRAME
    hop = FRAME
    quiet = 0
    tot = 0.0
    peak = 0.0
    for i in range(k):
        seg = np.asarray(x[i * hop:(i + 1) * hop]).astype(np.float64)
        rms = float(np.sqrt(np.mean(seg ** 2)))
        tot += rms
        quiet += 1 if rms < 10 ** (FLOOR / 20.0) else 0
        m = float(np.max(np.abs(seg)))
        peak = max(peak, m)
    mean_rms = tot / k
    return {"seconds": n / SR,
            "rms_dbfs": 20 * np.log10(max(mean_rms, 1e-9)),
            "quiet_frac": quiet / k,
            "peak_dbfs": 20 * np.log10(max(peak, 1e-9))}


def main() -> int:
    names = sorted(p.stem for p in PROOF.glob("*.MP4"))
    if len(sys.argv) > 1:
        names = [n for n in names if any(a in n for a in sys.argv[1:])]
    print(f"{'clip':<22}{'ch':<4}{'s':>7}{'RMS dBFS':>10}"
          f"{'静音帧%':>9}{'峰值 dBFS':>11}")
    print("-" * 63)
    bad = []
    for nm in names:
        d = CACHE / nm
        if not d.is_dir():
            print(f"{nm:<22}(无缓存)")
            continue
        for ch in sorted(d.glob("ch*.f32")):
            st = channel_stats(ch)
            if st is None:
                continue
            flag = ""
            if st["quiet_frac"] > 0.5:
                flag = "  <- 静音>50%"
                bad.append(f"{nm} {ch.name}")
            print(f"{nm:<22}{ch.stem:<4}{st['seconds']:>7.1f}"
                  f"{st['rms_dbfs']:>10.1f}{100 * st['quiet_frac']:>8.0f}%"
                  f"{st['peak_dbfs']:>11.1f}{flag}")
    print(f"\n静音占比 >50% 的通道: {len(bad)}")
    for b in bad:
        print("  ", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
