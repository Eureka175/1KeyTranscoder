"""Project version — single source of truth.

The version lives in the `VERSION` file at the package root (next to
`1kt.py`), so the identical lookup works both in a source checkout and
in an extracted release package. `release/build_release.py` refuses to
build when this file and the git tag disagree.
"""

from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def project_version() -> str:
    """Version string, e.g. "0.6.0". Raises when the file is missing or
    malformed — a package without a readable VERSION is not releasable."""
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"VERSION file unreadable: {VERSION_FILE}") from exc
    if not text:
        raise RuntimeError(f"VERSION file is empty: {VERSION_FILE}")
    return text.splitlines()[0].strip()


def version_string() -> str:
    """"1KeyTranscoder <version>" — the `--version` output."""
    return f"1KeyTranscoder {project_version()}"
