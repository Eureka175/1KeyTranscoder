"""Sony metadata-preservation POC orchestrator.

    original Sony MOV/MP4
        -> GPAC demux/inspection (diso/raw/nhml/meta dumps)
        -> preservation bundle (work/<job>/metadata/)
        -> FFmpeg libx265 ultrafast -> video/encoded.mkv
        -> audio: container-level copy from source
        -> GPAC reconstruction (tracks, tref, nrtm meta, brands)
        -> ISO-BMFF uuid byte patch -> final/output.mov
        -> validation report (report.json)

Usage:
    python sony_poc.py --source "testsets/adjust/车内高晃动适中噪点.MP4"
    python sony_poc.py --all          # all Sony originals in testsets/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core.probe import probe_source  # noqa: E402
from preservation import isobmf  # noqa: E402
from preservation.gpac import GpacContainerBackend  # noqa: E402
from preservation.models import PreservationBundle  # noqa: E402
from preservation.poc_video import (  # noqa: E402
    CopyAudioBackend,
    FFmpegUltrafastVideoBackend,
)
from preservation.sony import SonyPreservationBackend  # noqa: E402
from preservation.validate import compare  # noqa: E402

SONY_EXCLUDE = re.compile(r"dji", re.IGNORECASE)


def job_id_for(source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^\w.-]+", "_", source.stem)
    return f"{stem}-{digest}"


def sony_sources(root: Path) -> list[Path]:
    out = []
    for sub in ("adjust", "stress", "validate"):
        d = root / "testsets" / sub
        if d.is_dir():
            for f in sorted(d.glob("*.MP4")):
                if not SONY_EXCLUDE.search(f.name):
                    out.append(f)
    return out


def run_job(
    source: Path,
    work_root: Path,
    gpac: GpacContainerBackend,
    ffmpeg: Path,
    ffprobe: Path,
) -> dict:
    job_dir = work_root / job_id_for(source)
    job_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    def step(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        log.append(line)
        print(line, flush=True)

    step(f"source: {source}")
    step(f"job dir: {job_dir}")

    # 1. source manifest (ffprobe, unchanged probe module)
    summary, streams = probe_source(ffprobe, source)
    (job_dir / "source_manifest.json").write_text(
        json.dumps(
            {"summary": summary, "streams": streams},
            indent=2, ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    step(
        f"probed: {summary['width']}x{summary['height']} "
        f"{summary['fps']:.3f} fps, {summary['total_frames']} frames, "
        f"{summary['stream_count']} streams"
    )

    sony = SonyPreservationBackend(gpac, ffprobe)

    # 2. extraction -> preservation bundle
    bundle_dir = job_dir / "metadata"
    step("extracting metadata bundle (GPAC demux)...")
    bundle = sony.extract(source, bundle_dir)
    step(
        f"bundle: {len(bundle.tracks)} metadata track(s), "
        f"{len(bundle.boxes)} uuid box(es), "
        f"nrtm={'yes' if bundle.nrtm else 'no'}"
    )
    for t in bundle.tracks:
        step(
            f"  track {t.track_id} [{t.handler_type}/{t.sample_entry_type}] "
            f"{t.sample_count} samples x {t.constant_sample_size} B, "
            f"timescale {t.timescale}, refs={t.track_refs}"
        )

    # 3. video: x265 ultrafast. Containers are NOT dogma here: the
    # mux-path intermediate is MOV (GPAC quantizes MKV timestamps to
    # milliseconds, which would break exact rtmd alignment); a
    # bitstream-identical MKV is also produced for inspection/recovery.
    encoded_mov = job_dir / "video" / "encoded.mov"
    if not encoded_mov.is_file():
        step("encoding video (libx265 ultrafast -> MOV + MKV artifact)...")
        FFmpegUltrafastVideoBackend(ffmpeg, ffprobe).encode(
            source, encoded_mov
        )
    step(f"video ready: {encoded_mov}")

    # 4. audio: copy note (actual copy happens at mux time)
    CopyAudioBackend().note(source, job_dir / "audio")

    # 5. GPAC reconstruction: video (from MOV intermediate, exact
    # 1001/60000 deltas) + audio (container copy from source).
    stage = job_dir / "final" / "stage.mp4"
    final = job_dir / "final" / "output.mp4"
    stage.parent.mkdir(parents=True, exist_ok=True)
    step("muxing video+audio with MP4Box...")
    gpac.mux_new(stage, [f"{encoded_mov}#video", f"{source}#audio"])

    step("reconstructing rtmd track / tref / nrtm meta / brands...")
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
        gpac.set_brand(stage, major, compat, remove=["qt  ", "isom", "iso2"])

    # 6. uuid byte patch -> final output. GPAC already carries some
    # track-level uuid boxes over when importing from the source (seen:
    # the soun track's USMT), so insert only what's actually missing.
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

    # 7. validation
    step("validating original vs final...")
    report = compare(original=source, final=final, gpac=gpac,
                     ffprobe=ffprobe, scratch=job_dir / "validate")
    report["job_dir"] = str(job_dir)
    report["log"] = log
    (job_dir / "report.json").write_text(
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--work-dir", type=Path, default=SCRIPT_DIR / "work")
    ap.add_argument("--gpac-dir", type=Path,
                    default=Path(r"C:\Program Files\GPAC"))
    ap.add_argument("--ffmpeg", type=Path, default=SCRIPT_DIR / "tools" / "ffmpeg.exe")
    ap.add_argument("--ffprobe", type=Path, default=SCRIPT_DIR / "tools" / "ffprobe.exe")
    args = ap.parse_args()

    if not args.all and not args.source:
        ap.error("give --source or --all")

    gpac = GpacContainerBackend(args.gpac_dir)
    print(f"GPAC: {gpac.version()}")

    sources = sony_sources(SCRIPT_DIR) if args.all else [args.source]
    failures = 0
    reports = []
    for src in sources:
        if not src.is_file():
            print(f"SKIP (missing): {src}")
            failures += 1
            continue
        try:
            reports.append(run_job(
                src, args.work_dir, gpac, args.ffmpeg, args.ffprobe
            ))
        except Exception as exc:  # keep going across files
            failures += 1
            print(f"FAILED {src.name}: {exc}", flush=True)

    if args.all and reports:
        ok = sum(1 for r in reports if r["structural_success"])
        print(f"\n=== {ok}/{len(reports)} structurally successful, "
              f"{failures} failed ===")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
