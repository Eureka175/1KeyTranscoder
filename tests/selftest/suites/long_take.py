#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长镜头窗级漂移测量 (measure_window_delays) 纯算法单元测试。

被测口径来自研究脚本 (tests/regression/long_align_tools/track_drift.py,
60 s 窗 / 10 s 跳 + 12 kHz 粗扫 + 48 kHz 精化 + 连续性约束) 的生产移植;
本套件只用**合成信号**, 不依赖 ffmpeg / 真实素材, 覆盖:

  a) 常量整数延迟 +123 / −200 的精确恢复;
  b) 线性漂移 (120 s 内 0→240 采样) 的逐窗误差;
  c) 反相目标的极性 (-1) 与滞后正确性;
  d) 独立噪声不得给出假 lag (弱峰门);
  e) 连续性: 窗内存在更强的错误分支峰时仍沿真分支;
  f) 比窗短的输入 / 空输入不抛未捕获异常;
  附加: 分块抽取与整文件 decimate 一致、coarse 缓存复用、路径输入
  (有界窗口读取) 与 ndarray 结果一致、两次运行逐位一致。

数据约定 (与 core.sync_estimate 一致): delay > 0 = 目标晚到,
out[n] = in[n + delay]; 合成信号注入 +123 -> 测得 123.0。
"""

from __future__ import annotations

from ..paths import record
from ..paths import section


def l1_long_take_window_delays() -> None:
    """L1: 窗级连续性漂移测量 (纯算法, 合成信号) 的 14 条断言。"""
    section("L1 长镜头窗级漂移测量 (measure_window_delays)")
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:                 # 与其它套件一致: 缺依赖跳过
        record("long_take.numpy/scipy 可用", False,
               f"{exc} — 本组跳过 (measure_window_delays 需 numpy/scipy)")
        return
    record("long_take.numpy/scipy 可用", True)

    from core import sync_estimate

    SR = 48_000
    # 短几何 (秒级跑完): 1 s 窗 / 0.5 s 跳, 4 kHz 粗扫, 0.25 s 精化段。
    WIN_S = 1.0
    HOP_S = 0.5
    COARSE = 4000
    SEG_S = 0.25

    def lowpass_noise(n: int, seed: int) -> "np.ndarray":
        """限带噪声 (确定性; 5.9 kHz 低通), 幅度 0.5。"""
        rng = np.random.default_rng(seed)
        sos = signal.butter(4, 5900.0, btype="low", fs=SR, output="sos")
        x = signal.sosfilt(sos, rng.standard_normal(n))
        return (x / np.max(np.abs(x)) * 0.5).astype(np.float32)

    def delay_int(x: "np.ndarray", d: int) -> "np.ndarray":
        """整数前移/后移: d > 0 = 内容后移 d 采样 (目标晚到), 越界补零。"""
        y = np.zeros_like(x)
        if d >= 0:
            if d < x.size:
                y[d:] = x[: x.size - d]
        else:
            m = -d
            if m < x.size:
                y[: x.size - m] = x[m:]
        return y

    def measure(ref, tgt, **kw):
        base = dict(
            sample_rate=SR, window_seconds=WIN_S, hop_seconds=HOP_S,
            coarse_rate=COARSE, refine_segment_seconds=SEG_S,
            initial_seconds=2.0,
        )
        base.update(kw)
        return sync_estimate.measure_window_delays(ref, tgt, **base)

    # ---- 0) 分块抽取缓存 vs 整文件 decimate (分块余量有效性) ----------
    rng = np.random.default_rng(11)
    src = (rng.standard_normal(2 * SR) * 0.3).astype(np.float32)
    whole = signal.decimate(src.astype(np.float64), 4, ftype="fir",
                            zero_phase=True)
    part = sync_estimate.coarse_decimate(
        src, sample_rate=SR, coarse_rate=SR // 4, chunk_seconds=0.25)
    diff = float(np.max(np.abs(part.astype(np.float64) - whole)))
    record("long_take.分块抽取 == 整文件 decimate",
           part.shape[0] == whole.shape[0] and diff < 1e-6,
           f"len={part.shape[0]}/{whole.shape[0]} max|diff|={diff:.2e}")

    # ---- a) 常量整数延迟: +123 / −200 --------------------------------
    base = lowpass_noise(6 * SR, seed=1)
    res_p = measure(base, delay_int(base, 123))
    delays_p = [w["delay_samples"] for w in res_p["windows"]]
    record("long_take.常量 +123 采样精确恢复",
           res_p["n_windows"] > 0
           and all(d == 123.0 for d in delays_p)
           and res_p["rejected_windows"] == 0,
           f"n={len(delays_p)} delays={sorted(set(delays_p))}")
    res_m = measure(base, delay_int(base, -200))
    delays_m = [w["delay_samples"] for w in res_m["windows"]]
    record("long_take.常量 -200 采样精确恢复",
           res_m["n_windows"] > 0
           and all(d == -200.0 for d in delays_m)
           and res_m["rejected_windows"] == 0,
           f"n={len(delays_m)} delays={sorted(set(delays_m))}")

    # ---- 5) 返回结构 / 几何 / 首窗 ------------------------------------
    keys_ok = all(k in res_p for k in (
        "sample_rate", "window_samples", "hop_samples", "n_windows",
        "initial", "windows", "decimated_rate", "rejected_windows"))
    win_ok = all(k in res_p["windows"][0] for k in (
        "start_sample", "center_sample", "delay_samples", "confidence",
        "valid", "coarse_cc", "polarity", "search_radius_samples"))
    geom_ok = (res_p["window_samples"] == int(WIN_S * SR)
               and res_p["hop_samples"] == int(HOP_S * SR)
               and len(res_p["windows"]) == res_p["n_windows"]
               and res_p["initial"]["valid"]
               and res_p["initial"]["lag"] == 123)
    record("long_take.返回结构/几何/首窗", keys_ok and win_ok and geom_ok,
           f"keys={keys_ok} row={win_ok} geom={geom_ok} "
           f"n={res_p['n_windows']} initial={res_p['initial']['lag']}")

    # ---- 6) 传入 coarse 缓存必须得到逐位相同结果 ----------------------
    cache = sync_estimate.coarse_decimate(
        base, sample_rate=SR, coarse_rate=COARSE)
    tgt_base = delay_int(base, 123)
    cache_tgt = sync_estimate.coarse_decimate(
        tgt_base, sample_rate=SR, coarse_rate=COARSE)
    res_c = measure(base, tgt_base, ref_decimated=cache,
                    tgt_decimated=cache_tgt)
    record("long_take.复用 coarse 缓存结果一致",
           res_c["windows"] == res_p["windows"],
           f"cached={len(res_c['windows'])} rows")

    # ---- 7) raw 路径输入 (有界窗口读取) 与 ndarray 输入结果一致 --------
    import shutil
    import tempfile
    from pathlib import Path

    tmpdir = Path(tempfile.mkdtemp(prefix="long_take_win_"))
    try:
        ref_p = tmpdir / "ref.f32"
        tgt_p = tmpdir / "tgt.f32"
        base.astype(np.float32).tofile(ref_p)
        tgt_base.astype(np.float32).tofile(tgt_p)
        res_path = measure(ref_p, tgt_p, storage_dtype="f32")
        record("long_take.路径输入(有界读取)与 ndarray 一致",
               res_path["windows"] == res_p["windows"]
               and res_path["initial"] == res_p["initial"],
               f"n={res_path['n_windows']} rows")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- b) 线性漂移: 120 s 内 0 -> 240 采样 --------------------------
    n = 120 * SR
    drift_base = lowpass_noise(n, seed=2)
    k = np.arange(n, dtype=np.float64)
    d_ramp = 240.0 * k / (n - 1)
    drift_tgt = np.interp(
        k - d_ramp, k, drift_base.astype(np.float64),
        left=0.0, right=0.0,
    ).astype(np.float32)
    res_d = measure(drift_base, drift_tgt)
    errs = []
    for w in res_d["windows"]:
        if not w["valid"]:
            continue
        expect = 240.0 * w["center_sample"] / (n - 1)
        errs.append(abs(w["delay_samples"] - expect))
    record("long_take.线性漂移 0->240 误差<=2 采样",
           res_d["n_windows"] >= 200 and len(errs) == res_d["n_windows"]
           and max(errs) <= 2.0,
           f"n_win={res_d['n_windows']} valid={len(errs)} "
           f"max|err|={max(errs):.2f} median={np.median(errs):.2f}")

    # ---- c) 反相目标: 极性 -1, 滞后仍正确 ----------------------------
    inv = (-delay_int(base, 77)).astype(np.float32)
    res_i = measure(base, inv)
    pol_ok = all(w["polarity"] == -1 for w in res_i["windows"])
    lag_ok = all(w["delay_samples"] == 77.0 for w in res_i["windows"])
    record("long_take.反相: polarity=-1 且滞后 77",
           res_i["rejected_windows"] == 0 and pol_ok and lag_ok,
           f"rejected={res_i['rejected_windows']} pol_ok={pol_ok} "
           f"lag_ok={lag_ok}")

    # ---- d) 独立噪声: 不得给出假 lag ---------------------------------
    indep = np.random.default_rng(999).standard_normal(6 * SR).astype(
        np.float32)
    res_n = measure(base, indep)
    record("long_take.独立噪声无可信窗",
           res_n["rejected_windows"] == res_n["n_windows"]
           and all(w["delay_samples"] is None for w in res_n["windows"])
           and not res_n["initial"]["valid"],
           f"valid={res_n['n_windows'] - res_n['rejected_windows']}/"
           f"{res_n['n_windows']} initial_valid="
           f"{res_n['initial']['valid']}")

    # ---- e) 连续性: 更强错误分支峰在搜索半径外 ----------------------
    cont_base = lowpass_noise(6 * SR, seed=3)
    kk = np.arange(6 * SR, dtype=np.float64)
    d_true = 100.0 + 24.0 * kk / (6 * SR - 1)
    cont_tgt = np.interp(
        kk - d_true, kk, cont_base.astype(np.float64), left=0.0, right=0.0,
    )
    a0, a1 = 96000, 144000                      # 窗 4 = 注入区 (1 s)
    taper = np.zeros(6 * SR)
    taper[a0:a1] = np.hanning(a1 - a0)
    wrong = 270                                  # 真 lag ≈ 110, 差 ≈ 160 > 半径
    cont_tgt = (cont_tgt
                + 2.0 * taper * delay_int(cont_base, wrong).astype(
                    np.float64)).astype(np.float32)
    res_cb = measure(cont_base, cont_tgt)
    rows = res_cb["windows"]
    row4 = next(r for r in rows if r["start_sample"] == a0)
    expect4 = 100.0 + 24.0 * row4["center_sample"] / (6 * SR - 1)
    other = all(
        abs(r["delay_samples"] - (100.0 + 24.0 * r["center_sample"]
                                  / (6 * SR - 1))) <= 3.0
        for r in rows if r["valid"]
    )
    record("long_take.连续性沿真分支 (错误峰更强)",
           row4["valid"] and abs(row4["delay_samples"] - expect4) <= 3.0
           and other
           and abs(wrong - row4["delay_samples"]) > row4[
               "search_radius_samples"],
           f"win4 got={row4['delay_samples']} expect={expect4:.1f} "
           f"radius={row4['search_radius_samples']}")

    # ---- f) 边界: 比窗短 / 空输入 ------------------------------------
    short = base[:10000]
    res_s = measure(short, short)
    record("long_take.短于窗的输入 n_windows=0",
           res_s["n_windows"] == 0 and res_s["windows"] == [],
           f"n_win={res_s['n_windows']}")
    res_e = measure(np.zeros(0, dtype=np.float32),
                    np.zeros(0, dtype=np.float32))
    record("long_take.空输入 n_windows=0",
           res_e["n_windows"] == 0 and res_e["windows"] == [],
           f"n_win={res_e['n_windows']}")

    # ---- 确定性: 同输入两次运行逐位一致 ------------------------------
    res_again = measure(base, delay_int(base, 123))
    record("long_take.两次运行逐位一致",
           res_again["windows"] == res_p["windows"]
           and res_again["initial"] == res_p["initial"],
           f"n={len(res_again['windows'])}")


def l1_long_take_reference_algorithm() -> None:
    """L1: 参考 round-3 算法 (drift_rebuild 稳定化 + 分数插值施加)。

    a) 锚点稳定化: 7 锚点中值滤波剔除孤立跳变; 30 采样/锚点限幅对真实
       漂移自适应放大 (合成 0.5 采样/s 线性漂移不被压成阶梯);
       `curve_stabilize=False` 保留 round-2 的 apply_tv 行为 (120 限幅,
       无中值滤波) —— 用对照断言证明两条路径确实不同;
    b) `reference_interp_write` 与参考 `fine_delay.interp_write` 同义:
       常量整数移位逐样本精确、分数移位 = 全局线性插值、越界补零、
       返回 executed/n_samples。
    """
    section("L1 长镜头参考算法 (drift_rebuild 稳定化 + 分数插值施加)")
    try:
        import numpy as np
    except ImportError as exc:                 # 与其它套件一致: 缺依赖跳过
        record("long_take.ref.numpy 可用", False,
               f"{exc} — 本组跳过 (参考算法测试需 numpy)")
        return
    record("long_take.ref.numpy 可用", True)

    from core import long_take

    SR = 48_000
    BIN = int(long_take.PROFILE["curve_anchor_seconds"] * SR)

    def synth_windows(truth, duration_s: int, hop_s: float = 30.0,
                      jump: float = 0.0, jump_bins: tuple = ()) -> tuple:
        """合成窗记录: truth(样本位置)->延迟; jump_bins 内的窗加孤立跳变。"""
        n = int(duration_s * SR)
        hop = int(hop_s * SR)
        rows = []
        for s in range(0, max(1, n - 60 * SR), hop):
            b = s // BIN
            val = float(truth(s)) + (jump if b in jump_bins else 0.0)
            rows.append({
                "start_sample": float(s),
                "center_sample": float(s + 30 * SR),
                "delay_samples": val,
                "confidence": 0.8,
                "coarse_cc": 0.8,
                "valid": True,
            })
        return rows, n

    # ---- a1) 孤立跳变: 稳定化后曲线平坦 ------------------------------
    rows, n = synth_windows(lambda s: 1000.0, 1600, jump=130.0,
                            jump_bins=(7,))
    curve = long_take.build_curve_from_windows(
        rows, n_samples=n, sample_rate=SR)
    dev = np.max(np.abs(np.asarray(curve["grid_s"]) - 1000.0))
    record("long_take.ref 中值滤波剔除孤立 130 采样跳变",
           curve["ok"] and dev <= 1.0 and curve.get("stabilized") is True,
           f"ok={curve.get('ok')} max|dev|={dev:.2f}")
    legacy = long_take.build_curve_from_windows(
        rows, n_samples=n, sample_rate=SR,
        profile={**long_take.PROFILE,
                 "curve_stabilize": False,
                 "curve_jump_limit_samples": 120.0})
    dev_legacy = np.max(np.abs(np.asarray(legacy["grid_s"]) - 1000.0))
    record("long_take.ref 对照: round-2 口径保留跳变 (≥100)",
           dev_legacy >= 100.0,
           f"legacy max|dev|={dev_legacy:.1f}")

    # ---- a2) 真实线性漂移: 自适应限幅不压成阶梯 ----------------------
    def drift_truth(s: float) -> float:
        return 1000.0 + 0.5 * s / SR

    rows_d, n_d = synth_windows(drift_truth, 1600)
    curve_d = long_take.build_curve_from_windows(
        rows_d, n_samples=n_d, sample_rate=SR)
    grid = np.asarray(curve_d["grid_t"])
    truth = 1000.0 + 0.5 * grid / SR + 15.0     # 锚点取箱内窗中位 = +30 s 处
    # 末锚点之后是 hold 边界 (参考口径), 不属于漂移跟踪区 -> 只比较锚点覆盖区
    last_anchor = float(curve_d["anchors"][-1][0]) * SR
    mask = grid <= last_anchor
    err = np.max(np.abs((np.asarray(curve_d["grid_s"]) - truth)[mask]))
    record("long_take.ref 自适应限幅跟住 0.5 采样/s 真实漂移",
           curve_d["ok"] and err <= 2.0,
           f"ok={curve_d.get('ok')} max|err|={err:.2f}")
    fixed = long_take.build_curve_from_windows(
        rows_d, n_samples=n_d, sample_rate=SR,
        profile={**long_take.PROFILE, "curve_jump_clamp_adaptive": False})
    err_fixed = np.max(np.abs(
        (np.asarray(fixed["grid_s"]) - truth)[mask]))
    record("long_take.ref 对照: 固定 30 限幅会压出阶梯 (误差>50)",
           err_fixed > 50.0,
           f"fixed max|err|={err_fixed:.1f}")

    # ---- b) reference_interp_write 语义 ------------------------------
    import tempfile
    from pathlib import Path

    tmpdir = Path(tempfile.mkdtemp(prefix="long_take_ref_"))
    try:
        n_ref = 4 * SR
        rng = np.random.default_rng(7)
        src = (rng.standard_normal(n_ref) * 0.3).astype(np.float32)
        src_p = tmpdir / "src.f32"
        src.tofile(src_p)

        def curve_const(value: float) -> dict:
            return {"grid_t": [0.0, float(n_ref)], "grid_s": [value, value]}

        def run(value: float, name: str) -> tuple:
            dst = tmpdir / f"{name}.f32"
            stats = long_take.reference_interp_write(
                src_p, dst, curve_const(value), sample_rate=SR)
            out = np.fromfile(dst, dtype="<f4")
            return stats, out

        stats5, out5 = run(5.0, "int5")
        expect5 = np.zeros(n_ref, dtype=np.float32)
        expect5[: n_ref - 5] = src[5:]
        exact5 = int(np.max(np.abs(out5 - expect5)) == 0) if out5.size else False
        record("long_take.ref 整数移位 +5 逐样本精确/越界补零",
               bool(stats5.get("executed")) and out5.size == n_ref
               and exact5,
               f"executed={stats5.get('executed')} n={out5.size} "
               f"exact={exact5}")

        k = np.arange(n_ref, dtype=np.float64)
        frac = 2.5
        expect_f = np.interp(
            k + frac, k, src.astype(np.float64),
            left=0.0, right=0.0)
        stats_f, out_f = run(frac, "frac25")
        max_df = float(np.max(np.abs(out_f.astype(np.float64) - expect_f)))
        record("long_take.ref 分数移位 +2.5 = 全局线性插值",
               bool(stats_f.get("executed")) and max_df <= 1e-6,
               f"max|diff|={max_df:.2e}")

        stats_n, out_n = run(-5.0, "neg5")
        zero_ok = bool(np.all(out_n[:5] == 0.0))
        rest_ok = bool(np.all(out_n[5:] == src[: n_ref - 5]))
        record("long_take.ref 负移位 -5: 开头补零且其余精确",
               bool(stats_n.get("executed")) and zero_ok and rest_ok,
               f"zero_ok={zero_ok} rest_ok={rest_ok}")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
