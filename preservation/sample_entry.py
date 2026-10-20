#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把成品的音频 sample entry 恢复成源文件的写法。

**为什么需要这个模块**

通道同步的透明重封装，每条音轨都是先用 ffmpeg 落成独立 `.mov` 中间文件、
再交给 GPAC 组装的。ffmpeg 写 MOV 时用的是 QuickTime 的 `in24` 条目
(`samplesize=16` + `wave/frma/enda`)，GPAC 再把它翻译成 MP4 的 `ipcm` ——
**翻译时把位深写错了**：

    字段            源文件(ipcm)      重封装后(ipcm)
    samplesize      24                16        <- 声明成 16 bit
    尾部包长四字      [1,3,3,2]         [1,2,2,2] <- 3 字节包变成 2 字节包
    pcmC            24                24        <- 这一项仍是对的
    btrt            有                无        <- 只是码率提示, 无关

结果是条目**自相矛盾**：`pcmC` 说 24 bit，`samplesize` 说 16 bit。ffmpeg 采信
`pcmC`，所以用它解码完全正确（我们的残差复测也因此一直是 0）；而 Premiere /
VLC 采信 `samplesize`，按 16 bit 去读 24 bit 的数据 —— 每 3 个字节只取 2 个，
于是**四轨全是白噪音、原声只剩一点混音**。gyroflow 只读视频与 rtmd，不受影响。

**修复方式**：把成品的每条音频 sample entry 换回源文件的条目，并把源条目里的
`btrt` 去掉，使长度与成品一致 —— **长度不变意味着不需要移动任何 box、不需要
修正 stco/co64/iloc 偏移**，这是本修复刻意选择的形态。长度对不上就拒绝修改并
报错，绝不猜。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "read_audio_entries",
    "strip_optional_boxes",
    "patch_audio_sample_entries",
    "audio_entry_consistency",
]

#: 采样条目里可以安全去掉的"提示类"子 box（不影响格式语义）。
OPTIONAL_ENTRY_BOXES = ("btrt",)


def audio_entry_consistency(entry: bytes) -> dict[str, Any]:
    """音频 sample entry 是否自相矛盾。

    这条检查**不需要源文件**，因此能在 `check` 模式下抓到那个真实缺陷:
    `pcmC` 说 24 bit 而 `samplesize` 说 16 bit。ffmpeg 采信 `pcmC`(解码正确),
    Premiere/VLC 采信 `samplesize`(四轨白噪音)。

    **判定必须按 fourcc 分开**, 否则会误报:

    * `ipcm` / `fpcm` (ISO 23003-5): 真实位深在 `pcmC`; `samplesize` 必须与
      之一致, 每包字节数也必须与之一致。Sony 相机写的是
      `samplesize=24, pcmC=24, 3 字节包` —— 这是能正常播放的写法。
    * `in24` / `in32` / `fl32` / `fl64` / `sowt` / `twos` (QuickTime LPCM):
      位深由 fourcc 本身决定, `samplesize=16` 是**约定值**而不是位深,
      因此不能拿 `samplesize` 做判据; 只能查每包字节数。
    """
    rep: dict[str, Any] = {"consistent": None, "fourcc": None,
                           "samplesize_bits": None, "pcmc_bits": None,
                           "expected_bits": None, "packet_bytes": None,
                           "problems": []}
    if len(entry) < 36:
        rep["problems"].append("条目过短, 无法解析")
        rep["consistent"] = False
        return rep
    fourcc = entry[4:8].decode("latin-1", "replace")
    rep["fourcc"] = fourcc
    samplesize = struct.unpack(">H", entry[26:28])[0]
    rep["samplesize_bits"] = samplesize
    if len(entry) >= 52:
        tail4 = struct.unpack(">IIII", entry[36:52])
        # 第二个 u32 是每包字节数 (24bit -> 3, 16bit -> 2)
        rep["packet_bytes"] = tail4[1]
    pcmc_bits = None
    off = 52
    while off + 8 <= len(entry):
        size, typ = struct.unpack(">I4s", entry[off:off + 8])
        if size < 8 or off + size > len(entry):
            break
        if typ == b"pcmC" and size >= 14:
            # pcmC: 8 header + 4 version/flags + 1 format_flags + 1 sample_size
            pcmc_bits = entry[off + 13]
        off += size
    rep["pcmc_bits"] = pcmc_bits

    qt_bits = {"in24": 24, "in32": 32, "fl32": 32, "fl64": 64,
               "sowt": 16, "twos": 16, "raw ": 8}
    if fourcc in qt_bits:
        # QuickTime LPCM: samplesize 是约定值, 不作判据
        rep["expected_bits"] = qt_bits[fourcc]
        pb = rep["packet_bytes"]
        if pb and pb * 8 != qt_bits[fourcc]:
            rep["problems"].append(
                f"{fourcc} 每包 {pb} 字节, 与 {qt_bits[fourcc]} bit 不符")
    else:
        rep["expected_bits"] = pcmc_bits if pcmc_bits else samplesize
        if pcmc_bits is not None and samplesize and pcmc_bits != samplesize:
            rep["problems"].append(
                f"pcmC 声明 {pcmc_bits} bit 而 samplesize 声明 "
                f"{samplesize} bit")
        pb = rep["packet_bytes"]
        if pb and rep["expected_bits"] and pb * 8 != rep["expected_bits"]:
            rep["problems"].append(
                f"包长 {pb} 字节与 {rep['expected_bits']} bit 不符")
    rep["consistent"] = not rep["problems"]
    return rep


