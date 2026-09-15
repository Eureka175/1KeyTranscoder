#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任意 reference 延迟矫正 (core/audio_sync) 单元测试。"""

from __future__ import annotations

import json
from typing import Any
from ..paths import FFMPEG
from ..fixtures.audio import P3A_SR
from ..paths import WORK
from ..fixtures.plans import _p3a_ready
from ..fixtures.audio import _sync_content
from ..fixtures.plans import _sync_measure
from ..fixtures.plans import _sync_plan
from ..fixtures.plans import _sync_reader
from ..fixtures.plans import _sync_streams
from ..paths import record
from ..paths import section

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

SYNC_ALGO_VERSION = "2.3.0-p1"


def _sync_basic() -> None:
    """§algorithm: constant offset 估计 + 与既有符号约定一致 + timeline 落点。"""
    from core.audio_sync import (
        AudioSyncError, apply_sync_result, clear_sync_result, estimate_sync,
        plan_from_plan, plan_sync,
        REASON_REFERENCE_MISSING,
    )

    d = WORK / "sync"
    d.mkdir(parents=True, exist_ok=True)

    # ---- reference 必须是显式 AudioChannel, 没有隐式默认 -----------------
    from core.audio_models import SyncStatus

    missing_reason = None
    try:
        plan_sync("")
    except AudioSyncError as exc:
        missing_reason = exc.reason
    record("sync.空 reference -> reference_missing (不存在隐式默认 reference)",
           missing_reason == REASON_REFERENCE_MISSING, f"{missing_reason}")

    # ---- 恒定偏移标定: 9 个偏移值, 一次多声道 fixture, 一次解码 ----------
    # 一个 reference + N targets 正是本 API 的形态, 因此把 9 个偏移值放进
    # 一条 10 声道素材 (c0 = reference 延迟 0, c1..c9 = 各偏移值), 一次
    # 解码即可标定全部偏移, 且不改变被测语义。
    from core.audio_wav import WavFormat, write_wav

    content = (np.random.default_rng(777).standard_normal(28800) * 0.25
               ).astype(np.float32)
    n_samples = 48000
    base = 9600
    multi = np.zeros((n_samples, 1 + len(SYNC_OFFSET_MATRIX)),
                     dtype=np.float32)
    for ch, delay in enumerate((0,) + tuple(SYNC_OFFSET_MATRIX)):
        b = base + int(delay)
        lo, hi = max(0, b), min(n_samples, b + len(content))
        multi[lo:hi, ch] = content[lo - b: hi - b]
    matrix_path = d / "matrix.wav"
    write_wav(matrix_path, [multi], sample_rate=P3A_SR,
              channel_count=multi.shape[1], sample_format=WavFormat.FLOAT32,
              frame_count=n_samples, overwrite=True)

    plan_m = _sync_plan([{"source_id": "cam", "path": matrix_path,
                          "channels": multi.shape[1]}])
    reader_m = _sync_reader(plan_m, d / "matrix_work")
    targets_m = [f"cam:s0:c{i}" for i in range(1, multi.shape[1])]
    got_m = _sync_measure(plan_m, "cam:s0:c0", work=d / "matrix_work",
                          targets=targets_m, reader=reader_m)
    exact = {
        delay: got_m["offsets"].get(f"cam:s0:c{i}")
        for i, delay in enumerate(SYNC_OFFSET_MATRIX, start=1)
    }
    record(f"sync.恒定偏移 {list(SYNC_OFFSET_MATRIX)} 全部 sample-exact",
           all(exact[k] == k for k in SYNC_OFFSET_MATRIX)
           and got_m["offsets"].get("cam:s0:c0") == 0,
           f"measured={exact} ref={got_m['offsets'].get('cam:s0:c0')}")
    reader_m.close()

    # ---- reference 自身 offset 恒为 0, 且不写回 -------------------------
    ref_p = _sync_content(d / "z_ref.wav", 0, content=content)
    tgt_p = _sync_content(d / "z_tgt.wav", 60, content=content)
    plan = _sync_plan([{"source_id": "cam", "path": ref_p},
                       {"source_id": "rec", "path": tgt_p}])
    res = estimate_sync(plan, plan_from_plan(plan, "cam:s0:c0"),
                        ffmpeg=FFMPEG, work_dir=d / "z")
    record("sync.reference 自身 offset 恒为 0 且不出现在 targets 里",
           res.offset_of("cam:s0:c0") == 0
           and res.estimate("cam:s0:c0") is None
           and [e.channel_id for e in res.estimates] == ["rec:s0:c0"],
           f"{res.summary()}")
    written = apply_sync_result(plan, res)
    record("sync.reference 不被写成 SUCCESS (不会自我二次修正)",
           written == 1
           and plan.channel("cam:s0:c0").sync.status is SyncStatus.NOT_PROCESSED
           and plan.channel("rec:s0:c0").sync.status is SyncStatus.SUCCESS
           and plan.channel("rec:s0:c0").sync.offset_samples == 60.0,
           f"written={written} ref={plan.channel('cam:s0:c0').sync.status} "
           f"tgt={plan.channel('rec:s0:c0').sync.status}")
    record("sync.estimate 写入后可由 clear_sync_result 完全撤销",
           clear_sync_result(plan, res) == 1
           and plan.channel("rec:s0:c0").sync.status
           is SyncStatus.NOT_PROCESSED,
           f"{plan.channel('rec:s0:c0').sync.status}")

    # ---- 估算不污染 source metadata / 不改身份 --------------------------
    before = {(c.id, c.stream_index, c.channel_index)
              for c in plan.all_channels()}
    res2 = estimate_sync(plan, plan_from_plan(plan, "cam:s0:c0"),
                         ffmpeg=FFMPEG, work_dir=d / "z2")
    apply_sync_result(plan, res2)
    after = {(c.id, c.stream_index, c.channel_index)
             for c in plan.all_channels()}
    record("sync.估计不改 channel 身份 / 不写 source metadata",
           before == after
           and plan.channel("cam:s0:c0").sync.status
           is SyncStatus.NOT_PROCESSED,
           f"identity_stable={before == after}")
    clear_sync_result(plan, res2)


