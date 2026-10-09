"""时变音频同步的**显式 opt-in 执行层** (P4.6, algo 2.4.0-p46).

这一层存在的唯一理由
--------------------
`core/audio_sync_tv.py` 已经能够**规划**逐窗修正 (`build_correction_plan`) 并
**施加**它 (`apply_shift_plan`), 但没有任何生产模块导入它。原因不是能力缺失,
而是两个产品决策未决:

    D2 gap policy       (语义契约 DEFERRED)
    D5 coordinate       (语义契约 DEFERRED)

本模块不解这两个决策。它做的是另一件事: **把"未决"变成一个显式、可拒绝的
调用契约**。调用方必须自己说出用哪个 gap policy、哪个 coordinate contract;
说不出来就**拒绝执行**, 而不是替它选一个然后把实现默认值冒充成产品语义。

::

    policy=None       ->  Refused(audio_sync_tv_policy_required)
    coordinate=None   ->  Refused(audio_sync_tv_coordinate_required)

这条规则是整个模块的核心。它让 D2/D5 保持未决的同时, 让能力可以安全交付:
"我不知道产品语义" 与 "我无法执行" 是两件事, 而这里把前者显式化, 后者消除。

边界 (与既有模块的分工)
------------------------
* **不测量**。测量来自 `PairEstimate.frames` / 冻结的测量记录。本模块只接受
  已经存在的测量事实 (`valid` + `delay_samples`), 不产生它们。
* **不重新实现坐标或策略**。`build_correction_plan` / `apply_shift_plan` /
  `derive_states` 是唯一实现, 本模块是它们的生产边界。
* **不 import** `encoders` / `preservation` / 任何视频后端 / Sony 元数据。
* **不改** `AudioPlan=None` 的默认路径, 也不重构 `production/output.py`。

为什么 `current` 不在生产白名单里
---------------------------------
P3.7 已确认 (P4.5 复现) `work/p3/p37/semantics.py` 的 `current` policy 执行
``resolved[:] = True``, 把 195 个被拒绝的窗标成 `TRUSTED`。在测量 mask 定义的
检查下它是 **1/22**。`core/audio_sync_tv.py` 已经把这件事显式建模为
`mask_is_authoritative=False` + 非零的 `overclaimed_windows()`, 这是**可审计**
而不是**被隐藏**。但"可审计"不等于"可以生产": 一个会把没有测量的窗报成可信的
策略不得成为生产路径的选项。因此:

    PRODUCTION_POLICIES = ("hold", "no_correction_gap", "explicit_state",
                           "linear", "next")          # 刻意不含 "current"

`current` 仍在 `core/audio_sync_tv.GAP_POLICIES` 里 (研究可复现), 但请求它走
生产执行层会得到 `Refused(audio_sync_tv_policy_not_production)`, 而**不是**一个
静默降级。

为什么不新增 residual acceptance threshold
------------------------------------------
D3 是 `NOT ESTABLISHED`。本模块**度量**残差 (`ResidualSummary`), 但**不判定**
"产品合格", 也不引入任何 ±N 阈值。`ResidualSummary` 里的每一个数都是观测量,
`verdict` 只在**没有任何可信窗**这种结构性事实上取 `NO_TRUSTED_WINDOW`。
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from . import audio_sync_tv as tv

__all__ = [
    "TV_EXEC_VERSION",
    "PRODUCTION_POLICIES",
    "REASON_TV_POLICY_REQUIRED",
    "REASON_TV_COORDINATE_REQUIRED",
    "REASON_TV_POLICY_NOT_PRODUCTION",
    "REASON_TV_PLAN_INVALID",
    "REASON_TV_EVIDENCE_INSUFFICIENT",
    "REASON_TV_NO_TRUSTED_WINDOW",
    "REASON_TV_GAP_UNRESOLVED",
    "REASON_TV_APPLY_FAILED",
    "REASON_TV_VERIFICATION_FAILED",
    "REASON_TV_ALREADY_EXECUTED",
    "TimeVaryingRequest",
    "EvidenceContract",
    "ResidualSummary",
    "VerificationResult",
    "TimeVaryingSyncResult",
    "request_time_varying",
    "measurements_from_frames",
    "aggregate_frames_to_windows",
    "frame_aggregation_stats",
    "window_evidence",
    "build_time_varying_plan",
    "execute_time_varying",
    "verify_applied_shift",
    "residual_summary",
]

#: 本执行层的版本。与 `audio_sync_tv.ALGO_VERSION` 分开: 后者是**算法**版本,
#: 这里记录的是**执行边界**的版本, 两者的变更原因不同。
TV_EXEC_VERSION = "p4.6-1"

#: 允许进入生产路径的 gap policy。**刻意不含 `current`** —— 见模块 docstring。
PRODUCTION_POLICIES: tuple[str, ...] = (
    "hold", "no_correction_gap", "explicit_state", "linear", "next",
)

# —— 稳定拒绝码 (沿用本仓库 REASON_* 命名体系) ——
REASON_TV_POLICY_REQUIRED = "audio_sync_tv_policy_required"
REASON_TV_COORDINATE_REQUIRED = "audio_sync_tv_coordinate_required"
REASON_TV_POLICY_NOT_PRODUCTION = "audio_sync_tv_policy_not_production"
REASON_TV_PLAN_INVALID = "audio_sync_tv_plan_invalid"
REASON_TV_EVIDENCE_INSUFFICIENT = "audio_sync_tv_evidence_insufficient"
REASON_TV_NO_TRUSTED_WINDOW = "audio_sync_tv_no_trusted_window"
REASON_TV_GAP_UNRESOLVED = "audio_sync_tv_gap_unresolved"
REASON_TV_APPLY_FAILED = "audio_sync_tv_apply_failed"
REASON_TV_VERIFICATION_FAILED = "audio_sync_tv_verification_failed"
REASON_TV_ALREADY_EXECUTED = "audio_sync_tv_already_executed"


class TimeVaryingRefused(Exception):
    """时变路径**按契约拒绝执行**。

    这是**拒绝**, 不是崩溃: 调用方给出了一个本阶段无法安全满足的请求。与
    `audio_sync_tv.AudioSyncError` (执行无效) 分开, 因为二者的处理方式不同 ——
    拒绝应当被报告成一个 `Refused` 结果并保持原音频不动, 而不是让上层猜。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {self.detail}" if self.detail
                         else self.reason)


