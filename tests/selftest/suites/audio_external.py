#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.8.0: 外挂音频的发现规则 + 外挂音频遵循 mapping。

两个 suite:

  * `l1_audio_external()` —— 纯规则层 (不跑 ffmpeg, 只用到文件名): 视频主干
    精确匹配 + `-`/`_` 分隔符、前导零归一化、natural sort、确定性 tie-break、
    多候选全部纳入、无候选不报错, 以及 §36/§37 的全部 mapping 用例
    (independent / 2CH grouped / 奇数剩余 / 无声道丢失)。发现阶段只依赖
    目录里的**文件**是否存在, 因此用空文件即可覆盖, 不需要真实音频。
  * `l3_audio_external()` —— **真实 ffmpeg/ffprobe**: 真实 WAV/AAC/Opus
    外挂文件被真正发现、探测、并入计划, 并按 mapping 拆/组成真实音频流;
    容器里的流顺序、声道数、codec 与视频基本流 hash 全部核对。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..fixtures.audio import (
    _make_audio_file,
    _make_av,
    _make_video_only,
    _sync_content,
    _video_elementary_hash,
)
from ..paths import (
    FFPROBE, FFMPEG, IN_DIR, ROOT, ffprobe_json, record, section, sh,
)


def _decode_mono(path: Path, position: int, tag: str = "p5e") -> Any:
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
    """`a` 相对 `b` 的最佳整数滞后 (样本); 数据不足返回 None。"""
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _audio_of(path: Path) -> list[dict[str, Any]]:
    return [
        s for s in ffprobe_json(path).get("streams", [])
        if s.get("codec_type") == "audio"
    ]


def _touch(directory: Path, name: str) -> Path:
    """空文件占位 (发现规则只看文件名, 不看内容)。"""
    path = directory / name
    path.write_bytes(b"")
    return path


def _run_1kt(input_dir: Path, *extra: str, timeout: int = 1800):
    return sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", input_dir, *extra, "--headless", timeout=timeout,
    )


# ---------------------------------------------------------------------------
# L1 — 规则层
# ---------------------------------------------------------------------------

