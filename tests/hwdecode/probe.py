"""Input characterisation for the hardware-decode matrix.

Two deliberate design rules:

1. **No side effects on the corpus.**  Nothing here runs ``MP4Box -diso``
   (which writes an ``_info.xml`` next to the source) or otherwise writes
   into ``testsets/``.
2. **The container sample count is parsed independently.**  The whole
   hardware-decode question is a frame-count question, so the matrix may
   not answer it with a number produced by the same toolchain it is
   testing.  ``isobmff_video_samples`` walks ``moov/trak/mdia/minf/stbl``
   directly out of the file bytes, with no ffmpeg and no GPAC involved.

Facts published per input (the "input characteristics" side of the B/C
matrices):

* codec / profile / level / pix_fmt equivalent (bit depth, chroma)
* geometry (width, height, SAR, DAR), frame rates, VFR flag
* stream inventory (video / audio / data / other)
* **container-declared video sample count** — independent parse
* **media timescale, edit list, movie timescale** — independent parse
* **leading-picture count** — pictures presented before the first IRAP
* colour metadata (range / matrix / primaries / transfer)
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FFPROBE = ROOT / "tools" / "ffprobe.exe"
FFMPEG = ROOT / "tools" / "ffmpeg.exe"
MP4BOX = ROOT / "tools" / "GPAC" / "mp4box.exe"

# How many leading packets to scan when locating leading pictures.  The
# measured corpus has at most 3; 200 is a safety margin that still reads
# only the first GOP of any real camera file.
LEADING_SCAN_PACKETS = 200


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# independent ISOBMFF sample-table reader
# ---------------------------------------------------------------------------

VIDEO_HANDLERS = (b"vide",)
_SKIP_TO_CONTAINER = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


class _IsoError(Exception):
    pass


def _iter_boxes(data: bytes, start: int, end: int):
    """Yield (type, payload_start, payload_end) for boxes in [start, end)."""
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


def _find_box(data: bytes, start: int, end: int, path: list[bytes]):
    """Descend a box path, returning (payload_start, payload_end) or None."""
    if not path:
        return (start, end)
    head, rest = path[0], path[1:]
    for btype, ps, pe in _iter_boxes(data, start, end):
        if btype == head:
            if not rest:
                return (ps, pe)
            found = _find_box(data, ps, pe, rest)
            if found is not None:
                return found
    return None


def _handler_of(data: bytes, trak_ps: int, trak_pe: int) -> bytes | None:
    found = _find_box(data, trak_ps, trak_pe, [b"mdia", b"hdlr"])
    if found is None:
        return None
    ps, _ = found
    # hdlr: version/flags(4) predefined(4) handler_type(4)
    if ps + 12 > len(data):
        return None
    return data[ps + 8:ps + 12]


def _stsz_count(data: bytes, stbl_ps: int, stbl_pe: int) -> int | None:
    found = _find_box(data, stbl_ps, stbl_pe, [b"stsz"])
    if found is None:
        return None
    ps, pe = found
    if ps + 12 > pe:
        return None
    # stsz: version/flags(4) sample_size(4) sample_count(4)
    return struct.unpack_from(">I", data, ps + 8)[0]


def _stts_entries(data: bytes, stbl_ps: int, stbl_pe: int):
    found = _find_box(data, stbl_ps, stbl_pe, [b"stts"])
    if found is None:
        return None
    ps, pe = found
    if ps + 8 > pe:
        return None
    count = struct.unpack_from(">I", data, ps + 4)[0]
    out = []
    off = ps + 8
    for _ in range(count):
        if off + 8 > pe:
            break
        n, delta = struct.unpack_from(">II", data, off)
        out.append((n, delta))
        off += 8
    return out


@dataclass
class ContainerTrack:
    track_id: int
    handler: str
    sample_count: int | None
    stts_sum: int | None
    timescale: int | None
    mdhd_duration: int | None
    elst: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class ContainerFacts:
    ok: bool
    major_brand: str = ""
    movie_timescale: int | None = None
    movie_duration: int | None = None
    tracks: list[ContainerTrack] = field(default_factory=list)
    error: str | None = None

    @property
    def video(self) -> ContainerTrack | None:
        for t in self.tracks:
            if t.handler == "vide":
                return t
        return None


def read_container(path: Path) -> ContainerFacts:
    """Parse container-level facts straight out of the file bytes."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(16)
            if len(head) < 8:
                return ContainerFacts(False, error="short file")
            # Locate ftyp/moov without loading the whole mdat.
            size = struct.unpack_from(">I", head, 0)[0]
            btype = head[4:8]
            brand = ""
            if btype == b"ftyp" and size >= 12:
                brand = f.read(4).decode("latin-1", "replace")
            f.seek(0)
            data = _read_moov(f)
    except OSError as exc:
        return ContainerFacts(False, error=str(exc))
    if data is None:
        return ContainerFacts(False, brand=brand, error="no moov box")

    root = (0, len(data))
    moov = _find_box(data, root[0], root[1], [b"moov"])
    if moov is None:
        return ContainerFacts(False, brand=brand, error="no moov box")
    facts = ContainerFacts(True, major_brand=brand)

    mvhd = _find_box(data, moov[0], moov[1], [b"mvhd"])
    if mvhd is not None:
        ps = mvhd[0]
        version = data[ps]
        if version == 0:
            ts, dur = struct.unpack_from(">II", data, ps + 12)
        else:
            ts, dur = struct.unpack_from(">IQ", data, ps + 20)
        facts.movie_timescale = ts
        facts.movie_duration = dur

    for btype, ps, pe in _iter_boxes(data, moov[0], moov[1]):
        if btype != b"trak":
            continue
        handler = _handler_of(data, ps, pe)
        if handler is None:
            continue
        tkhd = _find_box(data, ps, pe, [b"tkhd"])
        track_id = -1
        if tkhd is not None:
            tps = tkhd[0]
            track_id = struct.unpack_from(">I", data, tps + (12 if data[tps] == 0 else 20))[0]
        mdhd = _find_box(data, ps, pe, [b"mdia", b"mdhd"])
        timescale = mdhd_dur = None
        if mdhd is not None:
            mps = mdhd[0]
            if data[mps] == 0:
                timescale, mdhd_dur = struct.unpack_from(">II", data, mps + 12)
            else:
                timescale, mdhd_dur = struct.unpack_from(">IQ", data, mps + 20)
        stbl = _find_box(data, ps, pe, [b"mdia", b"minf", b"stbl"])
        samples = stts = None
        if stbl is not None:
            samples = _stsz_count(data, stbl[0], stbl[1])
            entries = _stts_entries(data, stbl[0], stbl[1])
            if entries is not None:
                stts = sum(n * d for n, d in entries)
        elst_entries: list[tuple[int, int]] = []
        elst = _find_box(data, ps, pe, [b"edts", b"elst"])
        if elst is not None:
            eps = elst[0]
            ver = data[eps]
            n = struct.unpack_from(">I", data, eps + 4)[0]
            off = eps + 8
            for _ in range(min(n, 8)):
                if ver == 0:
                    dur, mt = struct.unpack_from(">Ii", data, off)
                    off += 12
                else:
                    dur, mt = struct.unpack_from(">Qq", data, off)
                    off += 20
                elst_entries.append((dur, mt))
        facts.tracks.append(
            ContainerTrack(
                track_id=track_id,
                handler=handler.decode("latin-1", "replace"),
                sample_count=samples,
                stts_sum=stts,
                timescale=timescale,
                mdhd_duration=mdhd_dur,
                elst=elst_entries,
            )
        )
    return facts


