"""通道延迟估计 (P1 正式版, algo 2.3.0-p1): 两阶段 GCC-PHAT + 相位斜率精估.

职责:
  * estimate_pair(): 参考/目标两流 -> 分帧粗扫 (8kHz) + 全速率精测轨迹 +
    全局延迟 (合格帧中位数) + 锚段相位斜率加权 LS 精估
    (fine_delay_samples / fine_delay_ms / fine_fit_r2);
  * summarize_trajectory(): 帧轨迹统计 (MAD / 极差 / 相邻步长 / drift_ppm),
    供集成层恒定性门与报告诊断;
  * classify_constant(): constant / non_constant 判定 (MAD + 实测位移),
    P1 只做二分 — step/ramp 细分属二期, 相邻步长仅作诊断警告;
  * boundary_pileup(): 合格帧落在搜索窗边缘的比例, 供 out_of_range 判定
    (真实时差超出搜索窗时帧测量堆积在窗边缘);
  * sample_pair_snr() / peak_noise_ratio(): **建组**用的相关强度采样。它们
    不产生延迟值, 只回答"两条轨是不是在录同一个声场" —— 单锚点模型无法
    表达"4 声道其实是两支互不相干的麦克风", 建组层需要这个量;
  * 底层件 open_source / read_f64 / gcc_phat_int / phase_slope_fine 公开,
    供 sync_fix 修后复检复用;
  * coarse_decimate(): 长素材的 12 kHz 分块抽取缓存 (窗级测量的只读输入,
    内存只与 coarse 数组成正比, 不触碰全速率整文件);
  * measure_window_delays(): 长镜头**窗级连续性漂移测量** (12 kHz 池化粗扫
    + 全速率局部精化 + 连续性约束), 供时变后备路径直接测窗 —— 生产帧几何
    在 30 s 窗内只有约 0.048 个可用帧, 帧级聚合测不出逐窗延迟.

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
流式读取; 延迟估计全程确定性 (固定 FFT 尺寸 + 整数帧位, 无任何 RNG)。
`sample_pair_snr()` 是唯一的抽样环节: 种子固定 (`_SNR_SEED`), 因此同一对
素材在任何一次运行里抽到同一批帧 —— 建组结论可复现, 但它**不参与**任何
延迟数值。
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import fft, signal

__all__ = [
    "shutdown_estimation_pool",
    "FrameDelay",
    "PairEstimate",
    "RawStream",
    "TrajectoryStats",
    "estimate_pair",
    "summarize_trajectory",
    "classify_constant",
    "measured_displacement_ms",
    "boundary_pileup",
    "open_source",
    "read_f64",
    "gcc_phat_int",
    "peak_noise_ratio",
    "sample_pair_snr",
    "phase_slope_fine",
    "coarse_decimate",
    "measure_window_delays",
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
# 建组抽样种子。全模块唯一的随机源, 且种子固定 —— 同一对素材在任何一次
# 运行里抽到同一批帧, 因此分组结论可复现 (报告里出现的组结构不是掷骰子)。
_SNR_SEED = 1234


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


# 读缓存块大小 (字节) 与每流槽位数: 顺序访问 (帧 hop < 帧长) 时把系统调用
# 数降低一个数量级, 而内存开销固定为 _BLOCK_BYTES * _BLOCK_SLOTS。
_BLOCK_BYTES = 1 << 20
_BLOCK_SLOTS = 4


class RawStream:
    """有界窗口读取器: 只把请求的窗口读进内存, **不做整文件映射**。

    为什么不用 np.memmap: 映射本身几乎不占内存, 但逐字节触碰之后, Windows
    会把每一个 file-backed 页计入进程 WorkingSet 并长期保留 —— 实测对 4 条
    110 MB 的轨做整轨扫描, RSS 恰好 +440 MB (见 work/channel_sync_memory_audit.md)。
    本类改用 seek + read 只取需要的窗口, 峰值内存 = O(窗口 + 读缓存),
    与文件时长无关; 读到的字节与映射读取**逐位一致**, 因此数值结果不变。

    对调用方暴露 ndarray 兼容的最小接口 (现有调用点无需改动):
      * ``shape[0]``  样本总数
      * ``arr[a:b]``  该窗口的只读 ndarray (边界自动裁剪)
      * ``read_into()``  直接把窗口读进调用方缓冲 (shift_stream 用, 免中间副本)
      * ``close()``   关闭句柄 (Windows 上删除文件前必须调用)
    """

    __slots__ = ("path", "dtype", "itemsize", "_fh", "_n", "_blocks")

    def __init__(self, path: str | Path, storage_dtype: str = "f32") -> None:
        self.path = Path(path)
        self.dtype = np.dtype("<f4") if storage_dtype == "f32" else np.dtype("<f8")
        self.itemsize = self.dtype.itemsize
        self._fh = open(self.path, "rb")
        try:
            size = self.path.stat().st_size
        except OSError:
            self._fh.close()
            raise
        # 与旧 np.memmap 的失败语义保持一致: 空文件 / 长度非 dtype 整数倍
        # 一律显式报错, 而不是静默当成 0 样本 (否则 shift_stream 会安静地
        # 写出 0 字节文件, estimate_pair 会静默返回 NaN, 掩盖上游 bug)。
        if size == 0:
            self._fh.close()
            raise ValueError(f"cannot open empty raw file: {self.path}")
        if size % self.itemsize != 0:
            self._fh.close()
            raise ValueError(
                f"{self.path}: size {size} is not a multiple of dtype "
                f"{self.dtype.str} ({self.itemsize} bytes)"
            )
        self._n = size // self.itemsize
        self._blocks: "OrderedDict[int, np.ndarray]" = OrderedDict()

    # ---- ndarray 兼容面 ----
    @property
    def shape(self) -> tuple[int]:
        return (self._n,)

    @property
    def ndim(self) -> int:
        return 1

    @property
    def size(self) -> int:
        return self._n

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, key: Any) -> np.ndarray:
        if isinstance(key, slice):
            if key.step not in (None, 1):
                raise ValueError("RawStream supports unit-step slices only")
            start = 0 if key.start is None else int(key.start)
            stop = self._n if key.stop is None else int(key.stop)
            if start < 0:
                start += self._n
            if stop < 0:
                stop += self._n
            start = max(0, min(start, self._n))
            stop = max(start, min(stop, self._n))
            return self.read(start, stop - start)
        idx = int(key)
        if idx < 0:
            idx += self._n
        if not 0 <= idx < self._n:
            raise IndexError(idx)
        return self.read(idx, 1)[0]

    # ---- 读取 ----
    def read(self, start: int, count: int) -> np.ndarray:
        """[start, start+count) 的只读窗口 (越界裁剪, 不补零)。

        返回值一律**不可写** (单块时是缓存块视图, 跨块时是新数组) —
        统一只读面既避免调用方误改缓存, 也避免"窄窗报错/宽窗静默"的不一致。
        调用方一律复制到自己的缓冲 (np.asarray(..., f64) / read_f64 /
        read_into) 后再使用。
        """
        start = max(0, int(start))
        count = min(int(count), self._n - start)
        if count <= 0:
            return np.empty(0, dtype=self.dtype)
        per_block = max(1, _BLOCK_BYTES // self.itemsize)
        first = start // per_block
        last = (start + count - 1) // per_block
        if first == last:
            block = self._block(first, per_block)
            off = start - first * per_block
            return block[off: off + count]
        out = np.empty(count, dtype=self.dtype)
        self._fill(out, 0, start, count, per_block)
        out.flags.writeable = False
        return out

    def read_into(self, buf: np.ndarray, offset: int, start: int,
                  count: int) -> int:
        """把 [start, start+count) 写进 buf[offset:offset+count]。

        返回实际写入的样本数 (越界时少于 count, 其余由调用方自行补零)。
        buf 必须与流同 dtype (shift_stream 的路径: 同存储精度, 无需转换)。
        """
        start = max(0, int(start))
        count = min(int(count), self._n - start)
        if count <= 0:
            return 0
        per_block = max(1, _BLOCK_BYTES // self.itemsize)
        self._fill(buf[offset: offset + count], 0, start, count, per_block)
        return count

    def _fill(self, out: np.ndarray, out_off: int, start: int, count: int,
              per_block: int) -> None:
        pos = start
        remaining = count
        written = 0
        while remaining > 0:
            idx = pos // per_block
            block = self._block(idx, per_block)
            off = pos - idx * per_block
            # max(..., 0): 文件若在打开后被截断, _block 会返回比预期短的块;
            # 这里必须保证 take >= 0 且循环必然收敛, 否则 remaining 会反向增长。
            take = min(remaining, max(0, block.shape[0] - off))
            if take <= 0:
                break
            out[out_off + written: out_off + written + take] = block[off:off + take]
            written += take
            pos += take
            remaining -= take

    def _block(self, idx: int, per_block: int) -> np.ndarray:
        cached = self._blocks.get(idx)
        if cached is not None:
            self._blocks.move_to_end(idx)
            return cached
        start = idx * per_block
        count = min(per_block, self._n - start)
        self._fh.seek(start * self.itemsize)
        raw = self._fh.read(count * self.itemsize)
        if len(raw) < count * self.itemsize:      # 文件被截断: 用实际长度
            count = len(raw) // self.itemsize
            raw = raw[: count * self.itemsize]
        block = np.frombuffer(raw, dtype=self.dtype, count=count)
        self._blocks[idx] = block
        while len(self._blocks) > _BLOCK_SLOTS:
            self._blocks.popitem(last=False)
        return block

    def close(self) -> None:
        self._blocks.clear()
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def __enter__(self) -> "RawStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"RawStream({self.path.name!r}, n={self._n}, "
                f"dtype={self.dtype.str})")


def open_source(src: Any, storage_dtype: str = "f32") -> Any:
    """打开 raw 源流: 路径 -> RawStream (有界窗口读取, 不做整文件映射);
    ndarray -> asarray。

    storage_dtype: "f32" | "f64" — 中间 raw 文件的存储精度 (见 §6.2 表:
    s32 源走 f64, 其余 f32)。ndarray 输入忽略该参数。

    历史: 旧实现返回 np.memmap; 逐字节触碰整轨会把整条文件计入进程
    WorkingSet (4×110 MB 轨 -> RSS +440 MB), 已由 work/channel_sync_memory_audit.md
    实测确认, 故改为有界窗口读取。
    """
    if isinstance(src, (str, Path)):
        return RawStream(src, storage_dtype)
    if isinstance(src, RawStream):
        return src                      # 已有流对象: 原样传递, 由持有者关闭
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


# ------------------------------------------------------- 建组用的相关强度采样
#
# 这一组函数**不产生任何延迟值**, 只回答"这两条轨是不是在录同一个声场"。
# 现行策略假定 4 条轨同源 (全部对齐到同一个锚点), 但真实 4 声道素材里
# CH1/CH2 与 CH3/CH4 常常是两支互不相干的麦克风。区分"同组/异组"需要的
# 是一个**逐帧归一化**的强度量, 而不是延迟估计。


def peak_noise_ratio(
    ref: np.ndarray,
    tgt: np.ndarray,
    *,
    lag0: int,
    search_lo: int,
    search_hi: int,
    rms_gate: float = _RMS_GATE,
) -> float:
    """单帧「相关峰 / 噪声底」比值; nan = 该帧不可判定。

    与 `gcc_phat_int` 的 `success` 判据同源 (`peak >= 2 * noise_floor`), 但
    返回比值本身 —— 建组要的是连续强度, 不是布尔。

    为什么不把 `gcc_phat_int` 重构成共用实现: 那条路径正在生产上跑, 任何改动
    都要重新证明逐位一致。这里只需**同一条公式** (同一窗口取材、同一噪声底
    表达式 `sqrt(2 ln N)/sqrt(N)`), 口径一致就够。

    RMS 门属于判据的一部分, 不是可选项: 缺了它两条静音轨会在噪声底上"相关"
    (实测 20260903_C1114: CH1 -64.3 dBFS / CH2 -68.1 dBFS 得比值 5.66),
    于是静音被聚成一个"麦克风组"。
    """
    if ref.size == 0 or tgt.size < ref.size:
        return float("nan")
    ref = ref - ref.mean()
    tgt = tgt - tgt.mean()
    r_ref = float(np.sqrt(np.mean(ref * ref)))
    r_tgt = float(np.sqrt(np.mean(tgt * tgt)))
    if r_ref < rms_gate or r_tgt < rms_gate:
        return float("nan")
    ref /= r_ref
    tgt /= r_tgt

    nfft = fft.next_fast_len(ref.size + tgt.size - 1, real=True)
    g = np.conj(fft.rfft(ref, nfft)) * fft.rfft(tgt, nfft)
    g /= np.abs(g) + _EPS_REL * float(np.max(np.abs(g))) + _EPS_ABS
    surface = fft.irfft(g, nfft)

    m_lo = max(0, lag0 + search_lo)
    m_hi = min(tgt.size - ref.size, lag0 + search_hi)
    if m_lo > m_hi:
        return float("nan")
    window = surface[m_lo: m_hi + 1]
    if window.size < 3:
        return float("nan")

    # 反相感知规则与 gcc_phat_int 一致: 负峰明显更强时以负峰为准
    v = max(float(np.max(window)), 0.0)
    neg = -float(np.min(window))
    if neg > v * 1.2:
        v = neg
    nf = math.sqrt(2.0 * math.log(nfft)) / math.sqrt(nfft)
    if nf <= 0.0:
        return float("nan")
    return v / nf


def sample_pair_snr(
    ref: Any,
    tgt: Any,
    *,
    sample_rate: int,
    frame_ms: float,
    hop_ms: float,
    search_window_ms: float,
    coarse_rate: int = 8000,
    frame_min_rms_dbfs: float = -50.0,
    samples: int = 200,
    seed: int = _SNR_SEED,
) -> dict[str, float]:
    """抽帧估计两流相关强度 —— 只服务建组, 输出不是延迟值。

    几何与 `estimate_pair` 的**粗扫阶段**完全一致 (`_coarse_slice` + 同一个
    `s_c` 搜索半窗), 因此这里的强度和生产判据是同一个量纲。

    采样是**确定性**的: `default_rng(seed)` 在固定版本 numpy 上可复现, 且
    抽样覆盖整条时间轴 (不是开头一段)。长素材上 200 帧 vs 全部 41645 帧的
    差别只是噪声, 建组不需要每帧都看。

    返回 {"samples": 可用帧数, "frames_total": 总帧数, "median"/"p25"/"p75"}。
    有效帧不足时各统计量为 nan —— 调用方必须把 nan 当"不可判定", 不许当 0。
    """
    if sample_rate % coarse_rate != 0:
        raise ValueError(
            f"sample_rate {sample_rate} 不能被 coarse_rate {coarse_rate} 整除"
        )
    q = sample_rate // coarse_rate
    f_c = int(round(frame_ms * coarse_rate / 1000.0))
    h_c = int(round(hop_ms * coarse_rate / 1000.0))
    s_c = int(math.ceil(search_window_ms * coarse_rate / 1000.0))
    rms_floor = 10.0 ** (frame_min_rms_dbfs / 20.0)

    n = min(ref.shape[0], tgt.shape[0])
    n_frames = 0 if n < f_c * q else (n - f_c * q) // (h_c * q) + 1
    out = {"samples": 0.0, "frames_total": float(n_frames),
           "median": float("nan"), "p25": float("nan"),
           "p75": float("nan")}
    if n_frames <= 0:
        return out

    k = min(int(samples), n_frames)
    if k >= n_frames:
        idx = range(n_frames)
    else:
        idx = np.random.default_rng(seed).choice(n_frames, size=k,
                                                 replace=False)
    vals: list[float] = []
    for kk in idx:
        c0 = int(kk) * h_c
        r = _coarse_slice(ref, c0, c0 + f_c, q)
        t = _coarse_slice(tgt, c0 - s_c, c0 + f_c + s_c, q)
        v = peak_noise_ratio(r, t, lag0=s_c, search_lo=-s_c, search_hi=s_c,
                             rms_gate=rms_floor)
        if v == v:
            vals.append(v)
    if not vals:
        return out
    arr = np.asarray(vals, dtype=np.float64)
    out["samples"] = float(arr.size)
    out["median"] = float(np.median(arr))
    out["p25"] = float(np.percentile(arr, 25))
    out["p75"] = float(np.percentile(arr, 75))
    return out


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
    workers: int = 1,
    gpu_frames: bool | None = None,
    _frames_override: list["FrameDelay"] | None = None,
) -> PairEstimate:
    """两阶段 + 精估 (§8.3/§8.4; ref_src/tgt_src 支持 raw 路径或 1-D ndarray)。

    ``workers > 1`` 时把**帧区间**切成若干段, 用**进程池**并行测量
    (单帧约 259 us, 其中约 60% 是 Python 开销, 线程会被 GIL 卡住, 因此
    必须是进程)。要求两个源都是**路径**: 每个子进程自己 open 自己的 raw
    流, 不共享句柄或缓存。帧之间无依赖 (`_coarse_slice` 无状态), 所以
    结果与 ``workers=1`` **逐位相同** —— 分块只改变"算在哪里", 不改变
    "算什么"; 拼接按帧区间顺序进行, 保持 frames 的时间序。

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
    if gpu_frames is None:
        _frame_mode = os.environ.get("KTS_GPU_FRAME_BATCH", "0").strip().lower()
        gpu_frames = _frame_mode not in ("", "0", "off", "false", "no")
    _hybrid = False
    if gpu_frames:
        _hybrid = (
            os.environ.get("KTS_GPU_FRAME_HYBRID", "1").strip().lower()
            not in ("", "0", "off", "false", "no")
            and int(workers or 1) > 1
            and isinstance(ref_src, (str, Path))
            and isinstance(tgt_src, (str, Path))
            and _frames_override is None
        )
        if not _hybrid:
            # single-process GPU path only
            workers = 1
    opened: list[Any] = []
    try:
        # 帧级并行 (workers>1): 只在两个源都是**路径**时可用 —— 子进程必须
        # 能自己打开数据。这里先把 n / 几何算出来以便切分, 真正的测量在
        # `_estimate_pair_parallel` 里由进程池完成, 父进程再负责聚合与精估。
        if (not _hybrid and workers and int(workers) > 1
                and isinstance(ref_src, (str, Path))
                and isinstance(tgt_src, (str, Path))):
            return _estimate_pair_parallel(
                ref_src, tgt_src,
                workers=int(workers),
                sample_rate=sample_rate,
                search_window_ms=search_window_ms,
                frame_ms=frame_ms,
                hop_ms=hop_ms,
                anchor_segment_seconds=anchor_segment_seconds,
                min_confidence=min_confidence,
                coarse_rate=coarse_rate,
                fine_phase_band_hz=fine_phase_band_hz,
                min_usable_frames=min_usable_frames,
                frame_min_rms_dbfs=frame_min_rms_dbfs,
                ref_index=ref_index,
                tgt_index=tgt_index,
                storage_dtype=storage_dtype,
            )
        ref = open_source(ref_src, storage_dtype)
        if isinstance(ref_src, (str, Path)):
            opened.append(ref)
        tgt = open_source(tgt_src, storage_dtype)
        if isinstance(tgt_src, (str, Path)):
            opened.append(tgt)
        if _hybrid:
            try:
                from .gpu_align.frame_est import estimate_frames_hybrid
                _hybrid_frames, _hybrid_stats = estimate_frames_hybrid(
                    ref_src, tgt_src, storage_dtype=storage_dtype,
                    sample_rate=sample_rate,
                    search_window_ms=search_window_ms,
                    frame_ms=frame_ms, hop_ms=hop_ms,
                    min_confidence=min_confidence,
                    frame_min_rms_dbfs=frame_min_rms_dbfs,
                    workers=int(workers))
                _frames_override = _hybrid_frames
            except Exception:                      # noqa: BLE001
                _frames_override = None
        return _estimate_pair(
            ref, tgt,
            sample_rate=sample_rate,
            search_window_ms=search_window_ms,
            frame_ms=frame_ms,
            hop_ms=hop_ms,
            anchor_segment_seconds=anchor_segment_seconds,
            min_confidence=min_confidence,
            coarse_rate=coarse_rate,
            fine_phase_band_hz=fine_phase_band_hz,
            min_usable_frames=min_usable_frames,
            frame_min_rms_dbfs=frame_min_rms_dbfs,
            ref_index=ref_index,
            tgt_index=tgt_index,
            storage_dtype=storage_dtype,
            gpu_frames=bool(gpu_frames),
            frames_override=_frames_override,
        )
    finally:
        # 只关闭本函数自己从路径打开的流; 调用方传入的流由调用方管理。
        # opened 逐个追加, 因此第二个 open 抛错时第一个也一定会被关闭。
        for stream in opened:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


