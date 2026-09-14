"""PCM Reader (v0.7.1 Phase 3A) — AudioSource/AudioStream -> canonical float32 PCM.

规范:

    AudioSource / AudioStream            (Phase 1/2 模型, 不重建探测)
            ↓  ffmpeg 解码 (唯一新增的 ffmpeg 执行点)
    canonical **float32** 中间格式
            ↓
    Routing / (Phase 3B: Mixing) / WAV Export

要点:

* **不建立第二套 ffprobe parser**: 本模块只消费已有 `AudioStream` 模型
  (`sample_rate` / `channel_count` / `codec_name` / `raw`), 探测仍由
  `core.probe` + `core.audio_probe` 负责。
* **canonical float32**: 线性 PCM 一律归一化到 `[-1, +1]` 的 float32
  (s16 → ×2⁻¹⁵, s24 → ×2⁻²³, s32 → ×2⁻³¹, f32 原样)。归一化**不改数据
  语义**: `sample_rate` 与声道数继续由 source model 提供, 本模块不猜。
* **chunked**: 解码产物落 raw 临时文件 (与 `core/channel_sync.py` 同策略,
  避免 Windows 管道大流量), 读取按块进行; 峰值内存 ≈ 一个 chunk, 与素材
  时长无关。`chunk_frames` 只影响分块, **不得**影响结果 (§chunk invariance)。
* 压缩音频 (AAC/Opus/…) 一律交给 ffmpeg 解码, 不做任何自研解码。
* EOF 以**实际解码样本数**为最终事实; 与声明值 (nb_frames / header /
  duration×rate) 不一致时记录 `audio_duration_metadata_mismatch`, 不静默
  相信 metadata。
* 本模块**不决定** render duration / EOF 策略 —— 那是
  `core.audio_timeline.AudioTimeline` 的职责; reader 只如实报告可用样本数。

支持的线性 PCM (与 `core/channel_sync.py` 的既有支持范围一致):
    pcm_s16le/be, pcm_s24le/be, pcm_s32le/be, pcm_f32le/be
其余 codec (含 u8/u24/u32 等) 走 ffmpeg 解码, 但**记录**为未显式验证格式;
ffmpeg 无法解码时抛 `audio_decode_failed`。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .audio_models import (
    AudioPlan,
    AudioSampleFormat,
    AudioSource,
    AudioStream,
)
from .audio_timeline import (
    PCM_CHUNK_FRAMES_DEFAULT,
    REASON_AUDIO_DURATION_METADATA_MISMATCH,
    AudioRenderError,
    declared_samples_of_stream,
)

__all__ = [
    "CANONICAL_PCM_DTYPE",
    "CANONICAL_PCM_FORMAT",
    "AudioPCMReader",
    "PCMStreamInfo",
    "REASON_AUDIO_DECODE_FAILED",
    "REASON_AUDIO_PCM_FORMAT_UNSUPPORTED",
    "REASON_AUDIO_PCM_SHORT_READ",
    "cleanup_tmp",
    "decode_spec",
    "identity_channelmap",
    "is_linear_pcm_codec",
    "pcm_tmp_dir",
    "sample_format_of",
]


def cleanup_tmp(work_dir: Path) -> None:
    """删除遗留的 raw 解码目录 (调用方在任务/测试结束时可调用)。"""
    path = Path(work_dir) / "audio_pcm"
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)

#: canonical 内部格式 —— Reader → Routing → Mixing 之间唯一格式 (§PCM 内部格式)。
CANONICAL_PCM_FORMAT = "f32"
CANONICAL_PCM_DTYPE = np.dtype("<f4")

#: 全部支持输入的原始位深 → float32 的归一化因子 (满量程 = 1.0)。
_INT_SCALES: dict[int, float] = {
    8: 1.0 / 128.0,
    16: 1.0 / 32768.0,
    24: 1.0 / 8388608.0,
    32: 1.0 / 2147483648.0,
}

#: `core/channel_sync.py` 已支持的线性 PCM codec (本阶段显式验证集合)。
_LINEAR_PCM_CODECS: dict[str, tuple[int, str]] = {
    "pcm_s16le": (16, "le"), "pcm_s16be": (16, "be"),
    "pcm_s24le": (24, "le"), "pcm_s24be": (24, "be"),
    "pcm_s32le": (32, "le"), "pcm_s32be": (32, "be"),
    "pcm_f32le": (32, "le"), "pcm_f32be": (32, "be"),
}

REASON_AUDIO_DECODE_FAILED = "audio_decode_failed"
REASON_AUDIO_PCM_FORMAT_UNSUPPORTED = "audio_pcm_format_unsupported"
REASON_AUDIO_PCM_SHORT_READ = "audio_pcm_short_read"


def is_linear_pcm_codec(codec_name: str) -> bool:
    """是否属于本阶段显式验证的线性 PCM 集合 (不排斥其它 codec 解码)。"""
    return str(codec_name or "").strip().lower() in _LINEAR_PCM_CODECS


def decode_spec(codec_name: str) -> dict[str, Any]:
    """codec 名 -> 解码/归一化事实 (显式支持集合内的精确位深与端序)。"""
    codec = str(codec_name or "").strip().lower()
    if codec in _LINEAR_PCM_CODECS:
        bits, endian = _LINEAR_PCM_CODECS[codec]
        floating = codec.startswith("pcm_f")
        return {
            "codec_name": codec,
            "linear_pcm": True,
            "bits": bits,
            "endian": endian,
            "float": floating,
            "scale": 1.0 if floating else _INT_SCALES[bits],
        }
    return {
        "codec_name": codec,
        "linear_pcm": False,
        "bits": None,
        "endian": None,
        "float": None,
        "scale": None,
    }


def pcm_tmp_dir(base: Path | None = None) -> Path:
    """raw 解码临时目录 (与 channel_sync 一样落在 work_dir 下)。"""
    root = Path(base) if base is not None else Path(tempfile.gettempdir())
    path = root / "audio_pcm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def identity_channelmap(channels: int) -> str:
    """ffmpeg `channelmap` 的单位映射表达式: `0|1|…|N-1`。

    作用: 强制"按位置逐声道复制", 阻止 ffmpeg 依据声明布局做重排/下混
    (例如 4 声道文件被标成 quad 时, FC/BC 会被折进 BL/BR —— 那会静默改变
    数据语义)。本阶段**禁止**任何隐式混音, 因此解码路径必须带上它。
    """
    count = max(1, int(channels))
    return "|".join(str(i) for i in range(count))


@dataclass
class PCMStreamInfo:
    """一条已解码流的**事实** (不含 PCM 本体)。"""

    stream_id: str
    source_id: str
    stream_index: int
    sample_rate: int
    channel_count: int
    codec_name: str = ""
    audio_position: int | None = None
    #: 声明长度 (nb_frames / duration×rate) —— 仅用于规划与 mismatch 检测
    declared_samples: int | None = None
    declared_source: str | None = None
    #: 实际解码样本数 —— **最终事实**
    actual_samples: int = 0
    raw_path: Path | None = None
    decode_seconds: float | None = None
    linear_pcm: bool = False
    bits: int | None = None
    endian: str | None = None
    scale: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.actual_samples / float(self.sample_rate)

    @property
    def duration_declared_seconds(self) -> float | None:
        if self.declared_samples is None or self.sample_rate <= 0:
            return None
        return self.declared_samples / float(self.sample_rate)

    @property
    def mismatch_samples(self) -> int | None:
        if self.declared_samples is None:
            return None
        return int(self.actual_samples) - int(self.declared_samples)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stream_id": self.stream_id,
            "source_id": self.source_id,
            "stream_index": int(self.stream_index),
            "sample_rate": int(self.sample_rate),
            "channel_count": int(self.channel_count),
            "codec_name": self.codec_name,
            "actual_samples": int(self.actual_samples),
            "duration_seconds": self.duration_seconds,
            "linear_pcm": bool(self.linear_pcm),
        }
        if self.audio_position is not None:
            data["audio_position"] = int(self.audio_position)
        if self.declared_samples is not None:
            data["declared_samples"] = int(self.declared_samples)
        if self.declared_source is not None:
            data["declared_source"] = self.declared_source
        if self.mismatch_samples is not None:
            data["mismatch_samples"] = self.mismatch_samples
            data["mismatch_seconds"] = self.mismatch_samples / float(
                self.sample_rate or 1
            )
        if self.bits is not None:
            data["bits"] = int(self.bits)
        if self.endian is not None:
            data["endian"] = self.endian
        if self.decode_seconds is not None:
            data["decode_seconds"] = round(float(self.decode_seconds), 4)
        if self.raw_path is not None:
            data["raw_path"] = str(self.raw_path)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


class AudioPCMReader:
    """`AudioSource` + `AudioStream` -> canonical float32 PCM (chunked)。

    典型用法 (Phase 3A 处理图内部):

        reader = AudioPCMReader(plan, ffmpeg=Path("tools/ffmpeg.exe"),
                                work_dir=work)
        reader.prepare()                       # 解码 → 得到 actual_samples
        timeline = resolve_timeline(plan, availability=reader.availability())
        block = reader.read("camera:s0", channel_index=2, start=0, count=1024)

    生命周期: 临时 raw 文件由 `close()` / 上下文管理器清理 (Windows 上必须
    先关闭再删除); 进程异常退出时残留在 `work_dir/audio_pcm` 下的文件不参与
    生产输出, 由调用方的 work_dir 清理策略负责。
    """

    def __init__(
        self,
        plan: AudioPlan | Iterable[AudioSource],
        *,
        ffmpeg: Path,
        work_dir: Path,
        chunk_frames: int = PCM_CHUNK_FRAMES_DEFAULT,
        timeout_sec: int = 1800,
        cache: bool = True,
    ) -> None:
        self.ffmpeg = Path(ffmpeg)
        self.work_dir = Path(work_dir)
        self.chunk_frames = max(1, int(chunk_frames))
        self.timeout_sec = int(timeout_sec)
        self.cache = bool(cache)
        sources = (
            list(plan.sources) if isinstance(plan, AudioPlan)
            else list(plan)
        )
        self.sources: list[AudioSource] = sources
        self._by_source: dict[str, AudioSource] = {
            s.source_id: s for s in sources
        }
        self._raw_dir = pcm_tmp_dir(self.work_dir)
        self._streams: dict[str, PCMStreamInfo] = {}
        self._handles: dict[str, Any] = {}
        self._decoded_seconds = 0.0
        self.closed = False

    # -- 来源 -> 文件 -----------------------------------------------------

    def _path_of(self, source: AudioSource) -> Path | None:
        if not source.path:
            return None
        path = Path(source.path)
        return path if path.is_file() else None

    # -- 解码 -------------------------------------------------------------

    def _decode(self, source: AudioSource, stream: AudioStream) -> PCMStreamInfo:
        stream_id = stream.id
        codec = str(stream.codec_name or "")
        spec = decode_spec(codec)
        channels = max(0, int(stream.channel_count or 0))
        rate = int(stream.sample_rate or 0)
        declared, declared_source = declared_samples_of_stream(stream, rate)
        info = PCMStreamInfo(
            stream_id=stream_id,
            source_id=source.source_id,
            stream_index=int(stream.stream_index),
            sample_rate=rate,
            channel_count=channels,
            codec_name=codec,
            audio_position=stream.audio_position,
            declared_samples=declared,
            declared_source=declared_source,
            linear_pcm=bool(spec["linear_pcm"]),
            bits=spec["bits"],
            endian=spec["endian"],
            scale=spec["scale"],
        )
        path = self._path_of(source)
        if path is None:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED,
                f"source {source.source_id!r} has no readable file path",
                location=stream_id,
            )
        if rate <= 0 or channels <= 0:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED,
                f"{stream_id}: incomplete stream facts "
                f"(sample_rate={rate}, channel_count={channels})",
                location=stream_id,
            )
        if codec and not spec["linear_pcm"]:
            info.warnings.append(
                f"codec {codec!r} is not in the linear-PCM verification set; "
                "decoded through ffmpeg, canonical float32 still applies"
            )

        out_raw = self._raw_dir / f"{_safe_name(stream_id)}.f32"
        position = (
            int(stream.audio_position)
            if stream.audio_position is not None else int(stream.stream_index)
        )
        cmd = [
            str(self.ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(path),
            "-map", f"0:a:{position}",
            "-vn", "-sn", "-dn",
        ]
        # ⚠️ 声道顺序必须**逐位保持**: ffmpeg 默认会按声明布局重排/混音
        # (实测: 4 声道 pcm_f32le + "quad" 布局会被重排并叠加 FC/BC),
        # 这会静默改变路由结果。identity channelmap 明确按位置复制,
        # 不插值、不混合、不改增益。
        if channels > 1:
            cmd += ["-af", f"channelmap={identity_channelmap(channels)}"]
        cmd += [
            "-f", "f32le",
            "-ac", str(channels),
            str(out_raw),
        ]
        started = _now()
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=self.timeout_sec,
        )
        info.decode_seconds = _now() - started
        ok = proc.returncode == 0 and out_raw.is_file()
        if not ok:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED,
                f"ffmpeg decode failed for {stream_id} "
                f"(rc={proc.returncode}): {(proc.stderr or '')[-300:]}",
                location=stream_id,
            )
        size = out_raw.stat().st_size
        item = CANONICAL_PCM_DTYPE.itemsize
        info.raw_path = out_raw
        info.actual_samples = int(size // (item * channels))
        if info.actual_samples * item * channels != size:
            info.warnings.append(
                f"decoded byte count {size} is not a whole number of "
                f"{channels}-channel frames — trailing partial frame ignored"
            )
        self._decoded_seconds += float(info.decode_seconds or 0.0)
        return info

    def _info(self, stream_id: str) -> PCMStreamInfo:
        info = self._streams.get(stream_id)
        if info is None:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED,
                f"stream {stream_id!r} was never decoded "
                "(call prepare()/read() first)",
                location=stream_id,
            )
        return info

    def _ensure_stream(self, stream_id: str) -> PCMStreamInfo:
        """按需解码一条流 (同一 stream_id 只解码一次, 不重复 decode)。"""
        if self.closed:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED, "reader is closed"
            )
        existing = self._streams.get(stream_id)
        if existing is None and self.cache:
            existing = self._streams.get(str(stream_id))
        if existing is not None:
            return existing
        found: tuple[AudioSource, AudioStream] | None = None
        for source in self.sources:
            for stream in source.streams:
                if stream.id == stream_id:
                    found = (source, stream)
                    break
            if found is not None:
                break
        if found is None:
            raise AudioRenderError(
                REASON_AUDIO_DECODE_FAILED,
                f"no source/stream matches {stream_id!r}",
                location=stream_id,
            )
        info = self._decode(*found)
        self._streams[stream_id] = info
        return info

    def prepare(
        self, stream_ids: Iterable[str] | None = None,
    ) -> list[PCMStreamInfo]:
        """解码全部 (或指定) 流并返回事实清单 (含 actual_samples)。

        `actual decoded EOF` 是最终事实; 与声明不一致时在 info.warnings 与
        `duration_mismatch_issues()` 里体现, **不**修改模型。
        """
        ids = (
            [str(i) for i in stream_ids] if stream_ids is not None
            else [
                s.id for source in self.sources for s in source.streams
            ]
        )
        return [self._ensure_stream(i) for i in ids]

    # -- 读取 -------------------------------------------------------------

    def _handle(self, stream_id: str) -> Any:
        handle = self._handles.get(stream_id)
        if handle is None:
            info = self._info(stream_id)
            handle = open(info.raw_path, "rb", buffering=0)
            self._handles[stream_id] = handle
        return handle

    def read(
        self,
        stream_id: str,
        *,
        channel_index: int = 0,
        start: int = 0,
        count: int,
    ) -> tuple[np.ndarray, int]:
        """读取某流某声道的 `count` 帧 (从源样本下标 `start` 开始)。

        返回 `(block, valid)`: `block` 长度恒为 `count` (float32, 越界补
        **静音 0.0**), `valid` 为其中真实来自文件的样本数。EOF 之后一律
        静音 —— 不循环, 不复制最后样本, 不产生 NaN。
        越界读取返回 `(zeros, 0)`, 便于调用方区分 EOF 与静音填充。
        """
        want = int(count)
        if want < 0:
            raise AudioRenderError(
                REASON_AUDIO_PCM_SHORT_READ, f"negative count: {want}"
            )
        info = self._ensure_stream(stream_id)
        block = np.zeros(want, dtype=CANONICAL_PCM_DTYPE)
        if want == 0:
            return block, 0
        chan = int(channel_index)
        if chan < 0 or chan >= max(1, info.channel_count):
            raise AudioRenderError(
                REASON_AUDIO_PCM_SHORT_READ,
                f"{stream_id}: channel_index {chan} out of range "
                f"(channel_count={info.channel_count})",
                location=stream_id,
            )
        lo = int(start)
        hi = lo + want
        lo_c = max(0, lo)
        hi_c = min(info.actual_samples, hi)
        valid = max(0, hi_c - lo_c)
        if valid > 0:
            handle = self._handle(stream_id)
            item = CANONICAL_PCM_DTYPE.itemsize
            stride = item * info.channel_count
            # 交错布局: 目标声道在每 stride 字节里占 item 字节 —— 必须按
            # **跨步**取样, 不能把前 valid 个 float 当成该声道 (那会读到其它
            # 声道的样本)。结果形状只由 (lo, valid) 决定, 与 chunk 划分无关。
            handle.seek(lo_c * stride)
            raw = handle.read(valid * stride)
            if len(raw) < valid * stride:
                raw += bytes(valid * stride - len(raw))
            framed = np.frombuffer(raw, dtype=CANONICAL_PCM_DTYPE).reshape(
                valid, info.channel_count
            )
            block[lo_c - lo: lo_c - lo + valid] = framed[:, chan]
        return block, valid

    def read_frames(
        self,
        stream_id: str,
        *,
        channel_index: int = 0,
        start: int,
        count: int,
    ) -> np.ndarray:
        """读取 `[start, start+count)`（**以该区间自身为原点**的样本坐标）。

        与 `read()` 的区别: `read()` 返回按请求下标对齐的块（便于窗口拼接）,
        本方法返回"贴着窗口"的块 —— `out[k]` 就是源样本 `start + k`, 越界
        一律静音 0.0. 逐样本按 timeline 对齐的混音用这个形式最自然。
        """
        want = int(count)
        if want <= 0:
            return np.zeros(max(0, want), dtype=CANONICAL_PCM_DTYPE)
        info = self._ensure_stream(stream_id)
        out = np.zeros(want, dtype=CANONICAL_PCM_DTYPE)
        lo = int(start)
        lo_c = max(0, lo)
        hi_c = min(int(info.actual_samples), lo + want)
        valid = max(0, hi_c - lo_c)
        if valid > 0:
            got, fetched = self.read(
                stream_id, channel_index=channel_index, start=lo_c,
                count=valid,
            )
            if fetched > 0:
                out[lo_c - lo: lo_c - lo + fetched] = got[:fetched]
        return out

    def eof_sample(self, stream_id: str) -> int:
        """实际解码样本数 (= 该流 EOF 的 sample-accurate 位置)。"""
        return int(self._info(stream_id).actual_samples)

    def info(self, stream_id: str) -> PCMStreamInfo:
        return self._info(stream_id)

    def describe(self) -> list[dict[str, Any]]:
        """已解码流的紧凑事实清单 (timeline 的 availability 来源)。"""
        return [
            {
                "source_id": info.source_id,
                "stream_index": info.stream_index,
                "stream_id": info.stream_id,
                "sample_rate": info.sample_rate,
                "channel_count": info.channel_count,
                "declared_samples": info.declared_samples,
                "declared_source": info.declared_source,
                "actual_samples": info.actual_samples,
                "codec_name": info.codec_name,
                "linear_pcm": info.linear_pcm,
            }
            for info in self._streams.values()
        ]

    def availability(self) -> dict[tuple[str, int], dict[str, Any]]:
        """`(source_id, stream_index) -> {declared,actual,…}` (timeline 入参)。"""
        return {
            (info.source_id, info.stream_index): {
                "declared_samples": info.declared_samples,
                "actual_samples": info.actual_samples,
                "declared_source": info.declared_source,
            }
            for info in self._streams.values()
        }

    def duration_mismatches(self, tolerance_samples: int = 1) -> list[dict[str, Any]]:
        """声明 vs 实际解码样本数的不一致 (§metadata 不可信, 但必须记录)。"""
        out: list[dict[str, Any]] = []
        for info in sorted(
            self._streams.values(), key=lambda i: (i.source_id, i.stream_index)
        ):
            delta = info.mismatch_samples
            if delta is None or abs(delta) <= int(tolerance_samples):
                continue
            out.append({
                "reason": REASON_AUDIO_DURATION_METADATA_MISMATCH,
                "stream_id": info.stream_id,
                "source_id": info.source_id,
                "stream_index": int(info.stream_index),
                "declared_samples": int(info.declared_samples),
                "actual_samples": int(info.actual_samples),
                "declared_source": info.declared_source,
                "delta_samples": int(delta),
                "delta_seconds": int(delta) / float(info.sample_rate or 1),
                "detail": (
                    f"{info.stream_id}: decoded {info.actual_samples} samples "
                    f"but {info.declared_source or 'metadata'} declares "
                    f"{info.declared_samples} ({delta:+d}) — actual decoded "
                    "EOF is authoritative"
                ),
            })
        return out

    @property
    def decode_seconds(self) -> float:
        """累计解码耗时 (性能回归/日志用; 不参与结果)。"""
        return float(self._decoded_seconds)

    @property
    def decoded_bytes_per_stream(self) -> dict[str, int]:
        return {
            sid: int(info.actual_samples * info.channel_count
                     * CANONICAL_PCM_DTYPE.itemsize)
            for sid, info in self._streams.items()
        }

    # -- 生命周期 ---------------------------------------------------------

    def close(self) -> None:
        if self.closed:
            return
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                pass
        self._handles.clear()
        for info in self._streams.values():
            if info.raw_path is None:
                continue
            try:
                os.unlink(info.raw_path)
            except OSError:
                pass
        self.closed = True

    def __enter__(self) -> "AudioPCMReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:      # pragma: no cover - 兜底清理
        try:
            self.close()
        except Exception:
            pass


def _now() -> float:
    import time
    return time.monotonic()


def _safe_name(text: str) -> str:
    """stream id -> 合法文件名 (Windows 上 ':' 非法)。"""
    out = "".join(
        ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(text)
    )
    return out or "stream"


def sample_format_of(codec_name: str) -> AudioSampleFormat:
    """codec 名 -> AudioSampleFormat 标签 (只做映射, 不猜位深)。"""
    codec = str(codec_name or "").strip().lower()
    table = {
        "pcm_s16le": AudioSampleFormat.S16, "pcm_s16be": AudioSampleFormat.S16,
        "pcm_s24le": AudioSampleFormat.S24, "pcm_s24be": AudioSampleFormat.S24,
        "pcm_s32le": AudioSampleFormat.S32, "pcm_s32be": AudioSampleFormat.S32,
        "pcm_f32le": AudioSampleFormat.FLT, "pcm_f32be": AudioSampleFormat.FLT,
        "pcm_f64le": AudioSampleFormat.DBL, "pcm_f64be": AudioSampleFormat.DBL,
    }
    return table.get(codec, AudioSampleFormat.UNKNOWN)
