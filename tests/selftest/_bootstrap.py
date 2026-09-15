#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tests/full_autotest.py` 薄兼容层使用的引导函数."""

from __future__ import annotations

import sys
from pathlib import Path


def run(argv: list[str] | None = None) -> int:
    """把仓库根加入 `sys.path` 后执行 selftest CLI。"""
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from .runner.cli import main

    return main(argv)


__all__ = ["run"]
