"""Audio Execution Path (Phase 4A) — "该走哪张音频图?" 的**唯一**判定。

解决的问题
----------

在 v0.7.1 里, "走路由还是走混音"这个决定藏在 `core.audio_process.
run_audio_render()` 内部 (`_resolve_mix_bus()` + `mixer is None` 分支)。
WAV/PCM 渲染能用, 但任何要接入**最终 MP4 输出**的代码都得把这套判断抄一遍
—— 那就等于出现两套图选择逻辑, 迟早分叉。

本模块把该判定提出来成为显式、可测试、可复用的概念:

    AudioPlan
        ↓
    resolve_audio_execution_path()
        ↓
    NONE / STREAM_COPY / PCM_ROUTE / PCM_MIX

边界 (硬要求)
-------------

* **不读 PCM**, **不解码**, **不写文件**, **不拼 ffmpeg argv**;
  纯函数, 只消费 `AudioPlan` 与可选的 `MixBus`;
* **不复制** `core.audio_process` 的逻辑: 混音意图的判定 (`mix_intent_bus`)
  与结构校验 (`validate_render_plan`) 各自只有一份实现, 由双方共用;
* **与视频完全解耦**: 本模块不认识编码器、容器、`-c:v` 或任何视频概念。
  音频的选择与保留是独立决定, 不因视频走 copy 还是重编码而改变;
* `AudioPlan = None` 表示"没有音频计划" = `NONE`(生产默认 `-map 0` +
  `-c:a copy`), 本模块**不**为它发明任何隐式默认值。

`STREAM_COPY` 与 `PCM_ROUTE` 的区别
----------------------------------

* `STREAM_COPY`: 输出声道恰为若干条**完整源流**的自然顺序 —— 可以用
  `-map` + `-c:a copy` 完成, 不需要 PCM;
* `PCM_ROUTE`: 输出是源声道的**子集或重排** —— `-map` 表达不了, 必须
  解码到 PCM 再按声道搬运 (`core.audio_route`);
* `PCM_MIX`: 需要样本级合成 (`Σ sample × gain`), 走 `core.audio_mix`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .audio_models import AudioPlan
from .audio_plan import effective_mapping

__all__ = [
    "AudioExecutionPath",
    "AudioExecutionPlan",
    "mix_intent_bus",
    "resolve_audio_execution_path",
]


class AudioExecutionPath(str, Enum):
    """本次音频输出要走哪张图。

    取值是**分类**, 不是 ffmpeg 命令:

    * `NONE`        —— 没有音频计划 / 没有选中任何声道:
                       生产默认路径 (`-map 0` + `-c:a copy`) 原样保留;
    * `STREAM_COPY` —— 输出 = 若干条完整源流, 按输出顺序排列:
                       `-map` + `-c:a copy` 即可, 无需 PCM;
    * `PCM_ROUTE`   —— 输出 = 源声道的子集 / 重排:
                       必须解码为 PCM 后按声道搬运;
    * `PCM_MIX`     —— 输出需要样本级合成: 走 mixer。
    """

    NONE = "none"
    STREAM_COPY = "stream_copy"
    PCM_ROUTE = "pcm_route"
    PCM_MIX = "pcm_mix"

    @property
    def uses_pcm(self) -> bool:
        """是否需要解码到 PCM (即是否要进入 PCM processing graph)。"""
        return self in (
            AudioExecutionPath.PCM_ROUTE, AudioExecutionPath.PCM_MIX
        )

    @property
    def is_mixing(self) -> bool:
        return self is AudioExecutionPath.PCM_MIX

    @property
    def is_stream_copy(self) -> bool:
        return self is AudioExecutionPath.STREAM_COPY


@dataclass
class AudioExecutionPlan:
    """图选择的**完整结论** (JSON-compatible, 不含 PCM / 不含 argv)。"""

    path: AudioExecutionPath
    #: 结构校验问题 (空 = 通过); 元素形状同 `AudioRenderIssue.to_dict()`。
    issues: list[dict[str, Any]] = field(default_factory=list)
    #: 实际会生效的混音 bus (`PCM_MIX` 时非 None)。
    mix_bus: Any = None
    #: 输出声道数 (来自有效映射; `NONE` 时为 0)。
    channel_count: int = 0
    #: 输出会分成几个音频流 (输出单元数); `STREAM_COPY` 时即可 `-map` 的数量。
    stream_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """图选择是否可执行 (有问题时**不代表**应该"尽力输出")。"""
        return not self.issues

    @property
    def uses_pcm(self) -> bool:
        return self.path.uses_pcm

    @property
    def is_mixing(self) -> bool:
        return self.path.is_mixing

    @property
    def is_stream_copy(self) -> bool:
        return self.path.is_stream_copy

    @property
    def needs_mux(self) -> bool:
        """是否需要在最终容器里重新安排音轨 (即选择性保留)。"""
        return self.path is not AudioExecutionPath.NONE

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path.value,
            "ok": bool(self.ok),
            "channel_count": int(self.channel_count),
            "stream_count": int(self.stream_count),
            "uses_pcm": bool(self.uses_pcm),
        }
        if self.mix_bus is not None:
            data["mix_bus"] = (
                self.mix_bus.to_dict()
                if hasattr(self.mix_bus, "to_dict") else True
            )
        if self.issues:
            data["issues"] = [dict(i) for i in self.issues]
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path.value,
            "ok": bool(self.ok),
            "channels": int(self.channel_count),
            "streams": int(self.stream_count),
            "reasons": [str(i.get("reason") or "") for i in self.issues],
        }


# ---------------------------------------------------------------------------
# 混音意图: 与 core.audio_process._resolve_mix_bus 的**优先级完全一致**
# ---------------------------------------------------------------------------

def mix_intent_bus(plan: AudioPlan, mix_bus: Any = None) -> Any:
    """决定本次渲染是否走混音, 并给出 MixBus (None = 纯路由)。

    * 显式传入 `mix_bus` -> 用它 (single bus; dict/Mapping 会被规范化为
      `MixBus`);
    * 否则 `plan.mix_buses` 非空 -> 用它 (首个 bus; 多 bus 目前不支持);
    * 否则 `plan.mix_mode` 非空 -> 全部选中声道 N->1 求和;
    * 否则 None (Phase 3A 路由路径, 行为完全不变)。

    ⚠️ 本函数是**唯一实现**: `core.audio_process.run_audio_render()` 直接
    导入它, 不再保留私有副本 —— 否则"抽离出来的判定"和"实际跑的判定"
    会立刻分叉。
    """
    from .audio_mix import build_mix_buses, mix_buses_of

    if mix_bus is not None:
        from .audio_mix import MixBus as _MixBus

        return mix_bus if isinstance(mix_bus, _MixBus) else _MixBus.from_dict(
            mix_bus if isinstance(mix_bus, Mapping) else {}
        )
    buses = mix_buses_of(plan)
    if buses:
        return buses[0]
    if not plan.mix_mode:
        return None
    mode = str(plan.mix_mode)
    derived = build_mix_buses(plan, mix_mode=mode, collapse=True)
    if len(derived) > 1:
        derived[0].notes.append(
            "only the first mix bus is rendered in this phase"
        )
    return derived[0] if derived else None


def _applied_offsets(plan: AudioPlan) -> dict[str, float]:
    """输出声道 -> **已应用**的 sync offset (样本)。0 = 不需要平移。

    只读 `effective_mapping()` 上已经存在的身份+offset (它们是
    `AudioChannel.sync` 的投影), 因此本模块依然不认识 timeline/mixer。
    """
    out: dict[str, float] = {}
    for entry in effective_mapping(plan):
        cid = str(entry.get("source_channel_id") or "")
        if not cid:
            continue
        raw = entry.get("sync_offset_samples")
        try:
            value = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            value = 0.0
        out[cid] = value
    return out


def _shifted_channels(
    plan: AudioPlan, channel_ids: Sequence[str],
) -> list[str]:
    """带非零已应用 offset 的输出声道 (stream copy 表达不了它们)。"""
    offsets = _applied_offsets(plan)
    return [
        str(cid) for cid in channel_ids
        if offsets.get(str(cid), 0.0) != 0.0
    ]


def _stream_copyable(plan: AudioPlan, channel_ids: Sequence[str]) -> bool:
    """输出是否恰为"若干条完整源流的自然顺序"(可用 `-map` + copy)。

    判据比 `AudioMapSpec.strategy` 更严格, 因为这里回答的是**容器级**
    问题: `-map <in>:a:<pos>` 必然带出该流的**全部**声道, 且顺序固定为
    源顺序。因此仅当

    * 每条源流只贡献**一个连续输出段**, 且
    * 该段恰为该流的全部声道, 且顺序与源一致 (`0,1,…,N-1`), 且
    * **没有任何参与声道带已应用的非零 sync offset**

    时才是真正的 stream copy。两条 mono 流互换顺序仍满足 (每条流各自
    完整且连续); 而"4 声道流里第 2 声道与第 1 声道互换"**不**满足 ——
    那不是 `-map` 能表达的, 必须走 PCM 路由。

    最后一条 (v0.8.0) 同样不是策略而是事实: `-map` 只能整条流搬运, 而
    alignment 是**样本级**平移 —— 已经算出非零 offset 的声道必须重新取样,
    "照抄"等于把对齐结果默默丢掉。
    """
    ids = list(channel_ids)
    if not ids:
        return False

    # 逐输出位置记录 (stream_id, channel_index)
    sequence: list[tuple[str, int]] = []
    total_of: dict[str, int] = {}
    for cid in ids:
        channel = plan.channel(cid)
        if channel is None:
            return False
        sequence.append((channel.stream_id, int(channel.channel_index)))
        total_of[channel.stream_id] = int(channel.channel_count)

    # 同一条流的所有出现必须在输出里**连续**, 且顺序 = 源顺序, 且完整
    runs: list[tuple[str, list[int]]] = []
    for stream_id, index in sequence:
        if runs and runs[-1][0] == stream_id:
            runs[-1][1].append(index)
        else:
            runs.append((stream_id, [index]))
    seen: set[str] = set()
    for stream_id, indices in runs:
        if stream_id in seen:
            return False                   # 同一条流被拆成多段 -> 不连续
        seen.add(stream_id)
        total = total_of.get(stream_id, 0)
        if total <= 0:
            return False
        if indices != list(range(total)):
            return False                   # 子集 / 重排 / 重复
    return not _shifted_channels(plan, ids)


def resolve_audio_execution_path(
    plan: AudioPlan | None,
    *,
    mix_bus: Any = None,
) -> AudioExecutionPlan:
    """`AudioPlan` -> `AudioExecutionPlan` (图选择的唯一入口)。

    判定顺序 (与既有实现等价, 只是被提出来成为显式概念):

    1. 没有计划 / 没有选中任何声道        -> `NONE`;
    2. 有混音意图 (显式 bus / `mix_buses` / `mix_mode`) 且 bus 合法
                                          -> `PCM_MIX`;
    3. 输出恰为完整源流的自然顺序          -> `STREAM_COPY`;
    4. 其余 (子集 / 重排)                  -> `PCM_ROUTE`;
    5. 结构校验不通过                      -> `path` 取上述判定值但
       `issues` 非空 (`ok=False`), 调用方**必须放弃**而不是"尽力输出"。

    `NONE` 是"生产默认路径不变"的**显式**表达: 不进入任何 PCM 图,
    `-map 0` + `-c:a copy` 照旧。本函数不会为 None 计划推断任何默认值
    (不假设"第一条流"、"第一个来源").
    """
    if plan is None:
        return AudioExecutionPlan(
            path=AudioExecutionPath.NONE,
            notes=["no audio plan: production default (-map 0 + -c:a copy)"],
        )

    mapping = effective_mapping(plan)
    if not mapping:
        # 结构问题交给校验器给出具体 reason; 这里只保证不会误判成 copy。
        from .audio_process import validate_render_plan

        return AudioExecutionPlan(
            path=AudioExecutionPath.NONE,
            issues=[i.to_dict() for i in validate_render_plan(plan)],
            notes=["no effective mapping: nothing to retain"],
        )

    bus = mix_intent_bus(plan, mix_bus)
    if bus is not None:
        from .audio_mix import validate_mix_bus

        issues = [dict(i) for i in validate_mix_bus(plan, bus)]
        return AudioExecutionPlan(
            path=AudioExecutionPath.PCM_MIX,
            issues=issues,
            mix_bus=bus,
            channel_count=len(mapping),
            stream_count=1,
            notes=[] if issues else ["mixing graph (sample-level summation)"],
        )

    from .audio_process import validate_render_plan

    issues = [i.to_dict() for i in validate_render_plan(plan)]
    channel_ids = [
        str(e.get("source_channel_id") or "") for e in mapping
    ]
    copyable = _stream_copyable(plan, channel_ids)
    path = (
        AudioExecutionPath.STREAM_COPY if copyable
        else AudioExecutionPath.PCM_ROUTE
    )
    if issues:
        # 校验失败时仍然报告"本来会选哪张图", 便于诊断; `ok=False` 才是结论,
        # 调用方必须放弃而不是"尽力输出"。
        return AudioExecutionPlan(
            path=path, issues=issues, channel_count=len(mapping),
            stream_count=_stream_group_count(plan),
            notes=["invalid plan: path is reported for diagnosis only"],
        )

    shifted = _shifted_channels(plan, channel_ids)
    if path is AudioExecutionPath.STREAM_COPY:
        notes = ["output is complete source streams: -map + -c:a copy"]
    elif shifted:
        # 不是"选了什么"而是"算出了什么": 已应用的 offset 必须重新取样,
        # 因此这张图不再只是结构问题。
        notes = [
            "applied sync offset on "
            f"{len(shifted)} channel(s) ({shifted[:4]}"
            f"{'…' if len(shifted) > 4 else ''}): -map cannot express a "
            "sample-level shift, PCM routing required"
        ]
    else:
        notes = ["output selects/reorders channels: PCM routing required"]

    return AudioExecutionPlan(
        path=path,
        channel_count=len(mapping),
        stream_count=_stream_group_count(plan),
        notes=notes,
    )


def _stream_group_count(plan: AudioPlan) -> int:
    """输出会分成几个音频流 (输出单元数)。

    是**报告字段**, 不参与判定; `output_tracks()` 对非法计划也安全返回。
    """
    from .audio_plan import output_tracks

    return len(output_tracks(plan))
