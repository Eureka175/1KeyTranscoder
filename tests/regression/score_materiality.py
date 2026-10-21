#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score the materiality variants (stage 1) against the corpus baseline.

Acceptance is the project's own verify criteria, per new fix:
  content_exact / |before - shift| <= 2 / |after| <= 1
plus lost_fix == 0 and changed_value == 0. For materiality-driven fixes the row
also carries a `materiality` block (ratio of delay to measured displacement),
which is reported: it is the evidence that the fix was justified.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
BASE = "ARM5_on.json"
TARGETS = ["G3_mat3_corpus.json", "G3_mat3full_corpus.json"]


def allfix(name: str):
    p = OUT / name
    if not p.is_file():
        return None
    rows = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for r in rows:
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            out[(r.get("name") or Path(r["file"]).name, ch)] = {
                "shift": c.get("shift_samples"),
                "delay": c.get("delay_ms"),
                "mat": c.get("materiality"),
                "v": (r.get("verify") or {}).get(str(ch)) or {},
            }
    return out


def passed(v: dict) -> bool:
    bef = (v.get("before") or {}).get("median")
    aft = (v.get("after") or {}).get("median")
    sh = v.get("shift_samples")
    ex = bool((v.get("exact") or {}).get("exact"))
    return (ex and bef is not None and sh is not None and abs(bef - sh) <= 2
            and aft is not None and abs(aft) <= 1)


def main() -> int:
    base = allfix(BASE)
    if base is None:
        print(f"{BASE} 缺失")
        return 2
    print(f"基线 {BASE}: {len(base)} 条修正")
    for name in TARGETS:
        f = allfix(name)
        if f is None:
            print(f"\n=== {name}: 缺文件")
            continue
        added = {k: v for k, v in f.items() if k not in base}
        lost = {k: v for k, v in base.items() if k not in f}
        changed = [k for k in set(base) & set(f)
                   if base[k]["shift"] != f[k]["shift"]]
        good = sum(1 for v in added.values() if passed(v["v"]))
        print(f"\n=== {name}")
        print(f"  fixed={len(f)}  added={len(added)} (达标 {good})  "
              f"lost={len(lost)}  changed={len(changed)}")
        for (nm, ch), v in sorted(added.items()):
            vv = v["v"]
            mat = v.get("mat") or {}
            ratio = mat.get("ratio")
            tag = "PASS" if passed(vv) else "FAIL"
            extra = f"  材料性 {ratio:.1f}x (位移 {mat.get('displacement_ms'):.2f}ms)" if ratio else ""
            print(f"  + {nm} CH{(ch or 0) + 1} shift={vv.get('shift_samples')} "
                  f"before={(vv.get('before') or {}).get('median')} "
                  f"after={(vv.get('after') or {}).get('median')} | {tag}{extra}")
        for (nm, ch), v in sorted(lost.items()):
            print(f"  - LOST {nm} CH{(ch or 0) + 1} shift={v['shift']}")
        for k in changed:
            print(f"  ~ CHANGED {k[0]} CH{(k[1] or 0) + 1} "
                  f"{base[k]['shift']} -> {f[k]['shift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
