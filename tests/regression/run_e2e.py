#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stratified end-to-end runs of the transparent channel-sync path.

Sampling frame (stated so the sample cannot be mistaken for the population):

  The population is the 226-clip calibration corpus, of which **146** are
  channel-sync eligible (4 mono PCM streams) and **80** are not.  Every
  eligible clip is already covered *decision-level* in T5/T6 (the OFF and ON
  arms over all 226).  This tier re-runs a **deliberate stratified sample**
  through the real container path, because a transparent run writes a
  multi-hundred-MB container and the 50 GB long takes cannot be remuxed here.

  One clip per stratum, chosen for the property the stratum tests — not the
  first one that came to hand:

    applied / large shift   (~20 ms, CH2)      -> uuid+tref+nrtm restoration
    applied / large shift   (~22 ms, CH1)      -> non-CH2 mover, order check
    applied / small shift   (<1 ms, CH2)       -> sub-millisecond integer shift
    already_aligned                            -> byte-identical copy branch
    measure_failed                             -> failure => source copy
    grouped anchor enabled                     -> flag reaches the transparent path

  `--check` levels are spread across the sample so all three are exercised on
  real media rather than asserted.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

HERE = Path(__file__).resolve().parent
OUT = REPO / "work" / "regression_r087" / "out"
LOGS = REPO / "work" / "regression_r087" / "logs"
MEDIA = Path(r"E:\1kt_media\speech")
MEDIA_LONG = Path(r"E:\1kt_media\long")
VTDIR = Path(r"E:\1kt_vt\in")
WORK = Path(r"E:\1kt_regress_v087")

CORPUS_OFF = OUT / "corpus_off_r087.json"

#: (id, clip, stratum, check level, extra CLI args)
SAMPLES = [
    ("E1", "20260904_C1214.MP4", "applied_large_CH2", "full", []),
    ("E2", "20260904_C1197.MP4", "applied_large_CH1", "basic", []),
    ("E3", "20260904_C1182.MP4", "applied_large_CH2b", "advanced", []),
    ("E4", "20260904_C1183.MP4", "applied_small_subms", "full", []),
    ("E5", "20260903_C1158.MP4", "already_aligned_copy", "full", []),
    ("E6", "20260903_C1083.MP4", "measure_failed_copy", "full", []),
    ("E7", "20260904_C1214.MP4", "grouped_enabled", "basic",
     ["--channel-sync-groups"]),
]


def find_clip(name: str) -> Path:
    for base in (VTDIR, MEDIA, MEDIA_LONG):
        p = base / name
        if p.is_file():
            return p
        hits = list(base.rglob(name)) if base.is_dir() else []
        if hits:
            return hits[0]
    raise SystemExit(f"clip not found: {name}")


