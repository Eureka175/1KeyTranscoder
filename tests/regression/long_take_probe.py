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

六个阶段（按优先级，预算用尽即优雅停止）::

    S1 traj       逐帧轨迹: 9 种几何 × 每对 (锚,目标)。含 2 s / 5 s 帧的
                  "密度上限"探针 —— 若这么长的帧仍救不回零证据窗，则"换几何"
                  这条路线被证伪。                          (~30 min / 2 文件)
    S2 windows    逐窗可行性: 5 种 (窗长,跳长) 下的解析率、缺口、接缝步长、
                  逐窗修正后的残余模拟。                    (~1 min)
    S2b enhance   零测量成本的四项分析: 滑窗位置能否救回零证据窗 / 自适应窗长
                  需要多长 / 有证据窗覆盖多少音频时长 / 60 s 最小段的分段常数
                  拟合残余与接缝。                          (~1 min)
    S2c refsweep  参考扫描: 12 个有序通道对（逐窗允许每窗换参考），找出让
                  证据密度最高的参考。                      (~12 min)
    S2d knobs     估计器旋钮扫描: 只用 estimate_pair 已有参数（置信门 / 粗扫率 /
                  搜索窗 / 相位带），看可用帧能否增加。      (~16 min)
    S3 synth      合成校验: 注入已知常数延迟与已知线性漂移，给出偏差/方差，并做
                  "对照 + 漂移"双窗口管线校验 —— 证明 S2 的数字可信。
                                                           (~6 min)

总计约 60-70 分钟（两条长镜头）；`--budget-minutes 150` 只是安全上限。

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
from preservation import sample_entry as sample_entry      # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
SR = 48000
PCM_CACHE = Path(r"E:\1kt_scratch\pcm")   # 只读复用已有解码缓存
DEFAULT_CLIPS = [r"F:\1KeyTranscoder\data\align_4ch_proof\20260919_C1305.MP4",
                 r"F:\1KeyTranscoder\data\align_4ch_proof\20260919_C1306.MP4"]
DEFAULT_OUT = Path(r"F:\_long_take_probe")
PREFER_STEMS: tuple[str, ...] = ("20260919_C1305", "20260919_C1306")

#: 几何扫描: (frame_ms, hop_ms, anchor_segment_seconds)。生产默认是 (200,100,20)。
#: 后两项是"密度上限"探针: 若 2 s / 5 s 帧仍救不回零证据窗, 说明素材在时间上
#: 稀疏, 换几何这条路线就被证伪 (帧越长, 每帧越容易过置信门)。
GEOMETRIES = [
    (100.0, 50.0, 20.0, "dense100"),
    (200.0, 100.0, 20.0, "prod200"),
    (250.0, 100.0, 20.0, "mid250"),
    (500.0, 250.0, 20.0, "coarse500"),
    (1000.0, 500.0, 20.0, "very-coarse1000"),
    (500.0, 100.0, 20.0, "overlap500"),
    (200.0, 100.0, 5.0, "prod200-short-anchor"),
    (2000.0, 1000.0, 20.0, "coarse2000"),
    (5000.0, 2500.0, 20.0, "coarse5000"),
]
PROD_GEOM = "prod200"
#: 参考扫描与估计器旋钮扫描用这个几何: 覆盖最好、代价最低 (帧数 = 时长/1 s)。
REF_GEOM = "very-coarse1000"

#: 参考扫描: 6 个有序通道对 (每条轨既当参考也当目标), 用来看"换参考能否提高
#: 逐窗证据密度" —— 生产中锚点选择是全局的, 逐窗允许每窗选不同参考。
REF_PAIRS = [(0, 1), (0, 2), (0, 3), (1, 0), (1, 2), (1, 3),
             (2, 0), (2, 1), (2, 3), (3, 0), (3, 1), (3, 2)]

#: 估计器旋钮扫描（全部是生产参数, 不新造语义）: 稀疏/弱相关素材上, 降低置信门、
#: 降低粗扫采样率、放宽搜索窗、收窄相位带, 都可能提高可用帧数。
KNOB_VARIANTS = [
    ("minconf0.2", {"min_confidence": 0.2}),
    ("coarse4000", {"coarse_rate": 4000}),
    ("search250", {"search_window_ms": 250.0}),
    ("band100-4000", {"fine_phase_band_hz": (100.0, 4000.0)}),
]

#: 逐窗尺度: (window_s, hop_s)。生产 tv 默认是 (30,10)。
WINDOW_SCALES = [(30.0, 10.0), (15.0, 5.0), (10.0, 5.0), (5.0, 2.0),
                 (60.0, 20.0)]
#: 自适应窗长阶梯: 零证据窗向上加长, 看多长才能凑够帧。
ADAPTIVE_WINDOW_LADDER = [30.0, 60.0, 120.0, 240.0, 480.0]
FEASIBILITY_MIN_RESOLVED = 0.60      # 计划书里的证据门

