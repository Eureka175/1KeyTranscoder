# -*- coding: utf-8 -*-
"""Lossless Sony XAVC remux mode.

The camera writes each clip into a 128 MiB media-data chunk.  When the
clip is shorter than the allocated chunk, the unused tail stays inside
``mdat`` (plus a small ``free`` box); a normal copy preserves it.  This
mode rewrites the ISOBMFF container with MP4Box's native track copy:
video, PCM audio and the Sony ``rtmd`` data track are copied bit-for-bit,
while sample tables/mdat are rebuilt without the dead padding.

Sony ``nrtm`` file-level meta, vendor uuid boxes, ``tref/cdsc`` and the
XAVC brands are restored through the existing preservation bundle, then
the result is validated against the source before it is placed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .paths import job_id_for, safe_unlink
from preservation import isobmf
from preservation.models import PreservationBundle
from preservation.sony import SonyPreservationBackend
from preservation.validate import compare

__all__ = ["RemuxResult", "remux_one", "remux_main"]


@dataclass
class RemuxResult:
    source: Path
    output: Path
    status: str
    source_size: int = 0
    output_size: int = 0
    saved_bytes: int = 0
    detail: str = ""
    report: dict[str, Any] | None = None


def _item_type_bytes_local(text: str) -> bytes | None:
    text = (text or "").strip()
    if not text:
        return None
    if len(text) == 8:
        try:
            return bytes.fromhex(text)
        except ValueError:
            return None
    if len(text) == 4:
        return text.encode("latin-1")
    return None


def remux_one(
    *,
    source: Path,
    dst: Path,
    work_dir: Path,
    gpac: Any,
    ffprobe: Path,
    log: Callable[[str], None],
    defer_check: bool = False,
    check_level: str = "basic",
    ffmpeg: Path | None = None,
    gyroflow: Path | None = None,
    no_metadata: bool = False,
) -> RemuxResult:
    """Remux one Sony file; returns a result.  Never modifies *source*."""
    work_dir.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    bundle_dir = work_dir / "metadata"
    sony = SonyPreservationBackend(gpac, ffprobe)
    bundle = sony.extract(source, bundle_dir)

    if not bundle.tracks and not bundle.nrtm:
        raise RuntimeError(
            "not a Sony XAVC source (no rtmd track / nrtm meta); "
            "--remux only supports Sony camera files"
        )
    is_sony = (
        str(bundle.major_brand or "").upper() == "XAVC"
        or any(str(t.sample_entry_type).lower() == "rtmd"
               for t in bundle.tracks)
    )
    if not is_sony:
        raise RuntimeError(
            "not a Sony XAVC source (major brand "
            f"{bundle.major_brand!r}, no rtmd track); "
            "--remux only supports Sony camera files"
        )

    info_ts, tracks = gpac.parse_info(gpac.info(source))
    video_id = next(
        (t["id"] for t in tracks if t.get("handler") == "vide"), None
    )
    if not video_id:
        raise RuntimeError("source has no video track")

    movie_ts = bundle.movie_timescale or bundle.video_timescale or info_ts
    if movie_ts <= 0:
        raise RuntimeError("no movie timescale in source")
    gpac.movie_timescale = movie_ts

    adds = [f"{source}#{video_id}"]
    for at in bundle.audio_tracks:
        adds.append(f"{source}#{at.track_id}")
    for mt in bundle.tracks:
        adds.append(f"{source}#{mt.track_id}")

    stage = work_dir / "stage.mp4"
    final = work_dir / "final.mp4"
    for p in (stage, final):
        safe_unlink(p)

    log(f"muxing native tracks with MP4Box ({len(adds)} track source(s))")
    gpac.mux_new(stage, adds)

    log("verifying copied rtmd payload / restoring tref + nrtm")
    sony.reconstruct(bundle, bundle_dir, stage)

    log("flattening and restoring XAVC brands")
    gpac.flatten(stage)
    if bundle.major_brand:
        compat = [
            b for b in bundle.compatible_brands
            if b and b != bundle.major_brand
        ]
        major = bundle.major_brand
        if bundle.brand_minor_version:
            major += f":{bundle.brand_minor_version}"
        gpac.set_brand(
            stage, major, compat,
            remove=[
                b for b in ("qt  ", "isom", "iso2")
                if b not in compat and b != bundle.major_brand
            ],
        )

    log("re-inserting vendor uuid boxes")
    existing = {
        (ctx, isobmf.sha256_bytes(raw))
        for ctx, _, raw in isobmf.extract_uuid_boxes(stage)
    }
    inserts = [
        (ctx, data)
        for ctx, data in sony.uuid_inserts(bundle, bundle_dir)
        if (ctx, isobmf.sha256_bytes(data)) not in existing
    ]
    isobmf.insert_uuid_boxes(stage, final, inserts)

    if bundle.nrtm and bundle.nrtm.item_id > 0:
        target = _item_type_bytes_local(bundle.nrtm.item_type)
        if target:
            try:
                isobmf.patch_meta_item_type(final, bundle.nrtm.item_id, target)
            except RuntimeError as exc:
                log(f"WARNING: meta item_type patch skipped: {exc}")

    known_facts: dict[str, Any] = {}
    if bundle.tracks:
        known_facts["rtmd_sha256"] = bundle.tracks[0].samples_sha256
    if bundle.nrtm:
        known_facts.update(
            lens_sha256=bundle.nrtm.lens_profile_sha256,
            lens_size=bundle.nrtm.lens_profile_size,
            xml_sha256=bundle.nrtm.xml_sha256,
        )
    if defer_check:
        return RemuxResult(
            source=source, output=final, status="prepared",
            source_size=source.stat().st_size,
            output_size=final.stat().st_size,
            saved_bytes=source.stat().st_size - final.stat().st_size,
            report={
                "known_facts": known_facts,
                "check_level": check_level,
                "no_metadata": bool(no_metadata),
            },
        )
    log("validating source vs remuxed file (advanced)")
    report = compare(
        source, final, gpac, ffprobe, work_dir / "validate",
        known_facts=known_facts, level="advanced", codec="hevc",
    )
    summary = report.get("summary", {})
    if not report.get("structural_success") or any(
        summary.get(k, 0) for k in ("MODIFIED", "MISSING")
    ):
        raise RuntimeError(
            f"validation failed: {json.dumps(summary, ensure_ascii=False)}"
        )

    # final placement is atomic; source is untouched
    safe_unlink(dst)
    os.replace(final, dst)
    src_size = source.stat().st_size
    out_size = dst.stat().st_size
    report["remux"] = {
        "source_bytes": src_size,
        "output_bytes": out_size,
        "saved_bytes": src_size - out_size,
        "saved_pct": round((1 - out_size / src_size) * 100, 3)
        if src_size else 0,
    }
    return RemuxResult(
        source=source, output=dst, status="done",
        source_size=src_size, output_size=out_size,
        saved_bytes=src_size - out_size, report=report,
    )


def _source_kind(ffprobe: Path, source: Path) -> str:
    """Classify a remux source without importing the encoder batch layer."""
    from core.probe import probe_source

    try:
        _summary, streams = probe_source(ffprobe, source)
    except Exception:
        return "unknown"
    is_dji = False
    for st in streams:
        tag = str(st.get("codec_tag_string") or "").lower()
        if tag == "rtmd":
            return "sony"
        if tag in ("djmd", "dbgi", "tmcd"):
            is_dji = True
    return "dji" if is_dji else "unknown"


def _copy_joined_sidecars(
    first_segment: Path, dst: Path, log: Callable[[str], None]
) -> None:
    """Attach XML sidecars of the first split segment to the joined output."""
    try:
        candidates = list(first_segment.parent.glob(
            f"{first_segment.stem}*.xml"))
        candidates += list(first_segment.parent.glob(
            f"{first_segment.stem}*.XML"))
    except OSError:
        return
    seen: set[Path] = set()
    for side in candidates:
        if not side.is_file() or side in seen:
            continue
        seen.add(side)
        suffix = side.name[len(first_segment.stem):]
        target = dst.with_name(f"{dst.stem}{suffix}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(side, target)
            log(f"copied joined sidecar {side.name} -> {target.name}")
        except OSError as exc:
            log(f"WARNING: joined sidecar copy failed {side}: {exc}")


def _remux_dji_one(
    *,
    source: Path,
    dst: Path,
    work_dir: Path,
    gpac: Any,
    ffprobe: Path,
    level: str,
    gyroflow: Path | None,
    log: Callable[[str], None],
    defer_check: bool = False,
) -> RemuxResult:
    """Lossless DJI container rebuild: all video/audio/djmd/dbgi/tmcd tracks
    are copied bit-for-bit, no video encode.  Validation follows --check."""
    from preservation import dji

    work_dir.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    movie_ts, tracks = dji.track_manifest(gpac, source)
    specs = dji.dji_track_specs(tracks)
    if specs["video_id"] is None:
        raise RuntimeError("DJI rebuild: source has no video track")
    if not specs["data_ids"]:
        raise RuntimeError(
            "DJI rebuild: source has no djmd/dbgi/tmcd data track")
    adds = [f"{source}#{specs['video_id']}"]
    adds += [f"{source}#{aid}" for aid in specs["audio_ids"]]
    adds += [f"{source}#{did}" for did, _entry in specs["data_ids"]]
    video_entry = next(
        (str(t.get("entry") or "hvc1") for t in tracks
         if t.get("handler") == "vide"), "hvc1")

    stage = work_dir / "stage.mp4"
    final = work_dir / "final.mp4"
    for pth in (stage, final):
        safe_unlink(pth)
    gpac.movie_timescale = movie_ts
    log(f"DJI rebuild: muxing {len(adds)} native track(s), no encode")
    gpac.mux_new(stage, adds)
    gpac.flatten(stage)

    if defer_check:
        return RemuxResult(
            source=source, output=stage, status="prepared",
            source_size=source.stat().st_size,
            output_size=stage.stat().st_size,
            saved_bytes=source.stat().st_size - stage.stat().st_size,
            report={
                "video_entry": video_entry,
                "check_level": level,
            },
        )

    log(f"DJI rebuild: validating ({level})")
    report = dji.run_dji_check(
        original=source,
        final=stage,
        gpac=gpac,
        ffprobe=ffprobe,
        gyroflow=gyroflow,
        scratch=work_dir / "validate",
        level=level,
        video_entry=video_entry,
        video_reencoded=False,
        log=log,
    )
    summary = report.get("summary", {})
    if not report.get("structural_success") or any(
        summary.get(k, 0) for k in ("MODIFIED", "MISSING")
    ):
        raise RuntimeError(
            "DJI rebuild validation failed: "
            f"{json.dumps(summary, ensure_ascii=False)}")

    safe_unlink(dst)
    os.replace(stage, dst)
    src_size = source.stat().st_size
    out_size = dst.stat().st_size
    report["remux"] = {
        "mode": "dji-join/rebuilt",
        "source_bytes": src_size,
        "output_bytes": out_size,
        "saved_bytes": src_size - out_size,
        "saved_pct": round((1 - out_size / src_size) * 100, 3)
        if src_size else 0,
        "check_level": level,
    }
    return RemuxResult(
        source=source, output=dst, status="done",
        source_size=src_size, output_size=out_size,
        saved_bytes=src_size - out_size, report=report,
    )


def _copy_sidecars(input_root: Path, output_root: Path, log: Callable[[str], None]) -> int:
    copied = 0
    for src in input_root.rglob("*"):
        if not src.is_file() or src.suffix.lower() != ".xml":
            continue
        rel = src.relative_to(input_root)
        dst = output_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    log(f"copied {copied} XML sidecar(s)")
    return copied


class _RemuxSlots:
    """--jobs gate for actual remux work (not for async validation)."""

    def __init__(self, limit: int) -> None:
        self._cond = threading.Condition()
        self._limit = max(1, int(limit))
        self._active = 0

    def acquire(self) -> None:
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, int(limit))
            self._cond.notify_all()


def _container_frame_check(
    source: Path, output: Path, ffprobe: Path, log: Callable[[str], None]
) -> dict[str, Any]:
    """--no-metadata remux check: container/frame validity only."""
    from core.probe import count_frames

    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("container check: output missing/empty")
    s_frames, s_fps = count_frames(ffprobe, source)
    o_frames, o_fps = count_frames(ffprobe, output)
    if not s_frames or not o_frames or s_frames != o_frames:
        raise RuntimeError(
            f"container check: frame count {s_frames} != {o_frames}")
    if s_fps and o_fps and s_fps != o_fps:
        raise RuntimeError(f"container check: fps {s_fps} != {o_fps}")
    log(f"container/frame check PASS ({o_frames} frames)")
    return {
        "status": "PASS",
        "mode": "container-only",
        "summary": {"frames": o_frames, "fps": o_fps},
        "structural_success": True,
    }


def _validate_prepared(
    *,
    prepared: RemuxResult,
    kind: str,
    check_level: str,
    no_metadata: bool,
    ffprobe: Path,
    gpac: Any,
    ffmpeg: Path | None,
    gyroflow: Path | None,
    log: Callable[[str], None],
) -> dict[str, Any]:
    if no_metadata:
        return _container_frame_check(
            prepared.source, prepared.output, ffprobe, log)
    if kind == "sony":
        from preservation.checker import run_checks

        facts = dict((prepared.report or {}).get("known_facts") or {})
        report = run_checks(
            original=prepared.source,
            final=prepared.output,
            gpac=gpac,
            ffprobe=ffprobe,
            level=check_level,
            gyroflow=gyroflow,
            work_dir=prepared.output.parent / "validate",
            known_facts=facts,
            codec="hevc",
            ffmpeg=ffmpeg,
            video_reencoded=False,
            log=log,
        )
    else:
        from preservation import dji

        vid_entry = str(
            (prepared.report or {}).get("video_entry") or "hvc1")
        report = dji.run_dji_check(
            original=prepared.source,
            final=prepared.output,
            gpac=gpac,
            ffprobe=ffprobe,
            gyroflow=gyroflow,
            scratch=prepared.output.parent / "validate",
            level=check_level,
            video_entry=vid_entry,
            video_reencoded=False,
            log=log,
        )
    summary = report.get("summary", {})
    if not report.get("structural_success") or any(
        summary.get(k, 0) for k in ("MODIFIED", "MISSING")
    ):
        raise RuntimeError(
            f"validation failed ({check_level}): "
            f"{json.dumps(summary, ensure_ascii=False)}")
    return report


def _place_prepared(prepared: RemuxResult, dst: Path) -> RemuxResult:
    safe_unlink(dst)
    os.replace(prepared.output, dst)
    src_size = prepared.source.stat().st_size
    out_size = dst.stat().st_size
    return RemuxResult(
        source=prepared.source, output=dst, status="done",
        source_size=src_size, output_size=out_size,
        saved_bytes=src_size - out_size,
        report=prepared.report,
    )


def remux_main(
    *,
    args: Any,
    input_root: Path,
    output_root: Path,
    ffprobe: Path,
    gpac: Any,
    script_dir: Path,
    gyroflow: Path | None = None,
    ffmpeg: Path | None = None,
    jobs: Any = "auto",
) -> int:
    from core.paths import discover_sources

    output_root.mkdir(parents=True, exist_ok=True)
    logs_root = output_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    total_log = logs_root / "total.log"
    logger = logging.getLogger("1kt.remux")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(total_log, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fh.formatter)
    logger.addHandler(sh)

    def log(msg: str) -> None:
        logger.info(msg)

    check_level = str(getattr(args, "check", "basic")).lower()
    dj_join = bool(getattr(args, "dji_join", False))
    no_metadata = bool(getattr(args, "no_metadata", False))
    original_sources = discover_sources(input_root)
    sources = original_sources
    source_root = input_root
    dji_pipeline = None
    if dj_join:
        from core.dji_join import ProgressiveStager, detect_plan

        plan = detect_plan(input_root, original_sources, ffprobe, log=log)
        if plan.groups:
            staging_root = output_root / (
                f".dji_join_work_{time.strftime('%Y%m%d_%H%M%S')}_"
                f"{os.getpid()}"
            )
            dji_pipeline = ProgressiveStager(
                plan=plan,
                staging_root=staging_root,
                gpac=gpac,
                ffprobe=ffprobe,
                log=log,
                keep_temp=bool(getattr(args, "dji_join_keep_temp", False)),
                # Remux can rebuild several already-joined groups at once;
                # the join worker itself remains ordered/one-at-a-time.
                parallel_groups=True,
            )
            try:
                sources = dji_pipeline.prepare()
            except Exception as exc:                   # noqa: BLE001
                log(f"[DJI-JOIN] FATAL: staging failed: {exc}")
                try:
                    dji_pipeline.close(keep=True)
                except Exception:
                    pass
                logger.removeHandler(fh)
                fh.close()
                return 1
            source_root = staging_root
            log(
                f"DJI-JOIN input substitution | {len(plan.groups)} group(s), "
                f"progressive/parallel remux | staging={staging_root} | "
                f"first group joined"
            )
        else:
            log(
                "DJI-JOIN | no split recording detected; remuxing source files "
                "individually"
            )

    log(
        f"remux mode | input={input_root} | output={output_root} | "
        f"files={len(sources)} | check={check_level} | jobs={jobs} | "
        f"async-check=on"
    )
    sidecars = _copy_sidecars(input_root, output_root, log)
    group_by_dest: dict[Path, Any] = {}
    if dji_pipeline is not None:
        group_by_dest = {
            dest.resolve(): group
            for dest, group in zip(dji_pipeline.group_dest, dji_pipeline.groups)
        }

    # --jobs: actual remux work slots.  Validation does not hold a slot; it
    # runs under one global verifier gate, exactly like the video pipeline.
    jobs_text = str(jobs).strip().lower()
    if jobs_text == "auto":
        fixed_jobs = None
    else:
        try:
            fixed_jobs = int(jobs_text)
            if fixed_jobs < 1:
                raise ValueError
        except (TypeError, ValueError):
            log(
                f"[FATAL] --jobs must be 'auto' or a positive integer "
                f"(got {jobs!r})")
            logger.removeHandler(fh)
            fh.close()
            return 2

    slots = _RemuxSlots(fixed_jobs if fixed_jobs is not None else 2)
    check_gate = threading.Semaphore(1)
    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def process_one(src: Path) -> dict[str, Any]:
        rel = src.relative_to(source_root)
        dst = output_root / rel
        if dji_pipeline is not None:
            try:
                dji_pipeline.ensure_ready(src)
            except Exception as exc:                # noqa: BLE001
                detail = f"DJI join failed: {exc}"
                log(f"[FAIL] {src.name} | {detail}")
                dji_pipeline.note_finished(src, "failed")
                return {"source": str(src), "output": "",
                        "status": "failed", "detail": detail}
            dji_pipeline.note_encode_started(src)
        if dst.is_file() and dst.stat().st_size > 0:
            log(f"[SKIP] {src.name} (exists)")
            if dji_pipeline is not None:
                dji_pipeline.note_finished(src, "skipped")
            return {"source": str(src), "output": str(dst),
                    "status": "skipped", "detail": ""}

        kind = _source_kind(ffprobe, src) if dj_join else "sony"
        work_dir = output_root / ".1ktwork" / job_id_for(src)
        file_log = logs_root / "files" / rel.parent / f"{rel.stem}.remux.log"
        file_log.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        last_exc: Exception | None = None
        prepared = None
        for attempt in (1, 2):
            try:
                log(
                    f"[WORK] {src.name} | kind={kind} | attempt {attempt}/2 "
                    f"| check={check_level}"
                )
                slots.acquire()
                try:
                    if kind == "dji":
                        prepared = _remux_dji_one(
                            source=src, dst=dst, work_dir=work_dir,
                            gpac=gpac, ffprobe=ffprobe,
                            level=check_level, gyroflow=gyroflow,
                            log=log, defer_check=True,
                        )
                    else:
                        prepared = remux_one(
                            source=src, dst=dst, work_dir=work_dir,
                            gpac=gpac, ffprobe=ffprobe, log=log,
                            defer_check=True, check_level=check_level,
                            ffmpeg=ffmpeg, gyroflow=gyroflow,
                            no_metadata=no_metadata,
                        )
                finally:
                    slots.release()

                log(f"[CHECK] waiting for verifier slot | {src.name}")
                check_gate.acquire()
                try:
                    report = _validate_prepared(
                        prepared=prepared, kind=kind,
                        check_level=check_level,
                        no_metadata=no_metadata,
                        ffprobe=ffprobe, gpac=gpac,
                        ffmpeg=ffmpeg, gyroflow=gyroflow, log=log,
                    )
                finally:
                    check_gate.release()
                prepared.report = report
                result = _place_prepared(prepared, dst)
                elapsed = time.time() - start
                result.report["remux"] = {
                    "mode": "dji-join/rebuilt" if kind == "dji"
                    else "sony-xavc",
                    "source_bytes": result.source_size,
                    "output_bytes": result.output_size,
                    "saved_bytes": result.saved_bytes,
                    "saved_pct": round(
                        (1 - result.output_size / result.source_size) * 100, 3
                    ) if result.source_size else 0,
                    "check_level": check_level,
                    "no_metadata": no_metadata,
                    "attempt": attempt,
                }
                report_path = (
                    logs_root / "remux_reports" / f"{rel.stem}.json")
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(result.report, ensure_ascii=False, indent=2)
                    + "\n", encoding="utf-8")
                file_log.write_text(
                    f"status=done\nelapsed_sec={elapsed:.3f}\n"
                    f"source_bytes={result.source_size}\n"
                    f"output_bytes={result.output_size}\n"
                    f"saved_bytes={result.saved_bytes}\n"
                    f"attempt={attempt}\n",
                    encoding="utf-8")
                log(
                    f"[OK] {src.name} | {result.source_size/1e6:.1f} -> "
                    f"{result.output_size/1e6:.1f} MB | saved "
                    f"{result.saved_bytes/1e6:.1f} MB | "
                    f"{elapsed:.1f}s | attempt {attempt}/2"
                )
                group = group_by_dest.get(src.resolve())
                if group is not None and group.segments:
                    _copy_joined_sidecars(group.segments[0].path, dst, log)
                if dji_pipeline is not None:
                    dji_pipeline.note_finished(src, "done")
                try:
                    shutil.rmtree(work_dir, ignore_errors=True)
                except OSError:
                    pass
                return {"source": str(src), "output": str(dst),
                        "status": "done", "elapsed_sec": round(elapsed, 3),
                        "source_bytes": result.source_size,
                        "output_bytes": result.output_size,
                        "saved_bytes": result.saved_bytes}
            except Exception as exc:                    # noqa: BLE001
                last_exc = exc
                if prepared is not None:
                    safe_unlink(prepared.output)
                    prepared = None
                log(f"[CHECK-FAIL] {src.name} attempt {attempt}/2 | {exc}")
                if attempt == 1:
                    log(f"[RETRY] {src.name} | remux + check again")
                    continue
        detail = f"{type(last_exc).__name__}: {last_exc}"
        file_log.write_text(detail + "\n", encoding="utf-8")
        log(f"[FAIL] {src.name} | {detail}")
        if dji_pipeline is not None:
            dji_pipeline.note_finished(src, "failed")
        return {"source": str(src), "output": str(dst),
                "status": "failed", "detail": detail}

    failed = 0
    try:
        if fixed_jobs is not None:
            with ThreadPoolExecutor(max_workers=fixed_jobs + 2) as ex:
                futures = [ex.submit(process_one, src) for src in sources]
                for fut in as_completed(futures):
                    rec = fut.result()
                    with results_lock:
                        results.append(rec)
                    if rec["status"] == "failed":
                        failed += 1
        else:
            from core.batch_hw import AdaptiveJobs

            ctrl = AdaptiveJobs(logger, start=2)
            pending = deque(sources)
            futures: dict[Any, Path] = {}
            last_wake = time.monotonic()
            with ThreadPoolExecutor(max_workers=ctrl.cap + 2) as ex:
                while pending or futures:
                    target = max(1, ctrl.current) + 2
                    while pending and len(futures) < target:
                        nxt = pending.popleft()
                        futures[ex.submit(process_one, nxt)] = nxt
                    if not futures:
                        break
                    done, _ = wait(
                        tuple(futures), return_when=FIRST_COMPLETED)
                    now = time.monotonic()
                    dt = max(0.001, now - last_wake)
                    last_wake = now
                    for fut in done:
                        futures.pop(fut, None)
                        rec = fut.result()
                        with results_lock:
                            results.append(rec)
                        if rec["status"] == "failed":
                            failed += 1
                    next_jobs = ctrl.note_sample(len(done) / dt)
                    slots.set_limit(next_jobs)
                    log(
                        f"[ADAPTIVE remux] completion rate={len(done)/dt:.2f} "
                        f"files/s -> next slots={next_jobs}"
                    )
    finally:
        if dji_pipeline is not None:
            try:
                paths = dji_pipeline.write_report()
                log(f"[DJI-JOIN] reports | "
                    f"{paths.get('joined', paths.get('plan'))}")
            except Exception as exc:                    # noqa: BLE001
                log(f"WARNING: DJI join report failed: {exc}")
            keep = (
                bool(getattr(args, "dji_join_keep_temp", False))
                or failed > 0
                or dji_pipeline.error is not None
            )
            dji_pipeline.close(keep=keep)

    summary = {
        "mode": "remux",
        "input": str(input_root),
        "output": str(output_root),
        "files": len(sources),
        "failed": failed,
        "check_level": check_level,
        "jobs": jobs,
        "async_check": True,
        "xml_sidecars": sidecars,
        "results": results,
    }
    if dji_pipeline is not None:
        summary["dji_join"] = {
            "groups": len(dji_pipeline.groups),
            "staging": str(dji_pipeline.root),
            "kept": bool(getattr(args, "dji_join_keep_temp", False)
                         or failed > 0),
        }
    (logs_root / "remux_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"Finished | done={len(sources)-failed} failed={failed}")
    logger.removeHandler(fh)
    fh.close()
    return 1 if failed else 0