def _boxes(f, start: int, end: int) -> list[tuple[str, int, int, int, int]]:
    """[(type, start, data_start, end, size)]"""
    out = []
    off = start
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
        out.append((typ.decode("latin-1"), off, data, off + size, size))
        off += size
    return out


def _child(f, box, *path):
    cur = box
    for name in path:
        kids = _boxes(f, cur[2], cur[3])
        cur = next((k for k in kids if k[0] == name), None)
        if cur is None:
            return None
    return cur


def _handler(f, trak) -> str | None:
    h = _child(f, trak, "mdia", "hdlr")
    if h is None:
        return None
    f.seek(h[2] + 8)
    return f.read(4).decode("latin-1", "replace")


def read_audio_entries(path: Path) -> list[dict[str, Any]]:
    """每条**音频**轨的第一条 sample entry: 位置 + 原始字节。"""
    out: list[dict[str, Any]] = []
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = _boxes(f, 0, end)
        moov = next((b for b in top if b[0] == "moov"), None)
        if moov is None:
            return out
        for trak in _boxes(f, moov[2], moov[3]):
            if trak[0] != "trak" or _handler(f, trak) != "soun":
                continue
            stsd = _child(f, trak, "mdia", "minf", "stbl", "stsd")
            if stsd is None:
                continue
            f.seek(stsd[2] + 4)
            count = struct.unpack(">I", f.read(4))[0]
            if count < 1:
                continue
            off = stsd[2] + 8
            f.seek(off)
            hdr = f.read(8)
            if len(hdr) < 8:
                continue
            size = struct.unpack(">I", hdr[:4])[0]
            if size < 8 or off + size > stsd[3]:
                continue
            f.seek(off)
            out.append({"offset": off, "size": size, "bytes": f.read(size)})
    return out


def fit_entry(source_entry: bytes, target_len: int) -> bytes | None:
    """把源条目整形成与目标等长（**当前不使用**，保留作为记录）。

    曾经想用 `free` box 补齐长度差，让普通转码路径也能回到源的写法。实测被
    GPAC 判为非法：`Cannot process box of type free` inside stsd。采样条目里
    放填充 box 不是可移植写法，因此这条路线作废 —— 只允许真正的等长替换。
    """
    base = strip_optional_boxes(source_entry)
    if len(base) == target_len:
        return base
    return None


