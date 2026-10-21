#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rewrite the review index from the authoritative AUD-04 check, and group the
fully-aligned files into a subfolder via hardlinks (same volume, no extra space).

Why a separate grouping instead of "trust the folder": AUD-03 PASS only means
"no adoptable fix was left over" -- a track refused by a quality gate does not
make it fail. Nine of the thirteen delivered files therefore still carry one
track 23.6-27.3 ms off while their reports read AUD-03 PASS. The grouping below
is based on AUD-04, which exists precisely to say that out loud.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DST = Path(r"F:\对齐复核")
REPORT = Path(r"E:\1kt_scratch\aud04\archive-repair-report.json")
GROUP = "全轨已对齐"


def main() -> int:
    d = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = {}
    for r in d.get("rows") or []:
        nm = Path(r.get("path", "")).name
        aud = {c["id"]: c for c in (r.get("checks") or [])}
        after = r.get("audio") or {}
        rows[nm] = {"aud04": aud.get("AUD-04") or {},
                    "aud03": aud.get("AUD-03") or {},
                    "aud02": aud.get("AUD-02") or {},
                    "tracks": after.get("rows") or []}

    full = [nm for nm, x in rows.items()
            if (x["aud04"].get("verdict") == "PASS")]
    part = [nm for nm in rows if nm not in full]

    gdir = DST / GROUP
    gdir.mkdir(exist_ok=True)
    for nm in full:
        src = DST / nm
        dst = gdir / nm
        if dst.exists():
            dst.unlink()
        os.link(src, dst)          # 同卷硬链接: 不占额外空间

    lines = [
        "# 对齐复核素材",
        "",
        "由 `archive_repair.py repair --groups --opt verify_max_rel=0.2 "
        "--opt recheck_windows=3 --opt verify_agree_samples=5` 产出的 13 件交付版本。",
        "",
        f"## 结论：**{len(full)} 件全轨已对齐**，{len(part)} 件仍有一条轨被拒",
        "",
        f"- 全轨已对齐（也放了一份硬链接在 `{GROUP}\\` 里）：",
        "  " + "、".join(f"`{n}`" for n in full),
        "",
        "## 为什么报告里写着 AUD-03 PASS 却仍有偏移",
        "",
        "`AUD-03` 的 PASS 条件是生产判 `already_aligned`，而那个状态的语义是",
        "**“没有可采纳的修正”** —— 被质量门拒掉的轨（`non_constant` / "
        "`low_confidence`）不计入。所以一个文件可以一面 AUD-03 PASS、",
        "一面留着 23–27 ms 的未修偏移。",
        "",
        "为此新增 **`AUD-04`**：单列“被拒但确实还偏”的轨。下表以它为准。",
        "",
        "| 文件 | AUD-04 | 仍被拒且偏移 ≥ 5 采样 | 本次施加的移位 |",
        "|---|---|---|---|",
    ]
    for nm in sorted(rows):
        x = rows[nm]
        det = str(x["aud04"].get("detail") or "")
        off = "-" if x["aud04"].get("verdict") == "PASS" else det
        shifts = []
        for t in x["tracks"]:
            if t.get("decision") == "fixed":
                shifts.append(f"CH{(t.get('stream') or 0) + 1}"
                              f"{(t.get('shift_samples') or 0):+d}")
        lines.append(f"| `{nm}` | {x['aud04'].get('verdict')} | "
                     f"{off} | {', '.join(shifts) or '-'} |")

    lines += ["", "## 那些被拒的轨为什么修不了", "",
              "它们是**偏移不恒定**的轨：全片位移范围 1.7–1.8 ms，单个刚性整数",
              "移位无法把整条轨对齐（实测 `C1179` 一类：中部窗 −0.2 采样、",
              "全片最差 33.9 采样）。需要**逐窗时变修正**（`--channel-sync-tv`），",
              "而该路径目前受限于测量产量，尚未启用。", ""]
    (DST / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"全轨已对齐 {len(full)} 件: {full}")
    print(f"仍有偏移 {len(part)} 件: {sorted(part)}")
    print(f"INDEX.md -> {DST / 'INDEX.md'}")
    print(f"硬链接目录 -> {gdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
