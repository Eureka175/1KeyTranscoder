#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1KeyTranscoder — 主入口 (thin orchestrator).

硬件后端 (NVEncC/QSVEncC) 的批量逻辑已抽出至 core/batch_hw.py;
x265 路径作为显式手动选项保留在本文件 (legacy, 永不自动回退软件).

典型用法:
    python 1kt.py --input <dir> --output <dir> --encoder nvenc --preset hq
    python 1kt.py ... --check basic|advanced|full
    python 1kt.py ... --jobs auto          (单后端自适应调度)
    python 1kt.py ... --experimental-multihw   (实验性双后端并行)
    python 1kt.py ... --headless           (无人值守)
    python 1kt.py --input <dir> --output <dir> --retry-list failed_files.json --encoder x265
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.batch_hw import (
    BatchCtx,
    detect_vfr,
    hw_backend_for,
    is_dji_source,
    run_hw_pool,
    run_multihw_pool,
    load_retry_list,
    record_failure,
)
from core.config import (
    find_executable,
    load_config,
    load_json_file,
    load_scaling_config,
    load_svtav1_config,
    resolve_config_file,
    verify_executable,
)
from core.dashboard import DashboardStatus
from core.logging_utils import (
    SCALING_CSV_FIELDS,
    append_csv,
    build_file_logger,
    make_scaling_csv_row,
    setup_logger,
)
from core.models import PRESETS, EffectiveParams
from core.paths import (
    discover_sources,
    format_path_relation,
    output_path_for,
    per_file_log_path,
)
from core.postprobe import postprobe_and_log
from core.probe import build_source_info, count_frames, probe_source
from core.scaling import ScalingEngine
from core.source_classifier import SourceClassifier
from encoders.svtav1 import SvtAv1Backend
from encoders.x265 import X265Backend
from preservation.gpac import GpacContainerBackend
from preservation.gyroflow import find_gyroflow
from preservation.pipeline import run_sony_pipeline

EXPERIMENTAL_BANNER = (
    "=" * 72
    + "\n[EXPERIMENTAL] --experimental-multihw 已启用:\n"
    + "  多硬件后端并行属于实验性功能, 无法保证视频编码质量一致性.\n"
    + "  如需质量一致性保证, 请使用单后端模式 (默认).\n"
    + "=" * 72
)


# ---------------------------------------------------------------------------
# x265 legacy path (manual --encoder x265 only; never auto-selected)
# ---------------------------------------------------------------------------

def run_ffmpeg(
    cmd: list[str],
    raw_log_path: Path,
    total_frames: int,
    file_logger: logging.Logger,
) -> tuple[int | None, float]:
    """Run one FFmpeg command with live progress."""
    import re
    import time

    started = time.monotonic()
    last_console = 0.0
    last_logged_progress = 0.0

    proc: subprocess.Popen[str] | None = None
    try:
        with raw_log_path.open("w", encoding="utf-8", errors="replace") as raw_log:
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

                frame_match = re.search(r"frame=\s*([0-9]+)", line)
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
                    try:
                        print(
                            f"\r[PROGRESS] {current_frame} frames / "
                            f"{total} frames total | "
                            f"{elapsed:.1f} sec elapsed | "
                            f"{bitrate_text}          ",
                            end="",
                            flush=True,
                        )
                    except OSError:
                        pass
                    last_console = now
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
        try:
            print()
        except OSError:
            pass
        return return_code, time.monotonic() - started
    except KeyboardInterrupt:
        try:
            print()
        except OSError:
            pass
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
        try:
            print()
        except OSError:
            pass
        file_logger.exception(
            "EXECUTION FAILED | elapsed=%.3f sec",
            time.monotonic() - started,
        )
        return None, time.monotonic() - started


