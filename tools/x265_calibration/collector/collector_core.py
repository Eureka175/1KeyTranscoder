"""x265 calibration collector — core engine (standalone, standard library only).

Independent observer of the 1KeyTranscoder calibration event spool.
This package is deliberately decoupled from the production transcoder:
it imports NOTHING from ``core/`` or ``encoders/`` and it never imports
ffmpeg/x265/numpy/scipy. Its only inputs are JSON event files and its
only outputs are JSONL records, summaries and a manifest.

Hard boundaries (v0.7.0 task §0, §12, §15, §17, §29, §31, §36):

* The Collector never modifies a production parameter, never retries,
  never re-encodes, never repairs, never reclassifies, and never
  produces data that could steer a production decision.
* It never recalculates scaling: it stores the SourceInfo /
  SourceClassification / ScalingContext / EffectiveParams / actual
  -x265-params that production itself computed.
* Raw ``samples/YYYY-MM-DD.jsonl`` is the authoritative dataset; the
  daily summaries are always rebuildable from it.
* A Collector failure can only ever result in incomplete calibration
  data. It cannot influence the production encode.

Layout owned by this module::

    <root>/
      events/pending/     # producer -> collector (atomic rename in)
      events/archive/     # consumed events, verbatim
      events/failed/      # invalid events + <name>.error.txt
      samples/YYYY-MM-DD.jsonl
      summaries/YYYY-MM-DD.json
      errors/collector.log
      state/              # consumer state (offsets, buffers, seen ids)
      manifest.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

COLLECTOR_NAME = "x265-calibration-collector"
COLLECTOR_VERSION = "1.0.0"
COLLECTOR_SCHEMA_VERSION = "1.0"
RECORD_VERSION = "1.0"

# ---------------------------------------------------------------------------
# outcome codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LOCKED = 3
EXIT_ERROR = 4
EXIT_DATASET_INVALID = 5


# ---------------------------------------------------------------------------
# tiny cross-platform advisory lock (one collector per spool)
# ---------------------------------------------------------------------------

def _windows_pid_alive(pid: int) -> bool:
    """Liveness probe on Windows via OpenProcess + GetExitCodeProcess."""
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32           # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True                          # cannot tell -> assume alive
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class SpoolLock:
    """Best-effort single-instance guard for one calibration root.

    Uses O_CREAT|O_EXCL so it needs no third-party dependency and works
    on Windows and POSIX. A stale lock (dead pid, old mtime) is broken
    automatically; the Collector must always be able to start after a
    crash.
    """

    def __init__(self, root: Path, stale_after_sec: float = 3600.0) -> None:
        self.path = root / "state" / "collector.lock"
        self.stale_after_sec = stale_after_sec
        self.acquired = False

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _alive(self, pid: int) -> bool:
        """Is the recorded pid a live process?

        Windows needs OpenProcess/GetExitCodeProcess: ``os.kill(pid, 0)``
        is NOT a liveness probe there (it takes a console event code), so
        using it made every lock acquisition fail on the soak host. Any
        uncertainty is resolved by the staleness timeout, never by
        raising into the collector's startup path.
        """
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            try:
                return _windows_pid_alive(pid)
            except Exception:
                return self._fresh_enough()
        try:
            os.kill(pid, 0)                 # POSIX liveness probe
            return True
        except OSError as exc:
            if getattr(exc, "errno", None) == 13:       # EPERM: it exists
                return True
            return False

    def _fresh_enough(self) -> bool:
        """Fallback: treat a recent lock file as held (never crash)."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age < 120.0

    def acquire(self) -> tuple[bool, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "pid": os.getpid(),
            "started": utc_now_iso(),
            "host": host_name(),
        }, ensure_ascii=False)
        for attempt in (1, 2):
            try:
                fd = os.open(
                    str(self.path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
            except FileExistsError:
                info = self._read()
                pid = int(info.get("pid") or 0)
                if self._alive(pid):
                    return False, (
                        f"another collector (pid {pid}, started "
                        f"{info.get('started', '?')}) holds {self.path}"
                    )
                if attempt == 2:
                    return False, f"stale lock could not be replaced: {self.path}"
                unlink_quiet(self.path)
                continue
            except OSError as exc:
                return False, f"lock error: {exc}"
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload + "\n")
            self.acquired = True
            return True, ""
        return False, "unreachable"

    def release(self) -> None:
        if self.acquired:
            unlink_quiet(self.path)
            self.acquired = False


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def local_day(timestamp: str | None = None) -> str:
    if timestamp:
        parsed = parse_iso(timestamp)
        if parsed is not None:
            return parsed.astimezone().strftime("%Y-%m-%d")
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def parse_iso(text: str) -> datetime | None:
    value = str(text or "").strip()
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def host_name() -> str:
    try:
        import socket

        return socket.gethostname()
    except Exception:
        return ""


def unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def ensure_dirs(root: Path) -> dict[str, Path]:
    dirs = {
        "root": root,
        "pending": root / "events" / "pending",
        "archive": root / "events" / "archive",
        "failed": root / "events" / "failed",
        "samples": root / "samples",
        "summaries": root / "summaries",
        "errors": root / "errors",
        "state": root / "state",
    }
    for key, path in dirs.items():
        if key == "root":
            continue
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
    )


