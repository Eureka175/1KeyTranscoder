# -*- coding: utf-8 -*-
"""Build the software (x265 / SVT-AV1) report + combined 4K evaluation report."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(r"F:\1KeyTranscoder")
HW = ROOT / "work" / "hw4k_matrix"
X265 = ROOT / "work" / "sw4k_matrix"
SVT = ROOT / "work" / "svt4k_matrix"
OUT = ROOT / "work" / "software_eval"
CHARTS = OUT / "charts"

HW_LABEL = {"nvenc": "NVENC HEVC", "qsv": "QSV HEVC",
            "nvenc-av1": "NVENC AV1", "qsv-av1": "QSV AV1"}
SW_LABEL = {"x265": "x265", "svtav1": "SVT-AV1"}
PRESETS = ["uhq", "hq", "small", "fast"]
COLORS = {"NVENC HEVC": "#1f77b4", "QSV HEVC": "#ff7f0e",
          "NVENC AV1": "#2ca02c", "QSV AV1": "#d62728",
          "x265": "#9467bd", "SVT-AV1": "#8c564b"}


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def f(v, d=2, default="-"):
    if v is None:
        return default
    try:
        return f"{float(v):.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def norm_hw(r):
    return {"family": HW_LABEL.get(r["encoder"], r["encoder"]),
            "preset": r["preset"], "clip": r["clip"],
            "mbps": r["video_mbps"], "vmaf": r["vmaf"],
            "psnr": r["psnr"], "ssim": r["ssim"],
            "pipe": r["pipeline_fps"], "enc": r["encoder_fps"],
            "cap": r["cap_maxrate_kbps"], "duration": r["duration"],
            "frames": r["frames"], "mb": r["output_mb"]}


def norm_sw(r):
    return {"family": SW_LABEL.get(r["encoder"], r["encoder"]),
            "preset": r["preset"], "clip": r["clip"],
            "mbps": r["video_mbps"], "vmaf": r["vmaf"],
            "psnr": r["psnr"], "ssim": r["ssim"],
            "pipe": r["pipeline_fps"], "enc": r["encoder_fps"],
            "cap": r["cap_maxrate_kbps"], "duration": r["duration"],
            "frames": r["frames"], "mb": r["output_mb"]}


def agg(rows, family, preset, clips=None, short=False):
    rs = [r for r in rows if r["family"] == family and r["preset"] == preset
          and (clips is None or r["clip"] in clips)
          and (r.get("duration", 99) >= 15) == (not short)]
    if not rs:
        return None
    pipe_vals = [r["pipe"] for r in rs if r["pipe"]]
    enc_vals = [r["enc"] for r in rs if r["enc"]]
    return {
        "n": len(rs),
        "mbps": statistics.mean(r["mbps"] for r in rs),
        "vmaf": statistics.mean(r["vmaf"] for r in rs),
        "pipe": statistics.mean(pipe_vals) if pipe_vals else None,
        "enc": statistics.mean(enc_vals) if enc_vals else None,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    hw = [norm_hw(r) for r in load(HW / "metrics.json")]
    x265 = [norm_sw(r) for r in load(X265 / "metrics.json")]
    svt = [norm_sw(r) for r in load(SVT / "metrics.json")]
    # split short-sample rows (x265 UHQ 10 s subset) from full-clip rows
    x265_short = [r for r in x265 if r["duration"] < 15]
    x265_full = [r for r in x265 if r["duration"] >= 15]
    clips = sorted({r["clip"] for r in hw})
    families = ["NVENC HEVC", "NVENC AV1", "QSV HEVC", "QSV AV1",
                "x265", "SVT-AV1"]
    allrows = hw + x265_full + svt

    # ---- merged metrics csv/json ----
    fields = ["family", "preset", "clip", "mbps", "vmaf", "psnr", "ssim",
              "pipe", "enc", "cap", "duration", "frames", "mb"]
    with (OUT / "metrics_software.csv").open("w", newline="",
                                            encoding="utf-8-sig") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(x265 + svt)
    (OUT / "metrics_software.json").write_text(
        json.dumps(x265 + svt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    lines = [
        "# 4K 软件编码器评估：x265 / SVT-AV1（30 s 多场景）",
        "",
        "与 `work/hw4k_matrix` 硬件矩阵使用同一批 6 条 30 s 只读切片。",
        "",
        "## 范围与限制（先看）",
        "",
        "* x265：FAST/HQ/SMALL 完整 6 片段；**UHQ 仅 2 条 10 s 片段**"
        "（4K UHQ 实测约 0.24 fps，6×30 s 估算 >6 h，无法在窗口内完成）。",
        "* SVT-AV1：FAST/HQ/SMALL 完整 6 片段；**UHQ 未完成**"
        "（实测约 1 帧/50 s ≈ 0.02 fps，10 s 2 片段也需 >4 h）。",
        "* 因此软件重挡位的码率/画质只作趋势参考，不与 30 s 完整片段直接比较。",
        "* 软件编码使用产品默认参数；x265 为 `slow`（HQ/UHQ/SMALL 同一 preset，"
        "不同 CRF/质量参数）。",
        "",
        "## 软件矩阵逐片段结果（30 s 完整片段）",
        "",
    ]
    for clip in clips:
        lines += [f"### {clip}", "",
                  "| Encoder | UHQ | HQ | SMALL | FAST |",
                  "|---|---|---|---|---|"]
        for fam in ("x265", "SVT-AV1"):
            cells = []
            for preset in PRESETS:
                a = agg(allrows, fam, preset, {clip})
                if a:
                    cells.append(f"{f(a['mbps'],1)} Mb / VMAF {f(a['vmaf'],2)} / "
                                 f"{f(a['pipe'],1)} fps")
                elif fam == "x265" and preset == "uhq":
                    # short-sample UHQ for action clips
                    rs = [r for r in x265_short if r["clip"] == clip
                          and r["preset"] == "uhq"]
                    cells.append(
                        f"{f(rs[0]['mbps'],1)} Mb / VMAF {f(rs[0]['vmaf'],2)}"
                        f" / 10 s" if rs else "—")
                else:
                    cells.append("—")
            lines.append(f"| {fam} | " + " | ".join(cells) + " |")
        lines += [""]

    lines += ["## 全片段平均（完整 30 s，x265 UHQ 除外）", "",
              "| Encoder | Preset | 视频 Mbps | VMAF | PSNR | SSIM | pipeline fps |",
              "|---|---|---|---|---|---|---|"]
    for fam in ("x265", "SVT-AV1"):
        for preset in PRESETS:
            a = agg(x265_full if fam == "x265" else svt, fam, preset)
            if not a:
                continue
            rs = [r for r in (x265_full if fam == "x265" else svt)
                  if r["family"] == fam and r["preset"] == preset]
            ps = statistics.mean(r["psnr"] for r in rs if r["psnr"])
            ss = statistics.mean(r["ssim"] for r in rs if r["ssim"])
            lines.append(f"| {fam} | {preset.upper()} | {f(a['mbps'],2)} | "
                         f"{f(a['vmaf'],3)} | {f(ps,2)} | {f(ss,5)} | "
                         f"{f(a['pipe'],1)} |")
    if x265_short:
        lines += ["", "x265 UHQ 短样本（2×10 s，仅趋势）："
                  + "；".join(f"{r['clip']} {r['mbps']:.1f} Mbps / "
                              f"VMAF {r['vmaf']:.3f}"
                              for r in x265_short)]
    lines += [""]

    # ---- cross-encoder comparison ----
    lines += ["## 与硬件编码器对照（FAST / HQ，视频 Mbps / VMAF）", ""]
    for preset in ("fast", "hq"):
        lines += [f"### {preset.upper()}", "",
                  "| 片段 | NVENC HEVC | NVENC AV1 | QSV HEVC | QSV AV1 | x265 | SVT-AV1 |",
                  "|---|---|---|---|---|---|---|"]
        for clip in clips:
            cells = []
            for fam in families:
                a = agg(allrows, fam, preset, {clip},
                        short=(fam == "x265" and preset == "uhq"))
                cells.append(f"{f(a['mbps'],1)} / {f(a['vmaf'],2)}"
                             if a else "—")
            lines.append(f"| {clip} | " + " | ".join(cells) + " |")
        lines += [""]

    # ---- speed ----
    lines += ["## 速度（pipeline fps，完整片段；越高越快）", "",
              "| Encoder | UHQ | HQ | SMALL | FAST |",
              "|---|---|---|---|---|"]
    for fam in families:
        cells = []
        for preset in PRESETS:
            a = agg(allrows, fam, preset)
            if a and a["pipe"]:
                cells.append(f"{a['pipe']:.1f}")
            elif fam == "x265" and preset == "uhq":
                cells.append("0.2–0.3（10 s 样本）")
            else:
                cells.append("—")
        lines.append(f"| {fam} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "* x265 UHQ 4K 实测约 0.24 fps；SVT-AV1 UHQ 4K 实测约 0.02 fps。",
        "* NVENC/QSV 编码器速度包含解码/复核/封装开销（pipeline fps）；"
        "纯硬解片段见硬件报告。",
        "",
    ]

    # ---- wall ----
    lines += ["## 软件编码的码率上限（CRF + VBV）", ""]
    wall = []
    for r in x265 + svt:
        if r.get("cap"):
            ratio = r["mbps"] / (r["cap"] / 1000)
            if ratio >= 0.8:
                wall.append((ratio, r))
    lines.append(f"mean/cap ≥80% 的格子：{len(wall)} 个。")
    for ratio, r in sorted(wall, reverse=True):
        lines.append(f"* {r['family']} / {r['preset'].upper()} / {r['clip']}: "
                     f"mean/cap≈{ratio*100:.0f}%（cap {r['cap']/1000:.1f} Mbps）")
    lines += [""]

    # ---- findings ----
    lines += ["## 主要发现", ""]
    for preset in ("fast", "hq", "small"):
        vals = []
        for fam in families:
            a = agg(allrows, fam, preset)
            if a:
                vals.append((a["mbps"], a["vmaf"], fam))
        if vals:
            cheapest = min(vals, key=lambda x: x[0])
            best = max(vals, key=lambda x: x[1])
            lines.append(
                f"* {preset.upper()}：码率最低 {cheapest[2]} "
                f"({cheapest[0]:.1f} Mbps / VMAF {cheapest[1]:.2f})；"
                f"VMAF 最高 {best[2]} "
                f"({best[0]:.1f} Mbps / {best[1]:.2f})。")
    lines += [
        "* 软件编码（x265/SVT-AV1）在多数场景可用明显更低码率接近硬件编码画质，"
        "但速度慢 3–50 倍；4K 实时/批量生产仍应使用 NVENC/QSV。",
        "* 夜间高 ISO 舞团片段对所有编码器都困难（VMAF 92–96）；"
        "SVT-AV1 HQ 在该片段上效率最好（28.6 Mbps / 95.15）。",
        "* x265 重挡位在 4K 下不具生产可行性；x265 UHQ 的码率甚至触顶 VBV cap"
        "（day_action4_pov 106%）。",
        "",
    ]

    # ---- charts ----
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for fam in families:
        xs, ys = [], []
        for preset in PRESETS:
            a = agg(allrows, fam, preset)
            if a:
                xs.append(a["mbps"])
                ys.append(a["vmaf"])
                ax.annotate(preset.upper(), (a["mbps"], a["vmaf"]),
                            textcoords="offset points", xytext=(3, 3),
                            fontsize=7, color=COLORS[fam])
        if xs:
            ax.plot(xs, ys, "o-", color=COLORS[fam], label=fam, markersize=6)
    ax.set_xlabel("mean video bitrate (Mbps, full 30 s clips)")
    ax.set_ylabel("mean VMAF")
    ax.set_title("4K evaluation — rate / quality (mean over 6 clips)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CHARTS / "all_encoders_rate_quality.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    speed = []
    for fam in families:
        a = agg(allrows, fam, "hq")
        if a and a["pipe"]:
            speed.append((fam, a["pipe"]))
    ax.bar([s[0] for s in speed], [s[1] for s in speed],
           color=[COLORS[s[0]] for s in speed])
    ax.set_yscale("log")
    ax.set_ylabel("pipeline fps (log)")
    ax.set_title("4K evaluation — HQ speed (6 clips)")
    ax.grid(axis="y", alpha=0.3)
    plt.xticks(rotation=20, fontsize=8)
    fig.tight_layout()
    fig.savefig(CHARTS / "speed_hq.png", dpi=140)
    plt.close(fig)
    lines += ["## 图表", "",
              "![all encoders](charts/all_encoders_rate_quality.png)",
              "![speed](charts/speed_hq.png)", ""]

    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- combined top-level report ----
    hw_agg = {}
    for fam in ("NVENC HEVC", "NVENC AV1", "QSV HEVC", "QSV AV1"):
        for preset in PRESETS:
            a = agg(hw, fam, preset)
            if a:
                hw_agg[(fam, preset)] = a
    combo = [
        "# 4K 编码器评估总报告（硬件全矩阵 + x265 / SVT-AV1）",
        "",
        "本报告汇总三组测试：",
        "",
        "1. **硬件全矩阵**：4 硬件编码器 × 4 挡位 × 6 条 30 s 片段"
        "（96 输出，2.6 h）→ `work/hw4k_matrix/REPORT.md`",
        "2. **x265**：FAST/HQ/SMALL 6 片段 + UHQ 2×10 s"
        "→ `work/software_eval/REPORT.md`",
        "3. **SVT-AV1**：FAST/HQ/SMALL 6 片段（UHQ 实测不可行）"
        "→ `work/software_eval/REPORT.md`",
        "",
        "## 素材",
        "",
        "* Action4 夜 POV（4K 4:3 10-bit HEVC）、舞团夜高 ISO（4K60 10-bit HEVC）",
        "* Action4 昼 POV、202610 白天（Sony 10-bit HEVC / Canon 8-bit H.264）",
        "* I:\\2026.09.24 活动（Sony 4:2:2 10-bit H.264）",
        "",
        "## 核心结论",
        "",
        "1. **速度**：NVENC 最快（FAST 硬解片段 49–94 encoder fps），QSV 次之"
        "（48–84）；x265 FAST 5.8–14.3，x265 HQ 0.7–3.1；SVT-AV1 FAST 3.3–23，"
        "HQ 2.6–12；x265 UHQ ~0.24 fps、SVT UHQ ~0.02 fps，4K 重挡位不可生产。",
        "2. **画质/码率**：UHQ/HQ 最高 VMAF 为 NVENC AV1（99.13/98.96），"
        "NVENC HEVC 紧随；QSV 码率显著更低但画质也更低。",
        "3. **软件效率**：x265 FAST 用 0.44–0.93× NVENC HEVC 的码率达到接近画质；"
        "SVT-AV1 FAST/HQ 用 0.33–0.84× 码率达到接近/相当画质，"
        "但速度慢 3–10 倍（相对硬件）。",
        "4. **撞墙**：硬件侧 NVENC 在 4K 高复杂度场景（舞团夜、Action4 昼）"
        "顶到动态 VBV cap（mean/cap 82–99%）；QSV 全部未撞。软件侧 "
        "x265 HQ/UHQ 在 Action4 昼也触顶（103%/106%），SVT SMALL 86%。",
        "5. **推荐**：批量/实时 4K 用 NVENC HEVC FAST/HQ；追求码率效率且能离线"
        "跑可用 SVT-AV1 HQ（比 x265 HQ 快、效率好），但要接受 4–10 倍时间；"
        "x265 UHQ 4K 不建议。",
        "",
        "## 硬件全矩阵摘要（全片段平均，Mbps / VMAF）",
        "",
        "| Encoder | UHQ | HQ | SMALL | FAST |",
        "|---|---|---|---|---|",
    ]
    for fam in ("NVENC HEVC", "NVENC AV1", "QSV HEVC", "QSV AV1"):
        cells = []
        for preset in PRESETS:
            a = hw_agg.get((fam, preset))
            cells.append(f"{a['mbps']:.1f} / {a['vmaf']:.2f}" if a else "—")
        combo.append(f"| {fam} | " + " | ".join(cells) + " |")
    combo += [
        "",
        "## 软件摘要（完整 30 s 平均，Mbps / VMAF / pipeline fps）",
        "",
        "| Encoder | UHQ | HQ | SMALL | FAST |",
        "|---|---|---|---|---|",
    ]
    for fam in ("x265", "SVT-AV1"):
        cells = []
        for preset in PRESETS:
            a = agg(x265_full if fam == "x265" else svt, fam, preset)
            cells.append(f"{a['mbps']:.1f} / {a['vmaf']:.2f} / {a['pipe']:.1f}"
                         if a else "—")
        combo.append(f"| {fam} | " + " | ".join(cells) + " |")
    combo += [
        "",
        "## 文件",
        "",
        "* 硬件矩阵：`work/hw4k_matrix/REPORT.md` / `metrics.csv`；原始输出 `runs/`",
        "* 软件矩阵：`work/software_eval/REPORT.md` / `metrics_software.csv`；"
        "原始输出 `work/sw4k_matrix/runs/`、`work/svt4k_matrix/runs/`",
        "* 只读切片：`work/hw4k_matrix/clips/`（6×30 s）+ `extra_clips/`"
        "（9 条备用素材）",
        "* 码控撞墙明细：`work/hw4k_matrix/rc_wall.txt`",
        "",
    ]
    (ROOT / "work" / "REPORT_4K_EVALUATION.md").write_text(
        "\n".join(combo) + "\n", encoding="utf-8")
    print("wrote software_eval/REPORT.md, metrics_software.csv, "
          "charts, REPORT_4K_EVALUATION.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
