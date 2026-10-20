#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Show concrete refused rows so the matrix is built on real row shapes.

Prints every refused track that is measurably off (>= 5 samples), with the
inputs each gate reads: usable_frames, confidence, traj_mad_ms,
traj_spread_samples, drift_ppm, constant.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ARM = Path(r"F:\1KeyTranscoder\work\regression_r087\out\ARCH_on5.json")
MIN_SAMPLES = 5


def main() -> int:
    arm = json.loads(ARM.read_text(encoding="utf-8"))
    rows = []
    for r in arm:
        for c in r.get("channels") or []:
            d = c.get("delay_ms")
            if c.get("decision") != "untouched" or not isinstance(d, (int, float)):
                continue
            if abs(d) < 1000 * MIN_SAMPLES / 48000:
                continue
            rows.append((Path(r["file"]).name, c))
    print(f"untouched 且 |delay| >= {MIN_SAMPLES} 采样: {len(rows)} 条\n")
    hdr = (f"{'file':<24}{'ch':<4}{'reason':<20}{'delay':>8}{'frames':>7}"
           f"{'conf':>7}{'mad':>7}{'spread':>8}{'drift':>8}{'const':>7}")
    print(hdr)
    print("-" * len(hdr))
    for name, c in sorted(rows, key=lambda x: str(x[1].get("reason"))):
        def f(k, w=7, p=2):
            v = c.get(k)
            return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"
        print(f"{name:<24}CH{(c.get('stream') or 0) + 1:<3}"
              f"{str(c.get('reason')):<20}{f('delay_ms', 8)}"
              f"{str(c.get('usable_frames')):>7}"
              f"{f('confidence'):>7}{f('traj_mad_ms')}{f('traj_spread_samples', 8, 1)}"
              f"{f('drift_ppm', 8, 2)}{str(c.get('constant')):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
