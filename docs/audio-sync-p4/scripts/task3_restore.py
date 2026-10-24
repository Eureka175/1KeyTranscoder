#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务3：输出 vs 原始 XAVC 全细节差异 + GPAC 最大还原 + Gyroflow 验证。

复用程序已有 API（不另写容器逻辑）:
  * 容器事实: `archive_repair.container_facts` / `uuid_inventory_equal`
  * Sony 提取+恢复: `preservation.sony.SonyPreservationBackend`,
    `preservation.transparent_restore.restore_after_transparent_remux`
  * GPAC: `preservation.gpac.GpacContainerBackend`, `preservation.dji.track_manifest`
  * 音频 sample entry / 时基 / tkhd: `preservation.sample_entry.patch_audio_sample_entries`,
    `core.channel_sync.repair_remux_timescale`, `preservation.isobmf.normalize_track_flags`
  * 消费方验证: `preservation.gyroflow.check`（Gyroflow 无头导出并比对）

模式:
  --compare-final  当前 E: 的 ffmpeg 成品 vs 原始 XAVC：容器差异 + Gyroflow 判定
  --segment        30 s 片段：GPAC 还原路径冒烟（验证 brand/uuid/rtmd/nrtm/Gyroflow）
  --full           全长 GPAC 还原（源视频/数据轨 + 对齐后的 4 路 WAV），输出
                   E:/1kt_work/long_align/deliver/<stem>_4ch_aligned_restored.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"
GF = Path(r"D:\Gyroflow-windows64\Gyroflow.exe")
WORK = REPO / "work" / "task3_20261025"
E_ROOT = Path(r"E:\1kt_work\long_align")


def _run(cmd, timeout=None):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _dev(p: Path) -> str:
    return str(p)


def facts_cmp(a: Path, b: Path) -> dict:
    from archive_repair import container_facts, uuid_inventory_equal
    fa, fb = container_facts(a), container_facts(b)
    keys = ["ftyp_major", "ftyp_compatible", "tref_cdsc", "nrtm",
            "audio_entries", "mvhd", "tracks", "top_level", "meta_scopes"]
    out = {"uuid": uuid_inventory_equal(fa, fb), "diff": {}}
    for k in keys:
        if fa.get(k) != fb.get(k):
            out["diff"][k] = {"source": fa.get(k), "output": fb.get(k)}
    out["source_facts"] = {
        "top_level": fa.get("top_level"), "ftyp_major": fa.get("ftyp_major"),
        "tracks": fa.get("tracks"), "uuid": fa.get("uuid"),
        "nrtm": fa.get("nrtm"), "tref_cdsc": fa.get("tref_cdsc"),
        "mvhd": fa.get("mvhd"),
    }
    out["output_facts"] = {
        "top_level": fb.get("top_level"), "ftyp_major": fb.get("ftyp_major"),
        "tracks": fb.get("tracks"), "uuid": fb.get("uuid"),
        "nrtm": fb.get("nrtm"), "tref_cdsc": fb.get("tref_cdsc"),
        "mvhd": fb.get("mvhd"),
    }
    return out


def streamhash(path: Path, selector: str) -> str:
    r = _run([FFMPEG, "-v", "error", "-nostdin", "-i", path, "-map", selector,
              "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"])
    return r.stdout.strip()


def audio_md5(path: Path, idx: int) -> str:
    r = _run([FFMPEG, "-v", "error", "-nostdin", "-i", path, "-map", f"0:a:{idx}",
              "-f", "md5", "-"])
    return r.stdout.strip()


def probe(path: Path) -> dict:
    r = _run([FFPROBE, "-v", "error", "-show_streams", "-show_format",
              "-of", "json", path])
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def stream_cmp(src: Path, dst: Path) -> dict:
    a, b = probe(src), probe(dst)

    def rows(j):
        return [{
            "type": s.get("codec_type"), "codec": s.get("codec_name"),
            "tag": s.get("codec_tag_string"),
            "w": s.get("width"), "h": s.get("height"),
            "fps": s.get("r_frame_rate"),
            "ch": s.get("channels"), "rate": s.get("sample_rate"),
            "bits": s.get("bits_per_sample") or s.get("bits_per_raw_sample"),
            "dur": s.get("duration"),
        } for s in j.get("streams", [])]

    ra, rb = rows(a), rows(b)
    return {"source": ra, "output": rb, "equal_sets": ra == rb,
            "duration": [a.get("format", {}).get("duration"),
                         b.get("format", {}).get("duration")]}


def gyro_check(original: Path, final: Path, scratch: Path) -> dict:
    from preservation.gyroflow import check
    return check(original, final, GF, scratch)


def _manifest(gpac, media: Path) -> tuple[int, list[dict]]:
    from preservation.dji import track_manifest
    return track_manifest(gpac, media)