def _read_moov(f) -> bytes | None:
    """Load only the moov box, skipping mdat by seeking."""
    f.seek(0, 2)
    file_size = f.tell()
    off = 0
    while off + 8 <= file_size:
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
            size = file_size - off
        if size < hdr_len:
            return None
        if btype == b"moov":
            f.seek(off)
            return f.read(size)
        off += size
    return None


# ---------------------------------------------------------------------------
# ffprobe-based stream facts
# ---------------------------------------------------------------------------

_CHROMA_BY_PIX = {
    "yuv420p": "4:2:0",
    "yuv420p10le": "4:2:0",
    "yuv420p12le": "4:2:0",
    "yuv422p": "4:2:2",
    "yuv422p10le": "4:2:2",
    "yuv422p12le": "4:2:2",
    "yuv444p": "4:4:4",
    "yuv444p10le": "4:4:4",
    "yuvj420p": "4:2:0",
    "p010le": "4:2:0",
}


def chroma_of(pix_fmt: str) -> str:
    if pix_fmt in _CHROMA_BY_PIX:
        return _CHROMA_BY_PIX[pix_fmt]
    if pix_fmt.startswith("yuv422"):
        return "4:2:2"
    if pix_fmt.startswith("yuv444"):
        return "4:4:4"
    if pix_fmt.startswith("yuv420"):
        return "4:2:0"
    return "unknown"


