#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7.1 Phase 3B: PCM 混音 / chunk invariance / 图等价。"""

from __future__ import annotations

import json
from ..paths import WORK
from ..fixtures.audio import _p3a_fixture
from ..assertions.audio import _p3a_hash
from ..assertions.audio import _p3a_impulse_map
from ..assertions.audio import _p3a_impulse_timeline
from ..fixtures.plans import _p3a_plan
from ..fixtures.plans import _p3a_read
from ..fixtures.plans import _p3a_ready
from ..fixtures.plans import _p3a_render
from ..fixtures.plans import _p3b_bus
from ..fixtures.audio import _p3b_fixture
from ..paths import record
from ..paths import section
from ..assertions.audio import set_p3a_offset

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def l1_audio_mix() -> None:
    """Phase 3B: N->1 混音 / gain / float32 累加 / peak / clipping。"""
    section("L1 PCM 混音 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix"):
        return
    from core.audio_mix import (
        ClipPolicy, MixBus, MixBusBuilder, MixGain, MixSink, db_to_linear,
        linear_to_db,
    )
    from core.audio_wav import WavFormat, read_wav

    d = WORK / "p3b"
    d.mkdir(parents=True, exist_ok=True)

    # --- gain 标度 (线性, 不做响度标准化) -------------------------------
    record("l1.p3b.gain 线性标度 (0dB=1.0, -6dB≈0.5012, +6dB≈1.9953)",
           db_to_linear(0.0) == 1.0
           and abs(db_to_linear(-6.0) - 0.501187) < 1e-6
           and abs(db_to_linear(6.0) - 1.995262) < 1e-6
           and abs(linear_to_db(db_to_linear(-6.0)) + 6.0) < 1e-9)

    # --- A=1, B=1, gain 0.5/0.5 -> 1.0 ---------------------------------
    a = _p3b_fixture(d / "one_a.wav", 1, 400, {0: 1.0}, at=100)
    b = _p3b_fixture(d / "one_b.wav", 1, 400, {0: 1.0}, at=100)
    plan = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": b, "channels": 1, "samples": 400},
    ])
    plan.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus = _p3b_bus(plan, ["a:s0:c0", "b:s0:c0"],
                   gains={"a:s0:c0": 0.5, "b:s0:c0": 0.5})
    out = d / "half_half.wav"
    res = _p3a_render(plan, out, chunk_frames=97, mix_bus=bus)
    arr, info = _p3a_read(out) if out.exists() else (None, None)
    record("l1.p3b.N->1: 1.0×0.5 + 1.0×0.5 = 1.0 (sample-exact)",
           res.ok and info is not None and info.channel_count == 1
           and float(arr[100, 0]) == 1.0
           and float(np.abs(arr[:, 0]).max()) == 1.0
           and res.mix_stats is not None
           and res.mix_stats.clip_count == 0
           and abs(res.mix_stats.peak - 1.0) < 1e-9,
           f"value={float(arr[100, 0]) if arr is not None else None} "
           f"errors={res.errors}")
    record("l1.p3b.峰值可分解到逐输入贡献 (Σ contributions = peak)",
           res.mix_stats is not None
           and abs(sum(x["contribution"] for x in res.mix_stats.peak_inputs)
                   - res.mix_stats.peak) < 1e-6
           and [x["channel_id"] for x in res.mix_stats.peak_inputs]
           == ["a:s0:c0", "b:s0:c0"]
           and res.mix_stats.peak_frame == 100,
           f"{res.mix_stats.peak_inputs if res.mix_stats else None}")

    # --- A=1, B=-1, gain 0.5/0.5 -> 0.0 --------------------------------
    neg = _p3b_fixture(d / "one_neg.wav", 1, 400, {0: -1.0}, at=100)
    plan2 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": neg, "channels": 1, "samples": 400},
    ])
    plan2.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus2 = _p3b_bus(plan2, ["a:s0:c0", "b:s0:c0"],
                    gains={"a:s0:c0": 0.5, "b:s0:c0": 0.5})
    out2 = d / "cancel.wav"
    res2 = _p3a_render(plan2, out2, chunk_frames=41, mix_bus=bus2)
    arr2, _i2 = _p3a_read(out2) if out2.exists() else (None, None)
    record("l1.p3b.相位相消: 1.0×0.5 + (-1.0)×0.5 = 0.0",
           res2.ok and arr2 is not None
           and float(arr2[100, 0]) == 0.0
           and float(np.abs(arr2).max()) == 0.0,
           f"value={float(arr2[100, 0]) if arr2 is not None else None}")

    # --- 溢出 / clipping: 1+1 (单位增益) --------------------------------
    plan3 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": b, "channels": 1, "samples": 400},
    ])
    plan3.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus3 = _p3b_bus(plan3, ["a:s0:c0", "b:s0:c0"])
    # DETECT (默认): 数据保留, 只统计
    out3 = d / "over_detect.wav"
    res3 = _p3a_render(plan3, out3, chunk_frames=64, mix_bus=bus3)
    arr3, _i3 = _p3a_read(out3) if out3.exists() else (None, None)
    record("l1.p3b.overflow + detect: 2.0 原样保留, |sum|>1 被计数",
           res3.ok and arr3 is not None and float(arr3[100, 0]) == 2.0
           and res3.mix_stats is not None
           and res3.mix_stats.clip_count == 1
           and abs(res3.mix_stats.peak - 2.0) < 1e-9,
           f"value={float(arr3[100, 0]) if arr3 is not None else None} "
           f"stats={res3.mix_stats.summary() if res3.mix_stats else None}")
    # 不自动 normalize: 除峰值外其余样本必须仍是 0 (没有被整体缩放)
    record("l1.p3b.不自动 normalize (非峰值样本保持 0, 未被整体缩放)",
           res3.ok and arr3 is not None
           and int(np.count_nonzero(arr3)) == 1)
    # HARD_CLIP: 显式裁剪
    out3h = d / "over_clip.wav"
    res3h = _p3a_render(plan3, out3h, chunk_frames=64, mix_bus=bus3,
                        clip_policy=ClipPolicy.HARD_CLIP)
    arr3h, _i3h = _p3a_read(out3h) if out3h.exists() else (None, None)
    record("l1.p3b.overflow + hard_clip: 显式裁到 1.0 且记录 hard_clipped",
           res3h.ok and arr3h is not None and float(arr3h[100, 0]) == 1.0
           and res3h.mix_stats is not None
           and res3h.mix_stats.hard_clipped == 1
           and res3h.mix_stats.clip_count == 1,
           f"value={float(arr3h[100, 0]) if arr3h is not None else None} "
           f"hard_clipped={res3h.mix_stats.hard_clipped if res3h.mix_stats else None}")
    # ERROR: 直接拒绝
    out3e = d / "over_error.wav"
    try:
        out3e.unlink()
    except OSError:
        pass
    res3e = _p3a_render(plan3, out3e, chunk_frames=64, mix_bus=bus3,
                        clip_policy=ClipPolicy.ERROR)
    record("l1.p3b.overflow + error: audio_mix_clipping 且不产出文件",
           (not res3e.ok)
           and "audio_mix_clipping" in [e.get("reason") for e in res3e.errors]
           and not out3e.exists(),
           f"{[e.get('reason') for e in res3e.errors]}")

    # --- 整数格式: 格式级裁剪与 float 输出可 over-range 对照 ------------
    out3f = d / "over_float32.wav"
    res3f = _p3a_render(plan3, out3f, chunk_frames=64, mix_bus=bus3,
                        sample_format=WavFormat.FLOAT32)
    arr3f, info3f = _p3a_read(out3f) if out3f.exists() else (None, None)
    record("l1.p3b.float32 输出允许 over-range (2.0 原样写出)",
           res3f.ok and arr3f is not None and float(arr3f[100, 0]) == 2.0
           and info3f is not None and info3f.is_float,
           f"value={float(arr3f[100, 0]) if arr3f is not None else None}")
    out3p = d / "over_pcm16.wav"
    res3p = _p3a_render(plan3, out3p, chunk_frames=64, mix_bus=bus3,
                        sample_format=WavFormat.PCM16)
    arr3p, _i3p = _p3a_read(out3p) if out3p.exists() else (None, None)
    record("l1.p3b.PCM16 输出: 2.0 被格式裁剪到 32767 (≈0.99997)",
           res3p.ok and arr3p is not None
           and abs(float(arr3p[100, 0]) - 32767.0 / 32768.0) < 1e-6
           and res3p.mix_stats is not None
           and res3p.mix_stats.format_clip_count >= 1,
           f"value={float(arr3p[100, 0]) if arr3p is not None else None} "
           f"format_clip={res3p.mix_stats.format_clip_count if res3p.mix_stats else None}")

    # --- 多来源 + 多声道: 2×stereo -> 2 个 mono 输出 --------------------
    cam = _p3b_fixture(d / "st_cam.wav", 2, 300,
                       {0: 0.5, 1: 0.25}, at=50)
    rec = _p3b_fixture(d / "st_rec.wav", 2, 300,
                       {0: 0.25, 1: 0.5}, at=50)
    plan4 = _p3a_plan([
        {"source_id": "cam", "path": cam, "channels": 2, "samples": 300},
        {"source_id": "rec", "path": rec, "channels": 2, "samples": 300},
    ])
    plan4.selected_channels = ["cam:s0:c0", "rec:s0:c0", "cam:s0:c1",
                               "rec:s0:c1"]
    bus4 = MixBusBuilder(plan4)
    multi = bus4.bus()
    multi.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("cam:s0:c0", 0.5),
                        MixGain("rec:s0:c0", 0.5)]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("cam:s0:c1", 0.5),
                        MixGain("rec:s0:c1", 0.5)]),
    ]
    out4 = d / "two_bus.wav"
    res4 = _p3a_render(plan4, out4, chunk_frames=37, mix_bus=multi)
    arr4, info4 = _p3a_read(out4) if out4.exists() else (None, None)
    record("l1.p3b.两路独立混音bus (L=L_cam+L_rec, R=R_cam+R_rec)",
           res4.ok and arr4 is not None and arr4.shape == (300, 2)
           and abs(float(arr4[50, 0]) - 0.375) < 1e-6
           and abs(float(arr4[50, 1]) - 0.375) < 1e-6
           and info4 is not None and info4.channel_count == 2
           and multi.trace(0)["is_mixing"] is True
           and [x["channel_id"] for x in multi.trace(1)["inputs"]]
           == ["cam:s0:c1", "rec:s0:c1"],
           f"L={float(arr4[50, 0]) if arr4 is not None else None} "
           f"R={float(arr4[50, 1]) if arr4 is not None else None}")

    # --- 短 source 在混音里也是静音 (EOF 策略统一) -----------------------
    long_src = _p3b_fixture(d / "long_m.wav", 1, 1000, {0: 0.5}, at=200)
    short_src = _p3b_fixture(d / "short_m.wav", 1, 400, {0: 0.5}, at=200)
    plan5 = _p3a_plan([
        {"source_id": "long", "path": long_src, "channels": 1, "samples": 1000},
        {"source_id": "short", "path": short_src, "channels": 1,
         "samples": 400},
    ])
    plan5.selected_channels = ["long:s0:c0", "short:s0:c0"]
    bus5 = _p3b_bus(plan5, ["long:s0:c0", "short:s0:c0"],
                    gains={"long:s0:c0": 0.5, "short:s0:c0": 0.5})
    out5 = d / "short_mix.wav"
    res5 = _p3a_render(plan5, out5, chunk_frames=64, mix_bus=bus5)
    arr5, info5 = _p3a_read(out5) if out5.exists() else (None, None)
    record("l1.p3b.混音不重新定义 EOF: 短 source EOF 后等同静音, 长 source 继续",
           res5.ok and arr5 is not None and info5 is not None
           and info5.frame_count == 1000
           and abs(float(arr5[200, 0]) - 0.5) < 1e-6
           and not np.any(arr5[500:]),
           f"frames={info5.frame_count if info5 else None} "
           f"v200={float(arr5[200, 0]) if arr5 is not None else None} "
           f"tail={float(np.abs(arr5[500:]).max()) if arr5 is not None else None}")
    # 回归 (F1): 静音统计必须走 **base timeline**。混音 timeline 的声道身份
    # 是 `mix0`, 拿它去查源声道 (`long:s0:c0`) 必然 miss -> 恒定 total=0 ->
    # silence_samples 恒报满 (1000/1000)。此处钉死正确语义:
    # 唯一输出声道的输入区间并集 = [0,1000) 覆盖整个 window -> 静音 0。
    record("l1.p3b.silence_samples 走 base timeline (混音图不恒报满)",
           res5.ok
           and res5.silence_samples == 0
           and res5.frames == 1000 and res5.output_channels == 1
           and res5.silence_samples != res5.frames * res5.output_channels,
           f"silence={res5.silence_samples} "
           f"total={res5.frames * res5.output_channels}")

    # --- 带 offset 的混音: 两轨对齐后相加 -------------------------------
    early = _p3b_fixture(d / "off_a.wav", 1, 1200, {0: 0.5}, at=1000)
    late = _p3b_fixture(d / "off_b.wav", 1, 1200, {0: 0.5}, at=1060)
    plan6 = _p3a_plan([
        {"source_id": "e", "path": early, "channels": 1, "samples": 1200},
        {"source_id": "l", "path": late, "channels": 1, "samples": 1200},
    ])
    set_p3a_offset(plan6, "l:s0:c0", 60)      # 晚到 60 -> 前移到 1000
    plan6.selected_channels = ["e:s0:c0", "l:s0:c0"]
    bus6 = _p3b_bus(plan6, ["e:s0:c0", "l:s0:c0"])
    out6 = d / "offset_mix.wav"
    res6 = _p3a_render(plan6, out6, chunk_frames=64, mix_bus=bus6)
    arr6, _i6 = _p3a_read(out6) if out6.exists() else (None, None)
    peaks6 = (_p3a_impulse_timeline(arr6, res6.timeline, threshold=1e-6)
              if arr6 is not None else None)
    record("l1.p3b.offset 在混音中同样生效: 晚到 60 的轨被前移后与另一轨求和",
           res6.ok and arr6 is not None and peaks6 == [[1000]]
           and abs(float(arr6[1060, 0]) - 1.0) < 1e-6
           and int(np.count_nonzero(arr6)) == 1,
           f"timeline_peak={peaks6} "
           f"window=[{res6.timeline.start_sample},{res6.timeline.end_sample}) "
           f"v={float(arr6[1060, 0]) if arr6 is not None else None}")

    # --- 混音规格校验 ---------------------------------------------------
    from core.audio_mix import validate_mix_bus

    bad = MixBusBuilder(plan).bus()
    bad.sinks = [MixSink(output_index=0,
                         inputs=[MixGain("nope:s0:c0", 1.0)])]
    issues = validate_mix_bus(plan, bad)
    record("l1.p3b.混音引用了不存在的声道 -> audio_channel_not_found",
           any(i["reason"] == "audio_channel_not_found" for i in issues),
           f"{[i['reason'] for i in issues]}")
    bad2 = MixBusBuilder(plan).bus()
    bad2.sinks = [MixSink(output_index=3, inputs=[MixGain("a:s0:c0", 1.0)])]
    issues2 = validate_mix_bus(plan, bad2)
    record("l1.p3b.混音输出 index 不连续 -> audio_mix_invalid",
           any(i["reason"] == "audio_mix_invalid" for i in issues2),
           f"{[i['reason'] for i in issues2]}")
    bad3 = MixBusBuilder(plan).bus()
    bad3.sinks = [MixSink(output_index=0,
                          inputs=[MixGain("a:s0:c0", -1.0)])]
    issues3 = validate_mix_bus(plan, bad3)
    record("l1.p3b.负增益 -> audio_mix_invalid (不做相位反相作为常规增益)",
           any(i["reason"] == "audio_mix_invalid" for i in issues3),
           f"{[i['reason'] for i in issues3]}")
    record("l1.p3b.MixBus JSON 往返 (gain / gain_db / policy)",
           MixBus.from_dict(bus.to_dict()).to_dict() == bus.to_dict()
           and MixBus.from_dict(
               {"mix_mode": "sum",
                "sinks": [{"output_index": 0,
                           "inputs": [{"channel_id": "a:s0:c0",
                                       "gain_db": -6.0}]}]}
           ).sinks[0].inputs[0].gain == db_to_linear(-6.0))

    # --- plan -> 模型字段 / 序列化 --------------------------------------
    plan.mix_buses = [bus]
    model_round = type(plan).from_dict(plan.to_dict())
    record("l1.p3b.AudioPlan.mix_buses JSON 往返 + is_default 因混音为假",
           len(model_round.mix_buses) == 1
           and model_round.mix_buses[0]["sinks"][0]["inputs"][0]["channel_id"]
           == "a:s0:c0"
           and plan.is_default is False
           and _p3a_plan([
               {"source_id": "a", "path": a, "channels": 1, "samples": 400},
           ]).is_default is True,
           f"mix_buses={json.dumps(model_round.mix_buses, ensure_ascii=False)[:120]}")
    from core.audio_plan import build_map_spec

    plan.mix_mode = "sum"
    spec_after = build_map_spec(plan)
    record("l1.p3b.-map 规格仍拒绝混音 (audio_mix_not_supported, 两阶段契约并存)",
           spec_after.executable is False
           and "audio_mix_not_supported"
           in [e["reason"] for e in spec_after.errors],
           f"{[e['reason'] for e in spec_after.errors]}")