def _restore(source: Path, media: Path, audio_adds: list[str], dst: Path,
             work: Path, log=print) -> dict:
    """GPAC 透明还原: media 提供 video/data 轨, audio_adds 提供音频轨。"""
    from preservation.gpac import GpacContainerBackend
    from preservation.transparent_restore import (
        restore_after_transparent_remux,
    )
    from preservation.sample_entry import patch_audio_sample_entries
    from preservation import isobmf
    from core.channel_sync import repair_remux_timescale

    gpac = GpacContainerBackend()
    work.mkdir(parents=True, exist_ok=True)
    movie_ts, tracks = _manifest(gpac, media)
    gpac.movie_timescale = movie_ts
    video = next(t["id"] for t in tracks if t["handler"] == "vide")
    data_ids = [t["id"] for t in tracks
                if t["handler"] not in ("vide", "soun")]
    adds = [f"{media}#{video}"] + list(audio_adds) + \
           [f"{media}#{tid}" for tid in data_ids]
    log(f"restore: media={media.name} movie_ts={movie_ts} "
        f"data_tracks={data_ids}")
    log(f"restore: adds={adds}")
    t0 = time.time()
    stage = restore_after_transparent_remux(source, adds, dst, work, gpac, log)
    log(f"restore: mux+uuid done in {time.time() - t0:.1f}s")
    repair_remux_timescale(dst, log=log)
    sdp = patch_audio_sample_entries(media, dst, log=log)
    log(f"restore: audio sample entries: ok={sdp.get('ok')} "
        f"reason={sdp.get('reason')}")
    desc = isobmf.normalize_track_flags(dst)
    log(f"restore: tkhd normalize: {len(desc)} track(s)")
    return {"stage": str(stage) if stage else None,
            "movie_timescale": movie_ts, "data_track_ids": data_ids,
            "sample_entries_ok": sdp.get("ok"),
            "sample_entries_reason": sdp.get("reason"),
            "tkhd_normalized": len(desc),
            "restore_seconds": round(time.time() - t0, 1)}


def mode_compare_final(source: Path, final: Path, out: Path) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    result = {"mode": "compare_final", "source": str(source),
              "final": str(final)}
    result["container"] = facts_cmp(source, final)
    result["streams"] = stream_cmp(source, final)
    result["gyroflow"] = gyro_check(source, final, WORK / "scratch_cmp")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return result


def mode_segment(source: Path, out: Path, start: float = 600.0,
                 dur: float = 30.0) -> dict:
    from preservation.gpac import GpacContainerBackend
    WORK.mkdir(parents=True, exist_ok=True)
    gpac = GpacContainerBackend()
    seg = WORK / "segment.mp4"
    if not seg.is_file():
        r = _run([gpac.mp4box, "-splitx", f"{start}:{start + dur}",
                  "-out", seg, source])
        if r.returncode != 0 or not seg.is_file():
            raise SystemExit(f"splitx failed: {r.stderr[-500:]}")
    movie_ts, tracks = _manifest(gpac, seg)
    audio_ids = [t["id"] for t in tracks if t["handler"] == "soun"]
    restored = WORK / "segment_restored.mp4"
    work = Path(r"E:\1kt_scratch\task3_work\seg")
    # 元数据 donor 必须是完整源文件: splitx 片段会丢文件级 meta/uuid,
    # 以片段为 donor 会让 Gyroflow 识别不到机型 (实测 IMU=0)。
    info = _restore(source, seg, [f"{seg}#{aid}" for aid in audio_ids],
                    restored, work, log=print)
    # Gyroflow type-3 (camera data): 片段自身 (丢 meta) vs 还原后 (donor 补全)
    gdir = WORK / "gyro_probe"
    gdir.mkdir(parents=True, exist_ok=True)
    def _t3(video, name):
        out3 = gdir / name
        r = _run([GF, "--export-metadata", f"3:{out3}", video], timeout=1800)
        if not out3.is_file():
            return {"error": f"rc={r.returncode}"}
        d = json.loads(out3.read_text(encoding="utf-8"))
        first = d[0].get("org_quat") if d else None
        qvar = sum(1 for e in d[1:] if any(
            abs((e.get("org_quat") or [1, 0, 0, 0])[i] - first[i]) > 1e-9
            for i in range(4)))
        return {"frames": len(d), "quat_varying": qvar,
                "first_timestamp_ms": d[0].get("timestamp_ms") if d else None}
    result = {"mode": "segment", "source": str(source),
              "segment": str(seg), "restored": str(restored),
              "restore": info,
              "container": facts_cmp(source, restored),
              "segment_container": facts_cmp(seg, restored),
              "streams": stream_cmp(seg, restored),
              "gyroflow_type3": {"segment": _t3(seg, "seg_camera_type3.json"),
                                  "restored": _t3(restored, "seg_restored_camera_type3.json")}}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return result


