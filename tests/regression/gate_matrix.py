#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate matrix over the **already measured** arms — no re-measurement.

Axes (every input is stored per track in the arm):
  A. `mad_max_ms`        constancy: 1.0 (baseline) / 2.0 / 3.0 / 4.0 ms
  B. `min_usable_frames` 5 (baseline) / 4 / 3 / 2
  C. `recheck_residual`  absolute 0.05 ms (baseline) / relative

Why this is exact for A and B:
  `classify_constant` requires `mad_ms <= mad_max_ms` and the observed full
  range <= mad_max_ms (frames >= 20). Both values are stored (`traj_mad_ms`,
  `traj_spread_samples`), so raising the threshold can be re-decided offline
  without guessing. The un-stored robust p5..p95 displacement is always <= the
  full range, so testing the full range is *conservative*: a cell that passes
  here would also pass in production.

C cannot be re-decided offline (the recheck residual is not stored). It is
reported as a separate candidate list, marked 需重测.

Metrics per cell — the point is the trade-off, not the count:
  fixed+   additional tracks that would be corrected
  recover  Σ|delay| of those tracks, i.e. misalignment removed (ms)
  accept   Σ spread of those tracks, i.e. non-constancy tolerated (ms)
  ratio    recover / accept (higher is better)
  risky    tracks whose spread >= delay (correcting them would trade a small
           constant offset for a variation as large as the offset)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ARMS = [
    (r"F:\1KeyTranscoder\work\regression_r087\out\ARCH_on5.json", "archive"),
    (r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_on.json", "corpus"),
    (r"F:\1KeyTranscoder\work\regression_r087\out\ARM5_holdout_on.json",
     "holdout"),
]

MIN_SHIFT = 5           # min_shift_samples (v0.8.7)
BASELINE_MAD = 1.0
BASELINE_MIN_FRAMES = 5
MAX_PPM = 5.0            # constant_max_ppm
DRIFT_MIN_MS = 0.1       # drift_min_ms
SR = 48000


def load_rows() -> list[dict]:
    """Load and de-duplicate: the archive arm and the corpus arm share files
    (the calibration corpus lives on E: but the same clips also exist on I:),
    so the same (file, stream) can appear twice."""
    seen = {}
    for p, tag in ARMS:
        path = Path(p)
        if not path.is_file():
            continue
        arm = json.loads(path.read_text(encoding="utf-8"))
        for r in arm:
            for c in r.get("channels") or []:
                key = (Path(r["file"]).name, c.get("stream"))
                if key in seen:
                    continue
                c["_arm"] = tag
                c["_file"] = Path(r["file"]).name
                seen[key] = c
    return list(seen.values())


#: 这些原因不是"策略门"调得动的: 无信号 / 无有效锚点 / 搜索窗边缘。
#: 把它们排除在翻转之外, 否则矩阵会把"根本没测出来"算成"门太严"。
NEVER = {"silent_track", "non_finite", "no_valid_anchor", "out_of_range"}


def decide(c: dict, mad: float, min_frames: int) -> str:
    """Re-decide one track under (mad, min_frames). Mirrors production order.

    `recheck_residual` 不在本函数内处理 —— 复检残差未入库, 只能重测,
    因此这里始终判 refused; 它作为 C 轴的候选清单单独列出。
    """
    dec = c.get("decision")
    if dec == "anchor":
        return "anchor"
    reason = c.get("reason")
    if reason in NEVER:
        return "refused"
    if reason == "recheck_residual":
        return "refused"
    d_ms = c.get("delay_ms")
    d_smp = c.get("delay_samples")
    if not isinstance(d_ms, (int, float)) or not isinstance(d_smp, (int, float)):
        return "refused"
    frames = c.get("usable_frames")
    frames = frames if isinstance(frames, (int, float)) else 0
    if frames < min_frames:                     # 帧数门 (A/B 轴)
        return "refused"
    m = c.get("traj_mad_ms")
    if not isinstance(m, (int, float)):
        return "refused"
    if m > mad:                                  # 恒定性: MAD 门
        return "refused"
    sp = c.get("traj_spread_samples")
    sp = sp if isinstance(sp, (int, float)) else float("inf")
    full_ms = sp * 1000.0 / SR
    if frames >= 20:
        if full_ms > mad:                        # 恒定性: 全范围门
            return "refused"
    else:
        # 帧数 <20 时 `classify_constant` **跳过**全范围门, 改看稳健位移
        # (p5..p95) 与漂移门 —— 这两个值都没入库, 无法离线精确重判。
        # 这类轨若被拒于 `non_constant`, 一律标为"需重测", 不计入离线翻转。
        if reason == "non_constant":
            return "needs_remeasure"
    # 漂移门: `|drift_ppm| > 5` 且实测稳健位移 > 0.1 ms 才判非恒定。
    # 稳健位移没入库; 用全范围作上界代理 (全范围 >= 稳健位移), 因此在
    # "全范围 > 0.1" 时按非恒定处理 —— 方向是**更保守**(拒绝得更多),
    # 不会把生产拒绝的轨误判成可修。
    drift = c.get("drift_ppm")
    if (isinstance(drift, (int, float)) and abs(drift) > MAX_PPM
            and full_ms > DRIFT_MIN_MS):
        return "refused"
    if abs(d_smp) <= MIN_SHIFT:
        return "aligned"
    return "fixed"


def cell(rows: list[dict], mad: float, min_frames: int) -> dict:
    fixed = 0
    rec = 0.0
    acc = 0.0
    risky = []
    for c in rows:
        if decide(c, mad, min_frames) != "fixed":
            continue
        if c.get("decision") == "fixed":
            continue                      # already fixed at baseline
        d = abs(float(c["delay_ms"]))
        sp = abs(float(c.get("traj_spread_samples") or 0)) * 1000.0 / SR
        fixed += 1
        rec += d
        acc += sp
        if sp >= d:
            risky.append(f"{c['_file']} CH{(c.get('stream') or 0) + 1} "
                         f"d={d:.2f}ms spread={sp:.2f}ms")
    return {"fixed": fixed, "recover_ms": rec, "accept_ms": acc,
            "ratio": (rec / acc) if acc > 0 else float("inf"),
            "risky": risky}


def main() -> int:
    rows = load_rows()
    base_fixed = sum(1 for c in rows if c.get("decision") == "fixed")
    print(f"arms={len(ARMS)}  track-rows={len(rows)}  "
          f"baseline fixed={base_fixed}\n")

    mads = [1.0, 2.0, 3.0, 4.0]
    frames = [5, 4, 3, 2]
    print("col = min_usable_frames, row = mad_max_ms;  单元格 = 新增修/回收ms/接受ms")
    label = "mad/frames"
    hdr = f"{label:<12}" + "".join(f"{f:>22}" for f in frames)
    print(hdr)
    print("-" * len(hdr))
    best = None
    for mad in mads:
        cells = []
        for f in frames:
            r = cell(rows, mad, f)
            cells.append(f"{r['fixed']:>3}/{r['recover_ms']:>6.1f}/{r['accept_ms']:>6.1f}")
            if r["fixed"] and (best is None or r["ratio"] > best[0]):
                best = (r["ratio"], mad, f, r)
        print(f"{mad:<12.1f}" + "".join(f"{c:>22}" for c in cells))

    print("\n基线 (mad=1.0, frames=5) 的新增量应为 0 —— 上面第一行第一列")
    base = cell(rows, BASELINE_MAD, BASELINE_MIN_FRAMES)
    print(f"  实测: 新增 {base['fixed']} 条")

    # 覆盖边界: 有多少轨被排除在离线重判之外
    nm = sum(1 for c in rows
             if decide(c, BASELINE_MAD, BASELINE_MIN_FRAMES)
             == "needs_remeasure")
    never = sum(1 for c in rows if c.get("reason") in NEVER)
    recheck = sum(1 for c in rows if c.get("reason") == "recheck_residual")
    print(f"\n覆盖边界: 需重测(帧数<20 的 non_constant) {nm} 条; "
          f"不可调门的拒绝 {never} 条; recheck_residual {recheck} 条")
    print("  这三类**不计入**上面的格子 —— 前两类离线无法精确重判, "
          "后一类残差未入库。")

    if best:
        ratio, mad, f, r = best
        print(f"\n单位收益最高的格子: mad_max_ms={mad} min_usable_frames={f}")
        print(f"  新增修正 {r['fixed']} 条, 回收 {r['recover_ms']:.1f} ms 偏差, "
              f"接受 {r['accept_ms']:.1f} ms 变化, 比值 {ratio:.1f}")
        for x in r["risky"][:8]:
            print(f"    RISKY {x}")

    # C: relative recheck — 这只是候选清单, 残差未存, 需重测
    print("\nC. recheck_residual 相对化候选 (残差未入库, **需重测确认**):")
    n = 0
    tot = 0.0
    for c in rows:
        if c.get("reason") != "recheck_residual":
            continue
        d = c.get("delay_ms")
        if not isinstance(d, (int, float)):
            continue
        n += 1
        tot += abs(d)
        print(f"  {c['_file']:<24}CH{(c.get('stream') or 0) + 1} "
              f"delay={d:+.2f}ms frames={c.get('usable_frames')} "
              f"conf={c.get('confidence')}")
    print(f"  合计 {n} 条, 若相对判据接受则可回收 {tot:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
