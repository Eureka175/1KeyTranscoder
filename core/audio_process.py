"""Audio Processing Graph (v0.7.1 Phase 3A) — AudioPlan -> PCM -> WAV。

本模块把已有各层**串起来**, 自己不实现任何 DSP:

    AudioPlan            (Phase 2: 来源 / 选择 / 映射)
        ↓
    AudioTimeline        (core.audio_timeline: 时长 / EOF / offset 唯一权威)
        ↓
    AudioPCMReader       (core.audio_pcm: ffmpeg -> canonical float32)
        ↓
    AudioRouter          (core.audio_route: 纯样本搬运, 无混音)
        ↓
    WavExporter          (core.audio_wav: RIFF/PCM16/24/32/float32)
        ↓
    AudioOutputSpec      (声明式输出描述; Phase 4 的 MP4 集成预留)

解耦要求 (§WAV 与 Mixing 解耦):

* `Routing only -> WAV`  (Phase 3A) —— 已实现
* `Mixing -> WAV`        (Phase 3B) —— 在同一张图上换掉路由节点即可
  (见 `core.audio_mix`); **WAV exporter 不实现 mixing**。

默认生产路径**完全不变**: 只有显式调用本模块才会解码/写 WAV;
`AudioPlan = None` 的老路径仍然是 `-map 0` + `-c:a copy`, 不经过这里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .audio_models import AudioPlan
from .audio_pcm import (
    PCM_CHUNK_FRAMES_DEFAULT,
    AudioPCMReader,
)
from .audio_plan import (
    REASON_AUDIO_MIX_NOT_SUPPORTED,
    effective_mapping,
    validate_selection,
)
from .audio_mix import (
    AudioMixer,
    MixBus,
    MixStats,
    mixing_timeline,
    validate_mix_bus,
)
from .audio_route import AudioRouter, AudioRouteSpec, build_route_spec
from .audio_timeline import (
    AudioRenderError,
    AudioTimeline,
    REASON_AUDIO_TIMELINE_INVALID,
    RenderPolicy,
    resolve_timeline,
)
from .audio_wav import (
    AudioOutputSpec,
    REASON_AUDIO_OUTPUT_INVALID,
    REASON_AUDIO_WAV_WRITE_FAILED,
    WavExportSpec,
    WavExporter,
    WavFormat,
    default_wav_name,
    unique_output_path,
)

__all__ = [
    "AudioOutputSpec",
    "AudioRenderResult",
    "REASON_AUDIO_MIX_INVALID",
    "build_output_spec",
    "output_spec_for_timeline",
    "prepare_plan",
    "resolve_render_timeline",
    "run_audio_render",
    "validate_render_plan",
    "wav_export_spec",
]

REASON_AUDIO_MIX_INVALID = "audio_mix_invalid"


# ---------------------------------------------------------------------------
# 渲染路径校验
# ---------------------------------------------------------------------------


@dataclass
class AudioRenderIssue:
    reason: str
    detail: str
    severity: str = "error"
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reason": self.reason,
            "detail": self.detail,
            "severity": self.severity,
        }
        if self.location is not None:
            data["location"] = self.location
        return data


def _pcm_facing_issues(plan: AudioPlan) -> list[AudioRenderIssue]:
    """PCM 渲染路径的结构校验 (与 ffmpeg argv 规格校验**不同**)。

    关键差异 (只在这里放宽, `core/audio_plan.py` 的 `-map` 规格校验
    **保持原样**, 它的约束不被削弱):

    * `mix_mode` 非空 = 请求样本级合成。`-map` 规格无法表达 -> 仍由
      `build_audio_map_spec` 以 `audio_mix_not_supported` 拒绝;
      但 PCM 渲染路径**能**表达 -> 这里放行 (Phase 3B 实现 MixEngine
      之前, 出现 mix_mode 会以 `audio_mix_invalid` 明确拒绝执行)。
    * 同一 source channel 映射到多个输出 = 复制语义。Phase 2 拒绝; PCM
      路径同样拒绝 (保持一致, §二十一: 本阶段不复制同一源声道)。
    """
    issues: list[AudioRenderIssue] = []
    for issue in validate_selection(plan):
        issues.append(AudioRenderIssue(
            reason=issue.reason,
            detail=issue.detail,
            severity=issue.severity.value,
            location=issue.location,
        ))
    if not effective_mapping(plan):
        issues.append(AudioRenderIssue(
            reason=REASON_AUDIO_TIMELINE_INVALID,
            detail="audio plan has no effective mapping (nothing to render)",
        ))
    return issues


def validate_render_plan(plan: AudioPlan) -> list[AudioRenderIssue]:
    """PCM 渲染路径的**完整**校验 (稳定 reason code 列表, 空 = 通过)。"""
    issues = _pcm_facing_issues(plan)
    seen: set[str] = set()
    for entry in effective_mapping(plan):
        cid = str(entry.get("source_channel_id") or "")
        if cid in seen:
            issues.append(AudioRenderIssue(
                reason="audio_mapping_output_order",
                detail=(
                    f"channel {cid!r} is routed to more than one output "
                    "channel (copy semantics are not defined)"
                ),
                location=cid,
            ))
        seen.add(cid)
    return issues


# ---------------------------------------------------------------------------
# 准备 / timeline
# ---------------------------------------------------------------------------


def prepare_plan(
    plan: AudioPlan,
    *,
    ffmpeg: Path,
    work_dir: Path,
    chunk_frames: int = PCM_CHUNK_FRAMES_DEFAULT,
    reader_factory: Any = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """建 reader (带上下文管理) 并预解码全部流。

    `reader_factory(plan, ffmpeg, work_dir, chunk_frames) -> reader` 可替换
    读取后端 (默认 `AudioPCMReader`; 测试可用同接口的管道读取器验证
    "结果不依赖读取传输层")。

    返回 `(reader, issues)`; issues 非空时**调用方必须放弃渲染** ——
    时间轴/数据错误绝不允许"尽可能输出一个结果"。
    """
    issues = [i.to_dict() for i in validate_render_plan(plan)]
    build = reader_factory if callable(reader_factory) else _default_reader
    reader = build(plan, ffmpeg, work_dir, chunk_frames)
    if issues:
        return reader, issues
    try:
        reader.prepare()
    except AudioRenderError as exc:
        issues.append(exc.to_dict())
    return reader, issues


def _default_reader(
    plan: AudioPlan, ffmpeg: Path, work_dir: Path, chunk_frames: int,
) -> AudioPCMReader:
    return AudioPCMReader(
        plan, ffmpeg=ffmpeg, work_dir=work_dir, chunk_frames=chunk_frames
    )


def resolve_render_timeline(
    plan: AudioPlan,
    reader: AudioPCMReader,
    *,
    render_policy: RenderPolicy | str = RenderPolicy.UNION,
    explicit_duration_seconds: float | None = None,
    explicit_start_seconds: float | None = None,
    explicit_end_seconds: float | None = None,
) -> AudioTimeline:
    """规划 -> (解码后校正) 的 timeline 解析 (渲染路径唯一入口)。

    规划阶段用**声明** duration; 解码后用**实际样本数**重算派生窗口
    (metadata 声明错误不得决定输出长度), 全过程记录
    `audio_duration_metadata_mismatch`。
    """
    timeline = resolve_timeline(
        plan,
        availability=reader.availability(),
        render_policy=render_policy,
        explicit_duration_seconds=explicit_duration_seconds,
        explicit_start_seconds=explicit_start_seconds,
        explicit_end_seconds=explicit_end_seconds,
    )
    timeline.refresh_from_reader(reader, plan=plan)
    return timeline


def output_spec_for_timeline(
    timeline: AudioTimeline,
    *,
    path: Path | str,
    sample_format: WavFormat | str = WavFormat.PCM24,
    layout: str = "",
) -> AudioOutputSpec:
    """AudioTimeline -> AudioOutputSpec (输出不是"随便一个 wav")。"""
    fmt = WavFormat.coerce(sample_format, WavFormat.PCM24) or WavFormat.PCM24
    channel_ids = list(timeline.output_channel_ids)
    source_ids: list[str] = []
    for ct in timeline.channels:
        if ct.source_id not in source_ids:
            source_ids.append(ct.source_id)
    return AudioOutputSpec(
        path=str(path),
        kind="wav",
        sample_format=fmt,
        sample_rate=int(timeline.sample_rate),
        channel_count=int(timeline.output_channels),
        frame_count=int(timeline.frame_count),
        source_ids=source_ids,
        channel_ids=channel_ids,
        layout=layout or _layout_hint(timeline),
        metadata={
            "start_sample": int(timeline.start_sample),
            "end_sample": int(timeline.end_sample),
            "duration_mode": timeline.duration_mode.value,
            "render_policy": timeline.render_policy.value,
        },
    )


def _layout_hint(timeline: AudioTimeline) -> str:
    """输出布局提示: 只有"某条流整条原样保留"时才敢沿用源布局。"""
    ids = list(timeline.output_channel_ids)
    if not ids:
        return ""
    pairs: list[tuple[str, int]] = []
    for ct in timeline.channels:
        key = (ct.source_id, ct.stream_index)
        if not pairs or pairs[-1] != key:
            pairs.append(key)
    if len(pairs) != 1:
        return ""
    source_id, stream_index = pairs[0]
    for ct in timeline.channels:
        if ct.declared_samples is None and ct.actual_samples is None:
            return ""
    return ""


def wav_export_spec(
    output: AudioOutputSpec,
    *,
    layout: str = "",
    overwrite: bool = False,
) -> WavExportSpec:
    """AudioOutputSpec -> WavExportSpec (含"禁止同名覆盖"策略)。"""
    return WavExportSpec(
        output=output,
        frame_count=int(output.frame_count),
        channel_count=int(output.channel_count),
        sample_rate=int(output.sample_rate),
        sample_format=output.sample_format,
        layout=layout or output.layout,
        overwrite=overwrite,
    )


def build_output_spec(
    plan: AudioPlan,
    timeline: AudioTimeline,
    output_dir: Path | str,
    *,
    sample_format: WavFormat | str = WavFormat.PCM24,
    name: str | None = None,
    layout: str = "",
    overwrite: bool = False,
) -> AudioOutputSpec:
    """命名 + 规格: `<source>_<track/channel>_<mapping>.wav` (集中在此)。

    命名不散落在 processor 里; 同名冲突**不覆盖** (`-2`, `-3`, …)。
    """
    fmt = WavFormat.coerce(sample_format, WavFormat.PCM24) or WavFormat.PCM24
    channel_ids = list(timeline.output_channel_ids)
    if name is None:
        name = default_wav_name(
            source_stem=_single_source_stem(timeline),
            channel_ids=channel_ids,
            mapping=(None if plan.mapping_kind == "derived" else "map"),
        )
    target = Path(output_dir) / name
    if target.suffix.lower() != ".wav":
        target = target.with_suffix(".wav")
    if not overwrite:
        target = unique_output_path(target)
    spec = output_spec_for_timeline(
        timeline, path=target, sample_format=fmt, layout=layout
    )
    spec.metadata["mapping_kind"] = plan.mapping_kind
    spec.metadata["preserve_original"] = bool(plan.preserve_original)
    return spec


def _single_source_stem(timeline: AudioTimeline) -> str | None:
    """只有一个来源参与时用它的 id 做文件名前缀, 否则 None (用 multi)。"""
    stems: list[str] = []
    for ct in timeline.channels:
        if ct.source_id not in stems:
            stems.append(ct.source_id)
    return stems[0] if len(stems) == 1 else None


# ---------------------------------------------------------------------------
# 完整渲染
# ---------------------------------------------------------------------------


@dataclass
class AudioRenderResult:
    """一次音频渲染的完整结果 (JSON-compatible 事实, 不含 PCM)。"""

    ok: bool
    output: AudioOutputSpec | None = None
    timeline: AudioTimeline | None = None
    route_spec: AudioRouteSpec | None = None
    mix_bus: MixBus | None = None
    mix_stats: MixStats | None = None
    prepared: list[dict[str, Any]] = field(default_factory=list)
    frames: int = 0
    output_channels: int = 0
    silence_samples: int = 0
    chunks: int = 0
    decode_seconds: float = 0.0
    peak: float = 0.0
    clipped_samples: int = 0
    duration_mismatches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": bool(self.ok),
            "frames": int(self.frames),
            "output_channels": int(self.output_channels),
            "silence_samples": int(self.silence_samples),
            "chunks": int(self.chunks),
            "decode_seconds": round(float(self.decode_seconds), 4),
            "peak": float(self.peak),
            "clipped_samples": int(self.clipped_samples),
            "prepared": list(self.prepared),
        }
        if self.output is not None:
            data["output"] = self.output.to_dict()
        if self.timeline is not None:
            data["timeline"] = self.timeline.to_dict()
        if self.route_spec is not None:
            data["route_spec"] = self.route_spec.to_dict()
        if self.mix_bus is not None:
            data["mix_bus"] = self.mix_bus.to_dict()
            data["graph"] = "mixing"
        else:
            data["graph"] = "routing"
        if self.mix_stats is not None:
            data["mix_stats"] = self.mix_stats.to_dict()
        if self.duration_mismatches:
            data["duration_mismatches"] = list(self.duration_mismatches)
        if self.errors:
            data["errors"] = list(self.errors)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "graph": "mixing" if self.mix_bus is not None else "routing",
            "frames": int(self.frames),
            "output_channels": int(self.output_channels),
            "output": self.output.path if self.output else None,
            "errors": [e.get("reason") for e in self.errors],
            "warnings": list(self.warnings),
        }


def run_audio_render(
    plan: AudioPlan,
    *,
    ffmpeg: Path,
    work_dir: Path,
    output_dir: Path | None = None,
    output_path: Path | str | None = None,
    sample_format: WavFormat | str = WavFormat.PCM24,
    chunk_frames: int = PCM_CHUNK_FRAMES_DEFAULT,
    render_policy: RenderPolicy | str = RenderPolicy.UNION,
    explicit_duration_seconds: float | None = None,
    explicit_start_seconds: float | None = None,
    explicit_end_seconds: float | None = None,
    layout: str = "",
    name: str | None = None,
    overwrite: bool = False,
    on_chunk: Any = None,
    reader_factory: Any = None,
    mix_bus: Any = None,
    clip_policy: Any = None,
) -> AudioRenderResult:
    """AudioPlan -> canonical float32 -> 路由/混音 -> WAV (P3A + P3B 全链路)。

    图的选择 (同一张图, 只换节点):

    * **无混音意图** (无 `mix_buses` / `mix_mode` 为 `None`) -> `AudioRouter`
      (Phase 3A: 1 输出声道 = 1 源声道, 纯搬运);
    * **有混音意图** (`plan.mix_buses` 非空或 `mix_mode` 非空) ->
      `core.audio_mix.AudioMixer` (Phase 3B: Σ sample × gain)。

    返回 `AudioRenderResult`; **失败时 `ok=False` 且不产出文件** (校验/时间轴
    问题绝不"尽可能输出")。渲染中途失败会删除半成品。

    `on_chunk(block, index)` 可选: 逐块观察 (测试/进度用), 收到的是
    **借用语义**的数组, 不得长期持有。
    `reader_factory` 可替换 PCM 读取后端 (接口同 `AudioPCMReader`)。
    `clip_policy` 是混音超范围策略 (`detect` / `hard_clip` / `error`)。
    """
    result = AudioRenderResult(ok=False)
    reader, issues = prepare_plan(
        plan, ffmpeg=ffmpeg, work_dir=work_dir, chunk_frames=chunk_frames,
        reader_factory=reader_factory,
    )
    if issues:
        result.errors = list(issues)
        reader.close()
        return result
    try:
        result.prepared = reader.describe()
        base_timeline = resolve_render_timeline(
            plan, reader,
            render_policy=render_policy,
            explicit_duration_seconds=explicit_duration_seconds,
            explicit_start_seconds=explicit_start_seconds,
            explicit_end_seconds=explicit_end_seconds,
        )
        result.duration_mismatches = reader.duration_mismatches()
        result.warnings.extend(base_timeline.warnings)

        # ---- 图的选择: 同一张图, 只换节点 (Routing / Mixing) ----------
        mixer = None
        bus = _resolve_mix_bus(plan, mix_bus)
        if bus is not None:
            issues = validate_mix_bus(plan, bus)
            if issues:
                result.errors = list(issues)
                return result
            timeline = mixing_timeline(plan, bus, base=base_timeline)
            result.mix_bus = bus
            mixer = AudioMixer(
                reader, bus, timeline,
                source_timeline=base_timeline,
                clip_policy=clip_policy,
                output_format=sample_format,
            )
        else:
            timeline = base_timeline
        result.timeline = timeline

        if mixer is None:
            route_spec = build_route_spec(timeline)
            result.route_spec = route_spec
            if not route_spec.executable:
                result.errors = list(route_spec.errors)
                return result

        target_dir = Path(output_dir) if output_dir is not None else (
            Path(output_path).parent if output_path is not None else work_dir
        )
        if output_path is not None:
            spec = output_spec_for_timeline(
                timeline, path=output_path, sample_format=sample_format,
                layout=layout,
            )
            spec.metadata["mapping_kind"] = plan.mapping_kind
        else:
            spec = build_output_spec(
                plan, timeline, target_dir, sample_format=sample_format,
                name=name, layout=layout, overwrite=overwrite,
            )
        if spec.frame_count != timeline.frame_count or \
                spec.channel_count != timeline.output_channels or \
                spec.sample_rate != timeline.sample_rate:
            result.errors = [{
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": (
                    "output spec does not match timeline: "
                    f"spec={spec.frame_count}f/{spec.channel_count}ch/"
                    f"{spec.sample_rate}Hz vs timeline="
                    f"{timeline.frame_count}f/{timeline.output_channels}ch/"
                    f"{timeline.sample_rate}Hz"
                ),
            }]
            return result

        export = wav_export_spec(spec, layout=layout, overwrite=overwrite)
        source = mixer if mixer is not None else AudioRouter(reader, timeline)
        writer = WavExporter(export)
        try:
            index = 0
            for block in _iter_blocks(source, chunk_frames):
                writer.write(block)
                if on_chunk is not None:
                    on_chunk(block, index)
                index += 1
                result.chunks += 1
            info = writer.close()
        except BaseException:
            writer.abort()          # 失败即清理半成品 (不产出"差不多"的文件)
            raise
        if info.frame_count != timeline.frame_count:
            result.errors = [{
                "reason": REASON_AUDIO_WAV_WRITE_FAILED,
                "detail": (
                    f"wrote {info.frame_count} frames but the timeline "
                    f"requires {timeline.frame_count}"
                ),
            }]
            return result

        result.output = spec
        result.frames = int(info.frame_count)
        result.output_channels = int(info.channel_count)
        result.peak = float(info.peak or 0.0)
        result.clipped_samples = int(writer.clipped_samples)
        total = result.frames * max(1, result.output_channels)
        if mixer is not None:
            result.mix_stats = mixer.stats
            # 逐输入几何查 **base timeline** (未混音的源声道身份):
            # `mixing_timeline()` 把输出声道身份换成了 `mix0`/`mix1`,
            # 拿它去查 `camera:s0:c0` 之类必然 miss -> 恒定 total=0 ->
            # silence_samples 恒报满。routing 图下 timeline 本就是 base,
            # 因此这一处修正不影响既有路由行为。
            result.silence_samples = max(
                0, total - _count_faithful_mix(mixer, base_timeline)
            )
        else:
            result.silence_samples = max(
                0, total - _count_faithful(reader, route_spec, timeline=timeline)
            )
        result.decode_seconds = float(reader.decode_seconds)
        result.ok = True
        return result
    except AudioRenderError as exc:
        result.errors = [exc.to_dict()]
        return result
    except ValueError as exc:
        result.errors = [{
            "reason": REASON_AUDIO_WAV_WRITE_FAILED,
            "detail": str(exc),
        }]
        return result
    finally:
        reader.close()


def _iter_blocks(source: Any, chunk_frames: int) -> Any:
    """统一 `AudioRouter` / `AudioMixer` 的块产出 (两者接口一致)。

    * `AudioMixer.frames()` 产出 `(block, faithful)`;
    * `AudioRouter.frames()` 只产出 `block` (`with_report=True` 时才带掩码)。

    两种节点都只暴露"逐块 float32 输出", WAV 导出因此不需要知道自己在写
    路由结果还是混音结果 (§WAV 与 Mixing 解耦)。
    """
    for item in source.frames(chunk_frames=chunk_frames):
        if isinstance(item, tuple):
            yield item[0]
        else:
            yield item


def _count_faithful_mix(mixer: Any, timeline: AudioTimeline) -> int:
    """混音路径下"至少有一个输入真实供数"的输出样本数 (逐 sink 并集)。

    ⚠️ `timeline` **必须是 base timeline** (未混音的那张), 因为逐输入的
    身份是源声道 (`camera:s0:c0`), 而 `mixing_timeline()` 产出的声道身份
    是 `mixN` —— 传混音 timeline 会让所有 lookup miss, 恒定返回 0。
    """
    total = 0
    for sink in mixer.bus.sinks:
        spans: list[tuple[int, int]] = []
        for item in sink.inputs:
            ct = timeline.channel(item.channel_id)
            if ct is None:
                continue
            bounds = ct.available_bounds
            if bounds is None:
                continue
            lo = max(bounds[0], timeline.start_sample)
            hi = min(bounds[1], timeline.end_sample)
            if hi > lo:
                spans.append((lo, hi))
        spans.sort()
        covered = 0
        cursor: int | None = None
        for lo, hi in spans:
            if cursor is None or lo > cursor:
                covered += hi - lo
                cursor = hi
            elif hi > cursor:
                covered += hi - cursor
                cursor = hi
        total += covered
    return int(total)


def _resolve_mix_bus(plan: AudioPlan, mix_bus: Any) -> Any:
    """决定本次渲染是否走混音, 并给出 MixBus (None = 纯路由)。

    * 显式传入 `mix_bus` -> 用它 (single bus);
    * 否则 `plan.mix_buses` 非空 -> 用它 (首个 bus; 多 bus 目前不支持);
    * 否则 `plan.mix_mode` 非空 -> 全部选中声道 N->1 求和;
    * 否则 None (Phase 3A 路由路径, 行为完全不变)。
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


def _count_faithful(
    reader: AudioPCMReader,
    spec: AudioRouteSpec,
    timeline: AudioTimeline,
) -> int:
    """真实来自源文件的样本总数 (其余 = EOF/窗口外确定性静音)。"""
    total = 0
    for route in spec.channels:
        ct = timeline.channel(route.channel_id)
        if ct is None:
            continue
        bounds = ct.available_bounds
        if bounds is None:
            continue
        lo = max(bounds[0], timeline.start_sample)
        hi = min(bounds[1], timeline.end_sample)
        total += max(0, hi - lo)
    return int(total)
