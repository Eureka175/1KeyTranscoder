#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the C1179 case: a shift that looks right in the middle and is wrong
across the clip.

Production p05.7 applied shift=1174 to `20260904_C1179 CH1` and its single-window
recheck passed, but the whole-clip residual is 31.5 samples. This script builds
exactly that state (CH1 shifted by 1174 against CH3) and compares

    1 window  (旧行为, 只看中部锚段)
    k windows (新行为, 沿片长均匀取 k 段, 取最差)

so the value of the new knob is measured on the real failure rather than argued.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.sync_fix import (  # noqa: E402
    recheck_residual,
    recheck_residual_windows,
)

FFMPEG = REPO / "tools" / "ffmpeg.exe"
WORK = Path(r"E:\1kt_scratch\recheck_test")
SRC = Path(r"I:\20260904\20260904_C1179.MP4")
SHIFT = 1174           # 生产在该轨上施加的位移 (采样)
ANCHOR_S = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def extract(idx: int, out: Path) -> None:
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(SRC),
         "-map", f"0:a:{idx}", "-f", "f32le", "-ac", "1", "-ar", "48000",
         str(out)], check=True)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    ref_p, tgt_p = WORK / "ref.f32", WORK / "tgt_raw.f32"
    if not ref_p.is_file():
        extract(2, ref_p)       # CH3 = 锚点
    if not tgt_p.is_file():
        extract(0, tgt_p)       # CH1 = 被修的轨

    raw = np.memmap(tgt_p, dtype=np.float32, mode="r")
    # 施加生产用的整数移位: 目标相对参考"早" SHIFT 个样本, 修掉它就是把
    # 目标整体前移 SHIFT (尾部补零), 与 `shift_stream` 的做法一致。
    fixed = np.empty_like(np.asarray(raw))
    if SHIFT < len(raw):
        fixed[:-SHIFT] = raw[SHIFT:]
        fixed[-SHIFT:] = 0.0
    else:
        fixed[:] = 0.0
    fixed_p = WORK / f"tgt_fixed_{SHIFT}.f32"
    fixed.astype(np.float32).tofile(fixed_p)

    print(f"参考={ref_p.name} 目标={fixed_p.name} shift={SHIFT} "
          f"anchor={ANCHOR_S}s")
    one = recheck_residual(ref_p, fixed_p, sample_rate=48000,
                           storage_dtype="f32",
                           anchor_segment_seconds=ANCHOR_S)
    print(f"  1 窗 (旧行为)     : {one:+.3f} ms  "
          f"({one * 48:.1f} 采样)")
    for k in (3, 5, 9):
        r = recheck_residual_windows(
            ref_p, fixed_p, sample_rate=48000, storage_dtype="f32",
            windows=k, anchor_segment_seconds=ANCHOR_S)
        vals = ", ".join(f"{v:+.2f}" for v in r["values"])
        print(f"  {k} 窗 (取最差)     : {r['worst_ms']:+.3f} ms "
              f"({r['worst_ms'] * 48:.1f} 采样)  [{vals}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
