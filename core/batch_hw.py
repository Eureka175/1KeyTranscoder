"""Hardware batch processing: per-file handlers, downgrade ladder,
failure records, and worker schedulers.

Extracted from the main program so 1kt.py stays a thin orchestrator.

Key behaviors (final design):
- hardware encode paths decode with the encoder's **own** reader, chosen
  per file by `encoders.hwdecode.route_decode`: `--avhw` on the
  sha256-pinned patched build when the input is in the runtime-proven
  allowlist, `--avsw` on the shipped build otherwise.  There is no FFmpeg
  frame feed into a rigaya encoder (the x265/SVT-AV1 backends decode with
  FFmpeg's NVDEC/QSV in-process instead -- see encoders/hwdecode_ffmpeg.py);
  every `--avhw` result is gated on the frame-integrity check
  (encoders/integrity.py);
- runtime encode failures NO LONGER open a prompt window: the
  downgrade ladder runs automatically (source -> 10bit 4:2:0 ->
  8bit 4:2:0) with a prominent WARNING per rung printed in the work
  window and recorded in logs + failed_files.json when exhausted;
- capability-driven downgrades happen silently up front (WARNING);
- reader failures fall back to an MP4Box strip once.

Hardware-decode fallback is deliberately narrow: only a *failed
integrity gate* or a *reader that was not constructed as requested*
causes a rerun with software decode, the hardware artifact is deleted
first so it can never be delivered, and the number of such fallbacks per
file is capped so a broken toolchain cannot loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
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
from encoders.hwdecode import (
    POLICY_OFF as HW_DECODE_OFF,
    POLICY_REQUIRE as HW_DECODE_REQUIRE,
    R_REQUIRE_UNMET,
    SOURCE_SONY,
    SOURCE_UNKNOWN,
    classify_reader_failure,
    memoize_unavailable,
    route_decode,
    source_kind_from_streams,
)
from encoders.integrity import (
    verify_container_counts,
    verify_encode,
)
from encoders.nvencc import NvencBackend
from encoders.qsvencc import QsvBackend
from encoders.resize import ResizeRequest, ResizeSpec, resolve_resize

from .config import find_hw_tool
from .dashboard import DashboardStatus
from .hw_vbv import apply_nvenc_dynamic_profile
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
from preservation.policy import log_metadata_plan, resolve_metadata_plan
from preservation import dji, isobmf

StatusCb = Callable[[str, str], None]

#: Container of every hardware video intermediate (`-f` for NVEncC /
#: QSVEncC).  MOV is the encode *working* container: GPAC's MKV reader
#: quantizes timestamps to milliseconds, so MKV is unusable here, and
#: the intermediate must be one predictable container that every
#: downstream stage (preservation rebuild, channel-sync re-mux) names
#: explicitly.  The **final** container is a separate decision and
#: follows the source — see ``core.paths.final_suffix_for`` and
#: ``place_final``.  Stated here rather than left to the backend default
#: so the two are never confused again.
HW_VIDEO_CONTAINER = "mov"

#: MOV is **not** available for AV1 — the muxer rejects it outright
#: ("av1 only supported in MP4 and AVIF"), so an AV1 intermediate must
#: be MP4.  This is a codec constraint, not a preference, and it is the
#: same constraint that makes the software path use `.part.mp4` for
#: SVT-AV1 (`1kt.py`) and `preservation/pipeline.py` pass an MP4
#: `encoded_path` for AV1.  Keyed by codec so the rule lives in one
#: place instead of being re-derived at each call site.
_INTERMEDIATE_CONTAINER_BY_CODEC = {"av1": "mp4"}

#: muxer name -> file extension for the hardware intermediate.
_CONTAINER_SUFFIX = {"mov": ".mov", "mp4": ".mp4"}


def intermediate_container(codec: str) -> str:
    """``-f`` container for a hardware encode of ``codec``.

    ``"mov"`` for everything except AV1; AV1 must be MP4 because the
    MOV muxer refuses it (measured on the pinned tools: NVEncC 9.31 and
    QSVEncC 8.26 both fail with ``av1 only supported in MP4 and AVIF``
    and write 0 frames).
    """
    return _INTERMEDIATE_CONTAINER_BY_CODEC.get(codec, HW_VIDEO_CONTAINER)


@dataclass(frozen=True)
class VideoHandoff:
    """本层刚产出**视频产物**后, 交给外部完成最终文件的请求 (v0.8.0+)。

    这是**视频侧的单向契约**: 硬件批量层只知道自己产出了一个"画面已经在里面
    的独立文件", 以及"最终该写到哪里"。它不知道音频、不知道 `AudioPlan`、
    也不知道对方会用这个产物做什么 —— 因此 NVENC / QSV / 硬件解码都不需要
    认识音频域 (由回归的 AST 断言钉住)。

    注册了 handoff 时本层的行为只有两处不同:

    * 编码命令**不带** `--audio-copy` —— 产物必须是纯视频, 否则最终容器里会
      多出一条没人要的音轨;
    * 编码成功后**不**把 `.part.mov` 改名成 `dst`, 而是把 finalize 交给对方。
    """

    source: Path
    video: Path
    output: Path
    work_dir: Path
    #: 该文件的 per-file logger (接手方写自己的 per-file 行时用; 可以为 None)
    file_logger: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "video": str(self.video),
            "output": str(self.output),
            "work_dir": str(self.work_dir),
        }


@dataclass
class HandoffOutcome:
    """handoff 的执行结果 (视频侧只读取这三个字段)。"""

    ok: bool = True
    applied: bool = False
    detail: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "applied": bool(self.applied),
            "detail": self.detail,
            "errors": list(self.errors),
        }


#: 每文件编码完成后的可选后处理钩子 (视频侧唯一的"外部接手"接缝)。
VideoHandoffHook = Callable[[VideoHandoff], HandoffOutcome]


# Reader identity each tool prints in "Input Info" when it really built
# the requested reader. Asserted by the integrity gate: a requested
# `avhw` that silently became `avsw` must never count as a hardware pass.
READER_IDENTITY: dict[str, dict[str, str]] = {
    "nvencc": {"avhw": "avcuvid", "avsw": "avsw"},
    "qsvencc": {"avhw": "avqsv", "avsw": "avsw"},
}

# Cap on hardware->software decode reruns for a single file. One is the
# legitimate case; more than one means the software path is also failing
# and the ladder must terminate rather than loop.
MAX_HW_DECODE_FALLBACKS = 1


# ---------------------------------------------------------------------------
# which binary performs the rigaya hardware decode
# ---------------------------------------------------------------------------
#
# **The deployed binary.**  Production does not consult `tools/avhw/` and does
# not require a sha256 match to enable native `--avhw`: the defect the patched
# build existed for is Sony-specific, Sony is now decoded in software before
# any attempt, and `--avhw` is therefore only ever asked of material that
# defect was not measured on.  The integrity gate below is what decides
# whether a hardware result may be delivered.
#
# `use_decode_binary` remains the single place that pins argv[0], so a future
# experimental binary can be substituted without touching any backend
# signature -- and so a mismatch is refused rather than silently accepted.
#
# `encoders.hwdecode.resolve_decode_tool` still resolves the research build by
# hash.  It is not called from this module: it serves the research line and the
# historical matrix, which still need it (see that function's docstring).


def use_decode_binary(
    cmd: list[str], default_tool: Path, decode_tool: Path
) -> list[str]:
    """Point an already-built argv at the binary that will perform the decode.

    Both must be the same file in the production configuration: the backends
    build argv against the deployed tool, and the deployed tool is what runs
    hardware decode.  The check is kept because argv and the binary that
    executes it drifting apart is exactly how a logged reader identity stops
    meaning anything, and because it is the seam an experimental binary would
    use.

    Refuses rather than guesses when argv does not start with the expected
    executable.
    """
    if not cmd:
        raise ValueError("use_decode_binary: empty command")
    if Path(cmd[0]) != Path(default_tool):
        raise ValueError(
            f"use_decode_binary: argv[0]={cmd[0]!r} is not the expected "
            f"tool {default_tool}; refusing to substitute the decode binary"
        )
    if Path(default_tool) == Path(decode_tool):
        return list(cmd)
    return [str(decode_tool), *cmd[1:]]


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
    """Prominent WARNING: work window + total log + per-file log.

    The console print is best-effort only: a broken/closed stdout
    (headless service, redirected log pipe) must never crash the job —
    the log files are the authoritative record."""
    logger.warning("[WARNING] %s", msg)
    file_logger.warning("WARNING | %s", msg)
    try:
        print(f"[WARNING] {msg}", flush=True)
    except OSError:
        pass
    if warnings is not None:
        warnings.append(msg)


def audio_track_ids(gpac: GpacContainerBackend, source: Path) -> list[int]:
    """Source audio track IDs via MP4Box -info (handler = soun).

    Uses -info text parsing rather than -diso XML: DJI files' diso
    output fails ElementTree parsing (hidden mjpeg cover track), and
    the strip fallback must work for every source family."""
    _, tracks = gpac.parse_info(gpac.info(source))
    return [t["id"] for t in tracks if t["handler"] == "soun"]


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
    """Post-delivery GC: keep report.json in logs, remove the rest.

    Thin wrapper over :func:`core.workdir.gc_job_dir` so the hardware and
    software paths share one implementation. Two behaviours changed when the
    two were merged, both deliberate:

    * a failure to copy ``report.json`` out now **retains** the directory
      instead of warning and deleting it anyway -- deleting the only copy of
      the report is data loss;
    * the root ``.1ktwork`` and any sibling job directory are still never
      touched (``gc_job_dir`` removes only ``work_dir``).
    """
    from core.workdir import gc_job_dir

    gc_job_dir(
        work_dir=work_dir,
        job_id=job_id,
        preserve_reports=preserve_reports,
        keep_work=keep_work,
        logger=logger,
        file_logger=file_logger,
    )


def hw_backend_for(
    encoder_name: str,
    script_dir: Path,
    args: argparse.Namespace,
    work_root: Path,
    logger: logging.Logger,
):
    """Build a hardware backend + probe/save capabilities at run start.

    encoder_name: "nvenc"/"qsv" (HEVC) or "nvenc-av1"/"qsv-av1" (AV1).

    The backend always carries the **shipped** build, because that is the
    binary the capability probe ran on and the only one a software
    (``--avsw``) rung may use.  Which binary performs *hardware decode* is a
    separate decision, made per file by
    ``encoders.hwdecode.resolve_decode_tool`` and applied to the argv only
    for an ``--avhw`` rung (see ``hw_encode_with_fallback``).  Swapping the
    tool here instead would make the recorded reader identity depend on
    startup state rather than on the run that actually happened."""
    base = encoder_name.split("-", 1)[0]
    kind = "nvencc" if base == "nvenc" else "qsvencc"
    codec = "av1" if encoder_name.endswith("-av1") else "hevc"
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
        caps = BackendCaps(
            tool=exe_name, codecs={"hevc": CodecCaps(), "av1": CodecCaps()}
        )
    json_path, raw_path = caps.save(caps_dir)
    logger.info(
        "Caps %s: device=%s driver=%s hevc(10bit=%s,422=%s,422-10bit=%s) "
        "av1(10bit=%s)",
        exe_name,
        caps.device,
        caps.driver,
        caps.codecs.get("hevc", CodecCaps()).bit10,
        caps.codecs.get("hevc", CodecCaps()).csp_422,
        caps.codecs.get("hevc", CodecCaps()).bit10_422,
        caps.codecs.get("av1", CodecCaps()).bit10,
    )
    logger.info("Caps files: %s | %s", json_path, raw_path)
    if kind == "nvencc":
        return NvencBackend(tool, caps, codec=codec)
    return QsvBackend(tool, caps, codec=codec)


def _last_encode_fps(raw_log: Path) -> float:
    """Parse the encoder's final 'encoded N frames, F fps' line."""
    try:
        text = raw_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    matches = re.findall(
        r"encoded\s+\d+\s+frames,\s*([\d.]+)\s+fps", text
    )
    return float(matches[-1]) if matches else 0.0


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
    hw_decode: str = HW_DECODE_OFF,
    hw_decode_memo: dict | None = None,
    seek_requested: bool = False,
    source_kind: str = SOURCE_UNKNOWN,
    resize: ResizeSpec | None = None,
    on_encode_complete: Callable[[], None] | None = None,
    integrity_gate: threading.Semaphore | None = None,
    encode_gate: threading.Semaphore | None = None,
    skip_integrity: bool = False,
) -> tuple[list[str], tuple[str, int], float]:
    """Automatic three-tier downgrade ladder (NO prompt window).

    Capability-driven downgrades happen silently up front (WARNING);
    runtime failures run the ladder automatically with a WARNING per
    rung; reader failures use the MP4Box strip fallback once.

    Hardware **decode** on this path is the encoder's *own* reader, chosen
    by ``encoders.hwdecode.route_decode`` from the **source class**:
    Sony sources are decoded in software before any attempt
    (``sony_hardware_decode_safety``), everything else uses native
    ``--avhw`` on the deployed binary.  There is no FFmpeg frame feed here
    -- the encoder opens the source file itself, in both cases.
    Every ``--avhw`` result must pass the integrity gate
    (``encoders.integrity.verify_encode``) before it is accepted; a failed
    gate deletes the artifact and reruns the same rung with ``--avsw``,
    at most once.
    Returns (warnings, final_format). Raises RuntimeError on fatal."""
    warnings: list[str] = []
    caps = backend.caps
    chroma = src_info.chroma
    depth = src_info.bit_depth

    planned, needs_downgrade = plan_initial_format(
        caps, backend.kind, chroma, depth, backend.codec
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

    # --- source-aware hardware-decode routing (HD-B01..B10) --------------
    memo = hw_decode_memo if hw_decode_memo is not None else {}
    route = route_decode(
        policy=hw_decode,
        backend=backend.name.split("-")[0],
        codec=backend.codec,
        chroma=chroma,
        depth=depth,
        memo=memo,
        seek_requested=seek_requested,
        source_kind=source_kind,
    )
    file_logger.info("DECODE_ROUTE | %s", route.log_line())
    if route.hardware:
        logger.info("[HWDEC] %s", route.log_line())
    for w in route.warnings:
        emit_warning(logger, file_logger, w, warnings)
    if route.reason == R_REQUIRE_UNMET:
        raise RuntimeError(f"[FATAL] {route.detail}")
    if not route.hardware and hw_decode == HW_DECODE_REQUIRE:
        raise RuntimeError(f"[FATAL] {route.detail}")

    reader = route.reader
    reader_expected = READER_IDENTITY[backend.kind][reader]
    hw_fallbacks = 0
    integrity_skipped = False
    discarded: list[str] = []
    #: Set only when a *completed* hardware attempt was rejected by the
    #: integrity gate. A source-driven software decision must never be
    #: reported as a fallback (see R_SONY_SOFTWARE).
    fallback_reason = ""

    # Hardware decode runs on the **deployed** binary, in both reader modes.
    # The patched research build is no longer a precondition for --avhw: the
    # defect it existed for is Sony-specific and Sony is now decoded in
    # software before any attempt, so `--avhw` is only ever asked of material
    # that defect was not measured on, and the integrity gate below decides
    # whether the result may be delivered.
    hw_tool = backend.tool

    cur_input = source
    stripped = False
    rung_idx = 0
    color = src_info.color
    # QSV's forcecfr path can emit duplicate/non-monotonic timestamps on
    # genuine VFR timelines.  If that specific muxer error appears, retry
    # the same format rung with --avsync vfr; the source timestamps are
    # preserved and QSVEncC accepts the same file.
    avsync_override: str | None = None
    avsync_retry_attempted = False

    def route_summary(integ, status: str, used_binary) -> str:
        """One line answering "why did this file use --avhw or --avsw?".

        Every field is a value this run established rather than a restatement
        of the request, so the line reads as evidence: `actual_encoder` is the
        binary the process was given, `decode_reader` is the reader requested
        of it, and `software_fallback` is set **only** by an integrity
        rejection.  A source-driven software decision is `route_reason=
        sony_hardware_decode_safety` (or a capability refusal) with
        `software_fallback=False`, because nothing was attempted and nothing
        failed -- reporting that as a fallback would be a lie about the run.
        """
        return (
            f"source_kind={route.source_kind} "
            f"requested_encoder={backend.name} "
            f"actual_encoder={Path(used_binary).name} "
            f"decode_reader={reader} "
            f"decode_policy={route.policy} "
            f"route_reason={route.reason} "
            f"hardware_integrity={integ} "
            f"software_fallback={bool(fallback_reason)} "
            f"fallback_reason={fallback_reason or 'none'} "
            f"final_status={status}"
        )

    while rung_idx < len(rungs):
        c, d = rungs[rung_idx]
        command_kwargs = dict(
            source=cur_input, output=output, profile=profile, chroma=c,
            depth=d, vfr=vfr, audio_copy=audio_copy, color=color,
            reader=reader, container=intermediate_container(backend.codec),
            resize=resize,
        )
        if avsync_override is not None:
            command_kwargs["avsync"] = avsync_override
        cmd, skipped, color_notes = backend.command(**command_kwargs)
        if reader == "avhw":
            cmd = use_decode_binary(cmd, backend.tool, hw_tool)
        if skipped and rung_idx == 0:
            emit_warning(
                logger,
                file_logger,
                f"profile keys not advertised by {backend.name} "
                f"(skipped): {', '.join(sorted(skipped))}",
                warnings,
            )
        if color_notes and rung_idx == 0:
            for note in color_notes:
                emit_warning(
                    logger, file_logger, f"{backend.name}: {note}", warnings
                )
        file_logger.info("COMMAND | %s", subprocess.list2cmdline(cmd))
        # Raw encoder stdout belongs to the job's work directory, not to the
        # user-visible output tree.  It used to sit next to the `.part.mov`
        # (`<output>.part.mov.nvencc.log`) and survived successful delivery,
        # leaving one log per source beside the finished MP4s.  The work dir is
        # GC'd on success and retained on failure, which is exactly the wanted
        # lifetime for a diagnostic log.
        raw_log = work_dir / f"encode.{label}.log"
        try:
            if raw_log.exists():
                raw_log.unlink()
        except OSError:
            pass
        if encode_gate is not None:
            encode_gate.acquire()
        try:
            rc, elapsed = run_hw_tool(
                cmd, raw_log, total_frames, label, progress=show_progress,
                progress_cb=progress_cb,
            )
        finally:
            if encode_gate is not None:
                encode_gate.release()

        # The encoder has already produced a complete intermediate here.  In
        # async-integrity mode this is the point where the next DJI group may
        # be released for encoding.  The callback is deliberately fired only
        # after the single verifier slot has been acquired: a group whose
        # intermediate is merely *waiting* for the verifier must not release
        # another group, otherwise two intermediates could pile up.  The
        # joined source stays on disk until note_finished(), so the verifier
        # can still read the expected container frame count.
        def _notify_encode_complete() -> None:
            if (on_encode_complete is None or rc != 0
                    or not output.is_file()
                    or output.stat().st_size <= 0):
                return
            try:
                on_encode_complete()
            except Exception as exc:                  # noqa: BLE001
                file_logger.warning(
                    "ASYNC-INTEGRITY | encode-complete callback failed: %s",
                    exc,
                )

        # --- integrity gate (HD-C11..C13, HD-F05) ------------------------
        verdict = None
        if reader != "avhw":
            if skip_integrity:
                integrity_skipped = True
                logger.warning(
                    "[INTEGRITY] skipped | --no-metadata | %s | "
                    "reader=%s | integrity=skipped",
                    source.name, reader,
                )
                file_logger.warning(
                    "INTEGRITY | skipped | reason=no_metadata | reader=%s | "
                    "integrity=skipped", reader,
                )
            _notify_encode_complete()
        if reader == "avhw" and skip_integrity:
            integrity_skipped = True
            logger.warning(
                "[INTEGRITY] skipped | --no-metadata | %s | frames=%s | "
                "integrity=skipped",
                source.name, total_frames or "?",
            )
            file_logger.warning(
                "INTEGRITY | skipped | reason=no_metadata | frames=%s | "
                "integrity=skipped",
                total_frames or "?",
            )
            _notify_encode_complete()
        elif reader == "avhw":
            # This gate fully software-decodes the output to count frames.
            # On long 4K files that is minutes, not seconds; log the phase so
            # the run never looks "stuck" after the encoder exits.
            logger.info(
                "[INTEGRITY] verifying hardware-decode output | frames=%s | "
                "size=%.1f MB | software decode (auto threads)",
                total_frames or "?",
                (output.stat().st_size / 1024**2)
                if output.is_file() else 0.0,
            )
            file_logger.info(
                "INTEGRITY | independent decode begins | frames=%s",
                total_frames or "?",
            )
            stop_heartbeat = threading.Event()
            heartbeat_started = time.monotonic()

            def _integrity_heartbeat() -> None:
                while not stop_heartbeat.wait(30.0):
                    elapsed = time.monotonic() - heartbeat_started
                    logger.info(
                        "[INTEGRITY] still software-decoding output | "
                        "elapsed=%.0f min | frames=%s",
                        elapsed / 60.0, total_frames or "?",
                    )
                    file_logger.info(
                        "INTEGRITY | still decoding | elapsed=%.0f sec",
                        elapsed,
                    )

            heartbeat = threading.Thread(
                target=_integrity_heartbeat,
                name="integrity-heartbeat",
                daemon=True,
            )
            heartbeat.start()
            try:
                log_text = raw_log.read_text(
                    encoding="utf-8", errors="replace"
                ) if raw_log.is_file() else ""
            except OSError:
                log_text = ""
            if integrity_gate is not None:
                logger.info(
                    "[INTEGRITY] waiting for the single verifier slot | "
                    "%s | frames=%s",
                    source.name, total_frames or "?",
                )
            try:
                if integrity_gate is not None:
                    integrity_gate.acquire()
                try:
                    # Holding the verifier slot: release the next DJI group
                    # now, but not before this group owns the only pending
                    # verification slot.
                    _notify_encode_complete()
                    verdict = verify_encode(
                        source=source,
                        output=output if (rc == 0 and output.is_file()) else None,
                        log_text=log_text,
                        rc=rc,
                        reader_requested=reader,
                        reader_expected_identity=reader_expected,
                    )
                finally:
                    if integrity_gate is not None:
                        integrity_gate.release()
            finally:
                stop_heartbeat.set()
                heartbeat.join(timeout=2.0)
            file_logger.info("INTEGRITY | %s", verdict.log_line())
            logger.info(
                "[INTEGRITY] %s | %s",
                "PASS" if verdict.ok else "FAIL",
                verdict.log_line(),
            )
            if not verdict.ok:
                _cls, _detail = classify_reader_failure(log_text)
                reason = verdict.reason
                emit_warning(
                    logger,
                    file_logger,
                    f"hardware decode integrity FAILED on {source.name}: "
                    f"{reason} — {verdict.detail}",
                    warnings,
                )
                # The artifact must not survive as a delivery candidate.
                if "discard_hardware_result" in verdict.actions or (
                    "fallback_to_software" in verdict.actions
                ):
                    safe_unlink(output)
                    discarded.append(f"{reason}: {verdict.detail}")
                memoize_unavailable(memo, route, reason, verdict.detail)
                hw_fallbacks += 1
                if hw_fallbacks > MAX_HW_DECODE_FALLBACKS:
                    raise RuntimeError(
                        "[FATAL] hardware decode failed integrity "
                        f"{hw_fallbacks} times ({reason}: {verdict.detail}); "
                        "refusing to retry further"
                    )
                reader = "avsw"
                reader_expected = READER_IDENTITY[backend.kind][reader]
                fallback_reason = f"{reason}: {verdict.detail}"
                emit_warning(
                    logger,
                    file_logger,
                    "hardware decode result discarded -> rerunning this "
                    "format rung with software decode (--avsw)",
                    warnings,
                )
                continue

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
        if ok and (reader == "avsw" and hw_fallbacks > 0
                   and not skip_integrity):
            # A hardware-integrity failure forced a software re-encode.
            # Re-check the replacement artifact by container + independent
            # decode counts; there is no avcuvid/avqsv reader identity to
            # reconcile.  This also runs under the single verifier slot.
            if integrity_gate is not None:
                integrity_gate.acquire()
            try:
                sw_ok, sw_counts = verify_container_counts(
                    source, output, ffprobe=ffprobe)
            finally:
                if integrity_gate is not None:
                    integrity_gate.release()
            file_logger.info(
                "INTEGRITY | software re-encode %s | %s",
                "PASS" if sw_ok else "FAIL", sw_counts,
            )
            logger.info(
                "[INTEGRITY] software re-encode %s | %s",
                "PASS" if sw_ok else "FAIL", sw_counts,
            )
            if not sw_ok:
                ok = False
            else:
                # The delivered artifact is now the software re-encode; the
                # hardware verdict belongs to the discarded attempt and must
                # not turn the accepted fallback into hardware_integrity=FAIL.
                verdict = None
        if ok:
            if integrity_skipped:
                integ = "SKIPPED"
            else:
                integ = "PASS" if (verdict is None or verdict.ok) else "FAIL"
            summary = route_summary(integ, "ok", cmd[0])
            file_logger.info("DECODE_DECISION | %s", summary)
            if route.hardware:
                logger.info("[HWDEC] %s | %s", source.name, summary)
            if discarded:
                file_logger.info(
                    "HWDEC_SUMMARY | fallbacks=%d discarded=%s",
                    hw_fallbacks, " | ".join(discarded),
                )
            return warnings, (c, d), _last_encode_fps(raw_log)

        tail = read_log_tail(raw_log)
        cls = "format" if rc == 0 else classify_failure(tail)

        # QSV forcecfr + VFR can fail at the muxer with a non-monotonic DTS.
        # Retry exactly once on the same rung with --avsync vfr before any
        # format downgrade or reader fallback; otherwise a valid VFR source
        # is reported as an all-rungs failure.
        if (
            not avsync_retry_attempted
            and avsync_override is None
            and vfr
            and backend.kind == "qsvencc"
            and ("non monotonically" in tail.lower()
                 or "invalid, non monotonically" in tail.lower())
        ):
            avsync_retry_attempted = True
            avsync_override = "vfr"
            safe_unlink(output)
            emit_warning(
                logger,
                file_logger,
                "QSV VFR forcecfr failed with non-monotonic DTS -> "
                "retrying same format with --avsync vfr",
                warnings,
            )
            continue

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

        if cls == "environment":
            raise RuntimeError(
                f"[FATAL] environment failure (no downgrade retry): "
                f"{tail[-400:]}"
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


def audio_plan_refusal(
    source_streams: list[dict[str, Any]],
    video_handoff: VideoHandoffHook | None,
) -> str | None:
    """该文件在注册了接手方时是否必须被拒绝? 返回原因 (`None` = 不拒绝)。

    只有 Sony / DJI 保留路径会被拒: 它们有自己的音频处理方式, 本阶段**不**
    重构它们。让它们照常跑就等于"用户显式给出的计划被静默忽略", 因此这里
    明确失败 (与软件路径对不支持的路径 rc=2 拒绝同义)。

    抽成独立函数是为了让这条规则可以被**真实素材**直接断言, 而不是只能靠
    跑一遍完整管线去间接观察。
    """
    if video_handoff is None:
        return None
    sony = is_sony_source(source_streams)
    dji = is_dji_source(source_streams)
    if not (sony or dji):
        return None
    kind = "Sony" if sony else "DJI"
    return (
        f"the explicit audio plan is not implemented on the {kind} "
        "preservation path; run this file without --audio-plan or use the "
        "classic software path"
    )


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
    if src_info.chroma == "mono":
        raise ValueError(
            "mono sources are not supported; use a 4:2:0/4:2:2/4:4:4 source"
        )
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
    c = src_info.color
    if c is not None and c.is_set:
        file_logger.info(
            "SOURCE_COLOR | primaries=%s transfer=%s matrix=%s range=%s "
            "master_display=%s max_cll=%s",
            c.primaries or "-", c.transfer or "-", c.matrix or "-",
            c.range or "-", c.master_display or "-", c.max_cll or "-",
        )


# ---------------------------------------------------------------------------
# failure records (JSON) + retry list
# ---------------------------------------------------------------------------

def _brief_error(log_path: str | None, detail_file: Path) -> str:
    """Write the last 40 log lines to detail_file; return the last 8
    lines as the brief summary (both independent files per failure)."""
    tail: list[str] = []
    if log_path:
        try:
            lines = Path(log_path).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            tail = lines[-40:]
        except OSError:
            pass
    if tail:
        try:
            detail_file.parent.mkdir(parents=True, exist_ok=True)
            detail_file.write_text(
                "\n".join(tail) + "\n", encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
    return "\n".join(tail[-8:]) if tail else "(no log available)"


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
    """Append one failure record to the JSON failure list; the brief
    error summary goes into its own file under failed_details/."""
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail_file = (
        failed_path.parent / "failed_details"
        / f"{job_id_for(source)}_{stamp}.txt"
    )
    brief = _brief_error(log_path, detail_file)
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
            "error_summary": brief,
            "error_detail_file": str(detail_file),
            "log_path": log_path,
        }
    )
    failed_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_retry_list(path: Path) -> list[Path]:
    """Load a failed_files.json (or plain path list) into source paths.

    Plain-text lists (one path per line) are supported as documented by
    --retry-list; malformed JSON falls back to line parsing instead of
    crashing the run."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    sources = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("source"):
                sources.append(Path(item["source"]))
            elif isinstance(item, str):
                sources.append(Path(item))
    if not sources:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "//")):
                sources.append(Path(line))
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
    hw_decode: str = HW_DECODE_OFF,
    hw_decode_memo: dict | None = None,
    source_kind: str = SOURCE_UNKNOWN,
    check_level: str,
    ffmpeg: Path | None = None,
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    status_cb: StatusCb | None = None,
    show_progress: bool = True,
    throughput_cb: Callable[[float], None] | None = None,
    audio_task: "AudioSyncTask | None" = None,
    resize: ResizeSpec | None = None,
    progress_cb: Callable[[int, float], None] | None = None,
    on_encode_complete: Callable[[], None] | None = None,
    integrity_gate: threading.Semaphore | None = None,
    encode_gate: threading.Semaphore | None = None,
    skip_integrity: bool = False,
) -> str:
    """Sony preservation path with a rigaya hardware encoder."""
    work_dir = work_root / job_id_for(src)
    # 中间产物后缀 = 实际容器 (HEVC -> .mov, AV1 -> .mp4, 见
    # intermediate_container): 保留管线按路径读它, 名实不符会误导排查。
    encoded_mov = work_dir / "video" / (
        "encoded" + _CONTAINER_SUFFIX[intermediate_container(backend.codec)]
    )

    planned, needs_downgrade = plan_initial_format(
        backend.caps, backend.kind, src_info.chroma, src_info.bit_depth, backend.codec
    )

    def build_cmd(chroma: str, depth: int) -> list[str]:
        cmd, _, _ = backend.command(
            src, encoded_mov, profile, chroma, depth, vfr,
            audio_copy=False, color=src_info.color,
            container=intermediate_container(backend.codec),
            resize=resize,
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
        if audio_task is not None:
            audio_task.note_video_started()
        if status_cb:
            status_cb("encoding", f"{planned[0]}/{planned[1]}")
        enc_warnings, used, enc_fps = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=source, output=out_mov,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=False, do_frame_check=True,
            no_downgrade=no_downgrade, gpac=gpac,
            hw_decode=hw_decode, hw_decode_memo=hw_decode_memo,
            source_kind=source_kind,
            resize=resize,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
            progress_cb=progress_cb,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=encode_gate,
            skip_integrity=skip_integrity,
        )
        warnings.extend(enc_warnings)
        file_logger.info("ENCODE_FORMAT | %s/%s", used[0], used[1])
        if throughput_cb is not None:
            throughput_cb(enc_fps)
        if audio_task is not None:
            audio_task.note_video_finished()
        if audio_task is not None:
            audio_task.note_video_finished()

    def pipe_log(msg: str) -> None:
        logger.info("[SONY] %s", msg)
        file_logger.info("PIPELINE | %s", msg)

    try:
        if backend.codec == "av1":
            logger.info(
                "[POLICY] %s | AV1 保留管线: rtmd/nrtm/uuid 元数据保留, "
                "不打 XAVC tag (AV1 不在 XAVC 规范内)",
                src.name,
            )
            file_logger.info(
                "POLICY | AV1 Sony preserve: rtmd/nrtm/uuid kept, "
                "XAVC brand NOT restored (AV1 not in XAVC spec)"
            )
        # 自动延时补偿 (Sony): 修正后的音频轨在重建时替代源音轨。
        # P2: --async-pipeline starts the audio task before this pipeline;
        # run_sony_pipeline resolves it lazily right before the final mux, so
        # metadata extraction + video encode overlap the audio stage.
        audio_sources = None
        audio_sources_getter = None
        if audio_task is not None:
            def audio_sources_getter() -> list[Path] | None:
                audio_task.get()
                return audio_task.audio_sources
        elif channel_sync and source_summary["audio_streams"] > 0 \
                and ffmpeg is not None:
            from core.channel_sync import run_channel_sync

            try:
                res = run_channel_sync(
                    source=src,
                    ffmpeg=ffmpeg,
                    work_dir=work_dir / "channel_sync",
                    streams=src_info.raw_streams,
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
            fix_hw_timing=True,
            check_level=check_level,
            codec=backend.codec,
            ffmpeg=ffmpeg,
            quality_opts=quality_opts,
            quality_csv=quality_csv,
            audio_sources=audio_sources,
            audio_sources_getter=audio_sources_getter,
            expected_video_size=(
                (resize.width, resize.height) if resize else None),
            log=pipe_log,
        )
    except Exception as exc:
        logger.error("[FAIL] preservation pipeline | %s | %s", src, exc)
        file_logger.exception("PRESERVATION PIPELINE FAILED")
        if audio_task is not None:
            audio_task.get()
        return "failed"

    if audio_task is not None:
        audio_task.get()
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
        logger.error(
            "[FAIL] Gyroflow consumer validation | %s | %s",
            src, gyro.get("detail"),
        )
        file_logger.error("GYROFLOW VALIDATION FAILED | %s", gyro.get("detail"))
        return "failed"

    dst.parent.mkdir(parents=True, exist_ok=True)
    final = work_dir / "final" / "output.mov"
    # 保留管线的工作容器永远是 MOV (GPAC / rtmd / nrtm / uuid 全部按此
    # 验证)。最终容器跟随**源**: Sony MOV -> 直接改名 MOV; 一旦出现
    # Sony MP4 源 -> validate 通过之后再做一次无损容器重封装。
    # 顺序很重要: 校验比对的是 build 产物, 所以重封装放在校验之后,
    # 且中间产物改名到 dst 之前失败不会留下半成品。
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
    hw_decode: str = HW_DECODE_OFF,
    hw_decode_memo: dict | None = None,
    source_kind: str = SOURCE_UNKNOWN,
    check_level: str = "basic",
    ffmpeg: Path | None = None,
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    status_cb: StatusCb | None = None,
    show_progress: bool = True,
    throughput_cb: Callable[[float], None] | None = None,
    video_handoff: VideoHandoffHook | None = None,
    audio_task: "AudioSyncTask | None" = None,
    resize: ResizeSpec | None = None,
    progress_cb: Callable[[int, float], None] | None = None,
    drop_data_tracks: bool = False,
    on_encode_complete: Callable[[], None] | None = None,
    integrity_gate: threading.Semaphore | None = None,
    encode_gate: threading.Semaphore | None = None,
    skip_integrity: bool = False,
) -> str:
    """Non-Sony material: video + audio only, single-tool single pass.
    At check_level='full' the PSNR/SSIM sample gates delivery (FAIL
    deletes the part file and fails the batch entry); with channel_sync
    the fixed audio replaces the copied audio post-encode.

    With ``video_handoff`` the encode produces a **video-only** artifact
    and the caller's hook owns the final file (v0.8.0+: that is how an
    explicit audio plan is applied on hardware backends). Without it
    nothing here changes."""
    # 中间产物的后缀必须与实际容器一致 (见 intermediate_container):
    # HEVC 是 MOV, AV1 只能是 MP4。此前后缀恒为 .mov, 而 AV1 编码器写的是
    # MP4 —— 又一处"名实不符"。
    part_dst = dst.with_name(
        f"{dst.stem}.part{_CONTAINER_SUFFIX[intermediate_container(backend.codec)]}"
    )
    safe_unlink(part_dst)
    # 有接手方 -> 只产出视频; 没有 -> 既有的一趟 video+audio copy 行为。
    audio_copy = video_handoff is None

    planned, needs_downgrade = plan_initial_format(
        backend.caps, backend.kind, src_info.chroma, src_info.bit_depth, backend.codec
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
        cmd, _, _ = backend.command(
            src, part_dst, profile, planned[0], planned[1],
            vfr, audio_copy=audio_copy, color=src_info.color,
            container=intermediate_container(backend.codec),
            resize=resize,
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
    if audio_task is not None:
        audio_task.note_video_started()
    try:
        warnings, used, enc_fps = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=src, output=part_dst,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=audio_copy, do_frame_check=False,
            no_downgrade=no_downgrade, gpac=gpac,
            hw_decode=hw_decode, hw_decode_memo=hw_decode_memo,
            source_kind=source_kind,
            resize=resize,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
            progress_cb=progress_cb,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=encode_gate,
            skip_integrity=skip_integrity,
        )
    except Exception as exc:
        logger.error("[FAIL] hardware encode | %s | %s", src, exc)
        file_logger.exception("HW ENCODE FAILED")
        safe_unlink(part_dst)
        if audio_task is not None:
            audio_task.note_video_finished()
        return "failed"
    if audio_task is not None:
        audio_task.note_video_finished()
    if throughput_cb is not None:
        throughput_cb(enc_fps)

    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
        logger.error("[FAIL] encoder produced no output | %s", src)
        file_logger.error("FAILED | encoder produced no output")
        safe_unlink(part_dst)
        return "failed"

    # PSNR/SSIM quality sample (check=full) gates delivery: a corrupted
    # encode must never land in the output tree.
    if check_level == "full" and ffmpeg is not None:
        from preservation.quality import run_quality_sample

        q = run_quality_sample(
            original=src,
            final=part_dst,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            scratch=work_dir / "quality",
            opts=quality_opts,
            csv_path=quality_csv,
            log=lambda msg: logger.info("[QUALITY] %s | %s", src.name, msg),
        )
        if q["status"] == "FAIL":
            logger.error(
                "[FAIL] quality sample | %s | %s", src, q["detail"]
            )
            file_logger.error(
                "QUALITY SAMPLE FAILED | %s | report=%s",
                q["detail"], work_dir / "quality",
            )
            safe_unlink(part_dst)
            return "failed"
    elif check_level == "full":
        logger.warning(
            "[QUALITY] full check requested but ffmpeg is unavailable; "
            "PSNR/SSIM sample skipped for %s", src.name,
        )

    # ---- 可选的"外部接手最终输出" (v0.8.0+: 显式音频计划) --------------
    # 视频侧到这里已经交付了一个**视频产物**; 接下来怎么做是接手方的事。
    # 注册了 handoff 时 channel_sync 不参与 (CLI 层面两者互斥), 因此这里
    # 明确按"接手方拥有最终文件"的语义走, 不留下第二条改音频的路径。
    if video_handoff is not None:
        return _finish_with_handoff(
            handoff=video_handoff, gpac=gpac, src=src, dst=dst,
            part_dst=part_dst,
            work_dir=work_dir, preset=preset, started=started,
            source_summary=source_summary, used=used, warnings=warnings,
            ffprobe=ffprobe, postprobe_csv=postprobe_csv,
            postprobe_stream_csv=postprobe_stream_csv,
            logger=logger, file_logger=file_logger,
        )

    # 自动延时补偿 (经典路径): 修正后的音频轨替换拷贝音频
    # P2: with --async-pipeline the task was already started before the video
    # encode; get() joins it now (its result is independent of the video).
    sync_report = None
    if audio_task is not None:
        sync_report = audio_task.get()
    elif channel_sync and ffmpeg is not None:
        from core.channel_sync import run_channel_sync

        try:
            sync_report = run_channel_sync(
                source=src,
                ffmpeg=ffmpeg,
                work_dir=work_dir / "channel_sync",
                streams=src_info.raw_streams,
                opts=channel_sync_opts,
                log=lambda msg: logger.info("[SYNC] %s | %s", src.name, msg),
            )
            file_logger.info(
                "CHANNEL_SYNC | %s | %s",
                sync_report["status"],
                json.dumps(sync_report.get("channels", []),
                           ensure_ascii=False),
            )
        except Exception as exc:
            logger.warning("[SYNC-FAIL] %s | %s — 原音频照旧", src, exc)
            file_logger.exception("CHANNEL SYNC FAILED")

    if sync_report is not None:
        res = sync_report
        if res["status"] == "applied":
            from preservation.audio_sync import remux_replace_audio

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


    # 交付: 中间产物是 **MOV**, 最终容器跟随源。相同 -> 直接改名;
    # 不同 (源为 MP4) -> 无损容器重封装, 绝不重新编码。
    try:
        place_final(
            gpac=gpac, intermediate=part_dst, dst=dst,
            work_dir=work_dir,
            meta_source=_meta_manifest(work_dir),
            drop_data_tracks=drop_data_tracks,
            log=lambda msg: file_logger.info("CONTAINER | %s", msg),
        )
    except (OSError, RuntimeError) as exc:
        logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
        file_logger.exception("FINAL PLACEMENT FAILED")
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


def _finish_with_handoff(
    *,
    handoff: VideoHandoffHook,
    gpac: GpacContainerBackend,
    src: Path,
    dst: Path,
    part_dst: Path,
    work_dir: Path,
    preset: str,
    started: float,
    source_summary: dict[str, Any],
    used: tuple[str, int],
    warnings: list[str],
    ffprobe: Path,
    postprobe_csv: Path,
    postprobe_stream_csv: Path,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> str:
    """视频产物已就绪 -> 由 handoff 写出最终文件 (v0.8.0+)。

    语义 (与软件路径的音频步骤逐条一致):

    * `applied=True`  -> 接手方已经写出 `dst`; 中间产物删掉, 照常 postprobe;
    * `applied=False` -> 接手方判定"没有要做的事"(例如空计划), **原样**把
      中间产物改名成 `dst` —— 也就是退化为既有行为, 而不是另拼一条命令;
    * `ok=False`      -> **明确失败**。音频计划被拒绝时绝不"忽略计划继续输出"
      (那会让用户以为计划生效了)。
    """
    outcome = handoff(VideoHandoff(
        source=src, video=part_dst, output=dst, work_dir=work_dir,
        file_logger=file_logger,
    ))
    file_logger.info(
        "VIDEO_HANDOFF | ok=%s applied=%s | %s",
        outcome.ok, outcome.applied, outcome.detail or "-",
    )
    for problem in outcome.errors:
        file_logger.error("VIDEO_HANDOFF ERROR | %s", problem)

    if not outcome.ok:
        logger.error(
            "[FAIL] post-encode step | %s | %s",
            src, outcome.detail or "see per-file log",
        )
        safe_unlink(part_dst)
        return "failed"

    if outcome.applied:
        if not dst.is_file() or dst.stat().st_size <= 0:
            logger.error("[FAIL] post-encode step produced no output | %s", src)
            safe_unlink(part_dst)
            return "failed"
        safe_unlink(part_dst)
    else:
        # 接手方判定"无事可做" -> 退化为既有行为, 但中间产物是 MOV 而
        # dst 的容器跟随源, 所以这里同样走"改名或无损重封装"。
        try:
            place_final(
                gpac=gpac, intermediate=part_dst, dst=dst,
                work_dir=work_dir,
                meta_source=_meta_manifest(work_dir),
                drop_data_tracks=drop_data_tracks,
                log=lambda msg: file_logger.info("CONTAINER | %s", msg),
            )
        except (OSError, RuntimeError) as exc:
            logger.error("[RENAME-FAIL] %s -> %s | %s", part_dst, dst, exc)
            file_logger.exception("FINAL PLACEMENT FAILED")
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


def encode_one_dji_hw(
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
    hw_decode: str = HW_DECODE_OFF,
    hw_decode_memo: dict | None = None,
    source_kind: str = SOURCE_UNKNOWN,
    check_level: str,
    ffmpeg: Path | None = None,
    quality_opts: dict[str, Any] | None = None,
    quality_csv: Path | None = None,
    channel_sync: bool = False,
    channel_sync_opts: dict[str, Any] | None = None,
    logger: logging.Logger,
    file_logger: logging.Logger,
    dry_run: bool,
    status_cb: StatusCb | None = None,
    show_progress: bool = True,
    throughput_cb: Callable[[float], None] | None = None,
    audio_task: "AudioSyncTask | None" = None,
    resize: ResizeSpec | None = None,
    progress_cb: Callable[[int, float], None] | None = None,
    on_encode_complete: Callable[[], None] | None = None,
    integrity_gate: threading.Semaphore | None = None,
    encode_gate: threading.Semaphore | None = None,
    skip_integrity: bool = False,
) -> str:
    """DJI preservation path (Osmo Action / drones).

    Video re-encoded; audio + djmd/dbgi/tmcd container-copied verbatim
    from the source (payload sha256-verified); Gyroflow consumer check
    compares per-frame quaternions. Track enumeration goes through
    MP4Box -info — -diso XML parsing fails on DJI files (hidden mjpeg
    cover track). The mjpeg cover and udta are not addressable by
    GPAC 26.02: dropped by policy and logged, never silently.
    """
    work_dir = work_root / job_id_for(src)
    # 中间产物后缀 = 实际容器 (HEVC -> .mov, AV1 -> .mp4), 见
    # intermediate_container。
    encoded_mov = work_dir / "video" / (
        "encoded" + _CONTAINER_SUFFIX[intermediate_container(backend.codec)]
    )
    final = work_dir / "final" / "output.mov"
    report_path = work_dir / "report.json"

    planned, needs_downgrade = plan_initial_format(
        backend.caps, backend.kind, src_info.chroma, src_info.bit_depth, backend.codec
    )
    log_hw_header(
        logger=logger, file_logger=file_logger, src=src, preset=preset,
        backend=backend, source_summary=source_summary, src_info=src_info,
        vfr=vfr, planned=planned, needs_downgrade=needs_downgrade,
        profile=profile,
    )
    file_logger.info(
        "POLICY | DJI source (djmd): video re-encoded; audio + "
        "djmd/dbgi/tmcd preserved natively; mjpeg cover + udta dropped "
        "(not addressable by GPAC 26.02)"
    )
    logger.info(
        "[POLICY] %s | DJI: video re-encoded, djmd/dbgi/tmcd preserved, "
        "cover/udta dropped",
        src.name,
    )

    def build_cmd(chroma: str, depth: int) -> list[str]:
        cmd, _, _ = backend.command(
            src, encoded_mov, profile, chroma, depth, vfr,
            audio_copy=False, color=src_info.color,
            container=intermediate_container(backend.codec),
            resize=resize,
        )
        return cmd

    if dry_run:
        cmd = build_cmd(*planned)
        file_logger.info(
            "DRY-RUN | planned encode: %s | %s",
            f"{planned[0]}/{planned[1]}",
            subprocess.list2cmdline(cmd),
        )
        return "dry-run"

    started = time.monotonic()
    warnings: list[str] = []
    if status_cb:
        status_cb("preserve", "dji djmd pipeline")

    def encode_video(source: Path, out_mov: Path) -> None:
        if audio_task is not None:
            audio_task.note_video_started()
        if status_cb:
            status_cb("encoding", f"{planned[0]}/{planned[1]}")
        enc_warnings, used, enc_fps = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=source, output=out_mov,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=False, do_frame_check=not vfr,
            no_downgrade=no_downgrade, gpac=gpac,
            hw_decode=hw_decode, hw_decode_memo=hw_decode_memo,
            source_kind=source_kind,
            resize=resize,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
            progress_cb=progress_cb,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=encode_gate,
            skip_integrity=skip_integrity,
        )
        warnings.extend(enc_warnings)
        file_logger.info("ENCODE_FORMAT | %s/%s", used[0], used[1])
        if throughput_cb is not None:
            throughput_cb(enc_fps)

    def pipe_log(msg: str) -> None:
        logger.info("[DJI] %s", msg)
        file_logger.info("DJI_PIPELINE | %s", msg)

    try:
        report: dict[str, Any] | None = None
        if (
            final.is_file()
            and final.stat().st_size > 0
            and report_path.is_file()
        ):
            try:
                cached = json.loads(
                    report_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                cached = {}
            if cached.get("structural_success") is True:
                pipe_log(f"resume: final output already rebuilt: {final}")
                report = cached
            else:
                pipe_log("previous run did not pass validation; rebuilding")
                safe_unlink(final)

        if report is None:
            # 1. video-only encode (reuse validated intermediate)
            want = source_summary.get("total_frames") or None
            if not _encoded_ok(ffprobe, encoded_mov, expected=want):
                try:
                    encoded_mov.unlink()
                except OSError:
                    pass
                encode_video(src, encoded_mov)
            if not _encoded_ok(ffprobe, encoded_mov, expected=want):
                raise RuntimeError(
                    f"video intermediate unreadable or incomplete: {encoded_mov}"
                )

            # 2-4. shared DJI rebuild: manifest -> GPAC mux ->
            # stts duration repair (hardware intermediate) -> dji check
            # P2: with --async-pipeline the audio task was started before the
            # video encode and is joined here, right before the container mux.
            audio_sources = None
            if audio_task is not None:
                stage = audio_task.get()
                if stage is not None and stage.get("status") == "applied":
                    audio_sources = audio_task.audio_sources
            elif channel_sync and source_summary["audio_streams"] > 0 \
                    and ffmpeg is not None:
                from core.channel_sync import run_channel_sync

                try:
                    res = run_channel_sync(
                        source=src,
                        ffmpeg=ffmpeg,
                        work_dir=work_dir / "channel_sync",
                        streams=src_info.raw_streams,
                        opts=channel_sync_opts,
                        log=lambda msg: logger.info(
                            "[SYNC] %s | %s", src.name, msg
                        ),
                    )
                    file_logger.info(
                        "CHANNEL_SYNC | %s | %s",
                        res["status"],
                        json.dumps(
                            res.get("channels", []), ensure_ascii=False
                        ),
                    )
                    if res["status"] == "applied":
                        audio_sources = [
                            Path(p) for p in (res.get("audio_files")
                                              or res["fixed_files"])
                        ]
                    elif res["status"] in ("measure_failed", "verify_failed"):
                        logger.warning(
                            "[SYNC-SKIP] %s | %s — 原音频照旧",
                            src.name, res["detail"],
                        )
                except Exception as exc:
                    logger.warning(
                        "[SYNC-FAIL] %s | %s — 原音频照旧", src, exc
                    )
                    file_logger.exception("CHANNEL SYNC FAILED")

            report = dji.dji_rebuild(
                original=src,
                encoded_mov=encoded_mov,
                work_dir=work_dir,
                gpac=gpac,
                ffprobe=ffprobe,
                gyroflow=gyroflow,
                vfr=vfr,
                level=check_level,
                fix_hw_timing=True,
                video_entry=(
                    "av01" if backend.codec == "av1" else "hvc1"
                ),
                ffmpeg=ffmpeg,
                quality_opts=quality_opts,
                quality_csv=quality_csv,
                audio_sources=audio_sources,
                expected_video_size=(
                    (resize.width, resize.height) if resize else None),
                log=pipe_log,
            )
    except Exception as exc:
        logger.error("[FAIL] dji pipeline | %s | %s", src, exc)
        file_logger.exception("DJI PIPELINE FAILED")
        if audio_task is not None:
            audio_task.get()
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
    try:
        place_final(
            gpac=gpac, intermediate=final, dst=dst, work_dir=work_dir,
            meta_source=_meta_manifest(work_dir),
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
        "gyroflow=%s | check=%s | warnings=%d",
        src.name, s["PRESERVED"], s["MODIFIED"], s["MISSING"],
        gyro.get("status") if gyro else "not-run",
        check_level,
        len(warnings),
    )
    file_logger.info(
        "DJI_PRESERVATION_REPORT | %s",
        json.dumps(
            {
                "summary": s,
                "structural_success": report["structural_success"],
                "gyroflow": gyro,
                "warnings": warnings,
                "report": str(report_path),
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


def is_sony_source(streams: list[dict[str, Any]]) -> bool:
    """Sony XAVC detection: a data stream carrying the rtmd codec tag."""
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "rtmd"
        for st in streams
    )


def is_dji_source(streams: list[dict[str, Any]]) -> bool:
    """DJI detection: a data stream carrying the djmd codec tag
    (Osmo Action series / drones; gyro quaternions live here)."""
    return any(
        st.get("codec_type") == "data"
        and st.get("codec_tag_string") == "djmd"
        for st in streams
    )


_PARTIAL_READ_PATTERNS = (
    "partial file",
    "moov atom not found",
    "invalid data found",
    "truncated",
    "error reading header",
)


def _encoded_ok(
    ffprobe: Path, path: Path, expected: int | None = None
) -> bool:
    """May this intermediate be *reused* as a complete encode?

    This gates resume: a ``True`` here means the file is taken as finished
    and is not re-encoded.  The previous implementation asked only whether
    at least one video packet could be read, which a **truncated** file
    passes — measured: cutting a 596 MB intermediate to 2 % still yielded
    ``nb_read_packets = 19``, rc = 0, and ``_encoded_ok == True``.  An
    interrupted run could therefore leave a partial ``encoded.mov`` that a
    later resume would treat as complete and deliver.

    Three conditions now, in order of strength:

    1. the file exists and is non-empty;
    2. the demuxer reports no partial/truncated read error;
    3. when ``expected`` is known, the packet count equals it exactly.

    ``expected`` is the source's own frame count, so this is the same
    reference the hardware-decode integrity gate uses: reconcile against
    the delivered container, never against a self-report.
    """
    path = Path(path)
    if not (path.is_file() and path.stat().st_size > 0):
        return False
    try:
        proc = subprocess.run(
            [str(ffprobe), "-v", "error", "-count_packets",
             "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets", "-of", "json", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", check=False,
        )
        if proc.returncode != 0:
            return False
        combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
        if any(p in combined for p in _PARTIAL_READ_PATTERNS):
            return False
        streams = json.loads(proc.stdout).get("streams", [])
        if not streams:
            return False
        packets = int(streams[0].get("nb_read_packets") or 0)
        if packets <= 0:
            return False
        if expected is not None and packets != expected:
            return False
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


# ---------------------------------------------------------------------------
# final placement: rename, or lossless container re-mux
# ---------------------------------------------------------------------------

#: ISO-BMFF source extension -> output extension, i.e. the container
#: *conversions* this project performs.  A conversion happens only when
#: the encode intermediate's container differs from the one the final
#: suffix asks for; re-muxing ISOBMFF -> ISOBMFF is a pure box rewrite.
#: `None` marks "not an ISOBMFF source format" — MP4Box cannot read it,
#: so it is never re-muxed and never silently re-labelled.
_REMUX_SUFFIX = {
    ".mov": ".MOV",
    ".mp4": ".MP4",
    ".m4v": ".M4V",
}


def _iso_media_kind(path: Path) -> str:
    """``"isobmf"`` or ``"other"`` — can MP4Box read this file at all?

    Deliberately *not* an attempt to tell MOV from MP4.  Neither
    available tool can: NVEncC writes ftyp major brand ``mp42`` for
    **both** ``-f mov`` and ``-f mp4`` (they differ only in the compat
    brand list, ``mp42`` vs ``mp42/iso2/mp41``), and ffprobe maps both
    to the single demuxer ``mov,mp4,m4a,3gp,3g2,mj2``.  Inventing a
    distinguishing rule there would be a guess dressed up as a check.

    What is knowable, and what actually matters for choosing the
    conversion path, is whether the bytes are ISOBMFF (MP4Box reads and
    rewrites the box tree) or something else such as Matroska (MP4Box
    fails, so the caller must not pretend a re-mux is available).
    """
    try:
        for box in isobmf.root_boxes(path):
            if box.type == "ftyp":
                return "isobmf"
    except (OSError, ValueError):
        return "other"
    return "other"


def remux_container_lossless(
    *,
    gpac: GpacContainerBackend,
    src: Path,
    dst: Path,
    log: Callable[[str], None],
    drop_data_tracks: bool = False,
) -> str:
    """``src`` -> ``dst`` as a **container-only** re-mux (stream copy).

    Every track is copied by its numeric id through GPAC/MP4Box, so the
    elementary streams are byte-identical (verified: video ES SHA256 is
    unchanged across the re-mux) and only the box structure changes.  No
    decoder or encoder is involved.  The source movie timescale is
    threaded through because GPAC otherwise resets it to the first
    track's media timescale.

    Tracks MP4Box cannot import are excluded, and the exclusion is logged
    rather than silent.  Measured case: a Sony source transcoded on the
    ordinary path carries its ``rtmd`` data track into the intermediate
    (FFmpeg ``-map 0`` copies it), and that track has neither handler nor
    sample entry, so ``-add file#N`` fails the whole re-mux with
    ``Failure while importing media: Bad Parameter`` and the delivery is
    lost entirely.  Copying the remaining tracks yields a valid MP4 whose
    video elementary stream is byte-identical to the intermediate's.

    Returns a human-readable description for the file log.
    """
    movie_ts, tracks = dji.track_manifest(gpac, src)
    if not tracks:
        raise RuntimeError(f"no tracks in {src} to re-mux")

    usable = [t for t in tracks if _is_remuxable_track(t)]
    dropped: list[dict] = []
    if drop_data_tracks:
        # --no-metadata policy: an ordinary transcode keeps video/audio/
        # subtitles only.  NVEncC can carry the source timecode track
        # (``tmcd``) into its MOV intermediate even without --data-copy, so
        # the container re-mux is the last place where a DJI/Sony metadata
        # track can still leak into a "no metadata" delivery.
        dropped = [t for t in usable if _is_metadata_track(t)]
        usable = [t for t in usable if not _is_metadata_track(t)]
        for t in dropped:
            log(
                f"container re-mux: dropping metadata track #{t.get('id')} "
                f"(handler={t.get('handler')!r} entry={t.get('entry')!r}) — "
                f"--no-metadata policy"
            )
    skipped = [t for t in tracks if t not in usable and t not in dropped]
    for t in skipped:
        log(
            f"container re-mux: skipping track #{t.get('id')} "
            f"(handler={t.get('handler')!r} entry={t.get('entry')!r}) — "
            f"MP4Box cannot import it"
        )
    if not usable:
        raise RuntimeError(
            f"no importable tracks in {src} to re-mux "
            f"({len(tracks)} present, all unusable)"
        )

    previous = gpac.movie_timescale
    gpac.movie_timescale = movie_ts or previous
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        gpac.mux_new(dst, [f"{src}#{t['id']}" for t in usable])
    finally:
        gpac.movie_timescale = previous
    # tkhd 轨道启用标志 / alternate group 归一 (同 isobmf.normalize_track_flags)。
    # 多音轨重封装后的成品若不归一, NLE 会按"备选组"语义只显示一条音轨。
    flags_desc: list[str] = []
    try:
        flags_desc = isobmf.normalize_track_flags(dst)
    except (OSError, RuntimeError) as exc:
        log(f"container re-mux: WARNING tkhd normalization skipped: {exc}")
    flags_note = (
        f"; tkhd: {len(flags_desc)} track flag(s) normalized"
        if flags_desc else ""
    )
    return (
        f"container-only re-mux: {src.name} ({len(usable)} track(s), "
        f"{len(skipped)} skipped, movie timescale {movie_ts}) -> "
        f"{dst.name} [stream copy, no re-encode]{flags_note}"
    )


def _is_remuxable_track(track: dict) -> bool:
    """Can MP4Box ``-add`` this track by id?

    A track with no sample entry has no media to import; the measured failure
    mode is the Sony ``rtmd`` data track, which the manifest reports with both
    ``handler`` and ``entry`` empty.  This requires an entry rather than
    allow-listing known handlers, so an unfamiliar but well-formed track is
    still copied.
    """
    return bool(str(track.get("entry") or "").strip())


def _is_metadata_track(track: dict) -> bool:
    """True for data/metadata tracks that ``--no-metadata`` must not carry.

    ``handler`` is ``"meta"`` for the mp4 data-track family (DJI
    djmd/dbgi/tmcd, Sony rtmd); the explicit entry list is a belt-and-braces
    check for muxers that report a different handler for timecode.
    """
    handler = str(track.get("handler") or "").lower()
    entry = str(track.get("entry") or "").lower()
    return handler in ("meta", "hint", "mdir") or entry in (
        "tmcd", "rtmd", "djmd", "dbgi", "nrtm",
    )


def place_final(
    *,
    gpac: GpacContainerBackend,
    intermediate: Path,
    dst: Path,
    work_dir: Path,
    log: Callable[[str], None],
    meta_source: Path | None = None,
    drop_data_tracks: bool = False,
) -> None:
    """Put the finished intermediate at ``dst`` with a matching container.

    The hardware video intermediate is **always MOV** and the final
    container follows the *source* (``core.paths.final_suffix_for``).
    Two branches, decided on the file *suffix* — because that is the
    only reliable statement available about which of the two ISOBMFF
    flavours is wanted (see ``_iso_media_kind``):

    * suffix already matches -> plain rename.  No re-mux: the MOV source
      case must not pay for, or risk, a second box rewrite.  The one
      exception is ``drop_data_tracks`` (``--no-metadata``): a re-mux is
      then required to strip data/timecode tracks the encoder intermediate
      carried over from the source.
    * suffix differs -> lossless container re-mux, built under
      ``work_dir/final/`` and renamed into place only once complete, so
      a half-written delivery is never visible at ``dst``.

    ``meta_source`` is the pre-remux artefact whose **file-level**
    ``meta`` box (Sony ``nrtm``) must survive the conversion.  A
    per-track ``-new``+``-add`` re-mux rebuilds the movie from its
    tracks and drops every file-level box; the ``uuid`` boxes are
    restored by ``isobmf.insert_uuid_boxes``, but nothing restored
    ``meta``, so the conversion silently produced a file whose Sony
    rtmd track no longer satisfied Gyroflow (measured: 14014 IMU
    samples in, **0** out, with a bit-identical rtmd payload).  That is
    precisely the "lossless" claim this function makes, so it is now
    *verified* rather than assumed.  Pass ``None`` for sources with no
    file-level meta to carry (e.g. DJI).

    A difference that *cannot* be re-muxed (non-ISOBMFF intermediate, no
    GPAC backend) **raises** instead of renaming, because an ``.MP4``
    that is not an MP4 is exactly the defect this function prevents.

    Raises RuntimeError (callers turn it into a failed file); never
    leaves a partial file at ``dst``.
    """
    want_suffix = _REMUX_SUFFIX.get(dst.suffix.lower())
    src_suffix = intermediate.suffix.upper()
    if want_suffix is not None and want_suffix == src_suffix \
            and not drop_data_tracks:
        safe_unlink(dst)
        os.replace(intermediate, dst)
        log(
            f"final: renamed {intermediate.name} -> {dst.name} "
            f"(container already {dst.suffix}, no re-mux)"
        )
        return

    if want_suffix is None:
        # Final container is one this project does not re-mux (MKV, TS,
        # MXF…).  The historical behaviour was a bare rename; keep it,
        # and say so rather than implying a conversion happened.
        if drop_data_tracks:
            log(
                f"final: WARNING --no-metadata cannot be enforced for "
                f"{dst.suffix} (container is not re-muxed here); metadata "
                f"tracks carried by the encoder intermediate stay"
            )
        safe_unlink(dst)
        os.replace(intermediate, dst)
        log(
            f"final: renamed {intermediate.name} -> {dst.name} "
            f"({dst.suffix} is not a re-muxed container; bytes unchanged)"
        )
        return

    if gpac is None:
        raise RuntimeError(
            f"container change to {dst.suffix} is needed for {dst.name} "
            f"but no GPAC backend is available"
        )
    kind = _iso_media_kind(intermediate)
    if kind != "isobmf":
        raise RuntimeError(
            f"container change to {dst.suffix} is needed for {dst.name} "
            f"but {intermediate.name} is not ISOBMFF, so it cannot be "
            f"re-muxed without re-encoding"
        )

    # Stage with the *target* suffix, not the source one: MP4Box selects
    # the output file type (and therefore the `ftyp` major brand) from
    # the -new path's extension.  Staging as `container.mov` and then
    # renaming produced a file whose major brand was `qt  ` — i.e. an
    # MP4 in name only, the very defect this function exists to prevent.
    # Measured: `-new X.mp4` -> major `isom`; `-new X.mov` -> major
    # `qt  `; both with the identical -add list.
    staged = work_dir / "final" / f"container-remux{dst.suffix}"
    safe_unlink(staged)
    log(remux_container_lossless(
        gpac=gpac, src=intermediate, dst=staged, log=log,
        drop_data_tracks=drop_data_tracks,
    ))
    if not (staged.is_file() and staged.stat().st_size > 0):
        raise RuntimeError(f"container re-mux produced no output: {staged}")

    # A per-track re-mux rebuilds the movie and drops every file-level
    # box plus the track-reference boxes.  uuid boxes are re-inserted
    # elsewhere; the file-level meta (Sony nrtm) and tref/cdsc are
    # re-applied here, and their survival is *asserted*: losing the nrtm
    # meta silently costs the whole consumer path (Gyroflow reads zero
    # IMU samples from an otherwise perfect file), which is exactly the
    # defect this branch used to ship unnoticed.
    manifest = _meta_manifest(work_dir)
    if manifest is not None:
        try:
            if gpac.restore_file_level_meta(manifest, staged):
                log(
                    "container re-mux: file-level meta (nrtm) re-applied "
                    "from the pre-remux artefact"
                )
            log(gpac.assert_file_level_meta(manifest, staged))
            # The same rebuild also drops tref/cdsc (rtmd -> video), which
            # validate.compare treats as a critical MISSING item.
            log(gpac.restore_track_refs(intermediate, staged))
            log(gpac.assert_track_refs(intermediate, staged))
        except Exception as exc:
            safe_unlink(staged)
            raise RuntimeError(
                f"container re-mux to {dst.suffix} lost file-level "
                f"metadata: {exc}"
            ) from exc

    # 元数据恢复 (file-level meta / tref/cdsc) 由 GPAC 重新执行 -set-meta /
    # -add-item / -set-xml / -ref, 每次都会重建 moov —— 因此 tkhd 的
    # flags/alternate_group 可能在恢复步骤之后被重置。落盘前再归一一次;
    # 幂等、只改头字节, 不触碰 mdat (见 isobmf.normalize_track_flags)。
    try:
        for desc in isobmf.normalize_track_flags(staged):
            log(f"final: {desc}")
    except (OSError, RuntimeError) as exc:
        log(f"final: WARNING tkhd normalization skipped: {exc}")

    safe_unlink(dst)
    os.replace(staged, dst)
    # A6: after a container re-mux the original `.part.mov` intermediate has
    # no consumer.  Leaving it in the output tree polluted user-visible
    # directories (observed for MP4 sources).  Safe to delete only after the
    # staged container is in place.
    safe_unlink(intermediate)
    log(f"final: {staged.name} -> {dst.name}")


def _meta_manifest(work_dir: Path) -> Path | None:
    """``work_dir/metadata/manifest.json`` when a bundle was extracted.

    ``None`` means "this source family has no file-level meta to carry"
    (e.g. DJI), which is a legitimate no-op — as opposed to a bundle
    that exists but cannot be honoured, which ``place_final`` raises on.
    """
    manifest = work_dir / "metadata" / "manifest.json"
    return manifest if manifest.is_file() else None


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
    hw_decode: str = HW_DECODE_OFF
    hw_decode_memo: dict = field(default_factory=dict)
    #: Requested hardware-decode policy. The reader is resolved per file by
    #: ``encoders.hwdecode.route_decode`` from the **source class**, and the
    #: binary that runs ``--avhw`` is the backend's deployed tool, so no tool
    #: path or provenance marker is carried here any more.
    check_level: str = "basic"
    #: NVENC only: x265_scaling.json used for source-relative maxrate/VBV.
    #: QSV stays on its static table; VCE has no entry point.
    hw_scaling_path: Path | None = None
    #: Explicit output resize request (exact resolution or long-edge factor).
    resize: ResizeRequest | None = None
    ffmpeg: Path | None = None
    quality_opts: dict[str, Any] | None = None
    channel_sync: bool = False
    channel_sync_opts: dict[str, Any] | None = None
    #: DJI 默认元数据策略的覆盖开关 (--preserve-metadata)。默认 False 时
    #: Pocket/无人机按"不保留 metadata"的普通转码路径处理; Action 系列不受影响。
    preserve_metadata: bool = False
    #: 强制按"保留元数据"处理的源 (resolve() 后比较) -> 连接时首段的机型标签。
    #: DJI Action 分段连接生成的 staging 文件会丢失 moov 里的机型 `tool` 标签,
    #: 无法再做 Action 系列识别; 连接时已确认原分段机型, 因此由调用方显式列出。
    #: --no-metadata 优先。
    preserve_metadata_sources: dict[Path, str | None] = field(
        default_factory=dict)
    #: 显式关闭元数据保留 (--no-metadata)。这是**元数据策略**, 不是解码开关:
    #: Sony/DJI 走普通转码路径 (video+audio only), 硬件优先解码与完整性校验
    #: 完全不变。与 --preserve-metadata 同时给出时以本开关为准 (显式关闭优先)。
    no_metadata: bool = False
    dry_run: bool = False
    failed_path: Path | None = None
    status: DashboardStatus | None = None
    show_progress: bool = True
    #: v0.8.0+: 每文件编码完成后的可选接手方 (见 `VideoHandoff`)。
    #: 注册后经典硬件路径只产出**视频产物**, 由接手方写出最终文件。
    #: 视频侧不知道接手方是谁, 也不知道它做什么。
    video_handoff: VideoHandoffHook | None = None
    warnings_total: list[str] = field(default_factory=list)
    # (backend_name, encode_fps) samples collected by workers, read by
    # the scheduler.  Guarded so the continuous scheduler can drain it while
    # workers append.
    throughput: list[tuple[str, float]] = field(default_factory=list)
    throughput_lock: Any = field(default_factory=threading.Lock)
    #: P2 (opt-in): one global audio-sync slot; channel_sync itself already
    #: spawns a 12-process estimator pool, so concurrent audio tasks must be
    #: gated to avoid CPU/memory oversubscription.
    audio_sync_gate: Any = field(
        default_factory=lambda: threading.Semaphore(1))
    #: P2: run audio channel-sync concurrently with video encode per file.
    async_pipeline: bool = False
    #: Progressive DJI split-join coordinator (core.dji_join.ProgressiveStager)
    #: or None.  When set, a joined unit's source is staged on demand, the
    #: encoder's progress arms the next unit's lossless join, and the source is
    #: reclaimed once the file is delivered.
    dji_stager: Any = None
    #: Opt-in: release the next DJI group for encoding as soon as this group's
    #: encoder has produced its intermediate, then run the software integrity
    #: decode while the next group encodes.  One verifier slot; the next group
    #: is capped so at most one intermediate can wait for verification.
    async_integrity: bool = False
    #: Serializes software integrity decode across workers when async mode is
    #: on (and on every hardware path where this context is passed).
    integrity_gate: Any = field(
        default_factory=lambda: threading.Semaphore(1))
    #: --jobs slot.  Only an actual encoder invocation holds this; waiting for
    #: integrity verification does not consume an encode slot, so async mode
    #: does not reduce --jobs concurrency.  Set by run_hw_pool().
    encode_gate: Any = None


def _record_throughput(
    ctx: BatchCtx, backend_name: str, fps: float
) -> None:
    """Thread-safe append used by the continuous scheduler."""
    with ctx.throughput_lock:
        ctx.throughput.append((backend_name, fps))


def _wait_for_encode_space(
    ctx: BatchCtx, logger: logging.Logger, src: Path
) -> None:
    """Do not start another encode when pending verified outputs filled disk.

    Async verification allows intermediate artifacts to accumulate while the
    verifier is behind.  Before starting a new encoder invocation, wait until
    the output volume has a small reserve; verification workers will reclaim
    space as they finish.  This is deliberately checked here (inside the
    worker) rather than in the scheduler so it also applies to the fixed
    --jobs pool and to a single worker.
    """
    if not ctx.async_integrity or ctx.dry_run or ctx.no_metadata:
        return
    try:
        reserve_gb = float(os.environ.get(
            "KTS_ASYNC_DISK_RESERVE_GB", "10"))
    except ValueError:
        reserve_gb = 10.0
    reserve = max(0, int(reserve_gb * 1024 ** 3))
    warned = False
    while True:
        try:
            free = shutil.disk_usage(ctx.output_root).free
        except OSError:
            return
        if free >= reserve:
            return
        if not warned:
            logger.warning(
                "[ASYNC-INTEGRITY] disk low | free=%.1f GB < reserve=%.1f "
                "GB | waiting to start %s until pending verification "
                "reclaims space",
                free / 1024 ** 3, reserve / 1024 ** 3, src.name,
            )
            warned = True
        time.sleep(10.0)


#: One process-wide audio-sync slot (P2).  channel_sync itself spawns a
#: 12-process estimator pool; concurrent audio tasks would oversubscribe CPU
#: and memory, so audio stages are serialized even when video slots are many.
_AUDIO_SYNC_GATE = threading.Semaphore(1)

#: A5: if the machine is already CPU-saturated (software decode fallback,
#: concurrent audio, ...), do not overlap the audio stage with video encode;
#: wait for the video to finish and run audio serially.  psutil is optional;
#: without it the historical always-overlap behaviour is kept.
_AUDIO_CPU_BUSY_PCT = 75.0


def _cpu_busy(threshold: float = _AUDIO_CPU_BUSY_PCT) -> bool:
    try:
        import psutil
    except Exception:                                  # noqa: BLE001
        return False
    try:
        return float(psutil.cpu_percent(interval=0.25)) >= threshold
    except Exception:                                  # noqa: BLE001
        return False


class AudioSyncTask:
    """Run channel_sync in a worker thread while the video encoder runs.

    Only the source file is read, so the audio stage has no dependency on the
    video intermediate.  ``AudioSyncTask.get()`` joins before the caller needs
    the corrected audio tracks (final mux).
    """

    def __init__(
        self, *,
        src: Path,
        work_dir: Path,
        streams: Any,
        channel_sync_opts: dict[str, Any] | None,
        ffmpeg: Path,
        logger: logging.Logger,
        file_logger: logging.Logger,
    ) -> None:
        self.src = src
        self.work_dir = work_dir
        self.streams = streams
        self.opts = channel_sync_opts
        self.ffmpeg = ffmpeg
        self.logger = logger
        self.file_logger = file_logger
        self.report: dict[str, Any] | None = None
        self.audio_sources: list[Path] | None = None
        self._video_started = threading.Event()
        self._video_finished = threading.Event()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"audio-sync:{src.name}", daemon=True)

    def start(self) -> "AudioSyncTask":
        self._thread.start()
        return self

    def note_video_started(self) -> None:
        self._video_started.set()

    def note_video_finished(self) -> None:
        self._video_finished.set()

    def abort(self) -> None:
        """Release a task that will never reach its merge point."""
        self._cancelled.set()
        self._video_started.set()
        self._video_finished.set()

    def _run(self) -> None:
        from core.channel_sync import run_channel_sync

        try:
            # Wait until the video stage really starts; then sample CPU.  If
            # the machine is saturated (e.g. hardware decode fell back to
            # software), wait for the video and run the audio serially.
            self._video_started.wait(timeout=30.0)
            if self._cancelled.is_set():
                return
            if _cpu_busy():
                self.logger.info(
                    "[P2] %s: CPU busy -> audio-sync deferred until video "
                    "finishes (serial fallback)", self.src.name)
                self._video_finished.wait(timeout=3600.0)
            if self._cancelled.is_set():
                return
            with _AUDIO_SYNC_GATE:
                res = run_channel_sync(
                    source=self.src,
                    ffmpeg=self.ffmpeg,
                    work_dir=self.work_dir / "channel_sync",
                    streams=self.streams,
                    opts=self.opts,
                    log=lambda msg: self.logger.info(
                        "[SYNC] %s | %s", self.src.name, msg),
                )
            self.report = res
            self.file_logger.info(
                "CHANNEL_SYNC | %s | %s",
                res["status"],
                json.dumps(res.get("channels", []), ensure_ascii=False),
            )
            if res["status"] == "applied":
                self.audio_sources = [
                    Path(p) for p in (res.get("audio_files")
                                      or res["fixed_files"])
                ]
            elif res["status"] in ("measure_failed", "verify_failed"):
                self.logger.warning(
                    "[SYNC-SKIP] %s | %s — 原音频照旧",
                    self.src.name, res["detail"],
                )
        except Exception as exc:                       # noqa: BLE001
            self.logger.warning(
                "[SYNC-FAIL] %s | %s — 原音频照旧", self.src, exc)
            self.file_logger.exception("CHANNEL SYNC FAILED")

    def get(self) -> dict[str, Any] | None:
        self._thread.join()
        return self.report


def _start_audio_sync(
    *,
    src: Path,
    work_dir: Path,
    src_info: Any,
    channel_sync_opts: dict[str, Any] | None,
    ffmpeg: Path | None,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> AudioSyncTask | None:
    if ffmpeg is None or not getattr(src_info, "raw_streams", None):
        return None
    return AudioSyncTask(
        src=src, work_dir=work_dir, streams=src_info.raw_streams,
        channel_sync_opts=channel_sync_opts, ffmpeg=ffmpeg,
        logger=logger, file_logger=file_logger,
    ).start()


def _status_cb_for(
    ctx: BatchCtx, src: Path
) -> Callable[[str, str], None]:
    def cb(status: str, detail: str = "") -> None:
        if ctx.status is not None:
            ctx.status.update(src, status, detail)
    return cb


def _dji_progress_cb(
    ctx: BatchCtx, src: Path, total_frames: int
) -> Callable[[int, float], None] | None:
    """Forward encoder progress to the progressive DJI stager, if any."""
    stager = ctx.dji_stager
    if stager is None:
        return None

    def cb(frames: int, _fps: float) -> None:
        stager.note_progress(src, frames, total_frames)

    return cb


def _dji_note_finished(ctx: BatchCtx, src: Path, result: str) -> None:
    stager = ctx.dji_stager
    if stager is not None:
        stager.note_finished(src, result)


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

    if dst.is_file() and dst.stat().st_size > 0:
        logger.info("[SKIP] %s | %s", src, dst)
        file_logger.info("SKIP | output already exists | output=%s", dst)
        if ctx.status is not None:
            ctx.status.finish(src, "skipped")
        _dji_note_finished(ctx, src, "skipped")
        return "skipped"

    # Progressive DJI split joining: block until this joined unit's source
    # exists (the previous unit's encode gates the next join), so a failed
    # join becomes a per-file failure instead of a whole-batch crash.
    if ctx.dji_stager is not None:
        try:
            ctx.dji_stager.ensure_ready(src)
        except Exception as exc:                   # noqa: BLE001
            logger.error("[DJI-JOIN] FATAL: %s", exc)
            file_logger.error("DJI JOIN FAILED | %s", exc)
            if ctx.failed_path is not None:
                record_failure(
                    ctx.failed_path,
                    source=src, preset=ctx.preset,
                    backend_name=backend.name,
                    stage="dji-join", error=str(exc),
                    log_path=str(file_log),
                )
            if ctx.status is not None:
                ctx.status.finish(src, "failed")
            _dji_note_finished(ctx, src, "failed")
            return "failed"

    # The row stays "queued" while a progressive join is being produced; the
    # real worker start (and therefore dashboard elapsed) begins only now.
    if ctx.status is not None:
        ctx.status.start(src, ctx.preset, backend.name)

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
        _dji_note_finished(ctx, src, "failed")
        return "failed"

    file_resize: ResizeSpec | None = None
    if ctx.resize is not None:
        try:
            file_resize = resolve_resize(
                ctx.resize, src_info.width, src_info.height)
        except ValueError as exc:
            logger.error("[RESIZE-FAIL] %s | %s", src, exc)
            file_logger.error("RESIZE FAILED | %s", exc)
            if ctx.status is not None:
                ctx.status.finish(src, "failed")
            if ctx.failed_path is not None:
                record_failure(
                    ctx.failed_path,
                    source=src, preset=ctx.preset, backend_name=backend.name,
                    stage="resize", error=str(exc), log_path=str(file_log),
                )
            _dji_note_finished(ctx, src, "failed")
            return "failed"
        file_logger.info(
            "RESIZE | source=%dx%d target=%dx%d algo=%s",
            src_info.width, src_info.height,
            file_resize.width, file_resize.height, file_resize.algo,
        )
        logger.info(
            "[RESIZE] %s | %dx%d -> %dx%d | %s",
            src.name, src_info.width, src_info.height,
            file_resize.width, file_resize.height, file_resize.algo,
        )

    quality_csv = (
        ctx.logs_root / "quality_samples.csv"
        if ctx.check_level == "full"
        else None
    )
    # Async integrity may leave pending intermediates on disk; do not start
    # another encoder when the output volume is below its safety reserve.
    _wait_for_encode_space(ctx, logger, src)

    # Encoder progress feeds the progressive DJI stager (None without one).
    progress_cb = _dji_progress_cb(
        ctx, src, source_summary["total_frames"]
    )
    if ctx.dji_stager is not None:
        # Probe succeeded, so this joined group is now really being encoded:
        # let the stager produce the next group in the background (the
        # stager's space guard still applies).
        ctx.dji_stager.note_encode_started(src)

    # Async integrity (opt-in): the classic encoder callback below releases
    # the next DJI group's encode gate immediately after the intermediate is
    # complete.  Verification then runs in this worker while another worker
    # encodes the next group.  The joined source is intentionally retained
    # until verifier/finalisation (note_finished), so verify_encode still has
    # the source container's expected sample count.
    on_encode_complete: Callable[[], None] | None = None
    integrity_gate: threading.Semaphore | None = None
    if ctx.async_integrity and not ctx.dry_run:
        integrity_gate = ctx.integrity_gate
        if ctx.dji_stager is not None:
            def _on_encode_complete(async_src: Path = src) -> None:
                try:
                    ctx.dji_stager.note_encode_complete(async_src)
                except Exception as exc:              # noqa: BLE001
                    file_logger.warning(
                        "ASYNC-INTEGRITY | stager release failed: %s", exc
                    )
                    return
                logger.info(
                    "[ASYNC-INTEGRITY] encode complete | %s | next group "
                    "may encode while this output is verified",
                    async_src.name,
                )

            on_encode_complete = _on_encode_complete

    # Hardware decode on this path is the rigaya readers, decided per file by
    # `encoders.hwdecode.route_decode` inside `hw_encode_with_fallback` from the
    # **source class**: Sony -> software before any attempt, everything else ->
    # native --avhw on the deployed binary. There is nothing to plan up front
    # and no FFmpeg frame feed. The classification is computed once, here, from
    # the probe result that the Sony/DJI dispatch below already uses, so there
    # is one Sony detector and every path (classic, Sony preservation, DJI
    # preservation, any preset, any worker) gets the same answer.
    source_kind = source_kind_from_streams(source_streams)
    file_logger.info(
        "SOURCE_KIND | %s | %s",
        source_kind,
        "Sony rtmd track present -> preventive software decode"
        if source_kind == SOURCE_SONY
        else "no Sony marker -> native hardware decode attempted",
    )

    # NVENC follows the x265 source-relative VBV contract: max_bitrate and
    # vbv_bufsize are derived from OB + efficiency class.  QSV is deliberately
    # left on its static table; VCE has no entry point.
    hw_profile = ctx.profile
    if backend.name.startswith("nvenc") and ctx.hw_scaling_path is not None:
        hw_profile, vbv_audit = apply_nvenc_dynamic_profile(
            ctx.profile, ctx.preset, src_info, ctx.hw_scaling_path)
        if vbv_audit is not None:
            file_logger.info(
                "NVENC_VBV | class=%s | OB=%.1f kbps | "
                "max_bitrate=%d kbps | vbv_bufsize=%d kbps | factor=%.2f",
                vbv_audit["source_class"], vbv_audit["ob_kbps"],
                vbv_audit["final_maxrate_kbps"],
                vbv_audit["final_bufsize_kbps"],
                vbv_audit["bufsize_factor"],
            )
            logger.info(
                "[NVENC-VBV] %s | class=%s | max_bitrate=%d | vbv=%d",
                src.name, vbv_audit["source_class"],
                vbv_audit["final_maxrate_kbps"],
                vbv_audit["final_bufsize_kbps"],
            )

    # 注册了接手方 (显式音频计划) 时, Sony / DJI 保留路径**明确拒绝该文件**:
    # 那两条管线有自己的音频处理方式, 让它们照常跑就等于"计划被静默忽略"。
    # 失败原因直接给出, 不降级、不回退 (与软件路径 rc=2 的拒绝同义)。
    refusal = audio_plan_refusal(source_streams, ctx.video_handoff)
    if refusal is not None:
        kind = "Sony" if is_sony_source(source_streams) else "DJI"
        logger.error("[AUDIO-FAIL] %s | %s", src, refusal)
        file_logger.error("AUDIO PLAN REFUSED | %s | %s", kind, refusal)
        if ctx.status is not None:
            ctx.status.finish(src, "failed")
        if ctx.failed_path is not None:
            record_failure(
                ctx.failed_path,
                source=src, preset=ctx.preset, backend_name=backend.name,
                stage="audio-plan", error=refusal, log_path=str(file_log),
            )
        _dji_note_finished(ctx, src, "failed")
        return "failed"

    # Metadata preservation policy is shared with the software classic path
    # (preservation/policy.py); this block only wires it to the hardware
    # dispatch.  --no-metadata is the metadata policy, not a decode switch:
    # Sony/DJI sources fall through to the ordinary video+audio transcode
    # path, while hardware-first decode and the integrity gate are unaffected.
    join_tool = ctx.preserve_metadata_sources.get(src.resolve())
    metadata_plan = resolve_metadata_plan(
        streams=source_streams,
        gpac=ctx.gpac,
        src=src,
        no_metadata=bool(ctx.no_metadata),
        preserve_metadata=bool(ctx.preserve_metadata),
        forced_join=(
            not ctx.no_metadata
            and src.resolve() in ctx.preserve_metadata_sources
        ),
        join_tool=join_tool,
    )
    log_metadata_plan(metadata_plan, src=src, logger=logger,
                      file_logger=file_logger)

    # P2: audio channel-sync only reads the source; start it before dispatch so
    # it overlaps the video encode.  One process-wide gate serializes audio
    # stages (channel_sync itself spawns a 12-process estimator pool).
    audio_task = None
    if (ctx.async_pipeline and not ctx.dry_run and ctx.channel_sync
            and ctx.ffmpeg is not None
            and source_summary.get("audio_streams", 0) > 0):
        audio_task = _start_audio_sync(
            src=src,
            work_dir=ctx.work_root / job_id_for(src),
            src_info=src_info,
            channel_sync_opts=ctx.channel_sync_opts,
            ffmpeg=ctx.ffmpeg,
            logger=logger,
            file_logger=file_logger,
        )

    if metadata_plan.is_sony and metadata_plan.preserve:
        result = encode_one_sony_hw(
            src=src,
            dst=dst,
            source_summary=source_summary,
            src_info=src_info,
            vfr=vfr,
            profile=hw_profile,
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
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            source_kind=source_kind,
            check_level=ctx.check_level,
            ffmpeg=ctx.ffmpeg,
            quality_opts=ctx.quality_opts,
            quality_csv=quality_csv,
            channel_sync=ctx.channel_sync,
            channel_sync_opts=ctx.channel_sync_opts,
            logger=logger,
            file_logger=file_logger,
            dry_run=ctx.dry_run,
            status_cb=_status_cb_for(ctx, src),
            show_progress=ctx.show_progress,
            throughput_cb=lambda fps: _record_throughput(
                ctx, backend.name, fps),
            audio_task=audio_task,
            resize=file_resize,
            progress_cb=progress_cb,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=ctx.encode_gate,
            skip_integrity=bool(ctx.no_metadata),
        )
    elif metadata_plan.is_dji and metadata_plan.preserve:
        result = encode_one_dji_hw(
            src=src,
            dst=dst,
            source_summary=source_summary,
            src_info=src_info,
            vfr=vfr,
            profile=hw_profile,
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
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            source_kind=source_kind,
            check_level=ctx.check_level,
            ffmpeg=ctx.ffmpeg,
            quality_opts=ctx.quality_opts,
            quality_csv=quality_csv,
            channel_sync=ctx.channel_sync,
            channel_sync_opts=ctx.channel_sync_opts,
            logger=logger,
            file_logger=file_logger,
            dry_run=ctx.dry_run,
            status_cb=_status_cb_for(ctx, src),
            show_progress=ctx.show_progress,
            throughput_cb=lambda fps: _record_throughput(
                ctx, backend.name, fps),
            audio_task=audio_task,
            resize=file_resize,
            progress_cb=progress_cb,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=ctx.encode_gate,
            skip_integrity=bool(ctx.no_metadata),
        )
    else:
        result = encode_one_hw_classic(
            src=src,
            dst=dst,
            source_summary=source_summary,
            src_info=src_info,
            vfr=vfr,
            profile=hw_profile,
            preset=ctx.preset,
            backend=backend,
            ffprobe=ctx.ffprobe,
            postprobe_csv=ctx.postprobe_csv,
            postprobe_stream_csv=ctx.postprobe_stream_csv,
            gpac=ctx.gpac,
            work_root=ctx.work_root,
            no_downgrade=ctx.no_downgrade,
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            source_kind=source_kind,
            check_level=ctx.check_level,
            ffmpeg=ctx.ffmpeg,
            quality_opts=ctx.quality_opts,
            quality_csv=quality_csv,
            channel_sync=ctx.channel_sync,
            channel_sync_opts=ctx.channel_sync_opts,
            logger=logger,
            file_logger=file_logger,
            dry_run=ctx.dry_run,
            status_cb=_status_cb_for(ctx, src),
            show_progress=ctx.show_progress,
            throughput_cb=lambda fps: _record_throughput(
                ctx, backend.name, fps),
            video_handoff=ctx.video_handoff,
            audio_task=audio_task,
            resize=file_resize,
            progress_cb=progress_cb,
            # --no-metadata: the ordinary transcode must not carry DJI/Sony
            # data/timecode tracks that the encoder intermediate inherited
            # from the source; the container re-mux strips them.
            drop_data_tracks=ctx.no_metadata,
            on_encode_complete=on_encode_complete,
            integrity_gate=integrity_gate,
            encode_gate=ctx.encode_gate,
            skip_integrity=bool(ctx.no_metadata),
        )

    # Release an async audio task even when the encoder failed before reaching
    # the merge point.  Success paths have already joined it (get() is
    # idempotent); abort() only matters for early failures.
    if audio_task is not None:
        audio_task.abort()
    if result == "done":
        # Sony/DJI paths already called this inside their encode function;
        # the classic hardware path did not, which left channel-sync scratch
        # directories behind after every successful file. Calling it here is
        # idempotent and covers every hardware branch.
        cleanup_work_dir(
            work_dir=ctx.work_root / job_id_for(src),
            job_id=job_id_for(src),
            preserve_reports=ctx.preserve_reports,
            keep_work=ctx.keep_work,
            logger=logger,
            file_logger=file_logger,
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
    _dji_note_finished(ctx, src, result)
    return result


class EncodeSlots:
    """Dynamic --jobs gate: only actual encoder invocations hold a slot.

    Unlike a plain Semaphore the limit can follow AdaptiveJobs' measured
    worker count for ``--jobs auto`` without replacing the gate object that
    already-running encode calls hold.
    """

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


class AdaptiveJobs:
    """Runtime-adaptive worker count (no hardcoded per-backend table).
    Measures per-wave AGGREGATE encode throughput (sum of per-file
    encoder fps across the wave) and adjusts the worker count between
    waves: grow when the current wave clearly beats the previous one,
    shrink back to the best observed count when aggregate degrades,
    and periodically re-probe upward. The safety cap derives from the
    machine (CPU cores), not from preset tables.
    """

    def __init__(self, logger: logging.Logger | None = None, start: int = 2):
        self.logger = logger
        self.current = max(1, start)
        self.best_w = self.current
        self.best_agg = 0.0
        self.history: list[tuple[int, float]] = []
        self.stable_rounds = 0
        self.cap = max(2, min(8, (os.cpu_count() or 4)))

    def _adapt(self, agg_fps: float, label: str, kind: str) -> int:
        """Shared adaptation core (kind is only for the log line)."""
        if agg_fps > self.best_agg:
            self.best_agg = agg_fps
            self.best_w = self.current
        self.history.append((self.current, agg_fps))

        prev_max = max(
            (a for w, a in self.history if w < self.current), default=0.0
        )
        if (
            self.current < self.cap
            and prev_max > 0
            and agg_fps > prev_max * 1.03
        ):
            # growing clearly helps -> add one worker
            self.current += 1
            self.stable_rounds = 0
        elif agg_fps < self.best_agg * 0.93:
            # aggregate collapsed -> back to the best observed count
            self.current = max(1, self.best_w)
            self.stable_rounds = 0
        else:
            self.stable_rounds += 1
            if self.stable_rounds >= 6 and self.current < self.cap:
                # periodic upward re-probe (cheap insurance against
                # local optima)
                self.current += 1
                self.stable_rounds = 0

        if self.logger is not None:
            self.logger.info(
                "[ADAPTIVE%s] %s aggregate=%.1f fps -> next workers=%d "
                "(best %.1f @ %d, cap %d)",
                f" {label}" if label else "", kind,
                agg_fps, self.current, self.best_agg, self.best_w, self.cap,
            )
        return self.current

    def note_wave(self, agg_fps: float, label: str = "") -> int:
        """Record one wave's aggregate fps (legacy/multihw path)."""
        return self._adapt(agg_fps, label, "wave")

    def note_sample(self, agg_fps: float, label: str = "") -> int:
        """Record a rolling-window sample (continuous scheduler path)."""
        return self._adapt(agg_fps, label, "completion window")


