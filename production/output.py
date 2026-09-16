"""Production Output Integration (Phase 4C / v0.8.0) — 把音频输出接进生产管线。

本模块是**跨域编排**: 它把音频域与视频域的产物合成一次生产输出, 但自己不
实现任何音频算法, 也不碰任何视频编码参数。

    AudioPlan ──┬── core.audio_format     (格式感知的 alignment / 输出编码策略)
                ├── core.audio_sync        (arbitrary-reference 对齐, 既有)
                ├── resolve_audio_execution_path()   (执行图唯一权威)
                ├── core.audio_output_structure      (输出流结构 / 成组)
                ├── encode_audio_from_plan()         (渲染 + 编码, 既有)
                │
    VideoOutputArtifact (已经落盘) ──────────────┘
                                                ↓
                                        OutputComposer
                                                ↓
                                        final container

v0.8.0 的三处新增 (全部落在**音频域**内, 视频域一行未改)
-------------------------------------------------------

1. **格式感知**: 输入是 PCM 还是 compressed 由 `core.audio_format` 判定;
   PCM 默认允许 alignment, compressed 默认原样保留, 显式要求时才
   decode -> PCM -> align -> re-encode (并给 warning);
2. **编码继承**: 输出 codec/bitrate 默认继承来源 (`manual > source > encoder
   default`), PCM 输入默认 PCM 输出, PCM/FLAC 不允许 bitrate;
3. **输出结构**: 有效映射 -> 若干条输出流 (`core.audio_output_structure`);
   整条完整源流仍是 `-map` + copy, 其余走 PCM 渲染 + 编码; 两者可以在同一
   次输出里混用。

`NONE` 走"什么都不做"而不是"重新拼一遍默认 argv" —— 生产默认路径因此
**结构上**不可能被本模块改变。

职责边界 (硬要求)
------------------

* **不重写执行图判定**: 只调用 `resolve_audio_execution_path()`;
* **不重写保留逻辑**: 只调用 `build_audio_retention()`;
* **不重写渲染/混音**: 只调用 `encode_audio_from_plan()` (它内部复用
  `run_audio_render()`);
* **不重写格式判定**: 只调用 `core.audio_format`;
* **不重写容器编排**: 只调用 `OutputComposer`;
* **不认识** `AudioMixer` / `AudioPCMReader` / `ChannelTimeline` /
  `GainMatrix` 等内部结构; 只消费产物契约;
* **不做** drift 检测/修正, **不做** resampling, **不做** loudness;
* **不做** 视频编码决策 (像素格式 / 帧率 / 码率一律不出现);
* 错误**不吞**: 各层稳定 reason code 原样上传。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from core.audio_encode import (
    AudioFormatSpec,
    EncodedAudioOutput,
    encode_audio_from_plan,
    shared_render_window,
)
from core.audio_execution import (
    AudioExecutionPath,
    AudioExecutionPlan,
    resolve_audio_execution_path,
)
from core.audio_format import (
    AlignmentDecision,
    ExplicitFormat,
    OutputFormatDecision,
    resolve_alignment,
    resolve_output_format,
)
from core.audio_models import AudioPlan
from core.audio_external import (
    ExternalAudioResult,
    append_external_sources,
    build_external_sources,
)
from core.audio_output_structure import (
    AudioMappingPolicy,
    OutputStructure,
    build_output_structure,
    group_plan,
)
from core.audio_retention import (
    AudioRetentionSpec,
    build_audio_retention,
)
from core.output_compose import (
    AudioInputRef,
    OutputComposer,
    VideoOutputArtifact,
)

__all__ = [
    "REASON_PRODUCTION_ALIGNMENT_FAILED",
    "REASON_PRODUCTION_AUDIO_PLAN_INVALID",
    "REASON_PRODUCTION_AUDIO_RETAIN_INVALID",
    "REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH",
    "REASON_PRODUCTION_GROUP_INPUT_UNKNOWN",
    "REASON_PRODUCTION_VERIFY_FAILED",
    "ProductionAudioOutcome",
    "ExternalAudioResult",
    "append_external_audio",
    "apply_alignment",
    "derive_video_only_command",
    "load_audio_plan_request",
    "produce_audio_output",
    "resolve_source_audio_plan",
]

REASON_PRODUCTION_AUDIO_PLAN_INVALID = "production_audio_plan_invalid"
REASON_PRODUCTION_AUDIO_RETAIN_INVALID = "production_audio_retain_invalid"
REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH = (
    "production_audio_selector_mismatch"
)
REASON_PRODUCTION_VERIFY_FAILED = "production_verify_failed"
REASON_PRODUCTION_ALIGNMENT_FAILED = "production_alignment_failed"
REASON_PRODUCTION_GROUP_INPUT_UNKNOWN = "production_group_input_unknown"


@dataclass
class ProductionAudioOutcome:
    """生产音频输出的结果 (JSON-compatible)。

    `applied=False` 只有一个合法含义: **执行图为 `NONE`** —— 没有音频计划,
    调用方应保持既有路径原样不动。任何其它情况都必须 `ok=True` 或带
    `errors`。
    """

    ok: bool
    applied: bool = False
    path: AudioExecutionPath = AudioExecutionPath.NONE
    output_path: str = ""
    execution: AudioExecutionPlan | None = None
    retention: AudioRetentionSpec | None = None
    encoded: list[EncodedAudioOutput] = field(default_factory=list)
    composition: Any = None
    #: v0.8.0: 格式感知的策略结论 (alignment / 输出编码 / 输出流结构)
    alignment: AlignmentDecision | None = None
    #: alignment 的**实测**结果: 实际写入了几条 offset, 每条是多少样本。
    alignment_applied: int = 0
    alignment_offsets: dict[str, float] = field(default_factory=dict)
    alignment_statuses: dict[str, str] = field(default_factory=dict)
    format: OutputFormatDecision | None = None
    structure: OutputStructure | None = None
    #: 逐组执行记录 (顺序 = 容器里的音轨顺序)
    # 逐组 * 来源不同 -> 逐组 * 输出 codec 不同 (§35 是**按来源**说的:
    # PCM source -> PCM / AAC -> AAC / Opus -> Opus)。因此这里记录每条
    # 输出流真正的编码决议, 而不是把第一条流的结论套给所有流。
    groups: list[dict[str, Any]] = field(default_factory=list)
    formats: list[OutputFormatDecision] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 交给 Composer 的音频输入契约 (顺序 = 输出音轨顺序); 内部字段, 不进报告。
    _compose_refs: list[Any] = field(default_factory=list, repr=False)
    #: Composer 的"额外只用于取音频"的输入文件 (整流保留用), 序号在已编码
    #: 音轨之后 —— 选择器的前缀按它算, 因此这里必须与 compose() 的分配一致。
    _extra_inputs: list[str] = field(default_factory=list, repr=False)

    @property
    def reasons(self) -> list[str]:
        return [str(e.get("reason") or "") for e in self.errors]

    @property
    def audio_stream_count(self) -> int:
        if self.composition is not None:
            return int(self.composition.audio_stream_count)
        return len(self.groups)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": bool(self.ok),
            "applied": bool(self.applied),
            "path": self.path.value,
            "output_path": self.output_path,
        }
        if self.execution is not None:
            data["execution"] = self.execution.to_dict()
        if self.alignment is not None:
            data["alignment"] = self.alignment.to_dict()
            data["alignment"]["applied"] = int(self.alignment_applied)
            if self.alignment_offsets:
                data["alignment"]["offsets"] = {
                    k: float(v) for k, v in self.alignment_offsets.items()
                }
            if self.alignment_statuses:
                data["alignment"]["statuses"] = dict(self.alignment_statuses)
        if self.format is not None:
            data["format"] = self.format.to_dict()
        if self.structure is not None:
            data["structure"] = self.structure.to_dict()
        if self.retention is not None:
            data["retention"] = self.retention.to_dict()
        if self.encoded:
            data["encoded"] = [e.to_dict() for e in self.encoded]
        if self.formats:
            data["formats"] = [f.to_dict() for f in self.formats]
        if self.groups:
            data["groups"] = [dict(g) for g in self.groups]
        if self.composition is not None:
            data["composition"] = self.composition.to_dict()
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "applied": bool(self.applied),
            "path": self.path.value,
            "audio_streams": int(self.audio_stream_count),
            "alignment": (
                self.alignment.reason if self.alignment is not None else None
            ),
            "codec": (
                self.format.codec if self.format is not None else None
            ),
            "encoded_codecs": [f.codec for f in self.formats],
            "alignment_applied": int(self.alignment_applied),
            "mapping": (
                self.structure.policy.summary()
                if self.structure is not None else None
            ),
            "reasons": self.reasons,
        }


# ---------------------------------------------------------------------------
# 计划构建: 源文件 + 外挂音频 -> AudioPlan
# ---------------------------------------------------------------------------


def load_audio_plan_request(path: Path | str) -> Any:
    """读取 `--audio-plan` JSON 文件 -> 请求对象 (生产入口的唯一入口)。

    生产入口因此不必 import `core.audio_request`; 解析失败抛
    `AudioRequestError` (稳定 reason code)。
    """
    from core.audio_request import load_audio_request

    return load_audio_request(path)


def append_external_audio(
    plan: AudioPlan,
    ffprobe: Path,
    source: Path | str,
    request: Any = None,
    *,
    enabled: bool | None = None,
) -> ExternalAudioResult:
    """发现视频同目录的外挂音频并**追加**进既有计划 (§16–§22/§29)。

    返回的 `ExternalAudioResult` 同时携带完整发现事实 (扫了什么、选了什么、
    为什么) —— 生产入口只要把这些字段写进日志即可, 不需要自己实现规则。
    `plan` 被**原地**扩展 (追加语义, §29), 因为这就是"原视频音频 + 外挂
    音频"这一个计划。
    """
    want = bool(getattr(request, "external", False)) if enabled is None \
        else bool(enabled)
    result = build_external_sources(
        source, ffprobe=ffprobe, enabled=want,
    )
    if result.ok and result.sources:
        append_external_sources(plan, result.sources)
    return result


def resolve_source_audio_plan(
    ffprobe: Path,
    source: Path | str,
    request: Any,
    *,
    source_id: str = "source",
    external: bool = True,
) -> AudioPlan:
    """源文件音频事实 -> `AudioPlan`, 并把请求应用上去。

    这是生产入口**唯一**需要调用的"音频计划"函数: `1kt.py` 因此完全不必
    import 任何 `core.audio_*` 模块 —— 音频域的内部结构被收在本层之后
    (架构断言钉住这一点)。请求解析失败时抛
    `core.audio_request.AudioRequestError` (稳定 reason code)。

    顺序 (v0.8.0): 探测 -> **追加外挂来源** -> 应用选择/排序。外挂必须
    先于选择进入计划, 否则计划文件里写的 `"clip001.wav:s1:c0"` 会因为
    "声道还不存在"而失败 —— 那不是用户的错。
    """
    from core.audio_probe import audio_probe_from_file

    probe = audio_probe_from_file(ffprobe, Path(source), source_id=source_id)
    candidate = probe.plan()
    if request is None:
        return candidate
    request = _coerce_request(request)
    if external and getattr(request, "external", False):
        result = append_external_audio(
            candidate, ffprobe, source, request
        )
        if not result.ok:
            first = result.errors[0] if result.errors else {}
            raise ValueError(
                f"{first.get('reason') or 'external_audio_failed'}: "
                f"{first.get('detail') or 'external audio discovery failed'}"
            )
    return _build_requested_plan(request, candidate)


def _coerce_request(request: Any) -> Any:
    """允许直接传 dict / JSON 路径 (调用方不必 import 请求类型)。"""
    from core.audio_request import AudioRequest, load_audio_request, \
        parse_audio_request

    if isinstance(request, AudioRequest):
        return request
    if isinstance(request, Mapping):
        return parse_audio_request(request)
    if isinstance(request, str):
        return load_audio_request(request)
    raise ValueError(
        REASON_PRODUCTION_AUDIO_PLAN_INVALID + ": unsupported audio "
        f"request type {type(request).__name__}"
    )


def _build_requested_plan(request: Any, plan: AudioPlan) -> AudioPlan:
    from core.audio_request import build_audio_plan

    return build_audio_plan(request, plan)


# ---------------------------------------------------------------------------
# alignment (§4/§5/§39/§40): 只会通过既有 core.audio_sync 执行
# ---------------------------------------------------------------------------


@dataclass
class AlignmentOutcome:
    """一次 alignment 的执行结果 (纯事实, JSON-compatible)。"""

    decision: AlignmentDecision
    applied: int = 0
    offsets: dict[str, float] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "decision": self.decision.to_dict(),
            "applied": int(self.applied),
            "offsets": {k: float(v) for k, v in self.offsets.items()},
            "statuses": dict(self.statuses),
        }
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.notes:
            data["notes"] = list(self.notes)
        return data


def apply_alignment(
    plan: AudioPlan,
    reference: str,
    *,
    ffmpeg: Path,
    work_dir: Path | None = None,
    requested: Any = None,
    log: Callable[[str], None] | None = None,
) -> AlignmentOutcome:
    """格式感知的 alignment (**唯一**执行入口)。

    流程与 §42 完全一致 —— 每一步都用既有实现:

        resolve_alignment()            格式分类 -> 允许/不允许 + warning
        plan_from_plan()               显式 reference -> SyncPlan
        estimate_sync()                既有 arbitrary-reference 估计
        apply_sync_result()            写 AudioChannel.sync (唯一 offset 入口)
        resolve_timeline()             (由 render 阶段读取, 本层不碰)

    ⚠️ **不猜 reference**: `reference` 必须显式给出 (§40)。compressed 参与
    时只做两件额外的事: 记录 warning, 并照常走 PCM 图 (解码 -> 对齐 ->
    重编码) —— 绝不用"改压缩包时间戳"冒充样本级对齐。
    """
    from core.audio_sync import (
        apply_sync_result,
        estimate_sync,
        plan_from_plan,
    )

    log = log or (lambda _msg: None)
    decision = resolve_alignment(plan, requested)
    out = AlignmentOutcome(decision=decision)

    if not decision.enabled:
        out.notes.append(
            f"alignment not enabled ({decision.reason}): no offset is "
            "estimated and no stream is decoded for alignment"
        )
        return out
    if not str(reference or "").strip():
        out.notes.append(
            "alignment is enabled but no reference channel was given; "
            "reference is never guessed, so no offset is applied"
        )
        return out

    sync = plan_from_plan(plan, str(reference))
    try:
        estimation = estimate_sync(
            plan, sync, ffmpeg=ffmpeg, work_dir=work_dir,
        )
    except Exception as exc:                       # noqa: BLE001
        out.errors.append({
            "reason": REASON_PRODUCTION_ALIGNMENT_FAILED,
            "detail": (
                f"alignment against reference {reference!r} failed: "
                f"{type(exc).__name__}: {exc}"
            ),
            "location": str(reference),
        })
        return out

    out.warnings.extend(estimation.warnings)
    written = apply_sync_result(plan, estimation)
    out.applied = int(written)
    for item in estimation.estimates:
        out.statuses[item.channel_id] = item.status
        if item.status == "success":
            out.offsets[item.channel_id] = float(item.offset_samples)
    if not written:
        out.warnings.append(
            "no target produced a usable estimate: every channel keeps its "
            "original position (a non-success estimate is never applied)"
        )
    log(
        f"alignment: reference={reference} applied={written} "
        f"offsets={out.offsets}"
    )
    return out


# ---------------------------------------------------------------------------
# 生产编排
# ---------------------------------------------------------------------------


def produce_audio_output(
    *,
    plan: AudioPlan | None,
    video: VideoOutputArtifact,
    output_path: Path | str,
    ffmpeg: Path,
    ffprobe: Path | None = None,
    work_dir: Path | None = None,
    audio_source: Path | str | None = None,
    audio_format: AudioFormatSpec | str | None = None,
    request: Any = None,
    mix_bus: Any = None,
    verify_decode: bool = True,
    timeout: int = 1800,
    log: Callable[[str], None] | None = None,
) -> ProductionAudioOutcome:
    """把音频输出接进一次生产输出 (`video` 是**已经落盘**的视频产物)。

    `plan is None` -> 返回 `applied=False` 且**不触碰任何文件**: 调用方保持
    既有 `-map 0` + `-c:a copy` 路径。默认行为不变是靠"什么都不做"保证的。

    `request` 是 `--audio-plan` 的解析结果 (可选)。它携带**用户意图**:
    alignment / reference / 输出结构 / 编码覆盖。所有策略解析都只读它,
    策略实现都在音频域内 (`core.audio_format` / `core.audio_output_
    structure`), 本层只负责按顺序调用。

    `audio_format` 是显式的**手写覆盖** (等价于请求里显式写了 `encode`),
    保留它是为了不打断既有调用点; 两者都给时以 `audio_format` 为准。

    输出会按 `core.audio_output_structure` 切成若干条音频流: 整条完整源流
    用 `-map` + copy (不重新编码), 其余每条渲染 + 编码一次。两者可以在同
    一次输出里混用, 顺序 = 结构里给出的顺序 = 容器里的顺序。
    """
    log = log or (lambda _msg: None)
    outcome = ProductionAudioOutcome(ok=False)

    if plan is None:
        outcome.ok = True
        outcome.applied = False
        outcome.path = AudioExecutionPath.NONE
        outcome.notes.append(
            "no audio plan: production default path untouched"
        )
        return outcome

    # ---- 0. 用户意图 (只读, 不做任何策略解释) ---------------------------
    spec, explicit = _requested_format(request, audio_format)
    alignment_policy = (
        getattr(request, "alignment", None) if request is not None else None
    )
    reference = (
        str(getattr(request, "sync_reference", "") or "")
        if request is not None else ""
    )
    manual_mapping = (
        getattr(request, "mapping", None) if request is not None else None
    )

    # ---- 1. alignment (格式感知; reference 必须显式) --------------------
    align = apply_alignment(
        plan, reference, ffmpeg=ffmpeg, work_dir=work_dir,
        requested=alignment_policy, log=log,
    )
    outcome.alignment = align.decision
    outcome.alignment_applied = int(align.applied)
    outcome.alignment_offsets = dict(align.offsets)
    outcome.alignment_statuses = dict(align.statuses)
    outcome.warnings.extend(align.warnings)
    outcome.notes.extend(align.notes)
    if not align.ok:
        outcome.errors = list(align.errors)
        return outcome

    # ---- 2. 输出编码策略 (manual > source > encoder default) ------------
    # 计划级决议有两个用途: 报告"用户意图解析成了什么", 以及**校验手写
    # codec 的组合是否成立** (例如 PCM + bitrate)。继承模式下它不判死:
    # §35 是按来源说的, 真正的编码参数由下面逐组决议。
    plan_format = resolve_output_format(
        plan, requested=spec, explicit=explicit,
    )
    outcome.format = plan_format
    outcome.warnings.extend(plan_format.warnings)
    outcome.notes.extend(plan_format.notes)
    # 显式拧过的旋钮不能"因为最终没编码就悄悄不算数": 手写 codec 的组合
    # (PCM + bitrate) 与**手写 bitrate 落到 lossless 输出** 都在这里判死。
    # 继承模式 (两者都没写) 下不判死: §35 是按来源说的, 真正的编码参数
    # 由下面逐组决议。
    if not plan_format.ok and (explicit.format or explicit.bitrate):
        outcome.errors = list(plan_format.errors)
        return outcome

    # ---- 3. 执行图判定 (唯一权威, 不重写) ------------------------------
    execution = resolve_audio_execution_path(plan, mix_bus=mix_bus)
    outcome.execution = execution
    outcome.path = execution.path

    if execution.path is AudioExecutionPath.NONE:
        # 计划存在但没有任何有效声道 -> 同样"什么都不做"。
        outcome.ok = True
        outcome.applied = False
        outcome.notes.append(
            "audio plan selects nothing: production default path untouched"
        )
        return outcome

    if not execution.ok:
        outcome.errors = [dict(i) for i in execution.issues] or [{
            "reason": REASON_PRODUCTION_AUDIO_PLAN_INVALID,
            "detail": "audio execution path is not executable",
        }]
        return outcome

    log(
        f"audio execution path: {execution.path.value} | "
        f"codec={plan_format.spec.format.value} "
        f"({plan_format.codec_origin})"
    )

    # ---- 4. 输出流结构 (mapping 的最终 structure) -----------------------
    structure = build_output_structure(plan, policy=manual_mapping)
    outcome.structure = structure
    if not structure.ok:
        outcome.errors = list(structure.errors)
        return outcome
    if not structure.groups:
        outcome.ok = True
        outcome.applied = False
        outcome.notes.append(
            "output structure has no group: production default path untouched"
        )
        return outcome
    if execution.path is AudioExecutionPath.PCM_MIX:
        _collapse_mix_structure(plan, structure, outcome)

    # ---- 4b. alignment 平移 -> **一份共用 render window** ---------------
    # `-map` + stream copy 没有"时间原点"可言, 而带 offset 的声道是按统一
    # timeline 重新取样的 (那份 window 的起点可能不是 0)。两者混在一个容器
    # 里就是两条流各有各的原点 —— 结果恰好是"看起来没对齐"。
    # 因此: 有非零 offset 时**整份输出共用同一张 render window**, 并且每条
    # 输出流都由 render 产出 (copy 无法表达这个 window)。
    align_window = None
    if any(float(v) != 0.0 for v in align.offsets.values()):
        align_window = _alignment_window(plan, structure, outcome)

    if execution.path is AudioExecutionPath.STREAM_COPY:
        # 纯整流保留: 保留 Phase 4C 的报告语义 (整份计划一次给选择器)。
        outcome.retention = _whole_plan_retention(plan, outcome)

    # ---- 5. 逐组产出: 整流 copy 或 渲染 + 编码 --------------------------
    if not _build_groups(
        plan, structure, outcome,
        ffmpeg=ffmpeg, ffprobe=ffprobe, work_dir=work_dir,
        output_path=output_path, requested_format=spec, explicit=explicit,
        mix_bus=mix_bus, audio_source=audio_source,
        shared_window=align_window,
    ):
        _cleanup(outcome)
        return outcome

    # ---- 6. 容器编排 (唯一 Composer, 不重写) ---------------------------
    composer = OutputComposer()
    composition = composer.compose(
        video, audio=outcome._compose_refs, output_path=output_path,
        ffmpeg=ffmpeg, ffprobe=ffprobe, timeout=timeout,
        extra_inputs=outcome._extra_inputs or None,
    )
    outcome.composition = composition
    outcome.output_path = composition.path
    outcome.warnings.extend(composition.warnings)

    if not composition.ok:
        outcome.errors = list(composition.errors)
        _cleanup(outcome)
        return outcome

    if not _verify_composition(composition, outcome):
        _cleanup(outcome)
        return outcome

    # ---- 7. 编排后校验 (编码音轨还必须是可解码的同一份内容) ------------
    if verify_decode and outcome.encoded:
        problems = _verify_encoded_streams(
            composition, outcome.encoded, ffmpeg,
        )
        if problems:
            outcome.errors = problems
            _cleanup(outcome)
            return outcome

    outcome.ok = True
    outcome.applied = True
    log(
        f"audio output composed: {len(composition.audio_tracks)} track(s), "
        f"path={composition.path}"
    )
    return outcome


# ---------------------------------------------------------------------------
# 逐组产出
# ---------------------------------------------------------------------------


def _requested_format(
    request: Any, audio_format: AudioFormatSpec | str | None,
) -> tuple[Any, ExplicitFormat]:
    """(请求的 spec, 显式标记) —— 手写 `audio_format` 视为全显式覆盖。"""
    if audio_format is not None:
        return audio_format, ExplicitFormat.all_set()
    if request is None:
        return None, ExplicitFormat()
    return (
        getattr(request, "encode", None),
        getattr(request, "encode_explicit", None) or ExplicitFormat(),
    )


def _alignment_window(
    plan: AudioPlan,
    structure: OutputStructure,
    outcome: ProductionAudioOutcome,
) -> Any:
    """alignment 平移后, 整份输出共用的 render window。

    为什么这**不是优化而是正确性**:

    * `-map` + stream copy 是"把源字节原样搬进容器", 没有时间原点可言;
    * 有 offset 的声道是按统一 timeline **重新取样**的, 而那张 timeline 的
      window 起点可能不是 0 (一条 target 前移 480 样本时 union window 从
      -480 开始);
    * 两者混在同一个容器里 = 两条流各有各的原点 —— 表现出来的就是"对齐没
      生效"。

    因此一旦真的产生了非零 offset: (1) 每条输出流都由 render 产出, (2) 所有
    组共用**同一份** window。组数与每组声道保持不变 —— mapping 不因为
    alignment 而改变 (§13/§14)。
    """
    window = shared_render_window(plan)
    outcome.warnings.append(
        "alignment shifts the sample timeline: every output stream is "
        f"rendered on one shared window (start={window.start_sample} samples, "
        f"{window.frame_count} frames) so the aligned positions hold in the "
        "container"
    )
    structure.notes.append(
        "alignment applied: shared render window "
        f"[{window.start_sample}, {window.end_sample}) for "
        f"{len(structure.groups)} output stream(s)"
    )
    outcome.notes.append(
        "stream copy is not used while alignment is applied: -map cannot "
        "express the shared render window"
    )
    for group in structure.groups:
        group.strategy = "aligned"
    return window


def _source_path(plan: AudioPlan, source_id: str) -> str | None:
    source = plan.source(str(source_id))
    path = getattr(source, "path", None) if source is not None else None
    return str(path) if path else None


def _collapse_mix_structure(
    plan: AudioPlan,
    structure: OutputStructure,
    outcome: ProductionAudioOutcome,
) -> None:
    """混音图**永远只产出一条**输出流 (bus 的输出声道)。

    这不是本层的策略选择, 而是混音图的结构事实:

        N 个源声道 --Σ(sample × gain)--> 1 个输出声道

    因此"2CH grouped 输出结构"与混音图不能同时成立 —— 与其给出一条静默
    重复 N 次的错误输出, 不如把结构显式收敛成一组并记录 warning。
    """
    from core.audio_output_structure import OutputGroup

    if len(structure.groups) == 1:
        return
    outcome.warnings.append(
        "the mixing graph defines a single output stream; the requested "
        f"output structure ({structure.policy.summary()}, "
        f"{len(structure.groups)} stream(s)) is collapsed to it"
    )
    structure.groups = [OutputGroup(
        channels=list(structure.channel_ids),
        kind="mix",
        mode="mix",
        strategy="mixing",
    )]
    structure.notes.append(
        "mixing graph: output structure collapsed to the single mixed stream"
    )


def _stream_audio_position(plan: AudioPlan, source_id: str, stream_index: Any) -> int | None:
    source = plan.source(str(source_id))
    if source is None or stream_index is None:
        return None
    stream = source.stream(int(stream_index))
    if stream is None:
        return None
    if stream.audio_position is None:
        return None
    return int(stream.audio_position)


def _whole_plan_retention(
    plan: AudioPlan, outcome: ProductionAudioOutcome,
) -> AudioRetentionSpec | None:
    """整份计划恰好可以整流保留时, 给出保留规格 (报告用)。"""
    retention = build_audio_retention(plan)
    outcome.warnings.extend(retention.warnings)
    return retention if retention.ok else None


def _copy_selector(
    plan: AudioPlan,
    group: Any,
    audio_source: Path | str | None,
    outcome: ProductionAudioOutcome,
) -> tuple[str, str] | None:
    """一条"整流保留"输出流 -> (文件路径, `-map` 段) —— 相对该文件。

    用既有 `build_audio_retention()` 在**该组的子计划**上取选择器: 子计划的
    输出恰好是一条完整的源流, 因此保留规格必须给出**一个** `-map`, 否则
    说明"执行图说可以 copy, 保留层说不行" —— 那是真问题, 明确报错。
    """
    sub = group_plan(plan, group.channels)
    retention = build_audio_retention(sub)
    outcome.warnings.extend(retention.warnings)
    selectors = []
    if retention.stream_copyable:
        args = retention.arguments()
        selectors = [
            args[i + 1] for i, token in enumerate(args[:-1]) if token == "-map"
        ]
    if len(selectors) != 1:
        outcome.errors.append({
            "reason": REASON_PRODUCTION_AUDIO_RETAIN_INVALID,
            "detail": (
                f"group {group.channels} was classified as stream copy but "
                f"the retention spec produced {len(selectors)} selector(s) "
                f"(reasons={retention.reasons})"
            ),
        })
        return None
    selector = selectors[0]
    if not selector.startswith("0:"):
        outcome.errors.append({
            "reason": REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH,
            "detail": (
                f"retention selector {selector!r} does not address the "
                "group's own input (expected a '0:...' selector)"
            ),
        })
        return None
    path = _source_path(plan, group.source_id)
    if path is None and group.kind == "primary" and audio_source is not None:
        path = str(audio_source)
    if not path:
        outcome.errors.append({
            "reason": REASON_PRODUCTION_GROUP_INPUT_UNKNOWN,
            "detail": (
                f"cannot locate the input file for output group "
                f"{group.channels} (source {group.source_id!r} has no path)"
            ),
        })
        return None
    return path, selector


def _build_groups(
    plan: AudioPlan,
    structure: OutputStructure,
    outcome: ProductionAudioOutcome,
    *,
    ffmpeg: Path,
    ffprobe: Path | None,
    work_dir: Path | None,
    output_path: Path | str,
    requested_format: Any,
    explicit: ExplicitFormat,
    mix_bus: Any,
    audio_source: Path | str | None,
    shared_window: Any = None,
) -> bool:
    """按结构产出每条输出流; 成功返回 True。

    产出顺序 = `structure.groups` 顺序 = 容器里的音轨顺序。整条完整源流
    用 `-map` + copy (不解码、不重编码), 其余每条渲染 + 编码一次。
    `shared_window` 非 None 时 (alignment 平移过时间轴) 每条流都必须走
    render, 并共用那一份 window。
    """
    target = Path(output_path)
    scratch = work_dir or target.parent
    scratch.mkdir(parents=True, exist_ok=True)

    encoded: list[EncodedAudioOutput] = []
    pending: list[tuple[Any, str, str]] = []      # (group, path, selector)
    for group in structure.groups:
        sub = group_plan(plan, group.channels)
        sub_exec = resolve_audio_execution_path(sub, mix_bus=mix_bus)
        if not sub_exec.ok:
            outcome.errors = [dict(i) for i in sub_exec.issues] or [{
                "reason": REASON_PRODUCTION_AUDIO_PLAN_INVALID,
                "detail": (
                    f"output group {group.channels} is not executable"
                ),
            }]
            return False
        copyable = (
            shared_window is None
            and sub_exec.path is AudioExecutionPath.STREAM_COPY
            and int(sub_exec.stream_count) == 1
            and not sub_exec.uses_pcm
        )
        if copyable:
            found = _copy_selector(plan, group, audio_source, outcome)
            if found is None:
                return False
            pending.append((group, found[0], found[1]))
            continue
        # 需要 PCM: 逐组决议编码参数 (codec 继承**这一组**的来源, §35),
        # 然后渲染 + 编码一次 (组内顺序 = mapping 顺序)。
        decision = resolve_output_format(
            plan, requested=requested_format, explicit=explicit,
            channel_ids=group.channels,
        )
        outcome.warnings.extend(decision.warnings)
        outcome.notes.extend(decision.notes)
        if not decision.ok:
            outcome.errors = list(decision.errors)
            return False
        outcome.formats.append(decision)
        produced = _encode_group(
            sub, group, outcome, ffmpeg=ffmpeg, ffprobe=ffprobe,
            work_dir=scratch, output_path=target, index=len(encoded),
            audio_format=decision.spec, mix_bus=mix_bus,
            shared_window=shared_window,
        )
        if produced is None:
            return False
        encoded.append(produced)
        pending.append((group, produced.path, ""))

    # ---- 输入序号: 视频 = 0, 已编码音轨 = 1..K, 追加输入 = K+1.. ---------
    extra_inputs: list[str] = []
    for group, path, selector in pending:
        if selector and path not in extra_inputs:
            extra_inputs.append(path)
    base = 1 + len(encoded)

    refs: list[Any] = []
    encoded_iter = iter(encoded)
    for group, path, selector in pending:
        if selector:
            index = base + extra_inputs.index(path)
            absolute = f"{index}:{selector.split(':', 1)[1]}"
            refs.append(AudioInputRef.from_selector(absolute))
            outcome.groups.append({
                "channels": list(group.channels),
                "strategy": "stream_copy",
                "source_id": group.source_id,
                "selector": absolute,
                "input": path,
            })
        else:
            track = next(encoded_iter)
            refs.append(track)
            outcome.groups.append({
                "channels": list(group.channels),
                "strategy": "encode",
                "source_id": group.source_id,
                "codec": track.codec,
                "channels_in_file": int(track.channel_count),
            })

    outcome.encoded = encoded
    outcome._compose_refs = refs
    outcome._extra_inputs = extra_inputs

    copied = sum(1 for g in pending if g[2])
    if copied and len(encoded):
        codecs = sorted({f.codec for f in outcome.formats})
        outcome.notes.append(
            f"{copied} output stream(s) are whole source streams and stay "
            f"stream-copied (their original codec is kept); the encoded "
            f"codec(s) {codecs} apply to the {len(encoded)} stream(s) that "
            "must be built from PCM"
        )
    if encoded and outcome.path is AudioExecutionPath.STREAM_COPY:
        # 计划级判定说"整流保留", 但输出结构要求其中若干条流重新构建 ——
        # 报告必须反映**实际做了什么**, 因此这里的结论升级为 PCM_ROUTE
        # (`execution.path` 仍是计划级判定的原样记录)。
        outcome.path = AudioExecutionPath.PCM_ROUTE
        outcome.notes.append(
            f"plan-level execution path was stream_copy; {len(encoded)} of "
            f"{len(pending)} output stream(s) are built from PCM because of "
            "the output structure (mapping), so the effective path is "
            "pcm_route"
        )
    return True


def _encode_group(
    sub: AudioPlan,
    group: Any,
    outcome: ProductionAudioOutcome,
    *,
    ffmpeg: Path,
    ffprobe: Path | None,
    work_dir: Path,
    output_path: Path,
    index: int,
    audio_format: AudioFormatSpec,
    mix_bus: Any,
    shared_window: Any = None,
) -> EncodedAudioOutput | None:
    """一组声道 -> 渲染 + 编码 -> `EncodedAudioOutput` (或 None=失败)。"""
    suffix = audio_format.format.suffix
    encoded_path = work_dir / f"{output_path.stem}.audio{index}{suffix}"
    kwargs: dict[str, Any] = {}
    if mix_bus is not None:
        kwargs["mix_bus"] = mix_bus
    if shared_window is not None:
        # 显式 window: 每组都渲染**同一段** timeline, 时间原点因此一致。
        kwargs["explicit_start_seconds"] = shared_window.start_seconds
        kwargs["explicit_duration_seconds"] = shared_window.duration_seconds

    result = encode_audio_from_plan(
        sub, ffmpeg=ffmpeg, work_dir=work_dir, output_path=encoded_path,
        audio_format=audio_format, ffprobe=ffprobe, overwrite=True, **kwargs,
    )
    outcome.warnings.extend(result.warnings)
    if not result.ok or result.output is None:
        outcome.errors = list(result.errors) or [{
            "reason": REASON_PRODUCTION_AUDIO_PLAN_INVALID,
            "detail": (
                f"audio encode produced no output for group "
                f"{group.channels}"
            ),
        }]
        return None
    return result.output


def _verify_composition(
    composition: Any, outcome: ProductionAudioOutcome,
) -> bool:
    """校验容器里的音轨**条数、种类与顺序**确实是我们要的结构。

    Composer 会给每条音轨记录它实际用的选择器 / 音轨来源; 这里逐条比对,
    因此"输入序号算错"或"某组被换成了 copy"这类错误不会以"文件看起来没
    问题"的形式溜过去。
    """
    tracks = list(getattr(composition, "audio_tracks", None) or [])
    if len(tracks) != len(outcome.groups):
        outcome.errors.append({
            "reason": REASON_PRODUCTION_VERIFY_FAILED,
            "detail": (
                f"container has {len(tracks)} audio track(s) but the output "
                f"structure has {len(outcome.groups)}"
            ),
        })
        return False
    for index, (track, group) in enumerate(zip(tracks, outcome.groups)):
        strategy = str(group.get("strategy") or "")
        if strategy == "stream_copy":
            expected = str(group.get("selector") or "")
            if track.get("kind") != "selector" or track.get("selector") != expected:
                outcome.errors.append({
                    "reason": REASON_PRODUCTION_VERIFY_FAILED,
                    "detail": (
                        f"audio track {index} was composed from "
                        f"{track.get('selector')!r} (kind={track.get('kind')!r}) "
                        f"but the structure planned the stream copy "
                        f"{expected!r} — input numbering mismatch"
                    ),
                })
                return False
        else:
            if track.get("kind") != "encoded":
                outcome.errors.append({
                    "reason": REASON_PRODUCTION_VERIFY_FAILED,
                    "detail": (
                        f"audio track {index} should come from the encoded "
                        f"artifact but was composed as "
                        f"{track.get('kind')!r}"
                    ),
                })
                return False
    return True


def _verify_encoded_streams(
    composition: Any,
    encoded: list[EncodedAudioOutput],
    ffmpeg: Path,
) -> list[dict[str, Any]]:
    """编排后校验: 每条编码音轨在容器里仍能解码出**合理**帧数。

    只做"能不能解码 + 帧数是否落在预期附近"这一层: 内容等价性已由
    `core.audio_encode` 的 encode->decode 回归负责, 这里防的是"容器里那条
    音轨其实不是我们编码的那条"。
    """
    from core.audio_encode import _probe_audio

    problems: list[dict[str, Any]] = []
    final = Path(composition.path)
    for index, track in enumerate(encoded):
        facts = _probe_audio(Path(track.path), None, ffmpeg)
        expected = int(track.expected_frames)
        got = int(facts.get("frames") or 0)
        if expected and got:
            # AAC 等有损格式有编码器 priming/padding; 这里只要求"同一量级"。
            if abs(got - expected) > max(4096, expected // 100):
                problems.append({
                    "reason": REASON_PRODUCTION_VERIFY_FAILED,
                    "detail": (
                        f"encoded track {index} reports {got} frames but the "
                        f"timeline expects {expected}"
                    ),
                })
        if not final.is_file():
            problems.append({
                "reason": REASON_PRODUCTION_VERIFY_FAILED,
                "detail": f"composed output missing: {final}",
            })
    return problems


def _cleanup(outcome: ProductionAudioOutcome) -> None:
    """失败时清掉本模块产生的编码中间产物 (工作区不攒垃圾)。"""
    for track in outcome.encoded:
        try:
            Path(track.path).unlink(missing_ok=True)
        except OSError:
            pass
    # 失败可能留下半成品容器: 一并删掉, 免得被当成"成功产物"。
    if outcome.output_path:
        try:
            Path(outcome.output_path).unlink(missing_ok=True)
        except OSError:
            pass


def derive_video_only_command(cmd: list[str]) -> list[str]:
    """把一条"一次成型"的编码命令派生成**只产出视频**的命令。

    为什么需要它: 生产软件路径 (`encoders/x265.py` / `svtav1.py`) 的命令是
    `-map 0` + `-c:a copy` 一次成型。若要在之后用 Composer 编排音频, 视频
    必须是一个**独立产物**。做法只有两种:

    1. 先照常编码 (顺带 copy 一份音频), 再把视频挑出来重新组装 —— 白拷
       一份音频, 且中间产物带着两条轨;
    2. 把同一条命令派生成"只要视频"。

    这里采用 (2): **不重写任何编码参数**, 只去掉音频相关的 token, 因此视频
    bitstream 与既有路径严格一致 (由回归用 elementary-stream sha256 钉住)。

    派生是**区间式**的, 不靠猜:

    * `-map 0` (全流映射) 区间内: 去掉 `-map 0`, 跳过 `-c:a` / `-c:s` /
      `-c:d` / `-c:t` 及其取值;
    * 其余区间 (`-map 0:v:0` 这类显式映射) : 原样保留, 只补 `-an`。

    派生结果**必须**含 `-an`, 否则说明这条命令的形态超出了本函数能安全处理
    的范围 —— 此时抛错, 而不是产出一条"可能同时编了音频"的命令。
    """
    strip_map = False
    out: list[str] = []
    index = 0
    while index < len(cmd):
        token = cmd[index]
        if token == "-map" and index + 1 < len(cmd) \
                and cmd[index + 1] == "0":
            strip_map = True
            index += 2
            continue
        if token in ("-c:a", "-c:s", "-c:d", "-c:t"):
            index += 2                       # 丢掉 codec 及其取值
            continue
        if strip_map and token.startswith("-c:a:") :
            index += 2
            continue
        out.append(token)
        index += 1

    if "-an" not in out:
        # 插到输出路径之前 (最后一个元素是输出文件)。
        out = out[:-1] + ["-an"] + out[-1:]
    if "-an" not in out:
        raise ValueError(
            "production_audio_plan_invalid: cannot derive a video-only "
            "command from this encoder command"
        )
    return out
