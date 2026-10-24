#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2CH 有效 (4CH 中 2 条静音) 默认跳过对齐的策略回归 (2026-10-25).

用户策略: 程序探测到音频流 >= 3 但只有 2 条健康 (其余静音) 时, **默认不做
对齐操作**。实现是 `core.channel_sync.skip_two_channel_only()` + 新的
`two_channel_only` 状态; 本 suite 钉住"判定条件"与"默认开启、可覆盖"。
"""

from __future__ import annotations

from ..paths import record, section

from core.channel_sync import DEFAULTS, _BOOL_KEYS, skip_two_channel_only


def l1_two_channel_only_policy() -> None:
    section("2CH 有效默认跳过对齐 (skip_two_channel_only)")
    cases = [
        (4, 2, True, True, "4 条中 2 条健康 → 跳过"),
        (3, 2, True, True, "3 条中 2 条健康 → 跳过"),
        (4, 3, True, False, "4 条中 3 条健康 → 正常处理"),
        (4, 4, True, False, "全健康 → 正常处理"),
        (4, 1, True, False, "仅 1 条健康 → 交给锚点/失败路径"),
        (2, 2, True, False, "2 条音频流 (2CH 布局) → 不做该判定"),
        (4, 2, False, False, "显式关闭覆盖 → 恢复旧行为"),
    ]
    for n_tracks, n_healthy, enabled, expected, label in cases:
        got = skip_two_channel_only(n_tracks, n_healthy, enabled)
        record(f"2chpol: {label}", got == expected,
               f"tracks={n_tracks} healthy={n_healthy} enabled={enabled} "
               f"-> {got}")
    record("2chpol: DEFAULTS 默认开启", DEFAULTS.get("skip_two_channel_only") is True,
           repr(DEFAULTS.get("skip_two_channel_only")))
    record("2chpol: 选项可经 effective_opts 覆盖 (在 _BOOL_KEYS 中)",
           "skip_two_channel_only" in _BOOL_KEYS)
