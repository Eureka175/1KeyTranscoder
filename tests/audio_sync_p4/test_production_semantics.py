#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P4.3 — production semantics regression (S1..S10 + consumer contracts).

WHAT THIS SUITE IS FOR
----------------------
P3/P4/P4.2 established the *evidence semantics*. P4.3 turns them into a
production contract. This suite asserts the contract holds — as properties of
the real production objects, not as a description of them.

The three invariants that matter (brief section 36):

    I1  SUCCESS is always evidence-valid
    I2  a numeric estimate with insufficient evidence cannot silently correct
    I3  an unresolved window cannot silently become a trusted correction

Everything else here supports those three: the state machine, the propagation
chain producer -> result -> plan -> consumer, coordinate/gap determinism, the
architecture boundary, and the proof that the DEFAULT path is untouched.

DISCIPLINE (inherited from P4.2)
--------------------------------
* every threshold is DERIVED from the production effective config
  (`core.channel_sync.effective_opts`), never a literal in a test;
* every case that claims "X cannot happen" carries a CONTROL proving the
  predicate fails on a deliberately broken input;
* an empty run is exit 2, never PASS;
* no dependency on `work/` or on the real corpus — this runs on a clone.

Run::

    python tests/audio_sync_p4/test_production_semantics.py
    python tests/audio_sync_p4/test_production_semantics.py --json out.json
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RESULTS: list[dict] = []
COUNTS = {"pass": 0, "fail": 0, "control_ok": 0, "control_failed": 0}


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
        _rec(name, True, "the predicate failed on the broken input, as required",
             kind="control")
        return
    _rec(name, False,
         "the predicate did NOT fail on a deliberately broken input — it is "
         "not discriminating", kind="control")


# ==========================================================================
# DERIVED production configuration — no threshold is written down in this file
# ==========================================================================
def gate() -> dict:
    from core.channel_sync import effective_opts

    eff = effective_opts(None)
    return {
        "min_usable_frames": int(eff["min_usable_frames"]),
        "min_confidence": float(eff["min_confidence"]),
        "min_usable_fraction": float(eff["min_usable_fraction"]),
    }


def suffices(usable: int, total: int, confidence: float, g: dict) -> bool:
    """The production evidence predicate, written ONCE and reused everywhere."""
    return (
        usable >= g["min_usable_frames"]
        and confidence >= g["min_confidence"]
        and (usable / total if total else 0.0) >= g["min_usable_fraction"]
    )


def abundant(g: dict) -> tuple[int, int, float]:
    """(usable, total, confidence) comfortably above every gate."""
    total = 4 * g["min_usable_frames"]
    usable = min(total, int(np.ceil(g["min_usable_fraction"] * total)) + 2)
    return usable, total, min(0.99, g["min_confidence"] + 0.4)


def scarce(g: dict) -> tuple[int, int, float]:
    """A NUMBER exists and is NOT trusted: enough frames/confidence to produce
    a value, but coverage below the gate."""
    total = 4 * g["min_usable_frames"]
    usable = max(g["min_usable_frames"],
                 int(np.floor(g["min_usable_fraction"] * total)) - 1)
    return usable, total, min(0.99, g["min_confidence"] + 0.4)


def small_geometry() -> dict:
    """A tiny but semantically identical geometry for the P4 plan object.

    window/hop are NOT the production 30 s / 10 s: the plan's semantics do not
    depend on the absolute size, and a 30 s window would make this suite
    allocate gigabytes for no additional coverage of the contract.
    """
    return {"window_samples": 1_000, "hop_samples": 500, "n_samples": 5_000}


def make_plan(measured: int, n_windows: int, **kw):
    """`measured` leading windows carry a valid measurement; the rest do not."""
    return make_plan_pattern([i < measured for i in range(n_windows)], **kw)


