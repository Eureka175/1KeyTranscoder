#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drive the real MOV->MP4 conversion through `place_final` and check whether
the audio sample entry survives it, then apply the bit-depth normaliser.

This exercises the same code path a normal (non-transparent) transcode takes
when the encoder emits a MOV intermediate and the delivery container is MP4.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from core.batch_hw import place_final  # noqa: E402
from preservation.gpac import GpacContainerBackend  # noqa: E402
from preservation.sample_entry import (  # noqa: E402
    audio_entry_consistency,
    normalise_audio_entry_bit_depth,
    read_audio_entries,
)

W = Path(r"E:\1kt_scratch\audio_sync_test")


def show(tag: str, p: Path) -> None:
    entries = read_audio_entries(p)
    if not entries:
        print(f"{tag}: 无音频条目")
        return
    for i, e in enumerate(entries):
        c = audio_entry_consistency(e["bytes"])
        print(f"{tag:<12} CH{i + 1} fourcc={c['fourcc']!r} "
              f"samplesize={c['samplesize_bits']} pcmC={c['pcmc_bits']} "
              f"pkt={c['packet_bytes']} consistent={c['consistent']} "
              f"{c['problems']}")


def main() -> int:
    src = W / "out.mp4"
    mov = W / "out.mov"
    dst = W / "final.mp4"
    if not src.is_file():
        print("先跑 test_audio_sync_remux.py 生成 out.mp4")
        return 2
    shutil.copyfile(src, mov)
    dst.unlink(missing_ok=True)
    print("--- place_final(MOV -> MP4) ---")
    place_final(gpac=GpacContainerBackend(None), intermediate=mov, dst=dst,
                work_dir=W, log=lambda m: print("   ", m))
    show("转换后", dst)
    print("--- normalise_audio_entry_bit_depth ---")
    rep = normalise_audio_entry_bit_depth(dst, log=lambda m: print("   ", m))
    print("   ", {k: rep[k] for k in ("ok", "patched", "reason") if k in rep})
    show("校正后", dst)
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
