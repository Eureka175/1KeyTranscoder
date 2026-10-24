#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJI 元数据默认策略回归 (2026-10-25).

用户策略: DJI 源**除 Action 系列外** (Pocket / 无人机) 默认**不保留**
metadata —— 走普通转码路径, 不重建 djmd/dbgi/tmcd; 需要保留时显式
``--preserve-metadata``。Action 系列默认保留 (Gyroflow 增稳依赖 djmd 四元数)。

L1: 纯字符串分类 (mp4box -info 的 `tool:` 机型标签)。
L2: 真实 DJI 样本上 `camera_tool` / `keep_metadata_by_default` 的判定。
"""

from __future__ import annotations

from pathlib import Path

from ..paths import ROOT, record, section

from preservation.dji import is_action_series


def l1_dji_metadata_policy() -> None:
    section("DJI 元数据默认策略 (机型分类)")
    cases = [
        ("DJI OsmoAction4", True, "Action 4"),
        ("DJI Osmo Action 5 Pro", True, "Action 5 (带空格)"),
        ("DJI OsmoAction6", True, "Action 6"),
        ("DJI OsmoPocket3", False, "Pocket 3"),
        ("DJI Mavic4 Pro L3B", False, "Mavic 4 Pro"),
        ("DJI Mini4 Pro", False, "Mini 4 Pro"),
        ("DJI Air3S", False, "Air 3S"),
        ("DJI Avata2", False, "Avata 2"),
        ("DJI OsmoAction", True, "裸 Action 前缀"),
        ("", False, "空字符串"),
        (None, False, "None (识别失败)"),
    ]
    for tool, expected, label in cases:
        got = is_action_series(tool)
        record(f"djipol: {label} → {'keep' if expected else 'skip'}",
               got == expected, f"tool={tool!r} action={got}")
    # 默认策略是"仅 Action 保留": 非 Action 与 None 都必须落到 skip, 不能
    # 因为"识别不到"就回到保留 (那会让 Pocket/无人机又悄悄重建元数据)。
    record("djipol: 识别失败默认不保留 (fail-closed)",
           is_action_series(None) is False, "None -> False")


def _find_sample(*candidates: str) -> Path | None:
    for rel in candidates:
        p = ROOT / rel if not rel.startswith("I:") else Path(rel)
        if p.is_file():
            return p
    return None


def l2_dji_metadata_policy() -> None:
    section("DJI 元数据默认策略 (真实样本机型识别)")
    from preservation.dji import camera_tool, keep_metadata_by_default
    from preservation.gpac import GpacContainerBackend

    gpac = GpacContainerBackend()
    action = _find_sample(
        r"testsets\action4_4x3_long\DJI_20260922101750_0031_D.MP4",
        r"I:\2026.09.24\Mavic4P\DJI_20260921164928_0296_D.MP4",
    )
    if action is None:
        record("djipol-l2: Action 样本可用", False, "no sample file found")
        return
    tool = camera_tool(gpac, action)
    keep, keep_tool = keep_metadata_by_default(gpac, action)
    record(
        f"djipol-l2: Action 机型识别 ({action.name})",
        tool is not None and keep is True and keep_tool == tool
        and is_action_series(tool),
        f"tool={tool!r} keep={keep}",
    )

    # 非 Action 样本 (Pocket / Mavic) 在 I: 上; 不存在时显式跳过而不是假设通过。
    non_action = _find_sample(
        r"I:\2026.09.24\Pocket3\DJI_20260922193237_0001_D.MP4",
        r"I:\2026.09.24\Mavic4P\DJI_20260921164928_0296_D.MP4",
    )
    if non_action is None:
        record("djipol-l2: 非 Action 样本 (Pocket/Mavic) 不存在 → 跳过", True,
               "skipped: sample not present")
        return
    tool2 = camera_tool(gpac, non_action)
    keep2, _ = keep_metadata_by_default(gpac, non_action)
    record(
        f"djipol-l2: 非 Action 机型默认不保留 ({non_action.name})",
        tool2 is not None and keep2 is False,
        f"tool={tool2!r} keep={keep2}",
    )
