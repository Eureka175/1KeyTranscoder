#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出每个 trak 的 stsd sample entry 及其内部 box（hvcC/btrt/pcmC...），
用于确认 GPAC 还原后 video sample entry 与源到底差在哪里。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402

AUDIO_ENTRY_HEADER = 28   # AudioSampleEntry v0
VISUAL_ENTRY_HEADER = 78  # VisualSampleEntry 固定头


def main() -> int:
    for p in map(Path, sys.argv[1:]):
        print("==", p)
        roots = isobmf.root_boxes(p)
        moov = next(b for b in roots if b.type == "moov")
        with p.open("rb") as f:
            for trak in isobmf._children(f, moov):
                if trak.type != "trak":
                    continue
                handler = isobmf._trak_handler(f, trak)
                stsd = None
                for mdia in isobmf._children(f, trak):
                    if mdia.type != "mdia":
                        continue
                    for minf in isobmf._children(f, mdia):
                        if minf.type != "minf":
                            continue
                        for stbl in isobmf._children(f, minf):
                            if stbl.type != "stbl":
                                continue
                            for c in isobmf._children(f, stbl):
                                if c.type == "stsd":
                                    stsd = c
                if stsd is None:
                    print(" ", handler, "no stsd")
                    continue
                entry = isobmf._read_header(f, stsd.data_offset + 8, stsd.end)
                f.seek(entry.start)
                raw = f.read(entry.size)
                header = AUDIO_ENTRY_HEADER if handler == "soun" else (
                    VISUAL_ENTRY_HEADER if handler in ("vide",) else 8)
                print(f"  {handler} entry={raw[4:8].decode('latin1')} "
                      f"size={entry.size}")
                off = header
                while off + 8 <= entry.size:
                    size = int.from_bytes(raw[off:off + 4], "big")
                    typ = raw[off + 4:off + 8].decode("latin1")
                    if size < 8 or off + size > entry.size:
                        break
                    payload = raw[off + 8:off + size]
                    print(f"    {typ} size={size} "
                          f"sha256={hashlib.sha256(payload).hexdigest()[:16]}")
                    off += size
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
