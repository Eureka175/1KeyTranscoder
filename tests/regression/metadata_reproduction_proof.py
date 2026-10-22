#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove metadata preservation through the production repair path.

`archive_repair.repair_one` is the production container-level pipeline: it remuxes
via GPAC, then `preservation.transparent_restore.restore_after_transparent_remux`
puts back the Sony bundle (brand, tref/cdsc, nrtm meta item, vendor uuid boxes),
restores the layout, and *verifies* uuid/brand/tref/nrtm equality -- refusing to
deliver when they differ.

This runs it on:
  * a proof-set source that actually needs a remux (C1159), the case where the
    restoration matters, and
  * two of the I:\\2026.09.24 sources (an already-aligned one and a DJI one), where
    production should just byte-copy.

No number here is inferred: everything is read from repair_one's own report.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(r"F:\1KeyTranscoder")
sys.path.insert(0, str(REPO))

import archive_repair as ar                      # noqa: E402
import core.channel_sync as cs                   # noqa: E402

OUT = Path(r"E:\1kt_work\metadata_proof")
OUT.mkdir(parents=True, exist_ok=True)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(src: Path, label: str) -> dict:
    dst = OUT / f"{label}_{src.stem}.mp4"
    if dst.exists():
        dst.unlink()
    logs: list[str] = []
    facts = ar.container_facts(src)
    rec = ar.repair_one(src, dst, opts=cs.production_opts(),
                        want_moov_first=bool(facts.get("moov_first")),
                        log=logs.append)
    out = {
        "src": src.name,
        "status": rec.get("status"),
        "file_copied": rec.get("file_copied"),
        "metadata": rec.get("metadata"),
        "top_level_before": rec.get("top_level_before"),
        "top_level_after": rec.get("top_level_after"),
        "layout": rec.get("layout_detail"),
        "elapsed_s": rec.get("elapsed_s"),
        "error": rec.get("error"),
        "workflow": rec.get("workflow"),
        "output_exists": dst.is_file(),
    }
    if dst.is_file():
        out["dst_size"] = dst.stat().st_size
        out["src_size"] = src.stat().st_size
        out["same_bytes"] = sha(src) == sha(dst)
        dst.unlink()
    out["log_tail"] = logs[-8:]
    return out


def main() -> int:
    cases = [
        (Path(r"F:\1KeyTranscoder\data\align_4ch_proof\20260903_C1159.MP4"),
         "proof_needs_fix"),
        (Path(r"I:\2026.09.24\FX3\C0513.MP4"), "folder_2ch"),
        (Path(r"I:\2026.09.24\Mavic4P\DJI_20260921164928_0296_D.MP4"),
         "folder_dji"),
    ]
    results = {}
    for src, label in cases:
        if not src.is_file():
            print(f"缺文件: {src}")
            continue
        print(f"\n=== {label}: {src.name} ({src.stat().st_size / 2**30:.2f} GiB)")
        r = run(src, label)
        results[label] = r
        print(f"  status={r['status']} file_copied={r['file_copied']} "
              f"same_bytes={r.get('same_bytes')} 用时={r['elapsed_s']}s")
        md = r.get("metadata") or {}
        print(f"  metadata.identical={md.get('identical')} "
              f"ftyp {md.get('ftyp_before')}->{md.get('ftyp_after')} "
              f"tref_same={md.get('tref_same')} "
              f"nrtm {md.get('nrtm_before')}->{md.get('nrtm_after')} "
              f"uuid_equal={(md.get('uuid') or {}).get('equal')} "
              f"(missing={(md.get('uuid') or {}).get('missing')}, "
              f"added={(md.get('uuid') or {}).get('added')})")
        print(f"  top_level {r['top_level_before']} -> {r['top_level_after']}")
        print(f"  layout: {r['layout']}")
        if r.get("error"):
            print(f"  error: {r['error']}")
    (OUT / "metadata_proof.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {OUT / 'metadata_proof.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
