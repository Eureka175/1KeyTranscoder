#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gap cost / window MAD summary from the long-take probe."""
from __future__ import annotations

import json
from pathlib import Path

B = Path(r"F:\_long_take_probe")
d = json.loads((B / "s2f_gaps.json").read_text(encoding="utf-8"))
for clip, rows in d.items():
    print(clip)
    for r in rows:
        g = r["hold_gap_cost"]
        print(f"  {r['geometry']:<20} CH{r['target']}: "
              f"解析 {r['resolved']}/{r['resolved'] + r['unresolved']}  "
              f"缺口 {g['n_gaps']}  误差p50/p90 {g['err_p50']}/{g['err_p90']}  "
              f">1ms {g['gaps_gt_48_samples']}  "
              f"窗内MAD p50 {r['window_mad_ms']['p50']}ms")
