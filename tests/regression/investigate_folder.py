#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材文件夹调研程序 —— CPU only / 离线 / 可续跑。

**几乎不自己实现判定逻辑**：每个文件的规范检查整套复用 `archive_repair` 的检查
套件（CTR-01..08 / FMT-01..05 / AUD-01..04，其中 AUD 直接调用生产
`run_channel_sync`），另外补三类它不覆盖的信息：逐块电平分布、立体声 L/R 相位
（仅信息）、4-mono 文件的通道对连通性与逐窗证据密度。

复用的已有 API::

    archive_repair.probe / container_facts / check_container / check_format /
    check_audio / summarise_check / iter_media            —— 规范检查全套
    core.channel_sync.production_opts / eligible_audio    —— 生产工作点与资格
    core.channel_sync._decode_stream / _track_health      —— 解码与分块 RMS
    core.sync_estimate.estimate_pair / summarize_trajectory /
    measured_displacement_ms / classify_constant /
    sample_pair_snr                                       —— 通道对测量
    core.audio_sync_tv.required_window_count / measured_window_count /
    window_index                                          —— 窗几何
    preservation.sample_entry.audio_entry_consistency     —— 由 CTR-08 覆盖

分阶段（每文件一个 checkpoint，中断重跑自动续）：

    S0 checks    整套规范检查（容器/格式/音频对齐）
    S1 profile   每 60 s 块电平与静音（逐音频流）
    S2 stereo    2ch 文件 L/R 相位与分块相关（**仅信息**: 产品不对齐 2ch）
    S3 pairs     4-mono 文件: 12 个有序通道对 × 2 几何、连通性、30 s 窗证据密度
    S4 report    逐机位汇总、检查码统计、需要处理的清单

用法::

    python tests/regression/investigate_folder.py \
        --root "I:\\2026.09.24" --out "F:\\_inv_2026_09_24" \
        --budget-minutes 180

输出 < 100 MB；不修改任何源文件。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np                                     # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import archive_repair as ar                            # noqa: E402
import core.channel_sync as cs                          # noqa: E402
from core import audio_sync_tv as tv                    # noqa: E402
from core import sync_estimate as se                    # noqa: E402
from core.probe import probe_source                     # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
SR = 48000
PCM_CACHE = Path(r"E:\1kt_scratch\pcm")
SCRATCH = Path(r"E:\1kt_scratch\investigate")
BLOCK_S = 60.0
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1,
                              default=_default), encoding="utf-8")
    tmp.replace(path)


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


class Budget:
    def __init__(self, minutes: float | None):
        self.limit = None if not minutes else minutes * 60.0

    @property
    def elapsed(self) -> float:
        return time.time() - T0

    def done(self) -> bool:
        return self.limit is not None and self.elapsed >= self.limit


