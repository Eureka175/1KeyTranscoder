#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run every ``tests/audio_sync_p4`` audit script and aggregate the verdicts.

These are *standalone audit scripts*, not pytest modules: each has its own
``main()`` and writes a JSON report via ``--json``.  ``pytest`` therefore
collects zero tests from that directory (verified: exit 5, "no tests ran").
This driver runs each one as its own process, so a crash in one audit cannot
hide the verdicts of the others, and aggregates the counts from the JSON.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "tests" / "audio_sync_p4"
OUT = (REPO / "work" / "regression_r087" / "out" / "p4_suites.json")
LOGS = REPO / "work" / "regression_r087" / "logs"

SCRIPTS = [
    "test_baseline_hash_portability.py",
    "test_downstream_state.py",
    "test_evidence_integrity.py",
    "test_production_eligibility.py",
    "test_production_semantics.py",
    "test_production_sync.py",
    "test_time_varying_exec.py",
]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in SCRIPTS:
        script = SUITE / name
        jpath = LOGS / (script.stem + ".json")
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, str(script), "--json", str(jpath)],
            cwd=str(REPO), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        dt = round(time.perf_counter() - t0, 1)
        (LOGS / (script.stem + ".log")).write_text(
            r.stdout or "", encoding="utf-8", errors="replace")
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        verdict = next((ln for ln in reversed(tail)
                        if ln.startswith("RESULT:")), "(no RESULT line)")
        row = {"script": name, "rc": r.returncode, "elapsed_s": dt,
               "verdict": verdict.strip(), "json": str(jpath)}
        if jpath.is_file():
            try:
                data = json.loads(jpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                row["cases"] = data.get("cases")
                row["fail"] = data.get("fail")
                row["counts"] = data.get("counts")
        rows.append(row)
        print(f"{name:<40} rc={r.returncode} {dt:>7}s  {verdict.strip()}",
              flush=True)

    n_fail = sum(1 for r in rows if r["rc"] != 0)

    def _count(v):
        # `cases` is an int in most scripts but a list in the one that reports
        # per-case rows; coercing blindly crashed the aggregator after every
        # audit had already passed.
        if isinstance(v, list):
            return len(v)
        if isinstance(v, int):
            return v
        return 0

    total_cases = sum(_count(r.get("cases")) for r in rows)
    OUT.write_text(json.dumps(
        {"scripts": rows, "total_cases": total_cases,
         "scripts_failing": n_fail}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nscripts={len(rows)} cases={total_cases} failing_scripts={n_fail}")
    print(f"-> {OUT}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
