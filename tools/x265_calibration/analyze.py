#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x265 calibration dataset analyzer (DEVELOPMENT-SIDE tool, offline).

Reads a collected calibration dataset and reports what a future
calibration session needs to re-fit: source properties, scaling
factors, resolved parameter behaviour, and production outcomes.

    python analyze.py <calibration-root-or-samples-dir> [--json out.json]
    python analyze.py <dataset> --dataset-summary
    python analyze.py <dataset> --group-by-param merange

Strict rules (v0.7.0 task §32):

* This tool NEVER writes x265.json or x265_scaling.json, never edits
  any configuration, and never touches the production transcoder.
* It is NOT part of the long-running soak host: it runs on the
  development machine after the collection month is over.
* Standard library only, offline, read-only over the dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ANALYZER_NAME = "x265-analyze"
ANALYZER_VERSION = "1.0.0"
RECORD_VERSION = 1.0

FAILURE_STAGES = (
    "startup_failed", "encode_failed", "verification_failed",
    "output_commit_failed", "probe_failed", "unknown",
)

SCALING_PARAMS = (
    "merange", "rc-lookahead", "gop-lookahead", "min-keyint", "keyint",
    "vbv-maxrate", "vbv-bufsize", "bframes", "ref",
)

# Values the producer may report instead of a number when a parameter is
# structurally absent (hardware backends have no scaling context).
NOT_APPLICABLE_MARKERS = ("not_applicable",)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def resolve_samples_dir(target: Path) -> Path:
    target = Path(target)
    if (target / "samples").is_dir():
        return target / "samples"
    if target.is_dir() and target.name.lower() == "samples":
        return target
    return target


def iter_records(samples_dir: Path) -> Iterator[dict[str, Any]]:
    for path in sorted(samples_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        yield record
        except OSError:
            continue


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _param(record: dict[str, Any], name: str) -> float | None:
    params = record.get("effective_params") or {}
    if not isinstance(params, dict):
        return None
    return _num(params.get(name))


def describe(values: list[float]) -> dict[str, Any]:
    clean = [v for v in values if isinstance(v, (int, float))
             and not math.isnan(v)]
    if not clean:
        return {"n": 0}
    clean_sorted = sorted(clean)
    out: dict[str, Any] = {
        "n": len(clean),
        "min": round(clean_sorted[0], 6),
        "max": round(clean_sorted[-1], 6),
        "mean": round(statistics.fmean(clean), 6),
        "median": round(statistics.median(clean), 6),
        "stdev": (
            round(statistics.pstdev(clean), 6) if len(clean) > 1 else 0.0
        ),
        "p10": round(_quantile(clean_sorted, 0.10), 6),
        "p90": round(_quantile(clean_sorted, 0.90), 6),
    }
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return float(
        sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
    )


def bucket(value: float | None, edges: list[float]) -> str:
    if value is None:
        return "unknown"
    for edge in edges:
        if value < edge:
            return f"<{edge:g}"
    return f">={edges[-1]:g}"


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common()}


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

