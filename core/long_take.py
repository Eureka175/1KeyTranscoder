"""长镜头默认对齐编排 (S2/S4/S5).

职责边界（不造第二套轮子）:

* **窗级测量**: `core.sync_estimate.measure_window_delays()`（S1，连续性跟踪；
  12 kHz 池化粗扫 + 全速率精化 + 极性无关的 |CC| 峰）。
* **逐窗计划/施加/独立复测**: `core.audio_sync_tv_exec`
  （`request_time_varying` / `measurements_from_records` / `run_time_varying`）。
  本模块只把"1 s 网格曲线"适配成 records，并在 1 s 跳被几何拒绝时退回 10 s 跳。
* **曲线构造**: 本模块。口径取自研究脚本 `apply_tv.py`（不复制实现）：
  2 min |CC| 加权中位锚点 → 跳变限幅 → 1 s 网格线性插值 → |shift|≤4800 硬门。
* **第二轮残差/极性**: 本模块的测量原语 + 既有 `gcc_phat_int`。

所有产品口径常量集中在 `PROFILE`，与 `core.channel_sync.DEFAULTS` 分离：
本模块是"长镜头后备"的策略层，channel_sync 决定何时进入它。
"""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from . import audio_sync_tv, audio_sync_tv_exec, sync_estimate

__all__ = [
    "PROFILE",
    "align_time_varying",
    "apply_curve",
    "build_curve_from_windows",
    "curve_records",
    "detect_polarity",
    "measure_residual_curve",
]

#: 长镜头后备的产品口径（任务书 §4.3–§4.6 / §7.1 D–F）。
#:
#: 3–8 声道的**自动开启范围**在 channel_sync.DEFAULTS 里（入口边界）；
#: 这里只放"进入时变路径之后"的几何与判据。
PROFILE: dict[str, Any] = {
    # 窗级测量几何。**测量窗 60 s / 10 s 跳**：K oracle 的连续性跟踪器即此几何，
    # 30 s 独立测量在 CH1/CH2 上有 ~100 采样分支跳变尾巴（long-take-windowed-
    # prototype.md §4）；曲线仍是 1 s 网格（见曲线构造）。
    "track_window_seconds": 60.0,
    "track_hop_seconds": 10.0,
    "track_coarse_rate": 12000,
    "track_min_cc": 0.06,
    # 计划几何（D）: 1 s 网格。**plan 的 window==hop**
    # （稠密 1 s 阶梯；nearest-centre 的窗中心随 window 偏移，30 s 窗会把
    # 前 15 s 全映射到窗 0 —— 实测漂移 2 采样/s 时造成 30 采样偏差）。
    #
    # ⚠️ 策略用 **hold** 而不是任务书 §7.1-D 的 `linear`：内置 `audio_sync_tv`
    # 把 `linear` 定义为 **offline-only**（缺口需要右端点，不能驱动流式生产
    # 路径），`audio_sync_tv_exec.apply_shift_plan` 会显式拒绝它。我们的曲线在
    # 1 s 网格上**稠密无缺口**，缺口填充器不参与，`hold` 与 `linear` 输出逐位
    # 相同 —— 选 hold 是"用内置 API 能执行的产品语义"，不是降级（checkpoint 记录）。
    "tv_policy": "hold",
    #
    # ⚠️ 坐标合同用 **latest-start** 而不是 `nearest-centre`：window==hop 时
    # 两者数学上**逐位等价**（nearest-centre 闭式
    # floor((pos - win//2 + hop//2)/hop) = floor(pos/hop) = latest-start）。
    # 但内置独立验证器 `_independent_join` 对 nearest-centre 是 O(n×n_windows)
    # 的朴素 Python 扫描：实测 60 s 素材 verify 16.5 s（外推 80 min/4808 窗约
    # 22 min/轨），而 latest-start 走向量化整除分支（同样 60 s 约 0.1 s）。
    # 选 latest-start = 保留内置逐样本验证能力，输出与 nearest-centre 逐位相同。
    "tv_coordinate": "latest-start",
    "tv_grid_hop_seconds": 1.0,
    "tv_fallback_hop_seconds": 10.0,
    "tv_search_ms": 250.0,
    "tv_max_shift_samples": 4800,
    # TV 路径的施加引擎：reference = 研究 `interp_write` 分数插值（交付物口径，
    # 2026-10-31 用户决策"完整实现参考算法"）；builtin = 内置 audio_sync_tv
    # 整数执行器（保留 fallback / 对照用）。
    "tv_apply_engine": "reference",
    # 曲线构造（2026-10-31 起 = 研究 round-3 `drift_rebuild` 完整口径）:
    #   2 min |CC| 加权中位锚点 -> 7 锚点(14 min)中值滤波去孤立跳变
    #   -> 每锚点限幅 30 采样(250 ppm) -> 线性插值。
    # `plot_drift2.py::anchors_from` 是参考实现；round-2 的 apply_tv 口径
    # （无限幅/120 限幅、无中值滤波）只保留在 `curve_stabilize=False` 分支。
    "curve_anchor_seconds": 120.0,
    "curve_stabilize": True,
    "curve_stabilize_median_anchors": 7,
    "curve_jump_limit_samples": 30.0,
    # 自适应限幅: 参考限幅 30 采样/2 min 隐含"真实漂移 ≤250 ppm"。合成/其它
    # 素材若有更大的**真实**漂移（如 0.5 采样/s = 10.4 ppm 的线性漂移，
    # 2 min 走 60 采样），绝对 30 会把人造阶梯当真实漂移压掉。检测到稳健
    # 斜率超过限幅时把限幅放大到 1.5×实际漂移（真实 80 min 素材的漂移只有
    # ~2 ppm，实际漂移 2 min ≤12 采样 < 30，因此不受影响、与参考逐位一致）。
    "curve_jump_clamp_adaptive": True,
    # 建曲线/锚点的最低 |CC|：研究 apply_tv.build_curve 用 0.08（accepted and
    # cc >= 0.08）。安静段落追踪器会一致抖动（cc 0.004–0.09），必须丢弃这些
    # 窗，否则 2 min 加权中位锚点被垃圾窗带偏（C1305 实测 65–70 min 差 260 采样）。
    "curve_min_cc": 0.08,
    # 第二轮残差（F）: 最多 2 轮。口径来自研究 round-3 fine pass
    # （long-take-alignment-final.md §12：60 s 窗 / 20 s 跳、±80 采样搜索、
    # |CC|≥0.20、5 min 加权中位平滑、|r|≤100、离局部中位 >30 的块丢弃）。
    # **不能**用 5 min 块 + ±256 搜索：实测 C1305 的 5 min 块在音乐基底上
    # 会选到 ±200 采样的伪峰（块残差 −236/−220/−208…、CC 0.26–0.51），把
    # 正确曲线拉坏；60 s 窗 + 窄搜索 + 5 min 中位平滑后伪峰被中位淘汰。
    "residual_window_seconds": 60.0,
    "residual_hop_seconds": 20.0,
    "residual_search_samples": 80,
    "residual_min_cc": 0.20,
    "residual_smooth_span_seconds": 300.0,
    "residual_smooth_hop_seconds": 60.0,
    "residual_outlier_samples": 30.0,
    "residual_max_abs_samples": 100.0,
    "residual_min_blocks": 3,
    # 残差"可忽略"门：研究 fine pass 即使残差中位 <1 采样也会施加（K 的
    # `aligned_fine` 就是 pass1+r）；若按 1 采样丢弃，C1306 CH4（中位 0.87）
    # 会少一道 K 有的修正，实测音频层 p90 变差（12.5 → 22.5 采样）。
    # 取 0.25 保留"有证据就施加"，同时仍能过滤纯噪声。
    "residual_min_median_samples": 0.25,
    "max_rounds": 2,
    # 极性（E）: 60 s 窗 / 30 s 跳, 零延迟 ±16 采样带符号峰
    "polarity_window_seconds": 60.0,
    "polarity_hop_seconds": 30.0,
    "polarity_peak_radius_samples": 16,
    "polarity_flip_negative_ratio": 0.8,
    "polarity_min_abs_cc": 0.25,
    # stereo 组刚性确认门（用户决策 C；任务书 §4.2）
    "bind_min_abs_cc": 0.90,
    "bind_lag_span_samples": 5.0,
    "bind_same_sign_ratio": 0.95,
    # 绑定**候选对**的抽帧强度门：比 group_by_snr 的建组门(1.3)高，因为
    # C1305 上 1.3 会单链接把四轨并成一组（P4.12 已记录）。整文件实测
    # (2026-10-30, production_opts, 200 帧/对):
    #   C1305  真对 CH1↔CH2 2.094；CH3↔CH4 1.435；跨组 ≤1.143
    #   C1306  真对 CH1↔CH2 1.682；CH3↔CH4 1.578；跨组 ≤1.050
    # 原门 1.75 在 C1306 上漏绑（CH2 被当成独立 TV target）。取 1.5 覆盖
    # 两文件的真对；CH3↔CH4 也会成为候选，但四重刚性门（|CC|≥0.90、
    # lag span≤5、同号≥95%）会拒绝它（两文件实测 span 大）——候选多一次
    # 跟踪确认，不会误绑。跨组 max 1.14 → 仍有 0.36 裕量。
    "bind_candidate_snr": 1.5,
}

