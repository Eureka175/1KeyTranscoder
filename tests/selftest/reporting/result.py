#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告落盘 (JSON + Markdown), 输出格式与重构前逐字一致。"""

from __future__ import annotations

import json
import time
from typing import Any

from .. import paths


def write_reports(
    level: str, results: list[dict[str, Any]], started: float,
) -> tuple[dict[str, Any], dict[str, int]]:
    """把断言结果落盘为 `work/autotest/autotest_report.{json,md}`。

    返回 `(report, summary)`。路径、字段名与 Markdown 表头与重构前逐字一致
    (报告的消费者是人, 逐字保持可读性比"更漂亮的结构"更重要)。计数直接来自
    `record()` 的结果列表, 因此 PASS/FAIL 语义与重构前完全等价。
    """
    summary = {
        "PASS": sum(1 for r in results if r["status"] == "PASS"),
        "FAIL": sum(1 for r in results if r["status"] == "FAIL"),
    }
    report: dict[str, Any] = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "elapsed_sec": round(time.time() - started, 1),
        "summary": summary,
        "items": results,
    }
    paths.WORK.mkdir(parents=True, exist_ok=True)
    (paths.WORK / "autotest_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 1KeyTranscoder 全量自动化测试报告",
        "",
        f"- 深度: {level} | 生成: {report['generated']} | "
        f"耗时: {report['elapsed_sec']}s",
        f"- 汇总: **{summary['PASS']} PASS / {summary['FAIL']} FAIL**",
        "",
        "| 级别 | 用例 | 状态 | 详情 |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['level']} | {r['name']} | {r['status']} | "
            f"{r['detail'][:120]} |"
        )
    (paths.WORK / "autotest_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )
    return report, summary


__all__ = ["write_reports"]
