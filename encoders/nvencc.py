"""NVEncC backend (rigaya, NVIDIA NVENC).

Consumes the authoritative nvenc.json profiles verbatim: profile keys
map to CLI flags via PARAM_MAP, and each flag is validated against the
tool's own --help before use (flags a given NVEncC version does not
advertise are skipped and reported as warnings). Hardware encode paths
always decode with --avsw (software) — hardware-decode frame loss on
Sony timelines is documented in docs/hardware_backend_design.md 5.2.

Output is a video-only mp4 (explicit -f mp4; the preservation pipeline
imports it with MP4Box and repairs the millisecond-timescale edit-list
truncation from the exact stts table before validation).
"""

from __future__ import annotations

from pathlib import Path

from .caps import BackendCaps, CONSERVATIVE_CAPS
from .hw import build_flag_args, known_flags

# nvenc.json profile key -> (flag candidates, kind)
PARAM_MAP = {
    "preset": ("--preset", "value"),
    "tune": ("--tune", "value"),
    "profile": ("--profile", "value"),
    "tier": ("--tier", "value"),
    "level": ("--level", "value"),
    "qvbr": ("--qvbr", "value"),
    "max_bitrate": ("--max-bitrate", "value"),
    "vbv_bufsize": ("--vbv-bufsize", "value"),
    "aq": ("--aq", "flag"),
    "aq_strength": ("--aq-strength", "value"),
    "aq_temporal": ("--aq-temporal", "flag"),
    "lookahead": ("--lookahead", "value"),
    "lookahead_level": ("--lookahead-level", "value"),
    "bframes": ("-b --bframes", "value"),
    "bref_mode": ("--bref-mode", "value"),
    "ref": ("--ref", "value"),
    "tf_level": ("--tf-level", "value"),
    "nonrefp": ("--nonrefp", "flag"),
    "mv_precision": ("--mv-precision", "value"),
    "chroma_qp_offset": ("--chroma-qp-offset", "value"),
    "qp_init": ("--qp-init", "list"),
    "qp_min": ("--qp-min", "list"),
    "qp_max": ("--qp-max", "list"),
    "gop_len": ("--gop-len", "value"),
    "aud": ("--aud", "flag"),
    "repeat_headers": ("--repeat-headers", "flag"),
    "pic_struct": ("--pic-struct", "flag"),
}

# nvenc.json keys intentionally not mapped: AV1-only or session-level
# options (atc_sei is AV1; split_enc/parallel/output_buf/cuda_schedule/
# avoid_idle_clock are process-level; avhw conflicts with the
# forced-software-decode policy; output_depth is derived from the
# source / downgrade ladder).
_SKIPPED_ALWAYS = (
    "atc_sei", "split_enc", "parallel", "output_buf",
    "cuda_schedule", "avoid_idle_clock", "avhw", "output_depth",
)


class NvencBackend:
    name = "nvenc"
    kind = "nvencc"

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
        """Full argument list (before -o) for one encode attempt.

        Returns (argv, skipped_keys)."""
        args, skipped = build_flag_args(profile, PARAM_MAP, self.known)
        args = [
            "--avsw", "--video-track", "1", "-c", "hevc",
            "--output-depth", str(depth),
            *args,
        ]
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
