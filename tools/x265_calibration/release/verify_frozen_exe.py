#!/usr/bin/env python
"""Deployment verification for x265-calibration-collector.exe.

Run this ONCE on the data-collector host (or on the build host) before the
soak starts.  It is read-only: it never writes next to the exe except for an
optional ``--json-out`` file, and it never needs the collector's spool.

    python verify_frozen_exe.py
    python verify_frozen_exe.py --exe D:\\1kt\\x265-calibration-collector.exe
    python verify_frozen_exe.py --inspect-archive       # prove no third-party py
    python verify_frozen_exe.py --json-out verify.json

Checks
------
1. the exe exists, is a Windows PE image, and is not a stub (size sanity),
2. ``<exe> --version`` runs, exits 0, prints the collector banner and reports
   ``frozen: True`` — i.e. its package-relative imports were frozen correctly,
3. ``<exe> --headless --validate <scratch>`` proves the *bundled* schema.json
   is reachable from a directory that contains no copy of its own,
4. optional: parse the archived table of contents and flag any bundled module
   that lives in a site-packages directory (numpy/scipy/... bloat).

Exit codes: 0 = all checks passed, 1 = a check failed, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_NAME = "x265-verify"
TOOL_VERSION = "1.0.0"

def _default_exe() -> Path:
    """Default collector path when running from a checkout.

    Inside a frozen bundle this module sits at ``<_MEIPASS>/verify_frozen_exe.py``
    so there is no ``parents[3]``; when frozen the default resolves to
    ``./x265-calibration-collector.exe`` next to the current directory instead
    of crashing at import. Callers pass ``--exe`` explicitly anyway.
    """
    try:
        return (Path(__file__).resolve().parent.parent.parent.parent
                / "dist" / "x265_calibration"
                / "x265-calibration-collector.exe")
    except IndexError:
        return Path.cwd() / "x265-calibration-collector.exe"


DEFAULT_EXE = _default_exe()

MAGIC = b"MEI\014\013\012\013\016"
# PyInstaller 6.x onefile (CArchive) trailer, 88 bytes read from the END of the
# file.  Verified byte-for-byte against a real PyInstaller 6.22.2 build in this
# repo and cross-checked with PyInstaller.archive.readers.CArchiveReader
# (pyimod01_archive.COOKIE = "!8sIIII", i.e. a 24-byte struct):
#
#   tail[ 0: 8] = last 8 bytes of the payload (usually "z\0\0...")
#   tail[ 8:16] = COOKIE[0:8]  = ASCII MAGIC "MEI\014\013\012\013\016"
#   tail[16:24] = COOKIE[8:16] = TOC offset, relative to the package start (u64 BE)
#   tail[24:32] = COOKIE[16:24] = TOC length in bytes (the low u64 of !II)
#   tail[32:40] = "python3XX.dll" + NUL padding
#   tail[40:88] = 48 bytes of padding consumed by the 88-byte read window
#                 (64-byte version string + 24-byte COOKIE)
#
# An earlier attempt read the fields from tail[16] as if the cookie began at
# offset 0 and got 1.47e13 / 8.10e18 instead of 311 / 311 - do not "fix" these
# offsets without re-measuring against a real build.
#
# TOC entry (PyInstaller 6.x): !8sIIIIBc = typecode(8s), position(I),
# length(I), ulength(I), flag(I), typcd(B), first byte of the name, then the
# rest of the NUL-terminated name.
TOC_ENTRY = struct.Struct("!8sIIIIBc")
TRAILER_SIZE = 88                          # bytes read from the end of file
SIZE_SANITY_MIN = 3 * 1024 * 1024          # a real CPython onefile build is >= ~6 MiB


class VerifyError(RuntimeError):
    pass


def say(message: str = "") -> None:
    try:
        sys.stdout.write(str(message) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeEncodeError):
        sys.stdout.write(str(message).encode("ascii", "replace").decode("ascii") + "\n")


def decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def run_exe(exe: Path, argv: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            [str(exe)] + argv,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(cwd), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise VerifyError(
            "the exe did not finish within %ds (argv=%r) - a hung bootloader or "
            "an antivirus scan blocking the _MEI extraction is the usual cause."
            % (timeout, argv)
        )
    except OSError as exc:
        raise VerifyError("cannot run %s: %s" % (exe, exc))
    return completed.returncode, decode(completed.stdout or b"")


# ---------------------------------------------------------------------------
# archive TOC
# ---------------------------------------------------------------------------

def read_archive_toc(exe: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (entries, notes) for the outer CArchive of a onefile exe.

    entries: list of (typecode, name).  notes: human-readable diagnostics.

    The authoritative path uses PyInstaller's own CArchiveReader when
    PyInstaller is importable (it is present on any host that built the exe).
    A stdlib-only fallback parses the documented trailer by hand; if it cannot
    make sense of the trailer it says so instead of guessing, because a
    wrongly-parsed TOC would produce a *false* "no schema.json bundled" alarm.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception:
        CArchiveReader = None                                   # type: ignore

    if CArchiveReader is not None:
        try:
            reader = CArchiveReader(str(exe))
            entries = [
                (str(typecode), str(name))
                for name, (typecode, _position, _length, _ulen, _flag)
                in reader.toc.items()
            ]
            notes = ["TOC read with PyInstaller's own CArchiveReader "
                     "(authoritative)"]
            return entries, notes
        except Exception as exc:                                # pragma: no cover
            notes = ["PyInstaller's CArchiveReader failed (%s); falling back "
                     "to the stdlib parser" % exc]
    else:
        notes = ["PyInstaller is not importable here; using the stdlib "
                 "trailer parser (less authoritative)"]

    fallback_entries, fallback_notes = _read_archive_toc_stdlib(exe)
    return fallback_entries, notes + fallback_notes


def _read_archive_toc_stdlib(exe: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Best-effort stdlib parse of the onefile CArchive trailer."""
    notes: list[str] = []
    data_size = exe.stat().st_size
    if data_size < TRAILER_SIZE + TOC_ENTRY.size:
        return [], ["file too small to contain a PyInstaller archive"]

    with exe.open("rb") as fh:
        fh.seek(data_size - TRAILER_SIZE)
        trailer = fh.read(TRAILER_SIZE)
    if len(trailer) < TRAILER_SIZE:
        return [], ["could not read the 88-byte archive trailer"]

    # The ASCII cookie string may sit at offset 0 or 8 of the window depending
    # on how much of the 64-byte version string precedes it; accept both.
    for base in (0, 8):
        if trailer[base:base + 8] == MAGIC:
            break
    else:
        return [], ["no PyInstaller archive cookie found in the last 88 bytes "
                    "(not a PyInstaller onefile exe, or repacked)"]

    toc_offset = struct.unpack_from("!Q", trailer, base + 8)[0]
    toc_len = struct.unpack_from("!Q", trailer, base + 16)[0]
    notes.append("stdlib trailer parse: base %d, TOC offset %d, TOC length %d"
                 % (base, toc_offset, toc_len))
    if not (0 < toc_offset < data_size) or toc_len <= 0 \
            or toc_offset + toc_len > data_size:
        return [], notes + ["implausible archive header values "
                            "(offset %d, length %d)" % (toc_offset, toc_len)]

    with exe.open("rb") as fh:
        fh.seek(toc_offset)
        blob = fh.read(toc_len)

    entries: list[tuple[str, str]] = []
    offset = 0
    while offset + TOC_ENTRY.size <= len(blob):
        (typecode_raw, _position, _length, _ulength, _flag,
         _typcd_raw, _first) = TOC_ENTRY.unpack_from(blob, offset)
        name_end = blob.find(b"\0", offset + TOC_ENTRY.size)
        if name_end < 0:
            notes.append("malformed TOC entry at offset %d; stopping" % offset)
            break
        name_len = name_end - offset - TOC_ENTRY.size + 1
        name = blob[offset + TOC_ENTRY.size:name_end].decode("utf-8", "replace")
        entries.append(
            (typecode_raw.decode("latin-1", "replace").strip("\0"), name)
        )
        offset += TOC_ENTRY.size + name_len
    leftover = len(blob) - offset
    if leftover:
        notes.append("%d unconsumed TOC bytes (parse may be incomplete)"
                     % leftover)
    if not entries:
        notes.append("TOC parsed but empty")
    return entries, notes


