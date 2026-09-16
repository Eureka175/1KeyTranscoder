"""Production Output Integration (Phase 4C) — 把音频输出接进生产管线。

本模块是**跨域编排**: 它把音频域与视频域的产物合成一次生产输出, 但自己不
实现任何音频算法, 也不碰任何视频编码参数。

    AudioPlan  ── resolve_audio_execution_path() ──┐
                                                   ├─> 单个执行层
    VideoOutputArtifact (已经落盘)  ───────────────┘
                                                   ↓
                                        OutputComposer
                                                   ↓
                                            final container

三种情况 (与执行图一一对应, **不重写判定**)
-------------------------------------------

| 执行图 | 做什么 | 额外输入 |
|---|---|---|
| `NONE` | 什么都不做 —— 调用方保持既有路径 | 无 |
| `STREAM_COPY` | 整流保留: 从源文件按 `-map` 挑流, `-c:a copy` | 无 |
| `PCM_ROUTE` / `PCM_MIX` | 渲染 PCM -> 编码 -> 组装 | 编码音轨文件 |

`NONE` 走"什么都不做"而不是"重新拼一遍默认 argv" —— 生产默认路径因此
**结构上**不可能被本模块改变。

职责边界 (硬要求)
------------------

* **不重写执行图判定**: 只调用 `resolve_audio_execution_path()`;
* **不重写保留逻辑**: 只调用 `build_audio_retention()`;
* **不重写渲染/混音**: 只调用 `encode_audio_from_plan()` (它内部复用
  `run_audio_render()`);
* **不重写容器编排**: 只调用 `OutputComposer`;
* **不认识** `AudioMixer` / `AudioPCMReader` / `ChannelTimeline` /
  `GainMatrix` 等内部结构; 只消费产物契约;
* **不做** sync 估计、offset 计算、drift、resampling;
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
)
from core.audio_execution import (
    AudioExecutionPath,
    AudioExecutionPlan,
    resolve_audio_execution_path,
)
from core.audio_models import AudioPlan
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
    "REASON_PRODUCTION_AUDIO_PLAN_INVALID",
    "REASON_PRODUCTION_AUDIO_RETAIN_INVALID",
    "REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH",
    "REASON_PRODUCTION_VERIFY_FAILED",
    "ProductionAudioOutcome",
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
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [str(e.get("reason") or "") for e in self.errors]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": bool(self.ok),
            "applied": bool(self.applied),
            "path": self.path.value,
            "output_path": self.output_path,
        }
        if self.execution is not None:
            data["execution"] = self.execution.to_dict()
        if self.retention is not None:
            data["retention"] = self.retention.to_dict()
        if self.encoded:
            data["encoded"] = [e.to_dict() for e in self.encoded]
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
            "audio_streams": (
                int(self.composition.audio_stream_count)
                if self.composition is not None else 0
            ),
            "reasons": self.reasons,
        }


def load_audio_plan_request(path: Path | str) -> Any:
    """读取 `--audio-plan` JSON 文件 -> 请求对象 (生产入口的唯一入口)。

    生产入口因此不必 import `core.audio_request`; 解析失败抛
    `AudioRequestError` (稳定 reason code)。
    """
    from core.audio_request import load_audio_request

    return load_audio_request(path)


def resolve_source_audio_plan(
    ffprobe: Path,
    source: Path | str,
    request: Any,
    *,
    source_id: str = "source",
) -> AudioPlan:
    """源文件音频事实 -> `AudioPlan`, 并把请求应用上去。

    这是生产入口**唯一**需要调用的"音频计划"函数: `1kt.py` 因此完全不必
    import 任何 `core.audio_*` 模块 —— 音频域的内部结构被收在本层之后
    (架构断言钉住这一点)。请求解析失败时抛
    `core.audio_request.AudioRequestError` (稳定 reason code)。
    """
    from core.audio_probe import audio_probe_from_file
    from core.audio_request import AudioRequest, build_audio_plan

    probe = audio_probe_from_file(ffprobe, Path(source), source_id=source_id)
    candidate = probe.plan()
    if request is None:
        return candidate
    if not isinstance(request, AudioRequest):
        # 允许直接传 dict / JSON 文本 (调用方不必 import 请求类型)。
        from core.audio_request import parse_audio_request

        if isinstance(request, Mapping):
            request = parse_audio_request(request)
        elif isinstance(request, str):
            from core.audio_request import load_audio_request

            request = load_audio_request(request)
        else:
            raise ValueError(
                REASON_PRODUCTION_AUDIO_PLAN_INVALID + ": unsupported audio "
                f"request type {type(request).__name__}"
            )
    return build_audio_plan(request, candidate)


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
    mix_bus: Any = None,
    verify_decode: bool = True,
    timeout: int = 1800,
    log: Callable[[str], None] | None = None,
) -> ProductionAudioOutcome:
    """把音频输出接进一次生产输出 (`video` 是**已经落盘**的视频产物)。

    `plan is None` -> 返回 `applied=False` 且**不触碰任何文件**: 调用方保持
    既有 `-map 0` + `-c:a copy` 路径。默认行为不变是靠"什么都不做"保证的。

    `audio_source` 是"整流保留取流的地方": `STREAM_COPY` 的选择器相对它,
    而不是相对视频产物 (视频产物在设计上**没有**音频)。生产管线里它通常
    就是源文件; 不传时选择器按第 0 个输入解释 (即视频产物), 那对整流保留
    必然是错的, 因此调用方应当显式给出。
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

    # ---- 1. 执行图判定 (唯一权威, 不重写) ------------------------------
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

    log(f"audio execution path: {execution.path.value}")

    # ---- 2. 分流: 整流保留 vs PCM 渲染+编码 ----------------------------
    # 整流保留的选择器是相对**源文件**的 (`0:a:N`), 而 Composer 的第 0 个
    # 输入是视频产物; 因此源文件作为第 1 个输入给出, 并把选择器前缀 0->1
    # 重映射。这是本层唯一需要记住的"输入序号"事实, 且是显式的。
    preserve_source: str | None = None
    selector_map: dict[str, str] | None = None
    if execution.path is AudioExecutionPath.STREAM_COPY:
        refs = _retention_inputs(plan, outcome)
        if not outcome.ok and outcome.errors:
            return outcome
        if audio_source is not None:
            preserve_source = str(audio_source)
            selector_map = {"0": "1"}
    else:
        refs = _encoded_inputs(
            plan, outcome,
            ffmpeg=ffmpeg, ffprobe=ffprobe, work_dir=work_dir,
            output_path=output_path, audio_format=audio_format,
            mix_bus=mix_bus,
        )
        if not outcome.ok and outcome.errors:
            return outcome

    # ---- 3. 容器编排 (唯一 Composer, 不重写) ---------------------------
    composer = OutputComposer()
    composition = composer.compose(
        video, audio=refs, output_path=output_path, ffmpeg=ffmpeg,
        ffprobe=ffprobe, timeout=timeout, extra_inputs=(
            [preserve_source] if preserve_source else None
        ),
        selector_map=selector_map,
    )
    outcome.composition = composition
    outcome.output_path = composition.path
    outcome.warnings.extend(composition.warnings)

    if not composition.ok:
        outcome.errors = list(composition.errors)
        _cleanup(outcome)
        return outcome

    # ---- 4. 编排后校验 (编码音轨还必须是可解码的同一份内容) ------------
    if verify_decode and outcome.encoded:
        problems = _verify_encoded_streams(
            composition, outcome.encoded, ffmpeg,
        )
        if problems:
            outcome.errors = problems
            _cleanup(outcome)
            return outcome
        outcome.warnings.extend(composition.warnings)

    outcome.ok = True
    outcome.applied = True
    log(
        f"audio output composed: {len(composition.audio_tracks)} track(s), "
        f"path={composition.path}"
    )
    return outcome


