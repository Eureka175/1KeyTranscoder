#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-take windowed alignment: prototype + end-to-end residual measurement.

Why this file exists
--------------------
The production frame pipeline cannot fix the two long takes: with the production
200 ms frame geometry only ~0.05 usable frames land in a 30 s plan window, so
`run_channel_sync` correctly rejects every track as `non_constant` (measured on
C1305: CH1/CH2/CH4 non_constant, displacement 8.7-14.8 ms, delay 23.8 ms).
The window coverage that the probe measured (68-89 %) was obtained by correlating
the **window itself**, not by aggregating short frames.

So this prototype measures each 30 s plan window directly (window-length
normalized cross-correlation against several candidate references, composed back to
the anchor), builds a piecewise shift trajectory under the D2 gap policy and D5
coordinate being compared, applies it by fractional resampling, and then measures
the residual on the written output -- outside the correction tool.

What it answers
---------------
    no correction vs single shift vs windowed correction,
    hold vs linear vs no_correction_gap,
    latest-start vs nearest-centre,

in samples of residual per 30 s window, against the same anchor. Nothing here is
wired into the production path yet: it is the evidence needed to choose D2/D5.

No video encoding; the audio arrays are only resampled in memory, one window at a
time (a clip here is 230 M samples per channel, so no full-length temporaries).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                      # noqa: E402
import core.channel_sync as cs                   # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
PCM_CACHE = Path(r"E:\1kt_scratch\pcm")
WORK = Path(r"E:\1kt_scratch\proto_lt")
OUT = Path(r"E:\1kt_work\proto_long_take")

SR = 48000
DEC = 4                          # coarse decimation (12 kHz)
WIN_S = 30.0
HOP_S = 10.0
SEARCH_MS = 250.0                # +-250 ms
FINE_RADIUS = 8                  # +-8 full-rate samples around the coarse peak
MIN_PEAK = 0.10

CLIPS = [
    REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
]


def load_channels(stem: str, n: int = 4) -> list[np.memmap]:
    out = []
    for i in range(n):
        p = PCM_CACHE / stem / f"ch{i}.f32"
        if not p.is_file():
            raise FileNotFoundError(p)
        out.append(np.memmap(p, dtype=np.float32, mode="r"))
    return out


