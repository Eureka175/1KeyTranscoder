#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the verify block of the newly added fixes, in full.

`exact=True / maxdiff=0.0` only says the written samples are the source shifted
by the claimed amount. It says nothing about whether that amount was right --
that is `before_matches_shift` (the claimed shift equals the measured pre-fix
delay) and `after_is_zero` (an independent residual on the output is zero).
Two of the new fixes fail both, which decides whether the variant is adoptable.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
TARGETS = {
    "archive mad=2.0": ("V_mad2_arch.json",
                        [("20260830_C0913.MP4", 0), ("20260904_C1179.MP4", 0)]),
    "archive both": ("V_both_arch.json",
                     [("20260830_C0913.MP4", 0), ("20260904_C1179.MP4", 0),
                      ("20260830_C0950.MP4", 0)]),
}


def main() -> int:
    for label, (fname, want) in TARGETS.items():
        p = OUT / fname
        if not p.is_file():
            print(f"{label}: 缺 {fname}")
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        idx = {r.get("name"): r for r in rows}
        print(f"\n=== {label}")
        for name, ch in want:
            r = idx.get(name)
            if not r:
                print(f"  {name}: 不在 arm 里")
                continue
            v = (r.get("verify") or {}).get(str(ch)) or {}
            print(f"\n  {name} CH{ch + 1}  status={r.get('status')}")
            print(f"    shift_samples = {v.get('shift_samples')}")
            for k in ("before", "after", "src_vs_fixed"):
                d = v.get(k) or {}
                if d:
                    print(f"    {k:<13} n={d.get('n')} "
                          f"median={d.get('median')} p25={d.get('p25')} "
                          f"p75={d.get('p75')} "
                          f"frac_usable={d.get('frac_usable')}")
            e = v.get("exact") or {}
            print(f"    exact         {e}")
            for k in ("ok", "before_matches_shift", "after_is_zero"):
                print(f"    {k:<21} {v.get(k)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
