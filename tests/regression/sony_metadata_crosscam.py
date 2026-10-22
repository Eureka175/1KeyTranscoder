#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sony 元数据保留 —— 跨机位验证（A7M5 之外）。

验证的是程序**已有**的保留管线，不新写实现：

    preservation.sony.SonyPreservationBackend.extract/reconstruct   （bundle）
    preservation.transparent_restore.restore_after_transparent_remux（恢复步骤）
    preservation.gpac.GpacContainerBackend                          （mux/brand/uuid）
    archive_repair.container_facts / uuid_inventory_equal           （逐项比对）

对每个素材做两件事：

  A. **抽取**：bundle 里到底有没有元数据 —— rtmd 轨（sample_count/constant_size）、
     私有 uuid box（label + sha256）、nrtm lens profile（sha256）、brand 与
     movie timescale。
  B. **往返**（可选，整文件 stream copy 到 E:）：用源自身作为所有轨的输入重建容器，
     再调用 `restore_after_transparent_remux` 做恢复，然后**逐项比对**源与成品：
     brand/兼容 brand、tref/cdsc、nrtm、uuid 多重集（ctx+guid+sha）、元数据轨
     （handler/sample_entry/timescale/duration/sample_count/constant_sample_size/
     track_refs/timecode）、视频与音频流参数、顶层 box 列表。

任何一项不相等即判 FAIL 并列出具体差异 —— 不做"看起来一样"的结论。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                                   # noqa: E402
from preservation.gpac import GpacContainerBackend            # noqa: E402
from preservation.sony import SonyPreservationBackend         # noqa: E402
from preservation.transparent_restore import (                # noqa: E402
    restore_after_transparent_remux,
)

OUT = Path(r"E:\1kt_work\sony_meta_crosscam")
WORK = OUT / "work"

CASES = [
    # label, path, roundtrip?
    ("A7M5(control)", r"F:\1KeyTranscoder\data\align_4ch_proof\20260903_C1159.MP4", True),
    ("A7M4", r"I:\2026.09.24\A7M4\C0147.MP4", True),
    ("FX3", r"I:\2026.09.24\FX3\C0513.MP4", True),
    ("FX30", r"I:\2026.09.24\FX30\20260922_fx30_2_9757.MP4", True),
    ("HXR-NX800", r"I:\2026.09.24\HXR-NX800\CA0230.MP4", False),
    ("Mavic4P(DJI)", r"I:\2026.09.24\Mavic4P\DJI_20260921164928_0296_D.MP4", False),
]

STREAM_FIELDS = [
    ("video", "codec"), ("video", "profile"), ("video", "pix_fmt"),
    ("video", "width"), ("video", "height"), ("video", "r_frame_rate"),
    ("audio", "codec"), ("audio", "channels"), ("audio", "sample_rate"),
    ("audio", "codec_tag_string"), ("audio", "bits_per_raw_sample"),
]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sig(streams: list[dict]) -> dict:
    """抽取用于比对的流参数（跳过必然重编号的 index/bit_rate）。"""
    out: dict = {"video": [], "audio": []}
    for s in streams:
        kind = s.get("codec_type")
        if kind not in out:
            continue
        out[kind].append({k: s.get(k) for _, k in STREAM_FIELDS
                          if _ == kind})
    return out


def _track_key(tr) -> dict:
    return {
        "handler_type": tr.handler_type,
        "handler_name": tr.handler_name,
        "sample_entry_type": tr.sample_entry_type,
        "timescale": tr.timescale,
        "duration": tr.duration,
        "sample_count": tr.sample_count,
        "constant_sample_size": tr.constant_sample_size,
        "stts": tr.stts,
        "track_refs": tr.track_refs,
        "timecode_tag": tr.timecode_tag,
    }