class Prepared:
    def __init__(
        self,
        summary: dict[str, Any],
        streams: list[dict[str, Any]],
        src_info,
        classification,
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
    classifier,
    engine,
    backend,
    file_logger: logging.Logger,
) -> Prepared:
    from core.logging_utils import (
        PRE_CSV_FIELDS,
        STREAM_CSV_FIELDS,
        append_rows,
        make_stream_rows,
        make_summary_csv_row,
    )

    source_summary, source_streams = probe_source(ffprobe, src)
    append_csv(
        preprobe_csv, PRE_CSV_FIELDS,
        make_summary_csv_row(src, source_summary, preset),
    )
    append_rows(
        preprobe_stream_csv, STREAM_CSV_FIELDS,
        make_stream_rows(src, source_streams, preset),
    )

    src_info = build_source_info(src, source_summary, source_streams)
    classification = classifier.classify(src_info)
    effective = engine.build(
        profile, preset, src_info, classification,
        param_order=backend.param_order,
        format_fixed=backend.format_fixed,
    )
    append_csv(
        scaling_csv, SCALING_CSV_FIELDS,
        make_scaling_csv_row(src, preset, effective),
    )
    return Prepared(
        source_summary, source_streams, src_info,
        classification, effective,
    )


def log_encode_header(
    *,
    logger: logging.Logger,
    file_logger: logging.Logger,
    src: Path,
    preset: str,
    prepared: Prepared,
    engine,
    effective_params: dict[str, Any],
    cmd: list[str],
) -> None:
    from core.logging_utils import format_mb

    source_summary = prepared.summary
    src_info = prepared.src_info
    classification = prepared.classification
    effective = prepared.effective

    logger.info(
        "[START] %s | preset=%s | fps=%.6f | frames=%s | LA-cap=%d",
        src, preset, source_summary["fps"],
        source_summary["total_frames"] or "?", engine.la_cap,
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
        src.name, classification.source_class, classification.normalized_ob,
    )
    logger.info(
        "[SCALING] spatial=%.6f | temporal=%.6f | pixel_rate=%.6f",
        effective.spatial_factor, effective.temporal_factor,
        effective.pixel_rate_factor,
    )
    logger.info("[COMMAND] %s", subprocess.list2cmdline(cmd))
    file_logger.info(
        "SOURCE | path=%s | size=%s MB | duration=%.3f sec | fps=%.9f | "
        "frames=%s",
        src, format_mb(source_summary["source_size_bytes"]),
        source_summary["duration"], source_summary["fps"],
        source_summary["total_frames"] or "?",
    )
    file_logger.info(
        "SOURCE_FORMAT | %sx%s | codec=%s | profile=%s | pix_fmt=%s | "
        "bit_depth=%d | chroma=%s | OB=%.3f kbps",
        src_info.width, src_info.height, src_info.codec, src_info.profile,
        src_info.pix_fmt, src_info.bit_depth, src_info.chroma,
        src_info.ob_kbps,
    )
    file_logger.info(
        "CLASSIFICATION | source_class=%s | normalized_ob=%.6f | "
        "evidence=%s",
        classification.source_class, classification.normalized_ob,
        classification.evidence,
    )
    file_logger.info("COMMAND | %s", subprocess.list2cmdline(cmd))
    file_logger.info(
        "EFFECTIVE_X265 | %s",
        json.dumps(effective_params, ensure_ascii=False, sort_keys=True),
    )


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
    backend,
    engine,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    from core.paths import safe_unlink

    safe_unlink(part_dst)
    safe_unlink(part_dst.with_name(part_dst.name + ".ffmpeg.log"))

    source_summary = prepared.summary
    cmd, effective_params = backend.build_command(
        ffmpeg, src, part_dst, profile, prepared.effective,
        source_summary["video_streams"], prepared.src_info,
    )
    log_encode_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        prepared=prepared, engine=engine,
        effective_params=effective_params, cmd=cmd,
    )
    if dry_run:
        file_logger.info("DRY-RUN | no encode performed.")
        return "dry-run"

    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = part_dst.with_name(part_dst.name + ".ffmpeg.log")
    return_code, elapsed = run_ffmpeg(
        cmd, ffmpeg_log, source_summary["total_frames"], file_logger
    )
    if return_code is None:
        logger.warning("[INTERRUPTED/EXEC-FAIL] %s", src)
        safe_unlink(part_dst)
        return "failed"
    if return_code != 0:
        logger.error("[FAIL] return=%d | elapsed=%.3f sec | %s",
                     return_code, elapsed, src)
        file_logger.error("FAILED | return=%d | elapsed=%.3f sec",
                          return_code, elapsed)
        safe_unlink(part_dst)
        return "failed"
    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
        logger.error("[FAIL] ffmpeg returned 0 but output is missing/empty | %s",
                     src)
        file_logger.error("FAILED | ffmpeg returned 0 but output missing/empty")
        safe_unlink(part_dst)
        return "failed"
    try:
        os.replace(part_dst, dst)
    except OSError as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
        file_logger.exception("RENAME FAILED | elapsed=%.3f sec", elapsed)
        return "failed"

    postprobe_and_log(
        src=src, dst=dst, preset=preset, elapsed=elapsed,
        source_summary=source_summary, ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger, file_logger=file_logger,
    )
    safe_unlink(ffmpeg_log)
    return "done"


