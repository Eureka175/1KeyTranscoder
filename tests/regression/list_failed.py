#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List the files a repair run could not even read, with reasons."""
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"I:\repaired\_rep")

rows: list[dict] = []
for p in sorted(Path(root).rglob("archive-repair-report.json")):
    rows.extend(json.loads(p.read_text(encoding="utf-8"))["rows"])

bad = [r for r in rows if r.get("overall") == "FAILED"]
print(f"FAILED {len(bad)} 个（共 {len(rows)} 条记录）")
for r in bad:
    src = Path(r.get("src") or "")
    size = src.stat().st_size / 2 ** 20 if src.is_file() else -1
    print(f"  {src.name:<26}{size:8.1f} MiB  {r.get('error')}")

applied = [r for r in rows if r.get("status") == "applied"]
print(f"\nREPAIRED {len(applied)} 个:")
for r in sorted(applied, key=lambda x: Path(x["src"]).name):
    print(f"  {Path(r['src']).name:<26}{str(r.get('shifts'))}")