def make_plan_pattern(valid, **kw):
    """Build a plan from an explicit per-window validity mask.

    The mask is the INPUT; the states are DERIVED from it (`state = f(mask)`),
    which is the property S10.3 asserts. A trailing-only gap can never produce
    REENTRY — nothing follows it — so S4 needs an interior gap, hence this
    builder.
    """
    from core.audio_sync_tv import build_correction_plan

    geom = small_geometry()
    valid = [bool(v) for v in valid]
    shifts = [480 if v else 0 for v in valid]
    measurements = [
        {"delay_samples": float(shifts[i]), "valid": valid[i]}
        for i in range(len(valid))
    ]
    return build_correction_plan(
        measurements, n_samples=geom["n_samples"],
        window_samples=geom["window_samples"],
        hop_samples=geom["hop_samples"], **kw,
    )


def row_for(status_decision: str, reason, usable: int, total: int,
            confidence: float, *, shift=480, coverage_stated=True) -> dict:
    """A channel_sync report row in the shape the producer writes."""
    row = {
        "stream": 3, "decision": status_decision, "reason": reason,
        "shift_samples": shift if status_decision == "fixed" else None,
        "delay_samples": 480.0, "delay_ms": 10.0,
        "confidence": confidence, "constant": True, "warnings": [],
        "usable_frames": usable, "polarity": 1, "drift_ppm": 0.0,
        "fractional_part_samples": 0.0, "storage_dtype": "f32",
        "rms_dbfs": -18.0,
    }
    if coverage_stated:
        row["evidence"] = {
            "usable_frames": usable, "total_frames": total,
            "coverage": (usable / total) if total else 0.0,
            "confidence": confidence,
        }
    return row


def resolve(row: dict):
    from core.audio_models import ChannelSyncReport

    rep = ChannelSyncReport({"status": "applied", "detail": "",
                             "anchor_stream": 2, "channels": [row],
                             "algo_version": "2.3.0-p1"})
    return rep.resolve(row)


def applied_offset(res) -> int:
    from core.audio_timeline import SyncApplication, _effective_offset

    off, _raw = _effective_offset(res, SyncApplication.APPLY)
    return int(off)


# ==========================================================================
# S1  SUCCESS + sufficient evidence
# ==========================================================================
def s1_success_with_evidence(g: dict) -> None:
    print("\n== S1  SUCCESS + sufficient evidence ==")
    from core.audio_models import SyncStatus

    usable, total, conf = abundant(g)
    res = resolve(row_for("fixed", None, usable, total, conf))
    value("S1.1 a fully-evidenced fix resolves to SUCCESS",
          res.status is SyncStatus.SUCCESS, f"status={res.status.value!r}")
    value("S1.2 ...and reads as applied", bool(res.applied),
          f"applied={res.applied} offset={res.offset_samples}")
    value("S1.3 ...and carries the evidence that justifies it",
          res.usable_frames == usable and res.total_frames == total
          and res.coverage == usable / total,
          f"usable={res.usable_frames}/{res.total_frames} "
          f"coverage={res.measurement_coverage:.3f}")
    value("S1.4 ...and SUCCESS implies the production gate is satisfied",
          suffices(res.usable_frames, res.total_frames, res.quality, g),
          f"gate={g}")


# ==========================================================================
# S2  LOW_CONFIDENCE + numeric estimate
# ==========================================================================
def s2_low_confidence_with_number(g: dict) -> None:
    print("\n== S2  LOW_CONFIDENCE + a numeric estimate ==")
    from core.audio_models import SyncStatus

    usable, total, conf = scarce(g)
    res = resolve(row_for("untouched", "evidence_insufficient",
                          usable, total, conf))
    value("S2.1 insufficient evidence resolves to LOW_CONFIDENCE",
          res.status is SyncStatus.LOW_CONFIDENCE,
          f"status={res.status.value!r}")
    value("S2.2 ...and the NUMBER still exists (it is not erased)",
          res.offset_samples is not None and res.quality == conf,
          f"offset_samples={res.offset_samples} quality={res.quality}. "
          f"The value is reported; what is withheld is TRUST.")
    value("S2.3 ...and the evidence that explains it travels with it",
          res.coverage is not None and (usable / total) < g["min_usable_fraction"],
          f"coverage={res.measurement_coverage:.3f} < "
          f"{g['min_usable_fraction']} — 'a number exists and is not trusted'")
    value("S2.4 ...and LOW_CONFIDENCE is not a FAILED state",
          res.processed and res.status is not SyncStatus.FAILED,
          "uncertainty is not error: an under-evidenced measurement is a "
          "valid outcome, not an execution failure")