def decisions_of(outdir: Path) -> dict:
    hits = sorted(outdir.rglob("channel_sync_*.json"))
    if not hits:
        return {}
    try:
        return json.loads(hits[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def shifts_of(dec: dict) -> dict:
    return {f"audio[{c.get('stream')}]": int(c.get("shift_samples") or 0)
            for c in (dec.get("channels") or [])
            if c.get("decision") == "fixed"}


def anchor_key_of(dec: dict) -> str:
    """The producer's own primary anchor — not a guess.

    Measuring residuals against a hard-coded CH3 is wrong on clips whose
    anchor is CH1: CH3 is then itself an unmeasured/untouched track, so the
    "residual" is between two tracks that were never claimed to be aligned.
    """
    a = dec.get("anchor_stream")
    if a is None:
        a = next((c.get("stream") for c in (dec.get("channels") or [])
                  if c.get("decision") == "anchor"
                  and c.get("anchor_role") != "secondary"), None)
    return f"audio[{a}]" if a is not None else "audio[2]"


def parse_log(logpath: Path) -> dict:
    """Pull the check-level facts back out of the per-run log."""
    if not logpath.is_file():
        return {}
    text = logpath.read_text(encoding="utf-8", errors="replace")
    facts: dict = {}
    m = re.search(
        r"\[CHECK\] \S+ \| level=(\S+) PRESERVED=(\S+) MODIFIED=(\S+) "
        r"MISSING=(\S+) structural_success=(\S+) quality=(\S+)", text)
    if m:
        facts["check"] = {
            "level": m.group(1),
            "PRESERVED": m.group(2), "MODIFIED": m.group(3),
            "MISSING": m.group(4),
            "structural_success": m.group(5), "quality": m.group(6),
        }
    m = re.search(r"\[CHECK\] \S+ \| level=(\S+) \| (\S+)", text)
    if m:
        facts["check_status"] = m.group(2)
    m = re.search(r"selfcheck[: ]\s*(\w+)\s*(\{[^}]*\})", text)
    if m:
        facts["selfcheck"] = {"verdict": m.group(1), "counts": m.group(2)}
    m = re.search(r"Gyroflow[^\n]*(PASS|FAIL)[^\n]*", text)
    if m:
        facts["gyroflow_line"] = m.group(0).strip()[:300]
    for key, pat in (
        ("sync_ok", r"\[SYNC-OK\][^\n]*"),
        ("sync_skip", r"\[SYNC-SKIP\][^\n]*"),
        ("check_failed", r"check_failed=(\d+)"),
    ):
        m = re.search(pat, text)
        if m:
            facts[key] = m.group(0)[:300]
    m = re.search(r"uuid — GPAC 已自带 (\d+) 个, 补插 (\d+) 个", text)
    if m:
        facts["uuid"] = {"gpac_present": int(m.group(1)),
                         "inserted": int(m.group(2))}
    m = re.search(r"Sony metadata — (\d+) 元数据轨", text)
    if m:
        facts["sony_meta_tracks"] = int(m.group(1))
    m = re.search(r"transparent remux — 按源轨顺序 \[([^\]]*)\]", text)
    if m:
        facts["remux_order"] = [x.strip() for x in m.group(1).split(",")]
    facts["check_na_info"] = ("PMS/SSIM 抽样在本模式下**不适用**"
                              in text or "SKIPPED_NOT_APPLICABLE" in text)
    return facts


def run_one(sid: str, clip_name: str, stratum: str, level: str,
            extra: list[str]) -> dict:
    src = find_clip(clip_name)
    run_dir = WORK / sid
    inp = run_dir / "in"
    outp = run_dir / "out"
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    inp.mkdir(parents=True, exist_ok=True)
    outp.mkdir(parents=True, exist_ok=True)
    link = inp / src.name
    try:
        link.hardlink_to(src)
    except OSError:
        shutil.copy2(src, link)

    cmd = [
        sys.executable, str(REPO / "1kt.py"),
        "--input", str(inp), "--output", str(outp),
        "--channel-sync-transparent", "--check", level,
        "--headless", "--log-level", "info",
    ] + extra
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    elapsed = round(time.perf_counter() - t0, 1)
    log = run_dir / "logs" / "total.log"
    (LOGS / f"{sid}_run.log").write_text(r.stdout or "", encoding="utf-8",
                                         errors="replace")
    dst = outp / src.name
    dec = decisions_of(outp / ".1ktwork")
    rec = {
        "id": sid, "clip": clip_name, "stratum": stratum, "level": level,
        "extra_args": extra, "rc": r.returncode, "elapsed_s": elapsed,
        "src": str(src), "dst": str(dst),
        "dst_exists": dst.is_file(),
        "dst_size": dst.stat().st_size if dst.is_file() else 0,
        "src_size": src.stat().st_size,
        "byte_identical": (dst.is_file()
                           and dst.stat().st_size == src.stat().st_size
                           and _sha(dst) == _sha(src)),
        "status": dec.get("status"),
        "shifts": shifts_of(dec),
        "anchor": anchor_key_of(dec),
        "log_facts": parse_log(log),
    }
    if dst.is_file() and dec:
        vcmd = [
            sys.executable, str(HERE / "e2e_verify.py"),
            "--src", str(src), "--out", str(dst),
            "--anchor", rec["anchor"],
            "--shifts", json.dumps(rec["shifts"]),
            "--json", str(OUT / f"e2e_{sid}.json"),
        ]
        v = subprocess.run(vcmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
        (LOGS / f"{sid}_verify.log").write_text(
            v.stdout or "", encoding="utf-8", errors="replace")
        rec["verify_summary"] = (v.stdout or "").strip().splitlines()[:1]
        vj = OUT / f"e2e_{sid}.json"
        if vj.is_file():
            rec["verify"] = json.loads(vj.read_text(encoding="utf-8"))
    return rec


def _sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    dest = OUT / "e2e_runs.json"
    rows: list[dict] = []
    for sid, clip, stratum, level, extra in SAMPLES:
        if only and only not in sid:
            continue
        rec = run_one(sid, clip, stratum, level, extra)
        rows.append(rec)
        v = rec.get("verify") or {}
        st = v.get("structure_match") or {}
        rv = v.get("residual_verdict") or {}
        bad = [k for k, x in rv.items() if x.get("ok") is False]
        skipped = [k for k, x in rv.items() if x.get("ok") is None]
        print(f"[{sid}] {clip:<22} {stratum:<20} rc={rec['rc']} "
              f"{rec['elapsed_s']:>7}s status={rec['status']} "
              f"bytecopy={rec['byte_identical']} "
              f"order={'SAME' if v.get('stream_order_preserved') else 'DIFF'} "
              f"ftyp={'SAME' if st.get('ftyp_major_brand_same') else 'DIFF'} "
              f"tref={'SAME' if st.get('tref_cdsc_same') else 'DIFF'} "
              f"nrtm={st.get('nrtm_src')}->{st.get('nrtm_out')} "
              f"uuid={v.get('uuid', {}).get('src_count')}->"
              f"{v.get('uuid', {}).get('out_count')} "
              f"changed={v.get('streams_changed')} bad_res={bad} "
              f"unmeasurable={skipped} "
              f"gyro={(v.get('gyroflow') or {}).get('status')} "
              f"check={(rec['log_facts'].get('check') or {}).get('structural_success')}",
              flush=True)
        dest.write_text(json.dumps(rows, ensure_ascii=False, indent=1,
                                   default=str), encoding="utf-8")
    if not rows:
        # Never overwrite a complete result set with an empty one: filtering by
        # a typo'd id would otherwise erase the whole tier silently.
        print(f"REFUSING to write: 0 runs matched {only!r}")
        return 1
    print(f"\n-> {dest} ({len(rows)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
