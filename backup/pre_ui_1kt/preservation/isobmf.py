"""Minimal ISO-BMFF box walker / verbatim extractor / byte patcher.

Exists because GPAC (26.02) has no way to write vendor `uuid` boxes:
`MP4Box -hx uuid` and `gpac -hx uuid` both return nothing. Everything
else (rtmd track, nrtm meta) is handled by MP4Box itself.

Scope (POC, deliberately narrow):
- walk root boxes and moov children (trak handler detection)
- extract `uuid` boxes verbatim (header + payload) with context
- insert verbatim `uuid` bytes back into a GPAC-produced file at the
  same structural context (root / moov tail / trak tail), fixing the
  ancestor box sizes

Safety rule: insertions happen inside moov or at EOF, never before the
mdat payload, so stco/co64 chunk offsets stay valid. A faststart file
(moov before mdat) is rejected loudly instead of being corrupted.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

UUID_PROF = bytes.fromhex("50524F4621D24FCEBB88695CFAC9C740")
UUID_USMT = bytes.fromhex("55534D5421D24FCEBB88695CFAC9C740")

_UUID_LABELS = {UUID_PROF: "PROF", UUID_USMT: "USMT"}

_COPY_CHUNK = 8 * 1024 * 1024


@dataclass
class Box:
    type: str               # 4CC decoded latin-1, "uuid" for uuid boxes
    start: int              # absolute offset of box header
    size: int               # full size incl. header
    header: int             # header size (8, 16 for largesize, 24 for uuid+large)
    uuid: bytes | None      # 16-byte extended type for uuid boxes

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def data_offset(self) -> int:
        return self.start + self.header


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_COPY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_header(f, offset: int, limit: int) -> Box | None:
    if offset + 8 > limit:
        return None
    f.seek(offset)
    head = f.read(8)
    if len(head) < 8:
        return None
    size32, btype = struct.unpack(">I4s", head)
    header = 8
    uuid = None
    if btype == b"uuid":
        f.seek(offset + 8)
        uuid = f.read(16)
        if len(uuid) < 16:
            return None
        header = 24
    if size32 == 1:
        f.seek(offset + 8)
        large = f.read(8)
        if len(large) < 8:
            return None
        size = struct.unpack(">Q", large)[0]
        header += 8
    elif size32 == 0:
        size = limit - offset
    else:
        size = size32
    if size < header or offset + size > limit:
        return None
    return Box(btype.decode("latin-1"), offset, size, header, uuid)


def _children(f, box: Box, fullbox: bool = False) -> list[Box]:
    start = box.data_offset + (4 if fullbox else 0)
    out = []
    off = start
    while off < box.end:
        child = _read_header(f, off, box.end)
        if child is None:
            break
        out.append(child)
        off = child.end
    return out


def root_boxes(path: Path) -> list[Box]:
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        out = []
        off = 0
        while off < end:
            b = _read_header(f, off, end)
            if b is None:
                break
            out.append(b)
            off = b.end
        return out


def _trak_handler(f, trak: Box) -> str:
    for mdia in _children(f, trak):
        if mdia.type != "mdia":
            continue
        for hdlr in _children(f, mdia):
            if hdlr.type != "hdlr":
                continue
            # hdlr payload: version/flags(4) pre_defined(4) handler_type(4)
            f.seek(hdlr.data_offset + 8)
            return f.read(4).decode("latin-1")
    return ""


def extract_uuid_boxes(path: Path) -> list[tuple[str, bytes, bytes]]:
    """Return [(context, ext_type, whole_box_bytes)].

    context: "root" | "moov" | "trak:<handler4cc>"
    """
    found: list[tuple[str, bytes, bytes]] = []
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        off = 0
        while off < end:
            b = _read_header(f, off, end)
            if b is None:
                break
            if b.type == "uuid":
                f.seek(b.start)
                found.append(("root", b.uuid or b"", f.read(b.size)))
            elif b.type == "moov":
                for child in _children(f, b):
                    if child.type == "uuid":
                        f.seek(child.start)
                        found.append(("moov", child.uuid or b"", f.read(child.size)))
                    elif child.type == "trak":
                        handler = _trak_handler(f, child)
                        for sub in _children(f, child):
                            if sub.type == "uuid":
                                f.seek(sub.start)
                                found.append((
                                    f"trak:{handler}",
                                    sub.uuid or b"",
                                    f.read(sub.size),
                                ))
            off = b.end
    return found


def uuid_label(ext_type: bytes) -> str:
    return _UUID_LABELS.get(ext_type, "")


def uuid_guid(ext_type: bytes) -> str:
    h = ext_type.hex().upper()
    return (
        f"{{{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}"
    )


def _patch_u32(f, offset: int, old: int, new: int) -> None:
    f.seek(offset)
    cur = struct.unpack(">I", f.read(4))[0]
    if cur != old:
        raise RuntimeError(f"patch_u32 @{offset}: expected {old}, found {cur}")
    f.seek(offset)
    f.write(struct.pack(">I", new))


def _patch_u64(f, offset: int, old: int, new: int) -> None:
    f.seek(offset)
    cur = struct.unpack(">Q", f.read(8))[0]
    if cur != old:
        raise RuntimeError(f"patch_u64 @{offset}: expected {old}, found {cur}")
    f.seek(offset)
    f.write(struct.pack(">Q", new))


def _stts_sum(f, trak: Box) -> int | None:
    """Sum of sample durations (stts) for a track, in track units.

    stts stays exact through GPAC rewrites (native sample-table copy),
    unlike mdhd durations which GPAC can mangle when rescaling a
    hardware-intermediate track to a different timescale.
    """
    for mdia in _children(f, trak):
        if mdia.type != "mdia":
            continue
        for minf in _children(f, mdia):
            if minf.type != "minf":
                continue
            for stbl in _children(f, minf):
                if stbl.type != "stbl":
                    continue
                for stts in _children(f, stbl):
                    if stts.type != "stts":
                        continue
                    f.seek(stts.data_offset + 4)
                    count = struct.unpack(">I", f.read(4))[0]
                    total = 0
                    f.seek(stts.data_offset + 8)
                    for _ in range(count):
                        sc, delta = struct.unpack(">II", f.read(8))
                        total += sc * delta
                    return total
    return None


def patch_track_durations(
    path: Path,
    movie_timescale: int,
    from_stts: bool = False,
) -> list[str]:
    """Fix tkhd/elst durations truncated or mangled by GPAC rewrites.

    GPAC (26.02) recomputes track-header presentation durations at its
    internal 600 ticks/s precision whenever an NHML import rewrites the
    file, regardless of the movie timescale requested for the output
    (1741740 units @60000 -> 17417.4 -> 1741700). The mdhd media
    durations and stts tables stay exact, so the true presentation
    duration is recoverable per track:

        presentation = (mdhd_duration - elst_media_time)  (track units)
                       * movie_timescale / track_timescale

    Hardware-backend intermediates (rigaya mp4, mvhd timescale 1000)
    hit a second defect: GPAC rescales the imported video track's
    timescale but NOT its mdhd duration, so the mdhd value itself is
    garbage. With `from_stts=True` the content duration is taken from
    the stts sum instead (always exact), which is the correct source of
    truth in both cases.

    Only 4/8-byte duration fields are rewritten in place; no box size
    or offset changes. Tracks whose values are already exact are
    untouched. Tracks with multi-entry or empty-edit edit lists are
    skipped (reported), never guessed.

    Returns a list of human-readable patch descriptions.
    """
    roots = root_boxes(path)
    moov = next((b for b in roots if b.type == "moov"), None)
    if moov is None:
        raise RuntimeError(f"no moov box in {path}")

    patched: list[str] = []
    with path.open("r+b") as f:
        for trak in _children(f, moov):
            if trak.type != "trak":
                continue
            handler = _trak_handler(f, trak)
            tkhd = mdia = elst = None
            for c in _children(f, trak):
                if c.type == "tkhd":
                    tkhd = c
                elif c.type == "edts":
                    for e in _children(f, c):
                        if e.type == "elst":
                            elst = e
                elif c.type == "mdia":
                    mdia = c
            if tkhd is None or mdia is None:
                continue
            mdhd = next(
                (c for c in _children(f, mdia) if c.type == "mdhd"), None
            )
            if mdhd is None:
                continue

            f.seek(mdhd.data_offset)
            ver = f.read(1)[0]
            f.seek(mdhd.data_offset + (20 if ver == 1 else 12))
            track_ts = struct.unpack(">I", f.read(4))[0]
            if ver == 1:
                media_dur = struct.unpack(">Q", f.read(8))[0]
            else:
                media_dur = struct.unpack(">I", f.read(4))[0]
            if not track_ts:
                continue

            # edit list: only the simple single-entry case is patched
            media_time = 0
            elst_entry_off: int | None = None
            elst_ver = 0
            if elst is not None:
                f.seek(elst.data_offset)
                elst_ver = f.read(1)[0]
                f.seek(elst.data_offset + 4)
                count = struct.unpack(">I", f.read(4))[0]
                if count != 1:
                    if count > 1:
                        patched.append(
                            f"trak:{handler}: multi-entry elst skipped"
                        )
                    elst = None
                else:
                    elst_entry_off = elst.data_offset + 8
                    f.seek(elst_entry_off + (8 if elst_ver == 1 else 4))
                    mt_size = 8 if elst_ver == 1 else 4
                    media_time = int.from_bytes(
                        f.read(mt_size), "big", signed=True
                    )

            if media_time < 0:
                patched.append(f"trak:{handler}: empty edit skipped")
                continue

            if from_stts:
                content = _stts_sum(f, trak)
                if content is None:
                    patched.append(f"trak:{handler}: no stts, skipped")
                    continue
                expected = round(content * movie_timescale / track_ts)
            else:
                expected = round(
                    (media_dur - media_time) * movie_timescale / track_ts
                )

            f.seek(tkhd.data_offset)
            tkhd_ver = f.read(1)[0]
            dur_off = tkhd.data_offset + (28 if tkhd_ver == 1 else 20)
            f.seek(dur_off)
            if tkhd_ver == 1:
                old = struct.unpack(">Q", f.read(8))[0]
            else:
                old = struct.unpack(">I", f.read(4))[0]
            if old != expected:
                if tkhd_ver == 1:
                    _patch_u64(f, dur_off, old, expected)
                else:
                    _patch_u32(f, dur_off, old, expected)
                patched.append(
                    f"trak:{handler}: tkhd duration {old} -> {expected}"
                )

            if elst is not None and elst_entry_off is not None:
                f.seek(elst_entry_off)
                if elst_ver == 1:
                    old_e = struct.unpack(">Q", f.read(8))[0]
                else:
                    old_e = struct.unpack(">I", f.read(4))[0]
                if old_e != expected:
                    if elst_ver == 1:
                        _patch_u64(f, elst_entry_off, old_e, expected)
                    else:
                        _patch_u32(f, elst_entry_off, old_e, expected)
                    patched.append(
                        f"trak:{handler}: elst duration "
                        f"{old_e} -> {expected}"
                    )
    return patched


def patch_movie_duration(path: Path) -> str | None:
    """Patch mvhd duration to max(tkhd durations).

    Companion to patch_track_durations(): GPAC's millisecond-timescale
    import truncation (rigaya hardware intermediates write mvhd at
    timescale 1000) shortens every tkhd and the mvhd consistently; after
    the per-track patch restores exact values, the movie-level duration
    must follow — validate.compare checks timeline.movie_duration too.

    In-place 4/8-byte patch; no box size/offset changes. Returns a
    human-readable description, or None when already exact.
    """
    roots = root_boxes(path)
    moov = next((b for b in roots if b.type == "moov"), None)
    if moov is None:
        raise RuntimeError(f"no moov box in {path}")

    with path.open("r+b") as f:
        max_dur = 0
        for child in _children(f, moov):
            if child.type != "trak":
                continue
            tkhd = next(
                (c for c in _children(f, child) if c.type == "tkhd"),
                None,
            )
            if tkhd is None:
                continue
            f.seek(tkhd.data_offset)
            ver = f.read(1)[0]
            f.seek(tkhd.data_offset + (28 if ver == 1 else 20))
            width = 8 if ver == 1 else 4
            dur = struct.unpack(">Q" if ver == 1 else ">I", f.read(width))[0]
            max_dur = max(max_dur, dur)

        mvhd = next((c for c in _children(f, moov) if c.type == "mvhd"), None)
        if mvhd is None:
            raise RuntimeError(f"no mvhd box in {path}")
        f.seek(mvhd.data_offset)
        ver = f.read(1)[0]
        if ver == 1:
            f.seek(mvhd.data_offset + 20)
            ts = struct.unpack(">I", f.read(4))[0]
            dur_off = mvhd.data_offset + 24
            old = struct.unpack(">Q", f.read(8))[0]
        else:
            f.seek(mvhd.data_offset + 12)
            ts = struct.unpack(">I", f.read(4))[0]
            dur_off = mvhd.data_offset + 16
            old = struct.unpack(">I", f.read(4))[0]
        if old == max_dur:
            return None
        if ver == 1:
            _patch_u64(f, dur_off, old, max_dur)
        else:
            _patch_u32(f, dur_off, old, max_dur)
        return f"mvhd duration {old} -> {max_dur} (timescale {ts})"


def patch_meta_item_type(
    path: Path,
    item_id: int,
    target_type: bytes,
) -> str | None:
    """Patch the item_type of a file-level meta item (infe v2) in place.

    GPAC's `-add-item` writes item_type 'mime' for re-added meta items;
    Sony sources carry item_type 0x00000000 (their infe is v0, which has
    no item_type field and is rendered as 00000000). validate.compare
    reports the divergence as nrtm.lens_profile.item_type MODIFIED.
    This narrow patch rewrites the 4-byte item_type of the matching
    v2 infe back to the source value.

    Idempotent: returns None when already equal. Only infe version 2 is
    touched (v0/v1 have no item_type field). Returns a human-readable
    description of the patch, or None when nothing changed.
    """
    if len(target_type) != 4:
        raise ValueError("target item_type must be exactly 4 bytes")

    roots = root_boxes(path)

    _FOUND_EXACT = object()

    def search_meta(meta_box: Box):
        """Walk one meta box's iinf entries; patch and return a
        description, _FOUND_EXACT when already equal, or None when no
        matching v2 infe exists."""
        for sub in _children(f, meta_box, fullbox=True):
            if sub.type != "iinf":
                continue
            # iinf fullbox: version/flags(4) + entry_count(2)
            f.seek(sub.data_offset + 4)
            count = struct.unpack(">H", f.read(2))[0]
            off = sub.data_offset + 6
            for _ in range(count):
                infe = _read_header(f, off, sub.end)
                if infe is None or infe.type != "infe":
                    break
                f.seek(infe.data_offset)
                ver = f.read(1)[0]
                if ver != 2:
                    off = infe.end
                    continue
                f.seek(infe.data_offset + 4)
                iid = struct.unpack(">H", f.read(2))[0]
                if iid != item_id:
                    off = infe.end
                    continue
                f.seek(infe.data_offset + 8)
                cur = f.read(4)
                if cur == target_type:
                    return _FOUND_EXACT
                _patch_u32(
                    f,
                    infe.data_offset + 8,
                    struct.unpack(">I", cur)[0],
                    struct.unpack(">I", target_type)[0],
                )
                return (
                    f"infe item {item_id} item_type "
                    f"{cur!r} -> {target_type!r}"
                )
        return None

    with path.open("r+b") as f:
        for root in roots:
            if root.type == "meta":
                res = search_meta(root)
                if res is _FOUND_EXACT:
                    return None
                if res is not None:
                    return res
            elif root.type == "moov":
                for child in _children(f, root):
                    if child.type != "meta":
                        continue
                    res = search_meta(child)
                    if res is _FOUND_EXACT:
                        return None
                    if res is not None:
                        return res
    raise RuntimeError(f"no v2 infe with item_id {item_id} in {path}")


def insert_uuid_boxes(
    src: Path,
    dst: Path,
    inserts: list[tuple[str, bytes]],
) -> None:
    """Write dst = src + verbatim uuid boxes at their structural context.

    inserts: [(context, whole_box_bytes)] with context as returned by
    extract_uuid_boxes(). Trak contexts are matched by handler type
    (first matching trak per insertion, in order).
    """
    roots = root_boxes(src)
    moov = next((b for b in roots if b.type == "moov"), None)
    mdat = next((b for b in roots if b.type == "mdat"), None)
    if moov is None:
        raise RuntimeError(f"no moov box in {src}")
    if mdat is not None and moov.end <= mdat.end and moov.start < mdat.start:
        raise RuntimeError(
            f"{src}: moov precedes mdat (faststart). Refusing to patch: "
            "insertions would invalidate stco/co64 chunk offsets."
        )

    with src.open("rb") as f:
        f.seek(moov.start)
        moov_buf = bytearray(f.read(moov.size))
        traks = []
        off = moov.header
        while off < moov.size:
            if off + 8 > moov.size:
                break
            size32, btype = struct.unpack(
                ">I4s", moov_buf[off:off + 8]
            )
            if btype == b"trak" and size32 >= 8 and off + size32 <= moov.size:
                f_abs = moov.start + off
                fake = Box("trak", f_abs, size32, 8, None)
                traks.append((off, _trak_handler(f, fake)))
            if size32 < 8:
                break
            off += size32

    root_payloads: list[bytes] = []
    moov_payloads: list[bytes] = []
    # trak insertions: (trak_rel_offset, payload) applied tail-first
    trak_inserts: list[tuple[int, int, bytes]] = []  # (trak_off, trak_end_off, data)
    used_traks: dict[str, int] = {}
    for context, data in inserts:
        if context == "root":
            root_payloads.append(data)
        elif context == "moov":
            moov_payloads.append(data)
        elif context.startswith("trak:"):
            handler = context[5:]
            idx = used_traks.get(handler, 0)
            matches = [t for t in traks if t[1] == handler]
            if idx >= len(matches):
                raise RuntimeError(
                    f"no trak with handler '{handler}' #{idx} in {src}"
                )
            used_traks[handler] = idx + 1
            trak_off, _ = matches[idx]
            trak_size = struct.unpack(
                ">I", moov_buf[trak_off:trak_off + 4]
            )[0]
            trak_inserts.append((trak_off, trak_off + trak_size, data))
        else:
            raise RuntimeError(f"unknown uuid context: {context}")

    total_trak_add = sum(len(d) for _, _, d in trak_inserts)
    total_moov_add = total_trak_add + sum(len(d) for d in moov_payloads)

    # apply trak insertions tail-first so earlier offsets stay valid
    for trak_off, trak_end, data in sorted(
        trak_inserts, key=lambda t: t[1], reverse=True
    ):
        moov_buf[trak_end:trak_end] = data
        new_size = struct.unpack(
            ">I", moov_buf[trak_off:trak_off + 4]
        )[0] + len(data)
        moov_buf[trak_off:trak_off + 4] = struct.pack(">I", new_size)

    # moov-context payloads go to the moov tail
    for data in moov_payloads:
        moov_buf.extend(data)

    # patch moov size (header at offset 0; reject largesize upgrade)
    if moov.header == 8:
        new_moov = moov.size + total_moov_add
        if new_moov >= 0xFFFFFFFF:
            raise RuntimeError("moov would need largesize upgrade")
        moov_buf[0:4] = struct.pack(">I", new_moov)
    else:
        moov_buf[8:16] = struct.pack(">Q", moov.size + total_moov_add)

    with src.open("rb") as fin, dst.open("wb") as fout:
        # 1. everything before moov (ftyp/free/mdat) verbatim
        remaining = moov.start
        while remaining > 0:
            chunk = fin.read(min(_COPY_CHUNK, remaining))
            if not chunk:
                break
            fout.write(chunk)
            remaining -= len(chunk)
        # 2. patched moov
        fout.write(moov_buf)
        # 3. everything after moov (e.g. meta) verbatim
        fin.seek(moov.end)
        while True:
            chunk = fin.read(_COPY_CHUNK)
            if not chunk:
                break
            fout.write(chunk)
        # 4. root-context payloads at EOF
        for data in root_payloads:
            fout.write(data)
