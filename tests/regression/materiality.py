#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate a *materiality* gate as a unified replacement for the constancy and
recheck gates.

Rule: correct the track when the offset being removed is much larger than the
variation the correction leaves behind, i.e. |delay| >= k * spread.
That is the question a user actually cares about ("is the file better now?"),
whereas `mad_max_ms` answers a different one ("is the trajectory flat?").

k = 2 / 3 / 5 / 10 are reported, together with the tracks a k = 3 rule would
refuse -- a refusal is only defensible if the ratio there is near 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import load, num  # noqa: E402

SR = 48000
MIN_SAMPLES = 5


def main() -> int:
    rows = load()
    meas = [c for c in rows if num(c.get("delay_samples")) is not None]
    ref = [c for c in meas
           if c.get("decision") == "untouched"
           and abs(num(c.get("delay_ms"), 0.0)) >= 1000 * MIN_SAMPLES / SR]

    print("材料性判据 |delay| >= k × spread:")
    for k in (2, 3, 5, 10):
        ok = [c for c in ref
              if (c["_spread_ms"] or 0.0) * k <= abs(num(c.get("delay_ms"), 0.0))]
        rec = sum(abs(num(c.get("delay_ms"), 0.0)) for c in ok)
        acc = sum(c["_spread_ms"] or 0.0 for c in ok)
        print(f"  k={k:<3} 可修 {len(ok):>3} 条 / 回收 {rec:>7.1f} ms / "
              f"接受 {acc:>6.1f} ms / 比值 {rec / max(1e-9, acc):>6.1f}")

    print("\nk=3 时会拒绝的轨（应当拒绝才合理）:")
    for c in ref:
        sp = c["_spread_ms"] or 0.0
        d = abs(num(c.get("delay_ms"), 0.0))
        if sp * 3 > d:
            ratio = d / sp if sp else float("inf")
            print(f"  {c['_file'][:14]:<14} CH{(c.get('stream') or 0) + 1} "
                  f"delay={d:6.2f}ms spread={sp:7.2f}ms ratio={ratio:6.2f} "
                  f"{c.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