def _estimate_pair_parallel(
    ref_path: Any,
    tgt_path: Any,
    *,
    workers: int,
    **kw: Any,
) -> PairEstimate:
    """把帧区间切成 `workers` 段, 用**进程池**并行测量, 再交给
    `_estimate_pair` 做聚合与精估 (两步与顺序实现共用同一段代码)。

    只有"在哪算"变了: 每帧的输入是绝对样本位置, 切分不改变任何一帧的
    输入, 因此 `frames` 与顺序实现逐位相同 (拼接按区间顺序保持时间序)。
    """
    from concurrent.futures import ProcessPoolExecutor

    sample_rate = kw["sample_rate"]
    coarse_rate = kw["coarse_rate"]
    q = sample_rate // coarse_rate
    f_c = int(round(kw["frame_ms"] * coarse_rate / 1000.0))
    h_c = int(round(kw["hop_ms"] * coarse_rate / 1000.0))
    frame = f_c * q
    hop = h_c * q
    s_c = int(math.ceil(kw["search_window_ms"] * coarse_rate / 1000.0))
    rms_floor = 10.0 ** (kw["frame_min_rms_dbfs"] / 20.0)

    storage_dtype = kw.get("storage_dtype", "f32")
    ref_probe = RawStream(ref_path, storage_dtype)
    tgt_probe = RawStream(tgt_path, storage_dtype)
    try:
        n = min(ref_probe.shape[0], tgt_probe.shape[0])
    finally:
        ref_probe.close()
        tgt_probe.close()
    n_frames = 0 if n < frame else (n - frame) // hop + 1

    params = dict(
        frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c,
        w_half=_FULL_SEARCH_HALF, q=q,
        min_confidence=kw["min_confidence"], rms_floor=rms_floor,
    )
    k_workers = max(1, min(int(workers), n_frames))
    bounds = np.linspace(0, n_frames, k_workers + 1).astype(int)
    payloads = [
        {"ref_path": ref_path, "tgt_path": tgt_path,
         "storage_dtype": storage_dtype,
         "params": dict(params, k_lo=int(bounds[i]), k_hi=int(bounds[i + 1]))}
        for i in range(k_workers)
    ]
    chunks: list[list[FrameDelay]] = []
    if k_workers <= 1 or n_frames == 0:
        chunks = [_chunk_job(payloads[0])] if payloads else [[]]
    else:
        ex = _get_estimation_pool(k_workers)
        # map 保序: chunks 顺序 == 帧区间顺序
        chunks = list(ex.map(_chunk_job, payloads))
    merged: list[FrameDelay] = [f for part in chunks for f in part]

    return estimate_pair(
        ref_path, tgt_path,
        storage_dtype=storage_dtype,
        ref_index=kw["ref_index"],
        tgt_index=kw["tgt_index"],
        workers=1,                       # 防止递归
        _frames_override=merged,
        **{k: v for k, v in kw.items()
           if k not in ("storage_dtype", "ref_index", "tgt_index")},
    )


