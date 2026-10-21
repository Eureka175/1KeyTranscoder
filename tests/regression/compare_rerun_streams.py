#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the re-repaired outputs with the delivered ones, stream by stream.

The 12 files whose decisions are unchanged should come out identical at the
stream level even if container bytes differ; C1213 is expected to differ on CH1
only (it now actually gets shifted).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

FFMPEG = Path(r"F:\1KeyTranscoder\tools\ffmpeg.exe")
NEW = Path(r"E:\1kt_scratch\gate_rerun\out")
OLD = Path(r"I:\repaired")


def stream_hash(p: Path, idx: int) -> str:
    r = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-i", str(p),
         "-map", f"0:{idx}", "-c", "copy", "-f", "streamhash",
         "-hash", "sha256", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    return r.stdout.strip().split(",")[-1] if r.returncode == 0 else "ERR"


def main() -> int:
    names = [p.stem for p in sorted(NEW.glob("*.MP4"))]
    print(f"{'file':<22}{'streams same/diff':<22}diff streams")
    print("-" * 78)
    for nm in names:
        old = list(OLD.rglob(nm + ".MP4"))
        if not old:
            print(f"{nm:<22}旧版本缺失")
            continue
        old, new = old[0], NEW / (nm + ".MP4")
        same, diff = 0, []
        for i in range(6):
            a, b = stream_hash(old, i), stream_hash(new, i)
            if a == b:
                same += 1
            else:
                diff.append(i)
        print(f"{nm:<22}{same}/6 same{'':<10}"
              + (",".join(str(d) for d in diff) if diff else "-"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
