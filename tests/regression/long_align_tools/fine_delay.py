#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fine delay refinement (round 3) on the F: caches.

Round 2 (yesterday, E:) used 2-minute anchors from the drift tracker. This round adds
a **residual correction pass**:

  1. s0(t) = 2-min anchor curve (same construction as the delivered pipeline);
  2. materialise pass1[k] = interp(x, k + s0(k)) for all four channels;
  3. measure the residual between pass1 and the anchor on 60 s windows (12 kHz coarse
     search +-80 samples, 48 kHz refine +-8), keep windows with |CC| >= 0.20;
  4. robust smoothing of the residual (5-minute weighted median, |r| <= 100 samples,
     reject outliers > 30 from the local median) -> r(t);
  5. final[k] = interp(pass1, k + r(k)); total shift = s0 + r, still <= 4800 samples;
  6. verify on 5-minute blocks (14M samples) against pass1, and run known-answer
     injections (constant +7, step +30 at 10 min) through the same refinement stage.

Outputs (F:): data/_scratch/aligned_fine/<stem>/{ch{i}_pass1.f32, ch{i}_fine.f32,
aligned_4ch_fine.wav}, data/_scratch/fine_delay.json,
docs/audio-sync-p4/plots/fine_delay.png.
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
SR12 = SR // DEC
MAX_SHIFT = 4800
WIN_S = 60.0
HOP_S = 20.0
BLOCK_S = 300.0
STORE = REPO / "data" / "_scratch" / "drift_rebuild.json"
PCM = REPO / "data" / "_scratch" / "pcm"
OUT = REPO / "data" / "_scratch" / "aligned_fine"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"
CLIPS = ["20260919_C1305", "20260919_C1306"]


def load_curves() -> dict:
    data = json.loads(STORE.read_text(encoding="utf-8"))
    curves = {}
    for a in data["analysis"]:
        key = (a["stem"], a["target"])
        anchors = a.get("anchors") or []
        if not anchors:
            continue
        t = np.array([x[0] for x in anchors], dtype=np.float64) * SR   # seconds->samples
        y = np.array([x[1] for x in anchors], dtype=np.float64)
        curves[key] = (t, y)
    return curves


def curve_eval(curve, n: int) -> np.ndarray:
    t, y = curve
    grid = np.arange(0, n, 10_000, dtype=np.float64)
    return np.interp(grid, t, y)


def interp_write(src_path: Path, dst_path: Path, shift_grid: np.ndarray,
                 n: int, grid_step: int = 10_000) -> None:
    src = np.memmap(src_path, dtype=np.float32, mode="r")
    grid = np.arange(0, n, grid_step, dtype=np.float64)
    with dst_path.open("wb") as fh:
        CH = 5_000_000
        for s0 in range(0, n, CH):
            s1 = min(n, s0 + CH)
            idx = np.arange(s0, s1, dtype=np.float64)
            shift = np.interp(idx, grid, shift_grid)
            pos = idx + shift
            invalid = (pos < 0) | (pos > n - 1)
            np.clip(pos, 0, n - 1, out=pos)
            block = np.interp(pos, np.arange(s0, s1, dtype=np.float64),
                              np.asarray(src[s0:s1], dtype=np.float64))
            block[invalid] = 0.0
            fh.write(block.astype(np.float32).tobytes())
    del src


