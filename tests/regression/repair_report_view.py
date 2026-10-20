#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the archive repair report as a compact, checkable table."""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(r"E:\1kt_archive_repaired")


def num(v, fmt: str = "{:.1f}") -> str:
    if isinstance(v, (int, float)):
        return fmt.format(v)
    return str(v)


def main() -> int:
    d = json.loads((OUT / "_REPAIR-REPORT.json").read_text(encoding="utf-8"))
    rows = d["rows"]
    print(f"selected={d['files_selected']} repaired={d['files_repaired']} "
          f"tracks_moved={d['tracks_moved']} "
          f"structural_failures={len(d['structural_failures'])}")
    print()
    hdr = (f"{'file':<30}{'shift':>7}{'after':>8}{'delta':>7}  "
           f"{'order':<5}{'ftyp':<5}{'tref':<5}{'nrtm':<11}{'uuid':<8}"
           f"{'dur':>10}  {'gyro':<6}{'sel==rpr':<9}")
    print(hdr)
    print("-" * len(hdr))
    bad = []
    for r in rows:
        v = r.get("verify") or {}
        st = v.get("structure_match") or {}
        rv = v.get("residual_verdict") or {}
        sh = r.get("decisions", {}).get("shifts") or {}
        for k, x in rv.items():
            if not x.get("shift"):
                continue
            ok = x.get("verdict") == "PASS"
            if not ok:
                bad.append((r["dst"], k, x))
            print(f"{Path(r['dst']).name:<30}"
                  f"{x['shift']:>+7d}"
                  f"{num(x.get('observed_out_samples')):>8}"
                  f"{num(x.get('accounting_delta_samples')):>7}  "
                  f"{'SAME' if v.get('stream_order_preserved') else 'DIFF':<5}"
                  f"{'SAME' if st.get('ftyp_major_brand_same') else 'DIFF':<5}"
                  f"{'SAME' if st.get('tref_cdsc_same') else 'DIFF':<5}"
                  f"{str(st.get('nrtm_src')) + '->' + str(st.get('nrtm_out')):<11}"
                  f"{str(v.get('uuid', {}).get('src_count')) + '->' + str(v.get('uuid', {}).get('out_count')):<8}"
                  f"{num((v.get('format') or {}).get('duration_delta_s'), '{:+.6f}'):>10}  "
                  f"{str((v.get('gyroflow') or {}).get('status')):<6}"
                  f"{str(r.get('selection_matches_repair')):<9}")
    mism = [r for r in rows if r.get("selection_matches_repair") is False]
    print()
    print(f"residual failures: {len(bad)}")
    for b in bad:
        print("  FAIL", b[0], b[1], b[2])
    print(f"selection/repair mismatches: {len(mism)}")
    for m in mism:
        print("  MISMATCH", m["src"], m.get("selection_shifts"),
              "vs", (m.get("decisions") or {}).get("shifts"))
    total = sum(Path(r["dst"]).stat().st_size for r in rows
                if Path(r["dst"]).is_file())
    src_total = sum(r["src_size"] for r in rows)
    print(f"\nstaged size {total / 2**30:.2f} GiB "
          f"(originals {src_total / 2**30:.2f} GiB, "
          f"delta {(total - src_total) / 2**20:+.1f} MiB)")
    return 0 if not bad and not mism and not d["structural_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
