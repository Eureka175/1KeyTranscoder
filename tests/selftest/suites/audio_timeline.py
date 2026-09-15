#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7.1 Phase 3A: 时间轴 / RenderPolicy / EOF + sync offset 方向。"""

from __future__ import annotations

from ..paths import WORK
from ..fixtures.audio import _p3a_fixture
from ..assertions.audio import _p3a_impulse_map
from ..fixtures.plans import _p3a_plan
from ..fixtures.plans import _p3a_read
from ..fixtures.plans import _p3a_ready
from ..fixtures.plans import _p3a_render
from ..paths import record
from ..paths import section
from ..assertions.audio import set_p3a_offset

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def l1_audio_timeline() -> None:
    """Phase 3A: AudioTimeline / RenderPolicy / duration / EOF 策略 (纯逻辑)。

    覆盖 §测试矩阵 T1–T8 与 §offset 语义 (正/负/零), 全部 deterministic。
    """
    if not _p3a_ready("timeline"):
        return

    section("L1 音频时间轴 / RenderPolicy / EOF (v0.7.1 Phase 3A)")
    from core.audio_timeline import (
        REASON_AUDIO_DURATION_UNKNOWN, REASON_AUDIO_SAMPLE_RATE_MISMATCH,
        AudioRenderError, RenderPolicy, resolve_timeline, samples_from_seconds,
        source_to_timeline, timeline_to_source,
    )

    d = WORK / "p3a_tl"
    d.mkdir(parents=True, exist_ok=True)

    # --- umeric: 换算 ----  ---------------------------------------------
    record("l1.p3a.样本/秒换算 (6.006s@48k = 288288)",
           samples_from_seconds(6.006, 48000) == 288288
           and samples_from_seconds(0.0, 48000) == 0)
    record("l1.p3a.offset 统一换算 timeline = source - offset",
           source_to_timeline(1960, 960) == 1000
           and timeline_to_source(1000, 960) == 1960
           and source_to_timeline(1000, -960) == 1960
           and source_to_timeline(1000, 0) == 1000)
    record("l1.p3a.RenderPolicy 只实现 UNION/EXPLICIT",
           RenderPolicy.coerce("union") is RenderPolicy.UNION
           and RenderPolicy.coerce("bogus", RenderPolicy.UNION)
           is RenderPolicy.UNION
           and RenderPolicy.INTERSECTION.value == "intersection")

    # --- T1: 单来源 -> 输出长度 = 来源长度 -------------------------------
    a = _p3a_fixture(d / "t1.wav", 1, 1000, {0: 100})
    p1 = _p3a_plan([{"source_id": "a", "path": a, "channels": 1,
                     "samples": 1000}])
    tl1 = resolve_timeline(p1)
    record("l1.p3a.T1 单来源 1000 -> 1000 samples",
           tl1.frame_count == 1000 and tl1.start_sample == 0
           and tl1.end_sample == 1000
           and tl1.render_policy is RenderPolicy.UNION,
           f"{tl1.summary()}")

    # --- T2: A=1000 B=1000 -> 1000 --------------------------------------
    b = _p3a_fixture(d / "t2.wav", 1, 1000, {0: 200})
    p2 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b, "channels": 1, "samples": 1000},
    ])
    tl2 = resolve_timeline(p2)
    record("l1.p3a.T2 双来源等长 -> 1000 samples",
           tl2.frame_count == 1000 and tl2.output_channels == 2)

    # --- T3/T4: UNION 取并集, 短 source 补静音 ---------------------------
    short = _p3a_fixture(d / "short.wav", 1, 800, {0: 100})
    p3 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": short, "channels": 1, "samples": 800},
    ])
    tl3 = resolve_timeline(p3)
    ct_b = tl3.channel("b:s0:c0")
    record("l1.p3a.T3 A=1000 B=800 -> UNION 1000; B 尾部 200 补静音",
           tl3.frame_count == 1000
           and ct_b.timeline_bounds == (0, 800)
           and ct_b.actual_samples is None            # 尚未解码, 不臆造
           and tl3.pads_after("b:s0:c0") is True
           and tl3.pads_after("a:s0:c0") is False,
           f"bounds={ct_b.timeline_bounds}")
    p4 = _p3a_plan([
        {"source_id": "a", "path": short, "channels": 1, "samples": 800},
        {"source_id": "b", "path": a, "channels": 1, "samples": 1000},
    ])
    tl4 = resolve_timeline(p4)
    record("l1.p3a.T4 A=800 B=1000 -> UNION 1000; A 尾部补静音",
           tl4.frame_count == 1000
           and tl4.pads_after("a:s0:c0") is True
           and tl4.pads_after("b:s0:c0") is False)

    # --- T5/T6: 带 offset 的 timeline 归正 ------------------------------
    #
    # ⚠️ 方向由实测钉死 (不是从字段名猜): 4×mono 素材 CH2 晚到 960 样本时,
    # channel_sync 报 `delay_samples=+960 / shift_samples=960`, 修正后音频与
    # 修正前的互相关 lag = -960 —— 即 **`out[n] = in[n + shift]`**, 晚到轨被
    # **前移**。统一 timeline 因此定义 `timeline = source - offset`:
    #   +100 (晚到) -> timeline [-100, 700): 内容前移, 尾部补静音
    #   -100 (早到) -> timeline [100, 900): 内容后移, 头部补静音
    rec = _p3a_fixture(d / "rec.wav", 1, 800, {0: 200})
    p5 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rec, "channels": 1, "samples": 800},
    ])
    set_p3a_offset(p5, "rec:s0:c0", 100)
    tl5 = resolve_timeline(p5)
    r5 = tl5.channel("rec:s0:c0")
    record("l1.p3a.T5 B offset=+100 (晚到) -> timeline [-100,700): 前移修正",
           r5.timeline_bounds == (-100, 700)
           and tl5.start_sample == -100
           and tl5.frame_count == 1100
           and tl5.clips_before("rec:s0:c0") is False
           and r5.planned_window(tl5.start_sample, tl5.end_sample)[0] == 0,
           f"bounds={r5.timeline_bounds} start={tl5.start_sample}")
    p6 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rec, "channels": 1, "samples": 800},
    ])
    set_p3a_offset(p6, "rec:s0:c0", -100)
    tl6 = resolve_timeline(p6)
    r6 = tl6.channel("rec:s0:c0")
    record("l1.p3a.T6 B offset=-100 (早到) -> timeline [100,900): 头部补静音",
           r6.timeline_bounds == (100, 900)
           and tl6.start_sample == 0
           and tl6.frame_count == 1000
           and tl6.clips_before("rec:s0:c0") is False
           and r6.planned_window(tl6.start_sample, tl6.end_sample)[0] == -100,
           f"bounds={r6.timeline_bounds} start={tl6.start_sample}")
    # --- T7/T8: overlap 与完全不 overlap 都允许 (不自动 mixing) ----------
    a2 = _p3a_fixture(d / "ov_a.wav", 1, 1000, {0: 100})
    b2 = _p3a_fixture(d / "ov_b.wav", 1, 1000, {0: 300})
    p7 = _p3a_plan([
        {"source_id": "a", "path": a2, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b2, "channels": 1, "samples": 1000},
    ])
    set_p3a_offset(p7, "b:s0:c0", -500)
    tl7 = resolve_timeline(p7)
    record("l1.p3a.T7 两源 timeline overlap 允许 (输出声道仍独立)",
           tl7.frame_count == 1500 and tl7.channel("b:s0:c0").timeline_bounds
           == (500, 1500))
    p8 = _p3a_plan([
        {"source_id": "a", "path": a2, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b2, "channels": 1, "samples": 1000},
    ])
    set_p3a_offset(p8, "b:s0:c0", -2000)
    tl8 = resolve_timeline(p8)
    record("l1.p3a.T8 完全不 overlap -> 并集仍完整 (不需要共同区间)",
           tl8.frame_count == 3000 and tl8.start_sample == 0
           and tl8.end_sample == 3000)

    # --- 采样率不一致拒绝; duration 未知拒绝 ----------------------------
    other = _p3a_fixture(d / "44k.wav", 1, 1000, {0: 100}, sample_rate=44100)
    p9 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "x", "path": other, "channels": 1, "samples": 1000,
         "sample_rate": 44100},
    ])
    p9.source("x").streams[0].sample_rate = 44100
    try:
        resolve_timeline(p9)
        mismatch = None
    except AudioRenderError as exc:
        mismatch = exc.reason
    record("l1.p3a.采样率不一致 -> audio_sample_rate_mismatch (不 resample)",
           mismatch == REASON_AUDIO_SAMPLE_RATE_MISMATCH, f"{mismatch}")

    p10 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
    ])
    p10.source("a").streams[0].duration_sec = None
    try:
        resolve_timeline(p10)
        unknown = None
    except AudioRenderError as exc:
        unknown = exc.reason
    record("l1.p3a.duration 无法确定 -> audio_duration_unknown (不猜)",
           unknown == REASON_AUDIO_DURATION_UNKNOWN, f"{unknown}")

    # --- 显式 duration 优先 (口音一致) ---------------------------------
    p11 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
    ])
    tl11 = resolve_timeline(p11, explicit_duration_seconds=0.01)
    record("l1.p3a.显式 duration=480 samples 覆盖并集推导",
           tl11.frame_count == 480 and tl11.explicit is True
           and tl11.duration_mode.value == "explicit")
    try:
        resolve_timeline(p11, render_policy=RenderPolicy.INTERSECTION)
        reserved = None
    except AudioRenderError as exc:
        reserved = exc.reason
    record("l1.p3a.INTERSECTION 为预留未实现 (显式拒绝, 不假装支持)",
           reserved is not None, f"{reserved}")


