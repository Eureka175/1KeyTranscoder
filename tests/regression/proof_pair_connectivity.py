#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Connectivity answer: can each clip reach every channel through measurable pairs?

Graph nodes are the four channels; an undirected edge exists when the pair is
measurable at the production bar (usable_frames >= 5). If the anchor (CH3, the
production anchor) reaches every other channel, the clip can in principle be
aligned by chaining measurements; if a channel is unreachable, its offset cannot
be established from this material at all.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
data = json.loads((OUT / "proof_pairs.json").read_text(encoding="utf-8"))
ANCHOR = 3          # 生产锚点 CH3
MIN_FRAMES = 5

for clip, d in data.items():
    print(f"\n=== {clip}  {d['duration_s']:.2f}s")
    for gname, pairs in d["edges"].items():
        adj: dict[int, set[int]] = {i: set() for i in range(1, 5)}
        measurable = []
        for key, rec in pairs.items():
            a, b = key.split("-")
            if rec["usable"] >= MIN_FRAMES:
                adj[int(a)].add(int(b))
                adj[int(b)].add(int(a))
                measurable.append((int(a), int(b), rec["usable"],
                                   rec["delay_ms"], rec.get("constant")))
        seen = {ANCHOR}
        stack = [ANCHOR]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        unreachable = sorted(set(range(1, 5)) - seen)
        ok = "连通" if not unreachable else f"不可达 {unreachable}"
        print(f"  {gname:<11} 可测边 {len(measurable):>2}  "
              f"从 CH{ANCHOR} 可达 {sorted(seen)}  -> {ok}")
        for a, b, n, delay, const in sorted(measurable):
            if ANCHOR in (a, b) or const:
                tag = "恒定" if const else "非恒定"
                print(f"      CH{a}-CH{b}: {n:>3} 帧 {delay:>8.2f}ms {tag}")
