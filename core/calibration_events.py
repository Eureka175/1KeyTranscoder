"""Calibration event emitter (production side, best effort, read-only observer).

This module is the ONLY production-side change of the x265 calibration
collection feature. It writes structured events about what production
ALREADY decided and ALREADY computed into a local filesystem event spool:

    <spool>/events/pending/<event_id>.json

Hard boundaries (v0.7.0 task §0, §15, §16, §17, §36):

* It never changes an encoder parameter, profile, preset, scaling value,
  bitrate, output path or retry decision.
* It never judges success. ``attempt_succeeded`` is only written after
  production itself finished its encode + verification + commit.
* It never raises into production: every entry point swallows its own
  exceptions and reports a telemetry failure through the provided
  logger. A missing, crashed or disk-full Collector can therefore only
  result in *incomplete calibration data*, never in a failed encode.
* Nothing here recalculates scaling, merange, lookahead or VBV. The
  values recorded are the objects production already built
  (SourceInfo / SourceClassification / ScalingContext / EffectiveParams
  and the serialized -x265-params string).

Enabled only by the operator, through either::

    ONEXKT_CALIBRATION=1
    ONEXKT_CALIBRATION_SPOOL=D:\\1kt\\calibration   (optional override)

or an explicit ``setup()`` call. When disabled, every function below is
a cheap no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# constants shared with tools/x265_calibration/schema.json
# --------------------------------------------------------------------------

EVENT_SCHEMA_VERSION = "1.0"
RECORD_SCHEMA_VERSION = "1.0"
PRODUCER = "1KeyTranscoder/x265-calibration"

# Failure stages (schema enums.failure_stage).
STAGE_STARTUP = "startup_failed"
STAGE_ENCODE = "encode_failed"
STAGE_VERIFICATION = "verification_failed"
STAGE_COMMIT = "output_commit_failed"
STAGE_PROBE = "probe_failed"
STAGE_UNKNOWN = "unknown"

# Verification result statuses (schema enums.verification_status).
VERIFY_PASSED = "passed"
VERIFY_FAILED = "failed"
VERIFY_SKIPPED = "skipped"
VERIFY_NOT_RUN = "not_run"
VERIFY_UNKNOWN = "unknown"

# Attempt kinds (schema enums.attempt_kind).
KIND_PRIMARY = "primary"
KIND_RETRY = "retry"
KIND_DOWNGRADE = "downgrade"
KIND_HWDEC_FALLBACK = "hardware_decode_fallback"
KIND_READER_FALLBACK = "reader_fallback"

ENV_ENABLE = "ONEXKT_CALIBRATION"
ENV_SPOOL = "ONEXKT_CALIBRATION_SPOOL"

_LOG = logging.getLogger("x265_archive.calibration")

# Serialized by a lock: several worker threads emit concurrently.
_SERIAL_LOCK = threading.Lock()
_SERIAL = 0

_STATE = threading.local()
_CONFIG: dict[str, Any] = {
    "enabled": False,
    "spool": None,          # Path | None
    "project_version": "",
    "dry_run": False,
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def utc_now_iso() -> str:
    """UTC ISO-8601 with milliseconds and 'Z', e.g. 2026-01-31T04:15:09.123Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _next_serial() -> int:
    global _SERIAL
    with _SERIAL_LOCK:
        _SERIAL += 1
        return _SERIAL


def _slug(text: str, limit: int = 40) -> str:
    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in str(text)
    )
    return safe[:limit] or "x"


