#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which gate actually refuses the hard tracks -- the robust one or the outlier one?

`classify_constant` has three geometric gates:
  1. mad_ms <= mad_max_ms
  2. measured_displacement_ms (robust p5..p95) <= mad_max_ms
  3. full observed range <= mad_max_ms            (only when frames >= 20)

All three read the same stored fields (`traj_mad_ms`, `measured_displacement_ms`,
`traj_spread_samples`), so this can be answered offline for every refused track
in the archive -- no re-measurement and no opinion.

If a track passes gate 2 and fails gate 3, then the refusal was driven by the
*extremes* of the trajectory, not by the bulk of it. That distinction decides the
plan: gate-3 refusals can be fixed by a more robust decision (no audio editing),
while genuine gate-2 refusals need per-segment correction.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
ARM = OUT / "ARCH_on5.json"
SR = 48000
MIN_SAMPLES = 5
MAD_GATE_MS = 1.0


def main() -> int:
    arm = json.loads(ARM.read_text(encoding="utf-8"))
    rows = []
    for r in arm:
        for c in r.get("channels") or []:
            if c.get("decision") != "untouched":
                continue
            d = c.get("delay_ms")
            if not isinstance(d, (int, float)):
                continue
            if abs(d) < 1000.0 * MIN_SAMPLES / SR:
                continue
            rows.append({"name": r.get("name"), "stream": c.get("stream"),
                         "reason": c.get("reason"), "delay_ms": d,
                         "frames": c.get("usable_frames"),
                         "mad": c.get("traj_mad_ms"),
                         "disp": c.get("measured_displacement_ms"),
                         "spread_smp": c.get("traj_spread_samples")})
    print(f"归档中“被拒且偏差 ≥ {MIN_SAMPLES} 采样”的轨: {len(rows)} 条\n")

    buckets = {"gate1_mad": [], "gate2_disp": [], "gate3_range": [],
               "no_data": [], "other": []}
    for x in rows:
        mad = x["mad"] if isinstance(x["mad"], (int, float)) else None
        disp = x["disp"] if isinstance(x["disp"], (int, float)) else None
        fr = x["frames"] if isinstance(x["frames"], (int, float)) else 0
        if mad is None:
            buckets["no_data"].append(x)
        elif mad > MAD_GATE_MS:
            buckets["gate1_mad"].append(x)
        elif disp is not None and disp > MAD_GATE_MS:
            buckets["gate2_disp"].append(x)
        elif fr >= 20:
            # 前两门都过 -> 只可能是第三门(全范围)拦下的
            buckets["gate3_range"].append(x)
        else:
            buckets["other"].append(x)

    print(f"{'按最后一道未通过的门归类':<28}{'条数':>6}{'占比':>8}"
          f"{'偏差中位 ms':>12}{'MAD 中位':>10}{'稳健位移中位 ms':>16}")
    print("-" * 86)
    for k in ("gate1_mad", "gate2_disp", "gate3_range", "other", "no_data"):
        v = buckets[k]
        if not v:
            print(f"{k:<28}{0:>6}")
            continue
        dly = st.median([x["delay_ms"] for x in v])
        mad = st.median([x["mad"] for x in v if isinstance(x["mad"], (int, float))]
                        or [0])
        disp = [x["disp"] for x in v if isinstance(x["disp"], (int, float))]
        dpm = st.median(disp) if disp else float("nan")
        print(f"{k:<28}{len(v):>6}{100 * len(v) / len(rows):>7.0f}%"
              f"{dly:>12.2f}{mad:>10.3f}{dpm:>16.3f}")

    g3 = buckets["gate3_range"]
    if g3:
        sp = sorted(x["spread_smp"] for x in g3
                    if isinstance(x["spread_smp"], (int, float)))
        print(f"\ngate3 组: 全范围(样本) 中位 {st.median(sp):.1f}, "
              f"最小 {sp[0]:.0f}, 最大 {sp[-1]:.0f}")
        print("  这些轨的**稳健位移**通过了门, 是全范围里的离群帧把它们拦下的。")
    print(f"\n结论建议: gate3 组 {len(g3)} 条 ({100 * len(g3) / len(rows):.0f}%) "
          "属于“判据过于敏感”, 可用更稳健的判据救回;"
          f" gate2 组 {len(buckets['gate2_disp'])} 条才是真需要逐段修正的。")
    (OUT / "tv_gate_attribution.json").write_text(
        json.dumps({k: [{kk: vv for kk, vv in x.items()} for x in v]
                    for k, v in buckets.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
