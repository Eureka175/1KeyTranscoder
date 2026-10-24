#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量音频对齐可行性探测 —— 复用生产 API（core.channel_sync / core.probe）。

设计要点（对应用户要求：性能优先、优先复用已有 API）:
  * 容器/流事实走生产 `core.probe.probe_streams()`；
  * 文件级布局门走生产 `core.channel_sync.eligible_audio()` (2CH/1CH 默认不对齐)；
  * 测量/锚点/健康检查/质量门/决策全走生产 `core.channel_sync.run_channel_sync()`
    —— 不另写一套对齐算法；transcode 模式、无 dst，不改源文件；
  * 文件级并行（ProcessPoolExecutor），每个 worker 独立 E: 临时工作目录，
    结束即删；内存/磁盘都有界；
  * 若默认阈值把所有轨判成 silent（真实素材里存在 -60 dBFS 以下但仍有内容的
    录音），用放宽阈值（silent/frame 门 -85 dBFS，仅本次探测）重跑一次，
    记录“alignable_relaxed_threshold”，用于区分“内容不可对齐”与“阈值过保守”。

输出: 每文件一条 JSON 记录（配置、静音轨、逐轨决策/延迟/漂移/置信度、
默认与放宽两次状态），供后续统计与图表使用。

用法:
  python work/probe_align_api.py --root <dir> --out <json>
        [--workers 6] [--patterns "*.MP4,*.mp4"]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
TMP_ROOT = Path(r"E:\1kt_scratch\probe_work")

RELAXED = {"silent_rms_dbfs": -85.0, "frame_min_rms_dbfs": -85.0}
KEEP_CH_FIELDS = (
    "stream", "role", "anchor", "decision", "reason", "delay_ms", "delay_samples",
    "fine_delay_ms", "confidence", "polarity", "drift_ppm", "constant",
    "rms_dbfs", "usable_frames", "warnings", "traj_mad_ms", "traj_spread_samples",
)


def _layout(streams: list[dict]) -> dict:
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    data = [s for s in streams if s.get("codec_type") == "data"]
    out = {
        "n_audio": len(audio),
        "audio": [{
            "index": s.get("index"),
            "codec": s.get("codec_name"),
            "tag": s.get("codec_tag_string"),
            "rate": s.get("sample_rate"),
            "channels": s.get("channels"),
            "bits": s.get("bits_per_sample") or s.get("bits_per_raw_sample"),
            "dur_s": s.get("duration"),
        } for s in audio],
        "n_data": len(data),
        "has_rtmd": any((s.get("codec_tag_string") or "") == "rtmd"
                        or (s.get("codec_name") or "") == "rtmd" for s in data),
        "video": None,
    }
    if video:
        v = video[0]
        out["video"] = {
            "codec": v.get("codec_name"), "tag": v.get("codec_tag_string"),
            "w": v.get("width"), "h": v.get("height"),
            "fps": v.get("r_frame_rate"), "avg_fps": v.get("avg_frame_rate"),
            "pix_fmt": v.get("pix_fmt"),
            "bits": v.get("bits_per_raw_sample"),
            "nb_frames": v.get("nb_frames"),
        }
    return out


def _summarize(res: dict) -> dict:
    chans = []
    for ch in res.get("channels", []):
        chans.append({k: ch.get(k) for k in KEEP_CH_FIELDS if k in ch})
    return {
        "status": res.get("status"),
        "detail": res.get("detail"),
        "anchor_stream": res.get("anchor_stream"),
        "anchor_channel": res.get("anchor_channel"),
        "channels": chans,
        "fixed_files": len(res.get("fixed_files") or []),
    }