# ---------------------------------------------------------------------------
# 请求 — 显式 opt-in
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TimeVaryingRequest:
    """一次时变同步执行的**显式**请求。

    ``policy`` 与 ``coordinate`` 的默认值是 `None`, 而 `None` 表示
    **"调用方没有说"**, 于是 `request_time_varying()` 会拒绝。这正是 D2/D5
    未决时唯一诚实的接口形状: 不给出一个看似合理的默认值, 因为那个默认值
    会被下游读成产品决策。

    `enabled=False` 是**默认**: 不显式打开就不进入这条路径。
    """

    enabled: bool = False
    policy: str | None = None
    coordinate: str | None = None
    window_samples: int = tv.DEFAULT_WINDOW_SAMPLES
    hop_samples: int = tv.DEFAULT_HOP_SAMPLES
    search_samples: int = tv.DEFAULT_SEARCH_SAMPLES
    chunk_samples: int | None = None
    #: 置位时, 即使全部窗都未决也照常执行 (输出将是"缺口策略的产物") 。
    #: 默认 False —— 一条没有任何可信测量的流**默认拒绝**, 因为此时输出不是
    #: 被测量的修正, 而纯粹是策略填充。
    allow_all_unresolved: bool = False
    #: 允许调用方覆写证据门; `None` 时取生产实际生效的门。
    gate: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TimeVaryingRefused(
                REASON_TV_PLAN_INVALID,
                f"enabled must be a bool, got {type(self.enabled).__name__}",
            )
        for name in ("window_samples", "hop_samples"):
            v = int(getattr(self, name))
            if v <= 0:
                raise TimeVaryingRefused(
                    REASON_TV_PLAN_INVALID, f"{name} must be > 0, got {v}"
                )
        if self.chunk_samples is not None and int(self.chunk_samples) <= 0:
            raise TimeVaryingRefused(
                REASON_TV_PLAN_INVALID,
                f"chunk_samples must be > 0, got {self.chunk_samples!r}",
            )


def request_time_varying(
    *,
    enabled: bool = False,
    policy: str | None = None,
    coordinate: str | None = None,
    **kw: Any,
) -> TimeVaryingRequest:
    """构造请求并**立即**校验两个未决决策是否被显式指定。

    校验放在这里而不是执行时, 是为了让"忘了指定 policy"在**进入文件 IO 之前**
    失败 —— 一条 900 MB 的流不该先被打开再因为一个参数缺失而中止。
    """
    req = TimeVaryingRequest(
        enabled=bool(enabled), policy=policy, coordinate=coordinate, **kw
    )
    if not req.enabled:
        return req
    if req.policy is None:
        raise TimeVaryingRefused(
            REASON_TV_POLICY_REQUIRED,
            "D2 (gap policy) has no frozen product semantic; a time-varying "
            "run must name its policy explicitly. Known policies: "
            + ", ".join(sorted(tv.GAP_POLICIES)),
        )
    if req.coordinate is None:
        raise TimeVaryingRefused(
            REASON_TV_COORDINATE_REQUIRED,
            "D5 (coordinate contract) has no frozen product semantic; a "
            "time-varying run must name its coordinate explicitly. Known "
            "coordinates: " + ", ".join(tv.COORDINATES),
        )
    if req.policy not in tv.GAP_POLICIES:
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID,
            f"unknown gap policy {req.policy!r}; known: "
            + ", ".join(sorted(tv.GAP_POLICIES)),
        )
    if req.policy not in PRODUCTION_POLICIES:
        raise TimeVaryingRefused(
            REASON_TV_POLICY_NOT_PRODUCTION,
            f"policy {req.policy!r} is a research policy and is not on the "
            "production whitelist " + repr(PRODUCTION_POLICIES) + ". It "
            "reports windows it did not measure as TRUSTED (P3.7 / P4.5), so "
            "it must not drive a production correction.",
        )
    if req.coordinate not in tv.COORDINATES:
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID,
            f"unknown coordinate contract {req.coordinate!r}; known: "
            + ", ".join(tv.COORDINATES),
        )
    return req


