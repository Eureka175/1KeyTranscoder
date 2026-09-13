"""Project version — single source of truth.

The version lives in the `VERSION` file at the package root (next to
`1kt.py`), so the identical lookup works both in a source checkout and
in an extracted release package. `release/build_release.py` refuses to
build when this file and the git tag disagree.

A frozen (PyInstaller) build has no package root: the file is bundled as a
data file and lands in `sys._MEIPASS`, so that location is probed as well.
Without this the very first `version_string()` call — argparse evaluates it
while building `--version` — raised and the executable died at startup.
"""

from __future__ import annotations

import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _candidates() -> list[Path]:
    paths = [VERSION_FILE]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        paths.append(Path(bundle) / "VERSION")
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / "VERSION")
    paths.append(Path.cwd() / "VERSION")
    # de-duplicate while preserving order (a source checkout can resolve the
    # first and last entry to the same file)
    seen: dict[str, Path] = {}
    for path in paths:
        seen.setdefault(str(path), path)
    return list(seen.values())


def project_version() -> str:
    """Version string, e.g. "0.6.0". Raises when no VERSION file is readable
    anywhere — a package without a readable VERSION is not releasable."""
    tried: list[str] = []
    for path in _candidates():
        tried.append(str(path))
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text.splitlines()[0].strip()
    raise RuntimeError(
        "VERSION file unreadable; looked at: " + ", ".join(tried)
    )


def version_string() -> str:
    """"1KeyTranscoder <version>" — the `--version` output."""
    return f"1KeyTranscoder {project_version()}"
