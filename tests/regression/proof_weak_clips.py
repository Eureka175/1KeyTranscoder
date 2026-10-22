#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Are CH1/CH2 of the short weak clips measurable at all, with any knob?

For C1180 (7.5 s), C1202 (6.0 s) and C1211 (25.5 s) the production pipeline finds
no usable frames for some channels. The long-take work showed two levers: a
partner closer to the signal (reference choice) and a looser confidence gate /
longer frame. This sweeps all pairs x three geometries x two confidence gates for
those three clips and reports the best usable-frame count per channel, which is
what decides whether the offset can be established at all.
"""
from __future__ import annotations

import json
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
WORK = Path(r"E:\1kt_scratch\pairs2")
SR = 48000
OUT = REPO / "work" / "regression_r087" / "out"
CLIPS = ["20260904_C1180", "20260904_C1202", "20260904_C1211"]
GEOMS = [(200.0, 100.0), (500.0, 250.0), (1000.0, 500.0)]
GATES = [0.3, 0.2]
PAIRS = [(i, j) for i in range(4) for j in range(4) if i != j]


def main() -> int:
    eff = cs.effective_opts(None)
    out: dict = {}
    for name in CLIPS:
        clip = PROOF / f"{name}.MP4"
        raw = []
        d = WORK / name
        d.mkdir(parents=True, exist_ok=True)
        for idx in range(4):
            dst = d / f"ch{idx}.f32"
            cached = CACHE / name / f"ch{idx}.f32"
            if not dst.is_file():
                if cached.is_file():
                    try:
                        os.link(cached, dst)
                    except OSError:
                        shutil.copyfile(cached, dst)
                else:
                    cs._decode_stream(FFMPEG, clip, idx, SR, dst, "f32")
            raw.append(dst)
        n = min(p.stat().st_size // 4 for p in raw)
        print(f"\n=== {name} {n / SR:.2f}s")
        best: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
        rows = []
        for frame_ms, hop_ms in GEOMS:
            for gate in GATES:
                for ref, tgt in PAIRS:
                    try:
                        est = se.estimate_pair(
                            str(raw[ref]), str(raw[tgt]), sample_rate=SR,
                            search_window_ms=eff["search_window_ms"],
                            frame_ms=frame_ms, hop_ms=hop_ms,
                            anchor_segment_seconds=20.0,
                            min_confidence=gate,
                            coarse_rate=eff["coarse_rate"],
                            fine_phase_band_hz=tuple(
                                eff["fine_phase_band_hz"]),
                            min_usable_frames=3,
                            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                            ref_index=ref, tgt_index=tgt, workers=2)
                    except Exception:               # noqa: BLE001
                        continue
                    u = int(est.usable_frames)
                    if u < 3:
                        continue
                    rows.append({"ref": ref + 1, "tgt": tgt + 1,
                                 "frame_ms": frame_ms, "gate": gate,
                                 "usable": u,
                                 "delay_ms": round(float(est.delay_ms), 2)})
                    best[tgt + 1].append((u, ref + 1, frame_ms, gate,
                                          round(float(est.delay_ms), 2)))
        for ch in sorted(best):
            lst = sorted(best[ch], reverse=True)[:3]
            if lst:
                print(f"  CH{ch}: 最佳 " + " | ".join(
                    f"{u}帧 ← CH{r} (帧 {f:.0f}ms, 门 {g}, {dl:+.2f}ms)"
                    for u, r, f, g, dl in lst))
            else:
                print(f"  CH{ch}: 任何组合都 <3 帧")
        out[name] = {"duration_s": n / SR, "rows": rows,
                     "best": {str(k): v[:5] for k, v in best.items()}}
        (OUT / "proof_pairs_knobs.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'proof_pairs_knobs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