def encode_one_sony(
    *,
    src: Path,
    dst: Path,
    prepared: Prepared,
    profile: dict[str, Any],
    preset: str,
    backend,
    engine,
    ffmpeg: Path,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None,
    work_root: Path,
    check_level: str = "basic",
    codec: str = "hevc",
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    """Sony preservation path (rtmd) for ffmpeg software backends
    (x265 / svtav1; --check levels apply like on hardware backends).
    codec="av1" 保留元数据但不恢复 XAVC brand (AV1 不在 XAVC 规范内)。"""
    import time

    from core.paths import job_id_for, safe_unlink

    source_summary = prepared.summary
    work_dir = work_root / job_id_for(src)
    # AV1 中间文件必须是 MP4 (ffmpeg 9 的 MOV muxer 不接受 AV1;
    # GPAC 对 MP4/MOV 一视同仁, 时间轴保真不受影响)
    encoded_mov = work_dir / "video" / (
        "encoded.mp4" if codec == "av1" else "encoded.mov"
    )

    cmd, effective_params = backend.build_video_command(
        ffmpeg, src, encoded_mov, profile,
        prepared.effective, prepared.src_info,
    )
    log_encode_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        prepared=prepared, engine=engine,
        effective_params=effective_params, cmd=cmd,
    )
    logger.info("[PRESERVE] Sony rtmd source -> preservation pipeline")
    file_logger.info("PRESERVATION | sony | work_dir=%s", work_dir)

    if dry_run:
        file_logger.info("DRY-RUN | no encode performed.")
        return "dry-run"

    started = time.monotonic()

    def encode_video(source: Path, out_mov: Path) -> None:
        out_mov.parent.mkdir(parents=True, exist_ok=True)
        raw_log = out_mov.with_name(out_mov.name + ".ffmpeg.log")
        rc, _ = run_ffmpeg(
            cmd, raw_log, source_summary["total_frames"], file_logger
        )
        if rc is None:
            raise RuntimeError("video encode interrupted/failed to run")
        if rc != 0:
            raise RuntimeError(f"video encode failed (rc={rc}), see {raw_log}")
        if not out_mov.is_file() or out_mov.stat().st_size <= 0:
            raise RuntimeError("video encode produced no output")
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
                f"frame rate mismatch: source {src_fps} vs encoded {out_fps}"
            )

    def pipe_log(msg: str) -> None:
        logger.info("[SONY] %s", msg)
        file_logger.info("PIPELINE | %s", msg)

    try:
        if codec == "av1":
            logger.info(
                "[POLICY] %s | AV1 保留管线: rtmd/nrtm/uuid 元数据保留, "
                "不打 XAVC tag (AV1 不在 XAVC 规范内)",
                src.name,
            )
            file_logger.info(
                "POLICY | AV1 Sony preserve: rtmd/nrtm/uuid kept, "
                "XAVC brand NOT restored (AV1 not in XAVC spec)"
            )
        report = run_sony_pipeline(
            source=src,
            work_dir=work_dir,
            encode_video=encode_video,
            gpac=gpac,
            ffprobe=ffprobe,
            has_audio=source_summary["audio_streams"] > 0,
            gyroflow=gyroflow,
            fix_hw_timing=False,
            check_level=check_level,
            codec=codec,
            encoded_path=encoded_mov,
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
        logger.error("[FAIL] Gyroflow consumer validation | %s | %s",
                     src, gyro.get("detail"))
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
    logger.info(
        "[PRESERVE-OK] %s | PRESERVED=%d MODIFIED=%d MISSING=%d | "
        "gyroflow=%s",
        src.name, s["PRESERVED"], s["MODIFIED"], s["MISSING"],
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
        src=src, dst=dst, preset=preset, elapsed=elapsed,
        source_summary=source_summary, ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger, file_logger=file_logger,
    )
    return "done"


def encode_one_dji_x265(
    *,
    src: Path,
    dst: Path,
    prepared: Prepared,
    profile: dict[str, Any],
    preset: str,
    backend,
    engine,
    ffmpeg: Path,
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None,
    work_root: Path,
    check_level: str = "basic",
    video_entry: str = "hvc1",
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    """DJI preservation path for ffmpeg software backends (x265/svtav1).

    Video-only encode (video intermediate is an FFmpeg MOV — no rigaya
    millisecond quantization, so fix_hw_timing=False) + shared GPAC
    rebuild (djmd/dbgi/tmcd native copies) + dji check.
    video_entry: "hvc1" (HEVC) / "av01" (AV1)."""
    import time

    from core.paths import job_id_for, safe_unlink
    from preservation import dji

    source_summary = prepared.summary
    work_dir = work_root / job_id_for(src)
    # AV1 中间文件必须是 MP4 (ffmpeg 9 的 MOV muxer 不接受 AV1)
    encoded_mov = work_dir / "video" / (
        "encoded.mp4" if video_entry == "av01" else "encoded.mov"
    )

    cmd, effective_params = backend.build_video_command(
        ffmpeg, src, encoded_mov, profile,
        prepared.effective, prepared.src_info,
    )
    log_encode_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        prepared=prepared, engine=engine,
        effective_params=effective_params, cmd=cmd,
    )
    file_logger.info(
        "POLICY | DJI source (djmd): %s 保留管线 "
        "(视频重编码 + djmd/dbgi/tmcd 原生保留)",
        backend.name,
    )
    logger.info(
        "[POLICY] %s | DJI: %s 保留管线, djmd/dbgi/tmcd 原生保留",
        src.name, backend.name,
    )

    if dry_run:
        file_logger.info("DRY-RUN | no encode performed.")
        return "dry-run"

    started = time.monotonic()

    def pipe_log(msg: str) -> None:
        logger.info("[DJI] %s", msg)
        file_logger.info("DJI_PIPELINE | %s", msg)

    try:
        encoded_mov.parent.mkdir(parents=True, exist_ok=True)
        raw_log = encoded_mov.with_name(encoded_mov.name + ".ffmpeg.log")
        rc, _ = run_ffmpeg(
            cmd, raw_log, source_summary["total_frames"], file_logger
        )
        if rc is None or rc != 0:
            raise RuntimeError(f"video encode failed (rc={rc})")
        if not encoded_mov.is_file() or encoded_mov.stat().st_size <= 0:
            raise RuntimeError("video encode produced no output")
        out_frames, out_fps = count_frames(ffprobe, encoded_mov)
        src_frames = source_summary["total_frames"]
        if src_frames and out_frames != src_frames:
            raise RuntimeError(
                f"frame count mismatch: source {src_frames} vs "
                f"encoded {out_frames}"
            )
        pipe_log(f"video ready: {encoded_mov}")

        report = dji.dji_rebuild(
            original=src,
            encoded_mov=encoded_mov,
            work_dir=work_dir,
            gpac=gpac,
            ffprobe=ffprobe,
            gyroflow=gyroflow,
            vfr=detect_vfr(prepared.src_info),
            level=check_level,
            fix_hw_timing=False,
            video_entry=video_entry,
            log=pipe_log,
        )
    except Exception as exc:
        logger.error("[FAIL] dji x265 pipeline | %s | %s", src, exc)
        file_logger.exception("DJI X265 PIPELINE FAILED")
        return "failed"

    if not report.get("structural_success"):
        logger.error(
            "[FAIL] dji structural preservation failed | %s | "
            "missing=%s modified=%s",
            src, report.get("critical_missing"),
            report.get("critical_modified"),
        )
        file_logger.error(
            "DJI STRUCTURAL VALIDATION FAILED | missing=%s | modified=%s",
            report.get("critical_missing"), report.get("critical_modified"),
        )
        return "failed"

    dst.parent.mkdir(parents=True, exist_ok=True)
    final = work_dir / "final" / "output.mov"
    try:
        safe_unlink(dst)
        os.replace(final, dst)
    except OSError as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", final, dst, exc)
        file_logger.exception(
            "RENAME FAILED | elapsed=%.3f sec",
            time.monotonic() - started,
        )
        return "failed"

    elapsed = time.monotonic() - started
    s = report["summary"]
    gyro = report.get("gyroflow")
    logger.info(
        "[PRESERVE-OK] %s | PRESERVED=%d MODIFIED=%d MISSING=%d | "
        "gyroflow=%s | check=%s",
        src.name, s["PRESERVED"], s["MODIFIED"], s["MISSING"],
        gyro.get("status") if gyro else "not-run",
        check_level,
    )
    file_logger.info(
        "DJI_PRESERVATION_REPORT | %s",
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
        src=src, dst=dst, preset=preset, elapsed=elapsed,
        source_summary=source_summary, ffprobe=ffprobe,
        postprobe_csv=postprobe_csv,
        postprobe_stream_csv=postprobe_stream_csv,
        logger=logger, file_logger=file_logger,
    )
    return "done"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "1KeyTranscoder: recursive, resumable archive encoder with "
            "Sony camera-metadata preservation (NVEncC/QSVEncC hardware "
            "backends; x265 as explicit manual option)."
        ),
    )
    parser.add_argument("--input", required=True, help="Input root directory.")
    parser.add_argument("--output", required=True, help="Output root directory.")
    parser.add_argument(
        "--preset",
        choices=[p.lower() for p in PRESETS] + ["all"],
        default="hq",
        help="UHQ/HQ/SMALL/FAST/all. Default: HQ.",
    )
    parser.add_argument(
        "--encoder",
        choices=["x265", "svtav1", "nvenc", "qsv", "nvenc-av1", "qsv-av1"],
        default=None,
        help=(
            "Encoder backend. Default: declared by the config file's "
            "'encoder' field. Hardware paths never fall back to "
            "software encoding. AV1 后端 (svtav1/nvenc-av1/qsv-av1) 对 "
            "Sony 源保留 rtmd/nrtm/uuid 元数据但按策略不打 XAVC tag "
            "(AV1 不在 XAVC 规范内); AV1 统一输出 4:2:0, 4:2:2 源 "
            "WARNING 后降采样。"
        ),
    )
    parser.add_argument("--config", default=None,
                        help="Path to the encoder profile JSON.")
    parser.add_argument(
        "--scaling-config", default=None,
        help="Path to the scaling rules JSON (x265/svtav1 backends).",
    )
    parser.add_argument("--tool-nvencc", default=None,
                        help="Explicit NVEncC64.exe path.")
    parser.add_argument("--tool-qsvencc", default=None,
                        help="Explicit QSVEncC64.exe path.")
    parser.add_argument("--ffmpeg", default=None, help="Explicit ffmpeg.exe path.")
    parser.add_argument("--ffprobe", default=None, help="Explicit ffprobe.exe path.")
    parser.add_argument(
        "--gpac-dir", default=None,
        help="GPAC installation directory (default: C:\\Program Files\\GPAC).",
    )
    parser.add_argument("--gyroflow", default=None,
                        help="Explicit Gyroflow.exe path.")
    parser.add_argument(
        "--check",
        choices=["basic", "advanced", "full"],
        default="basic",
        help=(
            "编码后验证强度. basic(默认)=仅必要核心元数据; "
            "advanced=完整结构校验+Gyroflow; full=advanced+详细自检"
            "(64项, 先探测 Gyroflow 安装, 未安装则提示并跳过消费端对比)."
            " Sony 与 DJI 路径均生效: DJI basic=轨道清单+载荷sha256; "
            "advanced=+Gyroflow 逐帧四元数; full=+逐轨时基/时长/"
            "载荷首尾字节/流级事实."
        ),
    )
    parser.add_argument(
        "--jobs",
        default="1",
        help=(
            "同一硬件后端的工作调度: 1(默认, 单线程) | N(固定并发) | "
            "auto(波次实测聚合吞吐动态调整工作数, 无写死预算表)."
        ),
    )
    parser.add_argument(
        "--experimental-multihw",
        action="store_true",
        help=(
            "实验性功能: NVENC+QSV 多硬件后端并行. 启用即声明: "
            "无法保证视频编码质量一致性."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无人值守: 不弹进度看板窗口, 全部信息落日志.",
    )
    parser.add_argument(
        "--retry-list",
        default=None,
        help=(
            "载入 failed_files.json (或路径清单) 作为输入集, 配合 "
            "--encoder 指定重跑后端 (如 x265)."
        ),
    )
    parser.add_argument(
        "--keep-work", action="store_true",
        help="成功后保留 .1ktwork 工作目录 (默认删除).",
    )
    parser.add_argument(
        "--no-downgrade", action="store_true",
        help="禁止编码格式降级: 能力不满足则跳过文件, 运行时失败不重试.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Probe and print commands without encoding.",
    )
    return parser.parse_args()


def spawn_dashboard(json_path: Path, script_dir: Path):
    """Open the nvidia-smi style dashboard in a separate console."""
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "core.dashboard_ui", str(json_path)],
            cwd=str(script_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except OSError:
        return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()

    if not input_root.is_dir():
        print(f"[FATAL] input directory not found: {input_root}",
              file=sys.stderr)
        return 2
    if output_root == input_root or format_path_relation(
        output_root, input_root
    ):
        print("[FATAL] output directory must not be inside input directory.",
              file=sys.stderr)
        return 2

    try:
        default_config_name = {
            "x265": "x265.json",
            "svtav1": "svtav1.json",
            "nvenc": "nvenc.json",
            "qsv": "qsv.json",
            "nvenc-av1": "nvenc_av1.json",
            "qsv-av1": "qsv_av1.json",
        }
        if args.config:
            config_path = resolve_config_file(
                script_dir, args.config, "x265.json"
            )
            config = load_json_file(config_path)
            declared = str(config.get("encoder") or "x265").lower()
            if args.encoder and args.encoder != declared:
                raise ValueError(
                    f"--config declares encoder '{declared}' but "
                    f"--encoder {args.encoder} was given"
                )
            encoder_name = declared
        else:
            encoder_name = args.encoder or "x265"
            config_path = resolve_config_file(
                script_dir, None, default_config_name[encoder_name]
            )
            config = load_json_file(config_path)

        profiles = config.get("profile")
        if not isinstance(profiles, dict) or any(
            p not in profiles for p in PRESETS
        ):
            raise ValueError(
                f"{config_path.name} must contain 'profile' with "
                f"{', '.join(PRESETS)}"
            )
        is_hardware = encoder_name in (
            "nvenc", "qsv", "nvenc-av1", "qsv-av1"
        )
        if args.experimental_multihw and encoder_name not in (
            "nvenc", "qsv"
        ):
            raise ValueError(
                "--experimental-multihw requires a HEVC hardware "
                "backend (nvenc/qsv); it manages both backends itself."
            )

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

        classifier = engine = scaling_config = scaling_path = None
        backend = None

        if encoder_name == "x265":
            config = load_config(config_path)
            scaling_path = resolve_config_file(
                script_dir, args.scaling_config, "x265_scaling.json"
            )
            scaling_config = load_scaling_config(scaling_path)
            classifier = SourceClassifier(scaling_config)
            engine = ScalingEngine(scaling_config)
            backend = X265Backend()
        elif encoder_name == "svtav1":
            config = load_svtav1_config(config_path)
            scaling_path = resolve_config_file(
                script_dir, args.scaling_config, "svtav1_scaling.json"
            )
            scaling_config = load_scaling_config(scaling_path)
            classifier = SourceClassifier(scaling_config)
            engine = ScalingEngine(scaling_config)
            backend = SvtAv1Backend()
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
    preserve_reports = logs_root / "preserve_reports"
    failed_path = logs_root / "failed_files.json"

    work_root = output_root / ".1ktwork"
    logger = setup_logger(total_log)

    if is_hardware:
        if args.experimental_multihw:
            print(EXPERIMENTAL_BANNER)
            logger.warning(
                "[EXPERIMENTAL] multihw enabled: quality consistency "
                "NOT guaranteed"
            )
            backend = hw_backend_for("nvenc", script_dir, args, work_root, logger)
            backend_qsv = hw_backend_for("qsv", script_dir, args, work_root, logger)
        else:
            backend = hw_backend_for(encoder_name, script_dir, args, work_root, logger)

    requested = (
        list(PRESETS)
        if args.preset == "all"
        else [args.preset.upper()]
    )
    multiple_presets = len(requested) > 1

    if args.retry_list:
        retry_sources = load_retry_list(Path(args.retry_list))
        if not retry_sources:
            print(f"[FATAL] retry list empty or unreadable: {args.retry_list}",
                  file=sys.stderr)
            return 2
        sources = retry_sources
        logger.info("RETRY LIST | %d file(s) from %s",
                    len(sources), args.retry_list)
    else:
        sources = discover_sources(input_root)

    jobs_value = args.jobs
    if jobs_value not in ("1", "auto"):
        try:
            int(jobs_value)
        except ValueError:
            print(f"[FATAL] --jobs must be 1, N or auto (got {jobs_value!r})",
                  file=sys.stderr)
            return 2

    # dashboard (window unless headless; JSON always written)
    dashboard_json = logs_root / "dashboard.json"
    status = DashboardStatus(dashboard_json)
    if not args.headless:
        spawn_dashboard(dashboard_json, script_dir)

    status.set_meta(
        {
            "encoders": (
                "nvenc+qsv(experimental)"
                if args.experimental_multihw
                else backend.name
            ),
            "preset": args.preset,
            "check": args.check,
            "total": len(sources),
            "gpu": getattr(getattr(backend, "caps", None), "device", ""),
            "jobs": jobs_value,
            "headless": args.headless,
        }
    )

    logger.info("============================================================")
    logger.info("1KeyTranscoder batch start")
    logger.info("Input : %s", input_root)
    logger.info("Output: %s", output_root)
    logger.info("Logs  : %s", logs_root)
    logger.info("Work  : %s", work_root)
    logger.info("Config: %s", config_path)
    logger.info("Backend: %s", backend.name)
    logger.info("Presets: %s", ", ".join(requested))
    logger.info("Files: %d", len(sources))
    logger.info("Check level: %s", args.check)
    logger.info("Jobs: %s", jobs_value)
    logger.info("Experimental multihw: %s", args.experimental_multihw)
    logger.info("Headless: %s", args.headless)
    logger.info("Keep work dirs: %s", args.keep_work)
    logger.info("============================================================")

    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}

    for preset in requested:
        profile = config["profile"][preset]
        logger.info("---------- PRESET %s ----------", preset)

        if not is_hardware:
            # x265 path (sequential; failure records + dashboard status
            # mirror the hardware path)
            for src in sources:
                dst = output_path_for(
                    src, input_root, output_root, preset, multiple_presets
                )
                # AV1 中间/临时文件必须是 MP4 (ffmpeg 9 MOV muxer 不接受 AV1)
                part_ext = ".part.mp4" if backend.name == "svtav1" else ".part.mov"
                part_dst = dst.with_name(dst.stem + part_ext)
                file_log = per_file_log_path(
                    src, input_root, logs_root, preset
                )
                file_logger = build_file_logger(file_log)
                if status is not None:
                    status.start(src, preset, backend.name)
                if dst.is_file() and dst.stat().st_size > 0:
                    logger.info("[SKIP] %s | %s", src, dst)
                    file_logger.info(
                        "SKIP | output already exists | output=%s", dst
                    )
                    counters["skipped"] += 1
                    if status is not None:
                        status.finish(src, "skipped")
                    continue
                try:
                    prepared = prepare_source(
                        src=src, preset=preset, ffprobe=ffprobe,
                        preprobe_csv=preprobe_csv,
                        preprobe_stream_csv=preprobe_stream_csv,
                        scaling_csv=scaling_csv, profile=profile,
                        classifier=classifier, engine=engine,
                        backend=backend, file_logger=file_logger,
                    )
                except Exception as exc:
                    logger.error("[PROBE-FAIL] %s | %s", src, exc)
                    file_logger.error("PREPROBE FAILED | %s", exc)
                    record_failure(
                        failed_path,
                        source=src, preset=preset,
                        backend_name=backend.name,
                        stage="probe", error=str(exc),
                        log_path=str(file_log),
                    )
                    counters["failed"] += 1
                    if status is not None:
                        status.finish(src, "failed")
                    continue
                codec = "av1" if getattr(backend, "name", "") == "svtav1" else "hevc"
                video_entry = "av01" if codec == "av1" else "hvc1"
                if (
                    codec == "av1"
                    and prepared.src_info.chroma not in ("4:2:0", "mono", "")
                ):
                    logger.warning(
                        "[WARNING] %s | AV1 统一输出 4:2:0: 源色度 %s 降采样"
                        "为 4:2:0 (SVT-AV1 420 策略)",
                        src.name, prepared.src_info.chroma,
                    )
                    file_logger.warning(
                        "CHROMA_DOWNGRADE | av1 420 policy: source chroma "
                        "%s -> 4:2:0", prepared.src_info.chroma,
                    )
                if any(
                    st.get("codec_type") == "data"
                    and st.get("codec_tag_string") == "rtmd"
                    for st in prepared.streams
                ):
                    result = encode_one_sony(
                        src=src, dst=dst, prepared=prepared, profile=profile,
                        preset=preset, backend=backend, engine=engine,
                        ffmpeg=ffmpeg, ffprobe=ffprobe,
                        postprobe_csv=postprobe_csv,
                        postprobe_stream_csv=postprobe_stream_csv,
                        gpac=gpac, gyroflow=gyroflow, work_root=work_root,
                        check_level=args.check, codec=codec,
                        logger=logger, file_logger=file_logger,
                        dry_run=args.dry_run,
                    )
                elif is_dji_source(prepared.streams):
                    result = encode_one_dji_x265(
                        src=src, dst=dst, prepared=prepared, profile=profile,
                        preset=preset, backend=backend, engine=engine,
                        ffmpeg=ffmpeg, ffprobe=ffprobe,
                        postprobe_csv=postprobe_csv,
                        postprobe_stream_csv=postprobe_stream_csv,
                        gpac=gpac, gyroflow=gyroflow, work_root=work_root,
                        check_level=args.check, video_entry=video_entry,
                        logger=logger, file_logger=file_logger,
                        dry_run=args.dry_run,
                    )
                else:
                    result = encode_one(
                        src=src, dst=dst, part_dst=part_dst,
                        prepared=prepared, ffmpeg=ffmpeg, ffprobe=ffprobe,
                        postprobe_csv=postprobe_csv,
                        postprobe_stream_csv=postprobe_stream_csv,
                        profile=profile, preset=preset, backend=backend,
                        engine=engine, logger=logger,
                        file_logger=file_logger, dry_run=args.dry_run,
                    )
                if result == "failed":
                    record_failure(
                        failed_path,
                        source=src, preset=preset,
                        backend_name=backend.name,
                        stage="encode", error="see per-file log",
                        log_path=str(file_log),
                    )
                counters[result] += 1
                if status is not None:
                    status.finish(src, result)
            continue

        # hardware backend(s)
        ctx = BatchCtx(
            preset=preset,
            profile=profile,
            ffprobe=ffprobe,
            gpac=gpac,
            gyroflow=gyroflow,
            input_root=input_root,
            output_root=output_root,
            logs_root=logs_root,
            work_root=work_root,
            preserve_reports=preserve_reports,
            preprobe_csv=preprobe_csv,
            preprobe_stream_csv=preprobe_stream_csv,
            postprobe_csv=postprobe_csv,
            postprobe_stream_csv=postprobe_stream_csv,
            multiple_presets=multiple_presets,
            keep_work=args.keep_work,
            no_downgrade=args.no_downgrade,
            check_level=args.check,
            dry_run=args.dry_run,
            failed_path=failed_path,
            status=status,
        )

        if args.experimental_multihw:
            round_counters = run_multihw_pool(
                ctx, backend, backend_qsv, sources, logger
            )
        else:
            round_counters = run_hw_pool(
                ctx, backend, sources, logger, jobs_value
            )

        for key, value in round_counters.items():
            counters[key] += value

    status.mark_finished()

    logger.info("============================================================")
    logger.info(
        "Finished | done=%d skipped=%d failed=%d dry-run=%d",
        counters["done"], counters["skipped"],
        counters["failed"], counters["dry-run"],
    )
    logger.info("Total log: %s", total_log)
    logger.info("Preprobe CSV: %s", preprobe_csv)
    logger.info("Postprobe CSV: %s", postprobe_csv)
    logger.info("Scaling CSV: %s", scaling_csv)
    logger.info("Preservation reports: %s", preserve_reports)
    logger.info("Failed files list: %s", failed_path)
    logger.info("Dashboard data: %s", dashboard_json)
    logger.info("============================================================")

    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