#: 时变路径的稳定原因码（报告/测试断言用，不是自由文案）。
REASON_NO_TARGETS = "no_time_varying_targets"
REASON_TRACK_FAILED = "track_failed"
REASON_CURVE_INVALID = "curve_invalid"
REASON_NO_IMPROVEMENT = "no_improvement"
REASON_APPLY_REFUSED = "apply_refused"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _chunk_samples(sample_rate: int, seconds: float) -> int:
    return max(1, int(round(seconds * sample_rate)))


def _raw_length(path: Path, storage_dtype: str) -> int:
    return int(Path(path).stat().st_size) // (4 if storage_dtype == "f32" else 8)


def _read_raw(path: Path, storage_dtype: str, start: int, count: int) -> np.ndarray:
    """有界读取 raw PCM 的 `[start, start+count)`；越界补零。"""
    item = 4 if storage_dtype == "f32" else 8
    dtype = np.dtype("<f4") if item == 4 else np.dtype("<f8")
    out = np.zeros(max(0, int(count)), dtype=dtype)
    lo = max(0, int(start))
    hi = min(_raw_length(path, storage_dtype), int(start) + int(count))
    if hi > lo:
        with open(path, "rb") as fh:
            fh.seek(lo * item)
            raw = fh.read((hi - lo) * item)
        out[lo - int(start): lo - int(start) + (hi - lo)] = np.frombuffer(
            raw, dtype=dtype)
    return out


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    vals, w = values[order], weights[order]
    cum = np.cumsum(w)
    if not float(cum[-1]) > 0:
        return float(np.median(vals))
    idx = int(np.searchsorted(cum, cum[-1] / 2.0))
    return float(vals[min(idx, len(vals) - 1)])


# ---------------------------------------------------------------------------
# 曲线构造（研究 apply_tv.build_curve 的**口径**，不是实现复制）
# ---------------------------------------------------------------------------
def _theil_sen_slope(x: np.ndarray, y: np.ndarray,
                     max_pairs: int = 20000) -> float:
    """稳健斜率（采样/单位 x）：参考 `plot_drift2.theil_sen` 的简化版。"""
    n = int(y.size)
    if n < 2:
        return 0.0
    if n * (n - 1) // 2 <= max_pairs:
        i, j = np.triu_indices(n, k=1)
    else:
        rng = np.random.default_rng(0)
        i = rng.integers(0, n, max_pairs)
        j = rng.integers(0, n, max_pairs)
    dt = x[j] - x[i]
    ok = np.abs(dt) > 1e-12
    if not ok.any():
        return 0.0
    return float(np.median((y[j][ok] - y[i][ok]) / dt[ok]))


