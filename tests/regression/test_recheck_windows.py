#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity-test the two new recheck knobs on a real clip.

Uses C1179 (the clip whose whole-clip residual the single-window check missed):
takes two of its own tracks as reference/target, so the returned residual is a
real measurement of a real pair, not a synthetic number.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.sync_fix import (  # noqa: E402
    recheck_residual,
    recheck_residual_windows,
)

FFMPEG = REPO / "tools" / "ffmpeg.exe"
WORK = Path(r"E:\1kt_scratch\recheck_test")
SRC = Path(r"I:\20260904\20260904_C1179.MP4")


def extract(idx: int, out: Path) -> None:
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(SRC),
         "-map", f"0:a:{idx}", "-f", "f32le", "-ac", "1", "-ar", "48000",
         str(out)], check=True)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    a, b = WORK / "a.f32", WORK / "b.f32"
    extract(2, a)          # CH3 (anchor in this clip)
    extract(0, b)          # CH1 (the refused one: 1190 samples = 24.79 ms)
    one = recheck_residual(a, b, sample_rate=48000, storage_dtype="f32",
                           anchor_segment_seconds=3.0)
    print(f"single window (旧行为) : {one:+.3f} ms")
    for k in (3, 5, 9):
        r = recheck_residual_windows(
            a, b, sample_rate=48000, storage_dtype="f32", windows=k,
            anchor_segment_seconds=3.0)
        vals = [round(v, 2) for v in r["values"]]
        print(f"{k} windows           : worst={r['worst_ms']:+.3f} ms  "
              f"values={vals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
