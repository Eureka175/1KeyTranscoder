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

    # --- 布局判定: 2ch/1ch 默认不做对齐 ---
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 2, "sample_rate": "48000"}]
    )
    record("channel_sync.2ch 默认不对齐", not ok and "2ch" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s16le",
          "channels": 1, "sample_rate": "48000"}]
    )
    record("channel_sync.1ch 默认不对齐", not ok and "1ch" in why, why)
    ok, why = eligible_audio(
        [{"codec_type": "audio", "codec_name": "aac", "channels": 1,
          "sample_rate": "48000"}] * 4
    )
    record("channel_sync.非 PCM 不对齐", not ok, why)
    ok, _ = eligible_audio(
        [{"codec_type": "audio", "codec_name": "pcm_s24le",
          "channels": 1, "sample_rate": "48000"}] * 4
    )
    record("channel_sync.4xmono PCM 对齐", ok)
    record("channel_sync.opts 默认值",
           effective_opts(None)["anchor_candidates"] == [2, 3, 0, 1]
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
    record("p1.锚点候选回退 CH3>CH4>CH1>CH2",
           DEFAULTS["anchor_candidates"] == [2, 3, 0, 1])
    record("p1.transparent 默认关",
           DEFAULTS["channel_sync_transparent"] is False)
    o = effective_opts({"max_lag_seconds": 0.5})
    record("p1.旧键 max_lag_seconds 映射 search_window_ms",
           o["search_window_ms"] == 500.0)
    o2 = effective_opts({"reference_stream": 0})
    record("p1.废弃键 reference_stream 忽略",
           o2["anchor_candidates"] == [2, 3, 0, 1]
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
