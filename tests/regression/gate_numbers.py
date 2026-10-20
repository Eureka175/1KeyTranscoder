#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authoritative numbers for the gate-analysis document.

Denominator matters and was wrong once: 737 "measurable" rows include 321
**anchor** rows, whose `usable_frames`/`traj_mad_ms` are null and whose
`confidence` is 1.0 by construction. Anchors are never gated, so they must not
be in the rate denominator. The gateable population is 416, not 737.

Everything printed here is quoted verbatim in
`docs/audio-sync-p4/gate-analysis.md`; if this script disagrees with the
document, the document is stale.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import (  # noqa: E402
    CUR,
    NEVER,
    RECHECK,
    baseline_delta,
    decide,
    load,
    num,
    sweep,
)

SR = 48000
MIN_SAMPLES = 5


def main() -> int:
    rows = load()
    assert baseline_delta(rows) == 0, "replica no longer matches production"
    anchor = [c for c in rows if c.get("decision") == "anchor"]
    hasdelay = [c for c in rows if num(c.get("delay_samples")) is not None]
    gate = [c for c in hasdelay if c.get("decision") != "anchor"]
    adopted = [c for c in gate
               if c.get("decision") in ("fixed", "already_aligned")]
    refused = [c for c in gate if c.get("decision") == "untouched"]
    off = [c for c in refused
           if abs(num(c.get("delay_ms"), 0.0)) >= 1000 * MIN_SAMPLES / SR]

    print(f"总行 {len(rows)} = anchor {len(anchor)} + adopted {len(adopted)}"
          f" + refused {len(refused)} + 不可测 "
          f"{len(rows) - len(anchor) - len(adopted) - len(refused)}")
    print(f"可门控 {len(gate)}  已采纳 {len(adopted)} "
          f"({100 * len(adopted) / len(gate):.1f}%)  被拒 {len(refused)}")
    print(f"被拒且 |delay|>={MIN_SAMPLES} 采样 {len(off)} 条, "
          f"Σ={sum(abs(num(c.get('delay_ms'), 0.0)) for c in off):.1f} ms")

    def rate(n: int) -> str:
        return f"{100 * n / len(gate):.1f}%"

    def adopted_under(param: str, v: float) -> int:
        """直接统计采纳数, 不用"基线 + 新增"。

        `min_shift_samples` 会把已经采纳的 `already_aligned` 重分类成
        `fixed`, 用加法就会重复计数（曾算出 102.2% 的可用率）。
        """
        kw = {"frames_thr": CUR["frames"], "conf_thr": CUR["conf"],
              "mad": CUR["mad"], "shift_thr": CUR["shift"]}
        if param == "frames":
            kw["frames_thr"] = int(v)
        elif param == "conf":
            kw["conf_thr"] = v
        elif param == "mad":
            kw["mad"] = v
        elif param == "shift":
            kw["shift_thr"] = int(v)
        return sum(1 for c in gate
                   if decide(c, **kw) in ("fixed", "aligned"))

    for param, vals in (("frames", list(range(1, 13))),
                        ("mad", [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]),
                        ("conf", [0.0, 0.3, 0.5, 0.7, 0.8]),
                        ("shift", [0, 1, 2, 3, 5])):
        print(f"\n--- {param}")
        print(f"{'值':>6}{'新增':>6}{'采纳':>6}{'可用率':>9}{'回收ms':>9}"
              f"{'接受ms':>9}")
        for s in sweep(rows, param, vals):
            ad = adopted_under(param, s["v"])
            print(f"{s['v']:>6g}{s['new_fixed']:>6}{ad:>6}{rate(ad):>9}"
                  f"{s['recover_ms']:>9.1f}{s['accept_ms']:>9.2f}")

    # 分区细节（子 agent 的读数, 在此复核）
    print("\n--- 分区细节")
    print(f"anchor 行: usable_frames/mad 为 None 的条数 "
          f"{sum(1 for c in anchor if c.get('usable_frames') is None)}"
          f"/{len(anchor)}")
    conf = [num(c.get("confidence"), 0.0) for c in gate]
    print(f"gateable 置信度: min {min(conf):.3f} max {max(conf):.3f} "
          f"中位 {st.median(conf):.3f}; <0.3 的条数 "
          f"{sum(1 for v in conf if v < 0.3)}")
    mad = [num(c.get("traj_mad_ms"), 0.0) for c in gate]
    print(f"gateable MAD: >1.0 的条数 {sum(1 for v in mad if v > 1.0)}; "
          f"=0 的条数 {sum(1 for v in mad if v == 0.0)}; "
          f"中位 {st.median(mad):.4f}")
    fr = [num(c.get("usable_frames"), 0) for c in gate]
    print(f"gateable 帧数: 恰为 5 的条数 {sum(1 for v in fr if v == 5)}; "
          f"<5 的条数 {sum(1 for v in fr if v < 5)} "
          f"(其中被拒 {sum(1 for c in gate if num(c.get('usable_frames'), 0) < 5 and c.get('decision') == 'untouched')})")
    ge = [c for c in gate if num(c.get("usable_frames"), 0) >= 20]
    lt = [c for c in gate if num(c.get("usable_frames"), 0) < 20]
    for tag, xs in (("帧数>=20", ge), ("帧数<20", lt)):
        sp = sorted(c["_spread_ms"] for c in xs)
        over = sum(1 for v in sp if v > 1.0)
        print(f"{tag}: n={len(xs)} 中位 {st.median(sp):.3f} "
              f"p90 {sp[int(len(sp) * 0.9)]:.3f} >1.0 的 {over} 条 "
              f"({100 * over / len(sp):.1f}%) max {max(sp):.2f}")
    print(f"low_confidence 行: {sum(1 for c in gate if c.get('reason') == 'low_confidence')} 条, "
          f"其中帧数<5 的 "
          f"{sum(1 for c in gate if c.get('reason') == 'low_confidence' and num(c.get('usable_frames'), 0) < 5)} 条")
    print(f"不可调门拒绝: {sum(1 for c in rows if c.get('reason') in NEVER)}; "
          f"recheck: {sum(1 for c in rows if c.get('reason') == RECHECK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
