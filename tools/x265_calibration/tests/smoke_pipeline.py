"""Smoke test: producer emitter -> collector, no encoder involved.

Exercises the event lifecycle, the retry model and restart recovery
using only the production emitter module and the collector engine.

    python tools/x265_calibration/tests/smoke_pipeline.py [workdir]
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools" / "x265_calibration"))

from core import calibration_events as cal            # noqa: E402
from core.models import (                             # noqa: E402
    EffectiveParams,
    ScalingContext,
    SourceClassification,
    SourceInfo,
    StreamBrief,
)
from collector import collector_core as cc            # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          + (f" | {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def make_source_info(path: Path) -> SourceInfo:
    return SourceInfo(
        path=path,
        size_bytes=12345678,
        duration_sec=12.5,
        width=3840,
        height=2160,
        fps=59.94,
        r_frame_rate="60000/1001",
        avg_frame_rate="60000/1001",
        codec="hevc",
        profile="Main 10",
        pix_fmt="yuv420p10le",
        bit_depth=10,
        chroma="4:2:0",
        ob_kbps=150000.0,
        video_bitrate_kbps=148000.0,
        video_stream_count=1,
        stream_info=(StreamBrief(0, "video", "hevc"),),
        raw_summary={"width": 3840},
        raw_streams=({"index": 0, "codec_type": "video",
                      "codec_name": "hevc", "width": 3840, "height": 2160},),
    )


def make_effective() -> EffectiveParams:
    context = ScalingContext(
        reference_width=3840,
        reference_height=2160,
        reference_fps=59.94,
        spatial_factor=0.5,
        temporal_factor=0.5005,
        pixel_rate_factor=0.25,
        aspect_ratio=1.7778,
        source_class="HIGH_BITRATE_LONG_GOP",
        normalized_ob=0.31,
    )
    return EffectiveParams(
        values={
            "crf": "21",
            "merange": "29",
            "rc-lookahead": "90",
            "min-keyint": "15",
            "vbv-maxrate": "127500",
            "vbv-bufsize": "240000",
        },
        audit={
            "merange": {"mode": "sqrt_pixels", "base": 57.0,
                        "factor": 0.5, "final": 29},
            "vbv-maxrate": {"mode": "dynamic_vbv", "ob_kbps": 150000.0,
                            "final_maxrate_kbps": 127500},
        },
        context=context,
    )


def scenario_failed_then_retry(root: Path, source: Path) -> dict:
    """job_started -> A001 fail -> A002 succeed -> job_succeeded."""
    info = make_source_info(source)
    classification = SourceClassification(
        "HIGH_BITRATE_LONG_GOP", 0.31, "normalized_ob >= high_min"
    )
    effective = make_effective()

    cal.setup(enabled=True, spool=root, project_version="1KeyTranscoder 0.7.0")
    cal.begin_job(
        job_id="JOB001",
        source=source,
        relative_path="corpus/JOB001.mp4",
        requested_preset="HQ",
        requested_encoder="x265",
        source_info=info,
        classification=classification,
        effective=effective,
        preset="HQ",
        crf=21,
        config_hash="cfg",
        scaling_config_hash="scale",
        input_root=root,
        output_root=root,
    )
    # attempt 1: fails
    cal.begin_attempt(backend="x265", preset="HQ", kind="primary",
                      crf=21, inherit_context=True, source_info=info,
                      classification=classification, effective=effective,
                      actual_x265_params="crf=21:merange=29:rc-lookahead=90")
    cal.attempt_failed(
        failure_stage=cal.STAGE_ENCODE,
        failure_reason="ffmpeg exited with rc=1",
        exit_code=1,
        output_exists=False,
        verification_status=cal.VERIFY_NOT_RUN,
        error_summary="x265 [error]: frame size is too large",
    )
    # attempt 2: retry succeeds
    cal.begin_attempt(backend="x265", preset="HQ", kind="retry",
                      trigger="production retry after failure", crf=21,
                      inherit_context=True,
                      actual_x265_params="crf=21:merange=29:rc-lookahead=90")
    cal.attempt_succeeded(
        exit_code=0,
        verification={"status": "passed",
                      "checks": ["encoder_exit_code_0",
                                 "output_file_present_non_empty"]},
        output={
            "output_size": 3456789,
            "output_duration": 12.5,
            "output_width": 3840,
            "output_height": 2160,
            "output_video_bitrate": 2200.0,
            "output_frame_count": 749,
            "compression_ratio": 0.28,
            "encode_fps": 12.4,
            "output_path": str(source),
        },
        tool_versions={"ffmpeg": "9.0.1", "libx265": "4.1"},
    )
    cal.end_job(accepted_attempt_number=2)
    return {"expected_attempts": 2}


def main() -> int:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        PROJECT / "work" / "calibration_smoke"
    )
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    corpus = work / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    source = corpus / "JOB001.mp4"
    source.write_bytes(b"not a real video, just a hashable file" * 64)

    root = work / "calibration"
    scenario_failed_then_retry(root, source)

    pending = root / "events" / "pending"
    files = sorted(p.name for p in pending.glob("*.json"))
    check("producer wrote 6 events atomically (no tmp left)",
          len(files) == 6 and not list(pending.glob("*.tmp")),
          f"{len(files)} files: {files}")

    # atomicity: no partial file is visible to the collector
    leftovers = [p.name for p in pending.iterdir() if p.name.startswith(".")]
    check("no partial/temp files visible in pending/", not leftovers,
          str(leftovers))

    log_lines: list[str] = []
    collector = cc.Collector(
        root, poll_interval=0.05, log=lambda lvl, msg: log_lines.append(msg)
    )
    collector.load_state()
    counters = collector.sweep()

    samples = sorted((root / "samples").glob("*.jsonl"))
    records = [json.loads(line) for line in samples[0].read_text(
        encoding="utf-8").splitlines() if line.strip()] if samples else []

    check("collector produced exactly 2 attempt records (retry preserved)",
          len(records) == 2, f"records={len(records)}")
    if len(records) == 2:
        first, second = records
        check("A001 kept as FAILED with stage + reason",
              first["status"] == "FAILED"
              and first["failure_stage"] == "encode_failed"
              and "rc=1" in first["failure_reason"],
              f"{first['attempt_id']} {first['status']}")
        check("A002 recorded as SUCCEEDED",
              second["status"] == "SUCCEEDED"
              and second["attempt_number"] == 2,
              f"{second['attempt_id']} {second['status']}")
        check("retry links to the previous attempt",
              second["attempt_kind"] == "retry"
              and second["retry_of_attempt_id"].endswith("A001"),
              str(second.get("retry_of_attempt_id")))
        check("effective params recorded verbatim (no recalculation)",
              second["effective_params"]["merange"] == "29"
              and second["effective_params"]["vbv-maxrate"] == "127500")
        check("scaling context recorded verbatim",
              second["scaling_context"]["spatial_factor"] == 0.5)
        check("classification recorded verbatim",
              second["classification"]["source_class"]
              == "HIGH_BITRATE_LONG_GOP")
        check("output + verification facts recorded",
              second["output_size"] == 3456789
              and second["verification_result"]["status"] == "passed")
        check("source hash + identity recorded",
              bool(second["source_sha256"])
              and second["relative_path"] == "corpus/JOB001.mp4",
              second["source_sha256"][:16])
        check("records are complete",
              second["completeness"]["complete"] is True
              and first["completeness"]["complete"] is True,
              f"A001={first['completeness']['missing']} "
              f"A002={second['completeness']['missing']}")
        check("failed attempt keeps its production verification verdict",
              first["verification_status"] == "not_run"
              and first["verification_result"]["failure_stage"]
              == "encode_failed")
    check("pending/ drained after consumption",
          not list(pending.glob("*.json")),
          str([p.name for p in pending.glob("*.json")]))
    check("events archived",
          len(list((root / "events" / "archive").rglob("*.json"))) == 6)
    check("daily summary written",
          len(list((root / "summaries").glob("*.json"))) == 1)
    check("manifest written", (root / "manifest.json").is_file())

    # ---- restart recovery (DEP-04) ---------------------------------
    collector2 = cc.Collector(root, log=lambda lvl, msg: None)
    collector2.load_state()
    counters2 = collector2.sweep()
    records_after = [json.loads(line) for line in samples[0].read_text(
        encoding="utf-8").splitlines() if line.strip()]
    check("restart does not duplicate records",
          counters2.records_written == 0 and len(records_after) == 2,
          f"written={counters2.records_written} total={len(records_after)}")

    # ---- duplicate event (DEP: idempotent) -------------------------
    archived = sorted((root / "events" / "archive").rglob("*.json"))
    shutil.copy2(archived[0], pending / archived[0].name)
    collector3 = cc.Collector(root, log=lambda lvl, msg: None)
    collector3.load_state()
    counters3 = collector3.sweep()
    check("duplicate event ignored (idempotent)",
          counters3.events_duplicate == 1
          and counters3.records_written == 0,
          f"dup={counters3.events_duplicate} "
          f"written={counters3.records_written}")

    # ---- partial file quarantined ----------------------------------
    (pending / "torn.json").write_text('{"event_id": "torn-1", '
                                      '"event_type": "attempt_star',
                                       encoding="utf-8")
    collector4 = cc.Collector(root, log=lambda lvl, msg: None)
    collector4.load_state()
    c4 = collector4.sweep()
    check("partial JSON file quarantined to failed/",
          c4.events_invalid == 1
          and (root / "events" / "failed" / "torn.json").is_file(),
          f"invalid={c4.events_invalid}")

    # ---- schema-invalid event rejected, not counted ----------------
    bad = {
        "schema_version": "1.0",
        "event_id": "bad-envelope-1",
        "event_type": "attempt_started",
        "job_id": "JOB001",
        # missing timestamp / attempt_id / attempt_number / backend ...
        "payload": {},
    }
    (pending / "bad1.json").write_text(json.dumps(bad), encoding="utf-8")
    collector5 = cc.Collector(root, log=lambda lvl, msg: None)
    collector5.load_state()
    c5 = collector5.sweep()
    records_now = [json.loads(line) for line in samples[0].read_text(
        encoding="utf-8").splitlines() if line.strip()]
    check("schema-invalid event rejected and quarantined",
          c5.events_invalid == 1 and len(records_now) == 2,
          f"invalid={c5.events_invalid} records={len(records_now)}")
    check("rejected events recorded in errors/rejected_events.jsonl",
          (root / "errors" / "rejected_events.jsonl").is_file())

    # ---- dataset validation ----------------------------------------
    report = cc.validate_dataset(root / "samples", collector.schema)
    check("--validate reports a consistent dataset",
          report["ok"] and report["counts"]["records"] == 2,
          json.dumps(report["counts"]))

    # ---- pure-collector process isolation --------------------------
    check("collector imports nothing from the project core",
          "core." not in Path(cc.__file__).read_text(encoding="utf-8"),
          "")

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("SMOKE TEST PASSED")
    print(f"dataset at: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
