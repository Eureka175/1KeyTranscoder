"""Sony metadata-preservation backend (POC).

Preserves, as structured container data (not generic key/value tags):

- rtmd timed metadata track: verbatim samples (raw + NHML pair), stsd
  sample entry type, timescale, stts/stsz timing, tref/cdsc association
- file-level meta (nrtm): Lens profile item + NonRealTimeMeta XML
- vendor uuid boxes: PROF x1 (root), USMT x4 (3 trak tails + moov tail)

KLV payloads stay opaque bytes; no reinterpretation.
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import isobmf
from .gpac import GpacContainerBackend
from .models import (
    BUNDLE_VERSION,
    MetadataTrack,
    NrtmMeta,
    PreservationBundle,
    PrivateBox,
)

NS = "{urn:mpeg:isobmff:schema:file:2016}"


def _tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1]


def _find_all(elem: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in elem.iter() if _tag(e) == name]


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    for e in elem:
        if _tag(e) == name:
            return e
    return None


class ParsedFile:
    """Parsed MP4Box -diso XML, convenience accessors."""

    def __init__(self, xml_text: str) -> None:
        # strip the default namespace for simpler matching
        self.root = ET.fromstring(xml_text.replace(NS, ""))

    def tracks(self) -> list[ET.Element]:
        moov = _find_child(self.root, "MovieBox")
        if moov is None:
            return []
        return [t for t in moov if _tag(t) == "TrackBox"]

    def track_info(self, trak: ET.Element) -> dict[str, Any]:
        tkhd = _find_all(trak, "TrackHeaderBox")[0]
        mdhd = _find_all(trak, "MediaHeaderBox")[0]
        hdlr = _find_all(trak, "HandlerBox")[0]
        # stsd's first child box is the sample entry, whatever its kind
        # (rtmd -> SampleDescriptionEntryBox, hvc1 -> HEVCSampleEntryBox,
        # twos -> AudioSampleDescriptionBox, ...)
        stsd_entry = ""
        stsd = _find_all(trak, "SampleDescriptionBox")
        if stsd:
            for child in stsd[0]:
                if child.get("Type"):
                    stsd_entry = child.get("Type", "")
                    break
        refs = []
        for tref in _find_all(trak, "TrackReferenceTypeBox"):
            targets = [
                int(e.get("TrackID", "0"))
                for e in _find_all(tref, "TrackReferenceEntry")
            ]
            refs.append({"type": tref.get("Type", ""), "targets": targets})
        stts = [
            [int(e.get("SampleCount", "0")), int(e.get("SampleDelta", "0"))]
            for e in _find_all(trak, "TimeToSampleEntry")
        ]
        stsz = _find_all(trak, "SampleSizeBox")
        const_size = 0
        sizes: list[int] = []
        if stsz:
            const_size = int(stsz[0].get("ConstantSampleSize", "0"))
            sizes = [
                int(e.get("Size", "0"))
                for e in _find_all(stsz[0], "SampleSizeEntry")
            ]
        return {
            "track_id": int(tkhd.get("TrackID", "0")),
            "track_duration": int(tkhd.get("Duration", "0")),
            "timescale": int(mdhd.get("TimeScale", "0")),
            "media_duration": int(mdhd.get("Duration", "0")),
            "handler_type": hdlr.get("hdlrType", ""),
            "handler_name": hdlr.get("Name", ""),
            "sample_entry": stsd_entry,
            "stts": stts,
            "constant_sample_size": const_size,
            "sample_sizes": sizes,
            "refs": refs,
        }

    def meta_box(self) -> ET.Element | None:
        return _find_child(self.root, "MetaBox")

    def meta_info(self) -> dict[str, Any] | None:
        meta = self.meta_box()
        if meta is None:
            return None
        hdlr = _find_child(meta, "HandlerBox")
        infe = _find_all(meta, "ItemInfoEntryBox")
        xml_box = _find_child(meta, "XMLBox")
        item = {}
        if infe:
            item = {
                "item_id": int(infe[0].get("item_ID", "0")),
                "item_name": infe[0].get("item_name", ""),
                "item_mime": infe[0].get("content_type", ""),
                "item_type": infe[0].get("item_type", ""),
            }
        return {
            "handler_type": hdlr.get("hdlrType", "") if hdlr is not None else "",
            "handler_name": hdlr.get("Name", "") if hdlr is not None else "",
            "has_xml": xml_box is not None,
            **item,
        }


def _ffprobe_stream_tags(ffprobe: Path, src: Path) -> list[dict[str, str]]:
    cmd = [
        str(ffprobe), "-v", "error", "-show_streams", "-of", "json",
        str(src),
    ]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [st.get("tags", {}) for st in payload.get("streams", [])]


def _fix_nhml_last_duration(nhml: Path, last_delta: int) -> None:
    """Add duration="<last stts delta>" to the last NHNTSample."""
    if last_delta <= 0:
        return
    text = nhml.read_text(encoding="utf-8")
    idx = text.rfind("<NHNTSample")
    if idx < 0:
        return
    end = text.find(">", idx)
    if end < 0:
        return
    if "duration=" in text[idx:end]:
        return
    text = text[:end] + f' duration="{last_delta}"' + text[end:]
    nhml.write_text(text, encoding="utf-8")


class SonyPreservationBackend:
    """MetadataPreservationBackend implementation for Sony XAVC files."""

    def __init__(
        self,
        gpac: GpacContainerBackend,
        ffprobe: Path | None = None,
    ) -> None:
        self.gpac = gpac
        self.ffprobe = ffprobe

    # ------------------------------------------------------------------

    def extract(self, source: Path, bundle_dir: Path) -> PreservationBundle:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        tracks_dir = bundle_dir / "tracks"
        boxes_dir = bundle_dir / "boxes"

        parsed = ParsedFile(self.gpac.diso_xml(source))

        # brand info
        ftyp = _find_child(parsed.root, "FileTypeBox")
        major = ftyp.get("MajorBrand", "") if ftyp is not None else ""
        minor = int(ftyp.get("MinorVersion", "0")) if ftyp is not None else 0
        compat = []
        if ftyp is not None:
            compat = [
                e.get("AlternateBrand", "")
                for e in _find_all(ftyp, "BrandEntry")
            ]

        # stream tags (timecode lives on the rtmd stream)
        stream_tags: list[dict[str, str]] = []
        if self.ffprobe:
            stream_tags = _ffprobe_stream_tags(self.ffprobe, source)

        tracks: list[MetadataTrack] = []
        for trak in parsed.tracks():
            info = parsed.track_info(trak)
            if info["handler_type"] != "meta" or not info["sample_entry"]:
                continue
            entry = info["sample_entry"]
            tdir = tracks_dir / entry
            tdir.mkdir(parents=True, exist_ok=True)

            samples_bin = tdir / "samples.bin"
            self.gpac.raw_track(source, info["track_id"], samples_bin)
            nhml, media = self.gpac.nhml_dump(
                source, info["track_id"], tdir, entry
            )
            # The NHML dump carries per-sample DTS but no duration for
            # the last sample; GPAC then imports it with delta 0 and the
            # track comes out one frame short. Pin it from the stts.
            _fix_nhml_last_duration(
                nhml, info["stts"][-1][1] if info["stts"] else 0
            )

            sizes = info["sample_sizes"]
            if not sizes and info["constant_sample_size"]:
                sizes = [info["constant_sample_size"]] * sum(
                    c for c, _ in info["stts"]
                )
            sizes_file = tdir / "sample_sizes.json"
            sizes_file.write_text(json.dumps(sizes), encoding="utf-8")

            sample_count = len(sizes)
            if samples_bin.stat().st_size != sum(sizes):
                raise RuntimeError(
                    f"raw dump size {samples_bin.stat().st_size} != "
                    f"sum(stsz) {sum(sizes)} for track {info['track_id']}"
                )

            # map stream tags by handler name (timecode lives here)
            tc_tag = ""
            data_tags = [
                t for t in stream_tags
                if t.get("handler_name") == info["handler_name"]
                or "timecode" in t
            ]
            if data_tags:
                tc_tag = data_tags[0].get("timecode", "")

            track = MetadataTrack(
                track_id=info["track_id"],
                handler_type=info["handler_type"],
                handler_name=info["handler_name"],
                sample_entry_type=entry,
                timescale=info["timescale"],
                duration=info["media_duration"],
                sample_count=sample_count,
                constant_sample_size=info["constant_sample_size"],
                sample_sizes_file=str(
                    sizes_file.relative_to(bundle_dir)
                ),
                stts=info["stts"],
                track_refs=[
                    {"type": r["type"], "target_track_id": t}
                    for r in info["refs"]
                    for t in r["targets"]
                ],
                timecode_tag=tc_tag,
                samples_file=str(samples_bin.relative_to(bundle_dir)),
                samples_sha256=isobmf.sha256_file(samples_bin),
                nhml_file=str(nhml.relative_to(bundle_dir)),
                nhml_media_file=str(media.relative_to(bundle_dir)),
            )
            (tdir / "track.json").write_text(
                json.dumps(
                    {k: v for k, v in track.__dict__.items()},
                    indent=2, ensure_ascii=False,
                ) + "\n",
                encoding="utf-8",
            )
            tracks.append(track)

        # uuid boxes (verbatim)
        boxes_dir.mkdir(parents=True, exist_ok=True)
        boxes: list[PrivateBox] = []
        ordinals: dict[tuple[str, str], int] = {}
        for context, ext, raw in isobmf.extract_uuid_boxes(source):
            label = isobmf.uuid_label(ext)
            guid = isobmf.uuid_guid(ext)
            ctx_safe = context.replace(":", "_")
            key = (context, guid)
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            fname = f"uuid_{ctx_safe}_{label or guid[1:9]}_{ordinal}.bin"
            (boxes_dir / fname).write_bytes(raw)
            boxes.append(PrivateBox(
                box_type="uuid",
                extended_type=guid,
                label=label,
                parent_context=context,
                ordinal=ordinal,
                size=len(raw),
                payload_file=f"boxes/{fname}",
                sha256=isobmf.sha256_bytes(raw),
            ))

        # file-level meta (nrtm)
        nrtm = None
        meta_info = parsed.meta_info()
        if meta_info and meta_info["handler_type"] == "nrtm":
            nrtm = NrtmMeta(
                handler_type="nrtm",
                item_id=meta_info["item_id"],
                item_name=meta_info["item_name"],
                item_mime=meta_info["item_mime"],
                item_type=meta_info["item_type"],
            )
            lens = boxes_dir / "lens_profile.bin"
            self.gpac.dump_meta_item(source, meta_info["item_id"], lens)
            nrtm.lens_profile_file = str(lens.relative_to(bundle_dir))
            nrtm.lens_profile_size = lens.stat().st_size
            nrtm.lens_profile_sha256 = isobmf.sha256_file(lens)
            xml_out = boxes_dir / "nrtm.xml"
            if self.gpac.dump_meta_xml(source, xml_out):
                nrtm.xml_file = str(xml_out.relative_to(bundle_dir))
                nrtm.xml_sha256 = isobmf.sha256_file(xml_out)

        bundle = PreservationBundle(
            version=BUNDLE_VERSION,
            source_path=str(source),
            source_size=source.stat().st_size,
            source_sha256="",  # filled by caller (large file, optional)
            major_brand=major,
            brand_minor_version=minor,
            compatible_brands=compat,
            tracks=tracks,
            boxes=boxes,
            nrtm=nrtm,
        )
        bundle.to_json(bundle_dir / "manifest.json")
        return bundle

    # ------------------------------------------------------------------

    def reconstruct(
        self,
        bundle: PreservationBundle,
        bundle_dir: Path,
        stage_mov: Path,
    ) -> None:
        """MP4Box-level reconstruction into stage_mov (in place).

        uuid byte-patching is NOT done here (it is a raw byte operation);
        call uuid_inserts() and apply with isobmf.insert_uuid_boxes().
        """
        for track in bundle.tracks:
            nhml = bundle_dir / track.nhml_file
            spec = str(nhml)
            if track.handler_name:
                spec += f":name={track.handler_name}"
            self.gpac.add_track(stage_mov, spec)

        # tref/cdsc: rtmd track -> video track. Track IDs may have
        # changed; resolve from the rebuilt file.
        rebuilt = ParsedFile(self.gpac.diso_xml(stage_mov))
        video_id = 0
        meta_ids: list[int] = []
        for trak in rebuilt.tracks():
            info = rebuilt.track_info(trak)
            if info["handler_type"] == "vide" and not video_id:
                video_id = info["track_id"]
            if info["handler_type"] == "meta":
                meta_ids.append(info["track_id"])
        if not video_id:
            raise RuntimeError(f"no video track in {stage_mov}")
        for meta_id in meta_ids:
            self.gpac.add_track_ref(stage_mov, meta_id, "cdsc", video_id)

        # file-level meta (nrtm)
        if bundle.nrtm:
            self.gpac.set_meta(stage_mov, bundle.nrtm.handler_type)
            if bundle.nrtm.lens_profile_file:
                self.gpac.add_meta_item(
                    stage_mov,
                    bundle_dir / bundle.nrtm.lens_profile_file,
                    bundle.nrtm.item_name,
                    bundle.nrtm.item_mime,
                    bundle.nrtm.item_id,
                )
            if bundle.nrtm.xml_file:
                self.gpac.set_meta_xml(
                    stage_mov, bundle_dir / bundle.nrtm.xml_file
                )

    # ------------------------------------------------------------------

    def uuid_inserts(
        self, bundle: PreservationBundle, bundle_dir: Path
    ) -> list[tuple[str, bytes]]:
        """Verbatim uuid payloads in original (context, ordinal) order."""
        inserts = []
        for box in sorted(
            bundle.boxes, key=lambda b: (b.parent_context, b.ordinal)
        ):
            inserts.append(
                (
                    box.parent_context,
                    (bundle_dir / box.payload_file).read_bytes(),
                )
            )
        return inserts
