#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare per-track decisions: new gate settings vs the old repair run.

`check` on a source file always reports FAIL when a track still needs fixing --
that is its purpose, not an error. What matters here is which tracks the new
gates now decide to fix, and whether any previously accepted fix would be lost.
"""
from __future__ import annotations

import json
from pathlib import Path

OLD = Path(r"F:\1KeyTranscoder\work\regression_r087\out\ARCH_on5.json")
NEW = Path(r"E:\1kt_scratch\gate_rerun\check_new")


def old_tracks(path: Path) -> dict[str, dict[int, dict]]:
    """Old decisions come from the measured archive arm: the repair run only
    recorded the shifts it applied, not the per-track verdicts."""
    out: dict[str, dict[int, dict]] = {}
    for r in json.loads(path.read_text(encoding="utf-8")):
        nm = r.get("name") or Path(r["file"]).name
        out[nm] = {c.get("stream"): {
            "decision": c.get("decision"), "reason": c.get("reason"),
            "shift": c.get("shift_samples"), "delay": c.get("delay_ms"),
            "frames": c.get("usable_frames")} for c in (r.get("channels") or [])}
    return out


def new_tracks(root: Path) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for p in sorted(root.rglob("archive-repair-report.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d.get("rows") or []:
            nm = Path(r.get("path") or r.get("src") or "").name
            a = r.get("audio") or {}
            out[nm] = {t.get("stream"): {
                "decision": t.get("decision"), "reason": t.get("reason"),
                "shift": t.get("shift_samples"), "delay": t.get("delay_ms"),
                "frames": t.get("usable_frames")}
                for t in (a.get("rows") or [])}
    return out


def main() -> int:
    old = old_tracks(OLD)
    new = new_tracks(NEW)

    def summarize(t: dict[int, dict]) -> str:
        parts = []
        for ch in sorted(t):
            x = t[ch]
            if x["decision"] == "fixed":
                parts.append(f"CH{ch + 1}+{x['shift']}")
            elif x["decision"] == "untouched" and x["reason"]:
                parts.append(f"CH{ch + 1}×{x['reason']}")
        return ", ".join(parts) or "-"

    names = sorted(new)
    print(f"{'file':<24}{'旧(修复时)':<44}{'新(新门限)':<44}{'变化'}")
    print("-" * 118)
    n_changed = 0
    for nm in names:
        o, nn = old.get(nm, {}), new.get(nm, {})
        so, sn = summarize(o), summarize(nn)
        of = {ch for ch in o if o[ch]["decision"] == "fixed"}
        nf = {ch for ch in nn if nn[ch]["decision"] == "fixed"}
        delta = ""
        if nf - of:
            delta += "新增修 " + ",".join(f"CH{c + 1}" for c in sorted(nf - of))
        if of - nf:
            delta += (" " if delta else "") + "失去修 " + ",".join(
                f"CH{c + 1}" for c in sorted(of - nf))
        if delta:
            n_changed += 1
        print(f"{nm:<24}{so:<44}{sn:<44}{delta}")
    print(f"\n有变化的文件: {n_changed}/{len(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
