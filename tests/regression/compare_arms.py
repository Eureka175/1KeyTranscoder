#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Field-exact comparison of two channel-sync arms.

The verdicts this produces map 1:1 onto the plan's acceptance criteria:

  A1  default behaviour unchanged   -> OFF today vs the frozen baseline
  A3  zero regression               -> lost_fix   == 0
  A4  zero worse                    -> changed_value == 0

`lost_fix` is a track that arm A corrected and arm B left alone.
`changed_value` is a track both arms corrected by *different amounts*.
Neither is allowed to happen for the grouped-anchor feature, which is
defined as **additive**.

Usage:
    python compare_arms.py --a base.json --b cand.json --label A1 --json out.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

FIELDS = ("decision", "reason", "delay_ms", "delay_samples",
          "shift_samples", "usable_frames", "confidence")


def load(p: Path) -> dict[str, dict]:
    return {r["name"]: r for r in json.loads(p.read_text(encoding="utf-8"))}


def anchor_of(rec: dict):
    """Primary anchor only.

    With grouping enabled a record can legitimately contain **two** rows with
    ``decision == "anchor"`` (one primary, one secondary).  Taking "the first
    row that says anchor" would then report a spurious anchor change on every
    grouped file, so the secondary designation is excluded explicitly and the
    primary is what gets compared.
    """
    chans = rec.get("channels") or []
    for c in chans:
        if (c.get("decision") == "anchor"
                and c.get("anchor_role") != "secondary"):
            return c.get("stream")
    return None


def secondary_anchors(rec: dict) -> list[int]:
    return sorted(c.get("stream") for c in (rec.get("channels") or [])
                  if c.get("decision") == "anchor"
                  and c.get("anchor_role") == "secondary")


def chan_map(rec: dict) -> dict[int, dict]:
    return {c.get("stream"): c for c in (rec.get("channels") or [])}


def shifts_of(rec: dict) -> dict[int, int]:
    return {c.get("stream"): int(c.get("shift_samples") or 0)
            for c in (rec.get("channels") or [])
            if c.get("decision") == "fixed"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="baseline arm")
    ap.add_argument("--b", required=True, help="candidate arm")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args()

    A, B = load(Path(a.a)), load(Path(a.b))
    common = sorted(set(A) & set(B))

    rows = []
    diffs: list[dict] = []
    status_kind: Counter = Counter()
    lost_fix: list[str] = []
    added_fix: list[str] = []
    changed_value: list[str] = []
    reclassified: list[str] = []
    secondary_anchor_rows = 0
    n_same = 0
    fix_a = fix_b = 0
    recheck_demotions = 0

    for name in common:
        ra, rb = A[name], B[name]
        fa, fb = shifts_of(ra), shifts_of(rb)
        fix_a += len(fa)
        fix_b += len(fb)
        secondary_anchor_rows += len(secondary_anchors(rb))
        rec_diffs = []
        if ra.get("status") != rb.get("status"):
            rec_diffs.append(f"status {ra.get('status')!r} -> "
                             f"{rb.get('status')!r}")
            status_kind[f"{ra.get('status')}->{rb.get('status')}"] += 1
        if anchor_of(ra) != anchor_of(rb):
            rec_diffs.append(f"anchor {anchor_of(ra)} -> {anchor_of(rb)}")
        ca, cb = chan_map(ra), chan_map(rb)
        for s in sorted(set(ca) | set(cb)):
            x, y = ca.get(s) or {}, cb.get(s) or {}
            for f in FIELDS:
                if x.get(f) != y.get(f):
                    # A track that merely becomes a *group reference* did not
                    # move: report it as a reclassification, not a behaviour
                    # change. It is not silent either — `audio_models` maps
                    # anchor -> ALREADY_ALIGNED while untouched -> FAILED, so
                    # this is visible downstream.
                    if (f == "decision" and x.get(f) == "untouched"
                            and y.get(f) == "anchor"):
                        reclassified.append(f"{name} CH{s + 1}")
                        continue
                    rec_diffs.append(f"CH{s + 1}.{f} {x.get(f)!r} -> "
                                     f"{y.get(f)!r}")
        for s in sorted(set(fa) - set(fb)):
            lost_fix.append(f"{name} CH{s + 1} (was {fa[s]})")
        for s in sorted(set(fb) - set(fa)):
            added_fix.append(f"{name} CH{s + 1} ({fb[s]})")
        for s in sorted(set(fa) & set(fb)):
            if fa[s] != fb[s]:
                changed_value.append(f"{name} CH{s + 1} {fa[s]} -> {fb[s]}")
        if rec_diffs:
            diffs.append({"name": name, "a": ra.get("status"),
                          "b": rb.get("status"), "diffs": rec_diffs})
        else:
            n_same += 1
        rows.append({"name": name, "status_a": ra.get("status"),
                     "status_b": rb.get("status"),
                     "fixed_a": fa, "fixed_b": fb,
                     "identical": not rec_diffs})

    # per-track totals make the "density not count" story checkable
    tracks_a = tracks_b = 0
    for name in common:
        tracks_a += len(chan_map(A[name]))
        tracks_b += len(chan_map(B[name]))

    verdict = {
        "label": a.label,
        "a": str(a.a), "b": str(a.b),
        "files_common": len(common),
        "files_only_a": sorted(set(A) - set(B)),
        "files_only_b": sorted(set(B) - set(A)),
        "files_identical": n_same,
        "files_with_diff": len(diffs),
        "tracks_a": tracks_a, "tracks_b": tracks_b,
        "fixed_tracks_a": fix_a, "fixed_tracks_b": fix_b,
        "status_transitions": dict(status_kind),
        "lost_fix": lost_fix,
        "added_fix": added_fix,
        "changed_value": changed_value,
        "reclassified_untouched_to_anchor": reclassified,
        "secondary_anchor_rows_b": secondary_anchor_rows,
        "A3_lost_fix_zero": not lost_fix,
        "A4_changed_value_zero": not changed_value,
        "A1_field_identical_all": n_same == len(common),
        "diffs": diffs,
    }
    print(f"== {a.label or 'compare'} ==")
    print(f"A={Path(a.a).name} ({len(A)})  B={Path(a.b).name} ({len(B)})")
    print(f"common={len(common)}  identical={n_same}  differing={len(diffs)}")
    print(f"tracks: A={tracks_a} B={tracks_b}   fixed: A={fix_a} B={fix_b}")
    print(f"status transitions: {dict(status_kind) or '{}'}")
    print(f"lost_fix={len(lost_fix)}  added_fix={len(added_fix)}  "
          f"changed_value={len(changed_value)}  "
          f"reclassified(untouched->anchor)={len(reclassified)}")
    print(f"secondary-anchor rows in B: {secondary_anchor_rows}")
    for line in lost_fix[:a.show]:
        print("  LOST   ", line)
    for line in added_fix[:a.show]:
        print("  ADDED  ", line)
    for line in changed_value[:a.show]:
        print("  CHANGED", line)
    for line in reclassified[:a.show]:
        print("  RECLASS", line)
    for d in diffs[:a.show]:
        print(f"  DIFF {d['name']:<26} [{d['a']} -> {d['b']}] "
              + "; ".join(d["diffs"])[:180])
    if len(diffs) > a.show:
        print(f"  ... {len(diffs) - a.show} more")
    if a.json:
        Path(a.json).write_text(
            json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
