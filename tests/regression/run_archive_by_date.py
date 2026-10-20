#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run `archive_repair.py repair` once per date folder under a drive root.

Why per folder and in parallel processes rather than one root:
  * the tool mirrors the input's relative path, so `--input I:\\20260903
    --output I:\\repaired\\20260903` yields `I:\\repaired\\20260903\\<sub>\\<file>`
    exactly as required;
  * scanning `I:\\` itself would sweep `I:\\2026.09.24` (649 GiB, explicitly
    out of scope) and every unrelated folder on the drive;
  * the tool is single-process, so parallelism is done at the folder level,
    with a **separate scratch root per worker** so two workers can never
    collide on a temp name.

Only files that were actually repaired are written (the tool's default). Use
`--keep-all` to mirror every input instead.

    python run_archive_by_date.py --root I:\\ --out I:\\repaired \\
        [--exclude 2026.09.24] [--workers 4] [--limit-days 2] [--keep-all]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "archive_repair.py"
DATE_RE = re.compile(r"^\d{8}$")


def media_count(d: Path) -> int:
    return sum(1 for p in d.rglob("*")
               if p.suffix.lower() in (".mp4", ".mov"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"I:\\")
    ap.add_argument("--out", default=r"I:\repaired")
    ap.add_argument("--exclude", action="append", default=["2026.09.24"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit-days", type=int, default=0)
    ap.add_argument("--scratch", default=r"E:\1kt_scratch\archive_by_date",
                    help="临时工作区根目录。**不要放在归档盘上**")
    ap.add_argument("--keep-all", action="store_true")
    ap.add_argument("--groups", action="store_true", default=True)
    a = ap.parse_args()

    root = Path(a.root)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(a.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    if scratch.drive.upper() == root.drive.upper():
        print(f"[FATAL] scratch={scratch} 与归档在同一盘符, 拒绝执行 "
              f"(会占用归档盘空间)", file=sys.stderr)
        return 2

    days = sorted((d for d in root.iterdir()
                   if d.is_dir() and DATE_RE.match(d.name)
                   and d.name not in set(a.exclude)),
                  key=lambda d: -media_count(d))
    if a.limit_days:
        days = days[: a.limit_days]
    print(f"待处理 {len(days)} 个日期目录 → {out}", flush=True)
    for d in days:
        print(f"  {d.name:<12} {media_count(d):>4} 个媒体文件", flush=True)

    results: list[dict] = []

    def run_one(idx_day: tuple[int, Path]) -> dict:
        idx, day = idx_day
        worker_scratch = scratch / f"w{idx % max(1, a.workers)}"
        worker_scratch.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(TOOL), "repair",
               "--input", str(day),
               "--output", str(out / day.name),
               "--json", str(out / "_rep" / day.name)]
        if a.groups:
            cmd.append("--groups")
        if a.keep_all:
            cmd.append("--keep-all")
        env = dict(**__import__("os").environ)
        env["ARCHIVE_REPAIR_SCRATCH"] = str(worker_scratch)
        t0 = time.perf_counter()
        r = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=36000)
        dt = round(time.perf_counter() - t0, 1)
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln]
        tally = next((ln for ln in reversed(tail) if ln.startswith("{")), "")
        (out / "_rep" / f"{day.name}.log").parent.mkdir(
            parents=True, exist_ok=True)
        (out / "_rep" / f"{day.name}.log").write_text(r.stdout or "",
                                                      encoding="utf-8")
        rec = {"day": day.name, "files": media_count(day), "rc": r.returncode,
               "elapsed_s": dt, "tally": tally}
        results.append(rec)
        print(f"  [{day.name}] rc={r.returncode} {dt:>7}s  {tally}", flush=True)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        list(ex.map(run_one, enumerate(days)))

    results.sort(key=lambda r: r["day"])
    (out / "_rep" / "by-date.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(r["files"] for r in results)
    failed = [r for r in results if r["rc"] != 0]
    print(f"\n目录 {len(results)} 个, 输入媒体 {total} 个, "
          f"失败目录 {len(failed)}")
    for r in results:
        print(f"  {r['day']:<12} {r['tally']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
