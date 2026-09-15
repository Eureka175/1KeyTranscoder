#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 音频集成: 模型 probe / 选择映射 / PCM 路由+WAV / 混音 / 任意 reference.

真实素材 + 真实 ffmpeg 解码; 输入全部自建于 `work/autotest/` 下。
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
from ..paths import FFMPEG
from ..paths import IN_DIR
from ..paths import ROOT
from ..fixtures.plans import _p3a_build_plan
from ..fixtures.plans import _p3a_integer_pcm
from ..fixtures.plans import _p3a_ready
from ..fixtures.plans import _p3a_render
from ..fixtures.audio import _raw_audio_stream
from ..paths import ffprobe_json
from ..paths import record
from ..paths import section
from ..paths import sh

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def l3_audio_probe() -> None:
    """v0.7.1 Phase 1: 真实 ffprobe -> AudioStream/Channel/Track/Plan 全链.

    只做探测与建模 (不编码、不改默认音频路径): 用 ffmpeg 合成两种真实
    布局 —— 单流 4CH PCM 与 4 条 mono PCM 流 —— 再走**真实** probe_source
    与既有 channel_sync.eligible_audio, 确认模型与生产判定一致。
    """
    section("L3 音频模型 probe 集成 (v0.7.1)")
    from core.audio_models import TrackBuildMode, SyncStatus
    from core.audio_probe import audio_probe_of
    from core.channel_sync import eligible_audio

    d = IN_DIR / "audio_model"
    d.mkdir(parents=True, exist_ok=True)

    def mk(dst: Path, args: list[str], timeout: int = 600) -> bool:
        r = sh(FFMPEG, "-v", "error", "-y", *args, dst, timeout=timeout)
        return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0

    def probe_model(path: Path):
        """真实 ffprobe JSON -> (raw streams, AudioProbeResult)。

        音频专用素材没有视频流, 因此不走 probe_source (它要求视频流),
        而是直接复用同一条 ffprobe 请求形态再经模型适配层建模。
        """
        raw = ffprobe_json(path).get("streams", [])
        return raw, audio_probe_of(raw)

    # --- 情况 C: 单流 4CH PCM (真实 channel_layout=4.0) ---
    src4 = d / "audio_model_4ch.mov"
    ok4 = mk(src4, ["-f", "lavfi", "-i",
                    "anoisesrc=duration=3:sample_rate=48000:color=pink",
                    "-af", "pan=4.0|FL=c0|FR=c0|FC=c0|BC=c0",
                    "-c:a", "pcm_s24le", "-ac", "4", "-f", "mov"])
    if ok4:
        raw4, probe4 = probe_model(src4)
        trk4 = probe4.plan().input_tracks
        record("l3.audio.4CH 真实探测 1 stream / 4 channel",
               probe4.stream_count == 1 and probe4.channel_count == 4
               and len(trk4) == 1 and trk4[0].channel_count == 4
               and trk4[0].channel_indices == [0, 1, 2, 3]
               and trk4[0].layout == "4.0",
               f"{probe4.summary()['streams']}")
        record("l3.audio.4CH 单通道选择可用 (不塌缩)",
               trk4[0].select_channels([2]).source_ids
               == [f"default:s{trk4[0].source_stream_index}:c2"]
               and trk4[0].select_channels([2]).channel_count == 1)
        record("l3.audio.4CH 立体声/4ch 流仍被 channel_sync 拒绝 (判定不变)",
               eligible_audio(raw4)[0] is False,
               eligible_audio(raw4)[1])
        record("l3.audio.4CH plan 默认全选 + 保留原始",
               probe4.plan().is_default
               and probe4.plan().output_channel_count == 4)
    else:
        record("l3.audio.4CH 真实探测 1 stream / 4 channel", False,
               "合成 4CH 素材失败")

    # --- 情况 D: 4 条 mono PCM 流 ---
    parts = []
    for i in range(4):
        p = d / f"audio_model_mono{i}.mov"
        if mk(p, ["-f", "lavfi", "-i",
                  f"anoisesrc=duration=3:sample_rate=48000:color=pink:seed={i}",
                  "-c:a", "pcm_s24le", "-ac", "1", "-f", "mov"]):
            parts.append(p)
    srcm = d / "audio_model_4mono.mov"
    okm = (len(parts) == 4 and mk(
        srcm,
        ["-i", parts[0], "-i", parts[1], "-i", parts[2], "-i", parts[3],
         "-map", "0:a:0", "-map", "1:a:0", "-map", "2:a:0", "-map", "3:a:0",
         "-c", "copy", "-shortest"],
    ))
    if okm:
        raw_m, probe_m = probe_model(srcm)
        record("l3.audio.4×mono 真实探测 4 stream / 4 channel",
               probe_m.stream_count == 4 and probe_m.channel_count == 4
               and probe_m.is_multi_mono, f"{probe_m.summary()['streams']}")
        record("l3.audio.4×mono audio_position 与容器 index 一致",
               [s.audio_position for s in probe_m.streams] == [0, 1, 2, 3]
               and probe_m.stream_indices == sorted(probe_m.stream_indices))
        record("l3.audio.4×mono eligible_audio 仍判可对齐 (生产判定一致)",
               eligible_audio(raw_m) == (True, ""), eligible_audio(raw_m)[1])
        plan_m = probe_m.plan()
        record("l3.audio.4×mono 4 track 各 1 channel",
               len(plan_m.input_tracks) == 4
               and [t.channel_indices for t in plan_m.input_tracks]
               == [[0], [0], [0], [0]]
               and [t.source_stream_index for t in plan_m.input_tracks]
               == probe_m.stream_indices)
        # 同步报告 -> 模型 (用真实流序号; 不跑算法, 只验接线)
        fake = {
            "status": "applied", "algo_version": "test", "anchor_stream": 3,
            "channels": [
                {"stream": i, "delay_samples": 100.0 * i,
                 "delay_ms": i * 2.083, "confidence": 0.9,
                 "decision": "fixed", "shift_samples": 100 * i}
                for i in range(4)
            ],
        }
        from core.audio_models import apply_channel_sync_report

        plan_m = apply_channel_sync_report(plan_m, fake)
        record("l3.audio.同步报告按音频序号回填 4 条 track",
               [t.sync_offset_samples for t in plan_m.input_tracks]
               == [0.0, 100.0, 200.0, 300.0]
               and all(t.sync_status is SyncStatus.SUCCESS
                       for t in plan_m.input_tracks)
               and [t.sync.anchor for t in plan_m.input_tracks]
               == ["stream:3"] * 4,
               f"{[t.sync_offset_samples for t in plan_m.input_tracks]}")
        record("l3.audio.同步后身份仍可追溯原始 stream/channel",
               [t.source_ids for t in plan_m.input_tracks]
               == [[f"default:s{i}:c0"] for i in probe_m.stream_indices])
    else:
        record("l3.audio.4×mono 真实探测 4 stream / 4 channel", False,
               "合成 4×mono 素材失败")

    # --- 情况 E: 2 条 stereo 流 ---
    st_parts = []
    for i in range(2):
        p = d / f"audio_model_stereo{i}.mov"
        if mk(p, ["-f", "lavfi", "-i",
                  f"anoisesrc=duration=3:sample_rate=48000:color=pink:seed={i}",
                  "-c:a", "pcm_s24le", "-ac", "2", "-f", "mov"]):
            st_parts.append(p)
    srcs = d / "audio_model_2stereo.mov"
    if len(st_parts) == 2 and mk(
        srcs, ["-i", st_parts[0], "-i", st_parts[1],
               "-map", "0:a:0", "-map", "1:a:0", "-c", "copy", "-shortest"],
    ):
        raw_s, probe_s = probe_model(srcs)
        tracks_s = probe_s.tracks(TrackBuildMode.PER_STREAM)
        record("l3.audio.2×stereo 真实探测 2 stream / 4 channel",
               probe_s.stream_count == 2 and probe_s.channel_count == 4
               and [t.channel_indices for t in tracks_s] == [[0, 1], [0, 1]])
        record("l3.audio.2×stereo 布局名可用或安全退化",
               all(s.layout_source in ("layout", "channel_count")
                   for s in probe_s.streams),
               f"{[s.layout_source for s in probe_s.streams]}")
        record("l3.audio.2×stereo 2ch 流仍被 channel_sync 拒绝 (判定不变)",
               eligible_audio(raw_s)[0] is False)
    else:
        record("l3.audio.2×stereo 真实探测 2 stream / 4 channel", False,
               "合成 2×stereo 素材失败")

    # --- 情况 F: 真实素材 layout 缺失但仍建成完整模型 ---
    real = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real.glob("*.MP4")) if real.is_dir() else []
    if real_files:
        raw_r, probe_r = probe_model(real_files[0])
        plan_r = probe_r.plan()
        record("l3.audio.真实 A7M5 4×mono 素材建模 (layout 缺失不丢流)",
               probe_r.stream_count == 4
               and [s.channel_count for s in probe_r.streams] == [1, 1, 1, 1]
               and len(plan_r.input_tracks) == 4
               and all(t.channel_count == 1 for t in plan_r.input_tracks)
               and all(t.layout == "" for t in plan_r.input_tracks),
               f"{probe_r.summary()['streams']}")
        record("l3.audio.真实素材 channel_sync 判定不变",
               eligible_audio(raw_r)[0] is True)
    else:
        record("l3.audio.真实 A7M5 4×mono 素材建模 (layout 缺失不丢流)", False,
               "testsets A7M5 4CH 素材不存在")


