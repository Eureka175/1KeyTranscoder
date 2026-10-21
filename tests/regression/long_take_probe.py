#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长镜头逐窗（时变）对齐可行性探针 —— CPU only / 离线 / 可续跑。

**这个程序不实现任何测量或计划逻辑**，它只做三件事：按顺序调用已有 API、
把结果落盘、写报告。所有测量与计划语义都来自生产模块：

    解码        core.channel_sync._decode_stream            （生产解码路径）
    逐帧测量    core.sync_estimate.estimate_pair            （含全帧轨迹 frames）
    轨迹统计    core.sync_estimate.summarize_trajectory
    位移/恒定性 core.sync_estimate.measured_displacement_ms / classify_constant
    建组度量    core.sync_estimate.sample_pair_snr
    逐窗计划    core.audio_sync_tv.build_correction_plan    （mask 由测量派生）
    窗几何      core.audio_sync_tv.window_index / window_seam /
                required_window_count / measured_window_count
    复检残差    core.sync_fix.recheck_residual_windows

用法（建议 2-3 小时预算）::

    python tests/regression/long_take_probe.py --budget-minutes 150

产出目录默认 `F:\\_long_take_probe`，全部可续跑：中断后重跑会跳过已完成的任务
（每个任务一个 checkpoint，原子写入）。输出 < 100 MB。

三阶段任务（按优先级，预算用尽即优雅停止）::

    S1 解码 + 逐帧轨迹（生产几何 × 每对 (参考,目标) × 多种几何）
    S2 逐窗可行性（多种 (窗长, 跳长)：覆盖率、缺口、接缝、逐窗修正后的残余模拟）
    S3 合成校验（注入已知常数延迟 / 已知线性漂移，给出估计器偏差与方差，
       以及逐窗管线能否复原已知漂移）—— 这一步用来证明 S2 的数字可信

关键设计口径（都写进报告）:

  * **覆盖密度**是判据：一个窗要进 plan，它必须有自己的测量；seam/尾部窗由
    policy 填充，不是测量。
  * 逐窗修正后的残余 = 窗内各帧延迟相对**该窗移位**的偏差；这是"做逐窗"能
    达到的物理极限，可直接与单移位方案对比。
  * 接缝步长 = 相邻已解析窗移位之差；步长越大，边界越可能可听。

不修改任何源文件；不写生产代码；只用 CPU。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

# --- 线程上限必须在 numpy 之前设置 -----------------------------------------
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np                                     # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import core.channel_sync as cs                          # noqa: E402
from core import audio_sync_tv as tv                    # noqa: E402
from core import sync_estimate as se                    # noqa: E402
from core import sync_fix as sf                         # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
SR = 48000
PCM_CACHE = Path(r"E:\1kt_scratch\pcm")   # 只读复用已有解码缓存
DEFAULT_CLIPS = [r"F:\1KeyTranscoder\data\align_4ch_proof\20260919_C1305.MP4",
                 r"F:\1KeyTranscoder\data\align_4ch_proof\20260919_C1306.MP4"]
DEFAULT_OUT = Path(r"F:\_long_take_probe")
PREFER_STEMS: tuple[str, ...] = ("20260919_C1305", "20260919_C1306")

#: 几何扫描: (frame_ms, hop_ms, anchor_segment_seconds)。生产默认是 (200,100,20)。
GEOMETRIES = [
    (100.0, 50.0, 20.0, "dense100"),
    (200.0, 100.0, 20.0, "prod200"),
    (250.0, 100.0, 20.0, "mid250"),
    (500.0, 250.0, 20.0, "coarse500"),
    (1000.0, 500.0, 20.0, "very-coarse1000"),
    (500.0, 100.0, 20.0, "overlap500"),
    (200.0, 100.0, 5.0, "prod200-short-anchor"),
]
PROD_GEOM = "prod200"

#: 逐窗尺度: (window_s, hop_s)。生产 tv 默认是 (30,10)。
WINDOW_SCALES = [(30.0, 10.0), (15.0, 5.0), (10.0, 5.0), (5.0, 2.0),
                 (60.0, 20.0)]
FEASIBILITY_MIN_RESOLVED = 0.60      # 计划书里的证据门

T0 = time.time()


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1,
                              default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


class Budget:
    """预算守卫: 每次任务前问一句, 超了就优雅停止并记录剩余任务。"""

    def __init__(self, minutes: float | None):
        self.limit_s = None if not minutes else minutes * 60.0

    @property
    def elapsed(self) -> float:
        return time.time() - T0

    def exhausted(self) -> bool:
        return self.limit_s is not None and self.elapsed >= self.limit_s

    def left(self) -> float | None:
        return None if self.limit_s is None else self.limit_s - self.elapsed


