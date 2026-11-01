#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precision policy for the GPU alignment architecture."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

__all__ = ["Precision", "Policy", "resolve_policy"]


class Precision(str, Enum):
    EXACT = "exact"      # FP64 compute, FP64 report
    FAST = "fast"        # FP32 compute, caller decides report tolerance
    HYBRID = "hybrid"    # FP32 search + FP64 confirmation (caller-driven)


@dataclass(frozen=True)
class Policy:
    precision: Precision
    storage_dtype: str
    compute_dtype: str
    report_dtype: str
    reason: str = ""


def resolve_policy(mode: str | None = None) -> Policy:
    if mode is None:
        mode = os.environ.get("KTS_GPU_ALIGN", "0")
    mode = (mode or "0").strip().lower()
    # User decision 2026-11-01: exact FP64 parity is NOT required for this
    # experimental path; only "not materially worse" is required.  "1"/on
    # therefore selects the fast FP32 path; exact remains explicit.
    if mode in ("exact", "fp64"):
        p = Precision.EXACT
    elif mode in ("1", "fast", "fp32", "true", "yes", "on"):
        p = Precision.FAST
    elif mode == "hybrid":
        p = Precision.HYBRID
    elif mode in ("0", "off", "false", "no", ""):
        p = Precision.EXACT
    else:
        raise ValueError(f"unknown GPU alignment precision: {mode!r}")
    if p is Precision.FAST:
        return Policy(p, "float32", "float32", "float64",
                      "fp32 fast path; report parity is caller's responsibility")
    if p is Precision.HYBRID:
        return Policy(p, "float64", "float32", "float64",
                      "fp32 search + fp64 confirm; storage kept fp64")
    return Policy(p, "float64", "float64", "float64", "exact fp64 path")
