#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collate every regression artefact into one machine-readable matrix.

The document quotes this file. Nothing here re-computes a verdict — it only
reads what the individual tiers already wrote, so the document and the raw
artefacts cannot drift apart.

Usage:
    python collate.py            # writes out/MATRIX.json and prints a table
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "work" / "regression_r087"
OUT = HERE / "out"
LOGS = HERE / "logs"


def _json(p: Path) -> dict | list | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _summary_line(p: Path) -> str:
    if not p.is_file():
        return ""
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "SUMMARY:" in ln:
            return ln.strip()
    return ""


def tier_autotest() -> dict:
    rows = {}
    for name, f in (("L1_unit", "F_L1.log"),
                    ("L2_toolchain", "F_L2.log")):
        line = _summary_line(LOGS / f)
        m = re.search(r"SUMMARY:\s*(\d+) PASS / (\d+) FAIL.*?\(([\d.]+)s\)",
                      line)
        rows[name] = {
            "pass": int(m.group(1)) if m else None,
            "fail": int(m.group(2)) if m else None,
            "elapsed_s": float(m.group(3)) if m else None,
            "summary": line,
        }
    return rows


def tier_l3() -> dict:
    p = LOGS / "F_L3.log"
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"PASS (\d+) / FAIL (\d+)", text)
    fails = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("FAIL")]
    return {"pass": int(m.group(1)) if m else None,
            "fail": int(m.group(2)) if m else None,
            "failures": fails}


def tier_p4() -> dict:
    d = _json(OUT / "p4_suites.json")
    if d is None:
        p = LOGS / "test_production_semantics.json"
        d = _json(p)
    rows = _json(LOGS / "p4_suites.json")
    if rows is None:
        # reconstruct from the per-script jsons
        out = []
        for name in ("test_baseline_hash_portability", "test_downstream_state",
                     "test_evidence_integrity", "test_production_eligibility",
                     "test_production_semantics", "test_production_sync",
                     "test_time_varying_exec"):
            j = _json(LOGS / f"{name}.json")
            log = LOGS / f"{name}.log"
            verdict = ""
            if log.is_file():
                for ln in log.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
                    if ln.startswith("RESULT:"):
                        verdict = ln.strip()
            out.append({"script": name + ".py",
                        "cases": (j or {}).get("cases"),
                        "fail": (j or {}).get("fail"),
                        "counts": (j or {}).get("counts"),
                        "verdict": verdict})
        return {"script": out}
    return {"script": rows.get("scripts", [])}


def tier_arms() -> dict:
    res = {}
    for label, f in (("A1_off_vs_baseline", "cmp_A1.json"),
                     ("A3A4_on_vs_off", "cmp_A3A4.json"),
                     ("holdout_on_vs_off", "cmp_holdout.json")):
        d = _json(OUT / f)
        if d is None:
            continue
        res[label] = {k: d.get(k) for k in (
            "files_common", "files_identical", "files_with_diff",
            "tracks_a", "tracks_b", "fixed_tracks_a", "fixed_tracks_b",
            "status_transitions", "A3_lost_fix_zero", "A4_changed_value_zero",
            "A1_field_identical_all", "secondary_anchor_rows_b")}
        res[label]["lost_fix"] = d.get("lost_fix")
        res[label]["added_fix"] = d.get("added_fix")
        res[label]["changed_value"] = d.get("changed_value")
        res[label]["reclassified"] = d.get("reclassified_untouched_to_anchor")
    return res


def tier_e2e() -> dict:
    runs = _json(OUT / "e2e_runs.json")
    if not isinstance(runs, list):
        return {}
    rows = []
    for r in runs:
        v = r.get("verify") or {}
        st = v.get("structure_match") or {}
        rv = v.get("residual_verdict") or {}
        lf = r.get("log_facts") or {}
        rows.append({
            "id": r["id"], "clip": r["clip"], "stratum": r["stratum"],
            "level": r["level"], "status": r["status"],
            "shifts": r["shifts"], "anchor": r.get("anchor"),
            "byte_identical": r["byte_identical"],
            "stream_order_preserved": v.get("stream_order_preserved"),
            "streams_changed": v.get("streams_changed"),
            "streams_lost": v.get("streams_lost"),
            "ftyp_same": st.get("ftyp_major_brand_same"),
            "ftyp": f"{st.get('ftyp_major_brand_src')}->"
                    f"{st.get('ftyp_major_brand_out')}",
            "tref_same": st.get("tref_cdsc_same"),
            "nrtm": f"{st.get('nrtm_src')}->{st.get('nrtm_out')}",
            "uuid": f"{v.get('uuid', {}).get('src_count')}->"
                    f"{v.get('uuid', {}).get('out_count')}",
            "uuid_missing": v.get("uuid", {}).get("missing"),
            "duration_delta_s": (v.get("format") or {}).get(
                "duration_delta_s"),
            "residual": {k: x.get("verdict") for k, x in rv.items()},
            "residual_max_abs_delta": max(
                (abs(x.get("accounting_delta_samples") or 0)
                 for x in rv.values() if x.get("ok") is not None),
                default=None),
            "gyroflow": (v.get("gyroflow") or {}).get("status"),
            "check": lf.get("check"),
            "selfcheck": lf.get("selfcheck"),
            "remux_order": lf.get("remux_order"),
            "uuid_inserts": lf.get("uuid"),
        })
    return {"runs": rows}


def tier_plumbing() -> dict:
    d = _json(OUT / "plumbing.json")
    if d is None:
        return {}
    fw = d.get("forwarding") or {}
    sb = d.get("start_bat") or {}
    cli = d.get("cli") or {}
    idem = d.get("idempotency") or {}
    br = d.get("broken") or {}
    return {
        "forwarding": {"rows": fw.get("rows"), "all_match": fw.get("all_match")},
        "start_bat": {"combos": sb.get("combos"),
                      "defaults": sb.get("defaults_declared"),
                      "all_match": sb.get("all_match")},
        "cli": {"rows": cli.get("rows"), "all_match": cli.get("all_match")},
        "idempotency": idem,
        "broken_inputs": br,
    }


def main() -> int:
    matrix = {
        "version": (HERE.parents[1] / "VERSION").read_text(
            encoding="utf-8").strip(),
        "tiers": {
            "T1_T2_autotest": tier_autotest(),
            "T3_L3_channel_sync": tier_l3(),
            "T4_p4_audits": tier_p4(),
            "T5_T6_T7_arms": tier_arms(),
            "T8_T9_e2e": tier_e2e(),
            "T10_plumbing": tier_plumbing(),
        },
    }
    dest = OUT / "MATRIX.json"
    dest.write_text(json.dumps(matrix, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(json.dumps(matrix, ensure_ascii=False, indent=1)[:6000])
    print(f"\n-> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
