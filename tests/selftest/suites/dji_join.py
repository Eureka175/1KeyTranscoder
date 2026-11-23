#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJI Action 分段连接 (1kt.py --dji-join) 契约测试.

L1: 纯检测/分组/暂存路径/CLI 开关 (无需真实素材, 毫秒级).
L2: 真实 20261114 / 20261115 素材上的分组检测 (素材不在时跳过).
L3: 用真实素材的 2 s 裁片跑一次真实 MP4Box 无损连接, 校验 CFR 视频、
    djmd/dbgi/tmcd 数据轨和 staging 树 (完整连接路径的轻量版).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from ..paths import FFPROBE, ROOT, record, section

#: 用户实测过的两个 DJI Action 4 分段目录 (L2/L3 用; 不在时显式跳过).
REAL_ROOTS = (
    Path(r"F:\Pictures\2026\202611\20261114"),
    Path(r"F:\Pictures\2026\202611\20261115"),
)
#: 只读样本: 最后一组 16 GB + 4 GB 的两段, L3 从中各裁 2 s.
REAL_GROUP = (
    Path(r"F:\Pictures\2026\202611\20261115\DJI_20261115164247_0025_D.MP4"),
    Path(r"F:\Pictures\2026\202611\20261115\DJI_20261115170843_0026_D.MP4"),
)

_BASE_START = datetime(2026, 11, 15, 16, 42, 47)
_VIDEO_SIG = ("hevc", 3840, 2880, "yuv420p10le", "30000/1001")
_AUDIO_SIG = ("aac", 2, 48000)


def _gpac_backend():
    """Prefer the repo's bundled GPAC, fall back to the installed one."""
    from preservation.gpac import GpacContainerBackend

    local = ROOT / "tools" / "GPAC"
    if (local / "mp4box.exe").is_file() or (local / "MP4Box.exe").is_file():
        return GpacContainerBackend(local)
    return GpacContainerBackend()


def _seg(
    index: int,
    start: datetime,
    duration: float = 600.0,
    *,
    parent: Path = Path(r"D:\src"),
    size_gb: float = 16.0,
    video_sig: tuple = _VIDEO_SIG,
    audio_sig: tuple = _AUDIO_SIG,
    data_tags: tuple[str, ...] = ("dbgi", "djmd", "tmcd"),
):
    from core.dji_join import Segment

    return Segment(
        path=parent / f"DJI_20261115164247_{index:04d}_D.MP4",
        start=start,
        index=index,
        duration=duration,
        size=int(size_gb * 1024 ** 3),
        video_signature=video_sig,
        audio_signature=audio_sig,
        data_tags=data_tags,
    )


