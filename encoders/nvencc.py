"""NVEncC backend (rigaya, NVIDIA NVENC).

Consumes the authoritative nvenc.json profiles verbatim: profile keys
map to CLI flags via PARAM_MAP, and each flag is validated against the
tool's own --help before use (flags a given NVEncC version does not
advertise are skipped and reported as warnings).

The decode reader is **not** decided here.  It arrives as an explicit
``reader`` argument produced by ``encoders.hwdecode.route_decode``, and
its default is ``avsw`` (software) — the v0.6.2 behaviour.  Hardware
decode is opt-in via ``--hw-decode`` and is gated on the runtime-proven
codec/chroma/depth allowlist plus the frame-integrity gate, because the
stock hardware reader loses frames silently on Sony timelines
(docs/hardware-decode/integration-test-matrix.md, category C).

Output is a video-only **MOV** (explicit ``-f mov``, never MP4).  MOV is
the hardware-encode *working* container: every consumer downstream
(preservation rebuild, channel-sync remux) names this intermediate
``*.mov``, and declaring one container while writing another is a defect
even when the two are structurally close (see docs/container-format.md).

The container is an explicit argument, not a guess.  ``-f mov`` and
``-f mp4`` do **not** produce the same file even though both open with
an ``ftyp``/``moov``/``mdat`` ISOBMFF layout — measured on NVEncC 9.31,
``-f mp4`` writes ``ftyp`` major ``mp42`` with compat
``mp42/iso2/mp41`` and a ``free`` filler box, while ``-f mov`` writes
compat ``mp42`` only and a QuickTime ``wide`` filler box.  The values the
pipeline actually reasons about (mvhd timescale 1000 + millisecond-
quantized edit list, video mdhd 240000, tkhd, elst, stts) are identical,
so the preservation pipeline's stts-based duration repair behaves the
same either way; that was verified, not assumed (docs/container-format.md
records the measurements).

``-f mov`` still goes through libavformat's ``mov`` muxer, so the
rigaya moov-at-EOF layout that MP4Box expects is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from core.color import ColorInfo

from .caps import BackendCaps, CONSERVATIVE_CAPS
from .hw import build_flag_args, color_flag_args, known_flags

# nvenc.json profile key -> (flag candidates, kind)
PARAM_MAP = {
    "preset": ("--preset", "value"),
    "tune": ("--tune", "value"),
    "profile": ("--profile", "value"),
    "tier": ("--tier", "value"),
    "level": ("--level", "value"),
    "qvbr": ("--qvbr", "value"),
    "cqp": ("--cqp", "list"),
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
    "refs_forward": ("--refs-forward", "value"),
    "refs_backward": ("--refs-backward", "value"),
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
    "tile_columns": ("--tile-columns", "value"),
    "tile_rows": ("--tile-rows", "value"),
    "part_size_min": ("--part-size-min", "value"),
    "part_size_max": ("--part-size-max", "value"),
    "bitstream_padding": ("--bitstream-padding", "flag"),
}

# nvenc.json keys intentionally not mapped: split_enc/parallel/
# output_buf/cuda_schedule/avoid_idle_clock are process/session-level
# options revisited separately; output_depth is derived from the
# source / downgrade ladder. atc_sei is handled explicitly in
# build_args (its "auto" value would be swallowed by the AUTO token
# check in build_flag_args) — it is an HEVC SEI (alternative transfer
# characteristics, HLG signalling), not an AV1 option.
_SKIPPED_ALWAYS = (
    "split_enc", "parallel", "output_buf",
    "cuda_schedule", "avoid_idle_clock", "avhw", "output_depth",
)


class NvencBackend:
    name = "nvenc"
    kind = "nvencc"

    def __init__(
        self,
        tool: Path,
        caps: BackendCaps | None = None,
        codec: str = "hevc",
    ) -> None:
        self.tool = tool
        self.caps = caps or CONSERVATIVE_CAPS
        self.known = known_flags(tool)
        self.codec = codec
        if codec != "hevc":
            self.name = f"{self.name}-{codec}"

    def build_args(
        self,
        profile: dict,
        chroma: str,
        depth: int,
        vfr: bool = False,
        color: ColorInfo | None = None,
        reader: str = "avsw",
    ) -> tuple[list[str], list[str], list[str]]:
        """Full argument list (before -o) for one encode attempt.

        Returns (argv, skipped_keys, color_notes).

        ``reader`` is ``"avsw"`` (software, default) or ``"avhw"``
        (hardware).  It is always passed explicitly: "let the tool
        choose" is not an option, because NVEncC's default reader and
        its silent fallbacks are exactly what the integrity gate has to
        be able to distinguish.
        """
        if reader not in ("avsw", "avhw"):
            raise ValueError(f"unknown decode reader: {reader!r}")
        args, skipped = build_flag_args(profile, PARAM_MAP, self.known)
        cargs, cnotes = color_flag_args(color, self.known)
        args = [
            f"--{reader}", "--video-track", "1", "-c", self.codec,
            "--output-depth", str(depth),
            *args,
        ]
        # atc_sei ("auto") is a legit rigaya CLI value and would be
        # swallowed by the AUTO token check in build_flag_args. HEVC
        # only (alternative transfer characteristics SEI, HLG).
        if (
            self.codec == "hevc"
            and profile.get("atc_sei")
            and "atc-sei" in self.known
        ):
            args += ["--atc-sei", str(profile["atc_sei"])]
        args += cargs
        if chroma == "4:2:2":
            args += ["--output-csp", "yuv422"]
        if vfr:
            args += ["--avsync", "forcecfr"]
        return args, skipped, cnotes

    def command(
        self,
        source: Path,
        output: Path,
        profile: dict,
        chroma: str,
        depth: int,
        vfr: bool = False,
        audio_copy: bool = False,
        color: ColorInfo | None = None,
        reader: str = "avsw",
        container: str = "mov",
    ) -> tuple[list[str], list[str], list[str]]:
        """Full argv for one encode attempt.

        ``container`` is the *actual* muxer format for ``-f`` (see the
        module docstring: MOV is the hardware working container).  It is
        a parameter rather than a constant so that the file suffix and
        the real container can never drift apart again, and so a caller
        that genuinely needs another container says so explicitly
        instead of relying on the filename.
        """
        args, skipped, notes = self.build_args(
            profile, chroma, depth, vfr, color, reader=reader
        )
        cmd = [str(self.tool), "-i", str(source), *args]
        if audio_copy:
            cmd += ["--audio-copy"]
        cmd += ["-f", container, "-o", str(output)]
        return cmd, skipped, notes
