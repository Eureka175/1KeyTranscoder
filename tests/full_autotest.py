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


def l1_x265() -> None:
    section("L1 x265 P0 修复断言")
    import json
    cfg = json.loads((ROOT / "x265.json").read_text(encoding="utf-8"))
    for tier, p in cfg["profile"].items():
        # rd 值域 2-6 均合法; FAST 档按用户决定保持 rd=2
        # (psy-rd 在 rd<3 时静默失效, 属已知取舍, 不改)
        record(f"x265.{tier}.rd 值域合法", 2 <= int(p["rd"]) <= 6)
        record(f"x265.{tier}.info=false (可复现)", p["info"] is False)
        record(f"x265.{tier}.无 no-strong-intra-smoothing",
               "no_strong_intra_smoothing" not in p)
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

    # C15 nvenc-av1 DJI basic (AV1 保留管线: av01 + 数据轨)
    out = OUT_DIR / "c15_nvenc_av1_dji"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "nvenc-av1", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C15_nvenc_av1_dji_av01+元数据", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and {"djmd", "dbgi", "tmcd"} <= tags,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')}")

    # C16 nvenc-av1 Sony -> 保留管线 (rtmd/nrtm/uuid 保留, 不打 XAVC tag)
    out = OUT_DIR / "c16_nvenc_av1_sony"
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
    expect("C16_nvenc_av1_sony_保留管线",
           rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1" and rtmd
           and "XAVC" not in major_brand and "AV1" in tail,
           f"rc={rc} codec={vs.get('codec_name')} rtmd={rtmd} "
           f"brand={major_brand} policy_warn={'AV1' in tail}")

    # C17 qsv-av1 DJI basic
    out = OUT_DIR / "c17_qsv_av1_dji"
    rc, _ = _run_1kt(cases["dji"], out,
                     "--encoder", "qsv-av1", "--preset", "hq",
                     "--check", "basic", "--jobs", "1")
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    expect("C17_qsv_av1_dji", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1",
           f"rc={rc} codec={vs.get('codec_name')}")

    # C18 svtav1 DJI basic (软件 AV1 保留管线: av01 + 数据轨)
    out = OUT_DIR / "c18_svtav1_dji"
    rc, tail = _run_1kt(cases["dji"], out,
                        "--encoder", "svtav1", "--preset", "fast",
                        "--check", "basic", timeout=3600)
    final = out / "DJI_20260830095031_0009_D.MP4"
    v = ffprobe_json(final) if final.is_file() else {}
    vs = next((s for s in v.get("streams", [])
               if s.get("codec_type") == "video"), {})
    tags = {s.get("codec_tag_string") for s in v.get("streams", [])}
    expect("C18_svtav1_dji_av01+元数据", rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and {"djmd", "dbgi", "tmcd"} <= tags,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')} tail={tail[-120:].strip()}")

    # C19 svtav1 Sony basic (保留管线: rtmd 保留 + 422->420 降级 + 无 XAVC)
    out = OUT_DIR / "c19_svtav1_sony"
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
    expect("C19_svtav1_sony_保留+420+无XAVC",
           rc == 0 and final.is_file()
           and vs.get("codec_name") == "av1"
           and vs.get("pix_fmt") == "yuv420p10le"
           and rtmd and "XAVC" not in major_brand
           and "4:2:2" in tail,
           f"rc={rc} codec={vs.get('codec_name')} "
           f"pix_fmt={vs.get('pix_fmt')} rtmd={rtmd} "
           f"brand={major_brand} downgrade_warn={'4:2:2' in tail} "
           f"tail={tail[-120:].strip()}")

    # C20 svtav1 经典路径 (剥离后的 DJI 文件: video+audio only)
    if "classic" in cases:
        out = OUT_DIR / "c20_svtav1_classic"
        rc, tail = _run_1kt(cases["classic"], out,
                            "--encoder", "svtav1", "--preset", "fast",
                            "--check", "basic", timeout=3600)
        final = out / "classic_test.MP4"
        v = ffprobe_json(final) if final.is_file() else {}
        types = sorted({s.get("codec_type") for s in v.get("streams", [])})
        vs = next((s for s in v.get("streams", [])
                   if s.get("codec_type") == "video"), {})
        expect("C20_svtav1_classic", rc == 0 and final.is_file()
               and types == ["audio", "video"]
               and vs.get("codec_name") == "av1",
               f"rc={rc} types={types} codec={vs.get('codec_name')} "
               f"tail={tail[-120:].strip()}")
    else:
        expect("C20_svtav1_classic", False, "classic 输入未生成")

    # C21 svtav1 DJI full (Gyroflow 逐帧四元数消费端 on av01)
    out = OUT_DIR / "c21_svtav1_dji_full"
    rc, tail = _run_1kt(cases["dji"], out,
                        "--encoder", "svtav1", "--preset", "fast",
                        "--check", "full", timeout=3600)
    final = out / "DJI_20260830095031_0009_D.MP4"
    expect("C21_svtav1_dji_full_gyroflow",
           rc == 0 and final.is_file() and "dji gyroflow" in tail,
           f"rc={rc} tail={tail[-160:].strip()}")

    # C22 质量抽样 PASS (真实管线产物: C19 svtav1 Sony 输出 vs 源)
    from core.config import find_executable
    from preservation.quality import run_quality_sample
    ffmpeg = find_executable("ffmpeg", ROOT)
    ffprobe = find_executable("ffprobe", ROOT)
    c19_final = OUT_DIR / "c19_svtav1_sony" / "C9037.MP4"
    if c19_final.is_file():
        q = run_quality_sample(
            original=cases["sony"] / "C9037.MP4",
            final=c19_final,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            scratch=WORK / "quality_c22",
            opts={"sample_rate": 1, "max_duration_sec": 600},
            log=lambda m: None,
        )
        expect("C22_quality_sample_PASS",
               q["status"] == "PASS"
               and q["psnr_avg_db"] and q["psnr_avg_db"] > 25
               and q["ssim_all"] and q["ssim_all"] > 0.8,
               f"status={q['status']} psnr={q['psnr_avg_db']} "
               f"ssim={q['ssim_all']} detail={q['detail']}")
    else:
        expect("C22_quality_sample_PASS", False, "C19 输出缺失")

    # C23 质量抽样 FAIL (灰屏垃圾文件: 同分辨率同帧数, PSNR 必然崩溃)
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
                scratch=WORK / "quality_c23",
                opts={"sample_rate": 1, "max_duration_sec": 600},
                log=lambda m: None,
            )
            expect("C23_quality_sample_FAIL_捕获",
                   q["status"] == "FAIL",
                   f"status={q['status']} psnr={q['psnr_avg_db']} "
                   f"ssim={q['ssim_all']} detail={q['detail']}")
        else:
            expect("C23_quality_sample_FAIL_捕获", False,
                   f"garbage 生成失败 rc={r.returncode}")
    else:
        expect("C23_quality_sample_FAIL_捕获", False, "classic 输入未生成")


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
        ("av1", l1_av1),
    ],
    "toolchain": [("toolchain", l2_toolchain)],
    "full": [("pipeline", l3_pipeline)],
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
