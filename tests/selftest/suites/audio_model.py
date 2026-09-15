#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7.1 Phase 1: 音频轨道模型 (AudioTrack / AudioChannel / AudioPlan)。"""

from __future__ import annotations

import json
from ..fixtures.audio import _raw_audio_stream
from ..paths import record
from ..paths import section

def l1_audio_model() -> None:
    """v0.7.1 Phase 1 音频模型: T1-T8 输入形态 / track 映射 / 同步 / 序列化."""
    import copy

    from core.audio_models import (
        AUDIO_MODEL_VERSION,
        AudioChannel,
        AudioPlan,
        AudioRole,
        AudioSampleFormat,
        AudioStream,
        AudioSyncResult,
        AudioTrack,
        AudioTrackBuilder,
        ChannelSyncReport,
        SyncStatus,
        TrackBuildMode,
        apply_channel_sync_report,
        normalize_channel_layout,
    )
    from core.audio_probe import (
        audio_probe_of,
        audio_streams_from_probe,
        build_audio_plan,
        build_audio_tracks,
        probe_has_audio,
    )
    section("L1 音频模型 (v0.7.1 Phase 1)")

    def model(streams: list[dict], index: int = 0):
        return audio_probe_of(streams).streams[index]

    # ---- T1: 1×mono stream ----
    t1 = model([_raw_audio_stream(2, channels=1, layout="mono")])
    record("audio.T1 mono 声道数/布局",
           t1.channel_count == 1 and t1.channel_layout == "mono"
           and t1.channel_names == ("FC",) and t1.layout_verified,
           f"{t1.channel_count} {t1.channel_layout} {t1.channel_names}")

    # ---- T2: 1×stereo stream ----
    t2 = model([_raw_audio_stream(1, channels=2, layout="stereo")])
    record("audio.T2 stereo 声道名",
           t2.channel_count == 2 and t2.channel_names == ("FL", "FR")
           and [c.channel_index for c in t2.channels()] == [0, 1])

    # ---- T3: 1×4CH stream ----
    t3 = model([_raw_audio_stream(2, channels=4, layout="4.0")])
    record("audio.T3 4CH 单流 -> 4 channel",
           t3.channel_count == 4
           and [c.channel_index for c in t3.channels()] == [0, 1, 2, 3]
           and t3.channel_names == ("FL", "FR", "FC", "BC"),
           f"{t3.channel_names}")

    # ---- T4: 4×mono streams ----
    t4_streams = [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    t4 = audio_probe_of(t4_streams)
    record("audio.T4 4×mono stream -> 4 stream / 4 channel",
           t4.stream_count == 4 and t4.channel_count == 4
           and t4.is_multi_mono and t4.stream_indices == [1, 2, 3, 4],
           f"{t4.stream_indices}")

    # ---- T5: 2×stereo streams ----
    t5 = audio_probe_of([_raw_audio_stream(2, channels=2, layout="stereo"),
                         _raw_audio_stream(3, channels=2, layout="stereo")])
    record("audio.T5 2×stereo -> 2 stream / 4 channel",
           t5.stream_count == 2 and t5.channel_count == 4
           and not t5.is_multi_mono)

    # ---- T6: unknown channel layout (声道数已知, layout 缺失) ----
    t6 = model([_raw_audio_stream(2, channels=4, layout=None)])
    record("audio.T6 未知布局不丢流",
           t6.channel_count == 4 and t6.channel_layout == ""
           and len(t6.channels()) == 4
           and [c.source_channel_name for c in t6.channels()]
           == ["C0", "C1", "C2", "C3"]
           and t6.layout_source == "layout_missing"
           and not t6.layout_verified,
           f"layout_source={t6.layout_source}")
    # 未知布局字符串同样不得崩溃, 也不得冒充标准声道
    t6b = model([_raw_audio_stream(2, channels=4, layout="mystery-4ch")])
    record("audio.T6 未知布局字符串安全退化",
           t6b.channel_count == 4 and t6b.layout_source == "layout_unknown"
           and t6b.channel_names == ("C0", "C1", "C2", "C3"))
    # 声道数与 layout 不符 -> 不按错位命名
    t6c = model([_raw_audio_stream(2, channels=4, layout="stereo")])
    record("audio.T6 布局/声道数不符不按错位命名",
           t6c.layout_source == "layout_count_mismatch"
           and t6c.channel_names == ("C0", "C1", "C2", "C3"))
    record("audio.normalize_channel_layout",
           normalize_channel_layout("  4.0 ") == "4.0"
           and normalize_channel_layout(None) == ""
           and normalize_channel_layout("N/A") == ""
           and normalize_channel_layout("unknown") == "")

    # ---- T7: metadata 缺失 ----
    t7 = model([_raw_audio_stream(1, channels=1, tags=None)])
    record("audio.T7 metadata 缺失",
           t7.metadata == {} and t7.language is None and t7.title is None
           and t7.is_default is False and t7.disposition == {})
    t7b = model([_raw_audio_stream(
        1, channels=1, tags={"language": "eng", "title": "CAM"},
        disposition={"default": 1},
    )])
    record("audio.T7 metadata 存在",
           t7b.language == "eng" and t7b.title == "CAM"
           and t7b.is_default and t7b.metadata["title"] == "CAM")

    # ---- T8: sample format 缺失 / unknown ----
    t8 = model([_raw_audio_stream(1, channels=1, sample_fmt=None)])
    t8b = model([_raw_audio_stream(1, channels=1, sample_fmt="weird24")])
    record("audio.T8 sample_fmt 缺失/未知 -> UNKNOWN",
           t8.sample_format is AudioSampleFormat.UNKNOWN
           and t8b.sample_format is AudioSampleFormat.UNKNOWN
           and AudioSampleFormat.coerce("s32") is AudioSampleFormat.S32)
    t8c = model([_raw_audio_stream(1, channels=1, sample_rate=None,
                                   duration=None, bit_rate=None)])
    record("audio.T8 sample_rate/duration/bit_rate 缺失安全",
           t8c.sample_rate == 0 and t8c.duration_sec is None
           and t8c.bit_rate is None and t8c.duration is None)

    # ---- non-audio streams 被忽略, audio_position 与 -map 0:a:N 对齐 ----
    mixed = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        _raw_audio_stream(1, channels=1),
        {"index": 2, "codec_type": "data", "codec_name": "rtmd"},
        _raw_audio_stream(3, channels=1),
    ]
    mstreams = audio_streams_from_probe(mixed)
    record("audio.非音频流被忽略 + audio_position 正确",
           [s.stream_index for s in mstreams] == [1, 3]
           and [s.audio_position for s in mstreams] == [0, 1]
           and probe_has_audio(mixed) and not probe_has_audio([mixed[0]]))
    record("audio.非法输入不崩溃",
           audio_streams_from_probe(None) == []
           and audio_streams_from_probe([{"codec_type": "audio"}])[0]
           .stream_index == -1
           and len(audio_streams_from_probe("nonsense")) == 0)

    # ---- Track: stream -> channel -> track 映射稳定性 ----
    trk = audio_probe_of([_raw_audio_stream(2, channels=4, layout="4.0")])
    tracks = trk.tracks()
    record("audio.track.4CH 流不得塌缩成不可再分的 1 个对象",
           len(tracks) == 1 and tracks[0].channel_count == 4
           and tracks[0].channel_indices == [0, 1, 2, 3]
           and tracks[0].source_ids
           == ["default:s2:c0", "default:s2:c1",
               "default:s2:c2", "default:s2:c3"],
           f"{tracks[0].track_id}")
    single = tracks[0].select_channels([2])
    record("audio.track.4CH 可取单通道 (身份保留)",
           single.channel_count == 1
           and single.channel_indices == [2]
           and single.channels[0].stream_index == 2
           and single.channels[0].channel_index == 2
           and single.source_ids == ["default:s2:c2"])
    rest = tracks[0].without_channels([1, 2])
    record("audio.track.剔除通道保持顺序",
           rest.channel_indices == [0, 3] and rest.source_ids
           == ["default:s2:c0", "default:s2:c3"])
    per_ch = trk.tracks(TrackBuildMode.PER_CHANNEL)
    record("audio.track.PER_CHANNEL 4CH -> 4 单通道 track",
           len(per_ch) == 4
           and all(t.channel_count == 1 for t in per_ch)
           and [t.track_id for t in per_ch] == ["a2c0", "a2c1", "a2c2", "a2c3"])
    mono_tracks = build_audio_tracks(
        [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    )
    record("audio.track.4×mono -> 4 track 各 1 channel",
           len(mono_tracks) == 4
           and [t.source_ids for t in mono_tracks]
           == [["default:s1:c0"], ["default:s2:c0"],
               ["default:s3:c0"], ["default:s4:c0"]]
           and all(t.metadata["audio_position"] == i
                   for i, t in enumerate(mono_tracks)))
    stereo_tracks = audio_probe_of([
        _raw_audio_stream(2, channels=2, layout="stereo"),
        _raw_audio_stream(3, channels=2, layout="stereo"),
    ]).tracks()
    record("audio.track.2×stereo -> 2 track / 4 channel",
           len(stereo_tracks) == 2
           and [t.channel_indices for t in stereo_tracks] == [[0, 1], [0, 1]]
           and [t.source_stream_index for t in stereo_tracks] == [2, 3])
    record("audio.track.role 默认 unknown 且不自动推断",
           all(t.role is AudioRole.UNKNOWN for t in tracks)
           and all(c.role is AudioRole.UNKNOWN for c in tracks[0].channels))
    tracks[0].channels[0].set_role(AudioRole.CAMERA_LEFT, "test-only")
    record("audio.track.role 显式赋值需理由",
           tracks[0].channels[0].role is AudioRole.CAMERA_LEFT
           and tracks[0].channels[0].role_reason == "test-only")
    record("audio.track.builder 等价入口",
           len(AudioTrackBuilder([_raw_audio_stream(2, channels=4)]).tracks())
           == 1
           and len(AudioTrackBuilder(
               [{"codec_type": "video"}]).tracks()) == 0)

    # ---- Plan ----
    plan = build_audio_plan([_raw_audio_stream(i, channels=1)
                             for i in (1, 2, 3, 4)])
    record("audio.plan 默认 = 全选 + 保留原始",
           plan.preserve_original and len(plan.selected_tracks) == 4
           and plan.selected() == plan.input_tracks and plan.is_default
           and plan.output_channel_count == 4)
    plan.select(["a1c0", "a3c0", "nope"])
    record("audio.plan 选择集过滤未知 id",
           plan.selected_tracks == ["a1c0", "a3c0"]
           and plan.output_channel_count == 2)
    plan.mix_mode = "sum"
    record("audio.plan 预留字段不改变默认判定",
           not plan.is_default and plan.mix_mode == "sum"
           and plan.channel_map == [] and plan.wav_outputs == [])
    record("audio.plan.select_channels 返回新对象不污染原 track",
           tracks[0].channel_count == 4)

    # ---- Sync integration: 报告 -> AudioTrack ----
    report = {
        "status": "applied",
        "detail": "ok",
        "algo_version": "2.3.0-p1",
        "anchor_stream": 2,
        "sync_mode": "transcode",
        "channels": [
            {"stream": 0, "delay_samples": 0.0, "delay_ms": 0.0,
             "confidence": 1.0, "decision": "anchor", "reason": None},
            {"stream": 1, "delay_samples": 960.0, "delay_ms": 20.0,
             "confidence": 0.91, "polarity": 1, "drift_ppm": 0.4,
             "constant": True, "decision": "fixed", "reason": None,
             "shift_samples": 960, "usable_frames": 40, "rms_dbfs": -18.2,
             "warnings": []},
            {"stream": 2, "delay_samples": 5.0, "delay_ms": 0.1,
             "confidence": 0.2, "decision": "untouched",
             "reason": "low_confidence", "warnings": ["weak"]},
            {"stream": 3, "delay_samples": None, "delay_ms": None,
             "confidence": 0.0, "decision": "untouched",
             "reason": "non_constant"},
        ],
    }
    sync_plan = build_audio_plan([_raw_audio_stream(i, channels=1)
                                  for i in (1, 2, 3, 4)])
    apply_channel_sync_report(sync_plan, report)
    s0, s1, s2, s3 = sync_plan.input_tracks
    record("audio.sync success/offset_samples/offset_ms 正确保存",
           s1.sync_status is SyncStatus.SUCCESS
           and s1.sync_offset_samples == 960.0 and s1.sync_offset_ms == 20.0
           and s1.sync.quality == 0.91 and s1.sync.anchor == "stream:2"
           and s1.channels[0].sync.applied,
           f"status={s1.sync_status} off={s1.sync_offset_samples}")
    record("audio.sync anchor -> already_aligned",
           s0.sync_status is SyncStatus.ALREADY_ALIGNED
           and not s0.channels[0].sync.applied)
    record("audio.sync low_confidence / non_constant 映射",
           s2.sync_status is SyncStatus.LOW_CONFIDENCE
           and s2.sync.reason == "low_confidence"
           and s2.channels[0].sync.warnings == ["weak"]
           and s3.sync_status is SyncStatus.NON_CONSTANT)
    record("audio.sync 不破坏原始 stream/channel 身份",
           [t.source_ids for t in sync_plan.input_tracks]
           == [["default:s1:c0"], ["default:s2:c0"],
               ["default:s3:c0"], ["default:s4:c0"]]
           and [t.source_stream_index for t in sync_plan.input_tracks]
           == [1, 2, 3, 4])
    ch4 = AudioChannel(stream_index=4, channel_index=0, channel_count=1,
                       sample_rate=48000, sample_format=AudioSampleFormat.S32)
    ch4.sync = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=960,
                               offset_ms=20.0)
    trk4 = AudioTrack(track_id="a4c0", source_stream_index=4, channels=[ch4])
    trk4.refresh_sync()
    record("audio.sync track 汇总反映声道结果",
           trk4.sync_status is SyncStatus.SUCCESS
           and trk4.sync_offset_samples == 960
           and trk4.sync_offset_ms == 20.0)
    # 报告未覆盖 / 文件级状态
    for status, want in (("not_eligible", SyncStatus.NOT_APPLICABLE),
                         ("tool_missing", SyncStatus.NOT_APPLICABLE),
                         ("measure_failed", SyncStatus.FAILED)):
        p = ChannelSyncReport({"status": status, "detail": "d"})
        r = p.resolve(None)
        record(f"audio.sync 文件级 {status} -> {want.value}",
               r.status is want and r.reason == "d")
    fresh = build_audio_plan([_raw_audio_stream(1, channels=1)])
    record("audio.sync 无报告 = not_processed",
           fresh.input_tracks[0].sync_status is SyncStatus.NOT_PROCESSED
           and not fresh.input_tracks[0].sync.processed)
    unknown_reason = ChannelSyncReport({"status": "applied"}).resolve(
        {"stream": 0, "decision": "untouched", "reason": "silent_track"}
    )
    record("audio.sync 未知 reason 归 FAILED 不崩溃",
           unknown_reason.status is SyncStatus.FAILED
           and unknown_reason.reason == "silent_track")
    record("audio.sync success 用实际移位量 shift_samples",
           ChannelSyncReport({"status": "applied"}).resolve(
               {"stream": 0, "decision": "fixed",
                "delay_samples": 960.4, "shift_samples": 960,
                "delay_ms": 20.008}
           ).offset_samples == 960.0)

    # ---- 序列化 ----
    def roundtrip(obj):
        return obj.from_dict(obj.to_dict()).to_dict() == obj.to_dict()

    probe_all = audio_probe_of([
        _raw_audio_stream(1, channels=1, layout="mono",
                          tags={"language": "eng"}, disposition={"default": 1}),
        _raw_audio_stream(2, channels=4, layout=None),
        _raw_audio_stream(3, channels=2, layout="stereo", sample_fmt=None),
    ])
    record("audio.json.stream 往返稳定",
           all(roundtrip(s) for s in probe_all.streams))
    record("audio.json.channel 往返稳定",
           all(roundtrip(c) for s in probe_all.streams for c in s.channels()))
    tracks_all = probe_all.tracks()
    record("audio.json.track 往返稳定",
           all(roundtrip(t) for t in tracks_all))
    record("audio.json.plan 往返稳定 (含同步结果)",
           roundtrip(sync_plan)
           and AudioPlan.from_dict(sync_plan.to_dict()).input_tracks[1]
           .sync_offset_samples == 960.0)
    record("audio.json.sample 与 §十三 示例同形",
           AudioChannel(
               stream_index=2, channel_index=0, sample_rate=48000,
               sample_format=AudioSampleFormat.S32,
           ).to_dict() == {
               "stream_index": 2, "channel_index": 0, "sample_rate": 48000,
               "sample_format": "s32", "enabled": True,
           })
    ch_json = AudioChannel.from_dict({
        "stream_index": 2, "channel_index": 0, "sample_rate": 48000,
        "sample_format": "s32", "enabled": True,
        "sync": {"status": "success", "offset_samples": 960, "offset_ms": 20.0},
    })
    record("audio.json.from_dict 读回示例 (channel)",
           ch_json.sync.status is SyncStatus.SUCCESS
           and ch_json.sync.offset_samples == 960.0
           and ch_json.sync.offset_ms == 20.0)
    record("audio.json 纯 JSON 可编码 (无不可序列化对象)",
           json.loads(json.dumps(sync_plan.to_dict(), ensure_ascii=False))
           == sync_plan.to_dict())
    record("audio.json 未来字段/未知 enum 不崩溃",
           AudioSyncResult.from_dict({"status": "from_the_future"}).status
           is SyncStatus.NOT_PROCESSED
           and AudioPlan.from_dict(
               {"model_version": 99, "unknown_key": {"x": 1}}).model_version
           == 99)
    record("audio.json.model_version 常量", AUDIO_MODEL_VERSION == 2)

    # ---- 兼容: raw 保留 / 既有字段复用 ----
    raw_in = _raw_audio_stream(2, channels=4, layout="4.0")
    kept = audio_probe_of([raw_in]).streams[0]
    record("audio.raw 原始 dict 原样保留",
           kept.raw == raw_in and kept.raw is not raw_in)
    # pcm_s24be + sample_fmt=s32 的真实形态必须照实保存 (不猜位深)
    real = audio_probe_of([_raw_audio_stream(
        1, channels=1, codec="pcm_s24be", sample_fmt="s32")]).streams[0]
    record("audio.真实 s24be/s32 照实保存",
           real.codec_name == "pcm_s24be"
           and real.sample_format is AudioSampleFormat.S32
           and real.bit_rate == 1152000)
    deep = audio_probe_of([_raw_audio_stream(
        1, channels=1, tags={"language": "eng"})]).streams[0]
    snapshot = deep.to_dict()
    snapshot["metadata"]["language"] = "mutated"
    snapshot["raw"]["codec_name"] = "mutated"
    record("audio.to_dict 深拷贝 (外部修改不回写模型)",
           deep.metadata["language"] == "eng"
           and deep.raw["codec_name"] == "pcm_s24le"
           and deep.to_dict()["metadata"]["language"] == "eng")
    plan_snapshot = audio_probe_of([_raw_audio_stream(
        1, channels=1, tags={"language": "eng"})]).plan()
    p_snap = plan_snapshot.to_dict()
    p_snap["input_tracks"][0]["metadata"]["language"] = "mutated"
    record("audio.plan.to_dict 深拷贝",
           plan_snapshot.input_tracks[0].metadata["language"] == "eng")
