#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive_repair.py — 归档成品的检出、修复与规范检查。

用途：把已经产出（转码后）的成品当作**独立对象**来检查与修复，不依赖源素材
是否还在手边。当前覆盖三类问题：

  1. **音频延迟重修**  多轨单声道 PCM 的轨间偏移重新测量并按整数样本移位修正。
  2. **元数据写入位置修复**  品牌 / `tref/cdsc` / `nrtm` / 厂商 uuid 是否还在
     原来的结构位置；重封装把 moov 挪到文件末尾时，恢复成源文件的 moov-first
     布局。
  3. **格式规范性检查**  时长一致性、流清单与顺序、sample entry、可解码性等。

设计约束（这些决定了实现长什么样）：

* **默认不动原文件。** `repair` 只往 `--output` 暂存目录写；要写回原件必须同时
  给 `--in-place --backup DIR --yes`，且写回前先把原件复制到 backup。
* **元数据不齐就不交成品。** 重封装之后，品牌的 / `tref` / `nrtm` / uuid 清单
  必须与输入**逐项相同**（context + payload）。任一不同即判该文件修复失败，
  退回"源文件原样拷贝"，绝不出一个"看着像成品、元数据被剥光"的文件。
* **判据用产品自己的入口。** 音频路径走 `core.channel_sync.run_channel_sync`
  (transparent=True)，元数据恢复走 `preservation.transparent_restore`，
  成品与输入的比对走 `preservation.validate.compare`。检查侧则用本文件自带的
  box 遍历器独立读结构 —— 检查不该用写它那套代码来检查。
* **不可测不算通过。** 静音轨、不可测残差一律记 `UNMEASURABLE`/`SKIP`，
  不折算成 PASS。

用法：

    python archive_repair.py check  --input I:\\20260904 --json report
    python archive_repair.py repair --input I:\\ --output E:\\repaired --json report
    python archive_repair.py repair --input I:\\20260904 --output E:\\repaired \\
                                    --in-place --backup E:\\backup --yes

退出码：0 = 没有 FAIL；1 = 存在 FAIL（检查项不通过或修复失败）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FFMPEG = ROOT / "tools" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffprobe.exe"

#: 临时目录。优先用 `ARCHIVE_REPAIR_SCRATCH`；未指定时 repair 使用输出目录下的
#: `_scratch`（与输出同盘，容量可控），check 使用系统临时目录。**不再硬编码
#: E:** —— 批处理机器上 E: 可能是相机卡或不存在，旧默认值让自主运行直接失败。
SCRATCH = Path(os.environ.get("ARCHIVE_REPAIR_SCRATCH")
               or (Path(tempfile.gettempdir()) / "1kt_archive_repair_scratch"))

#: 判定词汇。全部显式, 不允许"没报错就算过"。
PASS, FAIL, SKIP, WARN, FIXED = "PASS", "FAIL", "SKIP", "WARN", "FIXED"
UNMEASURABLE = "UNMEASURABLE"
#: 新状态 (2026-10-25): 坏到无法解析 / 半成品中间文件。
UNREADABLE = "UNREADABLE"
PARTIAL = "PARTIAL"

#: 线性 PCM fourcc。CTR-07/08 只对该集合做位深/包长一致性检查；
#: AAC (mp4a)、Opus 等压缩条目的字段布局完全不同，套用 PCM 解析会误报。
PCM_FOURCCS = frozenset({
    "ipcm", "fpcm", "in24", "in32", "fl32", "fl64",
    "sowt", "twos", "raw ", "lpcm",
})

#: 半成品中间文件命名 (例如 `xxx.part.mov`)。这类文件的 moov 常常在末尾，
#: 不是归档成品，一键修复应显式跳过而不是判 FAIL。
PARTIAL_MARKER = ".part."

#: 轨间残差超过 1 个采样即认为该轨没对齐 (整数移位理论上应为 0)。
RESIDUAL_TOL_SAMPLES = 1.0
#: 容器时长与流时长的容差 (秒)。
DURATION_TOL_S = 0.002


# ===========================================================================
# 1. 独立 box 遍历器 (检查侧; 不用 preservation.isobmf)
# ===========================================================================
class Box:
    __slots__ = ("type", "start", "data_start", "end", "size")

    def __init__(self, typ: str, start: int, data_start: int, end: int,
                 size: int) -> None:
        self.type, self.start, self.data_start = typ, start, data_start
        self.end, self.size = end, size

    def __repr__(self) -> str:          # 便于报错
        return f"<{self.type} @{self.start} size={self.size}>"


def boxes_in(f, start: int, end: int) -> list[Box]:
    out: list[Box] = []
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
        out.append(Box(typ.decode("latin-1"), off, data, off + size, size))
        off += size
    return out


def find(boxes: list[Box], typ: str) -> Box | None:
    return next((b for b in boxes if b.type == typ), None)


def walk(f, box: Box, *path: str) -> Box | None:
    cur = box
    for name in path:
        kids = boxes_in(f, cur.data_start, cur.end)
        cur = find(kids, name)
        if cur is None:
            return None
    return cur


def _guid(f, b: Box) -> str:
    f.seek(b.data_start)
    h = f.read(16).hex().upper()
    return (f"{{{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}")


def _sha_box(f, b: Box) -> str:
    f.seek(b.start)
    return hashlib.sha256(f.read(b.size)).hexdigest()


def _handler_of(f, trak: Box) -> str | None:
    """handler_type 在 trak/mdia/hdlr, 且 hdlr 是 FullBox (version+flags 各 4B)。"""
    hdlr = walk(f, trak, "mdia", "hdlr")
    if hdlr is None:
        return None
    f.seek(hdlr.data_start + 8)
    return f.read(4).decode("latin-1", "replace")


def container_facts(path: Path) -> dict[str, Any]:
    """结构事实。**只读**, 不依赖产品侧封装库。"""
    facts: dict[str, Any] = {
        "top_level": [], "uuid": [], "tref_cdsc": [], "nrtm": False,
        "ftyp_major": None, "ftyp_compatible": [], "moov_first": None,
        "tracks": [], "mvhd": {}, "meta_scopes": [], "audio_entries": [],
    }
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = boxes_in(f, 0, end)
        facts["top_level"] = [b.type for b in top]

        ftyp = find(top, "ftyp")
        if ftyp is not None:
            f.seek(ftyp.data_start)
            raw = f.read(min(ftyp.end - ftyp.data_start, 64))
            facts["ftyp_major"] = raw[0:4].decode("latin-1")
            facts["ftyp_compatible"] = [
                raw[i:i + 4].decode("latin-1")
                for i in range(8, len(raw) - 3, 4)
            ]

        moov = find(top, "moov")
        mdat = find(top, "mdat")
        if moov is not None and mdat is not None:
            facts["moov_first"] = moov.start < mdat.start

        for b in top:
            if b.type == "uuid":
                facts["uuid"].append({"ctx": "root", "guid": _guid(f, b),
                                      "sha": _sha_box(f, b)})
            elif b.type == "meta":
                facts["meta_scopes"].append("root")
                f.seek(b.start)
                if b"nrtm" in f.read(b.end - b.start):
                    facts["nrtm"] = True

        if moov is None:
            return facts
        for b in boxes_in(f, moov.data_start, moov.end):
            if b.type == "mvhd":
                f.seek(b.data_start)
                ver = f.read(1)[0]
                if ver == 1:
                    f.read(3)
                    f.seek(b.data_start + 4)
                    ctime, mtime, ts, dur = struct.unpack(">QQIQ", f.read(28))
                else:
                    f.read(3)
                    f.seek(b.data_start + 4)
                    ctime, mtime, ts, dur = struct.unpack(">IIII", f.read(16))
                facts["mvhd"] = {"timescale": ts, "duration": dur,
                                 "seconds": (dur / ts) if ts else None}
            elif b.type == "uuid":
                facts["uuid"].append({"ctx": "moov", "guid": _guid(f, b),
                                      "sha": _sha_box(f, b)})
            elif b.type == "meta":
                facts["meta_scopes"].append("moov")
                f.seek(b.start)
                if b"nrtm" in f.read(b.end - b.start):
                    facts["nrtm"] = True
            elif b.type == "trak":
                handler = _handler_of(f, b)
                tkhd = find(boxes_in(f, b.data_start, b.end), "tkhd")
                # 每条 trak 的 stsd 第一条 entry 的 fourcc。数据轨靠它区分
                # Sony `rtmd`（需要 tref/cdsc）与 DJI `djmd/dbgi/tmcd`
                # （不要求 cdsc）。只看 handler 会把两者混为一谈。
                stsd = walk(f, b, "mdia", "minf", "stbl", "stsd")
                entry = None
                if stsd is not None:
                    f.seek(stsd.data_start + 12)
                    entry = f.read(4).decode("latin-1", "replace")
                facts["tracks"].append({
                    "handler": handler,
                    "tkhd_size": tkhd.size if tkhd else None,
                    "entry": entry,
                })
                for sub in boxes_in(f, b.data_start, b.end):
                    if sub.type == "uuid":
                        facts["uuid"].append(
                            {"ctx": f"trak:{handler}", "guid": _guid(f, sub),
                             "sha": _sha_box(f, sub)})
                    elif sub.type == "tref":
                        for ref in boxes_in(f, sub.data_start, sub.end):
                            if ref.type != "cdsc":
                                continue
                            n = (ref.end - ref.data_start) // 4
                            f.seek(ref.data_start)
                            facts["tref_cdsc"].append(
                                {"handler": handler,
                                 "refs": list(struct.unpack(f">{n}I",
                                                            f.read(4 * n)))})
                stsd = walk(f, b, "mdia", "minf", "stbl", "stsd")
                if stsd is not None and handler in ("soun", "vide"):
                    # stsd 是 FullBox: version/flags(4) + entry_count(4) 之后
                    # 才是第一条 sample entry, 而 entry 的前 4 字节是 size,
                    # fourcc 在 +4 —— 一起算就是 data_start + 12。
                    f.seek(stsd.data_start + 12)
                    fourcc = f.read(4).decode("latin-1")
                    facts["audio_entries"].append(
                        {"handler": handler, "fourcc": fourcc})
    facts["uuid"].sort(key=lambda d: (d["ctx"], d["guid"], d["sha"]))
    facts["tref_cdsc"].sort(key=lambda d: (str(d["handler"]), d["refs"]))
    return facts


