#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性音频素材 fixture.

只有被多个 suite 复用的素材才抽到这里 (音频模型 / 选择 / 时间轴 / 路由 /
WAV / 混音 / sync 共用)。测试代码只调用 `_p3a_*` / `_p3b_*` / `_sync_*` /
`_make_*`, 不再自己 `tempfile` / 开 ffmpeg / 手写 WAV。

v0.8.0 追加的是"格式感知 + 外挂音频"用的容器素材 (`_make_video_only` /
`_make_audio_file` / `_make_av_compressed` / `_transcode_audio`) 与
`_video_elementary_hash()` —— 它们只是素材与度量, 不含任何被测逻辑。
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

P3A_SR = 48000


def _raw_audio_stream(
    index: int,
    *,
    channels: int | None = 1,
    codec: str = "pcm_s24le",
    sample_rate: Any = "48000",
    sample_fmt: Any = "s32",
    layout: str | None = None,
    duration: Any = "8.000000",
    bit_rate: Any = "1152000",
    tags: dict | None = None,
    disposition: dict | None = None,
    codec_long_name: str = "PCM signed 24-bit little-endian",
    tag_string: str = "in24",
) -> dict:
    """合成一条 ffprobe 风格 audio stream dict (L1 音频模型用例用)。

    默认值取真实 Sony A7M5 4CH 素材的实测形态 (pcm_s24be + sample_fmt
    s32 + 无 channel_layout); 传入 None 表示**该字段缺失**, 用于覆盖
    §10 情况 F / T7 / T8。
    """
    st: dict[str, Any] = {"index": index, "codec_type": "audio",
                          "codec_name": codec}
    if codec_long_name:
        st["codec_long_name"] = codec_long_name
    if channels is not None:
        st["channels"] = channels
    if sample_rate is not None:
        st["sample_rate"] = sample_rate
    if sample_fmt is not None:
        st["sample_fmt"] = sample_fmt
    if layout is not None:
        st["channel_layout"] = layout
    if duration is not None:
        st["duration"] = duration
    if bit_rate is not None:
        st["bit_rate"] = bit_rate
    if tag_string is not None:
        st["codec_tag_string"] = tag_string
    if tags is not None:
        st["tags"] = tags
    if disposition is not None:
        st["disposition"] = disposition
    return st


def _p3a_fixture(
    path: Path, channels: int, samples: int, impulses: dict[int, int],
    *, sample_rate: int = P3A_SR, amplitudes: dict[int, float] | None = None,
) -> Path:
    """确定性 impulse 素材 (浮点 WAV, bit-exact 往返)。

    `impulses[channel] = 采样下标`; 幅度默认 `0.5 + 0.1 * channel` (每个声道
    可区分), 便于断言"哪个声道的数据到了哪个输出声道"。
    """
    from core.audio_wav import WavFormat, write_wav

    amp = dict(amplitudes or {})
    x = np.zeros((samples, channels), dtype=np.float32)
    for ch, at in impulses.items():
        x[at, ch] = amp.get(ch, 0.5 + 0.1 * ch)
    write_wav(
        path, [x], sample_rate=sample_rate, channel_count=channels,
        sample_format=WavFormat.FLOAT32, frame_count=samples, overwrite=True,
    )
    return path


def _p3b_fixture(path: Path, channels: int, samples: int,
                 values: dict[int, float], *, at: int = 0,
                 sample_rate: int = P3A_SR) -> Path:
    """单点确定性常量素材: `values[channel] = 幅度` 落在样本 `at`。"""
    from core.audio_wav import WavFormat, write_wav

    x = np.zeros((samples, channels), dtype=np.float32)
    for ch, value in values.items():
        x[at, ch] = float(value)
    write_wav(path, [x], sample_rate=sample_rate, channel_count=channels,
              sample_format=WavFormat.FLOAT32, frame_count=samples,
              overwrite=True)
    return path


