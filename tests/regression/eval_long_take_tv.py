#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end evaluation of the **existing** time-varying sync path on long takes.

The point is not to re-measure the probe's window coverage (already known) but to
answer the product question with the production code path:

    global single shift vs. per-window correction (audio_sync_tv_exec), and which
    D2 gap policy / D5 coordinate actually keep the *output* aligned

Every configuration is run through `core.channel_sync.run_channel_sync`, i.e. the
same measurement, gates, write-back and self-check that production uses. The
acceptance number is then measured **outside** the tool from the written audio
files: per 30 s window, the residual lag between the corrected track and its own
reference, in samples. This is the "e2e residual" of the original requirement and
it does not trust the tool's internal verification -- it re-measures it.

No video encoding: only the fixed PCM audio tracks are written.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                      # noqa: E402
import core.channel_sync as cs                   # noqa: E402
import core.sync_estimate as se                  # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
WORK = Path(r"E:\1kt_scratch\tv_eval")
OUT = Path(r"E:\1kt_work\tv_eval")
PCM_CACHE = Path(r"E:\1kt_scratch\pcm")

CLIPS = [
    REPO / "data" / "align_4ch_proof" / "20260919_C1305.MP4",
    REPO / "data" / "align_4ch_proof" / "20260919_C1306.MP4",
]

CONFIGS = [
    ("global", {}),
    ("tv_linear_latest", {"time_varying": True, "tv_policy": "linear",
                          "tv_coordinate": "latest-start"}),
    ("tv_linear_nearest", {"time_varying": True, "tv_policy": "linear",
                           "tv_coordinate": "nearest-centre"}),
    ("tv_hold_latest", {"time_varying": True, "tv_policy": "hold",
                        "tv_coordinate": "latest-start"}),
]

WIN_S = 30.0
HOP_S = 10.0
MAX_LAG = 400          # search radius for the residual lag, in samples


def decode_channel(src: Path, stream_index: int, sr: int, stem: str,
                   tag: str) -> np.ndarray:
    """Decode one audio stream to mono f32, reusing the probe's PCM cache."""
    cache = PCM_CACHE / stem / f"ch{stream_index}.f32"
    if cache.is_file():
        return np.fromfile(cache, dtype=np.float32)
    tmp = WORK / "_decode" / f"{stem}_{tag}_{stream_index}.f32"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    ok, err = cs._decode_stream(FFMPEG, src, stream_index, sr, tmp, "f32")
    if not ok:
        raise RuntimeError(f"decode failed: {err}")
    arr = np.fromfile(tmp, dtype=np.float32)
    try:
        tmp.unlink()
    except OSError:
        pass
    return arr


def window_lags(out: np.ndarray, ref: np.ndarray, sr: int) -> dict:
    """Per 30 s window: lag L with out[n] ~ ref[n + L]; returns |L| statistics."""
    win = int(round(WIN_S * sr))
    hop = int(round(HOP_S * sr))
    lags: list[int] = []
    strengths: list[float] = []
    n = min(out.shape[0], ref.shape[0])
    start = 0
    while start + win <= n:
        a = out[start:start + win].astype(np.float64)
        b = ref[start:start + win].astype(np.float64)
        a = a - a.mean()
        b = b - b.mean()
        if np.sqrt((a * a).sum()) < 1e-6 or np.sqrt((b * b).sum()) < 1e-6:
            start += hop
            continue
        nfft = 1 << int(np.ceil(np.log2(win + MAX_LAG * 2)))
        A = np.fft.rfft(a, nfft)
        B = np.fft.rfft(b, nfft)
        cc = np.fft.irfft(A * np.conj(B), nfft)
        # positive lag: out[n] ~ ref[n + L] -> cc peak at +L
        cand = np.concatenate([cc[:MAX_LAG + 1], cc[-MAX_LAG:]])
        idx = int(np.argmax(np.abs(cand)))
        lag = idx if idx <= MAX_LAG else idx - (2 * MAX_LAG + 1)
        strength = float(np.abs(cand[idx]) / (np.linalg.norm(a) * np.linalg.norm(b)))
        lags.append(lag)
        strengths.append(strength)
        start += hop
    if not lags:
        return {"n_windows": 0}
    absl = np.abs(np.asarray(lags, dtype=np.float64))
    strong = np.asarray(strengths) >= 0.15
    return {
        "n_windows": int(len(lags)),
        "n_resolved": int(strong.sum()),
        "median_abs": float(np.median(absl[strong])) if strong.any() else None,
        "p90_abs": float(np.percentile(absl[strong], 90)) if strong.any() else None,
        "max_abs": float(np.max(absl[strong])) if strong.any() else None,
        "n_le_1": int((absl[strong] <= 1).sum()) if strong.any() else 0,
        "frac_le_1": (float((absl[strong] <= 1).mean()) if strong.any() else None),
    }


