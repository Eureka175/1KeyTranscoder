#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seconds-long probes of `effective_agree_tolerance` -- no full suite needed.

Covers the three dimensions the tolerance can take (absolute floor, relative to
the offset, and in units of the independent estimator's own window spread) plus
the degenerate cases that must not silently change behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"F:\1KeyTranscoder")
from core.channel_sync import effective_agree_tolerance as T  # noqa: E402
from core.channel_sync import effective_opts as E  # noqa: E402

SR = 48000


def main() -> int:
    cases = [
        ("仅绝对 20 采样", {"verify_agree_samples": 20}, 1200,
         [24.5, 24.6, 24.4]),
        ("相对 2% × 24ms(=1152采样)", {"verify_agree_rel": 0.02}, 1152,
         [24.5, 24.6, 24.4]),
        ("相对 2% × 4.8ms(=230采样)", {"verify_agree_rel": 0.02}, 230,
         [4.8, 4.9, 4.7]),
        ("3σ (窗离散≈0.1ms)", {"verify_agree_sigma": 3.0}, 1200,
         [24.5, 24.4, 24.3]),
        ("全部关闭", {}, 1200, [24.5, 24.4]),
    ]
    for label, cfg, dly, wins in cases:
        r = T(E(cfg), delay_samples=dly, ind_samples=dly,
              window_values=wins, sample_rate=SR)
        print(f"  {label:<28} tol={r['tolerance_samples']:>8.2f} 采样  "
              f"basis={r['basis']:<9} sd={r['window_spread_ms']}")
    print()
    r = T(E({"verify_agree_sigma": 3.0}), delay_samples=100, ind_samples=100,
          window_values=[1.0], sample_rate=SR)
    print(f"  单窗 + sigma (sd 无法算)     -> tol={r['tolerance_samples']:.2f} "
          f"basis={r['basis']} sd={r['window_spread_ms']}")
    r = T(E({"verify_agree_samples": 20, "verify_agree_rel": 0.5}),
          delay_samples=100, ind_samples=100, window_values=[1.0, 1.0],
          sample_rate=SR)
    print(f"  相对项 0.5×100=50 > 绝对 20   -> tol={r['tolerance_samples']:.2f} "
          f"basis={r['basis']}  (应取最宽=相对)")
    r = T(E({}), delay_samples=0, ind_samples=0, window_values=[],
          sample_rate=SR)
    print(f"  全关 + delay=0              -> tol={r['tolerance_samples']:.2f} "
          f"basis={r['basis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