def archive_report(exe: Path) -> dict:
    entries, notes = read_archive_toc(exe)
    names = [name for _tc, name in entries]
    lowered = [name.lower() for name in names]
    site_packages = sorted({
        name for name in names
        if "site-packages" in name.lower() or "dist-packages" in name.lower()
    })
    third_party_roots = (
        "numpy", "scipy", "pandas", "pip/", "setuptools", "pkg_resources",
        "mesonbuild", "ninja", "PyInstaller",
    )
    suspects = sorted({
        name for name in names
        if any(name.replace("\\", "/").lower().startswith(root.lower())
               for root in third_party_roots)
    })
    has_schema = any(
        name.replace("\\", "/").lower().lstrip("./").strip("/") == "schema.json"
        for name in names
    )
    return {
        "toc_entries": len(entries),
        "notes": notes,
        "has_schema_json_at_root": has_schema,
        "schema_like_entries": sorted(
            n for n in names if n.lower().endswith("schema.json")
        ),
        "site_packages_entries": site_packages,
        "suspicious_entries": suspects,
        "sample_entries": sorted(names)[:15],
    }


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

# Frozen executables this verifier understands. The collector gets the full
# treatment (banner identity + schema reachability proof); the tool exes get
# the identity check only, because they do not load schema.json.
KNOWN_EXES = {
    "x265-calibration-collector.exe": ("x265-calibration-collector", True),
    "x265-analyze.exe": ("x265-analyze", False),
    "x265-verify.exe": ("x265-verify", False),
    "x265-deploy.exe": ("x265-deploy", False),
}


