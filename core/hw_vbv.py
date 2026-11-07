#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hardware-backend VBV adaptation.

NVENC uses the same source-relative VBV table as x265
(``x265_scaling.json`` -> ``dynamic_vbv``): max_bitrate is derived from
the source OB and the efficiency class, then vbv_bufsize is derived from
that effective maxrate.  QSV and VCE are intentionally excluded: QSV is
kept byte-for-byte on its static table, and VCE has no entry point yet.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import load_scaling_config
from .models import SourceInfo
from .scaling import ScalingEngine
from .source_classifier import SourceClassifier

__all__ = ["apply_nvenc_dynamic_profile"]


@lru_cache(maxsize=4)
def _scaling(path_text: str) -> dict[str, Any]:
    return load_scaling_config(Path(path_text))


def apply_nvenc_dynamic_profile(
    profile: dict[str, Any],
    preset: str,
    src: SourceInfo,
    scaling_path: Path | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a per-file NVENC profile with x265-style dynamic VBV.

    On any invalid/missing configuration or unknown source rule the
    original profile object is returned (static NVENC fallback).
    """
    if scaling_path is None:
        return profile, None
    try:
        cfg = _scaling(str(Path(scaling_path).resolve()))
        cls = SourceClassifier(cfg).classify(src)
        vbv = ScalingEngine(cfg).dynamic_vbv(
            preset, src, cls.source_class)
    except Exception:                                  # noqa: BLE001
        return profile, None
    if vbv is None:
        return profile, None

    out = dict(profile)
    out["max_bitrate"] = int(vbv["final_maxrate_kbps"])
    out["vbv_bufsize"] = int(vbv["final_bufsize_kbps"])
    audit = {
        "mode": "dynamic_vbv",
        "source_class": vbv["source_class"],
        "normalized_ob": round(cls.normalized_ob, 6),
        "ob_kbps": vbv["ob_kbps"],
        "final_maxrate_kbps": vbv["final_maxrate_kbps"],
        "final_bufsize_kbps": vbv["final_bufsize_kbps"],
        "bufsize_factor": vbv["bufsize_factor"],
    }
    return out, audit
