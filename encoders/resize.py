#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hardware resize helpers shared by the rigaya backends.

Product policy:

* ``--resize WxH``   exact output resolution (no aspect-ratio correction);
* ``--scale FACTOR`` long-edge multiplier, preserving aspect ratio;
* ``--resize-algo``  kernel: bilinear / bicubic / lanczos, default lanczos;
* no option -> no scaling, so the default transcode path is unchanged.

Tool syntax verified on NVEncC 9.31 / QSVEncC 8.26:

    --vpp-resize algo=lanczos
    --output-res 1920x1080

QSV additionally uses ``--vpp-resize-mode quality``.  ``width=`` /
``height=`` are NOT params of ``--vpp-resize``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Union

RESIZE_ALGOS = (
    "bilinear",
    "bicubic",
    "lanczos",
)
DEFAULT_RESIZE_ALGO = "lanczos"
MAX_DIMENSION = 16384


@dataclass(frozen=True)
class ResizeSpec:
    """Explicit output resolution; aspect ratio is NOT corrected."""

    width: int
    height: int
    algo: str = DEFAULT_RESIZE_ALGO


@dataclass(frozen=True)
class ScaleFactor:
    """Long-edge multiplier; output keeps the source aspect ratio."""

    factor: float
    algo: str = DEFAULT_RESIZE_ALGO


ResizeRequest = Union[ResizeSpec, ScaleFactor]


def _even(value: float) -> int:
    """Nearest even integer, at least 2."""
    n = int(round(value / 2.0)) * 2
    return max(2, n)


def parse_resize_size(text: str) -> tuple[int, int]:
    """Parse exact ``WxH`` and reject odd/zero/absurd dimensions."""
    if not text or "x" not in text.lower():
        raise ValueError(f"invalid resize size {text!r}; expected WxH")
    left, right = text.lower().split("x", 1)
    try:
        width = int(left.strip())
        height = int(right.strip())
    except ValueError as exc:
        raise ValueError(f"invalid resize size {text!r}; expected WxH") from exc
    if width <= 0 or height <= 0:
        raise ValueError("resize width/height must be positive")
    if width % 2 or height % 2:
        raise ValueError(
            f"resize size {width}x{height} must use even width/height "
            "for 4:2:0 chroma")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(
            f"resize dimensions above {MAX_DIMENSION} are unsupported")
    return width, height


def parse_scale_factor(text: str) -> float:
    """Parse a positive finite long-edge multiplier."""
    try:
        factor = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid scale factor {text!r}; expected a positive number"
        ) from exc
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("scale factor must be a positive finite number")
    return factor


def resolve_resize(
    request: ResizeRequest,
    source_width: int,
    source_height: int,
) -> ResizeSpec:
    """Resolve a scale request against the actual source dimensions.

    ``ResizeSpec`` is returned unchanged.  ``ScaleFactor`` multiplies the
    long edge and derives the short edge from the source aspect ratio,
    then rounds both dimensions to even values for 4:2:0 encoding.
    """
    if isinstance(request, ResizeSpec):
        return request
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source dimensions are invalid for --scale")

    factor = request.factor
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("scale factor must be a positive finite number")

    if source_width >= source_height:
        target_w = _even(source_width * factor)
        target_h = _even(target_w * source_height / source_width)
    else:
        target_h = _even(source_height * factor)
        target_w = _even(target_h * source_width / source_height)

    if target_w > MAX_DIMENSION or target_h > MAX_DIMENSION:
        raise ValueError(
            f"--scale {factor:g} produces {target_w}x{target_h}, above "
            f"{MAX_DIMENSION}")
    return ResizeSpec(target_w, target_h, request.algo)


def resize_args(kind: str, spec: ResizeSpec) -> list[str]:
    """Return rigaya CLI arguments for a hardware resize.

    ``kind`` is ``"nvencc"`` or ``"qsvencc"``.  The caller must verify
    that the tool advertises ``--vpp-resize`` and ``--output-res``; this
    helper only formats the verified syntax.
    """
    if kind not in ("nvencc", "qsvencc"):
        raise ValueError(f"hardware resize unsupported for backend {kind!r}")
    if spec.algo not in RESIZE_ALGOS:
        raise ValueError(f"unsupported resize algorithm {spec.algo!r}")
    args = [
        "--vpp-resize", f"algo={spec.algo}",
        "--output-res", f"{spec.width}x{spec.height}",
    ]
    if kind == "qsvencc":
        # QSV's default resize mode can take a low-power path; force the
        # quality path so --resize-algo behaves consistently.
        args += ["--vpp-resize-mode", "quality"]
    return args

