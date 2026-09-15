#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1KeyTranscoder selftest 包.

分层 (严格单向):

    runner/      CLI + registry + executor      —— 只编排, 不含测试内容
    fixtures/    可复用素材 (音频/计划/真实媒体) —— 只造数据, 不含断言
    assertions/  可复用断言/观测原语             —— 纯函数
    suites/      按职责拆分的测试实现             —— 只调用上面三层
    reporting/   结果汇总 + 报告落盘

`paths` 是唯一持有可变测试状态 (`RESULTS` / `CURRENT_LEVEL`) 的模块。

对外入口仍是 `tests/full_autotest.py` (薄兼容层), CLI 与退出码不变:

    python tests/full_autotest.py --level unit
    python tests/full_autotest.py --level toolchain
    python tests/full_autotest.py --level full
"""

from __future__ import annotations

__all__ = [
    "paths", "runner", "fixtures", "assertions", "suites", "reporting", "main",
]


def main(*args, **kwargs):
    """转发到 `runner.cli.main` (保持 `selftest.main()` 可用)。"""
    from .runner.cli import main as _main

    return _main(*args, **kwargs)


def __getattr__(name: str):
    # 惰性暴露子包, 避免 import 顺序耦合 (numpy 等可选依赖只在真正用到时
    # 才被拉入)。
    if name in {"paths", "runner", "fixtures", "assertions", "suites",
                "reporting"}:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
