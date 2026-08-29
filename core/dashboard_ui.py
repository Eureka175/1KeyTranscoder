#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nvidia-smi 风格整体进度看板 (独立控制台窗口).

由主程序在非 headless 模式下以新控制台启动:
    python -m core.dashboard_ui <dashboard.json 路径>

每 1.5s 读取一次状态 JSON 并重绘; 状态 finished=true 时打印终局并退出。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CLEAR = "\033[H\033[2J"
HIDE = "\033[?25l"
SHOW = "\033[?25h"


def _bar(ratio: float, width: int = 18) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "#" * filled + "." * (width - filled)


def render(data: dict) -> None:
    meta = data.get("meta", {})
    rows = data.get("rows", [])
    lines: list[str] = []
    lines.append(CLEAR)
    lines.append("=" * 96)
    lines.append(
        f" 1KeyTranscoder 进度看板        {data.get('timestamp', '')}"
    )
    lines.append("=" * 96)
    lines.append(
        f" Encoder : {meta.get('encoders', '-')}   "
        f"Preset  : {meta.get('preset', '-')}   "
        f"Check   : {meta.get('check', '-')}   "
        f"Files   : {meta.get('total', 0)}"
    )
    lines.append(
        f" GPU     : {meta.get('gpu', '-')}"
    )
    lines.append("-" * 96)
    lines.append(
        f" {'FILE':<34} {'BACKEND':<8} {'STATUS':<14} "
        f"{'DETAIL':<24} {'TIME':>7}"
    )
    lines.append("-" * 96)
    for row in rows:
        name = row.get("name", "")[:33]
        backend = row.get("backend", "-")[:7]
        status = row.get("status", "-")[:13]
        detail = row.get("detail", "")[:23]
        if row.get("frames"):
            detail = f"{row['frames']}f {row.get('fps', 0):.0f}fps"
        elapsed = row.get("elapsed")
        time_s = f"{elapsed:.0f}s" if elapsed is not None else ""
        lines.append(
            f" {name:<34} {backend:<8} {status:<14} {detail:<24} {time_s:>7}"
        )
    lines.append("-" * 96)
    counts: dict[str, int] = {}
    for row in rows:
        s = row.get("status", "?")
        counts[s] = counts.get(s, 0) + 1
    lines.append(
        " " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + ("   |   " + "FINISHED" if data.get("finished") else "")
    )
    lines.append("=" * 96)
    sys.stdout.write(HIDE + "\n".join(lines) + "\n")
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m core.dashboard_ui <json_path>")
        return 2
    path = Path(sys.argv[1])
    print(f"[dashboard] watching {path} (Ctrl+C to close this window)")
    try:
        while True:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(1.0)
                continue
            render(data)
            if data.get("finished"):
                time.sleep(2.0)
                break
            time.sleep(1.5)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
