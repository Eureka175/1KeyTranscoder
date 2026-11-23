"""Shared Sony/DJI metadata-preservation policy.

Both the hardware batch path (``core/batch_hw.py``) and the software
classic path (``1kt.py``) used to duplicate this decision: which source
family a file belongs to, whether preservation is on, and which DJI model
tool tag was found.  The duplicated clones could drift (different logging,
different precedence for ``--no-metadata`` / ``--preserve-metadata`` /
split-join forcing).

This module is the single source of truth.  Both paths call
:func:`resolve_metadata_plan` and :func:`log_metadata_plan`; the encoder
backend (NVENC / QSV / x265 / SVT-AV1) only changes how the preserved
payload is re-muxed, never *whether* preservation is requested.

Precedence (highest first):
1. ``--no-metadata``   -> skip preservation for Sony/DJI.
2. split-join staging   -> preserve by default (the joined file has no
   movie ``tool`` tag, so the Action-series default cannot recognise it).
3. ``--preserve-metadata`` -> preserve, overriding the DJI default skip.
4. DJI default: Action series preserve, Pocket/drone skip.
5. Sony ``rtmd`` sources always preserve.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import dji as dji_mod


SONY = "sony"
DJI = "dji"
GENERIC = "generic"


@dataclass(frozen=True)
class MetadataPlan:
    """Resolved preservation decision for one source file."""

    family: str
    preserve: bool
    tool: str | None = None
    reason: str = "generic"
    forced_join: bool = False
    join_tool: str | None = None

    @property
    def is_sony(self) -> bool:
        return self.family == SONY

    @property
    def is_dji(self) -> bool:
        return self.family == DJI


def _is_sony_streams(streams: list[dict[str, Any]]) -> bool:
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "rtmd"
        for st in streams
    )


def _is_dji_streams(streams: list[dict[str, Any]]) -> bool:
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "djmd"
        for st in streams
    )


def resolve_metadata_plan(
    *,
    streams: list[dict[str, Any]],
    gpac: Any,
    src: Path,
    no_metadata: bool = False,
    preserve_metadata: bool = False,
    forced_join: bool = False,
    join_tool: str | None = None,
    dji_keep_default: Callable[..., tuple[bool, str | None]] | None = None,
) -> MetadataPlan:
    """Resolve the preservation plan; pure decision, no side effects.

    ``dji_keep_default`` is injectable for unit tests (defaults to
    :func:`preservation.dji.keep_metadata_by_default`).
    """
    is_sony = _is_sony_streams(streams)
    is_dji = _is_dji_streams(streams)
    family = SONY if is_sony else DJI if is_dji else GENERIC

    if no_metadata:
        return MetadataPlan(family, False, reason="no_metadata",
                            forced_join=forced_join, join_tool=join_tool)

    if is_sony:
        return MetadataPlan(SONY, True, reason="sony_rtmd")

    if not is_dji:
        return MetadataPlan(GENERIC, False, reason="generic")

    keep_default = dji_keep_default or dji_mod.keep_metadata_by_default
    default_keep, tool = keep_default(gpac, src)

    if forced_join:
        return MetadataPlan(
            DJI, True, tool=tool,
            reason="dji_join_preserve", forced_join=True,
            join_tool=join_tool,
        )
    if preserve_metadata:
        return MetadataPlan(
            DJI, True, tool=tool, reason="dji_preserve_opt",
        )
    if default_keep:
        return MetadataPlan(
            DJI, True, tool=tool, reason="dji_action_default",
        )
    return MetadataPlan(
        DJI, False, tool=tool, reason="dji_default_skip",
    )


def log_metadata_plan(
    plan: MetadataPlan,
    *,
    src: Path,
    logger: Any,
    file_logger: Any,
) -> None:
    """Emit the canonical, backend-independent policy log lines."""
    if plan.reason == "no_metadata" and plan.family in (SONY, DJI):
        logger.info(
            "[META_POLICY] %s | no_metadata: %s preservation skipped, "
            "ordinary transcode path used", src.name, plan.family,
        )
        file_logger.info(
            "META_POLICY | no_metadata | source_family=%s | "
            "container rebuild skipped", plan.family,
        )
        return
    if plan.family != DJI:
        return
    if plan.reason == "dji_default_skip":
        logger.info(
            "[META_POLICY] %s | DJI 非 Action 机型 (tool=%r): "
            "默认不保留元数据, 走普通转码路径 "
            "(可用 --preserve-metadata 覆盖)",
            src.name, plan.tool,
        )
        file_logger.info(
            "META_POLICY | dji_default_skip | tool=%r | "
            "container rebuild skipped", plan.tool,
        )
    elif plan.reason == "dji_join_preserve":
        tool = plan.join_tool if plan.join_tool is not None else plan.tool
        logger.info(
            "[META_POLICY] %s | DJI split-join staging source: "
            "preserve by default (join-time tool=%r; the joined file has "
            "no movie tool tag)", src.name, tool,
        )
        file_logger.info(
            "META_POLICY | dji_join_preserve | join_tool=%r", tool,
        )