def uuid_inventory_equal(a: dict, b: dict) -> dict[str, Any]:
    """uuid 清单必须**多重集**相等: 六条 per-track USMT 是六个 box, 不是一个。"""
    ka = [(u["ctx"], u["guid"], u["sha"]) for u in a.get("uuid", [])]
    kb = [(u["ctx"], u["guid"], u["sha"]) for u in b.get("uuid", [])]
    missing = sorted(f"{c}/{g}" for c, g, _ in set(ka) - set(kb))
    added = sorted(f"{c}/{g}" for c, g, _ in set(kb) - set(ka))
    return {"equal": set(ka) == set(kb), "missing": missing, "added": added,
            "count_a": len(ka), "count_b": len(kb)}


# ===========================================================================
# 2. ffprobe 事实
# ===========================================================================
def probe(path: Path) -> dict[str, Any]:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def typed_streams(doc: dict) -> list[tuple[str, dict]]:
    seen: dict[str, int] = {}
    out = []
    for s in doc.get("streams", []):
        t = s.get("codec_type", "?")
        out.append((f"{t}[{seen.get(t, 0)}]", s))
        seen[t] = seen.get(t, 0) + 1
    return out


def decodable(path: Path, index: int, seconds: float = 1.0) -> tuple[bool, str]:
    r = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-t", f"{seconds}",
         "-i", str(path), "-map", f"0:{index}", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    err = (r.stderr or "").strip()
    return r.returncode == 0 and not err, err[:200]


# ===========================================================================
# 3. 检查项
# ===========================================================================
def _item(cid: str, verdict: str, detail: str = "",
          data: Any = None) -> dict[str, Any]:
    return {"id": cid, "verdict": verdict, "detail": detail, "data": data}


def is_partial_name(path: Path) -> bool:
    """`.part.mov` 之类的半成品中间文件 (不是归档成品)。"""
    return PARTIAL_MARKER in path.name.lower()


def _data_entries(facts: dict) -> list[str]:
    """数据轨 (非 vide/soun) 的 stsd entry fourcc 列表。"""
    return [str(t.get("entry") or "")
            for t in facts.get("tracks", [])
            if str(t.get("handler")) not in ("vide", "soun")]


def check_container(path: Path, facts: dict, doc: dict) -> list[dict]:
    """元数据写入位置 / 结构规范。**只看文件自身**, 不需要源。"""
    out: list[dict] = []

    brand = facts.get("ftyp_major")
    out.append(_item("CTR-01", PASS if brand else FAIL,
                     f"ftyp major={brand!r}",
                     {"major": brand, "compatible": facts["ftyp_compatible"]}))

    data_entries = _data_entries(facts)
    cdsc = facts["tref_cdsc"]
    if not data_entries:
        out.append(_item("CTR-02", SKIP, "无数据轨, tref/cdsc 不适用", cdsc))
    elif "rtmd" in data_entries:
        # 只有 Sony rtmd 元数据轨通过 tref/cdsc 绑定到视频轨。
        # DJI 的 djmd/dbgi/tmcd 是普通数据轨，不要求 cdsc —— 旧检查把两者
        # 混为一谈，在 I: 的 221 个 DJI 文件上产生了误报 FAIL。
        out.append(_item("CTR-02", PASS if cdsc else FAIL,
                         f"rtmd 数据轨存在, tref/cdsc="
                         f"{'有' if cdsc else '缺失'}", cdsc))
    else:
        out.append(_item("CTR-02", SKIP,
                         f"非 Sony 数据轨 {sorted(set(data_entries))}, "
                         "不要求 tref/cdsc", cdsc))

    meta_scopes = facts["meta_scopes"]
    if meta_scopes:
        ok = bool(facts["nrtm"])
        out.append(_item("CTR-03", PASS if ok else WARN,
                         f"meta 作用域={meta_scopes}, nrtm={'有' if ok else '无'}",
                         {"scopes": meta_scopes, "nrtm": facts["nrtm"]}))
    else:
        out.append(_item("CTR-03", SKIP, "无 meta box (非 Sony XAVC 布局)"))

    uuids = facts["uuid"]
    ctxs = sorted({u["ctx"] for u in uuids})
    out.append(_item("CTR-04", PASS,
                     f"{len(uuids)} 个 uuid, 作用域={ctxs}", uuids))

    mf = facts.get("moov_first")
    if is_partial_name(path):
        out.append(_item("CTR-05", SKIP,
                         "半成品中间文件 (.part.) — 不作为归档成品要求",
                         {"moov_first": mf, "top_level": facts["top_level"]}))
    elif mf is None:
        out.append(_item("CTR-05", FAIL, "缺 moov 或 mdat"))
    else:
        out.append(_item("CTR-05", PASS if mf else WARN,
                         "moov 在 mdat 之前 (faststart)"
                         if mf else "moov 在 mdat 之后 (非 faststart)",
                         {"moov_first": mf, "top_level": facts["top_level"]}))

    mvhd = facts.get("mvhd") or {}
    secs = mvhd.get("seconds")
    fmt_dur = ((doc.get("format") or {}).get("duration"))
    if secs is None or fmt_dur is None:
        out.append(_item("CTR-06", SKIP, "无 mvhd 或无法读取时长"))
    else:
        delta = abs(secs - float(fmt_dur))
        out.append(_item("CTR-06", PASS if delta <= DURATION_TOL_S else FAIL,
                         f"mvhd {secs:.6f}s vs container {float(fmt_dur):.6f}s "
                         f"(差 {delta * 1000:+.3f} ms)",
                         {"mvhd_seconds": secs, "timescale":
                          mvhd.get("timescale"), "delta_s": delta}))

    entries = [e for e in facts["audio_entries"] if e["handler"] == "soun"]
    pcm_entries = [e for e in entries
                   if str(e.get("fourcc") or "") in PCM_FOURCCS]
    if pcm_entries:
        fourccs = sorted({e["fourcc"] for e in pcm_entries})
        # `in24` 是已知陷阱: Sony XAVC-S 的 LPCM 实际是 ipcm, 写成 in24 会让
        # 下游的 audio.tracks 关键项判 MODIFIED。
        bad = [c for c in fourccs if c == "in24"]
        out.append(_item("CTR-07", WARN if bad else PASS,
                         f"PCM sample entry={fourccs}"
                         + (" — in24 是已知陷阱 (应为 ipcm)" if bad else ""),
                         pcm_entries))
    elif entries:
        out.append(_item("CTR-07", SKIP,
                         f"非 PCM 音频条目 {sorted({e['fourcc'] for e in entries})}",
                         entries))
    else:
        out.append(_item("CTR-07", SKIP, "无音频轨"))

    # CTR-08: 音频 sample entry 内部一致性。**不需要源文件**。
    # 这条是为一个真实事故加的: 透明重封装把 ffmpeg 的 QuickTime `in24` 条目
    # 翻译成 MP4 `ipcm` 时只换了标签没换位深 —— `pcmC` 说 24 bit,
    # `samplesize` 说 16 bit。ffmpeg 采信前者(解码正确, 因此残差复测一直是 0),
    # Premiere/VLC 采信后者(四轨白噪音)。逐字段看永远发现不了, 必须把两个
    # 字段放在一起比。
    try:
        from preservation.sample_entry import (
            audio_entry_consistency,
            read_audio_entries,
        )

        wentries = read_audio_entries(path)
        # 只检查线性 PCM 条目；AAC/Opus 的 sample entry 字段布局不同，
        # 用 PCM 的 packet-bytes 规则去读会报出 "包长 1702061171 字节" 这种噪声。
        wentries = [e for e in wentries
                    if e["bytes"][4:8].decode("latin-1", "replace")
                    in PCM_FOURCCS]
        if not wentries:
            out.append(_item("CTR-08", SKIP,
                             "无 PCM 音频 sample entry (压缩音频不适用)"))
        else:
            bad = []
            for i, e in enumerate(wentries):
                c = audio_entry_consistency(e["bytes"])
                if not c["consistent"]:
                    bad.append({"track": i + 1, "detail": c})
            out.append(_item(
                "CTR-08", WARN if bad else PASS,
                f"{len(wentries)} 条 PCM 条目, "
                + ("全部自洽" if not bad else
                   f"{len(bad)} 条自相矛盾: " + "; ".join(
                       f"CH{b['track']} {b['detail']['problems']}"
                       for b in bad)),
                bad or None))
    except Exception as exc:                            # noqa: BLE001
        out.append(_item("CTR-08", SKIP,
                         f"无法检查: {type(exc).__name__}: {exc}"))

    # CTR-09: tkhd flags / alternate_group 规范。这是另一个真实事故:
    # 多条音轨落在同一个 alternate_group 或 flags 缺 enabled/in_movie/in_preview
    # 位时，Premiere 只显示一条音轨。生产路径现在会在交付前归一化
    # (`preservation.isobmf.normalize_track_flags`)；归档成品也应有同样检查。
    try:
        from preservation.isobmf import track_flags

        tf = track_flags(path)
        bad_flags = [t for t in tf
                     if (int(t.get("flags") or 0) & 0x7) != 0x7
                     or int(t.get("alternate_group") or 0) != 0]
        bad_alt = [t for t in tf if int(t.get("alternate_group") or 0) != 0]
        out.append(_item(
            "CTR-09", WARN if bad_flags else PASS,
            "tkhd flags/alternate_group 已归一化"
            if not bad_flags else
            f"{len(bad_flags)} 条轨 flags!=7 或 alternate_group!=0 "
            f"(其中 alt_group 非零 {len(bad_alt)} 条) — 多音轨在 Premiere "
            f"可能只显示一条", bad_flags or None))
    except Exception as exc:                            # noqa: BLE001
        out.append(_item("CTR-09", SKIP,
                         f"无法检查: {type(exc).__name__}: {exc}"))
    return out