def l3_audio_selection() -> None:
    """v0.7.1 Phase 2: 真实探测的多来源选择 + 通道映射 (含外挂 WAV).

    真实素材:
      * Sony A7M5 4CH (testsets, 4×mono PCM, layout 缺失)  -> media source
      * ffmpeg 生成的 deterministic 外挂 WAV (mono / stereo / 4CH) -> wav source

    只做"探测 -> 建模 -> 选择 -> 映射 -> dry-run spec"; **不执行 ffmpeg**,
    因此不会改变任何生产路径 (默认音频路径仍由 AudioPlan=None 决定)。
    """
    section("L3 音频来源/选择/映射 (v0.7.1 Phase 2)")
    from core.audio_models import (
        AudioSourceType, AudioSyncResult, SyncStatus, build_multi_source_plan,
    )
    from core.audio_plan import (
        REASON_AUDIO_MIX_NOT_SUPPORTED, AudioMapStrategy, AudioPlanner,
        build_map_spec, source_plan, validate_plan,
    )
    from core.audio_probe import audio_probe_of

    d = IN_DIR / "audio_sel"
    d.mkdir(parents=True, exist_ok=True)

    def mk_wav(dst: Path, channels: int, seed: int = 0) -> bool:
        r = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
               f"anoisesrc=duration=2:sample_rate=48000:color=pink:seed={seed}",
               "-c:a", "pcm_s24le", "-ac", str(channels),
               "-rf64", "auto", dst, timeout=600)
        return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0

    wav_mono = d / "ext_mono.wav"
    wav_stereo = d / "ext_stereo.wav"
    wav_4ch = d / "ext_4ch.wav"
    ok_mono = mk_wav(wav_mono, 1, seed=1)
    ok_stereo = mk_wav(wav_stereo, 2, seed=2)
    ok_4ch = mk_wav(wav_4ch, 4, seed=3)
    record("l3.audio2.外挂 WAV fixture 生成 (mono/stereo/4CH)",
           ok_mono and ok_stereo and ok_4ch)

    def probe_of_wav(path: Path, sid: str, kind, expected_channels: int):
        raw = ffprobe_json(path).get("streams", [])
        probe = audio_probe_of(raw, source_id=sid, source_type=kind,
                               path=str(path))
        return raw, probe

    if ok_mono and ok_stereo and ok_4ch:
        _rm, mono_p = probe_of_wav(wav_mono, "wav_mono",
                                   AudioSourceType.WAV, 1)
        _rs, stereo_p = probe_of_wav(wav_stereo, "wav_stereo",
                                     AudioSourceType.WAV, 2)
        _r4, quad_p = probe_of_wav(wav_4ch, "wav_4ch",
                                   AudioSourceType.WAV, 4)
        record("l3.audio2.真实 WAV 建模 (mono/stereo/4CH)",
               mono_p.channel_count == 1 and stereo_p.channel_count == 2
               and quad_p.channel_count == 4
               and mono_p.to_source().source_type is AudioSourceType.WAV
               and quad_p.to_source().stream_count == 1
               and quad_p.streams[0].id == "wav_4ch:s0",
               f"{[p.summary()['streams'] for p in (mono_p, stereo_p, quad_p)]}")
        record("l3.audio2.WAV 时间轴独立 (不与主容器共用)",
               mono_p.to_source().timing.shares_timeline is False)

        # --- 真实 A7M5 4CH media source + 外挂 WAV source ---
        real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
        real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
        if real_files:
            raw_real = ffprobe_json(real_files[0]).get("streams", [])
            cam_p = audio_probe_of(raw_real, source_id="camera",
                                   path=str(real_files[0]))
            plan = source_plan([
                {"source_id": "camera", "streams": cam_p.streams,
                 "path": str(real_files[0])},
                {"source_id": "wav_stereo", "streams": stereo_p.streams,
                 "source_type": "wav", "path": str(wav_stereo)},
            ])
            record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划",
                   plan.source_ids == ["camera", "wav_stereo"]
                   and plan.is_multi_source()
                   and len(plan.selected_channels) == 6
                   and build_map_spec(plan).full_stream_copy,
                   f"{plan.source_ids}")
            record("l3.audio2.多来源默认 spec 全部整流 copy (逐流一个操作)",
                   [o.source_id for o in build_map_spec(plan).operations]
                   == ["camera"] * 4 + ["wav_stereo"]
                   and all(o.strategy is AudioMapStrategy.STREAM_COPY
                           for o in build_map_spec(plan).operations)
                   and [o.output_indices
                        for o in build_map_spec(plan).operations]
                   == [[0], [1], [2], [3], [4, 5]],
                   f"{[o.to_dict()['output_indices'] for o in build_map_spec(plan).operations]}")
            record("l3.audio2.真实素材 audio_position 与容器 index 分离",
                   [c.stream_index for c in
                    plan.source("camera").channels()] == [1, 2, 3, 4]
                   and [c.id for c in plan.source("camera").channels()]
                   == [f"camera:s{i}:c0" for i in (1, 2, 3, 4)]
                   and plan.input_tracks[0].metadata["audio_position"] == 0)

            # 跨来源选择: camera 的第 3 条 mono + wav 的 L
            p = AudioPlanner(plan)
            p.select_channels("camera:s3:c0", "wav_stereo:s0:c0")
            p.map_channels("wav_stereo:s0:c0", "camera:s3:c0")
            spec = p.map_spec()
            record("l3.audio2.真实素材跨来源选择 + 重排 dry-run",
                   spec.executable
                   and [a.channel_id for a in spec.assignments]
                   == ["wav_stereo:s0:c0", "camera:s3:c0"]
                   and [o.input_index for o in spec.operations] == [1, 0],
                   f"{[a.channel_id for a in spec.assignments]}")
            record("l3.audio2.跨来源 spec 保留 source 身份与 sync 字段",
                   spec.trace(1)["source_id"] == "camera"
                   and spec.trace(1)["stream_index"] == 3
                   and "sync_status" in spec.trace(1))

            # 4×mono 全程保持 4 个独立通道 (不塌缩)
            p4 = AudioPlanner(plan)
            p4.select_sources("camera")
            p4.map_channels("camera:s4:c0", "camera:s2:c0",
                            "camera:s1:c0", "camera:s3:c0")
            spec4 = p4.map_spec()
            record("l3.audio2.真实 4×mono 重排 (4,2,1,3) 逐流重排",
                   spec4.executable
                   and [a.stream_index for a in spec4.assignments]
                   == [4, 2, 1, 3]
                   and [a.channel_index for a in spec4.assignments]
                   == [0, 0, 0, 0]
                   and [o.output_indices for o in spec4.operations]
                   == [[0], [1], [2], [3]],
                   f"{[a.stream_index for a in spec4.assignments]}")
            record("l3.audio2.4×mono 重排 = 整流 copy 重排 (无需声道过滤)",
                   spec4.full_stream_copy is True
                   and all(o.strategy is AudioMapStrategy.STREAM_COPY
                           for o in spec4.operations))
            single_stream = AudioPlanner(source_plan(
                [{"source_id": "ch4", "streams": [
                    _raw_audio_stream(2, channels=4, layout="4.0")]}]))
            single_stream.select_channels("ch4:s2:c2")
            single_spec = single_stream.map_spec()
            record("l3.audio2.单流取部分声道才需要声道过滤",
                   single_spec.full_stream_copy is False
                   and single_spec.strategy is AudioMapStrategy.CHANNEL_FILTER
                   and single_spec.operations[0].stream_channel_count == 4
                   and single_spec.operations[0].channel_indices == [2])
            only_cam = AudioPlanner(plan)
            only_cam.select_sources("camera")
            record("l3.audio2.真实素材整流全选仍可走 -map copy",
                   build_map_spec(only_cam.plan).full_stream_copy is True
                   and build_map_spec(only_cam.plan).strategy
                   is AudioMapStrategy.STREAM_COPY)

            # sync 结果经选择/映射后完整保留 (§18)
            # 注意: 真实 A7M5 是 4 条 mono 流 -> 每条流一个 single-channel track,
            # 因此逐个 track 附同步状态, 而不是在一条 track 上找 4 个声道。
            p2 = AudioPlanner(plan)
            for track in p2.plan.input_tracks:
                track.channels[0].sync = AudioSyncResult(
                    status=SyncStatus.SUCCESS,
                    offset_samples=960.0 + track.channels[0].stream_index,
                    offset_ms=20.0,
                    source="channel_sync_report",
                )
            p2.select_channels("camera:s3:c0")
            p2.map_channels("camera:s3:c0")
            spec2 = p2.map_spec()
            record("l3.audio2.真实素材同步后仍可追溯 "
                   "(output 0 -> camera:s3:c0 -> +963)",
                   spec2.trace(0)["channel_id"] == "camera:s3:c0"
                   and spec2.trace(0)["sync_offset_samples"] == 963.0
                   and spec2.trace(0)["sync_status"] == "success",
                   f"{spec2.trace(0)}")
            record("l3.audio2.未选中声道的同步状态不受影响",
                   p2.plan.channel("camera:s1:c0").sync.offset_samples == 961.0)
            mix_try = AudioPlanner(plan)
            mix_try.select_channels("camera:s1:c0", "camera:s2:c0")
            mix_try.plan.mix_mode = "sum"
            mix_spec = mix_try.map_spec()
            record("l3.audio2.混音仍被拒绝 (真实素材, audio_mix_not_supported)",
                   mix_spec.executable is False
                   and mix_spec.strategy is AudioMapStrategy.MIXING
                   and REASON_AUDIO_MIX_NOT_SUPPORTED in
                   [e["reason"] for e in mix_spec.errors])
            cam_only = AudioPlanner(plan)
            cam_only.select_sources("camera")
            record("l3.audio2.真实素材输出单元一律非混音",
                   all(not t.is_mixing
                       for t in cam_only.map_spec().tracks))
        else:
            record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划", False,
                   "testsets A7M5 4CH 素材不存在")
    else:
        record("l3.audio2.真实 WAV 建模 (mono/stereo/4CH)", False,
               "WAV fixture 生成失败")
        record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划", False,
               "WAV fixture 生成失败")


