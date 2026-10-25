#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档修复工具 `archive_repair.py` 的检查器/状态机回归 (2026-10-26).

这组用例钉住本轮升级的四件事:

1. 检查器不再把 DJI `djmd/dbgi/tmcd` 当 Sony `rtmd` 要求 `tref/cdsc`；
2. `CTR-07/08` 只对线性 PCM 条目做位深/包长一致性检查 (AAC/Opus 不适用)；
3. `.part.` 半成品与无 moov 的坏文件有独立状态, 不混进 FAIL；
4. `repair` 的判定词汇: `container_fixed` 只有真正改过且复检无 FAIL 才算
   `REPAIRED`；`UNREADABLE`/`PARTIAL` 不伪装成成功或失败。
"""

from __future__ import annotations

from pathlib import Path

from ..paths import record, section

import archive_repair as ar


def _facts(**over) -> dict:
    base = {
        "top_level": ["ftyp", "moov", "mdat"],
        "uuid": [],
        "tref_cdsc": [],
        "nrtm": False,
        "ftyp_major": "isom",
        "ftyp_compatible": ["isom"],
        "moov_first": True,
        "tracks": [{"handler": "vide", "entry": "hvc1"}],
        "mvhd": {"timescale": 60000, "duration": 60000, "seconds": 1.0},
        "meta_scopes": [],
        "audio_entries": [],
    }
    base.update(over)
    return base


def _doc(**over) -> dict:
    base = {"format": {"duration": "1.0"},
            "streams": [{"codec_type": "video", "duration": "1.0"}]}
    base.update(over)
    return base


def _items(path: Path, facts: dict, doc: dict) -> dict[str, dict]:
    return {i["id"]: i for i in ar.check_container(path, facts, doc)}


def l1_archive_repair_checker() -> None:
    section("archive_repair 检查器纠偏 / 状态机")
    missing = Path("__definitely_missing__.mp4")

    # ---- CTR-02: 只有 rtmd 要求 tref/cdsc -------------------------------
    dj = _facts(tracks=[{"handler": "vide", "entry": "hvc1"},
                        {"handler": "meta", "entry": "djmd"},
                        {"handler": "meta", "entry": "dbgi"},
                        {"handler": "tmcd", "entry": "tmcd"}])
    items = _items(missing, dj, _doc())
    record("arch: DJI 数据轨 (djmd/dbgi/tmcd) 不要求 cdsc",
           items["CTR-02"]["verdict"] == ar.SKIP,
           f"{items['CTR-02']['verdict']} {items['CTR-02']['detail']}")

    rtmd = _facts(tracks=[{"handler": "vide", "entry": "hvc1"},
                          {"handler": "meta", "entry": "rtmd"}])
    items = _items(missing, rtmd, _doc())
    record("arch: rtmd 缺 cdsc -> FAIL",
           items["CTR-02"]["verdict"] == ar.FAIL,
           f"{items['CTR-02']['verdict']} {items['CTR-02']['detail']}")
    rtmd_ok = _facts(tracks=rtmd["tracks"],
                     tref_cdsc=[{"handler": "meta", "refs": [1]}])
    items = _items(missing, rtmd_ok, _doc())
    record("arch: rtmd + cdsc -> PASS",
           items["CTR-02"]["verdict"] == ar.PASS,
           f"{items['CTR-02']['verdict']} {items['CTR-02']['detail']}")

    # ---- CTR-07: 非 PCM 条目不做 in24 陷阱检查 --------------------------
    aac = _facts(audio_entries=[{"handler": "soun", "fourcc": "mp4a"}])
    items = _items(missing, aac, _did := _doc())
    record("arch: AAC (mp4a) 不触发 CTR-07 in24 检查",
           items["CTR-07"]["verdict"] == ar.SKIP,
           f"{items['CTR-07']['verdict']} {items['CTR-07']['detail']}")
    pcm_in24 = _facts(audio_entries=[{"handler": "soun", "fourcc": "in24"}])
    items = _items(missing, pcm_in24, _doc())
    record("arch: PCM in24 -> WARN (已知陷阱)",
           items["CTR-07"]["verdict"] == ar.WARN,
           f"{items['CTR-07']['verdict']} {items['CTR-07']['detail']}")

    # ---- 半成品命名 -----------------------------------------------------
    record("arch: .part.mov 识别为半成品",
           ar.is_partial_name(Path("clip.part.mov")) is True
           and ar.is_partial_name(Path("clip.mov")) is False)

    # ---- 时长检查与容器修复判定 ----------------------------------------
    bad_dur = _facts(mvhd={"timescale": 1000, "duration": 1200,
                           "seconds": 1.2})
    record("arch: mvhd/容器时长不一致触发可修判定",
           ar.container_fix_needed(missing, bad_dur, _doc()) is True)
    good = _facts()
    record("arch: 无容器问题时不触发修复",
           ar.container_fix_needed(missing, good, _doc()) is False)

    # ---- 判定词汇 -------------------------------------------------------
    record("arch: unreadable -> UNREADABLE",
           ar.verdict_of({"status": "unreadable"}) == ar.UNREADABLE)
    record("arch: partial -> PARTIAL",
           ar.verdict_of({"status": "partial"}) == ar.PARTIAL)
    record("arch: container_fixed + 无 FAIL -> REPAIRED",
           ar.verdict_of({"status": "container_fixed",
                          "container_checks": [],
                          "format_checks": []}) == "REPAIRED")
    record("arch: container_fixed + 有 FAIL -> FAILED",
           ar.verdict_of({"status": "container_fixed",
                          "container_checks": [{"id": "X", "verdict": ar.FAIL}],
                          "format_checks": []}) == "FAILED")
    record("arch: applied + metadata identical -> REPAIRED",
           ar.verdict_of({"status": "applied",
                          "metadata": {"identical": True}}) == "REPAIRED")
    record("arch: applied + metadata mismatch -> FAILED",
           ar.verdict_of({"status": "applied",
                          "metadata": {"identical": False}}) == "FAILED")
    record("arch: no_fix_needed -> NOOP",
           ar.verdict_of({"no_fix_needed": True}) == "NOOP")

    # ---- PCM_FOURCCS 集合语义 ------------------------------------------
    record("arch: PCM fourcc 集合含 ipcm/in24、不含 mp4a",
           "ipcm" in ar.PCM_FOURCCS and "in24" in ar.PCM_FOURCCS
           and "mp4a" not in ar.PCM_FOURCCS)
