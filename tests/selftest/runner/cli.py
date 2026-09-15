#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口: 参数解析 + 深度选择 + 退出码.

CLI 与重构前**完全一致**:

    python tests/full_autotest.py --level unit        # L1 纯逻辑单测
    python tests/full_autotest.py --level toolchain   # L2 = L1 + 工具链探测
    python tests/full_autotest.py --level full        # L3 = L2 + 真实管线集成
    python tests/full_autotest.py --level all         # 等同 full

默认 `--level full`; 退出码 0 = 全部通过, 1 = 存在失败。
"""

from __future__ import annotations

import argparse
import sys
import time

from .. import paths
from ..reporting import write_reports
from .engine import LEVELS, build_registry, run_levels

#: 未取到兼容层 docstring 时的兜底描述文本。
DESCRIPTION = "1KeyTranscoder 全量自动化测试 (分级测试深度)"


def default_description() -> str:
    """重构前入口是 `argparse.ArgumentParser(description=__doc__)`.

    为让 `--help` 与重构前逐字一致, 这里优先取兼容层
    (`tests/full_autotest.py`) 的模块 docstring; 取不到时退回 `DESCRIPTION`
    (只影响帮助文本, 不影响任何测试行为)。
    """
    try:
        import tests.full_autotest as entry

        return entry.__doc__ or DESCRIPTION
    except Exception:                     # noqa: BLE001
        return DESCRIPTION


def main(
    argv: list[str] | None = None, *, description: str | None = None,
) -> int:
    ap = argparse.ArgumentParser(
        description=description if description is not None
        else default_description(),
    )
    ap.add_argument(
        "--level",
        choices=["unit", "toolchain", "full", "all"],
        default="full",
        help="测试深度: unit=纯逻辑; toolchain=+工具探测; full=+管线集成+故障注入",
    )
    args = ap.parse_args(argv)
    levels = LEVELS[args.level]

    paths.WORK.mkdir(parents=True, exist_ok=True)
    started = time.time()
    registry = build_registry()
    run_levels(levels, registry)

    report, summary = write_reports(args.level, paths.RESULTS, started)

    print("\n" + "=" * 60)
    print(f"SUMMARY: {summary['PASS']} PASS / {summary['FAIL']} FAIL "
          f"({report['elapsed_sec']}s)")
    print(f"报告: {paths.WORK / 'autotest_report.json'}")
    print(f"      {paths.WORK / 'autotest_report.md'}")
    for r in paths.RESULTS:
        if r["status"] == "FAIL":
            print(f"  FAIL [{r['level']}] {r['name']}: {r['detail'][:140]}")
    return 1 if summary["FAIL"] else 0


def run() -> int:
    """`python -m tests.selftest` 风格的入口 (不带 argv 转发)。"""
    return main(sys.argv[1:])


__all__ = ["DESCRIPTION", "main", "run"]