def depth_of(pix_fmt: str) -> int:
    m = re.search(r"p(\d{1,2})(le|be)?$", pix_fmt)
    if m:
        return int(m.group(1))
    if pix_fmt in ("p010le", "p012le", "p016le"):
        return 10
    return 8


@dataclass
class VideoStream:
    index: int
    codec: str
    profile: str
    pix_fmt: str
    width: int
    height: int
    bit_depth: int
    chroma: str
    r_frame_rate: str
    avg_frame_rate: str
    nb_frames: int | None
    time_base: str
    sar: str
    dar: str
    color_range: str | None
    color_space: str | None
    color_primaries: str | None
    color_transfer: str | None
    level: int | None
    field_order: str | None
    duration: float | None
    codec_tag: str | None = None


@dataclass
class InputFacts:
    path: str
    input_id: str
    exists: bool
    container: str
    has_video: bool
    video: VideoStream | None = None
    audio_streams: int = 0
    data_streams: int = 0
    other_streams: int = 0
    subtitle_streams: int = 0
    stream_kinds: list[str] = field(default_factory=list)
    # independent container parse
    container_samples: int | None = None
    # how container_samples was obtained: "isobmff-stsz" (byte-parsed here)
    # or "ffprobe-packets" (libavformat demuxer).  Recorded so the evidence
    # chain states which method produced the reference number.
    container_source: str | None = None
    container_stts_sum: int | None = None
    container_video_timescale: int | None = None
    container_elst: list[tuple[int, int]] = field(default_factory=list)
    movie_timescale: int | None = None
    movie_duration: int | None = None
    major_brand: str = ""
    isobmf_ok: bool = False
    # derived
    ffprobe_packets: int | None = None
    leading_pictures: int | None = None
    is_vfr: bool = False
    error: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def probe_input(
    path: Path,
    *,
    count_packets: bool = False,
    input_id: str = "",
    leading_scan: bool = True,
) -> InputFacts:
    """Characterise one input file."""
    path = Path(path)
    facts = InputFacts(
        path=str(path), input_id=input_id or path.stem, exists=False,
        container="", has_video=False,
    )
    if not path.is_file():
        facts.error = "missing"
        return facts
    facts.exists = True

    cmd = [
        str(FFPROBE), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format",
    ]
    if count_packets:
        cmd.append("-count_packets")
    cmd += ["-i", str(path)]
    try:
        p = _run(cmd)
    except subprocess.TimeoutExpired:
        facts.error = "ffprobe timeout"
        return facts
    if p.returncode != 0:
        facts.error = f"ffprobe rc={p.returncode}: {(p.stderr or '').strip()[:300]}"
        return facts
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        facts.error = f"json: {exc}"
        return facts

    facts.container = (
        (data.get("format") or {}).get("format_name", "") or ""
    ).split(",")[0]

    for s in data.get("streams", []):
        kind = s.get("codec_type")
        facts.stream_kinds.append(f"{kind}:{s.get('codec_name')}")
        if kind == "audio":
            facts.audio_streams += 1
        elif kind == "data":
            facts.data_streams += 1
        elif kind == "subtitle":
            facts.subtitle_streams += 1
        elif kind == "video" and facts.video is None:
            pix = s.get("pix_fmt", "") or ""
            nb = s.get("nb_frames")
            try:
                nb_i = int(nb) if nb is not None else None
            except (TypeError, ValueError):
                nb_i = None
            facts.video = VideoStream(
                index=s.get("index", 0),
                codec=s.get("codec_name", ""),
                profile=s.get("profile", "") or "",
                pix_fmt=pix,
                width=s.get("width", 0),
                height=s.get("height", 0),
                bit_depth=depth_of(pix),
                chroma=chroma_of(pix),
                r_frame_rate=s.get("r_frame_rate", ""),
                avg_frame_rate=s.get("avg_frame_rate", ""),
                nb_frames=nb_i,
                time_base=s.get("time_base", ""),
                sar=s.get("sample_aspect_ratio", "") or "",
                dar=s.get("display_aspect_ratio", "") or "",
                color_range=s.get("color_range"),
                color_space=s.get("color_space"),
                color_primaries=s.get("color_primaries"),
                color_transfer=s.get("color_transfer"),
                level=s.get("level"),
                field_order=s.get("field_order"),
                duration=_f(s.get("duration")),
                codec_tag=s.get("codec_tag_string"),
            )
            facts.has_video = True
        elif kind == "video":
            facts.other_streams += 1

    if facts.video is not None:
        rf, af = facts.video.r_frame_rate, facts.video.avg_frame_rate
        facts.is_vfr = bool(rf and af and rf != af)

    # Independent container parse (works for any ISOBMFF file).
    try:
        cf = read_container(path)
    except Exception as exc:  # pragma: no cover - defensive
        cf = ContainerFacts(False, error=str(exc))
    facts.isobmf_ok = cf.ok
    if cf.ok:
        facts.movie_timescale = cf.movie_timescale
        facts.movie_duration = cf.movie_duration
        facts.major_brand = cf.major_brand
        vt = cf.video
        if vt is not None:
            facts.container_samples = vt.sample_count
            facts.container_stts_sum = vt.stts_sum
            facts.container_video_timescale = vt.timescale
            facts.container_elst = vt.elst
            if vt.sample_count is not None:
                facts.container_source = "isobmff-stsz"

    if count_packets:
        facts.ffprobe_packets = _count_video_packets(path)
        if facts.container_samples is None and facts.ffprobe_packets is not None:
            facts.container_samples = facts.ffprobe_packets
            facts.container_source = "ffprobe-packets"

    if leading_scan and facts.has_video:
        facts.leading_pictures = leading_picture_count(path)

    return facts


