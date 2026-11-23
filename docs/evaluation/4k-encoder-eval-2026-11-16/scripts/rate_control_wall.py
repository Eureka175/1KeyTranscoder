# -*- coding: utf-8 -*-
"""Rate-control 'wall' analysis for the 4K hardware matrix."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(r"F:\1KeyTranscoder")
BASE = ROOT / "work" / "hw4k_matrix"
FFPROBE = ROOT / "tools" / "ffprobe.exe"


def packets(path: Path) -> list[tuple[float, int]]:
    proc = subprocess.run(
        [str(FFPROBE), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,dts_time,size", "-of", "json",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = json.loads(proc.stdout or "{}")
    out = []
    for p in data.get("packets", []):
        t = p.get("pts_time") or p.get("dts_time") or "0"
        try:
            out.append((float(t), int(p.get("size") or 0)))
        except (TypeError, ValueError):
            out.append((0.0, int(p.get("size") or 0)))
    return out


def windows(pkts: list[tuple[float, int]], width: float) -> list[float]:
    if not pkts:
        return []
    end = max(t for t, _ in pkts) + 1.0 / 29.97
    buckets: dict[int, int] = {}
    for t, size in pkts:
        buckets[int(t // width)] = buckets.get(int(t // width), 0) + size
    return [buckets.get(i, 0) * 8 / width / 1e6
            for i in range(int(end / width) + 1)]


def longest_run(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def main() -> int:
    rows = json.loads((BASE / "metrics.json").read_text(encoding="utf-8"))
    detail = []
    hdr = (f"{'clip':22} {'encoder':10} {'preset':6} {'cap':>7} {'mean':>7} "
           f"{'p95':>7} {'max':>7} {'mean%':>6} {'p95%':>6} {'max%':>6} "
           f"{'>=95%':>6} {'>=100%':>7} {'run>=90%':>8} {'3smax%':>7}")
    print(hdr)
    for r in rows:
        cap = r.get("cap_maxrate_kbps")
        if not cap:
            continue
        cap_mbps = cap / 1000.0
        out = Path(r["output"])
        if not out.is_file():
            continue
        pkts = packets(out)
        w1 = windows(pkts, 1.0)
        if not w1:
            continue
        w3 = windows(pkts, 3.0)
        s = sorted(w1)
        mean = sum(w1) / len(w1)
        p95 = s[max(0, int(0.95 * len(s)) - 1)]
        mx = s[-1]
        ge95 = sum(1 for x in w1 if x >= 0.95 * cap_mbps)
        ge100 = sum(1 for x in w1 if x >= cap_mbps)
        run = longest_run([x >= 0.90 * cap_mbps for x in w1])
        mx3 = max(w3) if w3 else mx
        detail.append((mean / cap_mbps, r, len(w1), ge95, ge100, run, mx3))
        print(f"{r['clip']:22} {r['encoder']:10} {r['preset']:6} "
              f"{cap_mbps:7.1f} {mean:7.1f} {p95:7.1f} {mx:7.1f} "
              f"{mean/cap_mbps*100:6.0f} {p95/cap_mbps*100:6.0f} "
              f"{mx/cap_mbps*100:6.0f} {ge95:6d} {ge100:7d} "
              f"{run:8d} {mx3/cap_mbps*100:7.0f}")
    print("\n== most cap-limited (mean/cap) ==")
    detail.sort(reverse=True)
    for ratio, r, n, ge95, ge100, run, mx3 in detail[:15]:
        cap_mbps = r["cap_maxrate_kbps"] / 1000.0
        print(f"{r['clip']:22} {r['encoder']:10} {r['preset']:6} "
              f"mean/cap={ratio*100:5.0f}% windows={n:3d} "
              f">=95%cap={ge95:3d} >=100%cap={ge100:3d} "
              f"run>=90%={run} 3smax={mx3/cap_mbps:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