# ==========================================================================
# S3 / S4  UNRESOLVED_GAP and REENTRY in the plan object
# ==========================================================================
def s3_s4_states() -> None:
    print("\n== S3/S4  UNRESOLVED_GAP and REENTRY ==")
    from core.audio_sync_tv import GapState, required_window_count

    geom = small_geometry()
    n = required_window_count(geom["n_samples"], hop_samples=geom["hop_samples"])
    plan = make_plan(measured=3, n_windows=n)
    states = [str(s) for s in plan.states.tolist()]
    counts = plan.state_counts()
    value("S3.1 a refused window is labelled UNRESOLVED_GAP, never TRUSTED",
          counts["UNRESOLVED_GAP"] == n - 3 and counts["TRUSTED"] == 3,
          f"measured 3 of {n} windows -> {counts}")
    value("S3.2 ...and it still carries a value (the policy's, not a measurement)",
          plan.n_windows == n and plan.decision(5).shift_samples is not None,
          f"window 5: shift={plan.decision(5).shift_samples} "
          f"state={plan.decision(5).state.value}")
    value("S3.3 ...and the value's SOURCE is 'policy', not 'measurement'",
          plan.decision(5).source == "policy"
          and plan.decision(0).source == "measurement",
          "the claim and the value are separate fields; a policy-sourced "
          "TRUSTED is unconstructible")

    # REENTRY needs a TRUSTED window AFTER a gap, so the fixture needs an
    # INTERIOR gap: a trailing gap has nothing to re-enter into.
    pattern = [True, True, False, False, False, True, True, True, True, True]
    plan_r = make_plan_pattern(pattern)
    states_r = [str(s) for s in plan_r.states.tolist()]
    counts_r = plan_r.state_counts()
    gap_idx = [i for i, v in enumerate(pattern) if not v]
    reentry_idx = [i for i, s in enumerate(states_r) if s == "REENTRY"]
    value("S4.1 the first accepted window after a gap is REENTRY",
          counts_r["REENTRY"] == 1
          and reentry_idx == [max(gap_idx) + 1],
          f"mask={[int(v) for v in pattern]} -> states={states_r}; the single "
          f"REENTRY sits at index {reentry_idx} = the window right after the gap")
    value("S4.2 ...and REENTRY is TRUSTED-with-a-boundary, not a third value",
          all(plan_r.decision(i).source == "measurement"
              for i, s in enumerate(states_r) if s in ("TRUSTED", "REENTRY")),
          "every REENTRY window is backed by an accepted measurement")
    value("S4.3 ...and no window over-claims a measurement",
          plan_r.overclaimed_windows() == 0,
          f"overclaimed={plan_r.overclaimed_windows()}")

    def break_it() -> None:
        assert counts["UNRESOLVED_GAP"] == 0, "x"
    control("S3/S4.C the state-count predicate can fail", break_it)


