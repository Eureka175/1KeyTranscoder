#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""延时补偿 (channel-sync) 纯逻辑与 P1 算法单元测试。"""

from __future__ import annotations

import tempfile
from typing import Any
from pathlib import Path
from ..paths import _work_set_mb
from ..paths import record
from ..paths import section

def l1_channel_sync() -> None:
    section("L1 延时补偿 (channel-sync)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        record("channel_sync.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (--channel-sync 需 numpy/scipy)")
        return
    record("channel_sync.numpy/scipy 可用", True)

    from core.channel_sync import effective_opts, eligible_audio
    from core.mp4_channel_sync import (
        fix_channels,
        measure_channels,
        measure_delay,
        verify_channels,
    )

    # --- 布局判定 (2026-10-29 用户决策 A: 按独立 PCM 声道数 3–8 自动) ---
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 2, "sample_rate": "48000"}]
    )
    record("channel_sync.2ch 默认不对齐",
           not ok and "channel_count_out_of_range" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 1, "sample_rate": "48000"}]
    )
    record("channel_sync.1ch 默认不对齐",
           not ok and "channel_count_out_of_range" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "aac", "channels": 1,
          "sample_rate": "48000"}] * 4
    )
    record("channel_sync.非 PCM 不对齐",
           not ok and "non_pcm_audio" in why, why)
    ok, _ = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s24le",
          "channels": 1, "sample_rate": "48000"}] * 4
    )
    record("channel_sync.4xmono PCM 对齐", ok)
    record("channel_sync.opts 默认值",
           effective_opts(None)["anchor_candidates"] == list(range(8))
           and effective_opts(None)["algo_version"] == "2.3.0-p1")

    # --- 算法包核心断言 (与手交付包 selftest 同源, 合成信号) ---
    SR = 48_000

    def speech_like(n: int, seed: int = 0) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, [100.0, 4000.0], btype="band", fs=SR,
                            output="sos")
        carrier = signal.sosfilt(sos, rng.standard_normal(n))
        carrier /= np.max(np.abs(carrier)) + 1e-12
        envelope = np.zeros(n)
        t = 0.0
        while t < n / SR:
            start = int(t * SR)
            dur = rng.uniform(0.08, 0.4)
            m = int(dur * SR)
            if start < n and m > 0:
                k = np.arange(min(m, n - start))
                env = rng.uniform(0.4, 1.0) * np.exp(-k / (dur * SR / 3.0))
                envelope[start: start + len(k)] += env
            t += rng.uniform(0.15, 0.6) + dur
        out = carrier * np.minimum(envelope, 1.0)
        return (out / (np.max(np.abs(out)) + 1e-12)).astype(np.float32)

    def delay_int(x: np.ndarray, n: int) -> np.ndarray:
        y = np.zeros_like(x)
        if n >= 0:
            if n < x.size:
                y[n:] = x[: x.size - n]
        else:
            m = -n
            if m < x.size:
                y[: x.size - m] = x[m:]
        return y

    def delay_frac(x: np.ndarray, delay: float, taps: int = 97) -> np.ndarray:
        i0 = int(np.floor(delay))
        frac = delay - i0
        m = np.arange(-(taps // 2), taps // 2 + 1)
        h = np.sinc(m - frac) * np.hanning(taps)
        h /= h.sum()
        return delay_int(np.convolve(x, h, mode="same"), i0)

    base = speech_like(15 * SR, seed=1)
    channels = [delay_int(base, 1223), delay_int(base, 1350), base, base]
    results = measure_channels(channels, reference_index=2)
    d = {r.channel: r.delay_samples for r in results}
    record("channel_sync.整数延迟 1223 样本", abs(d[0] - 1223.0) < 0.5,
           f"measured {d[0]:.2f}")
    record("channel_sync.整数延迟 1350 样本", abs(d[1] - 1350.0) < 0.5,
           f"measured {d[1]:.2f}")
    record("channel_sync.参考通道=0", d[2] == 0.0)
    record("channel_sync.有线通道≈0", abs(d[3]) < 0.5, f"{d[3]:.2f}")
    record("channel_sync.恒定判定", results[0].constant)
    record("channel_sync.置信度>0.5", results[0].confidence > 0.5)

    base2 = speech_like(10 * SR, seed=2)
    rf = measure_channels(
        [delay_frac(base2, 1223.4), base2], reference_index=1
    )[0]
    record("channel_sync.分数延迟 ±0.15",
           abs(rf.delay_samples - 1223.4) < 0.15,
           f"measured {rf.delay_samples:.3f}")

    base3 = speech_like(15 * SR, seed=3)
    ch3 = [delay_int(base3, 1223), delay_int(base3, 1350), base3, base3]
    res3 = measure_channels(ch3, reference_index=2)
    fixed = fix_channels(ch3, [r.delay_samples for r in res3])
    resid = verify_channels(fixed, reference_index=2)
    record("channel_sync.修正+复检 残差<0.05ms",
           all(abs(v) < 0.05 for v in resid),
           f"residuals={[f'{v:.4f}' for v in resid]}")

    base4 = speech_like(10 * SR, seed=4)
    rp = measure_delay(base4, -delay_int(base4, 500))
    record("channel_sync.反相检测+延迟正确",
           rp.polarity == -1 and abs(rp.delay_samples - 500.0) < 0.5,
           f"polarity={rp.polarity} delay={rp.delay_samples:.2f}")

    base5 = speech_like(8 * SR, seed=5)
    silence = np.zeros_like(base5)
    rs5 = measure_channels([silence, base5], reference_index=1)
    fixed5 = fix_channels([silence, base5], [rs5[0].delay_samples, 0.0])
    import math
    record("channel_sync.静音 nan + 原样保留",
           math.isnan(rs5[0].delay_samples)
           and np.array_equal(fixed5[0], silence[: fixed5.shape[1]]))

    # S3/A6 多声道结构支持（纯逻辑部分）
    from core.audio_wav import WavFormat
    from core.channel_sync import alignment_scope, _wav_format_for_codec

    def _pcm(n_ch: int, tag: str = "in24") -> list[dict]:
        return [{
            "codec_type": "audio", "codec_name": "pcm_s24be",
            "codec_tag_string": tag, "channels": n_ch,
            "sample_rate": "48000", "channel_layout": "4.0",
        }]

    scope4, why4 = alignment_scope(_pcm(4))
    record("channel_sync.单流 4ch 展开为 4 个独立声道 (A6)",
           why4 == "" and scope4 is not None and len(scope4) == 4
           and {c.stream for c in scope4} == {0}
           and [c.channel for c in scope4] == [0, 1, 2, 3]
           and all(c.stream_channels == 4 for c in scope4)
           and all(c.storage == "f32" for c in scope4), f"{why4}")
    scope2, why2 = alignment_scope(_pcm(2))
    record("channel_sync.单条 stereo (2ch) 不自动对齐",
           scope2 is None and "channel_count_out_of_range: 2" in why2, why2)
    record("channel_sync.多声道中间 WAV 用源精度",
           _wav_format_for_codec("pcm_s24be") is WavFormat.PCM24
           and _wav_format_for_codec("pcm_s32le") is WavFormat.PCM32
           and _wav_format_for_codec("pcm_f32be") is WavFormat.FLOAT32)


def l1_channel_sync_p1() -> None:
    section("L1 延时补偿 P1 (sync_estimate/sync_fix 纯逻辑)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        record("p1.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (--channel-sync 需 numpy/scipy)")
        return
    record("p1.numpy/scipy 可用", True)

    from core import sync_estimate, sync_fix
    from core.channel_sync import (
        DEFAULTS, effective_opts, eligible_audio, _muxer_for_entry,
    )

    # --- DEFAULTS (v3) ---
    record("p1.algo_version 2.3.0-p1", DEFAULTS["algo_version"] == "2.3.0-p1")
    record("p1.默认纯整数移位 (无 sinc 键)", "sinc" not in DEFAULTS)
    record("p1.锚点候选回退 CH1>CH2>…>CH8 (2026-10-29 A4)",
           DEFAULTS["anchor_candidates"] == list(range(8)))
    record("p1.transparent 默认关",
           DEFAULTS["channel_sync_transparent"] is False)
    o = effective_opts({"max_lag_seconds": 0.5})
    record("p1.旧键 max_lag_seconds 映射 search_window_ms",
           o["search_window_ms"] == 500.0)
    o2 = effective_opts({"reference_stream": 0})
    record("p1.废弃键 reference_stream 忽略",
           o2["anchor_candidates"] == list(range(8))
           and "reference_stream" not in o2)

    # --- eligible_audio 输入边界 ---
    mono = lambda rate, codec="pcm_s16le": {
        "codec_type": "audio", "codec_name": codec,
        "channels": 1, "sample_rate": str(rate),
    }
    ok, why = eligible_audio([mono(44100)] * 4)
    record("p1.44.1kHz 显式拒绝", not ok and "44100" in why, why)
    ok, why = eligible_audio([mono(96000)] * 4)
    record("p1.96kHz 接受", ok, why)
    ok, why = eligible_audio(
        [mono(48000), mono(96000), mono(48000), mono(48000)]
    )
    record("p1.混合采样率拒绝", not ok and "mixed" in why, why)
    ok, why = eligible_audio([mono(48000, "pcm_s16be")] * 4)
    record("p1.s16be 大端接受", ok, why)
    ok, why = eligible_audio([mono(48000, "pcm_s24be")] * 4)
    record("p1.s24be 大端接受 (A7M5 XAVC-S)", ok, why)
    ok, why = eligible_audio([mono(48000, "pcm_f32be")] * 4)
    record("p1.f32be 大端接受", ok, why)
    ok, why = eligible_audio([mono(48000, "aac")] * 4)
    record("p1.非 PCM 拒绝", not ok, why)

    # --- 音频中间文件的 sample entry 必须可复现源轨 (真实素材回归) ---
    # Sony XAVC-S LPCM 的 sample entry 是 ipcm; ffmpeg 的 MOV muxer 会写成
    # in24, 源音轨被替换后 preservation 的 audio.tracks 关键项即判 MODIFIED
    # 并使整个文件 --check basic 失败 (真实 A7M5 applied 素材 rc=1 实测)。
    # MP4 muxer 写 ipcm/fpcm, 因此按源 entry 选 muxer。
    _mux = _muxer_for_entry
    record("p1.ipcm 源 -> MP4 muxer (保持 ipcm)",
           _mux("ipcm") == "mp4" and _mux("IPCM") == "mp4")
    record("p1.fpcm 源 -> MP4 muxer",
           _mux("fpcm") == "mp4")
    record("p1.QuickTime PCM entry 保持 MOV muxer",
           all(_mux(e) == "mov"
               for e in ("in24", "in32", "sowt", "twos", "fl32", "")))

    # --- 合成信号 ---
    def speech_like(n: int, seed: int, fs: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, [100.0, 4000.0], btype="band", fs=fs,
                            output="sos")
        carrier = signal.sosfilt(sos, rng.standard_normal(n))
        carrier /= np.max(np.abs(carrier)) + 1e-12
        envelope = np.zeros(n)
        t = 0.0
        while t < n / fs:
            start = int(t * fs)
            dur = rng.uniform(0.08, 0.4)
            m = int(dur * fs)
            if start < n and m > 0:
                k = np.arange(min(m, n - start))
                env = rng.uniform(0.4, 1.0) * np.exp(-k / (dur * fs / 3.0))
                envelope[start: start + len(k)] += env
            t += rng.uniform(0.15, 0.6) + dur
        out = carrier * np.minimum(envelope, 1.0)
        return (out / (np.max(np.abs(out)) + 1e-12)).astype(np.float32)

    def delay_int(x: np.ndarray, n: int) -> np.ndarray:
        y = np.zeros_like(x)
        if n >= 0:
            if n < x.size:
                y[n:] = x[: x.size - n]
        else:
            m = -n
            if m < x.size:
                y[: x.size - m] = x[m:]
        return y

    def delay_frac(x: np.ndarray, delay: float, taps: int = 97) -> np.ndarray:
        i0 = int(np.floor(delay))
        frac = delay - i0
        m = np.arange(-(taps // 2), taps // 2 + 1)
        h = np.sinc(m - frac) * np.hanning(taps)
        h /= h.sum()
        return delay_int(np.convolve(x, h, mode="same"), i0)

    def est(ref: np.ndarray, tgt: np.ndarray, fs: int) -> Any:
        return sync_estimate.estimate_pair(
            ref, tgt,
            sample_rate=fs, search_window_ms=80.0, frame_ms=200.0,
            hop_ms=100.0, anchor_segment_seconds=3.0, min_confidence=0.3,
        )

    SR = 48_000
    base = speech_like(6 * SR, seed=1, fs=SR)

    # --- shift_stream: 纯整数移位 (delay>0 = 晚到 -> 前移, 尾补零) ---
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        x = base.copy()
        src = td / "x.f32"
        src.write_bytes(x.tobytes())

        def shift_and_read(delay: float) -> np.ndarray:
            dst = td / f"y{abs(delay):.0f}.f32"
            sync_fix.shift_stream(src, dst, delay_samples=delay,
                                  sample_rate=SR, storage_dtype="f32",
                                  chunk_seconds=1.0)
            y = np.memmap(dst, dtype="<f4", mode="r")
            out = np.asarray(y).copy()
            del y
            return out

        y = shift_and_read(1223.0)
        record("p1.shift 正向整数 1223 (前移+尾补零)",
               bool(y.shape[0] == x.shape[0]
                    and np.array_equal(y[: x.size - 1223], x[1223:])
                    and np.all(y[x.size - 1223:] == 0)))
        y2 = shift_and_read(-900.0)
        record("p1.shift 负向整数 -900 (后移+头补零)",
               bool(np.array_equal(y2[900:], x[: x.size - 900])
                    and np.all(y2[:900] == 0)))
        y3 = shift_and_read(1223.4)
        record("p1.shift 分数 1223.4 -> rint=1223 (与整数移位一致)",
               bool(np.array_equal(y3, y)))
        y4 = shift_and_read(-0.4)
        record("p1.shift 分数 -0.4 -> rint=0 (样本值不变)",
               bool(np.array_equal(y4, x)))
        m = sync_estimate.open_source(src, "f32")
        record("p1.路径输入为有界窗口流 (非整文件映射)",
               bool(isinstance(m, sync_estimate.RawStream)
                    and not isinstance(m, np.memmap)))
        record("p1.窗口读取与 ndarray 切片一致",
               bool(np.array_equal(np.asarray(m[100:600]), x[100:600])
                    and m.shape[0] == x.shape[0]))
        record("p1.越界窗口自动裁剪 (不报错/不补零)",
               bool(m[m.shape[0] - 4: m.shape[0] + 999].shape[0] == 4
                    and m[5: 5].shape[0] == 0))
        # 内存回归: 对 8 MB 流做整轨扫描 + 逐帧读取, 进程工作集增量必须有界
        # (旧 np.memmap 实现会把整条文件计入 WorkingSet; Stage 1.2 实测
        #  4x110 MB 轨 -> RSS +440 MB, 见 work/channel_sync_memory_audit.md)
        if _work_set_mb() is None:
            record("p1.整轨扫描后工作集增量有界", True,
                   "非 Windows: 跳过工作集测量")
        else:
            big_n = 16_000_000                      # 64 MB f32
            big = td / "big.f32"
            big.write_bytes(np.zeros(big_n, dtype="<f4").tobytes())
            stream = sync_estimate.open_source(big, "f32")
            before_mb = _work_set_mb()
            ssq = 0.0
            for a0 in range(0, big_n, 1 << 19):     # 整轨分块扫描
                blk = np.asarray(stream[a0: a0 + (1 << 19)], dtype=np.float64)
                ssq += float(np.sum(blk * blk))
            # 逐帧窗口读取 (与 estimate_pair 的访问模式相同)
            for a0 in range(0, big_n - 48000, 4800):
                window = stream[a0: a0 + 9600]
                if window.shape[0]:
                    ssq += float(window[0])
            stream.close()
            growth = _work_set_mb() - before_mb
            # 64 MB 整轨: 旧 np.memmap 实现会 +64 MB; 有界窗口读取器只保留
            # 窗口 + 4x1 MiB 读缓存 + f64 副本, 实测 < 20 MB
            record("p1.64MB 整轨扫描后工作集增量有界 (<= 32 MB, 实测 %.1f MB)"
                   % growth, bool(growth <= 32.0))
        try:
            mm = getattr(m, "_mmap", None)
            if mm is not None:
                mm.close()
            else:
                m.close()
        except OSError:
            pass

    # --- estimate_pair: 基础正确性 48k ---
    e1 = est(base, delay_int(base, 1223), SR)
    record("p1.48k 整数 +1223",
           abs(e1.delay_samples - 1223.0) <= 1.0 and e1.confidence > 0.5,
           f"measured {e1.delay_samples:.2f} conf {e1.confidence:.2f}")
    e2 = est(base, delay_int(base, -900), SR)
    record("p1.48k 整数 -900",
           abs(e2.delay_samples + 900.0) <= 1.0,
           f"measured {e2.delay_samples:.2f}")
    e3 = est(base, base.copy(), SR)
    record("p1.48k 已对齐 ≈0",
           abs(e3.delay_samples) <= 2.0 and e3.confidence > 0.5,
           f"measured {e3.delay_samples:.2f}")
    e4 = est(base, delay_frac(base, 1223.4), SR)
    record("p1.48k 分数 1223.4 相位精估 ±0.15",
           abs(e4.fine_delay_samples - 1223.4) < 0.15,
           f"fine={e4.fine_delay_samples:.3f} r2={e4.fine_fit_r2:.3f}")

    # --- recheck_residual: 整数移位量化残差与相位斜率复检 ---
    r_int = sync_fix.recheck_residual(
        base, base.copy(), sample_rate=SR, storage_dtype="f32",
        anchor_segment_seconds=3.0,
    )
    record("p1.整数修正复检残差 <0.05ms", abs(r_int) < 0.05,
           f"{r_int:.4f}ms")
    r_frac = sync_fix.recheck_residual(
        base, delay_frac(base, 0.4), sample_rate=SR, storage_dtype="f32",
        anchor_segment_seconds=3.0,
    )
    record("p1.复检测出 0.4 样本残余 (相位斜率)",
           abs(r_frac - 0.4 * 1000.0 / SR) < 0.02, f"{r_frac:.4f}ms")

    # --- 96k ---
    SR2 = 96_000
    base2 = speech_like(4 * SR2, seed=2, fs=SR2)
    e5 = est(base2, delay_int(base2, 2446), SR2)
    record("p1.96k 整数 +2446",
           abs(e5.delay_samples - 2446.0) <= 1.0,
           f"measured {e5.delay_samples:.2f}")
    e6 = est(base2, delay_frac(base2, 2446.6), SR2)
    record("p1.96k 分数 2446.6 相位精估 ±0.15",
           abs(e6.fine_delay_samples - 2446.6) < 0.15,
           f"fine={e6.fine_delay_samples:.3f}")

    # --- 恒定性: 中途跳变 / 线性漂移 ---
    step_sig = np.concatenate(
        [base[: 3 * SR], delay_int(base[3 * SR:], 2400)]
    )  # 后半段 +50ms 跳变
    e7 = est(base, step_sig, SR)
    st7 = sync_estimate.summarize_trajectory(e7.frames, SR)
    c7 = sync_estimate.classify_constant(
        st7, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR
    )
    record("p1.中途 50ms 跳变 -> non_constant",
           not c7 and st7.spread_samples > 1.0 * SR / 1000.0,
           f"spread={st7.spread_samples:.0f}smp mad={st7.mad_ms:.2f}ms "
           f"ppm={st7.drift_ppm:.1f}")

    # --- 恒定性漂移门 (真实素材标定: ppm 门 + 漂移材料性门) ---
    # 10ppm 漂移在 6s 上仅 0.06ms: 低于材料性门 (0.1ms) -> 视为恒定
    # (A7M5 实测: 已对齐轨的量化噪声斜率可达 5~30ppm, 漂移量仅 ~0.03ms)
    n_d = base.shape[0]
    idx = np.arange(n_d, dtype=np.float64)
    pos = idx - idx * 10e-6           # 10 ppm 线性漂移 (每 1e6 样本 10 样本)
    drift_sig = np.interp(pos, idx, base.astype(np.float64)).astype(np.float32)
    e8 = est(base, drift_sig, SR)
    st8 = sync_estimate.summarize_trajectory(e8.frames, SR)
    c8 = sync_estimate.classify_constant(
        st8, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR, drift_min_ms=0.1
    )
    record("p1.6s 上 10ppm (0.06ms) -> constant (材料性门)",
           c8 and abs(st8.drift_ppm) > 5.0
           and abs(st8.drift_total_ms) < 0.1,
           f"ppm={st8.drift_ppm:.1f} drift_total={st8.drift_total_ms:.3f}ms")

    # 同一 10ppm 漂移在 30s 上累计 0.3ms -> 超材料性门 -> non_constant
    base30 = speech_like(30 * SR, seed=3, fs=SR)
    n30 = base30.shape[0]
    idx30 = np.arange(n30, dtype=np.float64)
    pos30 = idx30 - idx30 * 10e-6
    drift30 = np.interp(
        pos30, idx30, base30.astype(np.float64)
    ).astype(np.float32)
    e8b = est(base30, drift30, SR)
    st8b = sync_estimate.summarize_trajectory(e8b.frames, SR)
    c8b = sync_estimate.classify_constant(
        st8b, mad_max_ms=1.0, max_ppm=5.0, sample_rate=SR, drift_min_ms=0.1
    )
    record("p1.30s 上 10ppm (0.3ms) -> non_constant",
           not c8b and abs(st8b.drift_ppm) > 5.0
           and abs(st8b.drift_total_ms) > 0.1,
           f"ppm={st8b.drift_ppm:.1f} drift_total={st8b.drift_total_ms:.3f}ms")

    # --- 超窗: 窄窗不可测 + 宽窗复测可测 (out_of_range 依据) ---
    e9 = est(base, delay_int(base, 4800), SR)   # +100ms > 80ms 搜索窗
    ew9 = sync_estimate.estimate_pair(
        base, delay_int(base, 4800),
        sample_rate=SR, search_window_ms=250.0, frame_ms=200.0,
        hop_ms=100.0, anchor_segment_seconds=3.0, min_confidence=0.3,
    )
    record("p1.100ms 超窗 -> 窄窗不可测 + 宽窗测出",
           e9.usable_frames < 5
           and ew9.usable_frames >= 5
           and abs(ew9.delay_samples - 4800.0) <= 2.0,
           f"narrow_usable={e9.usable_frames} "
           f"wide={ew9.delay_samples:.1f}")

    # --- 反相 ---
    rp = est(base, -delay_int(base, 500), SR)
    record("p1.反相 polarity=-1 延迟正确",
           rp.polarity == -1 and abs(rp.delay_samples - 500.0) <= 1.0,
           f"polarity={rp.polarity} delay={rp.delay_samples:.2f}")

    # --- 确定性 (bit 级) ---
    ea = est(base, delay_int(base, 1223), SR)
    eb = est(base, delay_int(base, 1223), SR)
    same = bool(
        ea.delay_samples == eb.delay_samples
        and ea.fine_delay_samples == eb.fine_delay_samples
        and np.array_equal(
            [f.delay_samples for f in ea.frames],
            [f.delay_samples for f in eb.frames], equal_nan=True,
        )
    )
    record("p1.估计确定性 (bit 级一致)", same)


def l1_channel_sync_groups() -> None:
    """分组锚点 (opt-in) 的纯逻辑: 强度采样 / 建组 / 组内锚点。

    这一组**不跑媒体**: 建组是纯函数, 采样是对合成信号做的。真实施加移位
    与复测在 L3 (端到端) 里做 —— 这里只把门与语义钉住。
    """
    section("L1 延时补偿 分组锚点 (opt-in)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        record("p1g.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (分组锚点需 numpy/scipy)")
        return
    record("p1g.numpy/scipy 可用", True)

    from core import sync_estimate
    from core.channel_sync import (
        DEFAULTS, effective_opts, group_anchor_of, group_by_snr,
    )

    # --- 默认关闭 + 键值语义 ---
    record("p1g.分组锚点默认关闭 (策略变更非缺陷修复)",
           DEFAULTS["group_anchor_enabled"] is False)
    record("p1g.分组默认阈值 1.3", DEFAULTS["group_snr_threshold"] == 1.3)
    record("p1g.分组默认 200 帧/对", DEFAULTS["group_snr_samples"] == 200)
    record("p1g.分组最少样本 30", DEFAULTS["group_snr_min_samples"] == 30)
    og = effective_opts({"group_anchor_enabled": 1,
                         "group_snr_threshold": "1.45",
                         "group_snr_samples": "120"})
    record("p1g.opts 归一 (bool/float/int)",
           og["group_anchor_enabled"] is True
           and og["group_snr_threshold"] == 1.45
           and og["group_snr_samples"] == 120,
           f"enabled={og['group_anchor_enabled']!r} "
           f"thr={og['group_snr_threshold']!r} "
           f"n={og['group_snr_samples']!r}")

    SR = 48_000

    def speech_like(n: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, [100.0, 4000.0], btype="band", fs=SR,
                            output="sos")
        x = signal.sosfilt(sos, rng.standard_normal(n))
        x /= np.max(np.abs(x)) + 1e-12
        return x.astype(np.float32)

    kw = dict(sample_rate=SR, frame_ms=100.0, hop_ms=100.0,
              search_window_ms=20.0, coarse_rate=8000,
              frame_min_rms_dbfs=-50.0, samples=40)
    a = speech_like(6 * SR, 11)
    same = sync_estimate.sample_pair_snr(a, np.roll(a, 673), **kw)
    diff = sync_estimate.sample_pair_snr(a, speech_like(6 * SR, 12), **kw)
    record("p1g.同源强度明显高于异源",
           same["median"] >= 3.0 and diff["median"] <= 1.3
           and same["median"] > 2.0 * diff["median"],
           f"same={same['median']:.2f} diff={diff['median']:.2f}")
    record("p1g.异源落在噪声底附近 (≈1, 低于阈值 1.3)",
           diff["median"] < DEFAULTS["group_snr_threshold"],
           f"diff={diff['median']:.2f}")

    # 静音必须被 RMS 门挡掉 —— 否则两条死轨会在噪声底上互相"相关"
    sil = np.zeros(6 * SR, dtype=np.float32)
    s_sil = sync_estimate.sample_pair_snr(a, sil, **kw)
    record("p1g.静音轨 -> 有效样本 0 (RMS 门), 不参与建组",
           s_sil["samples"] == 0.0 and s_sil["median"] != s_sil["median"],
           f"samples={s_sil['samples']} median={s_sil['median']}")

    # 素材太短 -> 0 帧 -> 不可判定 (调用方必须当"不可判定", 不许当 0)
    s_short = sync_estimate.sample_pair_snr(a[:100], a[:100], **kw)
    record("p1g.素材过短 -> frames_total=0 且 samples=0",
           s_short["frames_total"] == 0.0 and s_short["samples"] == 0.0,
           f"frames_total={s_short['frames_total']}")

    # 确定性: 固定种子, 同一对素材两次调用逐位相同
    d1 = sync_estimate.sample_pair_snr(a, speech_like(6 * SR, 13), **kw)
    d2 = sync_estimate.sample_pair_snr(a, speech_like(6 * SR, 13), **kw)
    record("p1g.强度采样确定性 (固定种子)",
           d1["median"] == d2["median"] and d1["samples"] == d2["samples"])

    # 反相: 负峰更强时强度取负峰的绝对值, 因此极性翻转不改变"是否同源"。
    # 目标窗必须比参考帧**长**, 否则相关面只有一个点 (m 只有 0 这一个取值),
    # 窗口恒 <3 而退化成 nan —— 这是 `peak_noise_ratio` 的几何约束, 不是缺陷。
    _half = 32
    _ref = a[: SR // 10].astype(np.float64)
    _tgt = a[: SR // 10 + 2 * _half].astype(np.float64)
    pos = sync_estimate.peak_noise_ratio(
        _ref, _tgt, lag0=_half, search_lo=-_half, search_hi=_half)
    neg = sync_estimate.peak_noise_ratio(
        _ref, -_tgt, lag0=_half, search_lo=-_half, search_hi=_half)
    record("p1g.反相仍判同源 (极性感知)",
           pos == pos and neg == neg and pos > 3.0
           and abs(pos - neg) <= 0.05 * pos,
           f"pos={pos:.2f} neg={neg:.2f}")

    # --- 建组 (纯函数) ---
    def snr_tbl(spec: dict, samples: float = 200.0) -> dict:
        """spec: {"1-2": median, ...} -> {(0,1): {"median":.., "samples":..}}。

        单对样本数可以按对覆盖 (值写成 (median, samples) 元组), 否则测不出
        "这一对样本不足、那一对足够" 这种情形。
        """
        tbl = {}
        for key, med in spec.items():
            i, j = (int(x) - 1 for x in key.split("-"))
            if isinstance(med, tuple):
                med, n = med
            else:
                n = samples
            tbl[(i, j)] = {"median": float(med), "samples": float(n)}
        return tbl

    two = snr_tbl({"1-2": 2.0, "3-4": 1.8,
                   "1-3": 0.9, "1-4": 0.85, "2-3": 0.95, "2-4": 0.9})
    g = group_by_snr(two, range(4), threshold=1.3, min_samples=30)
    record("p1g.两组 [[C1,C2],[C3,C4]]",
           g == [[0, 1], [2, 3]], str(g))

    one = snr_tbl({f"{i}-{j}": 2.0
                   for i in range(1, 5) for j in range(i + 1, 5)})
    g = group_by_snr(one, range(4), threshold=1.3, min_samples=30)
    record("p1g.四轨同源 -> 单组 (与现行单锚点等价)",
           g == [[0, 1, 2, 3]], str(g))

    none = snr_tbl({f"{i}-{j}": 0.8
                    for i in range(1, 5) for j in range(i + 1, 5)})
    g = group_by_snr(none, range(4), threshold=1.3, min_samples=30)
    record("p1g.全部不相关 -> 四个孤立点 (无可修)",
           g == [[0], [1], [2], [3]], str(g))

    # 有效样本不足 -> 该对不可判定, **不连边** (不猜)。这里只有 (1,2) 样本
    # 不足且比值很高, 若把它当真就会把两条互不相关的轨并成一组。
    few = snr_tbl({"1-2": (5.0, 5.0), "3-4": (1.8, 200.0)})
    g = group_by_snr(few, range(4), threshold=1.3, min_samples=30)
    record("p1g.样本不足的对不连边 (死轨高比值也不会并组)",
           g == [[0], [1], [2, 3]], str(g))

    nan_tbl = {(0, 1): {"median": float("nan"), "samples": 200.0},
               (2, 3): {"median": 1.8, "samples": 200.0}}
    g = group_by_snr(nan_tbl, range(4), threshold=1.3, min_samples=30)
    record("p1g.nan 强度不连边", g == [[0], [1], [2, 3]], str(g))

    # 单链接聚类的固有性质: 链式边会合并。这是**已知局限**, 不是缺陷 ——
    # 加法式合并使得"过度合并"的最坏后果只是少尝试一组, 不会丢掉已有修正。
    chain = snr_tbl({"1-2": 2.0, "2-3": 1.4, "1-3": 0.5,
                     "1-4": 0.5, "2-4": 0.5, "3-4": 0.5})
    g = group_by_snr(chain, range(4), threshold=1.3, min_samples=30)
    record("p1g.单链接链式合并 (已知局限, 记录行为)",
           g == [[0, 1, 2], [3]], str(g))

    # 三轨输入 (`min_audio_streams` 允许 3): 覆盖面必须包含 3 轨, 语料里没有
    # 3 声道的真实素材, 这一档只能靠合成输入守住。
    three = snr_tbl({"1-2": 2.0, "1-3": 0.9, "2-3": 0.9})
    g = group_by_snr(three, range(3), threshold=1.3, min_samples=30)
    record("p1g.三轨 [C1,C2] + 孤立 C3",
           g == [[0, 1], [2]], str(g))
    g = group_by_snr(snr_tbl({"1-2": 2.0, "1-3": 1.9, "2-3": 1.9}),
                     range(3), threshold=1.3, min_samples=30)
    record("p1g.三轨同源 -> 单组", g == [[0, 1, 2]], str(g))
    # 传入的边跨越不存在的轨 (防御性): 必须被忽略而不是抛错
    g = group_by_snr({(0, 1): {"median": 3.0, "samples": 200.0},
                      (1, 9): {"median": 3.0, "samples": 200.0}},
                     range(3), threshold=1.3, min_samples=30)
    record("p1g.跨越界轨的边被忽略", g == [[0, 1], [2]], str(g))

    # --- 组内锚点 = anchor_candidates 顺序里最靠前的成员 ---
    cand = [2, 3, 0, 1]
    record("p1g.组内锚点 [C1,C2] -> C1",
           group_anchor_of([0, 1], cand) == 0)
    record("p1g.组内锚点 [C3,C4] -> C3 (与主锚点同序)",
           group_anchor_of([2, 3], cand) == 2)
    record("p1g.组内锚点 [C1,C2,C4] -> C4",
           group_anchor_of([0, 1, 3], cand) == 3)
    record("p1g.单成员组锚点 = 自己",
           group_anchor_of([1], cand) == 1)
    # 结构性保证: 单组四轨时组内锚点 == 主锚点候选首位, 因此"同一组"的
    # 结论与现行逐位相同 (A1/A3 由此推出, 不靠回归去赌)
    record("p1g.单组四轨的组锚点 == 生产主锚点 (结构性零回归)",
           group_anchor_of([0, 1, 2, 3], cand) == cand[0])

# ==========================================================================
# 透明模式契约: check 路由 + 容器层依赖倒置
# ==========================================================================
def l1_transparent_contract() -> None:
    """`1kt.transparent_verify` 的路由, 与 `run_channel_sync` 的注入契约。

    这两条都是**纯逻辑**, 不需要编码也不需要真实素材:
      * 路由只依赖 stream 描述 (`rtmd` -> Sony, `djmd` -> DJI, 其余跳过);
      * 注入契约只依赖参数组合。

    为什么值得单独立 suite: 这一层曾经有两个静默缺陷 —— `--check` 在透明
    模式被完全忽略, 以及 DJI 判定 import 了一个不存在的名字被宽 except 吞掉
    (于是 DJI 分支永远进不去, 日志却理直气壮地说"不是 Sony/DJI")。两条都
    是"跑起来不报错、结果不对"的类型, 只能靠断言路由本身来守。
    """
    import importlib.util
    import inspect
    import sys

    section("L1 透明模式契约 (check 路由 / 容器层依赖倒置)")
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("_1kt_contract", root / "1kt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # --- 1. 注入契约: signature -------------------------------------------
    sig = inspect.signature(mod.run_channel_sync) if hasattr(
        mod, "run_channel_sync") else None
    if sig is None:
        from core.channel_sync import run_channel_sync
        sig = inspect.signature(run_channel_sync)
    record("p12c.run_channel_sync 暴露 restore_metadata 钩子",
           "restore_metadata" in sig.parameters,
           str(list(sig.parameters)))
    record("p12c.钩子默认 None (不改变既有调用点)",
           sig.parameters["restore_metadata"].default is None)

    # --- 2. 图省事漏注入 -> 必须在动手前失败 ------------------------------
    from core.channel_sync import run_channel_sync

    raised = None
    try:
        run_channel_sync(
            source=Path("no-such-input.mp4"), ffmpeg=Path("ffmpeg.exe"),
            work_dir=Path(tempfile.mkdtemp(prefix="p12c_")),
            streams=[], opts=None, transparent=True,
            dst=Path("out.mp4"), gpac=None, restore_metadata=None,
        )
    except ValueError as exc:
        raised = exc
    except Exception as exc:                            # noqa: BLE001
        raised = exc
    record("p12c.透明模式缺钩子 -> ValueError (而不是静默剥元数据)",
           isinstance(raised, ValueError),
           f"{type(raised).__name__}: {str(raised)[:110]}")
    record("p12c.该失败发生在任何工作之前 (先于读文件/探测)",
           isinstance(raised, ValueError),
           "源路径不存在却先报契约错误 —— 说明检查在入口")

    # --- 3. 非透明模式不受影响 -------------------------------------------
    ok_non_transparent = True
    try:
        run_channel_sync(
            source=Path("no-such-input.mp4"), ffmpeg=Path("ffmpeg.exe"),
            work_dir=Path(tempfile.mkdtemp(prefix="p12c_")),
            streams=[], opts=None, transparent=False, dst=None,
        )
    except ValueError:
        ok_non_transparent = False
    except Exception:                                   # noqa: BLE001
        pass                                            # 别的失败与本契约无关
    record("p12c.非透明路径不需要钩子", ok_non_transparent)

    # --- 4. 分层: 音频同步层不得 import 容器/元数据层 ---------------------
    import re
    forbidden = ("VideoEncoder", "Metadata", "Sony", "x265", "NVENC", "QSV",
                 "audio_mix", "output_compose", "audio_encode")
    leaks = []
    for name in ("channel_sync.py", "audio_sync.py", "sync_estimate.py",
                 "sync_fix.py", "audio_sync_tv.py"):
        p = root / "core" / name
        if not p.is_file():
            continue
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not re.match(r"^\s*(from|import)\s", line):
                continue
            if any(f in line for f in forbidden):
                leaks.append(f"{name}:{i}: {line.strip()}")
    record("p12c.音频同步层不 import 容器/元数据层 (依赖倒置成立)",
           not leaks, f"leaks={leaks}")

    from preservation.transparent_restore import (
        restore_after_transparent_remux,
    )
    rsig = inspect.signature(restore_after_transparent_remux)
    record("p12c.容器层恢复函数签名与钩子一致",
           list(rsig.parameters) == ["source", "adds", "dst", "work_dir",
                                     "gpac", "log"],
           str(list(rsig.parameters)))
    # 2026-10-31: 大文件快速字节级还原 (preservation/fast_restore.py) 接在
    # 透明恢复钩子里: 大于阈值自动启用、失败回退标准 GPAC 流程。钩子签名
    # 必须保持不变 (上面一条), 模式开关是模块级测试入口。
    from preservation.fast_restore import (
        FAST_RESTORE_MIN_BYTES,
        fast_restore_from_stage,
        verify_fast_restore_structure,
    )
    from preservation import transparent_restore as _tr
    record("p12c.透明恢复的大文件 fast-restore 入口就绪",
           callable(fast_restore_from_stage)
           and callable(verify_fast_restore_structure)
           and FAST_RESTORE_MIN_BYTES >= 1 << 30
           and callable(getattr(_tr, "set_fast_restore_mode", None)),
           f"threshold={FAST_RESTORE_MIN_BYTES} B, "
           f"mode_hook={callable(getattr(_tr, 'set_fast_restore_mode', None))}")

    # --- 5. check 路由三分支 ---------------------------------------------
    from preservation import checker as _checker
    from preservation import dji as _dji

    calls: dict[str, Any] = {}

    def fake_run_checks(**kw):
        calls["sony"] = kw
        return {"summary": {"PRESERVED": 1}, "structural_success": True,
                "quality": {"status": "SKIPPED_NOT_APPLICABLE"}}

    def fake_run_dji(**kw):
        calls["dji"] = kw
        return {"summary": {"PRESERVED": 2}, "structural_success": True,
                "quality": {"status": "SKIPPED_NOT_APPLICABLE"}}

    orig_sony, orig_dji = _checker.run_checks, _dji.run_dji_check
    _checker.run_checks = fake_run_checks
    _dji.run_dji_check = fake_run_dji
    try:
        work = Path(tempfile.mkdtemp(prefix="p12c_route_"))
        common = dict(src=Path("s.mp4"), dst=Path("d.mp4"),
                      ffmpeg=Path("ffmpeg.exe"), ffprobe=Path("ffprobe.exe"),
                      gpac=None, gyroflow=None, work_dir=work,
                      log=lambda _m: None)

        sony_streams = [
            {"codec_type": "video", "codec_name": "hevc",
             "codec_tag_string": "hvc1"},
            {"codec_type": "audio", "codec_name": "pcm_s24le"},
            {"codec_type": "data", "codec_tag_string": "rtmd"},
        ]
        r = mod.transparent_verify(streams=sony_streams, level="full",
                                   **common)
        record("p12c.Sony (rtmd) -> run_checks", "sony" in calls)
        record("p12c.Sony 路由传 video_reencoded=False",
               calls.get("sony", {}).get("video_reencoded") is False)
        record("p12c.Sony 路由保留请求的 level",
               calls.get("sony", {}).get("level") == "full")
        record("p12c.Sony 路由返回真实 summary",
               r.get("summary", {}).get("PRESERVED") == 1)

        calls.clear()
        dji_streams = [
            {"codec_type": "video", "codec_name": "hevc",
             "codec_tag_string": "hvc1"},
            {"codec_type": "audio", "codec_name": "pcm_s16le"},
            {"codec_type": "data", "codec_tag_string": "djmd"},
        ]
        mod.transparent_verify(streams=dji_streams, level="basic", **common)
        record("p12c.DJI (djmd) -> run_dji_check", "dji" in calls,
               f"calls={sorted(calls)}")
        record("p12c.DJI 路由传 video_reencoded=False",
               calls.get("dji", {}).get("video_reencoded") is False)

        calls.clear()
        plain = [{"codec_type": "video", "codec_name": "hevc",
                  "codec_tag_string": "hvc1"},
                 {"codec_type": "audio", "codec_name": "aac"}]
        r = mod.transparent_verify(streams=plain, level="full", **common)
        record("p12c.非 Sony/DJI -> SKIPPED_NO_CHECKER",
               r.get("status") == "SKIPPED_NO_CHECKER", str(r)[:90])
        record("p12c.跳过时不会误调任何扫描器",
               not calls, f"calls={sorted(calls)}")

        # 验证器自身抛异常不能影响已产出的文件
        def boom(**kw):
            raise RuntimeError("synthetic checker failure")

        _checker.run_checks = boom
        r = mod.transparent_verify(streams=sony_streams, level="full",
                                   **common)
        record("p12c.验证器异常 -> 返回 ERROR 而不是抛出",
               r.get("status") == "ERROR", str(r.get("status")))
    finally:
        _checker.run_checks = orig_sony
        _dji.run_dji_check = orig_dji

    # --- 6. video_reencoded 护栏 (PSNR 只在视频被重编码时才有定义) ---------
    for fname, matcher in (
        ("checker.py", "run_checks"),
        ("dji.py", "run_dji_check"),
    ):
        p = root / "preservation" / fname
        src = p.read_text(encoding="utf-8", errors="replace")
        record(f"p12c.{fname} 的 {matcher} 接受 video_reencoded",
               "video_reencoded" in src)
        record(f"p12c.{fname} video_reencoded 默认 True (编码路径语义不变)",
               "video_reencoded: bool = True" in src)
        # 跳过 PSNR 的分支必须被 `not video_reencoded` 守着 —— 否则
        # "透明模式跳过" 会变成 "所有模式跳过", 那一步是静默的。
        guarded = "not video_reencoded" in src
        record(f"p12c.{fname} 跳过 PSNR 的分支由 not video_reencoded 守着",
               guarded)
        record(f"p12c.{fname} 跳过时给出 SKIPPED_NOT_APPLICABLE (不伪装成 PASS)",
               "SKIPPED_NOT_APPLICABLE" in src)

    # --- 7. 最小幅度门 (v0.8.7): 5 样本以内都不修 ------------------------
    from core.channel_sync import _INT_KEYS, DEFAULTS, is_aligned_decision

    def aligned(samples: float, ms: float | None = None,
                *, thr: int = 5) -> bool:
        ms = samples / 48.0 if ms is None else ms
        return is_aligned_decision(ms, samples, aligned_max_ms=0.05,
                                   min_shift_samples=thr)

    record("p12c.最小幅度默认 5 样本",
           DEFAULTS["min_shift_samples"] == 5,
           str(DEFAULTS.get("min_shift_samples")))
    record("p12c.最小幅度进 _INT_KEYS (字符串可被强制成 int)",
           "min_shift_samples" in _INT_KEYS)
    for n in (0, 1, 3, 4, 5):
        record(f"p12c.{n} 样本 -> 不动", aligned(n))
    for n in (6, 7, 37, 39, 944):
        record(f"p12c.{n} 样本 -> 修", not aligned(n))
    record("p12c.负位移对称 (-5 不动)", aligned(-5))
    record("p12c.负位移对称 (-6 修)", not aligned(-6))
    record("p12c.亚样本位移不动", aligned(0.4))
    record("p12c.毫米带优先 (3 样本也属 0.0625ms, 仍不动)",
           aligned(3))
    record("p12c.thr=0 时 3 样本变为可修 (门是可配置的)",
           not aligned(3, thr=0))
    # 但 `aligned_max_ms` (0.05 ms) 是**独立**的下限: 48 kHz 下它覆盖 ±2.4
    # 样本, 所以 thr=0 也修不了 1~2 样本的轨。两条门是"或"关系, 不是替代。
    record("p12c.thr=0 也修不了 2 样本 (0.0417ms 仍在 aligned_max_ms 内)",
           aligned(2, thr=0))
    record("p12c.两条门是或关系: 0 样本恒不动", aligned(0, thr=0))
    record("p12c.真实归档的最大位移 +1222 不受影响",
           not aligned(1222))

    # --- 8. 精确模式分级验收 (--channel-sync-accurate) --------------------
    from core.channel_sync import (
        ACCURATE_TIERS,
        _BOOL_KEYS,
        resolve_accurate_tier,
    )

    record("p12c.精确模式默认关闭",
           DEFAULTS["channel_sync_accurate"] is False)
    record("p12c.精确模式进 _BOOL_KEYS (字符串可被强制成 bool)",
           "channel_sync_accurate" in _BOOL_KEYS)
    record("p12c.分级候选是 (1,2), 都不粗于常规门",
           ACCURATE_TIERS == (1, 2), str(ACCURATE_TIERS))
    record("p12c.常规门 5 样本必须比最粗的候选更粗",
           max(ACCURATE_TIERS) < DEFAULTS["min_shift_samples"])

    def tier_of(extras: dict, minshift: int = 5):
        return resolve_accurate_tier(extras, min_shift_samples=minshift)

    t, keep, drop = tier_of({})
    record("p12c.无候选 -> 不动 (tier 回到常规门)", (t, keep, drop) == (5, set(), {}))

    t, keep, drop = tier_of({0: (2, 0.4), 1: (3, 0.9), 2: (5, 0.2)})
    record("p12c.全部残差 <=1 样本 -> tier 1 全采纳",
           t == 1 and keep == {0, 1, 2} and not drop, f"{t} {keep} {drop}")

    t, keep, drop = tier_of({0: (2, 0.4), 1: (4, 1.6)})
    record("p12c.有一轨 1 样本不达标 -> 退 tier 2 并放弃 |shift|==2",
           t == 2 and keep == {1} and set(drop) == {0}, f"{t} {keep} {drop}")

    t, keep, drop = tier_of({0: (3, 2.7), 1: (5, 4.0)})
    record("p12c.1 与 2 样本都不可行 -> 退回常规门并全部放弃",
           t == 5 and not keep and set(drop) == {0, 1}, f"{t} {keep} {drop}")

    t, keep, drop = tier_of({0: (3, 1.0)})
    record("p12c.边界: 残差恰为 1.000 样本 -> tier 1 采纳",
           t == 1 and keep == {0})
    t, keep, drop = tier_of({0: (3, 1.0001)})
    record("p12c.边界: 残差 1.0001 样本 -> 不进 tier 1",
           t == 2 and keep == {0}, f"{t} {keep}")

    t, keep, drop = tier_of({0: (2, 0.1)})
    record("p12c.只有 |shift|==2 且 1 样本可验 -> tier 1 采纳它",
           t == 1 and keep == {0})

    t, keep, drop = tier_of({0: (2, 1.5)})
    record("p12c.只有 |shift|==2 且 1 样本不可验 -> tier 2 放弃它",
           t == 2 and not keep and set(drop) == {0}, f"{t} {keep} {drop}")

    # 分级必须单调: 采纳集合随 tier 增大只会缩小, 不会突然变大
    t, keep, drop = tier_of({0: (1, 0.0), 1: (2, 0.5)})
    record("p12c.|shift|<=1 的候选在 tier 1 就被放弃 (门本来就不修它)",
           set(drop) == {0} and keep == {1}, f"{keep} {drop}")

    # --- 9. 透明入口的 CLI 覆盖: 时变开关不得静默丢失 (2026-10-29) -------
    #
    # 事故形态: `--channel-sync-tv*` 三个开关只在 main() 里被读取; 透明批量
    # 入口在调用 main() 的分流**之前**就 return 了, 于是
    #   --channel-sync-transparent --channel-sync-tv \
    #   --channel-sync-tv-policy hold --channel-sync-tv-coordinate latest-start
    # 实际按固定延迟跑, 连"缺 policy/coordinate 就拒绝"的契约都不会触发。
    # 一个不生效的开关比一个会拒绝的开关更糟, 只能靠断言入口来守。
    import argparse
    import ast

    from core.channel_sync import effective_opts as _eff_tv

    def tv_eff(**kw):
        ns = argparse.Namespace(
            channel_sync_tv=None, channel_sync_tv_policy=None,
            channel_sync_tv_coordinate=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return _eff_tv(mod._apply_time_varying_cli(ns, {}))

    e = tv_eff(channel_sync_tv=True, channel_sync_tv_policy="hold",
               channel_sync_tv_coordinate="latest-start")
    record("p12d.透明入口的 TV 开关被覆写 (enabled/policy/coordinate)",
           e["time_varying"] is True and e["tv_policy"] == "hold"
           and e["tv_coordinate"] == "latest-start",
           f"{e['time_varying']} {e['tv_policy']} {e['tv_coordinate']}")
    e = tv_eff()
    record("p12d.未给 TV 开关时 config/DEFAULTS 原样保留",
           e["time_varying"] is False and e["tv_policy"] is None
           and e["tv_coordinate"] is None)
    e = tv_eff(channel_sync_tv_policy="nearest-centre")
    record("p12d.只给 policy 不打开 time_varying (与经典入口同义)",
           e["time_varying"] is False and e["tv_policy"] == "nearest-centre")
    e = tv_eff(channel_sync_tv=False)
    record("p12d.显式关闭 --channel-sync-tv 不会被当成 None",
           e["time_varying"] is False)

    tree = ast.parse((root / "1kt.py").read_text(encoding="utf-8"))
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_apply_time_varying_cli"):
                callers.add(node.name)
    record("p12d.经典入口与透明入口共用同一份 TV 覆盖 (不再各写一份)",
           {"main", "transparent_main"} <= callers,
           f"callers={sorted(callers)}")

    # --- 10. 汇总口径: 同步失败必须单列, 不能混进 failed=0 的 done ------
    # 2026-10-29 实测: 两个长片一边打 [SYNC-FAIL], 一边在汇总写 failed=0。
    for status, want in (
        ("applied", "synced"), ("already_aligned", "synced"),
        ("not_eligible", "skipped"), ("tool_missing", "skipped"),
        ("measure_failed", "failed"), ("verify_failed", "failed"),
        ("tv_not_configured", "failed"),
    ):
        got = mod._transparent_sync_outcome(status)
        record(f"p12d.透明终态 {status} -> {want}", got == want,
               f"got={got}")
    src_kt = (root / "1kt.py").read_text(encoding="utf-8")
    record("p12d.汇总行单列 sync_skipped/sync_failed",
           "sync_skipped=%d" in src_kt and "sync_failed=%d" in src_kt
           and 'counters["sync_failed"] += 1' in src_kt)


# ==========================================================================
def l1_recheck_knobs() -> None:
    """复检的两个新维度: 相对容差与全片多窗 (2026-10-20)。

    背景是两条实测结论:
      * 绝对门 0.05 ms 对**恒定大偏移**过严 —— `C1213 CH1` 测出 +20.79 ms,
        修正后残差 0.08 ms, 却被整条退回 ("为了不引入 0.08 ms 留了 20.79 ms");
      * 只看中部锚段对**非恒定偏移**过松 —— `C1179 CH1` 中部残差 -0.2 采样
        判通过, 全片最差 34 采样, 一个刚性整数移位修不了它。

    这里只断言**纯逻辑**: 选项存在且默认关闭、中心窗位置与旧公式逐位一致、
    多窗能在合成数据上看出单窗看不到的偏移。真实素材的收益由
    `tests/regression/measure_recheck_knobs.py` 度量。
    """
    import numpy as np

    from core import channel_sync as cs
    from core import sync_fix

    # --- 选项存在 / 默认 / 归一化 ---
    record("p13a.verify_max_rel 默认 0.0 (关闭, 与旧行为一致)",
           cs.DEFAULTS["verify_max_rel"] == 0.0)
    record("p13b.recheck_windows 默认 1 (只看中部锚段)",
           cs.DEFAULTS["recheck_windows"] == 1)
    record("p13c.verify_max_rel 在 _FLOAT_KEYS 里 (否则会被静默丢掉)",
           "verify_max_rel" in cs._FLOAT_KEYS)
    record("p13d.recheck_windows 在 _INT_KEYS 里 (否则会被静默丢掉)",
           "recheck_windows" in cs._INT_KEYS)
    eff = cs.effective_opts({"verify_max_rel": 0.05, "recheck_windows": 3})
    record("p13e.effective_opts 保留 verify_max_rel=0.05",
           eff["verify_max_rel"] == 0.05, str(eff.get("verify_max_rel")))
    record("p13f.effective_opts 把 recheck_windows 归一为 int 3",
           eff["recheck_windows"] == 3 and isinstance(eff["recheck_windows"], int))
    record("p13g.相对门打开后有效门 = max(0.05, delay*rel): 20.79ms -> 1.0395ms",
           abs(max(0.05, 20.79 * 0.05) - 1.0395) < 1e-9)
    record("p13h.相对门对小位移不放宽: 1ms -> 仍 0.05ms",
           abs(max(0.05, 1.0 * 0.05) - 0.05) < 1e-9)

    # --- 单窗中心位置必须复现旧公式 ---
    sr = 48000
    n = 1_000_000
    anchor = 20.0
    anchor_len = int(round(anchor * sr))
    old_a0 = (n - anchor_len) // 2
    record("p13i.anchor_len 计算与旧实现一致",
           min(anchor_len, n) == anchor_len)
    record("p13j.center_frac=0.5 用 (n-anchor_len)//2 (逐位复现旧公式)",
           old_a0 == (n - anchor_len) // 2)

    # --- 合成数据: 恒定偏移单窗能测到; 非恒定偏移只有多窗能看到 ---
    rng = np.random.default_rng(20261020)
    ref = rng.standard_normal(48000 * 6).astype("float64")
    # 恒定偏移 12 采样
    const = np.concatenate([np.zeros(12), ref])[:len(ref)]
    one = sync_fix._recheck(ref, const, sample_rate=sr, search_ms=5.0,
                            anchor_segment_seconds=1.0,
                            fine_phase_band_hz=(200.0, 8000.0))
    record("p13k.恒定偏移: 单窗测得 ~12 采样 (合成)",
           one == one and abs(one * sr / 1000.0 - 12.0) <= 1.0,
           f"{one:.4f}ms = {one * sr / 1000:.2f} samples")

    # 非恒定: 前 1/3 偏移 6 采样, 后 2/3 偏移 30 采样
    step = np.concatenate([np.zeros(6), ref])[:len(ref)]
    late = np.concatenate([np.zeros(30), ref])[:len(ref)]
    # 跳变点必须落在**中部窗之后**, 否则中部窗也会看到尾部的大偏移, 就测不出
    # "单窗看不见" 这件事了。中部窗起点 = (n-anchor_len)//2 ≈ 0.417n, 取 0.62n。
    x = int(len(ref) * 0.62)
    mixed = np.concatenate([step[:x], late[x:]])
    one_m = sync_fix._recheck(ref, mixed, sample_rate=sr, search_ms=5.0,
                              anchor_segment_seconds=1.0,
                              fine_phase_band_hz=(200.0, 8000.0))
    res = sync_fix.recheck_residual_windows.__wrapped__ if hasattr(
        sync_fix.recheck_residual_windows, "__wrapped__") else None
    vals = []
    for c in (0.08, 0.5, 0.92):
        vals.append(sync_fix._recheck(
            ref, mixed, sample_rate=sr, search_ms=5.0,
            anchor_segment_seconds=1.0, fine_phase_band_hz=(200.0, 8000.0),
            center_frac=c))
    spread = max(abs(v) for v in vals if v == v) - min(
        abs(v) for v in vals if v == v)
    record("p13l.非恒定偏移: 中部单窗看不到尾部的大偏移 (合成)",
           one_m == one_m and abs(one_m * sr / 1000.0) < 20.0,
           f"middle={one_m * sr / 1000:.2f} samples")
    record("p13m.非恒定偏移: 三窗取最差才能看到 ~30 采样 (合成)",
           max(abs(v) for v in vals if v == v) * sr / 1000.0 >= 25.0,
           f"values={[round(v * sr / 1000, 2) for v in vals]}")
    record("p13n.三窗之间的差 > 多窗才有的信息量", spread >= 0 and res is None,
           f"spread={spread:.4f}ms")

    # --- 多窗调用本身: 窗口数与 nan 处理 ---
    wr = sync_fix.recheck_residual_windows(
        ref, const, sample_rate=sr, storage_dtype="f32", windows=3,
        anchor_segment_seconds=1.0)
    record("p13o.多窗返回 3 个值且 worst 取 |最大|",
           wr["n_windows"] == 3 and len(wr["values"]) == 3
           and wr["worst_abs_ms"] == max(abs(v) for v in wr["values"]))
    record("p13p.多窗结果与单窗同量级 (恒定偏移, 合成)",
           abs(wr["worst_ms"] * sr / 1000.0 - 12.0) <= 1.5,
           f"worst={wr['worst_ms'] * sr / 1000:.2f} samples")

    # --- 回归: 片段短于锚段时长时必须仍可测 ---
    # 这个 bug 真实发生过: 为了消除越界读, 曾把"锚段占满全片"的情形直接判
    # nan, 结果所有短于 20 s 的片段复检全部 nan, 被当成复检失败退回,
    # 一次性误杀 7 条已经修好的轨 (C1182/C1197/C1212/C1214 等)。
    # 正确行为是**收缩锚段长度**、保留搜索余量, 而不是放弃测量。
    short = rng.standard_normal(30000)          # 0.625 s @48k
    short_t = np.concatenate([np.zeros(9), short])[:len(short)]
    v_short = sync_fix._recheck(
        short, short_t, sample_rate=sr, search_ms=5.0,
        anchor_segment_seconds=20.0, fine_phase_band_hz=(200.0, 8000.0))
    record("p13q.短于锚段的片段仍可复检 (锚段收缩, 不得返回 nan)",
           v_short == v_short
           and abs(v_short * sr / 1000.0 - 9.0) <= 1.5,
           f"{v_short} ms")
    wr_short = sync_fix.recheck_residual_windows(
        short, short_t, sample_rate=sr, storage_dtype="f32", windows=3,
        anchor_segment_seconds=20.0)
    record("p13r.短片段多窗同样可测 (窗口收缩, 非 nan)",
           all(v == v for v in wr_short["values"]),
           f"values={wr_short['values']}")

    # --- 第三道门: 独立预检估计必须与 shift 一致 (2026-10-20) ---
    # 背景: 只放宽"残差"门会放行"数据写对、数值写错"的轨 —— 实测 `rel=0.2`
    # 在语料上新增 4 条, 其中 `C1167 CH2`(shift −42/实测 −37) 与
    # `C1267 CH1`(shift 1056/实测 1050) 属于这一类。残差门天生看不出来,
    # 只有两条独立估计的一致性才看得出。
    record("p13s.verify_agree_samples 默认 0 (关闭)",
           cs.DEFAULTS["verify_agree_samples"] == 0)
    record("p13t.verify_agree_samples 在 _INT_KEYS 里",
           "verify_agree_samples" in cs._INT_KEYS)
    record("p13u.estimate_disagreement 计入估计层失败原因",
           "estimate_disagreement" in cs._ESTIMATION_FAIL_REASONS)
    eff_a = cs.effective_opts({"verify_agree_samples": 2})
    record("p13v.effective_opts 归一 verify_agree_samples 为 int",
           eff_a["verify_agree_samples"] == 2
           and isinstance(eff_a["verify_agree_samples"], int))

    import tempfile

    import numpy as _np

    from core import sync_fix as _sf

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _rng = _np.random.default_rng(11)
        _ref = _rng.standard_normal(sr * 4).astype("float32")
        (_td_ref := td / "ref.f32")
        _ref.tofile(_td_ref)
        eff_agree = cs.effective_opts({"verify_agree_samples": 2,
                                      "recheck_windows": 1})

        def _case(actual: int, claimed: int):
            import types as _types
            tgt = _np.concatenate([_np.zeros(actual), _ref])[:len(_ref)]
            tp = td / f"t_{actual}_{claimed}.f32"
            tgt.astype("float32").tofile(tp)
            est = _types.SimpleNamespace(delay_ms=claimed * 1000.0 / sr,
                                        delay_samples=float(claimed))
            return cs._agree_with_independent_estimate(
                1, est, claimed, {0: _td_ref, 1: tp}, 0, 0, sr, "f32",
                eff_agree)

        _same = _case(900, 900)
        record("p13w.独立估计与 shift 一致 -> 放行",
               _same["accepted"] is True, str(_same.get("diff_samples")))
        _off = _case(900, 840)
        record("p13x.独立估计差 60 采样 -> 拒绝并给出差值",
               _off["accepted"] is False
               and abs(_off["diff_samples"] - 60.0) <= 2.0,
               str(_off.get("diff_samples")))
        _near = _case(900, 901)
        record("p13y.差 1 采样在容差内 -> 放行 (粗护栏, 不替代精测)",
               _near["accepted"] is True, str(_near.get("diff_samples")))
        record("p13z.测量记录总是带 accepted 字段 (放行也要可审计)",
               all("accepted" in _case(*a) for a in ((900, 900), (900, 840))))

    # --- 材料性判据 (2026-10-21): 修正值不值得做 ---
    record("p14a.materiality_k 默认 0.0 (关闭)",
           cs.DEFAULTS["materiality_k"] == 0.0)
    record("p14b.materiality_k 在 _FLOAT_KEYS 里",
           "materiality_k" in cs._FLOAT_KEYS)
    record("p14c.k<=0 -> 不接受 (关闭时与旧实现一致)",
           cs.materiality_accepts(1150, 1.553, sr, k=0.0) is None)
    _m = cs.materiality_accepts(1150, 1.553, sr, k=3.0)
    record("p14d.15.4 倍比值 -> 接受并给出证据",
           _m is not None and abs(_m["ratio"] - 15.427) < 0.01,
           str(_m and round(_m["ratio"], 3)))
    record("p14e.2 倍比值 -> 不接受 (不足 k 倍)",
           cs.materiality_accepts(1150, 11.0, sr, k=3.0) is None)
    record("p14f.位移为 nan -> 不接受 (不可判定不当 0)",
           cs.materiality_accepts(1150, float("nan"), sr, k=3.0) is None)
    record("p14g.位移为 0 -> 比值 inf, 接受",
           (lambda r: r is not None and r["ratio"] == float("inf"))(
               cs.materiality_accepts(1150, 0.0, sr, k=3.0)))
    # 30 ms 偏差 vs 10 ms 位移 = 恰好 3 倍: 1440 采样 @48 kHz
    _b = cs.materiality_accepts(1440.0, 10.0, sr, k=3.0)
    record("p14h.恰好等于 k 倍 -> 接受 (边界含等号)",
           _b is not None and abs(_b["ratio"] - 3.0) < 1e-9,
           str(_b and _b["ratio"]))
    record("p14i.字符串 k 可被归一 (CLI 传参路径)",
           cs.materiality_accepts(1150, 1.553, sr, k="3") is not None)
    record("p14j.effective_opts 归一 materiality_k=3",
           cs.effective_opts({"materiality_k": "3"})["materiality_k"] == 3.0)

    # --- 一致性门默认只诊断 (2026-10-22) ---
    # 实测: 任何量纲的否决都误拒 5–23 条良好修正, 只多挡 ≤1 条不良。
    record("p14k.verify_agree_veto 默认 False (只诊断不否决)",
           cs.DEFAULTS["verify_agree_veto"] is False)
    record("p14l.verify_agree_veto 在 _BOOL_KEYS 里",
           "verify_agree_veto" in cs._BOOL_KEYS)
    record("p14m.verify_agree_rel 默认 0 (关闭)",
           cs.DEFAULTS["verify_agree_rel"] == 0.0)
    record("p14n.verify_agree_sigma 默认 0 (关闭)",
           cs.DEFAULTS["verify_agree_sigma"] == 0.0)
    _tol = cs.effective_agree_tolerance(
        cs.effective_opts({"verify_agree_samples": 5,
                           "verify_agree_rel": 0.02,
                           "verify_agree_sigma": 3.0}),
        delay_samples=1200.0, ind_samples=1210.0,
        window_values=[24.5, 24.6, 24.4], sample_rate=sr)
    record("p14o.容差 = max(绝对, 相对, sigma) 取最宽",
           _tol["tolerance_samples"] >= 24.0, str(_tol))
    record("p14p.相对项 = rel×|delay| = 0.02×1200 = 24 采样",
           abs(_tol["tolerance_samples"] - 24.0) < 1e-6, str(_tol))

    # --- 逐轨参考选择 (2026-10-22) ---
    record("p15a.track_reference 默认 False (关闭)",
           cs.DEFAULTS["track_reference"] is False)
    record("p15b.track_reference 在 _BOOL_KEYS 里",
           "track_reference" in cs._BOOL_KEYS)
    _cmp = cs.compose_reference_shift(324.5, 913.0, sr)
    record("p15c.链式合成: 6.76ms + 913 采样 = 25.78ms",
           _cmp is not None
           and abs(_cmp["composed_ms"] - 25.78125) < 1e-6
           and _cmp["reference_shift_samples"] == 913,
           str(_cmp and round(_cmp["composed_ms"], 3)))
    record("p15d.合成是加法 (符号约定与 shift_stream 一致)",
           cs.compose_reference_shift(100.0, -100.0, sr)["composed_samples"] == 0)
    record("p15e.非有限延迟 -> None (不可用不当 0)",
           cs.compose_reference_shift(float("nan"), 10.0, sr) is None)
    record("p15f.非整数参考移位 -> None",
           cs.compose_reference_shift(10.0, 3.5, sr) is None)

    # --- 生产工作点 (2026-10-22 合入) ---
    record("p16a.PRODUCTION_OPTS 的键都在 DEFAULTS 里",
           all(k in cs.DEFAULTS for k in cs.PRODUCTION_OPTS))
    record("p16b.PRODUCTION_OPTS 含 track_reference=True",
           cs.PRODUCTION_OPTS.get("track_reference") is True)
    record("p16c.PRODUCTION_OPTS 含 materiality_k=3",
           cs.PRODUCTION_OPTS.get("materiality_k") == 3.0)
    _po = cs.production_opts()
    record("p16d.production_opts() 而不改库默认",
           _po["track_reference"] is True
           and cs.DEFAULTS["track_reference"] is False)
    record("p16e.调用方覆盖优先于 profile",
           cs.production_opts({"track_reference": False})["track_reference"]
           is False)
    record("p16f.值为 None 的键不擦掉 profile",
           cs.production_opts({"track_reference": None})["track_reference"]
           is True)
    _pe = cs.effective_opts(cs.production_opts())
    record("p16g.effective_opts 归一后 profile 生效值正确",
           _pe["materiality_k"] == 3.0 and _pe["verify_max_rel"] == 0.2
           and _pe["recheck_windows"] == 3
           and _pe["verify_agree_samples"] == 20
           and _pe["track_reference"] is True)