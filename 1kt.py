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
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.batch_hw import (
    BatchCtx,
    _meta_manifest,
    detect_vfr,
    hw_backend_for,
    is_dji_source,
    place_final,
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
    resolve_log_level,
    setup_logger,
)
from core.models import PRESETS, EffectiveParams
from core.paths import (
    discover_sources,
    format_path_relation,
    job_id_for,
    output_path_for,
    per_file_log_path,
)
from core.postprobe import postprobe_and_log
from core.probe import build_source_info, count_frames, probe_source
from core.scaling import ScalingEngine
from core.source_classifier import SourceClassifier
from core.workdir import gc_job_dir
from core.version import version_string
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

#: `--audio-plan` 里选择器使用的**来源身份**。取固定名而不是文件名主干,
#: 这样同一份计划文件对整批素材都可读、可复现 ("source:s1:c0")。
AUDIO_SOURCE_ID = "source"


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


def _apply_audio_plan(
    *,
    src: Path,
    video: Path,
    video_container: str,
    dst: Path,
    work_dir: Path,
    audio_plan_request,
    ffmpeg: Path,
    ffprobe: Path,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> tuple[bool, bool, str]:
    """显式音频计划: **一个**实现, 软件与硬件后端共用 (v0.8.0+)。

    返回 `(ok, applied, detail)`:

    * `ok=False`  -> 计划被拒绝/编排失败。调用方必须**明确失败**, 绝不忽略计划
                     继续输出 (否则用户会以为计划生效了);
    * `applied=True`  -> 最终文件已经写好;
    * `applied=False` -> "没有要做的事"(空计划), 调用方保持既有路径原样。

    为什么软件与硬件必须共用这一个函数: §11/§26 要求"同一个 AudioPlan, 换视频
    后端不得改变音频结果"。两条路径各写一遍是唯一能破坏这个性质的方式。

    ⚠️ 本函数是 `1kt.py` 里**唯一**触达音频域的地方, 且只通过
    `production.output` —— 生产入口不认识 `AudioPlan` / `AudioTimeline` /
    `AudioEncoder`, 只转交一个不透明的请求对象。
    """
    from production.output import (
        VideoOutputArtifact,
        produce_audio_output,
        resolve_source_audio_plan,
    )

    try:
        candidate = resolve_source_audio_plan(
            ffprobe, src, audio_plan_request, source_id=AUDIO_SOURCE_ID,
        )
    except Exception as exc:                         # noqa: BLE001
        logger.error("[AUDIO-FAIL] %s | %s", src, exc)
        file_logger.error("AUDIO PLAN REJECTED | %s", exc)
        return False, False, str(exc)

    outcome = produce_audio_output(
        plan=candidate,
        video=VideoOutputArtifact(
            path=str(video), container=video_container, label="encoded",
        ),
        output_path=dst,
        audio_source=src,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        work_dir=work_dir,
        request=audio_plan_request,
        log=lambda msg: logger.info("[AUDIO] %s | %s", src.name, msg),
    )
    file_logger.info(
        "AUDIO_OUTPUT | %s | %s",
        outcome.path.value,
        json.dumps(outcome.summary(), ensure_ascii=False),
    )
    if not outcome.ok:
        logger.error(
            "[AUDIO-FAIL] %s | %s | %s",
            src, outcome.reasons,
            [e.get("detail") for e in outcome.errors],
        )
        file_logger.error(
            "AUDIO OUTPUT FAILED | %s",
            json.dumps(outcome.errors, ensure_ascii=False),
        )
        return False, False, ", ".join(outcome.reasons)
    if outcome.applied and Path(outcome.output_path) != dst:
        # Composer 写到了别处: 不猜, 明确报错 (调用方会删掉中间产物)。
        file_logger.error(
            "AUDIO OUTPUT PATH MISMATCH | %s != %s",
            outcome.output_path, dst,
        )
        return False, False, "audio output path mismatch"
    return True, bool(outcome.applied), outcome.path.value


def _audio_plan_handoff(
    *,
    audio_plan_request,
    ffmpeg: Path,
    ffprobe: Path,
    logger: logging.Logger,
) -> Any:
    """给硬件批量层用的"接手最终输出"钩子 (v0.8.0+)。

    硬件批量层只传一个 `VideoHandoff` (源 / 视频产物 / 目标 / 工作目录), 不知道
    音频的存在; 音频域也完全不知道视频是 NVENC 还是 QSV 编的。两侧因此保持
    零耦合:

        NVENC / QSV  ->  VideoHandoff  ->  (本钩子)  ->  production.output

    钩子内部与软件路径调用**同一个** `_apply_audio_plan`, 所以同一个计划在
    x265 / SVT-AV1 / NVENC / QSV 上必须得到相同的音频结构。
    """
    if audio_plan_request is None:
        return None

    def hook(request: Any) -> Any:
        from core.batch_hw import HandoffOutcome

        file_logger = getattr(request, "file_logger", None) or logger
        try:
            ok, applied, detail = _apply_audio_plan(
                src=request.source,
                video=request.video,
                video_container="mp4",
                dst=request.output,
                work_dir=request.work_dir / "audio",
                audio_plan_request=audio_plan_request,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                logger=logger,
                file_logger=file_logger,
            )
        except Exception as exc:                     # noqa: BLE001
            logger.exception("[AUDIO-FAIL] %s", request.source)
            return HandoffOutcome(
                ok=False, applied=False,
                detail=f"{type(exc).__name__}: {exc}",
                errors=[str(exc)],
            )
        return HandoffOutcome(
            ok=ok, applied=applied,
            detail=detail if (not ok or applied) else "audio plan disabled",
            errors=[] if ok else [detail],
        )

    return hook


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
    check_level: str = "basic",
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    audio_plan_request=None,
    gpac: Any = None,
    work_dir: Path | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    decode_plan: Any = None,
    drop_metadata_streams: bool = False,
) -> str:
    """Classic single-pass (x265/svtav1 software backends). At
    check_level='full' the PSNR/SSIM sample gates delivery; with
    channel_sync the fixed audio replaces the copied audio post-encode.

    With an explicit audio plan the same encoder command is derived into a
    video-only one and the audio is produced separately, then composed at the
    end (Phase 4C). Without a plan nothing here changes.

    ``drop_metadata_streams`` is the metadata-skip policy (``--no-metadata`` /
    DJI non-Action default): the encoder command then keeps only the main
    video + audio + subtitles, because camera data tracks and attached cover
    pictures cannot survive FFmpeg's MOV intermediate anyway.

    ``decode_plan`` is the hardware-first decode decision (see
    ``encoders/hwdecode_ffmpeg.plan_decode``). It is applied to the argv here
    rather than inside the encoder backend, so that "which decoder" and
    "which encoder" stay independent: selecting x265 must not force software
    decode."""
    from core.paths import safe_unlink

    safe_unlink(part_dst)
    safe_unlink(part_dst.with_name(part_dst.name + ".ffmpeg.log"))

    source_summary = prepared.summary
    cmd, effective_params = backend.build_command(
        ffmpeg, src, part_dst, profile, prepared.effective,
        source_summary["video_streams"], prepared.src_info,
        drop_metadata_streams=drop_metadata_streams,
    )
    if decode_plan is not None:
        from encoders.hwdecode_ffmpeg import apply_decode_plan

        cmd = apply_decode_plan(cmd, decode_plan, src)
        file_logger.info("DECODE_ROUTE | %s", decode_plan.log_line())
    # 显式音频计划: 视频产物必须独立, 否则 Composer 没有可编排的视频输入。
    # 派生只去掉音频 token, 不碰任何编码参数 (视频 bitstream 因此不变)。
    if audio_plan_request is not None:
        from production.output import derive_video_only_command

        cmd = derive_video_only_command(cmd)
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

    # PSNR/SSIM quality sample (check=full) gates delivery
    if check_level == "full":
        from preservation.quality import run_quality_sample

        q = run_quality_sample(
            original=src,
            final=part_dst,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            scratch=(work_dir or part_dst.parent) / "quality",
            opts=quality_opts,
            csv_path=quality_csv,
            log=lambda msg: logger.info("[QUALITY] %s | %s", src.name, msg),
        )
        if q["status"] == "FAIL":
            logger.error("[FAIL] quality sample | %s | %s", src, q["detail"])
            file_logger.error("QUALITY SAMPLE FAILED | %s", q["detail"])
            safe_unlink(part_dst)
            return "failed"

    # 显式音频计划 (Phase 4C / v0.8.0): 音频与视频是**平行**分支 —— 视频已在
    # 上一步编码完成, 这里只负责把音频域的产物与它组装成最终输出。
    # ⚠️ `1kt.py` **不** import 任何 `core.audio_*` 模块: 音频域的计划解析、
    # 格式策略与编排全部收在 `production.output` 之后, 生产入口只传
    # "源 + 请求"(请求对象对它是**不透明**的)。
    if audio_plan_request is not None:
        audio_ok, audio_applied, _detail = _apply_audio_plan(
            src=src, video=part_dst, video_container="",
            dst=dst,
            work_dir=(work_dir or part_dst.parent) / "audio",
            audio_plan_request=audio_plan_request,
            ffmpeg=ffmpeg, ffprobe=ffprobe,
            logger=logger, file_logger=file_logger,
        )
        if not audio_ok:
            safe_unlink(part_dst)
            return "failed"
        if audio_applied:
            # Composer 已经写出最终文件; 视频中间产物不再需要。
            safe_unlink(part_dst)
            postprobe_and_log(
                src=src, dst=dst, preset=preset, elapsed=elapsed,
                source_summary=source_summary, ffprobe=ffprobe,
                postprobe_csv=postprobe_csv,
                postprobe_stream_csv=postprobe_stream_csv,
                logger=logger, file_logger=file_logger,
            )
            safe_unlink(ffmpeg_log)
            return "done"
        # applied=False = 计划为空 -> 既有路径原样继续 (不做任何改动)。

    # 自动延时补偿 (经典路径): 修正后的音频轨替换拷贝音频
    if channel_sync:
        from core.channel_sync import run_channel_sync
        from preservation.audio_sync import remux_replace_audio

        try:
            res = run_channel_sync(
                source=src,
                ffmpeg=ffmpeg,
                work_dir=(work_dir or part_dst.parent) / "channel_sync",
                streams=prepared.streams,
                opts=channel_sync_opts,
                log=lambda msg: logger.info("[SYNC] %s | %s", src.name, msg),
            )
            file_logger.info(
                "CHANNEL_SYNC | %s | %s",
                res["status"],
                json.dumps(res.get("channels", []), ensure_ascii=False),
            )
            if res["status"] == "applied":
                synced = part_dst.with_name(part_dst.stem + ".synced.mov")
                remux_replace_audio(
                    gpac=gpac, video_src=part_dst,
                    fixed_files=[Path(p) for p in (res.get("audio_files")
                                                   or res["fixed_files"])],
                    dst=synced,
                    log=lambda msg: logger.info("[SYNC] %s", msg),
                )
                # GPAC 输出 mvhd timescale 与源不同: 按实际 timescale 修复
                # tkhd/elst 时长 (否则音频被 elst 截断为 1/3)
                from core.channel_sync import repair_remux_timescale

                repair_remux_timescale(
                    synced, log=lambda msg: logger.info("[SYNC] %s", msg)
                )
                safe_unlink(part_dst)
                os.replace(synced, part_dst)
            elif res["status"] in ("measure_failed", "verify_failed"):
                logger.warning(
                    "[SYNC-SKIP] %s | %s — 原音频照旧",
                    src.name, res["detail"],
                )
        except Exception as exc:
            logger.warning("[SYNC-FAIL] %s | %s — 原音频照旧", src, exc)
            file_logger.exception("CHANNEL SYNC FAILED")

    try:
        place_final(
            gpac=gpac, intermediate=part_dst, dst=dst,
            work_dir=(work_dir or part_dst.parent),
            meta_source=_meta_manifest(work_dir or part_dst.parent),
            log=lambda msg: file_logger.info("CONTAINER | %s", msg),
        )
    except (OSError, RuntimeError) as exc:
        # 交付失败就是失败。这里**不**退化为"改个名算了": 中间产物容器与
        # 目标容器不一致而转换又不可用时, 改名得到的就是"容器名实不符",
        # 正是本次修复要消灭的缺陷, 静默改名只会把它藏起来。
        logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
        file_logger.exception("FINAL PLACEMENT FAILED | elapsed=%.3f sec",
                             elapsed)
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
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    decode_plan: Any = None,
) -> str:
    """Sony preservation path (rtmd) for ffmpeg software backends
    (x265 / svtav1; --check levels apply like on hardware backends).
    codec="av1" 保留元数据但不恢复 XAVC brand (AV1 不在 XAVC 规范内)。

    ``decode_plan`` supplies the hardware-first decode route for the
    video-only intermediate. Sony is **not** special-cased here: the rtmd
    payload is re-added from the preservation bundle by MP4Box, so it does
    not constrain which decoder produced the video track."""
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
    if decode_plan is not None:
        from encoders.hwdecode_ffmpeg import apply_decode_plan

        cmd = apply_decode_plan(cmd, decode_plan, src)
        file_logger.info("DECODE_ROUTE | %s", decode_plan.log_line())
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

        # 自动延时补偿 (Sony): 修正后的音频轨在重建时替代源音轨
        audio_sources = None
        if channel_sync and source_summary["audio_streams"] > 0:
            from core.channel_sync import run_channel_sync

            try:
                res = run_channel_sync(
                    source=src,
                    ffmpeg=ffmpeg,
                    work_dir=work_dir / "channel_sync",
                    streams=prepared.streams,
                    opts=channel_sync_opts,
                    log=lambda msg: logger.info("[SYNC] %s | %s", src.name, msg),
                )
                file_logger.info(
                    "CHANNEL_SYNC | %s | %s",
                    res["status"],
                    json.dumps(res.get("channels", []), ensure_ascii=False),
                )
                if res["status"] == "applied":
                    audio_sources = [Path(p) for p in (
                        res.get("audio_files") or res["fixed_files"]
                    )]
                elif res["status"] in ("measure_failed", "verify_failed"):
                    logger.warning(
                        "[SYNC-SKIP] %s | %s — 原音频照旧",
                        src.name, res["detail"],
                    )
            except Exception as exc:
                logger.warning("[SYNC-FAIL] %s | %s — 原音频照旧", src, exc)
                file_logger.exception("CHANNEL SYNC FAILED")

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
            ffmpeg=ffmpeg,
            quality_opts=quality_opts,
            quality_csv=quality_csv,
            audio_sources=audio_sources,
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
    # Only a real FAIL blocks delivery. `SKIPPED` means the consumer-side
    # check did not run (Gyroflow absent, or check=basic) — a coverage
    # gap, not a defect in this file. It is surfaced in the preserve log
    # and in report["gyroflow"], so it cannot pass unnoticed.
    if gyro is not None and gyro.get("status") == "FAIL":
        logger.error("[FAIL] Gyroflow consumer validation | %s | %s",
                     src, gyro.get("detail"))
        file_logger.error("GYROFLOW VALIDATION FAILED | %s", gyro.get("detail"))
        return "failed"

    dst.parent.mkdir(parents=True, exist_ok=True)
    final = work_dir / "final" / "output.mov"
    # 保留管线的工作容器恒为 MOV; 最终容器跟随源 (见 place_final)。
    # 校验已经在上面的 run_sony_pipeline 内完成, 但**校验跑在校验对象还是
    # MOV 的时候** —— 这一步会逐轨重建容器, 而逐轨重建会丢文件级 meta
    # (nrtm), 消费端 (Gyroflow) 因此读不到 rtmd 轨。meta_source 就是让它
    # 在重封装后把 meta 与 tref/cdsc 补回来并断言。
    try:
        place_final(
            gpac=gpac, intermediate=final, dst=dst, work_dir=work_dir,
            meta_source=_meta_manifest(work_dir),
            log=lambda msg: file_logger.info("CONTAINER | %s", msg),
        )
    except (OSError, RuntimeError) as exc:
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
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    decode_plan: Any = None,
) -> str:
    """DJI preservation path for ffmpeg software backends (x265/svtav1).

    Video-only encode (video intermediate is an FFmpeg MOV — no rigaya
    millisecond quantization, so fix_hw_timing=False) + shared GPAC
    rebuild (djmd/dbgi/tmcd native copies) + dji check.
    video_entry: "hvc1" (HEVC) / "av01" (AV1).

    ``decode_plan`` supplies the hardware-first decode route; the djmd/dbgi/
    tmcd payloads are native track copies in the rebuild step, so they do not
    constrain the decoder choice."""
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
    if decode_plan is not None:
        from encoders.hwdecode_ffmpeg import apply_decode_plan

        cmd = apply_decode_plan(cmd, decode_plan, src)
        file_logger.info("DECODE_ROUTE | %s", decode_plan.log_line())
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

        # 自动延时补偿 (DJI): 修正后的音频轨在重建时替代源音轨
        audio_sources = None
        if channel_sync and source_summary["audio_streams"] > 0:
            from core.channel_sync import run_channel_sync

            try:
                res = run_channel_sync(
                    source=src,
                    ffmpeg=ffmpeg,
                    work_dir=work_dir / "channel_sync",
                    streams=prepared.streams,
                    opts=channel_sync_opts,
                    log=lambda msg: logger.info("[SYNC] %s | %s", src.name, msg),
                )
                file_logger.info(
                    "CHANNEL_SYNC | %s | %s",
                    res["status"],
                    json.dumps(res.get("channels", []), ensure_ascii=False),
                )
                if res["status"] == "applied":
                    audio_sources = [Path(p) for p in (
                        res.get("audio_files") or res["fixed_files"]
                    )]
                elif res["status"] in ("measure_failed", "verify_failed"):
                    logger.warning(
                        "[SYNC-SKIP] %s | %s — 原音频照旧",
                        src.name, res["detail"],
                    )
            except Exception as exc:
                logger.warning("[SYNC-FAIL] %s | %s — 原音频照旧", src, exc)
                file_logger.exception("CHANNEL SYNC FAILED")

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
            ffmpeg=ffmpeg,
            quality_opts=quality_opts,
            quality_csv=quality_csv,
            audio_sources=audio_sources,
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
        place_final(
            gpac=gpac, intermediate=final, dst=dst, work_dir=work_dir,
            log=lambda msg: file_logger.info("CONTAINER | %s", msg),
        )
    except (OSError, RuntimeError) as exc:
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
    parser.add_argument(
        "--version",
        action="version",
        version=version_string(),
        help="Print the version and exit.",
    )
    parser.add_argument("--input", required=True, help="Input root directory.")
    parser.add_argument("--output", required=True, help="Output root directory.")
    parser.add_argument(
        "--preset",
        type=str.lower,                      # accept UHQ / Hq / hQ / uhq alike
        choices=[p.lower() for p in PRESETS],
        default="hq",
        help="UHQ/HQ/SMALL/FAST (case-insensitive). Default: HQ.",
    )
    parser.add_argument(
        "--encoder",
        type=str.lower,                      # case-insensitive (nvenc/NVENC)
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
        "--channel-sync",
        action="store_true",
        help=(
            "自动延时补偿 (P1): 对多流单声道 PCM 布局 (无线麦 CH1/CH2 + 有线 "
            "CH3/CH4, 48k/96k, 44.1k 显式拒绝) 逐文件 GCC-PHAT 测量各通道相对"
            "锚点 (候选回退 CH3>CH4>CH1>CH2) 的固定观测时差并做纯整数样本"
            "移位 (尾部补零保全长); 空轨/低置信/非恒定/超窗轨 untouched, "
            "不阻止其它健康轨同步 (轨道级部分成功); 质量门不过或复检超差"
            "时该轨原样 + 警告。2ch/1ch 布局默认不做对齐; 不做漂移 resample"
            "与分数 sinc。依赖 numpy/scipy (可选)。"
        ),
    )
    parser.add_argument(
        "--channel-sync-transparent",
        action="store_true",
        help=(
            "延时补偿透明模式 (隐含 --channel-sync, 剪辑前预处理): 跳过视频"
            "编码, 视频与所有非音频流 stream copy, 仅对需要修正的音频轨重新"
            "生成, untouched 轨保持原始内容; 全部已对齐时输出与源文件字节级"
            "一致 (SHA256 相同); 文件级失败时输出为源文件原样拷贝。"
        ),
    )
    parser.add_argument(
        "--channel-sync-tv",
        action="store_true",
        default=None,
        help=(
            "**逐窗时变**延时补偿 (P4.6, 显式 opt-in, 默认关闭). 与 "
            "--channel-sync 的固定延迟不同, 它按计划窗 (默认 30 s 窗 / 10 s "
            "跳, P3 已冻结的生产几何) 逐窗施加不同移位, 因此能跟随真实漂移。"
            "**启用时必须同时给出 --channel-sync-tv-policy 与 "
            "--channel-sync-tv-coordinate**: 这两项对应两个尚未冻结的产品"
            "决策 (D2 gap policy / D5 coordinate contract), 缺任何一个都会"
            "**拒绝执行**并保持原音频不动 —— 不会替你选一个实现默认值, 也"
            "不会静默退回固定延迟。每轨的逐窗状态 (TRUSTED / UNRESOLVED_GAP "
            "/ REENTRY) 与拒绝理由写进报告。"
        ),
    )
    parser.add_argument(
        "--channel-sync-tv-policy",
        default=None,
        choices=["hold", "no_correction_gap", "explicit_state", "linear",
                 "next"],
        help=(
            "D2 gap policy (必需, 无默认). 决定**缺口窗**的移位怎么来: "
            "hold=前向携带最后一个可信值; no_correction_gap=缺口内为 0; "
            "explicit_state=与 hold 同移位, 状态为一等输出; "
            "linear=缺口两侧线性插值 (离线); next=用下一个可信值 (离线)。"
            "研究策略 'current' **不在可选列表内**: 它把没有测量的窗报告为 "
            "TRUSTED (P3.7/P4.5 实测 195 个窗), 不得进入生产路径。"
        ),
    )
    parser.add_argument(
        "--channel-sync-tv-coordinate",
        default=None,
        choices=["latest-start", "nearest-centre"],
        help=(
            "D5 coordinate contract (必需, 无默认). 决定**哪个窗拥有一个"
            "样本**: latest-start = 起点不晚于该样本的最晚窗 (P1/P3 历史"
            "合同); nearest-centre = 中心最近的窗。两者在真实素材上**不等价**"
            "(C1305 值一致率约 56.9%%, C1306 约 36.4%%), 因此必须由调用方"
            "指定, 不能由实现替你决定。"
        ),
    )
    parser.add_argument(
        "--channel-sync-accurate",
        action="store_true",
        default=None,
        help=(
            "精确模式 (v0.8.7, 显式 opt-in, **默认关闭**). 把\"最小修正幅度\""
            "从 5 样本降到 1 样本并放弃毫米带, 于是 2~5 样本的小偏移也会被修。"
            "每一级都必须由**施加后的独立复检残差**验收: 残差 ≤1 样本才按 1 "
            "样本粒度采纳; 做不到就退到 2 样本 (放弃 |shift|==2 的轨); 再做不到"
            "就退回常规 5 样本门并打 WARNING。判据是量出来的, 不是猜的。"
            "报告里会多一个 `accurate` 块, 逐轨写明候选/采纳/放弃与原因。"
            "代价: 小偏移轨要多一次移位+复检; 长片上可忽略。"
        ),
    )
    parser.add_argument(
        "--channel-sync-groups",
        action="store_true",
        default=None,
        help=(
            "分组锚点 (P4.12, 显式 opt-in, **默认关闭**). 单锚点模型隐含"
            "\"四条轨录同一个声场\"这个前提; 真实 4 声道素材里 CH1/CH2 与 "
            "CH3/CH4 常常是两支互不相干的麦克风, 此时锚点选中的那一组能被修, "
            "另一组没有任何修正机会。启用后先按\"相关峰/噪声底\"把健康轨分成"
            "互不相关的组, 未被锚点覆盖的组各用自己的组内锚点再测一次。"
            "**只做加法**: 只有判成 untouched 的轨才可能被改判, 已定的修正"
            "不会丢失、也不会改值 (实测全语料 226 文件: 新增 3 / 丢失 0 / 改值 0)。"
            "代价: 每个动作组多一次完整测量, 80 min 长片实测 +30%%~+84%%。"
        ),
    )
    parser.add_argument(
        "--channel-sync-group-threshold",
        default=None,
        type=float,
        help=(
            "分组锚点的相关强度门 (默认 1.3, 相关峰/噪声底中位数). 1.0 附近是"
            "\"与随机不可分\"的分界 (估计器自身的 success 判据是 2.0x)。实测"
            "组内中位 2.96 / 跨组 0.90; 阈值 1.2~1.5 之间没有片段丢失动作, "
            "1.2 起有过度合并的迹象。仅在 --channel-sync-groups 生效时有意义。"
        ),
    )
    parser.add_argument(
        "--audio-plan",
        default=None,
        help=(
            "显式音频输出计划 (JSON 文件). **默认不启用**: 不指定时音频"
            "完全按既有路径处理 (-map 0 + -c:a copy). 计划文件用既有声道"
            "身份 (\"<source>:s<stream>:c<channel>\") 表达选择/排除/排序, "
            "可选的 encode 块选择输出编码 (aac / opus / pcm / flac; 缺省时"
            "按输入格式继承, PCM 输入默认输出 PCM). 另有四个可选块: "
            "alignment=\"auto|enabled|disabled\" (PCM 默认允许对齐, "
            "compressed 默认原样保留, 显式要求时会 decode->PCM->对齐->重编码"
            "并给出 warning), sync.reference (对齐的参考声道, 必须显式指定, "
            "从不自动猜), mapping={mode,group_size} (外挂音频的输出流结构), "
            "external={} (发现并纳入同目录下与视频同名的外挂音频). 选择为空"
            "且无以上意图时该文件等于不启用 (走既有路径). 仅经典软件路径 "
            "(x265 / svtav1) 支持; 硬件后端与 Sony/DJI 保留管线会明确报错, "
            "不会静默忽略."
        ),
    )
    parser.add_argument(
        "--check",
        type=str.lower,                      # case-insensitive (Basic/FULL)
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
        default="auto",
        help=(
            "同一后端的工作调度: auto(默认, 按实测吞吐在波次间调整并发, "
            "不写死预算表) | 1(单线程, 确定性) | N(固定并发). "
            "并发上限取 min(32, 2×CPU 核数): 更大的值不会更快, 只会让 "
            "x265 线程与文件并发过度订阅."
        ),
    )
    parser.add_argument(
        "--experimental-multihw",
        action="store_true",
        help=(
            "实验性功能: NVENC+QSV 多硬件后端并行 (无需再指定 --encoder; "
            "两个后端自动探测, 只有一个可用时退化为单后端并告警). "
            "启用即声明: 无法保证视频编码质量一致性."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str.lower,
        choices=["error", "warn", "info", "debug"],
        default="info",
        help=(
            "日志文件详细度 (大小写不敏感). error=只写 error.log; "
            "warn=+warn.log; info(默认)=+total.log; debug=+debug.log "
            "(含完整命令行/阶段耗时/逐阶段内存). "
            "error.log 与 warn.log 始终写入, 与级别无关."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="控制台输出 DEBUG 级日志 (文件级别仍由 --log-level 决定).",
    )
    parser.add_argument(
        "--fresh-log",
        action="store_true",
        help=(
            "启动时清空 total.log / debug.log (默认追加, 便于跨批次排查). "
            "error.log 与 warn.log 始终追加, 不受影响."
        ),
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help=(
            "不保留相机厂商元数据 (Sony rtmd/nrtm/uuid, DJI djmd/dbgi/tmcd). "
            "**这是元数据保留策略, 不是解码开关**: 硬件优先解码对普通模式与 "
            "本模式一视同仁. 启用后 Sony/DJI 走普通转码路径 (容器级重建被"
            "跳过), 但输出有效性、音视频流完整性、帧数与失败检测**一项都"
            "不减** —— 进程失败、产物缺失/损坏、不支持的输入都不会被当成"
            "成功. 音频轨与 channel-sync 行为不变."
        ),
    )
    parser.add_argument(
        "--preserve-metadata",
        action="store_true",
        help=(
            "强制保留相机厂商元数据. 默认策略下 DJI **除 Action 系列外** "
            "(Pocket / 无人机) 不保留 metadata (走普通转码路径); 本开关可"
            "覆盖该默认策略, 让 Pocket/无人机也走 DJI 保留管线. "
            "与 --no-metadata 同时给出时以后者为准 (显式关闭优先)."
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
    # ---- 归档修复模式 (2026-10-26 合入主程序) ---------------------------
    # 为什么放进 1kt.py: 批处理链只需要调用一个程序 (转码 / 修复同入口),
    # 修复能力不再是一个只能手工跑的旁路脚本。修复不重新编码视频。
    parser.add_argument(
        "--repair", action="store_true",
        help="归档修复模式: 把 --input 下已产出 (转码后) 的成品一键修到 "
             "--output 暂存目录, 不重新编码。修音频偏移/元数据位置/容器规范, "
             "成品上重新测量验收; 失败文件自动用精确模式重试。",
    )
    parser.add_argument(
        "--repair-dirs", default="", metavar="REGEX",
        help="修复模式: 只遍历 --input 下**一级子目录名完全匹配**该正则的"
             "目录, 例如 ^\\d{8}$ (整盘输入时必用)。",
    )
    parser.add_argument(
        "--repair-json", default="",
        help="修复模式: 报告输出目录 (默认写到 --output 里)。",
    )
    parser.add_argument(
        "--repair-scratch", default="",
        help="修复模式: 临时目录 (默认 --output 下的 _scratch)。",
    )
    parser.add_argument(
        "--repair-resume", action="store_true",
        help="修复模式: 断点续跑 (读 --output/_progress.jsonl, 跳过已完成"
             "且成品存在的文件)。",
    )
    parser.add_argument(
        "--repair-retry-failed", type=int, default=1, metavar="N",
        help="修复模式: 失败文件用精确模式自动重试的轮数 (默认 1, 0=关)。",
    )
    parser.add_argument(
        "--repair-limit", type=int, default=0,
        help="修复模式: 最多处理 N 个文件 (调试/冒烟用)。",
    )
    parser.add_argument(
        "--repair-fix-layout", action="store_true",
        help="修复模式: 额外恢复 moov-first 布局 (MP4Box -inter, 较慢)。",
    )
    parser.add_argument(
        "--repair-no-container-fix", action="store_true",
        help="修复模式: 只修音频/元数据, 不做容器级修复。",
    )
    parser.add_argument(
        "--repair-no-groups", action="store_true",
        help="修复模式: 关闭分组锚点 (默认跟随生产 start.bat GROUPS=1)。",
    )
    parser.add_argument(
        "--repair-accurate", action="store_true",
        help="修复模式: 打开精确模式 (1→2→5 样本分级)。",
    )
    parser.add_argument(
        "--repair-opt", action="append", default=[], metavar="K=V",
        help="修复模式: 透传 channel-sync 选项覆盖, 可重复 "
             "(例如 --repair-opt verify_max_rel=0.2)。",
    )
    return parser.parse_args()


def _repair_jobs(value: Any) -> int:
    """把 1kt.py 的 --jobs (auto/N) 归一成修复模式的文件级并发数。"""
    text = str(value if value is not None else "auto").strip().lower()
    if text in ("", "auto"):
        return min(8, max(1, (os.cpu_count() or 2) // 2))
    try:
        return max(1, int(text))
    except ValueError:
        raise SystemExit(f"--jobs 需要 auto 或正整数: {value!r}")


def _apply_time_varying_cli(
    args: argparse.Namespace,
    opts: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """把 `--channel-sync-tv*` 三个开关覆写到 channel-sync 选项上。

    经典编码入口 (`main`) 与透明批量入口 (`transparent_main`) 是两条独立的
    调用链, 各自加载 config 并各自读 CLI, 所以这层覆盖必须**共用同一个
    函数**。它以前只写在 `main` 里, 结果是:

        --channel-sync-transparent --channel-sync-tv \
        --channel-sync-tv-policy hold --channel-sync-tv-coordinate latest-start

    在透明模式下完全按固定延迟执行 —— 三个开关被**静默丢弃**, 连"缺
    policy/coordinate 就拒绝"的契约都不会触发。一个不生效的开关比一个会
    拒绝的开关更糟, 因为用户以为时变已经开了。

    只覆写调用方**真的给了值**的键 (None = 没说), 与 config / 生产工作点的
    优先级保持一致; 一个没设置的 config 键不会把调用方的值擦掉。
    """
    tv = getattr(args, "channel_sync_tv", None)
    policy = getattr(args, "channel_sync_tv_policy", None)
    coordinate = getattr(args, "channel_sync_tv_coordinate", None)
    if tv is None and policy is None and coordinate is None:
        return opts
    out = dict(opts or {})
    if tv is not None:
        out["time_varying"] = bool(tv)
    if policy is not None:
        out["tv_policy"] = str(policy)
    if coordinate is not None:
        out["tv_coordinate"] = str(coordinate)
    return out


def _transparent_sync_outcome(status: str) -> str:
    """透明批量计数口径: `synced` / `skipped` / `failed`。

    `run_channel_sync` 的终态不止"成功/失败"两种, 透明模式下**每一种终态
    都会产出可用文件** (文件级失败 = 源文件原样拷贝), 所以不能只靠 `done`
    计数判断"同步是否真的发生":

    * `applied` / `already_aligned` -> `synced`;
    * `not_eligible` / `tool_missing` -> `skipped` (按设计不处理该文件);
    * 其余 (`measure_failed` / `verify_failed` / `tv_not_configured` …)
      -> `failed` (同步没做成, 输出只是源拷贝)。

    2026-10-29: 此前"其余"全部计进 `done` 而 `failed` 恒为 0, 于是两个长片
    的日志一边打 `[SYNC-FAIL]`, 一边在汇总里写 `failed=0` —— 同一份输出给出
    两个互相矛盾的事实。
    """
    if status in ("applied", "already_aligned"):
        return "synced"
    if status in ("not_eligible", "tool_missing"):
        return "skipped"
    return "failed"


def repair_main(args: argparse.Namespace, input_root: Path,
                output_root: Path) -> int:
    """`1kt.py --repair`: 归档成品自主修复 (不重新编码视频)。"""
    import archive_repair

    jobs = _repair_jobs(getattr(args, "jobs", "auto"))
    groups = not bool(getattr(args, "repair_no_groups", False))
    accurate = bool(getattr(args, "repair_accurate", False))
    dirs = getattr(args, "repair_dirs", "") or ""
    print(
        f"[REPAIR] 归档修复模式 | input={input_root} output={output_root} "
        f"jobs={jobs} groups={groups} accurate={accurate} "
        f"dirs={dirs or '*'}", flush=True,
    )
    return archive_repair.run_repair(
        str(input_root), str(output_root),
        jobs=jobs, groups=groups, accurate=accurate, dirs=dirs,
        json_dir=getattr(args, "repair_json", "") or "",
        scratch=getattr(args, "repair_scratch", "") or "",
        resume=bool(getattr(args, "repair_resume", False)),
        retry_failed=int(getattr(args, "repair_retry_failed", 1) or 0),
        fix_layout=bool(getattr(args, "repair_fix_layout", False)),
        fix_container=not bool(
            getattr(args, "repair_no_container_fix", False)),
        limit=int(getattr(args, "repair_limit", 0) or 0),
        opt_items=list(getattr(args, "repair_opt", []) or []),
    )


@contextlib.contextmanager
def _job_scratch(
    *,
    work_root: Path,
    src: Path,
    keep_work: bool,
    preserve_reports: Path | None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    create: bool = True,
):
    """Bracket one software-path job's scratch directory.

    The software paths compute ``work_dir`` themselves inside each encode
    function, so the lifetime is bracketed here at the dispatch site rather
    than re-indenting a 200-line loop body only to add a ``try/finally``.
    Bracketing the dispatch also covers what a per-``return`` cleanup would
    miss: an exception and a ``KeyboardInterrupt`` both run the exit path.

    ``create=False`` is for the case that does no work at all (an existing
    output is skipped), where creating then deleting a directory is noise.
    """
    job = job_id_for(src)
    work_dir = work_root / job
    if create:
        work_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield work_dir
    finally:
        if create or work_dir.exists():
            gc_job_dir(
                work_dir=work_dir,
                job_id=job,
                preserve_reports=preserve_reports,
                keep_work=keep_work,
                logger=logger,
                file_logger=file_logger,
            )


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
# 默认后端选择 (v0.6.2): 未指定 --encoder 时按能力优先 NVENC -> QSV -> x265
# ---------------------------------------------------------------------------

# Priority order for the automatic default backend. AV1 entries are
# deliberately absent: auto-selection must not silently change the codec.
AUTOSELECT_ORDER = ("nvenc", "qsv")
HW_AUTOSELECT_FALLBACK = "x265"


def _hw_encoders_available(script_dir: Path, log) -> list[str]:
    """Which HEVC hardware encoders are actually usable on this machine?

    A backend counts as available only when its rigaya tool exists *and*
    `--check-features` probes successfully and reports an HEVC section —
    the same probe the run itself uses, so auto-selection and execution
    cannot disagree.
    """
    from core.config import find_hw_tool
    from encoders.caps import probe_backend

    available: list[str] = []
    for name in AUTOSELECT_ORDER:
        exe_name = "NVEncC64.exe" if name == "nvenc" else "QSVEncC64.exe"
        kind = "nvencc" if name == "nvenc" else "qsvencc"
        try:
            tool = find_hw_tool(script_dir, exe_name)
        except Exception as exc:              # noqa: BLE001 - probe only
            log(f"  autoselect: {name} unavailable (tool not found: {exc})")
            continue
        try:
            caps = probe_backend(tool, kind)
        except Exception as exc:              # noqa: BLE001 - probe only
            log(f"  autoselect: {name} unavailable (probe error: {exc})")
            continue
        if caps is None:
            log(f"  autoselect: {name} unavailable (--check-features failed)")
            continue
        hevc = caps.codecs.get("hevc")
        if hevc is None or not (
            hevc.bit10 or hevc.csp_422 or hevc.csp_444 or hevc.bit10_422
        ):
            log(f"  autoselect: {name} unavailable (no HEVC encode support)")
            continue
        available.append(name)
        log(f"  autoselect: {name} available "
            f"(device={caps.device or '?'})")
    return available


def resolve_default_backend(
    args: argparse.Namespace, script_dir: Path, log=print
) -> str:
    """Pick the backend when the user did not name one.

    Priority: NVENC -> QSV -> x265, each gated on a real capability probe.

    ``--no-hw-autoselect`` was removed: it existed to restore the pre-v0.6.2
    fixed-x265 default, and ``--encoder x265`` expresses the same intent
    explicitly without a second flag whose absence had to be probed for.

    NOTE (behaviour boundary, deliberately kept): this only chooses the
    *initial* backend. The existing promise "hardware paths never fall back
    to software encoding" is untouched — a hardware run that fails still
    fails rather than silently re-encoding with x265.
    """
    available = _hw_encoders_available(script_dir, log)
    if available:
        return available[0]
    log("  autoselect: no hardware encoder available -> x265 (software)")
    return HW_AUTOSELECT_FALLBACK


# ---------------------------------------------------------------------------
# transparent 模式 (--channel-sync-transparent, 剪辑前预处理)
# ---------------------------------------------------------------------------

def transparent_verify(
    *,
    src: Path,
    dst: Path,
    streams: list[dict],
    ffmpeg: Path,
    ffprobe: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None,
    level: str,
    work_dir: Path,
    log: Any,
) -> dict[str, Any]:
    """透明模式的编码后验证 —— **与正式编码同一套 level 语义**。

    `--check` 的三级在正式编码路径上由 `preservation/checker.run_checks`
    (Sony) 与 `preservation/dji.run_dji_check` (DJI) 实现, 两者都是
    "源 vs 成品" 的对比, 本来就不关心成品怎么产生的。所以透明模式直接复用
    同一个入口, 只有一处差别: 视频是 **stream copy**, 因此 `full` 档里的
    PSNR/SSIM 没有定义物 —— 那个子项被**单列为 SKIPPED_NOT_APPLICABLE**
    并打 INFO, 既不静默跳过(报告会显得全绿却少做一项), 也不跑一个恒真的
    数字(那是把无信息量当质量证据)。其余各级与正式编码完全一致。

    失败**不影响已产出的文件** —— 这是透明模式的原则: 它只写报告与日志。
    """
    is_sony = any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "rtmd"
        for st in streams
    )
    # DJI 判定用的是**同一个**谓词 (`core.batch_hw.is_dji_source`, `djmd` 轨道),
    # 而且它在模块顶层已经 import 过了。这里原本写 `from preservation import
    # is_dji_source` —— 那个名字根本不存在, ImportError 被下面的 `except
    # Exception: dji = False` 吞掉, 于是 DJI 分支**永远进不去**, 每一支 DJI
    # 素材都会被打上"不属于 Sony/DJI 家族"的日志。静默的宽 except 把一个
    # 直接的 NameError 变成了一个看起来正常的业务判断。
    dji = bool(is_dji_source(streams)) if not is_sony else False

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    codec_name = str((video or {}).get("codec_name") or "hevc")
    # 输出视频是**源视频的拷贝**, 所以"期望的输出 codec"就是源的 codec。
    codec = codec_name if codec_name in ("hevc", "av1") else "hevc"

    try:
        if is_sony:
            from preservation.checker import run_checks

            return run_checks(
                original=src, final=dst, gpac=gpac, ffprobe=ffprobe,
                level=level, gyroflow=gyroflow, work_dir=work_dir,
                known_facts={}, codec=codec, ffmpeg=ffmpeg,
                quality_opts=None, quality_csv=None,
                video_reencoded=False, log=log,
            )
        if dji:
            from preservation.dji import run_dji_check

            return run_dji_check(
                original=src, final=dst, gpac=gpac, ffprobe=ffprobe,
                gyroflow=gyroflow, scratch=work_dir / "validate",
                level=level,
                video_entry=str((video or {}).get("codec_tag_string") or
                                "hvc1"),
                ffmpeg=ffmpeg, quality_opts=None, quality_csv=None,
                video_reencoded=False, log=log,
            )
        log(
            f"INFO: check={level} — 该源不属于 Sony/DJI 家族, 没有对应的"
            f"元数据验证器; 本文件跳过 check (透明模式原则: 输出不受影响)"
        )
        return {"status": "SKIPPED_NO_CHECKER", "summary": {}}
    except Exception as exc:                          # noqa: BLE001
        log(f"WARNING: check={level} 执行失败: {exc} — 输出文件不受影响")
        return {"status": "ERROR", "detail": str(exc), "summary": {}}


def transparent_main(
    *,
    args: argparse.Namespace,
    script_dir: Path,
    input_root: Path,
    output_root: Path,
    ffmpeg: Path,
    ffprobe: Path,
    gpac: GpacContainerBackend,
    gyroflow: Path | None = None,
) -> int:
    """视频/非音频流 stream copy + 轨道级音频同步, 不依赖编码器配置。

    输出命名沿用 output_path_for (原 basename, .MP4 后缀); 全部已对齐时
    输出与源文件字节级一致; 文件级失败时输出为源文件原样拷贝。
    """
    from core.channel_sync import run_channel_sync
    from preservation.transparent_restore import (
        restore_after_transparent_remux,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    logs_root = input_root.parent / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    total_log = logs_root / "total.log"
    work_root = output_root / ".1ktwork"
    logger = setup_logger(
        total_log,
        log_level=resolve_log_level(args.log_level),
        verbose=bool(args.verbose),
        fresh=bool(args.fresh_log),
    )

    # channel_sync 阈值: 生产工作点作为基线, --config 的 channel_sync 块覆盖
    from core.channel_sync import production_opts as _prod_opts

    channel_sync_opts: dict[str, Any] | None = _prod_opts()
    try:
        config_path = resolve_config_file(
            script_dir, args.config, "x265.json"
        )
        cfg = load_json_file(config_path)
        channel_sync_opts = _prod_opts(
            cfg.get("channel_sync") if isinstance(cfg, dict) else None
        )
    except Exception as exc:
        logger.warning(
            "TRANSPARENT CONFIG LOAD FAILED | %s (channel_sync DEFAULTS 生效)",
            exc,
        )

    # P4.12 — 分组锚点在**透明批量路径**上同样要能吃命令行开关。
    #
    # 这条路与下面是两条独立的入口 (本函数只做透明预处理, 不编码视频), 因此
    # 它自己加载 channel_sync 配置, 也必须自己做同一份 CLI 覆盖。漏掉这一处的
    # 后果很具体: `--channel-sync-groups` 在透明模式下静默失效, 而透明模式正是
    # 长程音频对齐测试会用的那条路。只覆写调用方真的给了值的键。
    _t_grp = getattr(args, "channel_sync_groups", None)
    _t_thr = getattr(args, "channel_sync_group_threshold", None)
    if _t_grp is not None or _t_thr is not None:
        from core.channel_sync import effective_opts

        channel_sync_opts = dict(channel_sync_opts or {})
        if _t_grp is not None:
            channel_sync_opts["group_anchor_enabled"] = bool(_t_grp)
        if _t_thr is not None:
            channel_sync_opts["group_snr_threshold"] = float(_t_thr)
        # 打**生效值**, 不是"调用方覆写了什么"。只打覆写值会让日志出现
        # `threshold=None` —— 而实际生效的是 DEFAULTS 的 1.30, 运维照着
        # "None" 去排查会走错方向。
        _eff = effective_opts(channel_sync_opts)
        logger.info(
            "CHANNEL-SYNC GROUPING | enabled=%s threshold=%.2f "
            "samples=%d min_samples=%d (CLI override: enabled=%s thr=%s)",
            _eff["group_anchor_enabled"], _eff["group_snr_threshold"],
            _eff["group_snr_samples"], _eff["group_snr_min_samples"],
            "yes" if _t_grp is not None else "no",
            "yes" if _t_thr is not None else "no",
        )

    # v0.8.7 — 精确模式在透明批量路径上同样要能吃开关 (P4.12 那个教训:
    # 这里漏一行, 开关就在透明模式下静默失效, 而透明模式正是长测走的路)。
    _t_acc = getattr(args, "channel_sync_accurate", None)
    if _t_acc is not None:
        from core.channel_sync import effective_opts as _eff_opts

        channel_sync_opts = dict(channel_sync_opts or {})
        channel_sync_opts["channel_sync_accurate"] = bool(_t_acc)
        _ae = _eff_opts(channel_sync_opts)
        logger.info(
            "CHANNEL-SYNC ACCURATE | enabled=%s min_shift_samples=%d "
            "(CLI override: %s)",
            _ae["channel_sync_accurate"], _ae["min_shift_samples"],
            "yes" if _t_acc is not None else "no",
        )

    # P4.6 — 时变同步在透明批量路径上同样要能吃命令行开关。与经典入口共用
    # `_apply_time_varying_cli`, 避免两头各写一份、漏掉一头就静默失效 (P4.12
    # 与 v0.8.7 都在这条路上出过同类事故)。这里打**生效值**, 而不是"覆写了
    # 什么": 日志里 `policy=None` 会让人以为用了默认策略, 而实际生效的是
    # DEFAULTS/config 里的值。
    _t_tv = getattr(args, "channel_sync_tv", None)
    _t_tv_policy = getattr(args, "channel_sync_tv_policy", None)
    _t_tv_coord = getattr(args, "channel_sync_tv_coordinate", None)
    if (_t_tv is not None or _t_tv_policy is not None
            or _t_tv_coord is not None):
        from core.channel_sync import effective_opts as _eff_tv_opts

        channel_sync_opts = _apply_time_varying_cli(args, channel_sync_opts)
        _tv_eff = _eff_tv_opts(channel_sync_opts)
        logger.info(
            "CHANNEL-SYNC TIME-VARYING | enabled=%s policy=%s "
            "coordinate=%s (CLI override: flag=%s policy=%s coordinate=%s)",
            _tv_eff["time_varying"], _tv_eff["tv_policy"],
            _tv_eff["tv_coordinate"],
            "yes" if _t_tv is not None else "no",
            "yes" if _t_tv_policy is not None else "no",
            "yes" if _t_tv_coord is not None else "no",
        )

    if args.retry_list:
        retry_sources = load_retry_list(Path(args.retry_list))
        if not retry_sources:
            print(
                f"[FATAL] retry list empty or unreadable: {args.retry_list}",
                file=sys.stderr,
            )
            return 2
        sources = retry_sources
        logger.info(
            "RETRY LIST | %d file(s) from %s", len(sources), args.retry_list
        )
    else:
        sources = discover_sources(input_root)

    dashboard_json = logs_root / "dashboard.json"
    status = DashboardStatus(dashboard_json)
    if not args.headless:
        spawn_dashboard(dashboard_json, script_dir)
    # `check` 必须记录**实际请求的等级**。这里原本写死 "basic", 于是
    # `--check advanced` 的看板与日志都显示 basic —— 报告在说谎。同时把
    # 等级和它在透明模式下的差别打出来, 免得"以为跑了 full"。
    status.set_meta(
        {
            "encoders": "channel-sync-transparent",
            "preset": "SYNC",
            "check": args.check,
            "total": len(sources),
            "gpu": "",
            "jobs": "1",
            "headless": args.headless,
        }
    )
    logger.info("Check level: %s (transparent)", args.check)
    if args.check == "full":
        logger.info(
            "INFO: check=full — PSNR/SSIM 抽样在本模式下**不适用** "
            "(视频为 stream copy, 编码帧逐位相同), 将单列为 "
            "SKIPPED_NOT_APPLICABLE; 其余 full 项 (详细自检/深度项) 照常执行"
        )

    logger.info("============================================================")
    logger.info("1KeyTranscoder transparent channel-sync batch start")
    logger.info("Input : %s", input_root)
    logger.info("Output: %s", output_root)
    logger.info("Logs  : %s", logs_root)
    logger.info("Files: %d", len(sources))
    logger.info("============================================================")

    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0,
                "check_failed": 0, "sync_skipped": 0, "sync_failed": 0}
    for src in sources:
        dst = output_path_for(src, input_root, output_root, "SYNC", False)
        #: 非空时写进看板 detail。`done` 只表示"输出文件可用", 不表示
        #: "音频被同步过" —— 文件级失败同样产出源文件拷贝。
        sync_detail = ""
        file_log = per_file_log_path(src, input_root, logs_root, "SYNC")
        file_logger = build_file_logger(file_log)
        if status is not None:
            status.start(src, "SYNC", "channel-sync-transparent")
        if dst.is_file() and dst.stat().st_size > 0:
            logger.info("[SKIP] %s | %s", src, dst)
            file_logger.info(
                "SKIP | output already exists | output=%s", dst
            )
            counters["skipped"] += 1
            if status is not None:
                status.finish(src, "skipped")
            continue
        if args.dry_run:
            logger.info("[DRY-RUN] %s -> %s", src, dst)
            file_logger.info("DRY-RUN | no sync performed.")
            counters["dry-run"] += 1
            if status is not None:
                status.finish(src, "dry-run")
            continue
        try:
            try:
                _fmt, streams = probe_source(ffprobe, src)
            except Exception as exc:
                # 探测失败 (如无视频流的纯音频文件): 输出 = 源文件原样拷贝
                logger.warning(
                    "[SYNC-SKIP] %s | probe failed (%s) — 输出为源文件原样拷贝",
                    src.name, exc,
                )
                file_logger.exception(
                    "TRANSPARENT PROBE FAILED — output is source copy"
                )
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                counters["sync_skipped"] += 1
                counters["done"] += 1
                if status is not None:
                    status.finish(src, "done")
                    status.update(src, "done", "sync skipped: probe failed")
                continue
            res = run_channel_sync(
                source=src,
                ffmpeg=ffmpeg,
                work_dir=work_root / job_id_for(src) / "channel_sync",
                streams=streams,
                opts=channel_sync_opts,
                transparent=True,
                dst=dst,
                gpac=gpac,
                # 容器层恢复由本层注入 (依赖倒置): 音频同步层不得 import
                # preservation.sony —— 那是 P4 审计 A.1 强制的分层边界。
                restore_metadata=restore_after_transparent_remux,
                log=lambda msg: logger.info("[SYNC] %s | %s", src.name, msg),
            )
            file_logger.info(
                "CHANNEL_SYNC | %s | %s",
                res["status"],
                json.dumps(res.get("channels", []), ensure_ascii=False),
            )
            outcome = _transparent_sync_outcome(res["status"])
            if outcome == "synced":
                if res["status"] == "applied":
                    logger.info(
                        "[SYNC-OK] %s | transparent remux "
                        "(scope=%s, fixed=%d, %s) | %s",
                        src.name, res.get("result_scope"),
                        len(res.get("fixed_files", [])),
                        "byte copy" if res.get("file_copied") else "remux",
                        dst,
                    )
                    # 编码后验证: 与正式编码同一套 level 语义。只有真的
                    # 重封装过才值得验 (下面 already_aligned 那条是字节级
                    # 拷贝, 验它等于拿源和源比)。
                    crep = transparent_verify(
                        src=src, dst=dst, streams=streams,
                        ffmpeg=ffmpeg, ffprobe=ffprobe, gpac=gpac,
                        gyroflow=gyroflow, level=args.check,
                        work_dir=work_root / job_id_for(src) / "check",
                        log=lambda msg: logger.info(
                            "[CHECK] %s | %s", src.name, msg),
                    )
                    summary = crep.get("summary") or {}
                    if summary:
                        logger.info(
                            "[CHECK] %s | level=%s PRESERVED=%s MODIFIED=%s "
                            "MISSING=%s structural_success=%s quality=%s",
                            src.name, args.check,
                            summary.get("PRESERVED"), summary.get("MODIFIED"),
                            summary.get("MISSING"),
                            crep.get("structural_success"),
                            (crep.get("quality") or {}).get("status", "-"),
                        )
                        file_logger.info(
                            "CHECK | level=%s | %s",
                            args.check,
                            json.dumps(crep, ensure_ascii=False,
                                       default=str)[:4000],
                        )
                        if not crep.get("structural_success", True):
                            # 只影响计数与日志: 透明模式的原则是**不改已产出
                            # 的文件**。把它记成 failed 会让人以为文件坏了,
                            # 因此单独计一个"验证未通过"。
                            counters["check_failed"] += 1
                    else:
                        logger.info(
                            "[CHECK] %s | level=%s | %s", src.name,
                            args.check, crep.get("status", "-"),
                        )
                else:
                    logger.info(
                        "[SYNC-OK] %s | already aligned — byte-identical "
                        "copy | %s",
                        src.name, dst,
                    )
                    logger.info(
                        "[CHECK] %s | level=%s | 输出与源文件字节级相同, "
                        "元数据对比恒等, check 跳过 (与源和源比较同义)",
                        src.name, args.check,
                    )
                counters["done"] += 1
            elif outcome == "skipped":
                counters["sync_skipped"] += 1
                sync_detail = f"sync skipped: {res['detail']}"
                logger.warning(
                    "[SYNC-SKIP] %s | %s — 输出为源文件原样拷贝 | %s",
                    src.name, res["detail"], dst,
                )
                counters["done"] += 1
            else:
                counters["sync_failed"] += 1
                sync_detail = f"sync failed: {res['detail']}"
                logger.warning(
                    "[SYNC-FAIL] %s | %s — 输出为源文件原样拷贝 | %s",
                    src.name, res["detail"], dst,
                )
                counters["done"] += 1
        except Exception as exc:
            logger.error("[SYNC-FAIL] %s | %s", src, exc)
            file_logger.exception("CHANNEL SYNC TRANSPARENT FAILED")
            counters["failed"] += 1
        if status is not None:
            status.finish(src, "done")
            if sync_detail:
                # finish() 会清空 detail。`done` 在透明模式下的含义是
                # "输出文件可用" (文件级失败也会产出源拷贝), 所以"同步是否
                # 真的发生"必须单独写出来; 否则看板显示 done, total.log 显示
                # [SYNC-FAIL], 看起来像两个互相矛盾的事实。
                status.update(src, "done", sync_detail[:160])

    status.mark_finished()
    logger.info("============================================================")
    logger.info(
        "Transparent finished | done=%d skipped=%d failed=%d dry-run=%d "
        "check_failed=%d sync_skipped=%d sync_failed=%d",
        counters["done"], counters["skipped"], counters["failed"],
        counters["dry-run"], counters["check_failed"],
        counters["sync_skipped"], counters["sync_failed"],
    )
    if counters["sync_failed"]:
        # `done` 里包含这些文件 —— 它们的输出是**可用的源文件拷贝**。这里
        # 不把它们算成 `failed` (进程/文件失败), 而是单列, 否则汇总里的
        # `failed=0` 会与上面的 `[SYNC-FAIL]` 警告互相矛盾。
        logger.warning(
            "SYNC FAILED %d 个文件: 音频未做任何修正, 输出为源文件原样拷贝 "
            "(视频/容器无损); 详见上文 [SYNC-FAIL] 行",
            counters["sync_failed"],
        )
    if counters["check_failed"]:
        # 验证未通过**不是**文件失败: 透明模式不改已产出的文件, 这里只把
        # 数量与位置报出来, 由人决定要不要回退。
        logger.warning(
            "CHECK FAILED %d 个文件: 输出文件保持原样, 详见上文 [CHECK] 行",
            counters["check_failed"],
        )
    logger.info("Total log: %s", total_log)
    logger.info("Dashboard data: %s", dashboard_json)
    logger.info("============================================================")
    # 只有**进程级失败**进退出码: 透明模式的契约是"文件级失败 -> 输出源
    # 拷贝", 产物仍可用; 把 sync_failed 也算失败会让批处理重试同一文件,
    # 而重跑的结果不会不同。sync_failed 只出现在汇总与 warn.log 里。
    return 1 if counters["failed"] else 0


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

    # --repair: 归档成品修复模式 (与转码共用同一入口, 供批处理直接调用)。
    # 在解析编码器/工具链之前分流: 修复不需要编码器配置, 只需要
    # ffmpeg/ffprobe/GPAC, 由 archive_repair 自己解析。
    if getattr(args, "repair", False):
        return repair_main(args, input_root, output_root)

    # 通用工具链解析 (transparent 模式只需要 ffmpeg/ffprobe/GPAC)
    try:
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

    # --channel-sync-transparent: 剪辑前预处理独立流程 (跳过视频编码,
    # 不依赖编码器配置)
    if args.channel_sync_transparent:
        return transparent_main(
            args=args,
            script_dir=script_dir,
            input_root=input_root,
            output_root=output_root,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            gpac=gpac,
            gyroflow=gyroflow,
        )

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
            if args.encoder:
                encoder_name = args.encoder
            elif args.experimental_multihw:
                # multihw does its own probe of both backends a few lines
                # below; skip the default autoselect so --check-features
                # is not run twice (each probe costs seconds).
                encoder_name = "nvenc"
            else:
                # v0.6.2: default backend is capability-first
                # NVENC -> QSV -> x265 instead of the old fixed x265.
                print("Default backend autoselect (no --encoder given):")
                encoder_name = resolve_default_backend(args, script_dir)
                print(f"  -> {encoder_name}")
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
        # --experimental-multihw owns hardware-backend selection: it probes,
        # ranks and schedules NVENC+QSV itself. Any explicit --encoder is a
        # conflict and is rejected up front -- including `nvenc`/`qsv`, which
        # previously acted as a silent "pin the primary backend" hint. A mode
        # that picks its own backends cannot also be told which to use, and a
        # hint that only sometimes wins is the kind of ambiguity that produces
        # a report whose stated backend is not the one that ran.
        multihw_backends: list[str] = []
        if args.experimental_multihw:
            if args.encoder:
                raise ValueError(
                    f"--experimental-multihw selects its own hardware "
                    f"encoders; --encoder {args.encoder} conflicts with it. "
                    f"Drop --encoder to let multihw probe and schedule the "
                    f"available hardware backends."
                )
            print("--experimental-multihw: probing hardware backends "
                  "(no --encoder required):")
            multihw_backends = _hw_encoders_available(script_dir, print)
            if not multihw_backends:
                raise ValueError(
                    "no usable hardware encoder: --experimental-multihw "
                    "requires at least one usable HEVC hardware backend "
                    "(NVENC or QSV) and none was found. It does not fall back "
                    "to x265 by design. Run `1kt.py --check-features` to "
                    "inspect this machine."
                )
            if len(multihw_backends) == 1:
                print(f"  [WARN] only {multihw_backends[0]} is available — "
                      f"multihw degrades to a single-backend pool "
                      f"(quality-consistency caveat no longer applies)")
            # multihw owns the choice outright; no --encoder hint to honour.
            encoder_name = multihw_backends[0]
            is_hardware = True

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

    quality_opts = (
        config.get("quality_check") if isinstance(config, dict) else None
    )
    # 生产工作点作为基线, config 文件的 channel_sync 块覆盖其上
    from core.channel_sync import production_opts as _prod_opts2

    channel_sync_opts = _prod_opts2(
        config.get("channel_sync") if isinstance(config, dict) else None
    )
    channel_sync_enabled = bool(
        getattr(args, "channel_sync", False)
        or getattr(args, "channel_sync_transparent", False)
    )

    # P4.6 — 时变同步: CLI 显式 opt-in。只覆写**调用方真的给了值**的键, 因此
    # 未指定时 config 块里的值保持不变 (与其它 CLI 覆盖同一手法)。
    #
    # 覆盖逻辑在 `_apply_time_varying_cli`: 透明批量入口 (`transparent_main`)
    # 与本处**共用同一份实现**。它曾经只写在这里, 于是透明模式把
    # `--channel-sync-tv*` 静默丢掉 (见该函数注释)。
    #
    # `_validate_tv_opts` 在 core.channel_sync 里做**必需参数**校验: policy 与
    # coordinate 缺任何一个都拒绝执行 (逐文件, 不静默退回固定延迟)。
    _tv_flag = getattr(args, "channel_sync_tv", None)
    channel_sync_opts = _apply_time_varying_cli(args, channel_sync_opts)
    # --channel-sync-tv 隐含 --channel-sync (与 transparent 同一处理)
    if _tv_flag:
        channel_sync_enabled = True

    # P4.12 — 分组锚点: 与上面的 P4.6 同一手法 (只覆写调用方真的给了值的键,
    # 因此 config 块里的其它 channel_sync 键保持不动)。`--channel-sync-groups`
    # 隐含 --channel-sync: 用户说"要分组"就等于说"要做延时补偿"。
    _grp_flag = getattr(args, "channel_sync_groups", None)
    _grp_thr = getattr(args, "channel_sync_group_threshold", None)
    if _grp_flag is not None or _grp_thr is not None:
        channel_sync_opts = dict(channel_sync_opts or {})
        if _grp_flag is not None:
            channel_sync_opts["group_anchor_enabled"] = bool(_grp_flag)
        if _grp_thr is not None:
            channel_sync_opts["group_snr_threshold"] = float(_grp_thr)
    if _grp_flag:
        channel_sync_enabled = True

    # v0.8.7 — 精确模式。同一手法: 只覆写真的给了值的键, 并且隐含
    # --channel-sync (说"要更精确"当然等于说"要做对齐")。
    _acc_flag = getattr(args, "channel_sync_accurate", None)
    if _acc_flag is not None:
        channel_sync_opts = dict(channel_sync_opts or {})
        channel_sync_opts["channel_sync_accurate"] = bool(_acc_flag)
    if _acc_flag:
        channel_sync_enabled = True

    # 显式音频计划 (v0.8.0+): 经典软件路径 (x265 / svtav1) 与硬件后端
    # (nvenc / qsv, 含 AV1) 都支持; Sony / DJI 保留管线有自己的音频处理方式,
    # 本阶段不去重构它们 —— 那些**文件**会被逐文件明确拒绝 (见 core/batch_hw),
    # 绝不"静默忽略用户显式给出的计划"。
    audio_plan_request = None
    if getattr(args, "audio_plan", None):
        from production.output import load_audio_plan_request

        try:
            audio_plan_request = load_audio_plan_request(args.audio_plan)
        except Exception as exc:                     # noqa: BLE001
            print(f"[FATAL] --audio-plan: {exc}", file=sys.stderr)
            return 2
        if channel_sync_enabled:
            print(
                "[FATAL] --audio-plan and --channel-sync/--channel-sync-"
                "transparent both decide the final audio; use one of them.",
                file=sys.stderr,
            )
            return 2
        if audio_plan_request.is_empty:
            # 只有编码格式、没有任何选择意图 -> 计划解析出来就是**默认计划**,
            # 执行图会是 NONE。此时直接当"未启用", 从而连"视频专用命令派生"
            # 都不会发生: 走的完全是既有路径, 零浪费。
            audio_plan_request = None

    work_root = output_root / ".1ktwork"
    logger = setup_logger(
        total_log,
        log_level=resolve_log_level(args.log_level),
        verbose=bool(args.verbose),
        fresh=bool(args.fresh_log),
    )
    if audio_plan_request is not None and is_hardware:
        logger.warning(
            "[AUDIO] --audio-plan on a hardware backend: the video artifact "
            "is produced video-only and the audio is composed separately; "
            "Sony/DJI sources will be refused per file (not implemented on "
            "the preservation paths)"
        )

    if is_hardware:
        if args.experimental_multihw:
            print(EXPERIMENTAL_BANNER)
            if len(multihw_backends) >= 2:
                logger.warning(
                    "[EXPERIMENTAL] multihw enabled: quality consistency "
                    "NOT guaranteed"
                )
            else:
                # Degraded on purpose: only one hardware backend exists,
                # so cross-backend quality spread cannot occur.
                logger.warning(
                    "[WARNING] multihw requested but only %s is usable; "
                    "running a single-backend pool (experimental quality "
                    "caveat does not apply)",
                    multihw_backends[0] if multihw_backends else "none",
                )
            backend = hw_backend_for("nvenc", script_dir, args, work_root, logger)
            backend_qsv = hw_backend_for("qsv", script_dir, args, work_root, logger)
        else:
            backend = hw_backend_for(encoder_name, script_dir, args, work_root, logger)

    # 环境版本记录 (软件 + 驱动) -> 结构化 JSON/CSV, 供行为复现
    try:
        from core.versions import collect_versions, write_version_report

        nvencc_path = None
        qsvencc_path = None
        if is_hardware:
            from core.config import find_hw_tool

            if encoder_name in ("nvenc", "nvenc-av1") or \
                    args.experimental_multihw:
                nvencc_path = find_hw_tool(script_dir, "NVEncC64.exe")
            if encoder_name in ("qsv", "qsv-av1") or \
                    args.experimental_multihw:
                qsvencc_path = find_hw_tool(script_dir, "QSVEncC64.exe")
        versions = collect_versions(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            gpac_version=gpac.version(),
            nvencc=nvencc_path,
            qsvencc=qsvencc_path,
            gyroflow=gyroflow,
            probe_svt=(encoder_name == "svtav1"),
            encoder=backend.name,
        )
        vj, vc = write_version_report(versions, logs_root)
        logger.info(
            "ENV VERSIONS | ffmpeg=%s | encoder=%s | %s",
            versions["tools"].get("ffmpeg", "?"),
            backend.name,
            "; ".join(
                f"{d['gpu']}={d['driver_version']}"
                for d in versions.get("gpu_drivers", [])
            )[:160],
        )
        logger.info("ENV REPORT | %s | %s", vj, vc)
    except Exception as exc:
        logger.warning("ENV VERSIONS FAILED | %s", exc)

    # ``--preset all`` was removed: it fanned one run out over every preset,
    # which multiplied output layout, retry-list semantics and (with multihw)
    # backend combinations by four. Running the CLI once per preset is
    # explicit and produces the same per-preset output paths, because each
    # single-preset run never inserts the <PRESET>/ level.
    requested = [args.preset.upper()]
    multiple_presets = len(requested) > 1        # always False; kept for the
    #                                              shared output-layout helper

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
    # Bounded and positive. The previous check accepted anything `int()` took,
    # so `--jobs 0` and `--jobs -3` silently meant single-threaded and
    # `--jobs 256` was handed straight to ThreadPoolExecutor. A concurrency
    # cap is a machine fact, not a preference: past it the work is oversubscribed
    # against x265's own frame threads and gets slower, not faster.
    if jobs_value not in ("1", "auto"):
        try:
            jobs_n = int(jobs_value)
        except ValueError:
            print(f"[FATAL] --jobs must be 1, N or auto (got {jobs_value!r})",
                  file=sys.stderr)
            return 2
        jobs_cap = max(2, min(32, (os.cpu_count() or 4) * 2))
        if jobs_n < 1:
            print(f"[FATAL] --jobs must be >= 1 (got {jobs_n}); "
                  f"use 1 for single-threaded.", file=sys.stderr)
            return 2
        if jobs_n > jobs_cap:
            print(f"[FATAL] --jobs {jobs_n} exceeds this machine's cap "
                  f"({jobs_cap} = min(32, 2xCPU)); higher concurrency "
                  f"oversubscribes the encoder's own threads.",
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
    if not is_hardware and jobs_value != "1":
        # Disclosed rather than implied. --jobs is honoured by the hardware
        # pools; the software path is still a sequential loop, so a run that
        # asks for concurrency there would otherwise look like it got some.
        logger.warning(
            "[JOBS] --jobs %s has no effect on the software backend "
            "(%s): this path is sequential. Concurrency applies to the "
            "hardware backends (nvenc/qsv).",
            jobs_value, backend.name,
        )
    logger.info("Experimental multihw: %s", args.experimental_multihw)
    logger.info("Headless: %s", args.headless)
    logger.info("Keep work dirs: %s", args.keep_work)
    logger.info("============================================================")

    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}

    # ------------------------------------------------------------------
    # Hardware-first decode capability snapshot (probed once per run).
    #
    # Built AFTER the output tree exists so the audit artefact can be saved
    # next to the rigaya caps, and BEFORE any encoding so every command is
    # constructed from the same snapshot the selection used -- otherwise a
    # re-probe could disagree with the plan that was already chosen.
    # ------------------------------------------------------------------
    decode_caps = None
    decode_memo: dict = {}
    try:
        from encoders.hwdecode_ffmpeg import build_caps, save_caps

        decode_caps = build_caps(ffmpeg, logger=logger)
        dc_json, dc_raw = save_caps(decode_caps, work_root / "caps")
        logger.info(
            "DECODE CAPS | ffmpeg=%s | hwaccels=%s | devices=%s",
            decode_caps.ffmpeg_version or "?",
            ",".join(decode_caps.hwaccels) or "none",
            ", ".join(
                f"{d['hwaccel']}[{d['index']}]={d['init']['status']}"
                for d in decode_caps.devices
            ) or "none",
        )
        logger.info("Decode caps files: %s | %s", dc_json, dc_raw)
    except Exception as exc:                       # noqa: BLE001
        # A failed capability probe must not abort the run, and must not be
        # recorded as "hardware unsupported": plan_decode() treats a missing
        # snapshot as no_hw_caps and falls back to software, out loud.
        logger.warning(
            "[DECODE CAPS] probe failed (%s); software decode will be used",
            exc,
        )

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
                # 中间产物的容器必须与实际写入的一致: SVT-AV1 走 ffmpeg,
                # 而 ffmpeg 9 的 MOV muxer 不接受 AV1 -> 中间体只能是 MP4;
                # x265 走 MOV。最终交付由 place_final 负责 (同名则改名,
                # 容器不同则无损重封装)。
                part_ext = ".part.mp4" if backend.name == "svtav1" else ".part.mov"
                part_dst = dst.with_name(dst.stem + part_ext)
                file_log = per_file_log_path(
                    src, input_root, logs_root, preset
                )
                file_logger = build_file_logger(file_log)
                if status is not None:
                    status.start(src, preset, backend.name)
                # Whether this iteration will do any work at all: a file whose
                # output already exists is skipped, and creating then deleting a
                # scratch directory for it would be pure noise.
                skipped_existing = dst.is_file() and dst.stat().st_size > 0
                if skipped_existing:
                    logger.info("[SKIP] %s | %s", src, dst)
                    file_logger.info(
                        "SKIP | output already exists | output=%s", dst
                    )
                    counters["skipped"] += 1
                    if status is not None:
                        status.finish(src, "skipped")
                    continue
                # One job = one scratch directory. Bracketed around the whole
                # dispatch so the exit path runs on an exception or Ctrl-C too,
                # which a cleanup call at each return would have missed.
                with _job_scratch(
                    work_root=work_root, src=src,
                    keep_work=args.keep_work,
                    preserve_reports=preserve_reports,
                    logger=logger, file_logger=file_logger,
                ):
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

                    # --- hardware-first decode decision (independent of encoder) ---
                    # Sony / DJI / generic are NOT distinguished here: the policy is
                    # hardware-first for every input, and the preservation payloads
                    # are re-added from the bundle by MP4Box afterwards, so they do
                    # not constrain which decoder produced the video track.
                    decode_plan = None
                    if decode_caps is not None:
                        from encoders.hwdecode_ffmpeg import plan_decode

                        decode_plan = plan_decode(
                            caps=decode_caps,
                            ffmpeg=ffmpeg,
                            codec=prepared.src_info.codec,
                            chroma=prepared.src_info.chroma,
                            depth=prepared.src_info.bit_depth,
                            # Probe fixtures are small clips keyed by
                            # (codec, chroma, depth, geometry), so they are shared
                            # across runs and live outside the output tree. Putting
                            # them under .1ktwork would accumulate: per-job cleanup
                            # removes only <job_id>/, never the work root, so a
                            # decode_probe/ there would grow with every new format.
                            # `preservation/gpac.py` uses the same system-temp
                            # scratch convention.
                            probe_work_dir=None,
                            memo=decode_memo,
                        )
                        file_logger.info(
                            "DECODE_ROUTE | %s", decode_plan.log_line()
                        )
                        if decode_plan.hardware:
                            logger.info(
                                "[DECODE] %s | %s", src.name, decode_plan.log_line()
                            )
                        for w in decode_plan.warnings:
                            logger.info("[DECODE] %s | %s", src.name, w)
                            file_logger.info("DECODE_NOTE | %s", w)

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
                    quality_csv = (
                        logs_root / "quality_samples.csv"
                        if args.check == "full" else None
                    )
                    # --no-metadata: the metadata-preservation policy, NOT a decode
                    # switch. Hardware-first decode was already decided above and
                    # is unaffected. What changes is only whether the source family
                    # gets its container-rebuild pipeline: with the flag set, Sony
                    # and DJI files take the ordinary transcode path, so the
                    # rtmd/nrtm/djmd/dbgi/tmcd tracks are not rebuilt and the
                    # XAVC/DJI branding is not restored.
                    #
                    # Everything that protects output *correctness* still runs:
                    # probe, encode return code, non-empty output, ffprobe
                    # readability, audio stream copy, place_final's container
                    # handling and the frame checks. Skipping preservation must
                    # never be read as "skip validation".
                    no_metadata = bool(getattr(args, "no_metadata", False))
                    preserve_metadata = bool(
                        getattr(args, "preserve_metadata", False))
                    is_sony = any(
                        st.get("codec_type") == "data"
                        and st.get("codec_tag_string") == "rtmd"
                        for st in prepared.streams
                    )
                    is_dji = is_dji_source(prepared.streams)
                    dji_tool: str | None = None
                    # DJI 默认元数据策略 (2026-10-25): 除 Action 系列外
                    # (Pocket / 无人机) 默认**不保留** metadata, 走普通转码
                    # 路径; 需要保留时显式 --preserve-metadata。
                    if is_dji and not no_metadata and not preserve_metadata:
                        from preservation.dji import keep_metadata_by_default

                        keep_dji, dji_tool = keep_metadata_by_default(gpac, src)
                        if not keep_dji:
                            no_metadata = True
                            logger.info(
                                "[META_POLICY] %s | DJI 非 Action 机型 "
                                "(tool=%r): 默认不保留元数据, 走普通转码路径 "
                                "(可用 --preserve-metadata 覆盖)",
                                src.name, dji_tool,
                            )
                            file_logger.info(
                                "META_POLICY | dji_default_skip | tool=%r | "
                                "container rebuild skipped", dji_tool,
                            )
                    if no_metadata:
                        keep = ("sony" if is_sony
                                else "dji" if is_dji
                                else "generic")
                        logger.info(
                            "[META_POLICY] %s | no_metadata: %s preservation "
                            "skipped, ordinary transcode path used",
                            src.name, keep,
                        )
                        file_logger.info(
                            "META_POLICY | no_metadata | source_family=%s | "
                            "container rebuild skipped", keep,
                        )
                    if is_sony and not no_metadata:
                        result = encode_one_sony(
                            src=src, dst=dst, prepared=prepared, profile=profile,
                            preset=preset, backend=backend, engine=engine,
                            ffmpeg=ffmpeg, ffprobe=ffprobe,
                            postprobe_csv=postprobe_csv,
                            postprobe_stream_csv=postprobe_stream_csv,
                            gpac=gpac, gyroflow=gyroflow, work_root=work_root,
                            check_level=args.check, codec=codec,
                            quality_opts=quality_opts,
                            quality_csv=quality_csv,
                            channel_sync=channel_sync_enabled,
                            channel_sync_opts=channel_sync_opts,
                            logger=logger, file_logger=file_logger,
                            dry_run=args.dry_run,
                            decode_plan=decode_plan,
                        )
                    elif is_dji and not no_metadata:
                        result = encode_one_dji_x265(
                            src=src, dst=dst, prepared=prepared, profile=profile,
                            preset=preset, backend=backend, engine=engine,
                            ffmpeg=ffmpeg, ffprobe=ffprobe,
                            postprobe_csv=postprobe_csv,
                            postprobe_stream_csv=postprobe_stream_csv,
                            gpac=gpac, gyroflow=gyroflow, work_root=work_root,
                            check_level=args.check, video_entry=video_entry,
                            quality_opts=quality_opts,
                            quality_csv=quality_csv,
                            channel_sync=channel_sync_enabled,
                            channel_sync_opts=channel_sync_opts,
                            logger=logger, file_logger=file_logger,
                            dry_run=args.dry_run,
                            decode_plan=decode_plan,
                        )
                    else:
                        result = encode_one(
                            src=src, dst=dst, part_dst=part_dst,
                            prepared=prepared, ffmpeg=ffmpeg, ffprobe=ffprobe,
                            postprobe_csv=postprobe_csv,
                            postprobe_stream_csv=postprobe_stream_csv,
                            profile=profile, preset=preset, backend=backend,
                            engine=engine, check_level=args.check,
                            quality_opts=quality_opts,
                            quality_csv=quality_csv,
                            channel_sync=channel_sync_enabled,
                            channel_sync_opts=channel_sync_opts,
                            audio_plan_request=audio_plan_request,
                            gpac=gpac,
                            work_dir=work_root / job_id_for(src),
                            logger=logger,
                            file_logger=file_logger, dry_run=args.dry_run,
                            decode_plan=decode_plan,
                            # 元数据策略 = 不保留 (--no-metadata / DJI 非
                            # Action 默认) 时, 普通转码命令只保留主视频+
                            # 音频+字幕: 相机 data 轨与 attached_pic 无法
                            # 忠实穿过 FFmpeg MOV 中间容器。
                            drop_metadata_streams=no_metadata,
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
            # Hardware decode for NVENC/QSV is the encoders' *own* reader,
            # resolved per file by encoders.hwdecode.route_decode from the
            # **source class**: Sony -> --avsw before any attempt (the deployed
            # reader drops leading pictures on XAVC), everything else -> --avhw
            # on the deployed binary, with the integrity gate deciding whether
            # the result may be delivered. There is no FFmpeg frame feed on
            # this path (the x265/SVT-AV1 paths use FFmpeg's NVDEC/QSV
            # directly, above), and no patched research build is required.
            hw_decode="auto",
            hw_decode_memo=decode_memo,
            check_level=args.check,
            ffmpeg=ffmpeg,
            quality_opts=quality_opts,
            channel_sync=channel_sync_enabled,
            channel_sync_opts=channel_sync_opts,
            preserve_metadata=bool(getattr(args, "preserve_metadata", False)),
            dry_run=args.dry_run,
            failed_path=failed_path,
            status=status,
            video_handoff=_audio_plan_handoff(
                audio_plan_request=audio_plan_request,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                logger=logger,
            ),
        )

        if args.experimental_multihw:
            if len(multihw_backends) >= 2:
                # jobs_value is passed here too: multihw obeys the same global
                # resource budget as the single-backend pool rather than
                # letting two per-backend controllers add up.
                round_counters = run_multihw_pool(
                    ctx, backend, backend_qsv, sources, logger, jobs_value
                )
            else:
                # Only one backend is usable: a single-backend pool with
                # --jobs semantics is strictly better than a dual pool
                # where one queue is always empty.
                round_counters = run_hw_pool(
                    ctx, backend, sources, logger, jobs_value
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
