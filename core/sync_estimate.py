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
流式读取; 延迟估计全程确定性 (固定 FFT 尺寸 + 整数帧位, 无任何 RNG)。
`sample_pair_snr()` 是唯一的抽样环节: 种子固定 (`_SNR_SEED`), 因此同一对
素材在任何一次运行里抽到同一批帧 —— 建组结论可复现, 但它**不参与**任何
延迟数值。
"""

from __future__ import annotations

import math
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import fft, signal

__all__ = [
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
    opened: list[Any] = []
    try:
        # 帧级并行 (workers>1): 只在两个源都是**路径**时可用 —— 子进程必须
        # 能自己打开数据。这里先把 n / 几何算出来以便切分, 真正的测量在
        # `_estimate_pair_parallel` 里由进程池完成, 父进程再负责聚合与精估。
        if (workers and int(workers) > 1
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
        with ProcessPoolExecutor(max_workers=k_workers) as ex:
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
