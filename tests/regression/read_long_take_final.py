#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the long-take probe's decision numbers (oracle / refsweep / gaps)."""
from __future__ import annotations

import json
from pathlib import Path

B = Path(r"E:\1kt_work\long_take_probe")


def load(name):
    p = B / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


o = load("s2e_oracle.json") or {}
print("=== S2e 上限（逐窗换几何+参考）===")
for clip, d in o.items():
    for scale in ("30/10s", "60/20s", "15/5s"):
        per = (d.get("targets") or {}).get(scale) or {}
        if not per:
            continue
        cells = "  ".join(f"CH{t} {100 * v['union_all_fraction']:.0f}%"
                          f"{'PASS' if v['union_all_passes_60pct'] else 'FAIL'}"
                          for t, v in sorted(per.items()))
        print(f"  {clip} {scale}: {cells}")

r = load("s2c_refsweep.json") or {}
print("\n=== S2c 最佳参考（30/10 s 解析窗数）===")
for clip, rows in r.items():
    print(f"  {clip}")
    by: dict[int, list] = {}
    for x in rows:
        by.setdefault(x["tgt"], []).append(x)
    for tgt, rs in sorted(by.items()):
        rs.sort(key=lambda x: -x["resolved_30_10"])
        cells = " | ".join(f"CH{x['ref']} {x['resolved_30_10']}窗"
                           f"({100 * x['resolved_fraction_30_10']:.0f}%)"
                           for x in rs[:2])
        print(f"    CH{tgt}: {cells}")

g = load("s2f_gaps.json") or {}
print("\n=== S2f 缺口代价（hold）===")
for clip, rows in g.items():
    print(f"  {clip}")
    for x in rows[:3]:
        h = x["hold_gap_cost"]
        print(f"    {x['geometry']} CH{x['target']}: 缺口 {h['n_gaps']} "
              f"误差 p50/p90 {h['err_p50']}/{h['err_p90']} 采样 "
              f"(>1ms {h['gaps_gt_48_samples']}) 窗内MAD p50 "
              f"{x['window_mad_ms']['p50']}ms")
