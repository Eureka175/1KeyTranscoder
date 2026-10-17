#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P4.4 — production correction eligibility regression (E1..E10).

WHAT THIS SUITE IS FOR
----------------------
P4.4 enables the **validated fixed-delay** subset of Audio Sync in production.
The thing that makes that safe is not the measurement (P1 validated it) and not
the applier (P1 validated it) — it is that **"a number exists" is no longer
enough to correct**. Before P4.4 each consumer answered "may I correct?" for
itself, and **none of them looked at the evidence**.

The contract this suite asserts, in the brief's terms:

    SUCCESS / trusted        -> eligible for validated fixed correction
    LOW_CONFIDENCE           -> not eligible
    UNRESOLVED_GAP           -> not eligible for a trusted correction
    FAILURE                  -> not eligible

    correction eligibility   !=   offset_samples exists
    raw_offset_samples exists     does NOT imply correction is allowed

The structural expression of that last line: an ineligible `CorrectionEligibility`
and an ineligible `FixedDelayEntry` carry **`offset_samples = None`**. The state
"refused but holding a usable number" is unrepresentable, not merely discouraged.
The number itself stays readable as `raw_offset_samples`, so a report can still
say "we measured 480 samples and did not use them".

    E1  SUCCESS + sufficient evidence         -> correction applied
    E2  LOW_CONFIDENCE + numeric estimate     -> correction not applied
    E3  UNRESOLVED_GAP                        -> no trusted correction
    E4  FAILURE                               -> no correction
    E5  positive fixed offset                 -> correct PCM result
    E6  negative fixed offset                 -> correct PCM result
    E7  zero offset                           -> exact identity
    E8  default path                          -> legacy behaviour
    E9  invalid evidence                      -> fail closed
    E10 raw_offset exists but status is LOW_CONFIDENCE -> cannot bypass

E1..E4, E9, E10 are deterministic by construction. E5..E7 drive the **real**
P1 correction path (synthetic PCM through `core.sync_fix.shift_stream`, whose
direction contract is independently pinned by `tests/selftest/suites/
audio_timeline.py`) and compare against an independently computed expectation.

DISCIPLINE
----------
* every threshold is DERIVED from `core.channel_sync.effective_opts`;
* every "cannot happen" claim carries a CONTROL proving the predicate fails on a
  deliberately broken input;
* an empty run is exit 2, never PASS;
* no dependency on `work/` or the real corpus.

Run::

    python tests/audio_sync_p4/test_production_eligibility.py
    python tests/audio_sync_p4/test_production_eligibility.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FS = 48_000
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
        "min_usable_frames": float(eff["min_usable_frames"]),
        "min_confidence": float(eff["min_confidence"]),
        "min_usable_fraction": float(eff["min_usable_fraction"]),
    }


def good_evidence(g: dict) -> dict:
    """Clears every OPERATIVE gate (frame floor + confidence).

    CHANGED 2026-10-16 (copy of tests/audio_sync_p4): `min_usable_fraction` is
    calibrated to 0.0, so a coverage-derived `usable` degenerated to 2 -- BELOW
    the 5-frame floor, i.e. not "good" at all.
    """
    total = 4 * int(g["min_usable_frames"])
    floor = int(g["min_usable_frames"])
    usable = min(total, max(floor * 2, floor + 2))
    return {"usable_frames": usable, "total_frames": total,
            "coverage": usable / total,
            "quality": min(0.99, g["min_confidence"] + 0.4)}


def poor_evidence(g: dict) -> dict:
    """A NUMBER exists and is not trusted.

    CHANGED 2026-10-16 (copy of tests/audio_sync_p4): "coverage below the gate"
    is unsatisfiable at 0.0 (the original expression produced a negative frame
    count). The frame floor produces no value, so the remaining route that keeps
    a numeric estimate is the CONFIDENCE floor -- which is exactly the
    production `low_confidence` reason.
    """
    total = 4 * int(g["min_usable_frames"])
    floor = int(g["min_usable_frames"])
    usable = min(total, floor + 1)
    return {"usable_frames": usable, "total_frames": total,
            "coverage": usable / total,
            "quality": max(0.0, g["min_confidence"] - 0.1)}


def sync(status, offset=480.0, **kw):
    from core.audio_models import AudioSyncResult, SyncStatus

    return AudioSyncResult(status=SyncStatus.coerce(status), offset_samples=offset,
                           **kw)


