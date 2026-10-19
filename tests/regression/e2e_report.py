#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the check-level and metadata facts collected by the e2e batch."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "work" / "regression_r087" / "out"


def main() -> int:
    runs = json.loads((OUT / "e2e_runs.json").read_text(encoding="utf-8"))
    print(f"{'id':<4}{'clip':<24}{'stratum':<20}{'lvl':<9}"
          f"{'check':<10}{'P':>4}{'M':>4}{'X':>4}{'quality':<24}"
          f"{'uuid':<12}{'meta':<6}")
    print("-" * 118)
    for r in runs:
        lf = r.get("log_facts") or {}
        ck = lf.get("check") or {}
        u = lf.get("uuid") or {}
        print(f"{r['id']:<4}{r['clip']:<24}{r['stratum']:<20}"
              f"{r['level']:<9}{str(ck.get('structural_success')):<10}"
              f"{str(ck.get('PRESERVED')):>4}{str(ck.get('MODIFIED')):>4}"
              f"{str(ck.get('MISSING')):>4}"
              f"{str(ck.get('quality')):<24}"
              f"{str(u.get('inserted')) + '/' + str(u.get('gpac_present')):<12}"
              f"{str(lf.get('sony_meta_tracks')):<6}")
    print()
    for r in runs:
        lf = r.get("log_facts") or {}
        v = r.get("verify") or {}
        print(f"[{r['id']}] status={r['status']} shifts={r['shifts']} "
              f"anchor={r['anchor']} bytecopy={r['byte_identical']} "
              f"order={lf.get('remux_order')}")
        print(f"     check_status={lf.get('check_status')} "
              f"selfcheck={lf.get('selfcheck')}")
        print(f"     sync_ok={(lf.get('sync_ok') or '')[:120]}")
        print(f"     check_na_info={lf.get('check_na_info')} "
              f"sync_skip={(lf.get('sync_skip') or '')[:80]}")
        rv = v.get("residual_verdict") or {}
        for k, x in rv.items():
            print(f"     {k:<10} {x.get('verdict'):<14} "
                  f"shift={x.get('shift')} exp={x.get('expected_out_samples')} "
                  f"obs={x.get('observed_out_samples')} "
                  f"delta={x.get('accounting_delta_samples')}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
