#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the container layout of the original, the one-off staging, and the
tool's output for the same clip."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"F:\1KeyTranscoder")
from archive_repair import container_facts  # noqa: E402

CASES = [
    ("I: 原件        ", r"I:\20260904\20260904_C1183.MP4"),
    ("one-off 暂存   ", r"E:\1kt_archive_repaired\20260904\20260904_C1183.MP4"),
    ("工具暂存       ", r"E:\1kt_scratch\ar_out2\20260904_C1183.MP4"),
]


def main() -> int:
    for tag, p in CASES:
        f = container_facts(Path(p))
        top = f["top_level"]
        print(f"{tag} moov_first={str(f['moov_first']):<5} "
              f"uuid={len(f['uuid'])} nrtm={f['nrtm']}  {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
