#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consumer-level proof: does Gyroflow read the same metadata from the restored file?

`preservation.gyroflow.check` compares what the consumer parses out of the original
against the restored file (IMU sample count, model, lens profile, timecode facts).
This is the check that matters: the failure mode that motivated the preservation
pipeline was Gyroflow going from 146146 IMU samples to 0 when container metadata was
lost, while the rtmd payload was byte-identical.

Runs on the artefacts already produced by sony_metadata_crosscam.py. No encoding.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

from preservation.gyroflow import check, find_gyroflow    # noqa: E402

OUT = REPO / "data" / "sony_meta_crosscam"
# F: 空间清理后, 往返成品已搬到 E:; 两处任一存在即可
RESTORED_ROOTS = [OUT / "work",
                  Path(r"E:\1kt_relocated_20261023\sony_meta_work")]
SCRATCH = Path(r"E:\1kt_scratch\gyro_check")

def _restored(name: str) -> Path:
    for root in RESTORED_ROOTS:
        cand = root / name.strip('"')
        if cand.is_dir():
            return cand
    return RESTORED_ROOTS[0] / name.strip('"')


CASES = [
    ("A7M5(control)",
     REPO / "data" / "align_4ch_proof" / "20260903_C1159.MP4",
     _restored("A7M5_control") / "restored.MP4"),
    ("A7M4",
     REPO / "data" / "_from_I" / "2026.09.24" / "A7M4" / "C0147.MP4",
     _restored("A7M4") / "restored.MP4"),
    ("FX3",
     REPO / "data" / "_from_I" / "2026.09.24" / "FX3" / "C0513.MP4",
     _restored("FX3") / "restored.MP4"),
    ("FX30",
     REPO / "data" / "_from_I" / "2026.09.24" / "FX30" / "20260922_fx30_2_9757.MP4",
     _restored("FX30") / "restored.MP4"),
    ("HXR-NX800",
     Path(r"F:\1KeyTranscoder\data\_from_I\2026.09.24\HXR-NX800\CA0230.MP4"),
     Path(r"E:\1kt_scratch\sony_meta_nx800\HXR-NX800\restored.MP4")),
]


def main() -> int:
    gf = find_gyroflow()
    if gf is None:
        print("Gyroflow 未找到")
        return 1
    print(f"Gyroflow: {gf}")
    results = []
    for label, original, final in CASES:
        if not original.is_file():
            print(f"\n=== {label}: 源缺失 {original}")
            continue
        if not final.is_file():
            print(f"\n=== {label}: 成品缺失 {final}")
            continue
        scratch = SCRATCH / label.replace("(", "_").replace(")", "")
        scratch.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {label}: {original.name} vs {final.name}")
        try:
            r = check(original, final, gf, scratch)
        except Exception as exc:                    # noqa: BLE001
            print(f"  检查失败: {type(exc).__name__}: {exc}")
            results.append({"label": label,
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"label": label, "report": r})
        print(json.dumps(r, ensure_ascii=False, indent=1)[:1400])
    (OUT / "gyroflow_consumer_check.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'gyroflow_consumer_check.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
