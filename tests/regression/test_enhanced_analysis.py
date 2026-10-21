#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-level test of `enhanced_window_analysis` with a synthetic trajectory.

The short proof clips are shorter than one 30 s window, so an end-to-end run
cannot exercise the sliding-offset / adaptive-window / piecewise paths. This
builds a trajectory with the same shape the estimator produces (frames every
hop, only some of them usable, bursts of evidence separated by quiet stretches)
and asserts the analysis returns coherent numbers: a sparse pattern must show
low base resolution, sliding offsets must recover some windows, adaptive lengths
must report which windows need a longer window, and the piecewise fit must
recover the two-level structure that was injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "regression"))

import long_take_probe as L  # noqa: E402

SR = 48000


def build(duration_s: float = 4800.0, hop_s: float = 1.0,
          seed: int = 7) -> dict:
    n = int(duration_s * SR)
    hop = int(hop_s * SR)
    centers = np.arange(hop // 2, n, hop, dtype=np.int64)
    rng = np.random.default_rng(seed)
    delays = np.full(centers.size, np.nan)
    confs = np.zeros(centers.size)
    # 证据: 每 60 s 里有一段 8 s 的"有声"窗口，段内延迟 = 前 2400 s 为 2400,
    # 之后跳到 2400+240（两级结构，用于检验分段拟合能否找到那个接缝）。
    for k, c in enumerate(centers):
        t = c / SR
        in_burst = (t % 60.0) < 8.0
        if not in_burst:
            continue
        base = 2400.0 if t < 2400.0 else 2640.0
        delays[k] = base + rng.normal(0.0, 0.4)
        confs[k] = 0.8
    return {"ref": 0, "tgt": 1, "frame_ms": 1000.0, "hop_ms": 1000.0,
            "anchor_segment_seconds": 20.0, "usable_frames": int(np.isfinite(
                delays).sum()), "delay_samples": float(np.nanmedian(delays)),
            "delay_ms": float(np.nanmedian(delays)) / SR * 1000.0,
            "confidence": 0.8, "fine_delay_ms": float("nan"),
            "fine_fit_r2": float("nan"), "n_frames": int(centers.size),
            "n_good": int(np.isfinite(delays).sum()),
            "centers": centers, "delays": delays, "confidences": confs,
            "summarize": None, "displacement_ms": None, "constant": False,
            "conf_gate": 0.3, "overrides": {}}


def main() -> int:
    n = int(4800.0 * SR)
    rec = build()
    a = L.enhanced_window_analysis(rec, n)
    fails: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")
        if not cond:
            fails.append(name)

    check("scales 至少两档", len(a["scales"]) >= 2, str(len(a["scales"])))
    sc = a["scales"][0]
    base_frac = sc["resolved_fraction_base"]
    best_frac = sc["sliding_best_fraction"]
    check("基础解析率反映稀疏证据 (<0.5)", base_frac < 0.5, f"{base_frac:.3f}")
    check("滑窗最优 >= 基础（只能更好）", best_frac >= base_frac,
          f"{base_frac:.3f} -> {best_frac:.3f}")
    ad = sc["adaptive_needed_window_s"]
    check("自适应窗长给出 p50/p90", ad["p50"] is not None and ad["p90"] is not None,
          f"p50={ad['p50']} p90={ad['p90']}")
    check("自适应窗长 p50 <= 480s (阶梯上界)",
          ad["p50"] is None or ad["p50"] <= 480.0, str(ad["p50"]))
    cov = sc["audio_time_coverage"]
    check("音频时长覆盖在 (0,1] 内", 0.0 < cov <= 1.0, f"{cov:.3f}")
    pw = a["piecewise"]
    check("分段拟合可用", pw.get("min_seg_ok") is True, str(pw.get("segments")))
    check("分段数 >= 2（注入过两级偏移）", (pw.get("segments") or 0) >= 2,
          str(pw.get("segments")))
    check("段内残余 p90 有限", pw.get("residual_p90") is not None
          and np.isfinite(pw["residual_p90"]), str(pw.get("residual_p90")))
    seams = pw.get("seam_steps") or []
    around = [s for s in seams if 200.0 <= s <= 300.0]
    check("找到 ~240 采样的接缝", bool(around),
          f"seams={[round(s, 1) for s in seams][:6]}")
    print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
