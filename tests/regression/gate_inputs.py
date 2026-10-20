#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate-input matrix: what the stored measurement fields actually say for each
verdict group.

Everything here comes from the measured arms — no re-measurement. It answers
"which gate is doing the refusing, and what values does it see?" before any
policy is changed.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ARMS = [
    r"F:\1KeyTranscoder\work\regression_r087\out\ARCH_on5.json",
    r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_on.json",
    r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_holdout_on.json",
]

FIELDS = ("delay_ms", "confidence", "traj_mad_ms", "traj_spread_samples",
          "usable_frames", "drift_ppm")


def num(v):
    return v if isinstance(v, (int, float)) and v == v else None


def describe(rows, group: str, pred) -> None:
    xs = [c for c in rows if pred(c)]
    if not xs:
        print(f"  {group:<20} (none)")
        return
    cells = []
    for f in FIELDS:
        vs = [num(c.get(f)) for c in xs]
        vs = [v for v in vs if v is not None]
        cells.append(f"{f.split('_')[0][:6]}={st.median(vs):>8.2f}" if vs
                     else f"{f.split('_')[0][:6]}={'':>8}")
    print(f"  {group:<20} n={len(xs):<4} " + " ".join(cells))


def main() -> int:
    which = sys.argv[1:] or ARMS
    for p in which:
        path = Path(p)
        if not path.is_file():
            print(f"{p}: missing")
            continue
        arm = json.loads(path.read_text(encoding="utf-8"))
        rows = [c for r in arm for c in (r.get("channels") or [])]
        print(f"\n=== {path.name}: {len(arm)} files, {len(rows)} track-rows")
        print(f"  decisions: {dict(Counter(c.get('decision') for c in rows))}")
        describe(rows, "fixed", lambda c: c.get("decision") == "fixed")
        describe(rows, "already_aligned",
                 lambda c: c.get("decision") == "already_aligned")
        for reason in ("non_constant", "low_confidence", "recheck_residual",
                       "insufficient_frames", "out_of_range"):
            describe(rows, reason, lambda c, r=reason: c.get("reason") == r)
        nc = [c for c in rows if c.get("reason") == "non_constant"]
        fx = [c for c in rows if c.get("decision") == "fixed"]
        print(f"  constant flag: non_constant rows -> "
              f"{dict(Counter(c.get('constant') for c in nc))}; "
              f"fixed rows -> {dict(Counter(c.get('constant') for c in fx))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
