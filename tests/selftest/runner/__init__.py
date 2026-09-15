"""测试执行编排 (CLI / registry / executor)。"""

from .engine import LEVELS, LEVEL_ALIASES, build_registry, run_levels

__all__ = ["LEVELS", "LEVEL_ALIASES", "build_registry", "run_levels"]
