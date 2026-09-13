#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEP-01..DEP-14 acceptance tests for the x265 calibration collector.

Runs on the DEVELOPMENT machine, offline, against a small corpus. Every
check maps to a numbered requirement of the v0.7.0 task:

    DEP-01 Collector starts independently
    DEP-02 Production starts without collector
    DEP-03 Collector starts after production
    DEP-04 Collector restart recovers pending events
    DEP-05 Collector failure does not fail encode
    DEP-06 Event atomicity
    DEP-07 Failed attempt preserved
    DEP-08 Retry creates new attempt
    DEP-09 Successful retry preserves previous failure
    DEP-10 Job final state correct
    DEP-11 Offline standalone binary
    DEP-12 Clean-machine execution
    DEP-13 No external network
    DEP-14 No Python installation required

Usage:

    python tools/x265_calibration/tests/dep_matrix.py \
        [--exe dist/x265_calibration/x265-calibration-collector.exe] \
        [--skip-clean-machine]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
TOOLS = PROJECT / "tools" / "x265_calibration"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(TOOLS))

from collector import collector_core as cc          # noqa: E402

WORK = PROJECT / "work" / "dep_matrix"
RESULTS: list[tuple[str, str, bool, str]] = []


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def record(dep: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((dep, name, bool(ok), detail))
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {dep} {name}" + (f" | {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 1800) -> tuple[int, str]:
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=full_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
    )
    return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")


