#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull the per-window recheck values out of the arm's per-file `logs`.

`G2_win3_corpus.json` was produced with `recheck_windows=3`; the channel-sync
logger records the three residuals for every rejected track. Those numbers are
the answer to "how big is a whole-clip residual for a fix that is correct in the
middle window", which sets the realistic tolerance.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
TARGETS = sys.argv[1:] or [
    "20260903_C1171.MP4", "20260904_C1182.MP4", "20260904_C1197.MP4",
    "20260904_C1212.MP4", "20260904_C1214.MP4", "20260903_C1174.MP4",
    "20260904_C1213.MP4",
]

for fname in ("G2_win3_corpus.json", "G2_rel_corpus.json"):
    p = OUT / fname
    if not p.is_file():
        continue
    rows = {r.get("name"): r for r in json.loads(p.read_text(encoding="utf-8"))}
    print(f"\n=== {fname}")
    for name in TARGETS:
        r = rows.get(name)
        if not r:
            continue
        hits = [ln for ln in (r.get("logs") or [])
                if "复检" in ln or "recheck" in ln.lower() or "残差" in ln]
        if not hits:
            continue
        print(f"  {name}:")
        for ln in hits:
            print(f"    {ln.strip()[:180]}")
