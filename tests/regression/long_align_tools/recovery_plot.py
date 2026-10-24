#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recovery-precision experiments + plots for the fine delay pass.

The first known-answer run injected the profiles on top of pass1 (the 2-min-anchor
output), so the measured "error" was really the real remaining residual, not a
recovery error. This version injects on top of **fine** (already residual-corrected),
so the recovered smoothing curve can be compared with the injected truth directly.

Injected profiles:
  * const7    : +7 samples everywhere
  * step30    : +30 samples after 70 % of the take (edge/transition test)
  * ramp5ppm  : +5 ppm linear drift

Also produces:
  * fine-vs-pass1 5-minute block residual comparison (from fine_delay.json);
  * real-data second-pass accuracy: measured window residual vs applied curve.

Outputs: data/_scratch/recovery.json, docs/audio-sync-p4/plots/recovery_precision.png,
docs/audio-sync-p4/plots/fine_vs_pass1.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "work"))
import fine_delay as fd  # noqa: E402

SR = fd.SR
ALIGNED = fd.OUT
PCM = fd.PCM
PLOT = fd.PLOT
CLIPS = fd.CLIPS
OUT = REPO / "data" / "_scratch"
KA = OUT / "ka_fine"


def inject(fine_path: Path, dst_path: Path, prof, n: int) -> np.ndarray:
    src = np.memmap(fine_path, dtype=np.float32, mode="r")
    y = np.empty(n, dtype=np.float32)
    CH = 5_000_000
    for s0 in range(0, n, CH):
        s1 = min(n, s0 + CH)
        idx = np.arange(s0, s1, dtype=np.float64)
        sh = prof(idx)
        pos = idx - sh
        invalid = (pos < 0) | (pos > n - 1)
        np.clip(pos, 0, n - 1, out=pos)
        block = np.interp(pos, np.arange(s0, s1, dtype=np.float64),
                          np.asarray(src[s0:s1], dtype=np.float64))
        block[invalid] = 0.0
        y[s0:s1] = block
    del src
    dst_path.write_bytes(y.tobytes())
    return y


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOT.mkdir(parents=True, exist_ok=True)
    KA.mkdir(parents=True, exist_ok=True)
    base_report = json.loads((OUT / "fine_delay.json").read_text(encoding="utf-8"))
    report = {"known_answer": {}, "real_residual": {}}

    profiles = {
        "const7": lambda idx: np.full_like(idx, 7.0),
        "step30": None,      # 取时长后段阶跃, 每个素材单独构造
        "ramp1ppm": lambda idx: 1e-6 * idx,
    }
    fig, axes = plt.subplots(2, 3, figsize=(16, 7.5), sharex="row")
    for row, stem in enumerate(CLIPS):
        d = ALIGNED / stem
        n = (PCM / stem / "ch0.f32").stat().st_size // 4
        work = KA / stem
        work.mkdir(parents=True, exist_ok=True)
        ref = np.memmap(d / "ch0_fine.f32", dtype=np.float32, mode="r")
        np.asarray(ref, dtype=np.float32).tofile(work / "ch0_pass1.f32")
        del ref
        step_t = int(0.7 * n)
        profiles["step30"] = (lambda idx, st=step_t:
                              np.where(idx > st, 30.0, 0.0))
        for col, (name, prof) in enumerate(profiles.items()):
            run = work / f"run_{name}"
            run.mkdir(exist_ok=True)
            (run / "ch0_pass1.f32").write_bytes(
                (work / "ch0_pass1.f32").read_bytes())
            inject(d / "ch2_fine.f32", run / "ch2_pass1.f32", prof, n)
            radius = 400 if name.startswith("ramp") else 80
            rows = fd.measure_residual(stem, run, 2, search48=radius)
            rcurve = fd.smooth_residual(rows, n)
            grid = np.arange(0, n, 10_000, dtype=np.float64)
            truth = prof(grid)
            err_full = np.abs(rcurve - truth)
            edge = 30 * SR
            inside = (grid > edge) & (grid < n - edge)
            err = err_full[inside]
            # 稳态误差: 排除边界与阶跃过渡带 (5-min 中值窗必然把阶跃抹开)
            tt = grid / SR
            dur = n / SR
            steady = inside.copy()
            if name == "step30":
                steady &= np.abs(tt - step_t / SR) > 300.0
            elif name == "ramp1ppm":
                steady &= (tt > 300.0) & (tt < dur - 300.0)
            sel = inside & steady
            errs = err_full[sel] if sel.any() else err
            stats = {
                "max_err": float(err.max()),
                "p90_err": float(np.percentile(err, 90)),
                "median_err": float(np.median(err)),
                "steady_max": float(errs.max()),
                "steady_p90": float(np.percentile(errs, 90)),
                "steady_median": float(np.median(errs)),
                "n_windows": len(rows),
            }
            report["known_answer"][f"{stem}_{name}"] = stats
            ax = axes[row][col]
            windows_t = np.array([r["t_s"] for r in rows]) / 60.0
            windows_r = np.array([r["residual"] for r in rows])
            windows_cc = np.array([r["cc"] for r in rows])
            good = windows_cc >= 0.20
            ax.scatter(windows_t[good], windows_r[good], c=windows_cc[good],
                       cmap="viridis", s=6, alpha=0.7, linewidths=0)
            ax.plot(grid / SR / 60.0, truth, "k--", lw=1.4, label="injected truth")
            ax.plot(grid / SR / 60.0, rcurve, color="#d62728", lw=1.8,
                    label="recovered / applied")
            ax.axhline(0, color="gray", lw=0.6)
            ax.set_title(f"{stem[-4:]} {name}: steady p90 "
                         f"{stats['steady_p90']:.1f} smp, max {stats['max_err']:.0f}")
            ax.set_ylabel("residual [samples]")
            ax.set_xlabel("time [min]")
            ax.grid(alpha=0.25)
            ax.legend(fontsize=6)
            print(f"  {stem} {name}: steady med={stats['steady_median']:.1f} "
                  f"p90={stats['steady_p90']:.1f} max={stats['max_err']:.1f}",
                  flush=True)
    fig.suptitle("Known-answer recovery of the fine delay pass (injected on fine)",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = PLOT / "recovery_precision.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")

    # ---------------- real-data second pass accuracy
    fig, axes = plt.subplots(2, 2, figsize=(15, 7))
    for row, stem in enumerate(CLIPS):
        for col, target in enumerate((2, 3)):
            key = f"{stem}_CH{target + 1}"
            rows = base_report["residual_points"][key]
            pts = base_report["residual_curve"][stem][str(target)]["pts"]
            t = np.array([r["t_s"] for r in rows])
            rr = np.array([r["residual"] for r in rows])
            cc = np.array([r["cc"] for r in rows])
            px = np.array([p[0] for p in pts], dtype=np.float64) * SR
            py = np.array([p[1] for p in pts], dtype=np.float64)
            applied = np.interp(t, px, py)
            good = cc >= 0.20
            err = np.abs(rr - applied)
            out = {"n": int(good.sum()),
                   "median_err": float(np.median(err[good])) if good.any() else None,
                   "p90_err": float(np.percentile(err[good], 90)) if good.any() else None,
                   "frac_le2": float(np.mean(err[good] <= 2.0)) if good.any() else None}
            report["real_residual"][key] = out
            ax = axes[row][col]
            sc = ax.scatter(t[good] / 60.0, err[good], c=cc[good],
                            cmap="viridis", s=7, alpha=0.8, linewidths=0)
            ax.set_yscale("symlog", linthresh=1)
            ax.axhline(1, color="gray", ls="--", lw=0.8)
            ax.axhline(2, color="gray", ls=":", lw=0.8)
            ax.set_title(f"{stem[-4:]} CH{target + 1}: |measured - applied|  "
                         f"(p90 {out['p90_err']:.1f}, <=2 smp "
                         f"{100 * out['frac_le2']:.0f}%)")
            ax.set_xlabel("time [min]")
            ax.set_ylabel("|residual difference| [samples]")
            ax.grid(alpha=0.25)
            fig.colorbar(sc, ax=ax, fraction=0.03, pad=0.01, label="|CC|")
    fig.tight_layout()
    p2 = PLOT / "recovery_residual_consistency.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    print(f"已写 {p2}")

    # ---------------- fine vs pass1 block residuals
    fig, axes = plt.subplots(2, 2, figsize=(15, 6.5))
    for row, stem in enumerate(CLIPS):
        bv = base_report["block_verify"][stem]
        labels, pass_med, fine_med, pass_p90, fine_p90 = [], [], [], [], []
        for key in bv:
            i, j = key.split("_")[1].split("-")
            pair = f"CH{int(i) + 1}-CH{int(j) + 1}"
            if pair in labels:
                continue
            labels.append(pair)
            pass_med.append(bv[f"pass1_{i}-{j}"]["median"])
            fine_med.append(bv[f"fine_{i}-{j}"]["median"])
            pass_p90.append(bv[f"pass1_{i}-{j}"]["p90"])
            fine_p90.append(bv[f"fine_{i}-{j}"]["p90"])
        x = np.arange(len(labels))
        w = 0.2
        for ax, vals_p, vals_f, title in (
                (axes[row][0], pass_med, fine_med, "median"),
                (axes[row][1], pass_p90, fine_p90, "p90")):
            ax.bar(x - w / 2, vals_p, w, label="pass1 (2-min anchors)",
                   color="#888888")
            ax.bar(x + w / 2, vals_f, w, label="fine (residual pass)",
                   color="#2ca02c")
            ax.set_xticks(x)
            ax.set_xticklabels(labels)
            ax.set_ylabel(f"5-min block residual {title} [samples]")
            ax.set_title(f"{stem}: {title} of 5-min block residual")
            ax.grid(alpha=0.25, axis="y")
            ax.legend(fontsize=7)
    fig.tight_layout()
    p3 = PLOT / "fine_vs_pass1.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    print(f"已写 {p3}")

    (OUT / "recovery.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 data/_scratch/recovery.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
