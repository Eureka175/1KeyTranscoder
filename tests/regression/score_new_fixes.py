#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score every newly added fix against the project's own verify criteria.

`verify_lib.py` defines them precisely, and they are stricter than "the shift
was applied":

    content_exact          : exact.max_abs_diff == 0  (the samples are the source
                             shifted by the claimed amount)
    before_matches_shift   : |before.median - shift| <= 2
    after_is_zero          : |after.median| <= 1

A fix that fails the last two was shifted by a number that is not the offset of
the whole clip -- i.e. a rigid integer shift was the wrong instrument for that
track, even though the data written is bit-exact.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")

#: (variant file, [expected new tracks])
CASES = [
    ("V_frames3_arch.json", ["20260830_C0950.MP4"]),
    ("V_mad2_arch.json", ["20260830_C0913.MP4", "20260904_C1179.MP4"]),
    ("V_frames3_corpus.json", []),
    ("V_mad2_corpus.json", ["20260904_C1179.MP4"]),
    ("V_both_arch.json", ["20260830_C0913.MP4", "20260830_C0950.MP4",
                          "20260904_C1179.MP4"]),
]


def score(fname: str, targets: list[str]) -> list[dict]:
    p = OUT / fname
    if not p.is_file():
        print(f"{fname}: 缺失")
        return []
    rows = {r.get("name"): r for r in json.loads(p.read_text(encoding="utf-8"))}
    out = []
    for name in targets:
        r = rows.get(name)
        if not r:
            continue
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            v = (r.get("verify") or {}).get(str(c.get("stream"))) or {}
            bef = (v.get("before") or {}).get("median")
            aft = (v.get("after") or {}).get("median")
            sh = v.get("shift_samples")
            exact = bool((v.get("exact") or {}).get("exact"))
            before_ok = bef is not None and sh is not None and abs(bef - sh) <= 2
            after_ok = aft is not None and abs(aft) <= 1
            out.append({"variant": fname, "name": name,
                        "ch": (c.get("stream") or 0) + 1, "shift": sh,
                        "before": bef, "after": aft, "exact": exact,
                        "before_ok": before_ok, "after_ok": after_ok,
                        "pass": exact and before_ok and after_ok})
    return out


def main() -> int:
    all_rows = []
    for fname, targets in CASES:
        all_rows += score(fname, targets)
    seen = {}
    for r in all_rows:
        seen.setdefault((r["name"], r["ch"]), r)
    hdr = (f"{'variant':<22}{'track':<28}{'shift':>7}{'before':>8}"
           f"{'after':>8}{'exact':>7}{'bef_ok':>8}{'aft_ok':>8}{'pass':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in all_rows:
        print(f"{r['variant']:<22}"
              f"{r['name'] + ' CH' + str(r['ch']):<28}"
              f"{str(r['shift']):>7}{str(r['before']):>8}"
              f"{str(r['after']):>8}{str(r['exact']):>7}"
              f"{str(r['before_ok']):>8}{str(r['after_ok']):>8}"
              f"{str(r['pass']):>7}")
    print(f"\n去重后 {len(seen)} 条不同轨; 通过 "
          f"{sum(1 for r in seen.values() if r['pass'])} 条")
    for k, r in sorted(seen.items()):
        print(f"  {'PASS' if r['pass'] else 'FAIL'}  {k[0]} CH{k[1]} "
              f"shift={r['shift']} before={r['before']} after={r['after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
