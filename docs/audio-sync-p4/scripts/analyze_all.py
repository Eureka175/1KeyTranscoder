#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务1/2 汇总：全量探测数据统计 + 图表 + 长素材/极性/恢复汇总。

输入（work/probe_20261025/）:
  testsets.json             —— 2CH 策略生效前（旧行为）
  testsets_after_2ch.json   —— 2CH 静音默认跳过策略生效后
  i_20260924.json           —— I:\\2026.09.24 专业机位 + DJI 探测
  ../data/_scratch/drift_analysis.json / drift_rebuild.json / recovery.json /
  polarity.json             —— 4CH 长素材深度分析结果

输出:
  work/probe_20261025/summary.json      机器可读汇总
  work/probe_20261025/summary.md        人读汇总（报告素材）
  docs/audio-sync-p4/plots/task2_*.png  图表
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

REPO = Path(r"F:\1KeyTranscoder")
WORK = REPO / "work" / "probe_20261025"
PLOT = REPO / "docs" / "audio-sync-p4" / "plots"
SCRATCH = REPO / "data" / "_scratch"


def load(name: str) -> list[dict]:
    p = WORK / name
    if not p.is_file():
        print(f"WARN missing {p}")
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def group_of(rec: dict) -> str:
    rel = rec.get("rel") or Path(rec.get("path", "")).name
    parts = Path(rel).parts
    if len(parts) > 1:
        return parts[0]
    # I: 记录 path 里含 \\2026.09.24\\<机位>\\
    p = rec.get("path", "")
    for marker in ("A7M4", "FX3", "FX30", "HXR-NX800", "Mavic4P", "Pocket3"):
        if f"\\{marker}\\" in p:
            return marker
    return "root"


def audio_categories(rec: dict) -> str:
    n = rec.get("n_audio")
    audio = rec.get("audio") or []
    if n is None:
        return "unknown"
    if n == 0:
        return "0-audio"
    chans = sorted({(a.get("channels"), a.get("codec")) for a in audio})
    if len(chans) == 1 and chans[0][0] == 1:
        return f"{n}x mono PCM"
    if len(chans) == 1 and chans[0][0] == 2:
        return f"{n}x 2ch ({chans[0][1]})"
    if len(chans) == 1:
        return f"{n}x {chans[0][0]}ch ({chans[0][1]})"
    return f"{n}x mixed"


def record_stats(recs: list[dict]) -> dict:
    out = {
        "count": len(recs),
        "by_verdict": dict(Counter(r.get("verdict") for r in recs)),
        "by_duration_bucket": dict(Counter(_dur_bucket(r) for r in recs)),
        "audio_categories": dict(Counter(audio_categories(r) for r in recs)),
        "total_gib": round(sum(r.get("size_mib") or 0 for r in recs) / 1024, 2),
        "silent_hist": dict(Counter(r.get("n_silent") for r in recs)),
        "with_rtmd": sum(1 for r in recs if r.get("has_rtmd")),
        "with_data": sum(1 for r in recs if (r.get("n_data") or 0) > 0),
    }
    return out


def _dur_bucket(rec: dict) -> str:
    d = rec.get("duration_s") or 0
    if d <= 0:
        return "unknown"
    for hi, name in ((10, "<=10s"), (30, "10-30s"), (60, "30-60s"),
                     (300, "1-5min"), (1200, "5-20min"), (1e9, ">20min")):
        if d <= hi:
            return name
    return "unknown"


def align_quality(recs: list[dict]) -> list[dict]:
    """从生产报告里取每个文件的 best-pair 质量数据。"""
    out = []
    for r in recs:
        chans = ((r.get("default") or {}).get("channels")) or []
        vals = [c for c in chans if c.get("delay_ms") is not None]
        if not vals:
            continue
        best = max(vals, key=lambda c: (c.get("confidence") or 0.0))
        out.append({
            "name": r.get("name"), "group": group_of(r),
            "verdict": r.get("verdict"),
            "delay_ms": best.get("delay_ms"),
            "confidence": best.get("confidence"),
            "drift_ppm": best.get("drift_ppm"),
            "constant": best.get("constant"),
            "decision": best.get("decision"),
            "reason": best.get("reason"),
            "n_silent": r.get("n_silent"),
            "rms": r.get("rms_dbfs"),
        })
    return out