def _est_frames(
    ref: Any,
    tgt: Any,
    *,
    k_lo: int,
    k_hi: int,
    frame: int,
    hop: int,
    h_c: int,
    f_c: int,
    s_c: int,
    w_half: int,
    q: int,
    min_confidence: float,
    rms_floor: float,
) -> list["FrameDelay"]:
    """逐帧测量 **[k_lo, k_hi)** 这一段. 帧之间完全独立 —— 每帧只读绝对
    样本位置 (`_coarse_slice` 无状态、带自身抽取余量), 因此任何切分方式都
    给出同一个结果, 这正是可以按帧并行 (进程) 的前提。"""
    out: list[FrameDelay] = []
    for k in range(k_lo, k_hi):
        center_sample = k * hop + frame // 2
        c0 = k * h_c
        ref_c = _coarse_slice(ref, c0, c0 + f_c, q)
        tgt_c = _coarse_slice(tgt, c0 - s_c, c0 + f_c + s_c, q)
        g = gcc_phat_int(
            ref_c, tgt_c, lag0=s_c, search_lo=-s_c, search_hi=s_c,
            rms_gate=rms_floor,
        )
        if not g.success or g.confidence < min_confidence:
            out.append(FrameDelay(center_sample, float("nan"), g.confidence))
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
            out.append(FrameDelay(center_sample, float("nan"), g2.confidence))
            continue
        out.append(
            FrameDelay(center_sample, float(g2.delay_samples), g2.confidence)
        )
    return out


def _chunk_job(payload: dict) -> list["FrameDelay"]:
    """进程池任务: 对一段帧区间做测量 (顶层函数, 必须可 pickle)。

    自己 open/close 各自的 raw 流 —— 不跨进程共享任何句柄或缓存。
    """
    import warnings as _w
    with _w.catch_warnings():
        _w.filterwarnings("ignore")
        ref = open_source(payload["ref_path"], payload["storage_dtype"])
        tgt = open_source(payload["tgt_path"], payload["storage_dtype"])
        try:
            return _est_frames(ref, tgt, **payload["params"])
        finally:
            for s in (tgt, ref):
                c = getattr(s, "close", None)
                if callable(c):
                    c()


