#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix the scorer's init-bug and report, per pair and per band, the weighted
delay modes from the window data. Purpose: find which estimator carries the
cross-group coherence, and whether candidate A is the global winner.
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

OUT = Path(r"E:\1kt_work\long_align")


def weighted_modes(vals, weights, k=3, min_gap=4):
    """Cluster by +-2 samples, then pick k strongest separated modes."""
    if not vals:
        return []
    hist = defaultdict(float)
    for d, w in zip(vals, weights):
        hist[int(d)] += float(w)
    merged = []
    used = set()
    for center in sorted(hist, key=lambda c: -hist[c]):
        if any(abs(center - u) < min_gap for u in used):
            continue
        w = sum(hist.get(center + o, 0.0) for o in (-2, -1, 0, 1, 2))
        merged.append([int(center), round(w, 2)])
        used.add(center)
        if len(merged) >= k:
            break
    return merged


for stem in ("20260919_C1305", "20260919_C1306"):
    raw = json.loads((OUT / f"windows_{stem}.json").read_text(encoding="utf-8"))
    per = defaultdict(list)
    for r in raw:
        per[f"{r['pair'][0]}-{r['pair'][1]}"].append(r)
    print(f"\n=== {stem}: per-band weighted modes (quality as weight)")
    for key, rows in sorted(per.items()):
        parts = []
        for method in ("full", "band0", "band1", "band2", "onset"):
            vals, ws = [], []
            for r in rows:
                c = (r["cands"] or {}).get(method)
                if c is None:
                    continue
                vals.append(c[0])
                ws.append(c[1])
            modes = weighted_modes(vals, ws, k=2)
            parts.append(f"{method}:{modes}")
        print(f"  {key}: " + " | ".join(parts))
