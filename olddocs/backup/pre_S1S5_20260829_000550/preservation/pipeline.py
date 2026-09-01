"""Sony production pipeline: extract -> encode -> rebuild -> validate.

This is the integrated Sony path used by 1keytransc.py. The video
encode is injected as a callback so this module stays independent of
the encoder backend (production: encoders/x265.py with the full
scaling machinery; the POC used libx265 ultrafast).

    original Sony MOV/MP4
        -> extract: preservation bundle (work_dir/metadata/)
        -> encode_video callback -> work_dir/video/encoded.mov
        -> MP4Box -new (video + per-track audio container copy
           + per-track rtmd container copy from the source)
        -> reconstruct: payload verification, tref/cdsc, nrtm meta,
           brands
        -> flatten, uuid byte patch (GPAC cannot write uuid boxes)
        -> final/output.mov -> validate.compare -> optional Gyroflow
        -> report.json

Timing strategy (GPAC-first, verified on GPAC 26.02):
- The rtmd and audio tracks are container-copied from the source
  (ISOBMFF -> ISOBMFF). Native track copy preserves the exact source
  timing (stts 1001/60000, elst, tkhd durations) with no drift.
- GPAC's NHML import path is deliberately NOT used for reconstruction:
  it recomputes every track's tkhd/elst durations at 600-tick
  precision (360360@60000 -> 360300) regardless of -timescale,
  :moovts or :timescale, which desyncs video from rtmd (the historical
  Gyroflow "IMU duration != video duration" failure).
- The source movie timescale is threaded through every mutating
  MP4Box command (GPAC resets it to the first track's media timescale
  otherwise; A7M4 needs 90000 while its video runs at 30000).
- isobmf.patch_track_durations() remains available as a Level-3
  fallback for GPAC versions with different import behavior; it is NOT
  part of the normal pipeline anymore.

Resume: an existing metadata/manifest.json is reused, a VALID
encoded.mov is reused (validated by ffprobe, so a partial file from an
interrupted encode is re-encoded, never trusted), and an existing
final/output.mov is handed back without rebuilding.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from . import isobmf
from .gpac import GpacContainerBackend
from .gyroflow import check as gyroflow_check
from .models import PreservationBundle
from .poc_video import CopyAudioBackend
from .sony import SonyPreservationBackend
from .validate import compare


def run_sony_pipeline(
    *,
    source: Path,
    work_dir: Path,
    encode_video: Callable[[Path, Path], None],
    gpac: GpacContainerBackend,
    ffprobe: Path,
    has_audio: bool = True,
    gyroflow: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)

    def step(msg: str) -> None:
        log(f"[{time.strftime('%H:%M:%S')}] {msg}")

    final = work_dir / "final" / "output.mov"
    report_path = work_dir / "report.json"
    if final.is_file() and final.stat().st_size > 0 and report_path.is_file():
        try:
            cached = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        # the report is written only after the file is fully built and
        # validated; a cached report from a FAILED run must not block a
        # retry, so rebuild unless the cached run actually passed
        if cached.get("structural_success") is True:
            step(f"resume: final output already rebuilt: {final}")
            return cached
        step("previous run did not pass validation; rebuilding")
        try:
            final.unlink()
        except OSError:
            pass

    sony = SonyPreservationBackend(gpac, ffprobe)

    # 1. extraction -> preservation bundle (resume-aware)
    bundle_dir = work_dir / "metadata"
    manifest = bundle_dir / "manifest.json"
    bundle = None
    if manifest.is_file():
        try:
            loaded = PreservationBundle.from_json(manifest)
            # a bundle is reusable only if it knows the movie timescale
            # and the per-track audio copy list (older POC manifests do
            # not, and #audio only copies the FIRST audio track)
            if (
                loaded.movie_timescale > 0
                and (not has_audio or loaded.audio_tracks)
            ):
                bundle = loaded
        except (TypeError, KeyError, ValueError):
            bundle = None
    if bundle is None:
        step("extracting metadata bundle (GPAC demux)...")
        bundle = sony.extract(source, bundle_dir)
    step(
        f"bundle: {len(bundle.tracks)} metadata track(s), "
        f"{len(bundle.audio_tracks)} audio track(s), "
        f"{len(bundle.boxes)} uuid box(es), "
        f"nrtm={'yes' if bundle.nrtm else 'no'}, "
        f"movie timescale {bundle.movie_timescale}, "
        f"video timescale {bundle.video_timescale}"
    )

    movie_timescale = bundle.movie_timescale or bundle.video_timescale
    if movie_timescale <= 0:
        raise RuntimeError(f"no movie timescale in bundle for {source}")
    # GPAC resets the movie timescale on every rewrite; thread the
    # source movie timescale through all mutating commands so track
    # durations are computed at full precision.
    gpac.movie_timescale = movie_timescale

    # 2. video encode (injected; production = scaled libx265 -> MOV).
    # MOV, not MKV: GPAC's MKV reader quantizes timestamps to
    # milliseconds and would break exact rtmd alignment.
    encoded_mov = work_dir / "video" / "encoded.mov"

    def _encoded_ok() -> bool:
        """A reusable intermediate must be a real file with >=1 video
        packet, not a partial artifact of an interrupted encode."""
        if not (encoded_mov.is_file() and encoded_mov.stat().st_size > 0):
            return False
        try:
            proc = subprocess.run(
                [str(ffprobe), "-v", "error", "-count_packets",
                 "-select_streams", "v:0", "-show_entries",
                 "stream=nb_read_packets", "-of", "json", str(encoded_mov)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", check=False,
            )
            if proc.returncode != 0:
                return False
            streams = json.loads(proc.stdout).get("streams", [])
            return bool(streams and int(
                streams[0].get("nb_read_packets") or 0
            ) > 0)
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    if not _encoded_ok():
        try:
            encoded_mov.unlink()
        except OSError:
            pass
        step("encoding video intermediate...")
        encode_video(source, encoded_mov)
        if not _encoded_ok():
            raise RuntimeError(
                f"video intermediate {encoded_mov} is not a readable "
                f"video file after encoding"
            )
    step(f"video ready: {encoded_mov}")

    # 3. audio: container-level per-track copy from source at mux time
    CopyAudioBackend().note(source, work_dir / "audio")

    # 4. GPAC reconstruction: ONE -new command carries the encoded
    # video, every source audio track, and every metadata track as
    # native ISOBMFF track copies (exact source timing, no NHML).
    stage = work_dir / "final" / "stage.mov"
    stage.parent.mkdir(parents=True, exist_ok=True)
    adds = [f"{encoded_mov}#video"]
    if has_audio:
        for at in bundle.audio_tracks:
            adds.append(f"{source}#{at.track_id}")
    for track in bundle.tracks:
        adds.append(f"{source}#{track.track_id}")
    step("muxing video+audio+metadata tracks with MP4Box (native copy)...")
    gpac.mux_new(stage, adds)

    step("verifying copied tracks / tref / nrtm meta...")
    sony.reconstruct(bundle, bundle_dir, stage)

    # moov must sit after mdat before byte-level uuid insertion
    gpac.flatten(stage)

    # brands AFTER flatten: -flat rewrites the file and resets ftyp
    # to GPAC's default (qt) otherwise
    if bundle.major_brand:
        compat = [
            b for b in bundle.compatible_brands
            if b and b != bundle.major_brand
        ]
        major = bundle.major_brand
        if bundle.brand_minor_version:
            major += f":{bundle.brand_minor_version}"
        gpac.set_brand(
            stage,
            major,
            compat,
            remove=[
                b for b in ("qt  ", "isom", "iso2")
                if b not in compat and b != bundle.major_brand
            ],
        )

    # 5. uuid byte patch. GPAC 26.02 cannot create vendor uuid boxes
    # (mp4box -hx uuid / gpac -hx uuid return nothing), so verbatim
    # uuid bytes are re-inserted by the narrow isobmf patch. GPAC
    # already carries some track-level uuid boxes over when importing
    # from the source, so insert only what's actually missing.
    step("inserting uuid boxes (ISO-BMFF patch)...")
    existing = {
        (ctx, isobmf.sha256_bytes(raw))
        for ctx, _, raw in isobmf.extract_uuid_boxes(stage)
    }
    inserts = [
        (ctx, data)
        for ctx, data in sony.uuid_inserts(bundle, bundle_dir)
        if (ctx, isobmf.sha256_bytes(data)) not in existing
    ]
    skipped = len(bundle.boxes) - len(inserts)
    if skipped:
        step(f"  {skipped} uuid box(es) already carried over by GPAC")
    isobmf.insert_uuid_boxes(stage, final, inserts)

    # 6. structural validation. Timing regression guards
    # (movie timescale/duration, track timescales/durations, stts,
    # rtmd elst, audio track count) live in validate.compare and make
    # the run FAIL on any drift. No duration patch is applied: the
    # direct-copy path is exact (see module docstring).
    step("validating original vs final...")
    report = compare(
        original=source,
        final=final,
        gpac=gpac,
        ffprobe=ffprobe,
        scratch=work_dir / "validate",
    )

    # 7. downstream consumer validation (hard acceptance test)
    if gyroflow is not None:
        step("Gyroflow headless validation...")
        try:
            report["gyroflow"] = gyroflow_check(
                original=source,
                final=final,
                gyroflow=gyroflow,
                scratch=work_dir / "validate",
            )
            step(
                f"gyroflow: {report['gyroflow']['status']} "
                f"({report['gyroflow']['detail']})"
            )
        except Exception as exc:
            report["gyroflow"] = {
                "status": "FAIL",
                "detail": f"Gyroflow validation error: {exc}",
            }
            step(f"gyroflow: FAIL ({exc})")

    report["job_dir"] = str(work_dir)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    s = report["summary"]
    step(
        f"done: PRESERVED={s['PRESERVED']} MODIFIED={s['MODIFIED']} "
        f"MISSING={s['MISSING']} UNKNOWN={s['UNKNOWN']} "
        f"structural_success={report['structural_success']}"
    )
    return report
