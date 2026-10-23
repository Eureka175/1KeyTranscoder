#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long_align.py — 长素材 4CH 常量延迟对齐（研究管线，目标 A）。

物理前提（已由容器事实确认）: 4 条 mono 流在同一文件、同一时钟、样本数完全相同
=> 通道间偏移是**常量整数延迟**，不需要逐窗时变修正。因此本管线只解 3 个整数
移位并把 4CH 绑到一起。

阶段:
  facts    读容器事实, 断言四轨等长同钟, 记录视频基线
  windows  60s 窗 / 20s 跳, 每对通道 5 个独立估计器 (全带/3 带 GCC-PHAT + 起音
           包络), 相邻估计器 ±2 采样一致才采纳
  solve    每对聚类出常量 -> 加权最小二乘求 3 个整数移位 + 环一致性 (同钟加法
           恒等式) 迭代剔离群; 自动选可信度最高的锚点
  apply    整数移位, |s| <= 4800 (100ms), 越界补 0; 输出 4ch WAV + 每轨 raw
  verify   对对齐结果重测: 可信窗残差、环残差、幂等性
  known    已知答案: 在真实长音频片段上注入 +977/-1234/+4700/0 等, 必须精确恢复;
           注入 >100ms 必须被拒绝
  remux    视频 stream copy + 4 轨对齐 PCM 重封装; 校验视频 streamhash 不变、
           无 VFR、音频采样数不变
  clips    5 个随机位置短片, 按原始关键帧(GOP)起点切割, 视频 copy 不重编码,
           总时长 <=60s
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import signal

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                       # noqa: E402

SR = 48000
PCM = Path(r"E:\1kt_scratch\pcm")
OUT = Path(r"E:\1kt_work\long_align")
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
GPAC_DIR = REPO / "tools" / "GPAC"

CLIPS = {
    "20260919_C1305": REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    "20260919_C1306": REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
}
WIN_S = 60.0
HOP_S = 20.0
MAX_SHIFT = 4800          # 100 ms
WIN = int(WIN_S * SR)
HOP = int(HOP_S * SR)
DEC = 4                   # 12 kHz
SEARCH12 = int(0.200 * SR / DEC)     # +-200 ms at 12 kHz
FINE_RADIUS = 8
MIN_METHODS = 2
CLUSTER_TOL = 2
BANDS = [(20.0, 1000.0), (1000.0, 3500.0), (3500.0, 5900.0)]


# ---------------------------------------------------------------- basic io
def pcm_path(stem: str, i: int, root: Path = PCM) -> Path:
    return root / stem / f"ch{i}.f32"


def load_ch(stem: str, i: int, root: Path = PCM) -> np.memmap:
    return np.memmap(pcm_path(stem, i, root), dtype=np.float32, mode="r")


def n_samples(stem: str, root: Path = PCM) -> int:
    return pcm_path(stem, 0, root).stat().st_size // 4


def decimate(x: np.ndarray, factor: int) -> np.ndarray:
    return signal.resample_poly(np.asarray(x, dtype=np.float32), 1, factor).astype(np.float32)


def signed_peak(surf: np.ndarray, search: int) -> tuple[int, float]:
    """Return (m, peak) where m in [-search, search] is the FFT-CC index."""
    cand = np.concatenate([surf[: search + 1], surf[-search:]])
    order = np.argsort(-np.abs(cand))
    idx = int(order[0])
    m = idx if idx <= search else idx - (2 * search + 1)
    peak = float(np.abs(cand[idx]))
    # 峰值/旁瓣比 (第二峰必须离主峰 >8 采样)
    runner = 0.0
    for j in order[1:]:
        if abs(int(j) - idx) > 8:
            runner = float(np.abs(cand[j]))
            break
    return m, peak / (runner + 1e-12)


