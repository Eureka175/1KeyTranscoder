#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tkhd 轨道启用标志 / alternate group 归一 —— 生产容器契约回归.

背景（2026-10-24, 4CH 长素材交付实测）:
  * Sony 源每轨 ``tkhd flags=7`` (enabled|in_movie|in_preview) 且
    ``alternate_group=0``;
  * ffmpeg 的多音轨 MP4 给第二条及之后的音轨写 ``flags=2``（未启用）并把
    所有音轨放进 ``alternate_group=1..N``；部分硬件 wrapper 也一样;
  * ISO/IEC 14496-12 把 alternate group 定义为"同一轨的备选版本"（多语言
    音轨的典型用法），所以 Premiere 按规范只显示一条可选音轨，而 MediaInfo
    不解释该语义、仍列出四条 —— 缺陷只在 NLE 里可见。

修复是 container 层事实恢复: ``preservation/isobmf.py`` 的
``normalize_track_flags()`` 在最终成品上把 tkhd flags/alternate_group 归一。
生产路径（Sony pipeline / 透明模式 / classic 音频替换 / DJI / 硬件容器
重封装）都在写出最终文件后调用它；本 suite 把该契约钉进回归。

L1: 纯 Python 构造的 ISOBMFF（moov 在 mdat 前/后、tkhd v0/v1）验证解析、
    归一、幂等、字节不变性（mdat payload 与文件大小不变）。
