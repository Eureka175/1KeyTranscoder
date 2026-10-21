#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the archive copy with the 4ch-proof copy of the same clip.

The proof folder is the untouched original; the archive copy went through the
transcode pipeline. If their audio layouts differ, every measurement conclusion
drawn from the archive copies has to be re-checked against the originals -- and
if they are the same layout, the earlier low per-window yield is a property of
the material, not of the copies.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FFPROBE = Path(r"F:\1KeyTranscoder\tools\ffprobe.exe")


def probe(p: Path) -> dict:
    r = subprocess.run([str(FFPROBE), "-v", "error", "-show_streams",
                        "-show_format", "-of", "json", str(p)],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return json.loads(r.stdout or "{}")


def show(tag: str, p: Path) -> dict:
    d = probe(p)
    fmt = d.get("format") or {}
    print(f"== {tag}: {p.name}  {p.stat().st_size / 1024 ** 2:.0f} MiB  "
          f"时长 {float(fmt.get('duration') or 0):.2f}s  "
          f"码率 {float(fmt.get('bit_rate') or 0) / 1e6:.1f} Mb/s")
    audio = []
    for s in d.get("streams") or []:
        if s.get("codec_type") == "audio":
            audio.append(s)
            print(f"   audio#{s.get('index')} {s.get('codec_name')} "
                  f"{s.get('sample_fmt')} {s.get('channels')}ch "
                  f"{s.get('sample_rate')}Hz "
                  f"bits={s.get('bits_per_raw_sample') or s.get('bits_per_sample')} "
                  f"dur={s.get('duration')}")
        else:
            print(f"   {s.get('codec_type')}#{s.get('index')} "
                  f"{s.get('codec_name')} {s.get('width')}x{s.get('height')}")
    return {"n_audio": len(audio),
            "codecs": sorted({s.get("codec_name") for s in audio}),
            "layout": sorted({s.get("channels") for s in audio}),
            "duration": float(fmt.get("duration") or 0)}


def main() -> int:
    names = sys.argv[1:] or ["20260904_C1213"]
    for nm in names:
        a = Path(r"F:\align_4ch_proof") / f"{nm}.MP4"
        cands = list(Path("I:/").glob(f"*/{nm}.MP4"))
        cands = [c for c in cands if "compact" not in str(c)]
        if not a.is_file():
            print(f"proof 缺失: {a}")
            continue
        x = show("proof", a)
        if cands:
            y = show("归档", cands[0])
            print(f"   对比: audio 数 {x['n_audio']} vs {y['n_audio']}, "
                  f"codec {x['codecs']} vs {y['codecs']}, "
                  f"布局 {x['layout']} vs {y['layout']}, "
                  f"时长 {x['duration']:.2f}s vs {y['duration']:.2f}s")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
