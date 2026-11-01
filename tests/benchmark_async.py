#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Short async/scheduler benchmark (B5).

Runs 1kt.py on a small test tree, records wall time/counters/RSS, and writes a
JSON baseline that later runs can be compared against.

    python tests/benchmark_async.py --input K:\\...\\in --output K:\\...\\out \
        --jobs auto [--async-pipeline] --label p1-auto
    python tests/benchmark_async.py --input ... --output ... --compare old.json

This is a development benchmark, not part of full_autotest; it never mutates
the input tree (1kt only reads it).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "1kt.py"


def _sample_rss(pid: int, stop: threading.Event, peak: dict) -> None:
    try:
        import psutil
    except Exception:                                  # noqa: BLE001
        return
    proc = None
    try:
        proc = psutil.Process(pid)
    except Exception:                                  # noqa: BLE001
        return
    while not stop.is_set():
        try:
            rss = proc.memory_info().rss
            tree = rss + sum(
                p.memory_info().rss for p in proc.children(recursive=True))
            peak["main"] = max(peak.get("main", 0), rss)
            peak["tree"] = max(peak.get("tree", 0), tree)
        except Exception:                              # noqa: BLE001
            pass
        stop.wait(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--encoder", default="nvenc")
    ap.add_argument("--preset", default="FAST")
    ap.add_argument("--jobs", default="auto")
    ap.add_argument("--check", default="basic")
    ap.add_argument("--async-pipeline", action="store_true")
    ap.add_argument("--label", default="bench")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--compare", default=None,
                    help="baseline JSON; reports wall/work delta")
    args = ap.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "_benchmark_stdout.log"
    cmd = [
        sys.executable, str(ENTRY),
        "--input", args.input, "--output", str(out_dir),
        "--encoder", args.encoder, "--preset", args.preset,
        "--check", args.check, "--jobs", str(args.jobs), "--headless",
    ]
    if args.async_pipeline:
        cmd.append("--async-pipeline")

    env = dict(os.environ)
    env.setdefault("TMP", str(out_dir / "_tmp"))
    env.setdefault("TEMP", str(out_dir / "_tmp"))
    Path(env["TMP"]).mkdir(parents=True, exist_ok=True)

    peak: dict = {}
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log,
                                stderr=subprocess.STDOUT, env=env)
        stop = threading.Event()
        th = threading.Thread(target=_sample_rss, args=(proc.pid, stop, peak),
                              daemon=True)
        th.start()
        rc = proc.wait()
        stop.set()
        th.join(timeout=2)
    wall = time.perf_counter() - started

    text = log_path.read_text(encoding="utf-8", errors="replace")
    summary = {}
    for line in reversed(text.splitlines()):
        if "Finished | done=" in line:
            for token in line.split("|")[-1].strip().split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    try:
                        summary[k] = int(v)
                    except ValueError:
                        summary[k] = v
            break
    files = sorted(p for p in out_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".mp4", ".mov"))
    record = {
        "label": args.label,
        "wall_s": round(wall, 3),
        "rc": rc,
        "counters": summary,
        "output_files": len(files),
        "output_bytes": sum(p.stat().st_size for p in files),
        "rss_peak_mib": {
            "main": round(peak.get("main", 0) / 2**20, 1),
            "tree": round(peak.get("tree", 0) / 2**20, 1),
        },
        "cmd": cmd,
        "log": str(log_path),
    }
    out_json = Path(args.json_out) if args.json_out else out_dir / "_benchmark.json"
    out_json.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))

    if args.compare:
        old = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print(f"compare vs {old.get('label')}: "
              f"wall {old.get('wall_s')} -> {record['wall_s']} "
              f"({(record['wall_s'] / old['wall_s'] - 1) * 100:+.1f}%)")
    return 0 if rc == 0 and summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
