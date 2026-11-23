# -*- coding: utf-8 -*-
"""SVT-AV1 (software) 4K matrix over the same 6 clips. Resumable.

Usage: python run_svt_matrix.py [fast hq small]   (uhq is impractical at 4K)
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
BASE = ROOT / "work" / "svt4k_matrix"
CLIPS = Path(os.environ.get("SVT_CLIPS", str(ROOT / "work" / "hw4k_matrix" / "clips")))
RUNS = BASE / "runs"
STATE = BASE / "state.jsonl"
PY = sys.executable
ENCODER = "svtav1"


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
    presets = sys.argv[1:] or ["fast", "hq", "small"]
    BASE.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    state_fh = STATE.open("a", encoding="utf-8")
    started = time.monotonic()
    try:
        for i, preset in enumerate(presets, 1):
            key = f"svtav1_{preset}"
            if done(key):
                print(f"[{i}/{len(presets)}] SKIP {key}", flush=True)
                continue
            run_dir = RUNS / preset / ENCODER
            in_dir = prepare_input(run_dir)
            out_dir = run_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            console = run_dir / "run.log"
            cmd = [PY, str(ROOT / "1kt.py"),
                   "--input", str(in_dir), "--output", str(out_dir),
                   "--encoder", ENCODER, "--preset", preset,
                   "--check", "basic", "--headless", "--jobs", "1"]
            print(f"[{i}/{len(presets)}] RUN {key} | clips=6", flush=True)
            t0 = time.monotonic()
            with console.open("w", encoding="utf-8", errors="replace") as fh:
                proc = subprocess.run(cmd, cwd=str(ROOT), stdout=fh,
                                      stderr=subprocess.STDOUT)
            wall = time.monotonic() - t0
            outs = sorted(p for p in out_dir.rglob("*")
                          if p.is_file() and p.suffix.lower() in (".mp4", ".mov")
                          and ".1ktwork" not in str(p))
            rec = {"key": key, "encoder": ENCODER, "preset": preset,
                   "run_dir": str(run_dir), "out_dir": str(out_dir),
                   "console_log": str(console), "rc": proc.returncode,
                   "wall_sec": round(wall, 3),
                   "outputs": [str(p) for p in outs],
                   "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
            state_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            state_fh.flush()
            print(f"[{i}/{len(presets)}] "
                  f"{'OK' if proc.returncode == 0 else 'RC=' + str(proc.returncode)} "
                  f"{key} | {wall/60:.1f} min | {len(outs)} output(s)", flush=True)
    finally:
        state_fh.close()
    print(f"ALL DONE in {(time.monotonic()-started)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

