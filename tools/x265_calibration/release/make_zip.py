"""Build the deployable ZIP for the x265 calibration collector.

    python tools/x265_calibration/release/make_zip.py [--clean] [--out PATH]

Output (default):
    dist/x265-calibration-collector-v0.7.0.zip

The ZIP extracts into ONE self-contained folder that needs NO Python at all -
not for collecting, not for analysis, not for verification:

    x265-calibration-collector/
      START-HERE.txt                one-paragraph pointer
      QUICKSTART.zh-CN.txt          Chinese quick start (entry point)
      README.txt                    full English deployment document
      x265-calibration-collector.exe
      START-COLLECTOR.bat           start it (foreground)
      CHECK-INSTALL.bat             read-only self test
      ANALYZE-DATA.bat              analyze the dataset -> analysis-report.txt
      VERIFY-PACKAGE.bat            prove every exe is self-contained
      INSTALL-SCHEDULED-TASK.bat    register at-boot task
      UNINSTALL-SCHEDULED-TASK.bat  deregister
      schema.json  collector.toml  VERSION
      PACKAGE-CONTENTS.txt          manifest with sizes + SHA-256
      SHA256SUMS.txt                checksums only (easy to diff)
      calibration/events/pending/   data directory, pre-created (empty)
      calibration/events/archive/
      calibration/events/failed/
      calibration/samples/
      calibration/summaries/
      calibration/errors/
      calibration/state/
      calibration/README_FIRST.txt  explains the data layout
      calibration/events/pending/README_FIRST.txt
                                    explains that files here are producer input
      tools/
        x265-analyze.exe            offline dataset analyzer
        x265-verify.exe             frozen-exe / package verifier
        x265-deploy.exe             deployment helper
        README.txt                  what the tool exes are for

Standard library only on the build side, deterministic content, safe to
re-run.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RELEASE_DIR = PROJECT_ROOT / "dist" / "x265_release"
STAGE_ROOT = RELEASE_DIR / "_stage"
DIST_DIR = PROJECT_ROOT / "dist" / "x265_calibration"
EXE_SRC = DIST_DIR / "x265-calibration-collector.exe"
TOOL_EXES = (
    "x265-analyze.exe",
    "x265-verify.exe",
    "x265-deploy.exe",
)
PKG_NAME = "x265-calibration-collector"

_here = Path(__file__).resolve().parent
_cal = _here.parent

# ---------------------------------------------------------------- data dir --
CALIB_DIRS = (
    "events/pending",
    "events/archive",
    "events/failed",
    "samples",
    "summaries",
    "errors",
    "state",
)

CALIB_README = """\
This is the calibration DATA directory. The collector owns it.

Layout
------
events/pending/     producer (your transcoder) drops event files here
events/archive/     events already consumed, kept verbatim for auditing
events/failed/      events that failed validation, plus a .error.txt saying why
samples/            THE DATASET. One file per day: YYYY-MM-DD.jsonl,
                    one immutable JSON record per encode attempt
summaries/          per-day rollups. Always rebuildable from samples/
errors/             collector.log and rejected_events.jsonl
state/              collector bookkeeping (offsets, buffers, lock file).
                    Do not edit. Safe to keep; only delete when stopped.

What to bring back after the soak
---------------------------------
samples/            (this is the data you actually need)
events/archive/     optional, for a full audit trail
errors/             optional, to see what went wrong along the way

Everything here is plain UTF-8 text. Roughly 3-6 KB per event and per record,
so a month of 5000 encodes is tens of MB, not gigabytes.

Do NOT delete this directory while the collector is running.
"""

PENDING_README = """\
The PRODUCTION transcoder writes its event files into this directory.

If you are reading this, it means no events have arrived yet. The two things
that must be true for events to appear:

  1. The account running the PRODUCTION transcoder has these two environment
     variables set to the calibration directory's ABSOLUTE path:

         ONEXKT_CALIBRATION=1
         ONEXKT_CALIBRATION_SPOOL=<...>\\calibration

  2. The collector is enabled in that build. With ONEXKT_CALIBRATION unset the
     transcoder does nothing extra at all (the emitter is a no-op).

