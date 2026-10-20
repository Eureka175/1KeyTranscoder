#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How many tracks would a `min_shift_samples` guard change, per arm?

Reads the measured arms and reports every applied shift with |shift| <= N,
so a threshold decision is made against data rather than intuition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "work" / "regression_r087" / "out"
ARMS = ["ARM_off.json", "ARM_on.json", "ARM_holdout_off.json",
        "ARM_holdout_on.json", "ARCH_off.json", "ARCH_on.json"]


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"guard: |shift| <= {limit} samples -> already_aligned\n")
    total_all = total_small = 0
    for name in ARMS:
        p = OUT / name
        if not p.is_file():
            print(f"{name:<24} MISSING")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        fixes = []
        for r in d:
            for c in r.get("channels") or []:
                if c.get("decision") == "fixed":
                    fixes.append((Path(r["file"]).name, c.get("stream"),
                                  int(c.get("shift_samples") or 0)))
        small = [f for f in fixes if abs(f[2]) <= limit]
        total_all += len(fixes)
        total_small += len(small)
        print(f"{name:<24} fixes={len(fixes):<4} "
              f"within {limit}: {len(small)}")
        for f in small:
            print(f"    {f[0]:<26} CH{f[1] + 1} shift={f[2]:+d} "
                  f"({f[2] / 48:.4f} ms)")
    print(f"\ntotal fixes={total_all}  affected={total_small}  "
          f"remaining={total_all - total_small}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
