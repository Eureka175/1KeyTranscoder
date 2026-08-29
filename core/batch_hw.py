"""Hardware batch processing: per-file handlers, downgrade ladder,
failure records, and worker schedulers.

Extracted from the main program so 1kt.py stays a thin orchestrator.

Key behaviors (final design):
- hardware encode paths always decode with --avsw (software);
- runtime encode failures NO LONGER open a prompt window: the
  downgrade ladder runs automatically (source -> 10bit 4:2:0 ->
  8bit 4:2:0) with a prominent WARNING per rung printed in the work
  window and recorded in logs + failed_files.json when exhausted;
- capability-driven downgrades happen silently up front (WARNING);
- reader failures fall back to an MP4Box strip once.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from encoders.caps import (
    BackendCaps,
    CodecCaps,
    downgrade_ladder,
    probe_backend,
    supports,
)
from encoders.hw import (
    classify_failure,
    plan_initial_format,
    read_log_tail,
    run_hw_tool,
)
from encoders.nvencc import NvencBackend
from encoders.qsvencc import QsvBackend

from .config import find_hw_tool
from .dashboard import DashboardStatus
from .logging_utils import (
    PRE_CSV_FIELDS,
    STREAM_CSV_FIELDS,
    append_csv,
    append_rows,
    build_file_logger,
    format_mb,
    make_stream_rows,
    make_summary_csv_row,
)
from .models import SourceInfo
from .paths import (
    job_id_for,
    output_path_for,
    per_file_log_path,
    safe_unlink,
)
from .postprobe import postprobe_and_log
from .probe import build_source_info, count_frames, probe_source

from preservation.gpac import GpacContainerBackend
from preservation.pipeline import run_sony_pipeline
from preservation.sony import ParsedFile

StatusCb = Callable[[str, str], None]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def detect_vfr(src_info: SourceInfo) -> bool:
    """VFR detection: r_frame_rate != avg_frame_rate (both non-empty)."""
    rfr = (src_info.r_frame_rate or "").strip()
    afr = (src_info.avg_frame_rate or "").strip()
    if not rfr or not afr or rfr in ("0/0", "N/A") or afr in ("0/0", "N/A"):
        return False
    return rfr != afr


def emit_warning(
    logger: logging.Logger,
    file_logger: logging.Logger,
    msg: str,
    warnings: list[str] | None = None,
) -> None:
    """Prominent WARNING: work window + total log + per-file log."""
    logger.warning("[WARNING] %s", msg)
    file_logger.warning("WARNING | %s", msg)
    print(f"[WARNING] {msg}", flush=True)
    if warnings is not None:
        warnings.append(msg)


def audio_track_ids(gpac: GpacContainerBackend, source: Path) -> list[int]:
    """Source audio track IDs via MP4Box -diso (handler = soun)."""
    parsed = ParsedFile(gpac.diso_xml(source))
    ids: list[int] = []
    for trak in parsed.tracks():
        info = parsed.track_info(trak)
        if info["handler_type"] == "soun":
            ids.append(info["track_id"])
    return ids


def strip_video_audio(
    gpac: GpacContainerBackend,
    source: Path,
    work_dir: Path,
    audio_copy: bool,
) -> Path:
    """MP4Box native track copy: video (+audio) only, exact timing."""
    stripped = work_dir / "video" / "stripped.mov"
    stripped.parent.mkdir(parents=True, exist_ok=True)
    try:
        if stripped.exists():
            stripped.unlink()
    except OSError:
        pass
    adds = [f"{source}#video"]
    if audio_copy:
        for track_id in audio_track_ids(gpac, source):
            adds.append(f"{source}#{track_id}")
    gpac.mux_new(stripped, adds)
    if not stripped.is_file() or stripped.stat().st_size <= 0:
        raise RuntimeError("strip produced no output")
    return stripped


def cleanup_work_dir(
    *,
    work_dir: Path,
    job_id: str,
    preserve_reports: Path,
    keep_work: bool,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> None:
    """Post-delivery GC: keep report.json in logs, remove the rest."""
    if keep_work:
        file_logger.info("KEEP-WORK | work dir retained | %s", work_dir)
        return
    report_src = work_dir / "report.json"
    if report_src.is_file():
        try:
            preserve_reports.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report_src, preserve_reports / f"{job_id}.json")
            file_logger.info(
                "GC | preservation report kept | %s",
                preserve_reports / f"{job_id}.json",
            )
        except OSError as exc:
            file_logger.warning(
                "GC | report copy failed (work dir still removed) | %s", exc
            )
    for attempt in range(3):
        try:
            shutil.rmtree(work_dir)
            file_logger.info("GC | work dir removed | %s", work_dir)
            return
        except OSError as exc:
            if attempt == 2:
                emit_warning(
                    logger,
                    file_logger,
                    f"GC failed, work dir retained: {work_dir} | {exc}",
                )
                return
            time.sleep(1.0)


def hw_backend_for(
    encoder_name: str,
    script_dir: Path,
    args: argparse.Namespace,
    work_root: Path,
    logger: logging.Logger,
):
    """Build a hardware backend + probe/save capabilities at run start."""
    kind = "nvencc" if encoder_name == "nvenc" else "qsvencc"
    exe_name = "NVEncC64.exe" if kind == "nvencc" else "QSVEncC64.exe"
    explicit = args.tool_nvencc if kind == "nvencc" else args.tool_qsvencc
    tool = find_hw_tool(script_dir, exe_name, explicit)
    caps = probe_backend(tool, kind)
    caps_dir = work_root / "caps"
    if caps is None:
        logger.warning(
            "[WARNING] %s capability probe failed; conservative "
            "(8bit 4:2:0) assumed",
            exe_name,
        )
        caps = BackendCaps(tool=exe_name, codecs={"hevc": CodecCaps()})
    json_path, raw_path = caps.save(caps_dir)
    logger.info(
        "Caps %s: device=%s driver=%s hevc(10bit=%s,422=%s,422-10bit=%s)",
        exe_name,
        caps.device,
        caps.driver,
        caps.codecs.get("hevc", CodecCaps()).bit10,
        caps.codecs.get("hevc", CodecCaps()).csp_422,
        caps.codecs.get("hevc", CodecCaps()).bit10_422,
    )
    logger.info("Caps files: %s | %s", json_path, raw_path)
    if encoder_name == "nvenc":
        return NvencBackend(tool, caps)
    return QsvBackend(tool, caps)


def hw_encode_with_fallback(
    *,
    label: str,
    backend,
    source: Path,
    output: Path,
    profile: dict,
    src_info: SourceInfo,
    vfr: bool,
    work_dir: Path,
    total_frames: int,
    ffprobe: Path,
    audio_copy: bool,
    do_frame_check: bool,
    no_downgrade: bool,
    gpac: GpacContainerBackend,
    logger: logging.Logger,
    file_logger: logging.Logger,
    progress_cb: Callable[[int, float], None] | None = None,
    show_progress: bool = True,
) -> tuple[list[str], tuple[str, int]]:
    """Automatic three-tier downgrade ladder (NO prompt window).

    Capability-driven downgrades happen silently up front (WARNING);
    runtime failures run the ladder automatically with a WARNING per
    rung; reader failures use the MP4Box strip fallback once.
    Returns (warnings, final_format). Raises RuntimeError on fatal."""
    warnings: list[str] = []
    caps = backend.caps
    chroma = src_info.chroma
    depth = src_info.bit_depth

    planned, needs_downgrade = plan_initial_format(
        caps, backend.kind, chroma, depth
    )
    if needs_downgrade:
        if no_downgrade:
            raise RuntimeError(
                f"[FATAL] {chroma}/{depth} unsupported by hardware and "
                "--no-downgrade is set; skipping"
            )
        emit_warning(
            logger,
            file_logger,
            f"capability-driven downgrade: {chroma}/{depth} -> "
            f"{planned[0]}/{planned[1]}",
            warnings,
        )

    rungs = [
        r for r in downgrade_ladder(*planned)
        if r == planned or supports(caps, r[0], r[1])
    ]
    if not rungs:
        raise RuntimeError(
            f"[FATAL] no encodable format rung for {chroma}/{depth}"
        )

    cur_input = source
    stripped = False
    rung_idx = 0
    while rung_idx < len(rungs):
        c, d = rungs[rung_idx]
        cmd, skipped = backend.command(
            cur_input, output, profile, c, d, vfr, audio_copy
        )
        if skipped and rung_idx == 0:
            emit_warning(
                logger,
                file_logger,
                f"profile keys not advertised by {backend.name} "
                f"(skipped): {', '.join(sorted(skipped))}",
                warnings,
            )
        file_logger.info("COMMAND | %s", subprocess.list2cmdline(cmd))
        raw_log = output.with_name(output.name + f".{label}.log")
        try:
            if raw_log.exists():
                raw_log.unlink()
        except OSError:
            pass
        rc, elapsed = run_hw_tool(
            cmd, raw_log, total_frames, label, progress=show_progress
        )

        ok = False
        if rc == 0 and output.is_file() and output.stat().st_size > 0:
            if do_frame_check:
                try:
                    frames, fps = count_frames(ffprobe, output)
                    src_frames, src_fps = count_frames(ffprobe, source)
                    if frames == src_frames and (
                        not src_fps or fps == src_fps
                    ):
                        ok = True
                    else:
                        emit_warning(
                            logger,
                            file_logger,
                            f"1:1 check failed ({frames}/{src_frames}, "
                            f"{fps}/{src_fps}) — treated as format "
                            "failure",
                            warnings,
                        )
                except Exception as exc:
                    emit_warning(
                        logger, file_logger, f"1:1 check error: {exc}",
                        warnings,
                    )
            else:
                ok = True
        if ok:
            return warnings, (c, d)

        tail = read_log_tail(raw_log)
        cls = "format" if rc == 0 else classify_failure(tail)

        if cls == "environment":
            raise RuntimeError(
                f"[FATAL] environment failure (no downgrade retry): "
                f"{tail[-400:]}"
            )
        if cls == "reader" and not stripped and gpac is not None:
            try:
                cur_input = strip_video_audio(
                    gpac, source, work_dir, audio_copy
                )
                stripped = True
                emit_warning(
                    logger,
                    file_logger,
                    "reader failure -> MP4Box strip fallback "
                    f"({cur_input}), retrying same format",
                    warnings,
                )
                continue
            except Exception as exc:
                emit_warning(
                    logger,
                    file_logger,
                    f"strip fallback failed: {exc}",
                    warnings,
                )

        # format-class failure -> next rung (automatic, no prompt)
        if rung_idx == len(rungs) - 1:
            raise RuntimeError(
                f"[FATAL] all format rungs failed; last log:\n"
                f"{tail[-500:]}"
            )
        if no_downgrade:
            raise RuntimeError(
                "[FATAL] runtime encode failure and --no-downgrade "
                "is set; no retry"
            )
        emit_warning(
            logger,
            file_logger,
            f"runtime encode failure -> downgrade retry: {c}/{d} -> "
            f"{rungs[rung_idx + 1][0]}/{rungs[rung_idx + 1][1]}",
            warnings,
        )
        rung_idx += 1
    raise RuntimeError("[FATAL] downgrade ladder exhausted")


# ---------------------------------------------------------------------------
# preparation + headers
# ---------------------------------------------------------------------------

def prepare_source_hw(
    *,
    src: Path,
    preset: str,
    ffprobe: Path,
    preprobe_csv: Path,
    preprobe_stream_csv: Path,
    file_logger: logging.Logger,
) -> tuple[dict[str, Any], list[dict[str, Any]], SourceInfo, bool]:
    """Probe + CSVs + SourceInfo + VFR flag."""
    source_summary, source_streams = probe_source(ffprobe, src)

    append_csv(
        preprobe_csv,
        PRE_CSV_FIELDS,
        make_summary_csv_row(src, source_summary, preset),
    )
    append_rows(
        preprobe_stream_csv,
        STREAM_CSV_FIELDS,
        make_stream_rows(src, source_streams, preset),
    )

    src_info = build_source_info(src, source_summary, source_streams)
    vfr = detect_vfr(src_info)
    if vfr:
        file_logger.warning(
            "WARNING | VFR source detected (r_frame_rate=%s != "
            "avg_frame_rate=%s) -> encoding with --avsync forcecfr "
            "(nearest rational-rate CFR)",
            src_info.r_frame_rate,
            src_info.avg_frame_rate,
        )
    return source_summary, source_streams, src_info, vfr


def log_hw_header(
    *,
    logger: logging.Logger,
    file_logger: logging.Logger,
    src: Path,
    preset: str,
    backend,
    source_summary: dict[str, Any],
    src_info: SourceInfo,
    vfr: bool,
    planned: tuple[str, int],
    needs_downgrade: bool,
    profile: dict[str, Any],
) -> None:
    logger.info(
        "[START] %s | preset=%s | backend=%s | fps=%.6f | frames=%s",
        src, preset, backend.name,
        source_summary["fps"], source_summary["total_frames"] or "?",
    )
    logger.info(
        "[SOURCE] size=%s MB | duration=%.3f sec | %sx%s | pix_fmt=%s | "
        "bit_depth=%d | chroma=%s | streams=%d",
        format_mb(source_summary["source_size_bytes"]),
        source_summary["duration"],
        src_info.width, src_info.height, src_info.pix_fmt,
        src_info.bit_depth, src_info.chroma or "?",
        source_summary["stream_count"],
    )
    logger.info(
        "[FORMAT-PLAN] source=%s/%d -> encode=%s/%d | downgrade=%s | vfr=%s",
        src_info.chroma, src_info.bit_depth,
        planned[0], planned[1], needs_downgrade, vfr,
    )
    file_logger.info(
        "SOURCE_FORMAT | codec=%s | profile=%s | pix_fmt=%s | "
        "bit_depth=%d | chroma=%s | r_frame_rate=%s | avg_frame_rate=%s",
        src_info.codec, src_info.profile, src_info.pix_fmt,
        src_info.bit_depth, src_info.chroma,
        src_info.r_frame_rate, src_info.avg_frame_rate,
    )
    file_logger.info(
        "FORMAT_PLAN | source=%s/%d | encode=%s/%d | needs_downgrade=%s "
        "| vfr=%s",
        src_info.chroma, src_info.bit_depth,
        planned[0], planned[1], needs_downgrade, vfr,
    )
    file_logger.info(
        "EFFECTIVE_%s | %s",
        backend.name.upper(),
        json.dumps(profile, ensure_ascii=False, sort_keys=True),
    )


# ---------------------------------------------------------------------------
# failure records (JSON) + retry list
# ---------------------------------------------------------------------------

def record_failure(
    failed_path: Path,
    *,
    source: Path,
    preset: str,
    backend_name: str,
    stage: str,
    error: str,
    log_path: str,
) -> None:
    """Append one failure record to the JSON failure list."""
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        records = json.loads(
            failed_path.read_text(encoding="utf-8")
        ) if failed_path.is_file() else []
    except (OSError, ValueError):
        records = []
    records.append(
        {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": str(source),
            "preset": preset,
            "backend": backend_name,
            "stage": stage,
            "error": error,
            "log_path": log_path,
        }
    )
    failed_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_retry_list(path: Path) -> list[Path]:
    """Load a failed_files.json (or plain list) into source paths."""
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("source"):
                sources.append(Path(item["source"]))
            elif isinstance(item, str):
                sources.append(Path(item))
    return [s for s in sources if s.is_file()]


# ---------------------------------------------------------------------------
# per-file processing (hardware backends)
# ---------------------------------------------------------------------------

def encode_one_sony_hw(
    *,
    src: Path,
    dst: Path,
    source_summary: dict[str, Any],
    src_info: SourceInfo,
    vfr: bool,
    profile: dict[str, Any],
    preset: str,
    backend,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None,
    work_root: Path,
    preserve_reports: Path,
    keep_work: bool,
    no_downgrade: bool,
    check_level: str,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    status_cb: StatusCb | None = None,
    show_progress: bool = True,
) -> str:
    """Sony preservation path with a rigaya hardware encoder."""
    work_dir = work_root / job_id_for(src)
    encoded_mov = work_dir / "video" / "encoded.mov"

    planned, needs_downgrade = plan_initial_format(
        backend.caps, backend.kind, src_info.chroma, src_info.bit_depth
    )

    def build_cmd(chroma: str, depth: int) -> list[str]:
        cmd, _ = backend.command(
            src, encoded_mov, profile, chroma, depth, vfr, audio_copy=False
        )
        return cmd

    cmd = build_cmd(*planned)
    log_hw_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        backend=backend, source_summary=source_summary, src_info=src_info,
        vfr=vfr, planned=planned, needs_downgrade=needs_downgrade,
        profile=profile,
    )
    logger.info("[PRESERVE] Sony rtmd source -> preservation pipeline")
    file_logger.info("PRESERVATION | sony | work_dir=%s", work_dir)
    if status_cb:
        status_cb("preserve", "sony rtmd pipeline")

    if dry_run:
        file_logger.info(
            "DRY-RUN | planned encode: %s | %s",
            f"{planned[0]}/{planned[1]}",
            subprocess.list2cmdline(cmd),
        )
        return "dry-run"

    started = time.monotonic()
    warnings: list[str] = []

    def encode_video(source: Path, out_mov: Path) -> None:
        if status_cb:
            status_cb("encoding", f"{planned[0]}/{planned[1]}")
        enc_warnings, used = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=source, output=out_mov,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=False, do_frame_check=True,
            no_downgrade=no_downgrade, gpac=gpac,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
        )
        warnings.extend(enc_warnings)
        file_logger.info("ENCODE_FORMAT | %s/%s", used[0], used[1])

    def pipe_log(msg: str) -> None:
        logger.info("[SONY] %s", msg)
        file_logger.info("PIPELINE | %s", msg)

    try:
        report = run_sony_pipeline(
            source=src,
            work_dir=work_dir,
            encode_video=encode_video,
            gpac=gpac,
            ffprobe=ffprobe,
            has_audio=source_summary["audio_streams"] > 0,
            gyroflow=gyroflow,
            fix_hw_timing=True,
            check_level=check_level,
            log=pipe_log,
        )
    except Exception as exc:
        logger.error("[FAIL] preservation pipeline | %s | %s", src, exc)
        file_logger.exception("PRESERVATION PIPELINE FAILED")
        return "failed"

    elapsed = time.monotonic() - started

    gyro = report.get("gyroflow")
    if not report.get("structural_success"):
        logger.error(
            "[FAIL] structural preservation failed | %s | missing=%s "
            "modified=%s",
            src, report.get("critical_missing"),
            report.get("critical_modified"),
        )
        file_logger.error(
            "STRUCTURAL VALIDATION FAILED | missing=%s | modified=%s",
            report.get("critical_missing"),
            report.get("critical_modified"),
        )
        return "failed"
    if gyro is not None and gyro.get("status") != "PASS":
        logger.error(
            "[FAIL] Gyroflow consumer validation | %s | %s",
            src, gyro.get("detail"),
        )
        file_logger.error("GYROFLOW VALIDATION FAILED | %s", gyro.get("detail"))
        return "failed"

    dst.parent.mkdir(parents=True, exist_ok=True)
    final = work_dir / "final" / "output.mov"
    try:
        safe_unlink(dst)
        os.replace(final, dst)
    except OSError as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", final, dst, exc)
        file_logger.exception("RENAME FAILED | elapsed=%.3f sec", elapsed)
        return "failed"

    s = report["summary"]
    sc = report.get("selfcheck")
    logger.info(
        "[PRESERVE-OK] %s | PRESERVED=%d MODIFIED=%d MISSING=%d | "
        "gyroflow=%s | check=%s | warnings=%d",
        src.name, s["PRESERVED"], s["MODIFIED"], s["MISSING"],
        gyro.get("status") if gyro else "not-run",
        check_level,
        len(warnings),
    )
    file_logger.info(
        "PRESERVATION_REPORT | %s",
        json.dumps(
            {
                "summary": s,
                "structural_success": report["structural_success"],
                "gyroflow": gyro,
                "selfcheck": sc,
                "warnings": warnings,
                "report": str(work_dir / "report.json"),
            },
            ensure_ascii=False,
        ),
    )

    postprobe_and_log(
        src=src, dst=dst, preset=preset, elapsed=elapsed,
        source_summary=source_summary, ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger, file_logger=file_logger,
    )

    cleanup_work_dir(
        work_dir=work_dir,
        job_id=job_id_for(src),
        preserve_reports=preserve_reports,
        keep_work=keep_work,
        logger=logger,
        file_logger=file_logger,
    )

    return "done"


def encode_one_hw_classic(
    *,
    src: Path,
    dst: Path,
    source_summary: dict[str, Any],
    src_info: SourceInfo,
    vfr: bool,
    profile: dict[str, Any],
    preset: str,
    backend,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    gpac: GpacContainerBackend,
    work_root: Path,
    no_downgrade: bool,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    status_cb: StatusCb | None = None,
    show_progress: bool = True,
) -> str:
    """Non-Sony material: video + audio only, single-tool single pass."""
    part_dst = dst.with_name(dst.stem + ".part.mov")
    safe_unlink(part_dst)

    planned, needs_downgrade = plan_initial_format(
        backend.caps, backend.kind, src_info.chroma, src_info.bit_depth
    )
    log_hw_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        backend=backend, source_summary=source_summary, src_info=src_info,
        vfr=vfr, planned=planned, needs_downgrade=needs_downgrade,
        profile=profile,
    )
    file_logger.info(
        "POLICY | non-Sony source: metadata dropped by policy "
        "(video+audio only)"
    )
    logger.info("[POLICY] %s | non-Sony: metadata dropped by policy", src.name)

    if dry_run:
        cmd, _ = backend.command(
            src, part_dst, profile, planned[0], planned[1],
            vfr, audio_copy=True,
        )
        file_logger.info(
            "DRY-RUN | no encode performed | %s",
            subprocess.list2cmdline(cmd),
        )
        return "dry-run"

    started = time.monotonic()
    work_dir = work_root / job_id_for(src)
    if status_cb:
        status_cb("encoding", f"{planned[0]}/{planned[1]}")
    try:
        warnings, used = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=src, output=part_dst,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=True, do_frame_check=False,
            no_downgrade=no_downgrade, gpac=gpac,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
        )
    except Exception as exc:
        logger.error("[FAIL] hardware encode | %s | %s", src, exc)
        file_logger.exception("HW ENCODE FAILED")
        safe_unlink(part_dst)
        return "failed"

    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
        logger.error("[FAIL] encoder produced no output | %s", src)
        file_logger.error("FAILED | encoder produced no output")
        safe_unlink(part_dst)
        return "failed"

    try:
        os.replace(part_dst, dst)
    except OSError as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
        file_logger.exception("RENAME FAILED")
        safe_unlink(part_dst)
        return "failed"

    elapsed = time.monotonic() - started
    file_logger.info(
        "ENCODE_FORMAT | %s/%s | warnings=%d",
        used[0], used[1], len(warnings),
    )

    postprobe_and_log(
        src=src, dst=dst, preset=preset, elapsed=elapsed,
        source_summary=source_summary, ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger, file_logger=file_logger,
    )
    return "done"


def is_sony_source(streams: list[dict[str, Any]]) -> bool:
    """Sony XAVC detection: a data stream carrying the rtmd codec tag."""
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "rtmd"
        for st in streams
    )


# ---------------------------------------------------------------------------
# batch context + per-file dispatcher + worker schedulers
# ---------------------------------------------------------------------------

@dataclass
class BatchCtx:
    """Everything the per-file hardware handler needs."""

    preset: str
    profile: dict[str, Any]
    ffprobe: Path
    gpac: GpacContainerBackend
    gyroflow: Path | None
    input_root: Path
    output_root: Path
    logs_root: Path
    work_root: Path
    preserve_reports: Path
    preprobe_csv: Path
    preprobe_stream_csv: Path
    postprobe_csv: Path
    postprobe_stream_csv: Path
    multiple_presets: bool = False
    keep_work: bool = False
    no_downgrade: bool = False
    check_level: str = "basic"
    dry_run: bool = False
    failed_path: Path | None = None
    status: DashboardStatus | None = None
    show_progress: bool = True
    warnings_total: list[str] = field(default_factory=list)


def _status_cb_for(
    ctx: BatchCtx, src: Path
) -> Callable[[str, str], None]:
    def cb(status: str, detail: str = "") -> None:
        if ctx.status is not None:
            ctx.status.update(src, status, detail)
    return cb


def process_file_hw(
    ctx: BatchCtx,
    backend,
    src: Path,
    logger: logging.Logger,
) -> str:
    """Full per-file flow for hardware backends: probe -> dispatch ->
    encode -> postprobe -> GC -> failure record. Returns a counter key."""
    dst = output_path_for(
        src, ctx.input_root, ctx.output_root,
        ctx.preset, ctx.multiple_presets,
    )
    file_log = per_file_log_path(
        src, ctx.input_root, ctx.logs_root, ctx.preset
    )
    file_logger = build_file_logger(file_log)
    if ctx.status is not None:
        ctx.status.start(src, ctx.preset, backend.name)

    if dst.is_file() and dst.stat().st_size > 0:
        logger.info("[SKIP] %s | %s", src, dst)
        file_logger.info("SKIP | output already exists | output=%s", dst)
        if ctx.status is not None:
            ctx.status.finish(src, "skipped")
        return "skipped"

    try:
        source_summary, source_streams, src_info, vfr = prepare_source_hw(
            src=src,
            preset=ctx.preset,
            ffprobe=ctx.ffprobe,
            preprobe_csv=ctx.preprobe_csv,
            preprobe_stream_csv=ctx.preprobe_stream_csv,
            file_logger=file_logger,
        )
    except Exception as exc:
        logger.error("[PROBE-FAIL] %s | %s", src, exc)
        file_logger.error("PREPROBE FAILED | %s", exc)
        if ctx.status is not None:
            ctx.status.finish(src, "failed")
        if ctx.failed_path is not None:
            record_failure(
                ctx.failed_path,
                source=src, preset=ctx.preset, backend_name=backend.name,
                stage="probe", error=str(exc), log_path=str(file_log),
            )
        return "failed"

    if is_sony_source(source_streams):
        result = encode_one_sony_hw(
            src=src,
            dst=dst,
            source_summary=source_summary,
            src_info=src_info,
            vfr=vfr,
            profile=ctx.profile,
            preset=ctx.preset,
            backend=backend,
            ffprobe=ctx.ffprobe,
            postprobe_csv=ctx.postprobe_csv,
            postprobe_stream_csv=ctx.postprobe_stream_csv,
            gpac=ctx.gpac,
            gyroflow=ctx.gyroflow,
            work_root=ctx.work_root,
            preserve_reports=ctx.preserve_reports,
            keep_work=ctx.keep_work,
            no_downgrade=ctx.no_downgrade,
            check_level=ctx.check_level,
            logger=logger,
            file_logger=file_logger,
            dry_run=ctx.dry_run,
            status_cb=_status_cb_for(ctx, src),
            show_progress=ctx.show_progress,
        )
    else:
        result = encode_one_hw_classic(
            src=src,
            dst=dst,
            source_summary=source_summary,
            src_info=src_info,
            vfr=vfr,
            profile=ctx.profile,
            preset=ctx.preset,
            backend=backend,
            ffprobe=ctx.ffprobe,
            postprobe_csv=ctx.postprobe_csv,
            postprobe_stream_csv=ctx.postprobe_stream_csv,
            gpac=ctx.gpac,
            work_root=ctx.work_root,
            no_downgrade=ctx.no_downgrade,
            logger=logger,
            file_logger=file_logger,
            dry_run=ctx.dry_run,
            status_cb=_status_cb_for(ctx, src),
            show_progress=ctx.show_progress,
        )

    if result == "failed" and ctx.failed_path is not None:
        record_failure(
            ctx.failed_path,
            source=src, preset=ctx.preset, backend_name=backend.name,
            stage="encode", error="see per-file log",
            log_path=str(file_log),
        )
    if ctx.status is not None:
        ctx.status.finish(src, result)
    return result


def budget_workers(backend_name: str, profile: dict[str, Any]) -> int:
    """Static worker budget per backend + preset weight (measured):
    NVENC quality-class saturates VE at 2 sessions, FAST scales to 3;
    QSV totals peak at 1 session (2 sessions total throughput is lower
    than solo)."""
    if backend_name == "nvenc":
        preset_name = str(profile.get("preset", "")).lower()
        return 3 if preset_name not in ("quality",) else 2
    return 1


def run_hw_pool(
    ctx: BatchCtx,
    backend,
    sources: list[Path],
    logger: logging.Logger,
    workers: int,
) -> dict[str, int]:
    """Single-backend worker pool (threads; work is subprocess-bound)."""
    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    if workers <= 1:
        ctx.show_progress = True
        for src in sources:
            counters[process_file_hw(ctx, backend, src, logger)] += 1
        return counters
    ctx.show_progress = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(process_file_hw, ctx, backend, src, logger): src
            for src in sources
        }
        for fut in as_completed(futures):
            result = fut.result()
            counters[result] += 1
    return counters


def run_multihw_pool(
    ctx: BatchCtx,
    backend_nvenc,
    backend_qsv,
    sources: list[Path],
    logger: logging.Logger,
) -> dict[str, int]:
    """EXPERIMENTAL: dual-backend parallel scheduling.

    Static routing v1: 4:2:2 sources go to NVENC (4:2:2 direct encode),
    everything else alternates between the two backends. Worker counts
    come from budget_workers(). Work stealing is not implemented (see
    questions in the implementation report)."""
    # route by probing chroma cheaply (probe already cached in CSV runs,
    # but routing happens before per-file probe; use ffprobe directly)
    nvenc_sources: list[Path] = []
    qsv_sources: list[Path] = []
    flip = False
    for src in sources:
        chroma = ""
        try:
            summary, _streams = probe_source(ctx.ffprobe, src)
            info = build_source_info(src, summary, _streams)
            chroma = info.chroma
        except Exception:
            pass
        if chroma == "4:2:2":
            nvenc_sources.append(src)
        else:
            (qsv_sources if flip else nvenc_sources).append(src)
            flip = not flip

    ctx.show_progress = False
    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    with ThreadPoolExecutor(
        max_workers=budget_workers("nvenc", ctx.profile) + 1
    ) as ex:
        futures = {}
        for src in nvenc_sources:
            futures[
                ex.submit(process_file_hw, ctx, backend_nvenc, src, logger)
            ] = src
        for src in qsv_sources:
            futures[
                ex.submit(process_file_hw, ctx, backend_qsv, src, logger)
            ] = src
        for fut in as_completed(futures):
            counters[fut.result()] += 1
    return counters
