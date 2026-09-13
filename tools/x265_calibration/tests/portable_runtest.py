#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end test of the portable 1KT-x265-portable workspace.

This is the test that matters for the soak host: it exercises RUN-TEST.bat
exactly as an operator does - media already sitting in input/, double-click,
walk away - and then asserts on the OUTCOME:

  * both processes come up (transcoder watcher + calibration collector)
  * the media in input/ is transcoded with no Python and no installed tools
  * calibration records appear by themselves, complete and joined
  * closing the launcher stops both processes

The workspace is produced by release/make_portable.py, which needs the
executables from release/build_standalone.py first:

    python tools/x265_calibration/release/build_standalone.py
    python tools/x265_calibration/release/make_portable.py --clean
    python tools/x265_calibration/tests/portable_runtest.py

Usage:  python tools/x265_calibration/tests/portable_runtest.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
PKG = PROJECT / "dist" / "x265_portable" / "1KT-x265-portable"
CORPUS = PROJECT / "work" / "pc" / "corpus"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" | {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)


def running(names: list[str]) -> dict[str, bool]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name " + ",".join("'%s'" % n for n in names)
         + " -ErrorAction SilentlyContinue | Select-Object "
           "-ExpandProperty ProcessName"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120,
    )
    text = (out.stdout or b"").decode("utf-8", "replace").lower()
    return {n.lower(): (n.lower() in text) for n in names}


def reset_workspace() -> None:
    """Empty output/logs/work/calibration but keep the guide files."""
    for sub in ("output", "logs", "work"):
        target = PKG / sub
        if not target.is_dir():
            continue
        for item in target.iterdir():
            if item.name == "PUT-MEDIA-HERE.txt":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
    calib = PKG / "calibration"
    if calib.is_dir():
        for item in calib.iterdir():
            if item.name == "README_FIRST.txt":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
    (calib / "events" / "pending").mkdir(parents=True, exist_ok=True)


def ensure_corpus() -> None:
    """Two tiny clips, built with the repo's ffmpeg if absent."""
    CORPUS.mkdir(parents=True, exist_ok=True)
    ffmpeg = PROJECT / "tools" / "ffmpeg.exe"
    for name, size, rate, bitrate in (
        ("a.mp4", "1920x1080", "30", "12000k"),
        ("b.mp4", "1280x720", "25", "700k"),
    ):
        target = CORPUS / name
        if target.is_file() and target.stat().st_size:
            continue
        subprocess.run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate={rate}:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-b:v", bitrate, str(target),
        ], check=True, timeout=600)


def main() -> int:
    print("=" * 78)
    print("PORTABLE WORKSPACE - RUN-TEST.bat END-TO-END")
    print("=" * 78)
    print(f"workspace: {PKG}")
    if not (PKG / "RUN-TEST.bat").is_file():
        print("[FATAL] the portable workspace is not built. Run:")
        print("        python tools/x265_calibration/release/"
              "make_portable.py --clean")
        return 2

    ensure_corpus()
    reset_workspace()
    for name in ("a.mp4", "b.mp4"):
        shutil.copy2(CORPUS / name, PKG / "input" / name)
    check("media staged in input/, nothing else prepared",
          len(list((PKG / "input").glob("*.mp4"))) == 2)

    # The host is supposed to have no Python: strip the interpreter out of
    # the environment so any accidental dependency shows up here.
    env = dict(os.environ)
    env["PATH"] = r"C:\Windows\system32;C:\Windows"
    env["PYTHONHOME"] = r"C:\nope"
    env["PYTHONPATH"] = r"C:\nope"

    stdout_path = PKG / "logs" / "_runtest_stdout.txt"
    outputs: list[Path] = []
    procs: dict[str, bool] = {}
    with stdout_path.open("wb") as fh:
        proc = subprocess.Popen(
            ["cmd", "/c", str(PKG / "RUN-TEST.bat")], cwd=str(PKG), env=env,
            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
            creationflags=0x00000200,
        )
        # Wait for the OUTCOME, not a fixed sleep: two delivered outputs and
        # both processes alive at the same time.
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(3)
            outputs = [p for p in (PKG / "output").rglob("*")
                       if p.is_file()
                       and p.suffix.upper() in (".MP4", ".MOV")
                       and ".1ktwork" not in str(p)]
            procs = running(["1kt-watch", "x265-calibration-collector"])
            if len(outputs) >= 2 and all(procs.values()):
                break
            if proc.poll() is not None:
                break
        time.sleep(8)                     # let the collector sweep
        procs = running(["1kt-watch", "x265-calibration-collector"])
        kill_tree(proc.pid)
        time.sleep(3)

    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    check("RUN-TEST.bat transcoded the media in input/",
          len(outputs) >= 2, f"{[p.name for p in outputs]}")
    check("transcoder and collector ran as two sibling processes",
          bool(procs.get("1kt-watch"))
          and bool(procs.get("x265-calibration-collector")), f"{procs}")
    after = running(["1kt-watch", "x265-calibration-collector"])
    check("closing the launcher stops both processes",
          not any(after.values()), f"{after}")

    # sweep anything still pending, then verify the dataset
    subprocess.run(
        [str(PKG / "bin" / "x265-calibration-collector.exe"), "--headless",
         "--once", "--watch", str(PKG / "calibration")],
        cwd=str(PKG), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=600,
    )
    records = []
    for path in sorted((PKG / "calibration" / "samples").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    check("calibration records appeared automatically (2 inputs -> 2 records)",
          len(records) == 2, f"{len(records)} records")
    if records:
        check("records complete: context + params + output + verification",
              all(r["completeness"]["complete"] for r in records),
              f"{[r['completeness']['missing'] for r in records]}")
        check("job final state recorded",
              all(r["job_final_status"] == "SUCCESS" for r in records))
        check("x265 tool version captured",
              all(r["libx265_version"] for r in records),
              records[0].get("libx265_version", ""))
        check("source classification and scaling factors captured",
              all(r["classification"].get("source_class")
                  and r["scaling_context"]["spatial_factor"] > 0
                  for r in records),
              sorted({r["classification"]["source_class"] for r in records}))
    check("launcher banner and both stages recorded in the transcript",
          "1KeyTranscoder - portable x265 test" in text
          and "starting calibration collector" in text
          and "1KeyTranscoder watchfolder" in text)
    check("no encode failures", "failed=0" in text)

    print()
    if FAILURES:
        print(f"PORTABLE WORKSPACE TEST FAILED: {len(FAILURES)} check(s)")
        for name in FAILURES:
            print(f"  - {name}")
        print()
        print("--- launcher transcript tail ---")
        print(text[-2500:])
        return 1
    print("PORTABLE WORKSPACE TEST: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
