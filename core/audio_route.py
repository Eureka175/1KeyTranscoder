"""Channel Routing (v0.7.1 Phase 3A) — 纯样本搬运, **不含任何混音**。

职责:

    AudioTimeline (唯一时长/EOF 权威)
            ↓
    AudioRouter.route_spec / run_route
            ↓
    固定数量 output frames (float32, 越界/EOF = 确定性静音 0.0)

硬约束:

* **不做出任何样本级相加**: 一个输出声道恰好由一个源声道供数 (1:1 搬运)。
  任何 "N 源声道 -> 1 输出声道" 都必须在到达本模块前被 Phase 2 的
  `audio_mix_not_supported` 拒绝; 本模块再兜一道 (`audio_mix_invalid`)。
  真正的混音是 Phase 3B 的 `core/audio_mix.py`。
* **不决定** render duration / EOF / short-source policy: 全部来自
  `AudioTimeline`; 尾部短了补静音, 不循环, 不复制最后一个样本, 不写 NaN。
* **chunked**: 分块处理, 峰值内存 ≈ 一个 chunk; `chunk_frames` 不得影响
  结果 (§chunk invariance): 每块只从 reader 读它真正需要的那一段, 按
  "每 (stream) 的读取区间并集" 合并, 读取顺序与块划分无关。
* **不重复 decode**: 同一 stream 只向 reader 要数据; reader 侧对
  stream_id 做解码缓存。

offset 语义统一走 `core.audio_timeline.source_to_timeline`
(`timeline = source - offset`), 本模块**不自己解释** offset。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

import numpy as np

from .audio_models import AudioPlan
from .audio_pcm import CANONICAL_PCM_DTYPE, AudioPCMReader
from .audio_timeline import (
    AudioRenderError,
    AudioTimeline,
    ChannelTimeline,
    SyncApplication,
    resolve_timeline,
)

__all__ = [
    "AudioRouteSpec",
    "AudioRouter",
    "ChannelRoute",
    "Interval",
    "REASON_AUDIO_MIX_INVALID",
    "REASON_AUDIO_OUTPUT_INVALID",
    "REASON_AUDIO_ROUTE_OFFSET_UNKNOWN",
    "build_route_spec",
    "interval_union",
    "merge_intervals",
    "route_to_callable",
    "run_route",
]

REASON_AUDIO_OUTPUT_INVALID = "audio_output_invalid"
REASON_AUDIO_MIX_INVALID = "audio_mix_invalid"
REASON_AUDIO_ROUTE_OFFSET_UNKNOWN = "audio_route_offset_unknown"

#: (start, count) —— 以**源样本**下标表达的半开区间 [start, start+count)
Interval = tuple[int, int]


def merge_intervals(
    intervals: Iterable[Interval], gap: int = 0,
) -> list[Interval]:
    """合并重叠/相邻区间 (按键排序, 与输入顺序无关 -> 块划分无关)。"""
    items = [(int(a), int(b)) for a, b in intervals if int(b) > 0]
    if not items:
        return []
    items.sort()
    out: list[Interval] = [items[0]]
    slack = max(0, int(gap))
    for start, count in items[1:]:
        last_start, last_count = out[-1]
        if start <= last_start + last_count + slack:
            end = max(last_start + last_count, start + count)
            out[-1] = (last_start, end - last_start)
        else:
            out.append((start, count))
    return out


def interval_union(
    intervals: Iterable[Interval], gap: int = 0,
) -> list[Interval]:
    """`merge_intervals` 的别名 (语义化命名)。"""
    return merge_intervals(intervals, gap=gap)


@dataclass
class ChannelRoute:
    """**一个输出声道 = 一个源声道** (Phase 3A 不含任何混音)。

    `offset_samples` 是该源声道相对统一 timeline 的样本偏移, 语义由
    `core.audio_timeline` 唯一定义 (`timeline = source - offset`)。
    """

    output_index: int
    channel_id: str
    source_id: str
    stream_id: str
    stream_index: int
    channel_index: int
    offset_samples: int = 0
    sample_rate: int = 0
    gain: float = 1.0
    available_samples: int | None = None
    sync_status: str = "not_processed"

    @property
    def is_mixing(self) -> bool:
        """1:1 搬运永远是 False; 该属性存在只是为了把"本阶段禁止混音"写明。"""
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_index": int(self.output_index),
            "channel_id": self.channel_id,
            "source_id": self.source_id,
            "stream_id": self.stream_id,
            "stream_index": int(self.stream_index),
            "channel_index": int(self.channel_index),
            "offset_samples": int(self.offset_samples),
            "sample_rate": int(self.sample_rate),
            "sync_status": self.sync_status,
        }


@dataclass
class AudioRouteSpec:
    """AudioTimeline -> 可执行的路由规格 (JSON-compatible, 无 PCM)。

    `routes[output_index]` 顺序即输出声道顺序; 每项恰好一个源声道。
    """

    sample_rate: int
    frame_count: int
    start_sample: int = 0
    channels: list[ChannelRoute] = field(default_factory=list)
    timeline: AudioTimeline | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def output_channels(self) -> int:
        return len(self.channels)

    @property
    def is_multi_source(self) -> bool:
        """输出是否由多个来源共同供数 (路由, **不是**混音)。"""
        return len({c.source_id for c in self.channels}) > 1

    @property
    def source_ids(self) -> list[str]:
        out: list[str] = []
        for route in self.channels:
            if route.source_id not in out:
                out.append(route.source_id)
        return out

    @property
    def executable(self) -> bool:
        return not self.errors and bool(self.channels) and self.frame_count >= 0

    def channel(self, output_index: int) -> ChannelRoute | None:
        if 0 <= output_index < len(self.channels):
            return self.channels[output_index]
        return None

    def stream_ids(self) -> list[str]:
        """去重后的参与流 (按首次出现顺序)。"""
        out: list[str] = []
        for route in self.channels:
            if route.stream_id not in out:
                out.append(route.stream_id)
        return out

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "sample_rate": int(self.sample_rate),
            "frame_count": int(self.frame_count),
            "start_sample": int(self.start_sample),
            "output_channels": int(self.output_channels),
            "channels": [c.to_dict() for c in self.channels],
        }
        if self.timeline is not None:
            data["timeline"] = self.timeline.summary()
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "sample_rate": int(self.sample_rate),
            "frame_count": int(self.frame_count),
            "output_channels": int(self.output_channels),
            "routes": [
                f"out{i} <- {c.channel_id} (offset {c.offset_samples:+d})"
                for i, c in enumerate(self.channels)
            ],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def build_route_spec(timeline: AudioTimeline) -> AudioRouteSpec:
    """AudioTimeline -> AudioRouteSpec (校验 1:1, 不读 PCM)。

    `AudioTimeline` 已经是"映射 + 时长"的权威, 因此这里只做结构性检查:
    输出声道数必须与 timeline 记录的一致, 且每个输出声道只有一个源声道。
    """
    errors: list[dict[str, Any]] = []
    routes: list[ChannelRoute] = []
    ids = list(timeline.output_channel_ids)
    if len(ids) != len(timeline.channels):
        errors.append({
            "reason": REASON_AUDIO_OUTPUT_INVALID,
            "detail": (
                f"timeline output ids ({len(ids)}) do not match channel "
                f"timelines ({len(timeline.channels)})"
            ),
        })
    seen_outputs: dict[int, str] = {}
    for index, ct in enumerate(timeline.channels):
        if index in seen_outputs:
            errors.append({
                "reason": REASON_AUDIO_MIX_INVALID,
                "detail": (
                    f"output channel {index} is fed twice "
                    f"({seen_outputs[index]!r} and {ct.channel_id!r}) — "
                    "sample-level summation is Phase 3B, not 3A"
                ),
                "location": ct.channel_id,
            })
            continue
        seen_outputs[index] = ct.channel_id
        if ct.declared_samples is None and ct.actual_samples is None:
            errors.append({
                "reason": REASON_AUDIO_ROUTE_OFFSET_UNKNOWN,
                "detail": (
                    f"{ct.channel_id}: no duration/availability fact — "
                    "cannot bound the read window"
                ),
                "location": ct.channel_id,
            })
        routes.append(ChannelRoute(
            output_index=index,
            channel_id=ct.channel_id,
            source_id=ct.source_id,
            stream_id=f"{ct.source_id}:s{ct.stream_index}",
            stream_index=ct.stream_index,
            channel_index=ct.channel_index,
            offset_samples=int(ct.offset_samples),
            sample_rate=int(ct.sample_rate or timeline.sample_rate),
            available_samples=ct.effective_samples,
            sync_status=ct.sync_status,
        ))
    return AudioRouteSpec(
        sample_rate=int(timeline.sample_rate),
        frame_count=int(timeline.frame_count),
        start_sample=int(timeline.start_sample),
        channels=routes,
        timeline=timeline,
        errors=errors,
        warnings=list(timeline.warnings),
    )


class AudioRouter:
    """按 `AudioTimeline` 把多来源 PCM 路由成固定长度的输出块。

        router = AudioRouter(reader, timeline)
        for block in router.frames(chunk_frames=4096):
            ...   # block.shape == (frames, output_channels), float32

    `frames()` 产出**恰好** `timeline.frame_count` 帧, 最后一块可能更短;
    全程峰值内存 ≈ `chunk_frames × output_channels × 4B`。
    """

    def __init__(
        self,
        reader: AudioPCMReader,
        timeline: AudioTimeline,
        *,
        sync_application: SyncApplication | str | None = None,
    ) -> None:
        self.reader = reader
        self.timeline = timeline
        self.spec = build_route_spec(timeline)
        if not self.spec.executable:
            first = self.spec.errors[0] if self.spec.errors else {}
            raise AudioRenderError(
                str(first.get("reason") or REASON_AUDIO_OUTPUT_INVALID),
                str(first.get("detail") or "route spec is not executable"),
                location=first.get("location"),
            )
        # timeline 已经应用过 offset 语义; 这里只做一次一致性确认, 避免
        # "timeline 说 APPLY、router 又按 NONE 再算一遍" 的双重解释。
        if sync_application is not None:
            mode = (
                sync_application.value
                if isinstance(sync_application, SyncApplication)
                else str(sync_application)
            )
            mismatch = [
                ct.channel_id for ct in timeline.channels
                if ct.offset_samples and ct.sync_application != mode
            ]
            if mismatch:
                raise AudioRenderError(
                    REASON_AUDIO_MIX_INVALID,
                    "sync_application mismatch between timeline and router: "
                    f"{mismatch[:4]}",
                    location=mismatch[0],
                )

    # -- 读取区间规划 -----------------------------------------------------

    def _read_plan(
        self, start: int, count: int,
    ) -> dict[str, list[Interval]]:
        """输出窗口 -> 每流的源样本读取区间 (合并, 与块划分无关)。"""
        end = int(start) + int(count)
        plan: dict[str, list[Interval]] = {}
        for route in self.spec.channels:
            # window 内该声道需要的源区间 [max(start,ct.start), min(end,ct.end))
            length = route.available_samples
            win_start = max(int(start), self.timeline.start_sample)
            win_end = min(end, self.timeline.end_sample)
            if win_end <= win_start:
                continue
            src_start = win_start - int(route.offset_samples)
            src_end = win_end - int(route.offset_samples)
            if length is not None:
                src_start = max(src_start, 0)
                src_end = min(src_end, int(length))
            if src_end <= src_start:
                continue
            plan.setdefault(route.stream_id, []).append(
                (src_start, src_end - src_start)
            )
        # 同一流可能被多条 route 以不同 offset 引用 (例如同一流取两路):
        # 合并后按"每流只顺序读一遍"执行, 与块大小无关。
        return {sid: merge_intervals(iv) for sid, iv in plan.items()}

    def _read_stream(
        self,
        stream_id: str,
        intervals: Sequence[Interval],
        *,
        channel_index: int,
    ) -> tuple[dict[int, np.ndarray], int]:
        """按合并区间读取一流的一道; 返回 {源下标: 有效数据} 与命中样本数。

        块内只保留**真实有效**样本 (EOF 之后的静音不拷贝), 因此后续按源
        下标精确搬运, 不需再判断 EOF —— 结果与 chunk 划分无关。
        """
        blocks: dict[int, np.ndarray] = {}
        fetched = 0
        for start, count in intervals:
            block, valid = self.reader.read(
                stream_id,
                channel_index=channel_index,
                start=start,
                count=count,
            )
            if valid > 0:
                blocks[int(start)] = np.ascontiguousarray(block[:valid])
            fetched += int(valid)
        return blocks, fetched

    # -- 分块渲染 ---------------------------------------------------------

    def frames(
        self, *, chunk_frames: int, with_report: bool = False,
    ) -> Iterator[Any]:
        """产出 output 块 (可选附带 faithful 掩码)。

        `with_report=True` 时产出 `(block, faithful)`; `faithful` 是
        bool 数组, True = 该样本真实来自源文件, False = EOF/窗口外静音。
        掩码只用于报告/测试, 渲染路径不需要它。
        """
        size = max(1, int(chunk_frames))
        total = int(self.timeline.frame_count)
        out_channels = self.spec.output_channels
        pos = int(self.timeline.start_sample)
        end = pos + total
        while pos < end:
            take = min(size, end - pos)
            block, faithful = self._render_block(pos, take)
            yield (block, faithful) if with_report else block
            pos += take

    def _render_block(
        self, start: int, count: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        out = np.zeros((count, self.spec.output_channels), dtype=CANONICAL_PCM_DTYPE)
        faithful = np.zeros((count, self.spec.output_channels), dtype=bool)
        cache: dict[tuple[str, int], tuple[dict[int, np.ndarray], int]] = {}
        end = start + count
        for route in self.spec.channels:
            offset = int(route.offset_samples)
            length = route.available_samples
            win_start = max(int(start), self.timeline.start_sample)
            win_end = min(end, self.timeline.end_sample)
            if win_end <= win_start:
                continue
            # 输出窗口 [win_start, win_end) -> 需要的源区间
            # [win_start + off, win_end + off)  (timeline = source - offset);
            # 源样本 cursor 落在输出声道 `cursor - offset` 处。
            src_start = win_start + offset
            src_end = win_end + offset
            if length is not None:
                src_start = max(src_start, 0)
                src_end = min(src_end, int(length))
            if src_end <= src_start:
                continue
            requested = merge_intervals([(src_start, src_end - src_start)])
            key = (route.stream_id, int(route.channel_index))
            fetched = cache.get(key)
            if fetched is None:
                fetched = self._read_stream(
                    route.stream_id, requested,
                    channel_index=int(route.channel_index),
                )
                cache[key] = fetched
            blocks, _valid = fetched
            # 唯一的 offset 应用点 (整条 route 只偏移一次, 循环内不叠加,
            # 因此与 chunk 划分无关): src cursor -> out cursor - offset。
            dst_pos = src_start - offset - int(start)
            cursor = src_start
            if dst_pos < 0:
                # 该源样本落在输出窗口之前 -> trim (不复制)
                cursor += -dst_pos
                dst_pos = 0
            while cursor < src_end:
                block = _block_covering(blocks, cursor)
                if block is None:
                    break
                take = min(block.shape[0], src_end - cursor, count - dst_pos)
                if take <= 0:
                    break
                out[dst_pos: dst_pos + take, route.output_index] = block[:take]
                faithful[dst_pos: dst_pos + take, route.output_index] = True
                dst_pos += take
                cursor += take
        return out, faithful


def _block_covering(
    blocks: Mapping[int, np.ndarray], cursor: int,
) -> np.ndarray | None:
    """找到覆盖源下标 `cursor` 的已读块, 返回从 `cursor` 起的视图。

    区间已按起点排序且互不重叠, 因此"起点 ≤ cursor 的最大者"是唯一候选;
    空数据块不会入表, 缺块即意味着该处是 EOF 静音。
    """
    best_start = -1
    for start in blocks:
        if best_start < start <= cursor:
            best_start = start
    if best_start < 0:
        return None
    block = blocks[best_start]
    offset = cursor - best_start
    if offset >= block.shape[0]:
        return None
    return block[offset:]


def run_route(
    plan: AudioPlan,
    reader: AudioPCMReader,
    *,
    chunk_frames: int,
    sink: Any,
    timeline: AudioTimeline | None = None,
    availability: Mapping[tuple[str, int], Any] | None = None,
    **timeline_kwargs: Any,
) -> dict[str, Any]:
    """便捷入口: AudioPlan -> 路由 -> 逐块喂给 `sink(block)`。

    `sink` 接收形状 `(frames, output_channels)` 的 float32 数组 (**借用
    语义**: sink 必须自己拷贝/消费, 不得长期持有该数组)。
    返回统计 dict (frames / chunks / faithful_samples / timeline)。
    """
    avail = availability if availability is not None else reader.availability()
    tl = timeline if timeline is not None else resolve_timeline(
        plan, availability=avail, **timeline_kwargs
    )
    tl.refresh_from_reader(reader, plan=plan)
    router = AudioRouter(reader, tl)
    chunks = 0
    frames_out = 0
    faithful_total = 0
    for block, faithful in router.frames(
        chunk_frames=chunk_frames, with_report=True
    ):
        sink(block)
        chunks += 1
        frames_out += int(block.shape[0])
        faithful_total += int(faithful.sum())
    return {
        "frames": frames_out,
        "chunks": chunks,
        "chunk_frames": int(chunk_frames),
        "output_channels": int(tl.output_channels),
        "faithful_samples": faithful_total,
        "silence_samples": frames_out * tl.output_channels - faithful_total,
        "timeline": tl.summary(),
    }


def route_to_callable(
    reader: AudioPCMReader,
    timeline: AudioTimeline,
    *,
    chunk_frames: int,
) -> Iterator[np.ndarray]:
    """`AudioRouter.frames` 的薄包装 (只产出 block, 不带掩码)。"""
    return AudioRouter(reader, timeline).frames(chunk_frames=chunk_frames)
