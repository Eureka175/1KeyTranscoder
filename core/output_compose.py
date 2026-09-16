"""Output Compose (Phase 4B) — AudioOutput + VideoOutput -> final container。

定位
----

本模块**既不属于音频域, 也不属于视频域**: 它是两个域之间的**单向协调层**。

    Audio Domain                     Video Domain
    core.audio_encode       ─┐      ┌─  video artifact
    EncodedAudioOutput       │      │   (VideoOutputArtifact)
                             ▼      ▼
                        OutputComposer            <- 本模块
                             ↓
                        final container

依赖方向严格单向:

    Composer  ->  AudioOutput contract
    Composer  ->  VideoOutput contract
    Composer  ->  ffmpeg (进程)

反过来一律禁止, 由回归以 AST 断言钉住:

* 本模块不 import `core.audio_plan` / `core.audio_timeline` /
  `core.audio_mix` / `core.audio_route` / `core.audio_process` /
  `core.audio_execution` —— 它不认识 plan/mixer/PCM, 只认识"一个已经编码好
  的音频文件"这个**契约**;
* 音频域不 import 本模块, 视频域也不 import 本模块;
* 本模块不 import `encoders/` / `preservation/` / `core.batch_hw` ——
  视频产物是**调用方给的一个路径**, 本模块不参与视频编码决策。

它负责什么
----------

容器层面的编排: stream mapping / stream ordering / container output /
metadata policy。**只有**这些。

它不负责什么
------------

音频算法 (routing / mixing / 编码)、视频编码算法、执行图判定
(`core.audio_execution` 仍是唯一权威)、时长与 offset
(`core.audio_timeline` 仍是唯一权威)、sync reference。本模块不重算这些,
也不需要知道它们。

音频/视频"契约"的形状
---------------------

两个产物都用同一套最小表达: **一个已经落盘的文件 + 它的容器身份**。

* 自描述文件 (encoded audio / MP4 / MOV / MKV…) -> `container` 非空,
  追加 `-i <path>` 后整体作为**一个输入**引用;
* 本来没有容器的裸流 -> `container` 为空, 此时必须给出 `stream` 定位,
  由容器层按 `path#stream` 附加轨道。

因此不存在"巨型 ffmpeg 命令生成器": 每个域各自产出自己的产物, 本模块只把
两份产物拼成一条命令。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .audio_encode import EncodedAudioOutput, _ffprobe_binary

__all__ = [
    "REASON_COMPOSE_AUDIO_MISSING",
    "REASON_COMPOSE_FAILED",
    "REASON_COMPOSE_NO_VIDEO",
    "REASON_COMPOSE_VERIFY_FAILED",
    "AudioInputRef",
    "OutputComposer",
    "OutputCompositionResult",
    "VideoOutputArtifact",
    "probe_container",
]

REASON_COMPOSE_NO_VIDEO = "output_compose_no_video"
REASON_COMPOSE_AUDIO_MISSING = "output_compose_audio_missing"
REASON_COMPOSE_FAILED = "output_compose_failed"
REASON_COMPOSE_VERIFY_FAILED = "output_compose_verify_failed"


@dataclass(frozen=True)
class VideoOutputArtifact:
    """视频域交给 Composer 的产物契约 (path + 是否自描述容器)。

    视频域 (x265 / SVT-AV1 / NVEncC / QSVEncC / GPAC 重建 / 未来任何后端)
    各自产出**已经编码好或已经复制好**的视频文件; 本模块只消费它。
    **不包含**任何编码参数 —— Composer 不替视频域做决定。
    """

    path: str
    container: str = ""          # 非空 = 自描述容器 (整条 -i 输入)
    stream: str = "v:0"          # container 为空时的流说明符
    label: str = "video"

    @property
    def is_self_describing(self) -> bool:
        return bool(self.container)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "container": self.container,
            "stream": self.stream,
            "self_describing": self.is_self_describing,
        }


@dataclass(frozen=True)
class AudioInputRef:
    """Composer 的**音频输入契约** (两种形态的统一表达)。

    1. **已编码音轨** —— `EncodedAudioOutput` (自描述文件, 各占一个 `-i`);
    2. **既有输入里的音频流** —— 一个 `-map` 选择器字符串 (例如
       `"0:a:0"`), 指向某个已经给出的输入 (默认第 0 个, 即视频输入)。

    形态 2 是给 `AudioRetentionSpec` 用的: "整流 stream copy 保留" 不需要
    任何新输入, 只是从源文件里挑流。两形态都**只**描述"取哪条音轨",
    不携带编码参数。
    """

    selector: str
    input_index: int | None = None
    #: 逐轨追溯信息 (报告用, 不参与 argv)
    kind: str = "selector"
    codec: str = ""
    sample_rate: int = 0
    channel_count: int = 0
    channel_ids: tuple[str, ...] = ()
    expected_frames: int = 0
    source_path: str = ""

    @classmethod
    def from_encoded(
        cls, track: "EncodedAudioOutput", input_index: int,
    ) -> "AudioInputRef":
        """已编码音轨 -> 契约 (该音轨自成一个输入)。"""
        if not track.container:
            # 裸流 (无容器): 只能按流说明符引用, 明确不支持, 不猜。
            raise ValueError(
                "output_compose_audio_missing: encoded audio without a "
                f"container cannot be addressed: {track.path!r}"
            )
        return cls(
            selector=f"{int(input_index)}:a:0",
            input_index=int(input_index),
            kind="encoded",
            codec=str(track.codec or ""),
            sample_rate=int(track.sample_rate),
            channel_count=int(track.channel_count),
            channel_ids=tuple(str(c) for c in track.channel_ids),
            expected_frames=int(track.expected_frames),
            source_path=str(track.path),
        )

    @classmethod
    def from_selector(
        cls, selector: str, *, input_index: int | None = None,
    ) -> "AudioInputRef":
        """既有输入里的流 -> 契约 (不新增 `-i`)。"""
        text = str(selector).strip()
        if not text:
            raise ValueError(
                "output_compose_audio_missing: empty audio map selector"
            )
        return cls(
            selector=text,
            input_index=(None if input_index is None else int(input_index)),
            kind="selector",
        )

    @property
    def adds_input(self) -> bool:
        """该音轨是否需要一个**新的** `-i` 输入。"""
        return self.kind == "encoded"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "selector": self.selector,
            "kind": self.kind,
        }
        if self.input_index is not None:
            data["input_index"] = int(self.input_index)
        if self.codec:
            data["codec"] = self.codec
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        if self.channel_count:
            data["channel_count"] = int(self.channel_count)
        if self.channel_ids:
            data["channel_ids"] = list(self.channel_ids)
        if self.expected_frames:
            data["expected_frames"] = int(self.expected_frames)
        if self.source_path:
            data["source_path"] = self.source_path
        return data


def _remap_selector(selector: str, mapping: Mapping[str, str]) -> str:
    """把选择器的**输入前缀**重映射 (`"0:a:2"` + `{"0": "1"}` -> `"1:a:2"`)。

    只动前缀, 不动流说明符 —— 本层不解释流语义。
    """
    text = str(selector)
    if ":" not in text:
        return text
    head, rest = text.split(":", 1)
    replacement = mapping.get(head)
    return text if replacement is None else f"{replacement}:{rest}"


def _coerce_audio_input(value: Any) -> AudioInputRef:
    """宽松接受多种形态 -> `AudioInputRef` (单一内部表达)。"""
    if isinstance(value, AudioInputRef):
        return value
    if isinstance(value, str):
        return AudioInputRef.from_selector(value)
    if isinstance(value, EncodedAudioOutput):
        # 输入序号由调用方在 compose() 里统一分配; 这里只占位。
        return AudioInputRef.from_encoded(value, -1)
    raise ValueError(
        "output_compose_audio_missing: unsupported audio input "
        f"{type(value).__name__}; expected EncodedAudioOutput, selector "
        "string or AudioInputRef"
    )


@dataclass
class OutputCompositionResult:
    """最终容器编排的结果 (JSON-compatible)。"""

    ok: bool
    path: str = ""
    command: list[str] = field(default_factory=list)
    video: VideoOutputArtifact | None = None
    #: 实际写进容器的音频轨道 (顺序 = 容器里的顺序)
    audio_tracks: list[dict[str, Any]] = field(default_factory=list)
    #: ffprobe 读回的整体事实 (streams, 顺序, 时长)
    streams: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0
    size_bytes: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    compose_seconds: float = 0.0

    @property
    def audio_stream_count(self) -> int:
        return len(self.audio_tracks)

    def video_stream(self) -> dict[str, Any] | None:
        return next(
            (s for s in self.streams if s.get("codec_type") == "video"), None
        )

    def audio_streams(self) -> list[dict[str, Any]]:
        return [s for s in self.streams if s.get("codec_type") == "audio"]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": bool(self.ok),
            "path": self.path,
            "compose_seconds": round(float(self.compose_seconds), 4),
            "audio_streams": int(self.audio_stream_count),
            "duration_seconds": round(float(self.duration_seconds), 6),
            "size_bytes": int(self.size_bytes),
        }
        if self.video is not None:
            data["video"] = self.video.to_dict()
        if self.audio_tracks:
            data["audio_tracks"] = [dict(t) for t in self.audio_tracks]
        if self.streams:
            data["streams"] = [dict(s) for s in self.streams]
        if self.errors:
            data["errors"] = [dict(e) for e in self.errors]
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "audio_streams": int(self.audio_stream_count),
            "reasons": [str(e.get("reason") or "") for e in self.errors],
        }


class OutputComposer:
    """把视频产物与一个或多个音频产物编排进最终容器 (**不做任何编码算法**)。

    视频一律 stream copy (`-c:v copy`) —— 视频怎么来是视频域的事, 本模块
    只把它搬进容器。音频一律 stream copy (`-c:a copy`) —— 音频怎么编码是
    音频域的事, 本模块只把它搬进容器。
    """

    def compose(
        self,
        video: VideoOutputArtifact,
        *,
        audio: list[Any] | None = None,
        output_path: Path | str,
        ffmpeg: Path,
        ffprobe: Path | None = None,
        map_metadata_from: int | None = 0,
        extra_args: list[str] | None = None,
        selector_map: Mapping[str, str] | None = None,
        extra_inputs: list[str] | None = None,
        overwrite: bool = True,
        timeout: int = 1800,
    ) -> OutputCompositionResult:
        """video + audio -> 最终容器。

        `audio` 接受三种形态 (内部统一成 `AudioInputRef`):

        * `EncodedAudioOutput`   —— 已编码音轨, 各占一个新的 `-i`;
        * `str`                  —— 既有输入里的 `-map` 选择器字符串;
        * `AudioInputRef`        —— 上面的显式形式。

        `extra_inputs` 是"额外 **只用于取音频** 的输入文件" (例如整流保留
        时的源文件)。它们排在视频与已编码音轨之后, 序号随分配顺序; 只被
        `selector_map` 重映射后的选择器引用, 不会被本层自行映射。

        `selector_map` 把选择器的**输入前缀**重映射到实际输入序号。
        典型用法: `AudioRetentionSpec` 的选择器是相对**源文件**的
        (`0:a:N`), 而这里第 0 个输入是视频产物、源文件是第 1 个输入,
        于是传 `{"0": "1"}`。本层只重映射前缀, 不解释流说明符。

        `map_metadata_from=i` 表示从第 i 个输入继承容器级 metadata
        (默认第一个输入); `None` = 不映射任何 metadata
        (metadata policy 是本层的职责, 但它**不解释** metadata 内容)。
        """
        import time

        raw_tracks = [t for t in (audio or []) if t is not None]
        result = OutputCompositionResult(ok=False, video=video)

        if not video.path or not Path(video.path).is_file():
            result.errors.append({
                "reason": REASON_COMPOSE_NO_VIDEO,
                "detail": f"video artifact not found: {video.path!r}",
            })
            return result

        # ---- 0. 归一化 + 分配输入序号 -----------------------------------
        # 已编码音轨各占一个 `-i` (序号 = 1, 2, …); 选择器形态不新增输入。
        tracks: list[AudioInputRef] = []
        next_index = 1
        for raw in raw_tracks:
            try:
                ref = _coerce_audio_input(raw)
            except ValueError as exc:
                result.errors.append({
                    "reason": REASON_COMPOSE_AUDIO_MISSING,
                    "detail": str(exc),
                })
                return result
            if ref.adds_input:
                ref = AudioInputRef.from_encoded(raw, next_index)
                next_index += 1
            elif selector_map:
                ref = AudioInputRef.from_selector(
                    _remap_selector(ref.selector, selector_map),
                )
            tracks.append(ref)

        missing = [
            t.source_path for t in tracks
            if t.adds_input and not Path(t.source_path).is_file()
        ]
        if missing:
            result.errors.append({
                "reason": REASON_COMPOSE_AUDIO_MISSING,
                "detail": f"encoded audio artifact(s) not found: {missing}",
            })
            return result

        # ---- 1. 输入: 视频在前, 已编码音轨依次在后, 额外输入最后 --------
        cmd: list[str] = [str(ffmpeg), "-v", "error", "-nostdin", "-y"]
        cmd += ["-i", str(video.path)]
        for track in tracks:
            if track.adds_input:
                cmd += ["-i", str(track.source_path)]
        for path in (extra_inputs or []):
            if path:
                cmd += ["-i", str(path)]

        # ---- 2. 视频: 原样搬进容器 (stream copy) -------------------------
        # ⚠️ 只映射**主**视频流。次视频流 (DJI 附加封面图等) 属于视频域的
        # 容器策略 (Sony/DJI 保留管线各自处理), 本层不替它猜。这是一个
        # 明确的已知边界, 不是"以后再说": 复杂容器请交给 preservation/
        # 的 MP4Box 重建路径, 本 Composer 面向单主视频流的编排。
        video_selector = (
            "0:v:0" if video.is_self_describing
            else f"0:{video.stream}"
        )
        cmd += ["-map", video_selector, "-c:v", "copy"]

        # ---- 3. 音频: 依次搬进容器 (顺序 = 本列表的顺序, 容器里同序) -----
        for track in tracks:
            cmd += ["-map", track.selector]
            entry = track.to_dict()
            entry["output_index"] = len(result.audio_tracks)
            result.audio_tracks.append(entry)
        if tracks:
            cmd += ["-c:a", "copy"]

        # ---- 4. 容器级策略 ----------------------------------------------
        if map_metadata_from is not None:
            cmd += ["-map_metadata", str(int(map_metadata_from))]
        cmd += ["-map_chapters", "-1"]
        if extra_args:
            cmd += [str(a) for a in extra_args]
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd.append(str(target))
        result.command = cmd
        result.path = str(target)

        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result.errors.append({
                "reason": REASON_COMPOSE_FAILED,
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return result
        result.compose_seconds = time.monotonic() - started

        if proc.returncode != 0 or not target.is_file():
            result.errors.append({
                "reason": REASON_COMPOSE_FAILED,
                "detail": (
                    f"ffmpeg compose failed (rc={proc.returncode}): "
                    f"{(proc.stderr or '')[-300:]}"
                ),
            })
            return result

        # ---- 5. 读回事实 (不假设"请求什么就得到什么") --------------------
        probe = _ffprobe_binary(ffprobe, ffmpeg)
        facts = probe_container(target, probe)
        result.streams = facts.get("streams", [])
        result.duration_seconds = float(facts.get("duration") or 0.0)
        result.size_bytes = int(target.stat().st_size)

        actual_audio = len(result.audio_streams())
        if actual_audio != len(tracks):
            result.errors.append({
                "reason": REASON_COMPOSE_VERIFY_FAILED,
                "detail": (
                    f"container has {actual_audio} audio stream(s) but "
                    f"{len(tracks)} were mapped"
                ),
            })
            return result
        if result.video_stream() is None:
            result.errors.append({
                "reason": REASON_COMPOSE_VERIFY_FAILED,
                "detail": "container has no video stream",
            })
            return result

        result.ok = True
        return result

    # -- 内部 ---------------------------------------------------------------

    # 裸流音频 (无容器) 在 `AudioInputRef.from_encoded()` 被明确拒绝: 没有
    # 容器就无法给出稳定选择器, 本层选择"报错"而不是"猜一个流号"。


def probe_container(
    path: Path | str, ffprobe: Path | None = None,
) -> dict[str, Any]:
    """读回容器的流清单与时长 (stream order 就是返回列表的顺序)。

    只报告事实, 不做策略; 失败返回 `{}`。
    """
    import json

    if ffprobe is None:
        return {}
    cmd = [
        str(ffprobe), "-v", "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,channels,"
        "sample_rate,channel_layout,duration,avg_frame_rate,nb_frames:"
        "format=format_name,duration",
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
    streams: list[dict[str, Any]] = []
    for raw in data.get("streams") or []:
        streams.append({
            "index": raw.get("index"),
            "codec_type": raw.get("codec_type"),
            "codec_name": raw.get("codec_name"),
            "width": raw.get("width"),
            "height": raw.get("height"),
            "channels": raw.get("channels"),
            "sample_rate": raw.get("sample_rate"),
            "channel_layout": raw.get("channel_layout"),
            "duration": raw.get("duration"),
            "avg_frame_rate": raw.get("avg_frame_rate"),
            "nb_frames": raw.get("nb_frames"),
        })
    fmt = data.get("format") or {}
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "streams": streams,
        "duration": duration,
        "format_name": fmt.get("format_name"),
    }
