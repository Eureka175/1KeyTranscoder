#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the smallest corpus subset that can validate a gate change.

Two kinds of file can tell us something:
  * files with a **refused but measurably off** track  -> can produce a new fix;
  * files with a **fixed** track                        -> can produce a regression
    (lost_fix / changed_value), which must stay at zero.

Everything else is guaranteed to be unchanged by a threshold tweak, so measuring
it only costs time. The subset is copied to E: because csrun keys its decode
cache by file stem, so identical content is fine.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO / "tests" / "regression"))

ARM = REPO / "work" / "regression_r087" / "out" / "ARM5_on.json"
DEST = Path(r"E:\1kt_scratch\gate_subset")
ROOTS = [Path(r"E:\1kt_media"), Path(r"I:\compact")]
SR = 48000
MIN_SAMPLES = 5


def locate(name: str) -> Path | None:
    for root in ROOTS:
        if not root.is_dir():
            continue
        hit = list(root.rglob(name))
        if hit:
            return hit[0]
    return None


def main() -> int:
    arm = json.loads(ARM.read_text(encoding="utf-8"))
    print(f"corpus arm: {len(arm)} files")
    picked: dict[str, dict] = {}
    for r in arm:
        need = None
        for c in r.get("channels") or []:
            d = c.get("delay_ms")
            reason = c.get("reason")
            if c.get("decision") == "fixed":
                need = "fixed"
                break
            if (c.get("decision") == "untouched"
                    and reason in ("non_constant", "recheck_residual",
                                   "low_confidence")
                    and isinstance(d, (int, float))
                    and abs(d) >= 1000 * MIN_SAMPLES / SR):
                need = "refused_off"
        if need:
            picked[r["file"]] = {"kind": need, "row": r}
    print(f"需要纳入的文件: {len(picked)} "
          f"(fixed {sum(1 for v in picked.values() if v['kind'] == 'fixed')} / "
          f"refused_off "
          f"{sum(1 for v in picked.values() if v['kind'] == 'refused_off')})")

    DEST.mkdir(parents=True, exist_ok=True)
    for f in DEST.glob("*"):
        if f.is_file():
            f.unlink()
    got, miss = [], []
    for src, info in sorted(picked.items()):
        p = Path(src)
        if not p.is_file():
            p = locate(Path(src).name) or Path(src)
        if not p.is_file():
            miss.append(src)
            continue
        shutil.copy2(p, DEST / p.name)
        got.append(p.name)
    print(f"已复制 {len(got)} 件；找不到 {len(miss)}")
    for m in miss:
        print("  missing", m)
    (DEST.parent / "gate_subset.json").write_text(
        json.dumps({"files": got, "missing": miss}, ensure_ascii=False,
                   indent=1), encoding="utf-8")
    total = sum((DEST / n).stat().st_size for n in got)
    print(f"subset 大小 {total / 1024 ** 2:.0f} MiB -> {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
