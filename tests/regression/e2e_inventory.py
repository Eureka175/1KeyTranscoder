#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory candidate clips for the e2e transparent tier.

Reads the freshly measured OFF arm plus ffprobe on disk, and prints a table
sized so a *stratified* sample can be chosen explicitly instead of
conveniently.  Nothing is selected here — this only reports the frame.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FFPROBE = REPO / "tools" / "ffprobe.exe"
OUT = REPO / "work" / "regression_r087" / "out"


def probe(path: Path) -> dict:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout or "{}")


def main() -> int:
    src = OUT / "corpus_off_r087.json"
    if not src.is_file():
        src = Path(r"E:\1kt_work\reports\baseline.json")
    recs = json.loads(src.read_text(encoding="utf-8"))
    print(f"# source: {src}  n={len(recs)}\n")
    hdr = (f"{'clip':<26}{'verdict':<18}{'size_MB':>9}{'dur_s':>9}"
           f"{'a_str':>6}{'cam':>10}  fixed(CH,shift)")
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for r in recs:
        p = Path(r["file"])
        if not p.is_file():
            continue
        st = probe(p)
        fmt = st.get("format") or {}
        size = int(fmt.get("size") or p.stat().st_size)
        dur = float(fmt.get("duration") or 0)
        audio = [s for s in st.get("streams", []) if s["codec_type"] == "audio"]
        maker = ((fmt.get("tags") or {}).get("com.apple.quicktime.make")
                 or (fmt.get("tags") or {}).get("make") or "?")
        model = ((fmt.get("tags") or {}).get("com.apple.quicktime.model")
                 or (fmt.get("tags") or {}).get("model") or "")
        fixed = [(c.get("stream"), c.get("shift_samples"))
                 for c in (r.get("channels") or [])
                 if c.get("decision") == "fixed"]
        rows.append({"name": r["name"], "file": str(p),
                     "verdict": r.get("status"), "size_mb": round(size / 2**20, 1),
                     "duration": round(dur, 2), "audio_streams": len(audio),
                     "make": maker, "model": model,
                     "fixed": fixed, "group": r.get("group")})
        print(f"{r['name']:<26}{(r.get('status') or '?'):<18}"
              f"{size / 2**20:>9.1f}{dur:>9.1f}{len(audio):>6}"
              f"{str(maker)[:10]:>10}  {fixed}")
    dest = OUT / "e2e_candidates.json"
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    # compact stratification summary
    print("\n# stratum counts")
    from collections import Counter
    c = Counter((r["verdict"], len(r["audio_streams"])) for r in rows)
    for k, v in sorted(c.items(), key=lambda kv: str(kv[0])):
        print(f"  {k}: {v}")
    print(f"\n-> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
