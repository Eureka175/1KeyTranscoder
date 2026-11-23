# -*- coding: utf-8 -*-
"""4K native hardware full-matrix evaluation (night/day multi-source).

Runs 4 hardware encoders x 4 presets over 6 read-only source excerpts:
  night_action4_pov, night_dance_iso, day_action4_pov,
  day_daily_sony, day_daily_canon, event_sony_422

Each run gets its own input root (hardlinks) so 1kt's per-file logs are kept
and never overwritten.  Resumable via state.jsonl.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"F:\1KeyTranscoder")
BASE = ROOT / "work" / "hw4k_matrix"
CLIPS = BASE / "clips"
RUNS = BASE / "runs"
STATE = BASE / "state.jsonl"
PY = sys.executable

ENCODERS = ["nvenc", "qsv", "nvenc-av1", "qsv-av1"]
PRESETS = ["fast", "hq", "small", "uhq"]


def specs() -> list[dict]:
    out = []
    for preset in PRESETS:
        for enc in ENCODERS:
            out.append({"encoder": enc, "preset": preset})
    return out


def run_key(spec: dict) -> str:
    return f"{spec['encoder']}_{spec['preset']}"


def done(key: str) -> bool:
    if not STATE.is_file():
        return False
    for line in STATE.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("key") == key and rec.get("rc") == 0:
            return True
    return False


def prepare_input(run_dir: Path) -> Path:
    in_dir = run_dir / "input"
    in_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(CLIPS.glob("*.MP4")):
        dst = in_dir / src.name
        if dst.exists():
            continue
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    return in_dir


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    todo = specs()
    total = len(todo)
    started = time.monotonic()
    state_fh = STATE.open("a", encoding="utf-8")
    try:
        for idx, spec in enumerate(todo, 1):
            key = run_key(spec)
            if done(key):
                print(f"[{idx}/{total}] SKIP {key} (already ok)", flush=True)
                continue
            run_dir = RUNS / spec["preset"] / spec["encoder"]
            in_dir = prepare_input(run_dir)
            out_dir = run_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            console = run_dir / "run.log"
            cmd = [
                PY, str(ROOT / "1kt.py"),
                "--input", str(in_dir),
                "--output", str(out_dir),
                "--encoder", spec["encoder"],
                "--preset", spec["preset"],
                "--check", "basic",
                "--headless",
                "--jobs", "1",
            ]
            print(f"[{idx}/{total}] RUN {key} | clips={len(list(in_dir.glob('*.MP4')))}",
                  flush=True)
            t0 = time.monotonic()
            with console.open("w", encoding="utf-8", errors="replace") as fh:
                proc = subprocess.run(cmd, cwd=str(ROOT), stdout=fh,
                                      stderr=subprocess.STDOUT)
            wall = time.monotonic() - t0
            outputs = sorted(
                p for p in out_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in (".mp4", ".mov")
                and ".1ktwork" not in str(p)
            )
            rec = {
                "key": key, "encoder": spec["encoder"],
                "preset": spec["preset"],
                "run_dir": str(run_dir), "input_dir": str(in_dir),
                "out_dir": str(out_dir), "console_log": str(console),
                "rc": proc.returncode, "wall_sec": round(wall, 3),
                "outputs": [str(p) for p in outputs],
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            state_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            state_fh.flush()
            print(f"[{idx}/{total}] {'OK' if proc.returncode == 0 else 'RC=' + str(proc.returncode)} "
                  f"{key} | {wall/60:.1f} min | {len(outputs)} output(s)", flush=True)
    finally:
        state_fh.close()
    print(f"ALL DONE in {(time.monotonic()-started)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
