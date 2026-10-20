#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted re-measurement: does relaxing a gate produce *correct* fixes?

The offline matrix can only say which tracks would be adopted. Whether the
adopted fix survives csrun's own three-layer verification (exact application /
independent residual / decision recheck) is computed inside csrun and is not
stored in the arm, so it has to be measured.

Cost control: only files that can actually change are included (they contain a
fixed track -- the regression side -- or a refused but measurably off track),
and files above a size cap are skipped. Two long takes are 43 / 50 GiB and their
improvement ratio is < 0.2, i.e. the gate is right to refuse them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))

ARM = REPO / "work" / "regression_r087" / "out" / "ARM5_on.json"
OUT = REPO / "work" / "regression_r087" / "out"
LOGS = REPO / "work" / "regression_r087" / "logs"
DEST = Path(r"E:\1kt_scratch\gate_subset")
CSRUN = REPO / "work" / "pairensics" / "grouped_anchor" / "csrun.py"
ROOTS = [Path(r"E:\1kt_media"), Path(r"I:\compact")]
#: 单位是 **MiB**。之前写成 `512 * 1024 ** 2`（字节）再拿 MiB 去比，
#: 比较永远不成立, 于是把 51 GiB / 44 GiB 的长镜头也复制了进来 ——
#: 一次就复制了 106 GiB。体积门必须与被比较的量同单位。
SIZE_CAP_MIB = 400
EXTRA = {"20260904_C1213.MP4", "20260904_C1206.MP4"}   # 用户关注的复检案例
SR = 48000
MIN_SAMPLES = 5

VARIANTS = [
    ("base", []),
    ("frames2", [("min_usable_frames", "2")]),
    ("mad2", [("mad_max_ms", "2.0")]),
]


def locate(name: str) -> Path | None:
    for root in ROOTS:
        if root.is_dir():
            hit = list(root.rglob(name))
            if hit:
                return hit[0]
    return None


def main() -> int:
    arm = json.loads(ARM.read_text(encoding="utf-8"))
    picked: dict[str, str] = {}
    for r in arm:
        for c in r.get("channels") or []:
            d = c.get("delay_ms")
            if c.get("decision") == "fixed":
                picked.setdefault(r["file"], "fixed")
            elif (c.get("decision") == "untouched"
                  and c.get("reason") in ("non_constant", "recheck_residual",
                                          "low_confidence")
                  and isinstance(d, (int, float))
                  and abs(d) >= 1000 * MIN_SAMPLES / SR):
                picked.setdefault(r["file"], c.get("reason") or "refused")
    keep, skip = [], []
    for src, why in sorted(picked.items()):
        name = Path(src).name
        p = Path(src)
        if not p.is_file():
            p = locate(name) or p
        if not p.is_file():
            skip.append((name, "missing"))
            continue
        mb = p.stat().st_size / 1024 ** 2
        if mb > SIZE_CAP_MIB and name not in EXTRA:
            skip.append((name, f"{mb:.0f} MiB > cap {SIZE_CAP_MIB}"))
            continue
        keep.append((p, why, mb))
    keep.sort(key=lambda x: x[2])
    print(f"纳入 {len(keep)} 件, 跳过 {len(skip)} 件")
    for n, w in skip[:6]:
        print(f"  skip {n}: {w}")
    print("  f 侧(fixed, 回归用) / r 侧(被拒偏移):")
    for p, why, mb in keep:
        print(f"    {mb:>6.0f} MiB  {why:<16} {p.name}")

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for p, _w, _mb in keep:
        shutil.copy2(p, DEST / p.name)
    total = sum((DEST / p.name).stat().st_size for p, _w, _mb in keep)
    print(f"复制 {len(keep)} 件 / {total / 1024 ** 2:.0f} MiB -> {DEST}")

    results = []
    for tag, sets in VARIANTS:
        out = OUT / f"G_{tag}.json"
        log = LOGS / f"G_{tag}.log"
        cmd = [sys.executable, str(CSRUN), "run", "--arm", tag,
               "--out", str(out), "--root", str(DEST),
               "--no-cache",          # 另一进程正在写共享解码缓存, 避免互相踩
               "--set", "group_anchor_enabled=1"]
        for k, v in sets:
            cmd += ["--set", f"{k}={v}"]
        print(f"\n[{tag}] {' '.join(cmd[-6:])}")
        t0 = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.call(cmd, cwd=str(REPO), stdout=fh,
                                 stderr=subprocess.STDOUT)
        dt = time.time() - t0
        print(f"  rc={rc} {dt:.0f}s")
        if out.is_file():
            d = json.loads(out.read_text(encoding="utf-8"))
            fixes = [(r["name"], c.get("stream"), c.get("shift_samples"))
                     for r in d for c in (r.get("channels") or [])
                     if c.get("decision") == "fixed"]
            print(f"  files={len(d)} fixed={len(fixes)}")
            results.append({"variant": tag, "rc": rc, "seconds": round(dt, 1),
                            "files": len(d), "fixes": fixes,
                            "rows": d})

    (OUT / "G_RESULTS.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k != "rows"}
                    for r in results], ensure_ascii=False, indent=1),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
