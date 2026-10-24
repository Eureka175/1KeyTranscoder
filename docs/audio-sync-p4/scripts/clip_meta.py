#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 GPAC splitx 片段补回文件级元数据（root uuid + top-level meta/XML）。

splitx 会保留 brand/轨道/轨级 uuid/tref，但丢失文件级 uuid 与 meta（nrtm
lens profile + XMP），导致 Gyroflow 认不出机型/IMU。这里从完整还原交付文件
原样拷贝这两类 root box 追加到片段末尾（idat 自包含，不会引用文件偏移）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--donor", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    clip, donor, out = Path(args.clip), Path(args.donor), Path(args.out)
    roots = isobmf.root_boxes(donor)
    payloads = []
    with donor.open("rb") as f:
        for b in roots:
            if b.type in ("uuid", "meta"):
                f.seek(b.start)
                payloads.append((b.type, f.read(b.size)))
    with clip.open("rb") as f, out.open("wb") as o:
        remaining = clip.stat().st_size
        while remaining > 0:
            chunk = f.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                break
            o.write(chunk)
            remaining -= len(chunk)
        for typ, raw in payloads:
            o.write(raw)
    print(f"appended {[(t, len(r)) for t, r in payloads]} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