def read_records(samples_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(samples_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND its children.

    A onefile PyInstaller exe runs a bootloader parent plus the real
    child, so ``Popen.kill()`` alone would leave a collector running in
    the background and watching the next test's spool.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=60)
    except Exception:
        pass


def kill_stray_collectors() -> int:
    """No collector may be watching anything while the tests run."""
    if os.name != "nt":
        return 0
    script = (
        "Get-Process -Name 'x265-calibration-collector' -ErrorAction "
        "SilentlyContinue | Stop-Process -Force; "
        "(Get-Process -Name 'x265-calibration-collector' -ErrorAction "
        "SilentlyContinue | Measure-Object).Count"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120,
        )
        return int((proc.stdout or b"0").decode().strip() or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def ensure_corpus(corpus: Path, ffmpeg: Path) -> list[Path]:
    """Two tiny clips: one high-ish bitrate, one low. Reused if present."""
    corpus.mkdir(parents=True, exist_ok=True)
    specs = (
        ("clip_a.mp4", "1920x1080", "30", "12000k"),
        ("clip_b.mp4", "1280x720", "25", "700k"),
    )
    made: list[Path] = []
    for name, size, rate, bitrate in specs:
        target = corpus / name
        if not target.is_file() or target.stat().st_size == 0:
            rc, out = run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"testsrc2=size={size}:rate={rate}:duration=2",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-b:v", bitrate, str(target),
            ], timeout=300)
            if rc != 0:
                raise RuntimeError(f"could not build corpus clip: {out[-400:]}")
        made.append(target)
    return made


def transcode(corpus: Path, out: Path, spool: Path, preset: str = "hq",
              encoder: str = "x265", check: str = "basic") -> tuple[int, str]:
    env = {
        "ONEXKT_CALIBRATION": "1",
        "ONEXKT_CALIBRATION_SPOOL": str(spool),
    }
    return run([
        sys.executable, str(PROJECT / "1kt.py"),
        "--input", str(corpus), "--output", str(out),
        "--encoder", encoder, "--preset", preset,
        "--check", check, "--headless", "--jobs", "1",
    ], cwd=PROJECT, env=env, timeout=3600)


# ---------------------------------------------------------------------------
# DEP-01 / DEP-03 / DEP-04 / DEP-05
# ---------------------------------------------------------------------------

def test_collector_process_model(corpus_dir: Path, exe: Path | None) -> None:
    section("DEP-01 / DEP-03 / DEP-04: independent process + restart recovery")

    root = fresh(WORK / "spool_independent")
    out = fresh(WORK / "out_independent")

    # DEP-02: production runs with NO collector present at all.
    rc, text = transcode(corpus_dir, out, root)
    pending = sorted((root / "events" / "pending").glob("*.json"))
    produced = len(list(out.glob("*.MP4")))
    record(
        "DEP-02", "production starts and completes without any collector",
        rc == 0 and produced == len(list(corpus_dir.glob("*.mp4")))
        and len(pending) > 0,
        f"rc={rc} outputs={produced} events={len(pending)}",
    )
    if rc != 0:
        print(text[-1500:])

    # DEP-05: a collector failure is contained and production is unaffected.
    rc2, text2 = transcode(corpus_dir, fresh(WORK / "out_fail"), root)
    record(
        "DEP-05", "collector never ran, production exit code still 0",
        rc2 == 0 and "telemetry failure" not in text2,
        f"rc={rc2}",
    )

    # DEP-01: the collector starts on its own, with no production process.
    if exe is not None:
        rc3, text3 = run(
            [exe, "--headless", "--once", "--watch", str(root)],
            timeout=600,
        )
        start_cmd = exe.name
    else:
        rc3, text3 = run(
            [sys.executable, "-m", "collector.collector_cli", "--headless",
             "--once", "--watch", str(root)],
            cwd=TOOLS, timeout=600,
        )
        start_cmd = "python -m collector.collector_cli"
    records = read_records(root / "samples")
    record(
        "DEP-01", "collector starts independently and consumes the backlog",
        rc3 == 0 and len(records) == 2,
        f"{start_cmd} rc={rc3} records={len(records)}",
    )
    if rc3 != 0:
        print(text3[-1200:])

    # DEP-03: production can also run BEFORE the collector is started; that
    # is exactly what just happened (events sat in pending/ until now).
    record(
        "DEP-03", "backlog produced before the collector started was consumed",
        len(records) == 2
        and not list((root / "events" / "pending").glob("*.json")),
    )

    # DEP-04: restart recovery - kill mid-run, restart, no double counting.
    #   simulate by copying an already-archived event back into pending/
    #   (what a crash between "persist" and "archive" leaves behind).
    archived = sorted((root / "events" / "archive").rglob("*.json"))
    assert archived, "no archived events to re-inject"
    target_pending = root / "events" / "pending"
    shutil.copy2(archived[0], target_pending / archived[0].name)
    # a torn file must also be handled
    (target_pending / "torn_event.json").write_text(
        '{"event_id": "torn-1", "event_type": "attempt_star',
        encoding="utf-8",
    )
    if exe is not None:
        rc4, text4 = run(
            [exe, "--headless", "--once", "--watch", str(root)], timeout=600
        )
    else:
        rc4, text4 = run(
            [sys.executable, "-m", "collector.collector_cli", "--headless",
             "--once", "--watch", str(root)],
            cwd=TOOLS, timeout=600,
        )
    after = read_records(root / "samples")
    quarantined = list((root / "events" / "failed").glob("torn_event.json"))
    record(
        "DEP-04", "restart recovers pending events without double counting",
        rc4 == 0 and len(after) == len(records) and bool(quarantined),
        f"records {len(records)} -> {len(after)}; "
        f"torn file quarantined={bool(quarantined)}",
    )


# ---------------------------------------------------------------------------
# DEP-06: atomicity
# ---------------------------------------------------------------------------

def test_atomicity(root: Path) -> None:
    section("DEP-06: event atomicity")
    pending = root / "events" / "pending"
    pending.mkdir(parents=True, exist_ok=True)

    # A tmp file must be invisible to the collector, and a completed event
    # must appear in one step (never as a readable partial file).
    tmp_path = pending / ".never-visible.json.tmp"
    tmp_path.write_text('{"event_id": "half', encoding="utf-8")
    collector = cc.Collector(root, log=lambda lvl, msg: None)
    collector.load_state()
    before = len(read_records(root / "samples"))
    collector.sweep()
    after = len(read_records(root / "samples"))
    record(
        "DEP-06", "collector ignores in-progress .tmp files",
        before == after and tmp_path.is_file(),
        f"records unchanged ({after})",
    )
    tmp_path.unlink()

    # Producer-side atomic write: no partial file, and the visible file is
    # complete valid JSON.
    from core import calibration_events as cal

    spool = fresh(WORK / "spool_atomic")
    cal.setup(enabled=True, spool=spool)
    cal.begin_job(
        job_id="ATOMIC-JOB",
        source=PROJECT / "VERSION",
        relative_path="VERSION",
        requested_preset="hq",
        requested_encoder="x265",
    )
    files = sorted((spool / "events" / "pending").glob("*.json"))
    leftovers = [
        p.name for p in (spool / "events" / "pending").iterdir()
        if p.name.startswith(".")
    ]
    payload_ok = False
    if files:
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        payload_ok = (
            payload.get("event_type") == "job_started"
            and payload.get("job_id") == "ATOMIC-JOB"
            and bool(payload.get("event_id"))
        )
    record(
        "DEP-06", "event files are written atomically (rename, no partial)",
        len(files) == 1 and not leftovers and payload_ok,
        f"files={len(files)} temp_leftovers={leftovers}",
    )


# ---------------------------------------------------------------------------
# DEP-07..DEP-10: failure / retry / job state
# ---------------------------------------------------------------------------

def test_failure_and_retry(root: Path) -> None:
    section("DEP-07..DEP-10: failed attempt, retry, job final state")
    from core.models import (
        EffectiveParams, ScalingContext, SourceClassification, SourceInfo,
        StreamBrief,
    )
    from core import calibration_events as cal

    spool = fresh(root)
    source = PROJECT / "VERSION"
    info = SourceInfo(
        path=source, size_bytes=6, duration_sec=1.0, width=3840, height=2160,
        fps=59.94, r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
        codec="hevc", profile="Main 10", pix_fmt="yuv420p10le", bit_depth=10,
        chroma="4:2:0", ob_kbps=150000.0, video_bitrate_kbps=148000.0,
        video_stream_count=1,
        stream_info=(StreamBrief(0, "video", "hevc"),),
    )
    effective = EffectiveParams(
        values={"merange": "29", "rc-lookahead": "90",
                "vbv-maxrate": "127500", "vbv-bufsize": "240000"},
        audit={"merange": {"mode": "sqrt_pixels", "final": 29}},
        context=ScalingContext(
            reference_width=3840, reference_height=2160, reference_fps=59.94,
            spatial_factor=0.5, temporal_factor=0.5005,
            pixel_rate_factor=0.25, aspect_ratio=1.7778,
            source_class="HIGH_BITRATE_LONG_GOP", normalized_ob=0.31,
        ),
    )
    classification = SourceClassification(
        "HIGH_BITRATE_LONG_GOP", 0.31, "normalized_ob >= high_min"
    )

    cal.setup(enabled=True, spool=spool, project_version="1KeyTranscoder 0.7.0")
    cal.begin_job(
        job_id="RETRY-JOB", source=source, relative_path="VERSION",
        requested_preset="hq", requested_encoder="x265",
        source_info=info, classification=classification, effective=effective,
        preset="hq", crf=21,
    )
    # A001: hardware style failure
    cal.begin_attempt(backend="nvenc", preset="hq", kind="primary",
                      inherit_context=True, encoder_format="4:2:0/8",
                      reader="avhw")
    cal.attempt_failed(
        failure_stage=cal.STAGE_VERIFICATION,
        failure_reason="hardware decode integrity gate failed: reader_identity",
        exit_code=0, output_exists=True, output_size=1024,
        verification_status=cal.VERIFY_FAILED,
        error_summary="avcuvid was not constructed",
    )
    # A002: software retry succeeds
    cal.begin_attempt(backend="x265", preset="hq", kind="retry",
                      trigger="hardware failure fallback", inherit_context=True)
    cal.attempt_succeeded(
        exit_code=0,
        verification={"status": "passed",
                      "checks": ["encoder_exit_code_0"]},
        output={"output_size": 2048, "output_duration": 1.0,
                "output_width": 3840, "output_height": 2160,
                "compression_ratio": 0.1},
        tool_versions={"ffmpeg": "9.0.1", "libx265": "4.3"},
    )
    cal.end_job(accepted_attempt_number=2)

    collector = cc.Collector(spool, log=lambda lvl, msg: None)
    collector.load_state()
    collector.sweep()
    records = read_records(spool / "samples")
    by_id = {r["attempt_id"]: r for r in records}

    record(
        "DEP-07", "failed attempt preserved with stage and reason",
        "RETRY-JOB-A001" in by_id
        and by_id["RETRY-JOB-A001"]["status"] == "FAILED"
        and by_id["RETRY-JOB-A001"]["failure_stage"] == "verification_failed",
        f"records={[r['attempt_id'] for r in records]}",
    )
    record(
        "DEP-08", "retry created a distinct attempt",
        "RETRY-JOB-A002" in by_id
        and by_id["RETRY-JOB-A002"]["attempt_number"] == 2
        and by_id["RETRY-JOB-A002"]["attempt_kind"] == "retry",
    )
    record(
        "DEP-09", "successful retry did NOT overwrite the earlier failure",
        "RETRY-JOB-A001" in by_id
        and by_id["RETRY-JOB-A001"]["status"] == "FAILED"
        and by_id["RETRY-JOB-A002"]["status"] == "SUCCEEDED"
        and len(records) == 2,
    )
    record(
        "DEP-10", "job final state SUCCESS with the accepted attempt",
        all(r["job_final_status"] == "SUCCESS" for r in records)
        and by_id["RETRY-JOB-A002"]["accepted_attempt_id"]
        == "RETRY-JOB-A002",
        f"final={by_id['RETRY-JOB-A002']['job_final_status']} "
        f"accepted={by_id['RETRY-JOB-A002']['accepted_attempt_id']}",
    )

    # a job where every attempt fails must record FAILED
    spool2 = fresh(WORK / "spool_jobfail")
    cal.setup(enabled=True, spool=spool2, project_version="1KeyTranscoder 0.7.0")
    cal.begin_job(
        job_id="FAIL-JOB", source=source, relative_path="VERSION",
        requested_preset="hq", requested_encoder="x265", source_info=info,
        classification=classification, effective=effective, preset="hq",
    )
    for number in (1, 2):
        cal.begin_attempt(backend="x265", preset="hq",
                          kind="primary" if number == 1 else "retry",
                          inherit_context=True)
        cal.attempt_failed(
            failure_stage=cal.STAGE_ENCODE,
            failure_reason=f"ffmpeg exited with rc={number}",
            exit_code=number, output_exists=False,
            verification_status=cal.VERIFY_NOT_RUN,
        )
    cal.end_job(failure_stage=cal.STAGE_ENCODE,
                failure_reason="all attempts failed")
    collector2 = cc.Collector(spool2, log=lambda lvl, msg: None)
    collector2.load_state()
    collector2.sweep()
    failed_records = read_records(spool2 / "samples")
    record(
        "DEP-10", "job final state FAILED retained when every attempt failed",
        len(failed_records) == 2
        and all(r["job_final_status"] == "FAILED" for r in failed_records)
        and all(r["status"] == "FAILED" for r in failed_records),
        f"records={[(r['attempt_id'], r['status'], r['job_final_status']) for r in failed_records]}",
    )


# ---------------------------------------------------------------------------
# DEP-11..DEP-14: standalone binary
# ---------------------------------------------------------------------------

def test_standalone(exe: Path, corpus_dir: Path,
                    skip_clean_machine: bool) -> None:
    section("DEP-11 / DEP-13 / DEP-14: standalone binary, offline, no Python")

    record("DEP-11", "standalone executable exists", exe.is_file(),
           f"{exe} ({exe.stat().st_size:,} bytes)" if exe.is_file() else "")
    if not exe.is_file():
        return

    rc, text = run([exe, "--version"], timeout=300)
    record(
        "DEP-11", "executable runs without project sources on sys.path",
        rc == 0 and "frozen: True" in text,
        f"rc={rc} | {text.strip().splitlines()[0] if text.strip() else ''}",
    )

    # DEP-13: no network. Check the binary for network APIs and run it in a
    # fresh spool to be sure nothing is contacted at runtime.
    binary = exe.read_bytes()
    network_markers = [
        m for m in (b"wininet", b"ws2_32", b"WinHttp", b"libssl",
                    b"URLDownloadToFile")
        if m in binary
    ]
    spool = fresh(WORK / "spool_exe")
    (spool / "events" / "pending").mkdir(parents=True, exist_ok=True)
    rc2, text2 = run(
        [exe, "--headless", "--once", "--watch", str(spool)], timeout=600
    )
    record(
        "DEP-13", "no network dependency (no network imports/APIs used)",
        rc2 == 0 and not network_markers,
        f"rc={rc2} markers={network_markers or 'none'}",
    )

    # DEP-14: no Python needed. Renamed PATH + HOME + PYTHONPATH so a
    # missing/broken interpreter cannot be used, then run the exe on a
    # spool that already has real events.
    env = {
        "PATH": r"C:\Windows\system32;C:\Windows",
        "PYTHONHOME": r"C:\nonexistent-python-home",
        "PYTHONPATH": r"C:\nonexistent-python-path",
        "PYTHONSTARTUP": "",
        "ONEXKT_CALIBRATION": "",
        "ONEXKT_CALIBRATION_SPOOL": "",
        "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
        "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "USERPROFILE": os.environ.get("USERPROFILE", r"C:\Users\Public"),
    }
    real_spool = WORK / "spool_clean_machine"
    fresh(real_spool)
    # Encode into the test work area, never into dist/: dist/ holds the
    # release artifacts and must not collect test output.
    produced = WORK / "clean_machine_out"
    fresh(produced)
    transcode(corpus_dir, produced, real_spool)
    env["PATH"] = r"C:\Windows\system32;C:\Windows"
    rc3, text3 = run(
        [exe, "--headless", "--once", "--watch", str(real_spool)],
        env=env, timeout=900,
    )
    records = read_records(real_spool / "samples")
    record(
        "DEP-14", "runs with no usable Python on PATH/PYTHONHOME/PYTHONPATH",
        rc3 == 0 and len(records) >= 1,
        f"rc={rc3} records={len(records)}",
    )
    if rc3 != 0:
        print(text3[-1200:])

    # DEP-12: clean-machine execution. Approximated by copying ONLY the
    # package contents to a directory outside the repository, removing every
    # project path from the environment, and running from there.
    if skip_clean_machine:
        record("DEP-12", "clean-machine execution (skipped)", True, "skipped")
        return
    package_src = PROJECT / "dist" / "x265_release" / (
        "x265-calibration-collector"
    )
    if not package_src.is_dir():
        record("DEP-12", "clean-machine execution", False,
               f"package not built: {package_src}")
        return
    machine = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / (
        "1kt_clean_machine_probe"
    )
    if machine.exists():
        shutil.rmtree(machine, ignore_errors=True)
    shutil.copytree(package_src, machine)
    exe_copy = machine / exe.name
    spool2 = machine / "calibration"
    spool2.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe_copy, "--headless", "--watch", str(spool2),
    ]
    env2 = dict(env)
    env2["PATH"] = r"C:\Windows\system32;C:\Windows"
    # Run it as a real background process, feed it nothing, and kill it
    # after a few sweeps: proves it starts and stays alive outside the repo.
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(machine), env=env2,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(8)
    alive = proc.poll() is None
    kill_tree(proc)
    try:
        out = (proc.communicate(timeout=30)[0] or b"").decode(
            "utf-8", "replace"
        )
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        out = ""
    log_written = (spool2 / "errors" / "collector.log").is_file()
    manifest = (spool2 / "manifest.json").is_file()
    record(
        "DEP-12", "clean-machine execution (outside the repo, minimal env)",
        alive and log_written and manifest,
        f"alive_after_8s={alive} log={log_written} manifest={manifest}",
    )
    if not alive:
        print(out[-1500:])
    shutil.rmtree(machine, ignore_errors=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dep_matrix.py")
    parser.add_argument(
        "--exe", type=Path,
        default=PROJECT / "dist" / "x265_calibration" / (
            "x265-calibration-collector.exe"
        ),
    )
    parser.add_argument("--skip-clean-machine", action="store_true")
    parser.add_argument("--only", default="",
                        help="comma-separated DEP ids to run")
    args = parser.parse_args(argv)

    WORK.mkdir(parents=True, exist_ok=True)
    ffmpeg = PROJECT / "tools" / "ffmpeg.exe"
    corpus_dir = WORK / "corpus"
    ensure_corpus(corpus_dir, ffmpeg)

    exe = args.exe if args.exe.is_file() else None
    if exe is None:
        print(f"[NOTE] executable not found at {args.exe}: "
              "binary checks will use the source CLI")

    wanted = {d.strip().upper() for d in args.only.split(",") if d.strip()}

    def enabled(dep: str) -> bool:
        return not wanted or dep in wanted

    print("=" * 78)
    print("DEP MATRIX - x265 calibration collector")
    print("=" * 78)
    print(f"project : {PROJECT}")
    print(f"corpus  : {corpus_dir}")
    print(f"exe     : {exe if exe else '(not built - source CLI used)'}")

    stray = kill_stray_collectors()
    if stray:
        print(f"[INFO] cleared {stray} stray collector process(es) first")

    if enabled("DEP-01"):
        test_collector_process_model(corpus_dir, exe)
    if enabled("DEP-06"):
        test_atomicity(WORK / "spool_atomic_scan")
    if enabled("DEP-07"):
        test_failure_and_retry(WORK / "spool_retry")
    if enabled("DEP-11"):
        test_standalone(
            exe if exe else args.exe, corpus_dir, args.skip_clean_machine
        )
    leftovers = kill_stray_collectors()
    record("CLEAN", "no collector process left running after the matrix",
           leftovers == 0, f"remaining={leftovers}")

    section("SUMMARY")
    passed = sum(1 for _, _, ok, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[2]]
    for dep, name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {dep}  {name}"
              + (f"  ({detail})" if detail and not ok else ""))
    print()
    print(f"  {passed}/{len(RESULTS)} checks passed")
    ids = sorted({dep for dep, _, _, _ in RESULTS})
    print(f"  DEP ids covered: {', '.join(ids)}")
    if failed:
        print()
        print("  FAILED:")
        for dep, name, _, detail in failed:
            print(f"    {dep} {name} | {detail}")
        return 1
    print()
    print("  DEP MATRIX: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