def _estimate_pair(
    ref: Any,
    tgt: Any,
    *,
    sample_rate: int,
    search_window_ms: float,
    frame_ms: float,
    hop_ms: float,
    anchor_segment_seconds: float,
    min_confidence: float,
    coarse_rate: int = 8000,
    fine_phase_band_hz: tuple[float, float] = (200.0, 8000.0),
    min_usable_frames: int = 5,
    frame_min_rms_dbfs: float = -50.0,
    ref_index: int = -1,
    tgt_index: int = -1,
    storage_dtype: str = "f32",
    gpu_frames: bool = False,
    frames_override: list["FrameDelay"] | None = None,
) -> PairEstimate:
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
    _frame_kw = dict(
        frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c, w_half=w_half, q=q,
        min_confidence=min_confidence, rms_floor=rms_floor,
    )
    if frames_override is not None:
        # 已由进程池算好 (帧区间并行); 这里只做聚合与精估, 与顺序路径共用
        # 同一段代码, 因此两条路径的结果定义完全相同。
        frames: list[FrameDelay] = list(frames_override)
    elif gpu_frames:
        _gpu_frames: list[FrameDelay] | None = None
        try:
            from .gpu_align.frame_est import estimate_frames_gpu

            _gpu_frames, _gpu_stats = estimate_frames_gpu(
                ref, tgt, sample_rate=sample_rate,
                storage_dtype=storage_dtype, k_lo=0, k_hi=n_frames,
                frame=frame, hop=hop, h_c=h_c, f_c=f_c, s_c=s_c,
                w_half=w_half, q=q, min_confidence=min_confidence,
                rms_floor=rms_floor)
        except Exception:                          # noqa: BLE001
            _gpu_frames = None
        frames = (_gpu_frames if _gpu_frames is not None
                  else _est_frames(ref, tgt, k_lo=0, k_hi=n_frames,
                                   **_frame_kw))
    else:
        frames = _est_frames(ref, tgt, k_lo=0, k_hi=n_frames, **_frame_kw)

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


def measured_displacement_ms(
    frames: list[FrameDelay], sample_rate: int, trim_pct: float = 5.0
) -> float:
    """**实测位移** (ms): 合格帧延迟轨迹的稳健极差。

    为什么不用 `TrajectoryStats.spread_samples`: 那是 ``max - min``, 对单个
    离群帧敏感 —— 一个落在搜索窗边缘的帧就能把一条实际恒定的轨道判成非恒定。
    实测 (testsets A7M5 20260919_C1306, 80 min, CH3->CH4):

        usable 3985/41645, MAD 0.0208 ms, 原始极差 23.5 ms,
        p1..p99 1.77 ms, **p5..p95 0.25 ms**

    中位离散只有 1 个样本、核心 90% 只走 0.25 ms, 却被极少数离群帧拉到
    23.5 ms。稳健极差取 p5..p95, 对离群帧不敏感。帧数 <20 时退化为原始
    极差, 因为此时裁剪没有统计意义。

    返回的是**观测到的位移**, 不是任何外推预测值。
    """
    d = np.array([f.delay_samples for f in frames
                  if f.delay_samples == f.delay_samples], dtype=np.float64)
    if d.size == 0:
        return float("nan")
    if d.size < 20 or not (0.0 < trim_pct < 50.0):
        rng = float(d.max() - d.min())
    else:
        lo, hi = np.percentile(d, [trim_pct, 100.0 - trim_pct])
        rng = float(hi - lo)
    return rng * 1000.0 / sample_rate


def classify_constant(
    stats: TrajectoryStats, *, mad_max_ms: float, max_ppm: float,
    sample_rate: int, drift_min_ms: float = 0.0,
    displacement_ms: float | None = None,
) -> bool:
    """恒定性判定: **实测离散** + **实测位移**。

    取代原来的 "MAD + 原始极差 + 最小二乘斜率外推" 三门。

    为什么换 (2026-10-16 实测, I:\\compact 263 个 4×mono + 两个 80 min 长文件):

    * 旧第三道门是 ``|drift_ppm| > max_ppm 且 |全片预测漂移| > drift_min_ms``,
      即把拟合斜率**线性外推到整条流**。那是**预测**位移, 不是观测位移。
      后果: 62 s 片段 MAD 只有 0.042 ms, 却因 49 ppm 外推 1.9 ms 被判非恒定;
      而同一批素材里"已对齐"轨的偶然斜率也能到 5~30 ppm。判据脆弱。
    * 旧第二道门用 ``max-min`` 极差, 对单个离群帧敏感 (见
      `measured_displacement_ms` 的实测数字)。

    现在的门, 前三项都是**实测量**:
      1. ``mad_ms <= mad_max_ms`` —— 中位绝对离差 (抗离群)
      2. ``displacement_ms <= mad_max_ms`` —— 稳健极差 p5..p95 (抗离群)
      3. 离群门槛 ``全范围 <= mad_max_ms`` —— 稳健极差会掩盖中间的大洞/跳变,
         所以再要求**观测到的全范围**不超过**同一道门** (1 倍, 与 MAD 门同尺),
         把"中间真的跳了"拦下来。
         (2026-10-20: 此处原写 ``2*mad_max_ms``, 与实现不符; 实现一直是 1 倍,
         以代码为准 —— 按 2 倍读会把门宽算错一倍。)
      4. ``drift_min_ms > 0`` 时, 斜率只作**绝对位移的补充证据**:
         需 ``|drift_ppm| > max_ppm`` **且** 实测稳健位移 ``> drift_min_ms``
         才判非恒定 —— 斜率必须被观测位移证实, 不能靠外推。

    证据不足 (无合格帧 / 轨迹太短) 一律判非恒定, 与旧行为一致。
    """
    if stats.mad_ms != stats.mad_ms:
        return False                       # 没有可算的 MAD: 不可判
    if stats.mad_ms > mad_max_ms:
        return False
    disp = (displacement_ms if displacement_ms is not None
            and displacement_ms == displacement_ms else None)
    if stats.spread_samples != stats.spread_samples:
        return False
    full_ms = stats.spread_samples * 1000.0 / sample_rate
    if disp is not None:
        if disp > mad_max_ms:
            return False
        # 离群门槛: 稳健极差会掩盖中间的大洞/跳变, 所以要求**观测到的**全范围
        # 不超过 mad_max_ms (与 MAD 门同尺)。样本 <20 时不适用 —— 此时稳健
        # 极差已退化成原始极差, 两者相等, 这一项没有额外信息 (实测
        # 20260903_C1156 只有 2 个合格帧, 曾被更宽的系数误杀)。
        if stats.usable_frames >= 20 and full_ms > mad_max_ms:
            return False
    else:
        # 调用方没给稳健位移: 退回原始极差 (旧行为), 不静默放宽
        if full_ms > mad_max_ms:
            return False
    if drift_min_ms > 0.0 and abs(stats.drift_ppm) > max_ppm:
        # 斜率超 ppm 门时, 要求**预测位移与实测位移都**具有材料性才判非恒定。
        #
        # 为什么是 AND 而不是 OR:
        #   * 预测位移 (线性拟合) 在**短轨迹**上不可靠 —— 合格帧稀少时斜率
        #     被少数点放大 (实测 C1197 3->1: 14 个合格帧、26.58 ppm、
        #     预测仅 0.066 ms, 而实测稳健极差 0.104 ms)。旧实现只用预测,
        #     于是这类轨道能通过。
        #   * 预测位移在**长轨迹**上才是那个会被线性外放到荒谬值的量 ——
        #     这正是要修的问题 (62 s 片段 MAD 0.042 ms 却外推出 1.9 ms)。
        # 取 AND: 只有"拟合说有大漂移"**且**"数据里确实看到大位移"才算
        # 非恒定。任一证据缺失时保守方向由 MAD 门与全范围门兜住。
        pred = abs(stats.drift_total_ms) if stats.drift_total_ms \
            == stats.drift_total_ms else 0.0
        observed = abs(disp if disp is not None else full_ms)
        if pred > drift_min_ms and observed > drift_min_ms:
            return False
        # 否则: 斜率超门但实测位移没有材料性 -> 视作量化噪声, 放行
    return True


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


# ------------------------------------------------- 长镜头窗级漂移测量 (S1)
#
# 为什么需要它: 生产帧几何 (200 ms 帧 / 100 ms 跳) 在 30 s 计划窗里平均只有
# 0.048 个可用帧, 长素材 (80 min) 上帧级聚合测不出逐窗延迟; 研究口径是
# **直接对窗做相关**。本节只移植口径, 不 import 研究脚本
# (tests/regression/long_align_tools/track_drift.py / long_align.py):
#
#   * 12 kHz 分块抽取缓存 (coarse_decimate): 内存只与 coarse 数组成正比
#     (80 min / 48 kHz / 12 kHz ≈ 231 MB/轨), 不触碰全速率整文件;
#   * 窗级 12 kHz 归一化线性互相关: 首窗 ±search_window_ms 全搜索, 之后每窗
#     只在前一**有效** lag 的 ±(96 + 8×t_min) 采样内找峰 (连续性约束 ——
#     逐窗独立测量会因内容分支切换跳变 ±130 采样, 已在 C1305/C1306 实测);
#   * 全速率 refine_segment_seconds 段上 ±refine_radius_samples 逐整数点积
#     精化, 取 |CC| 最大的整数 lag;
#   * 弱峰 (coarse |CC| < min_cc) 不采纳, 且**不更新**连续性预测;
#   * 每窗记录带符号峰方向 (polarity); 极性不影响滞后 (始终取 |CC| 峰).
#
# 数据约定与本模块一致: delay > 0 = 目标轨比参考**晚到** (修正 = 整体前移;
# out[n] = in[n + delay])。合成校准: 注入 +123 -> 123.0, 注入 −200 -> −200.0。

