#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1KeyTranscoder 全量自动化测试 (分级测试深度).

用法:
    python tests/full_autotest.py --level unit        # L1 纯逻辑单测 (无外部工具, 秒级)
    python tests/full_autotest.py --level toolchain   # L2 = L1 + 工具链/能力/旗标探测 (约1分钟, 不编码)
    python tests/full_autotest.py --level full        # L3 = L2 + 真实管线集成 + 故障注入 (约10-15分钟)
    python tests/full_autotest.py --level all         # 等同 full

分级定义:
  L1 unit       : 纯函数逻辑 (color 表/caps 解析/格式规划/失败分类/flag 构造/
                  probe 解析/paths/分类器/缩放引擎/gpac parse_info/dji facts)
  L2 toolchain  : 真实工具版本 + --check-features 能力解析 + known_flags
                  白名单 + Gyroflow/GPAC 可用性 (只探测, 不编码)
  L3 full       : 真实管线集成 (Sony/DJI/经典路径 x NVENC/QSV, basic+full
                  check) + 故障注入 (截断文件/trailing-garbage 触发 strip
                  回退) + failed_files.json/retry-list + 断点续跑

约束: 全部输入在 work/autotest/ 下自建副本, testsets 原文件只读;
      全部产物写入 work/autotest/ (报告 autotest_report.json/.md);
      不修改任何既有配置/文档。
退出码: 0 = 全部通过; 1 = 存在失败。

---

本文件是**薄兼容层**: CLI、退出码、报告格式与重构前逐字一致, 但测试实现
已按职责拆到 `tests/selftest/`:

    tests/selftest/
        runner/      CLI + registry + 执行器
        fixtures/    可复用素材 (确定性 PCM / AudioPlan / 真实媒体)
        assertions/  可复用断言原语
        suites/      按职责拆分的测试实现
        reporting/   结果汇总 + 报告落盘

兼容性: `full_autotest.SUITES` / `full_autotest.record` / … 等旧名仍然可用
(转发到真实实现, 不是第二套实现)。
"""

from __future__ import annotations

import sys
from pathlib import Path

# `tests/` 就是脚本目录, 因此父包 `tests` 可直接导入; 先把仓库根放进
# sys.path, 使 `core` / `encoders` / `preservation` 在入口处即可见。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.selftest import paths as _paths                      # noqa: E402
from tests.selftest import _bootstrap                           # noqa: E402
from tests.selftest.runner import engine as _engine             # noqa: E402

# ---------------------------------------------------------------------------
# 兼容转发 (旧代码可能 `from tests.full_autotest import X`)
# ---------------------------------------------------------------------------
ROOT = _paths.ROOT
WORK = _paths.WORK
IN_DIR = _paths.IN_DIR
OUT_DIR = _paths.OUT_DIR
FFPROBE = _paths.FFPROBE
FFMPEG = _paths.FFMPEG
MB = _paths.MB
GF_CANDIDATES = _paths.GF_CANDIDATES
RESULTS = _paths.RESULTS
CURRENT_LEVEL = _paths.CURRENT_LEVEL
LEVELS = _engine.LEVELS

record = _paths.record
section = _paths.section
sh = _paths.sh
ffprobe_json = _paths.ffprobe_json
main = _bootstrap.run

#: 重构前 `SUITES` 是"level -> [(显示名, 可调用对象)]"的 dict。惰性构建:
#: 单纯 `import tests.full_autotest` 不触发 suite 模块导入 (与重构前一致,
#: 当时 suite 函数就定义在本模块里)。
_LAZY = {"SUITES", "REGISTRY"}


def __getattr__(name: str):
    if name in _LAZY:
        registry = _engine.build_registry()
        globals()["SUITES"] = registry
        globals()["REGISTRY"] = registry
        return registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    sys.exit(main())
