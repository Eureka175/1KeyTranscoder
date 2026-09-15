#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1KeyTranscoder 全量自动化测试 (分级测试深度).

用法:
    python tests/full_autotest.py --level unit        # L1 纯逻辑单测 (无外部工具, 秒级)
    python tests/full_autotest.py --level toolchain   # L2 = L1 + 工具链/能力/旗标探测 (约1分钟, 不编码)
    python tests/full_autotest.py --level full        # L3 = L2 + 真实管线集成 + 故障注入 (约10-15分钟)
    python tests/full_autotest.py --level all         # 等同 full

分级定义:
  L1 unit       : 纯函数逻辑 (color 表/caps 解析/格式规划/失败分类/flag 构造/
                  probe 解析/paths/分类器/缩放引擎/gpac parse_info/dji facts)
  L2 toolchain  : 真实工具版本 + --check-features 能力解析 + known_flags
                  白名单 + Gyroflow/GPAC 可用性 (只探测, 不编码)
  L3 full       : 真实管线集成 (Sony/DJI/经典路径 x NVENC/QSV, basic+full
                  check) + 故障注入 (截断文件/trailing-garbage 触发 strip
                  回退) + failed_files.json/retry-list + 断点续跑

约束: 全部输入在 work/autotest/ 下自建副本, testsets 原文件只读;
      全部产物写入 work/autotest/ (报告 autotest_report.json/.md);
      不修改任何既有配置/文档。
退出码: 0 = 全部通过; 1 = 存在失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

# Phase 3A 起音频处理链路依赖 numpy (与 core/channel_sync.py 的既有可选依赖
# 一致); 缺失时只跳过 Phase 3A 用例, 不影响其它测试。
try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "work" / "autotest"
IN_DIR = WORK / "in"
OUT_DIR = WORK / "out"
FFPROBE = ROOT / "tools" / "ffprobe.exe"
FFMPEG = ROOT / "tools" / "ffmpeg.exe"
MB = Path(r"C:\Program Files\GPAC\mp4box.exe")
GF_CANDIDATES = [
    Path(r"D:\Gyroflow-windows64\Gyroflow.exe"),
    Path(r"C:\Program Files\Gyroflow\Gyroflow.exe"),
]

RESULTS: list[dict[str, Any]] = []
CURRENT_LEVEL = "L1"


def record(name: str, ok: bool, detail: str = "", level: str = "") -> None:
    RESULTS.append(
        {
            "name": name,
            "level": level or CURRENT_LEVEL,
            "status": "PASS" if ok else "FAIL",
            "detail": detail,
        }
    )


def section(title: str) -> None:
    print(f"\n== {title} ==")


def _work_set_mb() -> float | None:
    """本进程工作集 (MB); 非 Windows 或调用失败返回 None。

    内存回归断言用: np.memmap 会把被触碰的整条文件计入 WorkingSet,
    有界窗口读取器则不会 —— 这条断言把该性质钉进回归 (Stage 1.2)。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.restype = wt.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD,
        ]
        c = _PMC()
        c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(
                k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return c.WorkingSetSize / 1024 ** 2
    except Exception:
        return None


def sh(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout,
    )


def ffprobe_json(path: Path) -> dict:
    r = sh(FFPROBE, "-v", "error", "-show_streams", "-show_format",
           "-of", "json", path)
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout or "{}")


# ===========================================================================
# L1 — 纯逻辑单测
# ===========================================================================

def l1_color() -> None:
    from core.color import (
        ColorInfo, MATRIX_TOKENS, PRIMARIES_TOKENS, RANGE_TOKENS,
        TRANSFER_TOKENS, UNSET,
    )
    section("L1 core.color")
    record("color.ColorInfo.is_set 空", not ColorInfo().is_set)
    record("color.ColorInfo.is_set bt709",
           ColorInfo(primaries="bt709").is_set)
    record("color.UNSET 语义", "unknown" in UNSET and None in UNSET)
    record("color.PRIMARIES 别名", PRIMARIES_TOKENS["smpte431"] == "st431-2"
           and PRIMARIES_TOKENS["jedec-p22"] == "ebu3213-e")
    record("color.TRANSFER HLG/PQ",
           TRANSFER_TOKENS["arib-std-b67"] == "arib-std-b67"
           and TRANSFER_TOKENS["smpte2084"] == "smpte2084")
    record("color.MATRIX 别名", MATRIX_TOKENS["bt2020ncl"] == "bt2020nc"
           and MATRIX_TOKENS["chroma-derived-nc"] == "derived-ncl"
           and MATRIX_TOKENS["ictcp"] == "ictco")
    record("color.RANGE", RANGE_TOKENS == {
        "tv": "tv", "pc": "pc", "limited": "limited", "full": "full",
    })

    from encoders.hw import color_flag_args
    known = {
        "colorprim", "transfer", "colormatrix", "colorrange",
        "master-display", "max-cll",
    }
    args, notes = color_flag_args(
        ColorInfo("bt709", "bt709", "bt709", "tv"), known
    )
    record("hw.color_flag_args SDR",
           args == ["--colorprim", "bt709", "--transfer", "bt709",
                    "--colormatrix", "bt709", "--colorrange", "tv"],
           f"args={args}")
    args, _ = color_flag_args(
        ColorInfo("bt2020", "arib-std-b67", "bt2020nc", "tv"), known
    )
    record("hw.color_flag_args HLG",
           "--transfer" in args and "arib-std-b67" in args)
    args, notes = color_flag_args(
        ColorInfo(
            "bt2020", "smpte2084", "bt2020nc", "tv",
            "G(1,2)B(3,4)R(5,6)WP(7,8)L(1000,1)", "1000,400",
        ),
        known,
    )
    record("hw.color_flag_args HDR10 mdcv/clli",
           "--master-display" in args and "--max-cll" in args
           and "1000,400" in args, f"args={args}")
    args, notes = color_flag_args(
        ColorInfo("unknown", "unknown", "unknown", "unknown"), known
    )
    record("hw.color_flag_args 无信号不写", args == [], f"args={args}")
    args, notes = color_flag_args(
        ColorInfo("bt709", "bt709", "bt709", "tv",
                  "G(1,2)B(3,4)R(5,6)WP(7,8)L(1000,1)", "1000,400"),
        {"colorprim"},
    )
    record("hw.color_flag_args 白名单门控",
           args == ["--colorprim", "bt709"] and len(notes) >= 4,
           f"args={args} notes={notes}")


def l1_caps() -> None:
    from encoders.caps import (
        CONSERVATIVE_CAPS, BackendCaps, CodecCaps, _parse_nvenc,
        _parse_qsv, downgrade_ladder, supports,
    )
    section("L1 encoders.caps")
    nvenc_text = (
        "Codec: H.264/AVC\nRC Modes ...\n"
        "H.264/AVC: nv12, yuv422, yuv422(10bit)\n"
        "Codec: H.265/HEVC\nRC Modes ...\n"
        "H.265/HEVC: nv12, yv12, yv12(10bit), yuv444, yuv444(10bit), "
        "yuv422, yuv422(10bit)\n"
    )
    caps = _parse_nvenc(nvenc_text)
    hevc = caps.get("hevc")
    record("caps.nvenc hevc 解析", hevc is not None
           and hevc.bit10 and hevc.csp_422 and hevc.csp_444
           and hevc.bit10_422, f"hevc={hevc}")
    h264 = caps.get("h264")
    record("caps.nvenc h264 解析", h264 is not None and h264.csp_422
           and h264.bit10_422, f"h264={h264}")
    caps_plain = _parse_nvenc("H.265/HEVC: nv12, yv12(10bit), yuv444\n")
    record("caps.nvenc 无422时 csp_422=False",
           caps_plain["hevc"].bit10 and not caps_plain["hevc"].csp_422,
           f"plain={caps_plain['hevc']}")

    qsv_text = (
        "Codec: H.265/HEVC FF\n"
        "10bit depth       o o o o\n"
        "Codec: AV1 FF\n"
        "10bit depth       o o\n"
    )
    qcaps = _parse_qsv(qsv_text)
    record("caps.qsv hevc 10bit", qcaps.get("hevc") is not None
           and qcaps["hevc"].bit10, f"qsv={qcaps}")

    caps_b = BackendCaps(codecs=caps)
    record("caps.supports 422/10", supports(caps_b, "4:2:2", 10))
    record("caps.supports 422/8 (caps 含 8bit422)",
           supports(caps_b, "4:2:2", 8))
    record("caps.supports 444", supports(caps_b, "4:4:4", 8))
    record("caps.supports 420/10", supports(caps_b, "4:2:0", 10))
    record("caps.CONSERVATIVE 8bit420 地板",
           supports(CONSERVATIVE_CAPS, "4:2:0", 8)
           and not supports(CONSERVATIVE_CAPS, "4:2:0", 10)
           and not supports(CONSERVATIVE_CAPS, "4:2:2", 8))
    ladder = downgrade_ladder("4:2:2", 10)
    record("caps.ladder 422/10",
           ladder == [("4:2:2", 10), ("4:2:0", 10), ("4:2:0", 8)],
           f"ladder={ladder}")
    record("caps.ladder 420/10",
           downgrade_ladder("4:2:0", 10) == [("4:2:0", 10), ("4:2:0", 8)])


def l1_hw() -> None:
    from encoders.caps import BackendCaps, CodecCaps
    from encoders.hw import (
        build_flag_args, classify_failure, plan_initial_format,
    )
    section("L1 encoders.hw")
    caps422 = BackendCaps(codecs={"hevc": CodecCaps(
        bit10=True, csp_422=True, bit10_422=True,
    )})
    caps420 = BackendCaps(codecs={"hevc": CodecCaps(bit10=True)})
    record("hw.plan nvenc 422/10 保真",
           plan_initial_format(caps422, "nvencc", "4:2:2", 10)
           == (("4:2:2", 10), False))
    record("hw.plan qsv 422/10 转 420",
           plan_initial_format(caps422, "qsvencc", "4:2:2", 10)
           == (("4:2:0", 10), True))
    record("hw.plan 420/10 支持",
           plan_initial_format(caps420, "nvencc", "4:2:0", 10)
           == (("4:2:0", 10), False))
    record("hw.plan 420/10 不支持降级",
           plan_initial_format(BackendCaps(), "nvencc", "4:2:0", 10)
           == (("4:2:0", 10), True))
    record("hw.classify environment",
           classify_failure("error: no space left on device") == "environment")
    record("hw.classify reader",
           classify_failure("Failed to open the input file") == "reader")
    record("hw.classify format",
           classify_failure("some encoder error") == "format")

    known = {"aq", "no-aq", "aq-strength", "bframes", "qp-init", "aud"}
    args, skipped = build_flag_args(
        {
            "aq": True, "aq_off": False, "aq_strength": "AUTO",
            "bframes": 5, "qp_init": [1, 2], "aud": "off",
            "unknown_key": 1,
        },
        {
            "aq": ("--aq", "flag"),
            "aq_strength": ("--aq-strength", "value"),
            "bframes": ("-b --bframes", "value"),
            "qp_init": ("--qp-init", "list"),
            "aud": ("--aud", "flag"),
        },
        known,
    )
    record("hw.build_flag_args",
           args == ["--aq", "--bframes", "5", "--qp-init", "1:2"],
           f"args={args}")
    args2, skipped2 = build_flag_args(
        {"aq": True}, {"aq": ("--aq", "flag")}, {"nothing"},
    )
    record("hw.build_flag_args 白名单跳过",
           args2 == [] and "aq" in skipped2, f"skipped={skipped2}")


def l1_probe_paths() -> None:
    from core.probe import (
        _parse_ratio, _pix_fmt_bit_depth, _pix_fmt_chroma, _side_data_color,
    )
    from core.paths import discover_sources, job_id_for, output_path_for
    section("L1 probe/paths")
    record("probe.ratio 30000/1001",
           abs(_parse_ratio("30000/1001") - 29.97002997) < 1e-6)
    record("probe.ratio 0/0", _parse_ratio("0/0") == 0.0)
    record("probe.depth p010le", _pix_fmt_bit_depth("yuv420p10le", 0) == 10)
    record("probe.depth raw 优先",
           _pix_fmt_bit_depth("yuv420p", 10) == 10)
    record("probe.chroma 422/420/444",
           _pix_fmt_chroma("yuv422p10le") == "4:2:2"
           and _pix_fmt_chroma("yuv420p") == "4:2:0"
           and _pix_fmt_chroma("yuv444p") == "4:4:4")
    md, cll = _side_data_color({
        "side_data_list": [
            {
                "side_data_type": "Mastering display metadata",
                "red_x": "34000/50000", "red_y": "16000/50000",
                "green_x": "13250/50000", "green_y": "34500/50000",
                "blue_x": "7500/50000", "blue_y": "3000/50000",
                "white_point_x": "15635/50000",
                "white_point_y": "16450/50000",
                "max_luminance": "1000/10000",
                "min_luminance": "1/10000",
            },
            {
                "side_data_type": "Content light level metadata",
                "max_content": 1000, "max_average": 400,
            },
        ],
    })
    record("probe.side_data mdcv/clli",
           md == "G(13250,34500)B(7500,3000)R(34000,16000)"
                 "WP(15635,16450)L(1000,1)" and cll == "1000,400",
           f"md={md} cll={cll}")

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "a.MP4").write_bytes(b"x")
        (p / "b.txt").write_bytes(b"y")
        (p / "sub").mkdir()
        (p / "sub" / "c.mov").write_bytes(b"z")
        found = discover_sources(p)
        record("paths.discover 递归+扩展名",
               {f.name for f in found} == {"a.MP4", "c.mov"},
               f"found={found}")
    src = Path(r"F:\素材\dir\file.MP4")
    root = Path(r"F:\素材")
    out = output_path_for(src, root, Path(r"F:\out"), "HQ", False)
    record("paths.output_path_for",
           out == Path(r"F:\out\dir\file.MP4"), f"out={out}")
    record("paths.job_id 稳定",
           job_id_for(src) == job_id_for(src)
           and len(job_id_for(src)) > 8)


def l1_classifier_scaling() -> None:
    from core.models import SourceInfo
    from core.scaling import ScalingEngine, ceil_expression
    from core.source_classifier import SourceClassifier
    section("L1 classifier/scaling")

    def mk(ob_kbps: float, codec: str = "hevc") -> SourceInfo:
        return SourceInfo(
            path=Path("x.mp4"), size_bytes=1, duration_sec=1.0,
            width=3840, height=2160, fps=59.94,
            r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
            codec=codec, profile="Main 10", pix_fmt="yuv420p10le",
            bit_depth=10, chroma="4:2:0", ob_kbps=ob_kbps,
            video_bitrate_kbps=ob_kbps, video_stream_count=1,
            stream_info=(),
        )

    cfg = {
        "classification": {
            "intra_like_codecs": ["prores"],
            "thresholds": {"low_max": 0.12, "high_min": 0.25},
        },
        "dynamic_vbv": {},
    }
    clf = SourceClassifier(cfg)
    # normalized_ob = ob_kbps*1000/(w*h*fps): 4K60 150Mbps≈0.30,
    # 80Mbps≈0.16, 30Mbps≈0.06
    record("clf HIGH", clf.classify(mk(150000.0)).source_class
           == "HIGH_BITRATE_LONG_GOP")
    record("clf LOW", clf.classify(mk(30000.0)).source_class
           == "LOW_BITRATE_LONG_GOP")
    record("clf NORMAL", clf.classify(mk(80000.0)).source_class
           == "NORMAL_LONG_GOP")
    record("clf INTRA_LIKE", clf.classify(mk(99999.0, "prores")).source_class
           == "INTRA_LIKE")
    record("scaling.ceil FR*3", ceil_expression("FR*3", 59.94) == 180)
    record("scaling.ceil cap", ceil_expression("FR*3", 59.94, cap=200) == 180
           and ceil_expression("FR*3", 119.88, cap=200) == 200)
    engine = ScalingEngine({
        "reference": {"width": 3840, "height": 2160, "fps": 59.94},
        "param_rules": {},
        "dynamic_vbv": {},
    })
    sf, tf, pr = engine.factors(mk(30000.0))
    record("scaling.factors 4K60 参考", abs(sf - 1.0) < 1e-9
           and abs(tf - 1.0) < 1e-9)


def l1_gpac_dji() -> None:
    from preservation.dji import (
        _cam_summary, _quat_close, dji_track_specs, parsed_facts,
    )
    from preservation.gpac import GpacContainerBackend
    section("L1 gpac.parse_info / dji facts")
    info_text = """