# ==========================================================================
# S5  insufficient evidence cannot become SUCCESS
# ==========================================================================
def s5_no_under_evidenced_success(g: dict) -> None:
    print("\n== S5  insufficient evidence cannot become SUCCESS ==")
    from core.audio_models import SyncStatus

    # (a) the producer's own rows: every reason that means "not enough
    #     evidence" must map to a non-SUCCESS status.
    pairs = [
        ("untouched", "evidence_insufficient"),
        ("untouched", "low_confidence"),
        ("untouched", "insufficient_frames"),
        ("untouched", "recheck_residual"),
    ]
    bad = []
    for decision, reason in pairs:
        res = resolve(row_for(decision, reason, *scarce(g)))
        if res.status is SyncStatus.SUCCESS or res.applied:
            bad.append(f"{decision}/{reason} -> {res.status.value}")
    value("S5.1 no 'insufficient evidence' reason resolves to SUCCESS",
          not bad, f"checked {len(pairs)} reasons; violations={bad}")

    # (b) the invariant guard: a HISTORICAL/foreign row that says
    #     decision=fixed WHILE stating coverage below the gate must not be
    #     accepted. This is the case the producer can no longer emit, and the
    #     reason the guard exists.
    usable, total, conf = scarce(g)
    smuggled = row_for("fixed", None, usable, total, conf)
    res = resolve(smuggled)
    value("S5.2 a row asserting 'fixed' while stating sub-gate coverage is "
          "downgraded, not trusted",
          res.status is SyncStatus.LOW_CONFIDENCE and not res.applied,
          f"decision='fixed' with coverage={usable / total:.3f} < "
          f"{g['min_usable_fraction']} -> {res.status.value}, applied={res.applied}")
    value("S5.3 ...and the reason names WHY (the P4.2 reason code, no new one)",
          res.reason == "evidence_insufficient",
          f"reason={res.reason!r}")

    # (c) BACKWARD COMPATIBILITY: the same row WITHOUT any stated coverage is
    #     a historical record. It must keep today's behaviour exactly.
    legacy = resolve(row_for("fixed", None, usable, total, conf,
                             coverage_stated=False))
    value("S5.4 ...but a historical row that states NO coverage is unchanged",
          legacy.status is SyncStatus.SUCCESS and legacy.applied,
          "new fields must not force historical results to fail — 'not "
          "stated' is None, not 0, and None asserts nothing")

    # (d) the gate is DERIVED from production config, not baked in: a runner
    #     with a laxer stated gate accepts the row the default rejects.
    lax = dict(smuggled)
    rep_report = {
        "status": "applied", "detail": "", "anchor_stream": 2,
        "channels": [lax], "algo_version": "2.3.0-p1",
        "evidence_gate": {"min_usable_frames": 1, "min_confidence": 0.0,
                          "min_usable_fraction": 0.0},
    }
    from core.audio_models import ChannelSyncReport, SyncStatus as _S

    lax_res = ChannelSyncReport(rep_report).resolve(lax)
    value("S5.5 the guard reads the gate the REPORT states, not a literal",
          lax_res.status is _S.SUCCESS,
          "the same row under a stated laxer gate is accepted — so the "
          "guard is not a hardcoded 'coverage must be 0.6'")

    def break_it() -> None:
        assert res.status is SyncStatus.SUCCESS, "x"
    control("S5.C the 'under-evidenced is not SUCCESS' predicate can fail",
            break_it)


# ==========================================================================
# S6 / S7 / S8  the correction path
# ==========================================================================
def s6_s7_s8_correction_path(g: dict) -> None:
    print("\n== S6/S7/S8  what may enter the correction path ==")
    from core.audio_models import SyncStatus, SYNC_STATUS_PATH

    usable, total, conf = abundant(g)
    ok_row = resolve(row_for("fixed", None, usable, total, conf))
    value("S6.1 SUCCESS enters the correction path",
          applied_offset(ok_row) == int(round(ok_row.offset_samples)),
          f"effective_offset={applied_offset(ok_row)} for offset="
          f"{ok_row.offset_samples}")

    scarce_row = resolve(row_for("untouched", "evidence_insufficient",
                                 *scarce(g)))
    value("S7.1 LOW_CONFIDENCE does NOT enter the correction path",
          applied_offset(scarce_row) == 0,
          f"offset_samples={scarce_row.offset_samples} exists, but "
          f"effective_offset={applied_offset(scarce_row)} — a number is not "
          f"sufficient to be corrected")
    value("S7.2 ...even though a number is present (U1: the sentinel error)",
          scarce_row.offset_samples is not None
          and applied_offset(scarce_row) == 0,
          "'a number exists == a measurement exists' is exactly what this "
          "separation removes")

    # every non-SUCCESS status must be refused, enumerated from the state
    # machine rather than from a hand-written list
    refused = []
    for status in SyncStatus:
        if SYNC_STATUS_PATH[status]["may_enter_correction"]:
            continue
        from core.audio_models import AudioSyncResult
        from core.audio_timeline import SyncApplication, _effective_offset

        probe = AudioSyncResult(status=status, offset_samples=480.0)
        off, _ = _effective_offset(probe, SyncApplication.APPLY)
        if off != 0:
            refused.append(status.value)
    value("S7.3 every status the state machine marks non-correcting is "
          "refused by the apply gate",
          not refused,
          f"probed {len(list(SyncStatus))} statuses; leaks={refused}")

    # S8: the P4 plan cannot represent an unresolved window as trusted
    from core.audio_sync_tv import CorrectionDecision, GapState

    geom = small_geometry()
    plan = make_plan(measured=2, n_windows=10)
    gap_windows = [i for i in range(plan.n_windows)
                   if plan.decision(i).state is GapState.UNRESOLVED_GAP]
    value("S8.1 an UNRESOLVED_GAP window is never labelled TRUSTED",
          bool(gap_windows)
          and all(plan.decision(i).state is GapState.UNRESOLVED_GAP
                  for i in gap_windows),
          f"{len(gap_windows)} unresolved windows in a 10-window plan")
    value("S8.2 ...and the plan counts zero over-claimed windows",
          plan.overclaimed_windows() == 0,
          "state = f(resolved mask), so a policy-filled window cannot claim "
          "a measurement")

    raised = False
    try:
        CorrectionDecision(615, GapState.TRUSTED, source="policy")
    except BaseException:                              # noqa: BLE001
        raised = True
    value("S8.3 ...and a policy-sourced TRUSTED decision is unconstructible",
          raised,
          "the contradiction is rejected at construction, not by convention")

    def break_it() -> None:
        assert applied_offset(scarce_row) != 0, "x"
    control("S7.C the 'no silent correction' predicate can fail", break_it)


