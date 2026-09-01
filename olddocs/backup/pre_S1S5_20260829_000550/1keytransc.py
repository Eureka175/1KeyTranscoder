#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1KeyTranscoder main entry point (orchestration layer).

Replaces x265_archive.py as the single main program. Non-Sony sources
keep the classic direct-encode pipeline; Sony XAVC sources (detected by
their rtmd timed-metadata track) go through the metadata-preservation
pipeline:

    Original Sony MOV/MP4
        -> probe / SourceManifest (core.probe, unchanged)
        -> parameter scaling (core.scaling, unchanged)
        -> Sony preservation extract (preservation.sony -> bundle)
        -> video encode (encoders.x265, scaled libx265 -> MOV intermediate)
        -> MP4Box -new: video + per-track audio container copy + per-track
           rtmd container copy (native ISOBMFF copies keep source timing
           exact; GPAC's NHML import path is NOT used - it rounds track
           durations at 600-tick precision)
        -> reconstruct: payload verification, tref/cdsc, nrtm meta, brands
        -> uuid byte patch (GPAC cannot write vendor uuid boxes)
        -> validation: preservation.compare + Gyroflow headless parse
        -> deliver <basename>.MP4 (uppercase extension, basename kept)

Architecture
------------
    1keytransc.py            CLI, discovery, batch loop, resume, process
                             execution, postprobe, CSV/log orchestration
    core/                    models, probe, source_classifier, scaling,
                             config, logging_utils (unchanged)
    encoders/base.py         EncoderBackend protocol + pixel-format mapping
    encoders/x265.py         PARAM_MAP, x265 serialization, FFmpeg command
                             construction (full output + video-only
                             intermediate)
    preservation/            Sony metadata bundle extract/reconstruct,
                             GPAC container backend, ISO-BMFF byte patcher,
                             structural validation, Gyroflow validation,
                             pipeline orchestration

x265_archive.py remains temporarily as migration reference only; it is
no longer the entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.config import (
    find_executable,
    load_config,
    load_scaling_config,
    resolve_config_file,
    verify_executable,
)
from core.logging_utils import (
    POST_CSV_FIELDS,
    POST_STREAM_CSV_FIELDS,
    PRE_CSV_FIELDS,
    SCALING_CSV_FIELDS,
    STREAM_CSV_FIELDS,
    append_csv,
    append_rows,
    build_file_logger,
    format_mb,
    make_scaling_csv_row,
    make_stream_rows,
    make_summary_csv_row,
    setup_logger,
    stream_rows_from_raw,
)
from core.models import PRESETS, EffectiveParams, SourceInfo
from core.probe import build_source_info, probe_source
from core.scaling import ScalingEngine
from core.source_classifier import SourceClassifier
from encoders.base import EncoderBackend
from encoders.x265 import X265Backend
from preservation.gpac import GpacContainerBackend
from preservation.gyroflow import find_gyroflow
from preservation.pipeline import run_sony_pipeline


VIDEO_EXTS = {
    ".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".ts", ".mkv",
    ".avi", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg"
}


# ---------------------------------------------------------------------------
# Output / paths
# ---------------------------------------------------------------------------

def safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def output_path_for(
    src: Path,
    input_root: Path,
    output_root: Path,
    preset: str,
    multiple_presets: bool,
) -> Path:
    """Final output path: original basename, uppercase .MP4 extension,
    no suffixes. Source and output roots are distinct, so the source
    can never be overwritten."""
    relative = src.relative_to(input_root)

    if multiple_presets:
        # All presets write the same basename; keep them apart per preset.
        root = output_root / preset.upper()
    else:
        root = output_root

    return (
        root
        / relative.parent
        / f"{relative.stem}.MP4"
    )


def per_file_log_path(
    src: Path,
    input_root: Path,
    logs_root: Path,
    preset: str,
) -> Path:
    relative = src.relative_to(input_root)

    return (
        logs_root
        / "files"
        / relative.parent
        / f"{relative.stem}_{preset.upper()}.log"
    )