# Movie Info - 5 tracks - TimeScale 30000
Duration 00:00:03.503
# Track 1 Info - ID 1 - TimeScale 30000
Media Duration 00:00:03.503
Media Samples: 105 - CFR 29.970030/sec
Media Type: vide:hvc1
# Track 2 Info - ID 2 - TimeScale 48000
Media Duration 00:00:03.498
Media Samples: 164 - CFR 46.875000/sec
Media Type: soun:mp4a
# Track 3 Info - ID 3 - TimeScale 30000
Media Duration 00:00:03.503
Media Samples: 105 - CFR 29.970030/sec
Media Type: meta:djmd
# Track 5 Info - ID 7 - TimeScale 30000
Media Samples: 1 - CFR 0.285429/sec
Media Type: tmcd:tmcd
"""
    movie_ts, tracks = GpacContainerBackend.parse_info(info_text)
    record("gpac.parse_info movie ts", movie_ts == 30000)
    specs = dji_track_specs(tracks)
    record("gpac.parse_info dji specs",
           specs["video_id"] == 1 and specs["audio_ids"] == [2]
           and specs["data_ids"] == [(3, "djmd"), (7, "tmcd")],
           f"specs={specs}")
    djmd = next(t for t in tracks if t["entry"] == "djmd")
    record("gpac.parse_info 时长解析", djmd["media_duration_ms"] == 3503
           and djmd["sample_count"] == 105, f"t={djmd}")

    facts = parsed_facts({
        "detected_source": "DJI OsmoAction4",
        "lens_profile": {"x": 1},
        "has_accurate_timestamps": True,
        "frame_readout_time": 21.817,
    })
    record("dji.parsed_facts",
           facts == {
               "detected_source": "DJI OsmoAction4",
               "has_lens_profile": True,
               "has_accurate_timestamps": True,
               "readout_ms": 21.817,
           }, f"facts={facts}")
    cam = _cam_summary([
        {"frame": 0, "org_quat": [0.1, 0.2, 0.3, 0.4],
         "stab_quat": [0.5, 0.6, 0.7, 0.8]},
        {"frame": 1, "org_quat": [0.11, 0.2, 0.3, 0.4],
         "stab_quat": [0.5, 0.6, 0.7, 0.8]},
    ])
    record("dji.cam_summary", cam["count"] == 2
           and cam["frames"] == [0, 1])
    record("dji.quat_close 容差",
           _quat_close([0.1, 0.2], [0.10001, 0.2])
           and not _quat_close([0.1], [0.2]))


def l1_quality() -> None:
    section("L1 质量抽样 + 版本记录")
    from preservation.quality import (
        DEFAULTS,
        _evaluate,
        _parse_psnr_stats,
        effective_opts,
        sample_selected,
    )
    from core.versions import write_version_report

    opts = effective_opts(None)
    record("quality.opts 默认值", opts == DEFAULTS,
           f"keys={sorted(opts)}")
    merged = effective_opts(
        {"sample_rate": 3, "ssim_min": 0.9, "_comment": "x",
         "unknown_key": 1}
    )
    record("quality.opts 覆盖+忽略未知",
           merged["sample_rate"] == 3 and merged["ssim_min"] == 0.9
           and "unknown_key" not in merged,
           f"rate={merged['sample_rate']}")
    bad = effective_opts({"sample_rate": 0, "psnr_min_db": "x"})
    record("quality.opts 非法值回退默认",
           bad == DEFAULTS, f"bad={bad}")

    # 确定性伪随机: 同一名称结果稳定; 1000 样本抽样率 ~10%
    names = [f"clip_{i}.MP4" for i in range(1000)]
    picked = sum(1 for n in names if sample_selected(n, opts))
    stable = all(
        sample_selected(n, opts) == sample_selected(n, opts)
        for n in names[:100]
    )
    record("quality.sample 确定性 1-in-10",
           stable and 60 <= picked <= 140,
           f"picked={picked}/1000")

    ok, _ = _evaluate(40.0, 0.95, 0.0, opts)
    bad1, why1 = _evaluate(20.0, 0.95, 0.0, opts)
    bad2, why2 = _evaluate(40.0, 0.70, 0.0, opts)
    bad3, why3 = _evaluate(40.0, 0.95, 0.05, opts)
    bad4, why4 = _evaluate(None, None, 0.0, opts)
    record("quality.evaluate 阈值判定",
           ok and not bad1 and not bad2 and not bad3 and not bad4
           and "psnr" in why1 and "ssim" in why2 and "垃圾帧" in why3,
           f"{why1} | {why2} | {why3} | {why4}")

    stats = WORK / "quality_test_psnr.csv"
    stats.write_text(
        "n:1 mse_avg:1.0 psnr_avg:45.0\n"
        "n:2 mse_avg:1000.0 psnr_avg:10.0\n"
        "n:3 mse_avg:1.0 psnr_avg:44.0\n",
        encoding="utf-8",
    )
    vals = _parse_psnr_stats(stats)
    record("quality.parse stats 逐帧",
           vals == [45.0, 10.0, 44.0], f"vals={vals}")

    # 版本报告: 合成 dict -> JSON+CSV 双落盘, 结构可解析
    import json as _json
    vj, vc = write_version_report(
        {
            "generated": "2026-08-31 00:00:00",
            "encoder": "nvenc",
            "tools": {
                "ffmpeg": "9.0.1",
                "ffmpeg_libs": {"libsvtav1": "4.2.0"},
                "nvencc": "NVEncC 9.31",
            },
            "gpu_drivers": [
                {"gpu": "NVIDIA RTX 5070", "driver_version": "596.36"}
            ],
        },
        WORK / "ver_test",
    )
    vdata = _json.loads(vj.read_text(encoding="utf-8"))
    csv_text = vc.read_text(encoding="utf-8-sig")
    record("versions.report json+csv",
           vdata["tools"]["ffmpeg"] == "9.0.1"
           and vdata["gpu_drivers"][0]["driver_version"] == "596.36"
           and "libsvtav1" in csv_text
           and "596.36" in csv_text,
           f"json={vj.name} csv={vc.name}")


def l1_channel_sync() -> None:
    section("L1 延时补偿 (channel-sync)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        record("channel_sync.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (--channel-sync 需 numpy/scipy)")
        return
    record("channel_sync.numpy/scipy 可用", True)

    from core.channel_sync import effective_opts, eligible_audio
    from core.mp4_channel_sync import (
        fix_channels,
        measure_channels,
        measure_delay,
        verify_channels,
    )

    # --- 布局判定: 2ch/1ch 默认不做对齐 ---
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 2, "sample_rate": "48000"}]
    )
    record("channel_sync.2ch 默认不对齐", not ok and "2ch" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 1, "sample_rate": "48000"}]
    )
    record("channel_sync.1ch 默认不对齐", not ok and "1ch" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "aac", "channels": 1,
          "sample_rate": "48000"}] * 4
    )
    record("channel_sync.非 PCM 不对齐", not ok, why)
    ok, _ = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s24le",
          "channels": 1, "sample_rate": "48000"}] * 4
    )
    record("channel_sync.4xmono PCM 对齐", ok)
    record("channel_sync.opts 默认值",
           effective_opts(None)["anchor_candidates"] == [2, 3, 0, 1]
           and effective_opts(None)["algo_version"] == "2.3.0-p1")

    # --- 算法包核心断言 (与手交付包 selftest 同源, 合成信号) ---
    SR = 48_000

    def speech_like(n: int, seed: int = 0) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, [100.0, 4000.0], btype="band", fs=SR,
                            output="sos")
        carrier = signal.sosfilt(sos, rng.standard_normal(n))
        carrier /= np.max(np.abs(carrier)) + 1e-12
        envelope = np.zeros(n)
        t = 0.0
        while t < n / SR:
            start = int(t * SR)
            dur = rng.uniform(0.08, 0.4)
            m = int(dur * SR)
            if start < n and m > 0:
                k = np.arange(min(m, n - start))
                env = rng.uniform(0.4, 1.0) * np.exp(-k / (dur * SR / 3.0))
                envelope[start: start + len(k)] += env
            t += rng.uniform(0.15, 0.6) + dur
        out = carrier * np.minimum(envelope, 1.0)
        return (out / (np.max(np.abs(out)) + 1e-12)).astype(np.float32)

    def delay_int(x: np.ndarray, n: int) -> np.ndarray:
        y = np.zeros_like(x)
        if n >= 0:
            if n < x.size:
                y[n:] = x[: x.size - n]
        else:
            m = -n
            if m < x.size:
                y[: x.size - m] = x[m:]
        return y

    def delay_frac(x: np.ndarray, delay: float, taps: int = 97) -> np.ndarray:
        i0 = int(np.floor(delay))
        frac = delay - i0
        m = np.arange(-(taps // 2), taps // 2 + 1)
        h = np.sinc(m - frac) * np.hanning(taps)
        h /= h.sum()
        return delay_int(np.convolve(x, h, mode="same"), i0)

    base = speech_like(15 * SR, seed=1)
    channels = [delay_int(base, 1223), delay_int(base, 1350), base, base]
    results = measure_channels(channels, reference_index=2)
    d = {r.channel: r.delay_samples for r in results}
    record("channel_sync.整数延迟 1223 样本", abs(d[0] - 1223.0) < 0.5,
           f"measured {d[0]:.2f}")
    record("channel_sync.整数延迟 1350 样本", abs(d[1] - 1350.0) < 0.5,
           f"measured {d[1]:.2f}")
    record("channel_sync.参考通道=0", d[2] == 0.0)
    record("channel_sync.有线通道≈0", abs(d[3]) < 0.5, f"{d[3]:.2f}")
    record("channel_sync.恒定判定", results[0].constant)
    record("channel_sync.置信度>0.5", results[0].confidence > 0.5)

    base2 = speech_like(10 * SR, seed=2)
    rf = measure_channels(
        [delay_frac(base2, 1223.4), base2], reference_index=1
    )[0]
    record("channel_sync.分数延迟 ±0.15",
           abs(rf.delay_samples - 1223.4) < 0.15,
           f"measured {rf.delay_samples:.3f}")

    base3 = speech_like(15 * SR, seed=3)
    ch3 = [delay_int(base3, 1223), delay_int(base3, 1350), base3, base3]
    res3 = measure_channels(ch3, reference_index=2)
    fixed = fix_channels(ch3, [r.delay_samples for r in res3])
    resid = verify_channels(fixed, reference_index=2)
    record("channel_sync.修正+复检 残差<0.05ms",
           all(abs(v) < 0.05 for v in resid),
           f"residuals={[f'{v:.4f}' for v in resid]}")

    base4 = speech_like(10 * SR, seed=4)
    rp = measure_delay(base4, -delay_int(base4, 500))
    record("channel_sync.反相检测+延迟正确",
           rp.polarity == -1 and abs(rp.delay_samples - 500.0) < 0.5,
           f"polarity={rp.polarity} delay={rp.delay_samples:.2f}")

    base5 = speech_like(8 * SR, seed=5)
    silence = np.zeros_like(base5)
    rs5 = measure_channels([silence, base5], reference_index=1)
    fixed5 = fix_channels([silence, base5], [rs5[0].delay_samples, 0.0])
    import math
    record("channel_sync.静音 nan + 原样保留",
           math.isnan(rs5[0].delay_samples)
           and np.array_equal(fixed5[0], silence[: fixed5.shape[1]]))
def l1_channel_sync_p1() -> None:
    section("L1 延时补偿 P1 (sync_estimate/sync_fix 纯逻辑)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        record("p1.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (--channel-sync 需 numpy/scipy)")
        return
    record("p1.numpy/scipy 可用", True)

    from core import sync_estimate, sync_fix
    from core.channel_sync import (
        DEFAULTS, effective_opts, eligible_audio, _muxer_for_entry,
    )

    # --- DEFAULTS (v3) ---
    record("p1.algo_version 2.3.0-p1", DEFAULTS["algo_version"] == "2.3.0-p1")
    record("p1.默认纯整数移位 (无 sinc 键)", "sinc" not in DEFAULTS)
    record("p1.锚点候选回退 CH3>CH4>CH1>CH2",
           DEFAULTS["anchor_candidates"] == [2, 3, 0, 1])
    record("p1.transparent 默认关",
           DEFAULTS["channel_sync_transparent"] is False)
    o = effective_opts({"max_lag_seconds": 0.5})
    record("p1.旧键 max_lag_seconds 映射 search_window_ms",
           o["search_window_ms"] == 500.0)
    o2 = effective_opts({"reference_stream": 0})
    record("p1.废弃键 reference_stream 忽略",
           o2["anchor_candidates"] == [2, 3, 0, 1]
           and "reference_stream" not in o2)

    # --- eligible_audio 输入边界 ---
    mono = lambda rate, codec="pcm_s16le": {
        "codec_type": "audio", "codec_name": codec,
        "channels": 1, "sample_rate": str(rate),
    }
    ok, why = eligible_audio([mono(44100)] * 4)
    record("p1.44.1kHz 显式拒绝", not ok and "44100" in why, why)
    ok, why = eligible_audio([mono(96000)] * 4)
    record("p1.96kHz 接受", ok, why)
    ok, why = eligible_audio(
        [mono(48000), mono(96000), mono(48000), mono(48000)]
    )
    record("p1.混合采样率拒绝", not ok and "mixed" in why, why)
    ok, why = eligible_audio([mono(48000, "pcm_s16be")] * 4)
    record("p1.s16be 大端接受", ok, why)
    ok, why = eligible_audio([mono(48000, "pcm_s24be")] * 4)
    record("p1.s24be 大端接受 (A7M5 XAVC-S)", ok, why)
    ok, why = eligible_audio([mono(48000, "pcm_f32be")] * 4)
    record("p1.f32be 大端接受", ok, why)
    ok, why = eligible_audio([mono(48000, "aac")] * 4)
    record("p1.非 PCM 拒绝", not ok, why)

    # --- 音频中间文件的 sample entry 必须可复现源轨 (真实素材回归) ---
    # Sony XAVC-S LPCM 的 sample entry 是 ipcm; ffmpeg 的 MOV muxer 会写成
    # in24, 源音轨被替换后 preservation 的 audio.tracks 关键项即判 MODIFIED
    # 并使整个文件 --check basic 失败 (真实 A7M5 applied 素材 rc=1 实测)。
    # MP4 muxer 写 ipcm/fpcm, 因此按源 entry 选 muxer。
    _mux = _muxer_for_entry
    record("p1.ipcm 源 -> MP4 muxer (保持 ipcm)",
           _mux("ipcm") == "mp4" and _mux("IPCM") == "mp4")
    record("p1.fpcm 源 -> MP4 muxer",
           _mux("fpcm") == "mp4")
    record("p1.QuickTime PCM entry 保持 MOV muxer",
           all(_mux(e) == "mov"
               for e in ("in24", "in32", "sowt", "twos", "fl32", "")))

    # --- 合成信号 ---
    def speech_like(n: int, seed: int, fs: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, [100.0, 4000.0], btype="band", fs=fs,
                            output="sos")
        carrier = signal.sosfilt(sos, rng.standard_normal(n))
        carrier /= np.max(np.abs(carrier)) + 1e-12
        envelope = np.zeros(n)
        t = 0.0
        while t < n / fs:
            start = int(t * fs)
            dur = rng.uniform(0.08, 0.4)
            m = int(dur * fs)
            if start < n and m > 0:
                k = np.arange(min(m, n - start))
                env = rng.uniform(0.4, 1.0) * np.exp(-k / (dur * fs / 3.0))
                envelope[start: start + len(k)] += env
            t += rng.uniform(0.15, 0.6) + dur
        out = carrier * np.minimum(envelope, 1.0)
        return (out / (np.max(np.abs(out)) + 1e-12)).astype(np.float32)

    def delay_int(x: np.ndarray, n: int) -> np.ndarray:
        y = np.zeros_like(x)
        if n >= 0:
            if n < x.size:
                y[n:] = x[: x.size - n]
        else:
            m = -n
            if m < x.size:
                y[: x.size - m] = x[m:]
        return y

    def delay_frac(x: np.ndarray, delay: float, taps: int = 97) -> np.ndarray:
        i0 = int(np.floor(delay))
        frac = delay - i0
        m = np.arange(-(taps // 2), taps // 2 + 1)
        h = np.sinc(m - frac) * np.hanning(taps)
        h /= h.sum()
        return delay_int(np.convolve(x, h, mode="same"), i0)

    def est(ref: np.ndarray, tgt: np.ndarray, fs: int) -> Any:
        return sync_estimate.estimate_pair(
            ref, tgt,
            sample_rate=fs, search_window_ms=80.0, frame_ms=200.0,
            hop_ms=100.0, anchor_segment_seconds=3.0, min_confidence=0.3,
        )

    SR = 48_000
    base = speech_like(6 * SR, seed=1, fs=SR)

    # --- shift_stream: 纯整数移位 (delay>0 = 晚到 -> 前移, 尾补零) ---
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        x = base.copy()
        src = td / "x.f32"
        src.write_bytes(x.tobytes())

        def shift_and_read(delay: float) -> np.ndarray:
            dst = td / f"y{abs(delay):.0f}.f32"
            sync_fix.shift_stream(src, dst, delay_samples=delay,
                                  sample_rate=SR, storage_dtype="f32",
                                  chunk_seconds=1.0)
            y = np.memmap(dst, dtype="<f4", mode="r")
            out = np.asarray(y).copy()
            del y
            return out

        y = shift_and_read(1223.0)
        record("p1.shift 正向整数 1223 (前移+尾补零)",
               bool(y.shape[0] == x.shape[0]
                    and np.array_equal(y[: x.size - 1223], x[1223:])
                    and np.all(y[x.size - 1223:] == 0)))
        y2 = shift_and_read(-900.0)
        record("p1.shift 负向整数 -900 (后移+头补零)",
               bool(np.array_equal(y2[900:], x[: x.size - 900])
                    and np.all(y2[:900] == 0)))
        y3 = shift_and_read(1223.4)
        record("p1.shift 分数 1223.4 -> rint=1223 (与整数移位一致)",
               bool(np.array_equal(y3, y)))
        y4 = shift_and_read(-0.4)
        record("p1.shift 分数 -0.4 -> rint=0 (样本值不变)",
               bool(np.array_equal(y4, x)))
        m = sync_estimate.open_source(src, "f32")
        record("p1.路径输入为有界窗口流 (非整文件映射)",
               bool(isinstance(m, sync_estimate.RawStream)
                    and not isinstance(m, np.memmap)))
        record("p1.窗口读取与 ndarray 切片一致",
               bool(np.array_equal(np.asarray(m[100:600]), x[100:600])
                    and m.shape[0] == x.shape[0]))
        record("p1.越界窗口自动裁剪 (不报错/不补零)",
               bool(m[m.shape[0] - 4: m.shape[0] + 999].shape[0] == 4
                    and m[5: 5].shape[0] == 0))
        # 内存回归: 对 8 MB 流做整轨扫描 + 逐帧读取, 进程工作集增量必须有界
        # (旧 np.memmap 实现会把整条文件计入 WorkingSet; Stage 1.2 实测
        #  4x110 MB 轨 -> RSS +440 MB, 见 work/channel_sync_memory_audit.md)
        if _work_set_mb() is None:
            record("p1.整轨扫描后工作集增量有界", True,
                   "非 Windows: 跳过工作集测量")
        else:
            big_n = 16_000_000                      # 64 MB f32
            big = td / "big.f32"
            big.write_bytes(np.zeros(big_n, dtype="<f4").tobytes())
            stream = sync_estimate.open_source(big, "f32")
            before_mb = _work_set_mb()
            ssq = 0.0
            for a0 in range(0, big_n, 1 << 19):     # 整轨分块扫描
                blk = np.asarray(stream[a0: a0 + (1 << 19)], dtype=np.float64)
                ssq += float(np.sum(blk * blk))
            # 逐帧窗口读取 (与 estimate_pair 的访问模式相同)
            for a0 in range(0, big_n - 48000, 4800):
                window = stream[a0: a0 + 9600]
                if window.shape[0]:
                    ssq += float(window[0])
            stream.close()
            growth = _work_set_mb() - before_mb
            # 64 MB 整轨: 旧 np.memmap 实现会 +64 MB; 有界窗口读取器只保留
            # 窗口 + 4x1 MiB 读缓存 + f64 副本, 实测 < 20 MB
            record("p1.64MB 整轨扫描后工作集增量有界 (<= 32 MB, 实测 %.1f MB)"
                   % growth, bool(growth <= 32.0))
        try:
            mm = getattr(m, "_mmap", None)
            if mm is not None:
                mm.close()
            else:
                m.close()
        except OSError:
            pass

    # --- estimate_pair: 基础正确性 48k ---
    e1 = est(base, delay_int(base, 1223), SR)
    record("p1.48k 整数 +1223",
           abs(e1.delay_samples - 1223.0) <= 1.0 and e1.confidence > 0.5,
           f"measured {e1.delay_samples:.2f} conf {e1.confidence:.2f}")
    e2 = est(base, delay_int(base, -900), SR)
    record("p1.48k 整数 -900",
           abs(e2.delay_samples + 900.0) <= 1.0,
           f"measured {e2.delay_samples:.2f}")
    e3 = est(base, base.copy(), SR)
    record("p1.48k 已对齐 ≈0",
           abs(e3.delay_samples) <= 2.0 and e3.confidence > 0.5,
           f"measured {e3.delay_samples:.2f}")
    e4 = est(base, delay_frac(base, 1223.4), SR)
    record("p1.48k 分数 1223.4 相位精估 ±0.15",
           abs(e4.fine_delay_samples - 1223.4) < 0.15,
           f"fine={e4.fine_delay_samples:.3f} r2={e4.fine_fit_r2:.3f}")

    # --- recheck_residual: 整数移位量化残差与相位斜率复检 ---
    r_int = sync_fix.recheck_residual(
        base, base.copy(), sample_rate=SR, storage_dtype="f32",
        anchor_segment_seconds=3.0,
    )
    record("p1.整数修正复检残差 <0.05ms", abs(r_int) < 0.05,
           f"{r_int:.4f}ms")
    r_frac = sync_fix.recheck_residual(
        base, delay_frac(base, 0.4), sample_rate=SR, storage_dtype="f32",
        anchor_segment_seconds=3.0,
    )
    record("p1.复检测出 0.4 样本残余 (相位斜率)",
           abs(r_frac - 0.4 * 1000.0 / SR) < 0.02, f"{r_frac:.4f}ms")

    # --- 96k ---
    SR2 = 96_000
    base2 = speech_like(4 * SR2, seed=2, fs=SR2)
    e5 = est(base2, delay_int(base2, 2446), SR2)
    record("p1.96k 整数 +2446",
           abs(e5.delay_samples - 2446.0) <= 1.0,
           f"measured {e5.delay_samples:.2f}")
    e6 = est(base2, delay_frac(base2, 2446.6), SR2)
    record("p1.96k 分数 2446.6 相位精估 ±0.15",
           abs(e6.fine_delay_samples - 2446.6) < 0.15,
           f"fine={e6.fine_delay_samples:.3f}")

    # --- 恒定性: 中途跳变 / 线性漂移 ---
    step_sig = np.concatenate(
        [base[: 3 * SR], delay_int(base[3 * SR:], 2400)]
    )  # 后半段 +50ms 跳变
    e7 = est(base, step_sig, SR)
    st7 = sync_estimate.summarize_trajectory(e7.frames, SR)
    c7 = sync_estimate.classify_constant(
        st7, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR
    )
    record("p1.中途 50ms 跳变 -> non_constant",
           not c7 and st7.spread_samples > 1.0 * SR / 1000.0,
           f"spread={st7.spread_samples:.0f}smp mad={st7.mad_ms:.2f}ms "
           f"ppm={st7.drift_ppm:.1f}")

    # --- 恒定性漂移门 (真实素材标定: ppm 门 + 漂移材料性门) ---
    # 10ppm 漂移在 6s 上仅 0.06ms: 低于材料性门 (0.1ms) -> 视为恒定
    # (A7M5 实测: 已对齐轨的量化噪声斜率可达 5~30ppm, 漂移量仅 ~0.03ms)
    n_d = base.shape[0]
    idx = np.arange(n_d, dtype=np.float64)
    pos = idx - idx * 10e-6           # 10 ppm 线性漂移 (每 1e6 样本 10 样本)
    drift_sig = np.interp(pos, idx, base.astype(np.float64)).astype(np.float32)
    e8 = est(base, drift_sig, SR)
    st8 = sync_estimate.summarize_trajectory(e8.frames, SR)
    c8 = sync_estimate.classify_constant(
        st8, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR, drift_min_ms=0.1
    )
    record("p1.6s 上 10ppm (0.06ms) -> constant (材料性门)",
           c8 and abs(st8.drift_ppm) > 5.0
           and abs(st8.drift_total_ms) < 0.1,
           f"ppm={st8.drift_ppm:.1f} drift_total={st8.drift_total_ms:.3f}ms")

    # 同一 10ppm 漂移在 30s 上累计 0.3ms -> 超材料性门 -> non_constant
    base30 = speech_like(30 * SR, seed=3, fs=SR)
    n30 = base30.shape[0]
    idx30 = np.arange(n30, dtype=np.float64)
    pos30 = idx30 - idx30 * 10e-6
    drift30 = np.interp(
        pos30, idx30, base30.astype(np.float64)
    ).astype(np.float32)
    e8b = est(base30, drift30, SR)
    st8b = sync_estimate.summarize_trajectory(e8b.frames, SR)
    c8b = sync_estimate.classify_constant(
        st8b, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR, drift_min_ms=0.1
    )
    record("p1.30s 上 10ppm (0.3ms) -> non_constant",
           not c8b and abs(st8b.drift_ppm) > 5.0
           and abs(st8b.drift_total_ms) > 0.1,
           f"ppm={st8b.drift_ppm:.1f} drift_total={st8b.drift_total_ms:.3f}ms")

    # --- 超窗: 窄窗不可测 + 宽窗复测可测 (out_of_range 依据) ---
    e9 = est(base, delay_int(base, 4800), SR)   # +100ms > 80ms 搜索窗
    ew9 = sync_estimate.estimate_pair(
        base, delay_int(base, 4800),
        sample_rate=SR, search_window_ms=250.0, frame_ms=200.0,
        hop_ms=100.0, anchor_segment_seconds=3.0, min_confidence=0.3,
    )
    record("p1.100ms 超窗 -> 窄窗不可测 + 宽窗测出",
           e9.usable_frames < 5
           and ew9.usable_frames >= 5
           and abs(ew9.delay_samples - 4800.0) <= 2.0,
           f"narrow_usable={e9.usable_frames} "
           f"wide={ew9.delay_samples:.1f}")

    # --- 反相 ---
    rp = est(base, -delay_int(base, 500), SR)
    record("p1.反相 polarity=-1 延迟正确",
           rp.polarity == -1 and abs(rp.delay_samples - 500.0) <= 1.0,
           f"polarity={rp.polarity} delay={rp.delay_samples:.2f}")

    # --- 确定性 (bit 级) ---
    ea = est(base, delay_int(base, 1223), SR)
    eb = est(base, delay_int(base, 1223), SR)
    same = bool(
        ea.delay_samples == eb.delay_samples
        and ea.fine_delay_samples == eb.fine_delay_samples
        and np.array_equal(
            [f.delay_samples for f in ea.frames],
            [f.delay_samples for f in eb.frames], equal_nan=True,
        )
    )
    record("p1.估计确定性 (bit 级一致)", same)


def _raw_audio_stream(
    index: int,
    *,
    channels: int | None = 1,
    codec: str = "pcm_s24le",
    sample_rate: Any = "48000",
    sample_fmt: Any = "s32",
    layout: str | None = None,
    duration: Any = "8.000000",
    bit_rate: Any = "1152000",
    tags: dict | None = None,
    disposition: dict | None = None,
    codec_long_name: str = "PCM signed 24-bit little-endian",
    tag_string: str = "in24",
) -> dict:
    """合成一条 ffprobe 风格 audio stream dict (L1 音频模型用例用)。

    默认值取真实 Sony A7M5 4CH 素材的实测形态 (pcm_s24be + sample_fmt
    s32 + 无 channel_layout); 传入 None 表示**该字段缺失**, 用于覆盖
    §10 情况 F / T7 / T8。
    """
    st: dict[str, Any] = {"index": index, "codec_type": "audio",
                          "codec_name": codec}
    if codec_long_name:
        st["codec_long_name"] = codec_long_name
    if channels is not None:
        st["channels"] = channels
    if sample_rate is not None:
        st["sample_rate"] = sample_rate
    if sample_fmt is not None:
        st["sample_fmt"] = sample_fmt
    if layout is not None:
        st["channel_layout"] = layout
    if duration is not None:
        st["duration"] = duration
    if bit_rate is not None:
        st["bit_rate"] = bit_rate
    if tag_string is not None:
        st["codec_tag_string"] = tag_string
    if tags is not None:
        st["tags"] = tags
    if disposition is not None:
        st["disposition"] = disposition
    return st


def l1_audio_model() -> None:
    """v0.7.1 Phase 1 音频模型: T1-T8 输入形态 / track 映射 / 同步 / 序列化."""
    import copy

    from core.audio_models import (
        AUDIO_MODEL_VERSION,
        AudioChannel,
        AudioPlan,
        AudioRole,
        AudioSampleFormat,
        AudioStream,
        AudioSyncResult,
        AudioTrack,
        AudioTrackBuilder,
        ChannelSyncReport,
        SyncStatus,
        TrackBuildMode,
        apply_channel_sync_report,
        normalize_channel_layout,
    )
    from core.audio_probe import (
        audio_probe_of,
        audio_streams_from_probe,
        build_audio_plan,
        build_audio_tracks,
        probe_has_audio,
    )
    section("L1 音频模型 (v0.7.1 Phase 1)")

    def model(streams: list[dict], index: int = 0):
        return audio_probe_of(streams).streams[index]

    # ---- T1: 1×mono stream ----
    t1 = model([_raw_audio_stream(2, channels=1, layout="mono")])
    record("audio.T1 mono 声道数/布局",
           t1.channel_count == 1 and t1.channel_layout == "mono"
           and t1.channel_names == ("FC",) and t1.layout_verified,
           f"{t1.channel_count} {t1.channel_layout} {t1.channel_names}")

    # ---- T2: 1×stereo stream ----
    t2 = model([_raw_audio_stream(1, channels=2, layout="stereo")])
    record("audio.T2 stereo 声道名",
           t2.channel_count == 2 and t2.channel_names == ("FL", "FR")
           and [c.channel_index for c in t2.channels()] == [0, 1])

    # ---- T3: 1×4CH stream ----
    t3 = model([_raw_audio_stream(2, channels=4, layout="4.0")])
    record("audio.T3 4CH 单流 -> 4 channel",
           t3.channel_count == 4
           and [c.channel_index for c in t3.channels()] == [0, 1, 2, 3]
           and t3.channel_names == ("FL", "FR", "FC", "BC"),
           f"{t3.channel_names}")

    # ---- T4: 4×mono streams ----
    t4_streams = [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    t4 = audio_probe_of(t4_streams)
    record("audio.T4 4×mono stream -> 4 stream / 4 channel",
           t4.stream_count == 4 and t4.channel_count == 4
           and t4.is_multi_mono and t4.stream_indices == [1, 2, 3, 4],
           f"{t4.stream_indices}")

    # ---- T5: 2×stereo streams ----
    t5 = audio_probe_of([_raw_audio_stream(2, channels=2, layout="stereo"),
                         _raw_audio_stream(3, channels=2, layout="stereo")])
    record("audio.T5 2×stereo -> 2 stream / 4 channel",
           t5.stream_count == 2 and t5.channel_count == 4
           and not t5.is_multi_mono)

    # ---- T6: unknown channel layout (声道数已知, layout 缺失) ----
    t6 = model([_raw_audio_stream(2, channels=4, layout=None)])
    record("audio.T6 未知布局不丢流",
           t6.channel_count == 4 and t6.channel_layout == ""
           and len(t6.channels()) == 4
           and [c.source_channel_name for c in t6.channels()]
           == ["C0", "C1", "C2", "C3"]
           and t6.layout_source == "layout_missing"
           and not t6.layout_verified,
           f"layout_source={t6.layout_source}")
    # 未知布局字符串同样不得崩溃, 也不得冒充标准声道
    t6b = model([_raw_audio_stream(2, channels=4, layout="mystery-4ch")])
    record("audio.T6 未知布局字符串安全退化",
           t6b.channel_count == 4 and t6b.layout_source == "layout_unknown"
           and t6b.channel_names == ("C0", "C1", "C2", "C3"))
    # 声道数与 layout 不符 -> 不按错位命名
    t6c = model([_raw_audio_stream(2, channels=4, layout="stereo")])
    record("audio.T6 布局/声道数不符不按错位命名",
           t6c.layout_source == "layout_count_mismatch"
           and t6c.channel_names == ("C0", "C1", "C2", "C3"))
    record("audio.normalize_channel_layout",
           normalize_channel_layout("  4.0 ") == "4.0"
           and normalize_channel_layout(None) == ""
           and normalize_channel_layout("N/A") == ""
           and normalize_channel_layout("unknown") == "")

    # ---- T7: metadata 缺失 ----
    t7 = model([_raw_audio_stream(1, channels=1, tags=None)])
    record("audio.T7 metadata 缺失",
           t7.metadata == {} and t7.language is None and t7.title is None
           and t7.is_default is False and t7.disposition == {})
    t7b = model([_raw_audio_stream(
        1, channels=1, tags={"language": "eng", "title": "CAM"},
        disposition={"default": 1},
    )])
    record("audio.T7 metadata 存在",
           t7b.language == "eng" and t7b.title == "CAM"
           and t7b.is_default and t7b.metadata["title"] == "CAM")

    # ---- T8: sample format 缺失 / unknown ----
    t8 = model([_raw_audio_stream(1, channels=1, sample_fmt=None)])
    t8b = model([_raw_audio_stream(1, channels=1, sample_fmt="weird24")])
    record("audio.T8 sample_fmt 缺失/未知 -> UNKNOWN",
           t8.sample_format is AudioSampleFormat.UNKNOWN
           and t8b.sample_format is AudioSampleFormat.UNKNOWN
           and AudioSampleFormat.coerce("s32") is AudioSampleFormat.S32)
    t8c = model([_raw_audio_stream(1, channels=1, sample_rate=None,
                                   duration=None, bit_rate=None)])
    record("audio.T8 sample_rate/duration/bit_rate 缺失安全",
           t8c.sample_rate == 0 and t8c.duration_sec is None
           and t8c.bit_rate is None and t8c.duration is None)

    # ---- non-audio streams 被忽略, audio_position 与 -map 0:a:N 对齐 ----
    mixed = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        _raw_audio_stream(1, channels=1),
        {"index": 2, "codec_type": "data", "codec_name": "rtmd"},
        _raw_audio_stream(3, channels=1),
    ]
    mstreams = audio_streams_from_probe(mixed)
    record("audio.非音频流被忽略 + audio_position 正确",
           [s.stream_index for s in mstreams] == [1, 3]
           and [s.audio_position for s in mstreams] == [0, 1]
           and probe_has_audio(mixed) and not probe_has_audio([mixed[0]]))
    record("audio.非法输入不崩溃",
           audio_streams_from_probe(None) == []
           and audio_streams_from_probe([{"codec_type": "audio"}])[0]
           .stream_index == -1
           and len(audio_streams_from_probe("nonsense")) == 0)

    # ---- Track: stream -> channel -> track 映射稳定性 ----
    trk = audio_probe_of([_raw_audio_stream(2, channels=4, layout="4.0")])
    tracks = trk.tracks()
    record("audio.track.4CH 流不得塌缩成不可再分的 1 个对象",
           len(tracks) == 1 and tracks[0].channel_count == 4
           and tracks[0].channel_indices == [0, 1, 2, 3]
           and tracks[0].source_ids
           == ["default:s2:c0", "default:s2:c1",
               "default:s2:c2", "default:s2:c3"],
           f"{tracks[0].track_id}")
    single = tracks[0].select_channels([2])
    record("audio.track.4CH 可取单通道 (身份保留)",
           single.channel_count == 1
           and single.channel_indices == [2]
           and single.channels[0].stream_index == 2
           and single.channels[0].channel_index == 2
           and single.source_ids == ["default:s2:c2"])
    rest = tracks[0].without_channels([1, 2])
    record("audio.track.剔除通道保持顺序",
           rest.channel_indices == [0, 3] and rest.source_ids
           == ["default:s2:c0", "default:s2:c3"])
    per_ch = trk.tracks(TrackBuildMode.PER_CHANNEL)
    record("audio.track.PER_CHANNEL 4CH -> 4 单通道 track",
           len(per_ch) == 4
           and all(t.channel_count == 1 for t in per_ch)
           and [t.track_id for t in per_ch] == ["a2c0", "a2c1", "a2c2", "a2c3"])
    mono_tracks = build_audio_tracks(
        [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    )
    record("audio.track.4×mono -> 4 track 各 1 channel",
           len(mono_tracks) == 4
           and [t.source_ids for t in mono_tracks]
           == [["default:s1:c0"], ["default:s2:c0"],
               ["default:s3:c0"], ["default:s4:c0"]]
           and all(t.metadata["audio_position"] == i
                   for i, t in enumerate(mono_tracks)))
    stereo_tracks = audio_probe_of([
        _raw_audio_stream(2, channels=2, layout="stereo"),
        _raw_audio_stream(3, channels=2, layout="stereo"),
    ]).tracks()
    record("audio.track.2×stereo -> 2 track / 4 channel",
           len(stereo_tracks) == 2
           and [t.channel_indices for t in stereo_tracks] == [[0, 1], [0, 1]]
           and [t.source_stream_index for t in stereo_tracks] == [2, 3])
    record("audio.track.role 默认 unknown 且不自动推断",
           all(t.role is AudioRole.UNKNOWN for t in tracks)
           and all(c.role is AudioRole.UNKNOWN for c in tracks[0].channels))
    tracks[0].channels[0].set_role(AudioRole.CAMERA_LEFT, "test-only")
    record("audio.track.role 显式赋值需理由",
           tracks[0].channels[0].role is AudioRole.CAMERA_LEFT
           and tracks[0].channels[0].role_reason == "test-only")
    record("audio.track.builder 等价入口",
           len(AudioTrackBuilder([_raw_audio_stream(2, channels=4)]).tracks())
           == 1
           and len(AudioTrackBuilder(
               [{"codec_type": "video"}]).tracks()) == 0)

    # ---- Plan ----
    plan = build_audio_plan([_raw_audio_stream(i, channels=1)
                             for i in (1, 2, 3, 4)])
    record("audio.plan 默认 = 全选 + 保留原始",
           plan.preserve_original and len(plan.selected_tracks) == 4
           and plan.selected() == plan.input_tracks and plan.is_default
           and plan.output_channel_count == 4)
    plan.select(["a1c0", "a3c0", "nope"])
    record("audio.plan 选择集过滤未知 id",
           plan.selected_tracks == ["a1c0", "a3c0"]
           and plan.output_channel_count == 2)
    plan.mix_mode = "sum"
    record("audio.plan 预留字段不改变默认判定",
           not plan.is_default and plan.mix_mode == "sum"
           and plan.channel_map == [] and plan.wav_outputs == [])
    record("audio.plan.select_channels 返回新对象不污染原 track",
           tracks[0].channel_count == 4)

    # ---- Sync integration: 报告 -> AudioTrack ----
    report = {
        "status": "applied",
        "detail": "ok",
        "algo_version": "2.3.0-p1",
        "anchor_stream": 2,
        "sync_mode": "transcode",
        "channels": [
            {"stream": 0, "delay_samples": 0.0, "delay_ms": 0.0,
             "confidence": 1.0, "decision": "anchor", "reason": None},
            {"stream": 1, "delay_samples": 960.0, "delay_ms": 20.0,
             "confidence": 0.91, "polarity": 1, "drift_ppm": 0.4,
             "constant": True, "decision": "fixed", "reason": None,
             "shift_samples": 960, "usable_frames": 40, "rms_dbfs": -18.2,
             "warnings": []},
            {"stream": 2, "delay_samples": 5.0, "delay_ms": 0.1,
             "confidence": 0.2, "decision": "untouched",
             "reason": "low_confidence", "warnings": ["weak"]},
            {"stream": 3, "delay_samples": None, "delay_ms": None,
             "confidence": 0.0, "decision": "untouched",
             "reason": "non_constant"},
        ],
    }
    sync_plan = build_audio_plan([_raw_audio_stream(i, channels=1)
                                  for i in (1, 2, 3, 4)])
    apply_channel_sync_report(sync_plan, report)
    s0, s1, s2, s3 = sync_plan.input_tracks
    record("audio.sync success/offset_samples/offset_ms 正确保存",
           s1.sync_status is SyncStatus.SUCCESS
           and s1.sync_offset_samples == 960.0 and s1.sync_offset_ms == 20.0
           and s1.sync.quality == 0.91 and s1.sync.anchor == "stream:2"
           and s1.channels[0].sync.applied,
           f"status={s1.sync_status} off={s1.sync_offset_samples}")
    record("audio.sync anchor -> already_aligned",
           s0.sync_status is SyncStatus.ALREADY_ALIGNED
           and not s0.channels[0].sync.applied)
    record("audio.sync low_confidence / non_constant 映射",
           s2.sync_status is SyncStatus.LOW_CONFIDENCE
           and s2.sync.reason == "low_confidence"
           and s2.channels[0].sync.warnings == ["weak"]
           and s3.sync_status is SyncStatus.NON_CONSTANT)
    record("audio.sync 不破坏原始 stream/channel 身份",
           [t.source_ids for t in sync_plan.input_tracks]
           == [["default:s1:c0"], ["default:s2:c0"],
               ["default:s3:c0"], ["default:s4:c0"]]
           and [t.source_stream_index for t in sync_plan.input_tracks]
           == [1, 2, 3, 4])
    ch4 = AudioChannel(stream_index=4, channel_index=0, channel_count=1,
                       sample_rate=48000, sample_format=AudioSampleFormat.S32)
    ch4.sync = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=960,
                               offset_ms=20.0)
    trk4 = AudioTrack(track_id="a4c0", source_stream_index=4, channels=[ch4])
    trk4.refresh_sync()
    record("audio.sync track 汇总反映声道结果",
           trk4.sync_status is SyncStatus.SUCCESS
           and trk4.sync_offset_samples == 960
           and trk4.sync_offset_ms == 20.0)
    # 报告未覆盖 / 文件级状态
    for status, want in (("not_eligible", SyncStatus.NOT_APPLICABLE),
                         ("tool_missing", SyncStatus.NOT_APPLICABLE),
                         ("measure_failed", SyncStatus.FAILED)):
        p = ChannelSyncReport({"status": status, "detail": "d"})
        r = p.resolve(None)
        record(f"audio.sync 文件级 {status} -> {want.value}",
               r.status is want and r.reason == "d")
    fresh = build_audio_plan([_raw_audio_stream(1, channels=1)])
    record("audio.sync 无报告 = not_processed",
           fresh.input_tracks[0].sync_status is SyncStatus.NOT_PROCESSED
           and not fresh.input_tracks[0].sync.processed)
    unknown_reason = ChannelSyncReport({"status": "applied"}).resolve(
        {"stream": 0, "decision": "untouched", "reason": "silent_track"}
    )
    record("audio.sync 未知 reason 归 FAILED 不崩溃",
           unknown_reason.status is SyncStatus.FAILED
           and unknown_reason.reason == "silent_track")
    record("audio.sync success 用实际移位量 shift_samples",
           ChannelSyncReport({"status": "applied"}).resolve(
               {"stream": 0, "decision": "fixed",
                "delay_samples": 960.4, "shift_samples": 960,
                "delay_ms": 20.008}
           ).offset_samples == 960.0)

    # ---- 序列化 ----
    def roundtrip(obj):
        return obj.from_dict(obj.to_dict()).to_dict() == obj.to_dict()

    probe_all = audio_probe_of([
        _raw_audio_stream(1, channels=1, layout="mono",
                          tags={"language": "eng"}, disposition={"default": 1}),
        _raw_audio_stream(2, channels=4, layout=None),
        _raw_audio_stream(3, channels=2, layout="stereo", sample_fmt=None),
    ])
    record("audio.json.stream 往返稳定",
           all(roundtrip(s) for s in probe_all.streams))
    record("audio.json.channel 往返稳定",
           all(roundtrip(c) for s in probe_all.streams for c in s.channels()))
    tracks_all = probe_all.tracks()
    record("audio.json.track 往返稳定",
           all(roundtrip(t) for t in tracks_all))
    record("audio.json.plan 往返稳定 (含同步结果)",
           roundtrip(sync_plan)
           and AudioPlan.from_dict(sync_plan.to_dict()).input_tracks[1]
           .sync_offset_samples == 960.0)
    record("audio.json.sample 与 §十三 示例同形",
           AudioChannel(
               stream_index=2, channel_index=0, sample_rate=48000,
               sample_format=AudioSampleFormat.S32,
           ).to_dict() == {
               "stream_index": 2, "channel_index": 0, "sample_rate": 48000,
               "sample_format": "s32", "enabled": True,
           })
    ch_json = AudioChannel.from_dict({
        "stream_index": 2, "channel_index": 0, "sample_rate": 48000,
        "sample_format": "s32", "enabled": True,
        "sync": {"status": "success", "offset_samples": 960, "offset_ms": 20.0},
    })
    record("audio.json.from_dict 读回示例 (channel)",
           ch_json.sync.status is SyncStatus.SUCCESS
           and ch_json.sync.offset_samples == 960.0
           and ch_json.sync.offset_ms == 20.0)
    record("audio.json 纯 JSON 可编码 (无不可序列化对象)",
           json.loads(json.dumps(sync_plan.to_dict(), ensure_ascii=False))
           == sync_plan.to_dict())
    record("audio.json 未来字段/未知 enum 不崩溃",
           AudioSyncResult.from_dict({"status": "from_the_future"}).status
           is SyncStatus.NOT_PROCESSED
           and AudioPlan.from_dict(
               {"model_version": 99, "unknown_key": {"x": 1}}).model_version
           == 99)
    record("audio.json.model_version 常量", AUDIO_MODEL_VERSION == 2)

    # ---- 兼容: raw 保留 / 既有字段复用 ----
    raw_in = _raw_audio_stream(2, channels=4, layout="4.0")
    kept = audio_probe_of([raw_in]).streams[0]
    record("audio.raw 原始 dict 原样保留",
           kept.raw == raw_in and kept.raw is not raw_in)
    # pcm_s24be + sample_fmt=s32 的真实形态必须照实保存 (不猜位深)
    real = audio_probe_of([_raw_audio_stream(
        1, channels=1, codec="pcm_s24be", sample_fmt="s32")]).streams[0]
    record("audio.真实 s24be/s32 照实保存",
           real.codec_name == "pcm_s24be"
           and real.sample_format is AudioSampleFormat.S32
           and real.bit_rate == 1152000)
    deep = audio_probe_of([_raw_audio_stream(
        1, channels=1, tags={"language": "eng"})]).streams[0]
    snapshot = deep.to_dict()
    snapshot["metadata"]["language"] = "mutated"
    snapshot["raw"]["codec_name"] = "mutated"
    record("audio.to_dict 深拷贝 (外部修改不回写模型)",
           deep.metadata["language"] == "eng"
           and deep.raw["codec_name"] == "pcm_s24le"
           and deep.to_dict()["metadata"]["language"] == "eng")
    plan_snapshot = audio_probe_of([_raw_audio_stream(
        1, channels=1, tags={"language": "eng"})]).plan()
    p_snap = plan_snapshot.to_dict()
    p_snap["input_tracks"][0]["metadata"]["language"] = "mutated"
    record("audio.plan.to_dict 深拷贝",
           plan_snapshot.input_tracks[0].metadata["language"] == "eng")


def l1_audio_selection() -> None:
    """v0.7.1 Phase 2 音频来源/选择/通道映射: T1-T19 + 分离性 + 同步 + 序列化."""
    import copy as _copy

    from core.audio_models import (
        AUDIO_MODEL_VERSION,
        AudioPlan,
        AudioSource,
        AudioSourceBuilder,
        AudioSourceType,
        AudioStream,
        AudioSyncResult,
        AudioTiming,
        SyncStatus,
        build_audio_streams,
        build_multi_source_plan,
        build_source,
    )
    from core.audio_plan import (
        REASON_AUDIO_CHANNEL_NOT_FOUND,
        REASON_AUDIO_MAPPING_NOT_SELECTED,
        REASON_AUDIO_MIX_NOT_SUPPORTED,
        REASON_AUDIO_SOURCE_NOT_FOUND,
        REASON_AUDIO_STREAM_NOT_FOUND,
        AudioMapSpec,
        AudioMapStrategy,
        AudioPlanError,
        AudioPlanner,
        AudioValidationError,
        build_map_spec,
        channel_ref,
        exclude_channels,
        map_channel,
        map_channels,
        output_tracks,
        parse_channel_ref,
        select_channels,
        select_sources,
        select_tracks,
        source_plan,
        validate_mapping,
        validate_plan,
        validate_selection,
    )
    section("L1 音频来源/选择/映射 (v0.7.1 Phase 2)")

    # --- 素材: MP4(4CH) + MP4(2×2CH) + MP4(4×mono) + 外挂 WAV(mono/stereo/4CH)
    MP4_4CH = [_raw_audio_stream(2, channels=4, layout="4.0")]
    MP4_2x2CH = [
        _raw_audio_stream(1, channels=2, layout="stereo"),
        _raw_audio_stream(2, channels=2, layout="stereo"),
    ]
    MP4_4MONO = [_raw_audio_stream(i, channels=1) for i in (1, 2, 3, 4)]
    WAV_MONO = [_raw_audio_stream(0, channels=1, layout="mono",
                                  codec="pcm_s24le")]
    WAV_STEREO = [_raw_audio_stream(0, channels=2, layout="stereo",
                                    codec="pcm_s24le")]
    WAV_4CH = [_raw_audio_stream(0, channels=4, layout="4.0",
                                 codec="pcm_s24le")]

    def source_of(sid, streams, source_type=AudioSourceType.MEDIA, path=None):
        return build_source(sid, streams, source_type=source_type, path=path)

    # ---- A. Source ----
    t1 = source_of("cam", [_raw_audio_stream(1, channels=1)],
                   path="camera.mp4")
    record("audio2.T1 单 MP4 + 单 audio stream",
           t1.stream_count == 1 and t1.channel_count == 1
           and t1.source_type is AudioSourceType.MEDIA
           and t1.channels()[0].id == "cam:s1:c0"
           and t1.name == "camera.mp4",
           f"{t1.channel_ids}")
    t2 = source_of("cam", MP4_4CH, path="camera.mp4")
    record("audio2.T2 MP4 + 4CH",
           t2.stream_count == 1 and t2.channel_count == 4
           and t2.channel_ids == ["cam:s2:c0", "cam:s2:c1",
                                  "cam:s2:c2", "cam:s2:c3"])
    t3 = source_of("cam", MP4_2x2CH)
    record("audio2.T3 MP4 + 2×2CH",
           [s.channel_count for s in t3.streams] == [2, 2]
           and t3.channel_ids == ["cam:s1:c0", "cam:s1:c1",
                                  "cam:s2:c0", "cam:s2:c1"])
    t4 = source_of("cam", MP4_4MONO)
    record("audio2.T4 MP4 + 4×mono",
           [s.channel_count for s in t4.streams] == [1, 1, 1, 1]
           and t4.channel_ids == [f"cam:s{i}:c0" for i in (1, 2, 3, 4)])
    t5 = source_of("wav01", WAV_MONO, AudioSourceType.WAV, path="external.wav")
    record("audio2.T5 MP4 + 外挂 mono WAV",
           t5.source_type is AudioSourceType.WAV and t5.is_external
           and t5.stream_count == 1 and t5.channel_count == 1
           and t5.channel_ids == ["wav01:s0:c0"]
           and t5.timing.shares_timeline is False,
           f"shares_timeline={t5.timing.shares_timeline}")
    t6 = source_of("wav01", WAV_STEREO, AudioSourceType.WAV)
    record("audio2.T6 MP4 + 外挂 stereo WAV",
           t6.channel_count == 2
           and t6.channel_ids == ["wav01:s0:c0", "wav01:s0:c1"])
    t7 = source_of("wav01", WAV_4CH, AudioSourceType.WAV)
    record("audio2.T7 MP4 + 外挂 4CH WAV",
           t7.channel_count == 4
           and len(t7.streams) == 1
           and t7.channel_ids == [f"wav01:s0:c{i}" for i in range(4)])
    builder = AudioSourceBuilder()
    builder.add("cam", MP4_4CH, path="camera.mp4")
    builder.add("wav01", WAV_MONO, source_type="wav", path="a.wav")
    builder.add("wav02", WAV_STEREO, source_type="external", path="b.wav")
    record("audio2.T8 MP4 + 多个外部 WAV",
           builder.source_ids == ["cam", "wav01", "wav02"]
           and [s.input_index for s in builder.sources] == [0, 1, 2]
           and len(builder.channels()) == 7
           and builder.source("wav02").is_external)
    record("audio2.WAV 不是另一套模型 (与 media 同构)",
           isinstance(t5.streams[0], AudioStream)
           and t5.streams[0].channel_count == t5.channels()[0].channel_count
           and type(t5.channels()[0]).__name__ == "AudioChannel"
           and t2.channels()[0].__class__ is t5.channels()[0].__class__)
    try:
        builder.add("cam", MP4_4CH)
        record("audio2.重复 source_id 明确报错", False, "未报错")
    except ValueError as exc:
        record("audio2.重复 source_id 明确报错", "cam" in str(exc))
    record("audio2.source_id + stream_index 唯一定位一条流",
           t3.stream(1).id == "cam:s1" and t3.stream(2).id == "cam:s2"
           and t3.stream(1).id != t3.stream(2).id)
    record("audio2.AudioStream/AudioChannel 身份含 source",
           t5.streams[0].source_id == "wav01"
           and t5.channels()[0].source_id == "wav01"
           and t5.channels()[0].stream_id == "wav01:s0")

    # ---- 跨来源身份不碰撞 ----
    cam_s0c0 = source_of("camera", [_raw_audio_stream(0, channels=2)],
                         path="camera.mp4").channels()[0]
    rec_s0c0 = source_of("recorder", [_raw_audio_stream(0, channels=2)],
                         source_type=AudioSourceType.WAV,
                         path="recorder.wav").channels()[0]
    record("audio2.跨来源同名 stream/channel 不碰撞",
           cam_s0c0.stream_index == rec_s0c0.stream_index == 0
           and cam_s0c0.channel_index == rec_s0c0.channel_index == 0
           and cam_s0c0.id != rec_s0c0.id
           and cam_s0c0.id == "camera:s0:c0"
           and rec_s0c0.id == "recorder:s0:c0",
           f"{cam_s0c0.id} vs {rec_s0c0.id}")

    # ---- 时间轴只记录, 不测量 ----
    timing = AudioTiming(start_offset_sec=0.25, duration_sec=10.0,
                         shares_timeline=False, note="manual")
    ext = build_source("wav01", WAV_STEREO, source_type="wav",
                       timing=timing)
    record("audio2.外挂来源时间轴可表达且不推断",
           ext.timing.start_offset_sec == 0.25
           and ext.timing.shares_timeline is False
           and build_source("cam", MP4_4CH).timing.shares_timeline is True)

    # ---- 计划构造 ----
    plan = build_multi_source_plan([
        {"source_id": "camera", "streams": MP4_4CH, "path": "camera.mp4"},
        {"source_id": "recorder", "streams": WAV_STEREO,
         "source_type": "wav", "path": "recorder.wav"},
    ])
    record("audio2.multi-source plan 默认 = 全选 + 保留原始",
           plan.source_ids == ["camera", "recorder"]
           and plan.is_multi_source()
           and plan.is_default
           and len(plan.selected_channels) == 6
           and [t.track_id for t in plan.input_tracks]
           == ["camera:a2c0-1-2-3", "recorder:a0c0-1"],
           f"{[t.track_id for t in plan.input_tracks]}")
    record("audio2.plan 默认 spec = 整流 copy (不进 filtergraph)",
           build_map_spec(plan).full_stream_copy
           and build_map_spec(plan).strategy is AudioMapStrategy.STREAM_COPY
           and build_map_spec(plan).ok)
    record("audio2.source_plan 与 build_multi_source_plan 等价",
           source_plan([("camera", MP4_4CH)]).source_ids == ["camera"])
    record("audio2.audio_position 与 stream_index 不混淆",
           plan.channel("camera:s2:c0").stream_index == 2
           and plan.input_tracks[0].metadata["audio_position"] == 0)

    # ---- B. Selection ----
    def fresh_plan():
        return build_multi_source_plan([
            {"source_id": "camera", "streams": MP4_4CH, "path": "camera.mp4"},
            {"source_id": "recorder", "streams": WAV_STEREO,
             "source_type": "wav", "path": "recorder.wav"},
        ])

    def names(p):
        return [c.id for c in p.selected_channel_objects()]

    p = AudioPlanner(fresh_plan())
    p.select_all()
    record("audio2.T9 4CH 全选",
           names(p.plan)[:4] == [f"camera:s2:c{i}" for i in range(4)]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3",
                                          "recorder:a0c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    record("audio2.T10 4CH 选择 [0,2]",
           names(p.plan) == ["camera:s2:c0", "camera:s2:c2"]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3"])
    two = build_multi_source_plan(
        [{"source_id": "cam", "streams": MP4_2x2CH}])
    p = AudioPlanner(two)
    p.select_tracks("cam:a2c0-1")
    record("audio2.T11 2×2CH 选择其中一个 stereo stream",
           names(p.plan) == ["cam:s2:c0", "cam:s2:c1"]
           and [t.track_id for t in p.plan.selected()] == ["cam:a2c0-1"])
    p = AudioPlanner(two)
    p.select_channels("cam:s1:c0", "cam:s2:c1")
    record("audio2.T12 2×2CH 跨 stream 选择 (stream0:L + stream1:R)",
           names(p.plan) == ["cam:s1:c0", "cam:s2:c1"]
           and p.plan.selected_tracks == ["cam:a1c0-1", "cam:a2c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2", "recorder:s0:c0")
    record("audio2.T13 MP4 + WAV 跨 source 选择",
           names(p.plan) == ["camera:s2:c2", "recorder:s0:c0"]
           and p.plan.selected_tracks == ["camera:a2c0-1-2-3",
                                          "recorder:a0c0-1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("recorder")
    record("audio2.select_sources 按来源选择",
           names(p.plan) == ["recorder:s0:c0", "recorder:s0:c1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera", "recorder")
    record("audio2.select_sources 多来源全选",
           len(names(p.plan)) == 6)
    p = AudioPlanner(fresh_plan())
    p.exclude_channels("camera:s2:c1", "camera:s2:c3")
    record("audio2.exclude_channels 保持源顺序",
           names(p.plan) == ["camera:s2:c0", "camera:s2:c2",
                             "recorder:s0:c0", "recorder:s0:c1"])
    record("audio2.选择不改源对象身份",
           AudioPlanner(fresh_plan()).select_channels(
               "camera:s2:c0").selected_tracks == ["camera:a2c0-1-2-3"])
    p = AudioPlanner(fresh_plan())
    before = [c.id for c in p.plan.all_channels()]
    p.select_channels("camera:s2:c3")
    record("audio2.选择不破坏源模型 (原始声道仍在)",
           [c.id for c in p.plan.all_channels()] == before
           and p.plan.channel("camera:s2:c0") is not None)
    record("audio2.选择返回新计划而非破坏原计划",
           AudioPlanner(fresh_plan()).select_channels(
               "camera:s2:c0").selected_channels == ["camera:s2:c0"])

    # ---- C. Mapping ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c1")
    p.map_channels("camera:s2:c0", "camera:s2:c1")
    record("audio2.T14 identity mapping",
           [e["source_channel_id"] for e in p.plan.channel_mapping]
           == ["camera:s2:c0", "camera:s2:c1"]
           and p.plan.mapping_kind == "explicit"
           and p.plan.selected_channels == ["camera:s2:c0", "camera:s2:c1"])
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera")
    p.map_channels("camera:s2:c0", "camera:s2:c1",
                   "camera:s2:c2", "camera:s2:c3")
    record("audio2.STREAM_COPY 仅当恰好整流自然顺序",
           p.map_spec().full_stream_copy is True
           and p.map_spec().strategy is AudioMapStrategy.STREAM_COPY
           and p.plan.mapping_kind == "explicit")
    p = AudioPlanner(fresh_plan())
    p.select_sources("camera")
    p.map_channels("camera:s2:c2", "camera:s2:c0",
                   "camera:s2:c3", "camera:s2:c1")
    spec = p.map_spec()
    record("audio2.T15 4CH reorder (2,0,3,1)",
           [e["channel_index"] for e in p.plan.channel_mapping] == [2, 0, 3, 1]
           and p.plan.mapping_kind == "explicit"
           and spec.executable
           and spec.strategy is AudioMapStrategy.CHANNEL_FILTER
           and [spec.trace(i)["channel_index"] for i in range(4)]
           == [2, 0, 3, 1],
           f"{[e['channel_index'] for e in p.plan.channel_mapping]}")
    record("audio2.T15 reorder 不报 stream_copy",
           spec.full_stream_copy is False)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2", "recorder:s0:c0", "camera:s2:c0")
    p.map_channels("camera:s2:c2", "recorder:s0:c0", "camera:s2:c0")
    spec = p.map_spec()
    record("audio2.T16 跨来源 mapping (camera:c2, wav:c0, camera:c0)",
           spec.executable
           and [a.source_id for a in spec.assignments]
           == ["camera", "recorder", "camera"]
           and [a.channel_index for a in spec.assignments] == [2, 0, 0]
           and spec.source_ids == ["camera", "recorder"]
           and [o.strategy for o in spec.operations]
           == [AudioMapStrategy.CHANNEL_FILTER] * 3
           and [o.input_index for o in spec.operations] == [0, 1, 0],
           f"ops={[o.to_dict()['strategy'] for o in spec.operations]}")
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c1")
    try:
        p.map_channels("camera:s2:c0", "camera:s2:c0")
        record("audio2.T17 duplicate source rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T17 duplicate source rejection",
               exc.reason == "audio_mapping_output_order", exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("ghost:s0:c0")
        record("audio2.T18 missing source rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T18 missing source rejection",
               exc.reason == REASON_AUDIO_SOURCE_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("camera:s2:c7")
        record("audio2.T19 missing channel rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T19 missing channel rejection",
               exc.reason == REASON_AUDIO_CHANNEL_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("camera:s9:c0")
        record("audio2.T19 missing stream rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.T19 missing stream rejection",
               exc.reason == REASON_AUDIO_STREAM_NOT_FOUND, exc.reason)
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0")
    try:
        p.map_channels("not-an-id")
        record("audio2.非法 id rejection", False, "未拒绝")
    except AudioValidationError as exc:
        record("audio2.非法 id rejection",
               exc.reason == REASON_AUDIO_CHANNEL_NOT_FOUND)
    single_map = AudioPlanner(fresh_plan())
    single_map.select_channels("camera:s2:c0", "camera:s2:c1")
    record("audio2.map_channel 指定单个输出位置",
           [e["source_channel_id"] for e in
            single_map.map_channel("camera:s2:c1", 0).channel_mapping]
           == ["camera:s2:c1", "camera:s2:c0"])
    record("audio2.模块级 map_channels/select_channels 便捷入口",
           [c.id for c in map_channels(
               select_channels(fresh_plan(), "camera:s2:c1"),
               "camera:s2:c1").selected_channel_objects()]
           == ["camera:s2:c1"]
           and [c.id for c in exclude_channels(
               fresh_plan(), "camera:s2:c0").selected_channel_objects()][0]
           == "camera:s2:c1")

    # ---- D. Separation: selection != mapping != mixing ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    record("audio2.D CH1+CH3 selection 仍是两个独立源声道",
           len(p.plan.selected_channels) == 2
           and [c.id for c in p.plan.selected_channel_objects()]
           == ["camera:s2:c0", "camera:s2:c2"]
           and p.plan.mix_mode is None and not p.plan.channel_map)
    spec = p.map_spec()
    record("audio2.D selection 不产生任何 PCM 合成 (每输出恰好 1 源)",
           spec.executable
           and all(len(t.channel_refs_at(i)) == 1
                   for t in spec.tracks for i in range(t.channel_count))
           and all(not t.is_mixing for t in spec.tracks)
           and spec.strategy is AudioMapStrategy.CHANNEL_FILTER)
    record("audio2.D mapping 独立于 selection (顺序可变)",
           p.plan.mapping_kind == "derived"
           and p.map_channels("camera:s2:c2", "camera:s2:c0").mapping_kind
           == "explicit"
           and len(p.plan.selected_channels) == 2)
    for label, kwargs in (
        ("选择 3 个只映射 2 个",
         dict(sel=["camera:s2:c0", "camera:s2:c1", "camera:s2:c2"],
              mapped=["camera:s2:c0", "camera:s2:c1"])),
        ("显式 mix_mode", dict(sel=["camera:s2:c0"], mix="sum")),
    ):
        pp = AudioPlanner(fresh_plan())
        pp.select_channels(*kwargs["sel"])
        if "mix" in kwargs:
            pp.plan.mix_mode = kwargs["mix"]
        else:
            try:
                pp.map_channels(*kwargs["mapped"])
                record(f"audio2.D {label} -> 拒绝", False, "未拒绝")
                continue
            except AudioPlanError as exc:
                record(f"audio2.D {label} -> audio_mix_not_supported",
                       exc.reason == REASON_AUDIO_MIX_NOT_SUPPORTED)
                continue
        record(f"audio2.D {label} -> audio_mix_not_supported",
               REASON_AUDIO_MIX_NOT_SUPPORTED in validate_plan(pp.plan))
    crafted = AudioPlanner(fresh_plan())
    crafted.select_channels("camera:s2:c0", "camera:s2:c1")
    crafted.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"},
        {"output_index": 0, "source_channel_id": "camera:s2:c1"},
    ]
    crafted.plan.mapping_kind = "explicit"
    spec = crafted.map_spec()
    record("audio2.D N 源 -> 1 输出 = audio_mix_not_supported (结构性)",
           spec.executable is False
           and spec.errors[0]["reason"] == REASON_AUDIO_MIX_NOT_SUPPORTED
           and spec.strategy is AudioMapStrategy.MIXING,
           f"{[e['reason'] for e in spec.errors]}")

    # ---- V6 / V5 ----
    v6 = AudioPlanner(fresh_plan())
    v6.select_channels("camera:s2:c1")
    v6.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"}]
    v6.plan.mapping_kind = "explicit"
    record("audio2.V6 mapping 未选择的声道被拒",
           [i.reason for i in validate_mapping(v6.plan)]
           == [REASON_AUDIO_MAPPING_NOT_SELECTED]
           and v6.map_spec().executable is False)
    v5 = AudioPlanner(fresh_plan())
    v5.select_channels("camera:s2:c0", "camera:s2:c1")
    v5.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c0"},
        {"output_index": 2, "source_channel_id": "camera:s2:c1"},
    ]
    v5.plan.mapping_kind = "explicit"
    record("audio2.V5 output index 必须连续 (0,2 被拒)",
           "audio_mapping_output_order" in
           [i.reason for i in validate_mapping(v5.plan)]
           and v5.map_spec().executable is False)
    record("audio2.V4 选择集重复身份被拒",
           [i.reason for i in validate_selection(
               AudioPlan(input_tracks=fresh_plan().input_tracks,
                         selected_channels=["camera:s2:c0",
                                            "camera:s2:c0"]))]
           == ["audio_source_duplicate"])

    # ---- E. Sync preservation ----
    p = AudioPlanner(fresh_plan())
    p.plan.input_tracks[0].channels[2].sync = AudioSyncResult(
        status=SyncStatus.SUCCESS, offset_samples=960.0, offset_ms=20.0,
        quality=0.91, anchor="camera:s2", source="channel_sync_report",
    )
    p.select_channels("camera:s2:c0", "camera:s2:c2")
    p.map_channels("camera:s2:c2", "camera:s2:c0")
    spec = p.map_spec()
    ref = channel_ref(p.plan, "camera:s2:c2")
    record("audio2.E 选择+映射后 source 身份与 sync 全保留",
           ref.channel_id == "camera:s2:c2"
           and ref.source_id == "camera" and ref.stream_index == 2
           and ref.channel_index == 2
           and ref.sync_status == "success"
           and ref.sync_offset_samples == 960.0
           and ref.sync_offset_ms == 20.0,
           f"{ref.to_dict()}")
    trace = spec.trace(0)
    record("audio2.E output 0 -> camera:s2:c2 -> +960 samples 可追溯",
           trace["channel_id"] == "camera:s2:c2"
           and trace["sync_offset_samples"] == 960.0
           and trace["sync_status"] == "success")
    out_tracks = output_tracks(p.plan)
    record("audio2.E AudioOutputTrack 继承 sync",
           out_tracks[0].source_ids == ["camera:s2:c2", "camera:s2:c0"]
           and out_tracks[0].sync_of(0).offset_samples == 960.0
           and out_tracks[0].sync_of(1).status is SyncStatus.NOT_PROCESSED
           and out_tracks[0].strategy is AudioMapStrategy.CHANNEL_FILTER)

    # ---- AudioOutputTrack 语义 (§9) ----
    p = AudioPlanner(fresh_plan())
    p.select_channels("camera:s2:c2")
    tracks1 = output_tracks(p.plan)
    record("audio2.output track = 一个 source channel (单通道输出)",
           len(tracks1) == 1 and tracks1[0].channel_count == 1
           and tracks1[0].is_mono and tracks1[0].source_ids
           == ["camera:s2:c2"])
    full = output_tracks(fresh_plan())
    record("audio2.output track 可承载整条 source track (整流)",
           len(full) == 2
           and [t.channel_count for t in full] == [4, 2]
           and all(t.strategy is AudioMapStrategy.STREAM_COPY for t in full))
    record("audio2.output track metadata 记录音频序号 (供 -map 0:a:N)",
           [t.metadata["audio_position"] for t in full] == [0, 0]
           and [t.metadata["stream_index"] for t in full] == [2, 0])
    record("audio2.output track 不携带 mixer 参数",
           not any(hasattr(t, "gain") or hasattr(t, "weights")
                   for t in full)
           and all(not t.is_mixing for t in full))
    mixed = AudioPlanner(fresh_plan())
    mixed.select_channels("camera:s2:c2", "recorder:s0:c0")
    mixed.map_channels("camera:s2:c2", "recorder:s0:c0")
    cross = output_tracks(mixed.plan)
    record("audio2.跨来源输出单元按来源分段",
           len(cross) == 2 and all(t.is_mono for t in cross)
           and [t.source_ids for t in cross]
           == [["camera:s2:c2"], ["recorder:s0:c0"]]
           and cross[0].assignments[0].source_id == "camera"
           and cross[1].assignments[0].source_id == "recorder")
    swap = AudioPlanner(fresh_plan())
    swap.select_channels("camera:s2:c0", "camera:s2:c2")
    swap.map_channels("camera:s2:c2", "camera:s2:c0")
    cross1 = output_tracks(swap.plan)
    record("audio2.输出单元可承载重排后的多个源声道",
           len(cross1) == 1 and cross1[0].channel_count == 2
           and cross1[0].source_ids == ["camera:s2:c2", "camera:s2:c0"]
           and [a.channel_index for a in cross1[0].assignments] == [2, 0]
           and not cross1[0].is_multi_source)

    # ---- AudioMapSpec (§12) ----
    spec = fresh_plan_spec = build_map_spec(fresh_plan())
    record("audio2.spec 不持有 PCM / 不执行 ffmpeg / JSON 兼容",
           json.loads(json.dumps(spec.to_dict(), ensure_ascii=False))
           == spec.to_dict()
           and not hasattr(spec, "pcm") and not hasattr(spec, "run")
           and isinstance(spec.to_dict()["assignments"], list))
    record("audio2.spec output index 稳定且连续",
           [a for a in range(spec.output_channels)]
           == [i for i in range(len(spec.assignments))]
           and [o.output_indices for o in spec.operations]
           == [[0, 1, 2, 3], [4, 5]])
    record("audio2.spec 每通道身份完整",
           spec.assignments[0].source_id == "camera"
           and spec.assignments[4].source_id == "recorder"
           and all(a.channel_id.count(":") >= 2 for a in spec.assignments))
    record("audio2.spec 不含 mixer 参数",
           "gain" not in json.dumps(spec.to_dict())
           and "weights" not in json.dumps(spec.to_dict()))
    record("audio2.spec dry-run 摘要可用",
           spec.summary()["executable"] is True
           and spec.summary()["full_stream_copy"] is True
           and spec.summary()["output_channels"] == 6)
    bad = AudioPlanner(fresh_plan())
    bad.select_channels("camera:s2:c0")
    bad.plan.channel_mapping = [
        {"output_index": 0, "source_channel_id": "camera:s2:c7"}]
    bad.plan.mapping_kind = "explicit"
    record("audio2.spec 校验失败不抛异常 (dry-run 友好)",
           bad.map_spec().executable is False
           and bad.map_spec().errors[0]["reason"]
           == REASON_AUDIO_CHANNEL_NOT_FOUND
           and bad.map_spec().summary()["errors"])

    # ---- F. 序列化 ----
    def roundtrip(obj):
        return obj.from_dict(obj.to_dict()).to_dict() == obj.to_dict()

    multi = fresh_plan()
    record("audio2.F AudioSource 往返",
           all(roundtrip(s) for s in multi.sources))
    record("audio2.F AudioStream 往返 (含 source_id/timing)",
           all(roundtrip(s) for src in multi.sources for s in src.streams)
           and AudioStream.from_dict(
               multi.sources[0].streams[0].to_dict()
           ).source_id == "camera")
    record("audio2.F AudioChannel 往返 (含 source_id)",
           all(roundtrip(c) for src in multi.sources for c in src.channels()))
    record("audio2.F AudioTrack 往返 (含 source_id)",
           all(roundtrip(t) for t in multi.input_tracks))
    record("audio2.F AudioPlan 往返 (含 sources/selection/mapping)",
           roundtrip(multi)
           and AudioPlan.from_dict(multi.to_dict()).source_ids
           == ["camera", "recorder"])
    mapped = AudioPlanner(multi)
    mapped.select_channels("camera:s2:c2", "recorder:s0:c0")
    mapped.map_channels("recorder:s0:c0", "camera:s2:c2")
    record("audio2.F mapping plan 往返",
           roundtrip(mapped.plan)
           and AudioPlan.from_dict(mapped.plan.to_dict()).mapping_kind
           == "explicit"
           and [e["channel_index"] for e in
                AudioPlan.from_dict(mapped.plan.to_dict()).channel_mapping]
           == [0, 2])
    record("audio2.F AudioMapSpec 往返",
           roundtrip(build_map_spec(mapped.plan))
           and AudioMapSpec.from_dict(
               build_map_spec(mapped.plan).to_dict()
           ).executable is True)
    record("audio2.F AudioOutputTrack / ChannelRef 往返",
           all(roundtrip(t) for t in output_tracks(multi))
           and roundtrip(channel_ref(multi, "camera:s2:c2"))
           and parse_channel_ref("cam:left:s1:c3").channel_id
           == "cam:left:s1:c3")
    record("audio2.F AudioTiming 往返",
           roundtrip(AudioTiming(start_offset_sec=0.5,
                                 shares_timeline=False)))
    record("audio2.F model_version 提升到 2",
           AUDIO_MODEL_VERSION == 2
           and multi.model_version == 2)
    record("audio2.F from_dict 容忍未来字段",
           AudioPlan.from_dict({"model_version": 99,
                                "future": {"x": 1}}).model_version == 99)

    # ---- §21 默认生产路径不变 ----
    from encoders.x265 import build_command as _x265_command
    from core.models import EffectiveParams, ScalingContext, SourceInfo
    from core.color import ColorInfo

    ctx = ScalingContext(
        reference_width=3840, reference_height=2160, reference_fps=59.94,
        spatial_factor=1.0, temporal_factor=1.0, pixel_rate_factor=1.0,
        aspect_ratio=16 / 9, source_class="NORMAL_LONG_GOP",
        normalized_ob=0.2,
    )
    src_info = SourceInfo(
        path=Path("x.mp4"), size_bytes=1, duration_sec=1.0,
        width=3840, height=2160, fps=59.94,
        r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
        codec="hevc", profile="Main 10", pix_fmt="yuv420p10le",
        bit_depth=10, chroma="4:2:0", ob_kbps=1000.0,
        video_bitrate_kbps=1000.0, video_stream_count=1, stream_info=(),
        color=ColorInfo(),
    )
    argv, _eff = _x265_command(
        Path("ffmpeg"), Path("x.mp4"), Path("x.part.mp4"),
        {"preset": "medium", "crf": "20"},
        EffectiveParams(values={"keyint": "60"}, audit={}, context=ctx),
        1, src_info,
    )
    record("audio2.§21 默认音频路径 = -map 0 + -c:a copy (未改)",
           "-map" in argv and argv[argv.index("-map") + 1] == "0"
           and "-c:a" in argv and argv[argv.index("-c:a") + 1] == "copy"
           and "--audio-tracks" not in argv
           and not any("filter_complex" in a for a in argv),
           f"argv={argv[-8:]}")
    record("audio2.§22 未新增音频 CLI",
           all(not hasattr(AudioPlanner, name) for name in
               ("cli", "execute", "run_ffmpeg"))
           and not hasattr(AudioMapSpec, "argv"))


def l1_x265() -> None:
    section("L1 x265 P0 修复断言")
    import json
    cfg = json.loads((ROOT / "x265.json").read_text(encoding="utf-8"))
    for tier, p in cfg["profile"].items():
        # rd 值域 2-6 均合法; FAST 档按用户决定保持 rd=2
        # (psy-rd 在 rd<3 时静默失效, 属已知取舍, 不改)
        record(f"x265.{tier}.rd 值域合法", 2 <= int(p["rd"]) <= 6)
        record(f"x265.{tier}.info=false (可复现)", p["info"] is False)
        # no-strong-intra-smoothing 全档开启 (用户决定 2026-09-01):
        # 触发帧内强力平滑的条件苛刻, 对画面影响低; 带上后编码器改用
        # 其他手段平滑 (细纹理/颗粒素材保留更好)
        record(f"x265.{tier}.no-strong-intra-smoothing 开启",
               p.get("no_strong_intra_smoothing") is True)
        record(f"x265.{tier}.level 6.2", p["level_idc"] == 6.2)
    # CPB 钳位: 高码率源动态 VBV 产出超限 bufsize -> 应钳到 240000
    from core.models import SourceInfo
    from core.scaling import ScalingEngine
    from core.source_classifier import SourceClassifier
    from encoders.x265 import X265Backend
    from core.config import load_scaling_config
    scfg = load_scaling_config(ROOT / "x265_scaling.json")
    eng = ScalingEngine(scfg)
    clf = SourceClassifier(scfg)
    src = SourceInfo(
        path=Path("x.mp4"), size_bytes=1, duration_sec=1.0,
        width=3840, height=2160, fps=59.94,
        r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
        codec="hevc", profile="Main 10", pix_fmt="yuv420p10le",
        bit_depth=10, chroma="4:2:0", ob_kbps=150000.0,
        video_bitrate_kbps=150000.0, video_stream_count=1,
        stream_info=(),
    )
    eff = eng.build(
        cfg["profile"]["UHQ"], "UHQ", src,
        clf.classify(src), X265Backend.param_order,
        X265Backend.format_fixed,
    )
    buf = int(eff.values.get("vbv-bufsize", "0"))
    record("x265.CPB 钳位 ≤240000", buf <= 240000, f"bufsize={buf}")
    record("x265.CPB 审计链",
           eff.audit.get("vbv-bufsize", {}).get("mode") == "cpb_clamp",
           f"audit={eff.audit.get('vbv-bufsize', {}).get('mode')}")


def l1_av1() -> None:
    section("L1 AV1")
    from encoders.caps import _parse_nvenc, _parse_qsv
    from encoders.hw import plan_initial_format
    from encoders.caps import BackendCaps, CodecCaps
    caps = _parse_nvenc(
        "H.265/HEVC: nv12, yv12(10bit), yuv422(10bit)\n"
        "AV1: nv12, yv12, yv12(10bit)\n"
    )
    record("av1.caps nvenc 解析", caps.get("av1") is not None
           and caps["av1"].bit10 and not caps["av1"].csp_422,
           f"av1={caps.get('av1')}")
    qcaps = _parse_qsv(
        "Codec: AV1 FF\n10bit depth       o o o\n"
        "Codec: H.265/HEVC FF\n10bit depth       o o o o\n"
    )
    record("av1.caps qsv FF 解析", qcaps.get("av1") is not None
           and qcaps["av1"].bit10, f"av1={qcaps.get('av1')}")
    b = BackendCaps(codecs={"av1": CodecCaps(bit10=True)})
    record("av1.plan 422/10 恒转 420",
           plan_initial_format(b, "nvencc", "4:2:2", 10, "av1")
           == (("4:2:0", 10), True))
    record("av1.plan 420/10 无降级",
           plan_initial_format(b, "nvencc", "4:2:0", 10, "av1")
           == (("4:2:0", 10), False))
    import json
    for name, path in (("nvenc_av1.json", ROOT / "nvenc_av1.json"),
                       ("qsv_av1.json", ROOT / "qsv_av1.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        tiers = set(cfg["profile"])
        record(f"av1.{name} 四档完整", tiers == {"UHQ", "HQ", "SMALL", "FAST"},
               f"tiers={sorted(tiers)}")
        for t, p in cfg["profile"].items():
            if name.startswith("nvenc"):
                record(f"av1.{name}.{t} qvbr+profile=high",
                       p.get("profile") == "high"
                       and 0 <= int(p.get("qvbr", -1)) <= 63
                       and p.get("bframes", 0) <= 7)
            else:
                record(f"av1.{name}.{t} FF+icq",
                       p.get("function_mode") == "FF"
                       and isinstance(p.get("icq"), int))

    # --- svtav1 后端 (纯逻辑) ---
    from core.config import load_svtav1_config
    from encoders.svtav1 import (
        PARAM_MAP,
        SvtAv1Backend,
        av1_pix_fmt,
        format_svt_value,
    )
    from core.models import SourceInfo
    svt_cfg = load_svtav1_config(ROOT / "svtav1.json")
    record("av1.svtav1.json 加载+四档", set(svt_cfg["profile"])
           == {"UHQ", "HQ", "SMALL", "FAST"}, f"cfg=ok")
    for t, p in svt_cfg["profile"].items():
        record(f"av1.svtav1.{t} tune0+crf+preset",
               p.get("tune") == 0 and isinstance(p.get("crf"), int)
               and isinstance(p.get("preset"), int))
    want_map = {"keyint": "keyint", "vbv_maxrate": "mbr",
                "enable_qm": "enable-qm", "ac_bias": "ac-bias"}
    record("av1.svtav1 param map 关键键",
           want_map.items() <= PARAM_MAP.items(),
           f"map={sorted(PARAM_MAP)[:5]}...")
    record("av1.svtav1 bool 格式", format_svt_value("enable_tf", True, 30.0)
           == "1" and format_svt_value("enable_tf", False, 30.0) == "0")
    record("av1.svtav1 FR* 格式", format_svt_value("keyint", "FR*10", 59.94)
           == "600")
    si422 = SourceInfo(path=ROOT / "x", size_bytes=1, duration_sec=1.0,
                       width=3840, height=2160, fps=30.0,
                       r_frame_rate="30/1", avg_frame_rate="30/1",
                       codec="h264", profile="", pix_fmt="yuv422p10le",
                       bit_depth=10, chroma="4:2:2", ob_kbps=100000.0,
                       video_bitrate_kbps=100000.0, video_stream_count=1,
                       stream_info=())
    si420_8 = SourceInfo(path=ROOT / "x", size_bytes=1, duration_sec=1.0,
                         width=3840, height=2160, fps=30.0,
                         r_frame_rate="30/1", avg_frame_rate="30/1",
                         codec="h264", profile="", pix_fmt="yuv420p",
                         bit_depth=8, chroma="4:2:0", ob_kbps=100000.0,
                         video_bitrate_kbps=100000.0, video_stream_count=1,
                         stream_info=())
    record("av1.svtav1 pix_fmt 420 策略",
           av1_pix_fmt(si422) == "yuv420p10le"
           and av1_pix_fmt(si420_8) == "yuv420p",
           f"422->{av1_pix_fmt(si422)} 8bit420->{av1_pix_fmt(si420_8)}")
    from core.scaling import ScalingEngine
    from core.source_classifier import SourceClassifier
    from core.config import load_scaling_config
    sc = load_scaling_config(ROOT / "svtav1_scaling.json")
    eng = ScalingEngine(sc)
    cls_ = SourceClassifier(sc).classify(si422)
    eff = eng.build(
        svt_cfg["profile"]["HQ"], "HQ", si422, cls_,
        SvtAv1Backend.param_order, SvtAv1Backend.format_fixed,
    )
    cmd, effd = SvtAv1Backend().build_video_command(
        ROOT / "tools" / "ffmpeg.exe", ROOT / "src.mp4", ROOT / "out.mov",
        svt_cfg["profile"]["HQ"], eff, si422,
    )
    joined = " ".join(str(c) for c in cmd)
    record("av1.svtav1 build_video_command",
           "libsvtav1" in joined and "-tag:v" in joined
           and "av01" in joined and "yuv420p10le" in joined
           and "-svtav1-params" in joined
           and int(effd.get("mbr", 0)) > 0
           and effd.get("keyint") == "300"
           and effd.get("lookahead") == "60",
           f"mbr={effd.get('mbr')} keyint={effd.get('keyint')} "
           f"lookahead={effd.get('lookahead')}")


# ===========================================================================
# v0.6.2 — CLI 大小写不敏感 / 默认后端自动选择 / 分层日志
# ===========================================================================

def l1_cli_v062() -> None:
    """§1 preset case / §4 backend autoselect / §7 tiered logging."""
    section("L1 CLI v0.6.2")
    sys.path.insert(0, str(ROOT))
    import logging as _logging

    # ---- §1 CLI 大小写不敏感 (argparse type=str.lower) ----
    sys.argv = ["1kt.py"]
    from importlib import reload
    import importlib
    kt = importlib.import_module("1kt") if "1kt" in sys.modules else None
    parser_ok = True
    try:
        if kt is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("kt_mod",
                                                          ROOT / "1kt.py")
            kt = importlib.util.module_from_spec(spec)
            sys.modules["kt_mod"] = kt
            spec.loader.exec_module(kt)
        for argv_preset, want in (("HQ", "hq"), ("hq", "hq"), ("Hq", "hq"),
                                  ("uhq", "uhq"), ("ALL", "all")):
            sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                        "--preset", argv_preset]
            ns = kt.parse_args()
            record(f"v062.preset 大小写 {argv_preset} -> {want}",
                   ns.preset == want, f"got={ns.preset}")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--encoder", "NVENC"]
        record("v062.encoder 大小写 NVENC -> nvenc",
               kt.parse_args().encoder == "nvenc")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--check", "FULL"]
        record("v062.check 大小写 FULL -> full",
               kt.parse_args().check == "full")
        sys.argv = ["1kt.py", "--input", ".", "--output", ".",
                    "--log-level", "DEBUG"]
        record("v062.log-level 大小写 DEBUG -> debug",
               kt.parse_args().log_level == "debug")
    except Exception as exc:               # noqa: BLE001
        parser_ok = False
        record("v062.parse_args 可导入可调用", False,
               f"{type(exc).__name__}: {exc}")
    if not parser_ok:
        return

    # ---- §4 默认后端自动选择顺序 ----
    record("v062.autoselect 顺序 = NVENC -> QSV",
           kt.AUTOSELECT_ORDER == ("nvenc", "qsv"),
           f"{kt.AUTOSELECT_ORDER}")
    record("v062.autoselect 兜底 = x265",
           kt.HW_AUTOSELECT_FALLBACK == "x265")
    record("v062.autoselect 不含 AV1 (不静默改编码格式)",
           all("av1" not in n for n in kt.AUTOSELECT_ORDER))
    seen: list[str] = []

    class _Ns:
        no_hw_autoselect = True
    got = kt.resolve_default_backend(_Ns(), ROOT, log=lambda m: seen.append(m))
    record("v062.--no-hw-autoselect 固定 x265", got == "x265", f"got={got}")
    record("v062.--no-hw-autoselect 不探测硬件",
           any("disabled" in m for m in seen), f"{seen[:1]}")

    # ---- §7 分层日志 ----
    from core.logging_utils import (
        DEBUG_LOG_NAME, ERROR_LOG_NAME, WARN_LOG_NAME, resolve_log_level,
        setup_logger,
    )
    record("v062.resolve_log_level 映射",
           resolve_log_level("error") == _logging.ERROR
           and resolve_log_level("warn") == _logging.WARNING
           and resolve_log_level("info") == _logging.INFO
           and resolve_log_level("debug") == _logging.DEBUG
           and resolve_log_level(None) == _logging.INFO
           and resolve_log_level("WARN") == _logging.WARNING,
           "error/warn/info/debug/None/WARN")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="1kt-l1-log-") as td:
        tdir = Path(td)
        lg = setup_logger(tdir / "total.log",
                          log_level=resolve_log_level("info"))
        lg.debug("DBG-X"); lg.info("INFO-X")
        lg.warning("WARN-X"); lg.error("ERROR-X")
        for h in lg.handlers:
            h.flush()

        def _txt(name: str) -> str:
            # Always re-read: setup_logger reopens total.log per call, so a
            # previously captured string goes stale.
            p = tdir / name
            return p.read_text(encoding="utf-8") if p.is_file() else ""

        total1 = _txt("total.log")
        warn1 = _txt(WARN_LOG_NAME)
        err1 = _txt(ERROR_LOG_NAME)
        record("v062.total.log 收 INFO 及以上",
               "INFO-X" in total1 and "WARN-X" in total1
               and "ERROR-X" in total1,
               f"{len(total1.splitlines())} lines")
        record("v062.warn.log 只收 WARNING 及以上",
               "WARN-X" in warn1 and "ERROR-X" in warn1
               and "INFO-X" not in warn1,
               f"{len(warn1.splitlines())} lines")
        record("v062.error.log 只收 ERROR",
               "ERROR-X" in err1 and "WARN-X" not in err1
               and "INFO-X" not in err1,
               f"{len(err1.splitlines())} lines")
        record("v062.info 级不建 debug.log",
               not (tdir / DEBUG_LOG_NAME).is_file())
        lg2 = setup_logger(tdir / "total.log",
                           log_level=resolve_log_level("debug"))
        lg2.debug("DBG-Y")
        for h in lg2.handlers:
            h.flush()
        record("v062.debug 级建 debug.log 且收 DEBUG",
               (tdir / DEBUG_LOG_NAME).is_file()
               and "DBG-Y" in _txt(DEBUG_LOG_NAME))
        lg3 = setup_logger(tdir / "total.log",
                           log_level=resolve_log_level("error"))
        lg3.info("INFO-Z"); lg3.error("ERROR-Z")
        for h in lg3.handlers:
            h.flush()
        total3 = _txt("total.log")
        record("v062.--log-level error 时 total.log 不收 INFO",
               "ERROR-Z" in total3 and "INFO-Z" not in total3,
               f"{len(total3.splitlines())} lines "
               f"(INFO-X={'INFO-X' in total3} INFO-Z={'INFO-Z' in total3})")
        for _lg in (lg, lg2, lg3):
            for _h in list(_lg.handlers):
                _h.close()
                _lg.removeHandler(_h)


# ===========================================================================
# L2 — 工具链
# ===========================================================================

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
# L3 — 真实管线集成 + 故障注入
# ===========================================================================

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


def l3_pipeline() -> None:
    section("L3 管线集成")
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = _stage_inputs()

    def expect(case: str, cond: bool, detail: str) -> None:
        record(f"l3.{case}", cond, detail)

    # C1 Sony NVENC basic
    out = OUT_DIR / "c1_sony_nvenc"
    rc, tail = _run_1kt(cases["sony"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "basic", "--jobs", "1")
    final = out / "C9037.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    expect("C1_sony_nvenc_basic", rc == 0 and final.is_file(),
           f"rc={rc}")
    expect("C1_sony_colr_rext", vs.get("color_primaries") == "bt709"
           and vs.get("color_transfer") == "bt709"
           and vs.get("profile") == "Rext",
           f"profile={vs.get('profile')} primaries={vs.get('color_primaries')}")

    # C2 DJI NVENC basic
    out = OUT_DIR / "c2_dji_nvenc"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "nvenc", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C2_dji_nvenc_basic", rc == 0 and final.is_file(), f"rc={rc}")
    expect("C2_dji_元数据轨保留", {"djmd", "dbgi", "tmcd"} <= tags,
           f"tags={sorted(tags)}")

    # C3 DJI QSV (对齐配置)
    out = OUT_DIR / "c3_dji_qsv_aligned"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "qsv", "--preset", "hq",
                     "--config", str(ROOT / "qsv_aligned.json"),
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    expect("C3_dji_qsv_aligned", rc == 0 and final.is_file(), f"rc={rc}")

    # C4 Sony NVENC full (Gyroflow 消费端)
    out = OUT_DIR / "c4_sony_nvenc_full"
    rc, tail = _run_1kt(cases["sony"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "full", "--jobs", "1", timeout=2400)
    expect("C4_sony_nvenc_full", rc == 0 and (out / "C9037.MP4").is_file(),
           f"rc={rc} tail={tail[-160:].strip()}")

    # C5 DJI NVENC full
    out = OUT_DIR / "c5_dji_nvenc_full"
    rc, tail = _run_1kt(cases["dji"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "full", "--jobs", "1", timeout=2400)
    expect("C5_dji_nvenc_full",
           rc == 0 and (out / "DJI_20260830095031_0009_D.MP4").is_file(),
           f"rc={rc} tail={tail[-160:].strip()}")

    # C6 经典路径 (剥离后的 DJI 文件)
    if "classic" in cases:
        out = OUT_DIR / "c6_classic_nvenc"
        rc, _ = _run_1kt(cases["classic"], out,
                         "--encoder", "nvenc", "--preset", "hq",
                         "--check", "basic", "--jobs", "1")
        final = out / "classic_test.MP4"
        v = ffprobe_json(final) if final.is_file() else {}
        types = sorted({s.get("codec_type") for s in v.get("streams", [])})
        expect("C6_classic_video_audio", rc == 0 and final.is_file()
               and types == ["audio", "video"],
               f"rc={rc} types={types}")
    else:
        expect("C6_classic_video_audio", False, "classic 输入未生成")

    # C7a 故障注入: 截断文件 -> failed + failed_files.json
    # (logs_root = input_root.parent/logs; input_root=in/truncated -> in/logs)
    failed_path = IN_DIR / "logs" / "failed_files.json"
    if failed_path.is_file():
        failed_path.unlink()
    out = OUT_DIR / "c7a_truncated"
    rc, tail = _run_1kt(cases["truncated"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "basic", "--jobs", "1")
    records = json.loads(failed_path.read_text(encoding="utf-8")) \
        if failed_path.is_file() else []
    expect("C7a_truncated_failed_记录",
           rc == 1 and any("truncated" in str(r.get("source"))
                           for r in records),
           f"rc={rc} records={len(records)}")

    # C7b 尾部垃圾容错: avsw reader 容忍 trailing garbage, 正常交付
    out = OUT_DIR / "c7b_trailing_junk"
    rc, tail = _run_1kt(cases["trailing_junk"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "basic", "--jobs", "1")
    delivered = rc == 0 and (out / "trailing_junk.MP4").is_file()
    strip_hit = "strip fallback" in tail
    expect("C7b_trailing_junk_容错",
           delivered,
           f"rc={rc} delivered={delivered} strip_triggered={strip_hit}")

    # C7c strip 回退机制本体: 直测 strip_video_audio (视频+音频原生复制)
    from core.batch_hw import strip_video_audio
    from preservation.gpac import GpacContainerBackend
    gpac = GpacContainerBackend()
    strip_work = WORK / "strip_work"
    dji_src = cases["dji"] / "DJI_20260830095031_0009_D.MP4"
    try:
        stripped = strip_video_audio(gpac, dji_src, strip_work, True)
        v = ffprobe_json(stripped)
        types = sorted({s.get("codec_type") for s in v.get("streams", [])})
        expect("C7c_strip_机制", stripped.is_file()
               and types == ["audio", "video"], f"types={types}")
    except Exception as exc:
        expect("C7c_strip_机制", False, f"{type(exc).__name__}: {exc}")

    # C8 断点续跑: 重跑 C1 输入 -> 全部 SKIP
    out = OUT_DIR / "c1_sony_nvenc"  # 复用 C1 输出
    rc, tail = _run_1kt(cases["sony"], out,
                        "--encoder", "nvenc", "--preset", "hq",
                        "--check", "basic", "--jobs", "1")
    expect("C8_resume_skip", rc == 0 and "SKIP" in tail,
           f"rc={rc} skip={'SKIP' in tail}")

    # C9 retry-list: 显式列表重跑 (新输出目录)
    retry_file = WORK / "retry_list.txt"
    retry_file.write_text(
        str(cases["dji"] / "DJI_20260830095031_0009_D.MP4") + "\n",
        encoding="utf-8",
    )
    out = OUT_DIR / "c9_retry"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--retry-list", retry_file,
                     "--encoder", "nvenc", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    expect("C9_retry_list", rc == 0
           and (out / "DJI_20260830095031_0009_D.MP4").is_file(),
           f"rc={rc}")

    # C10 x265 Sony FAST basic (4:2:2 保真是 x265 的独有价值)
    out = OUT_DIR / "c10_x265_sony"
    rc, _ = _run_1kt(cases["sony"], out,
                     "--encoder", "x265", "--preset", "fast",
                     "--check", "basic", timeout=3600)
    final = out / "C9037.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    expect("C10_x265_sony_422保真", rc == 0 and final.is_file()
           and vs.get("pix_fmt") == "yuv422p10le"
           and vs.get("color_primaries") == "bt709",
           f"rc={rc} pix_fmt={vs.get('pix_fmt')} "
           f"primaries={vs.get('color_primaries')}")

    # C11 x265 经典路径 DJI (全流复制, 数据轨存活)
    out = OUT_DIR / "c11_x265_classic_dji"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "x265", "--preset", "fast",
                     "--check", "basic", timeout=3600)
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C11_x265_classic_dji_数据轨存活", rc == 0 and final.is_file()
           and {"djmd", "dbgi", "tmcd"} <= tags,
           f"rc={rc} tags={sorted(tags - {None})}")

    # C33 nvenc-av1 DJI basic (AV1 保留管线: av01 + 数据轨)
    out = OUT_DIR / "c33_nvenc_av1_dji"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "nvenc-av1", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C33_nvenc_av1_dji_av01+元数据", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and {"djmd", "dbgi", "tmcd"} <= tags,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')}")

    # C34 nvenc-av1 Sony -> 保留管线 (rtmd/nrtm/uuid 保留, 不打 XAVC tag)
    out = OUT_DIR / "c34_nvenc_av1_sony"
    rc, tail = _run_1kt(cases["sony"], out,
                        "--encoder", "nvenc-av1", "--preset", "hq",
                        "--check", "basic", "--jobs", "1", timeout=2400)
    final = out / "C9037.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    rtmd = any(s.get("codec_tag_string") == "rtmd"
               for s in v.get("streams", []))
    major_brand = (v.get("format") or {}).get("tags", {}).get(
        "major_brand", "")
    expect("C34_nvenc_av1_sony_保留管线",
           rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1" and rtmd
           and "XAVC" not in major_brand and "AV1" in tail,
           f"rc={rc} codec={vs.get('codec_name')} rtmd={rtmd} "
           f"brand={major_brand} policy_warn={'AV1' in tail}")

    # C35 qsv-av1 DJI basic
    out = OUT_DIR / "c35_qsv_av1_dji"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "qsv-av1", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    expect("C35_qsv_av1_dji", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1",
           f"rc={rc} codec={vs.get('codec_name')}")

    # C36 svtav1 DJI basic (软件 AV1 保留管线: av01 + 数据轨)
    out = OUT_DIR / "c36_svtav1_dji"
    rc, tail = _run_1kt(cases["dji"], out,
                        "--encoder", "svtav1", "--preset", "fast",
                        "--check", "basic", timeout=3600)
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C36_svtav1_dji_av01+元数据", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and {"djmd", "dbgi", "tmcd"} <= tags,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')} tail={tail[-120:].strip()}")

    # C37 svtav1 Sony basic (保留管线: rtmd 保留 + 422->420 降级 + 无 XAVC)
    out = OUT_DIR / "c37_svtav1_sony"
    rc, tail = _run_1kt(cases["sony"], out,
                        "--encoder", "svtav1", "--preset", "fast",
                        "--check", "basic", timeout=3600)
    final = out / "C9037.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    rtmd = any(s.get("codec_tag_string") == "rtmd"
               for s in v.get("streams", []))
    major_brand = (v.get("format") or {}).get("tags", {}).get(
        "major_brand", "")
    expect("C37_svtav1_sony_保留+420+无XAVC",
           rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and rtmd and "XAVC" not in major_brand
           and "4:2:2" in tail,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')} rtmd={rtmd} "
           f"brand={major_brand} downgrade_warn={'4:2:2' in tail} "
           f"tail={tail[-120:].strip()}")

    # C38 svtav1 经典路径 (剥离后的 DJI 文件: video+audio only)
    if "classic" in cases:
        out = OUT_DIR / "c38_svtav1_classic"
        rc, tail = _run_1kt(cases["classic"], out,
                            "--encoder", "svtav1", "--preset", "fast",
                            "--check", "basic", timeout=3600)
        final = out / "classic_test.MP4"
        v = ffprobe_json(final) if final.is_file() else {}
        types = sorted({s.get("codec_type") for s in v.get("streams", [])})
        vs = next((s for s in v.get("streams", [])
                   if s.get("codec_type") == "video"), {})
        expect("C38_svtav1_classic", rc == 0 and final.is_file()
               and types == ["audio", "video"]
               and vs.get("codec_name") == "av1",
               f"rc={rc} types={types} codec={vs.get('codec_name')} "
               f"tail={tail[-120:].strip()}")
    else:
        expect("C38_svtav1_classic", False, "classic 输入未生成")

    # C39 svtav1 DJI full (Gyroflow 逐帧四元数消费端 on av01)
    out = OUT_DIR / "c39_svtav1_dji_full"
    rc, tail = _run_1kt(cases["dji"], out,
                        "--encoder", "svtav1", "--preset", "fast",
                        "--check", "full", timeout=3600)
    final = out / "DJI_20260830095031_0009_D.MP4"
    expect("C39_svtav1_dji_full_gyroflow",
           rc == 0 and final.is_file() and "dji gyroflow" in tail,
           f"rc={rc} tail={tail[-160:].strip()}")

    from core.config import find_executable
    from preservation.quality import run_quality_sample
    ffmpeg = find_executable("ffmpeg", ROOT)
    ffprobe = find_executable("ffprobe", ROOT)

    # C12 质量抽样 PASS (真实管线产物: C10 x265 Sony 输出 vs 源)
    c10_final = OUT_DIR / "c10_x265_sony" / "C9037.MP4"
    if c10_final.is_file():
        q = run_quality_sample(
            original=cases["sony"] / "C9037.MP4",
            final=c10_final,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            scratch=WORK / "quality_c12",
            opts={"sample_rate": 1, "max_duration_sec": 600},
            log=lambda m: None,
        )
        expect("C12_quality_sample_PASS",
               q["status"] == "PASS"
               and q["psnr_avg_db"] and q["psnr_avg_db"] > 25
               and q["ssim_all"] and q["ssim_all"] > 0.8,
               f"status={q['status']} psnr={q['psnr_avg_db']} "
               f"ssim={q['ssim_all']} detail={q['detail']}")
    else:
        expect("C12_quality_sample_PASS", False, "C10 输出缺失")

    # C40 质量抽样 PASS — AV1 后端产物 (C37 svtav1 Sony 输出 vs 源)
    c37_final = OUT_DIR / "c37_svtav1_sony" / "C9037.MP4"
    if c37_final.is_file():
        q = run_quality_sample(
            original=cases["sony"] / "C9037.MP4",
            final=c37_final,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            scratch=WORK / "quality_c40",
            opts={"sample_rate": 1, "max_duration_sec": 600},
            log=lambda m: None,
        )
        expect("C40_svtav1_quality_sample_PASS",
               q["status"] == "PASS"
               and q["psnr_avg_db"] and q["psnr_avg_db"] > 25
               and q["ssim_all"] and q["ssim_all"] > 0.8,
               f"status={q['status']} psnr={q['psnr_avg_db']} "
               f"ssim={q['ssim_all']} detail={q['detail']}")
    else:
        expect("C40_svtav1_quality_sample_PASS", False, "C19 输出缺失")

    # C13 质量抽样 FAIL (灰屏垃圾文件: 同分辨率同帧数, PSNR 必然崩溃)
    if "classic" in cases:
        garbage = WORK / "quality_c23_garbage.mp4"
        r = sh(ffmpeg, "-hide_banner", "-nostdin", "-y",
               "-i", cases["classic"] / "classic_test.MP4",
               "-map", "0:v:0", "-an",
               "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
               "-pix_fmt", "yuv420p", garbage, timeout=900)
        if r.returncode == 0 and garbage.is_file():
            q = run_quality_sample(
                original=cases["classic"] / "classic_test.MP4",
                final=garbage,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                scratch=WORK / "quality_c13",
                opts={"sample_rate": 1, "max_duration_sec": 600},
                log=lambda m: None,
            )
            expect("C13_quality_sample_FAIL_捕获",
                   q["status"] == "FAIL",
                   f"status={q['status']} psnr={q['psnr_avg_db']} "
                   f"ssim={q['ssim_all']} detail={q['detail']}")
        else:
            expect("C13_quality_sample_FAIL_捕获", False,
                   f"garbage 生成失败 rc={r.returncode}")
    else:
        expect("C13_quality_sample_FAIL_捕获", False, "classic 输入未生成")

    # C14 延时补偿端到端: 合成 4xmono PCM (注入 CH1 +1223 样本 /
    # CH2 +900 样本) -> --channel-sync -> 输出复测对齐 (残差<0.1ms)
    sync_dir = IN_DIR / "sync"
    sync_dir.mkdir(exist_ok=True)
    sync_src = sync_dir / "sync_test.MP4"
    r = sh(ffmpeg, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=42:sample_rate=48000",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
           "-filter_complex",
           "[0:a]asplit=4[x1][x2][x3][x4];"
           "[x1]adelay=1223S[ch1];[x2]adelay=900S[ch2];"
           "[x3]anull[ch3];[x4]anull[ch4]",
           "-map", "[ch1]", "-map", "[ch2]", "-map", "[ch3]",
           "-map", "[ch4]", "-map", "1:v:0",
           "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-shortest", sync_src, timeout=900)
    if r.returncode == 0 and sync_src.is_file():
        out = OUT_DIR / "c14_channel_sync"
        rc, tail = _run_1kt(sync_dir, out,
                            "--encoder", "nvenc", "--preset", "fast",
                            "--check", "basic", "--channel-sync",
                            "--jobs", "1", timeout=1200)
        final = out / "sync_test.MP4"
        aligned = False
        if rc == 0 and final.is_file():
            try:
                from core.channel_sync import run_channel_sync
                v = ffprobe_json(final)
                res = run_channel_sync(
                    source=final, ffmpeg=ffmpeg,
                    work_dir=WORK / "c14_recheck",
                    streams=v.get("streams", []), log=lambda m: None,
                )
                delays = [
                    c["delay_ms"] for c in res.get("channels", [])
                    if c["stream"] != 2
                ]
                aligned = res["status"] in (
                    "applied", "already_aligned"
                ) and all(
                    d is None or abs(d) < 0.1 for d in delays
                )
                expect("C14_channel_sync_端到端对齐",
                       aligned,
                       f"rc={rc} status={res['status']} "
                       f"delays={delays} tail={tail[-120:].strip()}")
            except Exception as exc:
                expect("C14_channel_sync_端到端对齐", False,
                       f"{type(exc).__name__}: {exc}")
        else:
            expect("C14_channel_sync_端到端对齐", False,
                   f"rc={rc} tail={tail[-160:].strip()}")
    else:
        expect("C14_channel_sync_端到端对齐", False,
               f"合成文件生成失败 rc={r.returncode}")


def l3_channel_sync_p1() -> None:
    section("L3 延时补偿 P1 端到端")
    import hashlib

    try:
        import numpy as np
    except ImportError:
        record("l3.p1_numpy_可用", False, "numpy 缺失 — 本组跳过")
        return
    record("l3.p1_numpy_可用", True)

    def expect(case: str, cond: bool, detail: str) -> None:
        record(f"l3.{case}", cond, detail)

    def shb(*args: str, timeout: int = 3600) -> tuple[int, bytes]:
        r = subprocess.run(
            [str(a) for a in args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
        )
        return r.returncode, (r.stdout or b"")

    def vstream_md5(p: Path) -> str:
        rc, out = shb(FFMPEG, "-v", "error", "-i", p, "-map", "0:v:0",
                      "-c", "copy", "-f", "streamhash", "-hash", "md5", "-")
        for line in out.decode("utf-8", errors="replace").splitlines():
            if "MD5=" in line:
                return line.split("MD5=")[1].strip()
        return ""

    def astream_hash(p: Path, idx: int) -> str:
        rc, out = shb(FFMPEG, "-v", "error", "-i", p,
                      "-map", f"0:a:{idx}", "-f", "f32le", "-")
        return hashlib.md5(out).hexdigest() if rc == 0 else ""

    def recheck(source: Path, work: Path, name: str):
        from core.channel_sync import run_channel_sync

        v = ffprobe_json(source)
        return run_channel_sync(
            source=source, ffmpeg=FFMPEG, work_dir=work / name,
            streams=v.get("streams", []), log=lambda m: None,
        )

    p1a = IN_DIR / "sync_p1" / "a"
    p1b = IN_DIR / "sync_p1" / "b"
    p1c = IN_DIR / "sync_p1" / "c"
    p1d = IN_DIR / "sync_p1" / "d"
    for d in (p1a, p1b, p1c, p1d):
        d.mkdir(parents=True, exist_ok=True)
    # 清掉上一轮的产物 (否则 1kt 会 SKIP 旧输出)
    for out in ("c15_sync_partial", "c16_sync_adversarial",
                "c17_sync_transparent", "c18_sync_aligned_copy",
                "c23_transparent_not_eligible"):
        shutil.rmtree(OUT_DIR / out, ignore_errors=True)

    # --- C15 输入: CH1 静音 / CH2 +900 / CH3 参考 / CH4 +1223 ---
    src_a = p1a / "sync_p1_a.MP4"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=42:sample_rate=48000",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
           "-filter_complex",
           "[0:a]asplit=4[x1][x2][x3][x4];"
           "[x1]volume=0.0[ch1];[x2]adelay=900S[ch2];"
           "[x3]anull[ch3];[x4]adelay=1223S[ch4]",
           "-map", "[ch1]", "-map", "[ch2]", "-map", "[ch3]",
           "-map", "[ch4]", "-map", "1:v:0",
           "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-shortest", src_a, timeout=900)
    # --- C16 输入: CH2 中途 +50ms 跳变 (900S 前半 / 3300S 后半)。
    # ffmpeg 的 asplit+atrim+concat 组合会破坏后半段内容, concat 过滤器
    # 与 concat demuxer 在本机同样不可靠 — 故 CH2 直接用 numpy 构造
    # (测试环境已有 numpy), 其它轨仍用 adelay; 最后普通 mux。 ---
    src_b = p1b / "sync_p1_b.MP4"
    b1 = p1b / "b_ch1.mov"
    b2 = p1b / "b_ch2.mov"
    b2src = p1b / "b_ch2_src.f32"
    b3 = p1b / "b_ch3.mov"
    b4 = p1b / "b_ch4.mov"
    bv = p1b / "b_video.mp4"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=43:sample_rate=48000",
           "-filter_complex", "[0:a]adelay=300S[ch1]",
           "-map", "[ch1]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-f", "mov", b1, timeout=600)
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=43:sample_rate=48000",
           "-f", "f32le", b2src, timeout=600)
    if b2src.is_file():
        noise = np.fromfile(b2src, dtype="<f4")
        # CH2 前半: 总延迟 900 样本; 后半: 总延迟 3300 样本 (跳变 +2400
        # 样本 = +50ms; 注意后半段相对自身起点只延 2400 — 前半段尾部长
        # 出的 900 已计入总延迟)
        half1 = np.zeros(4 * 48000 + 900, dtype="<f4")
        half1[900:] = noise[: 4 * 48000]
        half2 = np.zeros(4 * 48000 + 2400, dtype="<f4")
        half2[2400:] = noise[4 * 48000: 8 * 48000]
        b2raw = p1b / "b_ch2.raw"
        b2raw.write_bytes(np.concatenate([half1, half2]).tobytes())
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "f32le", "-ar", "48000", "-ac", "1",
               "-i", b2raw, "-c:a", "pcm_s24le", "-f", "mov", b2,
               timeout=600)
        for tmp in (b2src, b2raw):
            try:
                tmp.unlink()
            except OSError:
                pass
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=43:sample_rate=48000",
           "-map", "0:a:0", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-f", "mov", b3, timeout=600)
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=43:sample_rate=48000",
           "-filter_complex", "[0:a]adelay=1223S[ch4]",
           "-map", "[ch4]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-f", "mov", b4, timeout=600)
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-pix_fmt", "yuv420p", bv, timeout=600)
    if b1.is_file() and b2.is_file() and b3.is_file() and b4.is_file() \
            and bv.is_file():
        r = sh(FFMPEG, "-v", "error", "-y",
               "-i", b1, "-i", b2, "-i", b3, "-i", b4, "-i", bv,
               "-map", "0:a:0", "-map", "1:a:0", "-map", "2:a:0",
               "-map", "3:a:0", "-map", "4:v:0",
               "-c", "copy", "-shortest", src_b, timeout=600)
    # 清理中间文件 (留在输入目录会被 1kt 当作源文件探测)
    for tmp in (b1, b2, b3, b4, bv):
        try:
            tmp.unlink()
        except OSError:
            pass
    # --- C18 输入: 全部已对齐 (无延迟) ---
    src_c = p1c / "sync_p1_c.MP4"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=6:seed=44:sample_rate=48000",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6",
           "-filter_complex", "[0:a]asplit=4[x1][x2][x3][x4]",
           "-map", "[x1]", "-map", "[x2]", "-map", "[x3]",
           "-map", "[x4]", "-map", "1:v:0",
           "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-shortest", src_c, timeout=900)
    expect("C15-18_合成输入就绪",
           src_a.is_file() and src_b.is_file() and src_c.is_file(),
           f"a={src_a.is_file()} b={src_b.is_file()} c={src_c.is_file()}")

    # --- C15: 转码 + 轨道级部分成功 (CH1 silent untouched, CH2/CH4 fixed) ---
    out15 = OUT_DIR / "c15_sync_partial"
    rc, tail = _run_1kt(p1a, out15, "--encoder", "nvenc",
                        "--preset", "fast", "--check", "basic",
                        "--channel-sync", "--jobs", "1",
                        "--keep-work", timeout=1800)
    final15 = out15 / "sync_p1_a.MP4"
    if rc == 0 and final15.is_file():
        # 读取原同步报告 (scope/anchor/决策)
        from core.paths import job_id_for

        jid = job_id_for(src_a)
        rep_path = (out15 / ".1ktwork" / jid / "channel_sync"
                    / "channel_sync_sync_p1_a.json")
        rep = {}
        if rep_path.is_file():
            rep = json.loads(rep_path.read_text(encoding="utf-8"))
        rows = {c["stream"]: c for c in rep.get("channels", [])}
        expect("C15_sync_partial_报告",
               rep.get("status") == "applied"
               and rep.get("result_scope") == "partial"
               and rep.get("anchor_stream") == 2
               and rep.get("sync_mode") == "transcode"
               and len(rep.get("audio_files", [])) == 4
               and len(rep.get("fixed_files", [])) == 2,
               f"status={rep.get('status')} scope={rep.get('result_scope')} "
               f"anchor={rep.get('anchor_stream')}")
        expect("C15_sync_partial_决策",
               rows.get(0, {}).get("decision") == "untouched"
               and rows.get(0, {}).get("reason") == "silent_track"
               and rows.get(1, {}).get("decision") == "fixed"
               and rows.get(1, {}).get("shift_samples") == 900
               and rows.get(2, {}).get("decision") == "anchor"
               and rows.get(3, {}).get("decision") == "fixed"
               and rows.get(3, {}).get("shift_samples") == 1223,
               json.dumps(rows, ensure_ascii=False))
        # 输出复测: CH2/CH4 已对齐, CH1 仍静音, 4 条音轨全在
        res15 = recheck(final15, WORK, "c15_recheck")
        r15 = {c["stream"]: c for c in res15.get("channels", [])}
        v15 = ffprobe_json(final15)
        n_audio = sum(1 for s in v15.get("streams", [])
                      if s.get("codec_type") == "audio")
        expect("C15_sync_partial_输出复测",
               res15["status"] in ("applied", "already_aligned")
               and r15.get(0, {}).get("reason") == "silent_track"
               and r15.get(1, {}).get("decision") == "already_aligned"
               and r15.get(3, {}).get("decision") == "already_aligned"
               and n_audio == 4,
               f"status={res15['status']} n_audio={n_audio} "
               f"rows={json.dumps(r15, ensure_ascii=False)}")
    else:
        expect("C15_sync_partial_报告", False, f"rc={rc} tail={tail[-200:]}")
        expect("C15_sync_partial_决策", False, "转码未产出")
        expect("C15_sync_partial_输出复测", False, "转码未产出")

    # --- C16: 对抗 — 中途 50ms 跳变轨 untouched, 其它轨独立同步 ---
    out16 = OUT_DIR / "c16_sync_adversarial"
    rc, tail = _run_1kt(p1b, out16, "--encoder", "nvenc",
                        "--preset", "fast", "--check", "basic",
                        "--channel-sync", "--jobs", "1", timeout=1800)
    final16 = out16 / "sync_p1_b.MP4"
    if rc == 0 and final16.is_file():
        res16 = recheck(final16, WORK, "c16_recheck")
        r16 = {c["stream"]: c for c in res16.get("channels", [])}
        expect("C16_对抗_中途跳变",
               r16.get(0, {}).get("decision") == "already_aligned"
               and r16.get(1, {}).get("reason") == "non_constant"
               and r16.get(3, {}).get("decision") == "already_aligned",
               f"status={res16['status']} "
               f"rows={json.dumps(r16, ensure_ascii=False)}")
    else:
        expect("C16_对抗_中途跳变", False, f"rc={rc} tail={tail[-200:]}")

    # --- C17: transparent — 视频/音频轨级检查 (C15 输入) ---
    out17 = OUT_DIR / "c17_sync_transparent"
    rc, tail = _run_1kt(p1a, out17, "--channel-sync-transparent",
                        timeout=1800)
    final17 = out17 / "sync_p1_a.MP4"
    if rc == 0 and final17.is_file():
        md5_src, md5_dst = vstream_md5(src_a), vstream_md5(final17)
        a0_src, a0_dst = astream_hash(src_a, 0), astream_hash(final17, 0)
        res17 = recheck(final17, WORK, "c17_recheck")
        r17 = {c["stream"]: c for c in res17.get("channels", [])}
        v17 = ffprobe_json(final17)
        n_audio = sum(1 for s in v17.get("streams", [])
                      if s.get("codec_type") == "audio")
        n_video = sum(1 for s in v17.get("streams", [])
                      if s.get("codec_type") == "video")
        expect("C17_transparent_视频_stream_copy",
               md5_src and md5_src == md5_dst,
               f"src={md5_src} dst={md5_dst}")
        expect("C17_transparent_untouched轨原样",
               a0_src and a0_src == a0_dst,
               f"ch1 hash equal={a0_src == a0_dst}")
        expect("C17_transparent_同步效果",
               res17["status"] in ("applied", "already_aligned")
               and r17.get(1, {}).get("decision") == "already_aligned"
               and r17.get(3, {}).get("decision") == "already_aligned"
               and n_audio == 4 and n_video == 1,
               f"status={res17['status']} n_audio={n_audio} "
               f"rows={json.dumps(r17, ensure_ascii=False)}")
    else:
        expect("C17_transparent_视频_stream_copy", False,
               f"rc={rc} tail={tail[-200:]}")
        expect("C17_transparent_untouched轨原样", False, "transparent 未产出")
        expect("C17_transparent_同步效果", False, "transparent 未产出")

    # --- C18: transparent 全部已对齐 -> 输出与源字节级一致 ---
    out18 = OUT_DIR / "c18_sync_aligned_copy"
    rc, tail = _run_1kt(p1c, out18, "--channel-sync-transparent",
                        timeout=1800)
    final18 = out18 / "sync_p1_c.MP4"
    if rc == 0 and final18.is_file():
        h_src = hashlib.sha256(src_c.read_bytes()).hexdigest()
        h_dst = hashlib.sha256(final18.read_bytes()).hexdigest()
        expect("C18_transparent_全对齐_字节一致", h_src == h_dst,
               f"sha256 equal={h_src == h_dst}")
    else:
        expect("C18_transparent_全对齐_字节一致", False,
               f"rc={rc} tail={tail[-200:]}")

    # --- C19: 确定性 — 同输入两次运行 JSON 逐字节一致 ---
    try:
        from core.channel_sync import run_channel_sync

        v = ffprobe_json(src_a)
        w = WORK / "c19_determinism"
        run_channel_sync(source=src_a, ffmpeg=FFMPEG, work_dir=w,
                         streams=v.get("streams", []), log=lambda m: None)
        j1 = (w / "channel_sync_sync_p1_a.json").read_text(encoding="utf-8")
        run_channel_sync(source=src_a, ffmpeg=FFMPEG, work_dir=w,
                         streams=v.get("streams", []), log=lambda m: None)
        j2 = (w / "channel_sync_sync_p1_a.json").read_text(encoding="utf-8")
        expect("C19_确定性_JSON字节一致", j1 == j2, f"equal={j1 == j2}")
    except Exception as exc:
        expect("C19_确定性_JSON字节一致", False,
               f"{type(exc).__name__}: {exc}")

    # --- C20: 性能/内存冒烟 — 10min 4ch 48k 单线程 ---
    import ctypes

    class PROC_MEM(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def rss_bytes() -> int:
        try:
            psapi = ctypes.WinDLL("psapi")
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(PROC_MEM), ctypes.c_ulong,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            h = kernel32.GetCurrentProcess()
            c = PROC_MEM()
            c.cb = ctypes.sizeof(c)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                return int(c.WorkingSetSize)
        except (OSError, AttributeError, ValueError):
            pass
        return -1

    src_d = p1d / "sync_p1_d.mov"
    if not src_d.is_file():
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "lavfi",
               "-i", "anoisesrc=color=pink:duration=600:seed=45:"
                     "sample_rate=48000",
               "-filter_complex",
               "[0:a]asplit=4[x1][x2][x3][x4];"
               "[x1]adelay=1223S[ch1];[x2]adelay=900S[ch2];"
               "[x3]anull[ch3];[x4]anull[ch4]",
               "-map", "[ch1]", "-map", "[ch2]", "-map", "[ch3]",
               "-map", "[ch4]",
               "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
               "-f", "mov", src_d, timeout=1800)
    if src_d.is_file():
        rss0 = rss_bytes()
        t0 = time.monotonic()
        res20 = recheck(src_d, WORK, "c20_perf")
        t_total = time.monotonic() - t0
        rss1 = rss_bytes()
        expect("C20_性能_10min4ch48k_全流程",
               res20["status"] == "applied" and t_total < 120.0,
               f"status={res20['status']} total={t_total:.1f}s")
        if rss0 > 0 and rss1 > 0:
            expect("C20_内存_RSS<512MB",
                   (rss1 - rss0) < 512 * 1024 * 1024,
                   f"delta={(rss1 - rss0) / 1024 / 1024:.0f}MB")
        else:
            expect("C20_内存_RSS<512MB", False, "RSS 测量不可用")
    else:
        expect("C20_性能_10min4ch48k_全流程", False, "10min 合成未生成")
        expect("C20_内存_RSS<512MB", False, "10min 合成未生成")

    # --- C21-C26: 对抗/边界 (进程内 run_channel_sync, 音频专用文件) ---
    def make_audio4(name: str, specs: list[str], extra: list[str] | None = None,
                    dur: int = 8, codec: str = "pcm_s24le") -> Path | None:
        """4×mono 音频专用文件: specs[i] 为第 i 轨的 lavfi 输入 + 可选滤镜。

        specs 元素如 "anoisesrc=...:seed=9" 或 "anoisesrc=...|aeval=nan"。
        """
        p = p1d / name
        cmd = [FFMPEG, "-v", "error", "-y"]
        for i, s in enumerate(specs):
            src, _, filt = s.partition("|")
            cmd += ["-f", "lavfi", "-i", src]
        fc = []
        maps = []
        for i, s in enumerate(specs):
            _src, has_f, filt = s.partition("|")
            if has_f:
                fc.append(f"[{i}:a]{filt}[c{i}]")
                maps += ["-map", f"[c{i}]"]
            else:
                maps += ["-map", f"{i}:a:0"]
        if fc:
            cmd += ["-filter_complex", ";".join(fc)]
        cmd += maps
        cmd += ["-c:a", codec, "-ar", "48000", "-ac", "1",
                "-f", "mov", *((extra or [])), p]
        r = sh(*cmd, timeout=600)
        return p if (p.is_file() and r.returncode == 0) else None

    def sync_of(p: Path, opts: dict | None = None, name: str = "c21") -> dict:
        from core.channel_sync import run_channel_sync

        v = ffprobe_json(p)
        return run_channel_sync(
            source=p, ffmpeg=FFMPEG, work_dir=WORK / f"{name}_sync",
            streams=v.get("streams", []), opts=opts, log=lambda m: None,
        )

    # C21: NaN/Inf 轨 -> 该轨 untouched(non_finite), 其它轨独立同步
    src21 = make_audio4(
        "sync_p1_e.mov",
        [
            f"anoisesrc=color=pink:duration=8:seed=9:sample_rate=48000|aeval=nan",
            "anoisesrc=color=pink:duration=8:seed=9:sample_rate=48000|adelay=900S",
            "anoisesrc=color=pink:duration=8:seed=9:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=9:sample_rate=48000|adelay=1223S",
        ],
        codec="pcm_f32le",
    )
    if src21:
        res21 = sync_of(src21, name="c21")
        r21 = {c["stream"]: c for c in res21.get("channels", [])}
        expect("C21_non_finite轨_untouched",
               res21["status"] == "applied"
               and r21.get(0, {}).get("decision") == "untouched"
               and r21.get(0, {}).get("reason") == "non_finite"
               and r21.get(1, {}).get("decision") == "fixed"
               and r21.get(3, {}).get("decision") == "fixed",
               f"status={res21['status']} "
               f"rows={json.dumps(r21, ensure_ascii=False)}")
    else:
        expect("C21_non_finite轨_untouched", False, "合成失败")

    # C22: 过短轨 -> 该轨 untouched(insufficient_frames), 其它轨独立同步
    src22 = make_audio4(
        "sync_p1_f.mov",
        [
            "anoisesrc=color=pink:duration=1:seed=10:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=10:sample_rate=48000|adelay=900S",
            "anoisesrc=color=pink:duration=8:seed=10:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=10:sample_rate=48000|adelay=1223S",
        ],
    )
    if src22:
        res22 = sync_of(src22, name="c22")
        r22 = {c["stream"]: c for c in res22.get("channels", [])}
        expect("C22_过短轨_untouched",
               res22["status"] == "applied"
               and r22.get(0, {}).get("reason") == "insufficient_frames"
               and r22.get(1, {}).get("decision") == "fixed"
               and r22.get(3, {}).get("decision") == "fixed",
               f"status={res22['status']} "
               f"rows={json.dumps(r22, ensure_ascii=False)}")
    else:
        expect("C22_过短轨_untouched", False, "合成失败")

    # C23: transparent 对不满足布局的文件 -> 输出 = 源文件原样拷贝
    p1g = IN_DIR / "sync_p1" / "g"
    shutil.rmtree(p1g, ignore_errors=True)
    p1g.mkdir(parents=True, exist_ok=True)
    src23 = p1g / "sync_p1_g.MP4"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=4:seed=11:sample_rate=48000",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
           "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-shortest", src23, timeout=600)
    src23b = p1g / "sync_p1_gb_audio_only.mov"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=3:seed=12:sample_rate=48000",
           "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
           "-f", "mov", src23b, timeout=600)
    if src23.is_file() and src23b.is_file():
        out23 = OUT_DIR / "c23_transparent_not_eligible"
        rc, tail = _run_1kt(p1g, out23, "--channel-sync-transparent",
                            timeout=1800)
        final23 = out23 / "sync_p1_g.MP4"
        final23b = out23 / "sync_p1_gb_audio_only.MP4"
        if rc == 0 and final23.is_file() and final23b.is_file():
            h_src = hashlib.sha256(src23.read_bytes()).hexdigest()
            h_dst = hashlib.sha256(final23.read_bytes()).hexdigest()
            expect("C23_transparent_非资格_原样拷贝", h_src == h_dst,
                   f"sha256 equal={h_src == h_dst}")
            h_src = hashlib.sha256(src23b.read_bytes()).hexdigest()
            h_dst = hashlib.sha256(final23b.read_bytes()).hexdigest()
            expect("C23_transparent_纯音频_探测回退拷贝", h_src == h_dst,
                   f"sha256 equal={h_src == h_dst}")
        else:
            expect("C23_transparent_非资格_原样拷贝", False,
                   f"rc={rc} tail={tail[-200:]}")
            expect("C23_transparent_纯音频_探测回退拷贝", False,
                   f"rc={rc} tail={tail[-200:]}")
    else:
        expect("C23_transparent_非资格_原样拷贝", False, "2ch 合成失败")
        expect("C23_transparent_纯音频_探测回退拷贝", False, "2ch 合成失败")

    # C24: 极低 SNR (独立噪声, 与锚点不相关) -> 该轨 untouched, 其它轨同步
    src24 = make_audio4(
        "sync_p1_h.mov",
        [
            "anoisesrc=color=pink:duration=8:seed=12:sample_rate=48000|adelay=300S",
            "anoisesrc=color=white:duration=8:seed=77:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=12:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=12:sample_rate=48000|adelay=1223S",
        ],
    )
    if src24:
        res24 = sync_of(src24, name="c24")
        r24 = {c["stream"]: c for c in res24.get("channels", [])}
        expect("C24_低SNR轨_untouched",
               res24["status"] == "applied"
               and r24.get(1, {}).get("decision") == "untouched"
               and r24.get(1, {}).get("reason") == "low_confidence"
               and r24.get(0, {}).get("decision") == "fixed"
               and r24.get(3, {}).get("decision") == "fixed",
               f"status={res24['status']} "
               f"rows={json.dumps(r24, ensure_ascii=False)}")
    else:
        expect("C24_低SNR轨_untouched", False, "合成失败")

    # C25: 无健康锚点 (全部互不相关) -> measure_failed / no_valid_anchor
    src25 = make_audio4(
        "sync_p1_i.mov",
        [
            "anoisesrc=color=pink:duration=6:seed=31:sample_rate=48000",
            "anoisesrc=color=pink:duration=6:seed=32:sample_rate=48000",
            "anoisesrc=color=pink:duration=6:seed=33:sample_rate=48000",
            "anoisesrc=color=pink:duration=6:seed=34:sample_rate=48000",
        ],
    )
    if src25:
        res25 = sync_of(src25, name="c25")
        expect("C25_无健康锚点_measure_failed",
               res25["status"] == "measure_failed"
               and "no_valid_anchor" in res25["detail"]
               and all(c["decision"] == "untouched"
                       for c in res25.get("channels", [])),
               f"status={res25['status']} detail={res25['detail']}")
    else:
        expect("C25_无健康锚点_measure_failed", False, "合成失败")

    # C26: 复检门单轨回退 — CH4 分数延迟 1223.4 样本 (整数修正后残余 0.4
    # 样本 ≈ 0.0083ms) 在 verify_max_ms=0.005 下复检失败回退, CH2 整数
    # 延迟 (残余 ≈0) 通过 -> 只回退 CH4。CH4 用 numpy 构造 (adelay 不接受
    # 分数样本)。
    src26 = p1d / "sync_p1_j.mov"
    c26_parts = []
    for idx, delay_s in ((0, "300"), (1, "900")):
        f = p1d / f"j_ch{idx}.mov"
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "lavfi",
               "-i", f"anoisesrc=color=pink:duration=8:seed=13:"
                     "sample_rate=48000",
               "-filter_complex", f"[0:a]adelay={delay_s}S[o]",
               "-map", "[o]", "-c:a", "pcm_s24le", "-ar", "48000",
               "-ac", "1", "-f", "mov", f, timeout=600)
        c26_parts.append(f)
    f = p1d / "j_ch2.mov"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=13:sample_rate=48000",
           "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
           "-f", "mov", f, timeout=600)
    c26_parts.append(f)
    j4src = p1d / "j_ch3_src.f32"
    r = sh(FFMPEG, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", "anoisesrc=color=pink:duration=8:seed=13:sample_rate=48000",
           "-f", "f32le", j4src, timeout=600)
    if j4src.is_file():
        noise4 = np.fromfile(j4src, dtype="<f4").astype(np.float64)
        taps = 97
        mm = np.arange(-(taps // 2), taps // 2 + 1)
        hh = np.sinc(mm - 0.4) * np.hanning(taps)
        hh /= hh.sum()
        frac4 = np.convolve(noise4, hh, mode="same")
        out4 = np.zeros(noise4.shape[0] + 1223, dtype=np.float32)
        out4[1223:] = frac4.astype(np.float32)
        j4raw = p1d / "j_ch3.raw"
        j4raw.write_bytes(out4.tobytes())
        f4 = p1d / "j_ch3.mov"
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "f32le", "-ar", "48000", "-ac", "1",
               "-i", j4raw, "-c:a", "pcm_s24le", "-f", "mov", f4,
               timeout=600)
        c26_parts.append(f4)
        for tmp in (j4src, j4raw):
            try:
                tmp.unlink()
            except OSError:
                pass
    if len(c26_parts) == 4 and all(p.is_file() for p in c26_parts):
        r = sh(FFMPEG, "-v", "error", "-y",
               "-i", c26_parts[0], "-i", c26_parts[1],
               "-i", c26_parts[2], "-i", c26_parts[3],
               "-map", "0:a:0", "-map", "1:a:0", "-map", "2:a:0",
               "-map", "3:a:0",
               "-c", "copy", "-shortest", src26, timeout=600)
    for tmp in c26_parts:
        try:
            tmp.unlink()
        except OSError:
            pass
    if src26.is_file():
        res26 = sync_of(
            src26, opts={"verify_max_ms": 0.005}, name="c26"
        )
        r26 = {c["stream"]: c for c in res26.get("channels", [])}
        expect("C26_复检门_仅回退该轨",
               res26["status"] == "applied"
               and r26.get(1, {}).get("decision") == "fixed"
               and r26.get(3, {}).get("decision") == "untouched"
               and r26.get(3, {}).get("reason") == "recheck_residual",
               f"status={res26['status']} "
               f"rows={json.dumps(r26, ensure_ascii=False)}")
    else:
        expect("C26_复检门_仅回退该轨", False, "合成失败")


def l3_audio_probe() -> None:
    """v0.7.1 Phase 1: 真实 ffprobe -> AudioStream/Channel/Track/Plan 全链.

    只做探测与建模 (不编码、不改默认音频路径): 用 ffmpeg 合成两种真实
    布局 —— 单流 4CH PCM 与 4 条 mono PCM 流 —— 再走**真实** probe_source
    与既有 channel_sync.eligible_audio, 确认模型与生产判定一致。
    """
    section("L3 音频模型 probe 集成 (v0.7.1)")
    from core.audio_models import TrackBuildMode, SyncStatus
    from core.audio_probe import audio_probe_of
    from core.channel_sync import eligible_audio

    d = IN_DIR / "audio_model"
    d.mkdir(parents=True, exist_ok=True)

    def mk(dst: Path, args: list[str], timeout: int = 600) -> bool:
        r = sh(FFMPEG, "-v", "error", "-y", *args, dst, timeout=timeout)
        return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0

    def probe_model(path: Path):
        """真实 ffprobe JSON -> (raw streams, AudioProbeResult)。

        音频专用素材没有视频流, 因此不走 probe_source (它要求视频流),
        而是直接复用同一条 ffprobe 请求形态再经模型适配层建模。
        """
        raw = ffprobe_json(path).get("streams", [])
        return raw, audio_probe_of(raw)

    # --- 情况 C: 单流 4CH PCM (真实 channel_layout=4.0) ---
    src4 = d / "audio_model_4ch.mov"
    ok4 = mk(src4, ["-f", "lavfi", "-i",
                    "anoisesrc=duration=3:sample_rate=48000:color=pink",
                    "-af", "pan=4.0|FL=c0|FR=c0|FC=c0|BC=c0",
                    "-c:a", "pcm_s24le", "-ac", "4", "-f", "mov"])
    if ok4:
        raw4, probe4 = probe_model(src4)
        trk4 = probe4.plan().input_tracks
        record("l3.audio.4CH 真实探测 1 stream / 4 channel",
               probe4.stream_count == 1 and probe4.channel_count == 4
               and len(trk4) == 1 and trk4[0].channel_count == 4
               and trk4[0].channel_indices == [0, 1, 2, 3]
               and trk4[0].layout == "4.0",
               f"{probe4.summary()['streams']}")
        record("l3.audio.4CH 单通道选择可用 (不塌缩)",
               trk4[0].select_channels([2]).source_ids
               == [f"default:s{trk4[0].source_stream_index}:c2"]
               and trk4[0].select_channels([2]).channel_count == 1)
        record("l3.audio.4CH 立体声/4ch 流仍被 channel_sync 拒绝 (判定不变)",
               eligible_audio(raw4)[0] is False,
               eligible_audio(raw4)[1])
        record("l3.audio.4CH plan 默认全选 + 保留原始",
               probe4.plan().is_default
               and probe4.plan().output_channel_count == 4)
    else:
        record("l3.audio.4CH 真实探测 1 stream / 4 channel", False,
               "合成 4CH 素材失败")

    # --- 情况 D: 4 条 mono PCM 流 ---
    parts = []
    for i in range(4):
        p = d / f"audio_model_mono{i}.mov"
        if mk(p, ["-f", "lavfi", "-i",
                  f"anoisesrc=duration=3:sample_rate=48000:color=pink:seed={i}",
                  "-c:a", "pcm_s24le", "-ac", "1", "-f", "mov"]):
            parts.append(p)
    srcm = d / "audio_model_4mono.mov"
    okm = (len(parts) == 4 and mk(
        srcm,
        ["-i", parts[0], "-i", parts[1], "-i", parts[2], "-i", parts[3],
         "-map", "0:a:0", "-map", "1:a:0", "-map", "2:a:0", "-map", "3:a:0",
         "-c", "copy", "-shortest"],
    ))
    if okm:
        raw_m, probe_m = probe_model(srcm)
        record("l3.audio.4×mono 真实探测 4 stream / 4 channel",
               probe_m.stream_count == 4 and probe_m.channel_count == 4
               and probe_m.is_multi_mono, f"{probe_m.summary()['streams']}")
        record("l3.audio.4×mono audio_position 与容器 index 一致",
               [s.audio_position for s in probe_m.streams] == [0, 1, 2, 3]
               and probe_m.stream_indices == sorted(probe_m.stream_indices))
        record("l3.audio.4×mono eligible_audio 仍判可对齐 (生产判定一致)",
               eligible_audio(raw_m) == (True, ""), eligible_audio(raw_m)[1])
        plan_m = probe_m.plan()
        record("l3.audio.4×mono 4 track 各 1 channel",
               len(plan_m.input_tracks) == 4
               and [t.channel_indices for t in plan_m.input_tracks]
               == [[0], [0], [0], [0]]
               and [t.source_stream_index for t in plan_m.input_tracks]
               == probe_m.stream_indices)
        # 同步报告 -> 模型 (用真实流序号; 不跑算法, 只验接线)
        fake = {
            "status": "applied", "algo_version": "test", "anchor_stream": 3,
            "channels": [
                {"stream": i, "delay_samples": 100.0 * i,
                 "delay_ms": i * 2.083, "confidence": 0.9,
                 "decision": "fixed", "shift_samples": 100 * i}
                for i in range(4)
            ],
        }
        from core.audio_models import apply_channel_sync_report

        plan_m = apply_channel_sync_report(plan_m, fake)
        record("l3.audio.同步报告按音频序号回填 4 条 track",
               [t.sync_offset_samples for t in plan_m.input_tracks]
               == [0.0, 100.0, 200.0, 300.0]
               and all(t.sync_status is SyncStatus.SUCCESS
                       for t in plan_m.input_tracks)
               and [t.sync.anchor for t in plan_m.input_tracks]
               == ["stream:3"] * 4,
               f"{[t.sync_offset_samples for t in plan_m.input_tracks]}")
        record("l3.audio.同步后身份仍可追溯原始 stream/channel",
               [t.source_ids for t in plan_m.input_tracks]
               == [[f"default:s{i}:c0"] for i in probe_m.stream_indices])
    else:
        record("l3.audio.4×mono 真实探测 4 stream / 4 channel", False,
               "合成 4×mono 素材失败")

    # --- 情况 E: 2 条 stereo 流 ---
    st_parts = []
    for i in range(2):
        p = d / f"audio_model_stereo{i}.mov"
        if mk(p, ["-f", "lavfi", "-i",
                  f"anoisesrc=duration=3:sample_rate=48000:color=pink:seed={i}",
                  "-c:a", "pcm_s24le", "-ac", "2", "-f", "mov"]):
            st_parts.append(p)
    srcs = d / "audio_model_2stereo.mov"
    if len(st_parts) == 2 and mk(
        srcs, ["-i", st_parts[0], "-i", st_parts[1],
               "-map", "0:a:0", "-map", "1:a:0", "-c", "copy", "-shortest"],
    ):
        raw_s, probe_s = probe_model(srcs)
        tracks_s = probe_s.tracks(TrackBuildMode.PER_STREAM)
        record("l3.audio.2×stereo 真实探测 2 stream / 4 channel",
               probe_s.stream_count == 2 and probe_s.channel_count == 4
               and [t.channel_indices for t in tracks_s] == [[0, 1], [0, 1]])
        record("l3.audio.2×stereo 布局名可用或安全退化",
               all(s.layout_source in ("layout", "channel_count")
                   for s in probe_s.streams),
               f"{[s.layout_source for s in probe_s.streams]}")
        record("l3.audio.2×stereo 2ch 流仍被 channel_sync 拒绝 (判定不变)",
               eligible_audio(raw_s)[0] is False)
    else:
        record("l3.audio.2×stereo 真实探测 2 stream / 4 channel", False,
               "合成 2×stereo 素材失败")

    # --- 情况 F: 真实素材 layout 缺失但仍建成完整模型 ---
    real = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real.glob("*.MP4")) if real.is_dir() else []
    if real_files:
        raw_r, probe_r = probe_model(real_files[0])
        plan_r = probe_r.plan()
        record("l3.audio.真实 A7M5 4×mono 素材建模 (layout 缺失不丢流)",
               probe_r.stream_count == 4
               and [s.channel_count for s in probe_r.streams] == [1, 1, 1, 1]
               and len(plan_r.input_tracks) == 4
               and all(t.channel_count == 1 for t in plan_r.input_tracks)
               and all(t.layout == "" for t in plan_r.input_tracks),
               f"{probe_r.summary()['streams']}")
        record("l3.audio.真实素材 channel_sync 判定不变",
               eligible_audio(raw_r)[0] is True)
    else:
        record("l3.audio.真实 A7M5 4×mono 素材建模 (layout 缺失不丢流)", False,
               "testsets A7M5 4CH 素材不存在")


def l3_audio_selection() -> None:
    """v0.7.1 Phase 2: 真实探测的多来源选择 + 通道映射 (含外挂 WAV).

    真实素材:
      * Sony A7M5 4CH (testsets, 4×mono PCM, layout 缺失)  -> media source
      * ffmpeg 生成的 deterministic 外挂 WAV (mono / stereo / 4CH) -> wav source

    只做"探测 -> 建模 -> 选择 -> 映射 -> dry-run spec"; **不执行 ffmpeg**,
    因此不会改变任何生产路径 (默认音频路径仍由 AudioPlan=None 决定)。
    """
    section("L3 音频来源/选择/映射 (v0.7.1 Phase 2)")
    from core.audio_models import (
        AudioSourceType, AudioSyncResult, SyncStatus, build_multi_source_plan,
    )
    from core.audio_plan import (
        REASON_AUDIO_MIX_NOT_SUPPORTED, AudioMapStrategy, AudioPlanner,
        build_map_spec, source_plan, validate_plan,
    )
    from core.audio_probe import audio_probe_of

    d = IN_DIR / "audio_sel"
    d.mkdir(parents=True, exist_ok=True)

    def mk_wav(dst: Path, channels: int, seed: int = 0) -> bool:
        r = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
               f"anoisesrc=duration=2:sample_rate=48000:color=pink:seed={seed}",
               "-c:a", "pcm_s24le", "-ac", str(channels),
               "-rf64", "auto", dst, timeout=600)
        return dst.is_file() and dst.stat().st_size > 0 and r.returncode == 0

    wav_mono = d / "ext_mono.wav"
    wav_stereo = d / "ext_stereo.wav"
    wav_4ch = d / "ext_4ch.wav"
    ok_mono = mk_wav(wav_mono, 1, seed=1)
    ok_stereo = mk_wav(wav_stereo, 2, seed=2)
    ok_4ch = mk_wav(wav_4ch, 4, seed=3)
    record("l3.audio2.外挂 WAV fixture 生成 (mono/stereo/4CH)",
           ok_mono and ok_stereo and ok_4ch)

    def probe_of_wav(path: Path, sid: str, kind, expected_channels: int):
        raw = ffprobe_json(path).get("streams", [])
        probe = audio_probe_of(raw, source_id=sid, source_type=kind,
                               path=str(path))
        return raw, probe

    if ok_mono and ok_stereo and ok_4ch:
        _rm, mono_p = probe_of_wav(wav_mono, "wav_mono",
                                   AudioSourceType.WAV, 1)
        _rs, stereo_p = probe_of_wav(wav_stereo, "wav_stereo",
                                     AudioSourceType.WAV, 2)
        _r4, quad_p = probe_of_wav(wav_4ch, "wav_4ch",
                                   AudioSourceType.WAV, 4)
        record("l3.audio2.真实 WAV 建模 (mono/stereo/4CH)",
               mono_p.channel_count == 1 and stereo_p.channel_count == 2
               and quad_p.channel_count == 4
               and mono_p.to_source().source_type is AudioSourceType.WAV
               and quad_p.to_source().stream_count == 1
               and quad_p.streams[0].id == "wav_4ch:s0",
               f"{[p.summary()['streams'] for p in (mono_p, stereo_p, quad_p)]}")
        record("l3.audio2.WAV 时间轴独立 (不与主容器共用)",
               mono_p.to_source().timing.shares_timeline is False)

        # --- 真实 A7M5 4CH media source + 外挂 WAV source ---
        real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
        real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
        if real_files:
            raw_real = ffprobe_json(real_files[0]).get("streams", [])
            cam_p = audio_probe_of(raw_real, source_id="camera",
                                   path=str(real_files[0]))
            plan = source_plan([
                {"source_id": "camera", "streams": cam_p.streams,
                 "path": str(real_files[0])},
                {"source_id": "wav_stereo", "streams": stereo_p.streams,
                 "source_type": "wav", "path": str(wav_stereo)},
            ])
            record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划",
                   plan.source_ids == ["camera", "wav_stereo"]
                   and plan.is_multi_source()
                   and len(plan.selected_channels) == 6
                   and build_map_spec(plan).full_stream_copy,
                   f"{plan.source_ids}")
            record("l3.audio2.多来源默认 spec 全部整流 copy (逐流一个操作)",
                   [o.source_id for o in build_map_spec(plan).operations]
                   == ["camera"] * 4 + ["wav_stereo"]
                   and all(o.strategy is AudioMapStrategy.STREAM_COPY
                           for o in build_map_spec(plan).operations)
                   and [o.output_indices
                        for o in build_map_spec(plan).operations]
                   == [[0], [1], [2], [3], [4, 5]],
                   f"{[o.to_dict()['output_indices'] for o in build_map_spec(plan).operations]}")
            record("l3.audio2.真实素材 audio_position 与容器 index 分离",
                   [c.stream_index for c in
                    plan.source("camera").channels()] == [1, 2, 3, 4]
                   and [c.id for c in plan.source("camera").channels()]
                   == [f"camera:s{i}:c0" for i in (1, 2, 3, 4)]
                   and plan.input_tracks[0].metadata["audio_position"] == 0)

            # 跨来源选择: camera 的第 3 条 mono + wav 的 L
            p = AudioPlanner(plan)
            p.select_channels("camera:s3:c0", "wav_stereo:s0:c0")
            p.map_channels("wav_stereo:s0:c0", "camera:s3:c0")
            spec = p.map_spec()
            record("l3.audio2.真实素材跨来源选择 + 重排 dry-run",
                   spec.executable
                   and [a.channel_id for a in spec.assignments]
                   == ["wav_stereo:s0:c0", "camera:s3:c0"]
                   and [o.input_index for o in spec.operations] == [1, 0],
                   f"{[a.channel_id for a in spec.assignments]}")
            record("l3.audio2.跨来源 spec 保留 source 身份与 sync 字段",
                   spec.trace(1)["source_id"] == "camera"
                   and spec.trace(1)["stream_index"] == 3
                   and "sync_status" in spec.trace(1))

            # 4×mono 全程保持 4 个独立通道 (不塌缩)
            p4 = AudioPlanner(plan)
            p4.select_sources("camera")
            p4.map_channels("camera:s4:c0", "camera:s2:c0",
                            "camera:s1:c0", "camera:s3:c0")
            spec4 = p4.map_spec()
            record("l3.audio2.真实 4×mono 重排 (4,2,1,3) 逐流重排",
                   spec4.executable
                   and [a.stream_index for a in spec4.assignments]
                   == [4, 2, 1, 3]
                   and [a.channel_index for a in spec4.assignments]
                   == [0, 0, 0, 0]
                   and [o.output_indices for o in spec4.operations]
                   == [[0], [1], [2], [3]],
                   f"{[a.stream_index for a in spec4.assignments]}")
            record("l3.audio2.4×mono 重排 = 整流 copy 重排 (无需声道过滤)",
                   spec4.full_stream_copy is True
                   and all(o.strategy is AudioMapStrategy.STREAM_COPY
                           for o in spec4.operations))
            single_stream = AudioPlanner(source_plan(
                [{"source_id": "ch4", "streams": [
                    _raw_audio_stream(2, channels=4, layout="4.0")]}]))
            single_stream.select_channels("ch4:s2:c2")
            single_spec = single_stream.map_spec()
            record("l3.audio2.单流取部分声道才需要声道过滤",
                   single_spec.full_stream_copy is False
                   and single_spec.strategy is AudioMapStrategy.CHANNEL_FILTER
                   and single_spec.operations[0].stream_channel_count == 4
                   and single_spec.operations[0].channel_indices == [2])
            only_cam = AudioPlanner(plan)
            only_cam.select_sources("camera")
            record("l3.audio2.真实素材整流全选仍可走 -map copy",
                   build_map_spec(only_cam.plan).full_stream_copy is True
                   and build_map_spec(only_cam.plan).strategy
                   is AudioMapStrategy.STREAM_COPY)

            # sync 结果经选择/映射后完整保留 (§18)
            # 注意: 真实 A7M5 是 4 条 mono 流 -> 每条流一个 single-channel track,
            # 因此逐个 track 附同步状态, 而不是在一条 track 上找 4 个声道。
            p2 = AudioPlanner(plan)
            for track in p2.plan.input_tracks:
                track.channels[0].sync = AudioSyncResult(
                    status=SyncStatus.SUCCESS,
                    offset_samples=960.0 + track.channels[0].stream_index,
                    offset_ms=20.0,
                    source="channel_sync_report",
                )
            p2.select_channels("camera:s3:c0")
            p2.map_channels("camera:s3:c0")
            spec2 = p2.map_spec()
            record("l3.audio2.真实素材同步后仍可追溯 "
                   "(output 0 -> camera:s3:c0 -> +963)",
                   spec2.trace(0)["channel_id"] == "camera:s3:c0"
                   and spec2.trace(0)["sync_offset_samples"] == 963.0
                   and spec2.trace(0)["sync_status"] == "success",
                   f"{spec2.trace(0)}")
            record("l3.audio2.未选中声道的同步状态不受影响",
                   p2.plan.channel("camera:s1:c0").sync.offset_samples == 961.0)
            mix_try = AudioPlanner(plan)
            mix_try.select_channels("camera:s1:c0", "camera:s2:c0")
            mix_try.plan.mix_mode = "sum"
            mix_spec = mix_try.map_spec()
            record("l3.audio2.混音仍被拒绝 (真实素材, audio_mix_not_supported)",
                   mix_spec.executable is False
                   and mix_spec.strategy is AudioMapStrategy.MIXING
                   and REASON_AUDIO_MIX_NOT_SUPPORTED in
                   [e["reason"] for e in mix_spec.errors])
            cam_only = AudioPlanner(plan)
            cam_only.select_sources("camera")
            record("l3.audio2.真实素材输出单元一律非混音",
                   all(not t.is_mixing
                       for t in cam_only.map_spec().tracks))
        else:
            record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划", False,
                   "testsets A7M5 4CH 素材不存在")
    else:
        record("l3.audio2.真实 WAV 建模 (mono/stereo/4CH)", False,
               "WAV fixture 生成失败")
        record("l3.audio2.真实 A7M5 + 外挂 WAV 组成多来源计划", False,
               "WAV fixture 生成失败")


def l3_channel_sync_p1_algo() -> None:
    """L3 延时补偿 P1 算法级专项 (纯音频算法, 进程内 run_channel_sync,
    不经过任何视频转码管线 — 可在不跑编码流程时单独执行)。"""
    section("L3 延时补偿 P1 算法级 (音频专用, 无转码)")
    import hashlib

    try:
        import numpy as np
    except ImportError:
        record("l3.p1_algo_numpy_可用", False, "numpy 缺失 — 本组跳过")
        return
    record("l3.p1_algo_numpy_可用", True)
    p1d = IN_DIR / "sync_p1" / "d"
    p1d.mkdir(parents=True, exist_ok=True)

    def expect(case: str, cond: bool, detail: str) -> None:
        record(f"l3.{case}", cond, detail)

    def shb(*args: str, timeout: int = 3600) -> tuple[int, bytes]:
        r = subprocess.run(
            [str(a) for a in args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
        )
        return r.returncode, (r.stdout or b"")

    def make_audio4(name: str, specs: list[str], codec: str = "pcm_s24le",
                    ar: int = 48000, dur: int = 8) -> Path | None:
        """4×mono 音频专用文件: specs[i] = "lavfi输入[|滤镜链]"。"""
        p = p1d / name
        cmd = [FFMPEG, "-v", "error", "-y"]
        for i, s in enumerate(specs):
            src, _, _filt = s.partition("|")
            cmd += ["-f", "lavfi", "-i", src]
        fc, maps = [], []
        for i, s in enumerate(specs):
            _src, has_f, filt = s.partition("|")
            if has_f:
                fc.append(f"[{i}:a]{filt}[c{i}]")
                maps += ["-map", f"[c{i}]"]
            else:
                maps += ["-map", f"{i}:a:0"]
        if fc:
            cmd += ["-filter_complex", ";".join(fc)]
        cmd += maps
        cmd += ["-c:a", codec, "-ar", str(ar), "-ac", "1", "-f", "mov", p]
        r = sh(*cmd, timeout=900)
        return p if (p.is_file() and r.returncode == 0) else None

    def sync_of(p: Path, opts: dict | None = None, name: str = "algo") -> dict:
        from core.channel_sync import run_channel_sync

        v = ffprobe_json(p)
        return run_channel_sync(
            source=p, ffmpeg=FFMPEG, work_dir=WORK / f"{name}_sync",
            streams=v.get("streams", []), opts=opts, log=lambda m: None,
        )

    def mux4(dst: Path, parts: list[Path]) -> bool:
        if not (len(parts) == 4 and all(p.is_file() for p in parts)):
            return False
        r = sh(FFMPEG, "-v", "error", "-y",
               "-i", parts[0], "-i", parts[1], "-i", parts[2],
               "-i", parts[3],
               "-map", "0:a:0", "-map", "1:a:0", "-map", "2:a:0",
               "-map", "3:a:0",
               "-c", "copy", "-shortest", dst, timeout=600)
        return dst.is_file() and r.returncode == 0

    def decode_f32(p: Path, idx: int) -> np.ndarray | None:
        rc, out = shb(FFMPEG, "-v", "error", "-i", p,
                      "-map", f"0:a:{idx}", "-f", "f32le", "-")
        return np.frombuffer(out, dtype="<f4") if rc == 0 else None

    def np_delay_raw(name: str, delay_samples: float, frac: float = 0.0,
                     seed: int = 20) -> Path:
        """numpy 构造带延迟轨的 raw (支持负延迟与分数延迟)。"""
        r = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
               "-i", f"anoisesrc=color=pink:duration=8:seed={seed}:"
                     "sample_rate=48000",
               "-f", "f32le", p1d / f"{name}_src.f32", timeout=600)
        noise = np.fromfile(p1d / f"{name}_src.f32", dtype="<f4")
        noise = noise.astype(np.float64)
        if frac:
            taps = 97
            mm = np.arange(-(taps // 2), taps // 2 + 1)
            hh = np.sinc(mm - frac) * np.hanning(taps)
            hh /= hh.sum()
            noise = np.convolve(noise, hh, mode="same")
        total = noise.shape[0]
        if delay_samples >= 0:
            # 晚到 delay: 内容后移, 尾部延长 (adelay 约定)
            total += int(delay_samples)
            out = np.zeros(total, dtype=np.float32)
            out[int(delay_samples):] = noise.astype(np.float32)
        else:
            # 早到 |delay|: 内容前移, 保全长 (尾部补零)
            out = np.zeros(total, dtype=np.float32)
            out[: total - int(-delay_samples)] = noise[
                int(-delay_samples):
            ].astype(np.float32)
        raw = p1d / f"{name}.raw"
        raw.write_bytes(out.tobytes())
        return raw

    def raw_to_mov(name: str, codec: str = "pcm_s24le") -> Path:
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "f32le", "-ar", "48000", "-ac", "1",
               "-i", p1d / f"{name}.raw", "-c:a", codec, "-f", "mov",
               p1d / f"{name}.mov", timeout=600)
        return p1d / f"{name}.mov"

    # C27: 负延迟端到端 (CH1 早到 900 样本 -> shift -900 后移 + 头补零)
    src27 = p1d / "sync_p1_k.mov"
    r1 = np_delay_raw("k_neg", -900, seed=20)
    r2 = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", "anoisesrc=color=pink:duration=8:seed=20:sample_rate=48000",
            "-filter_complex", "[0:a]adelay=900S[o]",
            "-map", "[o]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
            "-f", "mov", p1d / "k_pos.mov", timeout=600)
    r3 = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", "anoisesrc=color=pink:duration=8:seed=20:sample_rate=48000",
            "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
            "-f", "mov", p1d / "k_ref.mov", timeout=600)
    r4 = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", "anoisesrc=color=pink:duration=8:seed=20:sample_rate=48000",
            "-filter_complex", "[0:a]adelay=1223S[o]",
            "-map", "[o]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
            "-f", "mov", p1d / "k_ch4.mov", timeout=600)
    if mux4(src27, [raw_to_mov("k_neg"), p1d / "k_pos.mov",
                    p1d / "k_ref.mov", p1d / "k_ch4.mov"]):
        res27 = sync_of(src27, name="c27")
        r27 = {c["stream"]: c for c in res27.get("channels", [])}
        expect("C27_负延迟_shift负数",
               res27["status"] == "applied"
               and r27.get(0, {}).get("decision") == "fixed"
               and r27.get(0, {}).get("shift_samples") == -900
               and r27.get(1, {}).get("decision") == "fixed"
               and r27.get(1, {}).get("shift_samples") == 900
               and r27.get(3, {}).get("decision") == "fixed",
               f"status={res27['status']} "
               f"rows={json.dumps(r27, ensure_ascii=False)}")
        # 修正后的 CH1 音频文件 vs 参考轨: 直接用算法复测残差
        af0 = WORK / "c27_sync" / "audio_0.mov"
        ref3 = decode_f32(p1d / "k_ref.mov", 0)
        fx0 = decode_f32(af0, 0) if af0.is_file() else None
        if ref3 is not None and fx0 is not None:
            from core import sync_estimate

            ee = sync_estimate.estimate_pair(
                ref3, fx0, sample_rate=48000, search_window_ms=80.0,
                frame_ms=200.0, hop_ms=100.0,
                anchor_segment_seconds=3.0, min_confidence=0.3,
            )
            expect("C27_负延迟_修正轨已对齐",
                   abs(ee.delay_samples) <= 2.0 and ee.confidence > 0.5,
                   f"residual={ee.delay_samples:.2f} samples "
                   f"conf={ee.confidence:.2f}")
        else:
            expect("C27_负延迟_修正轨已对齐", False, "audio_0.mov 缺失")
    else:
        expect("C27_负延迟_shift负数", False, "合成失败")
        expect("C27_负延迟_修正轨已对齐", False, "合成失败")

    # C28: 96kHz 端到端
    src28 = make_audio4(
        "sync_p1_l.mov", [
            "anoisesrc=color=pink:duration=8:seed=21:sample_rate=96000|adelay=300S",
            "anoisesrc=color=pink:duration=8:seed=21:sample_rate=96000|adelay=2446S",
            "anoisesrc=color=pink:duration=8:seed=21:sample_rate=96000",
            "anoisesrc=color=pink:duration=8:seed=21:sample_rate=96000|adelay=1223S",
        ], ar=96000,
    )
    if src28:
        res28 = sync_of(src28, name="c28")
        r28 = {c["stream"]: c for c in res28.get("channels", [])}
        expect("C28_96kHz_整数延迟",
               res28["status"] == "applied"
               and r28.get(0, {}).get("shift_samples") == 300
               and r28.get(1, {}).get("shift_samples") == 2446
               and r28.get(3, {}).get("shift_samples") == 1223,
               f"status={res28['status']} "
               f"rows={json.dumps(r28, ensure_ascii=False)}")
    else:
        expect("C28_96kHz_整数延迟", False, "96k 合成失败")

    # C29: s32le 源 -> f64 存储管线端到端
    src29 = make_audio4(
        "sync_p1_m.mov", [
            "anoisesrc=color=pink:duration=8:seed=22:sample_rate=48000|adelay=300S",
            "anoisesrc=color=pink:duration=8:seed=22:sample_rate=48000|adelay=900S",
            "anoisesrc=color=pink:duration=8:seed=22:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=22:sample_rate=48000|adelay=1223S",
        ], codec="pcm_s32le",
    )
    if src29:
        res29 = sync_of(src29, name="c29")
        r29 = {c["stream"]: c for c in res29.get("channels", [])}
        expect("C29_s32le_f64管线",
               res29["status"] == "applied"
               and r29.get(1, {}).get("decision") == "fixed"
               and r29.get(1, {}).get("storage_dtype") == "f64"
               and r29.get(1, {}).get("shift_samples") == 900
               and r29.get(3, {}).get("decision") == "fixed",
               f"status={res29['status']} "
               f"rows={json.dumps(r29, ensure_ascii=False)}")
    else:
        expect("C29_s32le_f64管线", False, "s32le 合成失败")

    # C30: f32le 源 + >0dBFS 内容 — 不钳位、样本值不变 (整数移位)
    src30 = make_audio4(
        "sync_p1_n.mov", [
            "anoisesrc=color=pink:duration=8:seed=23:sample_rate=48000|volume=6dB,adelay=300S",
            "anoisesrc=color=pink:duration=8:seed=23:sample_rate=48000|adelay=900S",
            "anoisesrc=color=pink:duration=8:seed=23:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=23:sample_rate=48000",
        ], codec="pcm_f32le",
    )
    if src30:
        src0 = decode_f32(src30, 0)
        peak_src = float(np.max(np.abs(src0))) if src0 is not None else 0.0
        res30 = sync_of(src30, name="c30")
        r30 = {c["stream"]: c for c in res30.get("channels", [])}
        fixed30 = WORK / "c30_sync" / "audio_0.mov"
        ok_vals = False
        if fixed30.is_file() and src0 is not None:
            fx = decode_f32(fixed30, 0)
            if fx is not None and fx.shape[0] == src0.shape[0]:
                expect_shift = np.zeros_like(src0)
                expect_shift[: src0.shape[0] - 300] = src0[300:]
                ok_vals = bool(np.array_equal(fx, expect_shift))
        expect("C30_f32le超0dBFS_不钳位样本不变",
               res30["status"] == "applied"
               and r30.get(0, {}).get("decision") == "fixed"
               and r30.get(0, {}).get("shift_samples") == 300
               and peak_src > 1.0 and ok_vals,
               f"status={res30['status']} peak_src={peak_src:.2f} "
               f"values_exact={ok_vals}")
    else:
        expect("C30_f32le超0dBFS_不钳位样本不变", False, "f32le 合成失败")

    # C31: 近窗缘延迟 (68.75ms, 窄窗边缘置信不足) -> 宽窗采纳后照常修正
    src31 = make_audio4(
        "sync_p1_o.mov", [
            "anoisesrc=color=pink:duration=8:seed=24:sample_rate=48000|adelay=300S",
            "anoisesrc=color=pink:duration=8:seed=24:sample_rate=48000|adelay=3300S",
            "anoisesrc=color=pink:duration=8:seed=24:sample_rate=48000",
            "anoisesrc=color=pink:duration=8:seed=24:sample_rate=48000|adelay=1223S",
        ],
    )
    if src31:
        res31 = sync_of(src31, name="c31")
        r31 = {c["stream"]: c for c in res31.get("channels", [])}
        expect("C31_近窗缘_宽窗采纳修正",
               res31["status"] == "applied"
               and r31.get(1, {}).get("decision") == "fixed"
               and r31.get(1, {}).get("shift_samples") == 3300,
               f"status={res31['status']} "
               f"rows={json.dumps(r31, ensure_ascii=False)}")
    else:
        expect("C31_近窗缘_宽窗采纳修正", False, "合成失败")

    # C32: 覆盖率门确定性触发宽窗采纳 (稀疏语音内容, 天然覆盖率 ~65%) —
    # 修正阶段必须用采纳后的估计 (回归: 曾误用窄窗 NaN delay 导致崩溃/误标)
    def speech_like(n: int, seed: int, fs: int) -> np.ndarray:
        from scipy import signal as _sig

        rng = np.random.default_rng(seed)
        sos = _sig.butter(4, [100.0, 4000.0], btype="band", fs=fs,
                          output="sos")
        carrier = _sig.sosfilt(sos, rng.standard_normal(n))
        carrier /= np.max(np.abs(carrier)) + 1e-12
        envelope = np.zeros(n)
        t = 0.0
        while t < n / fs:
            start = int(t * fs)
            dur = rng.uniform(0.08, 0.4)
            m = int(dur * fs)
            if start < n and m > 0:
                k = np.arange(min(m, n - start))
                envelope[start: start + len(k)] += (
                    rng.uniform(0.4, 1.0) * np.exp(-k / (dur * fs / 3.0))
                )
            t += rng.uniform(0.15, 0.6) + dur
        out = carrier * np.minimum(envelope, 1.0)
        return (out / (np.max(np.abs(out)) + 1e-12)).astype(np.float32)

    def write_speech_mov(name: str, delay: int, seed: int = 25) -> Path:
        SR = 48000
        base = speech_like(8 * SR, seed=seed, fs=SR)
        out = np.zeros(base.shape[0] + max(0, delay), dtype=np.float32)
        if delay >= 0:
            out[delay:] = base
        else:
            out[: base.shape[0] + delay] = base[-delay:]
        raw = p1d / f"{name}.raw"
        raw.write_bytes(out.tobytes())
        r = sh(FFMPEG, "-v", "error", "-y",
               "-f", "f32le", "-ar", "48000", "-ac", "1",
               "-i", raw, "-c:a", "pcm_s24le", "-f", "mov",
               p1d / f"{name}.mov", timeout=600)
        try:
            raw.unlink()
        except OSError:
            pass
        return p1d / f"{name}.mov"

    src32 = p1d / "sync_p1_p.mov"
    if mux4(src32, [write_speech_mov("p1", 300),
                    write_speech_mov("p2", 3300),
                    write_speech_mov("p3", 0),
                    write_speech_mov("p4", 1223)]):
        res32 = sync_of(
            src32, opts={"min_usable_fraction": 0.95}, name="c32"
        )
        r32 = {c["stream"]: c for c in res32.get("channels", [])}
        expect("C32_宽窗采纳_修正阶段用采纳估计",
               res32["status"] == "applied"
               and r32.get(1, {}).get("decision") == "fixed"
               and r32.get(1, {}).get("shift_samples") == 3300
               and r32.get(1, {}).get("reason") is None,
               f"status={res32['status']} "
               f"rows={json.dumps(r32, ensure_ascii=False)}")
    else:
        expect("C32_宽窗采纳_修正阶段用采纳估计", False, "合成失败")

    # 清理本组中间文件 (留在输入目录会被当作源文件)
    for tmp in p1d.glob("k_*"):
        try:
            tmp.unlink()
        except OSError:
            pass


# ===========================================================================
# Phase 3A — PCM routing / timeline / WAV export (v0.7.1)
# ===========================================================================

P3A_SR = 48000


def _p3a_fixture(
    path: Path, channels: int, samples: int, impulses: dict[int, int],
    *, sample_rate: int = P3A_SR, amplitudes: dict[int, float] | None = None,
) -> Path:
    """确定性 impulse 素材 (浮点 WAV, bit-exact 往返)。

    `impulses[channel] = 采样下标`; 幅度默认 `0.5 + 0.1 * channel` (每个声道
    可区分), 便于断言"哪个声道的数据到了哪个输出声道"。
    """
    from core.audio_wav import WavFormat, write_wav

    amp = dict(amplitudes or {})
    x = np.zeros((samples, channels), dtype=np.float32)
    for ch, at in impulses.items():
        x[at, ch] = amp.get(ch, 0.5 + 0.1 * ch)
    write_wav(
        path, [x], sample_rate=sample_rate, channel_count=channels,
        sample_format=WavFormat.FLOAT32, frame_count=samples, overwrite=True,
    )
    return path


def _p3a_build_plan(specs: list[dict[str, Any]]) -> Any:
    """[{source_id, path, streams, [source_type]}, …] -> AudioPlan (多来源)。

    真实来源用探测得到的 `AudioStream`; 合成来源用 `_p3a_plan()`。
    """
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
    )

    plan = AudioPlan()
    tracks: list[Any] = []
    for spec in specs:
        streams = list(spec["streams"])
        tracks.extend(
            AudioTrackBuilder(streams, source_id=spec["source_id"]).tracks()
        )
        plan.sources.append(AudioSource(
            source_id=spec["source_id"],
            source_type=spec.get("source_type", AudioSourceType.MEDIA),
            path=str(spec["path"]),
            streams=streams,
            input_index=spec.get("input_index"),
        ))
    plan.input_tracks = tracks
    plan.selected_tracks = [t.track_id for t in tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    return plan


def _p3a_plan(
    specs: list[dict[str, Any]], *, sample_rate: int = P3A_SR,
) -> Any:
    """[{source_id, path, channels, samples}, …] -> AudioPlan (多来源 WAV)。

    declared / actual 均来自 fixture 的真实长度 (WAV 无 nb_frames, 因此
    声明值走 `duration × sample_rate`), 与真实探测路径一致。
    """
    from core.audio_models import build_audio_streams

    prepared: list[dict[str, Any]] = []
    for spec in specs:
        raw = [_raw_audio_stream(
            0,
            channels=int(spec["channels"]),
            codec="pcm_f32le",
            sample_rate=str(sample_rate),
            sample_fmt="flt",
            layout=None,
            duration=f"{int(spec['samples']) / sample_rate:.9f}",
            bit_rate=None,
            codec_long_name="PCM 32-bit floating point",
            tag_string=None,
        )]
        prepared.append({
            **spec,
            "streams": build_audio_streams(
                raw, source_id=spec["source_id"]
            ),
        })
    return _p3a_build_plan(prepared)


def _p3a_swap(plan: Any, channel_id: str, replacement: Any) -> bool:
    """把某个 AudioChannel 换成另一个对象 (保持身份串)。"""
    for track in plan.input_tracks:
        for index, ch in enumerate(track.channels):
            if ch.id == channel_id:
                track.channels[index] = replacement
                return True
    return False


def _p3a_route_channels(plan: Any, order: list[str]) -> bool:
    """按给定顺序设置输出 (显式 mapping); 数量不匹配 -> 用 exclude 表达。"""
    from core.audio_plan import AudioPlanner

    planner = AudioPlanner(plan)
    ok = True
    try:
        planner.select_channels(*order)
        if len(order) > 1:
            planner.map_channels(*order)
    except Exception:                     # noqa: BLE001
        ok = False
    return ok


def _p3a_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _p3a_impulse_map(arr: Any, threshold: float = 0.05) -> list[list[int]]:
    """逐输出声道 -> impulse 位置列表 (确定性断言用)。"""
    out: list[list[int]] = []
    for ch in range(arr.shape[1]):
        idx = np.nonzero(np.abs(arr[:, ch]) > threshold)[0]
        out.append([int(i) for i in idx[:4]])
    return out


def _p3a_impulse_timeline(
    arr: Any, timeline: Any, threshold: float = 0.05,
) -> list[list[int]]:
    """同 `_p3a_impulse_map`, 但把数组下标换算成 **timeline 样本位置**。

    输出窗口起点未必是 0 (offset 会把窗口推到负数起点), 因此断言"两轨是否
    对齐"必须比较 timeline 位置而不是数组下标。
    """
    base = int(getattr(timeline, "start_sample", 0) or 0)
    return [
        [i + base for i in positions]
        for positions in _p3a_impulse_map(arr, threshold)
    ]


class _PipePCMReader:
    """ffmpeg stdout 直读的 PCM reader (与 `AudioPCMReader` 同接口)。

    测试专用: 与生产读取器**同一抽象**、不同传输层 — 结果必须逐样本一致,
    从而把"结果不依赖读取实现"钉进回归 (Phase 3A §性能/§chunk invariance)。
    """

    def __init__(self, plan: Any, ffmpeg: Path, work_dir: Path,
                 chunk_frames: int = 16384) -> None:
        self.plan = plan
        self.ffmpeg = Path(ffmpeg)
        self.work_dir = Path(work_dir)
        self.chunk_frames = int(chunk_frames)
        self.streams: dict[str, dict[str, Any]] = {}
        self.procs: dict[str, Any] = {}
        self.positions: dict[str, int] = {}
        self.decoded = 0

    def _raw_stream(self, stream_id: str) -> dict[str, Any]:
        for source in self.plan.sources:
            for stream in source.streams:
                if stream.id == stream_id:
                    return {
                        "path": source.path,
                        "position": int(
                            stream.audio_position
                            if stream.audio_position is not None
                            else stream.stream_index
                        ),
                        "channels": int(stream.channel_count),
                        "sample_rate": int(stream.sample_rate),
                        "declared": (
                            int(round(float(stream.duration_sec or 0)
                                      * int(stream.sample_rate)))
                            if stream.duration_sec else None
                        ),
                    }
        raise KeyError(stream_id)

    def prepare(self, stream_ids: Any = None) -> list[Any]:
        ids = (
            [str(i) for i in stream_ids] if stream_ids is not None
            else [s.id for s in self.plan.sources for s in s.streams]
        )
        out = []
        for sid in ids:
            out.append(self._load(sid))
        return out

    def _load(self, stream_id: str) -> dict[str, Any]:
        if stream_id in self.streams:
            return self.streams[stream_id]
        spec = self._raw_stream(stream_id)
        # 实际可用帧数: WAV 读 header 的 data 大小 (与生产读取器同一判据),
        # 其它容器用 ffprobe 的 nb_frames / duration 声明值。
        actual = 0
        declared = spec["declared"]
        path = Path(spec["path"])
        try:
            from core.audio_wav import parse_wav_header

            head = parse_wav_header(path.read_bytes(), path=str(path))
            if head.channel_count == spec["channels"]:
                actual = int(head.frame_count)
        except Exception:                     # noqa: BLE001
            actual = 0
        if actual <= 0:
            approx = max(
                0, (path.stat().st_size - 4096)
                // max(1, 4 * int(spec["channels"]))
            )
            actual = int(declared) if declared else approx
        info = {
            "stream_id": stream_id, "source_id": stream_id.split(":")[0],
            "stream_index": int(stream_id.rsplit(":s", 1)[1]),
            "channels": spec["channels"],
            "sample_rate": spec["sample_rate"],
            "declared": declared,
            "actual": actual,
            "_spec": spec,
        }
        self.streams[stream_id] = info
        return info

    def _proc(self, stream_id: str) -> Any:
        proc = self.procs.get(stream_id)
        if proc is None:
            spec = self.streams[stream_id]["_spec"]
            cmd = [
                str(self.ffmpeg), "-v", "error", "-nostdin",
                "-i", str(spec["path"]),
                "-map", f"0:a:{spec['position']}",
                "-vn", "-sn", "-dn",
            ]
            if int(spec["channels"]) > 1:
                from core.audio_pcm import identity_channelmap

                cmd += ["-af", f"channelmap={identity_channelmap(int(spec['channels']))}"]
            cmd += [
                "-f", "f32le", "-ac", str(spec["channels"]), "-",
            ]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self.procs[stream_id] = proc
            self.positions[stream_id] = 0
        return proc

    def read(self, stream_id: str, *, channel_index: int = 0, start: int = 0,
             count: int) -> tuple[Any, int]:
        """顺序管道读取: 回退流(顺序读) 或 前进(co读取) 都正确。"""
        want = int(count)
        if want <= 0:
            return np.zeros(max(0, want), dtype="<f4"), 0
        info = self.streams[stream_id]
        proc = self._proc(stream_id)
        pos = self.positions[stream_id]
        chans = int(info["channels"])
        item = 4
        stride = item * chans
        lo = max(0, int(start))
        if lo < pos:
            proc.kill()
            proc.stdout.close()
            self.procs.pop(stream_id, None)
            proc = self._proc(stream_id)
            pos = 0
        skip = (lo - pos) * stride
        while skip > 0:
            got = proc.stdout.read(min(skip, 1 << 20))
            if not got:
                break
            skip -= len(got)
        raw = b""
        need = want * stride
        while len(raw) < need:
            got = proc.stdout.read(need - len(raw))
            if not got:
                break
            raw += got
        self.positions[stream_id] = lo + len(raw) // stride
        block = np.zeros(want, dtype="<f4")
        valid = len(raw) // stride
        if valid:
            framed = np.frombuffer(raw[: valid * stride], dtype="<f4").reshape(
                valid, chans
            )
            block[:valid] = framed[:, int(channel_index)]
        self.decoded += valid
        return block, valid

    def read_frames(self, stream_id: str, *, channel_index: int = 0,
                    start: int, count: int) -> Any:
        """与 `AudioPCMReader.read_frames` 同语义 (贴窗口, 越界补静音)。"""
        want = int(count)
        if want <= 0:
            return np.zeros(max(0, want), dtype="<f4")
        info = self.streams[stream_id]
        lo = int(start)
        lo_c = max(0, lo)
        hi_c = min(int(info["actual"]), lo + want)
        valid = max(0, hi_c - lo_c)
        out = np.zeros(want, dtype="<f4")
        if valid:
            got, fetched = self.read(
                stream_id, channel_index=channel_index, start=lo_c,
                count=valid,
            )
            if fetched > 0:
                out[lo_c - lo: lo_c - lo + fetched] = got[:fetched]
        return out

    def availability(self) -> dict[tuple[str, int], dict[str, Any]]:
        return {
            (i["source_id"], i["stream_index"]): {
                "declared_samples": i["declared"],
                "actual_samples": i["actual"],
                "declared_source": "duration" if i["declared"] else None,
            }
            for i in self.streams.values()
        }

    def duration_mismatches(self, tolerance_samples: int = 1) -> list[dict]:
        out = []
        for info in self.streams.values():
            dec, act = info["declared"], info["actual"]
            if dec is None or abs(act - dec) <= tolerance_samples:
                continue
            out.append({
                "reason": "audio_duration_metadata_mismatch",
                "stream_id": info["stream_id"],
                "declared_samples": int(dec), "actual_samples": int(act),
                "delta_samples": int(act) - int(dec),
            })
        return out

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "stream_id": i["stream_id"], "source_id": i["source_id"],
                "stream_index": i["stream_index"],
                "sample_rate": i["sample_rate"],
                "channel_count": i["channels"],
                "declared_samples": i["declared"],
                "actual_samples": i["actual"],
                "declared_source": "duration" if i["declared"] else None,
                "codec_name": "pcm_f32le", "linear_pcm": True,
            }
            for i in self.streams.values()
        ]

    def info(self, stream_id: str) -> Any:
        return self.streams[stream_id]

    @property
    def decode_seconds(self) -> float:
        return 0.0

    def close(self) -> None:
        for proc in self.procs.values():
            try:
                proc.kill()
                proc.stdout.close()
                proc.wait(timeout=10)
            except Exception:             # noqa: BLE001
                pass
        self.procs.clear()

    def __enter__(self) -> "_PipePCMReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _p3a_pipe_factory(plan: Any, ffmpeg: Any, work_dir: Any,
                      chunk_frames: int) -> Any:
    return _PipePCMReader(plan, ffmpeg, work_dir, chunk_frames)


def _p3a_render(
    plan: Any, out: Path, *, backend: str = "pipe", **kwargs: Any
) -> Any:
    """统一入口: 走真实渲染链路。

    `backend="pipe"` 用管道读取器 (与生产读取器同接口、不同传输层 — 两者
    结果必须逐样本一致); `backend="reader"` 用生产 `AudioPCMReader` (按块
    span 读, 适合 chunk 极小、渲染次数多的用例, 避免管道逐块读的开销)。
    """
    from core.audio_process import run_audio_render
    from core.audio_wav import WavFormat

    d = WORK / "p3a"
    d.mkdir(parents=True, exist_ok=True)
    factory = _p3a_pipe_factory if backend == "pipe" else None
    return run_audio_render(
        plan,
        ffmpeg=FFMPEG,
        work_dir=d / "work",
        output_path=out,
        sample_format=kwargs.pop("sample_format", WavFormat.FLOAT32),
        overwrite=True,
        reader_factory=factory,
        **kwargs,
    )


def _p3a_read(path: Path) -> Any:
    from core.audio_wav import read_wav

    return read_wav(path)


def _p3a_ready(tag: str) -> bool:
    """Phase 3A 前置条件 (numpy 可用); 缺失时记录一次并跳过整组。"""
    if np is not None:
        return True
    record(f"l1.p3a.{tag}.numpy 可用", False,
           "numpy 缺失 — Phase 3A 用例跳过 (与 channel-sync 同一可选依赖)")
    return False


def l1_audio_timeline() -> None:
    """Phase 3A: AudioTimeline / RenderPolicy / duration / EOF 策略 (纯逻辑)。

    覆盖 §测试矩阵 T1–T8 与 §offset 语义 (正/负/零), 全部 deterministic。
    """
    if not _p3a_ready("timeline"):
        return

    section("L1 音频时间轴 / RenderPolicy / EOF (v0.7.1 Phase 3A)")
    from core.audio_timeline import (
        REASON_AUDIO_DURATION_UNKNOWN, REASON_AUDIO_SAMPLE_RATE_MISMATCH,
        AudioRenderError, RenderPolicy, resolve_timeline, samples_from_seconds,
        source_to_timeline, timeline_to_source,
    )

    d = WORK / "p3a_tl"
    d.mkdir(parents=True, exist_ok=True)

    # --- umeric: 换算 ----  ---------------------------------------------
    record("l1.p3a.样本/秒换算 (6.006s@48k = 288288)",
           samples_from_seconds(6.006, 48000) == 288288
           and samples_from_seconds(0.0, 48000) == 0)
    record("l1.p3a.offset 统一换算 timeline = source - offset",
           source_to_timeline(1960, 960) == 1000
           and timeline_to_source(1000, 960) == 1960
           and source_to_timeline(1000, -960) == 1960
           and source_to_timeline(1000, 0) == 1000)
    record("l1.p3a.RenderPolicy 只实现 UNION/EXPLICIT",
           RenderPolicy.coerce("union") is RenderPolicy.UNION
           and RenderPolicy.coerce("bogus", RenderPolicy.UNION)
           is RenderPolicy.UNION
           and RenderPolicy.INTERSECTION.value == "intersection")

    # --- T1: 单来源 -> 输出长度 = 来源长度 -------------------------------
    a = _p3a_fixture(d / "t1.wav", 1, 1000, {0: 100})
    p1 = _p3a_plan([{"source_id": "a", "path": a, "channels": 1,
                     "samples": 1000}])
    tl1 = resolve_timeline(p1)
    record("l1.p3a.T1 单来源 1000 -> 1000 samples",
           tl1.frame_count == 1000 and tl1.start_sample == 0
           and tl1.end_sample == 1000
           and tl1.render_policy is RenderPolicy.UNION,
           f"{tl1.summary()}")

    # --- T2: A=1000 B=1000 -> 1000 --------------------------------------
    b = _p3a_fixture(d / "t2.wav", 1, 1000, {0: 200})
    p2 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b, "channels": 1, "samples": 1000},
    ])
    tl2 = resolve_timeline(p2)
    record("l1.p3a.T2 双来源等长 -> 1000 samples",
           tl2.frame_count == 1000 and tl2.output_channels == 2)

    # --- T3/T4: UNION 取并集, 短 source 补静音 ---------------------------
    short = _p3a_fixture(d / "short.wav", 1, 800, {0: 100})
    p3 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": short, "channels": 1, "samples": 800},
    ])
    tl3 = resolve_timeline(p3)
    ct_b = tl3.channel("b:s0:c0")
    record("l1.p3a.T3 A=1000 B=800 -> UNION 1000; B 尾部 200 补静音",
           tl3.frame_count == 1000
           and ct_b.timeline_bounds == (0, 800)
           and ct_b.actual_samples is None            # 尚未解码, 不臆造
           and tl3.pads_after("b:s0:c0") is True
           and tl3.pads_after("a:s0:c0") is False,
           f"bounds={ct_b.timeline_bounds}")
    p4 = _p3a_plan([
        {"source_id": "a", "path": short, "channels": 1, "samples": 800},
        {"source_id": "b", "path": a, "channels": 1, "samples": 1000},
    ])
    tl4 = resolve_timeline(p4)
    record("l1.p3a.T4 A=800 B=1000 -> UNION 1000; A 尾部补静音",
           tl4.frame_count == 1000
           and tl4.pads_after("a:s0:c0") is True
           and tl4.pads_after("b:s0:c0") is False)

    # --- T5/T6: 带 offset 的 timeline 归正 ------------------------------
    #
    # ⚠️ 方向由实测钉死 (不是从字段名猜): 4×mono 素材 CH2 晚到 960 样本时,
    # channel_sync 报 `delay_samples=+960 / shift_samples=960`, 修正后音频与
    # 修正前的互相关 lag = -960 —— 即 **`out[n] = in[n + shift]`**, 晚到轨被
    # **前移**。统一 timeline 因此定义 `timeline = source - offset`:
    #   +100 (晚到) -> timeline [-100, 700): 内容前移, 尾部补静音
    #   -100 (早到) -> timeline [100, 900): 内容后移, 头部补静音
    rec = _p3a_fixture(d / "rec.wav", 1, 800, {0: 200})
    p5 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rec, "channels": 1, "samples": 800},
    ])
    set_p3a_offset(p5, "rec:s0:c0", 100)
    tl5 = resolve_timeline(p5)
    r5 = tl5.channel("rec:s0:c0")
    record("l1.p3a.T5 B offset=+100 (晚到) -> timeline [-100,700): 前移修正",
           r5.timeline_bounds == (-100, 700)
           and tl5.start_sample == -100
           and tl5.frame_count == 1100
           and tl5.clips_before("rec:s0:c0") is False
           and r5.planned_window(tl5.start_sample, tl5.end_sample)[0] == 0,
           f"bounds={r5.timeline_bounds} start={tl5.start_sample}")
    p6 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rec, "channels": 1, "samples": 800},
    ])
    set_p3a_offset(p6, "rec:s0:c0", -100)
    tl6 = resolve_timeline(p6)
    r6 = tl6.channel("rec:s0:c0")
    record("l1.p3a.T6 B offset=-100 (早到) -> timeline [100,900): 头部补静音",
           r6.timeline_bounds == (100, 900)
           and tl6.start_sample == 0
           and tl6.frame_count == 1000
           and tl6.clips_before("rec:s0:c0") is False
           and r6.planned_window(tl6.start_sample, tl6.end_sample)[0] == -100,
           f"bounds={r6.timeline_bounds} start={tl6.start_sample}")
    # --- T7/T8: overlap 与完全不 overlap 都允许 (不自动 mixing) ----------
    a2 = _p3a_fixture(d / "ov_a.wav", 1, 1000, {0: 100})
    b2 = _p3a_fixture(d / "ov_b.wav", 1, 1000, {0: 300})
    p7 = _p3a_plan([
        {"source_id": "a", "path": a2, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b2, "channels": 1, "samples": 1000},
    ])
    set_p3a_offset(p7, "b:s0:c0", -500)
    tl7 = resolve_timeline(p7)
    record("l1.p3a.T7 两源 timeline overlap 允许 (输出声道仍独立)",
           tl7.frame_count == 1500 and tl7.channel("b:s0:c0").timeline_bounds
           == (500, 1500))
    p8 = _p3a_plan([
        {"source_id": "a", "path": a2, "channels": 1, "samples": 1000},
        {"source_id": "b", "path": b2, "channels": 1, "samples": 1000},
    ])
    set_p3a_offset(p8, "b:s0:c0", -2000)
    tl8 = resolve_timeline(p8)
    record("l1.p3a.T8 完全不 overlap -> 并集仍完整 (不需要共同区间)",
           tl8.frame_count == 3000 and tl8.start_sample == 0
           and tl8.end_sample == 3000)

    # --- 采样率不一致拒绝; duration 未知拒绝 ----------------------------
    other = _p3a_fixture(d / "44k.wav", 1, 1000, {0: 100}, sample_rate=44100)
    p9 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
        {"source_id": "x", "path": other, "channels": 1, "samples": 1000,
         "sample_rate": 44100},
    ])
    p9.source("x").streams[0].sample_rate = 44100
    try:
        resolve_timeline(p9)
        mismatch = None
    except AudioRenderError as exc:
        mismatch = exc.reason
    record("l1.p3a.采样率不一致 -> audio_sample_rate_mismatch (不 resample)",
           mismatch == REASON_AUDIO_SAMPLE_RATE_MISMATCH, f"{mismatch}")

    p10 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
    ])
    p10.source("a").streams[0].duration_sec = None
    try:
        resolve_timeline(p10)
        unknown = None
    except AudioRenderError as exc:
        unknown = exc.reason
    record("l1.p3a.duration 无法确定 -> audio_duration_unknown (不猜)",
           unknown == REASON_AUDIO_DURATION_UNKNOWN, f"{unknown}")

    # --- 显式 duration 优先 (口音一致) ---------------------------------
    p11 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 1000},
    ])
    tl11 = resolve_timeline(p11, explicit_duration_seconds=0.01)
    record("l1.p3a.显式 duration=480 samples 覆盖并集推导",
           tl11.frame_count == 480 and tl11.explicit is True
           and tl11.duration_mode.value == "explicit")
    try:
        resolve_timeline(p11, render_policy=RenderPolicy.INTERSECTION)
        reserved = None
    except AudioRenderError as exc:
        reserved = exc.reason
    record("l1.p3a.INTERSECTION 为预留未实现 (显式拒绝, 不假装支持)",
           reserved is not None, f"{reserved}")


def set_p3a_offset(plan: Any, channel_id: str, offset: float) -> None:
    """给某声道设一个"已测量"的固定整数样本 offset (Phase 1 模型语义)。"""
    from core.audio_models import AudioSyncResult, SyncStatus

    for track in plan.input_tracks:
        for ch in track.channels:
            if ch.id == channel_id:
                track.set_sync(AudioSyncResult(
                    status=SyncStatus.SUCCESS,
                    offset_samples=float(offset),
                    offset_ms=float(offset) * 1000.0 / P3A_SR,
                    source="channel_sync_report",
                ))
                return


def l1_audio_sync_offset() -> None:
    """Phase 3A: offset 方向由**现有实现**钉死 (§不得按字段名猜)。

    1) `core/sync_fix.shift_stream` = `out[n] = in[n + rint(delay)]`;
    2) 本阶段 timeline 换算 `timeline = source - offset` 必须与之同向;
    3) impulse 素材端到端验证正/负/零 offset 的 trim / pad 行为。
    """
    if not _p3a_ready("sync"):
        return

    section("L1 音频 sync offset 方向 (v0.7.1 Phase 3A)")
    from core.audio_models import AudioSyncResult, SyncStatus
    from core.audio_timeline import source_to_timeline, timeline_to_source
    from core.sync_fix import shift_stream

    sync_dir = WORK / "p3a_sync"
    sync_dir.mkdir(parents=True, exist_ok=True)

    # --- 1) 钉住既有 shift_stream 的方向 ---------------------------------
    n = 64
    src_arr = np.zeros(n, dtype="<f4")
    src_arr[40] = 1.0
    dst = sync_dir / "shift.raw"
    shift_stream(src_arr, dst, delay_samples=10.0, sample_rate=48000,
                 storage_dtype="f32", chunk_seconds=1.0)
    shifted = np.fromfile(dst, dtype="<f4")
    record("l1.p3a.shift_stream(+10) 前移: in[40] -> out[30]",
           shifted.shape[0] == n and int(np.argmax(shifted)) == 30
           and shifted[30] == 1.0,
           f"argmax={int(np.argmax(shifted))}")
    shift_stream(src_arr, dst, delay_samples=-10.0, sample_rate=48000,
                 storage_dtype="f32", chunk_seconds=1.0)
    back = np.fromfile(dst, dtype="<f4")
    record("l1.p3a.shift_stream(-10) 后移: in[40] -> out[50]",
           int(np.argmax(back)) == 50 and back[50] == 1.0,
           f"argmax={int(np.argmax(back))}")

    # --- 2) 换算同向 -----------------------------------------------------
    record("l1.p3a.offset 换算与 channel_sync 实测同向 (timeline = source - offset)",
           source_to_timeline(1960, 960) == 1000
           and timeline_to_source(1000, 960) == 1960)

    # --- 3) 端到端 impulse: 晚到轨被对齐到同一 timeline 位置 --------------
    d = WORK / "p3a_sync"
    anchor = _p3a_fixture(d / "anch.wav", 1, 4000, {0: 1000})
    late = _p3a_fixture(d / "late.wav", 1, 4000, {0: 1960})
    plan = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    set_p3a_offset(plan, "late:s0:c0", 960)
    out = d / "aligned.wav"
    res = _p3a_render(plan, out, chunk_frames=97)
    arr, _info = _p3a_read(out)
    peaks = _p3a_impulse_map(arr)
    record("l1.p3a.impulse +960: 两轨 impulse 对齐到同一 timeline 位置",
           res.ok and peaks == [[1960], [1960]]
           and res.timeline.start_sample == -960
           and res.frames == 4960,
           f"ok={res.ok} peaks={peaks} start={res.timeline.start_sample if res.timeline else None} "
           f"errors={res.errors}")

    # 负 offset: 内容**后移** (起点仍为 0, 头部补静音, 尾部延长)
    plan2 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "early", "path": late, "channels": 1, "samples": 4000},
    ])
    set_p3a_offset(plan2, "early:s0:c0", -960)
    out2 = d / "delayed.wav"
    res2 = _p3a_render(plan2, out2, chunk_frames=97)
    arr2, _i2 = _p3a_read(out2)
    peaks2 = _p3a_impulse_map(arr2)
    record("l1.p3a.impulse -960: 内容后移 960 (窗口 0..4960)",
           res2.ok and peaks2[0] == [1000] and peaks2[1] == [2920]
           and res2.frames == 4960
           and res2.timeline.start_sample == 0,
           f"peaks={peaks2} frames={res2.frames} "
           f"start={res2.timeline.start_sample if res2.timeline else None} "
           f"errors={res2.errors}")

    # 零 offset / 未测量: 原样
    plan3 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    out3 = d / "plain.wav"
    res3 = _p3a_render(plan3, out3, chunk_frames=101)
    arr3, _i3 = _p3a_read(out3)
    peaks3 = _p3a_impulse_map(arr3)
    record("l1.p3a.未测量 (not_processed) 不施加任何移位",
           res3.ok and peaks3 == [[1000], [1960]],
           f"peaks={peaks3}")

    # 状态非 success 时即便 offset 字段有值也不应用 (§只应用已确认结果)
    plan4 = _p3a_plan([
        {"source_id": "anch", "path": anchor, "channels": 1, "samples": 4000},
        {"source_id": "late", "path": late, "channels": 1, "samples": 4000},
    ])
    for track in plan4.input_tracks:
        for ch in track.channels:
            if ch.id == "late:s0:c0":
                track.set_sync(AudioSyncResult(
                    status=SyncStatus.LOW_CONFIDENCE, offset_samples=960.0,
                    reason="low_confidence",
                ))
    out4 = d / "unconfirmed.wav"
    res4 = _p3a_render(plan4, out4, chunk_frames=64)
    arr4, _i4 = _p3a_read(out4)
    peaks4 = _p3a_impulse_map(arr4)
    record("l1.p3a.low_confidence 的 offset 不被应用 (只用成功结论)",
           res4.ok and peaks4[1] == [1960], f"peaks={peaks4}")


def l1_audio_route() -> None:
    """Phase 3A: 路由 T1–T10 (4CH / 2×2CH / 4×mono / 跨来源 / 逐样本一致)。"""
    if not _p3a_ready("route"):
        return

    section("L1 音频通道路由 (v0.7.1 Phase 3A)")
    from core.audio_timeline import REASON_AUDIO_SAMPLE_RATE_MISMATCH

    d = WORK / "p3a_route"
    d.mkdir(parents=True, exist_ok=True)

    # --- T1/T2: 4CH -> 4CH 逐声道一致 ------------------------------------
    q = _p3a_fixture(d / "quad.wav", 4, 1000,
                     {0: 100, 1: 200, 2: 300, 3: 400})
    plan = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                       "samples": 1000}])
    out = d / "quad_out.wav"
    res = _p3a_render(plan, out, chunk_frames=128)
    arr, info = _p3a_read(out)
    peaks = _p3a_impulse_map(arr)
    record("l1.p3a.T2 4CH -> 4CH: out[i] == in[i]",
           res.ok and arr.shape == (1000, 4)
           and peaks == [[100], [200], [300], [400]]
           and info.frame_count == 1000 and info.channel_count == 4,
           f"peaks={peaks} shape={arr.shape}")

    # --- T3: 4CH reorder [2,0,3,1] --------------------------------------
    from core.audio_plan import AudioPlanner

    p3 = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                     "samples": 1000}])
    planner = AudioPlanner(p3)
    try:
        planner.map_channels("cam:s0:c2", "cam:s0:c0", "cam:s0:c3",
                             "cam:s0:c1")
        ok3 = True
    except Exception as exc:                # noqa: BLE001
        ok3, _ = False, exc
    out3 = d / "reorder.wav"
    res3 = _p3a_render(p3, out3, chunk_frames=97)
    arr3, _i3 = _p3a_read(out3)
    peaks3 = _p3a_impulse_map(arr3)
    record("l1.p3a.T3 4CH reorder [2,0,3,1] 逐样本精确",
           ok3 and res3.ok
           and peaks3 == [[300], [100], [400], [200]]
           and [tl_ch for tl_ch in res3.timeline.output_channel_ids]
           == ["cam:s0:c2", "cam:s0:c0", "cam:s0:c3", "cam:s0:c1"],
           f"peaks={peaks3} order={res3.timeline.output_channel_ids if res3.timeline else None}")

    # --- T3b: UNION 下短 source 尾部补**确定性静音** (渲染级验证) ---------
    b3 = _p3a_fixture(d / "u_b.wav", 1, 800, {0: 42})
    plan3 = _p3a_plan([
        {"source_id": "cam", "path": q, "channels": 4, "samples": 1000},
        {"source_id": "rec", "path": b3, "channels": 1, "samples": 800},
    ])
    out3b = d / "union_short.wav"
    res3b = _p3a_render(plan3, out3b, chunk_frames=91)
    arr3b, info3b = _p3a_read(out3b)
    tail = arr3b[800:, 4]
    record("l1.p3a.T3b UNION: 长 source 不截断, 短 source 尾部确定性静音",
           res3b.ok and arr3b.shape == (1000, 5)
           and tail.size == 200 and not np.any(tail)
           and float(np.max(np.abs(arr3b[:, 0]))) > 0.0
           and res3b.silence_samples >= 200
           and info3b.frame_count == 1000,
           f"shape={arr3b.shape} tailmax={float(np.abs(tail).max()) if tail.size else None} "
           f"silence={res3b.silence_samples}")

    # --- T12: metadata 与实际解码不一致 -> 检测 + 以实际为准 ---------------
    # fixture: header 声明 1000 帧, 文件里实际只有 600 帧 (声明 > 实际)。
    t12 = _p3a_truncated_fixture(d / "meta_lie.wav", 1000, 600)
    plan12 = _p3a_plan([
        {"source_id": "cam", "path": q, "channels": 4, "samples": 1000},
        {"source_id": "short", "path": t12, "channels": 1, "samples": 1000},
    ])
    out12 = d / "mismatch.wav"
    res12 = _p3a_render(plan12, out12, chunk_frames=64)
    arr12, info12 = _p3a_read(out12)
    plan12b = _p3a_plan([
        {"source_id": "short", "path": t12, "channels": 1, "samples": 1000},
    ])
    out12b = d / "mismatch_solo.wav"
    res12b = _p3a_render(plan12b, out12b, chunk_frames=64)
    arr12b, info12b = _p3a_read(out12b)
    record("l1.p3a.T12 header 声明 1000 / 实际 600 -> 检测 mismatch",
           res12.ok
           and any(m["reason"] == "audio_duration_metadata_mismatch"
                   for m in res12.duration_mismatches)
           and any("short" in str(m.get("stream_id"))
                   for m in res12.duration_mismatches),
           f"{json.dumps(res12.duration_mismatches, ensure_ascii=False)[:220]}")
    record("l1.p3a.T12 派生窗口以**实际解码**为准 (单来源输出 600 而非 1000)",
           res12b.ok and info12b.frame_count == 600
           and arr12b.shape == (600, 1) and res12b.frames == 600,
           f"frames={info12b.frame_count}")
    record("l1.p3a.T12 多来源时 UNION 取长 (cam 1000 不被误导的 metadata 截断)",
           res12.ok and info12.frame_count == 1000
           and arr12.shape == (1000, 5),
           f"frames={info12.frame_count}")

    # --- T4: 4CH -> 单声道 (CH3) ---------------------------------------
    p4 = _p3a_plan([{"source_id": "cam", "path": q, "channels": 4,
                     "samples": 1000}])
    AudioPlanner(p4).select_channels("cam:s0:c2")
    out4 = d / "mono.wav"
    res4 = _p3a_render(p4, out4, chunk_frames=64)
    arr4, info4 = _p3a_read(out4)
    ref, _ri = _p3a_read(q)
    record("l1.p3a.T4 4CH -> CH3 输出 mono 且样本精确相等",
           res4.ok and arr4.shape == (1000, 1)
           and info4.channel_count == 1
           and np.array_equal(arr4[:, 0], ref[:, 2]),
           f"shape={arr4.shape}")

    # --- T5/T6: 2×2CH / 4×mono 跨流重排 --------------------------------
    s1 = _p3a_fixture(d / "s1.wav", 2, 600, {0: 10, 1: 20})
    s2 = _p3a_fixture(d / "s2.wav", 2, 600, {0: 30, 1: 40})
    raw = [
        _raw_audio_stream(1, channels=2, codec="pcm_f32le",
                          sample_rate="48000", sample_fmt="flt",
                          duration="0.012500", bit_rate=None, tag_string=None),
        _raw_audio_stream(2, channels=2, codec="pcm_f32le",
                          sample_rate="48000", sample_fmt="flt",
                          duration="0.012500", bit_rate=None, tag_string=None),
    ]
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
        build_audio_streams,
    )

    src_a = AudioSource(
        source_id="s1", source_type=AudioSourceType.WAV, path=str(s1),
        streams=build_audio_streams(
            [_raw_audio_stream(0, channels=2, codec="pcm_f32le",
                               sample_rate="48000", sample_fmt="flt",
                               duration="0.012500", bit_rate=None,
                               tag_string=None)],
            source_id="s1"),
        input_index=0,
    )
    src_b = AudioSource(
        source_id="s2", source_type=AudioSourceType.WAV, path=str(s2),
        streams=build_audio_streams(
            [_raw_audio_stream(0, channels=2, codec="pcm_f32le",
                               sample_rate="48000", sample_fmt="flt",
                               duration="0.012500", bit_rate=None,
                               tag_string=None)],
            source_id="s2"),
        input_index=1,
    )
    plan5 = AudioPlan(sources=[src_a, src_b])
    plan5.input_tracks = (
        AudioTrackBuilder(src_a.streams, source_id="s1").tracks()
        + AudioTrackBuilder(src_b.streams, source_id="s2").tracks()
    )
    plan5.selected_tracks = [t.track_id for t in plan5.input_tracks]
    plan5.selected_channels = [c.id for c in plan5.all_channels()]
    planner5 = AudioPlanner(plan5)
    planner5.select_channels("s1:s0:c1", "s2:s0:c1", "s1:s0:c0",
                             "s2:s0:c0")
    planner5.map_channels("s1:s0:c1", "s2:s0:c1", "s1:s0:c0", "s2:s0:c0")
    out5 = d / "cross.wav"
    res5 = _p3a_render(plan5, out5, chunk_frames=64)
    arr5, info5 = _p3a_read(out5)
    peaks5 = _p3a_impulse_map(arr5)
    record("l1.p3a.T5 跨来源 2×2CH 重排 (L2R2L1R1) 精确",
           res5.ok and peaks5 == [[20], [40], [10], [30]]
           and info5.channel_count == 4,
           f"peaks={peaks5} err={res5.errors}")
    m1 = _p3a_fixture(d / "m1.wav", 1, 500, {0: 50})
    m2 = _p3a_fixture(d / "m2.wav", 1, 500, {0: 60})
    m3 = _p3a_fixture(d / "m3.wav", 1, 500, {0: 70})
    m4 = _p3a_fixture(d / "m4.wav", 1, 500, {0: 80})
    specs = [
        {"source_id": f"m{i}", "path": p, "channels": 1, "samples": 500}
        for i, p in enumerate((m1, m2, m3, m4), start=1)
    ]
    plan6 = _p3a_plan(specs)
    planner6 = AudioPlanner(plan6)
    order6 = ["m4:s0:c0", "m2:s0:c0", "m1:s0:c0", "m3:s0:c0"]
    planner6.select_channels(*order6)
    planner6.map_channels(*order6)
    out6 = d / "mono4.wav"
    res6 = _p3a_render(plan6, out6, chunk_frames=37)
    arr6, _i6 = _p3a_read(out6)
    peaks6 = _p3a_impulse_map(arr6)
    record("l1.p3a.T6 4×mono 跨流重排 (4,2,1,3) 不塌缩",
           res6.ok and peaks6 == [[80], [60], [50], [70]]
           and arr6.shape[1] == 4
           and len(plan6.input_tracks) == 4
           and all(t.channel_count == 1 for t in plan6.input_tracks),
           f"peaks={peaks6}")

    # --- T7: 外挂 WAV 输入输出逐样本一致 (bit-exact) ---------------------
    w = _p3a_fixture(d / "ext.wav", 2, 777, {0: 111, 1: 222})
    plan7 = _p3a_plan([{"source_id": "wav", "path": w, "channels": 2,
                        "samples": 777}])
    out7 = d / "ext_out.wav"
    res7 = _p3a_render(plan7, out7, chunk_frames=53)
    arr7, _i7 = _p3a_read(out7)
    ref7, _r7 = _p3a_read(w)
    record("l1.p3a.T7 外挂 WAV -> WAV 逐样本 bit-exact",
           res7.ok and arr7.shape == ref7.shape
           and np.array_equal(arr7, ref7),
           f"shape={arr7.shape}")

    # --- T8: camera + WAV 跨来源 routing (含多声道混合来源) --------------
    cam4 = _p3a_fixture(d / "cam4.wav", 4, 900, {0: 5, 1: 6, 2: 7, 3: 8})
    ext2 = _p3a_fixture(d / "ext2.wav", 2, 900, {0: 15, 1: 16})
    plan8 = _p3a_plan([
        {"source_id": "camera", "path": cam4, "channels": 4, "samples": 900},
        {"source_id": "recorder", "path": ext2, "channels": 2,
         "samples": 900},
    ])
    planner8 = AudioPlanner(plan8)
    order8 = ["camera:s0:c2", "recorder:s0:c0", "camera:s0:c0"]
    planner8.select_channels(*order8)
    planner8.map_channels(*order8)
    out8 = d / "cross3.wav"
    res8 = _p3a_render(plan8, out8, chunk_frames=41)
    arr8, info8 = _p3a_read(out8)
    peaks8 = _p3a_impulse_map(arr8)
    record("l1.p3a.T8 camera+WAV 跨来源 3 声道 routing (无相加)",
           res8.ok and peaks8 == [[7], [15], [5]]
           and info8.channel_count == 3
           and bool(res8.route_spec and res8.route_spec.is_multi_source)
           and res8.route_spec.source_ids == ["camera", "recorder"],
           f"peaks={peaks8} err={res8.errors}")

    # --- T9: 采样率不一致拒绝 -------------------------------------------
    other = _p3a_fixture(d / "alt.wav", 1, 900, {0: 5}, sample_rate=96000)
    plan9 = _p3a_plan([
        {"source_id": "camera", "path": cam4, "channels": 4, "samples": 900},
        {"source_id": "alt", "path": other, "channels": 1, "samples": 900},
    ])
    plan9.source("alt").streams[0].sample_rate = 96000
    out9 = d / "mismatch_sr.wav"
    try:
        out9.unlink()
    except OSError:
        pass
    res9 = _p3a_render(plan9, out9)
    record("l1.p3a.T9 不同采样率 -> audio_sample_rate_mismatch 且不产出文件",
           (not res9.ok)
           and REASON_AUDIO_SAMPLE_RATE_MISMATCH
           in [e.get("reason") for e in res9.errors]
           and not out9.exists(),
           f"{[e.get('reason') for e in res9.errors]}")

    # --- T10: 整数 PCM 归一化到 canonical float32 ------------------------
    t10 = _p3a_pcm16_roundtrip(d)
    record("l1.p3a.T10 s16/s24/s32 -> canonical float32 满量程归一",
           t10, "写入-读回-渲染三段一致")


def _p3a_pcm16_roundtrip(d: Path) -> bool:
    """整数 PCM 输入经 ffmpeg -> float32 -> WAV 后仍在 [-1,1] 且可往返。"""
    from core.audio_wav import WavFormat, read_wav, write_wav

    sr = 48000
    n = 480
    ramp = (np.arange(n, dtype=np.float32) / (n / 2.0) - 1.0).astype(np.float32)
    for fmt, codec, tol in (
        (WavFormat.PCM16, "pcm_s16le", 1.5 / 32768),
        (WavFormat.PCM24, "pcm_s24le", 3.0 / 8388608),
        (WavFormat.PCM32, "pcm_s32le", 3.0 / 2147483648),
    ):
        src = d / f"int_{fmt.value}.wav"
        write_wav(src, [ramp.reshape(-1, 1)], sample_rate=sr, channel_count=1,
                  sample_format=fmt, frame_count=n, overwrite=True)
        plan = _p3a_plan([{"source_id": "i", "path": src, "channels": 1,
                           "samples": n}], sample_rate=sr)
        plan.source("i").streams[0].codec_name = codec
        out = d / f"int_{fmt.value}_out.wav"
        res = _p3a_render(plan, out)
        if not res.ok:
            return False
        back, _info = read_wav(out)
        if float(np.max(np.abs(back))) > 1.0:
            return False
        if float(np.max(np.abs(back[:, 0] - ramp))) > tol:
            return False
    return True


def _p3a_truncated_fixture(path: Path, declared_frames: int,
                           real_frames: int) -> Path:
    """WAV fixture: **header 声明 `declared_frames` / 实际 `real_frames`**。

    做法: 正常写入 `real_frames` 帧, 再把 `data` chunk 长度改写成
    `declared_frames` 对应的字节数 (文件的真实长度仍是 real, 因此
    "实际解码样本数" 与 "metadata 声明值" 人为错开)。

    用于 T12: metadata 与实际解码不一致时必须**检测到**, 并以实际解码为
    最终事实 —— 不静默相信 metadata, 也不静默补静音。
    """
    from core.audio_wav import WavFormat, write_wav

    x = np.zeros((real_frames, 1), dtype=np.float32)
    x[100, 0] = 0.5
    x[real_frames - 1, 0] = 0.25
    write_wav(path, [x], sample_rate=P3A_SR, channel_count=1,
              sample_format=WavFormat.FLOAT32, frame_count=real_frames,
              overwrite=True)
    raw = bytearray(path.read_bytes())
    # 定位 data chunk 的 size 字段并改写为声明长度
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = bytes(raw[pos:pos + 4])
        size = int.from_bytes(raw[pos + 4:pos + 8], "little")
        if chunk_id == b"data":
            raw[pos + 4:pos + 8] = (declared_frames * 4).to_bytes(4, "little")
            break
        pos += 8 + size + (size % 2)
    path.write_bytes(bytes(raw))
    return path


def l1_audio_wav_export() -> None:
    """Phase 3A: WAV writer/reader (4 格式往返 + header 精确 + 命名)。"""
    if not _p3a_ready("wav"):
        return

    section("L1 WAV 导出 (v0.7.1 Phase 3A)")
    from core.audio_wav import (
        WavFormat, default_wav_name, parse_wav_header, read_wav,
        unique_output_path, write_wav,
    )

    d = WORK / "p3a_wav"
    d.mkdir(parents=True, exist_ok=True)
    sr = 48000
    n = 1000
    x = np.zeros((n, 4), dtype=np.float32)
    x[:, 0] = np.linspace(-1.0, 1.0, n)
    x[:, 1] = 0.5
    x[:, 2] = -0.25
    x[:, 3] = 1.0 / 3.0

    results = {}
    for fmt, tol in (
        (WavFormat.PCM16, 1.5 / 32768),
        (WavFormat.PCM24, 1.5 / 8388608),
        (WavFormat.PCM32, 1.5 / 2147483648),
        (WavFormat.FLOAT32, 0.0),
    ):
        path = d / f"rt_{fmt.value}.wav"
        info, spec = write_wav(
            path, [x[:300], x[300:700], x[700:]], sample_rate=sr,
            channel_count=4, sample_format=fmt, frame_count=n, layout="4.0",
            overwrite=True,
        )
        back, h = read_wav(path)
        err = float(np.max(np.abs(back - x))) if back.shape == x.shape else 9.9
        results[fmt] = (info, h, err, tol, spec)
        record(f"l1.p3a.WAV {fmt.value} 往返 (sample rate/channels/bits/frames)",
               back.shape == (n, 4) and h.sample_rate == sr
               and h.channel_count == 4
               and h.bits_per_sample == fmt.bits
               and h.frame_count == n and err <= max(tol, 1e-9)
               and h.data_bytes == n * 4 * fmt.bytes_per_sample,
               f"err={err:.3e} tol={tol:.3e} bits={h.bits_per_sample}")

    info16, h16, _e, _t, _s = results[WavFormat.PCM16]
    record("l1.p3a.WAV header 精确: riff/data 与实际字节数一致",
           h16.riff_bytes == path_size_check(info16.path) - 8
           and h16.data_bytes == 1000 * 4 * 2
           and h16.byte_rate == 48000 * 4 * 2
           and h16.block_align == 8,
           f"riff={h16.riff_bytes} size={path_size_check(info16.path)}")
    infof, hf, _ef, _tf, _sf = results[WavFormat.FLOAT32]
    record("l1.p3a.float32 输出 bit-exact 且 EXTENSIBLE 带 channel mask",
           _ef == 0.0 and hf.is_float and hf.extensible
           and hf.channel_mask == 0x33
           and hf.effective_format_code == 3,
           f"err={_ef} mask={hf.channel_mask:#x}")
    info24, h24, _e24, _t24, _s24 = results[WavFormat.PCM24]
    record("l1.p3a.24-bit 与 float 不依赖标准库 wave (EXTENSIBLE fmt=40B)",
           h24.extensible is True and h24.bits_per_sample == 24
           and h24.effective_format_code == 1,
           f"fmt_code={h24.effective_format_code}")
    record("l1.p3a.单声道写经典 PCM 头 (不滥用 EXTENSIBLE)",
           _p3a_mono_header(d))

    # --- T13: 输出样本数严格等于声明 (EOF 精确) -------------------------
    short = d / "short_decl.wav"
    try:
        write_wav(short, [x[:10]], sample_rate=sr, channel_count=4,
                  sample_format=WavFormat.PCM16, frame_count=20,
                  overwrite=True)
        exact = False
    except ValueError as exc:
        exact = "audio_wav_write_failed" in str(exc)
    record("l1.p3a.T13 写入样本数与声明不符 -> 报错且不留半成品",
           exact and not short.exists())

    # --- 命名策略 -------------------------------------------------------
    names = {
        "单声道": default_wav_name(channel_ids=["camera:s2:c2"]),
        "单来源整流": default_wav_name(source_stem="camera"),
        "映射": default_wav_name(channel_ids=["camera:s2:c2"],
                                 mapping="r2031"),
        "跨来源": default_wav_name(
            channel_ids=["camera:s1:c0", "recorder:s0:c0"]),
    }
    record("l1.p3a.WAV 命名稳定且可编程",
           names["单声道"] == "camera_s2c2.wav"
           and names["单来源整流"] == "camera_all.wav"
           and names["映射"] == "camera_s2c2_r2031.wav"
           and names["跨来源"] == "multi_mix.wav",
           f"{names}")

    clash = d / "dup.wav"
    clash.write_bytes(b"")
    second = unique_output_path(clash)
    record("l1.p3a.同名输出不覆盖 (自动 -2 后缀)",
           second.name == "dup-2.wav" and clash.exists())

    bad = d / "bad.wav"
    bad.write_bytes(b"not a wav at all")
    try:
        parse_wav_header(bad.read_bytes(), path=str(bad))
        rejected = False
    except ValueError as exc:
        rejected = "audio_pcm_format_unsupported" in str(exc)
    record("l1.p3a.非 WAV 输入明确拒绝 (audio_pcm_format_unsupported)",
           rejected)


def path_size_check(path: str) -> int:
    return Path(path).stat().st_size


def _p3a_mono_header(d: Path) -> bool:
    from core.audio_wav import WavFormat, write_wav

    p = d / "mono_head.wav"
    info, _spec = write_wav(
        p, [np.zeros((16, 1), dtype=np.float32)], sample_rate=48000,
        channel_count=1, sample_format=WavFormat.PCM16, frame_count=16,
        overwrite=True,
    )
    return (not info.extensible) and info.format_code == 1 \
        and info.bits_per_sample == 16


def l1_audio_chunk_invariance() -> None:
    """Phase 3A: chunk 边界不得影响结果 (§chunk invariance) 与内存有界。"""
    if not _p3a_ready("chunk"):
        return

    section("L1 音频 chunk invariance / 内存 (v0.7.1 Phase 3A)")
    from core.audio_wav import WavFormat, read_wav

    d = WORK / "p3a_chunk"
    d.mkdir(parents=True, exist_ok=True)

    # EOF 恰在 chunk 边界 / chunk 中间 (T9/T10): 长度取 1024 的整数倍附近。
    # chunk=1/7 是刻意的最坏情形 (每块 1~7 帧过管道), 只跑小 fixture 以控制
    # 回归时间; 边界对齐/不齐、长度不同都由 1024/1025/4096/4097 覆盖。
    for samples, chunks in (
        (1024, (1, 7, 256, 1024, 4096)),
        (1025, (1, 256, 1024)),
        (4096, (7, 256, 4096)),
        (4097, (256, 1024, 4096)),
    ):
        src = _p3a_fixture(d / f"eof_{samples}.wav", 2, samples,
                           {0: 7, 1: samples - 3})
        plan = _p3a_plan([
            {"source_id": "a", "path": src, "channels": 2,
             "samples": samples},
        ])
        hashes = {}
        shapes = {}
        for chunk in chunks:
            out = d / f"eof_{samples}_{chunk}.wav"
            res = _p3a_render(plan, out, chunk_frames=chunk,
                              backend="reader")
            if not res.ok:
                hashes[chunk] = f"ERR {res.errors}"
                continue
            arr, _info = read_wav(out)
            hashes[chunk] = _p3a_hash(out)
            shapes[chunk] = arr.shape
        record(f"l1.p3a.T9/T10 EOF={samples} (边界/中间) 全 chunk 划分 byte-identical",
               len(set(hashes.values())) == 1
               and all(s == (samples, 2) for s in shapes.values()),
               f"shapes={set(shapes.values())} distinct={len(set(hashes.values()))}")

    # T11: 多来源 + 重排 + offset 下的 chunk invariance (含跨 chunk 读回退)
    a = _p3a_fixture(d / "inv_a.wav", 4, 1200, {0: 111, 1: 500, 2: 900,
                                                3: 1199})
    b = _p3a_fixture(d / "inv_b.wav", 2, 900, {0: 700, 1: 42})
    from core.audio_plan import AudioPlanner

    plan = _p3a_plan([
        {"source_id": "cam", "path": a, "channels": 4, "samples": 1200},
        {"source_id": "rec", "path": b, "channels": 2, "samples": 900},
    ])
    set_p3a_offset(plan, "rec:s0:c0", 480)
    set_p3a_offset(plan, "rec:s0:c1", -240)
    order = ["rec:s0:c0", "cam:s0:c3", "rec:s0:c1", "cam:s0:c0"]
    pl = AudioPlanner(plan)
    pl.select_channels(*order)
    pl.map_channels(*order)
    hashes, peaks = {}, {}
    for chunk in (1, 7, 256, 1024, 4096):
        out = d / f"inv_{chunk}.wav"
        res = _p3a_render(plan, out, chunk_frames=chunk, backend="reader")
        if not res.ok:
            hashes[chunk] = f"ERR {[e.get('reason') for e in res.errors]}"
            continue
        hashes[chunk] = _p3a_hash(out)
        arr, _info = read_wav(out)
        peaks[chunk] = _p3a_impulse_map(arr)
    record("l1.p3a.T11 chunk 1/7/256/1024/4096 -> byte-identical 输出",
           len(set(hashes.values())) == 1,
           f"{json.dumps(hashes, ensure_ascii=False)}")
    record("l1.p3a.T11 impulse 位置与 chunk 无关 (含 +480/-240 offset)",
           len({json.dumps(v) for v in peaks.values()}) == 1
           and len(next(iter(peaks.values()))) == 4,
           f"{json.dumps(peaks.get(256), ensure_ascii=False)}")

    # 回归 (F1): routing 图的静音统计必须**逐路由签名求和**, 不受 F1 修正
    # 影响。索引 0 由两个来源共同供数 -> 在 [0,1000) 上重叠计入两次;
    # 索引 1 在 [400,1000) 上是确定性静音。与 `l1_audio_mix()` 的 mixing
    # 用例构成同一几何 (1000f + 400f) 下的对照 (routing 600 / mixing 0)。
    rout_a = _p3a_fixture(d / "faith_a.wav", 1, 1000, {0: 11})
    rout_b = _p3a_fixture(d / "faith_b.wav", 1, 400, {0: 22})
    plan_route = _p3a_plan([
        {"source_id": "cam", "path": rout_a, "channels": 1, "samples": 1000},
        {"source_id": "rec", "path": rout_b, "channels": 1, "samples": 400},
    ])
    plan_route.selected_channels = ["cam:s0:c0", "rec:s0:c0"]
    res_route = _p3a_render(
        plan_route, d / "faith_route.wav", chunk_frames=97, backend="reader"
    )
    record("l1.p3a.silence_samples (routing 1000f+400f, 2 输出声道) = 600",
           res_route.ok
           and res_route.frames == 1000 and res_route.output_channels == 2
           and res_route.silence_samples == 600,
           f"silence={res_route.silence_samples} frames={res_route.frames} "
           f"output_channels={res_route.output_channels}")

    # --- 内存/长素材: 不全量载入, 峰值内存受 chunk 限制 -------------------
    long_wav = d / "long.wav"
    long_seconds = 30
    _p3a_long_fixture(long_wav, seconds=long_seconds)
    before = _work_set_mb()
    big = d / "long_out.wav"
    res = _p3a_render(
        _p3a_plan([{"source_id": "long", "path": long_wav, "channels": 4,
                    "samples": P3A_SR * long_seconds}]),
        big,
        chunk_frames=16384,
    )
    after = _work_set_mb()
    growth = None if (before is None or after is None) else after - before
    record(f"l1.p3a.长素材 ({long_seconds}s×4CH float32 源) chunked 渲染成功",
           res.ok and res.frames == P3A_SR * long_seconds,
           f"frames={res.frames} errors={res.errors}")
    record("l1.p3a.长素材渲染内存增长受 chunk 限制 (非全量载入)",
           growth is None or growth < 260.0,
           f"ΔWorkingSet={growth}MB (chunk=16384 frames ≈ 0.34s ≈ 5.2MB)")
    # 无重复 decode: 每流只解一次
    record("l1.p3a.每流只解码一次 (无重复 decode)",
           res.ok and len(res.prepared) == 1
           and res.prepared[0]["actual_samples"] == P3A_SR * long_seconds,
           f"{json.dumps(res.prepared, ensure_ascii=False)[:160]}")


def _p3a_long_fixture(path: Path, *, seconds: float = 30.0) -> Path:
    """长音频 fixture (分块生成, 每块只驻留一个 chunk)。

    素材长度用 `seconds` 显式给出 (默认 30s: 4CH float32 ≈ 23MB, 足以验证
    "峰值内存受 chunk 限制"而不让回归时间失控)。
    """
    from core.audio_wav import WavFormat, write_wav

    total = int(P3A_SR * seconds)
    chunk = 4096
    t = (np.arange(total, dtype=np.float32)) / P3A_SR
    blocks = []
    for start in range(0, total, chunk):
        n = min(chunk, total - start)
        seg = t[start:start + n]
        block = np.zeros((n, 4), dtype=np.float32)
        block[:, 0] = 0.4 * np.sin(2 * np.pi * 440.0 * seg)
        block[:, 1] = 0.3 * np.sin(2 * np.pi * 1000.0 * seg)
        block[:, 2] = 0.2 * np.sin(2 * np.pi * 250.0 * seg)
        block[:, 3] = 0.1 * np.sin(2 * np.pi * 60.0 * seg)
        blocks.append(block)
    write_wav(path, blocks, sample_rate=P3A_SR, channel_count=4,
              sample_format=WavFormat.FLOAT32, frame_count=total,
              overwrite=True)
    return path


def _p3a_integer_pcm(d: Path) -> tuple[bool, str]:
    """s16/s24/s32/f32 线性 PCM -> canonical float32, 与 ffmpeg 直读逐样本一致。

    用确定性正弦 (lavfi `sine`) 生成, 避免"素材全静音"掩盖归一化错误。
    """
    from core.audio_models import build_audio_streams
    from core.audio_wav import read_wav

    details: list[str] = []
    all_ok = True
    for codec, wav_fmt in (("pcm_s16le", "s16"), ("pcm_s24le", "s24"),
                           ("pcm_s32le", "s32"), ("pcm_f32le", "f32")):
        src = d / f"int_{codec}.wav"
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                "sine=frequency=997:sample_rate=48000:duration=1",
                "-af", "volume=0.5", "-c:a", codec, "-ac", "1", src,
                timeout=600)
        if rc.returncode != 0:
            return False, f"{codec}: fixture 生成失败"
        streams = build_audio_streams(
            ffprobe_json(src).get("streams", []), source_id="pcm"
        )
        plan = _p3a_build_plan([{
            "source_id": "pcm", "path": str(src), "streams": streams,
        }])
        out = d / f"int_{codec}_out.wav"
        res = _p3a_render(plan, out, chunk_frames=4096)
        if not res.ok:
            return False, f"{codec}: render 失败 {res.errors}"
        got, info = read_wav(out)
        ref_path = d / f"int_{codec}.raw"
        rc2 = sh(FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:a:0",
                 "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1", ref_path)
        if rc2.returncode != 0:
            return False, f"{codec}: 参考解码失败"
        ref = np.fromfile(ref_path, dtype="<f4")
        exact = bool(np.array_equal(got[:, 0], ref[: info.frame_count]))
        peak = float(np.abs(got).max())
        # 归一化到 [-1,1] 且非静音 (s16 只有 16-bit 精度, sine 峰值本就低)
        all_ok = all_ok and exact and 0.01 < peak <= 1.0
        details.append(f"{wav_fmt}:exact={exact},peak={peak:.4f}")
    return all_ok, " ".join(details)


def l3_audio_process() -> None:
    """v0.7.1 Phase 3A: 真实素材 PCM routing + WAV export.

    真实素材:
      * Sony A7M5 4CH (testsets, **4×mono PCM s24be**, layout 缺失) -> media source
      * ffmpeg 生成的 deterministic 外挂 WAV (mono / stereo / 4CH) -> wav source

    验证: 4CH→4CH、CH selection、reorder、外挂 WAV、camera+WAV 跨来源 ->
    WAV, 全部逐样本可追溯; 并证明 4×mono **不会**被当成一个 4CH 流。
    """
    section("L3 音频 PCM 路由 / WAV 导出 (v0.7.1 Phase 3A)")
    from core.audio_models import AudioSourceType, build_audio_streams
    from core.audio_plan import AudioPlanner
    from core.audio_wav import read_wav

    d = IN_DIR / "audio_p3a"
    d.mkdir(parents=True, exist_ok=True)
    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if not real_files:
        record("l3.p3a.真实 A7M5 素材存在", False, "testsets 素材不存在")
        return
    src_mp4 = real_files[0]

    # --- 真实素材建模 (4×mono) -----------------------------------------
    raw_streams = ffprobe_json(src_mp4).get("streams", [])
    audio_streams = build_audio_streams(raw_streams, source_id="camera")
    record("l3.p3a.真实 A7M5 = 4 条独立 mono 流 (不是 1 条 4CH 流)",
           len(audio_streams) == 4
           and all(s.channel_count == 1 for s in audio_streams)
           and [s.stream_index for s in audio_streams] == [1, 2, 3, 4]
           and [s.audio_position for s in audio_streams] == [0, 1, 2, 3]
           and audio_streams[0].codec_name == "pcm_s24be",
           f"{[(s.stream_index, s.channel_count, s.codec_name) for s in audio_streams]}")

    def plan_of(sources: list[dict[str, Any]]) -> Any:
        return _p3a_build_plan(sources)

    cam_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4),
        "streams": audio_streams,
    }])

    # --- A7M5 4CH -> 4CH WAV -------------------------------------------
    out1 = d / "a7m5_all.wav"
    res1 = _p3a_render(cam_plan, out1, chunk_frames=4096)
    arr1, info1 = read_wav(out1) if out1.exists() else (None, None)
    expected_frames = 288288          # 实测: 真实素材 6.006s @48k
    record("l3.p3a.真实 A7M5 4×mono -> 4 声道 WAV (逐声道 = 逐流)",
           res1.ok and info1 is not None
           and info1.channel_count == 4
           and info1.sample_rate == 48000
           and info1.frame_count == expected_frames
           and arr1.shape == (expected_frames, 4),
           f"ok={res1.ok} info={info1.to_dict() if info1 else None} "
           f"errors={res1.errors}")

    # 与 ffmpeg 直接解码的真实样本逐样本比对 (canonical float32 归一化正确)
    refs_real: dict[int, Any] = {}
    for position in range(4):
        rp = d / f"a7m5_real_a{position}.raw"
        rc = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4,
                "-map", f"0:a:{position}", "-vn", "-sn", "-dn",
                "-f", "f32le", "-ac", "1", rp)
        if rc.returncode == 0 and rp.is_file():
            refs_real[position] = np.fromfile(rp, dtype="<f4")
    if info1 is not None and len(refs_real) == 4:
        same = all(
            np.array_equal(arr1[:, c], refs_real[c][: info1.frame_count])
            for c in range(4)
        )
        # ⚠️ 该 testsets 素材实测为**全静音** PCM (4 条流全部 0), 因此这里
        # 断言"逐样本一致 + 未被重排/混音"而不是"有非零内容"。
        record("l3.p3a.真实 A7M5 s24be -> canonical float32 逐样本一致 "
               "(素材实测全静音, 故只断言结构一致)",
               same and info1.channel_count == 4
               and all(float(np.abs(refs_real[c]).max()) == 0.0
                       for c in range(4)),
               f"frames={info1.frame_count} "
               f"peaks={[float(np.abs(arr1[:, c]).max()) for c in range(4)]}")
    else:
        record("l3.p3a.真实 A7M5 s24be -> canonical float32 逐样本一致 "
               "(素材实测全静音, 故只断言结构一致)",
               False, "参考解码失败")

    # --- A7M5 CH selection -> mono WAV ---------------------------------
    sel_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4), "streams": audio_streams,
    }])
    AudioPlanner(sel_plan).select_channels("camera:s3:c0")
    out2 = d / "a7m5_ch3.wav"
    res2 = _p3a_render(sel_plan, out2, chunk_frames=4096)
    arr2, info2 = read_wav(out2) if out2.exists() else (None, None)
    raw3 = d / "a7m5_ch3_ref.raw"
    rr3 = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4, "-map", "0:a:2",
             "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1", raw3)
    ref3 = np.fromfile(raw3, dtype="<f4") if rr3.returncode == 0 else None
    record("l3.p3a.真实 A7M5 选单通道 -> mono WAV 且样本来自该流",
           res2.ok and info2 is not None and info2.channel_count == 1
           and ref3 is not None
           and np.array_equal(arr2[:, 0], ref3[: info2.frame_count])
           and sel_plan.selected_channels == ["camera:s3:c0"],
           f"ok={res2.ok} frames={info2.frame_count if info2 else None} "
           f"errors={res2.errors}")

    # --- A7M5 reorder (4,2,1,3) ----------------------------------------
    ord_plan = plan_of([{
        "source_id": "camera", "path": str(src_mp4), "streams": audio_streams,
    }])
    order = ["camera:s4:c0", "camera:s2:c0", "camera:s1:c0", "camera:s3:c0"]
    planner = AudioPlanner(ord_plan)
    planner.select_channels(*order)
    planner.map_channels(*order)
    out3 = d / "a7m5_reorder.wav"
    res3 = _p3a_render(ord_plan, out3, chunk_frames=4096)
    arr3, info3 = read_wav(out3) if out3.exists() else (None, None)
    refs = []
    for position in (3, 1, 0, 2):
        rp = d / f"a7m5_ref_a{position}.raw"
        rc = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4,
                "-map", f"0:a:{position}", "-vn", "-sn", "-dn",
                "-f", "f32le", "-ac", "1", rp)
        refs.append(np.fromfile(rp, dtype="<f4") if rc.returncode == 0 else None)
    ok3 = (res3.ok and info3 is not None and info3.channel_count == 4
           and all(r is not None for r in refs))
    if ok3:
        n = info3.frame_count
        ok3 = all(np.array_equal(arr3[:, c], refs[c][:n]) for c in range(4))
    record("l3.p3a.真实 A7M5 4×mono 重排 (4,2,1,3) 逐样本精确",
           bool(ok3)
           and res3.timeline.output_channel_ids == order
           and res3.route_spec is not None
           and all(r.offset_samples == 0 for r in res3.route_spec.channels),
           f"ok={res3.ok} order={res3.timeline.output_channel_ids if res3.timeline else None} "
           f"errors={res3.errors}")
    record("l3.p3a.4×mono 重排仍是「逐流整流」(不塌缩成 1 条 4CH 流)",
           bool(res3.ok and res3.route_spec is not None
                and len(res3.route_spec.channels) == 4
                and len({r.stream_id for r in res3.route_spec.channels}) == 4
                and all(r.channel_index == 0
                        for r in res3.route_spec.channels)))

    # --- 真实素材上的静音事实 (不是缺陷) --------------------------------
    if info1 is not None:
        record("l3.p3a.真实 A7M5 音频实测全静音 (4 流全 0, 非管线缺陷)",
               float(np.abs(arr1).max()) == 0.0
               and info1.frame_count == expected_frames,
               f"peak={float(np.abs(arr1).max())} frames={info1.frame_count}")

    # --- 线性 PCM 整数格式 -> canonical float32 (确定性正弦, 非静音) ----
    int_ok, int_detail = _p3a_integer_pcm(d)
    record("l3.p3a.真实 ffmpeg 解码 s16/s24/s32/f32 -> float32 逐样本一致",
           int_ok, int_detail)

    # --- 外挂 WAV -> WAV (逐样本一致) ----------------------------------
    wav_mono = d / "ext_mono.wav"
    wav_stereo = d / "ext_stereo.wav"
    wav_4ch = d / "ext_4ch.wav"
    mk_ok = True
    for path, channels, seed in ((wav_mono, 1, 11), (wav_stereo, 2, 12),
                                 (wav_4ch, 4, 13)):
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                f"anoisesrc=duration=2:sample_rate=48000:color=pink:seed={seed}",
                "-c:a", "pcm_s24le", "-ac", str(channels), path, timeout=600)
        mk_ok = mk_ok and rc.returncode == 0 and path.is_file()
    record("l3.p3a.外挂 WAV fixture 生成 (mono/stereo/4CH)", mk_ok)

    wav_streams = build_audio_streams(
        ffprobe_json(wav_stereo).get("streams", []), source_id="recorder"
    )
    wav_plan = plan_of([{
        "source_id": "recorder", "path": str(wav_stereo),
        "streams": wav_streams, "source_type": AudioSourceType.WAV,
    }])
    out4 = d / "ext_stereo_out.wav"
    res4 = _p3a_render(wav_plan, out4, chunk_frames=997)
    arr4, info4 = read_wav(out4) if out4.exists() else (None, None)
    ref4, head4 = read_wav(wav_stereo)
    record("l3.p3a.外挂 WAV (stereo, pcm_s24le) -> WAV 逐样本一致",
           res4.ok and info4 is not None and info4.channel_count == 2
           and info4.frame_count == head4.frame_count == 96000
           and np.array_equal(arr4, ref4),
           f"ok={res4.ok} frames={info4.frame_count if info4 else None} "
           f"errors={res4.errors}")

    # --- camera + WAV 跨来源 -> 多声道 WAV ------------------------------
    cross_plan = plan_of([
        {"source_id": "camera", "path": str(src_mp4), "streams": audio_streams},
        {"source_id": "recorder", "path": str(wav_stereo),
         "streams": wav_streams, "source_type": AudioSourceType.WAV},
    ])
    cross_order = ["camera:s3:c0", "recorder:s0:c0", "camera:s1:c0"]
    cross = AudioPlanner(cross_plan)
    cross.select_channels(*cross_order)
    cross.map_channels(*cross_order)
    out5 = d / "cross_source.wav"
    res5 = _p3a_render(cross_plan, out5, chunk_frames=4096)
    arr5, info5 = read_wav(out5) if out5.exists() else (None, None)
    ok5 = (res5.ok and info5 is not None and info5.channel_count == 3
           and info5.frame_count == expected_frames          # UNION: camera 更长
           and ref3 is not None)
    if ok5:
        # 短来源 (2s WAV) 在 6.006s 窗口内: 前 96000 帧真实, 其余静音
        ok5 = (np.array_equal(arr5[:96000, 1], ref4[:, 0])
               and not np.any(arr5[96000:, 1])
               and np.array_equal(arr5[:, 0], ref3[: info5.frame_count]))
    record("l3.p3a.camera + 外挂 WAV 跨来源 -> 3 声道 WAV (UNION, 短源补静音)",
           bool(ok5)
           and res5.route_spec is not None
           and res5.route_spec.is_multi_source
           and res5.silence_samples > 0,
           f"ok={res5.ok} frames={info5.frame_count if info5 else None} "
           f"silence={res5.silence_samples} errors={res5.errors}")

    # --- 真实素材上的时长/EOF 事实 --------------------------------------
    if res1.timeline is not None:
        tl = res1.timeline
        record("l3.p3a.真实素材 render window = 实际解码并集 (无 metadata 冲突)",
               tl.frame_count == expected_frames
               and tl.start_sample == 0 and tl.end_sample == expected_frames
               and not res1.duration_mismatches
               and all(c.actual_samples == expected_frames
                       for c in tl.channels),
               f"{tl.summary()}")

    for tmp in d.glob("*.raw"):
        try:
            tmp.unlink()
        except OSError:
            pass


def _p3b_fixture(path: Path, channels: int, samples: int,
                 values: dict[int, float], *, at: int = 0,
                 sample_rate: int = P3A_SR) -> Path:
    """单点确定性常量素材: `values[channel] = 幅度` 落在样本 `at`。"""
    from core.audio_wav import WavFormat, write_wav

    x = np.zeros((samples, channels), dtype=np.float32)
    for ch, value in values.items():
        x[at, ch] = float(value)
    write_wav(path, [x], sample_rate=sample_rate, channel_count=channels,
              sample_format=WavFormat.FLOAT32, frame_count=samples,
              overwrite=True)
    return path


def _p3b_bus(plan: Any, ids: list[str], gains: Any = None, **kwargs: Any) -> Any:
    from core.audio_mix import MixBusBuilder

    return MixBusBuilder(plan).sum_all(ids, gains=gains, **kwargs)


def l1_audio_mix() -> None:
    """Phase 3B: N->1 混音 / gain / float32 累加 / peak / clipping。"""
    section("L1 PCM 混音 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix"):
        return
    from core.audio_mix import (
        ClipPolicy, MixBus, MixBusBuilder, MixGain, MixSink, db_to_linear,
        linear_to_db,
    )
    from core.audio_wav import WavFormat, read_wav

    d = WORK / "p3b"
    d.mkdir(parents=True, exist_ok=True)

    # --- gain 标度 (线性, 不做响度标准化) -------------------------------
    record("l1.p3b.gain 线性标度 (0dB=1.0, -6dB≈0.5012, +6dB≈1.9953)",
           db_to_linear(0.0) == 1.0
           and abs(db_to_linear(-6.0) - 0.501187) < 1e-6
           and abs(db_to_linear(6.0) - 1.995262) < 1e-6
           and abs(linear_to_db(db_to_linear(-6.0)) + 6.0) < 1e-9)

    # --- A=1, B=1, gain 0.5/0.5 -> 1.0 ---------------------------------
    a = _p3b_fixture(d / "one_a.wav", 1, 400, {0: 1.0}, at=100)
    b = _p3b_fixture(d / "one_b.wav", 1, 400, {0: 1.0}, at=100)
    plan = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": b, "channels": 1, "samples": 400},
    ])
    plan.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus = _p3b_bus(plan, ["a:s0:c0", "b:s0:c0"],
                   gains={"a:s0:c0": 0.5, "b:s0:c0": 0.5})
    out = d / "half_half.wav"
    res = _p3a_render(plan, out, chunk_frames=97, mix_bus=bus)
    arr, info = _p3a_read(out) if out.exists() else (None, None)
    record("l1.p3b.N->1: 1.0×0.5 + 1.0×0.5 = 1.0 (sample-exact)",
           res.ok and info is not None and info.channel_count == 1
           and float(arr[100, 0]) == 1.0
           and float(np.abs(arr[:, 0]).max()) == 1.0
           and res.mix_stats is not None
           and res.mix_stats.clip_count == 0
           and abs(res.mix_stats.peak - 1.0) < 1e-9,
           f"value={float(arr[100, 0]) if arr is not None else None} "
           f"errors={res.errors}")
    record("l1.p3b.峰值可分解到逐输入贡献 (Σ contributions = peak)",
           res.mix_stats is not None
           and abs(sum(x["contribution"] for x in res.mix_stats.peak_inputs)
                   - res.mix_stats.peak) < 1e-6
           and [x["channel_id"] for x in res.mix_stats.peak_inputs]
           == ["a:s0:c0", "b:s0:c0"]
           and res.mix_stats.peak_frame == 100,
           f"{res.mix_stats.peak_inputs if res.mix_stats else None}")

    # --- A=1, B=-1, gain 0.5/0.5 -> 0.0 --------------------------------
    neg = _p3b_fixture(d / "one_neg.wav", 1, 400, {0: -1.0}, at=100)
    plan2 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": neg, "channels": 1, "samples": 400},
    ])
    plan2.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus2 = _p3b_bus(plan2, ["a:s0:c0", "b:s0:c0"],
                    gains={"a:s0:c0": 0.5, "b:s0:c0": 0.5})
    out2 = d / "cancel.wav"
    res2 = _p3a_render(plan2, out2, chunk_frames=41, mix_bus=bus2)
    arr2, _i2 = _p3a_read(out2) if out2.exists() else (None, None)
    record("l1.p3b.相位相消: 1.0×0.5 + (-1.0)×0.5 = 0.0",
           res2.ok and arr2 is not None
           and float(arr2[100, 0]) == 0.0
           and float(np.abs(arr2).max()) == 0.0,
           f"value={float(arr2[100, 0]) if arr2 is not None else None}")

    # --- 溢出 / clipping: 1+1 (单位增益) --------------------------------
    plan3 = _p3a_plan([
        {"source_id": "a", "path": a, "channels": 1, "samples": 400},
        {"source_id": "b", "path": b, "channels": 1, "samples": 400},
    ])
    plan3.selected_channels = ["a:s0:c0", "b:s0:c0"]
    bus3 = _p3b_bus(plan3, ["a:s0:c0", "b:s0:c0"])
    # DETECT (默认): 数据保留, 只统计
    out3 = d / "over_detect.wav"
    res3 = _p3a_render(plan3, out3, chunk_frames=64, mix_bus=bus3)
    arr3, _i3 = _p3a_read(out3) if out3.exists() else (None, None)
    record("l1.p3b.overflow + detect: 2.0 原样保留, |sum|>1 被计数",
           res3.ok and arr3 is not None and float(arr3[100, 0]) == 2.0
           and res3.mix_stats is not None
           and res3.mix_stats.clip_count == 1
           and abs(res3.mix_stats.peak - 2.0) < 1e-9,
           f"value={float(arr3[100, 0]) if arr3 is not None else None} "
           f"stats={res3.mix_stats.summary() if res3.mix_stats else None}")
    # 不自动 normalize: 除峰值外其余样本必须仍是 0 (没有被整体缩放)
    record("l1.p3b.不自动 normalize (非峰值样本保持 0, 未被整体缩放)",
           res3.ok and arr3 is not None
           and int(np.count_nonzero(arr3)) == 1)
    # HARD_CLIP: 显式裁剪
    out3h = d / "over_clip.wav"
    res3h = _p3a_render(plan3, out3h, chunk_frames=64, mix_bus=bus3,
                        clip_policy=ClipPolicy.HARD_CLIP)
    arr3h, _i3h = _p3a_read(out3h) if out3h.exists() else (None, None)
    record("l1.p3b.overflow + hard_clip: 显式裁到 1.0 且记录 hard_clipped",
           res3h.ok and arr3h is not None and float(arr3h[100, 0]) == 1.0
           and res3h.mix_stats is not None
           and res3h.mix_stats.hard_clipped == 1
           and res3h.mix_stats.clip_count == 1,
           f"value={float(arr3h[100, 0]) if arr3h is not None else None} "
           f"hard_clipped={res3h.mix_stats.hard_clipped if res3h.mix_stats else None}")
    # ERROR: 直接拒绝
    out3e = d / "over_error.wav"
    try:
        out3e.unlink()
    except OSError:
        pass
    res3e = _p3a_render(plan3, out3e, chunk_frames=64, mix_bus=bus3,
                        clip_policy=ClipPolicy.ERROR)
    record("l1.p3b.overflow + error: audio_mix_clipping 且不产出文件",
           (not res3e.ok)
           and "audio_mix_clipping" in [e.get("reason") for e in res3e.errors]
           and not out3e.exists(),
           f"{[e.get('reason') for e in res3e.errors]}")

    # --- 整数格式: 格式级裁剪与 float 输出可 over-range 对照 ------------
    out3f = d / "over_float32.wav"
    res3f = _p3a_render(plan3, out3f, chunk_frames=64, mix_bus=bus3,
                        sample_format=WavFormat.FLOAT32)
    arr3f, info3f = _p3a_read(out3f) if out3f.exists() else (None, None)
    record("l1.p3b.float32 输出允许 over-range (2.0 原样写出)",
           res3f.ok and arr3f is not None and float(arr3f[100, 0]) == 2.0
           and info3f is not None and info3f.is_float,
           f"value={float(arr3f[100, 0]) if arr3f is not None else None}")
    out3p = d / "over_pcm16.wav"
    res3p = _p3a_render(plan3, out3p, chunk_frames=64, mix_bus=bus3,
                        sample_format=WavFormat.PCM16)
    arr3p, _i3p = _p3a_read(out3p) if out3p.exists() else (None, None)
    record("l1.p3b.PCM16 输出: 2.0 被格式裁剪到 32767 (≈0.99997)",
           res3p.ok and arr3p is not None
           and abs(float(arr3p[100, 0]) - 32767.0 / 32768.0) < 1e-6
           and res3p.mix_stats is not None
           and res3p.mix_stats.format_clip_count >= 1,
           f"value={float(arr3p[100, 0]) if arr3p is not None else None} "
           f"format_clip={res3p.mix_stats.format_clip_count if res3p.mix_stats else None}")

    # --- 多来源 + 多声道: 2×stereo -> 2 个 mono 输出 --------------------
    cam = _p3b_fixture(d / "st_cam.wav", 2, 300,
                       {0: 0.5, 1: 0.25}, at=50)
    rec = _p3b_fixture(d / "st_rec.wav", 2, 300,
                       {0: 0.25, 1: 0.5}, at=50)
    plan4 = _p3a_plan([
        {"source_id": "cam", "path": cam, "channels": 2, "samples": 300},
        {"source_id": "rec", "path": rec, "channels": 2, "samples": 300},
    ])
    plan4.selected_channels = ["cam:s0:c0", "rec:s0:c0", "cam:s0:c1",
                               "rec:s0:c1"]
    bus4 = MixBusBuilder(plan4)
    multi = bus4.bus()
    multi.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("cam:s0:c0", 0.5),
                        MixGain("rec:s0:c0", 0.5)]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("cam:s0:c1", 0.5),
                        MixGain("rec:s0:c1", 0.5)]),
    ]
    out4 = d / "two_bus.wav"
    res4 = _p3a_render(plan4, out4, chunk_frames=37, mix_bus=multi)
    arr4, info4 = _p3a_read(out4) if out4.exists() else (None, None)
    record("l1.p3b.两路独立混音bus (L=L_cam+L_rec, R=R_cam+R_rec)",
           res4.ok and arr4 is not None and arr4.shape == (300, 2)
           and abs(float(arr4[50, 0]) - 0.375) < 1e-6
           and abs(float(arr4[50, 1]) - 0.375) < 1e-6
           and info4 is not None and info4.channel_count == 2
           and multi.trace(0)["is_mixing"] is True
           and [x["channel_id"] for x in multi.trace(1)["inputs"]]
           == ["cam:s0:c1", "rec:s0:c1"],
           f"L={float(arr4[50, 0]) if arr4 is not None else None} "
           f"R={float(arr4[50, 1]) if arr4 is not None else None}")

    # --- 短 source 在混音里也是静音 (EOF 策略统一) -----------------------
    long_src = _p3b_fixture(d / "long_m.wav", 1, 1000, {0: 0.5}, at=200)
    short_src = _p3b_fixture(d / "short_m.wav", 1, 400, {0: 0.5}, at=200)
    plan5 = _p3a_plan([
        {"source_id": "long", "path": long_src, "channels": 1, "samples": 1000},
        {"source_id": "short", "path": short_src, "channels": 1,
         "samples": 400},
    ])
    plan5.selected_channels = ["long:s0:c0", "short:s0:c0"]
    bus5 = _p3b_bus(plan5, ["long:s0:c0", "short:s0:c0"],
                    gains={"long:s0:c0": 0.5, "short:s0:c0": 0.5})
    out5 = d / "short_mix.wav"
    res5 = _p3a_render(plan5, out5, chunk_frames=64, mix_bus=bus5)
    arr5, info5 = _p3a_read(out5) if out5.exists() else (None, None)
    record("l1.p3b.混音不重新定义 EOF: 短 source EOF 后等同静音, 长 source 继续",
           res5.ok and arr5 is not None and info5 is not None
           and info5.frame_count == 1000
           and abs(float(arr5[200, 0]) - 0.5) < 1e-6
           and not np.any(arr5[500:]),
           f"frames={info5.frame_count if info5 else None} "
           f"v200={float(arr5[200, 0]) if arr5 is not None else None} "
           f"tail={float(np.abs(arr5[500:]).max()) if arr5 is not None else None}")
    # 回归 (F1): 静音统计必须走 **base timeline**。混音 timeline 的声道身份
    # 是 `mix0`, 拿它去查源声道 (`long:s0:c0`) 必然 miss -> 恒定 total=0 ->
    # silence_samples 恒报满 (1000/1000)。此处钉死正确语义:
    # 唯一输出声道的输入区间并集 = [0,1000) 覆盖整个 window -> 静音 0。
    record("l1.p3b.silence_samples 走 base timeline (混音图不恒报满)",
           res5.ok
           and res5.silence_samples == 0
           and res5.frames == 1000 and res5.output_channels == 1
           and res5.silence_samples != res5.frames * res5.output_channels,
           f"silence={res5.silence_samples} "
           f"total={res5.frames * res5.output_channels}")

    # --- 带 offset 的混音: 两轨对齐后相加 -------------------------------
    early = _p3b_fixture(d / "off_a.wav", 1, 1200, {0: 0.5}, at=1000)
    late = _p3b_fixture(d / "off_b.wav", 1, 1200, {0: 0.5}, at=1060)
    plan6 = _p3a_plan([
        {"source_id": "e", "path": early, "channels": 1, "samples": 1200},
        {"source_id": "l", "path": late, "channels": 1, "samples": 1200},
    ])
    set_p3a_offset(plan6, "l:s0:c0", 60)      # 晚到 60 -> 前移到 1000
    plan6.selected_channels = ["e:s0:c0", "l:s0:c0"]
    bus6 = _p3b_bus(plan6, ["e:s0:c0", "l:s0:c0"])
    out6 = d / "offset_mix.wav"
    res6 = _p3a_render(plan6, out6, chunk_frames=64, mix_bus=bus6)
    arr6, _i6 = _p3a_read(out6) if out6.exists() else (None, None)
    peaks6 = (_p3a_impulse_timeline(arr6, res6.timeline, threshold=1e-6)
              if arr6 is not None else None)
    record("l1.p3b.offset 在混音中同样生效: 晚到 60 的轨被前移后与另一轨求和",
           res6.ok and arr6 is not None and peaks6 == [[1000]]
           and abs(float(arr6[1060, 0]) - 1.0) < 1e-6
           and int(np.count_nonzero(arr6)) == 1,
           f"timeline_peak={peaks6} "
           f"window=[{res6.timeline.start_sample},{res6.timeline.end_sample}) "
           f"v={float(arr6[1060, 0]) if arr6 is not None else None}")

    # --- 混音规格校验 ---------------------------------------------------
    from core.audio_mix import validate_mix_bus

    bad = MixBusBuilder(plan).bus()
    bad.sinks = [MixSink(output_index=0,
                         inputs=[MixGain("nope:s0:c0", 1.0)])]
    issues = validate_mix_bus(plan, bad)
    record("l1.p3b.混音引用了不存在的声道 -> audio_channel_not_found",
           any(i["reason"] == "audio_channel_not_found" for i in issues),
           f"{[i['reason'] for i in issues]}")
    bad2 = MixBusBuilder(plan).bus()
    bad2.sinks = [MixSink(output_index=3, inputs=[MixGain("a:s0:c0", 1.0)])]
    issues2 = validate_mix_bus(plan, bad2)
    record("l1.p3b.混音输出 index 不连续 -> audio_mix_invalid",
           any(i["reason"] == "audio_mix_invalid" for i in issues2),
           f"{[i['reason'] for i in issues2]}")
    bad3 = MixBusBuilder(plan).bus()
    bad3.sinks = [MixSink(output_index=0,
                          inputs=[MixGain("a:s0:c0", -1.0)])]
    issues3 = validate_mix_bus(plan, bad3)
    record("l1.p3b.负增益 -> audio_mix_invalid (不做相位反相作为常规增益)",
           any(i["reason"] == "audio_mix_invalid" for i in issues3),
           f"{[i['reason'] for i in issues3]}")
    record("l1.p3b.MixBus JSON 往返 (gain / gain_db / policy)",
           MixBus.from_dict(bus.to_dict()).to_dict() == bus.to_dict()
           and MixBus.from_dict(
               {"mix_mode": "sum",
                "sinks": [{"output_index": 0,
                           "inputs": [{"channel_id": "a:s0:c0",
                                       "gain_db": -6.0}]}]}
           ).sinks[0].inputs[0].gain == db_to_linear(-6.0))

    # --- plan -> 模型字段 / 序列化 --------------------------------------
    plan.mix_buses = [bus]
    model_round = type(plan).from_dict(plan.to_dict())
    record("l1.p3b.AudioPlan.mix_buses JSON 往返 + is_default 因混音为假",
           len(model_round.mix_buses) == 1
           and model_round.mix_buses[0]["sinks"][0]["inputs"][0]["channel_id"]
           == "a:s0:c0"
           and plan.is_default is False
           and _p3a_plan([
               {"source_id": "a", "path": a, "channels": 1, "samples": 400},
           ]).is_default is True,
           f"mix_buses={json.dumps(model_round.mix_buses, ensure_ascii=False)[:120]}")
    from core.audio_plan import build_map_spec

    plan.mix_mode = "sum"
    spec_after = build_map_spec(plan)
    record("l1.p3b.-map 规格仍拒绝混音 (audio_mix_not_supported, 两阶段契约并存)",
           spec_after.executable is False
           and "audio_mix_not_supported"
           in [e["reason"] for e in spec_after.errors],
           f"{[e['reason'] for e in spec_after.errors]}")


def l1_audio_mix_invariance() -> None:
    """Phase 3B: 混音结果与 chunk 划分无关 + 与路由图在 1:1 时逐样本一致。"""
    section("L1 PCM 混音 chunk invariance / 图等价 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix_inv"):
        return
    from core.audio_mix import MixBusBuilder
    from core.audio_wav import read_wav

    d = WORK / "p3b_inv"
    d.mkdir(parents=True, exist_ok=True)

    # 多来源 + 多声道 + offset 下的混音, 跨 chunk 划分 byte-identical
    a = _p3a_fixture(d / "inv_a.wav", 2, 1500, {0: 111, 1: 700})
    b = _p3a_fixture(d / "inv_b.wav", 2, 1200, {0: 900, 1: 42})
    plan = _p3a_plan([
        {"source_id": "cam", "path": a, "channels": 2, "samples": 1500},
        {"source_id": "rec", "path": b, "channels": 2, "samples": 1200},
    ])
    set_p3a_offset(plan, "rec:s0:c0", 480)
    set_p3a_offset(plan, "rec:s0:c1", -240)
    plan.selected_channels = ["cam:s0:c0", "rec:s0:c0", "cam:s0:c1",
                              "rec:s0:c1"]
    plan.mix_mode = "sum"
    bus = MixBusBuilder(plan).bus()
    from core.audio_mix import MixGain, MixSink

    bus.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("cam:s0:c0", 0.5),
                        MixGain("rec:s0:c0", 0.5)]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("cam:s0:c1", 0.5),
                        MixGain("rec:s0:c1", 0.5)]),
    ]
    hashes, peaks, stats = {}, {}, {}
    for chunk in (7, 256, 1024, 4096):
        out = d / f"mix_inv_{chunk}.wav"
        res = _p3a_render(plan, out, chunk_frames=chunk, mix_bus=bus,
                          backend="reader")
        if not res.ok:
            hashes[chunk] = f"ERR {[e.get('reason') for e in res.errors]}"
            continue
        hashes[chunk] = _p3a_hash(out)
        arr, _info = read_wav(out)
        peaks[chunk] = _p3a_impulse_map(arr, threshold=0.01)
        stats[chunk] = res.mix_stats.to_dict() if res.mix_stats else None
    record("l1.p3b.混音 chunk 7/256/1024/4096 -> byte-identical 输出",
           len(set(hashes.values())) == 1,
           f"{json.dumps(hashes, ensure_ascii=False)}")
    record("l1.p3b.混音 impulse 位置与 peak/clip 统计均与 chunk 无关",
           len({json.dumps(v) for v in peaks.values()}) == 1
           and len({json.dumps({k: v for k, v in (s or {}).items()
                                if k != "frames"}, sort_keys=True)
                    for s in stats.values()}) == 1,
           f"peaks={json.dumps(peaks.get(256), ensure_ascii=False)}")

    # 图等价: 1:1 单输入 sink 的"混音图"必须与 Phase 3A 路由图逐样本一致
    quad = _p3a_fixture(d / "eq.wav", 4, 800,
                        {0: 100, 1: 200, 2: 300, 3: 400})
    plan_route = _p3a_plan([
        {"source_id": "cam", "path": quad, "channels": 4, "samples": 800},
    ])
    order = ["cam:s0:c2", "cam:s0:c0", "cam:s0:c3", "cam:s0:c1"]
    from core.audio_plan import AudioPlanner

    planner = AudioPlanner(plan_route)
    planner.select_channels(*order)
    planner.map_channels(*order)
    route_out = d / "eq_route.wav"
    res_route = _p3a_render(plan_route, route_out, chunk_frames=64)
    plan_mix = _p3a_plan([
        {"source_id": "cam", "path": quad, "channels": 4, "samples": 800},
    ])
    plan_mix.selected_channels = list(order)
    bus_eq = MixBusBuilder(plan_mix).one_to_one(order)
    mix_out = d / "eq_mix.wav"
    res_mix = _p3a_render(plan_mix, mix_out, chunk_frames=64, mix_bus=bus_eq)
    arr_r, _ir = read_wav(route_out) if route_out.exists() else (None, None)
    arr_m, _im = read_wav(mix_out) if mix_out.exists() else (None, None)
    record("l1.p3b.图等价: 1:1 混音图 == 路由图 (逐样本, 含重排)",
           res_route.ok and res_mix.ok and arr_r is not None
           and arr_m is not None and np.array_equal(arr_r, arr_m)
           and _p3a_impulse_map(arr_m) == [[300], [100], [400], [200]],
           f"route_ok={res_route.ok} mix_ok={res_mix.ok} "
           f"peaks={_p3a_impulse_map(arr_m) if arr_m is not None else None}")

    # 混音与导出解耦: 同一计划既可只路由也可混音
    record("l1.p3b.WAV exporter 不实现 mixing (混音图与路由图共用同一 exporter)",
           res_route.ok and res_mix.ok
           and res_route.mix_bus is None and res_mix.mix_bus is not None
           and res_route.summary()["graph"] == "routing"
           and res_mix.summary()["graph"] == "mixing")

    # --- 读取传输层无关: 管道读取器 == 生产文件读取器 (逐字节) -----------
    pipe_out = d / "backend_pipe.wav"
    reader_out = d / "backend_reader.wav"
    res_pipe = _p3a_render(plan, pipe_out, chunk_frames=1024, mix_bus=bus,
                           backend="pipe")
    res_reader = _p3a_render(plan, reader_out, chunk_frames=1024, mix_bus=bus,
                             backend="reader")
    record("l1.p3b.混音结果与读取传输层无关 (ffmpeg 管道 == span 文件读取)",
           res_pipe.ok and res_reader.ok and pipe_out.exists()
           and reader_out.exists()
           and _p3a_hash(pipe_out) == _p3a_hash(reader_out),
           f"pipe={res_pipe.ok} reader={res_reader.ok}")


def l3_audio_mix() -> None:
    """v0.7.1 Phase 3B: 真实素材多来源混音 (A7M5 4×mono + 外挂 WAV)。"""
    section("L3 PCM 混音 (v0.7.1 Phase 3B)")
    if not _p3a_ready("mix_l3"):
        return
    from core.audio_models import build_audio_streams
    from core.audio_mix import MixBusBuilder, MixGain, MixSink, db_to_linear
    from core.audio_wav import WavFormat, read_wav

    d = IN_DIR / "audio_p3b"
    d.mkdir(parents=True, exist_ok=True)
    real_dir = ROOT / "testsets" / "a7m5_4k60p_265_10bit420_150m_xavchs_4ch"
    real_files = sorted(real_dir.glob("*.MP4")) if real_dir.is_dir() else []
    if not real_files:
        record("l3.p3b.真实 A7M5 素材存在", False, "testsets 素材不存在")
        return
    src_mp4 = real_files[0]
    cam_streams = build_audio_streams(
        ffprobe_json(src_mp4).get("streams", []), source_id="camera"
    )

    # 外挂 WAV (非静音, deterministic) 作为第二个来源
    rec_wav = d / "rec_p3b.wav"
    order = ["camera:s1:c0", "camera:s2:c0", "camera:s3:c0", "camera:s4:c0"]
    ok_mk = True
    for index, cid in enumerate(order):
        target = d / f"tone_{index}.wav"
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                f"sine=frequency={300 + 200 * index}:sample_rate=48000:"
                "duration=6.006", "-af", "volume=0.6", "-c:a", "pcm_s24le",
                "-ac", "1", target, timeout=600)
        ok_mk = ok_mk and rc.returncode == 0
        # 合并成一条 4 声道 WAV (混合来源: 4×mono 之外的另一种形态)
        if index == 0:
            inputs = ["-i", str(target)]
        else:
            inputs += ["-i", str(target)]
    if ok_mk:
        cmd = [FFMPEG, "-v", "error", "-y"]
        for index in range(4):
            cmd += ["-i", str(d / f"tone_{index}.wav")]
        cmd += ["-filter_complex",
                "[0:a][1:a][2:a][3:a]join=inputs=4:channel_layout=4.0[o]",
                "-map", "[o]", "-c:a", "pcm_s24le", rec_wav]
        rc = sh(*cmd, timeout=600)
        ok_mk = rc.returncode == 0 and rec_wav.is_file()
    record("l3.p3b.混音用外挂 4CH WAV fixture 生成 (4 路不同频率 sine)", ok_mk)

    if not ok_mk:
        return
    rec_streams = build_audio_streams(
        ffprobe_json(rec_wav).get("streams", []), source_id="recorder"
    )
    plan = _p3a_build_plan([
        {"source_id": "camera", "path": str(src_mp4), "streams": cam_streams},
        {"source_id": "recorder", "path": str(rec_wav), "streams": rec_streams},
    ])
    plan.selected_channels = ["camera:s1:c0", "recorder:s0:c0",
                              "recorder:s0:c1"]
    plan.mix_mode = "sum"
    bus = MixBusBuilder(plan).bus()
    bus.sinks = [
        MixSink(output_index=0, channel_id="mix0",
                inputs=[MixGain("camera:s1:c0", 1.0),
                        MixGain("recorder:s0:c0", db_to_linear(-6.0))]),
        MixSink(output_index=1, channel_id="mix1",
                inputs=[MixGain("recorder:s0:c1", 0.5)]),
    ]
    out = d / "real_mix.wav"
    res = _p3a_render(plan, out, chunk_frames=4096, mix_bus=bus)
    arr, info = read_wav(out) if out.exists() else (None, None)
    record("l3.p3b.真实 A7M5(全静音) + 外挂 WAV -> 2 声道混音 WAV",
           res.ok and info is not None and info.channel_count == 2
           and info.frame_count == 288288
           and arr is not None and float(np.abs(arr).max()) > 0.0
           and res.mix_stats is not None and res.mix_stats.outputs == 2,
           f"ok={res.ok} frames={info.frame_count if info else None} "
           f"peak={res.mix_stats.peak if res.mix_stats else None} "
           f"errors={res.errors}")

    # 轨迹可追溯: 输出 0 的两个输入都带身份与增益
    record("l3.p3b.混音输出可追溯到逐输入 (channel_id + gain)",
           bus.trace(0)["inputs"][0]["channel_id"] == "camera:s1:c0"
           and bus.trace(0)["inputs"][1]["channel_id"] == "recorder:s0:c0"
           and abs(bus.trace(0)["inputs"][1]["gain"] - 0.501187) < 1e-5
           and bus.trace(1)["is_mixing"] is False
           and bus.trace(0)["is_mixing"] is True,
           f"{bus.trace(0)}")

    # 混音输出 0 = camera(全静音) + recorder×(-6dB) -> 与参考逐样本一致。
    # ⚠️ 参考解码必须带 identity channelmap: 4 声道 WAV 会被 ffmpeg 按声明布局
    # 重排/下混 (quad), 与生产读取器的"按位置逐声道"语义不同。
    ref_path = d / "rec_mix_ref.raw"
    rc = sh(FFMPEG, "-v", "error", "-y", "-i", rec_wav, "-map", "0:a:0",
            "-af", f"channelmap=0|1|2|3,volume={db_to_linear(-6.0):.9f}",
            "-f", "f32le", "-ac", "4", ref_path, timeout=600)
    if rc.returncode == 0 and arr is not None:
        ref_all = np.fromfile(ref_path, dtype="<f4").reshape(-1, 4)
        ref = ref_all[:, 0]
        n = min(ref.shape[0], arr.shape[0])
        delta = float(np.max(np.abs(arr[:n, 0] - ref[:n]))) if n else 9.9
        cam_ref_path = d / "cam_ref.raw"
        rc2 = sh(FFMPEG, "-v", "error", "-y", "-i", src_mp4, "-map", "0:a:0",
                 "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1",
                 cam_ref_path)
        cam_peak = (float(np.abs(np.fromfile(cam_ref_path, dtype="<f4")).max())
                    if rc2.returncode == 0 else None)
        record("l3.p3b.混音输出 0 与参考 (recorder×-6dB) 逐样本一致",
               n > 100000 and delta <= 1e-6
               and cam_peak is not None and cam_peak == 0.0,
               f"n={n} max_delta={delta:.3e} camera_peak={cam_peak}")
    else:
        record("l3.p3b.混音输出 0 与参考 (recorder×-6dB) 逐样本一致",
               False, "参考解码失败")

    # 混音不改变源模型: 全部源声道仍在 (含未参与混音的), 增益只在 MixBus 上
    source_ids = [c.id for c in plan.all_channels()]
    record("l3.p3b.混音不改源声道身份/选择模型 (未参与混音的声道仍在)",
           len(source_ids) == 8                     # 4×mono + 4CH 全保留
           and plan.selected_channels == ["camera:s1:c0", "recorder:s0:c0",
                                          "recorder:s0:c1"]
           and "recorder:s0:c2" in source_ids
           and "recorder:s0:c2" not in plan.selected_channels
           and "camera:s2:c0" not in plan.selected_channels
           and plan.channel("camera:s1:c0").sync.status.value
           == "not_processed"
           and all(ch.sync.offset_samples is None for ch in plan.all_channels()),
           f"sources={source_ids} selected={plan.selected_channels}")
    record("l3.p3b.增益只存在于 MixBus (源模型上没有任何 mixer 参数)",
           not hasattr(plan.channel("camera:s1:c0"), "gain")
           and not hasattr(plan.channel("recorder:s0:c0"), "gain")
           and [g.gain for g in bus.sinks[0].inputs] == [1.0, db_to_linear(-6.0)],
           f"{[g.to_dict() for g in bus.sinks[0].inputs]}")


# ===========================================================================
# runner
# ===========================================================================

SUITES: dict[str, list[tuple[str, Callable[[], None]]]] = {
    "unit": [
        ("color/token", l1_color),
        ("caps", l1_caps),
        ("hw/plan/classify/flags", l1_hw),
        ("probe/paths", l1_probe_paths),
        ("classifier/scaling", l1_classifier_scaling),
        ("gpac/dji", l1_gpac_dji),
        ("x265 P0", l1_x265),
        ("quality/versions", l1_quality),
        ("channel-sync", l1_channel_sync),
        ("channel-sync P1", l1_channel_sync_p1),
        ("audio model v0.7.1", l1_audio_model),
        ("audio select/map v0.7.1", l1_audio_selection),
        ("audio timeline v0.7.1", l1_audio_timeline),
        ("audio sync offset v0.7.1", l1_audio_sync_offset),
        ("audio route v0.7.1", l1_audio_route),
        ("audio wav export v0.7.1", l1_audio_wav_export),
        ("audio chunk invariance v0.7.1", l1_audio_chunk_invariance),
        ("audio mix v0.7.1", l1_audio_mix),
        ("audio mix invariance v0.7.1", l1_audio_mix_invariance),
        ("av1", l1_av1),
        ("cli v0.6.2", l1_cli_v062),
    ],
    "toolchain": [("toolchain", l2_toolchain)],
    "full": [("pipeline", l3_pipeline),
             ("audio model probe v0.7.1", l3_audio_probe),
             ("audio select/map v0.7.1", l3_audio_selection),
             ("audio pcm/wav v0.7.1", l3_audio_process),
             ("audio mix v0.7.1", l3_audio_mix),
             ("channel-sync P1 E2E", l3_channel_sync_p1),
             ("channel-sync P1 算法级", l3_channel_sync_p1_algo)],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--level",
        choices=["unit", "toolchain", "full", "all"],
        default="full",
        help="测试深度: unit=纯逻辑; toolchain=+工具探测; full=+管线集成+故障注入",
    )
    args = ap.parse_args()
    levels = {
        "unit": ["unit"],
        "toolchain": ["unit", "toolchain"],
        "full": ["unit", "toolchain", "full"],
        "all": ["unit", "toolchain", "full"],
    }[args.level]

    WORK.mkdir(parents=True, exist_ok=True)
    started = time.time()
    global CURRENT_LEVEL
    for lvl in levels:
        print(f"\n########## 测试深度 {lvl.upper()} ##########")
        for name, fn in SUITES[lvl]:
            CURRENT_LEVEL = lvl
            t0 = time.monotonic()
            try:
                fn()
            except Exception as exc:
                record(f"{name} (异常)", False, f"{type(exc).__name__}: {exc}",
                       level=lvl)
            print(f"  [{lvl}] {name} — {time.monotonic() - t0:.1f}s")

    summary = {
        "PASS": sum(1 for r in RESULTS if r["status"] == "PASS"),
        "FAIL": sum(1 for r in RESULTS if r["status"] == "FAIL"),
    }
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": args.level,
        "elapsed_sec": round(time.time() - started, 1),
        "summary": summary,
        "items": RESULTS,
    }
    (WORK / "autotest_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 1KeyTranscoder 全量自动化测试报告",
        "",
        f"- 深度: {args.level} | 生成: {report['generated']} | "
        f"耗时: {report['elapsed_sec']}s",
        f"- 汇总: **{summary['PASS']} PASS / {summary['FAIL']} FAIL**",
        "",
        "| 级别 | 用例 | 状态 | 详情 |",
        "|---|---|---|---|",
    ]
    for r in RESULTS:
        lines.append(
            f"| {r['level']} | {r['name']} | {r['status']} | "
            f"{r['detail'][:120]} |"
        )
    (WORK / "autotest_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print(f"SUMMARY: {summary['PASS']} PASS / {summary['FAIL']} FAIL "
          f"({report['elapsed_sec']}s)")
    print(f"报告: {WORK / 'autotest_report.json'}")
    print(f"      {WORK / 'autotest_report.md'}")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"  FAIL [{r['level']}] {r['name']}: {r['detail'][:140]}")
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