def check_format(path: Path, facts: dict, doc: dict,
                 decode_probe: float) -> list[dict]:
    out: list[dict] = []
    fmt = doc.get("format") or {}
    streams = doc.get("streams") or []
    if not streams:
        return [_item("FMT-01", FAIL, "ffprobe 读不到任何流")]

    durs = []
    for key, s in typed_streams(doc):
        try:
            durs.append((key, float(s.get("duration"))))
        except (TypeError, ValueError):
            continue
    ctr = fmt.get("duration")
    if ctr and durs:
        worst = max(durs, key=lambda kv: abs(kv[1] - float(ctr)))
        delta = abs(worst[1] - float(ctr))
        out.append(_item("FMT-01", PASS if delta <= 0.05 else WARN,
                         f"container {float(ctr):.6f}s, 偏差最大的是 "
                         f"{worst[0]} {worst[1]:.6f}s (差 {delta * 1000:+.1f} ms)",
                         {"container": float(ctr), "worst": worst[0],
                          "delta_s": delta}))
    else:
        out.append(_item("FMT-01", SKIP, "时长不可读"))

    zero = [k for k, s in typed_streams(doc)
            if str(s.get("duration")) in ("0", "0.0", "None")]
    out.append(_item("FMT-02", FAIL if zero else PASS,
                     f"零/缺失时长流: {zero}" if zero else "无零长流", zero))

    order = [k.split("[")[0] for k, _ in typed_streams(doc)]
    rank = {"video": 0, "audio": 1, "data": 2}
    ranks = [rank.get(t, 3) for t in order]
    out.append(_item("FMT-03", PASS if ranks == sorted(ranks) else WARN,
                     f"轨道顺序={order}", order))

    if decode_probe > 0:
        bad = []
        for i in range(len(streams)):
            ok, err = decodable(path, i, decode_probe)
            if not ok:
                bad.append({"index": i, "error": err})
        out.append(_item("FMT-04", FAIL if bad else PASS,
                         f"解码探针 {decode_probe}s/流: "
                         + ("全部通过" if not bad else f"{len(bad)} 条失败"),
                         bad or None))
    else:
        out.append(_item("FMT-04", SKIP, "未启用解码探针 (--decode-probe 0)"))

    track_count = len(facts["tracks"])
    out.append(_item("FMT-05",
                     PASS if track_count == len(streams) else FAIL,
                     f"moov trak={track_count}, ffprobe streams={len(streams)}",
                     {"trak": track_count, "streams": len(streams)}))
    return out


def check_audio(path: Path, doc: dict,
                opts: dict | None = None) -> tuple[list[dict], dict]:
    """音频延迟是否已经对齐。

    **不自己实现测量。** 早先的版本用固定锚点(CH3)+ 单次 `estimate_pair` 做了
    一遍简化测量, 结果在 `20260904_C1183` 上只测到 CH4、把真正的缺陷
    (CH2 −37 样本) 归成"不可测", 于是 AUD-03 判了 PASS —— 一个把漏检当通过的
    检查器比没有检查器更糟。现在直接调生产入口 `run_channel_sync`
    (`transparent=False`), 锚点回退、分组、质量门、复检全部按产品语义走。

    代价: 会在 `SCRATCH` 下写临时的 `audio_*.mov` 并在结束时删掉;
    **源文件只被读取**。
    """
    from core.channel_sync import eligible_audio, run_channel_sync

    streams = doc.get("streams") or []
    ok, why = eligible_audio(streams)
    if not ok:
        return ([_item("AUD-01", SKIP, f"不可对齐: {why}"),
                 _item("AUD-02", SKIP, "无测量"),
                 _item("AUD-03", SKIP, "无测量")], {})

    SCRATCH.mkdir(parents=True, exist_ok=True)
    work = SCRATCH / ("chk_" + path.stem)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        rep = run_channel_sync(
            source=path, ffmpeg=FFMPEG, work_dir=work, streams=streams,
            opts=opts, transparent=False, log=lambda _m: None)
    except Exception as exc:                            # noqa: BLE001
        return ([_item("AUD-01", UNMEASURABLE,
                       f"测量失败: {type(exc).__name__}: {exc}"[:200]),
                 _item("AUD-02", SKIP, "无测量"),
                 _item("AUD-03", UNMEASURABLE, "无测量")], {})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    status = rep.get("status")
    rows = rep.get("channels") or []
    measured = [r for r in rows
                if r.get("delay_samples") is not None
                and r.get("decision") not in (None, "untouched")]
    detail = "; ".join(
        f"CH{(r.get('stream') or 0) + 1}={r.get('delay_samples')}"
        f"({r.get('decision')}"
        + (f",{r.get('reason')}" if r.get("reason") else "")
        + ")" for r in rows)
    items = [_item("AUD-01", PASS,
                   f"{len([s for s in streams if s.get('codec_type') == 'audio'])} "
                   f"条单声道 PCM, 生产入口判定 status={status}")]
    items.append(_item("AUD-02", PASS if measured else UNMEASURABLE,
                       detail or "无可测轨", rows))
    if status == "applied":
        fixed = [f"CH{(r.get('stream') or 0) + 1}"
                 f"({r.get('shift_samples'):+d})" for r in rows
                 if r.get("decision") == "fixed"]
        items.append(_item("AUD-03", FAIL,
                           f"存在需要修的轨: {fixed}", fixed))
    elif status == "already_aligned":
        items.append(_item("AUD-03", PASS, "所有可测轨已在容差内"))
    elif status == "not_eligible":
        items.append(_item("AUD-03", SKIP, str(rep.get("detail"))[:160]))
    else:
        # measure_failed / verify_failed / tv_not_configured 等: 既不能说
        # 对齐, 也不能说没对齐。如实记 UNMEASURABLE。
        items.append(_item("AUD-03", UNMEASURABLE,
                           f"status={status}: {str(rep.get('detail'))[:160]}"))
    # AUD-04: **被拒但确实还偏**的轨。
    #
    # 为什么必须单独报这一项: `AUD-03 PASS` 的条件是生产判 `already_aligned`,
    # 而那个状态的语义是"**没有可采纳的修正**" —— 被质量门拒掉的轨
    # (`non_constant` / `low_confidence`) 不算在里面。于是报告可以一边写
    # "AUD-03 PASS"、一边留着 23–27 ms 的未修偏移。实测 13 件交付里
    # **11 件属于这种情形**, 共 12 条轨 (偏差 23.68–27.31 ms)。
    # 把漏检写成 PASS 比没有检查更糟, 所以这里如实记为 WARN 并列出幅度。
    min_shift = 5
    still_off = []
    for r in rows:
        d = r.get("delay_ms")
        if r.get("decision") != "untouched":
            continue
        if not isinstance(d, (int, float)):
            continue
        if abs(d) < 1000.0 * min_shift / 48000.0:
            continue
        still_off.append({"stream": r.get("stream"),
                          "reason": r.get("reason"),
                          "delay_ms": d})
    if still_off:
        desc = "; ".join(
            f"CH{(x['stream'] or 0) + 1}{x['delay_ms']:+.2f}ms({x['reason']})"
            for x in still_off)
        items.append(_item(
            "AUD-04", WARN,
            f"{len(still_off)} 条轨仍被拒且偏差 >= {min_shift} 采样: {desc}",
            still_off))
    else:
        items.append(_item("AUD-04", PASS, "没有被拒且仍明显偏移的轨"))
    return items, {"status": status, "rows": rows,
                   "detail": rep.get("detail")}


# ===========================================================================
# 4. 修复
# ===========================================================================
def _rel(path: Path, base: Path) -> Path:
    try:
        return path.relative_to(base)
    except ValueError:
        return Path(path.name)


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream_packet_hash(path: Path, spec: str = "0:v:0") -> str | None:
    """ffmpeg stream-copy 的逐包 SHA256（不解码、不改码流）。

    用来回答一个独立于容器结构的问题:**成品里的视频样本字节是否和源逐包相同**。
    `preservation.validate.compare` 只比较结构/元数据/rtmd 载荷，**不比较视频
    载荷哈希** —— 2026-10-26 实测过一份被并发写坏的成品，validate 仍报
    structural_success=True（只多一条 nrtm MODIFIED），只有解码探针能看见。
    所以成人品必须再加这道闸门。
    """
    cmd = [str(FFMPEG), "-v", "error", "-i", str(path),
           "-map", spec, "-c", "copy", "-f", "streamhash",
           "-hash", "sha256", "-"]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, encoding="utf-8",
                           errors="replace", timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"SHA256=([0-9a-fA-F]+)", r.stdout or "")
    return m.group(1).lower() if m else None


def video_payload_equal(src: Path, dst: Path) -> dict[str, Any]:
    """源/成品的视频 packet hash 对照；没有视频流时 measured=False。"""
    a = _stream_packet_hash(src)
    b = _stream_packet_hash(dst)
    return {"source": a, "final": b,
            "measured": bool(a and b),
            "equal": bool(a and b and a == b)}