T0 = time.time()
WORKERS = 4          # main() 按 --workers 覆盖; 供各个任务循环共享


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
                   workers: int, overrides: dict | None = None) -> dict:
    """调用生产 estimate_pair, 把**全帧轨迹**（含被否帧）转成可落盘的数组。

    ``overrides`` 只允许覆盖 estimate_pair 已有的生产参数 (置信门/粗扫率/搜索窗/
    相位带), 用它们做"估计器旋钮"扫描; 不引入新语义。
    """
    eff = cs.effective_opts(None)
    kw: dict = {
        "search_window_ms": eff["search_window_ms"],
        "frame_ms": frame_ms, "hop_ms": hop_ms,
        "anchor_segment_seconds": anchor_s,
        "min_confidence": eff["min_confidence"],
        "coarse_rate": eff["coarse_rate"],
        "fine_phase_band_hz": tuple(eff["fine_phase_band_hz"]),
        "min_usable_frames": eff["min_usable_frames"],
        "frame_min_rms_dbfs": eff["frame_min_rms_dbfs"],
    }
    for k, v in (overrides or {}).items():
        if k not in kw:
            raise KeyError(f"不支持的 estimate_pair 覆盖项: {k}")
        kw[k] = v
    est = se.estimate_pair(
        str(raw[ref]), str(raw[tgt]),
        sample_rate=SR, ref_index=ref, tgt_index=tgt, workers=workers, **kw,
    )
    frames = est.frames or []
    centers = np.fromiter((f.center_sample for f in frames), dtype=np.int64,
                          count=len(frames))
    delays = np.fromiter((f.delay_samples for f in frames), dtype=np.float64,
                         count=len(frames))
    confs = np.fromiter((f.confidence for f in frames), dtype=np.float64,
                        count=len(frames))
    gate = float(kw["min_confidence"])
    good = ~np.isnan(delays) & (confs >= gate)
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
        "conf_gate": gate,
        "overrides": dict(overrides or {}),
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
    gate = float(rec.get("conf_gate", 0.3))
    good = ~np.isnan(delays) & (confs >= gate)
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
# S2b: 逐窗增强分析（滑窗位置 / 自适应窗长 / 音频时间覆盖 / 分段拟合）
# ---------------------------------------------------------------------------
def _good(rec: dict) -> np.ndarray:
    d = rec["delays"]
    c = rec["confidences"]
    return ~np.isnan(d) & (c >= float(rec.get("conf_gate", 0.3)))


def _counts_per_window(rec: dict, n_samples: int, ws: int, hs: int,
                       offset: int = 0) -> np.ndarray:
    """每个窗内的合格帧数。``offset`` 让窗网格整体右移（滑窗位置实验）。"""
    n_meas = tv.measured_window_count(n_samples - offset,
                                      window_samples=ws, hop_samples=hs)
    if n_meas <= 0:
        return np.zeros(0, dtype=np.int64)
    idx = tv.window_index("nearest-centre", rec["centers"] - offset,
                          window_samples=ws, hop_samples=hs,
                          n_windows=tv.required_window_count(
                              n_samples - offset, hop_samples=hs))
    good = _good(rec)
    return np.bincount(idx[good], minlength=n_meas)[:n_meas]


def _piecewise_segments(centers: np.ndarray, delays: np.ndarray,
                        min_seg_samples: int,
                        step_threshold_samples: float) -> dict:
    """把 lag 轨迹切成"段内常数"的段（二分递归），用于估计逐窗修正的残余与接缝。

    只用已测帧（nan 已剔除）。切分依据是左右两半中位差 > 阈值，且每段不少于
    ``min_seg_samples``（默认 60 s）—— 这正是"接缝可以多久出现一次"的约束。
    """
    n = centers.size
    if n < 8:
        return {"segments": 0, "residual_p50": None, "residual_p90": None,
                "seam_steps": [], "min_seg_ok": False}
    segs: list[tuple[int, int]] = [(0, n)]
    out: list[tuple[int, int]] = []
    while segs:
        a, b = segs.pop()
        if b - a < 8:
            out.append((a, b))
            continue
        mid = (a + b) // 2
        left = delays[a:mid]
        right = delays[mid:b]
        if (centers[mid] - centers[a]) < min_seg_samples or \
                (centers[b - 1] - centers[mid]) < min_seg_samples:
            out.append((a, b))
            continue
        if abs(float(np.median(left) - np.median(right))) <= step_threshold_samples:
            out.append((a, b))
            continue
        segs.append((a, mid))
        segs.append((mid, b))
    out.sort()
    residual: list[np.ndarray] = []
    seam: list[float] = []
    medians: list[float] = []
    for a, b in out:
        d = delays[a:b]
        if d.size == 0:
            continue
        m = float(np.median(d))
        medians.append(m)
        residual.append(np.abs(d - m))
    for i in range(1, len(medians)):
        seam.append(abs(medians[i] - medians[i - 1]))
    res = (np.concatenate(residual) if residual
           else np.zeros(0, dtype=np.float64))
    return {
        "segments": len(out),
        "residual_p50": float(np.percentile(res, 50)) if res.size else None,
        "residual_p90": float(np.percentile(res, 90)) if res.size else None,
        "seam_steps": seam,
        "seam_gt_1": int(sum(1 for s in seam if s > 1)),
        "seam_gt_48": int(sum(1 for s in seam if s > 48)),
        "min_seg_ok": True,
    }


