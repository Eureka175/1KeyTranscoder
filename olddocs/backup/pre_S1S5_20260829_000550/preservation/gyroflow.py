"""Gyroflow headless consumer validation.

Structural checks (MP4Box/ffprobe) are necessary but not sufficient:
the actual downstream consumer must be able to read the preserved Sony
metadata. Gyroflow's CLI (`--export-metadata 2:<path>`) runs the same
telemetry-parser engine the GUI uses, headless, and dumps the parsed
metadata as JSON — including raw_imu samples, detected camera, lens
info and frame rate.

check() parses BOTH the original source and the final output and
compares what the consumer sees. A structurally perfect file that
Gyroflow cannot read fails here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

_CANDIDATES = [
    Path(r"D:\Gyroflow-windows64\Gyroflow.exe"),
    Path(r"C:\Program Files\Gyroflow\Gyroflow.exe"),
]


def find_gyroflow(explicit: Path | None = None) -> Path | None:
    if explicit:
        return explicit if explicit.is_file() else None
    for c in _CANDIDATES:
        if c.is_file():
            return c
    return None


def _export(gyroflow: Path, video: Path, out_json: Path) -> dict[str, Any]:
    # Gyroflow resolves paths against ITS working directory, not ours:
    # always hand it absolute paths.
    video = video.resolve()
    out_json = out_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if out_json.exists():
        out_json.unlink()
    cmd = [
        str(gyroflow),
        str(video),
        "--export-metadata",
        f"2:{out_json}",
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
    )
    if not out_json.is_file():
        raise RuntimeError(
            f"Gyroflow produced no metadata export for {video.name} "
            f"(rc={proc.returncode}):\n{(proc.stdout or '')[-1500:]}"
        )
    return json.loads(out_json.read_text(encoding="utf-8"))


def _facts(parsed: dict[str, Any]) -> dict[str, Any]:
    cam = parsed.get("camera_identifier") or {}
    return {
        "imu_samples": len(parsed.get("raw_imu") or []),
        "detected_source": parsed.get("detected_source") or "",
        "camera_identifier": cam.get("identifier") or "",
        "lens_model": cam.get("lens_model") or "",
        "frame_rate": parsed.get("frame_rate"),
        "frame_readout_time": parsed.get("frame_readout_time"),
        "has_lens_profile": parsed.get("lens_profile") is not None,
        "has_lens_positions": bool(parsed.get("lens_positions")),
    }


def check(
    original: Path,
    final: Path,
    gyroflow: Path,
    scratch: Path,
) -> dict[str, Any]:
    """Compare what Gyroflow parses from original vs final.

    PASS requires: non-zero IMU samples in the final, and every
    consumer-visible fact equal to the original.
    """
    src_parsed = _export(gyroflow, original, scratch / "gyro_original.json")
    out_parsed = _export(gyroflow, final, scratch / "gyro_final.json")

    src = _facts(src_parsed)
    out = _facts(out_parsed)

    mismatches = {
        k: {"original": src[k], "final": out[k]}
        for k in src
        if k != "imu_samples" and src[k] != out[k]
    }

    status = PASS
    detail = ""
    if out["imu_samples"] == 0:
        status = FAIL
        detail = "Gyroflow read zero IMU samples from the final output"
    elif out["imu_samples"] != src["imu_samples"]:
        status = FAIL
        detail = (
            f"IMU sample count differs: original {src['imu_samples']} vs "
            f"final {out['imu_samples']}"
        )
    elif mismatches:
        status = FAIL
        detail = f"consumer-visible metadata differs: {mismatches}"
    else:
        detail = (
            f"{out['imu_samples']} IMU samples, "
            f"{out['detected_source']}, identifier and timeline match"
        )

    return {
        "tool": str(gyroflow),
        "status": status,
        "detail": detail,
        "original": src,
        "final": out,
        "mismatches": mismatches,
    }
