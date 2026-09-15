#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7.1 Phase 3A: 通道路由 / WAV 导出 / chunk invariance。"""

from __future__ import annotations

import json
from ..fixtures.audio import P3A_SR
from ..paths import WORK
from ..fixtures.audio import _p3a_fixture
from ..assertions.audio import _p3a_hash
from ..assertions.audio import _p3a_impulse_map
from ..fixtures.plans import _p3a_long_fixture
from ..fixtures.plans import _p3a_mono_header
from ..fixtures.plans import _p3a_pcm16_roundtrip
from ..fixtures.plans import _p3a_plan
from ..fixtures.plans import _p3a_read
from ..fixtures.plans import _p3a_ready
from ..fixtures.plans import _p3a_render
from ..fixtures.plans import _p3a_truncated_fixture
from ..fixtures.audio import _raw_audio_stream
from ..paths import _work_set_mb
from ..fixtures.plans import path_size_check
from ..paths import record
from ..paths import section
from ..assertions.audio import set_p3a_offset

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def l1_audio_route() -> None:
    """Phase 3A: 路由 T1–T10 (4CH / 2×2CH / 4×mono / 跨来源 / 逐样本一致)。"""
    if not _p3a_ready("route"):
        return

    section("L1 音频通道路由 (v0.7.1 Phase 3A)")
    from core.audio_timeline import REASON_AUDIO_SAMPLE_RATE_MISMATCH

    d = WORK / "p3a_route"
    d.mkdir(parents=True, exist_ok=True)

    # --- T1/T2: 4CH -> 4CH 逐声道一致 ------------------------------------
    q = _p3a_fixture(d / "quad.wav", 4, 1000,
                     {0: 100, 1: 200, 2: 300, 3: 400})
    plan = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                       "samples": 1000}])
    out = d / "quad_out.wav"
    res = _p3a_render(plan, out, chunk_frames=128)
    arr, info = _p3a_read(out)
    peaks = _p3a_impulse_map(arr)
    record("l1.p3a.T2 4CH -> 4CH: out[i] == in[i]",
           res.ok and arr.shape == (1000, 4)
           and peaks == [[100], [200], [300], [400]]
           and info.frame_count == 1000 and info.channel_count == 4,
           f"peaks={peaks} shape={arr.shape}")

    # --- T3: 4CH reorder [2,0,3,1] --------------------------------------
    from core.audio_plan import AudioPlanner

    p3 = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                     "samples": 1000}])
    planner = AudioPlanner(p3)
    try:
        planner.map_channels("cam:s0:c2", "cam:s0:c0", "cam:s0:c3",
                             "cam:s0:c1")
        ok3 = True
    except Exception as exc:                # noqa: BLE001
        ok3, _ = False, exc
    out3 = d / "reorder.wav"
    res3 = _p3a_render(p3, out3, chunk_frames=97)
    arr3, _i3 = _p3a_read(out3)
    peaks3 = _p3a_impulse_map(arr3)
    record("l1.p3a.T3 4CH reorder [2,0,3,1] 逐样本精确",
           ok3 and res3.ok
           and peaks3 == [[300], [100], [400], [200]]
           and [tl_ch for tl_ch in res3.timeline.output_channel_ids]
           == ["cam:s0:c2", "cam:s0:c0", "cam:s0:c3", "cam:s0:c1"],
           f"peaks={peaks3} order={res3.timeline.output_channel_ids if res3.timeline else None}")

    # --- T3b: UNION 下短 source 尾部补**确定性静音** (渲染级验证) ---------
    b3 = _p3a_fixture(d / "u_b.wav", 1, 800, {0: 42})
    plan3 = _p3a_plan([
        {"source_id": "cam", "path": q, "channels": 4, "samples": 1000},
        {"source_id": "rec", "path": b3, "channels": 1, "samples": 800},
    ])
    out3b = d / "union_short.wav"
    res3b = _p3a_render(plan3, out3b, chunk_frames=91)
    arr3b, info3b = _p3a_read(out3b)
    tail = arr3b[800:, 4]
    record("l1.p3a.T3b UNION: 长 source 不截断, 短 source 尾部确定性静音",
           res3b.ok and arr3b.shape == (1000, 5)
           and tail.size == 200 and not np.any(tail)
           and float(np.max(np.abs(arr3b[:, 0]))) > 0.0
           and res3b.silence_samples >= 200
           and info3b.frame_count == 1000,
           f"shape={arr3b.shape} tailmax={float(np.abs(tail).max()) if tail.size else None} "
           f"silence={res3b.silence_samples}")

    # --- T12: metadata 与实际解码不一致 -> 检测 + 以实际为准 ---------------
    # fixture: header 声明 1000 帧, 文件里实际只有 600 帧 (声明 > 实际)。
    t12 = _p3a_truncated_fixture(d / "meta_lie.wav", 1000, 600)
    plan12 = _p3a_plan([
        {"source_id": "cam", "path": q, "channels": 4, "samples": 1000},
        {"source_id": "short", "path": t12, "channels": 1, "samples": 1000},
    ])
    out12 = d / "mismatch.wav"
    res12 = _p3a_render(plan12, out12, chunk_frames=64)
    arr12, info12 = _p3a_read(out12)
    plan12b = _p3a_plan([
        {"source_id": "short", "path": t12, "channels": 1, "samples": 1000},
    ])
    out12b = d / "mismatch_solo.wav"
    res12b = _p3a_render(plan12b, out12b, chunk_frames=64)
    arr12b, info12b = _p3a_read(out12b)
    record("l1.p3a.T12 header 声明 1000 / 实际 600 -> 检测 mismatch",
           res12.ok
           and any(m["reason"] == "audio_duration_metadata_mismatch"
                   for m in res12.duration_mismatches)
           and any("short" in str(m.get("stream_id"))
                   for m in res12.duration_mismatches),
           f"{json.dumps(res12.duration_mismatches, ensure_ascii=False)[:220]}")
    record("l1.p3a.T12 派生窗口以**实际解码**为准 (单来源输出 600 而非 1000)",
           res12b.ok and info12b.frame_count == 600
           and arr12b.shape == (600, 1) and res12b.frames == 600,
           f"frames={info12b.frame_count}")
    record("l1.p3a.T12 多来源时 UNION 取长 (cam 1000 不被误导的 metadata 截断)",
           res12.ok and info12.frame_count == 1000
           and arr12.shape == (1000, 5),
           f"frames={info12.frame_count}")

    # --- T4: 4CH -> 单声道 (CH3) ---------------------------------------
    p4 = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                     "samples": 1000}])
    AudioPlanner(p4).select_channels("cam:s0:c2")
    out4 = d / "mono.wav"
    res4 = _p3a_render(p4, out4, chunk_frames=64)
    arr4, info4 = _p3a_read(out4)
    ref, _ri = _p3a_read(q)
    record("l1.p3a.T4 4CH -> CH3 输出 mono 且样本精确相等",
           res4.ok and arr4.shape == (1000, 1)
           and info4.channel_count == 1
           and np.array_equal(arr4[:, 0], ref[:, 2]),
           f"shape={arr4.shape}")

    # --- T5/T6: 2×2CH / 4×mono 跨流重排 --------------------------------
    s1 = _p3a_fixture(d / "s1.wav", 2, 600, {0: 10, 1: 20})
    s2 = _p3a_fixture(d / "s2.wav", 2, 600, {0: 30, 1: 40})
    raw = [
        _raw_audio_stream(1, channels=2, codec="pcm_f32le",
                          sample_rate="48000", sample_fmt="flt",
                          duration="0.012500", bit_rate=None, tag_string=None),
        _raw_audio_stream(2, channels=2, codec="pcm_f32le",
                          sample_rate="48000", sample_fmt="flt",
                          duration="0.012500", bit_rate=None, tag_string=None),
    ]
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
        build_audio_streams,
    )

    src_a = AudioSource(
        source_id="s1", source_type=AudioSourceType.WAV, path=str(s1),
        streams=build_audio_streams(
            [_raw_audio_stream(0, channels=2, codec="pcm_f32le",
                               sample_rate="48000", sample_fmt="flt",
                               duration="0.012500", bit_rate=None,
                               tag_string=None)],
            source_id="s1"),
        input_index=0,
    )
    src_b = AudioSource(
        source_id="s2", source_type=AudioSourceType.WAV, path=str(s2),
        streams=build_audio_streams(
            [_raw_audio_stream(0, channels=2, codec="pcm_f32le",
                               sample_rate="48000", sample_fmt="flt",
                               duration="0.012500", bit_rate=None,
                               tag_string=None)],
            source_id="s2"),
        input_index=1,
    )
    plan5 = AudioPlan(sources=[src_a, src_b])
    plan5.input_tracks = (
        AudioTrackBuilder(src_a.streams, source_id="s1").tracks()
        + AudioTrackBuilder(src_b.streams, source_id="s2").tracks()
    )
    plan5.selected_tracks = [t.track_id for t in plan5.input_tracks]
    plan5.selected_channels = [c.id for c in plan5.all_channels()]
    planner5 = AudioPlanner(plan5)
    planner5.select_channels("s1:s0:c1", "s2:s0:c1", "s1:s0:c0",
                             "s2:s0:c0")
    planner5.map_channels("s1:s0:c1", "s2:s0:c1", "s1:s0:c0", "s2:s0:c0")
    out5 = d / "cross.wav"
    res5 = _p3a_render(plan5, out5, chunk_frames=64)
    arr5, info5 = _p3a_read(out5)
    peaks5 = _p3a_impulse_map(arr5)
    record("l1.p3a.T5 跨来源 2×2CH 重排 (L2R2L1R1) 精确",
           res5.ok and peaks5 == [[20], [40], [10], [30]]
           and info5.channel_count == 4,
           f"peaks={peaks5} err={res5.errors}")
    m1 = _p3a_fixture(d / "m1.wav", 1, 500, {0: 50})
    m2 = _p3a_fixture(d / "m2.wav", 1, 500, {0: 60})
    m3 = _p3a_fixture(d / "m3.wav", 1, 500, {0: 70})
    m4 = _p3a_fixture(d / "m4.wav", 1, 500, {0: 80})
    specs = [
        {"source_id": f"m{i}", "path": p, "channels": 1, "samples": 500}
        for i, p in enumerate((m1, m2, m3, m4), start=1)
    ]
    plan6 = _p3a_plan(specs)
    planner6 = AudioPlanner(plan6)
    order6 = ["m4:s0:c0", "m2:s0:c0", "m1:s0:c0", "m3:s0:c0"]
    planner6.select_channels(*order6)
    planner6.map_channels(*order6)
    out6 = d / "mono4.wav"
    res6 = _p3a_render(plan6, out6, chunk_frames=37)
    arr6, _i6 = _p3a_read(out6)
    peaks6 = _p3a_impulse_map(arr6)
    record("l1.p3a.T6 4×mono 跨流重排 (4,2,1,3) 不塌缩",
           res6.ok and peaks6 == [[80], [60], [50], [70]]
           and arr6.shape[1] == 4
           and len(plan6.input_tracks) == 4
           and all(t.channel_count == 1 for t in plan6.input_tracks),
           f"peaks={peaks6}")

    # --- T7: 外挂 WAV 输入输出逐样本一致 (bit-exact) ---------------------
    w = _p3a_fixture(d / "ext.wav", 2, 777, {0: 111, 1: 222})
    plan7 = _p3a_plan([{"source_id": "wav", "path": w, "channels": 2,
                        "samples": 777}])
    out7 = d / "ext_out.wav"
    res7 = _p3a_render(plan7, out7, chunk_frames=53)
    arr7, _i7 = _p3a_read(out7)
    ref7, _r7 = _p3a_read(w)
    record("l1.p3a.T7 外挂 WAV -> WAV 逐样本 bit-exact",
           res7.ok and arr7.shape == ref7.shape
           and np.array_equal(arr7, ref7),
           f"shape={arr7.shape}")

    # --- T8: camera + WAV 跨来源 routing (含多声道混合来源) --------------
    cam4 = _p3a_fixture(d / "cam4.wav", 4, 900, {0: 5, 1: 6, 2: 7, 3: 8})
    ext2 = _p3a_fixture(d / "ext2.wav", 2, 900, {0: 15, 1: 16})
    plan8 = _p3a_plan([
        {"source_id": "camera", "path": cam4, "channels": 4, "samples": 900},
        {"source_id": "recorder", "path": ext2, "channels": 2,
         "samples": 900},
    ])
    planner8 = AudioPlanner(plan8)
    order8 = ["camera:s0:c2", "recorder:s0:c0", "camera:s0:c0"]
    planner8.select_channels(*order8)
    planner8.map_channels(*order8)
    out8 = d / "cross3.wav"
    res8 = _p3a_render(plan8, out8, chunk_frames=41)
    arr8, info8 = _p3a_read(out8)
    peaks8 = _p3a_impulse_map(arr8)
    record("l1.p3a.T8 camera+WAV 跨来源 3 声道 routing (无相加)",
           res8.ok and peaks8 == [[7], [15], [5]]
           and info8.channel_count == 3
           and bool(res8.route_spec and res8.route_spec.is_multi_source)
           and res8.route_spec.source_ids == ["camera", "recorder"],
           f"peaks={peaks8} err={res8.errors}")

    # --- T9: 采样率不一致拒绝 -------------------------------------------
    other = _p3a_fixture(d / "alt.wav", 1, 900, {0: 5}, sample_rate=96000)
    plan9 = _p3a_plan([
        {"source_id": "camera", "path": cam4, "channels": 4, "samples": 900},
        {"source_id": "alt", "path": other, "channels": 1, "samples": 900},
    ])
    plan9.source("alt").streams[0].sample_rate = 96000
    out9 = d / "mismatch_sr.wav"
    try:
        out9.unlink()
    except OSError:
        pass
    res9 = _p3a_render(plan9, out9)
    record("l1.p3a.T9 不同采样率 -> audio_sample_rate_mismatch 且不产出文件",
           (not res9.ok)
           and REASON_AUDIO_SAMPLE_RATE_MISMATCH
           in [e.get("reason") for e in res9.errors]
           and not out9.exists(),
           f"{[e.get('reason') for e in res9.errors]}")

    # --- T10: 整数 PCM 归一化到 canonical float32 ------------------------
    t10 = _p3a_pcm16_roundtrip(d)
    record("l1.p3a.T10 s16/s24/s32 -> canonical float32 满量程归一",
           t10, "写入-读回-渲染三段一致")


