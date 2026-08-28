"""Explicit configuration loading and executable discovery.

The x265 workflow loads exactly:

    x265.json           base 4K60 profiles
    x265_scaling.json   source-dependent scaling rules

There is no "*.json beside the script" heuristic: the project directory
also holds nvenc.json / qsv.json / vce.json (future encoders), which
must never be auto-detected or modified by this workflow.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import PRESETS, SOURCE_CLASSES


def resolve_config_file(
    script_dir: Path,
    explicit: str | None,
    default_name: str,
) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = script_dir / p
        if not p.is_file():
            raise FileNotFoundError(f"JSON config not found: {p}")
        return p.resolve()

    p = script_dir / default_name
    if not p.is_file():
        raise FileNotFoundError(
            f"{default_name} not found beside the script: {p}. "
            "Use an explicit option to specify it."
        )
    return p.resolve()


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON: {path} | line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")

    return data


def load_config(path: Path) -> dict[str, Any]:
    """Load and validate the x265 base profile JSON (x265.json)."""
    data = load_json_file(path)

    profiles = data.get("profile")
    if not isinstance(profiles, dict):
        raise ValueError("JSON must contain an object named 'profile'.")

    missing = [preset for preset in PRESETS if preset not in profiles]
    if missing:
        raise ValueError(
            f"Missing profile(s): {', '.join(missing)}"
        )

    for preset in PRESETS:
        profile = profiles[preset]
        if not isinstance(profile, dict):
            raise ValueError(f"profile.{preset} must be an object.")

        required = ("preset", "crf", "rd", "vbv_maxrate", "vbv_bufsize")
        missing_fields = [key for key in required if key not in profile]
        if missing_fields:
            raise ValueError(
                f"profile.{preset} missing: {', '.join(missing_fields)}"
            )

        if "rdo" in profile:
            raise ValueError(
                f"profile.{preset} contains obsolete key 'rdo'; use 'rd'."
            )

    return data


def load_scaling_config(path: Path) -> dict[str, Any]:
    """Load and validate x265_scaling.json (source-dependent rules)."""
    data = load_json_file(path)

    reference = data.get("reference")
    if not isinstance(reference, dict):
        raise ValueError(
            f"{path.name} must contain an object named 'reference'."
        )
    for key in ("width", "height", "fps"):
        if key not in reference:
            raise ValueError(
                f"{path.name}: reference missing '{key}'."
            )

    param_rules = data.get("param_rules", {})
    if not isinstance(param_rules, dict):
        raise ValueError(f"{path.name}: 'param_rules' must be an object.")
    for name, rule in param_rules.items():
        if name.startswith("_"):
            continue
        if not isinstance(rule, dict):
            raise ValueError(
                f"{path.name}: param_rules.{name} must be an object."
            )
        mode = rule.get("mode", "fixed")
        if mode not in ("fixed", "fps", "sqrt_pixels", "pixel_rate"):
            raise ValueError(
                f"{path.name}: param_rules.{name} has unknown "
                f"mode '{mode}'."
            )

    classification = data.get("classification", {})
    if not isinstance(classification, dict):
        raise ValueError(
            f"{path.name}: 'classification' must be an object."
        )

    dynamic_vbv = data.get("dynamic_vbv", {})
    if not isinstance(dynamic_vbv, dict):
        raise ValueError(
            f"{path.name}: 'dynamic_vbv' must be an object."
        )
    for preset, per_class in dynamic_vbv.items():
        if preset.startswith("_"):
            continue
        if preset not in PRESETS:
            raise ValueError(
                f"{path.name}: dynamic_vbv.{preset} is not a known "
                f"profile ({', '.join(PRESETS)})."
            )
        if not isinstance(per_class, dict):
            raise ValueError(
                f"{path.name}: dynamic_vbv.{preset} must be an object."
            )
        for cls, rule in per_class.items():
            if cls not in SOURCE_CLASSES:
                raise ValueError(
                    f"{path.name}: dynamic_vbv.{preset}.{cls} is not a "
                    f"known source class ({', '.join(SOURCE_CLASSES)})."
                )
            for key in ("min_ratio", "target_ratio", "max_ratio"):
                if key not in rule:
                    raise ValueError(
                        f"{path.name}: dynamic_vbv.{preset}.{cls} "
                        f"missing '{key}'."
                    )
            if not (
                float(rule["min_ratio"])
                <= float(rule["target_ratio"])
                <= float(rule["max_ratio"])
            ):
                raise ValueError(
                    f"{path.name}: dynamic_vbv.{preset}.{cls} must "
                    f"satisfy min_ratio <= target_ratio <= max_ratio."
                )

    return data


def find_executable(
    name: str,
    script_dir: Path,
    explicit: str | None = None,
) -> Path:
    exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"

    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = script_dir / p
        if not p.is_file():
            raise FileNotFoundError(f"{exe_name} not found: {p}")
        return p.resolve()

    candidates = [
        script_dir / exe_name,
        script_dir / "tools" / exe_name,
        Path.cwd() / exe_name,
        Path.cwd() / "tools" / exe_name,
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    found = shutil.which(exe_name)
    if found:
        return Path(found).resolve()

    raise FileNotFoundError(
        f"Could not find {exe_name}. "
        "Expected it beside the script, under tools\\, or in PATH."
    )


def find_hw_tool(
    script_dir: Path,
    exe_name: str,
    explicit: str | None = None,
) -> Path:
    """Locate a rigaya encoder executable (tools/<ToolVer>/<exe>.exe)."""
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = script_dir / p
        if not p.is_file():
            raise FileNotFoundError(f"{exe_name} not found: {p}")
        return p.resolve()

    candidates = sorted((script_dir / "tools").glob(f"**/{exe_name}"))
    if candidates:
        return candidates[0].resolve()

    found = shutil.which(exe_name)
    if found:
        return Path(found).resolve()

    raise FileNotFoundError(
        f"Could not find {exe_name}. Expected under tools\\ "
        "(e.g. tools/NVEncC_9.31_x64/) or in PATH; use --tool-* to "
        "specify an explicit path."
    )


def verify_executable(path: Path, expected: str) -> None:
    try:
        result = subprocess.run(
            [str(path), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"Cannot execute {expected}: {path} | {exc}"
        ) from exc

    text = (result.stdout or "") + (result.stderr or "")
    lower = text.lower()

    if result.returncode != 0:
        raise RuntimeError(
            f"{expected} returned {result.returncode}: {path}"
        )

    if expected.lower() == "ffmpeg" and "ffmpeg version" not in lower:
        raise RuntimeError(
            f"Path is not behaving like ffmpeg.exe: {path}"
        )

    if expected.lower() == "ffprobe" and "ffprobe version" not in lower:
        raise RuntimeError(
            f"Path is not behaving like ffprobe.exe: {path}"
        )
