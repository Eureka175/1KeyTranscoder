"""Single source of truth for output chroma / bit-depth planning.

Policy (2026-11-20):

* **Bit depth defaults to 10-bit for every output**, regardless of the
  source depth.  ``--output-depth {8,10,12}`` is the explicit override.
* **Chroma defaults to the source chroma** (4:2:0 / 4:2:2 / 4:4:4 / mono)
  where the selected backend can carry it; otherwise the capability fallback
  is 4:2:0/10 with a downgrade warning.  ``--output-chroma {420,422,444,mono}``
  is the explicit override.
* Explicit overrides are returned as requested, even when the backend does
  not advertise the format; it is the caller/encoder's job to fail loudly
  rather than silently changing a manually requested format.
* AV1 backends are still 4:2:0-only: a non-4:2:0 source is downgraded to
  4:2:0/10 by default (manual 4:2:2/4:4:4 cannot be honoured by these
  encoders).

Producers log ``[FORMAT-PLAN]`` (console) and ``FORMAT_PLAN`` (file log)
from this plan so every path emits the same diagnostic shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .caps import supports

_CHROMA_BASE = {
    "4:2:0": "yuv420p",
    "4:2:2": "yuv422p",
    "4:4:4": "yuv444p",
    "mono": "gray",
}

# x265 (FFmpeg libx265) advertises 4:2:0/4:2:2/4:4:4 at 8/10/12-bit.
_X265_SUPPORTED = {
    ("4:2:0", 8), ("4:2:0", 10), ("4:2:0", 12),
    ("4:2:2", 8), ("4:2:2", 10), ("4:2:2", 12),
    ("4:4:4", 8), ("4:4:4", 10), ("4:4:4", 12),
}
_DEFAULT_DEPTH = 10
_SUPPORTED_DEPTHS = (8, 10, 12)

# Process-wide overrides, configured once from the CLI.  The runner is
# single-purpose (one invocation = one encoder/format policy), so a small
# module-level setting is simpler and less error-prone than threading two
# extra parameters through every backend/ladder call site.
_MANUAL_DEPTH: int | None = None
_MANUAL_CHROMA: str | None = None


@dataclass(frozen=True)
class FormatPlan:
    """Resolved output chroma/depth and why it changed."""

    chroma: str
    depth: int
    downgrade: bool
    reason: str
    manual: bool = False

    def as_tuple(self) -> tuple[str, int]:
        return (self.chroma, self.depth)


def _normalize_backend(backend: str) -> str:
    return {
        "nvencc": "nvenc",
        "qsvencc": "qsv",
        "svt": "svtav1",
    }.get(backend, backend)


def _normalize_depth(depth: int) -> int:
    if depth <= 0:
        return _DEFAULT_DEPTH
    if depth <= 8:
        return 8
    if depth <= 10:
        return 10
    return 12


def _normalize_chroma(chroma: str | None) -> str | None:
    """Accept CLI aliases and stream names; return canonical name."""
    if chroma is None:
        return None
    text = str(chroma).strip().lower().replace(" ", "")
    aliases = {
        "420": "4:2:0", "4:2:0": "4:2:0", "yuv420p": "4:2:0",
        "422": "4:2:2", "4:2:2": "4:2:2", "yuv422p": "4:2:2",
        "444": "4:4:4", "4:4:4": "4:4:4", "yuv444p": "4:4:4",
        "mono": "mono", "gray": "mono",
    }
    if text not in aliases:
        raise ValueError(
            f"unsupported chroma {chroma!r}; use 420/422/444/mono"
        )
    return aliases[text]


def configure_output_format(*, depth: int | None = None,
                            chroma: str | None = None) -> None:
    """Install process-wide manual overrides (called from the CLI).

    ``depth=None`` means "default 10-bit"; ``chroma=None`` means
    "follow the source chroma".  Passing either argument is an explicit
    manual request and is recorded as such in ``FormatPlan.reason``.
    """
    global _MANUAL_DEPTH, _MANUAL_CHROMA
    if depth is not None:
        try:
            depth = int(depth)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid output depth: {depth!r}") from exc
        if depth not in _SUPPORTED_DEPTHS:
            raise ValueError(
                f"unsupported output depth {depth}; use 8, 10 or 12"
            )
    _MANUAL_DEPTH = depth
    _MANUAL_CHROMA = _normalize_chroma(chroma)
    if _MANUAL_CHROMA == "mono":
        raise ValueError(
            "mono output is not supported; use 420/422/444 "
            "(mono sources are also unsupported)"
        )


def reset_output_format() -> None:
    """Reset overrides to defaults (used by tests and embedded callers)."""
    global _MANUAL_DEPTH, _MANUAL_CHROMA
    _MANUAL_DEPTH = None
    _MANUAL_CHROMA = None


def output_format_overrides() -> tuple[int | None, str | None]:
    return _MANUAL_DEPTH, _MANUAL_CHROMA


def plan_output_format(
    *,
    backend: str,
    codec: str,
    chroma: str,
    depth: int,
    caps: Any | None = None,
) -> FormatPlan:
    """Return the planned output format for one source/back-end pair.

    ``backend`` accepts both CLI names and rigaya internal kinds
    (``nvencc`` / ``qsvencc``); ``caps`` is only needed for hardware
    backends and is the object returned by :mod:`encoders.caps`.
    """
    backend = _normalize_backend(backend)
    codec = (codec or "hevc").lower()
    src_chroma = _normalize_chroma(chroma) or "4:2:0"
    src_depth = _normalize_depth(depth)
    if src_chroma == "mono":
        raise ValueError(
            "mono sources are not supported; use a 4:2:0/4:2:2/4:4:4 source"
        )

    # Explicit CLI override: return exactly what the user asked for.  Do not
    # capability-downgrade here — a manual request that the backend cannot
    # honour must fail visibly instead of silently changing format.
    if _MANUAL_DEPTH is not None or _MANUAL_CHROMA is not None:
        target_chroma = _MANUAL_CHROMA or src_chroma
        target_depth = _MANUAL_DEPTH if _MANUAL_DEPTH is not None else _DEFAULT_DEPTH
        return FormatPlan(
            target_chroma, target_depth, False, "manual_output_format",
            manual=True,
        )

    target_depth = _DEFAULT_DEPTH
    depth_loss = src_depth > target_depth

    # AV1: every AV1 backend here is 4:2:0-only.
    if codec == "av1":
        downgrade = (src_chroma != "4:2:0") or depth_loss
        reason = "av1_420_only" if src_chroma != "4:2:0" else "default_10bit"
        return FormatPlan("4:2:0", target_depth, downgrade, reason)

    # Software x265: preserve source chroma where libx265 supports it.
    if backend == "x265":
        if (src_chroma, target_depth) in _X265_SUPPORTED:
            return FormatPlan(src_chroma, target_depth, depth_loss,
                              "source_supported" if not depth_loss
                              else "depth_normalized_to_10")
        return FormatPlan("4:2:0", target_depth, True, "x265_downgrade")

    # Hardware HEVC: preserve source chroma where measured caps allow it.
    if backend in ("nvenc", "qsv"):
        if caps is not None and supports(caps, src_chroma, target_depth, codec):
            return FormatPlan(src_chroma, target_depth, depth_loss,
                              "capability_supported" if not depth_loss
                              else "depth_normalized_to_10")
        return FormatPlan("4:2:0", target_depth, True, "capability_downgrade")

    # Unknown backend: conservative fallback.
    return FormatPlan("4:2:0", target_depth, True, "fallback_420_10")


def pix_fmt_name(chroma: str, depth: int) -> str:
    """FFmpeg pixel-format name for a planned (chroma, depth)."""
    base = _CHROMA_BASE.get(_normalize_chroma(chroma) or "4:2:0", "yuv420p")
    depth = _normalize_depth(depth)
    if depth > 8:
        return f"{base}{depth}le"
    return base


def log_line(plan: FormatPlan) -> str:
    """Compact single-line description shared by every backend."""
    manual = " | manual=True" if plan.manual else ""
    return (
        f"encode={plan.chroma}/{plan.depth} | downgrade={plan.downgrade} "
        f"| reason={plan.reason}{manual}"
    )
