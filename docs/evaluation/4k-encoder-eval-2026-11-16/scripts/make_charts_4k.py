# -*- coding: utf-8 -*-
"""Generate the final 4K evaluation chart pack.

Reads:
  work/hw4k_matrix/metrics.json   (96 hardware outputs)
  work/sw4k_matrix/metrics.json   (x265: fast/hq/small + 2x10s uhq)
  work/svt4k_matrix/metrics.json  (SVT-AV1: fast/hq/small)

Writes work/charts_4k/*.png + REPORT_CHARTS.md
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(r"F:\1KeyTranscoder")
HW = ROOT / "work" / "hw4k_matrix"
X265 = ROOT / "work" / "sw4k_matrix"
SVT = ROOT / "work" / "svt4k_matrix"
OUT = ROOT / "work" / "charts_4k"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ENC = {
    "nvenc": "NVENC HEVC",
    "qsv": "QSV HEVC",
    "nvenc-av1": "NVENC AV1",
    "qsv-av1": "QSV AV1",
    "x265": "x265",
    "svtav1": "SVT-AV1",
}
COLORS = {
    "NVENC HEVC": "#1f77b4",
    "QSV HEVC": "#ff7f0e",
    "NVENC AV1": "#2ca02c",
    "QSV AV1": "#d62728",
    "x265": "#9467bd",
    "SVT-AV1": "#8c564b",
}
HW_FAMS = ["NVENC HEVC", "NVENC AV1", "QSV HEVC", "QSV AV1"]
SW_FAMS = ["x265", "SVT-AV1"]
ALL_FAMS = HW_FAMS + SW_FAMS
PRESETS = ["fast", "hq", "small", "uhq"]
PRESET_LABEL = {"fast": "FAST", "hq": "HQ", "small": "SMALL", "uhq": "UHQ"}
MARKERS = {"fast": "o", "hq": "s", "small": "^", "uhq": "D"}
CLIP_LABEL = {
    "day_action4_pov": "Action4 昼 POV",
    "day_daily_sony": "日常 Sony 10-bit",
    "day_daily_canon": "日常 Canon 8-bit",
    "event_sony_422": "活动 Sony 4:2:2",
    "night_action4_pov": "Action4 夜 POV",
    "night_dance_iso": "舞团夜高 ISO",
}
CLIP_ORDER = ["day_action4_pov", "day_daily_sony", "day_daily_canon",
              "event_sony_422", "night_action4_pov", "night_dance_iso"]
NIGHT = {"night_action4_pov", "night_dance_iso"}


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(row: dict, family: str) -> dict:
    return {
        "family": family,
        "preset": row["preset"],
        "clip": row["clip"],
        "mbps": float(row["video_mbps"]),
        "vmaf": float(row["vmaf"]),
        "psnr": row.get("psnr"),
        "ssim": row.get("ssim"),
        "pipeline_fps": row.get("pipeline_fps"),
        "encoder_fps": row.get("encoder_fps"),
        "cap": row.get("cap_maxrate_kbps"),
        "win_mean": row.get("win_mean_mbps"),
        "win_p95": row.get("win_p95_mbps"),
        "win_max": row.get("win_max_mbps"),
        "duration": float(row.get("duration") or 0),
    }


def build_rows() -> tuple[list[dict], list[dict]]:
    rows = []
    for r in load(HW / "metrics.json"):
        rows.append(norm(r, ENC.get(r["encoder"], r["encoder"])))
    for r in load(X265 / "metrics.json"):
        rows.append(norm(r, "x265"))
    for r in load(SVT / "metrics.json"):
        rows.append(norm(r, "SVT-AV1"))
    full = [r for r in rows if r["duration"] >= 15]
    short = [r for r in rows if r["duration"] < 15]
    return full, short


FULL, SHORT = build_rows()


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def agg(family: str, preset: str) -> dict | None:
    rs = [r for r in FULL if r["family"] == family and r["preset"] == preset]
    if not rs:
        return None
    return {
        "mbps": mean(r["mbps"] for r in rs),
        "vmaf": mean(r["vmaf"] for r in rs),
        "pipeline_fps": mean(r["pipeline_fps"] for r in rs),
        "encoder_fps": mean(r["encoder_fps"] for r in rs),
        "n": len(rs),
    }


def clip_rows(clip: str) -> list[dict]:
    return [r for r in FULL if r["clip"] == clip]


# --------------------------------------------------------------------------
# 01  rate/quality curves per clip
# --------------------------------------------------------------------------
def chart_rate_quality_per_clip() -> Path:
    fig, axes = plt.subplots(2, 3, figsize=(16, 9.5), sharey=False)
    for ax, clip in zip(axes.flat, CLIP_ORDER):
        rs = clip_rows(clip)
        for fam in ALL_FAMS:
            rs_f = [r for r in rs if r["family"] == fam]
            if not rs_f:
                continue
            rs_f.sort(key=lambda r: r["mbps"])
            ax.plot([r["mbps"] for r in rs_f], [r["vmaf"] for r in rs_f],
                    "-", color=COLORS[fam], linewidth=1.4, alpha=0.85)
            ax.scatter([r["mbps"] for r in rs_f], [r["vmaf"] for r in rs_f],
                       color=COLORS[fam], s=46, zorder=3)
            for r in rs_f:
                ax.annotate(r["preset"].upper(), (r["mbps"], r["vmaf"]),
                            textcoords="offset points", xytext=(4, 4),
                            fontsize=6.6, color=COLORS[fam])
        ymin = min(r["vmaf"] for r in rs) - 0.9
        ymax = min(100.0, max(r["vmaf"] for r in rs) + 0.35)
        ax.set_ylim(ymin, ymax)
        ax.set_xscale("log")
        ax.set_xlabel("视频码率 (Mbps, 对数)")
        ax.set_ylabel("VMAF")
        ax.set_title(CLIP_LABEL[clip], fontsize=11)
        ax.grid(alpha=0.3, which="both")
    fam_handles = [Line2D([0], [0], color=COLORS[f], marker="o", lw=1.6,
                          label=f) for f in ALL_FAMS]
    preset_handles = [Line2D([0], [0], color="#555", marker=MARKERS[p],
                             linestyle="", label=PRESET_LABEL[p])
                      for p in PRESETS]
    fig.legend(handles=fam_handles, ncol=6, loc="upper left",
               bbox_to_anchor=(0.01, 0.995), fontsize=9, frameon=False)
    fig.legend(handles=preset_handles, ncol=4, loc="upper right",
               bbox_to_anchor=(0.99, 0.995), fontsize=9, frameon=False)
    fig.suptitle("4K 各片段：码率-质量曲线（点=挡位）", y=0.94, fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = OUT / "01_rate_quality_by_clip.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 02  aggregate rate/quality
# --------------------------------------------------------------------------
def chart_rate_quality_aggregate() -> Path:
    fig, ax = plt.subplots(figsize=(9, 5.6))
    for fam in ALL_FAMS:
        pts = []
        for preset in PRESETS:
            a = agg(fam, preset)
            if a:
                pts.append((a["mbps"], a["vmaf"], preset))
        if not pts:
            continue
        pts.sort(key=lambda x: x[0])
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                "-o", color=COLORS[fam], markersize=6, lw=1.6, label=fam)
        for x, y, preset in pts:
            ax.annotate(PRESET_LABEL[preset], (x, y), textcoords="offset points",
                        xytext=(4, 4), fontsize=7, color=COLORS[fam])
    ax.set_xscale("log")
    ax.set_xlabel("平均视频码率 (Mbps, 对数)")
    ax.set_ylabel("平均 VMAF")
    ax.set_title("4K 全片段平均：码率-质量")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    path = OUT / "02_rate_quality_aggregate.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 03  speed: pipeline fps vs quality + Action4 pure hw encoder fps
# --------------------------------------------------------------------------
def chart_speed() -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.6))
    for fam in ALL_FAMS:
        for preset in PRESETS:
            a = agg(fam, preset)
            if not a or not a["pipeline_fps"]:
                continue
            ax1.scatter(a["pipeline_fps"], a["vmaf"], s=70,
                        color=COLORS[fam], marker=MARKERS[preset],
                        zorder=3)
            ax1.annotate(f"{fam} {PRESET_LABEL[preset]}",
                         (a["pipeline_fps"], a["vmaf"]),
                         textcoords="offset points", xytext=(4, 4),
                         fontsize=6.3, color=COLORS[fam])
    ax1.set_xscale("log")
    ax1.set_xlabel("pipeline fps（全 6 片段平均，对数）")
    ax1.set_ylabel("平均 VMAF")
    ax1.set_title("速度 vs 质量（含解码/复核/封装开销）")
    ax1.grid(alpha=0.3, which="both")

    action = [r for r in FULL if r["clip"].startswith(("day_action4_pov",
                                                       "night_action4_pov"))]
    encs = HW_FAMS
    width = 0.18
    for i, preset in enumerate(PRESETS):
        vals = []
        for fam in encs:
            rs = [r for r in action if r["family"] == fam
                  and r["preset"] == preset and r["encoder_fps"]]
            vals.append(mean(r["encoder_fps"] for r in rs) or 0)
        xs = np.arange(len(encs)) + (i - 1.5) * width
        bars = ax2.bar(xs, vals, width, label=PRESET_LABEL[preset],
                       color=COLORS[HW_FAMS[0]], alpha=0.35 + 0.16 * i,
                       edgecolor="black", linewidth=0.4)
        for b, v in zip(bars, vals):
            if v:
                ax2.text(b.get_x() + b.get_width() / 2, v * 1.04,
                         f"{v:.0f}", ha="center", va="bottom", fontsize=7)
    ax2.set_xticks(np.arange(len(encs)))
    ax2.set_xticklabels(encs, fontsize=9)
    ax2.set_yscale("log")
    ax2.set_ylabel("encoder fps（对数）")
    ax2.set_title("纯硬解片段（Action4 昼/夜）编码器原生速度")
    ax2.grid(axis="y", alpha=0.3, which="both")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    path = OUT / "03_speed_quality.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 04  rate-control wall
# --------------------------------------------------------------------------
def chart_wall() -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.8),
                                   gridspec_kw={"width_ratios": [1.15, 1]})
    wall = []
    for r in FULL:
        if not r["cap"] or not r["win_mean"]:
            continue
        cap = r["cap"] / 1000.0
        ratio = r["win_mean"] / cap
        wall.append((ratio, r, cap))
        ax1.scatter(cap, r["win_mean"], s=46, color=COLORS[r["family"]],
                    marker=MARKERS[r["preset"]], alpha=0.85, zorder=3)
    lim = max(x[2] for x in wall) * 1.08
    ax1.plot([0, lim], [0, lim], "k--", lw=1, label="y = cap")
    ax1.plot([0, lim], [0, lim * 0.8], "r:", lw=1, label="y = 0.8 × cap")
    wall.sort(reverse=True, key=lambda x: x[0])
    for ratio, r, cap in wall[:6]:
        ax1.annotate(f"{r['family']} {PRESET_LABEL[r['preset']]}\n"
                     f"{CLIP_LABEL[r['clip']]}  mean {ratio*100:.0f}%",
                     (cap, r["win_mean"]), textcoords="offset points",
                     xytext=(6, -12), fontsize=6.5,
                     bbox=dict(boxstyle="round,pad=0.15", fc="white",
                               ec="gray", alpha=0.8))
    ax1.set_xlabel("配置 cap / max_bitrate (Mbps)")
    ax1.set_ylabel("实际 1 s 窗口平均码率 (Mbps)")
    ax1.set_title("码控撞墙：实际均值 vs 配置上限")
    ax1.legend(fontsize=7.5)
    ax1.grid(alpha=0.3)

    fams = ALL_FAMS
    width = 0.2
    for i, preset in enumerate(PRESETS):
        vals = []
        for fam in fams:
            rs = [r for r in FULL
                  if r["family"] == fam and r["preset"] == preset
                  and r["cap"] and r["win_mean"]]
            if rs:
                vals.append(max(r["win_mean"] / (r["cap"] / 1000) for r in rs))
            else:
                vals.append(0)
        xs = np.arange(len(fams)) + (i - 1.5) * width
        bars = ax2.bar(xs, [v * 100 for v in vals], width,
                       label=PRESET_LABEL[preset], color="#5b7da8",
                       alpha=0.35 + 0.16 * i, edgecolor="black",
                       linewidth=0.4)
        for b, v in zip(bars, vals):
            if v:
                ax2.text(b.get_x() + b.get_width() / 2, v * 100 + 1.5,
                         f"{v*100:.0f}", ha="center", va="bottom",
                         fontsize=6.5, rotation=90)
    ax2.axhline(80, color="orange", linestyle="--", lw=1)
    ax2.axhline(100, color="red", linestyle="--", lw=1)
    ax2.text(4.8, 82, "80%", color="orange", fontsize=8)
    ax2.text(4.8, 102, "100%", color="red", fontsize=8)
    ax2.set_xticks(np.arange(len(fams)))
    ax2.set_xticklabels(fams, fontsize=8.5)
    ax2.set_ylabel("各片段中最高 mean/cap (%)")
    ax2.set_ylim(0, 135)
    ax2.set_title("各 encoder/挡位最贴墙片段（越高越受限）")
    ax2.grid(axis="y", alpha=0.3)
    ax2.legend(fontsize=7.5, loc="upper right")
    fig.tight_layout()
    path = OUT / "04_rate_control_wall.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 05  HQ heatmaps: VMAF & bitrate
# --------------------------------------------------------------------------
def chart_hq_heatmaps() -> Path:
    fams = ALL_FAMS
    rows = CLIP_ORDER
    vmat = np.zeros((len(rows), len(fams)))
    bmat = np.zeros((len(rows), len(fams)))
    for i, clip in enumerate(rows):
        for j, fam in enumerate(fams):
            rs = [r for r in FULL if r["family"] == fam
                  and r["preset"] == "hq" and r["clip"] == clip]
            vmat[i, j] = rs[0]["vmaf"] if rs else np.nan
            bmat[i, j] = rs[0]["mbps"] if rs else np.nan
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.8))
    for ax, mat, title, fmt, cmap, vmin, vmax, unit in [
        (axes[0], vmat, "HQ 平均 VMAF", "{:.2f}", "viridis",
         np.nanmin(vmat) - 0.2, np.nanmax(vmat) + 0.05, ""),
        (axes[1], bmat, "HQ 视频码率", "{:.1f}", "magma", None, None, " Mbps"),
    ]:
        im = ax.imshow(mat, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        ax.set_xticks(range(len(fams)))
        ax.set_xticklabels(fams, rotation=20, fontsize=8.5)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([CLIP_LABEL[c] for c in rows], fontsize=8.5)
        ax.set_title(title, fontsize=11)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                if np.isnan(mat[i, j]):
                    continue
                val = mat[i, j]
                color = "white" if (val - np.nanmin(mat)) / (
                    np.nanmax(mat) - np.nanmin(mat) + 1e-9) < 0.55 else "black"
                ax.text(j, i, fmt.format(val) + unit, ha="center",
                        va="center", fontsize=7.2, color=color)
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    fig.suptitle("HQ 挡位：6 片段 × 6 编码器（VMAF / 码率）", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = OUT / "05_hq_heatmaps.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 06  night vs day quality
# --------------------------------------------------------------------------
def chart_night_day() -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2), sharey=False)
    for ax, preset in zip(axes, ["hq", "fast"]):
        night_vals, day_vals = [], []
        for fam in ALL_FAMS:
            rs = [r for r in FULL if r["family"] == fam
                  and r["preset"] == preset]
            night = [r["vmaf"] for r in rs if r["clip"] in NIGHT]
            day = [r["vmaf"] for r in rs if r["clip"] not in NIGHT]
            night_vals.append(mean(night) or 0)
            day_vals.append(mean(day) or 0)
        xs = np.arange(len(ALL_FAMS))
        w = 0.36
        b1 = ax.bar(xs - w / 2, night_vals, w, label="夜（2 片段）",
                    color="#31465f")
        b2 = ax.bar(xs + w / 2, day_vals, w, label="昼（4 片段）",
                    color="#c9a227")
        for bars in (b1, b2):
            for b in bars:
                h = b.get_height()
                if h:
                    ax.text(b.get_x() + b.get_width() / 2, h + 0.03,
                            f"{h:.2f}", ha="center", va="bottom",
                            fontsize=6.6, rotation=90)
        ax.set_xticks(xs)
        ax.set_xticklabels(ALL_FAMS, rotation=15, fontsize=8.5)
        ax.set_ylabel("平均 VMAF")
        ax.set_title(f"{PRESET_LABEL[preset]}：夜 vs 昼 平均 VMAF")
        ax.set_ylim(min(min(night_vals), min(day_vals)) - 2, 100.2)
        ax.grid(axis="y", alpha=0.3)
        ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    path = OUT / "06_night_vs_day.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 07  preset ladder
# --------------------------------------------------------------------------
def chart_preset_ladder() -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.2))
    xs = [0, 1, 2, 3]
    xt = ["FAST", "HQ", "SMALL", "UHQ"]
    for fam in ALL_FAMS:
        vals_m, vals_v = [], []
        for preset in PRESETS:
            a = agg(fam, preset)
            vals_m.append(a["mbps"] if a else np.nan)
            vals_v.append(a["vmaf"] if a else np.nan)
        ax1.plot(xs, vals_m, "-o", color=COLORS[fam], label=fam,
                 markersize=5)
        ax2.plot(xs, vals_v, "-o", color=COLORS[fam], label=fam,
                 markersize=5)
    ax1.set_xticks(xs)
    ax1.set_xticklabels(xt)
    ax1.set_ylabel("平均视频码率 (Mbps)")
    ax1.set_title("挡位梯度：平均码率")
    ax1.grid(alpha=0.3)
    ax2.set_xticks(xs)
    ax2.set_xticklabels(xt)
    ax2.set_ylabel("平均 VMAF")
    ax2.set_title("挡位梯度：平均 VMAF")
    ax2.grid(alpha=0.3)
    ax1.legend(fontsize=7.5, ncol=2)
    fig.tight_layout()
    path = OUT / "07_preset_ladder.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 08  efficiency vs NVENC HEVC
# --------------------------------------------------------------------------
def chart_efficiency() -> Path:
    fams = ["SVT-AV1", "x265", "QSV HEVC", "QSV AV1", "NVENC AV1"]
    presets = ["fast", "hq", "small"]
    base = {}
    for preset in presets:
        for clip in CLIP_ORDER:
            r = [x for x in FULL if x["family"] == "NVENC HEVC"
                 and x["preset"] == preset and x["clip"] == clip]
            if r:
                base[(preset, clip)] = r[0]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.3))
    w = 0.15
    for i, preset in enumerate(presets):
        ratios, deltas = [], []
        for fam in fams:
            rs = []
            for clip in CLIP_ORDER:
                r = [x for x in FULL if x["family"] == fam
                     and x["preset"] == preset and x["clip"] == clip]
                b = base.get((preset, clip))
                if r and b:
                    rs.append((r[0], b))
            ratios.append(mean(r["mbps"] / b["mbps"] for r, b in rs) or 0)
            deltas.append(mean(r["vmaf"] - b["vmaf"] for r, b in rs) or 0)
        xs = np.arange(len(fams)) + (i - 1) * w
        ax1.bar(xs, ratios, w, label=PRESET_LABEL[preset],
                alpha=0.55 + 0.15 * i)
        xs2 = np.arange(len(fams)) + (i - 1) * w
        ax2.bar(xs2, deltas, w, label=PRESET_LABEL[preset],
                alpha=0.55 + 0.15 * i)
    ax1.axhline(1.0, color="black", lw=1, linestyle="--")
    ax1.set_xticks(np.arange(len(fams)))
    ax1.set_xticklabels(fams, rotation=15, fontsize=8.5)
    ax1.set_ylabel("码率 / NVENC HEVC 同挡位")
    ax1.set_title("相对 NVENC HEVC 的码率（<1 更省）")
    ax1.grid(axis="y", alpha=0.3)
    ax1.legend(fontsize=8)
    ax2.axhline(0, color="black", lw=1)
    ax2.set_xticks(np.arange(len(fams)))
    ax2.set_xticklabels(fams, rotation=15, fontsize=8.5)
    ax2.set_ylabel("ΔVMAF vs NVENC HEVC")
    ax2.set_title("相对 NVENC HEVC 的画质差（>0 更好）")
    ax2.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = OUT / "08_efficiency_vs_nvenc.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    paths = [
        chart_rate_quality_per_clip(),
        chart_rate_quality_aggregate(),
        chart_speed(),
        chart_wall(),
        chart_hq_heatmaps(),
        chart_night_day(),
        chart_preset_ladder(),
        chart_efficiency(),
    ]
    md = ["# 4K 评估图表包", "",
          "数据源：硬件矩阵 96 输出 + x265 20 输出 + SVT-AV1 18 输出。",
          "所有曲线使用完整 30 s 片段；x265 UHQ 10 s 样本不进入曲线。", ""]
    titles = {
        "01_rate_quality_by_clip.png": "各片段码率-质量曲线（6 编码器 × 挡位）",
        "02_rate_quality_aggregate.png": "全片段平均码率-质量",
        "03_speed_quality.png": "速度-质量 & 纯硬解 encoder fps",
        "04_rate_control_wall.png": "码控撞墙（实际 vs cap）",
        "05_hq_heatmaps.png": "HQ 挡位 VMAF/码率热图",
        "06_night_vs_day.png": "夜 vs 昼 VMAF（HQ/FAST）",
        "07_preset_ladder.png": "挡位梯度（码率/VMAF）",
        "08_efficiency_vs_nvenc.png": "相对 NVENC HEVC 的效率",
    }
    for p in paths:
        md += [f"## {titles.get(p.name, p.name)}", "",
               f"![{p.name}]({p.name})", ""]
    (OUT / "REPORT_CHARTS.md").write_text("\n".join(md) + "\n",
                                          encoding="utf-8")
    print("charts:", *[p.name for p in paths], sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
