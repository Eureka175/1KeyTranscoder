#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the Gyroflow consumer-check summary (and extend it with NX800 later)."""
from __future__ import annotations

import json
from pathlib import Path

P = Path(r"F:\1KeyTranscoder\data\sony_meta_crosscam\gyroflow_consumer_check.json")
rows = json.loads(P.read_text(encoding="utf-8"))
for r in rows:
    rep = r.get("report") or {}
    label = r.get("label")
    status = rep.get("status") or "ERROR"
    detail = rep.get("detail") or rep.get("error") or ""
    print(f"{label:<14} {status:<6} {detail}")
    o, f = rep.get("original") or {}, rep.get("final") or {}
    if o and f:
        print(f"{'':<14} IMU {o.get('imu_samples')} -> {f.get('imu_samples')}  "
              f"model {o.get('detected_source')} -> {f.get('detected_source')}  "
              f"lens_profile {o.get('has_lens_profile')} -> "
              f"{f.get('has_lens_profile')}")
        mm = rep.get("mismatches") or {}
        if mm:
            print(f"{'':<14} MISMATCH {mm}")
