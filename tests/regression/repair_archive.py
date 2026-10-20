#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair the channel offsets found in the archive, staging the results on E:.

**This does not write to the archive.**  The output goes to a staging root that
mirrors `I:\\`'s relative layout, so the review is a matter of opening files on
E: and only then replacing the originals.  Writing back is a separate explicit
step (`_APPLY-BACK.ps1` is generated for it and is deliberately not executed
here) because an in-place rewrite of a 2 GB camera original is not something to
do on the strength of a log line.

Why the library entry point rather than `1kt.py --channel-sync-transparent`:
the CLI batch loop discovers a whole directory, so repairing 14 files out of a
235-file directory would copy the other 221 as well (94 GiB of archive in
scope — it does not fit, and it is not wanted).  `run_channel_sync` is the same
function the CLI calls with `transparent=True`; only path control differs.

Selection is `status == "applied"` from an already-measured arm.  `applied`
means the producer decided the track is off by more than `aligned_max_ms` and
its own recheck agreed.  Everything else is deliberately left alone:

    already_aligned  nothing to do
    measure_failed   no trustworthy measurement — moving it would be a guess
    skipped_not_4mono not a channel-sync layout

Usage:
    python repair_archive.py --arm <ARCH_on.json> --out E:\\1kt_archive_repaired
                             [--limit N] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
WORKROOT = Path(r"E:\1kt_scratch\archive_repair")
ARCHIVE_ROOT = Path(r"I:\\")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(src: Path) -> Path:
    """Relative path inside the archive, so the staging tree mirrors I:\\."""
    try:
        return src.relative_to(ARCHIVE_ROOT)
    except ValueError:
        return Path(src.name)


def _decisions(rep: dict) -> dict:
    return {
        "status": rep.get("status"),
        "scope": rep.get("result_scope"),
        "file_copied": rep.get("file_copied"),
        "anchor_stream": rep.get("anchor_stream"),
        "shifts": {f"audio[{c.get('stream')}]": int(c.get("shift_samples") or 0)
                   for c in (rep.get("channels") or [])
                   if c.get("decision") == "fixed"},
        "tracks": [
            {"stream": c.get("stream"), "decision": c.get("decision"),
             "role": c.get("anchor_role"), "reason": c.get("reason"),
             "delay_ms": c.get("delay_ms"),
             "shift_samples": c.get("shift_samples"),
             "usable_frames": c.get("usable_frames")}
            for c in (rep.get("channels") or [])
        ],
    }


def repair_one(src: Path, dst: Path, log_line, opts: dict | None) -> dict:
    import numpy  # noqa: F401  (run_channel_sync needs it)

    from core.channel_sync import run_channel_sync
    from preservation.gpac import GpacContainerBackend
    from preservation.transparent_restore import (
        restore_after_transparent_remux,
    )

    rec: dict = {"src": str(src), "dst": str(dst),
                 "src_size": src.stat().st_size}
    work = WORKROOT / src.stem
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()

    logs: list[str] = []
    t0 = time.perf_counter()
    try:
        rep = run_channel_sync(
            source=src, ffmpeg=FFMPEG, work_dir=work,
            streams=_streams(src), opts=opts, transparent=True,
            dst=dst, gpac=GpacContainerBackend(None),
            restore_metadata=restore_after_transparent_remux,
            log=logs.append,
        )
        rec["decisions"] = _decisions(rep)
        rec["dst_exists"] = dst.is_file()
        rec["dst_size"] = dst.stat().st_size if dst.is_file() else 0
        rec["log_tail"] = [ln for ln in logs[-12:]]
    except Exception as exc:                            # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["log_tail"] = logs[-12:]
    finally:
        rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
        shutil.rmtree(work, ignore_errors=True)
    log_line(rec)
    return rec


_STREAM_CACHE: dict[str, list[dict]] = {}


