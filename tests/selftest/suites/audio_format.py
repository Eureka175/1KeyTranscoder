#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.8.0: 格式感知 (PCM / compressed) + alignment 策略 + 输出编码继承。

两个 suite:

  * `l1_audio_format()` —— 纯策略层: 格式分类表、alignment 决策表 (含
    compressed warning)、输出 codec/bitrate 的优先级链 (manual > source >
    encoder default)、PCM + bitrate 的明确拒绝、以及 v0.8.0 的架构审计。
    不运行 ffmpeg。
  * `l3_audio_format()` —— **真实 ffmpeg/ffprobe**: PCM 默认允许 alignment、
    compressed 默认原样保留 (不解码不重编码)、compressed 显式 alignment
    走 decode -> PCM -> align -> re-encode 并给出 warning、AAC/Opus 手动
    bitrate、以及"视频基本流 sha256 在每种音频处理下都不变"。
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
    FFPROBE, FFMPEG, IN_DIR, ROOT, ffprobe_json, record, section, sh,
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
        REASON_FORMAT_BITRATE_NOT_APPLICABLE,
        WARNING_ALIGNMENT_COMPRESSED,
        AudioAlignmentPolicy,
        AudioInputFormat,
        ExplicitFormat,
        classify_codec,
        format_bitrate,
        is_pcm_codec,
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

    # --- 2. alignment 决策表 (§4/§5) ------------------------------------
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

    d = resolve_alignment(opus_plan, AudioAlignmentPolicy.ENABLED)
    record("v08.align Opus + enabled -> ENABLED 且必须重编码",
           d.enabled and d.requires_decode
           and d.reason == "alignment_enabled_compressed",
           f"{d.reason}")
    record("v08.align compressed 显式 alignment 给出 §5 的 warning 原文",
           WARNING_ALIGNMENT_COMPRESSED in d.warnings
           and "Audio alignment requested for compressed input." in
           WARNING_ALIGNMENT_COMPRESSED
           and "must be decoded to PCM and re-encoded" in
           WARNING_ALIGNMENT_COMPRESSED,
           f"{d.warnings}")
    record("v08.align 不走'伪造时间戳平移'的假对齐 (note 明确写出真实链路)",
           any("decode -> PCM -> align -> re-encode" in n for n in d.notes),
           f"{d.notes}")

    d = resolve_alignment(aac_plan, AudioAlignmentPolicy.ENABLED)
    record("v08.align AAC + enabled 与 Opus 走同一条规则",
           d.enabled and d.requires_decode and d.warnings)

    d = resolve_alignment(mixed_plan, AudioAlignmentPolicy.AUTO)
    record("v08.align PCM + compressed 混合 + auto -> DISABLED (compressed 否决)",
           not d.enabled and d.reason == "alignment_default_mixed"
           and d.compressed_sources == ["rec"],
           f"{d.reason} {d.formats}")
    d = resolve_alignment(mixed_plan, AudioAlignmentPolicy.ENABLED)
    record("v08.align 混合 + enabled 仍然 warning (§41)",
           d.enabled and d.requires_decode
           and WARNING_ALIGNMENT_COMPRESSED in d.warnings,
           f"{d.warnings}")

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
    from core.audio_format import format_bitrate
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
    record("p5.fmt.T3 AAC + 显式 alignment -> 输出 §5 的 warning (§41)",
           oc3.ok and oc3.applied
           and oc3.alignment.reason == "alignment_enabled_compressed"
           and any("Audio alignment requested for compressed input" in w
                   for w in oc3.alignment.warnings),
           f"{oc3.alignment.reason} {oc3.alignment.warnings}")
    record("p5.fmt.T3 已应用的 offset -> 每条输出流都走 render (共用 window)",
           oc3.path.value == "pcm_route"
           and [g["strategy"] for g in oc3.groups] == ["encode", "encode"]
           and any("shared window" in w for w in oc3.warnings),
           f"{oc3.path.value} {[g['strategy'] for g in oc3.groups]}")
    record("p5.fmt.T3 输出 codec = 各自 source codec (AAC 目标保持 AAC, §7)",
           len(oc3.formats) == 2
           and oc3.formats[1].codec == "aac"
           and oc3.formats[1].codec_origin == "source"
           and [a["codec_name"] for a in _audio_of(out3)][0].startswith("pcm_")
           and [a["codec_name"] for a in _audio_of(out3)][1] == "aac",
           f"{[f.summary() for f in oc3.formats]} "
           f"{[a['codec_name'] for a in _audio_of(out3)]}")
    record("p5.fmt.T3 视频基本流 sha256 未变 (音频重编码不动视频)",
           _video_elementary_hash(out3) == baseline_hash,
           f"{_video_elementary_hash(out3)[:16]}")
    record("p5.fmt.T3 mapping 未因 decode/re-encode 改变 (§14)",
           [g["channels"] for g in oc3.groups] == [[ref_id], [tgt_aac_id]],
           f"{[g['channels'] for g in oc3.groups]}")
    offset_aac = float(oc3.alignment_offsets.get(tgt_aac_id, 0.0))
    record("p5.fmt.T3 AAC 目标: offset 在**解码样本域**测得 (≥ 注入的 480)",
           oc3.alignment_applied == 1 and offset_aac >= 479.0,
           f"offset={offset_aac} (注入 480; AAC 解码还有编码器 priming)")
    left = _decode_mono(out3, 0, tag="p5f")
    right = _decode_mono(out3, 1, tag="p5f")
    lag = _best_lag(left, right)
    record("p5.fmt.T3 对齐后两路样本互相关滞后 = 0 (±2) —— 真的对齐了样本",
           lag is not None and abs(lag) <= 2, f"lag={lag}")

    # --- T4: Opus 目标 (同一条链路, 另一种 compressed codec) -------------
    oc4, out4, ref4_id, tgt_opus_id = _external_case("opus", tgt_opus)
    record("p5.fmt.T4 PCM + compressed 混合参与 alignment -> warning (§41)",
           oc4.ok and oc4.applied and oc4.alignment.enabled
           and any("Audio alignment requested for compressed input" in w
                   for w in oc4.alignment.warnings),
           f"{oc4.alignment.warnings}")
    record("p5.fmt.T4 Opus 目标 offset = 注入的 480 样本 (误差 ≤ 1)",
           oc4.alignment_applied == 1 and tgt_opus_id in oc4.alignment_offsets
           and abs(float(oc4.alignment_offsets[tgt_opus_id]) - 480.0) <= 1.0,
           f"{oc4.alignment_offsets}")
    record("p5.fmt.T4 同一个注入延迟在 PCM 域与压缩域测得不同 -> 必须解码对齐",
           abs(offset_aac - float(oc4.alignment_offsets[tgt_opus_id])) > 100.0,
           f"aac={offset_aac} opus="
           f"{oc4.alignment_offsets.get(tgt_opus_id)} (注入都是 480)")
    record("p5.fmt.T4 Opus 目标重编码后仍是 Opus (逐来源继承 codec, §35)",
           len(oc4.formats) == 2 and oc4.formats[1].codec == "opus"
           and [a["codec_name"] for a in _audio_of(out4)]
           == ["pcm_s16le", "opus"],
           f"{[f.summary() for f in oc4.formats]} "
           f"{[a['codec_name'] for a in _audio_of(out4)]}")
    record("p5.fmt.T4 视频基本流 sha256 未变",
           _video_elementary_hash(out4) == baseline_hash,
           f"{_video_elementary_hash(out4)[:16]}")
    left = _decode_mono(out4, 0, tag="p5o")
    right = _decode_mono(out4, 1, tag="p5o")
    lag = _best_lag(left, right)
    record("p5.fmt.T4 对齐后两路样本互相关滞后 = 0 (±2)",
           lag is not None and abs(lag) <= 2, f"lag={lag}")

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
    r = _run_1kt(cli_src, "--output", str(out_dir), "--encoder", "x265",
                 "--no-hw-autoselect", "--audio-plan", str(plan_file))
    produced = sorted(out_dir.rglob("*.MP4"))
    text = (r.stdout or "") + (r.stderr or "")
    ok_cli = bool(produced) and "[AUDIO-FAIL]" not in text
    cli_audio = _audio_of(produced[0]) if ok_cli else []
    record("p5.fmt.T7 真实 1kt.py: 无 encode 块时按输入格式继承 (AAC 保持)",
           ok_cli and len(cli_audio) == 1
           and cli_audio[0]["codec_name"] == "aac",
           f"rc={r.returncode} {[a['codec_name'] for a in cli_audio]}")
