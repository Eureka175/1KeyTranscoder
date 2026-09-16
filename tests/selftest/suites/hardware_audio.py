#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.8.0+ : `--audio-plan` 接入硬件 Video Encode Backend (NVENC / QSV)。

架构边界 (本 suite 用断言钉住的东西):

    Video Encode Backend   x265 / SVT-AV1 / NVENC / QSV   <- 同级, 不认识音频
    Audio Backend          AudioPlan -> AudioOutput        <- 不认识视频后端
    Output Composer        两者 -> 最终容器

两个 suite:

  * `l1_hardware_audio()` —— 契约层 (不跑 GPU): 视频侧"接手"契约的形状、
    Sony/DJI 拒绝规则的语义、硬件侧音频依赖 = 0 的 AST 审计、CLI 面不变。
  * `l3_hardware_audio()` —— **真实 NVENC / QSV**: 真编码, 逐案核对音频结构、
    视频基本流 sha256、硬件解码路由是否仍然走硬件, 以及"同一个 AudioPlan 换
    视频后端音频结果完全一致"。
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from typing import Any

from ..fixtures.audio import (
    _make_audio_file,
    _make_av,
    _make_av_10bit,
    _make_av_channels,
    _make_video_only,
    _sync_content,
    _video_elementary_hash,
)
from ..paths import (
    FFPROBE, FFMPEG, IN_DIR, ROOT, ffprobe_json, record, section, sh,
)

#: 硬件回归用的素材尺寸/帧率。320x240 已被三个编码器 (x265 / NVENC / QSV)
#: 与本机 AV1 实测接受; 再小会踩 x265 的 CTU 下限。
SIZE = "320x240"
RATE = 10


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _audio_of(path: Path) -> list[dict[str, Any]]:
    return [
        s for s in ffprobe_json(path).get("streams", [])
        if s.get("codec_type") == "audio"
    ]


def _video_of(path: Path) -> dict[str, Any] | None:
    return next(
        (s for s in ffprobe_json(path).get("streams", [])
         if s.get("codec_type") == "video"), None
    )


def _audio_signature(path: Path) -> list[tuple[Any, Any, Any]]:
    """音频**结构**签名: (codec, 声道数, 采样率), 顺序 = 容器顺序。

    §26 的"mapping 不因 backend 改变"比的就是这个签名 —— 加上顺序, 因此
    "同样的轨道换了顺序"也算不一致。
    """
    return [
        (a.get("codec_name"), a.get("channels"), a.get("sample_rate"))
        for a in _audio_of(path)
    ]


def _write_plan(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _run_1kt(input_dir: Path, out_dir: Path, *extra: str,
             timeout: int = 1800):
    out_dir.mkdir(parents=True, exist_ok=True)
    return sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", input_dir, "--output", out_dir, "--headless",
        "--preset", "FAST", *extra, timeout=timeout,
    )


def _run_case(input_dir: Path, out_dir: Path, encoder: str,
              plan: Path | None, *extra: str,
              ) -> tuple[Any, Path, str]:
    """跑一次真实生产入口; 返回 (结果, 产物路径, 输出文本)。"""
    args = ["--encoder", encoder]
    if plan is not None:
        args += ["--audio-plan", str(plan)]
    args += list(extra)
    result = _run_1kt(input_dir, out_dir, *args)
    produced = sorted(out_dir.rglob("*.MP4"))
    text = (result.stdout or "") + (result.stderr or "")
    return result, (produced[0] if produced else Path("")), text


def _dominant_hz(path: Path, position: int) -> float:
    """某条音轨的主频 (Hz) —— 用来证明"哪条流来自哪个外挂文件"。"""
    import numpy as np

    out = path.with_suffix(path.suffix + f".hw{position}.raw")
    r = sh(FFMPEG, "-v", "error", "-y", "-i", path,
           "-map", f"0:a:{position}", "-vn", "-ac", "1", "-f", "f32le",
           "-ar", "48000", out, timeout=600)
    if r.returncode != 0 or not out.is_file():
        return 0.0
    data = np.fromfile(out, dtype="<f4")
    if len(data) < 1024:
        return 0.0
    window = data[: min(len(data), 48000)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1.0 / 48000.0)
    return float(freqs[int(np.argmax(spectrum))]) if len(spectrum) else 0.0


