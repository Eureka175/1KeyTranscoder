#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full offline matrix + Pareto frontier + remaining-error accounting.

Complements `gate_charts.py` (which draws) by producing the numbers the
conclusion rests on:

  * adopted rate and **remaining uncorrected error** (Σ|delay| over tracks left
    unaligned) as a function of each threshold — "usable rate" in physical
    units, not just counts;
  * a 3-axis grid (min_usable_frames × mad_max_ms × min_shift_samples) with the
    Pareto set of (recovered ms, accepted ms);
  * the conditional structure that explains *why* the thresholds behave as they
    do (which gate binds for which subpopulation).

All of it is re-decided from the stored measurement fields by
`gate_charts.decide`, whose parity with production is asserted at start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import (  # noqa: E402
    CUR,
    RECHECK,
    SR,
    baseline_delta,
    decide,
    load,
    num,
    sweep,
)

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")


def main() -> int:
    rows = load()
    delta = baseline_delta(rows)
    meas = [c for c in rows if num(c.get("delay_samples")) is not None]
    adopted0 = sum(1 for c in meas
                   if c.get("decision") in ("fixed", "already_aligned"))
    refused = [c for c in meas if c.get("decision") not in
               ("fixed", "already_aligned", "anchor")]
    err0 = sum(abs(num(c.get("delay_ms"), 0.0)) for c in refused)
    print(f"parity={delta}  measurable={len(meas)}  adopted={adopted0} "
          f"({100 * adopted0 / len(meas):.1f}%)  refused={len(refused)}")
    print(f"基线余留未对齐总量 Σ|delay| = {err0:.1f} ms")

    # ---- 1. usable-rate & remaining-error curves -------------------------
    def curve(param: str, values: list[float]) -> list[dict]:
        out = []
        for s in sweep(rows, param, values):
            kw = {"frames_thr": CUR["frames"], "conf_thr": CUR["conf"],
                  "mad": CUR["mad"], "shift_thr": CUR["shift"]}
            if param == "frames":
                kw["frames_thr"] = int(s["v"])
            elif param == "conf":
                kw["conf_thr"] = s["v"]
            elif param == "mad":
                kw["mad"] = s["v"]
            elif param == "shift":
                kw["shift_thr"] = int(s["v"])
            # 余留误差: 未被修、且偏差超过 5 采样的轨
            remain = 0.0
            n_off = 0
            for c in meas:
                d = decide(c, **kw)
                if d in ("fixed", "aligned", "anchor"):
                    continue
                if abs(num(c.get("delay_samples"), 0.0)) <= CUR["shift"]:
                    continue
                v = abs(num(c.get("delay_ms"), 0.0))
                remain += v
                n_off += 1
            out.append({"v": s["v"], "new": s["new_fixed"],
                        "adopted": adopted0 + s["new_fixed"],
                        "rate_pct": 100.0 * (adopted0 + s["new_fixed"])
                        / len(meas),
                        "recover": s["recover_ms"], "accept": s["accept_ms"],
                        "remain_ms": remain, "n_off": n_off})
        return out

    print("\n=== 可用率与余留误差曲线 ===")
    for param, vals, unit in (
            ("frames", list(range(1, 13)), "min_usable_frames"),
            ("mad", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0], "mad_max_ms"),
            ("conf", [0.0, 0.3, 0.5, 0.7, 0.8], "min_confidence"),
            ("shift", [0, 1, 2, 3, 5], "min_shift_samples")):
        print(f"\n{unit}")
        print(f"  {'值':>6}{'新增':>6}{'采纳':>6}{'可用率%':>9}"
              f"{'回收ms':>9}{'余留ms':>9}{'余留轨':>7}")
        for r in curve(param, vals):
            print(f"  {r['v']:>6g}{r['new']:>6}{r['adopted']:>6}"
                  f"{r['rate_pct']:>9.1f}{r['recover']:>9.1f}"
                  f"{r['remain_ms']:>9.1f}{r['n_off']:>7}")

    # ---- 2. 3-axis grid + Pareto ----------------------------------------
    print("\n=== 三维格点 (frames × mad × shift) 的帕累托前沿 ===")
    pts = []
    for fr in (2, 3, 5):
        for mad in (1.0, 2.0, 3.0):
            for sh in (3, 5):
                kw = {"frames_thr": fr, "conf_thr": CUR["conf"], "mad": mad,
                      "shift_thr": sh}
                new = rec = acc = 0.0
                new = 0
                for c in meas:
                    d = decide(c, **kw)
                    if d == "fixed" and c.get("decision") != "fixed":
                        new += 1
                        rec += abs(num(c.get("delay_ms"), 0.0))
                        acc += c["_spread_ms"]
                pts.append({"frames": fr, "mad": mad, "shift": sh,
                            "new": new, "recover": rec, "accept": acc})
    pts.sort(key=lambda p: -p["recover"])
    frontier = []
    best_accept = float("inf")
    for p in pts:
        if p["accept"] < best_accept - 1e-9:
            frontier.append(p)
            best_accept = p["accept"]
    print(f"  {'frames':>7}{'mad':>6}{'shift':>6}{'新增':>6}{'回收ms':>9}"
          f"{'接受ms':>9}{'比值':>8}  帕累托")
    for p in pts:
        mark = "★" if any(p is f for f in frontier) else ""
        ratio = p["recover"] / p["accept"] if p["accept"] else float("inf")
        print(f"  {p['frames']:>7}{p['mad']:>6.1f}{p['shift']:>6}{p['new']:>6}"
              f"{p['recover']:>9.1f}{p['accept']:>9.1f}{ratio:>8.1f}  {mark}")

    # ---- 3. conditional structure --------------------------------------
    print("\n=== 为什么: 按 gates 的条件结构 ===")
    def share(pred, base):
        xs = [c for c in base if pred(c)]
        return len(xs)
    ge20 = [c for c in meas if num(c.get("usable_frames"), 0) >= 20]
    lt20 = [c for c in meas if num(c.get("usable_frames"), 0) < 20]
    lt5 = [c for c in meas if num(c.get("usable_frames"), 0) < 5]
    print(f"  帧数>=20: {len(ge20)} 条, 其中被拒 "
          f"{share(lambda c: c.get('decision') == 'untouched', ge20)}")
    print(f"  帧数 <20: {len(lt20)} 条, 其中被拒 "
          f"{share(lambda c: c.get('decision') == 'untouched', lt20)}")
    print(f"  帧数 <5 : {len(lt5)} 条, 其中被拒 "
          f"{share(lambda c: c.get('decision') == 'untouched', lt5)}")
    # 帧数<20 的被拒轨: MAD 与全范围都很小 -> 不是"不恒定", 是"样本不足"
    lt20_ref = [c for c in lt20 if c.get("decision") == "untouched"
                and num(c.get("delay_samples")) is not None]
    if lt20_ref:
        mads = [num(c.get("traj_mad_ms"), 0.0) for c in lt20_ref]
        sps = [c["_spread_ms"] for c in lt20_ref]
        mads.sort()
        sps.sort()
        med = lambda xs: xs[len(xs) // 2]
        print(f"  其中可测的 {len(lt20_ref)} 条: MAD 中位 {med(mads):.3f} ms "
              f"(最大 {max(mads):.2f}), 全范围中位 {med(sps):.3f} ms "
              f"(最大 {max(sps):.2f})")
        print("  -> 帧数少的那批不是'不恒定', 是'样本不足'; MAD 门对它们几乎不设限")
    for pct in (50, 80, 90):
        k = max(1, len(meas) * pct // 100)
        vals = sorted(abs(num(c.get("delay_ms"), 0.0)) for c in refused)
        if vals:
            print(f"  被拒轨 |delay| 的 p{pct} = "
                  f"{vals[min(len(vals) - 1, k * len(vals) // len(meas))]:.2f} ms")

    (OUT / "gate-matrix.json").write_text(json.dumps(
        {"parity_delta": delta, "measurable": len(meas),
         "adopted": adopted0, "refused": len(refused),
         "baseline_remaining_ms": err0,
         "curves": {p: curve(p, v) for p, v in (
             ("frames", list(range(1, 13))),
             ("mad", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]),
             ("conf", [0.0, 0.3, 0.5, 0.7, 0.8]),
             ("shift", [0, 1, 2, 3, 5]))},
         "grid": pts, "pareto": frontier},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {OUT / 'gate-matrix.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