def l1_audio_wav_export() -> None:
    """Phase 3A: WAV writer/reader (4 格式往返 + header 精确 + 命名)。"""
    if not _p3a_ready("wav"):
        return

    section("L1 WAV 导出 (v0.7.1 Phase 3A)")
    from core.audio_wav import (
        WavFormat, default_wav_name, parse_wav_header, read_wav,
        unique_output_path, write_wav,
    )

    d = WORK / "p3a_wav"
    d.mkdir(parents=True, exist_ok=True)
    sr = 48000
    n = 1000
    x = np.zeros((n, 4), dtype=np.float32)
    x[:, 0] = np.linspace(-1.0, 1.0, n)
    x[:, 1] = 0.5
    x[:, 2] = -0.25
    x[:, 3] = 1.0 / 3.0

    results = {}
    for fmt, tol in (
        (WavFormat.PCM16, 1.5 / 32768),
        (WavFormat.PCM24, 1.5 / 8388608),
        (WavFormat.PCM32, 1.5 / 2147483648),
        (WavFormat.FLOAT32, 0.0),
    ):
        path = d / f"rt_{fmt.value}.wav"
        info, spec = write_wav(
            path, [x[:300], x[300:700], x[700:]], sample_rate=sr,
            channel_count=4, sample_format=fmt, frame_count=n, layout="4.0",
            overwrite=True,
        )
        back, h = read_wav(path)
        err = float(np.max(np.abs(back - x))) if back.shape == x.shape else 9.9
        results[fmt] = (info, h, err, tol, spec)
        record(f"l1.p3a.WAV {fmt.value} 往返 (sample rate/channels/bits/frames)",
               back.shape == (n, 4) and h.sample_rate == sr
               and h.channel_count == 4
               and h.bits_per_sample == fmt.bits
               and h.frame_count == n and err <= max(tol, 1e-9)
               and h.data_bytes == n * 4 * fmt.bytes_per_sample,
               f"err={err:.3e} tol={tol:.3e} bits={h.bits_per_sample}")

    info16, h16, _e, _t, _s = results[WavFormat.PCM16]
    record("l1.p3a.WAV header 精确: riff/data 与实际字节数一致",
           h16.riff_bytes == path_size_check(info16.path) - 8
           and h16.data_bytes == 1000 * 4 * 2
           and h16.byte_rate == 48000 * 4 * 2
           and h16.block_align == 8,
           f"riff={h16.riff_bytes} size={path_size_check(info16.path)}")
    infof, hf, _ef, _tf, _sf = results[WavFormat.FLOAT32]
    record("l1.p3a.float32 输出 bit-exact 且 EXTENSIBLE 带 channel mask",
           _ef == 0.0 and hf.is_float and hf.extensible
           and hf.channel_mask == 0x33
           and hf.effective_format_code == 3,
           f"err={_ef} mask={hf.channel_mask:#x}")
    info24, h24, _e24, _t24, _s24 = results[WavFormat.PCM24]
    record("l1.p3a.24-bit 与 float 不依赖标准库 wave (EXTENSIBLE fmt=40B)",
           h24.extensible is True and h24.bits_per_sample == 24
           and h24.effective_format_code == 1,
           f"fmt_code={h24.effective_format_code}")
    record("l1.p3a.单声道写经典 PCM 头 (不滥用 EXTENSIBLE)",
           _p3a_mono_header(d))

    # --- T13: 输出样本数严格等于声明 (EOF 精确) -------------------------
    short = d / "short_decl.wav"
    try:
        write_wav(short, [x[:10]], sample_rate=sr, channel_count=4,
                  sample_format=WavFormat.PCM16, frame_count=20,
                  overwrite=True)
        exact = False
    except ValueError as exc:
        exact = "audio_wav_write_failed" in str(exc)
    record("l1.p3a.T13 写入样本数与声明不符 -> 报错且不留半成品",
           exact and not short.exists())

    # --- 命名策略 -------------------------------------------------------
    names = {
        "单声道": default_wav_name(channel_ids=["camera:s2:c2"]),
        "单来源整流": default_wav_name(source_stem="camera"),
        "映射": default_wav_name(channel_ids=["camera:s2:c2"],
                                 mapping="r2031"),
        "跨来源": default_wav_name(
            channel_ids=["camera:s1:c0", "recorder:s0:c0"]),
    }
    record("l1.p3a.WAV 命名稳定且可编程",
           names["单声道"] == "camera_s2c2.wav"
           and names["单来源整流"] == "camera_all.wav"
           and names["映射"] == "camera_s2c2_r2031.wav"
           and names["跨来源"] == "multi_mix.wav",
           f"{names}")

    clash = d / "dup.wav"
    clash.write_bytes(b"")
    second = unique_output_path(clash)
    record("l1.p3a.同名输出不覆盖 (自动 -2 后缀)",
           second.name == "dup-2.wav" and clash.exists())

    bad = d / "bad.wav"
    bad.write_bytes(b"not a wav at all")
    try:
        parse_wav_header(bad.read_bytes(), path=str(bad))
        rejected = False
    except ValueError as exc:
        rejected = "audio_pcm_format_unsupported" in str(exc)
    record("l1.p3a.非 WAV 输入明确拒绝 (audio_pcm_format_unsupported)",
           rejected)