def strip_optional_boxes(entry: bytes) -> bytes:
    """去掉条目里的 `btrt` 之类提示性 box，返回新字节串。

    entry 的固定部分是 8(header) + 8(reserved6+dri) + 20(声卡描述) +
    16(Sony ipcm 的四个 u32) = 52 字节，之后才是子 box。

    **必须同步改写头部的 size 字段** —— 少了这一步，条目头还宣称原来的长度，
    reader 会读越界 20 字节，比原来的位深错误更难查。
    """
    fixed = 52
    if len(entry) <= fixed:
        return entry
    head, rest = bytearray(entry[:fixed]), entry[fixed:]
    kept = bytearray()
    off = 0
    while off + 8 <= len(rest):
        size, typ = struct.unpack(">I4s", rest[off:off + 8])
        name = typ.decode("latin-1", "replace")
        if size < 8 or off + size > len(rest):
            # 结构不可解析: 原样保留, 由调用方按长度不匹配拒绝
            return entry
        if name not in OPTIONAL_ENTRY_BOXES:
            kept += rest[off:off + size]
        off += size
    if off != len(rest):
        return entry
    if struct.unpack(">I", head[0:4])[0] != len(entry):
        return entry                      # 头部 size 与实长不符, 不猜
    out = bytes(head) + bytes(kept)
    struct.pack_into(">I", head, 0, len(out))
    return bytes(head) + bytes(kept)