def enhanced_window_analysis(rec: dict, n_samples: int) -> dict:
    """在已有轨迹上做四件"零测量成本"的可行性分析。

    1. **滑窗位置**: 窗网格整体偏移 1/4、1/2、3/4 跳，看零证据窗能否被救回。
       （生产 applier 的窗网格是固定的，所以这一步回答"换个切法是否可行"。）
    2. **自适应窗长**: 零证据窗向上加长到 60/120/240/480 s，看多长能凑够 3 帧。
    3. **音频时间覆盖**: 有证据的窗覆盖了多长的音频（不是窗数，是时长）。
    4. **分段常数拟合**: 用 60 s 最小段做二分切分，给出段内残余与接缝步长。
    """
    out: dict = {"scales": [], "piecewise": None, "conf_gate":
                 float(rec.get("conf_gate", 0.3))}
    good = _good(rec)
    centers = rec["centers"][good]
    delays = rec["delays"][good]
    for window_s, hop_s in [(30.0, 10.0), (60.0, 20.0)]:
        ws = int(round(window_s * SR))
        hs = int(round(hop_s * SR))
        base = _counts_per_window(rec, n_samples, ws, hs, offset=0)
        if base.size == 0:
            continue
        n_need = tv.required_window_count(n_samples, hop_samples=hs)
        # 1) 滑窗位置
        offsets = [0, hs // 4, hs // 2, (3 * hs) // 4]
        per_off = []
        best = np.zeros_like(base)
        for off in offsets:
            c = _counts_per_window(rec, n_samples, ws, hs, offset=off)
            m = min(base.size, c.size)
            if m == 0:
                continue
            cnt = np.zeros(base.size, dtype=np.int64)
            cnt[:m] = c[:m]
            per_off.append({"offset_samples": off,
                            "resolved": int((cnt >= 3).sum())})
            best[:m] = np.maximum(best[:m], cnt[:m])
        # 2) 自适应窗长（仅 30 s 网格）
        ladder_need = []
        if (window_s, hop_s) == (30.0, 10.0):
            for w in range(base.size):
                if base[w] >= 3:
                    ladder_need.append(window_s)
                    continue
                need = None
                for L in ADAPTIVE_WINDOW_LADDER:
                    Ls = int(round(L * SR))
                    start = w * hs
                    cnt = int(np.count_nonzero(
                        (centers >= start) & (centers < start + Ls)))
                    if cnt >= 3:
                        need = L
                        break
                ladder_need.append(need if need is not None else -1.0)
            arr = np.asarray([v for v in ladder_need if v > 0],
                             dtype=np.float64)
        else:
            arr = np.zeros(0)
        # 3) 音频时间覆盖: 有证据窗的样本并集（用窗长近似, 按 resolved 计数）
        resolved_mask = base >= 3
        covered_samples = int(resolved_mask.sum()) * ws
        # 4) 无证据窗的连续长度（秒）
        quiet_runs: list[int] = []
        cur = 0
        for flag in resolved_mask.tolist():
            if flag:
                if cur:
                    quiet_runs.append(cur)
                cur = 0
            else:
                cur += 1
        if cur:
            quiet_runs.append(cur)
        out["scales"].append({
            "window_s": window_s, "hop_s": hop_s,
            "n_windows_needed": int(n_need),
            "resolved_base": int(resolved_mask.sum()),
            "resolved_fraction_base":
                float(resolved_mask.sum() / max(1, n_need)),
            "sliding_offsets": per_off,
            "sliding_best_resolved": int((best >= 3).sum()),
            "sliding_best_fraction": float((best >= 3).sum()
                                           / max(1, n_need)),
            "adaptive_needed_window_s": {
                "p50": float(np.percentile(arr, 50)) if arr.size else None,
                "p90": float(np.percentile(arr, 90)) if arr.size else None,
                "max": float(arr.max()) if arr.size else None,
                "n_covered": int(arr.size),
            },
            "audio_time_coverage": float(min(1.0, covered_samples / n_samples)),
            "quiet_run_windows": {
                "n": len(quiet_runs),
                "p50_s": (float(np.median(quiet_runs) * hop_s)
                          if quiet_runs else 0.0),
                "p90_s": (float(np.percentile(quiet_runs, 90) * hop_s)
                          if quiet_runs else 0.0),
                "max_s": (float(max(quiet_runs) * hop_s)
                          if quiet_runs else 0.0),
            },
        })
    pw = _piecewise_segments(centers, delays,
                             min_seg_samples=int(60.0 * SR),
                             step_threshold_samples=48.0)
    out["piecewise"] = pw
    return out


# ---------------------------------------------------------------------------
# S2c/S2d: 参考扫描 / 估计器旋钮扫描
# ---------------------------------------------------------------------------
def _traj_path(tdir: Path, clip_stem: str, ref: int, tgt: int,
               gname: str, variant: str | None = None) -> Path:
    suffix = f"__{variant}" if variant else ""
    return tdir / f"{clip_stem}__r{ref}_t{tgt}__{gname}{suffix}.npz"


def _run_pair_stage(tdir: Path, clip_stem: str, raw: list[Path],
                    pairs: list[tuple[int, int]], geom: str,
                    budget: Budget, pending: list[str],
                    variant: str | None = None,
                    overrides: dict | None = None) -> list[dict]:
    """通用"给定几何/通道对/参数变体，跑轨迹测量"的任务循环（可续跑）。"""
    g = next(g for g in GEOMETRIES if g[3] == geom)
    frame_ms, hop_ms, anchor_s = g[0], g[1], g[2]
    recs: list[dict] = []
    for ref, tgt in pairs:
        path = _traj_path(tdir, clip_stem, ref, tgt, geom, variant)
        rec = load_traj(path)
        if rec is None:
            if budget.exhausted():
                pending.append(f"pair__{clip_stem}__r{ref}_t{tgt}__{geom}"
                               + (f"__{variant}" if variant else ""))
                continue
            try:
                rec = run_trajectory(raw, ref, tgt, frame_ms, hop_ms, anchor_s,
                                     WORKERS, overrides)
            except Exception as exc:            # noqa: BLE001
                log(f"  r{ref}t{tgt} {geom}{variant or ''} 失败: "
                    f"{type(exc).__name__}: {exc}")
                pending.append(f"pair__{clip_stem}__r{ref}_t{tgt}__{geom}")
                continue
            rec["clip"] = clip_stem
            rec["geometry"] = geom
            rec["variant"] = variant
            save_traj(path, rec)
            log(f"  r{ref}t{tgt} {geom}{'/' + variant if variant else ''}: "
                f"可用 {rec['usable_frames']}/{rec['n_frames']}")
        rec["clip"] = clip_stem
        rec["geometry"] = geom
        rec.setdefault("variant", variant)
        recs.append(rec)
    return recs


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_report(out: Path, inv: list[dict], traj_meta: list[dict],
                 windows: dict, synthetic: dict | None,
                 budget: Budget, pending: list[str],
                 enhanced: dict | None = None,
                 refsweep: dict | None = None,
                 knobs: dict | None = None,
                 profile: dict | None = None,
                 correlation: dict | None = None,
                 entries: dict | None = None,
                 oracle: dict | None = None,
                 gaps: dict | None = None) -> None:
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
    if entries or profile or correlation:
        a("## 1b 素材体检\n")
        if entries:
            a("### sample entry 自洽性（容器声明 vs 实际）\n")
            a("| 文件 | 流 | fourcc | samplesize | pcmC 位深 | 自洽 |")
            a("|---|---|---|---|---|---|")
            for clip, rows in entries.items():
                for r in rows:
                    if r.get("error"):
                        a(f"| {clip} | — | — | — | — | 读不出: "
                          f"{r['error'][:40]} |")
                        continue
                    a(f"| {clip} | {r.get('stream')} | {r.get('fourcc')} | "
                      f"{r.get('samplesize_bits')} | {r.get('pcmc_bits')} | "
                      f"{'是' if r.get('consistent', True) else '**否**'} |")
            a("")
        if profile:
            a("### 逐通道电平与静音（60 s 块）\n")
            a("| 文件 | CH | RMS p10/p50/p90 dBFS | 峰值 | 静音块 | 静音占比 | "
              "最长静音 |")
            a("|---|---|---|---|---|---|---|")
            for clip, d in profile.items():
                for ch in d["channels"]:
                    a(f"| {clip} | CH{ch['ch']} | "
                      f"{ch['rms_db_p10']:.1f}/{ch['rms_db_p50']:.1f}/"
                      f"{ch['rms_db_p90']:.1f} | {ch['peak_db_max']:.1f} | "
                      f"{ch['silent_blocks']}/{ch['blocks']} | "
                      f"{100 * ch['silent_fraction']:.0f}% | "
                      f"{ch['longest_silent_run_s']:.0f}s |")
            a("")
        if correlation:
            a("### 分块相关性（8 kHz NCC 峰值，逐块）\n")
            a("| 文件 | 通道对 | NCC 中位 | p10 | 相关块 | 弱 | 不相关 | "
              "lag 中位 | lag 离散 |")
            a("|---|---|---|---|---|---|---|---|---|")
            for clip, d in correlation.items():
                for pr in d["pairs"]:
                    a(f"| {clip} | CH{pr['a']}-CH{pr['b']} | "
                      f"{pr['ncc_median']:.3f} | {pr['ncc_p10']:.3f} | "
                      f"{pr['blocks_correlated']} | {pr['blocks_weak']} | "
                      f"{pr['blocks_uncorrelated']} | {pr['lag_median']} | "
                      f"{pr['lag_spread']} |")
            a("")
            a("判读：`相关块` 少而 `不相关块` 多 = 相关性只在局部时段成立，"
              "那些时段的窗不可能有证据。\n")

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
    if enhanced:
        a("## 3b 逐窗增强分析\n")
        a("回答四个问题：滑窗位置能否救回零证据窗 / 自适应窗长需要多长 / "
          "有证据的窗覆盖了多少音频时长 / 分段常数拟合的残余与接缝。\n")
        for clip, rows in enhanced.items():
            a(f"### {clip}\n")
            a("| 几何 | 窗/跳 | 已解析 | 滑窗最优 | 自适应窗长 p50/p90 s | "
              "音频时长覆盖 | 无证据连续段 p50/p90/max s | 分段数 | "
              "段内残余 p90 | 接缝 >1/>48 |")
            a("|---|---|---|---|---|---|---|---|---|---|")
            for r in rows:
                pw = r.get("piecewise") or {}
                for sc in r.get("scales") or []:
                    ad = sc.get("adaptive_needed_window_s") or {}
                    qr = sc.get("quiet_run_windows") or {}
                    a(f"| {r['geometry']} | {sc['window_s']:.0f}/"
                      f"{sc['hop_s']:.0f}s | {sc['resolved_base']}/"
                      f"{sc['n_windows_needed']} | "
                      f"{sc['sliding_best_resolved']} "
                      f"({100 * sc['sliding_best_fraction']:.0f}%) | "
                      f"{_fmt(ad.get('p50'), 0)}/{_fmt(ad.get('p90'), 0)} | "
                      f"{100 * sc['audio_time_coverage']:.0f}% | "
                      f"{_fmt(qr.get('p50_s'), 0)}/{_fmt(qr.get('p90_s'), 0)}/"
                      f"{_fmt(qr.get('max_s'), 0)} | "
                      f"{_fmt(pw.get('segments'), 0)} | "
                      f"{_fmt(pw.get('residual_p90'), 1)} | "
                      f"{pw.get('seam_gt_1', 0)}/{pw.get('seam_gt_48', 0)} |")
            a("")

    if refsweep:
        a("## 3c 参考扫描（逐窗可选不同参考）\n")
        for clip, rows in refsweep.items():
            a(f"### {clip}\n")
            a("| 参考→目标 | 可用帧 | 延迟 | 30/10s 已解析 | 解析率 |")
            a("|---|---|---|---|---|")
            for r in sorted(rows, key=lambda x: (x["tgt"], -x["resolved_30_10"])):
                a(f"| CH{r['ref']}→CH{r['tgt']} | {r['usable_frames']} | "
                  f"{_fmt(r['delay_ms'], 2)}ms | {r['resolved_30_10']} | "
                  f"{100 * r['resolved_fraction_30_10']:.1f}% |")
            a("")
            best: dict[int, dict] = {}
            for r in rows:
                cur = best.get(r["tgt"])
                if cur is None or r["resolved_30_10"] > cur["resolved_30_10"]:
                    best[r["tgt"]] = r
            if best:
                a("每个目标的最佳参考: "
                  + "; ".join(f"CH{t}←CH{r['ref']} "
                              f"({r['resolved_30_10']} 窗)"
                              for t, r in sorted(best.items())))
                a("")

    if knobs:
        a("## 3d 估计器旋钮扫描（都用 estimate_pair 已有参数）\n")
        for clip, rows in knobs.items():
            a(f"### {clip}\n")
            a("| 变体 | 覆盖参数 | 目标 | 可用帧 | 30/10s 已解析 |")
            a("|---|---|---|---|---|")
            for r in rows:
                a(f"| {r['variant']} | `{r['overrides']}` | "
                  f"CH{r['target']} | {r['usable_frames']} | "
                  f"{r['resolved_30_10']} |")
            a("")

    if oracle:
        a("## 3e 上限分析（逐窗换几何 / 换参考能到多少）\n")
        a("`best_single` = 固定一个几何/参考；`union_geometry` = 逐窗换几何；"
          "`union_all` = 连参考与旋钮变体一起换。判据是 `union_all` 是否达到 "
          f"{FEASIBILITY_MIN_RESOLVED:.0%} 门。\n")
        for clip, d in oracle.items():
            a(f"### {clip}\n")
            a("| 窗/跳 | 目标 | 来源数 | 最好单一 | 逐窗换几何 | "
              "逐窗全换 | 60% 门 |")
            a("|---|---|---|---|---|---|---|")
            for scale, per_t in d["targets"].items():
                for tgt, v in sorted(per_t.items()):
                    a(f"| {scale} | CH{tgt} | {v['n_sources']} | "
                      f"{v['best_single_resolved']} "
                      f"({100 * v['best_single_fraction']:.1f}%) | "
                      f"{v['union_geometry_resolved']} "
                      f"({100 * v['union_geometry_fraction']:.1f}%) | "
                      f"{v['union_all_resolved']} "
                      f"({100 * v['union_all_fraction']:.1f}%) | "
                      f"{'PASS' if v['union_all_passes_60pct'] else 'FAIL'} |")
            a("")

    if gaps:
        a("## 3f 缺口代价 / 量化敏感度 / 窗内 MAD\n")
        a("`hold` 缺口代价 = 每段无证据窗两端测量的差 —— 这是 hold 策略本身"
          "引入的误差；`量化差` = 整数移位相对分数移位的残余增量。\n")
        a("| 文件 | 几何 | 目标 | 已解析/未解析 | 窗内 MAD p50/p90 ms | "
          "缺口数 | 缺口误差 p50/p90/max | 缺口>1ms | 量化差 p90 |")
        a("|---|---|---|---|---|---|---|---|---|")
        for clip, rows in gaps.items():
            for r in rows:
                hg = r["hold_gap_cost"]
                md = r["window_mad_ms"]
                q = r["quantization_sensitivity"]
                a(f"| {clip} | {r['geometry']} | CH{r['target']} | "
                  f"{r['resolved']}/{r['unresolved']} | "
                  f"{_fmt(md.get('p50'), 2)}/{_fmt(md.get('p90'), 2)} | "
                  f"{hg['n_gaps']} | {_fmt(hg['err_p50'], 1)}/"
                  f"{_fmt(hg['err_p90'], 1)}/{_fmt(hg['err_max'], 1)} | "
                  f"{hg['gaps_gt_48_samples']} | "
                  f"{_fmt(q.get('delta_p90'), 3)} |")
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
# S0b/S0c: 素材自身的体检 —— 电平/静音分布、分块相关性
# ---------------------------------------------------------------------------
def audio_entry_report(clip: Path) -> list[dict]:
    """音频 sample entry 自洽性（读容器, 不解码）。

    这条检查针对一个真实缺陷类: `pcmC` 说 24 bit 而 `samplesize` 说 16 bit 时,
    ffmpeg 采信前者解码正确, 而 Premiere/VLC 采信后者 -> 四轨白噪音。长镜头若
    中招, 后续所有测量都建立在"能解码但播放异常"的音频上, 必须先确认。
    """
    try:
        entries = sample_entry.read_audio_entries(clip)
    except Exception as exc:                    # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    out: list[dict] = []
    with clip.open("rb") as fh:
        for i, e in enumerate(entries):
            rec = {"stream": i, "offset": e.get("offset"),
                   "size": e.get("size")}
            raw = b""
            try:
                fh.seek(int(e["offset"]))
                raw = fh.read(int(e["size"]))
            except Exception as exc:            # noqa: BLE001
                rec["error"] = f"读条目失败: {type(exc).__name__}: {exc}"
            if raw:
                try:
                    rec.update(sample_entry.audio_entry_consistency(raw))
                except Exception as exc:        # noqa: BLE001
                    rec["error"] = f"{type(exc).__name__}: {exc}"
            out.append(rec)
    return out


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """连续 False 段的 (起始块, 长度)。"""
    out: list[tuple[int, int]] = []
    start = None
    for i, f in enumerate(flags + [True]):
        if not f:
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i - start))
            start = None
    return out


