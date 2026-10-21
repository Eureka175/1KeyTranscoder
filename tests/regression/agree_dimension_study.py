#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which dimension should the agreement tolerance use?

One arm was run with the gate recording but not rejecting, so every fixed track
carries its own disagreement `Δ` (samples), the relative figure Δ/|delay|, the
independent estimator's window spread, and the verifier's `after`.

For a candidate tolerance rule this is a binary classifier: "keep the fix if the
disagreement is within tolerance". The ground truth is the verifier: a kept fix is
**good** when `after` is measurable and within the track's own measured
displacement (a single integer shift cannot do better), **bad** when it exceeds
that or cannot be measured.

The rule that best separates good from bad is the one to implement. Absolute
samples, relative to the offset, and in units of the estimator's own spread are
compared side by side.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
ARM = "P_matshape.json"
SR = 48000


def good(v: dict) -> bool | None:
    aft = v.get("after")
    if aft is None or aft != aft:
        return None
    if abs(aft) <= 1:
        return True
    disp = v.get("disp_samples")
    return disp is not None and abs(aft) <= disp


def load() -> list[dict]:
    rows = json.loads((OUT / ARM).read_text(encoding="utf-8"))
    out = []
    for r in rows:
        for c in r.get("channels") or []:
            if c.get("decision") != "fixed":
                continue
            ch = c.get("stream")
            agr = c.get("agreement") or {}
            if not agr.get("measured"):
                continue
            v = (r.get("verify") or {}).get(str(ch)) or {}
            aft = (v.get("after") or {}).get("median")
            mat = c.get("materiality") or {}
            disp = mat.get("displacement_ms")
            out.append({
                "name": r.get("name"), "ch": ch,
                "delta": agr.get("diff_samples"),
                "ratio": agr.get("diff_ratio"),
                "delay_ms": c.get("delay_ms"),
                "mad_ms": c.get("traj_mad_ms"),
                "spread_ms": agr.get("window_spread_ms"),
                "confidence": c.get("confidence"),
                "frames": c.get("usable_frames"),
                "after": aft,
                "disp_samples": (None if disp is None
                                 else float(disp) * SR / 1000.0),
                "basis": agr.get("tolerance_basis"),
                "tol": agr.get("tolerance_samples"),
            })
    return out


def table(recs: list[dict], key, bins, label: str) -> None:
    print(f"\n=== 按 {label} 分箱（只有 Δ 可测的 {len(recs)} 条）")
    print(f"{'区间':<16}{'条数':>6}{'好':>5}{'差':>5}{'不可核验':>9}"
          f"{'好评率':>8}")
    print("-" * 52)
    for lo, hi in bins:
        sel = [r for r in recs if lo <= key(r) < hi]
        if not sel:
            continue
        g = [good(r) for r in sel]
        ok = sum(1 for x in g if x is True)
        bad = sum(1 for x in g if x is False)
        unk = sum(1 for x in g if x is None)
        tot = ok + bad
        rate = f"{100 * ok / tot:.0f}%" if tot else "—"
        name = f"{lo:g}-{hi:g}" if hi < 10 ** 9 else f">={lo:g}"
        print(f"{name:<16}{len(sel):>6}{ok:>5}{bad:>5}{unk:>9}{rate:>8}")


def main() -> int:
    recs = load()
    print(f"{ARM}: 被修且带一致性测量的轨 {len(recs)} 条")
    if not recs:
        print("无数据")
        return 0
    deltas = sorted(r["delta"] for r in recs)
    print(f"Δ 分布: 中位 {deltas[len(deltas) // 2]:.1f} 采样, "
          f"最小 {deltas[0]:.1f}, 最大 {deltas[-1]:.1f}")

    table(recs, lambda r: r["delta"],
          [(0, 5), (5, 10), (10, 20), (20, 40), (40, 100), (100, 10 ** 9)],
          "绝对差值 Δ (采样)")
    table(recs, lambda r: (r["ratio"] if r["ratio"] is not None else 9.9),
          [(0, .005), (.005, .01), (.01, .02), (.02, .05), (.05, .1),
           (.1, 1.0)],
          "相对差值 Δ/|delay|")

    def sigma_key(r):
        sp = r.get("spread_ms")
        if not sp or sp != sp or sp <= 0:
            return 0.0
        return abs(r["delta"]) / (float(sp) * SR / 1000.0)

    table(recs, sigma_key,
          [(0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 10 ** 9)],
          "以独立估计的窗间标准差为单位 Δ/σ")

    # 逐条列出最差样本, 供人工判断
    print("\n=== 明细（Δ 从大到小前 12 条）")
    print(f"{'file':<22}{'ch':<4}{'Δ':>7}{'Δ/|delay|':>11}{'Δ/σ':>7}"
          f"{'delay':>8}{'after':>8}{'位移':>8}{'质量':>6}")
    print("-" * 82)
    for r in sorted(recs, key=lambda x: -x["delta"])[:12]:
        g = good(r)
        tag = "好" if g is True else ("差" if g is False else "不可核验")
        sp = r.get("spread_ms")
        sig = ((abs(r["delta"]) / (float(sp) * SR / 1000.0))
               if sp and sp == sp and sp > 0 else float("nan"))
        aft = r["after"]
        print(f"{r['name']:<22}CH{(r['ch'] or 0) + 1:<3}{r['delta']:>7.1f}"
              f"{(r['ratio'] or 0):>11.4f}{sig:>7.2f}"
              f"{(r['delay_ms'] or 0):>8.2f}"
              f"{(aft if aft is not None else float('nan')):>8.1f}"
              f"{(r['disp_samples'] if r['disp_samples'] is not None else -1):>8.1f}"
              f"{tag:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
