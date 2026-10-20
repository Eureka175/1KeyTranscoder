#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the two new recheck knobs on the real corpora.

They serve opposite purposes and must be measured separately:

  verify_max_rel   loosens the gate for a LARGE STATIONARY offset, where the
                   absolute 0.05 ms gate throws away a 99% improvement
                   (C1213 CH1: 20.79 ms -> 0.08 ms).
  recheck_windows  tightens the gate against a NON-STATIONARY offset that the
                   middle-anchor-only check cannot see
                   (C1179 CH1: -0.2 samples in the middle, 34 samples across).

Runs: corpus (default root) and archive (18 date dirs), each with rel-only,
windows-only and both. Output names G2_*.json so nothing overwrites the G_* set.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
OUT = REPO / "work" / "regression_r087" / "out"
LOGS = REPO / "work" / "regression_r087" / "logs"
CSRUN = REPO / "work" / "pairensics" / "grouped_anchor" / "csrun.py"

VARIANTS = [
    ("rel", [("verify_max_rel", "0.05")]),
    ("win3", [("recheck_windows", "3")]),
    ("both", [("verify_max_rel", "0.05"), ("recheck_windows", "3")]),
]


def archive_roots() -> list[str]:
    roots = []
    for d in sorted(Path("I:/").iterdir()):
        if d.is_dir() and len(d.name) == 8 and d.name.isdigit():
            roots.append(str(d))
    return roots


def main() -> int:
    scope = sys.argv[1] if len(sys.argv) > 1 else "corpus"
    roots = archive_roots() if scope == "archive" else []
    print(f"scope={scope} roots={len(roots)}")
    for tag, sets in VARIANTS:
        out = OUT / f"G2_{tag}_{scope}.json"
        log = LOGS / f"G2_{tag}_{scope}.log"
        cmd = [sys.executable, str(CSRUN), "run", "--arm", f"g2{tag}",
               "--out", str(out), "--set", "group_anchor_enabled=1"]
        for k, v in sets:
            cmd += ["--set", f"{k}={v}"]
        for r in roots:
            cmd += ["--root", r]
        t0 = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.call(cmd, cwd=str(REPO), stdout=fh,
                                 stderr=subprocess.STDOUT)
        dt = time.time() - t0
        n = len(json.loads(out.read_text(encoding="utf-8"))) if out.is_file() else 0
        print(f"  {tag}: rc={rc} {dt:.0f}s files={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