def verdict(res, **kw):
    from core.audio_models import correction_eligibility

    return correction_eligibility(res, **kw)


# ==========================================================================
# E1  SUCCESS + sufficient evidence -> correction applied
# ==========================================================================
def e1_eligible(g: dict) -> None:
    print("\n== E1  SUCCESS + sufficient evidence ==")
    from core.audio_models import SyncStatus

    res = sync(SyncStatus.SUCCESS, **good_evidence(g))
    v = verdict(res, gate=g)
    value("E1.1 an evidence-satisfying SUCCESS is production eligible",
          v.eligible and v.reason is None,
          f"eligible={v.eligible} status={v.status.value} "
          f"coverage={v.coverage:.3f} gate={g['min_usable_fraction']}")
    value("E1.2 ...and the eligible verdict CARRIES the integer offset",
          v.offset_samples == 480 and isinstance(v.offset_samples, int),
          f"offset_samples={v.offset_samples!r}")
    value("E1.3 ...and the evidence was actually checked",
          v.evidence_checked is True,
          "an evidence-satisfying success must have had its evidence read — "
          "an unchecked pass would be the same defect in a new place")


# ==========================================================================
# E2  LOW_CONFIDENCE + numeric estimate -> correction not applied
# ==========================================================================
def e2_low_confidence(g: dict) -> None:
    print("\n== E2  LOW_CONFIDENCE + a numeric estimate ==")
    from core.audio_models import SyncStatus

    res = sync(SyncStatus.LOW_CONFIDENCE, **poor_evidence(g))
    v = verdict(res, gate=g)
    value("E2.1 LOW_CONFIDENCE is not production eligible",
          not v.eligible, f"reason={v.reason!r}")
    value("E2.2 ...and the verdict does NOT carry a usable offset",
          v.offset_samples is None,
          "refused AND holding a usable number is unrepresentable — the "
          "refusal is not a check that a later reader could skip")
    value("E2.3 ...but the measured number is still reported, not erased",
          v.raw_offset_samples == 480.0,
          f"raw_offset_samples={v.raw_offset_samples} — the report can say "
          f"'measured, not used'")
    value("E2.4 ...and the refusal names the state, from the state machine",
          v.reason == "sync_low_confidence",
          "the reason code is DERIVED from SYNC_STATUS_PATH, not hand-written "
          "at the call site")


# ==========================================================================
# E3 / E4  UNRESOLVED_GAP and FAILURE
# ==========================================================================
def e3_e4_states(g: dict) -> None:
    print("\n== E3/E4  UNRESOLVED_GAP and FAILURE ==")
    from core.audio_models import (
        ELIGIBILITY_REASON_BY_STATUS, SYNC_STATUS_PATH, SyncStatus,
        correction_eligibility,
    )

    # Every state the machine marks non-correcting must be refused, and the
    # check is driven off the machine rather than a hand-written list.
    leaks = []
    for status in SyncStatus:
        if SYNC_STATUS_PATH[status]["may_enter_correction"]:
            continue
        res = sync(status, offset=480.0, **good_evidence(g))
        v = verdict(res, gate=g)
        if v.eligible or v.offset_samples is not None:
            leaks.append(f"{status.value} -> eligible={v.eligible} "
                         f"offset={v.offset_samples}")
    value("E3/E4.1 every non-correcting status in the state machine is refused, "
          "even with perfect evidence",
          not leaks,
          f"probed {len(list(SyncStatus)) - 1} non-correcting statuses; "
          f"leaks={leaks}")

    # the specific two the brief names
    gap = verdict(sync(SyncStatus.LOW_CONFIDENCE, **good_evidence(g)), gate=g)
    value("E3.1 an unresolved/under-trusted value cannot become a trusted "
          "correction",
          not gap.eligible and gap.offset_samples is None,
          f"status={gap.status.value} reason={gap.reason!r}")
    failed = verdict(sync(SyncStatus.FAILED, offset=480.0), gate=g)
    value("E4.1 FAILED is refused, and the reason names failure",
          not failed.eligible and failed.reason == "sync_failed"
          and failed.offset_samples is None,
          f"reason={failed.reason!r}")

    value("E3/E4.2 every non-correcting member has a reason code, and only "
          "those members do",
          set(ELIGIBILITY_REASON_BY_STATUS) == {
              s for s in SyncStatus
              if not SYNC_STATUS_PATH[s]["may_enter_correction"]},
          f"{len(ELIGIBILITY_REASON_BY_STATUS)} codes for "
          f"{len(list(SyncStatus)) - 1} non-correcting members")

    # the P4 per-window state cannot leak in as a correction source
    from core.audio_sync_tv import GapState, CorrectionDecision

    raised = False
    try:
        CorrectionDecision(615, GapState.TRUSTED, source="policy")
    except BaseException:                              # noqa: BLE001
        raised = True
    value("E3.2 ...and a policy-filled window cannot claim TRUSTED at all",
          raised,
          "the per-window state machine refuses the contradiction at "
          "construction, so it cannot reach the eligibility gate as a "
          "trusted value")

    def break_it() -> None:
        assert failed.eligible, "x"
    control("E3/E4.C the refusal predicate can fail", break_it)


