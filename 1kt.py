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
    hw_backend_for,
    run_hw_pool,
    run_multihw_pool,
    load_retry_list,
)
from core.config import (
    find_executable,
    load_config,
    load_json_file,
    load_scaling_config,
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
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
) -> str:
    """x265 Sony preservation path (manual --encoder x265 only;
    --check levels apply like on hardware backends)."""
    import time

    from core.paths import job_id_for, safe_unlink

    source_summary = prepared.summary
    work_dir = work_root / job_id_for(src)
    encoded_mov = work_dir / "video" / "encoded.mov"

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
        choices=["x265", "nvenc", "qsv"],
        default=None,
        help=(
            "Encoder backend. Default: declared by the config file's "
            "'encoder' field. Hardware paths never fall back to "
            "software encoding."
        ),
    )
    parser.add_argument("--config", default=None,
                        help="Path to the encoder profile JSON.")
    parser.add_argument(
        "--scaling-config", default=None,
        help="Path to x265 scaling rules JSON (x265 backend only).",
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
            "nvenc": "nvenc.json",
            "qsv": "qsv.json",
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
        is_hardware = encoder_name in ("nvenc", "qsv")
        if args.experimental_multihw and not is_hardware:
            raise ValueError(
                "--experimental-multihw requires a hardware backend "
                "(nvenc/qsv); it manages both backends itself."
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
            # x265 legacy path (sequential, unchanged behavior)
            for src in sources:
                dst = output_path_for(
                    src, input_root, output_root, preset, multiple_presets
                )
                part_dst = dst.with_name(dst.stem + ".part.mov")
                file_log = per_file_log_path(
                    src, input_root, logs_root, preset
                )
                file_logger = build_file_logger(file_log)
                if dst.is_file() and dst.stat().st_size > 0:
                    logger.info("[SKIP] %s | %s", src, dst)
                    file_logger.info(
                        "SKIP | output already exists | output=%s", dst
                    )
                    counters["skipped"] += 1
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
                    counters["failed"] += 1
                    continue
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
                        check_level=args.check,
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
                counters[result] += 1
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
