"""Hardware batch processing: per-file handlers, downgrade ladder,
failure records, and worker schedulers.

Extracted from the main program so 1kt.py stays a thin orchestrator.

Key behaviors (final design):
- hardware encode paths decode with `--avsw` (software) by default;
  hardware decode is opt-in via `--hw-decode auto|require` and is
  gated by capability routing plus the frame-integrity gate
  (encoders/hwdecode.py, encoders/integrity.py);
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
import time
from collections import deque
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
from encoders.hwdecode import (
    POLICY_AUTO as HW_DECODE_AUTO,
    POLICY_OFF as HW_DECODE_OFF,
    POLICY_REQUIRE as HW_DECODE_REQUIRE,
    R_NOT_PROVEN,
    R_REQUIRE_UNMET,
    classify_reader_failure,
    memoize_unavailable,
    resolve_decode_tool,
    route_decode,
)
from encoders.integrity import verify_encode
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

#: Why the last `_resolve_hw_decode_tool` call did not return a binary. Kept
#: module-level (not per-file) because the reason is a property of the
#: installation, and re-hashing the binary for every file in a batch would be
#: pure waste. The cache key includes the tool path, so two backends -- or a
#: test that points at a different tree -- still resolve independently.
_HW_TOOL_CACHE: dict[tuple[str, str, str], tuple[Path | None, str]] = {}
_HW_TOOL_REASON = "not resolved"


def _resolve_hw_decode_tool(
    backend,
    *,
    script_dir: Path | None,
    logger: logging.Logger,
    file_logger: logging.Logger,
) -> Path | None:
    """The binary allowed to run ``--avhw``, or None with a logged reason.

    ``encoders.hwdecode.resolve_decode_tool`` pins hardware decode to the
    sha256-verified patched research build under ``tools/avhw/``: the shipped
    NVEncC/QSVEncC drop leading pictures on Sony XAVC with rc=0 and a
    playable-looking file, so they must never be the ones asked for
    ``--avhw``. When that build is absent or does not match its recorded hash
    there is no hardware-decode binary at all, and this returns None -- the
    caller then decides between a loud software fallback (``auto``) and a
    hard failure (``require``). Guessing is not an option here: a silently
    substituted stock binary is exactly the defect this gate exists for.
    """
    global _HW_TOOL_REASON

    root = Path(script_dir) if script_dir is not None else Path.cwd()
    explicit = getattr(backend, "explicit_tool", None)
    key = (
        backend.kind,
        str(root),
        str(explicit) if explicit else "",
    )
    cached = _HW_TOOL_CACHE.get(key)
    if cached is None:
        dt = resolve_decode_tool(
            kind=backend.kind,
            script_dir=root,
            explicit=explicit,
            policy=HW_DECODE_AUTO,
        )
        path = Path(dt.path) if dt.usable else None
        reason = (
            f"{dt.detail}" if dt.usable
            else f"no usable hardware-decode build ({dt.reason}): {dt.detail}"
        )
        cached = (path, reason)
        _HW_TOOL_CACHE[key] = cached

    tool, reason = cached
    _HW_TOOL_REASON = reason
    if tool is None:
        file_logger.warning("HWDEC_TOOL | %s", reason)
        return None

    logger.info("[HWDEC] %s decode binary: %s", backend.name, tool)
    file_logger.info("HWDEC_TOOL | %s | %s", backend.name, reason)
    return tool


def use_decode_binary(
    cmd: list[str], default_tool: Path, decode_tool: Path
) -> list[str]:
    """Point an already-built argv at the binary allowed to do hardware decode.

    The backends build argv against the *shipped* tool, because that is the
    only build the capability probe was run on and the only one a software
    rung may use. A hardware rung has to use the patched build instead, and
    that substitution happens here rather than inside every backend
    signature: the backends own *encoding* arguments, not which build of the
    tool performs *decoding*.

    Refuses rather than guesses when argv does not start with the expected
    executable: silently running an argv built for another binary would make
    the logged reader identity meaningless.
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
    backend = (
        NvencBackend(tool, caps, codec=codec) if kind == "nvencc"
        else QsvBackend(tool, caps, codec=codec)
    )
    #: The operator's explicit tool path, when one was given. Carried so
    #: `resolve_decode_tool` can honour it for `--avhw` without this
    #: function having to pre-empt the per-file routing decision.
    backend.explicit_tool = explicit
    return backend


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
    script_dir: Path | None = None,
) -> tuple[list[str], tuple[str, int], float]:
    """Automatic three-tier downgrade ladder (NO prompt window).

    Capability-driven downgrades happen silently up front (WARNING);
    runtime failures run the ladder automatically with a WARNING per
    rung; reader failures use the MP4Box strip fallback once.

    Hardware **decode** on this path is the encoder's *own* reader, chosen
    by ``encoders.hwdecode.route_decode``: ``--avhw`` on the
    provenance-verified patched build when the input is in the proven
    allowlist, ``--avsw`` on the shipped build otherwise. There is no
    FFmpeg frame feed here -- the encoder opens the source file itself.
    Every ``--avhw`` result must pass the integrity gate
    (``encoders.integrity.verify_encode``) before it is accepted; a failed
    gate deletes the artifact and reruns the same rung with ``--avsw``.
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

    # --- hardware-decode routing (HD-B01..B10) ---------------------------
    memo = hw_decode_memo if hw_decode_memo is not None else {}
    route = route_decode(
        policy=hw_decode,
        backend=backend.name.split("-")[0],
        codec=backend.codec,
        chroma=chroma,
        depth=depth,
        memo=memo,
        seek_requested=seek_requested,
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
    discarded: list[str] = []

    # The binary that may perform hardware decode. `--avhw` runs it; `--avsw`
    # runs the shipped build, so a fallback never depends on a research build
    # being present. Resolved once per file: it is a property of the machine,
    # not of a format rung.
    hw_tool = _resolve_hw_decode_tool(
        backend, script_dir=script_dir, logger=logger,
        file_logger=file_logger,
    )
    if hw_tool is None and reader == "avhw":
        detail = (
            f"{backend.name} hardware decode is not available: "
            f"{_HW_TOOL_REASON}"
        )
        if hw_decode == HW_DECODE_REQUIRE:
            raise RuntimeError(f"[FATAL] {detail}")
        emit_warning(logger, file_logger, detail, warnings)
        reader = "avsw"
        reader_expected = READER_IDENTITY[backend.kind][reader]
        # The route was chosen before the binary could be checked, so correct
        # the record now. Without this the per-file log keeps a `HARDWARE`
        # DECODE_ROUTE line above the warning that contradicts it, and the
        # audit trail then says the opposite of what will actually run.
        file_logger.info(
            "DECODE_ROUTE_EFFECTIVE | backend=%s codec=%s %s/%sbit -> "
            "SOFTWARE (reader=avsw, reason=%s) %s",
            route.backend, route.codec, route.chroma, route.depth,
            R_NOT_PROVEN, detail,
        )

    cur_input = source
    stripped = False
    rung_idx = 0
    color = src_info.color
    while rung_idx < len(rungs):
        c, d = rungs[rung_idx]
        cmd, skipped, color_notes = backend.command(
            cur_input, output, profile, c, d, vfr, audio_copy, color,
            reader=reader,
            container=intermediate_container(backend.codec),
        )
        if reader == "avhw" and hw_tool is not None:
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
        raw_log = output.with_name(output.name + f".{label}.log")
        try:
            if raw_log.exists():
                raw_log.unlink()
        except OSError:
            pass
        rc, elapsed = run_hw_tool(
            cmd, raw_log, total_frames, label, progress=show_progress,
        )

        # --- integrity gate (HD-C11..C13, HD-F05) ------------------------
        verdict = None
        if reader == "avhw":
            try:
                log_text = raw_log.read_text(
                    encoding="utf-8", errors="replace"
                ) if raw_log.is_file() else ""
            except OSError:
                log_text = ""
            verdict = verify_encode(
                source=source,
                output=output if (rc == 0 and output.is_file()) else None,
                log_text=log_text,
                rc=rc,
                reader_requested=reader,
                reader_expected_identity=reader_expected,
            )
            file_logger.info("INTEGRITY | %s", verdict.log_line())
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
        if ok:
            if discarded:
                file_logger.info(
                    "HWDEC_SUMMARY | fallbacks=%d discarded=%s",
                    hw_fallbacks, " | ".join(discarded),
                )
            return warnings, (c, d), _last_encode_fps(raw_log)

        tail = read_log_tail(raw_log)
        cls = "format" if rc == 0 else classify_failure(tail)

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
    script_dir: Path | None = None,
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
        enc_warnings, used, enc_fps = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=source, output=out_mov,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=False, do_frame_check=True,
            no_downgrade=no_downgrade, gpac=gpac,
            hw_decode=hw_decode, hw_decode_memo=hw_decode_memo,
            script_dir=script_dir,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
        )
        warnings.extend(enc_warnings)
        file_logger.info("ENCODE_FORMAT | %s/%s", used[0], used[1])
        if throughput_cb is not None:
            throughput_cb(enc_fps)

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
        # 自动延时补偿 (Sony): 修正后的音频轨在重建时替代源音轨
        audio_sources = None
        if channel_sync and source_summary["audio_streams"] > 0 \
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
    script_dir: Path | None = None,
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
        warnings, used, enc_fps = hw_encode_with_fallback(
            label="nvencc" if backend.kind == "nvencc" else "qsvencc",
            backend=backend, source=src, output=part_dst,
            profile=profile, src_info=src_info, vfr=vfr,
            work_dir=work_dir,
            total_frames=source_summary["total_frames"],
            ffprobe=ffprobe, audio_copy=audio_copy, do_frame_check=False,
            no_downgrade=no_downgrade, gpac=gpac,
            hw_decode=hw_decode, hw_decode_memo=hw_decode_memo,
            script_dir=script_dir,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
        )
    except Exception as exc:
        logger.error("[FAIL] hardware encode | %s | %s", src, exc)
        file_logger.exception("HW ENCODE FAILED")
        safe_unlink(part_dst)
        return "failed"
    if throughput_cb is not None:
        throughput_cb(enc_fps)

    if not part_dst.is_file() or part_dst.stat().st_size <= 0:
        logger.error("[FAIL] encoder produced no output | %s", src)
        file_logger.error("FAILED | encoder produced no output")
        safe_unlink(part_dst)
        return "failed"

    # PSNR/SSIM quality sample (check=full) gates delivery: a corrupted
    # encode must never land in the output tree.    if check_level == "full" and ffmpeg is not None:
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
    if channel_sync and ffmpeg is not None:
        from core.channel_sync import run_channel_sync
        from preservation.audio_sync import remux_replace_audio

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

    # 交付: 中间产物是 **MOV**, 最终容器跟随源。相同 -> 直接改名;
    # 不同 (源为 MP4) -> 无损容器重封装, 绝不重新编码。
    try:
        place_final(
            gpac=gpac, intermediate=part_dst, dst=dst,
            work_dir=work_dir,
            meta_source=_meta_manifest(work_dir),
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
    script_dir: Path | None = None,
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
            script_dir=script_dir,
            logger=logger, file_logger=file_logger,
            show_progress=show_progress,
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
            audio_sources = None
            if channel_sync and source_summary["audio_streams"] > 0 \
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
                log=pipe_log,
            )
    except Exception as exc:
        logger.error("[FAIL] dji pipeline | %s | %s", src, exc)
        file_logger.exception("DJI PIPELINE FAILED")
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
    skipped = [t for t in tracks if t not in usable]
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
    return (
        f"container-only re-mux: {src.name} ({len(usable)} track(s), "
        f"{len(skipped)} skipped, movie timescale {movie_ts}) -> "
        f"{dst.name} [stream copy, no re-encode]"
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


def place_final(
    *,
    gpac: GpacContainerBackend,
    intermediate: Path,
    dst: Path,
    work_dir: Path,
    log: Callable[[str], None],
    meta_source: Path | None = None,
) -> None:
    """Put the finished intermediate at ``dst`` with a matching container.

    The hardware video intermediate is **always MOV** and the final
    container follows the *source* (``core.paths.final_suffix_for``).
    Two branches, decided on the file *suffix* — because that is the
    only reliable statement available about which of the two ISOBMFF
    flavours is wanted (see ``_iso_media_kind``):

    * suffix already matches -> plain rename.  No re-mux: the MOV source
      case must not pay for, or risk, a second box rewrite.
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
    if want_suffix is not None and want_suffix == src_suffix:
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
        gpac=gpac, src=intermediate, dst=staged, log=log
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

    safe_unlink(dst)
    os.replace(staged, dst)
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
    #: Project root, used to resolve the binary permitted to run `--avhw`
    #: (`encoders.hwdecode.resolve_decode_tool`, which pins it to the
    #: sha256-verified patched build under `tools/avhw/`).
    script_dir: Path | None = None
    check_level: str = "basic"
    ffmpeg: Path | None = None
    quality_opts: dict[str, Any] | None = None
    channel_sync: bool = False
    channel_sync_opts: dict[str, Any] | None = None
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
    # the adaptive wave scheduler between waves
    throughput: list[tuple[str, float]] = field(default_factory=list)


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

    quality_csv = (
        ctx.logs_root / "quality_samples.csv"
        if ctx.check_level == "full"
        else None
    )

    # Hardware decode on this path is the rigaya readers, decided per file by
    # `encoders.hwdecode.route_decode` inside `hw_encode_with_fallback` -- there
    # is nothing to plan up front and no FFmpeg frame feed. The decision is
    # independent of the encoder and of the Sony/DJI/metadata policy: the
    # reader that produced the pictures is recorded, and every `--avhw` result
    # is gated on the integrity check.

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
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            script_dir=ctx.script_dir,
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
            throughput_cb=lambda fps: ctx.throughput.append(
                (backend.name, fps)
            ),
        )
    elif is_dji_source(source_streams):
        result = encode_one_dji_hw(
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
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            script_dir=ctx.script_dir,
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
            throughput_cb=lambda fps: ctx.throughput.append(
                (backend.name, fps)
            ),
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
            hw_decode=ctx.hw_decode,
            hw_decode_memo=ctx.hw_decode_memo,
            script_dir=ctx.script_dir,
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
            throughput_cb=lambda fps: ctx.throughput.append(
                (backend.name, fps)
            ),
            video_handoff=ctx.video_handoff,
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

    def note_wave(self, agg_fps: float, label: str = "") -> int:
        """Record one wave's aggregate fps; returns next worker count."""
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
                "[ADAPTIVE%s] wave aggregate=%.1f fps -> next workers=%d "
                "(best %.1f @ %d, cap %d)",
                f" {label}" if label else "",
                agg_fps, self.current, self.best_agg, self.best_w, self.cap,
            )
        return self.current


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

    if workers != "auto":
        n = int(workers)
        if n <= 1:
            ctx.show_progress = True
            for src in sources:
                counters[process_file_hw(ctx, backend, src, logger)] += 1
            return counters
        ctx.show_progress = False
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures = {
                ex.submit(process_file_hw, ctx, backend, src, logger): src
                for src in sources
            }
            for fut in as_completed(futures):
                counters[fut.result()] += 1
        return counters

    # ---- adaptive wave scheduling ----
    ctx.show_progress = False
    ctrl = AdaptiveJobs(logger, start=2)
    pending = deque(sources)
    while pending:
        wave_size = min(ctrl.current, len(pending))
        wave = [pending.popleft() for _ in range(wave_size)]
        ctx.throughput.clear()
        with ThreadPoolExecutor(max_workers=wave_size) as ex:
            futures = {
                ex.submit(process_file_hw, ctx, backend, src, logger): src
                for src in wave
            }
            for fut in as_completed(futures):
                counters[fut.result()] += 1
        agg = sum(fps for _, fps in ctx.throughput)
        ctrl.note_wave(agg, backend.name)
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

    while nvenc_sources or qsv_sources:
        remaining = budget
        wave: list[tuple[Path, Any]] = []
        # Take from each queue up to its own controller's allowance, but never
        # more than the global budget: the two limits compose, they do not add.
        take_n = min(ctrl_n.current, len(nvenc_sources), remaining)
        for _ in range(take_n):
            wave.append((nvenc_sources.popleft(), backend_nvenc))
        remaining -= take_n
        take_q = min(ctrl_q.current, len(qsv_sources), remaining)
        for _ in range(take_q):
            wave.append((qsv_sources.popleft(), backend_qsv))
        if not wave:
            break
        ctx.throughput.clear()
        with ThreadPoolExecutor(max_workers=len(wave)) as ex:
            futures = {
                ex.submit(process_file_hw, ctx, be, src, logger): src
                for src, be in wave
            }
            for fut in as_completed(futures):
                counters[fut.result()] += 1
        if len(wave) >= budget:
            logger.debug(
                "[MULTIHW] wave hit the global budget (%d/%d)", len(wave), budget
            )
        agg_n = sum(
            fps for name, fps in ctx.throughput if name == "nvenc"
        )
        agg_q = sum(
            fps for name, fps in ctx.throughput if name == "qsv"
        )
        if any(name == "nvenc" for name, _ in ctx.throughput):
            ctrl_n.note_wave(agg_n, "nvenc")
        if any(name == "qsv" for name, _ in ctx.throughput):
            ctrl_q.note_wave(agg_q, "qsv")
    return counters