def _sync_validation() -> None:
    """§error contract: 全部规划期约束都返回稳定 reason code。"""
    from core.audio_sync import (
        plan_from_plan, plan_sync, validate_sync_plan,
        REASON_REFERENCE_EQUALS_TARGET, REASON_SYNC_CHANNEL_NOT_FOUND,
        REASON_SYNC_DUPLICATE_TARGET, REASON_SYNC_SAMPLE_RATE_MISMATCH,
        REASON_REFERENCE_NOT_SELECTED, REASON_TARGET_NOT_SELECTED,
        REASON_SYNC_UNSUPPORTED_FORMAT,
    )

    d = WORK / "sync_val"
    d.mkdir(parents=True, exist_ok=True)

    a = _sync_content(d / "v_a.wav", 0)
    b = _sync_content(d / "v_b.wav", 10)
    plan = _sync_plan([{"source_id": "cam", "path": a},
                       {"source_id": "rec", "path": b}])

    def reasons(check: Any) -> list[str]:
        return [i["reason"] for i in validate_sync_plan(plan, check)]

    record("sync.reference 同时出现在 targets -> reference_equals_target",
           REASON_REFERENCE_EQUALS_TARGET in reasons(
               plan_sync("cam:s0:c0", ["cam:s0:c0"])),
           f"{reasons(plan_sync('cam:s0:c0', ['cam:s0:c0']))}")
    record("sync.重复 target -> sync_duplicate_target",
           REASON_SYNC_DUPLICATE_TARGET in reasons(
               plan_sync("cam:s0:c0", ["rec:s0:c0", "rec:s0:c0"])),
           f"{reasons(plan_sync('cam:s0:c0', ['rec:s0:c0', 'rec:s0:c0']))}")
    record("sync.unknown reference/target -> sync_channel_not_found",
           REASON_SYNC_CHANNEL_NOT_FOUND in reasons(
               plan_sync("ghost:s0:c0", ["rec:s0:c0"]))
           and REASON_SYNC_CHANNEL_NOT_FOUND in reasons(
               plan_sync("cam:s0:c0", ["ghost:s0:c0"])),
           f"{reasons(plan_sync('ghost:s0:c0', ['rec:s0:c0']))}")

    # selection: reference / target 被排除 -> 稳定错误, 且**不**回原 source 找回
    from core.audio_plan import AudioPlanner

    excl = _sync_plan([{"source_id": "cam", "path": a},
                       {"source_id": "rec", "path": b},
                       {"source_id": "ext", "path": _sync_content(d / "v_c.wav", 5)}])

    def excl_reasons(check: Any) -> list[str]:
        return [i["reason"] for i in validate_sync_plan(excl, check)]

    AudioPlanner(excl).exclude_channels("cam:s0:c0")
    ref_reasons = excl_reasons(plan_sync("cam:s0:c0", ["rec:s0:c0"]))
    tgt_reasons = excl_reasons(plan_sync("rec:s0:c0", ["cam:s0:c0"]))
    record("sync.reference 未被 selection 保留 -> reference_not_selected",
           REASON_REFERENCE_NOT_SELECTED in ref_reasons
           and "cam:s0:c0" not in excl.selected_channels
           and "cam:s0:c0" in [c.id for c in excl.all_channels()],
           f"{ref_reasons} selected={excl.selected_channels}")
    record("sync.target 未被 selection 保留 -> target_not_selected",
           REASON_TARGET_NOT_SELECTED in tgt_reasons,
           f"{tgt_reasons}")
    record("sync.被排除的声道不会从原 source 里被偷偷找回",
           "cam:s0:c0" not in excl.selected_channels
           and "cam:s0:c0" in [c.id for c in excl.all_channels()]
           and set(excl.selected_channels) == {"rec:s0:c0", "ext:s0:c0"},
           f"present={[c.id for c in excl.all_channels()]} "
           f"selected={excl.selected_channels}")

    # 采样率: 48k reference + 44.1k target -> 明确失败, 不偷偷 resample
    mism = _sync_plan([{"source_id": "cam", "path": a}], sample_rate=48000)
    from core.audio_models import (
        AudioSource as _AS, AudioTrackBuilder as _ATB,
    )

    ext_streams = _sync_streams("ext", sample_rate=44100)
    mism.input_tracks.extend(_ATB(ext_streams, source_id="ext").tracks())
    mism.sources.append(_AS(source_id="ext",
                            path=str(_sync_content(d / "v_44k.wav", 0,
                                                   sample_rate=44100)),
                            streams=ext_streams))
    mism.selected_tracks = [t.track_id for t in mism.input_tracks]
    mism.selected_channels = [c.id for c in mism.all_channels()]
    rate_reasons = [i["reason"] for i in validate_sync_plan(
        mism, plan_sync("cam:s0:c0", ["ext:s0:c0"]))]
    record("sync.48k reference + 44.1k target -> sync_sample_rate_mismatch "
           "(不 resample)",
           REASON_SYNC_SAMPLE_RATE_MISMATCH in rate_reasons,
           f"{rate_reasons}")

    # 采样率未知 (模型里没有事实) -> 同样拒绝, 不猜
    unknown_streams = _sync_streams("nos", sample_rate=0)
    unknown = _sync_plan([{"source_id": "cam", "path": a}])
    unknown.input_tracks.extend(_ATB(unknown_streams, source_id="nos").tracks())
    unknown.sources.append(_AS(
        source_id="nos", path=str(_sync_content(d / "v_norate.wav", 0)),
        streams=unknown_streams,
    ))
    unknown.selected_tracks = [t.track_id for t in unknown.input_tracks]
    unknown.selected_channels = [c.id for c in unknown.all_channels()]
    unk_reasons = [i["reason"] for i in validate_sync_plan(
        unknown, plan_sync("cam:s0:c0", ["nos:s0:c0"]))]
    record("sync.采样率未知 -> sync_unsupported_format (不猜采样率)",
           REASON_SYNC_UNSUPPORTED_FORMAT in unk_reasons,
           f"{unk_reasons}")