def _streams(src: Path) -> list[dict]:
    import subprocess

    key = str(src)
    if key not in _STREAM_CACHE:
        r = subprocess.run(
            [str(FFPROBE), "-v", "error", "-show_streams", "-of", "json",
             str(src)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace")
        _STREAM_CACHE[key] = (json.loads(r.stdout or "{}").get("streams") or [])
    return _STREAM_CACHE[key]


def verify_one(src: Path, dst: Path, rec: dict, out_json: Path) -> dict:
    import subprocess

    shifts = (rec.get("decisions") or {}).get("shifts") or {}
    anchor_i = (rec.get("decisions") or {}).get("anchor_stream")
    cmd = [sys.executable, str(Path(__file__).parent / "e2e_verify.py"),
           "--src", str(src), "--out", str(dst),
           "--shifts", json.dumps(shifts),
           "--json", str(out_json)]
    if anchor_i is not None:
        cmd += ["--anchor", f"audio[{anchor_i}]"]
    r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    if out_json.is_file():
        return json.loads(out_json.read_text(encoding="utf-8"))
    return {"error": (r.stdout or "").strip()[:400]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    help="arm JSON whose `applied` files are to be repaired")
    ap.add_argument("--out", default=r"E:\1kt_archive_repaired")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true",
                    help="re-run even if the staged output already exists")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--groups", action="store_true",
                    help="enable the grouped anchor — MUST match the arm the "
                         "selection came from, or the repair silently "
                         "under-fixes (a file the arm marked `applied` "
                         "becomes a no-op byte copy)")
    a = ap.parse_args()
    opts = {"group_anchor_enabled": True} if a.groups else None

    arm = json.loads(Path(a.arm).read_text(encoding="utf-8"))
    outroot = Path(a.out)
    outroot.mkdir(parents=True, exist_ok=True)
    WORKROOT.mkdir(parents=True, exist_ok=True)

    todo = [r for r in arm if r.get("status") == "applied"]
    if a.limit:
        todo = todo[:a.limit]
    print(f"arm={Path(a.arm).name}  files_in_arm={len(arm)}  "
          f"to_repair={len(todo)}  out={outroot}", flush=True)

    rows: list[dict] = []
    dest = outroot / "_REPAIR-REPORT.json"

    def flush() -> None:
        dest.write_text(json.dumps(
            {"arm": str(a.arm), "out": str(outroot), "rows": rows},
            ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    for i, r in enumerate(todo, 1):
        src = Path(r["file"])
        if not src.is_file():
            rows.append({"src": str(src), "error": "source missing"})
            print(f"[{i}/{len(todo)}] MISSING {src.name}", flush=True)
            continue
        dst = outroot / _rel(src)
        prior = r
        if dst.is_file() and not a.force:
            print(f"[{i}/{len(todo)}] SKIP (already staged) {dst.name}",
                  flush=True)
            rec = {"src": str(src), "dst": str(dst),
                   "skipped": True,
                   "decisions": {"status": prior.get("status"),
                                 "shifts": {
                                     f"audio[{c.get('stream')}]":
                                     int(c.get("shift_samples") or 0)
                                     for c in (prior.get("channels") or [])
                                     if c.get("decision") == "fixed"}}}
        else:

            def _p(x: dict) -> None:
                d = x.get("decisions") or {}
                print(f"[{i}/{len(todo)}] {src.name:<24} "
                      f"{d.get('status','?'):<16} "
                      f"shifts={d.get('shifts')} "
                      f"{x.get('elapsed_s','-')}s", flush=True)

            rec = repair_one(src, dst, _p, opts)

        # Cross-check the repair's own decision against the arm that selected
        # this file. A mismatch means the repair ran under different settings
        # than the selection (the first run of this script did exactly that:
        # selection came from a grouped arm, the repair ran ungrouped, and one
        # file came back as a no-op byte copy while still being counted as
        # "repaired").
        arm_shifts = {
            f"audio[{c.get('stream')}]": int(c.get("shift_samples") or 0)
            for c in (prior.get("channels") or [])
            if c.get("decision") == "fixed"}
        got = (rec.get("decisions") or {}).get("shifts") or {}
        rec["selection_shifts"] = arm_shifts
        rec["selection_matches_repair"] = (arm_shifts == got)

        if dst.is_file() and not a.no_verify:
            vj = outroot / "_verify" / (src.stem + ".json")
            vj.parent.mkdir(parents=True, exist_ok=True)
            rec["verify"] = verify_one(src, dst, rec, vj)
        rec["archive_verdict"] = prior.get("status")
        rec["archive_verify"] = prior.get("verify")
        rows.append(rec)
        flush()

    # ---- summary -------------------------------------------------------
    ok = [r for r in rows if (r.get("verify") or {}).get("residual_verdict")]
    moved = []
    for r in ok:
        rv = r["verify"].get("residual_verdict") or {}
        for k, v in rv.items():
            if v.get("shift"):
                moved.append({"file": Path(r["dst"]).name, "track": k,
                              "shift": v["shift"],
                              "observed_after": v.get("observed_out_samples"),
                              "verdict": v.get("verdict")})
    structural_bad = []
    for r in ok:
        v = r["verify"]
        st = v.get("structure_match") or {}
        if not (v.get("stream_order_preserved")
                and st.get("ftyp_major_brand_same")
                and st.get("tref_cdsc_same")
                and st.get("nrtm_src") == st.get("nrtm_out")
                and not v.get("uuid", {}).get("missing")
                and not v.get("streams_lost")):
            structural_bad.append(Path(r["dst"]).name)

    report = {
        "arm": str(a.arm),
        "out": str(outroot),
        "archive_in_scope": "I:\\<date dirs> (I:\\2026.09.24 excluded)",
        "files_in_arm": len(arm),
        "files_selected": len(todo),
        "files_repaired": len([r for r in rows if r.get("dst_exists")]),
        "tracks_moved": len(moved),
        "structural_failures": structural_bad,
        "rows": rows,
        "moved": moved,
    }
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=1,
                               default=str), encoding="utf-8")

    lines = [
        "# 归档声道对齐修复 — 待审核清单",
        "",
        f"来源 arm：`{a.arm}`",
        f"暂存目录：`{outroot}`（**镜像 `I:\\` 的相对路径**，未改动归档原件）",
        "",
        f"- 归档扫描件数：{len(arm)}（`I:\\2026.09.24` 已按你的要求排除）",
        f"- 选中修复：{len(todo)}（判据：`status == applied`）",
        f"- 实际产出：{report['files_repaired']}",
        f"- 被移动的轨：{report['tracks_moved']}",
        f"- 结构/元数据异常：{len(structural_bad)}"
        + (f" `{structural_bad}`" if structural_bad else ""),
        "",
        "## 逐文件",
        "",
        "| 文件 | 判定 | 移动的轨(位移) | 修后残差 | 顺序 | ftyp | tref | nrtm | uuid | 时长差(s) | Gyroflow |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if not r.get("verify"):
            lines.append(f"| `{Path(r['src']).name}` | {r.get('error', '-')} "
                         f"| - | - | - | - | - | - | - | - | - | - |")
            continue
        v = r["verify"]
        st = v.get("structure_match") or {}
        rv = v.get("residual_verdict") or {}
        moved_txt = ", ".join(f"{k}({x.get('shift'):+d})"
                              for k, x in rv.items() if x.get("shift")) or "—"
        after = ", ".join(f"{k}={x.get('observed_out_samples')}"
                          for k, x in rv.items() if x.get("shift")) or "—"
        rel = Path(r["dst"]).relative_to(outroot)
        dur = (v.get("format") or {}).get("duration_delta_s")
        cells = [
            f"`{rel}`", r.get("archive_verdict") or "-", moved_txt, after,
            "SAME" if v.get("stream_order_preserved") else "DIFF",
            "SAME" if st.get("ftyp_major_brand_same") else "DIFF",
            "SAME" if st.get("tref_cdsc_same") else "DIFF",
            f"{st.get('nrtm_src')}->{st.get('nrtm_out')}",
            f"{v.get('uuid', {}).get('src_count')}->"
            f"{v.get('uuid', {}).get('out_count')}",
            f"{dur:+.6f}" if isinstance(dur, (int, float)) else "-",
            str((v.get("gyroflow") or {}).get("status")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    (outroot / "_REPAIR-REPORT.md").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")

    ps = [
        "# 审核通过后再执行：把暂存件逐个替换回归档。",
        "# 本脚本由 repair_archive.py 生成，**从未被自动运行**。",
        "#",
        "# MARGINAL = 位移只有 3~4 个采样 (0.06~0.08 ms)，刚过 aligned_max_ms",
        "# 0.05 ms 的门。按现行策略它算 fixed，但它贴着 48 kHz 的量化粒度 ——",
        "# 是否替换由你决定，不想动就把带 MARGINAL 的那两行删掉。",
        "",
    ]
    for r in rows:
        if not r.get("dst_exists"):
            continue
        rv = (r.get("verify") or {}).get("residual_verdict") or {}
        sh = [x.get("shift") for x in rv.values() if x.get("shift")]
        tag = "MARGINAL " if sh and all(abs(s) <= 4 for s in sh) else ""
        ps.append(f"# {tag}shift={sh}")
        ps.append(f'Copy-Item -LiteralPath "{r["dst"]}" '
                  f'-Destination "{r["src"]}" -Force')
    (outroot / "_APPLY-BACK.ps1").write_text("\n".join(ps) + "\n",
                                             encoding="utf-8")

    print(f"\n-> {dest}")
    print(f"-> {outroot / '_REPAIR-REPORT.md'}")
    print(f"-> {outroot / '_APPLY-BACK.ps1'}")
    return 0 if not structural_bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
