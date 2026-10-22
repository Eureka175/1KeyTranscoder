#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-analyse the cross-camera metadata run with order-insensitive checks.

The run's two failures are both ordering artefacts: the restored file carries the
same box *set* (uuid labels/contexts/sha256 identical, none missing or added) and
the same metadata, but GPAC writes the top-level boxes in a different order and
the bundle lists boxes in a different sequence. Box order is not metadata content,
so the honest verdict distinguishes:

    content_equal   -- multiset equality (what preservation must guarantee)
    order_same      -- byte-order equality (informational)

Reads the saved JSON only; no measurement, no encoding.
"""
from __future__ import annotations

import json
from pathlib import Path

P = Path(r"F:\1KeyTranscoder\data\sony_meta_crosscam\sony_meta_crosscam.json")
d = json.loads(P.read_text(encoding="utf-8"))


def key_box(b: dict) -> tuple:
    return (b.get("label"), b.get("ctx"), b.get("ext"), b.get("sha256"))


def analyse(r: dict) -> dict:
    out = {"label": r["label"], "size_gib": r.get("size_gib"),
           "rtmd": (r.get("bundle") or {}).get("n_tracks"),
           "uuid": (r.get("bundle") or {}).get("n_boxes"),
           "nrtm": bool((r.get("bundle") or {}).get("nrtm")),
           "brand": (r.get("bundle") or {}).get("major_brand"),
           "movie_ts": (r.get("bundle") or {}).get("movie_timescale")}
    if "checks" not in r:
        out["roundtrip"] = "skipped"
        out["extract_error"] = r.get("extract_error")
        out["roundtrip_error"] = r.get("roundtrip_error")
        return out
    src_boxes = {(b["label"], b["ctx"], b["ext"], b["sha256"])
                 for b in (r.get("bundle") or {}).get("boxes") or []}
    after = r.get("bundle_after") or {}
    dst_boxes = {(b["label"], b["ctx"], b["ext"], b["sha256"])
                 for b in after.get("boxes") or []}
    src_top = r.get("source_facts", {}).get("top_level") or []
    dst_top = r.get("after_facts", {}).get("top_level") or []
    out.update({
        "roundtrip": "done",
        "content_equal": all([
            r["checks"].get("brand_equal"),
            r["checks"].get("compatible_brands_equal"),
            r["checks"].get("tref_cdsc_equal"),
            r["checks"].get("nrtm_equal"),
            r["checks"].get("uuid_equal"),
            src_boxes == dst_boxes,
            r["checks"].get("meta_tracks_equal"),
            r["checks"].get("nrtm_bundle_equal"),
            r["checks"].get("stream_signature_equal"),
        ]),
        "uuid_equal": r["checks"].get("uuid_equal"),
        "boxes_content_equal": src_boxes == dst_boxes,
        "box_order_same": [b["sha256"] for b in
                           ((r.get("bundle") or {}).get("boxes") or [])]
        == [b["sha256"] for b in (after.get("boxes") or [])],
        "top_level_content_equal": sorted(src_top) == sorted(dst_top),
        "top_level_order_same": src_top == dst_top,
        "top_src": src_top, "top_dst": dst_top,
        "meta_tracks_equal": r["checks"].get("meta_tracks_equal"),
        "streams_equal": r["checks"].get("stream_signature_equal"),
        "rtmd_samples": ((r.get("bundle") or {}).get("tracks") or [{}])[0]
        .get("sample_count"),
        "rtmd_bytes": ((r.get("bundle") or {}).get("tracks") or [{}])[0]
        .get("constant_sample_size"),
        "rtmd_ts": ((r.get("bundle") or {}).get("tracks") or [{}])[0]
        .get("timescale"),
        "elapsed_s": r.get("elapsed_s"),
    })
    return out


rows = [analyse(r) for r in d]
print(f"{'机位':<14}{'GPIB':>6}{'rtmd':>6}{'uuid':>5}{'nrtm':>5}"
      f"{'brand':>6}{'mts':>7}{'内容一致':>9}{'box顺序':>8}{'top内容':>8}"
      f"{'top顺序':>8}")
for r in rows:
    if r.get("roundtrip") != "done":
        print(f"{r['label']:<14}{r['size_gib']:>6}{r['rtmd']:>6}{r['uuid']:>5}"
              f"{'Y' if r['nrtm'] else 'N':>5}{str(r.get('brand')):>6}"
              f"{str(r.get('movie_ts')):>7}   跳过(抽取-only)"
              f"{(' ' + (r.get('extract_error') or r.get('roundtrip_error') or ''))[:40]}")
        continue
    print(f"{r['label']:<14}{r['size_gib']:>6}{r['rtmd']:>6}{r['uuid']:>5}"
          f"{'Y' if r['nrtm'] else 'N':>5}{str(r['brand']):>6}{r['movie_ts']:>7}"
          f"{'PASS' if r['content_equal'] else 'FAIL':>9}"
          f"{'一致' if r['box_order_same'] else '不同':>8}"
          f"{'一致' if r['top_level_content_equal'] else '不同':>8}"
          f"{'一致' if r['top_level_order_same'] else '不同':>8}")
    if not r["content_equal"]:
        print("    uuid_equal", r["uuid_equal"],
              "boxes_content", r["boxes_content_equal"],
              "meta_tracks", r["meta_tracks_equal"],
              "streams", r["streams_equal"])
    print(f"    rtmd: {r['rtmd_samples']} samples × {r['rtmd_bytes']}B "
          f"ts={r['rtmd_ts']}  顶层源 {r['top_src']} → 成品 {r['top_dst']}")
    print(f"    用时 {r['elapsed_s']}s")
Path(P.parent / "sony_meta_crosscam_analysis.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n已写 {P.parent / 'sony_meta_crosscam_analysis.json'}")
