#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the raw audio sample-entry bytes of every audio track, side by side.

The reported symptom (PR: all four channels white noise with faint original
audio; VLC: dropouts; ffmpeg/Gyroflow: fine) can only be explained by the
*sample description*, because ffmpeg decoding the samples gives a residual of
exactly 0. So dump the stsd entries themselves and diff them by hand.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


class Box:
    __slots__ = ("type", "start", "data_start", "end", "size")

    def __init__(self, typ, start, data_start, end, size):
        self.type, self.start, self.data_start = typ, start, data_start
        self.end, self.size = end, size


def boxes(f, start, end):
    out, off = [], start
    while off + 8 <= end:
        f.seek(off)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr)
        data = off + 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            data = off + 16
        elif size == 0:
            size = end - off
        if size < 8 or off + size > end:
            break
        out.append(Box(typ.decode("latin-1"), off, data, off + size, size))
        off += size
    return out


def child(f, b, *path):
    cur = b
    for name in path:
        cur = next((x for x in boxes(f, cur.data_start, cur.end)
                    if x.type == name), None)
        if cur is None:
            return None
    return cur


def stsd_raw(f, trak):
    stsd = child(f, trak, "mdia", "minf", "stbl", "stsd")
    if stsd is None:
        return b""
    f.seek(stsd.start)
    return f.read(stsd.size)


def handler(f, trak):
    h = child(f, trak, "mdia", "hdlr")
    if h is None:
        return None
    f.seek(h.data_start + 8)
    return f.read(4).decode("latin-1")


def dump(tag: str, path: Path) -> dict[int, bytes]:
    res: dict[int, bytes] = {}
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = boxes(f, 0, end)
        moov = next((b for b in top if b.type == "moov"), None)
        if moov is None:
            print(f"{tag}: NO MOOV")
            return res
        n = 0
        for t in boxes(f, moov.data_start, moov.end):
            if t.type != "trak" or handler(f, t) != "soun":
                continue
            res[n] = stsd_raw(f, t)
            n += 1
    return res


def annotate(blob: bytes) -> str:
    """Cheap fourcc scan of the entry so a missing child box is obvious."""
    out = []
    i = 8
    while i + 8 <= len(blob):
        size, typ = struct.unpack(">I4s", blob[i:i + 8])
        out.append(typ.decode("latin-1", "replace").strip() or "?")
        if size < 8:
            break
        i += size
    return ",".join(out) or "(none)"


def main() -> int:
    sets = {}
    for arg in sys.argv[1:]:
        tag, _, p = arg.partition("=")
        sets[tag] = dump(tag, Path(p))
    tracks = sorted({k for s in sets.values() for k in s})
    for k in tracks:
        print(f"\n===== audio track #{k} =====")
        for tag, s in sets.items():
            blob = s.get(k, b"")
            if not blob:
                print(f"  {tag:<22} (missing)")
                continue
            print(f"  {tag:<22} stsd size={len(blob):<4} "
                  f"entry_size={struct.unpack('>I', blob[16:20])[0]:<4} "
                  f"boxes={annotate(blob)}")
            # entry 从 blob[16] 开始 (stsd 8 + ver/flags 4 + count 4)
            if len(blob) > 16:
                esize = struct.unpack(">I", blob[16:20])[0]
                print(f"    entry hex ({esize}B): "
                      f"{blob[16:16 + esize].hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