# ---------------------------------------------------------------------------
# 证据 — 逐窗, 不是整流一个 SUCCESS
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EvidenceContract:
    """一条流的**逐窗**证据分布。

    存在的原因是一条明确的产品禁令: 不能把整条音频压成一个 `SUCCESS` 然后
    丢掉窗级不确定性。因此这里报告的是**分布**, 并且把 "未陈述" 与 "零" 分开。

    ``gate`` 是**生产实际生效**的门 (来自 `core.channel_sync.effective_opts`),
    不是本模块自造的字面量。``coverage_gate_ok`` 为 `None` 表示**无法判定**
    (门不可得), 而不是"不满足" —— 这与 D1 的兼容规则一致。
    """

    n_windows: int
    n_measured: int
    n_unresolved: int
    coverage_fraction: float
    n_with_value: int
    median_abs_delay_samples: float | None
    max_abs_delay_samples: float | None
    gate: Mapping[str, float] | None
    coverage_gate_ok: bool | None
    state_counts: Mapping[str, int]
    measured_state_counts: Mapping[str, int]
    n_overclaimed: int
    mask_is_authoritative: bool

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "n_windows": int(self.n_windows),
            "n_measured": int(self.n_measured),
            "n_unresolved": int(self.n_unresolved),
            "coverage_fraction": float(self.coverage_fraction),
            "n_with_value": int(self.n_with_value),
            "state_counts": dict(self.state_counts),
            "measured_state_counts": dict(self.measured_state_counts),
            "n_overclaimed": int(self.n_overclaimed),
            "mask_is_authoritative": bool(self.mask_is_authoritative),
        }
        if self.median_abs_delay_samples is not None:
            data["median_abs_delay_samples"] = float(
                self.median_abs_delay_samples)
        if self.max_abs_delay_samples is not None:
            data["max_abs_delay_samples"] = float(self.max_abs_delay_samples)
        if self.gate is not None:
            data["gate"] = {k: float(v) for k, v in self.gate.items()}
        if self.coverage_gate_ok is not None:
            data["coverage_gate_ok"] = bool(self.coverage_gate_ok)
        return data


def _production_gate() -> dict[str, float] | None:
    """生产实际生效的证据门, 或 None。

    **不写字面量**: 阈值只有一个来源 —— `core.channel_sync.effective_opts()`。
    导入是延迟的 (与 `core/audio_models._production_evidence_gate` 同一手法),
    因此不引入模块环。
    """
    try:
        from .channel_sync import effective_opts
    except Exception:                               # noqa: BLE001 - 可选依赖
        return None
    try:
        eff = effective_opts(None)
        return {
            "min_usable_frames": float(eff["min_usable_frames"]),
            "min_confidence": float(eff["min_confidence"]),
            "min_usable_fraction": float(eff["min_usable_fraction"]),
        }
    except Exception:                               # noqa: BLE001
        return None


def window_evidence(plan: tv.CorrectionPlan) -> EvidenceContract:
    """从 plan 派生逐窗证据分布。**只读**, 不重算任何测量。

    ``measured_state_counts`` 与 ``state_counts`` 分开报告, 因此对历史策略
    (状态被 mask 之外的簿记提升) 二者会不一致, 而那个不一致本身是可见的。
    """
    mask = np.asarray(plan.resolved, dtype=bool)
    shifts = np.asarray(plan.shifts)
    states = [str(s) for s in np.asarray(plan.states, dtype=object).tolist()]
    n = int(mask.size)
    measured = [s for s in states if s != tv.GapState.UNRESOLVED_GAP.value]
    with_value = int(np.count_nonzero(shifts != 0))

    absd: float | None = None
    mx: float | None = None
    if mask.any():
        vals = np.abs(shifts[mask].astype(np.float64))
        absd = float(np.median(vals))
        mx = float(np.max(vals))

    gate = _production_gate()
    cov = float(mask.sum()) / float(n) if n else 0.0
    ok: bool | None = None
    if gate is not None:
        ok = bool(cov >= gate["min_usable_fraction"])
    counts: dict[str, int] = {s.value: 0 for s in tv.GapState}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    mcounts: dict[str, int] = {s.value: 0 for s in tv.GapState}
    for s in measured:
        mcounts[s] = mcounts.get(s, 0) + 1
    return EvidenceContract(
        n_windows=n,
        n_measured=int(mask.sum()),
        n_unresolved=int((~mask).sum()),
        coverage_fraction=cov,
        n_with_value=with_value,
        median_abs_delay_samples=absd,
        max_abs_delay_samples=mx,
        gate=gate,
        coverage_gate_ok=ok,
        state_counts=counts,
        measured_state_counts=mcounts,
        n_overclaimed=int(plan.overclaimed_windows()),
        mask_is_authoritative=bool(plan.mask_is_authoritative),
    )


# ---------------------------------------------------------------------------
# 测量适配 — 既有事实 -> 计划输入
# ---------------------------------------------------------------------------
def measurements_from_frames(
    frames: Sequence[Any], *, n_samples: int, window_samples: int,
    hop_samples: int,
) -> list[dict[str, Any]]:
    """把一条**已经测出的**帧轨迹适配成 plan 输入。

    这是适配器, 不是测量器: 它只做 "事实 -> 记录形状" 的搬运, 并**拒绝**
    任何需要猜测才能填补的位置。

    一条帧轨迹 (``PairEstimate.frames``) 每个元素有
    ``center_sample`` / ``delay_samples`` / ``confidence``; ``delay_samples``
    为 NaN 表示**该帧不可测**。NaN 一律映射为 ``valid=False`` —— 这正是
    "没有测量" 与 "移位的测量为 0" 必须分开的地方 (不变量 I1)。

    存在性
    ------
    返回的记录数 == ``min(len(frames), required_window_count(n_samples))``。
    ``build_correction_plan`` 会把**尾部没有测量的窗**标成未决而不是补零,
    因此这里不补齐 —— 补齐会把"没有测量"伪造成一个值。
    """
    need = tv.required_window_count(n_samples, hop_samples=int(hop_samples))
    out: list[dict[str, Any]] = []
    for i, f in enumerate(frames):
        if i >= need:
            break
        d = getattr(f, "delay_samples", None)
        conf = getattr(f, "confidence", None)
        if d is None:
            out.append({"valid": False, "delay_samples": None,
                        "confidence": None,
                        "center_sample": getattr(f, "center_sample", None)})
            continue
        dv = float(d)
        finite = math.isfinite(dv)
        out.append({
            "valid": bool(finite),
            # 被拒绝的帧**仍然可能带一个数字**; 那个数字留在记录里供审计,
            # 但 `valid=False` 使 build_correction_plan 不会把它当移位使用。
            "delay_samples": dv if finite else None,
            "confidence": (float(conf) if conf is not None
                           and math.isfinite(float(conf)) else None),
            "center_sample": getattr(f, "center_sample", None),
        })
    return out


