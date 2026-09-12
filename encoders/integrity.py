"""Frame-integrity gate for hardware decode.

The gate exists because the failure this project actually hit was
**silent**: the stock hardware reader delivered `N − 3` frames with
`rc = 0`, no error text and a perfectly playable file.  A gate that only
checks "did the process succeed" would have passed it, and did.

Two levels, both required by the integration test matrix:

``count`` (always on when hardware decode is enabled)
    Five-source reconciliation plus reader identity.  Catches frame loss
    and truncation — the measured defect class — at the cost of one
    container parse and two decode passes over the *output*.

``sequence`` (opt-in via ``--hw-decode-verify``)
    Ordered per-frame fingerprint comparison between the hardware and
    software results.  Catches count-preserving corruption (reordering,
    substitution, duplication) that no counting gate can see.  Costs a
    second encode, so it is not the default.

The rule the gate enforces is the research line's own conclusion:
**reconcile against the delivered container, never against a decoder's
or reader's self-report.**  The reader's ``N frames`` line is recorded as
evidence but is never the reference — on XAVC it under-reports by
construction (``FramePosList::setPocAndFix``), so trusting it would mask
exactly the loss we are looking for.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .hwdecode import (
    R_COUNT_MISMATCH, R_INTEGRITY_OK, R_READER_UNAVAILABLE,
    R_SEQUENCE_MISMATCH,
)


# ---------------------------------------------------------------------------
# container reference count — read straight from the file bytes
# ---------------------------------------------------------------------------


def _iter_boxes(data: bytes, start: int, end: int):
    off = start
    while off + 8 <= end:
        size = struct.unpack_from(">I", data, off)[0]
        btype = data[off + 4:off + 8]
        hdr = 8
        if size == 1:
            if off + 16 > end:
                return
            size = struct.unpack_from(">Q", data, off + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            return
        yield btype, off + hdr, off + size
        off += size


def _find(data: bytes, start: int, end: int, path: list[bytes]):
    if not path:
        return (start, end)
    for btype, ps, pe in _iter_boxes(data, start, end):
        if btype == path[0]:
            if len(path) == 1:
                return (ps, pe)
            got = _find(data, ps, pe, path[1:])
            if got is not None:
                return got
    return None


def _moov_bytes(path: Path) -> bytes | None:
    with path.open("rb") as f:
        f.seek(0, 2)
        size_file = f.tell()
        off = 0
        while off + 8 <= size_file:
            f.seek(off)
            hdr = f.read(16)
            if len(hdr) < 8:
                return None
            size = struct.unpack_from(">I", hdr, 0)[0]
            btype = hdr[4:8]
            hdr_len = 8
            if size == 1:
                if len(hdr) < 16:
                    return None
                size = struct.unpack_from(">Q", hdr, 8)[0]
                hdr_len = 16
            elif size == 0:
                size = size_file - off
            if size < hdr_len:
                return None
            if btype == b"moov":
                f.seek(off)
                return f.read(size)
            off += size
    return None


def container_video_samples(path: Path) -> tuple[int | None, str]:
    """Video sample count from the container's own ``stsz`` box.

    Returns ``(count, method)``.  ``method`` is ``"isobmff-stsz"`` when
    the file was parsed here, ``"ffprobe-packets"`` when the input is not
    ISOBMFF and libavformat had to be asked (MKV), and ``"none"`` when
    neither worked.
    """
    path = Path(path)
    try:
        data = _moov_bytes(path)
    except OSError:
        data = None
    if data:
        moov = _find(data, 0, len(data), [b"moov"])
        if moov is not None:
            for btype, ps, pe in _iter_boxes(data, moov[0], moov[1]):
                if btype != b"trak":
                    continue
                hdlr = _find(data, ps, pe, [b"mdia", b"hdlr"])
                if hdlr is None:
                    continue
                hps = hdlr[0]
                if data[hps + 8:hps + 12] != b"vide":
                    continue
                stbl = _find(data, ps, pe, [b"mdia", b"minf", b"stbl"])
                if stbl is None:
                    continue
                stsz = _find(data, stbl[0], stbl[1], [b"stsz"])
                if stsz is None:
                    continue
                sps = stsz[0]
                if sps + 12 > len(data):
                    continue
                return struct.unpack_from(">I", data, sps + 8)[0], "isobmff-stsz"
    # non-ISOBMFF fallback
    n = _ffprobe_packet_count(path)
    if n is not None:
        return n, "ffprobe-packets"
    return None, "none"


def _ffprobe_packet_count(path: Path, ffprobe: Path | None = None,
                          timeout: int = 3600) -> int | None:
    exe = ffprobe or _default_ffprobe()
    if exe is None:
        return None
    cmd = [
        str(exe), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-print_format", "json",
        "-i", str(path),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        streams = json.loads(p.stdout.decode("utf-8", "replace")).get(
            "streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    try:
        return int(streams[0]["nb_read_packets"])
    except (KeyError, TypeError, ValueError):
        return None


def _default_ffprobe() -> Path | None:
    p = Path(__file__).resolve().parents[1] / "tools" / "ffprobe.exe"
    return p if p.is_file() else None


def _default_ffmpeg() -> Path | None:
    p = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg.exe"
    return p if p.is_file() else None


def independent_decoded_frames(path: Path, ffprobe: Path | None = None,
                               timeout: int = 7200) -> int | None:
    """Frames an independent decoder produces from ``path``."""
    exe = ffprobe or _default_ffprobe()
    if exe is None:
        return None
    cmd = [
        str(exe), "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-print_format", "json",
        "-i", str(path),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        streams = json.loads(p.stdout.decode("utf-8", "replace")).get(
            "streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    try:
        return int(streams[0]["nb_read_frames"])
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# log parsing (shared with the matrix harness by contract, not by import)
# ---------------------------------------------------------------------------

_INPUT_INFO_RE = re.compile(
    r"Input\s+Info\s+(?P<r>[A-Za-z][A-Za-z0-9_]*)\s*:", re.IGNORECASE
)
_ENCODED_RE = re.compile(r"encoded\s+(\d+)\s+frames", re.IGNORECASE)
_READN_RE = re.compile(r"^\s*(\d+)\s+frames,\s*End of file", re.MULTILINE)

READER_TOKENS = (
    "avcuvid", "avqsv", "avsw", "avhw",
    "d3d11va", "d3d11", "dxva2", "cuda", "vulkan",
)


def reader_identity_from_log(log_text: str) -> str | None:
    text = log_text or ""
    m = _INPUT_INFO_RE.search(text)
    if m:
        return m.group("r").lower()
    for line in text.splitlines():
        stripped = line.strip().lower()
        for tok in READER_TOKENS:
            if stripped.startswith(tok + ":"):
                return tok
    return None


def encoder_input_frames(log_text: str) -> int | None:
    m = _ENCODED_RE.search(log_text or "")
    return int(m.group(1)) if m else None


def reader_reported_frames(log_text: str) -> int | None:
    m = _READN_RE.search(log_text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


@dataclass
class IntegrityVerdict:
    ok: bool
    reason: str
    detail: str
    counts: dict = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    def log_line(self) -> str:
        state = "OK" if self.ok else "FAILED"
        return (
            f"integrity gate [{state}] reason={self.reason} "
            f"counts={self.counts} {self.detail}"
        )


def verify_encode(
    *,
    source: Path,
    output: Path | None,
    log_text: str,
    rc: int,
    reader_requested: str,
    reader_expected_identity: str,
    encode_completed: bool = True,
) -> IntegrityVerdict:
    """Count-level integrity gate for one hardware-decode encode.

    ``reader_expected_identity`` is what the tool must print in its
    ``Input Info`` line if it really built the hardware reader
    (``avcuvid`` for NVEncC, ``avqsv`` for QSVEncC).  A mismatch is a
    **silent software fallback** and fails the gate: treating it as a
    hardware pass is the single easiest way to fake this whole feature.
    """
    counts: dict = {"tool_rc": rc, "reader_requested": reader_requested}
    identity = reader_identity_from_log(log_text)
    counts["reader_identity"] = identity
    counts["reader_reported"] = reader_reported_frames(log_text)
    counts["encoder_input"] = encoder_input_frames(log_text)

    if rc != 0:
        return IntegrityVerdict(
            False, "decode_failed",
            f"tool exited rc={rc}", counts,
            actions=["fallback_to_software"],
        )
    if not encode_completed:
        return IntegrityVerdict(
            False, "decode_failed", "encoder did not report completion",
            counts, actions=["fallback_to_software"],
        )

    if identity != reader_expected_identity:
        return IntegrityVerdict(
            False, R_READER_UNAVAILABLE,
            f"requested {reader_requested} but the tool built "
            f"{identity!r} (expected {reader_expected_identity!r}) — "
            f"silent software fallback must not be recorded as a "
            f"hardware pass",
            counts, actions=["fallback_to_software", "mark_reader_unavailable"],
        )

    if output is None or not Path(output).is_file():
        return IntegrityVerdict(
            False, "decode_failed", "no output file produced", counts,
            actions=["fallback_to_software"],
        )
    out = Path(output)
    if out.stat().st_size == 0:
        return IntegrityVerdict(
            False, "decode_failed", "output file is empty", counts,
            actions=["fallback_to_software"],
        )

    expected, method = container_video_samples(Path(source))
    counts["container_expected"] = expected
    counts["container_method"] = method
    counts["output_stream"] = container_video_samples(out)[0] if _is_isobmff(out) else None
    counts["independent_decoded"] = independent_decoded_frames(out)

    if expected is None:
        return IntegrityVerdict(
            False, R_COUNT_MISMATCH,
            "no container reference count available for the source; "
            "refusing to certify an unverifiable hardware result",
            counts, actions=["fallback_to_software"],
        )

    mismatched = []
    for label in ("encoder_input", "output_stream", "independent_decoded"):
        v = counts.get(label)
        if v is None:
            mismatched.append(f"{label}=unavailable")
        elif v != expected:
            mismatched.append(f"{label}={v}")

    if mismatched:
        reader_note = ""
        if counts.get("reader_reported") is not None:
            reader_note = (
                f" (reader self-report={counts['reader_reported']}, which is "
                f"not trusted as a reference)"
            )
        return IntegrityVerdict(
            False, R_COUNT_MISMATCH,
            f"container expects {expected} samples but {', '.join(mismatched)}"
            f"{reader_note}",
            counts,
            actions=["discard_hardware_result", "fallback_to_software"],
        )

    return IntegrityVerdict(
        True, R_INTEGRITY_OK,
        f"all counts reconcile at {expected} ({method}); reader={identity}",
        counts,
    )


def _is_isobmff(path: Path) -> bool:
    try:
        with Path(path).open("rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] in (b"ftyp", b"moov", b"styp")


def verify_sequence(
    *,
    hw_output: Path,
    sw_output: Path,
    width: int,
    height: int,
    pix_fmt: str = "yuv420p10le",
    limit: int | None = None,
    ffmpeg: Path | None = None,
) -> IntegrityVerdict:
    """Ordered fingerprint comparison of a hardware vs software result.

    This is the check a counting gate cannot make: it compares the
    *pictures* position-for-position, so a count-preserving reorder,
    substitution or duplication is caught.  Used by
    ``--hw-decode-verify`` and by matrix test HD-C12.
    """
    a, err_a = frame_signatures(hw_output, width, height, pix_fmt=pix_fmt,
                                limit=limit, ffmpeg=ffmpeg)
    b, err_b = frame_signatures(sw_output, width, height, pix_fmt=pix_fmt,
                                limit=limit, ffmpeg=ffmpeg)
    counts = {
        "hw_frames": len(a), "sw_frames": len(b),
        "hw_error": err_a, "sw_error": err_b,
        "hw_digest": digest(a), "sw_digest": digest(b),
    }
    if err_a or err_b:
        return IntegrityVerdict(
            False, R_SEQUENCE_MISMATCH,
            f"fingerprint extraction failed (hw={err_a}, sw={err_b})",
            counts, actions=["fallback_to_software"],
        )
    if len(a) != len(b):
        return IntegrityVerdict(
            False, R_SEQUENCE_MISMATCH,
            f"frame count differs: hardware {len(a)} vs software {len(b)}",
            counts, actions=["discard_hardware_result", "fallback_to_software"],
        )
    n = min(len(a), len(b))
    first = None
    for i in range(n):
        if a[i] != b[i]:
            first = i
            break
    if first is not None:
        return IntegrityVerdict(
            False, R_SEQUENCE_MISMATCH,
            f"ordered picture sequence differs at index {first} "
            f"(hw {a[first][:6]} vs sw {b[first][:6]})",
            counts, actions=["discard_hardware_result", "fallback_to_software"],
        )
    return IntegrityVerdict(
        True, R_INTEGRITY_OK,
        f"{n} frames identical position-for-position "
        f"(digest {counts['hw_digest'][:16]})",
        counts,
    )


# ---------------------------------------------------------------------------
# fingerprint primitives (production copy)
#
# Deliberately a separate implementation from the matrix harness's
# tests/hwdecode/checks.py: the harness is the oracle, and an oracle that
# shares code with the thing it judges is not an oracle.
# ---------------------------------------------------------------------------

_CHUNK = 4096
_CHUNK_FRACS = (0.02, 0.27, 0.51, 0.79)
_Y_STRIDE = 977
_C_STRIDE = 331


def _iter_decoded(path: Path, w: int, h: int, pix_fmt: str, ffmpeg: Path):
    fsz = w * h * 3 if pix_fmt.startswith("yuv420") else w * h * 2
    cmd = [
        str(ffmpeg), "-v", "error", "-nostdin", "-i", str(path),
        "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=fsz)
    try:
        while True:
            buf = proc.stdout.read(fsz)
            if not buf or len(buf) < fsz:
                break
            yield buf
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def frame_signatures(
    path: Path, w: int, h: int, *, pix_fmt: str = "yuv420p10le",
    limit: int | None = None, ffmpeg: Path | None = None,
) -> tuple[list[list], str | None]:
    """Streaming per-frame signature; never writes raw frames to disk."""
    exe = ffmpeg or _default_ffmpeg()
    if exe is None:
        return [], "ffmpeg not found"
    ysz = w * h * 2
    csz = (w // 2) * (h // 2) * 2
    y_idx = list(range(0, max(ysz - 1, 1), 2 * _Y_STRIDE))
    c_idx = list(range(0, max(csz - 1, 1), 2 * _C_STRIDE))
    chunk_offsets = [
        min(int(ysz * f) & ~1, max(ysz - _CHUNK - 1, 0)) for f in _CHUNK_FRACS
    ]
    sigs: list[list] = []
    err: str | None = None
    try:
        for buf in _iter_decoded(Path(path), w, h, pix_fmt, exe):
            ys = [buf[i] | (buf[i + 1] << 8) for i in y_idx]
            us = [buf[ysz + i] | (buf[ysz + i + 1] << 8) for i in c_idx]
            vs = [buf[ysz + csz + i] | (buf[ysz + csz + i + 1] << 8)
                  for i in c_idx]
            ny = len(ys)
            d = sum(abs(ys[i + 1] - ys[i]) for i in range(ny - 1)) / max(ny - 1, 1)
            cd = hashlib.sha256()
            for off in chunk_offsets:
                cd.update(buf[off:off + _CHUNK])
            sigs.append([
                round(sum(ys) / ny, 3), round(sum(us) / len(us), 3),
                round(sum(vs) / len(vs), 3), round(d, 3),
                ys[0], ys[-1], cd.hexdigest()[:16],
            ])
            if limit and len(sigs) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    return sigs, err


def digest(sigs: list[list]) -> str:
    return hashlib.sha256(
        json.dumps(sigs, separators=(",", ":")).encode()
    ).hexdigest()