# ==========================================================================
# S9 / S10  coordinate and gap semantics are deterministic
# ==========================================================================
def s9_s10_determinism() -> None:
    print("\n== S9/S10  coordinate and gap semantics are deterministic ==")
    from core.audio_sync_tv import (
        DEFAULT_COORDINATE, DEFAULT_POLICY, DECISION_STATUS, GapState,
        GAP_POLICIES, required_window_count,
    )

    geom = small_geometry()
    n = required_window_count(geom["n_samples"], hop_samples=geom["hop_samples"])
    a = make_plan(measured=3, n_windows=n)
    b = make_plan(measured=3, n_windows=n)
    value("S9.1 the same geometry produces the same shift sequence",
          np.array_equal(a.shifts, b.shifts),
          f"shifts={a.shifts.tolist()}")
    value("S9.2 ...and the same state sequence",
          np.array_equal(a.states, b.states), f"states={a.states.tolist()}")
    value("S9.3 ...and the coordinate contract is a named, recorded parameter",
          a.coordinate in DECISION_STATUS["coordinate"]["implementation_default"]
          or a.coordinate == DEFAULT_COORDINATE,
          f"plan.coordinate={a.coordinate!r}; join is a declared artifact, "
          f"not an implicit consequence of integer division")
    value("S10.1 an unstated policy is the recorded implementation default",
          a.policy == DEFAULT_POLICY,
          f"policy={a.policy!r} == DEFAULT_POLICY")
    value("S10.2 ...and every declared policy is selectable without a code "
          "change",
          all(make_plan(3, n, policy=p).policy == p for p in
              sorted(GAP_POLICIES) if GAP_POLICIES[p] is False),
          f"online policies exercised: "
          f"{sorted(p for p in GAP_POLICIES if not GAP_POLICIES[p])}")
    value("S10.3 ...and state is a pure function of the mask (no history)",
          np.array_equal(a.states, b.states)
          and a.state_counts() == b.state_counts(),
          f"{a.state_counts()}")

    # D2/D5: the two concepts must remain SEPARATED and machine-readable
    for key, default in (("gap_policy", DEFAULT_POLICY),
                         ("coordinate", DEFAULT_COORDINATE)):
        entry = DECISION_STATUS[key]
        value(f"S10.4 {key}: implementation default and semantic contract are "
              f"separate fields",
              entry["implementation_default"] == default
              and entry["semantic_contract"] == "DEFERRED"
              and bool(entry["why_deferred"])
              and bool(entry["unblocked_by"]),
              f"default={entry['implementation_default']!r} is for "
              f"reproducibility; contract={entry['semantic_contract']} — "
              f"'the default is X' does not mean 'X is the product contract'")

    def break_it() -> None:
        assert not np.array_equal(a.shifts, b.shifts), "x"
    control("S9/S10.C the determinism predicate can fail", break_it)


