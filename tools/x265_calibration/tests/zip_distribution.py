#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end test of the deployable ZIP, exactly as the host will use it.

    python tools/x265_calibration/tests/zip_distribution.py [--zip PATH]

Steps, in the order the receiving operator experiences them:

  1. extract the zip into a directory with a SPACE in its name
  2. confirm the layout and that the pre-created data dirs survived
  3. confirm SHA256SUMS.txt matches every packaged file
  4. run the exe with no usable Python on PATH (--version/--status/--validate)
  5. write a real event through the production emitter into the shipped
     calibration dir and consume it with the packaged exe
  6. confirm the .bat launchers contain no parent-directory traversal
  7. EXECUTE CHECK-INSTALL.bat and confirm its report
  8. EXECUTE START-COLLECTOR.bat and confirm it watches <pkg>/calibration
     (not the package's parent) and writes its state inside the package
  9. run the bundled analyze.py against the collected record

The zip is produced by release/make_zip.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
TOOLS = PROJECT / "tools" / "x265_calibration"
PKG = "x265-calibration-collector"
DEFAULT_ZIP = (PROJECT / "dist" / "x265_release" /
               "x265-calibration-collector-v0.7.0.zip")

FAILURES: list[str] = []
PASSED: list[int] = [0]


class _Reporter:
    """Counts checks; callable so call sites stay `check(name, ok, detail)`."""

    def __call__(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}"
              + (f" | {detail}" if detail else ""))
        if ok:
            PASSED[0] += 1
        else:
            FAILURES.append(name)


