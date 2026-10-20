#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the audio sample-entry consistency verdict for a list of files."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"F:\1KeyTranscoder")
from preservation.sample_entry import (  # noqa: E402
    audio_entry_consistency,
    read_audio_entries,
)

CASES = [
    ("原始 Sony (ipcm 24)", r"I:\20260904\20260904_C1183.MP4"),
    ("坏掉的修复件 (ipcm 16)", r"E:\1kt_scratch\astest\c_broken.mp4"),
    ("修好的产出", r"E:\1kt_scratch\astest\fix1\20260904_C1183.MP4"),
    ("正规路径 (in24)", r"E:\1kt_scratch\audio_sync_test\out.mp4"),
]


def main() -> int:
    args = sys.argv[1:]
    cases = ([tuple(a.split("=", 1)) for a in args] if args else CASES)
    for tag, p in cases:
        path = Path(p)
        if not path.is_file():
            print(f"{tag:<26} (missing) {p}")
            continue
        entries = read_audio_entries(path)
        if not entries:
            print(f"{tag:<26} 无音频条目")
            continue
        for i, e in enumerate(entries):
            c = audio_entry_consistency(e["bytes"])
            print(f"{tag:<26} CH{i + 1} fourcc={c['fourcc']!r} "
                  f"consistent={c['consistent']} "
                  f"samplesize={c['samplesize_bits']} "
                  f"pcmC={c['pcmc_bits']} pkt={c['packet_bytes']} "
                  f"expected={c['expected_bits']} "
                  f"problems={c['problems']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