# ==========================================================================
# E5 / E6 / E7  the actual PCM result
# ==========================================================================
def _shift_reference(src: np.ndarray, shift: int) -> np.ndarray:
    """out[n] = in[n + shift], out-of-range reads zero — computed independently
    of `core.sync_fix`, from the contract alone."""
    n = src.shape[0]
    idx = np.arange(n, dtype=np.int64) + int(shift)
    out = np.zeros(n, dtype=src.dtype)
    valid = (idx >= 0) & (idx < n)
    out[valid] = src[idx[valid]]
    return out


def e5_e6_e7_pcm(g: dict) -> None:
    print("\n== E5/E6/E7  the PCM result of an eligible correction ==")
    import tempfile

    from core import sync_fix
    from core.channel_sync import effective_opts

    chunk_seconds = float(effective_opts(None)["fix_chunk_seconds"])
    rng = np.random.default_rng(20261011)
    n = 8_192
    src = (rng.standard_normal(n) * 0.25).astype("<f4")

    # E7 first: zero shift must be EXACT identity, not "close enough"
    with tempfile.TemporaryDirectory() as tmp:
        zero_out = Path(tmp) / "zero.f32"
        sync_fix.shift_stream(src, zero_out, delay_samples=0.0,
                              sample_rate=FS, storage_dtype="f32",
                              chunk_seconds=chunk_seconds)
        got0 = np.fromfile(zero_out, dtype="<f4")
    value("E7.1 a zero offset produces a byte-exact identity",
          got0.tobytes() == src.tobytes(),
          f"{n} samples, bit-identical to the source (not merely allclose)")

    for label, delay, expect in (
        ("E5 positive", 480.0, 480),
        ("E6 negative", -480.0, -480),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "out.f32"
            sync_fix.shift_stream(src, dst, delay_samples=delay,
                                  sample_rate=FS, storage_dtype="f32",
                                  chunk_seconds=chunk_seconds)
            got = np.fromfile(dst, dtype="<f4")
        want = _shift_reference(src, expect)
        value(f"{label} fixed offset: out[n] == in[n {expect:+d}] for every n",
              got.shape == want.shape and got.tobytes() == want.tobytes(),
              f"delay_samples={delay} -> shift={expect}; matched an "
              f"independently computed expectation, bit for bit")
        value(f"{label}: length is preserved exactly",
              got.shape[0] == n,
              "out-of-range reads are zero-filled; the sample count never "
              "changes")

    # The eligibility verdict is what feeds that applier, so the integer it
    # carries must be the one the applier would round to.
    for delay, expect in ((480.0, 480), (-480.0, -480), (480.4, 480),
                          (-480.6, -481), (0.0, 0)):
        v = verdict(sync("success", offset=delay))
        value(f"E5/E6/E7 verdict offset for delay_samples={delay} is {expect}",
              v.eligible and v.offset_samples == expect,
              f"offset_samples={v.offset_samples!r} — `rint` semantics, the "
              f"same rule `sync_fix.shift_stream` uses, applied in one place")

    def break_it() -> None:
        assert got0.tobytes() != src.tobytes(), "x"
    control("E5/E6/E7.C the bit-exactness predicate can fail", break_it)


# ==========================================================================
# E8  default path
# ==========================================================================
def e8_default_path(g: dict) -> None:
    print("\n== E8  default path ==")
    from core.audio_format import AudioAlignmentPolicy, resolve_alignment
    from core.audio_models import AudioPlan, AudioSyncResult
    from core.audio_timeline import SyncApplication, _effective_offset

    value("E8.1 AudioPlan = None cannot enable alignment",
          not resolve_alignment(None, AudioAlignmentPolicy.AUTO).enabled,
          f"reason="
          f"{resolve_alignment(None, AudioAlignmentPolicy.AUTO).reason!r}")

    blank = AudioSyncResult()
    value("E8.2 an untouched result is NOT_PROCESSED and not eligible",
          not verdict(blank).eligible
          and verdict(blank).reason == "sync_not_processed",
          f"reason={verdict(blank).reason!r}")
    value("E8.3 ...and it contributes exactly zero shift to the timeline",
          _effective_offset(blank, SyncApplication.APPLY)[0] == 0,
          "no opt-in, no correction — the legacy behaviour, unchanged")

    value("E8.4 a default AudioSyncResult still serialises as before",
          blank.to_dict() == {"status": "not_processed"},
          f"{blank.to_dict()} — the eligibility work added no field to the "
          f"serialised form")

    # eligibility must be a pure read: asking the question changes nothing
    probe = sync("success", **good_evidence(g))
    before = probe.to_dict()
    for _ in range(3):
        verdict(probe, gate=g)
    value("E8.5 asking for eligibility does not mutate the result",
          probe.to_dict() == before,
          "a query, not a step")

    def break_it() -> None:
        assert blank.to_dict() != {"status": "not_processed"}, "x"
    control("E8.C the serialisation-stability predicate can fail", break_it)


# ==========================================================================
# E9  invalid evidence -> fail closed
# ==========================================================================
def e9_fail_closed(g: dict) -> None:
    print("\n== E9  invalid evidence fails closed ==")
    from core.audio_models import (
        REASON_SYNC_EVIDENCE_INSUFFICIENT, REASON_SYNC_OFFSET_INVALID,
        REASON_SYNC_OFFSET_MISSING, REASON_SYNC_STATUS_UNKNOWN,
    )

    cases = [
        ("coverage below the gate while claiming SUCCESS",
         sync("success", offset=480.0, **poor_evidence(g)),
         REASON_SYNC_EVIDENCE_INSUFFICIENT),
        ("confidence below the gate while claiming SUCCESS",
         sync("success", offset=480.0, usable_frames=14, total_frames=20,
              coverage=0.7, quality=0.01),
         REASON_SYNC_EVIDENCE_INSUFFICIENT),
        ("too few usable frames while claiming SUCCESS",
         sync("success", offset=480.0, usable_frames=1, total_frames=20,
              coverage=0.7, quality=0.9),
         REASON_SYNC_EVIDENCE_INSUFFICIENT),
        ("NaN offset",
         sync("success", offset=float("nan")), REASON_SYNC_OFFSET_INVALID),
        ("infinite offset",
         sync("success", offset=float("inf")), REASON_SYNC_OFFSET_INVALID),
        ("offset beyond the int32 shift guard",
         sync("success", offset=float(1 << 40)), REASON_SYNC_OFFSET_INVALID),
        ("no offset at all",
         sync("success", offset=None), REASON_SYNC_OFFSET_MISSING),
        ("no status",
         None, REASON_SYNC_STATUS_UNKNOWN),
    ]
    bad = []
    for label, res, want in cases:
        v = verdict(res, gate=g)
        if v.eligible or v.offset_samples is not None or v.reason != want:
            bad.append(f"{label}: eligible={v.eligible} "
                       f"reason={v.reason!r} (want {want!r})")
    value("E9.1 every invalid input is refused, with the named reason, and "
          "carries no offset",
          not bad, f"checked {len(cases)} invalid inputs; violations={bad}")

    # The failure mode this exists for: an offset must never survive a state
    # that lost its trust. Stated as the invariant, over the whole state space.
    survivors = []
    for status in _all_statuses():
        v = verdict(sync(status, offset=480.0, **poor_evidence(g)), gate=g)
        if not v.eligible and v.offset_samples is not None:
            survivors.append(status.value)
    value("E9.2 no ineligible verdict anywhere carries an offset",
          not survivors, f"survivors={survivors or 'none'}")

    def break_it() -> None:
        assert verdict(sync("success", offset=float("nan")), gate=g).eligible, "x"
    control("E9.C the fail-closed predicate can fail", break_it)


def _all_statuses():
    from core.audio_models import SyncStatus

    return list(SyncStatus)


# ==========================================================================
# E10  raw offset cannot bypass eligibility
# ==========================================================================
def e10_no_bypass(g: dict) -> None:
    print("\n== E10  raw_offset_samples cannot bypass eligibility ==")
    from core.audio_models import SyncStatus
    from core.audio_timeline import SyncApplication, _effective_offset

    res = sync(SyncStatus.LOW_CONFIDENCE, offset=480.0)
    v = verdict(res, gate=g)
    value("E10.1 the raw number is present and visible",
          res.offset_samples == 480.0 and v.raw_offset_samples == 480.0,
          f"res.offset_samples={res.offset_samples} "
          f"verdict.raw_offset_samples={v.raw_offset_samples}")
    value("E10.2 ...and the apply gate still yields ZERO",
          _effective_offset(res, SyncApplication.APPLY)[0] == 0,
          "the single place the model becomes a plan refuses it")
    value("E10.3 ...while still reporting the raw value to the report",
          _effective_offset(res, SyncApplication.APPLY)[1] == 480.0,
          "reported, not applied — the two are separate return values")

    # a second consumer must reach the same answer, or the contract is not a
    # contract. Compare the model gate with the plan object.
    from core.audio_models import AudioChannel, AudioPlan, AudioTrack
    from core.audio_sync import FixedDelayPlan

    ch = AudioChannel(stream_index=0, channel_index=0)
    tr = AudioTrack(track_id="a0", source_stream_index=0, channels=[ch])
    tr.set_sync(res)
    plan = AudioPlan()
    plan.input_tracks = [tr]
    plan.selected_tracks = [tr.track_id]
    plan.selected_channels = [ch.id]
    entry = FixedDelayPlan(entries=(_entry("a0", ch.sync, g),)).offsets()
    value("E10.4 the plan object and the timeline gate agree",
          entry == {} and _effective_offset(ch.sync, SyncApplication.APPLY)[0] == 0,
          f"FixedDelayPlan.offsets()={entry}; two independent consumers, one "
          f"answer — which is what makes it a contract rather than a "
          f"convention")

    # CONTROL: flip the status only, leave the number identical
    ok = sync(SyncStatus.SUCCESS, offset=480.0, **good_evidence(g))
    value("E10.5 CONTROL: the SAME offset with a trusted, evidenced state IS "
          "applied",
          verdict(ok, gate=g).eligible
          and _effective_offset(ok, SyncApplication.APPLY)[0] == 480,
          "identical number, different state -> different decision. The "
          "discriminator is the state, not the value")

    def break_it() -> None:
        assert _effective_offset(res, SyncApplication.APPLY)[0] != 0, "x"
    control("E10.C the no-bypass predicate can fail", break_it)


def _entry(channel_id: str, sync_res, g: dict):
    from core.audio_sync import FixedDelayEntry

    return FixedDelayEntry.of(channel_id, sync_res, gate=g)


# ==========================================================================
# X  the plan object's structural invariant
# ==========================================================================
def x_plan_invariants(g: dict) -> None:
    print("\n== X  FixedDelayPlan: the invariant is structural ==")
    from core.audio_sync import FixedDelayEntry, FixedDelayPlan

    entries = (
        FixedDelayEntry.of("ok", sync("success", **good_evidence(g)), gate=g),
        FixedDelayEntry.of("low", sync("low_confidence", offset=999.0,
                                       **poor_evidence(g)), gate=g),
        FixedDelayEntry.of("fail", sync("failed", offset=999.0), gate=g),
    )
    p = FixedDelayPlan(entries=entries)
    value("X.1 offsets() contains ONLY the eligible entries",
          p.offsets() == {"ok": 480},
          f"{p.offsets()} — an ineligible entry is absent, not present-then-"
          f"skipped")
    value("X.2 ...and every refusal is explained",
          p.refusal_reasons() == {"low": "sync_low_confidence",
                                  "fail": "sync_failed"},
          f"{p.refusal_reasons()}")
    value("X.3 ...and an ineligible entry exposes offset_samples = None",
          all(e.offset_samples is None for e in p.refused_entries),
          "the number 999.0 was supplied to the constructor path and is "
          "absent from the verdict: the bypass is unrepresentable")
    value("X.4 the plan declares what it is NOT",
          p.kind == "fixed_integer_delay"
          and "policy" not in p.to_dict()
          and "coordinate" not in p.to_dict(),
          f"kind={p.kind!r}: fixed integer delay, no gap policy, no "
          f"coordinate contract — so it cannot be mistaken for the P4 "
          f"time-varying plan, which needs D2 and D5")

    # the P4 time-varying plan is a genuinely different object and is still
    # unreachable; the two must not be interchangeable
    from core.audio_sync_tv import CorrectionPlan

    value("X.5 the fixed plan is NOT the P4 CorrectionPlan",
          not issubclass(FixedDelayPlan, CorrectionPlan)
          and not isinstance(p, CorrectionPlan),
          "CorrectionPlan takes policy= and coordinate= as constructor "
          "parameters; using it in production would settle D2 and D5 by "
          "implementation convenience")

    def break_it() -> None:
        assert p.offsets() != {"ok": 480}, "x"
    control("X.C the structural-invariant predicate can fail", break_it)


# ==========================================================================
# S  single decision path
# ==========================================================================
def _code_only(fn) -> str:
    """A function's source with every docstring and comment removed.

    The property S.3 asserts is about CODE: "does this function re-implement
    the state table?". A docstring that *names* the table in order to explain
    why the function delegates to it is the opposite of a violation — so the
    check runs on the de-docstringed AST, not on the text. (This suite made the
    same substring mistake it had just fixed in three other suites; recording
    it here rather than loosening the assertion.)
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def s_single_path(g: dict) -> None:
    print("\n== S  one decision path, not one per consumer ==")
    import inspect

    from core import audio_timeline, audio_sync
    from core.audio_models import correction_eligibility

    tl = inspect.getsource(audio_timeline._effective_offset)
    ap = inspect.getsource(audio_sync.apply_sync_result)
    value("S.1 the timeline apply gate delegates to correction_eligibility",
          "correction_eligibility(" in tl,
          "it no longer decides from `status` alone")
    value("S.2 the P2 write path delegates to correction_eligibility",
          "correction_eligibility(" in ap,
          "it no longer decides from `status` alone")
    tl_code, ap_code = _code_only(audio_timeline._effective_offset), \
        _code_only(audio_sync.apply_sync_result)
    value("S.3 neither re-implements the state table (checked on CODE, not on "
          "prose)",
          "SYNC_STATUS_PATH" not in tl_code
          and "may_enter_correction" not in tl_code
          and "SYNC_STATUS_PATH" not in ap_code
          and "may_enter_correction" not in ap_code,
          "the membership question is asked in exactly one place; a docstring "
          "naming the table is not a second implementation")

    from production import output

    src = inspect.getsource(output.apply_alignment)
    value("S.4 the production alignment path builds a FixedDelayPlan",
          "FixedDelayPlan.from_sync_result" in src,
          "the production entry point consumes the plan object rather than "
          "inspecting per-item status itself")

    # the producer's own report is annotated with the SAME predicate
    from core import channel_sync

    cs_src = inspect.getsource(channel_sync)
    value("S.5 the P1 producer publishes the same verdict on every row",
          "_annotate_eligibility" in cs_src
          and "correction_eligibility" in cs_src,
          "so 'written as fixed' and 'eligible for correction' can be "
          "cross-checked independently instead of trusted to agree")

    value("S.6 the predicate is exposed from the model, not a new module",
          correction_eligibility.__module__ == "core.audio_models",
          f"__module__={correction_eligibility.__module__!r} — the model "
          f"already owns SyncStatus and SYNC_STATUS_PATH; a second home "
          f"would be a second truth")

    def break_it() -> None:
        assert "correction_eligibility(" not in tl, "x"
    control("S.C the delegation predicate can fail", break_it)

    # CONTROL for S.3: the code-only view must still SEE a real use of the
    # table — otherwise "not in" would pass vacuously.
    def break_it_table() -> None:
        assert "SYNC_STATUS_PATH" not in _code_only(
            __import__("core.audio_models", fromlist=["x"]).correction_eligibility
        ), "x"
    control("S.C2 the code-only view still sees the table where it IS used",
            break_it_table)


# ==========================================================================
# B  the real producer/consumer path agrees
# ==========================================================================
def b_producer_consumer(g: dict) -> None:
    print("\n== B  producer report -> plan agrees with the verdict ==")
    from core.audio_sync import FixedDelayPlan

    # rows in the shape `run_channel_sync` writes, including the P4.2 evidence
    # block; the plan must classify them exactly as the predicate does.
    rows = [
        {"stream": 0, "decision": "fixed", "reason": None,
         "shift_samples": 480, "delay_samples": 480.0, "delay_ms": 10.0,
         "confidence": 0.9, "constant": True, "warnings": [],
         "usable_frames": 14, "polarity": 1, "drift_ppm": 0.0,
         "storage_dtype": "f32", "rms_dbfs": -18.0,
         "evidence": {"usable_frames": 14, "total_frames": 20,
                      "coverage": 0.7, "confidence": 0.9}},
        {"stream": 1, "decision": "untouched", "reason": "evidence_insufficient",
         "shift_samples": None, "delay_samples": 480.0, "delay_ms": 10.0,
         "confidence": 0.9, "constant": True, "warnings": [],
         "usable_frames": 11, "polarity": 1, "drift_ppm": 0.0,
         "storage_dtype": "f32", "rms_dbfs": -18.0,
         "evidence": {"usable_frames": 11, "total_frames": 20,
                      "coverage": 0.55, "confidence": 0.9}},
    ]
    rep = {"status": "applied", "detail": "", "anchor_stream": 2,
           "channels": rows, "algo_version": "2.3.0-p1",
           "evidence_gate": g}
    p = FixedDelayPlan.from_channel_sync_report(rep)
    value("B.1 the report's evidence gate is what the plan judges with",
          p.gate == g,
          f"gate={p.gate} — an `opts` override cannot be re-interpreted by "
          f"the default thresholds")
    value("B.2 only the fully-evidenced row is eligible",
          p.offsets() == {"stream:0": 480},
          f"offsets={p.offsets()} refused={p.refusal_reasons()}")
    value("B.3 the refused row is explained by its own reason",
          p.refusal_reasons().get("stream:1") == "sync_low_confidence",
          "the row's reason code survives into the eligibility verdict")

    # the plan is a pure read of the report
    snapshot = json.dumps(rep, sort_keys=True, default=str)
    FixedDelayPlan.from_channel_sync_report(rep)
    value("B.4 plan construction does not mutate the report",
          json.dumps(rep, sort_keys=True, default=str) == snapshot,
          "a view, not a transformation")

    def break_it() -> None:
        assert p.offsets() != {"stream:0": 480}, "x"
    control("B.C the producer-consumer predicate can fail", break_it)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    g = gate()
    print("=" * 78)
    print("P4.4 — production correction eligibility regression")
    print("=" * 78)
    print("production gate (DERIVED from core.channel_sync.effective_opts):")
    print(f"  min_usable_frames={g['min_usable_frames']} "
          f"min_confidence={g['min_confidence']} "
          f"min_usable_fraction={g['min_usable_fraction']}")
    print(f"  good evidence={good_evidence(g)}")
    print(f"  poor evidence={poor_evidence(g)}")
    # CALIBRATED 2026-10-16: "poor" must be below an OPERATIVE gate. Coverage
    # is no longer one (calibrated to 0.0), so the guard accepts either an
    # absolute-floor shortfall or a confidence shortfall -- otherwise it would
    # block forever on a satisfied-but-inert coverage term.
    _poor = poor_evidence(g)
    _below = (
        _poor["coverage"] < g["min_usable_fraction"]
        or _poor["usable_frames"] < int(g["min_usable_frames"])
        or _poor["quality"] < g["min_confidence"]
    )
    if not _below:
        print("BLOCKED: the derived 'poor' fixture is not actually below any "
              "operative gate (coverage/frames/confidence) -- E2/E9 would not "
              "be testing what they claim", file=sys.stderr)
        return 2

    e1_eligible(g)
    e2_low_confidence(g)
    e3_e4_states(g)
    e5_e6_e7_pcm(g)
    e8_default_path(g)
    e9_fail_closed(g)
    e10_no_bypass(g)
    x_plan_invariants(g)
    s_single_path(g)
    b_producer_consumer(g)

    n_cases = len(RESULTS)
    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print("\n" + "=" * 78)
    print(f"cases         : {n_cases}")
    print(f"PASS          : {n_cases - n_fail}")
    print(f"FAIL          : {n_fail}")
    print(f"CONTROL_OK    : {COUNTS['control_ok']}")
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
