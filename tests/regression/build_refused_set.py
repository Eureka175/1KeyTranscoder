#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pick the smallest set of files that can validate the loosened gates.

Only files that actually contain a refused-but-offset track can produce a new
fix, so measuring anything else is wasted work. Files above a size cap are
excluded: the two long takes (43 / 50 GiB) cost minutes each to decode and both
have an improvement ratio < 0.2, i.e. they are the ones the gate *should*
refuse.

Emits a JSON list of source paths plus a copy plan under E:\\1kt_scratch.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_charts import load, num  # noqa: E402

DEST = Path(r"E:\1kt_scratch\refused_set")
SIZE_CAP = 5 * 1024 ** 3
SR = 48000
MIN_SAMPLES = 5


def main() -> int:
    rows = load()
    want: list[str] = []
    for c in rows:
        if num(c.get("delay_samples")) is None:
            continue
        if c.get("decision") != "untouched":
            continue
        if abs(num(c.get("delay_ms"), 0.0)) < 1000 * MIN_SAMPLES / SR:
            continue
        want.append(c.get("_file") or "")
    uniq = sorted(set(want))
    print(f"含被拒偏移轨的文件: {len(uniq)}")

    # gate_charts 的 arm 里存的是文件路径; 若只剩文件名则到 I: 下定位
    paths: list[Path] = []
    for w in uniq:
        p = Path(w)
        if not p.is_file():
            cand = list(Path("I:/").glob(f"*/{p.name}"))
            if cand:
                p = cand[0]
        if p.is_file():
            paths.append(p)
        else:
            print(f"  找不到: {w}")
    print(f"可定位: {len(paths)}")

    keep, skip_big = [], []
    for p in paths:
        if p.stat().st_size > SIZE_CAP:
            skip_big.append((str(p), p.stat().st_size))
        else:
            keep.append(p)
    print(f"排除超大文件 {len(skip_big)}:")
    for s, sz in skip_big:
        print(f"  {Path(s).name} {sz / 1024 ** 3:.2f} GiB")

    DEST.mkdir(parents=True, exist_ok=True)
    for f in DEST.glob("*"):
        if f.is_file():
            f.unlink()
    total = 0
    for p in keep:
        shutil.copy2(p, DEST / p.name)
        total += p.stat().st_size
    print(f"\n已复制 {len(keep)} 件 / {total / 1024 ** 2:.0f} MiB -> {DEST}")
    (DEST.parent / "refused_set.json").write_text(
        json.dumps({"sources": [str(p) for p in keep],
                    "skipped_big": [s for s, _ in skip_big]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
