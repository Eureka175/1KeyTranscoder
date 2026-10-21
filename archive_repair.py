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
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FFMPEG = ROOT / "tools" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffprobe.exe"
SCRATCH = Path(os.environ.get("ARCHIVE_REPAIR_SCRATCH",
                              r"E:\1kt_scratch\archive_repair"))

#: 判定词汇。全部显式, 不允许"没报错就算过"。
PASS, FAIL, SKIP, WARN, FIXED = "PASS", "FAIL", "SKIP", "WARN", "FIXED"
UNMEASURABLE = "UNMEASURABLE"

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
                facts["tracks"].append({
                    "handler": handler,
                    "tkhd_size": tkhd.size if tkhd else None,
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


def check_container(path: Path, facts: dict, doc: dict) -> list[dict]:
    """元数据写入位置 / 结构规范。**只看文件自身**, 不需要源。"""
    out: list[dict] = []

    brand = facts.get("ftyp_major")
    out.append(_item("CTR-01", PASS if brand else FAIL,
                     f"ftyp major={brand!r}",
                     {"major": brand, "compatible": facts["ftyp_compatible"]}))

    handlers = [t["handler"] for t in facts["tracks"]]
    has_data = any(h not in ("vide", "soun") for h in handlers)
    cdsc = facts["tref_cdsc"]
    if has_data:
        ok = bool(cdsc)
        out.append(_item("CTR-02", PASS if ok else FAIL,
                         f"数据轨存在, tref/cdsc={'有' if ok else '缺失'}",
                         cdsc))
    else:
        out.append(_item("CTR-02", SKIP, "无数据轨, tref/cdsc 不适用", cdsc))

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
    if mf is None:
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
    if entries:
        fourccs = sorted({e["fourcc"] for e in entries})
        # `in24` 是已知陷阱: Sony XAVC-S 的 LPCM 实际是 ipcm, 写成 in24 会让
        # 下游的 audio.tracks 关键项判 MODIFIED。
        bad = [c for c in fourccs if c == "in24"]
        out.append(_item("CTR-07", WARN if bad else PASS,
                         f"音频 sample entry={fourccs}"
                         + (" — in24 是已知陷阱 (应为 ipcm)" if bad else ""),
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
        if not wentries:
            out.append(_item("CTR-08", SKIP, "无音频 sample entry"))
        else:
            bad = []
            for i, e in enumerate(wentries):
                c = audio_entry_consistency(e["bytes"])
                if not c["consistent"]:
                    bad.append({"track": i + 1, "detail": c})
            out.append(_item(
                "CTR-08", WARN if bad else PASS,
                f"{len(wentries)} 条音频条目, "
                + ("全部自洽" if not bad else
                   f"{len(bad)} 条自相矛盾: " + "; ".join(
                       f"CH{b['track']} {b['detail']['problems']}"
                       for b in bad)),
                bad or None))
    except Exception as exc:                            # noqa: BLE001
        out.append(_item("CTR-08", SKIP,
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


def repair_one(src: Path, dst: Path, *, opts: dict | None,
               want_moov_first: bool, log) -> dict[str, Any]:
    """一次修复: 测 + 移 + 元数据恢复 + 布局恢复 + 校验。"""
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
        rec["error"] = "输出文件未生成"
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

    # --- 没有修复动作的文件不值得再验一遍 -------------------------------
    #
    # `already_aligned` / `measure_failed` 时透明路径产出的是**源文件字节级
    # 拷贝**, 对它跑 validate/gyroflow/重新测量等于"拿源和源比", 既慢又没有
    # 信息量。这类文件记为 NOOP, 由调用方决定是否保留拷贝 (--keep-all)。
    if rec["status"] != "applied":
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
    aud_items, aud_data = check_audio(dst, after_doc, opts)
    rec["audio_after"] = aud_data
    rec["audio_after_checks"] = aud_items
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
def iter_media(root: Path, pattern: str) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in
                  {e.lower() for e in pattern.split(",")})


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


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.input)
    files = iter_media(root, args.ext)
    if args.limit:
        files = files[: args.limit]
    opts: dict[str, Any] = {}
    if args.groups:
        opts["group_anchor_enabled"] = True
    if args.accurate:
        opts["channel_sync_accurate"] = True
    opts.update(parse_opt_overrides(getattr(args, "opt", [])))
    print(f"check: {len(files)} 个文件 (root={root}) "
          f"opts={opts or 'DEFAULTS'}", flush=True)
    rows = []
    n_fail = 0
    for i, p in enumerate(files, 1):
        doc = probe(p)
        if not doc.get("streams"):
            rows.append({"path": str(p), "overall": FAIL,
                         "checks": [_item("FMT-00", FAIL, "ffprobe 读不到流")]})
            n_fail += 1
            print(f"[{i}/{len(files)}] FAIL probe {p.name}", flush=True)
        else:
            r = summarise_check(p, doc, opts or None)
            rows.append(r)
            if r["overall"] == FAIL:
                n_fail += 1
            print(f"[{i}/{len(files)}] {r['overall']:<12} {p.name}", flush=True)
    md = write_report(rows, Path(args.json or "."), "归档规范检查")
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["overall"]] = tally.get(r["overall"], 0) + 1
    print(f"\n{tally}\n-> {md}")
    return 0 if n_fail == 0 else 1


def verdict_of(rec: dict[str, Any]) -> str:
    """把一次修复的结果归到一个词上。**只有真正改过内容才算 REPAIRED。**"""
    if rec.get("error") and rec.get("status") == "metadata_mismatch":
        return "FAILED"
    if rec.get("error") and not rec.get("no_fix_needed"):
        return "FAILED"
    if rec.get("status") == "applied":
        return "REPAIRED" if (rec.get("metadata") or {}).get("identical") \
            else "FAILED"
    if rec.get("no_fix_needed"):
        return "NOOP"
    return "FAILED" if rec.get("error") else "NOOP"


def cmd_repair(args: argparse.Namespace) -> int:
    root = Path(args.input)
    outp = Path(args.output)
    files = iter_media(root, args.ext)
    if args.limit:
        files = files[: args.limit]
    print(f"repair: {len(files)} 个候选文件 → {outp}", flush=True)

    opts: dict[str, Any] = {}
    if args.groups:
        opts["group_anchor_enabled"] = True
    if args.accurate:
        opts["channel_sync_accurate"] = True
    opts.update(parse_opt_overrides(getattr(args, "opt", [])))
    opts_or_none = opts or None

    rows: list[dict] = []
    applied = 0
    for i, src in enumerate(files, 1):
        doc = probe(src)
        if not doc.get("streams"):
            # 单个坏文件**不许中断整批**。这条记录必须带上 `overall`,
            # 否则下面统计时 KeyError 会把 200 多个文件的批次整体打掉
            # (真发生过: I:\20260901 有一个 ffprobe 读不了的文件)。
            rec = {"src": str(src), "status": "probe_failed",
                   "overall": "FAILED",
                   "error": "ffprobe 读不到流, 无法判定, 未做任何修改"}
            rows.append(rec)
            print(f"[{i}/{len(files)}] FAILED    {src.name} "
                  f"(ffprobe 读不到流)", flush=True)
            continue
        dst = outp / _rel(src, root if root.is_dir() else root.parent)
        facts = container_facts(src)
        rec = repair_one(
            src, dst, opts=opts_or_none,
            want_moov_first=bool(facts.get("moov_first")),
            log=lambda m: print(f"    {m}", flush=True))
        rec["overall"] = verdict_of(rec)
        if rec["overall"] == "NOOP" and not args.keep_all:
            # 没有修复动作的文件不留在暂存目录里: 一个 667 件的归档里有 355 件
            # 根本不可对齐、280 件已对齐, 把它们都拷一遍只是多占 90+ GiB。
            dst.unlink(missing_ok=True)
            rec["output_kept"] = False
        else:
            rec["output_kept"] = dst.is_file()
        if rec["overall"] == "REPAIRED":
            applied += 1
        rows.append(rec)
        print(f"[{i}/{len(files)}] {rec['overall']:<9} {src.name} "
              f"shifts={rec.get('shifts')} layout={rec.get('layout_detail')} "
              f"meta_ok={(rec.get('metadata') or {}).get('identical')} "
              f"status={rec.get('status')}",
              flush=True)

    md = write_report(rows, Path(args.json or outp), "归档修复")
    tally: dict[str, int] = {}
    for r in rows:
        k = r.get("overall") or "FAILED"
        tally[k] = tally.get(k, 0) + 1
    print(f"\n{tally}\n-> {md}")

    if args.in_place:
        if not (args.yes and args.backup):
            print("[FATAL] --in-place 需要同时给 --backup DIR 与 --yes",
                  file=sys.stderr)
            return 2
        n = apply_back(rows, Path(args.backup))
        print(f"in-place: 已替换 {n} 个文件 (备份在 {args.backup})")
    return 0 if not any(r["overall"] == "FAILED" for r in rows) else 1


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
        description="归档成品的延迟重修 / 元数据位置修复 / 规范检查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[1] if "用法：" in __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--input", required=True, help="文件或目录 (递归)")
        p.add_argument("--json", default="", help="报告输出目录")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--ext", default=".mp4,.MP4,.mov,.MOV",
                       help="扩展名过滤, 逗号分隔")

    c = sub.add_parser("check", help="只检查, 不写任何文件 (源文件只读)")
    common(c)
    c.add_argument("--groups", action="store_true",
                   help="按分组锚点语义测量 (与 repair 一致)")
    c.add_argument("--accurate", action="store_true",
                   help="按精确模式语义测量 (能看见 2~5 样本的小偏移)")
    c.add_argument("--opt", action="append", default=[], metavar="K=V",
                   help="透传给 channel-sync 的选项覆盖, 可重复; "
                        "例如 --opt verify_max_rel=0.2 --opt verify_agree_samples=5")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("repair", help="修复到暂存目录 (默认不改原件)")
    common(r)
    r.add_argument("--output", required=True, help="暂存目录 (必填)")
    r.add_argument("--groups", action="store_true",
                   help="打开分组锚点 (与 1kt.py 同名开关语义一致)")
    r.add_argument("--accurate", action="store_true",
                   help="打开精确模式 (1→2→5 样本分级)")
    r.add_argument("--opt", action="append", default=[], metavar="K=V",
                   help="透传给 channel-sync 的选项覆盖, 可重复; "
                        "例如 --opt verify_max_rel=0.2 --opt verify_agree_samples=5")
    r.add_argument("--in-place", action="store_true",
                   help="修完后写回原件 (必须同时给 --backup 与 --yes)")
    r.add_argument("--backup", default="", help="写回前的备份目录")
    r.add_argument("--yes", action="store_true", help="确认写回原件")
    r.add_argument("--keep-all", action="store_true",
                   help="无需修复的文件也保留一份拷贝 (默认只在真正修过时"
                        "才产出文件)")
    r.set_defaults(func=cmd_repair)

    args = ap.parse_args(argv)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
