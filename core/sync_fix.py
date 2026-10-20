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

__all__ = ["shift_stream", "recheck_residual", "recheck_residual_windows"]


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
    已在估计前拦截参与同步的轨)。src 支持 raw 路径 (RawStream) 或 ndarray。

    输出用普通文件句柄 seek+write, **不用 np.memmap(mode="w+")** —— 写映射
    会把整条输出文件的脏页留在进程 WorkingSet (实测单轨峰值 +36 MB)。
    峰值内存 ≈ 1 个 chunk (fix_chunk_seconds × sample_rate × itemsize)。
    """
    x = open_source(src, storage_dtype)
    owns = isinstance(src, (str, Path))
    n = x.shape[0]
    shift = int(np.rint(float(delay_samples)))
    dt = np.dtype("<f4") if storage_dtype == "f32" else np.dtype("<f8")
    block_n = max(1, int(round(chunk_seconds * sample_rate)))
    try:
        with open(dst, "wb") as fh:
            for a in range(0, n, block_n):
                b = min(n, a + block_n)
                # out[a:b] = x[a+shift : b+shift], 越界补零
                block = np.zeros(b - a, dtype=dt)
                s_lo = a + shift
                lo = max(0, s_lo)
                hi = min(n, b + shift)
                if hi > lo:
                    read_into = getattr(x, "read_into", None)
                    if read_into is not None:
                        # 直接读进目标块, 免去中间窗口副本
                        read_into(block, lo - s_lo, lo, hi - lo)
                    else:
                        block[lo - s_lo: hi - s_lo] = x[lo:hi]
                fh.write(block.tobytes())
    finally:
        if owns:
            close = getattr(x, "close", None)
            if callable(close):
                close()


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
    opened: list[Any] = []
    try:
        ref = open_source(ref_src, storage_dtype)
        if isinstance(ref_src, (str, Path)):
            opened.append(ref)
        tgt = open_source(fixed_src, storage_dtype)
        if isinstance(fixed_src, (str, Path)):
            opened.append(tgt)
        return _recheck(
            ref, tgt, sample_rate=sample_rate, search_ms=search_ms,
            anchor_segment_seconds=anchor_segment_seconds,
            fine_phase_band_hz=fine_phase_band_hz,
        )
    finally:
        # 只关闭本函数自己打开的流; 调用方传入的流由调用方管理
        # (Windows 上删除 raw 文件前必须关闭句柄)。opened 逐个追加,
        # 第二个 open 抛错时第一个也一定会被关闭。
        for stream in opened:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


def _recheck(
    ref: Any,
    tgt: Any,
    *,
    sample_rate: int,
    search_ms: float,
    anchor_segment_seconds: float,
    fine_phase_band_hz: tuple[float, float],
    center_frac: float = 0.5,
) -> float:
    n = min(ref.shape[0], tgt.shape[0])
    if n < 4096:
        return float("nan")
    s = int(math.ceil(search_ms * sample_rate / 1000.0))
    if n < 2 * s + 4096:
        return float("nan")
    anchor_len = min(int(round(anchor_segment_seconds * sample_rate)), n)
    # 锚段必须留出**搜索余量**: 读窗是 [a0-s, a0+anchor_len+s), 需要两头都在
    # 数组内。旧实现直接把 a0 = (n - anchor_len)//2 = 0、s = 240 传下去,
    # 读窗起点变成 -240 —— `read_f64` 把它钳到 0, 于是短片段实际退化成
    # "整片窗", 一直能测; 我上一版把这一步改成返回 nan, 结果所有短于 20 s 的
    # 片段复检全变 nan, 被当成"复检失败"退回, 一次性误杀 7 条已修好的轨。
    # 正确做法是在**收缩锚段长度**的基础上保留搜索余量, 而不是放弃测量。
    if anchor_len > n - 2 * s:
        anchor_len = n - 2 * s
    span = n - anchor_len
    # `center_frac` 决定锚段落在片长上的位置 (0=片头, 0.5=中部, 1=片尾)。
    # 0.5 必须**逐位复现**旧公式 `(n - anchor_len) // 2`。
    if center_frac == 0.5:
        a0 = span // 2
    else:
        c = min(1.0, max(0.0, float(center_frac)))
        a0 = min(max(int(round(span * c)), 0), span)
    # 收缩 anchor_len 之后这两条恒成立; 留作断言式的防御, 不猜。
    s = max(0, min(s, a0, n - anchor_len - a0))
    if s < 1:
        return float("nan")


    if s < 1:
        return float("nan")
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


def recheck_residual_windows(
    ref_src: Any,
    fixed_src: Any,
    *,
    sample_rate: int,
    storage_dtype: str,
    windows: int = 3,
    search_ms: float = 5.0,
    anchor_segment_seconds: float,
    fine_phase_band_hz: tuple[float, float] = (200.0, 8000.0),
) -> dict:
    """全片多窗复检: 沿片长均匀取 `windows` 个锚段, 返回**最差**残差。

    为什么需要它: 单窗复检只看中部锚段。若某轨的偏移不是全片常数,
    中部恰好局部对齐, 单窗就会判"通过", 而全片其实还有几十采样的残差
    （实测 `20260904_C1179 CH1`: 中部通过, 全片 31.5 采样）。
    反过来, 对**恒定**偏移只需要一个窗就够, 所以默认 `windows=1` 保持旧行为,
    多窗是显式开启的。

    返回 `{"worst_ms", "worst_abs_ms", "values", "n_windows", "centers"}`;
    `worst_ms` 为 nan 表示全部窗都不可测。
    """
    k = max(1, int(windows))
    # 0.5 仍是第一个窗: 单窗时与 recheck_residual 完全等价。
    if k == 1:
        centers = [0.5]
    else:
        # 避开最边上的 5%, 那里常含淡入/静音, 相关性差。
        centers = [0.05 + 0.9 * i / (k - 1) for i in range(k)]
    opened: list[Any] = []
    try:
        ref = open_source(ref_src, storage_dtype)
        if isinstance(ref_src, (str, Path)):
            opened.append(ref)
        tgt = open_source(fixed_src, storage_dtype)
        if isinstance(fixed_src, (str, Path)):
            opened.append(tgt)
        values: list[float] = []
        for c in centers:
            try:
                values.append(_recheck(
                    ref, tgt, sample_rate=sample_rate, search_ms=search_ms,
                    anchor_segment_seconds=anchor_segment_seconds,
                    fine_phase_band_hz=fine_phase_band_hz, center_frac=c,
                ))
            except Exception:                 # noqa: BLE001 — 单窗失败不致命
                values.append(float("nan"))
        good = [v for v in values if v == v]
        worst = max(good, key=abs) if good else float("nan")
        return {"worst_ms": worst,
                "worst_abs_ms": abs(worst) if worst == worst else float("nan"),
                "values": values, "n_windows": k, "centers": centers}
    finally:
        for stream in opened:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
