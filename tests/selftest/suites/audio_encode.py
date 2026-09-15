#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4B: 音频编码 + 最终输出编排。

两个 suite:

  * `l1_audio_encode()` —— 纯声明/契约层: 格式表、采样率与声道数校验、
    `EncodedAudioOutput` 事实、Composer 的 `-map` 编排与顺序权威;
    不运行 ffmpeg。
  * `l3_audio_encode()` —— 真实 ffmpeg + ffprobe 端到端:
    PCM -> encode -> decode 回归 (逐样本), routing / mixing 经编码后身份与
    顺序保持, 以及 video + audio 最终编排 (视频 bitstream 必须不变)。

关键回归: **视频没有被音频路径影响** —— 用 video packet hash 对比, 而不是
只比分辨率。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..fixtures.audio import _make_av, _make_av_channels
from ..paths import FFMPEG, IN_DIR, ffprobe_json, record, section, sh


# ---------------------------------------------------------------------------
# fixtures (自建, 短素材; 不做长程 benchmark)
# ---------------------------------------------------------------------------


def _decode_mono(path: Path, position: int, frames: int = 0) -> Any:
    """解码第 `position` 条音频为 float32 mono。"""
    import numpy as np

    out = path.with_suffix(path.suffix + f".dec{position}.raw")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = sh(FFMPEG, "-v", "error", "-y", "-i", path,
           "-map", f"0:a:{position}", "-vn", "-ac", "1",
           "-f", "f32le", "-ar", "48000", out, timeout=600)
    if r.returncode != 0 or not out.is_file():
        return None
    data = np.fromfile(out, dtype="<f4")
    return data[:frames] if frames else data


def _video_hash(path: Path) -> str:
    """视频**基本流**的 sha256 —— 跨容器重封装不变。

    用 `-f hash` 对 `-c copy` 出来的视频包做哈希: 只比分辨率是发现不了
    "音频路径顺手重编码了视频"这类回归的。
    """
    r = sh(FFMPEG, "-v", "error", "-i", path, "-map", "0:v:0",
           "-c", "copy", "-f", "hash", "-hash", "sha256", "-", timeout=600)
    if r.returncode != 0:
        return ""
    text = (r.stdout or "").strip()
    return text.split("=")[-1] if "=" in text else text


def _streams_of(path: Path) -> list[dict[str, Any]]:
    return ffprobe_json(path).get("streams", [])


def _audio_of(path: Path) -> list[dict[str, Any]]:
    return [s for s in _streams_of(path) if s.get("codec_type") == "audio"]


def _video_of(path: Path) -> dict[str, Any] | None:
    return next(
        (s for s in _streams_of(path) if s.get("codec_type") == "video"), None
    )


def _make_video_only(src: Path, dst: Path) -> bool:
    """从一个带音频的文件里抽出**纯视频**产物 (模拟 Video Domain 的产物)。"""
    r = sh(FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:v:0",
           "-c", "copy", "-an", dst, timeout=600)
    return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0


# ---------------------------------------------------------------------------
# L1 — 声明/契约层
# ---------------------------------------------------------------------------

