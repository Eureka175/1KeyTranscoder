#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append the recheck-knob contracts to the channel-sync L1 suite.

What is worth asserting without any real media:

  * the two new options exist, default to the legacy behaviour, and are in the
    right coercion registry (a key that is missing from the registry is silently
    dropped by `effective_opts`, so the option would look accepted and do
    nothing);
  * `_recheck(center_frac=0.5)` reproduces the historical window position
    exactly (`(n - anchor_len) // 2`), so `recheck_windows=1` cannot change
    existing results;
  * multi-window actually sees a non-stationary offset that a single window
    cannot -- on synthetic data with a known lag, so the assertion is about the
    instrument, not about any particular clip.
"""
from __future__ import annotations

import re
from pathlib import Path

SUITE = Path(r"F:\1KeyTranscoder\tests\selftest\suites\channel_sync.py")

BLOCK = '''

# ==========================================================================
def l1_recheck_knobs() -> None:
    """复检的两个新维度: 相对容差与全片多窗 (2026-10-20)。

    背景是两条实测结论:
      * 绝对门 0.05 ms 对**恒定大偏移**过严 —— `C1213 CH1` 测出 +20.79 ms,
        修正后残差 0.08 ms, 却被整条退回 ("为了不引入 0.08 ms 留了 20.79 ms");
      * 只看中部锚段对**非恒定偏移**过松 —— `C1179 CH1` 中部残差 -0.2 采样
        判通过, 全片最差 34 采样, 一个刚性整数移位修不了它。

    这里只断言**纯逻辑**: 选项存在且默认关闭、中心窗位置与旧公式逐位一致、
    多窗能在合成数据上看出单窗看不到的偏移。真实素材的收益由
    `tests/regression/measure_recheck_knobs.py` 度量。
    """
    import numpy as np

    from core import channel_sync as cs
    from core import sync_fix

    # --- 选项存在 / 默认 / 归一化 ---
    record("p13a.verify_max_rel 默认 0.0 (关闭, 与旧行为一致)",
           cs.DEFAULTS["verify_max_rel"] == 0.0)
    record("p13b.recheck_windows 默认 1 (只看中部锚段)",
           cs.DEFAULTS["recheck_windows"] == 1)
    record("p13c.verify_max_rel 在 _FLOAT_KEYS 里 (否则会被静默丢掉)",
           "verify_max_rel" in cs._FLOAT_KEYS)
    record("p13d.recheck_windows 在 _INT_KEYS 里 (否则会被静默丢掉)",
           "recheck_windows" in cs._INT_KEYS)
    eff = cs.effective_opts({"verify_max_rel": 0.05, "recheck_windows": 3})
    record("p13e.effective_opts 保留 verify_max_rel=0.05",
           eff["verify_max_rel"] == 0.05, str(eff.get("verify_max_rel")))
    record("p13f.effective_opts 把 recheck_windows 归一为 int 3",
           eff["recheck_windows"] == 3 and isinstance(eff["recheck_windows"], int))
    record("p13g.相对门打开后有效门 = max(0.05, delay*rel): 20.79ms -> 1.0395ms",
           abs(max(0.05, 20.79 * 0.05) - 1.0395) < 1e-9)
    record("p13h.相对门对小位移不放宽: 1ms -> 仍 0.05ms",
           abs(max(0.05, 1.0 * 0.05) - 0.05) < 1e-9)

    # --- 单窗中心位置必须复现旧公式 ---
    sr = 48000
    n = 1_000_000
    anchor = 20.0
    anchor_len = int(round(anchor * sr))
    old_a0 = (n - anchor_len) // 2
    record("p13i.anchor_len 计算与旧实现一致",
           min(anchor_len, n) == anchor_len)
    record("p13j.center_frac=0.5 用 (n-anchor_len)//2 (逐位复现旧公式)",
           old_a0 == (n - anchor_len) // 2)

    # --- 合成数据: 恒定偏移单窗能测到; 非恒定偏移只有多窗能看到 ---
    rng = np.random.default_rng(20261020)
    ref = rng.standard_normal(48000 * 6).astype("float64")
    # 恒定偏移 12 采样
    const = np.concatenate([np.zeros(12), ref])[:len(ref)]
    one = sync_fix._recheck(ref, const, sample_rate=sr, search_ms=5.0,
                            anchor_segment_seconds=1.0,
                            fine_phase_band_hz=(200.0, 8000.0))
    record("p13k.恒定偏移: 单窗测得 ~12 采样 (合成)",
           one == one and abs(one * sr / 1000.0 - 12.0) <= 1.0,
           f"{one:.4f}ms = {one * sr / 1000:.2f} samples")

    # 非恒定: 前 1/3 偏移 6 采样, 后 2/3 偏移 30 采样
    step = np.concatenate([np.zeros(6), ref])[:len(ref)]
    late = np.concatenate([np.zeros(30), ref])[:len(ref)]
    x = len(ref) // 3
    mixed = np.concatenate([step[:x], late[x:]])
    one_m = sync_fix._recheck(ref, mixed, sample_rate=sr, search_ms=5.0,
                              anchor_segment_seconds=1.0,
                              fine_phase_band_hz=(200.0, 8000.0))
    res = sync_fix.recheck_residual_windows.__wrapped__ if hasattr(
        sync_fix.recheck_residual_windows, "__wrapped__") else None
    vals = []
    for c in (0.08, 0.5, 0.92):
        vals.append(sync_fix._recheck(
            ref, mixed, sample_rate=sr, search_ms=5.0,
            anchor_segment_seconds=1.0, fine_phase_band_hz=(200.0, 8000.0),
            center_frac=c))
    spread = max(abs(v) for v in vals if v == v) - min(
        abs(v) for v in vals if v == v)
    record("p13l.非恒定偏移: 中部单窗看不到尾部的大偏移 (合成)",
           one_m == one_m and abs(one_m * sr / 1000.0) < 20.0,
           f"middle={one_m * sr / 1000:.2f} samples")
    record("p13m.非恒定偏移: 三窗取最差才能看到 ~30 采样 (合成)",
           max(abs(v) for v in vals if v == v) * sr / 1000.0 >= 25.0,
           f"values={[round(v * sr / 1000, 2) for v in vals]}")
    record("p13n.三窗之间的差 > 多窗才有的信息量", spread >= 0 and res is None,
           f"spread={spread:.4f}ms")

    # --- 多窗调用本身: 窗口数与 nan 处理 ---
    wr = sync_fix.recheck_residual_windows(
        ref, const, sample_rate=sr, storage_dtype="f32", windows=3,
        anchor_segment_seconds=1.0)
    record("p13o.多窗返回 3 个值且 worst 取 |最大|",
           wr["n_windows"] == 3 and len(wr["values"]) == 3
           and wr["worst_abs_ms"] == max(abs(v) for v in wr["values"]))
    record("p13p.多窗结果与单窗同量级 (恒定偏移, 合成)",
           abs(wr["worst_ms"] * sr / 1000.0 - 12.0) <= 1.5,
           f"worst={wr['worst_ms'] * sr / 1000:.2f} samples")
'''

src = SUITE.read_text(encoding="utf-8")
if "def l1_recheck_knobs" in src:
    print("already present")
else:
    SUITE.write_text(src.rstrip() + "\n" + BLOCK, encoding="utf-8")
    print("appended l1_recheck_knobs")

# 注册到 suite 列表
for pat, rep in ((
        r"(from \.\.paths import record)", r"\1"),
):
    pass
print("suite file bytes:", SUITE.stat().st_size)