def _sync_reference_permutation() -> None:
    """§核心不变量: reference 任意 + 换 reference 后全体 offset 一致平移。"""
    from core.audio_sync import plan_from_plan

    d = WORK / "sync_perm"
    d.mkdir(parents=True, exist_ok=True)

    content = (np.random.default_rng(31337).standard_normal(28800) * 0.25
               ).astype(np.float32)
    # A 基准; B 晚到 60; C 早到 47; D 晚到 83 —— 覆盖正/负/零
    delays = {"A": 0, "B": 60, "C": -47, "D": 83}
    specs = []
    for sid, delay in delays.items():
        specs.append({
            "source_id": sid,
            "path": _sync_content(d / f"p_{sid}.wav", delay, content=content),
            "source_type": "wav" if sid == "C" else "media",
        })
    # 完整 4×4 表: 每次显式给出"除 reference 外的全部声道"作 target,
    # 这样每个 reference 都有全部 4 个 key, 便于逐对比较 (reference 自身
    # 不在 target 集合里, 其 offset 由 result.offset_of() 补 0)。
    # 同一计划连续测 4 次 reference -> 解码只做一次 (reader 复用)。
    cid = {sid: f"{sid}:s0:c0" for sid in delays}
    all_ids = list(cid.values())
    plan_p = _sync_plan(specs)
    reader_p = _sync_reader(plan_p, d / "perm_work")
    off: dict[str, dict[str, int]] = {}
    for sid in ("A", "B", "C", "D"):
        targets = [c for c in all_ids if c != cid[sid]]
        got = _sync_measure(plan_p, cid[sid], work=d / "perm_work",
                            targets=targets, reader=reader_p)
        table = dict(got["offsets"])
        table[cid[sid]] = 0              # reference 自身恒 0
        off[cid[sid]] = table
    reader_p.close()

    record("sync.任意一路都能当 reference (A/B/C/D 各当一次, 全部成功)",
           all(set(off[r]) == set(cid.values()) for r in off)
           and all(off[r][r] == 0 for r in off),
           f"{ {r: off[r] for r in (cid['A'], cid['B'])} }")

    # 语义基准: reference=A 时 target B 的测量延迟 (+60 = B 晚到)
    d_ba = off[cid["A"]][cid["B"]]
    record("sync.测量值语义: reference=A 时 B = +60 (B 晚到 60 样本)",
           d_ba == 60, f"delay(B|A)={d_ba}")

    # 不变量 1 (反对称): off(A|B) == -off(B|A)
    anti = [(p, q) for p in off for q in off[p]
            if off[p][q] != -off[q][p]]
    record("sync.reference 置换反对称: off(A|B) == -off(B|A) 全对",
           not anti, f"violations={anti}")

    # 不变量 2 (坐标平移): off(C|B) == off(C|A) - off(B|A)
    #   —— 严格等价于需求书写的 off(C|B) == off(C|A) + off(A|B)
    #      (因为 off(A|B) == -off(B|A) 已由不变量 1 钉住)
    transit = [
        (p, q, r) for p in off for q in off[p] for r in off[p]
        if q != r and off[q][r] != off[p][r] - off[p][q]
    ]
    record("sync.reference 置换坐标平移: off(C|B) == off(C|A) - off(B|A)",
           not transit, f"violations={transit[:4]}")

    # 需求书写形式的等价性 (显式断言两种写法给出同一结果)
    lhs = off[cid["B"]][cid["C"]]
    rhs_stated = off[cid["A"]][cid["C"]] + off[cid["B"]][cid["A"]]
    rhs_shift = off[cid["A"]][cid["C"]] - off[cid["A"]][cid["B"]]
    record("sync.平移公式两种等价写法给出同一结果 (需求形式已核对)",
           lhs == rhs_stated == rhs_shift,
           f"off(C|B)={lhs}  off(C|A)+off(A|B)={rhs_stated}  "
           f"off(C|A)-off(B|A)={rhs_shift}")

    # 逐 sample 精确: 参考 A 时的期望值集合
    record("sync.参考 A: B=+60 / C=-47 / D=+83 (sample-exact)",
           off[cid["A"]][cid["B"]] == 60
           and off[cid["A"]][cid["C"]] == -47
           and off[cid["A"]][cid["D"]] == 83,
           f"{off[cid['A']]}")