def job_id_for(source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^\w.-]+", "_", source.stem)
    return f"{stem}-{digest}"


def is_sony_source(streams: list[dict[str, Any]]) -> bool:
    """Sony XAVC detection: a data stream carrying the rtmd codec tag."""
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "rtmd"
        for st in streams
    )


# ---------------------------------------------------------------------------
# Shared encode helpers
# ---------------------------------------------------------------------------

def run_ffmpeg(
    cmd: list[str],
    raw_log_path: Path,
    total_frames: int,
    file_logger: logging.Logger,
) -> tuple[int | None, float]:
    """Run one FFmpeg command with live progress.

    Returns (return_code, elapsed_sec). return_code is None when the
    run was interrupted or failed to execute (already logged); callers
    treat None like a failure and clean up their own outputs.
    """
    started = time.monotonic()
    last_console = 0.0
    last_logged_progress = 0.0

    proc: subprocess.Popen[str] | None = None

    try:
        with raw_log_path.open(
            "w",
            encoding="utf-8",
            errors="replace",
        ) as raw_log:

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            assert proc.stderr is not None

            for raw_line in proc.stderr:
                line = raw_line.rstrip("\r\n")

                raw_log.write(line + "\n")
                raw_log.flush()

                frame_match = re.search(
                    r"frame=\s*([0-9]+)",
                    line,
                )
                bitrate_match = re.search(
                    r"bitrate=\s*([0-9.]+)\s*kbits/s",
                    line,
                    re.IGNORECASE,
                )

                if not frame_match:
                    continue

                current_frame = int(frame_match.group(1))
                elapsed = time.monotonic() - started

                bitrate = (
                    float(bitrate_match.group(1))
                    if bitrate_match
                    else None
                )

                now = time.monotonic()

                if now - last_console >= 1.0:
                    total = str(total_frames) if total_frames > 0 else "?"
                    bitrate_text = (
                        f"{bitrate:.1f} kbps"
                        if bitrate is not None
                        else "N/A"
                    )

                    print(
                        f"\r[PROGRESS] {current_frame} frames / "
                        f"{total} frames total | "
                        f"{elapsed:.1f} sec elapsed | "
                        f"{bitrate_text}          ",
                        end="",
                        flush=True,
                    )
                    last_console = now

                # Keep a sparse progress history in the per-file log.
                if now - last_logged_progress >= 10.0:
                    file_logger.info(
                        "PROGRESS | frames=%d/%s | elapsed=%.3f sec | "
                        "bitrate=%s kbps",
                        current_frame,
                        total_frames if total_frames > 0 else "?",
                        elapsed,
                        (
                            f"{bitrate:.1f}"
                            if bitrate is not None
                            else "N/A"
                        ),
                    )
                    last_logged_progress = now

            return_code = proc.wait()

        print()
        return return_code, time.monotonic() - started

    except KeyboardInterrupt:
        print()
        file_logger.warning(
            "INTERRUPTED | elapsed=%.3f sec",
            time.monotonic() - started,
        )
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        return None, time.monotonic() - started

    except Exception:
        print()
        file_logger.exception(
            "EXECUTION FAILED | elapsed=%.3f sec",
            time.monotonic() - started,
        )
        return None, time.monotonic() - started


def count_frames(ffprobe: Path, path: Path) -> tuple[int, str]:
    """(decoded packet count, avg_frame_rate) for the first video stream."""
    cmd = [
        str(ffprobe), "-v", "error", "-count_packets",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=nb_read_packets,nb_frames,avg_frame_rate",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr[-500:]}")
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"no video stream in {path}")
    st = streams[0]
    frames = int(st.get("nb_read_packets") or st.get("nb_frames") or 0)
    return frames, str(st.get("avg_frame_rate") or "")


# ---------------------------------------------------------------------------
# Single file: shared preparation (probe -> CSV -> classify -> scale)
# ---------------------------------------------------------------------------

