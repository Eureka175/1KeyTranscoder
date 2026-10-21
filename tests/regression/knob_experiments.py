#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controlled experiments for the recheck knobs.

First result was surprising and had to be separated into causes:

  * `win3` alone lost 10 of 13 fixes that verify perfectly in the middle window.
    That is not a bug in the new code -- it is the discovery that the project's
    "residual <= 0.05 ms" claim is a *single-window* claim. Measured across a
    whole clip, real clips do not hold 2.4 samples even when the fix is right.
  * so whole-clip coverage needs a coverage-aware acceptance rule. The same
    `verify_max_rel` option expresses it: limit = max(0.05 ms, |delay| * rel),
    and rel = 1/k is the materiality rule "the residual must be at most 1/k of
    the offset we removed". rel=0.05 was k=20 (too tight); k=5 is rel=0.2.

Also included: a **baseline re-run with the new code and default options**. If
that does not reproduce the historical 13 fixes exactly, the earlier losses could
be run noise (decode cache) rather than a real effect, and nothing else can be
trusted.
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
    ("ctl", []),                                    # 对照: 新代码 + 默认选项
    ("rel20", [("verify_max_rel", "0.2")]),
    ("rel20win3", [("verify_max_rel", "0.2"), ("recheck_windows", "3")]),
    ("agree2", [("verify_agree_samples", "2")]),
    ("full", [("verify_max_rel", "0.2"), ("recheck_windows", "3"),
              ("verify_agree_samples", "2")]),
    # 实测独立估计的噪声底 ~4 采样, 容差 2 会误杀正确修正 (C1174 差 2.3)。
    # 容差 5 是实测出来的可用工作点。
    ("agree5", [("verify_agree_samples", "5")]),
    ("rel20agree5", [("verify_max_rel", "0.2"),
                     ("verify_agree_samples", "5")]),
]


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for tag, sets in VARIANTS:
        if only and tag != only:
            continue
        out = OUT / f"G3_{tag}_corpus.json"
        log = LOGS / f"G3_{tag}_corpus.log"
        cmd = [sys.executable, str(CSRUN), "run", "--arm", f"g3{tag}",
               "--out", str(out), "--set", "group_anchor_enabled=1"]
        for k, v in sets:
            cmd += ["--set", f"{k}={v}"]
        t0 = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.call(cmd, cwd=str(REPO), stdout=fh,
                                 stderr=subprocess.STDOUT)
        n = 0
        if out.is_file():
            n = len(json.loads(out.read_text(encoding="utf-8")))
        print(f"  {tag}: rc={rc} {time.time() - t0:.0f}s files={n}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
