#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C1197 CH2 materiality check: is its non-constant offset acceptable?

C1197 is now the only remaining short proof clip that needs more than a correct
reference (C1162 is scrap, C1180/C1202 are scrap). Its CH2 measures 6 usable
frames against CH3 at 1 s frames with a non-constant offset, so the materiality
criterion decides whether a single shift is justified:

    |delay|  vs  k * measured displacement

If the correction removes at least k times the variation it leaves behind, a single
shift is strictly better than doing nothing.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import core.channel_sync as cs                     # noqa: E402
from core import sync_estimate as se               # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
CACHE = Path(r"E:\1kt_scratch\pcm")
WORK = Path(r"E:\1kt_scratch\c1197")
SR = 48000
CLIP = "20260904_C1197"


def main() -> int:
    eff = cs.effective_opts(None)
    raw = []
    d = WORK
    d.mkdir(parents=True, exist_ok=True)
    for idx in range(4):
        dst = d / f"ch{idx}.f32"
        cached = CACHE / CLIP / f"ch{idx}.f32"
        if not dst.is_file():
            if cached.is_file():
                try:
                    os.link(cached, dst)
                except OSError:
                    shutil.copyfile(cached, dst)
            else:
                cs._decode_stream(FFMPEG, PROOF / f"{CLIP}.MP4", idx, SR,
                                  dst, "f32")
        raw.append(dst)
    for frame_ms, hop_ms in [(200.0, 100.0), (500.0, 250.0), (1000.0, 500.0)]:
        for ref, tgt in [(2, 1), (3, 1), (2, 0), (3, 0)]:
            est = se.estimate_pair(
                str(raw[ref]), str(raw[tgt]), sample_rate=SR,
                search_window_ms=eff["search_window_ms"],
                frame_ms=frame_ms, hop_ms=hop_ms,
                anchor_segment_seconds=20.0,
                min_confidence=eff["min_confidence"],
                coarse_rate=eff["coarse_rate"],
                fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                min_usable_frames=eff["min_usable_frames"],
                frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                ref_index=ref, tgt_index=tgt, workers=2)
            if est.usable_frames < 3 or not est.frames:
                continue
            stats = se.summarize_trajectory(est.frames, SR)
            disp = se.measured_displacement_ms(est.frames, SR)
            const = se.classify_constant(
                stats, mad_max_ms=eff["mad_max_ms"],
                max_ppm=eff["constant_max_ppm"], sample_rate=SR,
                drift_min_ms=eff["drift_min_ms"], displacement_ms=disp)
            ratio = abs(est.delay_ms) / disp if disp else float("inf")
            print(f"  帧 {frame_ms:>4.0f}ms  CH{ref + 1}->CH{tgt + 1}: "
                  f"{est.usable_frames:>3} 帧  延迟 {est.delay_ms:+.2f}ms  "
                  f"位移 {disp:.2f}ms  比值 {ratio:6.1f}x  "
                  f"恒定={bool(const)}  MAD "
                  f"{stats.mad_ms:.3f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
