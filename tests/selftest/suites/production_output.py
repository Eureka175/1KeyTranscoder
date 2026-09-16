#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4C: 音频输出接入生产入口 (CLI + 编排)。

两个 suite:

  * `l1_production_output()` —— 纯声明/契约层: `--audio-plan` 请求解析、
    "视频专用命令派生"、编排结果的 `applied` 语义、架构审计 (编排层不得
    依赖音频内部结构)。不运行 ffmpeg。
  * `l3_production_output()` —— **真实生产入口**: 直接跑 `1kt.py`
    (x265 经典软件路径), 分别验证默认路径零回归、整流保留、PCM route、
    PCM mix、以及 arbitrary-reference sync 走完整链。视频基本流 sha256
    在每种音频处理下都必须与默认路径一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..fixtures.audio import _make_av, _make_av_channels
from ..paths import (
    FFPROBE, FFMPEG, IN_DIR, ROOT, ffprobe_json, record, section, sh,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run_1kt(input_dir: Path, *extra: str, timeout: int = 1800):
    """跑真实生产入口 `1kt.py` (与既有 L3 用例同一方式)。"""
    return sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", input_dir, *extra, "--headless", timeout=timeout,
    )


def _video_hash(path: Path) -> str:
    """视频**基本流** sha256 —— 与 Phase 4B 回归同一口径。"""
    r = sh(FFMPEG, "-v", "error", "-i", path, "-map", "0:v:0",
           "-c", "copy", "-f", "hash", "-hash", "sha256", "-", timeout=600)
    if r.returncode != 0:
        return ""
    text = (r.stdout or "").strip()
    return text.split("=")[-1] if "=" in text else text


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


