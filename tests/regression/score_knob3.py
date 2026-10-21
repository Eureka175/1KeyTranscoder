#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score the G3 knob experiments against the corpus baseline.

`ctl` is the control: new code, default options. It must reproduce the
historical 13 fixes exactly, otherwise nothing else in this table is meaningful.

Then each candidate is scored with the project's own verify criteria
(content_exact / before_matches_shift / after_is_zero):
  * an ADDED fix is acceptable only if it passes all three;
  * a LOST fix is a regression if it passed all three under the baseline.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
BASE = "ARM5_on.json"
ORDER = ["G3_ctl_corpus.json", "G3_rel20_corpus.json",
         "G3_rel20win3_corpus.json", "G3_agree2_corpus.json",
         "G3_full_corpus.json", "G3_agree5_corpus.json",
         "G3_rel20agree5_corpus.json"]


def allfix(name: str) -> dict[tuple[str, int], dict] | None:
    p = OUT / name
    if not p.is_file():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for r in d:
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            out[(r.get("name") or Path(r["file"]).name, ch)] = {
                "shift": c.get("shift_samples"),
                "delay": c.get("delay_ms"),
                "v": (r.get("verify") or {}).get(str(ch)) or {},
            }
    return out


def score(v: dict) -> tuple[bool, str]:
    if not v:
        return False, "verify 缺失"
    bef = (v.get("before") or {}).get("median")
    aft = (v.get("after") or {}).get("median")
    sh = v.get("shift_samples")
    ex = bool((v.get("exact") or {}).get("exact"))
    bok = bef is not None and sh is not None and abs(bef - sh) <= 2
    aok = aft is not None and abs(aft) <= 1
    return (ex and bok and aok,
            f"shift={sh} before={bef} after={aft} exact={ex} "
            f"before_ok={bok} after_ok={aok}")


def main() -> int:
    base = allfix(BASE)
    if base is None:
        print(f"{BASE} 缺失")
        return 2
    print(f"基线 {BASE}: {len(base)} 条修正")
    for name in ORDER:
        f = allfix(name)
        if f is None:
            print(f"\n=== {name}: 未跑")
            continue
        added = {k: v for k, v in f.items() if k not in base}
        lost = {k: v for k, v in base.items() if k not in f}
        changed = [k for k in set(base) & set(f)
                   if base[k]["shift"] != f[k]["shift"]]
        ok_add = sum(1 for v in added.values() if score(v["v"])[0])
        bad_loss = sum(1 for v in lost.values() if score(v["v"])[0])
        print(f"\n=== {name}")
        print(f"  fixed={len(f)}  added={len(added)} "
              f"(达标 {ok_add})  lost={len(lost)} "
              f"(其中原本达标={bad_loss})  changed={len(changed)}")
        for (nm, ch), v in sorted(added.items()):
            ok, why = score(v["v"])
            print(f"    + {nm} CH{(ch or 0) + 1} delay={v['delay']} | "
                  f"{'PASS' if ok else 'FAIL'} {why}")
        for (nm, ch), v in sorted(lost.items()):
            ok, why = score(v["v"])
            tag = "!! REGRESSION" if ok else "good loss"
            print(f"    - {nm} CH{(ch or 0) + 1} | {tag} | {why}")
        for k in changed:
            print(f"    ~ {k[0]} CH{(k[1] or 0) + 1} "
                  f"{base[k]['shift']} -> {f[k]['shift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
