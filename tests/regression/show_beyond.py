#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name the fixes that exceed the displacement bound or cannot be verified."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000

for p in ("P_mat.json", "P_mat10.json", "P_mat20.json"):
    path = OUT / p
    if not path.is_file():
        continue
    print(f"== {p}")
    for r in json.loads(path.read_text(encoding="utf-8")):
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            v = (r.get("verify") or {}).get(str(ch)) or {}
            aft = (v.get("after") or {}).get("median")
            mat = c.get("materiality") or {}
            disp = mat.get("displacement_ms")
            name = r.get("name")
            if aft is None or aft != aft:
                print(f"  UNVERIFIABLE {name} CH{(ch or 0) + 1} "
                      f"shift={v.get('shift_samples')} 位移={disp}ms")
                continue
            if disp is None:
                if abs(aft) > 1.0:
                    print(f"  BEYOND(非材料性) {name} CH{(ch or 0) + 1} "
                          f"after={aft:.1f} 采样 (无材料性记录)")
                continue
            lim = float(disp) * SR / 1000.0
            if abs(aft) > lim:
                print(f"  BEYOND {name} CH{(ch or 0) + 1} "
                      f"after={aft:.1f} 采样 位移={float(disp):.2f}ms "
                      f"({lim:.0f} 采样)")
