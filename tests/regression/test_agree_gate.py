#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Three-way check of the estimate-agreement gate on synthetic audio.

The gate must:
  * pass when the claimed shift matches the real delay,
  * refuse when it is off by much more than the tolerance,
  * and pass a small disagreement (within tolerance) -- it is a coarse guard,
    not a second precision measurement.

Synthetic data keeps this a test of the instrument; the real-clip behaviour is
measured separately by `knob_experiments.py`.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.channel_sync import (  # noqa: E402
    _agree_with_independent_estimate,
    effective_opts,
)

SR = 48000
WORK = Path(r"E:\1kt_scratch\agree_test")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    ref = rng.standard_normal(SR * 8).astype(np.float32)
    ref_p = WORK / "ref.f32"
    ref.tofile(ref_p)

    eff = effective_opts({"verify_agree_samples": 2, "recheck_windows": 3})
    cases = [(1180, 1180, "完全一致"), (1180, 1120, "差 60 采样"),
             (1180, 1186, "差 6 采样"), (1180, 1181, "差 1 采样")]
    bad = 0
    for actual, claimed, label in cases:
        tgt = np.concatenate([np.zeros(actual), ref])[:len(ref)]
        tgt_p = WORK / f"tgt_{actual}_{claimed}.f32"
        tgt.astype(np.float32).tofile(tgt_p)
        est = types.SimpleNamespace(delay_ms=claimed * 1000.0 / SR)
        r = _agree_with_independent_estimate(
            1, est, claimed, {0: ref_p, 1: tgt_p}, 0, 0, SR, "f32", eff)
        if r is None:
            diff = 0.0
            verdict = "PASS"
        else:
            diff = r["diff_samples"]
            verdict = "REFUSED"
            bad += 1
        want = "PASS" if label in ("完全一致", "差 1 采样") else "REFUSED"
        ok = verdict == want
        bad -= 0 if ok else 0
        print(f"  实际 {actual} / 声称 {claimed} ({label}): {verdict} "
              f"diff={diff:.1f} 采样  期望 {want}  "
              f"{'OK' if ok else '*** 不符'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