def append_jsonl_durable(path: Path, obj: dict[str, Any]) -> int:
    """Append ONE JSON line, fsync'd. Returns the byte offset AFTER it.

    Raising here is safe: the caller keeps the event in pending and
    retries later, so a partially written line is never counted.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    data = (line + "\n").encode("utf-8")
    with path.open("ab") as fh:
        fh.seek(0, os.SEEK_END)
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
        return fh.tell()


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Iterate JSON lines, skipping an incomplete trailing line."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                text = raw.strip()
                if not text:
                    continue
                try:
                    yield json.loads(text)
                except ValueError:
                    continue
    except OSError:
        return


def tail_of_file(path: Path, limit: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

class Schema:
    """Loaded copy of schema.json (bundled with the executable)."""

    def __init__(self, data: dict[str, Any], source: str = "<embedded>") -> None:
        self.data = data
        self.source = source
        self.event_types = set(data.get("event_types") or [])
        self.payloads: dict[str, dict[str, Any]] = data.get("payloads") or {}
        envelope = data.get("envelope") or {}
        self.envelope_required: dict[str, str] = envelope.get("required") or {}
        self.envelope_optional: dict[str, str] = envelope.get("optional") or {}
        enums = data.get("enums") or {}
        self.enums: dict[str, set[str]] = {
            key: set(value)
            for key, value in enums.items()
            if isinstance(value, list)
        }
        self.event_schema_version = str(
            data.get("event_schema_version") or COLLECTOR_SCHEMA_VERSION
        )

    def required_for(self, event_type: str) -> dict[str, str]:
        spec = self.payloads.get(event_type) or {}
        return spec.get("required") or {}

    def optional_for(self, event_type: str) -> dict[str, str]:
        spec = self.payloads.get(event_type) or {}
        return spec.get("optional") or {}

    def known_payload_keys(self, event_type: str) -> set[str]:
        return set(self.required_for(event_type)) | set(
            self.optional_for(event_type)
        )

    def all_payload_keys(self) -> set[str]:
        keys: set[str] = set()
        for event_type in self.payloads:
            keys |= self.known_payload_keys(event_type)
        return keys


_SCHEMA_CACHE: dict[str, Schema] = {}


def load_schema(explicit: Path | None = None) -> Schema:
    """Find schema.json next to the code, in the bundle, or in the cwd."""
    if explicit is not None:
        try:
            data = json.loads(Path(explicit).read_text(encoding="utf-8"))
            return Schema(data, str(explicit))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot read schema {explicit}: {exc}") from exc

    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    candidates.append(here / "schema.json")
    candidates.append(here.parent / "schema.json")
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "schema.json")
        candidates.append(Path(bundle) / "x265_calibration" / "schema.json")
    candidates.append(Path.cwd() / "schema.json")
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "schema.json")
    try:
        candidates.append(Path(sys.argv[0]).resolve().parent / "schema.json")
    except (OSError, ValueError):
        pass

    for candidate in candidates:
        key = str(candidate)
        if key in _SCHEMA_CACHE:
            return _SCHEMA_CACHE[key]
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            schema = Schema(data, key)
            _SCHEMA_CACHE[key] = schema
            return schema
    raise RuntimeError(
        "schema.json not found (looked in: "
        + ", ".join(str(c) for c in candidates)
        + ")"
    )


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{4,160}$")


class ValidationResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _check_type(value: Any, expected: str) -> bool:
    types = _TYPE_CHECKS.get(expected)
    if types is None:
        return True
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, types)


def validate_event(
    event: Any,
    schema: Schema,
    *,
    strict_warnings: bool = False,
) -> ValidationResult:
    """Shape + semantics validation of one decoded event.

    Unknown fields are warnings (forward compatibility); missing or
    mistyped required fields are errors (the event is quarantined).
    """
    result = ValidationResult()
    if not isinstance(event, dict):
        result.errors.append("event is not a JSON object")
        return result

    for field_name, expected in schema.envelope_required.items():
        if field_name not in event:
            result.errors.append(f"missing envelope field '{field_name}'")
            continue
        value = event[field_name]
        if value is None or (isinstance(value, str) and not value.strip()):
            result.errors.append(f"empty envelope field '{field_name}'")
            continue
        if not _check_type(value, expected):
            result.errors.append(
                f"envelope field '{field_name}' must be {expected}, "
                f"got {type(value).__name__}"
            )

    for field_name, expected in schema.envelope_optional.items():
        if field_name in event and event[field_name] is not None:
            if not _check_type(event[field_name], expected):
                result.errors.append(
                    f"envelope field '{field_name}' must be {expected}, "
                    f"got {type(event[field_name]).__name__}"
                )

    event_type = str(event.get("event_type") or "")
    if event_type not in schema.event_types:
        result.errors.append(f"unknown event_type '{event_type}'")
        return result

    for field_name, expected in schema.required_for(event_type).items():
        if field_name not in event:
            result.errors.append(
                f"{event_type}: missing required field '{field_name}'"
            )
            continue
        value = event[field_name]
        if value is None:
            result.errors.append(
                f"{event_type}: required field '{field_name}' is null"
            )
            continue
        if isinstance(value, str) and not value.strip():
            result.errors.append(
                f"{event_type}: required field '{field_name}' is empty"
            )
            continue
        if not _check_type(value, expected):
            result.errors.append(
                f"{event_type}: field '{field_name}' must be {expected}, "
                f"got {type(value).__name__}"
            )

    for field_name, expected in schema.optional_for(event_type).items():
        if field_name in event and event[field_name] is not None:
            if not _check_type(event[field_name], expected):
                result.errors.append(
                    f"{event_type}: field '{field_name}' must be {expected}, "
                    f"got {type(event[field_name]).__name__}"
                )

    allowed = set(schema.envelope_required) | set(schema.envelope_optional) \
        | schema.known_payload_keys(event_type)
    for key in event:
        if key not in allowed:
            result.warnings.append(
                f"{event_type}: unknown field '{key}' (kept as-is)"
            )

    _semantic_checks(event, event_type, schema, result)

    if strict_warnings and result.warnings:
        result.errors.extend(result.warnings)
        result.warnings = []
    return result


def _semantic_checks(
    event: dict[str, Any],
    event_type: str,
    schema: Schema,
    result: ValidationResult,
) -> None:
    def enum_check(field_name: str, enum_name: str, required: bool) -> None:
        value = event.get(field_name)
        if value in (None, ""):
            if required:
                result.errors.append(
                    f"{event_type}: missing '{field_name}'"
                )
            return
        allowed = schema.enums.get(enum_name) or set()
        if allowed and str(value) not in allowed:
            result.errors.append(
                f"{event_type}: '{field_name}'={value!r} not in "
                f"{enum_name} {sorted(allowed)}"
            )

    enum_check("event_type", "event_type", True)
    if event_type == "attempt_failed":
        enum_check("failure_stage", "failure_stage", True)
    if "verification_status" in event:
        enum_check(
            "verification_status", "verification_status",
            event_type in ("attempt_failed",),
        )
    if event_type in ("job_succeeded", "job_failed"):
        enum_check("status", "job_status", True)
        expected = "SUCCESS" if event_type == "job_succeeded" else "FAILED"
        if str(event.get("status") or "") not in ("", expected):
            result.errors.append(
                f"{event_type}: status must be {expected!r}, "
                f"got {event.get('status')!r}"
            )
    if event_type in ("attempt_succeeded", "attempt_failed"):
        enum_check("status", "attempt_status", True)
        expected = (
            "SUCCEEDED" if event_type == "attempt_succeeded" else "FAILED"
        )
        if str(event.get("status") or "") not in ("", expected):
            result.errors.append(
                f"{event_type}: status must be {expected!r}, "
                f"got {event.get('status')!r}"
            )

    event_id = str(event.get("event_id") or "")
    if event_id and not _ID_RE.match(event_id):
        result.errors.append(
            f"event_id {event_id!r} is not filesystem-safe"
        )

    for key in ("job_id", "attempt_id"):
        value = event.get(key)
        if value and not _ID_RE.match(str(value)):
            result.errors.append(f"{key} {value!r} is not filesystem-safe")

    for key in (
        "timestamp", "start_time", "end_time",
    ):
        value = event.get(key)
        if value and parse_iso(value) is None:
            result.errors.append(
                f"{key} {value!r} is not a parseable ISO-8601 timestamp"
            )

    if event_type in ("attempt_started", "attempt_succeeded", "attempt_failed"):
        number = event.get("attempt_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            result.errors.append(
                f"{event_type}: attempt_number must be a positive integer "
                f"(got {number!r})"
            )
        if not event.get("attempt_id"):
            result.errors.append(f"{event_type}: missing attempt_id")

    if event_type == "attempt_started":
        # Provenance hashes are expected for the software x265 path; a
        # backend without a x265 config may legitimately omit them. Warn
        # (do not reject) so the dataset records the difference.
        for key in ("config_hash", "scaling_config_hash"):
            if not str(event.get(key) or "").strip():
                result.warnings.append(
                    f"attempt_started: '{key}' not reported by the producer"
                )

    if event_type == "job_succeeded":
        number = event.get("accepted_attempt_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            result.errors.append(
                "job_succeeded: accepted_attempt_number must be a positive "
                f"integer (got {number!r})"
            )
        if not event.get("accepted_attempt_id"):
            result.errors.append(
                "job_succeeded: missing accepted_attempt_id"
            )

    start = parse_iso(str(event.get("start_time") or ""))
    end = parse_iso(str(event.get("end_time") or ""))
    if start and end and end < start:
        result.warnings.append(
            f"{event_type}: end_time precedes start_time"
        )


# ---------------------------------------------------------------------------
# event file reading
# ---------------------------------------------------------------------------

def parse_event_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Decode one spool file into events.

    Accepts a single JSON object, a JSON array of objects, or one JSON
    object per line (JSONL). Returns (events, error_text).
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"unreadable: {exc}"
    text = raw.strip()
    if not text:
        return [], "empty file (partial write?)"
    try:
        data = json.loads(text)
    except ValueError as exc:
        events: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                return [], (
                    f"not valid JSON ({exc}); line {index} is also invalid — "
                    "file looks partially written"
                )
            if not isinstance(obj, dict):
                return [], f"line {index} is not a JSON object"
            events.append(obj)
        if not events:
            return [], "no JSON object found"
        return events, ""
    if isinstance(data, dict):
        return [data], ""
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        return data, ""
    return [], "JSON root is neither an object nor an array of objects"


def is_internal_temp(name: str) -> bool:
    """Files the producer may still be writing (atomicity)."""
    return name.startswith(".") or name.endswith(".tmp") or name.endswith(
        ".partial"
    )


# ---------------------------------------------------------------------------
# calibration record assembly
# ---------------------------------------------------------------------------

_RECORD_CONTEXT_KEYS = (
    "source_info",
    "classification",
    "scaling_context",
    "effective_params",
    "effective_params_audit",
    "actual_x265_params",
)

_COMPLETENESS_FIELDS = (
    "project_version",
    "job_id",
    "attempt_id",
    "attempt_number",
    "source_id",
    "source_path",
    "source_info",
    "classification",
    "scaling_context",
    "effective_params",
    "actual_x265_params",
    "start_time",
    "end_time",
    "wall_time_sec",
    "status",
    "verification_result",
)


def _merge_context(record: dict[str, Any], *sources: dict[str, Any]) -> None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _RECORD_CONTEXT_KEYS:
            value = source.get(key)
            if value in (None, "", {}, []):
                continue
            if record.get(key) in (None, "", {}, []):
                record[key] = value


def build_record(
    *,
    started: dict[str, Any] | None,
    finished: dict[str, Any],
    job_context: dict[str, Any],
    source_started: dict[str, Any] | None,
) -> dict[str, Any]:
    """One immutable attempt-level calibration record.

    Values are copied from the events verbatim. Missing pieces are left
    empty and flagged in ``completeness`` — the Collector never invents
    or recomputes a production value.
    """
    attempt_number = finished.get("attempt_number")
    record: dict[str, Any] = {
        "record_version": RECORD_VERSION,
        "record_type": "attempt",
        "event_schema_version": finished.get("schema_version", ""),
        "collected_at": utc_now_iso(),

        "project_version": finished.get("project_version")
        or job_context.get("project_version") or "",

        "job_id": finished.get("job_id", ""),
        "attempt_id": finished.get("attempt_id", ""),
        "attempt_number": attempt_number,
        "attempt_kind": finished.get("attempt_kind", ""),
        "attempt_sequence": 0,
        "retry_of_attempt_id": finished.get("retry_of_attempt_id", ""),
        "trigger": finished.get("trigger", ""),

        "source_id": job_context.get("source_id", ""),
        "source_sha256": job_context.get("source_sha256", ""),
        "source_path": job_context.get("source_path", ""),
        "relative_path": job_context.get("relative_path", ""),
        "source_size_bytes": job_context.get("source_size_bytes", 0),

        "backend": finished.get("backend") or job_context.get(
            "requested_encoder", ""
        ),
        "codec": finished.get("codec", ""),
        "encoder_format": finished.get("encoder_format", ""),
        "reader": finished.get("reader", ""),
        "preset": finished.get("preset") or job_context.get("preset", ""),
        "crf": finished.get("crf", job_context.get("crf")),
        "pix_fmt": finished.get("pix_fmt", ""),
        "config_hash": job_context.get("config_hash", ""),
        "scaling_config_hash": job_context.get("scaling_config_hash", ""),

        "start_time": finished.get("start_time")
        or (started or {}).get("start_time", ""),
        "end_time": finished.get("end_time", ""),
        "wall_time_sec": finished.get("wall_time_sec"),

        "status": finished.get("status", ""),
        "exit_code": finished.get("exit_code"),

        "output_path": finished.get("output_path", ""),
        "output_size": finished.get("output_size"),
        "output_duration": finished.get("output_duration"),
        "output_width": finished.get("output_width"),
        "output_height": finished.get("output_height"),
        "output_video_bitrate": finished.get("output_video_bitrate"),
        "output_frame_count": finished.get("output_frame_count"),
        "output_fps": finished.get("output_fps"),
        "output_pix_fmt": finished.get("output_pix_fmt", ""),
        "output_codec": finished.get("output_codec", ""),
        "compression_ratio": finished.get("compression_ratio"),
        "encode_fps": finished.get("encode_fps"),

        "verification_result": finished.get("verification_result"),
        "verification_status": finished.get("verification_status", ""),

        "failure_stage": finished.get("failure_stage", ""),
        "failure_reason": finished.get("failure_reason", ""),
        "error_summary": finished.get("error_summary", ""),

        "ffmpeg_version": finished.get("ffmpeg_version", ""),
        "libx265_version": finished.get("libx265_version", ""),
        "tool_versions": finished.get("tool_versions", {}),
        "warnings": finished.get("warnings", []),

        "command": finished.get("command", ""),
        "preservation": finished.get("preservation"),

        "run_id": job_context.get("run_id", ""),
        "collector_version": COLLECTOR_VERSION,
        "producer": finished.get("producer", ""),
        "host": finished.get("host", ""),
        "pid": finished.get("pid"),

        # join keys for the source -> job -> attempt -> output model
        "source_event_id": (source_started or {}).get("event_id", ""),
        "attempt_started_event_id": (started or {}).get("event_id", ""),
        "attempt_finished_event_id": finished.get("event_id", ""),
    }

    _merge_context(record, started or {}, finished, source_started or {})

    # A failed attempt reports its verification verdict as the schema
    # requires (verification_status). Surface it as a structured block too
    # so both outcomes are analysable the same way.
    if not record.get("verification_result"):
        status = str(record.get("verification_status") or "")
        if status:
            record["verification_result"] = {
                "status": status,
                "checks": [],
                "failure_stage": record.get("failure_stage", ""),
                "failure_reason": record.get("failure_reason", ""),
                "source": "attempt_failed event (production verdict)",
            }

    missing = [
        field for field in _COMPLETENESS_FIELDS
        if record.get(field) is None
        or (isinstance(record.get(field), str) and not record[field].strip())
    ]
    record["completeness"] = {
        "complete": not missing,
        "missing": missing,
        "attempt_started_seen": started is not None,
        "job_started_seen": source_started is not None,
        "job_finished_seen": bool(job_context.get("job_final_status")),
    }
    # Job-level join keys: the source -> job -> attempt -> output model.
    record["job_final_status"] = job_context.get("job_final_status", "")
    record["job_final_event_id"] = job_context.get("job_final_event_id", "")
    record["job_attempt_count"] = job_context.get("job_attempt_count")
    if record["job_attempt_count"] is None:
        record["job_attempt_count"] = job_context.get("attempt_count")
    record["job_failed_attempt_count"] = job_context.get(
        "job_failed_attempt_count"
    )
    if record["job_failed_attempt_count"] is None:
        record["job_failed_attempt_count"] = job_context.get(
            "failed_attempt_count"
        )
    record["accepted_attempt_number"] = job_context.get(
        "accepted_attempt_number"
    )
    record["accepted_attempt_id"] = job_context.get("accepted_attempt_id", "")
    if record["job_attempt_count"] is None:
        record["job_attempt_count"] = len(job_context.get("attempts") or {})
    return record


# ---------------------------------------------------------------------------
# job bookkeeping (in-memory, persistable, restartable)
# ---------------------------------------------------------------------------

class PendingStore:
    """Events consumed but not yet persistable (join not satisfied).

    ``ordered`` preserves arrival order so the dataset is deterministic.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.ordered: list[dict[str, Any]] = []
        self.attempt_numbers: dict[str, int] = {}

    # -- jobs ---------------------------------------------------------
    def ensure_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.setdefault(job_id, {"job_id": job_id})

    def note_source(self, event: dict[str, Any]) -> None:
        job = self.ensure_job(str(event.get("job_id") or ""))
        job["source_event_id"] = event.get("event_id", "")
        job["source_info"] = event.get("source_info") or job.get(
            "source_info", {}
        )
        job["classification"] = event.get("classification") or job.get(
            "classification", {}
        )
        job["scaling_context"] = event.get("scaling_context") or job.get(
            "scaling_context", {}
        )
        job["effective_params"] = event.get("effective_params") or job.get(
            "effective_params", {}
        )
        job["effective_params_audit"] = (
            event.get("effective_params_audit")
            or job.get("effective_params_audit", {})
        )
        job["actual_x265_params"] = event.get("actual_x265_params") or job.get(
            "actual_x265_params", ""
        )
        for key in (
            "source_id", "source_sha256", "source_path", "relative_path",
            "source_size_bytes", "requested_preset", "requested_encoder",
            "preset", "crf", "config_hash", "scaling_config_hash",
            "project_version", "run_id", "output_path", "check_level",
        ):
            if event.get(key) not in (None, ""):
                job[key] = event[key]
        job["source_started"] = event

    def note_attempt_started(self, event: dict[str, Any]) -> None:
        job_id = str(event.get("job_id") or "")
        job = self.ensure_job(job_id)
        attempts = job.setdefault("attempts", {})
        attempt_id = str(event.get("attempt_id") or "")
        attempts[attempt_id] = {
            "started": event,
            "finished": None,
            "seq": len(attempts),
        }
        number = event.get("attempt_number")
        if isinstance(number, int):
            key = f"{job_id}:{number}"
            self.attempt_numbers[key] = self.attempt_numbers.get(key, 0) + 1
        for key in (
            "preset", "crf", "pix_fmt", "codec", "chroma", "bit_depth",
            "encoder_format", "reader",
        ):
            if event.get(key) not in (None, "") and not job.get(key):
                job[key] = event[key]

    def note_attempt_finished(
        self,
        event: dict[str, Any],
        *,
        finished: bool,
    ) -> dict[str, Any] | None:
        """Attach a terminal attempt event; returns the record when ready."""
        job_id = str(event.get("job_id") or "")
        job = self.ensure_job(job_id)
        attempts = job.setdefault("attempts", {})
        attempt_id = str(event.get("attempt_id") or "")
        entry = attempts.get(attempt_id)
        if entry is None:
            # attempt_started not seen (lost/never written): still keep the
            # outcome, flagged as incomplete rather than dropped.
            entry = attempts[attempt_id] = {
                "started": None, "finished": None, "seq": len(attempts),
            }
        entry["finished"] = event
        job["last_finished_attempt"] = event
        job.setdefault("first_finished_at", time.monotonic())

        if finished:
            return self.finalize_record(job_id, attempt_id)
        return None

    def note_job_final(self, event: dict[str, Any]) -> None:
        job = self.ensure_job(str(event.get("job_id") or ""))
        job["job_final_status"] = event.get("status", "")
        job["job_final_event_id"] = event.get("event_id", "")
        job["accepted_attempt_id"] = event.get("accepted_attempt_id", "")
        job["accepted_attempt_number"] = event.get(
            "accepted_attempt_number"
        )
        for key in ("attempt_count", "failed_attempt_count"):
            if event.get(key) is not None:
                job[key] = event[key]
        job["job_final_event"] = event

    def can_finalize(self, job_id: str, attempt_id: str) -> bool:
        """A record waits for the source/job context (claim 18/31)."""
        job = self.jobs.get(job_id)
        if job is None:
            return True
        return bool(job.get("source_started"))

    def finalize_record(self, job_id: str, attempt_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        entry = (job.get("attempts") or {}).get(attempt_id)
        if entry is None or entry.get("finished") is None:
            return None
        record = build_record(
            started=entry.get("started"),
            finished=entry["finished"],
            job_context=job,
            source_started=job.get("source_started"),
        )
        record["attempt_sequence"] = int(entry.get("seq", 0)) + 1
        return record

    def pending_attempts(self, *, only_ready: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for job_id, job in self.jobs.items():
            ready = bool(job.get("source_started"))
            if only_ready and not ready:
                continue
            attempts = job.get("attempts") or {}
            for attempt_id in sorted(
                attempts,
                key=lambda a: int(attempts[a].get("seq", 0)),
            ):
                entry = attempts[attempt_id]
                if entry.get("finished") is None:
                    continue
                if entry.get("persisted"):
                    continue
                out.append((job_id, attempt_id))
        return out

    def attempts_waiting_for_job_final(
        self,
        *,
        now_monotonic: float,
        max_wait_sec: float,
    ) -> list[tuple[str, str]]:
        """Terminal attempts whose job-final event has not arrived yet.

        They are written immediately when a job final state is known, or
        after ``max_wait_sec`` (so a lost job event cannot stall the
        dataset — the record is then flagged incomplete).
        """
        out: list[tuple[str, str]] = []
        for job_id, job in self.jobs.items():
            if not job.get("source_started"):
                continue
            if job.get("job_final_status"):
                continue
            ready_at = float(job.get("first_finished_at") or 0.0)
            if not ready_at:
                continue
            if max_wait_sec > 0 and (now_monotonic - ready_at) < max_wait_sec:
                continue
            attempts = job.get("attempts") or {}
            for attempt_id in sorted(
                attempts, key=lambda a: int(attempts[a].get("seq", 0))
            ):
                entry = attempts[attempt_id]
                if entry.get("finished") is None or entry.get("persisted"):
                    continue
                out.append((job_id, attempt_id))
        return out

    def is_empty(self) -> bool:
        return all(
            not (job.get("attempts") or {}).get(a, {}).get("finished")
            for job in self.jobs.values()
            for a in (job.get("attempts") or {})
        ) and all(not job.get("job_final_status") for job in self.jobs.values())

    def prune_closed(self) -> None:
        """Drop fully consumed jobs so memory stays flat for a month."""
        for job_id in list(self.jobs):
            job = self.jobs[job_id]
            attempts = job.get("attempts") or {}
            unfinished = [
                a for a, entry in attempts.items()
                if entry.get("finished") is None
                or not entry.get("persisted")
            ]
            if unfinished:
                continue
            if not job.get("job_final_status"):
                continue
            del self.jobs[job_id]

    def forget_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)

    # -- persistence --------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "saved_at": utc_now_iso(),
            "jobs": self.jobs,
            "attempt_numbers": self.attempt_numbers,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PendingStore":
        store = cls()
        jobs = data.get("jobs")
        if isinstance(jobs, dict):
            store.jobs = {
                str(k): v for k, v in jobs.items() if isinstance(v, dict)
            }
        numbers = data.get("attempt_numbers")
        if isinstance(numbers, dict):
            store.attempt_numbers = {
                str(k): int(v) for k, v in numbers.items()
                if isinstance(v, int)
            }
        return store


# ---------------------------------------------------------------------------
# collector
# ---------------------------------------------------------------------------

class Counters:
    def __init__(self) -> None:
        self.events_seen = 0
        self.events_valid = 0
        self.events_invalid = 0
        self.events_duplicate = 0
        self.events_buffered = 0
        self.records_written = 0
        self.records_skipped_duplicate = 0

    def as_dict(self) -> dict[str, int]:
        return dict(vars(self))


class Collector:
    """watch -> validate -> persist -> archive."""

    def __init__(
        self,
        root: Path,
        *,
        schema: Schema | None = None,
        poll_interval: float = 2.0,
        strict: bool = False,
        log: Any = None,
        dry_run: bool = False,
        job_final_timeout_sec: float = 900.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.dirs = ensure_dirs(self.root)
        self.schema = schema or load_schema()
        self.poll_interval = max(0.1, float(poll_interval))
        self.strict = bool(strict)
        self.dry_run = bool(dry_run)
        self.job_final_timeout_sec = max(0.0, float(job_final_timeout_sec))
        self.counters = Counters()
        self.store = PendingStore()
        self.seen: set[str] = set()
        self.seen_loaded_from_dataset = False
        self.offsets: dict[str, int] = {}
        self.attempt_ids: set[str] = set()
        self._log = log or (lambda level, msg: None)
        self._state_path = self.dirs["state"] / "collector_state.json"
        self._offsets_path = self.dirs["state"] / "jsonl_offsets.json"
        self._order: list[str] = []

    # -- logging ------------------------------------------------------
    def log(self, level: str, message: str) -> None:
        self._log(level, message)

    # -- lifecycle ----------------------------------------------------
    def load_state(self) -> None:
        """Restart recovery: offsets, buffer and already-persisted ids."""
        self._truncate_partial_tails()
        try:
            data = json.loads(self._offsets_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.offsets = {
                    str(k): int(v) for k, v in data.get("offsets", {}).items()
                    if isinstance(v, int)
                }
        except (OSError, ValueError):
            self.offsets = {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.store = PendingStore.from_json(raw)
                for key in raw.get("seen_event_ids", []) or []:
                    self.seen.add(str(key))
        except (OSError, ValueError):
            pass
        self._load_seen_from_dataset()
        self.log(
            "info",
            f"state loaded: {len(self.offsets)} sample file(s), "
            f"{len(self.seen)} consumed event id(s), "
            f"{len(self.store.jobs)} open job(s)",
        )

    def save_state(self) -> None:
        if self.dry_run:
            return
        atomic_write_json(
            self._offsets_path,
            {"version": 1, "saved_at": utc_now_iso(),
             "offsets": self.offsets},
        )
        atomic_write_json(self._state_path, {
            **self.store.to_json(),
            "seen_event_ids": sorted(self.seen)[-200000:],
        })

    def _truncate_partial_tails(self) -> None:
        """Drop a torn last line left by a crash (partial JSONL)."""
        for path in sorted(self.dirs["samples"].glob("*.jsonl")):
            try:
                with path.open("rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if not data or data.endswith(b"\n"):
                continue
            index = data.rfind(b"\n")
            keep = data[: index + 1] if index >= 0 else b""
            try:
                with path.open("r+b") as fh:
                    fh.truncate(len(keep))
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                self.log(
                    "warn",
                    f"could not truncate partial line in {path.name}: {exc}",
                )
                continue
            self.log(
                "warn",
                f"recovered: dropped a partial trailing line from "
                f"{path.name} (crash/power-loss torn write)",
            )

    def _load_seen_from_dataset(self) -> None:
        """Authoritative duplicate detection straight from raw JSONL."""
        count = 0
        for path in sorted(self.dirs["samples"].glob("*.jsonl")):
            for record in read_jsonl(path):
                for key in (
                    "attempt_finished_event_id",
                    "attempt_started_event_id",
                    "source_event_id",
                ):
                    value = record.get(key)
                    if value:
                        self.seen.add(str(value))
                        count += 1
                attempt_id = record.get("attempt_id")
                if attempt_id:
                    self.attempt_ids.add(str(attempt_id))
        if count:
            self.seen_loaded_from_dataset = True
            self.log(
                "info",
                f"dataset index rebuilt from raw JSONL: {count} event id(s), "
                f"{len(self.attempt_ids)} attempt(s)",
            )

    # -- discovery ----------------------------------------------------
    def pending_files(self) -> list[Path]:
        found: list[Path] = []
        for path in self.dirs["pending"].iterdir():
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if is_internal_temp(path.name):
                continue                      # atomic write in progress
            if path.suffix.lower() not in (".json", ".jsonl"):
                continue
            found.append(path)
        found.sort(key=lambda p: (p.stat().st_mtime, p.name))
        return found

    # -- one sweep ----------------------------------------------------
    def sweep(self) -> Counters:
        self.counters = Counters()
        self._load_seen_from_dataset()
        files = self.pending_files()
        for path in files:
            events, error = parse_event_file(path)
            if error:
                self._quarantine(path, error)
                continue
            for index, event in enumerate(events):
                self.process_event(event, path, index)
        self._drain_ready_records()
        if not self.dry_run:
            self.store.prune_closed()
            self.save_state()
            self.write_manifest()
        return self.counters

    def _quarantine(self, path: Path, error: str) -> None:
        self.counters.events_invalid += 1
        name = path.name
        if not self.dry_run:
            try:
                target = self.dirs["failed"] / name
                if target.exists():
                    target = self.dirs["failed"] / f"{int(time.time())}-{name}"
                os.replace(path, target)
                atomic_write_text(
                    target.with_name(target.name + ".error.txt"),
                    f"collector: {COLLECTOR_VERSION}\n"
                    f"file: {path}\n"
                    f"time: {utc_now_iso()}\n"
                    f"validation: {error}\n"
                    f"size: {path.stat().st_size if path.exists() else '?'}\n"
                    f"excerpt tail:\n{tail_of_file(target, 12)}\n",
                )
            except OSError as exc:
                self.log("error", f"could not quarantine {name}: {exc}")
        self.log("error", f"invalid event file quarantined: {name} | {error}")

    def process_event(
        self,
        event: dict[str, Any],
        path: Path,
        index: int = 0,
    ) -> None:
        self.counters.events_seen += 1
        event_id = str(event.get("event_id") or "")
        event_type = str(event.get("event_type") or "")

        result = validate_event(event, self.schema, strict_warnings=self.strict)
        if not result.ok:
            self._reject_event(event, path, index, result)
            return
        for warning in result.warnings:
            self.log("warn", f"{path.name}: {warning}")

        if event_id and event_id in self.seen:
            self.counters.events_duplicate += 1
            self.log(
                "info",
                f"duplicate event {event_id} ignored (already consumed)",
            )
            self._consume_file(path, index, len_after=self._events_in(path))
            return

        self.counters.events_valid += 1
        try:
            self._apply(event, event_type)
        except Exception as exc:            # pragma: no cover - defensive
            self._reject_event(
                event, path, index,
                _error_result(f"internal error applying event: {exc}"),
            )
            self.log("error", traceback.format_exc())
            return

        if event_id:
            self.seen.add(event_id)
        if event_type in ("job_started", "job_succeeded", "job_failed"):
            # job-level events are fully captured in the buffer; nothing
            # else to persist from them.
            pass
        self._consume_file(path, index, len_after=self._events_in(path))

    def _events_in(self, path: Path) -> int:
        """How many events the file holds (for multi-event files)."""
        if not path.exists():
            return 1
        try:
            if path.suffix.lower() == ".jsonl":
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    return sum(1 for line in fh if line.strip())
        except OSError:
            return 1
        return 1

    def _apply(self, event: dict[str, Any], event_type: str) -> None:
        if event_type == "job_started":
            self.store.note_source(event)
            self.log(
                "info",
                f"job_started {event.get('job_id')} "
                f"({event.get('relative_path') or event.get('source_path')})",
            )
        elif event_type == "attempt_started":
            self.store.note_attempt_started(event)
            self.log(
                "info",
                f"attempt_started {event.get('attempt_id')} "
                f"#{event.get('attempt_number')} backend="
                f"{event.get('backend')}",
            )
        elif event_type == "attempt_succeeded":
            # Buffered; persisted at the end of the sweep once the job's
            # final state is known (see _drain_ready_records).
            self.store.note_attempt_finished(event, finished=True)
            self.log(
                "info",
                f"attempt_succeeded {event.get('attempt_id')} "
                f"wall={event.get('wall_time_sec')}s",
            )
        elif event_type == "attempt_failed":
            self.store.note_attempt_finished(event, finished=True)
            self.log(
                "warn",
                f"attempt_failed {event.get('attempt_id')} "
                f"stage={event.get('failure_stage')} "
                f"reason={str(event.get('failure_reason'))[:160]}",
            )
        elif event_type in ("job_succeeded", "job_failed"):
            self.store.note_job_final(event)
            self.log(
                "info",
                f"{event_type} {event.get('job_id')} "
                f"status={event.get('status')}",
            )

    def _reject_event(
        self,
        event: dict[str, Any],
        path: Path,
        index: int,
        result: ValidationResult,
    ) -> None:
        self.counters.events_invalid += 1
        event_id = str(event.get("event_id") or f"{path.name}#{index}")
        detail = "; ".join(result.errors) or "unknown validation failure"
        self.log(
            "error",
            f"event {event_id} rejected: {detail}",
        )
        if self.dry_run:
            return
        record = self.dirs["errors"] / "rejected_events.jsonl"
        try:
            append_jsonl_durable(record, {
                "detected_at": utc_now_iso(),
                "collector_version": COLLECTOR_VERSION,
                "source_file": str(path),
                "line": index,
                "event_id": event_id,
                "event_type": event.get("event_type", ""),
                "job_id": event.get("job_id", ""),
                "errors": result.errors,
                "warnings": result.warnings,
                "raw_event": event,
            })
        except OSError as exc:
            self.log("error", f"could not record rejected event: {exc}")
        self._consume_file(path, index, len_after=self._events_in(path))

    def _consume_file(self, path: Path, index: int, *, len_after: int) -> None:
        """Archive the event file exactly once all its events were seen.

        Files carrying several events are archived only after the last
        one, so a crash mid-file re-processes the file idempotently
        (duplicate event ids are ignored).
        """
        if self.dry_run or not path.exists():
            return
        if index + 1 < max(1, len_after):
            return
        try:
            day = local_day()
            target_dir = self.dirs["archive"] / day
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / path.name
            if target.exists():
                target = target_dir / f"{int(time.time())}-{path.name}"
            os.replace(path, target)
        except OSError as exc:
            self.log(
                "warn",
                f"could not archive {path.name}: {exc} "
                "(it stays in pending and is re-checked next sweep)",
            )

    # -- persistence ---------------------------------------------------
    def _drain_ready_records(self) -> None:
        """Persist attempts whose job context (and final state) is known.

        Written as soon as the attempt's job reached its final state, so
        the record carries the complete ``source -> job -> attempt``
        join; if the final job event never arrives the record is written
        after ``job_final_timeout_sec`` with explicit completeness flags
        (never dropped, never invented).
        """
        now = time.monotonic()
        for job_id, attempt_id in self.store.pending_attempts(
            only_ready=True
        ):
            job = self.store.jobs.get(job_id) or {}
            if not job.get("job_final_status"):
                ready_at = float(job.get("first_finished_at") or 0.0)
                if self.job_final_timeout_sec <= 0:
                    self.log(
                        "warn",
                        f"job {job_id}: persisting attempt {attempt_id} "
                        "without its job final event "
                        "(job_final_timeout_sec=0)",
                    )
                elif ready_at and (now - ready_at) < self.job_final_timeout_sec:
                    continue          # wait for job_succeeded/job_failed
                else:
                    self.log(
                        "warn",
                        f"job {job_id}: no final job event within "
                        f"{self.job_final_timeout_sec:.0f}s — persisting "
                        f"attempt {attempt_id} as flagged-incomplete",
                    )
            record = self.store.finalize_record(job_id, attempt_id)
            if record is not None:
                self._write_record(record)
        remaining = self.store.pending_attempts(only_ready=False)
        ready = {
            key for key in self.store.pending_attempts(only_ready=True)
            if (self.store.jobs.get(key[0]) or {}).get("job_final_status")
        }
        self.counters.events_buffered = max(0, len(remaining) - len(ready))

    def drain_buffered_records(self) -> int:
        """Persist every terminal attempt still missing its job context.

        Used at the END of a soak (``--drain-buffered``): the records
        are written with explicit ``completeness`` flags instead of
        being lost. Never invents the missing context.
        """
        written = 0
        for job_id, attempt_id in self.store.pending_attempts(
            only_ready=False
        ):
            record = self.store.finalize_record(job_id, attempt_id)
            if record is None:
                continue
            if self._write_record(record):
                written += 1
        return written

    def _write_record(self, record: dict[str, Any]) -> bool:
        attempt_id = str(record.get("attempt_id") or "")
        finished_id = str(record.get("attempt_finished_event_id") or "")
        if attempt_id and attempt_id in self.attempt_ids:
            self.counters.records_skipped_duplicate += 1
            self.log(
                "info",
                f"attempt {attempt_id} already persisted — skipping "
                "(idempotent)",
            )
            self._mark_persisted(record)
            return False
        if self.dry_run:
            self.counters.records_written += 1
            return True

        day = local_day(
            str(record.get("end_time") or record.get("start_time") or "")
        )
        path = self.dirs["samples"] / f"{day}.jsonl"
        try:
            offset = append_jsonl_durable(path, record)
        except OSError as exc:
            self.log(
                "error",
                f"could not persist attempt {attempt_id}: {exc} "
                "(the event stays pending and will be retried)",
            )
            return False

        self.offsets[path.name] = offset
        self.counters.records_written += 1
        if attempt_id:
            self.attempt_ids.add(attempt_id)
        if finished_id:
            self.seen.add(finished_id)
        self._mark_persisted(record)
        self.log(
            "info",
            f"persisted attempt {attempt_id} -> samples/{path.name} "
            f"({record.get('status')})",
        )
        self._write_daily_summary(day)
        return True

    def _mark_persisted(self, record: dict[str, Any]) -> None:
        job = self.store.jobs.get(str(record.get("job_id") or ""))
        if job is None:
            return
        entry = (job.get("attempts") or {}).get(
            str(record.get("attempt_id") or "")
        )
        if entry is not None:
            entry["persisted"] = True

    # -- summaries + manifest -----------------------------------------
    def _write_daily_summary(self, day: str) -> None:
        try:
            summary = rebuild_daily_summary(self.dirs["samples"], day)
        except Exception as exc:            # pragma: no cover - defensive
            self.log("warn", f"daily summary failed for {day}: {exc}")
            return
        try:
            atomic_write_json(
                self.dirs["summaries"] / f"{day}.json", summary
            )
        except OSError as exc:
            self.log("warn", f"could not write daily summary {day}: {exc}")

    def write_manifest(self) -> None:
        if self.dry_run:
            return
        existing = {}
        path = self.root / "manifest.json"
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

        samples = sorted(self.dirs["samples"].glob("*.jsonl"))
        records = sum(1 for p in samples for _ in read_jsonl(p))
        archive_count = sum(
            1 for p in self.dirs["archive"].rglob("*") if p.is_file()
        )
        failed_count = sum(
            1 for p in self.dirs["failed"].iterdir()
            if p.is_file() and p.suffix.lower() in (".json", ".jsonl")
        )
        manifest = {
            "manifest_version": "1.0",
            "collector": {
                "name": COLLECTOR_NAME,
                "version": COLLECTOR_VERSION,
                "schema_version": self.schema.event_schema_version,
                "record_version": RECORD_VERSION,
            },
            "root": str(self.root),
            "updated_at": utc_now_iso(),
            "created_at": existing.get("created_at") or utc_now_iso(),
            "dataset": {
                "sample_files": len(samples),
                "records": records,
                "first_day": samples[0].stem if samples else "",
                "last_day": samples[-1].stem if samples else "",
                "archive_events": archive_count,
                "failed_events": failed_count,
                "open_jobs_in_memory": len(self.store.jobs),
            },
            "last_sweep": {
                "at": utc_now_iso(),
                "counters": self.counters.as_dict(),
            },
            "totals": existing.get("totals", {}),
        }
        totals = manifest["totals"]
        for key, value in self.counters.as_dict().items():
            totals[key] = int(totals.get(key, 0)) + int(value)
        try:
            atomic_write_json(path, manifest)
        except OSError as exc:
            self.log("warn", f"could not write manifest: {exc}")

    # -- status --------------------------------------------------------
    def status(self) -> dict[str, Any]:
        samples = sorted(self.dirs["samples"].glob("*.jsonl"))
        records = sum(1 for p in samples for _ in read_jsonl(p))
        pending = len(self.pending_files())
        archive = sum(
            1 for p in self.dirs["archive"].rglob("*") if p.is_file()
        )
        failed = sum(
            1 for p in self.dirs["failed"].iterdir()
            if p.is_file() and p.suffix.lower() in (".json", ".jsonl")
        )
        succeeded = 0
        failed_attempts = 0
        for path in samples:
            for record in read_jsonl(path):
                if record.get("status") == "SUCCEEDED":
                    succeeded += 1
                elif record.get("status") == "FAILED":
                    failed_attempts += 1
        return {
            "collector": {
                "name": COLLECTOR_NAME,
                "version": COLLECTOR_VERSION,
                "schema_version": self.schema.event_schema_version,
                "schema_source": self.schema.source,
            },
            "root": str(self.root),
            "now": utc_now_iso(),
            "pending_events": pending,
            "archived_events": archive,
            "failed_event_files": failed,
            "dataset": {
                "sample_files": len(samples),
                "records": records,
                "succeeded_attempts": succeeded,
                "failed_attempts": failed_attempts,
                "first_day": samples[0].stem if samples else "",
                "last_day": samples[-1].stem if samples else "",
            },
            "open_jobs_in_memory": len(self.store.jobs),
        }


def _error_result(message: str) -> ValidationResult:
    result = ValidationResult()
    result.errors.append(message)
    return result


# ---------------------------------------------------------------------------
# daily summary (always rebuildable from raw JSONL)
# ---------------------------------------------------------------------------

def _blank_summary(day: str) -> dict[str, Any]:
    return {
        "summary_version": "1.0",
        "day": day,
        "generated_at": utc_now_iso(),
        "generator": f"{COLLECTOR_NAME} {COLLECTOR_VERSION}",
        "authoritative_source": f"samples/{day}.jsonl",
        "records": 0,
        "attempts": {"succeeded": 0, "failed": 0, "success_rate": None},
        "jobs": {
            "total": 0, "succeeded": 0, "failed": 0,
            "jobs_with_retry": 0, "retry_rate": None,
        },
        "failure_stages": {},
        "failure_reasons": {},
        "backends": {},
        "presets": {},
        "source_classes": {},
        "scaling": {},
        "output": {
            "bytes_total": 0, "bytes_mean": None,
            "compression_ratio_mean": None,
            "wall_time_sec_total": 0.0, "wall_time_sec_mean": None,
            "encode_fps_mean": None,
        },
        "completeness": {"complete": 0, "incomplete": 0, "missing_fields": {}},
        "data_quality": {"unknown_status_records": 0},
    }


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float))]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 6)


def rebuild_daily_summary(samples_dir: Path, day: str) -> dict[str, Any]:
    """Rebuild one daily summary from raw JSONL (never the other way)."""
    path = Path(samples_dir) / f"{day}.jsonl"
    summary = _blank_summary(day)
    jobs: dict[str, dict[str, Any]] = {}
    sizes: list[float] = []
    ratios: list[float] = []
    walls: list[float] = []
    fps_values: list[float] = []
    missing_counts: dict[str, int] = {}

    for record in read_jsonl(path):
        summary["records"] += 1
        status = str(record.get("status") or "")
        if status == "SUCCEEDED":
            summary["attempts"]["succeeded"] += 1
        elif status == "FAILED":
            summary["attempts"]["failed"] += 1
        else:
            summary["data_quality"]["unknown_status_records"] += 1

        job_id = str(record.get("job_id") or "")
        job = jobs.setdefault(job_id, {"attempts": 0, "succeeded": 0})
        job["attempts"] += 1
        if status == "SUCCEEDED":
            job["succeeded"] += 1

        stage = str(record.get("failure_stage") or "")
        if stage:
            summary["failure_stages"][stage] = (
                summary["failure_stages"].get(stage, 0) + 1
            )
            reason = str(record.get("failure_reason") or "")[:120]
            if reason:
                key = f"{stage}: {reason}"
                summary["failure_reasons"][key] = (
                    summary["failure_reasons"].get(key, 0) + 1
                )

        backend = str(record.get("backend") or "unknown")
        summary["backends"][backend] = summary["backends"].get(backend, 0) + 1
        preset = str(record.get("preset") or "unknown")
        summary["presets"][preset] = summary["presets"].get(preset, 0) + 1

        classification = record.get("classification") or {}
        source_class = str(classification.get("source_class") or "unknown")
        summary["source_classes"][source_class] = (
            summary["source_classes"].get(source_class, 0) + 1
        )

        context = record.get("scaling_context") or {}
        params = record.get("effective_params") or {}
        for key in (
            "spatial_factor", "temporal_factor", "pixel_rate_factor",
        ):
            bucket = summary["scaling"].setdefault(
                key, {"n": 0, "sum": 0.0}
            )
            value = context.get(key)
            if isinstance(value, (int, float)):
                bucket["n"] += 1
                bucket["sum"] += float(value)
        for key in ("merange", "rc-lookahead", "gop-lookahead", "min-keyint",
                    "vbv-maxrate", "vbv-bufsize"):
            value = params.get(key)
            if value is None:
                continue
            bucket = summary["scaling"].setdefault(key, {})
            bucket[str(value)] = bucket.get(str(value), 0) + 1

        size = record.get("output_size")
        if isinstance(size, (int, float)) and size:
            sizes.append(float(size))
            summary["output"]["bytes_total"] += int(size)
        ratio = record.get("compression_ratio")
        if isinstance(ratio, (int, float)):
            ratios.append(float(ratio))
        wall = record.get("wall_time_sec")
        if isinstance(wall, (int, float)):
            walls.append(float(wall))
            summary["output"]["wall_time_sec_total"] = round(
                summary["output"]["wall_time_sec_total"] + float(wall), 6
            )
        fps_value = record.get("encode_fps")
        if isinstance(fps_value, (int, float)) and fps_value:
            fps_values.append(float(fps_value))

        completeness = record.get("completeness") or {}
        if completeness.get("complete"):
            summary["completeness"]["complete"] += 1
        else:
            summary["completeness"]["incomplete"] += 1
            for field in completeness.get("missing") or []:
                missing_counts[str(field)] = missing_counts.get(
                    str(field), 0
                ) + 1

    summary["jobs"]["total"] = len(jobs)
    summary["jobs"]["succeeded"] = sum(
        1 for job in jobs.values() if job["succeeded"] > 0
    )
    summary["jobs"]["failed"] = sum(
        1 for job in jobs.values() if job["succeeded"] == 0
    )
    summary["jobs"]["jobs_with_retry"] = sum(
        1 for job in jobs.values() if job["attempts"] > 1
    )
    attempts_total = (
        summary["attempts"]["succeeded"] + summary["attempts"]["failed"]
    )
    if attempts_total:
        summary["attempts"]["success_rate"] = round(
            summary["attempts"]["succeeded"] / attempts_total, 6
        )
    if summary["jobs"]["total"]:
        summary["jobs"]["retry_rate"] = round(
            summary["jobs"]["jobs_with_retry"] / summary["jobs"]["total"], 6
        )
    if sizes:
        summary["output"]["bytes_mean"] = round(
            sum(sizes) / len(sizes), 3
        )
    if ratios:
        summary["output"]["compression_ratio_mean"] = round(
            sum(ratios) / len(ratios), 9
        )
    if walls:
        summary["output"]["wall_time_sec_mean"] = round(
            sum(walls) / len(walls), 6
        )
    summary["output"]["encode_fps_mean"] = _mean(fps_values)
    summary["completeness"]["missing_fields"] = dict(
        sorted(missing_counts.items(), key=lambda kv: -kv[1])
    )
    for key, bucket in list(summary["scaling"].items()):
        if isinstance(bucket, dict) and "n" in bucket and "sum" in bucket:
            summary["scaling"][key] = (
                round(bucket["sum"] / bucket["n"], 9)
                if bucket["n"] else None
            )
    summary["failure_reasons"] = dict(
        sorted(
            summary["failure_reasons"].items(),
            key=lambda kv: -kv[1],
        )[:25]
    )
    return summary


def rebuild_all_summaries(samples_dir: Path, summaries_dir: Path) -> list[str]:
    """Rebuild every daily summary from raw JSONL. Returns the days."""
    days: list[str] = []
    for path in sorted(Path(samples_dir).glob("*.jsonl")):
        day = path.stem
        summary = rebuild_daily_summary(samples_dir, day)
        atomic_write_json(Path(summaries_dir) / f"{day}.json", summary)
        days.append(day)
    return days


# ---------------------------------------------------------------------------
# dataset validation (--validate)
# ---------------------------------------------------------------------------

def validate_dataset(samples_dir: Path, schema: Schema) -> dict[str, Any]:
    """Re-validate the persisted dataset (read-only).

    Checks JSONL integrity, record shape, id uniqueness and the
    source -> job -> attempt -> output join. Never repairs anything.
    """
    report: dict[str, Any] = {
        "dataset": str(samples_dir),
        "validated_at": utc_now_iso(),
        "collector_version": COLLECTOR_VERSION,
        "files": [],
        "counts": {
            "files": 0, "lines": 0, "records": 0,
            "succeeded": 0, "failed": 0, "unknown_status": 0,
            "complete": 0, "incomplete": 0,
        },
        "errors": [],
        "warnings": [],
        "attempt_ids_seen": 0,
        "jobs_seen": 0,
        "duplicate_attempt_ids": [],
        "duplicate_event_ids": [],
        "orphan_attempts": [],
        "jobs_without_success": [],
        "incomplete_examples": [],
    }
    seen_attempts: dict[str, str] = {}
    seen_events: dict[str, str] = {}
    jobs: dict[str, dict[str, Any]] = {}

    for path in sorted(Path(samples_dir).glob("*.jsonl")):
        report["counts"]["files"] += 1
        file_info = {"name": path.name, "lines": 0, "records": 0,
                     "errors": [], "partial_tail": False}
        try:
            raw = path.read_bytes()
        except OSError as exc:
            report["errors"].append(f"{path.name}: unreadable ({exc})")
            continue
        if raw and not raw.endswith(b"\n"):
            file_info["partial_tail"] = True
            report["warnings"].append(
                f"{path.name}: last line is missing its newline "
                "(interrupted write?)"
            )
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for number, line in enumerate(fh, start=1):
                file_info["lines"] += 1
                report["counts"]["lines"] += 1
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError as exc:
                    file_info["errors"].append(f"line {number}: invalid JSON")
                    report["errors"].append(
                        f"{path.name}:{number}: invalid JSON ({exc})"
                    )
                    continue
                if not isinstance(record, dict):
                    report["errors"].append(
                        f"{path.name}:{number}: not a JSON object"
                    )
                    continue
                file_info["records"] += 1
                report["counts"]["records"] += 1

                status = str(record.get("status") or "")
                if status == "SUCCEEDED":
                    report["counts"]["succeeded"] += 1
                elif status == "FAILED":
                    report["counts"]["failed"] += 1
                else:
                    report["counts"]["unknown_status"] += 1
                    report["errors"].append(
                        f"{path.name}:{number}: unknown status {status!r}"
                    )

                completeness = record.get("completeness") or {}
                if completeness.get("complete"):
                    report["counts"]["complete"] += 1
                else:
                    report["counts"]["incomplete"] += 1
                    if len(report["incomplete_examples"]) < 20:
                        report["incomplete_examples"].append({
                            "attempt_id": record.get("attempt_id", ""),
                            "missing": completeness.get("missing", []),
                        })

                attempt_id = str(record.get("attempt_id") or "")
                job_id = str(record.get("job_id") or "")
                if not attempt_id:
                    report["errors"].append(
                        f"{path.name}:{number}: record without attempt_id"
                    )
                elif attempt_id in seen_attempts:
                    report["duplicate_attempt_ids"].append({
                        "attempt_id": attempt_id,
                        "first": seen_attempts[attempt_id],
                        "again": f"{path.name}:{number}",
                    })
                else:
                    seen_attempts[attempt_id] = f"{path.name}:{number}"

                for key in (
                    "attempt_finished_event_id", "attempt_started_event_id",
                    "source_event_id",
                ):
                    value = str(record.get(key) or "")
                    if not value:
                        continue
                    if value in seen_events and seen_events[value] != attempt_id:
                        report["duplicate_event_ids"].append({
                            "event_id": value,
                            "attempt_a": seen_events[value],
                            "attempt_b": attempt_id,
                        })
                    else:
                        seen_events[value] = attempt_id

                job = jobs.setdefault(job_id, {
                    "job_id": job_id, "attempts": 0, "succeeded": 0,
                    "failed": 0, "source_path": record.get("source_path", ""),
                })
                job["attempts"] += 1
                job["succeeded" if status == "SUCCEEDED" else "failed"] += 1

                if status == "FAILED":
                    if not record.get("failure_stage"):
                        report["errors"].append(
                            f"{path.name}:{number}: failed record without "
                            "failure_stage"
                        )
                    if record.get("attempt_started_seen") is False:
                        report["orphan_attempts"].append(attempt_id)

        report["files"].append(file_info)

    report["attempt_ids_seen"] = len(seen_attempts)
    report["jobs_seen"] = len(jobs)
    report["jobs_without_success"] = sorted(
        job["job_id"] for job in jobs.values() if job["succeeded"] == 0
    )[:200]
    report["ok"] = not report["errors"]
    return report
