#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the per-track outcome of the single-file re-runs."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")

for p in sorted(OUT.glob("CHK_*.json")):
    rows = json.loads(p.read_text(encoding="utf-8"))
    print(f"\n{p.name}: {len(rows)} file(s)")
    for r in rows:
        print(f"  {r.get('name')}  status={r.get('status')}")
        verify = r.get("verify") or {}
        for c in r.get("channels") or []:
            ch = (c.get("stream") or 0) + 1
            v = verify.get(str(c.get("stream"))) or {}
            print(f"    CH{ch}: {c.get('decision')} "
                  f"reason={c.get('reason')} delay={c.get('delay_ms')} "
                  f"frames={c.get('usable_frames')} "
                  f"shift={c.get('shift_samples')} "
                  f"verify_ok={v.get('ok')} "
                  f"after0={v.get('after_is_zero')}")