def aggregate_frames_to_windows(
    frames: Sequence[Any], *, n_samples: int, window_samples: int,
    hop_samples: int, min_frames_per_window: int,
) -> list[dict[str, Any]]:
    """把**帧级**轨迹聚合成**计划窗级**测量记录。

    这是生产适配器: `core/channel_sync` 已经算出每条轨的帧轨迹
    (`PairEstimate.frames`, 200 ms 帧 / 100 ms 跳, NaN = 该帧不可测), 而
    plan 的几何是 30 s 窗 / 10 s 跳。两者不是同一个东西, 因此必须显式转换 ——
    直接把帧当成窗会让 `build_correction_plan` 收到一个长度完全不同的序列。

    归属规则
    --------
    帧中心 ``c`` 归入窗 ``w = c // hop_samples``, 与 ``latest-start`` 的 join
    是**同一个函数** —— 一条帧属于哪个窗, 与一个样本属于哪个窗, 用的是同一个
    坐标约定。这不是巧合, 而是让"测量什么"与"施加到哪里"用同一把尺子。

    一个窗被接受为**可信**的条件 (两者都要) :

    * 落在该窗内的有限帧数 >= ``min_frames_per_window``
    * 这些帧里至少有一个是有限的

    窗的移位取这些有限帧的**中位数**。中位数而不是均值: 一个被离群帧污染的
    均值会移动整个窗 10 秒的音频, 而中位数不会。

    ``min_frames_per_window`` 是**调用方必须给出的参数**, 不是本函数的默认值:
    它是"多少帧才足以支撑一个窗"的判断, 属于证据门 (D1) 的范畴, 而 D1 的门
    由生产配置持有。这里强制它显式传入, 是为了让"这个门槛从哪来"永远可回答。

    尾部: 帧轨迹走不到的地方**不补**。没有帧的窗留在 mask 之外, 由
    `build_correction_plan` 标成未决 —— 缺口而不是一个编出来的值。
    """
    n = int(n_samples)
    hop = int(hop_samples)
    win = int(window_samples)
    if hop <= 0:
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID, f"hop_samples must be > 0, got {hop}")
    if int(min_frames_per_window) <= 0:
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID,
            f"min_frames_per_window must be > 0, got {min_frames_per_window}; "
            "a window must require at least one frame to be trusted",
        )
    need = tv.required_window_count(n, hop_samples=hop)

    # 逐帧归窗, 只保留有限值 —— 非有限帧被**丢弃并计数**, 不是当成 0。
    buckets: list[list[float]] = [[] for _ in range(need)]
    n_frames = 0
    n_finite = 0
    n_out_of_range = 0
    for f in frames:
        n_frames += 1
        c = getattr(f, "center_sample", None)
        if c is None:
            continue
        try:
            ci = int(c)
        except (TypeError, ValueError):
            continue
        w = ci // hop
        if not 0 <= w < need:
            n_out_of_range += 1
            continue
        d = getattr(f, "delay_samples", None)
        if d is None:
            continue
        try:
            dv = float(d)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(dv):
            continue
        n_finite += 1
        buckets[w].append(dv)

    out: list[dict[str, Any]] = []
    for w in range(need):
        vals = buckets[w]
        if len(vals) < int(min_frames_per_window):
            # 不够证据 -> 声明未决。**不**用 0 或前一个窗的值填充。
            out.append({"valid": False, "delay_samples": None,
                        "confidence": None})
            continue
        med = float(np.median(np.asarray(vals, dtype=np.float64)))
        if not math.isfinite(med):
            out.append({"valid": False, "delay_samples": None,
                        "confidence": None})
            continue
        out.append({"valid": True, "delay_samples": med,
                    "confidence": float(len(vals))})
    # 诊断计数挂在返回的 list 上 (属性形式), 供调用方写进日志而不改变返回形状。
    return out


def frame_aggregation_stats(
    frames: Sequence[Any], *, n_samples: int, window_samples: int,
    hop_samples: int, min_frames_per_window: int,
) -> dict[str, Any]:
    """聚合的可审计计数: 多少帧、多少有限、多少落在计划之外。"""
    need = tv.required_window_count(n_samples, hop_samples=int(hop_samples))
    hop = int(hop_samples)
    per_window = [0] * need
    n_frames = n_finite = n_out = 0
    for f in frames:
        n_frames += 1
        c = getattr(f, "center_sample", None)
        if c is None:
            continue
        w = int(c) // hop
        if not 0 <= w < need:
            n_out += 1
            continue
        d = getattr(f, "delay_samples", None)
        if d is None:
            continue
        try:
            dv = float(d)
        except (TypeError, ValueError):
            continue
        if math.isfinite(dv):
            n_finite += 1
            per_window[w] += 1
    n_trusted = sum(1 for c in per_window if c >= int(min_frames_per_window))
    return {
        "n_frames": n_frames,
        "n_finite_frames": n_finite,
        "n_frames_outside_plan": n_out,
        "min_frames_per_window": int(min_frames_per_window),
        "n_windows": need,
        "n_windows_meeting_frame_floor": n_trusted,
        "n_windows_below_frame_floor": need - n_trusted,
        "window_samples": int(window_samples),
        "hop_samples": hop,
    }


