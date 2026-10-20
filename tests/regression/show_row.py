#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the key blocks of one archive_repair.py report row."""
from __future__ import annotations

import json
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else
         r"E:\1kt_scratch\astest\fix1\archive-repair-report.json")
d = json.loads(p.read_text(encoding="utf-8"))
for r in d["rows"]:
    name = Path(r.get("dst") or r.get("src")).name
    print(f"== {name}  overall={r.get('overall')} status={r.get('status')}")
    print(f"   shifts={r.get('shifts')} layout={r.get('layout_detail')}")
    print(f"   duration_delta={r.get('duration_delta_s')} "
          f"validate={(r.get('validate') or {}).get('summary')} "
          f"gyro={r.get('gyroflow')}")
    se = r.get("sample_entry") or {}
    print(f"   sample_entry: ok={se.get('ok')} problems={se.get('problems')}")
    for k in sorted(k for k in se if k.startswith("CH")):
        print(f"     {k}: {se[k]}")
    for c in (r.get("container_checks") or []):
        if c["id"] in ("CTR-07", "CTR-08"):
            print(f"   {c['id']} {c['verdict']}: {c['detail']}")
    for c in (r.get("audio_after_checks") or []):
        print(f"   {c['id']} {c['verdict']}: {str(c['detail'])[:110]}")
    if r.get("error"):
        print(f"   ERROR {r['error'][:200]}")
