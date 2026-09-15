#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4A: 音频执行图判定 + 选择性 MP4 音频保留。

两个 suite:

  * `l1_audio_retention()` —— 纯声明层: `-map` 选择器构造, 以及"什么时候
    **不能**用 stream copy"的判定 (子集 / 重排 / 拆分 / 混音); 身份
    `stream_index` / `audio_position` / `input_index` 严格区分;
    `AudioTimeline` 是输出顺序权威。不运行 ffmpeg。
  * `l3_audio_retention()` —— 真实 ffmpeg + ffprobe 端到端: 用规格产出的
    真实 argv 生成 MOV/MP4, 再读回校验音轨数量 / 顺序 / codec, 并对保留轨
    做"解码后逐样本 == 源"的证明。

素材: L1 用既有确定性 fixture; L3 自建 `video + 4×mono PCM` (容器 index
与音频序号错开, 与真实 A7M5 形态一致) 并复用真实 A7M5 素材做结构对齐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..fixtures.audio import _make_av, _make_av_channels, _raw_audio_stream
from ..fixtures.plans import _p3a_build_plan
from ..paths import FFMPEG, IN_DIR, ffprobe_json, record, section, sh


def _streams(count: int, *, channels: int = 1, first_position: int = 0,
             first_index: int = 1) -> list[Any]:
    """`count` 条 audio stream 的合成事实 (默认 mono, 位置与容器 index 错开)。

    刻意让 `audio_position` 从 0 开始而 `stream_index` 从 1 开始 —— 这正是
    真实 A7M5 素材的形态, 用来钉住"两者绝不可互换"。
    """
    out = []
    for i in range(count):
        stream = _raw_audio_stream(
            first_index + i, channels=channels, layout=None if channels > 1
            else None,
        )
        stream["audio_position"] = first_position + i
        out.append(stream)
    return out


def _plan(specs: list[dict[str, Any]]) -> Any:
    """合成 plan: 先把 ffprobe 风格 dict 适配成 `AudioStream` 再建 plan。

    `_p3a_build_plan` 消费的是**模型对象**, 不是原始 dict —— 复用既有
    `build_audio_streams` (与生产探测同一条适配路径), 不另写一套。
    """
    from core.audio_models import build_audio_streams

    prepared = []
    for spec in specs:
        prepared.append({
            **spec,
            "streams": build_audio_streams(
                list(spec["streams"]), source_id=spec["source_id"]
            ),
        })
    return _p3a_build_plan(prepared)


def _single(count: int, *, channels: int = 1) -> Any:
    """单来源 camera, `count` 条流 (每条 `channels` 个声道)。"""
    return _plan([{
        "source_id": "camera",
        "path": "/nonexistent/camera.mov",
        "streams": _streams(count, channels=channels),
    }])


def _channels(plan: Any) -> list[str]:
    return [c.id for c in plan.all_channels()]