# 分块粗率抽取: 块长 (coarse 样本) 与两侧丢弃余量 (coarse 样本)。
# decimate(ftype="fir", zero_phase=True) 的 FIR 半长为 10*q 输入样本
# (q <= 12 时为 120 采样), 32 个 coarse 样本的余量大于它, 因此分块结果与
# "整文件一次 decimate" 数值一致 (单测钉住; 余量内的暂态全部丢弃)。
# 2026-11-01 perf: 100 s -> 25 s chunks. coarse_decimate 的分块边界在两侧
# 多读 margin 后丢弃，因此结果与整文件一次 decimate 逐位一致（单测钉住）；
# 更小的 chunk 只减少每线程的 transient f64 缓冲（~38 MB -> ~10 MB），
# 在 2 个 target 并行测量时直接降低峰值 RSS。
_COARSE_CHUNK_SECONDS = 25.0
_COARSE_MARGIN = 32
# 首窗全搜索后的全速率精化半径 (研究口径 ±12, 比窗内连续性精化 ±8 略宽):
# 12 kHz 粗峰量化误差最大 q 个全速率样本, 首窗没有连续性约束兜底。
_INITIAL_REFINE_RADIUS = 12
# 首窗失败后继续尝试的窗数 (研究: 首窗 |CC| 不足可退化用第二窗).
_INITIAL_ATTEMPTS = 2

#: 帧级估计的**可复用进程池** (2026-11-01 perf). 之前每次 estimate_pair 都新建
#: 12 进程池; 80 min 素材一次 run 会调用 18-36 次, Windows 上每次 spawn 的
#: 启动成本被重复支付。池只决定"算在哪里", 帧输入/顺序/结果与新建池逐位相同。
_PROC_POOL: Any = None
_PROC_POOL_WORKERS = 0
_PROC_POOL_LOCK = threading.Lock()


def _get_estimation_pool(workers: int) -> Any:
    """Get/reuse the frame-estimation process pool (grown on demand)."""
    from concurrent.futures import ProcessPoolExecutor

    global _PROC_POOL, _PROC_POOL_WORKERS
    with _PROC_POOL_LOCK:
        if _PROC_POOL is not None and workers <= _PROC_POOL_WORKERS:
            return _PROC_POOL
        if _PROC_POOL is not None:
            _PROC_POOL.shutdown(wait=True)
        _PROC_POOL = ProcessPoolExecutor(max_workers=workers)
        _PROC_POOL_WORKERS = workers
        return _PROC_POOL


def shutdown_estimation_pool() -> None:
    """Release the reusable estimation pool (called at end of channel-sync)."""
    try:
        from .gpu_align.frame_est import clear_frame_coarse_cache
        clear_frame_coarse_cache()
    except Exception:                                  # noqa: BLE001
        pass
    global _PROC_POOL, _PROC_POOL_WORKERS
    with _PROC_POOL_LOCK:
        pool, _PROC_POOL = _PROC_POOL, None
        _PROC_POOL_WORKERS = 0
    if pool is not None:
        pool.shutdown(wait=True)


def coarse_decimate(
    src: Any,
    *,
    sample_rate: int,
    coarse_rate: int = 12000,
    storage_dtype: str = "f32",
    chunk_seconds: float = _COARSE_CHUNK_SECONDS,
) -> np.ndarray:
    """把 1-D 源 (raw 路径或 ndarray) 分块抽取到 coarse_rate, 返回 float32。

    用途: 长素材窗级测量只需要 12 kHz 粗扫缓存 —— 一次生成、多次复用
    (`measure_window_delays(ref_decimated=..., tgt_decimated=...)`),
    避免每个 target 重算。路径输入走 `RawStream` 有界窗口读取, **不映射
    全速率整文件**; 内存峰值 ≈ 一个输入分块 (float64) + 输出 coarse 数组
    (80 min / 48 kHz / 12 kHz = 57.7 M 样本 ≈ 231 MB/轨)。

    分块边界: 每块两侧多读 `_COARSE_MARGIN` 个 coarse 样本再丢弃,
    因此与"整文件一次 decimate(ftype='fir', zero_phase=True)"数值一致
    (暂态全部落在丢弃区; 单测 `long_take.分块抽取 == 整文件` 钉住)。
    输出样本 j 对应输入位置 j*q (q = sample_rate // coarse_rate)。

    ndarray 输入不做拷贝以外的 I/O; raw 路径的存储精度由 storage_dtype 指定
    (与 `decode_stream` 的中间文件约定一致: f32 / f64)。
    """
    if sample_rate <= 0 or coarse_rate <= 0:
        raise ValueError("sample_rate / coarse_rate 必须为正")
    if sample_rate % coarse_rate != 0:
        raise ValueError(
            f"sample_rate {sample_rate} 不能被 coarse_rate {coarse_rate} 整除"
        )
    q = sample_rate // coarse_rate
    stream = open_source(src, storage_dtype)
    opened = isinstance(src, (str, Path))
    try:
        n = int(stream.shape[0])
        n_out = (n + q - 1) // q
        out = np.zeros(n_out, dtype=np.float32)
        chunk = max(1, int(chunk_seconds * coarse_rate))
        margin = _COARSE_MARGIN
        for c0 in range(0, n_out, chunk):
            cnt = min(chunk, n_out - c0)
            in_lo = (c0 - margin) * q
            in_hi = (c0 + cnt + margin) * q
            buf = np.zeros(in_hi - in_lo, dtype=np.float64)
            lo = max(0, in_lo)
            hi = min(n, in_hi)
            if hi > lo:
                buf[lo - in_lo: lo - in_lo + (hi - lo)] = np.asarray(
                    stream[lo:hi], dtype=np.float64
                )
            dec = signal.decimate(buf, q, ftype="fir", zero_phase=True)
            seg = dec[margin: margin + cnt]
            if seg.size:
                out[c0: c0 + seg.size] = np.asarray(seg, dtype=np.float32)
        return out
    finally:
        if opened:
            stream.close()


def _check_decimated(arr: Any, n_full: int, q: int, name: str) -> np.ndarray:
    """校验调用方传入的 coarse 缓存: 1-D 且长度 == ceil(n_full/q)。

    长度不符说明缓存不是本流/不是同一几何生成的 —— 显式报错, 不静默截断
    (静默会把滞后整体平移到错误分支)。
    """
    out = np.asarray(arr)
    if out.ndim != 1:
        raise ValueError(f"{name}: 期望 1-D coarse 缓存, 得到 shape {out.shape}")
    expected = (int(n_full) + q - 1) // q
    if out.shape[0] != expected:
        raise ValueError(
            f"{name}: coarse 缓存长度 {out.shape[0]} != 期望 {expected} "
            f"(n={int(n_full)}, q={q})"
        )
    return out


def _coarse_cc_peak(
    a12: np.ndarray,
    b12: np.ndarray,
    start_c: int,
    count_c: int,
    lag_lo: int,
    lag_hi: int,
    q: int,
) -> tuple[int, float, float] | None:
    """12 kHz 窗级归一化线性互相关: 在 [lag_lo, lag_hi] 内取 |CC| 最大峰。

    a12/b12: 全片 coarse 缓存 (a = 参考, b = 目标); start_c/count_c: 窗在
    coarse 坐标的起点/长度。相关面 surf[m] = Σ a[n]·b[n-m] 的峰位于
    m = -delay, 因此 lag = -m*q —— 与模块数据约定一致 (delay > 0 = 目标晚到)。
    返回 (lag48, signed_cc, abs_cc); 退化 (静音/窗太短/无有效 lag) 返回 None。

    signed_cc 归一化到 [-1, 1] (Cauchy-Schwarz): 其符号就是该窗的极性关系
    (负 = 目标反相), 取峰一律用 |CC| (极性不改变滞后)。
    """
    a = np.asarray(a12[start_c: start_c + count_c], dtype=np.float64)
    b = np.asarray(b12[start_c: start_c + count_c], dtype=np.float64)
    n = min(a.size, b.size)
    if n < 16:
        return None
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < _RMS_GATE or nb < _RMS_GATE:
        return None
    nfft = fft.next_fast_len(2 * n - 1, real=True)
    prod = fft.rfft(a, nfft) * np.conj(fft.rfft(b, nfft))
    surf = fft.irfft(prod, nfft)
    # lag = -m*q; 线性相关有效范围 |m| <= n-1 (nfft >= 2n-1, 不回绕)
    m_lo = max(-(n - 1), int(math.ceil(-lag_hi / q)))
    m_hi = min(n - 1, int(math.floor(-lag_lo / q)))
    if m_lo > m_hi:
        return None
    idx = np.arange(m_lo, m_hi + 1, dtype=np.int64)
    vals = surf[idx]                     # 负索引 = 回绕点, 即 m < 0
    k = int(np.argmax(np.abs(vals)))
    den = na * nb
    signed = float(vals[k]) / den
    return float(-int(idx[k]) * q), signed, abs(signed)