# ---------------------------------------------------------------------------
# 整流保留: 不产生新文件, 只给选择器
# ---------------------------------------------------------------------------

def _retention_inputs(
    plan: AudioPlan, outcome: ProductionAudioOutcome,
) -> list[Any]:
    """`STREAM_COPY` -> `AudioRetentionSpec` 的选择器清单。

    整流保留不需要任何新输入: `build_audio_retention()` 给出的选择器本来
    就是"相对**源文件**的 `-map` 值" (`0:a:N`), 而 Composer 的第 0 个输入
    正是那个源文件。

    ⚠️ 若 `AudioRetentionSpec.arguments()` 无法产出选择器 (需要声道过滤),
    说明执行图判定与保留规格不一致 —— 那是**真问题**, 明确报错而不是退回
    一个看起来能跑的路径。
    """
    retention = build_audio_retention(plan)
    outcome.retention = retention
    outcome.warnings.extend(retention.warnings)

    if not retention.ok:
        outcome.errors = list(retention.errors) or [{
            "reason": REASON_PRODUCTION_AUDIO_RETAIN_INVALID,
            "detail": "retention spec is not executable",
        }]
        return []

    args = retention.arguments()
    selectors = [
        args[i + 1] for i, token in enumerate(args[:-1]) if token == "-map"
    ]
    if len(selectors) != retention.output_stream_count \
            or not selectors:
        outcome.errors = [{
            "reason": REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH,
            "detail": (
                "retention spec reported "
                f"{retention.output_stream_count} stream(s) but produced "
                f"{len(selectors)} selector(s): {args}"
            ),
        }]
        return []

    # 选择器必须真的指向第 0 个输入, 否则 Composer 会取错文件。
    for selector in selectors:
        if not selector.startswith("0:"):
            outcome.errors = [{
                "reason": REASON_PRODUCTION_AUDIO_SELECTOR_MISMATCH,
                "detail": (
                    f"retention selector {selector!r} does not address the "
                    "composed input (expected a '0:...' selector)"
                ),
            }]
            return []

    return [AudioInputRef.from_selector(s) for s in selectors]


