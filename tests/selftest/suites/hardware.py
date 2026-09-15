#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 硬件/延时补偿集成: channel-sync P1 端到端 + 算法级 (无转码)。"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from ..paths import FFMPEG
from ..paths import IN_DIR
from ..paths import OUT_DIR
from ..paths import WORK
from ..fixtures.media import _run_1kt
from ..paths import ffprobe_json
from ..paths import record
from ..paths import section
from ..paths import sh

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