def _refine_window_peak(
    ref: Any,
    tgt: Any,
    *,
    start_sample: int,
    window_samples: int,
    coarse_lag: int,
    radius: int,
    segment_samples: int,
) -> tuple[int, float]:
    """窗中心段上的全速率整数 lag 精化 (研究 refine_48k 口径)。

    在 coarse_lag ± radius 内逐整数 lag m, 取 |corr(ref[k], tgt[k+m])| 最大
    的 m —— 与模块约定一致: tgt[k+m] ≈ ref[k] 时 m 就是目标相对参考的延迟
    (目标晚到 m, 修正 = 前移 m)。段 = 窗中心 segment_samples (研究 2 s),
    研究口径下段必须完整落在窗内 (越界候选跳过)。

    返回 (lag, |cc|); 精化退化 (静音/段比窗长) 时保留粗值并返回质量 0.0
    (研究口径: 精化失败不换 lag)。
    """
    seg = int(segment_samples)
    if seg < 8 or window_samples < seg:
        return int(coarse_lag), 0.0
    c0 = start_sample + window_samples // 2 - seg // 2
    if c0 < 0:
        c0 = 0
    ref_seg = read_f64(ref, c0, seg)          # 越界补零 (边界窗)
    av = ref_seg - ref_seg.mean()
    na = float(np.linalg.norm(av))
    if na < _RMS_GATE:
        return int(coarse_lag), 0.0
    # 一次有界读取覆盖全部候选偏移 (以粗略 lag 为中心 ±radius):
    # tgt_span[j] 对应该流绝对位置 c0 + coarse_lag - radius + j
    center = int(coarse_lag)
    tgt_span = read_f64(tgt, c0 + center - radius, seg + 2 * radius)
    win_end = int(start_sample) + int(window_samples)
    best_m: int | None = None
    best_val = 0.0
    for m in range(center - radius, center + radius + 1):
        b0 = c0 + m
        if b0 < 0 or b0 + seg > win_end:
            continue                          # 研究口径: 段必须完整落在窗内
        j = radius + (m - center)
        bv = tgt_span[j: j + seg]
        bv = bv - bv.mean()
        den = na * float(np.linalg.norm(bv))
        if den < 1e-12:
            continue
        val = float(np.dot(av, bv) / den)
        if best_m is None or abs(val) > abs(best_val):
            best_m = m
            best_val = val
    if best_m is None:
        return int(coarse_lag), 0.0
    return int(best_m), abs(float(best_val))


def gpu_align_mode() -> str:
    """Normalised GPU alignment mode ("fp32"/"fp64"/"")."""
    mode = os.environ.get("KTS_GPU_ALIGN", "0").strip().lower()
    if mode in ("", "0", "off", "false", "no"):
        return ""
    if mode == "fp64":
        return "fp64"
    return "fp32"


def gpu_align_enabled() -> bool:
    """True when the optional GPU alignment path is selected by env."""
    mode = os.environ.get("KTS_GPU_ALIGN", "0").strip().lower()
    return mode not in ("", "0", "off", "false", "no")


def prewarm_gpu_align(mode: str | None = None) -> bool:
    """Compile/warm the optional GPU window path in the *calling* thread.

    Reuses the run-level session when one is active; otherwise preserves the
    old standalone create/close behaviour.  Returns True when warmed.
    """
    sess = None
    owns = True
    try:
        from .gpu_align.window_cc import WindowCCEngine
        from .gpu_align import runtime as _gpu_runtime

        ctx = _gpu_runtime.current_run()
        if ctx is not None:
            sess = ctx.session()
            owns = False
        else:
            from .gpu_align import AlignGpuSession
            sess = AlignGpuSession.create(mode=mode)
        if sess is None:
            return False
        eng = WindowCCEngine(
            sess.backend, np.zeros(2048), np.zeros(2048),
            policy=sess.policy)
        try:
            eng.warmup()
        finally:
            eng.close()
        return True
    except Exception:                                      # noqa: BLE001
        return False
    finally:
        if sess is not None and owns:
            try:
                sess.close()
            except Exception:                              # noqa: BLE001
                pass


