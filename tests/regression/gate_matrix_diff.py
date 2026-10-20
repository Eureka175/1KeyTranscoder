#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where does the offline re-decision disagree with production at baseline?

If the replica is faithful, loosening nothing must change nothing. Any track
listed here is a signal that the replica models a gate differently -- print the
row so the missing gate can be identified instead of guessed at.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_matrix import (  # noqa: E402
    BASELINE_MAD,
    BASELINE_MIN_FRAMES,
    decide,
    load_rows,
)


def main() -> int:
    rows = load_rows()
    bad = [c for c in rows
           if decide(c, BASELINE_MAD, BASELINE_MIN_FRAMES) == "fixed"
           and c.get("decision") != "fixed"]
    print(f"基线不一致: {len(bad)} / {len(rows)}")
    for c in bad:
        print(f"  {c['_file']:<24}CH{(c.get('stream') or 0) + 1} "
              f"reason={c.get('reason')} frames={c.get('usable_frames')} "
              f"conf={c.get('confidence')} mad={c.get('traj_mad_ms')} "
              f"spread={c.get('traj_spread_samples')} "
              f"drift={c.get('drift_ppm')} const={c.get('constant')} "
              f"delay={c.get('delay_ms')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
