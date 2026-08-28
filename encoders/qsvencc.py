"""QSVEncC backend (rigaya, Intel Quick Sync Video).

Consumes the authoritative qsv.json profiles verbatim (same
flag-whitelist mechanism as nvencc). Policy: 4:2:2 sources always plan
a 4:2:0 conversion — direct 4:2:2 HEVC encode on Arc is the slow path
(~1.0x vs 2.0x for the conversion), and QSV has no 10-bit H.264
decode at all (silent avsw fallback confirmed empirically). Hardware
encode paths always decode with --avsw (software).
"""

from __future__ import annotations

from pathlib import Path

from .caps import BackendCaps, CONSERVATIVE_CAPS
from .hw import build_flag_args, known_flags

# qsv.json profile key -> (flag candidates, kind)
PARAM_MAP = {
    "tu": ("--tu", "value"),
    "tu_level": ("--tu-level", "value"),
    "icq": ("--icq", "value"),
    "profile": ("--profile", "value"),
    "tier": ("--tier", "value"),
    "level": ("--level", "value"),
    "max_bitrate": ("--max-bitrate", "value"),
    "vbv_bufsize": ("--vbv-bufsize", "value"),
    "bframes": ("-b --bframes", "value"),
    "ref": ("--ref", "value"),
    "b_pyramid": ("--b-pyramid", "flag"),
    "adaptive_i": ("--i-adapt", "flag"),
    "adaptive_b": ("--b-adapt", "flag"),
    "adaptive_ltr": ("--adapt-ltr", "flag"),
    "adaptive_cqm": ("--adapt-cqm", "flag"),
    "mbrc": ("--mbrc", "flag"),
    "scenario": ("--scenario-info", "value"),
    "sao": ("--sao", "value"),
    "ctu_size": ("--ctu", "value"),
    "transform_skip": ("--tskip", "flag"),
    "weight_p": ("--weightp", "flag"),
    "weight_b": ("--weightb", "flag"),
    "gpb": ("--hevc-gpb", "flag"),
    "aud": ("--aud", "flag"),
    "qp_min": ("--qp-min", "list"),
    "qp_max": ("--qp-max", "list"),
    "open_gop": ("--open-gop", "flag"),
    "hyper_mode": ("--hyper-mode", "value"),
    "gop_len": ("--gop-len", "value"),
    "async_depth": ("--async-depth", "value"),
    "output_buf_mb": ("--output-buf-mb", "value"),
    # qsv.json "lookahead" = QSVEncC's lookahead depth (--la-depth).
    # With plain --icq the LA depth is inert; see design doc 5.5
    # (Arc has no LA) — kept mapped for platforms that support LA.
    "lookahead": ("--la-depth", "value"),
}

_SKIPPED_ALWAYS = ("output_depth",)


class QsvBackend:
    name = "qsv"
    kind = "qsvencc"

    def __init__(self, tool: Path, caps: BackendCaps | None = None) -> None:
        self.tool = tool
        self.caps = caps or CONSERVATIVE_CAPS
        self.known = known_flags(tool)

    def build_args(
        self,
        profile: dict,
        chroma: str,
        depth: int,
        vfr: bool = False,
    ) -> tuple[list[str], list[str]]:
        args, skipped = build_flag_args(profile, PARAM_MAP, self.known)
        args = [
            "--avsw", "--video-track", "1", "-c", "hevc",
            "--output-depth", str(depth),
            *args,
        ]
        # 4:2:2 sources: planned 4:2:0 conversion (policy, see module
        # docstring) — chroma is already "4:2:0" when the planner ran.
        if chroma == "4:2:2":
            args += ["--output-csp", "yuv422"]
        if vfr:
            args += ["--avsync", "forcecfr"]
        return args, skipped

    def command(
        self,
        source: Path,
        output: Path,
        profile: dict,
        chroma: str,
        depth: int,
        vfr: bool = False,
        audio_copy: bool = False,
    ) -> tuple[list[str], list[str]]:
        args, skipped = self.build_args(profile, chroma, depth, vfr)
        cmd = [str(self.tool), "-i", str(source), *args]
        if audio_copy:
            cmd += ["--audio-copy"]
        cmd += ["-f", "mp4", "-o", str(output)]
        return cmd, skipped
