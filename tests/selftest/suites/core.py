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


def l1_container_format() -> None:
    """容器格式策略 (docs/container-format.md): 中间体 MOV + 最终跟随源。

    纯策略断言, 不碰编码器 —— 真实编码/重封装的端到端断言在 L3
    (`hardware.l3_container_format`)。
    """
    from core.batch_hw import (
        HW_VIDEO_CONTAINER,
        _iso_media_kind,
        _REMUX_SUFFIX,
        intermediate_container,
    )
    from core.paths import final_suffix_for, output_path_for
    section("L1 container/format policy")

    # 1. 最终容器跟随输入容器
    cases = [
        ("a.mov", ".MOV"), ("a.MOV", ".MOV"), ("A.Mov", ".MOV"),
        ("a.mp4", ".MP4"), ("a.MP4", ".MP4"),
        # 未显式承诺的扩展名沿用既有行为 (.MP4), 不悄悄换容器
        ("a.mkv", ".MP4"), ("a.ts", ".MP4"), ("a.mxf", ".MP4"),
    ]
    got = {name: final_suffix_for(Path(name)) for name, _ in cases}
    bad = [
        f"{n}:{got[n]}!={want}" for n, want in cases if got[n] != want
    ]
    record("container.final_suffix 跟随输入", not bad,
           f"got={got}" if bad else f"got={got}")

    # 2. 真实输出路径 (大小写无关, 目录结构保留)
    root_in, root_out = Path(r"F:\in"), Path(r"F:\out")
    mov_out = output_path_for(
        root_in / "sub" / "clip.mov", root_in, root_out, "HQ", False
    )
    mp4_out = output_path_for(
        root_in / "sub" / "clip.MP4", root_in, root_out, "HQ", False
    )
    record("container.output_path MOV 输入 -> MOV 输出",
           mov_out == root_out / "sub" / "clip.MOV", f"out={mov_out}")
    record("container.output_path MP4 输入 -> MP4 输出",
           mp4_out == root_out / "sub" / "clip.MP4", f"out={mp4_out}")
    # 多 preset 目录维度不受影响
    multi = output_path_for(
        root_in / "clip.mov", root_in, root_out, "HQ", True
    )
    record("container.output_path 多 preset 目录保留",
           multi == root_out / "HQ" / "clip.MOV", f"out={multi}")

    # 3. 硬件工作容器: MOV —— 但 AV1 必须用 MP4 (MOV muxer 直接拒绝 AV1,
    #    实测 NVEncC/QSVEncC 报 "av1 only supported in MP4 and AVIF")。
    #    这是 codec 约束, 不是偏好, 所以断言按 codec 取值而不是断言常量。
    record("container.hw 中间体容器 MOV (非 AV1)",
           HW_VIDEO_CONTAINER == "mov"
           and intermediate_container("hevc") == "mov",
           f"HW_VIDEO_CONTAINER={HW_VIDEO_CONTAINER!r} "
           f"hevc={intermediate_container('hevc')!r}")
    record("container.hw AV1 中间体必须是 MP4",
           intermediate_container("av1") == "mp4",
           f"av1={intermediate_container('av1')!r}")
    record("container.remux 表只含 ISOBMFF 容器",
           set(_REMUX_SUFFIX) == {".mov", ".mp4", ".m4v"}
           and _REMUX_SUFFIX[".mov"] == ".MOV"
           and _REMUX_SUFFIX[".mp4"] == ".MP4",
           f"table={_REMUX_SUFFIX}")

    # 4. 区分"MP4Box 能否读" —— 只有真的 ISOBMFF 才允许重封装
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        isobmf = tdp / "real.mov"
        isobmf.write_bytes(
            (12).to_bytes(4, "big") + b"ftyp" + b"mp42"
        )
        mkv = tdp / "fake.mkv"
        mkv.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 32)
        empty = tdp / "empty.bin"
        empty.write_bytes(b"")
        record("container.isobmf 判定 (ftyp / 非 ISOBMFF / 空文件)",
               _iso_media_kind(isobmf) == "isobmf"
               and _iso_media_kind(mkv) == "other"
               and _iso_media_kind(empty) == "other",
               f"mov={_iso_media_kind(isobmf)} "
               f"mkv={_iso_media_kind(mkv)} empty={_iso_media_kind(empty)}")


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


