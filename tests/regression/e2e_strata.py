#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise the e2e candidate inventory into the strata actually available."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "work" / "regression_r087" / "out"


def main() -> int:
    rows = json.loads((OUT / "e2e_candidates.json").read_text(encoding="utf-8"))
    print(f"total {len(rows)}")
    print(Counter((r["verdict"], r["audio_streams"]) for r in rows))
    c4 = [r for r in rows if r["audio_streams"] >= 4]
    print(f"\n--- {len(c4)} clips with >=4 audio streams, by make/model ---")
    for k, v in Counter((r["make"], r["model"]) for r in c4).items():
        print(f"  {k}: {v}")

    print("\n--- applied clips ---")
    for r in rows:
        if r["verdict"] == "applied":
            print(f"  {r['name']:<26}{r['size_mb']:>9.1f}MB "
                  f"{r['duration']:>7.1f}s  {r['make']}/{r['model']}  "
                  f"{r['fixed']}")

    print("\n--- Sony-like, >=4 streams, <=600 MB, by size ---")
    s = [r for r in c4 if r["size_mb"] <= 600]
    for r in sorted(s, key=lambda x: x["size_mb"])[:20]:
        print(f"  {r['name']:<26}{r['size_mb']:>9.1f}MB "
              f"{r['duration']:>7.1f}s {r['verdict']:<16} "
              f"{r['make']}/{r['model']}")

    print("\n--- measure_failed / non_constant-ish with >=4 streams ---")
    for r in c4:
        if r["verdict"] in ("measure_failed",):
            print(f"  {r['name']:<26}{r['size_mb']:>9.1f}MB "
                  f"{r['duration']:>7.1f}s {r['make']}/{r['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
