#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robustness / recovery tests for the collector (v0.7.0 task §30).

Verifies the six required fault cases:

    R1 Collector process kill        (mid-sweep, real process)
    R2 Power loss simulation         (torn JSONL line, torn event file)
    R3 Partial event                 (quarantined, never counted)
    R4 Partial JSONL                 (trailing line repaired on restart)
    R5 Duplicate event               (idempotent, counted once)
    R6 Disk full simulation          (persist fails, event stays pending)
       + Restart                     (state fully recovered afterwards)

Usage:  python tools/x265_calibration/tests/robustness.py
"""

from __future__ import annotations

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
from core import calibration_events as cal          # noqa: E402

WORK = PROJECT / "work" / "robustness"
RESULTS: list[tuple[str, str, bool, str]] = []


def record(case: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((case, name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {case} {name}"
          + (f" | {detail}" if detail else ""))


def fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_records(samples_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(samples_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND its children.

    On Windows ``Popen.kill()`` only terminates the process itself: a
    onefile PyInstaller exe runs a bootloader parent plus the real child,
    so killing only the parent leaves a collector running in the
    background — which then consumes the next test's spool. taskkill /T
    removes the whole tree.
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
    """Defensive: no collector may be watching anything while tests run."""
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


def emit_job(root: Path, job_id: str, *, fail_first: bool = False,
             count: int = 1) -> int:
    """Emit a complete job (optionally with a failed first attempt).

    Events are written with the same atomic rename contract the producer
    uses, one make_event_id per event, so a large synthetic backlog does
    not depend on process-local session state. Returns the number of
    attempt records a collector should produce for this job.
    """
    source = PROJECT / "VERSION"
    pending = root / "events" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    now = cal.utc_now_iso()

    def write(event_type: str, payload: dict, attempt_id: str = "") -> None:
        event_id = cal.make_event_id(event_type, job_id, attempt_id or None)
        body = dict(payload)
        body.update({
            "event_id": event_id,
            "schema_version": cal.EVENT_SCHEMA_VERSION,
            "event_type": event_type,
            "timestamp": now,
            "producer": cal.PRODUCER,
            "project_version": "1KeyTranscoder 0.7.0",
            "host": "fixture",
            "pid": 1,
            "job_id": job_id,
        })
        if attempt_id:
            body["attempt_id"] = attempt_id
        cal.atomic_write_json(pending / f"{event_id}.json", body)

    write("job_started", {
        "source_id": job_id,
        "source_path": str(source),
        "relative_path": "VERSION",
        "source_sha256": cal.sha256_file(source),
        "start_time": now,
        "requested_preset": "hq",
        "requested_encoder": "x265",
        "source_size_bytes": source.stat().st_size,
        "preset": "hq",
        "crf": 21,
    })

    records = 0
    number = 0
    if fail_first:
        number += 1
        attempt_id = f"{job_id}-A{number:03d}"
        write("attempt_started", {
            "attempt_number": number, "backend": "x265", "preset": "hq",
            "start_time": now, "attempt_kind": "primary",
        }, attempt_id)
        write("attempt_failed", {
            "attempt_number": number, "backend": "x265", "status": "FAILED",
            "end_time": cal.utc_now_iso(), "failure_stage": "encode_failed",
            "failure_reason": "simulated encoder failure", "exit_code": 1,
            "output_exists": False, "verification_status": "not_run",
            "wall_time_sec": 0.1, "attempt_kind": "primary",
        }, attempt_id)
        records += 1
    for _ in range(count):
        number += 1
        attempt_id = f"{job_id}-A{number:03d}"
        kind = "primary" if number == 1 else "retry"
        write("attempt_started", {
            "attempt_number": number, "backend": "x265", "preset": "hq",
            "start_time": now, "attempt_kind": kind,
        }, attempt_id)
        write("attempt_succeeded", {
            "attempt_number": number, "backend": "x265", "status": "SUCCEEDED",
            "end_time": cal.utc_now_iso(), "exit_code": 0,
            "wall_time_sec": 0.2, "attempt_kind": kind,
            "output_size": 1000 * number,
            "verification_result": {"status": "passed", "checks": ["sim"]},
            "verification_status": "passed",
        }, attempt_id)
        records += 1
    accepted = f"{job_id}-A{number:03d}"
    write("job_succeeded", {
        "status": "SUCCESS", "end_time": cal.utc_now_iso(),
        "accepted_attempt_id": accepted, "accepted_attempt_number": number,
        "attempt_count": number, "failed_attempt_count": 1 if fail_first else 0,
        "start_time": now, "wall_time_sec": 0.3,
    })
    return records


def sweep(root: Path) -> cc.Collector:
    collector = cc.Collector(root, log=lambda lvl, msg: None)
    collector.load_state()
    collector.sweep()
    return collector


# ---------------------------------------------------------------------------
# R1: collector process kill
# ---------------------------------------------------------------------------

def test_process_kill(exe: Path | None) -> None:
    """Kill a RUNNING collector that still has a backlog to drain.

    A large synthetic backlog guarantees the kill lands mid-consumption,
    so the test really exercises "already persisted data is not lost,
    pending events remain recoverable".
    """
    root = fresh(WORK / "r1_spool")
    expected_attempts = 0
    for index in range(120):
        expected_attempts += emit_job(
            root, f"KILL-{index:03d}", fail_first=(index % 10 == 0)
        )
    pending_before = len(list((root / "events" / "pending").glob("*.json")))
    record(
        "R1", "simulated backlog built",
        expected_attempts > 0 and pending_before > expected_attempts,
        f"{expected_attempts} attempt records expected from "
        f"{pending_before} pending events",
    )

    # A kill during the very first sweep would land before any record is
    # written, which tests "nothing lost" but not "already persisted data
    # survives". So let one sweep complete first: from then on the dataset
    # is non-empty and half the backlog is still pending.
    if exe is not None and exe.is_file():
        _run_once(exe, root)
        command = [str(exe), "--headless", "--watch", str(root),
                   "--poll-interval", "0.2", "--job-final-timeout", "0"]
    else:
        _run_once_source(root)
        command = [sys.executable, "-m", "collector.collector_cli",
                   "--headless", "--watch", str(root),
                   "--poll-interval", "0.2", "--job-final-timeout", "0"]

    # Re-publish the consumed half of the backlog, so a real kill can land
    # mid-sweep with records already on disk.
    for path in sorted((root / "events" / "archive").rglob("*.json"))[:250]:
        shutil.copy2(path, root / "events" / "pending" / path.name)
    already_persisted = len(read_records(root / "samples"))
    pending_before = len(list((root / "events" / "pending").glob("*.json")))

    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(TOOLS) if exe is None or not exe.is_file() else None,
    )
    alive_at_kill = False
    partial: list[dict] = []
    pending_after_kill: list[Path] = []
    # Kill the moment the run is provably underway but unfinished: some
    # events already consumed, others still waiting. A fixed sleep would
    # be a race; this waits for real progress and a real remainder.
    threshold = max(1, pending_before // 10)
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(0.05)
        pending_now = list((root / "events" / "pending").glob("*.json"))
        if len(pending_now) <= pending_before - threshold and pending_now:
            partial = read_records(root / "samples")
            pending_after_kill = pending_now
            alive_at_kill = proc.poll() is None
            kill_tree(proc)
            break
        if proc.poll() is not None:
            break
    if proc.poll() is None:
        alive_at_kill = True
        kill_tree(proc)

    # restart through the same entry point until the backlog is drained
    deadline = time.time() + 300
    while time.time() < deadline:
        if exe is not None and exe.is_file():
            rc, out = _run_once(exe, root)
        else:
            rc, out = _run_once_source(root)
        if rc != 0:
            break
        if not list((root / "events" / "pending").glob("*.json")):
            break

    final = read_records(root / "samples")
    pending_final = list((root / "events" / "pending").glob("*.json"))

    record(
        "R1", "collector killed mid-run: persisted data survived the kill",
        alive_at_kill
        and len(partial) >= already_persisted
        and len(pending_after_kill) > 0,
        f"persisted_before_kill={len(partial)} "
        f"(was {already_persisted} before the kill) "
        f"still_pending={len(pending_after_kill)}",
    )
    record(
        "R1", "restart finishes the backlog with no duplicates or loss",
        rc == 0 and len(final) == expected_attempts and not pending_final
        and len({r["attempt_id"] for r in final}) == len(final),
        f"records={len(final)} expected={expected_attempts} "
        f"pending={len(pending_final)} unique="
        f"{len({r['attempt_id'] for r in final})}",
    )


def _run_once(exe: Path, root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(exe), "--headless", "--once", "--watch", str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
    )
    return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")


def _run_once_source(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "collector.collector_cli", "--headless",
         "--once", "--watch", str(root)],
        cwd=str(TOOLS), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=300,
    )
    return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# R2 / R3: power loss (torn event file, torn JSONL line)
# ---------------------------------------------------------------------------

def test_power_loss() -> None:
    root = fresh(WORK / "r2_spool")
    emit_job(root, "POWER-001")
    sweep(root)
    records = read_records(root / "samples")
    sample_file = sorted((root / "samples").glob("*.jsonl"))[0]

    # power loss while a record is being appended: half a line on disk
    with sample_file.open("ab") as fh:
        fh.write(b'{"record_version": "1.0", "attempt_id": "TORN-A001", "sta')
        fh.flush()
        os.fsync(fh.fileno())

    # power loss while an event was being written: a left-over .tmp
    (root / "events" / "pending" / ".half-written.json.tmp").write_text(
        '{"event_id": "half', encoding="utf-8"
    )
    # and a torn (but renamed) event file
    (root / "events" / "pending" / "torn_event.json").write_text(
        '{"event_id": "torn", "event_type": "attempt_sta',
        encoding="utf-8",
    )

    collector = cc.Collector(root, log=lambda lvl, msg: None)
    collector.load_state()
    collector.sweep()

    after = read_records(root / "samples")
    text = sample_file.read_text(encoding="utf-8")
    torn_gone = "TORN-A001" not in text and text.endswith("\n")
    record(
        "R2", "torn JSONL tail dropped on restart, good records kept",
        len(after) == len(records) and torn_gone,
        f"records={len(after)} (was {len(records)}), tail_clean={torn_gone}",
    )
    record(
        "R3", "partial/torn event files quarantined, never counted",
        len(after) == len(records)
        and (root / "events" / "failed" / "torn_event.json").is_file()
        and (root / "events" / "pending" / ".half-written.json.tmp").is_file(),
        f"failed={[p.name for p in (root / 'events' / 'failed').glob('*.json')]}",
    )

    # the quarantined event must not be re-processed on a later sweep
    collector2 = cc.Collector(root, log=lambda lvl, msg: None)
    collector2.load_state()
    counters = collector2.sweep()
    after2 = read_records(root / "samples")
    record(
        "R3", "quarantined events stay out of the dataset",
        len(after2) == len(records) and counters.events_invalid == 0,
        f"records={len(after2)} newly_invalid={counters.events_invalid}",
    )


# ---------------------------------------------------------------------------
# R5: duplicate event
# ---------------------------------------------------------------------------

def test_duplicate() -> None:
    root = fresh(WORK / "r5_spool")
    emit_job(root, "DUP-001")
    sweep(root)
    records = read_records(root / "samples")

    # replay every archived event file into pending/ (e.g. a restore from
    # backup, or a crash between "persist" and "archive")
    replayed = 0
    for path in sorted((root / "events" / "archive").rglob("*.json")):
        shutil.copy2(path, root / "events" / "pending" / path.name)
        replayed += 1
    sweep(root)
    after = read_records(root / "samples")

    # an attempt record arriving twice must also be ignored
    sample_file = sorted((root / "samples").glob("*.jsonl"))[0]
    line = sample_file.read_text(encoding="utf-8").splitlines()[0]
    with sample_file.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    probe = cc.Collector(root, log=lambda lvl, msg: None)
    probe.load_state()
    probe.sweep()

    record(
        "R5", "replayed duplicate events are ignored (idempotent)",
        replayed > 0 and len(after) == len(records),
        f"replayed={replayed} records {len(records)} -> {len(after)}",
    )
    status = probe.status()
    record(
        "R5", "collector reports the duplicate it skipped",
        status["dataset"]["records"] >= len(records),
        f"records={status['dataset']['records']}",
    )


# ---------------------------------------------------------------------------
# R6: disk full simulation + restart
# ---------------------------------------------------------------------------

def test_disk_full() -> None:
    """Disk-full simulation inside a REAL collector process.

    The child process monkeypatches ``collector_core.append_jsonl_durable``
    so the persist step raises OSError(ENOSPC) exactly once, then reports
    the result as JSON. The event must stay in pending/ and be recovered by
    the next healthy sweep with no loss and no duplicate.
    """
    root = fresh(WORK / "r6_spool")
    emit_job(root, "FULL-001")
    pending_before = len(list((root / "events" / "pending").glob("*.json")))

    child = WORK / "disk_full_child.py"
    child.write_text(
        "import json, os, sys\n"
        "sys.path.insert(0, r'%s')\n"
        "from collector import collector_core as cc\n"
        "root = sys.argv[1]\n"
        "real = cc.append_jsonl_durable\n"
        "calls = {'n': 0}\n"
        "def failing(path, obj):\n"
        "    calls['n'] += 1\n"
        "    raise OSError(28, 'No space left on device (simulated)')\n"
        "cc.append_jsonl_durable = failing\n"
        "c = cc.Collector(root, log=lambda lvl, msg: None)\n"
        "c.load_state()\n"
        "failed_swaps = 0\n"
        "try:\n"
        "    c.sweep()\n"
        "except Exception:\n"
        "    failed_swaps += 1\n"
        "cc.append_jsonl_durable = real\n"
        "c2 = cc.Collector(root, log=lambda lvl, msg: None)\n"
        "c2.load_state()\n"
        "c2.sweep()\n"
        "print(json.dumps({'writes_attempted': calls['n'],\n"
        "                  'sweep_swaps': failed_swaps,\n"
        "                  'records': c2.status()['dataset']['records'],\n"
        "                  'pending_after': len(list(\n"
        "                      (cc.Path(root) / 'events' / 'pending')"
        ".glob('*.json')))}))\n"
        % str(TOOLS),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(child), str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600,
    )
    text = (proc.stdout or b"").decode("utf-8", "replace")
    payload = None
    for line in text.splitlines():
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    recovered = read_records(root / "samples")

    record(
        "R6", "disk-full persist failure keeps the event pending (no loss)",
        proc.returncode == 0 and payload is not None
        and payload["writes_attempted"] > 0
        and payload["records"] == 1
        and payload["pending_after"] == 0
        and len(recovered) == 1,
        f"rc={proc.returncode} child={payload} "
        f"pending_before={pending_before} records={len(recovered)}",
    )
    if payload is None:
        print(text[-1200:])
    record(
        "R6", "no duplicate record after the disk-full retry",
        len({r["attempt_id"] for r in recovered}) == len(recovered) == 1,
        f"attempts={[r['attempt_id'] for r in recovered]}",
    )


# ---------------------------------------------------------------------------
# R-extra: lock + concurrent collectors
# ---------------------------------------------------------------------------

def test_lock() -> None:
    root = fresh(WORK / "lock_spool")
    first = cc.Collector(root, log=lambda lvl, msg: None)
    lock = cc.SpoolLock(root)
    ok, why = lock.acquire()
    second_ok, second_why = cc.SpoolLock(root).acquire()
    lock.release()
    third = cc.SpoolLock(root)
    third_ok, _ = third.acquire()
    third.release()
    record(
        "LOCK", "one collector per calibration root (stale locks released)",
        ok and not second_ok and third_ok,
        f"first={ok} second={second_ok} after_release={third_ok}",
    )
    del first


# ---------------------------------------------------------------------------
# R-extra: production unaffected by a broken collector
# ---------------------------------------------------------------------------

def test_production_unaffected() -> None:
    """A collector that cannot even create its spool must not fail an encode."""
    root = fresh(WORK / "hostile_spool")
    # make the pending directory a FILE, so every event write must fail
    broken = root / "events"
    if broken.exists():
        shutil.rmtree(broken, ignore_errors=True)
    broken.write_text("not a directory", encoding="utf-8")

    result = cal.setup(enabled=True, spool=root)
    cal.begin_job(
        job_id="HOSTILE-001", source=PROJECT / "VERSION",
        relative_path="VERSION", requested_preset="hq",
        requested_encoder="x265",
    )
    cal.begin_attempt(backend="x265", preset="hq")
    written = cal.attempt_succeeded(
        exit_code=0, verification={"status": "passed"}
    )
    cal.end_job(accepted_attempt_number=1)
    record(
        "SAFE", "hostile spool: every emit returns None, nothing raises",
        result is True and written is None,
        f"setup={result} event_id={written}",
    )

    # and the production CLI still exits 0 with a hostile spool
    corpus = WORK / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    clip = corpus / "clip.mp4"
    if not clip.is_file():
        subprocess.run([
            str(PROJECT / "tools" / "ffmpeg.exe"), "-hide_banner",
            "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=25:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
            "yuv420p", str(clip),
        ], check=True, timeout=300)
    env = dict(os.environ)
    env["ONEXKT_CALIBRATION"] = "1"
    env["ONEXKT_CALIBRATION_SPOOL"] = str(root)
    proc = subprocess.run(
        [sys.executable, str(PROJECT / "1kt.py"), "--input", str(corpus),
         "--output", str(fresh(WORK / "hostile_out")), "--encoder", "x265",
         "--preset", "hq", "--headless", "--jobs", "1"],
        cwd=str(PROJECT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=1800,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    record(
        "SAFE", "production encode succeeds with a hostile calibration spool",
        proc.returncode == 0 and "telemetry failure" in out,
        f"rc={proc.returncode} telemetry_warning="
        f"{'telemetry failure' in out}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    exe = PROJECT / "dist" / "x265_calibration" / (
        "x265-calibration-collector.exe"
    )
    print("=" * 78)
    print("ROBUSTNESS / RECOVERY TESTS (task section 30)")
    print("=" * 78)
    stray = kill_stray_collectors()
    if stray:
        print(f"[INFO] cleared {stray} stray collector process(es) first")
    test_process_kill(exe if exe.is_file() else None)
    test_power_loss()
    test_duplicate()
    test_disk_full()
    test_lock()
    test_production_unaffected()
    leftovers = kill_stray_collectors()
    record("CLEAN", "no collector process left running after tests",
           leftovers == 0, f"remaining={leftovers}")

    print()
    print("-" * 78)
    passed = sum(1 for _case, _name, ok, _detail in RESULTS if ok)
    for case, name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {case:<5} {name}"
              + (f"  ({detail})" if detail and not ok else ""))
    print()
    print(f"  {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        return 1
    print("  ROBUSTNESS: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
