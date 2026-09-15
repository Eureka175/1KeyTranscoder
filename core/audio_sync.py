"""Arbitrary-reference audio delay correction (v0.7.1 之后的下一个开发周期).

本模块只解决一件事, 且只解决这一件:

    reference AudioChannel          (任意一路, 由调用方选)
            ↓  跨来源 constant delay 估计
    per-target constant offset      (样本, 整数)
            ↓  AudioChannel.sync
    AudioTimeline                   (唯一 render alignment 权威)
            ↓  AudioRouter / AudioMixer

核心原则: **Reference is data, not topology.**

    reference 是"这一次同步任务选出来的一路 AudioChannel",
    不是 camera / recorder / stream 0 / sources[0] / 某条预设路径。

任何 `AudioChannel` 都可以当 reference —— media 源、外挂 WAV、
任意 source / stream / track 里的任意声道, 只要与 targets 共享采样率、
有足够信号、能测出恒定偏移。**不存在"只能作 target"的声道**。

--------------------------------------------------------------------------
复用而非重写 (边界声明)
--------------------------------------------------------------------------
估计算法**不重新实现**: 继续使用既有的 `core.sync_estimate.estimate_pair`
(两阶段 GCC-PHAT + 相位斜率精估 + 轨迹恒定性分类)。本模块新增的只有三层:

    1. reference selection   —— 把 reference 从 `anchor_candidates` 候选顺序
                                改成"调用方显式指定的 AudioChannel";
    2. measurement plumbing  —— `channel_id` → (source, stream, channel)
                                → PCM 读取 → `estimate_pair`;
    3. timeline application  —— 结果写回 `AudioChannel.sync`, 由既有的
                                `core.audio_timeline.resolve_timeline()`
                                统一消费。

`core/channel_sync.py` / `sync_fix.py` / `sync_estimate.py` /
`mp4_channel_sync.py` **均未修改**: 文件级 channel_sync 管线按
`audio_position` 选锚点并输出报告, 本模块按 channel identity 选 reference,
两者并存, 各自服务不同场景。

--------------------------------------------------------------------------
offset 符号 (沿用既有实测语义, 不另立一套)
--------------------------------------------------------------------------
`core/channel_sync.py` 的方向约定 (实测钉死, 见 `core/sync_fix.py`):

    delay > 0  = 目标轨比锚点**晚到**;  修正动作 out[n] = in[n + delay]

`core/audio_timeline.source_to_timeline()` 的换算是:

    timeline_sample = source_sample - offset_samples

两式同向, 因此 **本模块的 offset 就是 `estimate_pair` 的 `delay_samples`,
不需要任何符号翻转**:

    ref impulse @1000, tgt impulse @1060  (target 晚到 60)
        → delay_samples = +60
        → AudioChannel.sync.offset_samples = +60
        → tgt 的 impulse 落到 timeline 1000  ==  ref ✓

--------------------------------------------------------------------------
明确不做 (本阶段边界)
--------------------------------------------------------------------------
漂移校正 / resampling / time-stretch / 变速 / 插值 / 分数样本修正 /
loudness 归一 / AGC / limiter / compressor / EQ / 降噪 / 频谱处理 /
音频编码 / MP4 mux / selective MP4 retention / 新 CLI。

漂移只**检出并报告**(`drift_ppm` / `constant` 原样带出), 不修正。

--------------------------------------------------------------------------
依赖
--------------------------------------------------------------------------
`numpy` + `core.sync_estimate` (+ 其可选依赖 `scipy`)。缺失时给稳定
reason code `sync_estimation_failed`, 不静默降级成"看起来对齐了"。
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .audio_models import (
    AudioChannel,
    AudioPlan,
    AudioSyncResult,
    SyncStatus,
)
from .audio_plan import effective_mapping
from .audio_timeline import AudioRenderError, SyncApplication

__all__ = [
    "AudioSyncError",
    "PairSyncEstimate",
    "PCMChannelWindow",
    "SyncEstimateResult",
    "SyncPlan",
    "apply_sync_result",
    "clear_sync_result",
    "estimate_sync",
    "plan_from_plan",
    "plan_sync",
    "reference_channel",
    "require_sync_plan",
    "validate_sync_plan",
    "REASON_REFERENCE_MISSING",
    "REASON_REFERENCE_NOT_SELECTED",
    "REASON_REFERENCE_EQUALS_TARGET",
    "REASON_SYNC_CHANNEL_NOT_FOUND",
    "REASON_SYNC_DUPLICATE_TARGET",
    "REASON_SYNC_ESTIMATION_FAILED",
    "REASON_SYNC_INSUFFICIENT_SIGNAL",
    "REASON_SYNC_SAMPLE_RATE_MISMATCH",
    "REASON_SYNC_UNSUPPORTED_FORMAT",
    "REASON_TARGET_NOT_SELECTED",
]


# ---------------------------------------------------------------------------
# 稳定 reason codes (新增; 不与既有 reason code 重复)
# ---------------------------------------------------------------------------

REASON_REFERENCE_MISSING = "reference_missing"
REASON_REFERENCE_NOT_SELECTED = "reference_not_selected"
REASON_TARGET_NOT_SELECTED = "target_not_selected"
REASON_REFERENCE_EQUALS_TARGET = "reference_equals_target"
REASON_SYNC_CHANNEL_NOT_FOUND = "sync_channel_not_found"
REASON_SYNC_DUPLICATE_TARGET = "sync_duplicate_target"
REASON_SYNC_SAMPLE_RATE_MISMATCH = "sync_sample_rate_mismatch"
REASON_SYNC_UNSUPPORTED_FORMAT = "sync_unsupported_format"
REASON_SYNC_INSUFFICIENT_SIGNAL = "sync_insufficient_signal"
REASON_SYNC_ESTIMATION_FAILED = "sync_estimation_failed"


class AudioSyncError(ValueError):
    """同步规划/估计期错误。携带稳定 `reason`, 便于逐项判定与日志比对。

    音频同步错误"听不出来", 因此本层**宁可拒绝**也不"尽可能给一个结果":
    任何不确定的情形一律以明确 reason code 失败, 由调用方决定是否继续。
    """

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        location: str | None = None,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.location = location

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"reason": self.reason, "detail": self.detail}
        if self.location is not None:
            data["location"] = self.location
        return data


# ---------------------------------------------------------------------------
# PCM adapter: estimate_pair 只要求 shape[0] + 单步切片
# ---------------------------------------------------------------------------


class PCMChannelWindow:
    """把一个 `(stream, channel)` 暴露成 `estimate_pair` 需要的只读 1-D 视图。

    `estimate_pair` / `read_f64` 对源对象的要求只有两条:

        * `src.shape[0]` -> 样本总数
        * `src[a:b]`     -> 该窗口的 1-D 数组 (越界自动裁剪)

    既有实现是 `core.sync_estimate.RawStream` (raw 文件上的有界窗口读取)。
    本类提供**同一接口**, 但底层是 `core.audio_pcm.AudioPCMReader` ——
    也就是 P3A 已经解码好的 canonical float32 数据, 因此估计与渲染
    消费的是**同一份**样本, 不需要第二套解码路径。

    reader 只要求提供:

        read_frames(stream_id, *, channel_index, start, count) -> ndarray
        actual_samples(stream_id) 或 eof_sample(stream_id)      -> int

    只读语义: 返回值一律不可写 (调用方 `read_f64` 本来也只做复制)。
    """

    __slots__ = ("_reader", "_stream_id", "_channel_index", "_n", "_dtype")

    def __init__(
        self,
        reader: Any,
        stream_id: str,
        channel_index: int,
        sample_count: int,
        *,
        dtype: str = "f32",
    ) -> None:
        self._reader = reader
        self._stream_id = str(stream_id)
        self._channel_index = int(channel_index)
        self._n = max(0, int(sample_count))
        # canonical PCM 是 float32; estimate_pair 内部一律 read_f64 提升,
        # 因此这里如实声明 f32, 不假装高位深。
        self._dtype = dtype

    @property
    def shape(self) -> tuple[int]:
        return (self._n,)

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, slice):
            idx = int(key)
            if idx < 0:
                idx += self._n
            if not 0 <= idx < self._n:
                raise IndexError(idx)
            return self.read(idx, 1)[0]
        if key.step not in (None, 1):
            raise ValueError("PCMChannelWindow supports unit-step slices only")
        start = 0 if key.start is None else int(key.start)
        stop = self._n if key.stop is None else int(key.stop)
        if start < 0:
            start += self._n
        if stop < 0:
            stop += self._n
        start = max(0, min(start, self._n))
        stop = max(start, min(stop, self._n))
        return self.read(start, stop - start)

    def read(self, start: int, count: int) -> Any:
        """[start, start+count) 的只读窗口 (越界裁剪, 不补零)。"""
        import numpy as np

        start = max(0, int(start))
        count = min(int(count), self._n - start)
        if count <= 0:
            return np.empty(0, dtype="<f4")
        out = self._reader.read_frames(
            self._stream_id,
            channel_index=self._channel_index,
            start=start,
            count=count,
        )
        out = np.asarray(out, dtype="<f4")
        out.flags.writeable = False
        return out

    def close(self) -> None:
        """无自有句柄 (reader 由调用方持有/关闭) —— 保持与 RawStream 同形。"""
        return None


# ---------------------------------------------------------------------------
# Reader seam
# ---------------------------------------------------------------------------


class _ReaderSeam:
    """参考流与目标流的读取接缝 (生产用 AudioPCMReader; 测试可注入替代)。

    存在的意义: 真实素材的"同一段音频"只能来自 reader 的解码结果, 不能
    用合成数组假装 —— 而单元测试又需要在不跑 ffmpeg 的前提下验证
    reference 置换/坐标平移等**纯数学**不变量。两者共用同一份代码路径,
    只是读取后端不同 (与 P3A 的 `reader_factory` 策略一致)。
    """

    @staticmethod
    def open_reader(
        plan: AudioPlan,
        *,
        ffmpeg: Path | None,
        work_dir: Path | None,
        chunk_frames: int,
        reader: Any = None,
    ) -> tuple[Any, bool]:
        """-> (reader, owns) —— owns=True 表示本函数创建、需由调用方关闭。"""
        if reader is not None:
            return reader, False
        if ffmpeg is None or work_dir is None:
            raise AudioSyncError(
                REASON_SYNC_ESTIMATION_FAILED,
                "sync estimation needs either an AudioPCMReader or "
                "(ffmpeg + work_dir) to decode PCM",
            )
        from .audio_pcm import PCM_CHUNK_FRAMES_DEFAULT, AudioPCMReader

        return (
            AudioPCMReader(
                plan,
                ffmpeg=Path(ffmpeg),
                work_dir=Path(work_dir),
                chunk_frames=int(chunk_frames or PCM_CHUNK_FRAMES_DEFAULT),
            ),
            True,
        )


def _stream_sample_count(reader: Any, stream_id: str) -> int:
    """reader -> 该流实际解码样本数 (EOF 的最终事实)。"""
    for name in ("actual_samples", "eof_sample"):
        fn = getattr(reader, name, None)
        if callable(fn):
            try:
                return int(fn(stream_id))
            except Exception:                      # noqa: BLE001 - 换下一个探针
                continue
    describe = getattr(reader, "describe", None)
    if callable(describe):
        for row in describe():
            if not isinstance(row, Mapping):
                continue
            if f"{row.get('source_id')}:s{row.get('stream_index')}" == stream_id:
                return int(row.get("actual_samples") or 0)
    raise AudioSyncError(
        REASON_SYNC_ESTIMATION_FAILED,
        f"reader cannot report the decoded sample count of {stream_id!r}",
        location=stream_id,
    )


# ---------------------------------------------------------------------------
# 同步计划 (reference 是 AudioChannel 身份, 不是 topology)
# ---------------------------------------------------------------------------


@dataclass
class SyncPlan:
    """一次同步任务: **1 个 reference channel + N 个 target channel**。

        reference: recorder:s2:c1        ← 任意 AudioChannel
        targets:   cameraA:s0:c0, cameraA:s0:c1, external:s0:c0

    身份只由 `channel_id` (`{source_id}:s{stream}:c{channel}`) 决定,
    **不涉及** source_type / path / stream 序号 / 来源排列顺序。
    "camera → recorder" 只是 `reference="camera:s0:c0"` 的一个具体实例,
    反过来 `reference="recorder:s2:c1"` 同样合法。

    约束:
      * reference 不得出现在 targets 里 (否则 `reference_equals_target`);
      * targets 不得重复 (否则 `sync_duplicate_target`);
      * reference 与 targets 必须都在 AudioPlan 的**有效映射**
        (`effective_mapping`) 里 —— 被 selection 排除的声道不会被
        偷偷从原 source 里找回来。
    """

    reference_channel_id: str
    target_channel_ids: list[str] = field(default_factory=list)
    method: str = "gcc_phat_pairwise"
    parameters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.reference_channel_id = str(self.reference_channel_id or "")
        self.target_channel_ids = [str(t) for t in self.target_channel_ids]
        self.method = str(self.method or "gcc_phat_pairwise")
        self.notes = [str(n) for n in self.notes]

    # -- 事实 -------------------------------------------------------------

    @property
    def target_count(self) -> int:
        return len(self.target_channel_ids)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reference_channel_id": self.reference_channel_id,
            "target_channel_ids": list(self.target_channel_ids),
            "method": self.method,
            "target_count": int(self.target_count),
        }
        if self.parameters:
            data["parameters"] = dict(self.parameters)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SyncPlan":
        if not data:
            raise AudioSyncError(
                REASON_REFERENCE_MISSING, "sync plan is empty"
            )
        return cls(
            reference_channel_id=str(data.get("reference_channel_id") or ""),
            target_channel_ids=[
                str(t) for t in (data.get("target_channel_ids") or [])
            ],
            method=str(data.get("method") or "gcc_phat_pairwise"),
            parameters=dict(data.get("parameters") or {}),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


def plan_sync(
    reference_channel_id: str,
    target_channel_ids: Iterable[str] = (),
    *,
    method: str = "gcc_phat_pairwise",
    parameters: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> SyncPlan:
    """建同步计划。`reference_channel_id` 必须是**显式给出**的 channel id。

    刻意不提供"默认 reference": 任何 `sources[0]` / `first_audio` /
    `camera` 之类的隐式规则都会让 reference 变成 topology 而不是 data。
    目标集合为空时表示"除 reference 外的全部有效输出声道"
    (由 `plan_from_plan()` 展开)。
    """
    if not str(reference_channel_id or ""):
        raise AudioSyncError(
            REASON_REFERENCE_MISSING,
            "reference_channel_id must be an explicit AudioChannel id "
            "(e.g. 'recorder:s2:c1'); no implicit default is defined",
        )
    return SyncPlan(
        reference_channel_id=str(reference_channel_id),
        target_channel_ids=[str(t) for t in target_channel_ids],
        method=str(method),
        parameters=dict(parameters or {}),
        notes=[str(n) for n in notes],
    )


def plan_from_plan(
    plan: AudioPlan,
    reference_channel_id: str,
    *,
    target_channel_ids: Iterable[str] | None = None,
    method: str = "gcc_phat_pairwise",
    parameters: Mapping[str, Any] | None = None,
) -> SyncPlan:
    """在既有 `AudioPlan` 上建同步计划 (targets 省略 = 其余全部有效声道)。"""
    if target_channel_ids is None:
        targets = [
            cid for cid in _effective_channel_ids(plan)
            if cid != str(reference_channel_id)
        ]
    else:
        targets = [str(t) for t in target_channel_ids]
    return plan_sync(
        reference_channel_id,
        targets,
        method=method,
        parameters=parameters,
    )


def _effective_channel_ids(plan: AudioPlan) -> list[str]:
    """有效映射的输出声道 id, 顺序 = output index (selection 已生效)。"""
    return [
        str(e.get("source_channel_id") or "")
        for e in effective_mapping(plan)
        if str(e.get("source_channel_id") or "")
    ]


# ---------------------------------------------------------------------------
# 校验 (全部为稳定 reason code, 不抛裸异常)
# ---------------------------------------------------------------------------


def _channel_or_none(plan: AudioPlan, channel_id: str) -> AudioChannel | None:
    return plan.channel(str(channel_id))


def validate_sync_plan(plan: AudioPlan, sync: SyncPlan) -> list[dict[str, Any]]:
    """同步计划校验 (稳定 reason code 列表, 空 = 通过)。

    检查顺序刻意固定, 便于调用方稳定映射到 UI/日志:

      1. reference 显式存在                  -> reference_missing
      2. targets 无重复                      -> sync_duplicate_target
      3. reference 不在 targets 里           -> reference_equals_target
      4. reference 能解析到 AudioChannel     -> sync_channel_not_found
      5. reference 在有效映射里              -> reference_not_selected
      6. 每个 target 能解析到 AudioChannel   -> sync_channel_not_found
      7. 每个 target 在有效映射里            -> target_not_selected
      8. reference + targets 样本率一致      -> sync_sample_rate_mismatch
      9. 参与声道都是可估计的 PCM 形态       -> sync_unsupported_format
    """
    issues: list[dict[str, Any]] = []
    ref_id = str(sync.reference_channel_id or "")

    # 1. reference 必须显式存在
    if not ref_id:
        issues.append({
            "reason": REASON_REFERENCE_MISSING,
            "detail": (
                "sync plan has no reference channel; the reference must be "
                "an explicit AudioChannel id (never a source/stream/path)"
            ),
        })
        return issues                     # 后续检查全部依赖 reference

    targets = [str(t) for t in sync.target_channel_ids]

    # 2. duplicate target
    seen: set[str] = set()
    dups: list[str] = []
    for tid in targets:
        if tid in seen and tid not in dups:
            dups.append(tid)
        seen.add(tid)
    if dups:
        issues.append({
            "reason": REASON_SYNC_DUPLICATE_TARGET,
            "detail": f"duplicate target channel(s): {dups}",
            "location": dups[0],
        })

    # 3. reference 不得同时是 target
    if ref_id in set(targets):
        issues.append({
            "reason": REASON_REFERENCE_EQUALS_TARGET,
            "detail": (
                f"reference {ref_id!r} also appears in the target set; "
                "the reference is never corrected against itself"
            ),
            "location": ref_id,
        })

    # 4. reference 必须是已知声道
    ref_channel = _channel_or_none(plan, ref_id)
    if ref_channel is None:
        issues.append({
            "reason": REASON_SYNC_CHANNEL_NOT_FOUND,
            "detail": f"reference channel {ref_id!r} is not in the audio plan",
            "location": ref_id,
        })

    # 5. reference 必须被 selection 保留
    selected = set(_effective_channel_ids(plan))
    if ref_channel is not None and ref_id not in selected:
        issues.append({
            "reason": REASON_REFERENCE_NOT_SELECTED,
            "detail": (
                f"reference channel {ref_id!r} exists but is not part of the "
                "plan's effective mapping (it was excluded by selection); "
                "it is not re-discovered from its source"
            ),
            "location": ref_id,
        })

    # 6/7. target 必须存在且被选中
    missing_targets: list[str] = []
    unselected_targets: list[str] = []
    for tid in targets:
        channel = _channel_or_none(plan, tid)
        if channel is None:
            missing_targets.append(tid)
        elif tid not in selected:
            unselected_targets.append(tid)
    if missing_targets:
        issues.append({
            "reason": REASON_SYNC_CHANNEL_NOT_FOUND,
            "detail": f"target channel(s) not in the audio plan: {missing_targets}",
            "location": missing_targets[0],
        })
    if unselected_targets:
        issues.append({
            "reason": REASON_TARGET_NOT_SELECTED,
            "detail": (
                "target channel(s) exist but were excluded by selection: "
                f"{unselected_targets}"
            ),
            "location": unselected_targets[0],
        })

    # 8/9. 采样率与格式 (只在声道都能解析时判定)
    resolvable = [ref_channel] if ref_channel is not None else []
    resolvable += [
        _channel_or_none(plan, tid) for tid in targets
    ]
    resolvable = [c for c in resolvable if c is not None]
    rates: dict[str, int] = {}
    for channel in resolvable:
        rate = int(channel.sample_rate or 0)
        if rate > 0:
            rates[channel.id] = rate
    # 同时看 AudioStream 上的采样率 (channel 的 sample_rate 来自 stream)
    for channel in resolvable:
        if channel.id in rates:
            continue
        source = plan.source(channel.source_id)
        stream = source.stream(channel.stream_index) if source else None
        if stream is not None and int(stream.sample_rate or 0) > 0:
            rates[channel.id] = int(stream.sample_rate)
    distinct = sorted(set(rates.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{cid}={rates[cid]}" for cid in sorted(rates))
        issues.append({
            "reason": REASON_SYNC_SAMPLE_RATE_MISMATCH,
            "detail": (
                "reference and all targets must share one sample rate "
                "(no resampling in this phase): " + detail
            ),
            "location": ref_id,
        })
    unknown = [c.id for c in resolvable if c.id not in rates]
    if unknown:
        issues.append({
            "reason": REASON_SYNC_UNSUPPORTED_FORMAT,
            "detail": (
                f"unknown sample rate for {unknown} — cannot estimate delay "
                "without a known rate"
            ),
            "location": unknown[0],
        })

    return issues


def require_sync_plan(plan: AudioPlan, sync: SyncPlan) -> None:
    """校验失败即抛 `AudioSyncError` (第一个 issue)。"""
    issues = validate_sync_plan(plan, sync)
    if issues:
        first = issues[0]
        raise AudioSyncError(
            str(first.get("reason") or REASON_SYNC_ESTIMATION_FAILED),
            str(first.get("detail") or "sync plan validation failed"),
            location=first.get("location"),
        )


def reference_channel(plan: AudioPlan, sync: SyncPlan) -> AudioChannel:
    """解析 reference -> AudioChannel (失败抛稳定 reason code)。"""
    require_sync_plan(plan, sync)
    channel = _channel_or_none(plan, sync.reference_channel_id)
    if channel is None:                    # pragma: no cover - require 已拦
        raise AudioSyncError(
            REASON_SYNC_CHANNEL_NOT_FOUND,
            f"reference channel {sync.reference_channel_id!r} not found",
            location=sync.reference_channel_id,
        )
    return channel


# ---------------------------------------------------------------------------
# 估计结果
# ---------------------------------------------------------------------------


@dataclass
class PairSyncEstimate:
    """一路 target 相对 reference 的估计结果 (全部为可报告事实)。

    `offset_samples` 的方向 = `core.channel_sync` 的 `delay_samples`
    (= `core.sync_fix` 的 `shift_samples`): **> 0 表示 target 比 reference 晚到**,
    timeline 应用后内容**前移**该样本数。
    """

    channel_id: str
    offset_samples: int = 0
    delay_samples: float = 0.0
    delay_ms: float = 0.0
    confidence: float = 0.0
    usable_frames: int = 0
    total_frames: int = 0
    fine_delay_samples: float | None = None
    polarity: int = 1
    drift_ppm: float = 0.0
    constant: bool = True
    applied: bool = False
    status: str = SyncStatus.NOT_PROCESSED.value
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def residual_samples(self) -> float:
        """浮点延迟的分数部分 (报告用; **不修正**, 无插值)。"""
        return float(self.delay_samples) - float(self.offset_samples)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "channel_id": self.channel_id,
            "offset_samples": int(self.offset_samples),
            "delay_samples": round(float(self.delay_samples), 6),
            "delay_ms": round(float(self.delay_ms), 6),
            "confidence": round(float(self.confidence), 6),
            "usable_frames": int(self.usable_frames),
            "total_frames": int(self.total_frames),
            "polarity": int(self.polarity),
            "drift_ppm": round(float(self.drift_ppm), 6),
            "constant": bool(self.constant),
            "applied": bool(self.applied),
            "status": self.status,
        }
        if self.fine_delay_samples is not None:
            data["fine_delay_samples"] = round(
                float(self.fine_delay_samples), 6
            )
        if self.residual_samples:
            data["residual_samples"] = round(self.residual_samples, 6)
        if self.reason is not None:
            data["reason"] = self.reason
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


@dataclass
class SyncEstimateResult:
    """一次同步任务的完整结果 (JSON-compatible, 不含 PCM)。

    语义要点:

      * `estimates` 只包含 **targets**; reference 自身不出现在这里
        (它的 offset 恒为 0, 也不允许被二次修正);
      * `reference_channel_id` 是本次任务的坐标系原点 —— 所有
        `offsets()` 都是相对它的值, 换 reference 即整体平移 (见
        `docs/release_notes_next.md` §6);
      * `algo_version` 记录产出这些 offset 的估计算法版本 (既有
        `core/sync_estimate` 的标定版本), 便于事后追溯。
    """

    sync_plan: SyncPlan
    sample_rate: int
    reference_channel_id: str
    estimates: list[PairSyncEstimate] = field(default_factory=list)
    method: str = "gcc_phat_pairwise"
    algo_version: str = ""
    applied: bool = False
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- 查询 -------------------------------------------------------------

    @property
    def ok(self) -> bool:
        return not self.errors

    def estimate(self, channel_id: str) -> PairSyncEstimate | None:
        want = str(channel_id)
        return next((e for e in self.estimates if e.channel_id == want), None)

    def offset_of(self, channel_id: str) -> int | None:
        """该声道在**统一 timeline 坐标系**里的 offset。

        reference -> 0 (恒, 且不可被修正); targets -> 估计值; 其它 -> None。
        """
        if str(channel_id) == self.reference_channel_id:
            return 0
        est = self.estimate(channel_id)
        return None if est is None else int(est.offset_samples)

    def offsets(self) -> dict[str, int]:
        """全部参与声道的 offset (reference 记 0)。"""
        out: dict[str, int] = {self.reference_channel_id: 0}
        for est in self.estimates:
            out[est.channel_id] = int(est.offset_samples)
        return out

    def channel_ids(self) -> list[str]:
        """参与本次同步的声道 (reference 在前, 其余按 target 顺序)。"""
        return [self.reference_channel_id] + [
            e.channel_id for e in self.estimates
        ]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reference_channel_id": self.reference_channel_id,
            "sample_rate": int(self.sample_rate),
            "method": self.method,
            "algo_version": self.algo_version,
            "applied": bool(self.applied),
            "estimates": [e.to_dict() for e in self.estimates],
            "offsets": self.offsets(),
        }
        if self.sync_plan.notes:
            data["notes"] = list(self.sync_plan.notes)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "reference": self.reference_channel_id,
            "targets": len(self.estimates),
            "applied": bool(self.applied),
            "offsets": self.offsets(),
            "errors": [e.get("reason") for e in self.errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 估计
# ---------------------------------------------------------------------------

#: 估计参数默认值 —— 与 `core.channel_sync.DEFAULTS` 同一套标定值, **不另立一套**。
_ESTIMATE_DEFAULTS: dict[str, Any] = {
    "search_window_ms": 80.0,
    "frame_ms": 200.0,
    "hop_ms": 100.0,
    "anchor_segment_seconds": 30.0,
    "min_confidence": 0.3,
    "coarse_rate": 8000,
    "fine_phase_band_hz": (200.0, 8000.0),
    "min_usable_frames": 5,
    "frame_min_rms_dbfs": -50.0,
}


def _sync_estimate_module() -> Any:
    """延迟导入 `core.sync_estimate` (numpy/scipy 为可选依赖)。"""
    try:
        return importlib.import_module(f"{__package__}.sync_estimate")
    except ImportError as exc:                 # pragma: no cover - 环境相关
        raise AudioSyncError(
            REASON_SYNC_ESTIMATION_FAILED,
            f"core.sync_estimate is unavailable ({exc}); numpy/scipy missing",
        ) from exc


def estimate_sync(
    plan: AudioPlan,
    sync: SyncPlan,
    *,
    reader: Any = None,
    ffmpeg: Path | None = None,
    work_dir: Path | None = None,
    chunk_frames: int = 0,
    parameters: Mapping[str, Any] | None = None,
) -> SyncEstimateResult:
    """reference + targets -> 逐 target constant offset (**不修改任何模型**)。

    流程:

      1. `require_sync_plan()` —— 全部结构/选择/采样率约束先过;
      2. 打开 PCM (显式 reader 优先, 否则由 ffmpeg+work_dir 建 `AudioPCMReader`);
      3. 对每个 target 调**既有** `sync_estimate.estimate_pair(ref, tgt)`,
         `ref_index` / `tgt_index` 用音频序号, 参考与目标方向不做任何翻转;
      4. 质量门 (与文件级管线同一套阈值): `usable_frames` / `confidence`
         / `constant`(`drift_ppm` 只记录不修正);
      5. reference 自身的 offset 恒为 0 — 它不参与任何修正。

    本函数**只产出估计**, 不写回 `AudioChannel.sync` (那一步是
    `apply_sync_result()`, 由调用方显式决定)。render alignment 永远只由
    `AudioTimeline` 决定, 因此不存在第二个 offset 真相源。
    """
    require_sync_plan(plan, sync)
    est = _sync_estimate_module()

    params = dict(_ESTIMATE_DEFAULTS)
    params.update(dict(parameters or {}))
    params.update({
        k: v for k, v in sync.parameters.items()
        if k in _ESTIMATE_DEFAULTS
    })
    band = params.get("fine_phase_band_hz") or (200.0, 8000.0)
    params["fine_phase_band_hz"] = tuple(float(v) for v in band)

    ref = reference_channel(plan, sync)
    sample_rate = int(ref.sample_rate or 0)
    if sample_rate <= 0:
        source = plan.source(ref.source_id)
        stream = source.stream(ref.stream_index) if source else None
        sample_rate = int(getattr(stream, "sample_rate", 0) or 0)

    result = SyncEstimateResult(
        sync_plan=sync,
        sample_rate=sample_rate,
        reference_channel_id=ref.id,
        method=sync.method,
        algo_version="2.3.0-p1",
    )

    reader_obj, owns = _ReaderSeam.open_reader(
        plan,
        ffmpeg=ffmpeg,
        work_dir=work_dir,
        chunk_frames=chunk_frames,
        reader=reader,
    )
    try:
        prepare = getattr(reader_obj, "prepare", None)
        if callable(prepare):
            try:
                prepare()
            except AudioRenderError as exc:
                raise AudioSyncError(
                    exc.reason, exc.detail, location=exc.location
                ) from exc

        ref_view = _view_of(reader_obj, ref)
        for tid in sync.target_channel_ids:
            channel = _channel_or_none(plan, tid)
            if channel is None:                # pragma: no cover - require 已拦
                continue
            result.estimates.append(
                _estimate_one(
                    est, reader_obj, ref, channel, ref_view,
                    sample_rate=sample_rate, params=params,
                )
            )
    finally:
        if owns:
            close = getattr(reader_obj, "close", None)
            if callable(close):
                close()

    result.warnings.extend(
        f"{e.channel_id}: {w}"
        for e in result.estimates for w in e.warnings
    )
    return result


def _view_of(reader: Any, channel: AudioChannel) -> Any:
    stream_id = f"{channel.source_id}:s{channel.stream_index}"
    return PCMChannelWindow(
        reader,
        stream_id,
        int(channel.channel_index),
        _stream_sample_count(reader, stream_id),
    )


def _estimate_one(
    est: Any,
    reader: Any,
    ref: AudioChannel,
    target: AudioChannel,
    ref_view: Any,
    *,
    sample_rate: int,
    params: Mapping[str, Any],
) -> PairSyncEstimate:
    """单 target: 既有 estimate_pair + 与文件级管线同一套质量门。"""
    out = PairSyncEstimate(channel_id=target.id)
    tgt_view = _view_of(reader, target)
    ref_pos = _audio_position(ref)
    tgt_pos = _audio_position(target)

    try:
        pair = est.estimate_pair(
            ref_view,
            tgt_view,
            sample_rate=sample_rate,
            search_window_ms=float(params["search_window_ms"]),
            frame_ms=float(params["frame_ms"]),
            hop_ms=float(params["hop_ms"]),
            anchor_segment_seconds=float(params["anchor_segment_seconds"]),
            min_confidence=float(params["min_confidence"]),
            coarse_rate=int(params["coarse_rate"]),
            fine_phase_band_hz=tuple(params["fine_phase_band_hz"]),
            min_usable_frames=int(params["min_usable_frames"]),
            frame_min_rms_dbfs=float(params["frame_min_rms_dbfs"]),
            ref_index=ref_pos,
            tgt_index=tgt_pos,
        )
    except Exception as exc:                   # noqa: BLE001 - 统一成 reason code
        out.status = SyncStatus.FAILED.value
        out.reason = REASON_SYNC_ESTIMATION_FAILED
        out.warnings.append(f"estimate_pair raised {type(exc).__name__}: {exc}")
        return out

    out.total_frames = len(pair.frames)
    out.usable_frames = int(pair.usable_frames)
    out.confidence = float(pair.confidence)
    out.delay_samples = float(pair.delay_samples)
    out.delay_ms = float(pair.delay_ms)
    out.polarity = int(pair.polarity or 1)
    fine = float(getattr(pair, "fine_delay_samples", float("nan")))
    out.fine_delay_samples = None if fine != fine else fine

    stats = est.summarize_trajectory(pair.frames, sample_rate)
    out.drift_ppm = float(getattr(stats, "drift_ppm", 0.0) or 0.0)

    # 与文件级管线一致的质量门 (阈值同 core/channel_sync.DEFAULTS)
    min_frames = int(params["min_usable_frames"])
    min_conf = float(params["min_confidence"])
    if out.usable_frames < min_frames:
        out.status = SyncStatus.FAILED.value
        out.reason = REASON_SYNC_INSUFFICIENT_SIGNAL
        out.warnings.append(
            f"only {out.usable_frames} usable frame(s) "
            f"(< {min_frames}) — insufficient correlated signal"
        )
        return out
    if out.confidence < min_conf:
        out.status = SyncStatus.FAILED.value
        out.reason = REASON_SYNC_INSUFFICIENT_SIGNAL
        out.warnings.append(
            f"confidence {out.confidence:.3f} < {min_conf}"
        )
        return out
    if out.delay_samples != out.delay_samples:
        out.status = SyncStatus.FAILED.value
        out.reason = REASON_SYNC_ESTIMATION_FAILED
        out.warnings.append("delay is NaN (non-finite measurement)")
        return out

    # 恒定偏移: 取整。
    # 取整语义必须与 `core/sync_fix.shift_stream` 的 `int(np.rint(delay))`
    # **完全一致** —— 两者都是 IEEE-754 round-half-to-even (banker's
    # rounding), 因此 Python 内建 `round()` 与 `np.rint()` 在同一输入上
    # 结果相同 (已验证 0.5/1.5/-0.5 三个边界)。固定偏差点绝不引入
    # 第二套取整规则。
    # 漂移只记录不修正 —— 本阶段不做 drift correction。
    out.offset_samples = int(round(out.delay_samples))
    out.status = SyncStatus.SUCCESS.value
    out.warnings.append(
        "constant_offset_only: fractional part kept as residual, "
        "no interpolation; drift is reported, not corrected"
    )
    if out.drift_ppm:
        out.warnings.append(f"drift_ppm={out.drift_ppm:.3f} (reported only)")
    return out


def _audio_position(channel: AudioChannel) -> int:
    """声道的音频序号 (`estimate_pair` 的 ref_index/tgt_index 用于报告)。"""
    pos = getattr(channel, "audio_position", None)
    if pos is None:
        return int(channel.channel_index)
    return int(pos)


# ---------------------------------------------------------------------------
# 应用到模型 (timeline 是唯一的 alignment 权威)
# ---------------------------------------------------------------------------


def apply_sync_result(
    plan: AudioPlan,
    result: SyncEstimateResult,
    *,
    application: SyncApplication | str = SyncApplication.APPLY,
) -> int:
    """把估计结果写入 `AudioChannel.sync` —— render alignment 的唯一入口。

    为什么写 `AudioChannel.sync` 而不是直接改 PCM / 直接改 timeline:
    这是**既有**的唯一消费路径 —— `core.audio_timeline.resolve_timeline()`
    从 `AudioChannel.sync` 取 `offset_samples` 并执行

        timeline_sample = source_sample - offset_samples

    因此写入这里等于"把 estimate 交给统一 timeline", 不存在第二个
    offset 真相源。同步层只决定"target 在 timeline 上的位置", 真正取样
    由 PCM 层 (`AudioRouter` / `AudioMixer`) 负责。

    返回实际写入的 target 数 (reference 不写入, 其偏移恒为 0)。

    已 `applied` 的结果重复调用是幂等的 (同一 offset 再写一次)。
    """
    mode = (
        application.value
        if isinstance(application, SyncApplication)
        else str(application or SyncApplication.APPLY.value)
    )
    written = 0
    for item in result.estimates:
        if item.status != SyncStatus.SUCCESS.value:
            # 估计失败的 target 一律保持"未处理": 绝不写入半可信偏移。
            continue
        channel = _channel_or_none(plan, item.channel_id)
        if channel is None:                    # pragma: no cover - 已校验
            continue
        channel.sync = AudioSyncResult(
            status=SyncStatus.SUCCESS,
            offset_samples=float(item.offset_samples),
            offset_ms=float(item.offset_samples) * 1000.0 / float(
                result.sample_rate or 1
            ),
            quality=float(item.confidence),
            anchor=result.reference_channel_id,
            reason="arbitrary_reference_estimate",
            drift_ppm=float(item.drift_ppm) if item.drift_ppm else None,
            constant=bool(item.constant),
            polarity=int(item.polarity),
            usable_frames=int(item.usable_frames),
            algo_version=result.algo_version or None,
            source="audio_sync",
            warnings=list(item.warnings),
        )
        item.applied = (mode == SyncApplication.APPLY.value)
        written += 1
    result.applied = written > 0
    return written


def clear_sync_result(plan: AudioPlan, result: SyncEstimateResult) -> int:
    """撤销本次同步写入的 sync (恢复 NOT_PROCESSED)。返回清除数。

    只清本次参与的 target; reference 本就没有被写过 offset。
    """
    cleared = 0
    for item in result.estimates:
        channel = _channel_or_none(plan, item.channel_id)
        if channel is None:
            continue
        if channel.sync.source == "audio_sync":
            channel.sync = AudioSyncResult()
            item.applied = False
            cleared += 1
    result.applied = False
    return cleared
