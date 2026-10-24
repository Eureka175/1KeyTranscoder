#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2026-10-25 关键证据 JSON 归档进 docs（work/ 在 .gitignore 里）。"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
dst = REPO / "docs" / "audio-sync-p4" / "reports" / "20261025"
dst.mkdir(parents=True, exist_ok=True)
files = [
    REPO / "work" / "task3_20261025" / "verify_v2_c1305.json",
    REPO / "work" / "task3_20261025" / "verify_v2_c1306.json",
    REPO / "work" / "task3_20261025" / "verify_restored_c1305.json",
    REPO / "work" / "task3_20261025" / "current_final_vs_source.json",
    REPO / "work" / "task3_20261025" / "fast_restore_c1305.json",
    REPO / "work" / "task3_20261025" / "stage_c1305_v2.json",
    REPO / "work" / "task3_20261025" / "stage_c1306_v2.json",
    REPO / "work" / "task3_20261025" / "shortcheck" / "result.json",
    REPO / "work" / "probe_20261025" / "summary.json",
    REPO / "work" / "probe_20261025" / "summary.md",
    REPO / "work" / "probe_20261025" / "testsets.json",
    REPO / "work" / "probe_20261025" / "testsets_after_2ch.json",
    REPO / "work" / "probe_20261025" / "i_20260924.json",
    REPO / "work" / "probe_20261025" / "dji_models.json",
    REPO / "work" / "PLAN_20261025" / "PROGRESS.md",
]
for p in files:
    if p.is_file():
        shutil.copy2(p, dst / p.name)
        print("copied", p.name)
print("->", dst)
