"""Hardware-first FFmpeg decode planning.

This module makes **one** decision, separately from any encoder decision:

    for this input, which decode route must be used, and **why**?

It is deliberately unaware of NVENC/QSV/x265, of Sony/DJI, and of the
metadata-preservation policy. Encoder selection reads the result; it never
writes it. That separation is the point of the redesign: ``--encoder x265``
selects an *encoder*, and must not silently drop the input onto software
decode.

Why FFmpeg rather than the rigaya readers
-----------------------------------------

``encoders/hwdecode.py`` routes the rigaya ``--avhw`` readers and pins them to
sha256-verified patched research builds under ``tools/avhw/``. That tree is
excluded from the release package (``release/build_release.py`` TOOL_DIRS), so
in a shipped installation that route is structurally unreachable. It also
carries a measured defect: stock ``--avhw`` drops leading pictures on Sony
XAVC (N-3 on XAVC HS, N-2 on XAVC S H.264 4:2:2), silently, with rc=0.

FFmpeg's NVDEC/QSV decoders are a different implementation with no such drop
stage, and ``tools/ffmpeg.exe`` ships with nvdec/cuvid/libvpl enabled.

Three rules this module obeys
-----------------------------

1. **Hardware before software, for every input.** Sony is not special-cased.
   ``is_sony_source()`` must not reach this module.
2. **Decide before encoding, not by failing and retrying.** A route whose
   compatibility was never established is not offered; software is selected
   deterministically when no hardware candidate is compatible.
3. **A registered decoder is not a working decoder.** ``ffmpeg -decoders``
   lists ``h264_qsv``, yet QSV refuses H.264 4:2:2 10-bit on the measured
   silicon with "Error querying IO surface: unsupported (-3)". Every
   acceptance fact here comes from an actual decode attempt.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SUBSTRATE_SW = "sw"
SUBSTRATE_FFMPEG_HW = "ffmpeg_hw"

# --- probe status (three states, never collapsed) ---------------------------

ST_MEASURED = "measured"
ST_UNAVAILABLE = "unavailable"
ST_PROBE_FAILED = "probe_failed"


@dataclass
class Probe:
    """One capability fact, or the reason there is none.

    ``unavailable`` and ``probe_failed`` are distinct on purpose. A missing
    device is a machine fact and may be remembered. A probe that crashed --
    timeout, unparsable output, transient driver error -- says nothing about
    the device and must **not** be cached as "unsupported".
    """

    status: str
    value: object | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ST_MEASURED

    def to_json(self) -> dict:
        return {"status": self.status, "value": self.value, "detail": self.detail}


# --- decode plan ------------------------------------------------------------

@dataclass
class DecodePlan:
    """Everything needed to build the decode half of a command, plus why."""

    substrate: str                  # "ffmpeg_hw" | "sw"
    decoder: str = ""               # e.g. "hevc_cuvid", "hevc_qsv"
    hwaccel: str = ""               # "cuda" | "qsv"
    device: str = ""                # ffmpeg device spec, e.g. "cuda=cu:0"
    device_index: int = 0
    surface_format: str = ""        # what the hw surface nominally holds
    target_format: str = ""         # what the encoder will receive
    reason: str = ""
    detail: str = ""
    needs_format_conversion: bool = False
    transfer: "Transfer | None" = None
    warnings: list[str] = field(default_factory=list)

    @property
    def hardware(self) -> bool:
        return self.substrate == SUBSTRATE_FFMPEG_HW

    def ffmpeg_input_args(self) -> list[str]:
        """Args placed **before** ``-i``."""
        if not self.hardware:
            return []
        return [
            "-init_hw_device", self.device,
            "-hwaccel", self.hwaccel,
            "-hwaccel_device", self.hwaccel,
            "-hwaccel_output_format", self.hwaccel,
            "-c:v", self.decoder,
        ]

    def ffmpeg_filter_args(self) -> list[str]:
        """Args placed after ``-i`` to land in frames the encoder accepts.

        The chain is the **probed** transfer, never a guess at the surface
        format: for HEVC 4:2:2 10-bit on this machine no direct
        ``hwdownload`` format is accepted at all and the working route goes
        through a device-side scale filter.
        """
        if not self.hardware or self.transfer is None:
            return []
        return ["-vf", ",".join(self.transfer.filters)]

    def log_line(self) -> str:
        if not self.hardware:
            return (
                f"decode route: SW substrate=sw decoder=ffmpeg-native "
                f"reason={self.reason} {self.detail}"
            )
        to = self.transfer.format if self.transfer else self.target_format
        via = "" if (self.transfer and self.transfer.direct) else " (via scale)"
        return (
            f"decode route: HW substrate={SUBSTRATE_FFMPEG_HW} "
            f"decoder={self.decoder} hwaccel={self.hwaccel} "
            f"device={self.device_index} -> {to}{via} "
            f"reason={self.reason} {self.detail}"
        )

    def to_json(self) -> dict:
        return {
            "substrate": self.substrate,
            "decoder": self.decoder,
            "hwaccel": self.hwaccel,
            "device_index": self.device_index,
            "surface_format": self.surface_format,
            "target_format": self.target_format,
            "hardware": self.hardware,
            "needs_format_conversion": self.needs_format_conversion,
            "transfer": (
                None if self.transfer is None else {
                    "format": self.transfer.format,
                    "filters": self.transfer.filters,
                    "direct": self.transfer.direct,
                }
            ),
            "reason": self.reason,
            "detail": self.detail,
        }


def plan_software(reason: str, detail: str = "") -> DecodePlan:
    """The deterministic answer when no hardware candidate is compatible."""
    return DecodePlan(substrate=SUBSTRATE_SW, reason=reason, detail=detail)


# --- pixel format mapping ---------------------------------------------------

# chroma + depth -> planar format FFmpeg encoders accept
_PLANAR = {
    ("4:2:0", 8): "yuv420p",
    ("4:2:0", 10): "yuv420p10le",
    ("4:2:0", 12): "yuv420p12le",
    ("4:2:2", 8): "yuv422p",
    ("4:2:2", 10): "yuv422p10le",
    ("4:2:2", 12): "yuv422p12le",
    ("4:4:4", 8): "yuv444p",
    ("4:4:4", 10): "yuv444p10le",
    ("mono", 8): "gray",
    ("mono", 10): "gray10le",
}

# The system-memory format a hardware surface *usually* downloads as. These
# are starting guesses only, never trusted: the transfer chain is resolved by
# probing (see resolve_transfer), because the real surface format depends on
# the device, the codec and the bit depth in ways that canot be predicted.
#
# Measured counter-example that forced this: HEVC 4:2:2 10-bit on this
# machine has a CUDA/QSV surface that will NOT download as p210le, nv16,
# yuv422p10le, p010le or yuv420p -- every direct hwdownload format is
# rejected with "Invalid output format". The working route is
# scale_cuda=format=yuv422p10le,hwdownload,format=yuv422p10le, and it is
# bit-exact against software decode (verified: identical raw SHA256), so it
# preserves 4:2:2 rather than subsampling to 4:2:0.
_SURFACE = {
    ("4:2:0", 8): "nv12",
    ("4:2:0", 10): "p010le",
    ("4:2:0", 12): "p012le",
    ("4:2:2", 8): "nv16",
    ("4:2:2", 10): "p210le",
    ("4:2:2", 12): "p212le",
    ("4:4:4", 8): "yuv444p",
    ("4:4:4", 10): "yuv444p10le",
}

# Per-hwaccel device-side filters that can write a *different* surface format
# before download. Tried in order when a direct hwdownload is refused.
_SCALE_FILTER = {"cuda": "scale_cuda", "qsv": "vpp_qsv"}

# codec -> {hwaccel: decoder name}
_DECODERS = {
    "hevc": {"cuda": "hevc_cuvid", "qsv": "hevc_qsv"},
    "h264": {"cuda": "h264_cuvid", "qsv": "h264_qsv"},
    "av1": {"cuda": "av1_cuvid", "qsv": "av1_qsv"},
    "mpeg2video": {"cuda": "mpeg2_cuvid", "qsv": "mpeg2_qsv"},
    "vp9": {"cuda": "vp9_cuvid", "qsv": "vp9_qsv"},
}


@dataclass
class Transfer:
    """A **probed** route from a hardware surface to a CPU pixel format.

    ``filters`` is the filter chain to place after ``-i`` (without ``-vf``).
    ``format`` is the system-memory format the chain ends in.

    This exists because the surface format is not predictable. For HEVC
    4:2:2 10-bit on this machine no direct ``hwdownload`` format works at
    all, while ``scale_cuda=format=yuv422p10le,hwdownload,format=yuv422p10le``
    works and is bit-exact against software decode.
    """

    format: str
    filters: list[str]
    direct: bool
    evidence: str = ""

    def log_line(self) -> str:
        kind = "direct hwdownload" if self.direct else "via device-side scale"
        return f"transfer {kind}: {','.join(self.filters)} -> {self.format}"


def _transfer_candidates(hwaccel: str, surface: str, target: str) -> list[Transfer]:
    """Routes to try, in preference order.

    Direct download first: it is the cheapest and the only one that involves
    no device-side pixel work at all. The device-side scale filter is the
    fallback, and it is deliberately asked to produce ``target`` (the format
    the encoder wants) rather than the nominal surface format, so a 4:2:2
    source stays 4:2:2 instead of being silently subsampled.
    """
    out: list[Transfer] = []
    for fmt in dict.fromkeys([surface, target]):
        out.append(Transfer(
            format=fmt,
            filters=[f"hwdownload,format={fmt}"],
            direct=True,
        ))
    scale = _SCALE_FILTER.get(hwaccel)
    if scale:
        for fmt in dict.fromkeys([target, surface]):
            # No hwdownload in between: the filter writes straight to the
            # requested system format, which is what makes this work where a
            # direct download has no matching surface format.
            out.append(Transfer(
                format=fmt,
                filters=[f"{scale}=format={fmt}", f"hwdownload,format={fmt}"],
                direct=False,
            ))
    return out


def resolve_transfer(
    ffmpeg: Path, hwaccel: str, index: int, decoder: str, codec: str,
    chroma: str, depth: int, *, work_dir: Path | None = None,
    caps: "HwDecodeCaps | None" = None,
) -> tuple[Transfer | None, Probe]:
    """Find a working surface -> CPU transfer for this combination.

    The result is cached on ``caps`` (keyed by hwaccel/device/format) so the
    search runs at most once per combination per run, not once per file.
    Returns ``(transfer, probe)``; ``transfer`` is None when nothing worked.
    """
    target = planar_format(chroma, depth)
    surface = surface_format(chroma, depth)
    if target is None or surface is None:
        return None, Probe(ST_UNAVAILABLE, value=False,
                           detail=f"no format mapping for {chroma}/{depth}bit")

    cache_key = ("transfer", hwaccel, index, codec, chroma, int(depth))
    if caps is not None:
        cached = caps.acceptance.get(cache_key)
        if cached is not None and cached.status == ST_MEASURED:
            if cached.value and cache_key in caps.transfers:
                return caps.transfers[cache_key], cached
            if not cached.value:
                return None, cached

    fixture = _ensure_fixture(ffmpeg, codec, chroma, depth, work_dir)
    if fixture is None:
        return None, Probe(
            ST_UNAVAILABLE, value=False,
            detail=f"cannot synthesise a {chroma}/{depth}bit probe fixture",
        )

    spec = _device_spec(hwaccel, index)
    attempts: list[str] = []
    for cand in _transfer_candidates(hwaccel, surface, target):
        rc, out = _run([
            str(ffmpeg), "-hide_banner", "-v", "error",
            "-init_hw_device", spec,
            "-hwaccel", hwaccel,
            "-hwaccel_device", hwaccel,
            "-hwaccel_output_format", hwaccel,
            "-c:v", decoder,
            "-i", str(fixture),
            "-vf", ",".join(cand.filters),
            "-frames:v", "2", "-f", "null", "-",
        ], timeout=120)
        if rc == 0:
            cand.evidence = f"accepted after {len(attempts)} rejected route(s)"
            probe = Probe(
                ST_MEASURED, value=True,
                detail=f"transfer via {','.join(cand.filters)}",
            )
            if caps is not None:
                caps.transfers[cache_key] = cand
                caps.acceptance[cache_key] = probe
            return cand, probe
        first = next((ln for ln in out.splitlines() if ln.strip()), "")
        attempts.append(f"{','.join(cand.filters)}: {first.strip()[:70]}")

        # Distinguish "this decoder cannot hold the format at all" from "no
        # download route worked". Measured: on the Intel Arc 140T the QSV
        # HEVC 4:2:2 10-bit path fails with
        # `[AVHWFramesContext] Unsupported pixel format` -- the decoder never
        # produces a surface -- whereas a genuine transfer problem would
        # report an unsupported hwdownload format against a real frame. Both
        # route to software/another device, but the reason code has to say
        # which one happened, so it can be diagnosed later.
        low = first.lower()
        if "hwframescontext" in low or "unsupported pixel format" in low:
            decode_probe = Probe(
                ST_MEASURED, value=False,
                detail=f"{decoder} cannot allocate a {chroma}/{depth}bit "
                       f"surface on {hwaccel}[{index}]: {first.strip()[:140]}",
            )
            if caps is not None:
                caps.acceptance[cache_key] = decode_probe
            return None, decode_probe

    probe = Probe(
        ST_MEASURED, value=False,
        detail=f"no working transfer for {codec} {chroma}/{depth}bit on "
               f"{hwaccel}[{index}]: " + " | ".join(attempts),
    )
    if caps is not None:
        caps.acceptance[cache_key] = probe
    return None, probe


# Candidate order when several hardware routes are compatible. This is a
# decode-side policy and is deliberately **not** the encoder order
# (NVENC -> QSV -> x265): assuming one from the other would be unfounded.
# NVIDIA first because NVDEC measured wider coverage -- it accepts H.264
# 4:2:2 10-bit, which QSV refuses on this silicon.
_DEFAULT_HW_ORDER = ("cuda", "qsv")

# Probe fixture geometry. Must be in the fixture *name* so a fixture built at
# a different size can never be silently reused: cuvid enforces per-codec
# minimum dimensions, and a too-small clip makes every NVDEC probe report a
# bogus failure ("Video width 128 not within range").
_PROBE_GEOMETRY = "320x240"
_PROBE_DURATION = "d=0.4"


def _probe_fixture_name(codec: str, chroma: str, depth: int) -> str:
    return (
        f"_hwprobe_{codec}_{chroma.replace(':', '')}_{depth}"
        f"_{_PROBE_GEOMETRY}.mp4"
    )


def planar_format(chroma: str, depth: int) -> str | None:
    """Format an FFmpeg encoder can consume, or None when unmapped."""
    return _PLANAR.get((chroma or "", int(depth or 0)))


def surface_format(chroma: str, depth: int) -> str | None:
    """System-memory format a hardware surface downloads as."""
    return _SURFACE.get((chroma or "", int(depth or 0)))


def decoder_for(codec: str, hwaccel: str) -> str | None:
    return _DECODERS.get((codec or "").lower(), {}).get(hwaccel)


# --- capability snapshot ----------------------------------------------------

@dataclass
class HwDecodeCaps:
    """Machine-level hardware-decode capability, probed once per run.

    ``acceptance`` is keyed ``(hwaccel, device_index, codec, chroma, depth)``
    and only ever holds facts obtained from an actual decode attempt.
    """

    ffmpeg: str = ""
    ffmpeg_version: str = ""
    hwaccels: tuple[str, ...] = ()
    registered_decoders: tuple[str, ...] = ()
    devices: list[dict] = field(default_factory=list)
    acceptance: dict = field(default_factory=dict)
    #: (hwaccel, index, codec, chroma, depth) -> Transfer resolved by probe
    transfers: dict = field(default_factory=dict)
    raw_probe_log: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "ffmpeg": self.ffmpeg,
            "ffmpeg_version": self.ffmpeg_version,
            "hwaccels": list(self.hwaccels),
            "registered_decoders": list(self.registered_decoders),
            "devices": self.devices,
            "acceptance": [
                {"key": list(k), **v.to_json()}
                for k, v in sorted(caps_items(self.acceptance))
            ],
            "transfers": [
                {"key": list(k), "filters": v.filters, "format": v.format,
                 "direct": v.direct}
                for k, v in sorted(caps_items(self.transfers))
            ],
            "probe_log": self.raw_probe_log[-200:],
        }


def caps_items(d: dict):
    """Deterministic iteration for the caps JSON (keys are mixed types)."""
    return sorted(d.items(), key=lambda i: str(i[0]))


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except OSError as exc:
        return -1, f"OSError: {exc}"


def probe_hwaccels(ffmpeg: Path) -> tuple[Probe, tuple[str, ...]]:
    rc, out = _run([str(ffmpeg), "-hide_banner", "-hwaccels"])
    if rc != 0:
        return Probe(ST_PROBE_FAILED, detail=out[-300:]), ()
    # First line is the header "Hardware acceleration methods:".
    names = tuple(ln.strip() for ln in out.splitlines()[1:] if ln.strip())
    return Probe(ST_MEASURED, value=list(names)), names


def probe_registered_decoders(ffmpeg: Path) -> tuple[Probe, tuple[str, ...]]:
    rc, out = _run([str(ffmpeg), "-hide_banner", "-decoders"])
    if rc != 0:
        return Probe(ST_PROBE_FAILED, detail=out[-300:]), ()
    names = []
    for line in out.splitlines():
        m = re.match(r"\s*[VAS]\S*\s+(\S+)\s", line)
        if m:
            names.append(m.group(1))
    return Probe(ST_MEASURED, value=names), tuple(names)


def _device_spec(hwaccel: str, index: int) -> str:
    if hwaccel == "cuda":
        return f"cuda=cu:{index}"
    if hwaccel == "qsv":
        return f"qsv=qsv:{index}"
    if hwaccel == "d3d11va":
        return f"d3d11va=dev:{index}"
    return f"{hwaccel}={hwaccel}:{index}"


def probe_device(ffmpeg: Path, hwaccel: str, index: int = 0) -> Probe:
    """Can this hardware device be initialised at all?

    Uses a lavfi source so the answer is about the *device*, not about any
    particular media file.
    """
    spec = _device_spec(hwaccel, index)
    rc, out = _run([
        str(ffmpeg), "-hide_banner", "-v", "error", "-init_hw_device", spec,
        "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.04",
        "-frames:v", "1", "-f", "null", "-",
    ], timeout=60)
    if rc == 0:
        return Probe(ST_MEASURED, value=True, detail=f"device {spec} initialised")
    detail = " | ".join(ln for ln in out.splitlines() if ln.strip())[-300:]
    low = detail.lower()
    # No device present is a machine fact; anything else is a probe failure.
    if any(m in low for m in (
        "no device", "cannot load", "not found", "invalid device",
        "unsupported device", "device creation failed",
    )):
        return Probe(ST_UNAVAILABLE, value=False, detail=detail)
    return Probe(ST_PROBE_FAILED, value=None, detail=detail)


def _device_identity(hwaccel: str, index: int) -> dict:
    """Best-effort identity of the physical device behind an ffmpeg hwaccel.

    Capability facts are properties of a *device*, not of "NVIDIA" or of this
    program, so every acceptance record is stored against this. NVEncC's
    ``--check-features`` reports the adapter and driver without needing any
    media, which is the cheapest authoritative source available here; the
    vendor's own tool is preferred over inferring from the ffmpeg build.
    """
    ident: dict = {"hwaccel": hwaccel, "index": index}
    if hwaccel == "cuda":
        exe = _find_tool("NVEncC64.exe")
        if exe is not None:
            rc, out = _run([str(exe), "--check-features"], timeout=120)
            if rc == 0:
                for line in out.splitlines():
                    m = re.match(r"#(\d+):\s*(.+?)\[([^\]]*)\]\s*$", line.strip())
                    if m and int(m.group(1)) == index:
                        ident["name"] = m.group(2).split(" (")[0].strip()
                        ident["driver"] = m.group(3).strip()
                        break
    elif hwaccel == "qsv":
        exe = _find_tool("QSVEncC64.exe")
        if exe is not None:
            rc, out = _run([str(exe), "--check-features"], timeout=120)
            if rc == 0:
                m = re.search(r"GPU:\s*([^\r\n]+)", out)
                if m:
                    ident["name"] = m.group(1).strip()
                m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", out)
                if m:
                    ident["driver"] = m.group(1)
    return ident


def _find_tool(exe_name: str) -> Path | None:
    """Locate a bundled rigaya tool, or None.

    Kept local (and tolerant) so the decode capability probe never depends on
    the encoder backends being importable.
    """
    import shutil as _shutil

    try:
        from core.config import find_hw_tool
        return find_hw_tool(Path(__file__).resolve().parent.parent, exe_name)
    except Exception:                              # noqa: BLE001
        found = _shutil.which(exe_name)
        return Path(found) if found else None


def _ensure_fixture(
    ffmpeg: Path, codec: str, chroma: str, depth: int,
    work_dir: Path | None,
) -> Path | None:
    """Synthesise (or reuse) a clip in the exact (codec, chroma, depth).

    Returns None when the fixture cannot be built -- which means the
    combination cannot be *tested* here, never that hardware fails it.
    """
    target = planar_format(chroma, depth)
    if target is None:
        return None
    encoder = {"hevc": "libx265", "h264": "libx264", "av1": "libsvtav1"}.get(codec)
    if encoder is None:
        return None

    import tempfile

    tmpdir = Path(work_dir) if work_dir else Path(tempfile.gettempdir())
    try:
        tmpdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        tmpdir = Path(tempfile.gettempdir())
    fx = tmpdir / _probe_fixture_name(codec, chroma, depth)
    if fx.is_file():
        return fx

    gen = [
        str(ffmpeg), "-hide_banner", "-v", "error", "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=s={_PROBE_GEOMETRY}:r=5:{_PROBE_DURATION}",
        "-vf", f"format={target}",
        "-c:v", encoder, "-pix_fmt", target,
    ]
    if encoder == "libx265":
        gen += ["-x265-params", "log-level=error:keyint=5:min-keyint=5"]
    elif encoder == "libx264":
        gen += ["-preset", "ultrafast"]
    gen += [str(fx)]
    rc, _out = _run(gen, timeout=120)
    return fx if (rc == 0 and fx.is_file()) else None


def probe_acceptance(
    ffmpeg: Path, hwaccel: str, index: int, codec: str, chroma: str, depth: int,
    *, work_dir: Path | None = None,
) -> Probe:
    """Does this decoder actually accept this (codec, chroma, depth)?

    A registered decoder is not a working decoder, so this performs a real
    decode. The fixture is synthesised in the exact pixel format via
    ``lavfi -> encoder`` so the test needs no corpus media.

    This probe covers *decode only*. A working decode is not the same as a
    usable hardware route -- HEVC 4:2:2 10-bit decodes and then refuses every
    direct download format -- so ``plan_decode`` additionally requires
    ``resolve_transfer`` to succeed before it offers a hardware route.
    """
    decoder = decoder_for(codec, hwaccel)
    if decoder is None:
        return Probe(ST_UNAVAILABLE, value=False,
                     detail=f"no {hwaccel} decoder mapped for codec {codec!r}")
    target = planar_format(chroma, depth)
    if target is None:
        return Probe(ST_UNAVAILABLE, value=False,
                     detail=f"no planar format mapped for {chroma}/{depth}bit")

    fx = _ensure_fixture(ffmpeg, codec, chroma, depth, work_dir)
    if fx is None:
        return Probe(
            ST_UNAVAILABLE, value=False,
            detail=f"cannot synthesise a {codec} {chroma}/{depth}bit fixture",
        )

    spec = _device_spec(hwaccel, index)
    rc, out = _run([
        str(ffmpeg), "-hide_banner", "-v", "error", "-progress", "pipe:2",
        "-init_hw_device", spec,
        "-hwaccel", hwaccel,
        "-hwaccel_device", hwaccel,
        "-hwaccel_output_format", hwaccel,
        "-c:v", decoder,
        "-i", str(fx),
        "-frames:v", "2", "-f", "null", "-",
    ], timeout=120)

    # Collect the WHOLE output for classification. Keeping only the tail is a
    # real trap here: QSV prints "Error querying IO surface: unsupported (-3)"
    # FIRST and then floods hundreds of "get_buffer() failed" /
    # "Decoding error: Invalid argument" lines, so a tail-only classifier sees
    # a generic decode error and loses the capability refusal entirely.
    lines = [ln for ln in out.splitlines() if ln.strip()]
    full = " | ".join(lines)
    progress = re.findall(r"^frame=(\d+)$", out, re.M)
    decoded = int(progress[-1]) if progress else 0
    evidence = full[-600:]

    if rc == 0:
        return Probe(ST_MEASURED, value=True,
                     detail=f"{decoder} accepted {codec} {chroma}/{depth}bit "
                            f"({decoded} frames)")

    low = full.lower()
    refusal = any(marker in low for marker in (
        "unable to decode", "unsupported", "not implemented",
        "image parameters invalid", "invalid output format",
        "not supported",
    ))
    if refusal and decoded == 0:
        # Refused without producing a single frame == a capability fact about
        # (decoder, format) on this device, safe to remember.
        return Probe(
            ST_MEASURED, value=False,
            detail=f"{decoder} REFUSED {codec} {chroma}/{depth}bit "
                   f"(rc={rc}, 0 frames): {evidence[-260:]}",
        )
    if decoded > 0:
        # It started decoding and then broke: an error, not a capability
        # boundary, and must not be cached as "unsupported".
        return Probe(
            ST_PROBE_FAILED, value=None,
            detail=f"{decoder} decoded {decoded} frames then failed "
                   f"(rc={rc}): {evidence[-200:]}",
        )
    return Probe(
        ST_PROBE_FAILED, value=None,
        detail=f"{decoder} probe inconclusive (rc={rc}): {evidence[-200:]}",
    )


# --- planning ---------------------------------------------------------------

def plan_decode(
    *,
    caps: HwDecodeCaps | None,
    ffmpeg: Path | None,
    codec: str,
    chroma: str,
    depth: int,
    hw_order: tuple[str, ...] = _DEFAULT_HW_ORDER,
    probe_work_dir: Path | None = None,
    allow_probe: bool = True,
    memo: dict | None = None,
) -> DecodePlan:
    """Choose the decode route for one input. Hardware first, always.

    No parameter here knows about the encoder, Sony, DJI, metadata policy or
    ``--no-metadata``. Those are separate layers by construction.

    ``memo`` carries runtime-discovered facts across files in one run so a
    device/combination that failed during execution is not re-attempted for
    every subsequent file.
    """
    if ffmpeg is None or caps is None:
        return plan_software(
            "no_ffmpeg_caps",
            "FFmpeg capability snapshot unavailable; using software decode",
        )

    target = planar_format(chroma, depth)
    if target is None:
        return plan_software(
            "format_unmapped",
            f"{chroma}/{depth}bit has no mapped planar output format",
        )

    surface = surface_format(chroma, depth)
    if surface is None:
        return plan_software(
            "surface_unmapped",
            f"{chroma}/{depth}bit has no mapped hardware surface format",
        )

    memo = memo if memo is not None else {}
    rejections: list[str] = []

    for hwaccel in hw_order:
        if hwaccel not in caps.hwaccels:
            rejections.append(f"{hwaccel}: not built in")
            continue
        decoder = decoder_for(codec, hwaccel)
        if decoder is None:
            rejections.append(f"{hwaccel}: no decoder for {codec}")
            continue
        if decoder not in caps.registered_decoders:
            rejections.append(f"{hwaccel}: {decoder} not registered")
            continue

        for dev in caps.devices:
            if dev.get("hwaccel") != hwaccel:
                continue
            idx = int(dev.get("index", 0))
            init = dev.get("init", {})
            if init.get("status") != ST_MEASURED or not init.get("value"):
                rejections.append(
                    f"{hwaccel}[{idx}]: device init {init.get('status')}"
                )
                continue

            key = (hwaccel, idx, codec, chroma, int(depth))
            if memo.get(key):
                rejections.append(f"{hwaccel}[{idx}]: {memo[key][:60]}")
                continue

            probe = caps.acceptance.get(key)
            if probe is None and allow_probe:
                probe = probe_acceptance(
                    ffmpeg, hwaccel, idx, codec, chroma, depth,
                    work_dir=probe_work_dir,
                )
                caps.acceptance[key] = probe

            if probe is None:
                rejections.append(f"{hwaccel}[{idx}]: not probed")
                continue
            if probe.status == ST_PROBE_FAILED:
                rejections.append(
                    f"{hwaccel}[{idx}]: probe_failed ({probe.detail[:60]})"
                )
                continue
            if probe.status == ST_UNAVAILABLE:
                rejections.append(f"{hwaccel}[{idx}]: unavailable")
                continue
            if probe.value is not True:
                rejections.append(
                    f"{hwaccel}[{idx}]: {decoder} refused this combination"
                )
                continue

            # Decode was accepted; now establish a working surface -> CPU
            # transfer. Probing decode alone is not enough: HEVC 4:2:2 10-bit
            # decodes fine and then fails to download in every direct format,
            # so a decode-only probe reports a false positive.
            transfer = caps.transfers.get(key)
            transfer_probe = None
            if transfer is None:
                transfer, transfer_probe = resolve_transfer(
                    ffmpeg, hwaccel, idx, decoder, codec, chroma, depth,
                    work_dir=probe_work_dir, caps=caps,
                )
            if transfer is None:
                rejections.append(
                    f"{hwaccel}[{idx}]: no working frame transfer"
                    + (f" ({transfer_probe.detail[:70]})"
                       if transfer_probe is not None else "")
                )
                continue

            plan = DecodePlan(
                substrate=SUBSTRATE_FFMPEG_HW,
                decoder=decoder, hwaccel=hwaccel,
                device=_device_spec(hwaccel, idx), device_index=idx,
                surface_format=surface, target_format=target,
                transfer=transfer,
                reason="hardware_compatible",
                detail=f"{decoder} accepted {codec} {chroma}/{depth}bit "
                       f"on device {idx}; {transfer.log_line()}",
            )
            if not transfer.direct:
                plan.warnings.append(
                    f"hardware decode needs a device-side format conversion "
                    f"({' ,'.join(transfer.filters)}) before encoding"
                )
            if transfer.format != target:
                plan.needs_format_conversion = True
            return plan

    return plan_software(
        "no_compatible_hw_decoder",
        f"{codec} {chroma}/{depth}bit: no compatible hardware decoder "
        f"({'; '.join(rejections) if rejections else 'no candidates'})",
    )


def build_caps(
    ffmpeg: Path,
    *,
    hw_order: tuple[str, ...] = _DEFAULT_HW_ORDER,
    device_indices: tuple[int, ...] = (0,),
    logger=None,
) -> HwDecodeCaps:
    """Probe machine-level hardware-decode capability once per run."""
    caps = HwDecodeCaps(ffmpeg=str(ffmpeg))

    rc, out = _run([str(ffmpeg), "-hide_banner", "-version"])
    if rc == 0:
        lines = out.splitlines()
        caps.ffmpeg_version = lines[0].strip() if lines else ""
        caps.raw_probe_log.append(caps.ffmpeg_version)

    ha_probe, hwaccels = probe_hwaccels(ffmpeg)
    caps.hwaccels = hwaccels
    caps.raw_probe_log.append(f"hwaccels: {ha_probe.status} {list(hwaccels)}")

    dec_probe, decoders = probe_registered_decoders(ffmpeg)
    caps.registered_decoders = decoders
    caps.raw_probe_log.append(
        f"registered decoders: {dec_probe.status} ({len(decoders)} total)"
    )

    for hwaccel in hw_order:
        if hwaccel not in hwaccels:
            caps.devices.append({
                "hwaccel": hwaccel, "index": 0,
                "init": Probe(ST_UNAVAILABLE, False,
                              f"{hwaccel} not in -hwaccels").to_json(),
            })
            continue
        for idx in device_indices:
            p = probe_device(ffmpeg, hwaccel, idx)
            entry = {
                "hwaccel": hwaccel, "index": idx, "init": p.to_json(),
            }
            if p.status == ST_MEASURED and p.value:
                entry.update(_device_identity(hwaccel, idx))
            caps.devices.append(entry)
            if logger is not None:
                logger.info(
                    "[DECODE CAPS] device %s[%d]: %s %s %s",
                    hwaccel, idx, p.status,
                    entry.get("name", "?"), p.detail[:80],
                )
    return caps


def save_caps(caps: HwDecodeCaps, caps_dir: Path) -> tuple[Path, Path]:
    """Persist the snapshot so it is auditable alongside the rigaya caps."""
    caps_dir.mkdir(parents=True, exist_ok=True)
    json_path = caps_dir / "ffmpeg_decode_caps.json"
    raw_path = caps_dir / "ffmpeg_decode_caps_raw.txt"
    json_path.write_text(
        json.dumps(caps.to_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raw_path.write_text(
        "\n".join(caps.raw_probe_log) + "\n", encoding="utf-8", errors="replace",
    )
    return json_path, raw_path


# --- runtime failure handling -----------------------------------------------

def record_runtime_failure(
    memo: dict, plan: DecodePlan, codec: str, chroma: str, depth: int,
    reason: str,
) -> None:
    """Remember a runtime failure for the rest of this run.

    Called only for failures that are properties of the *device/combination*
    rather than of one file (device init, frame transfer, decoder startup).
    A per-file decode error must not disable the route for other files.
    """
    if not plan.hardware:
        return
    key = (plan.hwaccel, plan.device_index, codec, chroma, int(depth))
    memo[key] = reason


# --- applying a plan to an existing argv ------------------------------------

def apply_decode_plan(
    cmd: list[str], plan: DecodePlan, source: Path | str
) -> list[str]:
    """Splice a decode plan into an already-built FFmpeg argv.

    The encoder backends build argv without any decode knowledge (that is the
    decoupling this redesign is for), so the plan is applied here instead of
    being threaded through every backend signature -- which would also change
    the contract `tests/selftest/suites/codecs.py` already pins.

    Insertion points:
      * hardware device/decoder args go immediately **before** the ``-i`` that
        names ``source``;
      * the ``hwdownload``/``format`` filter goes right after that input, and
        is merged into an existing ``-vf`` if the backend already set one
        (the x265 video-only command does not, but a future backend might).

    A software plan is a no-op, so callers can apply unconditionally.
    """
    if not plan.hardware:
        return list(cmd)

    src_texts = {str(source)}
    try:
        src_texts.add(str(Path(source).resolve()))
    except OSError:
        pass

    out = list(cmd)
    idx = None
    for i, tok in enumerate(out):
        if tok == "-i" and i + 1 < len(out) and out[i + 1] in src_texts:
            idx = i
            break
    if idx is None:
        # Cannot place the args safely. Refusing to guess is the only correct
        # behaviour: silently dropping the hardware args would produce a
        # software decode that the log still calls "hardware".
        raise ValueError(
            f"apply_decode_plan: no '-i {source}' found in argv; refusing to "
            f"guess where the hardware decode arguments belong"
        )

    insert = plan.ffmpeg_input_args()
    out[idx:idx] = insert
    after = idx + len(insert) + 2          # past "-i", source

    filt = plan.ffmpeg_filter_args()
    if filt:
        existing = None
        for j in range(after, len(out) - 1):
            if out[j] == "-vf":
                existing = j
                break
        if existing is None:
            out[after:after] = filt
        else:
            # Merge: keep the backend's chain and append ours so the surface is
            # downloaded first, then the encoder's own filtering runs.
            out[existing + 1] = f"{out[existing + 1]},{filt[1]}"
    return out