def _load_1kt():
    """Import 1kt.py under a private module name (does not run main)."""
    if "1kt" in sys.modules:
        return sys.modules["1kt"]
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("kt_dji_join_mod",
                                                  ROOT / "1kt.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["kt_dji_join_mod"] = module
    spec.loader.exec_module(module)
    return module


def l1_dji_join_contract() -> None:
    section("L1 DJI 分段连接 (--dji-join) 契约")

    from core.dji_join import (
        JoinPlan,
        SegmentGroup,
        group_segments,
        parse_action_name,
        staged_dest,
        write_reports,
    )

    # ---- 文件名解析 ----
    got = parse_action_name(Path("DJI_20261114212755_0006_D.MP4"))
    record(
        "dji: 文件名解析 DJI_20261114212755_0006_D.MP4",
        got is not None and got[0] == datetime(2026, 11, 14, 21, 27, 55)
        and got[1] == 6,
        f"got={got}",
    )
    for bad in ("IMG_0001.MP4", "DJI_20261114212755_0006_X.MP4",
                "DJI_20261114212755_0006_D", "DJI_20261314212755_0006_D.MP4"):
        record(f"dji: 非 Action 文件名拒绝 {bad}",
               parse_action_name(Path(bad)) is None)

    # ---- 纯分组逻辑 ----
    t0 = _BASE_START
    chain = [_seg(25, t0), _seg(26, t0 + timedelta(seconds=600)),
             _seg(27, t0 + timedelta(seconds=1200))]
    groups, singles = group_segments(chain)
    record("dji: 3 段连续分段合并为 1 组",
           len(groups) == 1 and len(groups[0].segments) == 3 and not singles,
           f"groups={len(groups)} singles={len(singles)}")

    gap = [_seg(25, t0), _seg(26, t0 + timedelta(seconds=1800))]
    groups, singles = group_segments(gap)
    record("dji: 时间不连续 -> 不合并 (2 个独立文件)",
           not groups and len(singles) == 2,
           f"groups={len(groups)} singles={len(singles)}")

    small_first = [_seg(25, t0, size_gb=1.0),
                   _seg(26, t0 + timedelta(seconds=600))]
    groups, singles = group_segments(small_first)
    record("dji: 前段未接近 16 GB 切分点 -> 不合并", not groups)

    signature_mismatch = [
        _seg(25, t0),
        _seg(26, t0 + timedelta(seconds=600),
             video_sig=("hevc", 1920, 1080, "yuv420p", "30000/1001")),
    ]
    groups, singles = group_segments(signature_mismatch)
    record("dji: 视频参数不同 -> 不合并", not groups)

    no_dji_tag = [
        _seg(25, t0),
        _seg(26, t0 + timedelta(seconds=600), data_tags=("rtmd",)),
    ]
    groups, singles = group_segments(no_dji_tag)
    record("dji: 后段缺 djmd/dbgi -> 不合并", not groups)

    index_jump = [_seg(25, t0), _seg(27, t0 + timedelta(seconds=600))]
    groups, singles = group_segments(index_jump)
    record("dji: 编号不连续 -> 不合并", not groups)

    other_dir = [_seg(25, t0, parent=Path(r"D:\a")),
                 _seg(26, t0 + timedelta(seconds=600), parent=Path(r"D:\b"))]
    groups, singles = group_segments(other_dir)
    record("dji: 不同目录 -> 不合并", not groups)

    groups, singles = group_segments([_seg(25, t0)])
    record("dji: 单段不会自己成组 -> 独立文件",
           not groups and len(singles) == 1)

    # ---- 暂存路径命名 ----
    dest = staged_dest(
        Path(r"D:\src\sub\DJI_20261115164247_0025_D.MP4"),
        Path(r"D:\src"), Path(r"E:\out\.dji_join_work_x"),
    )
    record("dji: staging 路径保持相对目录 + __joined 后缀",
           dest == Path(
               r"E:\out\.dji_join_work_x\sub"
               r"\DJI_20261115164247_0025_D__joined.MP4"),
           f"got={dest}")

    up = SegmentGroup(name="g", segments=chain)
    info = up.as_dict()
    record("dji: SegmentGroup.as_dict 汇总时长/段数",
           info["segment_count"] == 3
           and abs(info["total_duration_sec"] - 1800.0) < 1e-6,
           f"{info['segment_count']} / {info['total_duration_sec']}s")

    # ---- 报告落盘 ----
    with tempfile.TemporaryDirectory(prefix="1kt-dji-l1-") as td:
        plan = JoinPlan(input_root=Path(r"D:\src"), sources=[], groups=[],
                        singles=[])
        paths = write_reports(report_dir=Path(td), plan=plan)
        payload = json.loads(paths["plan"].read_text(encoding="utf-8"))
        record("dji: 计划报告 JSON 可解析且含 groups/sources",
               payload["sources"] == 0 and payload["groups"] == []
               and "generated_at" in payload,
               f"{paths['plan'].name}")

    # ---- CLI 开关: 默认关闭, --dji-join 才打开 ----
    kt = _load_1kt()
    old_argv = sys.argv
    try:
        sys.argv = ["1kt.py", "--input", ".", "--output", "."]
        defaults = kt.parse_args()
        record("dji: --dji-join 默认关闭 (默认 False)",
               defaults.dji_join is False,
               f"got={defaults.dji_join}")

        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--dji-join"]
        on = kt.parse_args()
        record("dji: --dji-join 打开连接开关", on.dji_join is True)
        record("dji: --dji-join-keep-temp 默认关闭",
               on.dji_join_keep_temp is False)

        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--dji-join", "--dji-join-keep-temp"]
        both = kt.parse_args()
        record("dji: --dji-join-keep-temp 独立解析",
               both.dji_join and both.dji_join_keep_temp)

        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--dji-join", "--async-integrity"]
        async_on = kt.parse_args()
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--dji-join", "--no-async-integrity"]
        async_off = kt.parse_args()
        record("dji: --async-integrity 默认开启且可显式关闭",
               defaults.async_integrity is True
               and async_on.async_integrity is True
               and async_off.async_integrity is False)

        # Async integrity contract: encode-complete releases the next group's
        # encode gate without reclaiming the joined source.
        import threading as _threading
        from core.dji_join import ProgressiveStager

        st = object.__new__(ProgressiveStager)
        st.groups = [None, None, None]
        st._index = {Path(f"dji-g{i}").resolve(): i for i in range(3)}
        st._ready = {0, 1, 2}
        st._finished = set()
        st._released = set()
        st._closed = False
        st._join_error = None
        st._cond = _threading.Condition()
        st._allowed = 1
        st.log = lambda _m: None
        st.note_encode_complete(Path("dji-g0"))
        record("dji: async encode-complete 释放下一组但不回收 joined 源",
               st._released == {0} and st._allowed == 2
               and 0 not in st._finished,
               f"released={st._released} allowed={st._allowed}")

        # ---- 与 --no-metadata 的相容性 ----
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--dji-join", "--no-metadata"]
        both_meta = kt.parse_args()
        record("dji: --dji-join + --no-metadata 可同时解析",
               both_meta.dji_join is True and both_meta.no_metadata is True)

        from core.batch_hw import BatchCtx

        batch_source = (ROOT / "core" / "batch_hw.py").read_text(
            encoding="utf-8"
        )
        kt_source = (ROOT / "1kt.py").read_text(encoding="utf-8")
        record("dji: 硬件路径已接入 --no-metadata (BatchCtx.no_metadata)",
               "no_metadata" in BatchCtx.__dataclass_fields__
               and 'no_metadata=bool(getattr(args, "no_metadata", False))'
               in kt_source)
        policy_source = (ROOT / "preservation" / "policy.py").read_text(
            encoding="utf-8"
        )
        record("dji: --no-metadata 优先于 --dji-join 的默认保留",
               "resolve_metadata_plan(" in batch_source
               and "forced_join=(" in batch_source
               and "not ctx.no_metadata" in batch_source
               and "metadata_plan.is_sony and metadata_plan.preserve"
               in batch_source
               and "metadata_plan.is_dji and metadata_plan.preserve"
               in batch_source)
        record("dji: --no-metadata 的 DJI/Sony 策略走普通转码路径",
               "no_metadata: %s preservation skipped" in policy_source
               and "ordinary transcode path used" in policy_source
               and "if no_metadata:" in policy_source
               and 'reason="no_metadata"' in policy_source)

        # ---- --no-metadata 必须真正剥掉数据轨 (tmcd 也不能漏) ----
        from core.batch_hw import _is_metadata_track

        metadata_cases = (
            ({"handler": "meta", "entry": "djmd"}, True),
            ({"handler": "meta", "entry": "dbgi"}, True),
            ({"handler": "tmcd", "entry": "tmcd"}, True),
            ({"handler": "vide", "entry": "hvc1"}, False),
            ({"handler": "soun", "entry": "mp4a"}, False),
            ({"handler": "subt", "entry": "tx3g"}, False),
        )
        wrong = [c for c, want in metadata_cases
                 if _is_metadata_track(c) is not want]
        record("dji: 元数据轨判定 (meta/tmcd 丢, 视频/音频/字幕留)",
               not wrong, f"wrong={wrong}")
        record(
            "dji: --no-metadata 在容器重封装处剥掉数据轨",
            "drop_data_tracks=ctx.no_metadata" in batch_source
            and "container re-mux: dropping metadata track" in batch_source
            and "drop_data_tracks: bool = False" in batch_source,
        )
    finally:
        sys.argv = old_argv

    # ---- 冲突组合必须硬拒绝 (在工具链解析之前, 因此可以快速子进程验证) ----
    script = str(ROOT / "1kt.py")
    with tempfile.TemporaryDirectory(prefix="1kt-dji-conflict-") as td:
        out = str(Path(td) / "out")
        cases = (
            ("--repair", "not supported in --repair"),
            ("--retry-list", "cannot be combined with --retry-list"),
            ("--channel-sync-transparent", "not supported with "
                                           "--channel-sync-transparent"),
        )
        for flag, needle in cases:
            cmd = [sys.executable, script, "--input", str(ROOT),
                   "--output", out, "--dji-join", flag]
            if flag == "--retry-list":
                cmd.append(str(Path(td) / "missing.json"))
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120)
            combined = (proc.stdout or "") + (proc.stderr or "")
            record(f"dji: --dji-join + {flag} 被拒绝 (rc=2)",
                   proc.returncode == 2 and needle in combined,
                   f"rc={proc.returncode} err={combined.strip()[-120:]}")


