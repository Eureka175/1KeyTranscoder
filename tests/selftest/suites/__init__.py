"""按职责划分的测试 suite.

runner 只认识"名字 -> 可调用对象"; suite 之间不互相 import, 单个 test 不
依赖前一个 test 的状态 (执行顺序只影响输出可读性)。
"""

from __future__ import annotations
