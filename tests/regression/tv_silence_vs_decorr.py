#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why is per-window yield so low on the hard clips -- silence or decorrelation?

`FrameDelay` carries only (center, delay, confidence); frames rejected by the
RMS floor are dropped from the trajectory, so a low usable count can mean two
very different things:

  * **silence**   -- there is nothing to correlate. Time-varying correction is
    then both impossible and unnecessary (no audible content to misalign).
  * **decorrelation** -- there is signal, but the two tracks do not share it
    (different mics, different content). Fixable only with a different
    measurement, and it is what decides whether the tv path is worth building
    for these clips.

This prints, per file, the RMS levels of both raw tracks in 200 ms frames next
to the confidence the estimator produced, so the two cases can be told apart.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
OUT = REPO / "work" / "regression_r087" / "out"
SCRATCH = Path(r"E:\1kt_scratch\tv_probe")
SR = 48000
FRAME_MS = 200.0
RMS_FLOOR = -50.0
FILES = ["20260830_C0917", "20260830_C0923", "20260830_C0922"]


def decode(path: Path, idx: int, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(path),
         "-map", f"0:a:{idx}", "-vn", "-sn", "-dn", "-f", "f32le",
         "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SR), str(out)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    return r.returncode == 0 and out.is_file()


def frame_rms(path: Path) -> np.ndarray:
    x = np.memmap(path, dtype=np.float32, mode="r")
    hop = int(SR * FRAME_MS / 1000.0)
    k = len(x) // hop
    rms = np.empty(k)
    for i in range(k):
        seg = np.asarray(x[i * hop:(i + 1) * hop])
        rms[i] = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
    return 20.0 * np.log10(np.maximum(rms, 1e-9))


def main() -> int:
    arm = json.loads((OUT / "ARCH_on5.json").read_text(encoding="utf-8"))
    by_name = {r.get("name"): r for r in arm}
    for nm in FILES:
        r = by_name.get(nm + ".MP4") or by_name.get(nm)
        if not r:
            print(f"{nm}: 不在 arm 里")
            continue
        path = Path(r.get("file") or "")
        target = None
        for c in r.get("channels") or []:
            if (c.get("decision") == "untouched"
                    and isinstance(c.get("delay_ms"), (int, float))):
                target = c
                break
        if target is None or not path.is_file():
            print(f"{nm}: 取不到目标轨")
            continue
        anchor = r.get("anchor")
        print(f"\n=== {nm}  目标 CH{(target.get('stream') or 0) + 1} "
              f"锚点 CH{(anchor or 0) + 1}  偏差 {target['delay_ms']:+.2f}ms "
              f"({target.get('reason')})")
        w = SCRATCH / nm
        w.mkdir(parents=True, exist_ok=True)
        pa, pb = w / "a.f32", w / "b.f32"
        if not (decode(path, anchor, pa) and decode(path, target.get("stream"), pb)):
            print("  解码失败")
            continue
        ra, rb = frame_rms(pa), frame_rms(pb)
        k = min(len(ra), len(rb))
        ra, rb = ra[:k], rb[:k]
        quiet = int(np.sum((ra < RMS_FLOOR) | (rb < RMS_FLOOR)))
        both_ok = k - quiet
        print(f"  帧数 {k}；至少一侧低于 {RMS_FLOOR:.0f} dBFS 的帧 {quiet} "
              f"({100 * quiet / k:.0f}%)；两侧都有信号 {both_ok} "
              f"({100 * both_ok / k:.0f}%)")
        print(f"  锚点电平 中位 {np.median(ra):.1f} dBFS  "
              f"目标电平 中位 {np.median(rb):.1f} dBFS")
        # 两侧都有信号的那些帧里, 估计器给出的置信度如何
        sel = np.where((ra >= RMS_FLOOR) & (rb >= RMS_FLOOR))[0]
        if len(sel):
            print(f"  两侧有信号帧的电平区间: "
                  f"{np.percentile(ra[sel], 10):.0f}..{np.percentile(ra[sel], 90):.0f}"
                  f" dBFS")
        print(f"  估计器可用帧 {target.get('usable_frames')} / {k} = "
              f"{100 * (target.get('usable_frames') or 0) / k:.1f}%")
        shutil.rmtree(w, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
