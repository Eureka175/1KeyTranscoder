#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parallel measurement of the 4ch-proof interview set.

`csrun` keeps its per-file scratch at `E:/1kt_scratch/tmp/<stem>` and its decode
cache at `E:/1kt_scratch/pcm/<stem>`, both keyed by the file stem. Two runs only
collide when they touch the *same* file, so disjoint shards can run concurrently
safely -- which is what this does: hardlink the short clips into N shards, run one
process per shard, then merge.

Two arms:
  base  production defaults
  mat   the stage-1 work point (materiality_k=3, verify_max_rel=0.2,
        recheck_windows=3, verify_agree_samples=5)

Merged arms land in work/regression_r087/out/P_{arm}.json so the usual scoring
scripts can read them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
OUT = REPO / "work" / "regression_r087" / "out"
LOGS = REPO / "work" / "regression_r087" / "logs"
CSRUN = REPO / "work" / "pairensics" / "grouped_anchor" / "csrun.py"
PROOF = Path(r"F:\1KeyTranscoder\data\align_4ch_proof")
ROOT = Path(r"F:\_proof_shard")
LONG = {"20260919_C1305.MP4", "20260919_C1306.MP4"}
SHARDS = 4

ARMS = {
    "base": [],
    "mat": [("materiality_k", "3"), ("verify_max_rel", "0.2"),
            ("recheck_windows", "3"), ("verify_agree_samples", "5")],
    # 剩余 21 条里 19 条卡在一致性门(容差 5)。加大容差看能救回几条、
    # 以及救回的那些是否通过独立核验 —— 这是"可行性"的关键一问。
    "mat10": [("materiality_k", "3"), ("verify_max_rel", "0.2"),
              ("recheck_windows", "3"), ("verify_agree_samples", "10")],
    "mat20": [("materiality_k", "3"), ("verify_max_rel", "0.2"),
              ("recheck_windows", "3"), ("verify_agree_samples", "20")],
    # 只记录不拦: 容差取极大, 让 gate 跑但全部放行 —— 这样每条被修轨都带
    # 自己的 Δ, 可以离线画"Δ vs 修正质量"的曲线, 再决定容差该用哪种量纲。
    "matshape": [("materiality_k", "3"), ("verify_max_rel", "0.2"),
                 ("recheck_windows", "3"),
                 ("verify_agree_samples", "100000")],
    # 一致性门默认只诊断(不否决)后的工作点: 门仍然测量并记录, 但不拦。
    "noveto": [("materiality_k", "3"), ("verify_max_rel", "0.2"),
               ("recheck_windows", "3"),
               ("verify_agree_samples", "20")],
}


def shard(short: list[Path]) -> list[Path]:
    if ROOT.exists():
        import shutil
        shutil.rmtree(ROOT)
    dirs = []
    for k in range(SHARDS):
        d = ROOT / f"s{k}"
        d.mkdir(parents=True, exist_ok=True)
        dirs.append(d)
    for i, f in enumerate(sorted(short)):
        tgt = dirs[i % SHARDS] / f.name
        if not tgt.exists():
            os.link(f, tgt)
    return dirs


def run_arm(arm: str, sets: list[tuple[str, str]], dirs: list[Path]) -> None:
    def one(d: Path, k: int) -> tuple[int, str, float]:
        out = OUT / f"P_{arm}_s{k}.json"
        log = LOGS / f"P_{arm}_s{k}.log"
        cmd = [sys.executable, str(CSRUN), "run", "--arm", f"{arm}s{k}",
               "--out", str(out), "--root", str(d)]
        for key, val in sets:
            cmd += ["--set", f"{key}={val}"]
        t0 = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.call(cmd, cwd=str(REPO), stdout=fh,
                                 stderr=subprocess.STDOUT)
        return rc, str(out), time.time() - t0

    print(f"\n[{arm}] {len(dirs)} 个分片并行…", flush=True)
    with ThreadPoolExecutor(max_workers=len(dirs)) as ex:
        results = list(ex.map(lambda a: one(*a), [(d, k)
                                                  for k, d in enumerate(dirs)]))
    merged = []
    for rc, path, dt in results:
        p = Path(path)
        n = 0
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            n = len(d)
            merged.extend(d)
        print(f"  分片 rc={rc} {n} 文件 {dt:.0f}s", flush=True)
    (OUT / f"P_{arm}.json").write_text(
        json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    print(f"  -> P_{arm}.json ({len(merged)} 文件)")


def main() -> int:
    files = [p for p in PROOF.glob("*.MP4") if p.name not in LONG]
    short = sorted(files)
    print(f"访谈/短素材 {len(short)} 件（长镜头另跑）")
    dirs = shard(short)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for arm, sets in ARMS.items():
        if only and arm != only:
            continue
        run_arm(arm, sets, dirs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
