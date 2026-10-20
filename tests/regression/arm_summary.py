#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise an archive arm: verdicts and the per-track fixes it found."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm")
    a = ap.parse_args()
    p = Path(a.arm)
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"{p.name}: n={len(d)}")
    print("verdicts:", dict(Counter(r.get("status") for r in d)))
    ap_ = [r for r in d if r.get("status") == "applied"]
    print(f"applied files: {len(ap_)}")
    for r in ap_:
        for c in r.get("channels") or []:
            if c.get("decision") == "fixed":
                dly = c.get("delay_ms")
                print(f"  {Path(r['file']).parent.name:<10} {r['name']:<24} "
                      f"CH{c.get('stream') + 1} "
                      f"shift={c.get('shift_samples'):+d} "
                      f"({dly:+.4f} ms)" if dly is not None else
                      f"  {r['name']} CH{c.get('stream') + 1} "
                      f"shift={c.get('shift_samples')}")
    mf = [r for r in d if r.get("status") == "measure_failed"]
    print(f"measure_failed: {len(mf)}")
    for r in mf:
        reasons = {c.get("reason") for c in (r.get("channels") or [])
                   if c.get("reason")}
        print(f"  {r['name']:<26} {sorted(reasons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