def _assert_real_detection(root: Path, want_groups: int, want_singles: int,
                           want_segments: int) -> None:
    from core.dji_join import detect_plan
    from core.paths import discover_sources

    sources = discover_sources(root)
    plan = detect_plan(root, sources, FFPROBE, log=None)
    label = root.name
    record(f"dji-l2 {label}: 检出 {want_groups} 个分段组",
           len(plan.groups) == want_groups,
           f"got={len(plan.groups)} groups={[g.name for g in plan.groups]}")
    record(f"dji-l2 {label}: 独立文件 {want_singles} 个",
           len(plan.singles) == want_singles,
           f"got={len(plan.singles)}")
    record(f"dji-l2 {label}: 覆盖 {want_segments} 个分段",
           plan.grouped_source_count == want_segments,
           f"got={plan.grouped_source_count}")
    record(f"dji-l2 {label}: 每组均 >=2 段且时长为正",
           all(len(g.segments) >= 2 and g.duration > 0
               for g in plan.groups),
           f"{[len(g.segments) for g in plan.groups]}")
    record(f"dji-l2 {label}: 组名前缀来自首段",
           all(g.name.startswith(g.segments[0].path.stem)
               for g in plan.groups))


def l2_dji_join_detection() -> None:
    section("L2 DJI 分段检测 (真实 20261114 / 20261115 素材)")
    present = [r for r in REAL_ROOTS if r.is_dir()]
    if len(present) < 2:
        record("dji-l2: 真实素材不存在 -> 跳过", True,
               f"present={[str(p) for p in present]}")
        return
    # 用户实测语料: 14 日 8 个 DJI 文件 -> 3 组 (6 段) + 2 独立;
    # 15 日 13 个 -> 4 组 (11 段) + 2 独立 (0014/0024 长度不足 16 GB)。
    _assert_real_detection(REAL_ROOTS[0], 3, 2, 6)
    _assert_real_detection(REAL_ROOTS[1], 4, 2, 11)


