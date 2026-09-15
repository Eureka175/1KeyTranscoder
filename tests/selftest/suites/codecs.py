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