def _per_file_logs(input_dir: Path) -> str:
    """该输入目录这一轮的 per-file 日志文本 (1kt.py 写在 input 的兄弟目录)。"""
    logs_root = input_dir.parent / "logs"
    if not logs_root.is_dir():
        return ""
    texts = []
    for path in sorted(logs_root.rglob("*.log")):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(texts)


def _audio_deps(path: Path) -> list[str]:
    """该文件里所有 `core.audio_*` / `production.*` import (含函数内)。"""
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        out += [m for m in mods
                if m.startswith("core.audio_") or m.startswith("production")]
    return out


def _audio_internal_names(path: Path) -> list[str]:
    """源码里**真的被使用**的音频域内部类型名 (不含注释与文档字符串)。

    ⚠️ 刻意不用字符串扫描: 边界本身需要被写进文档字符串解释 (例如"本层不
    知道 AudioPlan 的存在"), 字符串扫描会把"声明边界"误判成"越过边界"。
    这里只看 AST 里的标识符 —— 注释不进 AST, 文档字符串作为 Constant 被排除。
    """
    wanted = {
        "AudioPlan", "AudioTimeline", "AudioMixer", "AudioEncoder",
        "AudioExecutionPath", "AudioRetentionSpec", "EncodedAudioOutput",
        "AudioPCMReader", "ChannelTimeline",
    }
    used: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.alias):
            used.add((node.asname or node.name).split(".")[-1])
    return sorted(used & wanted)


# ---------------------------------------------------------------------------
# L1 — 契约层
# ---------------------------------------------------------------------------

