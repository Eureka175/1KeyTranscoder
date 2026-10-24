#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio polarity detection + repair test on the fine-aligned channels.

Detection: for every channel pair and every 60 s window (30 s hop) we take the signed
correlation peak near zero lag (±16 samples at 48 kHz) on the time-aligned audio. The
sign of the peak is the polarity relation of that pair in that window. Aggregating over
windows gives a per-pair polarity vote; per-channel polarity relative to CH1 follows
from the (0,i) pairs (and is checked for consistency against (2,3)).

Repair test:
  * flip the channels whose polarity is negative;
  * re-derive all six pair signs (expected: all positive);
  * measure a coherent-sum gain: RMS of the polarity-corrected 4-channel sum over RMS
    of the uncorrected sum (same windows), and the signed correlation between the
    CH1+CH2 and CH3+CH4 group sums before/after;
  * write a polarity-corrected 4ch WAV for auditioning.

Outputs: data/_scratch/polarity.json, docs/audio-sync-p4/plots/polarity.png,
aligned_fine/<stem>/aligned_4ch_fine_polarity.wav
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import signal

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))
import long_align as la  # noqa: E402

SR = 48000
DEC = 4
WIN_S = 60.0
HOP_S = 30.0
SEARCH48 = 16              # +-16 samples at 48 kHz (0.33 ms)
ALIGNED = REPO / "data" / "_scratch" / "aligned_fine"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"
CLIPS = ["20260919_C1305", "20260919_C1306"]