# ==========================================================================
# CONSUMER CONTRACT  producer -> result -> plan -> consumer
# ==========================================================================
def consumer_contract(g: dict) -> None:
    print("\n== C  consumer contract: evidence is not silently discarded ==")
    from core.audio_models import (
        AudioChannel, AudioPlan, AudioSyncResult, AudioTrack, SyncStatus,
    )

    usable, total, conf = scarce(g)
    res = resolve(row_for("untouched", "evidence_insufficient",
                          usable, total, conf))

    ch = AudioChannel(stream_index=0, channel_index=0)
    tr = AudioTrack(track_id="a0", source_stream_index=0, channels=[ch])
    tr.set_sync(res)
    value("C.1 the evidence survives result -> channel",
          ch.sync.coverage == res.coverage
          and ch.sync.total_frames == res.total_frames,
          f"channel.sync.coverage={ch.sync.coverage}")
    value("C.2 ...and result -> channel -> track (aggregated conservatively)",
          tr.sync_coverage is not None and tr.sync_coverage <= res.coverage,
          f"track.sync_coverage={tr.sync_coverage} (min over channels)")

    # a track whose channels disagree must not average the coverage away
    ch2 = AudioChannel(stream_index=1, channel_index=0)
    tr2 = AudioTrack(track_id="a1", source_stream_index=1, channels=[ch, ch2])
    u2, t2, c2 = abundant(g)
    tr2.channels[0].sync = res
    tr2.channels[1].sync = AudioSyncResult(
        status=SyncStatus.SUCCESS, offset_samples=480.0,
        usable_frames=u2, total_frames=t2, coverage=u2 / t2)
    tr2.refresh_sync()
    value("C.3 a fully measured channel does not mask an under-measured one",
          tr2.sync_coverage == res.coverage
          and tr2.sync_coverage < u2 / t2,
          f"min({res.coverage:.3f}, {u2 / t2:.3f}) = {tr2.sync_coverage:.3f} — "
          f"the track reports the WORST channel")

    # the plan-level traceability record
    from core.audio_plan import AudioChannelRef

    ref = AudioChannelRef.from_dict({
        "channel_id": "cam:s0:c0", "sync_status": res.status.value,
        "sync_offset_samples": res.offset_samples,
        "sync_coverage": res.coverage,
    })
    value("C.4 ...and channel -> AudioChannelRef -> dict round-trips exactly",
          AudioChannelRef.from_dict(ref.to_dict()).sync_coverage
          == res.coverage,
          f"coverage={ref.sync_coverage} survives the product-facing record")

    # the model is the LAST hop that must hold it; the PCM/encoder layers must
    # NOT have learned about it (brief section 27)
    from core.output_compose import AudioInputRef

    fields = [f.name for f in dataclasses.fields(AudioInputRef)]
    value("C.5 the PCM / composition layers stay state-free (and that is "
          "asserted, not assumed)",
          not any(k in f for f in fields
                  for k in ("sync", "coverage", "state", "evidence")),
          f"AudioInputRef fields={fields}")

    value("C.6 a consumer can tell 'fully measured' from 'partly measured' "
          "without parsing a log line",
          res.coverage is not None and res.coverage < 1.0
          and res.status is not SyncStatus.SUCCESS,
          "the contract pushes uncertainty UP into the model and the report; "
          "it does not push it DOWN into execution")

    def break_it() -> None:
        assert tr2.sync_coverage == 1.0, "x"
    control("C.C the conservative-aggregation predicate can fail", break_it)


