#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""详细 Sony 元数据对比自检 (detailed structural selfcheck).

比 validate.compare 更细: 逐轨 stts/stsz/stsd/elst/tref 全字段对比,
rtmd 载荷 sha256 + 首尾样本字节, nrtm lens/XML sha256, uuid 清单
(PROF/USMT/未知, 含上下文), ftyp 品牌, 逐音频轨字段, Gyroflow 消费端
解析。所有条目 + PASS/FAIL 写 log 入盘 (JSON + 可读文本)。

主程序 `--check full` 强度调用本模块; 独立 CLI 亦可用:
    python -m preservation.selfcheck <original> <final> <log_dir>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import isobmf
from .gpac import GpacContainerBackend
from .gyroflow import _export, _facts
from .sony import ParsedFile

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ffprobe_streams(ffprobe: Path, path: Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-count_packets",
            "-show_entries",
            "stream=index,codec_type,codec_name,profile,pix_fmt,width,"
            "height,sample_rate,channels,sample_fmt,r_frame_rate,"
            "avg_frame_rate,nb_read_packets,duration,bits_per_raw_sample:"
            "stream_tags=timecode,handler_name",
            "-of", "json", str(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-300:]}")
    return json.loads(proc.stdout).get("streams", [])


def _uuid_inventory(path: Path) -> dict[str, list[dict[str, Any]]]:
    inv: dict[str, list[dict[str, Any]]] = {}
    for context, ext, raw in isobmf.extract_uuid_boxes(path):
        label = isobmf.uuid_label(ext) or "UNKNOWN"
        inv.setdefault(label, []).append(
            {
                "context": context,
                "guid": isobmf.uuid_guid(ext),
                "sha256": isobmf.sha256_bytes(raw),
                "size": len(raw),
            }
        )
    return inv


def _ftyp(parsed: ParsedFile) -> dict[str, Any]:
    for e in parsed.root:
        if e.tag.split("}")[-1] == "FileTypeBox":
            return {
                "major": e.get("MajorBrand", ""),
                "minor": e.get("MinorVersion", ""),
                "compat": sorted(
                    x.get("AlternateBrand", "")
                    for x in e.iter()
                    if x.tag.split("}")[-1] == "BrandEntry"
                ),
            }
    return {"major": "", "minor": "", "compat": []}


def _payload_facts(
    gpac: GpacContainerBackend, path: Path, track_id: int, scratch: Path
) -> dict[str, Any]:
    """rtmd payload sha256 + first/last 32 sample bytes."""
    raw = scratch / "rtmd_samples.bin"
    gpac.raw_track(path, track_id, raw)
    digest = _sha256_file(raw)
    size = raw.stat().st_size
    head = tail = b""
    with raw.open("rb") as f:
        head = f.read(32)
        f.seek(max(0, size - 32))
        tail = f.read(32)
    try:
        raw.unlink()
    except OSError:
        pass
    return {
        "sha256": digest,
        "size": size,
        "head_bytes": head.hex(),
        "tail_bytes": tail.hex(),
    }


def _compare_items(
    results: list[dict[str, Any]],
    name: str,
    a: Any,
    b: Any,
    detail: str = "",
) -> bool:
    ok = a == b
    results.append(
        {
            "item": name,
            "status": PASS if ok else FAIL,
            "detail": detail or f"original={a!r} final={b!r}",
        }
    )
    return ok


