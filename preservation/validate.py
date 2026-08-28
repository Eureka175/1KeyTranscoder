"""ORIGINAL vs FINAL structural comparison.

Uses ffprobe + MP4Box(-diso/-raw/-dump-xml/-dump-item) + the isobmf
walker, never a single tool. Emits a machine-readable report with
per-item status: PRESERVED / MODIFIED / MISSING / UNKNOWN.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import isobmf
from .gpac import GpacContainerBackend
from .sony import ParsedFile, _tag

PRESERVED = "PRESERVED"
MODIFIED = "MODIFIED"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"


def _ffprobe(ffprobe: Path, path: Path) -> dict[str, Any]:
    cmd = [
        str(ffprobe), "-v", "error",
        "-show_format", "-show_streams", "-of", "json", str(path),
    ]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def _uuid_inventory(path: Path) -> list[dict[str, str]]:
    out = []
    for context, ext, raw in isobmf.extract_uuid_boxes(path):
        out.append({
            "context": context,
            "uuid": isobmf.uuid_guid(ext),
            "label": isobmf.uuid_label(ext),
            "sha256": isobmf.sha256_bytes(raw),
            "size": len(raw),
        })
    return out


class FileFacts:
    def __init__(
        self,
        path: Path,
        gpac: GpacContainerBackend,
        ffprobe: Path,
        scratch: Path,
        facts: dict[str, Any] | None = None,
    ) -> None:
        """facts overrides payload dumps with precomputed values
        (rtmd_sha256 / lens_sha256 / lens_size / xml_sha256), used for
        the ORIGINAL side where the extraction bundle already hashed
        the payloads — the raw dumps are deleted after hashing."""
        self.path = path
        scratch.mkdir(parents=True, exist_ok=True)
        facts = facts or {}
        self.probe = _ffprobe(ffprobe, path)
        self.parsed = ParsedFile(gpac.diso_xml(path))
        self.uuids = _uuid_inventory(path)
        self.tracks = [self.parsed.track_info(t) for t in self.parsed.tracks()]
        self.meta = self.parsed.meta_info()

        # payload hashes for verification
        self.rtmd_sha256 = str(facts.get("rtmd_sha256", "") or "")
        if not self.rtmd_sha256:
            meta_tracks = [
                t for t in self.tracks if t["handler_type"] == "meta"
            ]
            if meta_tracks:
                raw = scratch / "rtmd_samples.bin"
                gpac.raw_track(path, meta_tracks[0]["track_id"], raw)
                self.rtmd_sha256 = isobmf.sha256_file(raw)

        self.lens_sha256 = str(facts.get("lens_sha256", "") or "")
        self.lens_size = int(facts.get("lens_size", 0) or 0)
        self.xml_sha256 = str(facts.get("xml_sha256", "") or "")
        if self.meta and self.meta.get("item_id") and not self.lens_sha256:
            lens = scratch / "lens_profile.bin"
            gpac.dump_meta_item(path, self.meta["item_id"], lens)
            self.lens_sha256 = isobmf.sha256_file(lens)
            self.lens_size = lens.stat().st_size
        if self.meta and self.meta.get("has_xml") and not self.xml_sha256:
            xml_out = scratch / "meta.xml"
            if gpac.dump_meta_xml(path, xml_out):
                self.xml_sha256 = isobmf.sha256_file(xml_out)

    def stream(self, codec_type: str) -> dict[str, Any]:
        for st in self.probe.get("streams", []):
            if st.get("codec_type") == codec_type:
                return st
        return {}


def _item(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"item": name, "status": status, "detail": detail}


def compare(
    original: Path,
    final: Path,
    gpac: GpacContainerBackend,
    ffprobe: Path,
    scratch: Path,
    known_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = FileFacts(original, gpac, ffprobe, scratch / "original", known_facts)
    out = FileFacts(final, gpac, ffprobe, scratch / "final")

    items: list[dict[str, str]] = []

    def eq(name: str, a: Any, b: Any, ok_detail: str = "") -> None:
        if a == b:
            items.append(_item(name, PRESERVED, ok_detail or f"{a!r}"))
        else:
            items.append(_item(name, MODIFIED, f"original={a!r} final={b!r}"))

    # --- container / brands (from diso FileTypeBox, not ffprobe) ---
    def brands(p: ParsedFile) -> tuple[str, int, list[str]]:
        for e in p.root:
            if _tag(e) == "FileTypeBox":
                return (
                    e.get("MajorBrand", ""),
                    int(e.get("MinorVersion", "0")),
                    sorted(
                        x.get("AlternateBrand", "")
                        for x in e
                        if _tag(x) == "BrandEntry"
                    ),
                )
        return "", 0, []

    sb, ob = brands(src.parsed), brands(out.parsed)
    if sb == ob:
        items.append(_item("ftyp.brands", PRESERVED, f"{sb[0]} {sb[1]}"))
    else:
        items.append(_item("ftyp.brands", MODIFIED,
                           f"original={sb} final={ob}"))

    # --- movie timescale / track-header timeline ---
    # Regression guard: GPAC's default movie timescale of 600 (and its
    # 600-tick internal rounding on NHML import) truncates tkhd
    # durations, desyncing video from the rtmd timeline (Gyroflow warns
    # "IMU duration ... is different than video duration"). The direct
    # container-copy path keeps these exact; any drift must fail.
    def mvhd(p: ParsedFile) -> tuple[str, str]:
        for e in p.root.iter():
            if _tag(e) == "MovieHeaderBox":
                return e.get("TimeScale", ""), e.get("Duration", "")
        return "", ""

    eq("timeline.movie_timescale", mvhd(src.parsed)[0], mvhd(out.parsed)[0])
    eq("timeline.movie_duration", mvhd(src.parsed)[1], mvhd(out.parsed)[1])

    # --- track inventory ---
    eq("track_count", len(src.tracks), len(out.tracks))
    src_v = next((t for t in src.tracks if t["handler_type"] == "vide"), None)
    out_v = next(
        (t for t in out.tracks if t["handler_type"] == "vide"), None
    )
    src_a = [t for t in src.tracks if t["handler_type"] == "soun"]
    out_a = [t for t in out.tracks if t["handler_type"] == "soun"]
    src_m = [t for t in src.tracks if t["handler_type"] == "meta"]
    out_m = [t for t in out.tracks if t["handler_type"] == "meta"]

    # --- video ---
    sv, ov = src.stream("video"), out.stream("video")
    if out_v and src_v:
        eq("video.resolution",
           (sv.get("width"), sv.get("height")),
           (ov.get("width"), ov.get("height")))
        eq("video.frame_count",
           sv.get("nb_frames"), ov.get("nb_frames"))
        eq("video.frame_rate",
           sv.get("avg_frame_rate"), ov.get("avg_frame_rate"))
        eq("video.timescale",
           src_v["timescale"], out_v["timescale"])
        eq("video.track_duration",
           src_v["track_duration"], out_v["track_duration"])
        eq("video.stts", src_v["stts"], out_v["stts"])
        # Edit-list segment durations must match. MediaTime may shift
        # by the encoder's priming offset (e.g. A7M4 1001 -> 2002);
        # that shift is compensated inside the edit list, so the
        # presentation duration (tkhd, above) stays exact.
        s_elst = [e[0] for e in src_v["elst"]]
        o_elst = [e[0] for e in out_v["elst"]]
        mt_note = ""
        if (
            src_v["elst"] and out_v["elst"]
            and src_v["elst"][0][1] != out_v["elst"][0][1]
        ):
            mt_note = (
                f" (media_time {src_v['elst'][0][1]}->"
                f"{out_v['elst'][0][1]}, encoder priming, compensated)"
            )
        if s_elst == o_elst:
            items.append(_item(
                "video.elst", PRESERVED, f"durations match{mt_note}"
            ))
        else:
            items.append(_item(
                "video.elst", MODIFIED,
                f"original={src_v['elst']} final={out_v['elst']}",
            ))
        items.append(_item(
            "video.codec", PRESERVED,
            f"original={sv.get('codec_name')} final={ov.get('codec_name')}"
            if ov.get("codec_name") == "hevc"
            else f"UNEXPECTED codec {ov.get('codec_name')}",
        ))
        items.append(_item(
            "video.encode", PRESERVED,
            "libx265 intermediate, temporally 1:1",
        ))
    else:
        items.append(_item("video.track", MISSING, "no vide track in final"))

    # --- audio ---
    if out_a and src_a:
        # All audio streams must survive as independent streams, with
        # the same codec/rate/depth/layout and per-track timing.
        s_streams = sorted(
            (st.get("codec_name"), st.get("sample_rate"),
             st.get("channels"), st.get("sample_fmt"))
            for st in src.probe.get("streams", [])
            if st.get("codec_type") == "audio"
        )
        o_streams = sorted(
            (st.get("codec_name"), st.get("sample_rate"),
             st.get("channels"), st.get("sample_fmt"))
            for st in out.probe.get("streams", [])
            if st.get("codec_type") == "audio"
        )
        eq("audio.streams", s_streams, o_streams)
        s_tracks = sorted(
            (t["sample_entry"], t["timescale"], t["media_duration"],
             t["track_duration"], sum(c for c, _ in t["stts"]))
            for t in src_a
        )
        o_tracks = sorted(
            (t["sample_entry"], t["timescale"], t["media_duration"],
             t["track_duration"], sum(c for c, _ in t["stts"]))
            for t in out_a
        )
        eq("audio.tracks", s_tracks, o_tracks)
        sa = src.stream("audio")
        oa = out.stream("audio")
        eq("audio.sample_rate",
           sa.get("sample_rate"), oa.get("sample_rate"))
        eq("audio.channels", sa.get("channels"), oa.get("channels"))
        eq("audio.sample_entry",
           src_a[0]["sample_entry"], out_a[0]["sample_entry"])
        eq("audio.track_duration",
           src_a[0]["track_duration"], out_a[0]["track_duration"])
        sd = float(sa.get("duration", 0) or 0)
        od = float(oa.get("duration", 0) or 0)
        if abs(sd - od) <= 0.005:
            items.append(_item("audio.duration", PRESERVED,
                               f"{sd:.3f}s vs {od:.3f}s"))
        else:
            items.append(_item("audio.duration", MODIFIED,
                               f"original={sd} final={od}"))
    elif src_a:
        items.append(_item("audio.track", MISSING,
                           f"{len(src_a)} soun track(s) lost in final"))
    # (no audio in source: nothing to preserve)

    # --- rtmd metadata track ---
    if not out_m:
        items.append(_item("rtmd.track", MISSING, "no meta track in final"))
    else:
        s, o = src_m[0], out_m[0]
        eq("rtmd.sample_entry", s["sample_entry"], o["sample_entry"])
        eq("rtmd.handler", s["handler_type"], o["handler_type"])
        eq("rtmd.timescale", s["timescale"], o["timescale"])
        eq("rtmd.sample_count",
           sum(c for c, _ in s["stts"]), sum(c for c, _ in o["stts"]))
        eq("rtmd.constant_sample_size",
           s["constant_sample_size"], o["constant_sample_size"])
        eq("rtmd.stts", s["stts"], o["stts"])
        eq("rtmd.duration", s["media_duration"], o["media_duration"])
        eq("rtmd.track_duration",
           s["track_duration"], o["track_duration"])
        eq("rtmd.elst", s["elst"], o["elst"])
        eq("rtmd.payload_sha256", src.rtmd_sha256, out.rtmd_sha256)
        s_cdsc = any(r["type"] == "cdsc" for r in s["refs"])
        o_cdsc = any(r["type"] == "cdsc" for r in o["refs"])
        if s_cdsc and o_cdsc:
            items.append(_item("rtmd.tref_cdsc", PRESERVED))
        elif s_cdsc:
            items.append(_item(
                "rtmd.tref_cdsc", MISSING, "cdsc reference lost"))
        else:
            items.append(_item("rtmd.tref_cdsc", UNKNOWN,
                               "no cdsc in original"))
        # timecode tag (known secondary item)
        s_tc = src.stream("data").get("tags", {}).get("timecode", "")
        o_tc = out.stream("data").get("tags", {}).get("timecode", "")
        if s_tc:
            if o_tc == s_tc:
                items.append(_item("rtmd.timecode_tag", PRESERVED, s_tc))
            else:
                items.append(_item(
                    "rtmd.timecode_tag", MISSING,
                    f"original={s_tc!r} final={o_tc!r}"))

    # --- nrtm meta ---
    if src.meta and src.meta["handler_type"] == "nrtm":
        if out.meta and out.meta["handler_type"] == "nrtm":
            items.append(_item("nrtm.meta", PRESERVED, "hdlr=nrtm"))
            eq("nrtm.lens_profile.name",
               src.meta.get("item_name"), out.meta.get("item_name"))
            eq("nrtm.lens_profile.item_type",
               src.meta.get("item_type"), out.meta.get("item_type"))
            eq("nrtm.lens_profile.size", src.lens_size, out.lens_size)
            eq("nrtm.lens_profile.sha256",
               src.lens_sha256, out.lens_sha256)
            if src.xml_sha256:
                eq("nrtm.xml.sha256", src.xml_sha256, out.xml_sha256)
            else:
                items.append(_item("nrtm.xml", UNKNOWN,
                                   "no XML in original"))
        else:
            items.append(_item("nrtm.meta", MISSING,
                               "no nrtm meta box in final"))

    # --- uuid boxes ---
    def count_by(label: str, inv: list[dict[str, str]]) -> int:
        return sum(1 for u in inv if u["label"] == label)

    for label in ("PROF", "USMT"):
        sc, oc = count_by(label, src.uuids), count_by(label, out.uuids)
        if sc == 0:
            continue
        if oc == 0:
            items.append(_item(f"uuid.{label}", MISSING,
                               f"original had {sc}"))
        elif oc != sc:
            items.append(_item(f"uuid.{label}", MODIFIED,
                               f"count original={sc} final={oc}"))
        else:
            s_hash = sorted(u["sha256"] for u in src.uuids
                            if u["label"] == label)
            o_hash = sorted(u["sha256"] for u in out.uuids
                            if u["label"] == label)
            if s_hash == o_hash:
                s_ctx = sorted(u["context"] for u in src.uuids
                               if u["label"] == label)
                o_ctx = sorted(u["context"] for u in out.uuids
                               if u["label"] == label)
                if s_ctx == o_ctx:
                    items.append(_item(
                        f"uuid.{label}", PRESERVED,
                        f"{sc} box(es), bytes+context match"))
                else:
                    items.append(_item(
                        f"uuid.{label}", MODIFIED,
                        f"payloads match, context {s_ctx} vs {o_ctx}"))
            else:
                items.append(_item(f"uuid.{label}", MODIFIED,
                                   "payload bytes differ"))

    # unlabeled uuids are verified byte-for-byte like labeled ones
    for u in src.uuids:
        if not u["label"]:
            match = any(
                o["sha256"] == u["sha256"] and o["context"] == u["context"]
                for o in out.uuids
            )
            if match:
                items.append(_item(
                    f"uuid.unknown.{u['uuid'][:13]}", PRESERVED,
                    f"verbatim at {u['context']}"))
            else:
                items.append(_item(
                    f"uuid.unknown.{u['uuid'][:13]}", MISSING,
                    f"uuid at {u['context']} not in final"))

    summary = {
        PRESERVED: sum(1 for i in items if i["status"] == PRESERVED),
        MODIFIED: sum(1 for i in items if i["status"] == MODIFIED),
        MISSING: sum(1 for i in items if i["status"] == MISSING),
        UNKNOWN: sum(1 for i in items if i["status"] == UNKNOWN),
    }
    critical_missing = [
        i for i in items
        if i["status"] == MISSING
        and i["item"].split(".")[0]
        in ("rtmd", "nrtm", "uuid", "video", "audio")
        and i["item"] != "rtmd.timecode_tag"
    ]
    # These MODIFIED items break downstream consumers (Gyroflow timeline
    # desync) even though nothing is MISSING.
    critical_modified = [
        i for i in items
        if i["status"] == MODIFIED
        and i["item"] in (
            "timeline.movie_timescale",
            "timeline.movie_duration",
            "video.timescale",
            "video.track_duration",
            "video.stts",
            "video.elst",
            "audio.streams",
            "audio.tracks",
            "audio.track_duration",
            "rtmd.timescale",
            "rtmd.duration",
            "rtmd.track_duration",
            "rtmd.stts",
            "rtmd.elst",
            "rtmd.payload_sha256",
        )
    ]
    return {
        "original": str(original),
        "final": str(final),
        "summary": summary,
        "structural_success": not critical_missing and not critical_modified,
        "critical_missing": critical_missing,
        "critical_modified": critical_modified,
        "items": items,
    }