def silent_combos(recs: list[dict]) -> dict:
    out = Counter()
    for r in recs:
        chans = ((r.get("default") or {}).get("channels")) or []
        mask = "".join("1" if c.get("reason") == "silent_track" else "0"
                       for c in chans)
        if mask:
            out[mask] += 1
    return dict(out)


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOT.mkdir(parents=True, exist_ok=True)
    before = load("testsets.json")
    after = load("testsets_after_2ch.json")
    i_recs = load("i_20260924.json")

    summary = {
        "testsets_before_2ch_policy": record_stats(before),
        "testsets_after_2ch_policy": record_stats(after),
        "i_20260924": record_stats(i_recs),
        "testsets_by_group": {},
        "i_by_group": {},
        "silent_combos_after": silent_combos(after),
        "silent_combos_before": silent_combos(before),
        "two_channel_only_examples": [],
    }
    for g in sorted({group_of(r) for r in before}):
        rs = [r for r in before if group_of(r) == g]
        summary["testsets_by_group"][g] = record_stats(rs)
    for g in sorted({group_of(r) for r in i_recs}):
        rs = [r for r in i_recs if group_of(r) == g]
        summary["i_by_group"][g] = record_stats(rs)

    qual_before = align_quality(before)
    qual_after = align_quality(after)
    summary["quality_before"] = {
        "n": len(qual_before),
        "median_abs_delay_ms": round(float(np.median(
            [abs(q["delay_ms"]) for q in qual_before])), 3) if qual_before else None,
        "p90_abs_delay_ms": round(float(np.percentile(
            [abs(q["delay_ms"]) for q in qual_before], 90)), 3) if qual_before else None,
        "median_confidence": round(float(np.median(
            [q["confidence"] or 0 for q in qual_before])), 3) if qual_before else None,
        "constant_frac": round(float(np.mean(
            [bool(q["constant"]) for q in qual_before])), 3) if qual_before else None,
    }
    # two_channel_only 明细
    for r in after:
        if r.get("verdict") == "two_channel_only":
            ch = ((r.get("default") or {}).get("channels")) or []
            summary["two_channel_only_examples"].append({
                "name": r.get("name"),
                "rms": r.get("rms_dbfs"),
                "silent_mask": "".join(
                    "1" if c.get("reason") == "silent_track" else "0"
                    for c in ch),
                "duration_s": r.get("duration_s"),
            })
            break

    # ---------------- G1 inventory
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    groups = sorted(summary["testsets_by_group"])
    counts = [summary["testsets_by_group"][g]["count"] for g in groups]
    sizes = [summary["testsets_by_group"][g]["total_gib"] for g in groups]
    axes[0][0].bar(groups, counts, color="#4c72b0")
    axes[0][0].set_title("testsets: files per group")
    axes[0][0].tick_params(axis="x", rotation=20)
    axes[0][1].bar(groups, sizes, color="#dd8452")
    axes[0][1].set_title("testsets: total size per group [GiB]")
    axes[0][1].tick_params(axis="x", rotation=20)
    cats = summary["testsets_after_2ch_policy"]["audio_categories"]
    axes[1][0].barh(list(cats.keys()), list(cats.values()), color="#55a868")
    axes[1][0].set_title("testsets: audio layout categories")
    dur = summary["testsets_after_2ch_policy"]["by_duration_bucket"]
    axes[1][1].bar(list(dur.keys()), list(dur.values()), color="#c44e52")
    axes[1][1].set_title("testsets: duration buckets")
    axes[1][1].tick_params(axis="x", rotation=20)
    fig.suptitle("Task1 inventory: testsets (F:)")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_inventory.png", dpi=130)
    plt.close(fig)

    # ---------------- G2 verdicts (before/after policy)
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    verdicts = sorted(set(summary["testsets_before_2ch_policy"]["by_verdict"])
                      | set(summary["testsets_after_2ch_policy"]["by_verdict"]))
    vb = [summary["testsets_before_2ch_policy"]["by_verdict"].get(v, 0) for v in verdicts]
    va = [summary["testsets_after_2ch_policy"]["by_verdict"].get(v, 0) for v in verdicts]
    x = np.arange(len(verdicts))
    axes[0][0].bar(x - 0.2, vb, 0.4, label="before 2ch-skip", color="#888888")
    axes[0][0].bar(x + 0.2, va, 0.4, label="after 2ch-skip", color="#2ca02c")
    axes[0][0].set_xticks(x)
    axes[0][0].set_xticklabels(verdicts, rotation=20, ha="right")
    axes[0][0].set_title("testsets: verdicts before/after 2CH-skip policy")
    axes[0][0].legend(fontsize=7)
    sil = summary["testsets_after_2ch_policy"]["silent_hist"]
    sil_keys = sorted(sil, key=lambda k: -1 if k is None else k)
    axes[0][1].bar([str(k) for k in sil_keys], [sil[k] for k in sil_keys],
                   color="#8172b3")
    axes[0][1].set_title("testsets: #silent channels per file")
    axes[0][1].set_xlabel("n silent")
    igroups = sorted(summary["i_by_group"])
    ilayouts = Counter()
    for r in i_recs:
        ilayouts[(group_of(r), audio_categories(r))] += 1
    cats2 = sorted({c for _, c in ilayouts})
    bottom = np.zeros(len(igroups))
    colors = plt.cm.tab10(np.linspace(0, 1, len(cats2)))
    for c, col in zip(cats2, colors):
        vals = np.array([ilayouts.get((g, c), 0) for g in igroups])
        axes[1][0].bar(igroups, vals, bottom=bottom, label=c, color=col)
        bottom += vals
    axes[1][0].set_title("I:\\2026.09.24: audio layouts per camera")
    axes[1][0].tick_params(axis="x", rotation=20)
    axes[1][0].legend(fontsize=6)
    iver = Counter(r.get("verdict") for r in i_recs)
    axes[1][1].bar(list(iver.keys()), list(iver.values()), color="#937860")
    axes[1][1].set_title("I:\\2026.09.24: verdicts")
    axes[1][1].tick_params(axis="x", rotation=20)
    fig.suptitle("Task1/5: alignment eligibility and policy effects")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_verdicts.png", dpi=130)
    plt.close(fig)

    # ---------------- G3 quality
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    if qual_before:
        delay = np.array([abs(q["delay_ms"]) for q in qual_before])
        conf = np.array([q["confidence"] or 0 for q in qual_before])
        axes[0][0].scatter(delay, conf, s=10, alpha=0.6, c="#2ca02c")
        axes[0][0].set_xscale("symlog", linthresh=0.1)
        axes[0][0].set_xlabel("|delay| [ms]")
        axes[0][0].set_ylabel("confidence")
        axes[0][0].set_title("testsets (before policy): delay vs confidence")
        drift = np.array([abs(q["drift_ppm"] or 0) for q in qual_before])
        axes[0][1].hist(drift[drift > 0], bins=40, color="#d62728")
        axes[0][1].set_xlabel("|drift| [ppm]")
        axes[0][1].set_title("testsets: |drift| distribution (measured tracks)")
    axes[1][0].hist([q["confidence"] or 0 for q in qual_before], bins=30,
                    color="#1f77b4")
    axes[1][0].set_xlabel("confidence")
    axes[1][0].set_title("testsets: confidence distribution")
    if qual_before:
        by_group = defaultdict(list)
        for q in qual_before:
            by_group[q["group"]].append(abs(q["delay_ms"]))
        keys = sorted(by_group)
        axes[1][1].boxplot([by_group[k] for k in keys], tick_labels=keys)
        axes[1][1].set_yscale("symlog", linthresh=0.1)
        axes[1][1].set_title("|delay| by group")
        axes[1][1].tick_params(axis="x", rotation=20)
    fig.suptitle("Task1: measured alignment quality (production API)")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_quality.png", dpi=130)
    plt.close(fig)

    # ---------------- G4 two-channel detail
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    tco = [r for r in after if r.get("verdict") == "two_channel_only"]
    axes[0][0].hist([r.get("duration_s") or 0 for r in tco], bins=30,
                    color="#8172b3")
    axes[0][0].set_title(f"two_channel_only files (n={len(tco)}): durations")
    axes[0][0].set_xlabel("duration [s]")
    silent_rms, active_rms = [], []
    for r in tco:
        chans = ((r.get("default") or {}).get("channels")) or []
        for c in chans:
            rms = c.get("rms_dbfs")
            if rms is None:
                continue
            (silent_rms if c.get("reason") == "silent_track" else active_rms
             ).append(rms)
    if silent_rms:
        axes[0][1].hist(silent_rms, bins=30, alpha=0.7, label="silent",
                        color="#d62728")
    if active_rms:
        axes[0][1].hist(active_rms, bins=30, alpha=0.7, label="active",
                        color="#2ca02c")
    axes[0][1].set_title("RMS of silent vs active channels (dBFS)")
    axes[0][1].legend(fontsize=7)
    combos = Counter()
    for r in tco:
        chans = ((r.get("default") or {}).get("channels")) or []
        combos["".join("1" if c.get("reason") == "silent_track" else "0"
                       for c in chans)] += 1
    axes[1][0].bar(list(combos.keys()), list(combos.values()), color="#937860")
    axes[1][0].set_title("silent-channel mask (1=silent) in 2ch-only files")
    axes[1][0].tick_params(axis="x", rotation=20)
    # 有效 2ch 之间的相对延迟（旧行为测量值）
    d2 = []
    for r in before:
        if r.get("verdict") not in ("alignable",):
            continue
        chans = ((r.get("default") or {}).get("channels")) or []
        for c in chans:
            if c.get("delay_ms") is not None:
                d2.append(c["delay_ms"])
                break
    axes[1][1].hist(d2, bins=40, color="#4c72b0")
    axes[1][1].set_title("2 valid channels: relative delay (old behavior) [ms]")
    axes[1][1].set_xlabel("delay [ms]")
    fig.suptitle("Task5: 4CH files with only 2 valid channels")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_two_channel.png", dpi=130)
    plt.close(fig)

    # ---------------- G5 I: metadata / DJI
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    meta = Counter()
    for r in i_recs:
        meta[(group_of(r), "rtmd" if r.get("has_rtmd") else "no-rtmd")] += 1
    gk = sorted(summary["i_by_group"])
    rtmd = [meta.get((g, "rtmd"), 0) for g in gk]
    nort = [meta.get((g, "no-rtmd"), 0) for g in gk]
    axes[0][0].bar(gk, rtmd, label="has rtmd", color="#2ca02c")
    axes[0][0].bar(gk, nort, bottom=rtmd, label="no rtmd", color="#888888")
    axes[0][0].legend(fontsize=7)
    axes[0][0].set_title("I: rtmd metadata track presence")
    axes[0][0].tick_params(axis="x", rotation=20)
    has_data = [1 if (r.get("n_data") or 0) > 0 else 0 for r in i_recs]
    axes[0][1].bar(["no data track", "has data track"],
                   [has_data.count(0), has_data.count(1)], color="#dd8452")
    axes[0][1].set_title("I: data-track presence (DJI djmd/dbgi/tmcd)")
    sizes = [summary["i_by_group"][g]["total_gib"] for g in gk]
    axes[1][0].bar(gk, sizes, color="#4c72b0")
    axes[1][0].set_title("I: total size per camera [GiB]")
    axes[1][0].tick_params(axis="x", rotation=20)
    durs = []
    for g in gk:
        rs = [r.get("duration_s") or 0 for r in i_recs if group_of(r) == g]
        durs.append(rs)
    axes[1][1].boxplot(durs, tick_labels=gk, showfliers=False)
    axes[1][1].set_title("I: duration per camera [s]")
    axes[1][1].tick_params(axis="x", rotation=20)
    fig.suptitle("Task5: I:\\2026.09.24 professional-camera inventory")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_i_inventory.png", dpi=130)
    plt.close(fig)

    # ---------------- G6 long-take / polarity / recovery summary
    fig, axes = plt.subplots(2, 2, figsize=(15, 8))
    try:
        drift = json.loads((SCRATCH / "drift_analysis.json").read_text(encoding="utf-8"))
        segs = drift.get("segments", {})
        labels, rates = [], []
        for k, v in segs.items():
            if not v.get("ok"):
                continue
            for i, s in enumerate(v["segments"]):
                labels.append(f"{k.split('_')[1]}/{k.split('_')[2]} seg{i + 1}")
                rates.append(s["slope_ppm"])
        axes[0][0].barh(labels, rates, color=["#2ca02c" if r > 0 else "#d62728"
                                              for r in rates])
        axes[0][0].set_xlabel("drift rate [ppm]")
        axes[0][0].set_title("4CH long takes: piecewise drift rates (BIC)")
        axes[0][0].tick_params(axis="y", labelsize=6)
    except (OSError, ValueError, KeyError) as exc:
        axes[0][0].set_title(f"drift data unavailable: {exc}")
    try:
        rec = json.loads((SCRATCH / "recovery.json").read_text(encoding="utf-8"))
        ka = rec.get("known_answer", {})
        names = sorted(k for k in ka if "const7" in k or "step30" in k)
        axes[0][1].bar(names, [ka[n]["steady_p90"] for n in names],
                       color="#1f77b4")
        axes[0][1].set_title("recovery: steady-state p90 error of injected "
                             "const/step [samples]")
        axes[0][1].tick_params(axis="x", rotation=20)
    except (OSError, ValueError, KeyError):
        axes[0][1].set_title("recovery data unavailable")
    try:
        pol = json.loads((SCRATCH / "polarity.json").read_text(encoding="utf-8"))
        stems = list(pol.keys())
        gains = [pol[s]["sum_rms_gain_db_median"] for s in stems]
        axes[1][0].bar([s[-4:] for s in stems], gains, color="#2ca02c")
        axes[1][0].set_ylabel("dB")
        axes[1][0].set_title("polarity repair: coherent-sum gain")
    except (OSError, ValueError, KeyError):
        axes[1][0].set_title("polarity data unavailable")
    try:
        rb = json.loads((SCRATCH / "drift_rebuild.json").read_text(encoding="utf-8"))
        ana = {(a["stem"], a["target"]): a for a in rb.get("analysis", [])}
        names, swings = [], []
        for (stem, target), a in ana.items():
            names.append(f"{stem[-4:]}/CH{target + 1}")
            swings.append(a.get("swing_samples") or 0)
        axes[1][1].bar(names, swings, color="#dd8452")
        axes[1][1].set_ylabel("samples")
        axes[1][1].set_title("long takes: total drift swing per channel")
        axes[1][1].tick_params(axis="x", rotation=20)
    except (OSError, ValueError, KeyError):
        axes[1][1].set_title("drift rebuild data unavailable")
    fig.suptitle("4CH long takes: drift / recovery / polarity summary")
    fig.tight_layout()
    fig.savefig(PLOT / "task2_longtake_summary.png", dpi=130)
    plt.close(fig)

    # 写入 summary
    (WORK / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    lines = ["# 任务1/2 数据汇总", ""]
    for key, val in summary.items():
        if isinstance(val, dict):
            lines.append(f"## {key}")
            for k, v in val.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        else:
            lines.append(f"- {key}: {val}")
    (WORK / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {WORK / 'summary.json'} / summary.md")
    print(json.dumps({k: summary[k] for k in (
        "testsets_before_2ch_policy", "testsets_after_2ch_policy",
        "i_20260924")}, ensure_ascii=False, indent=1, default=str)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