class Prepared:
    def __init__(
        self,
        summary: dict[str, Any],
        streams: list[dict[str, Any]],
        src_info: SourceInfo,
        classification: Any,
        effective: EffectiveParams,
    ) -> None:
        self.summary = summary
        self.streams = streams
        self.src_info = src_info
        self.classification = classification
        self.effective = effective


def prepare_source(
    *,
    src: Path,
    preset: str,
    ffprobe: Path,
    preprobe_csv: Path,
    preprobe_stream_csv: Path,
    scaling_csv: Path,
    profile: dict[str, Any],
    classifier: SourceClassifier,
    engine: ScalingEngine,
    backend: EncoderBackend,
    file_logger: logging.Logger,
) -> Prepared:
    source_summary, source_streams = probe_source(ffprobe, src)

    pre_row = make_summary_csv_row(src, source_summary, preset)
    append_csv(preprobe_csv, PRE_CSV_FIELDS, pre_row)

    append_rows(
        preprobe_stream_csv,
        STREAM_CSV_FIELDS,
        make_stream_rows(src, source_streams, preset),
    )

    # Scaling layer on top of the (unchanged) probe result. Effective
    # parameters are fully resolved BEFORE command construction.
    src_info = build_source_info(src, source_summary, source_streams)
    classification = classifier.classify(src_info)
    effective = engine.build(
        profile,
        preset,
        src_info,
        classification,
        param_order=backend.param_order,
        format_fixed=backend.format_fixed,
    )

    append_csv(
        scaling_csv,
        SCALING_CSV_FIELDS,
        make_scaling_csv_row(src, preset, effective),
    )

    return Prepared(
        source_summary,
        source_streams,
        src_info,
        classification,
        effective,
    )


