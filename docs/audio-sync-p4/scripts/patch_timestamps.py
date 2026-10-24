#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 mvhd/tkhd/mdhd 的 creation/modification time 从源复制到还原产物（原地）。

GPAC 重建容器时会写新的 creation/modification time（源是拍摄时间，还原产物是
处理时间）；这是源与还原产物之间最后几处可无损消除的差异之一。时间字段等长，
只改 moov 内数值，不影响 sample table / mdat / 时长。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402


def _copy_times(fa, fa_box, fb, fb_box, label: str, desc: list) -> None:
    for lo in isobmf._children(fa, fa_box):
        if lo.type not in ("mvhd", "tkhd", "mdhd"):
            continue
        hi = next((c for c in isobmf._children(fb, fb_box) if c.type == lo.type),
                  None)
        if hi is None:
            continue
        fa.seek(lo.data_offset)
        va = fa.read(1)[0]
        fb.seek(hi.data_offset)
        vb = fb.read(1)[0]
        if va != vb:
            raise RuntimeError(f"{label}/{lo.type} version mismatch")
        n = 8 if va == 1 else 4
        fa.seek(lo.data_offset + 4)
        times_a = fa.read(2 * n)
        fb.seek(hi.data_offset + 4)
        times_b = fb.read(2 * n)
        if times_a != times_b:
            fb.seek(hi.data_offset + 4)
            fb.write(times_a)
            desc.append(f"{label}/{lo.type}: "
                        f"{times_b.hex()} -> {times_a.hex()}")


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    desc: list[str] = []
    with src.open("rb") as fa, dst.open("r+b") as fb:
        moov_a = next(b for b in isobmf.root_boxes(src) if b.type == "moov")
        moov_b = next(b for b in isobmf.root_boxes(dst) if b.type == "moov")
        _copy_times(fa, moov_a, fb, moov_b, "moov", desc)
        traks_a = [c for c in isobmf._children(fa, moov_a) if c.type == "trak"]
        traks_b = [c for c in isobmf._children(fb, moov_b) if c.type == "trak"]
        if len(traks_a) != len(traks_b):
            raise RuntimeError("track count mismatch")
        for i, (ta, tb) in enumerate(zip(traks_a, traks_b)):
            _copy_times(fa, ta, fb, tb, f"trak[{i}]", desc)
    print("\n".join(desc) or "no timestamp changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
