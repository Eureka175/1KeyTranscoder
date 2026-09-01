"""full 检查附加的视频质量抽样 (PSNR/SSIM 防花屏).

设计 (2026-08-31 实测验证):
- 抽样: 源文件名的 sha256 确定性 1-in-N (默认 10 取 1, 断点续跑/重跑结果
  稳定且分布均匀); 仅短视频参与 (时长 <= max_duration_sec, 默认 60s)。
- 度量: 单次 ffmpeg 同时跑 psnr + ssim, `settb=AVTB,setpts=N` 帧索引对齐
  (psnr/ssim 在 ffmpeg 9 为 framesync 过滤器, 容器 timebase 失配
  (1/1000 vs 1/30000) 会错配帧 — setpts=N 双端对齐彻底规避);
  `format=yuv420p10le` 统一色度/位深 (422 源按 420 输出策略同口径);
  psnr 同时写 stats_file 逐帧 CSV 做局部垃圾帧检测。
- 阈值 (防花屏/出错, 不是质量门槛): psnr_avg >= psnr_min_db (25),
  ssim_all >= ssim_min (0.80), 垃圾帧 (psnr < garbage_psnr_db=12) 占比
  <= max_garbage_frac (0.02) — 捕获局部损坏 (平均指标会被稀释)。
- 基础设施错误 (探测失败/ffmpeg 错误/超时) => SKIP 并记录, 不误杀
  正常文件; 阈值不达标 => FAIL (调用方置结构性失败)。
- 输出: scratch 下 quality_<stem>.json (+ 可选批量 CSV 追加)。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "sample_rate": 10,
    "max_duration_sec": 60.0,
    "psnr_min_db": 25.0,
    "ssim_min": 0.80,
    "garbage_psnr_db": 12.0,
    "max_garbage_frac": 0.02,
}

_PASS = "PASS"
_FAIL = "FAIL"
_SKIP = "SKIP"


def effective_opts(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """合并配置 (可选 quality_check 节) 与默认值; 越界值回退默认。"""
    opts = dict(DEFAULTS)
    for key, value in (cfg or {}).items():
        if key.startswith("_"):
            continue
        if key not in DEFAULTS:
            continue
        opts[key] = value
    try:
        opts["sample_rate"] = int(opts["sample_rate"])
        if opts["sample_rate"] < 1:
            opts["sample_rate"] = DEFAULTS["sample_rate"]
        opts["psnr_min_db"] = float(opts["psnr_min_db"])
        opts["ssim_min"] = float(opts["ssim_min"])
        opts["garbage_psnr_db"] = float(opts["garbage_psnr_db"])
        opts["max_garbage_frac"] = float(opts["max_garbage_frac"])
        opts["max_duration_sec"] = float(opts["max_duration_sec"])
        opts["enabled"] = bool(opts["enabled"])
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    return opts


def sample_selected(source_name: str, opts: dict[str, Any]) -> bool:
    """确定性伪随机 1-in-N 抽样 (sha256 首字节 % N == 0)。"""
    digest = hashlib.sha256(source_name.encode("utf-8")).digest()
    return digest[0] % opts["sample_rate"] == 0


def _probe_video(ffprobe: Path, path: Path) -> dict[str, Any] | None:
    cmd = [
        str(ffprobe), "-v", "error", "-count_packets",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,pix_fmt,nb_read_packets,r_frame_rate:"
        "format=duration",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    if not streams:
        return None
    st = streams[0]
    try:
        duration = float(fmt.get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        frames = int(st.get("nb_read_packets", 0) or 0)
    except (TypeError, ValueError):
        frames = 0
    return {
        "width": int(st.get("width", 0) or 0),
        "height": int(st.get("height", 0) or 0),
        "pix_fmt": st.get("pix_fmt", ""),
        "frames": frames,
        "duration_sec": duration,
    }


def _parse_psnr_stats(path: Path) -> list[float]:
    """psnr stats_file 逐帧 psnr_avg 值列表 (n:1 mse_avg:.. psnr_avg:..)。"""
    values: list[float] = []
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"psnr_avg:([\d.]+)", line)
        if m:
            values.append(float(m.group(1)))
    return values


def _evaluate(
    psnr_avg: float | None,
    ssim_all: float | None,
    garbage_frac: float,
    opts: dict[str, Any],
) -> tuple[bool, str]:
    """阈值判定 (纯函数, 可单测)。"""
    problems: list[str] = []
    if psnr_avg is None:
        problems.append("psnr 缺失")
    elif psnr_avg < float(opts["psnr_min_db"]):
        problems.append(
            f"psnr {psnr_avg:.2f} < {opts['psnr_min_db']} dB"
        )
    if ssim_all is None:
        problems.append("ssim 缺失")
    elif ssim_all < float(opts["ssim_min"]):
        problems.append(f"ssim {ssim_all:.4f} < {opts['ssim_min']}")
    if garbage_frac > float(opts["max_garbage_frac"]):
        problems.append(
            f"垃圾帧占比 {garbage_frac:.3f} > {opts['max_garbage_frac']}"
        )
    if problems:
        return False, "; ".join(problems)
    return True, "阈值达标"


def run_quality_sample(
    *,
    original: Path,
    final: Path,
    ffmpeg: Path,
    ffprobe: Path,
    scratch: Path,
    opts: dict[str, Any] | None = None,
    csv_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """抽样质量检查: 门控 (启用/短视频/1-in-N) -> 度量 -> 判定 -> 落盘。

    Returns dict: status=PASS|FAIL|SKIP, selected, reason, metrics,
    thresholds, detail, log_dir. FAIL 由调用方升级为结构性失败。"""
    eff = effective_opts(opts)
    # ffmpeg 以 scratch 为 cwd (stats_file 相对路径规避盘符冒号),
    # 输入路径必须绝对化
    original = original.resolve()
    final = final.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    stem = original.stem

    def result(status: str, detail: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "selected": status != _SKIP,
            "reason": detail if status == _SKIP else "",
            "original": str(original),
            "final": str(final),
            "duration_sec": extra.get("duration_sec"),
            "frames": extra.get("frames"),
            "psnr_avg_db": extra.get("psnr_avg_db"),
            "psnr_min_db_measured": extra.get("psnr_min_db_measured"),
            "ssim_all": extra.get("ssim_all"),
            "garbage_frames": extra.get("garbage_frames"),
            "garbage_frac": extra.get("garbage_frac"),
            "thresholds": {
                "psnr_min_db": eff["psnr_min_db"],
                "ssim_min": eff["ssim_min"],
                "garbage_psnr_db": eff["garbage_psnr_db"],
                "max_garbage_frac": eff["max_garbage_frac"],
            },
            "detail": detail,
            "log_dir": str(scratch),
        }

    def write_report(rep: dict[str, Any]) -> None:
        (scratch / f"quality_{stem}.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if csv_path is not None:
            try:
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                row = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": original.name,
                    "final": final.name,
                    "status": rep["status"],
                    "duration_sec": rep["duration_sec"],
                    "frames": rep["frames"],
                    "psnr_avg_db": rep["psnr_avg_db"],
                    "psnr_min_db_measured": rep["psnr_min_db_measured"],
                    "ssim_all": rep["ssim_all"],
                    "garbage_frames": rep["garbage_frames"],
                    "garbage_frac": rep["garbage_frac"],
                    "detail": rep["detail"],
                }
                exists = csv_path.is_file()
                with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    if not exists:
                        writer.writeheader()
                    writer.writerow(row)
            except OSError as exc:
                log(f"quality csv append failed: {exc}")

    if not eff["enabled"]:
        rep = result(_SKIP, "quality_check disabled")
        write_report(rep)
        return rep

    src_probe = _probe_video(ffprobe, original)
    if src_probe is None:
        rep = result(_SKIP, "source probe failed")
        write_report(rep)
        return rep
    duration = src_probe["duration_sec"]
    if duration > eff["max_duration_sec"]:
        rep = result(
            _SKIP,
            f"duration {duration:.1f}s > {eff['max_duration_sec']}s "
            "(long clip)",
            duration_sec=duration,
            frames=src_probe["frames"],
        )
        write_report(rep)
        return rep
    if not sample_selected(original.name, eff):
        rep = result(
            _SKIP,
            f"not sampled (1 in {eff['sample_rate']})",
            duration_sec=duration,
            frames=src_probe["frames"],
        )
        write_report(rep)
        return rep

    out_probe = _probe_video(ffprobe, final)
    if out_probe is None:
        rep = result(_SKIP, "final probe failed",
                     duration_sec=duration, frames=src_probe["frames"])
        write_report(rep)
        return rep
    if (src_probe["width"], src_probe["height"]) != (
        out_probe["width"], out_probe["height"]
    ):
        rep = result(
            _SKIP,
            f"resolution mismatch {src_probe['width']}x{src_probe['height']}"
            f" vs {out_probe['width']}x{out_probe['height']}",
            duration_sec=duration, frames=src_probe["frames"],
        )
        write_report(rep)
        return rep
    if (
        src_probe["frames"]
        and out_probe["frames"]
        and abs(src_probe["frames"] - out_probe["frames"]) > 2
    ):
        rep = result(
            _SKIP,
            f"frame count mismatch {src_probe['frames']} vs "
            f"{out_probe['frames']}",
            duration_sec=duration, frames=src_probe["frames"],
        )
        write_report(rep)
        return rep

    log(
        f"quality sample: {original.name} ({duration:.1f}s, "
        f"{src_probe['frames']} frames) psnr+ssim..."
    )
    graph = (
        "[0:v]settb=AVTB,setpts=N,format=yuv420p10le,split=2[x1][x2];"
        "[1:v]settb=AVTB,setpts=N,format=yuv420p10le,split=2[y1][y2];"
        "[x1][y1]psnr=stats_file=quality_psnr.csv[p];"
        "[x2][y2]ssim[s]"
    )
    try:
        proc = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-nostdin", "-y",
                "-i", str(original), "-i", str(final),
                "-filter_complex", graph,
                "-map", "[p]", "-map", "[s]", "-f", "null", "-",
            ],
            cwd=str(scratch),  # stats_file 相对路径 (盘符冒号会破坏 filtergraph)
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=3600,
        )
    except subprocess.TimeoutExpired:
        rep = result(_SKIP, "ffmpeg metrics timeout",
                     duration_sec=duration, frames=src_probe["frames"])
        write_report(rep)
        return rep
    except OSError as exc:
        rep = result(_SKIP, f"ffmpeg exec failed: {exc}",
                     duration_sec=duration, frames=src_probe["frames"])
        write_report(rep)
        return rep

    err = proc.stderr or ""
    m = re.search(
        r"PSNR y:[\d.]+\s+u:[\d.]+\s+v:[\d.]+\s+average:([\d.]+)", err
    )
    psnr_avg = float(m.group(1)) if m else None
    m = re.search(
        r"PSNR y:[\d.]+\s+u:[\d.]+\s+v:[\d.]+\s+average:[\d.]+\s+"
        r"min:([\d.]+)", err
    )
    psnr_min = float(m.group(1)) if m else None
    m = re.search(
        r"SSIM Y:[\d.]+\s+\([^)]*\)\s+U:[\d.]+\s+\([^)]*\)\s+"
        r"V:[\d.]+\s+\([^)]*\)\s+All:([\d.]+)", err
    )
    ssim_all = float(m.group(1)) if m else None

    if proc.returncode != 0 or (psnr_avg is None and ssim_all is None):
        rep = result(
            _SKIP, f"ffmpeg metrics failed rc={proc.returncode}: "
            f"{err[-200:]}",
            duration_sec=duration, frames=src_probe["frames"],
        )
        write_report(rep)
        return rep

    per_frame = _parse_psnr_stats(scratch / "quality_psnr.csv")
    floor = float(eff["garbage_psnr_db"])
    garbage = sum(1 for v in per_frame if v < floor)
    garbage_frac = (garbage / len(per_frame)) if per_frame else 0.0
    ok, verdict = _evaluate(psnr_avg, ssim_all, garbage_frac, eff)
    detail = (
        f"{verdict} | psnr_avg={psnr_avg:.2f} "
        f"(min {psnr_min if psnr_min is not None else float('nan'):.2f}) dB | "
        f"ssim={ssim_all:.4f} | 垃圾帧 {garbage}/{len(per_frame)}"
        if psnr_avg is not None and ssim_all is not None
        else verdict
    )
    rep = result(
        _PASS if ok else _FAIL, detail,
        duration_sec=duration,
        frames=len(per_frame) or src_probe["frames"],
        psnr_avg_db=round(psnr_avg, 3) if psnr_avg is not None else None,
        psnr_min_db_measured=round(psnr_min, 3) if psnr_min is not None
        else None,
        ssim_all=round(ssim_all, 5) if ssim_all is not None else None,
        garbage_frames=garbage,
        garbage_frac=round(garbage_frac, 5),
    )
    write_report(rep)
    log(f"quality sample: {rep['status']} ({detail})")
    return rep
