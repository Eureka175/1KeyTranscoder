# -*- coding: utf-8 -*-
"""Metrics for the 4K native hardware evaluation matrix (hw4k_matrix).

Reads state.jsonl, then for every output computes:
  * container/video bitrate + 1 s / 3 s window statistics (RC behaviour)
  * effective caps (max-bitrate/VBV) from 1kt's per-file log / command line
  * VMAF / PSNR / SSIM against the matching source excerpt (full clip,
    hardware-decoded, scaled to 1280 wide, preserving aspect)
  * encoder-internal fps (raw NVEncC/QSVEncC log or software progress line)
    and pipeline fps
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"F:\1KeyTranscoder")
BASE = ROOT / "work" / "hw4k_matrix"
CLIPS = BASE / "clips"
FFPROBE = ROOT / "tools" / "ffprobe.exe"
FFMPEG = ROOT / "tools" / "ffmpeg.exe"
QUALITY_DIR = BASE / "quality"
METRIC_SECONDS = 0.0  # 0 = full clip


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _ffprobe_json(args: list[str]) -> dict:
    proc = _run([str(FFPROBE), "-v", "error", "-of", "json", *args])
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout or "{}")


def _video_packets(path: Path) -> tuple[float, int, list[float]]:
    data = _ffprobe_json([
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,dts_time,size",
        "-show_entries", "format=duration",
        str(path),
    ])
    duration = float(data.get("format", {}).get("duration") or 0.0)
    buckets: dict[int, int] = {}
    total = 0
    for p in data.get("packets", []):
        size = int(p.get("size") or 0)
        total += size
        t = p.get("pts_time") or p.get("dts_time") or "0"
        try:
            t = float(t)
        except (TypeError, ValueError):
            t = 0.0
        buckets[int(t)] = buckets.get(int(t), 0) + size
    return duration, total, [b * 8 / 1e6 for _, b in sorted(buckets.items())]


def _stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "max": 0.0, "p95": 0.0, "cv": 0.0}
    s = sorted(values)
    mean = sum(s) / len(s)
    p95 = s[max(0, math.ceil(0.95 * len(s)) - 1)]
    var = sum((x - mean) ** 2 for x in s) / (len(s) - 1) if len(s) > 1 else 0
    cv = (math.sqrt(var) / mean) if mean else 0
    return {"mean": round(mean, 3), "max": round(s[-1], 3),
            "p95": round(p95, 3), "cv": round(cv, 4)}


def _src_info(path: Path) -> dict:
    data = _ffprobe_json(["-show_streams", "-show_format", str(path)])
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "src_bitrate_kbps": round(float(fmt.get("bit_rate") or 0) / 1000, 1),
        "src_duration": float(fmt.get("duration") or 0),
        "src_frames": int(v.get("nb_frames") or 0),
        "src_width": v.get("width"),
        "src_height": v.get("height"),
        "src_codec": v.get("codec_name"),
        "src_pix_fmt": v.get("pix_fmt"),
    }


def _quality(enc: Path, ref: Path) -> dict:
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(str(enc).encode("utf-8")).hexdigest()[:10]
    log_name = f"vmaf_{tag}.json"
    graph = (
        "[0:v]scale_cuda=1280:-2:format=yuv420p,hwdownload,format=yuv420p,"
        "split=3[d1][d2][d3];"
        "[1:v]scale_cuda=1280:-2:format=yuv420p,hwdownload,format=yuv420p,"
        "split=3[r1][r2][r3];"
        f"[d1][r1]libvmaf=n_threads=16:log_fmt=json:log_path={log_name}[v];"
        "[d2][r2]psnr[p];[d3][r3]ssim[s]"
    )
    cmd = [
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "info",
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(enc),
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(ref),
    ]
    if METRIC_SECONDS:
        cmd += ["-t", f"{METRIC_SECONDS:.3f}"]
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[p]",
            "-map", "[s]", "-f", "null", "-"]
    proc = _run(cmd, cwd=QUALITY_DIR)
    out = {"vmaf": None, "psnr": None, "ssim": None}
    log_path = QUALITY_DIR / log_name
    if log_path.is_file():
        try:
            payload = json.loads(log_path.read_text(encoding="utf-8"))
            out["vmaf"] = round(
                float(payload["pooled_metrics"]["vmaf"]["mean"]), 4)
        except Exception:                              # noqa: BLE001
            pass
    text = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"PSNR .*?average:([\d.]+)", text)
    if m:
        out["psnr"] = round(float(m.group(1)), 4)
    m = re.search(r"SSIM .*?All:([\d.]+) \(([\d.]+)\)", text)
    if m:
        out["ssim"] = round(float(m.group(1)), 6)
    if proc.returncode != 0:
        out.setdefault("error", "ffmpeg quality failed")
    return out


def _parse_log(log: Path, src_kbps: float) -> dict:
    info: dict = {"cap_maxrate_kbps": None, "cap_bufsize_kbps": None}
    if not log.is_file():
        return info
    text = log.read_text(encoding="utf-8", errors="replace")
    m = re.search(
        r"NVENC_VBV \| class=(\S+) \| OB=([\d.]+) kbps \| "
        r"max_bitrate=(\d+) kbps \| vbv_bufsize=(\d+) kbps", text)
    if m:
        info.update({"source_class": m.group(1),
                     "ob_kbps": float(m.group(2)),
                     "cap_maxrate_kbps": int(m.group(3)),
                     "cap_bufsize_kbps": int(m.group(4))})
    m = re.search(r"CLASSIFICATION \| source_class=(\S+)", text)
    if m:
        info.setdefault("source_class", m.group(1))
    m = re.search(r"COMMAND \| (.*)", text)
    if m:
        cmd = m.group(1)
        if info["cap_maxrate_kbps"] is None:
            cm = re.search(r"--max-bitrate (\d+)", cmd) or \
                re.search(r"vbv-maxrate=(\d+)", cmd) or \
                re.search(r"\bmbr=(\d+)", cmd)
            if cm:
                info["cap_maxrate_kbps"] = int(cm.group(1))
        if info["cap_bufsize_kbps"] is None:
            cm = re.search(r"--vbv-bufsize (\d+)", cmd) or \
                re.search(r"vbv-bufsize=(\d+)", cmd)
            if cm:
                info["cap_bufsize_kbps"] = int(cm.group(1))
        info["command"] = cmd[:400]
    m = re.search(r"DONE \| elapsed=([\d.]+) sec", text)
    if m:
        info["pipeline_sec"] = float(m.group(1))
    info.setdefault("ob_kbps", src_kbps)
    info["_text"] = text
    return info


def _raw_encoder_fps(run_dir: Path) -> tuple[float | None, str]:
    for pattern, tag in (("*.nvencc.log", "nvenc"),
                         ("*.qsvencc.log", "qsv")):
        for p in run_dir.rglob(pattern):
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.findall(r"encoded (\d+) frames, ([\d.]+) fps", text)
            if m:
                return float(m[-1][1]), tag
    return None, ""


def _eval_output(rec: dict, out: Path,
                 src_cache: dict[str, dict]) -> dict:
    clip = out.stem
    ref = CLIPS / f"{clip}.MP4"
    src = src_cache.get(str(ref))
    if src is None:
        src = _src_info(ref)
        src_cache[str(ref)] = src
    duration, vbytes, windows = _video_packets(out)
    w = _stats(windows)
    log = Path(rec["run_dir"]) / "logs" / "files" / (
        f"{clip}_{rec['preset'].upper()}.log")
    log_info = _parse_log(log, src["src_bitrate_kbps"])
    enc_fps, enc_tag = _raw_encoder_fps(Path(rec["run_dir"]))
    if enc_fps is None:
        prog = re.findall(
            r"PROGRESS \| frames=(\d+)/\d+ \| elapsed=([\d.]+) sec",
            log_info.get("_text", ""))
        if prog:
            done, el = prog[-1]
            if float(el) > 0:
                enc_fps = round(int(done) / float(el), 2)
                enc_tag = rec["encoder"]
    frames = src["src_frames"]
    pipeline = log_info.get("pipeline_sec")
    row = {
        "key": rec["key"], "encoder": rec["encoder"],
        "preset": rec["preset"], "clip": clip,
        "output": str(out),
        "output_mb": round(out.stat().st_size / 1e6, 2),
        "duration": round(duration, 3), "frames": frames,
        "video_mbps": round(vbytes * 8 / 1e6 / duration, 3) if duration else None,
        "win_mean_mbps": w["mean"], "win_max_mbps": w["max"],
        "win_p95_mbps": w["p95"], "win_cv": w["cv"],
        "source_class": log_info.get("source_class"),
        "ob_kbps": log_info.get("ob_kbps"),
        "cap_maxrate_kbps": log_info.get("cap_maxrate_kbps"),
        "cap_bufsize_kbps": log_info.get("cap_bufsize_kbps"),
        "pipeline_sec": pipeline,
        "pipeline_fps": round(frames / pipeline, 1) if pipeline else None,
        "encoder_fps": enc_fps, "encoder_tag": enc_tag,
        "src_mbps": round(src["src_bitrate_kbps"] / 1000, 3),
        "src_width": src["src_width"], "src_height": src["src_height"],
        "src_codec": src["src_codec"], "src_pix_fmt": src["src_pix_fmt"],
        "wall_sec": rec.get("wall_sec"),
    }
    row.update(_quality(out, ref))
    return row


def main() -> int:
    state = BASE / "state.jsonl"
    if not state.is_file():
        print("no state.jsonl", file=sys.stderr)
        return 2
    recs = []
    for line in state.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("rc") == 0 and rec.get("outputs"):
            recs.append(rec)
    print(f"runs: {len(recs)}")
    jobs = []
    for rec in recs:
        for o in rec["outputs"]:
            p = Path(o)
            if p.suffix.lower() in (".mp4", ".mov"):
                jobs.append((rec, p))
    rows = []
    src_cache: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_eval_output, r, o, src_cache) for r, o in jobs]
        for i, f in enumerate(futs, 1):
            try:
                rows.append(f.result())
            except Exception as exc:                   # noqa: BLE001
                print(f"[{i}/{len(futs)}] FAIL {exc}", file=sys.stderr)
            if i % 24 == 0 or i == len(futs):
                print(f"[{i}/{len(futs)}] metrics done", flush=True)
    rows.sort(key=lambda r: (r["encoder"], r["preset"], r["clip"]))
    for name in ("metrics.json",):
        pass
    (BASE / "metrics.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = ["encoder", "preset", "clip", "output_mb", "duration", "frames",
              "video_mbps", "win_mean_mbps", "win_max_mbps", "win_p95_mbps",
              "win_cv", "source_class", "ob_kbps", "cap_maxrate_kbps",
              "cap_bufsize_kbps", "pipeline_sec", "pipeline_fps",
              "encoder_fps", "src_mbps", "src_width", "src_height",
              "src_codec", "src_pix_fmt", "vmaf", "psnr", "ssim"]
    with (BASE / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote metrics.json / metrics.csv ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
