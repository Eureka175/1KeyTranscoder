#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prototype v4: iterative residual refinement of the windowed long-take plan.

v3 answered "where does the tail come from?":
    CH4 (strong pair): threshold 0.10 already gives p90 2 samples, 89 % of windows <=1
    CH1/CH2 (music bed): median 0-1 but p90 ~125; the outliers are in *covered*
        windows, i.e. some trusted window measurements point hundreds of samples off.
        Raising the peak threshold cleans the covered windows (p90 129 -> 12 at 0.60)
        but turns the rest into gaps whose linear fill is hundreds of samples off.

So neither "trust everything" nor "trust only the best" works. v4 tests the obvious
remaining loop: after applying the plan, re-measure the residual per window and fold
it back into the trajectory (the residual measurement is independent of the plan --
it correlates the corrected output against the anchor, not against the measurement
that built the plan). Iterate until no trusted window is more than one sample off or
three rounds pass.

Input: the per-window raw measurements saved by v3 (proto_long_take_v3.json), so this
run only pays for the resampling and residual measurement.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "regression"))

from proto_long_take_v3 import (           # noqa: E402
    PCM_CACHE, SR, WIN_S, HOP_S, load_channels, window_delay, summarise,
)

OUT = Path(r"E:\1kt_work\proto_long_take")
V3 = OUT / "proto_long_take_v3.json"
CLIP_PATHS = {
    "20260919_C1305": REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    "20260919_C1306": REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
}
TRUST = 0.10          # initial plan from every measurable window
REFINE = 0.40         # only fold back residuals measured with this confidence
MAX_ITER = 3


def correction_curve(starts, win, corrections):
    """Piecewise linear curve through per-window corrections at window centres."""
    xs = np.array([s + win / 2.0 for s in starts], dtype=np.float64)
    ys = np.array(corrections, dtype=np.float64)
    return lambda pos: np.interp(pos, xs, ys)


def apply_and_measure(channels, anchor_arr, starts, win, n, curve):
    res = []
    for w, s in enumerate(starts):
        pos = np.arange(s, s + win, dtype=np.float64)
        shift = curve(pos)
        src = np.asarray(channels[s:s + win], dtype=np.float64)
        posi = pos + shift
        np.clip(posi, 0, n - 1, out=posi)
        y = np.interp(posi, np.arange(s, s + win, dtype=np.float64), src)
        d, q = window_delay(np.asarray(anchor_arr[s:s + win]), y)
        res.append((d if np.isfinite(d) else float("nan"), q))
    return res


def main() -> int:
    data = json.loads(V3.read_text(encoding="utf-8"))
    report = []
    for entry in data:
        clip = entry["clip"]
        target = entry["target"]
        anchor = entry["anchor"]
        pairs = entry["raw_pairs"]
        starts = None
        try:
            channels = load_channels(clip, 4)
        except FileNotFoundError:
            print(f"缺 PCM: {clip}")
            continue
        n = channels[0].shape[0]
        win = int(WIN_S * SR)
        hop = int(HOP_S * SR)
        starts = list(range(0, max(0, n - win) + 1, hop))
        if len(starts) != len(pairs):
            print(f"窗口数不一致: {clip} {len(starts)} vs {len(pairs)}")
            continue
        anchor_arr = channels[anchor]
        # 初始修正 = 干净测量值 (线性插值跨缺口), 单位: 采样
        corr = []
        for d, q in pairs:
            corr.append(float(d) if d is not None and q >= TRUST else float("nan"))
        # 用线性插值填 NaN
        xs = np.array([s + win / 2.0 for s in starts])[np.isfinite(corr)]
        ys = np.array(corr)[np.isfinite(corr)]
        corr = np.interp(np.array([s + win / 2.0 for s in starts]), xs, ys)
        hist = []
        for it in range(MAX_ITER + 1):
            curve = correction_curve(starts, win, corr)
            measured = apply_and_measure(channels[target], anchor_arr, starts, win, n, curve)
            finite = [(i, d) for i, (d, q) in enumerate(measured) if np.isfinite(d)]
            absv = np.abs([d for _, d in finite])
            stats = {
                "iter": it,
                "n": int(absv.size),
                "median": float(np.median(absv)),
                "p90": float(np.percentile(absv, 90)),
                "max": float(np.max(absv)),
                "le1": int((absv <= 1).sum()),
            }
            hist.append(stats)
            print(f"    {clip} CH{target + 1} iter{it}: n={stats['n']} "
                  f"p50={stats['median']} p90={stats['p90']} max={stats['max']} "
                  f"<=1 {stats['le1']}/{stats['n']}", flush=True)
            if it == MAX_ITER or stats["le1"] == stats["n"]:
                break
            # 把残差折回: 只对有置信的窗; 其余窗沿用当前修正
            new = corr.copy()
            changed = 0
            for i, (d, q) in enumerate(measured):
                if np.isfinite(d) and q >= REFINE and abs(d) > 1:
                    new[i] = corr[i] + d
                    changed += 1
            if changed == 0:
                print("    收敛 (没有可折回的残差)")
                break
            # 平滑折回带来的跳变: 用相邻窗中位数抑制单点噪声
            smoothed = new.copy()
            for i in range(len(new)):
                lo = max(0, i - 1)
                hi = min(len(new), i + 2)
                smoothed[i] = float(np.median(new[lo:hi]))
            corr = smoothed
        final = hist[-1]
        report.append({"clip": clip, "target": target, "anchor": anchor,
                       "history": hist,
                       "initial": hist[0], "final": final})
        del channels
    (OUT / "proto_long_take_v4.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'proto_long_take_v4.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