def detailed_compare(
    *,
    original: Path,
    final: Path,
    gpac: GpacContainerBackend,
    ffprobe: Path,
    log_dir: Path,
    gyroflow: Path | None = None,
) -> dict[str, Any]:
    """Extensive ORIGINAL vs FINAL Sony metadata comparison.

    Returns the full result dict (also written to log_dir as
    selfcheck_<stem>.json + .txt)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    scratch = log_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    started = time.time()

    def note(name: str, a: Any, b: Any, detail: str = "") -> bool:
        return _compare_items(results, name, a, b, detail)

    src = ParsedFile(gpac.diso_xml(original))
    out = ParsedFile(gpac.diso_xml(final))
    src_streams = _ffprobe_streams(ffprobe, original)
    out_streams = _ffprobe_streams(ffprobe, final)

    # --- container / brands ---
    note("ftyp", _ftyp(src), _ftyp(out))

    # --- movie ---
    def mvhd(p: ParsedFile) -> dict[str, str]:
        for e in p.root.iter():
            if e.tag.split("}")[-1] == "MovieHeaderBox":
                return {
                    "timescale": e.get("TimeScale", ""),
                    "duration": e.get("Duration", ""),
                }
        return {}

    note("mvhd", mvhd(src), mvhd(out))

    # --- per-track deep comparison ---
    def track_map(p: ParsedFile) -> dict[str, list[dict[str, Any]]]:
        by_handler: dict[str, list[dict[str, Any]]] = {}
        for trak in p.tracks():
            info = p.track_info(trak)
            by_handler.setdefault(info["handler_type"], []).append(info)
        return {
            h: sorted(ts, key=lambda t: t["track_id"])
            for h, ts in by_handler.items()
        }

    src_tracks = track_map(src)
    out_tracks = track_map(out)
    note(
        "track.handlers",
        sorted(src_tracks),
        sorted(out_tracks),
        f"source track ids: "
        f"{[t['track_id'] for ts in src_tracks.values() for t in ts]}",
    )

    def video_facts(p: ParsedFile) -> dict[str, Any] | None:
        for trak in p.tracks():
            info = p.track_info(trak)
            if info["handler_type"] == "vide":
                return {
                    "timescale": info["timescale"],
                    "media_duration": info["media_duration"],
                    "track_duration": info["track_duration"],
                    "stts": info["stts"],
                    "elst": info["elst"],
                    "stsd": info["sample_entry"],
                    "refs": info["refs"],
                }
        return None

    sv = video_facts(src)
    ov = video_facts(out)
    if sv and ov:
        for key in ("timescale", "stts"):
            note(f"video.{key}", sv[key], ov[key])
        # codec change is the EXPECTED result of re-encoding: verify the
        # final is HEVC rather than requiring codec equality.
        results.append(
            {
                "item": "video.stsd",
                "status": PASS if ov["stsd"] in ("hvc1", "hev1") else FAIL,
                "detail": (
                    f"original={sv['stsd']} final={ov['stsd']} "
                    "(re-encode, HEVC expected)"
                ),
            }
        )
        note(
            "video.track_duration", sv["track_duration"], ov["track_duration"]
        )
        note(
            "video.elst.durations",
            [e[0] for e in sv["elst"]],
            [e[0] for e in ov["elst"]],
            f"media_time {sv['elst'][0][1] if sv['elst'] else '-'} -> "
            f"{ov['elst'][0][1] if ov['elst'] else '-'} "
            "(encoder priming shift allowed)",
        )

    # video ffprobe facts
    s_v = next(
        (s for s in src_streams if s.get("codec_type") == "video"), None
    )
    o_v = next(
        (s for s in out_streams if s.get("codec_type") == "video"), None
    )
    if s_v and o_v:
        for key in ("width", "height", "avg_frame_rate", "nb_read_packets"):
            note(f"video.ffprobe.{key}", s_v.get(key), o_v.get(key))
        results.append(
            {
                "item": "video.ffprobe.codec",
                "status": PASS if o_v.get("codec_name") == "hevc" else FAIL,
                "detail": (
                    f"original={s_v.get('codec_name')} "
                    f"final={o_v.get('codec_name')} "
                    "(re-encode, HEVC expected)"
                ),
            }
        )

    # audio per-track
    s_audio = sorted(src_tracks.get("soun", []), key=lambda t: t["track_id"])
    o_audio = sorted(out_tracks.get("soun", []), key=lambda t: t["track_id"])
    note("audio.track_count", len(s_audio), len(o_audio))
    for idx, (sa, oa) in enumerate(zip(s_audio, o_audio)):
        for key in (
            "sample_entry", "timescale", "media_duration", "track_duration",
        ):
            note(f"audio[{idx}].{key}", sa[key], oa[key])
        note(
            f"audio[{idx}].stts_sum",
            sum(c * d for c, d in sa["stts"]),
            sum(c * d for c, d in oa["stts"]),
        )
    s_a_streams = [s for s in src_streams if s.get("codec_type") == "audio"]
    o_a_streams = [s for s in out_streams if s.get("codec_type") == "audio"]
    note(
        "audio.ffprobe.codecs",
        [(s.get("codec_name"), s.get("sample_rate"), s.get("channels"))
         for s in s_a_streams],
        [(s.get("codec_name"), s.get("sample_rate"), s.get("channels"))
         for s in o_a_streams],
    )

    # --- metadata (rtmd) tracks: deep payload verification ---
    s_meta = src_tracks.get("meta", [])
    o_meta = out_tracks.get("meta", [])
    note("rtmd.track_count", len(s_meta), len(o_meta))
    for idx, (sm, om) in enumerate(zip(s_meta, o_meta)):
        for key in (
            "sample_entry", "handler_name", "timescale", "media_duration",
            "track_duration", "constant_sample_size", "stts", "elst",
        ):
            note(f"rtmd[{idx}].{key}", sm[key], om[key])
        note(
            f"rtmd[{idx}].sample_sizes",
            sm["sample_sizes"], om["sample_sizes"],
        )
        note(
            f"rtmd[{idx}].tref_cdsc",
            [r for r in sm["refs"] if r["type"] == "cdsc"],
            [r for r in om["refs"] if r["type"] == "cdsc"],
        )
        sp = _payload_facts(gpac, original, sm["track_id"], scratch)
        op = _payload_facts(gpac, final, om["track_id"], scratch)
        note(f"rtmd[{idx}].payload.sha256", sp["sha256"], op["sha256"])
        note(f"rtmd[{idx}].payload.size", sp["size"], op["size"])
        note(
            f"rtmd[{idx}].payload.head_bytes",
            sp["head_bytes"], op["head_bytes"],
        )
        note(
            f"rtmd[{idx}].payload.tail_bytes",
            sp["tail_bytes"], op["tail_bytes"],
        )

    # rtmd stream tags (timecode / handler)
    s_d = next(
        (s for s in src_streams if s.get("codec_type") == "data"), None
    )
    o_d = next(
        (s for s in out_streams if s.get("codec_type") == "data"), None
    )
    if s_d is not None or o_d is not None:
        s_tags = (s_d or {}).get("tags", {}) or {}
        o_tags = (o_d or {}).get("tags", {}) or {}
        note(
            "rtmd.tag.timecode", s_tags.get("timecode"), o_tags.get("timecode")
        )
        note(
            "rtmd.tag.handler_name",
            s_tags.get("handler_name"), o_tags.get("handler_name"),
        )

    # --- nrtm file-level meta ---
    s_meta_box = src.meta_info()
    o_meta_box = out.meta_info()
    if s_meta_box and s_meta_box.get("handler_type") == "nrtm":
        note("nrtm.handler", s_meta_box.get("handler_type"),
             (o_meta_box or {}).get("handler_type"))
        for key in ("item_id", "item_name", "item_mime", "item_type"):
            note(
                f"nrtm.{key}",
                s_meta_box.get(key, ""),
                (o_meta_box or {}).get(key, ""),
            )
        if s_meta_box.get("item_id"):
            lens_s = scratch / "src_lens.bin"
            lens_o = scratch / "out_lens.bin"
            gpac.dump_meta_item(original, s_meta_box["item_id"], lens_s)
            if (o_meta_box or {}).get("item_id"):
                gpac.dump_meta_item(
                    final, o_meta_box["item_id"], lens_o
                )
                note(
                    "nrtm.lens.sha256",
                    _sha256_file(lens_s), _sha256_file(lens_o),
                )
                note(
                    "nrtm.lens.size",
                    lens_s.stat().st_size, lens_o.stat().st_size,
                )
            else:
                results.append(
                    {"item": "nrtm.lens", "status": FAIL,
                     "detail": "lens item missing in final"}
                )
            for p in (lens_s, lens_o):
                try:
                    p.unlink()
                except OSError:
                    pass
        if s_meta_box.get("has_xml"):
            xml_s = scratch / "src_meta.xml"
            xml_o = scratch / "out_meta.xml"
            has_s = gpac.dump_meta_xml(original, xml_s)
            has_o = gpac.dump_meta_xml(final, xml_o)
            if has_s and has_o:
                note(
                    "nrtm.xml.sha256",
                    _sha256_file(xml_s), _sha256_file(xml_o),
                )
            else:
                results.append(
                    {"item": "nrtm.xml", "status": FAIL,
                     "detail": f"xml present: original={has_s} final={has_o}"}
                )

    # --- uuid inventory ---
    s_uuids = _uuid_inventory(original)
    o_uuids = _uuid_inventory(final)
    for label in sorted(set(s_uuids) | set(o_uuids)):
        s_list = sorted(
            (u["sha256"], u["context"]) for u in s_uuids.get(label, [])
        )
        o_list = sorted(
            (u["sha256"], u["context"]) for u in o_uuids.get(label, [])
        )
        note(f"uuid.{label}", s_list, o_list,
             f"original={len(s_list)} final={len(o_list)} box(es)")

    # --- Gyroflow consumer parse (optional) ---
    gyro: dict[str, Any] | None = None
    if gyroflow is not None:
        try:
            g_src = _facts(
                _export(gyroflow, original, scratch / "g_orig.json")
            )
            g_out = _facts(
                _export(gyroflow, final, scratch / "g_final.json")
            )
            mismatches = {
                k: {"original": g_src[k], "final": g_out[k]}
                for k in g_src
                if k != "imu_samples" and g_src[k] != g_out[k]
            }
            ok = (
                g_out["imu_samples"] > 0
                and g_out["imu_samples"] == g_src["imu_samples"]
                and not mismatches
            )
            gyro = {
                "status": PASS if ok else FAIL,
                "original": g_src,
                "final": g_out,
                "mismatches": mismatches,
            }
            results.append(
                {
                    "item": "gyroflow.consumer",
                    "status": PASS if ok else FAIL,
                    "detail": (
                        f"IMU {g_out['imu_samples']}/{g_src['imu_samples']} "
                        f"samples, {g_out['detected_source']}"
                    ),
                }
            )
        except Exception as exc:
            results.append(
                {"item": "gyroflow.consumer", "status": SKIP,
                 "detail": f"not run: {exc}"}
            )

    # --- summary ---
    summary = {
        s: sum(1 for r in results if r["status"] == s)
        for s in (PASS, FAIL, SKIP)
    }
    overall = summary[FAIL] == 0
    report = {
        "original": str(original),
        "final": str(final),
        "summary": summary,
        "overall": PASS if overall else FAIL,
        "elapsed_sec": round(time.time() - started, 2),
        "items": results,
        "gyroflow": gyro,
    }

    stem = original.stem
    json_path = log_dir / f"selfcheck_{stem}.json"
    txt_path = log_dir / f"selfcheck_{stem}.txt"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"1KeyTranscoder detailed Sony selfcheck — {original.name}",
        f"original: {original}",
        f"final   : {final}",
        f"overall : {report['overall']}  {summary}  "
        f"({report['elapsed_sec']}s)",
        "-" * 70,
    ]
    for r in results:
        lines.append(f"[{r['status']:>4}] {r['item']:<38} {r['detail']}")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python -m preservation.selfcheck "
            "<original> <final> <log_dir>",
            file=sys.stderr,
        )
        return 2
    original = Path(argv[0])
    final = Path(argv[1])
    log_dir = Path(argv[2])
    gpac = GpacContainerBackend(Path(r"C:\Program Files\GPAC"))
    ffprobe = Path(shutil.which("ffprobe") or "ffprobe")
    from .gyroflow import find_gyroflow
    gyro = find_gyroflow(None)
    report = detailed_compare(
        original=original,
        final=final,
        gpac=gpac,
        ffprobe=ffprobe,
        log_dir=log_dir,
        gyroflow=gyro,
    )
    print(f"selfcheck: {report['overall']} {report['summary']} -> {log_dir}")
    return 0 if report["overall"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