# ---------------------------------------------------------------------------
# 探测与解码（调用生产 API）
# ---------------------------------------------------------------------------
def probe(clip: Path) -> dict:
    """ffprobe 清单。生产侧由调用方提供 streams, 这里做同样的事。"""
    r = subprocess.run([str(FFPROBE), "-v", "error", "-show_streams",
                        "-show_format", "-of", "json", str(clip)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {clip}")
    d = json.loads(r.stdout)
    audio = [s for s in d.get("streams") or [] if s.get("codec_type") == "audio"]
    try:
        dur = float((d.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    return {"clip": str(clip), "name": clip.name, "duration_s": dur,
            "n_audio": len(audio),
            "channels": [int(s.get("channels") or 0) for s in audio],
            "sample_rate": [int(s.get("sample_rate") or 0) for s in audio],
            "codecs": [s.get("codec_name") for s in audio],
            "streams": audio}


def decode_channels(clip: Path, out_dir: Path, streams: list[dict],
                    budget: Budget) -> list[Path]:
    """4 条单声道轨 -> raw f32。命中已有缓存则直接复用（快照同 csrun）。"""
    raw_dir = out_dir / "raw" / clip.stem
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for idx, s in enumerate(streams):
        dst = raw_dir / f"ch{idx}.f32"
        cached = PCM_CACHE / clip.stem / f"ch{idx}.f32"
        if dst.is_file() and dst.stat().st_size > 0:
            paths.append(dst)
            continue
        if cached.is_file() and cached.stat().st_size > 0:
            # 只读复用: 用硬链接避免 3.4 GiB 级拷贝; 失败则退回复制（同卷）
            try:
                os.link(cached, dst)
            except OSError:
                import shutil
                shutil.copyfile(cached, dst)
            paths.append(dst)
            log(f"  复用缓存 {clip.stem} ch{idx} "
                f"({dst.stat().st_size / 2**30:.2f} GiB)")
            continue
        if budget.exhausted():
            raise SystemExit("预算用尽 (解码阶段)")
        log(f"  解码 {clip.stem} ch{idx} …")
        ok, err = cs._decode_stream(FFMPEG, clip, idx, SR, dst, "f32")
        if not ok:
            raise RuntimeError(f"解码失败 {clip} ch{idx}: {err}")
        paths.append(dst)
    return paths


# ---------------------------------------------------------------------------
# S1: 逐帧轨迹
# ---------------------------------------------------------------------------
def task_id_traj(clip: str, ref: int, tgt: int, geom: str) -> str:
    return f"traj__{clip}__r{ref}_t{tgt}__{geom}"


def run_trajectory(raw: list[Path], ref: int, tgt: int,
                   frame_ms: float, hop_ms: float, anchor_s: float,
                   workers: int) -> dict:
    """调用生产 estimate_pair, 把**全帧轨迹**（含被否帧）转成可落盘的数组。"""
    eff = cs.effective_opts(None)
    est = se.estimate_pair(
        str(raw[ref]), str(raw[tgt]),
        sample_rate=SR,
        search_window_ms=eff["search_window_ms"],
        frame_ms=frame_ms, hop_ms=hop_ms,
        anchor_segment_seconds=anchor_s,
        min_confidence=eff["min_confidence"],
        coarse_rate=eff["coarse_rate"],
        fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
        min_usable_frames=eff["min_usable_frames"],
        frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
        ref_index=ref, tgt_index=tgt,
        workers=workers,
    )
    frames = est.frames or []
    centers = np.fromiter((f.center_sample for f in frames), dtype=np.int64,
                          count=len(frames))
    delays = np.fromiter((f.delay_samples for f in frames), dtype=np.float64,
                         count=len(frames))
    confs = np.fromiter((f.confidence for f in frames), dtype=np.float64,
                        count=len(frames))
    good = ~np.isnan(delays) & (confs >= eff["min_confidence"])
    # 与生产决策链完全一致的三个调用（signature 逐个对齐 channel_sync L1496+）
    stats = (se.summarize_trajectory(frames, SR) if frames else None)
    disp_ms = (se.measured_displacement_ms(frames, SR) if frames else None)
    const = None
    if stats is not None and disp_ms is not None:
        const = bool(se.classify_constant(
            stats, mad_max_ms=eff["mad_max_ms"],
            max_ppm=eff["constant_max_ppm"], sample_rate=SR,
            drift_min_ms=eff["drift_min_ms"], displacement_ms=disp_ms))
    return {
        "ref": ref, "tgt": tgt,
        "frame_ms": float(frame_ms), "hop_ms": float(hop_ms),
        "anchor_segment_seconds": float(anchor_s),
        "usable_frames": int(est.usable_frames),
        "delay_samples": float(est.delay_samples),
        "delay_ms": float(est.delay_ms),
        "confidence": float(est.confidence),
        "fine_delay_ms": float(est.fine_delay_ms),
        "fine_fit_r2": float(est.fine_fit_r2),
        "n_frames": int(centers.size),
        "n_good": int(good.sum()),
        "centers": centers, "delays": delays, "confidences": confs,
        "summarize": None if stats is None else asdict(stats),
        "displacement_ms": None if disp_ms is None else float(disp_ms),
        "constant": const,
    }


def save_traj(path: Path, rec: dict) -> None:
    arr = {k: rec[k] for k in ("centers", "delays", "confidences")}
    meta = {k: v for k, v in rec.items() if k not in arr}
    np.savez_compressed(path, **arr)
    meta["npz"] = path.name
    atomic_json(path.with_suffix(".json"), meta)


def load_traj(path: Path) -> dict | None:
    meta = load_json(path.with_suffix(".json"))
    if meta is None or not path.is_file():
        return None
    z = np.load(path)
    meta.update({k: z[k] for k in ("centers", "delays", "confidences")})
    return meta


# ---------------------------------------------------------------------------
# S2: 逐窗可行性
# ---------------------------------------------------------------------------
def window_measurements(rec: dict, n_samples: int, window_samples: int,
                        hop_samples: int, min_frames: int) -> list[dict]:
    """把帧轨迹归到窗上（nearest-centre），每窗一条测量记录。

    记录形状即 `build_correction_plan` 要的 ``delay_samples`` + ``valid``；
    valid 的判定只用生产口径：该窗内**合格帧**数 >= min_frames。
    """
    n_win = tv.required_window_count(n_samples, hop_samples=hop_samples)
    centers = rec["centers"]
    delays = rec["delays"]
    confs = rec["confidences"]
    if centers.size == 0:
        return [{"delay_samples": float("nan"), "valid": False}
                for _ in range(max(0, tv.measured_window_count(
                    n_samples, window_samples=window_samples,
                    hop_samples=hop_samples)))]
    good = ~np.isnan(delays) & (confs >= 0.3)
    idx = tv.window_index("nearest-centre", centers,
                          window_samples=window_samples,
                          hop_samples=hop_samples, n_windows=n_win)
    n_meas = tv.measured_window_count(n_samples, window_samples=window_samples,
                                     hop_samples=hop_samples)
    out: list[dict] = []
    for w in range(n_meas):
        sel = good & (idx == w)
        d = delays[sel]
        d = d[~np.isnan(d)]
        if d.size >= min_frames:
            out.append({"delay_samples": float(np.median(d)),
                        "valid": True,
                        "n_frames": int(d.size),
                        "mad_samples": float(np.median(np.abs(d - np.median(d)))
                                             * 1.4826)})
        else:
            out.append({"delay_samples": float("nan"), "valid": False,
                        "n_frames": int(d.size), "mad_samples": None})
    return out


def window_report(rec: dict, n_samples: int, window_s: float, hop_s: float,
                  min_frames: int) -> dict:
    """一个 (窗长, 跳长) 的完整可行性读数。"""
    ws = int(round(window_s * SR))
    hs = int(round(hop_s * SR))
    meas = window_measurements(rec, n_samples, ws, hs, min_frames)
    n_meas = len([m for m in meas if m["valid"] or True])
    n_res = sum(1 for m in meas if m["valid"])
    n_need = tv.required_window_count(n_samples, hop_samples=hs)
    seam = n_need - len(meas)

    # 用生产 API 建 plan：mask 由测量有效性派生，缺口由 policy 填充
    plan = None
    plan_err = None
    try:
        plan = tv.build_correction_plan(meas, n_samples=n_samples,
                                        window_samples=ws, hop_samples=hs,
                                        policy=tv.DEFAULT_POLICY,
                                        coordinate=tv.DEFAULT_COORDINATE)
    except Exception as exc:                        # noqa: BLE001
        plan_err = f"{type(exc).__name__}: {exc}"

    # 逐窗修正后的残余: 窗内帧延迟相对该窗移位
    resid = {"p50": None, "p90": None, "p99": None, "n": 0}
    seam_steps: list[int] = []
    if plan is not None:
        shifts = np.asarray(plan.shifts)
        states = list(plan.states)
        good = ~np.isnan(rec["delays"]) & (rec["confidences"] >= 0.3)
        idx = tv.window_index("nearest-centre", rec["centers"],
                              window_samples=ws, hop_samples=hs,
                              n_windows=int(shifts.size))
        devs = []
        for w in range(int(shifts.size)):
            if not plan.resolved[w]:
                continue
            sel = good & (idx == w)
            d = rec["delays"][sel]
            d = d[~np.isnan(d)]
            if d.size:
                devs.append(np.abs(d - shifts[w]))
        if devs:
            a = np.concatenate(devs)
            resid = {"p50": float(np.percentile(a, 50)),
                     "p90": float(np.percentile(a, 90)),
                     "p99": float(np.percentile(a, 99)),
                     "n": int(a.size)}
        res_mask = np.asarray(plan.resolved)
        for w in range(int(shifts.size) - 1):
            if res_mask[w] and res_mask[w + 1]:
                seam_steps.append(abs(int(shifts[w + 1] - shifts[w])))

    steps = np.asarray(seam_steps, dtype=np.int64) if seam_steps else np.zeros(0)
    return {
        "window_s": window_s, "hop_s": hop_s,
        "window_samples": ws, "hop_samples": hs,
        "n_windows_needed": int(n_need),
        "n_windows_measured": int(n_meas),
        "n_windows_resolved": int(n_res),
        "resolved_fraction": (n_res / len(meas)) if meas else 0.0,
        "coverage_fraction_of_needed": (n_res / n_need) if n_need else 0.0,
        "seam_windows_unmeasured_tail": int(seam),
        "longest_unresolved_gap": int(_longest_gap(
            [bool(m["valid"]) for m in meas])),
        "frames_per_window": {
            "mean": (float(np.mean([m.get("n_frames", 0) for m in meas]))
                     if meas else None),
            "p10": float(np.percentile([m.get("n_frames", 0) for m in meas], 10))
            if meas else None,
            "p50": float(np.percentile([m.get("n_frames", 0) for m in meas], 50))
            if meas else None,
            "p90": float(np.percentile([m.get("n_frames", 0) for m in meas], 90))
            if meas else None,
            "zero_windows": int(sum(1 for m in meas
                                    if m.get("n_frames", 0) == 0)),
            # 可用帧在时间上**是否聚集**: 若 p50=0 而 mean>1, 说明可测段是
            # 成片的, 大量窗天生没有证据 —— 换几何救不了, 只能换窗位置或策略。
            "windows_ge3": int(sum(1 for m in meas
                                   if m.get("n_frames", 0) >= 3)),
            "hist": {str(k): int(sum(1 for m in meas
                                    if m.get("n_frames", 0) == k))
                     for k in (0, 1, 2, 3, 5, 10, 30)},
        },
        "plan": None if plan is None else {
            "policy": plan.policy, "coordinate": plan.coordinate,
            "n_windows": int(plan.n_windows),
            "state_counts": plan.state_counts(),
            "overclaimed": int(plan.overclaimed_windows()),
        },
        "plan_error": plan_err,
        "residual_after_windowed_correction_samples": resid,
        "seam_step_samples": {
            "n": int(steps.size),
            "p50": float(np.percentile(steps, 50)) if steps.size else None,
            "p90": float(np.percentile(steps, 90)) if steps.size else None,
            "max": int(steps.max()) if steps.size else None,
            "gt_1": int((steps > 1).sum()), "gt_5": int((steps > 5).sum()),
            "gt_48": int((steps > 48).sum()),
        },
        "feasible_at_60pct": bool(
            (n_res / n_need) >= FEASIBILITY_MIN_RESOLVED) if n_need else False,
    }


def _longest_gap(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        if f:
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
    return best


def _run_window_stage(out: Path, clip: Path, tdir: Path, anchor: int,
                      targets: list[int], n_samples: int, budget: Budget,
                      pending: list[str]) -> list[dict]:
    """S2: 对已有轨迹 checkpoint 跑逐窗可行性（不测量, 只分析）。"""
    rows: list[dict] = []
    for gname in [g[3] for g in GEOMETRIES]:
        for tgt in targets:
            npz = tdir / f"{clip.stem}__r{anchor}_t{tgt}__{gname}.npz"
            rec = load_traj(npz)
            if rec is None:
                continue
            for window_s, hop_s in WINDOW_SCALES:
                if budget.exhausted():
                    pending.append(
                        f"win__{clip.stem}__CH{tgt + 1}__{window_s:g}s")
                    continue
                row = window_report(rec, n_samples, window_s, hop_s,
                                    min_frames=3)
                row["clip"] = clip.stem
                row["target"] = tgt + 1
                row["geometry"] = gname
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# S3: 合成校验（证明 S2 的数字可信）
# ---------------------------------------------------------------------------
def _bandlimited_noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    # 简单带限: 滑动平均 + 一阶差分, 让频谱集中在语音/音乐常见区间
    k = np.hanning(9)
    k /= k.sum()
    x = np.convolve(x, k, mode="same")
    return x.astype(np.float32)


def _shift_with_drift(src: np.ndarray, delay_fn) -> np.ndarray:
    """out[n] = src[n + delay_fn(n)], 分数位移用线性插值。"""
    n = src.size
    pos = np.arange(n, dtype=np.float64) + delay_fn(n)
    out = np.interp(pos, np.arange(n, dtype=np.float64), src).astype(np.float32)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _write_raw(path: Path, arr: np.ndarray) -> Path:
    np.asarray(arr, dtype=np.float32).tofile(path)
    return path


def synthetic_validation(work: Path, budget: Budget, excerpt_s: float = 240.0,
                         workers: int = 4) -> dict:
    """注入已知延迟/漂移, 检查 estimate_pair 与逐窗管线能否复原真值。"""
    log("S3 合成校验 …")
    eff = cs.effective_opts(None)
    n = int(excerpt_s * SR)
    # 优先用真实素材（缓存里最长的那条），退化时才用合成噪声
    real = None
    preferred = [p for p in sorted(PCM_CACHE.glob("*/ch3.f32"))
                 if any(k in p.parent.name for k in PREFER_STEMS)]
    for cand in preferred + [p for p in sorted(PCM_CACHE.glob("*/ch3.f32"))
                             if p not in preferred]:
        try:
            size = cand.stat().st_size
        except OSError:
            continue
        if size >= n * 4:
            real = cand
            break
    if real is not None:
        src = np.fromfile(real, dtype=np.float32, count=n)
        src_label = f"真实素材 {real.parent.name}/ch3"
    else:
        src = _bandlimited_noise(n, 1234)
        src_label = "合成带限噪声"
    src = src - float(np.mean(src))

    d = work / "s3"
    d.mkdir(parents=True, exist_ok=True)
    ref_path = _write_raw(d / "ref.f32", src)

    cases: list[dict] = []
    # 注入方向: out[n] = src[n + d] —— 目标被**自己**提前了 d, 所以估计器
    # 报出的 delay = -d (生产约定 delay>0 = 目标晚到)。期望值一律取 -d。
    truth: list[tuple[str, float, callable]] = [
        ("常数 0", 0.0, lambda k: np.full(k, 0.0)),
        ("常数 +240 (目标提前 5.0ms)", -240.0, lambda k: np.full(k, 240.0)),
        ("常数 -1200 (目标延后 25.0ms)", 1200.0, lambda k: np.full(k, -1200.0)),
        # 漂移: 240 s 内线性变化 2400 采样 (100 ppm), 期望中位 = -1200
        ("漂移 0→2400 (100ppm)", -1200.0,
         lambda k: np.linspace(0.0, 2400.0, k)),
    ]
    for label, expected, fn in truth:
        if budget.exhausted():
            log("  预算用尽, 合成校验提前结束")
            break
        tgt = _shift_with_drift(src, fn)
        p = _write_raw(d / f"tgt_{abs(hash(label)) % 9999}.f32", tgt)
        for frame_ms, hop_ms, anchor_s, gname in GEOMETRIES:
            if budget.exhausted():
                break
            t0 = time.time()
            try:
                est = se.estimate_pair(
                    str(ref_path), str(p), sample_rate=SR,
                    search_window_ms=eff["search_window_ms"],
                    frame_ms=frame_ms, hop_ms=hop_ms,
                    anchor_segment_seconds=anchor_s,
                    min_confidence=eff["min_confidence"],
                    coarse_rate=eff["coarse_rate"],
                    fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                    min_usable_frames=eff["min_usable_frames"],
                    frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                    ref_index=0, tgt_index=1, workers=workers)
            except Exception as exc:            # noqa: BLE001
                cases.append({"case": label, "geometry": gname,
                              "error": f"{type(exc).__name__}: {exc}"})
                continue
            got = float(est.delay_samples)
            cases.append({
                "case": label, "expected_samples": expected,
                "geometry": gname, "frame_ms": frame_ms, "hop_ms": hop_ms,
                "got_samples": got, "error_samples": got - expected,
                "usable_frames": int(est.usable_frames),
                "delay_ms": float(est.delay_ms),
                "wall_s": round(time.time() - t0, 2),
            })
            log(f"    {label:<22} {gname:<22} got={got:+9.1f} "
                f"err={got - expected:+8.1f}  ({time.time() - t0:.1f}s)")

    # 逐窗管线校验: (a) 对照 —— 常数延迟下每窗中位应一致; (b) 漂移 ——
    # 每窗中位应跟上真值。两者一起才说明"逐窗数字可信": 没有 (a) 的零误差
    # 底噪, (b) 的偏差无法归因。
    def _window_rows(path: Path, truth_fn) -> list[dict]:
        est = se.estimate_pair(
            str(ref_path), str(path), sample_rate=SR,
            search_window_ms=eff["search_window_ms"],
            frame_ms=100.0, hop_ms=50.0, anchor_segment_seconds=20.0,
            min_confidence=eff["min_confidence"],
            coarse_rate=eff["coarse_rate"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
            min_usable_frames=eff["min_usable_frames"],
            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
            ref_index=0, tgt_index=1, workers=workers)
        frames = est.frames or []
        centers = np.array([f.center_sample for f in frames], dtype=np.int64)
        delays = np.array([f.delay_samples for f in frames], dtype=np.float64)
        confs = np.array([f.confidence for f in frames], dtype=np.float64)
        good = ~np.isnan(delays) & (confs >= eff["min_confidence"])
        out: list[dict] = []
        for w_s, h_s in WINDOW_SCALES:
            ws, hs = int(round(w_s * SR)), int(round(h_s * SR))
            if ws > n:
                continue
            n_win = tv.required_window_count(n, hop_samples=hs)
            idx = tv.window_index("nearest-centre", centers,
                                  window_samples=ws, hop_samples=hs,
                                  n_windows=n_win)
            for w in range(tv.measured_window_count(n, window_samples=ws,
                                                    hop_samples=hs)):
                sel = good & (idx == w)
                dd = delays[sel]
                if dd.size < 3:
                    continue
                mid = w * hs + ws / 2.0
                truth_d = truth_fn(mid, w, hs, ws)
                out.append({"window_s": w_s, "w": w,
                            "measured": float(np.median(dd)),
                            "truth": float(truth_d),
                            "error": float(np.median(dd) - truth_d),
                            "n_frames": int(dd.size)})
        return out

    def _summ(rows: list[dict]) -> dict:
        if not rows:
            return {}
        errs = np.array([abs(r["error"]) for r in rows])
        return {"n_windows": len(rows),
                "err_samples_p50": float(np.percentile(errs, 50)),
                "err_samples_p90": float(np.percentile(errs, 90)),
                "err_samples_max": float(errs.max())}

    control_rows = _window_rows(
        _write_raw(d / "const.f32", _shift_with_drift(
            src, lambda k: np.full(k, -1200.0))),
        lambda mid, w, hs, ws: 1200.0)   # 注入 -1200 => 目标延后 => 期望 +1200
    drift_rows = _window_rows(
        _write_raw(d / "drift.f32", _shift_with_drift(
            src, lambda k: np.linspace(0.0, 2400.0, k))),
        lambda mid, w, hs, ws: -2400.0 * (mid / n))
    drift_check = {
        "control_constant_-1200": _summ(control_rows),
        "drift_0_to_2400": _summ(drift_rows),
        "drift_in_window_span_samples": {
            "5s": 2400.0 * (5.0 * SR) / n,
            "10s": 2400.0 * (10.0 * SR) / n,
        },
        "control_sample_rows": control_rows[:6],
        "drift_sample_rows": drift_rows[:8],
    }
    # 合成校验的中间 raw 是纯派生数据, 用完即删（否则输出目录多出几百 MB）
    for leftover in d.glob("*.f32"):
        try:
            leftover.unlink()
        except OSError:
            pass
    return {"source": src_label, "excerpt_s": excerpt_s,
            "geometries": [g[3] for g in GEOMETRIES],
            "cases": cases, "drift_window_check": drift_check}


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_report(out: Path, inv: list[dict], traj_meta: list[dict],
                 windows: dict, synthetic: dict | None,
                 budget: Budget, pending: list[str]) -> None:
    lines: list[str] = []
    a = lines.append
    a("# 长镜头逐窗对齐可行性探针 —— 报告\n")
    a(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    a(f"- 用时: {budget.elapsed / 60:.1f} 分钟"
      + (f" (预算 {budget.limit_s / 60:.0f} 分钟)"
         if budget.limit_s else ""))
    a(f"- 全部测量/计划逻辑来自生产 API; 本程序只驱动与落盘")
    a("")
    a("## 1 素材\n")
    a("| 文件 | 时长 | 音频流 | 采样率 |")
    a("|---|---|---|---|")
    for it in inv:
        a(f"| {it['name']} | {it['duration_s']:.1f}s | {it['n_audio']} | "
          f"{it['sample_rate']} |")
    a("")
    a("## 2 逐帧测量（生产几何）\n")
    a("| 文件 | 参考→目标 | 可用帧 | 总帧 | 延迟 | MAD | 极差 | 漂移 |")
    a("|---|---|---|---|---|---|---|---|")
    for m in traj_meta:
        if m.get("frame_ms") != 200.0 or m.get("hop_ms") != 100.0:
            continue
        s = m.get("summarize") or {}
        a(f"| {m['clip']} | CH{m['ref'] + 1}→CH{m['tgt'] + 1} | "
          f"{m['usable_frames']} | {m['n_frames']} | {m['delay_ms']:+.3f}ms | "
          f"{_fmt(s.get('mad_ms'))}ms | {s.get('spread_samples')} | "
          f"{_fmt(s.get('drift_ppm'))}ppm |")
    a("")
    a("## 3 逐窗可行性\n")
    for clip, per_scale in windows.items():
        a(f"### {clip}\n")
        a("| 几何 | 窗/跳 | 需要窗 | 实测窗 | 已解析 | 解析率 | 最长缺口 | "
          "帧/窗 p50 | 缺口>0 窗 | 逐窗修正残余 p90 | 接缝步长 p90/max | "
          ">1/>5/>48 | 60% 门 |")
        a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in per_scale:
            fw = r["frames_per_window"]
            rs = r["residual_after_windowed_correction_samples"]
            ss = r["seam_step_samples"]
            a(f"| {r.get('geometry', '—')} | {r['window_s']:.0f}/"
              f"{r['hop_s']:.0f}s | "
              f"{r['n_windows_needed']} | {r['n_windows_measured']} | "
              f"{r['n_windows_resolved']} | "
              f"{100 * r['resolved_fraction']:.1f}% | "
              f"{r['longest_unresolved_gap']} | {_fmt(fw['p50'], 1)} | "
              f"{fw['zero_windows']} | {_fmt(rs.get('p90'), 1)} | "
              f"{_fmt(ss.get('p90'), 1)}/{ss.get('max')} | "
              f"{ss['gt_1']}/{ss['gt_5']}/{ss['gt_48']} | "
              f"{'PASS' if r['feasible_at_60pct'] else 'FAIL'} |")
        a("")
        plan = next((r["plan"] for r in per_scale if r["plan"]), None)
        errs = [r["plan_error"] for r in per_scale if r["plan_error"]]
        if plan:
            a(f"- plan 状态分布（{per_scale[0]['window_s']:.0f}s 窗, "
              f"policy={plan['policy']}, coordinate={plan['coordinate']}）: "
              f"`{plan['state_counts']}`；overclaimed={plan['overclaimed']}")
        if errs:
            a(f"- plan 构建失败: `{errs[0]}`")
        a("")
    if synthetic:
        a("## 4 合成校验（估计器可信度）\n")
        a(f"- 素材: {synthetic['source']}, 片段 {synthetic['excerpt_s']:.0f}s")
        a("")
        a("| 注入 | 几何 | 期望(采样) | 实测 | 误差 | 可用帧 |")
        a("|---|---|---|---|---|---|")
        for c in synthetic["cases"]:
            if c.get("error") and "expected_samples" not in c:
                a(f"| {c['case']} | {c.get('geometry')} | — | — | "
                  f"{c['error']} | — |")
                continue
            a(f"| {c['case']} | {c['geometry']} | "
              f"{c['expected_samples']:+.0f} | {c['got_samples']:+.1f} | "
              f"{c['error_samples']:+.1f} | {c['usable_frames']} |")
        a("")
        dc = synthetic.get("drift_window_check") or {}
        if dc.get("control_constant_-1200") or dc.get("drift_0_to_2400"):
            a("逐窗管线校验（每窗中位 vs 真值，采样）\n")
            a("| 场景 | 窗数 | 误差 p50 | p90 | max |")
            a("|---|---|---|---|---|")
            for key, label in (("control_constant_-1200", "对照: 常数 −1200"),
                               ("drift_0_to_2400", "漂移 0→2400")):
                s = dc.get(key) or {}
                if not s:
                    continue
                a(f"| {label} | {s['n_windows']} | "
                  f"{s['err_samples_p50']:.1f} | {s['err_samples_p90']:.1f} | "
                  f"{s['err_samples_max']:.1f} |")
            span = dc.get("drift_in_window_span_samples") or {}
            if span:
                a("")
                a("注: 漂移场景下，一个窗**自身跨度内**的真值变化量 = "
                  f"5 s 窗 {span.get('5s', 0):.0f} 采样 / "
                  f"10 s 窗 {span.get('10s', 0):.0f} 采样；"
                  "窗中位能逼近的上限由它决定，误差远小于它即视为跟上。")
            a("")
    a("## 5 结论输入\n")
    a("判定 `60% 门` = 已解析窗数 / applier 需要的窗数 ≥ 0.60"
      "（计划书 §4 的证据门）。")
    a("")
    if pending:
        a(f"**预算内未完成的任务（{len(pending)} 个）**:")
        for t in pending[:20]:
            a(f"- `{t}`")
        a("")
        a("重跑同一命令即可续跑。")
    out.joinpath("report.md").write_text("\n".join(lines), encoding="utf-8")


def _fmt(v, nd: int = 3) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f):
        return "—"
    return f"{f:.{nd}f}"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="长镜头逐窗对齐可行性探针 (CPU only, 可续跑)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--budget-minutes", type=float, default=150.0)
    ap.add_argument("--clips", nargs="*", default=DEFAULT_CLIPS)
    ap.add_argument("--workers", type=int, default=4,
                    help="estimate_pair 的进程数 (CPU)")
    ap.add_argument("--threads", type=int, default=4, help="BLAS 线程上限")
    ap.add_argument("--excerpt-s", type=float, default=240.0,
                    help="合成校验片段长度")
    ap.add_argument("--only", nargs="*", default=[],
                    help="只跑这些阶段: traj windows synth")
    ap.add_argument("--fresh", action="store_true", help="忽略已有 checkpoint")
    ap.add_argument("--no-frames-trajectory", action="store_true",
                    help="不落盘全帧轨迹 (只留统计)")
    args = ap.parse_args()

    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS"):
        os.environ[v] = str(max(1, args.threads))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.budget_minutes)
    stages = set(args.only) if args.only else {"traj", "windows", "synth"}

    log(f"输出目录 {out}; 预算 {args.budget_minutes} 分钟; "
        f"workers={args.workers} threads={args.threads}")

    # ---- S0 清单 ----
    inv: list[dict] = []
    for c in args.clips:
        p = Path(c)
        if not p.is_file():
            log(f"跳过不存在的素材: {p}")
            continue
        info = probe(p)
        inv.append(info)
        log(f"S0 {info['name']}: {info['duration_s']:.1f}s, "
            f"{info['n_audio']} 音频流, 采样率 {info['sample_rate']}")
    if not inv:
        log("没有可用素材")
        return 2
    atomic_json(out / "s0_inventory.json", inv)

    pending: list[str] = []
    traj_meta: list[dict] = []
    windows: dict[str, list] = {}

    # ---- S1 解码 + 轨迹 ----
    per_clip_raw: dict[str, tuple[list[Path], dict]] = {}
    for info in inv:
        clip = Path(info["clip"])
        try:
            raw = decode_channels(clip, out, info["streams"], budget)
        except SystemExit:
            pending.append(f"decode__{clip.stem}")
            continue
        per_clip_raw[clip.stem] = (raw, info)
        n_samples = min(p.stat().st_size // 4 for p in raw)
        log(f"S1 {clip.stem}: {n_samples} 采样/轨 "
            f"({n_samples / SR:.1f}s), raw 就绪")

        tdir = out / "traj"
        tdir.mkdir(exist_ok=True)
        # 目标 = 非锚轨; 锚轨按生产锚点候选顺序取第一个可用 (默认 CH3)
        anchor = 2 if len(raw) > 2 else 0
        targets = [i for i in range(len(raw)) if i != anchor]
        if "traj" not in stages:
            # 只跑 windows 时: 从已有 checkpoint 重建报告统计, 再单独跑窗口分析
            if "windows" in stages:
                for tgt in targets:
                    for geom in GEOMETRIES:
                        gname = geom[3]
                        rec = load_traj(
                            tdir / f"{clip.stem}__r{anchor}_t{tgt}__{gname}.npz")
                        if rec is not None:
                            rec.setdefault("clip", clip.stem)
                            rec.setdefault("geometry", gname)
                            traj_meta.append(rec)
                rows = _run_window_stage(out, clip, tdir, anchor, targets,
                                         n_samples, budget, pending)
                if rows:
                    windows[clip.stem] = rows
                    atomic_json(out / "s2_windows.json", windows)
            continue
        jobs: list[tuple[int, int, tuple]] = []
        for tgt in targets:
            for geom in GEOMETRIES:
                jobs.append((anchor, tgt, geom))
        for ref, tgt, geom in jobs:
            frame_ms, hop_ms, anchor_s, gname = geom
            tid = task_id_traj(clip.stem, ref, tgt, gname)
            npz = tdir / f"{clip.stem}__r{ref}_t{tgt}__{gname}.npz"
            rec = None if args.fresh else load_traj(npz)
            if rec is None:
                if budget.exhausted():
                    pending.append(tid)
                    continue
                t0 = time.time()
                try:
                    rec = run_trajectory(raw, ref, tgt, frame_ms, hop_ms,
                                         anchor_s, args.workers)
                except Exception as exc:        # noqa: BLE001
                    log(f"  {tid} 失败: {type(exc).__name__}: {exc}")
                    pending.append(tid)
                    continue
                rec["clip"] = clip.stem
                rec["geometry"] = gname
                if not args.no_frames_trajectory:
                    save_traj(npz, rec)
                else:
                    atomic_json(npz.with_suffix(".json"),
                                {k: v for k, v in rec.items()
                                 if k not in ("centers", "delays",
                                              "confidences")}
                                | {"clip": clip.stem, "geometry": gname})
                log(f"  {tid}: 可用 {rec['usable_frames']}/{rec['n_frames']} 帧, "
                    f"延迟 {rec['delay_ms']:+.3f}ms ({time.time() - t0:.1f}s)")
            traj_meta.append(rec)

        # ---- S2 逐窗（用生产几何 + 最密几何各做一遍）----
        if "windows" not in stages:
            continue
        rows = _run_window_stage(out, clip, tdir, anchor, targets,
                                 n_samples, budget, pending)
        if rows:
            windows[clip.stem] = rows
            atomic_json(out / "s2_windows.json", windows)

    # ---- S3 合成校验 ----
    synthetic = None
    if "synth" in stages:
        if budget.exhausted():
            pending.append("synth")
        else:
            try:
                synthetic = synthetic_validation(
                    out, budget, excerpt_s=args.excerpt_s,
                    workers=args.workers)
                atomic_json(out / "s3_synthetic.json", synthetic)
            except Exception as exc:            # noqa: BLE001
                log(f"S3 失败: {type(exc).__name__}: {exc}")
                pending.append("synth")

    # ---- 报告 ----
    atomic_json(out / "manifest.json", {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(budget.elapsed, 1),
        "budget_minutes": args.budget_minutes,
        "clips": [i["name"] for i in inv],
        "geometries": [g[3] for g in GEOMETRIES],
        "window_scales": WINDOW_SCALES,
        "feasibility_min_resolved": FEASIBILITY_MIN_RESOLVED,
        "pending": pending,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    })
    write_report(out, inv, traj_meta, windows, synthetic, budget, pending)
    log(f"完成: {out / 'report.md'}")
    if pending:
        log(f"有 {len(pending)} 个任务未完成 (预算/中断); 重跑可续")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
