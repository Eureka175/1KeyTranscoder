#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promote the gate-rerun output into the delivered `I:\\repaired` tree.

Only C1213 actually changes: the other twelve are stream-identical to what is
already delivered, so copying them again would just churn 1.9 GiB for nothing.
The superseded C1213 is kept under `_superseded/` so the previous delivery stays
recoverable.

The per-date report is updated in place (replacing C1213's row) so that
`verify_staged.py` keeps reading one authoritative report set; the full rerun
report is also filed under `_rep/gate-rerun/` for the record.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

NEW = Path(r"E:\1kt_scratch\gate_rerun\out")
REP = Path(r"I:\repaired")
NAME = "20260904_C1213.MP4"


def main() -> int:
    src = NEW / NAME
    hits = list(REP.rglob(NAME))
    if not hits:
        print(f"目标不存在: {NAME}")
        return 2
    dst = hits[0]
    print(f"替换 {dst}")

    sup = REP / "_superseded"
    sup.mkdir(exist_ok=True)
    shutil.copy2(dst, sup / NAME)
    print(f"旧版本备份 -> {sup / NAME}")

    shutil.copy2(src, dst)
    print(f"新版本写入 {dst}  ({dst.stat().st_size / 1024 ** 2:.1f} MiB)")

    # 更新按日期报告里 C1213 的那一行
    new_report = json.loads(
        (NEW / "archive-repair-report.json").read_text(encoding="utf-8"))
    new_row = next(r for r in new_report["rows"]
                   if Path(r.get("src", "")).name == NAME)
    per_date = REP / "_rep" / "20260904" / "archive-repair-report.json"
    if per_date.is_file():
        doc = json.loads(per_date.read_text(encoding="utf-8"))
        replaced = False
        for i, r in enumerate(doc.get("rows") or []):
            if Path(r.get("src", "")).name == NAME:
                doc["rows"][i] = new_row
                replaced = True
        per_date.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"报告行已更新 ({'ok' if replaced else '未找到, 未改'})")
    else:
        print(f"未找到按日期报告: {per_date}")

    arch = REP / "_rep" / "gate-rerun"
    arch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(NEW / "archive-repair-report.json",
                 arch / "archive-repair-report.json")
    shutil.copy2(NEW / "archive-repair-report.md",
                 arch / "archive-repair-report.md")
    print(f"完整重跑报告归档 -> {arch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
