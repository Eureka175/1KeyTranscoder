#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one channel-sync arm over a scope, N-way parallel, and merge the result.

`csrun` keeps per-file scratch (`E:/1kt_scratch/tmp/<stem>`) and decode cache
(`E:/1kt_scratch/pcm/<stem>`) keyed by the file stem, so disjoint shards are safe
to run concurrently. Sharding is **by file** (round robin) rather than by
directory, because the archive's date folders differ in size by two orders of
magnitude and directory sharding leaves one worker with everything.

Usage:
  python tests/regression/run_arm_sharded.py <arm> <scope> [--shards N] [--opts k=v ...]
    scope: corpus | archive | proof
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
OUT = REPO / "work" / "regression_r087" / "out"
LOGS = REPO / "work" / "regression_r087" / "logs"
CSRUN = REPO / "work" / "pairensics" / "grouped_anchor" / "csrun.py"
MEDIA = (".mp4", ".MP4", ".mov", ".MOV")


def gather(scope: str) -> list[Path]:
    if scope == "corpus":
        # E: 断开时回退到项目内的副本（离线跑批用）
        e_speech = Path(r"E:\1kt_media\speech")
        e_long = Path(r"E:\1kt_media\long")
        f_media = REPO / "data" / "_from_E" / "1kt_media"
        f_long_dir = REPO / "data" / "align_4ch_proof"
        roots = [e_speech if e_speech.is_dir() else f_media / "speech"]
        if e_long.is_dir():
            roots.append(e_long)
        elif f_long_dir.is_dir():
            roots.append(f_long_dir)   # 长镜头在证明集里, 同名文件
    elif scope == "archive":
        roots = [Path("I:/") / d.name for d in sorted(Path("I:/").iterdir())
                 if d.is_dir() and len(d.name) == 8 and d.name.isdigit()]
    elif scope == "proof":
        roots = [Path(r"F:\1KeyTranscoder\data\align_4ch_proof")]
    else:
        raise SystemExit(f"未知 scope: {scope}")
    files: list[Path] = []
    for r in roots:
        if r.is_dir():
            files.extend(p for p in r.rglob("*") if p.suffix in MEDIA)
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm")
    ap.add_argument("scope")
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--opts", nargs="*", default=[])
    ap.add_argument("--by-dir", action="store_true",
                    help="按顶层日期目录分片, 不暂存 (归档用)")
    args = ap.parse_args()

    files = gather(args.scope)
    if not files:
        print("没有媒体文件")
        return 2
    stage = Path(r"F:\_arm_stage") / args.arm
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    dirs = []
    for k in range(args.shards):
        d = stage / f"s{k}"
        d.mkdir(parents=True, exist_ok=True)
        dirs.append(d)
    if args.by_dir:
        # 归档: 按**日期目录**分片, 不暂存 —— 同名文件分布在不同子目录里
        # (实测 667 件里有 40 个同名不同目录), 展平成 basename 会互相覆盖;
        # 而且 I: -> F: 跨卷硬链接不可用, 退回拷贝会复制上百 GiB。
        roots = sorted({next((p for p in f.parents if p.name[:8].isdigit()
                              and len(p.name) == 8), f.parent)
                        for f in files})
        groups = [roots[k::args.shards] for k in range(args.shards)]
        print(f"{args.scope}: {len(files)} 文件 / {len(roots)} 个日期目录 "
              f"-> {args.shards} 分片 (按目录, 不暂存)")
        stage = None
    else:
        groups = [None] * args.shards
        for i, f in enumerate(files):
            tgt = dirs[i % args.shards] / f.name
            try:
                os.link(f, tgt)
            except OSError as exc:
                raise SystemExit(
                    f"硬链接失败 ({f} -> {tgt}): {exc}\n"
                    "拒绝跨卷回退拷贝 —— 会复制上百 GiB。归档请用 --by-dir。")
    print(f"{args.scope}: {len(files)} 文件 -> {args.shards} 分片 "
          f"(每片 {len(files) // args.shards}~{len(files) // args.shards + 1})")

    def run(k: int) -> tuple[int, int, str, float]:
        out = OUT / f"R_{args.arm}_{args.scope}_s{k}.json"
        log = LOGS / f"R_{args.arm}_{args.scope}_s{k}.log"
        cmd = [sys.executable, str(CSRUN), "run", "--arm", args.arm,
               "--out", str(out)]
        if groups[k] is None:
            cmd += ["--root", str(dirs[k])]
        else:
            for r in groups[k]:
                cmd += ["--root", str(r)]
        for kv in args.opts:
            cmd += ["--set", kv]
        t0 = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.call(cmd, cwd=str(REPO), stdout=fh,
                                 stderr=subprocess.STDOUT)
        n = len(json.loads(out.read_text(encoding="utf-8"))) if out.is_file() else 0
        return k, rc, str(out), time.time() - t0

    t0 = time.time()
    merged: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.shards) as ex:
        for k, rc, path, dt in ex.map(run, range(args.shards)):
            n = 0
            if Path(path).is_file():
                d = json.loads(Path(path).read_text(encoding="utf-8"))
                n = len(d)
                merged.extend(d)
            print(f"  shard{k}: rc={rc} {n} 文件 {dt:.0f}s", flush=True)
    (OUT / f"R_{args.arm}_{args.scope}.json").write_text(
        json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    print(f"合并 -> R_{args.arm}_{args.scope}.json "
          f"({len(merged)} 文件), 墙钟 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