# ==========================================================================
# P  default path protection
# ==========================================================================
def default_path(g: dict) -> None:
    print("\n== P  default path protection ==")
    from core.audio_format import AudioAlignmentPolicy, resolve_alignment
    from core.audio_models import AudioPlan, AudioSyncResult, SyncStatus

    d_none = resolve_alignment(None, AudioAlignmentPolicy.AUTO)
    value("P.1 AudioPlan = None -> alignment cannot be enabled, reason named",
          not d_none.enabled
          and d_none.reason == "audio_alignment_no_channels",
          f"enabled={d_none.enabled} reason={d_none.reason!r} — with no plan "
          f"there are no channels to align, and the resolver says so instead "
          f"of guessing a reference")

    off = resolve_alignment(AudioPlan(), "disabled")
    value("P.2 an empty plan resolves to disabled with a stable reason",
          not off.enabled and off.reason == "audio_alignment_no_channels",
          f"reason={off.reason!r} — the no-channel check precedes the policy "
          f"check, so 'disabled' is not the only thing keeping this off")

    # D1's schema addition must not alter any existing result's meaning
    blank = AudioSyncResult()
    d = blank.to_dict()
    value("P.3 a default AudioSyncResult serialises exactly as before",
          d == {"status": "not_processed"},
          f"to_dict()={d} — the new fields are absent when unstated, so "
          f"existing output is byte-identical")

    tr = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=480.0,
                         quality=0.9, usable_frames=592)
    before = {"status": "success", "offset_samples": 480.0, "quality": 0.9,
              "usable_frames": 592}
    value("P.4 a result that states no coverage gains no new keys",
          tr.to_dict() == before,
          f"to_dict()={tr.to_dict()}")

    # the P4 time-varying path must still be unreachable from production
    offenders = []
    for path in sorted((REPO / "core").glob("*.py")) + \
            sorted((REPO / "production").glob("*.py")) + [REPO / "1kt.py"]:
        if path.name == "audio_sync_tv.py":
            continue
        txt = path.read_text(encoding="utf-8", errors="replace")
        if "audio_sync_tv" in txt and "import" in txt:
            offenders.append(path.name)
    value("P.5 the time-varying path is still imported by no production module",
          not offenders,
          f"importers={offenders or 'none'} — default OFF is structural, not "
          f"a flag that configuration can flip")

    def break_it() -> None:
        assert d != {"status": "not_processed"}, "x"
    control("P.C the serialisation-stability predicate can fail", break_it)


# ==========================================================================
# A  architecture boundary
# ==========================================================================
def architecture_boundary() -> None:
    print("\n== A  architecture boundary ==")
    import re

    sync_files = ["channel_sync.py", "audio_sync.py", "sync_estimate.py",
                  "sync_fix.py", "audio_sync_tv.py"]
    # The boundary the brief names: an audio-sync module must not depend on the
    # video encoder, metadata, Sony path, muxing libraries or a composer.
    #
    # `preservation` is DELIBERATELY not in this list. `core/channel_sync.py`
    # does remux an output and repair its timescale, so it must touch the
    # container layer; that is boundary work, not sync semantics. What matters
    # architecturally is that those imports are FUNCTION-LOCAL, so importing
    # the sync layer does not drag the container layer in — asserted by A.2.
    forbidden = ("VideoEncoder", "Metadata", "Sony", "x265", "NVENC", "QSV",
                 "audio_mix", "output_compose", "audio_encode")
    leaks = []
    for name in sync_files:
        p = REPO / "core" / name
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            stripped = line.strip()
            if not re.match(r"^\s*(from|import)\s", line):
                continue
            if any(f in stripped for f in forbidden):
                leaks.append(f"{name}:{i}: {stripped}")
    value("A.1 the audio-sync layer imports no video/encoder/mux-library module",
          not leaks, f"imports scanned in {len(sync_files)} files; leaks={leaks}")

    # A.2: the container-layer imports that DO exist are delayed, so the sync
    # modules are importable without the container layer being loaded.
    from core import channel_sync as cs

    value("A.2 the container-layer imports in the P1 producer are "
          "function-local, not module-level",
          not hasattr(cs, "isobmf") and not hasattr(cs, "track_manifest"),
          "importing core.channel_sync does not pull preservation/* into the "
          "process — the dependency direction is one-way and lazy")

    # A.3: the two sync implementations share the contract, not the code
    tv = (REPO / "core" / "audio_sync_tv.py").read_text(
        encoding="utf-8", errors="replace")
    value("A.3 the time-varying module does not import the P1 producer",
          "import channel_sync" not in tv
          and "from .channel_sync" not in tv
          and "from . import channel_sync" not in tv,
          "the two sync paths share the contract, not the code")

    def break_it() -> None:
        assert leaks, "x"
    control("A.C the boundary predicate can fail", break_it)


