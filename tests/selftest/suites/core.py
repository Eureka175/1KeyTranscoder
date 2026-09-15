#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 核心逻辑: color / caps / hw 规划 / probe 解析 / 分类器 / GPAC+DJI 事实."""

from __future__ import annotations

import tempfile
from pathlib import Path
from ..paths import WORK
from ..paths import record
from ..paths import section

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


# ===========================================================================
# L1 — 纯逻辑单测
# ===========================================================================