def l1_audio_encode() -> None:
    """Phase 4B: 音频格式契约 + Composer 编排 (不运行 ffmpeg)。"""
    section("L1 音频编码 / 输出编排契约 (Phase 4B)")
    from core.audio_encode import (
        REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED,
        REASON_AUDIO_ENCODE_NO_OUTPUT,
        REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH,
        AudioEncodeFormat,
        AudioFormatSpec,
        resolve_audio_format,
    )
    from core.audio_timeline import AudioTimeline
    from core.output_compose import (
        REASON_COMPOSE_AUDIO_MISSING,
        REASON_COMPOSE_NO_VIDEO,
        OutputComposer,
        VideoOutputArtifact,
        probe_container,
    )

    # --- 1. 格式表: 显式、极少数、不猜 --------------------------------
    record("p4b.format 只有 3 个显式格式 (不是 codec framework)",
           set(AudioEncodeFormat) == {
               AudioEncodeFormat.AAC, AudioEncodeFormat.PCM,
               AudioEncodeFormat.FLAC,
           })
    record("p4b.format 映射明确 (encoder/container/suffix/lossless)",
           AudioEncodeFormat.AAC.encoder == "aac"
           and AudioEncodeFormat.AAC.container == "adts"
           and AudioEncodeFormat.AAC.suffix == ".aac"
           and AudioEncodeFormat.AAC.lossless is False
           and AudioEncodeFormat.PCM.encoder == "pcm_s16le"
           and AudioEncodeFormat.PCM.lossless is True
           and AudioEncodeFormat.FLAC.encoder == "flac"
           and AudioEncodeFormat.FLAC.lossless is True,
           f"{ {f.value: (f.encoder, f.container, f.suffix, f.lossless) for f in AudioEncodeFormat} }")
    record("p4b.format 宽松解析 + 未知值回退默认 (不抛)",
           AudioEncodeFormat.coerce("AAC") is AudioEncodeFormat.AAC
           and AudioEncodeFormat.coerce(" flac ") is AudioEncodeFormat.FLAC
           and AudioEncodeFormat.coerce("mp4a") is AudioEncodeFormat.AAC
           and AudioEncodeFormat.coerce("nope",
                                        AudioEncodeFormat.AAC)
           is AudioEncodeFormat.AAC
           and AudioEncodeFormat.coerce(None) is None)
    record("p4b.format resolve_audio_format 默认 AAC",
           resolve_audio_format(None).format is AudioEncodeFormat.AAC
           and resolve_audio_format("").format is AudioEncodeFormat.AAC
           and resolve_audio_format("flac").format is AudioEncodeFormat.FLAC)

    # --- 2. 参数只允许最小必要集 ---------------------------------------
    spec = AudioFormatSpec.from_dict({
        "format": "aac", "sample_rate": 48000, "channel_count": 2,
        "bitrate": "192k",
    })
    record("p4b.format 参数字段 = 最小必要集 (无 preset/quality/loudness)",
           {"format", "sample_rate", "channel_count", "bitrate"} <= set(
               spec.to_dict())
           and not any(
               hasattr(spec, name) for name in (
                   "preset", "quality", "loudness", "agc", "limiter",
                   "compressor", "lufs", "gain",
               )
           ),
           spec.to_dict())

    # --- 3. 采样率: 不偷偷 resample -------------------------------------
    tl = AudioTimeline(
        sample_rate=48000, start_sample=0, end_sample=48000,
        output_channel_ids=["camera:s1:c0", "camera:s2:c0"],
    )
    record("p4b.encode timeline 事实 = 48000Hz / 2ch / 48000 frames",
           tl.frame_count == 48000 and tl.output_channels == 2
           and tl.sample_rate == 48000)

    ok_spec = AudioFormatSpec(format=AudioEncodeFormat.AAC)
    issues_ok = ok_spec.validate(tl)
    record("p4b.encode 格式沿用 timeline 时校验通过",
           issues_ok == [], f"{issues_ok}")

    bad_rate = AudioFormatSpec(
        format=AudioEncodeFormat.AAC, sample_rate=44100
    ).validate(tl)
    record("p4b.encode 采样率不一致 -> 明确拒绝 (绝不偷偷 resample)",
           len(bad_rate) == 1
           and bad_rate[0]["reason"]
           == REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH,
           f"{bad_rate}")
    record("p4b.encode 拒绝理由里点明未实现 resampling",
           "resampling is not implemented" in bad_rate[0]["detail"])

    bad_ch = AudioFormatSpec(
        format=AudioEncodeFormat.AAC, channel_count=1
    ).validate(tl)
    record("p4b.encode 声道数必须来自 AudioTimeline",
           len(bad_ch) == 1
           and bad_ch[0]["reason"] == REASON_AUDIO_ENCODE_FORMAT_UNSUPPORTED
           and "must come from the AudioTimeline" in bad_ch[0]["detail"],
           f"{bad_ch}")

    empty_tl = AudioTimeline(sample_rate=48000, start_sample=0, end_sample=0)
    empty_issues = AudioFormatSpec().validate(empty_tl)
    record("p4b.encode timeline 无输出声道 -> 拒绝",
           len(empty_issues) == 1
           and empty_issues[0]["reason"] == REASON_AUDIO_ENCODE_NO_OUTPUT,
           f"{empty_issues}")

    # --- 4. encode_audio 的失败路径不产出假结果 --------------------------
    import tempfile

    from core.audio_encode import encode_audio

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        res = encode_audio(
            d / "missing.wav", d / "out.aac", ffmpeg=FFMPEG, timeline=tl,
        )
        record("p4b.encode 缺少渲染 PCM -> 失败且无 output 对象",
               res.ok is False and res.output is None
               and [e["reason"] for e in res.errors]
               == [REASON_AUDIO_ENCODE_NO_OUTPUT],
               res.summary())

        # 采样率不一致必须在**调用 ffmpeg 之前**就被拒绝
        fake = d / "fake.wav"
        fake.write_bytes(b"RIFF0000WAVE")
        res2 = encode_audio(
            fake, d / "out2.aac", ffmpeg=FFMPEG, timeline=tl,
            audio_format=AudioFormatSpec(
                format=AudioEncodeFormat.AAC, sample_rate=44100
            ),
        )
        record("p4b.encode 参数校验先于执行 (未启动任何进程)",
               res2.ok is False and res2.command == []
               and [e["reason"] for e in res2.errors]
               == [REASON_AUDIO_ENCODE_SAMPLE_RATE_MISMATCH],
               res2.summary())

    # --- 5. EncodedAudioOutput 是**实测**契约 ---------------------------
    from core.audio_encode import EncodedAudioOutput

    out = EncodedAudioOutput(
        path="x.aac", codec="aac", sample_rate=48000, channel_count=2,
        expected_frames=48000, probed_frames=49152, duration_seconds=1.024,
        channel_ids=["camera:s1:c0", "camera:s2:c0"], lossless=False,
    )
    record("p4b.contract EncodedAudioOutput 表达全部必要事实",
           out.to_dict()["channel_ids"]
           == ["camera:s1:c0", "camera:s2:c0"]
           and out.to_dict()["expected_frames"] == 48000
           and out.frame_delta == 1152,
           out.summary())
    record("p4b.contract 期望帧数来自 timeline, 实测帧数单独记录",
           out.expected_frames == 48000 and out.probed_frames == 49152)

    # --- 6. Composer 的编排与顺序权威 -----------------------------------
    composer = OutputComposer()
    record("p4b.compose composer 不 import 任何音频算法模块",
           _composer_imports() == set(),
           f"forbidden imports: {_composer_imports()}")
    record("p4b.compose composer 不认识 plan/timeline/mixer/PCM",
           all(
               not hasattr(composer, name) for name in (
                   "plan", "pcm", "mix", "route", "timeline", "sync",
                   "reference", "offset",
               )
           ))

    missing_video = VideoOutputArtifact(path="/nonexistent/v.mp4")
    res3 = composer.compose(
        missing_video, output_path="x.mp4", ffmpeg=FFMPEG,
    )
    record("p4b.compose 视频产物缺失 -> 明确拒绝 (无 VIDEO)",
           res3.ok is False
           and [e["reason"] for e in res3.errors]
           == [REASON_COMPOSE_NO_VIDEO],
           res3.summary())

    res4 = composer.compose(
        VideoOutputArtifact(path=str(Path(__file__)), container="mp4"),
        audio=[EncodedAudioOutput(path="/nonexistent/a.aac", codec="aac")],
        output_path="x.mp4", ffmpeg=FFMPEG,
    )
    record("p4b.compose 音频产物缺失 -> 明确拒绝 (无 AUDIO)",
           res4.ok is False
           and [e["reason"] for e in res4.errors]
           == [REASON_COMPOSE_AUDIO_MISSING],
           res4.summary())

    # --- 7. 契约形状: 自描述 vs 裸流 ------------------------------------
    self_desc = VideoOutputArtifact(path="v.mp4", container="mp4")
    record("p4b.contract 自描述产物 = 一个 -i 输入 (整体引用)",
           self_desc.is_self_describing
           and self_desc.to_dict()["self_describing"] is True)
    raw = VideoOutputArtifact(path="v.h265", container="", stream="v:0")
    record("p4b.contract 裸流产物的定位字段存在且不被猜",
           raw.is_self_describing is False and raw.stream == "v:0")

    # --- 8. probe_container 在无 ffprobe 时返回空而不是编造 --------------
    record("p4b.compose 无 ffprobe 时 probe_container 返回空 (不编造事实)",
           probe_container("whatever.mp4", None) == {})

    # --- 9. 架构审计: 音频域 / 视频域双向零不合理内部依赖 ---------------
    # 这是本阶段的**硬性约束**, 因此钉进回归而不是靠人工 review:
    #   Audio -> Video : 音频模块不得 import 编码器/保留管线/硬件批处理
    #   Video -> Audio : 视频模块不得 import mixer/PCM/timeline/encoder
    # 唯一允许的依赖是 Composer 对**两个契约**的单向依赖 (Composer 不属于
    # 任何一方, 因此不计入上面两条)。
    import ast as _ast

    audio_modules = [
        "audio_encode.py", "audio_execution.py", "audio_retention.py",
        "audio_route.py", "audio_pcm.py", "audio_mix.py", "audio_process.py",
        "audio_timeline.py", "audio_wav.py", "audio_plan.py",
        "audio_models.py", "audio_probe.py", "audio_sync.py",
    ]
    video_probes = [
        "encoders/x265.py", "encoders/svtav1.py", "encoders/nvencc.py",
        "encoders/qsvencc.py", "encoders/hw.py", "encoders/base.py",
        "preservation/pipeline.py", "preservation/sony.py",
        "preservation/dji.py", "preservation/gpac.py", "core/batch_hw.py",
        "1kt.py",
    ]
    forbidden_from_audio = {
        "encoders", "preservation", "batch_hw", "hwdecode", "integrity",
    }
    # ⚠️ 两点精确性要求 (否则断言会给出误导性的"违规"):
    #
    # 1. 按**精确模块路径**匹配, 不用 tail 匹配: `preservation/audio_sync.py`
    #    是既有的 GPAC 重封装 helper (v0.7.1 起被经典路径生产调用), 与新的
    #    `core.audio_sync` **没有任何关系**; 用 tail 匹配会把它误报成
    #    "视频模块引用了音频域"。
    # 2. 禁止集里只放**本阶段及其前序阶段新增的音频域模块**
    #    (`core.audio_*`)。既有模块不在此列: 本阶段不得顺手重构它们, 因此
    #    也不该由本阶段的断言去要求它们改变。
    forbidden_from_video = {
        "core.audio_mix", "core.audio_pcm", "core.audio_encode",
        "core.audio_execution", "core.audio_retention", "core.audio_route",
        "core.audio_process", "core.audio_timeline", "core.audio_plan",
        "core.audio_models", "core.audio_probe",
    }
    # 已知的**既有**例外 (非新音频域, 本阶段不触碰):
    #   `preservation.audio_sync` 提供 GPAC 音频轨替换, 被 `1kt.py` 与
    #   `core/batch_hw.py` 在 channel-sync 生效时调用。它没有 import 任何
    #   `core.audio_*` 模块, 因此不构成"视频域依赖音频域"。
    KNOWN_PREEXISTING = {"preservation.audio_sync"}

    root = Path(__file__).resolve().parents[3]
    audio_leaks: list[str] = []
    for name in audio_modules:
        path = root / "core" / name
        if not path.is_file():
            continue
        for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8"))):
            mods: list[str] = []
            if isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                parts = set(mod.split("."))
                if parts & forbidden_from_audio:
                    audio_leaks.append(f"{name}: {mod}")

    video_leaks: list[str] = []
    for name in video_probes:
        path = root / name
        if not path.is_file():
            continue
        for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8"))):
            mods: list[str] = []
            if isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                if any(mod == f or mod.startswith(f + ".")
                       for f in forbidden_from_video):
                    video_leaks.append(f"{name}: {mod}")

    record("p4b.arch Audio -> Video 内部依赖 0 条",
           not audio_leaks, f"{audio_leaks[:6]}")
    record("p4b.arch Video -> Audio 内部依赖 0 条 (新音频域)",
           not video_leaks, f"{video_leaks[:6]}")
    record("p4b.arch 既有 preservation.audio_sync 未被新音频域依赖",
           not _imports_matching(
               str(root / "core" / "audio_encode.py"),
               {"preservation"},
           ),
           "core/audio_encode.py does not import preservation")
    record("p4b.arch preservation.audio_sync 未反向依赖 core.audio_*",
           not _imports_matching(
               str(root / "preservation" / "audio_sync.py"),
               {"core.audio_encode", "core.audio_execution",
                "core.audio_retention", "core.audio_process",
                "core.audio_timeline", "core.audio_mix", "core.audio_pcm"},
           ),
           "preservation/audio_sync.py stays independent")

    # Composer 只允许依赖两个契约 (audio_encode 的产物类型) —— 不是音频算法
    composer_imports = _composer_imports()
    record("p4b.arch Composer 只依赖 AudioOutput/VideoOutput 契约",
           composer_imports == set(),
           f"forbidden={composer_imports}")

    # AudioOutput 契约本身不携带任何视频**参数** (按 AST 取公开参数名, 不用
    # 字符串扫描 —— docstring 里提到 "pix_fmt" 属于说明, 不是接口)。
    import core.audio_encode as _enc_mod

    enc_tree = _ast.parse(open(_enc_mod.__file__, encoding="utf-8").read())
    enc_args: set[str] = set()
    for node in enc_tree.body:
        if isinstance(node, _ast.FunctionDef):
            enc_args.update(
                a.arg for a in node.args.args + node.args.kwonlyargs
            )
        elif isinstance(node, _ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, _ast.FunctionDef):
                    enc_args.update(
                        a.arg for a in sub.args.args + sub.args.kwonlyargs
                    )
    video_arg_names = {
        "profile", "crf", "preset", "pix_fmt", "video_streams", "fps",
        "resolution", "codec", "scaling",
    }
    record("p4b.arch 音频编码模块的 API 无任何视频参数",
           not (enc_args & video_arg_names),
           f"args={sorted(enc_args)}")
    record("p4b.arch 音频编码模块不 import 编码器/保留管线/硬件批处理",
           not _imports_matching(_enc_mod.__file__, {
               "encoders", "preservation", "batch_hw",
           }),
           f"{_imports_matching(_enc_mod.__file__, {'encoders', 'preservation', 'batch_hw'})}")