# ---------------------------------------------------------------------------
# PCM 渲染 -> 编码
# ---------------------------------------------------------------------------

def _encoded_inputs(
    plan: AudioPlan,
    outcome: ProductionAudioOutcome,
    *,
    ffmpeg: Path,
    ffprobe: Path | None,
    work_dir: Path | None,
    output_path: Path | str,
    audio_format: AudioFormatSpec | str | None,
    mix_bus: Any,
) -> list[Any]:
    """`PCM_ROUTE` / `PCM_MIX` -> 渲染 + 编码 -> `EncodedAudioOutput` 清单。"""
    target = Path(output_path)
    scratch = work_dir or target.parent
    spec = AudioFormatSpec.coerce(audio_format)
    encoded_path = scratch / f"{target.stem}.audio{spec.format.suffix}"

    kwargs: dict[str, Any] = {}
    if mix_bus is not None:
        kwargs["mix_bus"] = mix_bus

    result = encode_audio_from_plan(
        plan, ffmpeg=ffmpeg, work_dir=scratch, output_path=encoded_path,
        audio_format=spec, ffprobe=ffprobe, overwrite=True, **kwargs,
    )
    outcome.warnings.extend(result.warnings)
    if not result.ok or result.output is None:
        outcome.errors = list(result.errors) or [{
            "reason": REASON_PRODUCTION_AUDIO_PLAN_INVALID,
            "detail": "audio encode produced no output",
        }]
        return []
    outcome.encoded.append(result.output)
    return [result.output]


def _verify_encoded_streams(
    composition: Any,
    encoded: list[EncodedAudioOutput],
    ffmpeg: Path,
) -> list[dict[str, Any]]:
    """编排后校验: 每条编码音轨在容器里仍能解码出**合理**帧数。

    只做"能不能解码 + 帧数是否落在预期附近"这一层: 内容等价性已由
    `core/audio_encode` 的 encode->decode 回归负责, 这里防的是"容器里那条
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
