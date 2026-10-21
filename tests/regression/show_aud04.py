#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print AUD-04 (refused but still measurably off) for a check report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else
         r"E:\1kt_scratch\aud04\archive-repair-report.json")
d = json.loads(p.read_text(encoding="utf-8"))
rows = d.get("rows") or []
pass_n = warn_n = 0
print(f"{'file':<24}{'AUD-04':<8}detail")
print("-" * 96)
for r in sorted(rows, key=lambda x: Path(x.get("path", "")).name):
    nm = Path(r.get("path", "")).name
    c = next((x for x in (r.get("checks") or []) if x.get("id") == "AUD-04"),
             None)
    if c is None:
        continue
    v = c.get("verdict")
    if v == "PASS":
        pass_n += 1
    else:
        warn_n += 1
    print(f"{nm:<24}{v:<8}{str(c.get('detail'))[:80]}")
print(f"\nAUD-04 PASS {pass_n} 件 / 非 PASS {warn_n} 件")
