#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration test: production fallback ladder -> attempt-level records.

Drives the REAL hardware per-file path
(``core.batch_hw.encode_one_hw_classic``) with the real hardware backend
and the collector enabled, but makes the first tool run fail so the
production downgrade ladder has to retry. Verifies that the ladder is
recorded as separate immutable attempts:

    A001  FAILED     primary    (failure_stage=encode_failed)
    A002  SUCCEEDED  downgrade  (trigger names the ladder rung)
    job   SUCCESS    accepted_attempt = A002

This is the hardware equivalent of DEP-07/08/09: production owns the
retry, the collector only observes it.

Usage:  python tools/x265_calibration/tests/ladder_integration.py
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
WORK = PROJECT / "work" / "ladder_test"
FFMPEG = PROJECT / "tools" / "ffmpeg.exe"

sys.path.insert(0, str(PROJECT))


def make_clip(path: Path) -> None:
    subprocess.run([
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    ], check=True, timeout=600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ladder_integration.py")
    parser.add_argument("--encoder", default="nvenc",
                        choices=["nvenc", "qsv"],
                        help="capability routing uses this backend")
    args = parser.parse_args(argv)

    from core import batch_hw
    from core import calibration_events as cal
    from core.batch_hw import hw_backend_for
    from core.config import find_executable, find_hw_tool
    from core.logging_utils import build_file_logger
    from core.probe import build_source_info, probe_source
    from preservation.gpac import GpacContainerBackend

    tool_name = "NVEncC64.exe" if args.encoder == "nvenc" else "QSVEncC64.exe"
    if find_hw_tool(PROJECT, tool_name) is None:
        print(f"[SKIP] {tool_name} not present — hardware backend unavailable")
        return 0

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    corpus = WORK / "corpus"
    corpus.mkdir(parents=True)
    src = corpus / "clip.mp4"
    make_clip(src)
    out_root = WORK / "out"
    logs_root = WORK / "logs"
    work_root = WORK / "workdir"
    spool = WORK / "calibration"
    for path in (out_root, logs_root, work_root):
        path.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_executable("ffmpeg", PROJECT, None)
    ffprobe = find_executable("ffprobe", PROJECT, None)

    class Args:
        gpu = None
        device = None
        qsv_device = None
        check_features = False
        verbose = False
        tool_nvencc = None
        tool_qsvencc = None
        hw_decode = "off"

    logger = logging.getLogger("ladder_integration")
    logger.handlers.clear()
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)
    file_logger = build_file_logger(logs_root / "ladder.log")
    backend = hw_backend_for(args.encoder, PROJECT, Args(), work_root, logger)

    summary, streams = probe_source(ffprobe, src)
    src_info = build_source_info(src, summary, streams)

    real_run = batch_hw.run_hw_tool
    state = {"calls": 0}

    def flaky_run(cmd, raw_log, total_frames, label, progress=True):
        state["calls"] += 1
        if state["calls"] == 1:
            Path(raw_log).write_text(
                "synthetic failure injected by the ladder integration test\n",
                encoding="utf-8",
            )
            return 3, 0.1
        return real_run(cmd, raw_log, total_frames, label, progress=progress)

    batch_hw.run_hw_tool = flaky_run

    cal.setup(enabled=True, spool=spool,
              project_version="1KeyTranscoder 0.7.0")
    profile = {
        "preset": "p4", "crf": 26, "bframes": 3, "ref": 3,
        "lookahead": 20, "gop_len": 60,
    }
    classification, _config, error = batch_hw._classification_for(src_info)
    if error:
        print("[WARN] classification:", error)
    telemetry = batch_hw.telemetry_begin_job(
        src=src, dst=out_root / "clip.MP4", preset="HQ", profile=profile,
        backend=backend.name, src_info=src_info, classification=classification,
        input_root=corpus, output_root=out_root, logs_root=logs_root,
        config_path=None, ffmpeg=ffmpeg, check_level="basic", dry_run=False,
        rel_path="clip.mp4",
    )
    try:
        result = batch_hw.encode_one_hw_classic(
            src=src, dst=out_root / "clip.MP4",
            source_summary=summary, src_info=src_info, vfr=False,
            profile=profile, preset="HQ", backend=backend, ffprobe=ffprobe,
            postprobe_csv=logs_root / "postprobe.csv",
            postprobe_stream_csv=logs_root / "postprobe_streams.csv",
            gpac=GpacContainerBackend(None), work_root=work_root,
            no_downgrade=False, hw_decode=batch_hw.HW_DECODE_OFF,
            hw_decode_memo={}, check_level="basic", ffmpeg=ffmpeg,
            quality_opts=None, quality_csv=None, channel_sync=False,
            channel_sync_opts=None, logger=logger, file_logger=file_logger,
            dry_run=False, status_cb=None, show_progress=False,
            throughput_cb=None, calibration=telemetry,
        )
    finally:
        batch_hw.run_hw_tool = real_run
        batch_hw.telemetry_end_job(
            telemetry, result="failed" if state["calls"] < 2 else "done",
            failure_stage="encode_failed" if state["calls"] < 2 else "",
            failure_reason=(
                "hardware encode failed" if state["calls"] < 2 else ""
            ),
        )

    subprocess.run([
        sys.executable, "-m", "collector.collector_cli", "--headless", "--once",
        "--watch", str(spool), "--job-final-timeout", "0",
    ], cwd=str(PROJECT / "tools" / "x265_calibration"), check=False, timeout=600)

    records = []
    for path in sorted((spool / "samples").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    print(f"\nproduction result: {result} | tool runs: {state['calls']}")
    print(f"records: {len(records)}")
    for record in records:
        print(f"  {record['attempt_id']:<20} {record['status']:<10} "
              f"kind={record.get('attempt_kind',''):<12} "
              f"format={record.get('encoder_format',''):<10} "
              f"stage={record.get('failure_stage','') or '-':<18} "
              f"job_final={record.get('job_final_status','') or '-'}")
        if record.get("trigger"):
            print(f"      trigger: {record['trigger'][:130]}")

    ok = (
        len(records) == 2
        and records[0]["status"] == "FAILED"
        and records[0]["attempt_number"] == 1
        and records[0]["failure_stage"] == "encode_failed"
        and records[1]["status"] == "SUCCEEDED"
        and records[1]["attempt_number"] == 2
        and records[1]["attempt_kind"] in ("downgrade", "reader_fallback")
        and all(r["job_final_status"] == "SUCCESS" for r in records)
    )
    print()
    print("RESULT:", "PASS - the production ladder is recorded as separate "
          "immutable attempts" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
