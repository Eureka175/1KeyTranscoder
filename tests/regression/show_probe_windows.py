#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick view of the probe's window resolution by geometry (for smoke checks)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

base = Path(sys.argv[1] if len(sys.argv) > 1 else r"F:\_ltp_smoke2")
d = json.loads((base / "s2_windows.json").read_text(encoding="utf-8"))
for clip, rows in d.items():
    print(f"=== {clip}")
    sel = [r for r in rows if r["window_s"] == 30 and r["hop_s"] == 10]
    for r in sel:
        fw = r["frames_per_window"]
        print(f"  CH{r['target']} {r['geometry']:<22} "
              f"resolved={r['n_windows_resolved']:>4} "
              f"({100 * r['resolved_fraction']:>5.1f}%) "
              f"mean_frames={fw['mean']:.2f} p50={fw['p50']:.0f} "
              f"ge3={fw['windows_ge3']:>3} hist={fw['hist']}")
