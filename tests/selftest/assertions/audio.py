#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频专用断言/观测原语: impulse 位置、输出摘要 hash、timeline 对齐换算。

这些函数被时间轴 / 路由 / WAV / 混音多个 suite 共用, 因此从测试实现里抽
出来; 语义与原实现逐字一致。

说明: 原 `full_autotest.py` 里**没有**通用的 `assert_equal` / `assert_close`
之类原语 (断言都是就地 `record(name, cond, detail)`), 因此这里不做
"为了对称而存在"的通用断言模块。
"""

from __future__ import annotations

import hashlib
from typing import Any
from pathlib import Path
from ..fixtures.audio import P3A_SR

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def _p3a_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _p3a_impulse_map(arr: Any, threshold: float = 0.05) -> list[list[int]]:
    """逐输出声道 -> impulse 位置列表 (确定性断言用)。"""
    out: list[list[int]] = []
    for ch in range(arr.shape[1]):
        idx = np.nonzero(np.abs(arr[:, ch]) > threshold)[0]
        out.append([int(i) for i in idx[:4]])
    return out


def _p3a_impulse_timeline(
    arr: Any, timeline: Any, threshold: float = 0.05,
) -> list[list[int]]:
    """同 `_p3a_impulse_map`, 但把数组下标换算成 **timeline 样本位置**。

    输出窗口起点未必是 0 (offset 会把窗口推到负数起点), 因此断言"两轨是否
    对齐"必须比较 timeline 位置而不是数组下标。
    """
    base = int(getattr(timeline, "start_sample", 0) or 0)
    return [
        [i + base for i in positions]
        for positions in _p3a_impulse_map(arr, threshold)
    ]


def set_p3a_offset(plan: Any, channel_id: str, offset: float) -> None:
    """给某声道设一个"已测量"的固定整数样本 offset (Phase 1 模型语义)。"""
    from core.audio_models import AudioSyncResult, SyncStatus

    for track in plan.input_tracks:
        for ch in track.channels:
            if ch.id == channel_id:
                track.set_sync(AudioSyncResult(
                    status=SyncStatus.SUCCESS,
                    offset_samples=float(offset),
                    offset_ms=float(offset) * 1000.0 / P3A_SR,
                    source="channel_sync_report",
                ))
                return
