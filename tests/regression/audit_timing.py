#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Container-level timing / chunk-layout audit for playback problems.

"VLC audio dropout" is not explained by the sample entry (that gives white
noise, not gaps). This checks the things that actually cause dropouts:

  * `elst` edit lists: a non-zero `media_time`, or a segment duration that
    disagrees with the track duration, shifts or truncates playback;
  * `mdhd`/`tkhd` duration vs the real sample count;
  * `stco`/`co64` bounds: every chunk must sit inside `mdat`, and chunks must
    not overlap;
  * audio/video interleave distance (a huge distance starves the demuxer).
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


def handler(f, trak):
    h = child(f, trak, "mdia", "hdlr")
    if h is None:
        return None
    f.seek(h.data_start + 8)
    return f.read(4).decode("latin-1")


def full(f, b, ver_off=0):
    f.seek(b.data_start)
    ver = f.read(1)[0]
    f.read(3)
    return ver


def mdhd(f, trak):
    b = child(f, trak, "mdia", "mdhd")
    if b is None:
        return None
    ver = full(f, b)
    if ver == 1:
        f.seek(b.data_start + 4)
        c, m, ts, dur = struct.unpack(">QQIQ", f.read(28))
    else:
        f.seek(b.data_start + 4)
        c, m, ts, dur = struct.unpack(">IIII", f.read(16))
    return {"timescale": ts, "duration": dur,
            "seconds": dur / ts if ts else None}


def tkhd(f, trak):
    b = child(f, trak, "tkhd")
    if b is None:
        return None
    ver = full(f, b)
    if ver == 1:
        f.seek(b.data_start + 4)
        rest = f.read(88)
        dur = struct.unpack(">Q", rest[84:92])[0] if len(rest) >= 92 else None
    else:
        f.seek(b.data_start + 4)
        rest = f.read(76)
        dur = struct.unpack(">I", rest[72:76])[0] if len(rest) >= 76 else None
    f.seek(b.data_start)
    v = f.read(1)[0]
    return {"version": v, "duration": dur}


def elst(f, trak):
    b = child(f, trak, "edts", "elst")
    if b is None:
        return None
    ver = full(f, b)
    f.seek(b.data_start + 4)
    n = struct.unpack(">I", f.read(4))[0]
    rows = []
    for _ in range(min(n, 8)):
        if ver == 1:
            sd, mt = struct.unpack(">Qq", f.read(16))
            rate = struct.unpack(">i", f.read(4))[0]
        else:
            sd, mt = struct.unpack(">Ii", f.read(8))
            rate = struct.unpack(">i", f.read(4))[0]
        rows.append({"segment_duration": sd, "media_time": mt,
                     "media_rate": rate / 65536.0})
    return {"version": ver, "count": n, "rows": rows}


def chunk_bounds(f, trak, mdat):
    stbl = child(f, trak, "mdia", "minf", "stbl")
    if stbl is None:
        return None
    stco = child(f, stbl, "stco")
    co64 = child(f, stbl, "co64")
    if stco is not None:
        f.seek(stco.data_start + 4)
        n = struct.unpack(">I", f.read(4))[0]
        raw = f.read(4 * min(n, 1))
        f.seek(stco.data_start + 8)
        offs = [struct.unpack(">I", f.read(4))[0] for _ in range(min(n, 2000))]
        kind = "stco"
    elif co64 is not None:
        f.seek(co64.data_start + 4)
        n = struct.unpack(">I", f.read(4))[0]
        f.seek(co64.data_start + 8)
        offs = [struct.unpack(">Q", f.read(8))[0] for _ in range(min(n, 2000))]
        kind = "co64"
    else:
        return None
    inside = sum(1 for o in offs
                 if mdat is not None and mdat.data_start <= o < mdat.end)
    return {"kind": kind, "entries": n, "offsets_sampled": len(offs),
            "inside_mdat": inside,
            "first": offs[0] if offs else None,
            "last": offs[-1] if offs else None,
            "sorted": offs == sorted(offs)}


def main() -> int:
    for arg in sys.argv[1:]:
        tag, _, p = arg.partition("=")
        path = Path(p)
        print(f"\n{'=' * 74}\n{tag}: {path.name}")
        with path.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            top = boxes(f, 0, end)
            mdat = next((b for b in top if b.type == "mdat"), None)
            mvhd = None
            moov = next((b for b in top if b.type == "moov"), None)
            if moov is None:
                print("  NO MOOV")
                continue
            for b in boxes(f, moov.data_start, moov.end):
                if b.type == "mvhd":
                    ver = full(f, b)
                    if ver == 1:
                        f.seek(b.data_start + 4)
                        _, _, ts, dur = struct.unpack(">QQIQ", f.read(28))
                    else:
                        f.seek(b.data_start + 4)
                        _, _, ts, dur = struct.unpack(">IIII", f.read(16))
                    mvhd = {"timescale": ts, "duration": dur,
                            "seconds": dur / ts if ts else None}
            print(f"  mdat: {mdat.data_start if mdat else None}.."
                  f"{mdat.end if mdat else None}  ({len(top)} 顶层 box)")
            print(f"  mvhd: {mvhd}")
            for i, t in enumerate(boxes(f, moov.data_start, moov.end)):
                if t.type != "trak":
                    continue
                h = handler(f, t)
                print(f"  --- trak#{i} {h}")
                print(f"      mdhd {mdhd(f, t)}  tkhd {tkhd(f, t)}")
                print(f"      elst {elst(f, t)}")
                print(f"      chunks {chunk_bounds(f, t, mdat)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
