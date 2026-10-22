#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate the per-track reference phase on the proof set.

Two checks:
  1 default-path neutrality: R_defchk_proof vs the earlier R_base2_proof (option
    off must reproduce every decision);
  2 effect: R_trackref_proof vs the previous work point P_noveto (same options
    minus track_reference) -- how many clips become fully aligned, which tracks
    were recovered, and whether any previous fix was lost or changed.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
MIN_SAMPLES = 5
SKIP = {"20260904_C1180", "20260904_C1202", "20260903_C1162",
        "20260903_C1164", "20260903_C1166", "20260903_C1170"}


def load(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def decisions(rows):
    out = {}
    for r in rows:
        for c in r.get("channels") or []:
            out[(r.get("name"), c.get("stream"))] = (
                c.get("decision"), c.get("reason"), c.get("shift_samples"))
    return out


def per_file(rows):
    res = {}
    for r in rows:
        name = r.get("name")
        targets = [c for c in (r.get("channels") or [])
                   if c.get("decision") != "anchor"]
        ok = all(c.get("decision") in ("fixed", "already_aligned")
                 for c in targets)
        res[name] = ok
    return res


def main() -> int:
    defchk, base2 = load("R_defchk2_proof.json"), load("R_base2_proof.json")
    if defchk and base2:
        a, b = decisions(base2), decisions(defchk)
        diff = [k for k in set(a) & set(b) if a[k] != b[k]]
        print(f"[默认路径中性] 旧 {len(a)} 轨 / 新 {len(b)} 轨 / 交集差异 "
              f"{len(diff)} 条")
        for k in diff[:8]:
            print("   ", k[0], f"CH{(k[1] or 0) + 1}", a[k], "->", b[k])

    new, old = load("R_trackref2_proof.json"), load("P_noveto.json")
    if not (new and old):
        print("缺对比数据")
        return 1
    da, db = decisions(old), decisions(new)
    fixed_a = {k for k, v in da.items() if v[0] == "fixed"}
    fixed_b = {k for k, v in db.items() if v[0] == "fixed"}
    added = sorted(fixed_b - fixed_a)
    lost = sorted(fixed_a - fixed_b)
    changed = [k for k in set(da) & set(db)
               if da[k][2] != db[k][2]]
    print(f"\n[效果] 修正 {len(fixed_a)} -> {len(fixed_b)}; "
          f"新增 {len(added)}, 失去 {len(lost)}, 值变化 {len(changed)}")
    for k in added:
        print(f"  + {k[0]} CH{(k[1] or 0) + 1} shift={db[k][2]}")
    for k in lost:
        print(f"  - LOST {k[0]} CH{(k[1] or 0) + 1} shift={da[k][2]}")

    fa, fb = per_file(old), per_file(new)
    print(f"\n[全通文件] {sum(fa.values())} -> {sum(fb.values())} / {len(fb)}")
    for name, ok in sorted(fb.items()):
        if ok and not fa.get(name):
            print(f"  新全通: {name}")
    print("\n[仍未全通]")
    for name, ok in sorted(fb.items()):
        if not ok:
            skipped = " (废/缺数据, 排除)" if name in SKIP else ""
            print(f"  {name}{skipped}")

    # 逐轨恢复证据
    print("\n[逐轨参考选择记录]")
    for r in new:
        for c in r.get("channels") or []:
            note = c.get("reference_choice")
            if note:
                print(f"  {r.get('name')} CH{(c.get('stream') or 0) + 1}: "
                      f"参考 CH{note.get('reference')} "
                      f"{note.get('delay_vs_reference_ms'):+.2f}ms + "
                      f"参考移位 {note.get('reference_shift_samples')} = "
                      f"{note.get('composed_ms'):+.2f}ms  "
                      f"({note.get('usable_frames')} 帧, "
                      f"恒定={note.get('constant')}, 轮 {note.get('round')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
