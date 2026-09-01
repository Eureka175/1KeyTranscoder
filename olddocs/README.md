# olddocs/ — 历史档案存档

> 本目录存放已退出活跃开发的历史文件, 不参与构建/运行, 仅供考古与
> 决策追溯。**请勿从这里导入代码**; 需要旧实现时以 git 历史为准
> (tag: pre_S1S5 / pre_ui / v0.2.0-alpha / v0.3.0-beta)。

| 路径 | 内容 | 归档原因 |
|---|---|---|
| `backup/` | 各阶段全量代码快照 (pre_S1S5 20260829 / pre_ui_1kt) 与原始 README | S1-S5 与 UI 重构前的历史基线, git tag 已覆盖, 目录冗余 |
| `x265_archive.py` | 早期 x265 单档批处理编排器 | 已被 `1kt.py` 完全取代 (DEPRECATED 横幅期结束); 核心模块仅保留 logger 名/文档提及, 无任何导入 |
| `sony_poc.py` | Sony 元数据保留独立 POC | 已并入 `preservation/` 保留管线; `preservation/__init__.py` 文档注释曾指向此处 |

归档时间: 2026-09-01 (av1 分支首次归档, main 分支同步)。