def _imports_matching(path: str, forbidden: set[str]) -> list[str]:
    """模块里匹配到 `forbidden` 的 import 列表 (精确前缀匹配)。"""
    import ast

    found: list[str] = []
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if any(mod == f or mod.startswith(f + ".") for f in forbidden):
                found.append(mod)
    return found


def _composer_imports() -> set[str]:
    """Composer 里被禁止的 import (必须为空集)。

    这是**架构断言**, 不是风格检查: Composer 只允许认识 AudioOutput /
    VideoOutput 两个契约, 不允许反向依赖音频算法或视频后端。
    """
    import ast

    forbidden = {
        "audio_plan", "audio_timeline", "audio_mix", "audio_route",
        "audio_process", "audio_execution", "audio_models", "audio_pcm",
        "audio_wav", "audio_sync", "audio_retention", "encoders",
        "preservation", "batch_hw",
    }
    import core.output_compose as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            tail = name.split(".")[-1]
            if tail in forbidden or name.split(".")[0] in forbidden:
                found.add(name)
    return found


# ---------------------------------------------------------------------------
# L3 — 真实编码 / 真实编排
# ---------------------------------------------------------------------------

def _plan_of(path: Path, source_id: str, *, input_index: int | None = 0) -> Any:
    """真实素材 -> AudioPlan (真实 ffprobe 事实, 不猜)。"""
    from core.audio_models import build_audio_streams
    from ..fixtures.plans import _p3a_build_plan

    raw = ffprobe_json(path).get("streams", [])
    streams = build_audio_streams(raw, source_id=source_id)
    return _p3a_build_plan([{
        "source_id": source_id,
        "path": str(path),
        "streams": streams,
        "input_index": input_index,
    }])


