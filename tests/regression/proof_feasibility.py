#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feasibility answer for the 4ch-proof interview set, plus cost/speed.

Reports, for the short set (two arms: production defaults, and the stage-1 work
point):
  * wall clock and per-file process time, and the parallel speedup
  * per-track decisions
  * for every fixed track: the independent verification verdict, bucketed into
    fully aligned / beneficial (residual within the measured displacement) /
    beyond the displacement bound / unverifiable
  * what is still not aligned, with the reason and the offset
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
MIN_SAMPLES = 5


def load(name: str) -> list[dict]:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def tracks(rows: list[dict]):
    for r in rows:
        for c in r.get("channels") or []:
            yield r, c


def verdict(r: dict, c: dict) -> dict:
    v = (r.get("verify") or {}).get(str(c.get("stream"))) or {}
    before = (v.get("before") or {}).get("median")
    after = (v.get("after") or {}).get("median")
    sh = v.get("shift_samples")
    exact = bool((v.get("exact") or {}).get("exact"))
    mat = c.get("materiality") or {}
    disp_ms = mat.get("displacement_ms")
    disp_s = None if disp_ms is None else float(disp_ms) * SR / 1000.0
    return {"before": before, "after": after, "shift": sh, "exact": exact,
            "disp_samples": disp_s,
            "aligned": after is not None and abs(after) <= 1,
            "beneficial": (after is not None and after == after
                           and disp_s is not None and abs(after) <= disp_s),
            "unverifiable": after is None or after != after}


def main() -> int:
    arm = sys.argv[1] if len(sys.argv) > 1 else "mat"
    base_rows = load("P_base.json") if (OUT / "P_base.json").is_file() else []
    rows = load(f"P_{arm}.json")
    print(f"=== P_{arm}: {len(rows)} 文件")

    elapsed = [r.get("elapsed_s") for r in rows
               if isinstance(r.get("elapsed_s"), (int, float))]
    if elapsed:
        print(f"  单文件处理时间: 中位 {sorted(elapsed)[len(elapsed) // 2]:.1f}s  "
              f"合计 {sum(elapsed):.0f}s")

    kinds: dict[str, int] = {}
    fixed_aligned = fixed_benefit = fixed_beyond = fixed_unver = 0
    still_off = []
    for r, c in tracks(rows):
        d = c.get("decision")
        kinds[d] = kinds.get(d, 0) + 1
        if d != "fixed":
            dm = c.get("delay_ms")
            if (d == "untouched" and isinstance(dm, (int, float))
                    and abs(dm) >= 1000 * MIN_SAMPLES / SR):
                still_off.append((r.get("name"), c.get("stream"),
                                  c.get("reason"), dm))
            continue
        v = verdict(r, c)
        if v["unverifiable"]:
            fixed_unver += 1
        elif v["aligned"]:
            fixed_aligned += 1
        elif v["beneficial"]:
            fixed_benefit += 1
        else:
            fixed_beyond += 1

    print(f"  判定分布: {kinds}")
    print(f"  修正轨核验: 完全对齐 {fixed_aligned}, 有益(残余<=位移) "
          f"{fixed_benefit}, 超上界 {fixed_beyond}, 不可核验 {fixed_unver}")
    print(f"  仍被拒且偏移 >= {MIN_SAMPLES} 采样: {len(still_off)} 条")
    for nm, ch, reason, dm in sorted(still_off, key=lambda x: -abs(x[3])):
        print(f"    {nm} CH{(ch or 0) + 1} {dm:+.2f}ms ({reason})")

    if base_rows and arm != "base":
        kb: dict[str, int] = {}
        off_b = 0
        for r, c in tracks(base_rows):
            kb[c.get("decision")] = kb.get(c.get("decision"), 0) + 1
            dm = c.get("delay_ms")
            if (c.get("decision") == "untouched"
                    and isinstance(dm, (int, float))
                    and abs(dm) >= 1000 * MIN_SAMPLES / SR):
                off_b += 1
        print(f"\n  对照 P_base: 判定 {kb}, 仍偏移 >= 5 采样 {off_b} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
