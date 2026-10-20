#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why is CH1 still ~998 samples away from CH3 in the repaired C1213?

Runs the production entry point with the grouped anchor on and prints the
grouping / SNR table / per-track decisions, so the answer is structural rather
than "it's close enough".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
WORK = Path(r"E:\1kt_scratch\why_ch1")

CLIP = Path(sys.argv[1] if len(sys.argv) > 1 else
            r"I:\repaired\20260904\20260904_C1213.MP4")


def streams(p: Path) -> list[dict]:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-of", "json", str(p)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    return json.loads(r.stdout or "{}").get("streams") or []


def main() -> int:
    from core.channel_sync import run_channel_sync

    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.glob("*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    logs: list[str] = []
    rep = run_channel_sync(
        source=CLIP, ffmpeg=FFMPEG, work_dir=WORK, streams=streams(CLIP),
        opts={"group_anchor_enabled": True}, transparent=False,
        log=lambda m: (logs.append(m), print("  ", m))[1])
    print(f"\nstatus={rep.get('status')}")
    g = rep.get("grouping") or {}
    print(f"grouping enabled={g.get('enabled')} groups={g.get('groups')} "
          f"primary_anchor={g.get('anchor')} "
          f"secondary={g.get('secondary_anchor_refs')}")
    if g.get("snr"):
        print("SNR (median 峰/噪声底, samples):")
        for k, v in g["snr"].items():
            print(f"    {k}: median={v.get('median')} "
                  f"samples={v.get('samples')}")
    print("\nper-track:")
    for c in rep.get("channels") or []:
        print(f"  CH{(c.get('stream') or 0) + 1}: decision={c.get('decision')} "
              f"role={c.get('anchor_role')} reason={c.get('reason')} "
              f"delay_ms={c.get('delay_ms')} "
              f"shift={c.get('shift_samples')} "
              f"frames={c.get('usable_frames')} "
              f"conf={c.get('confidence')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
