#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise a staged repair set and generate the (never-run) apply-back list."""
from __future__ import annotations

import json
import sys
from pathlib import Path

STAGED = Path(sys.argv[1] if len(sys.argv) > 1 else r"E:\1kt_archive_repaired")


def main() -> int:
    rows: list[dict] = []
    for p in sorted((STAGED / "_rep").rglob("archive-repair-report.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8"))["rows"])
    rep = [r for r in rows if r.get("status") == "applied"]
    rep.sort(key=lambda r: Path(r["src"]).name)

    print(f"{'file':<24}{'shifts':<24}{'layout':<30}{'MiB':>8}")
    print("-" * 90)
    lines = [
        "# 审核通过后再执行: 把暂存件逐个替换回归档。",
        "# 本脚本由 summarise_staged.py 生成, **从未被自动运行**。",
        "# 内建路径 (等价): archive_repair.py repair ... --in-place "
        "--backup DIR --yes",
        "",
    ]
    total = 0
    for r in rep:
        name = Path(r["src"]).name
        sh = r.get("shifts") or {}
        sz = Path(r["dst"]).stat().st_size
        total += sz
        print(f"{name:<24}{str(sh):<24}{str(r.get('layout_detail')):<30}"
              f"{sz / 2**20:>8.1f}")
        lines.append(f"# shift={sh}")
        lines.append(f'Copy-Item -LiteralPath "{r["dst"]}" '
                     f'-Destination "{r["src"]}" -Force')
    (STAGED / "_APPLY-BACK.ps1").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print(f"\n{len(rep)} 个文件, {total / 2**30:.2f} GiB")
    print(f"apply-back -> {STAGED / '_APPLY-BACK.ps1'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
