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
