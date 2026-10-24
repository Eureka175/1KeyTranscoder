#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parallel drift rebuild + plotting (v2).

Parallelisation (partial, where it pays):
  * channel decoding / decimation: 6 independent jobs (4 processes);
  * tracking: 4 independent jobs — one per (take, target) — each of which must stay
    sequential internally because the continuity tracker follows the previous lag.
  * plotting is single-threaded (it is milliseconds).

Robust drift rate: the previous version least-squares-fitted the raw tracker points,
which still contain content-driven jumps, producing nonsense rates (-218k ppm). This
version fits the Theil-Sen line on the 2-minute weighted-median anchors only, and
also reports the early swing, so the two regimes of C1305 CH4 (drift then plateau)
are both visible.

Outputs (F:, because E: is currently disconnected):
  data/_scratch/drift_rebuild.json
  docs/audio-sync-p4/plots/drift_overview.png
  docs/audio-sync-p4/plots/drift_<stem>.png
"""
from __future__ import annotations

import json
import subprocess
import time
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import signal

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))
import long_align as la  # noqa: E402

SR = 48000
DEC = 4
WIN_S = 60.0
HOP_S = 10.0
SEARCH0 = 3000
RADIUS_BASE = 96
RADIUS_PER_MIN = 8
MIN_CC = 0.06
BIN_S = 120.0
ANCHOR = 0
WORKERS = 4

CLIPS = {
    "20260919_C1305": REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    "20260919_C1306": REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
}
SCRATCH = REPO / "data" / "_scratch" / "pcm"
STORE = REPO / "data" / "_scratch" / "drift_rebuild.json"
PLOTDIR = REPO / "docs" / "audio-sync-p4" / "plots"


# ---------------------------------------------------------------- worker fns
def _decode_job(job: tuple[str, int]) -> tuple[str, int, str]:
    stem, idx = job
    d = SCRATCH / stem
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"ch{idx}.f32"
    if out.is_file() and out.stat().st_size > 0:
        return stem, idx, str(out)
    src = CLIPS[stem]
    cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(src),
           "-map", f"0:a:{idx}", "-vn", "-sn", "-dn",
           "-f", "f32le", "-ar", str(SR), "-ac", "1", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"decode {stem} a:{idx} failed: {r.stderr[-300:]}")
    return stem, idx, str(out)


def _load(stem: str, i: int) -> np.memmap:
    return np.memmap(SCRATCH / stem / f"ch{i}.f32", dtype=np.float32, mode="r")


def _decim_full(stem: str, i: int) -> np.ndarray:
    m = _load(stem, i)
    parts, CH = [], 480_000
    for s in range(0, len(m), CH):
        x = np.asarray(m[s:s + CH], dtype=np.float32)
        parts.append(signal.resample_poly(x, 1, DEC).astype(np.float32))
    return np.concatenate(parts)


def _cc_peak_12k(a12: np.ndarray, b12: np.ndarray, center48: int,
                 radius48: int) -> tuple[int, float]:
    n = min(len(a12), len(b12))
    a = a12[:n].astype(np.float64)
    b = b12[:n].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    surf = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    search = int((abs(center48) + radius48) / DEC) + 2
    cand = np.abs(np.concatenate([surf[:search + 1], surf[-search:]]) / den)
    # 向量化的 signed-lag 映射: 前 search+1 个是正 lag, 后 search 个是负 lag
    m = np.concatenate([np.arange(0, search + 1),
                        np.arange(-search, 0)]).astype(np.int64)
    lag48 = -m * DEC
    mask = (lag48 >= center48 - radius48) & (lag48 <= center48 + radius48)
    if not mask.any():
        return int(center48), 0.0
    cand = np.where(mask, cand, -1.0)
    i = int(np.argmax(cand))
    return int(lag48[i]), float(cand[i])


def _refine(stem: str, target: int, s: int, coarse: int, radius: int = 8):
    a = _load(stem, ANCHOR)
    b = _load(stem, target)
    aa = np.asarray(a[s:s + la.WIN], dtype=np.float32)
    bb = np.asarray(b[s:s + la.WIN], dtype=np.float32)
    return la.refine_48k(aa, bb, coarse, radius=radius)


def _decim_job(job: tuple[str, int]) -> tuple[str, int, float]:
    """Decimate one channel to 12 kHz and cache it on F:."""
    stem, idx = job
    t0 = time.perf_counter()
    out = SCRATCH / stem / f"ch{idx}_12k.f32"
    if out.is_file() and out.stat().st_size > 0:
        return stem, idx, 0.0
    arr = _decim_full(stem, idx)
    np.asarray(arr, dtype=np.float32).tofile(out)
    del arr
    return stem, idx, round(time.perf_counter() - t0, 2)


def _load12(stem: str, idx: int) -> np.memmap:
    return np.memmap(SCRATCH / stem / f"ch{idx}_12k.f32", dtype=np.float32,
                     mode="r")


def _track_job(job: tuple[str, int]) -> dict:
    """Continuity tracking for one (take, target); sequential inside by design."""
    t_job = time.perf_counter()
    stem, target = job
    dec_ref = _load12(stem, ANCHOR)
    dec_tgt = _load12(stem, target)
    n12 = min(len(dec_ref), len(dec_tgt))
    dur_s = n12 / (SR / DEC)
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    n_win = max(0, (int(dur_s * SR) - win) // hop + 1)
    i0 = int(120 * SR / DEC)
    lag0, cc0 = _cc_peak_12k(dec_ref[:i0], dec_tgt[:i0], 0, SEARCH0)
    lag0, q0 = _refine(stem, target, 0, lag0, radius=12)
    traj = []
    prev = lag0
    for w in range(n_win):
        s = w * hop
        t_min = s / SR / 60.0
        radius = int(RADIUS_BASE + RADIUS_PER_MIN * t_min)
        s12 = int(s / DEC)
        w12 = int(win / DEC)
        if s12 + w12 > n12:
            break
        lag, cc = _cc_peak_12k(dec_ref[s12:s12 + w12],
                               dec_tgt[s12:s12 + w12], prev, radius)
        acc = cc >= MIN_CC
        if acc:
            lag, q = _refine(stem, target, s, lag, radius=8)
            cc = min(cc, 1.0) * max(q, 0.0)
            prev = lag
        traj.append({"w": w, "t_min": round(t_min, 3), "lag": int(lag),
                     "cc": round(float(cc), 4), "accepted": bool(acc)})
    n_acc = sum(1 for r in traj if r["accepted"])
    return {"stem": stem, "target": target, "initial_lag": int(lag0),
            "n_win": len(traj), "n_accepted": n_acc, "traj": traj,
            "elapsed_s": round(time.perf_counter() - t_job, 2)}


# ---------------------------------------------------------------- analysis
def anchors_from(traj: list[dict]) -> list[list[float]]:
    rows = [r for r in traj if r["accepted"] and r["cc"] >= 0.08]
    bins: dict[int, list] = {}
    for r in rows:
        bins.setdefault(int(r["t_min"] * 60.0 // BIN_S), []).append(r)
    anchors = []
    for b in sorted(bins):
        rs = sorted(bins[b], key=lambda r: (r["lag"], -r["cc"]))
        ys = np.array([r["lag"] for r in rs], dtype=np.float64)
        w = np.array([r["cc"] for r in rs], dtype=np.float64)
        order = np.argsort(ys)
        ys, w = ys[order], w[order]
        cum = np.cumsum(w)
        anchors.append([b * BIN_S, float(ys[np.searchsorted(cum, cum[-1] / 2.0)])])
    if not anchors:
        return anchors
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    # 中值滤波去孤立跳变 (窗口 7 个锚点 = 14 min)
    if y.size >= 7:
        pad = np.pad(y, 3, mode="edge")
        y = np.array([np.median(pad[k:k + 7]) for k in range(y.size)])
    # 每 2-min 锚点最多移动 30 采样 (=0.25 ms/min = 250 ppm), 允许真实漂移
    lim = 30.0
    for k in range(1, y.size):
        d = y[k] - y[k - 1]
        if abs(d) > lim:
            y[k] = y[k - 1] + np.sign(d) * lim
    for k, a in enumerate(anchors):
        a[1] = float(y[k])
    return anchors


def theil_sen(x: np.ndarray, y: np.ndarray, n_pairs: int = 20000,
              seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(x)
    if n < 3:
        return float(np.nan), float(np.nan)
    if n * (n - 1) // 2 <= n_pairs:
        idx = np.array([(i, j) for i in range(n) for j in range(i + 1, n)])
    else:
        idx = np.column_stack([rng.integers(0, n, n_pairs),
                               rng.integers(0, n, n_pairs)])
    dt = x[idx[:, 1]] - x[idx[:, 0]]
    ok = np.abs(dt) > 1.0
    if not ok.any():
        return float(np.median(y)), 0.0
    slopes = (y[idx[ok, 1]] - y[idx[ok, 0]]) / dt[ok]
    b = float(np.median(slopes))
    a = float(np.median(y - b * x))
    return a, b


def analyse(rec: dict) -> dict:
    anchors = anchors_from(rec["traj"])
    # anchors[0] 已经是秒; 速率 = 采样/采样 -> ppm *1e6
    t = np.array([a[0] for a in anchors], dtype=np.float64)
    y = np.array([a[1] for a in anchors], dtype=np.float64)
    out = {"stem": rec["stem"], "target": rec["target"],
           "n_win": rec["n_win"], "n_accepted": rec["n_accepted"],
           "n_anchors": len(anchors), "anchors": anchors}
    if len(anchors) >= 4:
        # 全片速率: 抗离群 Theil-Sen on anchors
        a_all, b_all = theil_sen(t, y)
        out["rate_ppm_all"] = round(b_all / SR * 1e6, 3)
        # 前 40 分钟速率
        m = t <= 40 * 60.0
        if m.sum() >= 4:
            _, b40 = theil_sen(t[m], y[m])
            out["rate_ppm_first40"] = round(b40 / SR * 1e6, 3)
        else:
            out["rate_ppm_first40"] = None
        # 早段摆动与末段稳定性
        out["swing_samples"] = int(round(float(y.max() - y.min())))
        out["swing_ms"] = round(float(y.max() - y.min()) / SR * 1000.0, 3)
        out["start_samples"] = int(round(float(y[0])))
        out["end_samples"] = int(round(float(y[-1])))
        # 前 20 分钟 vs 后 20 分钟的中位值, 判断"漂移后趋稳"
        early = y[t <= 20 * 60.0]
        late = y[t >= 40 * 60.0]
        out["early_median"] = int(round(float(np.median(early)))) if early.size else None
        out["late_median"] = int(round(float(np.median(late)))) if late.size else None
    return out


# ---------------------------------------------------------------- plotting
def main() -> int:
    import argparse
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true",
                    help="reload cached tracks from drift_rebuild.json")
    args = ap.parse_args()
    PLOTDIR.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    if args.reuse and STORE.is_file():
        print("reusing cached tracks ...", flush=True)
        recs = json.loads(STORE.read_text(encoding="utf-8"))["tracks"]
    else:
        t0 = time.perf_counter()
        print(f"parallel decode ({2 * 3} jobs, {WORKERS} workers) ...", flush=True)
        jobs = [(stem, idx) for stem in CLIPS for idx in (ANCHOR, 2, 3)]
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            for stem, idx, path in ex.map(_decode_job, jobs):
                print(f"  {stem} ch{idx}: {Path(path).stat().st_size / 2**30:.2f} GiB")
        print(f"  decode done in {time.perf_counter() - t0:.1f}s", flush=True)

        t_dec = time.perf_counter()
        print(f"parallel 12 kHz decimation ({len(CLIPS) * 3} jobs, "
              f"{WORKERS} workers) ...", flush=True)
        dec_jobs = [(stem, idx) for stem in CLIPS for idx in (ANCHOR, 2, 3)]
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            for stem, idx, sec in ex.map(_decim_job, dec_jobs):
                print(f"  {stem} ch{idx}_12k: {sec:.1f}s", flush=True)
        print(f"  decimation done in {time.perf_counter() - t_dec:.1f}s", flush=True)

        t1 = time.perf_counter()
        print(f"parallel tracking ({len(CLIPS) * 2} jobs, {WORKERS} workers) ...",
              flush=True)
        track_jobs = [(stem, target) for stem in CLIPS for target in (2, 3)]
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            recs = list(ex.map(_track_job, track_jobs))
        wall = time.perf_counter() - t1
        seq = sum(r.get("elapsed_s", 0.0) for r in recs)
        print(f"  tracking done in {wall:.1f}s wall (sequential estimate {seq:.1f}s, "
              f"speed-up {seq / wall:.2f}x with {WORKERS} workers)", flush=True)
    analyses = [analyse(r) for r in recs]
    STORE.write_text(json.dumps({"tracks": recs, "analysis": analyses},
                                ensure_ascii=False), encoding="utf-8")
    print(f"已写 {STORE}")

    by = {(a["stem"], a["target"]): a for a in analyses}
    for a in analyses:
        print(f"  {a['stem']} CH{a['target'] + 1}: accepted {a['n_accepted']}/"
              f"{a['n_win']}, anchors {a['n_anchors']}, "
              f"rate(all)={a.get('rate_ppm_all')} ppm, "
              f"rate(0-40min)={a.get('rate_ppm_first40')} ppm, "
              f"swing={a.get('swing_samples')} samples")

    colors = {2: "#1f77b4", 3: "#d62728"}
    fig, axes = plt.subplots(2, 2, figsize=(15, 8.5), sharex="row")
    for row, stem in enumerate(CLIPS):
        n = (_load(stem, 0)).shape[0]
        for target in (2, 3):
            rec = next(r for r in recs if r["stem"] == stem and r["target"] == target)
            a = by[(stem, target)]
            tr = rec["traj"]
            t = np.array([r["t_min"] for r in tr if r["accepted"]])
            lag = np.array([r["lag"] for r in tr if r["accepted"]])
            cc = np.array([r["cc"] for r in tr if r["accepted"]])
            ax = axes[row][0]
            ax.scatter(t, lag, c=cc, cmap="viridis", s=6 + 20 * cc,
                       alpha=0.75, linewidths=0,
                       label=f"CH{target + 1} measured (colour=|CC|)")
            anc = np.array(a["anchors"]) if a["anchors"] else np.zeros((0, 2))
            if anc.size:
                ax.plot(anc[:, 0] / 60.0, anc[:, 1], color=colors[target],
                        lw=2.4, marker="o", ms=3,
                        label=f"CH{target + 1} 2-min anchors / applied")
            txt = (f"CH{target + 1}: swing {a.get('swing_samples')} smp "
                   f"({a.get('swing_ms')} ms)\n"
                   f"rate 0-40min {a.get('rate_ppm_first40')} ppm, "
                   f"all {a.get('rate_ppm_all')} ppm")
            ax.text(0.985, 0.03 if target == 2 else 0.13, txt,
                    transform=ax.transAxes, ha="right", va="bottom",
                    fontsize=7.5, color=colors[target])
            ax2 = axes[row][1]
            if anc.size:
                grid = np.arange(0, n, SR, dtype=np.float64)
                curve = np.interp(grid, anc[:, 0] * 60.0 * SR, anc[:, 1])
                resid, rt = [], []
                for r in tr:
                    if not r["accepted"]:
                        continue
                    c = float(np.interp(r["t_min"] * 60.0 * SR, grid, curve))
                    resid.append(r["lag"] - c)
                    rt.append(r["t_min"])
                ax2.scatter(rt, resid, c=colors[target], s=6, alpha=0.55,
                            label=f"CH{target + 1} residual")
        ax = axes[row][0]
        ax.axhline(-4, color="gray", ls="--", lw=1, label="CH2 = -4 (constant)")
        ax.set_ylabel("delay vs CH1 [samples]")
        ax.set_title(f"{stem}: measured 4CH drift vs CH1")
        ax.grid(alpha=0.25)
        ax.legend(loc="lower left", fontsize=7)
        ax2 = axes[row][1]
        ax2.axhline(0, color="k", lw=0.8)
        ax2.axhspan(-48, 48, color="gray", alpha=0.15, label="+-1 ms")
        ax2.set_ylabel("residual vs anchor curve [samples]")
        ax2.set_title(f"{stem}: tracked points - anchor curve")
        ax2.grid(alpha=0.25)
        ax2.legend(loc="upper left", fontsize=7)
        for a_ in (ax, ax2):
            a_.set_xlabel("time [min]")
    fig.suptitle("Long-take 4CH drift (rebuilt from F: sources): "
                 "measured delay, 2-min anchors, applied correction", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = PLOTDIR / "drift_overview.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"已写 {out} ({out.stat().st_size / 1024:.0f} KiB)")

    for stem in CLIPS:
        n = (_load(stem, 0)).shape[0]
        fig, ax = plt.subplots(figsize=(13, 5))
        for target in (2, 3):
            rec = next(r for r in recs if r["stem"] == stem and r["target"] == target)
            a = by[(stem, target)]
            tr = rec["traj"]
            t = np.array([r["t_min"] for r in tr if r["accepted"]])
            lag = np.array([r["lag"] for r in tr if r["accepted"]])
            cc = np.array([r["cc"] for r in tr if r["accepted"]])
            ax.scatter(t, lag, c=cc, cmap="viridis", s=7 + 20 * cc,
                       alpha=0.75, linewidths=0,
                       label=f"CH{target + 1} measured (colour=|CC|)")
            anc = np.array(a["anchors"]) if a["anchors"] else np.zeros((0, 2))
            if anc.size:
                ax.plot(anc[:, 0] / 60.0, anc[:, 1], color=colors[target],
                        lw=2.4, marker="o", ms=3,
                        label=f"CH{target + 1} anchors / applied")
            ax.text(0.985, 0.03 if target == 2 else 0.14,
                    f"CH{target + 1}: swing {a.get('swing_samples')} samples "
                    f"({a.get('swing_ms')} ms), rate 0-40min "
                    f"{a.get('rate_ppm_first40')} ppm",
                    transform=ax.transAxes, ha="right", va="bottom",
                    fontsize=8, color=colors[target])
        ax.axhline(-4, color="gray", ls="--", lw=1, label="CH2 = -4 (constant)")
        ax.set_xlabel("time [min]")
        ax.set_ylabel("delay vs CH1 [samples]")
        ax.set_title(f"{stem}: 4CH drift and applied shift")
        ax.grid(alpha=0.25)
        ax.legend(loc="center left", fontsize=8)
        fig.tight_layout()
        p = PLOTDIR / f"drift_{stem}.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        print(f"已写 {p} ({p.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