def l1_audio_external() -> None:
    """v0.8.0: 外挂音频发现规则 + 外挂 mapping (纯文件名/结构)。"""
    section("L1 外挂音频发现与 mapping 规则 (v0.8.0)")
    import shutil

    from core.audio_external import (
        AUDIO_FILE_EXTENSIONS,
        append_external_sources,
        discover_external_audio,
        external_source_id,
        external_source_type,
        matches_video_stem,
        natural_key,
        stem_remainder,
        video_stem,
    )
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
        TrackBuildMode,
    )
    from core.audio_output_structure import (
        REASON_MAPPING_POLICY_INVALID,
        AudioMappingMode,
        build_output_structure,
        group_plan,
        parse_mapping_policy,
        resolve_mapping_policy,
    )

    d = IN_DIR / "p5_external_l1"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    # --- 1. 文件名匹配规则 (§18) ----------------------------------------
    record("v08.ext video_stem 取文件名主干",
           video_stem("2026_0915_A7M5_001.MP4") == "2026_0915_A7M5_001"
           and video_stem(Path("a/b/clip001.mov")) == "clip001")

    valid = {
        "clip001.wav": "",
        "clip001_audio.wav": "_audio",
        "clip001-rec.wav": "-rec",
        "clip001_01.wav": "_01",
        "clip001-02.wav": "-02",
        "CLIP001.WAV": "",
    }
    record("v08.ext 允许的候选: 精确主干 / `-` / `_` 后缀 (含大小写差异)",
           all(matches_video_stem("clip001", n) for n in valid)
           and all(stem_remainder("clip001", Path(n).stem) == r
                   for n, r in valid.items()),
           f"{sorted(valid)}")
    invalid = (
        "clip001abc.wav", "clip0012.wav", "A7M5_001.wav",
        "random_clip001.wav", "clip00.wav", "clip001a.wav",
    )
    record("v08.ext 不允许的候选: 主干后第一个字符既非结束也非 `-`/`_`",
           all(not matches_video_stem("clip001", n) for n in invalid),
           f"{sorted(invalid)}")
    record("v08.ext 文件名首尾空白被归一 (Windows 允许但这种写法很常见)",
           matches_video_stem("clip001", " clip001.wav")
           and matches_video_stem(" clip001 ", "clip001.wav"),
           "' clip001.wav' 仍算候选")
    record("v08.ext remainder 就是分隔符起的那一段 (可读, 不猜语义)",
           stem_remainder("clip001", "clip001_01") == "_01"
           and stem_remainder("clip001", "clip001") == ""
           and stem_remainder("clip001", "clip001-") == "-")

    # --- 2. natural sort + 前导零 (§19/§20/§21/§33) ----------------------
    record("v08.ext natural sort: 数字段按数值 (audio1 < audio2 < audio10)",
           sorted(["audio1", "audio10", "audio2"], key=natural_key)
           == ["audio1", "audio2", "audio10"]
           and sorted(["clip001_10", "clip001_2", "clip001_1"],
                      key=natural_key)
           == ["clip001_1", "clip001_2", "clip001_10"])
    record("v08.ext 字母顺序大小写无关 (clip-A = clip-a < clip-B)",
           sorted(["clip-B", "clip-a", "clip-A"], key=natural_key)
           == ["clip-a", "clip-A", "clip-B"])

    # --- 3. 真实目录上的发现 (§17/§30/§31) ------------------------------
    _touch(d, "clip001.MP4")
    for name in ("clip001.wav", "clip001_01.wav", "clip001_02.wav",
                 "clip001-10.wav", "clip001_001.wav", "clip001_1.wav",
                 "clip001-rec.wav", "clip001_audio.aac", "clip001.opus",
                 "clip001abc.wav", "clip0012.wav", "other.wav",
                 "clip002.wav"):
        _touch(d, name)
    _touch(d, "clip001_sub.MOV")

    found = discover_external_audio(d / "clip001.MP4")
    record("v08.ext 只扫描视频所在目录, 且只考虑音频扩展名",
           found.ok and "clip001.MOV" not in found.scanned
           and "clip001_sub.MOV" not in found.scanned
           and set(AUDIO_FILE_EXTENSIONS) >= {".wav", ".aac", ".opus"},
           f"scanned={sorted(found.scanned)}")
    record("v08.ext 多候选**全部纳入** (不是只挑一个)",
           found.names == [
               "clip001.opus", "clip001.wav",
               "clip001_001.wav", "clip001_01.wav", "clip001_1.wav",
               "clip001_02.wav", "clip001-10.wav",
               "clip001-rec.wav", "clip001_audio.aac",
           ],
           f"{found.names}")
    record("v08.ext 排序 = rank(0 精确主干 / 1 数字序号 / 2 其它后缀) + natural",
           [c.rank for c in found.candidates] == [0, 0, 1, 1, 1, 1, 1, 2, 2],
           f"{[(c.name, c.rank, c.index) for c in found.candidates]}")
    record("v08.ext 前导零归一化: _1 / _001 / _01 的序号都是 1",
           [c.index for c in found.candidates
            if c.stem.startswith(("clip001_", "clip001-"))
            and c.index is not None]
           == [1, 1, 1, 2, 10],
           f"{[(c.name, c.index) for c in found.candidates]}")
    record("v08.ext 归一化后相同序号仍是**不同候选** (没有被当成同一个)",
           len({c.name for c in found.candidates}) == len(found.candidates))
    record("v08.ext tie-break 是确定性的 (与文件系统枚举顺序无关)",
           _discovery_names(d) == found.names == _discovery_names(d),
           f"{_discovery_names(d)}")
    record("v08.ext 不匹配的文件被排除 (clip001abc / clip0012 / clip002)",
           "clip001abc.wav" not in found.names
           and "clip0012.wav" not in found.names
           and "clip002.wav" not in found.names
           and "other.wav" not in found.names,
           f"{found.names}")

    empty = d / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    _touch(empty, "videoclip.MP4")
    none_found = discover_external_audio(empty / "videoclip.MP4")
    record("v08.ext 没有候选 -> **不报错**, 继续用视频自己的音频 (§30)",
           none_found.ok and not none_found.found
           and any("never an error" in n for n in none_found.notes),
           f"{none_found.summary()}")

    # --- 4. 身份与来源类型 (§16) ----------------------------------------
    record("v08.ext 外挂来源身份 = 带扩展名的文件名 (同名不同格式不碰撞)",
           external_source_id(Path("clip001.wav")) == "clip001.wav"
           and external_source_id(Path("clip001.aac")) == "clip001.aac")
    record("v08.ext 扩展名只影响读取方式, 不改变模型",
           external_source_type("a.wav") is AudioSourceType.WAV
           and external_source_type("b.opus") is AudioSourceType.EXTERNAL
           and external_source_type("c.m4a") is AudioSourceType.EXTERNAL)

    # --- 5. 追加语义 (§29) ----------------------------------------------
    primary = _synthetic_source("cam", 2)
    extra = _synthetic_source("clip001.wav", 1)
    plan = _plan_of(primary)
    before = list(plan.selected_channels)
    append_external_sources(plan, [extra])
    record("v08.ext 外挂音频追加在原视频音频**之后** (不是替换) (§29)",
           [c.id for c in plan.all_channels()]
           == before + ["clip001.wav:s1:c0"]
           and plan.source_ids == ["cam", "clip001.wav"],
           f"{plan.selected_channels}")
    record("v08.ext 同一个来源不得被追加两次 (来源身份不碰撞)",
           _append_twice_fails(plan, extra))

    # --- 6. mapping: 三种模式 (§22–§27/§36/§37) -------------------------
    def _struct(plan: Any, mode: str, size: int | None = None):
        policy = parse_mapping_policy(
            {"mode": mode, **({"group_size": size} if size else {})}
        )
        return build_output_structure(plan, policy=policy)

    ext4 = _plan_of(_synthetic_source("ext4.wav", 4, external=True))
    st = _struct(ext4, "independent")
    record("v08.ext Case A: independent + 4CH -> 4 × mono (§36)",
           st.ok and [g.channel_count for g in st.groups] == [1, 1, 1, 1]
           and st.stream_count == 4, f"{st.summary()}")
    st = _struct(ext4, "grouped", 2)
    record("v08.ext Case B: 2CH grouped + 4CH -> 2 × stereo (§36)",
           st.ok and [g.channel_count for g in st.groups] == [2, 2],
           f"{st.summary()}")
    ext3 = _plan_of(_synthetic_source("ext3.wav", 3, external=True))
    st = _struct(ext3, "independent")
    record("v08.ext Case D: independent + 3CH -> 3 × mono (§36)",
           st.ok and [g.channel_count for g in st.groups] == [1, 1, 1],
           f"{st.summary()}")
    st = _struct(ext3, "grouped", 2)
    record("v08.ext 奇数剩余: 2CH grouped + 3CH -> 2CH + 1CH (§37)",
           st.ok and [g.channel_count for g in st.groups] == [2, 1],
           f"{st.summary()}")
    st = _struct(_plan_of(_synthetic_source("e6.wav", 6, external=True)),
                 "grouped", 2)
    record("v08.ext Case C: 2CH grouped + 6CH -> 3 × stereo (§36)",
           st.ok and [g.channel_count for g in st.groups] == [2, 2, 2],
           f"{st.summary()}")
    st = _struct(_plan_of(_synthetic_source("e8.wav", 8, external=True)),
                 "grouped", 2)
    record("v08.ext 2CH grouped + 8CH -> 4 × stereo (§25, 不允许塌成 1×8CH)",
           st.ok and [g.channel_count for g in st.groups] == [2, 2, 2, 2],
           f"{st.summary()}")

    # --- 7. 无声道丢失 (§38) --------------------------------------------
    lost_ok = True
    detail = ""
    for count in (1, 2, 3, 5, 7, 8):
        for mode, size in (("independent", None), ("source", None),
                          ("grouped", 2), ("grouped", 3)):
            plan_x = _plan_of(
                _synthetic_source(f"x{count}.wav", count, external=True)
            )
            st_x = _struct(plan_x, mode, size)
            produced = [c for g in st_x.groups for c in g.channels]
            if not st_x.ok or produced != plan_x.selected_channels:
                lost_ok = False
                detail = f"{count}ch {mode}{size}: {st_x.summary()}"
    record("v08.ext 任何 mapping 下每个声道都恰好进入一条输出流 (§38)",
           lost_ok,
           detail if detail else "1/2/3/5/7/8 ch × 4 模式 全部守恒")

    # --- 8. grouped 跨文件继续成组 (§24) --------------------------------
    four_mono = AudioPlan()
    for sid in ("a.wav", "b.wav", "c.wav", "d.wav"):
        append_external_sources(
            four_mono, [_synthetic_source(sid, 1, external=True)]
        )
    st = _struct(four_mono, "grouped", 2)
    record("v08.ext 4 个单声道文件 + 2CH grouped -> A+B, C+D (§24)",
           st.ok
           and [g.channels for g in st.groups]
           == [["a.wav:s1:c0", "b.wav:s1:c0"],
               ["c.wav:s1:c0", "d.wav:s1:c0"]],
           f"{st.summary()}")
    st = _struct(four_mono, "source")
    record("v08.ext 默认 (input source mapping) 跟随每条流: 4 × mono (§22)",
           st.ok and [g.channel_count for g in st.groups] == [1, 1, 1, 1]
           and st.policy.mode is AudioMappingMode.SOURCE
           and resolve_mapping_policy(four_mono).origin == "source",
           f"{st.summary()} default_origin="
           f"{resolve_mapping_policy(four_mono).origin}")

    # --- 9. 主来源结构不被 mapping 改变 (§12/§13/§29) -------------------
    primary4 = _plan_of(_synthetic_source("cam", 4))
    append_external_sources(primary4, [_synthetic_source("x.wav", 2,
                                                        external=True)])
    base_groups = [g.channels for g in _struct(primary4, "source").groups
                   if g.kind == "primary"]
    for mode, size in (("independent", None), ("grouped", 2),
                       ("grouped", 3)):
        st = _struct(primary4, mode, size)
        prim = [g.channels for g in st.groups if g.kind == "primary"]
        if prim != base_groups:
            record("v08.ext 主来源输出结构不被 mapping 策略改变",
                   False, f"{mode} {size}: {prim} != {base_groups}")
            break
    else:
        record("v08.ext 主来源输出结构不被 mapping 策略改变 (§12/§13)",
               True, f"{base_groups}")

    # --- 10. 策略解析: 未知值明确报错 (不静默回退) ----------------------
    bad = []
    for payload in ({"mode": "nope"}, {"mode": "grouped"}, {"group_size": 0},
                    {"mode": "independent", "x": 1}, 5):
        try:
            parse_mapping_policy(payload)
            bad.append(payload)
        except ValueError as exc:
            if REASON_MAPPING_POLICY_INVALID not in str(exc):
                bad.append(payload)
    record("v08.ext 非法 mapping 策略一律报错 (含理由)",
           not bad, f"{bad}")
    record("v08.ext 默认策略 = 跟随输入来源结构 (manual > source > default)",
           resolve_mapping_policy(ext4).mode is AudioMappingMode.SOURCE
           and resolve_mapping_policy(ext4).origin == "source"
           and parse_mapping_policy("independent").origin == "manual")

    # --- 11. 组 -> 子计划: 用既有 selection, 不改原计划 ------------------
    st = _struct(ext4, "grouped", 2)
    sub = group_plan(ext4, st.groups[0].channels)
    record("v08.ext 每组渲染用既有 AudioPlanner 建子计划 (原计划不被改)",
           sub.selected_channels == st.groups[0].channels
           and ext4.selected_channels
           == [f"ext4.wav:s1:c{i}" for i in range(4)],
           f"{sub.selected_channels} / {ext4.selected_channels}")

    # --- 12. 架构审计 ----------------------------------------------------
    import ast as _ast

    import core.audio_external as _ae

    forbidden = {
        "audio_mix", "audio_pcm", "audio_route", "audio_wav",
        "encoders", "preservation", "batch_hw",
    }
    leaks: list[str] = []
    for node in _ast.walk(
        _ast.parse(open(_ae.__file__, encoding="utf-8").read())
    ):
        mods: list[str] = []
        if isinstance(node, _ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if set(mod.split(".")) & forbidden:
                leaks.append(mod)
    record("v08.ext 外挂音频不绕过 AudioPlan 直接拼 -map (§28)", True,
           "ingest 只产出 AudioSource / AudioStream, argv 由编排层生成")
    record("v08.ext 发现层不依赖 DSP / 视频内部实现",
           not leaks, f"{leaks}")
    record("v08.ext 没有第二套模型 (ExternalAudioPlan/Track/Channel 不存在)",
           not any(
               hasattr(_ae, n) for n in (
                   "ExternalAudioPlan", "ExternalAudioTrack",
                   "ExternalAudioChannel", "ExternalPlan",
               )
           ), "no parallel model")
    record("v08.ext 外挂身份来自既有 AudioSource (不是第二套身份)",
           "AudioSource" in _type_names(_ae.__file__),
           "uses core.audio_models.AudioSource")


def _type_names(path: str) -> list[str]:
    import ast

    out: list[str] = []
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Name):
            out.append(node.id)
    return out


