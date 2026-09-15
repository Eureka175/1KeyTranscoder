#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 工具链: 真实工具版本 / 能力探测 / 旗标白名单 (不编码)。"""

from __future__ import annotations

from pathlib import Path
from ..paths import ROOT
from ..paths import record
from ..paths import section
from ..paths import sh

def l2_toolchain() -> None:
    from core.config import find_executable, find_hw_tool
    from encoders.caps import probe_backend
    from encoders.hw import known_flags
    from preservation.gpac import GpacContainerBackend
    from preservation.gyroflow import find_gyroflow
    section("L2 工具链")
    ffprobe = find_executable("ffprobe", ROOT)
    ffmpeg = find_executable("ffmpeg", ROOT)
    r = sh(ffprobe, "-version")
    record("tool.ffprobe 可用", r.returncode == 0
           and "ffprobe version" in (r.stdout or "").lower())
    r = sh(ffmpeg, "-version")
    record("tool.ffmpeg 可用", r.returncode == 0
           and "ffmpeg version" in (r.stdout or "").lower())
    # 必须使用项目自带 tools/ffmpeg.exe (PATH 老版本不支持 AV1 新特性)
    record("tool.ffmpeg 项目自带 (tools/)",
           ROOT.resolve() in Path(ffmpeg).resolve().parents,
           f"ffmpeg={ffmpeg}")
    r = sh(ffmpeg, "-hide_banner", "-encoders")
    enc_text = (r.stdout or "") + (r.stderr or "")
    record("tool.ffmpeg libsvtav1 编码器", "libsvtav1" in enc_text)
    record("tool.ffmpeg libvmaf 滤波器", "libvmaf" in
           ((sh(ffmpeg, "-hide_banner", "-filters").stdout or "")
            + (sh(ffmpeg, "-hide_banner", "-filters").stderr or "")))
    nvenc = find_hw_tool(ROOT, "NVEncC64.exe")
    qsv = find_hw_tool(ROOT, "QSVEncC64.exe")
    r = sh(nvenc, "--version")
    record("tool.NVEncC 版本", r.returncode == 0
           and "NVEncC" in (r.stdout or ""), (r.stdout or "")[:80].strip())
    r = sh(qsv, "--version")
    record("tool.QSVEncC 版本", r.returncode == 0
           and "QSVEncC" in (r.stdout or ""), (r.stdout or "")[:80].strip())

    caps_n = probe_backend(nvenc, "nvencc")
    record("caps.nvenc 实机探测",
           caps_n is not None and caps_n.codecs.get("hevc") is not None,
           f"device={getattr(caps_n, 'device', '?')}")
    if caps_n and caps_n.codecs.get("hevc"):
        hevc = caps_n.codecs["hevc"]
        record("caps.nvenc 实机 10bit/422",
               hevc.bit10 and hevc.csp_422 and hevc.bit10_422, f"{hevc}")
    caps_q = probe_backend(qsv, "qsvencc")
    record("caps.qsv 实机探测",
           caps_q is not None and caps_q.codecs.get("hevc") is not None,
           f"device={getattr(caps_q, 'device', '?')}")
    if caps_q and caps_q.codecs.get("hevc"):
        record("caps.qsv 实机 10bit", caps_q.codecs["hevc"].bit10)

    kn = known_flags(nvenc)
    need = {"colorprim", "transfer", "colormatrix", "colorrange",
            "master-display", "max-cll", "atc-sei", "avsw"}
    record("flags.nvenc 色彩/软解旗标", need <= kn,
           f"missing={sorted(need - kn)}")
    kq = known_flags(qsv)
    record("flags.qsv 色彩/质量旗标",
           need <= kq and "quality" in kq,
           f"missing={sorted(need - kq)}")

    gpac = GpacContainerBackend()
    record("tool.GPAC 版本", "GPAC" in gpac.version())
    gyro = find_gyroflow(None)
    record("tool.Gyroflow 探测", gyro is not None, f"gyro={gyro}")

    dji_src = (ROOT / "testsets" / "action4_4k_4x3_30+60"
               / "DJI_20260830095031_0009_D.MP4")
    if dji_src.is_file():
        movie_ts, tracks = gpac.parse_info(gpac.info(dji_src))
        record("tool.GPAC -info DJI 解析",
               movie_ts > 0 and any(t["entry"] == "djmd" for t in tracks),
               f"movie_ts={movie_ts} tracks={len(tracks)}")




# ===========================================================================
# L2 — 工具链
# ===========================================================================
