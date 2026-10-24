#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从最终还原交付文件生成"保留 metadata"的抽检片段（P5 clips 的增强版）。

- GPAC splitx 按旧片段时间点切段（保留 brand/轨道/轨级 uuid/tref/rtmd 轨）；
- work/clip_meta.py 追加文件级 root uuid + meta（nrtm/XMP）；
- 校验：容器事实（brand/uuid 9/nrtm/tref/6 轨）、Gyroflow type-3 相机数据
  （帧数、变化四元数、与源全长 type3 的最近帧匹配）。

输出到 E:\\1kt_work\\long_align\\clips_refined\\（旧 clips_final 保留不动）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "work"))
import clip_meta  # noqa: E402

FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
GPAC = REPO / "tools" / "GPAC" / "mp4box.exe"
GF = Path(r"D:\Gyroflow-windows64\Gyroflow.exe")
OUT = Path(r"E:\1kt_work\long_align\clips_refined")
WORK = REPO / "work" / "task3_20261025"

SPECS = [
    ("20260919_C1305", 608.0),
    ("20260919_C1305", 1446.0),
    ("20260919_C1305", 2527.0),
    ("20260919_C1306", 153.0),
    ("20260919_C1306", 1569.0),
]
DUR = 10.09


def run(cmd, timeout=None):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


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


def nearest_source_time(src3: Path, quat: list[float]) -> float | None:
    if not src3.is_file() or not quat:
        return None
    d = json.loads(src3.read_text(encoding="utf-8"))
    best, idx = 1e9, -1
    for i, e in enumerate(d):
        q = e.get("org_quat")
        if not q:
            continue
        dist = sum(abs(q[k] - quat[k]) for k in range(4))
        if dist < best:
            best, idx = dist, i
    return d[idx].get("timestamp_ms") / 1000.0 if idx >= 0 else None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for stem, start in SPECS:
        donor = Path(r"E:\1kt_work\long_align\deliver") / \
            f"{stem}_4ch_aligned_restored.mp4"
        raw = OUT / f"{stem}_restored_clip_{int(start)}s_raw.mp4"
        clip = OUT / f"{stem}_restored_clip_{int(start)}s.mp4"
        range_s = f"{start}:{start + DUR}"
        r = run([GPAC, "-splitx", range_s, "-out", raw, donor], timeout=1800)
        if r.returncode != 0 or not raw.is_file():
            results.append({"stem": stem, "start": start,
                            "error": f"splitx rc={r.returncode}",
                            "stderr": (r.stderr or "")[-300:]})
            continue
        clip_meta.main.__globals__  # noqa: B018  (keep import warm)
        sys.argv = ["clip_meta.py", "--clip", str(raw), "--donor",
                    str(donor), "--out", str(clip)]
        clip_meta.main()
        raw.unlink(missing_ok=True)
        from archive_repair import container_facts
        f = container_facts(clip)
        t3 = gyro_t3(clip, WORK / "gyro_probe" /
                     f"{stem}_clip{int(start)}_t3.json")
        src3 = WORK / "gyro_probe" / (
            "src_camera_type3.json" if stem == "20260919_C1305"
            else f"{stem}_src_t3.json")
        matched = nearest_source_time(src3, t3.get("first_quat"))
        results.append({
            "stem": stem, "start": start, "file": str(clip),
            "container": {
                "brand": f.get("ftyp_major"),
                "tracks": [(t.get("handler"), t.get("entry"))
                           for t in f.get("tracks", [])],
                "uuid": len(f.get("uuid", [])),
                "nrtm": f.get("nrtm"),
                "tref": len(f.get("tref_cdsc") or []),
                "mvhd": f.get("mvhd"),
            },
            "gyro": t3,
            "first_frame_matched_source_time_s": matched,
        })
    rep = WORK / "clips_metadata_report.json"
    rep.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=1)[:8000])
    print("written", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