def channel_profile(raw: list[Path], n_samples: int,
                    block_s: float = 60.0) -> dict:
    """逐通道、逐 block 的电平与静音分布。

    这是"零证据窗"最可能的物理原因: 舞台收音在节目间隙会长时间静音。若某些
    block 全静音, 那么无论几何怎么调, 那些窗都不可能有证据 —— 不是测量缺陷,
    而是素材没有内容。阈值 -50 dBFS 与生产 `frame_min_rms_dbfs` 同口径。
    """
    bs = int(round(block_s * SR))
    blocks = max(1, n_samples // bs)
    per_ch: list[dict] = []
    for ch, path in enumerate(raw):
        arr = np.memmap(path, dtype=np.float32, mode="r")
        rms_db = np.zeros(blocks)
        peak = np.zeros(blocks)
        for b in range(blocks):
            seg = np.asarray(arr[b * bs:(b + 1) * bs], dtype=np.float64)
            if seg.size == 0:
                rms_db[b] = -120.0
                continue
            r = float(np.sqrt(np.mean(seg * seg)))
            rms_db[b] = 20.0 * math.log10(max(r, 1e-12))
            peak[b] = 20.0 * math.log10(max(float(np.max(np.abs(seg))), 1e-12))
        silent = (rms_db < -50.0).tolist()
        runs = _runs([not f for f in silent])   # _runs 返回 False 段 = 静音段
        per_ch.append({
            "ch": ch + 1,
            "rms_db_p10": float(np.percentile(rms_db, 10)),
            "rms_db_p50": float(np.percentile(rms_db, 50)),
            "rms_db_p90": float(np.percentile(rms_db, 90)),
            "peak_db_max": float(peak.max()),
            "silent_blocks": int(sum(silent)),
            "blocks": int(blocks),
            "silent_fraction": float(sum(silent) / max(1, blocks)),
            "longest_silent_run_s": (float(max((n for _, n in runs),
                                               default=0)) * block_s),
            "silent_runs": [[int(i), int(n)] for i, n in runs if n >= 2],
        })
        del arr
    return {"block_s": block_s, "n_samples": n_samples, "channels": per_ch}


def _decimate(arr, factor: int) -> np.ndarray:
    """抗混叠抽取: 先按 factor 做滑动平均, 再取样。"""
    a = np.asarray(arr, dtype=np.float32)
    if factor <= 1:
        return a - float(a.mean())
    k = np.ones(factor, dtype=np.float32) / factor
    a = np.convolve(a, k, mode="same")[::factor]
    return a - float(a.mean())


def _ncc_peak(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[float, int]:
    a = a - float(a.mean())
    b = b - float(b.mean())
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0, 0
    n = 1 << int(np.ceil(np.log2(a.size + b.size)))
    cc = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    cc = np.concatenate([cc[-max_lag:], cc[:max_lag + 1]]) / (na * nb)
    i = int(np.argmax(np.abs(cc)))
    return float(abs(cc[i])), int(i - max_lag)


def block_correlation(raw: list[Path], n_samples: int,
                      block_s: float = 120.0,
                      max_blocks: int = 60) -> dict:
    """分块相关性: 通道对在**每个时间块**上的 NCC 峰值。

    回答一个关键问题: 互相关是**全局**的, 还是只在该片段的某些时段成立。
    若是后者, "某段有证据、某段没有"就不是几何问题, 而是内容问题。
    8 kHz 抽取 + ±80 ms 搜索: 与生产粗扫同量级, 只判"有没有相关", 不用于定位。
    """
    bs = int(round(block_s * SR))
    n_blocks = max(1, n_samples // bs)
    step = max(1, n_blocks // max_blocks)
    idxs = list(range(0, n_blocks, step))[:max_blocks]
    dec = 6                                  # 48k -> 8k
    max_lag = int(0.080 * SR / dec)
    pairs = [(i, j) for i in range(len(raw)) for j in range(i + 1, len(raw))]
    res: dict = {"block_s": block_s, "decimated_rate": SR // dec,
                 "blocks_used": len(idxs), "pairs": []}
    arrs = [np.memmap(p, dtype=np.float32, mode="r") for p in raw]
    try:
        for i, j in pairs:
            peaks: list[float] = []
            lags: list[int] = []
            for b in idxs:
                seg_a = _decimate(arrs[i][b * bs:(b + 1) * bs], dec)
                seg_b = _decimate(arrs[j][b * bs:(b + 1) * bs], dec)
                p, lag = _ncc_peak(seg_a, seg_b, max_lag)
                peaks.append(p)
                lags.append(lag)
            pa = np.asarray(peaks)
            la = np.asarray(lags, dtype=np.int64)
            res["pairs"].append({
                "a": i + 1, "b": j + 1,
                "ncc_median": float(np.median(pa)),
                "ncc_p10": float(np.percentile(pa, 10)),
                "ncc_p90": float(np.percentile(pa, 90)),
                "blocks_correlated": int((pa >= 0.3).sum()),
                "blocks_weak": int(((pa >= 0.1) & (pa < 0.3)).sum()),
                "blocks_uncorrelated": int((pa < 0.1).sum()),
                "lag_median": int(np.median(la)),
                "lag_spread": int(la.max() - la.min()),
                "per_block_peaks": [round(float(x), 3) for x in pa],
            })
    finally:
        del arrs
    return res


# ---------------------------------------------------------------------------
# S2e: 上限分析 —— 所有 (几何 × 参考) 合在一起能到多少
# ---------------------------------------------------------------------------
def oracle_upper_bound(tdir: Path, stem: str, n_samples: int,
                       targets: list[int]) -> dict:
    """逐窗证据的**上限**: 把所有已测轨迹按 (几何, 参考, 变体) 当独立来源。

    每个窗算三种口径:

      * ``best_single``   —— 最好的单一来源（一个几何/参考固定用到底）
      * ``union_geometry``—— 同一目标下逐窗换几何
      * ``union_all``     —— 连参考与旋钮变体都逐窗换

    如果 ``union_all`` 都到不了 60% 证据门, 那么"逐窗修正"在这两条素材上就不
    成立 —— 与窗长、接缝、policy 都无关, 只是没有证据。
    """
    files = sorted(tdir.glob(f"{stem}__r*_t*__*.npz"))
    n_by_target: dict[int, dict[str, np.ndarray]] = {}
    for f in files:
        rec = load_traj(f)
        if rec is None:
            continue
        tgt = int(rec.get("tgt", -1))
        if tgt not in targets:
            continue
        label = (f"{rec.get('geometry', '?')}|r{rec.get('ref', -1) + 1}"
                 + (f"|{rec.get('variant')}" if rec.get("variant") else ""))
        good = _good(rec)
        n_by_target.setdefault(tgt, {})[label] = rec["centers"][good]
    out: dict = {"targets": {},
                 "n_sources": {str(t): len(v) for t, v in n_by_target.items()}}
    for window_s, hop_s in [(30.0, 10.0), (60.0, 20.0), (15.0, 5.0)]:
        ws, hs = int(round(window_s * SR)), int(round(hop_s * SR))
        n_need = tv.required_window_count(n_samples, hop_samples=hs)
        n_meas = tv.measured_window_count(n_samples, window_samples=ws,
                                          hop_samples=hs)
        if n_meas <= 0:          # 片段比窗短: 这一档窗尺度不适用
            out['targets'][f'{window_s:.0f}/{hop_s:.0f}s'] = {}
            continue
        per_t: dict = {}
        for tgt, sources in n_by_target.items():
            counts = {}
            for label, centers in sources.items():
                if centers.size == 0:
                    counts[label] = np.zeros(n_meas, dtype=np.int64)
                    continue
                idx = tv.window_index("nearest-centre", centers,
                                      window_samples=ws, hop_samples=hs,
                                      n_windows=n_need)
                counts[label] = np.bincount(idx, minlength=n_meas)[:n_meas]
            if not counts:
                continue
            mat = np.vstack([
                np.pad(v, (0, max(0, n_meas - v.size)), constant_values=0)
                for v in counts.values()])
            best_single = int((mat >= 3).sum(axis=1).max())
            fam: dict[str, np.ndarray] = {}
            for label, cnt in counts.items():
                g = label.split("|")[0]
                fam[g] = np.maximum(fam[g], cnt) if g in fam else cnt
            fam_mat = np.vstack(list(fam.values()))
            family_union = int((fam_mat >= 3).any(axis=0).sum())
            all_union = int((mat >= 3).any(axis=0).sum())
            per_t[str(tgt + 1)] = {
                "n_windows_needed": int(n_need),
                "n_windows_measured": int(n_meas),
                "n_sources": len(counts),
                "best_single_resolved": best_single,
                "best_single_fraction": best_single / max(1, n_need),
                "union_geometry_resolved": family_union,
                "union_geometry_fraction": family_union / max(1, n_need),
                "union_all_resolved": all_union,
                "union_all_fraction": all_union / max(1, n_need),
                "union_all_passes_60pct": ((all_union / max(1, n_need))
                                           >= FEASIBILITY_MIN_RESOLVED),
            }
        out["targets"][f"{window_s:.0f}/{hop_s:.0f}s"] = per_t
    return out


# ---------------------------------------------------------------------------
# S2f: 缺口代价 / 量化敏感度 / 窗内 MAD
# ---------------------------------------------------------------------------
def gap_and_quantization(rec: dict, n_samples: int, window_s: float,
                         hop_s: float, min_frames: int = 3) -> dict:
    """1) 缺口代价: 无证据段用 hold 时, 段两端测量差多大。
    2) 量化敏感度: 逐窗整数移位 vs 分数移位, 残余差多少。
    3) 窗内 MAD: 每个已解析窗的延迟离散度（该窗值可信度的直接度量）。
    """
    ws, hs = int(round(window_s * SR)), int(round(hop_s * SR))
    n_need = tv.required_window_count(n_samples, hop_samples=hs)
    n_meas = tv.measured_window_count(n_samples, window_samples=ws,
                                      hop_samples=hs)
    good = _good(rec)
    centers = rec["centers"][good]
    delays = rec["delays"][good]
    if centers.size == 0 or n_meas <= 0:
        return {}
    idx = tv.window_index("nearest-centre", centers, window_samples=ws,
                          hop_samples=hs, n_windows=n_need)
    med = np.full(n_meas, np.nan)
    mad = np.full(n_meas, np.nan)
    frac: list[np.ndarray] = []
    ints: list[np.ndarray] = []
    for w in range(n_meas):
        d = delays[idx == w]
        if d.size < min_frames:
            continue
        m = float(np.median(d))
        med[w] = m
        mad[w] = float(np.median(np.abs(d - m)) * 1.4826)
        frac.append(np.abs(d - m))
        ints.append(np.abs(d - float(round(m))))
    resolved = ~np.isnan(med)
    hold_err: list[float] = []
    hold_span: list[int] = []
    for start, length in _runs(resolved.tolist()):
        prev, nxt = start - 1, start + length
        if prev >= 0 and nxt < resolved.size and resolved[prev] and resolved[nxt]:
            hold_err.append(abs(float(med[nxt] - med[prev])))
            hold_span.append(length)
    mad_ok = mad[resolved]
    qf = np.concatenate(frac) if frac else np.zeros(0)
    qi = np.concatenate(ints) if ints else np.zeros(0)
    return {
        "window_s": window_s, "hop_s": hop_s,
        "resolved": int(resolved.sum()),
        "unresolved": int((~resolved).sum()),
        "window_mad_ms": {
            "p50": (float(np.percentile(mad_ok, 50)) / SR * 1000.0
                    if mad_ok.size else None),
            "p90": (float(np.percentile(mad_ok, 90)) / SR * 1000.0
                    if mad_ok.size else None),
        },
        "hold_gap_cost": {
            "n_gaps": len(hold_err),
            "err_p50": (float(np.percentile(hold_err, 50))
                        if hold_err else None),
            "err_p90": (float(np.percentile(hold_err, 90))
                        if hold_err else None),
            "err_max": (float(max(hold_err)) if hold_err else None),
            "gaps_gt_48_samples": int(sum(1 for e in hold_err if e > 48)),
            "gap_len_p50": (float(np.percentile(hold_span, 50))
                            if hold_span else None),
            "gap_len_max": (int(max(hold_span)) if hold_span else None),
        },
        "quantization_sensitivity": {
            "fractional_p90": (float(np.percentile(qf, 90))
                               if qf.size else None),
            "integer_p90": (float(np.percentile(qi, 90))
                            if qi.size else None),
            "delta_p90": (float(np.percentile(qi, 90) - np.percentile(qf, 90))
                          if qf.size and qi.size else None),
        },
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _stage_extras(out: Path, clip: Path, tdir: Path, raw: list[Path],
                  anchor: int, targets: list[int], n_samples: int,
                  stages: set[str], budget: Budget, pending: list[str],
                  enhanced: dict, refsweep: dict, knobs: dict) -> None:
    """S2b/S2c/S2d: 增强分析（零测量）、参考扫描、估计器旋钮扫描。"""
    stem = clip.stem
    if "enhance" in stages:
        rows: list[dict] = []
        for geom in GEOMETRIES:
            for tgt in targets:
                rec = load_traj(_traj_path(tdir, stem, anchor, tgt, geom[3]))
                if rec is None:
                    continue
                rec.setdefault("conf_gate", 0.3)
                a = enhanced_window_analysis(rec, n_samples)
                a.update({"geometry": geom[3], "target": tgt + 1})
                rows.append(a)
        if rows:
            enhanced[stem] = rows
            atomic_json(out / "s2b_enhanced.json", enhanced)
            log(f"  S2b 增强分析: {len(rows)} 条 (几何×目标)")

    if "refsweep" in stages:
        recs = _run_pair_stage(tdir, stem, raw, REF_PAIRS, REF_GEOM,
                               budget, pending)
        rows = []
        for rec in recs:
            wr = window_report(rec, n_samples, 30.0, 10.0, min_frames=3)
            rows.append({
                "ref": rec["ref"] + 1, "tgt": rec["tgt"] + 1,
                "usable_frames": rec["usable_frames"],
                "delay_ms": rec["delay_ms"],
                "resolved_30_10": wr["n_windows_resolved"],
                "resolved_fraction_30_10": wr["resolved_fraction"],
            })
        if rows:
            refsweep[stem] = rows
            atomic_json(out / "s2c_refsweep.json", refsweep)
            log(f"  S2c 参考扫描: {len(rows)} 个通道对")

    if "knobs" in stages:
        rows = []
        for vname, ov in KNOB_VARIANTS:
            recs = _run_pair_stage(tdir, stem, raw,
                                   [(anchor, t) for t in targets], REF_GEOM,
                                   budget, pending, variant=vname,
                                   overrides=ov)
            for rec in recs:
                wr = window_report(rec, n_samples, 30.0, 10.0, min_frames=3)
                rows.append({
                    "variant": vname, "overrides": ov,
                    "target": rec["tgt"] + 1,
                    "usable_frames": rec["usable_frames"],
                    "resolved_30_10": wr["n_windows_resolved"],
                    "resolved_fraction_30_10": wr["resolved_fraction"],
                })
        if rows:
            knobs[stem] = rows
            atomic_json(out / "s2d_knobs.json", knobs)
            log(f"  S2d 旋钮扫描: {len(rows)} 条 (变体×目标)")


def _stage_oracle_final(out: Path, clip: Path, tdir: Path, anchor: int,
                        targets: list[int], n_samples: int,
                        stages: set[str], budget: Budget,
                        pending: list[str], oracle: dict, gaps: dict) -> None:
    """S2e 上限分析 + S2f 缺口代价（都在已有轨迹上算, 不新增测量）。"""
    if "oracle" not in stages:
        return
    if budget.exhausted():
        pending.append(f"oracle__{clip.stem}")
        return
    try:
        oracle[clip.stem] = oracle_upper_bound(tdir, clip.stem, n_samples,
                                               targets)
        atomic_json(out / "s2e_oracle.json", oracle)
        best = max((v["union_all_fraction"]
                    for st in oracle[clip.stem]["targets"].values()
                    for v in st.values()), default=0.0)
        log(f"  S2e 上限分析: 逐窗换几何+参考的最好目标 "
            f"{100 * best:.0f}% (60% 门)")
    except Exception as exc:                # noqa: BLE001
        log(f"  S2e 失败: {type(exc).__name__}: {exc}")
        pending.append(f"oracle__{clip.stem}")
        return
    rows = []
    for gname in (REF_GEOM, "overlap500", PROD_GEOM):
        for tgt in targets:
            rec = load_traj(_traj_path(tdir, clip.stem, anchor, tgt, gname))
            if rec is None:
                continue
            row = gap_and_quantization(rec, n_samples, 30.0, 10.0)
            if row:
                row.update({"geometry": gname, "target": tgt + 1})
                rows.append(row)
    if rows:
        gaps[clip.stem] = rows
        atomic_json(out / "s2f_gaps.json", gaps)
        log(f"  S2f 缺口代价: {len(rows)} 条")


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
    stages = (set(args.only) if args.only
              else {"profile", "traj", "windows", "enhance",
                    "refsweep", "knobs", "oracle", "synth"})
    global WORKERS
    WORKERS = max(1, args.workers)

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
    enhanced: dict[str, list] = {}
    profile: dict[str, dict] = {}
    correlation: dict[str, dict] = {}
    entries: dict[str, list] = {}
    oracle: dict[str, dict] = {}
    gaps: dict[str, list] = {}
    refsweep: dict[str, list] = {}
    knobs: dict[str, list] = {}

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

        # ---- S0 体检: sample entry / 电平与静音 / 分块相关性 ----
        if "profile" in stages:
            entries[clip.stem] = audio_entry_report(clip)
            atomic_json(out / "s0_entries.json", entries)
            bad = [e for e in entries[clip.stem]
                   if e.get("consistent") is False or e.get("error")]
            log(f"  S0a sample entry: {len(entries[clip.stem])} 条, "
                f"不自洽 {len(bad)} 条")
            try:
                profile[clip.stem] = channel_profile(raw, n_samples)
                atomic_json(out / "s0b_profile.json", profile)
                for ch in profile[clip.stem]["channels"]:
                    log(f"  S0b CH{ch['ch']}: RMS中位 "
                        f"{ch['rms_db_p50']:.1f} dBFS, 静音 "
                        f"{100 * ch['silent_fraction']:.0f}%, "
                        f"最长静音 {ch['longest_silent_run_s']:.0f}s")
            except Exception as exc:            # noqa: BLE001
                log(f"  S0b 失败: {type(exc).__name__}: {exc}")
                pending.append(f"profile__{clip.stem}")
            if not budget.exhausted():
                try:
                    correlation[clip.stem] = block_correlation(raw, n_samples)
                    atomic_json(out / "s0c_correlation.json", correlation)
                    log(f"  S0c 分块相关性: "
                        f"{len(correlation[clip.stem]['pairs'])} 对, "
                        f"{correlation[clip.stem]['blocks_used']} 块")
                except Exception as exc:        # noqa: BLE001
                    log(f"  S0c 失败: {type(exc).__name__}: {exc}")
                    pending.append(f"corr__{clip.stem}")
            else:
                pending.append(f"corr__{clip.stem}")

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
            _stage_extras(out, clip, tdir, raw, anchor, targets, n_samples,
                          stages, budget, pending, enhanced, refsweep, knobs)
            _stage_oracle_final(out, clip, tdir, anchor, targets,
                                n_samples, stages, budget, pending,
                                oracle, gaps)
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

        # ---- S2 逐窗（所有已测几何）----
        if "windows" in stages:
            rows = _run_window_stage(out, clip, tdir, anchor, targets,
                                     n_samples, budget, pending)
            if rows:
                windows[clip.stem] = rows
                atomic_json(out / "s2_windows.json", windows)
        # ---- S2b/S2c/S2d: 增强分析 / 参考扫描 / 旋钮扫描 ----
        _stage_extras(out, clip, tdir, raw, anchor, targets, n_samples,
                      stages, budget, pending, enhanced, refsweep, knobs)
        _stage_oracle_final(out, clip, tdir, anchor, targets,
                            n_samples, stages, budget, pending,
                            oracle, gaps)

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
        "stages": sorted(stages),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    })
    write_report(out, inv, traj_meta, windows, synthetic, budget, pending,
                 enhanced, refsweep, knobs, profile, correlation, entries,
                 oracle, gaps)
    log(f"完成: {out / 'report.md'}")
    if pending:
        log(f"有 {len(pending)} 个任务未完成 (预算/中断); 重跑可续")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
