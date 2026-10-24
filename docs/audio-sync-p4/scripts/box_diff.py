#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""box 级差异清单：源 XAVC vs GPAC 还原产物（只读 moov/root，不读 mdat）。

对两文件的 box 树（root → moov → trak[i] → mdia → minf → stbl 等）做同名路径
匹配，报告：缺失/新增 box、size 变化、叶子 box payload hash 变化。用于任务3
"再次确认所有区别"。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
from preservation import isobmf  # noqa: E402

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts", "udta", "dinf",
              "mvex", "moof", "traf", "mfra"}


def walk(f, box, path, seen: dict, name: str):
    if not path and box.type in ("mdat", "free", "skip"):
        seen[path + (name,)] = (box.size, None, box.start)
        return
    f.seek(box.start)
    raw = f.read(box.size)
    seen[path + (name,)] = (box.size, hashlib.sha256(raw).hexdigest(),
                            box.start)
    if box.type not in CONTAINERS:
        return
    for idx, child in enumerate(
            isobmf._children(f, box, fullbox=box.type == "meta")):
        child_name = child.type + (f"[{idx}]" if child.type == "trak" else "")
        walk(f, child, path + (name,), seen, child_name)


def collect(p: Path) -> dict:
    out = {}
    with p.open("rb") as f:
        for b in isobmf.root_boxes(p):
            walk(f, b, (), out, b.type)
    return out


def main() -> int:
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    a, b = collect(src), collect(dst)
    res = {"source": str(src), "restored": str(dst),
           "box_count": [len(a), len(b)],
           "missing_boxes": ["/".join(k) for k in a if k not in b],
           "added_boxes": ["/".join(k) for k in b if k not in a],
           "size_diff": [{"path": "/".join(k), "source": a[k][0],
                          "restored": b[k][0]}
                         for k in a if k in b and a[k][0] != b[k][0]],
           "hash_diff": [{"path": "/".join(k), "size": a[k][0]}
                         for k in a if k in b and a[k][1] is not None
                         and b[k][1] is not None and a[k][1] != b[k][1]]}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if out:
        out.write_text(text, encoding="utf-8")
    print(text[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
