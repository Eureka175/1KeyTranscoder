"""Native rigaya hardware decode (`--avhw`) vs the FFmpeg decode route.

This suite replaces the former ``decode_pipe`` suite, which asserted the
architecture this project has since removed: FFmpeg decoding to y4m and
feeding NVEncC/QSVEncC through stdin. That route is gone, and asserting on
its absence is now as important as asserting on the new route.

Two independent decode architectures must both stay intact:

    x265 / SVT-AV1   FFmpeg NVDEC/QSV -> hwdownload -> libx265/libsvtav1
    NVENC / QSV      <source file> -> NVEncC/QSVEncC --avhw -> encoder

The routing tests drive the **real** ``hw_encode_with_fallback`` state machine
with a recording stand-in for the encoder process, so what is asserted is the
argv that would actually be executed -- not a log string and not a substring
of the source. That distinction matters because every historical failure mode
here was silent: a stale reader flag or a dropped ``--avhw`` still produced a
playable file and a successful-looking run.

The stand-in deliberately reports a **wrong** frame count for the ``--avhw``
rung, because that is the only way to reach the discard-and-retry contract
that protects against rigaya's head-loss defect. The trace records whether the
artifact still existed when the software rung started, so "the corrupt output
was deleted first" is proved rather than assumed.
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
        self.explicit_tool = None
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


# A hardware run whose reader really was the hardware one, but whose frame
# count does not reconcile with the container. Reaching that check (rather
# than an "unknown reader" refusal) is what makes the test exercise the
# discard path instead of an argv-shape shortcut.
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


def _run_fallback(backend, *, hw_decode, script_dir, tmp: Path, out_name: str):
    """Drive hw_encode_with_fallback with the encoder process recorded.

    The log written for an ``--avhw`` rung claims the hardware reader ran but
    reports fewer frames than the container holds, so the integrity gate
    rejects it. The trace records, at the moment each command starts, whether
    the output artifact already existed -- that is what proves the rejected
    artifact was removed before the software retry wrote anything.
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
            script_dir=script_dir,
        )
    finally:
        batch_hw.run_hw_tool = real_run
    return trace, warnings


