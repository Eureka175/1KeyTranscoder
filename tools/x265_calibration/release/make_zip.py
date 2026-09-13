"""Build the deployable ZIP for the x265 calibration collector.

    python tools/x265_calibration/release/make_zip.py [--clean] [--out PATH]

Output (default):
    dist/x265-calibration-collector-v0.7.0.zip

The ZIP extracts into ONE self-contained folder that runs on a Windows host
with no Python, no pip and no network:

    x265-calibration-collector/
      QUICKSTART.zh-CN.txt          entry point (Chinese quick start)
      README.txt                    full English deployment document
      x265-calibration-collector.exe
      START-COLLECTOR.bat           start manually
      CHECK-INSTALL.bat             read-only self test
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
      tools/analyze.py              dev-machine analyzer (not run on the host)
      tools/verify_frozen_exe.py    dev-machine frozen-exe verifier
      tools/deploy_helpers.py       dev-machine packaging helper
      README-DEV.txt                says the three tools/ files are dev-side

Standard library only, deterministic content, safe to re-run.
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
EXE_SRC = PROJECT_ROOT / "dist" / "x265_calibration" / (
    "x265-calibration-collector.exe"
)
PKG_NAME = "x265-calibration-collector"

# (source path, path inside the zip)
FLAT_FILES: tuple[tuple[Path, str], ...] = ()
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
These three files are DEVELOPMENT-MACHINE tools. Do not use them on this host.

  analyze.py           offline analyzer for the collected dataset. Reads
                       samples/*.jsonl and prints a summary (sources, scaling,
                       resolved parameters, dynamic VBV, production rates).
                       Read-only: it never writes x265.json or
                       x265_scaling.json.

  verify_frozen_exe.py checks that the .exe is genuinely self-contained
                       (schema bundled, no site-packages inside).

  deploy_helpers.py    packaging / deployment helper (build the release folder,
                       print the schtasks command, static offline check).

All three need a Python 3.8+ interpreter, which this soak host is not required
to have. Run them on the development machine after copying the dataset back.
"""

ROOT_POINTER = """\
Extract this whole folder before doing anything.

If you read Chinese, start here:      QUICKSTART.zh-CN.txt
Otherwise start here:                 README.txt

Three-step summary
------------------
1. Extract the whole folder somewhere, e.g. D:\\1kt\\collector\\
2. Set these two SYSTEM environment variables for the account that runs your
   PRODUCTION transcoder (paths must match where you extracted):

       ONEXKT_CALIBRATION       = 1
       ONEXKT_CALIBRATION_SPOOL = D:\\1kt\\collector\\calibration

3. Double-click START-COLLECTOR.bat   (or INSTALL-SCHEDULED-TASK.bat)

Then double-click CHECK-INSTALL.bat to confirm it is working.
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
        "INSTALL-SCHEDULED-TASK.bat",
        "UNINSTALL-SCHEDULED-TASK.bat",
    ):
        add(_here / name, f"{PKG_NAME}/{name}")

    add(PROJECT_ROOT / "VERSION", f"{PKG_NAME}/VERSION")
    add(_cal / "analyze.py", f"{PKG_NAME}/tools/analyze.py")
    add(_here / "verify_frozen_exe.py", f"{PKG_NAME}/tools/verify_frozen_exe.py")
    add(_here / "deploy_helpers.py", f"{PKG_NAME}/tools/deploy_helpers.py")
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
    (pkg / "tools" / "README-DEV.txt").write_text(
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
    lines.append(f"executable bytes : {exe_size:,}")
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
    print(f"  entries      : {len(names)}")
    print(f"  integrity    : {'OK' if bad is None else 'CORRUPT at ' + str(bad)}")
    print(f"  exe in zip   : {copied_hash == exe_hash} "
          f"({'identical' if copied_hash == exe_hash else 'MISMATCH'})")
    for required in (
        f"{PKG_NAME}/QUICKSTART.zh-CN.txt",
        f"{PKG_NAME}/START-COLLECTOR.bat",
        f"{PKG_NAME}/CHECK-INSTALL.bat",
        f"{PKG_NAME}/INSTALL-SCHEDULED-TASK.bat",
        f"{PKG_NAME}/schema.json",
        f"{PKG_NAME}/PACKAGE-CONTENTS.txt",
        f"{PKG_NAME}/calibration/events/pending/README_FIRST.txt",
    ):
        print(f"  {'present' if required in names else 'MISSING':<8}: {required}")

    if not args.keep_stage:
        shutil.rmtree(STAGE_ROOT, ignore_errors=True)

    ok = bad is None and copied_hash == exe_hash
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
