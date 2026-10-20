#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare each gate variant against its baseline, with verification status.

The arm JSON records, per file, the per-track decision plus a `verify` block for
every fixed track (exact / before_matches_shift / after_is_zero). A variant is
only adoptable if every added fix verifies and nothing is lost or changed --
counts alone are not enough.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")

PAIRS = [
    ("ARCH_on5.json", "V_frames3_arch.json", "archive  frames=3"),
    ("ARCH_on5.json", "V_mad2_arch.json", "archive  mad=2.0"),
    ("ARCH_on5.json", "V_both_arch.json", "archive  frames=3+mad=2.0"),
    ("ARM5_on.json", "V_frames3_corpus.json", "corpus   frames=3"),
    ("ARM5_on.json", "V_mad2_corpus.json", "corpus   mad=2.0"),
    ("ARM5_on.json", "V_both_corpus.json", "corpus   frames=3+mad=2.0"),
]


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
                "frames": c.get("usable_frames"),
                "v": (r.get("verify") or {}).get(str(ch)) or {},
            }
    return out


def vsum(v: dict) -> str:
    if not v:
        return "verify缺失"
    e = v.get("exact") or {}
    return (f"ok={v.get('ok')} exact={e.get('exact')} "
            f"maxdiff={e.get('max_abs_diff')} "
            f"after0={v.get('after_is_zero')} "
            f"before_match={v.get('before_matches_shift')}")


def main() -> int:
    for base_name, var_name, label in PAIRS:
        base, var = load(base_name), load(var_name)
        if base is None or var is None:
            print(f"\n=== {label}: 缺文件 ({base_name} / {var_name})")
            continue
        fb, fv = fixes(base), fixes(var)
        added = {k: v for k, v in fv.items() if k not in fb}
        lost = [k for k in fb if k not in fv]
        changed = [k for k in set(fb) & set(fv) if fb[k]["shift"] != fv[k]["shift"]]
        print(f"\n=== {label}")
        print(f"  files base={len(base)} var={len(var)}   "
              f"fixed base={len(fb)} var={len(fv)}")
        print(f"  added={len(added)}  lost={len(lost)}  changed={len(changed)}")
        for (name, ch), v in sorted(added.items()):
            print(f"    + {name} CH{(ch or 0) + 1} shift={v['shift']} "
                  f"delay={v['delay']} frames={v['frames']} | {vsum(v['v'])}")
        for k in lost:
            print(f"    - LOST {k[0]} CH{(k[1] or 0) + 1} shift={fb[k]['shift']}")
        for k in changed:
            print(f"    ~ CHANGED {k[0]} CH{(k[1] or 0) + 1} "
                  f"{fb[k]['shift']} -> {fv[k]['shift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