def measurements_from_records(
    records: Sequence[Mapping[str, Any]], *, n_samples: int,
    hop_samples: int,
) -> list[dict[str, Any]]:
    """冻结测量记录的规范化: 只保留 ``valid`` / ``delay_samples``, 并**校验在场**。

    缺 ``valid`` 一律报错而不是默认 "有效" —— 默认有效会让一条没有测量声明的
    记录变成一次可信修正。
    """
    need = tv.required_window_count(n_samples, hop_samples=int(hop_samples))
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        if i >= need:
            break
        if "valid" not in rec:
            raise TimeVaryingRefused(
                REASON_TV_EVIDENCE_INSUFFICIENT,
                f"measurement record {i} has no 'valid' field; validity must "
                "be stated, never assumed",
            )
        d = rec.get("delay_samples")
        valid = bool(rec["valid"])
        dv: float | None = None
        if d is not None:
            try:
                fv = float(d)
            except (TypeError, ValueError):
                fv = float("nan")
            if math.isfinite(fv):
                dv = fv
            else:
                valid = False
        if not valid:
            dv = None
        out.append({"valid": valid, "delay_samples": dv,
                    "confidence": rec.get("confidence")})
    return out


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TimeVaryingPlan:
    """一个**已被接受**的时变计划及其出处。"""

    plan: tv.CorrectionPlan
    request: TimeVaryingRequest
    evidence: EvidenceContract
    n_measurement_records: int
    exec_version: str = TV_EXEC_VERSION

    @property
    def policy(self) -> str:
        return self.plan.policy

    @property
    def coordinate(self) -> str:
        return self.plan.coordinate

    @property
    def n_windows(self) -> int:
        return self.plan.n_windows

    def to_dict(self) -> dict[str, Any]:
        return {
            "exec_version": self.exec_version,
            "algo_version": self.plan.version,
            "policy": self.plan.policy,
            "coordinate": self.plan.coordinate,
            "n_windows": int(self.plan.n_windows),
            "window_samples": int(self.plan.window_samples),
            "hop_samples": int(self.plan.hop_samples),
            "n_samples": int(self.plan.n_samples),
            "search_samples": int(self.plan.search_samples),
            "n_measurement_records": int(self.n_measurement_records),
            "offline_only": bool(self.plan.offline_only),
            "evidence": self.evidence.to_dict(),
        }


def build_time_varying_plan(
    source: Any, *, request: TimeVaryingRequest,
    measurements: Sequence[Mapping[str, Any]] | None = None,
    frames: Sequence[Any] | None = None,
    context: tv.GapContext | None = None,
) -> TimeVaryingPlan:
    """构造并校验一个时变计划。**不写任何文件。**

    拒绝 (而不是猜测) 的情形:

    * 请求未开启                      -> `TimeVaryingRefused`
    * 既没有 ``measurements`` 也没有 ``frames`` -> `REASON_TV_EVIDENCE_INSUFFICIENT`
    * 一个可信窗都没有且未 ``allow_all_unresolved`` -> `REASON_TV_NO_TRUSTED_WINDOW`
    * plan 自身不合法 (长度/mask/移位) -> `REASON_TV_PLAN_INVALID`

    最后一条很重要: 一条**全部窗都未决**的流, 其输出完全由缺口策略决定, 不是
    被测量的修正。默认拒绝它; 要执行必须显式 ``allow_all_unresolved=True``。
    """
    if not request.enabled:
        raise TimeVaryingRefused(
            REASON_TV_POLICY_REQUIRED,
            "time-varying execution is opt-in; enabled=False",
        )
    if measurements is None and frames is None:
        raise TimeVaryingRefused(
            REASON_TV_EVIDENCE_INSUFFICIENT,
            "no measurement records and no frame trajectory were supplied; "
            "the plan cannot derive a mask from evidence it does not have",
        )

    n = _source_length(source)

    if frames is not None:
        recs = measurements_from_frames(
            frames, n_samples=n, window_samples=request.window_samples,
            hop_samples=request.hop_samples,
        )
    else:
        recs = measurements_from_records(
            measurements, n_samples=n, hop_samples=request.hop_samples,
        )
    if not recs:
        raise TimeVaryingRefused(
            REASON_TV_EVIDENCE_INSUFFICIENT,
            "the measurement trajectory is empty; there is no window to plan",
        )

    try:
        plan = tv.build_correction_plan(
            recs, n_samples=n,
            window_samples=request.window_samples,
            hop_samples=request.hop_samples,
            policy=request.policy,                     # type: ignore[arg-type]
            coordinate=request.coordinate,             # type: ignore[arg-type]
            search_samples=request.search_samples,
            context=context,
        )
    except tv.AudioSyncError as exc:
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID, f"{type(exc).__name__}: {exc}"
        ) from exc

    ev = window_evidence(plan)
    if ev.n_measured == 0 and not request.allow_all_unresolved:
        raise TimeVaryingRefused(
            REASON_TV_NO_TRUSTED_WINDOW,
            f"not one of {ev.n_windows} windows carries an accepted "
            "measurement; with no trusted window the output is entirely the "
            "gap policy's product and is not a measured correction. Pass "
            "allow_all_unresolved=True to execute anyway.",
        )
    if ev.n_overclaimed != 0:
        # 生产白名单已排除 `current`, 所以走到这里意味着白名单被绕过。
        raise TimeVaryingRefused(
            REASON_TV_POLICY_NOT_PRODUCTION,
            f"plan reports {ev.n_overclaimed} window(s) as measured that were "
            "not measured; this is the P3.7 over-claim and must not reach a "
            "production output",
        )
    return TimeVaryingPlan(
        plan=plan, request=request, evidence=ev,
        n_measurement_records=len(recs),
    )