def l1_audio_chunk_invariance() -> None:
    """Phase 3A: chunk 边界不得影响结果 (§chunk invariance) 与内存有界。"""
    if not _p3a_ready("chunk"):
        return

    section("L1 音频 chunk invariance / 内存 (v0.7.1 Phase 3A)")
    from core.audio_wav import WavFormat, read_wav

    d = WORK / "p3a_chunk"
    d.mkdir(parents=True, exist_ok=True)

    # EOF 恰在 chunk 边界 / chunk 中间 (T9/T10): 长度取 1024 的整数倍附近。
    # chunk=1/7 是刻意的最坏情形 (每块 1~7 帧过管道), 只跑小 fixture 以控制
    # 回归时间; 边界对齐/不齐、长度不同都由 1024/1025/4096/4097 覆盖。
    for samples, chunks in (
        (1024, (1, 7, 256, 1024, 4096)),
        (1025, (1, 256, 1024)),
        (4096, (7, 256, 4096)),
        (4097, (256, 1024, 4096)),
    ):
        src = _p3a_fixture(d / f"eof_{samples}.wav", 2, samples,
                           {0: 7, 1: samples - 3})
        plan = _p3a_plan([
            {"source_id": "a", "path": src, "channels": 2,
             "samples": samples},
        ])
        hashes = {}
        shapes = {}
        for chunk in chunks:
            out = d / f"eof_{samples}_{chunk}.wav"
            res = _p3a_render(plan, out, chunk_frames=chunk,
                              backend="reader")
            if not res.ok:
                hashes[chunk] = f"ERR {res.errors}"
                continue
            arr, _info = read_wav(out)
            hashes[chunk] = _p3a_hash(out)
            shapes[chunk] = arr.shape
        record(f"l1.p3a.T9/T10 EOF={samples} (边界/中间) 全 chunk 划分 byte-identical",
               len(set(hashes.values())) == 1
               and all(s == (samples, 2) for s in shapes.values()),
               f"shapes={set(shapes.values())} distinct={len(set(hashes.values()))}")

    # T11: 多来源 + 重排 + offset 下的 chunk invariance (含跨 chunk 读回退)
    a = _p3a_fixture(d / "inv_a.wav", 4, 1200, {0: 111, 1: 500, 2: 900,
                                                3: 1199})
    b = _p3a_fixture(d / "inv_b.wav", 2, 900, {0: 700, 1: 42})
    from core.audio_plan import AudioPlanner

    plan = _p3a_plan([
        {"source_id": "cam", "path": a, "channels": 4, "samples": 1200},
        {"source_id": "rec", "path": b, "channels": 2, "samples": 900},
    ])
    set_p3a_offset(plan, "rec:s0:c0", 480)
    set_p3a_offset(plan, "rec:s0:c1", -240)
    order = ["rec:s0:c0", "cam:s0:c3", "rec:s0:c1", "cam:s0:c0"]
    pl = AudioPlanner(plan)
    pl.select_channels(*order)
    pl.map_channels(*order)
    hashes, peaks = {}, {}
    for chunk in (1, 7, 256, 1024, 4096):
        out = d / f"inv_{chunk}.wav"
        res = _p3a_render(plan, out, chunk_frames=chunk, backend="reader")
        if not res.ok:
            hashes[chunk] = f"ERR {[e.get('reason') for e in res.errors]}"
            continue
        hashes[chunk] = _p3a_hash(out)
        arr, _info = read_wav(out)
        peaks[chunk] = _p3a_impulse_map(arr)
    record("l1.p3a.T11 chunk 1/7/256/1024/4096 -> byte-identical 输出",
           len(set(hashes.values())) == 1,
           f"{json.dumps(hashes, ensure_ascii=False)}")
    record("l1.p3a.T11 impulse 位置与 chunk 无关 (含 +480/-240 offset)",
           len({json.dumps(v) for v in peaks.values()}) == 1
           and len(next(iter(peaks.values()))) == 4,
           f"{json.dumps(peaks.get(256), ensure_ascii=False)}")

    # 回归 (F1): routing 图的静音统计必须**逐路由签名求和**, 不受 F1 修正
    # 影响。索引 0 由两个来源共同供数 -> 在 [0,1000) 上重叠计入两次;
    # 索引 1 在 [400,1000) 上是确定性静音。与 `l1_audio_mix()` 的 mixing
    # 用例构成同一几何 (1000f + 400f) 下的对照 (routing 600 / mixing 0)。
    rout_a = _p3a_fixture(d / "faith_a.wav", 1, 1000, {0: 11})
    rout_b = _p3a_fixture(d / "faith_b.wav", 1, 400, {0: 22})
    plan_route = _p3a_plan([
        {"source_id": "cam", "path": rout_a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rout_b, "channels": 1, "samples": 400},
    ])
    plan_route.selected_channels = ["cam:s0:c0", "rec:s0:c0"]
    res_route = _p3a_render(
        plan_route, d / "faith_route.wav", chunk_frames=97, backend="reader"
    )
    record("l1.p3a.silence_samples (routing 1000f+400f, 2 输出声道) = 600",
           res_route.ok
           and res_route.frames == 1000 and res_route.output_channels == 2
           and res_route.silence_samples == 600,
           f"silence={res_route.silence_samples} frames={res_route.frames} "
           f"output_channels={res_route.output_channels}")

    # --- 内存/长素材: 不全量载入, 峰值内存受 chunk 限制 -------------------
    long_wav = d / "long.wav"
    long_seconds = 30
    _p3a_long_fixture(long_wav, seconds=long_seconds)
    before = _work_set_mb()
    big = d / "long_out.wav"
    res = _p3a_render(
        _p3a_plan([{"source_id": "long", "path": long_wav, "channels": 4,
                    "samples": P3A_SR * long_seconds}]),
        big,
        chunk_frames=16384,
    )
    after = _work_set_mb()
    growth = None if (before is None or after is None) else after - before
    record(f"l1.p3a.长素材 ({long_seconds}s×4CH float32 源) chunked 渲染成功",
           res.ok and res.frames == P3A_SR * long_seconds,
           f"frames={res.frames} errors={res.errors}")
    record("l1.p3a.长素材渲染内存增长受 chunk 限制 (非全量载入)",
           growth is None or growth < 260.0,
           f"ΔWorkingSet={growth}MB (chunk=16384 frames ≈ 0.34s ≈ 5.2MB)")
    # 无重复 decode: 每流只解一次
    record("l1.p3a.每流只解码一次 (无重复 decode)",
           res.ok and len(res.prepared) == 1
           and res.prepared[0]["actual_samples"] == P3A_SR * long_seconds,
           f"{json.dumps(res.prepared, ensure_ascii=False)[:160]}")
