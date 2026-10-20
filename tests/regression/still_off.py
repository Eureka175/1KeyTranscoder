#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""For every repaired file, what is *still* off after the repair?

Reads AUD-02 from the repair reports (which is a fresh measurement of the
**output**, not of the source) and prints per-track decision / reason / delay,
so "aligned or not" is answered by measurement rather than by the fact that the
file was reported REPAIRED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else r"I:\repaired\_rep")


def main() -> int:
    rows: list[dict] = []
    for p in sorted(Path(ROOT).rglob("archive-repair-report.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8"))["rows"])
    rep = [r for r in rows if r.get("status") == "applied"]
    print(f"{'file':<24}{'CH':<4}{'decision':<16}{'reason':<20}"
          f"{'delay_ms':>10}  {'still_off?':<10}")
    print("-" * 88)
    n_off = 0
    worst = None
    for r in sorted(rep, key=lambda x: Path(x["src"]).name):
        name = Path(r["src"]).name
        for c in (r.get("audio_after") or {}).get("rows") or []:
            ch = (c.get("stream") or 0) + 1
            dec = c.get("decision")
            reason = c.get("reason") or ""
            dly = c.get("delay_ms")
            off = ""
            if dec == "untouched" and isinstance(dly, (int, float)) \
                    and abs(dly) >= 0.05:
                off = f"YES {abs(dly):.2f}ms"
                n_off += 1
                if worst is None or abs(dly) > abs(worst[2]):
                    worst = (name, ch, dly, reason)
            print(f"{name:<24}CH{ch:<3}{str(dec):<16}{reason:<20}"
                  f"{(f'{dly:+.4f}' if isinstance(dly, (int, float)) else '-'):>10}"
                  f"  {off:<10}")
    print(f"\n仍明显未对齐(untouched 且 |delay|>=0.05ms) 的轨: {n_off}")
    if worst:
        print(f"最严重: {worst[0]} CH{worst[1]} {worst[2]:+.3f} ms ({worst[3]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
