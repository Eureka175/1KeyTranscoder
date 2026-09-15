"""Selective MP4 Audio Retention (Phase 4A) — 选择性地保留 MP4 音频。

目标
----

把已经稳定的 `AudioPlan` / `AudioMapSpec` / `AudioTimeline` 链路接到**最终
容器输出**上, 让"保留哪条音轨、以什么顺序保留"成为一个显式、可测试、可
序列化的决定, 而不是散落在各编码后端的硬编码 `-map 0`。

与视频**完全解耦**: 本模块只回答"音频怎么进容器"。它不认识视频编码器、
不读视频流、不依赖视频是 stream copy 还是重编码 —— 音频选择是独立决定。

本阶段 (4A) 的能力边界
-----------------------

已实现:

    无音频计划           -> `-map 0` + `-c:a copy`   (生产默认, 完全不变)
    整条音频流选择/重排  -> `-map <in>:a:<pos>` + `-c:a copy` (原编码保留)
    删除某条音频流       -> 不产生对应的 `-map`
    video                -> 不参与, 不修改 (见上)

**未实现** (Phase 4B+): 音频编码 (AAC/Opus/…)、PCM -> 编码音轨的写回、
声道过滤 filtergraph、码率/质量参数、新 CLI。因此当输出需要"部分声道 /
重排"(`PCM_ROUTE`) 或"样本合成"(`PCM_MIX`) 时, 本模块**明确报错并拒绝**
产生可执行的 `-map`, 而不是假装 stream copy 能表达它。

身份原则
--------

* 身份仍是 `source -> stream -> channel`; 本模块**不引入**第二套
  `mp4_audio_index` 身份。
* `stream_index` (容器索引) 与 `audio_position` (`-map 0:a:N` 的 N) 严格
  区分: `-map` 选择器一律用 `audio_position`, 绝不用 `stream_index` 顶替。
* 输出顺序的权威是 `AudioTimeline.output_channel_ids`; 本模块的
  `channel_ids` 必须与它**逐一相等**, 否则报
  `audio_retention_order_mismatch` 并拒绝执行 —— 不允许出现两个顺序真相。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from .audio_execution import (
    AudioExecutionPath,
    AudioExecutionPlan,
    resolve_audio_execution_path,
)
from .audio_models import AudioPlan
from .audio_plan import AudioMapStrategy, build_map_spec, effective_mapping

__all__ = [
    "AudioOutputPacket",
    "AudioPacketKind",
    "AudioRetentionSpec",
    "DEFAULT_AUDIO_CODEC",
    "REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED",
    "REASON_AUDIO_RETENTION_EXECUTION_INVALID",
    "REASON_AUDIO_RETENTION_ORDER_MISMATCH",
    "REASON_AUDIO_RETENTION_SELECTOR_UNKNOWN",
    "REASON_AUDIO_RETENTION_SOURCE_MISSING",
    "build_audio_retention",
    "format_audio_retention",
    "plan_has_audio",
    "retention_to_args",
]

#: 保留原编码时的 ffmpeg 参数 (stream copy, 不重新编码)。
DEFAULT_AUDIO_CODEC = "copy"

REASON_AUDIO_RETENTION_SOURCE_MISSING = "audio_retention_source_missing"
REASON_AUDIO_RETENTION_SELECTOR_UNKNOWN = "audio_retention_selector_unknown"
REASON_AUDIO_RETENTION_ORDER_MISMATCH = "audio_retention_order_mismatch"
REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED = (
    "audio_retention_channel_filter_unsupported"
)
REASON_AUDIO_RETENTION_EXECUTION_INVALID = "audio_retention_execution_invalid"


class AudioPacketKind(str, Enum):
    """一个输出音频单元是怎么来的。"""

    #: 整条源流原样保留 (`-map` + `-c:a copy`), 输出字节 = 源字节。
    COPY = "copy"
    #: 需要按声道搬运 —— 当前阶段**不可直接执行** (Phase 4B 写回)。
    ROUTED = "routed"


@dataclass
class AudioOutputPacket:
    """最终容器里的**一个**输出音频单元。

    `source_id` / `stream_index` / `channel_index` 保留完整身份链;
    `selector` 是给 ffmpeg 的 `-map` 值 (用 `audio_position`, 不是
    `stream_index`); `input_index` 是多输入时的 `-i` 序号。
    """

    output_stream_index: int
    kind: AudioPacketKind
    source_id: str
    stream_index: int
    audio_position: int | None
    input_index: int | None
    channel_indices: list[int] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    channel_count: int = 0
    stream_channel_count: int = 0
    strategy: str = ""
    selector: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_copy(self) -> bool:
        return self.kind is AudioPacketKind.COPY

    @property
    def stream_id(self) -> str:
        return f"{self.source_id}:s{self.stream_index}"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "output_stream_index": int(self.output_stream_index),
            "kind": self.kind.value,
            "source_id": self.source_id,
            "stream_id": self.stream_id,
            "stream_index": int(self.stream_index),
            "channel_indices": list(self.channel_indices),
            "channel_ids": list(self.channel_ids),
            "channel_count": int(self.channel_count),
        }
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.input_index is not None:
            data["input_index"] = int(self.input_index)
        if self.stream_channel_count:
            data["stream_channel_count"] = int(self.stream_channel_count)
        if self.strategy:
            data["strategy"] = self.strategy
        if self.selector:
            data["selector"] = self.selector
        if self.notes:
            data["notes"] = list(self.notes)
        return data


@dataclass
class AudioRetentionSpec:
    """"最终 MP4 里保留哪些音频"的完整声明 (不执行任何东西)。

    * `path`      —— 来自 `resolve_audio_execution_path()` 的图判定;
    * `no_audio`  —— 明确不要音频 (输出由调用方加 `-an`);
    * `default_plan` —— 走生产默认 (`-map 0` + `-c:a copy`), 与重构前逐字
      一致, 且**不**枚举音轨;
    * `packets`   —— 逐输出音频单元 (顺序 = 容器里的音轨顺序);
    * `arguments()` —— 可直接拼进 argv 的 `-map` / `-c:a` 片段。

    `stream_copyable` 为 False 时 `arguments()` **抛异常**: 宁可失败, 也
    不能输出一条"声称是 stream copy 其实是错的"命令行。
    """

    path: AudioExecutionPath
    execution: AudioExecutionPlan | None = None
    packets: list[AudioOutputPacket] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    no_audio: bool = False
    default_plan: bool = False
    preserve_original: bool = True
    notes: list[str] = field(default_factory=list)

    # -- 结论 -------------------------------------------------------------

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def stream_copyable(self) -> bool:
        """是否可以只用 `-map` + `-c:a copy` 完成 (本阶段唯一可执行形态)。"""
        if not self.ok or self.no_audio or not self.packets:
            return False
        return all(p.is_copy for p in self.packets)

    @property
    def output_stream_count(self) -> int:
        return len(self.packets)

    @property
    def reasons(self) -> list[str]:
        return [str(e.get("reason") or "") for e in self.errors]

    # -- argv 片段 --------------------------------------------------------

    def arguments(self, *, codec: str = DEFAULT_AUDIO_CODEC) -> list[str]:
        """-> `["-map", …, "-c:a", "copy"]` (本阶段可执行的唯一形态)。

        默认计划返回空列表: 生产默认路径的 `-map 0` + `-c:a copy` 已经由
        既有代码给出, 本模块**不重复也不改写**它 —— "默认行为不变"是靠
        "什么都不做"保证的, 不是靠重新拼一遍。
        """
        if self.default_plan:
            return []
        if self.no_audio:
            return ["-an"]
        if not self.stream_copyable:
            raise AudioRetentionError(
                REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED,
                "output is not expressible as pure -map + stream copy "
                f"(packets={[p.kind.value for p in self.packets]}, "
                f"reasons={self.reasons})",
            )
        args: list[str] = []
        for packet in self.packets:
            args += ["-map", packet.selector]
        args += ["-c:a", codec]
        return args

    # -- 报告 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path.value,
            "ok": bool(self.ok),
            "stream_copyable": bool(self.stream_copyable),
            "no_audio": bool(self.no_audio),
            "default_plan": bool(self.default_plan),
            "output_stream_count": int(self.output_stream_count),
            "packets": [p.to_dict() for p in self.packets],
        }
        if self.channel_ids:
            data["channel_ids"] = list(self.channel_ids)
        if self.execution is not None:
            data["execution"] = self.execution.to_dict()
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path.value,
            "streams": int(self.output_stream_count),
            "copyable": bool(self.stream_copyable),
            "no_audio": bool(self.no_audio),
            "default": bool(self.default_plan),
            "reasons": self.reasons,
        }


class AudioRetentionError(Exception):
    """保留规格不可执行 (稳定 reason code, 不是"要不要尽力输出"的问题)。"""

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
# selector 构造
# ---------------------------------------------------------------------------

def _map_selector(
    input_index: int | None, audio_position: int | None,
) -> str:
    """`-map` 选择器 —— **只**用 `audio_position`, 绝不用 `stream_index`。

    ⚠️ 输入前缀**必须**显式写出: ffmpeg 的 `-map` 语法里 `a:0` 不是
    "第一个输入的第 0 条音频" 的合法写法 (缺少流说明符时它按**流索引**
    解释, 语义完全不同)。`input_index is None` 表示"未指定输入", 等价于
    第一个输入 (`0`), 因此这里落成 `0:a:N`。

    这正是 `stream_index` / `audio_position` / `input_index` 三者必须分开
    持有的原因: 任何"猜一个数字"的做法都会静默选错流。
    """
    prefix = 0 if input_index is None else int(input_index)
    if audio_position is None:
        return f"{prefix}:a"
    return f"{prefix}:a:{int(audio_position)}"


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def plan_has_audio(plan: AudioPlan | None) -> bool:
    """计划里是否存在**可参与输出**的音频 (空计划 = False)。"""
    if plan is None:
        return False
    from .audio_plan import effective_mapping

    return bool(effective_mapping(plan))


def _timeline_channel_ids(timeline: Any) -> list[str]:
    if timeline is None:
        return []
    return [str(c) for c in getattr(timeline, "output_channel_ids", []) or []]


def _resolve_stream_facts(
    plan: AudioPlan, source_id: str, stream_index: int,
) -> tuple[int | None, int | None, int]:
    """(input_index, audio_position, stream_channel_count)。

    容器 `stream_index` 与音频序号 `audio_position` 在这里**分开取**, 不通
    过解析 `channel_id` 反推 (那是明确的错误来源, 见 `core.audio_plan` 的
    `_stream_signature`)。
    """
    source = plan.source(source_id)
    stream = source.stream(stream_index) if source is not None else None
    return (
        source.input_index if source is not None else None,
        stream.audio_position if stream is not None else None,
        stream.channel_count if stream is not None else 0,
    )


def _operations_to_packets(
    plan: AudioPlan, operations: Sequence[Any], spec: AudioRetentionSpec,
) -> list[AudioOutputPacket]:
    """把 `AudioMapSpec.operations` 归并成输出单元。

    归并规则 (决定能不能 `-map`): 同一条源流的所有操作**合并**成一个输出
    单元, 只有当

      * 合并后的输出位置连续, 且
      * 覆盖该流**全部**声道, 且顺序 = 源顺序

    时才可能用 `-map <in>:a:<pos>`; 否则该单元是 `ROUTED` (Phase 4B)。

    注意 `-map` 的**位置语义**: 每个 `-map` 参数按顺序占用输出流编号, 因此
    只有当 COPY 单元恰好构成输出的**前缀**时, 直接拼接 `-map` 才是正确的
    顺序。COPY 落在 ROUTED 之后时无法只用 `-map` 表达, 记为错误。
    """
    # 按 (input, source, stream) 归并, 保留首次出现的输出位置顺序
    order: list[tuple[str, int]] = []
    grouped: dict[tuple[str, int], list[Any]] = {}
    for op in operations:
        key = (str(op.source_id), int(op.stream_index))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(op)

    packets: list[AudioOutputPacket] = []
    for key in order:
        ops = grouped[key]
        source_id, stream_index = key
        input_index, audio_position, total = _resolve_stream_facts(
            plan, source_id, stream_index
        )
        if input_index is None and audio_position is None and total <= 0:
            spec.errors.append({
                "reason": REASON_AUDIO_RETENTION_SOURCE_MISSING,
                "detail": (
                    f"source/stream facts unavailable for {source_id!r} "
                    f"stream {stream_index} (cannot build a -map selector)"
                ),
                "location": f"{source_id}:s{stream_index}",
            })

        indices: list[int] = []
        ids: list[str] = []
        out_positions: list[int] = []
        for op in ops:
            indices.extend(int(i) for i in op.channel_indices)
            ids.extend(str(i) for i in op.channel_ids)
            out_positions.extend(int(i) for i in op.output_indices)

        contiguous = bool(out_positions) and out_positions == list(
            range(min(out_positions), min(out_positions) + len(out_positions))
        )
        complete = total > 0 and sorted(indices) == list(range(total))
        natural = indices == list(range(total))
        strategy = ops[0].strategy
        if len({op.strategy for op in ops}) > 1:
            strategy = AudioMapStrategy.CHANNEL_FILTER

        copyable = bool(
            contiguous and complete and natural
            and strategy is AudioMapStrategy.STREAM_COPY
        )
        kind = AudioPacketKind.COPY if copyable else AudioPacketKind.ROUTED
        notes: list[str] = []
        if kind is AudioPacketKind.ROUTED:
            if not complete:
                notes.append("channel subset: -map cannot express it")
            elif not natural:
                notes.append("channel reorder: -map cannot express it")
            elif not contiguous:
                notes.append("stream split across output positions")
            if strategy is not AudioMapStrategy.STREAM_COPY:
                notes.append(f"strategy={strategy.value}")

        packets.append(AudioOutputPacket(
            output_stream_index=len(packets),
            kind=kind,
            source_id=source_id,
            stream_index=stream_index,
            audio_position=audio_position,
            input_index=input_index,
            channel_indices=indices,
            channel_ids=ids,
            channel_count=len(indices),
            stream_channel_count=total,
            strategy=strategy.value,
            selector=_map_selector(input_index, audio_position),
            notes=notes,
        ))
    return packets


def build_audio_retention(
    plan: AudioPlan | None,
    *,
    timeline: Any = None,
    mix_bus: Any = None,
) -> AudioRetentionSpec:
    """`AudioPlan` (+ `AudioTimeline`) -> `AudioRetentionSpec`。

    纯声明: 不读 PCM, 不执行 ffmpeg, 不写文件。`plan is None` 时返回
    `default_plan=True` 且 `arguments()` 为空 —— 生产默认路径
    (`-map 0` + `-c:a copy`) 原样保留, 本模块不参与。

    `timeline` 传入时, 其 `output_channel_ids` 是**输出顺序的权威**;
    与本模块从 `AudioMapSpec` 推出的顺序不一致即报
    `audio_retention_order_mismatch` 并拒绝。
    """
    execution = resolve_audio_execution_path(plan, mix_bus=mix_bus)

    if plan is None:
        return AudioRetentionSpec(
            path=AudioExecutionPath.NONE,
            execution=execution,
            default_plan=True,
            notes=[
                "no audio plan: production default -map 0 + -c:a copy "
                "(this module contributes nothing)"
            ],
        )

    if not plan_has_audio(plan):
        return AudioRetentionSpec(
            path=AudioExecutionPath.NONE,
            execution=execution,
            no_audio=True,
            errors=[dict(i) for i in execution.issues],
            notes=["no channel selected: output carries no audio (-an)"],
        )

    if execution.path.uses_pcm:
        # PCM 图是**明确的转交信号**, 不是"计划非法": 分层混音/路由由
        # core.audio_process 负责, 而把 PCM 写回编码音轨属于 Phase 4B。
        # 因此这里**明确**给出"不可用 -map 表达"的 reason, 而不是留一个
        # 空规格让调用方以为"没有音轨要保留"。
        errors = [dict(i) for i in execution.issues]
        errors.append({
            "reason": REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED,
            "detail": (
                f"execution path is {execution.path.value}: the output is "
                "produced by the PCM pipeline and cannot be expressed as "
                "-map + stream copy (encoding and mux write-back are 4B)"
            ),
        })
        return AudioRetentionSpec(
            path=execution.path,
            execution=execution,
            channel_ids=[
                str(e.get("source_channel_id") or "")
                for e in effective_mapping(plan)
            ],
            errors=errors,
            notes=[
                "routing/mixing graph: retention is produced by the PCM "
                "pipeline, not by -map (encode + mux write-back is Phase 4B)"
            ],
        )

    map_spec = build_map_spec(plan)
    spec = AudioRetentionSpec(
        path=execution.path,
        execution=execution,
        preserve_original=bool(map_spec.preserve_original),
        errors=[dict(e) for e in map_spec.errors],
        warnings=list(map_spec.warnings),
    )
    spec.packets = _operations_to_packets(plan, map_spec.operations, spec)

    # 输出顺序的唯一权威: AudioTimeline.output_channel_ids
    for packet in spec.packets:
        spec.channel_ids.extend(packet.channel_ids)
    reference = _timeline_channel_ids(timeline)
    if reference and reference != spec.channel_ids:
        spec.errors.append({
            "reason": REASON_AUDIO_RETENTION_ORDER_MISMATCH,
            "detail": (
                "container packet order disagrees with AudioTimeline: "
                f"packets={spec.channel_ids} timeline={reference}"
            ),
        })
    if not spec.packets and not spec.errors:
        spec.errors.append({
            "reason": REASON_AUDIO_RETENTION_SELECTOR_UNKNOWN,
            "detail": "no output packet could be derived from the map spec",
        })

    # COPY 单元必须是输出的前缀, 否则纯 -map 拼接会给出错误的音轨顺序。
    seen_routed = False
    for packet in spec.packets:
        if packet.is_copy and seen_routed:
            spec.errors.append({
                "reason": REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED,
                "detail": (
                    f"stream {packet.stream_id} is copied but appears after "
                    "a routed stream: -map ordering cannot express it"
                ),
                "location": packet.stream_id,
            })
        if not packet.is_copy:
            seen_routed = True

    # (b) 混音/路由混入 copy 路径 -> 明确拒绝, 绝不假装是 stream copy。
    if execution.issues:
        spec.errors.append({
            "reason": REASON_AUDIO_RETENTION_EXECUTION_INVALID,
            "detail": (
                "audio execution path is not valid: "
                f"{[i.get('reason') for i in execution.issues]}"
            ),
        })
    return spec


def retention_to_args(
    spec: AudioRetentionSpec, *, codec: str = DEFAULT_AUDIO_CODEC,
) -> list[str]:
    """便捷入口: `AudioRetentionSpec.arguments()` (失败即抛)。"""
    return spec.arguments(codec=codec)


def format_audio_retention(spec: AudioRetentionSpec) -> str:
    """一行可读摘要 (日志/测试断言用, 稳定格式)。"""
    if spec.default_plan:
        return "audio-retention: default (-map 0 + -c:a copy)"
    if spec.no_audio:
        return "audio-retention: no audio (-an)"
    parts = [
        f"{p.kind.value}:{p.selector}"
        f"[{','.join(str(i) for i in p.channel_indices)}]"
        for p in spec.packets
    ]
    return (
        f"audio-retention: path={spec.path.value} "
        f"streams={len(spec.packets)} copyable={spec.stream_copyable} "
        f"{' '.join(parts)}"
    )