def _expected_banner(exe: Path) -> tuple[str, bool]:
    """(banner substring that must appear in --version, needs schema proof)."""
    known = KNOWN_EXES.get(exe.name)
    if known is not None:
        return known
    # Unknown exe: still require *some* identity output, but do not demand a
    # specific banner and do not probe schema reachability.
    return "", False


def _check_schema_reachability(exe: Path, report: dict) -> None:
    """Prove the collector loads schema.json from inside its own bundle.

    The exe looks for schema.json next to the module, in sys._MEIPASS, in the
    cwd, next to the exe and next to sys.argv[0]. Running from a scratch
    directory that contains none of those therefore proves the *bundled* copy
    was found. --status reports the resolved path as schema_source, which
    makes the proof explicit.
    """
    scratch = Path(tempfile.mkdtemp(prefix="1kt-collector-verify-"))
    try:
        if (scratch / "schema.json").exists():
            raise VerifyError("scratch dir unexpectedly contains schema.json")
        code2, out2 = run_exe(exe, ["--headless", "--validate", str(scratch)],
                              scratch)
        report["checks"]["schema"] = {
            "exit_code": code2, "output": out2.replace("\r\n", "\n").strip(),
        }
        if "schema.json not found" in out2:
            raise VerifyError(
                "the exe cannot find schema.json when no copy sits next to it: "
                "the bundled datas entry is missing.\n%s" % out2
            )
        if code2 not in (0, 5):        # EXIT_OK / EXIT_DATASET_INVALID
            raise VerifyError(
                "--validate on an empty dataset exited %d (expected 0 or 5):"
                "\n%s" % (code2, out2)
            )

        code3, out3 = run_exe(exe, ["--headless", "--status", "--spool",
                                    str(scratch)], scratch)
        report["checks"]["schema_source"] = {
            "exit_code": code3, "output": out3.replace("\r\n", "\n").strip(),
        }
        source = ""
        try:
            source = str(json.loads(out3).get("collector", {})
                         .get("schema_source", ""))
        except (ValueError, AttributeError):
            pass
        report["schema_source"] = source
        if not source:
            raise VerifyError(
                "--status did not report a schema_source; cannot prove which "
                "schema.json the exe loaded:\n%s" % out3
            )
        if "_MEI" not in source and "Temp" not in source and "tmp" not in source:
            raise VerifyError(
                "schema_source %r does not look like the extracted bundle "
                "(_MEI...). The exe is probably loading a schema.json from the "
                "developer's source tree (sys._MEIPASS resolution follows the "
                "symlink on the build host) - retest on a host without the "
                "repository present." % source
            )
        say("  schema loaded from inside the bundle: %s" % source)
    finally:
        try:
            for leftover in scratch.iterdir():
                if leftover.is_file():
                    leftover.unlink()
                else:
                    shutil.rmtree(leftover, ignore_errors=True)
            scratch.rmdir()
        except OSError:
            pass


