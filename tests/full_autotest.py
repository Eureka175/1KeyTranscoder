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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

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
    from core.channel_sync import DEFAULTS, effective_opts, eligible_audio

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
        record("p1.路径输入 memmap (禁 np.fromfile 全量加载)",
               isinstance(m, np.memmap))
        try:
            mm = getattr(m, "_mmap", None)
            if mm is not None:
                mm.close()
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

    # C12 质量抽样 PASS (真实管线产物: C10 x265 Sony 输出 vs 源)
    from core.config import find_executable
    from preservation.quality import run_quality_sample
    ffmpeg = find_executable("ffmpeg", ROOT)
    ffprobe = find_executable("ffprobe", ROOT)
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

    # C13 质量抽样 FAIL (灰屏垃圾文件: 同分辨率同帧数, PSNR 必然崩溃)
    if "classic" in cases:
        garbage = WORK / "quality_c13_garbage.mp4"
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
    ],
    "toolchain": [("toolchain", l2_toolchain)],
    "full": [("pipeline", l3_pipeline),
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