def l3_audio_process() -> None:
    """v0.7.1 Phase 3A: 真实素材 PCM routing + WAV export.

    真实素材:
      * Sony A7M5 4CH (testsets, **4×mono PCM s24be**, layout 缺失) -> media source
      * ffmpeg 生成的 deterministic 外挂 WAV (mono / stereo / 4CH) -> wav source

    验证: 4CH→4CH、CH selection、reorder、外挂 WAV、camera+WAV 跨来源 ->
    WAV, 全部逐样本可追溯; 并证明 4×mono **不会**被当成一个 4CH 流。
    """
    section("L3 音频 PCM 路由 / WAV 导出 (v0.7.1 Phase 3A)")
    from core.audio_models import AudioSourceType, build_audio_streams
    from core.audio_plan import AudioPlanner
    from core.audio_wav import read_wav

    d = IN_DIR / "audio_p3a"
    d.mkdir(parents=True, exist_ok=True)
    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if not real_files:
        record("l3.p3a.真实 A7M5 素材存在", False, "testsets 素材不存在")
        return
    src_mp4 = real_files[0]

    # --- 真实素材建模 (4×mono) -----------------------------------------
    raw_streams = ffprobe_json(src_mp4).get("streams", [])
    audio_streams = build_audio_streams(raw_streams, source_id="camera")
    record("l3.p3a.真实 A7M5 = 4 条独立 mono 流 (不是 1 条 4CH 流)",
           len(audio_streams) == 4
           and all(s.channel_count == 1 for s in audio_streams)
           and [s.stream_index for s in audio_streams] == [1, 2, 3, 4]
           and [s.audio_position for s in audio_streams] == [0, 1, 2, 3]
           and audio_streams[0].codec_name == "pcm_s24be",
           f"{[(s.stream_index, s.channel_count, s.codec_name) for s in audio_streams]}")

    def plan_of(sources: list[dict[str, Any]]) -> Any:
        return _p3a_build_plan(sources)

    cam_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4),
        "streams": audio_streams,
    }])

    # --- A7M5 4CH -> 4CH WAV -------------------------------------------
    out1 = d / "a7m5_all.wav"
    res1 = _p3a_render(cam_plan, out1, chunk_frames=4096)
    arr1, info1 = read_wav(out1) if out1.exists() else (None, None)
    expected_frames = 288288          # 实测: 真实素材 6.006s @48k
    record("l3.p3a.真实 A7M5 4×mono -> 4 声道 WAV (逐声道 = 逐流)",
           res1.ok and info1 is not None
           and info1.channel_count == 4
           and info1.sample_rate == 48000
           and info1.frame_count == expected_frames
           and arr1.shape == (expected_frames, 4),
           f"ok={res1.ok} info={info1.to_dict() if info1 else None} "
           f"errors={res1.errors}")

    # 与 ffmpeg 直接解码的真实样本逐样本比对 (canonical float32 归一化正确)
    refs_real: dict[int, Any] = {}
    for position in range(4):
        rp = d / f"a7m5_real_a{position}.raw"
        rc = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4,
                "-map", f"0:a:{position}", "-vn", "-sn", "-dn",
                "-f", "f32le", "-ac", "1", rp)
        if rc.returncode == 0 and rp.is_file():
            refs_real[position] = np.fromfile(rp, dtype="<f4")
    if info1 is not None and len(refs_real) == 4:
        same = all(
            np.array_equal(arr1[:, c], refs_real[c][: info1.frame_count])
            for c in range(4)
        )
        # ⚠️ 该 testsets 素材实测为**全静音** PCM (4 条流全部 0), 因此这里
        # 断言"逐样本一致 + 未被重排/混音"而不是"有非零内容"。
        record("l3.p3a.真实 A7M5 s24be -> canonical float32 逐样本一致 "
               "(素材实测全静音, 故只断言结构一致)",
               same and info1.channel_count == 4
               and all(float(np.abs(refs_real[c]).max()) == 0.0
                       for c in range(4)),
               f"frames={info1.frame_count} "
               f"peaks={[float(np.abs(arr1[:, c]).max()) for c in range(4)]}")
    else:
        record("l3.p3a.真实 A7M5 s24be -> canonical float32 逐样本一致 "
               "(素材实测全静音, 故只断言结构一致)",
               False, "参考解码失败")

    # --- A7M5 CH selection -> mono WAV ---------------------------------
    sel_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4), "streams": audio_streams,
    }])
    AudioPlanner(sel_plan).select_channels("camera:s3:c0")
    out2 = d / "a7m5_ch3.wav"
    res2 = _p3a_render(sel_plan, out2, chunk_frames=4096)
    arr2, info2 = read_wav(out2) if out2.exists() else (None, None)
    raw3 = d / "a7m5_ch3_ref.raw"
    rr3 = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4, "-map", "0:a:2",
             "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1", raw3)
    ref3 = np.fromfile(raw3, dtype="<f4") if rr3.returncode == 0 else None
    record("l3.p3a.真实 A7M5 选单通道 -> mono WAV 且样本来自该流",
           res2.ok and info2 is not None and info2.channel_count == 1
           and ref3 is not None
           and np.array_equal(arr2[:, 0], ref3[: info2.frame_count])
           and sel_plan.selected_channels == ["camera:s3:c0"],
           f"ok={res2.ok} frames={info2.frame_count if info2 else None} "
           f"errors={res2.errors}")

    # --- A7M5 reorder (4,2,1,3) ----------------------------------------
    ord_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4), "streams": audio_streams,
    }])
    order = ["camera:s4:c0", "camera:s2:c0", "camera:s1:c0", "camera:s3:c0"]
    planner = AudioPlanner(ord_plan)
    planner.select_channels(*order)
    planner.map_channels(*order)
    out3 = d / "a7m5_reorder.wav"
    res3 = _p3a_render(ord_plan, out3, chunk_frames=4096)
    arr3, info3 = read_wav(out3) if out3.exists() else (None, None)
    refs = []
    for position in (3, 1, 0, 2):
        rp = d / f"a7m5_ref_a{position}.raw"
        rc = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4,
                "-map", f"0:a:{position}", "-vn", "-sn", "-dn",
                "-f", "f32le", "-ac", "1", rp)
        refs.append(np.fromfile(rp, dtype="<f4") if rc.returncode == 0 else None)
    ok3 = (res3.ok and info3 is not None and info3.channel_count == 4
           and all(r is not None for r in refs))
    if ok3:
        n = info3.frame_count
        ok3 = all(np.array_equal(arr3[:, c], refs[c][:n]) for c in range(4))
    record("l3.p3a.真实 A7M5 4×mono 重排 (4,2,1,3) 逐样本精确",
           bool(ok3)
           and res3.timeline.output_channel_ids == order
           and res3.route_spec is not None
           and all(r.offset_samples == 0 for r in res3.route_spec.channels),
           f"ok={res3.ok} order={res3.timeline.output_channel_ids if res3.timeline else None} "
           f"errors={res3.errors}")
    record("l3.p3a.4×mono 重排仍是「逐流整流」(不塌缩成 1 条 4CH 流)",
           bool(res3.ok and res3.route_spec is not None
                and len(res3.route_spec.channels) == 4
                and len({r.stream_id for r in res3.route_spec.channels}) == 4
                and all(r.channel_index == 0
                        for r in res3.route_spec.channels)))

    # --- 真实素材上的静音事实 (不是缺陷) --------------------------------
    if info1 is not None:
        record("l3.p3a.真实 A7M5 音频实测全静音 (4 流全 0, 非管线缺陷)",
               float(np.abs(arr1).max()) == 0.0
               and info1.frame_count == expected_frames,
               f"peak={float(np.abs(arr1).max())} frames={info1.frame_count}")

    # --- 线性 PCM 整数格式 -> canonical float32 (确定性正弦, 非静音) ----
    int_ok, int_detail = _p3a_integer_pcm(d)
    record("l3.p3a.真实 ffmpeg 解码 s16/s24/s32/f32 -> float32 逐样本一致",
           int_ok, int_detail)

    # --- 外挂 WAV -> WAV (逐样本一致) ----------------------------------
    wav_mono = d / "ext_mono.wav"
    wav_stereo = d / "ext_stereo.wav"
    wav_4ch = d / "ext_4ch.wav"
    mk_ok = True
    for path, channels, seed in ((wav_mono, 1, 11), (wav_stereo, 2, 12),
                                 (wav_4ch, 4, 13)):
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                f"anoisesrc=duration=2:sample_rate=48000:color=pink:seed={seed}",
                "-c:a", "pcm_s24le", "-ac", str(channels), path, timeout=600)
        mk_ok = mk_ok and rc.returncode == 0 and path.is_file()
    record("l3.p3a.外挂 WAV fixture 生成 (mono/stereo/4CH)", mk_ok)

    wav_streams = build_audio_streams(
        ffprobe_json(wav_stereo).get("streams", []), source_id="recorder"
    )
    wav_plan = plan_of([{
        "source_id": "recorder", "path": str(wav_stereo),
        "streams": wav_streams, "source_type": AudioSourceType.WAV,
    }])
    out4 = d / "ext_stereo_out.wav"
    res4 = _p3a_render(wav_plan, out4, chunk_frames=997)
    arr4, info4 = read_wav(out4) if out4.exists() else (None, None)
    ref4, head4 = read_wav(wav_stereo)
    record("l3.p3a.外挂 WAV (stereo, pcm_s24le) -> WAV 逐样本一致",
           res4.ok and info4 is not None and info4.channel_count == 2
           and info4.frame_count == head4.frame_count == 96000
           and np.array_equal(arr4, ref4),
           f"ok={res4.ok} frames={info4.frame_count if info4 else None} "
           f"errors={res4.errors}")

    # --- camera + WAV 跨来源 -> 多声道 WAV ------------------------------
    cross_plan = plan_of([
        {"source_id": "camera", "path": str(src_mp4), "streams": audio_streams},
        {"source_id": "recorder", "path": str(wav_stereo),
         "streams": wav_streams, "source_type": AudioSourceType.WAV},
    ])
    cross_order = ["camera:s3:c0", "recorder:s0:c0", "camera:s1:c0"]
    cross = AudioPlanner(cross_plan)
    cross.select_channels(*cross_order)
    cross.map_channels(*cross_order)
    out5 = d / "cross_source.wav"
    res5 = _p3a_render(cross_plan, out5, chunk_frames=4096)
    arr5, info5 = read_wav(out5) if out5.exists() else (None, None)
    ok5 = (res5.ok and info5 is not None and info5.channel_count == 3
           and info5.frame_count == expected_frames          # UNION: camera 更长
           and ref3 is not None)
    if ok5:
        # 短来源 (2s WAV) 在 6.006s 窗口内: 前 96000 帧真实, 其余静音
        ok5 = (np.array_equal(arr5[:96000, 1], ref4[:, 0])
               and not np.any(arr5[96000:, 1])
               and np.array_equal(arr5[:, 0], ref3[: info5.frame_count]))
    record("l3.p3a.camera + 外挂 WAV 跨来源 -> 3 声道 WAV (UNION, 短源补静音)",
           bool(ok5)
           and res5.route_spec is not None
           and res5.route_spec.is_multi_source
           and res5.silence_samples > 0,
           f"ok={res5.ok} frames={info5.frame_count if info5 else None} "
           f"silence={res5.silence_samples} errors={res5.errors}")

    # --- 真实素材上的时长/EOF 事实 --------------------------------------
    if res1.timeline is not None:
        tl = res1.timeline
        record("l3.p3a.真实素材 render window = 实际解码并集 (无 metadata 冲突)",
               tl.frame_count == expected_frames
               and tl.start_sample == 0 and tl.end_sample == expected_frames
               and not res1.duration_mismatches
               and all(c.actual_samples == expected_frames
                       for c in tl.channels),
               f"{tl.summary()}")

    for tmp in d.glob("*.raw"):
        try:
            tmp.unlink()
        except OSError:
            pass