def make_event_id(
    event_type: str,
    job_id: str,
    attempt_id: str | None = None,
) -> str:
    """Unique, filesystem-safe, sortable event id."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    millis = f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
    parts = [
        stamp + millis,
        _slug(job_id, 24),
        _slug(attempt_id, 24) if attempt_id else "-",
        _slug(event_type, 24),
        f"{_next_serial():06d}",
        uuid.uuid4().hex[:6],
    ]
    return "-".join(parts)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming SHA-256 of a source file (best effort; "" on failure)."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path_text(path: Path, root: Path | None) -> str:
    """Relative path when the source lives under the batch input root."""
    if root is None:
        return Path(path).name
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return Path(path).name


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically: .tmp -> flush -> fsync -> os.replace().

    The Collector only ever sees complete files. Raises on failure; all
    callers here are wrapped in best-effort guards.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.write("\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            # fsync is not available on every filesystem/target; the
            # rename is what makes the event visible.
            pass
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# serialization of production domain objects (NO recalculation)
# --------------------------------------------------------------------------

def source_info_to_dict(src: Any) -> dict[str, Any]:
    """SourceInfo -> JSON, verbatim. Never re-probes the file."""
    if src is None:
        return {}
    return {
        "path": str(getattr(src, "path", "")),
        "size_bytes": int(getattr(src, "size_bytes", 0) or 0),
        "duration_sec": float(getattr(src, "duration_sec", 0.0) or 0.0),
        "width": int(getattr(src, "width", 0) or 0),
        "height": int(getattr(src, "height", 0) or 0),
        "fps": float(getattr(src, "fps", 0.0) or 0.0),
        "r_frame_rate": str(getattr(src, "r_frame_rate", "") or ""),
        "avg_frame_rate": str(getattr(src, "avg_frame_rate", "") or ""),
        "codec": str(getattr(src, "codec", "") or ""),
        "profile": str(getattr(src, "profile", "") or ""),
        "pix_fmt": str(getattr(src, "pix_fmt", "") or ""),
        "bit_depth": int(getattr(src, "bit_depth", 0) or 0),
        "chroma": str(getattr(src, "chroma", "") or ""),
        "ob_kbps": float(getattr(src, "ob_kbps", 0.0) or 0.0),
        "video_bitrate_kbps": float(
            getattr(src, "video_bitrate_kbps", 0.0) or 0.0
        ),
        "video_stream_count": int(
            getattr(src, "video_stream_count", 0) or 0
        ),
        "stream_info": [
            {
                "index": int(getattr(b, "index", 0) or 0),
                "codec_type": str(getattr(b, "codec_type", "") or ""),
                "codec_name": str(getattr(b, "codec_name", "") or ""),
            }
            for b in (getattr(src, "stream_info", ()) or ())
        ],
        "color": {
            "primaries": str(
                getattr(getattr(src, "color", None), "primaries", "") or ""
            ),
            "transfer": str(
                getattr(getattr(src, "color", None), "transfer", "") or ""
            ),
            "matrix": str(
                getattr(getattr(src, "color", None), "matrix", "") or ""
            ),
            "range": str(
                getattr(getattr(src, "color", None), "range", "") or ""
            ),
            "master_display": str(
                getattr(
                    getattr(src, "color", None), "master_display", ""
                )
                or ""
            ),
            "max_cll": str(
                getattr(getattr(src, "color", None), "max_cll", "") or ""
            ),
        },
        "video_streams_raw": [
            {
                k: v for k, v in dict(st).items()
                if k in (
                    "index", "codec_type", "codec_name", "profile",
                    "width", "height", "pix_fmt", "r_frame_rate",
                    "avg_frame_rate", "nb_frames", "bit_rate",
                    "bits_per_raw_sample", "color_space", "color_transfer",
                    "color_primaries", "color_range", "time_base",
                    "field_order", "codec_tag_string",
                )
            }
            for st in (getattr(src, "raw_streams", ()) or ())
        ],
    }


def classification_to_dict(classification: Any) -> dict[str, Any]:
    """SourceClassification -> JSON, verbatim."""
    if classification is None:
        return {}
    return {
        "source_class": str(
            getattr(classification, "source_class", "") or ""
        ),
        "normalized_ob": float(
            getattr(classification, "normalized_ob", 0.0) or 0.0
        ),
        "evidence": str(getattr(classification, "evidence", "") or ""),
    }


def scaling_context_to_dict(context: Any) -> dict[str, Any]:
    """ScalingContext -> JSON, verbatim (no recomputation)."""
    if context is None:
        return {}
    return {
        "reference_width": int(getattr(context, "reference_width", 0) or 0),
        "reference_height": int(
            getattr(context, "reference_height", 0) or 0
        ),
        "reference_fps": float(getattr(context, "reference_fps", 0.0) or 0.0),
        "spatial_factor": float(
            getattr(context, "spatial_factor", 0.0) or 0.0
        ),
        "temporal_factor": float(
            getattr(context, "temporal_factor", 0.0) or 0.0
        ),
        "pixel_rate_factor": float(
            getattr(context, "pixel_rate_factor", 0.0) or 0.0
        ),
        "aspect_ratio": float(getattr(context, "aspect_ratio", 0.0) or 0.0),
        "source_class": str(getattr(context, "source_class", "") or ""),
        "normalized_ob": float(
            getattr(context, "normalized_ob", 0.0) or 0.0
        ),
    }


def effective_params_to_dict(effective: Any) -> dict[str, Any]:
    """EffectiveParams.values -> JSON, verbatim (the resolved values)."""
    if effective is None:
        return {}
    values = getattr(effective, "values", None)
    return dict(values) if isinstance(values, dict) else {}


def effective_audit_to_dict(effective: Any) -> dict[str, Any]:
    """EffectiveParams.audit -> JSON, verbatim (the scaling trail)."""
    if effective is None:
        return {}
    audit = getattr(effective, "audit", None)
    return dict(audit) if isinstance(audit, dict) else {}


def hash_config(path: Path | None) -> str:
    """sha256 of a config file, for provenance ("" when unreadable)."""
    if path is None:
        return ""
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError:
        return ""


def parse_tool_versions(text: str) -> dict[str, str]:
    """Extract ffmpeg / libx265 versions from ffmpeg stderr or -version."""
    import re

    out: dict[str, str] = {}
    m = re.search(r"ffmpeg version\s+(\S+)", text or "")
    if m:
        out["ffmpeg"] = m.group(1)
    m = re.search(
        r"HEVC encoder version\s+(\S+)", text or ""
    )
    if m:
        out["libx265"] = m.group(1)
    if "libx265" not in out:
        m = re.search(r"libx265:\s*(\S+)", text or "")
        if m:
            out["libx265"] = m.group(1)
    return out


_LIBX265_CACHE: dict[str, Any] = {"done": False, "version": ""}


def probe_libx265_version(ffmpeg: Any) -> str:
    """libx265 version string, probed ONCE per process and cached.

    `ffmpeg -h encoder=libx265` first; on builds that do not print the
    banner there, a one-frame synthetic encode (``lavfi color``, no
    source file) is used as a fallback. Either way this runs before the
    first job, never during an encode, so it cannot disturb production
    timing.
    """
    if _LIBX265_CACHE.get("done"):
        return str(_LIBX265_CACHE.get("version") or "")
    _LIBX265_CACHE["done"] = True
    version = ""
    try:
        import subprocess

        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-h", "encoder=libx265"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        for line in (proc.stdout or "").splitlines():
            marker = "HEVC encoder version"
            if marker in line:
                version = line.split(marker, 1)[1].strip()
                break
        if not version:
            # Some builds do not print the banner in `-h`; fall back to a
            # one-frame synthetic encode (no source file is read).
            proc = subprocess.run(
                [
                    str(ffmpeg), "-hide_banner", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "color=size=64x64:rate=1",
                    "-frames:v", "1", "-c:v", "libx265",
                    "-f", "null", "-",
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=120, check=False,
            )
            for line in (proc.stdout or "").splitlines():
                marker = "HEVC encoder version"
                if marker in line:
                    version = line.split(marker, 1)[1].strip()
                    break
    except Exception:
        version = ""
    _LIBX265_CACHE["version"] = version
    return version


# --------------------------------------------------------------------------
# session state
# --------------------------------------------------------------------------

@dataclass
class AttemptState:
    attempt_id: str
    attempt_number: int
    backend: str
    started_monotonic: float
    start_time: str
    kind: str = KIND_PRIMARY
    payload_extra: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    open: bool = True


@dataclass
class JobState:
    job_id: str
    source_id: str
    source_path: str
    source_sha256: str
    relative_path: str
    start_time: str
    started_monotonic: float
    requested_preset: str
    requested_encoder: str
    attempt_counter: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    source_info: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    scaling_context: dict[str, Any] = field(default_factory=dict)
    effective_params: dict[str, Any] = field(default_factory=dict)
    effective_params_audit: dict[str, Any] = field(default_factory=dict)
    actual_x265_params: str = ""
    preset: str = ""
    crf: float | None = None
    config_hash: str = ""
    scaling_config_hash: str = ""
    output_path: str = ""
    closed: bool = False


def _job_slot() -> dict[str, Any]:
    slot = getattr(_STATE, "job", None)
    if slot is None:
        slot = {}
        _STATE.job = slot
    return slot


def _attempt_slot() -> dict[str, Any]:
    slot = getattr(_STATE, "attempt", None)
    if slot is None:
        slot = {}
        _STATE.attempt = slot
    return slot


def current_job() -> JobState | None:
    return _job_slot().get("state")


def current_attempt() -> AttemptState | None:
    return _attempt_slot().get("state")


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def setup(
    *,
    enabled: bool | None = None,
    spool: Path | str | None = None,
    project_version: str = "",
    dry_run: bool = False,
) -> bool:
    """Enable/configure the emitter. Returns True when events will be written.

    Disabled unless the operator asks for it (env var or explicit call).
    Never raises.
    """
    try:
        if enabled is None:
            enabled = str(os.environ.get(ENV_ENABLE, "")).strip().lower() in (
                "1", "true", "yes", "on",
            )
        if spool is None:
            spool_text = str(os.environ.get(ENV_SPOOL, "")).strip()
            spool = Path(spool_text) if spool_text else None
        if enabled and spool is None:
            # Default spool location, resolved by the caller's cwd only
            # when the operator did not name one explicitly.
            spool = None
        _CONFIG["enabled"] = bool(enabled) and spool is not None
        _CONFIG["spool"] = Path(spool) if spool is not None else None
        _CONFIG["project_version"] = str(project_version or "")
        _CONFIG["dry_run"] = bool(dry_run)
        if enabled and spool is None:
            _LOG.warning(
                "[CALIBRATION] %s is set but %s is empty — calibration "
                "events disabled", ENV_ENABLE, ENV_SPOOL,
            )
        return bool(_CONFIG["enabled"])
    except Exception:  # pragma: no cover - defensive
        _CONFIG["enabled"] = False
        return False


def is_enabled() -> bool:
    return bool(_CONFIG.get("enabled"))


def spool_root() -> Path | None:
    value = _CONFIG.get("spool")
    return Path(value) if value else None


def _report_failure(context: str, exc: BaseException) -> None:
    """Telemetry failure: loud in the production log, never fatal."""
    try:
        _LOG.warning(
            "[CALIBRATION] telemetry failure during %s: %s: %s "
            "(production continues; calibration data incomplete)",
            context, type(exc).__name__, exc,
        )
    except Exception:  # pragma: no cover
        pass


def emit(event_type: str, payload: dict[str, Any], *,
         event_id: str | None = None) -> str | None:
    """Best-effort atomic write of ONE event. Returns the event id or None.

    Never raises, never blocks on the Collector, never creates the
    spool directories as a precondition for encoding: if anything fails
    the function logs a telemetry failure and returns None.
    """
    if not is_enabled():
        return None
    try:
        root = spool_root()
        if root is None:
            return None
        pending = root / "events" / "pending"
        env = dict(payload)
        eid = event_id or str(env.pop("event_id", "") or "") or make_event_id(
            event_type,
            str(env.get("job_id", "") or "unknown"),
            str(env.get("attempt_id") or "") or None,
        )
        env["event_id"] = eid
        env["schema_version"] = str(
            env.get("schema_version") or EVENT_SCHEMA_VERSION
        )
        env["event_type"] = event_type
        env.setdefault("timestamp", utc_now_iso())
        env.setdefault("producer", PRODUCER)
        if _CONFIG.get("project_version"):
            env.setdefault("project_version", _CONFIG["project_version"])
        try:
            env.setdefault("host", socket.gethostname())
        except Exception:
            pass
        env.setdefault("pid", os.getpid())
        atomic_write_json(pending / f"{eid}.json", env)
        return eid
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure(f"emit({event_type})", exc)
        return None


def begin_job(
    *,
    job_id: str,
    source: Path,
    relative_path: str,
    requested_preset: str,
    requested_encoder: str,
    source_sha256: str = "",
    source_info: Any = None,
    classification: Any = None,
    effective: Any = None,
    effective_params: dict[str, Any] | None = None,
    scaling_context: dict[str, Any] | None = None,
    actual_x265_params: str = "",
    preset: str = "",
    crf: float | None = None,
    config_hash: str = "",
    scaling_config_hash: str = "",
    output_path: Path | str | None = None,
    input_root: Path | None = None,
    output_root: Path | None = None,
    logs_root: Path | None = None,
    check_level: str = "",
    presets: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Write job_started and open the job scope for this thread.

    Returns the job_id (also when telemetry is disabled, so callers can
    keep one code path).
    """
    if not is_enabled():
        return job_id
    try:
        source_path = Path(source)
        state = JobState(
            job_id=str(job_id),
            source_id=_slug(source_path.stem, 64),
            source_path=str(source_path),
            source_sha256=source_sha256
            or sha256_file(source_path),
            relative_path=relative_path,
            start_time=utc_now_iso(),
            started_monotonic=time.monotonic(),
            requested_preset=str(requested_preset),
            requested_encoder=str(requested_encoder),
            source_info=source_info_to_dict(source_info),
            classification=classification_to_dict(classification),
            scaling_context=(
                scaling_context_to_dict(getattr(effective, "context", None))
                or dict(scaling_context or {})
            ),
            effective_params=(
                effective_params_to_dict(effective)
                or dict(effective_params or {})
            ),
            effective_params_audit=effective_audit_to_dict(effective),
            actual_x265_params=str(actual_x265_params or ""),
            preset=str(preset or ""),
            crf=float(crf) if crf is not None else None,
            config_hash=str(config_hash or ""),
            scaling_config_hash=str(scaling_config_hash or ""),
            output_path=str(output_path or ""),
        )
        _job_slot()["state"] = state

        payload: dict[str, Any] = {
            "job_id": state.job_id,
            "source_id": state.source_id,
            "source_path": state.source_path,
            "relative_path": state.relative_path,
            "source_sha256": state.source_sha256,
            "start_time": state.start_time,
            "requested_preset": state.requested_preset,
            "requested_encoder": state.requested_encoder,
            "source_size_bytes": int(
                getattr(source_info, "size_bytes", 0) or 0
            ),
            "output_path": state.output_path,
            "input_root": str(input_root or ""),
            "output_root": str(output_root or ""),
            "logs_root": str(logs_root or ""),
            "check_level": str(check_level or ""),
            "dry_run": bool(_CONFIG.get("dry_run")),
        }
        if presets:
            payload["presets"] = list(presets)
        if state.source_info:
            payload["source_info"] = state.source_info
        if state.classification:
            payload["classification"] = state.classification
        if state.scaling_context:
            payload["scaling_context"] = state.scaling_context
        if state.effective_params:
            payload["effective_params"] = state.effective_params
        if state.effective_params_audit:
            payload["effective_params_audit"] = (
                state.effective_params_audit
            )
        if state.actual_x265_params:
            payload["actual_x265_params"] = state.actual_x265_params
        if state.preset:
            payload["preset"] = state.preset
        if state.crf is not None:
            payload["crf"] = state.crf
        if state.config_hash:
            payload["config_hash"] = state.config_hash
        if state.scaling_config_hash:
            payload["scaling_config_hash"] = state.scaling_config_hash
        if extra:
            payload.update(extra)
        emit("job_started", payload)
        return state.job_id
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure("begin_job", exc)
        return job_id