def _process_file_hw_guarded(
    ctx: BatchCtx, backend, src: Path, logger: logging.Logger
) -> str:
    """process_file_hw, but never leave the DJI stager waiting on a crash.

    An unexpected exception would otherwise skip ``note_finished`` and the
    next joined group's ``ensure_ready`` could wait forever (especially with
    the parallel pool, where the scheduler keeps other workers blocked).
    """
    try:
        return process_file_hw(ctx, backend, src, logger)
    except BaseException:
        _dji_note_finished(ctx, src, "failed")
        raise


def _admit_sources(ctx: BatchCtx, backend, sources: list[Path]) -> None:
    """B2: make every queued source visible in the dashboard up front."""
    if ctx.status is None:
        return
    for src in sources:
        ctx.status.admit(src, ctx.preset, backend.name)


def _largest_first(sources: list[Path]) -> list[Path]:
    """B4: long jobs first so refilling short jobs cannot starve them."""
    def key(p: Path) -> tuple[int, str]:
        try:
            return (-p.stat().st_size, str(p))
        except OSError:
            return (0, str(p))
    return sorted(sources, key=key)


def run_hw_pool(
    ctx: BatchCtx,
    backend,
    sources: list[Path],
    logger: logging.Logger,
    workers: int | str,
) -> dict[str, int]:
    """Single-backend scheduling.

    workers: int >= 1 (fixed), or "auto" (adaptive wave scheduling:
    the worker count is adjusted between waves from measured aggregate
    throughput)."""
    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    _admit_sources(ctx, backend, sources)
    admitted = list(sources)

    if ctx.async_integrity:
        logger.info(
            "[ASYNC-INTEGRITY] enabled | verifier queue=1 | encode slots "
            "are controlled by --jobs; verification runs outside the "
            "encoder process"
        )

    if workers != "auto":
        n = int(workers)
        if ctx.async_integrity:
            # --jobs counts encoder invocations only.  The pool may hold up to
            # two extra workers waiting for verification; they never hold an
            # encode slot.
            ctx.encode_gate = threading.Semaphore(max(1, n))
            pool_workers = n + 2
        else:
            pool_workers = n
        if n <= 1 and not ctx.async_integrity:
            ctx.show_progress = True
            for src in sources:
                counters[_process_file_hw_guarded(
                    ctx, backend, src, logger)] += 1
            return counters
        ctx.show_progress = False
        with ThreadPoolExecutor(max_workers=pool_workers) as ex:
            futures = {
                ex.submit(
                    _process_file_hw_guarded, ctx, backend, src, logger
                ): src
                for src in sources
            }
            for fut in as_completed(futures):
                counters[fut.result()] += 1
        return counters

    # ---- continuous adaptive scheduling (P1) ----
    ctx.show_progress = False
    ctrl = AdaptiveJobs(logger, start=2)
    if ctx.async_integrity:
        # Auto starts at two encoder slots; --jobs remains an encoder
        # concurrency limit, not a total worker count.  Extra pool workers
        # only wait for verification and therefore do not consume a slot.
        ctx.encode_gate = EncodeSlots(2)
    pending_extra = 2 if ctx.async_integrity else 0
    # B4: longest first; the continuous refill then packs shorter jobs around
    # the long critical-path file instead of delaying it to the last wave.
    # Progressive DJI joining is the exception: its joined groups must be
    # encoded in plan order (the stager only produces one group ahead), so
    # the admitted order is preserved there.
    if ctx.dji_stager is not None:
        ordered = list(admitted)
        logger.info(
            "[SCHED] auto DJI-join plan order: %s",
            ", ".join(p.name for p in ordered[:5]) +
            (" ..." if len(ordered) > 5 else ""),
        )
    else:
        ordered = _largest_first(admitted)
        logger.info(
            "[SCHED] auto largest-first order: %s",
            ", ".join(p.name for p in ordered[:5]) +
            (" ..." if len(ordered) > 5 else ""),
        )
    pending = deque(ordered)
    fps_window: deque[float] = deque(maxlen=8)
    counters.update({"done": 0, "skipped": 0, "failed": 0,
                     "dry-run": 0})
    with ThreadPoolExecutor(max_workers=ctrl.cap + pending_extra) as ex:
        futures: dict[Any, Path] = {}
        while pending or futures:
            # Refill immediately up to the current target; a completion below
            # frees a slot and this loop draws the next source right away.
            target = max(1, ctrl.current) + pending_extra
            while pending and len(futures) < target:
                _src = pending.popleft()
                futures[ex.submit(
                    _process_file_hw_guarded, ctx, backend, _src,
                    logger)] = _src
            if not futures:
                break
            done, _not_done = wait(
                tuple(futures.keys()), return_when=FIRST_COMPLETED)
            for fut in done:
                futures.pop(fut, None)
                try:
                    counters[fut.result()] += 1
                except Exception:                      # noqa: BLE001
                    logger.exception("[SCHED] worker raised")
                    counters["failed"] += 1
            with ctx.throughput_lock:
                samples = list(ctx.throughput)
                ctx.throughput.clear()
            fps_window.extend(fps for _name, fps in samples)
            if fps_window:
                new_jobs = ctrl.note_sample(sum(fps_window), backend.name)
                if isinstance(ctx.encode_gate, EncodeSlots):
                    ctx.encode_gate.set_limit(new_jobs)

    return counters


