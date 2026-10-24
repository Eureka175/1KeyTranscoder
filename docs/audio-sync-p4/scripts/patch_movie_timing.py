#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把还原产物的 movie/track 时长与时间基修正成与源一致（原地、只改 moov）。

GPAC `-new` 对部分机位会把 movie timescale 选错（实测 A7M4 源 50000 →
GPAC 输出 5000），mdhd 已由 patch_mdhd.py 修好，但 mvhd/tkhd/elst 里的时长仍
在错误的 movie 时间基下。这里直接从源文件复制这些**纯数值**字段：

  * mvhd: timescale + duration
  * 每条 trak 的 tkhd: duration
  * 每条 trak 的 edts/elst: entries 原样复制（media_time 属于媒体时间基，
    与源一致；只修正 segment_duration 的 movie 时间基）

只处理 trak 顺序/handler 与源一致的还原文件；不触碰 sample table / mdat。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402


def _u32(b: bytes, off: int) -> int:
    return struct.unpack(">I", b[off:off + 4])[0]


def _mvhd_values(f, root, moov) -> tuple[int, int, int]:
    """返回 (version, timescale, duration, data_offset)。"""
    for c in isobmf._children(f, moov):
        if c.type != "mvhd":
            continue
        f.seek(c.data_offset)
        head = f.read(32)
        ver = head[0]
        if ver == 0:
            return 0, _u32(head, 12), _u32(head, 16), c.data_offset
        ts = _u32(head, 20)
        dur = struct.unpack(">Q", head[24:32])[0]
        return 1, ts, dur, c.data_offset
    raise RuntimeError("no mvhd")


def _tkhd_values(f, trak) -> tuple[int, int, int]:
    for c in isobmf._children(f, trak):
        if c.type != "tkhd":
            continue
        f.seek(c.data_offset)
        head = f.read(36)
        ver = head[0]
        if ver == 0:
            return 0, _u32(head, 20), c.data_offset
        return 1, struct.unpack(">Q", head[28:36])[0], c.data_offset
    raise RuntimeError("no tkhd")


def _edts_elst(f, trak):
    for c in isobmf._children(f, trak):
        if c.type != "edts":
            for e in isobmf._children(f, c):
                if e.type == "elst":
                    return c, e
    return None, None


def patch(src: Path, dst: Path) -> list[str]:
    descs = []
    fa = src.open("rb")
    fb = dst.open("r+b")
    try:
        moov_a = next(b for b in isobmf.root_boxes(src) if b.type == "moov")
        moov_b = next(b for b in isobmf.root_boxes(dst) if b.type == "moov")
        va, ts_a, dur_a, off_a = _mvhd_values(fa, None, moov_a)
        vb, ts_b, dur_b, off_b = _mvhd_values(fb, None, moov_b)
        if ts_a != ts_b or dur_a != dur_b:
            if va != vb:
                raise RuntimeError("mvhd version mismatch")
            fb.seek(off_b + (12 if vb == 0 else 20))
            fb.write(struct.pack(">I", ts_a))
            fb.seek(off_b + (16 if vb == 0 else 24))
            if vb == 0:
                fb.write(struct.pack(">I", dur_a))
            else:
                fb.write(struct.pack(">Q", dur_a))
            descs.append(f"mvhd timescale {ts_b}->{ts_a} duration {dur_b}->{dur_a}")
        traks_a = [c for c in isobmf._children(fa, moov_a) if c.type == "trak"]
        traks_b = [c for c in isobmf._children(fb, moov_b) if c.type == "trak"]
        if len(traks_a) != len(traks_b):
            raise RuntimeError("track count mismatch")
        for i, (ta, tb) in enumerate(zip(traks_a, traks_b)):
            ha = isobmf._trak_handler(fa, ta)
            hb = isobmf._trak_handler(fb, tb)
            if ha != hb:
                raise RuntimeError(f"track {i} handler mismatch {ha} vs {hb}")
            va_t, dur_src, off_tk_a = _tkhd_values(fa, ta)
            vb_t, dur_dst, off_tk_b = _tkhd_values(fb, tb)
            if va_t != vb_t:
                raise RuntimeError(f"track {i} tkhd version mismatch")
            if dur_src != dur_dst:
                fb.seek(off_tk_b + (20 if vb_t == 0 else 28))
                fb.write(struct.pack(">I" if vb_t == 0 else ">Q", dur_src))
                descs.append(f"{ha} tkhd duration {dur_dst}->{dur_src}")
            ea, la = _edts_elst(fa, ta)
            eb, lb = _edts_elst(fb, tb)
            if la is not None and lb is not None:
                fa.seek(la.start)
                raw_a = fa.read(la.size)
                fb.seek(lb.start)
                raw_b = fb.read(lb.size)
                # entries 在 box header(8)+version/flags(4)+count(4) 之后
                if raw_a[:16] == raw_b[:16] and len(raw_a) == len(raw_b):
                    if raw_a != raw_b:
                        fb.seek(lb.start)
                        fb.write(raw_a)
                        descs.append(f"{ha} elst entries copied")
            elif la is not None and lb is None:
                descs.append(f"{ha} WARNING: source has edts/elst, restored has none")
    finally:
        fa.close()
        fb.close()
    return descs


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    descs = patch(src, dst)
    print("\n".join(descs) or "no timing changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
