"""Hardware decode as an FFmpeg y4m pipe into the rigaya encoders.

Pure-logic tests over argv construction plus the plan's recorded format. These
matter because every failure mode here is *silent*: a stale reader flag makes
the encoder reject the pipe and the ladder quietly falls back to a lossless
re-mux, so a run looks successful while the hardware decode never happened.
That actually occurred during development and is asserted against below.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths

record = paths.record


def l1_decode_pipe() -> None:
    from encoders.hwdecode_ffmpeg import (
        DecodePlan, Transfer, apply_stdin_pipe, ffmpeg_pipe_args,
    )

    paths.section("L1 FFmpeg 硬解 -> rigaya y4m 管道 (encoders/hwdecode_ffmpeg.py)")

    src = "C:/x/in.mp4"
    out = "C:/x/out.mov"
    base = [
        "NVEncC64.exe", "-i", src, "--avsw", "--video-track", "1",
        "-c", "hevc", "--output-depth", "10", "-f", "mov", "-o", out,
    ]

    # --- the two edits ---------------------------------------------------
    got = apply_stdin_pipe(base, src)
    record("pipe.读端改为 stdin (-i -)",
           "-i" in got and got[got.index("-i") + 1] == "-", " ".join(got[:6]))

    # The regression that mattered: `--avsw` is a *file-input* reader selector.
    # Left in place the encoder rejects the pipe, the ladder falls back to the
    # MP4Box strip, and the run still reports success with warnings>=1.
    record("pipe.文件读端选择器被移除 (--avsw/--avhw)",
           not any(t in ("--avsw", "--avhw") for t in got),
           " ".join(t for t in got if t.startswith("--av")))

    record("pipe.声明 --y4m", "--y4m" in got)
    record("pipe.其余参数保持不变",
           all(t in got for t in ("-c", "hevc", "--output-depth", "10")),
           " ".join(got))

    # --- refuses rather than guessing ------------------------------------
    try:
        apply_stdin_pipe(base, "C:/other.mp4")
        record("pipe.未知输入时拒绝改写 (不猜)", False, "no error raised")
    except ValueError as exc:
        record("pipe.未知输入时拒绝改写 (不猜)", True, str(exc)[:70])

    # --- y4m needs a *planar* format and -strict -1 -----------------------
    plan = DecodePlan(
        substrate="ffmpeg_hw", decoder="hevc_cuvid", hwaccel="cuda",
        device="cuda=cu:0", device_index=0,
        surface_format="p010le", target_format="yuv420p10le",
        codec="hevc", chroma="4:2:0", depth=10,
        transfer=Transfer(format="p010le",
                          filters=["hwdownload,format=p010le"], direct=True),
        reason="hardware_compatible",
    )
    args = ffmpeg_pipe_args(Path("ffmpeg.exe"), plan, src)
    joined = " ".join(args)

    # y4m has no semi-planar format; feeding p010le makes the muxer write a
    # malformed header ("y4m: failed to parse y4m header") and the encoder
    # exits 1 -- measured during development.
    fmt = args[args.index("-pix_fmt") + 1]
    record("pipe.y4m 使用 planar 格式而非 p010le",
           fmt == "yuv420p10le", f"-pix_fmt {fmt}")
    record("pipe.传递链追加 format=<planar> 转换",
           "format=yuv420p10le" in joined,
           [a for a in args if a.startswith("hwdownload") or "format=" in a][:1])
    record("pipe.-strict -1 (10-bit y4m 非官方格式)",
           args[args.index("-strict") + 1] == "-1")
    record("pipe.输出到 stdout 且为 yuv4mpegpipe",
           args[-1] == "-" and "yuv4mpegpipe" in args)
    record("pipe.携带硬解设备与解码器",
           "-hwaccel" in args and "hevc_cuvid" in args)

    # --- a software plan must not produce a pipe -------------------------
    sw = DecodePlan(substrate="sw", reason="no_compatible_hw_decoder",
                    codec="hevc", chroma="4:2:0", depth=10)
    try:
        ffmpeg_pipe_args(Path("ffmpeg.exe"), sw, src)
        record("pipe.软件计划拒绝生成硬解管道", False, "no error raised")
    except ValueError as exc:
        record("pipe.软件计划拒绝生成硬解管道", True, str(exc)[:60])

    # --- the ladder must actually consult it -----------------------------
    import inspect

    from core import batch_hw

    fsrc = inspect.getsource(batch_hw.hw_encode_with_fallback)
    record("pipe.降级梯为原始输入构造管道",
           "decode_pipe = ffmpeg_pipe_args(" in fsrc)
    record("pipe.只在原始输入上使用管道 (回退后不用)",
           "rung_pipe = decode_pipe if cur_input == source else None" in fsrc)
    record("pipe.管道传给 run_hw_tool",
           "decode_pipe=rung_pipe" in fsrc)

    from encoders import hw as hwmod

    record("pipe.run_hw_tool 支持 decode_pipe 形参",
           "decode_pipe" in inspect.signature(hwmod.run_hw_tool).parameters)
    # A failing feeder must not be treated as success.
    hsrc = inspect.getsource(hwmod.run_hw_tool)
    record("pipe.feeder 失败 -> 本次尝试判失败",
           "DECODE FEEDER FAILED" in hsrc and "return None" in hsrc)