def l1_audio_sync_offset() -> None:
    """Phase 3A: offset 方向由**现有实现**钉死 (§不得按字段名猜)。

    1) `core/sync_fix.shift_stream` = `out[n] = in[n + rint(delay)]`;
    2) 本阶段 timeline 换算 `timeline = source - offset` 必须与之同向;
    3) impulse 素材端到端验证正/负/零 offset 的 trim / pad 行为。
    """
    if not _p3a_ready("sync"):
        return

    section("L1 音频 sync offset 方向 (v0.7.1 Phase 3A)")
    from core.audio_models import AudioSyncResult, SyncStatus
    from core.audio_timeline import source_to_timeline, timeline_to_source
    from core.sync_fix import shift_stream

    sync_dir = WORK / "p3a_sync"
    sync_dir.mkdir(parents=True, exist_ok=True)

    # --- 1) 钉住既有 shift_stream 的方向 ---------------------------------
    n = 64
    src_arr = np.zeros(n, dtype="<f4")
    src_arr[40] = 1.0
    dst = sync_dir / "shift.raw"
    shift_stream(src_arr, dst, delay_samples=10.0, sample_rate=48000,
                 storage_dtype="f32", chunk_seconds=1.0)
    shifted = np.fromfile(dst, dtype="<f4")
    record("l1.p3a.shift_stream(+10) 前移: in[40] -> out[30]",
           shifted.shape[0] == n and int(np.argmax(shifted)) == 30
           and shifted[30] == 1.0,
           f"argmax={int(np.argmax(shifted))}")
    shift_stream(src_arr, dst, delay_samples=-10.0, sample_rate=48000,
                 storage_dtype="f32", chunk_seconds=1.0)
    back = np.fromfile(dst, dtype="<f4")
    record("l1.p3a.shift_stream(-10) 后移: in[40] -> out[50]",
           int(np.argmax(back)) == 50 and back[50] == 1.0,
           f"argmax={int(np.argmax(back))}")

    # --- 2) 换算同向 -----------------------------------------------------
    record("l1.p3a.offset 换算与 channel_sync 实测同向 (timeline = source - offset)",
           source_to_timeline(1960, 960) == 1000
           and timeline_to_source(1000, 960) == 1960)

    # --- 3) 端到端 impulse: 晚到轨被对齐到同一 timeline 位置 --------------
    d = WORK / "p3a_sync"
    anchor = _p3a_fixture(d / "anch.wav", 1, 4000, {0: 1000})
    late = _p3a_fixture(d / "late.wav", 1, 4000, {0: 1960})
    plan = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    set_p3a_offset(plan, "late:s0:c0", 960)
    out = d / "aligned.wav"
    res = _p3a_render(plan, out, chunk_frames=97)
    arr, _info = _p3a_read(out)
    peaks = _p3a_impulse_map(arr)
    record("l1.p3a.impulse +960: 两轨 impulse 对齐到同一 timeline 位置",
           res.ok and peaks == [[1960], [1960]]
           and res.timeline.start_sample == -960
           and res.frames == 4960,
           f"ok={res.ok} peaks={peaks} start={res.timeline.start_sample if res.timeline else None} "
           f"errors={res.errors}")

    # 负 offset: 内容**后移** (起点仍为 0, 头部补静音, 尾部延长)
    plan2 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "early", "path": late, "channels": 1, "samples": 4000},
    ])
    set_p3a_offset(plan2, "early:s0:c0", -960)
    out2 = d / "delayed.wav"
    res2 = _p3a_render(plan2, out2, chunk_frames=97)
    arr2, _i2 = _p3a_read(out2)
    peaks2 = _p3a_impulse_map(arr2)
    record("l1.p3a.impulse -960: 内容后移 960 (窗口 0..4960)",
           res2.ok and peaks2[0] == [1000] and peaks2[1] == [2920]
           and res2.frames == 4960
           and res2.timeline.start_sample == 0,
           f"peaks={peaks2} frames={res2.frames} "
           f"start={res2.timeline.start_sample if res2.timeline else None} "
           f"errors={res2.errors}")

    # 零 offset / 未测量: 原样
    plan3 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    out3 = d / "plain.wav"
    res3 = _p3a_render(plan3, out3, chunk_frames=101)
    arr3, _i3 = _p3a_read(out3)
    peaks3 = _p3a_impulse_map(arr3)
    record("l1.p3a.未测量 (not_processed) 不施加任何移位",
           res3.ok and peaks3 == [[1000], [1960]],
           f"peaks={peaks3}")

    # 状态非 success 时即便 offset 字段有值也不应用 (§只应用已确认结果)
    plan4 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    for track in plan4.input_tracks:
        for ch in track.channels:
            if ch.id == "late:s0:c0":
                track.set_sync(AudioSyncResult(
                    status=SyncStatus.LOW_CONFIDENCE, offset_samples=960.0,
                    reason="low_confidence",
                ))
    out4 = d / "unconfirmed.wav"
    res4 = _p3a_render(plan4, out4, chunk_frames=64)
    arr4, _i4 = _p3a_read(out4)
    peaks4 = _p3a_impulse_map(arr4)
    record("l1.p3a.low_confidence 的 offset 不被应用 (只用成功结论)",
           res4.ok and peaks4[1] == [1960], f"peaks={peaks4}")