def refine_48k(a: np.ndarray, b: np.ndarray, coarse: int,
               radius: int = FINE_RADIUS) -> tuple[int, float]:
    """用窗中心 2 s 段直接点积, 在 coarse +- radius 内精化到 48k 整数延迟。

    b[n] ~ a[n - delay] 的约定下, dot(a[k], b[k+m]) 的峰在 m = +delay。
    """
    seg = int(2.0 * SR)
    c0 = len(a) // 2 - seg // 2
    av = np.array(a[c0:c0 + seg], dtype=np.float64)
    av -= av.mean()
    na = np.linalg.norm(av)
    # 注意: 初值不能用 -1.0 当阈值 —— 完美相关时 |val| == 1.0, 严格大于永远
    # 不成立, 会静默返回 coarse 并附带一个假的 quality=1.0 (实测踩过)。
    best = (None, -1.0)             # type: ignore[assignment]
    for m in range(coarse - radius, coarse + radius + 1):
        b0 = c0 + m
        if b0 < 0 or b0 + seg > len(b):
            continue
        bv = np.array(b[b0:b0 + seg], dtype=np.float64)
        bv -= bv.mean()
        den = na * np.linalg.norm(bv)
        if den < 1e-9:
            continue
        val = float(np.dot(av, bv) / den)
        if best[0] is None or abs(val) > abs(best[1]):
            best = (m, val)
    if best[0] is None:
        return int(coarse), 0.0
    return int(best[0]), abs(float(best[1]))


def band_surfaces(a12: np.ndarray, b12: np.ndarray, nfft: int):
    """Return dict name -> (m, prominence) for full band and 3 sub-bands."""
    A = np.fft.rfft(a12 - a12.mean(), nfft)
    B = np.fft.rfft(b12 - b12.mean(), nfft)
    prod = A * np.conj(B)
    freqs = np.fft.rfftfreq(nfft, 1.0 / (SR / DEC))
    out = {}
    masks = {"full": np.ones_like(freqs)}
    for k, (lo, hi) in enumerate(BANDS):
        masks[f"band{k}"] = ((freqs >= lo) & (freqs <= hi)).astype(np.float64)
    for name, mask in masks.items():
        surf = np.fft.irfft(prod * mask, nfft)
        m, prom = signed_peak(surf, SEARCH12)
        out[name] = (-m * DEC, prom)     # 12k 峰在 -delay, 回到 48k 采样
    return out