def l3_audio_encode() -> None:
    """Phase 4B: 真实 PCM -> encode -> decode 回归 + 最终编排 (ffmpeg/ffprobe)。"""
    section("L3 音频编码 / 最终输出编排 (Phase 4B)")
    from core.audio_encode import (
        AudioEncodeFormat,
        AudioFormatSpec,
        encode_audio,
        encode_audio_from_plan,
    )
    from core.audio_execution import (
        AudioExecutionPath,
        resolve_audio_execution_path,
    )
    from core.audio_plan import AudioPlanner
    from core.audio_retention import build_audio_retention
    from core.output_compose import (
        OutputComposer,
        VideoOutputArtifact,
    )
    import numpy as np

    d = IN_DIR / "p4b_encode"
    d.mkdir(parents=True, exist_ok=True)

    src = d / "p4b_src.mov"
    if not _make_av(src, seconds=1, streams=4):
        record("l3.p4b.真实素材 (video + 4×mono PCM) 生成", False,
               "ffmpeg 合成失败")
        return

    plan = _plan_of(src, "camera")
    record("l3.p4b.素材 = 1 video + 4 audio, 容器 index 与音频序号错开",
           len(_video_of(src) or {}) > 0 and len(_audio_of(src)) == 4
           and [s["index"] for s in _audio_of(src)] == [1, 2, 3, 4],
           f"{[(s['index'], s.get('codec_name')) for s in _audio_of(src)]}")

    # 真正的多声道素材: "取单条流的部分声道" 只有多声道流才构造得出来
    # (4×mono 里选 1 条 mono 就是整流, 那是 copy 不是 route)。
    quad_src = d / "p4b_quad.mov"
    quad_plan: Any = None
    if _make_av_channels(quad_src, 4):
        quad_plan = _plan_of(quad_src, "camera")
    else:
        record("l3.p4b.多声道素材 (1×4CH PCM) 生成", False, "ffmpeg 失败")

    # =====================================================================
    # 1. 执行路径仍然是唯一权威 (四态各验一次)
    # =====================================================================
    paths_seen: dict[str, str] = {}
    paths_seen["none"] = resolve_audio_execution_path(None).path.value
    paths_seen["stream_copy"] = resolve_audio_execution_path(plan).path.value
    if quad_plan is not None:
        sub = AudioPlanner(quad_plan)
        sub.select_channels("camera:s1:c2")     # 4CH 里只取 1 个声道
        paths_seen["pcm_route"] = resolve_audio_execution_path(
            sub.plan).path.value
    mix = AudioPlanner(_plan_of(src, "camera"))
    mix.select_channels("camera:s1:c0", "camera:s2:c0")
    mix.plan.mix_mode = "sum"
    paths_seen["pcm_mix"] = resolve_audio_execution_path(mix.plan).path.value
    record("l3.p4b.四条执行路径判定正确 (resolve 仍是唯一权威)",
           paths_seen == {
               "none": "none", "stream_copy": "stream_copy",
               "pcm_route": "pcm_route", "pcm_mix": "pcm_mix",
           },
           f"{paths_seen}")

    # =====================================================================
    # 2. PCM_ROUTE -> encode -> decode: 无损必须逐样本一致
    # =====================================================================
    # route 计划: 4CH 流里取"部分声道" —— 这是 -map 表达不了、必须走 PCM
    # 的真实形态 (reorder 整流仍属 STREAM_COPY)。
    route_planner = AudioPlanner(_plan_of(quad_src, "camera"))
    route_planner.select_channels("camera:s1:c0", "camera:s1:c2")
    routed = route_planner.plan
    ex_route = resolve_audio_execution_path(routed)
    record("l3.p4b.route 计划 (4CH 取 2 个声道) -> PCM_ROUTE",
           ex_route.path is AudioExecutionPath.PCM_ROUTE and ex_route.ok,
           ex_route.summary())

    enc_route = encode_audio_from_plan(
        routed, ffmpeg=FFMPEG, work_dir=d / "w_route",
        output_path=d / "route_pcm.wav",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
        chunk_frames=4096, overwrite=True,
    )
    record("l3.p4b.route PCM 编码成功且 timeline 来自渲染 (不是重新算的)",
           enc_route.ok and enc_route.output is not None
           and enc_route.timeline is not None
           and enc_route.timeline.output_channel_ids
           == [c.id for c in routed.selected_channel_objects()],
           enc_route.summary())

    if enc_route.ok and enc_route.output is not None:
        out = enc_route.output
        record("l3.p4b.route 编码产物事实 = 48000Hz / 2ch / 无损帧数精确",
               out.sample_rate == 48000 and out.channel_count == 2
               and out.lossless
               and out.probed_frames == out.expected_frames
               and out.frame_delta == 0,
               f"{out.summary()}")

        # 逐样本: 解码编码产物 == 直接渲染的 PCM
        rendered = encode_audio_from_plan(
            routed, ffmpeg=FFMPEG, work_dir=d / "w_route2",
            output_path=d / "route_pcm_ref.wav",
            audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
            chunk_frames=4096, overwrite=True,
        )
        from core.audio_wav import read_wav

        got, got_info = read_wav(d / "route_pcm.wav")
        ref, ref_info = read_wav(d / "route_pcm_ref.wav")
        record("l3.p4b.route encode->decode 逐样本一致 (无损回归)",
               rendered.ok and got.shape == ref.shape
               and bool(np.array_equal(got, ref))
               and got_info.frame_count == ref_info.frame_count,
               f"got={got.shape} ref={ref.shape}")

        # 无损格式输出的是**未压缩 PCM**: 用"每个输出声道是否非静音"确认
        # 被选中的源声道确实都编码进去了 (身份未丢)。
        active = [i for i in range(got.shape[1])
                  if float(np.abs(got[:, i]).max()) > 0.01]
        record("l3.p4b.route 被选中的 2 个输出声道都有内容 (身份未丢)",
               active == [0, 1] and got.shape[1] == 2, f"active={active}")

    # =====================================================================
    # 3. PCM_MIX -> encode -> decode: gain / clipping 规则不变
    # =====================================================================
    mix_plan_src = AudioPlanner(_plan_of(src, "camera"))
    mix_plan_src.select_channels("camera:s1:c0", "camera:s2:c0")
    from core.audio_mix import MixBusBuilder

    mix_bus = MixBusBuilder(mix_plan_src.plan).sum_all(
        ["camera:s1:c0", "camera:s2:c0"],
        gains={"camera:s1:c0": 0.5, "camera:s2:c0": 0.25},
    )
    mixed = mix_plan_src.plan
    mixed.mix_buses = [mix_bus]
    ex_mix = resolve_audio_execution_path(mixed)
    record("l3.p4b.mix 计划 -> PCM_MIX (混音仍走 PCM 图)",
           ex_mix.path is AudioExecutionPath.PCM_MIX and ex_mix.ok,
           ex_mix.summary())

    enc_mix = encode_audio_from_plan(
        mixed, ffmpeg=FFMPEG, work_dir=d / "w_mix",
        output_path=d / "mix_pcm.wav",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
        chunk_frames=4096, overwrite=True,
    )
    record("l3.p4b.mix 混音 -> 编码成功且输出身份 = mixN (不是源声道)",
           enc_mix.ok and enc_mix.timeline is not None
           and all(str(c).startswith("mix")
                   for c in enc_mix.timeline.output_channel_ids)
           and enc_mix.output is not None
           and enc_mix.output.channel_count == 1,
           enc_mix.summary())

    if enc_mix.ok:
        from core.audio_wav import read_wav

        got_mix, _ = read_wav(d / "mix_pcm.wav")
        # gain 0.5/0.25 无削顶 -> 峰值应显著小于两路直接相加
        peak = float(np.abs(got_mix).max())
        record("l3.p4b.mix 编码产物保留 mixer 的 gain 规则 (未二次缩放)",
               0.01 < peak <= 1.0,
               f"peak={peak:.4f}")
        enc_mix2 = encode_audio_from_plan(
            mixed, ffmpeg=FFMPEG, work_dir=d / "w_mix2",
            output_path=d / "mix_pcm_ref.wav",
            audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
            chunk_frames=4096, overwrite=True,
        )
        ref_mix, _ = read_wav(d / "mix_pcm_ref.wav")
        record("l3.p4b.mix encode->decode 逐样本一致 (混音结果未被编码器改写)",
               enc_mix2.ok and got_mix.shape == ref_mix.shape
               and bool(np.array_equal(got_mix, ref_mix)),
               f"{got_mix.shape} vs {ref_mix.shape}")

    # =====================================================================
    # 4. AAC 有损: 帧数差异被**记录**而不是被忽略; timeline 仍是权威
    # =====================================================================
    enc_aac = encode_audio_from_plan(
        routed, ffmpeg=FFMPEG, work_dir=d / "w_aac",
        output_path=d / "route_aac.aac",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.AAC),
        chunk_frames=4096, overwrite=True,
    )
    record("l3.p4b.aac PCM_ROUTE -> AAC 编码成功",
           enc_aac.ok and enc_aac.output is not None
           and enc_aac.output.codec == "aac"
           and enc_aac.output.channel_count == 2
           and enc_aac.output.sample_rate == 48000,
           enc_aac.summary())
    if enc_aac.ok and enc_aac.output is not None:
        out_aac = enc_aac.output
        record("l3.p4b.aac 期望帧数来自 timeline, 实测帧数单独记录",
               out_aac.expected_frames == enc_aac.timeline.frame_count
               and out_aac.probed_frames > 0,
               f"expected={out_aac.expected_frames} "
               f"probed={out_aac.probed_frames} delta={out_aac.frame_delta}")
        # AAC 时长必须落在 timeline 时长附近 (允许编码延迟)
        expected_sec = enc_aac.timeline.duration_seconds
        record("l3.p4b.aac 编码时长与 timeline 一致 (允许帧对齐)",
               abs(out_aac.duration_seconds - expected_sec) < 0.05,
               f"{out_aac.duration_seconds:.4f} vs {expected_sec:.4f}")

        # 真正 decode, 不只看 rc
        decoded = _decode_mono(d / "route_aac.aac", 0)
        record("l3.p4b.aac decode 回来的样本数合理 (真正解码验证)",
               decoded is not None and len(decoded) > 40000
               and abs(len(decoded) - out_aac.expected_frames) < 4096,
               f"decoded={0 if decoded is None else len(decoded)}")

    # =====================================================================
    # 5. STREAM_COPY 仍然是 copy (本阶段没有强制重编码)
    # =====================================================================
    retention = build_audio_retention(plan)
    record("l3.p4b.copy 整流计划仍走 -map + -c:a copy (未强制重编码)",
           retention.ok and retention.stream_copyable
           and retention.arguments()
           == ["-map", "0:a:0", "-map", "0:a:1", "-map", "0:a:2",
               "-map", "0:a:3", "-c:a", "copy"],
           retention.arguments())

    # =====================================================================
    # 6. 最终编排: VideoOutput + AudioOutput -> MP4
    # =====================================================================
    video_only = d / "p4b_video.mp4"
    if not _make_video_only(src, video_only):
        record("l3.p4b.编排 视频产物 (纯视频, stream copy) 生成", False,
               "ffmpeg 失败")
    else:
        video_hash_src = _video_hash(video_only)
        video = VideoOutputArtifact(
            path=str(video_only), container="mp4", label="x264",
        )
        composer = OutputComposer()

        # --- (a) 视频 copy + 音频 copy (STREAM_COPY 形态) ----------------
        enc_copy = encode_audio_from_plan(
            plan, ffmpeg=FFMPEG, work_dir=d / "w_copy",
            output_path=d / "copy_pcm.wav",
            audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
            chunk_frames=4096, overwrite=True,
        )
        # 4 声道 PCM 编码后作为**一条** 4ch 音轨进容器
        res_copy = composer.compose(
            video, audio=[enc_copy.output] if enc_copy.ok else [],
            output_path=d / "final_copy.mp4", ffmpeg=FFMPEG,
            ffprobe=FFMPEG.with_name("ffprobe.exe"),
        )
        record("l3.p4b.编排 (a) video copy + audio copy -> MP4",
               res_copy.ok and res_copy.audio_stream_count == 1
               and res_copy.video_stream() is not None,
               res_copy.summary())

        # --- (b) 视频 copy + PCM_ROUTE + 编码音频 ------------------------
        res_route = composer.compose(
            video,
            audio=[enc_route.output] if enc_route.ok else [],
            output_path=d / "final_route.mp4", ffmpeg=FFMPEG,
            ffprobe=FFMPEG.with_name("ffprobe.exe"),
        )
        record("l3.p4b.编排 (b) video copy + PCM_ROUTE + encoded audio",
               res_route.ok and res_route.audio_stream_count == 1,
               res_route.summary())

        # --- (c) 视频 copy + PCM_MIX + 编码音频 --------------------------
        res_mix = composer.compose(
            video, audio=[enc_mix.output] if enc_mix.ok else [],
            output_path=d / "final_mix.mp4", ffmpeg=FFMPEG,
            ffprobe=FFMPEG.with_name("ffprobe.exe"),
        )
        record("l3.p4b.编排 (c) video copy + PCM_MIX + encoded audio",
               res_mix.ok and res_mix.audio_stream_count == 1,
               res_mix.summary())

        # --- (d) 多条编码音轨: 顺序 = 传入顺序 ---------------------------
        multi_specs = []
        for idx, ch in enumerate(("camera:s1:c0", "camera:s2:c0")):
            p = AudioPlanner(_plan_of(src, "camera"))
            p.select_channels(ch)
            enc = encode_audio_from_plan(
                p.plan, ffmpeg=FFMPEG, work_dir=d / f"w_multi{idx}",
                output_path=d / f"multi{idx}_pcm.wav",
                audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
                chunk_frames=4096, overwrite=True,
            )
            if enc.ok and enc.output is not None:
                multi_specs.append(enc.output)
        res_multi = composer.compose(
            video, audio=multi_specs,
            output_path=d / "final_multi.mp4", ffmpeg=FFMPEG,
            ffprobe=FFMPEG.with_name("ffprobe.exe"),
        )
        record("l3.p4b.编排 (d) 两条音频 -> 两条音轨且顺序 = 传入顺序",
               res_multi.ok and len(multi_specs) == 2
               and res_multi.audio_stream_count == 2
               and [t["source_path"] for t in res_multi.audio_tracks]
               == [t.path for t in multi_specs],
               f"{res_multi.summary()}")

        # --- (e) sync -> timeline -> route -> encode -> container ---------
        # arbitrary-reference 的矫正结果必须一路走到**最终编码音频输出**:
        # 本阶段只**消费**已进入 AudioTimeline 的 offset, 不重算 sync。
        #
        # 素材必须是"可观测位移"的形态: 用 4 声道共享伪噪声、每路不同延迟
        # (与既有 sync.timeline 用例同一手法)。纯 sine 不适合 —— 对周期信号
        # 做整数样本位移可能产生**完全一样**的样本, 那样的断言证明不了任何事。
        import numpy as np_mod

        from core.audio_wav import WavFormat as _WF
        from core.audio_wav import write_wav as _write_wav
        from core.audio_wav import read_wav as _read_wav
        from core.audio_sync import (
            apply_sync_result,
            estimate_sync,
            plan_from_plan,
        )

        sync_dir = d / "sync_src"
        sync_dir.mkdir(parents=True, exist_ok=True)
        n = 48000
        base = 9600
        rng = np_mod.random.default_rng(4242)
        delays = (0, 60, -47, 83)
        content = (rng.standard_normal(n - 2 * base) * 0.25).astype(
            np_mod.float32)
        x = np_mod.zeros((n, 4), dtype=np_mod.float32)
        for ch, dl in enumerate(delays):
            b = base + dl
            lo, hi = max(0, b), min(n, b + len(content))
            x[lo:hi, ch] = content[lo - b: hi - b]
        sync_wav = sync_dir / "four_ch.wav"
        _write_wav(sync_wav, [x], sample_rate=48000, channel_count=4,
                   sample_format=_WF.FLOAT32, frame_count=n, overwrite=True)

        def _starts(arr: Any) -> list[int]:
            return [int(np_mod.nonzero(np_mod.abs(arr[:, i]) > 1e-6)[0][0])
                    for i in range(arr.shape[1])]

        # reference = c0; 其余三路被矫正到同一 timeline 位置
        sync_plan = _plan_of(sync_wav, "cam")
        est = estimate_sync(
            sync_plan, plan_from_plan(sync_plan, "cam:s0:c0"),
            ffmpeg=FFMPEG, work_dir=sync_dir / "est",
        )
        written = apply_sync_result(sync_plan, est)
        record("l3.p4b.sync 4CH 各延迟 -> estimate + apply 成功",
               est.ok and written == 3,
               f"offsets={est.offsets()} written={written}")

        enc_sync = encode_audio_from_plan(
            sync_plan, ffmpeg=FFMPEG, work_dir=d / "w_sync",
            output_path=d / "sync_pcm.wav",
            audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
            chunk_frames=4096, overwrite=True,
        )
        record("l3.p4b.sync 矫正后的 plan 经编码成功 (offset 由 timeline 承担)",
               enc_sync.ok and enc_sync.timeline is not None,
               enc_sync.summary())

        if enc_sync.ok and est.ok:
            a_sync, info_sync = _read_wav(d / "sync_pcm.wav")
            st = _starts(a_sync)
            # offset 语义: timeline = source - offset; 4 路对齐后落点相同,
            # 全部落在 base + max(delay) 处 (既有实测结论)。
            record("l3.p4b.sync 编码产物里 4 个声道落到同一 timeline 位置",
                   len(st) == 4 and len(set(st)) == 1,
                   f"starts={st} expected_all_equal")
            record("l3.p4b.sync 编码产物声道内容逐样本一致 (真的对齐了)",
                   all(bool(np_mod.allclose(a_sync[:, i], a_sync[:, 0],
                                            atol=1e-6))
                       for i in range(a_sync.shape[1])),
                   f"shape={a_sync.shape}")
            record("l3.p4b.sync 对齐落点 = base + max(delay) (沿用既有语义)",
                   st and st[0] == base + max(delays),
                   f"start={st[0] if st else None} expected={base + max(delays)}")
            record("l3.p4b.sync 编码无损 -> 帧数与 timeline 完全一致",
                   info_sync.frame_count == enc_sync.timeline.frame_count,
                   f"{info_sync.frame_count} vs "
                   f"{enc_sync.timeline.frame_count}")
            record("l3.p4b.sync offset 来自 timeline, 编码器没有重算 sync",
                   # 编码产物就是渲染结果的 PCM 副本: 若编码层重算过 sync,
                   # 4 个声道的对齐落点不可能仍然相同。
                   len(set(st)) == 1 and st[0] == base + max(delays))

            res_sync = composer.compose(
                video, audio=[enc_sync.output],
                output_path=d / "final_sync.mp4", ffmpeg=FFMPEG,
                ffprobe=FFMPEG.with_name("ffprobe.exe"),
            )
            sync_tl = enc_sync.timeline.duration_seconds
            sa = res_sync.audio_streams()[0] if res_sync.ok else {}
            try:
                sa_sec = float(sa.get("duration") or 0.0)
            except (TypeError, ValueError):
                sa_sec = 0.0
            record("l3.p4b.sync 同步音频进入最终容器且时长 = timeline",
                   res_sync.ok and abs(sa_sec - sync_tl) < 0.06,
                   f"container={sa_sec:.4f} timeline={sync_tl:.4f}")
            record("l3.p4b.sync 同步路径下视频 hash 依然不变",
                   _video_hash(d / "final_sync.mp4") == video_hash_src,
                   "hash compared")
        else:
            record("l3.p4b.sync 端到端同步编排", False,
                   f"enc={enc_sync.summary()} est={est.ok}")

        # =================================================================
        # 7. ⚠️ 关键回归: 视频没有被音频路径影响
        # =================================================================
        finals = {
            "copy": d / "final_copy.mp4",
            "route": d / "final_route.mp4",
            "mix": d / "final_mix.mp4",
            "multi": d / "final_multi.mp4",
        }
        hashes = {
            name: _video_hash(path)
            for name, path in finals.items() if path.is_file()
        }
        record("l3.p4b.video 视频 basic stream hash 在四种音频处理下完全一致",
               bool(hashes) and video_hash_src
               and all(h == video_hash_src for h in hashes.values()),
               f"src={video_hash_src[:16]} "
               f"{ {k: v[:16] for k, v in hashes.items()} }")

        src_v = _video_of(video_only) or {}
        same_props = []
        for name, path in finals.items():
            if not path.is_file():
                continue
            v = _video_of(path) or {}
            same_props.append(
                (v.get("codec_name"), v.get("width"), v.get("height"),
                 v.get("avg_frame_rate")) == (
                    src_v.get("codec_name"), src_v.get("width"),
                    src_v.get("height"), src_v.get("avg_frame_rate"))
            )
        record("l3.p4b.video 视频 codec/宽/高/帧率 与视频产物一致",
               bool(same_props) and all(same_props), f"{same_props}")

        # composer 的命令里视频永远是 copy, 且不出现任何视频编码参数
        cmd = res_route.command
        record("l3.p4b.video composer 只 stream copy 视频 (无视频编码参数)",
               "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
               and all(tok not in cmd for tok in (
                   "-crf", "-preset", "libx265", "libsvtav1", "-pix_fmt",
               )),
               f"{cmd[-8:]}")

        # 音频参数只作用于音频
        record("l3.p4b.video composer 的音频参数只有 -map / -c:a copy",
               "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
               and "-b:a" not in cmd and "-ar" not in cmd,
               f"{cmd}")

        # =================================================================
        # 8. 编排后的音轨事实 (ffprobe 读回)
        # =================================================================
        final_audio = _audio_of(finals["route"])
        record("l3.p4b.编排 输出的音轨 codec/声道/采样率 = 编码产物",
               len(final_audio) == 1
               and final_audio[0].get("codec_name") == "pcm_s16le"
               and final_audio[0].get("channels") == 2
               and int(final_audio[0].get("sample_rate")) == 48000,
               f"{[(s.get('codec_name'), s.get('channels'), s.get('sample_rate')) for s in final_audio]}")

        order_ok = True
        order_detail = []
        if len(multi_specs) == 2:
            fa = _audio_of(finals["multi"])
            # 每条 mono 编码产物的频率不同, 解码后按频率区分先后
            freqs = []
            for i in range(len(fa)):
                data = _decode_mono(finals["multi"], i)
                if data is None:
                    order_ok = False
                    break
                freqs.append(_dominant_hz(data, 48000))
            order_detail.append(f"freqs={[round(f) for f in freqs]}")
            order_ok = order_ok and len(freqs) == 2 and freqs[0] < freqs[1]
        record("l3.p4b.编排 音轨在容器里的顺序 = 传入顺序 (身份未错位)",
               order_ok, " ".join(order_detail))

        # =================================================================
        # 9. duration 策略: 容器不擅自 truncate / loop 音频
        # =================================================================
        timeline_sec = enc_route.timeline.duration_seconds
        if finals["route"].is_file():
            ra = _audio_of(finals["route"])[0]
            try:
                audio_sec = float(ra.get("duration") or 0.0)
            except (TypeError, ValueError):
                audio_sec = 0.0
            record("l3.p4b.duration 容器里的音频时长 = timeline 时长",
                   abs(audio_sec - timeline_sec) < 0.05,
                   f"container={audio_sec:.4f} timeline={timeline_sec:.4f}")

        # --- 音频比视频长: Composer **不**截断, 也不用 -shortest ----------
        # (Composer 不是时长权威: 截断/循环属于篡改, 一律不做。若将来需要
        #  "输出长度策略", 那是 AudioTimeline / 显式参数的事。)
        long_src = d / "p4b_long.mov"
        if _make_av(long_src, 1, seconds=3):
            long_plan = AudioPlanner(_plan_of(long_src, "camera"))
            long_plan.select_channels("camera:s1:c0")
            enc_long = encode_audio_from_plan(
                long_plan.plan, ffmpeg=FFMPEG, work_dir=d / "w_long",
                output_path=d / "long_pcm.wav",
                audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
                chunk_frames=4096, overwrite=True,
            )
            res_long = composer.compose(
                video,
                audio=[enc_long.output] if enc_long.ok else [],
                output_path=d / "final_long.mp4", ffmpeg=FFMPEG,
                ffprobe=FFMPEG.with_name("ffprobe.exe"),
            )
            long_tl = (
                enc_long.timeline.duration_seconds if enc_long.ok
                and enc_long.timeline is not None else 0.0
            )
            if res_long.ok and long_tl > 2.5:
                la = res_long.audio_streams()[0]
                try:
                    la_sec = float(la.get("duration") or 0.0)
                except (TypeError, ValueError):
                    la_sec = 0.0
                record("l3.p4b.duration 音频长于视频时不被截断 (-shortest 未启用)",
                       abs(la_sec - long_tl) < 0.06
                       and "-shortest" not in res_long.command,
                       f"audio={la_sec:.4f} timeline={long_tl:.4f} "
                       f"shortest={'-shortest' in res_long.command}")
                record("l3.p4b.duration 音频长于视频时容器时长取二者较长",
                       res_long.duration_seconds >= long_tl - 0.06,
                       f"container={res_long.duration_seconds:.4f} "
                       f"audio={long_tl:.4f}")
                record("l3.p4b.video 音频更长时视频 hash 依然不变",
                       _video_hash(d / "final_long.mp4") == video_hash_src,
                       "hash compared")
            else:
                record("l3.p4b.duration 长音频编排", False,
                       f"ok={res_long.ok} tl={long_tl} "
                       f"err={res_long.errors}")
        else:
            record("l3.p4b.duration 长音频素材生成", False, "ffmpeg failed")

    # =====================================================================
    # 10. 临时产物生命周期: 中间 WAV 默认被清理
    # =====================================================================
    keep_dir = d / "w_lifecycle"
    enc_life = encode_audio_from_plan(
        routed, ffmpeg=FFMPEG, work_dir=keep_dir,
        output_path=d / "lifecycle.wav",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
        chunk_frames=4096, overwrite=True,
    )
    leftovers = sorted(p.name for p in keep_dir.glob("*.render.wav"))
    record("l3.p4b.lifecycle 中间渲染 WAV 默认被清理 (无工作区垃圾)",
           enc_life.ok and leftovers == [], f"leftovers={leftovers}")

    keep_dir2 = d / "w_lifecycle_keep"
    enc_keep = encode_audio_from_plan(
        routed, ffmpeg=FFMPEG, work_dir=keep_dir2,
        output_path=d / "lifecycle_keep.wav",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
        chunk_frames=4096, overwrite=True, keep_intermediate=True,
    )
    kept = sorted(p.name for p in keep_dir2.glob("*.render.wav"))
    record("l3.p4b.lifecycle keep_intermediate=True 时保留中间产物",
           enc_keep.ok and len(kept) == 1, f"kept={kept}")

    # 失败路径也不能留垃圾
    fail_dir = d / "w_lifecycle_fail"
    fail_dir.mkdir(parents=True, exist_ok=True)
    bad = encode_audio_from_plan(
        routed, ffmpeg=FFMPEG, work_dir=fail_dir,
        output_path=d / "lifecycle_fail.aac",
        audio_format=AudioFormatSpec(
            format=AudioEncodeFormat.AAC, sample_rate=44100
        ),
        chunk_frames=4096, overwrite=True,
    )
    fail_leftovers = sorted(p.name for p in fail_dir.glob("*.render.wav"))
    record("l3.p4b.lifecycle 失败路径同样清理中间产物",
           bad.ok is False and fail_leftovers == [],
           f"ok={bad.ok} leftovers={fail_leftovers}")


def _dominant_hz(data: Any, sample_rate: int) -> float:
    """主频 (用于确认音轨身份/顺序)。"""
    import numpy as np

    if data is None or len(data) < 1024:
        return 0.0
    window = data[: min(len(data), sample_rate)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1.0 / sample_rate)
    if not len(spectrum):
        return 0.0
    return float(freqs[int(np.argmax(spectrum))])

