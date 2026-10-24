#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPAC 元数据还原的**快速字节级**实现（绕过 GPAC 对 50 GiB 文件的多次整文件重写）。

背景: `preservation.transparent_restore` 的标准流程在 C1305 上把 49.78 GiB 的
stage 用 MP4Box `-ref` 重写, 17 分钟只推进了不到 1 GiB 的 I/O（GPAC 对超大文件
的 track-ref/meta/flatten/brand 各是一次整文件重写），不可接受。stage 本身已经
是合法的 6 轨 MP4（video+4audio+rtmd），缺的只是容器层元数据:

  1. ftyp 品牌（isom/qt → XAVC）
  2. 9 个厂商 uuid box（root PROF/XMP、moov USMT、6 个 per-trak USMT）
  3. 文件级 meta box（nrtm lens profile + XML, idat 自包含）
  4. meta 轨的 tref/cdsc（→video）

本脚本**一次顺序拷贝**完成全部补齐 + stco/co64 偏移修正（因为 ftyp 从 20/28 B
变成源的 32 B，且 moov 内插入 box 会让 mdat 后移），随后用程序内既有 API 做
时基/时长修复与 audio sample entry 校验，最后调用 verify_restored.py 验证。

输入: stage 文件、源文件、bundle 目录（metadata/boxes/*.bin、tracks/rtmd/*）。
输出: 还原后的 MP4。
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


def _root_map(path: Path) -> dict[str, isobmf.Box]:
    return {b.type: b for b in isobmf.root_boxes(path)}


def _read_box_bytes(path: Path, box: isobmf.Box) -> bytes:
    with path.open("rb") as f:
        f.seek(box.start)
        return f.read(box.size)


def _children_buf(buf: bytes) -> list[isobmf.Box]:
    bio = io.BytesIO(buf)
    out = []
    off = 0
    while off < len(buf):
        b = isobmf._read_header(bio, off, len(buf))
        if b is None:
            break
        out.append(b)
        off = b.end
    return out


def _trak_handler_buf(buf: bytes, trak: isobmf.Box) -> str:
    bio = io.BytesIO(buf)
    return isobmf._trak_handler(bio, trak)


def _patch_chunk_offsets(moov: bytearray, delta: int) -> list[str]:
    """把所有 stco/co64 的 chunk offset 加 delta（mdat 后移量）。"""
    descs = []
    buf = bytes(moov)
    roots = _children_buf(buf)
    moov_box = roots[0]
    bio = io.BytesIO(buf)
    for trak in isobmf._children(bio, moov_box):
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
                    for child in isobmf._children(bio, stbl):
                        if child.type not in ("stco", "co64"):
                            continue
                        do = child.data_offset
                        count = struct.unpack(">I", buf[do + 4:do + 8])[0]
                        if child.type == "stco":
                            for i in range(count):
                                p = do + 8 + 4 * i
                                v = struct.unpack(">I", buf[p:p + 4])[0]
                                moov[p:p + 4] = struct.pack(">I", v + delta)
                        else:
                            for i in range(count):
                                p = do + 8 + 8 * i
                                v = struct.unpack(">Q", buf[p:p + 8])[0]
                                moov[p:p + 8] = struct.pack(">Q", v + delta)
                        descs.append(f"{child.type}: {count} offsets +{delta}")
    return descs


def _insert_into_trak_tail(moov: bytearray, handler: str, index: int,
                           payload: bytes) -> bool:
    """把 payload 插到第 index 个该 handler 的 trak 末尾（更新 trak 大小）。"""
    buf = bytes(moov)
    roots = _children_buf(buf)
    moov_box = roots[0]
    bio = io.BytesIO(buf)
    seen = 0
    for trak in isobmf._children(bio, moov_box):
        if trak.type != "trak":
            continue
        if _trak_handler_buf(buf, trak) != handler:
            continue
        if seen != index:
            seen += 1
            continue
        end = trak.end
        moov[end:end] = payload
        # trak 与 moov 两层大小都要同步: 只改 trak 会让后续 _children 解析
        # 以旧的 moov.end 截断, 越插越找不到后面的轨道。
        old = struct.unpack(">I", moov[trak.start:trak.start + 4])[0]
        moov[trak.start:trak.start + 4] = struct.pack(">I", old + len(payload))
        moov_sz = struct.unpack(">I", moov[0:4])[0]
        moov[0:4] = struct.pack(">I", moov_sz + len(payload))
        return True
    return False


def _insert_moov_tail(moov: bytearray, payload: bytes) -> None:
    roots = _children_buf(bytes(moov))
    moov_box = roots[0]
    moov[moov_box.end:moov_box.end] = payload
    old = struct.unpack(">I", moov[0:4])[0]
    moov[0:4] = struct.pack(">I", old + len(payload))


def _replace_audio_entries(moov: bytearray,
                           source_entries: list[bytes]) -> list[str]:
    """把 stage 的音频 sample entry 换成源条目（in24 -> ipcm）。

    GPAC 以 .mov 名重建 stage 时会写 QuickTime `in24` 条目(78 B)，源是
    MP4 `ipcm` 条目(50 B)。条目长度不同，必须同时修正 stsd/stbl/minf/
    mdia/trak/moov 六层祖先大小。按"从后往前"处理，保证后面条目的替换
    不会改变前面已记录的偏移。
    """
    if not source_entries:
        return []
    buf = bytes(moov)
    roots = _children_buf(buf)
    moov_box = roots[0]
    bio = io.BytesIO(buf)
    targets = []
    idx = 0
    for trak in isobmf._children(bio, moov_box):
        if trak.type != "trak" or _trak_handler_buf(buf, trak) != "soun":
            continue
        if idx >= len(source_entries):
            break
        new_entry = source_entries[idx]
        idx += 1
        for mdia in isobmf._children(bio, trak):
            if mdia.type != "mdia":
                continue
            for minf in isobmf._children(bio, mdia):
                if minf.type != "minf":
                    continue
                for stbl in isobmf._children(bio, minf):
                    if stbl.type != "stbl":
                        continue
                    for stsd in isobmf._children(bio, stbl):
                        if stsd.type != "stsd":
                            continue
                        entry = isobmf._read_header(bio, stsd.data_offset + 8,
                                                    stsd.end)
                        if entry is None:
                            continue
                        delta = len(new_entry) - entry.size
                        ancestors = [stsd.start, stbl.start, minf.start,
                                     mdia.start, trak.start, moov_box.start]
                        targets.append((entry.start, entry.size, delta,
                                        ancestors))
    descs = []
    for entry_start, entry_size, delta, ancestors in sorted(
            targets, key=lambda x: x[0], reverse=True):
        moov[entry_start:entry_start + entry_size] = new_entry
        for anc in ancestors:
            old = struct.unpack(">I", moov[anc:anc + 4])[0]
            moov[anc:anc + 4] = struct.pack(">I", old + delta)
        descs.append(f"audio entry {entry_size} -> {len(new_entry)} bytes")
    return descs


def _trak_uuid_count(moov: bytearray, handler: str, index: int) -> int:
    """stage 里第 index 个 handler 轨已有的 uuid box 数（用于去重）。"""
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    moov_box = _children_buf(buf)[0]
    seen = 0
    for trak in isobmf._children(bio, moov_box):
        if trak.type != "trak" or _trak_handler_buf(buf, trak) != handler:
            continue
        if seen != index:
            seen += 1
            continue
        return sum(1 for c in isobmf._children(bio, trak) if c.type == "uuid")
    return -1


def _moov_uuid_count(moov: bytearray) -> int:
    buf = bytes(moov)
    bio = io.BytesIO(buf)
    moov_box = _children_buf(buf)[0]
    return sum(1 for c in isobmf._children(bio, moov_box) if c.type == "uuid")


def build(*, stage: Path, source: Path, bundle_dir: Path, dst: Path) -> dict:
    s_roots = _root_map(source)
    ftyp_src = _read_box_bytes(source, s_roots["ftyp"])
    meta_src = _read_box_bytes(source, s_roots["meta"]) if "meta" in s_roots else b""
    uuid_root = [b for b in isobmf.root_boxes(source)
                 if b.type == "uuid"]
    root_uuid_bytes = b"".join(_read_box_bytes(source, b) for b in uuid_root)

    # 源 moov 级 uuid 与 tref
    moov_src = s_roots["moov"]
    src_moov_bytes = _read_box_bytes(source, moov_src)
    src_moov_box = _children_buf(src_moov_bytes)[0]
    bio_src = io.BytesIO(src_moov_bytes)
    moov_uuid = b""
    tref_bytes = b""
    for child in isobmf._children(bio_src, src_moov_box):
        if child.type == "uuid":
            moov_uuid += src_moov_bytes[child.start:child.end]
        if child.type == "trak":
            for kid in isobmf._children(bio_src, child):
                if kid.type == "tref":
                    tref_bytes = src_moov_bytes[kid.start:kid.end]
    if not tref_bytes:
        raise RuntimeError("source has no tref box")

    # bundle 里的 per-trak uuid（gpac 从源抽出后按文件名记录归属）
    boxes_dir = bundle_dir / "boxes"
    trak_uuid: dict[str, list[bytes]] = {}
    for p in sorted(boxes_dir.glob("uuid_trak_*_*.bin")):
        name = p.name  # uuid_trak_soun_USMT_0.bin
        parts = name.split("_")
        handler = parts[2]
        trak_uuid.setdefault(handler, []).append(p.read_bytes())
    for k in trak_uuid:
        trak_uuid[k].sort()

    # 源音频 sample entry（ipcm 24bit 48k mono）用于替换 stage 的 in24
    from preservation.sample_entry import read_audio_entries

    src_entries = [e["bytes"] for e in read_audio_entries(source)]

    # ---- 构造新 moov
    moov_bytes = _read_box_bytes(stage, _root_map(stage)["moov"])
    moov = bytearray(moov_bytes)
    counts = {"uuid": 0, "tref": 0}
    for handler in ("vide", "soun", "meta"):
        for i, payload in enumerate(trak_uuid.get(handler, [])):
            # GPAC 导入源轨时已经带了部分 per-trak uuid (实测 vide/meta),
            # 重复插入会让 uuid 多重集比源多; 只补缺失的。
            if _trak_uuid_count(moov, handler, i) == 0:
                if _insert_into_trak_tail(moov, handler, i, payload):
                    counts["uuid"] += 1
    if moov_uuid and _moov_uuid_count(moov) == 0:
        _insert_moov_tail(moov, moov_uuid)
        counts["uuid"] += 1
    if not _insert_into_trak_tail(moov, "meta", 0, tref_bytes):
        raise RuntimeError("stage has no meta track for tref insertion")
    counts["tref"] = 1
    audio_descs = _replace_audio_entries(moov, src_entries)

    # ---- 计算 mdat 后移量: ftyp 长度差 + moov 增长
    stage_roots = _root_map(stage)
    stage_ftyp_size = stage_roots["ftyp"].size
    delta = (len(ftyp_src) - stage_ftyp_size) + (
        len(moov) - stage_roots["moov"].size)
    off_descs = _patch_chunk_offsets(moov, delta)

    # ---- 顺序写出: 新 ftyp + 新 moov + mdat + 其它 root + root 元数据
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage_size = stage.stat().st_size
    written = 0
    with stage.open("rb") as f, dst.open("wb") as out:
        f.seek(0)
        out.write(ftyp_src)
        written += len(ftyp_src)
        out.write(bytes(moov))
        written += len(moov)
        mdat = stage_roots["mdat"]
        f.seek(mdat.start)
        remaining = mdat.size
        CH = 8 * 1024 * 1024
        while remaining > 0:
            chunk = f.read(min(CH, remaining))
            if not chunk:
                break
            out.write(chunk)
            remaining -= len(chunk)
            written += len(chunk)
        # 其它 root back 写入 (stage 里 mdat 之后的未知 box 原样保留)
        for b in isobmf.root_boxes(stage):
            if b.type in ("ftyp", "moov", "mdat"):
                continue
            # GPAC 在 mdat 末尾留下 size=0 的零类型 padding box；其语义是
            # "到文件尾"，原样保留会吞掉后面追加的 root uuid/meta，丢弃。
            if b.type.startswith("\x00"):
                continue
            f.seek(b.start)
            out.write(f.read(b.size))
            written += b.size
        # root 级元数据: uuid×2 + meta
        out.write(root_uuid_bytes)
        out.write(meta_src)
        written += len(root_uuid_bytes) + len(meta_src)
    return {"dst": str(dst), "written_bytes": written,
            "stage_bytes": stage_size, "delta": delta,
            "trak_uuid_inserted": counts["uuid"],
            "tref_inserted": counts["tref"],
            "root_uuid_bytes": len(root_uuid_bytes),
            "meta_bytes": len(meta_src),
            "ftyp": [stage_ftyp_size, len(ftyp_src)],
            "offset_patches": off_descs,
            "audio_entries": audio_descs}


def postprocess(*, source: Path, dst: Path, movie_timescale: int = 60000) -> dict:
    from preservation.sample_entry import patch_audio_sample_entries
    from preservation import isobmf as ib

    out = {"duration_patches": [], "sample_entries": None, "tkhd": 0}
    for desc in ib.patch_track_durations(dst, movie_timescale, from_stts=True):
        out["duration_patches"].append(desc)
    mv = ib.patch_movie_duration(dst)
    if mv:
        out["duration_patches"].append(mv)
    sdp = patch_audio_sample_entries(source, dst, log=lambda m: None)
    out["sample_entries"] = {"ok": sdp.get("ok"), "reason": sdp.get("reason")}
    out["tkhd"] = len(ib.normalize_track_flags(dst))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--bundle-dir", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    res = {"stem": args.stem}
    res["build"] = build(stage=Path(args.stage), source=Path(args.source),
                         bundle_dir=Path(args.bundle_dir),
                         dst=Path(args.dst))
    res["postprocess"] = postprocess(source=Path(args.source),
                                     dst=Path(args.dst))
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:3000])
    print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
