#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-check archive_repair.py's verdicts against a measured arm.

The tool has its own measurement path; if it disagrees with the arm the archive
scan produced, one of them is wrong. Compares per file:
  arm `applied`  <-> tool AUD-03 == FAIL
  arm `already_aligned` <-> tool overall != FAIL
  arm `measure_failed`  <-> tool AUD-03 == UNMEASURABLE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    arm_path, rep_path = sys.argv[1], sys.argv[2]
    arm = {Path(r["file"]).name: r for r in
           json.loads(Path(arm_path).read_text(encoding="utf-8"))}
    rep = json.loads(Path(rep_path).read_text(encoding="utf-8"))["rows"]
    rep = {Path(r.get("path") or "").name: r for r in rep}

    mismatches = []
    tally: dict[str, int] = {}
    covered = 0
    # 以**报告覆盖到的文件**为准逐个对照 —— 报告通常只跑了一个子目录,
    # 拿整份 arm 去比会把"不在本次范围"误报成"工具漏了"。
    for name, t in rep.items():
        r = arm.get(name)
        if r is None:
            mismatches.append(f"{name}: 工具报了但 arm 里没有")
            continue
        st = r.get("status")
        tally[st] = tally.get(st, 0) + 1
        covered += 1
        aud3 = next((c for c in t.get("checks") or []
                     if c["id"] == "AUD-03"), {})
        v = aud3.get("verdict")
        if st == "applied" and v != "FAIL":
            mismatches.append(f"{name}: arm=applied 但工具 AUD-03={v}")
        elif st == "already_aligned" and v not in ("PASS", "SKIP"):
            mismatches.append(f"{name}: arm=already_aligned 但 AUD-03={v}")
        elif st == "measure_failed" and v not in ("UNMEASURABLE", "SKIP"):
            mismatches.append(f"{name}: arm=measure_failed 但 AUD-03={v}")
        elif st == "skipped_not_4mono" and v not in ("SKIP", "UNMEASURABLE"):
            mismatches.append(f"{name}: arm=skipped 但 AUD-03={v}")
    print(f"本次对照覆盖 {covered} 个文件, arm 状态分布: {tally}")
    print(f"不一致: {len(mismatches)}")
    for m in mismatches[:20]:
        print("  ", m)
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