def analyze(samples_dir: Path) -> dict[str, Any]:
    records = list(iter_records(samples_dir))
    report: dict[str, Any] = {
        "analyzer": "x265 calibration analyzer",
        "analyzer_version": ANALYZER_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S."
        ) + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
        "dataset": {
            "samples_dir": str(samples_dir),
            "files": sorted(p.name for p in samples_dir.glob("*.jsonl")),
            "records": len(records),
        },
        "scope_note": (
            "Read-only analysis. This tool never writes x265.json or "
            "x265_scaling.json and never feeds values back into production."
        ),
        "provenance": {},
        "attempts": {},
        "jobs": {},
        "sources": {},
        "scaling": {},
        "parameters": {},
        "outputs": {},
        "data_quality": {},
    }
    if not records:
        return report

    # ---------------- provenance -------------------------------------
    project_versions = Counter(str(r.get("project_version") or "") for r in records)
    backends = Counter(str(r.get("backend") or "") for r in records)
    presets = Counter(str(r.get("preset") or "") for r in records)
    hosts = Counter(str(r.get("host") or "") for r in records)
    ffmpeg_versions = Counter(str(r.get("ffmpeg_version") or "") for r in records)
    x265_versions = Counter(str(r.get("libx265_version") or "") for r in records)
    report["provenance"] = {
        "project_versions": _counter_dict(project_versions),
        "backends": _counter_dict(backends),
        "presets": _counter_dict(presets),
        "hosts": _counter_dict(hosts),
        "ffmpeg_versions": _counter_dict(ffmpeg_versions),
        "libx265_versions": _counter_dict(x265_versions),
        "single_version_run": (
            len([v for v in project_versions if v]) <= 1
            and len([v for v in ffmpeg_versions if v]) <= 1
            and len([v for v in x265_versions if v]) <= 1
        ),
    }

    # ---------------- attempts ---------------------------------------
    succeeded = [r for r in records if r.get("status") == "SUCCEEDED"]
    failed = [r for r in records if r.get("status") == "FAILED"]
    unknown = [r for r in records if r.get("status") not in
               ("SUCCEEDED", "FAILED")]
    total = len(records)
    report["attempts"] = {
        "total": total,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "unknown_status": len(unknown),
        "success_rate": round(len(succeeded) / total, 6) if total else None,
        "failure_rate": round(len(failed) / total, 6) if total else None,
        "by_backend": _counter_dict(
            Counter(str(r.get("backend") or "") for r in records)
        ),
        "by_attempt_kind": _counter_dict(
            Counter(str(r.get("attempt_kind") or "") for r in records)
        ),
        "failure_stages": _counter_dict(
            Counter(
                str(r.get("failure_stage") or "")
                for r in failed if r.get("failure_stage")
            )
        ),
        "failure_reasons_top": _counter_dict(
            Counter(
                str(r.get("failure_reason") or "")[:120]
                for r in failed if r.get("failure_reason")
            )
        ),
        "verification_status": _counter_dict(
            Counter(str(r.get("verification_status") or "") for r in records)
        ),
    }

    # ---------------- jobs -------------------------------------------
    jobs: dict[str, dict[str, Any]] = {}
    for record in records:
        job_id = str(record.get("job_id") or "")
        job = jobs.setdefault(job_id, {
            "job_id": job_id,
            "attempts": 0,
            "succeeded": 0,
            "failed": 0,
            "final_status": str(record.get("job_final_status") or ""),
            "source_path": record.get("source_path", ""),
            "backends": set(),
            "kinds": Counter(),
            "start_time": record.get("start_time", ""),
        })
        job["attempts"] += 1
        if record.get("status") == "SUCCEEDED":
            job["succeeded"] += 1
        elif record.get("status") == "FAILED":
            job["failed"] += 1
        job["backends"].add(str(record.get("backend") or ""))
        job["kinds"][str(record.get("attempt_kind") or "")] += 1

    retried = [j for j in jobs.values() if j["attempts"] > 1]
    fallback_jobs = [
        j for j in jobs.values()
        if any(k in j["kinds"] for k in
               ("downgrade", "reader_fallback", "hardware_decode_fallback"))
    ]
    success_after_retry = [
        j for j in retried if j["succeeded"] > 0 and j["failed"] > 0
    ]
    report["jobs"] = {
        "total": len(jobs),
        "succeeded": sum(1 for j in jobs.values() if j["succeeded"] > 0),
        "failed": sum(1 for j in jobs.values() if j["succeeded"] == 0),
        "with_retry": len(retried),
        "retry_rate": round(len(retried) / len(jobs), 6) if jobs else None,
        "with_fallback": len(fallback_jobs),
        "fallback_rate": (
            round(len(fallback_jobs) / len(jobs), 6) if jobs else None
        ),
        "recovered_after_failure": len(success_after_retry),
        "recovery_rate": (
            round(len(success_after_retry) / len(retried), 6)
            if retried else None
        ),
        "max_attempts_per_job": max(
            (j["attempts"] for j in jobs.values()), default=0
        ),
    }

    # ---------------- sources ----------------------------------------
    def source_of(record: dict[str, Any]) -> dict[str, Any]:
        return record.get("source_info") or {}

    widths = [source_of(r).get("width") for r in records]
    heights = [source_of(r).get("height") for r in records]
    fps_values = [
        _num(source_of(r).get("fps")) for r in records
    ]
    bitrates = [_num(source_of(r).get("ob_kbps")) for r in records]
    durations = [_num(source_of(r).get("duration_sec")) for r in records]
    sizes = [_num(source_of(r).get("size_bytes")) for r in records]

    report["sources"] = {
        "unique_sources": len({
            str(r.get("source_path") or "") for r in records
        }),
        "resolution": _counter_dict(Counter(
            f"{w}x{h}" for w, h in zip(widths, heights) if w and h
        )),
        "width": describe([float(w) for w in widths if w]),
        "height": describe([float(h) for h in heights if h]),
        "fps": describe([f for f in fps_values if f]),
        "fps_buckets": _counter_dict(Counter(
            bucket(f, [24.5, 25.5, 30.5, 50.5, 60.5, 120.5])
            for f in fps_values
        )),
        "codec": _counter_dict(Counter(
            str(source_of(r).get("codec") or "") for r in records
        )),
        "profile": _counter_dict(Counter(
            str(source_of(r).get("profile") or "") for r in records
        )),
        "pix_fmt": _counter_dict(Counter(
            str(source_of(r).get("pix_fmt") or "") for r in records
        )),
        "bit_depth": _counter_dict(Counter(
            str(source_of(r).get("bit_depth") or "") for r in records
        )),
        "chroma": _counter_dict(Counter(
            str(source_of(r).get("chroma") or "") for r in records
        )),
        "container_bitrate_kbps": describe(
            [b for b in bitrates if b]
        ),
        "duration_sec": describe([d for d in durations if d]),
        "source_size_bytes": describe([s for s in sizes if s]),
        "source_class": _counter_dict(Counter(
            str((r.get("classification") or {}).get("source_class") or "")
            for r in records
        )),
        "normalized_ob": describe([
            n for n in (
                _num((r.get("classification") or {}).get("normalized_ob"))
                for r in records
            ) if n is not None
        ]),
        "normalized_ob_buckets": _counter_dict(Counter(
            bucket(
                _num((r.get("classification") or {}).get("normalized_ob")),
                [0.04, 0.08, 0.12, 0.16, 0.25, 0.40, 0.80],
            )
            for r in records
        )),
        "source_class_x_preset": _counter_dict(Counter(
            f"{str((r.get('classification') or {}).get('source_class') or '?')}"
            f"/{str(r.get('preset') or '?')}"
            for r in records
        )),
    }

    # ---------------- scaling ----------------------------------------
    contexts = [r.get("scaling_context") or {} for r in records]
    spatial = [_num(c.get("spatial_factor")) for c in contexts]
    temporal = [_num(c.get("temporal_factor")) for c in contexts]
    pixel_rate = [_num(c.get("pixel_rate_factor")) for c in contexts]
    applicable = [
        c for c in contexts
        if not any(
            str(c.get("status") or "").startswith(m)
            for m in NOT_APPLICABLE_MARKERS
        )
    ]
    reference = Counter(
        f"{c.get('reference_width')}x{c.get('reference_height')}"
        f"@{c.get('reference_fps')}"
        for c in applicable if c.get("reference_width")
    )
    report["scaling"] = {
        "records_with_context": len(applicable),
        "records_marked_not_applicable": len(contexts) - len(applicable),
        "reference": _counter_dict(reference),
        "spatial_factor": describe([s for s in spatial if s is not None]),
        "temporal_factor": describe([t for t in temporal if t is not None]),
        "pixel_rate_factor": describe(
            [p for p in pixel_rate if p is not None]
        ),
        "aspect_ratio_note": (
            "aspect_ratio is diagnostic-only in this phase and is never "
            "used as a multiplier"
        ),
        "spatial_factor_buckets": _counter_dict(Counter(
            bucket(s, [0.25, 0.5, 0.75, 1.0, 1.5, 2.0])
            for s in spatial
        )),
        "pixel_rate_factor_buckets": _counter_dict(Counter(
            bucket(p, [0.1, 0.25, 0.5, 1.0, 2.0, 4.0])
            for p in pixel_rate
        )),
    }

    # ---------------- parameters -------------------------------------
    params: dict[str, Any] = {}
    for name in SCALING_PARAMS:
        present: list[float] = []
        excluded = 0
        for record in records:
            raw = (record.get("effective_params") or {}).get(name)
            value = _num(raw)
            if value is None:
                if raw not in (None, ""):
                    excluded += 1
                continue
            present.append(value)
        params[name] = {
            "reported": len(present),
            "non_numeric_markers": excluded,
            "distribution": describe(present),
            "distinct_values": _counter_dict(Counter(
                f"{v:g}" for v in present
            )) if len(set(present)) <= 25 else {
                "note": f"{len(set(present))} distinct values",
                "min": min(present) if present else None,
                "max": max(present) if present else None,
            },
        }
        # behaviour of each parameter vs the source it was applied to
        by_resolution: dict[str, list[float]] = defaultdict(list)
        for record in records:
            value = _param(record, name)
            if value is None:
                continue
            info = source_of(record)
            key = f"{info.get('width')}x{info.get('height')}"
            by_resolution[key].append(value)
        params[name]["by_resolution"] = {
            key: describe(values)
            for key, values in sorted(by_resolution.items())
        }
    report["parameters"] = params

    # dynamic VBV behaviour: audit trail of how vbv-maxrate was produced
    vbv_modes = Counter()
    vbv_ratios: list[float] = []
    for record in records:
        audit = record.get("effective_params_audit") or {}
        entry = audit.get("vbv-maxrate") or {}
        mode = str(entry.get("mode") or "unknown")
        vbv_modes[mode] += 1
        if mode == "dynamic_vbv":
            ob = _num(entry.get("ob_kbps"))
            final = _num(entry.get("final_maxrate_kbps"))
            if ob and final:
                vbv_ratios.append(final / ob)
    report["dynamic_vbv"] = {
        "modes": _counter_dict(vbv_modes),
        "achieved_ratio_vs_ob": describe(vbv_ratios),
        "note": (
            "mode 'static' = base-profile VBV kept (scaling disabled, no "
            "rule for the class, or OB unknown); 'cpb_clamp' = HEVC level "
            "MaxCPB clamp applied; 'dynamic_vbv' = source-relative value"
        ),
    }

    # ---------------- outputs ----------------------------------------
    walls = [_num(r.get("wall_time_sec")) for r in records]
    out_sizes = [_num(r.get("output_size")) for r in records]
    ratios = [_num(r.get("compression_ratio")) for r in records]
    encode_fps = [_num(r.get("encode_fps")) for r in records]
    durations = [_num(r.get("output_duration")) for r in records]
    speeds = [
        fps / 1.0 for fps in encode_fps if fps
    ]
    ok_walls = [
        _num(r.get("wall_time_sec")) for r in succeeded
    ]
    ok_durations = [_num(r.get("output_duration")) for r in succeeded]
    realtime = [
        d / w for d, w in zip(ok_durations, ok_walls)
        if d and w and w > 0
    ]
    report["outputs"] = {
        "wall_time_sec": describe([w for w in walls if w is not None]),
        "output_size_bytes": describe(
            [s for s in out_sizes if s is not None]
        ),
        "output_duration_sec": describe(
            [d for d in durations if d is not None]
        ),
        "compression_ratio": describe([r for r in ratios if r is not None]),
        "encode_fps": describe([f for f in encode_fps if f is not None]),
        "encode_fps_all": describe(speeds),
        "realtime_factor": describe(realtime),
        "by_backend": {
            backend: {
                "attempts": sum(
                    1 for r in records if r.get("backend") == backend
                ),
                "encode_fps": describe([
                    f for f in (
                        _num(r.get("encode_fps")) for r in records
                        if r.get("backend") == backend
                    ) if f
                ]),
                "compression_ratio": describe([
                    c for c in (
                        _num(r.get("compression_ratio")) for r in records
                        if r.get("backend") == backend
                    ) if c is not None
                ]),
            }
            for backend in sorted({
                str(r.get("backend") or "") for r in records
            })
        },
        "by_preset": {
            preset: {
                "attempts": sum(
                    1 for r in records if r.get("preset") == preset
                ),
                "compression_ratio": describe([
                    c for c in (
                        _num(r.get("compression_ratio")) for r in records
                        if r.get("preset") == preset
                    ) if c is not None
                ]),
            }
            for preset in sorted({
                str(r.get("preset") or "") for r in records
            })
        },
    }

    # ---------------- data quality -----------------------------------
    missing_fields: Counter = Counter()
    incomplete = 0
    for record in records:
        completeness = record.get("completeness") or {}
        if completeness.get("complete"):
            continue
        incomplete += 1
        for field in completeness.get("missing") or []:
            missing_fields[str(field)] += 1
    jobs_without_final = sum(
        1 for j in jobs.values() if not j["final_status"]
    )
    report["data_quality"] = {
        "complete_records": len(records) - incomplete,
        "incomplete_records": incomplete,
        "missing_fields_top": _counter_dict(missing_fields),
        "jobs_without_final_event": jobs_without_final,
        "records_with_verification_result": sum(
            1 for r in records if r.get("verification_result")
        ),
        "records_without_attempt_started": sum(
            1 for r in records
            if (r.get("completeness") or {}).get("attempt_started_seen")
            is False
        ),
        "records_without_job_started": sum(
            1 for r in records
            if (r.get("completeness") or {}).get("job_started_seen") is False
        ),
        "coverage_note": (
            "Attempt-level records are the dataset unit; a month of "
            "collection is expected to hold every attempt including the "
            "failed ones (see attempts.failure_stages)."
        ),
    }
    return report


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _table(counter: dict[str, int], key: str = "value",
           limit: int = 12) -> str:
    if not counter:
        return "  (none)\n"
    lines = []
    for name, count in list(counter.items())[:limit]:
        lines.append(f"  {str(name)[:60]:<62} {count:>8,}")
    if len(counter) > limit:
        lines.append(f"  ... {len(counter) - limit} more")
    return "\n".join(lines) + "\n"