def begin_attempt(
    *,
    backend: str,
    preset: str = "",
    kind: str = KIND_PRIMARY,
    trigger: str = "",
    attempt_number: int | None = None,
    command: str = "",
    encoder_format: str = "",
    reader: str = "",
    crf: float | None = None,
    pix_fmt: str = "",
    extra: dict[str, Any] | None = None,
    inherit_context: bool = False,
    source_info: Any = None,
    classification: Any = None,
    effective: Any = None,
    effective_params: dict[str, Any] | None = None,
    scaling_context: dict[str, Any] | None = None,
    actual_x265_params: str = "",
) -> AttemptState | None:
    """Write attempt_started and open the attempt scope for this thread.

    Try to keep these events as the only source of attempt identity:
    a new attempt_id is minted here, so a retry can never reuse (and
    therefore never overwrite) a previous attempt.
    """
    if not is_enabled():
        return None
    try:
        job = current_job()
        if job is None:
            return None
        job.attempt_counter += 1
        number = int(attempt_number) if attempt_number else job.attempt_counter
        job.attempt_counter = max(job.attempt_counter, number)
        attempt_id = f"{job.job_id}-A{number:03d}"
        state = AttemptState(
            attempt_id=attempt_id,
            attempt_number=number,
            backend=str(backend),
            started_monotonic=time.monotonic(),
            start_time=utc_now_iso(),
            kind=str(kind or KIND_PRIMARY),
            payload_extra={},
        )
        _attempt_slot()["state"] = state

        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "attempt_id": state.attempt_id,
            "attempt_number": state.attempt_number,
            "backend": state.backend,
            "preset": str(preset or job.preset or ""),
            "start_time": state.start_time,
            "attempt_kind": state.kind,
        }
        if job.config_hash:
            payload["config_hash"] = job.config_hash
        if job.scaling_config_hash:
            payload["scaling_config_hash"] = job.scaling_config_hash
        if job.source_info:
            payload["source_size_bytes"] = int(
                job.source_info.get("size_bytes") or 0
            )
        if trigger:
            payload["trigger"] = str(trigger)
        if state.attempt_number > 1:
            previous = state.attempt_number - 1
            payload["retry_of_attempt_id"] = f"{job.job_id}-A{previous:03d}"
        if command:
            payload["command"] = str(command)
        if encoder_format:
            payload["encoder_format"] = str(encoder_format)
        if reader:
            payload["reader"] = str(reader)
        if crf is not None:
            payload["crf"] = float(crf)
        elif job.crf is not None:
            payload["crf"] = float(job.crf)
        if pix_fmt:
            payload["pix_fmt"] = str(pix_fmt)
        info_dict = source_info_to_dict(source_info) or job.source_info
        class_dict = (
            classification_to_dict(classification) or job.classification
        )
        context_dict = (
            scaling_context_to_dict(getattr(effective, "context", None))
            or dict(scaling_context or {})
            or job.scaling_context
        )
        values_dict = (
            effective_params_to_dict(effective)
            or dict(effective_params or {})
            or job.effective_params
        )
        audit_dict = (
            effective_audit_to_dict(effective)
            or job.effective_params_audit
        )
        params_text = str(actual_x265_params or job.actual_x265_params or "")
        if inherit_context or info_dict:
            if info_dict:
                payload["source_info"] = info_dict
            if class_dict:
                payload["classification"] = class_dict
            if context_dict:
                payload["scaling_context"] = context_dict
            if values_dict:
                payload["effective_params"] = values_dict
            if audit_dict:
                payload["effective_params_audit"] = audit_dict
            if params_text:
                payload["actual_x265_params"] = params_text
        if name := getattr(source_info, "chroma", ""):
            payload["chroma"] = str(name)
        if depth := getattr(source_info, "bit_depth", 0):
            payload["bit_depth"] = int(depth)
        if extra:
            payload.update(extra)
        # Everything stated here is part of the immutable attempt identity
        # and is re-emitted with the terminal event. The attempt context
        # (source/classification/scaling/effective params) travels in the
        # terminal event itself so a calibration record never depends on
        # the job_started file still being present.
        state.payload_extra = {
            key: value for key, value in payload.items()
            if key not in (
                "job_id", "attempt_id", "attempt_number", "backend",
                "start_time", "config_hash", "scaling_config_hash",
                "timestamp", "event_id", "producer",
                "schema_version", "host", "pid", "project_version",
            )
        }
        state.context = {
            key: payload[key] for key in (
                "source_info", "classification", "scaling_context",
                "effective_params", "effective_params_audit",
                "actual_x265_params",
            ) if payload.get(key)
        }
        emit("attempt_started", payload)
        return state
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure("begin_attempt", exc)
        return None