def _sync_source_order() -> None:
    """§source-order invariance: 排列 source 不得改变任何结果。"""
    d = WORK / "sync_order"
    d.mkdir(parents=True, exist_ok=True)

    content = (np.random.default_rng(4242).standard_normal(28800) * 0.25
               ).astype(np.float32)
    delays = {"A": 0, "B": 60, "C": -47}
    specs = [{"source_id": s,
              "path": _sync_content(d / f"o_{s}.wav", dl, content=content)}
             for s, dl in delays.items()]

    base = _sync_measure(_sync_plan(specs, order=["A", "B", "C"]),
                         "A:s0:c0", work=d / "ord0")
    perms = {
        "C,A,B": ["C", "A", "B"],
        "B,C,A": ["B", "C", "A"],
    }
    results = {"A,B,C": base["offsets"]}
    for label, order in perms.items():
        results[label] = _sync_measure(
            _sync_plan(specs, order=order), "A:s0:c0",
            work=d / f"ord_{label.replace(',', '')}",
        )["offsets"]
    record("sync.source 排列不影响结果 (channel identity 相同 -> 值完全一致)",
           len({json.dumps(v, sort_keys=True) for v in results.values()}) == 1,
           f"{json.dumps(results, ensure_ascii=False)}")

    # 显式禁止的隐式规则: sources[0] / stream0 / first_audio 都不是 reference
    plan0 = _sync_plan(specs, order=["C", "A", "B"])
    got = _sync_measure(plan0, "A:s0:c0", work=d / "ord_expl")
    # 首来源是 C, 但本次任务的 reference 是显式给出的 A。
    # C 的内容比 A **早到** 47 样本, 因此 off(C|A) = -47 —— 与
    # reference=A 时 B=+60 同一坐标系 (负 = 比 reference 早到)。
    # 若"首来源即 reference"这条隐式规则存在, 这里会得到 C=0 / A=+47。
    record("sync.首来源/首流不会被隐式当成 reference",
           plan0.sources[0].source_id == "C"
           and got["offsets"]["A:s0:c0"] == 0
           and got["offsets"]["C:s0:c0"] == -47
           and got["offsets"]["C:s0:c0"] != 0,
           f"first_source={plan0.sources[0].source_id} offsets={got['offsets']}")