def _sync_content(
    path: Path, delay: int, *, samples: int = 48000,
    content: Any = None, baseline: int = 9600,
    sample_rate: int = P3A_SR, channels: int = 1, channel: int = 0,
) -> Path:
    """确定性**共享内容**素材: 同一段伪噪声整体平移 `delay` 个样本。

    `delay > 0` = 该路**晚到** (内容出现在更大的样本下标上) —— 这正是
    `core/channel_sync` 的 `delay_samples` 符号约定, 因此 fixture 的
    "delay" 与被测语义同向, 测试里不需要任何符号换算。

    为什么不用单个 impulse: GCC-PHAT 的帧级 RMS 门
    (`frame_min_rms_dbfs`) 会正确地把稀疏 impulse 判为"不可测" ——
    这是既有算法的真实行为, 不该为了造测试而绕过它。伪噪声是既有
    channel-sync 测试与真实素材共同的可测形态。
    """
    from core.audio_wav import WavFormat, write_wav

    if content is None:
        rng = np.random.default_rng(20260915)
        content = (rng.standard_normal(
            max(1, int(samples) - 2 * int(baseline))
        ) * 0.25).astype(np.float32)
    x = np.zeros((int(samples), int(channels)), dtype=np.float32)
    base = int(baseline) + int(delay)
    lo = max(0, base)
    hi = min(int(samples), base + len(content))
    if hi > lo:
        x[lo:hi, int(channel)] = content[lo - base: hi - base]
    write_wav(
        path, [x], sample_rate=int(sample_rate),
        channel_count=int(channels),
        sample_format=WavFormat.FLOAT32, frame_count=int(samples),
        overwrite=True,
    )
    return path


# ---------------------------------------------------------------------------
# 真实容器素材 (ffmpeg 合成; retention / encode 两个 suite 共用)
# ---------------------------------------------------------------------------