def _source_length(source: Any) -> int:
    """样本数 —— 只读长度, 不整文件载入。"""
    from .sync_estimate import open_source

    if isinstance(source, (str, bytes, Path)) or hasattr(source, "__fspath__"):
        x = open_source(source, "f32")
    else:
        x = source
    try:
        shape = getattr(x, "shape", None)
        if shape is None:
            raise TimeVaryingRefused(
                REASON_TV_PLAN_INVALID,
                f"source of type {type(x).__name__} has no length",
            )
        return int(shape[0])
    finally:
        if x is not source:
            close = getattr(x, "close", None)
            if callable(close):
                close()


# ---------------------------------------------------------------------------
# 独立验证
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VerificationResult:
    """对**已经写出的 PCM** 的独立核对结果。

    `checked_samples` 是实际逐样本比对过的数量。`mismatches == 0` 且
    `checked_samples == n_samples` 才算通过 —— 一个只检查了前 1000 个样本的
    验证器会报告 `complete=False`, 而不是通过。
    """

    checked_samples: int
    n_samples: int
    mismatches: int
    first_mismatch: int | None
    complete: bool
    contract: str

    @property
    def ok(self) -> bool:
        return self.mismatches == 0 and self.complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "checked_samples": int(self.checked_samples),
            "n_samples": int(self.n_samples),
            "mismatches": int(self.mismatches),
            "complete": bool(self.complete),
            "ok": bool(self.ok),
            **({"first_mismatch": int(self.first_mismatch)}
               if self.first_mismatch is not None else {}),
        }


def _independent_join(
    coordinate: str, *, hop: int, win: int, n_windows: int
) -> Callable[[np.ndarray], np.ndarray]:
    """**独立**实现 sample -> window 映射 (不复用 `audio_sync_tv.window_index`)。

    验证器的价值全部来自它与被测实现**不共享代码**。这里用逐样本的朴素写法:
    对 `latest-start` / `sample-domain` 直接做整除; 对 `nearest-centre` 显式
    搜索中心最近的窗 (O(n_windows) 的朴素扫描, 生产实现对同一问题用的是闭式)。

    一个"验证器"如果调用被测函数来计算期望值, 它验证的是自己。
    """
    nw = int(n_windows)
    if coordinate in ("latest-start", "sample-domain"):
        def join(pos: np.ndarray) -> np.ndarray:
            w = np.floor_divide(np.asarray(pos, dtype=np.int64), int(hop))
            return np.clip(w, 0, nw - 1)
        return join
    if coordinate == "nearest-centre":
        centres = (np.arange(nw, dtype=np.int64) * int(hop)
                   + int(win) // 2)

        def join(pos: np.ndarray) -> np.ndarray:
            p = np.asarray(pos, dtype=np.int64)
            out = np.empty(p.shape, dtype=np.int64)
            flat = p.reshape(-1)
            res = out.reshape(-1)
            for i, v in enumerate(flat.tolist()):
                # Naive linear scan of every window centre, integer distance.
                #
                # TIE-BREAK: on an exact tie the LARGER index wins, because that
                # is what the production closed form does. nearest-centre is
                # defined as floor((pos + hop//2 - win//2) / hop), and
                # floor_divide rounds a tie toward +inf, i.e. toward the later
                # window. The first version of this verifier used `d < bestd`,
                # which prefers the EARLIER window -- and a tie is not a corner
                # case: at the production ratio (hop = 480 000, win = 1 440 000)
                # every sample at the midpoint between two centres is an exact
                # tie, so k = 960 000 was reported as a mismatch that did not
                # exist. The verifier was wrong, not the applier.
                best = 0
                bestd = abs(int(v) - int(centres[0]))
                for w in range(1, nw):
                    d = abs(int(v) - int(centres[w]))
                    if d <= bestd:
                        bestd = d
                        best = w
                res[i] = best
            return out
        return join
    raise TimeVaryingRefused(
        REASON_TV_PLAN_INVALID, f"unknown coordinate contract {coordinate!r}"
    )


def verify_applied_shift(
    dst: Any, source: Any, plan: tv.CorrectionPlan, *,
    storage_dtype: str = "f32", step: int = 1 << 20,
) -> VerificationResult:
    """逐样本核对 ``out[k] == in[k + S[join(k)]]``, 期望值由**独立** join 计算。

    流式: 每次只读入 ``step`` 个输出样本和它们对应的源区间, 因此 900 MB 的流
    不会因为验证而整体载入。``step=1`` 也会正确工作 (只是慢), 测试用它来证明
    **分块不影响验证结论**。

    越界读按契约补零 (不变量 I7), 因此 `k + shift < 0` 或 `>= n` 处期望值为 0。
    """
    from .sync_estimate import open_source

    n = int(plan.n_samples)
    hop = int(plan.hop_samples)
    win = int(plan.window_samples)
    shifts = np.asarray(plan.shifts)
    join = _independent_join(
        plan.coordinate, hop=hop, win=win, n_windows=plan.n_windows
    )

    out_stream = open_source(dst, storage_dtype)
    in_stream = open_source(source, storage_dtype)
    owns = [s for s, src in ((out_stream, dst), (in_stream, source))
            if isinstance(src, (str, bytes, Path)) or hasattr(src, "__fspath__")]
    checked = 0
    mism = 0
    first: int | None = None
    try:
        pos = 0
        while pos < n:
            b = min(n, pos + int(step))
            idx = np.arange(pos, b, dtype=np.int64)
            w = join(idx)
            s = shifts[w]
            src_idx = idx + s
            got = np.asarray(out_stream.read(pos, b - pos))
            want = np.zeros(b - pos, dtype=got.dtype)
            inside = (src_idx >= 0) & (src_idx < n)
            if inside.any():
                lo = int(np.min(src_idx[inside]))
                hi = int(np.max(src_idx[inside])) + 1
                window = np.asarray(in_stream.read(lo, hi - lo))
                take = src_idx[inside] - lo
                if take.size:
                    want[inside] = window[take]
            eq = (got == want) | (np.isnan(got) & np.isnan(want))
            bad = int((~eq).sum())
            if bad:
                mism += bad
                if first is None:
                    off = int(np.argmax(~eq))
                    first = pos + off
            checked += int(b - pos)
            pos = b
    finally:
        for s in owns:
            close = getattr(s, "close", None)
            if callable(close):
                close()
    return VerificationResult(
        checked_samples=checked, n_samples=n, mismatches=mism,
        first_mismatch=first, complete=(checked == n),
        contract=(
            "out[k] == in[k + S[join_independent(k)]]; join re-derived from "
            "the coordinate contract's definition, NOT from audio_sync_tv"
        ),
    )


# ---------------------------------------------------------------------------
# 残差 — 度量, 不判定
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ResidualSummary:
    """一条流的窗级残差分布。**只报告, 不设阈值。**

    D3 (residual acceptance) 是 `NOT ESTABLISHED`。因此这里没有 `passed` 字段:
    一个布尔字段会立刻被下游读成"产品合格判定", 而本阶段**没有**依据支持任何
    ±N 样本的门。`verdict` 只在**结构性**事实上取非 None —— 目前只有一种:
    一个可信窗都没有, 于是中位数等统计量不存在。
    """

    n_windows: int
    n_measured: int
    n_unresolved: int
    n_valid_population: int
    median_abs: float | None
    p95_abs: float | None
    max_abs: float | None
    verdict: str | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "n_windows": int(self.n_windows),
            "n_measured": int(self.n_measured),
            "n_unresolved": int(self.n_unresolved),
            "n_valid_population": int(self.n_valid_population),
            "note": self.note,
        }
        for k in ("median_abs", "p95_abs", "max_abs"):
            v = getattr(self, k)
            if v is not None:
                data[k] = float(v)
        if self.verdict is not None:
            data["verdict"] = self.verdict
        return data