def window_signed_peak(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a12 = signal.resample_poly(np.asarray(a, np.float64), 1, DEC)
    b12 = signal.resample_poly(np.asarray(b, np.float64), 1, DEC)
    n = min(len(a12), len(b12))
    a12 = a12[:n] - a12[:n].mean()
    b12 = b12[:n] - b12[:n].mean()
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = np.fft.irfft(np.fft.rfft(a12, nfft) * np.conj(np.fft.rfft(b12, nfft)), nfft)
    den = np.linalg.norm(a12) * np.linalg.norm(b12) + 1e-12
    search = int(SEARCH48 / DEC) + 2
    cand = np.concatenate([surf[:search + 1], surf[-search:]]) / den
    i = int(np.argmax(np.abs(cand)))
    return float(cand[i]), float(abs(cand[i]))


def measure(stem: str, channels: dict[int, np.memmap]) -> dict:
    n = min(len(v) for v in channels.values())
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    starts = list(range(0, max(0, n - win) + 1, hop))
    pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    rows = {f"{i}-{j}": [] for i, j in pairs}
    # 同时计算和信号统计
    sum_before = []
    group_cc_before = []
    for s in starts:
        win_arrays = {i: np.asarray(channels[i][s:s + win], np.float64)
                      for i in range(4)}
        for (i, j) in pairs:
            sc, ac = window_signed_peak(win_arrays[i], win_arrays[j])
            rows[f"{i}-{j}"].append({"t_s": s / SR, "signed": sc, "abs": ac})
        g1 = win_arrays[0] + win_arrays[1]
        g2 = win_arrays[2] + win_arrays[3]
        g1 = g1 - g1.mean()
        g2 = g2 - g2.mean()
        nfft = int(1 << int(np.ceil(np.log2(2 * win))))
        surf = np.fft.irfft(np.fft.rfft(g1, nfft) * np.conj(np.fft.rfft(g2, nfft)), nfft)
        den = np.linalg.norm(g1) * np.linalg.norm(g2) + 1e-12
        cand = np.concatenate([surf[:int(SEARCH48 / DEC) + 1],
                               surf[-int(SEARCH48 / DEC):]]) / den
        group_cc_before.append(float(cand[int(np.argmax(np.abs(cand)))]))
        total = sum(win_arrays[i] for i in range(4))
        sum_before.append(float(np.sqrt(np.mean(total * total))))
    return {"rows": rows, "starts": starts, "sum_before": sum_before,
            "group_cc_before": group_cc_before}


def analyse(meas: dict) -> dict:
    out = {"pairs": {}, "flip": {}}
    strong = 0.25
    for key, rows in meas["rows"].items():
        vals = [r["signed"] for r in rows if r["abs"] >= strong]
        if not vals:
            vals = [r["signed"] for r in rows]
        v = np.asarray(vals)
        out["pairs"][key] = {
            "n_strong": int(len(vals)),
            "median_signed": float(np.median(v)),
            "frac_positive": float(np.mean(v > 0)),
            "median_abs": float(np.median(np.abs(v))),
        }
    # 每通道相对 CH1 的极性: (0,i) 的中位带符号值
    flip = {0: False}
    for i in (1, 2, 3):
        key = f"0-{i}"
        if key in out["pairs"]:
            flip[i] = out["pairs"][key]["median_signed"] < 0
    flip = {int(k): bool(v) for k, v in flip.items()}
    out["flip"] = flip
    # 一致性检查: pair (i,j) 的预期符号 = (-1)^(flip_i+flip_j)
    checks = {}
    for key, v in out["pairs"].items():
        i, j = [int(x) for x in key.split("-")]
        expected_positive = (not flip[i]) == (not flip[j])
        observed = v["median_signed"] > 0
        checks[key] = {"expected_positive": expected_positive,
                       "observed_positive": observed,
                       "consistent": expected_positive == observed}
    out["consistency"] = checks
    out["consistent"] = all(v["consistent"] for v in checks.values())
    return out


def main() -> int:
    PLOT.mkdir(parents=True, exist_ok=True)
    report = {}
    for stem in CLIPS:
        d = ALIGNED / stem
        chans = {i: np.memmap(d / f"ch{i}_fine.f32", dtype=np.float32, mode="r")
                 for i in range(4)}
        print(f"\n=== {stem}: measuring signed polarity per window ...", flush=True)
        meas = measure(stem, chans)
        ana = analyse(meas)
        n = min(len(v) for v in chans.values())
        # 修正后: 和信号与群组相关
        flips = ana["flip"]
        sum_after, group_after = [], []
        win = int(WIN_S * SR)
        for s in meas["starts"]:
            arr = {i: np.asarray(chans[i][s:s + win], np.float64)
                   * (-1.0 if flips[i] else 1.0) for i in range(4)}
            total = sum(arr.values())
            sum_after.append(float(np.sqrt(np.mean(total * total))))
            g1 = arr[0] + arr[1]
            g2 = arr[2] + arr[3]
            g1 = g1 - g1.mean()
            g2 = g2 - g2.mean()
            nfft = int(1 << int(np.ceil(np.log2(2 * win))))
            surf = np.fft.irfft(np.fft.rfft(g1, nfft) * np.conj(np.fft.rfft(g2, nfft)), nfft)
            den = np.linalg.norm(g1) * np.linalg.norm(g2) + 1e-12
            cand = np.concatenate([surf[:int(SEARCH48 / DEC) + 1],
                                   surf[-int(SEARCH48 / DEC):]]) / den
            group_after.append(float(cand[int(np.argmax(np.abs(cand)))]))
        sum_before = np.asarray(meas["sum_before"])
        sum_after = np.asarray(sum_after)
        gain_db = 20 * np.log10((sum_after + 1e-12) / (sum_before + 1e-12))
        report[stem] = {
            "pairs": ana["pairs"], "flip": ana["flip"],
            "consistency": ana["consistency"], "consistent": ana["consistent"],
            "sum_rms_gain_db_median": float(np.median(gain_db)),
            "sum_rms_gain_db_p10": float(np.percentile(gain_db, 10)),
            "sum_rms_gain_db_p90": float(np.percentile(gain_db, 90)),
            "group_cc_before_median": float(np.median(meas["group_cc_before"])),
            "group_cc_after_median": float(np.median(group_after)),
            "n_windows": len(meas["starts"]),
        }
        print(f"  flip={ana['flip']} consistent={ana['consistent']}")
        for k, v in ana["pairs"].items():
            print(f"  pair {k}: n={v['n_strong']} median_signed={v['median_signed']:+.3f} "
                  f"frac_positive={v['frac_positive']:.2f}")
        print(f"  sum RMS gain median {np.median(gain_db):+.2f} dB; "
              f"group CC before {np.median(meas['group_cc_before']):+.3f} -> "
              f"after {np.median(group_after):+.3f}")
        # 极性修正后的 4ch wav
        inter = d / "_pol_inter.f32"
        with inter.open("wb") as fh:
            CH = 4_000_000
            for s0 in range(0, n, CH):
                s1 = min(n, s0 + CH)
                block = np.empty((s1 - s0, 4), dtype=np.float32)
                for i in range(4):
                    sign = -1.0 if flips[i] else 1.0
                    block[:, i] = np.asarray(chans[i][s0:s1]) * sign
                fh.write(block.tobytes())
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "f32le",
               "-ar", str(SR), "-ac", "4", "-i", str(inter),
               "-c:a", "pcm_s24le", str(d / "aligned_4ch_fine_polarity.wav")]
        subprocess.run(cmd, capture_output=True)
        inter.unlink()
        del chans
    (REPO / "data" / "_scratch" / "polarity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 data/_scratch/polarity.json")

    # ---------------- plot
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.6))
    pairs = ["0-1", "0-2", "0-3", "1-2", "1-3", "2-3"]
    labels = [f"CH{i + 1}-CH{j + 1}" for i, j in
              [(int(x.split("-")[0]), int(x.split("-")[1])) for x in pairs]]
    width = 0.38
    idx = np.arange(len(pairs))
    for ax, stem in zip(axes, CLIPS):
        r = report[stem]
        before = [r["pairs"][k]["median_signed"] for k in pairs]
        after = []
        for k in pairs:
            i, j = [int(x) for x in k.split("-")]
            sign = (-1.0 if r["flip"][str(i)] else 1.0) * \
                   (-1.0 if r["flip"][str(j)] else 1.0)
            after.append(r["pairs"][k]["median_signed"] * sign)
        ax.bar(idx - width / 2, before, width, label="before polarity fix",
               color="#888888")
        ax.bar(idx + width / 2, after, width, label="after polarity fix",
               color="#2ca02c")
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(idx)
        ax.set_xticklabels(labels, rotation=20)
        ax.set_ylabel("median signed correlation")
        ax.set_title(f"{stem}: pair polarity (60 s windows)\n"
                     f"sum gain {r['sum_rms_gain_db_median']:+.2f} dB, "
                     f"group CC {r['group_cc_before_median']:+.2f} -> "
                     f"{r['group_cc_after_median']:+.2f}")
        ax.grid(alpha=0.25, axis="y")
        ax.legend(fontsize=7)
    # 第二组: 逐窗符号比例
    fig.tight_layout()
    p = PLOT / "polarity.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")
    # 单独一张: 逐窗符号稳定性
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.2))
    for ax, stem in zip(axes, CLIPS):
        r = report[stem]
        fracs = [r["pairs"][k]["frac_positive"] for k in pairs]
        colors = ["#2ca02c" if f >= 0.5 else "#d62728" for f in fracs]
        ax.bar(idx, fracs, color=colors)
        ax.axhline(0.5, color="k", ls="--", lw=0.8)
        ax.set_xticks(idx)
        ax.set_xticklabels(labels, rotation=20)
        ax.set_ylim(0, 1)
        ax.set_ylabel("fraction of windows with positive sign")
        ax.set_title(f"{stem}: polarity vote per pair")
        ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p2 = PLOT / "polarity_votes.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    print(f"已写 {p2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
