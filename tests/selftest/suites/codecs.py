#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编码器后端单元测试: x265 P0 / AV1 (svt-av1)。"""

from __future__ import annotations

from pathlib import Path
from ..paths import ROOT
from ..paths import record
from ..paths import section

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
    _vbv_mode = eff.audit.get("vbv-bufsize", {}).get("mode")
    record("x265.CPB 审计链",
           _vbv_mode in ("dynamic_vbv", "cpb_clamp"),
           f"audit={_vbv_mode}")


def l1_nvenc_dynamic_vbv() -> None:
    """NVENC maxrate/VBV follow the x265 source-relative table.

    QSV is intentionally excluded (its JSON must stay static) and VCE has
    no entry point; this test only guards the NVENC mapping.
    """
    section("L1 NVENC dynamic maxrate/VBV")
    import json
    from core.config import load_scaling_config
    from core.hw_vbv import apply_nvenc_dynamic_profile
    from core.models import SourceInfo

    src = SourceInfo(
        path=Path("x.mp4"), size_bytes=1, duration_sec=1.0,
        width=3840, height=2160, fps=59.94,
        r_frame_rate="60000/1001", avg_frame_rate="60000/1001",
        codec="hevc", profile="Main 10", pix_fmt="yuv420p10le",
        bit_depth=10, chroma="4:2:0", ob_kbps=150000.0,
        video_bitrate_kbps=150000.0, video_stream_count=1,
        stream_info=(),
    )
    nvenc = json.loads((ROOT / "nvenc.json").read_text(encoding="utf-8"))
    path = ROOT / "x265_scaling.json"
    load_scaling_config(path)  # validate the shared table first

    uhq, audit = apply_nvenc_dynamic_profile(
        nvenc["profile"]["UHQ"], "UHQ", src, path)
    record("nvenc.UHQ max_bitrate 跟随 x265 动态 VBV",
           uhq["max_bitrate"] == 105000
           and uhq["vbv_bufsize"] == 157500,
           f"max={uhq.get('max_bitrate')} vbv={uhq.get('vbv_bufsize')}")
    record("nvenc.UHQ 审计源分类", audit is not None
           and audit["source_class"] == "HIGH_BITRATE_LONG_GOP",
           f"audit={audit}")

    hq, _ = apply_nvenc_dynamic_profile(
        nvenc["profile"]["HQ"], "HQ", src, path)
    record("nvenc.HQ max_bitrate/vbv 跟随动态表",
           hq["max_bitrate"] == 75000 and hq["vbv_bufsize"] == 150000,
           f"max={hq.get('max_bitrate')} vbv={hq.get('vbv_bufsize')}")

    # Unknown OB -> static fallback, original profile values retained.
    unknown = SourceInfo(**{**src.__dict__, "ob_kbps": 0.0,
                            "video_bitrate_kbps": 0.0})
    fallback, no_audit = apply_nvenc_dynamic_profile(
        nvenc["profile"]["HQ"], "HQ", unknown, path)
    record("nvenc 未知 OB 回退静态表",
           no_audit is None
           and fallback["max_bitrate"] == 80000
           and fallback["vbv_bufsize"] == 240000,
           f"max={fallback.get('max_bitrate')} "
           f"vbv={fallback.get('vbv_bufsize')}")

    qsv = json.loads((ROOT / "qsv.json").read_text(encoding="utf-8"))
    qsv_static = {
        "UHQ": (100000, 300000),
        "HQ": (80000, 240000),
        "SMALL": (40000, 120000),
        "FAST": (60000, 240000),
    }
    record("qsv static maxrate/VBV 不变",
           all((int(qsv["profile"][p]["max_bitrate"]),
                int(qsv["profile"][p]["vbv_bufsize"])) == qsv_static[p]
               for p in qsv_static),
           "qsv.json must not follow dynamic VBV")


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


