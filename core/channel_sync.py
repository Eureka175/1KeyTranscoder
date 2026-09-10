"""自动延时补偿集成层 (P1 正式版, algo 2.3.0-p1).

流程 (transcode 模式; transparent 模式跳过视频编码, 见下):
  1. effective_opts 归一 (DEFAULTS + 用户覆盖, 旧键 max_lag_seconds 兼容);
  2. eligible_audio(): 文件级边界 — 音频流 >= 3 / 每流单声道 / 线性 PCM
     (s16/s24/s32/f32 × le/be) / 采样率一致且 ∈ {48000, 96000};
     **2ch/1ch 布局默认不做对齐** (用户决定); 44.1kHz 显式拒绝;
  3. ffmpeg 逐流解码 raw (存储精度按 §6.2 表: s32 源走 f64, 其余 f32),
     逐轨健康检查 (NaN/Inf / 静音 RMS / 最短时长) — 轨道级状态;
  4. 锚点候选回退 [CH3, CH4, CH1, CH2] (0-based [2,3,0,1]): 首个与至少
     一条其它健康轨得到有效估计者当选; 全部失败 -> no_valid_anchor;
  5. anchor <-> 其余健康轨逐一 estimate_pair (两阶段 GCC-PHAT + 相位斜率
     精估), 每轨独立质量门: usable_frames / confidence / constant
     (MAD + 漂移 ppm) / out_of_range (搜索窗边缘堆积);
  6. 每轨独立决策: silent_track / non_finite / insufficient_frames /
     low_confidence / non_constant / out_of_range -> untouched (不阻止
     其它健康轨同步); |delay| < aligned_max_ms -> already_aligned;
     其余 -> fixed (纯整数样本移位, rint(delay_samples));
  7. 对 fixed 轨执行 recheck_residual() (局部 GCC + 相位斜率, 门
     verify_max_ms): 失败仅回退**该轨**为 untouched, 不回滚其它轨;
  8. 输出: 每轨一个音频中间文件 (fixed 轨 = 移位后按源 codec 回编码,
     其余轨 = 源轨 stream copy, bit-exact) — audio_files 全轨清单,
     fixed_files 为其中实际被移位的子集; 尾部补零保全长, 不做截断;
  9. 文件级失败 (无有效锚点 / 全部健康 target 均不可靠 / 解码或回编码
     失败) 时整文件音频原样; 轨道级失败保留其它成功轨的结果。

transparent 模式 (剪辑前预处理, --channel-sync-transparent):
  视频与所有非音频流 stream copy, 仅 fixed 音轨重新生成, untouched
  音轨保持原始内容; 全部已对齐时输出为源文件字节级拷贝
  (SHA256(output) == SHA256(input)); 文件级失败时输出 = 源文件拷贝
  (音频原样)。重封装走既有 GPAC 后端 (同 preservation/audio_sync.py
  的机制, 但按轨引用源文件以保留 data/meta 轨)。

vendored core/mp4_channel_sync.py (ChronoSync 1.x, MIT) 自 algo
2.1.0-p1 起不再被引用, 保留作回滚与对照; P1 与 vendored 1.x 的差异
对照见 docs/design/channel_sync_p1.md。

numpy/scipy 为可选依赖: 仅 --channel-sync 且源具备多流单声道 PCM
布局时才导入; 缺失时给出明确 WARNING 并跳过 (不影响正常转码)。

方向约定: delay > 0 = 目标轨比锚点**晚到**, 修正 = 整体前移 delay。
语义约定: delay 是**当前文件内的观测轨间时差** (可包含电子/无线链路
延迟、录音链路差、麦克风物理位置的声学传播差等), 不得解释为设备固有
latency; 不同物理位置不禁止同步, 但相关性不足时安全放弃该轨。
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "DEFAULTS",
    "effective_opts",
    "eligible_audio",
    "run_channel_sync",
    "repair_remux_timescale",
]

DEFAULTS: dict[str, Any] = {
    # —— 模式 ——
    "channel_sync_transparent": False,   # 视频/非音频 stream copy + 轨道级音频同步

    # —— 输入边界 ——
    "min_audio_streams": 3,              # 至少 3 条独立单声道 PCM 流才启用
    "supported_sample_rates": [48000, 96000],  # 仅 48k/96k (44.1k 显式拒绝)
    "supported_codecs": [
        # 用户决定: 大小端无所谓, 都支持; 需要排除的是 44.1kHz 采样率
        "pcm_s16le", "pcm_s16be",
        "pcm_s24le", "pcm_s24be",
        "pcm_s32le", "pcm_s32be",
        "pcm_f32le", "pcm_f32be",
    ],
    "min_audio_seconds": 2.0,            # 初值, 待真实素材标定 (过短轨=轨道级不可测)
    "silent_rms_dbfs": -60.0,            # 初值, 待真实素材标定 (整轨静音判定)

    # —— 估计 ——
    "search_window_ms": 80.0,            # 初值, 待真实素材标定 (电子带上限30 + 10m声学~29 + 余量)
    "wide_search_ms": 250.0,             # 初值, 待真实素材标定 (仅诊断超窗用的宽窗复测)
    "frame_ms": 200.0,                   # 初值, 待真实素材标定
    "hop_ms": 100.0,                     # 初值, 待真实素材标定
    "coarse_rate": 8000,                 # 初值, 待真实素材标定 (48k->q6, 96k->q12, 整数抽取)
    "anchor_segment_seconds": 30.0,      # 初值, 待真实素材标定 (相位斜率锚段, 不足取全长)
    "min_confidence": 0.3,               # 初值, 待真实素材标定
    "min_usable_frames": 5,              # 初值, 待真实素材标定
    "min_usable_fraction": 0.6,          # 初值, 待真实素材标定 (轨迹证据覆盖率: 大洞时恒定性不可靠 -> 宽窗复测)
    "fine_phase_band_hz": [200.0, 8000.0],  # 初值, 待真实素材标定 (96k 上沿仍 8k)
    # 帧级 RMS 门 (dBFS): 任一窗低于此值该帧不可测 — 抽取滤波振铃会把
    # 真静音抬到 ~1e-3 (-60dB), 缺省 -50dB 高于振铃、低于真实内容
    "frame_min_rms_dbfs": -50.0,         # 初值, 待真实素材标定

    # —— 恒定性 (P1 二分 constant/non_constant; step/ramp 细分属二期) ——
    "mad_max_ms": 1.0,                   # 初值, 待真实素材标定
    "step_min_ms": 3.0,                  # 初值, 待真实素材标定 (仅诊断警告, 不参与判定)
    "constant_max_ppm": 5.0,             # 初值, 待真实素材标定
    # 漂移材料性门 (真实素材标定): ppm 超门**且**全片预测漂移 > 此值才判非恒定 —
    # A7M5 实测: 已对齐轨的量化噪声斜率可达 5~30ppm 但漂移量仅 ~0.03ms;
    # 真实慢漂移轨 39~49ppm 对应 0.42~1.9ms, 远超此门仍被正确拦下
    "drift_min_ms": 0.1,                 # 初值, 待真实素材标定

    # —— 修正 / 复检 ——
    "verify_max_ms": 0.05,               # 复检残差阈值 (整数修正量化残差理论上 <= 0.5 sample)
    "aligned_max_ms": 0.05,              # 全部通道 |delay| 低于此值 -> already_aligned
    "fix_chunk_seconds": 60.0,           # 初值, 待真实素材标定 (移位分块大小)

    # —— 锚点 (P1: 候选顺序 + 健康门; 完整打分属二期) ——
    "anchor_candidates": [2, 3, 0, 1],   # CH3 > CH4 > CH1 > CH2 (0-based); CH3 非"真值基准"

    # —— 兼容 ——
    "max_lag_seconds": None,             # 旧键: 读取时映射为 search_window_ms
    "algo_version": "2.3.0-p1",
}

_INT_KEYS = ("min_audio_streams", "coarse_rate", "min_usable_frames")
_FLOAT_KEYS = ("min_audio_seconds", "silent_rms_dbfs", "search_window_ms",
               "wide_search_ms", "frame_ms", "hop_ms",
               "anchor_segment_seconds",
               "min_confidence", "min_usable_fraction", "mad_max_ms",
               "step_min_ms", "constant_max_ppm", "drift_min_ms",
               "verify_max_ms",
               "aligned_max_ms", "fix_chunk_seconds", "frame_min_rms_dbfs")
_LIST_INT_KEYS = ("supported_sample_rates", "anchor_candidates")
_LIST_FLOAT_KEYS = ("fine_phase_band_hz",)
_LIST_STR_KEYS = ("supported_codecs",)
_BOOL_KEYS = ("channel_sync_transparent",)

# 参与"全部健康 target 均失败 -> 文件级 measure_failed" 判定的轨道级原因
_ESTIMATION_FAIL_REASONS = frozenset({
    "insufficient_frames", "low_confidence", "non_constant",
    "out_of_range", "recheck_residual",
})


def effective_opts(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """DEFAULTS + 用户覆盖归一; 非法值整体回退默认。

    兼容: 旧键 max_lag_seconds (秒) 非 None 时映射为
    search_window_ms = max_lag_seconds * 1000; 废弃键 reference_stream
    (锚点改由 anchor_candidates 候选顺序决定) 读取时忽略。
    """
    opts = dict(DEFAULTS)
    for key, value in (cfg or {}).items():
        if key.startswith("_"):
            continue
        if key in DEFAULTS:
            opts[key] = value
    try:
        for key in _INT_KEYS:
            opts[key] = int(opts[key])
        for key in _FLOAT_KEYS:
            opts[key] = float(opts[key])
        for key in _LIST_INT_KEYS:
            opts[key] = [int(v) for v in opts[key]]
        for key in _LIST_FLOAT_KEYS:
            opts[key] = [float(v) for v in opts[key]]
        for key in _LIST_STR_KEYS:
            opts[key] = [str(v) for v in opts[key]]
        for key in _BOOL_KEYS:
            opts[key] = bool(opts[key])
        if opts["max_lag_seconds"] is not None:
            opts["search_window_ms"] = float(opts["max_lag_seconds"]) * 1000.0
        opts["algo_version"] = str(opts["algo_version"])
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    return opts


def eligible_audio(streams: list[dict[str, Any]]) -> tuple[bool, str]:
    """文件级资格判定 (P1, 保持名称与返回结构兼容)。

    对齐规则 (用户决定): 仅针对多流单声道 PCM 布局 (无线麦 CH1/CH2 +
    有线参考 CH3/CH4, >=3 条独立 mono PCM 流); **2ch (立体声) / 1ch
    (单声道) 布局默认不做对齐** — 立体声流内部相位关系不应被通道间
    重排破坏, 单流无从对齐。

    P1 起采样率限定 48k/96k (**44.1k 等一律显式拒绝**并 WARNING — 有意的
    范围收窄, 不再 silently 跑旧算法), codec 支持线性 PCM 八类
    (s16/s24/s32/f32 × 小端/大端 — 用户决定: 大小端无所谓, 都支持),
    压缩/非线性格式 (aac/alaw 等) 一律拒, 各流采样率须一致。
    """
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio) < DEFAULTS["min_audio_streams"]:
        return False, (
            f"仅 {len(audio)} 条音频流: 1ch/2ch 布局默认不做对齐 "
            f"(需 >= {DEFAULTS['min_audio_streams']} 条独立单声道 PCM 流)"
        )
    for i, st in enumerate(audio):
        codec = str(st.get("codec_name", ""))
        channels = st.get("channels")
        if codec not in DEFAULTS["supported_codecs"]:
            return False, (
                f"stream {i}: codec {codec!r} is not supported PCM"
            )
        try:
            if int(channels) != 1:
                return False, (
                    f"stream {i}: {channels} 声道 — 2ch/多声道布局默认"
                    "不做对齐 (仅单声道 PCM 流参与)"
                )
        except (TypeError, ValueError):
            return False, f"stream {i}: unknown channel layout"
        raw_rate = st.get("sample_rate")
        try:
            rate = int(raw_rate)
        except (TypeError, ValueError):
            return False, f"unsupported_sample_rate: {raw_rate}"
        if rate not in DEFAULTS["supported_sample_rates"]:
            return False, f"unsupported_sample_rate: {rate}"
    rates = {int(st.get("sample_rate")) for st in audio}
    if len(rates) != 1:
        return False, f"mixed sample rates: {sorted(rates)}"
    return True, ""


def _decode_stream(
    ffmpeg: Path,
    source: Path,
    stream_index: int,
    sample_rate: int,
    out_raw: Path,
    storage_dtype: str,
) -> tuple[bool, str]:
    """单流解码为 raw mono (写文件避免大数组走管道; f32/f64 按存储精度)。"""
    fmt = "f32le" if storage_dtype == "f32" else "f64le"
    proc = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(source),
            "-map", f"0:a:{stream_index}",
            "-vn", "-sn", "-dn",
            "-f", fmt, "-ac", "1", "-ar", str(sample_rate),
            str(out_raw),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    ok = (
        proc.returncode == 0
        and out_raw.is_file()
        and out_raw.stat().st_size > 0
    )
    return ok, (proc.stderr or "")[-300:]


# ffmpeg 的 MOV muxer 写 QuickTime PCM 条目 (in24/sowt/twos/in32/fl32),
# MP4 muxer 写 ISO 条目 (ipcm/fpcm)。Sony XAVC-S 的 LPCM 实际是 **ipcm**
# — 若音频中间文件用 MOV 承载, 源音轨被替换后容器里的 sample entry 会
# 变成 in24, preservation 的 `audio.tracks` 关键项随即判 MODIFIED 并使
# 整个文件 `--check basic` 失败 (真实 A7M5 素材实测: applied 的文件
# rc=1 且不产出)。因此按**源轨自己的 sample entry** 选择能复现同一条目
# 的 muxer, 使替换对容器透明。
_MP4_SAMPLE_ENTRIES = ("ipcm", "fpcm")


def _muxer_for_entry(sample_entry: str) -> str:
    """返回能复现源轨 sample entry 的 ffmpeg muxer 名 (默认 mov)。"""
    entry = str(sample_entry or "").strip().lower()
    return "mp4" if entry in _MP4_SAMPLE_ENTRIES else "mov"


def _copy_stream(
    ffmpeg: Path,
    source: Path,
    stream_index: int,
    out_file: Path,
    muxer: str = "mov",
) -> tuple[bool, str]:
    """源音轨 stream copy 到独立容器 (untouched 轨 bit-exact 保留)。"""
    proc = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(source),
            "-map", f"0:a:{stream_index}",
            "-vn", "-sn", "-dn",
            "-c:a", "copy", "-f", muxer, str(out_file),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    ok = (
        proc.returncode == 0
        and out_file.is_file()
        and out_file.stat().st_size > 0
    )
    return ok, (proc.stderr or "")[-300:]


def _track_health(arr: Any, np: Any) -> tuple[bool, float]:
    """分块健康扫描: (全部有限?, rms_dbfs)。峰值内存恒定。"""
    ssq = 0.0
    n = arr.shape[0]
    chunk = 1 << 20
    for a in range(0, n, chunk):
        blk = np.asarray(arr[a: a + chunk], dtype=np.float64)
        if not np.all(np.isfinite(blk)):
            return False, float("nan")
        ssq += float(np.sum(blk * blk))
    if n == 0 or ssq <= 0.0:
        return True, float("-inf")
    return True, 10.0 * math.log10(ssq / n)


def _close_map(arr: Any) -> None:
    """关闭 memmap 底层句柄 (Windows 上删除文件前必须先关映射)。"""
    m = getattr(arr, "_mmap", None)
    if m is not None:
        m.close()


def repair_remux_timescale(
    path: Path, log: Callable[[str], None] = print
) -> None:
    """按输出文件**实际** mvhd timescale 重跑 stts 时长修复 (P1 修正辅助)。

    preservation/audio_sync.remux_replace_audio 用源文件 mvhd timescale
    (rigaya 中间件 = 1000) 调用 isobmf.patch_track_durations, 而 GPAC
    26.02 的 -new 输出 mvhd timescale 实际为 3000 (不采纳 -timescale 1000)
    — 结果 tkhd/elst 被写成 1/3 时长 (2.667s vs 8s), 音频解码被 elst 截断。
    本函数从输出文件自身读取真实 mvhd timescale 后重跑同一修复, 把时长
    恢复为精确值 (stts 始终精确, 是可靠的事实来源)。P1 不修改
    preservation/, 故修正逻辑放在集成层并接入各 classic/transparent
    重封装调用点。
    """
    from preservation import isobmf

    with path.open("rb") as f:
        roots = isobmf.root_boxes(path)
        moov = next((b for b in roots if b.type == "moov"), None)
        if moov is None:
            raise RuntimeError(f"no moov box in {path}")
        mvhd = next(
            (b for b in isobmf._children(f, moov) if b.type == "mvhd"),
            None,
        )
        if mvhd is None:
            raise RuntimeError(f"no mvhd box in {path}")
        f.seek(mvhd.data_offset)
        ver = f.read(1)[0]
        f.seek(mvhd.data_offset + (20 if ver == 1 else 12))
        movie_ts = struct.unpack(">I", f.read(4))[0]
    if movie_ts <= 0:
        raise RuntimeError(f"invalid mvhd timescale {movie_ts}")
    for desc in isobmf.patch_track_durations(path, movie_ts, from_stts=True):
        log(desc)
    mv_desc = isobmf.patch_movie_duration(path)
    if mv_desc:
        log(mv_desc)


def run_channel_sync(
    *,
    source: Path,
    ffmpeg: Path,
    work_dir: Path,
    streams: list[dict[str, Any]],
    opts: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
    transparent: bool | None = None,
    dst: Path | None = None,
    gpac: Any | None = None,
) -> dict[str, Any]:
    """测量 -> 锚点回退 -> 轨道级质量门 -> 整数修正 -> 复检 -> 输出。

    transparent: 显式覆盖 opts["channel_sync_transparent"]; 为 True 时
    dst 必填 (透明模式最终输出路径)。

    status:
      applied         至少一轨 fixed; audio_files 含全轨音频文件
                      (fixed 轨已移位; 其余轨为源轨 stream copy)
      already_aligned 无轨需要修正 (可含轨道级 untouched, 见 channels)
      not_eligible    布局不满足 (非多流单声道 PCM / 采样率超范围)
      measure_failed  文件级: 无有效锚点 / 全部健康 target 均不可靠 /
                      解码失败 (原音频照旧 + 警告)
      verify_failed   文件级: 回编码/重封装失败 (原音频照旧 + 警告)
      tool_missing    numpy/scipy 缺失
    除 applied 外宿主一律保持原音频不动; applied 时轨道级 untouched
    轨 (silent_track/non_finite/低置信/非恒定/超范围/复检失败) 同样
    不被修改 — 绝不静音或乱移任何轨。
    """
    eff = effective_opts(opts)
    t_mode = bool(transparent if transparent is not None
                  else eff["channel_sync_transparent"])
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    ctx: dict[str, Any] = {"anchor": None}

    def report(
        status: str,
        detail: str,
        *,
        scope: str | None = None,
        channels: list[dict] | None = None,
        audio_files: list[Path] | None = None,
        fixed_files: list[Path] | None = None,
        output_file: Path | None = None,
        file_copied: bool = False,
    ) -> dict[str, Any]:
        rep = {
            "status": status,
            "detail": detail,
            "source": str(source),
            "algo_version": eff["algo_version"],
            "anchor_stream": ctx["anchor"],
            "sync_mode": "transparent" if t_mode else "transcode",
            "result_scope": scope,
            # 旧键保留 (兼容下游读取): 语义 = 锚点流
            "reference_stream": ctx["anchor"],
            "channels": channels or [],
            "audio_files": [str(p) for p in (audio_files or [])],
            "fixed_files": [str(p) for p in (fixed_files or [])],
            "output_file": str(output_file) if output_file else None,
            "file_copied": bool(file_copied),
            "log_dir": str(work_dir),
        }
        (work_dir / f"channel_sync_{stem}.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return rep

    def finish(
        status: str, detail: str, **kw: Any
    ) -> dict[str, Any]:
        """终态写入: 透明模式文件级失败时输出 = 源文件字节级拷贝。"""
        if t_mode and dst is not None and status != "applied":
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dst)
                kw["output_file"] = dst
                kw["file_copied"] = True
                log(f"channel-sync: transparent — output = 源文件原样拷贝")
            except OSError as exc:
                log(f"channel-sync: transparent copy failed: {exc}")
                status = "verify_failed" if status not in (
                    "not_eligible", "tool_missing"
                ) else status
                detail = f"transparent copy failed: {exc}"
        return report(status, detail, **kw)

    ok, reason = eligible_audio(streams)
    if not ok:
        log(f"channel-sync: not eligible — {reason}")
        return finish("not_eligible", reason)

    try:
        import numpy as np
        from . import sync_estimate, sync_fix
    except ImportError as exc:
        log(
            f"channel-sync: numpy/scipy missing ({exc}) — skipped, "
            "audio untouched"
        )
        return finish("tool_missing", f"numpy/scipy missing: {exc}")

    audio = [s for s in streams if s.get("codec_type") == "audio"]
    sample_rate = int(audio[0].get("sample_rate", 0) or 0)
    codecs = [str(st.get("codec_name", "")) for st in audio]
    # 源轨 sample entry (ipcm / in24 / sowt / ...), 决定中间文件 muxer
    entries = [str(st.get("codec_tag_string", "") or "") for st in audio]
    storage = [
        "f64" if c in ("pcm_s32le", "pcm_s32be") else "f32"
        for c in codecs
    ]
    n_tracks = len(audio)
    min_samples = max(1, int(round(eff["min_audio_seconds"] * sample_rate)))

    def j(x: float, nd: int) -> float | None:
        return None if x != x else round(x, nd)   # NaN 判定沿用 x != x 惯例

    def plain_row(i: int, decision: str, reason_: str | None) -> dict[str, Any]:
        row = {
            "stream": i,
            "delay_ms": None,
            "delay_samples": None,
            "confidence": 0.0,
            "polarity": 1,
            "drift_ppm": 0.0,
            "constant": False,
            "warnings": [],
            "decision": decision,
            "reason": reason_,
            "fine_delay_ms": None,
            "shift_samples": None,
            "fractional_part_samples": None,
            "storage_dtype": storage[i],
            "traj_mad_ms": None,
            "traj_spread_samples": None,
            "traj_drift_ms": None,
            "usable_frames": None,
            "rms_dbfs": None,
        }
        if codecs[i] in ("pcm_s32le", "pcm_s32be"):
            row["precision_note"] = "s32 source processed via f64 pipeline"
        return row

    def est_row(i: int, e: Any, stats: Any, decision: str,
                reason_: str | None, shift: int | None = None) -> dict[str, Any]:
        warnings = []
        if e.polarity < 0:
            warnings.append(
                "polarity inversion detected (negative correlation peak)"
            )
        if stats.step_max_ms == stats.step_max_ms \
                and stats.step_max_ms > eff["step_min_ms"]:
            warnings.append(
                f"trajectory adjacent-step {stats.step_max_ms:.2f}ms > "
                f"{eff['step_min_ms']}ms (diagnostic only, 分类属二期)"
            )
        if e.fine_delay_samples != e.fine_delay_samples:
            warnings.append("phase slope estimate degenerated (diagnostic)")
        row = {
            "stream": i,
            "delay_ms": j(e.delay_ms, 4),
            "delay_samples": j(e.delay_samples, 3),
            "confidence": round(e.confidence, 3),
            "polarity": e.polarity,
            "drift_ppm": round(stats.drift_ppm, 2),
            # 与决策门一致: MAD + 极差 + 漂移(ppm×材料性) 三门
            "constant": bool(
                sync_estimate.classify_constant(
                    stats, mad_max_ms=eff["mad_max_ms"],
                    max_ppm=eff["constant_max_ppm"],
                    sample_rate=sample_rate,
                    drift_min_ms=eff["drift_min_ms"],
                )
            ),
            "warnings": warnings,
            "decision": decision,
            "reason": reason_,
            "fine_delay_ms": j(e.fine_delay_ms, 4),
            "shift_samples": shift,
            "fractional_part_samples": (
                j(e.delay_samples - int(shift), 3)
                if shift is not None and e.delay_samples == e.delay_samples
                else None
            ),
            "storage_dtype": storage[i],
            "traj_mad_ms": j(stats.mad_ms, 4),
            "traj_spread_samples": j(stats.spread_samples, 3),
            "traj_drift_ms": j(getattr(stats, "drift_total_ms", float("nan")), 4),
            "usable_frames": e.usable_frames,
        }
        if codecs[i] in ("pcm_s32le", "pcm_s32be"):
            row["precision_note"] = "s32 source processed via f64 pipeline"
        return row

    decoded = [work_dir / f"decoded_{i}.{storage[i]}" for i in range(n_tracks)]
    fixed_raw = [work_dir / f"fixed_{i}.{storage[i]}" for i in range(n_tracks)]
    intermediates = decoded + fixed_raw
    arrs: list[Any] = []

    try:
        # 1. 逐流解码 raw (§6.2 存储精度表)
        for i in range(n_tracks):
            ok, err = _decode_stream(
                ffmpeg, source, i, sample_rate, decoded[i], storage[i]
            )
            if not ok:
                log(f"channel-sync: WARNING stream {i} decode failed")
                return finish(
                    "measure_failed",
                    f"audio stream {i} decode failed: {err[-200:]}",
                )
        arrs = [
            sync_estimate.open_source(decoded[i], storage[i])
            for i in range(n_tracks)
        ]

        # 2. 逐轨健康检查 (轨道级状态; 不健康轨 -> untouched, 不阻止其它轨)
        health: list[tuple[bool, str | None, float]] = []
        rms_of: dict[int, float | None] = {}
        rows: list[dict] = [plain_row(i, "untouched", None)
                            for i in range(n_tracks)]
        for i, a in enumerate(arrs):
            if a.shape[0] < min_samples:
                health.append((False, "insufficient_frames", float("nan")))
                rows[i]["reason"] = "insufficient_frames"
                rms_of[i] = None
                continue
            finite, rms_db = _track_health(a, np)
            rms_of[i] = rms_db if rms_db == rms_db else None
            rows[i]["rms_dbfs"] = (
                round(rms_db, 1) if rms_db == rms_db else None
            )
            if not finite:
                health.append((False, "non_finite", rms_db))
                rows[i]["reason"] = "non_finite"
            elif rms_db < eff["silent_rms_dbfs"]:
                health.append((False, "silent_track", rms_db))
                rows[i]["reason"] = "silent_track"
            else:
                health.append((True, None, rms_db))
        for i, (_ok, reason_, _r) in enumerate(health):
            if not _ok:
                log(
                    f"channel-sync: CH{i + 1} {reason_} — 轨道级 untouched"
                    f"{' (rms ' + format(_r, '.1f') + ' dBFS)' if _r == _r else ''}"
                )
        healthy = [i for i in range(n_tracks) if health[i][0]]

        # 3. 锚点候选回退: [CH3, CH4, CH1, CH2], 仅健康轨可当选
        order = [c for c in eff["anchor_candidates"]
                 if 0 <= c < n_tracks and health[c][0]]
        anchor: int | None = None
        ests: dict[int, Any] = {}
        tried: list[int] = []
        est_kwargs = dict(
            sample_rate=sample_rate,
            search_window_ms=eff["search_window_ms"],
            frame_ms=eff["frame_ms"],
            hop_ms=eff["hop_ms"],
            anchor_segment_seconds=eff["anchor_segment_seconds"],
            min_confidence=eff["min_confidence"],
            coarse_rate=eff["coarse_rate"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
            min_usable_frames=eff["min_usable_frames"],
            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
        )
        for cand in order:
            cand_ests = {}
            for i in healthy:
                if i == cand:
                    continue
                cand_ests[i] = sync_estimate.estimate_pair(
                    arrs[cand], arrs[i],
                    storage_dtype=storage[i],
                    ref_index=cand, tgt_index=i,
                    **est_kwargs,
                )
            correlated = any(
                e.usable_frames >= eff["min_usable_frames"]
                and e.confidence >= eff["min_confidence"]
                for e in cand_ests.values()
            )
            if not correlated:
                tried.append(cand)
                continue               # anchor_uncorrelated -> 下一候选
            anchor = cand
            ests = cand_ests
            break
        if anchor is None:
            detail = (
                "no_valid_anchor: 候选 "
                + ",".join(f"CH{c + 1}" for c in tried)
                + " 与全部健康轨均不相关"
            )
            log(f"channel-sync: WARNING {detail}")
            for i in healthy:
                rows[i]["reason"] = "no_valid_anchor"
            return finish("measure_failed", detail, channels=rows)
        ctx["anchor"] = anchor

        # 4. 轨道级质量门 + 逐轨决策 (恒定 = MAD + 极差 + 漂移 ppm 三门;
        #    窄窗不可靠/边缘堆积时宽窗复测以区分 out_of_range)
        search_samples = eff["search_window_ms"] * sample_rate / 1000.0
        wide_window_ms = max(
            eff["wide_search_ms"], 3.0 * eff["search_window_ms"]
        )

        def wide_estimate(i: int) -> Any:
            return sync_estimate.estimate_pair(
                arrs[anchor], arrs[i],
                storage_dtype=storage[i],
                ref_index=anchor, tgt_index=i,
                **dict(est_kwargs, search_window_ms=wide_window_ms),
            )

        anchor_row = plain_row(anchor, "anchor", None)
        anchor_row.update({
            "delay_ms": 0.0, "delay_samples": 0.0, "confidence": 1.0,
            "constant": True, "fine_delay_ms": 0.0,
            "rms_dbfs": (
                round(rms_of[anchor], 1)
                if rms_of.get(anchor) is not None else None
            ),
        })
        rows[anchor] = anchor_row
        to_fix: list[int] = []
        used_est: dict[int, Any] = {}   # 每轨最终采用的估计 (窄窗或宽窗)
        for i in healthy:
            if i == anchor:
                continue
            e = ests[i]
            stats = sync_estimate.summarize_trajectory(e.frames, sample_rate)
            reason_: str | None = None
            coverage = (
                e.usable_frames / max(1, len(e.frames))
                if e.frames else 0.0
            )
            if e.usable_frames < eff["min_usable_frames"]:
                # 窄窗无可靠帧: 宽窗复测 — 真实时差超窗 vs 确实不可测
                ew = wide_estimate(i)
                if (
                    ew.usable_frames >= eff["min_usable_frames"]
                    and ew.confidence >= eff["min_confidence"]
                ):
                    if abs(ew.delay_samples) > search_samples:
                        reason_ = "out_of_range"
                        log(
                            f"channel-sync: CH{i + 1} 宽窗复测 delay "
                            f"{ew.delay_ms:+.3f}ms 超出搜索窗 "
                            f"±{eff['search_window_ms']}ms — out_of_range"
                        )
                    else:
                        # 窄窗边缘置信不足但宽窗在窗内测出 -> 采用宽窗估计
                        e, stats = ew, sync_estimate.summarize_trajectory(
                            ew.frames, sample_rate
                        )
                        used_est[i] = ew
                        log(
                            f"channel-sync: CH{i + 1} 窄窗无可测帧, "
                            f"宽窗窗内测出 {ew.delay_ms:+.3f}ms — 采用宽窗估计"
                        )
                else:
                    # 窄窗宽窗均无足够可测帧: 用"有信号的帧数"区分原因 —
                    # 有信号但相关性不足 -> low_confidence;
                    # 有信号的帧本身就太少 (稀疏内容/极短素材) -> insufficient_frames
                    attempted = sum(
                        1 for f in e.frames if f.confidence > 0.0
                    )
                    if attempted >= eff["min_usable_frames"]:
                        reason_ = "low_confidence"
                        log(
                            f"channel-sync: CH{i + 1} 有信号帧 {attempted} 个但"
                            f"均可测性不足 (相关峰值未过门) — low_confidence"
                        )
                    else:
                        reason_ = "insufficient_frames"
            elif coverage < eff["min_usable_fraction"]:
                # 轨迹证据覆盖率不足 (存在大洞): 恒定性判断不可靠 -> 宽窗复测
                ew = wide_estimate(i)
                if (
                    ew.usable_frames >= eff["min_usable_frames"]
                    and ew.confidence >= eff["min_confidence"]
                ):
                    if abs(ew.delay_samples) > search_samples:
                        reason_ = "out_of_range"
                    else:
                        e, stats = ew, sync_estimate.summarize_trajectory(
                            ew.frames, sample_rate
                        )
                        used_est[i] = ew
                        log(
                            f"channel-sync: CH{i + 1} 窄窗证据覆盖率 "
                            f"{coverage:.0%} 不足, 采用宽窗估计 "
                            f"({ew.delay_ms:+.3f}ms)"
                        )
                else:
                    reason_ = "low_confidence"
                    log(
                        f"channel-sync: CH{i + 1} 窄窗证据覆盖率 "
                        f"{coverage:.0%} 不足且宽窗不可靠 — low_confidence"
                    )
            elif sync_estimate.boundary_pileup(e.frames, search_samples) > 0.5:
                # 边缘堆积: 宽窗确认真实时差是否在窗内
                ew = wide_estimate(i)
                if (
                    ew.usable_frames >= eff["min_usable_frames"]
                    and ew.confidence >= eff["min_confidence"]
                    and abs(ew.delay_samples) > search_samples + 0.5
                ):
                    reason_ = "out_of_range"
                    log(
                        f"channel-sync: CH{i + 1} 宽窗复测 delay "
                        f"{ew.delay_ms:+.3f}ms 超出搜索窗 "
                        f"±{eff['search_window_ms']}ms — out_of_range"
                    )
                # 宽窗也在窗内 -> 窄窗边缘值可用, 按常规门继续
            if reason_ is None:
                if e.confidence < eff["min_confidence"]:
                    reason_ = "low_confidence"
                elif not sync_estimate.classify_constant(
                    stats, mad_max_ms=eff["mad_max_ms"],
                    max_ppm=eff["constant_max_ppm"],
                    sample_rate=sample_rate,
                    drift_min_ms=eff["drift_min_ms"],
                ):
                    reason_ = "non_constant"
                elif e.delay_samples != e.delay_samples:
                    reason_ = "low_confidence"
                elif abs(e.delay_samples) > search_samples:
                    reason_ = "out_of_range"
            if reason_ is not None:
                rows[i] = est_row(i, e, stats, "untouched", reason_)
                rows[i]["rms_dbfs"] = (
                    round(rms_of[i], 1) if rms_of.get(i) is not None else None
                )
                log(
                    f"channel-sync: CH{i + 1} {reason_} (conf "
                    f"{e.confidence:.2f}, usable {e.usable_frames}, "
                    f"mad {stats.mad_ms:.3f}ms, "
                    f"ppm {stats.drift_ppm:.2f}) — 该轨 untouched"
                )
                continue
            if abs(e.delay_ms) < eff["aligned_max_ms"]:
                rows[i] = est_row(i, e, stats, "already_aligned", None)
            else:
                rows[i] = est_row(i, e, stats, "fixed", None)
                to_fix.append(i)
            rows[i]["rms_dbfs"] = (
                round(rms_of[i], 1) if rms_of.get(i) is not None else None
            )

        log(
            f"channel-sync: anchor=CH{anchor + 1}, measured "
            + ", ".join(
                f"CH{i + 1}="
                + (
                    f"{used_est.get(i, ests[i]).delay_samples * 1000.0 / sample_rate:+.3f}ms"
                    f"(conf {used_est.get(i, ests[i]).confidence:.2f})"
                    if used_est.get(i, ests[i]).delay_samples
                    == used_est.get(i, ests[i]).delay_samples
                    else "nan"
                )
                for i in healthy if i != anchor
            )
        )

        # 5. 逐轨修正 (纯整数移位) + 独立复检 (失败只回退该轨)
        fixed_ok: list[int] = []
        for i in to_fix:
            e = used_est.get(i, ests[i])   # 宽窗采纳轨用采纳估计 (窄窗 delay 可能为 NaN)
            shift = int(np.rint(e.delay_samples))
            try:
                sync_fix.shift_stream(
                    arrs[i], fixed_raw[i],
                    delay_samples=e.delay_samples,
                    sample_rate=sample_rate,
                    storage_dtype=storage[i],
                    chunk_seconds=eff["fix_chunk_seconds"],
                )
            except Exception as exc:      # noqa: BLE001 — 单轨失败不回滚其它轨
                rows[i] = est_row(i, e,
                                  sync_estimate.summarize_trajectory(
                                      e.frames, sample_rate),
                                  "untouched", "recheck_residual")
                log(f"channel-sync: WARNING CH{i + 1} shift failed: {exc}")
                continue
            try:
                residual = sync_fix.recheck_residual(
                    arrs[anchor],
                    sync_estimate.open_source(fixed_raw[i], storage[i]),
                    sample_rate=sample_rate,
                    storage_dtype=storage[i],
                    anchor_segment_seconds=eff["anchor_segment_seconds"],
                    fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                )
            except Exception as exc:      # noqa: BLE001
                residual = float("nan")
                log(f"channel-sync: WARNING CH{i + 1} recheck error: {exc}")
            if residual != residual or abs(residual) >= eff["verify_max_ms"]:
                rows[i]["decision"] = "untouched"
                rows[i]["reason"] = "recheck_residual"
                rows[i]["shift_samples"] = None
                log(
                    f"channel-sync: WARNING CH{i + 1} 复检残差 "
                    f"{residual:+.4f}ms 超门 {eff['verify_max_ms']}ms — "
                    "仅该轨回退为 untouched"
                )
                continue
            rows[i]["shift_samples"] = shift
            rows[i]["fractional_part_samples"] = j(
                e.delay_samples - float(shift), 3
            )
            log(f"channel-sync: fixed CH{i + 1} "
                f"(shift {shift} samples, 复检残差 {residual:+.4f}ms)")
            fixed_ok.append(i)

        # 6. 结果范围判定
        failed_est = [
            i for i in healthy if i != anchor
            and rows[i]["reason"] in _ESTIMATION_FAIL_REASONS
        ]
        aligned_any = any(
            rows[i]["decision"] == "already_aligned"
            for i in healthy if i != anchor
        )
        if not fixed_ok and failed_est and not aligned_any:
            detail = "all measurable targets failed: " + "; ".join(
                f"CH{i + 1} {rows[i]['reason']}" for i in failed_est
            )
            log(f"channel-sync: WARNING {detail}")
            return finish("measure_failed", detail, channels=rows)

        if not fixed_ok:
            scope = "partial" if any(
                r["decision"] == "untouched" for r in rows
            ) else "file"
            log(
                f"channel-sync: already aligned (无轨需要修正; 可测轨 "
                f"|delay| < {eff['aligned_max_ms']} ms)"
            )
            return finish("already_aligned",
                          "no track requires a fix", scope=scope,
                          channels=rows)

        # 7. per-stream 输出: fixed 轨回编码 (源 codec), 其余轨 stream copy
        audio_files: list[Path] = []
        for i in range(n_tracks):
            out = work_dir / f"audio_{i}.mov"
            if i in fixed_ok:
                src_raw = fixed_raw[i]
                fmt = "f32le" if storage[i] == "f32" else "f64le"
                proc = subprocess.run(
                    [
                        str(ffmpeg), "-v", "error", "-nostdin", "-y",
                        "-f", fmt, "-ar", str(sample_rate), "-ac", "1",
                        "-i", str(src_raw),
                        "-c:a", codecs[i],
                        "-f", _muxer_for_entry(entries[i]), str(out),
                    ],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    errors="replace", timeout=1800,
                )
                if proc.returncode != 0 or not out.is_file():
                    return finish(
                        "verify_failed",
                        f"re-encode stream {i} failed: "
                        f"{(proc.stderr or '')[-200:]}",
                        channels=rows,
                    )
            else:
                ok_c, err_c = _copy_stream(
                    ffmpeg, source, i, out, _muxer_for_entry(entries[i])
                )
                if not ok_c:
                    return finish(
                        "verify_failed",
                        f"stream copy {i} failed: {err_c[-200:]}",
                        channels=rows,
                    )
            audio_files.append(out)

        scope = "partial" if any(
            r["decision"] == "untouched" for r in rows
        ) else "file"
        log(
            f"channel-sync: applied ({len(fixed_ok)}/{n_tracks} tracks "
            f"fixed, scope={scope})"
        )

        # 8. transparent: 视频/非音频 stream copy + 音频轨替换重封装
        output_file: Path | None = None
        file_copied = False
        if t_mode and dst is not None:
            if gpac is None:
                log("channel-sync: transparent remux needs GPAC — 回退原样拷贝")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dst)
                output_file, file_copied = dst, True
            else:
                try:
                    from preservation.dji import track_manifest

                    movie_ts, tracks = track_manifest(gpac, source)
                    video_any = any(
                        t["handler"] == "vide" for t in tracks
                    )
                    if not video_any:
                        raise RuntimeError(
                            f"no video track in {source} for transparent remux"
                        )
                    gpac.movie_timescale = movie_ts
                    adds = [f"{source}#video"]
                    for t in tracks:
                        if t["handler"] in ("vide", "soun"):
                            continue
                        adds.append(f"{source}#{t['id']}")
                    for af in audio_files:
                        adds.append(f"{af}#audio")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        dst.unlink()
                    log("channel-sync: transparent remux (video/non-audio "
                        "stream copy + fixed audio) with MP4Box...")
                    gpac.mux_new(dst, adds)
                    # GPAC 输出 mvhd timescale 与源不一致: 按实际 timescale
                    # 修复 tkhd/elst 时长 (同 classic 路径修正辅助)
                    repair_remux_timescale(dst, log=log)
                    output_file = dst
                except Exception as exc:      # noqa: BLE001
                    log(f"channel-sync: transparent remux failed: {exc} — "
                        "回退原样拷贝")
                    try:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, dst)
                        output_file, file_copied = dst, True
                    except OSError as exc2:
                        return finish(
                            "verify_failed",
                            f"transparent fallback copy failed: {exc2}",
                            channels=rows,
                        )

        return report(
            "applied",
            f"measure -> fix -> verify OK ({len(fixed_ok)}/{n_tracks} fixed)",
            scope=scope,
            channels=rows,
            audio_files=audio_files,
            fixed_files=[work_dir / f"audio_{i}.mov" for i in fixed_ok],
            output_file=output_file,
            file_copied=file_copied,
        )
    finally:
        for a in arrs:
            _close_map(a)
        for p in intermediates:
            try:
                p.unlink()
            except OSError:
                pass
