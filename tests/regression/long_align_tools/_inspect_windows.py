#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect per-window evidence to see whether cross-group delays have a tight
high-quality core (unbiased) or only a broad peak.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

OUT = Path(r"E:\1kt_work\long_align")
for stem in ("20260919_C1305", "20260919_C1306"):
    raw = json.loads((OUT / f"windows_{stem}.json").read_text(encoding="utf-8"))
    per = {}
    for r in raw:
        key = f"{r['pair'][0]}-{r['pair'][1]}"
        per.setdefault(key, []).append(r)
    print(f"\n=== {stem}")
    for key, rows in sorted(per.items()):
        vals = [(r["delay"], r["quality"], r["n_methods"]) for r in rows
                if r["delay"] is not None]
        if not vals:
            continue
        d = np.array([v[0] for v in vals])
        q = np.array([v[1] for v in vals])
        order = np.argsort(-q)
        top = d[order[: max(5, len(d) // 20)]]
        print(f"  pair {key}: n={len(d)} | all mode={Counter(d.tolist()).most_common(3)}")
        print(f"    top5% quality (n={len(top)}): mode={Counter(top.tolist()).most_common(3)}"
              f" median={np.median(top):.1f}")
        # quality-weighted histogram
        hist = {}
        for dd, qq in zip(d, q):
            hist[int(dd)] = hist.get(int(dd), 0.0) + float(qq)
        best = sorted(hist.items(), key=lambda kv: -kv[1])[:4]
        print(f"    quality-weighted top: {[(k, round(v, 2)) for k, v in best]}")
