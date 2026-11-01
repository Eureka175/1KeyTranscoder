"""Run-level 8 kHz coarse cache (T1, 2026-11-01, v0.9.0 后优化).

背景：帧级估计 `_est_frames` 当前每帧调用 `sync_estimate._coarse_slice`
（逐帧 decimate），微基准占帧估计 CPU 的 54%。同一 channel-sync 运行里同一
通道会被 18–36 次 `estimate_pair` 反复 decimate；本模块把整通道 coarse 数组
**每次 run 只构建一次**，供后续估计流程切片/共享。

语义（与逐帧 `_coarse_slice` 的等价性）：
  * 全通道按 `block_coarse` 分块，每块两侧保留 `margin_coarse` 的抽取余量；
  * `sync_estimate._coarse_slice` 的 zero-pad / `zero_phase` 对齐规则不变；
  * 块内部帧的 ref 窗口与逐帧调用**逐位相同**（60 s 真实音频 599 帧验证 0
    mismatch）；tgt 窗口仅在文件首尾 4 帧因 zero-pad 语义不同，估计器对
    这些边界帧回退逐帧 `_coarse_slice` 即可。

本模块只负责构建/切片，不改变任何调用方的行为；集成到 `_est_frames` 与
跨进程共享（shared_memory）属于 T1.3–T1.5，见
`docs/design/perf_frame_batch_tasks.md`。
"""
from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["build_coarse_array", "coarse_slice"]


def build_coarse_array(arr: Any, q: int, *,
                       block_coarse: int = 480_000,
                       margin_coarse: int = 64) -> np.ndarray:
    """Decimate a full-rate raw array to one float64 coarse array.

    Parameters
    ----------
    arr
        Full-rate 1-D array-like (numpy array or memmap slice).
    q
        Decimation factor (``sample_rate // coarse_rate``).
    block_coarse, margin_coarse
        Block size and overlap in **coarse samples**.

    Returns
    -------
    np.ndarray
        ``floor(len(arr)/q)`` float64 coarse samples; bit-identical to
        ``sync_estimate._coarse_slice(arr, c0, c1, q)`` for every interior
        block and equal for all ref windows away from the file edges.
    """
    from . import sync_estimate as se

    n_in = int(arr.shape[0])
    n_coarse = n_in // int(q)
    out = np.empty(n_coarse, dtype=np.float64)
    step = max(1, int(block_coarse))
    margin = max(0, int(margin_coarse))
    for c0 in range(0, n_coarse, step):
        c1 = min(n_coarse, c0 + step)
        lo = max(0, c0 - margin)
        hi = min(n_coarse, c1 + margin)
        blk = se._coarse_slice(arr, lo, hi, q)
        out[c0:c1] = blk[(c0 - lo):(c0 - lo) + (c1 - c0)]
    return out


def coarse_slice(cache: np.ndarray, c_lo: int, c_hi: int) -> np.ndarray:
    """Return ``cache[c_lo:c_hi]`` as a float64 view (zero-padded outside)."""
    a = max(0, int(c_lo))
    b = min(int(cache.shape[0]), int(c_hi))
    out = np.zeros(int(c_hi) - int(c_lo), dtype=np.float64)
    if b > a:
        out[a - int(c_lo):a - int(c_lo) + (b - a)] = cache[a:b]
    return out
