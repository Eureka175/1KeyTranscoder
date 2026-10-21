#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise alignment outcome by **video duration**.

The question this answers: is there a clip length beyond which 4-channel
alignment stops working? That decides what the tuning set can and cannot prove,
and whether the long takes are a length problem or a content problem.

Buckets are by duration; for each bucket it reports, per arm:
  files, target tracks (anchors excluded), aligned tracks
  (already_aligned + fixed), refused tracks with a real offset, and how many
  files are fully aligned (every non-anchor track within tolerance).
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
SR = 48000
MIN_SAMPLES = 5
BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 120), (120, 300), (300, 10 ** 9)]

ARMS = [("P_base.json", "默认"), ("P_mat.json", "工作点(agree5)"),
        ("P_mat10.json", "agree10"), ("P_mat20.json", "agree20")]
LONG = ["P_long_C1305.json", "P_long_C1306.json"]


def bucket_of(dur: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= dur < hi:
            if hi >= 10 ** 9:
                return f">{lo}s"
            return f"{lo}-{hi}s"
    return "?"


def summarise(name: str) -> dict | None:
    p = OUT / name
    if not p.is_file():
        return None
    rows = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for r in rows:
        dur = r.get("duration")
        if not isinstance(dur, (int, float)):
            continue
        b = bucket_of(dur)
        st = out.setdefault(b, {"files": 0, "targets": 0, "aligned": 0,
                                "off": 0, "full_files": 0, "short": 0})
        st["files"] += 1
        file_ok = True
        has_target = False
        for c in r.get("channels") or []:
            if c.get("decision") in ("anchor", None) and c.get("reason") is None:
                continue
            has_target = True
            d = c.get("decision")
            dm = c.get("delay_ms")
            if d in ("fixed", "already_aligned"):
                st["aligned"] += 1
            else:
                file_ok = False
                if isinstance(dm, (int, float)) and abs(dm) >= 1000 * MIN_SAMPLES / SR:
                    st["off"] += 1
            st["targets"] += 1
        if has_target and file_ok:
            st["full_files"] += 1
    return out


def main() -> int:
    arms = [(label, summarise(name)) for name, label in ARMS]
    order = [f"{lo}-{hi}s" for lo, hi in BUCKETS[:-1]] + [">300s"]
    print("按视频时长分桶（目标轨 = 非锚轨；aligned = fixed+already_aligned）\n")
    for label, data in arms:
        if data is None:
            print(f"=== {label}: 未跑")
            continue
        print(f"=== {label}")
        print(f"{'时长':<12}{'文件':>5}{'目标轨':>7}{'已对齐':>7}"
              f"{'仍偏移':>7}{'对齐率':>8}{'全对齐文件':>11}")
        print("-" * 60)
        for b in order:
            st = data.get(b)
            if not st:
                continue
            rate = (100.0 * st["aligned"] / st["targets"]
                    if st["targets"] else 0.0)
            print(f"{b:<12}{st['files']:>5}{st['targets']:>7}"
                  f"{st['aligned']:>7}{st['off']:>7}{rate:>7.0f}%"
                  f"{st['full_files']:>11}")
        tot = {k: sum(v[k] for v in data.values())
               for k in ("files", "targets", "aligned", "off", "full_files")}
        print(f"{'合计':<12}{tot['files']:>5}{tot['targets']:>7}"
              f"{tot['aligned']:>7}{tot['off']:>7}"
              f"{100.0 * tot['aligned'] / max(1, tot['targets']):>7.0f}%"
              f"{tot['full_files']:>11}\n")

    print("=== 两条长镜头（单独测）")
    for n in LONG:
        p = OUT / n
        if not p.is_file():
            print(f"  {n}: 未跑")
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        for r in rows:
            dur = r.get("duration")
            chans = [c for c in (r.get("channels") or [])
                     if c.get("decision") != "anchor"]
            aligned = [c for c in chans
                       if c.get("decision") in ("fixed", "already_aligned")]
            off = [c for c in chans if c.get("decision") == "untouched"
                   and isinstance(c.get("delay_ms"), (int, float))
                   and abs(c["delay_ms"]) >= 1000 * MIN_SAMPLES / SR]
            print(f"  {r.get('name')}: 时长 {dur}s 状态 {r.get('status')}  "
                  f"目标轨 {len(chans)} 已对齐 {len(aligned)} "
                  f"仍偏移 {len(off)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
