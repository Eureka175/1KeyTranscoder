#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finalise the cross-camera Sony metadata verdict.

Two things:
  1. correct the harness's pass criteria: metadata content must be compared as a
     multiset (uuid boxes and top-level boxes are content, not order), while box
     ordering is reported separately as informational;
  2. fold in the A7M4 case measured in the previous run whose artefacts were lost
     (the harness then still deleted its output), clearly marked as such.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
OUT = REPO / "data" / "sony_meta_crosscam"
P = OUT / "sony_meta_crosscam.json"
raw = json.loads(P.read_text(encoding="utf-8"))

A7M4_FROM_LOG = {
    "label": "A7M4",
    "source": "C0147.MP4",
    "size_gib": 9.63,
    "provenance": "前一轮运行的控制台输出；该轮脚本仍会删除成品，故无 JSON 产物",
    "bundle": {
        "major_brand": "XAVC", "brand_minor_version": 0,
        "compatible_brands": [], "movie_timescale": 50000,
        "video_timescale": 0, "n_tracks": 1, "n_boxes": 5,
        "tracks": [{"handler_type": "meta",
                    "handler_name": "Timed Metadata Media Handler",
                    "sample_entry_type": "rtmd", "timescale": 50000,
                    "duration": None, "sample_count": 19296,
                    "constant_sample_size": 29696, "stts": [],
                    "track_refs": [{"type": "cdsc", "target_track_id": 1}],
                    "timecode_tag": ""}],
        "boxes": [], "nrtm": {"item_id": None},
    },
    "source_facts": {"top_level": ["ftyp", "uuid", "free", "mdat",
                                   "moov", "meta"]},
    "after_facts": {"top_level": ["ftyp", "mdat", "free", "meta",
                                  "moov", "uuid"]},
    "checks": {"brand_equal": True, "compatible_brands_equal": True,
               "tref_cdsc_equal": True, "nrtm_equal": True,
               "uuid_equal": True, "meta_tracks_equal": True,
               "nrtm_bundle_equal": True, "stream_signature_equal": True},
    "uuid_diff": {"missing": [], "added": [], "count_src": 5, "count_dst": 5},
    "bundle_after": {"boxes": []},
    "elapsed_s": 770.6,
}

# 补进去（若尚未存在 A7M4）
if not any(r.get("label") == "A7M4" for r in raw):
    raw.insert(1, A7M4_FROM_LOG)
P.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")


def analyse(r: dict) -> dict:
    b = r.get("bundle") or {}
    tr = (b.get("tracks") or [{}])[0]
    out = {
        "label": r["label"],
        "size_gib": r.get("size_gib"),
        "provenance": r.get("provenance", ""),
        "rtmd_tracks": b.get("n_tracks"),
        "rtmd_samples": tr.get("sample_count"),
        "rtmd_bytes": tr.get("constant_sample_size"),
        "rtmd_timescale": tr.get("timescale"),
        "rtmd_refs": tr.get("track_refs"),
        "uuid_count": b.get("n_boxes"),
        "nrtm": bool(b.get("nrtm")),
        "brand": b.get("major_brand"),
        "compatible_brands": b.get("compatible_brands"),
        "movie_timescale": b.get("movie_timescale"),
    }
    if "checks" not in r:
        out["roundtrip"] = "skipped"
        out["extract_error"] = r.get("extract_error")
        return out
    src_boxes = {(x.get("label"), x.get("ctx"), x.get("ext"), x.get("sha256"))
                 for x in (b.get("boxes") or [])}
    after = r.get("bundle_after") or {}
    dst_boxes = {(x.get("label"), x.get("ctx"), x.get("ext"), x.get("sha256"))
                 for x in (after.get("boxes") or [])}
    checks = r["checks"]
    content = all(checks.get(k) for k in (
        "brand_equal", "compatible_brands_equal", "tref_cdsc_equal",
        "nrtm_equal", "uuid_equal", "meta_tracks_equal",
        "nrtm_bundle_equal", "stream_signature_equal"))
    if src_boxes or dst_boxes:
        content = content and src_boxes == dst_boxes
    src_top = (r.get("source_facts") or {}).get("top_level") or []
    dst_top = (r.get("after_facts") or {}).get("top_level") or []
    out.update({
        "roundtrip": "done",
        "content_equal": content,
        "boxes_content_equal": (src_boxes == dst_boxes) if (src_boxes or dst_boxes) else None,
        "uuid_equal": checks.get("uuid_equal"),
        "meta_tracks_equal": checks.get("meta_tracks_equal"),
        "streams_equal": checks.get("stream_signature_equal"),
        "nrtm_equal": checks.get("nrtm_equal"),
        "tref_equal": checks.get("tref_cdsc_equal"),
        "brand_equal": checks.get("brand_equal"),
        "compat_equal": checks.get("compatible_brands_equal"),
        "top_level_content_equal": sorted(src_top) == sorted(dst_top),
        "top_level_order_same": src_top == dst_top,
        "top_src": src_top, "top_dst": dst_top,
        "elapsed_s": r.get("elapsed_s"),
    })
    return out


rows = [analyse(r) for r in raw]
print(f"{'机位':<14}{'GiB':>6}{'rtmd 样本':>10}{'×B':>7}{'ts':>7}"
      f"{'uuid':>5}{'nrtm':>5}{'brand':>6}{'mts':>7}"
      f"{'内容一致':>9}{'顶层内容':>9}{'顶层顺序':>9}")
def _v(v):
    return '-' if v is None else str(v)


for x in rows:
    head = (f"{x['label']:<14}{_v(x['size_gib']):>6}"
            f"{_v(x['rtmd_samples']):>10}{_v(x['rtmd_bytes']):>7}"
            f"{_v(x['rtmd_timescale']):>7}{_v(x['uuid_count']):>5}"
            f"{'Y' if x['nrtm'] else 'N':>5}{_v(x['brand']):>6}"
            f"{_v(x['movie_timescale']):>7}")
    if x['roundtrip'] != 'done':
        note = x.get('extract_error') or ''
        print(head + f"{'— 抽取-only':>12}  {note[:30]}")
        continue
    print(head + f"{'PASS' if x['content_equal'] else 'FAIL':>9}"
          f"{'一致' if x['top_level_content_equal'] else '不同':>9}"
          f"{'一致' if x['top_level_order_same'] else '不同':>9}")
    print(f"    rtmd refs={x['rtmd_refs']}  顶层源 {x['top_src']} → "
          f"成品 {x['top_dst']}"
          + (f"  [{x['provenance']}]" if x['provenance'] else ''))
(OUT / "sony_meta_crosscam_analysis.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n已写 {OUT / 'sony_meta_crosscam_analysis.json'}")