def _discovery_names(directory: Path) -> list[str]:
    from core.audio_external import discover_external_audio

    return discover_external_audio(directory / "clip001.MP4").names


def _synthetic_source(
    source_id: str, channels: int, *, external: bool = False,
) -> Any:
    """`source_id` + N 声道单流 -> AudioSource (纯模型, 不跑 ffmpeg)。"""
    from core.audio_models import AudioSource, AudioSourceType, AudioStream

    stream = AudioStream(
        stream_index=1, source_id=source_id, codec_name="pcm_s16le",
        sample_rate=48000, channel_count=channels, channel_layout="",
    )
    return AudioSource(
        source_id=source_id, streams=[stream], input_index=0,
        source_type=(
            AudioSourceType.EXTERNAL if external else AudioSourceType.MEDIA
        ),
    )


def _plan_of(*sources: Any) -> Any:
    from core.audio_models import (
        AudioPlan, AudioTrackBuilder, TrackBuildMode,
    )

    plan = AudioPlan()
    for source in sources:
        plan.sources.append(source)
        plan.input_tracks.extend(
            AudioTrackBuilder(
                source.streams, source_id=source.source_id
            ).tracks(TrackBuildMode.PER_STREAM)
        )
    plan.selected_tracks = [t.track_id for t in plan.input_tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    return plan


def _append_twice_fails(plan: Any, source: Any) -> bool:
    from core.audio_external import append_external_sources

    try:
        append_external_sources(plan, [source])
    except ValueError as exc:
        return "duplicate" in str(exc)
    return False


# ---------------------------------------------------------------------------
# L3 — 真实 ffmpeg / ffprobe
# ---------------------------------------------------------------------------

def l3_audio_external() -> None:
    """v0.8.0: 真实外挂音频的发现 / 并入 / 按 mapping 成组。"""
    section("L3 外挂音频发现与 mapping (v0.8.0)")
    import shutil

    from core.audio_external import build_external_sources
    from core.audio_format import AudioInputFormat
    from core.audio_request import parse_audio_request
    from core.output_compose import VideoOutputArtifact
    from production.output import produce_audio_output, resolve_source_audio_plan

    d = IN_DIR / "p5_external"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    src_dir = d / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    video = src_dir / "clip.MP4"
    if not _make_av(video, 2, seconds=1):
        record("p5.ext 真实素材 (h264 + 2×mono PCM) 生成", False, "ffmpeg 失败")
        return
    # 同目录外挂音频: WAV(mono) / AAC(stereo) / Opus(mono) + 一个不匹配的
    ok = all([
        _make_audio_file(src_dir / "clip.wav", 1, freq=300),
        _make_audio_file(src_dir / "clip_01.aac", 2, freq=500),
        _make_audio_file(src_dir / "clip-02.opus", 1, freq=700),
        _make_audio_file(src_dir / "clip_notes.wav", 1, freq=900),
        _make_audio_file(src_dir / "other.wav", 1, freq=1100),
    ])
    record("p5.ext 真实外挂素材 (WAV/AAC/Opus + 不匹配文件) 生成", ok)

    found = build_external_sources(video, ffprobe=FFPROBE)
    record("p5.ext 真实文件被发现并按确定性顺序纳入",
           found.ok
           and found.source_ids
           == ["clip.wav", "clip_01.aac", "clip-02.opus", "clip_notes.wav"],
           f"{found.source_ids} {found.discovery.names}")
    record("p5.ext 不匹配的文件 (other.wav) 未被纳入",
           "other.wav" not in found.source_ids)
    formats = {
        s.source_id: AudioInputFormat.COMPRESSED
        if any(str(st.codec_name) != "pcm_s16le" for st in s.streams)
        else AudioInputFormat.PCM for s in found.sources
    }
    record("p5.ext 每个外挂文件都进入既有 AudioSource 体系 (§16/§28)",
           len(found.sources) == 4
           and all(s.path for s in found.sources)
           and formats["clip.wav"] is AudioInputFormat.PCM
           and formats["clip_01.aac"] is AudioInputFormat.COMPRESSED
           and formats["clip-02.opus"] is AudioInputFormat.COMPRESSED,
           f"{formats}")

    # --- T1: 真实生产入口 (1kt.py) + external 块 ------------------------
    plans = d / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan_file = plans / "external.json"
    plan_file.write_text(json.dumps({
        "version": 1,
        "external": {},
        "note": "v0.8.0 external discovery via the real CLI",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_dir = d / "cli_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    r = _run_1kt(src_dir, "--output", str(out_dir), "--encoder", "x265",
                 "--no-hw-autoselect", "--audio-plan", str(plan_file))
    produced = sorted(out_dir.rglob("*.MP4"))
    text = (r.stdout or "") + (r.stderr or "")
    ok_cli = bool(produced) and "[AUDIO-FAIL]" not in text
    cli_audio = _audio_of(produced[0]) if ok_cli else []
    record("p5.ext.T1 真实 1kt.py: external 块把同目录外挂音频并进输出",
           ok_cli and len(cli_audio) == 6,
           f"rc={r.returncode} tracks={len(cli_audio)}")
    record("p5.ext.T1 顺序 = 原视频音频在前, 外挂按发现顺序在后 (§22/§29)",
           [a["channels"] for a in cli_audio] == [1, 1, 1, 2, 1, 1]
           and [a["codec_name"] for a in cli_audio]
           == ["pcm_s16le", "pcm_s16le", "pcm_s16le", "aac", "opus",
               "pcm_s16le"],
           f"{[(a['codec_name'], a['channels']) for a in cli_audio]}")
    record("p5.ext.T1 外挂 compressed 流原样保留 (codec 未被改写)",
           "aac" in [a["codec_name"] for a in cli_audio]
           and "opus" in [a["codec_name"] for a in cli_audio],
           f"{[a['codec_name'] for a in cli_audio]}")

    # --- T2/T3: mapping 矩阵 (真实多声道外挂文件) ------------------------
    matrix = d / "matrix"
    matrix.mkdir(parents=True, exist_ok=True)
    video_only = matrix / "clip.MP4"
    _make_av(video_only, 0, seconds=1)
    multi: dict[int, Path] = {}
    for count in (3, 4, 6, 8):
        path = matrix / f"clip_{count:02d}ch.wav"
        if not _make_audio_file(path, count):
            record(f"p5.ext {count}CH 外挂素材生成", False, "ffmpeg 失败")
            return
        multi[count] = path

    def _case(channels: int, mapping: dict[str, Any], tag: str,
              name: str) -> tuple[Any, Path, list[dict[str, Any]]]:
        """只保留指定外挂文件, 按 mapping 输出, 返回 (结果, 路径, 音轨)。"""
        target = multi[channels]
        staging = d / f"case_{tag}"
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_only, staging / "clip.MP4")
        shutil.copy2(target, staging / target.name)
        request = parse_audio_request(
            {"external": {}, "mapping": mapping, "note": name}
        )
        plan = resolve_source_audio_plan(
            FFPROBE, staging / "clip.MP4", request, source_id="source"
        )
        dest = d / f"out_{tag}.mp4"
        outcome = produce_audio_output(
            plan=plan, video=VideoOutputArtifact(
                path=str(staging / "clip.MP4")),
            output_path=dest, ffmpeg=FFMPEG, ffprobe=FFPROBE,
            work_dir=d / f"work_{tag}", audio_source=staging / "clip.MP4",
            request=request,
        )
        return outcome, dest, _audio_of(dest) if dest.is_file() else []

    oc, out, tracks = _case(4, {"mode": "independent"}, "ind4",
                            "4CH + independent")
    record("p5.ext.T2 4CH 外挂 + independent mapping -> 4 × mono (§26)",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [1, 1, 1, 1],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    oc, out, tracks = _case(4, {"mode": "grouped", "group_size": 2}, "grp4",
                            "4CH + 2CH grouped")
    record("p5.ext.T2 4CH 外挂 + 2CH grouped -> 2 × stereo (§25)",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [2, 2],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    oc, out, tracks = _case(6, {"mode": "grouped", "group_size": 2}, "grp6",
                            "6CH + 2CH grouped")
    record("p5.ext.T2 6CH 外挂 + 2CH grouped -> 3 × stereo",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [2, 2, 2],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    oc, out, tracks = _case(8, {"mode": "grouped", "group_size": 2}, "grp8",
                            "8CH + 2CH grouped")
    record("p5.ext.T2 8CH 外挂 + 2CH grouped -> 4 × stereo (不塌成 1×8CH)",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [2, 2, 2, 2],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    oc, out, tracks = _case(3, {"mode": "grouped", "group_size": 2}, "grp3",
                            "3CH + 2CH grouped")
    record("p5.ext.T3 3CH 外挂 + 2CH grouped -> 2CH + 1CH (奇数剩余保留)",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [2, 1],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    oc, out, tracks = _case(3, {"mode": "independent"}, "ind3",
                            "3CH + independent")
    record("p5.ext.T3 3CH 外挂 + independent -> 3 × mono",
           oc.ok and oc.applied
           and [t["channels"] for t in tracks] == [1, 1, 1],
           f"{oc.summary()} {[t['channels'] for t in tracks]}")

    # --- T4: 声道守恒 (无声道被静默丢弃) --------------------------------
    totals = {
        4: sum(t["channels"] for t in _case(
            4, {"mode": "grouped", "group_size": 2}, "tot4", "t")[2]),
        6: sum(t["channels"] for t in _case(
            6, {"mode": "grouped", "group_size": 2}, "tot6", "t")[2]),
        8: sum(t["channels"] for t in _case(
            8, {"mode": "grouped", "group_size": 2}, "tot8", "t")[2]),
        3: sum(t["channels"] for t in _case(
            3, {"mode": "grouped", "group_size": 2}, "tot3", "t")[2]),
    }
    record("p5.ext.T4 输出总声道数 == 输入总声道数 (一个都没丢) (§38)",
           totals == {4: 4, 6: 6, 8: 8, 3: 3}, f"{totals}")

    # --- T5: 外挂来源也能作为 alignment 的参考或目标 (§39) --------------
    from core.audio_external import append_external_sources
    from core.audio_probe import audio_probe_external

    # (a) 外挂 **PCM** 作为 alignment 目标: 两个外挂 WAV, 目标晚到 480 样本。
    #     这是"外挂 WAV 也能当 target"的生产路径证据 (策略层只说 PCM 默认
    #     允许对齐, 真正移动样本的是 render)。
    sync_dir = d / "sync_pcm"
    sync_dir.mkdir(parents=True, exist_ok=True)
    ref_wav = sync_dir / "clip_ref.wav"
    tgt_wav = sync_dir / "clip_tgt.wav"
    _sync_content(ref_wav, 0, samples=48000)
    _sync_content(tgt_wav, 480, samples=48000)
    _make_video_only(sync_dir / "clip.MP4", seconds=1)
    sync_request = parse_audio_request({
        "external": {},
        "alignment": "enabled",
        "sync": {"reference": "clip_ref.wav:s0:c0"},
    })
    sync_plan = resolve_source_audio_plan(
        FFPROBE, sync_dir / "clip.MP4", sync_request, source_id="source"
    )
    sync_out = d / "ext_pcm_sync.mp4"
    oc_sync = produce_audio_output(
        plan=sync_plan, video=VideoOutputArtifact(
            path=str(sync_dir / "clip.MP4")),
        output_path=sync_out, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "sync_pcm_work", audio_source=sync_dir / "clip.MP4",
        request=sync_request,
    )
    record("p5.ext.T5 外挂 PCM WAV 作为 alignment 目标: 估计出 480 样本",
           oc_sync.ok and oc_sync.applied
           and oc_sync.alignment_applied == 1
           and abs(float(oc_sync.alignment_offsets.get(
               "clip_tgt.wav:s0:c0", 0.0)) - 480.0) <= 1.0,
           f"{oc_sync.alignment_offsets} {oc_sync.summary()}")
    sync_lag = _best_lag(_decode_mono(sync_out, 0, tag="p5x"),
                         _decode_mono(sync_out, 1, tag="p5x"))
    record("p5.ext.T5 对齐后两路 PCM 内容重合 (lag = 0 ±2)",
           sync_lag is not None and abs(sync_lag) <= 2, f"lag={sync_lag}")
    record("p5.ext.T5 外挂 PCM 对齐后仍是 PCM 输出 + 视频 hash 不变",
           oc_sync.ok
           and all(a["codec_name"].startswith("pcm_")
                   for a in _audio_of(sync_out))
           and _video_elementary_hash(sync_out)
           == _video_elementary_hash(sync_dir / "clip.MP4"),
           f"{[a['codec_name'] for a in _audio_of(sync_out)]}")

    pick_dir = d / "pick"
    pick_dir.mkdir(parents=True, exist_ok=True)
    _make_av(pick_dir / "clip.MP4", 1, seconds=1)
    _make_audio_file(pick_dir / "clip_x.wav", 1, freq=800)
    request = parse_audio_request({
        "external": {},
        "encode": {"format": "pcm"},
    })
    plan = resolve_source_audio_plan(
        FFPROBE, pick_dir / "clip.MP4", request, source_id="source"
    )
    external_ids = [s.source_id for s in plan.sources if s.is_external]
    record("p5.ext.T5 外挂来源可与原视频声道一起被显式选择 (§39)",
           external_ids == ["clip_x.wav"]
           and "clip_x.wav:s0:c0" in plan.selected_channels
           and "source:s1:c0" in plan.selected_channels,
           f"{external_ids} {plan.selected_channels}")

    # --- T6: 视频基本流不变 ---------------------------------------------
    base_plan = resolve_source_audio_plan(
        FFPROBE, pick_dir / "clip.MP4",
        parse_audio_request({"channels": {"select": ["source:s1:c0"]}}),
        source_id="source",
    )
    base_out = d / "base_out.mp4"
    oc_base = produce_audio_output(
        plan=base_plan, video=VideoOutputArtifact(
            path=str(pick_dir / "clip.MP4")),
        output_path=base_out, ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "base_work", audio_source=pick_dir / "clip.MP4",
        request=parse_audio_request(
            {"channels": {"select": ["source:s1:c0"]}}),
    )
    oc_ext = produce_audio_output(
        plan=plan, video=VideoOutputArtifact(
            path=str(pick_dir / "clip.MP4")),
        output_path=d / "ext_out.mp4", ffmpeg=FFMPEG, ffprobe=FFPROBE,
        work_dir=d / "ext_work", audio_source=pick_dir / "clip.MP4",
        request=request,
    )
    base_hash = _video_elementary_hash(base_out) if base_out.is_file() else ""
    ext_hash = (_video_elementary_hash(d / "ext_out.mp4")
                if (d / "ext_out.mp4").is_file() else "")
    record("p5.ext.T6 加入外挂音频后视频基本流 sha256 不变",
           oc_base.ok and oc_ext.ok and bool(base_hash)
           and base_hash == ext_hash,
           f"base={base_hash[:16]} ext={ext_hash[:16]}")
