"""Frozen entry point: the watchfolder runner.

``watchfolder.py`` polls an input directory and runs the transcoder as a
subprocess once per interval. It is the "drop files into input/ and walk
away" driver.

When frozen, ``watchfolder.py``'s own ``sys.executable + 1kt.py`` invocation
is wrong: ``sys.executable`` is this binary, and there is no ``1kt.py`` on
disk. This launcher therefore locates the REAL transcoder executable and
passes it explicitly, so the watch loop and the transcoder stay separate
processes exactly as in a source checkout.

Resolution order for the transcoder:

  1. ``--transcoder`` on the command line (explicit always wins)
  2. ``1kt.exe`` next to this executable
  3. ``..\\bin\\1kt.exe`` (the portable package layout)
  4. ``tools\\1kt.exe``
  5. a source checkout: ``python <repo>\\1kt.py`` (development convenience)

The chosen command is printed at startup so a wrong pick is visible
immediately rather than after the first poll interval.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(sys.executable).resolve().parent
SPAWN_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _root() -> Path:
    """Package root: the folder containing bin/, tools/, config/."""
    for candidate in (HERE, HERE.parent):
        if (candidate / "tools").is_dir() and (candidate / "bin").is_dir():
            return candidate
    return HERE.parent


def discover(explicit: str | None, *candidates: Path) -> str | None:
    """Resolve a toolchain path: explicit wins, then the package layout.

    Auto-discovery exists because a long caret-continued cmd.exe command
    line with nested quoting is fragile and failed silently in testing: the
    launcher's `^`-joined arguments did not reach this program intact, so the
    transcoder started without its tool paths and could not find ffmpeg.
    With discovery, RUN-TEST.bat only has to pass the interesting knobs.
    """
    if explicit:
        return str(Path(explicit).resolve())
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate.resolve())
    return None


def find_transcoder(explicit: str | None, root: Path | None = None) -> list[str]:
    if explicit:
        path = Path(explicit)
        if path.is_file():
            if path.suffix.lower() == ".py":
                return [sys.executable, str(path)]
            return [str(path)]
        raise SystemExit("[FATAL] --transcoder not found: %s" % path)

    roots = [r for r in (HERE, root, HERE.parent, HERE.parent.parent) if r]
    for base in roots:
        for candidate in (
            base / "1kt.exe",
            base / "bin" / "1kt.exe",
            base / "tools" / "1kt.exe",
        ):
            if candidate.is_file():
                return [str(candidate.resolve())]

    # source checkout: this exe lives in <repo>/tools/x265_calibration/release
    for up in (HERE, *HERE.parents):
        script = up / "1kt.py"
        if script.is_file():
            return [sys.executable, str(script)]

    raise SystemExit(
        "[FATAL] cannot find the transcoder. Looked for 1kt.exe next to this "
        "program, in bin\\ and in tools\\, then for a 1kt.py source checkout. "
        "Pass --transcoder <path> to be explicit."
    )


def parse_args(argv: list[str]) -> dict:
    import argparse

    ap = argparse.ArgumentParser(
        prog="1kt-watch",
        description="Watch a folder and transcode whatever appears in it.",
    )
    ap.add_argument("--version", action="version", version="1kt-watch 1.0.0")
    ap.add_argument("--input", required=True, help="input folder to watch")
    ap.add_argument("--output", required=True, help="output folder")
    ap.add_argument("--encoder", default="x265")
    ap.add_argument("--preset", default="hq")
    ap.add_argument("--check", default="basic",
                    choices=["basic", "advanced", "full"])
    ap.add_argument("--jobs", default="1")
    ap.add_argument("--interval", type=int, default=60,
                    help="seconds between scans (default 60)")
    ap.add_argument("--transcoder", default=None,
                    help="explicit path to 1kt.exe (or 1kt.py)")
    ap.add_argument("--once", action="store_true",
                    help="run a single pass and exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--calibration-spool", default=None,
                    help="enable calibration events into this directory")
    # Explicit toolchain/config paths instead of a generic pass-through
    # string. A generic "--extra <quoted string>" cannot survive the trip
    # through cmd.exe -> this program -> the transcoder: cmd.exe does not
    # treat \" as an escape, and nested quoting arrived mangled. Typed
    # options avoid the whole problem.
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    ap.add_argument("--gpac-dir", default=None)
    ap.add_argument("--config", default=None,
                    help="encoder profile JSON (x265.json)")
    ap.add_argument("--scaling-config", default=None,
                    help="scaling rules JSON (x265_scaling.json)")
    return vars(ap.parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    inp = Path(args["input"]).resolve()
    out = Path(args["output"]).resolve()
    inp.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    root = _root()
    transcoder = find_transcoder(args["transcoder"], root)

    cmd = transcoder + [
        "--input", str(inp),
        "--output", str(out),
        "--encoder", args["encoder"],
        "--preset", args["preset"],
        "--check", args["check"],
        "--jobs", str(args["jobs"]),
        "--headless",
    ]
    if args["dry_run"]:
        cmd.append("--dry-run")

    resolved_tools = {
        "--ffmpeg": discover(args["ffmpeg"], root / "tools" / "ffmpeg.exe"),
        "--ffprobe": discover(args["ffprobe"], root / "tools" / "ffprobe.exe"),
        "--gpac-dir": discover(args["gpac_dir"],
                               root / "tools" / "GPAC" / "mp4box.exe"),
        "--config": discover(args["config"], root / "config" / "x265.json",
                             root / "x265.json"),
        "--scaling-config": discover(args["scaling_config"],
                                     root / "config" / "x265_scaling.json",
                                     root / "x265_scaling.json"),
    }
    for option, value in resolved_tools.items():
        if value:
            # --gpac-dir takes the directory, everything else a file
            cmd += [option, str(Path(value).parent)
                    if option == "--gpac-dir" else value]
    missing = [opt for opt, val in resolved_tools.items() if not val]
    if missing:
        print("[WARN] could not locate: %s" % ", ".join(missing))
        print("       the transcoder will fall back to its own search "
              "(beside the exe, tools\\, PATH)")

    env = dict(os.environ)
    spool = args["calibration_spool"]
    if spool:
        env["ONEXKT_CALIBRATION"] = "1"
        env["ONEXKT_CALIBRATION_SPOOL"] = str(Path(spool).resolve())

    print("=" * 74)
    print(" 1KeyTranscoder watchfolder")
    print("-" * 74)
    print(" input      : %s" % inp)
    print(" output     : %s" % out)
    print(" encoder    : %s   preset: %s   check: %s   jobs: %s"
          % (args["encoder"], args["preset"], args["check"], args["jobs"]))
    print(" interval   : %ss" % args["interval"])
    print(" transcoder : %s" % " ".join(transcoder))
    if spool:
        print(" calibration: %s" % env["ONEXKT_CALIBRATION_SPOOL"])
    else:
        print(" calibration: disabled (no --calibration-spool)")
    print("=" * 74)
    print(" Drop media files into the input folder. Existing outputs are")
    print(" skipped, so re-scanning is cheap. Ctrl+C stops the loop.")
    print("=" * 74)
    print(flush=True)

    pass_no = 0
    while True:
        pass_no += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print("\n[watch] pass %d at %s" % (pass_no, stamp), flush=True)
        try:
            proc = subprocess.run(cmd, cwd=str(inp.parent), env=env)
        except KeyboardInterrupt:
            print("\n[watch] stopped by user")
            return 0
        print("[watch] pass %d finished rc=%d" % (pass_no, proc.returncode),
              flush=True)
        if args["once"]:
            return proc.returncode
        try:
            time.sleep(max(1, int(args["interval"])))
        except KeyboardInterrupt:
            print("\n[watch] stopped by user")
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write(
            "[FATAL] watch launcher: %s: %s\n" % (type(exc).__name__, exc)
        )
        sys.exit(1)