def _finish_attempt_common(
    attempt: AttemptState | None,
    *,
    include_context: bool,
    extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], JobState | None]:
    job = current_job()
    payload: dict[str, Any] = {}
    if attempt is None:
        return payload, job
    payload["job_id"] = job.job_id if job else ""
    payload["attempt_id"] = attempt.attempt_id
    payload["attempt_number"] = attempt.attempt_number
    payload["backend"] = attempt.backend
    payload["attempt_kind"] = attempt.kind
    payload["start_time"] = attempt.start_time
    payload["preset"] = (
        str(attempt.payload_extra.get("preset") or "")
        or (job.preset if job else "")
    )
    # Attempt-scoped facts stated at attempt_started must survive into the
    # terminal event (they are part of the immutable attempt identity).
    for key in (
        "retry_of_attempt_id", "parent_attempt_id", "trigger", "codec",
        "chroma", "bit_depth", "command", "encoder_format", "reader",
        "warnings",
    ):
        value = attempt.payload_extra.get(key)
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)
    if include_context:
        for key, value in (attempt.context or {}).items():
            if value not in (None, "", {}, []):
                payload.setdefault(key, value)
    # Facts production learned while building the real command line (the
    # literal -x265-params string, the pixel format) are attached at the
    # same time as the extras.
    for key, value in (extra or {}).items():
        if value not in (None, "", [], {}):
            payload[key] = value
    if include_context and job is not None:
        # Job-level context fills gaps only: the attempt's own snapshot
        # wins, because it is what this attempt actually encoded with.
        for key in (
            "source_info",
            "classification",
            "scaling_context",
            "effective_params",
            "effective_params_audit",
            "actual_x265_params",
        ):
            value = getattr(job, key, None)
            if value:
                payload.setdefault(key, value)
        if job.crf is not None:
            payload.setdefault("crf", float(job.crf))
    return payload, job