def mode_full(source: Path, stem: str, out: Path) -> dict:
    from preservation.gpac import GpacContainerBackend
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = Path(r"E:\1kt_scratch\temp")
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    fine = REPO / "data" / "_scratch" / "aligned_fine" / stem
    wavs = [fine / f"ch{i}_pol.wav" for i in range(4)]
    for w in wavs:
        if not w.is_file():
            raise SystemExit(f"missing aligned wav: {w}")
    dst = E_ROOT / "deliver" / f"{stem}_4ch_aligned_restored.mp4"
    work = E_ROOT / "restore_work" / stem
    audio_adds = [f"{_dev(w)}#audio" for w in wavs]
    info = _restore(source, source, audio_adds, dst, work, log=print)
    result = {"mode": "full", "source": str(source), "output": str(dst),
              "restore": info,
              "container": facts_cmp(source, dst),
              "streams": stream_cmp(source, dst),
              "video_streamhash_equal":
                  streamhash(source, "0:v:0") == streamhash(dst, "0:v:0"),
              "gyroflow": gyro_check(source, dst, WORK / f"scratch_{stem}")}
    result["audio_md5"] = [
        {"wav": str(w), "wav_md5": audio_md5(w, 0),
         "out_md5": audio_md5(dst, i),
         "match": audio_md5(w, 0) == audio_md5(dst, i)}
        for i, w in enumerate(wavs)]
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return result


def mode_stage(source: Path, stem: str, out: Path) -> dict:
    """只做 GPAC 元数据抽取 + mux stage（不做 GPAC 的多次整文件重写）。

    产出 stage 后由 work/fast_restore.py 做字节级元数据补齐（一次顺序拷贝）。
    """
    from preservation.gpac import GpacContainerBackend
    from preservation.dji import track_manifest
    from preservation.sony import SonyPreservationBackend

    WORK.mkdir(parents=True, exist_ok=True)
    tmp = Path(r"E:\1kt_scratch\temp")
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    fine = REPO / "data" / "_scratch" / "aligned_fine" / stem
    wavs = [fine / f"ch{i}_pol.wav" for i in range(4)]
    for w in wavs:
        if not w.is_file():
            raise SystemExit(f"missing aligned wav: {w}")
    work = E_ROOT / "restore_work" / stem
    work.mkdir(parents=True, exist_ok=True)
    gpac = GpacContainerBackend()
    movie_ts, tracks = track_manifest(gpac, source)
    gpac.movie_timescale = movie_ts
    video = next(t["id"] for t in tracks if t["handler"] == "vide")
    data_ids = [t["id"] for t in tracks
                if t["handler"] not in ("vide", "soun")]
    # 音频必须先转成 ffmpeg 的 .mov (pcm_s24be / QuickTime in24) 再交给 GPAC。
    # 实测 GPAC 直接 -add wav#audio 时写出的 in24 payload 与 WAV 内容完全不符
    # (解码结果与对齐 WAV 零样本相等、最佳 lag 6.4 万采样)，而生产
    # channel-sync 透明模式走的就是 ffmpeg .mov 中间文件，这里与生产保持一致。
    audio_dir = work / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    movs = []
    for i, w in enumerate(wavs):
        m = audio_dir / f"ch{i}_pol.mov"
        if not m.is_file() or m.stat().st_mtime < w.stat().st_mtime:
            r = _run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", w,
                      "-c:a", "pcm_s24be", "-f", "mov", m])
            if r.returncode != 0 or not m.is_file():
                raise SystemExit(f"wav->mov failed: {r.stderr[-400:]}")
        movs.append(m)
    adds = [f"{source}#{video}"] + [f"{m}#audio" for m in movs] + \
           [f"{source}#{d}" for d in data_ids]
    sony = SonyPreservationBackend(gpac, None)
    bundle = sony.extract(source, work / "metadata")
    stage = work / "transparent_stage.mov"
    if stage.exists():
        stage.unlink()
    print(f"stage: movie_ts={movie_ts} data_tracks={data_ids} "
          f"uuid_boxes={len(bundle.boxes)} nrtm={bundle.nrtm is not None}",
          flush=True)
    gpac.mux_new(stage, adds)
    result = {"mode": "stage", "source": str(source), "stage": str(stage),
              "bundle_dir": str(work / "metadata"),
              "audio_movs": [str(m) for m in movs],
              "movie_timescale": movie_ts, "data_track_ids": data_ids,
              "uuid_boxes": len(bundle.boxes),
              "nrtm": bundle.nrtm is not None,
              "brand": bundle.major_brand,
              "stage_bytes": stage.stat().st_size}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True,
                    choices=["compare-final", "segment", "full", "stage"])
    ap.add_argument("--source", default=r"F:\1KeyTranscoder\data\align_4ch_proof\20260919_C1305.MP4")
    ap.add_argument("--final", default=r"E:\1kt_work\long_align\deliver\20260919_C1305_4ch_aligned_final.mp4")
    ap.add_argument("--stem", default="20260919_C1305")
    ap.add_argument("--out", default=r"F:\1KeyTranscoder\work\task3_20261025\task3_result.json")
    ap.add_argument("--start", type=float, default=600.0)
    ap.add_argument("--dur", type=float, default=30.0)
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if args.mode == "compare-final":
        res = mode_compare_final(Path(args.source), Path(args.final), out)
    elif args.mode == "segment":
        res = mode_segment(Path(args.source), out, args.start, args.dur)
    elif args.mode == "stage":
        res = mode_stage(Path(args.source), args.stem, out)
    else:
        res = mode_full(Path(args.source), args.stem, out)
    print(f"\n[{args.mode}] done in {time.time() - t0:.1f}s -> {out}")
    print(json.dumps(res.get("gyroflow", {}), ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