def _write_plan(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _decode_mono(path: Path, position: int, frames: int = 0) -> Any:
    import numpy as np

    out = path.with_suffix(path.suffix + f".p4c{position}.raw")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = sh(FFMPEG, "-v", "error", "-y", "-i", path,
           "-map", f"0:a:{position}", "-vn", "-ac", "1",
           "-f", "f32le", "-ar", "48000", out, timeout=600)
    if r.returncode != 0 or not out.is_file():
        return None
    data = np.fromfile(out, dtype="<f4")
    return data[:frames] if frames else data


def _dominant_hz(data: Any, sample_rate: int = 48000) -> float:
    import numpy as np

    if data is None or len(data) < 1024:
        return 0.0
    window = data[: min(len(data), sample_rate)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1.0 / sample_rate)
    return float(freqs[int(np.argmax(spectrum))]) if len(spectrum) else 0.0


# ---------------------------------------------------------------------------
# L1 — 契约层
# ---------------------------------------------------------------------------

def l1_production_output() -> None:
    """Phase 4C: --audio-plan 请求 + 视频专用命令派生 + applied 语义。"""
    section("L1 音频输出生产接入契约 (Phase 4C)")
    from core.audio_encode import AudioEncodeFormat
    from core.audio_models import AudioPlan
    from core.audio_request import (
        REASON_AUDIO_REQUEST_INVALID,
        REASON_AUDIO_REQUEST_VERSION,
        AudioRequestError,
        build_audio_plan,
        describe_audio_request,
        parse_audio_request,
    )
    from core.audio_plan import AudioPlanner
    from production.output import (
        derive_video_only_command,
        produce_audio_output,
    )

    # --- 1. 视频专用命令派生: 只去掉音频, 编码参数一字不动 ---------------
    x265_cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-stats", "-y", "-i", "in.MP4",
        "-map", "0", "-map_metadata", "0", "-map_chapters", "0",
        "-c:v:0", "libx265", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p10le",
        "-c:v:1", "copy",
        "-c:a", "copy", "-c:s", "copy", "-c:d", "copy", "-c:t", "copy",
        "-fps_mode", "passthrough", "-x265-params", "a=b",
        "-movflags", "+use_metadata_tags", "out.part.mov",
    ]
    derived = derive_video_only_command(x265_cmd)
    record("p4c.derive 派生出 -an (视频产物必须独立于音频)",
           "-an" in derived, f"{derived[-4:]}")
    record("p4c.derive 去掉全部音频/字幕/数据/附件 codec token",
           not any(t in derived for t in (
               "-c:a", "-c:s", "-c:d", "-c:t")),
           f"{[t for t in derived if t.startswith('-c:')]}")
    record("p4c.derive 去掉 -map 0 (不再全流映射)",
           "-map" not in derived
           or derived[derived.index("-map") + 1] != "0")
    record("p4c.derive 视频编码参数逐项不变 (bitstream 因此不变)",
           all(t in derived for t in (
               "-c:v:0", "libx265", "-preset", "slow", "-crf", "18",
               "-pix_fmt", "yuv420p10le", "-c:v:1", "copy",
               "-x265-params", "a=b", "-fps_mode", "passthrough",
               "-map_metadata", "0", "-map_chapters", "0",
           )),
           "video tokens preserved")
    record("p4c.derive 输出路径仍在最后 (未被打乱)",
           derived[-1] == "out.part.mov" and derived[-2] == "-an")

    svt_cmd = [
        "ffmpeg", "-y", "-i", "in.MP4", "-map", "0",
        "-c:v:0", "libsvtav1", "-c:a", "copy", "-c:s", "copy",
        "-svtav1-params", "x=y", "out.part.mp4",
    ]
    record("p4c.derive svtav1 命令同样可派生",
           derive_video_only_command(svt_cmd) == [
               "ffmpeg", "-y", "-i", "in.MP4", "-c:v:0", "libsvtav1",
               "-svtav1-params", "x=y", "-an", "out.part.mp4",
           ],
           f"{derive_video_only_command(svt_cmd)}")

    # 已经是视频专用的命令 (显式 -map 0:v:0) 必须原样返回
    already = [
        "ffmpeg", "-y", "-i", "in.MP4", "-map", "0:v:0", "-c:v", "libx265",
        "-an", "-sn", "-dn", "out.mov",
    ]
    record("p4c.derive 已是视频专用的命令原样返回 (幂等)",
           derive_video_only_command(already) == already)

    # --- 2. 请求解析: 未知键/版本/map 一致性都明确报错 -------------------
    record("p4c.request 空计划 -> is_empty (不改动选择)",
           parse_audio_request({"version": 1}).is_empty)
    record("p4c.request 默认 encode = aac",
           parse_audio_request({}).encode.format is AudioEncodeFormat.AAC)

    def _reason(payload: dict) -> str:
        try:
            parse_audio_request(payload)
        except AudioRequestError as exc:
            return exc.reason
        return ""

    record("p4c.request 未知键 -> audio_request_invalid (不静默忽略)",
           _reason({"unknown": 1}) == REASON_AUDIO_REQUEST_INVALID)
    record("p4c.request 未知版本 -> audio_request_version",
           _reason({"version": 99}) == REASON_AUDIO_REQUEST_VERSION)
    record("p4c.request 未知 encode 键 -> 报错",
           _reason({"encode": {"loudness": True}})
           == REASON_AUDIO_REQUEST_INVALID)
    record("p4c.request 未知 format -> 报错 (不是静默回退)",
           # ⚠️ v0.8.0: `opus` 现在是**合法**格式 (§9 要求至少 AAC/Opus),
           # 因此这条用例换成真正不在表里的 codec —— 断言强度不变, 只是
           # 被否定的取值从"当时不支持"变成"始终不在表里"。
           _reason({"encode": {"format": "mp3"}})
           == REASON_AUDIO_REQUEST_INVALID)
    record("p4c.request opus 成为合法格式 (v0.8.0)",
           parse_audio_request(
               {"encode": {"format": "opus", "bitrate": "96k"}}
           ).encode.format is AudioEncodeFormat.OPUS)
    record("p4c.request map 与 select 集合不一致 -> 报错",
           _reason({"channels": {"select": ["a"], "map": ["b"]}})
           == "audio_request_selection")
    record("p4c.request map 缺 select -> 报错 (map 只排序, 不增删)",
           _reason({"channels": {"map": ["a"]}})
           == "audio_request_selection")
    record("p4c.request map 有重复 -> 报错",
           _reason({"channels": {"select": ["a", "b"],
                                 "map": ["a", "a"]}})
           == "audio_request_selection")

    ok_req = parse_audio_request({
        "version": 1,
        "encode": {"format": "flac"},
        "channels": {"select": ["source:s1:c0", "source:s2:c0"],
                     "map": ["source:s2:c0", "source:s1:c0"]},
    })
    record("p4c.request 合法请求解析成功 (select + map 同集合)",
           ok_req.encode.format is AudioEncodeFormat.FLAC
           and ok_req.map == ["source:s2:c0", "source:s1:c0"]
           and not ok_req.is_empty,
           describe_audio_request(ok_req))

    # --- 3. 请求 -> AudioPlan: 用的是**既有**模型, 且空请求不改动 ---------
    record("p4c.request 空请求 -> 计划仍是默认计划 (NONE 的机制保证)",
           build_audio_plan(
               parse_audio_request({"version": 1}), AudioPlan()
           ).is_default)

    # --- 4. 编排结果语义: applied=False 只有一个合法含义 ----------------
    outcome = produce_audio_output(
        plan=None,
        video=__import__(
            "core.output_compose", fromlist=["VideoOutputArtifact"]
        ).VideoOutputArtifact(path="x"),
        output_path="y", ffmpeg=FFMPEG,
    )
    record("p4c.outcome plan=None -> ok 且 applied=False 且不碰文件",
           outcome.ok and not outcome.applied
           and outcome.path.value == "none"
           and outcome.encoded == []
           and outcome.retention is None,
           outcome.summary())

    # --- 5. 架构审计: 编排层只依赖 contract ------------------------------
    import ast as _ast

    import production.output as _po

    # 编排层**允许**依赖的公开 API: `core.audio_models` / `core.audio_plan`
    # (模型与计划) / `core.audio_probe` (从源文件产出计划) /
    # `core.audio_request` (用户意图 -> 计划) / `core.audio_execution`
    # (执行图判定) / `core.audio_encode` (编码产物契约) /
    # `core.audio_retention` (整流保留规格) / `core.output_compose` (编排)。
    #
    # **禁止**: DSP 内部实现 (`audio_mix` / `audio_pcm` / `audio_route` /
    # `audio_timeline` / `audio_wav`) 与视频侧实现 (`encoders` /
    # `preservation` / `batch_hw`) —— 编排层不得下沉到任何一侧的内部。
    forbidden = {
        "audio_mix", "audio_pcm", "audio_timeline", "audio_route",
        "audio_wav", "encoders", "preservation", "batch_hw",
        "hwdecode", "integrity",
    }
    tree = _ast.parse(open(_po.__file__, encoding="utf-8").read())
    leaks: list[str] = []
    for node in _ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, _ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            parts = set(mod.split("."))
            if parts & forbidden:
                leaks.append(mod)
    record("p4c.arch 编排层不依赖音频内部结构 / 视频内部结构",
           not leaks, f"{leaks}")
    source = open(_po.__file__, encoding="utf-8").read()
    record("p4c.arch 编排层不 import AudioMixer / AudioPCMReader / 编码器",
           all(tok not in source for tok in (
               "from core.audio_mix", "from core.audio_pcm",
               "from core.audio_route", "from core.audio_timeline",
               "from encoders", "from preservation",
           )),
           "import audit")
    record("p4c.arch 编排层公开 API 不含视频编码参数",
           not (_public_args(_po.__file__) & {
               "crf", "preset", "pix_fmt", "fps", "resolution",
               "video_codec",
           }),
           f"{sorted(_public_args(_po.__file__))}")

    # --- 6. 生产入口不得碰音频域内部 -------------------------------------
    # `1kt.py` 只允许通过 `production.output` 触达音频; 一旦它开始直接
    # import `core.audio_*`, 音频域就重新耦合进视频侧入口了。注意要连
    # **函数内 import** 一起查 (它们同样是耦合)。
    entry = ROOT / "1kt.py"
    entry_tree = _ast.parse(open(entry, encoding="utf-8").read())
    entry_audio: list[str] = []
    for node in _ast.walk(entry_tree):
        mods: list[str] = []
        if isinstance(node, _ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("core.audio_"):
                entry_audio.append(mod)
    record("p4c.arch 1kt.py 不 import 任何 core.audio_* (含函数内 import)",
           not entry_audio, f"{entry_audio}")

    entry_source = open(entry, encoding="utf-8").read()
    # ⚠️ v0.8.0+ : 这里由"字符串扫描"改为 **AST 标识符扫描**。
    # 原因: 边界本身需要被写进文档字符串解释 (例如 `_apply_audio_plan` 的
    # "生产入口不认识 AudioPlan / AudioEncoder, 只转交一个不透明的请求对象"),
    # 而字符串扫描会把"声明边界"误判成"越过边界"。新判据更强也更准:
    # 只看代码里真正被引用的名字 (Name / Attribute / import alias),
    # 注释与文档字符串 (Constant) 不计入 —— 因此"真的用了内部类型"依然会被
    # 抓到, 而"写明不使用"不会被误伤。
    used = _audio_internal_identifiers(entry_source)
    record("p4c.arch 1kt.py 不出现音频域内部类型名 (代码层面, 非注释)",
           not used, f"{used}")
    record("p4c.arch 1kt.py 只通过 production.output 触达音频",
           "from production.output" in entry_source,
           "entry imports production.output")

    # --- 7. 使用文档里的 JSON 示例必须是**真 schema** ---------------------
    # `docs/audio_plan.md` 是给用户看的; 里面的每个 JSON 块都能被真实的请求
    # 解析器解析, 才算"来自当前实际 schema"而不是文档作者想象的字段。
    doc = ROOT / "docs" / "audio_plan.md"
    if doc.is_file():
        blocks = _json_blocks(doc.read_text(encoding="utf-8"))
        bad: list[str] = []
        for index, block in enumerate(blocks):
            try:
                parse_audio_request(json.loads(block))
            except Exception as exc:                 # noqa: BLE001
                bad.append(f"#{index}: {exc}")
        record("p4c.doc docs/audio_plan.md 的 JSON 示例全部通过真实 schema 解析",
               bool(blocks) and not bad,
               f"{len(blocks)} blocks, bad={bad}")
        record("p4c.doc 文档覆盖了全部四个可选键 + 选择三件套",
               all(key in doc.read_text(encoding="utf-8")
                   for key in ('"select"', '"exclude"', '"map"',
                               '"alignment"', '"sync"', '"mapping"',
                               '"external"', '"encode"')),
               "keys documented")


def _json_blocks(source: str) -> list[str]:
    """从 Markdown 里取出所有 ```json 代码块 (文档契约测试用)。"""
    blocks: list[str] = []
    inside = False
    current: list[str] = []
    for line in source.splitlines():
        if line.strip().startswith("```"):
            if inside:
                blocks.append("\n".join(current))
                current = []
                inside = False
                continue
            inside = line.strip().lower() in ("```json", "```jsonc")
            continue
        if inside:
            current.append(line)
    return blocks


def _audio_internal_identifiers(source: str) -> list[str]:
    """源码里被引用的音频域内部类型名 (AST 标识符, 排除注释/文档字符串)。"""
    import ast as _ast_mod

    wanted = {
        "AudioMixer", "AudioPCMReader", "ChannelTimeline",
        "AudioRetentionSpec", "AudioExecutionPath", "EncodedAudioOutput",
        "AudioEncoder", "AudioPlan", "AudioTimeline",
    }
    used: set[str] = set()
    for node in _ast_mod.walk(_ast_mod.parse(source)):
        if isinstance(node, _ast_mod.Name):
            used.add(node.id)
        elif isinstance(node, _ast_mod.Attribute):
            used.add(node.attr)
        elif isinstance(node, _ast_mod.alias):
            used.add((node.asname or node.name).split(".")[-1])
    return sorted(used & wanted)


def _public_args(path: str) -> set[str]:
    """模块内所有函数/方法的公开参数名 (AST, 不是字符串扫描)。"""
    import ast

    args: set[str] = set()
    for node in ast.parse(open(path, encoding="utf-8").read()).body:
        if isinstance(node, ast.FunctionDef):
            args.update(
                a.arg for a in node.args.args + node.args.kwonlyargs
            )
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef):
                    args.update(
                        a.arg for a in sub.args.args + sub.args.kwonlyargs
                    )
    return args