_TOOL_VERSION_CACHE: dict[str, dict[str, str]] = {}


def tool_versions_for(ffmpeg: Any) -> dict[str, str]:
    """ffmpeg version reported by the project's own ffmpeg, cached.

    One ``ffmpeg -version`` call per process (no encode, no video read).
    """
    key = str(ffmpeg)
    if key in _TOOL_VERSION_CACHE:
        return dict(_TOOL_VERSION_CACHE[key])
    versions: dict[str, str] = {}
    try:
        import subprocess

        proc = subprocess.run(
            [str(ffmpeg), "-version"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        versions.update(parse_tool_versions(proc.stdout or ""))
    except Exception:
        pass
    versions.setdefault("ffmpeg", "")
    _TOOL_VERSION_CACHE[key] = dict(versions)
    return versions


def attempt_succeeded(
    *,
    exit_code: int = 0,
    verification: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    tool_versions: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Record production's OWN success verdict. Never judges by itself.

    Must be called only after: encoder success AND production output
    verification passed AND output committed/accepted.
    """
    if not is_enabled():
        return None
    attempt = current_attempt()
    try:
        payload, job = _finish_attempt_common(
            attempt, include_context=True, extra=extra
        )
        if attempt is None:
            return None
        end = time.monotonic()
        payload.update({
            "status": "SUCCEEDED",
            "end_time": utc_now_iso(),
            "exit_code": int(exit_code),
            "wall_time_sec": round(end - attempt.started_monotonic, 6),
            "verification_result": dict(verification or {
                "status": VERIFY_SKIPPED,
            }),
            "verification_status": str(
                (verification or {}).get("status", VERIFY_SKIPPED)
            ),
        })
        for key, value in (output or {}).items():
            payload[key] = value
        if tool_versions:
            payload["tool_versions"] = dict(tool_versions)
            if tool_versions.get("ffmpeg"):
                payload["ffmpeg_version"] = str(tool_versions["ffmpeg"])
            if tool_versions.get("libx265"):
                payload["libx265_version"] = str(tool_versions["libx265"])
        if warnings:
            payload["warnings"] = list(warnings)
        if extra:
            payload.update(extra)
        attempt.open = False
        eid = emit("attempt_succeeded", payload)
        if job is not None:
            job.attempts.append({
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "status": "SUCCEEDED",
                "wall_time_sec": payload["wall_time_sec"],
                "output_size": payload.get("output_size"),
                "encode_fps": payload.get("encode_fps"),
                "attempt_kind": attempt.kind,
                "backend": attempt.backend,
            })
        _attempt_slot()["state"] = None
        return eid
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure("attempt_succeeded", exc)
        return None


def attempt_failed(
    *,
    failure_stage: str,
    failure_reason: str,
    exit_code: int | None = None,
    output_exists: bool = False,
    output_size: int | None = None,
    verification_status: str = VERIFY_NOT_RUN,
    output_path: Path | str | None = None,
    error_summary: str = "",
    tool_versions: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Record a failed attempt. NEVER overwritten by a later retry."""
    if not is_enabled():
        return None
    attempt = current_attempt()
    try:
        payload, job = _finish_attempt_common(
            attempt, include_context=True, extra=extra
        )
        if attempt is None:
            return None
        end = time.monotonic()
        payload.update({
            "status": "FAILED",
            "end_time": utc_now_iso(),
            "wall_time_sec": round(end - attempt.started_monotonic, 6),
            "failure_stage": str(failure_stage or STAGE_UNKNOWN),
            "failure_reason": str(failure_reason or ""),
            "output_exists": bool(output_exists),
            "verification_status": str(
                verification_status or VERIFY_NOT_RUN
            ),
        })
        if exit_code is not None:
            payload["exit_code"] = int(exit_code)
        if output_size is not None:
            payload["output_size"] = int(output_size)
        if output_path is not None:
            payload["output_path"] = str(output_path)
        if error_summary:
            payload["error_summary"] = _tail(error_summary, 4000)
        if tool_versions:
            payload["tool_versions"] = dict(tool_versions)
        if warnings:
            payload["warnings"] = list(warnings)
        if extra:
            payload.update(extra)
        attempt.open = False
        eid = emit("attempt_failed", payload)
        if job is not None:
            job.attempts.append({
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "status": "FAILED",
                "failure_stage": str(failure_stage or STAGE_UNKNOWN),
                "failure_reason": str(failure_reason or ""),
                "wall_time_sec": payload["wall_time_sec"],
                "attempt_kind": attempt.kind,
                "backend": attempt.backend,
            })
        _attempt_slot()["state"] = None
        return eid
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure("attempt_failed", exc)
        return None


def _tail(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else "..." + value[-limit:]


def end_job(
    *,
    accepted_attempt_number: int | None = None,
    output: dict[str, Any] | None = None,
    failure_stage: str = "",
    failure_reason: str = "",
    exit_code: int | None = None,
    error_summary: str = "",
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Close the job: SUCCESS when an attempt was accepted, else FAILED.

    The final state is derived from what production already recorded —
    the Collector never decides it and never overrides it.
    """
    if not is_enabled():
        return None
    job = current_job()
    try:
        if job is None or job.closed:
            return None
        accepted = [
            a for a in job.attempts if a["status"] == "SUCCEEDED"
        ]
        failed = [a for a in job.attempts if a["status"] == "FAILED"]
        end = time.monotonic()
        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "start_time": job.start_time,
            "end_time": utc_now_iso(),
            "wall_time_sec": round(end - job.started_monotonic, 6),
            "attempt_count": len(job.attempts),
            "failed_attempt_count": len(failed),
            "requested_preset": job.requested_preset,
            "requested_encoder": job.requested_encoder,
        }
        for key in (
            "source_info",
            "classification",
            "scaling_context",
            "effective_params",
            "actual_x265_params",
        ):
            value = getattr(job, key, None)
            if value:
                payload[key] = value
        if job.preset:
            payload["preset"] = job.preset
        if job.crf is not None:
            payload["crf"] = float(job.crf)

        if accepted:
            chosen = accepted[-1]
            number = (
                int(accepted_attempt_number)
                if accepted_attempt_number
                else int(chosen["attempt_number"])
            )
            payload.update({
                "status": "SUCCESS",
                "accepted_attempt_id": f"{job.job_id}-A{number:03d}",
                "accepted_attempt_number": number,
            })
            if job.output_path:
                payload["output_path"] = job.output_path
            for key, value in (output or {}).items():
                payload[key] = value
            if extra:
                payload.update(extra)
            job.closed = True
            return emit("job_succeeded", payload)

        payload["status"] = "FAILED"
        if failure_stage:
            payload["failure_stage"] = str(failure_stage)
        if failure_reason:
            payload["failure_reason"] = str(failure_reason)
        if exit_code is not None:
            payload["exit_code"] = int(exit_code)
        if error_summary:
            payload["error_summary"] = _tail(error_summary, 4000)
        if extra:
            payload.update(extra)
        job.closed = True
        return emit("job_failed", payload)
    except Exception as exc:      # pragma: no cover - defensive
        _report_failure("end_job", exc)
        return None


def abandon_job(reason: str = "process ended before the job reported") -> None:
    """Drop the thread-local job scope without writing a final event.

    Used when a job never reached a decision in this process; the
    Collector then keeps whatever it has with explicit completeness
    flags instead of an invented outcome.
    """
    try:
        job = current_job()
        if job is not None and not job.closed:
            _LOG.info(
                "[CALIBRATION] job %s left open (%s)", job.job_id, reason
            )
        _job_slot()["state"] = None
        _attempt_slot()["state"] = None
    except Exception:  # pragma: no cover - defensive
        pass
