"""x265 calibration collector — command line entry point.

Headless, offline, standalone. Never touches the network, never starts
ffmpeg/x265, never modifies production parameters.

    x265-calibration-collector --headless --watch  <pending-or-root>
    x265-calibration-collector --headless --validate <dataset>
    x265-calibration-collector --version
    x265-calibration-collector --headless --once
    x265-calibration-collector --headless --status
    x265-calibration-collector --headless --rebuild-summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from . import collector_core as core

DEFAULT_CONFIG_NAME = "collector.toml"


# ---------------------------------------------------------------------------
# output helpers (encoding-safe on any Windows console/codepage)
# ---------------------------------------------------------------------------

def emit_line(text: str) -> None:
    try:
        sys.stdout.write(str(text) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeEncodeError):
        try:
            sys.stdout.write(
                str(text).encode("ascii", "replace").decode("ascii") + "\n"
            )
            sys.stdout.flush()
        except OSError:
            pass


def emit_json(payload: Any) -> None:
    emit_line(json.dumps(payload, ensure_ascii=False, indent=2,
                         sort_keys=True))


# ---------------------------------------------------------------------------
# minimal TOML reader (no third-party dependency, offline-only config)
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict[str, Any]:
    """Read a flat/sectioned TOML file well enough for collector.toml.

    Only ``key = value`` lines, ``[section]`` headers, booleans, numbers,
    quoted strings, ``#`` comments and single-line arrays are needed —
    PyInstaller must not require a TOML parser beyond the standard
    library (``tomllib`` exists on 3.11+, used when available).
    """
    try:
        import tomllib                       # Python 3.11+
    except ImportError:
        tomllib = None                       # type: ignore[assignment]

    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if tomllib is not None:
        try:
            return dict(tomllib.loads(raw.decode("utf-8")))
        except Exception:
            pass

    config: dict[str, Any] = {}
    section: dict[str, Any] = config
    for line in raw.decode("utf-8", "replace").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            name = text[1:-1].strip()
            section = config.setdefault(name, {})
            continue
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip().strip('"').strip("'")
        value = value.strip()
        section[key] = _parse_scalar(value)
    return config


def _parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

class Logger:
    """Console + errors/collector.log, daily-line capped, never fatal."""

    def __init__(self, log_path: Path, verbose: bool = False,
                 quiet: bool = False) -> None:
        self.log_path = log_path
        self.verbose = verbose
        self.quiet = quiet
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def __call__(self, level: str, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} | {level.upper():5s} | {message}"
        if level in ("error", "warn") or self.verbose:
            if not self.quiet or level == "error":
                emit_line(line)
        try:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# path resolution: --watch/--status may point at the root or at pending/
# ---------------------------------------------------------------------------

def resolve_root(target: Path) -> tuple[Path, str]:
    value = Path(target)
    if not value.is_absolute():
        value = (Path.cwd() / value).resolve()
    parts = [p.lower() for p in value.parts]
    if value.name.lower() == "pending" and "events" in parts:
        return value.parent.parent, "events/pending"
    if value.name.lower() == "events":
        return value.parent, "events"
    if value.name.lower() == "samples":
        return value.parent, "samples"
    return value, "root"


def default_spool(script_dir: Path) -> Path:
    """Where calibration data lives when the operator names nothing.

    The production side defaults to the SAME location when it runs with
    ``ONEXKT_CALIBRATION_SPOOL`` unset, so no configuration is required
    on the soak host.
    """
    for candidate in (Path.cwd(), script_dir):
        probe = candidate / "calibration"
        if probe.is_dir():
            return probe.resolve()
    return (Path.cwd() / "calibration").resolve()


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_watch(args: argparse.Namespace, root: Path, log: Logger) -> int:
    lock = core.SpoolLock(root)
    ok, why = lock.acquire()
    if not ok:
        log("error", f"refusing to start: {why}")
        return core.EXIT_LOCKED

    collector = core.Collector(
        root,
        schema=args.schema_obj,
        poll_interval=args.poll_interval,
        strict=args.strict,
        log=log,
        dry_run=args.dry_run,
        job_final_timeout_sec=args.job_final_timeout,
    )
    log("info", f"{core.COLLECTOR_NAME} {core.COLLECTOR_VERSION} starting")
    log("info", f"calibration root : {root}")
    log("info", f"pending dir      : {collector.dirs['pending']}")
    log("info", f"samples dir      : {collector.dirs['samples']}")
    log("info", f"schema           : {args.schema_obj.source}")
    log("info", f"poll interval    : {args.poll_interval}s")

    try:
        collector.load_state()
    except Exception as exc:
        log("error", f"state recovery failed: {exc}")
        log("error", traceback.format_exc())
        lock.release()
        return core.EXIT_ERROR

    if args.dry_run:
        log("warn", "dry-run: nothing will be persisted or archived")

    sweeps = 0
    try:
        while True:
            sweeps += 1
            try:
                counters = collector.sweep()
            except Exception as exc:
                # A Collector-side failure is contained here: pending
                # events stay pending and are retried next sweep.
                log("error", f"sweep failed: {type(exc).__name__}: {exc}")
                log("error", traceback.format_exc())
                counters = collector.counters
            if counters.events_seen or args.verbose:
                log(
                    "info",
                    "sweep %d | seen=%d valid=%d invalid=%d dup=%d "
                    "records=%d" % (
                        sweeps, counters.events_seen, counters.events_valid,
                        counters.events_invalid, counters.events_duplicate,
                        counters.records_written,
                    ),
                )
            if args.once:
                log("info", "--once: single sweep done, exiting")
                break
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        log("info", "interrupted: shutting down cleanly")
    finally:
        try:
            collector.save_state()
            collector.write_manifest()
        except Exception as exc:
            log("warn", f"final state write failed: {exc}")
        lock.release()
        log("info", "collector stopped")
    return core.EXIT_OK


def cmd_validate(args: argparse.Namespace, target: Path, log: Logger) -> int:
    dataset = Path(target)
    if dataset.is_dir() and (dataset / "samples").is_dir():
        dataset = dataset / "samples"
    if not dataset.is_dir():
        emit_line(f"[FAIL] dataset directory not found: {dataset}")
        return core.EXIT_USAGE
    report = core.validate_dataset(dataset, args.schema_obj)
    if args.json_out:
        core.atomic_write_json(Path(args.json_out), report)
    emit_json(report)
    counts = report["counts"]
    emit_line(
        "[%s] files=%d records=%d succeeded=%d failed=%d complete=%d "
        "incomplete=%d errors=%d" % (
            "OK" if report["ok"] else "INVALID",
            counts["files"], counts["records"], counts["succeeded"],
            counts["failed"], counts["complete"], counts["incomplete"],
            len(report["errors"]),
        )
    )
    return core.EXIT_OK if report["ok"] else core.EXIT_DATASET_INVALID


def cmd_status(args: argparse.Namespace, root: Path, log: Logger) -> int:
    collector = core.Collector(
        root, schema=args.schema_obj, log=log, dry_run=True
    )
    collector.load_state()
    emit_json(collector.status())
    return core.EXIT_OK


def cmd_rebuild_summary(
    args: argparse.Namespace, root: Path, log: Logger
) -> int:
    collector = core.Collector(
        root, schema=args.schema_obj, log=log, dry_run=args.dry_run
    )
    days = core.rebuild_all_summaries(
        collector.dirs["samples"], collector.dirs["summaries"]
    )
    emit_json({
        "rebuilt": len(days),
        "days": days,
        "summaries_dir": str(collector.dirs["summaries"]),
        "authoritative_source": str(collector.dirs["samples"]),
    })
    return core.EXIT_OK


def cmd_drain(args: argparse.Namespace, root: Path, log: Logger) -> int:
    collector = core.Collector(
        root, schema=args.schema_obj, log=log, dry_run=args.dry_run
    )
    collector.load_state()
    written = collector.drain_buffered_records()
    collector.save_state()
    collector.write_manifest()
    emit_json({
        "drained_records": written,
        "note": (
            "terminal attempts whose job context was never seen were "
            "persisted with explicit completeness flags"
        ),
    })
    return core.EXIT_OK


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=core.COLLECTOR_NAME,
        description=(
            "Offline x265 calibration data collector: observes the "
            "production event spool and persists attempt-level "
            "calibration records. Never encodes, never retries, never "
            "changes production behaviour, never uses the network."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  x265-calibration-collector --headless --watch "
            "D:\\1kt\\calibration\n"
            "  x265-calibration-collector --headless --validate "
            "D:\\1kt\\calibration\n"
            "  x265-calibration-collector --version\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version=version_line())
    parser.add_argument(
        "--headless", action="store_true",
        help="Never prompt, never open a window (required for deployment).",
    )
    parser.add_argument(
        "--watch", metavar="PATH",
        help="Watch mode: calibration root, events/ or events/pending/.",
    )
    parser.add_argument(
        "--validate", metavar="PATH",
        help="Validate a collected dataset (samples dir or calibration root).",
    )
    parser.add_argument("--once", action="store_true",
                        help="Watch mode: process the current backlog once, "
                             "then exit.")
    parser.add_argument("--status", action="store_true",
                        help="Print one JSON status object and exit.")
    parser.add_argument("--rebuild-summary", action="store_true",
                        help="Rebuild every daily summary from raw JSONL.")
    parser.add_argument("--drain-buffered", action="store_true",
                        help="End-of-soak: persist terminal attempts whose "
                             "job context was never seen (flagged).")
    parser.add_argument("--spool", metavar="PATH",
                        help="Calibration root when --watch/--status is not "
                             "given (or to override collector.toml).")
    parser.add_argument("--config", metavar="PATH",
                        help=f"Config file (default: {DEFAULT_CONFIG_NAME} "
                             "next to the executable).")
    parser.add_argument("--schema", metavar="PATH",
                        help="schema.json to validate against.")
    parser.add_argument("--poll-interval", type=float, default=None,
                        help="Seconds between pending scans (default 2.0).")
    parser.add_argument("--job-final-timeout", type=float, default=None,
                        help="Seconds to wait for a job's final event before "
                             "persisting its attempts as flagged-incomplete "
                             "(default 900; 0 = do not wait).")
    parser.add_argument("--strict", action="store_true",
                        help="Treat schema warnings (unknown fields) as "
                             "validation errors.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report without writing anything.")
    parser.add_argument("--verbose", action="store_true",
                        help="Log every sweep.")
    parser.add_argument("--quiet", action="store_true",
                        help="Console errors only (the log file still gets "
                             "everything).")
    parser.add_argument("--json-out", metavar="PATH",
                        help="--validate: also write the report to this file.")
    return parser


def version_line() -> str:
    return (
        f"{core.COLLECTOR_NAME} {core.COLLECTOR_VERSION}\n"
        f"event schema {core.COLLECTOR_SCHEMA_VERSION} | "
        f"record version {core.RECORD_VERSION}\n"
        f"python {sys.version.split()[0]} | offline: no network used | "
        f"frozen: {bool(getattr(sys, 'frozen', False))}"
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    exe_dir = Path(sys.executable).resolve().parent
    script_dir = Path(__file__).resolve().parent.parent

    # --- config -------------------------------------------------------
    config: dict[str, Any] = {}
    config_path: Path | None = None
    candidates = []
    if args.config:
        candidates.append(Path(args.config))
    else:
        candidates.append(Path.cwd() / DEFAULT_CONFIG_NAME)
        candidates.append(exe_dir / DEFAULT_CONFIG_NAME)
        candidates.append(script_dir / DEFAULT_CONFIG_NAME)
    for candidate in candidates:
        if candidate.is_file():
            config = load_config(candidate)
            config_path = candidate
            break
    section = config.get("collector", {}) if isinstance(config, dict) else {}

    # --- schema -------------------------------------------------------
    schema_path = Path(args.schema) if args.schema else None
    if schema_path is None:
        cfg_schema = section.get("schema") or config.get("schema")
        if cfg_schema:
            candidate = Path(str(cfg_schema))
            if not candidate.is_absolute():
                base = config_path.parent if config_path else Path.cwd()
                candidate = base / candidate
            if candidate.is_file():
                schema_path = candidate
    try:
        args.schema_obj = core.load_schema(schema_path)
    except RuntimeError as exc:
        emit_line(f"[FATAL] {exc}")
        return core.EXIT_ERROR

    # --- spool root ---------------------------------------------------
    if args.spool:
        root = Path(args.spool)
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
    elif args.watch:
        root, _kind = resolve_root(Path(args.watch))
    elif args.validate:
        root, _kind = resolve_root(Path(args.validate))
    elif args.status or args.rebuild_summary or args.drain_buffered:
        cfg_root = section.get("root") or config.get("root")
        if cfg_root:
            root = Path(str(cfg_root))
            if not root.is_absolute():
                base = config_path.parent if config_path else Path.cwd()
                root = (base / str(cfg_root)).resolve()
        else:
            root = default_spool(script_dir)
    else:
        parser.print_help()
        emit_line("")
        emit_line("[FATAL] nothing to do: pass --watch, --validate, "
                  "--status, --rebuild-summary or --drain-buffered")
        return core.EXIT_USAGE

    if args.poll_interval is None:
        args.poll_interval = float(
            section.get("poll_interval_sec")
            or config.get("poll_interval_sec")
            or 2.0
        )
    if not args.strict:
        args.strict = bool(section.get("strict") or config.get("strict"))
    if args.job_final_timeout is None:
        args.job_final_timeout = float(
            section.get("job_final_timeout_sec")
            or config.get("job_final_timeout_sec")
            or 900.0
        )

    log_path = root / "errors" / "collector.log"
    log = Logger(log_path, verbose=bool(args.verbose),
                 quiet=bool(args.quiet))

    try:
        if args.validate:
            return cmd_validate(args, Path(args.validate), log)
        if args.status:
            return cmd_status(args, root, log)
        if args.rebuild_summary:
            return cmd_rebuild_summary(args, root, log)
        if args.drain_buffered:
            return cmd_drain(args, root, log)
        if args.watch:
            return cmd_watch(args, root, log)
        parser.print_help()
        return core.EXIT_USAGE
    except KeyboardInterrupt:
        log("warn", "interrupted")
        return core.EXIT_OK
    except Exception as exc:
        log("error", f"fatal: {type(exc).__name__}: {exc}")
        log("error", traceback.format_exc())
        emit_line(f"[FATAL] {type(exc).__name__}: {exc}")
        return core.EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
