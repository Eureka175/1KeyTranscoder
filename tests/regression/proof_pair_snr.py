#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Can the four channels be aligned against each other at all?

"All four channels carry valid audio" is necessary but not sufficient: alignment
needs **shared content**. To measure that without decoding anything, this uses
the production grouping metric (`sample_pair_snr`, the same quantity the grouping
threshold 1.3 is compared against) over the cached channel PCM.

For each clip it prints the 6 pairwise SNRs and, per track, the best partner.
A track whose best partner is below the threshold has no alignable partner -- no
anchoring strategy, no robust gate and no per-window correction can align it,
because there is no shared signal to measure. That distinction is the whole point:
it separates "we failed to measure" from "there is nothing to measure".
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.channel_sync import effective_opts  # noqa: E402
from core.sync_estimate import open_source, sample_pair_snr  # noqa: E402

CACHE = Path(r"E:\1kt_scratch\pcm")
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
SR = 48000
THRESHOLD = 1.3


def main() -> int:
    names = sorted(p.stem for p in PROOF.glob("*.MP4"))
    if len(sys.argv) > 1:
        names = [n for n in names if any(a in n for a in sys.argv[1:])]
    eff = effective_opts(None)
    rows = []
    out: list[dict] = []
    for nm in names:
        d = CACHE / nm
        if not d.is_dir():
            print(f"{nm}: 无缓存")
            continue
        paths = sorted(d.glob("ch*.f32"))
        if len(paths) < 4:
            print(f"{nm}: 只有 {len(paths)} 个通道缓存")
            continue
        arrs = [open_source(p, "f32") for p in paths]
        try:
            snr = {}
            for i in range(len(arrs)):
                for j in range(i + 1, len(arrs)):
                    s = sample_pair_snr(
                        arrs[i], arrs[j], sample_rate=SR,
                        frame_ms=eff["frame_ms"], hop_ms=eff["hop_ms"],
                        search_window_ms=eff["search_window_ms"],
                        coarse_rate=eff["coarse_rate"],
                        frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                        samples=eff["group_snr_samples"])
                    snr[(i, j)] = s
        finally:
            for a in arrs:
                c = getattr(a, "close", None)
                if callable(c):
                    c()
        best = {}
        for i in range(len(arrs)):
            cands = [(snr[(min(i, j), max(i, j))].get("median", float("nan")), j)
                     for j in range(len(arrs)) if j != i]
            cands = [c for c in cands if c[0] == c[0]]
            best[i] = max(cands) if cands else (float("nan"), -1)
        pair_txt = "  ".join(
            f"{a + 1}-{b + 1}:{v.get('median', float('nan')):.2f}"
            for (a, b), v in snr.items())
        print(f"{nm}  {pair_txt}")
        for i, (v, j) in best.items():
            tag = "可对齐" if v >= THRESHOLD else ("**无共同内容**")
            print(f"    CH{i + 1} 最佳伙伴 CH{j + 1} SNR={v:.2f}  {tag}")
        rows.append((nm, snr, best))
        out.append({"clip": nm,
                    "snr": {f"{a + 1}-{b + 1}": v for (a, b), v in snr.items()},
                    "best": {f"CH{i + 1}": {"partner": j + 1, "snr": v}
                             for i, (v, j) in best.items()}})

    n_tracks = sum(len(b) for _, _, b in rows)
    n_pairable = sum(1 for _, _, b in rows for v, _ in b.values()
                     if v == v and v >= THRESHOLD)
    (Path(r"F:\1KeyTranscoder\work\regression_r087\out")
     / "proof_pair_snr.json").write_text(
        __import__("json").dumps(out, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n汇总: {len(rows)} 件 / {n_tracks} 轨;"
          f" 至少有一个可对齐伙伴的轨 {n_pairable}"
          f" ({100 * n_pairable / max(1, n_tracks):.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
