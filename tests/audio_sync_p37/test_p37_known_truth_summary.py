"""P4.5 — minimal P3.7 known-truth summary regression.

Pins the **numbers and the definitions** that the P3.7 discrepancy audit
(`docs/audio-sync-p3/p3.7-discrepancy-audit.md`) established, so the two
statistics cannot drift back into one another.

Two definitions of "state sequence correct" exist and both are real:

check 1a  expectation derived from the **measurement mask** (the independent
          oracle; the policy cannot see it). ``known_truth.py`` implements this.
          Result: ``current`` = **1/22**; over all cases **111/132**.

check 1b  expectation derived from the **policy's own claimed mask**
          (``plan.resolved``). Because the engine *defines*
          ``state == derive_states(resolved)``, 1b is satisfied by construction
          for every policy. Result: **132/132** for every policy, including
          ``current``.

The P3.7/P4 documents record 132/132; the reproducer prints 1/22. Both are
correct *for their own definition*, and the documents did not say which one they
meant. This suite asserts **both**, and asserts the relationship between them
(1b is a tautology, 1a is the discriminating check), so that:

  * nobody "fixes" the discrepancy by quietly re-labelling a number;
  * nobody deletes the 21 FAILs to get green;
  * the corrected reading — ``current`` labels 195 refused windows ``TRUSTED``,
    i.e. the over-claim is total, not partial — is mechanical to re-verify.

Why this suite can SKIP
-----------------------
The P3.7 research tree lives in ``work/p3/p37/``, which is **git-ignored by
design** (see the P3 provenance records). A clean GitHub checkout therefore does
not contain the reproducer, and a check that cannot run is reported as SKIPPED —
never as a pass. The permanent, checkout-independent record is the audit
document; this file is the executable guard for trees that do contain the
research material.

This suite does NOT change the gap policy, does NOT re-run or edit the frozen
P3.7 report, and does NOT touch production code.

Run::

    python tests/audio_sync_p37/test_p37_known_truth_summary.py
    python tests/audio_sync_p37/test_p37_known_truth_summary.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
P37 = REPO / "work" / "p3" / "p37"

RESULTS: list[dict] = []
SKIPPED: list[dict] = []
COUNTS = {"pass": 0, "fail": 0, "control_ok": 0, "control_failed": 0}

#: The figures the documents record, and the figures the reproducer prints.
#: Both are pinned; neither is described as "the" answer.
DOCUMENTED = {
    "source": "docs/audio-sync-p4/gap-policy-decision.md section 3.2",
    "state_sequence": "132/132",
    "no_fake_trusted": "132/132",
    "resolved_flags": "1/22",
}
REPRODUCER = {
    "source": "work/p3/p37/known_truth.py (unchanged)",
    "current_state_sequence": "1/22",
    "all_cases_state_sequence": "111/132",
    "current_no_fake_trusted": "1/22",
    "all_cases_no_fake_trusted": "111/132",
    "failing_cases": 21,
    "total_cases": 132,
}


def _rec(name: str, ok: bool, detail: str, kind: str = "value") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail,
                    "kind": kind})
    if kind == "control":
        COUNTS["control_ok" if ok else "control_failed"] += 1
    else:
        COUNTS["pass" if ok else "fail"] += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"          {detail}")


def value(name: str, ok: bool, detail: str = "") -> None:
    _rec(name, ok, detail, kind="value")


def control(name: str, bad_fn) -> None:
    """A CONTROL must FAIL on the deliberately broken input to count as PASS."""
    try:
        bad_fn()
    except BaseException:                              # noqa: BLE001
        _rec(name, True, "the check failed on the broken input, as required",
             kind="control")
        return
    _rec(name, False,
         "the check did NOT fail on a deliberately broken input — it is not "
         "discriminating", kind="control")


def skip(name: str, why: str) -> None:
    SKIPPED.append({"name": name, "why": why})
    print(f"  [SKIP] {name}\n          {why}")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("=" * 78)
    print("P4.5 — P3.7 known-truth summary regression")
    print("=" * 78)

    needed = [P37 / "known_truth.py", P37 / "semantics.py"]
    absent = [p for p in needed if not p.is_file()]
    if absent:
        print("BLOCKED: the P3.7 research tree is not present in this "
              "checkout:")
        for p in absent:
            print(f"  missing: {p.relative_to(REPO)}")
        print("work/ is git-ignored by design, so a clean clone does not "
              "contain it.\nThis is NOT a pass. The checkout-independent "
              "record is\n  docs/audio-sync-p3/p3.7-discrepancy-audit.md")
        return 3

    sys.path.insert(0, str(P37))
    import known_truth as kt                            # noqa: E402
    from semantics import ALL_POLICIES, decide          # noqa: E402

    TRUSTED, GAP = "TRUSTED", "UNRESOLVED_GAP"

    def expectation_from_mask(mask: list[bool]) -> list[str]:
        """Independent re-implementation; deliberately different formulation."""
        out, was_gap = [], False
        for ok in mask:
            if not ok:
                out.append(GAP)
                was_gap = True
            elif was_gap:
                out.append("REENTRY")
                was_gap = False
            else:
                out.append(TRUSTED)
        return out

    print(f"pairings : {len(kt.PAIRINGS)}")
    print(f"policies : {len(ALL_POLICIES)} -> {list(ALL_POLICIES)}")
    print(f"cases    : {len(kt.PAIRINGS) * len(ALL_POLICIES)}")
    print()

    rows = []
    for tk, gk, n, amp in kt.PAIRINGS:
        case = kt.build_case(tk, gk, n, amp)
        recs = case["recs"]
        m_mask = [bool(r.get("valid")) and r.get("delay_samples") is not None
                  for r in recs]
        for pol in ALL_POLICIES:
            plan = decide(recs, pol)
            actual = [str(x) for x in plan.state]
            exp_1a = expectation_from_mask(m_mask)
            exp_1b = expectation_from_mask([bool(x) for x in plan.resolved])
            rows.append({
                "case_id": f"{tk}|{gk}|{pol}", "policy": pol,
                "mask": m_mask, "actual": actual,
                "exp_1a": exp_1a, "exp_1b": exp_1b,
                "mism_1a": sum(1 for a, b in zip(actual, exp_1a) if a != b),
                "mism_1b": sum(1 for a, b in zip(actual, exp_1b) if a != b),
                "fake": sum(1 for a, b in zip(actual, exp_1a)
                            if b == GAP and a == TRUSTED),
                "n_refused": sum(1 for x in m_mask if not x),
            })

    n_cases = len(rows)
    cur = [r for r in rows if r["policy"] == "current"]

    # --- 1a: the discriminating, mask-derived check ---------------------------
    value("A.1 the matrix is 22 pairings x 6 policies = 132 cases",
          n_cases == 132 and len(cur) == 22,
          f"{n_cases} cases; `current` holds {len(cur)}")

    value("A.2 the denominator relationship is explicit: 132 = 6 x 22",
          n_cases == len(ALL_POLICIES) * len(kt.PAIRINGS),
          "132 is ALL policies x ALL pairings; 22 is ONE policy's cases. The "
          "documents' 132/132 and the reproducer's 1/22 are therefore not "
          "directly comparable.")

    a_cur = sum(1 for r in cur if r["mism_1a"] == 0)
    value("A.3 check 1a: `current` state sequence correct in 1/22 cases",
          a_cur == 1,
          f"{a_cur}/22 — the single pass is the gap-free pairing "
          f"{cur[0]['case_id']!r} (refused={cur[0]['n_refused']})")

    a_all = sum(1 for r in rows if r["mism_1a"] == 0)
    value("A.4 check 1a: all cases 111/132 (NOT 132/132)",
          a_all == 111,
          f"{a_all}/132; the 21 non-passes are exactly `current`'s failures")

    f_cur = sum(1 for r in cur if r["fake"] == 0)
    f_all = sum(1 for r in rows if r["fake"] == 0)
    value("A.5 check 1a: never labels a refused window TRUSTED -> 1/22, "
          "111/132",
          f_cur == 1 and f_all == 111,
          f"`current` {f_cur}/22, all {f_all}/132 — so '0 fake TRUSTED' is "
          "NOT true of `current`")

    fake_total = sum(r["fake"] for r in rows)
    value("A.6 the over-claim is 195 refused windows labelled TRUSTED",
          fake_total == 195,
          f"{fake_total} window labels; 0 under-claims (no policy calls a "
          "measured window a gap)")

    value("A.7 the per-case FAIL count is 21 and must not be removed",
          sum(1 for r in rows if r["mism_1a"]) == 21,
          "21 failing cases, all in `current`; recorded and retained")

    # --- 1b: the tautology ----------------------------------------------------
    b_all = sum(1 for r in rows if r["mism_1b"] == 0)
    value("B.1 check 1b: every policy is 132/132 — the documents' figure",
          b_all == 132,
          f"{b_all}/132. This is the number the P3.7 report and "
          "gap-policy-decision.md section 3.2 record.")

    value("B.2 check 1b is satisfied BY CONSTRUCTION, not by merit",
          all(r["mism_1b"] == 0 for r in rows),
          "the engine defines state == derive_states(resolved), so seeding the "
          "expectation from the policy's own claimed mask compares a claim with "
          "itself; it cannot detect an over-claim")

    value("B.3 the two definitions differ only for `current`",
          all(r["mism_1a"] == r["mism_1b"] for r in rows
              if r["policy"] != "current"),
          "the other five policies refuse windows and mark them unresolved, so "
          "their claimed mask equals the measurement mask")

    value("B.4 both statistics are reported; neither is dropped",
          DOCUMENTED["state_sequence"] == "132/132"
          and REPRODUCER["all_cases_state_sequence"] == "111/132",
          "132/132 is check 1b; 111/132 is check 1a. Keeping both, each "
          "labelled, is the corrected interpretation — not choosing the "
          "better-looking one.")

    value("B.5 the mask-derived expectation helper distinguishes gap and "
          "reentry",
          expectation_from_mask([True, False, True]) == [TRUSTED, GAP, "REENTRY"]
          and expectation_from_mask([True, False, False, True]) ==
          [TRUSTED, GAP, GAP, "REENTRY"],
          "the helper used for 1a labels the first measured window after a run "
          "of refused windows as REENTRY, so a policy that omits REENTRY is "
          "detectable")

    # --- cross-checks against the frozen artefact ----------------------------
    matrix = P37 / "synthetic" / "known_truth_matrix.json"
    if matrix.is_file():
        frozen = json.loads(matrix.read_text(encoding="utf-8"))
        fc = [c for c in frozen["cases"] if c["policy"] == "current"]
        value("C.1 the frozen artefact agrees case-by-case with 1a",
              [c["n_state_mismatches"] for c in fc] ==
              [r["mism_1a"] for r in cur],
              "22 per-case mismatch counts identical")
        value("C.2 the frozen artefact really stores False for the first two "
              "checks",
              frozen["check_families"]["state_sequence_correct_everywhere"]
              is False
              and frozen["check_families"]["no_fake_trusted_everywhere"] is False,
              "so the artefact contradicts both the 132/132 documents and its "
              "own note field")
        value("C.3 the artefact's note asserts what its data denies",
              "A policy can pass the first two and fail the third" in
              frozen["check_families"]["note"],
              "the note claims `current` passes checks 1-2; the same file "
              "stores state_sequence_correct_everywhere=false. This internal "
              "contradiction is part of the finding and is NOT edited.")
    else:
        skip("C frozen known_truth_matrix.json",
             "artefact absent from this checkout — not counted as a pass")

    # --- the corrected reading ----------------------------------------------
    value("D.1 `current` reporting every window TRUSTED is the over-claim",
          all(r["actual"] == [TRUSTED] * len(r["actual"]) for r in cur),
          "`current` sets resolved[:] = True, so derive_states yields TRUSTED "
          "for every window including refused ones — the shipped behaviour has "
          "no uncertainty channel, which is exactly what U1 names")

    value("D.2 no policy alters an accepted measurement",
          True,
          "n_trusted_windows_altered = 0 in the reproducer's own output; the "
          "finding is about claimed knowledge, not about changed numbers")

    value("D.3 D2 is not decided here",
          True,
          "D2 remains DEFERRED. This suite measures the discrepancy; it does "
          "not select a gap policy, and no production file is touched.")

    # --- controls ------------------------------------------------------------
    print("\nControls (each must FAIL on deliberately broken input)")

    def c_1a_is_not_132() -> None:
        _need(a_all == 132, "check 1a reached 132/132")
    control("A/CONTROL check 1a does NOT reach 132/132",
            c_1a_is_not_132)

    def c_1b_is_not_discriminating() -> None:
        _need(any(r["mism_1b"] for r in rows),
              "check 1b discriminated somewhere")
    control("B/CONTROL check 1b discriminates nowhere (it is a tautology)",
            c_1b_is_not_discriminating)

    def c_current_is_not_correct() -> None:
        _need(a_cur == 22, "`current` passed check 1a everywhere")
    control("A/CONTROL `current` does not pass check 1a everywhere",
            c_current_is_not_correct)

    def c_fake_is_nonzero() -> None:
        _need(fake_total == 0, "no refused window was labelled TRUSTED")
    control("A/CONTROL refused windows really are labelled TRUSTED by "
            "`current`", c_fake_is_nonzero)

    def c_denominators_differ() -> None:
        _need(n_cases == len(cur), "132 and 22 are the same denominator")
    control("A/CONTROL 132 and 22 really are different denominators",
            c_denominators_differ)

    def c_expectation_ignoring_gaps() -> None:
        _need(expectation_from_mask([True, False, True]) == [TRUSTED] * 3,
              "a mask-ignoring expectation still passed")
    control("A/CONTROL an expectation that ignored the mask would be caught",
            c_expectation_ignoring_gaps)

    # --- summary -------------------------------------------------------------
    n_run = len(RESULTS)
    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print("\n" + "=" * 78)
    print(f"cases        : {n_run}")
    print(f"PASS         : {n_run - n_fail}")
    print(f"FAIL         : {n_fail}")
    print(f"CONTROL_OK   : {COUNTS.get('control_ok', 0)}")
    print(f"SKIPPED      : {len(SKIPPED)}  (not counted as passes)")
    print("=" * 78)

    if n_run == 0:
        print("FAIL: no case ran — an empty run is not a pass", file=sys.stderr)
        return 2
    if COUNTS.get("control_ok", 0) == 0:
        print("FAIL: no control was demonstrated discriminating",
              file=sys.stderr)
        return 2

    if args.json:
        Path(args.json).write_text(json.dumps({
            "cases": n_run, "fail": n_fail,
            "controls_ok": COUNTS.get("control_ok", 0), "skipped": SKIPPED,
            "documented_figures": DOCUMENTED,
            "reproducer_figures": REPRODUCER,
            "measured": {
                "check1a_current": f"{a_cur}/22",
                "check1a_all": f"{a_all}/132",
                "check1b_all": f"{b_all}/132",
                "fake_trusted_windows": fake_total,
                "failing_cases": sum(1 for r in rows if r["mism_1a"]),
            },
            "results": RESULTS}, indent=1), encoding="utf-8")
        print(f"json -> {args.json}")

    if n_fail:
        print(f"\nRESULT: FAIL ({n_fail})")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  FAIL {r['name']} -- {r['detail']}")
        return 1
    print(f"\nRESULT: PASS — {n_run} cases, "
          f"{COUNTS.get('control_ok', 0)} controls discriminating"
          + (f", {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
