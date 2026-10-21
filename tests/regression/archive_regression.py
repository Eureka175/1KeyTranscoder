#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Archive regression: new work point vs the historical baseline.

Compares per tracked decision (not just fix counts), then labels each added fix
with the independent verifier so the result is "added N, of which M verified",
not "added N".
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
NEW = "R_noveto5_archive.json"
BASE = "ARCH_on5.json"


def load(n: str) -> list[dict]:
    return json.loads((OUT / n).read_text(encoding="utf-8"))


def decisions(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        for c in (r.get("channels") or []):
            out[(r.get("name"), c.get("stream"))] = (
                c.get("decision"), c.get("reason"), c.get("shift_samples"))
    return out


def grade(r: dict, c: dict) -> tuple[str, str]:
    v = (r.get("verify") or {}).get(str(c.get("stream"))) or {}
    before = (v.get("before") or {}).get("median")
    after = (v.get("after") or {}).get("median")
    exact = bool((v.get("exact") or {}).get("exact"))
    if after is None or after != after:
        return "不可核验", ""
    imp = (abs(before) / max(abs(after), 1.0)) if before is not None else 0.0
    ok = exact and (abs(after) <= 1.0 or imp >= 10.0)
    return ("良好" if ok else "不良"), f"after={after:.1f} 改善={imp:.0f}×"


def main() -> int:
    base, new = load(BASE), load(NEW)
    print(f"基线 {BASE}: {len(base)} 文件")
    print(f"新版 {NEW}: {len(new)} 文件")
    da, db = decisions(base), decisions(new)
    common = set(da) & set(db)
    changed = [k for k in common if da[k] != db[k]]
    fixed_a = {k for k, v in da.items() if v[0] == "fixed"}
    fixed_b = {k for k, v in db.items() if v[0] == "fixed"}
    added = sorted(fixed_b - fixed_a)
    lost = sorted(fixed_a - fixed_b)
    print(f"逐轨记录: 基线 {len(da)} / 新 {len(db)} / 交集 {len(common)}")
    print(f"判定变化 {len(changed)} 条; 修正: 基线 {len(fixed_a)} -> 新 {len(fixed_b)}")
    print(f"新增修正 {len(added)}, 失去 {len(lost)}")

    by_name = {(r.get("name")): r for r in new}
    buckets: dict[str, int] = {}
    print("\n新增修正的独立核验:")
    for k in added:
        r = by_name.get(k[0])
        c = next((c for c in (r.get("channels") or [])
                  if c.get("stream") == k[1]), None) if r else None
        tag, detail = grade(r, c) if (r and c) else ("未知", "")
        buckets[tag] = buckets.get(tag, 0) + 1
        if tag != "良好":
            print(f"  {k[0]} CH{(k[1] or 0) + 1} shift={db[k][2]} -> {tag} {detail}")
    print(f"  分档: {buckets}")
    if lost:
        print("\n失去的修正:")
        for k in lost:
            print(f"  {k[0]} CH{(k[1] or 0) + 1} 原 shift={da[k][2]}")

    # 只统计判定确实变了的轨, 避免把"reason 措辞变化"当成行为变化
    real = [k for k in changed
            if da[k][0] != db[k][0]]
    print(f"\n其中**决策本身**变化 {len(real)} 条 "
          f"(其余为 reason 措辞变化)")
    for k in real[:12]:
        print(f"  {k[0]} CH{(k[1] or 0) + 1}: {da[k]} -> {db[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
