#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the audio description of a source file and its repaired versions.

The reported symptom is "all 4 channels are white noise with faint original
audio mixed in, PR cannot decode, VLC stutters" while ffmpeg/ffprobe read the
file fine. That signature points at the *sample description* (stsd) or the
sample tables (stts/stsz/stsc), not at the samples themselves -- ffmpeg is
lenient about the former and our own checks never looked at it.

Prints, per audio track: ffprobe's view, the raw stsd entry (fourcc + fields +
child boxes such as pcmC), and stts/stsz/stsc summaries.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
FFPROBE = REPO / "tools" / "ffprobe.exe"


# --------------------------------------------------------------------------
# minimal box reader (independent of preservation.isobmf)
# --------------------------------------------------------------------------
class Box:
    __slots__ = ("type", "start", "data_start", "end", "size")

    def __init__(self, typ, start, data_start, end, size):
        self.type, self.start, self.data_start = typ, start, data_start
        self.end, self.size = end, size


def boxes(f, start, end):
    out, off = [], start
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


def child(f, b, *path):
    cur = b
    for name in path:
        cur = next((x for x in boxes(f, cur.data_start, cur.end)
                    if x.type == name), None)
        if cur is None:
            return None
    return cur


def traks(f, moov):
    return [b for b in boxes(f, moov.data_start, moov.end) if b.type == "trak"]


def handler(f, trak):
    h = child(f, trak, "mdia", "hdlr")
    if h is None:
        return None
    f.seek(h.data_start + 8)
    return f.read(4).decode("latin-1")


def full_u32(f, b, offset):
    f.seek(b.data_start + offset)
    return struct.unpack(">I", f.read(4))[0]


def stsd_entries(f, trak):
    stsd = child(f, trak, "mdia", "minf", "stbl", "stsd")
    if stsd is None:
        return []
    n = full_u32(f, stsd, 4)                      # version/flags 之后是 count
    f.seek(stsd.data_start + 8)
    out = []
    off = f.tell()
    for _ in range(n):
        f.seek(off)
        raw = f.read(8)
        if len(raw) < 8:
            break
        size, typ = struct.unpack(">I4s", raw)
        entry = {"fourcc": typ.decode("latin-1"), "size": size,
                 "offset": off, "fields": {}, "children": []}
        # AudioSampleEntry: 8 header + 6 reserved + 2 dri + 8 (ver/rev/vendor)
        # + 2 channelcount + 2 samplesize + 2 pre_defined + 2 reserved
        # + 4 samplerate  => fourcc 之后 28 字节
        f.seek(off + 8)
        body = f.read(size - 8)
        if len(body) >= 28:
            ver, rev, vendor, ch, ssize, pre, res, srate = struct.unpack(
                ">HHIHHHHI", body[6:6 + 20])
            entry["fields"] = {
                "version": ver, "revision": rev, "vendor": hex(vendor),
                "channelcount": ch, "samplesize": ssize,
                "pre_defined": pre, "reserved": res,
                "samplerate_fixed16": srate / 65536.0,
            }
            # 子 box 从 entry 起点 + 8 + 28 开始
            sub_start = off + 8 + 28
            for cb in boxes(f, sub_start, off + size):
                blob = None
                if cb.type == "pcmC":
                    f.seek(cb.data_start)
                    d = f.read(cb.size - 8)
                    blob = {"format_flags": d[0] if d else None,
                            "pcm_sample_size": d[1] if len(d) > 1 else None}
                elif cb.type in ("wave", "enda", "chan", "btrt"):
                    f.seek(cb.data_start)
                    blob = {"bytes": f.read(min(cb.size - 8, 24)).hex()}
                entry["children"].append({"type": cb.type, "box": blob})
        out.append(entry)
        off += size
    return out


