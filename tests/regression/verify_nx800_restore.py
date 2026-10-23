#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the NX800 (4-mono XAVC) metadata round trip that just completed.

The restore itself finished: E:\\1kt_scratch\\sony_meta_nx800\\HXR-NX800\\restored.MP4
exists (41.14 GiB). The cross-camera harness crashed *after* the restore, in its own
comparison code, because it read box dicts with the wrong key names -- so the
artefact was never verified. This runs the verification against the existing file
instead of paying another 2.5-hour restore.

Compares source vs restored, item by item:
    container_facts  -> top-level box set/order, ftyp brand + compatible brands,
                        tref/cdsc, nrtm, uuid multiset (uuid_inventory_equal)
    probe            -> every video/audio stream parameter
    sony.extract     -> bundle tracks (rtmd sample count/size/timescale/refs) and
                        private box hashes
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                       # noqa: E402
from preservation.gpac import GpacContainerBackend            # noqa: E402
from preservation.sony import SonyPreservationBackend         # noqa: E402

SRC = REPO / "data" / "_from_I" / "2026.09.24" / "HXR-NX800" / "CA0230.MP4"
DST = Path(r"E:\1kt_scratch\sony_meta_nx800\HXR-NX800\restored.MP4")
WORK = Path(r"E:\1kt_scratch\nx800_verify")
OUT = Path(r"E:\1kt_work\proto_long_take\nx800_verify.json")

STREAM_FIELDS = ["codec_type", "codec_name", "profile", "pix_fmt", "width",
                 "height", "r_frame_rate", "sample_rate", "channels",
                 "bits_per_raw_sample", "codec_tag_string"]


def sig(doc: dict) -> list[dict]:
    out = []
    for s in sorted(doc.get("streams") or [], key=lambda x: (x.get("codec_type") or "", x.get("index") or 0)):
        out.append({k: s.get(k) for k in STREAM_FIELDS})
    return out


def box_key(b: dict) -> tuple:
    return (b.get("label"), b.get("ctx") or b.get("parent_context"),
            b.get("ext") or b.get("extended_type"), b.get("sha256"))


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    result: dict = {"src": str(SRC), "dst": str(DST),
                    "src_exists": SRC.is_file(), "dst_exists": DST.is_file()}
    if not (SRC.is_file() and DST.is_file()):
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1
    result["dst_size"] = DST.stat().st_size
    result["src_size"] = SRC.stat().st_size
    gpac = GpacContainerBackend()
    sony = SonyPreservationBackend(gpac, None)

    bf, af = ar.container_facts(SRC), ar.container_facts(DST)
    uuid_cmp = ar.uuid_inventory_equal(bf, af)
    result["container"] = {
        "top_level_src": bf.get("top_level"),
        "top_level_dst": af.get("top_level"),
        "top_level_content_equal": sorted(bf.get("top_level") or [])
        == sorted(af.get("top_level") or []),
        "top_level_order_same": bf.get("top_level") == af.get("top_level"),
        "brand_src": bf.get("ftyp_major"), "brand_dst": af.get("ftyp_major"),
        "brand_equal": bf.get("ftyp_major") == af.get("ftyp_major"),
        "compat_equal": sorted(bf.get("ftyp_compatible") or [])
        == sorted(af.get("ftyp_compatible") or []),
        "tref_equal": bf.get("tref_cdsc") == af.get("tref_cdsc"),
        "nrtm_equal": bf.get("nrtm") == af.get("nrtm"),
        "uuid_equal": uuid_cmp["equal"],
        "uuid_missing": uuid_cmp["missing"],
        "uuid_added": uuid_cmp["added"],
        "uuid_count": [uuid_cmp["count_a"], uuid_cmp["count_b"]],
    }
    result["streams_equal"] = sig(ar.probe(SRC)) == sig(ar.probe(DST))
    result["streams_src"] = sig(ar.probe(SRC))
    result["streams_dst"] = sig(ar.probe(DST))

    try:
        b_src = sony.extract(SRC, WORK / "bundle_src")
        b_dst = sony.extract(DST, WORK / "bundle_dst")
        def tracks(b):
            return [{"handler": t.handler_type, "name": t.handler_name,
                     "entry": t.sample_entry_type, "timescale": t.timescale,
                     "duration": t.duration, "samples": t.sample_count,
                     "const_size": t.constant_sample_size,
                     "refs": t.track_refs, "tc": t.timecode_tag}
                    for t in b.tracks]
        result["tracks_equal"] = tracks(b_src) == tracks(b_dst)
        result["tracks_src"] = tracks(b_src)
        result["tracks_dst"] = tracks(b_dst)
        bs = sorted(box_key({"label": x.label, "ctx": x.parent_context,
                             "ext": x.extended_type, "sha256": x.sha256})
                    for x in b_src.boxes)
        bd = sorted(box_key({"label": x.label, "ctx": x.parent_context,
                             "ext": x.extended_type, "sha256": x.sha256})
                    for x in b_dst.boxes)
        result["boxes_equal"] = bs == bd
        result["boxes_count"] = [len(bs), len(bd)]
        result["boxes_missing"] = [x[0] for x in bs if x not in bd]
        result["boxes_added"] = [x[0] for x in bd if x not in bs]
        result["nrtm_bundle_equal"] = (
            (b_src.nrtm is None and b_dst.nrtm is None)
            or (b_src.nrtm is not None and b_dst.nrtm is not None
                and b_src.nrtm.lens_profile_sha256 == b_dst.nrtm.lens_profile_sha256
                and b_src.nrtm.xml_sha256 == b_dst.nrtm.xml_sha256))
    except Exception as exc:                        # noqa: BLE001
        result["bundle_error"] = f"{type(exc).__name__}: {exc}"

    content_keys = ["brand_equal", "compat_equal", "tref_equal", "nrtm_equal",
                    "uuid_equal"]
    content = all(result["container"].get(k) for k in content_keys)
    content = content and result.get("streams_equal", False)
    content = content and result.get("tracks_equal", False)
    content = content and result.get("boxes_equal", False)
    content = content and result.get("nrtm_bundle_equal", False)
    result["content_equal"] = bool(content)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    c = result["container"]
    print(f"src {result['src_size'] / 2**30:.2f} GiB -> dst {result['dst_size'] / 2**30:.2f} GiB")
    for k in ("brand_equal", "compat_equal", "tref_equal", "nrtm_equal",
              "uuid_equal", "top_level_content_equal", "top_level_order_same"):
        print(f"  {k}: {c.get(k)}")
    print(f"  uuid missing/added: {c.get('uuid_missing')} / {c.get('uuid_added')} "
          f"(count {c.get('uuid_count')})")
    print(f"  streams_equal: {result.get('streams_equal')}")
    print(f"  tracks_equal: {result.get('tracks_equal')} "
          f"(rtmd samples {((result.get('tracks_src') or [{}])[0]).get('samples')})")
    print(f"  boxes_equal: {result.get('boxes_equal')} count {result.get('boxes_count')} "
          f"missing {result.get('boxes_missing')} added {result.get('boxes_added')}")
    print(f"  nrtm_bundle_equal: {result.get('nrtm_bundle_equal')}")
    print(f"CONTENT_EQUAL: {result['content_equal']}")
    print(f"已写 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
