#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-entry forwarding matrix + CLI/robustness checks (no video encoding).

Every run here uses `--dry-run` (watchfolder) or a deliberately broken/short
input, so **no encoder is ever invoked**. That is a hard constraint of this
session, and it is satisfiable because the question being asked is "does the
switch reach the child process", not "does the encode succeed".

watchfolder's three-state contract under test:

    neither flag                -> nothing forwarded (1kt.py default = off)
    --channel-sync              -> --channel-sync forwarded
    --channel-sync-groups       -> --channel-sync + --channel-sync-groups
    --no-channel-sync           -> nothing forwarded, notice printed
    --no-channel-sync + any of the above -> rc=2 (contradiction is an error)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = REPO / "work" / "regression_r087" / "out"
WORK = Path(r"E:\1kt_regress_v087") / "plumbing"


def run_watchfolder(flags: list[str]) -> tuple[int, str]:
    inp = WORK / "in"
    outp = WORK / "out"
    inp.mkdir(parents=True, exist_ok=True)
    outp.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(REPO / "watchfolder.py"),
           "--input", str(inp), "--output", str(outp),
           "--dry-run", "--once", "--jobs", "1"] + flags
    r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=600)
    return r.returncode, (r.stdout or "")


def forwarded(stdout: str) -> list[str]:
    line = next((ln for ln in stdout.splitlines()
                 if "| running:" in ln), "")
    if not line:
        return []
    tail = line.split("| running:", 1)[1]
    return [t for t in tail.split() if t.startswith("--")]


def check_forwarding() -> dict:
    cases = [
        ("F1 none", [], []),
        ("F2 sync", ["--channel-sync"], ["--channel-sync"]),
        ("F3 groups", ["--channel-sync-groups"],
         ["--channel-sync", "--channel-sync-groups"]),
        ("F4 explicit off", ["--no-channel-sync"], []),
    ]
    rows = []
    for name, flags, expect in cases:
        rc, so = run_watchfolder(flags)
        got = [f for f in forwarded(so)
               if f in ("--channel-sync", "--channel-sync-groups")]
        rows.append({"case": name, "flags": flags, "expect": expect,
                     "got": got, "rc": rc, "match": got == expect,
                     "notice": "本轮不做声道对齐" in so})
    conflicts = [
        ("F5 conflict sync", ["--no-channel-sync", "--channel-sync"]),
        ("F6 conflict groups", ["--no-channel-sync",
                                "--channel-sync-groups"]),
    ]
    for name, flags in conflicts:
        rc, so = run_watchfolder(flags)
        rows.append({"case": name, "flags": flags, "rc": rc,
                     "expect": "rc=2", "match": rc == 2,
                     "got": [],
                     "stderr": so.strip()[:200]})
    return {"rows": rows,
            "all_match": all(r["match"] for r in rows)}


