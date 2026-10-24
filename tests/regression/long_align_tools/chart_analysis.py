#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deeper chart analysis of the long-take 4CH drift.

Uses the cached rebuild data on F: (drift_rebuild.json + 12 kHz / 48 kHz PCM) to add
five analyses that the first overview plot did not have:

  A. rolling drift rate (10-minute robust slope, ppm) — shows *when* the drift happens
     (C1305 CH4: early ramp then plateau; C1306 CH3: slow ramp);
  B. piecewise-linear fit with automatic breakpoints (BIC-selected, 1-3 segments) and
     the slope of each segment;
  C. residual diagnostics: histogram of tracker residuals after removing the anchor
     curve, and residual vs |CC| (measurement-noise structure);
  D. per-band delay analysis (low 60-1500, mid 1500-4000, high 4000-5900 Hz) per
     5-minute block — clock drift is band-independent, content/path artefacts are not;
  E. measurement noise floor vs integration window length (10/20/40/80 s, band-limited
     full-band at 48 kHz, 10 positions per point) — quantifies how many samples the
     residual floor of a 60 s window can be;
  F. pairwise coherence heatmap (6 pairs x time, 60 s windows) — shows which channel
     pairs carry common content at which time, i.e. where alignment is observable.

Outputs: docs/audio-sync-p4/plots/drift_analysis_a.png, _b.png, coherence_heatmap.png
and data/_scratch/drift_analysis.json.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(r"F:\\1KeyTranscoder")
SCRATCH = REPO / "data" / "_scratch"
PCM = SCRATCH / "pcm"
STORE = SCRATCH / "drift_rebuild.json"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"
SR = 48000
DEC = 4
SR12 = SR // DEC
MAX_LAG = 2400
BANDS = [("low", 60.0, 1500.0), ("mid", 1500.0, 4000.0), ("high", 4000.0, 5900.0)]


def load_json() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8"))


def curve_from(anchors: list[list[float]]):
    t = np.array([a[0] for a in anchors], dtype=np.float64)
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    return lambda sec: np.interp(sec, t, y)


def band_peaks(a12: np.ndarray, b12: np.ndarray, center48: float,
               radius48: int) -> dict:
    n = min(len(a12), len(b12))
    a = a12[:n].astype(np.float64)
    b = b12[:n].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    prod = np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft))
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    freqs = np.fft.rfftfreq(nfft, 1.0 / SR12)
    out = {}
    radius12 = max(4, int(radius48 / DEC))
    # 候选数组必须覆盖 |center|+radius 的范围 (延迟可达数千采样, 不是零附近)
    search = int((abs(center48) + radius48) / DEC) + 2
    m_all = np.concatenate([np.arange(0, search + 1),
                            np.arange(-search, 0)]).astype(np.int64)
    lag_all = -m_all * DEC
    sel = np.abs(lag_all - center48) <= radius48
    for name, lo, hi in BANDS:
        mask = ((freqs >= lo) & (freqs <= hi)).astype(np.float64)
        surf = np.fft.irfft(prod * mask, nfft)
        cand = np.abs(np.concatenate([surf[:search + 1],
                                      surf[-search:]]) / den)
        if not sel.any():
            out[name] = (np.nan, 0.0)
            continue
        cand2 = np.where(sel, cand, -1.0)
        i = int(np.argmax(cand2))
        out[name] = (float(lag_all[i]), float(cand2[i]))
    return out


def rolling_rate(anchors: list[list[float]], win_s: float = 600.0,
                 hop_s: float = 120.0) -> list[tuple[float, float]]:
    t = np.array([a[0] for a in anchors], dtype=np.float64)
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    dur = t[-1]
    out = []
    c = win_s / 2
    while c < dur:
        m = np.abs(t - c) <= win_s / 2
        if m.sum() >= 5:
            tt, yy = t[m], y[m]
            # Theil-Sen (小样本直接全对)
            slopes = []
            for i in range(len(tt)):
                for j in range(i + 1, len(tt)):
                    dt = tt[j] - tt[i]
                    if dt > 0:
                        slopes.append((yy[j] - yy[i]) / dt)
            out.append((float(c / 60.0), float(np.median(slopes) / SR * 1e6)))
        c += hop_s
    return out


