#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the raw per-window delay trajectory for pairs 0-3 and 2-3, to decide whether
CH4's relationship is a smooth drift (correct by resampling) or content-driven jumps
(nothing linear to fit)."""
import json
from pathlib import Path

OUT = Path(r"E:\1kt_work\long_align")
for stem in ("20260919_C1305", "20260919_C1306"):
    raw = json.loads((OUT / f"windows_{stem}.json").read_text(encoding="utf-8"))
    per = {}
    for r in raw:
        per.setdefault(f"{r['pair'][0]}-{r['pair'][1]}", []).append(r)
    print(f"\n=== {stem}")
    for key in ("0-2", "0-3", "2-3"):
        rows = sorted(per[key], key=lambda r: r["window"])
        vals = [(r["start_s"] / 60.0, r["delay"], r["quality"]) for r in rows
                if r["delay"] is not None]
        print(f"  pair {key}: {len(vals)} windows; every 4th:")
        line = []
        for k, (tmin, d, q) in enumerate(vals):
            if k % 4:
                continue
            line.append(f"{tmin:5.1f}m:{d:+5d}(q{q:.1f})")
        for chunk in range(0, len(line), 6):
            print("    " + "  ".join(line[chunk:chunk + 6]))
