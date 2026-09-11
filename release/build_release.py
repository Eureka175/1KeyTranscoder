"""1KeyTranscoder official release builder.

Produces a self-contained Windows package plus a `release-manifest.json`
describing every packaged file (size + sha256) and the archive itself.

    python release/build_release.py                # official build
    python release/build_release.py --dry-run      # list, do not write
    python release/build_release.py --no-zip       # staging tree only

Package layout = repository layout minus development artefacts, because
`1kt.py` resolves every runtime tool and profile JSON relative to
`Path(__file__).resolve().parent`:

    1KeyTranscoder-v<version>-win64-selfcontained/
        1kt.py  watchfolder.py  start.bat
        core/  encoders/  preservation/  tests/
        <encoder + scaling profile>.json
        tools/ffmpeg.exe  tools/ffprobe.exe
        tools/NVEncC_9.31_x64/  tools/QSVEncC_8.26_x64/  tools/GPAC/
        README.md  LICENSE  VERSION  release-manifest.json

Deliberately NOT packaged (development-only, see ALLOWLIST below):
`.git/`, `work/`, `testsets/`, `docs/`, `olddocs/`, `logs/`,
`metadata_forensics/`, `tools/VCEEncC_9.12_x64/` (VCE backend is not
wired up), `__pycache__/`.

Safety rules (task book §3): refuses to build when tracked files are
modified, when the working branch is not `main`, or when the VERSION
file disagrees with the `v<version>` git tag. `--allow-dirty` records
the deviation in the manifest instead of failing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import project_version  # noqa: E402

# --- package allowlist -----------------------------------------------------

TOP_FILES = (
    "1kt.py",
    "watchfolder.py",
    "start.bat",
    "README.md",
    "LICENSE",
    "VERSION",
)
TOP_DIRS = ("core", "encoders", "preservation", "tests")
TOP_GLOBS = ("*.json",)
TOOL_FILES = ("tools/ffmpeg.exe", "tools/ffprobe.exe")
TOOL_DIRS = (
    "tools/NVEncC_9.31_x64",
    "tools/QSVEncC_8.26_x64",
    "tools/GPAC",
)
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")

# runtime tools whose versions are recorded in the manifest
VERSION_PROBES = (
    ("ffmpeg", "tools/ffmpeg.exe", ("-version",)),
    ("ffprobe", "tools/ffprobe.exe", ("-version",)),
    ("NVEncC", "tools/NVEncC_9.31_x64/NVEncC64.exe", ("--version",)),
    ("QSVEncC", "tools/QSVEncC_8.26_x64/QSVEncC64.exe", ("--version",)),
    ("GPAC", "tools/GPAC/mp4box.exe", ("-version",)),
)

_COPY_CHUNK = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_COPY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout.strip()


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
        line = line.strip()
        if line:
            return line[:200]
    return "NO-OUTPUT"


def collect_runtime_versions() -> dict[str, str]:
    out = {}
    for name, rel, args in VERSION_PROBES:
        out[name] = probe_version(ROOT / rel, args)
    return out


def wanted_files() -> list[Path]:
    """Every file that belongs in the package, as repo-relative paths."""
    picked: list[Path] = []
    for name in TOP_FILES:
        p = ROOT / name
        if p.is_file():
            picked.append(p)
        else:
            raise RuntimeError(f"required package file missing: {name}")
    for name in TOP_DIRS:
        d = ROOT / name
        if not d.is_dir():
            raise RuntimeError(f"required package directory missing: {name}")
        picked += [f for f in sorted(d.rglob("*")) if f.is_file()]
    for pattern in TOP_GLOBS:
        picked += sorted(ROOT.glob(pattern))
    for rel in TOOL_FILES:
        p = ROOT / rel
        if not p.is_file():
            raise RuntimeError(f"required runtime binary missing: {rel}")
        picked.append(p)
    for rel in TOOL_DIRS:
        d = ROOT / rel
        if not d.is_dir():
            raise RuntimeError(f"required runtime directory missing: {rel}")
        picked += [f for f in sorted(d.rglob("*")) if f.is_file()]

    keep: list[Path] = []
    seen: set[str] = set()
    for path in picked:
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        key = rel.as_posix()
        if key in seen:
            continue
        seen.add(key)
        keep.append(path)
    return keep


def copy_into_staging(files: list[Path], stage: Path) -> int:
    total = 0
    for src in files:
        rel = src.relative_to(ROOT)
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        total += dst.stat().st_size
    return total


def file_records(stage: Path) -> list[dict]:
    records = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "release-manifest.json":
            continue
        rel = path.relative_to(stage).as_posix()
        records.append({
            "path": rel,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return records


def make_zip(stage: Path, archive: Path, log=print) -> None:
    files = [p for p in sorted(stage.rglob("*")) if p.is_file()]
    log(f"  zipping {len(files)} files -> {archive.name}")
    with zipfile.ZipFile(
        archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
    ) as zf:
        for i, path in enumerate(files, 1):
            zf.write(path, path.relative_to(stage.parent).as_posix())
            if i % 200 == 0:
                log(f"    {i}/{len(files)}")


def build(args: argparse.Namespace) -> int:
    started = time.time()
    log = print

    log("=" * 72)
    log("1KeyTranscoder release build")
    log("=" * 72)

    version = args.version or project_version()
    log(f"version          : {version}")

    # ---- §3 git safety -------------------------------------------------
    branch = git("branch", "--show-current")
    commit = git("rev-parse", "HEAD")
    describe = git("describe", "--tags", "--always", "--dirty")
    dirty_lines = [
        ln for ln in git("status", "--porcelain").splitlines()
        if ln.strip() and not ln.startswith("??")
    ]
    untracked = [
        ln[3:].strip() for ln in git("status", "--porcelain").splitlines()
        if ln.startswith("??")
    ]
    log(f"branch           : {branch}")
    log(f"commit           : {commit}")
    log(f"describe         : {describe}")
    log(f"tracked modified : {len(dirty_lines)}")
    for ln in dirty_lines:
        log(f"    {ln}")
    log(f"untracked        : {len(untracked)}")
    for name in untracked:
        log(f"    {name}")

    problems = []
    if branch != "main":
        problems.append(f"branch is {branch!r}, expected 'main'")
    if dirty_lines and not args.allow_dirty:
        problems.append(
            f"{len(dirty_lines)} tracked file(s) modified — commit or stash "
            "first (use --allow-dirty only for development builds)"
        )
    tag = f"v{version}"
    try:
        tag_commit = git("rev-list", "-n", "1", tag)
    except RuntimeError:
        tag_commit = ""
    if not tag_commit:
        problems.append(f"tag {tag} does not exist")
    elif tag_commit != commit and not args.allow_dirty:
        problems.append(
            f"tag {tag} points at {tag_commit[:10]}, HEAD is {commit[:10]}"
        )
    if problems:
        log("")
        for p in problems:
            log(f"[STOP] {p}")
        log("Refusing to build (task book §3).")
        return 2

    out_dir = Path(args.out_dir).resolve()
    pkg_name = f"1KeyTranscoder-v{version}-{args.platform}-selfcontained"
    stage = out_dir / pkg_name
    archive = out_dir / f"{pkg_name}.zip"

    files = wanted_files()
    log("")
    log(f"package files    : {len(files)} entries from the allowlist")
    if args.dry_run:
        for p in files:
            log(f"    {p.relative_to(ROOT).as_posix()}")
        log("")
        log(f"[dry-run] would create {stage}")
        log(f"[dry-run] would create {archive}")
        return 0

    # ---- staging -------------------------------------------------------
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    copied = copy_into_staging(files, stage)
    log(f"staged           : {stage}")
    log(f"staged bytes     : {copied:,} ({copied / 1024 / 1024:.1f} MiB)")

    # ---- runtime versions ---------------------------------------------
    runtime = collect_runtime_versions()
    for name, text in runtime.items():
        log(f"  {name:<8}: {text[:96]}")

    build_time = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )
    manifest = {
        "package": pkg_name,
        "version": version,
        "git_commit": commit,
        "git_describe": describe,
        "git_branch": branch,
        "git_tag": tag,
        "dirty": bool(dirty_lines),
        "tracked_modified": dirty_lines,
        "build_time": build_time,
        "build_host": platform.node(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "arch": "x64",
        "os_version": platform.version(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "builder": "release/build_release.py",
        "runtime_versions": runtime,
        "entrypoint": "1kt.py",
        "excluded_by_design": [
            ".git/", "work/", "testsets/", "docs/", "olddocs/",
            "logs/", "metadata_forensics/", "tools/VCEEncC_9.12_x64/",
            "__pycache__/",
        ],
        "files": file_records(stage),
    }

    inner = stage / "release-manifest.json"
    inner.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"manifest         : {inner.relative_to(out_dir).as_posix()} "
        f"({len(manifest['files'])} file records)")

    if args.no_zip:
        log("")
        log("[--no-zip] staging tree only; no archive written")
        return 0

    # ---- archive -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    make_zip(stage, archive, log)

    zip_sha = sha256_file(archive)
    zip_size = archive.stat().st_size
    (out_dir / f"{archive.name}.sha256").write_text(
        f"{zip_sha}  {archive.name}\n", encoding="utf-8"
    )
    outer = dict(manifest)
    outer["archive"] = {
        "name": archive.name,
        "size": zip_size,
        "sha256": zip_sha,
    }
    (out_dir / "release-manifest.json").write_text(
        json.dumps(outer, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    log("")
    log("-" * 72)
    log(f"archive          : {archive}")
    log(f"archive size     : {zip_size:,} B ({zip_size / 1024 / 1024:.1f} MiB)")
    log(f"archive sha256   : {zip_sha}")
    log(f"sidecar          : {archive.name}.sha256")
    log(f"outer manifest   : {out_dir / 'release-manifest.json'}")
    log(f"elapsed          : {time.time() - started:.1f}s")
    if dirty_lines:
        log("")
        log("[WARNING] built from a DIRTY tree (--allow-dirty); "
            "manifest records dirty=true. NOT an official release.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=None,
                    help="Override the version (default: VERSION file).")
    ap.add_argument("--platform", default="win64",
                    help="Platform tag used in the package name.")
    ap.add_argument("--out-dir", default=str(ROOT / "dist"),
                    help="Where to write the staging tree and archive.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be packaged; write nothing.")
    ap.add_argument("--no-zip", action="store_true",
                    help="Build the staging tree only.")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="Development build: proceed despite a dirty tree "
                         "or a version/tag mismatch (recorded in manifest).")
    args = ap.parse_args(argv)
    try:
        return build(args)
    except Exception as exc:  # noqa: BLE001 - top-level build guard
        print(f"\n[FATAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
