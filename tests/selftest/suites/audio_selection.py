#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7.1 Phase 2: 来源 / 选择 / 声道映射 (AudioSource / AudioMapSpec)。"""

from __future__ import annotations

import json
from pathlib import Path
from ..fixtures.audio import _raw_audio_stream
from ..paths import record
from ..paths import section

def l1_audio_selection() -> None:
    """v0.7.1 Phase 2 音频来源/选择/通道映射: T1-T19 + 分离性 + 同步 + 序列化."""
    import copy as _copy

    from core.audio_models import (
        AUDIO_MODEL_VERSION,
        AudioPlan,
        AudioSource,
        AudioSourceBuilder,
        AudioSourceType,
        AudioStream,
        AudioSyncResult,
        AudioTiming,
        SyncStatus,
        build_audio_streams,
        build_multi_source_plan,
        build_source,
    )
    from core.audio_plan import (
        REASON_AUDIO_CHANNEL_NOT_FOUND,
        REASON_AUDIO_MAPPING_NOT_SELECTED,
        REASON_AUDIO_MIX_NOT_SUPPORTED,
        REASON_AUDIO_SOURCE_NOT_FOUND,
        REASON_AUDIO_STREAM_NOT_FOUND,
        AudioMapSpec,
        AudioMapStrategy,
        AudioPlanError,
        AudioPlanner,
        AudioValidationError,
        build_map_spec,
        channel_ref,
        exclude_channels,
        map_channel,
        map_channels,
        output_tracks,
        parse_channel_ref,
        select_channels,
        select_sources,
        select_tracks,
        source_plan,
        validate_mapping,
        validate_plan,
        validate_selection,
    )
    section("L1 音频来源/选择/映射 (v0.7.1 Phase 2)")

    # --- 素材: MP4(4CH) + MP4(2×2CH) + MP4(4×mono) + 外挂 WAV(mono/stereo/4CH)
    MP4_4CH = [_raw_audio_stream(2, channels=4, layout="4.0")]
    MP4_2x2CH = [
        _raw_audio_stream(1, channels=2, layout="stereo"),
        _raw_audio_stream(2, channels=2, layout="stereo"),
    ]
    MP4_4MONO = [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    WAV_MONO = [_raw_audio_stream(0, channels=1, layout="mono",
                                  codec="pcm_s24le")]
    WAV_STEREO = [_raw_audio_stream(0, channels=2, layout="stereo",
                                    codec="pcm_s24le")]
    WAV_4CH = [_raw_audio_stream(0, channels=4, layout="4.0",
                                 codec="pcm_s24le")]

    def source_of(sid, streams, source_type=AudioSourceType.MEDIA, path=None):
        return build_source(sid, streams, source_type=source_type, path=path)

    # ---- A. Source ----
    t1 = source_of("cam", [_raw_audio_stream(1, channels=1)],
                   path="camera.mp4")
    record("audio2.T1 单 MP4 + 单 audio stream",
           t1.stream_count == 1 and t1.channel_count == 1
           and t1.source_type is AudioSourceType.MEDIA
           and t1.channels()[0].id == "cam:s1:c0"
           and t1.name == "camera.mp4",
           f"{t1.channel_ids}")
    t2 = source_of("cam", MP4_4CH, path="camera.mp4")
    record("audio2.T2 MP4 + 4CH",
           t2.stream_count == 1 and t2.channel_count == 4
           and t2.channel_ids == ["cam:s2:c0", "cam:s2:c1",
                                  "cam:s2:c2", "cam:s2:c3"])
    t3 = source_of("cam", MP4_2x2CH)
    record("audio2.T3 MP4 + 2×2CH",
           [s.channel_count for s in t3.streams] == [2, 2]
           and t3.channel_ids == ["cam:s1:c0", "cam:s1:c1",
                                  "cam:s2:c0", "cam:s2:c1"])
    t4 = source_of("cam", MP4_4MONO)
    record("audio2.T4 MP4 + 4×mono",
           [s.channel_count for s in t4.streams] == [1, 1, 1, 1]
           and t4.channel_ids == [f"cam:s{i}:c0" for i in (1, 2, 3, 4)])
    t5 = source_of("wav01", WAV_MONO, AudioSourceType.WAV, path="external.wav")
    record("audio2.T5 MP4 + 外挂 mono WAV",
           t5.source_type is AudioSourceType.WAV and t5.is_external
           and t5.stream_count == 1 and t5.channel_count == 1
           and t5.channel_ids == ["wav01:s0:c0"]
           and t5.timing.shares_timeline is False,
           f"shares_timeline={t5.timing.shares_timeline}")
    t6 = source_of("wav01", WAV_STEREO, AudioSourceType.WAV)
    record("audio2.T6 MP4 + 外挂 stereo WAV",
           t6.channel_count == 2
           and t6.channel_ids == ["wav01:s0:c0", "wav01:s0:c1"])
    t7 = source_of("wav01", WAV_4CH, AudioSourceType.WAV)
    record("audio2.T7 MP4 + 外挂 4CH WAV",
           t7.channel_count == 4
           and len(t7.streams) == 1
           and t7.channel_ids == [f"wav01:s0:c{i}" for i in range(4)])
    builder = AudioSourceBuilder()
    builder.add("cam", MP4_4CH, path="camera.mp4")
    builder.add("wav01", WAV_MONO, source_type="wav", path="a.wav")
    builder.add("wav02", WAV_STEREO, source_type="external", path="b.wav")
    record("audio2.T8 MP4 + 多个外部 WAV",
           builder.source_ids == ["cam", "wav01", "wav02"]
           and [s.input_index for s in builder.sources] == [0, 1, 2]
           and len(builder.channels()) == 7
           and builder.source("wav02").is_external)
    record("audio2.WAV 不是另一套模型 (与 media 同构)",
           isinstance(t5.streams[0], AudioStream)
           and t5.streams[0].channel_count == t5.channels()[0].channel_count
           and type(t5.channels()[0]).__name__ == "AudioChannel"
           and t2.channels()[0].__class__ is t5.channels()[0].__class__)
    try:
        builder.add("cam", MP4_4CH)
        record("audio2.重复 source_id 明确报错", False, "未报错")
    except ValueError as exc:
        record("audio2.重复 source_id 明确报错", "cam" in str(exc))
    record("audio2.source_id + stream_index 唯一定位一条流",
           t3.stream(1).id == "cam:s1" and t3.stream(2).id == "cam:s2"
           and t3.stream(1).id != t3.stream(2).id)
    record("audio2.AudioStream/AudioChannel 身份含 source",
           t5.streams[0].source_id == "wav01"
           and t5.channels()[0].source_id == "wav01"
           and t5.channels()[0].stream_id == "wav01:s0")

    # ---- 跨来源身份不碰撞 ----
    cam_s0c0 = source_of("camera", [_raw_audio_stream(0, channels=2)],
                         path="camera.mp4").channels()[0]
    rec_s0c0 = source_of("recorder", [_raw_audio_stream(0, channels=2)],
                         source_type=AudioSourceType.WAV,
                         path="recorder.wav").channels()[0]
    record("audio2.跨来源同名 stream/channel 不碰撞",
           cam_s0c0.stream_index == rec_s0c0.stream_index == 0
           and cam_s0c0.channel_index == rec_s0c0.channel_index == 0
           and cam_s0c0.id != rec_s0c0.id
           and cam_s0c0.id == "camera:s0:c0"
           and rec_s0c0.id == "recorder:s0:c0",
           f"{cam_s0c0.id} vs {rec_s0c0.id}")

    # ---- 时间轴只记录, 不测量 ----
    timing = AudioTiming(start_offset_sec=0.25, duration_sec=10.0,
                         shares_timeline=False, note="manual")
    ext = build_source("wav01", WAV_STEREO, source_type="wav",
                       timing=timing)
    record("audio2.外挂来源时间轴可表达且不推断",
           ext.timing.start_offset_sec == 0.25
           and ext.timing.shares_timeline is False
           and build_source("cam", MP4_4CH).timing.shares_timeline is True)

    # ---- 计划构造 ----
    plan = build_multi_source_plan([
        {"source_id": "camera", "streams": MP4_4CH, "path": "camera.mp4"},
        {"source_id": "recorder", "streams": WAV_STEREO,
         "source_type": "wav", "path": "recorder.wav"},
    ])
    record("audio2.multi-source plan 默认 = 全选 + 保留原始",
           plan.source_ids == ["camera", "recorder"]
           and plan.is_multi_source()
           and plan.is_default
           and len(plan.selected_channels) == 6
           and [t.track_id for t in plan.input_tracks]
           == ["camera:a2c0-1-2-3", "recorder:a0c0-1"],
           f"{[t.track_id for t in plan.input_tracks]}")
    record("audio2.plan 默认 spec = 整流 copy (不进 filtergraph)",
           build_map_spec(plan).full_stream_copy
           and build_map_spec(plan).strategy is AudioMapStrategy.STREAM_COPY
           and build_map_spec(plan).ok)
    record("audio2.source_plan 与 build_multi_source_plan 等价",
           source_plan([("camera", MP4_4CH)]).source_ids == ["camera"])
    record("audio2.audio_position 与 stream_index 不混淆",
           plan.channel("camera:s2:c0").stream_index == 2
           and plan.input_tracks[0].metadata["audio_position"] == 0)

    # ---- B. Selection ----
    def fresh_plan():
        return build_multi_source_plan([
            {"source_id": "camera", "streams": MP4_4CH, "path": "camera.mp4"},
            {"source_id": "recorder", "streams": WAV_STEREO,
             "source_type": "wav", "path": "recorder.wav"},
        ])

    def names(p):
        return [c.id for c in p.selected_channel_objects()]

    p = AudioPlanner(fresh_plan())
    p.select_all()
    record("audio2.T9 4CH 全选",
           names(p.plan)[:4] == [f"camera:s2:c{i}" for i in range(4)]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3",
                                          "recorder:a0c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    record("audio2.T10 4CH 选择 [0,2]",
           names(p.plan) == ["camera:s2:c0", "camera:s2:c2"]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3"])
    two = build_multi_source_plan(
        [{"source_id": "cam", "streams": MP4_2x2CH}])
    p = AudioPlanner(two)
    p.select_tracks("cam:a2c0-1")
    record("audio2.T11 2×2CH 选择其中一个 stereo stream",
           names(p.plan) == ["cam:s2:c0", "cam:s2:c1"]
           and [t.track_id for t in p.plan.selected()] == ["cam:a2c0-1"])
    p = AudioPlanner(two)
    p.select_channels("cam:s1:c0", "cam:s2:c1")
    record("audio2.T12 2×2CH 跨 stream 选择 (stream0:L + stream1:R)",
           names(p.plan) == ["cam:s1:c0", "cam:s2:c1"]
           and p.plan.selected_tracks == ["cam:a1c0-1", "cam:a2c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2", "recorder:s0:c0")
    record("audio2.T13 MP4 + WAV 跨 source 选择",
           names(p.plan) == ["camera:s2:c2", "recorder:s0:c0"]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3",
                                          "recorder:a0c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("recorder")
    record("audio2.select_sources 按来源选择",
           names(p.plan) == ["recorder:s0:c0", "recorder:s0:c1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera", "recorder")
    record("audio2.select_sources 多来源全选",
           len(names(p.plan)) == 6)
    p = AudioPlanner(fresh_plan())
    p.exclude_channels("camera:s2:c1", "camera:s2:c3")
    record("audio2.exclude_channels 保持源顺序",
           names(p.plan) == ["camera:s2:c0", "camera:s2:c2",
                             "recorder:s0:c0", "recorder:s0:c1"])
    record("audio2.选择不改源对象身份",
           AudioPlanner(fresh_plan()).select_channels(
               "camera:s2:c0").selected_tracks == ["camera:a2c0-1-2-3"])
    p = AudioPlanner(fresh_plan())
    before = [c.id for c in p.plan.all_channels()]
    p.select_channels("camera:s2:c3")
    record("audio2.选择不破坏源模型 (原始声道仍在)",
           [c.id for c in p.plan.all_channels()] == before
           and p.plan.channel("camera:s2:c0") is not None)
    record("audio2.选择返回新计划而非破坏原计划",
           AudioPlanner(fresh_plan()).select_channels(
               "camera:s2:c0").selected_channels == ["camera:s2:c0"])

    # ---- C. Mapping ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c1")
    p.map_channels("camera:s2:c0", "camera:s2:c1")
    record("audio2.T14 identity mapping",
           [e["source_channel_id"] for e in p.plan.channel_mapping]
           == ["camera:s2:c0", "camera:s2:c1"]
           and p.plan.mapping_kind == "explicit"
           and p.plan.selected_channels == ["camera:s2:c0", "camera:s2:c1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera")
    p.map_channels("camera:s2:c0", "camera:s2:c1",
                   "camera:s2:c2", "camera:s2:c3")
    record("audio2.STREAM_COPY 仅当恰好整流自然顺序",
           p.map_spec().full_stream_copy is True
           and p.map_spec().strategy is AudioMapStrategy.STREAM_COPY
           and p.plan.mapping_kind == "explicit")
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera")
    p.map_channels("camera:s2:c2", "camera:s2:c0",
                   "camera:s2:c3", "camera:s2:c1")
    spec = p.map_spec()
    record("audio2.T15 4CH reorder (2,0,3,1)",
           [e["channel_index"] for e in p.plan.channel_mapping] == [2, 0, 3, 1]
           and p.plan.mapping_kind == "explicit"
           and spec.executable
           and spec.strategy is AudioMapStrategy.CHANNEL_FILTER
           and [spec.trace(i)["channel_index"] for i in range(4)]
           == [2, 0, 3, 1],
           f"{[e['channel_index'] for e in p.plan.channel_mapping]}")
    record("audio2.T15 reorder 不报 stream_copy",
           spec.full_stream_copy is False)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2", "recorder:s0:c0", "camera:s2:c0")
    p.map_channels("camera:s2:c2", "recorder:s0:c0", "camera:s2:c0")
    spec = p.map_spec()
    record("audio2.T16 跨来源 mapping (camera:c2, wav:c0, camera:c0)",
           spec.executable
           and [a.source_id for a in spec.assignments]
           == ["camera", "recorder", "camera"]
           and [a.channel_index for a in spec.assignments] == [2, 0, 0]
           and spec.source_ids == ["camera", "recorder"]
           and [o.strategy for o in spec.operations]
           == [AudioMapStrategy.CHANNEL_FILTER] * 3
           and [o.input_index for o in spec.operations] == [0, 1, 0],
           f"ops={[o.to_dict()['strategy'] for o in spec.operations]}")
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c1")
    try:
        p.map_channels("camera:s2:c0", "camera:s2:c0")
        record("audio2.T17 duplicate source rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T17 duplicate source rejection",
               exc.reason == "audio_mapping_output_order", exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("ghost:s0:c0")
        record("audio2.T18 missing source rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T18 missing source rejection",
               exc.reason == REASON_AUDIO_SOURCE_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("camera:s2:c7")
        record("audio2.T19 missing channel rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T19 missing channel rejection",
               exc.reason == REASON_AUDIO_CHANNEL_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("camera:s9:c0")
        record("audio2.T19 missing stream rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T19 missing stream rejection",
               exc.reason == REASON_AUDIO_STREAM_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("not-an-id")
        record("audio2.非法 id rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.非法 id rejection",
               exc.reason == REASON_AUDIO_CHANNEL_NOT_FOUND)
    single_map = AudioPlanner(fresh_plan())
    single_map.select_channels("camera:s2:c0", "camera:s2:c1")
    record("audio2.map_channel 指定单个输出位置",
           [e["source_channel_id"] for e in
            single_map.map_channel("camera:s2:c1", 0).channel_mapping]
           == ["camera:s2:c1", "camera:s2:c0"])
    record("audio2.模块级 map_channels/select_channels 便捷入口",
           [c.id for c in map_channels(
               select_channels(fresh_plan(), "camera:s2:c1"),
               "camera:s2:c1").selected_channel_objects()]
           == ["camera:s2:c1"]
           and [c.id for c in exclude_channels(
               fresh_plan(), "camera:s2:c0").selected_channel_objects()][0]
           == "camera:s2:c1")

    # ---- D. Separation: selection != mapping != mixing ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    record("audio2.D CH1+CH3 selection 仍是两个独立源声道",
           len(p.plan.selected_channels) == 2
           and [c.id for c in p.plan.selected_channel_objects()]
           == ["camera:s2:c0", "camera:s2:c2"]
           and p.plan.mix_mode is None and not p.plan.channel_map)
    spec = p.map_spec()
    record("audio2.D selection 不产生任何 PCM 合成 (每输出恰好 1 源)",
           spec.executable
           and all(len(t.channel_refs_at(i)) == 1
                   for t in spec.tracks for i in range(t.channel_count))
           and all(not t.is_mixing for t in spec.tracks)
           and spec.strategy is AudioMapStrategy.CHANNEL_FILTER)
    record("audio2.D mapping 独立于 selection (顺序可变)",
           p.plan.mapping_kind == "derived"
           and p.map_channels("camera:s2:c2", "camera:s2:c0").mapping_kind
           == "explicit"
           and len(p.plan.selected_channels) == 2)
    for label, kwargs in (
        ("选择 3 个只映射 2 个",
         dict(sel=["camera:s2:c0", "camera:s2:c1", "camera:s2:c2"],
              mapped=["camera:s2:c0", "camera:s2:c1"])),
        ("显式 mix_mode", dict(sel=["camera:s2:c0"], mix="sum")),
    ):
        pp = AudioPlanner(fresh_plan())
        pp.select_channels(*kwargs["sel"])
        if "mix" in kwargs:
            pp.plan.mix_mode = kwargs["mix"]
        else:
            try:
                pp.map_channels(*kwargs["mapped"])
                record(f"audio2.D {label} -> 拒绝", False, "未拒绝")
                continue
            except AudioPlanError as exc:
                record(f"audio2.D {label} -> audio_mix_not_supported",
                       exc.reason == REASON_AUDIO_MIX_NOT_SUPPORTED)
                continue
        record(f"audio2.D {label} -> audio_mix_not_supported",
               REASON_AUDIO_MIX_NOT_SUPPORTED in validate_plan(pp.plan))
    crafted = AudioPlanner(fresh_plan())
    crafted.select_channels("camera:s2:c0", "camera:s2:c1")
    crafted.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"},
        {"output_index": 0, "source_channel_id": "camera:s2:c1"},
    ]
    crafted.plan.mapping_kind = "explicit"
    spec = crafted.map_spec()
    record("audio2.D N 源 -> 1 输出 = audio_mix_not_supported (结构性)",
           spec.executable is False
           and spec.errors[0]["reason"] == REASON_AUDIO_MIX_NOT_SUPPORTED
           and spec.strategy is AudioMapStrategy.MIXING,
           f"{[e['reason'] for e in spec.errors]}")

    # ---- V6 / V5 ----
    v6 = AudioPlanner(fresh_plan())
    v6.select_channels("camera:s2:c1")
    v6.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"}]
    v6.plan.mapping_kind = "explicit"
    record("audio2.V6 mapping 未选择的声道被拒",
           [i.reason for i in validate_mapping(v6.plan)]
           == [REASON_AUDIO_MAPPING_NOT_SELECTED]
           and v6.map_spec().executable is False)
    v5 = AudioPlanner(fresh_plan())
    v5.select_channels("camera:s2:c0", "camera:s2:c1")
    v5.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"},
        {"output_index": 2, "source_channel_id": "camera:s2:c1"},
    ]
    v5.plan.mapping_kind = "explicit"
    record("audio2.V5 output index 必须连续 (0,2 被拒)",
           "audio_mapping_output_order" in
           [i.reason for i in validate_mapping(v5.plan)]
           and v5.map_spec().executable is False)
    record("audio2.V4 选择集重复身份被拒",
           [i.reason for i in validate_selection(
               AudioPlan(input_tracks=fresh_plan().input_tracks,
                         selected_channels=["camera:s2:c0",
                                            "camera:s2:c0"]))]
           == ["audio_source_duplicate"])

    # ---- E. Sync preservation ----
    p = AudioPlanner(fresh_plan())
    p.plan.input_tracks[0].channels[2].sync = AudioSyncResult(
        status=SyncStatus.SUCCESS, offset_samples=960.0, offset_ms=20.0,
        quality=0.91, anchor="camera:s2", source="channel_sync_report",
    )
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    p.map_channels("camera:s2:c2", "camera:s2:c0")
    spec = p.map_spec()
    ref = channel_ref(p.plan, "camera:s2:c2")
    record("audio2.E 选择+映射后 source 身份与 sync 全保留",
           ref.channel_id == "camera:s2:c2"
           and ref.source_id == "camera" and ref.stream_index == 2
           and ref.channel_index == 2
           and ref.sync_status == "success"
           and ref.sync_offset_samples == 960.0
           and ref.sync_offset_ms == 20.0,
           f"{ref.to_dict()}")
    trace = spec.trace(0)
    record("audio2.E output 0 -> camera:s2:c2 -> +960 samples 可追溯",
           trace["channel_id"] == "camera:s2:c2"
           and trace["sync_offset_samples"] == 960.0
           and trace["sync_status"] == "success")
    out_tracks = output_tracks(p.plan)
    record("audio2.E AudioOutputTrack 继承 sync",
           out_tracks[0].source_ids == ["camera:s2:c2", "camera:s2:c0"]
           and out_tracks[0].sync_of(0).offset_samples == 960.0
           and out_tracks[0].sync_of(1).status is SyncStatus.NOT_PROCESSED
           and out_tracks[0].strategy is AudioMapStrategy.CHANNEL_FILTER)

    # ---- AudioOutputTrack 语义 (§9) ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2")
    tracks1 = output_tracks(p.plan)
    record("audio2.output track = 一个 source channel (单通道输出)",
           len(tracks1) == 1 and tracks1[0].channel_count == 1
           and tracks1[0].is_mono and tracks1[0].source_ids
           == ["camera:s2:c2"])
    full = output_tracks(fresh_plan())
    record("audio2.output track 可承载整条 source track (整流)",
           len(full) == 2
           and [t.channel_count for t in full] == [4, 2]
           and all(t.strategy is AudioMapStrategy.STREAM_COPY for t in full))
    record("audio2.output track metadata 记录音频序号 (供 -map 0:a:N)",
           [t.metadata["audio_position"] for t in full] == [0, 0]
           and [t.metadata["stream_index"] for t in full] == [2, 0])
    record("audio2.output track 不携带 mixer 参数",
           not any(hasattr(t, "gain") or hasattr(t, "weights")
                   for t in full)
           and all(not t.is_mixing for t in full))
    mixed = AudioPlanner(fresh_plan())
    mixed.select_channels("camera:s2:c2", "recorder:s0:c0")
    mixed.map_channels("camera:s2:c2", "recorder:s0:c0")
    cross = output_tracks(mixed.plan)
    record("audio2.跨来源输出单元按来源分段",
           len(cross) == 2 and all(t.is_mono for t in cross)
           and [t.source_ids for t in cross]
           == [["camera:s2:c2"], ["recorder:s0:c0"]]
           and cross[0].assignments[0].source_id == "camera"
           and cross[1].assignments[0].source_id == "recorder")
    swap = AudioPlanner(fresh_plan())
    swap.select_channels("camera:s2:c0", "camera:s2:c2")
    swap.map_channels("camera:s2:c2", "camera:s2:c0")
    cross1 = output_tracks(swap.plan)
    record("audio2.输出单元可承载重排后的多个源声道",
           len(cross1) == 1 and cross1[0].channel_count == 2
           and cross1[0].source_ids == ["camera:s2:c2", "camera:s2:c0"]
           and [a.channel_index for a in cross1[0].assignments] == [2, 0]
           and not cross1[0].is_multi_source)

    # ---- AudioMapSpec (§12) ----
    spec = fresh_plan_spec = build_map_spec(fresh_plan())
    record("audio2.spec 不持有 PCM / 不执行 ffmpeg / JSON 兼容",
           json.loads(json.dumps(spec.to_dict(), ensure_ascii=False))
           == spec.to_dict()
           and not hasattr(spec, "pcm") and not hasattr(spec, "run")
           and isinstance(spec.to_dict()["assignments"], list))
    record("audio2.spec output index 稳定且连续",
           [a for a in range(spec.output_channels)]
           == [i for i in range(len(spec.assignments))]
           and [o.output_indices for o in spec.operations]
           == [[0, 1, 2, 3], [4, 5]])
    record("audio2.spec 每通道身份完整",
           spec.assignments[0].source_id == "camera"
           and spec.assignments[4].source_id == "recorder"
           and all(a.channel_id.count(":") >= 2 for a in spec.assignments))
    record("audio2.spec 不含 mixer 参数",
           "gain" not in json.dumps(spec.to_dict())
           and "weights" not in json.dumps(spec.to_dict()))
    record("audio2.spec dry-run 摘要可用",
           spec.summary()["executable"] is True
           and spec.summary()["full_stream_copy"] is True
           and spec.summary()["output_channels"] == 6)
    bad = AudioPlanner(fresh_plan())
    bad.select_channels("camera:s2:c0")
    bad.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c7"}]
    bad.plan.mapping_kind = "explicit"
    record("audio2.spec 校验失败不抛异常 (dry-run 友好)",
           bad.map_spec().executable is False
           and bad.map_spec().errors[0]["reason"]
           == REASON_AUDIO_CHANNEL_NOT_FOUND
           and bad.map_spec().summary()["errors"])

    # ---- F. 序列化 ----
    def roundtrip(obj):
        return obj.from_dict(obj.to_dict()).to_dict() == obj.to_dict()

    multi = fresh_plan()
    record("audio2.F AudioSource 往返",
           all(roundtrip(s) for s in multi.sources))
    record("audio2.F AudioStream 往返 (含 source_id/timing)",
           all(roundtrip(s) for src in multi.sources for s in src.streams)
           and AudioStream.from_dict(
               multi.sources[0].streams[0].to_dict()
           ).source_id == "camera")
    record("audio2.F AudioChannel 往返 (含 source_id)",
           all(roundtrip(c) for src in multi.sources for c in src.channels()))
    record("audio2.F AudioTrack 往返 (含 source_id)",
           all(roundtrip(t) for t in multi.input_tracks))
    record("audio2.F AudioPlan 往返 (含 sources/selection/mapping)",
           roundtrip(multi)
           and AudioPlan.from_dict(multi.to_dict()).source_ids
           == ["camera", "recorder"])
    mapped = AudioPlanner(multi)
    mapped.select_channels("camera:s2:c2", "recorder:s0:c0")
    mapped.map_channels("recorder:s0:c0", "camera:s2:c2")
    record("audio2.F mapping plan 往返",
           roundtrip(mapped.plan)
           and AudioPlan.from_dict(mapped.plan.to_dict()).mapping_kind
           == "explicit"
           and [e["channel_index"] for e in
                AudioPlan.from_dict(mapped.plan.to_dict()).channel_mapping]
           == [0, 2])
    record("audio2.F AudioMapSpec 往返",
           roundtrip(build_map_spec(mapped.plan))
           and AudioMapSpec.from_dict(
               build_map_spec(mapped.plan).to_dict()
           ).executable is True)
    record("audio2.F AudioOutputTrack / ChannelRef 往返",
           all(roundtrip(t) for t in output_tracks(multi))
           and roundtrip(channel_ref(multi, "camera:s2:c2"))
           and parse_channel_ref("cam:left:s1:c3").channel_id
           == "cam:left:s1:c3")
    record("audio2.F AudioTiming 往返",
           roundtrip(AudioTiming(start_offset_sec=0.5,
                                 shares_timeline=False)))
    record("audio2.F model_version 提升到 2",
           AUDIO_MODEL_VERSION == 2
           and multi.model_version == 2)
    record("audio2.F from_dict 容忍未来字段",
           AudioPlan.from_dict({"model_version": 99,
                                "future": {"x": 1}}).model_version == 99)

    # ---- §21 默认生产路径不变 ----
    from encoders.x265 import build_command as _x265_command
    from core.models import EffectiveParams, ScalingContext, SourceInfo
    from core.color import ColorInfo

    ctx = ScalingContext(
        reference_width=3840, reference_height=2160, reference_fps=59.94,
        spatial_factor=1.0, temporal_factor=1.0, pixel_rate_factor=1.0,
        aspect_ratio=16 / 9, source_class="NORMAL_LONG_GOP",
        normalized_ob=0.2,
    )
    src_info = SourceInfo(
        path=Path("x.mp4"), size_bytes=1, duration_sec=1.0,
        width=3840, height=2160, fps=59.94,
        r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
        codec="hevc", profile="Main 10", pix_fmt="yuv420p10le",
        bit_depth=10, chroma="4:2:0", ob_kbps=1000.0,
        video_bitrate_kbps=1000.0, video_stream_count=1, stream_info=(),
        color=ColorInfo(),
    )
    argv, _eff = _x265_command(
        Path("ffmpeg"), Path("x.mp4"), Path("x.part.mp4"),
        {"preset": "medium", "crf": "20"},
        EffectiveParams(values={"keyint": "60"}, audit={}, context=ctx),
        1, src_info,
    )
    record("audio2.§21 默认音频路径 = -map 0 + -c:a copy (未改)",
           "-map" in argv and argv[argv.index("-map") + 1] == "0"
           and "-c:a" in argv and argv[argv.index("-c:a") + 1] == "copy"
           and "--audio-tracks" not in argv
           and not any("filter_complex" in a for a in argv),
           f"argv={argv[-8:]}")
    record("audio2.§22 未新增音频 CLI",
           all(not hasattr(AudioPlanner, name) for name in
               ("cli", "execute", "run_ffmpeg"))
           and not hasattr(AudioMapSpec, "argv"))
