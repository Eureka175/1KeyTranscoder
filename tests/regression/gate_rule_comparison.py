#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Should the agreement gate veto, or only report?

Framework: a gate exists to reject bad fixes without rejecting good ones. This
scores candidate rules against the independent verifier over the whole archive
(the record-only arm accepted everything, so every fixed track carries its own
disagreement and its own post-fix residual).

Ground truth for a fix being good:
    after measurable  and  |after| <= max(1 sample, the track's measured displacement)

Rules compared:
    none                     accept everything (the record-only arm)
    agreement veto, absolute / relative / sigma
    post-fix guard only      reject when |after| is unmeasurable or exceeds the
                             bound -- i.e. what production's recheck already does

For each rule: kept good, kept bad (false accept), rejected good (false reject).
A veto that only adds false rejects is not buying anything over the post-fix
guard.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000


def merged() -> list[dict]:
    rows: list[dict] = []
    for p in sorted(OUT.glob("P_matshape_arch_s*.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    # 访谈集那批也带上, 样本更多
    fp = OUT / "P_matshape.json"
    if fp.is_file():
        rows.extend(json.loads(fp.read_text(encoding="utf-8")))
    return rows


def dataset() -> list[dict]:
    recs = []
    for r in merged():
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            agr = c.get("agreement") or {}
            ch = c.get("stream")
            v = (r.get("verify") or {}).get(str(ch)) or {}
            aft = (v.get("after") or {}).get("median")
            mat = c.get("materiality") or {}
            disp = mat.get("displacement_ms")
            disp_s = (None if disp is None else float(disp) * SR / 1000.0)
            w = [x for x in (agr.get("windows_ms") or []) if x == x]
            sd_s = (st.pstdev(w) * SR / 1000.0) if len(w) >= 2 else None
            # 好 = after 可测 且 <= max(1, 位移)
            if aft is None or aft != aft:
                good = False
            else:
                bound = max(1.0, disp_s if disp_s is not None else 0.0)
                good = abs(aft) <= bound
            recs.append({
                "name": r.get("name"), "ch": ch,
                "delay_s": abs(c.get("delay_ms") or 0) * SR / 1000.0,
                "delta": agr.get("diff_samples"),
                "measured": bool(agr.get("measured")),
                "sd_s": sd_s,
                "after": aft, "good": good,
                "disp_s": disp_s, "exact": bool(
                    ((v.get("exact") or {}).get("exact"))),
            })
    return recs


def score(recs: list[dict], keep) -> dict:
    kept = [r for r in recs if keep(r)]
    good_kept = sum(1 for r in kept if r["good"])
    bad_kept = len(kept) - good_kept
    rej_good = sum(1 for r in recs if not keep(r) and r["good"])
    return {"kept": len(kept), "good_kept": good_kept,
            "bad_kept": bad_kept, "rej_good": rej_good,
            "total": len(recs)}


def main() -> int:
    recs = dataset()
    n_good = sum(1 for r in recs if r["good"])
    print(f"样本: {len(recs)} 条被修轨, 其中良好 {n_good}, "
          f"不良 {len(recs) - n_good}")
    meas = [r for r in recs if r["measured"] and r["delta"] is not None]
    print(f"带一致性测量: {len(meas)} 条")
    if not recs:
        return 0

    rules = [("不做否决(全放行)", lambda r: True),
             ("修后残差护栏 (after<=max(1,位移))", lambda r: r["good"])]
    for t in (5, 10, 20, 50):
        rules.append((f"一致性否决 绝对 {t} 采样",
                      lambda r, t=t: (not r["measured"]) or r["delta"] is None
                      or r["delta"] <= t))
    for rel in (0.02, 0.05, 0.10):
        rules.append((f"一致性否决 相对 {rel:g}×|delay|",
                      lambda r, rel=rel: (not r["measured"])
                      or r["delta"] is None or r["delay_s"] <= 0
                      or r["delta"] <= rel * r["delay_s"]))
    for k in (2.0, 3.0, 5.0):
        rules.append((f"一致性否决 {k:g}σ",
                      lambda r, k=k: (not r["measured"])
                      or r["delta"] is None or not r["sd_s"]
                      or r["delta"] <= k * r["sd_s"]))

    print(f"\n{'规则':<34}{'保留':>7}{'保留且好':>9}{'误收':>6}{'误拒':>6}")
    print("-" * 64)
    for label, fn in rules:
        s = score(recs, fn)
        print(f"{label:<34}{s['kept']:>7}{s['good_kept']:>9}"
              f"{s['bad_kept']:>6}{s['rej_good']:>6}")

    # 一致性否决额外拒掉了多少"其实良好"的轨
    print("\n=== 一致性否决相对'修后护栏'的增量")
    base = score(recs, lambda r: r["good"])
    for t in (5, 20):
        s = score(recs, lambda r, t=t: ((not r["measured"]) or r["delta"] is None
                                        or r["delta"] <= t))
        print(f"  绝对 {t}: 额外拒掉 {base['good_kept'] - s['good_kept']} 条"
              f"良好轨, 额外挡住 {s['bad_kept'] - base['bad_kept']} 条不良轨")

    bad = [r for r in recs if not r["good"]]
    print(f"\n=== 不良样本 {len(bad)} 条（用于判断护栏是否漏）")
    for r in sorted(bad, key=lambda x: -(abs(x["after"]) if x["after"] == x["after"]
                                        and x["after"] is not None else 9999))[:12]:
        d = r["delta"]
        print(f"  {r['name']} CH{(r['ch'] or 0) + 1} Δ={d if d is None else round(d, 1)} "
              f"after={r['after']} 位移={r['disp_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
