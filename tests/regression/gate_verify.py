#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Did the newly adopted fixes actually verify?

The offline matrix says *which* tracks a looser threshold would adopt. csrun's
`verify` block says whether the fix is real:

  * `exact`            -- the written samples are bit-identical to the source
                          shifted by the claimed amount (`max_abs_diff` 0)
  * `after_is_zero`    -- an independent residual estimate on the OUTPUT is 0
  * `before_matches_shift` -- the measured pre-fix delay equals the applied shift

A variant is only worth adopting if every new fix carries all three, and if no
previously fixed track is lost or changes value.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
VARIANTS = ["base", "frames3", "frames2", "mad2"]


def load(tag: str) -> list[dict] | None:
    p = OUT / f"G_{tag}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def fixes(rows: list[dict]) -> dict[tuple[str, int], dict]:
    out = {}
    for r in rows:
        for c in r.get("channels") or []:
            if c.get("decision") == "fixed":
                out[(r.get("name") or Path(r["file"]).name,
                     c.get("stream"))] = {
                    "shift": c.get("shift_samples"),
                    "delay_ms": c.get("delay_ms"),
                    "frames": c.get("usable_frames"),
                    "verify": (r.get("verify") or {}).get(
                        str(c.get("stream"))) or {},
                }
    return out


def verdict(v: dict) -> tuple[bool, str]:
    if not v:
        return False, "verify 缺失"
    exact = (v.get("exact") or {})
    ok = bool(v.get("ok"))
    exact_ok = bool(exact.get("exact"))
    after = bool(v.get("after_is_zero"))
    before = bool(v.get("before_matches_shift"))
    parts = [f"ok={ok}", f"exact={exact_ok}",
             f"after0={after}", f"before={before}"]
    if exact:
        parts.append(f"maxdiff={exact.get('max_abs_diff')}")
    aft = v.get("after") or {}
    if aft:
        parts.append(f"after_med={aft.get('median')}")
    return (ok and exact_ok and after and before), " ".join(parts)


def main() -> int:
    base = load("base")
    if base is None:
        print("G_base.json 缺失")
        return 2
    fb = fixes(base)
    print(f"基线: {len(base)} 文件, {len(fb)} 条修正\n")

    for tag in VARIANTS[1:]:
        rows = load(tag)
        if rows is None:
            print(f"{tag}: 未跑")
            continue
        fv = fixes(rows)
        new = {k: v for k, v in fv.items() if k not in fb}
        lost = [k for k in fb if k not in fv]
        changed = [k for k in fb if k in fv and fv[k]["shift"] != fb[k]["shift"]]
        print(f"=== {tag}: files={len(rows)} fixed={len(fv)} "
              f"新增={len(new)} lost={len(lost)} changed={len(changed)}")
        bad = 0
        for (name, ch), v in sorted(new.items()):
            good, why = verdict(v["verify"])
            bad += 0 if good else 1
            print(f"  {'OK ' if good else '!!!'} {name} CH{(ch or 0) + 1} "
                  f"shift={v['shift']} delay={v['delay_ms']} "
                  f"frames={v['frames']} | {why}")
        for k in lost:
            print(f"  !!! LOST {k}")
        for k in changed:
            print(f"  !!! CHANGED {k}: {fb[k]['shift']} -> {fv[k]['shift']}")
        print(f"  -> 新修全部通过: {'是' if bad == 0 else f'否 ({bad} 条)'}"
              f"; 回归: {'无' if not lost and not changed else '有'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