def build_curve_from_windows(
    windows: Sequence[Mapping[str, Any]],
    *,
    n_samples: int,
    sample_rate: int,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """连续性窗测量记录 -> 1 s 网格曲线。

    口径（研究已验证，见任务书 §4.3/§4.4）:

    1. 只取 `valid=True` 且 **`confidence` ≥ `curve_min_cc`(0.08)** 的窗 —— 对应
       研究 `apply_tv.build_curve` 的 `accepted and cc >= 0.08`（K track 里的
       `cc` 就是本实现的 `confidence`；实测用 confidence 过滤后我们的 round1
       与 K pass1 逐位一致 0/0/0）。安静段落（C1305 62–80 min）追踪器与 K 轨迹
       **一致地抖动**（cc 0.004–0.09、lag 700–1560），不过滤时 2 min 加权中位
       锚点会被垃圾窗带偏（实测 r1 与 K pass1 在 65–70 min 差 260 采样）。
       **退化回退**：若 confidence 过滤后不足 2 窗（强重采样/线性伸缩的合成
       素材会出现 coarse |CC| 0.14 但 confidence 0.02），改用 `coarse_cc`
       再过滤一次——这只在"confidence 门把整条曲线饿死"时发生，真实素材不会
       触发，因此不改变与 K 的逐位对齐。
    2. 按 **2 min 分箱**，取 |CC| 加权中位 lag 作为锚点（单窗内容分支跳变
       被中位抑制）；
    3. 锚点间做**跳变限幅**: 与上一锚点差 >120 采样时按 ±120 截断
       （保留缓慢漂移，拒绝一次性的内容分支切换）；
    4. 线性插值到 **1 s 网格**（首尾保持边缘锚点）；
    5. |shift| > `tv_max_shift_samples`(4800) 一律判无效（硬门，不夹断）。
    """
    prof = dict(PROFILE if profile is None else profile)
    min_cc = float(prof["curve_min_cc"])

    def _cc_of(w: Mapping[str, Any]) -> float:
        return abs(float(w.get("confidence") or 0.0))

    def _coarse_of(w: Mapping[str, Any]) -> float:
        coarse = w.get("coarse_cc")
        if coarse is None or not math.isfinite(float(coarse)):
            return _cc_of(w)
        return abs(float(coarse))

    base = [
        w for w in windows
        if bool(w.get("valid")) and w.get("delay_samples") is not None
        and math.isfinite(float(w["delay_samples"]))
    ]
    rows = [w for w in base if _cc_of(w) >= min_cc]
    if len(rows) < 2:
        # 退化回退（见 docstring 第 1 点）：confidence 门把整条曲线饿死时，
        # 用粗相关 |CC| 重新过滤；真实素材上不会走到这里。
        rows = [w for w in base if _coarse_of(w) >= min_cc]
    if not rows:
        return {"ok": False, "reason": "no_valid_windows", "n_windows": 0}
    anchor_s = float(prof["curve_anchor_seconds"])
    bin_samples = anchor_s * sample_rate
    jump = float(prof["curve_jump_limit_samples"])
    bins: dict[int, list[Mapping[str, Any]]] = {}
    for w in rows:
        # 分箱与锚点时间都用**窗起点**、锚点时间 = 箱起点（研究 apply_tv
        # build_curve 口径）。曾改用"箱内窗中心均值"以消除线性漂移下的
        # 半箱偏差，但那是**偏离 K 成品口径**：实测 C1305 尾段各 5 min 段
        # 中位差 ±50–90 采样（斜率大处 = 斜率×60 s）。验收 oracle 是 K 成品，
        # 因此回到研究口径（偏差写进已知边界，不在实现里"修正"）。
        start = float(w.get("start_sample", 0))
        b = int(start // bin_samples)
        bins.setdefault(b, []).append(w)
    anchors: list[list[float]] = []
    for b in sorted(bins):
        rs = bins[b]
        vals = np.asarray([float(w["delay_samples"]) for w in rs], dtype=np.float64)
        weights = np.asarray(
            [max(float(w.get("confidence") or 0.0), 1e-6) for w in rs],
            dtype=np.float64)
        anchors.append([float(b) * anchor_s,
                        _weighted_median(vals, weights),
                        float(len(rs))])
    if not anchors:
        return {"ok": False, "reason": "too_few_anchors",
                "n_anchors": 0, "n_windows": len(rows)}
    if len(anchors) == 1:
        # 短素材 (整条不足 2 min): 一个锚点就是一条常量曲线 —— 这是"测量
        # 只有一段证据"的诚实表达, 不补造第二个锚点。
        single = float(anchors[0][1])
        if abs(single) > float(prof["tv_max_shift_samples"]):
            return {"ok": False, "reason": "shift_out_of_range",
                    "max_shift": abs(single)}
        grid = np.arange(0, int(n_samples), _chunk_samples(
            sample_rate, float(prof["tv_grid_hop_seconds"])),
            dtype=np.float64)
        return {
            "ok": True,
            "grid_t": [float(x) for x in grid],
            "grid_s": [single] * int(grid.size),
            "anchors": anchors,
            "max_shift": abs(single),
            "n_windows": len(rows),
            "n_anchors": 1,
            "grid_hop_samples": _chunk_samples(
                sample_rate, float(prof["tv_grid_hop_seconds"])),
        }
    ys = [a[1] for a in anchors]
    stabilize = bool(prof.get("curve_stabilize", True))
    if stabilize:
        # 参考 `plot_drift2.py::anchors_from`（round-3 drift_rebuild 口径）:
        # 1) 7 锚点（14 min）中值滤波 —— 去掉孤立的内容分支跳变；
        # 2) 每锚点限幅（30 采样 = 250 ppm）；真实漂移超过该限幅时按
        #    1.5×实测漂移自适应放大，避免把真实漂移压成阶梯（见 PROFILE）。
        win = int(prof.get("curve_stabilize_median_anchors", 7))
        if win >= 3 and len(ys) >= win:
            pad = np.pad(np.asarray(ys, dtype=np.float64), win // 2,
                         mode="edge")
            ys = [float(np.median(pad[k:k + win])) for k in range(len(ys))]
        lim = float(prof.get("curve_jump_limit_samples", 30.0))
        if bool(prof.get("curve_jump_clamp_adaptive", True)) and len(ys) >= 3:
            slope = _theil_sen_slope(np.arange(len(ys), dtype=np.float64),
                                     np.asarray(ys, dtype=np.float64))
            if math.isfinite(slope) and abs(slope) > lim:
                lim = float(math.ceil(1.5 * abs(slope)))
        for k in range(1, len(ys)):
            delta = ys[k] - ys[k - 1]
            if abs(delta) > lim:
                ys[k] = ys[k - 1] + math.copysign(lim, delta)
    else:
        for k in range(1, len(ys)):
            delta = ys[k] - ys[k - 1]
            if abs(delta) > jump:
                ys[k] = ys[k - 1] + math.copysign(jump, delta)
    for k, y in enumerate(ys):
        anchors[k][1] = y
    if max(abs(y) for y in ys) > float(prof["tv_max_shift_samples"]):
        return {"ok": False, "reason": "shift_out_of_range",
                "max_shift": max(abs(y) for y in ys)}
    t = np.asarray([a[0] for a in anchors], dtype=np.float64) * sample_rate
    y = np.asarray(ys, dtype=np.float64)
    grid = np.arange(0, int(n_samples), _chunk_samples(
        sample_rate, float(prof["tv_grid_hop_seconds"])), dtype=np.float64)
    if grid.size == 0:
        return {"ok": False, "reason": "empty_grid"}
    # 参考 `fine_delay.load_curves` 用 np.interp 在锚点间线性插值，
    # 边界自然 hold（首锚点前/末锚点后取最近锚点值）。1 s 网格与参考的
    # 10k 网格只差亚采样插值误差。
    s = np.interp(grid, t, y)
    if float(np.max(np.abs(s))) > float(prof["tv_max_shift_samples"]):
        return {"ok": False, "reason": "shift_out_of_range",
                "max_shift": float(np.max(np.abs(s)))}
    return {
        "ok": True,
        "grid_t": [float(x) for x in grid],
        "grid_s": [float(x) for x in s],
        "anchors": anchors,
        "max_shift": float(np.max(np.abs(s))),
        "n_windows": len(rows),
        "n_anchors": len(anchors),
        "grid_hop_samples": _chunk_samples(
            sample_rate, float(prof["tv_grid_hop_seconds"])),
        "stabilized": stabilize,
    }


def curve_records(
    curve: Mapping[str, Any],
    *,
    n_samples: int,
    hop_samples: int,
) -> list[dict[str, Any]]:
    """1 s 网格曲线 -> `measurements_from_records` 需要的逐窗记录。

    记录数必须等于 `required_window_count(n_samples, hop_samples)`；曲线网格
    不足时**尾部保持最后值**（研究口径：边缘 hold），不补零、不猜测。
    """
    need = audio_sync_tv.required_window_count(
        int(n_samples), hop_samples=int(hop_samples))
    grid_t = np.asarray(curve["grid_t"], dtype=np.float64)
    grid_s = np.asarray(curve["grid_s"], dtype=np.float64)
    out: list[dict[str, Any]] = []
    for i in range(need):
        target_t = float(i) * hop_samples
        if target_t <= grid_t[-1]:
            val = float(np.interp(target_t, grid_t, grid_s))
        else:
            val = float(grid_s[-1])
        out.append({"valid": True, "delay_samples": val, "confidence": 1.0})
    return out


# ---------------------------------------------------------------------------
# 施加（全部走内置 audio_sync_tv_exec）
# ---------------------------------------------------------------------------
def render_fractional_pass1(
    src_raw: Path,
    dst_raw: Path,
    curve: Mapping[str, Any],
    *,
    sample_rate: int,
    storage_dtype: str = "f32",
    profile: Mapping[str, Any] | None = None,
    chunk_samples: int = 2_000_000,
) -> Path:
    """把曲线以**分数插值**重建一版 pass-1 音频（只用于残差测量）。

    为什么需要它：round-2 残差口径与 K research `refine_curve` 同源；K 的
    pass-1 音频是 `interp_write`（逐样本 `np.interp`，分数移位）生成的，而
    生产 round-1 施加用内置 `audio_sync_tv_exec`（每 1 s 窗一个整数移位 =
    hold 阶梯）。在弱内容窗，两者的亚采样差会翻转相关峰分支，导致同一
    fine-pass 算法在"我们的整数 pass1"和"K 的分数 pass1"上测出不同残差
    （实测 C1305 曲线差 median 4.8–7 / p90 12–35）。
    这里按**同一曲线**用分数插值重建一份仅用于残差测量的 pass-1（不替换
    生产产物：最终施加仍走内置整数执行器），使残差测量条件与 K 一致。
    边界与 `interp_write` 相同：越界输出 0（不夹到边缘）。
    """
    prof = dict(PROFILE if profile is None else profile)
    n = _raw_length(Path(src_raw), storage_dtype)
    grid_t = np.asarray(curve["grid_t"], dtype=np.float64)
    grid_s = np.asarray(curve["grid_s"], dtype=np.float64)
    halo = int(float(prof["tv_max_shift_samples"])) + 4
    chunk = max(1, int(chunk_samples))
    with Path(dst_raw).open("wb") as fh:
        for k0 in range(0, max(1, n), chunk):
            k1 = min(n, k0 + chunk)
            if k1 <= k0:
                continue
            idx = np.arange(k0, k1, dtype=np.float64)
            pos = idx + np.interp(idx, grid_t, grid_s)
            lo = max(0, k0 - halo)
            hi = min(n, k1 + halo)
            vals = _read_raw(src_raw, storage_dtype, lo, hi - lo
                             ).astype(np.float64)
            base = np.arange(lo, hi, dtype=np.float64)
            out = np.interp(pos, base, vals)
            invalid = (pos < 0.0) | (pos > n - 1.0)
            out[invalid] = 0.0
            fh.write(out.astype(np.float32).tobytes())
    return Path(dst_raw)


def reference_interp_write(
    src_raw: Path,
    dst_raw: Path,
    curve: Mapping[str, Any],
    *,
    sample_rate: int,
    storage_dtype: str = "f32",
    profile: Mapping[str, Any] | None = None,
    grid_step: int = 10_000,
    chunk_samples: int = 5_000_000,
) -> dict[str, Any]:
    """参考实现的分数插值施加（研究 `fine_delay.interp_write` 逐行等价）。

    这是 K round-3 final 的真实施加方式：
      * 曲线在 **10,000 采样网格** 上采样后逐块线性插值（块 5,000,000 采样，
        块内 `np.interp(pos, arange(s0,s1), src[s0:s1])`）；
      * `pos = k + s(k)`，先 clamp 到 [0,n-1] 再插值，越界样本写 0；
      * 输出 float32。

    为什么要改：交付物就是用它生成的（`fine_delay.py`）。此前生产用内置
    `audio_sync_tv` 的 1 s hold 整数移位，虽然逐窗把曲线执行得"正确"，但
    (a) 分数移位与整数移位存在亚采样差，(b) round-3 的 drift_rebuild 曲线
    在跳变段被稳定化后仍需要线性插值才能与交付物逐样本一致。用户 2026-10-31
    决策"使用参考的算法完整实现"后，TV 路径的 pass-1/final 都用本函数。
    """
    prof = dict(PROFILE if profile is None else profile)
    n = _raw_length(Path(src_raw), storage_dtype)
    if n <= 0:
        return {"executed": False, "reason": "empty_source"}
    grid_t = np.asarray(curve["grid_t"], dtype=np.float64)
    grid_s = np.asarray(curve["grid_s"], dtype=np.float64)
    grid = np.arange(0, n, int(grid_step), dtype=np.float64)
    shift = np.interp(grid, grid_t, grid_s)
    chunk = max(1, int(chunk_samples))
    with Path(dst_raw).open("wb") as fh:
        for s0 in range(0, n, chunk):
            s1 = min(n, s0 + chunk)
            idx = np.arange(s0, s1, dtype=np.float64)
            vals = _read_raw(src_raw, storage_dtype, s0, s1 - s0
                             ).astype(np.float64)
            pos = idx + np.interp(idx, grid, shift)
            invalid = (pos < 0.0) | (pos > n - 1.0)
            np.clip(pos, 0.0, n - 1.0, out=pos)
            block = np.interp(pos, np.arange(s0, s1, dtype=np.float64), vals)
            block[invalid] = 0.0
            fh.write(block.astype(np.float32).tobytes())
    return {"executed": True, "engine": "reference_interp", "grid_step":
            int(grid_step), "n_samples": n,
            "output": str(dst_raw)}


def apply_curve(
    src_raw: Path,
    dst_raw: Path,
    curve: Mapping[str, Any],
    *,
    sample_rate: int,
    storage_dtype: str = "f32",
    profile: Mapping[str, Any] | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """把 1 s 网格曲线施加到一条 raw PCM 上（内置 plan/execute/验证）。

    1 s 跳被几何约束拒绝时退回 10 s 跳（同一曲线重新采样），并在结果里标
    `plan_geometry_fallback="hop=10s"`（任务书 §4.4.4）。

    策略固定 `hold`（见 `PROFILE["tv_policy"]` 的注释：内置 executor 拒绝
    offline-only 的 `linear`；1 s 稠密曲线无缺口，两者输出逐位相同）。
    """
    prof = dict(PROFILE if profile is None else profile)
    n_samples = _raw_length(Path(src_raw), storage_dtype)
    if n_samples <= 0:
        return {"executed": False, "reason": "empty_source"}
    grid_hop = _chunk_samples(sample_rate, float(prof["tv_grid_hop_seconds"]))
    # ⚠️ plan 的 window_samples 必须 == hop_samples（稠密 1 s 阶梯）。
    # 若沿用 30 s 窗 + 1 s 跳，nearest-centre 的窗中心 c(w)=w*hop+window/2
    # 会把前 15 s 全部映射到窗 0（实测漂移 2 采样/s 时造成 30 采样偏差）；
    # 稠密曲线本身是逐 1 s 的阶梯，window==hop 才让 join(k) 与曲线一一对应。
    search = int(round(float(prof["tv_search_ms"]) * sample_rate / 1000.0))
    geometry_fallback: str | None = None
    last: dict[str, Any] = {}
    for hop, label in ((grid_hop, None),
                       (_chunk_samples(sample_rate,
                                       float(prof["tv_fallback_hop_seconds"])),
                        "hop=10s")):
        if hop < grid_hop:
            continue
        if geometry_fallback is not None and label is None:
            continue
        request = audio_sync_tv_exec.request_time_varying(
            enabled=True,
            policy=str(prof["tv_policy"]),
            coordinate=str(prof["tv_coordinate"]),
            window_samples=hop,
            hop_samples=hop,
            search_samples=search,
            chunk_samples=hop,
            require_evidence_gate=False,
        )
        records = curve_records(curve, n_samples=n_samples, hop_samples=hop)
        # 内置 `linear` 是**离线**策略: 接口要求显式 GapContext（缺口两端
        # 端点值）。我们的曲线在 1 s 网格上是稠密的（无缺口），端点值按
        # "首/末窗的实测移位"显式给出 —— 不是猜测: 稠密 mask 下 filler 不会
        # 使用它们，只是接口契约要求"缺口端点必须由调用方声明"。
        vals = [int(round(float(r["delay_samples"]))) for r in records]
        ctx = audio_sync_tv.GapContext(
            stream_has_trust=bool(vals),
            gap_endpoints=(vals[0], vals[-1]) if vals else None,
            gap_extent=(0, len(records)),
            stream_end=len(records),
        )
        res = audio_sync_tv_exec.run_time_varying(
            Path(src_raw), Path(dst_raw), request=request,
            measurements=records,
            context=(ctx if audio_sync_tv.GAP_POLICIES.get(
                str(prof["tv_policy"])) else None),
            storage_dtype=storage_dtype, verify=verify,
        )
        last = {
            "executed": bool(res.executed),
            "reason": res.reason,
            "detail": res.detail,
            "n_windows": int(res.n_windows),
            "state_counts": dict(res.state_counts or {}),
            "verification": res.verification,
            "evidence": res.evidence.get("coverage_fraction")
            if isinstance(res.evidence, dict) else None,
        }
        if res.executed:
            last["hop_samples"] = hop
            if geometry_fallback:
                last["plan_geometry_fallback"] = geometry_fallback
            return last
        if res.reason in ("tv_plan_invalid", "tv_evidence_insufficient"):
            geometry_fallback = label or "hop=1s"
            continue
        return last
    return last


# ---------------------------------------------------------------------------
# 第二轮残差测量（5 min 块 / 50 % 重叠）
# ---------------------------------------------------------------------------
def _abs_peak_delay(
    ref: np.ndarray,
    tgt: np.ndarray,
    *,
    sample_rate: int,
    search: int,
    coarse_rate: int = 12000,
    refine_radius: int = 8,
    refine_segment_seconds: float = 2.0,
) -> tuple[float, float]:
    """极性无关的 |CC| 峰 lag + 全速率局部精化；返回 `(lag, |cc|)`。

    为什么不能直接用 `gcc_phat_int`: 它的极性翻转启发式要求负峰比正峰强
    **1.2 倍**才采信负峰；实测 CH3/CH4 反相内容上真（负）峰只比伪正峰强
    ~1.1 倍，于是残差测量会选到 +125 采样的错误分支并把错误写进 round-2
    （2026-10-29 真实片段实测：追踪曲线正确、round-2 却加了 +123）。
    这里对 |CC| 取峰（极性只影响符号、不改变 lag），与 S1 跟踪器同一口径。
    """
    q = max(1, int(round(sample_rate / float(coarse_rate))))
    # 盒式低通 + 抽取（不是裸 stride）：反相/白噪内容上裸抽取会让"非 q 整数倍"
    # 的时差变成不相干采样（合成测试实测残差块全部 < min_cc）。盒式平均保留
    # 相关峰，粗 lag 分辨率 q 采样由后面的全速率精化补回。
    x = np.asarray(ref, dtype=np.float64)
    y = np.asarray(tgt, dtype=np.float64)
    if q > 1:
        nx = (x.size // q) * q
        ny = (y.size // q) * q
        a = x[:nx].reshape(-1, q).mean(axis=1)
        b = y[:ny].reshape(-1, q).mean(axis=1)
    else:
        a, b = x, y
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    a0 = a - a.mean()
    b0 = b - b.mean()
    na = float(np.linalg.norm(a0))
    nb = float(np.linalg.norm(b0))
    if na <= 1e-12 or nb <= 1e-12 or n < 4:
        return 0.0, 0.0
    r = int(np.ceil(search / q)) + 1
    best_lag = 0.0
    best_cc = 0.0
    for lag in range(-r, r + 1):
        if lag >= 0:
            aa, bb = a0[: n - lag], b0[lag:]
        else:
            aa, bb = a0[-lag:], b0[: n + lag]
        if aa.size < 4:
            continue
        den = na * nb
        if den <= 1e-12:
            continue
        cc = abs(float(np.dot(aa, bb) / den))
        if cc > best_cc:
            best_cc, best_lag = cc, float(lag) * q
    # 全速率精化：围绕粗 lag 用中心 2 s 段逐整数 lag 取 |CC| 最大
    seg = min(len(ref), max(1, int(round(refine_segment_seconds * sample_rate))))
    c0 = max(0, (len(ref) - seg) // 2)
    av = np.asarray(ref[c0: c0 + seg], dtype=np.float64)
    av = av - av.mean()
    na_seg = float(np.linalg.norm(av))
    best = (best_lag, best_cc)
    if na_seg > 1e-12:
        for lag in range(int(round(best_lag)) - refine_radius,
                         int(round(best_lag)) + refine_radius + 1):
            s0 = c0 + lag
            if s0 < 0 or s0 + seg > len(tgt):
                continue
            bv = np.asarray(tgt[s0: s0 + seg], dtype=np.float64)
            bv = bv - bv.mean()
            den = na_seg * float(np.linalg.norm(bv))
            if den <= 1e-12:
                continue
            cc = abs(float(np.dot(av, bv) / den))
            if cc > best[1]:
                best = (float(lag), cc)
    return float(best[0]), float(best[1])


def measure_residual_curve(
    anchor_raw: Path,
    target_raw: Path,
    *,
    sample_rate: int,
    storage_dtype: str = "f32",
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """在 round-1 输出上测残差（target vs anchor），返回 1 s 网格残差曲线。

    口径 = 研究 round-3 fine pass（`long-take-alignment-final.md` §12，
    与 `tests/regression/long_align_tools/fine_delay.py` 同一算法）：

    1. 60 s 窗 / 20 s 跳；12 kHz 粗扫（`resample_poly` 1/4）+ FFT 相关峰，
       搜索 `±search48` 采样（默认 ±80），coarse = -peak*DEC；
    2. 48 kHz 精化：窗中心 2 s 段在 coarse ±8 采样内逐整数点积取 |CC| 最大；
    3. 只保留 |CC| ≥ 0.20 的窗；离**局部中位**（±2 窗）>30 采样的窗丢弃；
    4. 5 min 滑动**中位**平滑（1 min 跳，窗口覆盖 t0±300 s），插值到 1 s
       网格，`|r| ≤ 100` 硬门；
    5. 有效窗 <3 / 平滑后残差中位 <1 采样 → `ok=False`（不把噪声写回）。

    为什么不用 5 min 长块 + 宽搜索：实测 C1305 上会选到 ±200 采样的伪峰
    （块残差 −236/−220/−208，CC 仅 0.26–0.51），把正确曲线拉坏。
    为什么用 `resample_poly` 而不是盒式抽取：这是研究 fine pass 的实现，
    与被比较的 K `refine_curve` JSON 同源；盒式抽取在非整数倍 coarse 网格
    上对弱内容窗会翻转相关峰，实测与 K refine 的差更大。
    """
    from scipy import signal

    prof = dict(PROFILE if profile is None else profile)
    n = min(_raw_length(Path(anchor_raw), storage_dtype),
            _raw_length(Path(target_raw), storage_dtype))
    win = _chunk_samples(sample_rate, float(prof["residual_window_seconds"]))
    hop = _chunk_samples(sample_rate, float(prof["residual_hop_seconds"]))
    search48 = int(prof["residual_search_samples"])
    min_cc = float(prof["residual_min_cc"])
    dec = max(1, int(round(sample_rate / 12000.0)))
    seg_samples = _chunk_samples(sample_rate, 2.0)
    starts = [
        s for s in range(0, max(1, n - win), hop)
        if min(win, n - s) >= win // 2
    ]

    def measure_one(s: int) -> dict[str, Any] | None:
        length = min(win, n - s)
        ref = _read_raw(anchor_raw, storage_dtype, s, length).astype(np.float64)
        tgt = _read_raw(target_raw, storage_dtype, s, length).astype(np.float64)
        if float(np.sqrt(np.mean(ref * ref))) < 1e-6 \
                or float(np.sqrt(np.mean(tgt * tgt))) < 1e-6:
            return None
        a12 = signal.resample_poly(ref, 1, dec).astype(np.float64)
        b12 = signal.resample_poly(tgt, 1, dec).astype(np.float64)
        n12 = min(len(a12), len(b12))
        if n12 < 16:
            return None
        nfft = int(1 << int(np.ceil(np.log2(2 * n12))))
        A = np.fft.rfft(a12[:n12] - a12[:n12].mean(), nfft)
        B = np.fft.rfft(b12[:n12] - b12[:n12].mean(), nfft)
        surf = np.fft.irfft(A * np.conj(B), nfft)
        den = (float(np.linalg.norm(a12[:n12]))
               * float(np.linalg.norm(b12[:n12])) + 1e-12)
        radius12 = max(4, search48 // dec)
        search = radius12 + 2
        cand = np.abs(np.concatenate(
            [surf[:search + 1], surf[-search:]]) / den)
        m = np.concatenate([np.arange(0, search + 1),
                            np.arange(-search, 0)]).astype(np.int64)
        i = int(np.argmax(cand))
        coarse = -float(m[i]) * dec
        c0 = len(ref) // 2 - seg_samples // 2
        av = ref[c0:c0 + seg_samples] - ref[c0:c0 + seg_samples].mean()
        best = (coarse, 0.0)
        for mm in range(int(coarse) - 8, int(coarse) + 9):
            b0 = c0 + mm
            if b0 < 0 or b0 + seg_samples > len(tgt):
                continue
            bv = tgt[b0:b0 + seg_samples] - tgt[b0:b0 + seg_samples].mean()
            dn = float(np.linalg.norm(av)) * float(np.linalg.norm(bv))
            if dn < 1e-9:
                continue
            val = float(np.dot(av, bv) / dn)
            if abs(val) > abs(best[1]):
                best = (float(mm), val)
        cc = abs(float(best[1]))
        if cc < min_cc:
            return None
        return {
            "start_s": s / float(sample_rate),
            "center_s": (s + length / 2.0) / float(sample_rate),
            "residual": float(best[0]),
            "cc": float(cc),
        }

    # S5: 残差窗互相独立，按窗并行；归并按 starts 顺序，结果与串行逐位一致。
    workers = min(4, max(1, (os.cpu_count() or 4)))
    if len(starts) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            measured = list(ex.map(measure_one, starts))
        rows = [m for m in measured if m is not None]
    else:
        rows = [m for m in (measure_one(s) for s in starts) if m is not None]
    n_windows = len(starts)
    if len(rows) < int(prof["residual_min_blocks"]):
        return {"ok": False, "reason": "too_few_blocks",
                "n_blocks": len(rows), "n_windows": n_windows}
    outlier = float(prof["residual_outlier_samples"])
    max_abs = float(prof["residual_max_abs_samples"])
    span = float(prof["residual_smooth_span_seconds"])
    smooth_hop = float(prof.get(
        "residual_smooth_hop_seconds",
        float(prof["residual_hop_seconds"])))

    # 离群: 偏离局部中位（±2 窗）> 30 采样
    t = np.asarray([r["start_s"] for r in rows], dtype=np.float64)
    y = np.asarray([r["residual"] for r in rows], dtype=np.float64)
    keep = np.ones(y.size, dtype=bool)
    for k in range(y.size):
        lo, hi = max(0, k - 2), min(y.size, k + 3)
        if abs(y[k] - float(np.median(y[lo:hi]))) > outlier:
            keep[k] = False
    if int(keep.sum()) >= 5:
        t, y = t[keep], y[keep]

    # 5 min 滑动中位（1 min 跳）→ 1 s 网格
    ts: list[float] = []
    ys: list[float] = []
    t0 = 0.0
    dur = n / float(sample_rate)
    while t0 < dur:
        m = np.abs(t - (t0 + span / 2.0)) <= span
        if int(m.sum()) >= 3:
            ts.append(t0 + span / 2.0)
            ys.append(float(np.median(y[m])))
        t0 += smooth_hop
    if len(ts) < 2:
        # 短素材（平滑窗覆盖不到两个采样点）: 直接对窗口残差插值，
        # 让合成/短片段检查仍能验证本函数；真实 80 min 素材不会走这里。
        ts, ys = list(t), list(y)
    if len(ts) < 2:
        return {"ok": False, "reason": "too_few_blocks",
                "n_blocks": len(rows), "n_windows": n_windows}
    grid_s = _chunk_samples(sample_rate, float(prof["tv_grid_hop_seconds"]))
    grid = np.arange(0, n, grid_s, dtype=np.float64)
    curve = np.interp(grid, np.asarray(ts) * float(sample_rate),
                      np.asarray(ys))
    curve = np.clip(curve, -max_abs, max_abs)
    median_abs = float(np.median(np.abs(curve)))
    if median_abs < float(prof["residual_min_median_samples"]):
        return {"ok": False, "reason": "residual_negligible",
                "n_blocks": len(rows), "n_windows": n_windows,
                "median_abs": median_abs}
    smooth_at_blocks = np.interp(
        np.asarray([r["start_s"] for r in rows], dtype=np.float64),
        grid / float(sample_rate), curve)
    kept = sum(1 for r, v in zip(rows, smooth_at_blocks)
               if abs(r["residual"] - v) <= outlier)
    return {
        "ok": True,
        "grid_t": [float(x) for x in grid],
        "grid_s": [float(x) for x in curve],
        "blocks": rows,
        "n_blocks": int(kept),
        "n_windows": n_windows,
        "median_abs": median_abs,
        "max_abs": float(np.max(np.abs(curve))),
        "discarded": int(len(rows) - kept),
    }


def add_curves(base: Mapping[str, Any],
               residual: Mapping[str, Any]) -> dict[str, Any]:
    """`s_total(t) = s_round1(t) + s_residual(t)`（同一 1 s 网格），带硬门。"""
    grid_t = np.asarray(base["grid_t"], dtype=np.float64)
    grid_s = np.asarray(base["grid_s"], dtype=np.float64)
    rt = np.asarray(residual["grid_t"], dtype=np.float64)
    rs = np.asarray(residual["grid_s"], dtype=np.float64)
    if rt.size and (rt[-1] > grid_t[-1] or rt[0] < grid_t[0]):
        rs = np.interp(grid_t, rt, rs)
    elif rs.size != grid_s.size:
        rs = np.interp(grid_t, rt, rs)
    total = grid_s + rs
    max_shift = float(np.max(np.abs(total)))
    if max_shift > float(PROFILE["tv_max_shift_samples"]):
        return {"ok": False, "reason": "shift_out_of_range",
                "max_shift": max_shift}
    return {
        "ok": True,
        "grid_t": [float(x) for x in grid_t],
        "grid_s": [float(x) for x in total],
        "max_shift": max_shift,
    }


# ---------------------------------------------------------------------------
# 极性（默认自动，组级一起翻）
# ---------------------------------------------------------------------------
def detect_polarity(
    anchor_raw: Path,
    target_raw: Path,
    *,
    sample_rate: int,
    storage_dtype: str = "f32",
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """零延迟附近带符号相关峰的极性投票（任务书 §4.6）。

    60 s 窗 / 30 s 跳，每窗在 ±16 采样内取 |CC| 最大峰并保留符号；负票比例
    ≥0.8 且中位 |CC| ≥0.25 → 建议翻转。返回 `{votes, negative_ratio,
    median_abs_cc, flip, consistent}`。
    """
    prof = dict(PROFILE if profile is None else profile)
    n = min(_raw_length(Path(anchor_raw), storage_dtype),
            _raw_length(Path(target_raw), storage_dtype))
    win = _chunk_samples(sample_rate, float(prof["polarity_window_seconds"]))
    hop = _chunk_samples(sample_rate, float(prof["polarity_hop_seconds"]))
    radius = int(prof["polarity_peak_radius_samples"])
    # 每窗用中心 2 s 段做带符号相关（与生产 min_confidence 的短段口径一致）
    seg = _chunk_samples(sample_rate, 2.0)
    votes = 0
    negative = 0
    ccs: list[float] = []
    for s in range(0, max(1, n - win), hop):
        c0 = s + win // 2 - seg // 2
        a = _read_raw(anchor_raw, storage_dtype, c0, seg).astype(np.float64)
        best: tuple[float, float] | None = None
        for lag in range(-radius, radius + 1):
            b = _read_raw(target_raw, storage_dtype, c0 + lag,
                         seg).astype(np.float64)
            a0 = a - a.mean()
            b0 = b - b.mean()
            den = (float(np.linalg.norm(a0)) * float(np.linalg.norm(b0)))
            if den <= 1e-12:
                continue
            cc = float(np.dot(a0, b0) / den)
            if best is None or abs(cc) > abs(best[1]):
                best = (float(lag), cc)
        if best is None:
            continue
        lag, cc = best
        if abs(cc) < float(prof["polarity_min_abs_cc"]):
            continue
        votes += 1
        ccs.append(abs(cc))
        if cc < 0:
            negative += 1
    negative_ratio = (negative / votes) if votes else 0.0
    median_abs = float(np.median(ccs)) if ccs else 0.0
    flip = (
        votes > 0
        and negative_ratio >= float(prof["polarity_flip_negative_ratio"])
        and median_abs >= float(prof["polarity_min_abs_cc"])
    )
    return {
        "votes": votes,
        "negative": negative,
        "negative_ratio": negative_ratio,
        "median_abs_cc": median_abs,
        "flip": bool(flip),
    }


# ---------------------------------------------------------------------------
# 施加辅助：曲线 -> 目标 raw；极性翻转；曲线叠加已在上方
# ---------------------------------------------------------------------------
def _invert_raw(src: Path, dst: Path, *, storage_dtype: str,
                chunk_samples: int = 4_800_000) -> int:
    item = 4 if storage_dtype == "f32" else 8
    dtype = np.dtype("<f4") if item == 4 else np.dtype("<f8")
    n = _raw_length(src, storage_dtype)
    written = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            raw = fi.read(chunk_samples * item)
            if not raw:
                break
            block = -np.frombuffer(raw, dtype=dtype)
            fo.write(block.tobytes())
            written += block.size
    return written


def _convert_storage(src: Path, src_dtype: str, dst_dtype: str, dst: Path,
                     chunk_samples: int = 4_800_000) -> int:
    """raw PCM 精度转换（仅当同一文件内各声道解码精度不一致时使用）。"""
    if src_dtype == dst_dtype:
        return 0
    s_item = 4 if src_dtype == "f32" else 8
    d_item = 4 if dst_dtype == "f32" else 8
    s_dtype = np.dtype("<f4") if s_item == 4 else np.dtype("<f8")
    d_dtype = np.dtype("<f4") if d_item == 4 else np.dtype("<f8")
    n = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            raw = fi.read(chunk_samples * s_item)
            if not raw:
                break
            block = np.frombuffer(raw, dtype=s_dtype).astype(d_dtype)
            fo.write(block.tobytes())
            n += block.size
    return n


# ---------------------------------------------------------------------------
# 顶层编排
# ---------------------------------------------------------------------------
def align_time_varying(
    *,
    work_dir: Path,
    anchor: int,
    decoded: Sequence[Path],
    storages: Sequence[str],
    sample_rate: int,
    groups: Sequence[Sequence[int]] = (),
    profile: Mapping[str, Any] | None = None,
    log: Callable[[str], None] = print,
    measure: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对全文件做时变对齐（恒定优先失败后的**整文件**后备）。

    参数
    ----
    work_dir : 中间产物目录（`long_take/` 下）
    anchor   : 主锚点声道下标（默认 CH1=0；由 channel_sync 的锚点回退决定）
    decoded  : 每个声道已解码的 raw PCM 路径（f32/f64，按 storages）
    storages : 每个声道的存储精度 ("f32"/"f64")
    groups   : stereo 组（每组按**同一曲线**施加；组内代表 = 最小下标）
    measure  : 窗测量函数，缺省 `sync_estimate.measure_window_delays`

    返回 dict：`applied`（是否成功）、`reason`（失败时稳定码）、
    `targets`（逐目标曲线/残差/极性/施加统计）、`fixed_raw`（成功时每声道
    最终 raw 路径）、`flipped`、`rounds`、`timings`、`max_shift`。
    """
    prof = dict(PROFILE if profile is None else profile)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    measure = measure or sync_estimate.measure_window_delays
    t_all = time.time()
    timings: dict[str, float] = {}
    storages = [str(s) for s in storages]
    decoded = [Path(p) for p in decoded]
    n_chan = len(decoded)
    if not (0 <= int(anchor) < n_chan):
        return {"applied": False, "reason": REASON_TRACK_FAILED,
                "detail": f"anchor {anchor} out of range"}
    # 测量/施加原语要求同一次测量里两条流精度一致 (`storage_dtype` 是单参数)。
    # 生产解码按源 codec 决定 f32/f64; 同一文件内混用 (如 s32 + s16) 时统一到
    # 共同精度 (全 f64 才用 f64, 否则 f32 —— PCM 24bit 内容 f32 足够)。
    storage_common = "f64" if all(s == "f64" for s in storages) else "f32"
    prepared_inputs: list[Path] = []
    if any(s != storage_common for s in storages):
        converted: list[Path] = []
        for i, s in enumerate(list(storages)):
            if s == storage_common:
                converted.append(decoded[i])
                continue
            dst = work_dir / f"tv_input_{i}.{storage_common}"
            _convert_storage(decoded[i], s, storage_common, dst)
            prepared_inputs.append(dst)
            converted.append(dst)
            log(f"long-take: CH{i + 1} {s} -> {storage_common} "
                "（同文件各声道精度统一）")
        decoded = converted
        storages = [storage_common] * n_chan

    def _cleanup() -> None:
        for p in prepared_inputs:
            try:
                Path(p).unlink()
            except OSError:
                pass
        prepared_inputs.clear()

    # 组：只接受"两两不重叠、恰好 2 成员"的组（任务书 §4.2.6）；其余独立。
    # 含锚点的组 = 与锚点刚性绑定的 stereo 组: 组内相对偏移是**源内容**,
    # 整组跟随锚点移动 (锚点自身不移位 -> 该组不产生修正)。
    seen: set[int] = set()
    group_of: dict[int, int] = {}
    followed_anchor: list[int] = []
    units: list[list[int]] = []
    for g in groups:
        members = [int(m) for m in g]
        if len(members) != 2 or any(m in seen for m in members):
            log(f"long-take: stereo group {g} ignored "
                "(需恰好 2 成员且不重叠)")
            continue
        seen.update(members)
        if anchor in members:
            followed_anchor.extend(m for m in members if m != anchor)
            continue
        for m in members:
            group_of[m] = members[0]
        units.append(members)
    for i in range(n_chan):
        if i == anchor or i in seen:
            continue
        units.append([i])

    results: dict[str, Any] = {}
    final_raw: dict[int, Path] = {}
    flipped: list[int] = []
    any_target = False
    t1 = time.time()
    for members in units:
        rep = members[0]
        any_target = True
        tgt_dir = work_dir / f"target_{rep}"
        tgt_dir.mkdir(parents=True, exist_ok=True)
        # 1) 窗级连续性测量（组代表 vs 锚点）
        try:
            windows = measure(
                decoded[anchor], decoded[rep],
                sample_rate=sample_rate,
                window_seconds=float(prof["track_window_seconds"]),
                hop_seconds=float(prof["track_hop_seconds"]),
                coarse_rate=int(prof["track_coarse_rate"]),
                min_cc=float(prof["track_min_cc"]),
                storage_dtype=storage_common,
            )
        except Exception as exc:                       # noqa: BLE001
            log(f"long-take: CH{rep + 1} window measurement failed: "
                f"{type(exc).__name__}: {exc}")
            results[str(rep)] = {"ok": False, "reason": REASON_TRACK_FAILED,
                                 "detail": f"{type(exc).__name__}: {exc}"}
            continue
        n_samples = _raw_length(decoded[rep], storages[rep])
        curve = build_curve_from_windows(
            windows.get("windows", []), n_samples=n_samples,
            sample_rate=sample_rate, profile=prof)
        if not curve.get("ok"):
            log(f"long-take: CH{rep + 1} curve invalid: {curve.get('reason')}")
            results[str(rep)] = {"ok": False, "reason": REASON_CURVE_INVALID,
                                 "curve": curve}
            continue
        log(f"long-take: CH{rep + 1} curve ok — windows "
            f"{curve['n_windows']}, anchors {curve['n_anchors']}, "
            f"max|shift| {curve['max_shift']:.1f} samples")
        curve_round1 = {
            "grid_t": curve.get("grid_t"),
            "grid_s": curve.get("grid_s"),
        }

        # 2) round-1 施加（每个成员用同一曲线；组内 delta 是源内容不动）
        #
        # 用户决策（2026-10-31）：TV 路径完整采用参考实现 —— 分数插值
        # （reference_interp_write，研究 interp_write 等价），不再用内置
        # 整数执行器；内置 apply_curve 保留作 fallback（异常时退回并在
        # 报告里标 engine=builtin）。
        rounds = 0
        applied: dict[int, Path] = {}
        apply_engine = str(prof.get("tv_apply_engine", "reference"))
        for m in members:
            dst = tgt_dir / f"round1_{m}.{storages[m]}"
            stats: dict[str, Any]
            if apply_engine == "reference":
                try:
                    stats = reference_interp_write(
                        decoded[m], dst, curve, sample_rate=sample_rate,
                        storage_dtype=storages[m], profile=prof)
                except Exception as exc:               # noqa: BLE001
                    log(f"long-take: CH{m + 1} reference apply failed "
                        f"({type(exc).__name__}: {exc}) — 退回内置执行器")
                    stats = apply_curve(
                        decoded[m], dst, curve, sample_rate=sample_rate,
                        storage_dtype=storages[m], profile=prof)
            else:
                stats = apply_curve(
                    decoded[m], dst, curve, sample_rate=sample_rate,
                    storage_dtype=storages[m], profile=prof)
            if not stats.get("executed"):
                log(f"long-take: CH{m + 1} round-1 apply refused: "
                    f"{stats.get('reason')} {stats.get('detail')}")
                break
            applied[m] = dst
        if len(applied) != len(members):
            results[str(rep)] = {"ok": False, "reason": REASON_APPLY_REFUSED,
                                 "apply": stats}
            continue
        rounds = 1

        # 3) 第二轮残差（只在代表上测，s_total 同施加到全组）
        #
        # round-1 输出已经是**分数插值 pass1**（与 K fine_delay 的 pass1 同
        # 条件），残差直接在它上面测；final 再对 pass1 做一次分数插值
        # （参考实现的双重插值），而不是把总曲线一次施加到源上。
        residual_stats: dict[str, Any] = {"used": False}
        if int(prof["max_rounds"]) > 1:
            res = measure_residual_curve(
                decoded[anchor], applied[rep], sample_rate=sample_rate,
                storage_dtype=storages[rep], profile=prof)
            residual_stats = dict(res)
            if res.get("ok"):
                total = add_curves(curve, res)
                if total.get("ok"):
                    total["anchors"] = curve.get("anchors")
                    settled = {}
                    for m in members:
                        dst = tgt_dir / f"round2_{m}.{storages[m]}"
                        if apply_engine == "reference":
                            try:
                                stats2 = reference_interp_write(
                                    applied[m], dst, res,
                                    sample_rate=sample_rate,
                                    storage_dtype=storages[m], profile=prof)
                            except Exception as exc:   # noqa: BLE001
                                log(f"long-take: CH{m + 1} reference round-2 "
                                    f"failed ({type(exc).__name__}: {exc}) — "
                                    "退回内置执行器(总曲线)")
                                stats2 = apply_curve(
                                    decoded[m], dst, total,
                                    sample_rate=sample_rate,
                                    storage_dtype=storages[m], profile=prof)
                        else:
                            stats2 = apply_curve(
                                decoded[m], dst, total,
                                sample_rate=sample_rate,
                                storage_dtype=storages[m], profile=prof)
                        if stats2.get("executed"):
                            settled[m] = dst
                    if len(settled) == len(members):
                        applied = settled
                        curve = total
                        rounds = 2
                        residual_stats["used"] = True
                        log(f"long-take: CH{rep + 1} round-2 residual used "
                            f"(blocks {res.get('n_blocks')}, "
                            f"median |r| {res.get('median_abs'):.2f} samples)")
                    else:
                        log(f"long-take: CH{rep + 1} round-2 apply refused; "
                            "keeping round-1 (rounds=1)")
                else:
                    residual_stats["applied"] = False
            else:
                log(f"long-take: CH{rep + 1} residual skipped "
                    f"({res.get('reason')})")

        # 4) 极性（代表 vs 锚点，组级一起翻）
        pol = detect_polarity(
            decoded[anchor], applied[rep], sample_rate=sample_rate,
            storage_dtype=storages[rep], profile=prof)
        if pol.get("flip"):
            for m in members:
                inv = tgt_dir / f"polarity_{m}.{storages[m]}"
                _invert_raw(applied[m], inv, storage_dtype=storages[m])
                applied[m] = inv
                flipped.append(m)
            log(f"long-take: group CH{[m + 1 for m in members]} polarity "
                f"flipped (votes {pol['votes']}, neg "
                f"{pol['negative_ratio']:.0%}, |CC| "
                f"{pol['median_abs_cc']:.2f})")
        for m in members:
            final_raw[m] = applied[m]
        results[str(rep)] = {
            "ok": True,
            "members": [m + 1 for m in members],
            "rounds": rounds,
            "curve": {k: curve.get(k) for k in (
                "n_windows", "n_anchors", "max_shift", "grid_hop_samples")},
            "curve_anchors": curve.get("anchors"),
            # 完整施加曲线（验收/审计用；1 s 网格，~4800 点/轨）
            "curve_grid": (
                {"grid_t": curve.get("grid_t"),
                 "grid_s": curve.get("grid_s")}
                if curve.get("grid_t") else None),
            "curve_round1": curve_round1,
            "apply_engine": apply_engine,
            "residual": residual_stats,
            "polarity": pol,
        }
        timings[f"target_{rep}"] = round(time.time() - t1, 3)
        t1 = time.time()

    timings["total"] = round(time.time() - t_all, 3)
    ok = bool(units) and bool(any_target) and all(
        bool(v.get("ok")) for v in results.values()) and len(results) == len(units)
    if not ok:
        _cleanup()
        return {"applied": False, "reason": REASON_NO_IMPROVEMENT,
                "targets": results, "timings": timings,
                "followed_anchor": [m + 1 for m in followed_anchor],
                "detail": "not every target produced a verified curve"}
    max_shift = max(
        (float(v["curve"].get("max_shift") or 0.0)
         for v in results.values() if v.get("ok")), default=0.0)
    _cleanup()
    return {
        "applied": True,
        "reason": None,
        "targets": results,
        "fixed_raw": {str(k): str(v) for k, v in final_raw.items()},
        "storage": storage_common,
        "followed_anchor": [m + 1 for m in followed_anchor],
        "flipped": [m + 1 for m in flipped],
        "rounds": int(max((v.get("rounds", 1) for v in results.values()),
                          default=1)),
        "max_shift": max_shift,
        "timings": timings,
    }
