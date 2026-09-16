"""跨域生产编排 (Phase 4C)。

本包**不属于**音频域, 也**不属于**视频域: 它只把两个域各自的产物契约编排成
一次生产输出。

    Audio Domain                      Video Domain
    core/audio_*.py                   encoders/ 1kt.py preservation/
        │ EncodedAudioOutput               │ (一个已经落盘的视频文件)
        ▼                                  ▼
    production.output  ────────────────────┘
        │
        ▼
    core.output_compose.OutputComposer
        │
        ▼
    final container

依赖方向严格单向, 且**只依赖公开 contract**:

* 允许 `production.output` -> `core.audio_process` / `core.audio_encode` /
  `core.audio_retention` / `core.audio_execution` 的**公开 API**;
* 允许 `production.output` -> `core.output_compose`;
* **禁止**反向: 音频域/视频域都不得 import 本包;
* **禁止**本包进入音频内部 (AudioMixer / AudioPCMReader / ChannelTimeline)
  或视频内部 (编码器 argv / 像素格式 / 帧率)。

音频与视频是**平行分支**: 视频走既有编码路径产出文件, 音频走
`AudioPlan -> 执行图 -> PCM/保留 -> 编码`, 最后才在 Composer 汇合。
不存在"视频管线调用音频管线"这种串行关系。
"""

from __future__ import annotations

__all__ = ["output"]


def __getattr__(name: str):
    if name == "output":
        import importlib

        return importlib.import_module(".output", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
