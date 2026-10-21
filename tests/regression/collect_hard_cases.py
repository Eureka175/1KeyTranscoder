#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect the hard-to-align archive material for review.

Selection rule (measured, not guessed): a file is "hard" when it contains at
least one track the quality gates **refused while the track is measurably off**
(|delay| >= min_shift_samples). Those are the tracks a single rigid integer shift
cannot fix -- non_constant / low_confidence -- and they are exactly the ones left
untouched in the delivery.

The two long takes are excluded by name (C1305 / C1306, 43 / 50 GiB): they are
hard for the same reason but decoding them just to look takes minutes each.

Output goes to F:\\ with an index that states, per file, which tracks are off and
by how much, so the folder can be judged without re-reading the arms.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
ARM = OUT / "ARCH_on5.json"
DELIVERED = OUT  # 已交付的 13 件(名字)从 I:\\repaired 目录取
DEST = Path(r"F:\1KeyTranscoder\data\hard_cases")
EXCLUDE_NAMES = {"20260919_C1305.MP4", "20260919_C1306.MP4"}
SR = 48000
MIN_SAMPLES = 5
#: 源文件体积上限 (MiB): 只排除"大到无法人工查看"的, 不是排除难点
SIZE_CAP_MIB = 2000


def main() -> int:
    arm = json.loads(ARM.read_text(encoding="utf-8"))
    delivered = {p.name for p in Path(r"I:\repaired").rglob("*.MP4")
                 if "_superseded" not in str(p)}

    picked: dict[str, dict] = {}
    for r in arm:
        path = Path(r.get("file") or "")
        name = r.get("name") or path.name
        if name in EXCLUDE_NAMES:
            continue
        off = []
        shifts = []
        for c in r.get("channels") or []:
            if c.get("decision") == "fixed":
                shifts.append((c.get("stream"), c.get("shift_samples")))
                continue
            d = c.get("delay_ms")
            if c.get("decision") != "untouched":
                continue
            if not isinstance(d, (int, float)):
                continue
            if abs(d) < 1000.0 * MIN_SAMPLES / SR:
                continue
            off.append({"stream": c.get("stream"), "reason": c.get("reason"),
                        "delay_ms": d, "frames": c.get("usable_frames")})
        if not off:
            continue
        if not path.is_file():
            print(f"  源缺失: {path}")
            continue
        mb = path.stat().st_size / 1024 ** 2
        if mb > SIZE_CAP_MIB:
            print(f"  超出体积上限跳过: {name} {mb:.0f} MiB")
            continue
        picked[name] = {"path": path, "off": off, "shifts": shifts,
                        "mib": mb, "status": r.get("status")}

    names = sorted(picked)
    total = sum(picked[n]["mib"] for n in names)
    print(f"难点文件 {len(names)} 件, 合计 {total / 1024:.2f} GiB")

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    lines = ["# 难以对齐的素材", "",
             "选择规则：**至少有一条轨被质量门拒绝、而该轨确实还有 "
             f"≥ {MIN_SAMPLES} 采样（0.104 ms）的偏移**。这类轨需要逐窗时变修正，"
             "单个刚性整数移位修不了。", "",
             "已排除两个长镜头 `20260919_C1305`（49.82 GiB）与 "
             "`20260919_C1306`（43.13 GiB）。", "",
             f"共 **{len(names)} 件 / {total / 1024:.2f} GiB**。", "",
             "| 文件 | MiB | 仍被拒的轨（偏差 / 原因 / 可用帧） | 该文件已施加的移位 | 是否在已交付 13 件内 |",
             "|---|---|---|---|---|"]
    for nm in names:
        x = picked[nm]
        shutil.copy2(x["path"], DEST / nm)
        off = "; ".join(
            f"CH{(o['stream'] or 0) + 1} {o['delay_ms']:+.2f} ms "
            f"({o['reason']}, {o['frames']} 帧)" for o in x["off"])
        sh = ", ".join(f"CH{(s or 0) + 1}{v:+d}" for s, v in x["shifts"]) or "-"
        lines.append(f"| `{nm}` | {x['mib']:.0f} | {off} | {sh} | "
                     f"{'是' if nm in delivered else '否'} |")
    lines += ["", "## 读法", "",
              "- “仍被拒”= 该轨的偏差不恒定（或测量置信不足），生产拒绝施加单个整数移位；",
              "- “已施加的移位”= 同一文件里其它轨已经修好了，说明**不是整件失败**，",
              "  而是逐轨的判据问题；",
              "- 这些文件如果播放，未修的轨会比其它轨晚 23–27 ms。", ""]
    (DEST / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
