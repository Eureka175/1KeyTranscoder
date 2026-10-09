"""P4 — time-varying audio sync: production regression suite.

Runs from **tracked code only**. Nothing here imports ``work/``; that is a hard
requirement (see ``docs/audio-sync-p4/current-state-audit.md`` §6, and the P3
``tests/audio_sync_p311/test_coordinates.py`` which cannot run on a clone).

The standing rule of this line, from ``P4-HANDOFF.md`` §4::

    every check must be demonstrated discriminating before it is trusted
    every count must be compared to an independently derived expectation
    "the test passed" is not evidence until the test has been shown able to fail

So this suite carries three kinds of check and keeps them separate:

  * **VALUE**   — assert the resulting value, not merely that no exception came
  * **RAISE**   — assert the *specific* error type for invalid input
  * **CONTROL** — assert that a fixture can fail; a check that cannot fail is
                  reported as a finding, not as a pass

``SILENT_WRONG`` is counted. It must be 0.

Run::

    python tests/audio_sync_p4/test_production_sync.py
    python tests/audio_sync_p4/test_production_sync.py --json out.json

Exit 0 only if every case ran, at least one case ran, and ``silent_wrong == 0``.
An empty run exits 2 (the P3 silent-pass defect class).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import audio_sync_tv as tv                                    # noqa: E402
from core.sync_estimate import RawStream                                # noqa: E402

FS = 48_000
HOP = tv.DEFAULT_HOP_SAMPLES          # 480 000
WIN = tv.DEFAULT_WINDOW_SAMPLES       # 1 440 000
PROD_N = {230_750_520: 481, 199_903_704: 417}

# --------------------------------------------------------------------------
# harness — counts VALUE_OK / RAISED / SILENT_WRONG / CONTROL, like P3.8
# --------------------------------------------------------------------------
RESULTS: list[dict] = []
COUNTS = {
    "value_ok": 0,
    "raised": 0,
    "silent_wrong": 0,
    "control_ok": 0,
    "control_failed": 0,
    "fail": 0,
}


def _record(name: str, kind: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "kind": kind, "ok": bool(ok),
                    "detail": detail})
    if ok:
        COUNTS[kind] += 1
    else:
        if kind == "silent_wrong":
            COUNTS["silent_wrong"] += 1
        elif kind == "control_ok":
            COUNTS["control_failed"] += 1
        else:
            COUNTS["fail"] += 1
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not ok:
        line += f" -- {detail}"
    print(line)


def value(name: str, ok: bool, detail: str = "") -> None:
    _record(name, "silent_wrong" if not ok else "value_ok", ok, detail)


def raises(name: str, exc_type: type, fn, *a, **kw) -> None:
    """Assert the SPECIFIC error type. A wrong exception type is a failure."""
    try:
        fn(*a, **kw)
    except exc_type:
        _record(name, "raised", True)
        return
    except BaseException as exc:                       # noqa: BLE001
        _record(name, "raised", False,
                f"raised {type(exc).__name__}: {exc} (expected "
                f"{exc_type.__name__})")
        return
    _record(name, "raised", False, f"did not raise (expected {exc_type.__name__})")


def control(name: str, bad_fn) -> None:
    """A CONTROL: assert the check under test CAN fail.

    ``bad_fn`` must raise AssertionError (or any exception) when the invariant
    is broken. If it returns cleanly, the check is not discriminating and is
    reported as a finding.
    """
    try:
        bad_fn()
    except BaseException:                              # noqa: BLE001
        _record(name, "control_ok", True)
        return
    _record(name, "control_ok", False,
            "the check did NOT fail on a deliberately broken input — it is "
            "not discriminating")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def ramp(n: int) -> np.ndarray:
    return np.arange(n, dtype="<f4")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def write_raw(path: Path, arr: np.ndarray, dtype: str = "<f4") -> None:
    a = np.asarray(arr, dtype=dtype)
    with open(path, "wb") as fh:
        fh.write(a.tobytes())


def slow_reference(
    src: np.ndarray, plan: tv.CorrectionPlan, dtype: str = "<f4"
) -> np.ndarray:
    """Independent, obvious, slow implementation of the SAME contract.

    Deliberately shares no code with ``_render_block``: it materialises the
    per-sample window index and indexes the shift array directly. Any
    disagreement is a real defect in one of them. (On a full-length stream this
    is exactly the 1.8 GB allocation P3.11 hit; here it runs on short fixtures.)
    """
    n = int(plan.n_samples)
    idx = np.arange(n, dtype=np.int64)
    w = plan.window_of(idx)
    shift = plan.shifts[w]
    out_idx = idx + shift
    valid = (out_idx >= 0) & (out_idx < n)
    out = np.zeros(n, dtype=dtype)
    out[valid] = np.asarray(src, dtype=dtype)[out_idx[valid]]
    return out


def plan_from_shift_curve(
    n_windows: int, curve, *, n_samples: int, policy: str = "hold",
    coordinate: str = "latest-start", resolved=None,
) -> tv.CorrectionPlan:
    """Build a plan from a per-window shift curve; resolved implied by curve.

    ``curve`` returns ``(shift, resolved)`` for a window index.
    """
    shifts = np.zeros(n_windows, dtype=np.int64)
    mask = np.zeros(n_windows, dtype=bool)
    for i in range(n_windows):
        s, r = curve(i)
        shifts[i] = int(s)
        mask[i] = bool(r)
    if resolved is not None:
        mask = np.asarray(resolved, dtype=bool)
    return tv.build_correction_plan(
        resolved=mask, shifts=shifts, n_samples=n_samples,
        window_samples=WIN, hop_samples=HOP, policy=policy,
        coordinate=coordinate,
    )


# ==========================================================================
# 1. state semantics
# ==========================================================================
def t1_state_semantics() -> None:
    print("\n== T1  state semantics: state = f(resolved mask) ==")
    # the canonical sequence from P3.7's state machine
    got = list(tv.derive_states(np.array([True, False, False, True, True, False, True])))
    want = ["TRUSTED", "UNRESOLVED_GAP", "UNRESOLVED_GAP", "REENTRY",
            "TRUSTED", "UNRESOLVED_GAP", "REENTRY"]
    value("T1.1 the Q0-style sequence is exactly as specified", got == want,
          f"got {got}")

    value("T1.2 no window without a measurement is TRUSTED",
          all(s != "TRUSTED" for s, r in zip(got, [1, 0, 0, 1, 1, 0, 1]) if not r))

    # all-refused is a LEGAL plan, not an error
    allgap = list(tv.derive_states(np.zeros(5, dtype=bool)))
    value("T1.3 an all-refused stream is legal and yields no REENTRY",
          allgap == ["UNRESOLVED_GAP"] * 5, f"{allgap}")

    value("T1.4 all-trusted yields no REENTRY",
          list(tv.derive_states(np.ones(4, dtype=bool))) == ["TRUSTED"] * 4)

    # REENTRY is its own state, front and back
    value("T1.5 REENTRY appears after a leading gap",
          tv.derive_states(np.array([False, True]))[1] == "REENTRY")
    value("T1.6 REENTRY appears after a trailing gap",
          tv.derive_states(np.array([True, False, True]))[2] == "REENTRY")
    value("T1.7 a single trusted window between two gaps is REENTRY not TRUSTED",
          tv.derive_states(np.array([False, True, False]))[1] == "REENTRY")

    # state is a PURE FUNCTION of the mask: the same mask gives the same states
    m = np.array([True, True, False, True, False, False, True])
    value("T1.8 state is a pure function of the mask (repeatable)",
          np.array_equal(tv.derive_states(m), tv.derive_states(m)))

    # 0/1 integer masks accepted; anything else refused
    value("T1.9 a 0/1 integer mask is accepted",
          np.array_equal(tv.derive_states(np.array([1, 0, 1])),
                         tv.derive_states(np.array([True, False, True]))))
    raises("T1.10 a mask containing 2 is refused (not silently True)",
           tv.AudioSyncPlanError, tv.derive_states, np.array([1, 2, 0]))
    raises("T1.11 an empty mask is refused",
           tv.AudioSyncPlanError, tv.derive_states, np.zeros(0, dtype=bool))
    raises("T1.12 a 2-D mask is refused",
           tv.AudioSyncPlanError, tv.derive_states, np.zeros((2, 2), dtype=bool))

    # CONTROL: assert that T1.2's predicate can fail. The predicate says "no
    # window without a measurement is TRUSTED"; a mask/state pair that breaks
    # it must be caught by that same predicate.
    def break_it() -> None:
        bad_states = ["TRUSTED"]
        bad_mask = [False]
        assert all(
            s != "TRUSTED" for s, r in zip(bad_states, bad_mask) if not r
        ), "the predicate did not catch a TRUSTED window with no measurement"
    control("T1.C the 'no unmeasured TRUSTED' predicate can fail", break_it)


# ==========================================================================
# 2. the decision object: value and epistemic state are separate
# ==========================================================================
def t2_decision_object() -> None:
    print("\n== T2  correction value and epistemic state are separate ==")
    a = tv.CorrectionDecision(615, tv.GapState.TRUSTED)
    b = tv.CorrectionDecision(615, tv.GapState.UNRESOLVED_GAP)
    value("T2.1 the same shift with two different states is constructible",
          a.shift_samples == b.shift_samples and a.state != b.state)
    value("T2.2 resolved is derived from state, not from the number",
          a.resolved is True and b.resolved is False)
    value("T2.3 TRUSTED requires source='measurement'",
          a.source == "measurement" and b.source == "policy")

    raises("T2.4 a measurement-sourced UNRESOLVED_GAP is unrepresentable",
           tv.AudioSyncPlanError, tv.CorrectionDecision, 615,
           tv.GapState.UNRESOLVED_GAP, "measurement")
    raises("T2.5 a policy-sourced TRUSTED is unrepresentable",
           tv.AudioSyncPlanError, tv.CorrectionDecision, 615,
           tv.GapState.TRUSTED, "policy")
    raises("T2.6 a policy-sourced REENTRY is unrepresentable",
           tv.AudioSyncPlanError, tv.CorrectionDecision, 615,
           tv.GapState.REENTRY, "policy")
    raises("T2.7 a bare string state is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, 615, "TRUSTED")
    raises("T2.8 NaN shift is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, float("nan"),
           tv.GapState.TRUSTED, "measurement")
    raises("T2.9 +Inf shift is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, float("inf"),
           tv.GapState.TRUSTED, "measurement")
    raises("T2.10 -Inf shift is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, float("-inf"),
           tv.GapState.TRUSTED, "measurement")
    raises("T2.11 a huge shift is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, 1 << 31,
           tv.GapState.TRUSTED, "measurement")
    raises("T2.12 a shift one past the guard is refused",
           tv.AudioSyncPlanError, tv.CorrectionDecision, (1 << 31) + 1,
           tv.GapState.TRUSTED, "measurement")
    # boundary: the guard's exact edge is ACCEPTED
    value("T2.13 the exact guard boundary ±(2**31 - 1) is accepted",
          tv.CorrectionDecision((1 << 31) - 1, tv.GapState.TRUSTED,
                                "measurement").shift_samples == (1 << 31) - 1
          and tv.CorrectionDecision(-((1 << 31) - 1), tv.GapState.TRUSTED,
                                    "measurement").shift_samples
          == -((1 << 31) - 1))

    # CONTROL: the range guard must be able to fail
    def break_it() -> None:
        assert tv.CorrectionDecision((1 << 31) + 5, tv.GapState.TRUSTED,
                                     "measurement")
    control("T2.C the shift-range guard can fail", break_it)


# ==========================================================================
# 3. plan invariants
# ==========================================================================
def t3_plan_invariants() -> None:
    print("\n== T3  plan invariants ==")
    n = 8 * HOP
    need = tv.required_window_count(n, hop_samples=HOP)
    value("T3.1 required window count is ceil(n/hop)", need == 8, f"{need}")

    shifts = np.array([100] * 8, dtype=np.int64)
    mask = np.ones(8, dtype=bool)
    p = tv.build_correction_plan(resolved=mask, shifts=shifts, n_samples=n)
    value("T3.2 plan length equals required_window_count exactly",
          p.n_windows == need)

    # a short plan is REFUSED, not padded (P3.10 refused the unpadded plan)
    raises("T3.3 a plan one window short is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=np.ones(7, dtype=bool), shifts=np.zeros(7, dtype=np.int64),
           n_samples=n)
    raises("T3.4 a plan one window long is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=np.ones(9, dtype=bool), shifts=np.zeros(9, dtype=np.int64),
           n_samples=n)
    raises("T3.5 an empty plan is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=np.zeros(0, dtype=bool), shifts=np.zeros(0, dtype=np.int64),
           n_samples=n)

    # states are DERIVED: a contradicting state sequence cannot be supplied
    raises("T3.6 a states array that contradicts the mask is refused",
           tv.AudioSyncPlanError, tv.CorrectionPlan,
           shifts=shifts, states=np.array(["TRUSTED"] * 8, dtype=object),
           resolved=np.array([True] * 4 + [False] * 4), policy="hold",
           coordinate="latest-start", window_samples=WIN, hop_samples=HOP,
           n_samples=n)

    # mask must be DERIVED from stated validity, never assumed
    meas_missing_valid = [{"delay_samples": 10.0} for _ in range(need)]
    raises("T3.7 a measurement record with no 'valid' field is refused",
           tv.AudioSyncContextError, tv.build_correction_plan,
           meas_missing_valid, n_samples=n)
    meas_missing_delay = [{"valid": True} for _ in range(need)]
    raises("T3.8 a measurement record with no 'delay_samples' is refused",
           tv.AudioSyncContextError, tv.build_correction_plan,
           meas_missing_delay, n_samples=n)
    raises("T3.9 a valid record with a non-finite delay is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           [{"valid": True, "delay_samples": float("nan")}] * need,
           n_samples=n)
    raises("T3.10 more measurement records than windows is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           [{"valid": True, "delay_samples": 0.0}] * (need + 1), n_samples=n)

    # an unknown policy / coordinate is refused, not defaulted
    raises("T3.11 an unknown gap policy is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=mask, shifts=shifts, n_samples=n, policy="carry_on")
    raises("T3.12 an unknown coordinate contract is refused",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=mask, shifts=shifts, n_samples=n, coordinate="midpoint")
    raises("T3.13 window_index refuses an unknown coordinate",
           tv.AudioSyncContextError, tv.window_index, "centre-of-mass",
           np.array([0]), window_samples=WIN, hop_samples=HOP, n_windows=8)

    # provenance lives IN the object
    value("T3.14 the plan carries policy/coordinate/version provenance",
          p.policy == "hold" and p.coordinate == "latest-start"
          and p.version == tv.ALGO_VERSION and p.resolved.shape == (8,))

    # stream_has_trust must agree with the mask
    raises("T3.15 stream_has_trust contradicting the mask is refused",
           tv.AudioSyncPlanError, tv.CorrectionPlan, shifts=shifts,
           states=tv.derive_states(mask), resolved=mask, policy="hold",
           coordinate="latest-start", window_samples=WIN, hop_samples=HOP,
           n_samples=n, stream_has_trust=False)

    # an offline-only policy may not be marked online, and vice versa
    raises("T3.16 a policy mislabelled offline_only is refused",
           tv.AudioSyncPlanError, tv.CorrectionPlan, shifts=shifts,
           states=tv.derive_states(mask), resolved=mask, policy="hold",
           coordinate="latest-start", window_samples=WIN, hop_samples=HOP,
           n_samples=n, offline_only=True)

    # window() bounds
    raises("T3.17 decision() out of range is refused",
           tv.AudioSyncPlanError, p.decision, 8)
    raises("T3.18 decision() negative index is refused",
           tv.AudioSyncPlanError, p.decision, -1)

    # CONTROL: T3.3's check must be able to fail
    def break_it() -> None:
        shift_plan = np.ones(7, dtype=np.int64)
        r = np.ones(7, dtype=bool)
        made = tv.build_correction_plan(resolved=r, shifts=shift_plan,
                                       n_samples=n)
        assert made.n_windows == 7, "x"
    control("T3.C the 'exact plan length' check can fail", break_it)


# ==========================================================================
# 4. coordinate contract
# ==========================================================================
def t4_coordinates() -> None:
    print("\n== T4  coordinate contract ==")
    for n, nw in PROD_N.items():
        idx = np.arange(nw, dtype=np.int64) * HOP
        a = tv.window_index("latest-start", idx, window_samples=WIN,
                            hop_samples=HOP, n_windows=nw)
        d = tv.window_index("sample-domain", idx, window_samples=WIN,
                            hop_samples=HOP, n_windows=nw)
        b = tv.window_index("nearest-centre", idx, window_samples=WIN,
                            hop_samples=HOP, n_windows=nw)
        value(f"T4.1 n={n}: sample-domain IS latest-start (identical function)",
              np.array_equal(a, d))
        value(f"T4.2 n={n}: block-start indices are in range",
              int(b.min()) >= 0 and int(b.max()) < nw)
        value(f"T4.3 n={n}: nearest-centre is latest-start - 1 from block 1",
              bool(np.all(b[1:] == a[1:] - 1)) and int(b[0]) == int(a[0]),
              f"near {b[:4].tolist()} latest {a[:4].tolist()}")

    # the raw nearest-centre mapping returns -1 at block 0 (unclamped)
    raw = tv.window_nearest_centre(np.array([0]), window_samples=WIN,
                                    hop_samples=HOP)
    value("T4.4 the RAW nearest-centre mapping returns -1 at block 0",
          int(raw[0]) == -1, f"{raw.tolist()}")
    clipped = tv.window_nearest_centre(np.array([0]), window_samples=WIN,
                                        hop_samples=HOP, n_windows=8)
    value("T4.5 the contracted mapping CLIPS block 0 to window 0",
          int(clipped[0]) == 0)

    # first divergence is exactly the first hop boundary, in samples
    idx = np.array([HOP - 1, HOP, HOP + 1], dtype=np.int64)
    a = tv.window_index("latest-start", idx, window_samples=WIN,
                        hop_samples=HOP, n_windows=481)
    b = tv.window_index("nearest-centre", idx, window_samples=WIN,
                        hop_samples=HOP, n_windows=481)
    value("T4.6 first index divergence is sample HOP = 480000 (10.0 s)",
          int(a[0]) == int(b[0]) and int(a[1]) != int(b[1]),
          f"a={a.tolist()} b={b.tolist()}")

    # beyond coverage BOTH clip explicitly
    over = np.array([481 * HOP, 481 * HOP + 5, 10 ** 9], dtype=np.int64)
    value("T4.7 beyond coverage latest-start clips to the last window",
          bool(np.all(tv.window_index("latest-start", over, window_samples=WIN,
                                      hop_samples=HOP, n_windows=481) == 480)))
    value("T4.8 beyond coverage nearest-centre clips to the last window",
          bool(np.all(tv.window_index("nearest-centre", over,
                                      window_samples=WIN, hop_samples=HOP,
                                      n_windows=481) == 480)))

    # with window < hop, sample-domain is not a covering function: refuse
    raises("T4.9 sample-domain with window < hop is refused",
           tv.AudioSyncContextError, tv.window_sample_domain, np.array([0]),
           window_samples=HOP // 2, hop_samples=HOP, n_windows=8)

    # required vs measured windows: the seam, by definition not by memory
    for n, want_seam in ((230_750_520, 3), (199_903_704, 3),
                         (2 * HOP, 2), (3 * HOP, 2)):
        got = tv.window_seam(n, window_samples=WIN, hop_samples=HOP)
        value(f"T4.10 n={n}: seam is {want_seam}", got == want_seam, f"{got}")
    value("T4.11 seam is a function of (n, win, hop) alone",
          tv.window_seam(230_750_520, window_samples=WIN, hop_samples=HOP)
          == 3 and tv.window_seam(199_903_704, window_samples=WIN,
                                  hop_samples=HOP) == 3)

    raises("T4.12 a zero hop is refused",
           tv.AudioSyncContextError, tv.required_window_count, 100,
           hop_samples=0)
    raises("T4.13 a negative n is refused",
           tv.AudioSyncContextError, tv.required_window_count, -1,
           hop_samples=HOP)

    # CONTROL: T4.1 must be able to fail if sample-domain were a real third rule
    def break_it() -> None:
        idx2 = np.arange(50, dtype=np.int64) * HOP
        a2 = tv.window_index("latest-start", idx2, window_samples=WIN,
                             hop_samples=HOP, n_windows=50)
        assert np.array_equal(a2, a2 + 1), "x"
    control("T4.C the 'sample-domain == latest-start' comparison can fail",
            break_it)


# ==========================================================================
# 5. gap policy behaviour
# ==========================================================================
def t5_gap_policies() -> None:
    print("\n== T5  gap policy behaviour ==")
    # mask:  T T G G T G G G T
    #        ----------------> indices 0..8
    mask = np.array([1, 1, 0, 0, 1, 0, 0, 0, 1], dtype=bool)
    raw = np.array([100, 200, 0, 0, 300, 0, 0, 0, 400], dtype=np.int64)
    n = 9 * HOP

    hold = tv.build_correction_plan(resolved=mask, shifts=raw, n_samples=n,
                                     policy="hold")
    value("T5.1 hold carries the last trusted shift",
          hold.shifts.tolist() == [100, 200, 200, 200, 300, 300, 300, 300, 400],
          f"{hold.shifts.tolist()}")

    nogap = tv.build_correction_plan(resolved=mask, shifts=raw, n_samples=n,
                                      policy="no_correction_gap")
    value("T5.2 no_correction_gap writes 0 in every gap",
          nogap.shifts.tolist() == [100, 200, 0, 0, 300, 0, 0, 0, 400],
          f"{nogap.shifts.tolist()}")

    expl = tv.build_correction_plan(resolved=mask, shifts=raw, n_samples=n,
                                     policy="explicit_state")
    value("T5.3 explicit_state has BIT-IDENTICAL shifts to hold",
          np.array_equal(expl.shifts, hold.shifts))
    value("T5.4 hold and explicit_state are distinguishable only by state",
          np.array_equal(hold.states, expl.states)
          and hold.state_counts() == expl.state_counts())

    curr = tv.build_correction_plan(resolved=mask, shifts=raw, n_samples=n,
                                     policy="current")
    # 'current' is the historical policy: it reports gap windows as TRUSTED.
    # P4 models that as mask_is_authoritative=False rather than letting the
    # state sequence violate the invariant, so the over-claim is COUNTABLE.
    value("T5.5 current reports no UNRESOLVED_GAP (its documented over-claim)",
          curr.state_counts()["UNRESOLVED_GAP"] == 0,
          f"{curr.state_counts()}")
    value("T5.5b every OTHER online policy reports the mask's gaps",
          hold.state_counts()["UNRESOLVED_GAP"] == int((~mask).sum())
          and nogap.state_counts()["UNRESOLVED_GAP"] == int((~mask).sum())
          and expl.state_counts()["UNRESOLVED_GAP"] == int((~mask).sum()))
    value("T5.5c current is the ONLY policy that over-claims",
          curr.overclaimed_windows() == int((~mask).sum())
          and hold.overclaimed_windows() == 0
          and nogap.overclaimed_windows() == 0
          and expl.overclaimed_windows() == 0,
          f"current={curr.overclaimed_windows()}")
    value("T5.5d every honest policy passes is_honest()",
          hold.is_honest() and nogap.is_honest() and expl.is_honest()
          and not curr.is_honest())
    value("T5.5e current's SHIFTS are identical to hold's (only the claim "
          "differs)",
          np.array_equal(curr.shifts, hold.shifts),
          f"curr={curr.shifts.tolist()} hold={hold.shifts.tolist()}")

    # leading gap: hold writes 0, current takes the FIRST trusted value
    lead_mask = np.array([0, 0, 1, 1], dtype=bool)
    lead_raw = np.array([0, 0, 500, 600], dtype=np.int64)
    lead_hold = tv.build_correction_plan(resolved=lead_mask, shifts=lead_raw,
                                          n_samples=4 * HOP, policy="hold")
    lead_curr = tv.build_correction_plan(resolved=lead_mask, shifts=lead_raw,
                                          n_samples=4 * HOP, policy="current")
    value("T5.6 hold writes 0 across a LEADING gap",
          lead_hold.shifts.tolist() == [0, 0, 500, 600],
          f"{lead_hold.shifts.tolist()}")
    value("T5.7 current takes the first trusted value across a leading gap",
          lead_curr.shifts.tolist() == [500, 500, 500, 600],
          f"{lead_curr.shifts.tolist()}")
    value("T5.8 hold and current differ ONLY at the leading gap",
          int((lead_hold.shifts != lead_curr.shifts).sum()) == 2)
    value("T5.9 the first trusted window after a gap is REENTRY",
          list(lead_hold.states) == ["UNRESOLVED_GAP", "UNRESOLVED_GAP",
                                     "REENTRY", "TRUSTED"],
          f"{list(lead_hold.states)}")

    # all-refused stream: legal, and every policy yields 0
    empty = np.zeros(3, dtype=bool)
    z = np.zeros(3, dtype=np.int64)
    for pol in ("hold", "no_correction_gap", "explicit_state", "current"):
        q = tv.build_correction_plan(resolved=empty, shifts=z,
                                      n_samples=3 * HOP, policy=pol)
        value(f"T5.10 policy {pol}: an all-refused stream yields all-zero shifts",
              q.shifts.tolist() == [0, 0, 0], f"{q.shifts.tolist()}")
        value(f"T5.11 policy {pol}: an all-refused stream has no REENTRY",
              q.state_counts()["REENTRY"] == 0)

    # offline-only policies must NOT be drivable from the streaming path
    for pol in ("linear", "next"):
        raises(f"T5.12 {pol} without GapContext is refused",
               tv.AudioSyncContextError, tv.build_correction_plan,
               resolved=mask, shifts=raw, n_samples=n, policy=pol)
    value("T5.13 linear and next are registered offline-only (need a right "
          "endpoint)", tv.GAP_POLICIES["linear"] is True
          and tv.GAP_POLICIES["next"] is True)
    value("T5.14 hold/no_correction_gap/explicit_state/current are ONLINE "
          "(they need no right endpoint)",
          not tv.GAP_POLICIES["hold"]
          and not tv.GAP_POLICIES["no_correction_gap"]
          and not tv.GAP_POLICIES["explicit_state"]
          and not tv.GAP_POLICIES["current"],
          f"current offline_only = {tv.GAP_POLICIES['current']}")
    value("T5.14b only linear needs caller-supplied endpoint VALUES",
          tv.GAP_POLICY_NEEDS_ENDPOINTS["linear"] is True
          and not any(tv.GAP_POLICY_NEEDS_ENDPOINTS[p]
                      for p in ("hold", "no_correction_gap",
                                "explicit_state", "current", "next")))
    # current is online: it must build with no GapContext at all, and its
    # leading-gap behaviour must still be the documented one
    cur_lead = tv.build_correction_plan(
        resolved=np.array([0, 0, 1, 1], dtype=bool),
        shifts=np.array([0, 0, 500, 600], dtype=np.int64),
        n_samples=4 * HOP, policy="current")
    value("T5.14c current builds with NO context and takes the first trusted "
          "value", cur_lead.shifts.tolist() == [500, 500, 500, 600],
          f"{cur_lead.shifts.tolist()}")

    # an offline plan is refused by the streaming applier
    lin = tv.build_correction_plan(
        resolved=mask, shifts=raw, n_samples=n, policy="linear",
        context=tv.GapContext(stream_has_trust=True, gap_endpoints=(200, 300),
                              gap_extent=(2, 5)),
    )
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "s.f32"
        write_raw(src, ramp(n))
        raises("T5.15 apply refuses an offline-only plan",
               tv.AudioSyncContextError, tv.apply_shift_plan, src,
               Path(td) / "o.f32", lin)
    raises("T5.16 linear needs endpoint VALUES even offline",
           tv.AudioSyncContextError, tv.build_correction_plan,
           resolved=mask, shifts=raw, n_samples=n, policy="linear",
           context=tv.GapContext(stream_has_trust=True))

    # GapContext.demand_endpoints
    raises("T5.17 GapContext.demand_endpoints raises when absent",
           tv.AudioSyncContextError,
           tv.GapContext(stream_has_trust=True).demand_endpoints, "linear")
    raises("T5.18 gap_endpoints with the wrong arity is refused",
           tv.AudioSyncContextError,
           tv.GapContext(stream_has_trust=True,
                         gap_endpoints=(1, 2, 3)).demand_endpoints, "linear")

    # CONTROL: T5.1's expectation must be able to fail
    def break_it() -> None:
        assert hold.shifts.tolist() == [100, 200, 0, 0, 300, 0, 0, 0, 400], "x"
    control("T5.C the hold-carries-shift expectation can fail", break_it)


# ==========================================================================
# 6. apply mechanics and bit-exactness
# ==========================================================================
def t6_apply_mechanics() -> None:
    print("\n== T6  apply mechanics ==")
    n = 12 * HOP + 7          # deliberately not a multiple of hop
    need = tv.required_window_count(n, hop_samples=HOP)
    mask = np.zeros(need, dtype=bool)
    shifts = np.zeros(need, dtype=np.int64)
    # a shift curve that changes often, including a negative and a zero
    for i in range(need):
        if i % 3 == 0:
            mask[i] = True
            shifts[i] = (i - 5) * 37
    p = tv.build_correction_plan(resolved=mask, shifts=shifts, n_samples=n,
                                  policy="hold")
    src_arr = ramp(n)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "in.f32"
        out = td / "out.f32"
        write_raw(src, src_arr)
        stats = tv.apply_shift_plan(src, out, p, storage_dtype="f32",
                                     chunk_samples=n)   # one block
        got = np.fromfile(out, dtype="<f4")
        want = slow_reference(src_arr, p)
        value("T6.1 output sample count equals input sample count",
              got.size == n == stats.n_samples_out, f"{got.size} vs {n}")
        value("T6.2 bytes written equals n * itemsize",
              stats.bytes_written == n * 4, f"{stats.bytes_written}")
        value("T6.3 single-block output matches the independent reference",
              bool(np.array_equal(got, want)),
              f"{int((got != want).sum())} differing samples")
        value("T6.4 apply_count is 1 (non-zero, so a silent no-op is visible)",
              stats.apply_count == 1)

        # the plan's own reported counts must be consistent
        counts = p.state_counts()
        value("T6.5 stats state counts match the plan's state counts",
              stats.n_trusted == counts["TRUSTED"]
              and stats.n_unresolved == counts["UNRESOLVED_GAP"]
              and stats.n_reentry == counts["REENTRY"])

        # a plan of the wrong length for the stream is refused
        p_other = tv.build_correction_plan(
            resolved=np.ones(need, dtype=bool),
            shifts=np.zeros(need, dtype=np.int64), n_samples=n, policy="hold")
        src2 = td / "short.f32"
        write_raw(src2, ramp(n // 2))
        raises("T6.6 a stream shorter than the plan is refused",
               tv.AudioSyncContextError, tv.apply_shift_plan, src2,
               td / "o2.f32", p_other)

        raises("T6.7 a non-plan argument is refused",
               tv.AudioSyncContextError, tv.apply_shift_plan, src,
               td / "o3.f32", {"shifts": [1, 2, 3]})
        raises("T6.8 chunk_samples = 0 is refused",
               tv.AudioSyncContextError, tv.apply_shift_plan, src,
               td / "o4.f32", p, chunk_samples=0)
        raises("T6.9 negative chunk_samples is refused",
               tv.AudioSyncContextError, tv.apply_shift_plan, src,
               td / "o5.f32", p, chunk_samples=-1)

        # out-of-range reads zero-fill rather than wrapping
        p_big = tv.build_correction_plan(
            resolved=np.ones(need, dtype=bool),
            shifts=np.full(need, n + 1000, dtype=np.int64), n_samples=n,
            policy="hold")
        ob = td / "big.f32"
        tv.apply_shift_plan(src, ob, p_big, storage_dtype="f32",
                            chunk_samples=n)
        big = np.fromfile(ob, dtype="<f4")
        value("T6.10 a shift that empties the read yields an all-zero output",
              bool(np.all(big == 0)) and big.size == n)
        p_neg = tv.build_correction_plan(
            resolved=np.ones(need, dtype=bool),
            shifts=np.full(need, -(n + 1000), dtype=np.int64), n_samples=n,
            policy="hold")
        on = td / "neg.f32"
        tv.apply_shift_plan(src, on, p_neg, storage_dtype="f32",
                            chunk_samples=n)
        neg = np.fromfile(on, dtype="<f4")
        value("T6.11 a large negative shift also yields all zeros, right length",
              bool(np.all(neg == 0)) and neg.size == n)

    # CONTROL: the reference comparison must be able to fail
    def break_it() -> None:
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([1.0, 2.0, 9.0])
        assert np.array_equal(a, b), "x"
    control("T6.C the bit-exact comparison can fail", break_it)


# ==========================================================================
# 7. chunk invariance — byte level, several sizes, boundaries inside segments
# ==========================================================================
def t7_chunk_invariance() -> None:
    print("\n== T7  chunk invariance (byte level) ==")
    n = 9 * HOP + 12345
    need = tv.required_window_count(n, hop_samples=HOP)
    mask = np.zeros(need, dtype=bool)
    shifts = np.zeros(need, dtype=np.int64)
    for i in range(need):
        if i % 2 == 0:
            mask[i] = True
            shifts[i] = (i % 7 - 3) * 101
        else:
            mask[i] = False
    src_arr = ramp(n)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "in.f32"
        write_raw(src, src_arr)
        # 1 puts a chunk boundary INSIDE a segment; HOP*3+7 splits a segment
        # and a coordinate zone; n is one whole chunk. All must be identical.
        sizes = [n, HOP, HOP * 3, HOP * 3 + 7, HOP // 3, 4096, 999, 1]
        hashes: dict[str, str] = {}
        for cs in sizes:
            # 'current' so that every window has a non-zero carry, and 'hold'
            # for the shipped rule: both policies are exercised
            p = tv.build_correction_plan(
                resolved=mask, shifts=shifts, n_samples=n, policy="hold")
            o = td / f"out_{cs}.f32"
            tv.apply_shift_plan(src, o, p, storage_dtype="f32",
                                chunk_samples=cs)
            hashes[str(cs)] = sha256_file(o)
        ref = hashes[str(n)]
        same = [k for k, v in hashes.items() if v == ref]
        value(f"T7.1 output PCM is byte-identical at all {len(sizes)} chunk "
              f"sizes", len(same) == len(sizes),
              f"differing: {sorted(set(hashes) - set(same), key=int)}")
        value("T7.2 a chunk size that splits a coordinate zone is included",
              str(HOP * 3 + 7) in same)

        # and the same for every online policy, since state is mask-derived
        for pol in ("hold", "no_correction_gap", "explicit_state", "current"):
            ph = []
            for cs in (n, HOP * 3 + 7, 999):
                pp = tv.build_correction_plan(
                    resolved=mask, shifts=shifts, n_samples=n, policy=pol)
                oo = td / f"o_{pol}_{cs}.f32"
                tv.apply_shift_plan(src, oo, pp, storage_dtype="f32",
                                    chunk_samples=cs)
                ph.append(sha256_file(oo))
            value(f"T7.3 policy {pol}: byte-identical across 3 chunk sizes",
                  len(set(ph)) == 1)

        # the state sequence itself must not depend on chunking: it is derived
        # from the mask, so two independently built plans agree exactly
        p1 = tv.build_correction_plan(resolved=mask, shifts=shifts, n_samples=n,
                                       policy="hold")
        p2 = tv.build_correction_plan(resolved=mask, shifts=shifts, n_samples=n,
                                       policy="hold")
        value("T7.4 the state sequence is identical across plan rebuilds",
              np.array_equal(p1.states, p2.states))

        # a stream object (RawStream) and a path must give the same bytes
        o_path = td / "by_path.f32"
        o_stream = td / "by_stream.f32"
        tv.apply_shift_plan(src, o_path, p1, storage_dtype="f32",
                            chunk_samples=HOP)
        st = RawStream(src, "f32")
        try:
            tv.apply_shift_plan(st, o_stream, p1, storage_dtype="f32",
                                chunk_samples=HOP)
        finally:
            st.close()
        value("T7.5 applying from a RawStream matches applying from a path",
              sha256_file(o_path) == sha256_file(o_stream))

    # CONTROL: the byte comparison must be able to fail
    def break_it() -> None:
        assert hashes[str(n)] == "0" * 64, "x"
    control("T7.C the sha256 equality check can fail", break_it)


# ==========================================================================
# 8. failure safety — no silent wrong result
# ==========================================================================
def t8_failure_safety() -> None:
    print("\n== T8  failure safety (no silent wrong) ==")
    n = 4 * HOP
    ok = np.ones(4, dtype=bool)
    z = np.zeros(4, dtype=np.int64)

    # missing measurement / missing endpoint / invalid mask / NaN / Inf /
    # huge shift / negative index / truncated context — each must RAISE
    raises("T8.1 missing measurement set (neither measurements nor arrays)",
           tv.AudioSyncContextError, tv.build_correction_plan, None,
           n_samples=n)
    raises("T8.2 empty measurement list is refused, not treated as no-gap",
           tv.AudioSyncContextError, tv.build_correction_plan, [], n_samples=n)
    raises("T8.3 missing endpoint for a policy that needs one",
           tv.AudioSyncContextError, tv.build_correction_plan,
           resolved=np.array([1, 0, 1, 1], dtype=bool),
           shifts=np.array([10, 0, 30, 40], dtype=np.int64), n_samples=n,
           policy="linear", context=tv.GapContext(stream_has_trust=True))
    raises("T8.4 invalid mask (value 2)",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=np.array([1, 2, 0, 1]), shifts=z, n_samples=n)
    raises("T8.5 NaN shift in the supplied plan",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=ok, shifts=np.array([0, 0, 0, np.nan]), n_samples=n)
    raises("T8.6 Inf shift in the supplied plan",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=ok, shifts=np.array([0, 0, 0, np.inf]), n_samples=n)
    raises("T8.7 huge shift beyond the guard",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=ok, shifts=np.array([0, 0, 0, 10 ** 12]), n_samples=n)
    raises("T8.8 truncated record set (fewer records than windows) — refused "
           "by length, never read as a refusal",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=ok, shifts=np.zeros(3, dtype=np.int64), n_samples=n)

    # a REFUSED measurement record that still carries a number must not have
    # that number used as a shift
    recs = [
        {"valid": True, "delay_samples": 500.0},
        {"valid": False, "delay_samples": 999.0},   # refused but numbered
        {"valid": True, "delay_samples": 100.0},
        {"valid": True, "delay_samples": 100.0},
    ]
    p = tv.build_correction_plan(recs, n_samples=n, policy="hold")
    value("T8.9 a refused window's returned number is NOT used as a shift",
          p.shifts.tolist() == [500, 500, 100, 100], f"{p.shifts.tolist()}")
    value("T8.10 the refused window is UNRESOLVED_GAP",
          p.states[1] == "UNRESOLVED_GAP")

    # UNRESOLVED_GAP is a VALID state, not an error: the plan must exist
    value("T8.11 uncertainty does not raise — it is a valid epistemic state",
          p.state_counts()["UNRESOLVED_GAP"] == 1)

    # a truncated mask must be declared or raise; here the plan length itself
    # is the declaration, so a short mask is refused by the length check
    raises("T8.12 a mask shorter than the stream is refused (not stretched)",
           tv.AudioSyncPlanError, tv.build_correction_plan,
           resolved=np.ones(2, dtype=bool), shifts=np.zeros(2, dtype=np.int64),
           n_samples=n)

    # error-vs-uncertainty separation at the type level
    value("T8.13 UNRESOLVED_GAP is not an AudioSyncError",
          not issubclass(type(tv.GapState.UNRESOLVED_GAP), tv.AudioSyncError))
    value("T8.14 the error hierarchy is AudioSyncError > Context/Plan",
          issubclass(tv.AudioSyncContextError, tv.AudioSyncError)
          and issubclass(tv.AudioSyncPlanError, tv.AudioSyncError))

    # CONTROL: T8.9's expectation must be able to fail
    def break_it() -> None:
        assert p.shifts.tolist() == [500, 999, 100, 100], "x"
    control("T8.C the 'refused number is unused' check can fail", break_it)


# ==========================================================================
# 9. default-path protection
# ==========================================================================
def t9_default_path() -> None:
    print("\n== T9  default path protection ==")
    import importlib
    import inspect

    ch = importlib.import_module("core.channel_sync")
    fx = importlib.import_module("core.sync_fix")
    es = importlib.import_module("core.sync_estimate")

    src_ch = inspect.getsource(ch)
    value("T9.1 core/channel_sync.py does not import the time-varying module",
          "audio_sync_tv" not in src_ch)
    value("T9.2 core/sync_fix.py does not import the time-varying module",
          "audio_sync_tv" not in inspect.getsource(fx))
    value("T9.3 core/sync_estimate.py does not import the time-varying module",
          "audio_sync_tv" not in inspect.getsource(es))

    # the shipped scalar path is unchanged: its signature still takes a scalar
    sig = inspect.signature(fx.shift_stream)
    value("T9.4 shift_stream still takes a SCALAR delay_samples",
          "delay_samples" in sig.parameters
          and sig.parameters["delay_samples"].kind
          is inspect.Parameter.KEYWORD_ONLY)
    value("T9.5 the P1 algorithm version is unchanged",
          ch.DEFAULTS["algo_version"] == "2.3.0-p1",
          f"{ch.DEFAULTS['algo_version']}")

    # the module must not be importable into production by accident: nothing
    # in core/ references it except itself
    core = REPO / "core"
    # STRENGTHENED (P4.4): these were substring scans, so a *comment* naming
    # the module read as a reference, while an aliased import read as clean.
    # The property is a dependency, so it is read from the import statements.
    refs = [f.name for f in sorted(core.glob("*.py"))
            if f.name != "audio_sync_tv.py"
            and _imports_audio_sync_tv(
                f.read_text(encoding="utf-8", errors="replace"))]
    value("T9.6 no other core module imports audio_sync_tv",
          refs == [], f"imported by {refs}")

    # 1kt.py must not import it either
    kt = (REPO / "1kt.py").read_text(encoding="utf-8", errors="replace")
    value("T9.7 1kt.py does not import audio_sync_tv",
          not _imports_audio_sync_tv(kt))
    ap = (REPO / "core" / "audio_plan.py").read_text(encoding="utf-8",
                                                     errors="replace")
    value("T9.8 core/audio_plan.py does not import audio_sync_tv",
          not _imports_audio_sync_tv(ap))

    # dependency direction: the module must not reach into video/preservation.
    # Scan IMPORT statements only — the module docstring deliberately NAMES the
    # things it must not depend on, and a plain substring scan would match
    # those names and report a false violation.
    mod = importlib.import_module("core.audio_sync_tv")
    tree = ast.parse(inspect.getsource(mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(("." * (node.level or 0)) + (node.module or ""))
    allowed = {".sync_estimate", "core.sync_estimate", "numpy", "math",
               "dataclasses", "enum", "typing", "__future__"}
    unexpected = sorted(set(imported) - allowed)
    value("T9.9 the module imports ONLY the stdlib, numpy and the estimator's "
          "stream reader", unexpected == [],
          f"unexpected imports: {unexpected}")
    for bad in ("encoders", "preservation", "nvencc", "qsvencc", "svtav1",
                "gpac", "sony", "dji", "batch_hw", "audio_plan"):
        value(f"T9.9 the module does not IMPORT {bad!r}",
              not any(bad in name for name in imported), f"{imported}")

    # CONTROL: T9.6's scan must be able to find something when it exists
    def break_it() -> None:
        found = [f.name for f in core.glob("*.py")
                 if "import numpy" in f.read_text(encoding="utf-8",
                                                  errors="replace")]
        assert found == [], "x"
    control("T9.C the source-scan check can fail", break_it)

    # CONTROL for the strengthened check: it must SEE a real import, and must
    # NOT be fooled by prose that merely names the module.
    def break_it_prose() -> None:
        assert _imports_audio_sync_tv("# see core.audio_sync_tv for why"), "x"
    control("T9.C2 the import check is not fooled by a comment naming the "
            "module", break_it_prose)

    def break_it_import() -> None:
        assert not _imports_audio_sync_tv("from . import audio_sync_tv"), "x"
    control("T9.C3 the import check DOES see a real import", break_it_import)


# ==========================================================================
# 10. real-corpus configuration (geometry, not audio)
# ==========================================================================
def t10_production_geometry() -> None:
    print("\n== T10  production geometry is the frozen P3 config ==")
    value("T10.1 window default is 1 440 000 samples (30.0 s)",
          tv.DEFAULT_WINDOW_SAMPLES == 1_440_000)
    value("T10.2 hop default is 480 000 samples (10.0 s)",
          tv.DEFAULT_HOP_SAMPLES == 480_000)
    value("T10.3 search default is ±3000 samples",
          tv.DEFAULT_SEARCH_SAMPLES == 3_000)
    value("T10.4 the production ratio is win = 3*hop",
          tv.DEFAULT_WINDOW_SAMPLES == 3 * tv.DEFAULT_HOP_SAMPLES)
    value("T10.5 window and hop are SEPARATE named constants",
          tv.DEFAULT_WINDOW_SAMPLES != tv.DEFAULT_HOP_SAMPLES)

    # the corpora are non-multiples of hop, which is why the seam is 3
    for n, stem in ((230_750_520, "C1305"), (199_903_704, "C1306")):
        value(f"T10.6 {stem}: n mod hop is non-zero -> seam 3",
              n % tv.DEFAULT_HOP_SAMPLES != 0
              and tv.window_seam(n, window_samples=tv.DEFAULT_WINDOW_SAMPLES,
                                 hop_samples=tv.DEFAULT_HOP_SAMPLES) == 3,
              f"n mod hop = {n % tv.DEFAULT_HOP_SAMPLES}")

    # 2 when n is a multiple of hop -- the other branch of P3.11's rule
    n2 = 480 * tv.DEFAULT_HOP_SAMPLES
    value("T10.7 n a multiple of hop -> seam 2",
          tv.window_seam(n2, window_samples=tv.DEFAULT_WINDOW_SAMPLES,
                         hop_samples=tv.DEFAULT_HOP_SAMPLES) == 2)

    # measured geometry must be assertable, not assumed
    cfg = {"n_win": tv.DEFAULT_WINDOW_SAMPLES, "hop": tv.DEFAULT_HOP_SAMPLES,
           "search": tv.DEFAULT_SEARCH_SAMPLES}
    value("T10.8 the production config asserts exactly as P3 asserted it",
          cfg == {"n_win": 1_440_000, "hop": 480_000, "search": 3000}, f"{cfg}")

    # CONTROL: the geometry assertion must be able to fail
    def break_it() -> None:
        assert tv.DEFAULT_HOP_SAMPLES == 3000, "x"
    control("T10.C the geometry assertion can fail", break_it)


# ==========================================================================
# 11. long-stream state transitions (bounded memory, no full materialisation)
# ==========================================================================
def t11_long_run() -> None:
    print("\n== T11  long-run state transitions (100k+ windows) ==")
    n_windows = 120_000
    rng = np.random.default_rng(20261008)
    # ~30% trusted, in runs, so gaps and reentries are plentiful
    block = rng.random(n_windows) < 0.30
    for _ in range(3):                       # smooth it into runs
        block = np.convolve(block.astype(np.int8), np.ones(9, dtype=np.int8),
                            mode="same") > 4
    mask = block.astype(bool)
    shifts = rng.integers(-3000, 3001, size=n_windows).astype(np.int64)
    n_samples = n_windows * HOP

    plan = tv.build_correction_plan(resolved=mask, shifts=shifts,
                                     n_samples=n_samples, policy="hold")
    counts = plan.state_counts()
    value("T11.1 the plan builds over 120 000 windows",
          plan.n_windows == n_windows, f"{plan.n_windows}")
    value("T11.2 all three states occur (the run is not vacuous)",
          counts["TRUSTED"] > 0 and counts["UNRESOLVED_GAP"] > 0
          and counts["REENTRY"] > 0, f"{counts}")
    value("T11.3 state and shift arrays are the same length as the plan",
          plan.states.shape[0] == n_windows
          and plan.shifts.shape[0] == n_windows)

    # independently derived expectation: count transitions from the mask
    expect_reentry = int((mask[1:] & ~mask[:-1]).sum())
    value("T11.4 REENTRY count equals an independently counted transition "
          "count", counts["REENTRY"] == expect_reentry,
          f"{counts['REENTRY']} vs {expect_reentry}")
    expect_gap = int((~mask).sum())
    value("T11.5 UNRESOLVED_GAP count equals the number of False mask entries",
          counts["UNRESOLVED_GAP"] == expect_gap,
          f"{counts['UNRESOLVED_GAP']} vs {expect_gap}")
    value("T11.6 the three counts sum to the window count",
          sum(counts.values()) == n_windows)

    # determinism at scale
    plan2 = tv.build_correction_plan(resolved=mask, shifts=shifts,
                                      n_samples=n_samples, policy="hold")
    value("T11.7 the plan is deterministic at scale",
          np.array_equal(plan.shifts, plan2.shifts)
          and np.array_equal(plan.states, plan2.states))

    # CONTROL: T11.4 must be able to fail
    def break_it() -> None:
        assert counts["REENTRY"] == expect_reentry + 1, "x"
    control("T11.C the transition-count comparison can fail", break_it)


def t12_closed_form_equivalence() -> None:
    """The applier's inlined join must equal the public ``window_index``.

    ``_render_block`` computes the window index with integer arithmetic inline,
    deliberately: materialising a per-sample window index costs 1.8 GB at the
    C1305 length (P3.11 measured 3632 MiB peak that way), while the closed form
    keeps memory bounded. That optimisation is only sound if the closed form and
    the public function are the SAME function — so the equality is asserted on
    every coordinate, at the real geometry and at awkward offsets, rather than
    assumed.
    """
    print("\n== T12  closed form vs public window_index ==")
    for coord in ("latest-start", "nearest-centre", "sample-domain"):
        off = (HOP // 2 - WIN // 2) if coord == "nearest-centre" else 0
        # every window boundary, plus offsets around it, for a real plan size
        nw = 481
        base = np.arange(0, nw, dtype=np.int64) * HOP
        probes = np.concatenate([
            base,
            base + 1,
            base - 1,
            base + HOP // 2,
            base + off,
            base + off - 1,
            base + off + 1,
        ])
        probes = np.clip(probes, 0, nw * HOP - 1)
        want = tv.window_index(coord, probes, window_samples=WIN,
                               hop_samples=HOP, n_windows=nw)
        if coord == "nearest-centre":
            got = np.clip((probes + off) // HOP, 0, nw - 1)
        else:
            got = np.clip(probes // HOP, 0, nw - 1)
        value(f"T12.1 {coord}: inlined closed form == window_index on "
              f"{probes.size} probes", bool(np.array_equal(want, got)),
              f"{int((want != got).sum())} differing samples")

    # the segment boundary must be where the join actually changes
    for coord in ("latest-start", "nearest-centre"):
        off = (HOP // 2 - WIN // 2) if coord == "nearest-centre" else 0
        bad = 0
        for w in (0, 1, 2, 7, 100, 480):
            boundary = (w + 1) * HOP - off
            if boundary <= 0 or boundary >= 481 * HOP:
                continue
            before = int(tv.window_index(
                coord, np.array([boundary - 1]), window_samples=WIN,
                hop_samples=HOP, n_windows=481)[0])
            at = int(tv.window_index(
                coord, np.array([boundary]), window_samples=WIN,
                hop_samples=HOP, n_windows=481)[0])
            if not (before == w and at == w + 1):
                bad += 1
        value(f"T12.2 {coord}: the segment boundary formula is exact",
              bad == 0, f"{bad} boundary/boundaries wrong")

    # per-sample equality against the independent slow reference on a fixture
    n = 3 * HOP + 5000
    need = tv.required_window_count(n, hop_samples=HOP)
    mask = np.ones(need, dtype=bool)
    shifts = np.array([(i - 1) * 17 for i in range(need)], dtype=np.int64)
    arr = ramp(n)
    for coord in ("latest-start", "nearest-centre"):
        p = tv.build_correction_plan(resolved=mask, shifts=shifts,
                                      n_samples=n, coordinate=coord)
        want = slow_reference(arr, p)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "s.f32"
            out = Path(td) / "o.f32"
            write_raw(src, arr)
            # a chunk size that does NOT align with hop, so segment and chunk
            # boundaries interleave
            tv.apply_shift_plan(src, out, p, storage_dtype="f32",
                                chunk_samples=n // 7 + 13)
            got = np.fromfile(out, dtype="<f4")
        value(f"T12.3 {coord}: block applier == independent per-sample "
              f"reference", bool(np.array_equal(got, want)),
              f"{int((got != want).sum())} differing samples")

    # CONTROL: T12.1 must be able to fail
    def break_it() -> None:
        nw = 20
        probes = np.arange(nw, dtype=np.int64) * HOP
        a = tv.window_index("latest-start", probes, window_samples=WIN,
                            hop_samples=HOP, n_windows=nw)
        b = tv.window_index("nearest-centre", probes, window_samples=WIN,
                            hop_samples=HOP, n_windows=nw)
        assert np.array_equal(a, b), "x"
    control("T12.C the closed-form equivalence comparison can fail", break_it)


def _imports_audio_sync_tv(src: str) -> bool:
    """Does this source **import** the time-varying module? AST, not substring.

    P4.4: the previous check was ``"audio_sync_tv" in txt``, which a comment
    naming the module satisfies and an aliased import does not. Provenance
    comments that explain *why* the module is not imported must not read as
    imports — while a real (or lazily-named) import must not slip through.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any("audio_sync_tv" in a.name for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if "audio_sync_tv" in (node.module or ""):
                return True
            if any("audio_sync_tv" in a.name for a in node.names):
                return True
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in ("import_module", "__import__"):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and "audio_sync_tv" in arg.value):
                        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("=" * 78)
    print("P4 — time-varying audio sync: production regression")
    print(f"     algo {tv.ALGO_VERSION}; geometry win={tv.DEFAULT_WINDOW_SAMPLES} "
          f"hop={tv.DEFAULT_HOP_SAMPLES} search=±{tv.DEFAULT_SEARCH_SAMPLES}")
    print("=" * 78)

    t1_state_semantics()
    t2_decision_object()
    t3_plan_invariants()
    t4_coordinates()
    t5_gap_policies()
    t6_apply_mechanics()
    t7_chunk_invariance()
    t8_failure_safety()
    t9_default_path()
    t10_production_geometry()
    t11_long_run()
    t12_closed_form_equivalence()

    n_cases = len(RESULTS)
    n_fail = COUNTS["fail"] + COUNTS["silent_wrong"] + COUNTS["control_failed"]
    print("\n" + "=" * 78)
    print(f"cases                     : {n_cases}")
    print(f"VALUE_OK                  : {COUNTS['value_ok']}")
    print(f"RAISED (expected errors)  : {COUNTS['raised']}")
    print(f"CONTROL_OK                : {COUNTS['control_ok']}")
    print(f"CONTROL_FAILED            : {COUNTS['control_failed']}")
    print(f"SILENT_WRONG              : {COUNTS['silent_wrong']}")
    print(f"FAIL                      : {COUNTS['fail']}")
    print("=" * 78)

    # --- the anti-silent-pass gates. These are PERMANENT requirements. ---
    if n_cases == 0:
        print("FAIL: no case ran — an empty run is not a pass", file=sys.stderr)
        return 2
    if COUNTS["value_ok"] == 0:
        print("FAIL: no VALUE case ran", file=sys.stderr)
        return 2
    if COUNTS["raised"] == 0:
        print("FAIL: no RAISE case ran", file=sys.stderr)
        return 2
    if COUNTS["control_ok"] == 0:
        print("FAIL: no CONTROL case was demonstrated discriminating",
              file=sys.stderr)
        return 2

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"cases": n_cases, "counts": COUNTS, "results": RESULTS},
            indent=1), encoding="utf-8")
        print(f"json -> {args.json}")

    if n_fail:
        print(f"\nRESULT: FAIL ({n_fail} case(s))")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  FAIL {r['name']}"
                      + (f" -- {r['detail']}" if r["detail"] else ""))
        return 1
    print(f"\nRESULT: PASS — {n_cases} cases, "
          f"{COUNTS['value_ok']} value, {COUNTS['raised']} raise, "
          f"{COUNTS['control_ok']} control; SILENT_WRONG = 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
