#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the long-take attempt: status, detail, per-track verdicts."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
for n in ("P_long_C1305.json", "P_long_C1306.json"):
    p = OUT / n
    if not p.is_file():
        print(f"{n}: 缺失")
        continue
    rows = json.loads(p.read_text(encoding="utf-8"))
    print(f"\n=== {n}")
    for r in rows:
        print(f"  {r.get('name')} status={r.get('status')} "
              f"elapsed={round(r.get('elapsed_s') or 0)}s")
        print(f"  detail={r.get('detail')}")
        for c in r.get("channels") or []:
            ch = (c.get("stream") or 0) + 1
            print(f"    CH{ch} decision={c.get('decision')} "
                  f"reason={c.get('reason')} delay={c.get('delay_ms')} "
                  f"frames={c.get('usable_frames')} "
                  f"mad={c.get('traj_mad_ms')}")
        logs = r.get("logs") or []
        for ln in logs[:6]:
            print(f"    log: {str(ln)[:160]}")