def _sync_cross_source_and_identity() -> None:
    """跨来源 (media/WAV 互为 reference) + 同流内声道独立。"""
    from core.audio_wav import read_wav

    d = WORK / "sync_cross"
    d.mkdir(parents=True, exist_ok=True)

    content = (np.random.default_rng(909).standard_normal(28800) * 0.25
               ).astype(np.float32)
    cam_p = _sync_content(d / "c_cam.wav", 0, content=content)
    rec_p = _sync_content(d / "c_rec.wav", 75, content=content,
                          channels=2, channel=1)
    ext_p = _sync_content(d / "c_ext.wav", -33, content=content)

    def mk(content2: bool = False) -> Any:
        return _sync_plan([
            {"source_id": "camera", "path": cam_p},
            {"source_id": "recorder", "path": rec_p,
             "source_type": "wav", "channels": 2},
            {"source_id": "external", "path": ext_p, "source_type": "wav"},
        ])

    # camera <-> recorder <-> external 六个方向
    pairs = [
        ("camera:s0:c0", "recorder:s0:c1", 75),
        ("recorder:s0:c1", "camera:s0:c0", -75),
        ("camera:s0:c0", "external:s0:c0", -33),
        ("external:s0:c0", "camera:s0:c0", 33),
        ("recorder:s0:c1", "external:s0:c0", -108),
        ("external:s0:c0", "recorder:s0:c1", 108),
    ]
    measured: dict[str, int | None] = {}
    for ref, tgt, _expect in pairs:
        got = _sync_measure(mk(), ref, work=d / f"x_{ref[0]}{tgt[0]}")
        measured[f"{ref}->{tgt}"] = got["offsets"].get(tgt)
    record("sync.跨来源任意方向 (camera/recorder/WAV 互为 reference)",
           all(measured[f"{r}->{t}"] == e for r, t, e in pairs),
           f"{measured}")

    # 同一 stream 内 c0/c1 必须独立 (recorder 的 c1 有内容, c0 是静音)
    got = _sync_measure(mk(), "camera:s0:c0", work=d / "x_ids",
                        targets=["recorder:s0:c0", "recorder:s0:c1"])
    rec = got["result"]
    c0 = rec.estimate("recorder:s0:c0")
    c1 = rec.estimate("recorder:s0:c1")
    record("sync.同一 stream 内 c0/c1 保持独立 (静音声道不被当成 c1 的结果)",
           c0 is not None and c1 is not None
           and c1.offset_samples == 75
           and c0.status != "success",
           f"c0={c0.status if c0 else None}/{c0.reason if c0 else None} "
           f"c1={c1.offset_samples if c1 else None}")