def log_encode_header(
    *,
    logger: logging.Logger,
    file_logger: logging.Logger,
    src: Path,
    preset: str,
    prepared: Prepared,
    engine: ScalingEngine,
    effective_params: dict[str, Any],
    cmd: list[str],
) -> None:
    source_summary = prepared.summary
    src_info = prepared.src_info
    classification = prepared.classification
    effective = prepared.effective
    source_class = classification.source_class
    normalized_ob = classification.normalized_ob

    logger.info(
        "[START] %s | preset=%s | fps=%.6f | frames=%s | LA-cap=%d",
        src,
        preset,
        source_summary["fps"],
        source_summary["total_frames"] or "?",
        engine.la_cap,
    )
    logger.info(
        "[SOURCE] size=%s MB | duration=%.3f sec | bitrate=%.3f kbps | "
        "streams=%d",
        format_mb(source_summary["source_size_bytes"]),
        source_summary["duration"],
        source_summary["container_bitrate_kbps"],
        source_summary["stream_count"],
    )
    logger.info(
        "[CLASS] %s | class=%s | normalized_ob=%.6f bits/pixel-frame",
        src.name,
        source_class,
        normalized_ob,
    )
    logger.info(
        "[SCALING] spatial=%.6f | temporal=%.6f | pixel_rate=%.6f",
        effective.spatial_factor,
        effective.temporal_factor,
        effective.pixel_rate_factor,
    )
    vbv_audit = effective.audit.get("vbv-maxrate", {})
    if vbv_audit.get("mode") == "dynamic_vbv":
        logger.info(
            "[VBV] OB=%.1f kbps | class=%s | ratios min/target/max="
            "%.2f/%.2f/%.2f | dynamic=%.1f/%.1f/%.1f kbps | "
            "maxrate=%d kbps | bufsize=%d kbps",
            vbv_audit["ob_kbps"],
            source_class,
            vbv_audit["min_ratio"],
            vbv_audit["target_ratio"],
            vbv_audit["max_ratio"],
            vbv_audit["dynamic_min_kbps"],
            vbv_audit["dynamic_target_kbps"],
            vbv_audit["dynamic_max_kbps"],
            vbv_audit["final_maxrate_kbps"],
            vbv_audit["final_bufsize_kbps"],
        )
    else:
        logger.info(
            "[VBV] static base-profile VBV in use | maxrate=%s kbps",
            effective_params.get("vbv-maxrate", "?"),
        )
    logger.info("[COMMAND] %s", subprocess.list2cmdline(cmd))

    file_logger.info(
        "SOURCE | path=%s | size=%s MB | duration=%.3f sec | fps=%.9f | "
        "frames=%s",
        src,
        format_mb(source_summary["source_size_bytes"]),
        source_summary["duration"],
        source_summary["fps"],
        source_summary["total_frames"] or "?",
    )
    file_logger.info(
        "SOURCE_FORMAT | %sx%s | codec=%s | profile=%s | pix_fmt=%s | "
        "bit_depth=%d | chroma=%s | OB=%.3f kbps | "
        "video_bitrate=%.3f kbps",
        src_info.width,
        src_info.height,
        src_info.codec,
        src_info.profile,
        src_info.pix_fmt,
        src_info.bit_depth,
        src_info.chroma,
        src_info.ob_kbps,
        src_info.video_bitrate_kbps,
    )
    file_logger.info(
        "CLASSIFICATION | source_class=%s | normalized_ob=%.6f "
        "bits/pixel-frame | evidence=%s",
        source_class,
        normalized_ob,
        classification.evidence,
    )
    file_logger.info(
        "SCALING | spatial_factor=%.6f | temporal_factor=%.6f | "
        "pixel_rate_factor=%.6f",
        effective.spatial_factor,
        effective.temporal_factor,
        effective.pixel_rate_factor,
    )
    for xkey in sorted(effective.audit):
        file_logger.info(
            "SCALED_PARAM | %s | %s",
            xkey,
            json.dumps(
                effective.audit[xkey],
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    file_logger.info("COMMAND | %s", subprocess.list2cmdline(cmd))
    file_logger.info(
        "EFFECTIVE_X265 | %s",
        json.dumps(effective_params, ensure_ascii=False, sort_keys=True),
    )


# ---------------------------------------------------------------------------
# Single file: postprobe + CSV (shared by both pipelines)
# ---------------------------------------------------------------------------

def postprobe_and_log(
    *,
    src: Path,
    dst: Path,
    preset: str,
    elapsed: float,
    source_summary: dict[str, Any],
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> str:
    post_status = "done"
    error_text = ""

    try:
        output_summary, output_streams = probe_source(ffprobe, dst)
    except Exception as exc:
        output_summary = {}
        output_streams = []
        post_status = "done_postprobe_failed"
        error_text = str(exc)

        logger.error("[POST-PROBE-FAIL] %s | %s", dst, exc)
        file_logger.exception("POSTPROBE FAILED")
    else:
        source_size = source_summary["source_size_bytes"]
        output_size = output_summary["source_size_bytes"]

        ratio = output_size / source_size if source_size > 0 else 0.0

        logger.info(
            "[DONE] elapsed=%.3f sec | "
            "source=%.3f MB -> output=%.3f MB | ratio=%.3f%%",
            elapsed,
            source_size / (1024 ** 2),
            output_size / (1024 ** 2),
            ratio * 100.0,
        )

        file_logger.info(
            "DONE | elapsed=%.3f sec | source=%.3f MB | "
            "output=%.3f MB | ratio=%.6f | saved=%.6f",
            elapsed,
            source_size / (1024 ** 2),
            output_size / (1024 ** 2),
            ratio,
            1.0 - ratio,
        )

    source_size = source_summary["source_size_bytes"]
    output_size = (
        int(output_summary.get("source_size_bytes", 0))
        if output_summary
        else (dst.stat().st_size if dst.is_file() else 0)
    )

    ratio = output_size / source_size if source_size > 0 else 0.0

    post_row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset": preset,
        "source": str(src),
        "output": str(dst),
        "status": post_status,
        "elapsed_sec": f"{elapsed:.3f}",

        "source_size_bytes": source_size,
        "source_size_mb": format_mb(source_size),
        "source_size_gib": f"{source_size / (1024 ** 3):.6f}",

        "output_size_bytes": output_size,
        "output_size_mb": format_mb(output_size),
        "output_size_gib": f"{output_size / (1024 ** 3):.6f}",
        "output_source_ratio": f"{ratio:.6f}",
        "space_saved_ratio": f"{1.0 - ratio:.6f}",

        "source_fps": f"{source_summary['fps']:.9f}",
        "source_duration_sec": f"{source_summary['duration']:.6f}",
        "source_frames_est": source_summary["total_frames"],

        "output_fps": f"{output_summary.get('fps', 0.0):.9f}",
        "output_duration_sec": f"{output_summary.get('duration', 0.0):.6f}",
        "output_frames_est": output_summary.get("total_frames", 0),

        "output_width": output_summary.get("width", 0),
        "output_height": output_summary.get("height", 0),
        "output_pix_fmt": output_summary.get("pix_fmt", ""),
        "output_codec": output_summary.get("codec", ""),
        "output_profile": output_summary.get("profile", ""),
        "output_r_frame_rate": output_summary.get("r_frame_rate", ""),
        "output_avg_frame_rate": output_summary.get("avg_frame_rate", ""),
        "output_stream_count": output_summary.get("stream_count", 0),
        "output_video_streams": output_summary.get("video_streams", 0),
        "output_audio_streams": output_summary.get("audio_streams", 0),
        "output_subtitle_streams": output_summary.get("subtitle_streams", 0),
        "output_data_streams": output_summary.get("data_streams", 0),
        "output_attachment_streams": output_summary.get(
            "attachment_streams", 0,
        ),
        "output_audio_codecs": output_summary.get("audio_codecs", ""),
        "output_subtitle_codecs": output_summary.get("subtitle_codecs", ""),
        "output_data_codecs": output_summary.get("data_codecs", ""),
        "output_attachment_codecs": output_summary.get(
            "attachment_codecs", "",
        ),
        "error": error_text,
    }

    append_csv(postprobe_csv, POST_CSV_FIELDS, post_row)

    if output_streams:
        append_rows(
            postprobe_stream_csv,
            STREAM_CSV_FIELDS,
            [
                {
                    **row,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "preset": preset,
                    "source": str(src),
                    "source_name": src.name,
                    "output": str(dst),
                }
                for row in stream_rows_from_raw(output_streams, src, preset, output=dst)
            ],
        )

    return post_status


# ---------------------------------------------------------------------------
# Single file: classic direct-encode pipeline (non-Sony sources)
# ---------------------------------------------------------------------------

def encode_one(
    *,
    src: Path,
    dst: Path,
    part_dst: Path,
    prepared: Prepared,
    ffmpeg: Path,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    profile: dict[str, Any],
    preset: str,
    backend: EncoderBackend,
    engine: ScalingEngine,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    safe_unlink(part_dst)
    safe_unlink(part_dst.with_name(part_dst.name + ".ffmpeg.log"))

    source_summary = prepared.summary

    cmd, effective_params = backend.build_command(
        ffmpeg,
        src,
        part_dst,
        profile,
        prepared.effective,
        source_summary["video_streams"],
        prepared.src_info,
    )

    log_encode_header(
        logger=logger,
        file_logger=file_logger,
        src=src,
        preset=preset,
        prepared=prepared,
        engine=engine,
        effective_params=effective_params,
        cmd=cmd,
    )

    if dry_run:
        file_logger.info("DRY-RUN | no encode performed.")
        return "dry-run"

    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = part_dst.with_name(part_dst.name + ".ffmpeg.log")

    return_code, elapsed = run_ffmpeg(
        cmd,
        ffmpeg_log,
        source_summary["total_frames"],
        file_logger,
    )

    if return_code is None:
        logger.warning("[INTERRUPTED/EXEC-FAIL] %s", src)
        safe_unlink(part_dst)
        return "failed"

    if return_code != 0:
        logger.error(
            "[FAIL] return=%d | elapsed=%.3f sec | %s",
            return_code,
            elapsed,
            src,
        )
        file_logger.error(
            "FAILED | return=%d | elapsed=%.3f sec",
            return_code,
            elapsed,
        )
        safe_unlink(part_dst)
        return "failed"

    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
        logger.error(
            "[FAIL] ffmpeg returned 0 but output is missing/empty | %s",
            src,
        )
        file_logger.error(
            "FAILED | ffmpeg returned 0 but output missing/empty",
        )
        safe_unlink(part_dst)
        return "failed"

    try:
        os.replace(part_dst, dst)
    except OSError as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
        file_logger.exception("RENAME FAILED | elapsed=%.3f sec", elapsed)
        return "failed"

    postprobe_and_log(
        src=src,
        dst=dst,
        preset=preset,
        elapsed=elapsed,
        source_summary=source_summary,
        ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger,
        file_logger=file_logger,
    )

    safe_unlink(ffmpeg_log)
    return "done"


# ---------------------------------------------------------------------------
# Single file: Sony metadata-preservation pipeline
# ---------------------------------------------------------------------------

def encode_one_sony(
    *,
    src: Path,
    dst: Path,
    prepared: Prepared,
    profile: dict[str, Any],
    preset: str,
    backend: X265Backend,
    engine: ScalingEngine,
    ffmpeg: Path,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None,
    work_root: Path,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    source_summary = prepared.summary
    work_dir = work_root / job_id_for(src)
    encoded_mov = work_dir / "video" / "encoded.mov"

    cmd, effective_params = backend.build_video_command(
        ffmpeg,
        src,
        encoded_mov,
        profile,
        prepared.effective,
        prepared.src_info,
    )

    log_encode_header(
        logger=logger,
        file_logger=file_logger,
        src=src,
        preset=preset,
        prepared=prepared,
        engine=engine,
        effective_params=effective_params,
        cmd=cmd,
    )
    logger.info("[PRESERVE] Sony rtmd source -> preservation pipeline")
    file_logger.info(
        "PRESERVATION | sony | work_dir=%s",
        work_dir,
    )

    if dry_run:
        file_logger.info("DRY-RUN | no encode performed.")
        return "dry-run"

    started = time.monotonic()

    def encode_video(source: Path, out_mov: Path) -> None:
        out_mov.parent.mkdir(parents=True, exist_ok=True)
        raw_log = out_mov.with_name(out_mov.name + ".ffmpeg.log")
        rc, _ = run_ffmpeg(
            cmd,
            raw_log,
            source_summary["total_frames"],
            file_logger,
        )
        if rc is None:
            raise RuntimeError("video encode interrupted/failed to run")
        if rc != 0:
            raise RuntimeError(
                f"video encode failed (rc={rc}), see {raw_log}"
            )
        if not out_mov.is_file() or out_mov.stat().st_size <= 0:
            raise RuntimeError("video encode produced no output")

        # Hard 1:1 timeline requirement: same frame count, same rate.
        out_frames, out_fps = count_frames(ffprobe, out_mov)
        src_frames = source_summary["total_frames"]
        if src_frames and out_frames != src_frames:
            raise RuntimeError(
                f"frame count mismatch: source {src_frames} vs "
                f"encoded {out_frames}"
            )
        src_fps = source_summary["avg_frame_rate"]
        if src_fps and out_fps != src_fps:
            raise RuntimeError(
                f"frame rate mismatch: source {src_fps} vs "
                f"encoded {out_fps}"
            )

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
            src,
            report.get("critical_missing"),
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
            src,
            gyro.get("detail"),
        )
        file_logger.error(
            "GYROFLOW VALIDATION FAILED | %s",
            gyro.get("detail"),
        )
        return "failed"

    # Deliver the rebuilt container.
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
    logger.info(
        "[PRESERVE-OK] %s | PRESERVED=%d MODIFIED=%d MISSING=%d | "
        "gyroflow=%s",
        src.name,
        s["PRESERVED"],
        s["MODIFIED"],
        s["MISSING"],
        gyro.get("status") if gyro else "not-run",
    )
    file_logger.info(
        "PRESERVATION_REPORT | %s",
        json.dumps(
            {
                "summary": s,
                "structural_success": report["structural_success"],
                "gyroflow": gyro,
                "report": str(work_dir / "report.json"),
            },
            ensure_ascii=False,
        ),
    )

    postprobe_and_log(
        src=src,
        dst=dst,
        preset=preset,
        elapsed=elapsed,
        source_summary=source_summary,
        ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger,
        file_logger=file_logger,
    )

    return "done"


# ---------------------------------------------------------------------------
# Discovery / arguments / main
# ---------------------------------------------------------------------------

def discover_sources(root: Path) -> list[Path]:
    return sorted(
        (
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ),
        key=lambda p: str(p).lower(),
    )


def format_path_relation(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "1KeyTranscoder: recursive, resumable x265 archive encoder "
            "with Sony camera-metadata preservation."
        ),
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Input root directory.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output root directory.",
    )
    parser.add_argument(
        "--preset",
        choices=[p.lower() for p in PRESETS] + ["all"],
        default="hq",
        help="UHQ/HQ/SMALL/FAST/all. Default: HQ.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to x265 base profile JSON "
            "(default: x265.json beside the script)."
        ),
    )
    parser.add_argument(
        "--scaling-config",
        default=None,
        help=(
            "Path to x265 scaling rules JSON "
            "(default: x265_scaling.json beside the script)."
        ),
    )
    parser.add_argument(
        "--ffmpeg",
        default=None,
        help="Explicit ffmpeg.exe path.",
    )
    parser.add_argument(
        "--ffprobe",
        default=None,
        help="Explicit ffprobe.exe path.",
    )
    parser.add_argument(
        "--gpac-dir",
        default=None,
        help=(
            "GPAC installation directory containing mp4box.exe "
            "(default: C:\\Program Files\\GPAC). Required for Sony "
            "metadata preservation."
        ),
    )
    parser.add_argument(
        "--gyroflow",
        default=None,
        help=(
            "Explicit Gyroflow.exe path for the headless consumer "
            "validation of preserved Sony gyro metadata. Auto-detected "
            "when omitted; validation is skipped when not found."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe and print commands without encoding.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()

    if not input_root.is_dir():
        print(
            f"[FATAL] input directory not found: {input_root}",
            file=sys.stderr,
        )
        return 2

    if output_root == input_root or format_path_relation(
        output_root,
        input_root,
    ):
        print(
            "[FATAL] output directory must not be inside input directory.",
            file=sys.stderr,
        )
        return 2

    try:
        config_path = resolve_config_file(
            script_dir,
            args.config,
            "x265.json",
        )
        config = load_config(config_path)

        scaling_path = resolve_config_file(
            script_dir,
            args.scaling_config,
            "x265_scaling.json",
        )
        scaling_config = load_scaling_config(scaling_path)

        classifier = SourceClassifier(scaling_config)
        engine = ScalingEngine(scaling_config)
        backend = X265Backend()

        ffmpeg = find_executable("ffmpeg", script_dir, args.ffmpeg)
        ffprobe = find_executable("ffprobe", script_dir, args.ffprobe)

        verify_executable(ffmpeg, "ffmpeg")
        verify_executable(ffprobe, "ffprobe")

        gpac = GpacContainerBackend(
            Path(args.gpac_dir) if args.gpac_dir else None
        )
        gyroflow = find_gyroflow(
            Path(args.gyroflow) if args.gyroflow else None
        )

    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    output_root.mkdir(parents=True, exist_ok=True)

    logs_root = input_root.parent / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    total_log = logs_root / "total.log"
    preprobe_csv = logs_root / "preprobe.csv"
    preprobe_stream_csv = logs_root / "preprobe_streams.csv"
    postprobe_csv = logs_root / "postprobe.csv"
    postprobe_stream_csv = logs_root / "postprobe_streams.csv"
    scaling_csv = logs_root / "scaling.csv"

    work_root = output_root / ".1ktwork"

    logger = setup_logger(total_log)

    requested = (
        list(PRESETS)
        if args.preset == "all"
        else [args.preset.upper()]
    )

    sources = discover_sources(input_root)

    multiple_presets = len(requested) > 1

    logger.info("============================================================")
    logger.info("1KeyTranscoder batch start")
    logger.info("Input : %s", input_root)
    logger.info("Output: %s", output_root)
    logger.info("Logs  : %s", logs_root)
    logger.info("Work  : %s", work_root)
    logger.info("Config: %s", config_path)
    logger.info("Scaling config: %s", scaling_path)
    logger.info("Scaling enabled: %s", engine.enabled)
    logger.info("FFmpeg: %s", ffmpeg)
    logger.info("FFprobe: %s", ffprobe)
    logger.info("GPAC  : %s", gpac.version())
    logger.info(
        "Gyroflow: %s",
        gyroflow if gyroflow else "not found (consumer validation skipped)",
    )
    logger.info("Backend: %s", backend.name)
    logger.info("Presets: %s", ", ".join(requested))
    logger.info("Files: %d", len(sources))
    logger.info(
        "LA cap: %d frames (x265_scaling.json "
        "param_rules.rc_lookahead.cap)",
        engine.la_cap,
    )
    logger.info("============================================================")

    counters = {
        "done": 0,
        "skipped": 0,
        "failed": 0,
        "dry-run": 0,
    }

    for preset in requested:
        profile = config["profile"][preset]

        logger.info("---------- PRESET %s ----------", preset)

        for src in sources:
            dst = output_path_for(
                src,
                input_root,
                output_root,
                preset,
                multiple_presets,
            )
            part_dst = dst.with_name(dst.stem + ".part.mov")

            file_log = per_file_log_path(src, input_root, logs_root, preset)
            file_logger = build_file_logger(file_log)

            if dst.is_file() and dst.stat().st_size > 0:
                logger.info("[SKIP] %s | %s", src, dst)
                file_logger.info(
                    "SKIP | output already exists | output=%s",
                    dst,
                )
                counters["skipped"] += 1
                continue

            try:
                prepared = prepare_source(
                    src=src,
                    preset=preset,
                    ffprobe=ffprobe,
                    preprobe_csv=preprobe_csv,
                    preprobe_stream_csv=preprobe_stream_csv,
                    scaling_csv=scaling_csv,
                    profile=profile,
                    classifier=classifier,
                    engine=engine,
                    backend=backend,
                    file_logger=file_logger,
                )
            except Exception as exc:
                logger.error("[PROBE-FAIL] %s | %s", src, exc)
                file_logger.error("PREPROBE FAILED | %s", exc)
                counters["failed"] += 1
                continue

            if is_sony_source(prepared.streams):
                result = encode_one_sony(
                    src=src,
                    dst=dst,
                    prepared=prepared,
                    profile=profile,
                    preset=preset,
                    backend=backend,
                    engine=engine,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    postprobe_csv=postprobe_csv,
                    postprobe_stream_csv=postprobe_stream_csv,
                    gpac=gpac,
                    gyroflow=gyroflow,
                    work_root=work_root,
                    logger=logger,
                    file_logger=file_logger,
                    dry_run=args.dry_run,
                )
            else:
                result = encode_one(
                    src=src,
                    dst=dst,
                    part_dst=part_dst,
                    prepared=prepared,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    postprobe_csv=postprobe_csv,
                    postprobe_stream_csv=postprobe_stream_csv,
                    profile=profile,
                    preset=preset,
                    backend=backend,
                    engine=engine,
                    logger=logger,
                    file_logger=file_logger,
                    dry_run=args.dry_run,
                )

            counters[result] += 1

    logger.info("============================================================")
    logger.info(
        "Finished | done=%d skipped=%d failed=%d dry-run=%d",
        counters["done"],
        counters["skipped"],
        counters["failed"],
        counters["dry-run"],
    )
    logger.info("Total log: %s", total_log)
    logger.info("Preprobe CSV: %s", preprobe_csv)
    logger.info("Postprobe CSV: %s", postprobe_csv)
    logger.info("Scaling CSV: %s", scaling_csv)
    logger.info("============================================================")

    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
