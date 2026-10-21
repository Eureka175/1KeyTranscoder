#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the agreement tolerance better expressed in samples, or as a confidence?

The gate compares the production estimate with an independent one and rejects the
track when they differ by more than a tolerance. A fixed sample count means a
different demand at every scale: 20 samples is 0.42 ms, which is 1.7% of a 24 ms
offset but 8% of a 5 ms one. The question is which formulation actually predicts
whether the resulting fix is correct.

This extracts, for every track the gate judged, the disagreement it measured
(the channel-sync log records it) together with the track's own statistics, and
joins it to the independent verifier's `after`. Then it bins by

    absolute   |delta| in samples
    relative   |delta| / |delay|
    confidence |delta| / (k * mad)  i.e. in units of the track's own scatter

and reports, per bin, how many fixes ended up aligned / within the displacement
bound / beyond it. The bin whose ordering best separates good from bad is the
right way to express the tolerance.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
PAT = re.compile(
    r"独立预检估计\s*([+-]?[\d.]+)ms\s*\(([+-]?[\d.]+)\s*采样\)\s*与\s*"
    r"shift\s*([+-]?\d+)\s*相差\s*([\d.]+)\s*采样")
ARMS = ["P_mat.json", "P_mat10.json", "P_mat20.json"]


def rows_of(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def collect() -> list[dict]:
    """Per track: gate measurement, track stats, and the verifier's outcome."""
    seen: dict[tuple, dict] = {}
    for name in ARMS:
        for r in rows_of(name):
            nm = r.get("name")
            est = {}
            for ln in r.get("logs") or []:
                m = PAT.search(ln)
                if m:
                    est[ln] = float(m.group(4))
            for c in r.get("channels") or []:
                ch = c.get("stream")
                key = (nm, ch)
                dly = c.get("delay_ms")
                mad = c.get("traj_mad_ms")
                rec = {
                    "name": nm, "ch": ch,
                    "decision": c.get("decision"),
                    "reason": c.get("reason"),
                    "delay_ms": dly, "mad_ms": mad,
                    "confidence": c.get("confidence"),
                    "frames": c.get("usable_frames"),
                }
                # 该轨的一致性差值: 从日志里找包含 CH 标记的那条
                delta = None
                for ln, dv in est.items():
                    if f"CH{ch + 1} " in ln and "独立预检" in ln:
                        delta = dv
                        break
                rec["delta"] = delta
                v = (r.get("verify") or {}).get(str(ch)) or {}
                rec["after"] = (v.get("after") or {}).get("median")
                mat = c.get("materiality") or {}
                disp = mat.get("displacement_ms")
                rec["disp_samples"] = (None if disp is None
                                       else float(disp) * SR / 1000.0)
                if rec["decision"] == "fixed" or delta is not None:
                    prev = seen.get(key)
                    if prev is None or (delta is not None
                                        and prev.get("delta") is None):
                        seen[key] = rec
    return list(seen.values())


def quality(rec: dict) -> str:
    aft = rec.get("after")
    if aft is None or aft != aft:
        return "unverifiable"
    if abs(aft) <= 1:
        return "aligned"
    disp = rec.get("disp_samples")
    if disp is not None and abs(aft) <= disp:
        return "within_disp"
    return "beyond"


def main() -> int:
    recs = [r for r in collect() if r.get("delta") is not None]
    print(f"具有一致性测量记录的轨: {len(recs)}\n")
    # 只有被修的轨能评价质量; 被拒的也要看差值分布, 但质量未知
    fixed = [r for r in recs if r["decision"] == "fixed" and r["after"] is not None
             and r["after"] == r["after"]]
    print(f"其中被修且可核验: {len(fixed)}\n")

    bins_abs = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 10 ** 9)]
    bins_rel = [(0, 0.005), (0.005, 0.01), (0.01, 0.02), (0.02, 0.05),
                (0.05, 1.0)]
    for label, bins, get in (
        ("按绝对差值(采样)", bins_abs, lambda r: abs(r["delta"])),
        ("按相对差值 |Δ|/|delay|", bins_rel,
         lambda r: abs(r["delta"]) / max(1e-9, abs(r["delay_ms"]) * SR / 1000.0)),
    ):
        print(f"=== {label}")
        print(f"{'区间':<18}{'条数':>6}{'aligned':>9}{'within_disp':>13}"
              f"{'beyond':>8}{'差评率':>8}")
        print("-" * 62)
        for lo, hi in bins:
            sel = [r for r in fixed if lo <= get(r) < hi]
            if not sel:
                continue
            q = [quality(r) for r in sel]
            bad = sum(1 for x in q if x == "beyond")
            name = (f"{lo:g}-{hi:g}" if hi < 10 ** 9 else f">={lo:g}")
            print(f"{name:<18}{len(sel):>6}{q.count('aligned'):>9}"
                  f"{q.count('within_disp'):>13}{bad:>8}"
                  f"{100 * bad / len(sel):>7.0f}%")
        print()

    # 用轨迹自身散布(mad)归一
    bins_mad = [(0, 1), (1, 3), (3, 10), (10, 10 ** 9)]
    print("=== 按 |Δ| / MAD (以轨迹自身散布为单位)")
    print(f"{'区间':<14}{'条数':>6}{'aligned':>9}{'within_disp':>13}"
          f"{'beyond':>8}{'差评率':>8}")
    print("-" * 58)
    for lo, hi in bins_mad:
        sel = []
        for r in fixed:
            mad_s = None
            if isinstance(r.get("mad_ms"), (int, float)):
                mad_s = abs(r["mad_ms"]) * SR / 1000.0
            if mad_s is None or mad_s <= 0:
                continue
            ratio = abs(r["delta"]) / mad_s
            if lo <= ratio < hi:
                sel.append(r)
        if not sel:
            continue
        q = [quality(r) for r in sel]
        bad = sum(1 for x in q if x == "beyond")
        name = (f"{lo:g}-{hi:g}" if hi < 10 ** 9 else f">={lo:g}")
        print(f"{name:<14}{len(sel):>6}{q.count('aligned'):>9}"
              f"{q.count('within_disp'):>13}{bad:>8}"
              f"{100 * bad / len(sel):>7.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
