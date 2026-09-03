"""mp4_channel_sync — 无线麦克风通道延迟同步算法（独立交付包）。

Vendored into 1KeyTranscoder from the ChronoSync handoff package
(F:\\ChronoSync\\handoff\\mp4_channel_sync, 2026-09-01)。**MIT License**
(与宿主项目 LGPL-3.0-or-later 兼容; 按 MIT 文本保留版权声明)。

Standalone Python module for measuring and correcting the constant per-file
latency of wireless microphone channels (CH1/CH2) relative to wired channels
(CH3/CH4) inside multi-stream MP4 recordings.

依赖: numpy + scipy(>=1.9)。**无任何其它依赖**（不含 ffmpeg/ChronoSync）:
容器解复用/重封装由宿主程序（如 1KeyTranscoder）负责，本模块只吃
float32 mono 数组、只吐数组与测量结果。

实测事实（真实素材，20260903_Cxxxx 系列，19 片）:
* 片内延迟恒定（同容器同时钟，无线延迟 = 纯固定 latency，无漂移）;
* 延迟跨文件不恒定（实测 19.7-29.5 ms），必须逐文件测量;
* 有线对 CH3/CH4 相互对齐 < 0.005 ms，作为参考。

约定（方向）: delay = t_channel - t_reference; delay > 0 表示该通道
到达更晚，修正时需整体**前移** delay。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import fft, signal

__all__ = [
    "DelayResult",
    "gcc_phat",
    "measure_channels",
    "measure_delay",
    "fix_channels",
    "verify_channels",
]

DEFAULT_SAMPLE_RATE = 48_000


# ---------------------------------------------------------------- GCC-PHAT


@dataclass
class _GccResult:
    delay_samples: float
    peak: float
    second_peak: float
    prominence: float
    confidence: float
    polarity: int
    success: bool


def gcc_phat(
    reference: np.ndarray,
    target: np.ndarray,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    search_min: int | None = None,
    search_max: int | None = None,
    eps_rel: float = 1e-3,
    eps_abs: float = 1e-12,
) -> _GccResult:
    """GCC-PHAT 延迟估计（正则化白化 + 抛物线亚样本 + 反相感知）。

    ``delay = t_target - t_reference``（正值 = target 到达更晚）。
    反相对的相关峰为负，会自动识别并返回 polarity=-1。
    """
    ref = np.asarray(reference, dtype=np.float64)
    tgt = np.asarray(target, dtype=np.float64)
    if ref.size == 0 or tgt.size == 0:
        return _GccResult(float("nan"), 0.0, 0.0, 0.0, 0.0, 1, False)
    ref = ref - ref.mean()
    tgt = tgt - tgt.mean()
    r_ref = float(np.sqrt(np.mean(ref * ref)))
    r_tgt = float(np.sqrt(np.mean(tgt * tgt)))
    if r_ref < 1e-12 or r_tgt < 1e-12:
        return _GccResult(float("nan"), 0.0, 0.0, 0.0, 0.0, 1, False)
    ref /= r_ref
    tgt /= r_tgt

    nfft = fft.next_fast_len(ref.size + tgt.size - 1, real=True)
    g = np.conj(fft.rfft(ref, nfft)) * fft.rfft(tgt, nfft)
    g /= np.abs(g) + eps_rel * float(np.max(np.abs(g))) + eps_abs
    surface = fft.irfft(g, nfft)

    lag_min_valid = -(tgt.size - 1)
    lag_max_valid = ref.size - 1
    lo = lag_min_valid if search_min is None else max(int(search_min), lag_min_valid)
    hi = lag_max_valid if search_max is None else min(int(search_max), lag_max_valid)
    if lo > hi:
        return _GccResult(float("nan"), 0.0, 0.0, 0.0, 0.0, 1, False)

    i_lo = lo if lo >= 0 else lo + nfft
    i_hi = hi if hi >= 0 else hi + nfft
    if i_lo <= i_hi:
        window = surface[i_lo : i_hi + 1]
    else:
        window = np.concatenate((surface[i_lo:], surface[: i_hi + 1]))

    # 反相感知：负峰明显更强时翻转相关面
    polarity = 1
    pos_best = float(np.max(window))
    neg_best = -float(np.min(window))
    if neg_best > pos_best * 1.2:
        polarity = -1
        window = -window

    padded = np.concatenate(([-np.inf], window, [-np.inf]))
    peaks, _ = signal.find_peaks(padded, distance=2)
    if peaks.size == 0:
        return _GccResult(float("nan"), 0.0, 0.0, 0.0, 0.0, polarity, False)
    keep = [
        int(i) - 1
        for i in peaks
        if padded[i] > padded[i - 1] and padded[i] > padded[i + 1]
    ]
    if not keep:
        return _GccResult(float("nan"), 0.0, 0.0, 0.0, polarity, False)
    peaks = np.asarray(keep)
    with warnings.catch_warnings():
        # 等高峰（周期信号）prominence=0 是合法情形，不是问题
        warnings.filterwarnings("ignore", message="some peaks have a prominence of 0")
        prom = signal.peak_prominences(window, peaks)[0]
    order = np.argsort(window[peaks])[::-1]
    best = peaks[order[0]]
    second = peaks[order[1]] if order.size > 1 and abs(peaks[order[1]] - best) >= 8 else None

    rel = best
    delta = 0.0
    if 1 <= rel <= window.size - 2:
        y0, y1, y2 = window[rel - 1], window[rel], window[rel + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            delta = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
    delay_samples = float(lo + rel) + delta

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
        np.clip(0.3 * height_score + 0.5 * ratio_score + 0.2 * prominence_score, 0.0, 1.0)
    )
    success = bool(v >= 2.0 * nf)
    return _GccResult(
        delay_samples=delay_samples, peak=v,
        second_peak=(float(window[second]) if second is not None else 0.0),
        prominence=prominence, confidence=confidence, polarity=polarity,
        success=success,
    )


# ---------------------------------------------------------------- 测量


@dataclass
class DelayResult:
    """单通道测量结果（delay 方向见模块 docstring）。"""

    channel: int
    delay_samples: float  # 正 = 比参考更晚; nan = 无法测量
    delay_ms: float
    confidence: float  # [0, 1]
    peak: float
    polarity: int  # +1 正常 / -1 反相
    drift_ppm: float  # 三窗口一致性线性拟合的斜率（≈0 即纯固定延迟）
    constant: bool  # 片内恒定（各窗口 offset 极差 <= 2 样本）
    warnings: list[str] = field(default_factory=list)


def measure_delay(
    reference: np.ndarray,
    target: np.ndarray,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    n_windows: int = 3,
    max_lag_seconds: float = 1.0,
    min_confidence: float = 0.3,
) -> DelayResult:
    """测量 target 相对 reference 的延迟，并做恒定/漂移判定。

    1) 全长 GCC 得主延迟; 2) 在 1/4、1/2、3/4 处开窗复测三个局部 offset;
    3) 极差 <= 2 样本且斜率 |ppm| < 20 -> constant（片内纯固定延迟）。
    全部确定性、无随机数。
    """
    warnings: list[str] = []
    common = min(reference.size, target.size)
    ref = np.asarray(reference[:common], dtype=np.float64)
    tgt = np.asarray(target[:common], dtype=np.float64)

    gcc = gcc_phat(ref, tgt, sample_rate)
    if not gcc.success or gcc.confidence < min_confidence:
        return DelayResult(
            channel=0, delay_samples=float("nan"), delay_ms=float("nan"),
            confidence=gcc.confidence, peak=gcc.peak, polarity=gcc.polarity,
            drift_ppm=0.0, constant=False,
            warnings=[*warnings, "GCC failed or confidence below threshold"],
        )
    d0 = gcc.delay_samples
    if gcc.polarity < 0:
        warnings.append("polarity inversion detected (negative correlation peak)")

    window = min(int(16.0 * sample_rate), common // 2)
    if window < 4096:
        return DelayResult(
            channel=0, delay_samples=d0, delay_ms=d0 * 1000.0 / sample_rate,
            confidence=gcc.confidence, peak=gcc.peak, polarity=gcc.polarity,
            drift_ppm=0.0, constant=True,
            warnings=[*warnings, "signal too short for the drift check"],
        )

    positions = [int(common * f) for f in (0.25, 0.5, 0.75)]
    offsets, centers = [], []
    margin = int(max_lag_seconds * sample_rate)
    for pos in positions:
        start = max(0, min(pos - window // 2, common - window))
        rw = ref[start : start + window]
        tw = tgt[start : start + window]
        w = gcc_phat(
            rw, tw, sample_rate,
            search_min=int(round(d0)) - margin, search_max=int(round(d0)) + margin,
        )
        if w.success and w.confidence >= min_confidence:
            offsets.append(w.delay_samples)
            centers.append(float(start + window // 2))

    drift_ppm = 0.0
    constant = False
    if len(offsets) >= 2:
        slope = float(np.polyfit(np.asarray(centers), np.asarray(offsets), 1)[0])
        drift_ppm = slope * 1e6
        spread = float(np.max(offsets) - np.min(offsets))
        constant = spread <= 2.0 and abs(drift_ppm) < 20.0
        if not constant:
            warnings.append(
                f"offsets vary across windows (spread {spread:.1f} samples, "
                f"{drift_ppm:+.1f} ppm) — not a pure constant latency"
            )
    else:
        warnings.append("drift check windows failed; constant assumed from full GCC")

    return DelayResult(
        channel=0, delay_samples=d0, delay_ms=d0 * 1000.0 / sample_rate,
        confidence=gcc.confidence, peak=gcc.peak, polarity=gcc.polarity,
        drift_ppm=drift_ppm, constant=constant, warnings=warnings,
    )


def measure_channels(
    channels: list[np.ndarray],
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    reference_index: int = 2,
    max_lag_seconds: float = 1.0,
    min_confidence: float = 0.3,
) -> list[DelayResult]:
    """测量全部通道相对参考通道的延迟。

    通道数组须为同采样率 float32/float64 mono；参考通道自身返回 0。
    """
    if not (0 <= reference_index < len(channels)):
        raise ValueError(f"reference_index {reference_index} out of range")
    results: list[DelayResult] = []
    for i, ch in enumerate(channels):
        if i == reference_index:
            results.append(
                DelayResult(
                    channel=i, delay_samples=0.0, delay_ms=0.0,
                    confidence=1.0, peak=1.0, polarity=1,
                    drift_ppm=0.0, constant=True,
                )
            )
            continue
        r = measure_delay(
            channels[reference_index], ch, sample_rate,
            max_lag_seconds=max_lag_seconds, min_confidence=min_confidence,
        )
        r.channel = i
        results.append(r)
    return results


# ---------------------------------------------------------------- 修正


def _fractional_shift(x: np.ndarray, shift: float, taps: int = 65) -> np.ndarray:
    """带限（窗 sinc）分数移位：输出 y[n] ≈ x[n - shift]（shift 可正可负）。

    奇数 taps 保证卷积居中（偶数核会引入 taps/2 样本偏移）。
    """
    x = np.asarray(x, dtype=np.float64)
    i0 = int(np.floor(shift))
    frac = shift - i0
    m = np.arange(-(taps // 2), taps // 2 + 1)
    h = np.sinc(m - frac) * np.hanning(taps)
    h /= h.sum()
    y = np.convolve(x, h, mode="same")
    if i0 >= 0:
        out = np.zeros_like(y)
        if i0 < y.size:
            out[i0:] = y[: y.size - i0]
        return out
    m0 = -i0
    out = np.zeros_like(y)
    if m0 < y.size:
        out[: y.size - m0] = y[m0:]
    return out


def fix_channels(
    channels: list[np.ndarray],
    delays_samples: list[float],
    sample_rate: float = DEFAULT_SAMPLE_RATE,
) -> np.ndarray:
    """按测量延迟把各通道**前移**对齐（delay > 0 的通道整体提前）。

    * 分数样本延迟用窗 sinc 精确实现（残差 < 0.1 样本）；
    * 无法测量的通道（nan）原样保留；
    * 所有通道裁剪到共同对齐长度，输出 float32 (channels, n)；
    * 电平不改变（不做归一化/增益）。
    """
    assert len(channels) == len(delays_samples)
    shifted = []
    for ch, delay in zip(channels, delays_samples):
        ch = np.asarray(ch, dtype=np.float64)
        if np.isnan(delay) or delay == 0.0:
            shifted.append(ch)
        else:
            # _fractional_shift(x, s) 实现 y[n] ≈ x[n - s]；
            # delay > 0 表示到达更晚，需要前移 -> s = -delay。
            shifted.append(_fractional_shift(ch, -delay))
    common = min(s.size for s in shifted)
    return np.stack([s[:common] for s in shifted]).astype(np.float32)


def verify_channels(
    channels: np.ndarray | list[np.ndarray],
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    reference_index: int = 2,
    window_seconds: float = 8.0,
) -> list[float]:
    """复检：返回各通道相对参考的残差延迟（毫秒）。< 0.05 ms 视为通过。"""
    chans = [np.asarray(c) for c in channels]
    window = int(window_seconds * sample_rate)
    common = min(c.size for c in chans)
    ref = chans[reference_index][:common]
    residuals = []
    for i, ch in enumerate(chans):
        if i == reference_index:
            residuals.append(0.0)
            continue
        tgt = ch[:common]
        start = max(0, common // 2 - window // 2)
        g = gcc_phat(
            ref[start : start + window], tgt[start : start + window],
            sample_rate, search_min=-int(0.1 * sample_rate), search_max=int(0.1 * sample_rate),
        )
        residuals.append(float(g.delay_samples) * 1000.0 / sample_rate if g.success else float("nan"))
    return residuals
