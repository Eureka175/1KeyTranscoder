#!/usr/bin/env python
"""Build the standalone x265 calibration collector executable.

Standard library only; run it with any Python 3.9+ interpreter that has
PyInstaller installed (this repo: Python 3.11.9 + PyInstaller 6.22.2).

    python tools\\x265_calibration\\release\\build_standalone.py
    python tools\\x265_calibration\\release\\build_standalone.py --clean
    python tools\\x265_calibration\\release\\build_standalone.py --workdir D:\\pi-work
    python tools\\x265_calibration\\release\\build_standalone.py --no-verify

What it does
------------
1. locates the spec (next to this script) and the repo root (derived from the
   spec location, never from the cwd),
2. checks every build input exists before starting PyInstaller,
3. optionally pre-cleans the work directory and the previous output,
4. runs ``python -m PyInstaller <spec> --noconfirm --workpath ... --distpath
   ... [--clean] [--log-level ...]`` with an explicit working directory,
5. prints the exact output path and size,
6. optionally re-applies an ASCII temp path when the system temp path is
   non-ASCII (PyInstaller's own extraction/bootstrap can misbehave there),
7. verifies the produced exe really runs (``--version``) and that the schema
   file it needs is reachable, then fails loudly on any problem.

On failure it prints the tail of the PyInstaller output and exits non-zero.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# constants (all paths derived from this file's location -> cwd independent)
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).resolve()
RELEASE_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = RELEASE_DIR.parent                      # tools/x265_calibration
REPO_ROOT = PROJECT_ROOT.parent.parent                 # F:\1KeyTranscoder

SPEC_PATH = RELEASE_DIR / "x265_calibration_collector.spec"
ENTRY_SCRIPT = RELEASE_DIR / "frozen_entry.py"
SCHEMA_PATH = PROJECT_ROOT / "schema.json"

# The three portable tool executables (analyzer / verifier / deploy helper).
# Built from one spec so the soak host needs no Python for ANY step, not just
# for collecting.
TOOL_SPEC_PATH = RELEASE_DIR / "x265_all_exes.spec"
TOOL_EXE_NAMES = ("x265-analyze.exe", "x265-verify.exe", "x265-deploy.exe")
TOOL_ENTRY_SCRIPTS = (
    RELEASE_DIR / "frozen_analyze.py",
    RELEASE_DIR / "frozen_verify.py",
    RELEASE_DIR / "frozen_deploy.py",
)

EXE_NAME = "x265-calibration-collector.exe"
DEFAULT_DIST_DIR = REPO_ROOT / "dist" / "x265_calibration"
DEFAULT_WORK_DIR = REPO_ROOT / "build" / "x265_calibration"

# working directory for the PyInstaller run: the directory from which the
# collector is importable as a top-level package (`python -m
# collector.collector_cli`), i.e. the semantic parent of the package.
BUILD_CWD = PROJECT_ROOT

OUTPUT_TAIL_LINES = 60


class BuildError(RuntimeError):
    """Raised for any condition that must abort the build."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def say(message: str = "") -> None:
    """Print one line, never dying on a hostile console codepage."""
    try:
        sys.stdout.write(str(message) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeEncodeError):
        try:
            sys.stdout.write(
                str(message).encode("ascii", "replace").decode("ascii") + "\n"
            )
            sys.stdout.flush()
        except OSError:
            pass


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return "%.2f %s" % (value, unit)
        value /= 1024.0
    return "%.2f GiB" % value


