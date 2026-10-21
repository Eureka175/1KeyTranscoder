#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Directly test the eight tracks my SNR classification called "no content".

The user states the premise of this set: all four channels are verified to have
**valid and correlated** audio. My earlier classification used the production
grouping metric (`sample_pair_snr`, threshold 1.3) and put 8 tracks in a
"no shared content" bucket -- which contradicts the premise. So either the metric
is conservative or my reading of it is wrong. This settles that with a direct
normalised cross-correlation: if a clear peak appears at a consistent lag, the
content is correlated and the metric, not the material, is the limitation.

For each track it reports the NCC peak (0..1) over several segments and the lag,
plus the depth of the peak against the correlation floor.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

CACHE = Path(r"E:\1kt_scratch\pcm")
OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
SEG = int(SR * 2.0)          # 2 s segments
MAX_LAG = int(SR * 0.08)     # +-80 ms


def ncc_peak(a: np.ndarray, b: np.ndarray) -> tuple[float, int]:
    """Normalised cross-correlation peak and its lag (in samples)."""
    a = a - a.mean()
    b = b - b.mean()
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0, 0
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    fa = np.fft.rfft(a, n)
    fb = np.fft.rfft(b, n)
    cc = np.fft.irfft(fa * np.conj(fb), n)
    cc = np.concatenate([cc[-MAX_LAG:], cc[:MAX_LAG + 1]])
    cc = cc / (na * nb)
    idx = int(np.argmax(np.abs(cc)))
    lag = idx - MAX_LAG
    return float(abs(cc[idx])), int(lag)


def main() -> int:
    cls = json.loads((OUT / "proof_classification.json").read_text(
        encoding="utf-8"))
    rows = cls.get("no_content") or []
    if not rows:
        print("没有 no_content 记录")
        return 0
    print(f"{'clip':<22}{'ch':<4}{'伙伴':<5}{'段数':>5}"
          f"{'NCC 峰值中位':>13}{'最大':>8}{'lag 中位':>10}{'lag 一致性':>11}")
    print("-" * 82)
    summary = []
    for r in rows:
        clip, ch, partner = r["clip"], r["ch"], r.get("partner")
        da = CACHE / clip / f"ch{(partner or 1) - 1}.f32"
        db = CACHE / clip / f"ch{ch - 1}.f32"
        if not (da.is_file() and db.is_file()):
            print(f"{clip:<22}CH{ch:<3}(缓存缺失)")
            continue
        a = np.memmap(da, dtype=np.float32, mode="r")
        b = np.memmap(db, dtype=np.float32, mode="r")
        n = min(len(a), len(b))
        starts = [int(n * f) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
        peaks, lags = [], []
        for s in starts:
            if s + SEG > n:
                continue
            p, l = ncc_peak(np.asarray(a[s:s + SEG]),
                            np.asarray(b[s:s + SEG]))
            peaks.append(p)
            lags.append(l)
        if not peaks:
            print(f"{clip:<22}CH{ch:<3}(片段太短)")
            continue
        med = float(np.median(peaks))
        mx = float(np.max(peaks))
        lag_med = int(np.median(lags))
        spread = max(lags) - min(lags)
        print(f"{clip:<22}CH{ch:<3}CH{partner:<3}{len(peaks):>5}"
              f"{med:>13.3f}{mx:>8.3f}{lag_med:>10d}{spread:>11d}")
        summary.append((clip, ch, med, mx, lag_med, spread))
    if summary:
        meds = [s[2] for s in summary]
        print(f"\n8 条轨的 NCC 峰值中位: "
              f"中位 {np.median(meds):.3f}, 最小 {min(meds):.3f}, "
              f"最大 {max(meds):.3f}")
        print("对照: 白噪声/无关信号的 NCC 峰值应接近 0 (<0.02)")
    (OUT / "proof_ncc_check.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
