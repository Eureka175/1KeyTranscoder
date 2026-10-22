#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Can every non-data-lacking clip in the proof set be aligned?

The long-take run showed the bottleneck is **which channel you measure against**,
not the material: the natural groups are CH1-CH2 (NCC ~0.98) and CH3-CH4 (~0.8),
and measuring a weak channel against the production anchor (CH3) can yield zero
usable frames while its own partner yields hundreds.

This measures all 12 ordered channel pairs on the clips that still fail, at two
geometries, and then asks the question that actually matters for a fix: is the
"measurable pair" graph connected from the anchor to every track? A connected
graph means every target has a measurement path, so the clip can be aligned by
chaining (measure CH1 against CH2, CH2 against CH3, ...).

Edges are accepted with the production bar: usable_frames >= min_usable_frames.
No shift is applied here -- this is a coverage/decision experiment, not a repair.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import core.channel_sync as cs                       # noqa: E402
from core import sync_estimate as se                 # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
CACHE = Path(r"E:\1kt_scratch\pcm")
WORK = Path(r"E:\1kt_scratch\pairs")
SR = 48000
OUT = REPO / "work" / "regression_r087" / "out"

CLIPS = ["20260904_C1180", "20260904_C1202", "20260903_C1162",
         "20260904_C1197", "20260904_C1211",
         # 对照: 已知缺数据的
         "20260903_C1164", "20260903_C1166", "20260903_C1170"]
GEOMS = [(200.0, 100.0, 20.0, "prod200"), (1000.0, 500.0, 20.0, "coarse1000")]
PAIRS = [(i, j) for i in range(4) for j in range(4) if i != j]


def decode(clip: Path) -> list[Path]:
    d = WORK / clip.stem
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for idx in range(4):
        dst = d / f"ch{idx}.f32"
        if dst.is_file() and dst.stat().st_size > 0:
            out.append(dst)
            continue
        cached = CACHE / clip.stem / f"ch{idx}.f32"
        if cached.is_file() and cached.stat().st_size > 0:
            try:
                import os
                os.link(cached, dst)
            except OSError:
                shutil.copyfile(cached, dst)
            out.append(dst)
            continue
        ok, err = cs._decode_stream(FFMPEG, clip, idx, SR, dst, "f32")
        if not ok:
            raise RuntimeError(f"decode {clip.name} ch{idx}: {err}")
        out.append(dst)
    return out


def main() -> int:
    eff = cs.effective_opts(None)
    result: dict = {}
    for name in CLIPS:
        clip = PROOF / f"{name}.MP4"
        if not clip.is_file():
            print(f"{name}: 缺文件")
            continue
        raw = decode(clip)
        n = min(p.stat().st_size // 4 for p in raw)
        print(f"\n=== {name}  {n / SR:.2f}s")
        edges: dict = {}
        for frame_ms, hop_ms, anchor_s, gname in GEOMS:
            for ref, tgt in PAIRS:
                t0 = time.time()
                try:
                    est = se.estimate_pair(
                        str(raw[ref]), str(raw[tgt]), sample_rate=SR,
                        search_window_ms=eff["search_window_ms"],
                        frame_ms=frame_ms, hop_ms=hop_ms,
                        anchor_segment_seconds=anchor_s,
                        min_confidence=eff["min_confidence"],
                        coarse_rate=eff["coarse_rate"],
                        fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                        min_usable_frames=eff["min_usable_frames"],
                        frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                        ref_index=ref, tgt_index=tgt, workers=2)
                except Exception as exc:            # noqa: BLE001
                    print(f"  r{ref + 1}->t{tgt + 1} {gname} 失败: {exc}")
                    continue
                rec = {"usable": int(est.usable_frames),
                       "delay_ms": round(float(est.delay_ms), 3),
                       "conf": round(float(est.confidence), 3),
                       "wall_s": round(time.time() - t0, 2)}
                if est.frames:
                    stats = se.summarize_trajectory(est.frames, SR)
                    disp = se.measured_displacement_ms(est.frames, SR)
                    rec["mad_ms"] = round(float(stats.mad_ms), 3) \
                        if stats.mad_ms == stats.mad_ms else None
                    rec["constant"] = bool(se.classify_constant(
                        stats, mad_max_ms=eff["mad_max_ms"],
                        max_ppm=eff["constant_max_ppm"], sample_rate=SR,
                        drift_min_ms=eff["drift_min_ms"],
                        displacement_ms=disp))
                edges.setdefault(gname, {})[f"{ref + 1}-{tgt + 1}"] = rec
                if rec["usable"] >= 5:
                    print(f"  {gname:<11} CH{ref + 1}->CH{tgt + 1}: "
                          f"可用 {rec['usable']:>4}  延迟 {rec['delay_ms']:>8.2f}ms"
                          f"  const={rec.get('constant')}")
        result[name] = {"duration_s": n / SR, "edges": edges}
        atomic = json.dumps(result, ensure_ascii=False, indent=1)
        (OUT / "proof_pairs.json").write_text(atomic, encoding="utf-8")
    print(f"\n已写 {OUT / 'proof_pairs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
