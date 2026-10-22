#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise the container-reproduction results from the investigation checkpoints."""
from __future__ import annotations

import json
from pathlib import Path

B = Path(r"E:\1kt_work\inv_2026_09_24\files")
agg: dict[str, dict] = {}
samples: dict[str, dict] = {}
for f in sorted(B.glob("*.json")):
    r = json.loads(f.read_text(encoding="utf-8"))
    c = r.get("container") or {}
    g = r.get("group") or "?"
    v = (c.get("variants") or {}).get("plain") or {}
    a = agg.setdefault(g, {"n": 0, "ok": 0, "faithful": 0, "match": 0,
                           "box": 0, "brand": 0, "maps": set(),
                           "dropped": set(), "diffs": set()})
    a["n"] += 1
    if v.get("ok"):
        a["ok"] += 1
    if v.get("faithful"):
        a["faithful"] += 1
    if v.get("streams_match"):
        a["match"] += 1
    if v.get("ok") and not v.get("box_differs"):
        a["box"] += 1
    if v.get("ok") and not v.get("brand_differs"):
        a["brand"] += 1
    a["maps"].add(v.get("map"))
    for x in (v.get("dropped_streams") or []):
        a["dropped"].add(x.get("type"))
    for d in (v.get("stream_diffs") or []):
        a["diffs"].add(d.get("field") or "missing")
    if g not in samples and v.get("ok"):
        samples[g] = {"name": r.get("name"), "v": v,
                      "boxes": c.get("source_boxes"),
                      "brand": c.get("source_brand"),
                      "data_src": c.get("data_streams_source"),
                      "data_kept": c.get("data_streams_kept")}

print("容器复现验证（plain 变体，每机位汇总）\n")
print(f"{'机位':<12}{'文件':>4}{'重封成功':>8}{'全部流忠实':>10}"
      f"{'流字段一致':>10}{'box一致':>8}{'brand一致':>9}  映射/丢弃/差异字段")
for g, a in sorted(agg.items()):
    print(f"{g:<12}{a['n']:>4}{a['ok']:>8}{a['faithful']:>10}"
          f"{a['match']:>10}{a['box']:>8}{a['brand']:>9}  "
          f"map={sorted(x for x in a['maps'] if x)} "
          f"drop={sorted(a['dropped']) or '-'} "
          f"diffs={sorted(a['diffs']) or '-'}")

print("\n每机位样本明细\n")
for g, s in sorted(samples.items()):
    v = s["v"]
    print(f"{g}: {s['name']}")
    print(f"  源 box: {s['boxes']}  brand: {s['brand']}  "
          f"data 轨 源/保留: {s['data_src']}/{s['data_kept']}")
    print(f"  重封: map={v.get('map')} faithful={v.get('faithful')} "
          f"streams_match={v.get('streams_match')} "
          f"box_differs={v.get('box_differs')} "
          f"brand_differs={v.get('brand_differs')}")
    for d in (v.get("stream_diffs") or [])[:6]:
        print(f"    差异 {d.get('kind')}{d.get('n')}: {d.get('field')} "
              f"{d.get('source')!r} -> {d.get('output')!r}")