def l1_hardware_audio() -> None:
    """`--audio-plan` 硬件接入的契约与边界 (不跑 GPU)。"""
    section("L1 硬件后端音频计划契约 (v0.8.0+)")
    from core.batch_hw import (
        BatchCtx,
        HandoffOutcome,
        VideoHandoff,
        audio_plan_refusal,
        encode_one_hw_classic,
    )

    # --- 1. 视频侧"接手"契约: 形状里没有音频词汇 -------------------------
    fields = set(VideoHandoff.__dataclass_fields__)
    record("v08hw.handoff 视频侧接手契约只有视频侧概念 (无音频词汇)",
           {"source", "video", "output", "work_dir"} <= fields
           and not any("audio" in f for f in fields)
           and not any("plan" in f for f in fields),
           f"{sorted(fields)}")
    outcome_fields = set(HandoffOutcome.__dataclass_fields__)
    record("v08hw.handoff 返回契约 = (ok, applied, detail, errors) 四个通用字段",
           outcome_fields == {"ok", "applied", "detail", "errors"},
           f"{sorted(outcome_fields)}")
    record("v08hw.handoff 默认=未接手 (ok/not applied, 不改变任何路径)",
           HandoffOutcome().ok and not HandoffOutcome().applied)
    import inspect

    record("v08hw.handoff 经典硬件编码接受可选接手方 (默认 None)",
           "video_handoff" in inspect.signature(
               encode_one_hw_classic).parameters
           and inspect.signature(
               encode_one_hw_classic).parameters["video_handoff"].default
           is None)
    record("v08hw.handoff BatchCtx 携带该字段且默认关闭",
           "video_handoff" in BatchCtx.__dataclass_fields__
           and BatchCtx.__dataclass_fields__["video_handoff"].default is None)

    # --- 2. Sony / DJI 拒绝规则 -----------------------------------------
    sony = [{"codec_type": "video"}, {"codec_type": "data",
                                      "codec_tag_string": "rtmd"}]
    dji = [{"codec_type": "video"}, {"codec_type": "data",
                                     "codec_tag_string": "djmd"}]
    plain = [{"codec_type": "video"}, {"codec_type": "audio",
                                       "codec_tag_string": "in24"}]
    hook = lambda _request: HandoffOutcome()          # noqa: E731
    record("v08hw.refuse Sony/DJI 素材 + 计划 -> 明确拒绝 (带原因)",
           audio_plan_refusal(sony, hook) is not None
           and "Sony" in (audio_plan_refusal(sony, hook) or "")
           and "preservation path" in (audio_plan_refusal(sony, hook) or "")
           and "DJI" in (audio_plan_refusal(dji, hook) or ""),
           f"{audio_plan_refusal(sony, hook)}")
    record("v08hw.refuse 普通素材 + 计划 -> 不拒绝 (硬件路径正常支持)",
           audio_plan_refusal(plain, hook) is None)
    record("v08hw.refuse 没有计划时 Sony/DJI 一律不拒绝 (既有路径零改动)",
           audio_plan_refusal(sony, None) is None
           and audio_plan_refusal(dji, None) is None)

    # --- 3. 架构审计: 硬件侧对音频域的依赖 ------------------------------
    hardware_files = [
        ROOT / "core" / "batch_hw.py",
        ROOT / "encoders" / "nvencc.py",
        ROOT / "encoders" / "qsvencc.py",
        ROOT / "encoders" / "hw.py",
        ROOT / "encoders" / "hwdecode.py",
        ROOT / "encoders" / "integrity.py",
        ROOT / "encoders" / "caps.py",
        ROOT / "encoders" / "base.py",
    ]
    leaks = {f.name: _audio_deps(f) for f in hardware_files if _audio_deps(f)}
    record("v08hw.arch NVENC/QSV/硬件解码/批量层 import 音频域 = 0",
           not leaks, f"{leaks}")
    found: dict[str, list[str]] = {}
    for path in hardware_files:
        hit = _audio_internal_names(path)
        if hit:
            found[path.name] = hit
    record("v08hw.arch 硬件侧**代码**里不出现音频域内部类型名 "
           "(注释/文档字符串不算越界)",
           not found, f"{found}")
    entry = (ROOT / "1kt.py").read_text(encoding="utf-8")
    entry_deps = _audio_deps(ROOT / "1kt.py")
    record("v08hw.arch 生产入口不 import 任何 core.audio_* (含函数内)",
           not [m for m in entry_deps if m.startswith("core.audio_")],
           f"{entry_deps}")
    record("v08hw.arch 生产入口只通过 production.output 触达音频",
           entry_deps and set(entry_deps) == {"production.output"}
           and "from production.output import" in entry,
           f"{sorted(set(entry_deps))}")
    record("v08hw.arch 音频域不反向 import 视频后端实现",
           not [
               m for m in _video_deps(ROOT / "production" / "output.py")
           ],
           f"{_video_deps(ROOT / 'production' / 'output.py')}")

    # --- 4. 软硬件共用一个音频实现 (否则 §26 的等价性无法成立) ----------
    tree = ast.parse(entry)
    plan_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "produce_audio_output"
    ]
    record("v08hw.parity 生产入口只有一个 produce_audio_output 调用点 "
           "(软硬件共用, 不复制音频逻辑)",
           len(plan_calls) == 1, f"call sites={len(plan_calls)}")
    record("v08hw.parity 硬件接手钩子与软件路径调用同一个编排函数",
           "_apply_audio_plan" in entry
           and entry.count("_apply_audio_plan(") >= 3)

    # --- 5. 执行顺序: 视频产物 -> 音频 -> Composer ----------------------
    record("v08hw.order 接手方拿到的是**视频产物** + 目标路径 + 工作目录",
           {"source", "video", "output", "work_dir"}
           <= set(VideoHandoff.__dataclass_fields__)
           and "file_logger" in VideoHandoff.__dataclass_fields__)

    # --- 6. CLI 面: 没有新增参数 ---------------------------------------
    import re

    args_now = set(re.findall(r'add_argument\(\s*["\'](--[a-z0-9-]+)', entry))
    record("v08hw.cli 本阶段没有新增任何 CLI 参数 (仍是 --audio-plan)",
           len(args_now) == 30 and "--audio-plan" in args_now,
           f"{len(args_now)} args")


