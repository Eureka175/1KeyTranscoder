#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-root convenience launcher for DJI Action split recordings.

The feature itself lives in the main program (``1kt.py --dji-join``, default
off) and in :mod:`core.dji_join`; this wrapper only adds:

* several input roots in one command (the main program takes one);
* ``--join-only`` to materialise the joined staging tree and stop before
  encoding, for inspection.

It deliberately does not implement a second join or encoder pipeline: the
normal mode forwards root by root to ``1kt.py --dji-join``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.dji_join import detect_plan, stage_plan, write_reports
from core.paths import discover_sources
from preservation.gpac import GpacContainerBackend

WORK_PREFIX = ".dji_join_work_"


def _load_gpac(script_dir: Path) -> GpacContainerBackend:
    local = script_dir / "tools" / "GPAC"
    if (local / "mp4box.exe").is_file() or (local / "MP4Box.exe").is_file():
        return GpacContainerBackend(local)
    return GpacContainerBackend()


def _join_only(
    *,
    roots: list[Path],
    output_root: Path,
    ffprobe: Path,
    gpac: GpacContainerBackend,
) -> int:
    staging_root = output_root / (
        f"{WORK_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{os.getpid()}"
    )
    failed = 0
    for root in roots:
        sources = discover_sources(root)
        plan = detect_plan(root, sources, ffprobe, log=print)
        report_dir = output_root / "logs" / "dji_join"
        if not plan.groups:
            write_reports(report_dir=report_dir, plan=plan)
            print(f"[JOIN-ONLY] {root.name}: no split recording detected")
            continue
        staging_root.mkdir(parents=True, exist_ok=True)
        root_staging = staging_root / root.name
        try:
            staging = stage_plan(
                plan=plan,
                staging_root=root_staging,
                gpac=gpac,
                ffprobe=ffprobe,
                log=print,
            )
        except Exception as exc:                   # noqa: BLE001
            write_reports(report_dir=report_dir, plan=plan)
            print(f"[JOIN-ONLY] FAILED {root}: {exc}", file=sys.stderr)
            failed += 1
            continue
        write_reports(
            report_dir=report_dir,
            plan=plan,
            joined=staging.joined,
            staging_root=root_staging,
        )
        print(f"[JOIN-ONLY] {root.name}: {len(staging.joined)} joined -> "
              f"{root_staging}")
    if staging_root.exists():
        print(f"[JOIN-ONLY] staging tree: {staging_root}")
    else:
        print("[JOIN-ONLY] nothing to join")
    return 1 if failed else 0


def _run_main(
    *,
    roots: list[Path],
    args: argparse.Namespace,
    script_dir: Path,
) -> int:
    for root in roots:
        cmd = [
            sys.executable, str(script_dir / "1kt.py"),
            "--input", str(root),
            "--output", str(Path(args.output).resolve()),
            "--dji-join",
            "--encoder", str(args.encoder),
            "--preset", str(args.preset),
            "--check", str(args.check),
            "--jobs", str(args.jobs),
        ]
        if args.scale:
            cmd += ["--scale", str(args.scale)]
        if args.ffprobe:
            cmd += ["--ffprobe", str(Path(args.ffprobe).resolve())]
        if args.ffmpeg:
            cmd += ["--ffmpeg", str(Path(args.ffmpeg).resolve())]
        if args.headless:
            cmd.append("--headless")
        if args.preserve_metadata:
            cmd.append("--preserve-metadata")
        if args.keep_temp:
            cmd.append("--dji-join-keep-temp")
        if args.dry_run:
            cmd.append("--dry-run")
        print("[DJI-JOIN] " + subprocess.list2cmdline(cmd), flush=True)
        proc = subprocess.run(cmd, cwd=str(script_dir))
        if proc.returncode != 0:
            print(f"[DJI-JOIN] 1kt.py failed (rc={proc.returncode}) for {root}",
                  file=sys.stderr)
            return proc.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", nargs="+", required=True,
                    help="input roots (each is searched recursively)")
    ap.add_argument("--output", required=True, help="final output root")
    ap.add_argument("--ffprobe", default=str(script_dir / "tools" / "ffprobe.exe"))
    ap.add_argument("--ffmpeg", default=str(script_dir / "tools" / "ffmpeg.exe"))
    ap.add_argument("--encoder", default="nvenc")
    ap.add_argument("--preset", default="fast")
    ap.add_argument("--scale", default=None,
                    help="optional long-edge factor; omit for original size")
    ap.add_argument("--check", default="basic")
    ap.add_argument("--jobs", default="1")
    ap.add_argument("--preserve-metadata", action="store_true", default=True)
    ap.add_argument("--no-preserve-metadata", dest="preserve_metadata",
                    action="store_false")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.add_argument("--dry-run", action="store_true",
                    help="only detect groups; do not join or encode")
    ap.add_argument("--join-only", action="store_true",
                    help="join into a staging tree under --output; do not encode")
    ap.add_argument("--keep-temp", action="store_true",
                    help="keep the staging tree after a successful encode")
    args = ap.parse_args(argv)

    roots: list[Path] = []
    for raw in args.input:
        root = Path(raw).resolve()
        if not root.is_dir():
            print(f"[FATAL] input root not found: {root}", file=sys.stderr)
            return 2
        roots.append(root)
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    # Never write a staging/join artefact inside the (read-only) source tree.
    for root in roots:
        if output_root == root or output_root.is_relative_to(root):
            print(f"[FATAL] --output must not be inside --input: {output_root}",
                  file=sys.stderr)
            return 2

    if args.join_only:
        return _join_only(
            roots=roots,
            output_root=output_root,
            ffprobe=Path(args.ffprobe).resolve(),
            gpac=_load_gpac(script_dir),
        )
    return _run_main(roots=roots, args=args, script_dir=script_dir)


if __name__ == "__main__":
    raise SystemExit(main())