L2: 真实 muxer（ffmpeg / MP4Box）写出的多音轨文件上验证同一性质。
"""

from __future__ import annotations

import hashlib
import math
import struct
import wave
from pathlib import Path

from ..paths import (
    FFMPEG, MB, ROOT, WORK, ffprobe_json, record, section, sh,
)

from preservation import isobmf


# ---------------------------------------------------------------------------
# synthetic ISOBMFF helpers (L1)
# ---------------------------------------------------------------------------

def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _fullbox(typ: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(typ, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _hdlr(handler: bytes) -> bytes:
    return _fullbox(
        b"hdlr", 0, 0,
        struct.pack(">I", 0) + handler + b"\x00" * 12 + b"handler\x00",
    )


def _tkhd_bytes(track_id: int, *, version: int, flags: int,
                alternate_group: int) -> bytes:
    matrix = struct.pack(">9i", 0x00010000, 0, 0,
                         0, 0x00010000, 0,
                         0, 0, 0x40000000)
    tail = (struct.pack(">hhHH", 0, alternate_group, 0x0100, 0)
            + matrix + struct.pack(">II", 0, 0))
    if version == 0:
        body = (struct.pack(">IIII", 0, 0, track_id, 0)
                + struct.pack(">I", 0) + b"\x00" * 8 + tail)
    else:
        body = (struct.pack(">QQ", 0, 0)
                + struct.pack(">II", track_id, 0)
                + struct.pack(">Q", 0) + b"\x00" * 8 + tail)
    return _fullbox(b"tkhd", version, flags, body)


def _trak(track_id: int, *, version: int, flags: int,
          alternate_group: int, handler: bytes) -> bytes:
    return _box(
        b"trak",
        _tkhd_bytes(track_id, version=version, flags=flags,
                    alternate_group=alternate_group)
        + _box(b"mdia", _hdlr(handler)),
    )


def _synthetic_mp4(path: Path, *, moov_first: bool,
                   versions: tuple[int, ...] = (0, 0)) -> bytes:
    """Write ftyp + [mdat] + moov; returns the mdat payload bytes."""
    mdat = bytes(range(256)) * 37
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isomiso2mp41")
    traks = b"".join(
        _trak(1 + i, version=versions[i],
              flags=3 if i == 0 else 2,
              alternate_group=0 if i == 0 else 1,
              handler=b"vide" if i == 0 else b"soun")
        for i in range(len(versions))
    )
    moov = _box(b"moov", traks)
    path.write_bytes(
        ftyp + (moov + _box(b"mdat", mdat) if moov_first
                else _box(b"mdat", mdat) + moov)
    )
    return mdat


def _mdat_sha(path: Path, expected: bytes) -> str:
    """sha256 of the mdat payload; empty string if it does not match."""
    roots = isobmf.root_boxes(path)
    mdat = next((b for b in roots if b.type == "mdat"), None)
    if mdat is None:
        return ""
    with path.open("rb") as f:
        f.seek(mdat.data_offset)
        payload = f.read(mdat.size - mdat.header)
    if payload != expected:
        return ""
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------

def l1_track_flags() -> None:
    section("tkhd 轨道标志 / alternate group (纯 ISOBMFF)")
    d = WORK / "tkhd_l1"
    d.mkdir(parents=True, exist_ok=True)

    for label, moov_first in (("moov 在 mdat 之后", False),
                              ("moov 在 mdat 之前 (faststart)", True)):
        p = d / f"case_{int(moov_first)}.mp4"
        mdat = _synthetic_mp4(p, moov_first=moov_first)
        before = isobmf.track_flags(p)
        record(
            f"tkhd 解析: {label}",
            len(before) == 2
            and before[0]["track_id"] == 1
            and before[1]["track_id"] == 2
            and before[1]["flags"] == 2
            and before[1]["alternate_group"] == 1
            and before[1]["handler"] == "soun",
            repr(before),
        )
        size_before = p.stat().st_size
        mdat_before = _mdat_sha(p, mdat)
        descs = isobmf.normalize_track_flags(p)
        after = isobmf.track_flags(p)
        record(
            f"tkhd 归一: {label} 每轨 flags=7 / alt=0",
            len(after) == 2
            and all(t["flags"] == 7 and t["alternate_group"] == 0
                    for t in after)
            and len(descs) == 2,
            f"descs={descs}",
        )
        record(
            f"tkhd 归一: {label} 文件大小/mdat 字节不变",
            p.stat().st_size == size_before
            and _mdat_sha(p, mdat) == mdat_before,
            f"size {size_before}->{p.stat().st_size} mdat_sha={mdat_before[:12]}",
        )
        again = isobmf.normalize_track_flags(p)
        record(
            f"tkhd 归一: {label} 幂等 (第二次无改动)",
            again == [],
            repr(again),
        )

    # tkhd v1 (64-bit time fields)
    p1 = d / "case_v1.mp4"
    _synthetic_mp4(p1, moov_first=False, versions=(0, 1))
    descs = isobmf.normalize_track_flags(p1)
    after = isobmf.track_flags(p1)
    record(
        "tkhd v1: 归一 flags=7 / alt=0 且 track id 正确",
        len(after) == 2
        and all(t["flags"] == 7 and t["alternate_group"] == 0
                for t in after)
        and [t["track_id"] for t in after] == [1, 2],
        f"descs={descs} after={after}",
    )


# ---------------------------------------------------------------------------
# L2 (real muxers)
# ---------------------------------------------------------------------------

def _write_wav(path: Path, freq: float, seconds: float = 0.4) -> None:
    rate = 48000
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _mp4box() -> Path | None:
    candidates = [MB, ROOT / "tools" / "GPAC" / "mp4box.exe"]
    return next((p for p in candidates if p.is_file()), None)


def _streamhash(path: Path) -> str:
    r = sh(FFMPEG, "-v", "error", "-i", path, "-map", "0:a",
           "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-",
           timeout=120)
    return (r.stdout or "").strip()


def l2_track_flags() -> None:
    section("tkhd 轨道标志 (真实 muxer 输出)")
    if not FFMPEG.is_file():
        record("tkhd L2: ffmpeg 可用", False, f"missing {FFMPEG}")
        return
    d = WORK / "tkhd_l2"
    d.mkdir(parents=True, exist_ok=True)
    a1, a2 = d / "a1.wav", d / "a2.wav"
    _write_wav(a1, 440.0)
    _write_wav(a2, 880.0)

    # ---- ffmpeg 多音轨 MP4（用户实际遇到的路径）
    ff = d / "ff_multi_audio.mp4"
    r = sh(FFMPEG, "-v", "error", "-y", "-i", a1, "-i", a2,
           "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s24be",
           "-movflags", "+faststart", ff, timeout=120)
    if r.returncode != 0 or not ff.is_file():
        record("tkhd L2: ffmpeg 多音轨样本构建", False,
               f"rc={r.returncode} {r.stderr[-300:]}")
    else:
        before = isobmf.track_flags(ff)
        hash_before = _streamhash(ff)
        descs = isobmf.normalize_track_flags(ff)
        after = isobmf.track_flags(ff)
        hash_after = _streamhash(ff)
        audios = [s for s in ffprobe_json(ff).get("streams", [])
                  if s.get("codec_type") == "audio"]
        record(
            "ffmpeg 多音轨: 归一后全部 flags=7 / alt=0, 音轨数不变",
            len(after) == len(before) >= 2
            and all(t["flags"] == 7 and t["alternate_group"] == 0
                    for t in after)
            and len(audios) == len(before),
            f"before={[(t['track_id'], t['flags'], t['alternate_group']) for t in before]} "
            f"descs={descs}",
        )
        record(
            "ffmpeg 多音轨: tkhd 补丁不动音频 payload",
            hash_before == hash_after and "0,a" in hash_before,
            f"{hash_before[-60:]} == {hash_after[-60:]}",
        )

    # ---- MP4Box 多音轨（生产 muxer）
    mb = _mp4box()
    if mb is None:
        record("tkhd L2: MP4Box 可用", False, "no mp4box.exe")
        return
    gp = d / "gpac_multi_audio.mp4"
    r = sh(mb, "-new", gp, "-add", f"{a1}#audio", "-add", f"{a2}#audio",
           timeout=180)
    if r.returncode != 0 or not gp.is_file():
        record("tkhd L2: MP4Box 多音轨样本构建", False,
               f"rc={r.returncode} {r.stderr[-300:]}")
        return
    descs = isobmf.normalize_track_flags(gp)
    after = isobmf.track_flags(gp)
    audios = [s for s in ffprobe_json(gp).get("streams", [])
              if s.get("codec_type") == "audio"]
    record(
        "MP4Box 多音轨: 归一后全部 flags=7 / alt=0, 音轨数不变",
        len(after) >= 2
        and all(t["flags"] == 7 and t["alternate_group"] == 0
                for t in after)
        and len(audios) == len(after),
        f"descs={descs}",
    )
