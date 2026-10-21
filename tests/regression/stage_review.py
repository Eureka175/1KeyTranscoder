#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect the delivered repairs for review and write an honest index.

"Aligned" is not a property of the file as a whole: every delivered file has at
least one track that was shifted, and some still carry track-level refusals
(non_constant / low_confidence) where a rigid integer shift would not have
helped. The index states, per file, which tracks are aligned and which are not,
with the measured offsets -- so the material can be judged by measurement
instead of by the fact that the pipeline reported REPAIRED.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

SRC = Path(r"I:\repaired")
DST = Path(r"F:\对齐复核")
REP = SRC / "_rep"
MIN_SAMPLES = 5
SR = 48000


def collect() -> dict[str, dict]:
    """Merge all delivered report rows, keyed by file name."""
    out: dict[str, dict] = {}
    for p in sorted(REP.rglob("archive-repair-report.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d.get("rows") or []:
            if r.get("status") != "applied":
                continue
            name = Path(r.get("src") or r.get("path") or "").name
            if name:
                out[name] = r
    return out


def track_state(row: dict) -> tuple[list[str], list[str], float]:
    """(aligned tracks, still-off tracks, sum of remaining |delay| ms)"""
    after = (row.get("audio_after") or {})
    aligned, off = [], []
    total = 0.0
    for t in after.get("rows") or []:
        ch = (t.get("stream") or 0) + 1
        dec = t.get("decision")
        d = t.get("delay_ms")
        if dec in ("anchor", "already_aligned", "fixed"):
            aligned.append(f"CH{ch}({d:+.4f}ms)" if isinstance(d, (int, float))
                           else f"CH{ch}")
            continue
        if isinstance(d, (int, float)) and abs(d) >= 1000 * MIN_SAMPLES / SR:
            off.append(f"CH{ch}({d:+.2f}ms,{t.get('reason')})")
            total += abs(d)
        else:
            off.append(f"CH{ch}({t.get('reason')})")
    return aligned, off, total


def main() -> int:
    rows = collect()
    files = sorted(p for p in SRC.rglob("*.MP4"))
    files = [f for f in files if "_superseded" not in str(f)]
    print(f"交付件数 {len(files)}, 报告行 {len(rows)}")

    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    lines = ["# 对齐复核素材", "",
             "由 `archive_repair.py repair --groups --opt verify_max_rel=0.2 "
             "--opt recheck_windows=3 --opt verify_agree_samples=5` 产出的交付版本。",
             "",
             "**“已对齐”是逐轨属性，不是整件属性。** 下表如实列出每件里仍然"
             "被拒的轨：那些是 `non_constant` / `low_confidence` 的轨，"
             "单个整数移位修不了它们（需要逐窗时变修正）。", ""]
    n_full = 0
    for f in files:
        name = f.name
        shutil.copy2(f, DST / name)
        row = rows.get(name) or {}
        aligned, off, total = track_state(row)
        shifts = row.get("shifts") or {}
        shutil_ = ", ".join(f"{k.replace('audio[', 'CH').replace(']', '')}"
                            f"{v:+d}" for k, v in shifts.items()) or "-"
        full = not off
        n_full += 1 if full else 0
        print(f"  {name}: 修正[{shutil_}] 已对齐{len(aligned)}轨 "
              f"未对齐{len(off)}轨 {off or ''}")
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"- 本次施加的整数移位：`{shutil_}`")
        lines.append(f"- 修后已对齐：{', '.join(aligned) or '无'}")
        lines.append(f"- **仍未对齐**：{', '.join(off) if off else '无 —— 全轨已对齐'}")
        if off:
            lines.append(f"- 仍留偏差合计：**{total:.2f} ms**")
        lines.append("")

    lines.insert(4, f"共 {len(files)} 件；其中 **{n_full} 件全轨已对齐**，"
                    f"{len(files) - n_full} 件仍有被拒的轨（明细见各节）。\n")
    (DST / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    total_size = sum((DST / f.name).stat().st_size for f in files)
    print(f"\n已复制 {len(files)} 件 / {total_size / 1024 ** 3:.2f} GiB -> {DST}")
    print(f"全轨已对齐: {n_full}/{len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