def _stat_block(label: str, stats: dict[str, Any]) -> str:
    if not stats or not stats.get("n"):
        return f"  {label:<26} (no data)\n"
    return (
        f"  {label:<26} n={stats['n']:<6} "
        f"min={_fmt(stats['min']):<12} p10={_fmt(stats['p10']):<12} "
        f"median={_fmt(stats['median']):<12} mean={_fmt(stats['mean']):<12} "
        f"p90={_fmt(stats['p90']):<12} max={_fmt(stats['max'])}\n"
    )


def render(report: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add("x265 CALIBRATION DATASET SUMMARY")
    add("=" * 78)
    add(f"analyzer        : {report['analyzer']} {report['analyzer_version']}")
    add(f"generated       : {report['generated_at']}")
    add(f"samples dir     : {report['dataset']['samples_dir']}")
    add(f"sample files    : {len(report['dataset']['files'])}")
    add(f"records         : {report['dataset']['records']:,}")
    add("")
    add("Read-only analysis: this tool never writes x265.json or")
    add("x265_scaling.json and never feeds values back into production.")
    add("")

    if not report["dataset"]["records"]:
        add("EMPTY DATASET — nothing to analyze.")
        return "\n".join(out) + "\n"

    prov = report["provenance"]
    add("-" * 78)
    add("PROVENANCE")
    add("-" * 78)
    add(f"  project versions : {prov['project_versions']}")
    add(f"  backends         : {prov['backends']}")
    add(f"  presets          : {prov['presets']}")
    add(f"  ffmpeg versions  : {prov['ffmpeg_versions']}")
    add(f"  libx265 versions : {prov['libx265_versions']}")
    add(f"  single-version run: {prov['single_version_run']}")
    add("")

    att = report["attempts"]
    add("-" * 78)
    add("PRODUCTION / ATTEMPTS")
    add("-" * 78)
    add(f"  attempts total   : {att['total']:,}")
    add(f"  succeeded        : {att['succeeded']:,}")
    add(f"  failed           : {att['failed']:,}")
    add(f"  success rate     : {_fmt(att['success_rate'])}")
    add(f"  failure rate     : {_fmt(att['failure_rate'])}")
    if att["unknown_status"]:
        add(f"  unknown status   : {att['unknown_status']:,}")
    add("  attempts by backend:")
    add(_table(att["by_backend"]))
    add("  attempts by kind:")
    add(_table(att["by_attempt_kind"]))
    add("  failure stages:")
    add(_table(att["failure_stages"]))
    add("  top failure reasons:")
    add(_table(att["failure_reasons_top"], limit=8))
    add("  verification status:")
    add(_table(att["verification_status"]))
    add("")

    jobs = report["jobs"]
    add("-" * 78)
    add("JOBS (source -> job -> attempt -> accepted output)")
    add("-" * 78)
    add(f"  jobs total       : {jobs['total']:,}")
    add(f"  succeeded        : {jobs['succeeded']:,}")
    add(f"  failed           : {jobs['failed']:,}")
    add(f"  with retry       : {jobs['with_retry']:,} "
        f"(rate {_fmt(jobs['retry_rate'])})")
    add(f"  with fallback    : {jobs['with_fallback']:,} "
        f"(rate {_fmt(jobs['fallback_rate'])})")
    add(f"  recovered        : {jobs['recovered_after_failure']:,} "
        f"(rate {_fmt(jobs['recovery_rate'])})")
    add(f"  max attempts/job : {jobs['max_attempts_per_job']}")
    add("")

    src = report["sources"]
    add("-" * 78)
    add("SOURCES")
    add("-" * 78)
    add(f"  unique sources   : {src['unique_sources']:,}")
    add("  resolution:")
    add(_table(src["resolution"]))
    add(_stat_block("width", src["width"]))
    add(_stat_block("height", src["height"]))
    add(_stat_block("fps", src["fps"]))
    add("  fps buckets:")
    add(_table(src["fps_buckets"]))
    add("  codec:")
    add(_table(src["codec"]))
    add("  profile:")
    add(_table(src["profile"]))
    add("  pix_fmt:")
    add(_table(src["pix_fmt"]))
    add("  bit depth:")
    add(_table(src["bit_depth"]))
    add("  chroma:")
    add(_table(src["chroma"]))
    add(_stat_block("container bitrate kbps", src["container_bitrate_kbps"]))
    add(_stat_block("duration sec", src["duration_sec"]))
    add("  source class:")
    add(_table(src["source_class"]))
    add(_stat_block("normalized_ob", src["normalized_ob"]))
    add("  normalized_ob buckets (bits/pixel-frame):")
    add(_table(src["normalized_ob_buckets"]))
    add("  source class / preset:")
    add(_table(src["source_class_x_preset"], limit=20))
    add("")

    sc = report["scaling"]
    add("-" * 78)
    add("SCALING")
    add("-" * 78)
    add(f"  records with context     : {sc['records_with_context']:,}")
    add(f"  marked not_applicable    : "
        f"{sc['records_marked_not_applicable']:,}")
    add("  reference:")
    add(_table(sc["reference"]))
    add(_stat_block("spatial_factor", sc["spatial_factor"]))
    add(_stat_block("temporal_factor", sc["temporal_factor"]))
    add(_stat_block("pixel_rate_factor", sc["pixel_rate_factor"]))
    add("  spatial factor buckets:")
    add(_table(sc["spatial_factor_buckets"]))
    add("  pixel-rate factor buckets:")
    add(_table(sc["pixel_rate_factor_buckets"]))
    add("")

    add("-" * 78)
    add("PARAMETERS (as resolved by production, verbatim)")
    add("-" * 78)
    for name, block in report["parameters"].items():
        add(f"  {name}")
        add(f"    reported={block['reported']:,} "
            f"non_numeric_markers={block['non_numeric_markers']:,}")
        stats = block["distribution"]
        if stats.get("n"):
            distinct = block["distinct_values"]
            distinct_text = (
                str(len(distinct)) if "note" not in distinct else "many"
            )
            add(
                f"    distinct={distinct_text} "
                f"min={_fmt(stats['min'])} median={_fmt(stats['median'])} "
                f"max={_fmt(stats['max'])}"
            )
        for key, values in list(block["by_resolution"].items())[:6]:
            if values.get("n"):
                add(f"      {key:<12} n={values['n']:<6} "
                    f"min={_fmt(values['min']):<8} "
                    f"median={_fmt(values['median']):<8} "
                    f"max={_fmt(values['max'])}")
    add("")

    vbv = report["dynamic_vbv"]
    add("DYNAMIC VBV")
    add(f"  modes            : {vbv['modes']}")
    add(_stat_block("achieved ratio vs OB", vbv["achieved_ratio_vs_ob"]))
    add(f"  {vbv['note']}")
    add("")

    out_stats = report["outputs"]
    add("-" * 78)
    add("OUTPUT / PERFORMANCE")
    add("-" * 78)
    add(_stat_block("wall time sec", out_stats["wall_time_sec"]))
    add(_stat_block("output size bytes", out_stats["output_size_bytes"]))
    add(_stat_block("output duration sec", out_stats["output_duration_sec"]))
    add(_stat_block("compression ratio", out_stats["compression_ratio"]))
    add(_stat_block("encode fps", out_stats["encode_fps"]))
    add(_stat_block("realtime factor", out_stats["realtime_factor"]))
    add("  by backend:")
    for backend, block in out_stats["by_backend"].items():
        fps = block["encode_fps"]
        ratio = block["compression_ratio"]
        add(f"    {backend or '(none)':<12} attempts={block['attempts']:<7} "
            f"encode_fps(median)={_fmt(fps.get('median')):<10} "
            f"ratio(median)={_fmt(ratio.get('median'))}")
    add("")

    dq = report["data_quality"]
    add("-" * 78)
    add("DATA QUALITY")
    add("-" * 78)
    add(f"  complete records          : {dq['complete_records']:,}")
    add(f"  incomplete records        : {dq['incomplete_records']:,}")
    add(f"  jobs without final event  : {dq['jobs_without_final_event']:,}")
    add(f"  records with verification : "
        f"{dq['records_with_verification_result']:,}")
    add("  missing fields (top):")
    add(_table(dq["missing_fields_top"], limit=10))
    add("")
    add("=" * 78)
    add("END OF SUMMARY — raw samples/*.jsonl remain the authoritative data.")
    add("=" * 78)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _make_console_utf8() -> None:
    """Best-effort UTF-8 console, so non-ASCII report text is not mojibake.

    The report contains an em dash and box characters; a stock Windows console
    is cp936/cp1252 and would render them as garbage. This only affects
    display - the JSON output and the dataset are always UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _make_console_utf8()
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description=(
            "Offline analyzer for an x265 calibration dataset. Read-only: "
            "it never writes x265.json or x265_scaling.json."
        ),
    )
    parser.add_argument("dataset",
                        help="calibration root or its samples/ directory")
    parser.add_argument("--version", action="version",
                        version=f"{ANALYZER_NAME} {ANALYZER_VERSION}")
    parser.add_argument("--json", metavar="PATH",
                        help="also write the full report as JSON here")
    parser.add_argument("--dataset-summary", action="store_true",
                        help="print the summary (default behaviour)")
    parser.add_argument("--quiet", action="store_true",
                        help="write nothing to stdout (with --json)")
    args = parser.parse_args(argv)

    samples_dir = resolve_samples_dir(Path(args.dataset))
    if not samples_dir.is_dir():
        print(f"[FATAL] dataset directory not found: {samples_dir}",
              file=sys.stderr)
        return 2

    report = analyze(samples_dir)
    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    if not args.quiet:
        sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