def l1_audio_mix_invariance() -> None:
    """Phase 3B: 混音结果与 chunk 划分无关 + 与路由图在 1:1 时逐样本一致。"""
    section("L1 PCM 混音 chunk invariance / 图等价 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix_inv"):
        return
    from core.audio_mix import MixBusBuilder
    from core.audio_wav import read_wav

    d = WORK / "p3b_inv"
    d.mkdir(parents=True, exist_ok=True)

    # 多来源 + 多声道 + offset 下的混音, 跨 chunk 划分 byte-identical
    a = _p3a_fixture(d / "inv_a.wav", 2, 1500, {0: 111, 1: 700})
    b = _p3a_fixture(d / "inv_b.wav", 2, 1200, {0: 900, 1: 42})
    plan = _p3a_plan([
        {"source_id": "cam", "path": a, "channels": 2, "samples": 1500},
        {"source_id": "rec", "path": b, "channels": 2, "samples": 1200},
    ])
    set_p3a_offset(plan, "rec:s0:c0", 480)
    set_p3a_offset(plan, "rec:s0:c1", -240)
    plan.selected_channels = ["cam:s0:c0", "rec:s0:c0", "cam:s0:c1",
                              "rec:s0:c1"]
    plan.mix_mode = "sum"
    bus = MixBusBuilder(plan).bus()
    from core.audio_mix import MixGain, MixSink

    bus.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("cam:s0:c0", 0.5),
                        MixGain("rec:s0:c0", 0.5)]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("cam:s0:c1", 0.5),
                        MixGain("rec:s0:c1", 0.5)]),
    ]
    hashes, peaks, stats = {}, {}, {}
    for chunk in (7, 256, 1024, 4096):
        out = d / f"mix_inv_{chunk}.wav"
        res = _p3a_render(plan, out, chunk_frames=chunk, mix_bus=bus,
                          backend="reader")
        if not res.ok:
            hashes[chunk] = f"ERR {[e.get('reason') for e in res.errors]}"
            continue
        hashes[chunk] = _p3a_hash(out)
        arr, _info = read_wav(out)
        peaks[chunk] = _p3a_impulse_map(arr, threshold=0.01)
        stats[chunk] = res.mix_stats.to_dict() if res.mix_stats else None
    record("l1.p3b.混音 chunk 7/256/1024/4096 -> byte-identical 输出",
           len(set(hashes.values())) == 1,
           f"{json.dumps(hashes, ensure_ascii=False)}")
    record("l1.p3b.混音 impulse 位置与 peak/clip 统计均与 chunk 无关",
           len({json.dumps(v) for v in peaks.values()}) == 1
           and len({json.dumps({k: v for k, v in (s or {}).items()
                                if k != "frames"}, sort_keys=True)
                    for s in stats.values()}) == 1,
           f"peaks={json.dumps(peaks.get(256), ensure_ascii=False)}")

    # 图等价: 1:1 单输入 sink 的"混音图"必须与 Phase 3A 路由图逐样本一致
    quad = _p3a_fixture(d / "eq.wav", 4, 800,
                        {0: 100, 1: 200, 2: 300, 3: 400})
    plan_route = _p3a_plan([
        {"source_id": "cam", "path": quad, "channels": 4, "samples": 800},
    ])
    order = ["cam:s0:c2", "cam:s0:c0", "cam:s0:c3", "cam:s0:c1"]
    from core.audio_plan import AudioPlanner

    planner = AudioPlanner(plan_route)
    planner.select_channels(*order)
    planner.map_channels(*order)
    route_out = d / "eq_route.wav"
    res_route = _p3a_render(plan_route, route_out, chunk_frames=64)
    plan_mix = _p3a_plan([
        {"source_id": "cam", "path": quad, "channels": 4, "samples": 800},
    ])
    plan_mix.selected_channels = list(order)
    bus_eq = MixBusBuilder(plan_mix).one_to_one(order)
    mix_out = d / "eq_mix.wav"
    res_mix = _p3a_render(plan_mix, mix_out, chunk_frames=64, mix_bus=bus_eq)
    arr_r, _ir = read_wav(route_out) if route_out.exists() else (None, None)
    arr_m, _im = read_wav(mix_out) if mix_out.exists() else (None, None)
    record("l1.p3b.图等价: 1:1 混音图 == 路由图 (逐样本, 含重排)",
           res_route.ok and res_mix.ok and arr_r is not None
           and arr_m is not None and np.array_equal(arr_r, arr_m)
           and _p3a_impulse_map(arr_m) == [[300], [100], [400], [200]],
           f"route_ok={res_route.ok} mix_ok={res_mix.ok} "
           f"peaks={_p3a_impulse_map(arr_m) if arr_m is not None else None}")

    # 混音与导出解耦: 同一计划既可只路由也可混音
    record("l1.p3b.WAV exporter 不实现 mixing (混音图与路由图共用同一 exporter)",
           res_route.ok and res_mix.ok
           and res_route.mix_bus is None and res_mix.mix_bus is not None
           and res_route.summary()["graph"] == "routing"
           and res_mix.summary()["graph"] == "mixing")

    # --- 读取传输层无关: 管道读取器 == 生产文件读取器 (逐字节) -----------
    pipe_out = d / "backend_pipe.wav"
    reader_out = d / "backend_reader.wav"
    res_pipe = _p3a_render(plan, pipe_out, chunk_frames=1024, mix_bus=bus,
                           backend="pipe")
    res_reader = _p3a_render(plan, reader_out, chunk_frames=1024, mix_bus=bus,
                             backend="reader")
    record("l1.p3b.混音结果与读取传输层无关 (ffmpeg 管道 == span 文件读取)",
           res_pipe.ok and res_reader.ok and pipe_out.exists()
           and reader_out.exists()
           and _p3a_hash(pipe_out) == _p3a_hash(reader_out),
           f"pipe={res_pipe.ok} reader={res_reader.ok}")
