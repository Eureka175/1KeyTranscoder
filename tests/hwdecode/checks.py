"""Verification primitives for the hardware-decode matrix.

Everything here is *measurement*, not policy: counts, fingerprints,
metadata, ordering.  The tests decide what a measurement means.

The frame fingerprint is adapted from the research line's streaming
implementation (``hwdecode2/fingerprint.py``), which exists because a
4K 10-bit raw dump is ~206 GB per pass for a 10 170-frame clip — it
filled a drive during the original work.  The rule it established and
this module keeps: **never write raw frames to disk; reduce to a
fixed-size signature as the pipe streams past.**

Two independent measurement families are provided:

``count_manifest`` (cheap, five sources)
    container samples · reader self-report · encoder input ·
    output stream packets · independently decoded output frames

``frame_signatures`` (expensive, decisive)
    per-frame ordered signature of the *decoded pictures*, used to prove
    picture identity position-for-position — the check that a count
    comparison cannot make.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator

from .probe import FFPROBE, FFMPEG, probe_input

# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# five-source count reconciliation
# ---------------------------------------------------------------------------


@dataclass
class CountManifest:
    """Every frame count available about one encode, with its source."""

    input_id: str = ""
    # 1. container-declared samples in the source's video track
    container_expected: int | None = None
    # 2. what the decoder/reader claimed it read (tool log self-report;
    #    known to under-report on XAVC — FramePosList::setPocAndFix)
    reader_reported: int | None = None
    # 3. what the encoder says it consumed
    encoder_input: int | None = None
    # 4. packets in the produced file, via the container's own tables
    output_stream: int | None = None
    # 5. frames an independent decoder actually produced from the output
    independent_decoded: int | None = None
    # 5b. second, differently-implemented independent decode count
    independent_decoded_alt: int | None = None
    # extras
    reader_identity: str | None = None
    tool_rc: int | None = None
    source_container: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


_READER_TOKENS = (
    "avcuvid", "avqsv", "avsw", "avhw",
    "d3d11va", "d3d11", "dxva2", "cuda", "vulkan",
)

# NVEncC: "Input Info     avcuvid: H.265/HEVC, 3840x2160, 60000/1001 fps"
# QSVEncC: "Input Info     avqsv: H.265/HEVC, ..."
# software: "Input Info     avsw: hevc(yuv420p10le)->p010 [AVX2]"
_INPUT_INFO_RE = re.compile(
    r"Input\s+Info\s+(?P<r>[A-Za-z][A-Za-z0-9_]*)\s*:", re.IGNORECASE
)


def reader_identity_from_log(log_text: str) -> str | None:
    """Which decoder did the tool actually construct?

    Never infer this from the command line alone (HD-A03): rigaya tools
    silently fall back to ``avsw`` when the requested hardware reader is
    unavailable (QSVEncC does this without any error), and a
    software-fallback run mistaken for a hardware run would make every
    downstream test meaningless.

    The reliable site is the ``Input Info`` line, where both tools print
    the reader they built next to the codec they resolved.  The fallback
    scan only accepts a known reader token followed by a colon at the
    start of a value position, so an error message mentioning ``avhw``
    cannot be mistaken for a constructed reader.
    """
    text = log_text or ""
    m = _INPUT_INFO_RE.search(text)
    if m:
        return m.group("r").lower()
    for line in text.splitlines():
        stripped = line.strip()
        for tok in _READER_TOKENS:
            if stripped.lower().startswith(tok + ":"):
                return tok
    return None


_ENCODED_RE = re.compile(r"encoded\s+(\d+)\s+frames", re.IGNORECASE)
_READN_RE = re.compile(r"^\s*(\d+)\s+frames,\s*End of file", re.MULTILINE)


def encoder_input_from_log(log_text: str) -> int | None:
    """``encoded N frames`` — the encoder's own input count."""
    m = _ENCODED_RE.search(log_text or "")
    return int(m.group(1)) if m else None


