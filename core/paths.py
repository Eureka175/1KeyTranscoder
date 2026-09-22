"""Shared path helpers: discovery, output paths, job ids, safe unlink.

Extracted from the main program so both the main batch loop and the
hardware batch module (core.batch_hw) share one implementation.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

VIDEO_EXTS = {
    ".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".ts", ".mkv",
    ".avi", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg"
}

#: Final-output extension per *source* extension.  The default output
#: container follows the input container: a `.MOV` source produces a
#: `.MOV`, an `.MP4` source produces an `.MP4`.  Everything else keeps
#: the historical `.MP4` output (that was the pre-existing behaviour for
#: every extension, so nothing regresses) — the two ISOBMFF containers
#: are the ones this project can re-mux losslessly in both directions,
#: so they are the two it promises.  See docs/container-format.md.
FINAL_SUFFIX_BY_SOURCE = {
    ".mov": ".MOV",
    ".mp4": ".MP4",
}

#: Used for every source extension without an explicit entry above.
FINAL_SUFFIX_DEFAULT = ".MP4"


def final_suffix_for(src: Path) -> str:
    """Upper-cased output extension for ``src`` (see the table above).

    Case: the extension is **upper-cased** (`foo.mov` and `foo.MOV` both
    produce `foo.MOV`), matching the extension convention the project
    already used when every output was `.MP4`.  Only the extension is
    normalised, never the stem.
    """
    return FINAL_SUFFIX_BY_SOURCE.get(src.suffix.lower(), FINAL_SUFFIX_DEFAULT)


def safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def discover_sources(root: Path) -> list[Path]:
    return sorted(
        (
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ),
        key=lambda p: str(p).lower(),
    )


def format_path_relation(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def output_path_for(
    src: Path,
    input_root: Path,
    output_root: Path,
    preset: str,
    multiple_presets: bool,
) -> Path:
    """Final output path: original basename, extension from the source
    container, no extra suffixes.  Source and output roots are distinct,
    so the source can never be overwritten.

    The extension is ``final_suffix_for(src)``: `.MOV` for MOV input,
    `.MP4` for MP4 input (and for every other source extension).  The
    caller is responsible for the *container* actually matching it —
    ``core.batch_hw``/``1kt.py`` re-mux losslessly when the encode
    intermediate (always MOV) does not match.  Resume and existing-output
    detection key off this same path, so they follow the container change
    automatically.
    """
    relative = src.relative_to(input_root)

    if multiple_presets:
        # All presets write the same basename; keep them apart per preset.
        root = output_root / preset.upper()
    else:
        root = output_root

    return (
        root
        / relative.parent
        / f"{relative.stem}{final_suffix_for(src)}"
    )


def per_file_log_path(
    src: Path,
    input_root: Path,
    logs_root: Path,
    preset: str,
) -> Path:
    relative = src.relative_to(input_root)

    return (
        logs_root
        / "files"
        / relative.parent
        / f"{relative.stem}_{preset.upper()}.log"
    )


def job_id_for(source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^\w.-]+", "_", source.stem)
    return f"{stem}-{digest}"
