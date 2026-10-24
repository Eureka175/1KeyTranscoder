"""延时补偿音频替换 (GPAC 重封装): 把修正后的音频流替换回容器。

供经典路径 (硬件/x265) 使用: 视频轨从编码产物原生复制, 音频轨改用
channel-sync 修正后的 per-stream 中间文件; 随后执行 stts 时基修复
(与 DJI 重建同款, 消除 rigaya 毫秒量化/GPAC 截断的时长偏差)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import isobmf
from .dji import track_manifest
from .gpac import GpacContainerBackend


def remux_replace_audio(
    *,
    gpac: GpacContainerBackend,
    video_src: Path,
    fixed_files: list[Path],
    dst: Path,
    log: Callable[[str], None] = print,
) -> None:
    """video_src 的视频轨 + fixed_files 的音频轨 -> dst (GPAC -new)。"""
    movie_ts, tracks = track_manifest(gpac, video_src)
    video_id = next(
        (t["id"] for t in tracks if t["handler"] == "vide"), None
    )
    if video_id is None:
        raise RuntimeError(
            f"no video track in {video_src} for audio replacement"
        )
    gpac.movie_timescale = movie_ts
    adds = [f"{video_src}#video"]
    for fixed in fixed_files:
        adds.append(f"{fixed}#audio")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists():
            dst.unlink()
    except OSError:
        pass
    log("remuxing video + fixed audio tracks with MP4Box...")
    gpac.mux_new(dst, adds)
    # stts 时基修复 (与 DJI 重建同款): rigaya 中间文件的毫秒量化 elst
    # 会被 GPAC 截断轨道时长, 从 stts 反推恢复精确时长。
    for desc in isobmf.patch_track_durations(dst, movie_ts, from_stts=True):
        log(desc)
    mv_desc = isobmf.patch_movie_duration(dst)
    if mv_desc:
        log(mv_desc)
    # 音频 sample entry 的自洽性护栏。
    #
    # 这里**不做**"换回源条目"的补丁: 此时音频轨用的是 ffmpeg 的 `.mov`
    # 中间文件, 条目是 QuickTime `in24` (合法写法, `samplesize=16` 是约定值,
    # 位深由 fourcc 决定), 与源的 `ipcm` 长度不同, 换不了。实测
    # `place_final` 的 MOV→MP4 容器重封装会**原样保留** `in24`, 不会把它翻成
    # 自相矛盾的 `ipcm`, 所以这条路径是安全的。
    #
    # 真正出问题的是**透明模式**的 `restore_metadata` 链路 (flatten + set_brand
    # XAVC 之后 GPAC 把 in24 翻成 ipcm 却照搬 samplesize=16) —— 那里已经用
    # `patch_audio_sample_entries` 换回了源条目。这里只做最后一道自洽检查:
    # 条目自相矛盾是无条件错误, 宁可让这次转码失败也不交付白噪音。
    from .sample_entry import (
        audio_entry_consistency,
        normalise_audio_entry_bit_depth,
        read_audio_entries,
    )

    bad = [i + 1 for i, e in enumerate(read_audio_entries(dst))
           if not audio_entry_consistency(e["bytes"])["consistent"]]
    if bad:
        fixed = normalise_audio_entry_bit_depth(dst, log=log)
        still = [i + 1 for i, e in enumerate(read_audio_entries(dst))
                 if not audio_entry_consistency(e["bytes"])["consistent"]]
        if still:
            raise RuntimeError(
                f"音频 sample entry 自相矛盾且无法校正: 轨 {still}")
        log(f"WARNING 音频 sample entry 曾自相矛盾 (轨 {bad}), 已按 pcmC 校正")

    # tkhd 轨道启用标志 / alternate group 归一。
    #
    # 这条路径的成品由 GPAC `-new` + 音频中间文件拼出。多音轨容器交给 NLE
    # 时, ffmpeg/GPAC 默认的 flags=2 + alternate_group 会让 Premiere 按
    # "备选组"语义只显示一条音轨 (详见 isobmf.normalize_track_flags 说明);
    # 源文件口径是每轨 flags=7 / alternate_group=0, 所以这是在恢复容器事实。
    try:
        for desc in isobmf.normalize_track_flags(dst):
            log(f"track flags: {desc}")
    except (OSError, RuntimeError) as exc:
        log(f"WARNING track-flag normalization skipped: {exc}")