def l1_gpac_paths() -> None:
    """MP4Box 路径改写: 跨卷 (源/输出盘 != 进程 cwd 盘) 不得抛异常.

    回归自真实故障: 从脚本目录 (F:) 启动、素材在 K: 时,
    `_opt` 内的 `os.path.relpath(p, Path.cwd())` 抛
    `ValueError: path is on mount 'K:', start on mount 'F:'`,
    整条 preservation 链路 (DJI/Sony/strip/container 转换) 全灭。
    """
    import ast
    import os

    from preservation.gpac import _opt, _opt_spec, _rel_base
    section("L1 gpac 路径改写 / MP4Box cwd")

    # 两个不存在的盘符足以让 ntpath.relpath 按"卷不同"报错 —— 纯词法
    # 判断, 与盘是否真的存在无关, 所以这条断言是离线可复现的。
    far, near = Path("Z:/a/b.mp4"), Path("Z:/c")
    other = Path("Y:/c")
    raised = False
    try:
        os.path.relpath(str(far), str(other))
    except ValueError:
        raised = True
    record("gpac 跨卷 relpath 确实会抛 ValueError (故障前提)",
           raised and far.drive.lower() != other.drive.lower())

    # 跨卷: 不得抛异常, 退回绝对路径 (MP4Box 的 -new/-add 接受绝对路径;
    # 而 base 永远取自"必须相对化的那条路径", 所以这里只会落在位置参数上)
    got = _opt(far, other)
    record("gpac._opt 跨卷不抛异常且退回绝对路径",
           got == str(far), f"got={got!r}")

    # 同卷: 仍然相对化 (保持 GPAC 26.02 已验证的 a:b:c 语义)。
    # base 是目录 Z:\c, 所以相对路径要先回到卷根再进 a\。
    rel = os.path.join("..", "a", "b.mp4")
    got = _opt(far, near)
    record("gpac._opt 同卷仍相对化", got == rel, f"got={got!r}")

    # base 缺省时退回进程 cwd (老行为, 供未改写的调用方使用)
    record("gpac._opt 无 base 时退回 cwd",
           _opt("relative/x.mp4") == "relative/x.mp4"
           and _opt(Path.cwd() / "x.mp4") == "x.mp4")

    # -add 片段: 只相对化 '#' 之前的部分
    got = _opt_spec(f"{far}#video", near)
    record("gpac._opt_spec 相对化 '#' 前部分",
           got == rel + "#video", f"got={got!r}")

    # _rel_base: 优先取目标所在目录 (存在即可作为 MP4Box 的 cwd),
    # 目录尚未建立时退回所在卷根 —— 两种情况都必须能相对化。
    base_existing = _rel_base(WORK / "gpac_cwd_probe.mov")
    record("gpac._rel_base 取目标所在目录",
           base_existing == WORK and _rel_base(Path(__file__)) == Path(__file__).parent,
           f"base={base_existing}")
    deep = Path("Z:/nope/deeper/out.mov")
    record("gpac._rel_base 目录不存在时退回卷根",
           _rel_base(deep) == Path("Z:\\") and _rel_base("x.mov") == Path.cwd(),
           f"base={_rel_base(deep)}")

    # GPAC 的**临时文件**必须在媒体/工作卷上（2026-10-30 真实事故）：GPAC
    # 26.02 把 -new/-add 的导入暂存写到 %TEMP%（默认 C:），50 GiB 透明重封装
    # 需要 C: 上数十 GiB，于是报 "No space left on device" 并回退成"源文件
    # 拷贝"—— 即使输出卷还有 300+ GiB。断言 TMP/TEMP/TMPDIR 被重定向到
    # cwd 下的 .gpactmp；无 cwd 时不得改写系统环境。
    from preservation.gpac import _gpac_env
    import shutil as _shutil

    probe_dir = WORK / "gpac_temp_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    env = _gpac_env(probe_dir)
    vals = {k: env.get(k) for k in ("TMP", "TEMP", "TMPDIR")}
    record("gpac 临时目录重定向到媒体卷 (TMP/TEMP/TMPDIR)",
           all(v == str(probe_dir / ".gpactmp") for v in vals.values()),
           f"{vals}")
    record("gpac 无 cwd 时不改写系统 TMP",
           _gpac_env(None).get("TMP") == os.environ.get("TMP"))
    _shutil.rmtree(probe_dir, ignore_errors=True)

    # 结构守卫: gpac.py 里每一处 _opt/_opt_spec 都必须显式传 base,
    # 且用了它们的每次 _run 必须钉住 cwd —— 否则跨卷故障会复活。
    src = (Path(__file__).resolve().parents[2].parent
           / "preservation" / "gpac.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    missing_base: list[int] = []
    missing_cwd: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else None
        if name in ("_opt", "_opt_spec") and len(node.args) < 2 \
                and not node.keywords:
            missing_base.append(node.lineno)
        if name == "_run":
            uses_opt = any(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id in ("_opt", "_opt_spec")
                for arg in node.args for sub in ast.walk(arg)
            )
            if uses_opt and not any(k.arg == "cwd" for k in node.keywords):
                missing_cwd.append(node.lineno)
    record("gpac.py 所有 _opt 调用都显式传 base", not missing_base,
           f"行号={missing_base}")
    record("gpac.py 用 _opt 的 _run 都钉住 cwd", not missing_cwd,
           f"行号={missing_cwd}")


# ===========================================================================
# L1 — 纯逻辑单测
# ===========================================================================
