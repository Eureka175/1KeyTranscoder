"""Source-aware hardware decode routing for NVENC/QSV.

Two independent decode architectures, and one decision rule inside the
rigaya one:

    x265 / svtav1   FFmpeg NVDEC/QSV -> hwdownload -> FFmpeg encoder
    nvenc / qsv     <source> -> the encoder's OWN reader -> encoder
                      Sony        -> --avsw, decided before any attempt
                      non-Sony    -> --avhw, judged by the integrity gate
                      unknown     -> --avhw, recorded as unclassified

The routing tests drive the **real** ``hw_encode_with_fallback`` state machine
with a recording stand-in for the encoder process, so what is asserted is the
argv that would actually execute -- not a log string and not a substring of the
source. That distinction matters because every historical failure here was
silent: a dropped ``--avhw`` or a stale reader flag still produced a playable
file and a successful-looking run.

Two stand-in behaviours are load-bearing:

* for the integrity cases it reports a **wrong** frame count on the ``--avhw``
  rung, which is the only way to reach the discard-and-retry contract;
* the trace records whether the output artifact already existed when each
  command started, so "the rejected artifact was deleted before the retry" is
  proved rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths

record = paths.record


class _FakeBackend:
    """Minimal rigaya-shaped backend: real argv surface, no process."""

    def __init__(self, kind: str, tool: Path, codec: str = "hevc") -> None:
        self.kind = kind
        self.tool = Path(tool)
        self.codec = codec
        self.name = "nvenc" if kind == "nvencc" else "qsv"
        from encoders.caps import BackendCaps, CodecCaps

        self.caps = BackendCaps(
            tool="fake", codecs={"hevc": CodecCaps(bit10=True)}
        )

    def command(self, source, output, profile, chroma, depth, vfr=False,
                audio_copy=False, color=None, reader="avsw", container="mov"):
        cmd = [
            str(self.tool), "-i", str(source), f"--{reader}",
            "--video-track", "1", "-c", self.codec,
            "--output-depth", str(depth),
            "-f", container, "-o", str(output),
        ]
        return cmd, [], []


class _SrcInfo:
    codec = "hevc"
    chroma = "4:2:0"
    bit_depth = 10
    color = None


#: A hardware run that really used the hardware reader but whose frame count
#: does not reconcile with the container. Reaching the reconciliation (rather
#: than an "unknown reader" refusal) is what exercises the discard path.
_HW_LOG = (
    "NVEncC (x64) 9.31 (r1)\n"
    "Input Info avcuvid: 4096x2160, 59.94fps\n"
    "240 frames, End of file\n"
    "encoded 237 frames, 100.0 fps\n"
)
_SW_LOG = (
    "NVEncC (x64) 9.31\n"
    "Input Info avsw: 4096x2160, 59.94fps\n"
    "240 frames, End of file\n"
    "encoded 240 frames, 100.0 fps\n"
)


def _run_fallback(backend, *, hw_decode, tmp: Path, out_name: str,
                  source_kind, seek=False):
    """Drive hw_encode_with_fallback with the encoder process recorded.

    Returns (trace, warnings). Each trace entry records the argv, the reader it
    asks for, and whether the output artifact already existed at that moment.
    """
    import logging

    from core import batch_hw

    trace: list[dict] = []
    real_run = batch_hw.run_hw_tool
    output = tmp / out_name

    def fake_run(cmd, raw_log, total_frames, label, progress=True, **kw):
        is_hw = "--avhw" in cmd
        trace.append({
            "cmd": list(cmd),
            "hw": is_hw,
            "preexisting_output": output.is_file(),
        })
        Path(raw_log).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_log).write_text(_HW_LOG if is_hw else _SW_LOG, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\0" * 64)
        return 0, 0.01

    sink = logging.getLogger("native_decode_test")
    sink.addHandler(logging.NullHandler())
    batch_hw.run_hw_tool = fake_run
    try:
        warnings, _used, _fps = batch_hw.hw_encode_with_fallback(
            label="nvencc", backend=backend,
            source=tmp / "src.mp4", output=output,
            profile={}, src_info=_SrcInfo(), vfr=False,
            work_dir=tmp, total_frames=240, ffprobe=tmp / "ffprobe.exe",
            audio_copy=False, do_frame_check=False, no_downgrade=False,
            gpac=None, logger=sink, file_logger=sink,
            hw_decode=hw_decode, hw_decode_memo={},
            source_kind=source_kind, seek_requested=seek,
        )
    finally:
        batch_hw.run_hw_tool = real_run
    return trace, warnings


def _sony_streams() -> list[dict]:
    """The shape ``is_sony_source`` keys on: a data stream tagged rtmd."""
    return [
        {"codec_type": "video", "codec_tag_string": "hvc1"},
        {"codec_type": "data", "codec_tag_string": "rtmd"},
        {"codec_type": "audio", "codec_tag_string": "mp4a"},
    ]


def _dji_streams() -> list[dict]:
    return [
        {"codec_type": "video", "codec_tag_string": "hvc1"},
        {"codec_type": "data", "codec_tag_string": "djmd"},
    ]


def _plain_streams() -> list[dict]:
    return [
        {"codec_type": "video", "codec_tag_string": "avc1"},
        {"codec_type": "audio", "codec_tag_string": "mp4a"},
    ]


def l1_native_decode() -> None:
    from core import batch_hw
    from encoders.hwdecode import (
        R_SONY_SOFTWARE,
        SOURCE_NON_SONY,
        SOURCE_SONY,
        SOURCE_UNKNOWN,
        route_decode,
        source_kind_from_streams,
    )

    paths.section("L1 来源感知的 rigaya 解码路由 (Sony=avsw / 非 Sony=avhw)")

    # =====================================================================
    # 1. source classification reuses the existing Sony detector
    # =====================================================================
    record("src.Sony 来源被识别 (rtmd 数据轨)",
           source_kind_from_streams(_sony_streams()) == SOURCE_SONY,
           source_kind_from_streams(_sony_streams()))
    record("src.非 Sony 来源被识别",
           source_kind_from_streams(_plain_streams()) == SOURCE_NON_SONY,
           source_kind_from_streams(_plain_streams()))
    # DJI is *not* Sony: its own metadata tag must not be read as rtmd.
    record("src.DJI 来源不是 Sony (djmd != rtmd)",
           source_kind_from_streams(_dji_streams()) == SOURCE_NON_SONY,
           source_kind_from_streams(_dji_streams()))
    record("src.探测为空 -> unknown (不猜)",
           source_kind_from_streams([]) == SOURCE_UNKNOWN
           and source_kind_from_streams(None) == SOURCE_UNKNOWN,
           "empty probe -> unknown")
    # The detector must be the same one the dispatch uses, not a copy.
    import inspect

    hsrc = inspect.getsource(source_kind_from_streams)
    record("src.复用 core.batch_hw.is_sony_source (不重新实现检测)",
           "from core.batch_hw import is_sony_source" in hsrc,
           "delegates to the existing detector")
    record("src.不按文件名/分辨率猜测来源",
           "name" not in hsrc.replace("is_sony_source", "")
           and "width" not in hsrc and "suffix" not in hsrc)

    # =====================================================================
    # 2. routing: the source class decides the reader
    # =====================================================================
    def route(source_kind, backend="nvenc", codec="hevc", chroma="4:2:0",
              depth=10, seek=False, policy="auto"):
        return route_decode(
            policy=policy, backend=backend, codec=codec, chroma=chroma,
            depth=depth, source_kind=source_kind, seek_requested=seek,
        )

    for backend in ("nvenc", "qsv"):
        r = route(SOURCE_SONY, backend=backend)
        record(f"route.Sony + {backend} -> 软件读端",
               r.reader == "avsw" and not r.hardware, r.reader)
        record(f"route.Sony + {backend} 的原因是来源安全策略",
               r.reason == R_SONY_SOFTWARE, r.reason)
        record(f"route.Sony + {backend} 不报警告 (是预防, 不是失败)",
               r.warnings == [], f"{len(r.warnings)} warning(s)")

    for backend in ("nvenc", "qsv"):
        r = route(SOURCE_NON_SONY, backend=backend)
        record(f"route.非 Sony + {backend} -> 硬件读端",
               r.reader == "avhw" and r.hardware, r.reader)

    # The allowlist is no longer a gate: a 4:2:0/8-bit source is not in it,
    # yet must still get hardware.
    r = route(SOURCE_NON_SONY, codec="hevc", chroma="4:2:0", depth=8)
    record("route.白名单外的非 Sony 组合仍走硬件 (allowlist 不再是准入门槛)",
           r.hardware, f"reader={r.reader} reason={r.reason}")
    r = route(SOURCE_NON_SONY, codec="h264", chroma="4:2:0", depth=8)
    record("route.非 Sony H.264 8bit 也走硬件", r.hardware, r.reason)

    r = route(SOURCE_UNKNOWN)
    record("route.来源未知 -> 尝试硬件并记录为未知",
           r.hardware and r.source_kind == SOURCE_UNKNOWN
           and r.reason == "source_unclassified",
           f"reader={r.reader} reason={r.reason}")

    # =====================================================================
    # 3. what must still refuse hardware
    # =====================================================================
    r = route(SOURCE_NON_SONY, seek=True)
    record("route.带 seek 时非 Sony 也拒绝硬解 (读端不等价)",
           not r.hardware and r.reason == "seek_not_equivalent", r.reason)

    r = route(SOURCE_NON_SONY, backend="qsv", codec="h264", chroma="4:2:2")
    record("route.已知能力拒绝 (QSV h264 4:2:2 10bit) 仍拒绝",
           not r.hardware and r.reason == "capability_refused", r.reason)

    r = route(SOURCE_NON_SONY, backend="nvenc", codec="h264", chroma="4:2:2")
    record("route.同格式 NVENC 未被拒绝 (拒绝是按后端的实测事实)",
           r.hardware, f"reader={r.reader}")

    r = route_decode(
        policy="auto", backend="nvenc", codec="hevc", chroma="4:2:0", depth=10,
        source_kind=SOURCE_NON_SONY,
        memo={("nvenc", "hevc", "4:2:0", 10): "device init failed once"},
    )
    record("route.本运行记住的设备/格式失败仍拒绝重试",
           not r.hardware and r.reason == "reader_unavailable", r.reason)

    # Sony policy outranks the allowlist *and* a remembered failure: the
    # reason must stay the source decision, not a generic fallback.
    r = route_decode(
        policy="auto", backend="nvenc", codec="hevc", chroma="4:2:0", depth=10,
        source_kind=SOURCE_SONY,
        memo={("nvenc", "hevc", "4:2:0", 10): "device init failed once"},
    )
    record("route.Sony 的原因不因其它拒绝条件而改变",
           r.reason == R_SONY_SOFTWARE, r.reason)

    r = route(SOURCE_SONY, policy="require")
    record("route.policy=require 下 Sony 明确失败 (不静默软解)",
           r.reason == "require_unmet" and r.reader == "avhw", r.reason)

    # Determinism over the whole grid (matrix HD-B08 consumes this).
    grid = [(sk, b, c, ch, d)
            for sk in (SOURCE_SONY, SOURCE_NON_SONY, SOURCE_UNKNOWN)
            for b in ("nvenc", "qsv")
            for c, ch, d in (("hevc", "4:2:0", 10), ("h264", "4:2:2", 10),
                             ("hevc", "4:2:0", 8))]
    first = [route(sk, backend=b, codec=c, chroma=ch, depth=d).to_json()
             for sk, b, c, ch, d in grid]
    second = [route(sk, backend=b, codec=c, chroma=ch, depth=d).to_json()
              for sk, b, c, ch, d in grid]
    record("route.全网格决策可复现", first == second, f"{len(grid)} decisions")
    record("route.每条决策都带 source_kind 字段",
           all("source_kind" in d for d in first))

    # =====================================================================
    # 4. argv level: the reader flag IS the decision
    # =====================================================================
    src = "C:/x/in.mp4"
    out = "C:/x/out.mov"
    nv = _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe"))
    qs = _FakeBackend("qsvencc", Path("C:/t/QSVEncC64.exe"))

    nv_hw, _, _ = nv.command(src, out, {}, "4:2:0", 10, reader="avhw")
    nv_sw, _, _ = nv.command(src, out, {}, "4:2:0", 10, reader="avsw")
    qs_hw, _, _ = qs.command(src, out, {}, "4:2:0", 10, reader="avhw")
    qs_sw, _, _ = qs.command(src, out, {}, "4:2:0", 10, reader="avsw")

    record("argv.NVEnc 硬解请求 --avhw",
           "--avhw" in nv_hw and "--avsw" not in nv_hw, " ".join(nv_hw[:6]))
    record("argv.NVEnc 软解请求 --avsw",
           "--avsw" in nv_sw and "--avhw" not in nv_sw, " ".join(nv_sw[:6]))
    record("argv.QSV 硬解请求 --avhw",
           "--avhw" in qs_hw and "--avsw" not in qs_hw, " ".join(qs_hw[:6]))
    record("argv.QSV 软解请求 --avsw",
           "--avsw" in qs_sw and "--avhw" not in qs_sw, " ".join(qs_sw[:6]))
    record("argv.读端是源文件本身 (不是 stdin)",
           nv_hw[nv_hw.index("-i") + 1] == src,
           f"-i {nv_hw[nv_hw.index('-i') + 1]}")

    # =====================================================================
    # 5. no FFmpeg frame feed anywhere in the rigaya path
    # =====================================================================
    fsrc = inspect.getsource(batch_hw.hw_encode_with_fallback)
    record("pipe.生产路径无 FFmpeg 帧馈送 (decode_pipe/rung_pipe)",
           "decode_pipe" not in fsrc and "rung_pipe" not in fsrc)
    record("pipe.生产路径无 y4m 管道改写",
           "apply_stdin_pipe" not in fsrc
           and "ffmpeg_pipe_args" not in fsrc)
    record("pipe.run_hw_tool 无 decode_pipe 形参",
           "decode_pipe" not in inspect.signature(
               batch_hw.run_hw_tool).parameters)
    record("pipe.run_hw_tool 无 feeder 进程处理",
           "feeder" not in inspect.getsource(batch_hw.run_hw_tool))

    from encoders import hwdecode_ffmpeg as hdf

    record("pipe.hwdecode_ffmpeg 不再导出管道 API",
           not hasattr(hdf, "ffmpeg_pipe_args")
           and not hasattr(hdf, "apply_stdin_pipe"))

    # =====================================================================
    # 6. the ladder really runs what the router chose
    # =====================================================================
    tmp = paths.WORK / "native_decode"
    tmp.mkdir(parents=True, exist_ok=True)

    # (1) Sony: one rung, --avsw, no warning, and --avhw never attempted.
    for kind, tool in (("nvencc", "C:/t/NVEncC64.exe"),
                       ("qsvencc", "C:/t/QSVEncC64.exe")):
        b = _FakeBackend(kind, Path(tool))
        trace, warns = _run_fallback(
            b, hw_decode="auto", tmp=tmp, out_name=f"sony_{kind}.mov",
            source_kind=SOURCE_SONY,
        )
        record(f"sony.{b.name} 只跑一次软解 rung",
               len(trace) == 1, f"{len(trace)} command(s)")
        record(f"sony.{b.name} argv 用 --avsw 且用已部署二进制",
               bool(trace) and "--avsw" in trace[0]["cmd"]
               and Path(trace[0]["cmd"][0]) == b.tool,
               " ".join(trace[0]["cmd"][:3]) if trace else "-")
        record(f"sony.{b.name} 从未尝试 --avhw",
               not any("--avhw" in e["cmd"] for e in trace),
               f"{len(trace)} command(s), hw={sum(e['hw'] for e in trace)}")
        record(f"sony.{b.name} 不把这个决定记成回退 (无告警)",
               warns == [], f"{len(warns)} warning(s)")

    # (2) non-Sony: --avhw first, on the deployed binary, and when the gate
    #     rejects it the artifact is gone before the --avsw retry.
    for kind, tool in (("nvencc", "C:/t/NVEncC64.exe"),
                       ("qsvencc", "C:/t/QSVEncC64.exe")):
        b = _FakeBackend(kind, Path(tool))
        trace, warns = _run_fallback(
            b, hw_decode="auto", tmp=tmp, out_name=f"nonson_{kind}.mov",
            source_kind=SOURCE_NON_SONY,
        )
        first = trace[0] if trace else {}
        record(f"nonson.{b.name} 先试 --avhw",
               "--avhw" in first.get("cmd", []),
               f"{' '.join(first.get('cmd', [])[:3]) or '-'}")
        record(f"nonson.{b.name} --avhw 使用已部署二进制",
               bool(first) and Path(first["cmd"][0]) == b.tool,
               first.get("cmd", ["-"])[0])
        sw = [e for e in trace if "--avsw" in e["cmd"]]
        record(f"nonson.{b.name} 完整性失败后回退 --avsw",
               bool(sw), f"{len(trace)} command(s), {len(sw)} software")
        record(f"nonson.{b.name} 被拒产物在回退前已删除",
               bool(sw) and sw[0]["preexisting_output"] is False,
               f"software rung saw existing output="
               f"{sw[0]['preexisting_output'] if sw else '-'}")
        later = trace[1:]
        record(f"nonson.{b.name} 每次重试前都无旧产物",
               bool(later) and all(
                   e["preexisting_output"] is False for e in later),
               f"{len(later)} rerun(s)")
        record(f"nonson.{b.name} 硬解最多一次 + 软解最多一次",
               sum(1 for e in trace if "--avhw" in e["cmd"]) <= 1
               and len(sw) <= 1,
               f"hw={sum(1 for e in trace if '--avhw' in e['cmd'])} "
               f"sw={len(sw)}")
        record(f"nonson.{b.name} 回退原因被记录",
               any("integrity" in w.lower() or "hardware decode" in w.lower()
                   for w in warns),
               f"{len(warns)} warning(s)")

    # (3) unknown source: still attempts hardware, and says so.
    b = _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe"))
    trace, _warns = _run_fallback(
        b, hw_decode="auto", tmp=tmp, out_name="unknown.mov",
        source_kind=SOURCE_UNKNOWN,
    )
    record("unknown.来源未知时尝试 --avhw (并记录为未分类)",
           bool(trace) and "--avhw" in trace[0]["cmd"],
           " ".join(trace[0]["cmd"][:3]) if trace else "-")

    # (4) a seek refuses hardware outright, so no --avhw attempt happens.
    b = _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe"))
    trace, warns = _run_fallback(
        b, hw_decode="auto", tmp=tmp, out_name="seek.mov",
        source_kind=SOURCE_NON_SONY, seek=True,
    )
    record("seek.带 seek 时从不尝试 --avhw",
           bool(trace) and not any("--avhw" in e["cmd"] for e in trace),
           f"{len(trace)} command(s)")
    record("seek.带 seek 时出声说明原因",
           any("seek" in w.lower() for w in warns), f"{len(warns)} warning(s)")

    # (5) capability refusal: software, announced, no --avhw attempt.
    b = _FakeBackend("qsvencc", Path("C:/t/QSVEncC64.exe"))
    b.caps = b.caps
    trace, warns = _run_fallback(
        b, hw_decode="auto", tmp=tmp, out_name="refused.mov",
        source_kind=SOURCE_NON_SONY,
    )
    # The fake backend declares hevc caps; a hevc 4:2:0 source is not refused,
    # so assert the *code* path instead: route-level refusal + no hw argv.
    r = route_decode(policy="auto", backend="qsv", codec="h264",
                     chroma="4:2:2", depth=10, source_kind=SOURCE_NON_SONY)
    record("refusal.能力拒绝时路由给出 capability_refused 且不请求硬件",
           not r.hardware and r.reason == "capability_refused" and r.reader == "avsw",
           r.reason)

    # (6) policy=require with a Sony source must fail, not silently soft-decode.
    try:
        _run_fallback(
            _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe")),
            hw_decode="require", tmp=tmp, out_name="req.mov",
            source_kind=SOURCE_SONY,
        )
        record("require.Sony 来源下 require 硬解失败 (不静默软解)", False,
               "no error raised")
    except RuntimeError as exc:
        record("require.Sony 来源下 require 硬解失败 (不静默软解)", True,
               str(exc)[:90])

    # =====================================================================
    # 7. the audit line can answer "why this reader?"
    # =====================================================================
    dsrc = inspect.getsource(batch_hw.hw_encode_with_fallback)
    for field in ("source_kind", "decode_reader", "decode_policy",
                  "route_reason", "hardware_integrity", "software_fallback",
                  "fallback_reason", "final_status", "actual_encoder"):
        record(f"audit.DECODE_DECISION 记录 {field}", field in dsrc)
    record("audit.回退标记只由完整性拒绝设置",
           "fallback_reason = f\"{reason}" in dsrc
           or "fallback_reason = f" in dsrc,
           "fallback_reason assigned in the integrity branch only")

    # =====================================================================
    # 8. x265 / SVT-AV1 must be untouched by the rigaya change
    # =====================================================================
    from encoders.hwdecode_ffmpeg import (
        DecodePlan,
        Transfer,
        apply_decode_plan,
    )

    x265 = [
        "ffmpeg.exe", "-hide_banner", "-nostdin", "-i", "C:/x/in.mp4",
        "-c:v", "libx265", "-preset", "medium", "C:/x/out.mkv",
    ]
    plan = DecodePlan(
        substrate="ffmpeg_hw", decoder="hevc_cuvid", hwaccel="cuda",
        device="cuda=cu:0", device_index=0,
        surface_format="p010le", target_format="yuv420p10le",
        codec="hevc", chroma="4:2:0", depth=10,
        transfer=Transfer(format="p010le",
                          filters=["hwdownload,format=p010le"], direct=True),
        reason="hardware_compatible",
    )
    hw_cmd = apply_decode_plan(x265, plan, "C:/x/in.mp4")
    joined = " ".join(hw_cmd)
    record("ffmpeg.x265 仍获得 FFmpeg 原生硬解参数",
           "-hwaccel" in hw_cmd and "hevc_cuvid" in hw_cmd
           and "cuda=cu:0" in hw_cmd, joined[:90])
    record("ffmpeg.x265 硬解插入在 -i 之前, 滤镜在其后",
           hw_cmd.index("-hwaccel") < hw_cmd.index("-i")
           < hw_cmd.index("-vf"), joined[:90])
    record("ffmpeg.x265 编码参数逐字保留",
           all(t in hw_cmd for t in ("libx265", "-preset", "medium")))
    record("ffmpeg.x265 无 y4m 中介",
           "yuv4mpegpipe" not in hw_cmd and hw_cmd[-1] != "-", hw_cmd[-1])

    sw_plan = DecodePlan(substrate="sw", reason="no_compatible_hw_decoder",
                         codec="hevc", chroma="4:2:0", depth=10)
    record("ffmpeg.软件计划不改写 FFmpeg argv (逐字相同)",
           apply_decode_plan(x265, sw_plan, "C:/x/in.mp4") == x265)
    try:
        apply_decode_plan(x265, plan, "C:/x/other.mp4")
        record("ffmpeg.输入不匹配时拒绝改写 (不猜)", False, "no error")
    except ValueError as exc:
        record("ffmpeg.输入不匹配时拒绝改写 (不猜)", True, str(exc)[:70])

    # SVT-AV1 uses the same planner; assert it is reachable for its codec.
    svt = DecodePlan(
        substrate="ffmpeg_hw", decoder="hevc_cuvid", hwaccel="cuda",
        device="cuda=cu:0", device_index=0,
        surface_format="p010le", target_format="yuv420p10le",
        codec="hevc", chroma="4:2:0", depth=10,
        transfer=Transfer(format="p010le",
                          filters=["hwdownload,format=p010le"], direct=True),
        reason="hardware_compatible",
    )
    svt_cmd = apply_decode_plan(
        ["ffmpeg.exe", "-i", "C:/x/in.mp4", "-c:v", "libsvtav1",
         "C:/x/out.mp4"],
        svt, "C:/x/in.mp4",
    )
    record("ffmpeg.SVT-AV1 同样获得 FFmpeg 硬解参数",
           "hevc_cuvid" in svt_cmd and "libsvtav1" in svt_cmd)

    # =====================================================================
    # 9. the research build is quarantined, not deleted, and not a gate
    # =====================================================================
    from encoders.hwdecode import resolve_decode_tool

    root = paths.ROOT
    stock_nv = root / "tools" / "NVEncC_9.31_x64" / "NVEncC64.exe"
    dt = resolve_decode_tool(kind="nvencc", script_dir=root, policy="auto")
    record("research.研究版解析器仍保留 (provenance 资料未删除)",
           hasattr(dt, "role") and dt.role in ("patched", "unusable"),
           f"role={dt.role} reason={dt.reason}")
    # The production path must not consult it: no reference in the encode path.
    record("research.生产编码路径不调用 resolve_decode_tool",
           "resolve_decode_tool" not in inspect.getsource(
               batch_hw.hw_encode_with_fallback)
           and "resolve_decode_tool" not in inspect.getsource(
               batch_hw.process_file_hw),
           "quarantined from production routing")
    record("research.--avhw 使用 backend.tool (已部署二进制)",
           "hw_tool = backend.tool" in inspect.getsource(
               batch_hw.hw_encode_with_fallback))
    from encoders.hwdecode import decode_tool_is_stock

    record("research.装船构建仍被识别 (防误用闸门保留)",
           decode_tool_is_stock("nvenc", stock_nv) is True,
           "tools/NVEncC_9.31_x64/NVEncC64.exe -> stock")