def measure_window_delays(
    ref_src: Any,
    tgt_src: Any,
    *,
    sample_rate: int,
    window_seconds: float = 60.0,
    hop_seconds: float = 10.0,
    coarse_rate: int = 12000,
    search_window_ms: float = 250.0,
    continuity_radius_samples: int = 96,
    continuity_radius_per_minute: int = 8,
    min_cc: float = 0.06,
    refine_radius_samples: int = 8,
    refine_segment_seconds: float = 2.0,
    initial_seconds: float = 120.0,
    ref_decimated: Any | None = None,
    tgt_decimated: Any | None = None,
    workers: int = 1,
    storage_dtype: str = "f32",
    gpu_cc: bool = False,
    gpu_align: bool | None = None,
) -> dict[str, Any]:
    """长镜头**窗级连续性漂移测量** (S1 测量原语; 只测量, 不构造曲线/不施加)。

    参考实现口径 (tests/regression 的研究脚本, 已在 C1305/C1306 上验证):
    12 kHz 池化粗扫 + 48 kHz 局部精化 + 连续性约束跟踪。生产帧几何在 30 s
    窗内只有约 0.048 个可用帧, 因此长镜头时变路径必须直接对窗相关。

    流程:
      1. 建 12 kHz coarse 缓存 (`coarse_decimate`; 也可由调用方传入
         `ref_decimated`/`tgt_decimated` 复用);
      2. 首窗 (前 `initial_seconds` 或整条取小) 做 ±`search_window_ms` 全
         搜索 + 全速率精化; coarse |CC| < `min_cc` 时退化用第二窗; 两窗都
         不足则 `initial.valid=False`, 全部窗口 valid=False;
      3. 逐窗: 只在前一**有效** lag 的 ±(`continuity_radius_samples` +
         `continuity_radius_per_minute`×t_min) 采样内找 |CC| 最大峰; 弱峰
         (< `min_cc`) 记 valid=False 且**不更新**预测 (连续性靠上一有效值);
      4. 采纳窗在窗中心 `refine_segment_seconds` 段上 ±`refine_radius_samples`
         逐整数点积精化, `delay_samples` = 该整数 lag;
         `confidence` = min(coarse|CC|,1) × max(refine|CC|,0) (研究口径)。

    数据约定: delay > 0 = 目标轨比参考**晚到**, 与 `estimate_pair` /
    `shift_stream` 同向 (`out[n] = in[n + delay]`)。极性只记录在
    `polarity` (±1, 带符号峰方向), 不改变滞后。

    确定性: 固定 FFT 尺寸 + 整数窗位, 无 RNG; 同一输入两次运行结果逐位一致。
    `workers` 只为调用面稳定保留: 本函数**顺序执行**（每窗的搜索半径依赖
    前一有效 lag，连续性就是顺序语义），结果与 worker 数无关。S5 的并行
    落在**调用层**: 绑定候选对互相独立用线程池并测；round-2 残差窗互相独立
    用线程池并测（`core.long_take.measure_residual_curve`）。
    路径输入不整文件载入: 全速率只用于窗中心段的 `read_f64` 有界读取。

    返回::

        {"sample_rate": int, "window_samples": int, "hop_samples": int,
         "n_samples": int, "n_windows": int,
         "initial": {"lag": int|None, "cc": float, "coarse_cc": float,
                     "refine_quality": float, "valid": bool, "polarity": int,
                     "start_sample": int, "attempts": [...]},
         "windows": [{"start_sample": int, "center_sample": int,
                      "delay_samples": float|None, "confidence": float,
                      "valid": bool, "coarse_cc": float, "polarity": int,
                      "search_radius_samples": int}, ...],
         "decimated_rate": int, "rejected_windows": int,
         "workers_effective": 1}
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate 必须为正")
    if sample_rate % coarse_rate != 0:
        raise ValueError(
            f"sample_rate {sample_rate} 不能被 coarse_rate {coarse_rate} 整除"
        )
    if window_seconds <= 0 or hop_seconds <= 0:
        raise ValueError("window_seconds / hop_seconds 必须为正")
    q = sample_rate // coarse_rate
    win = int(round(window_seconds * sample_rate))
    hop = int(round(hop_seconds * sample_rate))
    seg_samples = int(round(refine_segment_seconds * sample_rate))
    search_samples = int(round(search_window_ms * sample_rate / 1000.0))
    radius0 = max(0, int(continuity_radius_samples))
    radius_per_min = max(0, int(continuity_radius_per_minute))
    min_cc = float(min_cc)
    _t_measure = time.perf_counter()
    _owns_align_session = True
    _ctx = None
    _surf_stop = threading.Event()
    _surf_thread = None
    _refine_batch_eng = None
    try:
        from .gpu_align import runtime as _gpu_runtime
        _ctx = _gpu_runtime.current_run()
    except Exception:                                  # noqa: BLE001
        _ctx = None

    opened: list[Any] = []
    try:
        ref = open_source(ref_src, storage_dtype)
        if isinstance(ref_src, (str, Path)):
            opened.append(ref)
        tgt = open_source(tgt_src, storage_dtype)
        if isinstance(tgt_src, (str, Path)):
            opened.append(tgt)
        n = min(int(ref.shape[0]), int(tgt.shape[0]))

        if ref_decimated is None:
            _build_ref = lambda: coarse_decimate(
                ref, sample_rate=sample_rate, coarse_rate=coarse_rate,
                storage_dtype=storage_dtype)
            if _ctx is not None:
                _key = _ctx.coarse_key(
                    ref_src, sample_rate=sample_rate,
                    coarse_rate=coarse_rate, storage_dtype=storage_dtype)
                ref12 = _ctx.coarse_get_or_build(_key, _build_ref)
            else:
                ref12 = _build_ref()
        else:
            ref12 = _check_decimated(ref_decimated, int(ref.shape[0]), q,
                                     "ref_decimated")
        if tgt_decimated is None:
            _build_tgt = lambda: coarse_decimate(
                tgt, sample_rate=sample_rate, coarse_rate=coarse_rate,
                storage_dtype=storage_dtype)
            if _ctx is not None:
                _key = _ctx.coarse_key(
                    tgt_src, sample_rate=sample_rate,
                    coarse_rate=coarse_rate, storage_dtype=storage_dtype)
                tgt12 = _ctx.coarse_get_or_build(_key, _build_tgt)
            else:
                tgt12 = _build_tgt()
        else:
            tgt12 = _check_decimated(tgt_decimated, int(tgt.shape[0]), q,
                                     "tgt_decimated")

        # 可选 GPU 粗相关: 只把窗级 coarse CC 的 FFT 放到 cuFFT(float64);
        # 结果记录仍按 CPU 口径 round 到 6 位小数, 整数峰/refine 逻辑不变。
        _gpu = None
        if gpu_cc:
            try:
                from . import gpu_cc as _gpu_mod
                if _gpu_mod.available():
                    _gpu = _gpu_mod.prepare(ref12, tgt12)
            except Exception:                          # noqa: BLE001
                _gpu = None

        # Optional new GPU architecture.  FP32 is the default when the
        # program enables CUDA acceleration; fp64 remains available.
        # Precompute every regular window's full-search surface once, then do
        # the continuity peak selection on the CPU.  This removes the
        # per-window launch/host round-trip that made the old gpu_cc path
        # barely useful for sequential windows.
        _align_sess = None
        _align_eng = None
        _surf_windows = None
        _surf_valid = None
        _surf_lo = _surf_hi = 0
        if gpu_align is None:
            _env = os.environ.get("KTS_GPU_ALIGN", "0").strip().lower()
            gpu_align = _env not in ("", "0", "off", "false", "no")
        _owns_align_session = True
        if gpu_align:
            try:
                from .gpu_align import AlignGpuSession
                from .gpu_align.window_cc import WindowCCEngine

                _mode = os.environ.get("KTS_GPU_ALIGN", "").strip().lower()
                if _mode in ("", "0", "off", "false", "no"):
                    _mode = "fp32"
                if _ctx is not None:
                    _align_sess = _ctx.session()
                    _owns_align_session = False
                else:
                    _align_sess = AlignGpuSession.create(mode=_mode)
                if _align_sess is not None:
                    _align_eng = WindowCCEngine(
                        _align_sess.backend, ref12, tgt12,
                        policy=_align_sess.policy)
                    _align_eng.warmup()
                    if _ctx is not None:
                        _ctx.metrics.record_engine(created=True)
            except Exception:                          # noqa: BLE001
                _align_sess = _align_eng = None

        _refine_batch_eng = None
        _use_batch_refine = (
            _align_sess is not None
            and os.environ.get("KTS_GPU_REFINE_BATCH", "0").strip().lower()
            not in ("", "0", "off", "false", "no"))
        if _use_batch_refine:
            try:
                from .gpu_align.refine_batch import RefineBatchEngine
                _refine_batch_eng = RefineBatchEngine(
                    _align_sess.backend, policy=_align_sess.policy,
                    exact=os.environ.get(
                        "KTS_GPU_REFINE_EXACT", "1").strip().lower()
                    not in ("", "0", "off", "false", "no"))
                if _ctx is not None:
                    _ctx.metrics.record_engine(created=True)
            except Exception:                          # noqa: BLE001
                _refine_batch_eng = None
                _use_batch_refine = False

        def _coarse_peak(start_c: int, count_c: int, lag_lo: int,
                         lag_hi: int) -> tuple | None:
            if _gpu is not None:
                try:
                    from . import gpu_cc as _gpu_mod
                    got = _gpu_mod.coarse_cc_peak(
                        _gpu, start_c, count_c, lag_lo, lag_hi, q)
                except Exception:                      # noqa: BLE001
                    got = None
                if got is not None:
                    return got
            return _coarse_cc_peak(ref12, tgt12, start_c, count_c,
                                   lag_lo, lag_hi, q)

        n_windows = 0 if n < win else (n - win) // hop + 1
        _surf_q = None
        _surf_failed = False
        _surf_cur_lo = _surf_cur_end = 0
        _surf_batch = None
        _surf_batch_valid = None
        _surf_batch_lo = 0
        _surf_batch_hi = 0
        _pipeline_surfaces = (
            _align_eng is not None and n_windows > 0
            and os.environ.get("KTS_GPU_SURFACE_PIPELINE", "0").strip().lower()
            not in ("", "0", "off", "false", "no"))
        if _align_eng is not None and n_windows > 0:
            try:
                _surf_c = int(math.ceil(search_samples / q))
                _reqs = [(int(round(w * hop / q)),
                          int(round(win / q))) for w in range(n_windows)]
                if _pipeline_surfaces:
                    _chunk = max(1, int(os.environ.get(
                        "KTS_GPU_SURFACE_BATCH", "32") or 32))
                    _surf_q = queue.Queue(maxsize=2)

                    def _surface_producer() -> None:
                        try:
                            for _lo in range(0, len(_reqs), _chunk):
                                if _surf_stop.is_set():
                                    return
                                _w, _m_lo, _m_hi, _v = (
                                    _align_eng.surface_windows(
                                        _reqs[_lo:_lo + _chunk],
                                        lag_lo_c=-_surf_c,
                                        lag_hi_c=_surf_c))
                                while not _surf_stop.is_set():
                                    try:
                                        _surf_q.put((_lo, _w, _v,
                                                     _m_lo, _m_hi),
                                                    timeout=0.2)
                                        break
                                    except queue.Full:
                                        continue
                        except Exception:              # noqa: BLE001
                            try:
                                _surf_q.put(("error", None, None, 0, 0),
                                            timeout=0.5)
                            except Exception:          # noqa: BLE001
                                pass

                    _surf_thread = threading.Thread(
                        target=_surface_producer, name="gpu-surfaces",
                        daemon=True)
                    _surf_thread.start()
                else:
                    (_surf_windows, _surf_lo, _surf_hi,
                     _surf_valid) = _align_eng.surface_windows(
                        _reqs, lag_lo_c=-_surf_c, lag_hi_c=_surf_c)
                    if (_surf_windows.ndim != 2
                            or _surf_windows.shape[0] != n_windows):
                        _surf_windows = None
            except Exception:                          # noqa: BLE001
                _surf_windows = None
                _surf_q = None

        _surface_m_lo = 0
        _surface_m_hi = 0

        def _surface_for(w: int) -> tuple | None:
            nonlocal _surf_cur_lo, _surf_cur_end, _surf_batch, \
                _surf_batch_valid, _surf_batch_lo, _surf_failed, \
                _surface_m_lo, _surface_m_hi
            if _surf_windows is not None:
                if w >= _surf_windows.shape[0]:
                    return None
                return (_surf_windows[w:w + 1], _surf_valid[w:w + 1],
                        _surf_lo, _surf_hi)
            if _surf_q is None or _surf_failed:
                return None
            while w >= _surf_cur_end:
                try:
                    item = _surf_q.get(timeout=60.0)
                except Exception:                      # noqa: BLE001
                    _surf_failed = True
                    return None
                if item[0] == "error":
                    _surf_failed = True
                    return None
                (_surf_batch_lo, _surf_batch, _surf_batch_valid,
                 _surface_m_lo, _surface_m_hi) = item
                _surf_batch_lo = int(_surf_batch_lo)
                _surf_cur_lo = _surf_batch_lo
                _surf_cur_end = _surf_batch_lo + _surf_batch.shape[0]
                if _surf_cur_end <= w:
                    continue
            i = w - _surf_batch_lo
            if i < 0 or i >= _surf_batch.shape[0]:
                return None
            return (_surf_batch[i:i + 1], _surf_batch_valid[i:i + 1],
                    _surface_m_lo, _surface_m_hi)

        initial: dict[str, Any] = {
            "lag": None, "cc": 0.0, "coarse_cc": 0.0, "refine_quality": 0.0,
            "valid": False, "polarity": 1, "start_sample": 0, "attempts": [],
        }
        prev: int | None = None

        # ---- 首窗: 全搜索定位初始 lag (研究: 前 initial_seconds 全带搜索)
        for attempt in range(_INITIAL_ATTEMPTS):
            start = attempt * hop
            avail = n - start
            if avail <= 0:
                break
            span = min(int(round(initial_seconds * sample_rate)), avail)
            span_c = int(round(span / q))
            if span_c < 16:
                break
            peak = _coarse_peak(
                int(round(start / q)), span_c,
                -search_samples, search_samples)
            if peak is None:
                continue
            lag_c, signed, abs_cc = peak
            initial["attempts"].append({
                "start_sample": int(start), "lag": int(lag_c),
                "coarse_cc": round(float(abs_cc), 6),
                "search_samples": int(search_samples),
            })
            if abs_cc < min_cc:
                continue
            win_eff = min(win, avail)
            lag, quality = _refine_window_peak(
                ref, tgt, start_sample=start, window_samples=win_eff,
                coarse_lag=lag_c, radius=_INITIAL_REFINE_RADIUS,
                segment_samples=seg_samples,
            )
            initial.update({
                "lag": int(lag),
                "cc": float(min(abs_cc, 1.0) * max(quality, 0.0)),
                "coarse_cc": float(abs_cc),
                "refine_quality": float(quality),
                "valid": True,
                "polarity": 1 if signed >= 0 else -1,
                "start_sample": int(start),
            })
            prev = int(lag)
            break

        # ---- 逐窗: 连续性约束搜索 + 全速率精化
        windows: list[dict[str, Any]] = []
        _pending_refine: list[tuple[int, int, int, float]] = []
        for w in range(n_windows):
            start = w * hop
            t_min = start / sample_rate / 60.0
            radius = radius0 + int(round(radius_per_min * t_min))
            row: dict[str, Any] = {
                "start_sample": int(start),
                "center_sample": int(start + win // 2),
                "delay_samples": None,
                "confidence": 0.0,
                "valid": False,
                "coarse_cc": 0.0,
                "polarity": 1,
                "search_radius_samples": int(radius),
            }
            if prev is not None:
                peak = None
                _surf_item = _surface_for(w)
                if _surf_item is not None:
                    try:
                        _win_rows, _win_valid, _win_m_lo, _win_m_hi = (
                            _surf_item)
                        n_c = int(round(win / q))
                        m_lo = max(-(n_c - 1),
                                   int(math.ceil(-(prev + radius) / q)))
                        m_hi = min(n_c - 1,
                                   int(math.floor(-(prev - radius) / q)))
                        if (m_lo <= m_hi and _win_m_lo <= m_lo
                                and m_hi <= _win_m_hi):
                            _pk = _align_eng.peaks_from_windows(
                                _win_rows, _win_valid,
                                m_lo=_win_m_lo,
                                lag_lo_c=m_lo, lag_hi_c=m_hi,
                            )[0]
                            if _pk is not None:
                                peak = (float(_pk.lag * q),
                                        float(_pk.signed_cc),
                                        float(_pk.abs_cc))
                    except Exception:                  # noqa: BLE001
                        peak = None
                if peak is None:
                    peak = _coarse_peak(
                        int(round(start / q)), int(round(win / q)),
                        prev - radius, prev + radius)
                if peak is not None:
                    lag_c, signed, abs_cc = peak
                    row["coarse_cc"] = round(float(abs_cc), 6)
                    row["polarity"] = 1 if signed >= 0 else -1
                    if abs_cc >= min_cc:
                        if _use_batch_refine:
                            # defer full-rate correlation to one GPU batch;
                            # continuity uses the coarse lag provisional value.
                            row["delay_samples"] = float(lag_c)
                            row["confidence"] = 0.0
                            row["valid"] = True
                            prev = int(lag_c)
                            _pending_refine.append(
                                (w, int(start), int(lag_c), float(abs_cc)))
                        else:
                            lag, quality = _refine_window_peak(
                                ref, tgt, start_sample=start,
                                window_samples=win, coarse_lag=lag_c,
                                radius=int(refine_radius_samples),
                                segment_samples=seg_samples,
                            )
                            row["delay_samples"] = float(lag)
                            row["confidence"] = round(
                                float(min(abs_cc, 1.0) * max(quality, 0.0)), 6
                            )
                            row["valid"] = True
                            prev = int(lag)
            windows.append(row)

        if _pending_refine and _refine_batch_eng is not None:
            try:
                from .gpu_align.refine_batch import RefineRequest
                _reqs = []
                for _w, _start, _center, _abs_cc in _pending_refine:
                    _c0 = _start + win // 2 - seg_samples // 2
                    if _c0 < 0:
                        _c0 = 0
                    _rseg = read_f64(ref, _c0, seg_samples)
                    _tspan = read_f64(
                        tgt, _c0 + _center - int(refine_radius_samples),
                        seg_samples + 2 * int(refine_radius_samples))
                    _reqs.append(RefineRequest(
                        _rseg, _tspan, int(_center),
                        int(refine_radius_samples), int(_c0),
                        int(_start + win), int(seg_samples)))
                _got = _refine_batch_eng.refine(_reqs)
                for (_w, _start, _center, _abs_cc), (_lag, _quality) in zip(
                        _pending_refine, _got):
                    windows[_w]["delay_samples"] = float(_lag)
                    windows[_w]["confidence"] = round(
                        float(min(_abs_cc, 1.0) * max(_quality, 0.0)), 6)
                if _ctx is not None:
                    for _st in _refine_batch_eng.stats:
                        _ctx.metrics.record_batch(_st)
            except Exception as _exc:                  # noqa: BLE001
                if os.environ.get("KTS_GPU_REFINE_DEBUG"):
                    print("batch_refine_error:", type(_exc).__name__, _exc)
                # Correctness fallback: never leave provisional coarse
                # confidence=0 rows when the GPU batch fails.
                for _w, _start, _center, _abs_cc in _pending_refine:
                    _lag, _quality = _refine_window_peak(
                        ref, tgt, start_sample=_start,
                        window_samples=win, coarse_lag=_center,
                        radius=int(refine_radius_samples),
                        segment_samples=seg_samples)
                    windows[_w]["delay_samples"] = float(_lag)
                    windows[_w]["confidence"] = round(
                        float(min(_abs_cc, 1.0) * max(_quality, 0.0)), 6)

        rejected = sum(1 for r in windows if not r["valid"])
        return {
            "sample_rate": int(sample_rate),
            "window_samples": int(win),
            "hop_samples": int(hop),
            "n_samples": int(n),
            "n_windows": int(n_windows),
            "initial": initial,
            "windows": windows,
            "decimated_rate": int(coarse_rate),
            "rejected_windows": int(rejected),
            "workers_effective": 1,
            "gpu_align": {
                "used": _align_eng is not None,
                "stats": ([s.as_dict() for s in _align_eng.stats]
                          if _align_eng is not None else []),
            },
        }
    finally:
        try:
            _surf_stop.set()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            if _surf_thread is not None:
                _surf_thread.join(timeout=3)
        except Exception:                                  # noqa: BLE001
            pass
        if _ctx is not None and _align_eng is not None:
            try:
                for _st in _align_eng.stats:
                    _ctx.metrics.record_batch(_st)
            except Exception:                          # noqa: BLE001
                pass
        if _ctx is not None:
            _ctx.metrics.record_stage(
                "window_measure", time.perf_counter() - _t_measure)
        if _refine_batch_eng is not None:
            try:
                _refine_batch_eng.close()
            except Exception:                          # noqa: BLE001
                pass
        if _align_eng is not None:
            try:
                _align_eng.close()
            except Exception:                          # noqa: BLE001
                pass
        if _align_sess is not None and _owns_align_session:
            try:
                _align_sess.close()
            except Exception:                          # noqa: BLE001
                pass
        if _gpu is not None:
            try:
                from . import gpu_cc as _gpu_mod
                _gpu_mod.release(_gpu)
            except Exception:                          # noqa: BLE001
                pass
        for stream in reversed(opened):
            close = getattr(stream, "close", None)
            if callable(close):
                close()
