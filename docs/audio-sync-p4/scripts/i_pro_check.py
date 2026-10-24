#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务5 验证：把任务3的 GPAC 元数据还原流程用在 I:/2026.09.24 的专业机位文件上。

这些文件是 2CH（A7M4/FX3/FX30），不参与 4CH 对齐；但它们的 Sony 容器元数据
（brand/uuid/nrtm/tref/rtmd 轨）应与 testsets 长素材一样被还原。此脚本在**短文件**
上跑：GPAC extract + mux stage（源所有轨原样）+ fast_restore 字节级补齐 +
mdhd 修复，然后逐项对比容器事实、视频/rtmd 码流、音频 md5、Gyroflow type-3。
"""
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
    r = run([GF, "--export-metadata", f"3:{out}", video], timeout=3600)
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
            "first_quat": first}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    src = Path(args.source)
    stem = src.stem
    work = Path(r"E:\1kt_scratch\i_pro") / stem
    work.mkdir(parents=True, exist_ok=True)
    import fast_restore as fr
    from preservation.gpac import GpacContainerBackend
    from preservation.dji import track_manifest
    from preservation.sony import SonyPreservationBackend

    gpac = GpacContainerBackend()
    movie_ts, tracks = track_manifest(gpac, src)
    gpac.movie_timescale = movie_ts
    ids = [t["id"] for t in tracks]
    sony = SonyPreservationBackend(gpac, None)
    bundle = sony.extract(src, work / "metadata")
    stage = work / "transparent_stage.mov"
    gpac.mux_new(stage, [f"{src}#{i}" for i in ids])
    dst = work / f"{stem}_restored.mp4"
    res: dict = {"source": str(src), "stage": str(stage), "out": str(dst),
                 "movie_timescale": movie_ts,
                 "tracks": [(t["id"], t["handler"], t.get("entry"))
                            for t in tracks]}
    res["build"] = fr.build(stage=stage, source=src,
                            bundle_dir=work / "metadata", dst=dst)
    res["postprocess"] = fr.postprocess(source=src, dst=dst)
    r = subprocess.run([sys.executable, str(REPO / "work" / "patch_mdhd.py"),
                        str(dst)], capture_output=True, text=True)
    res["mdhd"] = r.stdout.strip().splitlines()
    r = subprocess.run([sys.executable, str(REPO / "work" / "patch_movie_timing.py"),
                        str(src), str(dst)], capture_output=True, text=True)
    res["movie_timing"] = r.stdout.strip().splitlines()

    from archive_repair import container_facts, uuid_inventory_equal
    fa, fb = container_facts(src), container_facts(dst)
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
        "mvhd": fa.get("mvhd") == fb.get("mvhd"),
        "top_level": [fa.get("top_level"), fb.get("top_level")],
    }
    res["video_streamhash_equal"] = streamhash(src, "0:v:0") == streamhash(
        dst, "0:v:0")
    res["rtmd_streamhash_equal"] = streamhash(src, "0:d:0") == streamhash(
        dst, "0:d:0")
    n_audio = len([t for t in tracks if t["handler"] == "soun"])
    res["audio_md5"] = [
        {"source": audio_md5(src, i), "restored": audio_md5(dst, i),
         "match": audio_md5(src, i) == audio_md5(dst, i)}
        for i in range(n_audio)]
    gdir = WORK / "gyro_probe"
    res["gyro"] = {"source": gyro_t3(src, gdir / f"{stem}_src_t3.json"),
                   "restored": gyro_t3(dst, gdir / f"{stem}_restored_t3.json")}
    if all("first_ts" in res["gyro"][k] for k in ("source", "restored")):
        res["gyro"]["timestamp_diff_ms"] = {
            "first": round(abs(res["gyro"]["source"]["first_ts"]
                               - res["gyro"]["restored"]["first_ts"]), 3),
            "last": round(abs(res["gyro"]["source"]["last_ts"]
                              - res["gyro"]["restored"]["last_ts"]), 3),
        }
    Path(args.out).write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps({k: res.get(k) for k in (
        "stage", "movie_timescale", "tracks", "container",
        "video_streamhash_equal", "rtmd_streamhash_equal", "audio_md5",
        "gyro", "mdhd")}, ensure_ascii=False, indent=1, default=str)[:5000])
    print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