def is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def decode_output(raw: bytes) -> str:
    """Decode subprocess output without ever raising."""
    for encoding in ("utf-8", "cp1252", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def tail(text: str, lines: int = OUTPUT_TAIL_LINES) -> str:
    stripped = [line for line in text.replace("\r\n", "\n").split("\n")]
    while stripped and not stripped[-1].strip():
        stripped.pop()
    if len(stripped) <= lines:
        return "\n".join(stripped)
    return "... (output truncated) ...\n" + "\n".join(stripped[-lines:])


def rmtree_best_effort(path: Path, reason: str) -> None:
    if not path.exists():
        return
    say("  removing %s (%s)" % (path, reason))
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise BuildError(
            "cannot remove %s: %s\n"
            "A running instance of the collector or a scanner holding a file "
            "can cause this; close it and retry." % (path, exc)
        )


# ---------------------------------------------------------------------------
# environment inspection
# ---------------------------------------------------------------------------

def check_inputs() -> None:
    missing = []
    for label, path in (
        ("spec file", SPEC_PATH),
        ("frozen entry launcher", ENTRY_SCRIPT),
        ("collector package", PROJECT_ROOT / "collector" / "__init__.py"),
        ("collector CLI", PROJECT_ROOT / "collector" / "collector_cli.py"),
        ("collector core", PROJECT_ROOT / "collector" / "collector_core.py"),
        ("schema.json", SCHEMA_PATH),
        ("tool spec file", TOOL_SPEC_PATH),
    ):
        if not path.exists():
            missing.append("  - %s: %s" % (label, path))
    if missing:
        raise BuildError(
            "required build inputs are missing:\n" + "\n".join(missing)
        )

    # The spec must keep bundling schema.json at the bundle root, otherwise the
    # frozen exe cannot find it on a host that has no copy next to the exe.
    spec_text = SPEC_PATH.read_text(encoding="utf-8", errors="replace")
    if "schema.json" not in spec_text:
        raise BuildError(
            "spec %s does not appear to bundle schema.json" % SPEC_PATH
        )
    # The exe name in the spec decides the output file name; catch a rename
    # here rather than after a two-minute build.
    if "x265-calibration-collector" not in spec_text:
        raise BuildError(
            "spec %s does not set the expected application name "
            "'x265-calibration-collector'" % SPEC_PATH
        )


def pyinstaller_version() -> str:
    """Return PyInstaller's version, or raise BuildError."""
    try:
        completed = subprocess.run(
            [sys.executable, "-c",
             "import PyInstaller, sys; sys.stdout.write(PyInstaller.__version__)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise BuildError("cannot run %s: %s" % (sys.executable, exc))
    text = decode_output(completed.stdout or b"").strip()
    if completed.returncode != 0 or not text:
        raise BuildError(
            "PyInstaller is not importable by %s\n%s"
            % (sys.executable, tail(text))
        )
    return text


def temp_path_is_ascii() -> tuple[bool, str]:
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    return (is_ascii(temp) if temp else True), temp


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def run_pyinstaller(work_dir: Path, dist_dir: Path, clean: bool,
                    log_level: str, spec: Path | None = None) -> None:
    spec_path = spec or SPEC_PATH
    command = [
        sys.executable, "-m", "PyInstaller",
        str(spec_path),
        "--noconfirm",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--log-level", log_level,
    ]
    if clean:
        # --clean also wipes PyInstaller's own cache directory (CONF['cachedir']).
        command.append("--clean")

    say("  spec     : %s" % spec_path.name)
    say("  cwd      : %s" % BUILD_CWD)
    say("  command  : %s" % " ".join(command))
    started = _dt.datetime.now()

    try:
        completed = subprocess.run(
            command,
            cwd=str(BUILD_CWD),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise BuildError("cannot start PyInstaller: %s" % exc)

    output = decode_output(completed.stdout or b"")
    elapsed = (_dt.datetime.now() - started).total_seconds()
    say("  PyInstaller finished in %.1fs with exit code %d"
        % (elapsed, completed.returncode))

    if completed.returncode != 0:
        raise BuildError(
            "PyInstaller failed (exit code %d).\n"
            "----- tail of PyInstaller output -----\n%s\n"
            "--------------------------------------"
            % (completed.returncode, tail(output))
        )

    # Even on success, missing-module warnings are the classic cause of a
    # frozen exe that dies at startup; surface them.
    warnings = [
        line for line in output.replace("\r\n", "\n").split("\n")
        if "missing module" in line.lower()
        or "ERROR:" in line
        or "WARNING: Hidden import" in line
    ]
    if warnings:
        say("  PyInstaller warnings (%d line(s)):" % len(warnings))
        for line in warnings[:25]:
            say("    " + line.strip())
        if len(warnings) > 25:
            say("    ... %d more (see the warn file in the work dir)"
                % (len(warnings) - 25))


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_tool_exe(exe_path: Path, skip: bool) -> None:
    """Smoke-test a bundled tool exe.

    Runs it with an argument that exercises the full frozen import graph
    (including argparse) but does no work and never touches the network or the
    filesystem. A missing hidden import shows up here rather than on the soak
    host.
    """
    with exe_path.open("rb") as fh:
        if fh.read(2) != b"MZ":
            raise BuildError("%s is not a PE executable" % exe_path)
    if skip:
        return

    probes = {
        # A positional path that cannot exist: exercises the full import
        # graph and the CLI, but the tool exits with its documented
        # "dataset directory not found" path and writes nothing.
        "x265-analyze.exe": (
            ["__dsh_probe_no_such_dataset__"],
            ("not found", "fatal"),
        ),
        "x265-verify.exe": (["--help"], ("usage",)),
        "x265-deploy.exe": (["--help"], ("usage",)),
    }
    argv_expect = probes.get(exe_path.name)
    if argv_expect is None:
        return
    argv, expect = argv_expect

    try:
        completed = subprocess.run(
            [str(exe_path)] + argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError("cannot run %s: %s" % (exe_path.name, exc))

    text = decode_output(completed.stdout or b"")
    lowered = text.lower()
    # A frozen exe that failed to import its bundle prints a traceback.
    broken = ("traceback (most recent call last)" in lowered
              or "modulenotfounderror" in lowered
              or "importerror" in lowered)
    if broken or not any(token in lowered for token in expect):
        raise BuildError(
            "%s failed its smoke probe (exit code %d).\n"
            "----- exe output -----\n%s\n----------------------"
            % (exe_path.name, completed.returncode, tail(text, 20))
        )
    say("  %-18s probe OK" % exe_path.name)


def verify_exe(exe_path: Path, skip: bool) -> None:
    # The PE check is free and always worth doing.
    with exe_path.open("rb") as fh:
        raw = fh.read(2)
    if raw != b"MZ":
        raise BuildError(
            "%s is not a Windows PE executable (first bytes %r)"
            % (exe_path, raw)
        )

    if skip:
        say("  verification skipped (--no-verify); PE header check passed")
        return

    completed = subprocess.run(
        [str(exe_path), "--version"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(exe_path.parent),
    )
    text = decode_output(completed.stdout or b"")
    banner = text.replace("\r\n", "\n").strip()
    if completed.returncode != 0 or "x265-calibration-collector" not in banner:
        raise BuildError(
            "the frozen exe failed its --version smoke test "
            "(exit code %d).\n----- exe output -----\n%s\n----------------------\n"
            "A common cause is a package-relative import the frozen module "
            "graph did not resolve; check the warn-*.txt in the work dir."
            % (completed.returncode, tail(text, 25))
        )
    if "frozen: True" not in banner:
        raise BuildError(
            "the exe did not report frozen: True - wrong binary or stale exe.\n"
            "----- exe output -----\n%s" % tail(text, 25)
        )
    say("  smoke test OK:")
    for line in banner.split("\n"):
        say("    " + line)

    # Schema reachability. The exe resolves schema.json from the bundle, the
    # cwd, the exe dir and argv[0]'s dir. Running it from a directory that
    # contains no schema.json proves the *bundle* copy is the one found.
    scratch = exe_path.parent / "_schema_probe_no_schema_here"
    try:
        scratch.mkdir(exist_ok=True)
        probe = subprocess.run(
            [str(exe_path), "--headless", "--validate", str(scratch)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(scratch),
        )
        probe_text = decode_output(probe.stdout or b"")
        if "schema.json not found" in probe_text:
            raise BuildError(
                "the frozen exe cannot find schema.json: the bundle copy is "
                "missing.\n----- exe output -----\n%s" % tail(probe_text, 25)
            )
        # A successful run on an EMPTY dataset is the proof we want: the
        # schema was located (the bundle copy, since cwd/exe-dir/argv[0]
        # have none) and the validator ran end to end.
        if '"ok": true' not in probe_text and "[OK]" not in probe_text:
            raise BuildError(
                "unexpected --validate probe output.\n"
                "----- exe output -----\n%s" % tail(probe_text, 25)
            )
        say("  schema.json is reachable from inside the bundle")
    finally:
        try:
            if scratch.exists():
                scratch.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_standalone.py",
        description="Build x265-calibration-collector.exe (single-file, "
                    "console, offline).",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="wipe the work directory, PyInstaller's cache and the previous "
             "output before building (full rebuild).",
    )
    parser.add_argument(
        "--workdir", metavar="PATH", default=None,
        help="PyInstaller work directory (default: %s)." % DEFAULT_WORK_DIR,
    )
    parser.add_argument(
        "--distdir", metavar="PATH", default=None,
        help="output directory (default: %s)." % DEFAULT_DIST_DIR,
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="skip running the produced exe (--version / schema probe).",
    )
    parser.add_argument(
        "--no-tools", action="store_true",
        help="build only the collector; skip the portable tool executables "
             "(analyzer / verifier / deploy helper).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["TRACE", "DEBUG", "INFO", "WARN", "DEPRECATION", "ERROR",
                 "FATAL"],
        help="PyInstaller log level (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    work_dir = Path(args.workdir).expanduser().resolve() if args.workdir \
        else DEFAULT_WORK_DIR
    dist_dir = Path(args.distdir).expanduser().resolve() if args.distdir \
        else DEFAULT_DIST_DIR
    exe_path = dist_dir / EXE_NAME

    say("=" * 72)
    say("x265 calibration collector - standalone build")
    say("=" * 72)
    say("python      : %s (%s)" % (sys.version.split()[0], sys.executable))
    say("spec        : %s" % SPEC_PATH)
    say("repo root   : %s" % REPO_ROOT)
    say("work dir    : %s" % work_dir)
    say("output      : %s" % exe_path)

    try:
        check_inputs()
        version = pyinstaller_version()
        say("PyInstaller : %s" % version)

        if not is_ascii(str(REPO_ROOT)):
            raise BuildError(
                "the repo path contains non-ASCII characters (%s). PyInstaller "
                "is known to mis-handle non-ASCII paths in the build tree and "
                "in the extracted _MEI directory; move the checkout to an "
                "ASCII-only path such as C:\\build\\1KeyTranscoder." % REPO_ROOT
            )

        if args.clean:
            say("-" * 72)
            say("cleaning")
            rmtree_best_effort(work_dir, "--clean")
            rmtree_best_effort(dist_dir, "--clean")
            for stale in (
                REPO_ROOT / "build" / "x265_calibration_collector",
                Path(str(work_dir)) / "x265_calibration_collector",
            ):
                if stale != work_dir:
                    rmtree_best_effort(stale, "--clean (stale work dir)")

        ok_temp, temp_value = temp_path_is_ascii()
        if not ok_temp:
            fallback = Path("C:/1kt-pyi-temp")
            say("-" * 72)
            say("NOTE: TEMP/TMP is %r (non-ASCII). PyInstaller's bootstrap can "
                "fail on non-ASCII extraction paths." % temp_value)
            try:
                fallback.mkdir(parents=True, exist_ok=True)
                os.environ["TEMP"] = str(fallback)
                os.environ["TMP"] = str(fallback)
                say("      using %s for this build" % fallback)
            except OSError as exc:
                say("      could not create %s: %s" % (fallback, exc))

        say("-" * 72)
        say("building (this takes ~1-3 minutes)")
        run_pyinstaller(work_dir, dist_dir, args.clean, args.log_level)

        if not exe_path.is_file():
            raise BuildError(
                "PyInstaller reported success but %s does not exist." % exe_path
            )

        tool_exes: list[Path] = []
        if not args.no_tools:
            say("-" * 72)
            say("building the portable tool executables")
            say("  (the soak host has no Python: the analyzer and the other")
            say("   dev-side tools ship as standalone exes too)")
            run_pyinstaller(work_dir, dist_dir, args.clean, args.log_level,
                            spec=TOOL_SPEC_PATH)
            for name in TOOL_EXE_NAMES:
                candidate = dist_dir / name
                if not candidate.is_file():
                    raise BuildError(
                        "PyInstaller reported success but the tool "
                        "executable %s does not exist." % candidate
                    )
                tool_exes.append(candidate)

        say("-" * 72)
        say("verifying")
        verify_exe(exe_path, args.no_verify)
        for tool in tool_exes:
            verify_tool_exe(tool, args.no_verify)

    except BuildError as exc:
        say("")
        say("[BUILD FAILED] %s" % exc)
        return 1
    except KeyboardInterrupt:
        say("")
        say("[BUILD ABORTED] interrupted")
        return 130

    size_bytes = exe_path.stat().st_size
    say("=" * 72)
    say("[BUILD OK]")
    say("  exe  : %s" % exe_path)
    say("  size : %d bytes (%s)" % (size_bytes, human_size(size_bytes)))
    say("  built: %s" % _dt.datetime.fromtimestamp(
        exe_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
    for tool in tool_exes:
        size = tool.stat().st_size
        say("  tool : %s  (%s)" % (tool, human_size(size)))
    say("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
