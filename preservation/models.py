"""Data models for the preservation bundle.

Everything serializes to plain JSON dicts so metadata/manifest.json is
self-describing and machine-checkable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BUNDLE_VERSION = 1


@dataclass
class MetadataTrack:
    """One timed metadata track (Sony rtmd in this POC)."""

    track_id: int
    handler_type: str            # e.g. "meta"
    handler_name: str            # e.g. "Timed Metadata Media Handler"
    sample_entry_type: str       # e.g. "rtmd"
    timescale: int               # e.g. 60000
    duration: int                # in timescale units
    sample_count: int
    constant_sample_size: int    # 0 if variable; Sony rtmd is 19456
    sample_sizes_file: str       # JSON list, relative to bundle dir
    stts: list[list[int]]        # run-length [[count, delta], ...]
    track_refs: list[dict[str, Any]]  # [{"type": "cdsc", "target_track_id": 1}]
    timecode_tag: str            # "" if none
    samples_file: str            # raw concatenated samples, relative path
    samples_sha256: str
    nhml_file: str               # reconstruction NHML, relative path
    nhml_media_file: str         # NHML baseMediaFile, relative path

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MetadataTrack":
        return cls(**d)


@dataclass
class PrivateBox:
    """A verbatim private box (Sony uuid PROF/USMT in this POC)."""

    box_type: str                # "uuid"
    extended_type: str           # GUID, e.g. "{50524F46-...}" (PROF)
    label: str                   # "PROF" / "USMT" / ""
    parent_context: str          # "root" | "moov" | "trak:vide" | ...
    ordinal: int                 # index among same context+type
    size: int                    # full box size incl. header
    payload_file: str            # whole box incl. header, relative path
    sha256: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PrivateBox":
        return cls(**d)


@dataclass
class AudioTrackInfo:
    """One source audio track (for container-level per-track copy)."""

    track_id: int
    handler_type: str            # "soun"
    timescale: int
    media_duration: int          # mdhd units
    track_duration: int          # tkhd units (movie timescale)
    sample_entry: str            # e.g. "ipcm" / "twos"
    sample_count: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AudioTrackInfo":
        return cls(**d)


@dataclass
class NrtmMeta:
    """File-level meta (Sony 'nrtm'): lens profile item + XML."""

    handler_type: str = "nrtm"
    item_id: int = 0
    item_name: str = ""
    item_mime: str = ""
    item_type: str = ""
    lens_profile_file: str = ""
    lens_profile_size: int = 0
    lens_profile_sha256: str = ""
    xml_file: str = ""
    xml_sha256: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NrtmMeta":
        return cls(**d)


@dataclass
class PreservationBundle:
    """Whole preservation bundle for one source file."""

    version: int
    source_path: str
    source_size: int
    source_sha256: str
    major_brand: str
    brand_minor_version: int
    compatible_brands: list[str]
    tracks: list[MetadataTrack] = field(default_factory=list)
    audio_tracks: list[AudioTrackInfo] = field(default_factory=list)
    boxes: list[PrivateBox] = field(default_factory=list)
    nrtm: NrtmMeta | None = None
    # Video track timescale of the source (e.g. 60000). Kept for
    # reference/diagnostics.
    video_timescale: int = 0
    # Movie (mvhd) timescale of the source container. NOT always equal
    # to the video track timescale: A7M5 uses 60000/60000, A7M4 uses
    # 90000/30000. The rebuilt container's movie timescale must match
    # this, or GPAC's default of 600 truncates track durations and the
    # video timeline desyncs from the rtmd timeline.
    movie_timescale: int = 0

    def to_json(self, path: Path) -> None:
        data = asdict(self)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def from_json(cls, path: Path) -> "PreservationBundle":
        d = json.loads(path.read_text(encoding="utf-8"))
        tracks = [MetadataTrack.from_dict(t) for t in d.pop("tracks", [])]
        audio_tracks = [
            AudioTrackInfo.from_dict(a) for a in d.pop("audio_tracks", [])
        ]
        boxes = [PrivateBox.from_dict(b) for b in d.pop("boxes", [])]
        nrtm_d = d.pop("nrtm", None)
        bundle = cls(**d)
        bundle.tracks = tracks
        bundle.audio_tracks = audio_tracks
        bundle.boxes = boxes
        bundle.nrtm = NrtmMeta.from_dict(nrtm_d) if nrtm_d else None
        return bundle
