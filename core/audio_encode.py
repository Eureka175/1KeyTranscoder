"""Audio Encode (Phase 4B) — PCM -> encoded audio -> **EncodedAudioOutput**。

职责边界 (硬要求)
-----------------

本模块只做一件事:

    rendered PCM  ->  audio encoder  ->  encoded audio file

它**不**负责:

* 执行图判定 (`core.audio_execution.resolve_audio_execution_path()` 是唯一权威);
* 时长 / EOF / offset (`core.audio_timeline.AudioTimeline` 是唯一权威);
* routing / mixing (`core.audio_route` / `core.audio_mix`);
* 找 sync reference / 计算 offset (`core.audio_sync` 已把结果写进 timeline);
* 视频的任何东西 (编码、容器、fps、像素格式);
* 容器策略 (由 `core.output_compose` 负责)。

与视频**完全解耦**: 本模块不 import `encoders/` / `preservation/` /
`core.batch_hw`, 不认识 video codec, API 中没有视频参数。音频编码是独立
决定, 不因视频走 copy 还是重编码而改变。

PCM 从哪来
----------

**不重新实现渲染**: 复用既有的 `core.audio_process.run_audio_render()` ——
它已经实现了"routing / mixing 同一张图只换节点", 并经 Phase 3A/3B/4A 回归
钉住。本模块只是把它的 WAV 产物交给编码器。因此:

    AudioPlan -> run_audio_render()  ->  AudioTimeline + WAV
                     (既有)                    |
                                               v
                                        encode_audio()  ->  EncodedAudioOutput

`AudioTimeline` 仍然是最终音频身份/顺序/时长的权威: 编码器**不**发明
duration, 不重排声道, 不改采样率。采样率不一致一律拒绝
(`audio_encode_sample_rate_mismatch`), **不偷偷 resample**。

采样率与声道布局
----------------

* 采样率来自 timeline; 显式指定的编码采样率若与 timeline 不同 -> 拒绝。
* 声道布局: 中间 WAV 是自描述的 (WAVE_FORMAT_EXTENSIBLE + channel mask),
  因此 ffmpeg 直接读它即可得到正确布局 —— 这正是"布局来自最终 PCM 输出"
  而不是"把输入布局复制到输出"的做法 (routing / mixing 之后输入布局可能
  已经不代表输出了)。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .audio_models import AudioPlan
from .audio_timeline import AudioTimeline
from .audio_wav import (
    AudioOutputSpec,
    WavFormat,
    default_wav_name,
    unique_output_path,
)

__all__ = [
    "AudioEncodeFormat",
    "AudioEncodeResult",
    "AudioFormatSpec",
    "EncodedAudioOutput",
    "REASON_AUDIO_ENCODE_FAILED",
    "REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED",
    "REASON_AUDIO_ENCODE_NO_OUTPUT",
    "REASON_AUDIO_ENCODE_RENDER_FAILED",
    "REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH",
    "REASON_AUDIO_ENCODE_VERIFY_FAILED",
    "RenderWindow",
    "encode_audio",
    "encode_audio_from_plan",
    "resolve_audio_format",
    "shared_render_window",
]

REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED = "audio_encode_format_unsupported"
REASON_AUDIO_ENCODE_RENDER_FAILED = "audio_encode_render_failed"
REASON_AUDIO_ENCODE_FAILED = "audio_encode_failed"
REASON_AUDIO_ENCODE_NO_OUTPUT = "audio_encode_no_output"
REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH = "audio_encode_sample_rate_mismatch"
REASON_AUDIO_ENCODE_VERIFY_FAILED = "audio_encode_verify_failed"


class AudioEncodeFormat(str, Enum):
    """本阶段支持的音频输出格式 (故意只有极少数几个)。

    ⚠️ 这不是"codec abstraction framework": 每个取值就是一条明确的编码器
    + 容器 + 文件扩展名映射, 不做能力探测、不做 fallback 链。扩充格式是
    有意的显式动作 (改这张表), 不是运行时猜测。

    * `AAC`  -> 通用有损音频编码 (MP4 家族容器, v0.8.0 起; 见下方 `_FORMATS`
      注释: 裸 ADTS 会丢掉编码器 priming, 让采样级对齐失效);
    * `OPUS` -> 低码率 Speech/WebM 场景 (v0.8.0 加入, §9 要求至少 AAC/Opus);
    * `PCM`  -> 无损 PCM, 用于"编码器必须 bit-exact"的回归;
    * `FLAC` -> 无损压缩, 用于跨平台无损归档。

    `accepts_bitrate` 把"这个格式能不能带 `-b:a`"变成结构事实: 无损格式
    (PCM/FLAC) 带 bitrate 一律拒绝 (§10), 而不是静默忽略。
    """

    AAC = "aac"
    OPUS = "opus"
    PCM = "pcm"
    FLAC = "flac"

    @property
    def encoder(self) -> str:
        return _FORMATS[self].encoder

    @property
    def container(self) -> str:
        return _FORMATS[self].container

    @property
    def suffix(self) -> str:
        return _FORMATS[self].suffix

    @property
    def lossless(self) -> bool:
        return _FORMATS[self].lossless

    @property
    def encoder_codec(self) -> str:
        """该编码器产出的 ffprobe `codec_name` (继承判定用, 不猜)。"""
        return _FORMATS[self].codec

    @property
    def accepts_bitrate(self) -> bool:
        """是否可以带 `-b:a` —— 只有有损格式可以。"""
        return not _FORMATS[self].lossless

    @classmethod
    def coerce(
        cls, value: Any, default: Any = None,
    ) -> "AudioEncodeFormat | Any":
        if value is None or value == "":
            return default
        if isinstance(value, cls):
            return value
        try:
            # 允许 "AAC" / "aac " 这类写法, 也允许常见别名
            key = str(value).strip().lower()
            key = _ALIASES.get(key, key)
            return cls(key)
        except ValueError:
            return default


@dataclass(frozen=True)
class _FormatFacts:
    encoder: str
    container: str
    suffix: str
    lossless: bool
    extension: str          # 送入容器时 ffmpeg 需要的 muxer 提示
    codec: str = ""         # 编码器产出的 ffprobe codec_name


_FORMATS: dict[AudioEncodeFormat, _FormatFacts] = {
    # ⚠️ v0.8.0: AAC 的容器从裸 ADTS 改为 MP4 家族 (`.m4a`)。
    # 原因**不是**偏好, 而是采样级正确性: ADTS 无法携带编码器 priming,
    # 于是 "PCM -> AAC(ADTS) -> 解码" 会让内容整体后移 1024 个样本 (实测),
    # 任何 alignment 结果都会被这一步吃掉。MP4 容器把 priming 写进 edit
    # list, 解码端据此裁掉, 实测内容位置与输入逐样本一致 —— 这正是
    # "compressed 显式 alignment -> decode -> PCM -> align -> re-encode"
    # 必须成立的前提。
    AudioEncodeFormat.AAC: _FormatFacts(
        encoder="aac", container="mp4", suffix=".m4a", lossless=False,
        extension="m4a", codec="aac",
    ),
    AudioEncodeFormat.OPUS: _FormatFacts(
        encoder="libopus", container="opus", suffix=".opus", lossless=False,
        extension="opus", codec="opus",
    ),
    AudioEncodeFormat.PCM: _FormatFacts(
        encoder="pcm_s16le", container="wav", suffix=".wav", lossless=True,
        extension="wav", codec="pcm_s16le",
    ),
    AudioEncodeFormat.FLAC: _FormatFacts(
        encoder="flac", container="flac", suffix=".flac", lossless=True,
        extension="flac", codec="flac",
    ),
}

#: 宽松别名 -> 枚举值 (只做拼写归一, 不做能力猜测)。
_ALIASES = {
    "m4a": "aac",
    "mp4a": "aac",
    "pcm_s16le": "pcm",
    "wav": "pcm",
    "libopus": "opus",
}


@dataclass
class AudioFormatSpec:
    """编码参数 (**最小必要集**, 不是 bitrate/quality framework)。

    只表达"用什么编码、什么采样率/声道数"; 故意不含 preset / 质量档 /
    per-scene policy / loudness / dynamics。
    """

    format: AudioEncodeFormat = AudioEncodeFormat.AAC
    sample_rate: int = 0            # 0 = 沿用 timeline (推荐)
    channel_count: int = 0          # 0 = 沿用 timeline (推荐)
    bitrate: str = ""               # 仅 AAC 有意义; 空 = 编码器默认
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def coerce(cls, value: Any) -> "AudioFormatSpec":
        if value is None:
            return cls()
        if isinstance(value, AudioFormatSpec):
            return value
        if isinstance(value, (str, AudioEncodeFormat)):
            fmt = AudioEncodeFormat.coerce(value, AudioEncodeFormat.AAC)
            return cls(format=fmt or AudioEncodeFormat.AAC)
        if isinstance(value, dict):
            return cls.from_dict(value)
        raise ValueError(
            f"{REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED}: cannot interpret "
            f"{type(value).__name__} as an audio format"
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AudioFormatSpec":
        fmt = AudioEncodeFormat.coerce(
            data.get("format") or data.get("codec"), AudioEncodeFormat.AAC
        ) or AudioEncodeFormat.AAC
        return cls(
            format=fmt,
            sample_rate=int(data.get("sample_rate") or 0),
            channel_count=int(data.get("channel_count") or 0),
            bitrate=str(data.get("bitrate") or ""),
            extra_args=[str(a) for a in (data.get("extra_args") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"format": self.format.value}
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        if self.channel_count:
            data["channel_count"] = int(self.channel_count)
        if self.bitrate:
            data["bitrate"] = self.bitrate
        if self.extra_args:
            data["extra_args"] = list(self.extra_args)
        return data

    def encoder(self) -> str:
        return self.format.encoder

    def validate(self, timeline: AudioTimeline) -> list[dict[str, Any]]:
        """与 timeline 的一致性校验 (空 = 通过)。

        **不偷偷 resample**: 显式采样率与 timeline 不一致直接拒绝。
        """
        issues: list[dict[str, Any]] = []
        if not timeline.output_channel_ids:
            issues.append({
                "reason": REASON_AUDIO_ENCODE_NO_OUTPUT,
                "detail": "timeline has no output channel (nothing to encode)",
            })
        if self.sample_rate and self.sample_rate != int(timeline.sample_rate):
            issues.append({
                "reason": REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH,
                "detail": (
                    f"requested {self.sample_rate} Hz but the render is "
                    f"{int(timeline.sample_rate)} Hz — resampling is not "
                    "implemented (refusing instead of converting silently)"
                ),
            })
        if self.channel_count and self.channel_count != int(
                timeline.output_channels):
            issues.append({
                "reason": REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED,
                "detail": (
                    f"requested {self.channel_count} channels but the render "
                    f"has {int(timeline.output_channels)} — channel count "
                    "must come from the AudioTimeline"
                ),
            })
        return issues


@dataclass
class EncodedAudioOutput:
    """一次音频编码的**产物契约** (JSON-compatible, 不含 PCM/argv)。

    这是 Audio Domain 交给 Composer 的东西。字段全部来自**实测**
    (ffprobe 读回编码文件), 不是"我们请求了什么"。
    """

    path: str
    codec: str
    container: str = ""
    sample_rate: int = 0
    channel_count: int = 0
    layout: str = ""
    #: 期望帧数 (来自 AudioTimeline; 权威)
    expected_frames: int = 0
    #: 探测到的实际帧数 (0 = 探测不到; AAC 等有编码延迟, 允许少量差异)
    probed_frames: int = 0
    duration_seconds: float = 0.0
    size_bytes: int = 0
    #: 输出声道身份 (来自 AudioTimeline.output_channel_ids —— 顺序权威)
    channel_ids: list[str] = field(default_factory=list)
    lossless: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def frame_delta(self) -> int:
        """实测帧数 - 期望帧数 (无损格式必须为 0, 有损格式允许编码延迟)。"""
        if not self.probed_frames:
            return 0
        return int(self.probed_frames) - int(self.expected_frames)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "codec": self.codec,
            "sample_rate": int(self.sample_rate),
            "channel_count": int(self.channel_count),
            "expected_frames": int(self.expected_frames),
            "duration_seconds": round(float(self.duration_seconds), 6),
            "size_bytes": int(self.size_bytes),
            "lossless": bool(self.lossless),
        }
        if self.container:
            data["container"] = self.container
        if self.layout:
            data["layout"] = self.layout
        if self.probed_frames:
            data["probed_frames"] = int(self.probed_frames)
        if self.channel_ids:
            data["channel_ids"] = list(self.channel_ids)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "rate": int(self.sample_rate),
            "channels": int(self.channel_count),
            "frames": int(self.expected_frames),
            "delta": int(self.frame_delta),
        }


@dataclass
class AudioEncodeResult:
    """编码的完整结果 (含失败详情)。"""

    ok: bool
    output: EncodedAudioOutput | None = None
    format: AudioFormatSpec = field(default_factory=AudioFormatSpec)
    timeline: AudioTimeline | None = None
    #: 中间 WAV 的声明式描述 (渲染产物; 编码成功后通常已被清理)
    rendered: AudioOutputSpec | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    encode_seconds: float = 0.0
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": bool(self.ok),
            "format": self.format.to_dict(),
            "encode_seconds": round(float(self.encode_seconds), 4),
        }
        if self.output is not None:
            data["output"] = self.output.to_dict()
        if self.rendered is not None:
            data["rendered"] = self.rendered.to_dict()
        if self.timeline is not None:
            data["timeline"] = {
                "sample_rate": int(self.timeline.sample_rate),
                "frames": int(self.timeline.frame_count),
                "channels": int(self.timeline.output_channels),
                "channel_ids": list(self.timeline.output_channel_ids),
            }
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "codec": self.format.encoder(),
            "reasons": [str(e.get("reason") or "") for e in self.errors],
        }


def encode_audio(
    wav_path: Path | str,
    output_path: Path | str,
    *,
    ffmpeg: Path,
    timeline: AudioTimeline,
    audio_format: AudioFormatSpec | str | None = None,
    ffprobe: Path | None = None,
    overwrite: bool = False,
    timeout: int = 900,
) -> AudioEncodeResult:
    """rendered WAV (canonical PCM) -> encoded audio。

    本函数**只做编码**: 输入必须是已经渲染好的 PCM WAV, 输出是编码文件。
    它不读 `AudioPlan`, 不做 routing/mixing, 不决定输出顺序, 不碰视频。

    `timeline` 只用于**校验与事实**(采样率/声道数/帧数/输出身份), 因此本
    函数无法在 timeline 之外发明 duration 或重排声道。
    """
    import time

    spec = AudioFormatSpec.coerce(audio_format)
    result = AudioEncodeResult(ok=False, format=spec, timeline=timeline)

    src = Path(wav_path)
    if not src.is_file():
        result.errors.append({
            "reason": REASON_AUDIO_ENCODE_NO_OUTPUT,
            "detail": f"rendered PCM not found: {src}",
        })
        return result

    issues = spec.validate(timeline)
    if issues:
        result.errors = issues
        return result

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        target = unique_output_path(target)

    # ⚠️ 不传 -ar / -ac / -channel_layout: 采样率、声道数与**布局**都来自
    # 那个自描述的 WAV (= AudioTimeline 的实际输出), 而不是"重新指定一次"。
    # 这样编码器没有机会改写 timeline 已经确定的事实。
    cmd = [
        str(ffmpeg), "-v", "error", "-nostdin", "-y",
        "-i", str(src),
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-c:a", spec.format.encoder,
        "-f", spec.format.container,
    ]
    if spec.bitrate:
        cmd += ["-b:a", str(spec.bitrate)]
    if spec.extra_args:
        cmd += [str(a) for a in spec.extra_args]
    cmd.append(str(target))
    result.command = cmd

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result.errors.append({
            "reason": REASON_AUDIO_ENCODE_FAILED,
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return result
    result.encode_seconds = time.monotonic() - started

    if proc.returncode != 0 or not target.is_file():
        result.errors.append({
            "reason": REASON_AUDIO_ENCODE_FAILED,
            "detail": (
                f"{spec.format.encoder} failed (rc={proc.returncode}): "
                f"{(proc.stderr or '')[-300:]}"
            ),
        })
        return result

    output = _inspect_encoded(target, spec, timeline, ffprobe, ffmpeg)

    # lossless 必须逐帧一致; 有损格式允许编码器 priming/padding 的少量差异
    # (AAC 的 1024 样本 frame 对齐), 但差异必须**被记录**而不是被忽略。
    if output.probed_frames:
        if spec.format.lossless and output.frame_delta != 0:
            result.errors.append({
                "reason": REASON_AUDIO_ENCODE_VERIFY_FAILED,
                "detail": (
                    f"lossless {spec.format.encoder} frame count "
                    f"{output.probed_frames} != timeline "
                    f"{output.expected_frames}"
                ),
            })
            return result
        if not spec.format.lossless and output.frame_delta:
            result.warnings.append(
                f"{spec.format.encoder} frame delta {output.frame_delta} "
                f"(encoder priming/padding) — timeline remains the authority"
            )
    else:
        result.warnings.append(
            "encoded frame count could not be probed; timeline remains the "
            "authority for duration"
        )

    result.output = output
    result.ok = True
    return result


def _inspect_encoded(
    path: Path,
    spec: AudioFormatSpec,
    timeline: AudioTimeline,
    ffprobe: Path | None,
    ffmpeg: Path | None = None,
) -> EncodedAudioOutput:
    """读回**实测**事实 (不假设"我们请求了什么"就是结果)。"""
    facts = _probe_audio(path, ffprobe, ffmpeg)
    return EncodedAudioOutput(
        path=str(path),
        codec=str(facts.get("codec_name") or spec.format.encoder),
        container=str(facts.get("format_name") or spec.format.container),
        sample_rate=int(facts.get("sample_rate") or timeline.sample_rate),
        channel_count=int(
            facts.get("channels") or timeline.output_channels
        ),
        layout=str(facts.get("channel_layout") or ""),
        expected_frames=int(timeline.frame_count),
        probed_frames=int(facts.get("frames") or 0),
        duration_seconds=float(facts.get("duration") or 0.0),
        size_bytes=int(path.stat().st_size),
        channel_ids=list(timeline.output_channel_ids),
        lossless=bool(spec.format.lossless),
        notes=[f"frames_source={facts.get('frames_source')}"]
        if facts.get("frames_source") else [],
    )


def _ffprobe_binary(ffprobe: Path | None, ffmpeg: Path | None = None) -> Path | None:
    """定位 ffprobe —— 复用调用方给的路径, 否则取 ffmpeg 的同目录同名工具。

    刻意**不** import 生产配置定位器: 音频域只接受显式工具路径, 不反向依赖
    应用层的参数解析 (§音视频解耦)。找不到就返回 None, 调用方把"探测不到"
    记为 warning, 而不是编造事实。
    """
    if ffprobe is not None:
        candidate = Path(ffprobe)
        return candidate if candidate.is_file() else None
    if ffmpeg is not None:
        for name in ("ffprobe.exe", "ffprobe"):
            candidate = Path(ffmpeg).with_name(name)
            if candidate.is_file():
                return candidate
    return None


def _probe_audio(
    path: Path, ffprobe: Path | None = None, ffmpeg: Path | None = None,
) -> dict[str, Any]:
    """用 ffprobe 读回编码文件的音频事实 (失败时返回 {}, 不静默编造)。

    ⚠️ `nb_frames` 的语义**随容器而变**: 裸流容器 (adts / flac / wav) 报的是
    **样本数**, 而 MP4 家族报的是 **packet 数** (AAC 一包 1024 样本)。因此
    这里以 `duration × sample_rate` 为**权威样本数**, `nb_frames` 只在
    duration 不可得时兜底, 并在两者明显不一致时记下 `frames_source` ——
    否则 "PCM -> AAC(mp4) -> 读回" 会把 49504 个样本读成 50。
    """
    import json

    probe = _ffprobe_binary(ffprobe, ffmpeg)
    if probe is None:
        return {}
    cmd = [
        str(probe), "-v", "error", "-select_streams", "a:0",
        "-show_entries",
        "stream=codec_name,channels,sample_rate,channel_layout,duration,"
        "nb_frames:format=format_name,duration",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return {}
    streams = data.get("streams") or []
    if not streams:
        return {}
    stream = streams[0]
    fmt = data.get("format") or {}

    try:
        declared = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        declared = 0
    duration = 0.0
    for candidate in (stream.get("duration"), fmt.get("duration")):
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            duration = value
            break
    try:
        rate = int(stream.get("sample_rate") or 0)
    except (TypeError, ValueError):
        rate = 0

    frames = 0
    source = "unknown"
    if duration > 0 and rate > 0:
        frames = int(round(duration * float(rate)))
        source = "duration"
        if declared and frames and abs(declared - frames) <= max(
            1, frames // 1000
        ):
            source = "duration+nb_frames"
        elif declared:
            # 容器报的是 packet 数 (MP4 家族的音频流) 或其他计数口径:
            # 明确标注, 不把它当成样本数。
            source = "duration(nb_frames=packets)"
    elif declared:
        frames = declared
        source = "nb_frames"

    return {
        "codec_name": stream.get("codec_name"),
        "channels": stream.get("channels"),
        "sample_rate": stream.get("sample_rate"),
        "channel_layout": stream.get("channel_layout"),
        "frames": frames,
        "frames_source": source,
        "nb_frames": declared,
        "duration": duration,
        "format_name": fmt.get("format_name"),
    }


def encode_audio_from_plan(
    plan: AudioPlan,
    *,
    ffmpeg: Path,
    work_dir: Path,
    output_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    audio_format: AudioFormatSpec | str | None = None,
    sample_format: WavFormat | str = WavFormat.PCM24,
    ffprobe: Path | None = None,
    chunk_frames: int = 0,
    overwrite: bool = False,
    keep_intermediate: bool = False,
    **render_kwargs: Any,
) -> AudioEncodeResult:
    """`AudioPlan` -> rendered PCM -> encoded audio (本阶段的完整音频链路)。

    只做"渲染 + 编码"两步, 每步都复用既有实现:

    1. `run_audio_render()` (既有) 负责执行图判定 + routing/mixing + 时长;
    2. `encode_audio()` (本模块) 负责 PCM -> encoded。

    它**不**决定最终容器怎么排布音轨 (那是 `core.output_compose`), 也
    **不**参与视频。

    `keep_intermediate=True` 保留中间 WAV (调试/回归用); 默认在 `finally`
    里删除, 因此**异常路径也不会留下工作区垃圾**。
    """
    import time

    from .audio_pcm import PCM_CHUNK_FRAMES_DEFAULT
    from .audio_process import run_audio_render

    spec = AudioFormatSpec.coerce(audio_format)
    result = AudioEncodeResult(ok=False, format=spec)
    started = time.monotonic()

    target_dir = Path(work_dir) if output_dir is None else Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if output_path is not None:
        target = Path(output_path)
    else:
        source_stem = None
        for source in plan.sources:
            source_stem = source.source_id
            break
        name = default_wav_name(source_stem=source_stem, channel_ids=())
        name = Path(name).with_suffix(spec.format.suffix).name
        target = target_dir / name

    stem = Path(target).stem
    wav_path = target_dir / f"{stem}.render.wav"
    if wav_path.exists() and not overwrite:
        wav_path = unique_output_path(wav_path)

    kwargs: dict[str, Any] = dict(render_kwargs)
    if chunk_frames:
        kwargs["chunk_frames"] = chunk_frames
    else:
        kwargs["chunk_frames"] = PCM_CHUNK_FRAMES_DEFAULT

    try:
        render = run_audio_render(
            plan, ffmpeg=ffmpeg, work_dir=work_dir,
            output_path=wav_path, sample_format=sample_format,
            overwrite=overwrite, **kwargs,
        )
        result.rendered = render.output
        result.timeline = render.timeline
        if not render.ok or render.timeline is None:
            result.errors = list(render.errors) or [{
                "reason": REASON_AUDIO_ENCODE_RENDER_FAILED,
                "detail": "PCM render produced no output",
            }]
            return result

        encoded = encode_audio(
            wav_path, target, ffmpeg=ffmpeg, timeline=render.timeline,
            audio_format=spec, ffprobe=ffprobe, overwrite=overwrite,
        )
        result.output = encoded.output
        result.errors = list(encoded.errors)
        result.warnings = list(render.warnings) + list(encoded.warnings)
        result.encode_seconds = float(encoded.encode_seconds)
        result.command = list(encoded.command)
        result.ok = encoded.ok
        return result
    finally:
        if not keep_intermediate:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass


def resolve_audio_format(
    value: Any, *, default: AudioEncodeFormat = AudioEncodeFormat.AAC,
) -> AudioFormatSpec:
    """宽松解析用户给的音频格式 -> `AudioFormatSpec` (未知值 -> 默认)。"""
    if value is None or value == "":
        return AudioFormatSpec(format=default)
    spec = AudioFormatSpec.coerce(value)
    return spec


@dataclass(frozen=True)
class RenderWindow:
    """整份计划的 render window (样本 + 秒)。

    `start_sample` 可以是**负数**: 一条 target 前移 480 个样本时, 它的可用
    区间就是 [-480, …], 于是整份计划的 union window 也从 -480 开始。这不是
    异常, 而是统一 timeline 的定义结果。
    """

    sample_rate: int = 0
    start_sample: int = 0
    end_sample: int = 0
    frame_count: int = 0

    @property
    def start_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(self.start_sample) / float(self.sample_rate)

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(self.frame_count) / float(self.sample_rate)

    @property
    def shifted(self) -> bool:
        return self.start_sample != 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_rate": int(self.sample_rate),
            "start_sample": int(self.start_sample),
            "end_sample": int(self.end_sample),
            "frame_count": int(self.frame_count),
            "start_seconds": round(self.start_seconds, 9),
            "duration_seconds": round(self.duration_seconds, 9),
        }


def shared_render_window(plan: AudioPlan) -> RenderWindow:
    """整份计划的 render window —— **多组渲染时必须共用它**。

    为什么必须共用: 每组单独渲染时, 各自的 union window 由**该组自己的**
    声道决定。一旦有 alignment 平移, 不同组的 window 起点就不同 (例如被
    前移 480 样本的那组从 -480 开始), 于是各组产出的 PCM 时间原点不一致,
    容器里合起来就是"没对齐"。共用同一个 window 才能让每条输出流落在同一
    时间轴上, 同时**保持各自独立的声道结构** (mapping 不变)。

    只读 `AudioTimeline` (时长权威), 不读 PCM、不写文件: 它是"问权威要一个
    数字", 不是第二套时长实现。
    """
    from .audio_timeline import resolve_timeline

    timeline = resolve_timeline(plan)
    return RenderWindow(
        sample_rate=int(timeline.sample_rate),
        start_sample=int(timeline.start_sample),
        end_sample=int(timeline.end_sample),
        frame_count=int(timeline.frame_count),
    )
