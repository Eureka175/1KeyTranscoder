"""Backend boundary protocols for the preservation POC.

These define the seams only. The concrete POC implementations are:

    VideoBackend                 = FFmpegUltrafastVideoBackend (poc_video.py)
    AudioBackend                 = CopyAudioBackend (poc_video.py)
    MetadataPreservationBackend  = SonyPreservationBackend (sony.py)
    ContainerBackend             = GpacContainerBackend (gpac.py)

DJI (or other cameras) can later add another MetadataPreservationBackend
without touching the video/audio/container sides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import PreservationBundle


class VideoBackend(Protocol):
    """Encode the source video into an intermediate container."""

    def encode(self, source: Path, out_mov: Path) -> None:
        """Encode source's main video to out_mov (MOV intermediate;
        NOT MKV: GPAC's MKV reader quantizes timestamps to
        milliseconds). Must be 1:1 in frame count and frame rate with
        the source."""
        ...


class AudioBackend(Protocol):
    """Provide audio for the final container. POC: plain copy."""

    def note(self, source: Path, dest_dir: Path) -> None:
        """Record what will be done with audio (no re-encode)."""
        ...


class MetadataPreservationBackend(Protocol):
    """Camera-specific metadata extraction and reconstruction."""

    def extract(self, source: Path, bundle_dir: Path) -> PreservationBundle:
        """Demux/inspect source and build a structured bundle on disk."""
        ...

    def reconstruct(self, bundle: PreservationBundle, stage_mov: Path) -> None:
        """Add preserved metadata structures into stage_mov (in place),
        except byte-level box insertions handled by the container layer."""
        ...


class ContainerBackend(Protocol):
    """Container-level mux/demux/inspection (GPAC/MP4Box in this POC)."""

    ...
