#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""详细 Sony 元数据对比自检 — 薄 CLI 入口.

实现位于 preservation/selfcheck.py (主程序 --check full 也直接调用);
本文件仅保留独立命令行用法:
    python tests/sony_selfcheck.py <original> <final> <log_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from preservation.selfcheck import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