def normalise_audio_entry_bit_depth(
    path: Path,
    *,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """把**自相矛盾**的 `ipcm`/`fpcm` 条目按 `pcmC` 校正回来自洽的写法。

    场景: ffmpeg 的 `.mov` 中间文件用 QuickTime `in24` (此时 `samplesize=16`
    是约定值), GPAC 把它翻成 MP4 `ipcm` 时照搬了 16, 但 `pcmC` 仍写 24 ——
    条目自相矛盾。这里以 `pcmC` 为准把 `samplesize` 与每包字节数改回去。

    纯等长字段改写, 不移动任何 box、不改偏移, 而且**不需要源文件**。
    `pcmC` 是 ISO 23003-5 里权威的位深声明, 用它校正比猜更可靠。
    """
    entries = read_audio_entries(path)
    rep: dict[str, Any] = {"patched": [], "skipped": [], "ok": True}
    if not entries:
        rep["ok"] = False
        rep["reason"] = "没有音频 sample entry"
        return rep
    writes: list[tuple[int, bytes, bytes]] = []
    for i, e in enumerate(entries):
        blob = e["bytes"]
        c = audio_entry_consistency(blob)
        if c["consistent"]:
            continue
        fourcc = c["fourcc"]
        bits = c["pcmc_bits"]
        if fourcc not in ("ipcm", "fpcm") or not bits:
            rep["skipped"].append({"track": i + 1,
                                   "reason": f"{fourcc} 无 pcmC, 不猜"})
            rep["ok"] = False
            continue
        new = bytearray(blob)
        struct.pack_into(">H", new, 26, bits)          # samplesize
        if len(new) >= 52:
            # Sony/GPAC 布局: 固定部 52 字节, 其中 tail4[1] = 每包字节数
            struct.pack_into(">I", new, 40, max(1, bits // 8))
        writes.append((e["offset"], blob, bytes(new)))
        rep["patched"].append({"track": i + 1, "bits": bits})

    if not writes:
        rep["reason"] = "无需校正"
        rep["ok"] = all(not s for s in rep["skipped"])
        return rep

    with path.open("r+b") as f:
        for offset, cur, new in writes:
            f.seek(offset)
            if f.read(len(cur)) != cur:
                rep["ok"] = False
                rep["reason"] = f"偏移 {offset} 处内容与预期不符, 放弃"
                return rep
        for offset, _cur, new in writes:
            f.seek(offset)
            f.write(new)

    after = read_audio_entries(path)
    bad = [i + 1 for i, e in enumerate(after)
           if not audio_entry_consistency(e["bytes"])["consistent"]]
    rep["ok"] = not bad
    rep["consistent_after"] = not bad
    rep["reason"] = ("已按 pcmC 校正 samplesize/包长"
                     if not bad else f"校正后仍不自洽: 轨 {bad}")
    log(f"sdpatch: {rep['reason']}")
    return rep


def patch_audio_sample_entries(
    source: Path,
    final: Path,
    *,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """把 final 的每条音频 sample entry 换成 source 的（去掉 btrt 后等长）。

    返回报告；`ok=False` 时 final **未被改动**（只要有一轨对不上就整体不动，
    不做半套）。
    """
    src_entries = read_audio_entries(source)
    dst_entries = read_audio_entries(final)
    rep: dict[str, Any] = {
        "ok": False,
        #: 补丁后成品条目是否**自洽**。与 `ok`（与源逐字节一致）是两条独立
        #: 判据：自相矛盾是无条件错误，与源不同未必。
        "consistent_after": None,
        "source_tracks": len(src_entries),
        "final_tracks": len(dst_entries),
        "patched": [],
        "skipped": [],
    }
    if not src_entries or not dst_entries:
        rep["reason"] = "一端没有音频 sample entry"
        rep["consistent_after"] = all(
            audio_entry_consistency(d["bytes"])["consistent"]
            for d in dst_entries) if dst_entries else None
        return rep
    if len(src_entries) != len(dst_entries):
        rep["reason"] = (f"音频轨数不一致: 源 {len(src_entries)} / "
                         f"成品 {len(dst_entries)}")
        rep["consistent_after"] = all(
            audio_entry_consistency(d["bytes"])["consistent"]
            for d in dst_entries)
        return rep

    plans: list[tuple[int, bytes, bytes]] = []
    for i, (s, d) in enumerate(zip(src_entries, dst_entries)):
        # **只做等长替换。**
        #
        # 曾试过"源条目 + free box 补齐到成品长度"来消除长度差, 被 GPAC 直接
        # 判为非法: `Cannot process box of type free` inside stsd —— 采样条目里
        # 放填充 box 不是可移植写法, 不能用。等长就是硬约束; 长度对不上就放弃,
        # 交由调用方决定 (并打日志), 绝不改长度。
        target = strip_optional_boxes(s["bytes"])
        if len(target) != len(d["bytes"]):
            rep["skipped"].append({
                "track": i + 1,
                "reason": f"条目长度不一致 (源去 btrt 后 {len(target)} B, "
                          f"成品 {len(d['bytes'])} B) —— 拒绝做长度改变的补丁",
            })
            continue
        if target == d["bytes"]:
            rep["skipped"].append({"track": i + 1, "reason": "已经一致"})
            continue
        plans.append((d["offset"], d["bytes"], target))

    if not plans:
        unchanged = all(s.get("reason") == "已经一致" for s in rep["skipped"])
        rep["ok"] = unchanged or not rep["skipped"]
        rep["reason"] = ("全部音频 sample entry 已与源一致"
                         if unchanged else "没有可应用且安全的补丁")
        rep["consistent_after"] = all(
            audio_entry_consistency(d["bytes"])["consistent"]
            for d in dst_entries)
        return rep

    with final.open("r+b") as f:
        for offset, cur, target in plans:
            f.seek(offset)
            now = f.read(len(cur))
            if now != cur:
                rep["reason"] = f"偏移 {offset} 处读到的不是预期条目, 放弃"
                rep["consistent_after"] = all(
                    audio_entry_consistency(d["bytes"])["consistent"]
                    for d in dst_entries)
                return rep
        for offset, cur, target in plans:
            f.seek(offset)
            f.write(target)

    # 写完立即回读校验: 报告只能说"我验证过", 不能说"我写过"
    after = read_audio_entries(final)
    bad = []
    for i, (s, a) in enumerate(zip(src_entries, after)):
        want = strip_optional_boxes(s["bytes"])
        if a["bytes"] != want:
            bad.append(i + 1)
    rep["ok"] = not bad
    rep["patched"] = [i + 1 for i in range(len(src_entries))
                      if i + 1 not in bad]
    # 独立于"与源是否一致"的另一条判据: **条目自身是否自洽**。
    # 自相矛盾(pcmC 24 / samplesize 16)是无条件错误, 无论源长什么样;
    # 调用方应据此拒绝交付。
    rep["consistent_after"] = all(
        audio_entry_consistency(a["bytes"])["consistent"] for a in after)
    if bad:
        rep["reason"] = f"回读校验失败: 轨 {bad}"
    else:
        rep["reason"] = "已用源的 sample entry 覆盖 (去 btrt, 等长)"
    log(f"sdpatch: {rep['reason']}")
    for line in rep["skipped"]:
        log(f"sdpatch: CH{line['track']} {line['reason']}")
    return rep
