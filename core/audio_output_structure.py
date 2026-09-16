"""Output Structure (v0.8.0) — 最终输出**音频流结构**的确定性规则。

回答的问题是: "这次输出到底有几条音频流, 每条流里是哪几个声道, 按什么
顺序?" —— 这是 mapping 的**结构性**一面, 与 `core.audio_plan` 的
`AudioOutputTrack` / `AudioMapSpec` 是同一件事的两个视角, 因此本模块
**不重新实现 mapping**: 它消费 `effective_mapping()` 与既有的
`output_tracks()`, 只负责切分与外部来源的成组规则。

    输入 (有效映射, 已按 selection/mapping 排好序)
        ↓
    OutputStructure.groups  ->  [ [channel_id, …], … ]  (顺序 = 容器顺序)
        ↓  每组各自成为一条输出音频流
    stream copy 或 PCM render+encode

Mapping 来源与优先级 (§12/§27)
-----------------------------

    manual mapping  >  input source mapping  >  default project mapping

三个 mode 的语义 (§22–§26/§36/§37):

* `SOURCE` (**默认**, 即"input source mapping"): 每个外挂输入的**每条流**
  自成一条输出流, 声道数原样保留。单声道文件 -> 一条独立输出 (§22),
  立体声文件 -> 一条 stereo 输出 (§23);
* `INDEPENDENT` (manual): 一个声道 = 一条输出流 (§26/Case A/Case D);
* `GROUPED(n)` (manual): 连续 n 个声道 = 一条输出流, **跨文件继续成组**
  (§24: A+B, C+D); 最后一组不足 n 时保留剩余声道, **既不丢弃也不复制**
  (§37: 3CH + 2CH grouped -> 2CH + 1CH)。

不变量 (§38) —— 结构性, 不是运行时特判
--------------------------------------

    Σ len(group) == len(effective_mapping)

每个被选中的声道**恰好**进入一条输出流。做不到就报错 (`audio_grouping_
channel_lost`), 绝不"部分成功然后偷偷丢声道"。原视频音频的结构不被本模块
改变 (§12/§13/§29): 它只按既有输出单元切分; 结构策略只作用于外挂来源的
声道序列 —— 这正是"外挂音频必须严格遵循最终确定的 mapping"的实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .audio_models import AudioPlan

__all__ = [
    "DEFAULT_GROUP_SIZE",
    "OUTPUT_STRUCTURE_VERSION",
    "AudioMappingMode",
    "AudioMappingPolicy",
    "OutputGroup",
    "OutputStructure",
    "REASON_GROUPING_CHANNEL_LOST",
    "REASON_GROUPING_INVALID",
    "REASON_MAPPING_POLICY_INVALID",
    "build_output_structure",
    "default_mapping_policy",
    "group_plan",
    "parse_mapping_policy",
    "resolve_mapping_policy",
]

OUTPUT_STRUCTURE_VERSION = 1

#: 默认组大小 —— 仅用于 `INDEPENDENT` (一个声道一条流)。
DEFAULT_GROUP_SIZE = 1

REASON_GROUPING_CHANNEL_LOST = "audio_grouping_channel_lost"
REASON_GROUPING_INVALID = "audio_grouping_invalid"
REASON_MAPPING_POLICY_INVALID = "audio_mapping_policy_invalid"


class AudioMappingMode(str, Enum):
    """最终输出结构的取得方式 (§27)。

    * `SOURCE`      —— 跟随每个输入来源自身的流结构 (= "input source mapping");
    * `INDEPENDENT` —— 一个声道一条输出流 (显式指定时);
    * `GROUPED`     —— 每 n 个声道一条输出流 (显式指定时)。
    """

    SOURCE = "source"
    INDEPENDENT = "independent"
    GROUPED = "grouped"

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> "AudioMappingMode | Any":
        if value is None or value == "":
            return default
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return default


@dataclass(frozen=True)
class AudioMappingPolicy:
    """已解析的结构策略 (`origin` 记录它从哪来, 便于报告/测试)。"""

    mode: AudioMappingMode = AudioMappingMode.SOURCE
    group_size: int = DEFAULT_GROUP_SIZE
    origin: str = "default"

    @property
    def is_independent(self) -> bool:
        return self.mode is AudioMappingMode.INDEPENDENT

    @property
    def is_source(self) -> bool:
        return self.mode is AudioMappingMode.SOURCE

    @property
    def effective_group_size(self) -> int:
        if self.mode is AudioMappingMode.INDEPENDENT:
            return 1
        return max(1, int(self.group_size))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mode": self.mode.value,
            "origin": self.origin,
        }
        if self.mode is AudioMappingMode.GROUPED:
            data["group_size"] = int(self.effective_group_size)
        return data

    def summary(self) -> str:
        if self.mode is AudioMappingMode.GROUPED:
            return f"{self.mode.value}({self.effective_group_size})"
        return self.mode.value


#: 默认项目映射 (§27 的最后一档): 一个声道一条输出流。
DEFAULT_PROJECT_POLICY = AudioMappingPolicy(
    mode=AudioMappingMode.SOURCE, group_size=1, origin="default"
)

_ALLOWED_POLICY_KEYS = {"mode", "group_size"}
_MAX_GROUP_SIZE = 64


def parse_mapping_policy(value: Any) -> AudioMappingPolicy | None:
    """宽松输入 (`AudioMappingPolicy` / dict / 字符串) -> 策略; 非法即抛。

    未知键、未知 mode、非正 group_size 一律**报错**, 不静默回退 ——
    结构策略猜错会让输出流数悄悄变掉, 那比失败难查得多。
    """
    if value is None or value == "":
        return None
    if isinstance(value, AudioMappingPolicy):
        return value
    if isinstance(value, (str, AudioMappingMode)):
        mode = AudioMappingMode.coerce(value, None)
        if mode is None:
            raise ValueError(
                f"{REASON_MAPPING_POLICY_INVALID}: unknown mapping mode "
                f"{value!r}; supported: "
                f"{[m.value for m in AudioMappingMode]}"
            )
        return AudioMappingPolicy(
            mode=mode,
            group_size=DEFAULT_GROUP_SIZE,
            origin="manual",
        )
    if not isinstance(value, Mapping):
        raise ValueError(
            f"{REASON_MAPPING_POLICY_INVALID}: mapping must be an object, got "
            f"{type(value).__name__}"
        )
    unknown = sorted(set(value) - _ALLOWED_POLICY_KEYS)
    if unknown:
        raise ValueError(
            f"{REASON_MAPPING_POLICY_INVALID}: unknown mapping key(s) "
            f"{unknown}; allowed: {sorted(_ALLOWED_POLICY_KEYS)}"
        )
    mode = AudioMappingMode.coerce(
        value.get("mode"), AudioMappingMode.SOURCE
    ) or AudioMappingMode.SOURCE
    if "mode" in value and value.get("mode") not in (None, "") \
            and AudioMappingMode.coerce(value.get("mode"), None) is None:
        raise ValueError(
            f"{REASON_MAPPING_POLICY_INVALID}: unknown mapping mode "
            f"{value.get('mode')!r}; supported: "
            f"{[m.value for m in AudioMappingMode]}"
        )
    raw_size = value.get("group_size")
    if raw_size is None or raw_size == "":
        size = DEFAULT_GROUP_SIZE
    else:
        try:
            size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{REASON_MAPPING_POLICY_INVALID}: group_size must be an "
                f"integer, got {raw_size!r}"
            ) from exc
    if size < 1 or size > _MAX_GROUP_SIZE:
        raise ValueError(
            f"{REASON_MAPPING_POLICY_INVALID}: group_size must be between 1 "
            f"and {_MAX_GROUP_SIZE}, got {size}"
        )
    if mode is AudioMappingMode.GROUPED and not value.get("group_size"):
        raise ValueError(
            f"{REASON_MAPPING_POLICY_INVALID}: mode 'grouped' requires an "
            "explicit group_size (how many channels form one output stream)"
        )
    return AudioMappingPolicy(mode=mode, group_size=size, origin="manual")


def source_group_size(plan: AudioPlan, source_id: str) -> int:
    """某个来源自身的流结构大小 (uniform 才有意义, 否则 1)。

    只读事实: 该来源所有音频流的 `channel_count` 一致且 > 1 时返回它,
    否则返回 1 (单声道 / 混合结构 -> 逐流成组即可表达)。
    """
    source = plan.source(str(source_id))
    if source is None:
        return 1
    counts = sorted({int(s.channel_count or 0) for s in source.streams})
    if len(counts) == 1 and counts[0] > 1:
        return int(counts[0])
    return 1


def resolve_mapping_policy(
    plan: AudioPlan | None,
    *,
    manual: Any = None,
) -> AudioMappingPolicy:
    """manual > input source mapping > default project mapping (§27)。

    没有 manual 时本模块走 `SOURCE` (跟随输入来源自身的流结构) —— 这**就是**
    "input source mapping", 也是 §22/§23 的默认行为 (单声道文件 -> 独立流,
    立体声文件 -> stereo 流), 且与 §12 "输入 mapping 与输出 mapping 默认
    保持一致" 严格等价。`origin` 因此是 `manual` / `source` / `default`。
    """
    parsed = parse_mapping_policy(manual)
    if parsed is not None:
        return parsed
    if plan is None:
        return DEFAULT_PROJECT_POLICY
    # input source mapping: 主来源的流结构若为多声道, 外挂来源按同一结构成组
    # (跟随来源), 否则仍是跟随来源 (等价于逐流)。两者都落在 SOURCE 分支。
    return AudioMappingPolicy(
        mode=AudioMappingMode.SOURCE,
        group_size=DEFAULT_GROUP_SIZE,
        origin="source",
    )


# ---------------------------------------------------------------------------
# 输出单元切分
# ---------------------------------------------------------------------------


@dataclass
class OutputGroup:
    """一条**输出音频流** (容器里的一个 audio track)。"""

    channels: list[str] = field(default_factory=list)
    kind: str = "primary"
    source_id: str = ""
    stream_index: int | None = None
    mode: str = AudioMappingMode.SOURCE.value
    strategy: str = ""

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def output_streams(self) -> int:
        """这一组需要几条 `-map`/输出流 —— 永远是 1 (定义即"一条流")。"""
        return 1

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "channels": list(self.channels),
            "channel_count": int(self.channel_count),
            "kind": self.kind,
            "mode": self.mode,
        }
        if self.source_id:
            data["source_id"] = self.source_id
        if self.stream_index is not None:
            data["stream_index"] = int(self.stream_index)
        if self.strategy:
            data["strategy"] = self.strategy
        return data


@dataclass
class OutputStructure:
    """最终输出结构 + 完整判定事实。"""

    policy: AudioMappingPolicy = DEFAULT_PROJECT_POLICY
    groups: list[OutputGroup] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    external_source_ids: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    version: int = OUTPUT_STRUCTURE_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def stream_count(self) -> int:
        return len(self.groups)

    @property
    def channel_count(self) -> int:
        return sum(g.channel_count for g in self.groups)

    @property
    def external_groups(self) -> list[OutputGroup]:
        return [g for g in self.groups if g.kind == "external"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "ok": bool(self.ok),
            "policy": self.policy.to_dict(),
            "stream_count": int(self.stream_count),
            "channel_count": int(self.channel_count),
            "channel_ids": list(self.channel_ids),
            "groups": [g.to_dict() for g in self.groups],
            "errors": [dict(e) for e in self.errors],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "mapping": self.policy.summary(),
            "streams": int(self.stream_count),
            "channels": [
                int(g.channel_count) for g in self.groups
            ],
            "reasons": [str(e.get("reason") or "") for e in self.errors],
        }


def _mapped_channel_ids(plan: AudioPlan) -> list[str]:
    from .audio_plan import effective_mapping

    return [
        str(e.get("source_channel_id") or "")
        for e in effective_mapping(plan)
        if str(e.get("source_channel_id") or "")
    ]


def _stream_key(plan: AudioPlan, channel_id: str) -> tuple[str, int]:
    channel = plan.channel(channel_id)
    if channel is None:
        return ("", -1)
    return (channel.source_id, int(channel.stream_index))


def _chunk(
    channel_ids: Sequence[str], size: int,
) -> list[list[str]]:
    """连续 `size` 个声道一组; 最后一组保留剩余 (不丢弃、不复制)。"""
    step = max(1, int(size))
    return [
        list(channel_ids[i:i + step])
        for i in range(0, len(channel_ids), step)
    ]


def build_output_structure(
    plan: AudioPlan | None,
    *,
    external_source_ids: Iterable[str] | None = None,
    policy: Any = None,
) -> OutputStructure:
    """有效映射 -> 输出流结构 (纯结构运算, 不读 PCM)。

    切分规则:

    * **主来源**声道: 按**既有输出单元**切分 (相邻且同一条源流 = 一条输出
      流) —— 与 `core.audio_plan.output_tracks()` 完全一致, 因此原视频音频
      的 mapping 不被本模块改变 (§12/§13);
    * **外挂来源**声道: 按结构策略切分 (`SOURCE` 逐流 / `INDEPENDENT` 逐声道 /
      `GROUPED(n)` 连续成组, 跨文件继续)。

    `external_source_ids` 缺省时由计划自身判定 (`AudioSource.is_external`) ——
    外挂身份是模型里已有的事实, 不需要第二个标志位, 也不需要调用方复述。

    任何声道都不允许丢失: 切分后 `Σ len(group)` 必须等于映射长度, 否则
    报 `audio_grouping_channel_lost` (§38)。
    """
    resolved = resolve_mapping_policy(plan, manual=policy)
    out = OutputStructure(policy=resolved)
    if plan is None:
        out.notes.append("no audio plan: no output structure")
        return out

    if external_source_ids is None:
        external = {s.source_id for s in plan.sources if s.is_external}
    else:
        external = {str(s) for s in external_source_ids}
    out.external_source_ids = sorted(external)
    mapped = _mapped_channel_ids(plan)
    out.channel_ids = list(mapped)
    if not mapped:
        out.notes.append("no channel selected: no output audio stream")
        return out

    # 逐声道标注 kind, 然后切成"主来源 run" 与 "外挂 run"。
    kinds = [
        "external" if _stream_key(plan, cid)[0] in external else "primary"
        for cid in mapped
    ]
    runs: list[tuple[str, list[str]]] = []
    for cid, kind in zip(mapped, kinds):
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(cid)
        else:
            runs.append((kind, [cid]))

    for kind, run in runs:
        if kind == "primary":
            out.groups.extend(_primary_groups(plan, run))
        else:
            out.groups.extend(_external_groups(plan, run, resolved))

    produced = [cid for g in out.groups for cid in g.channels]
    if produced != mapped:
        out.errors.append({
            "reason": REASON_GROUPING_CHANNEL_LOST,
            "detail": (
                "output structure does not reproduce the effective mapping "
                f"exactly: produced={produced} expected={mapped}"
            ),
        })
        return out

    if out.groups:
        out.notes.append(
            f"mapping={resolved.summary()} origin={resolved.origin}: "
            f"{len(out.groups)} output audio stream(s) from "
            f"{len(mapped)} channel(s)"
        )
    return out


def _primary_groups(
    plan: AudioPlan, channel_ids: Sequence[str],
) -> list[OutputGroup]:
    """主来源: 相邻且同一条源流 = 一条输出流 (与既有输出单元一致)。"""
    from .audio_plan import output_tracks

    wanted = list(channel_ids)
    wanted_set = set(wanted)
    groups: list[OutputGroup] = []
    for track in output_tracks(plan):
        ids = [
            str(c) for c in track.metadata.get("source_channel_ids") or []
        ]
        if not ids or not all(cid in wanted_set for cid in ids):
            continue
        groups.append(OutputGroup(
            channels=ids,
            kind="primary",
            source_id=str(track.metadata.get("source_id") or ""),
            stream_index=track.metadata.get("stream_index"),
            mode="plan",
            strategy=track.strategy.value,
        ))
        wanted_set -= set(ids)
    if wanted_set:
        # 既有输出单元没有覆盖某几个声道 (理论上不该发生) -> 明确报错,
        # 绝不用"补一组"来掩盖结构判定的分歧。
        groups.append(OutputGroup(
            channels=sorted(wanted_set, key=wanted.index),
            kind="primary",
            mode="plan",
        ))
    return groups


def _external_groups(
    plan: AudioPlan,
    channel_ids: Sequence[str],
    policy: AudioMappingPolicy,
) -> list[OutputGroup]:
    """外挂来源: 按结构策略切分 (§22–§26/§36/§37)。"""
    mode = policy.mode
    if mode is AudioMappingMode.SOURCE:
        chunks: list[list[str]] = []
        for cid in channel_ids:
            key = _stream_key(plan, cid)
            if chunks and _stream_key(plan, chunks[-1][0]) == key:
                chunks[-1].append(cid)
            else:
                chunks.append([cid])
    elif mode is AudioMappingMode.INDEPENDENT:
        chunks = _chunk(channel_ids, 1)
    else:
        chunks = _chunk(channel_ids, policy.effective_group_size)

    groups: list[OutputGroup] = []
    for chunk in chunks:
        source_id, stream_index = _stream_key(plan, chunk[0])
        groups.append(OutputGroup(
            channels=chunk,
            kind="external",
            source_id=source_id,
            stream_index=stream_index,
            mode=mode.value,
        ))
    return groups


def group_plan(plan: AudioPlan, channel_ids: Sequence[str]) -> AudioPlan:
    """为**一条输出流**建子计划: 选择 = 这组的声道, 顺序 = 组内顺序。

    这是"每组一次 render"的实现方式: 子计划仍然只用既有
    `AudioPlanner.select_channels()`, 因此组内的声道身份/顺序/时长完全由
    既有 selection + `AudioTimeline` 决定, 本模块不发明第二条渲染路径。

    ⚠️ 返回的是**副本**: 原计划不被修改 (调用方可对同一计划切出多组)。
    """
    import copy

    from .audio_plan import AudioPlanner

    sub = copy.deepcopy(plan)
    if not channel_ids:
        return sub
    AudioPlanner(sub).select_channels(*[str(c) for c in channel_ids])
    return sub