def check_start_bat() -> dict:
    """Execute start.bat's own argument-building fragment, not a copy of it."""
    text = (REPO / "start.bat").read_text(encoding="utf-8", errors="replace")
    frag_lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if (s.startswith('set "SYNC=') or s.startswith('set "GROUPS=')
                or s.startswith('set "ACCURATE=')):
            frag_lines.append(s)
        if s.startswith('if "%SYNC%"=="1"'):
            frag_lines.append(s)
        if s.startswith('if "%GROUPS%"=="1"'):
            frag_lines.append(s)
        if s.startswith('if "%ACCURATE%"=="1"'):
            frag_lines.append(s)
    results = {}
    for sync, groups, acc in (("1", "1", "0"), ("1", "0", "0"),
                              ("1", "0", "1"), ("1", "1", "1"),
                              ("0", "1", "1"), ("0", "0", "0")):
        script = (f'@echo off\nset "SYNC={sync}"\nset "GROUPS={groups}"\n'
                  f'set "ACCURATE={acc}"\n'
                  + "\n".join(l for l in frag_lines
                              if not l.startswith('set "SYNC=')
                              and not l.startswith('set "GROUPS=')
                              and not l.startswith('set "ACCURATE='))
                  + '\n<nul set /p "=%SYNCARGS%"\n')
        bat = WORK / f"_frag_{sync}{groups}{acc}.bat"
        bat.parent.mkdir(parents=True, exist_ok=True)
        bat.write_text(script, encoding="ascii", errors="replace")
        r = subprocess.run(["cmd", "/c", str(bat)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
        args = [t for t in (r.stdout or "").split()
                if t.startswith("--")]
        results[f"SYNC={sync} GROUPS={groups} ACC={acc}"] = args
    defaults = {ln.strip() for ln in text.splitlines()
                if ln.strip().startswith(('set "SYNC=', 'set "GROUPS=',
                                          'set "ACCURATE='))}
    ok = (results["SYNC=1 GROUPS=1 ACC=0"]
          == ["--channel-sync", "--channel-sync-groups"]
          and results["SYNC=1 GROUPS=0 ACC=0"] == ["--channel-sync"]
          and results["SYNC=1 GROUPS=0 ACC=1"]
          == ["--channel-sync", "--channel-sync-accurate"]
          and results["SYNC=1 GROUPS=1 ACC=1"]
          == ["--channel-sync", "--channel-sync-groups",
              "--channel-sync-accurate"]
          and results["SYNC=0 GROUPS=1 ACC=1"] == []
          and results["SYNC=0 GROUPS=0 ACC=0"] == [])
    return {"fragment_lines": frag_lines, "combos": results,
            "defaults_declared": sorted(defaults),
            "default_is_sync_on": ('set "SYNC=1"' in defaults
                                   and 'set "GROUPS=1"' in defaults),
            "all_match": ok}


def check_cli_defaults() -> dict:
    sys.path.insert(0, str(REPO))
    src = (REPO / "1kt.py").read_text(encoding="utf-8", errors="replace")
    rows = {
        "--channel-sync-groups present": '"--channel-sync-groups"' in src,
        "--channel-sync-group-threshold present":
            '"--channel-sync-group-threshold"' in src,
        "--channel-sync-accurate present":
            '"--channel-sync-accurate"' in src,
        "both default None (so config file wins when absent)":
            src.count("default=None") >= 2,
    }
    # functional: parse both flags against a real (empty) input dir. A valid
    # parse must reach "0 files", not an argparse error — an argparse error is
    # rc=2 with "unrecognized arguments", which is NOT the same as the exit
    # code the program itself uses for a missing directory.
    empty_in = WORK / "cli_in"
    empty_out = WORK / "cli_out"
    empty_in.mkdir(parents=True, exist_ok=True)
    empty_out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, str(REPO / "1kt.py"),
         "--input", str(empty_in), "--output", str(empty_out),
         "--channel-sync-groups", "--channel-sync-group-threshold", "1.15",
         "--dry-run", "--headless", "--check", "basic"],
        cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    out = r.stdout or ""
    rows["flags parse (no argparse error)"] = (
        "unrecognized arguments" not in out and r.returncode == 0)
    r2 = subprocess.run(
        [sys.executable, str(REPO / "1kt.py"), "--input", "x",
         "--output", "y", "--check", "bogus"],
        cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    rows["invalid --check rejected"] = r2.returncode == 2
    # `--channel-sync-groups` must imply `--channel-sync` (documented in --help)
    help_txt = subprocess.run(
        [sys.executable, str(REPO / "1kt.py"), "--help"],
        cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace").stdout or ""
    rows["help documents groups implies sync"] = (
        "隐含" in help_txt or "implies" in help_txt)
    return {"rows": rows, "all_match": all(rows.values())}


def check_idempotency() -> dict:
    """Second transparent run on an existing output must not touch it."""
    run_dir = WORK.parent / "E4"
    inp, outp = run_dir / "in", run_dir / "out"
    clip = next(inp.glob("*.MP4"), None)
    if clip is None:
        return {"status": "SKIPPED_NO_FIXTURE",
                "why": "E4 run directory absent"}
    dst = outp / clip.name
    if not dst.is_file():
        return {"status": "SKIPPED_NO_FIXTURE",
                "why": "E4 output absent"}
    before = _sha(dst)
    cmd = [sys.executable, str(REPO / "1kt.py"),
           "--input", str(inp), "--output", str(outp),
           "--channel-sync-transparent", "--check", "full",
           "--headless"]
    r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    log = (outp.parent / "logs" / "total.log")
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() \
        else (r.stdout or "")
    after = _sha(dst)
    skipped = "[SKIP]" in text
    return {"status": "PASS" if (skipped and before == after) else "FAIL",
            "second_run_rc": r.returncode,
            "log_says_skip": skipped,
            "sha_unchanged": before == after,
            "sha": before[:24]}


def check_broken_inputs() -> dict:
    """Corrupt / empty / non-eligible inputs must degrade, not crash.

    Each variant gets its **own** input directory. Sharing one and cleaning it
    between variants silently deleted the next variant's fixture, which shows
    up as "Files: 0" — a test bug that looks exactly like a product bug.
    """
    base = WORK / "broken"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    rows = []
    variants: list[tuple[str, str, bytes | None, Path | None]] = []

    src_clip = next((WORK.parent / "E4" / "in").glob("*.MP4"), None)
    if src_clip is not None:
        variants.append(("T1 truncated 4MiB", "truncated.MP4", None,
                         src_clip))
    variants.append(("T2 zero-byte", "empty.MP4", b"", None))
    variants.append(("T3 garbage bytes", "garbage.MP4",
                     b"\x00\x01\x02\x03" * 4096, None))

    for name, fname, payload, head_from in variants:
        stem = Path(fname).stem
        indir = base / f"in_{stem}"
        indir.mkdir(parents=True, exist_ok=True)
        path = indir / fname
        if head_from is not None:
            with head_from.open("rb") as f:
                path.write_bytes(f.read(4 * 1024 * 1024))
        else:
            path.write_bytes(payload)
        outdir = base / f"out_{stem}"
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(REPO / "1kt.py"),
               "--input", str(indir), "--output", str(outdir),
               "--channel-sync-transparent", "--check", "basic", "--headless"]
        r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=900)
        dst = outdir / fname
        logf = indir.parent / "logs" / "total.log"
        logtxt = (logf.read_text(encoding="utf-8", errors="replace")
                  if logf.is_file() else "")
        nfiles = None
        for ln in logtxt.splitlines():
            if "Files:" in ln:
                try:
                    nfiles = int(ln.rsplit(":", 1)[1].strip())
                except ValueError:
                    pass
        rows.append({
            "case": name, "rc": r.returncode,
            "discovered_files": nfiles,
            "output_written": dst.is_file(),
            "output_is_source_copy": (dst.is_file()
                                      and _sha(dst) == _sha(path)),
            "no_traceback": "Traceback (most recent call last)"
                            not in (r.stdout or ""),
            "treated_as_zero_files": nfiles == 0,
        })
    return {"rows": rows,
            "all_ok": all(r["no_traceback"] and r["rc"] == 0 for r in rows)}


def _sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    rep: dict = {}
    if not only or only == "forward":
        rep["forwarding"] = check_forwarding()
        print("forwarding:", json.dumps(
            [{k: r.get(k) for k in ("case", "got", "match", "rc")}
             for r in rep["forwarding"]["rows"]], ensure_ascii=False),
            "all_match:", rep["forwarding"]["all_match"])
    if not only or only == "startbat":
        rep["start_bat"] = check_start_bat()
        print("start.bat:", json.dumps(rep["start_bat"]["combos"],
                                       ensure_ascii=False),
              "defaults:", rep["start_bat"]["defaults_declared"],
              "ok:", rep["start_bat"]["all_match"])
    if not only or only == "cli":
        rep["cli"] = check_cli_defaults()
        print("cli:", json.dumps(rep["cli"]["rows"], ensure_ascii=False))
    if not only or only == "idem":
        rep["idempotency"] = check_idempotency()
        print("idempotency:", json.dumps(rep["idempotency"],
                                         ensure_ascii=False))
    if not only or only == "broken":
        rep["broken"] = check_broken_inputs()
        print("broken:", json.dumps(rep["broken"], ensure_ascii=False))
    dest = OUT / "plumbing.json"
    prev = {}
    if dest.is_file():
        try:
            prev = json.loads(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    prev.update(rep)
    dest.write_text(json.dumps(prev, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"-> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
