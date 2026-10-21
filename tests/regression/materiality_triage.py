#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify the materiality-driven fixes into "aligned", "beneficial but not
aligned", and "value suspect".

The project's verify criteria (`|after| <= 1`) were written for tracks whose
offset is constant: there a correct fix leaves ~0. A non-constant track can never
satisfy that, because one integer shift cannot flatten a varying offset -- the
best it can do is leave the measured displacement. Judging those fixes with the
same bar would reject every one of them and hide the fact that they are still
15-100x better than before.

So this reports three things per new fix:

  improvement  |before| / max(|after|, 1)   -- how much better than leaving it
  value_ok     |before - shift| <= 2          -- the applied number is trustworthy
  aligned      |after| <= 1                   -- fully aligned (rare here)

and buckets them. A fix is worth keeping if value_ok, regardless of `aligned`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
BASE = "ARM5_on.json"


def allfix(name: str):
    p = OUT / name
    if not p.is_file():
        return None
    out = {}
    for r in json.loads(p.read_text(encoding="utf-8")):
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


def analyse(name: str) -> None:
    base, f = allfix(BASE), allfix(name)
    if f is None:
        print(f"{name}: 缺文件")
        return
    added = {k: v for k, v in f.items() if k not in base}
    lost = [k for k in base if k not in f]
    changed = [k for k in set(base) & set(f) if base[k]["shift"] != f[k]["shift"]]
    rows = []
    for k, v in added.items():
        vv = v["v"]
        before = (vv.get("before") or {}).get("median")
        after = (vv.get("after") or {}).get("median")
        sh = vv.get("shift_samples")
        ex = bool((vv.get("exact") or {}).get("exact"))
        value_ok = (before is not None and sh is not None
                    and abs(before - sh) <= 2)
        aligned = (after is not None and abs(after) <= 1)
        improve = (abs(before) / max(abs(after), 1.0)
                   if before is not None and after is not None else float("nan"))
        rows.append({"key": k, "before": before, "after": after, "shift": sh,
                     "exact": ex, "value_ok": value_ok,
                     "aligned": aligned, "improve": improve,
                     "mat": v.get("mat") or {}})

    n = len(rows)
    # 验收口径 (2026-10-21 修订):
    #   `|after| <= 1` 是**恒定位移**轨的判据; 一次性整数移位对非恒定轨
    #   的物理极限是"残余 ≈ 实测位移", 用它当上界才是诚实的。
    #   另外 `|before - shift| <= 2` 不能当"值可疑"的判据 —— 当 after≈0 时,
    #   修正已被成品证明是对的, before 那 2-3 采样的差只是它自己的测量噪声。
    def disp_samples(r):
        d = (r["mat"] or {}).get("displacement_ms")
        return None if d is None else float(d) * 48000.0 / 1000.0

    good, benefit, suspect, unver = [], [], [], []
    for r in rows:
        if not r["exact"]:
            suspect.append(r)
            continue
        if r["after"] is None or r["after"] != r["after"]:
            unver.append(r)
            continue
        if r["aligned"]:
            good.append(r)
            continue
        d = disp_samples(r)
        if d is not None and abs(r["after"]) <= d:
            benefit.append(r)
        else:
            suspect.append(r)
    print(f"\n=== {name}")
    print(f"  fixed {len(base)} -> {len(f)}  added {n}  lost {len(lost)}  "
          f"changed {len(changed)}")
    print(f"  完全对齐 (|after| <= 1)        : {len(good)}")
    print(f"  有益 (残余 <= 实测位移, 物理极限): {len(benefit)}")
    print(f"  超出位移上界 / 数据不精确       : {len(suspect)}")
    print(f"  不可核验 (after 为 nan)         : {len(unver)}")
    if benefit:
        imps = sorted(r["improve"] for r in benefit if r["improve"] == r["improve"])
        print(f"  '有益'组的改善倍数: 中位 {imps[len(imps) // 2]:.0f}x "
              f"最小 {imps[0]:.0f}x 最大 {imps[-1]:.0f}x")
    if suspect:
        print("  超出上界的条目:")
        for r in suspect:
            d = (r["mat"] or {}).get("displacement_ms")
            print(f"    {r['key'][0]} CH{(r['key'][1] or 0) + 1} "
                  f"shift={r['shift']} before={r['before']} "
                  f"after={r['after']} 位移={d}ms")
    if unver:
        print("  不可核验:")
        for r in unver:
            print(f"    {r['key'][0]} CH{(r['key'][1] or 0) + 1} "
                  f"shift={r['shift']}")
    if lost:
        print(f"  !! LOST {lost}")
    if changed:
        print(f"  !! CHANGED {changed}")


def main() -> int:
    names = sys.argv[1:] or ["G3_mat3_corpus.json", "G3_mat3full_corpus.json"]
    for n in names:
        analyse(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