def l1_native_decode() -> None:
    from core import batch_hw
    from encoders.hwdecode import (
        decode_tool_is_stock,
        resolve_decode_tool,
        route_decode,
    )

    paths.section("L1 原生 rigaya 硬解路由 (--avhw / --avsw)")

    # --- the patch is what makes --avhw safe, and it is verified by hash ---
    root = paths.ROOT
    stock_nv = root / "tools" / "NVEncC_9.31_x64" / "NVEncC64.exe"

    dt_nv = resolve_decode_tool(kind="nvencc", script_dir=root, policy="auto")
    dt_qs = resolve_decode_tool(kind="qsvencc", script_dir=root, policy="auto")
    builds_present = bool(dt_nv.usable and dt_qs.usable)

    record(
        "native.只有 patched 构建可获得时 --avhw 才可用",
        (not dt_nv.usable)
        or "tools/avhw" in str(dt_nv.path).replace("\\", "/"),
        f"nvenc usable={dt_nv.usable} path={dt_nv.path}",
    )
    record(
        "native.构建缺失/哈希不符 -> not_proven (不是可用)",
        builds_present
        or (dt_nv.reason == "not_proven" and dt_qs.reason == "not_proven"),
        f"nvenc={dt_nv.reason} qsv={dt_qs.reason}",
    )
    record(
        "native.装船构建被识别为 stock (防误用闸门)",
        decode_tool_is_stock("nvenc", stock_nv) is True,
        "tools/NVEncC_9.31_x64/NVEncC64.exe -> stock",
    )

    # --- argv-level: the reader flag IS the decode decision ---------------
    src = "C:/x/in.mp4"
    out = "C:/x/out.mov"
    nv = _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe"))
    qs = _FakeBackend("qsvencc", Path("C:/t/QSVEncC64.exe"))

    nv_hw, _, _ = nv.command(src, out, {}, "4:2:0", 10, reader="avhw")
    nv_sw, _, _ = nv.command(src, out, {}, "4:2:0", 10, reader="avsw")
    qs_hw, _, _ = qs.command(src, out, {}, "4:2:0", 10, reader="avhw")

    record("native.NVEnc 硬解 argv 请求 --avhw",
           "--avhw" in nv_hw and "--avsw" not in nv_hw, " ".join(nv_hw[:6]))
    record("native.NVEnc 软解 argv 请求 --avsw",
           "--avsw" in nv_sw and "--avhw" not in nv_sw, " ".join(nv_sw[:6]))
    record("native.QSV 硬解 argv 请求 --avhw",
           "--avhw" in qs_hw and "--avsw" not in qs_hw, " ".join(qs_hw[:6]))
    record("native.读端是源文件本身 (不是 stdin)",
           nv_hw[nv_hw.index("-i") + 1] == src
           and "-" not in (nv_hw[nv_hw.index("-i") + 1],),
           f"-i {nv_hw[nv_hw.index('-i') + 1]}")

    # --- the tool substitution that carries the decode binary -------------
    patched = Path("C:/t/patched/NVEncC64.exe")
    got = batch_hw.use_decode_binary(nv_hw, nv.tool, patched)
    record("native.硬解 rung 替换为 patched 解码二进制",
           Path(got[0]) == patched, f"argv[0]={got[0]}")
    record("native.替换只改 argv[0], 其余参数逐字保留",
           got[1:] == nv_hw[1:], " ".join(got[1:8]))
    record("native.同一二进制时不做改动",
           batch_hw.use_decode_binary(nv_hw, nv.tool, nv.tool) == nv_hw)
    try:
        batch_hw.use_decode_binary(
            ["C:/other/tool.exe", "-i", src], nv.tool, patched
        )
        record("native.期望二进制不符时拒绝替换 (不猜)", False, "no error")
    except ValueError as exc:
        record("native.期望二进制不符时拒绝替换 (不猜)", True, str(exc)[:70])

    # --- no FFmpeg feeder anywhere in the production path -----------------
    import inspect

    fsrc = inspect.getsource(batch_hw.hw_encode_with_fallback)
    record("native.生产路径无 FFmpeg 帧馈送 (decode_pipe/rung_pipe)",
           "decode_pipe" not in fsrc and "rung_pipe" not in fsrc)
    record("native.生产路径无 y4m 管道改写",
           "apply_stdin_pipe" not in fsrc
           and "ffmpeg_pipe_args" not in fsrc)
    record("native.run_hw_tool 无 decode_pipe 形参",
           "decode_pipe" not in inspect.signature(
               batch_hw.run_hw_tool).parameters)
    record("native.run_hw_tool 无 feeder 进程处理",
           "feeder" not in inspect.getsource(batch_hw.run_hw_tool))

    from encoders import hwdecode_ffmpeg as hdf

    record("native.hwdecode_ffmpeg 不再导出管道 API",
           not hasattr(hdf, "ffmpeg_pipe_args")
           and not hasattr(hdf, "apply_stdin_pipe"))

    # --- the ladder actually runs the chosen reader -----------------------
    tmp = paths.WORK / "native_decode"
    tmp.mkdir(parents=True, exist_ok=True)
    cache_key = ("nvencc", str(tmp), "")

    # (1) No patched build: software decode on the shipped build, announced.
    batch_hw._HW_TOOL_CACHE.pop(cache_key, None)
    trace, warns = _run_fallback(
        _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe")),
        hw_decode="auto", script_dir=tmp, tmp=tmp, out_name="a.mov",
    )
    record("native.无 patched 构建时只跑一次软解 rung",
           len(trace) == 1, f"{len(trace)} command(s)")
    record("native.无 patched 构建时 argv 用 --avsw 与装船构建",
           trace and "--avsw" in trace[0]["cmd"]
           and Path(trace[0]["cmd"][0]) == Path("C:/t/NVEncC64.exe"),
           " ".join(trace[0]["cmd"][:3]) if trace else "-")
    record("native.该情形有明确告警 (不静默)",
           any("hardware decode not used" in w or "not_proven" in w
               for w in warns),
           f"{len(warns)} warning(s)")

    # (2) A resolvable decoder binary: --avhw first, integrity gate rejects,
    #     corrupt artifact deleted, --avsw retry on the shipped build.
    patched_bin = tmp / "patched" / "NVEncC64.exe"
    patched_bin.parent.mkdir(parents=True, exist_ok=True)
    patched_bin.write_bytes(b"stub")
    batch_hw._HW_TOOL_CACHE[cache_key] = (patched_bin, "test injection")
    try:
        backend = _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe"))
        trace, warns = _run_fallback(
            backend, hw_decode="auto", script_dir=tmp, tmp=tmp,
            out_name="b.mov",
        )
        first = trace[0] if trace else {}
        record("native.可用解码二进制时先试 --avhw",
               "--avhw" in first.get("cmd", []),
               f"{' '.join(first.get('cmd', [])[:3]) or '-'}")
        record("native.硬解 rung 使用 patched 二进制",
               first and Path(first["cmd"][0]) == patched_bin,
               first.get("cmd", ["-"])[0])
        sw = [e for e in trace if "--avsw" in e["cmd"]]
        record("native.完整性闸门失败后回到 --avsw 重跑",
               bool(sw), f"{len(trace)} command(s), {len(sw)} software")
        record("native.软件重试用装船构建",
               bool(sw) and Path(sw[0]["cmd"][0]) == backend.tool,
               sw[0]["cmd"][0] if sw else "-")
        record("native.被拒硬解产物在软件重试前已删除",
               bool(sw) and sw[0]["preexisting_output"] is False,
               f"software rung saw existing output="
               f"{sw[0]['preexisting_output'] if sw else '-'}")
        # The real contract: every attempt that is *not* the first must start
        # from a state where the previous artifact is gone. The first rung
        # legitimately starts clean because nothing has run yet -- which is
        # exactly why the assertion is about reruns, not about all rungs.
        later = trace[1:]
        record("native.除首次外每次重试前都不存在旧产物",
               bool(later) and all(
                   e["preexisting_output"] is False for e in later
               ),
               f"{len(later)} rerun(s), "
               f"max preexisting="
               f"{max((e['preexisting_output'] for e in later), default=False)}")
        record("native.软解重试有上限 (硬解最多一次)",
               len([e for e in trace if "--avhw" in e["cmd"]]) <= 1
               and len(sw) <= 1,
               f"hw={len([e for e in trace if '--avhw' in e['cmd']])} "
               f"sw={len(sw)}")
        record("native.回退原因被记录",
               any("integrity" in w.lower() or "hardware decode" in w.lower()
                   for w in warns),
               f"{len(warns)} warning(s)")
    finally:
        batch_hw._HW_TOOL_CACHE.pop(cache_key, None)

    # --- --hw-decode require cannot be silently satisfied -----------------
    try:
        _run_fallback(
            _FakeBackend("nvencc", Path("C:/t/NVEncC64.exe")),
            hw_decode="require", script_dir=tmp, tmp=tmp, out_name="c.mov",
        )
        record("native.require 无可用硬解时硬失败", False, "no error raised")
    except RuntimeError as exc:
        record("native.require 无可用硬解时硬失败", True, str(exc)[:80])

    # --- routing still refuses what was measured unsafe -------------------
    r = route_decode(policy="auto", backend="nvenc", codec="hevc",
                     chroma="4:2:1", depth=10)
    record("native.未证明组合路由到软件 (not_proven)",
           not r.hardware and r.reason == "not_proven", r.reason)
    r2 = route_decode(policy="auto", backend="nvenc", codec="hevc",
                      chroma="4:2:0", depth=10, seek_requested=True)
    record("native.带 seek 时拒绝硬解 (读端不等价)",
           not r2.hardware and r2.reason == "seek_not_equivalent", r2.reason)

    # --- the x265/SVT-AV1 FFmpeg route must survive this change -----------
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
    record("native.x265 仍获得 FFmpeg 原生硬解参数",
           "-hwaccel" in hw_cmd and "hevc_cuvid" in hw_cmd
           and "cuda=cu:0" in hw_cmd, joined[:90])
    record("native.x265 硬解插入在 -i 之前, 滤镜在其后",
           hw_cmd.index("-hwaccel") < hw_cmd.index("-i")
           < hw_cmd.index("-vf"), joined[:90])
    record("native.x265 编码参数逐字保留",
           all(t in hw_cmd for t in ("libx265", "-preset", "medium")),
           " ".join(t for t in hw_cmd if t in ("libx265", "medium")))
    record("native.x265 无 y4m 中介",
           "yuv4mpegpipe" not in hw_cmd and hw_cmd[-1] != "-", hw_cmd[-1])

    sw_plan = DecodePlan(substrate="sw", reason="no_compatible_hw_decoder",
                         codec="hevc", chroma="4:2:0", depth=10)
    record("native.软件计划不改写 FFmpeg argv (逐字相同)",
           apply_decode_plan(x265, sw_plan, "C:/x/in.mp4") == x265)
    try:
        apply_decode_plan(x265, plan, "C:/x/other.mp4")
        record("native.输入不匹配时拒绝改写 (不猜)", False, "no error")
    except ValueError as exc:
        record("native.输入不匹配时拒绝改写 (不猜)", True, str(exc)[:70])

    # --- the matrix gate stays honest -------------------------------------
    # Asserted against the matrix harness's own provenance check, not against
    # a restatement of the resolver's answer: if the builds are absent the
    # harness must refuse, so the matrix can never be reported green on this
    # installation.
    try:
        from tests.hwdecode import sources as hw_sources

        prov = hw_sources.verify_provenance(strict=False)
        prov_ok = bool(prov.get("ok"))
        prov_note = (
            f"provenance ok={prov_ok}, "
            f"{len(prov.get('failures') or [])} failure(s)"
        )
    except Exception as exc:                       # noqa: BLE001
        prov = None
        prov_ok = False
        prov_note = f"harness provenance check failed to run: {exc}"
    if prov is None:
        record("native.矩阵 harness 可调用 (provenance 检查)",
               builds_present, prov_note)
    else:
        record(
            "native.矩阵 provenance 与构建缺失状态一致 (不虚报 PASS)",
            prov_ok is builds_present,
            f"{prov_note}; patched builds present={builds_present}",
        )