def envelope(x: np.ndarray, hop: int = 480) -> np.ndarray:
    """100 Hz 起音包络: 每 10 ms 的 log 能量 (dimensionless)。"""
    n = (len(x) // hop) * hop
    frames = np.asarray(x[:n], dtype=np.float64).reshape(-1, hop)
    e = np.sqrt((frames * frames).mean(axis=1)) + 1e-9
    env = np.log(e)
    # 起音强度: 正差分 (对内容变化敏感, 对稳态无关)
    d = np.diff(env, prepend=env[0])
    return np.maximum(d, 0.0)


def envelope_delay(a: np.ndarray, b: np.ndarray) -> tuple[int, float] | None:
    ea, eb = envelope(a), envelope(b)
    n = min(len(ea), len(eb))
    if n < 1000:
        return None
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    A = np.fft.rfft(ea[:n] - ea[:n].mean(), nfft)
    B = np.fft.rfft(eb[:n] - eb[:n].mean(), nfft)
    surf = np.fft.irfft(A * np.conj(B), nfft)
    search = 20                              # +-200 ms at 100 Hz
    m, prom = signed_peak(surf, search)
    coarse = -m * 480                        # 10 ms frame -> 48k samples
    d, q = refine_48k(a, b, coarse, radius=480)
    if q <= 0:
        return None
    return d, q * min(prom, 100.0)


def window_estimates(a: np.ndarray, b: np.ndarray) -> dict:
    """One window: 5 candidate delays + agreement cluster."""
    cands: dict[str, tuple[int, float]] = {}
    a12 = decimate(a, DEC)
    b12 = decimate(b, DEC)
    n12 = min(len(a12), len(b12))
    a12, b12 = a12[:n12], b12[:n12]
    nfft = int(1 << int(np.ceil(np.log2(2 * n12))))
    for name, (coarse, prom) in band_surfaces(a12, b12, nfft).items():
        d, q = refine_48k(a, b, int(round(coarse)))
        cands[name] = (d, q * min(prom, 100.0))
    env = envelope_delay(a, b)
    if env is not None:
        cands["onset"] = env
    # 一致性: 找容纳 >=2 个估计器的 +-2 采样簇
    best = None
    items = list(cands.items())
    for name, (d, _q) in items:
        members = [(n, v) for n, v in items if abs(v[0] - d) <= CLUSTER_TOL]
        score = (len(members), sum(v[1] for _n, v in members))
        if best is None or score > best[0]:
            best = (score, members)
    if best is None:
        return {"cands": {}, "delay": None, "quality": 0.0, "n_methods": 0}
    members = best[1]
    delays = [v[0] for _n, v in members]
    quals = [v[1] for _n, v in members]
    d = int(np.median(delays))
    return {
        "cands": {n: [v[0], round(float(v[1]), 3)] for n, v in cands.items()},
        "delay": d,
        "quality": round(float(max(quals)), 3),
        "n_methods": len(members),
        "methods": sorted(n for n, _ in members),
        "spread": int(max(delays) - min(delays)),
    }


# ---------------------------------------------------------------- stages
def stage_facts(clips: dict[str, Path]) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    facts = {}
    for stem, clip in clips.items():
        doc = ar.probe(clip)
        audio = [s for s in doc["streams"] if s.get("codec_type") == "audio"]
        video = [s for s in doc["streams"] if s.get("codec_type") == "video"]
        durs = {int(s["duration_ts"]) for s in audio}
        facts[stem] = {
            "clip": str(clip),
            "n_audio": len(audio),
            "audio_sample_rate": sorted({int(s["sample_rate"]) for s in audio}),
            "audio_codec": sorted({s.get("codec_name") for s in audio}),
            "audio_duration_ts": sorted(durs),
            "audio_equal_length": len(durs) == 1,
            "video_count": len(video),
            "video_codec": [s.get("codec_name") for s in video],
            "video_nb_frames": [s.get("nb_frames") for s in video],
            "pcm_cache": [str(pcm_path(stem, i)) for i in range(len(audio))],
            "pcm_samples": n_samples(stem),
        }
        print(f"  {stem}: {len(audio)} audio tracks, dur_ts={sorted(durs)}, "
              f"equal={len(durs) == 1}, pcm={n_samples(stem)} samples")
    (OUT / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return facts


def _window_job(args: tuple) -> tuple:
    stem, i, j, w = args
    a_all = load_ch(stem, i)
    b_all = load_ch(stem, j)
    s = w * HOP
    a = np.asarray(a_all[s:s + WIN])
    b = np.asarray(b_all[s:s + WIN])
    est = window_estimates(a, b)
    est.update({"pair": [i, j], "window": w, "start_s": s / SR})
    return (i, j, w, est)


def stage_windows(stem: str, workers: int, max_windows: int = 0) -> dict:
    n = n_samples(stem)
    n_win = max(0, (n - WIN) // HOP + 1)
    if max_windows:
        n_win = min(n_win, max_windows)
    jobs = [(stem, i, j, w) for i in range(4) for j in range(i + 1, 4)
            for w in range(n_win)]
    print(f"  {stem}: {n_win} windows x 6 pairs = {len(jobs)} jobs, {workers} workers")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, (i, j, w, est) in enumerate(ex.map(_window_job, jobs, chunksize=4), 1):
            results.append(est)
            if k % 200 == 0:
                print(f"    {k}/{len(jobs)} ({time.time() - t0:.0f}s)", flush=True)
    (OUT / f"windows_{stem}.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8")
    good = sum(1 for r in results if r["delay"] is not None)
    print(f"  {stem}: done in {time.time() - t0:.0f}s, usable {good}/{len(results)}")
    return {"windows": results, "n_win": n_win}


def cluster_pair(rows: list[dict]) -> dict:
    vals = [(r["delay"], r["quality"]) for r in rows if r["delay"] is not None]
    if len(vals) < 3:
        return {"ok": False, "reason": "too_few_windows", "n": len(vals)}
    d = np.array([v[0] for v in vals], dtype=np.int64)
    q = np.array([v[1] for v in vals], dtype=np.float64)
    # 1 采样直方图 + +-2 平滑
    hist = {}
    for x in d:
        hist[int(x)] = hist.get(int(x), 0) + 1
    best = None
    for c in hist:
        support = sum(hist.get(c + k, 0) for k in range(-CLUSTER_TOL, CLUSTER_TOL + 1))
        score = support
        if best is None or score > best[0]:
            best = (score, c)
    if best is None:
        return {"ok": False, "reason": "no_cluster", "n": len(vals)}
    support, center = best
    inl = [(int(x), float(w)) for x, w in zip(d, q) if abs(int(x) - center) <= CLUSTER_TOL]
    inl.sort()
    # 加权中位数
    tot = sum(w for _x, w in inl)
    acc = 0.0
    med = inl[0][0]
    for x, w in inl:
        acc += w
        if acc >= tot / 2:
            med = x
            break
    spread = max(x for x, _ in inl) - min(x for x, _ in inl)
    return {"ok": True, "delay": int(med), "support": support, "n": len(vals),
            "support_frac": support / len(vals), "inlier_spread": int(spread),
            "coverage": len(inl) / len(vals)}


def stage_solve(stem: str) -> dict:
    raw = json.loads((OUT / f"windows_{stem}.json").read_text(encoding="utf-8"))
    pairs: dict[str, list] = {}
    for r in raw:
        pairs.setdefault(f"{r['pair'][0]}-{r['pair'][1]}", []).append(r)
    clusters = {}
    for key, rows in pairs.items():
        c = cluster_pair(rows)
        c["windows"] = len(rows)
        clusters[key] = c
        print(f"  pair {key}: ok={c.get('ok')} delay={c.get('delay')} "
              f"support={c.get('support')}/{c.get('n')} "
              f"spread={c.get('inlier_spread')}")
    # solve for each candidate anchor; pick the one with lowest residual cost
    best = None
    for anchor in range(4):
        sol = _solve_anchor(clusters, anchor)
        if sol is None:
            continue
        cost = sol["cost"]
        if best is None or cost < best[0]:
            best = (cost, sol)
    if best is None:
        raise SystemExit(f"{stem}: no consistent solution")
    sol = best[1]
    sol["pairs"] = clusters
    (OUT / f"solve_{stem}.json").write_text(
        json.dumps(sol, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  anchor=CH{sol['anchor'] + 1} shifts={sol['shifts']} "
          f"cost={sol['cost']:.2f} dropped={sol['dropped']}")
    return sol


def _solve_anchor(clusters: dict, anchor: int) -> dict | None:
    """Weighted LS for s_j - s_i = d(i->j), anchor fixed 0, then integer + outlier removal."""
    pairs = [(int(k.split("-")[0]), int(k.split("-")[1]), v)
             for k, v in clusters.items() if v.get("ok")]
    if not pairs:
        return None
    max_shift = MAX_SHIFT + 16
    for _round in range(6):
        # unknown order: channel indices except anchor
        idx = [c for c in range(4) if c != anchor]
        pos = {c: k for k, c in enumerate(idx)}
        rows, rhs, wts = [], [], []
        for i, j, v in pairs:
            row = np.zeros(len(idx))
            if i != anchor:
                row[pos[i]] -= 1.0
            if j != anchor:
                row[pos[j]] += 1.0
            rows.append(row)
            rhs.append(float(v["delay"]))
            wts.append(float(v.get("support", 1)))
        A = np.asarray(rows)
        b = np.asarray(rhs)
        w = np.asarray(wts)
        Aw = A * w[:, None]
        bw = b * w
        sol, *_ = np.linalg.lstsq(Aw, bw, rcond=None)
        shifts = {anchor: 0}
        for c in idx:
            shifts[c] = int(np.rint(sol[pos[c]]))
        resid = {f"{i}-{j}": float(shifts[j] - shifts[i] - v["delay"])
                 for i, j, v in pairs}
        bad = [k for k, r in resid.items() if abs(r) > 2.0]
        if bad:
            worst = max(bad, key=lambda k: abs(resid[k]))
            pairs = [(i, j, v) for i, j, v in pairs
                     if f"{i}-{j}" != worst]
            continue
        if any(abs(s) > max_shift for s in shifts.values()):
            return None
        residual = [abs(shifts[j] - shifts[i] - v["delay"]) for i, j, v in pairs]
        cost = float(np.sum(np.square(residual)))
        return {"anchor": int(anchor),
                "shifts": {str(k): v for k, v in sorted(shifts.items())},
                "residuals": resid, "cost": cost,
                "dropped": [k for k in clusters if k not in
                            [f"{i}-{j}" for i, j, _ in pairs]],
                "n_pairs": len(pairs)}
    return None



# ---------------------------------------------------------------- long baseline
def decim_full(stem: str, i: int, factor: int = DEC, chunk: int = 480_000,
               root: Path = PCM) -> np.ndarray:
    """Whole-file decimation to 12 kHz, chunked to bound memory."""
    m = load_ch(stem, i, root)
    n = len(m)
    parts = []
    for s in range(0, n, chunk):
        x = np.asarray(m[s:s + chunk], dtype=np.float32)
        parts.append(signal.resample_poly(x, 1, factor).astype(np.float32))
    return np.concatenate(parts)


def _surf_peaks(a12: np.ndarray, b12: np.ndarray, search: int) -> dict:
    nfft = int(1 << int(np.ceil(np.log2(2 * len(a12)))))
    A = np.fft.rfft(a12 - a12.mean(), nfft)
    B = np.fft.rfft(b12 - b12.mean(), nfft)
    prod = A * np.conj(B)
    freqs = np.fft.rfftfreq(nfft, 1.0 / (SR / DEC))
    out = {}
    masks = {
        "full": np.ones_like(freqs),
        "low": ((freqs >= 60) & (freqs <= 1500)).astype(np.float64),
        "mid": ((freqs >= 1500) & (freqs <= 5000)).astype(np.float64),
    }
    for name, mask in masks.items():
        surf = np.fft.irfft(prod * mask, nfft)
        m, prom = signed_peak(surf, search)
        out[name] = (-m * DEC, prom)
    # PHAT
    mag = np.abs(prod)
    phat = prod / (mag + 1e-12 * (mag.max() + 1e-30))
    surf = np.fft.irfft(phat, nfft)
    m, prom = signed_peak(surf, search)
    out["phat"] = (-m * DEC, prom)
    return out


def onset_whole(sig48: np.ndarray, hop: int = 480) -> np.ndarray:
    n = (len(sig48) // hop) * hop
    frames = np.asarray(sig48[:n], dtype=np.float64).reshape(-1, hop)
    e = np.sqrt((frames * frames).mean(axis=1)) + 1e-9
    env = np.log(e)
    d = np.diff(env, prepend=env[0])
    return np.maximum(d, 0.0)


def _refine_via_segments(a: np.ndarray, b: np.ndarray, coarse: int,
                         radius: int, n_seg: int = 20,
                         seg_len: int = SR) -> tuple[int, float]:
    """Refine a delay at 48 kHz by summing normalised dot products over several
    1 s segments sampled across the whole take (avoids edge/short-segment bias)."""
    n = min(len(a), len(b))
    starts = np.linspace(0, max(0, n - seg_len), n_seg).astype(np.int64)
    segs = [(int(s), np.array(a[s:s + seg_len], dtype=np.float64),
             np.array(b[s:s + seg_len], dtype=np.float64)) for s in starts]
    best = (None, -1.0)
    for m in range(coarse - radius, coarse + radius + 1):
        acc = 0.0
        cnt = 0
        for s, av, bv0 in segs:
            b0 = s + m
            if b0 < 0 or b0 + seg_len > n:
                continue
            bv = np.array(b[b0:b0 + seg_len], dtype=np.float64)
            av2 = av - av.mean()
            bv -= bv.mean()
            den = np.linalg.norm(av2) * np.linalg.norm(bv)
            if den < 1e-9:
                continue
            acc += float(np.dot(av2, bv) / den)
            cnt += 1
        if cnt == 0:
            continue
        val = acc / cnt
        if best[0] is None or abs(val) > abs(best[1]):
            best = (m, val)
    if best[0] is None:
        return int(coarse), 0.0
    return int(best[0]), abs(float(best[1]))


def stage_refine(stem: str, chunk_s: float = 480.0, hop_s: float = 240.0) -> dict:
    """Chunked long-baseline estimates + whole-file onset, per pair."""
    t0 = time.time()
    print(f"  {stem}: decimating 4 channels to 12 kHz ...", flush=True)
    dec = [decim_full(stem, i) for i in range(4)]
    print(f"    12k arrays: {[len(x) for x in dec]} ({time.time() - t0:.0f}s)",
          flush=True)
    search = int(0.200 * SR / DEC)
    chunk = int(chunk_s * SR / DEC)
    hop = int(hop_s * SR / DEC)
    onset = [onset_whole(load_ch(stem, i)) for i in range(4)]
    results = {}
    for i in range(4):
        for j in range(i + 1, 4):
            rows = []
            starts = list(range(0, max(0, len(dec[i]) - chunk) + 1, hop))
            for s in starts:
                a = dec[i][s:s + chunk]
                b = dec[j][s:s + chunk]
                peaks = _surf_peaks(a, b, search)
                rows.append({"start_s": s * DEC / SR,
                             "peaks": {k: [int(v[0]), round(float(v[1]), 3)]
                                       for k, v in peaks.items()}})
            # chunk cluster per estimator: mode over chunk delays
            clusters = {}
            for name in ("full", "low", "mid", "phat"):
                ds = [r["peaks"][name][0] for r in rows]
                qs = [r["peaks"][name][1] for r in rows]
                if not ds:
                    continue
                hist = {}
                for d, q in zip(ds, qs):
                    hist[int(d)] = hist.get(int(d), 0) + float(q)
                best = []
                for c in hist:
                    sup = sum(hist.get(c + k, 0.0) for k in (-2, -1, 0, 1, 2))
                    best.append((sup, c))
                best.sort(reverse=True)
                clusters[name] = {"delay": int(best[0][1]),
                                  "weight": round(best[0][0], 3),
                                  "n": len(ds),
                                  "top": [[int(c), round(float(w), 2)]
                                          for w, c in best[:4]]}
            # whole-file onset envelope
            nfft = int(1 << int(np.ceil(np.log2(2 * len(onset[i])))))
            A = np.fft.rfft(onset[i] - onset[i].mean(), nfft)
            B = np.fft.rfft(onset[j] - onset[j].mean(), nfft)
            surf = np.fft.irfft(A * np.conj(B), nfft)
            m, prom = signed_peak(surf, 20)
            coarse = -m * 480
            a48, b48 = load_ch(stem, i), load_ch(stem, j)
            d, q = _refine_via_segments(a48, b48, coarse, radius=480)
            onset_est = {"delay": int(d), "quality": round(float(q), 4),
                         "coarse": int(coarse), "prom": round(float(prom), 2)}
            results[f"{i}-{j}"] = {"chunks": rows, "clusters": clusters,
                                   "onset": onset_est}
            print(f"    pair {i}-{j}: " + " ".join(
                f"{k}:{v['delay']}(w{v['weight']:.1f})" for k, v in clusters.items())
                + f" onset:{onset_est['delay']}(q{onset_est['quality']:.3f})",
                flush=True)
    (OUT / f"refine_{stem}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {stem}: refine done in {time.time() - t0:.0f}s")
    return results


def _apply_job(job: tuple) -> tuple:
    stem, i, s, n, outdir = job
    src = load_ch(stem, i)
    out = np.zeros(n, dtype=np.float32)
    if s >= 0:
        if s < n:
            out[:n - s] = np.asarray(src[s:])
    else:
        if -s < n:
            out[-s:] = np.asarray(src[:n + s])
    np.asarray(out).tofile(outdir / f"ch{i}.f32")
    return i, int(s)


def stage_apply(stem: str, sol: dict, workers: int = 4) -> dict:
    shifts = {int(k): v for k, v in sol["shifts"].items()}
    n = n_samples(stem)
    outdir = OUT / "aligned" / stem
    outdir.mkdir(parents=True, exist_ok=True)
    # 分通道并行写 raw f32 (每个 230M 采样 = 920 MB, 不构造 4ch 大数组)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, s in ex.map(_apply_job,
                           [(stem, i, shifts.get(i, 0), n, outdir) for i in range(4)]):
            print(f"  CH{i + 1}: shift {s:+d} samples ({s / SR * 1000:+.1f} ms), "
                  f"zero-fill {'tail' if s >= 0 else 'head'} {abs(s)} samples")
    # 分块写 interleaved f32, 供 ffmpeg 生成 4ch wav
    inter_path = outdir / "aligned_interleaved.f32"
    CHUNK = 4_000_000
    with inter_path.open("wb") as fh:
        for s0 in range(0, n, CHUNK):
            s1 = min(n, s0 + CHUNK)
            block = np.empty(((s1 - s0), 4), dtype=np.float32)
            for i in range(4):
                src = np.memmap(outdir / f"ch{i}.f32", dtype=np.float32, mode="r")
                block[:, i] = np.asarray(src[s0:s1])
                del src
            fh.write(block.tobytes())
    cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y",
           "-f", "f32le", "-ar", str(SR), "-ac", "4",
           "-i", str(inter_path),
           "-c:a", "pcm_s24le", str(outdir / "aligned_4ch.wav")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"wav write failed: {r.stderr[-300:]}")
    # per-channel 24-bit wav (remux 用)
    for i in range(4):
        cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y",
               "-f", "f32le", "-ar", str(SR), "-ac", "1",
               "-i", str(outdir / f"ch{i}.f32"),
               "-c:a", "pcm_s24le", str(outdir / f"ch{i}.wav")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"ch{i} wav failed: {r.stderr[-300:]}")
    meta = {"stem": stem, "shifts": sol["shifts"], "n_samples": n,
            "wav": str(outdir / "aligned_4ch.wav"),
            "zero_fill": {str(i): abs(shifts.get(i, 0)) for i in range(4)},
            "wav_bytes": (outdir / "aligned_4ch.wav").stat().st_size}
    (OUT / f"apply_{stem}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def _verify_job(job: tuple) -> dict:
    stem, i, j, w, root = job
    a = load_ch(stem, i, root)
    b = load_ch(stem, j, root)
    s = w * HOP
    est = window_estimates(np.asarray(a[s:s + WIN]), np.asarray(b[s:s + WIN]))
    return {"pair": [i, j], "window": w, "d": est["delay"],
            "q": est["quality"], "m": est["n_methods"]}


def stage_verify(stem: str, sol: dict, max_windows: int = 0,
                 workers: int = 10) -> dict:
    root = OUT / "aligned"          # pcm_path 会再拼 stem
    n = n_samples(stem, root)
    n_win = max(0, (n - WIN) // HOP + 1)
    if max_windows:
        n_win = min(n_win, max_windows)
    anchor = sol["anchor"]
    # 三个对锚点 + 2-3 (覆盖全部 4 轨)
    pairs = [(anchor, i) for i in range(4) if i != anchor]
    if (2, 3) not in pairs and (3, 2) not in pairs:
        pairs.append((2, 3))
    jobs = [(stem, i, j, w, root) for i, j in pairs for w in range(n_win)]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, r in enumerate(ex.map(_verify_job, jobs, chunksize=8), 1):
            rows.append(r)
            if k % 300 == 0:
                print(f"    verify {k}/{len(jobs)}", flush=True)
    good = [r for r in rows if r["d"] is not None]
    vals = [abs(r["d"]) for r in good]
    stats = {}
    if vals:
        a = np.asarray(vals)
        stats = {"n": int(a.size), "median": float(np.median(a)),
                 "p90": float(np.percentile(a, 90)), "max": float(np.max(a)),
                 "le1": int((a <= 1).sum())}
    per_pair = {}
    for i, j in pairs:
        v = [abs(r["d"]) for r in good if tuple(r["pair"]) == (i, j)]
        if v:
            aa = np.asarray(v)
            per_pair[f"{i}-{j}"] = {"n": int(aa.size),
                                    "median": float(np.median(aa)),
                                    "p90": float(np.percentile(aa, 90)),
                                    "max": float(np.max(aa)),
                                    "le1": int((aa <= 1).sum())}
    report = {"stem": stem, "anchor": anchor, "shifts": sol["shifts"],
              "n_windows": n_win, "n_measured": len(good),
              "residual": stats, "per_pair": per_pair,
              "details": [{"pair": r["pair"], "w": r["window"], "d": r["d"],
                           "q": r["q"], "m": r["m"]} for r in rows]}
    (OUT / f"verify_{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {stem}: residual |d| n={stats.get('n')} med={stats.get('median')} "
          f"p90={stats.get('p90')} max={stats.get('max')} le1={stats.get('le1')}")
    for k, v in per_pair.items():
        print(f"    pair {k}: med={v['median']} p90={v['p90']} max={v['max']} "
              f"le1={v['le1']}/{v['n']}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["facts", "windows", "solve", "refine", "apply", "verify", "all"])
    ap.add_argument("--stem", default="")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--max-windows", type=int, default=0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stems = [args.stem] if args.stem else list(CLIPS)
    if args.stage == "facts":
        stage_facts(CLIPS)
        return 0
    for stem in stems:
        print(f"\n=== {stem} [{args.stage}]")
        if args.stage in ("windows", "all"):
            stage_windows(stem, args.workers, args.max_windows)
        if args.stage == "refine":
            stage_refine(stem)
            continue
        if args.stage in ("solve", "all"):
            sol = stage_solve(stem)
            if args.stage == "all":
                stage_apply(stem, sol)
        elif args.stage == "apply":
            sol = json.loads((OUT / f"solve_{stem}.json").read_text(encoding="utf-8"))
            stage_apply(stem, sol)
        elif args.stage == "verify":
            sol = json.loads((OUT / f"solve_{stem}.json").read_text(encoding="utf-8"))
            stage_verify(stem, sol, args.max_windows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
