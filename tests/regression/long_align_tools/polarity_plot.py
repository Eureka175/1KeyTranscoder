#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plot the polarity results from polarity.json and finish the polarity-corrected WAV.

The measurement itself is done (both takes: flip = {CH1:no, CH2:no, CH3:yes,
CH4:yes}); the previous run crashed in the plotting code because the in-memory flip
dict has int keys while the code indexed it with str. This reads the saved JSON and
renders:
  * per-pair signed correlation before/after polarity fix;
  * per-pair positive-vote fraction;
  * the coherent-sum gain and group correlation before/after.
Also writes aligned_4ch_fine_polarity.wav for any take where it is missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))
import long_align as la  # noqa: E402

SR = 48000
SCRATCH = REPO / "data" / "_scratch"
ALIGNED = SCRATCH / "aligned_fine"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"
CLIPS = ["20260919_C1305", "20260919_C1306"]
PAIRS = ["0-1", "0-2", "0-3", "1-2", "1-3", "2-3"]
LABELS = [f"CH{i + 1}-CH{j + 1}" for i, j in
          [(int(x.split("-")[0]), int(x.split("-")[1])) for x in PAIRS]]


def ensure_wav(stem: str, rep: dict) -> bool:
    d = ALIGNED / stem
    out = d / "aligned_4ch_fine_polarity.wav"
    if out.is_file():
        return True
    flips = rep["flip"]
    flip = {int(k): bool(v) for k, v in flips.items()}
    n = (d / "ch0_fine.f32").stat().st_size // 4
    inter = d / "_pol_inter.f32"
    with inter.open("wb") as fh:
        CH = 4_000_000
        for s0 in range(0, n, CH):
            s1 = min(n, s0 + CH)
            block = np.empty((s1 - s0, 4), dtype=np.float32)
            for i in range(4):
                m = np.memmap(d / f"ch{i}_fine.f32", dtype=np.float32, mode="r")
                block[:, i] = np.asarray(m[s0:s1]) * (-1.0 if flip.get(i) else 1.0)
                del m
            fh.write(block.tobytes())
    cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "f32le",
           "-ar", str(SR), "-ac", "4", "-i", str(inter),
           "-c:a", "pcm_s24le", str(out)]
    subprocess.run(cmd, capture_output=True)
    inter.unlink()
    return out.is_file()


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOT.mkdir(parents=True, exist_ok=True)
    rep = json.loads((SCRATCH / "polarity.json").read_text(encoding="utf-8"))
    # 确保两条素材的极性修正 wav 存在
    for stem in CLIPS:
        ok = ensure_wav(stem, rep[stem])
        print(f"{stem}: polarity wav {'OK' if ok else 'MISSING'}")
    # ---------------- figure 1: signed CC before/after + votes
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    idx = np.arange(len(PAIRS))
    width = 0.38
    for col, stem in enumerate(CLIPS):
        r = rep[stem]
        flip = {int(k): bool(v) for k, v in r["flip"].items()}
        before = [r["pairs"][k]["median_signed"] for k in PAIRS]
        after = []
        for k in PAIRS:
            i, j = [int(x) for x in k.split("-")]
            sgn = (-1.0 if flip.get(i) else 1.0) * (-1.0 if flip.get(j) else 1.0)
            after.append(r["pairs"][k]["median_signed"] * sgn)
        ax = axes[0][col]
        ax.bar(idx - width / 2, before, width, color="#888888",
               label="before polarity fix")
        ax.bar(idx + width / 2, after, width, color="#2ca02c",
               label="after polarity fix")
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(idx)
        ax.set_xticklabels(LABELS, rotation=20)
        ax.set_ylabel("median signed correlation")
        ax.set_title(f"{stem}: polarity per channel pair\n"
                     f"flip: " + ", ".join(f"CH{i + 1}={'YES' if v else 'no'}"
                                           for i, v in sorted(flip.items())))
        ax.grid(alpha=0.25, axis="y")
        ax.legend(fontsize=7)
        ax = axes[1][col]
        fracs = [r["pairs"][k]["frac_positive"] for k in PAIRS]
        colors = ["#2ca02c" if f >= 0.5 else "#d62728" for f in fracs]
        ax.bar(idx, fracs, color=colors)
        ax.axhline(0.5, color="k", ls="--", lw=0.8)
        ax.set_xticks(idx)
        ax.set_xticklabels(LABELS, rotation=20)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("fraction of windows with positive sign")
        ax.set_title(f"{stem}: polarity vote consistency")
        ax.grid(alpha=0.25, axis="y")
    fig.suptitle("Audio polarity detection and repair test (60 s windows)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = PLOT / "polarity.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")

    # ---------------- figure 2: coherent-sum gain
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(CLIPS))
    gains = [rep[s]["sum_rms_gain_db_median"] for s in CLIPS]
    ax.bar(x - 0.18, gains, 0.34, color="#2ca02c",
           label="sum RMS gain after flip [dB]")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([s[-4:] for s in CLIPS])
    ax.set_ylabel("dB")
    ax.set_title("Coherent-sum gain of the 4-channel mix after polarity repair")
    for i, g in enumerate(gains):
        ax.text(i - 0.18, g + 0.05, f"{g:+.2f} dB", ha="center", fontsize=9)
    ax2 = ax.twinx()
    before = [rep[s]["group_cc_before_median"] for s in CLIPS]
    after = [rep[s]["group_cc_after_median"] for s in CLIPS]
    ax2.plot(x + 0.18, before, "o-", color="#d62728", label="group CC before")
    ax2.plot(x + 0.18, after, "s-", color="#1f77b4", label="group CC after")
    ax2.axhline(0, color="k", lw=0.6, ls=":")
    ax2.set_ylabel("signed group correlation")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="lower right")
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p2 = PLOT / "polarity_gain.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    print(f"已写 {p2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