Files you drop in here by hand are ignored, never deleted, never quarantined
(only *.json / *.jsonl files are treated as events). This file is here on
purpose so the folder is not empty and obvious.

While the collector is stopped, events pile up here and are consumed when it
starts again. Nothing is lost.
"""

DEV_README = """\
These are standalone executables. Python is NOT needed - not here, not
anywhere. Each one carries its own runtime.

  x265-analyze.exe   offline analyzer for the collected dataset. Reads
                     calibration\\samples\\*.jsonl and prints a summary
                     (sources, scaling, resolved parameters, dynamic VBV,
                     production rates). Read-only: it never writes x265.json
                     or x265_scaling.json and never touches the dataset.

                       x265-analyze.exe ..\\calibration
                       x265-analyze.exe ..\\calibration --json report.json

                     Or just double-click ANALYZE-DATA.bat in the parent
                     folder, which does the same thing and saves the output.

  x265-verify.exe    verifies that an executable is genuinely self-contained
                     (schema bundled inside, no site-packages dependency).

                       x265-verify.exe --exe ..\\x265-calibration-collector.exe

                     Or double-click VERIFY-PACKAGE.bat in the parent folder
                     to check every exe in this package at once.

  x265-deploy.exe    deployment helper: prints the exact schtasks command,
                     runs a static offline/no-network check, and can rebuild
                     the release folder.
                       x265-deploy.exe --help

The .bat files in the parent folder are convenience wrappers around these and
take no arguments, which is the intended way to use them.
"""

ROOT_POINTER = """\
Extract this whole folder before doing anything.

If you read Chinese, start here:      QUICKSTART.zh-CN.txt
Otherwise start here:                 README.txt

Python is NOT required. Every program in this folder is standalone.

Three-step summary
------------------
1. Extract the whole folder somewhere, e.g. D:\\1kt\\collector\\
2. Set these two SYSTEM environment variables for the account that runs your
   PRODUCTION transcoder (paths must match where you extracted):

       ONEXKT_CALIBRATION       = 1
       ONEXKT_CALIBRATION_SPOOL = D:\\1kt\\collector\\calibration

3. Double-click START-COLLECTOR.bat   (or INSTALL-SCHEDULED-TASK.bat)

Then double-click CHECK-INSTALL.bat to confirm it is working.
After the soak, double-click ANALYZE-DATA.bat to get the summary.
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def build_sources() -> list[tuple[Path, str]]:
    """(source on disk, destination path inside the zip)."""
    pairs: list[tuple[Path, str]] = []

    def add(source: Path, dest: str, required: bool = True) -> None:
        if not source.is_file():
            if required:
                raise SystemExit(f"[FAIL] missing package input: {source}")
            print(f"[WARN] optional input not found, skipped: {source}")
            return
        pairs.append((source, dest))

    add(EXE_SRC, f"{PKG_NAME}/x265-calibration-collector.exe")
    add(_cal / "release" / "QUICKSTART.zh-CN.txt",
        f"{PKG_NAME}/QUICKSTART.zh-CN.txt")
    add(_cal / "README.txt", f"{PKG_NAME}/README.txt")
    add(_cal / "schema.json", f"{PKG_NAME}/schema.json")
    add(_cal / "collector.toml", f"{PKG_NAME}/collector.toml")

    for name in (
        "START-COLLECTOR.bat",
        "CHECK-INSTALL.bat",
        "ANALYZE-DATA.bat",
        "VERIFY-PACKAGE.bat",
        "INSTALL-SCHEDULED-TASK.bat",
        "UNINSTALL-SCHEDULED-TASK.bat",
    ):
        add(_here / name, f"{PKG_NAME}/{name}")

    add(PROJECT_ROOT / "VERSION", f"{PKG_NAME}/VERSION")

    # The portable tool executables: the soak host has no Python, so nothing
    # in this package may depend on an interpreter.
    for name in TOOL_EXES:
        add(DIST_DIR / name, f"{PKG_NAME}/tools/{name}")
    return pairs


