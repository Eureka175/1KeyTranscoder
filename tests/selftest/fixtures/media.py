#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实素材 / 输入副本 fixture.

`testsets/` 只读, 全部输入副本建在 `work/autotest/` 下。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from ..paths import IN_DIR
from ..paths import MB
from ..paths import ROOT
from ..paths import sh

def _stage_inputs() -> dict[str, Path]:
    """自建输入副本; 返回 case -> 输入目录. testsets 只读."""
    shutil.rmtree(IN_DIR, ignore_errors=True)
    IN_DIR.mkdir(parents=True, exist_ok=True)
    cases: dict[str, Path] = {}

    sony_dir = IN_DIR / "sony"
    sony_dir.mkdir()
    shutil.copy2(
        ROOT / "testsets" / "a7m4_4k30p_264_hi422p_xavcs" / "C9037.MP4",
        sony_dir / "C9037.MP4",
    )
    cases["sony"] = sony_dir

    dji_dir = IN_DIR / "dji"
    dji_dir.mkdir()
    shutil.copy2(
        ROOT / "testsets" / "action4_4k_4x3_30+60"
        / "DJI_20260830095031_0009_D.MP4",
        dji_dir / "DJI_20260830095031_0009_D.MP4",
    )
    cases["dji"] = dji_dir

    # 经典路径输入: DJI 素材剥离全部元数据轨 (仅视频+音频)
    classic_dir = IN_DIR / "classic"
    classic_dir.mkdir()
    classic_src = classic_dir / "classic_test.MP4"
    r = sh(MB, "-new", classic_src,
           "-add", str(cases["dji"] / "DJI_20260830095031_0009_D.MP4")
           + "#video",
           "-add", str(cases["dji"] / "DJI_20260830095031_0009_D.MP4")
           + "#2")
    if r.returncode == 0 and classic_src.is_file():
        cases["classic"] = classic_dir

    # 故障注入: 截断文件
    trunc_dir = IN_DIR / "truncated"
    trunc_dir.mkdir()
    full = cases["dji"] / "DJI_20260830095031_0009_D.MP4"
    data = full.read_bytes()[:100_000]
    (trunc_dir / "truncated.MP4").write_bytes(data)
    cases["truncated"] = trunc_dir

    # 故障注入: 尾部垃圾 (reader 失败 -> strip 回退)
    junk_dir = IN_DIR / "trailing_junk"
    junk_dir.mkdir()
    junk = full.read_bytes() + b"\x00" * 65536
    (junk_dir / "trailing_junk.MP4").write_bytes(junk)
    cases["trailing_junk"] = junk_dir

    return cases


def _run_1kt(input_dir: Path, out_dir: Path, *extra: str,
             timeout: int = 1800) -> tuple[int, str]:
    r = sh(sys.executable, ROOT / "1kt.py",
           "--input", input_dir, "--output", out_dir, *extra,
           "--headless", timeout=timeout)
    return r.returncode, (r.stdout or "")