def measure_residual(stem: str, dirpath: Path, target: int,
                     search48: int = 80) -> list[dict]:
    a = np.memmap(dirpath / "ch0_pass1.f32", dtype=np.float32, mode="r")
    b = np.memmap(dirpath / f"ch{target}_pass1.f32", dtype=np.float32, mode="r")
    n = min(len(a), len(b))
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    rows = []
    for w in range(0, max(0, n - win) // hop + 1):
        s = w * hop
        aa = np.asarray(a[s:s + win], dtype=np.float64)
        bb = np.asarray(b[s:s + win], dtype=np.float64)
        # 12 kHz 粗扫 (残差应在 0 附近)
        a12 = signal.resample_poly(aa, 1, DEC).astype(np.float64)
        b12 = signal.resample_poly(bb, 1, DEC).astype(np.float64)
        n12 = min(len(a12), len(b12))
        nfft = int(1 << int(np.ceil(np.log2(2 * n12))))
        A = np.fft.rfft(a12[:n12] - a12[:n12].mean(), nfft)
        B = np.fft.rfft(b12[:n12] - b12[:n12].mean(), nfft)
        surf = np.fft.irfft(A * np.conj(B), nfft)
        den = np.linalg.norm(a12[:n12]) * np.linalg.norm(b12[:n12]) + 1e-12
        radius12 = max(4, search48 // DEC)   # +-search48 samples at 48 kHz
        search = radius12 + 2
        cand = np.abs(np.concatenate([surf[:search + 1], surf[-search:]]) / den)
        m = np.concatenate([np.arange(0, search + 1),
                            np.arange(-search, 0)]).astype(np.int64)
        i = int(np.argmax(cand))
        coarse = -float(m[i]) * DEC
        # 48 kHz 精化: 2 s 中心段点积
        seg = int(2 * SR)
        c0 = len(aa) // 2 - seg // 2
        av = aa[c0:c0 + seg] - aa[c0:c0 + seg].mean()
        best = (coarse, 0.0)
        for mm in range(int(coarse) - 8, int(coarse) + 9):
            b0 = c0 + mm
            if b0 < 0 or b0 + seg > len(bb):
                continue
            bv = bb[b0:b0 + seg] - bb[b0:b0 + seg].mean()
            dn = np.linalg.norm(av) * np.linalg.norm(bv)
            if dn < 1e-9:
                continue
            val = float(np.dot(av, bv) / dn)
            if abs(val) > abs(best[1]):
                best = (float(mm), val)
        rows.append({"t_s": s / SR, "residual": best[0],
                     "cc": abs(float(best[1])) if best[1] else 0.0})
    del a, b
    return rows


def smooth_residual(rows: list[dict], n: int) -> np.ndarray:
    good = [r for r in rows if r["cc"] >= 0.20]
    if len(good) < 5:
        return np.zeros(int(np.ceil(n / 10_000)) + 1, dtype=np.float64)
    t = np.array([r["t_s"] for r in good], dtype=np.float64)
    y = np.array([r["residual"] for r in good], dtype=np.float64)
    # 离群: 偏离局部中值 > 30 采样
    keep = np.ones(len(y), dtype=bool)
    for k in range(len(y)):
        lo, hi = max(0, k - 2), min(len(y), k + 3)
        med = np.median(y[lo:hi])
        if abs(y[k] - med) > 30:
            keep[k] = False
    if keep.sum() >= 5:
        t, y = t[keep], y[keep]
    # 5 分钟滑动加权中值 -> 1 分钟 hop
    ts, ys = [], []
    t0 = 0.0
    dur = n / SR
    while t0 < dur:
        m = np.abs(t - (t0 + 150.0)) <= 300.0
        if m.sum() >= 3:
            ts.append(t0 + 150.0)
            ys.append(float(np.median(y[m])))
        t0 += 60.0
    if len(ts) < 2:
        return np.zeros(int(np.ceil(n / 10_000)) + 1, dtype=np.float64)
    grid = np.arange(0, n, 10_000, dtype=np.float64)
    arr = np.interp(grid, np.array(ts) * SR, np.array(ys))
    arr = np.clip(arr, -100.0, 100.0)
    return arr


def block_verify(dirpath: Path, n: int, pairs=((0, 2), (0, 3))) -> dict:
    ch = {i: np.memmap(dirpath / f"ch{i}_{'pass1' if False else 'pass1'}.f32",
                       dtype=np.float32, mode="r")
          for i in range(4)} if False else None
    out = {}
    blk = int(BLOCK_S * SR)
    ch = {}
    for i in range(4):
        p1 = dirpath / f"ch{i}_pass1.f32"
        pf = dirpath / f"ch{i}_fine.f32"
        ch[i] = (np.memmap(p1, dtype=np.float32, mode="r") if p1.is_file() else None,
                 np.memmap(pf, dtype=np.float32, mode="r") if pf.is_file() else None)
    for tag, pos in (("pass1", 0), ("fine", 1)):
        for (i, j) in pairs:
            if ch[i][pos] is None or ch[j][pos] is None:
                continue
            lags, ccs = [], []
            for s in range(0, n - blk + 1, blk):
                a = np.asarray(ch[i][pos][s:s + blk], dtype=np.float64)
                b = np.asarray(ch[j][pos][s:s + blk], dtype=np.float64)
                a -= a.mean()
                b -= b.mean()
                nfft = int(1 << int(np.ceil(np.log2(2 * blk))))
                surf = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
                den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
                R = 400
                cand = np.concatenate([surf[:R + 1], surf[-R:]])
                idx = int(np.argmax(np.abs(cand)))
                mm = idx if idx <= R else idx - (2 * R + 1)
                lags.append(-mm)
                ccs.append(float(abs(cand[idx]) / den))
            a = np.asarray(lags, dtype=np.float64)
            cc = np.asarray(ccs)
            strong = cc >= 0.25
            vals = np.abs(a[strong]) if strong.any() else np.abs(a)
            out[f"{tag}_{i}-{j}"] = {
                "n": int(vals.size), "median": float(np.median(vals)),
                "p90": float(np.percentile(vals, 90)), "max": float(np.max(vals)),
                "le1": int((vals <= 1).sum()), "le2": int((vals <= 2).sum()),
                "mean_cc": float(cc.mean())}
    del ch
    return out


def main() -> int:
    PLOT.mkdir(parents=True, exist_ok=True)
    curves = load_curves()
    report = {"curves": {}, "residual_points": {}, "residual_curve": {},
              "block_verify": {}, "known_answer": {}}
    for stem in CLIPS:
        d = OUT / stem
        d.mkdir(parents=True, exist_ok=True)
        n = (PCM / stem / "ch0.f32").stat().st_size // 4
        print(f"\n=== {stem}: n={n} ({n / SR / 60:.1f} min)", flush=True)
        # s0 for each channel
        s0 = {}
        for target in (2, 3):
            t, y = curves[(stem, target)]
            grid = np.arange(0, n, 10_000, dtype=np.float64)
            s0[target] = np.interp(grid, t, y)
        # pass1: CH1 = 0, CH2 = -4 (constant), CH3/CH4 = 2-min anchor curves
        grid = np.arange(0, n, 10_000, dtype=np.float64)
        for i in range(4):
            if i == 0:
                shift = np.zeros_like(grid)
            elif i == 1:
                shift = np.full_like(grid, -4.0)
            else:
                t, y = curves[(stem, i)]
                shift = np.interp(grid, t, y)
            interp_write(PCM / stem / f"ch{i}.f32", d / f"ch{i}_pass1.f32",
                         shift, n)
            report["curves"].setdefault(stem, {})[str(i)] = {
                "max_abs_shift": float(np.abs(shift).max()),
                "min_shift": float(shift.min()), "max_shift": float(shift.max())}
        print("  pass1 written", flush=True)
        # residual measurement on pass1
        rcurves = {}
        for target in (2, 3):
            rows = measure_residual(stem, d, target)
            report["residual_points"][f"{stem}_CH{target + 1}"] = rows
            rcurve = smooth_residual(rows, n)
            rcurves[target] = rcurve
            grid = np.arange(0, n, 10_000, dtype=np.float64)
            report["residual_curve"].setdefault(stem, {})[str(target)] = {
                "n_points": len(rows),
                "max_abs_correction": float(np.abs(rcurve).max()),
                "median_correction": float(np.median(np.abs(rcurve))),
                "pts": [[round(g / SR, 2), round(v, 2)]
                        for g, v in zip(grid[::60], rcurve[::60])]}
            print(f"  CH{target + 1}: {len(rows)} residual windows, "
                  f"max|corr|={np.abs(rcurve).max():.1f} samples", flush=True)
        # final = pass1 shifted by residual curve
        for i in range(4):
            if i in (2, 3):
                r = rcurves[i]
            else:
                r = np.zeros_like(np.arange(0, n, 10_000, dtype=np.float64))
            # pass1 已含 s0; 这里只在 pass1 上再做 r (double interp 很小)
            interp_write(d / f"ch{i}_pass1.f32", d / f"ch{i}_fine.f32", r, n)
        print("  fine written", flush=True)
        # 4ch wav
        inter = d / "_inter.f32"
        with inter.open("wb") as fh:
            CH = 4_000_000
            for s0_ in range(0, n, CH):
                s1 = min(n, s0_ + CH)
                block = np.empty((s1 - s0_, 4), dtype=np.float32)
                for i in range(4):
                    m = np.memmap(d / f"ch{i}_fine.f32", dtype=np.float32, mode="r")
                    block[:, i] = np.asarray(m[s0_:s1])
                    del m
                fh.write(block.tobytes())
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "f32le",
               "-ar", str(SR), "-ac", "4", "-i", str(inter),
               "-c:a", "pcm_s24le", str(d / "aligned_4ch_fine.wav")]
        subprocess.run(cmd, capture_output=True)
        inter.unlink()
        # verification
        bv = block_verify(d, n)
        report["block_verify"][stem] = bv
        for k, v in bv.items():
            print(f"  5min {k}: n={v['n']} med={v['median']:.1f} "
                  f"p90={v['p90']:.1f} max={v['max']} le1={v['le1']} "
                  f"meanCC={v['mean_cc']:.2f}", flush=True)
        # known answer: constant +7 and step +30 on pass1 CH3, through the same stage
        p1 = np.memmap(d / "ch2_pass1.f32", dtype=np.float32, mode="r")
        ref = np.memmap(d / "ch0_pass1.f32", dtype=np.float32, mode="r")
        test = OUT / f"{stem}_ka"
        test.mkdir(parents=True, exist_ok=True)
        for name, prof in (("const7", lambda x: 7.0),
                           ("step30", lambda x: 30.0 if x > 600 * SR else 0.0)):
            y = np.empty(n, dtype=np.float32)
            CH = 5_000_000
            for s0_ in range(0, n, CH):
                s1 = min(n, s0_ + CH)
                idx = np.arange(s0_, s1, dtype=np.float64)
                sh = np.asarray([prof(x) for x in idx])
                pos = idx + sh
                invalid = (pos < 0) | (pos > n - 1)
                np.clip(pos, 0, n - 1, out=pos)
                block = np.interp(pos, np.arange(s0_, s1, dtype=np.float64),
                                  np.asarray(p1[s0_:s1], dtype=np.float64))
                block[invalid] = 0.0
                y[s0_:s1] = block
            (test / f"ch2_{name}.f32").write_bytes(y.tobytes())
            np.asarray(ref, dtype=np.float32).tofile(test / "ch0_ref.f32")
            d2 = test / f"run_{name}"
            d2.mkdir(exist_ok=True)
            (d2 / "ch0_pass1.f32").write_bytes(
                np.asarray(ref, dtype=np.float32).tobytes())
            (d2 / "ch2_pass1.f32").write_bytes((test / f"ch2_{name}.f32").read_bytes())
            rows = measure_residual(stem, d2, 2)
            rc = smooth_residual(rows, n)
            grid = np.arange(0, n, 10_000, dtype=np.float64)
            truth = np.array([prof(x) for x in grid])
            err = np.abs(rc - truth)
            inside = (grid > 30 * SR) & (grid < (n / SR - 30) * SR)
            report["known_answer"][f"{stem}_{name}"] = {
                "max_err_samples": float(err[inside].max()),
                "p90_err_samples": float(np.percentile(err[inside], 90)),
                "mean_err_samples": float(err[inside].mean())}
            print(f"  known-answer {name}: max_err={err[inside].max():.1f} "
                  f"p90={np.percentile(err[inside], 90):.1f} samples", flush=True)
        del p1, ref
    (REPO / "data" / "_scratch" / "fine_delay.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 data/_scratch/fine_delay.json")

    # ---------------- plot
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.5))
    colors = {2: "#1f77b4", 3: "#d62728"}
    for ax, stem in zip(axes, CLIPS):
        for target in (2, 3):
            rows = report["residual_points"][f"{stem}_CH{target + 1}"]
            t = np.array([r["t_s"] for r in rows]) / 60.0
            rr = np.array([r["residual"] for r in rows])
            cc = np.array([r["cc"] for r in rows])
            good = cc >= 0.20
            ax.scatter(t[good], rr[good], c=cc[good], cmap="viridis", s=7,
                       alpha=0.75, linewidths=0, label=f"CH{target + 1} residual")
            pts = report["residual_curve"][stem][str(target)]["pts"]
            if pts:
                xs = [p[0] / 60.0 for p in pts]
                ys = [p[1] for p in pts]
                ax.plot(xs, ys, color=colors[target], lw=2,
                        label=f"CH{target + 1} smoothing / applied")
        ax.axhline(0, color="k", lw=0.8)
        ax.set_title(f"{stem}: residual after 2-min-anchor alignment")
        ax.set_xlabel("time [min]")
        ax.set_ylabel("residual [samples]")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7)
    fig.tight_layout()
    p = PLOT / "fine_delay.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
