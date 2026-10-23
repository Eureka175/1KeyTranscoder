#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second correction round: s_total(t) = s_tv(t) + s_residual(t).

s_tv comes from applytv_<stem>.json (the drift-tracked curve already applied to
aligned_tv/), s_residual from refine_curve_<stem>.json (5-minute block residuals
measured on the TV-aligned audio). Applying the sum gives a new alignment whose
strong-coherence blocks should land within a few samples of zero.

The output writes ch{i}_tv2.f32 / .wav (same rules: zero-fill out of range, total
|shift| <= 4800 samples = 100 ms).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

OUT = Path(r"E:\1kt_work\long_align")
SR = 48000
MAX_SHIFT = 4800
CHUNK = 5_000_000


def write_shifted(stem: str, i: int, grid_t: np.ndarray, grid_s: np.ndarray,
                  n: int, dst: Path) -> dict:
    src = np.memmap(la.pcm_path(stem, i), dtype=np.float32, mode="r")
    maxs = 0.0
    with dst.open("wb") as fh:
        for s0 in range(0, n, CHUNK):
            s1 = min(n, s0 + CHUNK)
            idx = np.arange(s0, s1, dtype=np.float64)
            shift = np.interp(idx, grid_t, grid_s)
            maxs = max(maxs, float(np.abs(shift).max()))
            if maxs > MAX_SHIFT:
                raise SystemExit(f"CH{i} shift {maxs:.0f} > {MAX_SHIFT}")
            pos = idx + shift
            invalid = (pos < 0.0) | (pos > n - 1.0)
            np.clip(pos, 0.0, n - 1.0, out=pos)
            block = np.interp(pos, np.arange(s0, s1, dtype=np.float64),
                              np.asarray(src[s0:s1], dtype=np.float64))
            block[invalid] = 0.0
            fh.write(block.astype(np.float32).tobytes())
    del src
    return {"max_shift": maxs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    args = ap.parse_args()
    stem = args.stem
    n = la.n_samples(stem)
    outdir = OUT / "aligned_tv2" / stem
    outdir.mkdir(parents=True, exist_ok=True)
    base = json.loads((OUT / f"applytv_{stem}.json").read_text(encoding="utf-8"))
    refine = json.loads((OUT / f"refine_curve_{stem}.json").read_text(encoding="utf-8"))
    meta = {"stem": stem, "n": n, "channels": {}}
    # CH1: identity; CH2: constant -4 (same as before)
    src0 = np.memmap(la.pcm_path(stem, 0), dtype=np.float32, mode="r")
    with (outdir / "ch0_tv2.f32").open("wb") as fh:
        for s0 in range(0, n, CHUNK):
            fh.write(np.asarray(src0[s0:s0 + CHUNK], dtype=np.float32).tobytes())
    del src0
    src1 = np.memmap(la.pcm_path(stem, 1), dtype=np.float32, mode="r")
    out1 = np.zeros(n, dtype=np.float32)
    out1[4:] = np.asarray(src1[:n - 4])
    out1.tofile(outdir / "ch1_tv2.f32")
    del src1, out1
    meta["channels"]["0"] = {"shift": "constant 0"}
    meta["channels"]["1"] = {"shift": "constant -4 samples"}
    for i in (2, 3):
        ch = base["channels"][str(i)]
        if not ch.get("ok"):
            print(f"CH{i + 1}: original curve missing")
            continue
        gt = np.asarray(ch["grid_t"], dtype=np.float64)
        gs = np.asarray(ch["grid_s"], dtype=np.float64)
        rc = refine.get(str(i)) or {}
        if rc.get("ok"):
            rt = np.asarray(rc["grid_t"], dtype=np.float64)
            rs = np.asarray(rc["grid_s"], dtype=np.float64)
            # 合并到同一 1 s 网格
            gt_new = np.arange(0, n, SR, dtype=np.float64)
            s_orig = np.interp(gt_new, gt, gs)
            s_res = np.interp(gt_new, rt, rs)
            gs_new = s_orig + s_res
        else:
            gt_new, gs_new = gt, gs
        info = write_shifted(stem, i, gt_new, gs_new, n, outdir / f"ch{i}_tv2.f32")
        meta["channels"][str(i)] = {
            "ok": True, "max_shift": info["max_shift"],
            "refine_used": bool(rc.get("ok")),
            "refine_n_blocks": rc.get("n_blocks"),
            "grid_t": gt_new.tolist(), "grid_s": gs_new.tolist()}
        print(f"CH{i + 1}: applied s_tv + residual; max|shift|={info['max_shift']:.0f} "
              f"samples ({info['max_shift'] / SR * 1000:.1f} ms) "
              f"refine={'yes' if rc.get('ok') else 'no'}")
    # wavs
    for i in range(4):
        f = outdir / f"ch{i}_tv2.f32"
        if not f.is_file():
            continue
        cmd = [str(la.FFMPEG), "-v", "error", "-nostdin", "-y",
               "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(f),
               "-c:a", "pcm_s24le", str(outdir / f"ch{i}_tv2.wav")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr[-300:])
    (OUT / f"applytv2_{stem}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写 {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
