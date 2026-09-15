#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享路径 / 环境 / 记录原语 (原 full_autotest.py 头部)。

本模块是**唯一**持有可变测试状态的地方:
  * `RESULTS`  : 已记录的断言 (record() 追加);
  * `CURRENT_LEVEL` : 当前执行的深度 (runner 在切换 level 时写入)。

suite 模块通过 `record()` / `section()` 记录, 或 `from .. import paths` 后
`paths.RESULTS` 读取;**不要** `from ..paths import RESULTS` —— 那样拿到的是
旧列表对象的副本, 会漏统计。

`ROOT` 的语义与原实现一致 (仓库根目录), 但**推导多一层**: 原入口在
`tests/full_autotest.py` (上溯两级), 本模块在 `tests/selftest/paths.py`
(上溯三级)。两条路径解析到同一个目录。
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from pathlib import Path

#: 仓库根目录 (原 `tests/full_autotest.py` 的 `ROOT` = `.parent.parent`)。
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "work" / "autotest"


IN_DIR = WORK / "in"


OUT_DIR = WORK / "out"


FFPROBE = ROOT / "tools" / "ffprobe.exe"


FFMPEG = ROOT / "tools" / "ffmpeg.exe"


MB = Path(r"C:\Program Files\GPAC\mp4box.exe")


GF_CANDIDATES = [
    Path(r"D:\Gyroflow-windows64\Gyroflow.exe"),
    Path(r"C:\Program Files\Gyroflow\Gyroflow.exe"),
]


RESULTS: list[dict[str, Any]] = []


CURRENT_LEVEL = "L1"


def record(name: str, ok: bool, detail: str = "", level: str = "") -> None:
    RESULTS.append(
        {
            "name": name,
            "level": level or CURRENT_LEVEL,
            "status": "PASS" if ok else "FAIL",
            "detail": detail,
        }
    )


def section(title: str) -> None:
    print(f"\n== {title} ==")


def _work_set_mb() -> float | None:
    """本进程工作集 (MB); 非 Windows 或调用失败返回 None。

    内存回归断言用: np.memmap 会把被触碰的整条文件计入 WorkingSet,
    有界窗口读取器则不会 —— 这条断言把该性质钉进回归 (Stage 1.2)。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.restype = wt.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD,
        ]
        c = _PMC()
        c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(
                k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return c.WorkingSetSize / 1024 ** 2
    except Exception:
        return None


def sh(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout,
    )


def ffprobe_json(path: Path) -> dict:
    r = sh(FFPROBE, "-v", "error", "-show_streams", "-show_format",
           "-of", "json", path)
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout or "{}")
