#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate comparison with an **independent** quality label.

The previous version defined "good" as "the post-fix bound holds", which makes the
post-fix rule tautologically perfect. This one labels quality by a quantity the
rules do not use:

    improvement = |before| / max(|after|, 1)

`before` is the independent pre-fix measurement and `after` the independent
post-fix one, both from the verifier. A fix is **good** when the data written is
exact AND the error dropped by at least 10x (or is already below one sample), and
**bad** when it is not exact, unmeasurable, or improved by less than 10x.

Then the candidate gates are scored on what they are for: how many bad fixes they
keep, and how many good fixes they throw away. Records are de-duplicated by
(clip, channel) because the archive arm and the proof-set arm overlap.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
MIN_IMPROVE = 10.0


def merged() -> list[dict]:
    rows: list[dict] = []
    for p in sorted(OUT.glob("P_matshape_arch_s*.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    fp = OUT / "P_matshape.json"
    if fp.is_file():
        rows.extend(json.loads(fp.read_text(encoding="utf-8")))
    return rows


def dataset() -> list[dict]:
    seen: dict[tuple, dict] = {}
    for r in merged():
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            key = (r.get("name"), ch)
            agr = c.get("agreement") or {}
            v = (r.get("verify") or {}).get(str(ch)) or {}
            before = (v.get("before") or {}).get("median")
            after = (v.get("after") or {}).get("median")
            exact = bool((v.get("exact") or {}).get("exact"))
            mat = c.get("materiality") or {}
            disp = mat.get("displacement_ms")
            w = [x for x in (agr.get("windows_ms") or []) if x == x]
            sd_s = (st.pstdev(w) * SR / 1000.0) if len(w) >= 2 else None
            # 独立标签
            if after is None or after != after:
                good = False
                improve = float("nan")
            else:
                improve = (abs(before) / max(abs(after), 1.0)
                           if before is not None else float("nan"))
                good = exact and (abs(after) <= 1.0
                                  or (improve == improve
                                      and improve >= MIN_IMPROVE))
            seen[key] = {
                "name": r.get("name"), "ch": ch, "good": good,
                "exact": exact, "before": before, "after": after,
                "improve": improve,
                "delay_s": abs(c.get("delay_ms") or 0) * SR / 1000.0,
                "delta": agr.get("diff_samples"),
                "measured": bool(agr.get("measured")),
                "sd_s": sd_s,
                "disp_s": (None if disp is None
                           else float(disp) * SR / 1000.0),
            }
    return list(seen.values())


def score(recs: list[dict], keep) -> dict:
    kept = [r for r in recs if keep(r)]
    good = sum(1 for r in kept if r["good"])
    return {"kept": len(kept), "good": good, "bad": len(kept) - good,
            "rej_good": sum(1 for r in recs if not keep(r) and r["good"])}


def main() -> int:
    recs = dataset()
    ng = sum(1 for r in recs if r["good"])
    imps = sorted(r["improve"] for r in recs if r["improve"] == r["improve"])
    print(f"去重后样本: {len(recs)} 条被修轨; 良好 {ng}, 不良 {len(recs) - ng}")
    if imps:
        print(f"改善倍数: 中位 {st.median(imps):.0f}×, "
              f"最小 {imps[0]:.1f}×, 最大 {imps[-1]:.0f}×")
    bad = [r for r in recs if not r["good"]]
    print(f"不良明细: "
          + "; ".join(f"{r['name']} CH{(r['ch'] or 0) + 1}"
                      f"(after={r['after']}, 改善={r['improve']:.1f}×)"
                      if r["improve"] == r["improve"] else
                      f"{r['name']} CH{(r['ch'] or 0) + 1}(after=nan)"
                      for r in bad[:8]))

    rules = [("不做否决(全放行)", lambda r: True)]
    for t in (5, 10, 20, 50):
        rules.append((f"否决 绝对 {t} 采样",
                      lambda r, t=t: (not r["measured"]) or r["delta"] is None
                      or r["delta"] <= t))
    for rel in (0.02, 0.05, 0.10):
        rules.append((f"否决 相对 {rel:g}×|delay|",
                      lambda r, rel=rel: (not r["measured"])
                      or r["delta"] is None or r["delay_s"] <= 0
                      or r["delta"] <= rel * r["delay_s"]))
    for k in (2.0, 3.0, 5.0):
        rules.append((f"否决 {k:g}σ",
                      lambda r, k=k: (not r["measured"])
                      or r["delta"] is None or not r["sd_s"]
                      or r["delta"] <= k * r["sd_s"]))

    print(f"\n{'规则':<26}{'保留':>6}{'保留且好':>9}{'误收':>6}{'误拒':>6}")
    print("-" * 56)
    for label, fn in rules:
        s = score(recs, fn)
        print(f"{label:<26}{s['kept']:>6}{s['good']:>9}{s['bad']:>6}"
              f"{s['rej_good']:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
