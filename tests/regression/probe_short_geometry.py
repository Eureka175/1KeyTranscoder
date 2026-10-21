#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why do 6-12 s clips yield zero usable frames -- geometry, or correlation?

For the short clips that fail (`C1180` 7.5 s, `C1202` 6.0 s, `C1162` 12.0 s) this
reports, from the decode cache:

  1. the six pairwise grouping SNRs (is there anything to correlate at all), and
  2. for the anchor/target pair that matters, how the frame yield changes when
     the geometry is scaled down: the production 200 ms frame / 100 ms hop with a
     20 s anchor segment is longer than these clips, which can only collapse the
     coverage.

If yield recovers with a smaller geometry, the fix is adaptation; if the pairwise
SNR is near 1, no geometry will help.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.channel_sync import effective_opts  # noqa: E402
from core.sync_estimate import (  # noqa: E402
    estimate_pair,
    open_source,
    sample_pair_snr,
)

CACHE = Path(r"E:\1kt_scratch\pcm")
SR = 48000
CLIPS = ["20260904_C1180", "20260904_C1202", "20260903_C1162"]

GEOMETRIES = [
    # (frame_ms, hop_ms, anchor_segment_seconds, label)
    (200.0, 100.0, 20.0, "生产默认"),
    (200.0, 100.0, 2.0, "仅缩锚段(2s)"),
    (400.0, 50.0, 2.0, "帧400/跳50/锚2s"),
    (400.0, 100.0, 1.0, "帧400/跳100/锚1s"),
    (800.0, 200.0, 1.0, "帧800/跳200/锚1s"),
]


def main() -> int:
    eff = effective_opts(None)
    for clip in CLIPS:
        d = CACHE / clip
        if not d.is_dir():
            print(f"{clip}: 无缓存")
            continue
        paths = sorted(d.glob("ch*.f32"))
        print(f"\n=== {clip}  {len(paths)} 通道")
        arrs = [open_source(p, "f32") for p in paths]
        try:
            n = min(a.shape[0] for a in arrs)
            print(f"  时长 {n / SR:.2f}s")
            # 1. 两两建组 SNR
            print("  两两 SNR:", end=" ")
            for i in range(len(arrs)):
                for j in range(i + 1, len(arrs)):
                    s = sample_pair_snr(
                        arrs[i], arrs[j], sample_rate=SR,
                        frame_ms=eff["frame_ms"], hop_ms=eff["hop_ms"],
                        search_window_ms=eff["search_window_ms"],
                        coarse_rate=eff["coarse_rate"],
                        frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                        samples=eff["group_snr_samples"])
                    print(f"{i + 1}-{j + 1}:{s.get('median', float('nan')):.2f}",
                          end="  ")
            print()
            # 2. 几何扫描: 以 CH3 为参考(生产锚点候选顺序的首选可用项)
            ref = 2 if len(arrs) > 2 else 0
            print(f"  几何扫描 (参考 CH{ref + 1}):")
            for tgt in range(len(arrs)):
                if tgt == ref:
                    continue
                line = f"    CH{tgt + 1}: "
                for frame_ms, hop_ms, anchor_s, label in GEOMETRIES:
                    try:
                        e = estimate_pair(
                            arrs[ref], arrs[tgt], sample_rate=SR,
                            search_window_ms=eff["search_window_ms"],
                            frame_ms=frame_ms, hop_ms=hop_ms,
                            anchor_segment_seconds=anchor_s,
                            min_confidence=eff["min_confidence"],
                            coarse_rate=eff["coarse_rate"],
                            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                            min_usable_frames=eff["min_usable_frames"],
                            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                            ref_index=ref, tgt_index=tgt)
                        line += (f"{label}={e.usable_frames}"
                                 f"({e.delay_ms:+.2f}ms)  "
                                 if e.usable_frames else f"{label}=0  ")
                    except Exception as exc:      # noqa: BLE001
                        line += f"{label}=ERR({type(exc).__name__})  "
                print(line)
        finally:
            for a in arrs:
                c = getattr(a, "close", None)
                if callable(c):
                    c()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