def l1_hardware_resize() -> None:
    """Hardware resize: lanczos default, explicit --output-res syntax."""
    section("L1 hardware resize")
    from encoders.nvencc import NvencBackend
    from encoders.qsvencc import QsvBackend
    from encoders.resize import (
        DEFAULT_RESIZE_ALGO,
        RESIZE_ALGOS,
        ResizeSpec,
        ScaleFactor,
        parse_resize_size,
        parse_scale_factor,
        resolve_resize,
        resize_args,
    )

    record("resize.default algo is lanczos",
           DEFAULT_RESIZE_ALGO == "lanczos", DEFAULT_RESIZE_ALGO)
    record("resize.algo choices narrowed",
           tuple(RESIZE_ALGOS) == ("bilinear", "bicubic", "lanczos"),
           "bilinear/bicubic/lanczos")
    record("resize.parse WxH",
           parse_resize_size("1920x1080") == (1920, 1080))
    record("resize.scale factor 0.5 -> 1080p",
           resolve_resize(ScaleFactor(0.5), 3840, 2160).width == 1920
           and resolve_resize(ScaleFactor(0.5), 3840, 2160).height == 1080)
    record("resize.scale factor 0.25 -> 960x540",
           resolve_resize(ScaleFactor(0.25), 3840, 2160).width == 960
           and resolve_resize(ScaleFactor(0.25), 3840, 2160).height == 540)
    record("resize.scale factor 2 -> 4K",
           resolve_resize(ScaleFactor(2.0), 1920, 1080).width == 3840
           and resolve_resize(ScaleFactor(2.0), 1920, 1080).height == 2160)
    portrait = resolve_resize(ScaleFactor(0.5), 2160, 3840)
    record("resize.scale portrait keeps aspect",
           (portrait.width, portrait.height) == (1080, 1920))
    record("resize.exact ignores aspect",
           resolve_resize(ResizeSpec(1600, 900), 3840, 2160).width == 1600
           and resolve_resize(ResizeSpec(1600, 900), 3840, 2160).height == 900)
    for bad in ("1921x1080", "0x1080", "1920", "abcx1080"):
        try:
            parse_resize_size(bad)
            ok = False
        except ValueError:
            ok = True
        record(f"resize.reject {bad}", ok)
    for bad in ("0", "-1", "nan", "inf"):
        try:
            parse_scale_factor(bad)
            ok = False
        except ValueError:
            ok = True
        record(f"resize.reject scale {bad}", ok)

    nv = NvencBackend(ROOT / "tools" / "NVEncC_9.31_x64" / "NVEncC64.exe")
    qs = QsvBackend(ROOT / "tools" / "QSVEncC_8.26_x64" / "QSVEncC64.exe")
    record("resize.nvenc tool advertises resize", nv.supports_resize)
    record("resize.qsv tool advertises resize", qs.supports_resize)

    spec = ResizeSpec(1920, 1080, "lanczos")
    ncmd, _, _ = nv.command(
        ROOT / "in.mp4", ROOT / "out.mov", {}, "4:2:0", 10,
        resize=spec)
    qcmd, _, _ = qs.command(
        ROOT / "in.mp4", ROOT / "out.mov", {}, "4:2:0", 10,
        resize=spec)
    njoined = " ".join(str(x) for x in ncmd)
    qjoined = " ".join(str(x) for x in qcmd)
    record("resize.nvenc command args",
           "--vpp-resize algo=lanczos" in njoined
           and "--output-res 1920x1080" in njoined
           and "--vpp-resize-mode" not in njoined)
    record("resize.qsv command args",
           "--vpp-resize algo=lanczos" in qjoined
           and "--output-res 1920x1080" in qjoined
           and "--vpp-resize-mode quality" in qjoined)
    plain, _, _ = nv.command(
        ROOT / "in.mp4", ROOT / "out.mov", {}, "4:2:0", 10)
    record("resize.no option -> no scaling args",
           "--vpp-resize" not in " ".join(str(x) for x in plain))
    record("resize.helper rejects VCE",
           not _resize_kind_supported("vceenc"),
           "VCE has no entry point")

    from encoders.hwdecode_ffmpeg import (
        DecodePlan,
        Transfer,
        apply_decode_plan,
        plan_software,
    )

    def _cmd_with_filter(plan, spec):
        cmd = ["ffmpeg", "-i", "in.mp4", "-c:v", "libx265", "out.mov"]
        return " ".join(apply_decode_plan(cmd, plan, "in.mp4", resize=spec))

    cuda_plan = DecodePlan(
        substrate="ffmpeg_hw", decoder="hevc_cuvid", hwaccel="cuda",
        target_format="nv12",
        transfer=Transfer(
            format="nv12", filters=["hwdownload,format=nv12"], direct=True),
    )
    qsv_plan = DecodePlan(
        substrate="ffmpeg_hw", decoder="hevc_qsv", hwaccel="qsv",
        target_format="nv12",
        transfer=Transfer(
            format="nv12", filters=["hwdownload,format=nv12"], direct=True),
    )
    record("resize.x265 cuda hw filter",
           "scale_cuda" in _cmd_with_filter(cuda_plan, spec)
           and "w=1920" in _cmd_with_filter(cuda_plan, spec),
           _cmd_with_filter(cuda_plan, spec))
    record("resize.x265 qsv hw filter",
           "vpp_qsv" in _cmd_with_filter(qsv_plan, spec)
           and "scale_mode=hq" in _cmd_with_filter(qsv_plan, spec),
           _cmd_with_filter(qsv_plan, spec))
    sw_plan = plan_software("test", codec="hevc", chroma="4:2:0", depth=10)
    record("resize.x265 software fallback filter",
           "scale=w=1920:h=1080:flags=lanczos"
           in _cmd_with_filter(sw_plan, spec),
           _cmd_with_filter(sw_plan, spec))


def _resize_kind_supported(kind: str) -> bool:
    from encoders.resize import ResizeSpec, resize_args
    try:
        resize_args(kind, ResizeSpec(1280, 720))
        return True
    except ValueError:
        return False