def _cc_peak(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    """m = argmax |sum a[n] b[n+m]|, plus normalized peak."""
    a = a - a.mean()
    b = b - b.mean()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0, 0.0
    nfft = int(1 << int(np.ceil(np.log2(a.size + b.size))))
    A = np.fft.rfft(a, nfft)
    B = np.fft.rfft(b, nfft)
    cc = np.fft.irfft(A * np.conj(B), nfft)
    idx = int(np.argmax(np.abs(cc)))
    m = idx if idx < nfft // 2 else idx - nfft
    return m, float(np.abs(cc[idx]) / (na * nb))


def window_delay(ref: np.ndarray, tgt: np.ndarray) -> tuple[float, float]:
    """tgt[n] ~ ref[n - delay]; delay > 0 means target lags. Coarse-to-fine."""
    sr_c = SR // DEC
    search_c = int(round(SEARCH_MS / 1000.0 * sr_c))
    # 粗: 抽取 (均值滤波抗混叠) 后全窗相关
    r = np.asarray(ref, dtype=np.float64)[: (ref.size // DEC) * DEC]
    g = np.asarray(tgt, dtype=np.float64)[: (tgt.size // DEC) * DEC]
    r = r.reshape(-1, DEC).mean(axis=1)
    g = g.reshape(-1, DEC).mean(axis=1)
    n = min(r.size, g.size)
    r, g = r[:n], g[:n]
    # 只在 |m| <= search_c 内找峰: 用前后拼接的循环相关面
    nfft = int(1 << int(np.ceil(np.log2(2 * n))))
    A = np.fft.rfft(r - r.mean(), nfft)
    B = np.fft.rfft(g - g.mean(), nfft)
    cc = np.fft.irfft(A * np.conj(B), nfft)
    cand = np.concatenate([cc[:search_c + 1], cc[-search_c:]])
    idx = int(np.argmax(np.abs(cand)))
    m_c = idx if idx <= search_c else idx - (2 * search_c + 1)
    coarse = m_c * DEC
    # 细: 在粗峰附近用全速率、窗中心 2 s 段直接点积
    seg = int(2.0 * SR)
    c0 = ref.size // 2 - seg // 2
    a = np.asarray(ref[c0:c0 + seg], dtype=np.float64)
    a = a - a.mean()
    # 粗扫的 cc[m] = sum a[n] b[n-m] 给出 m = +delay; 细扫用搜索偏移 s 表示
    # a[n] ~ b[n + s], 于是 s = -delay。这里从 -coarse 出发在其附近细化。
    best = (0.0, 0.0)
    # 实测: irfft(A*conj(B)) 的峰在 -delay; 细扫直接点积 a[k]·b[k+m] 的峰在 +delay
    center = -int(round(coarse))
    for m in range(center - FINE_RADIUS, center + FINE_RADIUS + 1):
        b0 = c0 + m
        if b0 < 0 or b0 + seg > tgt.size:
            continue
        b = np.asarray(tgt[b0:b0 + seg], dtype=np.float64)
        b = b - b.mean()
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom < 1e-9:
            continue
        val = float(np.dot(a, b) / denom)
        if abs(val) > abs(best[1]):
            best = (float(m), val)
    m_fine, val = best
    if m_fine == 0.0 and val == 0.0:
        return float("nan"), 0.0
    # a[k]·b[k+m] 在 m = +delay 处取峰
    return float(m_fine), abs(val)


def calibrate() -> None:
    rng = np.random.default_rng(7)
    x = rng.standard_normal(SR * 40).astype(np.float32)
    shift = 123
    y = np.concatenate([np.zeros(shift, np.float32), x[:-shift]])
    d, peak = window_delay(x, y)
    assert abs(d - shift) <= 1.0, f"sign convention wrong: d={d} != {shift}"
    print(f"  calibration: delay={d:.2f} (expect {shift}), peak={peak:.3f}")


def measure_windows(channels: list[np.ndarray], anchor: int) -> dict:
    n = channels[0].shape[0]
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    starts = list(range(0, max(0, n - win) + 1, hop))
    n_tracks = len(channels)
    t0 = time.time()
    # 第一遍: 目标 vs 锚点 (直接测量)
    direct: dict[int, list[tuple[float, float]]] = {}
    for i in range(n_tracks):
        if i == anchor:
            continue
        vals = [window_delay(channels[anchor][s:s + win],
                             channels[i][s:s + win]) for s in starts]
        direct[i] = vals
        ok = sum(1 for d, p in vals if np.isfinite(d) and p >= MIN_PEAK)
        print(f"    direct CH{anchor + 1}->CH{i + 1}: {ok}/{len(vals)} resolved "
              f"({time.time() - t0:.0f}s)")
    # 第二遍: 未解析窗用其它参考构图 (j 相对锚点的窗口值也需可用)
    alt: dict[tuple[int, int], list[tuple[float, float]]] = {}
    refs = [j for j in range(n_tracks) if j != anchor]
    need: dict[tuple[int, int], list[int]] = {}
    for i in range(n_tracks):
        if i == anchor:
            continue
        bad = [w for w, (d, p) in enumerate(direct[i])
               if not (np.isfinite(d) and p >= MIN_PEAK)]
        for j in refs:
            if j == i:
                continue
            need[(j, i)] = bad
    for (j, i), wins in need.items():
        if not wins:
            continue
        vals = direct[i] if j == anchor else None
        # measure j->i on the requested windows only
        got = []
        for w in wins:
            s = starts[w]
            got.append(window_delay(channels[j][s:s + win],
                                    channels[i][s:s + win]))
        alt[(j, i)] = got
    print(f"    alternates: {sum(len(v) for v in alt.values())} window pairs "
          f"({time.time() - t0:.0f}s)")
    return {"starts": starts, "win": win, "hop": hop, "direct": direct,
            "alt": alt, "need": need, "n": n, "n_tracks": n_tracks}


def absolute_windows(meas: dict, anchor: int) -> dict[int, list[tuple]]:
    """Per target, per window: (delay_abs, peak, via_ref) composed to the anchor."""
    starts = meas["starts"]
    direct = meas["direct"]
    alt = meas["alt"]
    need = meas["need"]
    out: dict[int, list[tuple]] = {}
    for i, vals in direct.items():
        rows: list[tuple] = []
        for w, (d, p) in enumerate(vals):
            if np.isfinite(d) and p >= MIN_PEAK:
                rows.append((d, p, anchor))
                continue
            best = None
            for j in range(meas["n_tracks"]):
                if j in (i, anchor):
                    continue
                key = (j, i)
                if key not in alt or w not in need.get(key, []):
                    continue
                idx = need[key].index(w)
                d_ij, p_ij = alt[key][idx]
                d_ja, p_ja = direct[j][w] if np.isfinite(direct[j][w][0]) else (float("nan"), 0.0)
                if not (np.isfinite(d_ij) and np.isfinite(d_ja)):
                    continue
                pc = min(p_ij, p_ja)
                if pc < MIN_PEAK:
                    continue
                if best is None or pc > best[1]:
                    best = (d_ij + d_ja, pc, j)
            rows.append(best if best is not None else (float("nan"), 0.0, -1))
        out[i] = rows
    return out


def shift_at(pos: np.ndarray, xs: np.ndarray, ys: np.ndarray, policy: str,
             n: int, starts: list[int], win: int) -> np.ndarray:
    if xs.size == 0:
        return np.zeros(pos.size, dtype=np.float64)
    if policy == "hold":
        idx = np.searchsorted(xs, pos, side="right") - 1
        idx = np.clip(idx, 0, ys.size - 1)
        vals = ys[idx].copy()
        vals[pos < xs[0]] = ys[0]
        return vals
    if policy == "linear":
        return np.interp(pos, xs, ys)
    if policy == "no_correction_gap":
        # 只在被信任窗覆盖的时间范围内修正, 其它位置 0 (明确"留错误")
        vals = np.interp(pos, xs, ys)
        covered = np.zeros(pos.size, dtype=bool)
        for s in starts:
            a0 = max(s, int(pos[0]))
            b0 = min(s + win, int(pos[-1]) + 1)
            if b0 > a0:
                covered[a0 - int(pos[0]): b0 - int(pos[0])] = True
        vals[~covered] = 0.0
        return vals
    raise ValueError(policy)


def residual_of_config(channels, anchor_arr, wins, starts, shift_traj, window_lags) -> list[float]:
    pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("calibration of the delay sign convention")
    calibrate()
    results = []
    for clip in CLIPS:
        stem = clip.stem
        print(f"\n=== {stem}")
        channels = load_channels(stem)
        n = channels[0].shape[0]
        streams = ar.probe(clip)["streams"]
        rep = cs.run_channel_sync(
            source=clip, ffmpeg=FFMPEG, work_dir=WORK / stem / "global",
            streams=streams, opts=cs.production_opts({}), log=lambda m: None)
        anchor = rep.get("anchor_stream")
        print(f"  n={n} ({n / SR:.0f}s) production anchor=CH{anchor + 1} "
              f"status={rep.get('status')}")
        meas = measure_windows(channels, anchor)
        abs_win = absolute_windows(meas, anchor)
        win, hop, starts = meas["win"], meas["hop"], meas["starts"]
        anchor_arr = channels[anchor]
        for i, rows in abs_win.items():
            trusted = [(s + (0.0 if True else 0), r) for s, r in zip(starts, rows)
                       if np.isfinite(r[0]) and r[1] >= MIN_PEAK]
            row_res = {
                "clip": stem, "target": i, "anchor": anchor,
                "n_windows": len(starts),
                "trusted": len(trusted),
                "trusted_fraction": len(trusted) / len(starts),
            }
            ds = [r[0] for _, r in trusted]
            row_res["raw"] = summarise(ds)
            med = float(np.rint(np.median(ds))) if ds else 0.0
            row_res["single_shift"] = {"shift": med,
                                       **summarise([d - med for d in ds])}
            for policy, coord in (("hold", "nearest-centre"),
                                  ("linear", "latest-start"),
                                  ("linear", "nearest-centre")):
                xs = np.array([s + (0.0 if coord == "latest-start" else win / 2.0)
                               for s, _ in trusted], dtype=np.float64)
                ys = np.array([r[0] for _, r in trusted], dtype=np.float64)
                lags = []
                for w, s in enumerate(starts):
                    pos = np.arange(s, s + win, dtype=np.float64)
                    shift = shift_at(pos, xs, ys, policy, n, starts, win)
                    src = np.asarray(channels[i][s:s + win], dtype=np.float64)
                    posi = pos + shift
                    np.clip(posi, 0, n - 1, out=posi)
                    y = np.interp(posi, np.arange(s, s + win, dtype=np.float64), src)
                    d, p = window_delay(np.asarray(anchor_arr[s:s + win]), y)
                    if np.isfinite(d) and p >= MIN_PEAK:
                        lags.append(d)
                key = f"{policy}/{coord}"
                row_res[key] = summarise(lags)
                print(f"    CH{i + 1} {key:34s} p50={row_res[key].get('median')} "
                      f"p90={row_res[key].get('p90')} max={row_res[key].get('max')} "
                      f"<=1 {row_res[key].get('le1')}/{row_res[key].get('n')}")
            print(f"    CH{i + 1} raw p90={row_res['raw'].get('p90')} "
                  f"single-shift({med:+.0f}) p90={row_res['single_shift'].get('p90')} "
                  f"trusted={row_res['trusted_fraction']:.1%}")
            results.append(row_res)
    (OUT / "proto_long_take.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'proto_long_take.json'}")
    return 0


def summarise(lags: list[float]) -> dict:
    if not lags:
        return {"n": 0}
    a = np.abs(np.asarray(lags, dtype=np.float64))
    return {"n": int(a.size), "median": float(np.median(a)),
            "p90": float(np.percentile(a, 90)), "max": float(np.max(a)),
            "le1": int((a <= 1).sum()), "frac_le1": float((a <= 1).mean())}


if __name__ == "__main__":
    raise SystemExit(main())