def run_config(clip: Path, label: str, over: dict, streams: list[dict],
               sr: int, loglines: list[str]) -> dict:
    t0 = time.time()
    work = WORK / label / clip.stem
    work.mkdir(parents=True, exist_ok=True)
    opts = cs.production_opts(over)
    rep = cs.run_channel_sync(
        source=clip, ffmpeg=FFMPEG, work_dir=work, streams=streams,
        opts=opts, log=lambda m: loglines.append(str(m)),
    )
    res: dict = {
        "clip": clip.stem, "config": label, "status": rep.get("status"),
        "detail": rep.get("detail"), "anchor": rep.get("anchor_stream"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    rows = rep.get("channels") or []
    files = [Path(p) for p in (rep.get("audio_files") or [])]
    anchor = rep.get("anchor_stream")
    per_channel = {}
    if rep.get("status") == "applied" and files:
        for i, row in enumerate(rows):
            if row.get("decision") != "fixed":
                continue
            ref1 = row.get("reference") or ((anchor + 1) if anchor is not None else 1)
            ref_index = int(ref1) - 1
            try:
                out = decode_channel(files[i], 0, sr, f"{clip.stem}_{label}_out{i}", "out")
                ref = decode_channel(clip, ref_index, sr, clip.stem, "src")
            except Exception as exc:                 # noqa: BLE001
                per_channel[i] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            n = min(out.shape[0], ref.shape[0])
            stats = window_lags(out[:n], ref[:n], sr)
            stats["ref_stream"] = ref_index
            stats["shift_samples"] = row.get("shift_samples")
            tv = row.get("time_varying") or {}
            stats["tv_executed"] = tv.get("executed")
            stats["tv_states"] = tv.get("state_counts")
            stats["tv_verification"] = tv.get("verification")
            per_channel[i] = stats
    else:
        res["rows_summary"] = [
            {"stream": r.get("stream"), "decision": r.get("decision"),
             "reason": r.get("reason"), "shift": r.get("shift_samples"),
             "tv": (r.get("time_varying") or {}).get("reason")
             or (r.get("time_varying") or {}).get("executed")}
            for r in rows
        ]
    res["channels"] = per_channel
    return res


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for clip in CLIPS:
        if not clip.is_file():
            print(f"缺文件: {clip}")
            continue
        doc = ar.probe(clip)
        streams = doc.get("streams") or []
        sr = next(int(s["sample_rate"]) for s in streams
                  if s.get("codec_type") == "audio")
        print(f"\n=== {clip.name} sr={sr} streams={len(streams)}")
        for label, over in CONFIGS:
            loglines: list[str] = []
            print(f"  [{label}] ...", flush=True)
            r = run_config(clip, label, over, streams, sr, loglines)
            results.append(r)
            print(f"    status={r['status']} anchor={r['anchor']} "
                  f"({r['elapsed_s']}s)")
            for i, st in (r.get("channels") or {}).items():
                if "error" in st:
                    print(f"    CH{i + 1} error {st['error']}")
                    continue
                print(f"    CH{i + 1} ref=CH{st['ref_stream'] + 1} "
                      f"windows={st['n_windows']} resolved={st['n_resolved']} "
                      f"|lag| p50={st['median_abs']} p90={st['p90_abs']} "
                      f"max={st['max_abs']} <=1:{st['n_le_1']}/{st['n_resolved']} "
                      f"tv={st['tv_executed']} states={st['tv_states']}")
            (OUT / "tv_eval.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'tv_eval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