def segmented_fit(anchors: list[list[float]], max_seg: int = 3) -> dict:
    """Piecewise-linear fit with DP; BIC selects segment count."""
    t = np.array([a[0] for a in anchors], dtype=np.float64)
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    n = len(t)
    if n < 6:
        return {"ok": False}

    def seg_sse(i: int, j: int) -> float:
        tt, yy = t[i:j], y[i:j]
        if len(tt) < 2:
            return 0.0
        A = np.column_stack([np.ones_like(tt), tt])
        sol, *_ = np.linalg.lstsq(A, yy, rcond=None)
        r = yy - A @ sol
        return float(r @ r)

    best = None
    for K in range(1, max_seg + 1):
        # DP: cost[k][j] = min over split of cost[k-1][i] + sse(i,j)
        INF = float("inf")
        cost = np.full((K + 1, n + 1), INF)
        prev = np.zeros((K + 1, n + 1), dtype=np.int64)
        cost[0][0] = 0.0
        for k in range(1, K + 1):
            for j in range(k, n + 1):
                for i in range(k - 1, j):
                    if cost[k - 1][i] == INF:
                        continue
                    c = cost[k - 1][i] + seg_sse(i, j)
                    if c < cost[k][j]:
                        cost[k][j] = c
                        prev[k][j] = i
        sse = cost[K][n]
        if sse == INF:
            continue
        bic = n * np.log(max(sse / n, 1e-12)) + 2 * K * np.log(n)
        # 还原断点
        cuts = []
        j = n
        for k in range(K, 0, -1):
            i = int(prev[k][j])
            cuts.append((i, j))
            j = i
        cuts.reverse()
        segs = []
        for i, j in cuts:
            tt, yy = t[i:j], y[i:j]
            A = np.column_stack([np.ones_like(tt), tt])
            sol, *_ = np.linalg.lstsq(A, yy, rcond=None)
            segs.append({"t0_min": round(float(tt[0] / 60.0), 2),
                         "t1_min": round(float(tt[-1] / 60.0), 2),
                         "slope_ppm": round(float(sol[1] / SR * 1e6), 3),
                         "start_samples": round(float(sol[0] + sol[1] * tt[0]), 1),
                         "end_samples": round(float(sol[0] + sol[1] * tt[-1]), 1)})
        cand = {"K": K, "sse": sse, "bic": float(bic), "segments": segs}
        if best is None or cand["bic"] < best["bic"]:
            best = cand
    best["ok"] = True
    return best


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOT.mkdir(parents=True, exist_ok=True)
    data = load_json()
    recs = data["tracks"]
    ana = {(a["stem"], a["target"]): a for a in data["analysis"]}
    curves = {k: curve_from(v["anchors"]) for k, v in ana.items() if v["anchors"]}
    out_json = {"segments": {}, "rolling_rate": {}, "band_analysis": {},
                "noise_floor": {}, "residual_stats": {}}

    # ---------------- A: rolling rate + segmented fit
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    colors = {2: "#1f77b4", 3: "#d62728"}
    for row, stem in enumerate(["20260919_C1305", "20260919_C1306"]):
        for target in (2, 3):
            a = ana[(stem, target)]
            anchors = a["anchors"]
            rr = rolling_rate(anchors)
            seg = segmented_fit(anchors)
            out_json["rolling_rate"][f"{stem}_CH{target + 1}"] = rr
            out_json["segments"][f"{stem}_CH{target + 1}"] = seg
            ax = axes[row][0]
            if rr:
                xs = [p[0] for p in rr]
                ys = [p[1] for p in rr]
                ax.plot(xs, ys, color=colors[target], lw=1.6,
                        label=f"CH{target + 1} rolling rate")
            ax2 = axes[row][1]
            if seg.get("ok"):
                for s in seg["segments"]:
                    ax2.plot([s["t0_min"], s["t1_min"]],
                             [s["start_samples"], s["end_samples"]],
                             color=colors[target], lw=2.4,
                             label=(f"CH{target + 1} seg {s['slope_ppm']:+.2f} ppm"
                                    if s is seg["segments"][0] else None))
                    ax2.axvline(s["t0_min"], color=colors[target], ls=":",
                                lw=0.8, alpha=0.6)
                ax2.text(0.985, 0.03 if target == 2 else 0.15,
                         f"CH{target + 1}: K={seg['K']}",
                         transform=ax2.transAxes, ha="right", va="bottom",
                         fontsize=8, color=colors[target])
                ax2.scatter([p[0] / 60.0 for p in anchors],
                            [p[1] for p in anchors], s=8,
                            color=colors[target], alpha=0.5)
        for ax, title, ylab in ((axes[row][0],
                                 f"{stem}: rolling 10-min drift rate",
                                 "rate [ppm]"),
                                (axes[row][1],
                                 f"{stem}: piecewise-linear fit (BIC breakpoints)",
                                 "delay vs CH1 [samples]")):
            ax.set_title(title)
            ax.set_ylabel(ylab)
            ax.set_xlabel("time [min]")
            ax.grid(alpha=0.25)
            ax.legend(loc="upper left", fontsize=7)
    fig.suptitle("Drift timing analysis: rolling rate and segmented fits", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = PLOT / "drift_analysis_a.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")

    # ---------------- B: residual histogram / cc scatter / band slopes / noise floor
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    # residual histogram + cc scatter from tracker points
    for stem, target, color in (("20260919_C1305", 3, "#d62728"),
                                ("20260919_C1306", 2, "#1f77b4")):
        rec = next(r for r in recs if r["stem"] == stem and r["target"] == target)
        f = curves[(stem, target)]
        res, ccs = [], []
        for r in rec["traj"]:
            if not r["accepted"]:
                continue
            res.append(r["lag"] - float(f(r["t_min"] * 60.0)))
            ccs.append(r["cc"])
        res = np.asarray(res)
        ccs = np.asarray(ccs)
        ok = np.abs(res) < 400
        axes[0][0].hist(res[ok], bins=60, alpha=0.55, color=color,
                        label=f"{stem[-4:]} CH{target + 1}")
        axes[0][1].scatter(ccs[ok], np.abs(res[ok]), s=6, alpha=0.35, color=color,
                           label=f"{stem[-4:]} CH{target + 1}")
        out_json["residual_stats"][f"{stem}_CH{target + 1}"] = {
            "median_abs": float(np.median(np.abs(res[ok]))),
            "p90_abs": float(np.percentile(np.abs(res[ok]), 90)),
            "max_abs": float(np.max(np.abs(res[ok]))),
        }
    axes[0][0].set_title("tracker residual after anchor curve (bins)")
    axes[0][0].set_xlabel("residual [samples]")
    axes[0][0].set_ylabel("windows")
    axes[0][0].legend(fontsize=7)
    axes[0][0].grid(alpha=0.25)
    axes[0][1].set_title("|residual| vs coherence |CC|")
    axes[0][1].set_xlabel("|CC|")
    axes[0][1].set_ylabel("|residual| [samples]")
    axes[0][1].set_yscale("log")
    axes[0][1].legend(fontsize=7)
    axes[0][1].grid(alpha=0.25)

    # band analysis: per 5-min block, band delays around curve
    band_curves = {}
    for stem in ["20260919_C1305", "20260919_C1306"]:
        for target in (2, 3):
            ref = np.memmap(PCM / stem / "ch0_12k.f32", dtype=np.float32, mode="r")
            tgt = np.memmap(PCM / stem / f"ch{target}_12k.f32", dtype=np.float32,
                            mode="r")
            n = min(len(ref), len(tgt))
            block = int(300 * SR12)
            hops = int(150 * SR12)
            f = curves[(stem, target)]
            rows = []
            pos = 0
            while pos + block <= n:
                t_s = pos / SR12
                center = float(f(t_s + 150.0))
                peaks = band_peaks(np.asarray(ref[pos:pos + block]),
                                   np.asarray(tgt[pos:pos + block]),
                                   center, radius48=360)
                rows.append({"t_min": round(t_s / 60.0, 2),
                             **{k: [None if not np.isfinite(v[0]) else int(v[0]),
                                    round(v[1], 3)] for k, v in peaks.items()}})
                pos += hops
            band_curves[(stem, target)] = rows
            out_json["band_analysis"][f"{stem}_CH{target + 1}"] = rows
            del ref, tgt
    ax = axes[1][0]
    style = {"low": ("-", 1.8), "mid": ("--", 1.2), "high": (":", 1.2)}
    for (stem, target), rows in band_curves.items():
        if stem.endswith("C1305") and target != 3:
            continue
        if stem.endswith("C1306") and target != 2:
            continue
        t = [r["t_min"] for r in rows]
        for band in ("low", "mid", "high"):
            ys = [r[band][0] if r[band][0] is not None else np.nan for r in rows]
            ax.plot(t, ys, style[band][0], lw=style[band][1], alpha=0.8,
                    label=f"{stem[-4:]} CH{target + 1} {band}")
    # 数值化: 每个通道/频带的稳健斜率
    band_slopes = {}
    for key, rows in band_curves.items():
        for band in ("low", "mid", "high"):
            pts = [(r["t_min"] * 60.0, r[band][0]) for r in rows
                   if r[band][0] is not None]
            if len(pts) < 5:
                continue
            tt = np.array([p[0] for p in pts], dtype=np.float64)
            yy = np.array([p[1] for p in pts], dtype=np.float64)
            slopes = []
            for i in range(len(tt)):
                for j in range(i + 1, len(tt)):
                    dt = tt[j] - tt[i]
                    if dt > 0:
                        slopes.append((yy[j] - yy[i]) / dt)
            band_slopes[f"{key[0]}_CH{key[1] + 1}_{band}"] = round(
                float(np.median(slopes) / SR * 1e6), 3)
    out_json["band_slopes_ppm"] = band_slopes
    ax.set_title("per-band delay over 5-min blocks (clock drift is band-flat)")
    ax.set_xlabel("time [min]")
    ax.set_ylabel("delay vs CH1 [samples]")
    ax.legend(fontsize=6, ncol=2)
    ax.grid(alpha=0.25)

    # noise floor vs window length: inlier fraction within +-2 samples
    ax = axes[1][1]
    lengths = [10, 20, 40, 80]
    noise = {}
    rng = np.random.default_rng(0)
    # 控制通道 CH2 还没解码过 (此前只解码了 CH1/CH3/CH4), 补上
    import subprocess as _sp
    for _stem in ('20260919_C1305', '20260919_C1306'):
        _out = PCM / _stem / 'ch1.f32'
        if not _out.is_file():
            _cmd = [str(REPO / 'tools' / 'ffmpeg.exe'), '-v', 'error', '-nostdin',
                    '-y', '-i', str(REPO / 'data' / 'align_4ch_proof' / f'{_stem}.MP4'),
                    '-map', '0:a:1', '-vn', '-sn', '-dn', '-f', 'f32le',
                    '-ar', str(SR), '-ac', '1', str(_out)]
            _sp.run(_cmd, capture_output=True)
            print(f'  decoded control channel {_stem} ch1')
    series = [
        ("20260919_C1305", 1, "#2ca02c", lambda sec: -4.0),   # CH1<->CH2 control
        ("20260919_C1305", 3, "#d62728", curves[("20260919_C1305", 3)]),
        ("20260919_C1306", 2, "#1f77b4", curves[("20260919_C1306", 2)]),
    ]
    for stem, target, color, f in series:
        ref = np.memmap(PCM / stem / "ch0.f32", dtype=np.float32, mode="r")
        tgt = np.memmap(PCM / stem / f"ch{target}.f32", dtype=np.float32, mode="r")
        n = min(len(ref), len(tgt))
        fracs, meds, fracs10 = [], [], []
        for L in lengths:
            w = int(L * SR)
            n_pos = 16
            pos = rng.integers(0, max(1, n - w), n_pos)
            devs = []
            for s in pos:
                a = np.asarray(ref[s:s + w], dtype=np.float64)
                b = np.asarray(tgt[s:s + w], dtype=np.float64)
                a -= a.mean()
                b -= b.mean()
                nfft = int(1 << int(np.ceil(np.log2(2 * w))))
                surf = np.fft.irfft(np.fft.rfft(a, nfft) *
                                    np.conj(np.fft.rfft(b, nfft)), nfft)
                cand = np.concatenate([surf[:MAX_LAG + 1], surf[-MAX_LAG:]])
                den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
                idx = int(np.argmax(np.abs(cand)))
                m = idx if idx <= MAX_LAG else idx - (2 * MAX_LAG + 1)
                lag = -m
                peak = float(np.abs(cand[idx]) / den)
                expected = float(f((s + w / 2) / SR))
                if peak >= 0.10:
                    devs.append(lag - expected)
            devs = np.asarray(devs, dtype=np.float64)
            if devs.size:
                frac10 = float(np.mean(np.abs(devs) <= 10.0) * 100.0)
                frac = float(np.mean(np.abs(devs) <= 2.0) * 100.0)
                med = float(np.median(np.abs(devs[np.abs(devs) <= 2.0]))) \
                    if np.any(np.abs(devs) <= 2.0) else np.nan
            else:
                frac, med = np.nan, np.nan
            fracs.append(frac)
            meds.append(med)
            fracs10.append(frac10)
        noise[f"{stem}_CH{target + 1}"] = {
            "lengths_s": lengths, "inlier_pct_within_2_samples": fracs,
            "inlier_pct_within_10_samples": fracs10,
            "median_inlier_abs_dev": meds, "n_positions": 16}
        ax.plot(lengths, fracs, "o-", color=color,
                label=f"{stem[-4:]} CH{target + 1} (control)" if target == 1
                else f"{stem[-4:]} CH{target + 1}")
        del ref, tgt
    ax.set_xscale("log")
    ax.set_ylim(0, 105)
    ax.set_title("fraction of positions within +-2 samples of expected (16 positions)")
    ax.set_xlabel("window length [s]")
    ax.set_ylabel("inliers [%]")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=7)
    out_json["noise_floor"] = noise

    fig.tight_layout()
    p = PLOT / "drift_analysis_b.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")

    # ---------------- C: pairwise coherence heatmap
    fig, axes = plt.subplots(1, 2, figsize=(16, 4.8))
    pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    for ax, stem in zip(axes, ["20260919_C1305", "20260919_C1306"]):
        ch = {}
        for idx in (0, 1, 2, 3):
            pth = PCM / stem / f"ch{idx}_12k.f32"
            if pth.is_file():
                ch[idx] = np.memmap(pth, dtype=np.float32, mode="r")
        n = min(len(v) for v in ch.values())
        win = int(60 * SR12)
        hop = int(30 * SR12)
        starts = list(range(0, n - win, hop))
        mat = np.zeros((len(pairs), len(starts)), dtype=np.float32)
        for pi, (i, j) in enumerate(pairs):
            if i not in ch or j not in ch:
                continue
            a = ch[i]
            b = ch[j]
            for k, s in enumerate(starts):
                aa = np.asarray(a[s:s + win], dtype=np.float64)
                bb = np.asarray(b[s:s + win], dtype=np.float64)
                aa -= aa.mean()
                bb -= bb.mean()
                nfft = int(1 << int(np.ceil(np.log2(2 * win))))
                surf = np.fft.irfft(np.fft.rfft(aa, nfft) *
                                    np.conj(np.fft.rfft(bb, nfft)), nfft)
                den = np.linalg.norm(aa) * np.linalg.norm(bb) + 1e-12
                # 双向 lag: 延迟可正可负 (CH1<->CH2 为 -4 采样), 只扫正 lag 会漏掉
                L = 400
                cand = np.concatenate([surf[:L + 1], surf[-L:]])
                mat[pi, k] = float(np.max(np.abs(cand)) / den)
        coh_summary = {}
        for pi, (i, j) in enumerate(pairs):
            row = mat[pi]
            coh_summary[f"CH{i + 1}-CH{j + 1}"] = {
                "mean": round(float(np.mean(row)), 3),
                "p90": round(float(np.percentile(row, 90)), 3),
                "frac_gt_0.25": round(float(np.mean(row > 0.25)), 3),
                "frac_gt_0.5": round(float(np.mean(row > 0.5)), 3),
            }
        out_json.setdefault("coherence", {})[stem] = coh_summary
        im = ax.imshow(mat, aspect="auto", origin="lower", cmap="viridis",
                       vmin=0, vmax=1.0,
                       extent=[0, n / SR12 / 60.0, -0.5, len(pairs) - 0.5])
        ax.set_yticks(range(len(pairs)))
        ax.set_yticklabels([f"CH{i + 1}-CH{j + 1}" for i, j in pairs])
        ax.set_xlabel("time [min]")
        ax.set_title(f"{stem}: pairwise |CC| (60 s windows)")
        fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01)
        for idx in ch.values():
            del idx
    fig.tight_layout()
    p = PLOT / "coherence_heatmap.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"已写 {p}")

    (SCRATCH / "drift_analysis.json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写 {SCRATCH / 'drift_analysis.json'}")

    # 终端摘要
    print("\n=== 分段拟合 (BIC) ===")
    for k, v in out_json["segments"].items():
        if not v.get("ok"):
            continue
        seg_txt = "; ".join(f"[{s['t0_min']:.0f}-{s['t1_min']:.0f}min "
                            f"{s['slope_ppm']:+.2f}ppm]" for s in v["segments"])
        print(f"  {k}: K={v['K']} {seg_txt}")
    print("\n=== 分频带斜率 (ppm) ===")
    for k, v in sorted(out_json.get("band_slopes_ppm", {}).items()):
        print(f"  {k}: {v:+.2f}")
    print("\n=== 通道对相干度 (60 s 窗) ===")
    for stem, d in (out_json.get("coherence") or {}).items():
        parts = [f"{k}:mean{v['mean']:.2f}(>{0.25} {v['frac_gt_0.25']:.0%})"
                 for k, v in d.items()]
        print(f"  {stem}: " + "; ".join(parts))
    print("\n=== ±2 采样内位置比例 (%) 与中位内点偏差 ===")
    for k, v in noise.items():
        print(f"  {k}: " + ", ".join(
            f"{L}s={('- ' if p is None or (isinstance(p, float) and np.isnan(p)) else round(p, 0))}%"
            for L, p in zip(v["lengths_s"], v["inlier_pct_within_2_samples"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