def reader_reported_from_log(log_text: str) -> int | None:
    """The reader's ``N frames, End of file`` self-report (unreliable)."""
    m = _READN_RE.search(log_text or "")
    return int(m.group(1)) if m else None


def count_manifest(
    *,
    input_id: str,
    source: Path,
    output: Path | None,
    log_text: str = "",
    rc: int | None = None,
    source_container: int | None = None,
) -> CountManifest:
    cm = CountManifest(input_id=input_id, tool_rc=rc)
    cm.reader_identity = reader_identity_from_log(log_text)
    cm.reader_reported = reader_reported_from_log(log_text)
    cm.encoder_input = encoder_input_from_log(log_text)

    if source_container is None:
        try:
            source_container = probe_input(source).container_samples
        except Exception as exc:  # noqa: BLE001
            cm.notes.append(f"source probe failed: {exc}")
    cm.container_expected = source_container
    cm.source_container = source_container

    if output is not None and Path(output).is_file():
        cm.output_stream = output_packet_count(Path(output))
        cm.independent_decoded = independent_frame_count(Path(output))
        cm.independent_decoded_alt = independent_frame_count_ffmpeg(Path(output))
    return cm


def output_packet_count(path: Path) -> int | None:
    """Video packets in the produced file (container's own tables)."""
    from .probe import read_container

    try:
        cf = read_container(path)
    except Exception:  # noqa: BLE001
        cf = None
    if cf is not None and cf.ok and cf.video is not None:
        if cf.video.sample_count is not None:
            return cf.video.sample_count
    cmd = [
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-print_format", "json", "-i", str(path),
    ]
    try:
        p = run(cmd, timeout=1800)
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    try:
        streams = json.loads(p.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    try:
        return int(streams[0]["nb_read_packets"])
    except (KeyError, TypeError, ValueError):
        return None


def independent_frame_count(path: Path, timeout: int = 7200) -> int | None:
    """Frames an *independent decoder* actually produces from ``path``.

    Primary method is ``ffprobe -count_frames``: libavcodec decodes the
    stream and the count reported is decoded output, not the container's
    claim.  It is a genuinely different implementation from the rigaya
    tools and from this harness's own ISO-BMF parse.

    See :func:`independent_frame_count_ffmpeg` for the second,
    independent method used as a cross-check (HD-C08).
    """
    cmd = [
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries", "stream=nb_read_frames",
        "-print_format", "json", "-i", str(path),
    ]
    try:
        p = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    try:
        streams = json.loads(p.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    try:
        return int(streams[0]["nb_read_frames"])
    except (KeyError, TypeError, ValueError):
        return None


def independent_frame_count_ffmpeg(path: Path, timeout: int = 7200) -> int | None:
    """Second independent decode count, via FFmpeg's progress stream.

    ``-progress pipe:1`` emits ``frame=N`` in key=value form on stdout
    even under ``-v error``, so this counts the frames FFmpeg's own
    decoder pipeline actually pushed through — a different code path
    from ffprobe's ``-count_frames`` and from any container table.
    """
    cmd = [
        str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
        "-map", "0:v:0", "-f", "null", "-", "-progress", "pipe:1",
    ]
    try:
        p = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    last = None
    for line in (p.stdout or "").splitlines():
        if line.startswith("frame="):
            try:
                last = int(line.split("=", 1)[1])
            except (IndexError, ValueError):
                continue
    return last


def reconcile(cm: CountManifest) -> tuple[bool, list[str]]:
    """Do all available counts agree?

    Returns ``(ok, reasons)``.  The container's declared sample count is
    the reference where it exists — it is the number the *file* claims,
    which is what a delivered artifact must contain.  The reader's
    self-report is recorded but **never** used as the reference: it
    under-reports on XAVC by construction (RC-3).
    """
    reasons: list[str] = []
    ref = cm.container_expected
    if ref is None:
        reasons.append("no container reference count available")
        return False, reasons

    for label, value in (
        ("encoder_input", cm.encoder_input),
        ("output_stream", cm.output_stream),
        ("independent_decoded", cm.independent_decoded),
        ("independent_decoded_alt", cm.independent_decoded_alt),
    ):
        if value is None:
            reasons.append(f"{label} unavailable")
        elif value != ref:
            reasons.append(f"{label}={value} != container={ref}")
    return not reasons, reasons


# ---------------------------------------------------------------------------
# ordered frame fingerprint
# ---------------------------------------------------------------------------

_CHUNK = 4096
_CHUNK_FRACS = (0.02, 0.27, 0.51, 0.79)
_Y_STRIDE = 977      # odd, coprime with common widths -> spreads across rows
_C_STRIDE = 331


def _iter_decoded(path: Path, w: int, h: int, pix_fmt: str) -> Iterator[bytes]:
    fsz = w * h * 3 if pix_fmt.startswith("yuv420") else w * h * 2
    cmd = [
        str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
        "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fsz
    )
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


def _iter_raw_file(path: Path, w: int, h: int, pix_fmt: str) -> Iterator[bytes]:
    fsz = w * h * 3 if pix_fmt.startswith("yuv420") else w * h * 2
    with Path(path).open("rb") as f:
        while True:
            buf = f.read(fsz)
            if len(buf) < fsz:
                break
            yield buf


def frame_signatures(
    path: Path,
    w: int,
    h: int,
    *,
    pix_fmt: str = "yuv420p10le",
    limit: int | None = None,
    expect_frames: int | None = None,
) -> tuple[list[list], str | None]:
    """Ordered per-frame signatures of the decoded pictures.

    Signature per frame is ``[meanY, meanU, meanV, mean|dY|, firstY,
    lastY, chunk_digest16]``.  The chunk digest over four strided 4 KB
    windows of the luma plane makes two different pictures colliding
    practically impossible while staying cheap.
    """
    ysz = w * h * 2
    csz = (w // 2) * (h // 2) * 2
    y_idx = list(range(0, max(ysz - 1, 1), 2 * _Y_STRIDE))
    c_idx = list(range(0, max(csz - 1, 1), 2 * _C_STRIDE))
    chunk_offsets = [
        min(int(ysz * f) & ~1, max(ysz - _CHUNK - 1, 0)) for f in _CHUNK_FRACS
    ]
    sigs: list[list] = []
    err: str | None = None
    it = (
        _iter_raw_file(path, w, h, pix_fmt)
        if Path(path).suffix.lower() == ".yuv"
        else _iter_decoded(path, w, h, pix_fmt)
    )
    try:
        for buf in it:
            ys = [buf[i] | (buf[i + 1] << 8) for i in y_idx]
            us = [buf[ysz + i] | (buf[ysz + i + 1] << 8) for i in c_idx]
            vs = [buf[ysz + csz + i] | (buf[ysz + csz + i + 1] << 8) for i in c_idx]
            ny = len(ys)
            d = sum(abs(ys[i + 1] - ys[i]) for i in range(ny - 1)) / max(ny - 1, 1)
            cd = hashlib.sha256()
            for off in chunk_offsets:
                cd.update(buf[off:off + _CHUNK])
            sigs.append([
                round(sum(ys) / ny, 3),
                round(sum(us) / len(us), 3),
                round(sum(vs) / len(vs), 3),
                round(d, 3),
                ys[0],
                ys[-1],
                cd.hexdigest()[:16],
            ])
            if limit and len(sigs) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    if err is None and expect_frames and not limit and len(sigs) != expect_frames:
        err = f"decoded {len(sigs)} frames, expected {expect_frames}"
    return sigs, err


def digest(sigs: list[list]) -> str:
    return hashlib.sha256(
        json.dumps(sigs, separators=(",", ":")).encode()
    ).hexdigest()


def compare_signatures(a: list[list], b: list[list]) -> dict:
    """Position-by-position comparison of two ordered signature lists."""
    n = min(len(a), len(b))
    first = None
    maxdev = 0.0
    for i in range(n):
        if a[i] == b[i]:
            continue
        dev = max(
            (abs(float(x) - float(y)) for x, y in zip(a[i][:6], b[i][:6])),
            default=0.0,
        )
        maxdev = max(maxdev, dev)
        if first is None:
            first = i
    return {
        "compared": n,
        "len_a": len(a),
        "len_b": len(b),
        "identical": (a[:n] == b[:n]) and len(a) == len(b),
        "first_diff_index": first,
        "max_numeric_deviation": round(maxdev, 4),
    }


# ---------------------------------------------------------------------------
# packet / timing evidence
# ---------------------------------------------------------------------------


def packet_table(path: Path, limit: int | None = None) -> list[dict]:
    """Ordered (pts, dts, flags, size) for the video packets."""
    cmd = [
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts,dts,pts_time,dts_time,flags,size",
        "-print_format", "json", "-i", str(path),
    ]
    if limit:
        cmd = cmd[:1] + ["-read_intervals", f"%+#{limit}"] + cmd[1:]
    try:
        p = run(cmd, timeout=3600)
    except subprocess.TimeoutExpired:
        return []
    if p.returncode != 0:
        return []
    try:
        return json.loads(p.stdout).get("packets", [])
    except json.JSONDecodeError:
        return []


def packet_manifest(path: Path, limit: int | None = None) -> dict:
    pk = packet_table(path, limit=limit)
    return {
        "count": len(pk),
        "pts": [p.get("pts") for p in pk],
        "dts": [p.get("dts") for p in pk],
        "flags": [p.get("flags") for p in pk],
        "sizes": [p.get("size") for p in pk],
        "keyframe_indices": [
            i for i, p in enumerate(pk) if "K" in (p.get("flags") or "")
        ],
        "sha256": hashlib.sha256(
            json.dumps(
                [
                    (p.get("pts"), p.get("dts"), p.get("flags"), p.get("size"))
                    for p in pk
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }


def pts_monotonic(path: Path) -> tuple[bool, str]:
    """Are presentation timestamps strictly increasing?"""
    pk = packet_table(path)
    times = []
    for p in pk:
        t = p.get("pts_time") or p.get("dts_time")
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    if len(times) < 2:
        return False, "not enough timestamps"
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            return False, f"non-monotonic at index {i}: {times[i-1]} -> {times[i]}"
    return True, "monotonic"


def parse_tool_log(text: str) -> dict:
    """Structured facts pulled out of a rigaya tool log."""
    out: dict = {}
    m = re.search(r"encoded\s+(\d+)\s+frames,\s*([\d.]+)\s+fps", text or "")
    if m:
        out["encoded_frames"] = int(m.group(1))
        out["fps"] = float(m.group(2))
    m = _READN_RE.search(text or "")
    if m:
        out["reader_frames"] = int(m.group(1))
    out["reader_identity"] = reader_identity_from_log(text)
    out["output_depth"] = _search_str(text, r"output depth\s*:\s*(\S+)")
    out["output_format"] = _search_str(text, r"output format\s*:\s*(\S+)")
    m = re.search(r"^\s*([\w/\.]+)\s*:\s*(\d{3,5})x(\d{3,5}),\s*([\d/.]+)\s*fps",
                  text or "", re.MULTILINE)
    if m:
        out["input_codec"] = m.group(1)
        out["input_res"] = f"{m.group(2)}x{m.group(3)}"
        out["input_fps"] = m.group(4)
    out["raw"] = None
    return out


def _search_str(text: str, pat: str) -> str | None:
    m = re.search(pat, text or "", re.IGNORECASE)
    return m.group(1) if m else None