def _split_clip(mp4box: Path, src: Path, dest: Path, seconds: float) -> bool:
    proc = subprocess.run(
        [str(mp4box), "-splitx", f"0:{seconds}", "-out", str(dest), str(src)],
        capture_output=True, text=True, timeout=600,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def l3_dji_join_real_media() -> None:
    section("L3 DJI 分段无损连接 (真实裁片 + MP4Box 全路径)")

    from core.dji_join import (
        JoinPlan,
        SegmentGroup,
        cleanup_staging,
        join_group,
        probe_segment,
        stage_plan,
        staged_dest,
    )
    from core.probe import probe_streams

    if not all(p.is_file() for p in REAL_GROUP):
        record("dji-l3: 真实分段样本不存在 -> 跳过", True,
               "sample not present")
        return
    if not FFPROBE.is_file():
        record("dji-l3: ffprobe 不可用 -> 跳过", True, str(FFPROBE))
        return
    try:
        gpac = _gpac_backend()
    except Exception as exc:                       # noqa: BLE001
        record("dji-l3: GPAC 不可用 -> 跳过", True, str(exc))
        return

    with tempfile.TemporaryDirectory(prefix="1kt-dji-l3-") as td:
        tmp = Path(td)
        clips: list[Path] = []
        ok = True
        for src in REAL_GROUP:
            clip = tmp / src.name
            if not _split_clip(gpac.mp4box, src, clip, 2.0):
                ok = False
                break
            clips.append(clip)
        record("dji-l3: 从真实分段裁出 2 s 测试片", ok,
               f"{[p.name for p in clips]}")
        if not ok:
            return

        segs = [probe_segment(FFPROBE, c) for c in clips]
        record("dji-l3: 裁片仍带 djmd/dbgi/tmcd 数据轨",
               all(s is not None
                   and {"djmd", "dbgi", "tmcd"} <= set(s.data_tags)
                   for s in segs),
               f"{[s.data_tags if s else None for s in segs]}")
        if any(s is None for s in segs):
            return

        group = SegmentGroup(name="l3-joined", segments=list(segs))
        joined = tmp / "joined.mp4"
        result = join_group(gpac, group, joined, FFPROBE)
        record("dji-l3: 真实裁片无损连接成功 (ok=True, 无错误)",
               result["ok"] is True,
               f"errors={result['errors']}")
        record("dji-l3: 连接后长度 = 各段之和 (误差 < 0.5 s)",
               abs(result["joined_duration_sec"] - group.duration) < 0.5,
               f"{result['joined_duration_sec']:.3f}s vs "
               f"{group.duration:.3f}s")
        record("dji-l3: 连接后三种数据轨完整",
               {"djmd", "dbgi", "tmcd"} <= set(result["data_tags"]),
               f"{result['data_tags']}")

        fmt, streams = probe_streams(FFPROBE, joined)
        video = next((s for s in streams if s.get("codec_type") == "video"),
                     {})
        want_frames = 0
        for seg in segs:
            seg_video = next(
                (st for st in seg.probe["streams"]
                 if st.get("codec_type") == "video"), {})
            want_frames += int(seg_video.get("nb_frames") or 0)
        record("dji-l3: 连接后视频为 CFR 30000/1001, time_base=1/30000",
               video.get("avg_frame_rate") == "30000/1001"
               and video.get("time_base") == "1/30000",
               f"{video.get('avg_frame_rate')} / {video.get('time_base')}")
        record("dji-l3: 连接后帧数 = 各段帧数之和 (无边界丢帧/补帧)",
               video.get("nb_frames") == str(want_frames),
               f"got={video.get('nb_frames')} want={want_frames}")

        # ---- staging 树: 连接文件 + 未分组文件硬链接 ----
        extra = tmp / "DJI_20261115153941_0024_D.MP4"
        extra.write_bytes(clips[0].read_bytes())
        plan = JoinPlan(
            input_root=tmp,
            sources=[clips[0], clips[1], extra],
            groups=[group],
            singles=[],
        )
        staging_root = tmp / ".dji_join_work_l3"
        staging = stage_plan(
            plan=plan, staging_root=staging_root, gpac=gpac,
            ffprobe=FFPROBE, log=None,
        )
        joined_dest = staged_dest(clips[0], tmp, staging_root)
        record("dji-l3: staging 树生成 1 个连接文件 + 1 个硬链接源",
               joined_dest.is_file() and extra.stat().st_size
               == (staging_root / extra.name).stat().st_size,
               f"sources={[p.name for p in staging.sources]}")
        record("dji-l3: staging 输入数 = 连接组数 + 独立文件数",
               len(staging.sources) == 2,
               f"{[p.name for p in staging.sources]}")
        cleanup_staging(staging_root)
        record("dji-l3: staging 树清理后不存在", not staging_root.exists())

        # ---- --no-metadata 的数据轨剥离 (真实连接文件, 视频/音频流复制) ----
        from core.batch_hw import place_final, remux_container_lossless

        no_drop = tmp / "remux_all.mp4"
        remux_container_lossless(
            gpac=gpac, src=joined, dst=no_drop, log=lambda _m: None,
        )
        _, all_tracks = gpac.parse_info(gpac.info(no_drop))
        all_entries = {str(t.get("entry") or "") for t in all_tracks}
        record("dji-l3: 普通容器重封装保留 djmd/dbgi/tmcd",
               {"djmd", "dbgi", "tmcd"} <= all_entries,
               f"{sorted(all_entries)}")

        stripped = tmp / "remux_stripped.mp4"
        remux_container_lossless(
            gpac=gpac, src=joined, dst=stripped, log=lambda _m: None,
            drop_data_tracks=True,
        )
        _, stripped_tracks = gpac.parse_info(gpac.info(stripped))
        handlers = sorted(str(t.get("handler") or "")
                          for t in stripped_tracks)
        record("dji-l3: --no-metadata 重封装后只剩视频+音频 (无 tmcd)",
               handlers == ["soun", "vide"], f"{handlers}")

        # place_final 是生产路径实际走的那一层: 同后缀 (.mp4->.mp4) 也必须
        # 重封装才能剥掉数据轨, 不能走"直接改名"。
        intermediate = tmp / "final_intermediate.mp4"
        intermediate.write_bytes(joined.read_bytes())
        final_out = tmp / "final_stripped.mp4"
        place_final(
            gpac=gpac, intermediate=intermediate, dst=final_out,
            work_dir=tmp, log=lambda _m: None, drop_data_tracks=True,
        )
        _, final_tracks = gpac.parse_info(gpac.info(final_out))
        final_handlers = sorted(str(t.get("handler") or "")
                                for t in final_tracks)
        record("dji-l3: place_final(drop_data_tracks) 同后缀也重封装剥轨",
               final_handlers == ["soun", "vide"]
               and not intermediate.exists(),
               f"handlers={final_handlers} "
               f"intermediate_gone={not intermediate.exists()}")