def _sync_timeline_alignment() -> None:
    """§timeline integration: 估计 -> AudioChannel.sync -> AudioTimeline -> 落点。"""
    from core.audio_sync import estimate_sync, plan_from_plan, apply_sync_result
    from core.audio_process import run_audio_render
    from core.audio_wav import read_wav

    d = WORK / "sync_tl"
    d.mkdir(parents=True, exist_ok=True)

    # 4CH 单流: 每个声道不同延迟 —— 覆盖 "一条流内多声道各自矫正"
    n = 48000
    base = 9600
    rng = np.random.default_rng(5150)
    chans = 4
    delays = (0, 60, -47, 83)
    content = (rng.standard_normal(n - 2 * base) * 0.25).astype(np.float32)
    x = np.zeros((n, chans), dtype=np.float32)
    for ch, dl in enumerate(delays):
        b = base + dl
        lo, hi = max(0, b), min(n, b + len(content))
        x[lo:hi, ch] = content[lo - b: hi - b]
    from core.audio_wav import WavFormat, write_wav

    path = d / "four_ch.wav"
    write_wav(path, [x], sample_rate=P3A_SR, channel_count=chans,
              sample_format=WavFormat.FLOAT32, frame_count=n, overwrite=True)

    def mk(ch: int = 4) -> Any:
        return _sync_plan([{"source_id": "cam", "path": path,
                            "channels": ch}])

    def starts(arr: Any) -> list[int]:
        return [int(np.nonzero(np.abs(arr[:, i]) > 1e-6)[0][0])
                for i in range(arr.shape[1])]

    plan = mk()
    r0 = run_audio_render(plan, ffmpeg=FFMPEG, work_dir=d / "w0",
                          output_path=d / "pre.wav", chunk_frames=4096,
                          overwrite=True, sample_format=WavFormat.FLOAT32)
    a0, _ = read_wav(d / "pre.wav")
    pre_starts = starts(a0)
    record("sync.timeline 前: 4CH 各自停在原始位置 (未对齐)",
           pre_starts == [base + dl for dl in delays],
           f"starts={pre_starts} window={(r0.timeline.start_sample, r0.timeline.end_sample)}")

    # reference = CH1 (c0); 其余三路被矫正
    for ref in ("cam:s0:c0", "cam:s0:c2"):
        plan = mk()
        res = estimate_sync(plan, plan_from_plan(plan, ref),
                            ffmpeg=FFMPEG, work_dir=d / f"e_{ref[-1]}")
        written = apply_sync_result(plan, res)
        out = d / f"post_{ref[-1]}.wav"
        r1 = run_audio_render(plan, ffmpeg=FFMPEG, work_dir=d / f"r_{ref[-1]}",
                              output_path=out, chunk_frames=4096,
                              overwrite=True, sample_format=WavFormat.FLOAT32)
        a1, _ = read_wav(out)
        st = starts(a1)
        same = all(np.allclose(a1[:, i], a1[:, 0], atol=1e-6)
                   for i in range(a1.shape[1]))
        record(f"sync.reference={ref}: 四声道内容落到同一 timeline 位置",
               r1.ok and written == 3 and len(set(st)) == 1 and same
               and res.offset_of(ref) == 0,
               f"starts={st} written={written} "
               f"offsets={[(ct.channel_id, ct.offset_samples) for ct in r1.timeline.channels]}")
        record(f"sync.reference={ref}: reference 自身 offset=0 未被二次修正",
               all(ct.offset_samples == 0 for ct in r1.timeline.channels
                   if ct.channel_id == ref),
               f"{[(ct.channel_id, ct.offset_samples) for ct in r1.timeline.channels]}")

    # 2CH 单流: 两个声道各自独立估计 (2×2CH 场景的基本单元)
    plan2 = _sync_plan([
        {"source_id": "camA", "path": path, "channels": 2},
    ])
    got = _sync_measure(plan2, "camA:s0:c0", work=d / "two2")
    record("sync.2 声道单流: 每声道独立估计 (2×2CH 的基本单元)",
           got["offsets"].get("camA:s0:c1") == 60,
           f"{got['offsets']}")

    # 4×mono (四条独立流)
    mono_paths = [
        _sync_content(d / f"mono_{i}.wav", dl, content=content)
        for i, dl in enumerate(delays)
    ]
    mono_specs = [{"source_id": f"m{i}", "path": p}
                  for i, p in enumerate(mono_paths)]
    plan4 = _sync_plan(mono_specs)
    got4 = _sync_measure(plan4, "m0:s0:c0", work=d / "fourmono")
    record("sync.4×mono (四条独立流): 任意一路可当 reference",
           got4["offsets"].get("m1:s0:c0") == 60
           and got4["offsets"].get("m2:s0:c0") == -47
           and got4["offsets"].get("m3:s0:c0") == 83,
           f"{got4['offsets']}")


SYNC_OFFSET_MATRIX = (0, 1, 7, 60, 1000, -1, -7, -60, -1000)


def l1_audio_sync() -> None:
    """任意 reference 的 constant delay 矫正 (真实解码估计, 真 ffmpeg)。"""
    if not _p3a_ready("sync"):
        return

    section("L1 任意 reference 延迟矫正 (audio sync)")
    _sync_basic()
    _sync_validation()
    _sync_reference_permutation()
    _sync_source_order()
    _sync_cross_source_and_identity()
    _sync_timeline_alignment()
