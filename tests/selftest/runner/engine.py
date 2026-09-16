#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite 注册表 + 执行器.

职责边界 (严格):
  * registry  : suite 名 -> [(显示名, 可调用对象)] 的声明与查询;
  * executor  : 按 level 展开 suite, 顺序执行, 兜住异常。

本模块**不**包含任何音频算法 / ffmpeg fixture / 具体测试数据 / AudioPlan
构造 / channel_sync 断言 —— 那些全部在 fixtures/ 与 suites/ 下。
"""

from __future__ import annotations

import time
from typing import Callable

from .. import paths

SuiteFn = Callable[[], None]

#: level -> 实际执行的 suite 序列。`all` 是 `full` 的既有别名 (未改变)。
LEVELS: dict[str, list[str]] = {
    "unit": ["unit"],
    "toolchain": ["unit", "toolchain"],
    "full": ["unit", "toolchain", "full"],
    "all": ["unit", "toolchain", "full"],
}

#: 兼容别名 (CLI 里 `--level all` 与 `--level full` 等价)。
LEVEL_ALIASES: dict[str, str] = {"all": "full"}


def build_registry() -> dict[str, list[tuple[str, SuiteFn]]]:
    """构建 suite 注册表.

    这里是**唯一**的"测试发现"入口。每个 suite 的显示名与重构前逐字一致,
    因此报告里的用例名不变 (外部若按名字过滤报告, 不会被打断)。
    显示名沿用旧命名 (`audio model v0.7.1`, `channel-sync P1` …) —— 命名
    体系迁移是独立议题, 不属于本次结构重构。
    """
    from ..suites import (
        audio_encode, audio_integration, audio_mix, audio_model,
        audio_retention, audio_route, audio_selection, audio_sync,
        audio_timeline, channel_sync, cli, codecs, core, hardware, pipeline,
        production_output, toolchain,
    )

    return {
        "unit": [
            ("color/token", core.l1_color),
            ("caps", core.l1_caps),
            ("hw/plan/classify/flags", core.l1_hw),
            ("probe/paths", core.l1_probe_paths),
            ("classifier/scaling", core.l1_classifier_scaling),
            ("gpac/dji", core.l1_gpac_dji),
            ("x265 P0", codecs.l1_x265),
            ("quality/versions", core.l1_quality),
            ("channel-sync", channel_sync.l1_channel_sync),
            ("channel-sync P1", channel_sync.l1_channel_sync_p1),
            ("audio model v0.7.1", audio_model.l1_audio_model),
            ("audio select/map v0.7.1", audio_selection.l1_audio_selection),
            ("audio timeline v0.7.1", audio_timeline.l1_audio_timeline),
            ("audio sync offset v0.7.1", audio_timeline.l1_audio_sync_offset),
            ("audio route v0.7.1", audio_route.l1_audio_route),
            ("audio wav export v0.7.1", audio_route.l1_audio_wav_export),
            ("audio chunk invariance v0.7.1",
             audio_route.l1_audio_chunk_invariance),
            ("audio mix v0.7.1", audio_mix.l1_audio_mix),
            ("audio mix invariance v0.7.1",
             audio_mix.l1_audio_mix_invariance),
            ("audio sync (arbitrary reference)", audio_sync.l1_audio_sync),
            ("audio retention/mp4 v0.8 (Phase 4A)",
             audio_retention.l1_audio_retention),
            ("audio encode/compose v0.8 (Phase 4B)",
             audio_encode.l1_audio_encode),
            ("audio production output v0.8 (Phase 4C)",
             production_output.l1_production_output),
            ("av1", codecs.l1_av1),
            ("cli v0.6.2", cli.l1_cli_v062),
        ],
        "toolchain": [("toolchain", toolchain.l2_toolchain)],
        "full": [
            ("pipeline", pipeline.l3_pipeline),
            ("audio model probe v0.7.1",
             audio_integration.l3_audio_probe),
            ("audio select/map v0.7.1",
             audio_integration.l3_audio_selection),
            ("audio pcm/wav v0.7.1",
             audio_integration.l3_audio_process),
            ("audio mix v0.7.1", audio_integration.l3_audio_mix),
            ("audio sync (arbitrary reference)",
             audio_integration.l3_audio_sync),
            ("channel-sync P1 E2E", hardware.l3_channel_sync_p1),
            ("channel-sync P1 算法级", hardware.l3_channel_sync_p1_algo),
            ("audio retention/mp4 v0.8 (Phase 4A)",
             audio_retention.l3_audio_retention),
            ("audio encode/compose v0.8 (Phase 4B)",
             audio_encode.l3_audio_encode),
            ("audio production output v0.8 (Phase 4C)",
             production_output.l3_production_output),
        ],
    }


def run_levels(
    levels: list[str],
    registry: dict[str, list[tuple[str, SuiteFn]]],
) -> None:
    """按给定顺序执行 suite; 单个 suite 抛异常不中断其余 suite。"""
    for lvl in levels:
        print(f"\n########## 测试深度 {lvl.upper()} ##########")
        for name, fn in registry[lvl]:
            paths.CURRENT_LEVEL = lvl
            t0 = time.monotonic()
            try:
                fn()
            except Exception as exc:      # noqa: BLE001 - 报告而不是中断
                paths.record(
                    f"{name} (异常)", False,
                    f"{type(exc).__name__}: {exc}", level=lvl,
                )
            print(f"  [{lvl}] {name} — {time.monotonic() - t0:.1f}s")


__all__ = ["LEVELS", "LEVEL_ALIASES", "build_registry", "run_levels"]
