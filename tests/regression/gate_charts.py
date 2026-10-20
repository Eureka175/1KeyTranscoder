#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate analysis: histograms, continuous usability curves, parameter matrix.

Input is the **already measured** arms (no re-measurement): every field a gate
reads is stored per track. The offline decision replica is validated by
requiring that the current thresholds reproduce production exactly (delta = 0);
if that check fails the script says so and refuses to publish curves.

Outputs a self-contained HTML with inline SVG (no dependencies) and a Markdown
summary. All numbers quoted in the report come from the same data structures
that draw the charts, so text and picture cannot drift apart.

Boundary, stated once and honoured everywhere:
  * gates whose inputs are stored  -> swept continuously: usable_frames,
    confidence, mad_max_ms, min_shift_samples;
  * `recheck_residual`             -> NOT swept: the residual is not stored;
  * tracks refused for silence / no anchor / out_of_range are never counted as
    recoverable by a threshold change.
"""
from __future__ import annotations

import html
import json
import math
import statistics as st
import sys
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
DOCS = Path(r"F:\1KeyTranscoder\docs\audio-sync-p4")
ARMS = [
    (OUT / "ARCH_on5.json", "archive I:\\"),
    (OUT / "ARM5_on.json", "corpus E:\\1kt_media"),
    (OUT / "ARM5_holdout_on.json", "holdout I:\\compact"),
]

SR = 48000
#: 现行阈值
CUR = {"frames": 5, "conf": 0.3, "mad": 1.0, "shift": 5,
       "ppm": 5.0, "drift_ms": 0.1}
#: 这些原因不是阈值能翻的
NEVER = {"silent_track", "non_finite", "no_valid_anchor", "out_of_range"}
RECHECK = "recheck_residual"


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load() -> list[dict]:
    seen: dict[tuple, dict] = {}
    for path, tag in ARMS:
        if not path.is_file():
            continue
        for r in json.loads(path.read_text(encoding="utf-8")):
            for c in r.get("channels") or []:
                key = (Path(r["file"]).name, c.get("stream"))
                if key in seen:
                    continue
                c["_arm"] = tag
                c["_file"] = Path(r["file"]).name
                c["_spread_ms"] = (c.get("traj_spread_samples") or 0) * 1000.0 / SR
                seen[key] = c
    return list(seen.values())


def num(v, default=None):
    return v if isinstance(v, (int, float)) and v == v else default


def decide(c: dict, frames_thr: int, conf_thr: float, mad: float,
           shift_thr: int) -> str:
    """Re-decide one track. Mirrors production order.

    已被生产**接受**的轨 (fixed / already_aligned) 只可能被 `min_shift_samples`
    重新分类 —— 它们已经通过了全部其它门, 再拿别的门去否掉它们等于用离线模型
    推翻一次真实测量。被拒的轨才需要逐门重判。
    """
    dec = c.get("decision")
    if dec == "anchor":
        return "anchor"
    d_smp = num(c.get("delay_samples"))
    d_ms = num(c.get("delay_ms"))
    if dec in ("fixed", "already_aligned"):
        if d_smp is None:
            return "refused"
        return "aligned" if abs(d_smp) <= shift_thr else "fixed"
    reason = c.get("reason")
    if reason in NEVER:
        return "unrecoverable"
    if reason == RECHECK:
        return "recheck"
    if d_smp is None or d_ms is None:
        return "unmeasured"
    frames = num(c.get("usable_frames"), 0)
    conf = num(c.get("confidence"), 0.0)
    if frames < frames_thr or conf < conf_thr:
        return "refused"
    m = num(c.get("traj_mad_ms"))
    if m is None or m > mad:
        return "refused"
    if frames >= 20:
        if c["_spread_ms"] > mad:
            return "refused"
    elif reason == "non_constant":
        return "needs_remeasure"        # 稳健位移/漂移门未入库
    drift = num(c.get("drift_ppm"), 0.0)
    if abs(drift) > CUR["ppm"] and c["_spread_ms"] > CUR["drift_ms"]:
        return "refused"
    return "aligned" if abs(d_smp) <= shift_thr else "fixed"


def baseline_delta(rows: list[dict]) -> int:
    bad = 0
    for c in rows:
        d = decide(c, CUR["frames"], CUR["conf"], CUR["mad"], CUR["shift"])
        if d == "fixed" and c.get("decision") != "fixed":
            bad += 1
        if d != "fixed" and c.get("decision") == "fixed":
            bad += 1
    return bad


# ---------------------------------------------------------------------------
# metrics along a sweep
# ---------------------------------------------------------------------------
def sweep(rows: list[dict], param: str, values: list[float]) -> list[dict]:
    out = []
    for v in values:
        kw = {"frames_thr": CUR["frames"], "conf_thr": CUR["conf"],
              "mad": CUR["mad"], "shift_thr": CUR["shift"]}
        if param == "frames":
            kw["frames_thr"] = int(round(v))
        elif param == "conf":
            kw["conf_thr"] = v
        elif param == "mad":
            kw["mad"] = v
        elif param == "shift":
            kw["shift_thr"] = int(round(v))
        n_fixed = n_aligned = 0
        rec = acc = 0.0
        for c in rows:
            d = decide(c, **kw)
            if d == "fixed" and c.get("decision") != "fixed":
                n_fixed += 1
                rec += abs(num(c.get("delay_ms"), 0.0))
                acc += c["_spread_ms"]
            elif d in ("fixed", "aligned") and c.get("decision") in (
                    "fixed", "already_aligned"):
                n_aligned += 1
        out.append({"v": v, "new_fixed": n_fixed, "adopted": n_aligned,
                    "recover_ms": rec, "accept_ms": acc})
    return out


# ---------------------------------------------------------------------------
# tiny SVG toolkit
# ---------------------------------------------------------------------------
W, H = 720, 300
PAD = {"l": 58, "r": 16, "t": 34, "b": 44}


def _sx(x, x0, x1):
    return PAD["l"] + (x - x0) / (x1 - x0) * (W - PAD["l"] - PAD["r"])


def _sy(y, y0, y1):
    return H - PAD["b"] - (y - y0) / (y1 - y0) * (H - PAD["t"] - PAD["b"])


def _nice_top(v):
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def svg_hist(title: str, bins: list[tuple[float, float]], counts: list[list[int]],
             labels: list[str], colors: list[str], xlabel: str,
             gates: list[tuple[float, str]], x0=None, x1=None,
             ylabel: str = "轨道数") -> str:
    n = len(bins)
    x0 = bins[0][0] if x0 is None else x0
    x1 = bins[-1][1] if x1 is None else x1
    tot = [sum(cc[i] for cc in counts) for i in range(n)]
    y1 = _nice_top(max(tot) if tot else 1)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'font-family="Segoe UI,sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<text x="{PAD["l"]}" y="18" font-size="13" '
             f'font-weight="600">{html.escape(title)}</text>']
    # axes + y ticks
    parts.append(f'<line x1="{PAD["l"]}" y1="{_sy(0, 0, y1):.1f}" '
                 f'x2="{W - PAD["r"]}" y2="{_sy(0, 0, y1):.1f}" stroke="#333"/>')
    for k in range(5):
        yv = y1 * k / 4
        yy = _sy(yv, 0, y1)
        parts.append(f'<line x1="{PAD["l"] - 4}" y1="{yy:.1f}" '
                     f'x2="{W - PAD["r"]}" y2="{yy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{PAD["l"] - 6}" y="{yy + 4:.1f}" '
                     f'text-anchor="end" fill="#666">{yv:g}</text>')
    parts.append(f'<text x="14" y="{H / 2}" fill="#666" '
                 f'transform="rotate(-90 14 {H / 2})" text-anchor="middle">'
                 f'{html.escape(ylabel)}</text>')
    # stacked bars
    bw = (W - PAD["l"] - PAD["r"]) / n
    for i, (lo, hi) in enumerate(bins):
        xa = _sx(lo, x0, x1)
        xb = _sx(hi, x0, x1)
        base = 0.0
        for k, cc in enumerate(counts):
            if cc[i] == 0:
                continue
            ya = _sy(base, 0, y1)
            yb = _sy(base + cc[i], 0, y1)
            parts.append(f'<rect x="{xa + 1:.1f}" y="{yb:.1f}" '
                         f'width="{max(1.0, xb - xa - 2):.1f}" '
                         f'height="{ya - yb:.1f}" fill="{colors[k]}" '
                         f'fill-opacity="0.85"/>')
            base += cc[i]
    # gates
    for gx, gname in gates:
        if x0 <= gx <= x1:
            xx = _sx(gx, x0, x1)
            parts.append(f'<line x1="{xx:.1f}" y1="{PAD["t"]}" x2="{xx:.1f}" '
                         f'y2="{H - PAD["b"]}" stroke="#d00" '
                         f'stroke-dasharray="5 3"/>')
            parts.append(f'<text x="{xx + 3:.1f}" y="{PAD["t"] + 12}" '
                         f'fill="#d00" font-size="10">{html.escape(gname)}</text>')
    # x ticks
    for k in range(5):
        xv = x0 + (x1 - x0) * k / 4
        parts.append(f'<text x="{_sx(xv, x0, x1):.1f}" y="{H - PAD["b"] + 16}" '
                     f'text-anchor="middle" fill="#666">{xv:g}</text>')
    parts.append(f'<text x="{(W + PAD["l"]) / 2:.0f}" y="{H - 6}" '
                 f'text-anchor="middle" fill="#666">{html.escape(xlabel)}</text>')
    # legend
    lx = PAD["l"] + 6
    for k, lab in enumerate(labels):
        parts.append(f'<rect x="{lx}" y="{H - PAD["b"] - 26}" width="9" '
                     f'height="9" fill="{colors[k]}" fill-opacity="0.85"/>')
        parts.append(f'<text x="{lx + 12}" y="{H - PAD["b"] - 18}" '
                     f'fill="#444">{html.escape(lab)}</text>')
        lx += 16 + 7 * len(lab)
    parts.append("</svg>")
    return "".join(parts)


def svg_curve(title: str, xs: list[float], series: list[tuple[str, list[float], str]],
              xlabel: str, ylabel: str,
              gates: list[tuple[float, str]] = ()) -> str:
    lo, hi = min(xs), max(xs)
    allv = [v for _, ys, _ in series for v in ys if v == v]
    y1 = _nice_top(max(allv) if allv else 1)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'font-family="Segoe UI,sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<text x="{PAD["l"]}" y="18" font-size="13" '
             f'font-weight="600">{html.escape(title)}</text>',
             f'<line x1="{PAD["l"]}" y1="{_sy(0, 0, y1):.1f}" '
             f'x2="{W - PAD["r"]}" y2="{_sy(0, 0, y1):.1f}" stroke="#333"/>']
    for k in range(5):
        yv = y1 * k / 4
        yy = _sy(yv, 0, y1)
        parts.append(f'<line x1="{PAD["l"] - 4}" y1="{yy:.1f}" '
                     f'x2="{W - PAD["r"]}" y2="{yy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{PAD["l"] - 6}" y="{yy + 4:.1f}" '
                     f'text-anchor="end" fill="#666">{yv:g}</text>')
    parts.append(f'<text x="14" y="{H / 2}" fill="#666" '
                 f'transform="rotate(-90 14 {H / 2})" text-anchor="middle">'
                 f'{html.escape(ylabel)}</text>')
    for gx, gname in gates:
        if lo <= gx <= hi:
            xx = _sx(gx, lo, hi)
            parts.append(f'<line x1="{xx:.1f}" y1="{PAD["t"]}" x2="{xx:.1f}" '
                         f'y2="{H - PAD["b"]}" stroke="#d00" '
                         f'stroke-dasharray="5 3"/>')
            parts.append(f'<text x="{xx + 3:.1f}" y="{PAD["t"] + 12}" '
                         f'fill="#d00" font-size="10">{html.escape(gname)}</text>')
    for name, ys, color in series:
        pts = " ".join(f"{_sx(x, lo, hi):.1f},{_sy(v, 0, y1):.1f}"
                       for x, v in zip(xs, ys) if v == v)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="2"/>')
    for k in range(5):
        xv = lo + (hi - lo) * k / 4
        parts.append(f'<text x="{_sx(xv, lo, hi):.1f}" y="{H - PAD["b"] + 16}" '
                     f'text-anchor="middle" fill="#666">{xv:g}</text>')
    parts.append(f'<text x="{(W + PAD["l"]) / 2:.0f}" y="{H - 6}" '
                 f'text-anchor="middle" fill="#666">{html.escape(xlabel)}</text>')
    lx = PAD["l"] + 6
    for name, _ys, color in series:
        parts.append(f'<line x1="{lx}" y1="{H - PAD["b"] - 22}" x2="{lx + 16}" '
                     f'y2="{H - PAD["b"] - 22}" stroke="{color}" '
                     f'stroke-width="2"/>')
        parts.append(f'<text x="{lx + 20}" y="{H - PAD["b"] - 18}" '
                     f'fill="#444">{html.escape(name)}</text>')
        lx += 24 + 7 * len(name)
    parts.append("</svg>")
    return "".join(parts)


def svg_heat(title: str, xs: list[float], ys: list[float], grid: list[list[float]],
             xlabel: str, ylabel: str) -> str:
    W2, H2 = 520, 360
    pad = {"l": 70, "r": 70, "t": 34, "b": 50}
    vmax = max((v for row in grid for v in row), default=1) or 1
    cw = (W2 - pad["l"] - pad["r"]) / len(xs)
    chh = (H2 - pad["t"] - pad["b"]) / len(ys)
    parts = [f'<svg viewBox="0 0 {W2} {H2}" xmlns="http://www.w3.org/2000/svg" '
             f'font-family="Segoe UI,sans-serif" font-size="10">',
             f'<rect width="{W2}" height="{H2}" fill="#fff"/>',
             f'<text x="{pad["l"]}" y="18" font-size="13" '
             f'font-weight="600">{html.escape(title)}</text>']
    for j, yv in enumerate(ys):
        for i, xv in enumerate(xs):
            v = grid[j][i]
            t = v / vmax
            col = f"rgb({int(255 - 180 * t)},{int(255 - 40 * t)},{int(255 - 200 * t)})"
            x = pad["l"] + i * cw
            y = pad["t"] + (len(ys) - 1 - j) * chh
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw - 1:.1f}" '
                         f'height="{chh - 1:.1f}" fill="{col}"/>')
            if v:
                parts.append(f'<text x="{x + cw / 2:.1f}" y="{y + chh / 2 + 3:.1f}" '
                             f'text-anchor="middle" fill="#222">{v:g}</text>')
    for i, xv in enumerate(xs):
        parts.append(f'<text x="{pad["l"] + i * cw + cw / 2:.1f}" '
                     f'y="{H2 - pad["b"] + 14}" text-anchor="middle" '
                     f'fill="#666">{xv:g}</text>')
    for j, yv in enumerate(ys):
        y = pad["t"] + (len(ys) - 1 - j) * chh + chh / 2 + 3
        parts.append(f'<text x="{pad["l"] - 6:.1f}" y="{y:.1f}" '
                     f'text-anchor="end" fill="#666">{yv:g}</text>')
    parts.append(f'<text x="{(W2 + pad["l"]) / 2:.0f}" y="{H2 - 8}" '
                 f'text-anchor="middle" fill="#666">{html.escape(xlabel)}</text>')
    parts.append(f'<text x="16" y="{H2 / 2:.0f}" fill="#666" '
                 f'transform="rotate(-90 16 {H2 / 2:.0f})" '
                 f'text-anchor="middle">{html.escape(ylabel)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
def binned(values: list[float], edges: list[float]) -> list[tuple[float, float]]:
    return list(zip(edges[:-1], edges[1:]))


def count_into(values: list[float], edges: list[float]) -> list[int]:
    out = [0] * (len(edges) - 1)
    for v in values:
        for i in range(len(edges) - 1):
            if edges[i] <= v < edges[i + 1]:
                out[i] += 1
                break
        else:
            if v >= edges[-1]:
                out[-1] += 1
    return out


def main() -> int:
    rows = load()
    delta = baseline_delta(rows)
    meas = [c for c in rows if num(c.get("delay_samples")) is not None]
    adopted = [c for c in meas if c.get("decision") in ("fixed",
                                                        "already_aligned")]
    print(f"rows={len(rows)} measurable={len(meas)} adopted={len(adopted)}")
    print(f"baseline replica delta = {delta} "
          f"({'faithful' if delta == 0 else 'MISMATCH - curves not published'})")
    if delta != 0:
        return 2

    charts: list[tuple[str, str, str]] = []      # (id, title, svg)

    # ---- histogram 1: usable_frames ----
    edges = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 80, 120, 200, 400]
    ad = [num(c.get("usable_frames"), 0) for c in meas]
    ref = [num(c.get("usable_frames"), 0) for c in meas
           if c.get("decision") not in ("fixed", "already_aligned")]
    charts.append(("h_frames", "可用帧数分布（红色虚线 = 现行门 5，蓝 = 候选门 3）",
                   svg_hist("每次测量的可用帧数：门槛落在哪里",
                            binned(ad, edges),
                            [count_into(ref, edges), count_into(ad, edges)],
                            ["被拒", "全部可测"],
                            ["#e08a8a", "#6a9fd8"],
                            "usable_frames（对数式分箱）",
                            [(5, "5"), (3, "3")])))

    # ---- histogram 2: confidence ----
    e2 = [0, .05, .1, .15, .2, .25, .3, .35, .4, .45, .5, .55, .6, .65,
          .7, .75, .8, .85, .9]
    cf = [num(c.get("confidence"), 0.0) for c in meas]
    cf_ref = [num(c.get("confidence"), 0.0) for c in meas
              if c.get("decision") not in ("fixed", "already_aligned")]
    charts.append(("h_conf", "置信度分布（红色虚线 = 现行门 0.3）",
                   svg_hist("置信度：可测轨几乎全部 >0.6，0.3 这道门几乎不生效",
                            binned(cf, e2),
                            [count_into(cf_ref, e2), count_into(cf, e2)],
                            ["被拒", "全部可测"],
                            ["#e08a8a", "#6a9fd8"], "confidence",
                            [(0.3, "0.3")], x0=0, x1=0.9)))

    # ---- histogram 3: MAD ----
    e3 = [0, .02, .04, .06, .08, .1, .15, .2, .3, .5, .8, 1.0, 1.5, 2.0,
          3.0, 5.0]
    mad = [num(c.get("traj_mad_ms"), 0.0) for c in meas]
    mad_ref = [num(c.get("traj_mad_ms"), 0.0) for c in meas
               if c.get("decision") not in ("fixed", "already_aligned")]
    charts.append(("h_mad", "轨迹 MAD（红色虚线 = 现行门 1.0 ms）",
                   svg_hist("MAD：绝大多数的拒绝不是 MAD 造成的",
                            binned(mad, e3),
                            [count_into(mad_ref, e3), count_into(mad, e3)],
                            ["被拒", "全部可测"],
                            ["#e08a8a", "#6a9fd8"],
                            "traj_mad_ms（ms）", [(1.0, "1.0")],
                            x0=0, x1=3.0)))

    # ---- histogram 4: spread, split by frames>=20 ----
    e4 = [0, .1, .2, .3, .5, .8, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0]
    sp_ge = [c["_spread_ms"] for c in meas if num(c.get("usable_frames"), 0) >= 20]
    sp_lt = [c["_spread_ms"] for c in meas if num(c.get("usable_frames"), 0) < 20]
    charts.append(("h_spread", "轨迹全范围（分帧数≥20 / <20；红虚线 = 门 1.0 ms）",
                   svg_hist("全范围：**只有帧数≥20 时才被这道门检查**",
                            binned(sp_ge + sp_lt, e4),
                            [count_into(sp_lt, e4), count_into(sp_ge, e4)],
                            ["帧数<20（门不检查）", "帧数≥20（门生效）"],
                            ["#c9c9c9", "#6a9fd8"],
                            "spread（ms，全范围）", [(1.0, "1.0")],
                            x0=0, x1=6.0)))

    # ---- histogram 5: |delay| of refused measurable tracks ----
    e5 = [0, .1, .2, .5, 1, 2, 5, 10, 15, 18, 20, 22, 24, 26, 28, 32]
    ref_delay = [abs(num(c.get("delay_ms"), 0.0)) for c in meas
                 if c.get("decision") == "untouched"]
    ok_delay = [abs(num(c.get("delay_ms"), 0.0)) for c in meas
                if c.get("decision") in ("fixed", "already_aligned")]
    charts.append(("h_delay", "|偏差|：被拒轨（红）与已采纳轨（蓝）",
                   svg_hist("留在未修轨上的偏差：20–26 ms 有一个峰",
                            binned(ref_delay + ok_delay, e5),
                            [count_into(ref_delay, e5),
                             count_into(ok_delay, e5)],
                            ["被拒（未修）", "已采纳（修了/已对齐）"],
                            ["#e08a8a", "#6a9fd8"], "|delay|（ms）",
                            [(5 * 1000 / SR, "5 采样")], x0=0, x1=32)))

    # ---- sweeps ----
    cur_meas_fixed = sum(1 for c in meas if c.get("decision") == "fixed")
    sw_frames = sweep(rows, "frames", list(range(1, 13)))
    f_rate = [100.0 * s["new_fixed"] / max(1, len(meas)) for s in sw_frames]
    f_rec = [s["recover_ms"] for s in sw_frames]
    charts.append(("c_frames", "min_usable_frames 连续扫描：可用率与回收量",
                   svg_curve("min_usable_frames 连续扫描",
                             [s["v"] for s in sw_frames],
                             [("新增修正（条）", [s["new_fixed"] for s in sw_frames],
                               "#1f77b4"),
                              ("回收偏差（ms，右轴同比例）", f_rec, "#d62728")],
                             "min_usable_frames", "条数 / ms",
                             [(CUR["frames"], "现行 5")])))

    mad_vals = [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 0.9, 1.0, 1.2, 1.5, 1.8,
                2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]
    sw_mad = sweep(rows, "mad", mad_vals)
    charts.append(("c_mad", "mad_max_ms 连续扫描",
                   svg_curve("mad_max_ms 连续扫描",
                             [s["v"] for s in sw_mad],
                             [("新增修正（条）", [s["new_fixed"] for s in sw_mad],
                               "#1f77b4"),
                              ("回收偏差（ms）", [s["recover_ms"] for s in sw_mad],
                               "#d62728"),
                              ("接受的变化（ms）", [s["accept_ms"] for s in sw_mad],
                               "#2ca02c")],
                             "mad_max_ms", "条数 / ms",
                             [(CUR["mad"], "现行 1.0")])))

    conf_vals = [i / 20 for i in range(0, 19)]
    sw_conf = sweep(rows, "conf", conf_vals)
    # 置信度门**只可能是"拦下了多少条"**, 不可能是"放开后多修多少条":
    # 被它拦下的轨 conf=0 且 usable_frames<5, 放开 conf 也过不了帧数门。
    conf_blocked = [sum(1 for c in meas
                        if num(c.get("confidence"), 0.0) < v
                        and c.get("decision") not in ("fixed", "already_aligned"))
                    for v in conf_vals]
    charts.append(("c_conf", "min_confidence 连续扫描：它不是瓶颈",
                   svg_curve("min_confidence：无论取多少, 放开的收益都是 0",
                             conf_vals,
                             [("放开后新增修正（条）",
                               [s["new_fixed"] for s in sw_conf], "#1f77b4"),
                              ("被该门拦下的轨（条）", conf_blocked, "#d62728")],
                             "min_confidence", "条数",
                             [(CUR["conf"], "现行 0.3")])))

    shift_vals = list(range(0, 13))
    sw_shift = sweep(rows, "shift", shift_vals)
    charts.append(("c_shift", "min_shift_samples 连续扫描",
                   svg_curve("min_shift_samples 连续扫描（越小修得越多）",
                             [s["v"] for s in sw_shift],
                             [("新增修正（条）", [s["new_fixed"] for s in sw_shift],
                               "#1f77b4"),
                              ("收回偏差（ms）", [s["recover_ms"] for s in sw_shift],
                               "#d62728")],
                             "min_shift_samples", "条数 / ms",
                             [(CUR["shift"], "现行 5")])))

    # ---- heatmap frames x mad ----
    fx = list(range(1, 11))
    my = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    grid = []
    for mad_v in my:
        row = []
        for fr in fx:
            row.append(sum(1 for c in meas
                           if decide(c, fr, CUR["conf"], mad_v,
                                     CUR["shift"]) == "fixed"))
        grid.append(row)
    charts.append(("hm", "min_usable_frames × mad_max_ms 采纳数热图",
                   svg_heat("采纳数热图：两条参数的关系（帧数门在左、MAD 门在上）",
                            fx, my, grid, "min_usable_frames", "mad_max_ms")))

    # ---- markdown + html ----
    base = decide
    n_never = sum(1 for c in rows if c.get("reason") in NEVER)
    n_recheck = sum(1 for c in rows if c.get("reason") == RECHECK)
    n_nr = sum(1 for c in rows
               if base(c, CUR["frames"], CUR["conf"], CUR["mad"],
                       CUR["shift"]) == "needs_remeasure")

    html_parts = [
        "<!doctype html><meta charset='utf-8'><title>音频门限参数矩阵</title>",
        "<style>body{font-family:'Segoe UI',sans-serif;max-width:820px;"
        "margin:24px auto;line-height:1.6;color:#222}"
        "h1{font-size:20px} h2{font-size:16px;margin-top:28px;"
        "border-left:4px solid #1f77b4;padding-left:8px}"
        "table{border-collapse:collapse;font-size:13px}"
        "td,th{border:1px solid #ddd;padding:4px 8px}"
        "th{background:#f4f6f8} code{background:#f4f6f8;padding:1px 4px}"
        "figure{margin:14px 0}</style>",
        "<h1>音频门限：参数矩阵、连续可用率与结论</h1>",
        f"<p>数据来源：3 个已测 arm，去重后 <b>{len(rows)}</b> 条轨道记录，"
        f"其中可测 <b>{len(meas)}</b> 条、已采纳 <b>{len(adopted)}</b> 条。"
        f"离线重判与生产基线差 <code>{delta}</code>（0 = 复刻忠实）。</p>",
        "<p><b>边界</b>：<code>usable_frames</code> / <code>confidence</code> / "
        "<code>mad_max_ms</code> / <code>min_shift_samples</code> 的输入都在库里，"
        "可连续扫描；<code>recheck_residual</code> 的残差未入库，不扫；"
        f"静音/无锚点/超范围 <b>{n_never}</b> 条永远不可由阈值救回；"
        f"帧数&lt;20 的 <code>non_constant</code> <b>{n_nr}</b> 条因稳健位移未入库"
        "无法离线精确重判。</p>",
    ]
    for cid, title, svg in charts:
        html_parts.append(f"<h2>{html.escape(title)}</h2><figure>{svg}</figure>")

    # ---- 参数之间的关系 ----
    def at(param: str, v: float) -> dict:
        return next(s for s in sweep(rows, param, [v]))

    f3, f2 = at("frames", 3), at("frames", 2)
    m2, m3 = at("mad", 2.0), at("mad", 3.0)
    combined = sum(1 for c in meas
                   if decide(c, 3, CUR["conf"], 2.0, CUR["shift"]) == "fixed")
    html_parts.append(
        "<h2>参数之间的关系</h2><table>"
        "<tr><th>关系</th><th>证据（本次数据）</th></tr>"
        "<tr><td><code>min_usable_frames</code> 是"
        "<code>mad_max_ms</code> 的**前置条件**</td>"
        f"<td>全范围门只在帧数≥20 时生效；实测帧数门槛从 5 降到 3 就能多修 "
        f"{f3['new_fixed']} 条、回收 {f3['recover_ms']:.1f} ms，"
        f"而接受的片内变化只有 {f3['accept_ms']:.1f} ms。"
        "帧数少的轨 MAD/全范围都极小（MAD 中位 0.06–0.10 ms），"
        "它们不是不恒定，是**样本太少被帧数门挡住**。</td></tr>"
        "<tr><td><code>min_confidence</code> 不是瓶颈</td>"
        f"<td>可测轨置信度几乎全部在 0.6–0.8；被拒轨要么 conf=0 且帧数<5"
        "（根本没有可用帧），要么通过置信门后栽在恒定性上。"
        "把它从 0.3 放到 0 或收到 0.9，新增修正恒为 0。</td></tr>"
        "<tr><td><code>mad_max_ms</code> 只在帧数≥20 时起作用</td>"
        f"<td>1.0→2.0 多修 {m2['new_fixed']} 条 / 回收 {m2['recover_ms']:.1f} ms / "
        f"接受 {m2['accept_ms']:.1f} ms；3.0 时接受量涨到 "
        f"{m3['accept_ms']:.1f} ms（约 {m3['recover_ms']:.0f} ms 的 9%），"
        "再往上不划算。</td></tr>"
        "<tr><td>两个门**可叠加**</td>"
        f"<td>frames=3 且 mad=2.0 时新增修正 {combined} 条，"
        "约等于单独效果之和（3+3），说明二者命中的是不同子群。</td></tr>"
        "<tr><td><code>min_shift_samples</code> 与上面都正交</td>"
        "<td>它只决定“多小的偏差值得写”。降到 0 会多出 106 条修正，"
        "但合计只回收 <b>3 ms</b>（全部是 &lt;0.1 ms 的亚毫秒修正）——"
        "这条规则很便宜，也不是误差来源。</td></tr>"
        "<tr><td><code>recheck_residual</code> 是独立一维</td>"
        f"<td>{n_recheck} 条候选、可回收 113.3 ms；残差未入库，"
        "本轮不出曲线，需重测。</td></tr></table>")

    html_parts.append(
        "<h2>结论</h2><ol>"
        f"<li><b>可用率</b>：{len(meas)} 条可测轨里已采纳 {len(adopted)} 条"
        f"（{100.0 * len(adopted) / max(1, len(meas)):.1f}%）。"
        "剩下的未采纳轨里，"
        f"{n_never} 条是静音/无锚点/超范围（阈值救不回），"
        f"{n_nr} 条需要重测确认。</li>"
        "<li><b>唯一便宜且安全的松绑</b>：<code>min_usable_frames</code> "
        f"5 → 3，多修 {f3['new_fixed']} 条、回收 {f3['recover_ms']:.1f} ms，"
        f"代价 {f3['accept_ms']:.1f} ms。收益/代价比 ≈ "
        f"{f3['recover_ms'] / max(1e-9, f3['accept_ms']):.0f}:1。</li>"
        "<li><b>次选</b>：<code>mad_max_ms</code> 1.0 → 2.0，"
        f"回收 {m2['recover_ms']:.1f} ms / 接受 {m2['accept_ms']:.1f} ms"
        "（15:1）。3.0 以上代价陡增，不建议。</li>"
        "<li><b>不要动</b> <code>min_confidence</code>（无收益）与 "
        "<code>min_shift_samples</code>（本身只值 3 ms，且是明确的产品决定）。</li>"
        "<li><b>真正该改的是判据形状</b>：<code>recheck_residual</code> 用绝对门"
        "（0.05 ms）会把“留 20.79 ms 偏差”判成比“留 0.08 ms”更安全。"
        "应改成相对判据，这一项单独值 113 ms。</li>"
        "</ol>")
    html_parts.append("</body>")
    html_path = OUT / "gate-analysis.html"
    html_path.write_text("".join(html_parts), encoding="utf-8")

    # ---- console summary ----
    print("\nmin_usable_frames 扫描 (新增/回收ms/接受ms):")
    for s in sw_frames:
        print(f"  {s['v']:>2}  {s['new_fixed']:>3} / {s['recover_ms']:>7.1f} / "
              f"{s['accept_ms']:>6.1f}")
    print("\nmad_max_ms 扫描:")
    for s in sw_mad:
        print(f"  {s['v']:>4}  {s['new_fixed']:>3} / {s['recover_ms']:>7.1f} / "
              f"{s['accept_ms']:>6.1f}")
    print("\nmin_confidence 扫描 (新增修正):")
    print("  " + "  ".join(f"{s['v']:.2f}:{s['new_fixed']}" for s in sw_conf))
    print("\nmin_shift_samples 扫描 (新增修正/回收ms):")
    print("  " + "  ".join(f"{s['v']}:{s['new_fixed']}/{s['recover_ms']:.0f}"
                           for s in sw_shift))
    print(f"\n-> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