def probe_one(path_str: str) -> dict:
    t0 = time.time()
    path = Path(path_str)
    rec: dict = {"path": str(path), "name": path.name,
                 "size_mib": round(path.stat().st_size / 2**20, 1)}
    work = TMP_ROOT / f"{path.stem}_{os.getpid()}"
    try:
        from core.channel_sync import eligible_audio, run_channel_sync
        from core.probe import probe_streams

        fmt, streams = probe_streams(FFPROBE, path)
        rec["duration_s"] = float(fmt.get("duration") or 0.0)
        rec.update(_layout(streams))
        ok, why = eligible_audio(streams)
        rec["eligible"] = bool(ok)
        rec["eligibility"] = why
        if not ok:
            rec["verdict"] = "not_eligible_layout"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec
        rms = []
        logs: list[str] = []
        res = run_channel_sync(source=path, ffmpeg=FFMPEG, work_dir=work,
                              streams=streams, opts={},
                              log=lambda m: logs.append(m))
        rec["default"] = _summarize(res)
        rec["default_log"] = logs[-12:]
        silent = [c for c in res.get("channels", [])
                  if c.get("reason") == "silent_track"]
        rec["n_silent"] = len(silent)
        rec["rms_dbfs"] = [c.get("rms_dbfs") for c in res.get("channels", [])]
        # 信号统计（用于判定“真静音”vs“阈值保守”）
        n_audio = rec["n_audio"]
        status = res.get("status")
        # 默认失败且所有轨都低于静音门 -> 用放宽阈值再测一次
        if status == "measure_failed" and rec["n_silent"] >= n_audio:
            shutil.rmtree(work, ignore_errors=True)
            work2 = TMP_ROOT / f"{path.stem}_{os.getpid()}_relaxed"
            try:
                logs2: list[str] = []
                res2 = run_channel_sync(
                    source=path, ffmpeg=FFMPEG, work_dir=work2,
                    streams=streams, opts=dict(RELAXED),
                    log=lambda m: logs2.append(m))
                rec["relaxed"] = _summarize(res2)
                rec["relaxed_log"] = logs2[-8:]
                shutil.rmtree(work2, ignore_errors=True)
            except Exception as exc:          # noqa: BLE001
                rec["relaxed"] = {"status": "error", "error": repr(exc)}
                shutil.rmtree(work2, ignore_errors=True)
        # 结论
        chans = rec["default"]["channels"]
        non_silent = [c for c in chans if c.get("reason") != "silent_track"]
        if status in ("applied", "already_aligned"):
            rec["verdict"] = "alignable"
        elif rec["n_silent"] >= n_audio:
            relax = rec.get("relaxed") or {}
            if relax.get("status") in ("applied", "already_aligned"):
                rec["verdict"] = "alignable_relaxed_threshold"
                rec["relaxed_note"] = (
                    "默认 silent 阈值把整轨判静音；放宽后对齐成功（阈值偏保守）")
            else:
                rec["verdict"] = "not_alignable_all_silent"
        elif len(non_silent) == 2 and n_audio > 2:
            rec["verdict"] = "two_channel_only"
            # 看这两条有效轨之间是否能建立关系
            pair_ok = [c for c in non_silent
                       if c.get("decision") in ("fixed", "already_aligned")]
            rec["two_ch_valid_decision"] = pair_ok[0]["decision"] if pair_ok else None
            rec["two_ch_detail"] = rec["default"]["detail"]
        elif status == "measure_failed":
            rec["verdict"] = "not_alignable"
        else:
            rec["verdict"] = status or "unknown"
    except Exception as exc:                  # noqa: BLE001
        rec["verdict"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[-800:]
    finally:
        shutil.rmtree(work, ignore_errors=True)
        rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--patterns", default="*.MP4,*.mp4")
    args = ap.parse_args()
    root = Path(args.root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    files = []
    for pat in [p.strip() for p in args.patterns.split(",") if p.strip()]:
        for f in sorted(root.rglob(pat)):
            if f not in seen:
                seen.add(f)
                files.append(f)
    print(f"{root}: {len(files)} file(s), workers={args.workers}", flush=True)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_one, str(f)): f for f in files}
        for k, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as exc:          # noqa: BLE001
                f = futs[fut]
                rec = {"path": str(f), "name": f.name, "verdict": "error",
                       "error": repr(exc)}
            results.append(rec)
            print(f"[{k}/{len(files)}] {rec.get('name')} -> "
                  f"{rec.get('verdict')} ({rec.get('elapsed_s')}s, "
                  f"total {time.time() - t0:.0f}s)", flush=True)
            if k % 10 == 0:
                out.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\nwritten {out} ({len(results)} records, {time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
