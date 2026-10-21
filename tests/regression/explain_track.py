#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explain one track's fate in a measured arm (default: C1174 CH2)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
arm = sys.argv[1] if len(sys.argv) > 1 else "R_noveto4_archive.json"
needle = sys.argv[2] if len(sys.argv) > 2 else "C1174"
rows = json.loads((OUT / arm).read_text(encoding="utf-8"))
for r in rows:
    name = r.get("name") or ""
    if needle not in name:
        continue
    print(f"{name}  status={r.get('status')}")
    for c in r.get("channels") or []:
        ch = (c.get("stream") or 0) + 1
        print(f"  CH{ch} decision={c.get('decision')} "
              f"reason={c.get('reason')} delay={c.get('delay_ms')} "
              f"frames={c.get('usable_frames')} "
              f"mat={c.get('materiality')}")
    for ln in (r.get("logs") or []):
        s = str(ln)
        if "CH2" in s or "复检" in s or "材料性" in s:
            print(f"  log: {s[:170]}")
