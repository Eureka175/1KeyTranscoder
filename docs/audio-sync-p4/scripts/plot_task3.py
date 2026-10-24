#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务3 对比图：源 XAVC / 旧 ffmpeg 成品 / GPAC 字节级还原 的容器与消费方事实。"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(r"F:\1KeyTranscoder")
WORK = REPO / "work" / "task3_20261025"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"


def load(stem: str) -> dict:
    p = WORK / f"verify_v2_{stem.lower()}.json"
    if not p.is_file():
        p = WORK / f"verify_v2_c1305.json"
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    d = load("c1305")
    c = d["container"]
    labels = ["brand", "compat", "tracks", "uuid\nmultiset", "nrtm",
              "tref/cdsc", "audio\nentries", "mvhd", "video\nES", "rtmd\npayload",
              "audio\nmd5"]
    source_ok = [1] * len(labels)
    restored_ok = [
        1 if c["brand_equal"][0] == c["brand_equal"][1] else 0,
        1 if c["compat_equal"] else 0,
        1 if c["tracks_equal"] else 0,
        1 if c["uuid"]["equal"] else 0,
        1 if c["nrtm"][0] == c["nrtm"][1] else 0,
        1 if c["tref"][0] == c["tref"][1] else 0,
        1 if c["audio_entries_equal"] else 0,
        1 if c["mvhd_equal"] else 0,
        1 if d["video_streamhash"]["equal"] else 0,
        1 if d["rtmd_streamhash"]["equal"] else 0,
        sum(1 for a in d["audio_md5"] if a["match"]) / max(1, len(d["audio_md5"])),
    ]
    # 旧 ffmpeg 成品：全部缺失（除 audio entries 与 mvhd 之外，实测 audio entries
    # 一致、mvhd 不一致；见 current_final_vs_source.json）
    ff = [0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1]

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    x = range(len(labels))
    w = 0.27
    axes[0].bar([i - w for i in x], source_ok, w, label="source XAVC",
                color="#2ca02c")
    axes[0].bar(list(x), ff, w, label="old ffmpeg deliverable",
                color="#d62728")
    axes[0].bar([i + w for i in x], restored_ok, w,
                label="GPAC byte-restored", color="#1f77b4")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    axes[0].set_ylim(0, 1.15)
    axes[0].set_ylabel("match with source (1 = equal)")
    axes[0].set_title("Container/stream facts: source vs deliverables")
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    src = d["gyro"]["source"]
    rst = d["gyro"]["restored"]
    names = ["source XAVC", "old ffmpeg", "byte-restored"]
    qvars = [src["quat_varying"], 0, rst["quat_varying"]]
    colors = ["#2ca02c", "#d62728", "#1f77b4"]
    axes[1].bar(names, qvars, color=colors)
    for i, v in enumerate(qvars):
        axes[1].text(i, v + max(qvars) * 0.02, str(v), ha="center", fontsize=9)
    axes[1].set_ylabel("frames with varying camera quaternion")
    axes[1].set_title(
        "Gyroflow camera data (288150 frames total)\n"
        f"timestamp offset: old ffmpeg 7.376 ms, restored "
        f"{d['gyro'].get('timestamp_diff_ms', {}).get('first', 0.0)} ms")
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out = PLOT / "task3_metadata_comparison.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