def container_reference_count(path: Path) -> tuple[int | None, str | None]:
    """Container-declared video sample count, cheapest method first.

    ``isobmff-stsz`` is a direct byte parse of the sample-size box and is
    the preferred reference; non-ISOBMFF inputs (MKV) fall back to the
    libavformat demuxer count, flagged as such.
    """
    path = Path(path)
    try:
        cf = read_container(path)
        if cf.ok and cf.video is not None and cf.video.sample_count is not None:
            return cf.video.sample_count, "isobmff-stsz"
    except Exception:  # noqa: BLE001
        pass
    n = _count_video_packets(path)
    return (n, "ffprobe-packets") if n is not None else (None, None)


def _count_video_packets(path: Path) -> int | None:
    cmd = [
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-print_format", "json", "-i", str(path),
    ]
    try:
        p = _run(cmd)
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


@dataclass
class LeadingInfo:
    first_irap_pts: float | None
    leading: int
    total_scanned: int
    flagged_discard: int
    truncated_scan: bool


def leading_picture_scan(path: Path) -> LeadingInfo:
    """Count pictures whose PTS precedes the first IRAP's PTS.

    Decode order is the container's sample order, so the first IRAP is
    normally the first packet and the leading pictures follow it.  With
    open-GOP those pictures are *presented* before the IRAP even though
    they are stored after it — which is exactly the shape both rigaya
    patches address.
    """
    cmd = [
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags,dts_time",
        "-print_format", "json",
        "-read_intervals", f"%+#{LEADING_SCAN_PACKETS}",
        "-i", str(path),
    ]
    try:
        p = _run(cmd)
    except subprocess.TimeoutExpired:
        return LeadingInfo(None, 0, 0, 0, True)
    if p.returncode != 0:
        return LeadingInfo(None, 0, 0, 0, True)
    try:
        packets = json.loads(p.stdout).get("packets", [])
    except json.JSONDecodeError:
        return LeadingInfo(None, 0, 0, 0, True)

    first_k = None
    for pk in packets:
        if "K" in (pk.get("flags") or ""):
            first_k = _f(pk.get("pts_time"))
            break
    if first_k is None:
        return LeadingInfo(None, 0, len(packets), 0, len(packets) >= LEADING_SCAN_PACKETS)
    lead = 0
    discard = 0
    for pk in packets:
        t = _f(pk.get("pts_time"))
        flags = pk.get("flags") or ""
        if "D" in flags:
            discard += 1
        if t is not None and t < first_k - 1e-6:
            lead += 1
    return LeadingInfo(
        first_k, lead, len(packets), discard,
        len(packets) >= LEADING_SCAN_PACKETS,
    )


def leading_picture_count(path: Path) -> int | None:
    info = leading_picture_scan(path)
    if info.first_irap_pts is None:
        return None
    return info.leading
