"""自动延时补偿集成层 (无线麦克风通道延迟同步).

包装 vendored 算法包 core/mp4_channel_sync (GCC-PHAT 测量 + 窗 sinc
修正 + 复检): 解码各音频流为 float32 mono -> measure_channels(参考
CH3) -> 质量门 (confidence/constant/verify 残差) -> fix_channels ->
尾部补零保全长 -> 按原采样格式回编码为 per-stream 音频中间文件 +
结构化报告。宿主路径 (经典/Sony/DJI) 负责把 fixed audio 中间文件
接入重封装。

numpy/scipy 为可选依赖: 仅 --channel-sync 且源具备多流单声道 PCM
布局时才导入; 缺失时给出明确 WARNING 并跳过 (不影响正常转码)。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

DEFAULTS: dict[str, Any] = {
    "reference_stream": 2,      # CH3 (0-based) — 有线通道作参考
    "min_confidence": 0.3,
    "max_lag_seconds": 1.0,
    "verify_max_ms": 0.05,      # 复检残差阈值 (算法包验收标准)
    "aligned_max_ms": 0.05,     # 全部通道 |delay| 低于此值 -> 无需修正
    "min_audio_streams": 3,     # 至少 3 条单声道 PCM 流才启用 (需有线参考)
}


def effective_opts(cfg: dict[str, Any] | None) -> dict[str, Any]:
    opts = dict(DEFAULTS)
    for key, value in (cfg or {}).items():
        if key.startswith("_"):
            continue
        if key in DEFAULTS:
            opts[key] = value
    try:
        opts["reference_stream"] = int(opts["reference_stream"])
        opts["min_confidence"] = float(opts["min_confidence"])
        opts["max_lag_seconds"] = float(opts["max_lag_seconds"])
        opts["verify_max_ms"] = float(opts["verify_max_ms"])
        opts["aligned_max_ms"] = float(opts["aligned_max_ms"])
        opts["min_audio_streams"] = int(opts["min_audio_streams"])
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    return opts


def eligible_audio(streams: list[dict[str, Any]]) -> tuple[bool, str]:
    """判定源布局是否适合自动延时补偿。

    对齐规则 (用户决定): 仅针对多流单声道 PCM 布局 (无线麦 CH1/CH2 +
    有线参考 CH3/CH4, ≥3 条独立 mono PCM 流); **2ch (立体声) / 1ch
    (单声道) 布局默认不做对齐** — 立体声流内部相位关系不应被通道间
    重排破坏, 单流无从对齐。"""
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio) < DEFAULTS["min_audio_streams"]:
        return False, (
            f"仅 {len(audio)} 条音频流: 1ch/2ch 布局默认不做对齐 "
            f"(需 >= {DEFAULTS['min_audio_streams']} 条独立单声道 PCM 流)"
        )
    for i, st in enumerate(audio):
        codec = str(st.get("codec_name", ""))
        channels = st.get("channels")
        if not codec.startswith("pcm_"):
            return False, f"stream {i}: codec {codec!r} is not PCM"
        try:
            if int(channels) != 1:
                return False, (
                    f"stream {i}: {channels} 声道 — 2ch/多声道布局默认"
                    "不做对齐 (仅单声道 PCM 流参与)"
                )
        except (TypeError, ValueError):
            return False, f"stream {i}: unknown channel layout"
    rates = {str(st.get("sample_rate")) for st in audio}
    if len(rates) != 1:
        return False, f"mixed sample rates: {sorted(rates)}"
    return True, ""


def _decode_stream(
    ffmpeg: Path,
    source: Path,
    stream_index: int,
    sample_rate: int,
    out_f32: Path,
) -> bool:
    """单流解码为 float32 mono raw (写入文件, 避免大数组走管道)。"""
    proc = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(source),
            "-map", f"0:a:{stream_index}",
            "-vn", "-sn", "-dn",
            "-f", "f32le", "-ac", "1", "-ar", str(sample_rate),
            str(out_f32),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    return proc.returncode == 0 and out_f32.is_file() and out_f32.stat().st_size > 0


def run_channel_sync(
    *,
    source: Path,
    ffmpeg: Path,
    work_dir: Path,
    streams: list[dict[str, Any]],
    opts: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """测量 -> 质量门 -> 修正 -> 回编码。返回结构化结果。

    status:
      applied         已修正, fixed_files 可用 (接入重封装)
      already_aligned 全部通道已对齐, 无需修正 (原音频照旧)
      not_eligible    布局不满足 (非多流单声道 PCM)
      measure_failed  测量质量门未过 (原音频照旧 + 警告)
      verify_failed   修正复检未过 (原音频照旧 + 警告)
      tool_missing    numpy/scipy 缺失
    除 applied 外, 宿主一律保持原音频不动 — 绝不静音或乱移。
    """
    eff = effective_opts(opts)
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem

    def report(status: str, detail: str, **extra: Any) -> dict[str, Any]:
        rep = {
            "status": status,
            "detail": detail,
            "source": str(source),
            "reference_stream": eff["reference_stream"],
            "channels": extra.get("channels", []),
            "fixed_files": [str(p) for p in extra.get("fixed_files", [])],
            "log_dir": str(work_dir),
        }
        (work_dir / f"channel_sync_{stem}.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return rep

    ok, reason = eligible_audio(streams)
    if not ok:
        log(f"channel-sync: not eligible — {reason}")
        return report("not_eligible", reason)

    try:
        import numpy as np
        from .mp4_channel_sync import (
            fix_channels,
            measure_channels,
            verify_channels,
        )
    except ImportError as exc:
        log(
            f"channel-sync: numpy/scipy missing ({exc}) — skipped, "
            "audio untouched"
        )
        return report("tool_missing", f"numpy/scipy missing: {exc}")

    audio = [s for s in streams if s.get("codec_type") == "audio"]
    sample_rate = int(audio[0].get("sample_rate", 0) or 0)
    if sample_rate <= 0:
        return report("measure_failed", "unknown sample rate")

    # 1. 解码全部音频流为 float32 mono
    f32_files: list[Path] = []
    for i, st in enumerate(audio):
        f32 = work_dir / f"decoded_{i}.f32"
        if not _decode_stream(ffmpeg, source, i, sample_rate, f32):
            return report("measure_failed", f"audio stream {i} decode failed")
        f32_files.append(f32)

    channels = [
        np.fromfile(str(f), dtype=np.float32) for f in f32_files
    ]
    common = min(c.size for c in channels)
    channels = [c[:common] for c in channels]
    if common < int(2.0 * sample_rate):
        return report(
            "measure_failed",
            f"audio too short for reliable GCC ({common / sample_rate:.1f}s)",
        )

    # 2. 测量
    try:
        results = measure_channels(
            channels,
            sample_rate=sample_rate,
            reference_index=eff["reference_stream"],
            max_lag_seconds=eff["max_lag_seconds"],
            min_confidence=eff["min_confidence"],
        )
    except Exception as exc:
        return report("measure_failed", f"measure error: {exc}")

    chan_rows = []
    ref = eff["reference_stream"]
    for r in results:
        chan_rows.append({
            "stream": r.channel,
            "delay_ms": None if r.delay_ms != r.delay_ms else round(r.delay_ms, 4),
            "delay_samples": (
                None if r.delay_samples != r.delay_samples
                else round(r.delay_samples, 3)
            ),
            "confidence": round(r.confidence, 3),
            "polarity": r.polarity,
            "drift_ppm": round(r.drift_ppm, 2),
            "constant": r.constant,
            "warnings": r.warnings,
        })
    log(
        "channel-sync: measured "
        + ", ".join(
            f"CH{r.channel + 1}={r.delay_ms:+.3f}ms(conf {r.confidence:.2f})"
            if r.delay_ms == r.delay_ms
            else f"CH{r.channel + 1}=nan"
            for r in results
        )
    )

    failed = [
        r for r in results
        if r.channel != ref and (
            r.delay_samples != r.delay_samples
            or r.confidence < eff["min_confidence"]
            or not r.constant
        )
    ]
    if failed:
        return report(
            "measure_failed",
            "quality gate failed: "
            + ", ".join(f"CH{r.channel + 1}" for r in failed),
            channels=chan_rows,
        )

    # 3. 已对齐判定
    non_ref = [r for r in results if r.channel != ref]
    max_delay_ms = max(
        (abs(r.delay_ms) for r in non_ref if r.delay_ms == r.delay_ms),
        default=0.0,
    )
    if max_delay_ms < eff["aligned_max_ms"]:
        log(
            f"channel-sync: already aligned (max |delay| "
            f"{max_delay_ms:.4f} ms < {eff['aligned_max_ms']} ms)"
        )
        return report("already_aligned", "already aligned", channels=chan_rows)

    # 4. 修正 + 复检
    delays = [r.delay_samples for r in results]
    try:
        fixed = fix_channels(channels, delays, sample_rate=sample_rate)
        residuals = verify_channels(
            fixed, sample_rate=sample_rate,
            reference_index=eff["reference_stream"],
        )
    except Exception as exc:
        return report("verify_failed", f"fix/verify error: {exc}",
                      channels=chan_rows)

    rows = [
        None if v != v else round(v, 4) for v in residuals
    ]
    bad = [
        (i, v) for i, v in enumerate(rows)
        if i != ref and v is not None and abs(v) > eff["verify_max_ms"]
    ]
    if bad:
        return report(
            "verify_failed",
            "residuals: " + ", ".join(
                f"CH{i + 1}={v:+.4f}ms" for i, v in bad
            ),
            channels=chan_rows,
        )
    log(
        "channel-sync: fixed, residuals "
        + ", ".join(
            f"CH{i + 1}={v if v is None else f'{v:+.4f}'}ms"
            for i, v in enumerate(rows)
        )
    )

    # 5. 尾部补零保全长 (原长 = common), 各通道独立回编码
    fixed_files: list[Path] = []
    for i, st in enumerate(audio):
        ch = np.asarray(fixed[i], dtype=np.float32)
        padded = np.zeros(common, dtype=np.float32)
        n = min(ch.size, common)
        padded[:n] = ch[:n]
        raw = work_dir / f"fixed_{i}.f32"
        padded.tofile(str(raw))
        codec = str(st.get("codec_name", "pcm_s24le"))
        out = work_dir / f"fixed_{i}.mov"
        proc = subprocess.run(
            [
                str(ffmpeg), "-v", "error", "-nostdin", "-y",
                "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
                "-i", str(raw),
                "-c:a", codec, "-f", "mov", str(out),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=1800,
        )
        if proc.returncode != 0 or not out.is_file():
            return report(
                "verify_failed",
                f"re-encode stream {i} failed: {proc.stderr[-200:]}",
                channels=chan_rows,
            )
        fixed_files.append(out)
        try:
            raw.unlink()
        except OSError:
            pass

    for f in f32_files:
        try:
            f.unlink()
        except OSError:
            pass

    log(f"channel-sync: applied ({len(fixed_files)} fixed audio files)")
    return report(
        "applied",
        "measure -> fix -> verify OK",
        channels=chan_rows,
        fixed_files=fixed_files,
        residuals_ms=rows,
    )
