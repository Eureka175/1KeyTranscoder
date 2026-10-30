#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.8.0: 格式感知 (PCM / compressed) + alignment 策略 + 输出编码继承。

两个 suite:

  * `l1_audio_format()` —— 纯策略层: 格式分类表、alignment 决策表、S3b 的
    **participating-only PCM 准入** (`alignment=enabled` 拒绝非 PCM 参与流,
    `auto`/`disabled` 旧行为逐字节不变)、拒绝发生在解码前的执行层证据、
    输出 codec/bitrate 的优先级链 (manual > source > encoder default)、
    PCM + bitrate 的明确拒绝、以及 v0.8.0 的架构审计。不运行 ffmpeg。
  * `l3_audio_format()` —— **真实 ffmpeg/ffprobe**: PCM 默认允许 alignment、
    compressed 默认原样保留 (不解码不重编码)、`enabled` 参与者含 AAC/Opus
    -> **显式拒绝且无产物** (A3/S3b, 不再 warning + 重编码)、未参与输出的
    压缩流不否决、AAC/Opus 手动 bitrate、以及"视频基本流 sha256 在每种音频
    处理下都不变"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..fixtures.audio import (
    _make_audio_file,
    _make_av,
    _make_av_compressed,
    _make_video_only,
    _sync_content,
    _transcode_audio,
    _video_elementary_hash,
)
from ..paths import (
    FFPROBE, FFMPEG, IN_DIR, ROOT, WORK, ffprobe_json, record, section, sh,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _audio_of(path: Path) -> list[dict[str, Any]]:
    return [
        s for s in ffprobe_json(path).get("streams", [])
        if s.get("codec_type") == "audio"
    ]


def _probe_audio_streams(path: Path) -> list[dict[str, Any]]:
    r = sh(FFPROBE, "-v", "error", "-show_streams", "-of", "json", path)
    if r.returncode != 0:
        return []
    return json.loads(r.stdout or "{}").get("streams", [])


def _stream(codec_name: str, *, channels: int = 1, sample_rate: int = 48000,
            bit_rate: int | None = None, index: int = 0) -> Any:
    from core.audio_models import AudioStream

    return AudioStream(
        stream_index=index,
        source_id="s",
        codec_name=codec_name,
        sample_rate=sample_rate,
        channel_count=channels,
        bit_rate=bit_rate,
    )


def _plan_with(*streams: Any) -> Any:
    """单一来源 (source_id="s") + 给定流 -> 生产形态的 AudioPlan。"""
    return _plan_sources({"s": list(streams)})


def _plan_sources(sources: dict[str, list[Any]]) -> Any:
    """`{source_id: [AudioStream]}` -> 带 AudioSource 的 AudioPlan。

    刻意用 `build_source` + `AudioTrackBuilder` 走**生产同一条**建模路径
    (Phase 1 那套只填 input_tracks 的写法会让格式判定走退化分支)。
    """
    from core.audio_models import (
        AudioPlan, AudioSource, AudioTrackBuilder, TrackBuildMode,
    )

    plan = AudioPlan()
    for source_id, streams in sources.items():
        for index, stream in enumerate(streams, start=1):
            stream.stream_index = index
            stream.source_id = source_id
        source = AudioSource(
            source_id=source_id, streams=list(streams), input_index=0,
        )
        plan.sources.append(source)
        plan.input_tracks.extend(
            AudioTrackBuilder(
                source.streams, source_id=source_id
            ).tracks(TrackBuildMode.PER_STREAM)
        )
    plan.selected_tracks = [t.track_id for t in plan.input_tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    return plan


def _decode_mono(path: Path, position: int, tag: str = "p5") -> Any:
    import numpy as np

    out = path.with_suffix(path.suffix + f".{tag}{position}.raw")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = sh(FFMPEG, "-v", "error", "-y", "-i", path,
           "-map", f"0:a:{position}", "-vn", "-ac", "1",
           "-f", "f32le", "-ar", "48000", out, timeout=600)
    if r.returncode != 0 or not out.is_file():
        return None
    return np.fromfile(out, dtype="<f4")


def _best_lag(a: Any, b: Any, *, max_lag: int = 64) -> int | None:
    """`a` 相对 `b` 的最佳整数滞后 (样本); 数据不足返回 None。

    用中段窗口做互相关 —— 对齐是否真的发生不能只看"我们写了什么 offset",
    还要看样本本身是否真的重合。
    """
    import numpy as np

    if a is None or b is None:
        return None
    n = min(len(a), len(b))
    if n < 4 * max_lag + 8:
        return None
    lo, hi = int(n * 0.3), int(n * 0.7)
    win = a[lo:hi]
    best, best_lag = None, 0
    for lag in range(-max_lag, max_lag + 1):
        start = lo + lag
        if start < 0 or start + len(win) > n:
            continue
        score = float(np.dot(win, b[start:start + len(win)]))
        if best is None or score > best:
            best, best_lag = score, lag
    return best_lag


def _run_1kt(input_dir: Path, *extra: str, timeout: int = 1800):
    return sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", input_dir, *extra, "--headless", timeout=timeout,
    )


# ---------------------------------------------------------------------------
# L1 — 策略层
# ---------------------------------------------------------------------------

def l1_audio_format() -> None:
    """v0.8.0: 格式分类 + alignment 决策表 + 输出编码优先级链。"""
    section("L1 格式感知 / alignment / 编码继承 (v0.8.0)")
    from core.audio_encode import AudioEncodeFormat, AudioFormatSpec
    from core.audio_format import (
        REASON_ALIGNMENT_REJECTED_NON_PCM,
        REASON_FORMAT_BITRATE_NOT_APPLICABLE,
        AudioAlignmentPolicy,
        AudioInputFormat,
        ExplicitFormat,
        classify_codec,
        format_bitrate,
        is_pcm_codec,
        participating_formats,
        resolve_alignment,
        resolve_output_format,
    )
    from core.audio_plan import AudioPlanner

    # --- 1. 格式分类: PCM codec -> uncompressed, 其它 -> compressed ------
    pcm_codecs = (
        "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
        "pcm_s24be", "pcm_u8", "pcm_s16be", "pcm_alaw", "pcm_mulaw",
    )
    record("v08.fmt 全部 PCM 家族 codec 判为 uncompressed",
           all(is_pcm_codec(c) for c in pcm_codecs)
           and all(classify_codec(c) is AudioInputFormat.PCM
                   for c in pcm_codecs),
           f"{pcm_codecs}")
    other_codecs = (
        "aac", "opus", "flac", "mp3", "vorbis", "ac3", "eac3", "dts",
        "alac", "wma", "wmav2", "truehd", "mp2",
    )
    record("v08.fmt 其它 audio codec 判为 compressed (含无损压缩 flac/alac)",
           all(classify_codec(c) is AudioInputFormat.COMPRESSED
               for c in other_codecs),
           f"{other_codecs}")
    record("v08.fmt 未知/缺失 codec 保守归 compressed (不主动解码重编码)",
           classify_codec("") is AudioInputFormat.COMPRESSED
           and classify_codec(None) is AudioInputFormat.COMPRESSED
           and classify_codec("no_such_codec") is AudioInputFormat.COMPRESSED)
    record("v08.fmt 大小写/空白不影响判定 (ffprobe 名字归一)",
           classify_codec(" PCM_S16LE ") is AudioInputFormat.PCM
           and classify_codec("AAC") is AudioInputFormat.COMPRESSED)

    pcm_plan = _plan_with(_stream("pcm_s24le", index=1))
    aac_plan = _plan_with(_stream("aac", bit_rate=192000, index=1))
    opus_plan = _plan_with(_stream("opus", bit_rate=128000, index=1))
    mixed_streams_plan = _plan_with(
        _stream("pcm_s16le", index=1), _stream("aac", index=2)
    )
    mixed_plan = _plan_sources({
        "cam": [_stream("pcm_s16le", index=1)],
        "rec": [_stream("aac", bit_rate=192000, index=1)],
    })

    from core.audio_format import plan_formats
    record("v08.fmt plan_formats 只报告**参与输出**的来源格式",
           plan_formats(pcm_plan) == {"s": AudioInputFormat.PCM}
           and plan_formats(aac_plan) == {"s": AudioInputFormat.COMPRESSED}
           and plan_formats(mixed_plan) == {
               "cam": AudioInputFormat.PCM,
               "rec": AudioInputFormat.COMPRESSED,
           },
           f"{plan_formats(mixed_plan)}")
    record("v08.fmt 同一来源内混装 PCM+compressed -> 整源保守判 compressed",
           plan_formats(mixed_streams_plan)
           == {"s": AudioInputFormat.COMPRESSED},
           f"{plan_formats(mixed_streams_plan)}")
    record("v08.fmt 被 selection 排除的来源不参与格式判定",
           plan_formats(mixed_plan, channel_ids=["cam:s1:c0"])
           == {"cam": AudioInputFormat.PCM}
           and plan_formats(mixed_plan, channel_ids=["rec:s1:c0"])
           == {"rec": AudioInputFormat.COMPRESSED},
           f"{plan_formats(mixed_plan, channel_ids=['cam:s1:c0'])}")

    # --- 2. alignment 决策表 (§4/§5; S3b = participating-only PCM 准入) --
    d = resolve_alignment(pcm_plan, AudioAlignmentPolicy.AUTO)
    record("v08.align PCM + auto -> 默认 ENABLED (§4)",
           d.enabled and d.reason == "alignment_default_pcm",
           f"{d.reason}")
    record("v08.align PCM 默认不猜 reference (必须显式给)",
           d.reference_required and not d.any_compressed
           and any("reference" in n for n in d.notes),
           f"{d.notes}")
    record("v08.align PCM 默认不产生任何 warning (不动压缩流)",
           d.warnings == [])

    d = resolve_alignment(aac_plan, AudioAlignmentPolicy.AUTO)
    record("v08.align compressed + auto -> 默认 DISABLED (§4)",
           not d.enabled and d.reason == "alignment_default_compressed",
           f"{d.reason}")
    record("v08.align compressed 默认路径不重编码 (无 warning, 走原流)",
           d.warnings == [] and not d.requires_decode)

    # --- 2a. enabled 的 PCM-only 准入: 任意 CH 数 (1/2/4/8/>8) 全 PCM 允许 --
    ch_plans = {
        1: _plan_with(_stream("pcm_s16le", index=1)),
        2: _plan_with(_stream("pcm_s16le", channels=2, index=1)),
        4: _plan_with(*[_stream("pcm_s24le", index=i)
                       for i in range(1, 5)]),
        8: _plan_with(*[_stream("pcm_s24le", index=i)
                       for i in range(1, 9)]),
        12: _plan_with(*[_stream("pcm_s24le", index=i)
                        for i in range(1, 13)]),
    }
    ch_decisions = {
        ch: resolve_alignment(plan, AudioAlignmentPolicy.ENABLED)
        for ch, plan in ch_plans.items()
    }
    record("v08.align enabled + 全 PCM 1CH/2CH/4CH/8CH/12CH -> 允许 (任意 CH 数)",
           all(
               dec.enabled and not dec.refused
               and dec.reason == "alignment_enabled_pcm"
               and not dec.requires_decode and dec.warnings == []
               for dec in ch_decisions.values()
           ),
           f"{[(ch, dec.enabled, dec.reason) for ch, dec in ch_decisions.items()]}")

    # --- 2b. enabled + 参与流含 AAC/Opus -> 显式拒绝 (不再 warning+重编码) --
    decisions = {
        codec: resolve_alignment(plan, AudioAlignmentPolicy.ENABLED)
        for codec, plan in (("aac", aac_plan), ("opus", opus_plan))
    }
    record("v08.align enabled + 参与的 AAC/Opus -> 显式拒绝 (稳定 reason)",
           all(
               not dec.enabled and dec.refused
               and dec.reason == REASON_ALIGNMENT_REJECTED_NON_PCM
               and not dec.requires_decode and dec.warnings == []
               and not dec.reference_required
               for dec in decisions.values()
           ),
           f"{[(codec, dec.reason, dec.refused) for codec, dec in decisions.items()]}")
    record("v08.align enabled 拒绝时点名非 PCM 流 (stream + codec_name)",
           all(
               dec.non_pcm_streams == [{
                   "stream": "s:s1",
                   "channel_id": "s:s1:c0",
                   "codec_name": codec,
               }]
               for codec, dec in decisions.items()
           ),
           f"{[(codec, dec.non_pcm_streams) for codec, dec in decisions.items()]}")
    record("v08.align enabled 拒绝不再走'解码 -> 重编码'链路 (A3: 只允许 PCM)",
           all(
               not dec.requires_decode
               and any("refusing instead of warning" in n for n in dec.notes)
               and any("no audio is opened, decoded or re-encoded" in n
                       for n in dec.notes)
               for dec in decisions.values()
           ),
           f"{[dec.notes for dec in decisions.values()]}")

    d = resolve_alignment(mixed_plan, AudioAlignmentPolicy.AUTO)
    record("v08.align PCM + compressed 混合 + auto -> DISABLED (compressed 否决)",
           not d.enabled and d.reason == "alignment_default_mixed"
           and d.compressed_sources == ["rec"],
           f"{d.reason} {d.formats}")
    d = resolve_alignment(mixed_plan, AudioAlignmentPolicy.ENABLED)
    record("v08.align 混合 + enabled -> 显式拒绝 (participating-only 点名压缩源)",
           d.refused and not d.enabled
           and d.reason == REASON_ALIGNMENT_REJECTED_NON_PCM
           and d.compressed_sources == ["rec"]
           and d.non_pcm_streams
           and d.non_pcm_streams[0]["stream"] == "rec:s1"
           and d.non_pcm_streams[0]["codec_name"] == "aac"
           and d.warnings == [],
           f"{d.to_dict()}")

    # --- 2c. participating-only: 文件里未参与输出的压缩流**不**否决 --------
    selected_pcm_only = _plan_with(
        _stream("pcm_s16le", index=1),
        _stream("aac", bit_rate=192000, index=2),
    )
    AudioPlanner(selected_pcm_only).select_channels("s:s1:c0")
    d = resolve_alignment(selected_pcm_only, AudioAlignmentPolicy.ENABLED)
    record("v08.align enabled + 未参与的压缩流 -> 不拒绝 (participating-only 关键)",
           d.enabled and not d.refused and d.reason == "alignment_enabled_pcm"
           and d.formats == {"s": "pcm"} and d.compressed_sources == [],
           f"{d.to_dict()}")
    record("v08.align participating_formats 只统计参与声道引用的流",
           participating_formats(selected_pcm_only)
           == {"s": AudioInputFormat.PCM}
           and plan_formats(selected_pcm_only)
           == {"s": AudioInputFormat.COMPRESSED},
           f"{participating_formats(selected_pcm_only)} vs "
           f"{plan_formats(selected_pcm_only)}")
    d = resolve_alignment(selected_pcm_only, AudioAlignmentPolicy.AUTO)
    record("v08.align auto 旧口径不变 (来源级保守: 未参与压缩流仍否决 auto)",
           not d.enabled and d.reason == "alignment_default_compressed",
           f"{d.reason}")
    d = resolve_alignment(
        mixed_plan, AudioAlignmentPolicy.ENABLED,
        channel_ids=["cam:s1:c0"],
    )
    record("v08.align enabled 显式 channel_ids 也只检查参与的声道",
           d.enabled and not d.refused
           and d.reason == "alignment_enabled_pcm"
           and d.formats == {"cam": "pcm"},
           f"{d.to_dict()}")

    # --- 2d. auto/disabled 决策字典不新增字段 (旧报告逐字节兼容) ----------
    auto_dict = resolve_alignment(aac_plan, AudioAlignmentPolicy.AUTO).to_dict()
    expected_auto = {
        "requested": "auto",
        "enabled": False,
        "reason": "alignment_default_compressed",
        "formats": {"s": "compressed"},
        "requires_decode": False,
        "reference_required": False,
        "compressed_sources": ["s"],
    }
    record("v08.align auto 决策字典字段/取值与旧版一致 (无 refused 新字段)",
           {key: auto_dict[key] for key in expected_auto} == expected_auto
           and "refused" not in auto_dict
           and "non_pcm_streams" not in auto_dict,
           f"{auto_dict}")
    disabled_dict = resolve_alignment(
        pcm_plan, AudioAlignmentPolicy.DISABLED).to_dict()
    record("v08.align disabled 决策字典字段/取值与旧版一致",
           disabled_dict["reason"] == "alignment_disabled_by_request"
           and "refused" not in disabled_dict
           and "non_pcm_streams" not in disabled_dict,
           f"{disabled_dict}")

    d = resolve_alignment(pcm_plan, AudioAlignmentPolicy.DISABLED)
    record("v08.align disabled -> 不估计不应用 (显式关闭优先)",
           not d.enabled and d.reason == "alignment_disabled_by_request")
    d = resolve_alignment(pcm_plan, "auto")
    record("v08.align 策略值宽松解析 (字符串 -> 枚举), 未知值不猜",
           AudioAlignmentPolicy.coerce("ENABLED")
           is AudioAlignmentPolicy.ENABLED
           and AudioAlignmentPolicy.coerce("nope", None) is None)

    from core.audio_models import AudioPlan
    d = resolve_alignment(AudioPlan(), AudioAlignmentPolicy.AUTO)
    record("v08.align 空计划 -> 无事可做 (不是错误)",
           not d.enabled and d.reason == "audio_alignment_no_channels")
    d = resolve_alignment(AudioPlan(), AudioAlignmentPolicy.ENABLED)
    record("v08.align enabled + 空计划同样不是拒绝 (没有参与流)",
           not d.enabled and not d.refused
           and d.reason == "audio_alignment_no_channels")

    # --- 2e. 执行层: 拒绝发生在任何解码/estimate 之前 (§4.9) --------------
    from types import SimpleNamespace
    from unittest.mock import patch

    from core.output_compose import VideoOutputArtifact
    from production.output import apply_alignment, produce_audio_output

    calls: list[str] = []

    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("estimate_sync")
        raise AssertionError(
            "admission refusal must happen before estimate/decode"
        )

    refusal_plan = _plan_with(_stream("aac", bit_rate=192000, index=1))
    with patch("core.audio_sync.estimate_sync", side_effect=_forbidden):
        refused = apply_alignment(
            refusal_plan, "s:s1:c0", ffmpeg=FFMPEG, requested="enabled")
        no_ref = apply_alignment(
            refusal_plan, "", ffmpeg=FFMPEG, requested="enabled")
        auto_out = apply_alignment(
            refusal_plan, "s:s1:c0", ffmpeg=FFMPEG, requested="auto")
    record("v08.align 执行层 enabled + AAC -> outcome 明确失败 + 稳定错误码",
           (not refused.ok)
           and [str(e.get("reason")) for e in refused.errors]
           == [REASON_ALIGNMENT_REJECTED_NON_PCM]
           and refused.applied == 0
           and refused.decision.refused
           and "AUDIO PLAN REJECTED"
           in str(refused.errors[0].get("detail")),
           f"{refused.errors}")
    record("v08.align 拒绝发生在 estimate/decode 之前 (estimate_sync 未被调用)",
           calls == [] and refused.decision.requires_decode is False
           and refused.plan is None and refused.statuses == {}
           and refused.offsets == {},
           f"estimate_calls={calls} plan={refused.plan}")
    record("v08.align 拒绝先于 reference 检查 (缺 reference 也先报非 PCM)",
           [str(e.get("reason")) for e in no_ref.errors]
           == [REASON_ALIGNMENT_REJECTED_NON_PCM],
           f"{no_ref.errors}")
    record("v08.align auto + compressed 执行层旧行为不变 (跳过不是报错)",
           auto_out.ok and not auto_out.decision.enabled
           and auto_out.decision.reason == "alignment_default_compressed"
           and calls == [],
           f"{auto_out.to_dict()}")

    # 生产编排 (1kt.py `_apply_audio_plan` 消费的就是这一层):
    # outcome.ok=False 才会让调用方返回 False -> 非零退出, 不产出半成品。
    rejection_request = SimpleNamespace(
        alignment="enabled", sync_reference="s:s1:c0")
    rejected_path = WORK / "s3b_plan_rejected.mp4"
    try:
        rejected_path.unlink()
    except OSError:
        pass
    oc = produce_audio_output(
        plan=refusal_plan,
        video=VideoOutputArtifact(path="not-used.mp4"),
        output_path=rejected_path,
        ffmpeg=FFMPEG,
        request=rejection_request,
    )
    record("v08.align 生产编排: 拒绝 -> outcome.ok=False + 同一稳定错误码",
           (not oc.ok) and oc.reasons == [REASON_ALIGNMENT_REJECTED_NON_PCM]
           and not oc.applied
           and oc.alignment is not None and oc.alignment.refused,
           f"{oc.summary()}")
    record("v08.align 生产编排: 拒绝不产出半成品 (目标文件未创建)",
           not rejected_path.exists(),
           f"exists={rejected_path.exists()}")

    # --- 3. 输出编码: manual > source-derived > encoder default (§8) -----
    record("v08.codec PCM 输入默认输出 PCM (§6: 不自动 PCM -> AAC)",
           resolve_output_format(
               pcm_plan, requested=AudioFormatSpec(),
           ).spec.format is AudioEncodeFormat.PCM,
           f"{resolve_output_format(pcm_plan).summary()}")
    r = resolve_output_format(aac_plan)
    record("v08.codec AAC 输入默认保持 AAC (§7)",
           r.spec.format is AudioEncodeFormat.AAC
           and r.codec_origin == "source", f"{r.summary()}")
    r = resolve_output_format(opus_plan)
    record("v08.codec Opus 输入默认保持 Opus (§35)",
           r.spec.format is AudioEncodeFormat.OPUS
           and r.codec_origin == "source", f"{r.summary()}")

    r = resolve_output_format(
        aac_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.AAC,
                                  bitrate="128k"),
        explicit=ExplicitFormat(format=True, bitrate=True),
    )
    record("v08.codec source AAC 192k + manual AAC 128k -> AAC 128k (§8)",
           r.spec.format is AudioEncodeFormat.AAC and r.spec.bitrate == "128k"
           and r.codec_origin == "manual"
           and r.bitrate_origin == "manual", f"{r.summary()}")
    r = resolve_output_format(
        aac_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.OPUS,
                                  bitrate="96k"),
        explicit=ExplicitFormat(format=True, bitrate=True),
    )
    record("v08.codec source AAC 192k + manual Opus 96k -> Opus 96k (§8)",
           r.spec.format is AudioEncodeFormat.OPUS and r.spec.bitrate == "96k",
           f"{r.summary()}")
    r = resolve_output_format(opus_plan)
    record("v08.codec source Opus 128k + 无覆盖 -> Opus 128k (bitrate 继承)",
           r.spec.format is AudioEncodeFormat.OPUS
           and r.spec.bitrate == "128k"
           and r.bitrate_origin == "source", f"{r.summary()}")
    r = resolve_output_format(aac_plan)
    record("v08.codec AAC 输入默认继承 192k (source-derived bitrate)",
           r.spec.bitrate == "192k" and r.bitrate_origin == "source",
           f"{r.summary()}")
    r = resolve_output_format(
        aac_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.OPUS),
        explicit=ExplicitFormat(format=True),
    )
    record("v08.codec 手动换 codec 且未指定 bitrate -> 不搬运原码率",
           r.spec.bitrate == "" and r.bitrate_origin == "none",
           f"{r.summary()}")
    r = resolve_output_format(_plan_with(_stream("vorbis", bit_rate=160000)))
    record("v08.codec 表里没有的 codec -> encoder default + warning (不静默)",
           r.spec.format is AudioEncodeFormat.AAC
           and r.codec_origin == "encoder_default"
           and any("audio_format_inherit_unavailable" in w
                   for w in r.warnings),
           f"{r.summary()} {r.warnings}")

    # bitrate 数值格式化 (不猜, 不丢精度)
    record("v08.codec bitrate 格式化: 整千用 k, 否则保留精确值",
           format_bitrate(192000) == "192k"
           and format_bitrate(127999) == "127999"
           and format_bitrate(0) == "" and format_bitrate(None) == ""
           and format_bitrate("96000") == "96k")

    # --- 4. PCM/FLAC 不允许 bitrate (§10) --------------------------------
    r = resolve_output_format(
        pcm_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.PCM,
                                  bitrate="128k"),
        explicit=ExplicitFormat(format=True, bitrate=True),
    )
    record("v08.codec PCM 显式指定 bitrate -> 明确拒绝 (§10)",
           not r.ok
           and r.reasons == [REASON_FORMAT_BITRATE_NOT_APPLICABLE],
           f"{r.summary()}")
    r = resolve_output_format(
        pcm_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.FLAC,
                                  bitrate="128k"),
        explicit=ExplicitFormat(format=True, bitrate=True),
    )
    record("v08.codec FLAC (lossless) 带 bitrate 同样拒绝",
           not r.ok and r.reasons == [REASON_FORMAT_BITRATE_NOT_APPLICABLE])
    r = resolve_output_format(
        pcm_plan,
        requested=AudioFormatSpec(format=AudioEncodeFormat.AAC,
                                  bitrate="128k"),
        explicit=ExplicitFormat(format=True, bitrate=True),
    )
    record("v08.codec PCM -> AAC 128k 允许 (§10 显式转换)",
           r.ok and r.spec.format is AudioEncodeFormat.AAC
           and r.spec.bitrate == "128k", f"{r.summary()}")

    # --- 5. alignment 不改变 mapping / 编码不改变 mapping (§13/§14) ------
    four = _plan_with(
        *[_stream("pcm_s16le", index=i) for i in (1, 2, 3, 4)]
    )
    from core.audio_plan import effective_mapping
    before = [e.get("source_channel_id") for e in effective_mapping(four)]
    resolve_output_format(four)
    resolve_alignment(four, AudioAlignmentPolicy.ENABLED)
    after = [e.get("source_channel_id") for e in effective_mapping(four)]
    record("v08.map 策略解析不改变输出身份/顺序 (§13)",
           before == after == [f"s:s{i}:c0" for i in (1, 2, 3, 4)],
           f"{after}")

    # --- 6. 架构审计: 策略模块不认识 PCM / argv / 视频 ----------------
    import ast as _ast

    import core.audio_format as _af
    import core.audio_output_structure as _aos
    import core.audio_external as _ae

    forbidden = {
        "audio_mix", "audio_pcm", "audio_route", "audio_wav", "audio_process",
        "encoders", "preservation", "batch_hw",
    }
    leaks: list[str] = []
    for module in (_af, _aos, _ae):
        tree = _ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in _ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                if set(mod.split(".")) & forbidden:
                    leaks.append(f"{module.__name__}: {mod}")
    record("v08.arch 策略层不依赖 DSP / 视频内部实现",
           not leaks, f"{leaks}")
    record("v08.arch 策略层不拼 argv / 不起进程 (纯函数)",
           all("subprocess" not in open(m.__file__, encoding="utf-8").read()
               for m in (_af, _aos)),
           "no subprocess in policy modules")
    record("v08.arch 策略层公开 API 不含视频编码参数",
           not ({"crf", "preset", "pix_fmt", "fps", "video_codec"}
                & _public_args(_af.__file__)
                | {"crf", "preset", "pix_fmt", "fps", "video_codec"}
                & _public_args(_aos.__file__)),
           "argument audit")
    record("v08.arch 1kt.py 仍不 import 任何 core.audio_* (含函数内)",
           not [
               m for m in _imports(ROOT / "1kt.py")
               if m.startswith("core.audio_")
           ],
           f"{_imports(ROOT / '1kt.py')}")
    record("v08.arch 1kt.py 仍只通过 production.output 触达音频",
           "from production.output import" in
           open(ROOT / "1kt.py", encoding="utf-8").read())