def l1_audio_retention() -> None:
    """Phase 4A: 执行图判定 + 选择性保留 (纯声明, 不执行 ffmpeg)。"""
    section("L1 选择性 MP4 音频保留 / 执行图判定 (Phase 4A)")
    from core.audio_execution import (
        AudioExecutionPath,
        mix_intent_bus,
        resolve_audio_execution_path,
    )
    from core.audio_models import AudioSourceType
    from core.audio_plan import AudioMapStrategy, AudioPlanner
    from core.audio_retention import (
        AudioPacketKind,
        REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED,
        REASON_AUDIO_RETENTION_ORDER_MISMATCH,
        AudioRetentionError,
        build_audio_retention,
        format_audio_retention,
        plan_has_audio,
        retention_to_args,
    )

    # =====================================================================
    # 1. 无 AudioPlan: 生产默认路径 (本阶段最硬的回归)
    # =====================================================================
    ex_none = resolve_audio_execution_path(None)
    record("p4a.exec plan=None -> NONE (不推断任何默认值)",
           ex_none.path is AudioExecutionPath.NONE
           and ex_none.ok and not ex_none.uses_pcm
           and ex_none.channel_count == 0,
           ex_none.summary())

    spec_none = build_audio_retention(None)
    record("p4a.retain plan=None -> default_plan (-map 0 + -c:a copy 不变)",
           spec_none.default_plan and spec_none.ok
           and spec_none.arguments() == []
           and spec_none.output_stream_count == 0,
           format_audio_retention(spec_none))
    record("p4a.默认规格 arguments() 为空 (默认路径不由本层重拼)",
           spec_none.arguments() == [] and spec_none.output_stream_count == 0
           and spec_none.default_plan and spec_none.stream_copyable is False,
           f"args={spec_none.arguments()} streams="
           f"{spec_none.output_stream_count}")
    record("p4a.plan_has_audio(None) 为 False (无计划 = 无音频决定)",
           plan_has_audio(None) is False)

    # =====================================================================
    # 2. 整条音频流保留: 完整源流的自然顺序 = 真 stream copy
    # =====================================================================
    one = _single(1)
    ex1 = resolve_audio_execution_path(one)
    record("p4a.exec 单条完整流 -> STREAM_COPY",
           ex1.path is AudioExecutionPath.STREAM_COPY and ex1.ok
           and not ex1.uses_pcm,
           ex1.summary())

    spec1 = build_audio_retention(one)
    record("p4a.retain 整流选择 -> 可执行 -map + -c:a copy",
           spec1.ok and spec1.stream_copyable
           and spec1.arguments() == ["-map", "0:a:0", "-c:a", "copy"],
           spec1.arguments())

    mono = _single(4)
    spec_m = build_audio_retention(mono)
    record("p4a.retain 2×2CH / 4×mono 的 -map 顺序 = 选择顺序",
           spec_m.ok and spec_m.stream_copyable
           and spec_m.arguments()
           == ["-map", "0:a:0", "-map", "0:a:1", "-map", "0:a:2",
               "-map", "0:a:3", "-c:a", "copy"],
           spec_m.arguments())
    record("p4a.身份 mono 流每流一个声道 (不塌缩)",
           [len(p.channel_indices) for p in spec_m.packets] == [1, 1, 1, 1]
           and [p.stream_index for p in spec_m.packets] == [1, 2, 3, 4]
           and [p.audio_position for p in spec_m.packets] == [0, 1, 2, 3],
           f"{[p.to_dict() for p in spec_m.packets]}")

    # --- 多声道单流 ---
    quad = _single(1, channels=4)
    spec_q = build_audio_retention(quad)
    record("p4a.retain 单流 4CH 整流保留 -> 一条 -map",
           spec_q.ok and spec_q.stream_copyable
           and spec_q.arguments() == ["-map", "0:a:0", "-c:a", "copy"]
           and spec_q.packets[0].channel_indices == [0, 1, 2, 3]
           and spec_q.packets[0].stream_channel_count == 4,
           spec_q.arguments())

    # =====================================================================
    # 3. 删除一个音频流: 其余按指定顺序保留
    # =====================================================================
    drop = AudioPlanner(_single(4))
    drop.select_channels("camera:s1:c0", "camera:s3:c0", "camera:s4:c0")
    spec_drop = build_audio_retention(drop.plan)
    record("p4a.retain 删除 camera:s2 -> 只保留 0/2/3 (顺序不变)",
           spec_drop.ok and spec_drop.stream_copyable
           and spec_drop.arguments()
           == ["-map", "0:a:0", "-map", "0:a:2", "-map", "0:a:3",
               "-c:a", "copy"],
           spec_drop.arguments())
    record("p4a.retain 被排除的流不出现在任何 -map 里",
           all("0:a:1" not in a for a in spec_drop.arguments())
           and spec_drop.channel_ids
           == ["camera:s1:c0", "camera:s3:c0", "camera:s4:c0"])

    # =====================================================================
    # 4. reorder: [2,0,3,1] —— 仍是整流 copy, 但顺序改变
    # =====================================================================
    reorder = AudioPlanner(_single(4))
    reorder.map_channels(  # 与 select 顺序不同: 显式 mapping
        "camera:s3:c0", "camera:s1:c0", "camera:s4:c0", "camera:s2:c0"
    )
    spec_re = build_audio_retention(reorder.plan)
    record("p4a.retain reorder [2,0,3,1] -> -map 顺序 = 输出顺序",
           spec_re.ok and spec_re.stream_copyable
           and spec_re.arguments()
           == ["-map", "0:a:2", "-map", "0:a:0", "-map", "0:a:3",
               "-map", "0:a:1", "-c:a", "copy"],
           spec_re.arguments())
    record("p4a.retain reorder 后身份仍逐项可追溯 (不按位置猜)",
           spec_re.channel_ids
           == ["camera:s3:c0", "camera:s1:c0", "camera:s4:c0",
               "camera:s2:c0"]
           and [p.stream_index for p in spec_re.packets] == [3, 1, 4, 2]
           and [p.audio_position for p in spec_re.packets] == [2, 0, 3, 1],
           f"{[p.to_dict() for p in spec_re.packets]}")
    record("p4a.retain reorder 不产生声道过滤 (整流重排仍是 copy)",
           all(p.strategy == AudioMapStrategy.STREAM_COPY.value
               for p in spec_re.packets)
           and reorder.plan.mapping_kind == "explicit")

    # =====================================================================
    # 5. channel-level selection: 不能当成整流 copy
    # =====================================================================
    sub = AudioPlanner(_single(1, channels=4))
    sub.select_channels("camera:s1:c2")
    ex_sub = resolve_audio_execution_path(sub.plan)
    spec_sub = build_audio_retention(sub.plan)
    record("p4a.exec 单流取部分声道 -> PCM_ROUTE (不是 copy)",
           ex_sub.path is AudioExecutionPath.PCM_ROUTE
           and ex_sub.uses_pcm and ex_sub.ok,
           ex_sub.summary())
    record("p4a.retain 部分声道 -> 拒绝伪装成 stream copy",
           not spec_sub.stream_copyable
           and not spec_sub.ok
           and REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
           in spec_sub.reasons,
           spec_sub.summary())
    raised = False
    try:
        retention_to_args(spec_sub)
    except AudioRetentionError as exc:
        raised = exc.reason == (
            REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
        )
    record("p4a.retain 不可执行的规格 arguments() 直接抛 (不输出错误 argv)",
           raised)
    record("p4a.retain PCM 路径不产生任何 -map 且带明确 reason",
           spec_sub.packets == []
           and REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
           in spec_sub.reasons
           and spec_sub.channel_ids == ["camera:s1:c2"],
           f"packets={len(spec_sub.packets)} ids={spec_sub.channel_ids} "
           f"reasons={spec_sub.reasons}")

    # 声道重排 (完整但顺序不同) 同样不能用 -map 表达
    swap = AudioPlanner(_single(1, channels=4))
    swap.map_channels("camera:s1:c1", "camera:s1:c0",
                      "camera:s1:c3", "camera:s1:c2")
    ex_swap = resolve_audio_execution_path(swap.plan)
    spec_swap = build_audio_retention(swap.plan)
    record("p4a.exec 单流内声道重排 -> PCM_ROUTE",
           ex_swap.path is AudioExecutionPath.PCM_ROUTE and ex_swap.ok,
           ex_swap.summary())
    record("p4a.retain 声道重排不能用 -map 表达 (完整但非自然顺序)",
           not spec_swap.stream_copyable and spec_swap.packets == []
           and spec_swap.path is AudioExecutionPath.PCM_ROUTE
           and REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
           in spec_swap.reasons,
           spec_swap.summary())

    # =====================================================================
    # 6. mix: 必须进入 PCM 路径, 绝不走 stream copy
    # =====================================================================
    mixer = AudioPlanner(_single(2))
    mixer.select_channels("camera:s1:c0", "camera:s2:c0")
    mixer.plan.mix_mode = "sum"
    ex_mix = resolve_audio_execution_path(mixer.plan)
    spec_mix = build_audio_retention(mixer.plan)
    record("p4a.exec 混音意图 -> PCM_MIX (与 audio_process 判定同源)",
           ex_mix.path is AudioExecutionPath.PCM_MIX
           and ex_mix.uses_pcm and ex_mix.is_mixing,
           ex_mix.summary())
    record("p4a.retain 混音不产生任何 -map (交回 PCM 图)",
           not spec_mix.stream_copyable
           and spec_mix.packets == []
           and spec_mix.path is AudioExecutionPath.PCM_MIX
           and any("Phase 4B" in n for n in spec_mix.notes),
           spec_mix.summary())
    record("p4a.MIXING 不是 executable (既有契约未变)",
           AudioPlanner(mixer.plan).map_spec().executable is False)

    # =====================================================================
    # 7. 多来源: input_index 与 audio_position 联合定位
    # =====================================================================
    multi = _plan([
        {
            "source_id": "camera", "source_type": AudioSourceType.MEDIA,
            "path": "/nonexistent/camera.mov", "input_index": 0,
            "streams": _streams(2),
        },
        {
            "source_id": "recorder", "source_type": AudioSourceType.EXTERNAL,
            "path": "/nonexistent/recorder.wav", "input_index": 1,
            "streams": _streams(1),
        },
    ])
    both = AudioPlanner(multi)
    both.select_channels("recorder:s1:c0", "camera:s2:c0")
    both.map_channels("recorder:s1:c0", "camera:s2:c0")   # 同一集合, 只定顺序
    spec_multi = build_audio_retention(both.plan)
    record("p4a.retain 多来源 -> -map 带正确 input_index",
           spec_multi.ok and spec_multi.stream_copyable
           and spec_multi.arguments()
           == ["-map", "1:a:0", "-map", "0:a:1", "-c:a", "copy"],
           spec_multi.arguments())
    record("p4a.retain 多来源身份保留 source_id (不是 mp4 序号)",
           [p.source_id for p in spec_multi.packets]
           == ["recorder", "camera"]
           and [p.input_index for p in spec_multi.packets] == [1, 0]
           and [p.audio_position for p in spec_multi.packets] == [0, 1])

    # =====================================================================
    # 8. 明确不要音频
    # =====================================================================
    none_sel = AudioPlanner(_single(2))
    none_sel.exclude_channels(*_channels(none_sel.plan))
    spec_off = build_audio_retention(none_sel.plan)
    record("p4a.retain 全部排除 -> no_audio 且 -an (不是 -map 0)",
           spec_off.no_audio and spec_off.arguments() == ["-an"]
           and not spec_off.default_plan,
           spec_off.summary())
    record("p4a.exec 无有效映射 -> NONE (不会误判成 copy)",
           resolve_audio_execution_path(none_sel.plan).path
           is AudioExecutionPath.NONE)

    # =====================================================================
    # 9. AudioTimeline 是输出顺序权威
    # =====================================================================
    class _TL:
        def __init__(self, ids: list[str]) -> None:
            self.output_channel_ids = ids

    ok_tl = build_audio_retention(
        reorder.plan,
        timeline=_TL(["camera:s3:c0", "camera:s1:c0", "camera:s4:c0",
                      "camera:s2:c0"]),
    )
    record("p4a.retain 与 AudioTimeline 顺序一致 -> 通过",
           ok_tl.ok and ok_tl.channel_ids == ok_tl.channel_ids)

    bad_tl = build_audio_retention(
        reorder.plan,
        timeline=_TL(["camera:s1:c0", "camera:s3:c0", "camera:s4:c0",
                      "camera:s2:c0"]),
    )
    record("p4a.retain 与 AudioTimeline 顺序不一致 -> 明确拒绝",
           not bad_tl.ok
           and REASON_AUDIO_RETENTION_ORDER_MISMATCH in bad_tl.reasons
           and not bad_tl.stream_copyable,
           bad_tl.summary())

    # =====================================================================
    # 10. 与视频完全解耦 (结构性断言, 不是字符串扫描)
    # =====================================================================
    import ast as _ast
    import core.audio_execution as _ex_mod
    import core.audio_retention as _re_mod

    forbidden_modules = {
        "encoders", "preservation", "core.batch_hw", "core.scaling",
        "core.models", "core.color", "core.probe",
    }
    forbidden_names = {
        "profile", "encode", "encoder", "preset", "crf", "pix_fmt",
        "video_streams", "wav_export_spec",
    }
    imported: set[str] = set()
    public_args: set[str] = set()
    module_consts: list[str] = []
    for module in (_ex_mod, _re_mod):
        src = open(module.__file__, encoding="utf-8").read()
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                imported.add(node.module)
        for node in tree.body:
            if isinstance(node, _ast.FunctionDef):
                public_args.update(
                    a.arg for a in node.args.args + node.args.kwonlyargs
                )
            elif isinstance(node, _ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, _ast.FunctionDef):
                        public_args.update(
                            a.arg for a in sub.args.args + sub.args.kwonlyargs
                        )
            elif isinstance(node, _ast.Assign):
                for t in node.targets:
                    if isinstance(t, _ast.Name):
                        value = node.value
                        if isinstance(value, _ast.Constant) and isinstance(
                                value.value, str):
                            module_consts.append(value.value)

    record("p4a.模块不 import 任何视频/编码器/容器模块",
           not (imported & forbidden_modules),
           f"imports={sorted(imported)}")
    record("p4a.API 不存在任何视频/编码器参数",
           not (public_args & forbidden_names),
           f"args={sorted(public_args)}")
    record("p4a.模块不构造任何视频 ffmpeg 参数 (只出现音频相关 token)",
           all(tok not in " ".join(module_consts) for tok in (
               "-c:v", "-an", "-vf", "libx265", "libsvtav1",
           )),
           f"consts={module_consts}")

    # =====================================================================
    # 11. 同一条流被拆到多个输出段: 不能用单个 -map 表达
    # =====================================================================
    # `-map <in>:a:<pos>` 必然带出该流**全部**声道, 因此当一条 2CH 流的两个
    # 声道在输出里**不相邻**时, 任何 `-map` 拼法都会给出错误的音轨边界。
    # 判据比 `AudioMapSpec.strategy` 更严格 (后者只看单个输出段是否整流)。
    from core.audio_plan import build_map_spec as _bms

    inter = AudioPlanner(_single(2, channels=2))
    inter.select_channels(
        "camera:s1:c0", "camera:s1:c1", "camera:s2:c0", "camera:s2:c1"
    )
    plan_split = inter.plan
    # 交错顺序 (s1c0, s2c0, s1c1, s2c1): 每条流的声道都不连续
    entries = [
        {"output_index": 0, "source_channel_id": "camera:s1:c0",
         "source_id": "camera", "stream_index": 1, "channel_index": 0},
        {"output_index": 1, "source_channel_id": "camera:s2:c0",
         "source_id": "camera", "stream_index": 2, "channel_index": 0},
        {"output_index": 2, "source_channel_id": "camera:s1:c1",
         "source_id": "camera", "stream_index": 1, "channel_index": 1},
        {"output_index": 3, "source_channel_id": "camera:s2:c1",
         "source_id": "camera", "stream_index": 2, "channel_index": 1},
    ]
    plan_split.channel_mapping = entries
    plan_split.mapping_kind = "explicit"
    plan_split.selected_channels = [
        str(e["source_channel_id"]) for e in entries
    ]
    ex_split = resolve_audio_execution_path(plan_split)
    spec_split = build_audio_retention(plan_split)
    record("p4a.exec 同一条流被拆到不相邻输出位置 -> PCM_ROUTE",
           ex_split.path is AudioExecutionPath.PCM_ROUTE and ex_split.ok,
           ex_split.summary())
    record("p4a.retain 流被拆开时拒绝 -map (单 -map 必然带出整条流)",
           not spec_split.stream_copyable and not spec_split.ok
           and spec_split.packets == []
           and REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
           in spec_split.reasons,
           f"packets={len(spec_split.packets)} "
           f"reasons={spec_split.reasons}")
    record("p4a.2×2CH 交错顺序确实不是整流 copy (判据生效)",
           _bms(plan_split).strategy is AudioMapStrategy.CHANNEL_FILTER
           or not _bms(plan_split).full_stream_copy,
           f"strategy={_bms(plan_split).strategy.value} "
           f"full_stream_copy={_bms(plan_split).full_stream_copy}")

    # =====================================================================
    # 12. 报告契约: 每个 record() 的 detail 必须能渲染成字符串
    # =====================================================================
    # `record(name, ok, detail)` 的 detail 会被报告层切片渲染; 传一个 set
    # 会让**整个报告落盘**在最后一步崩掉 (用例全绿却写不出报告)。这条断言
    # 静态检查本包所有 suite 的 record 调用, 把该类错误钉在测试里。
    import ast as _ast
    from pathlib import Path as _Path

    str_ok = (
        _ast.JoinedStr, _ast.Constant, _ast.Name, _ast.Call,
        _ast.Attribute, _ast.BinOp, _ast.IfExp, _ast.Subscript,
        _ast.Compare,
    )
    offenders: list[str] = []
    for module_path in sorted(_Path(__file__).resolve().parent.glob("*.py")):
        tree = _ast.parse(module_path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if fname != "record" or len(node.args) < 3:
                continue
            if not isinstance(node.args[2], str_ok):
                offenders.append(
                    f"{module_path.name}:{node.lineno}"
                    f"({type(node.args[2]).__name__})"
                )
    record("p4a.所有 record() 的 detail 都是字符串表达式 (报告不崩)",
           not offenders, f"{offenders[:6]}")



def _audio_streams(path: Path) -> list[dict[str, Any]]:
    """输出的音频流事实 (按容器顺序)。"""
    raw = ffprobe_json(path).get("streams", [])
    return [s for s in raw if s.get("codec_type") == "audio"]


def _run_retention(
    src: Path, dst: Path, spec: Any, *, extra: list[str] | None = None,
) -> tuple[bool, str]:
    """用保留规格的**真实** argv 产出输出 (视频 stream copy)。

    音频参数完全来自 `spec.arguments()` —— 测试不自己拼 `-map`/`-c:a`,
    因此"规格说的"和"实际跑的"不可能分叉。`-map 0:v` 只是把源视频轨原样
    带进容器 (stream copy), 音频模块不参与该决定。
    """
    args = [
        "-v", "error", "-y", "-i", str(src),
        "-map", "0:v", "-c:v", "copy",
        *spec.arguments(),
        *(extra or []),
        str(dst),
    ]
    r = sh(FFMPEG, *args, timeout=900)
    return r.returncode == 0 and dst.is_file(), " ".join(args[-10:])


def _decode_mono(path: Path, position: int, frames: int) -> Any:
    """解码第 `position` 条音频为 float32 mono (逐样本比较用)。"""
    import numpy as np

    out = path.with_suffix(f".dec{position}.raw")
    r = sh(FFMPEG, "-v", "error", "-y", "-i", path,
           "-map", f"0:a:{position}", "-vn", "-ac", "1",
           "-f", "f32le", "-ar", "48000", out, timeout=600)
    if r.returncode != 0 or not out.is_file():
        return None
    data = np.fromfile(out, dtype="<f4")
    return data[:frames] if frames else data


def _plan_of(path: Path, source_id: str, *, input_index: int | None = 0) -> Any:
    """真实 MOV -> AudioPlan (真实 ffprobe 事实, 不猜)。"""
    from core.audio_models import build_audio_streams

    raw = ffprobe_json(path).get("streams", [])
    streams = build_audio_streams(raw, source_id=source_id)
    return _p3a_build_plan([{
        "source_id": source_id,
        "path": str(path),
        "streams": streams,
        "input_index": input_index,
    }])


def l3_audio_retention() -> None:
    """Phase 4A: 真实容器级选择性音频保留 (video copy, audio 选择)。"""
    section("L3 选择性 MP4 音频保留 (Phase 4A)")
    from core.audio_execution import (
        AudioExecutionPath,
        resolve_audio_execution_path,
    )
    from core.audio_plan import AudioPlanner
    from core.audio_retention import (
        REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED,
        build_audio_retention,
    )
    import numpy as np

    d = IN_DIR / "p4a_retention"
    d.mkdir(parents=True, exist_ok=True)

    src = d / "p4a_src_4mono.mov"
    if not _make_av(src, 4):
        record("l3.p4a.真实素材 (video + 4×mono PCM) 生成", False,
               "ffmpeg 合成失败")
        return

    raw_src = ffprobe_json(src).get("streams", [])
    audio_src = [s for s in raw_src if s.get("codec_type") == "audio"]
    record("l3.p4a.素材形态 = 1 video + 4 audio, 容器 index 与音频序号错开",
           len(raw_src) == 5 and len(audio_src) == 4
           and [s["index"] for s in audio_src] == [1, 2, 3, 4],
           f"{[(s['index'], s.get('codec_name')) for s in audio_src]}")

    plan = _plan_of(src, "camera")
    record("l3.p4a.真实探测 audio_position 0..3 与 stream_index 1..4 分离",
           [c.audio_position for c in plan.source("camera").streams]
           == [0, 1, 2, 3]
           and [c.stream_index for c in plan.source("camera").streams]
           == [1, 2, 3, 4],
           f"{[(c.stream_index, c.audio_position) for c in plan.source('camera').streams]}")

    ex = resolve_audio_execution_path(plan)
    record("l3.p4a.真实素材整流选择 -> STREAM_COPY",
           ex.path is AudioExecutionPath.STREAM_COPY and ex.ok,
           ex.summary())

    # =====================================================================
    # 1. 全保留: 输出音轨集合与源一致
    # =====================================================================
    full = build_audio_retention(plan)
    out_full = d / "p4a_full.mov"
    ok_full, cmd_full = _run_retention(src, out_full, full)
    a_full = _audio_streams(out_full) if ok_full else []
    record("l3.p4a.全保留 -> 输出 4 条音频 (数量)",
           ok_full and full.stream_copyable and len(a_full) == 4,
           f"ok={ok_full} streams={len(a_full)} cmd=…{cmd_full}")
    record("l3.p4a.全保留 -> codec 原样 (stream copy, 未重编码)",
           all(s.get("codec_name") == "pcm_s16le" for s in a_full)
           and all(s.get("channels") == 1 for s in a_full),
           f"{[(s.get('codec_name'), s.get('channels')) for s in a_full]}")
    record("l3.p4a.全保留 -> 视频轨仍在且未重编码 (h264)",
           any(s.get("codec_type") == "video"
               and s.get("codec_name") == "h264"
               for s in ffprobe_json(out_full).get("streams", [])),
           "video present")

    # 4 条 mono 流: 输出音轨顺序 = 输入音频序号顺序
    if len(a_full) == 4:
        frames = 48000
        tones = []
        for i in range(4):
            data = _decode_mono(out_full, i, frames)
            tones.append(data is not None and float(np.abs(data).max()) > 0.1)
        record("l3.p4a.全保留 -> 4 条音轨都有内容 (逐轨解码非静音)",
               all(tones), f"{tones}")

    # =====================================================================
    # 2. 删除一条流: 其余保持顺序
    # =====================================================================
    drop = AudioPlanner(plan)
    drop.select_channels("camera:s1:c0", "camera:s2:c0", "camera:s4:c0")
    spec_drop = build_audio_retention(drop.plan)
    out_drop = d / "p4a_drop.mov"
    ok_drop, cmd_drop = _run_retention(src, out_drop, spec_drop)
    a_drop = _audio_streams(out_drop) if ok_drop else []
    record("l3.p4a.删除 camera:s3 -> 输出 3 条音频",
           ok_drop and spec_drop.stream_copyable and len(a_drop) == 3,
           f"ok={ok_drop} streams={len(a_drop)} cmd=…{cmd_drop}")
    record("l3.p4a.删除后 codec/声道形态不变 (仍是原始 PCM)",
           all(s.get("codec_name") == "pcm_s16le" and s.get("channels") == 1
               for s in a_drop),
           f"{[(s.get('codec_name'), s.get('channels')) for s in a_drop]}")

    # 逐样本: 保留的 3 条输出音轨 == 源的第 1/2/4 条 (顺序与内容都对)
    exact = True
    detail = []
    if len(a_drop) == 3:
        for out_pos, src_pos in enumerate((0, 1, 3)):
            got = _decode_mono(out_drop, out_pos, 48000)
            want = _decode_mono(src, src_pos, 48000)
            same = bool(
                got is not None and want is not None
                and got.shape == want.shape
                and np.array_equal(got, want)
            )
            exact = exact and same
            detail.append(f"out{out_pos}==src{src_pos}:{same}")
    record("l3.p4a.删除后逐样本与源一致 (顺序 + 内容)",
           exact and len(a_drop) == 3, " ".join(detail))

    # =====================================================================
    # 3. reorder: [2,0,3,1] —— 身份与内容都要跟着走
    # =====================================================================
    order = AudioPlanner(plan)
    order.select_channels("camera:s1:c0", "camera:s2:c0", "camera:s3:c0",
                          "camera:s4:c0")
    order.map_channels("camera:s3:c0", "camera:s1:c0", "camera:s4:c0",
                       "camera:s2:c0")
    spec_re = build_audio_retention(order.plan)
    out_re = d / "p4a_reorder.mov"
    ok_re, cmd_re = _run_retention(src, out_re, spec_re)
    a_re = _audio_streams(out_re) if ok_re else []
    record("l3.p4a.reorder [2,0,3,1] -> 输出 4 条音频且仍可 copy",
           ok_re and spec_re.stream_copyable and len(a_re) == 4,
           f"ok={ok_re} streams={len(a_re)} cmd=…{cmd_re}")

    reorder_exact = True
    reorder_detail = []
    if len(a_re) == 4:
        for out_pos, src_pos in enumerate((2, 0, 3, 1)):
            got = _decode_mono(out_re, out_pos, 48000)
            want = _decode_mono(src, src_pos, 48000)
            same = bool(
                got is not None and want is not None
                and got.shape == want.shape
                and np.array_equal(got, want)
            )
            reorder_exact = reorder_exact and same
            reorder_detail.append(f"out{out_pos}==src{src_pos}:{same}")
    record("l3.p4a.reorder 逐样本落到正确的输出音轨 (身份未错位)",
           reorder_exact and len(a_re) == 4, " ".join(reorder_detail))

    # 交叉验证: 重排后再按 (2,0,3,1) 反推应当回到源顺序
    re_out0 = _decode_mono(out_re, 0, 48000)
    re_src0 = _decode_mono(src, 0, 48000)
    record("l3.p4a.reorder 与源顺序不同 (确实换了位)",
           len(a_re) == 4 and re_out0 is not None and re_src0 is not None
           and not np.array_equal(re_out0, re_src0))

    # =====================================================================
    # 4. 单流内取部分声道: 明确拒绝, 且**不产出**文件
    # =====================================================================
    quad_src = d / "p4a_src_4ch.mov"
    quad_ready = _make_av_channels(quad_src, 4)
    quad_plan: Any = None
    mixed_subset_plan: Any = None
    if not quad_ready:
        record("l3.p4a.多声道素材 (video + 1×4CH PCM) 生成", False,
               "ffmpeg 合成失败")
    else:
        quad_streams = [
            s for s in ffprobe_json(quad_src).get("streams", [])
            if s.get("codec_type") == "audio"
        ]
        record("l3.p4a.多声道素材 = 1 条 4CH 流",
               len(quad_streams) == 1 and quad_streams[0].get("channels") == 4,
               f"{[(s['index'], s.get('channels')) for s in quad_streams]}")

        quad_plan = _plan_of(quad_src, "camera")
        whole = build_audio_retention(quad_plan)
        ex_whole = resolve_audio_execution_path(quad_plan)
        record("l3.p4a.4CH 整流保留 -> STREAM_COPY",
               whole.ok and whole.stream_copyable
               and ex_whole.path is AudioExecutionPath.STREAM_COPY
               and whole.arguments() == ["-map", "0:a:0", "-c:a", "copy"],
               whole.arguments())

        sub = AudioPlanner(quad_plan)
        sub.select_channels("camera:s1:c2")
        mixed_subset_plan = sub.plan
        spec_sub = build_audio_retention(sub.plan)
        record("l3.p4a.4CH 取单声道 -> 拒绝 stream copy (不能假装整流)",
               not spec_sub.stream_copyable and not spec_sub.ok
               and REASON_AUDIO_RETENTION_CHANNEL_FILTER_UNSUPPORTED
               in spec_sub.reasons,
               spec_sub.summary())
        record("l3.p4a.拒绝时路径为 PCM_ROUTE (交回 PCM 图, 不是计划非法)",
               spec_sub.path is AudioExecutionPath.PCM_ROUTE
               and resolve_audio_execution_path(sub.plan).path
               is AudioExecutionPath.PCM_ROUTE,
               resolve_audio_execution_path(sub.plan).summary())

        # 4CH -> 输出的 4CH WAV 仍是既有 PCM 图的能力 (本阶段不改动)
        record("l3.p4a.4CH 的部分声道由既有 PCM 图承担 (职责不重叠)",
               spec_sub.execution is not None
               and spec_sub.execution.uses_pcm
               and spec_sub.packets == [])

    # =====================================================================
    # 4b. ⚠️ 图判定与真实渲染一致 (抽离出来的判定不能与实现分叉)
    # =====================================================================
    # `run_audio_render()` 是既有实现; 对同一批**可解码**的 plan 分别调用
    # (a) 新的显式判定, (b) 真实渲染, 断言两者选的是同一张图。
    # 混音意图的唯一真相是 `_resolve_mix_bus()`, 因此还断言
    # "自动判定 == 显式传 bus" —— 抽离时优先级不能被改变。
    from core.audio_execution import mix_intent_bus
    from core.audio_mix import MixBusBuilder
    from core.audio_process import run_audio_render

    graph_work = d / "graph"
    graph_work.mkdir(parents=True, exist_ok=True)

    route_case = AudioPlanner(_plan_of(src, "camera")).plan   # 整流: 4×mono
    mix_planner = AudioPlanner(_plan_of(src, "camera"))
    mix_planner.select_channels("camera:s1:c0", "camera:s2:c0")
    mix_case_plan = mix_planner.plan
    mix_case_bus = MixBusBuilder(mix_case_plan).sum_all(
        ["camera:s1:c0", "camera:s2:c0"]
    )
    # `MixBusBuilder.sum_all()` 只**返回** bus, 不写回 plan; 要构造"计划自带
    # 混音意图"的形态必须显式登记 —— `_resolve_mix_bus()` 的优先级是
    # 显式参数 -> `plan.mix_buses` -> `plan.mix_mode`, 这一条正是验证它。
    mix_case_plan.mix_buses = [mix_case_bus]
    auto_expected = bool(mix_case_plan.mix_buses)

    graph_cases: list[tuple[str, Any, Any, Any]] = [
        ("copy-4mono", route_case, None, AudioExecutionPath.STREAM_COPY),
        ("pcm-4ch-subset", mixed_subset_plan, None,
         AudioExecutionPath.PCM_ROUTE),
        ("pcm-mix", mix_case_plan, mix_case_bus,
         AudioExecutionPath.PCM_MIX),
    ]
    graph_ok = True
    graph_detail: list[str] = []
    for label, case_plan, case_bus, want_path in graph_cases:
        if case_plan is None:
            graph_ok = False
            graph_detail.append(f"{label}:SKIP(no fixture)")
            continue
        execution = resolve_audio_execution_path(case_plan, mix_bus=case_bus)
        result = run_audio_render(
            case_plan, ffmpeg=FFMPEG, work_dir=graph_work,
            output_path=graph_work / f"graph_{label}.wav", overwrite=True,
            chunk_frames=4096, mix_bus=case_bus,
        )
        actual_mixing = result.mix_bus is not None
        actual_routing = result.route_spec is not None
        same = bool(
            result.ok
            and actual_mixing == execution.is_mixing
            and actual_routing == (not execution.is_mixing)
            and execution.path is want_path
        )
        graph_ok = graph_ok and same
        graph_detail.append(
            f"{label}:{execution.path.value}"
            f"(mix={actual_mixing},route={actual_routing},ok={result.ok})"
        )
    record("l3.p4a.显式图判定与 run_audio_render 逐例一致 (路由 vs 混音)",
           graph_ok, " ".join(graph_detail))

    auto_bus = mix_intent_bus(mix_case_plan)
    record("l3.p4a.混音意图自动判定 == 显式 MixBus (优先级未变)",
           auto_expected and auto_bus is not None
           and mix_case_bus is not None
           and resolve_audio_execution_path(
               mix_case_plan, mix_bus=auto_bus).path
           is resolve_audio_execution_path(
               mix_case_plan, mix_bus=mix_case_bus).path
           is AudioExecutionPath.PCM_MIX,
           f"auto={type(auto_bus).__name__} "
           f"plan_buses={len(mix_case_plan.mix_buses)}")

    # 混音输出身份是 mixN, 与"源声道身份"不同源 —— 因此混音绝不能被当成
    # "整流保留"(那会写出错误的 -map)。
    mix_result = run_audio_render(
        mix_case_plan, ffmpeg=FFMPEG, work_dir=graph_work,
        output_path=graph_work / "graph_mix_identity.wav", overwrite=True,
        chunk_frames=4096, mix_bus=mix_case_bus,
    )
    tl_ids = list(
        getattr(mix_result.timeline, "output_channel_ids", []) or []
    )
    record("l3.p4a.混音输出身份 = mixN (与源声道身份不同源, 不可 -map)",
           mix_result.ok and tl_ids
           and all(str(c).startswith("mix") for c in tl_ids)
           and "camera:s1:c0" not in tl_ids,
           f"{tl_ids}")

    # =====================================================================
    # 5. 与 video 解耦: 同一个音频决定在"视频被重编码"时不变
    # =====================================================================
    out_enc = d / "p4a_video_reencoded.mov"
    args_enc = [
        "-v", "error", "-y", "-i", str(src),
        "-map", "0:v", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "45", "-pix_fmt", "yuv420p",
        *spec_drop.arguments(),
        str(out_enc),
    ]
    r_enc = sh(FFMPEG, *args_enc, timeout=900)
    a_enc = _audio_streams(out_enc) if out_enc.is_file() else []
    record("l3.p4a.音频决定不依赖视频策略 (视频重编码时音轨集合不变)",
           r_enc.returncode == 0 and len(a_enc) == 3
           and all(s.get("codec_name") == "pcm_s16le" for s in a_enc)
           and len(a_enc) == spec_drop.output_stream_count,
           f"streams={len(a_enc)} expected={spec_drop.output_stream_count}")

    # 同一份音频规格在两次不同视频处理下给出完全相同的音频参数
    record("l3.p4a.同一规格的音频 argv 与视频处理无关 (逐字一致)",
           spec_drop.arguments() == spec_drop.arguments()
           and "--" not in " ".join(spec_drop.arguments())
           and all(tok not in spec_drop.arguments()
                   for tok in ("-c:v", "-pix_fmt", "-crf")),
           spec_drop.arguments())

    # =====================================================================
    # 6. 无音频计划: 既有默认路径的 argv 形态不变
    # =====================================================================
    default_spec = build_audio_retention(None)
    out_def = d / "p4a_default.mov"
    args_def = [
        "-v", "error", "-y", "-i", str(src),
        "-map", "0", "-c:a", "copy",
        *default_spec.arguments(),
        str(out_def),
    ]
    r_def = sh(FFMPEG, *args_def, timeout=900)
    a_def = _audio_streams(out_def) if out_def.is_file() else []
    record("l3.p4a.默认规格不改变 -map 0 + -c:a copy (4 条音轨全保留)",
           default_spec.arguments() == []
           and r_def.returncode == 0 and len(a_def) == 4,
           f"streams={len(a_def)} args={default_spec.arguments()}")

    # =====================================================================
    # 7. 与真实 A7M5 素材对齐 (4×mono, 音频全静音不影响结构判定)
    # =====================================================================
    from ..paths import ROOT

    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if real_files:
        real_plan = _plan_of(real_files[0], "camera")
        spec_real = build_audio_retention(real_plan)
        record("l3.p4a.真实 A7M5 4×mono -> 4 条 -map copy",
               spec_real.ok and spec_real.stream_copyable
               and spec_real.arguments()
               == ["-map", "0:a:0", "-map", "0:a:1", "-map", "0:a:2",
                   "-map", "0:a:3", "-c:a", "copy"],
               spec_real.arguments())
        real_drop = AudioPlanner(real_plan)
        real_drop.select_channels("camera:s2:c0", "camera:s4:c0")
        spec_rd = build_audio_retention(real_drop.plan)
        record("l3.p4a.真实 A7M5 删除 2 条 -> 保留 (2,4) 且用 audio_position",
               spec_rd.ok and spec_rd.stream_copyable
               and spec_rd.arguments()
               == ["-map", "0:a:1", "-map", "0:a:3", "-c:a", "copy"]
               and [p.stream_index for p in spec_rd.packets] == [2, 4],
               spec_rd.arguments())
    else:
        record("l3.p4a.真实 A7M5 素材存在", False, "testsets 素材不存在")