def inspect_bundle(bundle) -> dict:
    return {
        "major_brand": bundle.major_brand,
        "brand_minor_version": bundle.brand_minor_version,
        "compatible_brands": list(bundle.compatible_brands),
        "movie_timescale": bundle.movie_timescale,
        "video_timescale": bundle.video_timescale,
        "n_tracks": len(bundle.tracks),
        "tracks": [_track_key(t) for t in bundle.tracks],
        "n_boxes": len(bundle.boxes),
        "boxes": [{"label": b.label, "ctx": b.parent_context,
                   "ext": b.extended_type, "sha256": b.sha256}
                  for b in bundle.boxes],
        "nrtm": None if bundle.nrtm is None else {
            "item_id": bundle.nrtm.item_id,
            "item_name": bundle.nrtm.item_name,
            "item_type": bundle.nrtm.item_type,
            "lens_sha256": bundle.nrtm.lens_profile_sha256,
            "lens_size": bundle.nrtm.lens_profile_size,
            "xml_sha256": bundle.nrtm.xml_sha256,
        },
    }


def run_case(label: str, src: Path, roundtrip: bool, gpac: GpacContainerBackend,
             out: Path, work_root: Path) -> dict:
    t0 = time.time()
    work = work_root / label.replace("(", "_").replace(")", "")
    work.mkdir(parents=True, exist_ok=True)
    res: dict = {"label": label, "source": src.name,
                 "size_gib": round(src.stat().st_size / 2**30, 3)}
    sony = SonyPreservationBackend(gpac, None)
    before_facts = ar.container_facts(src)
    # A. 抽取
    try:
        bundle = sony.extract(src, work / "bundle")
    except Exception as exc:                        # noqa: BLE001
        res["extract_error"] = f"{type(exc).__name__}: {exc}"
        return res
    res["bundle"] = inspect_bundle(bundle)
    res["source_facts"] = {
        "top_level": before_facts.get("top_level"),
        "ftyp_major": before_facts.get("ftyp_major"),
        "ftyp_compatible": before_facts.get("ftyp_compatible"),
        "tref_cdsc": before_facts.get("tref_cdsc"),
        "nrtm": before_facts.get("nrtm"),
        "uuid_count": len(before_facts.get("uuid") or []),
        "moov_first": before_facts.get("moov_first"),
    }
    if not roundtrip:
        res["roundtrip"] = {"skipped": "extraction-only (源过大或非 Sony)"}
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res
    # B. 往返: 源自身作为每条轨的输入 (video/data 引用源轨, audio 也引用源轨),
    #    再走生产恢复步骤。
    dst = work / f"restored{src.suffix}"
    if dst.exists():
        dst.unlink()
    try:
        movie_ts, tracks = gpac.parse_info(gpac.info(src))
        adds: list[str] = []
        for t in sorted(tracks, key=lambda x: x.get("number", 0)):
            h = t.get("handler")
            adds.append(f"{src}#video" if h == "vide" else f"{src}#{t['id']}")
        if bundle.movie_timescale:
            gpac.movie_timescale = int(bundle.movie_timescale)
        logs: list[str] = []
        restore_after_transparent_remux(
            src, adds, dst, work / "restore", gpac, logs.append)
        res["restore_logs"] = logs[-10:]
    except Exception as exc:                        # noqa: BLE001
        res["roundtrip_error"] = f"{type(exc).__name__}: {exc}"
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res
    if not dst.is_file():
        res["roundtrip_error"] = "输出未生成"
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res
    # C. 逐项比对
    after_facts = ar.container_facts(dst)
    uuid_cmp = ar.uuid_inventory_equal(before_facts, after_facts)
    after_doc = ar.probe(dst)
    src_doc = ar.probe(src)
    try:
        after_bundle = sony.extract(dst, work / "bundle_after")
        after_inspect = inspect_bundle(after_bundle)
    except Exception as exc:                        # noqa: BLE001
        after_inspect = {"error": f"{type(exc).__name__}: {exc}"}
    checks = {
        "brand_equal": before_facts.get("ftyp_major")
        == after_facts.get("ftyp_major"),
        "compatible_brands_equal": sorted(before_facts.get("ftyp_compatible") or [])
        == sorted(after_facts.get("ftyp_compatible") or []),
        "tref_cdsc_equal": before_facts.get("tref_cdsc")
        == after_facts.get("tref_cdsc"),
        "nrtm_equal": before_facts.get("nrtm") == after_facts.get("nrtm"),
        "uuid_equal": uuid_cmp["equal"],
        "top_level_equal": before_facts.get("top_level")
        == after_facts.get("top_level"),
        "meta_tracks_equal": res["bundle"]["tracks"]
        == (after_inspect.get("tracks") if "tracks" in after_inspect else None),
        "boxes_equal": res["bundle"]["boxes"]
        == (after_inspect.get("boxes") if "boxes" in after_inspect else None),
        "nrtm_bundle_equal": res["bundle"]["nrtm"]
        == after_inspect.get("nrtm"),
        "stream_signature_equal": _sig(src_doc.get("streams") or [])
        == _sig(after_doc.get("streams") or []),
    }
    res["checks"] = checks
    res["all_pass"] = all(checks.values())
    res["uuid_diff"] = {"missing": uuid_cmp["missing"],
                        "added": uuid_cmp["added"],
                        "count_src": uuid_cmp["count_a"],
                        "count_dst": uuid_cmp["count_b"]}
    res["after_facts"] = {
        "top_level": after_facts.get("top_level"),
        "ftyp_major": after_facts.get("ftyp_major"),
        "ftyp_compatible": after_facts.get("ftyp_compatible"),
        "tref_cdsc": after_facts.get("tref_cdsc"),
        "nrtm": after_facts.get("nrtm"),
        "uuid_count": len(after_facts.get("uuid") or []),
    }
    res["streams_src"] = _sig(src_doc.get("streams") or [])
    res["streams_dst"] = _sig(after_doc.get("streams") or [])
    res["dst_size"] = dst.stat().st_size
    res["same_bytes"] = _sha(src) == _sha(dst) if dst.stat().st_size == src.stat().st_size else False
    try:
        dst.unlink()
    except OSError:
        pass
    res["elapsed_s"] = round(time.time() - t0, 1)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Sony 元数据保留跨机位验证")
    ap.add_argument("--out", default=r"E:\1kt_work\sony_meta_crosscam")
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args()
    OUT = Path(args.out)
    WORK = OUT / "work"
    WORK.mkdir(parents=True, exist_ok=True)
    gpac = GpacContainerBackend()
    results = []
    for label, path, roundtrip in CASES:
        if args.only and label not in args.only:
            continue
        src = Path(path)
        if not src.is_file():
            print(f"缺文件: {src}")
            continue
        print(f"\n=== {label}: {src.name} "
              f"({src.stat().st_size / 2**30:.2f} GiB)")
        r = run_case(label, src, roundtrip, gpac, OUT, WORK)
        results.append(r)
        b = r.get("bundle") or {}
        if "extract_error" in r:
            print(f"  抽取失败: {r['extract_error']}")
        else:
            print(f"  抽取: 元数据轨 {b.get('n_tracks')} 条, "
                  f"uuid {b.get('n_boxes')} 个, nrtm="
                  f"{'有' if b.get('nrtm') else '无'}, brand="
                  f"{b.get('major_brand')}, movie_ts="
                  f"{b.get('movie_timescale')}")
            for t in b.get("tracks") or []:
                print(f"    rtmd: {t['handler_name']!r} "
                      f"{t['sample_count']} samples × "
                      f"{t['constant_sample_size']}B ts={t['timescale']} "
                      f"refs={t['track_refs']} tc={t['timecode_tag']}")
        if "roundtrip" in r:
            print(f"  往返: 跳过（{r['roundtrip']['skipped']}）")
        elif "checks" in r:
            for k, v in r["checks"].items():
                print(f"    {'PASS' if v else 'FAIL'}  {k}")
            print(f"  {'ALL PASS' if r['all_pass'] else 'HAS FAILURES'} "
                  f"({r['elapsed_s']}s, 输出 {r['dst_size'] / 2**30:.2f} GiB)")
            if not r["all_pass"]:
                print(f"    uuid diff: {r['uuid_diff']}")
                if not r["checks"]["stream_signature_equal"]:
                    print(f"    src streams: {r['streams_src']}")
                    print(f"    dst streams: {r['streams_dst']}")
        else:
            print(f"  往返失败: {r.get('roundtrip_error')}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sony_meta_crosscam.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'sony_meta_crosscam.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