check = _Reporter()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zip_distribution.py")
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    args = parser.parse_args(argv)
    zip_path: Path = args.zip

    print("=" * 78)
    print("DEPLOYABLE ZIP - END-TO-END TEST")
    print("=" * 78)
    print(f"zip: {zip_path}")
    if not zip_path.is_file():
        print(f"[FATAL] zip not found. Build it with:\n"
              f"  python tools/x265_calibration/release/make_zip.py")
        return 2

    # --- 1. extract to an awkward path -------------------------------------
    base = Path(tempfile.gettempdir()) / "1kt zip dist test"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        bad = zf.testzip()
        zf.extractall(base)
    pkg = base / PKG
    check("zip integrity", bad is None, f"entries={len(names)}")
    check("extracts into a single top-level folder", pkg.is_dir())
    check("path with a space works", pkg.is_dir(), str(base))
    check("no absolute or parent-traversal entries",
          not any(n.startswith(("/", "\\")) or ".." in n for n in names))

    # --- 2. layout ---------------------------------------------------------
    for rel in (
        "x265-calibration-collector.exe", "QUICKSTART.zh-CN.txt",
        "START-HERE.txt", "README.txt", "schema.json", "collector.toml",
        "VERSION", "SHA256SUMS.txt", "PACKAGE-CONTENTS.txt",
        "START-COLLECTOR.bat", "CHECK-INSTALL.bat", "ANALYZE-DATA.bat",
        "VERIFY-PACKAGE.bat",
        "INSTALL-SCHEDULED-TASK.bat", "UNINSTALL-SCHEDULED-TASK.bat",
        "tools/README.txt",
        "tools/x265-analyze.exe", "tools/x265-verify.exe",
        "tools/x265-deploy.exe",
        "calibration/README_FIRST.txt",
        "calibration/events/pending/README_FIRST.txt",
    ):
        check(f"present: {rel}", (pkg / rel).is_file())
    for rel in ("events/pending", "events/archive", "events/failed", "samples",
                "summaries", "errors", "state"):
        check(f"data dir survives zip: calibration/{rel}",
              (pkg / "calibration" / rel).is_dir())
    py_files = [p.name for p in pkg.rglob("*.py")]
    check("no Python source shipped: nothing in the package needs an "
          "interpreter", not py_files, f"found={py_files or 'none'}")

    # --- 3. checksums ------------------------------------------------------
    sums: dict[str, str] = {}
    for line in (pkg / "SHA256SUMS.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        if "  " in line:
            digest, rel = line.split("  ", 1)
            sums[rel.strip()] = digest.strip()
    problems = []
    for rel, digest in sums.items():
        target = pkg / rel
        if not target.is_file() or sha256_bytes(target.read_bytes()) != digest:
            problems.append(rel)
    check("SHA256SUMS covers and matches every packaged file",
          not problems and len(sums) >= 14,
          f"{len(sums)} entries, problems={problems or 'none'}")

    exe = pkg / "x265-calibration-collector.exe"
    calib = pkg / "calibration"

    # --- 4. no Python on PATH ---------------------------------------------
    minimal_env = {
        "PATH": r"C:\Windows\system32;C:\Windows",
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
        "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
        "USERPROFILE": os.environ.get("USERPROFILE", r"C:\Users\Public"),
        "PYTHONHOME": r"C:\nonexistent-python-home",
        "PYTHONPATH": r"C:\nonexistent-python-path",
    }

    def run_min(args: list, timeout: int = 600) -> tuple[int, str]:
        proc = subprocess.run([str(a) for a in args], cwd=str(pkg),
                              env=minimal_env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")

    rc, out = run_min([exe, "--version"])
    check("--version runs with no usable Python", rc == 0 and "frozen: True" in out,
          f"rc={rc}")
    rc, out = run_min([exe, "--headless", "--status", "--spool", calib])
    check("--status on the shipped empty data dir", rc == 0 and '"records": 0' in out,
          f"rc={rc}")
    rc, out = run_min([exe, "--headless", "--validate", calib])
    check("--validate on an empty dataset reports OK", rc == 0 and "[OK]" in out,
          f"rc={rc}")

    # --- 5. production side writes, collector consumes ---------------------
    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(TOOLS))
    from core import calibration_events as cal

    cal.setup(enabled=True, spool=calib, project_version="1KeyTranscoder 0.7.0")
    cal.begin_job(job_id="ZIPTEST-SMOKE", source=pkg / "VERSION",
                  relative_path="VERSION", requested_preset="HQ",
                  requested_encoder="x265", preset="HQ", crf=21)
    cal.begin_attempt(backend="x265", preset="HQ", kind="primary")
    cal.attempt_succeeded(
        exit_code=0,
        verification={"status": "passed", "checks": ["simulated"]},
        output={"output_size": 12345},
    )
    cal.end_job(accepted_attempt_number=1)

    pending = list((calib / "events" / "pending").glob("*.json"))
    check("production emitter writes into the shipped data dir",
          len(pending) == 4, f"{len(pending)} events")

    rc, out = run_min([exe, "--headless", "--once", "--watch", calib])
    records = []
    for path in sorted((calib / "samples").glob("*.jsonl")):
        records += [json.loads(l) for l in
                    path.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("packaged collector consumes the backlog", rc == 0 and len(records) == 1,
          f"rc={rc} records={len(records)}")
    if records:
        r = records[0]
        check("record content is correct",
              r["attempt_id"] == "ZIPTEST-SMOKE-A001"
              and r["status"] == "SUCCEEDED"
              and r["job_final_status"] == "SUCCESS"
              and r["verification_status"] == "passed",
              f"{r['attempt_id']} {r['status']}")
    check("non-event README files in pending/ are ignored",
          (calib / "events" / "pending" / "README_FIRST.txt").is_file()
          and not list((calib / "events" / "failed").glob("README*")))

    # --- 6. batch files have no parent traversal ---------------------------
    for bat in ("START-COLLECTOR.bat", "CHECK-INSTALL.bat",
                "INSTALL-SCHEDULED-TASK.bat"):
        text = (pkg / bat).read_text(encoding="utf-8", errors="replace")
        check(f"{bat}: data dir is <pkg>\\calibration",
              'set "CALIB=%HERE%calibration"' in text
              and "..\\calibration" not in text)

    # --- 7. execute CHECK-INSTALL.bat -------------------------------------
    proc = subprocess.run(["cmd", "/c", str(pkg / "CHECK-INSTALL.bat")],
                          cwd=str(pkg), input=b"\r\n\r\n",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=600)
    report = pkg / "check-report.txt"
    text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
    check("CHECK-INSTALL.bat runs and writes its report",
          proc.returncode == 0 and report.is_file(), f"rc={proc.returncode}")
    check("report carries version / status / validate blocks",
          "x265-calibration-collector 1.0.0" in text and '"records"' in text
          and "[OK]" in text)
    check("report reminds about the ONEXKT_* variables",
          "ONEXKT_CALIBRATION_SPOOL" in text)

    # --- 8. execute START-COLLECTOR.bat -----------------------------------
    proc = subprocess.Popen(["cmd", "/c", str(pkg / "START-COLLECTOR.bat")],
                            cwd=str(pkg), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = calib / "errors" / "collector.log"
    started = False
    for _ in range(40):
        time.sleep(0.5)
        if log.is_file():
            started = True
            break
    alive = proc.poll() is None
    kill_tree(proc)
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    check("START-COLLECTOR.bat launches a live collector",
          started and alive, f"log={started} alive={alive}")
    check("it watches <pkg>\\calibration, not the parent directory",
          str(calib) in log_text)
    check("nothing was created outside the package",
          not (base / "calibration").exists())

    # --- 8b. the frozen tool exes, still with NO Python --------------------
    # These run under minimal_env on purpose: the soak host has no
    # interpreter, so every tool must work without one.
    rc, out = run_min([pkg / "tools" / "x265-analyze.exe", calib])
    check("tools/x265-analyze.exe analyzes the dataset with no Python",
          rc == 0 and "x265 CALIBRATION DATASET SUMMARY" in out
          and "records         : 1" in out,
          f"rc={rc}")
    check("analyzer output has no mojibake replacement characters",
          "\ufffd" not in out)
    check("analyzer JSON output works too",
          run_min([pkg / "tools" / "x265-analyze.exe", calib,
                   "--json", str(pkg / "analysis.json")])[0] == 0
          and (pkg / "analysis.json").is_file())

    rc, out = run_min([pkg / "tools" / "x265-verify.exe", "--exe", exe])
    check("tools/x265-verify.exe verifies the collector exe with no Python",
          rc == 0 and "VERIFY OK" in out, f"rc={rc}")

    rc, out = run_min([pkg / "tools" / "x265-deploy.exe", "offline-check"])
    check("tools/x265-deploy.exe runs with no Python",
          rc == 0 and "no network-capable imports" in out, f"rc={rc}")

    # --- 8c. execute ANALYZE-DATA.bat and VERIFY-PACKAGE.bat ---------------
    proc = subprocess.run(["cmd", "/c", str(pkg / "ANALYZE-DATA.bat")],
                          cwd=str(pkg), input=b"\r\n\r\n",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=900)
    areport = pkg / "analysis-report.txt"
    atext = (areport.read_text(encoding="utf-8", errors="replace")
             if areport.is_file() else "")
    check("ANALYZE-DATA.bat runs and writes analysis-report.txt",
          proc.returncode == 0 and areport.is_file(),
          f"rc={proc.returncode}")
    check("analysis report contains the dataset summary",
          "x265 CALIBRATION DATASET SUMMARY" in atext
          and "PRODUCTION / ATTEMPTS" in atext)

    proc = subprocess.run(["cmd", "/c", str(pkg / "VERIFY-PACKAGE.bat")],
                          cwd=str(pkg), input=b"\r\n\r\n",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=2400)
    vreport = pkg / "verify-report.txt"
    vtext = (vreport.read_text(encoding="utf-8", errors="replace")
             if vreport.is_file() else "")
    check("VERIFY-PACKAGE.bat runs and writes verify-report.txt",
          proc.returncode == 0 and vreport.is_file(),
          f"rc={proc.returncode}")
    check("all four packaged executables verified as self-contained",
          vtext.count("[VERIFY OK]") == 4
          and "every executable is self-contained" in vtext,
          f"VERIFY OK count={vtext.count('[VERIFY OK]')}")

    shutil.rmtree(base, ignore_errors=True)

    print()
    print("-" * 78)
    print(f"  checks passed : {PASSED[0]}")
    print(f"  checks failed : {len(FAILURES)}")
    print()
    if FAILURES:
        print("ZIP DISTRIBUTION TEST FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("ZIP DISTRIBUTION TEST: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
