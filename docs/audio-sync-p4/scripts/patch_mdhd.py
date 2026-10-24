#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把每条轨的 mdhd duration 修成 stts 总和（媒体时基的精确时长）。

GPAC `mux_new` 写出的 mdhd duration 会带 edit-list media_time 偏移
(C1305 视频: 288440152 = stts 288438150 + 2002)，与源不一致；Gyroflow 等
消费方用 mdhd 判断视频时长，可能因此拒绝 metadata。此脚本原地修正，
不改动 sample table / mdat。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402


def stts_sum(f, trak) -> int | None:
    for c in isobmf._children(f, trak):
        if c.type != "mdia":
            continue
        for d in isobmf._children(f, c):
            if d.type != "mdhd":
                continue
            # mdhd 里没有 stts；向下找 minf/stbl
            for e in isobmf._children(f, c):
                if e.type != "minf":
                    continue
                for g in isobmf._children(f, e):
                    if g.type != "stbl":
                        continue
                    for h in isobmf._children(f, g):
                        if h.type == "stts":
                            f.seek(h.data_offset + 4)
                            n = struct.unpack(">I", f.read(4))[0]
                            raw = f.read(8 * n)
                            total = 0
                            for i in range(n):
                                cnt, delta = struct.unpack(
                                    ">II", raw[8 * i:8 * i + 8])
                                total += cnt * delta
                            return total
    return None


def main() -> int:
    path = Path(sys.argv[1])
    changed = []
    with path.open("r+b") as f:
        roots = isobmf.root_boxes(path)
        moov = next(b for b in roots if b.type == "moov")
        for trak in isobmf._children(f, moov):
            if trak.type != "trak":
                continue
            handler = isobmf._trak_handler(f, trak)
            total = stts_sum(f, trak)
            if total is None:
                continue
            for mdia in isobmf._children(f, trak):
                if mdia.type != "mdia":
                    continue
                for mdhd in isobmf._children(f, mdia):
                    if mdhd.type != "mdhd":
                        continue
                    f.seek(mdhd.data_offset)
                    ver = f.read(1)[0]
                    if ver == 0:
                        f.seek(mdhd.data_offset + 16)
                        old = struct.unpack(">I", f.read(4))[0]
                        if old != total:
                            f.seek(mdhd.data_offset + 16)
                            f.write(struct.pack(">I", total))
                            changed.append(f"{handler}: mdhd {old} -> {total}")
                    else:
                        f.seek(mdhd.data_offset + 24)
                        old = struct.unpack(">Q", f.read(8))[0]
                        if old != total:
                            f.seek(mdhd.data_offset + 24)
                            f.write(struct.pack(">Q", total))
                            changed.append(f"{handler}: mdhd {old} -> {total}")
    print("\n".join(changed) or "no mdhd changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
