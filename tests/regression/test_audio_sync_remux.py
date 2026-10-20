#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exercise `remux_replace_audio` (the normal transcode path's audio step)
without encoding anything.

Stand-in for the real pipeline: `video_src` = an existing archive file (it still
carries the original audio, exactly like the encoded intermediate does), and
the replacement audio tracks are the same tracks pulled through ffmpeg `.mov`
intermediates — which is what `run_channel_sync(transparent=False)` produces and
what caused the bug.

Checks the output's audio sample entries: consistent, and bit depth matches the
reference. ffmpeg decoding is explicitly NOT the acceptance test here, because
ffmpeg prefers `pcmC` and happily decodes the broken file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

FFMPEG = REPO / "tools" / "ffmpeg.exe"
WORK = Path(r"E:\1kt_scratch\audio_sync_test")
SRC = Path(r"I:\20260904\20260904_C1183.MP4")


def main() -> int:
    from preservation.audio_sync import remux_replace_audio
    from preservation.gpac import GpacContainerBackend
    from preservation.sample_entry import (
        audio_entry_consistency,
        read_audio_entries,
    )

    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.glob("*"):
        if p.is_file():
            p.unlink()

    n_audio = len([s for s in _streams(SRC) if s.get("codec_type") == "audio"])
    print(f"源 {SRC.name}: {n_audio} 条音轨")
    mids = []
    for i in range(n_audio):
        out = WORK / f"audio_{i}.mov"
        r = subprocess.run(
            [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(SRC),
             "-map", f"0:a:{i}", "-c", "copy", str(out)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace")
        if r.returncode != 0 or not out.is_file():
            print(f"  中间文件 {i} 失败: {r.stdout[-200:]}")
            return 1
        mids.append(out)
    # 故意先把中间文件的条目特征打出来, 证明"坏"确实来自这里
    mid = read_audio_entries(mids[0])
    print(f"  中间文件条目: fourcc={mid[0]['bytes'][4:8]!r} "
          f"consistency={audio_entry_consistency(mid[0]['bytes'])}")

    dst = WORK / "out.mp4"
    logs: list[str] = []
    try:
        remux_replace_audio(gpac=GpacContainerBackend(None),
                            video_src=SRC, fixed_files=mids, dst=dst,
                            log=lambda m: (logs.append(m), print("  ", m))[1])
    except Exception as exc:                            # noqa: BLE001
        print(f"  remux_replace_audio 失败: {type(exc).__name__}: {exc}")
        return 1

    ref = read_audio_entries(SRC)
    got = read_audio_entries(dst)
    print(f"\n参照 {len(ref)} 条 / 成品 {len(got)} 条")
    bad = 0
    for i, (r_, g_) in enumerate(zip(ref, got)):
        c_ref = audio_entry_consistency(r_["bytes"])
        c_got = audio_entry_consistency(g_["bytes"])
        same_bits = c_ref["samplesize_bits"] == c_got["samplesize_bits"]
        ok = c_got["consistent"] and same_bits
        bad += 0 if ok else 1
        print(f"  CH{i + 1}: ref {c_ref['samplesize_bits']}bit -> "
              f"out {c_got['samplesize_bits']}bit "
              f"pcmC={c_got['pcmc_bits']} consistent={c_got['consistent']} "
              f"{'OK' if ok else '*** FAIL'}")
        if not ok:
            print(f"        problems={c_got['problems']}")
    print(f"\n结论: {'PASS' if not bad else f'FAIL ({bad} 条)'}")
    return 0 if not bad else 1


def _streams(p: Path) -> list[dict]:
    import json

    r = subprocess.run(
        [str(REPO / "tools" / "ffprobe.exe"), "-v", "error",
         "-show_streams", "-of", "json", str(p)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace")
    return json.loads(r.stdout or "{}").get("streams") or []


if __name__ == "__main__":
    raise SystemExit(main())
