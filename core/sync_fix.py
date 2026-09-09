"""通道修正 (P1 正式版, algo 2.3.0-p1): 纯整数样本移位 + 修后复检.

职责:
  * shift_stream(): P1 唯一正式修正路径 — out[n] = in[n + shift],
    shift = int(np.rint(delay_samples)); delay > 0 = 目标轨晚到 -> 前移,
    前移产生的尾部补零 (负延迟后移同理头部补零); 输出样本数 == 输入;
    分块 memmap 搬移, 无滤波/无插值/无块间状态, 峰值内存与文件时长
    无关, 样本值本身不改变 (48kHz 下最大量化残差 0.5 sample ≈ 10.4µs,
    96kHz 下 ≈ 5.2µs);
  * recheck_residual(): 修后流 vs 锚流 — 锚段局部 GCC + 相位斜率精估
    (与 sync_estimate 同源), 返回残余延迟 (ms, nan = 不可测), 供
    0.05ms 复检门。

依赖: numpy + core.sync_estimate (可选依赖, ImportError 由集成层统一捕获).

边界 (P1 明确禁止): 分数 sinc 修正 / 漂移 resample / phase vocoder /
任意改变采样值的滤波链。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .sync_estimate import (
    gcc_phat_int,
    open_source,
    phase_slope_fine,
    read_f64,
)

__all__ = ["shift_stream", "recheck_residual"]


def shift_stream(
    src: Any,
    dst: Any,
    *,
    delay_samples: float,
    sample_rate: int,
    storage_dtype: str,
    chunk_seconds: float,
) -> None:
    """整数前移: out[n] = in[n + rint(delay)] (delay>0 前移, 越界补零)。

    样本值逐位复制, 不经过任何算术/滤波 — NaN/Inf 原样通过 (健康门
    已在估计前拦截参与同步的轨)。src 支持 raw 路径 (memmap) 或 ndarray。
    """
    x = open_source(src, storage_dtype)
    n = x.shape[0]
    shift = int(np.rint(float(delay_samples)))
    dt = np.dtype("<f4") if storage_dtype == "f32" else np.dtype("<f8")
    block_n = max(1, int(round(chunk_seconds * sample_rate)))
    out = np.memmap(dst, dtype=dt, mode="w+", shape=(n,))
    try:
        for a in range(0, n, block_n):
            b = min(n, a + block_n)
            # out[a:b] = x[a+shift : b+shift], 越界补零
            block = np.zeros(b - a, dtype=dt)
            s_lo = a + shift
            lo = max(0, s_lo)
            hi = min(n, b + shift)
            if hi > lo:
                block[lo - s_lo: hi - s_lo] = x[lo:hi]
            out[a:b] = block
        out.flush()
    finally:
        del out


def recheck_residual(
    ref_src: Any,
    fixed_src: Any,
    *,
    sample_rate: int,
    storage_dtype: str,
    search_ms: float = 5.0,
    anchor_segment_seconds: float,
    fine_phase_band_hz: tuple[float, float] = (200.0, 8000.0),
) -> float:
    """修后复检: 修后流 vs 锚流的锚段残差 (ms), nan = 不可测。

    锚段 (两流中部 anchor_segment_seconds, 不足取全长) 内先做 ±search_ms
    局部 GCC-PHAT 整数粗扫, 再按整数补偿做相位斜率加权 LS 精估 (与
    sync_estimate 第 4 步同源), 返回残余延迟; 精估退化时回退整数值。
    整数修正的量化残差理论上 <= 0.5 sample — 超过 verify_max_ms 明显
    超差时才判失败 (区分"修正动作: 整数"与"测量结果: 相位斜率")。
    """
    ref = open_source(ref_src, storage_dtype)
    tgt = open_source(fixed_src, storage_dtype)
    n = min(ref.shape[0], tgt.shape[0])
    if n < 4096:
        return float("nan")
    anchor_len = min(int(round(anchor_segment_seconds * sample_rate)), n)
    a0 = (n - anchor_len) // 2
    s = int(math.ceil(search_ms * sample_rate / 1000.0))

    ref_seg = read_f64(ref, a0, anchor_len)
    tgt_win = read_f64(tgt, a0 - s, anchor_len + 2 * s)
    g = gcc_phat_int(ref_seg, tgt_win, lag0=s, search_lo=-s, search_hi=s)
    if not g.success:
        return float("nan")
    d_int = int(g.delay_samples)
    tgt_seg = read_f64(tgt, a0 + d_int, anchor_len)
    delta, _r2, _pol = phase_slope_fine(
        ref_seg, tgt_seg, sample_rate=sample_rate, band_hz=fine_phase_band_hz
    )
    total = d_int + delta if delta == delta else float(d_int)
    return total * 1000.0 / sample_rate
