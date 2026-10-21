#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify every refused track on the 4ch-proof set into two causes.

The proof folder settles the data question: all four channels carry valid audio
(levels well above the silence floor, ~0% silent frames). So a refused track on
one of those clips is either

  * **no shared content** -- its best partner over the production grouping metric
    (`sample_pair_snr`, threshold 1.3) is below the threshold. There is nothing
    to correlate, so no anchor choice, gate change or per-window correction can
    align it. That is a property of the recording, not of the software.
  * **decision problem** -- it does have a partner above the threshold, yet the
    pipeline left it untouched. Those are the tracks a better decision or a
    per-window correction could actually recover.

The split decides what the feasibility plan should spend effort on, so it is
worth computing per track rather than arguing about it.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"F:\1KeyTranscoder\work\regression_r087\out")
ARM = OUT / "ARCH_on5.json"
SNR = OUT / "proof_pair_snr.json"
THRESHOLD = 1.3
SR = 48000
MIN_SAMPLES = 5


def main() -> int:
    arm = {r.get("name"): r for r in json.loads(ARM.read_text(encoding="utf-8"))}
    snr = {d["clip"]: d for d in json.loads(SNR.read_text(encoding="utf-8"))}

    rows = []
    for clip, d in sorted(snr.items()):
        r = arm.get(clip + ".MP4") or arm.get(clip)
        if r is None:
            print(f"{clip}: arm 里没有")
            continue
        for c in r.get("channels") or []:
            ch = (c.get("stream") or 0) + 1
            best = (d["best"].get(f"CH{ch}") or {})
            bsnr = best.get("snr")
            rows.append({
                "clip": clip, "ch": ch,
                "decision": c.get("decision"), "reason": c.get("reason"),
                "delay_ms": c.get("delay_ms"),
                "shift": c.get("shift_samples"),
                "frames": c.get("usable_frames"),
                "best_snr": bsnr,
                "partner": best.get("partner"),
            })

    refused = [x for x in rows
               if x["decision"] == "untouched"
               and isinstance(x["delay_ms"], (int, float))
               and abs(x["delay_ms"]) >= 1000 * MIN_SAMPLES / SR]
    no_content = [x for x in refused
                  if not (isinstance(x["best_snr"], (int, float))
                          and x["best_snr"] >= THRESHOLD)]
    decision = [x for x in refused if x not in no_content]

    print(f"证明集: {len(snr)} 件 / {len(rows)} 轨")
    kinds: dict[str, int] = {}
    for x in rows:
        kinds[x["decision"]] = kinds.get(x["decision"], 0) + 1
    print(f"生产判定: {kinds}")
    print(f"被拒且偏差 ≥ {MIN_SAMPLES} 采样: {len(refused)} 条")
    print(f"  其中 无共同内容(SNR<{THRESHOLD}) : {len(no_content)}")
    print(f"  其中 有伙伴却被拒(决策问题)     : {len(decision)}")

    if decision:
        print(f"\n{'clip':<22}{'ch':<4}{'reason':<18}{'偏差ms':>9}"
              f"{'伙伴':>6}{'SNR':>7}{'可用帧':>8}")
        print("-" * 76)
        for x in sorted(decision, key=lambda y: -abs(y["delay_ms"])):
            print(f"{x['clip']:<22}CH{x['ch']:<3}{str(x['reason']):<18}"
                  f"{x['delay_ms']:>9.2f}CH{x['partner']:<4}"
                  f"{x['best_snr']:>7.2f}{str(x['frames']):>8}")
    if no_content:
        print(f"\n无共同内容的轨（任何算法都无法对齐）:")
        for x in sorted(no_content, key=lambda y: -abs(y["delay_ms"])):
            print(f"  {x['clip']} CH{x['ch']} {x['delay_ms']:+.2f}ms "
                  f"({x['reason']}, 最佳伙伴 CH{x['partner']} "
                  f"SNR={x['best_snr']})")

    (OUT / "proof_classification.json").write_text(
        json.dumps({"rows": rows, "refused": refused,
                    "no_content": no_content, "decision": decision},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
