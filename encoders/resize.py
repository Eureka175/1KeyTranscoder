#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hardware resize helpers shared by the rigaya backends.

Current product policy: hardware scaling is explicit (``--scale WxH``),
default kernel ``spline36`` for both NVENC and QSV.  No scaling is done
when the option is absent, so the default transcode path is unchanged.

Tool syntax verified on NVEncC 9.31 / QSVEncC 8.26:

    --vpp-resize algo=spline36
    --output-res 1920x1080

QSV additionally uses ``--vpp-resize-mode quality``.  ``width=`` /
``height=`` are NOT params of ``--vpp-resize``.
"""
from __future__ import annotations

from dataclasses import dataclass

RESIZE_ALGOS = (
    "bilinear",
    "bicubic",
    "spline16",
    "spline36",
    "spline64",
    "lanczos3",
    "lanczos4",
)
DEFAULT_RESIZE_ALGO = "spline36"


@dataclass(frozen=True)
class ResizeSpec:
    width: int
    height: int
    algo: str = DEFAULT_RESIZE_ALGO


def parse_resize_size(text: str) -> tuple[int, int]:
    """Parse ``WxH`` and reject odd/zero/absurd dimensions."""
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
    if width > 16384 or height > 16384:
        raise ValueError("resize dimensions above 16384 are unsupported")
    return width, height


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