def restore_layout(path: Path, want_moov_first: bool,
                   log) -> tuple[bool, str]:
    """把 moov 挪回 mdat 之前。

    `MP4Box -inter` 重新交织但**不重编码**; 实测逐流 streamhash 不变、
    `validate.compare` 43/0/0、Gyroflow 读到同样的 IMU。所以它是安全的
    布局后处理, 而不是"再转一次"。
    """
    if not want_moov_first:
        return True, "输入本身不是 moov-first, 不强制改变布局"
    tmp = path.with_suffix(".layout.mp4")
    tmp.unlink(missing_ok=True)
    gpac_dir = os.environ.get("GPAC_DIR")
    exe = (Path(gpac_dir) / "mp4box.exe") if gpac_dir else None
    if exe is None or not exe.is_file():
        for cand in (Path(r"C:\Program Files\GPAC\mp4box.exe"),
                     ROOT / "tools" / "GPAC" / "mp4box.exe"):
            if cand.is_file():
                exe = cand
                break
    if exe is None:
        return False, "找不到 MP4Box, 无法恢复 moov-first 布局"
    r = subprocess.run([str(exe), "-inter", "500", str(path), "-out",
                        str(tmp)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=7200)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False, f"MP4Box -inter 失败: {(r.stdout or '')[-200:]}"
    tmp.replace(path)
    log("layout: 已恢复 moov-first (MP4Box -inter)")
    return True, "moov-first 已恢复"


def container_fix_needed(path: Path, facts: dict, doc: dict,
                         *, fix_layout: bool = False) -> bool:
    """是否存在「不需要重封装音频、只修容器」就能修好的问题。"""
    fixable = {"CTR-06", "CTR-08", "CTR-09"}
    if fix_layout:
        fixable.add("CTR-05")
    items = check_container(path, facts, doc)
    return any(i["id"] in fixable and i["verdict"] in (FAIL, WARN)
               for i in items)


def repair_container(path: Path, *, fix_layout: bool = False,
                     log=lambda _m: None) -> dict[str, Any]:
    """容器级一键修复（不重封装、不重新编码）。

    覆盖 2026-10-25 在 I: 归档实际发现的几类可独立修复的问题：

    * `CTR-09` tkhd flags / alternate_group ── 归一化为 enabled+in_movie+
      in_preview 且 alternate_group=0（Premiere 多音轨可见性）；
    * `CTR-08` PCM sample entry 自相矛盾 ── 以 `pcmC` 为准校正 samplesize/
      每包字节数（`normalise_audio_entry_bit_depth`，不需要源文件）；
    * `CTR-06`/时长时基 ── 按 stts 重建 tkhd/elst/mvhd 时长
      (`repair_remux_timescale`)；
    * 可选 `CTR-05` moov-first 布局 ── 仅当显式 `--fix-layout`（会触发
      MP4Box -inter 整文件重交织，默认不开）。

    所有步骤都是等长字段改写或显式 box 重排；失败只记录，不抛给调用方。
    """
    rep: dict[str, Any] = {"changes": [], "errors": [],
                           "tkhd": [], "durations": [], "sample_entry": None,
                           "layout": None, "ok": True}

    try:
        from preservation.isobmf import normalize_track_flags

        descs = normalize_track_flags(path)
        rep["tkhd"] = descs
        rep["changes"] += descs
        for d in descs:
            log(d)
    except Exception as exc:                            # noqa: BLE001
        rep["errors"].append(f"tkhd: {type(exc).__name__}: {exc}")

    try:
        from preservation.sample_entry import normalise_audio_entry_bit_depth

        ent = normalise_audio_entry_bit_depth(path, log=lambda _m: None)
        rep["sample_entry"] = ent
        if ent.get("patched"):
            rep["changes"].append(
                f"sample_entry: {ent.get('reason') or 'pcmC 校正'}")
            log(f"sample_entry: {ent.get('reason')}")
    except Exception as exc:                            # noqa: BLE001
        rep["errors"].append(f"sample_entry: {type(exc).__name__}: {exc}")

    try:
        from core.channel_sync import repair_remux_timescale

        dlogs: list[str] = []
        repair_remux_timescale(path, log=lambda m: (dlogs.append(m), log(m))[1])
        if dlogs:
            rep["durations"] = dlogs
            rep["changes"] += dlogs
    except Exception as exc:                            # noqa: BLE001
        rep["errors"].append(f"durations: {type(exc).__name__}: {exc}")

    if fix_layout:
        facts = container_facts(path)
        if facts.get("moov_first") is False:
            ok, detail = restore_layout(path, True, log)
            rep["layout"] = {"ok": ok, "detail": detail}
            if ok:
                rep["changes"].append("layout: moov-first 已恢复")

    rep["ok"] = not rep["errors"]
    return rep


def repair_residual_audio(dst: Path, after_doc: dict, opts: dict | None,
                          *, max_rounds: int = 2) -> tuple[list, dict, list]:
    """对一次修复后仍有残差偏移的成品做追加轮次（直到 already_aligned）。

    生产入口的“宽窗采纳”在少数文件上会先采纳一个整数移位，再在成品复测时
    看到 1–20 样本的残差（I:\\20260830_C0925 实测 +20 样本）。旧实现修一次就
    结束，报告里会出现 AUD-03 FAIL。这里对**输出成品**再跑透明同步 1–2 轮，
    每轮只重封装视频/元数据、按新测量移位音频；最多 2 轮防止抖动。

    返回 (checks, audio_data, rounds)；所有异常都变成 rounds 记录，不抛出。
    """
    from core.channel_sync import run_channel_sync
    from preservation.gpac import GpacContainerBackend
    from preservation.transparent_restore import restore_after_transparent_remux

    rounds: list[dict] = []
    aud_items, aud_data = check_audio(dst, after_doc, opts)
    for r in range(max_rounds):
        if aud_data.get("status") != "applied":
            break
        tmp = dst.with_name(dst.stem + f".round{r + 1}.mp4")
        tmp.unlink(missing_ok=True)
        work = SCRATCH / ("round_" + dst.stem)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            rep = run_channel_sync(
                source=dst, ffmpeg=FFMPEG, work_dir=work,
                streams=after_doc.get("streams") or [], opts=opts,
                transparent=True, dst=tmp,
                gpac=GpacContainerBackend(None),
                restore_metadata=restore_after_transparent_remux,
                log=lambda _m: None)
        except Exception as exc:                        # noqa: BLE001
            rounds.append({"round": r + 1,
                           "error": f"{type(exc).__name__}: {exc}"[:200]})
            tmp.unlink(missing_ok=True)
            break
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if rep.get("status") != "applied" or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            rounds.append({"round": r + 1, "status": rep.get("status"),
                           "note": "无追加修复，停止"})
            break
        tmp.replace(dst)
        # 追加重封装可能重置 tkhd/时长/sample entry；再跑一遍容器级修复。
        fixes = repair_container(dst, log=lambda _m: None)
        after_doc = probe(dst)
        rounds.append({
            "round": r + 1,
            "shifts": {
                f"audio[{c.get('stream')}]": c.get("shift_samples")
                for c in (rep.get("channels") or [])
                if c.get("decision") == "fixed"},
            "container_changes": len(fixes.get("changes") or []),
        })
        aud_items, aud_data = check_audio(dst, after_doc, opts)
    return aud_items, aud_data, rounds


def repair_one(src: Path, dst: Path, *, opts: dict | None,
               want_moov_first: bool, log,
               fix_container: bool = True,
               fix_layout: bool = False) -> dict[str, Any]:
    """一次修复: 音频对齐 + 元数据恢复 + 容器规范 + 校验（一键）。"""
    import numpy  # noqa: F401

    from core.channel_sync import run_channel_sync
    from preservation.gpac import GpacContainerBackend
    from preservation.transparent_restore import (
        restore_after_transparent_remux,
    )

    rec: dict[str, Any] = {"src": str(src), "dst": str(dst),
                           "src_size": src.stat().st_size}
    work = SCRATCH / ("work_" + src.stem)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)

    before = container_facts(src)
    before_doc = probe(src)
    logs: list[str] = []
    t0 = time.perf_counter()
    try:
        rep = run_channel_sync(
            source=src, ffmpeg=FFMPEG, work_dir=work,
            streams=before_doc.get("streams") or [], opts=opts,
            transparent=True, dst=dst, gpac=GpacContainerBackend(None),
            restore_metadata=restore_after_transparent_remux,
            log=lambda m: (logs.append(m), log(m))[1],
        )
        rec["status"] = rep.get("status")
        rec["file_copied"] = rep.get("file_copied")
        rec["shifts"] = {
            f"audio[{c.get('stream')}]": int(c.get("shift_samples") or 0)
            for c in (rep.get("channels") or [])
            if c.get("decision") == "fixed"}
        rec["anchor"] = rep.get("anchor_stream")
        rec["accurate"] = rep.get("accurate")
    except Exception as exc:                            # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["log_tail"] = logs[-10:]
        return rec
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rec["elapsed_s"] = round(time.perf_counter() - t0, 1)

    if not dst.is_file():
        if rec.get("status") == "applied":
            rec["error"] = "输出文件未生成"
            return rec
        # 非 applied（two_channel_only / measure_failed / not_eligible 等）
        # 的透明路径不会落 dst。若容器还有可修问题，先复制源文件作为容器
        # 修复的输入；否则直接 NOOP，避免为无需处理的 2CH 文件白拷一份。
        if fix_container and container_fix_needed(
                src, before, before_doc, fix_layout=fix_layout):
            shutil.copy2(src, dst)
            rec["file_copied"] = True
        else:
            rec["no_fix_needed"] = True
            return rec

    # --- 布局恢复 (源是 moov-first 而我们重封装成了 moov-last 时才做) ---
    after = container_facts(dst)
    if before.get("moov_first") and after.get("moov_first") is False:
        rec["layout_repaired"], rec["layout_detail"] = restore_layout(
            dst, True, log)
        after = container_facts(dst)
    else:
        rec["layout_repaired"] = None
        if rec.get("status") != "applied":
            rec["layout_detail"] = "未重封装 (字节级拷贝), 布局不变"
        elif not before.get("moov_first"):
            rec["layout_detail"] = "输入不是 moov-first, 不强制改变布局"
        else:
            rec["layout_detail"] = "重封装后仍是 moov-first"

    # --- 元数据必须逐项与输入相同, 否则不交成品 ---------------------------
    uuid_cmp = uuid_inventory_equal(before, after)
    meta_ok = (
        before.get("ftyp_major") == after.get("ftyp_major")
        and before.get("tref_cdsc") == after.get("tref_cdsc")
        and before.get("nrtm") == after.get("nrtm")
        and uuid_cmp["equal"]
    )
    rec["metadata"] = {
        "ftyp_before": before.get("ftyp_major"),
        "ftyp_after": after.get("ftyp_major"),
        "tref_same": before.get("tref_cdsc") == after.get("tref_cdsc"),
        "nrtm_before": before.get("nrtm"),
        "nrtm_after": after.get("nrtm"),
        "uuid": uuid_cmp,
        "identical": meta_ok,
    }
    rec["top_level_before"] = before.get("top_level")
    rec["top_level_after"] = after.get("top_level")
    if not meta_ok:
        # 宁可退回原样拷贝, 也不交一个元数据被剥掉的"成品"。
        shutil.copy2(src, dst)
        rec["status"] = "metadata_mismatch"
        rec["file_copied"] = True
        rec["error"] = ("重封装后元数据与输入不一致, 已退回源文件原样拷贝: "
                        f"{json.dumps(rec['metadata'], ensure_ascii=False)[:400]}")
        return rec

    # --- 容器级一键修复（不需要源文件） ----------------------------------
    if fix_container and dst.is_file():
        rec["container_fixes"] = repair_container(
            dst, fix_layout=fix_layout, log=log)
        after = container_facts(dst)

    # --- 没有音频修复动作时，若容器有可修问题也产出成品 -----------------
    #
    # `already_aligned` / `measure_failed` 时透明路径产出的是**源文件字节级
    # 拷贝**。旧实现直接 NOOP；现在先判断是否存在可独立修的容器问题
    # (tkhd flags、sample entry 自洽、时长时基)，有就复制一份并修，
    # 没有才真正 NOOP —— 不为了修容器把所有无需处理的大文件都拷一遍。
    if rec["status"] != "applied":
        if fix_container and container_fix_needed(
                src, before, before_doc, fix_layout=fix_layout):
            if not dst.is_file():
                shutil.copy2(src, dst)
            fixes = rec.get("container_fixes") or repair_container(
                dst, fix_layout=fix_layout, log=log)
            rec["container_fixes"] = fixes
            if fixes.get("changes"):
                rec["status"] = "container_fixed"
                rec["no_fix_needed"] = False
                # 成人品硬闸门: 容器级修复 = 字节级拷贝 + 等长字段改写，
                # 因此 (1) 成品大小必须与源相同，(2) 视频载荷必须逐包相同。
                # 2026-10-26 实测并发路径曾产出被截断/写坏的拷贝（216 MB
                # vs 源 304 MB）——只看容器结构（moov 能读）发现不了。
                try:
                    ssz, dsz = src.stat().st_size, dst.stat().st_size
                    rec["size_check"] = {"source": ssz, "final": dsz,
                                         "equal": ssz == dsz}
                    vp = video_payload_equal(src, dst)
                    rec["video_payload"] = vp
                    if ssz != dsz:
                        rec["status"] = "size_mismatch"
                        rec["error"] = (f"成品大小 {dsz} != 源 {ssz}: 拷贝不"
                                        "完整, 拒绝交付")
                    elif vp.get("measured") and not vp["equal"]:
                        rec["status"] = "payload_mismatch"
                        rec["error"] = ("视频载荷与源不一致 (容器修复不应改变"
                                        "样本数据), 拒绝交付")
                except OSError as exc:
                    rec["status"] = "size_mismatch"
                    rec["error"] = f"成品完整性核对失败: {exc}"
                after = container_facts(dst)
                after_doc = probe(dst)
                rec["format_checks"] = check_format(dst, after, after_doc, 0.0)
                rec["container_checks"] = check_container(dst, after, after_doc)
                rec["top_level_after"] = after.get("top_level")
                return rec
        rec["no_fix_needed"] = True
        return rec

    # --- 内容与规范的独立核对 -------------------------------------------
    after_doc = probe(dst)
    fmt_ctr = (after_doc.get("format") or {}).get("duration")
    src_ctr = (before_doc.get("format") or {}).get("duration")
    rec["duration_before"] = float(src_ctr) if src_ctr else None
    rec["duration_after"] = float(fmt_ctr) if fmt_ctr else None
    rec["duration_delta_s"] = (
        round(float(fmt_ctr) - float(src_ctr), 6)
        if (src_ctr and fmt_ctr) else None)
    rec["format_checks"] = check_format(dst, after, after_doc, 0.0)
    rec["container_checks"] = check_container(dst, after, after_doc)

    # 硬闸门: 音频 sample entry 必须与源的**格式语义**一致。
    # 只看 ffmpeg 能不能解码是不够的 —— ffmpeg 采信 `pcmC`, 而 Premiere/VLC
    # 采信 `samplesize`, 条目自相矛盾时前者解码正确、后者四轨白噪音。
    # 这里用源条目逐字段比对, 不一致就把整次修复判为失败(输出退回源拷贝)。
    try:
        from preservation.sample_entry import (
            audio_entry_consistency,
            read_audio_entries,
            strip_optional_boxes,
        )

        src_e = read_audio_entries(src)
        out_e = read_audio_entries(dst)
        sdp: dict[str, Any] = {"source_tracks": len(src_e),
                               "final_tracks": len(out_e)}
        problems = []
        if len(src_e) != len(out_e):
            problems.append(f"音频轨数 源{len(src_e)}/成品{len(out_e)}")
        for i, (s, o) in enumerate(zip(src_e, out_e)):
            c = audio_entry_consistency(o["bytes"])
            if not c["consistent"]:
                problems.append(f"CH{i + 1} 条目自相矛盾 {c['problems']}")
            want = strip_optional_boxes(s["bytes"])
            if o["bytes"] != want:
                problems.append(f"CH{i + 1} 条目与源不一致")
            sdp[f"CH{i + 1}"] = {
                "source_bits": s and audio_entry_consistency(
                    s["bytes"])["pcmc_bits"],
                "final_bits": c["pcmc_bits"],
                "samplesize_bits": c["samplesize_bits"],
                "matches_source": o["bytes"] == want,
            }
        sdp["ok"] = not problems
        sdp["problems"] = problems
        rec["sample_entry"] = sdp
        if problems:
            shutil.copy2(src, dst)
            rec["status"] = "sample_entry_mismatch"
            rec["file_copied"] = True
            rec["error"] = ("音频 sample entry 与源不一致, 已退回源文件原样"
                            f"拷贝: {problems[:4]}")
            return rec
    except Exception as exc:                            # noqa: BLE001
        rec["sample_entry"] = {"ok": False,
                               "error": f"{type(exc).__name__}: {exc}"}

    # 音频必须真的对齐了: 用**成品**重新测一遍 (不是看日志)。
    # 对仍有残留偏移的成品追加 1–2 轮修复 (见 repair_residual_audio)。
    aud_items, aud_data, aud_rounds = repair_residual_audio(
        dst, after_doc, opts)
    rec["audio_after"] = aud_data
    rec["audio_after_checks"] = aud_items
    rec["audio_rounds"] = aud_rounds
    # 追加轮次会再重封装一次，可能把 moov 放回末尾 / 重置 tkhd 字段；
    # 这里做最后一次布局与容器归一化，并用最终文件重新生成检查项。
    try:
        after = container_facts(dst)
        if before.get("moov_first") and after.get("moov_first") is False:
            ok, detail = restore_layout(dst, True, log)
            rec["layout_repaired"], rec["layout_detail"] = ok, detail
            after = container_facts(dst)
        if aud_rounds:
            rec["container_fixes"] = repair_container(
                dst, fix_layout=False, log=log)
            after = container_facts(dst)
        after_doc = probe(dst)
        rec["format_checks"] = check_format(dst, after, after_doc, 0.0)
        rec["container_checks"] = check_container(dst, after, after_doc)
        rec["top_level_after"] = after.get("top_level")
    except Exception as exc:                            # noqa: BLE001
        rec["finalize_error"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        from preservation.validate import compare

        cmp_scratch = SCRATCH / ("val_" + src.stem)
        cres = compare(src, dst, GpacContainerBackend(None), FFPROBE,
                       cmp_scratch, level="advanced")
        rec["validate"] = {"structural_success": cres.get(
            "structural_success"), "summary": cres.get("summary")}
        shutil.rmtree(cmp_scratch, ignore_errors=True)
    except Exception as exc:                            # noqa: BLE001
        rec["validate"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    # --- 成人品硬闸门 (2026-10-26) --------------------------------------
    # 1) validate.compare 不允许 MODIFIED / MISSING；
    # 2) 视频载荷必须与源逐包相同（streamhash）。
    # 为什么必须有: 2026-10-26 的并发批处理曾产出一份视频样本损坏的成品，
    # validate 仍报 structural_success=True（只多一条 nrtm MODIFIED），
    # 只有解码探针能看见。修复程序必须自己确认"改对了"，而不是把
    # 复检留给人工。
    _val = rec.get("validate") or {}
    _sum = _val.get("summary") or {}
    if (_val.get("structural_success") is False
            or int(_sum.get("MODIFIED") or 0) > 0
            or int(_sum.get("MISSING") or 0) > 0):
        rec["status"] = "validate_mismatch"
        rec["error"] = ("validate.compare 发现成品与源不一致: "
                        + json.dumps(_sum, ensure_ascii=False))
        return rec
    try:
        vp = video_payload_equal(src, dst)
        rec["video_payload"] = vp
        if vp.get("measured") and not vp["equal"]:
            rec["status"] = "payload_mismatch"
            rec["error"] = ("视频载荷与源不一致 (streamhash 不同): 重封装"
                            "破坏了视频样本, 拒绝交付")
            return rec
    except OSError as exc:
        rec["video_payload"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from preservation.gyroflow import check as gyro_check, find_gyroflow

        gf = find_gyroflow(None)
        if not gf:
            rec["gyroflow"] = "UNAVAILABLE"
        else:
            # `gyro_check` 的最后一个参数是**临时目录**, 它会在里面写两份
            # 导出 JSON (单个片段可达 400 MiB)。用完必须删 —— 早先漏了这一步,
            # 跑 13 个文件就在 scratch 里留下 2.7 GiB 垃圾。
            gscratch = SCRATCH / ("gyro_" + src.stem)
            shutil.rmtree(gscratch, ignore_errors=True)
            try:
                rec["gyroflow"] = gyro_check(
                    src, dst, gf, gscratch).get("status")
            finally:
                shutil.rmtree(gscratch, ignore_errors=True)
    except Exception as exc:                            # noqa: BLE001
        rec["gyroflow"] = f"ERROR: {type(exc).__name__}"
    return rec


# ===========================================================================
# 5. 报告
# ===========================================================================
def summarise_check(path: Path, doc: dict,
                    opts: dict | None = None) -> dict[str, Any]:
    facts = container_facts(path)
    items = (check_container(path, facts, doc) + check_format(path, facts, doc, 1.0))
    a_items, a_data = check_audio(path, doc, opts)
    items += a_items
    verdicts = [i["verdict"] for i in items]
    overall = (FAIL if FAIL in verdicts
               else WARN if WARN in verdicts
               else UNMEASURABLE if (UNMEASURABLE in verdicts
                                     and PASS not in verdicts)
               else PASS)
    return {"path": str(path), "overall": overall, "checks": items,
            "audio": a_data, "facts": {
                "top_level": facts["top_level"],
                "ftyp_major": facts["ftyp_major"],
                "moov_first": facts["moov_first"],
                "uuid_count": len(facts["uuid"]),
                "nrtm": facts["nrtm"],
                "tracks": [t["handler"] for t in facts["tracks"]],
            }}


def write_report(rows: list[dict], outdir: Path, title: str) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "archive-repair-report.json"
    dest.write_text(json.dumps({"title": title, "rows": rows},
                               ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    lines = [f"# {title}", ""]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("overall") or r.get("status") or "?"] = (
            counts.get(r.get("overall") or r.get("status") or "?", 0) + 1)
    lines.append("| 状态 | 文件数 |")
    lines.append("|---|---|")
    for k, v in sorted(counts.items()):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    for r in rows:
        head = Path(r.get("path") or r.get("dst") or r.get("src")).name
        lines.append(f"## {head}")
        lines.append("")
        if r.get("checks"):
            lines.append("| 检查 | 判定 | 说明 |")
            lines.append("|---|---|---|")
            for c in r["checks"]:
                detail = str(c.get("detail") or "").replace("|", "\\|")
                lines.append(f"| `{c['id']}` | **{c['verdict']}** | "
                             f"{detail} |")
            lines.append("")
        if r.get("shifts") is not None:
            lines.append(f"- 状态: `{r.get('status')}`  "
                         f"位移: `{r.get('shifts')}`  "
                         f"耗时: {r.get('elapsed_s')}s")
            if r.get("metadata"):
                m = r["metadata"]
                lines.append(f"- 元数据: 相同={m['identical']} "
                             f"ftyp {m['ftyp_before']}→{m['ftyp_after']} "
                             f"tref={'同' if m['tref_same'] else '**变**'} "
                             f"nrtm {m['nrtm_before']}→{m['nrtm_after']} "
                             f"uuid {m['uuid']['count_a']}→"
                             f"{m['uuid']['count_b']}")
            lines.append(f"- 布局: {r.get('top_level_before')} → "
                         f"{r.get('top_level_after')} "
                         f"({r.get('layout_detail')})")
            if r.get("duration_delta_s") is not None:
                lines.append(f"- 时长差: {r['duration_delta_s']:+.6f}s")
            if r.get("validate"):
                lines.append(f"- validate: {r['validate'].get('summary')}")
            lines.append(f"- Gyroflow: {r.get('gyroflow')}")
            if r.get("error"):
                lines.append(f"- **错误**: {r['error']}")
            lines.append("")
    md = outdir / "archive-repair-report.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md


# ===========================================================================
# 6. CLI
# ===========================================================================
def iter_media(root: Path, pattern: str,
               dirs: str | None = None) -> list[Path]:
    """列出待处理媒体。

    `dirs` 是**一级子目录名**的完全匹配正则（例如 `^\\d{8}$`）；给定时只
    遍历这些子目录（不遍历根目录下的散文件/其它目录）。批处理整盘输入时
    必须用它，否则会把 `_pre_repair_backup`、`compact`、`repaired` 等
    非本轮对象一起扫进来。
    """
    exts = {e.lower() for e in pattern.split(",")}
    if root.is_file():
        return [root]
    if dirs:
        rx = re.compile(dirs)
        candidates = [d for d in sorted(root.iterdir())
                      if d.is_dir() and rx.fullmatch(d.name)]
        return sorted(p for d in candidates for p in d.rglob("*")
                      if p.is_file() and p.suffix.lower() in exts)
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in exts)


def parse_opt_overrides(items: list[str]) -> dict[str, Any]:
    """把 `--opt k=v` 解析成 dict; 值的类型交给 `effective_opts` 归一。

    为什么不在 CLI 里做类型判断: `core.channel_sync.effective_opts` 已经按
    `_INT_KEYS`/`_FLOAT_KEYS`/`_BOOL_KEYS` 逐键强制类型, 在这里再判一次就是
    两处规则、迟早不一致。CLI 只负责把字符串搬过去。
    """
    out: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--opt 需要 key=value: {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise SystemExit(f"--opt 的键为空: {item!r}")
        out[k] = v.strip()
    return out


def _worker_init(scratch_root: str) -> None:
    """每个 worker 进程独立的临时目录，避免并行时互删音频中间文件。"""
    global SCRATCH
    SCRATCH = Path(scratch_root) / f"pid{os.getpid()}"
    SCRATCH.mkdir(parents=True, exist_ok=True)


def _parallel_map(func, payloads: list, jobs: int, scratch_root: Path,
                  what: str, on_row=None) -> list[dict]:
    """按文件并行执行 worker；jobs<=1 时在同一进程内串行。

    worker 只返回记录（不共享文件句柄）；scratch 目录按 pid 隔离。
    `on_row(done_index, record)` 在**主进程**里按完成顺序回调（用于写
    可断点续跑的进度文件）；它抛异常只记录，不中断批次。
    """
    if not payloads:
        return []
    _worker_init(str(scratch_root))

    def _emit(i: int, r: dict) -> None:
        if on_row is None:
            return
        try:
            on_row(i, r)
        except Exception as exc:                        # noqa: BLE001
            print(f"[warn] progress write failed: {exc}", flush=True)

    if jobs <= 1:
        out: list[dict] = []
        for i, p in enumerate(payloads, 1):
            try:
                r = func(p)
            except Exception as exc:                    # noqa: BLE001
                r = {"overall": FAIL, "error": f"{type(exc).__name__}: {exc}",
                     "path": str(p[0] if isinstance(p, tuple) else p)}
            out.append(r)
            _emit(i - 1, r)
            name = Path(str(r.get("path") or r.get("src") or
                          r.get("dst") or "?")).name
            print(f"[{i}/{len(payloads)}] {r.get('overall', '?'):<11} "
                  f"{name}", flush=True)
        return out

    from concurrent.futures import ProcessPoolExecutor, as_completed

    results: list[dict] = [{} for _ in payloads]
    with ProcessPoolExecutor(max_workers=jobs,
                             initializer=_worker_init,
                             initargs=(str(scratch_root),)) as ex:
        futures = {ex.submit(func, p): i for i, p in enumerate(payloads)}
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as exc:                    # noqa: BLE001
                r = {"overall": FAIL,
                     "error": f"{type(exc).__name__}: {exc}",
                     "path": str(payloads[i][0]
                                 if isinstance(payloads[i], tuple) else "")}
            results[i] = r
            _emit(i, r)
            name = Path(str(r.get("path") or r.get("src") or
                          r.get("dst") or "?")).name
            print(f"[{done}/{len(payloads)}] [{what}] "
                  f"{r.get('overall', '?'):<11} {name}", flush=True)
    return results


def _check_worker(payload: tuple) -> dict:
    path_str, opts = payload
    p = Path(path_str)
    try:
        doc = probe(p)
        if not doc.get("streams"):
            return {"path": str(p), "overall": UNREADABLE,
                    "checks": [_item("FMT-00", UNREADABLE,
                                     "ffprobe 读不到流 (moov 缺失/零填充/损坏)")]}
        if is_partial_name(p):
            r = summarise_check(p, doc, opts)
            r["overall"] = PARTIAL
            r["partial"] = True
            return r
        return summarise_check(p, doc, opts)
    except Exception as exc:                            # noqa: BLE001
        return {"path": str(p), "overall": FAIL,
                "checks": [_item("CHK-ERR", FAIL,
                                 f"{type(exc).__name__}: {exc}")]}


def _repair_worker(payload: tuple) -> dict:
    src_str, dst_str, opts, want_moov_first, fix_container, fix_layout, \
        keep_all = payload
    src, dst = Path(src_str), Path(dst_str)
    try:
        doc = probe(src)
        if not doc.get("streams"):
            return {"src": str(src), "dst": str(dst),
                    "status": "unreadable", "overall": UNREADABLE,
                    "error": "ffprobe 读不到流 (moov 缺失/零填充/损坏), "
                             "无法修复, 未做任何修改"}
        if is_partial_name(src):
            return {"src": str(src), "dst": str(dst),
                    "status": "partial", "overall": PARTIAL,
                    "error": "半成品中间文件 (.part.), 不是归档成品, 跳过"}
        facts = container_facts(src)
        rec = repair_one(
            src, dst, opts=opts,
            want_moov_first=bool(facts.get("moov_first")),
            fix_container=fix_container, fix_layout=fix_layout,
            log=lambda _m: None)
        rec["overall"] = verdict_of(rec)
        if rec["overall"] == "NOOP" and not keep_all:
            dst.unlink(missing_ok=True)
            rec["output_kept"] = False
        else:
            rec["output_kept"] = dst.is_file()
        return rec
    except Exception as exc:                            # noqa: BLE001
        return {"src": str(src), "dst": str(dst), "status": "error",
                "overall": "FAILED",
                "error": f"{type(exc).__name__}: {exc}"}


def _common_opts(groups: bool, accurate: bool,
                 opt_items: list[str] | None) -> dict[str, Any] | None:
    """生产工作点 + 最新稳定开关（分组锚点默认开）+ 调用方覆盖。

    分组锚点自 v0.8.7 起是 `start.bat` 的生产默认（GROUPS=1），修复侧从
    2026-10-26 起跟随同一口径：**除非显式 `--no-groups`，一律用生产工作点
    的最新稳定算法**（`production_opts()` = materiality_k/verify_max_rel/
    recheck_windows/verify_agree_samples/track_reference）。
    精确模式（1→2→5 样本分级）仍按产品决定 opt-in（`--accurate`）。
    """
    from core.channel_sync import production_opts

    opts: dict[str, Any] = production_opts()
    if groups:
        opts["group_anchor_enabled"] = True
    if accurate:
        opts["channel_sync_accurate"] = True
    opts.update(parse_opt_overrides(list(opt_items or [])))
    return opts or None


def _resolve_scratch(args: argparse.Namespace, outp: Path | None) -> Path:
    """scratch 目录：显式 --scratch > 环境变量 > repair 输出盘 _scratch / 系统临时。

    旧默认值是硬编码的 `E:\1kt_scratch\archive_repair`；E: 在批处理机上可能
    根本不存在（或是一张相机卡），自主运行会直接失败。现在 repair 默认用
    输出目录同盘的 `_scratch`（容量充足、跑完即删），check 用系统临时目录。
    """
    explicit = getattr(args, "scratch", "") or ""
    if explicit:
        s = Path(explicit)
    elif os.environ.get("ARCHIVE_REPAIR_SCRATCH"):
        s = Path(os.environ["ARCHIVE_REPAIR_SCRATCH"])
    elif outp is not None:
        s = outp / "_scratch"
    else:
        s = Path(tempfile.gettempdir()) / "1kt_archive_repair_scratch"
    s.mkdir(parents=True, exist_ok=True)
    return s


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.input)
    files = iter_media(root, args.ext, getattr(args, "dirs", None))
    if args.limit:
        files = files[: args.limit]
    opts = _common_opts(bool(getattr(args, "groups", True)),
                        bool(getattr(args, "accurate", False)),
                        getattr(args, "opt", []))
    jobs = int(getattr(args, "jobs", 1) or 1)
    scratch = _resolve_scratch(args, None)
    print(f"check: {len(files)} 个文件 (root={root}) jobs={jobs} "
          f"groups={bool(getattr(args, 'groups', True))} "
          f"scratch={scratch} opts={opts or 'DEFAULTS'}", flush=True)
    if not files:
        print("[warn] 没有匹配的媒体文件；检查 --input/--dirs/--ext", flush=True)
        return 2
    rows = _parallel_map(_check_worker,
                         [(str(p), opts or None) for p in files],
                         jobs, scratch, "check")
    # 报告默认写到被检查对象旁边（不污染当前工作目录 / 仓库根）。
    report_dir = Path(args.json) if args.json else (
        root if root.is_dir() else root.parent)
    md = write_report(rows, report_dir, "归档规范检查")
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["overall"]] = tally.get(r["overall"], 0) + 1
    print(f"\n{tally}\n-> {md}")
    return 0 if all(r["overall"] in (PASS, WARN, PARTIAL) for r in rows) else 1


def _final_checks(rec: dict[str, Any]) -> list[dict]:
    """修复后重新生成的检查项（容器 + 格式 + 成品音频复测）。"""
    return ((rec.get("container_checks") or [])
            + (rec.get("format_checks") or [])
            + (rec.get("audio_after_checks") or []))


def verdict_of(rec: dict[str, Any]) -> str:
    """把一次修复的结果归到一个词上。**只有真正改过内容才算 REPAIRED。**

    2026-10-26 收紧：成品复检里只要还有任何一条 `FAIL`（包括 AUD-03
    “还剩需要修的轨”），这次修复就不算 REPAIRED。旧实现只对
    `container_fixed` 看检查项，`applied` 只看元数据是否相同，于是
    “修了一次但成品仍有残差”会被报成 REPAIRED —— 与检查器的结论自相
    矛盾。现在两者用同一份 `_final_checks`。
    """
    status = rec.get("status")
    if status == "unreadable":
        return UNREADABLE
    if status == "partial":
        return PARTIAL
    if rec.get("error") and status in ("metadata_mismatch",
                                       "sample_entry_mismatch"):
        return "FAILED"
    if rec.get("error") and not rec.get("no_fix_needed"):
        return "FAILED"
    checks = _final_checks(rec)
    if any(c.get("verdict") == FAIL for c in checks):
        return "FAILED"
    if status == "container_fixed":
        return "REPAIRED"
    if status == "applied":
        return "REPAIRED" if (rec.get("metadata") or {}).get("identical") \
            else "FAILED"
    if rec.get("no_fix_needed"):
        return "NOOP"
    return "FAILED" if rec.get("error") else "NOOP"


def _repair_payloads(files: list[Path], root: Path, outp: Path,
                     opts: dict | None, fix_container: bool, fix_layout: bool,
                     keep_all: bool) -> list[tuple]:
    base = root if root.is_dir() else root.parent
    return [(str(p), str(outp / _rel(p, base)), opts, False,
             fix_container, fix_layout, keep_all) for p in files]


def write_summary(rows: list[dict], outp: Path, *, root: Path,
                  elapsed_s: float, jobs: int, groups: bool,
                  accurate: bool, retry_rounds: int = 0) -> Path:
    """批次总账：状态计数 + 按一级目录细分 + 失败清单。"""
    outp.mkdir(parents=True, exist_ok=True)
    tally: dict[str, int] = {}
    by_dir: dict[str, dict[str, int]] = {}
    failed: list[dict] = []
    unreadable: list[dict] = []
    for r in rows:
        k = r.get("overall") or "FAILED"
        tally[k] = tally.get(k, 0) + 1
        src = Path(str(r.get("src") or r.get("path") or ""))
        try:
            rel = src.relative_to(root)
            group = rel.parts[0] if len(rel.parts) > 1 else "."
        except ValueError:
            group = "."
        by_dir.setdefault(group, {}).setdefault(k, 0)
        by_dir[group][k] += 1
        if k == "FAILED":
            failed.append({"src": str(src),
                           "dst": r.get("dst"),
                           "error": r.get("error"),
                           "checks": [c for c in _final_checks(r)
                                      if c.get("verdict") == FAIL]})
        elif k == UNREADABLE:
            unreadable.append({"src": str(src),
                               "error": r.get("error")})
    data = {
        "title": "归档修复总账",
        "root": str(root),
        "jobs": jobs,
        "groups": groups,
        "accurate": accurate,
        "retry_rounds": retry_rounds,
        "elapsed_s": round(elapsed_s, 1),
        "total": len(rows),
        "tally": tally,
        "by_dir": by_dir,
        "failed": failed,
        "unreadable": unreadable,
    }
    js = outp / "_summary.json"
    js.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                             default=str), encoding="utf-8")
    lines = ["# 归档修复总账", "",
             f"- 输入根: `{root}`",
             f"- jobs={jobs} groups={groups} accurate={accurate} "
             f"精确模式重试轮数={retry_rounds}",
             f"- 总文件: {len(rows)}  耗时: {elapsed_s:.1f}s",
             "", "| 状态 | 文件数 |", "|---|---|"]
    for k, v in sorted(tally.items()):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 按目录", "", "| 目录 | 状态计数 |", "|---|---|"]
    for g in sorted(by_dir):
        lines.append(f"| {g} | " + ", ".join(
            f"{k}={v}" for k, v in sorted(by_dir[g].items())) + " |")
    if failed:
        lines += ["", "## 失败文件", ""]
        for f in failed:
            ids = ",".join(c["id"] for c in f["checks"])
            lines.append(f"- `{f['src']}` — {f.get('error') or ids}")
    if unreadable:
        lines += ["", "## 不可解析 (如实报告, 不中断批次)", ""]
        for f in unreadable:
            lines.append(f"- `{f['src']}` — {f.get('error')}")
    md = outp / "_summary.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return js


