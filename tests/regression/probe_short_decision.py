#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Does geometry adaptation actually change the decision on the short clips?

The yield sweep said smaller geometry produces more usable frames for the pair
that already works (CH3/CH4), but the tracks that fail (CH1/CH2 of C1180, C1202)
produce zero frames under every geometry tried. Yield is not the question -- the
decision is. This runs the real production entry point on each clip under the
default geometry and under a short-clip one, and prints the per-track verdict
side by side.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

FFPROBE = REPO / "tools" / "ffprobe.exe"
FFMPEG = REPO / "tools" / "ffmpeg.exe"
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
WORK = Path(r"E:\1kt_scratch\short_geom")
CLIPS = ["20260904_C1180", "20260904_C1202", "20260903_C1162",
         "20260904_C1197", "20260904_C1211"]

VARIANTS = [
    ("默认几何", {}),
    ("短片段几何", {"frame_ms": 400.0, "hop_ms": 50.0,
                    "anchor_segment_seconds": 2.0}),
]


def streams(p: Path) -> list[dict]:
    r = subprocess.run([str(FFPROBE), "-v", "error", "-show_streams",
                        "-of", "json", str(p)],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return json.loads(r.stdout or "{}").get("streams") or []


def main() -> int:
    from core.channel_sync import run_channel_sync

    for clip in CLIPS:
        src = PROOF / f"{clip}.MP4"
        if not src.is_file():
            print(f"{clip}: 缺文件")
            continue
        st = streams(src)
        print(f"\n=== {clip}")
        for label, extra in VARIANTS:
            wd = WORK / f"{clip}_{abs(hash(label)) % 1000}"
            shutil.rmtree(wd, ignore_errors=True)
            logs: list[str] = []
            try:
                rep = run_channel_sync(
                    source=src, ffmpeg=FFMPEG, work_dir=wd, streams=st,
                    opts=extra, transparent=False,
                    log=lambda m: logs.append(m))
            except Exception as exc:          # noqa: BLE001
                print(f"  {label}: 异常 {type(exc).__name__}: {exc}")
                continue
            finally:
                shutil.rmtree(wd, ignore_errors=True)
            cells = []
            for c in rep.get("channels") or []:
                ch = (c.get("stream") or 0) + 1
                if c.get("decision") == "anchor":
                    cells.append(f"CH{ch}=锚")
                else:
                    cells.append(f"CH{ch}={c.get('decision')}"
                                 f"{'(' + str(c.get('reason')) + ')' if c.get('reason') else ''}"
                                 f"[{c.get('usable_frames')}帧]")
            print(f"  {label}: status={rep.get('status')}")
            print(f"    " + "  ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