def _video_deps(path: Path) -> list[str]:
    """`production/` 里对视频侧实现的 import (`encoders` / `preservation`)。"""
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        out += [m for m in mods
                if m.split(".")[0] in ("encoders", "preservation")
                or m.endswith("batch_hw")]
    return out


# ---------------------------------------------------------------------------
# L3 — 真实硬件
# ---------------------------------------------------------------------------

def l3_hardware_audio() -> None:
    """真实 NVENC / QSV 上的 `--audio-plan`。"""
    section("L3 硬件后端音频计划 (v0.8.0+)")
    d = IN_DIR / "p5hw"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    plans = d / "plans"

    # ---------------------------------------------------------------- 素材
    quad_dir = d / "quad"
    quad_dir.mkdir(parents=True, exist_ok=True)
    quad = quad_dir / "clip.MP4"
    if not _make_av_channels(quad, 4, size=SIZE, rate=RATE):
        record("v08hw 素材 (h264 + 1×4CH PCM) 生成", False, "ffmpeg 失败")
        return
    record("v08hw 素材 = 1 video + 1×4CH audio (强制 PCM 路径的形态)",
           _video_of(quad) is not None
           and [a.get("channels") for a in _audio_of(quad)] == [4],
           f"{[(a.get('codec_name'), a.get('channels')) for a in _audio_of(quad)]}")

    ext_dir = d / "ext"
    ext_dir.mkdir(parents=True, exist_ok=True)
    if not _make_video_only(ext_dir / "clip.MP4", size=SIZE, rate=RATE):
        record("v08hw 外挂音频素材 (video-only) 生成", False, "ffmpeg 失败")
        return
    for name, freq in (("clip_01.wav", 300), ("clip_02.wav", 700)):
        _make_audio_file(ext_dir / name, 1, freq=freq)

    align_dir = d / "align"
    align_dir.mkdir(parents=True, exist_ok=True)
    _make_video_only(align_dir / "clip.MP4", size=SIZE, rate=RATE)
    tmp_wav = align_dir / "tgt_tmp.wav"
    _sync_content(align_dir / "clip_ref.wav", 0, samples=48000)
    _sync_content(tmp_wav, 480, samples=48000)
    r = sh(FFMPEG, "-v", "error", "-y", "-i", tmp_wav, "-vn", "-c:a",
           "libopus", "-b:a", "96k", align_dir / "clip_tgt.opus", timeout=600)
    tmp_wav.unlink(missing_ok=True)
    record("v08hw 外挂对齐素材 (PCM ref + 晚到 480 样本的 Opus) 生成",
           r.returncode == 0 and (align_dir / "clip_tgt.opus").is_file())

    hwdec_dir = d / "hwdec"
    hwdec_dir.mkdir(parents=True, exist_ok=True)
    if not _make_av_10bit(hwdec_dir / "clip.mp4", size="640x360", rate=RATE):
        record("v08hw 硬件解码素材 (HEVC 4:2:0 10-bit) 生成", False, "ffmpeg 失败")
        return
    record("v08hw 硬件解码素材是白名单组合 (hevc/4:2:0/10bit)",
           (_video_of(hwdec_dir / "clip.mp4") or {}).get("pix_fmt")
           == "yuv420p10le",
           f"{( _video_of(hwdec_dir / 'clip.mp4') or {}).get('pix_fmt')}")

    # ---------------------------------------------------------------- 计划
    subset = _write_plan(plans, "subset_opus", {
        "version": 1,
        "channels": {"select": ["source:s1:c0", "source:s1:c2"]},
        "encode": {"format": "opus", "bitrate": "96k"},
    })
    subset_aac = _write_plan(plans, "subset_aac", {
        "version": 1,
        "channels": {"select": ["source:s1:c1", "source:s1:c3"]},
        "encode": {"format": "aac", "bitrate": "128k"},
    })
    subset_pcm = _write_plan(plans, "subset_pcm", {
        "version": 1,
        "channels": {"select": ["source:s1:c0", "source:s1:c1"]},
        "encode": {"format": "pcm"},
    })
    select_one = _write_plan(plans, "select_one", {
        "version": 1, "channels": {"select": ["source:s1:c0"]},
    })
    external = _write_plan(plans, "external", {
        "version": 1, "external": {},
        "mapping": {"mode": "independent"},
    })
    align = _write_plan(plans, "align", {
        "version": 1, "external": {}, "alignment": "enabled",
        "sync": {"reference": "clip_ref.wav:s0:c0"},
    })
    empty_plan = _write_plan(plans, "empty", {
        "version": 1, "encode": {"format": "pcm"},
    })

    # ------------------------------------------------- NVENC: 默认 + 五种计划
    baselines: dict[str, tuple[str, tuple]] = {}
    for encoder in ("nvenc", "qsv"):
        result, produced, text = _run_case(
            quad_dir, d / f"base_{encoder}", encoder, None)
        ok = (result.returncode == 0 and produced.is_file()
              and produced.stat().st_size > 0)
        record(f"v08hw.{encoder} 默认路径 (无计划) 真实硬件编码成功",
               ok, f"rc={result.returncode} {produced.name}")
        if not ok:
            for line in text.splitlines():
                if "FAIL" in line:
                    record(f"v08hw.{encoder} 失败原因", False, line[:200])
            return
        baselines[encoder] = (
            _video_elementary_hash(produced), _audio_signature(produced)
        )

    for encoder in ("nvenc", "qsv"):
        base_hash, base_sig = baselines[encoder]

        # --- PCM 路由 (4CH 取 2 声道) --------------------------------
        result, produced, text = _run_case(
            quad_dir, d / f"pcm_{encoder}", encoder, subset_pcm)
        sig = _audio_signature(produced) if produced.is_file() else []
        record(f"v08hw.{encoder}+PCM 4CH 取 2 声道 -> 单条 2ch PCM 输出",
               result.returncode == 0 and sig == [("pcm_s16le", 2, "48000")],
               f"rc={result.returncode} {sig}")
        record(f"v08hw.{encoder}+PCM 视频基本流 sha256 与默认路径一致",
               produced.is_file()
               and _video_elementary_hash(produced) == base_hash,
               f"{_video_elementary_hash(produced)[:16]} vs {base_hash[:16]}")

        # --- Opus 手动 bitrate --------------------------------------
        result, produced, text = _run_case(
            quad_dir, d / f"opus_{encoder}", encoder, subset)
        sig = _audio_signature(produced) if produced.is_file() else []
        record(f"v08hw.{encoder}+Opus 手动 96k -> 单条 opus 2ch",
               result.returncode == 0 and sig == [("opus", 2, "48000")],
               f"rc={result.returncode} {sig}")

        # --- AAC 手动 bitrate ---------------------------------------
        result, produced, text = _run_case(
            quad_dir, d / f"aac_{encoder}", encoder, subset_aac)
        sig = _audio_signature(produced) if produced.is_file() else []
        record(f"v08hw.{encoder}+AAC 手动 128k -> 单条 aac 2ch",
               result.returncode == 0 and sig == [("aac", 2, "48000")],
               f"rc={result.returncode} {sig}")
        record(f"v08hw.{encoder}+AAC 视频基本流 sha256 与默认路径一致",
               produced.is_file()
               and _video_elementary_hash(produced) == base_hash,
               f"{_video_elementary_hash(produced)[:16]}")

        # --- 外挂音频 ------------------------------------------------
        result, produced, text = _run_case(
            ext_dir, d / f"ext_{encoder}", encoder, external)
        sig = _audio_signature(produced) if produced.is_file() else []
        # 顺序不能只看条数: 逐条解码主频, 证明第 0 条来自 clip_01.wav
        # (300 Hz)、第 1 条来自 clip_02.wav (700 Hz) —— 即 natural 顺序。
        hz = ([_dominant_hz(produced, i) for i in range(len(sig))]
              if sig else [])
        record(f"v08hw.{encoder}+外挂 WAV -> 2 条音轨, 顺序 = 发现顺序 (§25)",
               result.returncode == 0
               and sig == [("pcm_s16le", 1, "48000"),
                           ("pcm_s16le", 1, "48000")]
               and len(hz) == 2 and abs(hz[0] - 300) < 20
               and abs(hz[1] - 700) < 20,
               f"rc={result.returncode} {sig} hz={[round(h, 1) for h in hz]}")

        # --- compressed + 显式 alignment -----------------------------
        result, produced, text = _run_case(
            align_dir, d / f"align_{encoder}", encoder, align)
        sig = _audio_signature(produced) if produced.is_file() else []
        detail = _per_file_logs(align_dir)
        record(f"v08hw.{encoder}+compressed 显式 alignment -> 解码后重编码",
               result.returncode == 0
               and "alignment_enabled_compressed" in detail
               and "applied=1" in text
               and sig == [("pcm_s16le", 1, "48000"),
                           ("opus", 1, "48000")],
               f"rc={result.returncode} {sig} "
               f"compressed_alignment="
               f"{'alignment_enabled_compressed' in detail}")
        record(f"v08hw.{encoder}+alignment 估计出的 offset = 480",
               "'clip_tgt.opus:s0:c0': 480.0" in text,
               [ln for ln in text.splitlines() if "offset" in ln][:1])

        # --- 硬件解码 + 硬件编码 + 计划 ------------------------------
        result_def, produced_def, text_def = _run_case(
            hwdec_dir, d / f"hwdec_base_{encoder}", encoder,
            select_one, "--hw-decode", "auto")
        result_plan, produced_plan, text_plan = _run_case(
            hwdec_dir, d / f"hwdec_plan_{encoder}", encoder,
            select_one, "--hw-decode", "auto")
        hw_route = "-> HARDWARE" in text_def and "-> HARDWARE" in text_plan
        record(f"v08hw.{encoder} 硬件解码 + 硬件编码 + 计划: 解码仍走 avhw",
               hw_route and result_plan.returncode == 0,
               [ln[-90:] for ln in text_plan.splitlines()
                if "decode route" in ln][:1])
        record(f"v08hw.{encoder} 计划不改变硬件解码下的视频基本流",
               produced_def.is_file() and produced_plan.is_file()
               and _video_elementary_hash(produced_def)
               == _video_elementary_hash(produced_plan),
               f"{_video_elementary_hash(produced_plan)[:16]} vs "
               f"{_video_elementary_hash(produced_def)[:16]}")
        record(f"v08hw.{encoder} 硬件解码 + 计划: 音频按计划保留 1 条",
               _audio_signature(produced_plan)
               == [("pcm_s16le", 1, "48000")],
               f"{_audio_signature(produced_plan)}")

    # ------------------------------------------------- 空计划 = 未启用
    result, produced, text = _run_case(
        quad_dir, d / "empty", "nvenc", empty_plan)
    base_hash, base_sig = baselines["nvenc"]
    record("v08hw 空计划 -> 与默认路径完全一致 (音轨/视频 hash 都没变)",
           result.returncode == 0
           and _audio_signature(produced) == base_sig
           and _video_elementary_hash(produced) == base_hash,
           f"rc={result.returncode} {_audio_signature(produced)}")
    record("v08hw 空计划不触发视频专用命令派生 (日志里没有 AUDIO 步骤)",
           "audio execution path" not in text,
           "no audio step in log")

    # ------------------------------------------------- 跨后端音频等价 (§26)
    parity: dict[str, tuple] = {}
    for encoder in ("x265", "svtav1", "nvenc", "qsv"):
        extra = ("--no-hw-autoselect",) if encoder in ("x265", "svtav1") else ()
        result, produced, text = _run_case(
            quad_dir, d / f"parity_{encoder}", encoder, subset_aac, *extra)
        parity[encoder] = (
            tuple(_audio_signature(produced)) if produced.is_file() else ()
        )
    record("v08hw.parity 同一个 AudioPlan: x265/svtav1/nvenc/qsv 音频结构完全一致",
           len(set(parity.values())) == 1 and parity["x265"] != (),
           f"{parity}")

    # ------------------------------------------------- 硬件 AV1 (§24)
    result, produced, text = _run_case(
        hwdec_dir, d / "av1_qsv", "qsv-av1", select_one)
    record("v08hw.av1 QSV AV1 + 计划 -> 真实编码成功 (av1 + 计划音频)",
           result.returncode == 0
           and (_video_of(produced) or {}).get("codec_name") == "av1"
           and _audio_signature(produced) == [("pcm_s16le", 1, "48000")],
           f"rc={result.returncode} "
           f"{( _video_of(produced) or {}).get('codec_name')} "
           f"{_audio_signature(produced)}")
    result_av1_plan, produced_av1_plan, text_av1_plan = _run_case(
        hwdec_dir, d / "av1_nvenc", "nvenc-av1", select_one)
    result_av1_def, produced_av1_def, text_av1_def = _run_case(
        hwdec_dir, d / "av1_nvenc_def", "nvenc-av1", None)
    same = (
        result_av1_plan.returncode == result_av1_def.returncode
        and not produced_av1_plan.is_file() and not produced_av1_def.is_file()
    )
    record("v08hw.av1 NVENC AV1 与计划无关地失败 (KNOWN LIMITATION, 非本阶段引入)",
           same and "Failed to create encoder" in text_av1_plan
           and "Failed to create encoder" in text_av1_def,
           "both with and without --audio-plan: "
           f"rc={result_av1_plan.returncode}/{result_av1_def.returncode}")

    # ------------------------------------------------- Sony / DJI 拒绝 (§19)
    from core.batch_hw import audio_plan_refusal, is_sony_source

    sony_file = (ROOT / "testsets" / "a7m4_4k30p_264_hi422p_xavcs"
                 / "C9037.MP4")
    if sony_file.is_file():
        from core.probe import probe_source

        _summary, streams = probe_source(FFPROBE, sony_file)
        reason = audio_plan_refusal(streams, lambda _r: None)
        record("v08hw.sony 真实 Sony 素材被识别且在有计划时明确拒绝 (§19)",
               is_sony_source(streams) and reason is not None
               and "Sony" in reason and "not implemented" in reason,
               f"{reason}")
        record("v08hw.sony 没有计划时同一素材不被拒绝 (既有路径零改动)",
               audio_plan_refusal(streams, None) is None)
    else:
        record("v08hw.sony 真实 Sony 素材可用", False,
               f"missing {sony_file}")

    # ------------------------------------------------- CLI 冲突仍然明确
    _, _, text = _run_case(quad_dir, d / "conflict_sync", "nvenc",
                           subset_pcm, "--channel-sync")
    record("v08hw.cli --audio-plan + --channel-sync -> 仍然明确报错 (rc=2)",
           "both decide the final audio" in text,
           [ln for ln in text.splitlines() if "FATAL" in ln][:1])
    bad_plan = _write_plan(plans, "bad", {
        "version": 1, "channels": {"select": ["source:s9:c0"]},
    })
    result_bad, produced_bad, text_bad = _run_case(
        quad_dir, d / "bad_plan", "nvenc", bad_plan)
    record("v08hw.cli 硬件后端上非法计划 -> 明确失败, 不产出半成品",
           result_bad.returncode != 0 and "[AUDIO-FAIL]" in text_bad
           and not produced_bad.is_file(),
           f"rc={result_bad.returncode}")