def stage_package(pairs: list[tuple[Path, str]], version: str,
                  verbose: bool = True) -> Path:
    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    pkg = STAGE_ROOT / PKG_NAME
    pkg.mkdir(parents=True)

    for source, dest in pairs:
        target = STAGE_ROOT / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # data directory skeleton, pre-created so step 2 of the quick start can
    # simply be pointed at it
    for sub in CALIB_DIRS:
        (pkg / "calibration" / sub).mkdir(parents=True, exist_ok=True)
    (pkg / "calibration" / "README_FIRST.txt").write_text(
        CALIB_README, encoding="utf-8", newline="\r\n"
    )
    (pkg / "calibration" / "events" / "pending" / "README_FIRST.txt").write_text(
        PENDING_README, encoding="utf-8", newline="\r\n"
    )
    (pkg / "tools" / "README.txt").write_text(
        DEV_README, encoding="utf-8", newline="\r\n"
    )
    (pkg / "START-HERE.txt").write_text(
        ROOT_POINTER, encoding="utf-8", newline="\r\n"
    )

    # manifest + checksums over everything currently staged
    entries: list[tuple[str, int, str]] = []
    for path in sorted(pkg.rglob("*")):
        if path.is_file():
            rel = path.relative_to(pkg).as_posix()
            entries.append((rel, path.stat().st_size, sha256_file(path)))

    exe_size = next(
        (size for rel, size, _h in entries
         if rel == "x265-calibration-collector.exe"), 0
    )
    tool_bytes = sum(
        size for rel, size, _h in entries
        if rel.startswith("tools/") and rel.endswith(".exe")
    )
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("x265 calibration collector - package contents")
    lines.append("=" * 78)
    lines.append(f"package          : {PKG_NAME}")
    lines.append(f"project version  : {version}")
    lines.append(
        f"built (UTC)      : "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    lines.append(f"files            : {len(entries)}")
    lines.append(f"total bytes      : {sum(s for _r, s, _h in entries):,}")
    lines.append(f"collector bytes  : {exe_size:,}")
    lines.append(f"tool exes bytes  : {tool_bytes:,}")
    lines.append("")
    lines.append("python required  : NO - every executable is standalone")
    lines.append("network required : NO - nothing opens a socket")
    lines.append("")
    lines.append("branch           : feature/x265-calibration-collector")
    lines.append("baseline         : v0.7.0 (1a41fff)")
    lines.append("status           : ARCHIVED / READY FOR 1-MONTH SOAK")
    lines.append("")
    lines.append("-" * 78)
    lines.append(f"{'size':>12}  {'sha256':<64}  path")
    lines.append("-" * 78)
    for rel, size, digest in entries:
        lines.append(f"{size:>12,}  {digest}  {rel}")
    lines.append("=" * 78)
    manifest = "\n".join(lines) + "\n"
    (pkg / "PACKAGE-CONTENTS.txt").write_text(
        manifest, encoding="utf-8", newline="\r\n"
    )
    (pkg / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {rel}\n" for rel, _s, digest in entries),
        encoding="utf-8", newline="\r\n",
    )

    if verbose:
        print(f"  staged {len(entries)} files "
              f"({sum(s for _r, s, _h in entries):,} bytes) in {pkg}")
    return pkg


def make_zip(pkg: Path, out: Path) -> Path:
    outer = PKG_NAME  # extracting gives one top-level folder
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    # deterministic: fixed timestamps, sorted order, DEFLATE
    stamp = (2026, 9, 13, 0, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for path in sorted(pkg.rglob("*")):
            rel = path.relative_to(pkg).as_posix()
            arcname = f"{outer}/{rel}"
            if path.is_dir():
                # explicit directory entry (empty dirs must survive the zip)
                info = zipfile.ZipInfo(arcname + "/", date_time=stamp)
                info.external_attr = (0o40755 << 16) | 0x10
                info.compress_type = zipfile.ZIP_STORED
                zf.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(arcname, date_time=stamp)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compresslevel=9)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_zip.py")
    parser.add_argument("--out", type=Path, default=None,
                        help="output zip path")
    parser.add_argument("--clean", action="store_true",
                        help="remove the staging directory afterwards")
    parser.add_argument("--keep-stage", action="store_true",
                        help="keep the staging directory (for inspection)")
    args = parser.parse_args(argv)

    version = project_version()
    default_out = (RELEASE_DIR /
                   f"x265-calibration-collector-v{version}.zip")
    out = args.out or default_out

    print("=" * 78)
    print("building deployable zip")
    print("=" * 78)
    print(f"  project root : {PROJECT_ROOT}")
    print(f"  version      : {version}")
    print(f"  input exe    : {EXE_SRC}")
    if not EXE_SRC.is_file():
        print()
        print("[FAIL] the standalone executable is missing. Build it first:")
        print("       python tools/x265_calibration/release/"
              "build_standalone.py --clean")
        return 1

    pairs = build_sources()
    exe_hash = sha256_file(EXE_SRC)
    pkg = stage_package(pairs, version)
    zip_path = make_zip(pkg, out)

    print(f"  zip          : {zip_path}")
    print(f"  zip size     : {zip_path.stat().st_size:,} bytes "
          f"({zip_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"  exe sha256   : {exe_hash}")
    print()

    # self-check: read the zip back and confirm the entries survived
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        bad = zf.testzip()
        exe_entry = f"{PKG_NAME}/x265-calibration-collector.exe"
        data = zf.read(exe_entry) if exe_entry in names else b""
        copied_hash = hashlib.sha256(data).hexdigest()
        tool_hashes = {}
        for tool in TOOL_EXES:
            entry = f"{PKG_NAME}/tools/{tool}"
            tool_hashes[tool] = (
                hashlib.sha256(zf.read(entry)).hexdigest()
                if entry in names else ""
            )
    print(f"  entries      : {len(names)}")
    print(f"  integrity    : {'OK' if bad is None else 'CORRUPT at ' + str(bad)}")
    print(f"  collector    : {copied_hash == exe_hash} "
          f"({'identical' if copied_hash == exe_hash else 'MISMATCH'})")
    for tool in TOOL_EXES:
        expected = sha256_file(DIST_DIR / tool)
        match = tool_hashes[tool] == expected
        print(f"  {tool:<18}: {'identical' if match else 'MISMATCH'}")
    for required in (
        f"{PKG_NAME}/QUICKSTART.zh-CN.txt",
        f"{PKG_NAME}/START-HERE.txt",
        f"{PKG_NAME}/START-COLLECTOR.bat",
        f"{PKG_NAME}/CHECK-INSTALL.bat",
        f"{PKG_NAME}/ANALYZE-DATA.bat",
        f"{PKG_NAME}/VERIFY-PACKAGE.bat",
        f"{PKG_NAME}/INSTALL-SCHEDULED-TASK.bat",
        f"{PKG_NAME}/UNINSTALL-SCHEDULED-TASK.bat",
        f"{PKG_NAME}/schema.json",
        f"{PKG_NAME}/PACKAGE-CONTENTS.txt",
        f"{PKG_NAME}/calibration/events/pending/README_FIRST.txt",
        f"{PKG_NAME}/tools/README.txt",
    ) + tuple(f"{PKG_NAME}/tools/{tool}" for tool in TOOL_EXES):
        print(f"  {'present' if required in names else 'MISSING':<8}: "
              f"{required.removeprefix(PKG_NAME + '/')}")

    if not args.keep_stage:
        shutil.rmtree(STAGE_ROOT, ignore_errors=True)

    tools_ok = all(
        tool_hashes[tool] == sha256_file(DIST_DIR / tool)
        for tool in TOOL_EXES
    )
    ok = bad is None and copied_hash == exe_hash and tools_ok
    print()
    print("=" * 78)
    print("[ZIP OK]" if ok else "[ZIP FAILED]")
    print("=" * 78)
    if ok:
        print("Copy the zip to the soak host, extract the WHOLE folder, then")
        print("follow QUICKSTART.zh-CN.txt (or README.txt).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
