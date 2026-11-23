#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nvidia-smi 风格整体进度看板 (独立控制台窗口).

由主程序在非 headless 模式下以新控制台启动:
    python -m core.dashboard_ui <dashboard.json 路径>

每 1.5s 读取一次状态 JSON 并重绘; 状态 finished=true 时打印终局并退出。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

CLEAR = "\033[H\033[2J"
HIDE = "\033[?25l"
SHOW = "\033[?25h"

#: The queue can contain thousands of files.  The window only shows the
#: first rows; the remainder is summarized as an ellipsis line so the
#: dashboard stays readable and does not scroll the current work off screen.
MAX_VISIBLE_ROWS = 20


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
    for row in rows[:MAX_VISIBLE_ROWS]:
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
    if len(rows) > MAX_VISIBLE_ROWS:
        lines.append(
            f" ... 队列其余 {len(rows) - MAX_VISIBLE_ROWS} 个文件已省略"
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


def _pid_alive(pid: int) -> bool:
    """Return whether *pid* is still running.

    Used as a backstop for the case where the parent console is killed
    without running its finally block.  On Windows the child dashboard is
    in its own console, so it does not receive the parent's Ctrl+C event;
    checking the parent PID is what makes the window close in that case.
    """
    if pid <= 0:
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
            handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - best-effort monitor
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _enable_windows_vt() -> None:
    """Enable ANSI/VT redraw in a freshly created Windows console."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)      # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:                                  # noqa: BLE001
        pass


def main() -> int:
    _enable_windows_vt()
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("json_path", nargs="?")
    ap.add_argument("--parent-pid", type=int, default=0)
    args, _unknown = ap.parse_known_args()
    if not args.json_path:
        print("usage: python -m core.dashboard_ui <json_path> "
              "[--parent-pid PID]")
        return 2
    path = Path(args.json_path)
    parent_pid = args.parent_pid
    print(f"[dashboard] watching {path} "
          f"(Ctrl+C to close this window; parent pid={parent_pid or '-'})")
    try:
        while True:
            if parent_pid and not _pid_alive(parent_pid):
                break
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
