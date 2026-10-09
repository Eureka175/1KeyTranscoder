"""时变音频延迟修正 (P4 生产路径, algo 2.4.0-p4): 每窗修正值 + 认知状态.

职责与边界
----------
本模块只做**修正的表示与应用**, 不做测量:

  * ``CorrectionDecision``  : 一个窗的 ``(shift_samples, state)`` — 修正值与
    认知状态是**两个独立冻结字段**, 不是一个可空数字;
  * ``CorrectionPlan``      : 整个流的 ``(shifts, states, mask, policy,
    coordinate)`` — 构造时校验内部一致性, 不一致就**拒绝存在**;
  * ``apply_shift_plan``    : 把 plan 应用到 PCM 流, 分块, 无跨块状态;
  * ``derive_states``       : 状态是 resolved mask 的**纯函数**;
  * ``GapContext``          : 缺口需要的有界上下文, 必须**显式传入** — 缺失报错.

坐标约定 (P4 显式合同, 见 docs/audio-sync-p4/coordinate-decision.md)
-----------------------------------------------------------------
``out[k] = in[k + S[join(k)]]``, 其中 ``k`` 是输出样本坐标 (物理修正位置),
``join(k)`` 是 ``sample -> window`` 的映射, 由 ``coordinate`` 参数命名:

  * ``latest-start``   ``w = k // hop``            (P1/P3 历史合同, 默认)
  * ``nearest-centre`` ``w`` = 中心最近的窗, 裁剪到 plan 范围
  * ``sample-domain``  **与 latest-start 是同一个函数** (重叠窗下最大覆盖窗
    索引恒为 ``k // hop``), 因此**不是**第三个候选, 保留仅为拒绝误用.

``window_samples`` 与 ``hop_samples`` 一律作为**独立参数**传入, 且默认值取自
本模块的具名常量; 两者混用曾造成越界读取 (P3.8 §6.1)。

安全不变量 (每条都在 P3 中真实失败过一次)
--------------------------------------------
I1  修正值与认知状态分离 — 一个可空数字无法区分 "0 移位的测量" 与 "没有测量";
I2  没有可信测量的窗**不得**被报告为 resolved;
I3  状态 = f(resolved mask), 不是 f(块内历史); 禁止 ``shifts[-1]`` /
    ``previous_chunk`` / 全局可变状态;
I4  需要的上下文必须**在场**, 不能只"原则上可得" — 缺失一律 ``AudioSyncContextError``;
I5  任何带符号转换**之前**校验移位范围, 且不使用 ``np.abs`` (避免溢出环绕);
I6  流对象**透传**, 不物化 (``np.asarray(RawStream)`` 不报错, 走序列协议, 实测
    86x/173x 慢);
I7  越界读补零, 输出样本数 == 输入样本数;
I8  分块不变性: 输出 PCM 与 ``chunk_samples`` 无关.

与生产/研究的关系
------------------
* 不 import ``encoders`` / ``preservation`` / 硬件后端 / Sony 元数据;
* 只依赖 numpy 与 ``core.sync_estimate`` 的流式读取面;
* **不**被 ``core/channel_sync.py`` 调用, 除非显式开启 feature gate;
* 生产边界是 ``core/audio_sync_tv_exec.py`` (P4.6) —— 本模块自身仍不决定
  gap policy 或 coordinate contract, 两个决策的语义状态仍是 DEFERRED; 那个
  模块的职责只是把"未决"变成**必须显式指定, 否则拒绝**的调用契约。
* ``AudioPlan`` 与本模块无关 (它是声明式的, 不执行)。

状态语义
--------
::

    TRUSTED         本窗自身有被接受的测量
    UNRESOLVED_GAP  本窗被拒绝; 移位是**策略决定, 不是测量**
    REENTRY         缺口之后第一个被接受的窗 — 缺口决定与测量相遇之处

``UNRESOLVED_GAP`` 是**合法认知状态, 不是错误**。错误是 ``AudioSyncError``
的子类: 上下文缺失 / mask 非法 / 非有限值 / 移位越界 / plan 长度不符。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

__all__ = [
    "ALGO_VERSION",
    "DEFAULT_COORDINATE",
    "DEFAULT_POLICY",
    "DECISION_STATUS",
    "DEFAULT_SEARCH_SAMPLES",
    "DEFAULT_HOP_SAMPLES",
    "DEFAULT_WINDOW_SAMPLES",
    "COORDINATES",
    "GAP_POLICIES",
    "GAP_POLICY_NEEDS_ENDPOINTS",
    "WINDOW_STATES",
    "AudioSyncError",
    "AudioSyncContextError",
    "AudioSyncPlanError",
    "GapState",
    "CorrectionDecision",
    "GapContext",
    "CorrectionPlan",
    "derive_states",
    "window_index",
    "window_latest_start",
    "window_nearest_centre",
    "window_sample_domain",
    "required_window_count",
    "measured_window_count",
    "window_seam",
    "build_correction_plan",
    "apply_shift_plan",
    "ShiftPlanStats",
]

ALGO_VERSION = "2.4.0-p4"

# —— 具名常量: window/hop 必须分开传参, 默认值只在这里出现一次 ——
DEFAULT_WINDOW_SAMPLES = 1_440_000   # 30.0 s @ 48 kHz — P3 生产几何
DEFAULT_HOP_SAMPLES = 480_000        # 10.0 s @ 48 kHz — P3 生产几何
DEFAULT_SEARCH_SAMPLES = 3_000       # ±62.5 ms — P3 生产几何

DEFAULT_POLICY = "hold"
DEFAULT_COORDINATE = "latest-start"

#: **P4.3**: 两个未决产品决策的**机器可读状态**, 把两个必须分开的概念钉在一起:
#:
#:     implementation_default   本代码在无人指定时**实际用的**值。
#:                              存在理由是**可复现性**, 不是偏好。
#:     semantic_contract        产品**已经冻结的语义**。DEFERRED = 没有冻结。
#:
#: 二者混为一谈正是 P4.3 要消除的歧义: "默认值是 X" 不等于 "X 就是产品契约"。
#: `tests/audio_sync_p4/test_production_semantics.py` 断言这张表与
#: `DEFAULT_POLICY` / `DEFAULT_COORDINATE` 一致 —— 改默认值而不同时改这张表会
#: 让测试失败, 而不是让一个未决决策悄悄改变。
DECISION_STATUS: Mapping[str, Mapping[str, str]] = {
    "gap_policy": {
        "id": "D2 (canonical P4: D1)",
        "implementation_default": DEFAULT_POLICY,
        "semantic_contract": "DEFERRED",
        "owner": "product",
        "why_deferred": (
            "no candidate dominates: each is the better answer in a different "
            "row, and the rows that separate them are decided by the position "
            "of a discontinuity inside the gap, which is not observable"
        ),
        "unblocked_by": (
            "a product statement of what the output audio is for, plus "
            "acceptance of a residual budget per state (D3)"
        ),
    },
    "coordinate": {
        "id": "D5 (canonical P4: D2)",
        "implementation_default": DEFAULT_COORDINATE,
        "semantic_contract": "DEFERRED",
        "owner": "product + engineering",
        "why_deferred": (
            "latest-start and nearest-centre are not equivalent "
            "implementations (C1305 56.94 %, C1306 36.37 % value agreement); "
            "choosing means answering whether a correction should anticipate "
            "a change or lag it, which is a product question"
        ),
        "unblocked_by": (
            "a product statement of the acceptable error profile, and an "
            "independent reference whose true delay is known at sample "
            "resolution"
        ),
    },
}

#: 策略名 -> 是否需要**右端点** (因而只能离线运行) 。
#:
#: 这个表只回答一个问题: 该策略在决定第 i 个窗时, 是否必须知道**后面**的
#: 测量结果? 需要 -> 离线 (流式路径不得使用) ; 不需要 -> 在线。
#:
#: 注意 ``current`` 是**在线**的: 它的前导缺口取"第一个可信值", 而第一个可信
#: 索引可以由 mask 在构造期确定 (有界的工作量, 有界的存储) , 不需要在决定某个
#: 窗时读取它之后的样本。P3.11 也正是把它当作可运行时策略实测的。
GAP_POLICIES: Mapping[str, bool] = {
    # policy name -> needs a right endpoint (offline only)
    "hold": False,                # 前向携带最后一个可信移位, 无可携带时为 0
    "no_correction_gap": False,   # 缺口内一律 0 — 完全不需要上下文
    "explicit_state": False,      # 与 hold 移位逐位相同, 状态为一等输出
    "current": False,             # 前向携带; 前导缺口取第一个可信值
    "linear": True,               # 需要缺口两侧端点值 -> 离线
    "next": True,                 # 需要"下一个"可信移位 -> 离线
}

#: 策略名 -> 是否需要调用方**显式提供缺口端点值**。
#: ``linear`` 需要; 其余都不需要 ( ``current`` 自己从 mask 求得首个可信值) 。
GAP_POLICY_NEEDS_ENDPOINTS: Mapping[str, bool] = {
    "hold": False,
    "no_correction_gap": False,
    "explicit_state": False,
    "current": False,
    "linear": True,
    "next": False,
}

#: 允许的坐标合同 (join) 。sample-domain 与 latest-start 同函数, 见模块 docstring。
COORDINATES: tuple[str, ...] = ("latest-start", "nearest-centre", "sample-domain")

#: 移位绝对值上限 — 在**任何带符号转换之前**校验 (P3.8 §6.5) 。
#: ±2**31 允许, ±2**31+1 拒绝; 不使用 np.abs (INT_MIN 的 abs 会溢出)。
SHIFT_LIMIT = 1 << 31


class AudioSyncError(Exception):
    """本模块所有错误的基类。**错误** 与 **未决** 是两件事:

    ``UNRESOLVED_GAP`` 是合法认知状态, 绝不抛异常; 本类只表示执行无效。
    """


class AudioSyncContextError(AudioSyncError):
    """必需上下文缺失 / 非法。绝不静默猜测, 绝不用默认值兜底。"""


class AudioSyncPlanError(AudioSyncError):
    """plan 本身不合法: 长度不符、非有限值、移位越界、状态与 mask 矛盾。"""


class GapState(str, Enum):
    """一个窗的认知状态。成员的 ``.value`` 是稳定字符串, 可跨进程序列化。"""

    TRUSTED = "TRUSTED"
    UNRESOLVED_GAP = "UNRESOLVED_GAP"
    REENTRY = "REENTRY"


#: 状态名 -> 枚举, 用于从已序列化的 plan 重建。
WINDOW_STATES: Mapping[str, "GapState"] = {s.value: s for s in GapState}


# --------------------------------------------------------------------------
# 坐标合同 (join): sample -> window
# --------------------------------------------------------------------------
def window_latest_start(
    pos: np.ndarray | int, *, hop_samples: int, n_windows: int | None = None
) -> np.ndarray:
    """``w = pos // hop`` — 起点不晚于 ``pos`` 的**最晚**一个窗。

    这是 P1/P3 的历史合同, 也是 ``gap_semantics.apply_shift_plan`` 实际计算的
    函数。在 plan 覆盖范围之外 ``pos // hop`` 会超出最后一个窗 — 此时**裁剪**
    到 ``n_windows - 1`` 是一个**显式选择**, 不是意外 (P3.11 测试 T2 固定住)。
    """
    p = np.asarray(pos, dtype=np.int64)
    w = p // int(hop_samples)
    if n_windows is not None:
        w = np.clip(w, 0, int(n_windows) - 1)
    return w


def window_nearest_centre(
    pos: np.ndarray | int, *,
    window_samples: int, hop_samples: int, n_windows: int | None = None
) -> np.ndarray:
    """``w`` = 中心离 ``pos`` 最近的窗。

    中心 ``c(w) = w*hop + window/2``, 所以 ``w = round((pos - window/2)/hop)``。
    用整数算术实现, 避免浮点边界抖动::

        num = pos - window//2 + hop//2        (四舍五入)
        raw = floor_div(num, hop)

    **原始映射在 block 0 返回 -1** (不是合法窗索引) — 这是 P3 记录过的缺陷类。
    传入 ``n_windows`` 时裁剪到 ``[0, n_windows-1]``; 不传时返回原始值, 调用方
    必须自己处理负索引。
    """
    p = np.asarray(pos, dtype=np.int64)
    num = p - int(window_samples) // 2 + int(hop_samples) // 2
    raw = np.floor_divide(num, int(hop_samples))
    if n_windows is not None:
        raw = np.clip(raw, 0, int(n_windows) - 1)
    return raw


def window_sample_domain(
    pos: np.ndarray | int, *,
    window_samples: int, hop_samples: int, n_windows: int | None = None
) -> np.ndarray:
    """样本域权威: 覆盖该样本的测量窗中**序号最大**者。

    重叠窗 (``window > hop``) 下, 覆盖 ``pos`` 的窗满足
    ``pos - window < w*hop <= pos``, 其最大索引恒为 ``pos // hop`` — 因此本函数
    **与** :func:`window_latest_start` **是同一个函数**, 不是第三个候选。
    P3.11 在两个生产 plan 尺寸上断言了这个恒等 (0 差异)。

    保留此入口是为了让"sample-domain 是第三候选"这个误用**可被明确拒绝**,
    而不是让调用方自己写一个看似不同的实现。
    """
    if int(window_samples) < int(hop_samples):
        raise AudioSyncContextError(
            f"sample-domain requires window_samples >= hop_samples "
            f"(got window={window_samples}, hop={hop_samples}): with "
            f"non-overlapping windows no window may cover an earlier sample"
        )
    return window_latest_start(
        pos, hop_samples=int(hop_samples), n_windows=n_windows
    )


def window_index(
    coordinate: str, pos: np.ndarray | int, *,
    window_samples: int, hop_samples: int, n_windows: int
) -> np.ndarray:
    """按名字派发 join, 结果一律裁剪进 ``[0, n_windows-1]``。

    这是**唯一**的坐标派发点 — 一个坐标合同的变化只在这里体现。
    """
    if coordinate not in COORDINATES:
        raise AudioSyncContextError(
            f"unknown coordinate contract {coordinate!r}; "
            f"known: {', '.join(COORDINATES)}"
        )
    if coordinate == "latest-start":
        return window_latest_start(
            pos, hop_samples=hop_samples, n_windows=n_windows
        )
    if coordinate == "nearest-centre":
        return window_nearest_centre(
            pos, window_samples=window_samples, hop_samples=hop_samples,
            n_windows=n_windows,
        )
    return window_sample_domain(
        pos, window_samples=window_samples, hop_samples=hop_samples,
        n_windows=n_windows,
    )


def required_window_count(n_samples: int, *, hop_samples: int) -> int:
    """applier 需要多少窗才能寻址**每一个**样本: ``ceil(n / hop)``。

    与 :func:`measured_window_count` 的差就是 P3 记录的 seam。
    """
    n = int(n_samples)
    h = int(hop_samples)
    if n < 0:
        raise AudioSyncContextError(f"n_samples must be >= 0, got {n}")
    if h <= 0:
        raise AudioSyncContextError(f"hop_samples must be > 0, got {h}")
    return (n + h - 1) // h


def measured_window_count(
    n_samples: int, *, window_samples: int, hop_samples: int
) -> int:
    """测量层能给出多少个**完整**窗: ``floor((n - window)/hop) + 1``。

    ``n`` 短于一个窗时该式给出 <= 0 — 这是**域错误**, 不是 seam。返回值下限
    为 0, 由调用方决定如何声明。
    """
    n = int(n_samples)
    w = int(window_samples)
    h = int(hop_samples)
    if h <= 0:
        raise AudioSyncContextError(f"hop_samples must be > 0, got {h}")
    if w <= 0:
        raise AudioSyncContextError(f"window_samples must be > 0, got {w}")
    if n < w:
        return 0
    return (n - w) // h + 1


def window_seam(
    n_samples: int, *, window_samples: int, hop_samples: int
) -> int:
    """``required_window_count - measured_window_count`` — 未测量的尾部窗数。

    P3.11 实测形式: ``n mod hop == 0`` 时差 2, 否则差 3 (``window = 3*hop``)。
    函数按定义计算, 不硬编码这两个值。
    """
    return required_window_count(n_samples, hop_samples=hop_samples) - (
        measured_window_count(
            n_samples, window_samples=window_samples, hop_samples=hop_samples
        )
    )


# --------------------------------------------------------------------------
# 认知状态
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CorrectionDecision:
    """一个窗的修正决定: **值**与**认知状态**是两个独立冻结字段。

    ``shift_samples`` 是整数修正 (与 ``sync_fix.shift_stream`` 的
    ``out[n] = in[n+shift]`` 同向同语义), ``state`` 是"这个值是怎么来的"。

    不变量 (构造即强制, 矛盾**不可表示**):

    * ``TRUSTED`` / ``REENTRY`` 只能由 ``source="measurement"`` 构造;
    * ``UNRESOLVED_GAP`` 只能由 ``source="policy"`` 构造;
    * 因此 "一个测量来源的决定声称未决" 在类型层面就无法构造。

    两个状态、同一个值都是合法且**必须可区分**的::

        CorrectionDecision(615, GapState.TRUSTED)
        CorrectionDecision(615, GapState.UNRESOLVED_GAP)
    """

    shift_samples: int
    state: GapState
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, GapState):
            raise AudioSyncPlanError(
                f"state must be a GapState, got {type(self.state).__name__}"
            )
        if self.source is None:
            # 未显式给出的来源**从状态推导**, 而不是默认成 "policy"。
            # 默认成 policy 会让最常见的写法
            # ``CorrectionDecision(615, GapState.TRUSTED)`` 直接报错 — 一个
            # 让正确用法失败的默认值是缺陷, 不是严谨。
            object.__setattr__(
                self, "source",
                "policy" if self.state is GapState.UNRESOLVED_GAP
                else "measurement",
            )
        if self.state is GapState.UNRESOLVED_GAP:
            if self.source != "policy":
                raise AudioSyncPlanError(
                    "a decision with no accepted measurement must be "
                    "source='policy'; claiming source="
                    f"{self.source!r} for UNRESOLVED_GAP would assert a "
                    "measurement that does not exist"
                )
        else:
            if self.source != "measurement":
                raise AudioSyncPlanError(
                    f"{self.state.value} requires source='measurement', got "
                    f"{self.source!r}; a policy-filled value must not be "
                    "reported as measured"
                )
        _check_shift_scalar(self.shift_samples)

    @property
    def resolved(self) -> bool:
        """该窗的移位是否**由本窗自身的测量**支撑。"""
        return self.state is not GapState.UNRESOLVED_GAP


def _check_shift_scalar(value: Any) -> int:
    """把移位校验成 python int, **在任何带符号转换之前** (不变量 I5) 。"""
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioSyncPlanError(f"shift is not numeric: {value!r}") from exc
    if not math.isfinite(f):
        raise AudioSyncPlanError(f"shift is not finite: {value!r}")
    if abs(f) >= SHIFT_LIMIT:
        # 这里用 abs(float) 而不是 np.abs(int): 浮点域上不存在 INT_MIN 环绕,
        # 而整数域上 np.abs(INT_MIN) 会静默回绕成 INT_MIN (P3.8 §6.5)。
        raise AudioSyncPlanError(
            f"shift {f!r} exceeds the ±{SHIFT_LIMIT} sample guard; this is "
            "checked before any signed cast"
        )
    return int(f)


def derive_states(resolved: np.ndarray) -> np.ndarray:
    """状态序列 = resolved mask 的**纯函数** (不变量 I3) 。

    ::

        resolved[i] == False                    -> UNRESOLVED_GAP
        resolved[i] == True and i-1 was a gap   -> REENTRY
        resolved[i] == True otherwise           -> TRUSTED

    这里是状态序列的**唯一**定义。没有分块、没有访问前一个窗、没有可变游标。
    P3.7 证明逐块状态**不可能**分块不变: 一个只有一个窗的块无法知道前面是缺口,
    因此无法给自己标注 ``REENTRY``。从 mask 推导把这个缺陷类整体消除, 并让分块
    不变性自动成立。
    """
    r = np.asarray(resolved)
    if r.ndim != 1:
        raise AudioSyncPlanError(f"resolved must be 1-D, got shape {r.shape}")
    if r.size == 0:
        raise AudioSyncPlanError("resolved mask is empty")
    if r.dtype != np.bool_:
        # 显式接受 0/1 整数与 True/False, 但拒绝其它值: 一个 mask 里出现 2
        # 是上游缺陷, 静默当成 True 就是"看似合理的数字来自错误的地方"。
        if not np.all((r == 0) | (r == 1)):
            bad = np.unique(r[~((r == 0) | (r == 1))])[:5]
            raise AudioSyncPlanError(
                f"resolved mask must be boolean or 0/1; found {bad.tolist()}"
            )
        r = r.astype(bool)
    else:
        r = r.copy()
    if not r.any():
        # 全未决是**合法**的 (整条流都没有可用测量)。状态序列全为
        # UNRESOLVED_GAP, 没有任何窗被标 REENTRY。
        return np.full(r.shape, GapState.UNRESOLVED_GAP.value, dtype=object)
    absent = ~r
    entered = np.empty(r.shape, dtype=bool)
    entered[0] = False
    entered[1:] = r[1:] & absent[:-1]
    out = np.where(
        absent,
        GapState.UNRESOLVED_GAP.value,
        np.where(entered, GapState.REENTRY.value, GapState.TRUSTED.value),
    )
    return out.astype(object)


# --------------------------------------------------------------------------
# 缺口策略
# --------------------------------------------------------------------------
def _fill_hold(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """前向携带最后一个可信移位; 前导缺口 (没有可携带值) 为 0。"""
    out = np.zeros(shifts.shape, dtype=np.int64)
    carry = 0
    for i in range(shifts.size):
        if resolved[i]:
            carry = int(shifts[i])
        out[i] = carry
    return out


def _fill_no_correction_gap(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """缺口内一律 0。**不需要任何上下文** — 因此在任何分块下都自动不变。"""
    return np.where(resolved, shifts, 0).astype(np.int64)


def _fill_explicit_state(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """与 ``hold`` 逐位相同的移位; 差别只在状态是一等输出。"""
    return _fill_hold(
        shifts, resolved, gap_endpoints=gap_endpoints, offline_ok=offline_ok
    )


def _fill_current(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """历史策略: 前向携带, **前导缺口取第一个可信值**。

    与 ``hold`` 的唯一差别就在前导缺口 — P3 实测 "the only numerical
    difference is the leading gap", 0.4-0.5% 的窗。

    仍然是在线策略: 首个可信索引由 mask 一次性确定 (有界工作量, 有界存储),
    决定第 i 个窗时不需要读取它之后**尚未到达**的测量。
    """
    out = np.zeros(shifts.shape, dtype=np.int64)
    trusted = np.flatnonzero(resolved)
    if trusted.size == 0:
        return out
    carry = int(shifts[int(trusted[0])])
    for i in range(shifts.size):
        if resolved[i]:
            carry = int(shifts[i])
        out[i] = carry
    return out


def _fill_linear(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """离线: 用缺口两侧可信值线性插值。

    需要**右端点** — 因此必须显式给出 ``gap_endpoints`` (缺口两端可信窗的值)。
    离线策略不得用于流式路径; 这里保留实现是为了让离线参考可复现, 不是为了生产。
    """
    if gap_endpoints is None:
        raise AudioSyncContextError(
            "policy 'linear' needs the gap's two endpoint VALUES; supply "
            "GapContext.gap_endpoints. Absence raises rather than guessing."
        )
    left_value, right_value = (int(gap_endpoints[0]), int(gap_endpoints[1]))
    out = np.array(shifts, dtype=np.int64, copy=True)
    n = shifts.size
    i = 0
    while i < n:
        if resolved[i]:
            i += 1
            continue
        j = i
        while j < n and not resolved[j]:
            j += 1
        # [i, j) 是缺口; 两侧端点由上下文给出 (左端 = 缺口前一个可信窗,
        # 右端 = 缺口后第一个可信窗) 。
        span = j - i + 1
        for k in range(i, j):
            t = (k - i + 1) / span
            out[k] = int(round(left_value + (right_value - left_value) * t))
        i = j
    return out


def _fill_next(
    shifts: np.ndarray, resolved: np.ndarray, *,
    gap_endpoints: tuple[int, int] | None, offline_ok: bool
) -> np.ndarray:
    """离线: 缺口内使用**下一个**可信移位; 尾部缺口为 0。"""
    out = np.zeros(shifts.shape, dtype=np.int64)
    nxt: int | None = None
    for i in range(shifts.size - 1, -1, -1):
        if resolved[i]:
            nxt = int(shifts[i])
            out[i] = nxt
        elif nxt is not None:
            out[i] = nxt
    return out


_FILLERS: Mapping[str, Callable[..., np.ndarray]] = {
    "hold": _fill_hold,
    "no_correction_gap": _fill_no_correction_gap,
    "explicit_state": _fill_explicit_state,
    "current": _fill_current,
    "linear": _fill_linear,
    "next": _fill_next,
}


@dataclass(frozen=True, slots=True)
class GapContext:
    """一个缺口需要的**有界**上下文 — 必须显式传入, 缺失报错。

    P3.7 测得流式实现需要且仅需要 (每流 + 每缺口) :

    * ``stream_has_trust``  : 全流是否存在任何可信窗 (或: 前方/后方是否还有?)
    * ``gap_endpoints``     : 当前缺口两端可信窗的**值** (2 个整数)
    * ``gap_extent``        : 当前缺口在**全局**坐标下的范围 (2 个整数)

    ``stream_end`` 用来区分"mask 到此为止"与"这条流到此为止" — P3.8 记录过一个
    **被截断的 mask 与一次拒绝无法区分**的缺陷 (``[0]`` 而真值是 ``[400]``) 。
    """

    stream_has_trust: bool
    gap_endpoints: tuple[int, int] | None = None
    gap_extent: tuple[int, int] | None = None
    stream_end: int | None = None

    def demand_endpoints(self, policy: str) -> tuple[int, int]:
        """需要端点却缺失 -> 报错。**绝不猜测, 绝不兜底。**"""
        if self.gap_endpoints is None:
            raise AudioSyncContextError(
                f"policy {policy!r} requires the gap's two endpoint values; "
                "they were not supplied. A chunk cannot infer them, and a "
                "guess here is the defect class this design exists to remove."
            )
        if len(self.gap_endpoints) != 2:
            raise AudioSyncContextError(
                f"gap_endpoints must hold exactly 2 values, got "
                f"{len(self.gap_endpoints)}"
            )
        return (int(self.gap_endpoints[0]), int(self.gap_endpoints[1]))


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    """整流的修正计划: 移位序列 + 状态序列 + 派生它们的 mask + 出处。

    ``provenance`` 是本对象的一部分, 不是旁边的一行日志: 它记录策略名、
    坐标合同、窗口数、算法版本, 以及 mask 本身 ("保留下来, 使推导可审计而不是
    隐含的") 。
    """

    shifts: np.ndarray
    states: np.ndarray
    resolved: np.ndarray
    policy: str
    coordinate: str
    window_samples: int
    hop_samples: int
    n_samples: int
    search_samples: int = DEFAULT_SEARCH_SAMPLES
    version: str = ALGO_VERSION
    stream_has_trust: bool = True
    gap_extent: tuple[int, int] | None = None
    gap_endpoints: tuple[int, int] | None = None
    offline_only: bool = False
    #: ``False`` 只对历史策略 ``current`` 成立: 它把缺口窗报告为 ``TRUSTED``
    #: (P3.7 记录的 semantic over-claim, **1/22**) 。带上这个标志使它成为
    #: **可审计的**已知偏差, 而不是对不变量 I2 的静默违反:
    #: :meth:`overclaimed_windows` 精确指出有多少个窗声称了没有的测量。
    mask_is_authoritative: bool = True

    def __post_init__(self) -> None:
        shifts = np.asarray(self.shifts)
        resolved = np.asarray(self.resolved)
        if shifts.ndim != 1:
            raise AudioSyncPlanError(f"shifts must be 1-D, got {shifts.shape}")
        if resolved.ndim != 1:
            raise AudioSyncPlanError(
                f"resolved must be 1-D, got {resolved.shape}"
            )
        if shifts.shape != resolved.shape:
            raise AudioSyncPlanError(
                f"shifts {shifts.shape} and resolved {resolved.shape} must "
                "have the same shape"
            )
        if shifts.size == 0:
            raise AudioSyncPlanError("plan has no windows")
        if self.policy not in GAP_POLICIES:
            raise AudioSyncPlanError(
                f"unknown gap policy {self.policy!r}; known: "
                f"{', '.join(sorted(GAP_POLICIES))}"
            )
        if self.coordinate not in COORDINATES:
            raise AudioSyncPlanError(
                f"unknown coordinate contract {self.coordinate!r}; known: "
                f"{', '.join(COORDINATES)}"
            )
        if self.offline_only and not GAP_POLICIES[self.policy]:
            raise AudioSyncPlanError(
                f"plan marks {self.policy!r} offline_only but that policy is "
                "an online policy"
            )
        for i, v in enumerate(shifts.tolist()):
            _check_shift_scalar(v)
        # 长度必须是 applier 需要的确切窗数, **不是下界** (P3.10: 未补齐的 plan
        # 被 applier 拒绝, 这是正确行为) 。
        need = required_window_count(
            self.n_samples, hop_samples=int(self.hop_samples)
        )
        if shifts.size != need:
            raise AudioSyncPlanError(
                f"plan has {shifts.size} windows but the stream needs {need} "
                f"at hop_samples={self.hop_samples}"
            )
        mask = resolved.astype(bool) if resolved.dtype != np.bool_ else resolved
        if self.mask_is_authoritative:
            expected = derive_states(mask)
        else:
            # 历史 over-claim 策略: 状态由**提升后的** mask 派生, 使 "每个窗都
            # 声称已测量" 成为 plan 的一个显式属性, 而不是一个被掩盖的事实。
            expected = derive_states(np.ones(mask.shape, dtype=bool))
        got = np.asarray(self.states, dtype=object)
        if got.shape != expected.shape or not np.array_equal(got, expected):
            bad = int((got != expected).sum()) if got.shape == expected.shape \
                else shifts.size
            raise AudioSyncPlanError(
                f"states disagree with the resolved mask at {bad} window(s); "
                "the state sequence is a derivation, not an independent input"
            )
        if bool(mask.any()) != bool(self.stream_has_trust):
            raise AudioSyncPlanError(
                f"stream_has_trust={self.stream_has_trust} contradicts the "
                f"mask (any trusted = {bool(mask.any())})"
            )
        object.__setattr__(self, "shifts", shifts.astype(np.int64, copy=True))
        object.__setattr__(self, "states", expected)
        object.__setattr__(self, "resolved", mask.copy())
        object.__setattr__(self, "offline_only", bool(GAP_POLICIES[self.policy]))

    # ---- 只读视图 ----
    @property
    def n_windows(self) -> int:
        return int(self.shifts.size)

    def decision(self, window: int) -> CorrectionDecision:
        """取一个窗的决定 — 值 + 状态, 两者一起返回, 不可分开使用。"""
        i = int(window)
        if not 0 <= i < self.n_windows:
            raise AudioSyncPlanError(
                f"window {i} out of range [0, {self.n_windows})"
            )
        state = GapState(str(self.states[i]))
        return CorrectionDecision(
            shift_samples=int(self.shifts[i]),
            state=state,
            source="measurement" if state is not GapState.UNRESOLVED_GAP
            else "policy",
        )

    def decisions(self) -> Iterable[CorrectionDecision]:
        for i in range(self.n_windows):
            yield self.decision(i)

    def state_counts(self) -> dict[str, int]:
        """每个状态各有多少窗 — 生产日志的追踪口径。"""
        counts = {s.value: 0 for s in GapState}
        for v in self.states.tolist():
            counts[str(v)] = counts.get(str(v), 0) + 1
        return counts

    def overclaimed_windows(self) -> int:
        """有多少个窗声称了自己没有的测量。

        对每个在线策略这都是 **0**, 因为状态是从 mask 派生的。只有历史策略
        ``current`` 会非零 (P3.7 的 semantic over-claim) 。这个数字进生产日志,
        使"声称了没有的测量"永远可见。
        """
        if self.mask_is_authoritative:
            return 0
        return int((~self.resolved).sum())

    def is_honest(self) -> bool:
        """是否没有窗声称自己没做过的测量。"""
        return self.overclaimed_windows() == 0

    def window_of(self, pos: np.ndarray | int) -> np.ndarray:
        """把输出样本坐标映射到窗索引, 使用本 plan 声明的坐标合同。"""
        return window_index(
            self.coordinate, pos, window_samples=self.window_samples,
            hop_samples=self.hop_samples, n_windows=self.n_windows,
        )

    def shift_at(self, pos: np.ndarray | int) -> np.ndarray:
        """某个输出样本坐标上**实际被施加**的移位。

        校验与报告都必须用它, 而不是用"某个窗的移位" — 坐标合同不同, 同一个
        样本收到的值就不同 (P4 实测: nearest-centre 下 plan 的逐窗索引与施加值
        相差一个 hop, 造成 732 样本的伪 excess) 。
        """
        return self.shifts[self.window_of(pos)]


def build_correction_plan(
    measurements: Sequence[Mapping[str, Any]] | None = None,
    *,
    resolved: Sequence[bool] | np.ndarray | None = None,
    shifts: Sequence[int] | np.ndarray | None = None,
    n_samples: int,
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
    hop_samples: int = DEFAULT_HOP_SAMPLES,
    policy: str = DEFAULT_POLICY,
    coordinate: str = DEFAULT_COORDINATE,
    search_samples: int = DEFAULT_SEARCH_SAMPLES,
    context: GapContext | None = None,
    version: str = ALGO_VERSION,
) -> CorrectionPlan:
    """从测量记录构造 plan。

    输入
    ----
    ``measurements``: 每窗一条记录, 需要 ``delay_samples`` (float) 与
    ``valid`` (bool) 。这是 P3 测量层的记录形状; ``resolved`` mask **从测量
    有效性派生, 不是外部传进来的** (P3 要求: mask 必须派生, 不得作为参数接受) 。

    或者直接给 ``resolved`` + ``shifts`` (供测试与已冻结 plan 的重放)。

    输出
    ----
    :class:`CorrectionPlan`, 长度为 :func:`required_window_count` —
    **包括没有测量、只能由策略填充的尾部窗**。
    """
    if measurements is None and (resolved is None or shifts is None):
        raise AudioSyncContextError(
            "build_correction_plan needs either measurements, or both "
            "resolved and shifts"
        )
    if policy not in GAP_POLICIES:
        raise AudioSyncPlanError(
            f"unknown gap policy {policy!r}; known: "
            f"{', '.join(sorted(GAP_POLICIES))}"
        )
    need = required_window_count(n_samples, hop_samples=int(hop_samples))

    if measurements is not None:
        if len(measurements) == 0:
            raise AudioSyncContextError("measurements is empty")
        if len(measurements) > need:
            raise AudioSyncPlanError(
                f"{len(measurements)} measurement records exceed the {need} "
                "windows the stream needs"
            )
        n_meas = len(measurements)
        mask = np.zeros(need, dtype=bool)
        raw = np.zeros(need, dtype=np.int64)
        for i, rec in enumerate(measurements):
            if "valid" not in rec:
                raise AudioSyncContextError(
                    f"measurement {i} has no 'valid' field; the mask must be "
                    "derived from stated validity, not assumed"
                )
            if "delay_samples" not in rec:
                raise AudioSyncContextError(
                    f"measurement {i} has no 'delay_samples' field"
                )
            valid = bool(rec["valid"])
            mask[i] = valid
            if valid:
                d = float(rec["delay_samples"])
                if not math.isfinite(d):
                    raise AudioSyncPlanError(
                        f"measurement {i} is valid but its delay is not "
                        f"finite: {rec['delay_samples']!r}"
                    )
                raw[i] = _check_shift_scalar(int(np.rint(d)))
            else:
                # 被拒绝的窗**仍然可能带一个数字** (estimator 返回了值但标
                # valid=False) 。那个数字不得被当成移位使用。
                raw[i] = 0
        # 尾部: 没有测量的窗一律未决。**声明它, 不是补零, 不是重复最后一个值。**
        for i in range(n_meas, need):
            mask[i] = False
            raw[i] = 0
    else:
        mask_in = np.asarray(resolved)
        raw = np.asarray(shifts, dtype=np.int64)
        if raw.ndim != 1 or mask_in.ndim != 1:
            raise AudioSyncPlanError("resolved and shifts must be 1-D")
        if raw.size != mask_in.size:
            raise AudioSyncPlanError(
                f"resolved {mask_in.size} and shifts {raw.size} differ"
            )
        if raw.size != need:
            raise AudioSyncPlanError(
                f"supplied plan has {raw.size} windows but the stream needs "
                f"{need} at hop_samples={hop_samples}"
            )
        if mask_in.dtype != np.bool_:
            if not np.all((mask_in == 0) | (mask_in == 1)):
                raise AudioSyncPlanError("resolved must be boolean or 0/1")
        mask = mask_in.astype(bool)

    ctx = context if context is not None else GapContext(
        stream_has_trust=bool(mask.any())
    )
    if not ctx.stream_has_trust and bool(mask.any()):
        raise AudioSyncContextError(
            "context says the stream has no trusted window, but the mask has "
            f"{int(mask.sum())} trusted window(s)"
        )

    filler = _FILLERS[policy]
    if GAP_POLICIES[policy] and context is None:
        raise AudioSyncContextError(
            f"policy {policy!r} needs a RIGHT endpoint and cannot be driven "
            "from a streaming chunk; supply an explicit GapContext to run it "
            "offline"
        )
    if GAP_POLICY_NEEDS_ENDPOINTS[policy]:
        if context is None:
            raise AudioSyncContextError(
                f"policy {policy!r} requires the gap's two endpoint values; "
                "supply GapContext.gap_endpoints. Absence raises rather than "
                "guessing."
            )
        context.demand_endpoints(policy)
    filled = filler(
        raw, mask, gap_endpoints=ctx.gap_endpoints,
        offline_ok=not GAP_POLICIES[policy],
    )
    # 历史策略 `current` 把缺口窗报告为 TRUSTED — P3.7 记录的 semantic
    # over-claim。它被显式建模为 `mask_is_authoritative = False`, 而不是让
    # 状态序列去违反 I2; 代价是 `overclaimed_windows()` 会非零, 因此这个
    # 偏差可以被计数、被记录、被拒绝, 但不会被隐藏。
    authoritative = policy != "current"
    states = derive_states(
        mask if authoritative else np.ones(mask.shape, dtype=bool)
    )
    return CorrectionPlan(
        shifts=filled, states=states, resolved=mask, policy=policy,
        coordinate=coordinate, window_samples=int(window_samples),
        hop_samples=int(hop_samples), n_samples=int(n_samples),
        search_samples=int(search_samples), version=version,
        stream_has_trust=bool(mask.any()),
        gap_extent=ctx.gap_extent, gap_endpoints=ctx.gap_endpoints,
        mask_is_authoritative=authoritative,
    )


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ShiftPlanStats:
    """一次 apply 的计数 — 让"空跑却报 PASS"不可能发生。"""

    n_samples_in: int
    n_samples_out: int
    n_blocks: int
    n_measurements: int
    n_trusted: int
    n_unresolved: int
    n_reentry: int
    coordinate: str
    policy: str
    bytes_written: int
    apply_count: int
    #: 有多少个窗声称了自己没有的测量 (只有历史策略 `current` 会非零) 。
    n_overclaimed: int = 0


def _open_stream(src: Any, storage_dtype: str) -> tuple[Any, bool]:
    """返回 (stream, owns) 。路径 -> 打开一个**有界** :class:`RawStream`。"""
    from .sync_estimate import RawStream, open_source

    if isinstance(src, (str, bytes)) or hasattr(src, "__fspath__"):
        return RawStream(src, storage_dtype), True
    stream = open_source(src, storage_dtype)
    return stream, stream is not src


def apply_shift_plan(
    src: Any,
    dst: Any,
    plan: CorrectionPlan,
    *,
    storage_dtype: str = "f32",
    chunk_samples: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ShiftPlanStats:
    """把 plan 施加到流上, 分块, **无跨块状态**, 内存有界。

    契约::

        out[k] = in[k + S[join(k)]]     join 由 plan.coordinate 命名
        越界读补零
        输出样本数 == 输入样本数

    实现说明 (每条都对应一次真实失败):

    * 逐块计算 ``w = join(k)`` — **不物化** 每样本坐标标签。P3.11 自己的实现
      在 C1305 长度上物化一个 int64/样本 = 1.8 GB, 峰值 RSS 冲到 3632 MiB;
      engine 路径是 110 MiB 平。
    * 逐块从 ndarray 取值而不是对整个流 ``np.asarray`` — 后者在流对象上不报错,
      走序列协议, 实测 86x/173x 慢。
    * 块内按**相同的窗**切段, 因此每段一次拷贝, 没有逐样本 python 循环。
    * ``shift`` 已在 ``CorrectionPlan`` 构造时校验过范围与有限性; 这里再做一次
      防御性检查, 仍然在**任何转换之前**。
    """
    if not isinstance(plan, CorrectionPlan):
        raise AudioSyncContextError(
            f"plan must be a CorrectionPlan, got {type(plan).__name__}"
        )
    if plan.offline_only:
        raise AudioSyncContextError(
            f"plan policy {plan.policy!r} is marked offline-only; it must not "
            "be applied in a streaming production path"
        )
    n = int(plan.n_samples)
    h = int(plan.hop_samples)
    if n < 0 or h <= 0:
        raise AudioSyncContextError(
            f"plan has n_samples={n}, hop_samples={h}"
        )
    # 通用 ndarray 输入走 itemsize 推导; RawStream 用自身 dtype。
    stream, owns = _open_stream(src, storage_dtype)
    try:
        n_stream = int(stream.shape[0])
        if n_stream != n:
            raise AudioSyncContextError(
                f"stream has {n_stream} samples but the plan was built for "
                f"{n}; refusing to apply a plan of the wrong length"
            )
        block_n = int(chunk_samples) if chunk_samples is not None else h
        if block_n <= 0:
            raise AudioSyncContextError(
                f"chunk_samples must be > 0, got {chunk_samples!r}"
            )
        dt = np.dtype("<f4") if storage_dtype == "f32" else np.dtype("<f8")
        n_blocks = 0
        written = 0
        with open(dst, "wb") as fh:
            for a in range(0, n, block_n):
                b = min(n, a + block_n)
                block = _render_block(stream, a, b, plan, dt, h)
                fh.write(block.tobytes())
                n_blocks += 1
                written += block.nbytes
                if progress is not None:
                    progress(b, n)
        counts = plan.state_counts()
        return ShiftPlanStats(
            n_samples_in=n, n_samples_out=n, n_blocks=n_blocks,
            n_measurements=counts.get(GapState.TRUSTED.value, 0)
            + counts.get(GapState.REENTRY.value, 0),
            n_trusted=counts.get(GapState.TRUSTED.value, 0),
            n_unresolved=counts.get(GapState.UNRESOLVED_GAP.value, 0),
            n_reentry=counts.get(GapState.REENTRY.value, 0),
            coordinate=plan.coordinate, policy=plan.policy,
            bytes_written=written, apply_count=1,
            n_overclaimed=plan.overclaimed_windows(),
        )
    finally:
        if owns:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


def _render_block(
    stream: Any, a: int, b: int, plan: CorrectionPlan,
    dt: np.dtype, hop: int
) -> np.ndarray:
    """渲染 ``[a, b)`` 一个输出块。

    块内窗索引单调不减, 因此在窗变化处切段, 每段一次 ``stream.read`` 拷贝。

    ``join`` 用**整数闭式**在段首求值, 而不是给每个样本调用 numpy 函数 —
    P3.10 的 driver 正是靠闭式避免了每样本坐标标签, 而 P3.11 自己的实现物化
    了一个 int64/样本 (C1305 长度上 1.8 GB, 峰值 RSS 3632 MiB) 。闭式与
    ``window_index`` 的等价性由 ``tests/audio_sync_p4`` 断言, 不是假设。
    """
    out = np.zeros(b - a, dtype=dt)
    shifts = plan.shifts
    n = plan.n_samples
    coord = plan.coordinate
    win = plan.window_samples
    # join 的加性偏移: nearest-centre 选 (pos + off) // hop, 其余选 pos // hop。
    # off = hop//2 - win//2, 在生产几何 (win = 3*hop) 下等于 -hop/2, 于是
    # block b>=1 整块取 S[b-1] — 与 P3.11 实测的 lag 一个 hop 一致。
    off = (hop // 2 - win // 2) if coord == "nearest-centre" else 0
    pos = a
    while pos < b:
        if coord == "latest-start" or coord == "sample-domain":
            w = pos // hop
        else:  # nearest-centre
            w = (pos + off) // hop
            if w < 0:
                w = 0
            elif w >= shifts.size:
                w = shifts.size - 1
        if w >= shifts.size:
            w = shifts.size - 1
        # 段结束 = 该窗在 k 轴上**不再被选中**的最小位置, 裁剪到块尾:
        #   join(k) = (k + off) // hop 变为 w 的区间上界是 (w+1)*hop - off + 1,
        #   等价地 join(pos) == w 当且仅当 pos < (w+1)*hop - off。
        # 用**精确边界**而不是"至少前进一个样本": 后者会在 join 相对 pos 滞后时
        # 让段退化成单样本, 循环变成 O(n) — 一个只在替代坐标下出现的性能悬崖,
        # 实测把一次 2 s 的 apply 变成分钟级。
        seg_end = min(b, (w + 1) * hop - off)
        if seg_end <= pos:
            # 只有 w 被裁剪到计划末尾时才可能发生; 前进一个样本是正确的
            # (该样本是最后一个属于该窗的样本) 。
            seg_end = min(b, pos + 1)
        shift = int(shifts[w])
        lo = max(0, pos + shift)
        hi = min(n, seg_end + shift)
        if hi > lo:
            dst0 = lo - a - shift
            read_into = getattr(stream, "read_into", None)
            if read_into is not None:
                read_into(out, dst0, lo, hi - lo)
            else:
                out[dst0: dst0 + (hi - lo)] = stream[lo:hi]
        pos = seg_end
    return out
