# 环境与版本（测于 2026-11-16）

| 项 | 值 |
|---|---|
| 产品版本 | `VERSION` = 0.9.14 |
| Git HEAD | `399f311c8c533ef3e352085b2ba037bee7936623`（工作树含评估/功能改动，`git status --short` 见下） |
| GPU | NVIDIA GeForce RTX 5070 Laptop GPU, 616.92, 8151 MiB |
| CPU | Intel(R) Core(TM) Ultra 9 285H |
| OS | Microsoft Windows 11 家庭版 中文版 |
| FFmpeg | ffmpeg version 2026-06-15-git-44d082edc8-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers |
| NVEncC | NVEncC (x64) 9.31 (r4047) by rigaya, Aug  8 2026 12:55:12 (VC 1944/Win) |
| QSVEncC | QSVEncC (x64) 8.26 (r4504) by rigaya, Aug  8 2026 12:17:31 (VC 1944/Win) |

## git status --short（摘录）

```text
M 1kt.py
 M README.md
 M README.zh-CN.md
 M VERSION
 M core/batch_hw.py
 M core/dashboard_ui.py
 M docs/README.md
 M encoders/qsvencc.py
 M nvenc_av1.json
 M preservation/gpac.py
 M release/build_release.py
 M tests/selftest/runner/engine.py
 M tests/selftest/suites/hardware_audio.py
?? core/dji_join.py
?? dji_join.py
?? docs/deploy/
?? docs/dji-segments.md
?? docs/evaluation/4k-encoder-eval-2026-11-16/
?? docs/hardware-decode/canon-r6v-investigation/
?? docs/release_notes_v0.9.14.md
?? docs/x265-calibration/
?? tests/selftest/suites/dji_join.py
?? upstream-issues/
```

> 本归档只新增 `docs/` 文档与静态证据；未修改产品代码。`work/` 下的原始输出按项目约定不入库。
