"""Release package integrity verifier (task book §6/§7/§8).

    python release/verify_package.py dist/1KeyTranscoder-v<version>-win64-selfcontained.zip \
        --extract-dir "%TEMP%\\1KT-v<version>-clean"

Steps:
  1. archive exists, size > 0, sidecar SHA256 present and matching
  2. release-manifest.json present, its `archive` block matches the file
  3. extract into an independent directory (never the repo root)
  4. required entries present; forbidden development artefacts absent
  5. every file in the manifest reconciles against the extracted tree
     (size + sha256), and the extracted tree has no extra files
  6. spot-check the manifest's runtime versions against the extracted
     binaries actually running

Exit code 0 = PASS, 1 = FAIL, 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REQUIRED_ENTRIES = (
    "1kt.py",
    "VERSION",
    "README.md",
    "LICENSE",
    "NOTICE",
    "licenses/GPL-3.0.txt",
    "release-manifest.json",
    "core/version.py",
    "encoders",
    "preservation",
    "tools/ffmpeg.exe",
    "tools/ffprobe.exe",
    "tools/NVEncC_9.31_x64/NVEncC64.exe",
    "tools/QSVEncC_8.26_x64/QSVEncC64.exe",
    "tools/GPAC/mp4box.exe",
    "x265.json",
    "nvenc.json",
    "nvenc_av1.json",
    "qsv_av1.json",
    "svtav1.json",
    "svtav1_scaling.json",
    "x265_scaling.json",
)

# task book §7: development artefacts must not ship
FORBIDDEN_PREFIXES = (
    ".git",
    "work",
    "testsets",
    "olddocs",
    "docs",
    "logs",
    "metadata_forensics",
    ".dsh-drop",
    "tools/VCEEncC_9.12_x64",
    "dist",
    "release",
)
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")
FORBIDDEN_PARTS = ("__pycache__",)

# §8: items that must reconcile hash-for-hash
KEY_FILES = (
    "1kt.py",
    "tools/ffmpeg.exe",
    "tools/ffprobe.exe",
    "tools/NVEncC_9.31_x64/NVEncC64.exe",
    "tools/QSVEncC_8.26_x64/QSVEncC64.exe",
    "tools/GPAC/mp4box.exe",
    "x265.json",
    "nvenc.json",
    "nvenc_av1.json",
    "qsv_av1.json",
    "svtav1.json",
    "svtav1_scaling.json",
    "x265_scaling.json",
)

CHUNK = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f": {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.rows if not r[1]]


def probe_version(exe: Path, args: tuple[str, ...]) -> str:
    if not exe.is_file():
        return "MISSING"
    try:
        proc = subprocess.run(
            [str(exe), *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"PROBE-FAILED ({type(exc).__name__})"
    for line in (proc.stdout or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return "NO-OUTPUT"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archive", help="Path to the release .zip")
    ap.add_argument("--extract-dir", default=None,
                    help="Clean directory to extract into (must be outside "
                         "the repository root).")
    ap.add_argument("--keep-extracted", action="store_true",
                    help="Do not delete an existing extract dir first.")
    args = ap.parse_args(argv)

    rep = Report()
    archive = Path(args.archive).resolve()
    print("=" * 72)
    print(f"release package verification: {archive}")
    print("=" * 72)

    # ---- 1. archive + sidecar -----------------------------------------
    if not rep.check("archive exists", archive.is_file(), str(archive)):
        return 1
    size = archive.stat().st_size
    rep.check("archive size > 0", size > 0, f"{size:,} B")
    sidecar = archive.with_name(archive.name + ".sha256")
    rep.check("sha256 sidecar exists", sidecar.is_file(), sidecar.name)
    actual_sha = sha256_file(archive)
    if sidecar.is_file():
        want = sidecar.read_text(encoding="utf-8").split()[0].strip()
        rep.check("sha256 sidecar matches archive", want == actual_sha,
                  actual_sha if want == actual_sha
                  else f"sidecar={want[:16]}… actual={actual_sha[:16]}…")

    # ---- 2. outer manifest --------------------------------------------
    outer_path = archive.parent / "release-manifest.json"
    if not rep.check("outer release-manifest.json exists",
                     outer_path.is_file(), str(outer_path)):
        return 1
    outer = json.loads(outer_path.read_text(encoding="utf-8"))
    blk = outer.get("archive") or {}
    rep.check("manifest archive.sha256 matches file",
              blk.get("sha256") == actual_sha,
              f"manifest={str(blk.get('sha256'))[:16]}…")
    rep.check("manifest archive.size matches file",
              blk.get("size") == size,
              f"manifest={blk.get('size')} actual={size}")
    # The expected version is whatever this checkout's VERSION file says —
    # the verifier must never be pinned to one release (a hard-coded 0.6.0
    # here would false-FAIL every later package).
    expected_version = (Path(__file__).resolve().parent.parent / "VERSION")
    want_version = (expected_version.read_text(encoding="utf-8").strip()
                    if expected_version.is_file() else "")
    version = outer.get("version", "")
    rep.check("manifest version == VERSION file",
              bool(want_version) and version == want_version,
              f"manifest={version!r} VERSION={want_version!r}")
    commit = outer.get("git_commit", "")
    rep.check("manifest git_commit present", bool(commit), commit[:12])
    rep.check("manifest dirty flag false", outer.get("dirty") is False,
              f"dirty={outer.get('dirty')}")

    # ---- 3. extract ----------------------------------------------------
    if args.extract_dir:
        extract_root = Path(args.extract_dir).expanduser().resolve()
    else:
        extract_root = Path(archive.parent / "_extract").resolve()
    if not args.keep_extracted and extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        rep.check("zip archive readable (CRC)", bad is None, bad or "all OK")
        zf.extractall(extract_root)
    pkg_name = outer.get("package") or archive.stem
    pkg = extract_root / pkg_name
    rep.check("extracted package root", pkg.is_dir(), str(pkg))

    # ---- 4. required / forbidden entries -------------------------------
    for rel in REQUIRED_ENTRIES:
        rep.check(f"required: {rel}", (pkg / rel).exists())
    offenders = []
    for path in pkg.rglob("*"):
        rel = path.relative_to(pkg).as_posix()
        if any(rel == p or rel.startswith(p + "/") for p in FORBIDDEN_PREFIXES):
            offenders.append(rel)
        elif path.suffix.lower() in FORBIDDEN_SUFFIXES:
            offenders.append(rel)
        elif any(part in FORBIDDEN_PARTS for part in path.relative_to(pkg).parts):
            offenders.append(rel)
    rep.check("no development artefacts packaged", not offenders,
              "clean" if not offenders else f"{len(offenders)}: {offenders[:5]}")

    # ---- 5. file-by-file reconciliation --------------------------------
    inner_path = pkg / "release-manifest.json"
    inner = json.loads(inner_path.read_text(encoding="utf-8"))
    records = inner.get("files") or []
    rep.check("inner manifest file records > 0", len(records) > 0,
              f"{len(records)} records")
    missing, mismatch = [], []
    for rec in records:
        target = pkg / rec["path"]
        if not target.is_file():
            missing.append(rec["path"])
            continue
        if target.stat().st_size != rec["size"]:
            mismatch.append(f"{rec['path']} size")
            continue
        if sha256_file(target) != rec["sha256"]:
            mismatch.append(f"{rec['path']} sha256")
    rep.check("all manifest files present", not missing,
              "all present" if not missing else f"{len(missing)}: {missing[:5]}")
    rep.check("all manifest hashes match", not mismatch,
              "all match" if not mismatch else f"{len(mismatch)}: {mismatch[:5]}")

    on_disk = {
        p.relative_to(pkg).as_posix()
        for p in pkg.rglob("*") if p.is_file()
    } - {"release-manifest.json"}
    extra = sorted(on_disk - {r["path"] for r in records})
    rep.check("no unlisted files in package", not extra,
              "clean" if not extra else f"{len(extra)}: {extra[:5]}")

    for rel in KEY_FILES:
        target = pkg / rel
        rec = next((r for r in records if r["path"] == rel), None)
        ok = bool(rec) and target.is_file() and \
            sha256_file(target) == rec["sha256"]
        rep.check(f"key file reconciles: {rel}", ok,
                  f"sha256={rec['sha256'][:16]}…" if ok else "MISMATCH")

    # ---- 6. runtime version spot-check ---------------------------------
    probes = (
        ("ffmpeg", "tools/ffmpeg.exe", ("-version",)),
        ("NVEncC", "tools/NVEncC_9.31_x64/NVEncC64.exe", ("--version",)),
        ("QSVEncC", "tools/QSVEncC_8.26_x64/QSVEncC64.exe", ("--version",)),
        ("GPAC", "tools/GPAC/mp4box.exe", ("-version",)),
    )
    for name, rel, pargs in probes:
        want = (inner.get("runtime_versions") or {}).get(name, "")
        got = probe_version(pkg / rel, pargs)
        rep.check(f"runtime version reproducible: {name}",
                  bool(want) and want == got,
                  got if want == got else f"manifest={want[:60]!r} ran={got[:60]!r}")

    print("")
    print("-" * 72)
    total = len(rep.rows)
    bad = rep.failed
    print(f"RESULT: {total - len(bad)}/{total} checks PASS"
          + ("" if not bad else f", {len(bad)} FAIL"))
    print(f"package : {pkg}")
    print(f"sha256  : {actual_sha}")
    if bad:
        print("\nFAILURES:")
        for name, _, detail in bad:
            print(f"  {name}: {detail}")
        print("\nRelease = BLOCKED")
        return 1
    print("\nPackage integrity = PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
