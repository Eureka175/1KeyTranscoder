#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the remaining probe outputs: refsweep, knobs, enhanced, synthetic."""
from __future__ import annotations

import json
from pathlib import Path

B = Path(r"F:\_long_take_probe")


def L(name: str):
    p = B / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def main() -> int:
    print("=== S2c 参考扫描：每个目标的最佳参考（30/10 s 解析窗数）===")
    for clip, rows in (L("s2c_refsweep.json") or {}).items():
        print(f"  {clip}")
        by_tgt: dict[int, list[dict]] = {}
        for r in rows:
            by_tgt.setdefault(r["tgt"], []).append(r)
        for tgt, rs in sorted(by_tgt.items()):
            rs.sort(key=lambda x: -x["resolved_30_10"])
            top = rs[:3]
            print(f"    CH{tgt} 最佳参考: "
                  + " | ".join(f"CH{r['ref']} {r['resolved_30_10']}窗"
                               f"({100 * r['resolved_fraction_30_10']:.0f}%)"
                               for r in top))

    print("\n=== S2d 旋钮扫描（30/10 s 解析窗数, 按目标取最好变体）===")
    for clip, rows in (L("s2d_knobs.json") or {}).items():
        print(f"  {clip}")
        base = {}
        for r in rows:
            key = r["target"]
            cur = base.get(key)
            if cur is None or r["resolved_30_10"] > cur["resolved_30_10"]:
                base[key] = r
        for tgt, r in sorted(base.items()):
            print(f"    CH{tgt}: 最好变体 {r['variant']} "
                  f"{r['resolved_30_10']}窗 {r['overrides']} "
                  f"(可用帧 {r['usable_frames']})")

    print("\n=== S2b 逐窗增强（30/10 s, 最好的几何/目标组合）===")
    for clip, rows in (L("s2b_enhanced.json") or {}).items():
        best = sorted(rows, key=lambda r: -(r["scales"][0]["resolved_base"]
                                            if r["scales"] else 0))[:3]
        print(f"  {clip}")
        for r in best:
            sc = next((s for s in r["scales"] if s["window_s"] == 30.0), None)
            if not sc:
                continue
            ad = sc["adaptive_needed_window_s"]
            qr = sc["quiet_run_windows"]
            pw = r.get("piecewise") or {}
            print(f"    {r['geometry']:<20} CH{r['target']}: 基础 {sc['resolved_base']}"
                  f"/{sc['n_windows_needed']} | 滑窗最优 "
                  f"{sc['sliding_best_resolved']} ({100 * sc['sliding_best_fraction']:.0f}%)"
                  f" | 自适应窗长 p50/p90 {ad['p50']}/{ad['p90']}s"
                  f" | 覆盖 {100 * sc['audio_time_coverage']:.0f}%"
                  f" | 无证据段 p50/p90 {qr['p50_s']:.0f}/{qr['p90_s']:.0f}s"
                  f" | 分段 {pw.get('segments')} 残余p90 {pw.get('residual_p90')}")

    print("\n=== S3 合成校验 ===")
    s = L("s3_synthetic.json") or {}
    cases = [c for c in (s.get("cases") or [])
             if c.get("expected_samples") is not None]
    consts = [c for c in cases if not c["case"].startswith("漂移")]
    drift = [c for c in cases if c["case"].startswith("漂移")]
    if consts:
        errs = [abs(c["error_samples"]) for c in consts]
        print(f"  常数延迟: {len(consts)} 组, 最大误差 {max(errs):.2f} 采样")
    if drift:
        errs = [abs(c["error_samples"]) for c in drift]
        print(f"  漂移: {len(drift)} 组, 最大偏差 {max(errs):.1f} 采样 (注入漂移量)")
    dc = s.get("drift_window_check") or {}
    for key, label in (("control_constant_-1200", "对照 常数"),
                       ("drift_0_to_2400", "漂移 逐窗")):
        v = dc.get(key) or {}
        if v:
            print(f"  {label}: {v['n_windows']} 窗 误差 p50 {v['err_samples_p50']:.1f}"
                  f" p90 {v['err_samples_p90']:.1f} max {v['err_samples_max']:.1f} 采样")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
