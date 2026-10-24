#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streaming MP4/MOV box inventory (never loads mdat).

Reads top-level box headers by seeking, loads only the moov box (typically tens of MB)
and enumerates:
  * major brand / compatible brands;
  * track handler types (vide / soun / ... );
  * whether a Sony rtmd data track / sample entry exists;
  * uuid boxes at top level and inside moov (vendor metadata);
  * udta/meta presence.

Usage: python mp4_inventory.py FILE [FILE ...]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

CONTAINERS = {"moov", "trak", "mdia", "minf", "udta", "meta", "ilst", "stbl",
              "edts", "dinf", "mvex", "moof", "traf", "mfra", "tref"}
HANDLER_NAMES = {"vide": "video", "soun": "audio", "rtmd": "rtmd-data",
                 "tmcd": "timecode", "text": "text", "meta": "meta",
                 "mdir": "meta", "sbtl": "subtitle", "subp": "subtitle"}


def iter_boxes(fh, start: int, end: int, depth: int = 0):
    off = start
    while off + 8 <= end:
        fh.seek(off)
        head = fh.read(16)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        typ = head[4:8].decode("latin1")
        hdr = 8
        if size == 1:
            if len(head) < 16:
                return
            size = struct.unpack(">Q", head[8:16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            return
        yield typ, off, size, hdr, depth
        if typ in CONTAINERS:
            child = off + hdr + (4 if typ == "meta" else 0)
            yield from iter_boxes(fh, child, off + size, depth + 1)
        off += size


def inspect(path: Path) -> dict:
    size = path.stat().st_size
    out = {"file": path.name, "size_gib": round(size / 2**30, 2),
           "brand": None, "compatible": [], "track_handlers": [],
           "track_ids": [], "rtmd_box": False, "uuid": [], "top_boxes": [],
           "moov_off": None, "moov_size": None}
    with path.open("rb") as fh:
        # top-level scan until moov (moov may be anywhere)
        pos = 0
        while pos + 8 <= size:
            fh.seek(pos)
            head = fh.read(16)
            bsize = struct.unpack(">I", head[:4])[0]
            typ = head[4:8].decode("latin1")
            hdr = 8
            if bsize == 1:
                bsize = struct.unpack(">Q", head[8:16])[0]
                hdr = 16
            elif bsize == 0:
                bsize = size - pos
            if bsize < hdr:
                break
            out["top_boxes"].append(typ)
            if typ == "ftyp":
                fh.seek(pos)
                raw = fh.read(min(bsize, 256))
                out["brand"] = raw[8:12].decode("latin1")
                vals = []
                for i in range(16, len(raw) - 3, 4):
                    b = raw[i:i + 4].decode("latin1")
                    if b.isprintable():
                        vals.append(b)
                out["compatible"] = vals
            if typ == "uuid":
                fh.seek(pos + 8)
                u = fh.read(16)
                out["uuid"].append(("top:" + u.hex()[:16], bsize))
            if typ == "moov":
                out["moov_off"], out["moov_size"] = pos, bsize
                break
            pos += bsize
        if out["moov_off"] is None:
            return out
        moov = out["moov_off"]
        # enumerate moov structure without loading it
        cur_trak = None
        for typ, off, bsize, hdr, depth in iter_boxes(fh, moov, moov + out["moov_size"]):
            if typ == "trak":
                cur_trak = {"handlers": [], "uuid": [], "ids": []}
                out.setdefault("traks", []).append(cur_trak)
            if typ == "hdlr":
                fh.seek(off + 16)
                htype = fh.read(4).decode("latin1")
                name = HANDLER_NAMES.get(htype, htype)
                out["track_handlers"].append(name)
                if cur_trak is not None:
                    cur_trak["handlers"].append(name)
            if typ == "tkhd":
                data = fh.read(0)  # noop
                fh.seek(off + 8)
                ver = fh.read(1)[0]
                tid_off = off + 20 if ver == 0 else off + 28
                fh.seek(tid_off)
                tid = struct.unpack(">I", fh.read(4))[0]
                out["track_ids"].append(tid)
                if cur_trak is not None:
                    cur_trak["ids"].append(tid)
            if typ == "uuid":
                fh.seek(off + 8)
                u = fh.read(16)
                out["uuid"].append(("moov:" + u.hex()[:16], bsize))
                if cur_trak is not None:
                    cur_trak["uuid"].append(u.hex()[:16])
            if typ == "rtmd":
                out["rtmd_box"] = True
    return out


def main(argv: list[str]) -> int:
    for a in argv:
        p = Path(a)
        if not p.is_file():
            print(f"missing: {p}")
            continue
        info = inspect(p)
        print(f"\n=== {info['file']} ({info['size_gib']} GiB)")
        print(f"  brand={info['brand']}  compatible={info['compatible']}")
        print(f"  top-level boxes: {info['top_boxes']}")
        print(f"  tracks: handlers={info['track_handlers']} ids={info['track_ids']}")
        print(f"  Sony rtmd sample entry present: {info['rtmd_box']}")
        print(f"  uuid boxes: {[(u, s) for u, s in info['uuid']]}")
        traks = info.get("traks", [])
        if traks:
            print("  per-trak:")
            for i, t in enumerate(traks, 1):
                print(f"    trak{i}: handlers={t['handlers']} ids={t['ids']} "
                      f"uuid={t['uuid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