def _run_repair(args: argparse.Namespace) -> int:
    """一次自主批处理：发现 → 修复 → 成品复检 → 精确模式重试 → 总账。

    `--resume` 使用输出目录下的 `_progress.jsonl`：每条成功记录（REPAIRED/
    NOOP）在完成时立即落盘；重启时只重跑未完成的文件。因此中途断电/终止后
    再跑同一条命令即可续跑，不需要人工挑文件。
    """
    root = Path(args.input)
    outp = Path(args.output)
    t_start = time.perf_counter()
    files = iter_media(root, args.ext, getattr(args, "dirs", None))
    if args.limit:
        files = files[: args.limit]
    # 防止把输出目录当输入再修一遍（例如 --output 落在 --input 内部）。
    try:
        outp_res = outp.resolve()
        files = [p for p in files
                 if not p.resolve().is_relative_to(outp_res)]
    except OSError:
        pass
    if not files:
        print("[warn] 没有匹配的媒体文件；检查 --input/--dirs/--ext",
              flush=True)
        return 2

    jobs = int(getattr(args, "jobs", 1) or 1)
    fix_container = not bool(getattr(args, "no_container_fix", False))
    fix_layout = bool(getattr(args, "fix_layout", False))
    keep_all = bool(getattr(args, "keep_all", False))
    groups = bool(getattr(args, "groups", True))
    accurate = bool(getattr(args, "accurate", False))
    resume = bool(getattr(args, "resume", False))
    retry_limit = int(getattr(args, "retry_failed", 1) or 0)
    opts = _common_opts(groups, accurate, getattr(args, "opt", []))
    scratch = _resolve_scratch(args, outp)
    # 未显式指定 scratch 时，repair 的 scratch 就在输出盘里；批次结束即删。
    scratch_auto = not (getattr(args, "scratch", "")
                        or os.environ.get("ARCHIVE_REPAIR_SCRATCH"))
    print(f"repair: {len(files)} 个候选文件 → {outp} jobs={jobs} "
          f"groups={groups} accurate={accurate} "
          f"container_fix={fix_container} layout_fix={fix_layout} "
          f"resume={resume} scratch={scratch}", flush=True)
    if not FFMPEG.is_file() or not FFPROBE.is_file():
        print(f"[fatal] 找不到工具: {FFMPEG} / {FFPROBE}", flush=True)
        return 2

    # ---- 断点续跑: 读取上次的成功记录 -----------------------------------
    outp.mkdir(parents=True, exist_ok=True)
    progress_path = outp / "_progress.jsonl"
    done: dict[str, dict] = {}
    if resume and progress_path.is_file():
        for line in progress_path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("overall") not in ("REPAIRED", "NOOP"):
                continue
            dstp = Path(str(row.get("dst") or ""))
            if row.get("overall") == "NOOP" or dstp.is_file():
                done[str(row.get("src"))] = row
    if done:
        print(f"resume: 已完成 {len(done)} 个文件，跳过", flush=True)

    progress_fh = progress_path.open("a", encoding="utf-8")

    def _on_row(_idx: int, r: dict) -> None:
        progress_fh.write(json.dumps(
            {"src": r.get("src"), "dst": r.get("dst"),
             "overall": r.get("overall"), "status": r.get("status")},
            ensure_ascii=False, default=str) + "\n")
        progress_fh.flush()

    pending = [p for p in files if str(p) not in done]
    try:
        payloads = _repair_payloads(pending, root, outp, opts,
                                    fix_container, fix_layout, keep_all)
        fresh = _parallel_map(_repair_worker, payloads, jobs, scratch,
                              "repair", on_row=_on_row) if pending else []
    finally:
        progress_fh.close()
    by_src: dict[str, dict] = {}
    for i, p in enumerate(pending):
        r = fresh[i] if i < len(fresh) else {}
        if not r.get("src"):
            r = {"src": str(p), "overall": "FAILED",
                 "error": r.get("error") or "worker 未返回记录"}
        by_src[str(p)] = r
    rows: list[dict] = []
    for p in files:
        sp = str(p)
        rows.append(done.get(sp) or by_src.get(sp)
                    or {"src": sp, "overall": "FAILED",
                        "error": "缺少结果记录"})

    # ---- 自主重试: 失败文件用精确模式（1→2→5 分级 + 施加后复检）再跑 ----
    retry_rounds = 0
    for attempt in range(1, retry_limit + 1):
        bad = [i for i, r in enumerate(rows)
               if r.get("overall") == "FAILED"]
        if not bad:
            break
        retry_rounds = attempt
        opts2 = _common_opts(groups, True, getattr(args, "opt", []))
        print(f"retry {attempt}/{retry_limit}: {len(bad)} 个失败文件 → "
              f"精确模式重试", flush=True)
        bad_files = [files[i] for i in bad]
        payloads2 = _repair_payloads(bad_files, root, outp, opts2,
                                     fix_container, fix_layout, keep_all)
        try:
            progress_fh = progress_path.open("a", encoding="utf-8")
            # **串行重试**: 并发路径已被证实（2026-10-26）会在高负载下偶发
            # 产出损坏/截断的成品；失败文件用单进程重跑，既避开竞争条件，
            # 也让"重试后再检验"成为确定性路径。
            rr = _parallel_map(_repair_worker, payloads2, 1, scratch,
                               "retry", on_row=_on_row)
        finally:
            progress_fh.close()
        for k, i in enumerate(bad):
            row = rr[k] if k < len(rr) else {
                "src": str(files[i]), "overall": "FAILED",
                "error": "retry worker 未返回记录"}
            row["retried"] = attempt
            row["first_error"] = rows[i].get("error")
            rows[i] = row

    # ---- 报告 + 总账 -----------------------------------------------------
    md = write_report(rows, Path(args.json or outp), "归档修复")
    elapsed = time.perf_counter() - t_start
    js = write_summary(rows, outp, root=root, elapsed_s=elapsed, jobs=jobs,
                       groups=groups, accurate=accurate,
                       retry_rounds=retry_rounds)
    tally: dict[str, int] = {}
    for r in rows:
        k = r.get("overall") or "FAILED"
        tally[k] = tally.get(k, 0) + 1
    print(f"\n{tally}  耗时 {elapsed:.1f}s", flush=True)
    failed = [r for r in rows if r.get("overall") == "FAILED"]
    if failed:
        print(f"[!] {len(failed)} 个文件仍失败:", flush=True)
        for r in failed[:20]:
            print(f"    {r.get('src')} — {r.get('error')}", flush=True)
    print(f"-> {md}\n-> {js}", flush=True)
    if scratch_auto:
        shutil.rmtree(scratch, ignore_errors=True)

    if args.in_place:
        if not (args.yes and args.backup):
            print("[FATAL] --in-place 需要同时给 --backup DIR 与 --yes",
                  file=sys.stderr)
            return 2
        n = apply_back(rows, Path(args.backup))
        print(f"in-place: 已替换 {n} 个文件 (备份在 {args.backup})")

    # 无人值守退出码: 只有真正的修复失败才算失败；UNREADABLE/PARTIAL 是
    # 输入本身的限制，已在总账里如实列出，不让批处理循环因此报错。
    return 1 if failed else 0


