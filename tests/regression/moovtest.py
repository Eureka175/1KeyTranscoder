#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is `MP4Box -inter` a safe post-pass that restores the moov-first layout?

It changes chunk interleaving, so packet order in the file changes. The
question is whether anything *decoded* changes. Checked here: per-stream
streamhash, ffprobe duration, and preservation.validate.compare.

    python moovtest.py SRC_BEFORE_AFTER  IN  OUT
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
FF = REPO / "tools" / "ffmpeg.exe"
FP = REPO / "tools" / "ffprobe.exe"


def streams(p: str) -> list[dict]:
    r = subprocess.run([str(FP), "-v", "error", "-show_streams", "-of",
                        "json", p], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(r.stdout or "{}").get("streams") or []


def shash(p: str, i: int) -> str:
    r = subprocess.run(
        [str(FF), "-v", "error", "-nostdin", "-i", p, "-map", f"0:{i}",
         "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip().split(",")[-1] if r.returncode == 0 else "ERR"


def dur(p: str) -> str:
    r = subprocess.run([str(FP), "-v", "error", "-show_format", "-of",
                        "json", p], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(r.stdout or "{}")["format"]["duration"]


def main() -> int:
    a, b = sys.argv[1], sys.argv[2]
    sa, sb = streams(a), streams(b)
    print(f"streams {len(sa)} vs {len(sb)}")
    for i in range(min(len(sa), len(sb))):
        ha, hb = shash(a, i), shash(b, i)
        tag = sa[i].get("codec_tag_string")
        verdict = "SAME" if ha == hb else "DIFF"
        print(f"  [{i}] {sa[i].get('codec_type')}/{tag}  {verdict}")
    print(f"duration {dur(a)} vs {dur(b)}")

    from preservation.gpac import GpacContainerBackend
    from preservation.validate import compare

    rep = compare(Path(a), Path(b), GpacContainerBackend(None), FP,
                  Path(r"E:\1kt_scratch\moovtest\val"), level="advanced")
    print("structural_success:", rep.get("structural_success"))
    print("summary:", rep.get("summary"))
    bad = [it for it in (rep.get("items") or [])
           if it.get("status") != "PRESERVED"]
    for it in bad[:20]:
        print("  ", it.get("status"), it.get("name"),
              str(it.get("detail"))[:120])

    # consumer side: does Gyroflow still read the file?
    from preservation.gyroflow import check as gyro_check, find_gyroflow

    gf = find_gyroflow(None)
    if gf is None:
        print("gyroflow: UNAVAILABLE")
    else:
        g = gyro_check(Path(a), Path(b), gf,
                       Path(r"E:\1kt_scratch\moovtest\gyro"))
        print("gyroflow:", g.get("status"), str(g.get("detail"))[:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
