"""通道延迟估计 (P1 正式版, algo 2.3.0-p1): 两阶段 GCC-PHAT + 相位斜率精估.

职责:
  * estimate_pair(): 参考/目标两流 -> 分帧粗扫 (8kHz) + 全速率精测轨迹 +
    全局延迟 (合格帧中位数) + 锚段相位斜率加权 LS 精估
    (fine_delay_samples / fine_delay_ms / fine_fit_r2);
  * summarize_trajectory(): 帧轨迹统计 (MAD / 极差 / 相邻步长 / drift_ppm),
    供集成层恒定性门与报告诊断;
  * classify_constant(): constant / non_constant 判定 (MAD + 线性漂移 ppm),
    P1 只做二分 — step/ramp 细分属二期, 相邻步长仅作诊断警告;
  * boundary_pileup(): 合格帧落在搜索窗边缘的比例, 供 out_of_range 判定
    (真实时差超出搜索窗时帧测量堆积在窗边缘);
  * 底层件 open_source / read_f64 / gcc_phat_int / phase_slope_fine 公开,
    供 sync_fix 修后复检复用.

定位说明 (P1): 相位斜率精估只用于**测量稳定性、诊断读数与修后复检**,
不驱动任何分数 sinc 修正 — 默认修正路径是纯整数样本移位 (§6.1/§10)。
fine_delay_samples = round(全局延迟) + 相位斜率残余, 报告为诊断读数。

依赖: numpy + scipy (可选依赖, ImportError 由集成层统一捕获).

边界 (二期不做, 勿顺手实现): 锚点完整打分 / 轨迹三分类 (跳变/漂移细分) /
重采样修正 / 全对两两估计 / 分数 sinc 修正 / 设备延迟数据库。
单次 FFT <= 2^23 样本 (96kHz x 30s 锚段); 帧化 + memmap 分块读取,
禁止 np.fromfile() 全量加载长素材; 峰值内存与文件时长无关。

数据约定: delay > 0 = 目标轨比参考**晚到** (修正 = 整体前移 delay);
一切估计数学在 float64 下进行; raw 文件按存储精度 (f32/f64) memmap
流式读取; 全程确定性 (固定 FFT 尺寸 + 整数帧位, 无任何 RNG)。
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import fft, signal

__all__ = [
    "FrameDelay",
    "PairEstimate",
    "TrajectoryStats",
    "estimate_pair",
    "summarize_trajectory",
    "classify_constant",
    "boundary_pileup",
    "open_source",
    "read_f64",
    "gcc_phat_int",
    "phase_slope_fine",
]

# PHAT 正则化 (沿用 vendored 1.x 判据)
_EPS_REL = 1e-3
_EPS_ABS = 1e-12
# 分块 decimate 两侧余量 (coarse 样本): scipy decimate(ftype="fir") 默认
# numtaps = 20*q+1, 暂态 = 10 个 coarse 样本 < 16, 切走后余量内无暂态
# (已数值验证: 分块结果与全量 decimate bit 级一致)
_DECIMATE_MARGIN = 16
# 全速率帧搜索半窗 (样本): 粗扫 (8kHz) 量化误差 <= q/2 <= 6 (96k),
# ±8 覆盖粗延迟量化 + 峰位抖动, 把全速率搜索收窄到小区间
_FULL_SEARCH_HALF = 8
# 均方根门: 低于视为静音帧 (vendored 原值, 仅挡精确零; 抽取/滤波振铃
# 会把"静音"抬到 ~1e-3, 帧级静音由 frame_min_rms_dbfs 门拦截)
_RMS_GATE = 1e-12


# ---------------------------------------------------------------- 数据类


@dataclass
class FrameDelay:
    """单帧测量结果 (delay_samples = nan 表示该帧不可测, 帧仍保留在轨迹中)."""

    center_sample: int      # 帧中心在流中的样本位置
    delay_samples: float    # nan = 该帧不可测
    confidence: float


@dataclass
class PairEstimate:
    """(参考, 目标) 一对流的估计结果 (方向: delay > 0 = 目标晚到)."""

    ref: int
    tgt: int
    delay_samples: float      # 合格帧延迟中位数 (全局)
    delay_ms: float
    confidence: float         # 合格帧置信度中位数
    usable_frames: int
    frames: list[FrameDelay] = field(default_factory=list)  # 全帧轨迹 (含被否帧, 供二期分类器)
    fine_delay_samples: float = float("nan")  # 相位斜率精估 (整数部分 + 残余), 诊断读数
    fine_delay_ms: float = float("nan")       # 相位斜率精估 (ms), 诊断读数
    fine_fit_r2: float = float("nan")         # 相位拟合优度 (诊断, 不设门)
    polarity: int = 1         # +1 / -1 (仅报告, 不拒修)


@dataclass
class TrajectoryStats:
    """帧轨迹统计 (合格帧; nan 表示无合格帧可算)."""

    usable_frames: int
    mad_ms: float             # 合格帧延迟 MAD (ms)
    spread_samples: float     # 合格帧延迟极差 (样本)
    step_max_ms: float        # 相邻合格帧延迟差最大值 (ms, 仅诊断警告)
    drift_ppm: float          # 合格帧延迟对帧中心的线性拟合斜率 x1e6
    drift_total_ms: float = float("nan")   # 拟合在轨迹跨度上的预测漂移总量 (ms)


# ---------------------------------------------------------------- 输入读取


def open_source(src: Any, storage_dtype: str = "f32") -> np.ndarray:
    """打开 raw 源流: 路径 -> np.memmap (流式, 不全量载入); ndarray -> asarray。

    storage_dtype: "f32" | "f64" — 中间 raw 文件的存储精度 (见 §6.2 表:
    s32 源走 f64, 其余 f32)。ndarray 输入忽略该参数。
    """
    if isinstance(src, (str, Path)):
        dt = np.dtype("<f4") if storage_dtype == "f32" else np.dtype("<f8")
        return np.memmap(src, dtype=dt, mode="r")
    arr = np.asarray(src)
    if arr.ndim != 1:
        raise ValueError(f"expected 1-D mono source, got shape {arr.shape}")
    return arr


def read_f64(arr: np.ndarray, start: int, length: int) -> np.ndarray:
    """零填充读 [start, start+length), 越界补零, 返回 float64 副本."""
    out = np.zeros(length, dtype=np.float64)
    lo = max(0, start)
    hi = min(arr.shape[0], start + length)
    if hi > lo:
        out[lo - start: hi - start] = np.asarray(arr[lo:hi], dtype=np.float64)
    return out


def _coarse_slice(arr: np.ndarray, c_lo: int, c_hi: int, q: int) -> np.ndarray:
    """把流的 coarse 区间 [c_lo, c_hi) (单位 = 抽取后样本) 抽取到 8kHz。

    两侧带 _DECIMATE_MARGIN 余量抗滤波器暂态, 切走后返回定长
    (c_hi - c_lo) 的 float64; 流外区间补零 (zero_phase 映射 y[j] 精确对应
    输入 j*q, 已数值验证)。分块进行, 内存与文件时长无关。
    """
    n_in = arr.shape[0]
    in_lo = (c_lo - _DECIMATE_MARGIN) * q
    in_hi = (c_hi + _DECIMATE_MARGIN) * q
    src_lo = max(0, in_lo)
    src_hi = min(n_in, in_hi)
    pad_l = src_lo - in_lo          # in_lo < 0 时的左侧补零
    pad_r = in_hi - src_hi          # in_hi > n_in 时的右侧补零
    chunk = np.zeros(pad_l + max(0, src_hi - src_lo) + pad_r, dtype=np.float64)
    if src_hi > src_lo:
        chunk[pad_l: pad_l + (src_hi - src_lo)] = np.asarray(
            arr[src_lo:src_hi], dtype=np.float64
        )
    total = chunk.shape[0] // q
    seg = signal.decimate(chunk[: total * q], q, ftype="fir", zero_phase=True)
    # seg[j] <-> coarse 位置 (in_lo // q) + j; 目标区间起点 = c_lo
    base = in_lo // q
    a = c_lo - base                 # == _DECIMATE_MARGIN
    return seg[a: a + (c_hi - c_lo)]


# ---------------------------------------------------------------- GCC-PHAT


@dataclass
class _GccOut:
    delay_samples: float   # 整数延迟 (argmax, 无亚样本); nan = 失败
    confidence: float
    polarity: int
    success: bool


def gcc_phat_int(
    ref: np.ndarray,
    tgt: np.ndarray,
    *,
    lag0: int,
    search_lo: int,
    search_hi: int,
    rms_gate: float = _RMS_GATE,
) -> _GccOut:
    """帧级 GCC-PHAT (整数精度; 置信度合成沿用 vendored 1.x 公式)。

    ref: (F,) 参考帧; tgt: (L,) 目标窗口 (L >= F), tgt[0] 对应的流位置比
    ref[0] 早 lag0 样本 (lag0 = ref_start - tgt_start)。互相关面
    c[m] = sum_n ref[n] * tgt[n + m], delay = m - lag0, 搜索范围
    delay ∈ [search_lo, search_hi] (m ∈ [lag0+search_lo, lag0+search_hi]
    与 [0, L-F] 的交集, 恒为连续区间, 无回绕)。
    反相对的窗口按 vendored 1.2 规则翻转后照常取峰 (延迟仍正确)。
    rms_gate: 任一窗 RMS 低于该值直接判失败 (帧级静音/近静音拦截 —
    抽取滤波振铃会把真静音抬到 ~1e-3, 远高于精确零门)。
    """
    fail = _GccOut(float("nan"), 0.0, 1, False)
    F = ref.shape[0]
    L = tgt.shape[0]
    if F == 0 or L < F:
        return fail
    ref = ref - ref.mean()
    tgt = tgt - tgt.mean()
    r_ref = float(np.sqrt(np.mean(ref * ref)))
    r_tgt = float(np.sqrt(np.mean(tgt * tgt)))
    if r_ref < rms_gate or r_tgt < rms_gate:
        return fail
    ref /= r_ref
    tgt /= r_tgt

    nfft = fft.next_fast_len(F + L - 1, real=True)
    g = np.conj(fft.rfft(ref, nfft)) * fft.rfft(tgt, nfft)
    g /= np.abs(g) + _EPS_REL * float(np.max(np.abs(g))) + _EPS_ABS
    surface = fft.irfft(g, nfft)

    m_lo = max(0, lag0 + search_lo)
    m_hi = min(L - F, lag0 + search_hi)
    if m_lo > m_hi:
        return fail
    window = surface[m_lo: m_hi + 1]
    if window.size < 3:
        return fail

    # 反相感知: 负峰明显更强时翻转相关面 (沿用 vendored 1.2 规则)
    polarity = 1
    pos_best = float(np.max(window))
    neg_best = -float(np.min(window))
    if neg_best > pos_best * 1.2:
        polarity = -1
        window = -window

    padded = np.concatenate(([-np.inf], window, [-np.inf]))
    peaks, _ = signal.find_peaks(padded, distance=2)
    if peaks.size == 0:
        return _GccOut(float("nan"), 0.0, polarity, False)
    keep = [
        int(i) - 1
        for i in peaks
        if padded[i] > padded[i - 1] and padded[i] > padded[i + 1]
    ]
    if not keep:
        return _GccOut(float("nan"), 0.0, polarity, False)
    peaks = np.asarray(keep)
    with warnings.catch_warnings():
        # 等高峰 (周期信号) prominence=0 是合法情形, 不是问题
        warnings.filterwarnings(
            "ignore", message="some peaks have a prominence of 0"
        )
        prom = signal.peak_prominences(window, peaks)[0]
    order = np.argsort(window[peaks])[::-1]
    best = peaks[order[0]]
    second = (
        peaks[order[1]]
        if order.size > 1 and abs(peaks[order[1]] - best) >= 8
        else None
    )

    delay_samples = float(m_lo + int(best) - lag0)
    v = max(float(window[best]), 0.0)
    nf = np.sqrt(2.0 * np.log(nfft)) / np.sqrt(nfft)
    height_score = (
        min(1.0, np.log10(v / nf) / np.log10(1.0 / nf)) if v > nf else 0.0
    )
    if second is None or window[second] <= 0:
        ratio_score = 1.0
    else:
        r = v / float(window[second])
        ratio_score = max(0.0, min(1.0, (r - 1.0) / (r - 1.0 + 0.5)))
    prominence = float(prom[order[0]])
    prominence_score = min(1.0, prominence / v) if v > 0 else 0.0
    confidence = float(
        np.clip(
            0.3 * height_score + 0.5 * ratio_score + 0.2 * prominence_score,
            0.0, 1.0,
        )
    )
    return _GccOut(
        delay_samples=delay_samples, confidence=confidence,
        polarity=polarity, success=bool(v >= 2.0 * nf),
    )


# ---------------------------------------------------------------- 相位斜率精估


def phase_slope_fine(
    ref_seg: np.ndarray,
    tgt_seg: np.ndarray,
    *,
    sample_rate: int,
    band_hz: tuple[float, float] = (200.0, 8000.0),
) -> tuple[float, float, int]:
    """锚段相位斜率加权最小二乘精估 (P1 定位: 诊断读数 + 修后复检)。

    G(f) = conj(R(f)) * T(f); 调用方已按整数延迟补偿 tgt (残余 ∈ ±0.5
    样本, 带内 |φ| < π 不卷绕)。带内 φ(f) = -2πf·Δτ, 加权 LS:
    Δτ = -Σ w f φ / (2π Σ w f²), 权重 w = |G(f)| (能量乘积加权, 噪声 bin
    自动降权)。返回 (delta_samples, r2, polarity); 带内无能量等退化情形
    delta = nan。**不驱动分数修正**。
    """
    nan3 = (float("nan"), float("nan"), 1)
    L = ref_seg.shape[0]
    if L < 4096 or tgt_seg.shape[0] != L:
        return nan3
    nfft = fft.next_fast_len(L, real=True)
    spec_r = fft.rfft(ref_seg, nfft)
    spec_t = fft.rfft(tgt_seg, nfft)
    g = np.conj(spec_r) * spec_t
    del spec_r, spec_t

    freqs = fft.rfftfreq(nfft, d=1.0 / sample_rate)
    lo, hi = float(band_hz[0]), min(float(band_hz[1]), sample_rate / 2.0)
    mask = (freqs >= lo) & (freqs <= hi)
    if not np.any(mask):
        return nan3
    gb = g[mask]
    fb = freqs[mask]
    w = np.abs(gb)
    denom = float(np.sum(w * fb * fb))
    if denom <= 1e-300:
        return nan3
    phi = np.angle(gb)
    delta_sec = -float(np.sum(w * fb * phi)) / (2.0 * np.pi * denom)

    # 加权 R² (诊断): 残差按复角差计算, 抗个别 bin 卷绕
    fit = -2.0 * np.pi * fb * delta_sec
    resid = np.angle(np.exp(1j * (phi - fit)))
    w_sum = float(np.sum(w))
    mean = float(np.sum(w * phi)) / w_sum
    ss_res = float(np.sum(w * resid * resid))
    ss_tot = float(np.sum(w * (phi - mean) * (phi - mean)))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-300 else float("nan")

    # 反相检测 (沿用 vendored 思路): 白化锚段相关面在整数补偿后峰位于
    # lag≈0, 比较 ±64 样本窗内正/负峰, 负峰 1.2 倍更强 -> polarity = -1
    gw = g / (np.abs(g) + _EPS_REL * float(np.max(np.abs(g))) + _EPS_ABS)
    surface = fft.irfft(gw, nfft)
    w64 = np.concatenate((surface[:65], surface[nfft - 64:]))
    pos = float(np.max(w64))
    neg = -float(np.min(w64))
    polarity = -1 if neg > pos * 1.2 else 1

    return delta_sec * sample_rate, r2, polarity


# ---------------------------------------------------------------- 帧轨迹估计


def estimate_pair(
    ref_src: Any,
    tgt_src: Any,
    *,
    sample_rate: int,
    search_window_ms: float,
    frame_ms: float,
    hop_ms: float,
    anchor_segment_seconds: float,
    min_confidence: float,
    storage_dtype: str = "f32",
    coarse_rate: int = 8000,
    fine_phase_band_hz: tuple[float, float] = (200.0, 8000.0),
    min_usable_frames: int = 5,
    frame_min_rms_dbfs: float = -50.0,
    ref_index: int = -1,
    tgt_index: int = -1,
) -> PairEstimate:
    """两阶段 + 精估 (§8.3/§8.4; ref_src/tgt_src 支持 raw 路径或 1-D ndarray)。

    1) 抽取粗扫: 两流 decimate 到 coarse_rate (8kHz), 逐帧 GCC-PHAT,
       搜索窗 ±search_window_ms, 把全速率搜索收窄到 ±_FULL_SEARCH_HALF;
    2) 全速率帧轨迹: 每帧在粗延迟 ±8 样本内 GCC-PHAT 取整数延迟;
       置信度 < min_confidence 或任一窗 RMS < frame_min_rms_dbfs 的帧
       记 nan (保留在 frames, 供二期分类器);
    3) 全局延迟 = 合格帧延迟中位数 (nan-aware);
    4) 相位斜率精估: 两流中部 anchor_segment_seconds 锚段, 整数补偿后
       带内加权 LS -> fine_delay_samples (诊断/复检, 不驱动修正);
    5) 反相检测: 锚段白化相关面, 仅记录。
    """
    ref = open_source(ref_src, storage_dtype)
    tgt = open_source(tgt_src, storage_dtype)
    n = min(ref.shape[0], tgt.shape[0])
    if sample_rate % coarse_rate != 0:
        raise ValueError(
            f"sample_rate {sample_rate} 不能被 coarse_rate {coarse_rate} 整除"
        )
    q = sample_rate // coarse_rate

    f_c = int(round(frame_ms * coarse_rate / 1000.0))
    h_c = int(round(hop_ms * coarse_rate / 1000.0))
    frame = f_c * q                     # 全速率帧长 (与 coarse 帧位一致)
    hop = h_c * q
    s_c = int(math.ceil(search_window_ms * coarse_rate / 1000.0))
    w_half = _FULL_SEARCH_HALF
    rms_floor = 10.0 ** (frame_min_rms_dbfs / 20.0)

    n_frames = 0 if n < frame else (n - frame) // hop + 1
    frames: list[FrameDelay] = []
    for k in range(n_frames):
        center_sample = k * hop + frame // 2
        c0 = k * h_c
        ref_c = _coarse_slice(ref, c0, c0 + f_c, q)
        tgt_c = _coarse_slice(tgt, c0 - s_c, c0 + f_c + s_c, q)
        g = gcc_phat_int(
            ref_c, tgt_c, lag0=s_c, search_lo=-s_c, search_hi=s_c,
            rms_gate=rms_floor,
        )
        if not g.success or g.confidence < min_confidence:
            frames.append(FrameDelay(center_sample, float("nan"), g.confidence))
            continue
        center = int(round(g.delay_samples)) * q
        ref_f = read_f64(ref, k * hop, frame)
        tgt_f = read_f64(tgt, k * hop + center - w_half, frame + 2 * w_half)
        g2 = gcc_phat_int(
            ref_f, tgt_f,
            lag0=w_half - center,
            search_lo=center - w_half, search_hi=center + w_half,
            rms_gate=rms_floor,
        )
        if not g2.success or g2.confidence < min_confidence:
            frames.append(FrameDelay(center_sample, float("nan"), g2.confidence))
            continue
        frames.append(
            FrameDelay(center_sample, float(g2.delay_samples), g2.confidence)
        )

    delays = np.array([f.delay_samples for f in frames], dtype=np.float64)
    confs = np.array([f.confidence for f in frames], dtype=np.float64)
    good = ~np.isnan(delays)
    usable = int(np.count_nonzero(good))
    if usable == 0:
        return PairEstimate(
            ref=ref_index, tgt=tgt_index,
            delay_samples=float("nan"), delay_ms=float("nan"),
            confidence=0.0, usable_frames=0, frames=frames,
        )
    global_delay = float(np.median(delays[good]))
    conf_med = float(np.median(confs[good]))

    fine = float("nan")
    r2 = float("nan")
    polarity = 1
    if usable >= min_usable_frames and n >= 4096:
        anchor_len = min(int(round(anchor_segment_seconds * sample_rate)), n)
        a0 = (n - anchor_len) // 2
        d_int = int(math.floor(global_delay + 0.5))   # 确定性取整 (含负值)
        ref_seg = read_f64(ref, a0, anchor_len)
        tgt_seg = read_f64(tgt, a0 + d_int, anchor_len)
        delta, r2, polarity = phase_slope_fine(
            ref_seg, tgt_seg,
            sample_rate=sample_rate, band_hz=fine_phase_band_hz,
        )
        if delta == delta:      # NaN 判定沿用 x != x 惯例的否定写法
            fine = d_int + delta

    return PairEstimate(
        ref=ref_index, tgt=tgt_index,
        delay_samples=global_delay,
        delay_ms=global_delay * 1000.0 / sample_rate,
        confidence=conf_med, usable_frames=usable, frames=frames,
        fine_delay_samples=fine,
        fine_delay_ms=fine * 1000.0 / sample_rate,
        fine_fit_r2=r2, polarity=polarity,
    )


def summarize_trajectory(
    frames: list[FrameDelay], sample_rate: int
) -> TrajectoryStats:
    """合格帧轨迹统计: MAD / 极差 / 相邻步长 / 线性漂移 (nan-aware)。

    drift_total_ms = 线性拟合在轨迹跨度上的预测漂移总量 (ms) — 用于区分
    "真实慢漂移"与"整数量化噪声导致的偶然斜率"(见 classify_constant)。
    """
    good = [f for f in frames if f.delay_samples == f.delay_samples]
    usable = len(good)
    if usable == 0:
        return TrajectoryStats(
            usable, float("nan"), float("nan"), float("nan"), 0.0,
            float("nan"),
        )
    d = np.array([f.delay_samples for f in good], dtype=np.float64)
    med = float(np.median(d))
    mad_ms = float(np.median(np.abs(d - med))) * 1000.0 / sample_rate
    spread = float(np.max(d) - np.min(d))
    step = float(np.max(np.abs(np.diff(d)))) if usable >= 2 else 0.0
    step_ms = step * 1000.0 / sample_rate
    drift_ppm = 0.0
    drift_total_ms = float("nan")
    if usable >= 2:
        c = np.array([f.center_sample for f in good], dtype=np.float64)
        slope = float(np.polyfit(c, d, 1)[0])
        drift_ppm = slope * 1e6
        span = float(c[-1] - c[0])
        drift_total_ms = slope * span * 1000.0 / sample_rate
    return TrajectoryStats(
        usable, mad_ms, spread, step_ms, drift_ppm, drift_total_ms
    )


def classify_constant(
    stats: TrajectoryStats, *, mad_max_ms: float, max_ppm: float,
    sample_rate: int, drift_min_ms: float = 0.0,
) -> bool:
    """P1 恒定性二分: MAD / 极差 / 线性漂移 三门 (constant/non_constant)。

    - 极差门用 mad_max_ms 同值: 中途跳变时中位数可能恰好落在跳变前值上
      (MAD 退化), 此时极差 (50ms 级) 仍能可靠拦下;
    - 漂移门 = ppm 门 + **材料性门**: ppm 超门**且**全片预测漂移
      |drift_total_ms| > drift_min_ms 才判非恒定。真实素材标定依据
      (testsets A7M5): 整数样本量化噪声会让"已对齐"轨的偶然斜率落在
      5–30 ppm (spread 仅 2–3 样本 = 0.05ms), 单看 ppm 会误判为非恒定;
      而真实慢漂移轨 (C1154 CH1 ≈39ppm/0.42ms, C1159 CH2 ≈49ppm/1.9ms)
      的预测漂移远超 0.1ms, 仍被正确拦下。
    - step_max_ms 只作诊断警告, 不参与判定 — 跳变/漂移细分属二期。
    """
    spread_ms = stats.spread_samples * 1000.0 / sample_rate
    drift_bad = (
        abs(stats.drift_ppm) > max_ppm
        and (
            drift_min_ms <= 0.0
            or (
                stats.drift_total_ms == stats.drift_total_ms
                and abs(stats.drift_total_ms) > drift_min_ms
            )
        )
    )
    return (
        stats.mad_ms == stats.mad_ms
        and stats.mad_ms <= mad_max_ms
        and stats.spread_samples == stats.spread_samples
        and spread_ms <= mad_max_ms
        and not drift_bad
    )


def boundary_pileup(frames: list[FrameDelay], search_samples: float) -> float:
    """合格帧落在搜索窗边缘 (|delay| >= search_samples - 0.5) 的比例。

    真实时差超出搜索窗时, 可测帧会堆积在窗边缘 — 集成层据此判定
    out_of_range (而非把边缘值当作真实延迟去修)。
    """
    good = [f.delay_samples for f in frames if f.delay_samples == f.delay_samples]
    if not good:
        return 0.0
    edge = sum(1 for v in good if abs(v) >= search_samples - 0.5)
    return edge / len(good)