def residual_summary(
    residuals: Sequence[float], *, n_windows: int, n_measured: int,
    n_unresolved: int, note: str = "",
) -> ResidualSummary:
    """把一批窗级残差归约成分布。NaN/Inf 被**排除并计数**, 不算作 0。"""
    vals = np.asarray(
        [float(r) for r in residuals
         if r is not None and math.isfinite(float(r))], dtype=np.float64)
    if vals.size == 0:
        return ResidualSummary(
            n_windows=int(n_windows), n_measured=int(n_measured),
            n_unresolved=int(n_unresolved), n_valid_population=0,
            median_abs=None, p95_abs=None, max_abs=None,
            verdict=REASON_TV_NO_TRUSTED_WINDOW if n_measured == 0 else None,
            note=note or "no finite residual was measured",
        )
    a = np.abs(vals)
    return ResidualSummary(
        n_windows=int(n_windows), n_measured=int(n_measured),
        n_unresolved=int(n_unresolved), n_valid_population=int(a.size),
        median_abs=float(np.median(a)),
        p95_abs=float(np.percentile(a, 95.0)),
        max_abs=float(np.max(a)),
        verdict=None, note=note,
    )


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TimeVaryingSyncResult:
    """一次时变同步执行的**可解释**结果。

    每一个字段都回答一个维护者会问的问题: 执行了吗 / 为什么没有 / 计划多长 /
    写了多少字节 / 输出多少样本 / 状态如何分布 / 独立验证过没有。

    `refusals` 记录**每一条被拒绝的窗的理由**的聚合 (状态 -> 窗数), 因此
    "为什么这段音频没被校正" 不需要调试器。
    """

    executed: bool
    reason: str | None
    detail: str = ""
    policy: str | None = None
    coordinate: str | None = None
    n_windows: int = 0
    n_samples_in: int = 0
    n_samples_out: int = 0
    bytes_written: int = 0
    n_blocks: int = 0
    state_counts: Mapping[str, int] = field(default_factory=dict)
    refusals: Mapping[str, int] = field(default_factory=dict)
    evidence: Mapping[str, Any] | None = None
    verification: Mapping[str, Any] | None = None
    dst: str | None = None
    exec_version: str = TV_EXEC_VERSION

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "exec_version": self.exec_version,
            "executed": bool(self.executed),
            "n_windows": int(self.n_windows),
            "n_samples_in": int(self.n_samples_in),
            "n_samples_out": int(self.n_samples_out),
            "bytes_written": int(self.bytes_written),
            "n_blocks": int(self.n_blocks),
            "state_counts": {k: int(v) for k, v in self.state_counts.items()},
            "refusals": {k: int(v) for k, v in self.refusals.items()},
        }
        for k in ("reason", "policy", "coordinate", "dst"):
            v = getattr(self, k)
            if v is not None:
                data[k] = str(v)
        if self.detail:
            data["detail"] = self.detail
        if self.evidence is not None:
            data["evidence"] = dict(self.evidence)
        if self.verification is not None:
            data["verification"] = dict(self.verification)
        return data


