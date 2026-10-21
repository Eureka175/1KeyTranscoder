#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final numbers for the 4ch-proof interview set under the diagnostic-only gate.

Quality is labelled independently of any gate: a fixed track is good when the
written data is exact and the independently measured error dropped by at least
10x (or is already under one sample). Unmeasurable residuals are reported
separately -- they are not claimed as aligned.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
MIN_SAMPLES = 5
MIN_IMPROVE = 10.0


def load(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def main() -> int:
    arm = sys.argv[1] if len(sys.argv) > 1 else "noveto"
    rows = load(f"P_{arm}.json")
    if rows is None:
        print(f"P_{arm}.json 缺失")
        return 2
    files = len(rows)
    targets = aligned = 0
    good = unver = bad = 0
    off = []
    imps = []
    for r in rows:
        for c in r.get("channels") or []:
            if c.get("decision") == "anchor":
                continue
            targets += 1
            d = c.get("decision")
            if d == "already_aligned":
                aligned += 1
                continue
            if d != "fixed":
                dm = c.get("delay_ms")
                if (d == "untouched" and isinstance(dm, (int, float))
                        and abs(dm) >= 1000 * MIN_SAMPLES / SR):
                    off.append((r.get("name"), c.get("stream"), dm,
                                c.get("reason")))
                continue
            aligned += 1
            ch = c.get("stream")
            v = (r.get("verify") or {}).get(str(ch)) or {}
            before = (v.get("before") or {}).get("median")
            after = (v.get("after") or {}).get("median")
            exact = bool((v.get("exact") or {}).get("exact"))
            if after is None or after != after:
                unver += 1
                continue
            imp = abs(before) / max(abs(after), 1.0) if before is not None else 0.0
            imps.append(imp)
            ok = exact and (abs(after) <= 1.0 or imp >= MIN_IMPROVE)
            if ok:
                good += 1
            else:
                bad += 1

    print(f"=== P_{arm}: {files} 件 / {targets} 目标轨")
    print(f"  已对齐(生产口径): {aligned}/{targets} "
          f"({100 * aligned / max(1, targets):.0f}%)")
    fixes = good + bad + unver
    print(f"  其中被修 {fixes} 条: 独立核验良好 {good}, 不良 {bad}, "
          f"不可核验 {unver}")
    if imps:
        print(f"  改善倍数: 中位 {st.median(imps):.0f}×, "
              f"最小 {min(imps):.1f}×")
    print(f"  仍被拒且偏移 >= {MIN_SAMPLES} 采样: {len(off)} 条")
    reasons: dict[str, int] = {}
    for _n, _c, _d, rs in off:
        reasons[rs] = reasons.get(rs, 0) + 1
    print(f"    原因分布: {reasons}")
    for nm, ch, dm, rs in sorted(off, key=lambda x: -abs(x[2]))[:10]:
        print(f"      {nm} CH{(ch or 0) + 1} {dm:+.2f}ms ({rs})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
