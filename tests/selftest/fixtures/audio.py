#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性音频素材 fixture.

只有被多个 suite 复用的素材才抽到这里 (音频模型 / 选择 / 时间轴 / 路由 /
WAV / 混音 / sync 共用)。测试代码只调用 `_p3a_*` / `_p3b_*` / `_sync_*`,
不再自己 `tempfile` / 开 ffmpeg / 手写 WAV。
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
) -> bool:
    """`video(h264) + N 条 mono PCM s16le`, 每条音轨用不同频率的 sine。

    容器 index 与音频序号故意错开 (video=0, audio=1..N), 与真实 A7M5 形态
    一致 —— 用来钉住"`-map` 选择器用 `audio_position`, 不是 `stream_index`"。
    """
    from ..paths import FFMPEG, sh

    args: list[str] = [
        "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s=64x36:r=10:d={seconds}",
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
) -> bool:
    """`video(h264) + 1 条 N 声道 PCM s16le` 的确定性 MOV。

    用于验证"取单条流的**部分**声道": 只有真正的多声道流才能构造这个场景
    —— mono 流取第 0 声道其实就是整流 (那是 copy, 不是子集)。
    """
    from ..paths import FFMPEG, sh

    if channels > 4:
        return False
    args = [
        "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s=64x36:r=10:d={seconds}",
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