def l3_audio_mix() -> None:
    """v0.7.1 Phase 3B: 真实素材多来源混音 (A7M5 4×mono + 外挂 WAV)。"""
    section("L3 PCM 混音 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix_l3"):
        return
    from core.audio_models import build_audio_streams
    from core.audio_mix import MixBusBuilder, MixGain, MixSink, db_to_linear
    from core.audio_wav import WavFormat, read_wav

    d = IN_DIR / "audio_p3b"
    d.mkdir(parents=True, exist_ok=True)
    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if not real_files:
        record("l3.p3b.真实 A7M5 素材存在", False, "testsets 素材不存在")
        return
    src_mp4 = real_files[0]
    cam_streams = build_audio_streams(
        ffprobe_json(src_mp4).get("streams", []), source_id="camera"
    )

    # 外挂 WAV (非静音, deterministic) 作为第二个来源
    rec_wav = d / "rec_p3b.wav"
    order = ["camera:s1:c0", "camera:s2:c0", "camera:s3:c0", "camera:s4:c0"]
    ok_mk = True
    for index, cid in enumerate(order):
        target = d / f"tone_{index}.wav"
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                f"sine=frequency={300 + 200 * index}:sample_rate=48000:"
                "duration=6.006", "-af", "volume=0.6", "-c:a", "pcm_s24le",
                "-ac", "1", target, timeout=600)
        ok_mk = ok_mk and rc.returncode == 0
        # 合并成一条 4 声道 WAV (混合来源: 4×mono 之外的另一种形态)
        if index == 0:
            inputs = ["-i", str(target)]
        else:
            inputs += ["-i", str(target)]
    if ok_mk:
        cmd = [FFMPEG, "-v", "error", "-y"]
        for index in range(4):
            cmd += ["-i", str(d / f"tone_{index}.wav")]
        cmd += ["-filter_complex",
                "[0:a][1:a][2:a][3:a]join=inputs=4:channel_layout=4.0[o]",
                "-map", "[o]", "-c:a", "pcm_s24le", rec_wav]
        rc = sh(*cmd, timeout=600)
        ok_mk = rc.returncode == 0 and rec_wav.is_file()
    record("l3.p3b.混音用外挂 4CH WAV fixture 生成 (4 路不同频率 sine)", ok_mk)

    if not ok_mk:
        return
    rec_streams = build_audio_streams(
        ffprobe_json(rec_wav).get("streams", []), source_id="recorder"
    )
    plan = _p3a_build_plan([
        {"source_id": "camera", "path": str(src_mp4), "streams": cam_streams},
        {"source_id": "recorder", "path": str(rec_wav), "streams": rec_streams},
    ])
    plan.selected_channels = ["camera:s1:c0", "recorder:s0:c0",
                              "recorder:s0:c1"]
    plan.mix_mode = "sum"
    bus = MixBusBuilder(plan).bus()
    bus.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("camera:s1:c0", 1.0),
                        MixGain("recorder:s0:c0", db_to_linear(-6.0))]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("recorder:s0:c1", 0.5)]),
    ]
    out = d / "real_mix.wav"
    res = _p3a_render(plan, out, chunk_frames=4096, mix_bus=bus)
    arr, info = read_wav(out) if out.exists() else (None, None)
    record("l3.p3b.真实 A7M5(全静音) + 外挂 WAV -> 2 声道混音 WAV",
           res.ok and info is not None and info.channel_count == 2
           and info.frame_count == 288288
           and arr is not None and float(np.abs(arr).max()) > 0.0
           and res.mix_stats is not None and res.mix_stats.outputs == 2,
           f"ok={res.ok} frames={info.frame_count if info else None} "
           f"peak={res.mix_stats.peak if res.mix_stats else None} "
           f"errors={res.errors}")

    # 轨迹可追溯: 输出 0 的两个输入都带身份与增益
    record("l3.p3b.混音输出可追溯到逐输入 (channel_id + gain)",
           bus.trace(0)["inputs"][0]["channel_id"] == "camera:s1:c0"
           and bus.trace(0)["inputs"][1]["channel_id"] == "recorder:s0:c0"
           and abs(bus.trace(0)["inputs"][1]["gain"] - 0.501187) < 1e-5
           and bus.trace(1)["is_mixing"] is False
           and bus.trace(0)["is_mixing"] is True,
           f"{bus.trace(0)}")

    # 混音输出 0 = camera(全静音) + recorder×(-6dB) -> 与参考逐样本一致。
    # ⚠️ 参考解码必须带 identity channelmap: 4 声道 WAV 会被 ffmpeg 按声明布局
    # 重排/下混 (quad), 与生产读取器的"按位置逐声道"语义不同。
    ref_path = d / "rec_mix_ref.raw"
    rc = sh(FFMPEG, "-v", "error", "-y", "-i", rec_wav, "-map", "0:a:0",
            "-af", f"channelmap=0|1|2|3,volume={db_to_linear(-6.0):.9f}",
            "-f", "f32le", "-ac", "4", ref_path, timeout=600)
    if rc.returncode == 0 and arr is not None:
        ref_all = np.fromfile(ref_path, dtype="<f4").reshape(-1, 4)
        ref = ref_all[:, 0]
        n = min(ref.shape[0], arr.shape[0])
        delta = float(np.max(np.abs(arr[:n, 0] - ref[:n]))) if n else 9.9
        cam_ref_path = d / "cam_ref.raw"
        rc2 = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4, "-map", "0:a:0",
                 "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1",
                 cam_ref_path)
        cam_peak = (float(np.abs(np.fromfile(cam_ref_path, dtype="<f4")).max())
                    if rc2.returncode == 0 else None)
        record("l3.p3b.混音输出 0 与参考 (recorder×-6dB) 逐样本一致",
               n > 100000 and delta <= 1e-6
               and cam_peak is not None and cam_peak == 0.0,
               f"n={n} max_delta={delta:.3e} camera_peak={cam_peak}")
    else:
        record("l3.p3b.混音输出 0 与参考 (recorder×-6dB) 逐样本一致",
               False, "参考解码失败")

    # 混音不改变源模型: 全部源声道仍在 (含未参与混音的), 增益只在 MixBus 上
    source_ids = [c.id for c in plan.all_channels()]
    record("l3.p3b.混音不改源声道身份/选择模型 (未参与混音的声道仍在)",
           len(source_ids) == 8                     # 4×mono + 4CH 全保留
           and plan.selected_channels == ["camera:s1:c0", "recorder:s0:c0",
                                          "recorder:s0:c1"]
           and "recorder:s0:c2" in source_ids
           and "recorder:s0:c2" not in plan.selected_channels
           and "camera:s2:c0" not in plan.selected_channels
           and plan.channel("camera:s1:c0").sync.status.value
           == "not_processed"
           and all(ch.sync.offset_samples is None for ch in plan.all_channels()),
           f"sources={source_ids} selected={plan.selected_channels}")
    record("l3.p3b.增益只存在于 MixBus (源模型上没有任何 mixer 参数)",
           not hasattr(plan.channel("camera:s1:c0"), "gain")
           and not hasattr(plan.channel("recorder:s0:c0"), "gain")
           and [g.gain for g in bus.sinks[0].inputs] == [1.0, db_to_linear(-6.0)],
           f"{[g.to_dict() for g in bus.sinks[0].inputs]}")


