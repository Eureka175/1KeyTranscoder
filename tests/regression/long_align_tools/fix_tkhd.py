#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streaming in-place MP4/MOV tkhd fixer (see fix_tkhd for the rationale).

Root cause: ffmpeg puts extra audio tracks in the same alternate_group=1 and writes
tkhd flags=2 (not enabled) for them.  MP4/MOV semantics treat an alternate group as
"pick one", so Adobe Premiere imports the four mono PCM tracks as ONE selectable
audio track, while MediaInfo still lists four.  The Sony source has flags=7 and
alternate_group=0 on every track.

This streaming version only reads/writes the moov box and a few header bytes, so it is
safe for 40+ GiB files: the mdat payload is seeked over and never read or written.
Originals are recorded in <file>.tkhd.json for `--revert`.

Usage: python fix_tkhd.py FILE [FILE ...] [--revert]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

CONTAINERS = {"moov", "trak", "mdia", "minf", "udta", "meta", "ilst",
              "moof", "traf", "mvex", "edts", "dinf", "stbl", "mfra"}
SIDECAR_SUFFIX = ".tkhd.json"


def box_header(data: bytes, off: int, end: int):
    if off + 8 > end:
        return None
    size = struct.unpack(">I", data[off:off + 4])[0]
    typ = data[off + 4:off + 8].decode("latin1")
    hdr = 8
    if size == 1:
        if off + 16 > end:
            return None
        size = struct.unpack(">Q", data[off + 8:off + 16])[0]
        hdr = 16
    elif size == 0:
        size = end - off
    if size < hdr or off + size > end:
        return None
    return typ, off, size, hdr


def iter_boxes(data: bytes, start: int, end: int):
    off = start
    while True:
        got = box_header(data, off, end)
        if got is None:
            break
        typ, b_off, size, hdr = got
        yield typ, b_off, size, hdr
        if typ in CONTAINERS:
            child = b_off + hdr + (4 if typ == "meta" else 0)
            yield from iter_boxes(data, child, b_off + size)
        off = b_off + size


def tkhd_fields(data: bytes, off: int, size: int) -> dict:
    ver = data[off + 8]
    flags = int.from_bytes(data[off + 9:off + 12], "big")
    tid_off = off + 20 if ver == 0 else off + 28
    alt_off = off + 42 if ver == 0 else off + 54
    return {"ver": ver, "flags": flags,
            "track_id": struct.unpack(">I", data[tid_off:tid_off + 4])[0],
            "alt_group": struct.unpack(">H", data[alt_off:alt_off + 2])[0],
            "flags_off": off + 8, "alt_off": alt_off}


def patch_file(path: Path, revert: bool = False) -> list[dict]:
    sidecar = path.with_name(path.name + SIDECAR_SUFFIX)
    size = path.stat().st_size
    with path.open("r+b") as fh:
        moov_off = moov_size = None
        pos = 0
        while pos + 8 <= size:
            fh.seek(pos)
            head = fh.read(16)
            if len(head) < 8:
                break
            bsize = struct.unpack(">I", head[:4])[0]
            typ = head[4:8].decode("latin1")
            hdr = 8
            if bsize == 1 and len(head) >= 16:
                bsize = struct.unpack(">Q", head[8:16])[0]
                hdr = 16
            elif bsize == 0:
                bsize = size - pos
            if bsize < hdr:
                break
            if typ == "moov":
                moov_off, moov_size = pos, bsize
                break
            pos += bsize
        if moov_off is None:
            raise SystemExit(f"{path}: no moov box found")
        fh.seek(moov_off)
        moov = fh.read(moov_size)
        if len(moov) != moov_size:
            raise SystemExit(f"{path}: truncated moov")
        found = []
        for typ, off, bsize, hdr in iter_boxes(moov, 0, len(moov)):
            if typ != "tkhd":
                continue
            f = tkhd_fields(moov, off, bsize)
            found.append({"track_id": f["track_id"],
                          "flags": f["flags"], "alt_group": f["alt_group"],
                          "ver": f["ver"],
                          "abs_flags_off": moov_off + f["flags_off"],
                          "abs_alt_off": moov_off + f["alt_off"]})
        if not found:
            raise SystemExit(f"{path}: no tkhd boxes")
        if not revert:
            sidecar.write_text(json.dumps(found, indent=1), encoding="utf-8")
            for s in found:
                fh.seek(s["abs_flags_off"])
                fh.write(struct.pack(">I", (s["ver"] << 24) | 7))
                fh.seek(s["abs_alt_off"])
                fh.write(struct.pack(">H", 0))
        else:
            if sidecar.is_file():
                saved = json.loads(sidecar.read_text(encoding="utf-8"))
            else:
                saved = found
            for s in saved:
                fh.seek(s["abs_flags_off"])
                fh.write(struct.pack(">I", (s["ver"] << 24) | s["flags"]))
                fh.seek(s["abs_alt_off"])
                fh.write(struct.pack(">H", s["alt_group"]))
    return found


def main(argv: list[str]) -> int:
    revert = "--revert" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    for path in files:
        if not path.is_file():
            print(f"missing: {path}")
            continue
        found = patch_file(path, revert=revert)
        action = "reverted" if revert else "patched"
        print(f"{path.name}: {action}")
        for s in found:
            if revert:
                print(f"  track {s['track_id']}: flags -> {s['flags']}, "
                      f"alternate_group -> {s['alt_group']}")
            else:
                print(f"  track {s['track_id']}: flags {s['flags']} -> 7, "
                      f"alternate_group {s['alt_group']} -> 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
