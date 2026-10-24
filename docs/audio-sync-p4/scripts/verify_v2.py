#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全长还原产物的一套验证：fast_restore + mdhd 修复 + 容器/流/Gyroflow 逐项对比。

所有对比都用程序已有 API 或 ffmpeg/ffprobe 的基本能力；Gyroflow type-3 与
源导出（src_t3）做同样解析后比较帧数/变化四元数数/首帧时间戳。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "work"))
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
GF = Path(r"D:\Gyroflow-windows64\Gyroflow.exe")
WORK = REPO / "work" / "task3_20261025"


def run(cmd, timeout=None):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def streamhash(p: Path, selector: str) -> str:
    return run([FFMPEG, "-v", "error", "-i", p, "-map", selector, "-c", "copy",
                "-f", "streamhash", "-hash", "sha256", "-"]).stdout.strip()


def audio_md5(p: Path, idx: int) -> str:
    return run([FFMPEG, "-v", "error", "-i", p, "-map", f"0:a:{idx}",
                "-f", "md5", "-"]).stdout.strip()


def gyro_t3(video: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run([GF, "--export-metadata", f"3:{out}", video], timeout=7200)
    if not out.is_file():
        return {"error": f"rc={r.returncode}"}
    d = json.loads(out.read_text(encoding="utf-8"))
    n = len(d)
    first = d[0].get("org_quat") if n else None
    qvar = sum(1 for e in d[1:] if any(
        abs((e.get("org_quat") or [1, 0, 0, 0])[i] - first[i]) > 1e-9
        for i in range(4)))
    return {"frames": n, "quat_varying": qvar,
            "first_ts": d[0].get("timestamp_ms") if n else None,
            "last_ts": d[-1].get("timestamp_ms") if n else None,
            "first_quat": first, "path": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--wav-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    stem = args.stem
    src, stage, bundle = Path(args.source), Path(args.stage), Path(args.bundle)
    wav_dir = Path(args.wav_dir)
    dst = Path(r"E:\1kt_work\long_align\deliver") / f"{stem}_4ch_aligned_restored.mp4"
    res: dict = {"stem": stem}

    import fast_restore as fr
    res["build"] = fr.build(stage=stage, source=src, bundle_dir=bundle, dst=dst)
    res["postprocess"] = fr.postprocess(source=src, dst=dst)
    r = subprocess.run([sys.executable, str(REPO / "work" / "patch_mdhd.py"),
                        str(dst)], capture_output=True, text=True)
    res["mdhd"] = r.stdout.strip().splitlines()

    from archive_repair import container_facts, uuid_inventory_equal
    fa, fb = container_facts(src), container_facts(dst)
    res["container"] = {
        "brand_equal": [fa.get("ftyp_major"), fb.get("ftyp_major")],
        "compat_equal": fa.get("ftyp_compatible") == fb.get("ftyp_compatible"),
        "tracks_equal": [(t.get("handler"), t.get("entry"))
                         for t in fa.get("tracks", [])]
        == [(t.get("handler"), t.get("entry")) for t in fb.get("tracks", [])],
        "uuid": uuid_inventory_equal(fa, fb),
        "nrtm": [fa.get("nrtm"), fb.get("nrtm")],
        "tref": [len(fa.get("tref_cdsc") or []), len(fb.get("tref_cdsc") or [])],
        "audio_entries_equal": fa.get("audio_entries") == fb.get("audio_entries"),
        "mvhd_equal": fa.get("mvhd") == fb.get("mvhd"),
        "top_level": [fa.get("top_level"), fb.get("top_level")],
    }
    res["video_streamhash"] = {
        "source": streamhash(src, "0:v:0"),
        "restored": streamhash(dst, "0:v:0"),
    }
    res["video_streamhash"]["equal"] = (
        res["video_streamhash"]["source"] == res["video_streamhash"]["restored"])
    res["rtmd_streamhash"] = {
        "source": streamhash(src, "0:d:0"),
        "restored": streamhash(dst, "0:d:0"),
    }
    res["rtmd_streamhash"]["equal"] = (
        res["rtmd_streamhash"]["source"] == res["rtmd_streamhash"]["restored"])
    res["audio_md5"] = []
    for i in range(4):
        w = wav_dir / f"ch{i}_pol.wav"
        if w.is_file():
            a, b = audio_md5(w, 0), audio_md5(dst, i)
            res["audio_md5"].append({"wav": a, "restored": b, "match": a == b})
    gdir = WORK / "gyro_probe"
    res["gyro"] = {"restored": gyro_t3(dst, gdir / f"{stem}_restored_t3.json")}
    src3 = gdir / ("src_camera_type3.json" if stem == "20260919_C1305"
                   else f"{stem}_src_t3.json")
    if src3.is_file():
        d = json.loads(src3.read_text(encoding="utf-8"))
        first = d[0].get("org_quat") if d else None
        qvar = sum(1 for e in d[1:] if any(
            abs((e.get("org_quat") or [1, 0, 0, 0])[i] - first[i]) > 1e-9
            for i in range(4)))
        res["gyro"]["source"] = {
            "frames": len(d), "quat_varying": qvar,
            "first_ts": d[0].get("timestamp_ms") if d else None,
            "last_ts": d[-1].get("timestamp_ms") if d else None,
            "first_quat": first,
        }
        if "frames" in res["gyro"]["restored"]:
            res["gyro"]["timestamp_diff_ms"] = {
                "first": round(abs(
                    (res["gyro"]["source"]["first_ts"] or 0)
                    - (res["gyro"]["restored"]["first_ts"] or 0)), 3),
                "last": round(abs(
                    (res["gyro"]["source"]["last_ts"] or 0)
                    - (res["gyro"]["restored"]["last_ts"] or 0)), 3),
            }
    Path(args.out).write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str)[:6000])
    print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
