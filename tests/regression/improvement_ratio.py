#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Would fixing a refused track actually be better than leaving it?

For every refused track that is measurably off, compare the offset we would
remove (|delay|) with the variation we would have to live with (the measured
trajectory spread). Ratio = how many times better the corrected version is.

A refusal is only defensible when that ratio is near 1. When it is 10x, the
gate is not protecting anything -- it is preserving error.
"""
from __future__ import annotations

import collections
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import load, num  # noqa: E402

MIN_SAMPLES = 5
SR = 48000


def main() -> int:
    rows = load()
    meas = [c for c in rows if num(c.get("delay_samples")) is not None]
    ref = [c for c in meas
           if c.get("decision") == "untouched"
           and abs(num(c.get("delay_ms"), 0.0)) >= 1000 * MIN_SAMPLES / SR]
    rowsout = []
    for c in ref:
        d = abs(num(c.get("delay_ms"), 0.0))
        sp = c["_spread_ms"] or 0.0
        ratio = d / sp if sp > 0 else float("inf")
        rowsout.append((d, sp, ratio, c))

    finite = sorted(r[2] for r in rowsout if r[2] != float("inf"))
    print(f"被拒且 |delay| >= {MIN_SAMPLES} 采样: {len(rowsout)} 条")
    print(f"改善倍数 |delay|/spread: 中位 {st.median(finite):.1f}x  "
          f"最小 {finite[0]:.2f}x  最大 {finite[-1]:.1f}x")
    print(f"  改善倍数 <1（修了更差）: {sum(1 for v in finite if v < 1)}")
    print(f"  1–3: {sum(1 for v in finite if 1 <= v < 3)}")
    print(f"  >=3: {sum(1 for v in finite if v >= 3)}")
    print(f"  spread=0（无变化可接受）: {len(rowsout) - len(finite)}")

    print("\n最差 5 条（改善最小）:")
    for d, sp, r, c in sorted(rowsout, key=lambda x: x[2])[:5]:
        print(f"  {c['_file'][:14]} CH{(c.get('stream') or 0) + 1} "
              f"delay={d:6.2f}ms spread={sp:5.2f}ms ratio={r:5.2f} "
              f"reason={c.get('reason')} frames={c.get('usable_frames')}")

    g: dict[str, list[float]] = collections.defaultdict(list)
    for d, sp, r, c in rowsout:
        if r != float("inf"):
            g[c.get("reason") or "?"].append(r)
    print("\n按原因分组的改善倍数:")
    for k, v in sorted(g.items()):
        print(f"  {k:<18} n={len(v):<3} 中位 {st.median(v):5.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
