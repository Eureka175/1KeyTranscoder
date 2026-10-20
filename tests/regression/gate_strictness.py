#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How strict is each gate? Count the tracks it refuses, and by how much they
are actually off.

Reads a measured arm (per-track decision / reason / measured delay) and
answers, per refusal reason:
  * how many tracks
  * their measured |delay| distribution (this is what is being *left* unfixed)
  * how many are "clearly off" (>= 5 samples, the minimum shift we would write)

A track refused at 20 ms is not a conservative success; it is 20 ms of
uncorrected error. That distinction is the whole point of this table.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    paths = sys.argv[1:] or [
        r"F:\1KeyTranscoder\work\regression_r087\out\ARCH_on5.json",
        r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_on.json",
        r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_holdout_on.json",
    ]
    for p in paths:
        path = Path(p)
        if not path.is_file():
            print(f"{p}: missing")
            continue
        arm = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n=== {path.name}  ({len(arm)} files)")
        buckets: dict[str, list[float]] = defaultdict(list)
        dec_count: dict[str, int] = defaultdict(int)
        for r in arm:
            for c in r.get("channels") or []:
                dec = c.get("decision")
                dec_count[dec] += 1
                if dec != "untouched":
                    continue
                reason = c.get("reason") or "?"
                d = c.get("delay_ms")
                if isinstance(d, (int, float)) and d == d:
                    buckets[reason].append(abs(d))
                else:
                    buckets[reason].append(float("nan"))
        print(f"  decisions: {dict(dec_count)}")
        print(f"  {'reason':<24}{'n':>5}{'median|d|':>11}{'max|d|':>9}"
              f"{'>=0.104ms':>11}{'>=1ms':>8}")
        print("  " + "-" * 68)
        tot_off = 0
        tot_n = 0
        for reason in sorted(buckets):
            vals = [v for v in buckets[reason] if v == v]
            n = len(buckets[reason])
            med = statistics.median(vals) if vals else float("nan")
            mx = max(vals) if vals else float("nan")
            ge_small = sum(1 for v in vals if v >= 1000 * 5 / 48000)
            ge_1ms = sum(1 for v in vals if v >= 1.0)
            tot_n += n
            tot_off += ge_small
            print(f"  {reason:<24}{n:>5}{med:>11.3f}{mx:>9.2f}"
                  f"{ge_small:>11}{ge_1ms:>8}")
        print(f"  {'合计 untouched':<24}{tot_n:>5}"
              f"{'':>11}{'':>9}{tot_off:>11}"
              f"  <- 其中 {tot_off} 条偏差 >= 5 采样(约 0.104ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
