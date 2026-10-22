#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the long-take probe outputs and print the decision numbers."""
from __future__ import annotations

import json
from pathlib import Path

B = Path(r"F:\_long_take_probe")


def L(name: str):
    p = B / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def main() -> int:
    print("=== S2e 上限分析（60% 门）===")
    o = L("s2e_oracle.json") or {}
    for clip, d in o.items():
        print(f"{clip}  来源数: {d.get('n_sources')}")
        for scale, per in d["targets"].items():
            for tgt, v in sorted(per.items()):
                print(f"  {scale:<10} CH{tgt}: 单一 {v['best_single_fraction']:.0%}"
                      f" | 逐窗换几何 {v['union_geometry_fraction']:.0%}"
                      f" | 全部换 {v['union_all_fraction']:.0%}"
                      f" -> {'PASS' if v['union_all_passes_60pct'] else 'FAIL'}")

    print("\n=== S0b 电平静音（60 s 块）===")
    for clip, d in (L("s0b_profile.json") or {}).items():
        for ch in d["channels"]:
            print(f"  {clip} CH{ch['ch']}: "
                  f"RMS p10/p50/p90={ch['rms_db_p10']:.1f}/"
                  f"{ch['rms_db_p50']:.1f}/{ch['rms_db_p90']:.1f} dBFS  "
                  f"静音 {100 * ch['silent_fraction']:.0f}%  "
                  f"最长 {ch['longest_silent_run_s']:.0f}s")

    print("\n=== S0c 分块相关性（120 s 块, 8 kHz NCC）===")
    for clip, d in (L("s0c_correlation.json") or {}).items():
        print(f"  {clip}: {d['blocks_used']} 块")
        for pr in d["pairs"]:
            print(f"    CH{pr['a']}-CH{pr['b']}: NCC中位 {pr['ncc_median']:.3f}"
                  f" | 相关 {pr['blocks_correlated']} 弱 {pr['blocks_weak']}"
                  f" 不相关 {pr['blocks_uncorrelated']}"
                  f" | lag {pr['lag_median']} 离散 {pr['lag_spread']}")

    print("\n=== S2 逐窗解析率（30/10 s，按几何）===")
    w = L("s2_windows.json") or {}
    for clip, rows in w.items():
        print(f"  {clip}")
        best = {}
        for r in rows:
            if r["window_s"] == 30.0 and r["hop_s"] == 10.0:
                key = (r["geometry"], r["target"])
                best[key] = r
        for (g, t), r in sorted(best.items()):
            print(f"    {g:<22} CH{t}: 解析 {r['n_windows_resolved']}/"
                  f"{r['n_windows_needed']} ({100 * r['resolved_fraction']:.1f}%)"
                  f" 缺口>0 {r['frames_per_window']['zero_windows']}"
                  f" 接缝>48 {r['seam_step_samples']['gt_48']}")

    print("\n=== S2f hold 缺口代价（30/10 s）===")
    for clip, rows in (L("s2f_gaps.json") or {}).items():
        print(f"  {clip}")
        for r in rows:
            g = r["hold_gap_cost"]
            print(f"    {r['geometry']:<22} CH{r['target']}: 解析 "
                  f"{r['resolved']}/{r['resolved'] + r['unresolved']}  "
                  f"缺口 {g['n_gaps']} 误差 p50/p90/max="
                  f"{g['err_p50']}/{g['err_p90']}/{g['err_max']}  "
                  f">1ms {g['gaps_gt_48_samples']}  "
                  f"窗内MAD p50 {r['window_mad_ms']['p50']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