# ---------------------------------------------------------------------------
# S0 整套规范检查（复用 archive_repair）
# ---------------------------------------------------------------------------
def stage_checks(path: Path) -> dict:
    doc = ar.probe(path)
    streams = doc.get("streams") or []
    if not streams:
        return {"error": "ffprobe 读不到流",
                "stderr": (doc.get("_stderr") or "")[:200]}
    opts = cs.production_opts()
    summary = ar.summarise_check(path, doc, opts)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    eligible, why = cs.eligible_audio(streams)
    try:
        dur = float((doc.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    # summarise_check 的真实返回: {path, overall, checks, audio, facts}
    return {
        "duration_s": dur,
        "size_bytes": path.stat().st_size,
        "n_audio": len(audio),
        "n_video": len(video),
        "audio_layout": [{"codec": s.get("codec_name"),
                          "channels": s.get("channels"),
                          "sample_rate": s.get("sample_rate"),
                          "codec_tag": s.get("codec_tag_string")}
                         for s in audio],
        "eligible_4mono": bool(eligible),
        "eligible_reason": why,
        "verdict": summary.get("overall"),
        "items": summary.get("checks") or [],
        "audio_data": summary.get("audio") or {},
        "facts": summary.get("facts") or {},
    }


# ---------------------------------------------------------------------------
# S1 电平/静音 profile（复用 _decode_stream + _track_health）
# ---------------------------------------------------------------------------
def decode_channel(path: Path, stream_index: int, dst: Path) -> bool:
    if dst.is_file() and dst.stat().st_size > 0:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    ok, err = cs._decode_stream(FFMPEG, path, stream_index, SR, dst, "f32")
    if not ok:
        log(f"    解码失败 {path.name} stream {stream_index}: {err}")
    return ok


def stage_profile(path: Path, n_audio: int, scratch: Path,
                  budget: Budget) -> dict:
    chans: list[dict] = []
    for si in range(n_audio):
        if budget.done():
            chans.append({"stream": si, "skipped": "budget"})
            continue
        dst = scratch / path.stem / f"ch{si}.f32"
        if not decode_channel(path, si, dst):
            chans.append({"stream": si, "error": "decode_failed"})
            continue
        arr = np.memmap(dst, dtype=np.float32, mode="r")
        n = int(arr.size)
        bs = int(round(BLOCK_S * SR))
        blocks = max(1, n // bs)
        rms = np.zeros(blocks)
        for b in range(blocks):
            seg = np.asarray(arr[b * bs:(b + 1) * bs])
            _ok, db = cs._track_health(seg, np)     # 复用生产 RMS 口径
            rms[b] = db if db == db else -120.0
        finite, whole_db = cs._track_health(arr, np)
        silent = (rms < -50.0).tolist()
        runs, start = [], None
        for i, f in enumerate(silent + [False]):
            if f and start is None:
                start = i
            elif not f and start is not None:
                runs.append([start, i - start])
                start = None
        chans.append({
            "stream": si,
            "seconds": round(n / SR, 2),
            "finite": bool(finite),
            "rms_db_whole": round(float(whole_db), 2)
            if whole_db == whole_db else None,
            "rms_db_p10": float(np.percentile(rms, 10)),
            "rms_db_p50": float(np.percentile(rms, 50)),
            "rms_db_p90": float(np.percentile(rms, 90)),
            "blocks": int(blocks),
            "silent_blocks": int(sum(silent)),
            "silent_fraction": float(sum(silent) / max(1, blocks)),
            "longest_silent_run_s": float(max((x for _, x in runs),
                                              default=0) * BLOCK_S),
        })
        del arr
    # 解码结果**保留**在 scratch/<stem>/chN.f32: pairs 阶段直接复用, 省一次
    # 全片解码 (4-mono 大文件上是分钟级)。整轮结束时统一清理。
    return {"channels": chans}


# ---------------------------------------------------------------------------
# S2 立体声 L/R（信息项；核心库没有 2ch 对齐 API，产品也决定不对齐）
# ---------------------------------------------------------------------------
def _ncc_peak(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[float, int]:
    a = a - float(a.mean())
    b = b - float(b.mean())
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0, 0
    n = 1 << int(np.ceil(np.log2(a.size + b.size)))
    cc = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    cc = np.concatenate([cc[-max_lag:], cc[:max_lag + 1]]) / (na * nb)
    i = int(np.argmax(np.abs(cc)))
    return float(abs(cc[i])), int(i - max_lag)


def stage_stereo(path: Path, layout: list[dict], scratch: Path,
                 budget: Budget) -> dict | None:
    if len(layout) != 1 or int(layout[0].get("channels") or 0) != 2:
        return None
    codec = str(layout[0].get("codec") or "")
    if codec not in cs.DEFAULTS["supported_codecs"]:
        return {"note": "非 PCM 立体声, 不做相位分析", "codec": codec}
    if budget.done():
        return {"skipped": "budget"}
    dst = scratch / f"{path.stem}_stereo.f32"
    if not (dst.is_file() and dst.stat().st_size > 0):
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(path),
             "-vn", "-map", "0:a:0", "-acodec", "pcm_f32le",
             "-f", "f32le", "-ar", str(SR), str(dst)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if r.returncode != 0 or not dst.is_file():
            return {"error": f"decode rc={r.returncode} "
                             f"{(r.stderr or '')[:150]}"}
    arr = np.memmap(dst, dtype=np.float32, mode="r")
    frames = int(arr.size // 2)
    if frames < SR:
        del arr
        return {"note": "过短, 不做相位分析", "seconds": round(frames / SR, 2)}
    left = arr[0:frames * 2:2]
    right = arr[1:frames * 2:2]
    seg = min(int(30 * SR), frames)
    step = max(1, frames // 12)
    peaks, lags = [], []
    for start in range(0, max(1, frames - seg + 1), step):
        # np.array(...) 拷贝: memmap 视图会让 Windows 无法删除临时文件
        la = np.array(left[start:start + seg], dtype=np.float32)
        rb = np.array(right[start:start + seg], dtype=np.float32)
        peak, lag = _ncc_peak(la, rb, int(0.005 * SR))
        peaks.append(peak)
        lags.append(lag)
        if len(peaks) >= 8:
            break
    del left, right, arr
    try:
        dst.unlink()
    except OSError:
        pass                 # 句柄释放前删不掉; 主流程结束时统一清理 scratch
    return {"seconds": round(frames / SR, 2),
            "lr_ncc_median": float(np.median(peaks)),
            "lr_lag_median_samples": int(np.median(lags)),
            "lr_lag_median_ms": round(float(np.median(lags)) * 1000.0 / SR, 4),
            "lr_lag_spread_samples": int(max(lags) - min(lags)),
            "note": "仅信息: 产品不对齐 2ch, 此值不驱动修正"}


# ---------------------------------------------------------------------------
# S3 4-mono: 通道对 / 连通性 / 逐窗证据
# ---------------------------------------------------------------------------
def stage_pairs(path: Path, scratch: Path) -> dict:
    eff = cs.effective_opts(cs.production_opts())
    raw: list[Path] = []
    d = scratch / path.stem
    d.mkdir(parents=True, exist_ok=True)
    cached = PCM_CACHE / path.stem
    for idx in range(4):
        dst = d / f"ch{idx}.f32"
        if dst.is_file() and dst.stat().st_size > 0:
            raw.append(dst)
            continue
        c = cached / f"ch{idx}.f32"
        if c.is_file() and c.stat().st_size > 0:
            try:
                os.link(c, dst)
            except OSError:
                shutil.copyfile(c, dst)
            raw.append(dst)
            continue
        if not decode_channel(path, idx, dst):
            return {"error": "decode_failed"}
        raw.append(dst)
    n = min(p.stat().st_size // 4 for p in raw)
    pairs = [(i, j) for i in range(4) for j in range(4) if i != j]
    geoms = [(eff["frame_ms"], eff["hop_ms"], "prod"),
             (1000.0, 500.0, "coarse1000")]
    edges: dict = {}
    for frame_ms, hop_ms, gname in geoms:
        edges[gname] = {}
        for ref, tgt in pairs:
            try:
                est = se.estimate_pair(
                    str(raw[ref]), str(raw[tgt]), sample_rate=SR,
                    search_window_ms=eff["search_window_ms"],
                    frame_ms=frame_ms, hop_ms=hop_ms,
                    anchor_segment_seconds=eff["anchor_segment_seconds"],
                    min_confidence=eff["min_confidence"],
                    coarse_rate=eff["coarse_rate"],
                    fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                    min_usable_frames=eff["min_usable_frames"],
                    frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                    ref_index=ref, tgt_index=tgt, workers=2)
            except Exception as exc:                # noqa: BLE001
                edges[gname][f"{ref + 1}-{tgt + 1}"] = {
                    "error": f"{type(exc).__name__}: {exc}"}
                continue
            rec = {"usable": int(est.usable_frames),
                   "delay_ms": round(float(est.delay_ms), 3),
                   "confidence": round(float(est.confidence), 3)}
            if est.frames:
                stats = se.summarize_trajectory(est.frames, SR)
                disp = se.measured_displacement_ms(est.frames, SR)
                rec["mad_ms"] = (round(float(stats.mad_ms), 3)
                                 if stats.mad_ms == stats.mad_ms else None)
                rec["displacement_ms"] = round(float(disp), 3)
                rec["constant"] = bool(se.classify_constant(
                    stats, mad_max_ms=eff["mad_max_ms"],
                    max_ppm=eff["constant_max_ppm"], sample_rate=SR,
                    drift_min_ms=eff["drift_min_ms"], displacement_ms=disp))
            edges[gname][f"{ref + 1}-{tgt + 1}"] = rec
    # 建组度量（生产函数）
    snr = {}
    try:
        for i, j in ((0, 1), (2, 3), (0, 2), (1, 3)):
            s = se.sample_pair_snr(
                raw[i], raw[j], sample_rate=SR, frame_ms=eff["frame_ms"],
                hop_ms=eff["hop_ms"],
                search_window_ms=eff["search_window_ms"],
                coarse_rate=eff["coarse_rate"],
                frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                samples=eff["group_snr_samples"])
            snr[f"{i + 1}-{j + 1}"] = s
    except Exception as exc:                        # noqa: BLE001
        snr = {"error": f"{type(exc).__name__}: {exc}"}
    # 连通性（生产锚点 CH3 为根）
    conn = {}
    for gname, pe in edges.items():
        adj = {i: set() for i in range(1, 5)}
        for key, rec in pe.items():
            if "error" in rec:
                continue
            a, b = key.split("-")
            if rec["usable"] >= eff["min_usable_frames"]:
                adj[int(a)].add(int(b))
                adj[int(b)].add(int(a))
        seen, stack = {3}, [3]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        conn[gname] = {"reachable": sorted(seen),
                       "unreachable": sorted(set(range(1, 5)) - seen),
                       "all_reachable": len(seen) == 4}
    # 30 s 窗证据密度（用生产窗几何 API 计算窗数/映射）
    win: dict = {}
    try:
        est = se.estimate_pair(
            str(raw[2]), str(raw[0]), sample_rate=SR,
            search_window_ms=eff["search_window_ms"],
            frame_ms=1000.0, hop_ms=500.0,
            anchor_segment_seconds=eff["anchor_segment_seconds"],
            min_confidence=eff["min_confidence"],
            coarse_rate=eff["coarse_rate"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
            min_usable_frames=eff["min_usable_frames"],
            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
            ref_index=2, tgt_index=0, workers=2)
        if est.frames:
            c = np.array([f.center_sample for f in est.frames], dtype=np.int64)
            dly = np.array([f.delay_samples for f in est.frames])
            good = ~np.isnan(dly)
            ws, hs = int(30 * SR), int(10 * SR)
            n_need = tv.required_window_count(n, hop_samples=hs)
            n_meas = tv.measured_window_count(n, window_samples=ws,
                                              hop_samples=hs)
            idx = tv.window_index("nearest-centre", c, window_samples=ws,
                                  hop_samples=hs, n_windows=n_need)
            counts = np.bincount(idx[good], minlength=n_need)
            win = {"windows_needed": int(n_need),
                   "windows_measured": int(n_meas),
                   "resolved": int((counts >= 3).sum()),
                   "resolved_fraction": float((counts >= 3).sum()
                                              / max(1, n_need)),
                   "note": "CH3→CH1, 1 s 帧, 30/10 s 粗估; "
                           "详细逐窗见 long_take_probe.py"}
    except Exception as exc:                        # noqa: BLE001
        win = {"error": f"{type(exc).__name__}: {exc}"}
    shutil.rmtree(scratch / path.stem, ignore_errors=True)
    return {"duration_s": round(n / SR, 2), "edges": edges,
            "connectivity": conn, "group_snr": snr,
            "window_estimate": win}


# ---------------------------------------------------------------------------
# S3 编码格式深挖（视频/容器/码流结构）
# ---------------------------------------------------------------------------
def _ffprobe_packets(path: Path, window_s: float) -> list[tuple[float, str, int]]:
    """取前 window_s 秒的视频包: (pts_time, flags, size)。用 -read_intervals 限流。"""
    cmd = [str(FFPROBE), "-v", "error", "-select_streams", "v:0",
           "-read_intervals", f"%+{window_s:.3f}",
           "-show_entries", "packet=pts_time,flags,size",
           "-of", "csv=p=0", str(path)]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    import re as _re
    out = []
    num_re = _re.compile(r"^-?\d+(?:\.\d+)?$")
    flag_re = _re.compile(r"^[A-Za-z_]+$")
    for line in (r.stdout or "").splitlines():
        parts = [x.strip() for x in line.split(",") if x.strip()]
        nums = [x for x in parts if num_re.match(x)]
        flags = [x for x in parts if flag_re.match(x)]
        if len(nums) < 2:
            continue
        try:
            ts = float(nums[0])
            size = int(float(nums[1]))
        except ValueError:
            continue
        out.append((ts, flags[0] if flags else "_", size))
    return out


def stage_video(path: Path, window_s: float = 120.0) -> dict:
    """编码格式深挖: 生产 probe 的解析字段 + 原始流字段 + 标签 + 码流结构。"""
    try:
        summary, streams = probe_source(FFPROBE, path)
    except Exception as exc:                        # noqa: BLE001
        return {"error": f"probe_source: {type(exc).__name__}: {exc}"}
    doc = ar.probe(path)
    raw_video = next((s for s in (doc.get("streams") or [])
                      if s.get("codec_type") == "video"), {})
    facts = ar.container_facts(path)
    fps = float(summary.get("fps") or 0.0)
    video_bps = float(summary.get("video_bitrate_bps") or 0.0)
    w = int(summary.get("width") or 0)
    h = int(summary.get("height") or 0)
    packets = _ffprobe_packets(path, window_s)
    key_ts = [ts for ts, flags, _ in packets if "K" in flags]
    intervals = [round(key_ts[i + 1] - key_ts[i], 3)
                 for i in range(len(key_ts) - 1)]
    # 逐秒码率（窗口内）
    per_sec: dict[int, int] = {}
    for ts, _flags, size in packets:
        per_sec[int(ts)] = per_sec.get(int(ts), 0) + size
    sec_bps = [v * 8 for v in per_sec.values()]
    sizes = [sz for _ts, _f, sz in packets]
    return {
        "duration_s": summary.get("duration"),
        "codec": summary.get("codec"),
        "codec_long_name": summary.get("codec_long_name"),
        "profile": summary.get("profile"),
        "codec_tag_string": summary.get("codec_tag_string"),
        "codec_tag": summary.get("codec_tag"),
        "width": w, "height": h, "resolution": summary.get("resolution"),
        "pix_fmt": summary.get("pix_fmt"),
        "bits_per_raw_sample": summary.get("bits_per_raw_sample"),
        "field_order": summary.get("field_order"),
        "fps": fps,
        "r_frame_rate": summary.get("r_frame_rate"),
        "avg_frame_rate": summary.get("avg_frame_rate"),
        "time_base": summary.get("time_base"),
        "video_bitrate_mbps": summary.get("video_bitrate_mbps"),
        "container_bitrate_mbps": summary.get("container_bitrate_mbps"),
        "color_primaries": summary.get("color_primaries"),
        "color_transfer": summary.get("color_transfer"),
        "color_space": summary.get("color_space"),
        "color_range": summary.get("color_range"),
        "chroma_location": raw_video.get("chroma_location"),
        "sample_aspect_ratio": raw_video.get("sample_aspect_ratio"),
        "display_aspect_ratio": raw_video.get("display_aspect_ratio"),
        "nb_frames": raw_video.get("nb_frames"),
        "has_b_frames": raw_video.get("has_b_frames"),
        "refs": raw_video.get("refs"),
        "level": raw_video.get("level"),
        "side_data": raw_video.get("side_data_list") or [],
        "format_tags": (doc.get("format") or {}).get("tags") or {},
        "stream_tags": raw_video.get("tags") or {},
        "brands": {"ftyp_major": facts.get("ftyp_major"),
                   "top_level": facts.get("top_level"),
                   "moov_first": facts.get("moov_first")},
        "audio_streams": [{"index": s.get("index"),
                           "codec": s.get("codec_name"),
                           "profile": s.get("profile"),
                           "channels": s.get("channels"),
                           "sample_rate": s.get("sample_rate"),
                           "bit_rate": s.get("bit_rate"),
                           "tag": s.get("codec_tag_string"),
                           "sample_fmt": s.get("sample_fmt"),
                           "bits": s.get("bits_per_raw_sample")
                           or s.get("bits_per_sample"),
                           "tags": s.get("tags") or {}}
                          for s in (doc.get("streams") or [])
                          if s.get("codec_type") == "audio"],
        # 全部流的原始关键字段（跨机位对比用；data 轨/时间码轨常在这里暴露）
        "all_streams": [{"index": s.get("index"),
                         "type": s.get("codec_type"),
                         "codec": s.get("codec_name"),
                         "profile": s.get("profile"),
                         "tag": s.get("codec_tag_string"),
                         "pix_fmt": s.get("pix_fmt"),
                         "width": s.get("width"),
                         "height": s.get("height"),
                         "field_order": s.get("field_order"),
                         "r_frame_rate": s.get("r_frame_rate"),
                         "time_base": s.get("time_base"),
                         "nb_frames": s.get("nb_frames"),
                         "bit_rate": s.get("bit_rate"),
                         "channels": s.get("channels"),
                         "sample_rate": s.get("sample_rate"),
                         "handler_name": (s.get("tags") or {}).get(
                             "handler_name"),
                         "vendor_id": (s.get("tags") or {}).get("vendor_id"),
                         "encoder_tag": (s.get("tags") or {}).get("encoder"),
                         "disposition": s.get("disposition")}
                        for s in (doc.get("streams") or [])],
        "format_tags_all": (doc.get("format") or {}).get("tags") or {},
        # 派生量: bits per pixel per frame —— 跨机位比较编码效率的归一量
        "bits_per_pixel": (round(video_bps / (w * h * fps), 4)
                           if w and h and fps and video_bps else None),
        "packets": {
            "window_s": window_s,
            "n_packets": len(packets),
            "n_keyframes": len(key_ts),
            "keyint_s_mean": (round(sum(intervals) / len(intervals), 3)
                              if intervals else None),
            "keyint_s_min": min(intervals) if intervals else None,
            "keyint_s_max": max(intervals) if intervals else None,
            "keyint_frames_mean": (round(sum(intervals) / len(intervals) * fps)
                                   if intervals and fps else None),
            "packet_size_mean": (round(sum(sizes) / len(sizes), 1)
                                 if sizes else None),
            "per_sec_mbps_p10": (round(float(np.percentile(sec_bps, 10)) / 1e6, 2)
                                 if sec_bps else None),
            "per_sec_mbps_p50": (round(float(np.percentile(sec_bps, 50)) / 1e6, 2)
                                 if sec_bps else None),
            "per_sec_mbps_p90": (round(float(np.percentile(sec_bps, 90)) / 1e6, 2)
                                 if sec_bps else None),
            "per_sec_mbps_max": (round(float(max(sec_bps)) / 1e6, 2)
                                 if sec_bps else None),
        },
    }


def _run(cmd: list[str], timeout: float = 1800.0) -> tuple[int, str]:
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.returncode, (r.stderr or "") + (r.stdout or "")


def _filter_metric(args: list[str], filt: str) -> dict | None:
    """跑一次 ffmpeg 滤镜比较, 解析 SSIM/PSNR 的 All: 数值。"""
    rc, out = _run(args)
    if rc != 0:
        return None
    for line in out.splitlines():
        if filt == "ssim" and "All:" in line:
            try:
                return {"ssim": float(line.split("All:")[1].split()[0])}
            except (IndexError, ValueError):
                pass
        if filt == "psnr" and "average:" in line:
            try:
                return {"psnr_db": float(line.split("average:")[1].split()[0])}
            except (IndexError, ValueError):
                pass
    return None


def stage_container(path: Path, video: dict, duration: float,
                    scratch: Path, seconds: float) -> dict:
    """容器格式复现验证（**不编码**: 全程 -c copy）。

    做两件事:

      1. 把源前 N 秒 stream copy 成新 mp4（一次带 faststart, 一次不带），
      2. 逐项比对: 顶层 box 结构 / ftyp brand / 每条流的 codec、profile、pix_fmt、
         分辨率、帧率、time_base、tag、色彩字段、音频采样率/声道/位深/采样条目,
         以及流数量与流顺序。

    结论按"可复现项 / 不可复现项"分开列，不合并成一个模糊的 PASS。
    """
    d = scratch / f"container_{path.stem}"
    d.mkdir(parents=True, exist_ok=True)
    D = max(1.0, min(seconds, float(duration or seconds)))
    src_doc = ar.probe(path)
    src_facts = ar.container_facts(path)
    src_streams = src_doc.get("streams") or []
    out: dict = {"excerpt_s": D, "source_boxes": src_facts.get("top_level"),
                 "source_brand": src_facts.get("ftyp_major"),
                 "source_streams": len(src_streams),
                 "variants": {}}

    def stream_signature(streams: list[dict]) -> list[dict]:
        sig = []
        for s in streams:
            sig.append({
                "index": s.get("index"),
                "type": s.get("codec_type"),
                "codec": s.get("codec_name"),
                "profile": s.get("profile"),
                "tag": s.get("codec_tag_string"),
                "pix_fmt": s.get("pix_fmt"),
                "width": s.get("width"),
                "height": s.get("height"),
                "r_frame_rate": s.get("r_frame_rate"),
                "avg_frame_rate": s.get("avg_frame_rate"),
                "time_base": s.get("time_base"),
                "channels": s.get("channels"),
                "sample_rate": s.get("sample_rate"),
                "sample_fmt": s.get("sample_fmt"),
                "bits_per_sample": s.get("bits_per_raw_sample")
                or s.get("bits_per_sample"),
                "color_range": s.get("color_range"),
                "color_space": s.get("color_space"),
                "color_transfer": s.get("color_transfer"),
                "color_primaries": s.get("color_primaries"),
                "chroma_location": s.get("chroma_location"),
                "field_order": s.get("field_order"),
                "bit_rate": s.get("bit_rate"),
            })
        return sig

    src_sig = stream_signature(src_streams)
    # 映射策略: 先按"全部流"忠实重封; 失败则退到视频+音频, 并记录被放弃的流。
    # mp4 muxer 不接受相机私有 data 轨 (实测 Sony meta: "Could not find tag for
    # codec none"), 这正是"容器格式能否完整复现"要回答的问题。
    maps = [("all", ["-map", "0"]), ("av", ["-map", "0:v", "-map", "0:a"])]
    for label, flags in (("plain", []),
                         ("faststart", ["-movflags", "+faststart"])):
        dest = d / f"remux_{label}.mp4"
        rc, err, used_map = -1, "", None
        for mname, margs in maps:
            cmd = ([str(FFMPEG), "-v", "error", "-nostdin", "-y",
                    "-ss", "0", "-t", f"{D:.3f}", "-i", str(path)]
                   + margs + ["-c", "copy"] + flags + [str(dest)])
            try:
                rc, err = _run(cmd)
            except subprocess.TimeoutExpired:
                rc, err = -1, "timeout"
            if rc == 0 and dest.is_file() and dest.stat().st_size > 0:
                used_map = mname
                break
        if used_map is None:
            out["variants"][label] = {
                "ok": False, "rc": rc, "stderr": err[-300:],
                "note": "全部映射策略均失败",
            }
            continue
        rep_doc = ar.probe(dest)
        rep_facts = ar.container_facts(dest)
        rep_streams = rep_doc.get("streams") or []
        rep_sig = stream_signature(rep_streams)
        diffs = []
        for i in range(max(len(src_sig), len(rep_sig))):
            a = src_sig[i] if i < len(src_sig) else None
            b = rep_sig[i] if i < len(rep_sig) else None
            if a is None or b is None:
                diffs.append({"stream": i, "missing": "src" if a is None
                              else "out"})
                continue
            for k in a:
                if k == "bit_rate":
                    continue          # 段长/封装差异会让平均码率变化, 单列
                if a[k] != b[k]:
                    diffs.append({"stream": i, "field": k,
                                  "source": a[k], "output": b[k]})
        dropped = []
        if used_map == "av":
            dropped = [{"index": s.get("index"),
                        "type": s.get("codec_type"),
                        "codec": s.get("codec_name")}
                       for s in src_streams
                       if s.get("codec_type") not in ("video", "audio")]
        out["variants"][label] = {
            "ok": True,
            "map": used_map,
            "dropped_streams": dropped,
            "faithful": used_map == "all",
            "size_bytes": dest.stat().st_size,
            "boxes": rep_facts.get("top_level"),
            "brand": rep_facts.get("ftyp_major"),
            "moov_first": bool(rep_facts.get("moov_first")),
            "streams": len(rep_streams),
            "stream_diffs": diffs,
            "streams_match": not diffs,
            "box_differs": rep_facts.get("top_level") != src_facts.get("top_level"),
            "brand_differs": rep_facts.get("ftyp_major") != src_facts.get("ftyp_major"),
        }
    import shutil as _sh
    _sh.rmtree(d, ignore_errors=True)     # scratch 在 E:, 允许删除
    return out


# ---------------------------------------------------------------------------
# S4 报告
# ---------------------------------------------------------------------------
def _items(rec: dict) -> list[dict]:
    return (rec.get("checks") or {}).get("items") or []


def write_report(out: Path, files: list[dict], budget: Budget,
                 pending: list[str]) -> None:
    lines: list[str] = []
    a = lines.append
    a("# 素材调研报告\n")
    a(f"- 生成: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    a(f"- 用时: {budget.elapsed / 60:.1f} 分钟"
      + (f" (预算 {budget.limit / 60:.0f} 分钟)" if budget.limit else ""))
    a(f"- 文件: {len(files)}")
    a("")
    a("检查项来自 `archive_repair.summarise_check`（CTR-01..08 / FMT-01..05 / "
      "AUD-01..04），音频裁决直接调用生产 `run_channel_sync`。")
    a("")
    # 1 总览
    a("## 1 总览\n")
    a("| 机位 | 文件 | 总时长 h | 总大小 GiB | 有音频 | 4-mono | PASS | FAIL |")
    a("|---|---|---|---|---|---|---|---|")
    groups: dict[str, list] = {}
    for f in files:
        groups.setdefault(f.get("group", "?"), []).append(f)
    for g in sorted(groups):
        gs = groups[g]
        dur = sum((x.get("checks", {}).get("duration_s") or 0)
                  for x in gs) / 3600.0
        size = sum((x.get("checks", {}).get("size_bytes") or 0)
                   for x in gs) / 2**30
        na = sum(1 for x in gs if (x.get("checks", {}).get("n_audio") or 0) > 0)
        el = sum(1 for x in gs if x.get("checks", {}).get("eligible_4mono"))
        npass = sum(1 for x in gs if x.get("checks", {}).get("verdict") == "PASS")
        nfail = sum(1 for x in gs if x.get("checks", {}).get("verdict") == "FAIL")
        a(f"| {g} | {len(gs)} | {dur:.2f} | {size:.1f} | {na} | {el} | "
          f"{npass} | {nfail} |")
    a("")
    # 2 检查码统计
    code_stats: dict[str, dict[str, int]] = {}
    for f in files:
        for it in _items(f):
            cid = it.get("id") or it.get("check")
            verdict = it.get("verdict") or it.get("status")
            if not cid:
                continue
            d = code_stats.setdefault(cid, {})
            d[verdict] = d.get(verdict, 0) + 1
    a("## 2 检查码统计（所有文件汇总）\n")
    a("| 检查 | PASS | WARN | FAIL | SKIP |")
    a("|---|---|---|---|---|")
    for cid in sorted(code_stats):
        d = code_stats[cid]
        a(f"| `{cid}` | {d.get('PASS', 0)} | {d.get('WARN', 0)} | "
          f"{d.get('FAIL', 0)} | {d.get('SKIP', 0)} |")
    a("")
    # 3 逐文件
    a("## 3 逐文件\n")
    a("| 机位 | 文件 | 时长 s | GiB | 音频 | 资格 | 判定 | 失败/警告项 |")
    a("|---|---|---|---|---|---|---|---|")
    for f in files:
        c = f.get("checks") or {}
        if "error" in c:
            a(f"| {f.get('group')} | {f.get('name')} | — | — | — | — | "
              f"**探测失败** | {c['error'][:50]} |")
            continue
        bad = [f"{it.get('id')}" for it in _items(f)
               if (it.get("verdict") or it.get("status")) in ("FAIL", "WARN")]
        a(f"| {f.get('group')} | {f.get('name')} | "
          f"{c.get('duration_s') or 0:.1f} | "
          f"{(c.get('size_bytes') or 0) / 2**30:.2f} | {c.get('n_audio')} | "
          f"{'4-mono' if c.get('eligible_4mono') else (c.get('eligible_reason') or '')[:28]} | "
          f"{c.get('verdict')} | {','.join(bad) or '—'} |")
    a("")
    # 4 电平
    a("## 4 电平/静音 profile（60 s 块）\n")
    a("| 文件 | 流 | 秒 | 整轨 RMS | p10/p50/p90 | 静音块 | 最长静音 s |")
    a("|---|---|---|---|---|---|---|")
    for f in files:
        for ch in (f.get("profile") or {}).get("channels") or []:
            if "error" in ch or "skipped" in ch:
                a(f"| {f.get('name')} | {ch.get('stream')} | — | — | — | — | "
                  f"{ch.get('error') or 'budget'} |")
                continue
            a(f"| {f.get('name')} | {ch['stream']} | {ch['seconds']:.0f} | "
              f"{ch.get('rms_db_whole')} | {ch['rms_db_p10']:.1f}/"
              f"{ch['rms_db_p50']:.1f}/{ch['rms_db_p90']:.1f} | "
              f"{ch['silent_blocks']}/{ch['blocks']} | "
              f"{ch['longest_silent_run_s']:.0f} |")
    a("")
    # 5 立体声
    stereo = [(f.get("name"), f.get("stereo")) for f in files if f.get("stereo")]
    if stereo:
        a("## 4b 立体声 L/R 相位（仅信息）\n")
        a("| 文件 | 秒 | L/R NCC 中位 | lag 中位 ms | lag 离散 采样 | 备注 |")
        a("|---|---|---|---|---|---|")
        for name, s in stereo:
            a(f"| {name} | {s.get('seconds')} | {s.get('lr_ncc_median')} | "
              f"{s.get('lr_lag_median_ms')} | "
              f"{s.get('lr_lag_spread_samples')} | "
              f"{s.get('note') or s.get('error') or ''} |")
        a("")
    # 6 4-mono 深度
    eligible = [f for f in files
                if (f.get("checks") or {}).get("eligible_4mono")]
    if eligible:
        a("## 6 4-mono 文件深度分析\n")
        for f in eligible:
            c = f["checks"]
            a(f"### {f.get('name')}  ({c.get('duration_s') or 0:.1f}s)\n")
            ad = c.get("audio_data") or {}
            a(f"- 生产裁决: status=`{ad.get('status')}` detail=`{ad.get('detail')}`")
            for ch in ad.get("rows") or ad.get("channels") or []:
                a(f"  - CH{(ch.get('stream') or 0) + 1}: {ch.get('decision')} "
                  f"reason={ch.get('reason')} shift={ch.get('shift_samples')}")
            p = f.get("pairs") or {}
            if p:
                for g, v in (p.get("connectivity") or {}).items():
                    a(f"- 连通性 ({g}): 可达 {v['reachable']} / "
                      f"不可达 {v['unreachable']} -> "
                      f"{'全通' if v['all_reachable'] else '**不可全通**'}")
                w = p.get("window_estimate") or {}
                if "resolved_fraction" in (w or {}):
                    a(f"- 30 s/10 s 解析率（粗估）: "
                      f"{100 * w['resolved_fraction']:.0f}% "
                      f"({w['resolved']}/{w['windows_needed']})")
                gs = p.get("group_snr") or {}
                if gs and "error" not in gs:
                    a("- 建组 SNR: " + ", ".join(
                        f"CH{k}: {v.get('median', float('nan')):.2f}"
                        for k, v in gs.items()))
            a("")
    # 5 编码格式逐文件
    vid_files = [f for f in files if (f.get("video") or {}).get("codec")]
    if vid_files:
        a("## 5 编码格式（逐文件，视频）\n")
        a("| 文件 | codec | profile | pix_fmt | 分辨率 | fps | Mbps | bpp | "
          "色彩 | keyint s/帧 | 每秒 Mbps p10/p50/p90 |")
        a("|---|---|---|---|---|---|---|---|---|---|---|")
        for f in vid_files:
            v = f["video"]
            pk = v.get("packets") or {}
            a(f"| {f.get('name')} | {v.get('codec')} | {v.get('profile')} | "
              f"{v.get('pix_fmt')} | {v.get('resolution')} | "
              f"{v.get('fps')} | {v.get('video_bitrate_mbps')} | "
              f"{v.get('bits_per_pixel')} | "
              f"{v.get('color_primaries')}/{v.get('color_space')}/"
              f"{v.get('color_range')} | "
              f"{pk.get('keyint_s_mean')}/{pk.get('keyint_frames_mean')} | "
              f"{pk.get('per_sec_mbps_p10')}/{pk.get('per_sec_mbps_p50')}/"
              f"{pk.get('per_sec_mbps_p90')} |")
        a("")
        a("**容器标签 / side data**\n")
        for f in vid_files:
            v = f["video"]
            tags = {k: v2 for k, v2 in (v.get("format_tags") or {}).items()
                    if k in ("encoder", "creation_time", "major_brand",
                             "minor_version", "compatible_brands",
                             "make", "model", "software")}
            if tags or v.get("side_data"):
                a(f"- `{f.get('name')}`: tags={tags} "
                  f"side_data={[sd.get('side_data_type') if isinstance(sd, dict) else sd for sd in (v.get('side_data') or [])]}")
        a("")
        # 6 逐机位汇总
        a("## 6 跨机位流格式对比\n")
        a("| 机位 | 组合 (codec/profile/pix_fmt/色彩) | 文件数 | Mbps 范围 | "
          "fps | keyint 帧 | bpp 范围 | 音频格式(条目) | data/时间码轨 | "
          "ftyp brand | HDR side data |")
        a("|---|---|---|---|---|---|---|---|---|---|---|")
        agg: dict[str, dict] = {}
        for f in vid_files:
            v = f["video"]
            key = (v.get("codec"), v.get("profile"), v.get("pix_fmt"),
                   f"{v.get('color_primaries')}/{v.get('color_space')}")
            g = agg.setdefault(f.get("group") or "?", {})
            g.setdefault(key, []).append(v)
        for g in sorted(agg):
            for key, vs in agg[g].items():
                mbps = [x.get("video_bitrate_mbps") or 0 for x in vs]
                bpp = [x.get("bits_per_pixel") or 0 for x in vs]
                fps = sorted({x.get("fps") for x in vs})
                ki = [x.get("packets", {}).get("keyint_frames_mean") or 0
                      for x in vs]
                # 音频格式与 sample entry: 用同机位第一个文件做代表
                rep_v = vs[0]
                aud = rep_v.get("audio_streams") or []
                aud_desc = "; ".join(
                    f"{x.get('codec')}/{x.get('channels')}ch/"
                    f"{x.get('sample_rate')}/{x.get('bits')}bit/"
                    f"{x.get('tag')}" for x in aud) or "无"
                streams = rep_v.get("all_streams") or []
                dat = [x for x in streams
                       if x.get("type") in ("data", "subtitle")]
                dat_desc = "; ".join(
                    f"{x.get('type')}:{x.get('codec') or '-'}"
                    f"({x.get('handler_name') or x.get('tag') or ''})"
                    for x in dat) or "无"
                brands = rep_v.get("brands") or {}
                sds = [sd.get("side_data_type") if isinstance(sd, dict) else sd
                       for sd in (rep_v.get("side_data") or [])]
                a(f"| {g} | {'/'.join(str(k) for k in key)} | {len(vs)} | "
                  f"{min(mbps):.1f}–{max(mbps):.1f} | "
                  f"{','.join(str(x) for x in fps)} | "
                  f"{min(ki)}–{max(ki)} | "
                  f"{min(bpp):.4f}–{max(bpp):.4f} | {aud_desc} | {dat_desc} | "
                  f"{brands.get('ftyp_major')} | "
                  f"{','.join(str(x) for x in sds) or '—'} |")
        a("")
        # 7 容器格式复现验证（不编码）
        rep_files = [f for f in vid_files if f.get("container")]
        if rep_files:
            a("## 8 容器格式复现验证（全程 -c copy，不编码）\n")
            a("口径: 取源前 N 秒 stream copy 重封为 mp4，再与源**逐项**比对："
              "流数量/顺序、每条流的 codec/profile/tag/pix_fmt/分辨率/帧率/"
              "time_base/声道/采样率/位深/色彩字段，以及顶层 box 结构与 ftyp "
              "brand。按用户要求**未执行任何编码器**——这里只回答"
              "“容器与流参数能否被完整复现”。\n")
            a("| 文件 | 段长 s | 重封 | 映射 | 忠实(全部流) | 丢弃流 | 流字段一致 | "
              "差异字段 | box 结构 | brand | faststart 变体 |")
            a("|---|---|---|---|---|---|---|---|---|---|---|")
            n_match = n_any = 0
            common_diffs: dict[str, int] = {}
            for f in rep_files:
                c = f["container"]
                v = (c.get("variants") or {}).get("plain") or {}
                vf = (c.get("variants") or {}).get("faststart") or {}
                n_any += 1
                if v.get("streams_match"):
                    n_match += 1
                ds = v.get("stream_diffs") or []
                for dd in ds:
                    key = (f"流{dd.get('stream')}:{dd.get('field')}"
                           if dd.get("field") else f"流{dd.get('stream')}:missing")
                    common_diffs[key] = common_diffs.get(key, 0) + 1
                diff_desc = ",".join(sorted({(dd.get("field")
                                              or "missing") for dd in ds})) or "—"
                dropped = ",".join(str(x.get('type')) for x in
                                     (v.get('dropped_streams') or [])) or "—"
                a(f"| {f.get('name')} | {c.get('excerpt_s')} | "
                  f"{'OK' if v.get('ok') else 'FAIL'} | {v.get('map') or '—'} | "
                  f"{'是' if v.get('faithful') else '否'} | {dropped} | "
                  f"{'是' if v.get('streams_match') else '否'} | {diff_desc} | "
                  f"{'一致' if v.get('ok') and not v.get('box_differs') else '不同'} | "
                  f"{'一致' if v.get('ok') and not v.get('brand_differs') else '不同'} | "
                  f"{'OK' if vf.get('ok') else 'FAIL'} |")
            a("")
            a(f"流字段完全一致: **{n_match}/{n_any}**")
            if common_diffs:
                a("")
                a("最常见的不可复现字段（stream:field → 出现次数）: " + ", ".join(
                    f"{k}×{v}" for k, v in sorted(common_diffs.items(),
                                                  key=lambda kv: -kv[1])[:12]))
            a("")
            a("已知的**结构性不可复现项**（ffmpeg muxer 决定，不是缺陷）: "
              "`ftyp` major_brand（相机写 XAVC/mp42 等，ffmpeg 写 isom/mp41）、"
              "厂商私有 uuid/meta 轨的布局、moov 位置（相机通常 moov 在后；"
              "加了 `-movflags +faststart` 才能前移）。这些在表里以 `brand 不同`"
              "/`box 结构不同` 体现。")
            a("")
    # 8 需要处理
    a("## 9 需要处理的文件\n")
    needs = []
    for f in files:
        c = f.get("checks") or {}
        if "error" in c:
            needs.append((f.get("name"), "探测失败"))
            continue
        fails = [it.get("id") for it in _items(f)
                 if (it.get("verdict") or it.get("status")) == "FAIL"]
        if fails:
            needs.append((f.get("name"), "FAIL: " + ",".join(str(x) for x in fails)))
        elif c.get("verdict") == "WARN":
            needs.append((f.get("name"), "WARN"))
    if needs:
        a("| 文件 | 原因 |")
        a("|---|---|")
        for n, why in needs:
            a(f"| {n} | {why} |")
    else:
        a("无")
    a("")
    if pending:
        a(f"**预算内未完成: {len(pending)} 个阶段**；重跑同一命令即可续跑。")
    out.joinpath("report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="素材文件夹调研 (CPU only, 可续跑)")
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-minutes", type=float, default=180.0)
    ap.add_argument("--ext", default=".MP4")
    ap.add_argument("--only", nargs="*", default=[],
                    help="只跑这些阶段: checks profile stereo pairs")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--video-window", type=float, default=120.0,
                    help="码流结构分析窗口 (秒, 默认 120)")
    ap.add_argument("--container-seconds", type=float, default=5.0,
                    help="容器复现验证的段长 (秒, 默认 5; 全程 -c copy 不编码)")
    ap.add_argument("--container-max-files", type=int, default=0,
                    help="最多对多少个文件做容器复现验证 (0 = 全部)")
    args = ap.parse_args()

    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS"):
        os.environ[v] = str(max(1, args.threads))

    root = Path(args.root)
    out = Path(args.out)
    # 盘策略: E:/I: 可完全控制; F: 只允许项目文件夹内, 且本程序不删除 F: 上任何东西
    _drive = str(out.drive).upper()
    _proj = Path(r"F:\1KeyTranscoder").resolve()
    try:
        _inside_proj = _proj in out.resolve().parents or out.resolve() == _proj
    except OSError:
        _inside_proj = False
    if _drive == "F:" and not _inside_proj:
        raise SystemExit(
            f"输出目录 {out} 在 F: 盘项目文件夹之外。F: 只允许项目文件夹内的写入; "
            f"请用 E:/I: 或 F:\\1KeyTranscoder 下的路径。")
    out.mkdir(parents=True, exist_ok=True)
    (out / "files").mkdir(parents=True, exist_ok=True)
    scratch = SCRATCH / f"run_{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.budget_minutes)
    stages = (set(args.only) if args.only
              else {"checks", "video", "profile", "stereo", "container",
                    "pairs"})
    pattern = args.ext
    files = ar.iter_media(root, pattern)
    log(f"root={root} 文件 {len(files)}; 阶段 {sorted(stages)}; "
        f"预算 {args.budget_minutes} 分钟")

    records: list[dict] = []
    pending: list[str] = []
    n_containers = 0
    for n, path in enumerate(files, 1):
        ck = out / "files" / f"{path.stem}.json"
        rec = {} if args.fresh else (
            json.loads(ck.read_text(encoding="utf-8")) if ck.is_file() else {})
        rec.setdefault("name", path.name)
        rec.setdefault("group", path.parent.name)
        rec["path"] = str(path)
        log(f"[{n}/{len(files)}] {path.parent.name}/{path.name}")
        if budget.done():
            pending.append(f"all__{path.stem}")
            records.append(rec)
            continue
        if "checks" in stages and "checks" not in rec:
            rec["checks"] = stage_checks(path)
            atomic_json(ck, rec)
            c = rec["checks"]
            if "error" in c:
                log(f"    探测失败: {c['error']}")
                records.append(rec)
                continue
            log(f"    {c.get('duration_s') or 0:.1f}s audio={c.get('n_audio')} "
                f"verdict={c.get('verdict')} "
                f"eligible_4mono={c.get('eligible_4mono')}")
        c = rec.get("checks") or {}
        if "error" in c:
            records.append(rec)
            continue
        if ("profile" in stages and "profile" not in rec
                and (c.get("n_audio") or 0) > 0):
            rec["profile"] = stage_profile(path, c["n_audio"], scratch, budget)
            atomic_json(ck, rec)
            ch = (rec["profile"].get("channels") or [{}])[0]
            if "rms_db_p50" in ch:
                log(f"    电平 p50={ch['rms_db_p50']:.1f} dBFS "
                    f"静音 {100 * ch['silent_fraction']:.0f}%")
        if "video" in stages and "video" not in rec:
            rec["video"] = stage_video(path, args.video_window)
            atomic_json(ck, rec)
            v = rec["video"]
            if "error" in v:
                log(f"    编码格式探测失败: {v['error']}")
            else:
                pk = v.get("packets") or {}
                log(f"    {v.get('codec')}/{v.get('profile')} "
                    f"{v.get('pix_fmt')} {v.get('resolution')}@"
                    f"{v.get('fps')} {v.get('video_bitrate_mbps')}Mbps "
                    f"keyint={pk.get('keyint_frames_mean')}f")
        if ("stereo" in stages and "stereo" not in rec
                and (c.get("n_audio") or 0) > 0):
            st = stage_stereo(path, c.get("audio_layout") or [], scratch, budget)
            if st is not None:
                rec["stereo"] = st
                atomic_json(ck, rec)
        if ("container" in stages and "container" not in rec
                and (rec.get("video") or {}).get("codec")
                and (args.container_max_files <= 0
                     or n_containers < args.container_max_files)):
            budget_left = (budget.limit - budget.elapsed) if budget.limit else None
            if budget_left is not None and budget_left < 30:
                pending.append(f"container__{path.stem}")
            else:
                rec["container"] = stage_container(
                    path, rec["video"], c.get("duration_s") or 0.0, scratch,
                    args.container_seconds)
                atomic_json(ck, rec)
                n_containers += 1
                cv = (rec["container"].get("variants") or {}).get("plain") or {}
                log(f"    容器复现: 流字段"
                    f"{'一致' if cv.get('streams_match') else '有差异'} "
                    f"boxes{'一致' if not cv.get('box_differs') else '不同'} "
                    f"brand{'一致' if not cv.get('brand_differs') else '不同'}")
        if c.get("eligible_4mono") and "pairs" in stages and "pairs" not in rec:
            rec["pairs"] = stage_pairs(path, scratch)
            atomic_json(ck, rec)
            for g, v in ((rec["pairs"].get("connectivity")) or {}).items():
                log(f"    连通性 {g}: 可达 {v['reachable']}")
        records.append(rec)

    shutil.rmtree(scratch, ignore_errors=True)
    atomic_json(out / "manifest.json", {
        "root": str(root), "files": len(records),
        "elapsed_s": round(budget.elapsed, 1),
        "budget_minutes": args.budget_minutes,
        "stages": sorted(stages), "pending": pending,
        "python": sys.version.split()[0], "numpy": np.__version__,
    })
    write_report(out, records, budget, pending)
    log(f"完成: {out / 'report.md'}")
    if pending:
        log(f"有 {len(pending)} 个阶段未完成 (预算); 重跑可续")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