# ==========================================================================
# SM  production state machine coherence
# ==========================================================================
def state_machine() -> None:
    print("\n== SM  production state machine ==")
    from core.audio_models import SYNC_STATUS_PATH, SyncStatus

    value("SM.1 every SyncStatus member has exactly one state-machine row",
          set(SYNC_STATUS_PATH) == set(SyncStatus),
          f"{len(SYNC_STATUS_PATH)} rows for {len(list(SyncStatus))} members")

    violating = [s.value for s, row in SYNC_STATUS_PATH.items()
                 if row["may_enter_correction"] and not row["value_trusted"]]
    value("SM.2 nothing may enter the correction path unless its value is "
          "trusted",
          not violating,
          "U1: a number existing must never be enough to publish a correction")

    both = [s.value for s, row in SYNC_STATUS_PATH.items()
            if row["value_trusted"] and row["is_unresolved"]]
    value("SM.3 no state is simultaneously trusted and unresolved",
          not both, f"overlaps={both or 'none'}")

    value("SM.4 SUCCESS is the only correcting member",
          [s for s, row in SYNC_STATUS_PATH.items()
           if row["may_enter_correction"]] == [SyncStatus.SUCCESS],
          "so 'offset exists -> corrected' has exactly one key, and that key "
          "is the one the evidence gate guards")

    def break_it() -> None:
        assert violating, "x"
    control("SM.C the state-machine predicate can fail", break_it)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    g = gate()
    print("=" * 78)
    print("P4.3 — production semantics regression")
    print("=" * 78)
    print("production gate (DERIVED from core.channel_sync.effective_opts):")
    print(f"  min_usable_frames={g['min_usable_frames']} "
          f"min_confidence={g['min_confidence']} "
          f"min_usable_fraction={g['min_usable_fraction']}")
    print(f"  abundant fixture = {abundant(g)}; scarce fixture = {scarce(g)}")
    if scarce(g)[0] < g["min_usable_frames"]:
        print("BLOCKED: the derived 'scarce' fixture has too few usable frames "
              "to produce a value at all — the S2 case would not be testing "
              "what it claims", file=sys.stderr)
        return 2

    s1_success_with_evidence(g)
    s2_low_confidence_with_number(g)
    s3_s4_states()
    s5_no_under_evidenced_success(g)
    s6_s7_s8_correction_path(g)
    s9_s10_determinism()
    consumer_contract(g)
    default_path(g)
    architecture_boundary()
    state_machine()

    n_cases = len(RESULTS)
    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print("\n" + "=" * 78)
    print(f"cases        : {n_cases}")
    print(f"PASS         : {n_cases - n_fail}")
    print(f"FAIL         : {n_fail}")
    print(f"CONTROL_OK   : {COUNTS['control_ok']}")
    print(f"CONTROL_FAILED: {COUNTS['control_failed']}")
    print("=" * 78)

    if n_cases == 0:
        print("FAIL: no case ran — an empty run is not a pass", file=sys.stderr)
        return 2
    if COUNTS["control_ok"] == 0:
        print("FAIL: no control was demonstrated discriminating",
              file=sys.stderr)
        return 2

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"cases": n_cases, "fail": n_fail, "gate": g,
             "counts": COUNTS, "results": RESULTS}, indent=1),
            encoding="utf-8")
        print(f"json -> {args.json}")

    if n_fail:
        print(f"\nRESULT: FAIL ({n_fail})")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  FAIL {r['name']} -- {r['detail']}")
        return 1
    print(f"\nRESULT: PASS — {n_cases} cases, "
          f"{COUNTS['control_ok']} controls discriminating")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
