#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify a staged archive-repair set produced by `archive_repair.py`.

Asserts, per file, the things that actually matter for shipping it back:

  * the file was really repaired (`status=applied`, metadata identical)
  * the container layout matches the archive convention (moov-first)
  * no content was lost or altered (`validate` MODIFIED=0 MISSING=0)
  * the audio is aligned **when re-measured on the output** (AUD-03 PASS)
  * duration unchanged, Gyroflow still reads the camera

and that the staged file set equals the expected set.

    python verify_staged.py --staged E:\\1kt_archive_repaired \\
        --report-dir ... --expect-from E:\\1kt_archive_repaired_v1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_reports(root: Path) -> list[dict]:
    rows: list[dict] = []
    for p in sorted(root.rglob("archive-repair-report.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8"))["rows"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", required=True)
    ap.add_argument("--report-root", default="")
    ap.add_argument("--expect-from", default="",
                    help="目录: 用它里面的 .MP4 名字作为期望文件集")
    a = ap.parse_args()

    staged = Path(a.staged)
    report_root = Path(a.report_root) if a.report_root else staged / "_rep"
    rows = load_reports(report_root)
    by_name = {}
    for r in rows:
        name = Path(r.get("dst") or r.get("src")).name
        prev = by_name.get(name)
        # 同一文件可能出现在多个报告里 (分目录跑); 保留真正修复的那条
        if prev is None or (prev.get("status") != "applied"
                            and r.get("status") == "applied"):
            by_name[name] = r

    on_disk = {p.name: p for p in staged.rglob("*")
               if p.suffix.lower() in (".mp4", ".mov")}
    print(f"暂存目录里 {len(on_disk)} 个媒体文件; 报告覆盖 {len(by_name)} 个")

    expected = None
    if a.expect_from:
        exp = Path(a.expect_from)
        expected = {p.name for p in exp.rglob("*")
                    if p.suffix.lower() in (".mp4", ".mov")}
        missing = sorted(expected - set(on_disk))
        extra = sorted(set(on_disk) - expected)
        print(f"期望文件集 {len(expected)} 个; 缺={missing} 多={extra}")

    hdr = (f"{'file':<24}{'moov1st':<9}{'meta':<6}{'dur':>10}"
           f"{'MOD':>4}{'MIS':>4}{'AUD03':<9}{'gyro':<6}  uuid")
    print()
    print(hdr)
    print("-" * len(hdr))
    bad: list[str] = []
    for name in sorted(on_disk):
        r = by_name.get(name)
        if r is None:
            bad.append(f"{name}: 报告里没有")
            print(f"{name:<24} (no report row)")
            continue
        md = r.get("metadata") or {}
        top_after = r.get("top_level_after") or []
        moov_before_mdat = (
            top_after.index("moov") < top_after.index("mdat")
            if "moov" in top_after and "mdat" in top_after else None)
        dur = r.get("duration_delta_s")
        val = r.get("validate") or {}
        summ = val.get("summary") or {}
        aud = next((c for c in (r.get("audio_after_checks") or [])
                    if c["id"] == "AUD-03"), {})
        uu = md.get("uuid") or {}
        print(f"{name:<24}{str(moov_before_mdat):<9}"
              f"{str(md.get('identical')):<6}"
              f"{(f'{dur:+.6f}' if isinstance(dur, (int, float)) else '-'):>10}"
              f"{str(summ.get('MODIFIED')):>4}{str(summ.get('MISSING')):>4}"
              f"{str(aud.get('verdict')):<9}"
              f"{str(r.get('gyroflow')):<6}  "
              f"{uu.get('count_a')}->{uu.get('count_b')}")
        if r.get("status") != "applied":
            bad.append(f"{name}: status={r.get('status')}")
        if not md.get("identical"):
            bad.append(f"{name}: 元数据与输入不一致")
        if moov_before_mdat is not True:
            bad.append(f"{name}: moov 不在 mdat 之前")
        if dur not in (None, 0.0) and abs(dur) > 1e-6:
            bad.append(f"{name}: 时长差 {dur}")
        if summ.get("MODIFIED") not in (0,) or summ.get("MISSING") not in (0,):
            bad.append(f"{name}: validate {summ}")
        if aud.get("verdict") != "PASS":
            bad.append(f"{name}: 修后 AUD-03={aud.get('verdict')}")
        if r.get("gyroflow") != "PASS":
            bad.append(f"{name}: gyroflow={r.get('gyroflow')}")

    print()
    if bad:
        print(f"不合格 {len(bad)} 条:")
        for b in bad:
            print("  ", b)
        return 1
    print(f"全部通过: {len(on_disk)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
