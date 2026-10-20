#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Field-level diff of the audio/video sample entries between two files.

Purpose: pin down *exactly which bytes* a repair run changed inside the sample
description. The reported symptom (PR: all four channels white noise with
faint original audio; ffmpeg/Gyroflow: perfect) can only come from here,
because ffmpeg decoding the samples gives a residual of exactly 0.

The layout below is the QuickTime/ISO `SoundDescription` v1 that Sony XAVC
LPCM uses, validated against the source file (channelcount=1, samplesize=24,
samplerate=48000 fits every byte).
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


def entries(path: Path) -> list[tuple[str, int, bytes]]:
    """[(handler, index, entry_bytes)] for every trak that has an stsd."""
    out = []
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = boxes(f, 0, end)
        moov = next((b for b in top if b.type == "moov"), None)
        if moov is None:
            return out
        idx = 0
        for t in boxes(f, moov.data_start, moov.end):
            if t.type != "trak":
                continue
            h = handler(f, t)
            stsd = child(f, t, "mdia", "minf", "stbl", "stsd")
            if stsd is None:
                continue
            f.seek(stsd.data_start + 4)
            n = struct.unpack(">I", f.read(4))[0]
            off = stsd.data_start + 8
            for _ in range(n):
                f.seek(off)
                raw = f.read(8)
                if len(raw) < 8:
                    break
                size, typ = struct.unpack(">I4s", raw)
                if size < 8:
                    break
                f.seek(off)
                out.append((h or "?", idx, f.read(size)))
                idx += 1
                off += size
    return out


def sound_fields(e: bytes) -> dict:
    """SoundDescription v1 (+ the four u32 that follow samplerate)."""
    if len(e) < 36:
        return {"_short": True}
    v = {"fourcc": e[4:8].decode("latin-1"), "size": len(e)}
    (v["version"], v["revision"]) = struct.unpack(">HH", e[16:20])
    v["vendor"] = struct.unpack(">I", e[20:24])[0]
    (v["channelcount"], v["samplesize"], v["compression_id"],
     v["packet_size"]) = struct.unpack(">HHHH", e[24:32])
    v["samplerate"] = struct.unpack(">I", e[32:36])[0] / 65536.0
    if len(e) >= 52:
        v["tail4"] = list(struct.unpack(">IIII", e[36:52]))
    # 子 box 从 52 开始 (Sony XAVC 的 layout)
    kids = []
    off = 52
    while off + 8 <= len(e):
        size, typ = struct.unpack(">I4s", e[off:off + 8])
        if size < 8 or off + size > len(e):
            break
        kids.append((typ.decode("latin-1"), size, e[off + 8:off + size].hex()))
        off += size
    v["children"] = kids
    return v


def main() -> int:
    a_tag, _, a_p = sys.argv[1].partition("=")
    b_tag, _, b_p = sys.argv[2].partition("=")
    ea, eb = entries(Path(a_p)), entries(Path(b_p))
    print(f"A={a_tag} ({len(ea)} entries)   B={b_tag} ({len(eb)} entries)")
    for i, (ha, idxa, ba) in enumerate(ea):
        hb, idxb, bb = eb[i] if i < len(eb) else ("?", -1, b"")
        print(f"\n--- [{i}] handler={ha} entry_size A={len(ba)} B={len(bb)}")
        if ha == "soun":
            fa, fb = sound_fields(ba), sound_fields(bb)
            for k in fa:
                va, vb = fa[k], fb.get(k)
                mark = "" if va == vb else "   <<< DIFF"
                print(f"    {k:<14} A={va!r:<40} B={vb!r}{mark}")
        else:
            # 视频/数据条目: 只报 fourcc 与前 16 字节是否有差异
            print(f"    fourcc A={ba[4:8]!r} B={bb[4:8]!r}")
            n = min(len(ba), len(bb))
            diff = [k for k in range(n) if ba[k] != bb[k]]
            print(f"    差异字节数={len(diff)}"
                  + (f" 前几处={diff[:12]}" if diff else "  (逐字节相同)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
