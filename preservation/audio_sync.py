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