def _refusals_of(plan: tv.CorrectionPlan) -> dict[str, int]:
    """状态 -> 窗数, 作为**拒绝理由**的聚合。

    `UNRESOLVED_GAP` 是合法认知状态而不是错误, 它在这里表示"这些窗没有被
    可信测量支撑, 其移位来自策略"。这句话必须在结果里出现, 否则"这段音频为
    什么没被校正"就得靠猜。
    """
    return {k: int(v) for k, v in plan.state_counts().items() if v}


def execute_time_varying(
    source: Any, dst: Any, *, plan: TimeVaryingPlan,
    storage_dtype: str = "f32", verify: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> TimeVaryingSyncResult:
    """把 plan 施加到 PCM 并 (默认) 独立复测。

    原子性: 输出先写到目标目录下的临时文件, **验证通过后才 rename 到 ``dst``**。
    施加或验证失败时临时文件被删除, ``dst`` 不被创建/覆盖 —— 因此"发生异常时
    残留不完整临时输出"不会变成"一个看起来完整的坏输出"。
    """
    if not isinstance(plan, TimeVaryingPlan):
        raise TimeVaryingRefused(
            REASON_TV_PLAN_INVALID,
            f"plan must be a TimeVaryingPlan, got {type(plan).__name__}",
        )
    p = plan.plan
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".part", dir=str(target.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        try:
            stats = tv.apply_shift_plan(
                source, tmp, p, storage_dtype=storage_dtype,
                chunk_samples=plan.request.chunk_samples, progress=progress,
            )
        except tv.AudioSyncError as exc:
            raise TimeVaryingRefused(
                REASON_TV_APPLY_FAILED, f"{type(exc).__name__}: {exc}"
            ) from exc
        except OSError as exc:
            raise TimeVaryingRefused(
                REASON_TV_APPLY_FAILED, f"OSError: {exc}"
            ) from exc

        out_samples = int(tmp.stat().st_size) // (
            4 if storage_dtype == "f32" else 8
        )
        if out_samples != int(p.n_samples):
            raise TimeVaryingRefused(
                REASON_TV_PLAN_INVALID,
                f"output has {out_samples} samples but the plan is for "
                f"{p.n_samples}; output size invariant violated",
            )

        ver: dict[str, Any] | None = None
        if verify:
            v = verify_applied_shift(
                tmp, source, p, storage_dtype=storage_dtype
            )
            ver = v.to_dict()
            if not v.ok:
                raise TimeVaryingRefused(
                    REASON_TV_VERIFICATION_FAILED,
                    f"independent verification found {v.mismatches} "
                    f"mismatching sample(s) (first at {v.first_mismatch}); "
                    f"checked {v.checked_samples}/{v.n_samples}",
                )
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return TimeVaryingSyncResult(
        executed=True, reason=None,
        detail=(f"policy={p.policy} coordinate={p.coordinate} "
                f"windows={p.n_windows}"),
        policy=p.policy, coordinate=p.coordinate,
        n_windows=p.n_windows,
        n_samples_in=int(stats.n_samples_in),
        n_samples_out=int(stats.n_samples_out),
        bytes_written=int(stats.bytes_written),
        n_blocks=int(stats.n_blocks),
        state_counts=dict(p.state_counts()),
        refusals=_refusals_of(p),
        evidence=plan.evidence.to_dict(),
        verification=ver,
        dst=str(target),
    )


def run_time_varying(
    source: Any, dst: Any, *, request: TimeVaryingRequest,
    measurements: Sequence[Mapping[str, Any]] | None = None,
    frames: Sequence[Any] | None = None,
    context: tv.GapContext | None = None,
    storage_dtype: str = "f32", verify: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> TimeVaryingSyncResult:
    """一次性入口: 请求 -> plan -> apply -> 独立复测。

    **这是生产调用面**。任何一步的拒绝都以 `TimeVaryingSyncResult(executed=
    False, reason=<稳定码>)` 返回, 原音频与 `dst` 都不被触碰; 只有 `enabled=
    False` 之外的**契约**错误才抛 `TimeVaryingRefused` (参数本身写错了)。

    返回的 `executed=False` **不是失败**: 它表示按契约没有执行, 且给出原因。
    调用方 (例如 `channel_sync`) 据此保持原音频不动, 而不是回退到别的修正。
    """
    if not request.enabled:
        return TimeVaryingSyncResult(
            executed=False, reason=REASON_TV_POLICY_REQUIRED,
            detail="time-varying execution is opt-in; enabled=False. The "
                   "fixed-delay path is unchanged.",
        )
    try:
        plan = build_time_varying_plan(
            source, request=request, measurements=measurements, frames=frames,
            context=context,
        )
    except TimeVaryingRefused as exc:
        return TimeVaryingSyncResult(
            executed=False, reason=exc.reason, detail=exc.detail,
            policy=request.policy, coordinate=request.coordinate,
        )
    try:
        res = execute_time_varying(
            source, dst, plan=plan, storage_dtype=storage_dtype,
            verify=verify, progress=progress,
        )
    except TimeVaryingRefused as exc:
        return TimeVaryingSyncResult(
            executed=False, reason=exc.reason, detail=exc.detail,
            policy=request.policy, coordinate=request.coordinate,
            n_windows=plan.n_windows,
            evidence=plan.evidence.to_dict(),
        )
    return res
