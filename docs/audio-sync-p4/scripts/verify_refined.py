#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 refine_restored.py 生成的 v2 产物：与源逐项对比（容器/码流/音频/Gyroflow）。"""
from __future__ import annotations

import argparse
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
GDIR = WORK / "gyro_probe"


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
            "last_ts": d[-1].get("timestamp_ms") if n else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--refined", required=True)
    ap.add_argument("--wav-dir", required=True)
    ap.add_argument("--baseline", required=True,
                    help="verify_v2_<stem>.json（含源 streamhash 与 gyro 源导出）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    src, ref = Path(args.source), Path(args.refined)
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    res: dict = {"stem": args.stem, "source": str(src), "refined": str(ref)}

    from archive_repair import container_facts, uuid_inventory_equal
    fa, fb = container_facts(src), container_facts(ref)
    res["container"] = {
        "brand": [fa.get("ftyp_major"), fb.get("ftyp_major")],
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
        "source_known": baseline["video_streamhash"]["source"],
        "refined": streamhash(ref, "0:v:0")}
    res["video_streamhash"]["equal"] = (
        res["video_streamhash"]["source_known"]
        == res["video_streamhash"]["refined"])
    res["rtmd_streamhash"] = {
        "source_known": baseline["rtmd_streamhash"]["source"],
        "refined": streamhash(ref, "0:d:0")}
    res["rtmd_streamhash"]["equal"] = (
        res["rtmd_streamhash"]["source_known"]
        == res["rtmd_streamhash"]["refined"])
    wav_dir = Path(args.wav_dir)
    res["audio_md5"] = []
    for i in range(4):
        w = wav_dir / f"ch{i}_pol.wav"
        if not w.is_file():
            continue
        a, b = audio_md5(w, 0), audio_md5(ref, i)
        res["audio_md5"].append({"wav": a, "refined": b, "match": a == b})
    try:
        sys.path.insert(0, str(REPO / "work"))
        import box_diff
        a, b = box_diff.collect(src), box_diff.collect(ref)
        res["box_diff"] = {
            "box_count": [len(a), len(b)],
            "missing": ["/".join(k) for k in a if k not in b],
            "added": ["/".join(k) for k in b if k not in a],
            "size_diff_paths": ["/".join(k) for k in a
                                if k in b and a[k][0] != b[k][0]],
            "hash_diff_paths": ["/".join(k) for k in a
                                if k in b and a[k][1] is not None
                                and b[k][1] is not None
                                and a[k][1] != b[k][1]],
        }
    except Exception as exc:  # noqa: BLE001
        res["box_diff"] = {"error": str(exc)}
    res["gyro"] = {"refined": gyro_t3(
        ref, GDIR / f"{args.stem}_refined_t3.json")}
    src3 = GDIR / ("src_camera_type3.json" if args.stem == "20260919_C1305"
                   else f"{args.stem}_src_t3.json")
    if src3.is_file():
        d = json.loads(src3.read_text(encoding="utf-8"))
        first = d[0].get("org_quat") if d else None
        qvar = sum(1 for e in d[1:] if any(
            abs((e.get("org_quat") or [1, 0, 0, 0])[i] - first[i]) > 1e-9
            for i in range(4)))
        res["gyro"]["source"] = {"frames": len(d), "quat_varying": qvar,
                                 "first_ts": d[0].get("timestamp_ms") if d else None,
                                 "last_ts": d[-1].get("timestamp_ms") if d else None}
        if "first_ts" in res["gyro"]["refined"]:
            res["gyro"]["timestamp_diff_ms"] = {
                "first": round(abs(res["gyro"]["source"]["first_ts"]
                                   - res["gyro"]["refined"]["first_ts"]), 3),
                "last": round(abs(res["gyro"]["source"]["last_ts"]
                                  - res["gyro"]["refined"]["last_ts"]), 3),
            }
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