def table_summary(f, trak):
    stbl = child(f, trak, "mdia", "minf", "stbl")
    res = {}
    if stbl is None:
        return res
    stsd = child(f, stbl, "stsd")
    if stsd is not None:
        res["stsd_count"] = full_u32(f, stsd, 4)
    stts = child(f, stbl, "stts")
    if stts is not None:
        n = full_u32(f, stts, 4)
        f.seek(stts.data_start + 8)
        pairs = [struct.unpack(">II", f.read(8)) for _ in range(min(n, 8))]
        res["stts_entries"] = n
        res["stts_head"] = pairs
        tot = 0
        f.seek(stts.data_start + 8)
        for _ in range(n):
            c, d = struct.unpack(">II", f.read(8))
            tot += c
        res["sample_count_from_stts"] = tot
    stsz = child(f, stbl, "stsz")
    if stsz is not None:
        f.seek(stsz.data_start + 4)
        fixed, count = struct.unpack(">II", f.read(8))
        res["stsz_sample_size"] = fixed
        res["stsz_count"] = count
    stsc = child(f, stbl, "stsc")
    if stsc is not None:
        res["stsc_entries"] = full_u32(f, stsc, 4)
    stco = child(f, stbl, "stco")
    co64 = child(f, stbl, "co64")
    if stco is not None:
        res["stco_entries"] = full_u32(f, stco, 4)
    if co64 is not None:
        res["co64_entries"] = full_u32(f, co64, 4)
    return res


def ffprobe_view(path: Path) -> list[dict]:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    doc = json.loads(r.stdout or "{}")
    out = []
    for s in doc.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        out.append({
            "index": s.get("index"),
            "codec_name": s.get("codec_name"),
            "codec_tag_string": s.get("codec_tag_string"),
            "sample_fmt": s.get("sample_fmt"),
            "sample_rate": s.get("sample_rate"),
            "channels": s.get("channels"),
            "bits_per_raw_sample": s.get("bits_per_raw_sample"),
            "bits_per_sample": s.get("bits_per_sample"),
            "duration": s.get("duration"),
            "nb_frames": s.get("nb_frames"),
            "start_time": s.get("start_time"),
        })
    fmt = doc.get("format") or {}
    out.append({"format": {"duration": fmt.get("duration"),
                           "bit_rate": fmt.get("bit_rate")}})
    return out


def describe(tag: str, path: Path) -> None:
    print(f"\n{'=' * 78}\n{tag}: {path}")
    if not path.is_file():
        print("  MISSING")
        return
    print(f"  size {path.stat().st_size / 2**20:.1f} MiB")
    print("  ffprobe:")
    for s in ffprobe_view(path):
        if "format" in s:
            print(f"    container: {s['format']}")
        else:
            print(f"    [{s['index']}] {s['codec_name']} tag={s['codec_tag_string']} "
                  f"fmt={s['sample_fmt']} bps={s['bits_per_raw_sample']}/"
                  f"{s['bits_per_sample']} sr={s['sample_rate']} "
                  f"ch={s['channels']} dur={s['duration']} "
                  f"frames={s['nb_frames']} start={s['start_time']}")
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        top = boxes(f, 0, end)
        print(f"  top_level: {[b.type for b in top]}")
        moov = next((b for b in top if b.type == "moov"), None)
        if moov is None:
            print("  NO MOOV")
            return
        for i, t in enumerate(traks(f, moov)):
            h = handler(f, t)
            if h != "soun":
                continue
            print(f"  --- trak #{i} handler={h}")
            for e in stsd_entries(f, t):
                print(f"      stsd fourcc={e['fourcc']!r} size={e['size']} "
                      f"fields={e['fields']}")
                for c in e["children"]:
                    print(f"        child {c['type']}: {c['box']}")
            print(f"      tables: {table_summary(f, t)}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: diag_audio.py TAG=PATH [TAG=PATH ...]")
        return 2
    for arg in sys.argv[1:]:
        tag, _, p = arg.partition("=")
        describe(tag, Path(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
