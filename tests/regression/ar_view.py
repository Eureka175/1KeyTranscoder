#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print an archive_repair.py report as a compact table."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    p = Path(sys.argv[1] if len(sys.argv) > 1 else
             r"E:\1kt_scratch\ar_smoke\archive-repair-report.json")
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = d["rows"]
    print(f"{d['title']} — {len(rows)} 行")
    for r in rows:
        name = Path(r.get("path") or r.get("dst") or r.get("src")).name
        print(f"\n=== {name}  overall={r.get('overall')}")
        if r.get("status"):
            print(f"    status={r.get('status')} shifts={r.get('shifts')} "
                  f"copied={r.get('file_copied')} "
                  f"layout={r.get('layout_detail')}")
            print(f"    top_level {r.get('top_level_before')} -> "
                  f"{r.get('top_level_after')}")
            print(f"    duration_delta={r.get('duration_delta_s')} "
                  f"validate={(r.get('validate') or {}).get('summary')} "
                  f"gyro={r.get('gyroflow')}")
            if r.get("acccurate"):
                print(f"    accurate={r.get('acccurate')}")
            if r.get("error"):
                print(f"    ERROR {r['error'][:200]}")
        for c in r.get("checks") or []:
            print(f"    {c['id']:<8} {c['verdict']:<14} "
                  f"{str(c.get('detail') or '')[:120]}")
        for c in r.get("audio_after_checks") or []:
            print(f"    {c['id']:<8} {c['verdict']:<14} "
                  f"{str(c.get('detail') or '')[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
