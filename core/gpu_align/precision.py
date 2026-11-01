#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precision policy for the GPU alignment architecture (fp32 / fp64)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

__all__ = ["Precision", "Policy", "resolve_policy"]


class Precision(str, Enum):
    FP64 = "fp64"        # 64-bit compute, fp64 report
    FP32 = "fp32"        # 32-bit compute, caller decides report tolerance
    HYBRID = "hybrid"    # reserved


@dataclass(frozen=True)
class Policy:
    precision: Precision
    storage_dtype: str
    compute_dtype: str
    report_dtype: str
    reason: str = ""


def resolve_policy(mode: str | None = None) -> Policy:
    if mode is None:
        mode = os.environ.get("KTS_GPU_ALIGN", "fp32")
    mode = (mode or "fp32").strip().lower()
    if mode in ("fp64", "fp64le"):
        p = Precision.FP64
    elif mode in ("1", "fp32", "true", "yes", "on", "off", "0", ""):
        p = Precision.FP32
    elif mode == "hybrid":
        p = Precision.HYBRID
    else:
        p = Precision.FP32
    if p is Precision.FP64:
        return Policy(p, "float64", "float64", "float64", "fp64")
    if p is Precision.HYBRID:
        return Policy(p, "float64", "float32", "float64",
                      "fp32 search + fp64 confirm (reserved)")
    return Policy(p, "float32", "float32", "float64", "fp32")