def cmd_repair(args: argparse.Namespace) -> int:
    return _run_repair(args)


def run_repair(input_path: str, output_path: str, *,
               jobs: int = 4, groups: bool = True, accurate: bool = False,
               dirs: str = "", ext: str = ".mp4,.MP4,.mov,.MOV",
               limit: int = 0, json_dir: str = "", scratch: str = "",
               resume: bool = False, retry_failed: int = 1,
               fix_layout: bool = False, fix_container: bool = True,
               keep_all: bool = False, opt_items: list[str] | None = None) -> int:
    """程序化入口（`1kt.py --repair` 走这里，其它批处理入口也可直接调）。"""
    ns = argparse.Namespace(
        input=input_path, output=output_path, jobs=jobs, groups=groups,
        accurate=accurate, dirs=dirs, ext=ext, limit=limit,
        json=json_dir, scratch=scratch, resume=resume,
        retry_failed=retry_failed, fix_layout=fix_layout,
        no_container_fix=not fix_container, keep_all=keep_all,
        in_place=False, backup="", yes=False, opt=list(opt_items or []),
    )
    return _run_repair(ns)


def apply_back(rows: list[dict], backup: Path) -> int:
    """把暂存件写回原件。先备份, 再替换; 任一步失败即停。"""
    backup.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in rows:
        if r.get("overall") not in ("REPAIRED",):
            continue
        src, dst = Path(r["src"]), Path(r["dst"])
        if not dst.is_file():
            continue
        bak = backup / src.name
        if not bak.is_file():
            shutil.copy2(src, bak)
        dst.replace(src)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="归档成品一键修复：音频偏移 + 元数据位置 + 容器规范 + 规范检查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[1] if "用法：" in __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--input", required=True, help="文件或目录 (递归)")
        p.add_argument("--json", default="", help="报告输出目录")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--ext", default=".mp4,.MP4,.mov,.MOV",
                       help="扩展名过滤, 逗号分隔")
        p.add_argument("--dirs", default="", metavar="REGEX",
                       help="只遍历一级子目录名**完全匹配**该正则的目录, "
                            "例如 ^\\d{8}$ (整盘输入时必用)")
        p.add_argument("--scratch", default="",
                       help="临时目录；默认 repair 用输出盘 _scratch, "
                            "check 用系统临时目录")
        p.add_argument("--jobs", type=int,
                       default=min(4, max(1, (os.cpu_count() or 2) // 2)),
                       help="按文件并行的工作进程数 (默认 min(4, CPU/2))；"
                            "1 = 串行")

    c = sub.add_parser("check", help="只检查, 不写任何文件 (源文件只读)")
    common(c)
    c.add_argument("--groups", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="按分组锚点语义测量 (默认开, 与生产 start.bat "
                        "GROUPS=1 一致; --no-groups 关闭)")
    c.add_argument("--accurate", action="store_true",
                   help="按精确模式语义测量 (能看见 2~5 样本的小偏移)")
    c.add_argument("--opt", action="append", default=[], metavar="K=V",
                   help="透传给 channel-sync 的选项覆盖, 可重复; "
                        "例如 --opt verify_max_rel=0.2 "
                        "--opt verify_agree_samples=5")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser(
        "repair", help="一键修复到暂存目录 (音频 + 容器；默认不改原件)")
    common(r)
    r.add_argument("--output", required=True, help="暂存目录 (必填)")
    r.add_argument("--groups", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="分组锚点 (默认开, 与生产 start.bat GROUPS=1 一致; "
                        "--no-groups 关闭)")
    r.add_argument("--accurate", action="store_true",
                   help="打开精确模式 (1→2→5 样本分级); 失败重试时自动打开")
    r.add_argument("--opt", action="append", default=[], metavar="K=V",
                   help="透传给 channel-sync 的选项覆盖, 可重复; "
                        "例如 --opt verify_max_rel=0.2 "
                        "--opt verify_agree_samples=5")
    r.add_argument("--resume", action="store_true",
                   help="断点续跑: 读输出目录 _progress.jsonl, 跳过已完成且"
                        "成品存在的文件")
    r.add_argument("--retry-failed", type=int, default=1, metavar="N",
                   help="失败文件的自动重试轮数 (用精确模式; 默认 1, 0=关)")
    r.add_argument("--in-place", action="store_true",
                   help="修完后写回原件 (必须同时给 --backup 与 --yes)")
    r.add_argument("--backup", default="", help="写回前的备份目录")
    r.add_argument("--yes", action="store_true", help="确认写回原件")
    r.add_argument("--keep-all", action="store_true",
                   help="无需修复的文件也保留一份拷贝 (默认只在真正修过时"
                        "才产出文件)")
    r.add_argument("--no-container-fix", action="store_true",
                   help="只修音频/元数据，不做 tkhd/sample-entry/时长 等"
                        "容器级修复")
    r.add_argument("--fix-layout", action="store_true",
                   help="额外把 moov-last 文件恢复为 moov-first "
                        "(MP4Box -inter，整文件重交织，较慢)")
    r.set_defaults(func=cmd_repair)

    args = ap.parse_args(argv)
    if int(getattr(args, "jobs", 1) or 1) > 1:
        # 并行进程各自再开 BLAS/OpenMP 线程会过度订阅；每个 worker 单线程，
        # 总吞吐由文件级并行提供。用户显式设置的环境变量优先。
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ.setdefault(key, "1")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
