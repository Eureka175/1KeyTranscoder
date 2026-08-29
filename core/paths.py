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
    """Final output path: original basename, uppercase .MP4 extension,
    no suffixes. Source and output roots are distinct, so the source
    can never be overwritten."""
    relative = src.relative_to(input_root)

    if multiple_presets:
        # All presets write the same basename; keep them apart per preset.
        root = output_root / preset.upper()
    else:
        root = output_root

    return (
        root
        / relative.parent
        / f"{relative.stem}.MP4"
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