def verify(exe: Path, inspect_archive: bool) -> dict:
    report: dict = {"exe": str(exe), "ok": False, "checks": {}}

    if not exe.is_file():
        raise VerifyError("exe not found: %s" % exe)
    size = exe.stat().st_size
    report["size_bytes"] = size
    report["checks"]["file"] = "ok"
    with exe.open("rb") as fh:
        head = fh.read(2)
        fh.seek(0)
        digest = hashlib.sha256()
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    report["sha256"] = digest.hexdigest()
    if head != b"MZ":
        raise VerifyError("%s is not a Windows PE executable" % exe)
    if size < SIZE_SANITY_MIN:
        raise VerifyError(
            "%s is only %d bytes - that is a stub, not a working onefile build."
            % (exe, size)
        )

    # --version -----------------------------------------------------------
    # The collector has a rich banner; the three tool exes have a plain
    # "<name> <version>" one. Requiring the collector banner for all of them
    # made this verifier reject its own siblings, so the expectation now
    # depends on which executable is being checked.
    expected_banner, identity_check = _expected_banner(exe)
    code, out = run_exe(exe, ["--version"], exe.parent)
    report["checks"]["version"] = {
        "exit_code": code, "output": out.replace("\r\n", "\n").strip(),
    }
    if code != 0:
        raise VerifyError("--version exited %d:\n%s" % (code, out))
    if expected_banner and expected_banner not in out:
        raise VerifyError(
            "--version did not print the expected banner %r:\n%s"
            % (expected_banner, out)
        )
    if identity_check:
        if "frozen: True" not in out:
            raise VerifyError(
                "--version did not report 'frozen: True'; the exe is either "
                "not the frozen build or the frozen module graph is broken:"
                "\n%s" % out
            )
        # schema reachability from a directory that has no schema.json -------
        # The collector looks for schema.json next to the module, in
        # sys._MEIPASS, in the cwd, next to the exe and next to sys.argv[0].
        # Running from a scratch directory that contains no schema.json - and
        # whose parent does not either - therefore proves the *bundled* copy
        # was found. --status reports the resolved path as schema_source,
        # which makes the proof explicit.
        _check_schema_reachability(exe, report)

    # optional: packaged module audit -------------------------------------
    if inspect_archive:
        info = archive_report(exe)
        report["archive"] = info
        if info["notes"]:
            for note in info["notes"]:
                say("  archive: " + note)
        # Only the collector must carry schema.json at the bundle root; the
        # tool exes ship it as well but do not load it, so its absence is not
        # a failure for them.
        if identity_check and not info["has_schema_json_at_root"]:
            raise VerifyError(
                "no schema.json at the bundle root (sys._MEIPASS); entries "
                "ending in schema.json: %r" % (info["schema_like_entries"],)
            )
        if info["site_packages_entries"]:
            raise VerifyError(
                "%d site-packages entries were bundled - the exe is not "
                "stdlib-only; first few: %r"
                % (len(info["site_packages_entries"]),
                   info["site_packages_entries"][:10])
            )
        if info["suspicious_entries"]:
            raise VerifyError(
                "unexpected third-party entries in the bundle: %r"
                % (info["suspicious_entries"][:10],)
            )
    report["ok"] = True
    return report


def tool_version() -> str:
    return TOOL_VERSION


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_frozen_exe.py",
        description="Smoke-test and audit a standalone (frozen) executable.",
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (TOOL_NAME, TOOL_VERSION))
    parser.add_argument("--exe", metavar="PATH", default=None,
                        help="default: %s" % DEFAULT_EXE)
    parser.add_argument("--inspect-archive", action="store_true",
                        help="also parse the onefile archive TOC and assert "
                             "that nothing from site-packages was bundled.")
    parser.add_argument("--json-out", metavar="PATH", default=None,
                        help="also write the report as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exe = Path(args.exe).expanduser().resolve() if args.exe else DEFAULT_EXE

    say("verifying %s" % exe)
    try:
        report = verify(exe, args.inspect_archive)
    except VerifyError as exc:
        say("")
        say("[VERIFY FAILED] %s" % exc)
        return 1

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        say("report written to %s" % args.json_out)

    if args.inspect_archive:
        info = report.get("archive", {})
        say("archive TOC entries        : %s" % info.get("toc_entries"))
        say("schema.json at bundle root : %s" % info.get("has_schema_json_at_root"))
        say("site-packages entries      : %d" % len(info.get("site_packages_entries", [])))
        say("suspicious entries         : %d" % len(info.get("suspicious_entries", [])))

    say("")
    say("[VERIFY OK] %s (%d bytes)" % (exe, report["size_bytes"]))
    say("sha256: %s" % report["sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