def run_multihw_pool(
    ctx: BatchCtx,
    backend_nvenc,
    backend_qsv,
    sources: list[Path],
    logger: logging.Logger,
    workers: int | str = "auto",
) -> dict[str, int]:
    """EXPERIMENTAL dual-backend scheduling (adaptive per backend).

    Static routing v1 (4:2:2 -> NVENC, others alternate) + one adaptive
    worker controller per backend, waves taken from both queues
    concurrently.

    ``workers`` is the same ``--jobs`` value the single-backend pool takes.
    It is honoured here as a **global** budget across both queues rather than
    per backend: previously this function ignored ``--jobs`` entirely and the
    two controllers grew independently, so a wave could reach
    ``cap_nvenc + cap_qsv`` = up to 16 concurrent encodes.
    """
    nvenc_sources: deque[Path] = deque()
    qsv_sources: deque[Path] = deque()
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
    # B2: admit the routed sources using the backend that will run them.
    if ctx.status is not None:
        for _src in nvenc_sources:
            ctx.status.admit(_src, ctx.preset, backend_nvenc.name)
        for _src in qsv_sources:
            ctx.status.admit(_src, ctx.preset, backend_qsv.name)
    # B4: largest-first within each routed queue.
    nvenc_sources = deque(_largest_first(list(nvenc_sources)))
    qsv_sources = deque(_largest_first(list(qsv_sources)))
    ctrl_n = AdaptiveJobs(logger, start=2)
    ctrl_q = AdaptiveJobs(logger, start=1)

    # Global concurrency budget. Without this the two per-backend controllers
    # grow independently (each capped at min(8, cpu_count)), so the wave size
    # could reach 16 concurrent encodes on a 16-core machine -- and `--jobs`
    # was not consulted at all on this path. The brief is explicit that
    # jobs=auto must still bound multihw: it is a resource constraint, not a
    # licence to create as many tasks as there are queues.
    if workers == "auto":
        budget = ctrl_n.cap
        budget_source = f"auto -> cap {budget} (min(8, cpu_count))"
    else:
        budget = max(1, int(workers))
        budget_source = f"explicit --jobs {budget}"
    logger.info(
        "[MULTIHW] global concurrency budget = %d (%s); per-backend "
        "controllers: nvenc=%d qsv=%d",
        budget, budget_source, ctrl_n.current, ctrl_q.current,
    )

    counters = {"done": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    fps_n: deque[float] = deque(maxlen=8)
    fps_q: deque[float] = deque(maxlen=8)

    with ThreadPoolExecutor(max_workers=budget) as ex:
        futures: dict[Any, tuple[Path, Any, str]] = {}
        active_n = 0
        active_q = 0
        while nvenc_sources or qsv_sources or futures:
            # B1: refill each backend up to its own target while respecting
            # the global budget; completion immediately frees a slot.
            while len(futures) < budget:
                made = False
                if nvenc_sources and active_n < ctrl_n.current:
                    _src = nvenc_sources.popleft()
                    _f = ex.submit(_process_file_hw_guarded, ctx,
                                   backend_nvenc, _src, logger)
                    futures[_f] = (_src, backend_nvenc, "nvenc")
                    active_n += 1
                    made = True
                if len(futures) >= budget:
                    break
                if qsv_sources and active_q < ctrl_q.current:
                    _src = qsv_sources.popleft()
                    _f = ex.submit(_process_file_hw_guarded, ctx,
                                   backend_qsv, _src, logger)
                    futures[_f] = (_src, backend_qsv, "qsv")
                    active_q += 1
                    made = True
                if not made:
                    break
            if not futures:
                break
            done, _ = wait(tuple(futures.keys()),
                           return_when=FIRST_COMPLETED)
            for fut in done:
                _src, _be, _name = futures.pop(fut)
                if _name == "nvenc":
                    active_n -= 1
                else:
                    active_q -= 1
                try:
                    counters[fut.result()] += 1
                except Exception:                      # noqa: BLE001
                    logger.exception("[SCHED] multihw worker raised")
                    counters["failed"] += 1
            with ctx.throughput_lock:
                samples = list(ctx.throughput)
                ctx.throughput.clear()
            for name, fps in samples:
                (fps_n if name == "nvenc" else fps_q).append(fps)
            if fps_n:
                ctrl_n.note_sample(sum(fps_n), "nvenc")
            if fps_q:
                ctrl_q.note_sample(sum(fps_q), "qsv")

    return counters


