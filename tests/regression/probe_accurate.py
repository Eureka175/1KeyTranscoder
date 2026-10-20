#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe `--channel-sync-accurate` on the real clips that carry a 2..5 sample
offset — the only place the tiering can actually be observed.

Audio path only (`transparent=False`): the question is what the decision and
the post-fix recheck say, not what the container looks like.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
SCRATCH = Path(r"E:\1kt_scratch\accurate_probe")

CLIPS = [
    r"I:\20260901\20260901_C0977.MP4",
    r"I:\20260901\20260901_C1016.MP4",
]


def streams(p: Path) -> list[dict]:
    import subprocess

    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-of", "json", str(p)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    return json.loads(r.stdout or "{}").get("streams") or []


def run(p: Path, accurate: bool) -> dict:
    import numpy  # noqa: F401

    from core.channel_sync import run_channel_sync

    wd = SCRATCH / (p.stem + ("_acc" if accurate else "_base"))
    if wd.exists():
        shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    t0 = time.perf_counter()
    opts = {"channel_sync_accurate": True} if accurate else None
    try:
        rep = run_channel_sync(
            source=p, ffmpeg=FFMPEG, work_dir=wd, streams=streams(p),
            opts=opts, log=logs.append, transparent=False)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    return {"status": rep.get("status"),
            "channels": rep.get("channels") or [],
            "accurate": rep.get("accurate"),
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "warnings": [ln for ln in logs if "WARNING" in ln
                         or "accurate" in ln]}


def show(tag: str, rep: dict) -> None:
    print(f"  {tag:<10} status={rep['status']:<16} {rep['elapsed_s']}s")
    for c in rep["channels"]:
        d = c.get("decision")
        sh = c.get("shift_samples")
        fr = c.get("usable_frames")
        print(f"      CH{(c.get('stream') or 0) + 1} {d:<16} "
              f"shift={str(sh):<6} delay_ms={c.get('delay_ms')} "
              f"frames={fr} reason={c.get('reason')}")
    if rep.get("accurate"):
        a = rep["accurate"]
        print(f"      accurate: tier={a['tier_samples']} "
              f"kept={a['kept']} dropped={a['dropped']}")
        print(f"      candidates: {json.dumps(a['candidates'], ensure_ascii=False)}")
    for w in rep["warnings"]:
        print(f"      LOG {w}")


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for clip in CLIPS:
        p = Path(clip)
        print(f"\n=== {p.name} ===")
        if not p.is_file():
            print("  MISSING")
            continue
        show("baseline", run(p, False))
        show("accurate", run(p, True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
