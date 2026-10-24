#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对已验证的 GPAC 还原产物做最后一轮"流格式"细化（一次顺序拷贝）：

目标：把 source 与 restored 之间剩余的可无损消除的容器差异补齐，尽量贴近
原始 XAVC 的 moov 结构：

1. mvhd/tkhd/mdhd 的 creation/modification time 从源复制（GPAC 会写处理时间）；
2. 每个 trak 的 stsd sample entry 用源条目替换（GPAC 会在 hvc1 entry 内多写
   btrt，video entry 755 -> 789 字节）；
3. 源没有而 GPAC 补写的 minf/hdlr box 删除（C1305 上 4 条音轨各多一个）。

所有操作只改 moov；因为 moov 变小/变大导致 mdat 位移，同步修正全部 co64/stco
chunk offset。mdat 字节、sample payload、track 时长均不变。
"""
from __future__ import annotations

import argparse
import io
import json
import struct
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402


def load_moov(path: Path) -> tuple[bytes, isobmf.Box]:
    roots = isobmf.root_boxes(path)
    box = next(b for b in roots if b.type == "moov")
    with path.open("rb") as f:
        f.seek(box.start)
        return f.read(box.size), box


def children_buf(buf: bytes) -> list[isobmf.Box]:
    bio = io.BytesIO(buf)
    out, off = [], 0
    while off < len(buf):
        b = isobmf._read_header(bio, off, len(buf))
        if b is None:
            break
        out.append(b)
        off = b.end
    return out


def traks(moov: bytearray) -> list[dict]:
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    mb = children_buf(buf)[0]
    out = []
    for t in isobmf._children(bio, mb):
        if t.type != "trak":
            continue
        out.append({"box": t,
                    "handler": isobmf._trak_handler(bio, t),
                    "kids": isobmf._children(bio, t)})
    return out


def chain(moov: bytearray, trak_index: int, target: str) -> list[isobmf.Box] | None:
    """返回从 trak 到 target 的 box 链（含两端），用于改祖先 size。"""
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    mb = children_buf(buf)[0]
    trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
    if trak_index >= len(trks):
        return None
    trak = trks[trak_index]

    def dfs(node) -> list[isobmf.Box] | None:
        for c in isobmf._children(bio, node):
            if c.type == target:
                return [trak, node, c]
            if c.type in ("mdia", "minf", "stbl", "edts"):
                hit = dfs(c)
                if hit:
                    return [trak] + hit
        return None

    return dfs(trak)


def adjust_sizes(moov: bytearray, boxes, delta: int) -> None:
    for b in boxes:
        old = struct.unpack(">I", moov[b.start:b.start + 4])[0]
        moov[b.start:b.start + 4] = struct.pack(">I", old + delta)


def remove_box(moov: bytearray, boxes) -> None:
    box = boxes[-1]
    for b in boxes:
        if b is not box:
            adjust_sizes(moov, [b], -box.size)
    del moov[box.start:box.end]


def replace_box(moov: bytearray, boxes, payload: bytes) -> None:
    box = boxes[-1]
    delta = len(payload) - box.size
    for b in boxes:
        if b is not box:
            adjust_sizes(moov, [b], delta)
    moov[box.start:box.end] = payload


def source_entry(src_moov: bytes, trak_index: int) -> bytes | None:
    buf = bytes(src_moov)
    bio = io.BytesIO(buf)
    mb = children_buf(buf)[0]
    trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
    if trak_index >= len(trks):
        return None
    t = trks[trak_index]
    stsd = None
    for mdia in isobmf._children(bio, t):
        if mdia.type != "mdia":
            continue
        for minf in isobmf._children(bio, mdia):
            if minf.type != "minf":
                continue
            for stbl in isobmf._children(bio, minf):
                if stbl.type != "stbl":
                    continue
                for c in isobmf._children(bio, stbl):
                    if c.type == "stsd":
                        stsd = c
    if stsd is None:
        return None
    entry = isobmf._read_header(bio, stsd.data_offset + 8, stsd.end)
    return buf[entry.start:entry.end]


def copy_times(moov: bytearray, src_moov: bytes, desc: list[str]) -> None:
    """mvhd + 每个 trak 的 tkhd/mdhd creation/mod 时间从源复制。"""
    def find_clh(buf: bytes, index: int | None, typ: str):
        bio = io.BytesIO(buf)
        mb = children_buf(buf)[0]
        if index is None:
            for c in isobmf._children(bio, mb):
                if c.type == typ:
                    return c
            return None
        trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
        if index >= len(trks):
            return None
        t = trks[index]
        for mdia in isobmf._children(bio, t):
            if mdia.type == "mdia":
                for c in isobmf._children(bio, mdia):
                    if c.type == typ and typ == "mdhd":
                        return c
        for c in isobmf._children(bio, t):
            if c.type == typ:
                return c
        return None

    def copy_one(dst_buf: bytearray, src_buf: bytes, index, typ):
        d = find_clh(bytes(dst_buf), index, typ)
        s = find_clh(src_buf, index, typ)
        if not d or not s:
            return
        verse = src_buf[s.data_offset]
        if dst_buf[d.data_offset] != verse:
            return
        n = 8 if verse == 1 else 4
        d_start = d.data_offset + 4
        s_start = s.data_offset + 4
        if bytes(dst_buf[d_start:d_start + 2 * n]) != src_buf[s_start:s_start + 2 * n]:
            dst_buf[d_start:d_start + 2 * n] = src_buf[s_start:s_start + 2 * n]
            desc.append(f"{'moov' if index is None else f'trak[{index}]'}/{typ}")

    copy_one(moov, src_moov, None, "mvhd")
    n = len(traks(moov))
    for i in range(n):
        copy_one(moov, src_moov, i, "tkhd")
        copy_one(moov, src_moov, i, "mdhd")


def find_chain2(moov: bytearray, trak_index: int, types: tuple[str, ...]):
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    mb = children_buf(buf)[0]
    trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
    if trak_index >= len(trks):
        return None
    node = trks[trak_index]
    chain = [node]
    for typ in types:
        nxt = next((c for c in isobmf._children(bio, node) if c.type == typ),
                   None)
        if nxt is None:
            return None
        chain.append(nxt)
        node = nxt
    return chain


def box_bytes(moov: bytearray, chain) -> bytes:
    return bytes(moov[chain[-1].start:chain[-1].end])


def patch_offsets(moov: bytearray, delta: int) -> list[str]:
    descs = []
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    mb = children_buf(buf)[0]
    for trak in isobmf._children(bio, mb):
        if trak.type != "trak":
            continue
        for mdia in isobmf._children(bio, trak):
            if mdia.type != "mdia":
                continue
            for minf in isobmf._children(bio, mdia):
                if minf.type != "minf":
                    continue
                for stbl in isobmf._children(bio, minf):
                    if stbl.type != "stbl":
                        continue
                    for c in isobmf._children(bio, stbl):
                        if c.type not in ("stco", "co64"):
                            continue
                        do = c.data_offset
                        cnt = struct.unpack(">I", buf[do + 4:do + 8])[0]
                        for i in range(cnt):
                            if c.type == "stco":
                                p = do + 8 + 4 * i
                                v = struct.unpack(">I", buf[p:p + 4])[0]
                                moov[p:p + 4] = struct.pack(">I", v + delta)
                            else:
                                p = do + 8 + 8 * i
                                v = struct.unpack(">Q", buf[p:p + 8])[0]
                                moov[p:p + 8] = struct.pack(">Q", v + delta)
                        descs.append(f"{c.type}: {cnt} offsets {delta:+d}")
    return descs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--restored", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    src, rst, out = Path(args.source), Path(args.restored), Path(args.out)
    src_moov, _ = load_moov(src)
    dst_moov, moov_box = load_moov(rst)
    moov = bytearray(dst_moov)
    descs: dict = {"timestamps": [], "stsd": [], "minf_hdlr_removed": [],
                   "offsets": []}

    copy_times(moov, src_moov, descs["timestamps"])

    # stsd entry 替换（从后往前，避免偏移漂移）
    n = len(traks(moov))
    for i in reversed(range(n)):
        ch = chain(moov, i, "stsd")
        if not ch:
            continue
        stsd = ch[-1]
        buf = bytes(moov)
        bio = io.BytesIO(buf)
        entry = isobmf._read_header(bio, stsd.data_offset + 8, stsd.end)
        cur = buf[entry.start:entry.end]
        new = source_entry(src_moov, i)
        if new is None or new == cur:
            continue
        ch2 = chain(moov, i, "stsd")
        # chain 返回 [trak, (mdia), (minf), (stbl), stsd]? 重新构造含 stsd 的祖先
        buf = bytes(moov)
        bio = io.BytesIO(buf)
        mb = children_buf(buf)[0]
        trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
        trak = trks[i]
        ancestors = [trak]
        node = trak
        for typ in ("mdia", "minf", "stbl", "stsd"):
            nxt = next((c for c in isobmf._children(bio, node)
                        if c.type == typ), None)
            if nxt is None:
                break
            ancestors.append(nxt)
            node = nxt
        stsd_box = ancestors[-1]
        entry = isobmf._read_header(bio, stsd_box.data_offset + 8, stsd_box.end)
        # 把 entry 当作要替换的 box：祖先链 = 除 stsd 外的祖先 + entry
        delta = len(new) - entry.size
        for b in ancestors:
            adjust_sizes(moov, [b], delta)
        moov[entry.start:entry.end] = new
        moov[0:4] = struct.pack(">I", len(moov))
        descs["stsd"].append(f"trak[{i}]: entry {entry.size} -> {len(new)} bytes")

    # 删除源没有的 minf/hdlr
    n = len(traks(moov))
    for i in reversed(range(n)):
        buf = bytes(moov)
        bio = io.BytesIO(buf)
        mb = children_buf(buf)[0]
        trks = [c for c in isobmf._children(bio, mb) if c.type == "trak"]
        if i >= len(trks):
            continue
        trak = trks[i]
        minf = None
        for mdia in isobmf._children(bio, trak):
            if mdia.type == "mdia":
                for c in isobmf._children(bio, mdia):
                    if c.type == "minf":
                        minf = c
        if minf is None:
            continue
        has_src_hdlr = b"hdlr" in bytes(src_moov[
            next((c for c in isobmf._children(io.BytesIO(src_moov),
                        children_buf(src_moov)[0]) if c.type == "trak"), None).start:
            ]) if False else None
        # 判断源同 index trak 的 minf 是否有 hdlr
        def minf_children_types(moov_bytes: bytes, index: int):
            bio2 = io.BytesIO(moov_bytes)
            mb2 = children_buf(moov_bytes)[0]
            tr = [c for c in isobmf._children(bio2, mb2) if c.type == "trak"][index]
            for md in isobmf._children(bio2, tr):
                if md.type == "mdia":
                    for c in isobmf._children(bio2, md):
                        if c.type == "minf":
                            return [x.type for x in isobmf._children(bio2, c)]
            return []

        src_kids = minf_children_types(src_moov, i) if i < len(
            [c for c in isobmf._children(io.BytesIO(src_moov),
                                         children_buf(src_moov)[0])
             if c.type == "trak"]) else []
        dst_kids = [c.type for c in isobmf._children(bio, minf)]
        if "hdlr" not in src_kids and "hdlr" in dst_kids:
            hdlr = next(c for c in isobmf._children(bio, minf)
                        if c.type == "hdlr")
            # 祖先链: trak, mdia, minf, hdlr
            ancestors = [trak]
            node = trak
            for typ in ("mdia", "minf"):
                node = next(c for c in isobmf._children(bio, node)
                            if c.type == typ)
                ancestors.append(node)
            ancestors.append(hdlr)
            for b in ancestors[:-1]:
                adjust_sizes(moov, [b], -hdlr.size)
            moov[hdlr.start:hdlr.end] = b""
            moov[0:4] = struct.pack(">I", len(moov))
            descs["minf_hdlr_removed"].append(f"trak[{i}] -{hdlr.size} bytes")

    # 复制源的 mdhd 与 mdia/hdlr（GPAC 会改写 language=0x7fff、handler 名字/
    # 保留字段与源不同；这些是纯描述字段，逐字节对齐可完全消除差异）。
    n = len(traks(moov))
    for i in reversed(range(n)):
        for types in (("mdia", "mdhd"), ("mdia", "hdlr")):
            ch = find_chain2(moov, i, types)
            ch_src = find_chain2(bytearray(src_moov), i, types)
            if not ch or not ch_src:
                continue
            cur = box_bytes(moov, ch)
            want = box_bytes(bytearray(src_moov), ch_src)
            if cur == want:
                continue
            delta = len(want) - len(cur)
            for b in ch[:-1]:
                adjust_sizes(moov, [b], delta)
            moov[ch[-1].start:ch[-1].end] = want
            moov[0:4] = struct.pack(">I", len(moov))
            descs.setdefault("mdhd_hdlr", []).append(
                f"trak[{i}] {types[-1]} {len(cur)} -> {len(want)} bytes")

    old_moov_size = len(dst_moov)
    delta = len(moov) - old_moov_size
    # 所有子 box 的 size 已按各自 delta 修正，但 root moov 自己的 size 也要更新。
    moov[0:4] = struct.pack(">I", len(moov))
    descs["offsets"] = patch_offsets(moov, delta)

    # 写出：保留 restored 的 ftyp 与 mdat 之后的所有 root box
    roots = isobmf.root_boxes(rst)
    ftyp = next(b for b in roots if b.type == "ftyp")
    with rst.open("rb") as f, out.open("wb") as o:
        f.seek(ftyp.start)
        o.write(f.read(ftyp.size))
        o.write(bytes(moov))
        f.seek(moov_box.end)
        remaining = rst.stat().st_size - moov_box.end
        while remaining > 0:
            chunk = f.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                break
            o.write(chunk)
            remaining -= len(chunk)
    rep = {"source": str(src), "restored": str(rst), "out": str(out),
           "delta": delta, "moov": [old_moov_size, len(moov)],
           **descs}
    Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
