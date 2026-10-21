#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Effect of relaxing the estimate-agreement tolerance (5 / 10 / 20 samples).

Counts are not enough: each new fix is scored against the independent verifier
(`before`, `shift`, `after`) and bucketed into

    aligned     |after| <= 1                      -- fully aligned
    beneficial  |after| <= measured displacement   -- within a single shift's
                                                    physical limit
    beyond      |after| >  displacement           -- worse than the data allows
    unverifiable after is nan

The interesting column is what tolerances 10 and 20 *add* on top of 5, and
whether those additions verify.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
ARMS = [("P_mat.json", "agree5"), ("P_mat10.json", "agree10"),
        ("P_mat20.json", "agree20")]


def load(name: str):
    p = OUT / name
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def fixes(rows: list[dict]) -> dict[tuple[str, int], dict]:
    out = {}
    for r in rows:
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            v = (r.get("verify") or {}).get(str(ch)) or {}
            mat = c.get("materiality") or {}
            out[(r.get("name"), ch)] = {
                "before": (v.get("before") or {}).get("median"),
                "after": (v.get("after") or {}).get("median"),
                "shift": v.get("shift_samples"),
                "exact": bool((v.get("exact") or {}).get("exact")),
                "disp_ms": mat.get("displacement_ms"),
                "delay": c.get("delay_ms"),
            }
    return out


def bucket(v: dict) -> str:
    after = v["after"]
    if after is None or after != after:
        return "unverifiable"
    if abs(after) <= 1:
        return "aligned"
    disp = v["disp_ms"]
    if disp is None:
        return "beyond"          # 非材料性放行: 用严格口径
    return "beneficial" if abs(after) <= float(disp) * SR / 1000.0 else "beyond"


def main() -> int:
    data = {}
    for name, label in ARMS:
        rows = load(name)
        if rows is None:
            print(f"{label}: 未跑")
            continue
        data[label] = fixes(rows)

    labels = list(data)
    print(f"{'口径':<10}{'修正':>6}{'完全对齐':>9}{'有益':>7}{'超上界':>8}"
          f"{'不可核验':>9}")
    print("-" * 52)
    for label in labels:
        f = data[label]
        cnt = {"aligned": 0, "beneficial": 0, "beyond": 0, "unverifiable": 0}
        for v in f.values():
            cnt[bucket(v)] += 1
        print(f"{label:<10}{len(f):>6}{cnt['aligned']:>9}"
              f"{cnt['beneficial']:>7}{cnt['beyond']:>8}"
              f"{cnt['unverifiable']:>9}")

    # 与 agree5 相比, 10/20 新增了哪些
    if "agree5" in data:
        base = data["agree5"]
        for label in labels:
            if label == "agree5":
                continue
            new = {k: v for k, v in data[label].items() if k not in base}
            lost = [k for k in base if k not in data[label]]
            print(f"\n=== {label} 相对 agree5: 新增 {len(new)} 条, "
                  f"失去 {len(lost)} 条")
            for k, v in sorted(new.items()):
                print(f"  + {k[0]} CH{(k[1] or 0) + 1} "
                      f"shift={v['shift']} before={v['before']} "
                      f"after={v['after']} 位移={v['disp_ms']} "
                      f"-> {bucket(v)}")
            for k in lost:
                print(f"  - LOST {k[0]} CH{(k[1] or 0) + 1}")

    # 最差残余
    for label in labels:
        worst = sorted((v for v in data[label].values()
                        if v["after"] == v["after"] and v["after"] is not None),
                       key=lambda v: -abs(v["after"]))[:5]
        print(f"\n{label} 残余最大的 5 条: "
              + ", ".join(f"{abs(v['after']):.1f}" for v in worst)
              + " 采样")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
