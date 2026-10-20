#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score the two new recheck knobs against the baseline, using the project's own
verification criteria (content_exact / before_matches_shift / after_is_zero).

The two knobs are supposed to move the decision set in opposite directions, so
the report separates "gained" from "lost" and scores both:

  verify_max_rel   is expected to GAIN fixes on stationary large offsets; a gain
                   is only acceptable if it passes the same three criteria.
  recheck_windows  is expected to LOSE fixes whose offset is not constant; a
                   loss is a success if the lost fix failed the criteria (the
                   old gate was letting a bad fix through) and a regression if
                   it passed (the new gate is now too strict).
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
BASE = "ARM5_on.json"
VARIANTS = ["G2_rel_corpus.json", "G2_win3_corpus.json", "G2_both_corpus.json"]


def load(name: str) -> list[dict] | None:
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def fixes(rows: list[dict]) -> dict[tuple[str, int], dict]:
    out = {}
    for r in rows:
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
        return False, "verify缺失"
    bef = (v.get("before") or {}).get("median")
    aft = (v.get("after") or {}).get("median")
    sh = v.get("shift_samples")
    exact = bool((v.get("exact") or {}).get("exact"))
    bok = bef is not None and sh is not None and abs(bef - sh) <= 2
    aok = aft is not None and abs(aft) <= 1
    return (exact and bok and aok,
            f"shift={sh} before={bef} after={aft} exact={exact} "
            f"before_ok={bok} after_ok={aok}")


def main() -> int:
    base = load(BASE)
    if base is None:
        print(f"{BASE} 缺失")
        return 2
    fb = fixes(base)
    print(f"基线 {BASE}: {len(base)} 文件, {len(fb)} 条修正")

    for name in VARIANTS:
        rows = load(name)
        if rows is None:
            print(f"\n=== {name}: 未完成/缺失")
            continue
        fv = fixes(rows)
        added = {k: v for k, v in fv.items() if k not in fb}
        lost = {k: v for k, v in fb.items() if k not in fv}
        changed = [k for k in set(fb) & set(fv) if fb[k]["shift"] != fv[k]["shift"]]
        print(f"\n=== {name}: files={len(rows)} fixed={len(fv)}")
        print(f"  added={len(added)} lost={len(lost)} changed={len(changed)}")
        for (nm, ch), v in sorted(added.items()):
            ok, why = score(v["v"])
            print(f"    + {nm} CH{(ch or 0) + 1} delay={v['delay']} | "
                  f"{'PASS' if ok else 'FAIL'} {why}")
        for (nm, ch), v in sorted(lost.items()):
            ok, why = score(v["v"])
            verdict = ("GOOD LOSS (旧门放过了坏修正)" if not ok
                       else "!! REGRESSION (旧门本来是对的)")
            print(f"    - {nm} CH{(ch or 0) + 1} delay={v['delay']} | "
                  f"{verdict} | {why}")
        for k in changed:
            print(f"    ~ CHANGED {k[0]} CH{(k[1] or 0) + 1} "
                  f"{fb[k]['shift']} -> {fv[k]['shift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