def l3_audio_sync() -> None:
    """真实素材: A7M5 4×mono 任意一路当 reference + 外挂 WAV 反向作 reference。"""
    section("L3 任意 reference 延迟矫正 (真实素材)")

    from core.audio_models import AudioSourceType, build_audio_streams
    from core.audio_sync import (
        apply_sync_result, clear_sync_result, estimate_sync, plan_from_plan,
    )

    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if not real_files:
        record("l3.sync.真实 A7M5 素材存在", False, "testsets 素材不存在")
        return
    src_mp4 = real_files[0]
    d = IN_DIR / "audio_sync"
    d.mkdir(parents=True, exist_ok=True)

    raw = ffprobe_json(src_mp4).get("streams", [])
    streams = build_audio_streams(raw, source_id="camera")
    from core.audio_wav import WavFormat, write_wav

    # 外挂 4CH WAV (与 camera 同长), 四路不同频率 -> 与 camera 静音轨无相关
    ext = d / "ext_sync.wav"
    ext_sr = 48000
    frames = 288288
    t = np.arange(frames, dtype=np.float32) / ext_sr
    ext_arr = np.stack([
        (0.6 * np.sin(2 * np.pi * (300 + 200 * i) * t)).astype(np.float32)
        for i in range(4)
    ], axis=1)
    write_wav(ext, [ext_arr], sample_rate=ext_sr, channel_count=4,
              sample_format=WavFormat.FLOAT32, frame_count=frames,
              overwrite=True)

    from core.audio_models import (
        AudioPlan, AudioSource, AudioTrackBuilder,
    )

    plan = AudioPlan()
    tracks: list[Any] = []
    tracks.extend(AudioTrackBuilder(streams, source_id="camera").tracks())
    plan.sources.append(AudioSource(source_id="camera", path=str(src_mp4),
                                    streams=streams))
    ext_streams = build_audio_streams([{
        "index": 0, "codec_type": "audio", "codec_name": "pcm_f32le",
        "sample_rate": str(ext_sr), "channels": 4,
        "channel_layout": "4.0", "sample_fmt": "flt",
        "duration": f"{frames / ext_sr:.9f}", "nb_frames": str(frames),
        "codec_long_name": "PCM 32-bit floating point",
    }], source_id="external")
    tracks.extend(AudioTrackBuilder(ext_streams, source_id="external").tracks())
    plan.sources.append(AudioSource(
        source_id="external", source_type=AudioSourceType.WAV,
        path=str(ext), streams=ext_streams,
    ))
    plan.input_tracks = tracks
    plan.selected_tracks = [t.track_id for t in tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]

    ids = list(plan.selected_channels)
    record("l3.sync.真实素材计划含 camera 4×mono + external 4CH (8 声道)",
           len(ids) == 8
           and "camera:s1:c0" in ids and "external:s0:c3" in ids,
           f"{ids}")

    # 任意一路当 reference —— camera 轨与 external 轨都必须能当选
    # (camera 实测全静音, 因此以它作 reference 时估计必然 insufficient_signal;
    #  这不是缺陷, 而是"reference 必须自身可测"的正确行为)
    for ref in ("external:s0:c0", "external:s0:c3", "camera:s1:c0"):
        sp = plan_from_plan(plan, ref, target_channel_ids=[
            c for c in ids if c != ref
        ])
        res = estimate_sync(plan, sp, ffmpeg=FFMPEG, work_dir=d / f"e_{ref[-1]}")
        ok = res.ok and res.offset_of(ref) == 0
        if ref.startswith("external"):
            # external 是真实可测信号: camera 全静音 -> 全部 insufficient_signal
            oks = [e.status for e in res.estimates]
            record(f"l3.sync.reference={ref}: 外挂 WAV 可作 reference (无隐式限制)",
                   ok and all(s != "success" for s in oks)
                   and all(e.reason == "sync_insufficient_signal"
                           for e in res.estimates
                           if e.channel_id.startswith("camera")),
                   f"ok={ok} statuses={oks[:3]} reasons="
                   f"{sorted({e.reason for e in res.estimates if e.reason})}")
        else:
            record(f"l3.sync.reference={ref}: 静音轨道作 reference -> 稳定失败",
                   ok and all(e.status != "success" for e in res.estimates),
                   f"ok={ok} reasons="
                   f"{sorted({e.reason for e in res.estimates if e.reason})}")

    # 外挂 WAV 内部可自同步 (4CH 同源, 理论 offset = 0)
    sp = plan_from_plan(plan, "external:s0:c0",
                        target_channel_ids=["external:s0:c1", "external:s0:c2"])
    res = estimate_sync(plan, sp, ffmpeg=FFMPEG, work_dir=d / "e_self")
    record("l3.sync.外挂 WAV 自身 4 声道可测且 offset=0 (同源无位移)",
           res.ok and res.offsets().get("external:s0:c1") == 0
           and res.offsets().get("external:s0:c2") == 0,
           f"{res.offsets()} reasons="
           f"{[e.reason for e in res.estimates if e.reason]}")

    clear_sync_result(plan, res)
