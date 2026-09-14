"""WAV Export (v0.7.1 Phase 3A) — RIFF 写入/读取, 不依赖大型音频框架。

设计要点:

* **不手写"看起来能播放"的 WAV**: 头部字段 (RIFF 大小 / fmt / data 大小 /
  byte_rate / block_align / bits_per_sample) 全部按实际写出的字节数回填;
  容器大小超 4 GiB 时自动切到 RF64 (ds64), 不截断。
* 24-bit 与 IEEE float 无法用标准库 `wave` 完整表达, 因此自带小型 writer
  (纯标准库 + numpy 数值转换), **不引入任何音频框架**。
* 多声道 (>= 3 声道) 且需要标称布局时使用 `WAVE_FORMAT_EXTENSIBLE`
  (fmt 大小 40, 带 channel mask), 这是"必要时"的唯一扩展用法;
  1/2 声道写经典 PCM/IEEE_FLOAT 头, 兼容性最好。
* **不实现 mixing**: exporter 只把 (frames, channels) 的 float32 写成文件。
  混音在 Phase 3B 的 `core/audio_mix.py`。
* **不决定**输出长度 / EOF: 长度由调用方 (AudioTimeline) 给定; exporter
  只校验"写入样本数 == 声明样本数", 不一致直接报
  `audio_wav_write_failed`。
* reader (`read_wav`) 用于 round-trip 校验与外部 WAV 事实读取, 返回
  canonical float32 —— 与 `core.audio_pcm` 的中间格式一致。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping, Sequence

import numpy as np

from .audio_pcm import CANONICAL_PCM_DTYPE

__all__ = [
    "AudioOutputSpec",
    "REASON_AUDIO_WAV_WRITE_FAILED",
    "REASON_AUDIO_OUTPUT_INVALID",
    "WAV_EXTENSIBLE_MIN_CHANNELS",
    "WavExportSpec",
    "WavExporter",
    "WavFormat",
    "WavInfo",
    "default_wav_name",
    "format_code_of",
    "is_float_format_code",
    "read_wav",
    "unique_output_path",
    "write_wav",
]

REASON_AUDIO_WAV_WRITE_FAILED = "audio_wav_write_failed"
REASON_AUDIO_OUTPUT_INVALID = "audio_output_invalid"

#: 3 声道及以上写 EXTENSIBLE (含 channel mask), 1/2 声道写经典头。
WAV_EXTENSIBLE_MIN_CHANNELS = 3

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_SUBFORMAT_PCM = "0100000000001000800000aa00389b71"
_SUBFORMAT_FLOAT = "0300000000001000800000aa00389b71"

#: 标准声道顺序 -> WAVE channel mask 位 (与 ffmpeg/RIFF 约定一致)。
_CHANNEL_MASK_BITS: dict[str, int] = {
    "FL": 0x1, "FR": 0x2, "FC": 0x4, "LFE": 0x8,
    "BL": 0x10, "BR": 0x20, "FLC": 0x40, "FRC": 0x80,
    "BC": 0x100, "SL": 0x200, "SR": 0x400,
}

_LAYOUT_TO_MASK: dict[str, int] = {
    "mono": 0x4, "stereo": 0x3, "2.1": 0x7, "3.0": 0x7, "4.0": 0x33,
    "quad": 0x33, "5.0": 0x37, "5.1": 0x3F, "7.1": 0x63F,
}


class WavFormat(str, Enum):
    """WAV 输出采样格式 (Phase 3A 四种全支持)。

    ================  =========================  ==========================
    取值               位深/编码                   满量程换算
    ================  =========================  ==========================
    PCM16             16-bit signed integer       round(x * 32768) 截到 s16
    PCM24             24-bit signed integer       round(x * 2^23) 截到 s24
    PCM32             32-bit signed integer       round(x * 2^31) 截到 s32
    FLOAT32           32-bit IEEE float           原样 (允许超范围, 不裁剪)
    ================  =========================  ==========================

    `FLOAT32` **允许 over-range 数据原样写入** (不自动 normalize, 不硬裁剪),
    必要时仍可被 `audio_mix_clipping` 报出 —— 数据行为可预测优先。
    """

    PCM16 = "pcm16"
    PCM24 = "pcm24"
    PCM32 = "pcm32"
    FLOAT32 = "float32"

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> Any:
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return default
        text = str(value).strip().lower().replace("-", "").replace("_", "")
        table = {
            "pcm16": cls.PCM16, "s16": cls.PCM16, "16": cls.PCM16,
            "pcm24": cls.PCM24, "s24": cls.PCM24, "24": cls.PCM24,
            "pcm32": cls.PCM32, "s32": cls.PCM32, "32": cls.PCM32,
            "float32": cls.FLOAT32, "f32": cls.FLOAT32,
            "flt": cls.FLOAT32, "float": cls.FLOAT32,
        }
        return table.get(text, default)

    @property
    def bits(self) -> int:
        return 32 if self is WavFormat.FLOAT32 else int(self.value[3:])

    @property
    def is_float(self) -> bool:
        return self is WavFormat.FLOAT32

    @property
    def bytes_per_sample(self) -> int:
        return self.bits // 8

    def clip_limit(self) -> float:
        """该输出格式可无损表示的**开区间**上界 (|x| >= limit 即裁剪)。

        整数格式写 `rint(x * 2^(bits-1))` 后截到 `[-(2^(bits-1)),
        2^(bits-1)-1]`, 因此 `|x| >= 1.0` 必被裁剪 (PCM16 因量化步长更大,
        实际从 `32767.5/32768` 起就“顶格”)。FLOAT32 不裁剪 -> inf。
        """
        if self.is_float:
            return float("inf")
        half = float(1 << (self.bits - 1))
        return (half - 0.5) / half

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.value,
            "bits": int(self.bits),
            "float": bool(self.is_float),
            "bytes_per_sample": int(self.bytes_per_sample),
        }


def format_code_of(fmt: WavFormat, channels: int) -> int:
    """(格式, 声道数) -> RIFF wFormatTag 基码 (EXTENSIBLE 另由 writer 决定)。"""
    if fmt.is_float:
        return _WAVE_FORMAT_IEEE_FLOAT
    return _WAVE_FORMAT_PCM


def is_float_format_code(code: int) -> bool:
    """基础格式码是否 IEEE float (EXTENSIBLE SubFormat 已归一后使用)。"""
    return int(code) == _WAVE_FORMAT_IEEE_FLOAT


def channel_mask_for(channels: int, layout: str | None = None) -> int:
    """声道数/布局 -> WAVE channel mask (未知布局 -> 0 = 不声明)。"""
    if layout:
        key = str(layout).strip().lower()
        if key in _LAYOUT_TO_MASK:
            return _LAYOUT_TO_MASK[key]
        parts = [p.strip().upper() for p in str(layout).replace("-", " ").split()
                 if p.strip()]
        mask = 0
        for part in parts:
            mask |= _CHANNEL_MASK_BITS.get(part, 0)
        if mask:
            return mask
    table = {1: 0x4, 2: 0x3, 3: 0x7, 4: 0x33, 5: 0x37, 6: 0x3F, 8: 0x63F}
    return table.get(int(channels), 0)


# ---------------------------------------------------------------------------
# 输出描述 (§AudioOutputSpec)
# ---------------------------------------------------------------------------


@dataclass
class AudioOutputSpec:
    """一次音频输出的**声明式**描述 (为 Phase 4 的 MP4 集成预留)。

    Phase 3A 只用它描述 WAV 产物: 不含 PCM、不含 argv、JSON-compatible,
    命名策略集中在这里而不是散落在 processor 里 (§WAV 输出命名)。
    """

    path: str
    kind: str = "wav"
    sample_format: WavFormat = WavFormat.PCM24
    sample_rate: int = 0
    channel_count: int = 0
    frame_count: int = 0
    source_ids: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    layout: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def data_bytes(self) -> int:
        return int(self.frame_count) * int(self.channel_count) \
            * self.sample_format.bytes_per_sample

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "kind": self.kind,
            "sample_format": self.sample_format.value,
            "sample_rate": int(self.sample_rate),
            "channel_count": int(self.channel_count),
            "frame_count": int(self.frame_count),
            "data_bytes": int(self.data_bytes),
        }
        if self.source_ids:
            data["source_ids"] = list(self.source_ids)
        if self.channel_ids:
            data["channel_ids"] = list(self.channel_ids)
        if self.layout:
            data["layout"] = self.layout
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        if self.notes:
            data["notes"] = list(self.notes)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioOutputSpec":
        return cls(
            path=str(data.get("path") or ""),
            kind=str(data.get("kind") or "wav"),
            sample_format=WavFormat.coerce(
                data.get("sample_format"), WavFormat.PCM24
            ) or WavFormat.PCM24,
            sample_rate=int(data.get("sample_rate") or 0),
            channel_count=int(data.get("channel_count") or 0),
            frame_count=int(data.get("frame_count") or 0),
            source_ids=[str(s) for s in (data.get("source_ids") or [])],
            channel_ids=[str(s) for s in (data.get("channel_ids") or [])],
            layout=str(data.get("layout") or ""),
            metadata=dict(data.get("metadata") or {}),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


@dataclass
class WavExportSpec:
    """WAV 导出的完整参数 (输出文件 + 格式 + 期望长度)。"""

    output: AudioOutputSpec
    frame_count: int = 0
    channel_count: int = 0
    sample_rate: int = 0
    sample_format: WavFormat = WavFormat.PCM24
    layout: str = ""
    overwrite: bool = False

    def validate(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if not self.output.path:
            issues.append({
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": "output path is empty",
            })
        if self.sample_rate <= 0:
            issues.append({
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": f"invalid sample_rate: {self.sample_rate}",
            })
        if self.channel_count <= 0:
            issues.append({
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": f"invalid channel_count: {self.channel_count}",
            })
        if self.frame_count < 0:
            issues.append({
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": f"negative frame_count: {self.frame_count}",
            })
        if self.output.kind != "wav":
            issues.append({
                "reason": REASON_AUDIO_OUTPUT_INVALID,
                "detail": (
                    f"Phase 3A only writes 'wav' outputs, got "
                    f"{self.output.kind!r}"
                ),
            })
        return issues

    @property
    def data_bytes(self) -> int:
        return int(self.frame_count) * int(self.channel_count) \
            * self.sample_format.bytes_per_sample

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output.to_dict(),
            "frame_count": int(self.frame_count),
            "channel_count": int(self.channel_count),
            "sample_rate": int(self.sample_rate),
            "sample_format": self.sample_format.value,
            "layout": self.layout,
            "data_bytes": int(self.data_bytes),
        }


def default_wav_name(
    *,
    source_stem: str | None = None,
    channel_ids: Sequence[str] = (),
    mapping: str | None = None,
    suffix: str = "",
) -> str:
    """稳定的可编程命名: `<source>_<track/channel>_<mapping>.wav`。

    规则 (无随机、无时间戳 —— 便于测试与复跑比对):

      * 1 个输出声道   -> `<source>_<c3>` (例: `camera_s2c2`)
      * 整个 source    -> `<source>_all`
      * 多声道         -> `<source>_mix` (跨来源) / `<source>_Nch`
      * `mapping` 非空  -> 追加 `_<mapping>` (例: `r2031`)

    文件名中的非法字符一律替换为 `_`; **同名覆盖**由
    `unique_output_path()` / `WavExporter` 的 `overwrite=False` 拦住。
    """
    stems = _unique_stems(channel_ids) if channel_ids else []
    if source_stem:
        base = _safe(source_stem)
    elif len(stems) == 1:
        base = _safe(stems[0])
    elif stems:
        base = "multi"
    else:
        base = "audio"
    if not channel_ids:
        track = "all"
    elif len(channel_ids) == 1:
        parts = _parse_ids(channel_ids[0])
        track = f"s{parts[1]}c{parts[2]}" if parts else "ch0"
    else:
        track = "mix" if len(stems) > 1 else f"{len(channel_ids)}ch"
    name = f"{base}_{track}"
    if mapping:
        name += f"_{_safe(mapping)}"
    if suffix:
        name += f"_{_safe(suffix)}"
    return name + ".wav"


def unique_output_path(path: Path | str, *, overwrite: bool = False) -> Path:
    """同名冲突 -> 追加 `-2`, `-3`, … (§禁止同名覆盖)。"""
    target = Path(path)
    if overwrite or not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 1000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find a free output name for {target}")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


@dataclass
class WavInfo:
    """读回的/写出的 WAV 事实 (header + 实际样本)。"""

    path: str
    sample_rate: int
    channel_count: int
    bits_per_sample: int
    format_code: int
    extensible: bool
    frame_count: int
    data_bytes: int
    riff_bytes: int
    byte_rate: int
    block_align: int
    channel_mask: int = 0
    #: data chunk 起始偏移 (读取用; 不假定固定 44 字节头部)
    data_offset: int = 0
    #: EXTENSIBLE 时 SubFormat 的基础格式码 (PCM / IEEE_FLOAT; 否则同 format_code)
    subformat_code: int = 0
    peak: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.frame_count / float(self.sample_rate)

    @property
    def effective_format_code(self) -> int:
        """真实编码: EXTENSIBLE 时看 SubFormat, 否则看 wFormatTag。"""
        if self.format_code == _WAVE_FORMAT_EXTENSIBLE and self.subformat_code:
            return int(self.subformat_code)
        return int(self.format_code)

    @property
    def is_float(self) -> bool:
        return self.effective_format_code == _WAVE_FORMAT_IEEE_FLOAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sample_rate": int(self.sample_rate),
            "channel_count": int(self.channel_count),
            "bits_per_sample": int(self.bits_per_sample),
            "format_code": int(self.format_code),
            "subformat_code": int(self.subformat_code),
            "effective_format_code": int(self.effective_format_code),
            "float": bool(self.is_float),
            "extensible": bool(self.extensible),
            "frame_count": int(self.frame_count),
            "data_bytes": int(self.data_bytes),
            "riff_bytes": int(self.riff_bytes),
            "byte_rate": int(self.byte_rate),
            "block_align": int(self.block_align),
            "channel_mask": int(self.channel_mask),
            "duration_seconds": self.duration_seconds,
            "peak": self.peak,
            "warnings": list(self.warnings),
        }


def _fmt_chunk(
    fmt: WavFormat, channels: int, sample_rate: int, layout: str = "",
) -> tuple[bytes, int, bool]:
    """-> (fmt chunk 载荷, wFormatTag, 是否 extensible)。"""
    bits = fmt.bits
    block_align = channels * fmt.bytes_per_sample
    byte_rate = sample_rate * block_align
    base_code = format_code_of(fmt, channels)
    use_extensible = channels >= WAV_EXTENSIBLE_MIN_CHANNELS
    if use_extensible:
        mask = channel_mask_for(channels, layout)
        sub = _SUBFORMAT_FLOAT if fmt.is_float else _SUBFORMAT_PCM
        payload = struct.pack(
            "<HHIIHHHHI", _WAVE_FORMAT_EXTENSIBLE, channels, sample_rate,
            byte_rate, block_align, bits, 22, bits, mask,
        ) + bytes.fromhex(sub)
        return payload, _WAVE_FORMAT_EXTENSIBLE, True
    payload = struct.pack(
        "<HHIIHH", base_code, channels, sample_rate,
        byte_rate, block_align, bits,
    )
    return payload, base_code, False


def _encode(block: np.ndarray, fmt: WavFormat) -> bytes:
    """float32 [-1,1] -> 目标整数/浮点表示 (PCM 裁剪, FLOAT32 原样)。"""
    data = np.asarray(block, dtype=CANONICAL_PCM_DTYPE)
    if fmt is WavFormat.FLOAT32:
        return np.ascontiguousarray(data, dtype="<f4").tobytes()
    if fmt is WavFormat.PCM16:
        scaled = np.rint(data.astype(np.float64) * 32768.0)
        clipped = np.clip(scaled, -32768.0, 32767.0)
        return clipped.astype("<i2").tobytes()
    if fmt is WavFormat.PCM32:
        scaled = np.rint(data.astype(np.float64) * 2147483648.0)
        clipped = np.clip(scaled, -2147483648.0, 2147483647.0)
        return clipped.astype("<i4").tobytes()
    if fmt is WavFormat.PCM24:
        scaled = np.rint(data.astype(np.float64) * 8388608.0)
        clipped = np.clip(scaled, -8388608.0, 8388607.0).astype("<i4")
        raw = clipped.astype("<i4").view("<u1").reshape(-1, 4)
        return np.ascontiguousarray(raw[:, :3]).tobytes()
    raise ValueError(f"unsupported wav format: {fmt!r}")


class WavExporter:
    """流式 WAV writer: 逐块 `write(block)`, `close()` 时回填尺寸。

        with WavExporter(spec) as writer:
            for block in router.frames(chunk_frames=4096):
                writer.write(block)
        info = writer.info          # 实际写出的 header + 样本事实

    写出样本数必须严格等于 `spec.frame_count × spec.channel_count`;
    提前 close / 数量不符 -> `audio_wav_write_failed` (不产出"差不多"的文件)。
    """

    def __init__(self, spec: WavExportSpec) -> None:
        issues = spec.validate()
        if issues:
            raise ValueError(
                f"{issues[0]['reason']}: {issues[0]['detail']}"
            )
        self.spec = spec
        target = Path(spec.output.path)
        if not spec.overwrite:
            target = unique_output_path(target)
            self.spec.output.path = str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self.sample_format = spec.sample_format
        self._frames_written = 0
        self._bytes_written = 0
        self._peak = 0.0
        self._clip_count = 0
        self._handle: BinaryIO | None = None
        self._closed = False
        self.info: WavInfo | None = None
        self._open()

    # -- 生命周期 ---------------------------------------------------------

    def _open(self) -> None:
        fmt_payload, _code, extensible = _fmt_chunk(
            self.sample_format, self.spec.channel_count,
            self.spec.sample_rate, self.spec.layout,
        )
        self._extensible = extensible
        self._fmt_payload = fmt_payload
        self._handle = open(self.path, "wb+")
        # 头部先写占位, close() 时用真实尺寸回填 (RIFF 必须准确)。
        self._handle.write(b"RIFF" + struct.pack("<I", 0) + b"WAVE")
        self._handle.write(b"fmt " + struct.pack("<I", len(fmt_payload)))
        self._handle.write(fmt_payload)
        self._handle.write(b"data" + struct.pack("<I", 0))
        self._data_offset = self._handle.tell()

    def write(self, block: Any) -> int:
        """写入一块 (frames, channels) float32; 返回写入的帧数。"""
        if self._closed or self._handle is None:
            raise ValueError(
                f"{REASON_AUDIO_WAV_WRITE_FAILED}: writer is closed"
            )
        data = np.asarray(block, dtype=CANONICAL_PCM_DTYPE)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.ndim != 2 or data.shape[1] != self.spec.channel_count:
            raise ValueError(
                f"{REASON_AUDIO_WAV_WRITE_FAILED}: block shape "
                f"{data.shape} does not match expected channel count "
                f"{self.spec.channel_count}"
            )
        if data.shape[0] == 0:
            return 0
        remaining = self.spec.frame_count - self._frames_written
        if data.shape[0] > remaining:
            # 最后一块多写 = EOF 不精确 -> 拒绝而不是静默截断
            raise ValueError(
                f"{REASON_AUDIO_WAV_WRITE_FAILED}: block writes "
                f"{data.shape[0]} frames but only {remaining} remain of the "
                f"declared {self.spec.frame_count} frames"
            )
        raw = _encode(data, self.sample_format)
        self._handle.write(raw)
        self._frames_written += int(data.shape[0])
        self._bytes_written += len(raw)
        if data.size:
            peak = float(np.max(np.abs(data)))
            if peak > self._peak:
                self._peak = peak
            if not self.sample_format.is_float:
                self._clip_count += int(np.count_nonzero(np.abs(data) > 1.0))
        return int(data.shape[0])

    def close(self) -> WavInfo:
        """回填 RIFF/data 尺寸并返回实测事实 (样本数不符 -> 抛错 + 删文件)。"""
        if self._closed:
            assert self.info is not None
            return self.info
        assert self._handle is not None
        problems: list[str] = []
        if self._frames_written != self.spec.frame_count:
            problems.append(
                f"wrote {self._frames_written} frames but declared "
                f"{self.spec.frame_count}"
            )
        if self._bytes_written != self.spec.data_bytes:
            problems.append(
                f"wrote {self._bytes_written} data bytes but expected "
                f"{self.spec.data_bytes}"
            )
        data_size = self._bytes_written
        fmt_payload = self._fmt_payload
        if len(fmt_payload) % 2:
            fmt_payload += b"\x00"
        riff_size = 4 + (8 + len(fmt_payload)) + 8 + data_size
        # 头部布局 (chunk 之间无额外间隙):
        #   0  RIFF<size=4>WAVE                     12 B
        #  12  "fmt "<size> + payload(+pad)      8+N B
        #  20+len(fmt_payload)  "data"<size=4>      8 B
        data_header_pos = 12 + 8 + len(fmt_payload)
        self._handle.seek(4)
        self._handle.write(struct.pack("<I", riff_size))
        self._handle.seek(data_header_pos + 4)      # 跳过 "data" 标识
        self._handle.write(struct.pack("<I", data_size))
        self._handle.flush()
        self._handle.close()
        self._handle = None
        self._closed = True
        if problems:
            try:
                self.path.unlink()
            except OSError:
                pass
            raise ValueError(
                f"{REASON_AUDIO_WAV_WRITE_FAILED}: " + "; ".join(problems)
            )
        size_on_disk = self.path.stat().st_size
        extra: list[str] = []
        expected_size = 12 + 8 + len(fmt_payload) + 8 + data_size
        if size_on_disk != expected_size:
            extra.append(
                f"file size {size_on_disk} != computed {expected_size}"
            )
        self.info = WavInfo(
            path=str(self.path),
            sample_rate=int(self.spec.sample_rate),
            channel_count=int(self.spec.channel_count),
            bits_per_sample=int(self.sample_format.bits),
            format_code=int(_WAVE_FORMAT_IEEE_FLOAT
                            if self.sample_format.is_float
                            else _WAVE_FORMAT_PCM),
            extensible=bool(self._extensible),
            frame_count=int(self._frames_written),
            data_bytes=int(data_size),
            riff_bytes=int(riff_size),
            byte_rate=int(self.spec.sample_rate * self.spec.channel_count
                          * self.sample_format.bytes_per_sample),
            block_align=int(self.spec.channel_count
                            * self.sample_format.bytes_per_sample),
            channel_mask=int(channel_mask_for(
                self.spec.channel_count, self.spec.layout
            )),
            peak=float(self._peak),
            warnings=extra,
        )
        return self.info

    @property
    def frames_written(self) -> int:
        return int(self._frames_written)

    @property
    def closed(self) -> bool:
        """是否已 close (close 失败也算终结 —— 文件已删除)。"""
        return bool(self._closed)

    def abort(self) -> None:
        """失败路径: 关闭句柄并删除半成品 (不抛异常)。

        `close()` 在样本数不符时会**主动**抛错并删文件; 渲染中途异常时用
        本方法清理, 保证"失败不产出文件"。
        """
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._closed = True
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def peak(self) -> float:
        return float(self._peak)

    @property
    def clipped_samples(self) -> int:
        """|sample| > 1.0 的样本数 (整型输出会被裁剪, float 输出原样保留)。"""
        return int(self._clip_count)

    def __enter__(self) -> "WavExporter":
        return self

    def __exit__(self, *exc: Any) -> None:
        if not self._closed:
            try:
                self.close()
            except ValueError:
                if exc[0] is None:
                    raise


def write_wav(
    path: Path | str,
    blocks: Iterable[Any],
    *,
    sample_rate: int,
    channel_count: int,
    sample_format: WavFormat | str = WavFormat.PCM24,
    frame_count: int | None = None,
    layout: str = "",
    overwrite: bool = False,
    source_ids: Sequence[str] = (),
    channel_ids: Sequence[str] = (),
) -> tuple[WavInfo, AudioOutputSpec]:
    """便捷入口: 逐块写入 WAV, 返回 (实测事实, 输出描述)。

    未显式给 `frame_count` 时, 先按写入量累计 (此时 exacter EOF 由调用方
    的 timeline 保证; 生产路径一直显式传 `timeline.frame_count`)。
    """
    fmt = WavFormat.coerce(sample_format, WavFormat.PCM24) or WavFormat.PCM24
    frames = int(frame_count) if frame_count is not None else 0
    spec = WavExportSpec(
        output=AudioOutputSpec(
            path=str(path),
            kind="wav",
            sample_format=fmt,
            sample_rate=int(sample_rate),
            channel_count=int(channel_count),
            frame_count=frames,
            source_ids=list(source_ids),
            channel_ids=list(channel_ids),
            layout=layout,
        ),
        frame_count=frames,
        channel_count=int(channel_count),
        sample_rate=int(sample_rate),
        sample_format=fmt,
        layout=layout,
        overwrite=overwrite,
    )
    if frame_count is None:
        # 无声明长度: 用两遍式缓冲 (仅测试/小数据路径), 生产必须显式给长度。
        all_blocks = [
            np.asarray(b, dtype=CANONICAL_PCM_DTYPE).reshape(-1, channel_count)
            for b in blocks
        ]
        frames = int(sum(b.shape[0] for b in all_blocks))
        spec.frame_count = frames
        spec.output.frame_count = frames
    writer = WavExporter(spec)
    with writer:
        for block in blocks:
            writer.write(block)
    info = writer.info or writer.close()
    spec.output.frame_count = info.frame_count
    spec.output.sample_rate = info.sample_rate
    spec.output.channel_count = info.channel_count
    return info, spec.output


# ---------------------------------------------------------------------------
# 读取 (round-trip 校验 + 外挂 WAV 事实读取)
# ---------------------------------------------------------------------------


def read_wav(
    path: Path | str, *, count: int | None = None, offset: int = 0,
) -> tuple[np.ndarray, WavInfo]:
    """读取 WAV -> canonical float32 (frames, channels), 并返回头部事实。

    只支持本模块写出的形态 + 常见标准 WAV: PCM 16/24/32 与 IEEE float32
    (含 EXTENSIBLE 的 SubFormat 判定)。其它位深/编码 -> ValueError
    (`audio_pcm_format_unsupported`) —— 不猜。

    **data chunk 长度为最终事实**: 头部声明大于实际字节数时按实际读取并在
    `info.warnings` 里记录 (截断文件不静默补零)。
    """
    target = Path(path)
    raw = target.read_bytes()
    info = parse_wav_header(raw, path=str(target))
    code = info.effective_format_code
    item = info.bits_per_sample // 8
    if code not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_IEEE_FLOAT):
        raise ValueError(
            f"audio_pcm_format_unsupported: unsupported WAV format "
            f"{code:#06x} in {target}"
        )
    data = raw[info.data_offset: info.data_offset + info.data_bytes]
    channels = int(info.channel_count)
    item = max(1, item)
    frames = len(data) // (item * channels)
    start = max(0, int(offset)) if count is not None else 0
    if count is not None:
        frames = min(frames, max(0, int(count)))
        data = data[start * item * channels:
                    (start + frames) * item * channels]
    if item == 2:
        arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif item == 3:
        packed = np.frombuffer(data, dtype="<u1").reshape(-1, 3)
        wide = np.zeros((packed.shape[0], 4), dtype="<u1")
        wide[:, :3] = packed
        val = wide.view("<i4").reshape(-1).astype(np.int64)
        # 24-bit 值补符号位 (>= 2^23 视为负数)
        val = np.where(val >= 8388608, val - 16777216, val)
        arr = (val / 8388608.0).astype(np.float32)
    elif item == 4:
        if code == _WAVE_FORMAT_IEEE_FLOAT:
            arr = np.frombuffer(data, dtype="<f4").astype(np.float32)
        else:
            arr = np.frombuffer(data, dtype="<i4").astype(np.float32) \
                / 2147483648.0
    else:
        raise ValueError(
            f"audio_pcm_format_unsupported: {info.bits_per_sample}-bit WAV "
            f"in {target}"
        )
    out = arr.reshape(-1, channels) if channels else arr.reshape(-1, 1)
    info.frame_count = int(out.shape[0])
    if out.size:
        info.peak = float(np.max(np.abs(out)))
    return np.ascontiguousarray(out, dtype=CANONICAL_PCM_DTYPE), info


def parse_wav_header(raw: bytes, *, path: str = "") -> WavInfo:
    """RIFF 解析 (只读头部事实; 不假定 data 在固定偏移)。"""
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(
            f"audio_pcm_format_unsupported: not a RIFF/WAVE file: {path}"
        )
    riff_size = struct.unpack_from("<I", raw, 4)[0]
    pos = 12
    fmt: dict[str, Any] | None = None
    data_offset: int | None = None
    data_size = 0
    declared_data_size = 0
    warnings: list[str] = []
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        chunk_size = struct.unpack_from("<I", raw, pos + 4)[0]
        body = pos + 8
        if chunk_id == b"fmt ":
            code, channels, rate, byte_rate, align, bits = struct.unpack_from(
                "<HHIIHH", raw, body
            )
            mask = 0
            sub = int(code)
            if code == _WAVE_FORMAT_EXTENSIBLE and chunk_size >= 40:
                mask = struct.unpack_from("<I", raw, body + 20)[0]
                sub = int(struct.unpack_from("<H", raw, body + 24)[0])
            fmt = {
                "format_code": int(code),
                "subformat_code": sub,
                "channel_count": int(channels),
                "sample_rate": int(rate),
                "byte_rate": int(byte_rate),
                "block_align": int(align),
                "bits_per_sample": int(bits),
                "channel_mask": int(mask),
                "extensible": code == _WAVE_FORMAT_EXTENSIBLE,
            }
        elif chunk_id == b"data":
            data_offset = body
            declared_data_size = int(chunk_size)
            data_size = int(chunk_size)
            break
        pos = body + chunk_size + (chunk_size % 2)
    if fmt is None:
        raise ValueError(
            f"audio_pcm_format_unsupported: missing fmt chunk: {path}"
        )
    if data_offset is None:
        raise ValueError(
            f"audio_pcm_format_unsupported: missing data chunk: {path}"
        )
    available = max(0, len(raw) - data_offset)
    if data_size > available:
        warnings.append(
            f"data chunk declares {declared_data_size} bytes but only "
            f"{available} are present (truncated) — actual bytes are "
            "authoritative"
        )
        data_size = available
    elif available > data_size:
        warnings.append(
            f"{available - data_size} trailing byte(s) after the declared "
            "data chunk"
        )
    item = max(1, int(fmt["bits_per_sample"]) // 8)
    frame_bytes = item * max(1, int(fmt["channel_count"]))
    return WavInfo(
        path=path,
        sample_rate=int(fmt["sample_rate"]),
        channel_count=int(fmt["channel_count"]),
        bits_per_sample=int(fmt["bits_per_sample"]),
        format_code=int(fmt["format_code"]),
        extensible=bool(fmt["extensible"]),
        frame_count=int(data_size // frame_bytes),
        data_bytes=int(data_size),
        riff_bytes=int(riff_size),
        byte_rate=int(fmt["byte_rate"]),
        block_align=int(fmt["block_align"]),
        channel_mask=int(fmt["channel_mask"]),
        data_offset=int(data_offset),
        subformat_code=int(fmt["subformat_code"]),
        warnings=warnings,
    )


def _subformat_code(raw: bytes) -> int:
    """EXTENSIBLE fmt 的 SubFormat GUID 前 2 字节 -> 基础格式码 (缺失 -> 0)。"""
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        chunk_size = struct.unpack_from("<I", raw, pos + 4)[0]
        body = pos + 8
        if chunk_id == b"fmt " and chunk_size >= 40:
            return int(struct.unpack_from("<H", raw, body + 24)[0])
        pos = body + chunk_size + (chunk_size % 2)
    return 0


def _safe(text: str) -> str:
    out = "".join(
        ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(text)
    )
    return out.strip("_") or "audio"


def _parse_ids(channel_id: str) -> tuple[str, int, int] | None:
    """`camera:s2:c2` -> (source, stream, channel); 不合法返回 None。"""
    parts = str(channel_id).split(":")
    if len(parts) < 3:
        return None
    try:
        stream = int(parts[-2].lstrip("sS") or 0)
        channel = int(parts[-1].lstrip("cC") or 0)
    except ValueError:
        return None
    return ":".join(parts[:-2]), stream, channel


def _unique_stems(channel_ids: Sequence[str]) -> list[str]:
    out: list[str] = []
    for cid in channel_ids:
        parsed = _parse_ids(cid)
        stem = parsed[0] if parsed else str(cid)
        if stem and stem not in out:
            out.append(stem)
    return out