# ---------------------------------------------------------------------------
# L3 — 真实生产入口
# ---------------------------------------------------------------------------

def l3_production_output() -> None:
    """Phase 4C: 真实 `1kt.py` 生产入口 (x265) + 四种音频处理。"""
    section("L3 音频输出生产接入 (Phase 4C)")
    from core.audio_probe import audio_probe_from_file
    from core.audio_request import build_audio_plan, parse_audio_request
    from core.audio_execution import resolve_audio_execution_path

    d = IN_DIR / "p4c_production"
    # ⚠️ 每次重建: 生产入口会**递归**扫描输入目录, 上一轮残留的素材会污染
    # 本轮的批次结果 (实测: 残留的 64x36 小图让 x265 报 "Picture size must
    # be at least one CTU", 于是整批 rc=1, 尽管本轮目标文件其实成功了)。
    import shutil as _shutil_clean

    _shutil_clean.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    src_dir = d / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "clip.mp4"
    if not _make_av(src, 4, seconds=1):
        record("l3.p4c.真实素材 (h264 + 4×mono PCM) 生成", False,
               "ffmpeg 合成失败")
        return
    # x265 编码需要可解码视频; 4 条 mono 音频用于音频侧判定
    record("l3.p4c.素材 = 1 video + 4 audio",
           _video_of(src) is not None and len(_audio_of(src)) == 4,
           f"{[(s.get('index'), s.get('codec_name')) for s in _audio_of(src)]}")

    # --- 探测事实 -> 声道身份 (计划文件里就用这些字符串) -----------------
    probe = audio_probe_from_file(FFPROBE, src, source_id="source")
    channel_ids = [c.id for c in probe.plan().all_channels()]
    record("l3.p4c.生产探测给出稳定声道身份 (计划文件可写)",
           channel_ids == [f"source:s{i}:c0" for i in (1, 2, 3, 4)],
           f"{channel_ids}")

    plans_dir = d / "plans"
    keep_two = _write_plan(plans_dir / "keep_two.json", {
        "version": 1,
        "channels": {"select": [channel_ids[2], channel_ids[0]]},
        "note": "P4C stream-copy retention (drop 2, reorder)",
    })
    route_two = _write_plan(plans_dir / "route_two.json", {
        "version": 1,
        "channels": {"select": [channel_ids[0], channel_ids[1]]},
        "encode": {"format": "pcm"},
    })
    mix_two = _write_plan(plans_dir / "mix_two.json", {
        "version": 1,
        "channels": {"select": [channel_ids[0], channel_ids[1]]},
        "encode": {"format": "pcm"},
        "note": "P4C mix (AudioPlan.mix_mode set below via planner)",
    })
    sync_plan = _write_plan(plans_dir / "sync.json", {
        "version": 1,
        "channels": {"select": channel_ids},
        "encode": {"format": "pcm"},
    })

    def _run_case(name: str, plan_path: Path | None) -> tuple[Path, Any]:
        """跑一次生产入口; 返回 (产物, 该文件的成功证据)。

        ⚠️ 断言**不**绑整批 rc: 1kt.py 是批处理入口, 输入目录下任何文件
        失败都会让 rc 非零; 本 suite 关心的是"目标文件是否被正确产出"。
        因此 `_run_case` 同时给出"per-file 成功证据"。
        """
        out_dir = d / f"out_{name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        extra = ["--output", str(out_dir), "--encoder", "x265",
                 "--no-hw-autoselect"]
        if plan_path is not None:
            extra += ["--audio-plan", str(plan_path)]
        r = _run_1kt(src_dir, *extra)
        produced = sorted(out_dir.rglob("*.MP4"))
        target = produced[0] if produced else Path("")
        text = (r.stdout or "") + (r.stderr or "")
        return target, (r, _per_file_ok(text, "clip.mp4"))

    def _per_file_ok(text: str, name: str) -> bool:
        """该文件是否**没有**失败 (失败在 1kt.py 里有多种出声方式)。

        只认这一条是不够的: 编码器非零退出是 `[FAIL]`, 而音频计划被拒 /
        编排失败走的是 `[AUDIO-FAIL]` —— 本阶段新增的路径正是后者。
        """
        markers = (
            "[FAIL]", "[AUDIO-FAIL]", "[PROBE-FAIL]", "[RENAME-FAIL]",
        )
        return not any(
            marker in line and name in line
            for line in text.splitlines()
            for marker in markers
        )

    # --- Test 1: 默认路径 (无 --audio-plan) ------------------------------
    default_out, (default_run, default_file_ok) = _run_case("default", None)
    default_ok = default_out.is_file() and default_out.stat().st_size > 0
    record("l3.p4c.T1 默认路径 (无 --audio-plan) 生产入口成功",
           default_ok and default_file_ok,
           f"rc={default_run.returncode} out={default_out.name} "
           f"file_ok={default_file_ok}")
    default_audio = _audio_of(default_out) if default_ok else []
    default_video = _video_of(default_out) if default_ok else None
    record("l3.p4c.T1 默认路径保留全部 4 条音轨 (既有行为)",
           len(default_audio) == 4
           and all(a.get("codec_name") == "pcm_s16le" for a in default_audio),
           f"{len(default_audio)} tracks")
    baseline_vhash = _video_hash(default_out) if default_ok else ""
    record("l3.p4c.T1 默认路径视频 hash 可基准化", bool(baseline_vhash),
           f"{baseline_vhash[:16]}")

    if not default_ok:
        return

    # --- Test 2: STREAM_COPY 整流保留 ------------------------------------
    keep_out, (keep_run, keep_file_ok) = _run_case("keep", keep_two)
    keep_ok = keep_out.is_file() and keep_out.stat().st_size > 0
    keep_audio = _audio_of(keep_out) if keep_ok else []
    record("l3.p4c.T2 整流保留 (选 2 条 / 重排) 生产入口成功",
           keep_file_ok and keep_ok and len(keep_audio) == 2,
           f"rc={keep_run.returncode} tracks={len(keep_audio)} "
           f"file_ok={keep_file_ok}")
    record("l3.p4c.T2 保留的音轨仍可解码出内容 (不是空轨)",
           all(
               (_decode_mono(keep_out, i) is not None
                and len(_decode_mono(keep_out, i)) > 40000)
               for i in range(len(keep_audio))
           ) and len(keep_audio) == 2,
           f"{len(keep_audio)} decoded")

    # --- Test 3: PCM_ROUTE + 编码 ----------------------------------------
    # ⚠️ 4×mono 素材选 2 条 = 每条流各自完整 -> 仍是 STREAM_COPY。要真正走
    # PCM 路由, 必须取**同一条流的部分声道**, 因此另建一条 1×4CH 素材。
    quad_src = src_dir / "quad.mp4"
    quad_channel_ids: list[str] = []
    if _make_av_channels(quad_src, 4):
        quad_probe = audio_probe_from_file(
            FFPROBE, quad_src, source_id="source"
        )
        quad_channel_ids = [c.id for c in quad_probe.plan().all_channels()]
        record("l3.p4c.T3 多声道素材 (1×4CH) 探测出 4 个声道身份",
               quad_channel_ids
               == [f"source:s1:c{i}" for i in range(4)],
               f"{quad_channel_ids}")
    else:
        record("l3.p4c.T3 多声道素材生成", False, "ffmpeg 失败")

    route_ok = False
    route_audio: list[dict[str, Any]] = []
    route_out = Path("")
    if quad_channel_ids:
        quad_plan_file = _write_plan(plans_dir / "route_quad.json", {
            "version": 1,
            "channels": {"select": [quad_channel_ids[0], quad_channel_ids[2]]},
            "encode": {"format": "pcm"},
        })
        quad_dir = d / "src_quad"
        quad_dir.mkdir(parents=True, exist_ok=True)
        import shutil as _shutil
        _shutil.copy2(quad_src, quad_dir / "quad.mp4")
        out_dir = d / "out_route"
        out_dir.mkdir(parents=True, exist_ok=True)
        run = _run_1kt(
            quad_dir, "--output", str(out_dir), "--encoder", "x265",
            "--no-hw-autoselect", "--audio-plan", str(quad_plan_file),
        )
        produced = sorted(out_dir.rglob("*.MP4"))
        route_out = produced[0] if produced else Path("")
        route_ok = route_out.is_file() and route_out.stat().st_size > 0
        route_audio = _audio_of(route_out) if route_ok else []
        record("l3.p4c.T3 PCM_ROUTE (4CH 取 2 声道) 生产入口成功",
               run.returncode == 0 and route_ok
               and len(route_audio) == 1
               and route_audio[0].get("channels") == 2,
               f"rc={run.returncode} "
               f"channels={route_audio[0].get('channels') if route_audio else None}")
        if route_ok:
            decoded = _decode_mono(route_out, 0)
            record("l3.p4c.T3 路由后的音轨逐样本可解码 (帧数 = timeline)",
                   decoded is not None and len(decoded) > 40000,
                   f"frames={0 if decoded is None else len(decoded)}")
    else:
        record("l3.p4c.T3 PCM_ROUTE 生产入口", False, "素材缺失")

    # 整流保留 + PCM 编码形态 (4×mono 选 2 条) 也验一遍
    route2_out, (route2_run, route2_file_ok) = _run_case("route2", route_two)
    route2_ok = route2_out.is_file() and route2_out.stat().st_size > 0
    record("l3.p4c.T3 整流选择 + PCM 编码计划 生产入口成功",
           route2_file_ok and route2_ok
           and len(_audio_of(route2_out)) == 2,
           f"rc={route2_run.returncode} "
           f"tracks={len(_audio_of(route2_out)) if route2_ok else 0}")

    # --- Test 4: PCM_MIX -------------------------------------------------
    # 混音需要 AudioPlan.mix_mode / mix_buses —— 请求 schema 刻意不表达
    # "怎么算"(那是执行图的事), 因此生产入口的混音用例直接构造计划并调用
    # 编排层, 走与 CLI 完全相同的代码路径。
    from core.audio_plan import AudioPlanner
    from core.audio_mix import MixBusBuilder
    from production.output import (
        VideoOutputArtifact,
        produce_audio_output,
    )
    from core.audio_encode import AudioFormatSpec, AudioEncodeFormat

    mix_dir = d / "mix_probe"
    mix_dir.mkdir(parents=True, exist_ok=True)
    video_only = mix_dir / "video.mp4"
    r = sh(FFMPEG, "-v", "error", "-y", "-i", default_out, "-map", "0:v:0",
           "-c", "copy", "-an", video_only, timeout=600)
    mix_plan_base = audio_probe_from_file(
        FFPROBE, src, source_id="source"
    ).plan()
    mixer = AudioPlanner(mix_plan_base)
    mixer.select_channels(channel_ids[0], channel_ids[1])
    bus = MixBusBuilder(mixer.plan).sum_all(
        [channel_ids[0], channel_ids[1]],
        gains={channel_ids[0]: 0.5, channel_ids[1]: 0.25},
    )
    mixed_plan = mixer.plan
    mixed_plan.mix_buses = [bus]
    ex_mix = resolve_audio_execution_path(mixed_plan)
    mix_out = mix_dir / "mixed.mp4"
    mix_result = produce_audio_output(
        plan=mixed_plan,
        video=VideoOutputArtifact(path=str(video_only), container="",
                                  label="encoded"),
        audio_source=src,
        output_path=mix_out,
        ffmpeg=FFMPEG,
        ffprobe=FFMPEG.with_name("ffprobe.exe"),
        work_dir=mix_dir / "work",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
    )
    mix_audio = _audio_of(mix_out) if mix_out.is_file() else []
    record("l3.p4c.T4 PCM_MIX 经生产编排层成功 (身份 = mixN)",
           ex_mix.path.value == "pcm_mix" and mix_result.ok
           and mix_result.applied and len(mix_audio) == 1,
           f"path={ex_mix.path.value} {mix_result.summary()}")
    if mix_result.ok and mix_out.is_file():
        decoded = _decode_mono(mix_out, 0)
        peak = float(__import__("numpy").abs(decoded).max()) \
            if decoded is not None and len(decoded) else 0.0
        record("l3.p4c.T4 混音产物保留 gain 规则且可解码",
               decoded is not None and len(decoded) > 40000
               and 0.01 < peak <= 1.0,
               f"frames={0 if decoded is None else len(decoded)} "
               f"peak={peak:.4f}")

    # --- Test 5: arbitrary-reference sync 走完整生产链 --------------------
    sync_dir = d / "sync_probe"
    sync_dir.mkdir(parents=True, exist_ok=True)
    from core.audio_sync import (
        apply_sync_result,
        estimate_sync,
        plan_from_plan,
    )

    sync_base = audio_probe_from_file(
       FFPROBE, src, source_id="source"
    ).plan()
    # 4 条 mono 各自延迟不同 -> 用伪噪声素材才能测; 这里用真素材的形态,
    # 若不可测则如实记录 (不伪造"同步成功")。
    estimation = estimate_sync(
        sync_base, plan_from_plan(sync_base, channel_ids[0]),
        ffmpeg=FFMPEG, work_dir=sync_dir / "estimate",
    )
    applied = apply_sync_result(sync_base, estimation)
    ex_sync = resolve_audio_execution_path(sync_base)
    sync_out = sync_dir / "synced.mp4"
    sync_result = produce_audio_output(
        plan=sync_base,
        video=VideoOutputArtifact(path=str(video_only), container="",
                                  label="encoded"),
        audio_source=src,
        output_path=sync_out,
        ffmpeg=FFMPEG,
        ffprobe=FFMPEG.with_name("ffprobe.exe"),
        work_dir=sync_dir / "work",
        audio_format=AudioFormatSpec(format=AudioEncodeFormat.PCM),
    )
    record("l3.p4c.T5 sync 结果经生产编排层写入音频输出",
           sync_result.ok and sync_result.applied,
           f"est_ok={estimation.ok} applied={applied} path={ex_sync.path.value} "
           f"{sync_result.summary()}")
    if sync_result.ok and sync_out.is_file():
        record("l3.p4c.T5 同步后的音轨逐条可解码 (同步信息没在编排中丢失)",
               len(_audio_of(sync_out)) == 4
               and all(_decode_mono(sync_out, i) is not None
                       for i in range(len(_audio_of(sync_out)))),
               f"tracks={len(_audio_of(sync_out))}")

    # --- Test 6: ⚠️ 视频基本流在每种音频处理下都不变 ---------------------
    # ⚠️ 两种素材不能混用一个基准: `route` 用的是 1×4CH 素材, 与默认路径的
    # 4×mono 素材**不是同一个视频源**, hash 本来就会不同。因此为 4CH 素材
    # 单独跑一次"默认路径"作为它自己的基准 —— 这才是真正在问的问题:
    # "同一素材, 加了音频计划之后视频是否改变"。
    quad_base_dir = d / "quad_baseline_in"
    quad_base_dir.mkdir(parents=True, exist_ok=True)
    _shutil_clean.copy2(quad_src, quad_base_dir / "quad.mp4")
    quad_out_dir = d / "quad_baseline_out"
    quad_out_dir.mkdir(parents=True, exist_ok=True)
    quad_run = _run_1kt(
        quad_base_dir, "--output", str(quad_out_dir), "--encoder", "x265",
        "--no-hw-autoselect",
    )
    quad_produced = sorted(quad_out_dir.rglob("*.MP4"))
    quad_baseline = (
        _video_hash(quad_produced[0]) if quad_produced else ""
    )
    record("l3.p4c.T6 4CH 素材的独立视频基准可得",
           bool(quad_baseline), f"{quad_baseline[:16]}")

    same_source_hashes = {
        "default": baseline_vhash,
        "keep": _video_hash(keep_out) if keep_ok else "",
        "mix": _video_hash(mix_out) if mix_out.is_file() else "",
        "sync": _video_hash(sync_out) if sync_out.is_file() else "",
    }
    record("l3.p4c.T6 同素材下视频基本流 sha256 与默认路径完全一致",
           baseline_vhash
           and all(h == baseline_vhash
                   for h in same_source_hashes.values()),
           f"base={baseline_vhash[:16]} "
           f"{ {k: v[:16] for k, v in same_source_hashes.items()} }")
    record("l3.p4c.T6 4CH 素材加音频计划后视频 hash 与其基准一致",
           bool(quad_baseline) and route_ok
           and _video_hash(route_out) == quad_baseline,
           f"base={quad_baseline[:16]} "
           f"route={_video_hash(route_out)[:16] if route_ok else ''}")
    props_ok = True
    for path in (keep_out, route_out, mix_out, sync_out):
        if not path.is_file():
            continue
        v = _video_of(path) or {}
        d0 = default_video or {}
        props_ok = props_ok and (
            v.get("codec_name"), v.get("width"), v.get("height"),
            v.get("avg_frame_rate"),
        ) == (
            d0.get("codec_name"), d0.get("width"), d0.get("height"),
            d0.get("avg_frame_rate"),
        )
    record("l3.p4c.T6 视频 codec/宽/高/帧率与默认路径一致",
           props_ok, "properties compared")

    # --- Test 7: 计划文件不合法 -> 明确失败, 且不产出半成品 --------------
    bad_plan = _write_plan(plans_dir / "bad.json", {
        "version": 1, "channels": {"select": ["source:s9:c0"]},
    })
    bad_out, (bad_run, bad_file_ok) = _run_case("bad", bad_plan)
    record("l3.p4c.T7 选择不存在的声道 -> 生产入口明确失败 (不静默忽略)",
           (not bad_file_ok) and not bad_out.is_file(),
           f"rc={bad_run.returncode} file_ok={bad_file_ok} "
           f"produced={bad_out.name or 'none'}")

    unknown_plan = _write_plan(plans_dir / "unknown.json", {
        "version": 1, "channels": {"select": [channel_ids[0]]},
        "loudness": True,
    })
    unk_out, (unk_run, unk_file_ok) = _run_case("unknown_key", unknown_plan)
    record("l3.p4c.T7 计划文件含未知键 -> 明确失败",
           unk_run.returncode != 0 and not unk_out.is_file(),
           f"rc={unk_run.returncode}")

    # --- Test 8: 空选择计划 == 不启用 (默认路径零回归) -------------------
    empty_plan = _write_plan(plans_dir / "empty.json", {
        "version": 1, "encode": {"format": "pcm"},
    })
    empty_out, (empty_run, empty_file_ok) = _run_case("empty", empty_plan)
    empty_audio = _audio_of(empty_out) if empty_out.is_file() else []
    record("l3.p4c.T8 空选择计划 -> 与默认路径一致 (4 轨, 未重编码)",
           empty_file_ok and len(empty_audio) == 4
           and all(a.get("codec_name") == "pcm_s16le"
                   for a in empty_audio),
           f"rc={empty_run.returncode} tracks={len(empty_audio)} "
           f"file_ok={empty_file_ok}")
    record("l3.p4c.T8 空选择计划下视频 hash 仍等于默认路径",
           empty_out.is_file()
           and _video_hash(empty_out) == baseline_vhash,
           "hash compared")

    # --- Test 9: 硬件后端与计划的组合行为 --------------------------------
    # ⚠️ post-v0.8.0: 硬件后端 (NVENC/QSV) **已支持** `--audio-plan`, 因此
    # 这条用例从"硬件必须报错"改为"硬件被接受 + 计划真的生效, 且不适用于
    # 该文件的计划仍然逐文件明确失败"。
    hw_dir = d / "out_hw_conflict"
    hardware_run = sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", src_dir, "--output", hw_dir,
        "--encoder", "nvenc", "--preset", "FAST",
        "--audio-plan", str(keep_two),
        "--headless", timeout=1800,
    )
    combined = (hardware_run.stdout or "") + (hardware_run.stderr or "")
    hw_clip = hw_dir / "clip.MP4"
    hw_audio = _audio_of(hw_clip) if hw_clip.is_file() else []
    record("l3.p4c.T9 硬件后端接受 --audio-plan (不再启动即拒绝)",
           "only supported on the classic software path" not in combined
           and hw_clip.is_file() and len(hw_audio) == 2,
           f"rc={hardware_run.returncode} tracks={len(hw_audio)}")
    record("l3.p4c.T9 硬件后端上计划不适用该文件时逐文件明确失败 (不静默忽略)",
           "audio_stream_not_found" in combined
           and "[AUDIO-FAIL]" in combined
           and not (hw_dir / "quad.MP4").is_file(),
           f"rc={hardware_run.returncode}")

    sync_conflict = sh(
        __import__("sys").executable, ROOT / "1kt.py",
        "--input", src_dir, "--output", d / "out_conflict2",
        "--encoder", "x265", "--no-hw-autoselect",
        "--channel-sync", "--audio-plan", str(keep_two),
        "--headless", timeout=600,
    )
    combined2 = (sync_conflict.stdout or "") + (sync_conflict.stderr or "")
    record("l3.p4c.T9 --audio-plan 与 --channel-sync 同时给出 -> 明确报错",
           sync_conflict.returncode != 0
           and "both decide the final audio" in combined2,
           f"rc={sync_conflict.returncode}")
