#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Same data as the HTML charts, rendered as terminal histograms.

The HTML is the deliverable; this exists because the chat answer has to show
the shape, and a number without its distribution is what got us into trouble.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import SR, count_into, load, num  # noqa: E402

BAR = "#"


def show(title: str, edges: list[float], values: list[float],
         gate: float | None = None, gate_label: str = "") -> None:
    counts = count_into(values, edges)
    top = max(counts) if counts else 1
    print(f"\n{title}  (n={len(values)})")
    for i, c in enumerate(counts):
        lo, hi = edges[i], edges[i + 1]
        mark = ""
        if gate is not None and lo <= gate < hi:
            mark = f"   <-- 门 {gate_label or gate}"
        bar = BAR * int(round(40 * c / top)) if top else ""
        print(f"  {lo:>7.3f}–{hi:<7.3f} {c:>5}  {bar}{mark}")


def main() -> int:
    rows = load()
    meas = [c for c in rows if num(c.get("delay_samples")) is not None]
    refused = [c for c in meas if c.get("decision") == "untouched"]
    print(f"rows={len(rows)} measurable={len(meas)} refused={len(refused)}")

    show("① 可用帧数 usable_frames（可测轨）", [0, 1, 2, 3, 4, 5, 6, 8, 10,
                                              15, 20, 30, 50, 80, 120, 400],
         [num(c.get("usable_frames"), 0) for c in meas], 5 * 0 + 5, "5")
    show("② 置信度 confidence（可测轨）",
         [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9],
         [num(c.get("confidence"), 0.0) for c in meas], 0.3, "0.3")
    show("③ 轨迹 MAD（ms，可测轨）",
         [0, .05, .1, .2, .3, .5, .8, 1.0, 1.5, 2.0, 3.0, 5.0],
         [num(c.get("traj_mad_ms"), 0.0) for c in meas], 1.0, "1.0")
    show("④ |偏差|（ms，被拒轨 —— 未修的误差）",
         [0, .1, .2, .5, 1, 2, 5, 10, 15, 18, 20, 22, 24, 26, 28, 32],
         [abs(num(c.get("delay_ms"), 0.0)) for c in refused], None)
    ge = [c["_spread_ms"] for c in meas if num(c.get("usable_frames"), 0) >= 20]
    lt = [c["_spread_ms"] for c in meas if num(c.get("usable_frames"), 0) < 20]
    show("⑤ 全范围 spread（ms，帧数≥20 —— 门在此生效）",
         [0, .1, .2, .5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0], ge, 1.0, "1.0")
    show("⑥ 全范围 spread（ms，帧数<20 —— 门不检查）",
         [0, .1, .2, .5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0], lt, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
