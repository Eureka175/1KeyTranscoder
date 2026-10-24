#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小文件（真实 Sony 4CH 短片段）上的 GPAC 还原对照实验。

目的：把"标准 transparent_restore"与"fast_restore 字节级还原"在同一个**真实
Sony 源**（testsets 的 3 秒 4CH 片段）上对比，并用 Gyroflow type-3 验证
消费方是否读到同样的相机数据 —— 这样可以在秒级迭代，而不是每次 50 GiB。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "work"))
from preservation import isobmf  # noqa: E402
from preservation.gpac import GpacContainerBackend  # noqa: E402
from preservation.dji import track_manifest  # noqa: E402
from preservation.sony import SonyPreservationBackend  # noqa: E402
from preservation.transparent_restore import (  # noqa: E402
    restore_after_transparent_remux,
)

FFMPEG = REPO / "tools" / "ffmpeg.exe"
GF = Path(r"D:\Gyroflow-windows64\Gyroflow.exe")
WORK = REPO / "work" / "task3_20261025" / "shortcheck"


def run(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def facts(p: Path) -> dict:
    from archive_repair import container_facts
    f = container_facts(p)
    return {k: f.get(k) for k in ("ftyp_major", "ftyp_compatible", "nrtm",
                                  "tref_cdsc", "audio_entries", "mvhd",
                                  "top_level")} | {
        "tracks": [(t.get("handler"), t.get("entry"))
                   for t in f.get("tracks", [])],
        "uuid_n": len(f.get("uuid", [])),
    }


def gyro_type3(video: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run([GF, "--export-metadata", f"3:{out}", video])
    if not out.is_file():
        return {"error": f"rc={r.returncode}", "stderr": (r.stderr or "")[-200:]}
    d = json.loads(out.read_text(encoding="utf-8"))
    n = len(d)
    first = d[0].get("org_quat") if n else None
    qvar = sum(1 for e in d[1:] if any(
        abs((e.get("org_quat") or [1, 0, 0, 0])[i] - first[i]) > 1e-9
        for i in range(4)))
    return {"frames": n, "quat_varying": qvar,
            "first_ts": d[0].get("timestamp_ms") if n else None,
            "first_quat": first}


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"F:\1KeyTranscoder\testsets\a7m5_4ch_speech\20260903\A7M5\20260903_C1093.MP4")
    WORK.mkdir(parents=True, exist_ok=True)
    gpac = GpacContainerBackend()
    movie_ts, tracks = track_manifest(gpac, src)
    gpac.movie_timescale = movie_ts
    ids = [t["id"] for t in tracks]
    print("source:", src, "ts", movie_ts,
          "tracks", [(t["id"], t["handler"]) for t in tracks], flush=True)

    # ---------- 标准透明还原（production API；短文件秒级）
    std_out = WORK / "std_restored.mp4"
    std_work = WORK / "std_work"
    std_work.mkdir(parents=True, exist_ok=True)
    try:
        stage = restore_after_transparent_remux(
            src, [f"{src}#{i}" for i in ids], std_out, std_work, gpac,
            print)
        std_ok = std_out.is_file()
    except Exception as exc:                  # noqa: BLE001
        print("standard restore failed:", exc)
        std_ok = False
    if std_ok:
        from core.channel_sync import repair_remux_timescale
        from preservation.sample_entry import patch_audio_sample_entries
        repair_remux_timescale(std_out, log=print)
        print("sample entries:", patch_audio_sample_entries(src, std_out))
        print("tkhd:", len(isobmf.normalize_track_flags(std_out)))

    # ---------- fast_restore
    fast_out = WORK / "fast_restored.mp4"
    fast_work = WORK / "fast_work"
    fast_work.mkdir(parents=True, exist_ok=True)
    fast_stage = fast_work / "transparent_stage.mov"
    if fast_stage.exists():
        fast_stage.unlink()
    sony = SonyPreservationBackend(gpac, None)
    bundle = sony.extract(src, fast_work / "metadata")
    gpac.mux_new(fast_stage, [f"{src}#{i}" for i in ids])
    sys.path.insert(0, str(REPO / "work"))
    import fast_restore as fr
    build = fr.build(stage=fast_stage, source=src,
                     bundle_dir=fast_work / "metadata", dst=fast_out)
    post = fr.postprocess(source=src, dst=fast_out)
    print("fast build:", json.dumps(build, ensure_ascii=False)[:500])
    print("fast post:", post)

    # 时间基修正（两种产物都做）
    import subprocess as sp
    for p in (std_out, fast_out):
        if p.is_file():
            r = sp.run([sys.executable, str(REPO / "work" / "patch_mdhd.py"),
                        str(p)], capture_output=True, text=True)
            print("mdhd", p.name, r.stdout.strip().replace("\n", "; "))

    # ---------- 对比
    res = {"source": str(src), "movie_ts": movie_ts,
           "tracks": [(t["id"], t["handler"], t.get("entry")) for t in tracks],
           "facts": {"source": facts(src)},
           "gyro_type3": {"source": gyro_type3(src, WORK / "src_t3.json")}}
    for name, p in (("std", std_out), ("fast", fast_out)):
        if p.is_file():
            res["facts"][name] = facts(p)
            res["gyro_type3"][name] = gyro_type3(
                p, WORK / f"{name}_t3.json")
    (WORK / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["facts"], ensure_ascii=False, indent=1)[:3000])
    print(json.dumps(res["gyro_type3"], ensure_ascii=False, indent=1)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