def _imports(path: Path) -> list[str]:
    import ast

    out: list[str] = []
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _public_args(path: str) -> set[str]:
    """模块内所有函数/方法的公开参数名 (AST, 不是字符串扫描)。"""
    import ast

    args: set[str] = set()
    for node in ast.parse(open(path, encoding="utf-8").read()).body:
        if isinstance(node, ast.FunctionDef):
            args.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef):
                    args.update(
                        a.arg for a in sub.args.args + sub.args.kwonlyargs
                    )
    return args


# ---------------------------------------------------------------------------
# L3 — 真实 ffmpeg / ffprobe
# ---------------------------------------------------------------------------

def l3_audio_format() -> None:
    """v0.8.0: 真实素材上的格式感知 alignment 与编码继承。"""
    section("L3 格式感知 / alignment / 编码 (v0.8.0)")
    import shutil

    from core.audio_external import append_external_sources
    from core.audio_plan import AudioPlanner
    from core.audio_probe import audio_probe_external, audio_probe_from_file
    from core.audio_request import parse_audio_request
    from core.audio_format import (
        REASON_ALIGNMENT_REJECTED_NON_PCM,
        format_bitrate,
    )
    from core.output_compose import VideoOutputArtifact
    from production.output import produce_audio_output, resolve_source_audio_plan

    d = IN_DIR / "p5_format"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    # --- 素材: 视频 + 4xmono PCM (基准), 视频专用产物 --------------------
    src_dir = d / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    pcm_src = src_dir / "clip.MP4"
    if not _make_av(pcm_src, 4, seconds=1):
        record("p5.fmt 真实素材 (h264 + 4×mono PCM) 生成", False, "ffmpeg 失败")
        return
    probe = audio_probe_from_file(FFPROBE, pcm_src, source_id="source")
    channels = [c.id for c in probe.plan().all_channels()]
    record("p5.fmt 素材 = 1 video + 4 audio (PCM)",
           len(_audio_of(pcm_src)) == 4
           and all(a["codec_name"] == "pcm_s16le"
                   for a in _audio_of(pcm_src)),
           f"{[a['codec_name'] for a in _audio_of(pcm_src)]}")

    video_only = d / "video_only.mp4"
    record("p5.fmt 视频专用产物 (Composer 的输入 0)",
           _make_video_only(video_only, seconds=1))
    baseline_hash = _video_elementary_hash(video_only)
    record("p5.fmt 视频基准 hash 可得", bool(baseline_hash),
           baseline_hash[:16])

    # --- T1: PCM + auto -> alignment 允许, 但无 reference 不产生 offset --
    plan = resolve_source_audio_plan(
        FFPROBE, pcm_src,
        parse_audio_request({"channels": {"select": channels[:2]}}),
        source_id="source",
    )
    out = d / "t1_pcm.mp4"
    oc = produce_audio_output(
        plan=plan, video=VideoOutputArtifact(path=str(video_only)),
        output_path=out, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "t1_work", audio_source=pcm_src,
        request=parse_audio_request({"channels": {"select": channels[:2]}}),
    )
    record("p5.fmt.T1 PCM + auto: alignment 默认 ENABLED 且不猜 reference",
           oc.ok and oc.applied
           and oc.alignment.enabled
           and oc.alignment.reason == "alignment_default_pcm"
           and oc.alignment_applied == 0
           and any("never guessed" in n for n in oc.notes),
           f"{oc.alignment.reason} applied={oc.alignment_applied}")
    record("p5.fmt.T1 无 reference 时不会为了 alignment 重新编码 (仍整流保留)",
           oc.path.value == "stream_copy" and not oc.encoded
           and len(_audio_of(out)) == 2,
           f"path={oc.path.value} encoded={len(oc.encoded)} "
           f"tracks={len(_audio_of(out))}")
    record("p5.fmt.T1 PCM -> PCM 默认输出 (§6)",
           oc.format.spec.format.value == "pcm"
           and oc.format.codec_origin == "source"
           and all(a["codec_name"] == "pcm_s16le" for a in _audio_of(out)),
           oc.format.summary())
    record("p5.fmt.T1 视频基本流 sha256 未变",
           _video_elementary_hash(out) == baseline_hash,
           f"{_video_elementary_hash(out)[:16]}")

    # --- T2: compressed 素材 -------------------------------------------
    aac_src = d / "aac_src.mp4"
    if not _make_av_compressed(aac_src, "aac", channels=1, seconds=1):
        record("p5.fmt AAC 素材生成", False, "ffmpeg 失败")
        return
    aac_probe = audio_probe_from_file(FFPROBE, aac_src, source_id="source")
    aac_channel = aac_probe.plan().all_channels()[0].id
    aac_stream = aac_probe.streams[0]
    record("p5.fmt compressed 素材探测出 aac + 可判定 bitrate",
           aac_stream.codec_name == "aac" and int(aac_stream.bit_rate or 0) > 0,
           f"{aac_stream.codec_name} {aac_stream.bit_rate}")

    req2 = parse_audio_request({"channels": {"select": [aac_channel]}})
    plan = resolve_source_audio_plan(
        FFPROBE, aac_src, req2, source_id="source"
    )
    out2 = d / "t2_aac_copy.mp4"
    oc2 = produce_audio_output(
        plan=plan, video=VideoOutputArtifact(path=str(video_only)),
        output_path=out2, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "t2_work", audio_source=aac_src, request=req2,
    )
    record("p5.fmt.T2 compressed + auto: 默认不 alignment, 不警告",
           oc2.ok and oc2.applied
           and oc2.alignment.reason == "alignment_default_compressed"
           and not oc2.alignment.enabled
           and oc2.alignment.warnings == [],
           f"{oc2.alignment.reason} {oc2.alignment.warnings}")
    record("p5.fmt.T2 compressed 默认 NO decode / NO re-encode (原流保留)",
           oc2.path.value == "stream_copy" and not oc2.encoded
           and [a["codec_name"] for a in _audio_of(out2)] == ["aac"],
           f"path={oc2.path.value} {[a['codec_name'] for a in _audio_of(out2)]}")
    record("p5.fmt.T2 compressed 默认保持 source codec + source bitrate",
           oc2.format.spec.format.value == "aac"
           and oc2.format.codec_origin == "source"
           and oc2.format.spec.bitrate
           == format_bitrate(aac_stream.bit_rate),
           oc2.format.summary())
    record("p5.fmt.T2 视频基本流 sha256 未变",
           _video_elementary_hash(out2) == baseline_hash,
           f"{_video_elementary_hash(out2)[:16]}")

    # --- T3/T4: 真实延迟素材 (共享伪噪声, 目标晚到 480 样本) --------------
    delay_dir = d / "delay"
    delay_dir.mkdir(parents=True, exist_ok=True)
    ref_wav = delay_dir / "clip_ref.wav"
    tgt_wav = delay_dir / "clip_tgt.tmp.wav"
    tgt_opus = delay_dir / "clip_tgt.opus"
    tgt_aac = delay_dir / "clip_tgt.aac"
    _sync_content(ref_wav, 0, samples=48000)
    _sync_content(tgt_wav, 480, samples=48000)
    ok_opus = _transcode_audio(tgt_opus, tgt_wav, "opus", bitrate="96k")
    ok_aac = _transcode_audio(tgt_aac, tgt_wav, "aac", bitrate="96k")
    try:
        tgt_wav.unlink()
    except OSError:
        pass
    record("p5.fmt.T3 可测素材 (共享内容 + 480 样本延迟 + Opus/AAC 转码) 生成",
           ok_opus and ok_aac and ref_wav.is_file(),
           f"opus={tgt_opus.is_file()} aac={tgt_aac.is_file()}")

    def _external_case(name: str, target: Path) -> tuple[Any, Path, str, str]:
        """ref(PCM WAV) + target(compressed) -> 显式 alignment 走完整链路。"""
        plan = audio_probe_from_file(
            FFPROBE, pcm_src, source_id="source"
        ).plan()
        append_external_sources(plan, [
            audio_probe_external(
                FFPROBE, ref_wav, source_id=ref_wav.name).to_source(),
            audio_probe_external(
                FFPROBE, target, source_id=target.name).to_source(),
        ])
        ref_id = f"{ref_wav.name}:s0:c0"
        tgt_id = f"{target.name}:s0:c0"
        request = parse_audio_request({
            "channels": {"select": [ref_id, tgt_id]},
            "alignment": "enabled",
            "sync": {"reference": ref_id},
        })
        # 选择必须**先应用到计划** (request 本身只携带策略; 应用是既有
        # build_audio_plan 的职责) —— 否则计划里会留着视频自己的 4 条音轨。
        from core.audio_request import build_audio_plan
        plan = build_audio_plan(request, plan)
        dest = d / f"t3_{name}.mp4"
        outcome = produce_audio_output(
            plan=plan, video=VideoOutputArtifact(path=str(video_only)),
            output_path=dest, ffmpeg=FFMPEG, ffprobe=FFPROBE,
            work_dir=d / f"t3_{name}_work", audio_source=pcm_src,
            request=request,
        )
        return outcome, dest, ref_id, tgt_id

    oc3, out3, ref_id, tgt_aac_id = _external_case("aac", tgt_aac)
    record("p5.fmt.T3 参与的 AAC + enabled -> 显式拒绝 (A3: 不再 warning+重编码)",
           (not oc3.ok) and not oc3.applied
           and oc3.reasons == [REASON_ALIGNMENT_REJECTED_NON_PCM]
           and oc3.alignment is not None
           and oc3.alignment.refused and not oc3.alignment.enabled
           and not oc3.alignment.requires_decode
           and oc3.alignment.warnings == [],
           f"{oc3.summary()}")
    record("p5.fmt.T3 拒绝发生在解码前: 无 offset / 无编码 / 无产物",
           oc3.alignment_applied == 0 and oc3.alignment_offsets == {}
           and not oc3.encoded and not oc3.groups
           and oc3.composition is None and oc3.execution is None
           and not out3.is_file(),
           f"applied={oc3.alignment_applied} encoded={len(oc3.encoded)} "
           f"groups={len(oc3.groups)} exists={out3.is_file()}")
    record("p5.fmt.T3 错误条目点名非 PCM 流 (AAC) + 稳定错误码",
           any(str(e.get("reason")) == REASON_ALIGNMENT_REJECTED_NON_PCM
               and f"{tgt_aac.name}:s0" in str(e.get("detail"))
               and "aac" in str(e.get("detail"))
               for e in oc3.errors),
           f"{oc3.errors}")

    # --- T4: Opus 目标 (同一条准入规则, 另一种 compressed codec) ----------
    oc4, out4, ref4_id, tgt_opus_id = _external_case("opus", tgt_opus)
    record("p5.fmt.T4 参与的 Opus + enabled -> 显式拒绝 (与 AAC 同一规则)",
           (not oc4.ok) and not oc4.applied
           and oc4.reasons == [REASON_ALIGNMENT_REJECTED_NON_PCM]
           and oc4.alignment is not None and oc4.alignment.refused
           and oc4.alignment.non_pcm_streams
           and oc4.alignment.non_pcm_streams[0]["codec_name"] == "opus"
           and not out4.is_file(),
           f"{oc4.summary()} {oc4.errors} {oc4.alignment.non_pcm_streams}")

    # --- T4b: participating-only —— 未参与输出的压缩流不否决 enabled ------
    # 计划里保留一条**未被选中**的 AAC 外挂来源; 参与输出的只有 pcm_src 的
    # 2 条 PCM 声道 -> enabled 必须放行 (与 T3/T4 同一份代码, 区别只在参与集)。
    po_plan = audio_probe_from_file(FFPROBE, pcm_src, source_id="source").plan()
    append_external_sources(po_plan, [
        audio_probe_external(
            FFPROBE, tgt_aac, source_id=tgt_aac.name).to_source(),
    ])
    po_request = parse_audio_request({
        "channels": {"select": channels[:2]},
        "alignment": "enabled",
        "sync": {"reference": channels[0]},
    })
    from core.audio_request import build_audio_plan
    po_plan = build_audio_plan(po_request, po_plan)
    po_out = d / "t4b_participating_only.mp4"
    po = produce_audio_output(
        plan=po_plan, video=VideoOutputArtifact(path=str(video_only)),
        output_path=po_out, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "t4b_work", audio_source=pcm_src, request=po_request,
    )
    record("p5.fmt.T4b 未参与的压缩流不否决 enabled (participating-only §4.9)",
           po.ok and po.applied
           and po.alignment is not None
           and po.alignment.enabled and not po.alignment.refused
           and po.alignment.reason == "alignment_enabled_pcm"
           and REASON_ALIGNMENT_REJECTED_NON_PCM not in po.reasons,
           f"{po.summary()} {po.errors}")
    po_audio = _audio_of(po_out)
    record("p5.fmt.T4b 未参与压缩流仍在计划里但未进产物 (证明是 participating 判定)",
           any(s.source_id == tgt_aac.name for s in po_plan.sources)
           and bool(po_audio)
           and all(a["codec_name"].startswith("pcm_") for a in po_audio),
           f"plan_sources={[s.source_id for s in po_plan.sources]} "
           f"out={[a['codec_name'] for a in po_audio]}")
    record("p5.fmt.T4b 视频基本流 sha256 未变 (参与对齐不动视频)",
           _video_elementary_hash(po_out) == baseline_hash,
           f"{_video_elementary_hash(po_out)[:16]}")

    # --- T5: 手动 codec + bitrate (外挂立体声 -> 独立流 -> 必须重编码) ----
    t5_dir = d / "t5"
    t5_dir.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    t5_video = t5_dir / "clip.MP4"
    _shutil.copy2(video_only, t5_video)
    _sync_content(t5_dir / "clip.wav", 0, samples=48000, channels=2)

    def _opus_case(bitrate: str, tag: str) -> tuple[Any, Path]:
        request = parse_audio_request({
            "external": {},
            "mapping": {"mode": "independent"},
            "encode": {"format": "opus", "bitrate": bitrate},
        })
        plan = resolve_source_audio_plan(
            FFPROBE, t5_video, request, source_id="source"
        )
        dest = d / f"t5_{tag}.mp4"
        return produce_audio_output(
            plan=plan, video=VideoOutputArtifact(path=str(video_only)),
            output_path=dest, ffmpeg=FFMPEG, ffprobe=FFPROBE,
            work_dir=d / f"t5_{tag}_work", audio_source=t5_video,
            request=request,
        ), dest

    oc5, out5 = _opus_case("64k", "64k")
    oc5b, out5b = _opus_case("256k", "256k")
    a5 = _audio_of(out5)
    record("p5.fmt.T5 外挂 PCM -> 独立流 -> Opus 64k (manual override) 成功",
           oc5.ok and oc5.applied and len(a5) == 2
           and all(a["codec_name"] == "opus" for a in a5),
           f"{oc5.summary()} {[a['codec_name'] for a in a5]}")
    record("p5.fmt.T5 手动 bitrate 记录为 manual 且逐组生效",
           oc5.formats and oc5.formats[0].bitrate_origin == "manual"
           and oc5.formats[0].spec.bitrate == "64k"
           and oc5b.formats[0].spec.bitrate == "256k",
           f"{[f.summary() for f in oc5.formats]}")
    size64 = out5.stat().st_size if out5.is_file() else 0
    size256 = out5b.stat().st_size if out5b.is_file() else 0
    record("p5.fmt.T5 手动 bitrate 真的到达编码器 (256k 产物 > 64k 产物)",
           size64 > 0 and size256 > size64,
           f"64k={size64}B 256k={size256}B")
    record("p5.fmt.T5 视频基本流 sha256 未变",
           _video_elementary_hash(out5) == baseline_hash,
           f"{_video_elementary_hash(out5)[:16]}")

    # --- T6: PCM + bitrate -> 明确拒绝, 无半成品 ------------------------
    plan = resolve_source_audio_plan(
        FFPROBE, pcm_src,
        parse_audio_request({"channels": {"select": [channels[0]]}}),
        source_id="source",
    )
    req6 = parse_audio_request({
        "channels": {"select": [channels[0]]},
        "encode": {"format": "pcm", "bitrate": "128k"},
    })
    out6 = d / "t6_reject.mp4"
    oc6 = produce_audio_output(
        plan=plan, video=VideoOutputArtifact(path=str(video_only)),
        output_path=out6, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "t6_work", audio_source=pcm_src, request=req6,
    )
    record("p5.fmt.T6 PCM + bitrate -> 生产编排明确失败且不产出半成品",
           (not oc6.ok)
           and "audio_format_bitrate_not_applicable" in oc6.reasons
           and not out6.is_file(),
           f"{oc6.reasons} exists={out6.is_file()}")

    # 只写 bitrate (不写 codec) 也必须拒绝, 不能因为"最终没编码"就静默忽略
    plan = resolve_source_audio_plan(
        FFPROBE, pcm_src,
        parse_audio_request({"channels": {"select": [channels[0]]}}),
        source_id="source",
    )
    req6b = parse_audio_request({
        "channels": {"select": [channels[0]]},
        "encode": {"bitrate": "128k"},
    })
    out6b = d / "t6b_reject.mp4"
    oc6b = produce_audio_output(
        plan=plan, video=VideoOutputArtifact(path=str(video_only)),
        output_path=out6b, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "t6b_work", audio_source=pcm_src, request=req6b,
    )
    record("p5.fmt.T6 只给 bitrate (codec 继承为 PCM) -> 同样明确拒绝",
           (not oc6b.ok)
           and "audio_format_bitrate_not_applicable" in oc6b.reasons
           and not out6b.is_file(),
           f"{oc6b.reasons} exists={out6b.is_file()}")

    # --- T7: 真实生产入口 (1kt.py) 上的格式继承 -------------------------
    cli_dir = d / "cli"
    cli_src = cli_dir / "src"
    cli_src.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(aac_src, cli_src / "aac.mp4")
    plans = d / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan_file = plans / "inherit.json"
    plan_file.write_text(json.dumps({
        "version": 1,
        "channels": {"select": [aac_channel]},
        "note": "v0.8.0 codec inheritance through the real CLI",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_dir = d / "cli_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    r = _run_1kt(cli_src, "--output", str(out_dir), "--encoder", "x265", "--audio-plan", str(plan_file))
    produced = sorted(out_dir.rglob("*.MP4"))
    text = (r.stdout or "") + (r.stderr or "")
    ok_cli = bool(produced) and "[AUDIO-FAIL]" not in text
    cli_audio = _audio_of(produced[0]) if ok_cli else []
    record("p5.fmt.T7 真实 1kt.py: 无 encode 块时按输入格式继承 (AAC 保持)",
           ok_cli and len(cli_audio) == 1
           and cli_audio[0]["codec_name"] == "aac",
           f"rc={r.returncode} {[a['codec_name'] for a in cli_audio]}")

    # --- T8: 真实 1kt.py 上 enabled 的非 PCM 显式拒绝 + 非零退出 ----------
    # 这是 §4.9 的完整链路证据: production.output 的 ok=False ->
    # 1kt.py `_apply_audio_plan` 返回 False -> 进程非零退出且不产出半成品。
    rej_src = cli_dir / "rej_src"
    rej_src.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(aac_src, rej_src / "aac.mp4")
    reject_plan = plans / "enabled_reject.json"
    reject_plan.write_text(json.dumps({
        "version": 1,
        "channels": {"select": [aac_channel]},
        "alignment": "enabled",
        "sync": {"reference": aac_channel},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rej_out = d / "cli_reject_out"
    rej_out.mkdir(parents=True, exist_ok=True)
    r_rej = _run_1kt(rej_src, "--output", str(rej_out), "--encoder", "x265",
                     "--audio-plan", str(reject_plan))
    rej_text = (r_rej.stdout or "") + (r_rej.stderr or "")
    rej_produced = sorted(rej_out.rglob("*.MP4"))
    record("p5.fmt.T8 真实 1kt.py: enabled + 参与 AAC -> 非零退出 + 稳定错误码",
           r_rej.returncode != 0
           and REASON_ALIGNMENT_REJECTED_NON_PCM in rej_text
           and "[AUDIO-FAIL]" in rej_text
           and "AUDIO PLAN REJECTED" in rej_text,
           f"rc={r_rej.returncode}")
    record("p5.fmt.T8 显式拒绝不产出半成品 (CLI 输出目录为空)",
           not rej_produced, f"{[p.name for p in rej_produced]}")
