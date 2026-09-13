"""Deployment helpers for the x265 calibration collector (dev-machine side).

    python tools/x265_calibration/release/deploy_helpers.py package
        -> dist/x265_release/x265-calibration-collector/ (staging folder)

    python tools/x265_calibration/release/deploy_helpers.py make-zip
        -> dist/x265_release/x265-calibration-collector-v<version>.zip
           THE deliverable: one folder, extract-and-run on the soak host.
           Delegates to release/make_zip.py.

    python tools/x265_calibration/release/deploy_helpers.py install-dir D:\\1kt\\collector
        -> writes the scheduled-task install script + start.bat into a folder
           (superseded by the .bat files already inside the zip)

    python tools/x265_calibration/release/deploy_helpers.py schedule-task
        -> prints the schtasks.exe command (does NOT execute it)

    python tools/x265_calibration/release/deploy_helpers.py offline-check
        -> static check: nothing in the collector can reach the network

    python tools/x265_calibration/release/deploy_helpers.py check-package [DIR]
        -> runs the packaged exe in the given (or staged) folder

Standard library only. Nothing here runs during the soak.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

RELEASE_DIR = PROJECT_ROOT / "dist" / "x265_release"
PACKAGE_NAME = "x265-calibration-collector"
EXE_NAME = "x265-calibration-collector.exe"

SHIP_FILES = (
    (PROJECT_ROOT / "tools" / "x265_calibration" / "README.txt", "README.txt"),
    (PROJECT_ROOT / "tools" / "x265_calibration" / "schema.json",
     "schema.json"),
    (PROJECT_ROOT / "tools" / "x265_calibration" / "collector.toml",
     "collector.toml"),
    (PROJECT_ROOT / "VERSION", "VERSION"),
)


def exe_path() -> Path:
    return (
        PROJECT_ROOT / "dist" / "x265_calibration" / EXE_NAME
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _here() -> Path:
    return Path(__file__).resolve().parent


def build_package(clean: bool = False) -> int:
    exe = exe_path()
    if not exe.is_file():
        print(f"[FAIL] executable not found: {exe}")
        print("       run: python tools/x265_calibration/release/"
              "build_standalone.py --clean")
        return 1

    target = RELEASE_DIR / PACKAGE_NAME
    if clean and target.exists():
        shutil.rmtree(target)
    (target / "tools").mkdir(parents=True, exist_ok=True)

    shutil.copy2(exe, target / EXE_NAME)
    for source, name in SHIP_FILES:
        if not source.is_file():
            print(f"[FAIL] missing release input: {source}")
            return 1
        shutil.copy2(source, target / name)

    for helper in ("analyze.py",):
        source = PROJECT_ROOT / "tools" / "x265_calibration" / helper
        if source.is_file():
            shutil.copy2(source, target / "tools" / helper)
    for helper in ("verify_frozen_exe.py", "deploy_helpers.py"):
        source = _here() / helper
        if source.is_file():
            shutil.copy2(source, target / "tools" / helper)

    # checksums for the deployment record (reproducibility identity)
    lines = []
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{_sha256(path)}  {path.relative_to(target).as_posix()}")
    (target / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    exe_size = (target / EXE_NAME).stat().st_size
    print("=" * 72)
    print("[PACKAGE OK]")
    print(f"  directory : {target}")
    print(f"  exe       : {EXE_NAME} ({exe_size:,} bytes)")
    print("  contents  :")
    for path in sorted(target.rglob("*")):
        if path.is_file():
            rel = path.relative_to(target).as_posix()
            print(f"    {rel:<50} {path.stat().st_size:>12,}")
    print("=" * 72)
    print("Copy this whole directory to the soak host, then read README.txt.")
    return 0


def write_install_scripts(install_dir: Path) -> int:
    install_dir.mkdir(parents=True, exist_ok=True)
    start_bat = install_dir / "start-collector.bat"
    start_bat.write_text(
        "@echo off\r\n"
        "rem Start the x265 calibration collector (headless).\r\n"
        "rem Run this from Task Scheduler or interactively.\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        f"\"%~dp0{EXE_NAME}\" --headless --watch \"%~dp0..\\calibration\" "
        "--verbose\r\n"
        "exit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )
    install_task = install_dir / "install-scheduled-task.cmd"
    install_task.write_text(
        "@echo off\r\n"
        "rem Registers the collector as a startup task. Run as Administrator.\r\n"
        "setlocal\r\n"
        "set TASK=1KeyTranscoder Calibration Collector\r\n"
        f"set EXE=%~dp0{EXE_NAME}\r\n"
        "set ARGS=--headless --watch \"%~dp0..\\calibration\"\r\n"
        "schtasks /Create /TN \"%TASK%\" /SC ONSTART /RU SYSTEM /RL HIGHEST "
        "/TR \"\\\"%EXE%\\\" %ARGS%\" /F\r\n"
        "if errorlevel 1 (\r\n"
        "  echo [FAIL] schtasks failed. Run this script as Administrator.\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "echo [OK] task registered. Configure failure-restart in Task "
        "Scheduler properties.\r\n"
        "schtasks /Query /TN \"%TASK%\" /V /FO LIST\r\n",
        encoding="utf-8",
    )
    uninstall_task = install_dir / "uninstall-scheduled-task.cmd"
    uninstall_task.write_text(
        "@echo off\r\n"
        "schtasks /Delete /TN \"1KeyTranscoder Calibration Collector\" /F\r\n",
        encoding="utf-8",
    )
    print(f"[OK] install scripts written to {install_dir}")
    for name in ("start-collector.bat", "install-scheduled-task.cmd",
                 "uninstall-scheduled-task.cmd"):
        print(f"     {name}")
    return 0


def print_schedule_command() -> int:
    exe = exe_path()
    print("Register the collector task (run in an elevated cmd.exe):")
    print()
    print(
        'schtasks /Create /TN "1KeyTranscoder Calibration Collector" '
        "/SC ONSTART /RU SYSTEM /RL HIGHEST "
        f'/TR "\\"{exe}\\" --headless --watch D:\\1kt\\calibration" /F'
    )
    print()
    print("Then, in Task Scheduler, open the task and set:")
    print("  [x] Restart the task if it fails (1 minute, up to 999 times)")
    print("  [ ] Stop the task if it runs longer than  -> DISABLED")
    print("  [x] If the task is already running -> Do not start a new instance")
    print()
    print("Production side (system environment variables for the account that")
    print("runs the transcoder):")
    print("  ONEXKT_CALIBRATION=1")
    print("  ONEXKT_CALIBRATION_SPOOL=D:\\1kt\\calibration")
    return 0


def check_offline() -> int:
    """Static check: nothing in the collector can reach the network.

    ``socket`` is imported for ``gethostname()`` only (a local call);
    every actually network-capable construct is rejected here.
    """
    banned = (
        "import ssl", "import http", "import urllib", "import requests",
        "from http", "from urllib", "urlopen", "socket.socket",
        "socket.create_connection", "subprocess.Popen", "subprocess.run",
        "os.system", "requests.get", "requests.post",
    )
    collector_dir = PROJECT_ROOT / "tools" / "x265_calibration" / "collector"
    problems: list[str] = []
    for path in sorted(collector_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                problems.append(f"{path.name}: {needle}")
        # socket may only be used for the local host name
        if "import socket" in text:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("socket.") and "gethostname" not in stripped:
                    problems.append(
                        f"{path.name}: socket use -> {stripped[:60]}"
                    )
    if problems:
        for item in problems:
            print(f"[CHECK] {item}")
        return 1
    print("[OK] no network-capable imports or calls in collector/*.py")
    print("     (socket.gethostname() only, which is a local call)")
    return 0


def run_deployed_check(package_dir: Path) -> int:
    exe = package_dir / EXE_NAME
    if not exe.is_file():
        print(f"[FAIL] {exe} not found")
        return 1
    scratch = package_dir / "_probe"
    scratch.mkdir(exist_ok=True)
    try:
        probe = subprocess.run(
            [str(exe), "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(scratch), timeout=120,
        )
        print(probe.stdout.decode("utf-8", "replace"))
        if probe.returncode != 0:
            return 1
        watch = subprocess.run(
            [str(exe), "--headless", "--once", "--watch", str(scratch)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(scratch), timeout=300,
        )
        print(watch.stdout.decode("utf-8", "replace")[-1500:])
        print(f"exit code: {watch.returncode}")
        return watch.returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_make_zip(extra: list[str]) -> int:
    """Delegate to the sibling make_zip.py (it is a module, not a package)."""
    import importlib.util

    script = _here() / "make_zip.py"
    if not script.is_file():
        print(f"[FAIL] {script} not found next to deploy_helpers.py")
        return 1
    spec = importlib.util.spec_from_file_location("_cal_make_zip", script)
    if spec is None or spec.loader is None:
        print(f"[FAIL] cannot load {script}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main(extra))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_helpers.py")
    sub = parser.add_subparsers(dest="command", required=True)
    pkg = sub.add_parser("package", help="build the release package directory")
    pkg.add_argument("--clean", action="store_true")
    mz = sub.add_parser("make-zip",
                        help="build the deployable extract-and-run zip")
    mz.add_argument("--clean", action="store_true")
    mz.add_argument("--out", type=Path, default=None)
    mz.add_argument("--keep-stage", action="store_true")
    inst = sub.add_parser("install-dir", help="write deployment scripts")
    inst.add_argument("directory", type=Path)
    sub.add_parser("schedule-task", help="print the schtasks command")
    sub.add_parser("offline-check", help="static no-network check")
    check = sub.add_parser("check-package", help="run the packaged exe")
    check.add_argument("directory", type=Path, nargs="?",
                       default=RELEASE_DIR / PACKAGE_NAME)
    args = parser.parse_args(argv)

    if args.command == "package":
        return build_package(clean=args.clean)
    if args.command == "make-zip":
        extra: list[str] = []
        if args.clean:
            extra.append("--clean")
        if args.keep_stage:
            extra.append("--keep-stage")
        if args.out is not None:
            extra += ["--out", str(args.out)]
        return run_make_zip(extra)
    if args.command == "install-dir":
        return write_install_scripts(args.directory)
    if args.command == "schedule-task":
        return print_schedule_command()
    if args.command == "offline-check":
        return check_offline()
    if args.command == "check-package":
        return run_deployed_check(args.directory)
    return 2


if __name__ == "__main__":
    sys.exit(main())