def _make_av(
    dst: Path, streams: int = 4, *, seconds: int = 1,
    size: str = "1280x720", rate: int = 10,
) -> bool:
    """`video(h264) + N 条 mono PCM s16le`, 每条音轨用不同频率的 sine。

    容器 index 与音频序号故意错开 (video=0, audio=1..N), 与真实 A7M5 形态
    一致 —— 用来钉住"`-map` 选择器用 `audio_position`, 不是 `stream_index`"。

    ⚠️ 默认 **1280x720**: 这条 fixture 也会被**真实生产入口** (`1kt.py`)
    消费, 而生产 x265 profile 带 `ctu=64` + `level-idc=6.2`, 对小于一个
    CTU 的画面会直接报 "Picture size must be at least one CTU"。因此尺寸
    必须是真的能编码的画面, 不能是 64x36 那种"只够 ffprobe 看"的占位。
    """
    from ..paths import FFMPEG, sh

    args: list[str] = [
        "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s={size}:r={rate}:d={seconds}",
    ]
    for i in range(streams):
        args += [
            "-f", "lavfi", "-i",
            f"sine=frequency={440 + 110 * i}:sample_rate=48000:"
            f"duration={seconds}",
        ]
    args += ["-map", "0:v", "-c:v", "libx264", "-preset", "ultrafast",
             "-crf", "40", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]
    for i in range(streams):
        args += ["-map", f"{i + 1}:a", f"-ac:a:{i}", "1"]
    r = sh(FFMPEG, *args, dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


def _make_av_channels(
    dst: Path, channels: int, *, seconds: int = 1,
    size: str = "1280x720", rate: int = 10,
) -> bool:
    """`video(h264) + 1 条 N 声道 PCM s16le` 的确定性 MOV。

    用于验证"取单条流的**部分**声道": 只有真正的多声道流才能构造这个场景
    —— mono 流取第 0 声道其实就是整流 (那是 copy, 不是子集)。

    ⚠️ 尺寸同样必须是真能编码的画面 (理由见 `_make_av`)。
    """
    from ..paths import FFMPEG, sh

    if channels > 4:
        return False
    args = [
        "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s={size}:r={rate}:d={seconds}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
        "-pix_fmt", "yuv420p",
        "-af", f"pan={channels}c|c0=c0|c1=c0|c2=c0|c3=c0",
        "-c:a", "pcm_s16le", "-ac", str(channels),
    ]
    r = sh(FFMPEG, *args, dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


# ---------------------------------------------------------------------------
# v0.8.0: 格式感知 / 外挂音频 素材
# ---------------------------------------------------------------------------

def _make_video_only(
    dst: Path, *, seconds: int = 1, size: str = "320x240", rate: int = 10,
) -> bool:
    """只有视频流的确定性 MP4 (外挂音频用例的宿主)。"""
    from ..paths import FFMPEG, sh

    r = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s={size}:r={rate}:d={seconds}",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
           "-pix_fmt", "yuv420p", dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


def _make_audio_file(
    dst: Path, channels: int = 1, *, seconds: int = 1, freq: int = 440,
    codec: str | None = None,
) -> bool:
    """外挂音频文件 (扩展名决定编码: .wav -> PCM, .opus -> libopus, .aac -> aac)。

    ⚠️ 这不是"第二套 codec 表": 它只是**造素材**的测试辅助, 编码选择由
    扩展名决定, 与生产代码里的 `AudioEncodeFormat` 表无关。
    """
    from ..paths import FFMPEG, sh

    suffix = Path(dst).suffix.lower()
    chosen = codec or {
        ".opus": "libopus", ".aac": "aac", ".flac": "flac",
    }.get(suffix, "pcm_s16le")
    args: list[str] = [
        "-v", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={seconds}",
    ]
    if channels > 1:
        args += ["-af", "pan=" + f"{channels}c" + "".join(
            f"|c{i}=c0" for i in range(channels))]
    args += ["-ac", str(channels), "-c:a", chosen]
    if chosen in ("libopus", "aac"):
        args += ["-b:a", "96k"]
    r = sh(FFMPEG, *args, dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


def _make_av_compressed(
    dst: Path, codec: str = "aac", *, channels: int = 1, seconds: int = 1,
    size: str = "320x240", rate: int = 10, bitrate: str = "192k",
) -> bool:
    """`video(h264) + 1 条 N 声道 compressed 音频` 的确定性 MP4。

    用于验证"compressed 默认不 alignment / 显式 alignment 时解码重编码":
    只有真的 compressed 流才能构造这个场景 (PCM 走的是另一条分支)。
    """
    from ..paths import FFMPEG, sh

    encoder = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame"}.get(
        codec, codec)
    args: list[str] = [
        "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s={size}:r={rate}:d={seconds}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
        "-pix_fmt", "yuv420p",
    ]
    if channels > 1:
        args += ["-af", "pan=" + f"{channels}c" + "".join(
            f"|c{i}=c0" for i in range(channels))]
    args += ["-ac", str(channels), "-c:a", encoder, "-b:a", bitrate]
    r = sh(FFMPEG, *args, dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


def _transcode_audio(
    dst: Path, src: Path, codec: str, *, bitrate: str = "96k",
) -> bool:
    """把已有音频文件重编码成 compressed 格式 (外挂 Opus/AAC 素材)。"""
    from ..paths import FFMPEG, sh

    encoder = {"aac": "aac", "opus": "libopus"}.get(codec, codec)
    r = sh(FFMPEG, "-v", "error", "-y", "-i", src, "-vn",
           "-c:a", encoder, "-b:a", bitrate, dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0


def _video_elementary_hash(path: Path) -> str:
    """视频**基本流** sha256 (与 Phase 4B/4C 回归同一口径)。

    音频无论怎么处理, 这个值都必须不变 —— 那是"视频域一行未改"的机器证据。
    """
    from ..paths import FFMPEG, sh

    r = sh(FFMPEG, "-v", "error", "-i", path, "-map", "0:v:0",
           "-c", "copy", "-f", "hash", "-hash", "sha256", "-", timeout=600)
    if r.returncode != 0:
        return ""
    text = (r.stdout or "").strip()
    return text.split("=")[-1] if "=" in text else text


def _make_av_10bit(
    dst: Path, *, seconds: int = 1, size: str = "320x240", rate: int = 10,
) -> bool:
    """`video(HEVC 4:2:0 10-bit) + 1 条 mono PCM` 的确定性 MP4。

    硬件**解码**白名单只认 runtime-proven 的 `(backend, codec, chroma, depth)`
    组合 —— nvenc/qsv 各有一条 4:2:0/10bit 的 HEVC 记录, 因此"硬件解码仍然可用"
    这件事只能用 10-bit HEVC 素材来验证 (这不是测试偏好, 是白名单的事实)。
    """
    from ..paths import FFMPEG, sh

    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi", "-i",
           f"testsrc2=size={size}:rate={rate}:duration={seconds}",
           "-f", "lavfi", "-i",
           f"sine=frequency=440:sample_rate=48000:duration={seconds}",
           "-map", "0:v", "-map", "1:a",
           "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt",
           "yuv420p10le", "-c:a", "pcm_s16le", "-ac", "1", dst, timeout=900)
    return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0
