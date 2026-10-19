#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect the aggregated P4 audit results (per-script case counts)."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "work" / "regression_r087" / "out"


def main() -> int:
    d = json.loads((OUT / "p4_suites.json").read_text(encoding="utf-8"))
    total = 0
    for s in d["scripts"]:
        c = s.get("cases")
        n = len(c) if isinstance(c, list) else (c or 0)
        total += n
        print(f"{s['script']:<40} rc={s['rc']} "
              f"kind={type(c).__name__:<5} n={n:<5} fail={s.get('fail')}")
    print(f"\nsum of per-script n = {total}")
    print(f"aggregator total_cases = {d.get('total_cases')}")
    print(f"failing_scripts = {d.get('scripts_failing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
