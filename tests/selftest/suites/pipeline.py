#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 真实管线集成 + 故障注入 (Sony/DJI/经典路径 x NVENC/QSV)。"""

from __future__ import annotations

import json
import shutil
from ..paths import IN_DIR
from ..paths import OUT_DIR
from ..paths import ROOT
from ..paths import WORK
from ..fixtures.media import _run_1kt
from ..fixtures.media import _stage_inputs
from ..paths import ffprobe_json
from ..paths import record
from ..paths import section
from ..paths import sh

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
