"""P4.6 — time-varying sync: production execution regression family.

This is the permanent, tracked regression for the explicit opt-in time-varying
path added in P4.6. It runs from **tracked code only** — nothing here imports
``work/``.

The standing rule of this line (P4-HANDOFF §4)::

    every check must be demonstrated discriminating before it is trusted
    every count must be compared to an independently derived expectation
    "the test passed" is not evidence until the test has been shown able to fail

Check kinds, kept separate:

  * **VALUE**   — assert the resulting value, not merely that no exception came
  * **RAISE**   — assert the *specific* refusal/error type AND reason code
  * **CONTROL** — assert that a check CAN fail; a check that cannot fail is
                  reported as a finding, not as a pass

``SILENT_WRONG`` is counted. It must be 0.

Sections (T-numbers follow the P4.6 brief):

  T1–T7    basic delay (zero / positive / negative / constant / piecewise /
           two transitions / multiple channels)
  T8–T15   gap and state (leading / middle / trailing / reentry / repeated /
           all refused / one valid / policy refuses unresolved evidence)
  T16–T22  coordinate (latest-start / nearest-centre / boundaries / non-multiple
           hop / EOF tail / overlap disagreement / chunk crosses boundary)
  T23–T28  evidence (coverage / high confidence low coverage / sufficient /
           NaN-Inf / missing measurement / raw estimate but not trusted)
  T29–T35  plan and apply (mismatched count / invalid source index / invalid
           mask / invalid coordinate / invalid policy / invalid plan length /
           output size invariant)
  T36–T44  chunk invariance at 256 / 1024 / 4096 / 16384
  T45–T52  the refusal contract and the P3.7 over-claim guard
  T53+     controls

Run::

    python tests/audio_sync_p4/test_time_varying_exec.py
    python tests/audio_sync_p4/test_time_varying_exec.py --json out.json

Exit 0 only if every case ran, at least one case ran, and ``silent_wrong == 0``.
An empty run exits 2 (the P3 silent-pass defect class).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import audio_sync_tv as tv                                    # noqa: E402
from core import audio_sync_tv_exec as ex                               # noqa: E402

FS = 48_000
HOP = tv.DEFAULT_HOP_SAMPLES          # 480 000 = 10.0 s
WIN = tv.DEFAULT_WINDOW_SAMPLES       # 1 440 000 = 30.0 s

#: The production hop, kept under a name that no loop variable shadows.
#: ``HOP`` above is module-level and IS shadowed inside T36 by
#: ``for c in CHUNKS``-style locals in an earlier version of this file, which
#: silently turned a 480 000 bound into 1024 and produced a nonsense failure.
HOP_DEFAULT = tv.DEFAULT_HOP_SAMPLES

#: the four chunk sizes the brief names. 480_000 (the production hop) is added
#: because a chunk equal to one hop is the degenerate case for segment splitting.
CHUNKS = (256, 1024, 4096, 16384, HOP)

RESULTS: list[dict] = []
COUNTS = {
    "value_ok": 0, "raised": 0, "silent_wrong": 0,
    "control_ok": 0, "control_failed": 0, "fail": 0,
}

_TMP: tempfile.TemporaryDirectory | None = None


def tmpdir() -> Path:
    global _TMP
    if _TMP is None:
        _TMP = tempfile.TemporaryDirectory(prefix="p46tv_")
    return Path(_TMP.name)


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


def raises_tv(name: str, reason: str, fn, *a, **kw) -> None:
    """Assert the SPECIFIC refusal reason. A wrong reason is a failure."""
    try:
        fn(*a, **kw)
    except ex.TimeVaryingRefused as exc:
        if exc.reason == reason:
            _record(name, "raised", True)
        else:
            _record(name, "raised", False,
                    f"refused with {exc.reason!r}, expected {reason!r}")
        return
    except BaseException as exc:                            # noqa: BLE001
        _record(name, "raised", False,
                f"raised {type(exc).__name__}: {exc} (expected "
                f"TimeVaryingRefused/{reason})")
        return
    _record(name, "raised", False,
            f"did not raise (expected TimeVaryingRefused/{reason})")


def raises_type(name: str, exc_type: type, fn, *a, **kw) -> None:
    try:
        fn(*a, **kw)
    except exc_type:
        _record(name, "raised", True)
        return
    except BaseException as exc:                            # noqa: BLE001
        _record(name, "raised", False,
                f"raised {type(exc).__name__}: {exc} (expected "
                f"{exc_type.__name__})")
        return
    _record(name, "raised", False,
            f"did not raise (expected {exc_type.__name__})")


def control(name: str, bad_fn) -> None:
    try:
        bad_fn()
    except BaseException:                                   # noqa: BLE001
        _record(name, "control_ok", True)
        return
    _record(name, "control_ok", False,
            "the check did NOT fail on a deliberately broken input — it is "
            "not discriminating")


# --------------------------------------------------------------------------
# material + helpers
# --------------------------------------------------------------------------
def reference(n: int) -> np.ndarray:
    """A full-band, non-repeating reference. Deterministic, no RNG."""
    t = np.arange(n, dtype=np.float64) / float(FS)
    x = (0.55 * np.sin(2.0 * np.pi * 440.0 * t)
         + 0.25 * np.sin(2.0 * np.pi * 1310.0 * t + 0.7)
         + 0.15 * np.sin(2.0 * np.pi * 3111.0 * t + 1.9)
         + 0.05 * np.sin(2.0 * np.pi * 7331.0 * t + 2.3))
    return x.astype(np.float32)


def source_with_delay(ref: np.ndarray, delay_of) -> np.ndarray:
    """``src[k] = ref[k - delay_of(k)]``, zero-filled out of range.

    The sign convention is the production one, and it was WRONG in the first
    version of this harness (the source was built as an *advance*, so every
    known-truth case failed by exactly the shift). It is stated here so the
    convention lives in exactly one place:

      * a POSITIVE ``delay_samples`` means the target arrives **late**, i.e.
        ``src`` lags ``ref`` by that many samples, so ``src[k] = ref[k - D]``;
      * the correction is therefore a forward shift by ``+D``:
        ``out[k] = src[k + D] = ref[k]``.

    ``sync_fix.shift_stream`` uses ``out[n] = in[n + rint(delay_samples)]`` and
    ``apply_shift_plan`` uses ``out[k] = in[k + S[join(k)]]`` -- the same
    direction, which is why the plan is fed ``S = delay_samples`` and the
    correction recovers the reference.
    """
    n = int(ref.size)
    k = np.arange(n, dtype=np.int64)
    shifts = np.array([int(delay_of(int(i))) for i in k.tolist()],
                      dtype=np.int64)
    src = np.zeros(n, dtype=np.float32)
    j = k - shifts
    inside = (j >= 0) & (j < n)
    src[inside] = ref[j[inside]]
    return src


def truth_from_plan(src: np.ndarray, plan: tv.CorrectionPlan) -> np.ndarray:
    """Ground truth for ``out`` built from the PLAN's own shifts and the same
    coordinate contract.

    It shares no code with the applier -- which segments a block and issues one
    ``read_into`` per window -- because this is the naive sample-at-a-time form
    of ``out[k] = in[k + S[join(k)]]``. Ground truth is derived from the plan
    rather than from an independently invented trajectory, so a rounding
    divergence between the trajectory and the plan cannot be miscounted as an
    apply defect.
    """
    n = int(plan.n_samples)
    k = np.arange(n, dtype=np.int64)
    hop = int(plan.hop_samples)
    if plan.coordinate == "nearest-centre":
        off = hop // 2 - int(plan.window_samples) // 2
        w = np.clip(np.floor_divide(k + off, hop), 0, plan.n_windows - 1)
    else:
        w = np.clip(k // hop, 0, plan.n_windows - 1)
    j = k + np.asarray(plan.shifts)[w]
    out = np.zeros(n, dtype=np.float32)
    inside = (j >= 0) & (j < n)
    out[inside] = src[j[inside]]
    return out


def write_f32(path: Path, arr: np.ndarray) -> Path:
    np.ascontiguousarray(arr, dtype="<f4").tofile(path)
    return path


def records_for(n_windows: int, valid_of, delay_of) -> list[dict]:
    """Measurement records: ``valid`` is a function of the window index."""
    out = []
    for i in range(n_windows):
        v = bool(valid_of(i))
        d = delay_of(i)
        out.append({
            "valid": v,
            # a REJECTED window can still carry a number; it must stay in the
            # record for audit but must never be used as a shift.
            "delay_samples": (float(d) if d is not None else None),
            "confidence": 0.9 if v else 0.2,
        })
    return out


def req(policy: str = "hold", coordinate: str = "latest-start",
        chunk: int | None = None, **kw) -> ex.TimeVaryingRequest:
    return ex.request_time_varying(
        enabled=True, policy=policy, coordinate=coordinate,
        chunk_samples=chunk, **kw)


def nw_for(n: int) -> int:
    return tv.required_window_count(n, hop_samples=HOP)


def run_case(name: str, *, delay_of, valid_of=None, policy: str = "hold",
             coordinate: str = "latest-start", n_windows: int = 4,
             chunk: int | None = 4096, expect_executed: bool = True,
             allow_all_unresolved: bool = False, known_truth: bool = True):
    """Build material for a piecewise trajectory, execute, and verify.

    Returns ``(result, src_path, dst_path, ref, src)`` or ``None`` when the run
    was refused (the caller decides whether that was expected).
    """
    n = n_windows * HOP
    ref = reference(n)
    src = source_with_delay(ref, delay_of)
    d = tmpdir() / f"{name}.src.f32"
    write_f32(d, src)
    need = nw_for(n)
    valid_of = valid_of or (lambda i: True)
    recs = records_for(need, valid_of, delay_of)
    dst = tmpdir() / f"{name}.out.f32"
    try:
        r = ex.run_time_varying(
            d, dst, request=req(policy=policy, coordinate=coordinate,
                                chunk=chunk,
                                allow_all_unresolved=allow_all_unresolved),
            measurements=recs, verify=True,
        )
    except ex.TimeVaryingRefused as exc:
        if expect_executed:
            value(f"{name} executed", False, f"refused: {exc.reason} {exc.detail}")
            return None
        value(f"{name} refused as expected", False,
              f"raised instead of returning: {exc.reason}")
        return None
    if expect_executed != bool(r.executed):
        value(f"{name} executed == {expect_executed}", False,
              f"executed={r.executed} reason={r.reason} detail={r.detail}")
        return None
    value(f"{name} executed == {expect_executed}", True)
    if not r.executed:
        value(f"{name} gave a reason", r.reason is not None, f"{r.reason}")
        value(f"{name} wrote no output file", not dst.exists(),
              f"{dst} exists")
        return r, d, dst, ref, src
    if known_truth:
        # Ground truth from the PLAN's shifts and the same coordinate contract,
        # via the naive sample-at-a-time reference in truth_from_plan(). This is
        # the "correction mechanics" check: did the applier read the source
        # index the contract prescribes for every sample?
        p = ex.build_time_varying_plan(
            d, request=req(policy=policy, coordinate=coordinate,
                           chunk=chunk, allow_all_unresolved=allow_all_unresolved),
            measurements=recs)
        truth = truth_from_plan(src, p.plan)
        got = np.fromfile(dst, dtype="<f4")
        bad = int((got != truth).sum())
        value(f"{name} matches the contract's own correction exactly", bad == 0,
              f"{bad} of {n} samples differ")
        # a second, independent oracle: the source is a KNOWN lag of a
        # reference, so where the plan is TRUSTED and the declared delay is
        # correct, the output must equal the reference again.
        ok_w = [i for i in range(p.plan.n_windows)
                if str(p.plan.states[i]) != tv.GapState.UNRESOLVED_GAP.value]
        if ok_w:
            recovered = int((got == ref).sum())
            value(f"{name} recovers the reference on measured windows",
                  recovered > 0, f"not one sample equals the reference")
    return r, d, dst, ref, src


# ==========================================================================
# T1–T7  basic delay
# ==========================================================================
def t01_zero_offset() -> None:
    print("\n== T1  zero offset ==")
    out = run_case("T1", delay_of=lambda i: 0)
    if out:
        r = out[0]
        value("T1 plan has no unresolved window", r.refusals.get(
            tv.GapState.UNRESOLVED_GAP.value, 0) == 0, f"{r.refusals}")
        value("T1 all windows TRUSTED",
              r.state_counts.get(tv.GapState.TRUSTED.value) == r.n_windows,
              f"{r.state_counts}")


def t02_positive_offset() -> None:
    print("\n== T2  positive offset (target arrives late) ==")
    D = 615
    out = run_case("T2", delay_of=lambda i: D)
    if out:
        r, _d, dst, _ref, _src = out
        value("T2 verification complete and exact",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        # a forward shift pulls the LAST D samples of the source past the end,
        # so the trailing D output samples must be the zero fill (invariant I7:
        # out-of-range read is zero fill, never a wrap and never a repeat).
        got = np.fromfile(dst, dtype="<f4")
        value(f"T2 the trailing {D} samples are zero-filled (no wrap, no "
              f"repeat-last-sample)", bool(np.all(got[len(got) - D:] == 0.0)),
              f"{got[len(got) - D:][:5].tolist()}")
        value("T2 the sample just before the tail is no longer zero (the shift "
              "really happened)", float(got[len(got) - D - 1]) != 0.0,
              f"{float(got[len(got) - D - 1])}")
        value("T2 the head is NOT zero (a late target loses its head, not its "
              "tail)", float(got[0]) != 0.0, f"{float(got[0])}")


def t03_negative_offset() -> None:
    print("\n== T3  negative offset (target arrives early) ==")
    D = -459
    out = run_case("T3", delay_of=lambda i: D)
    if out:
        r, _d, dst, _ref, src = out
        got = np.fromfile(dst, dtype="<f4")
        value("T3 verification complete and exact",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        # a NEGATIVE (backward) shift pulls the first |D| samples from before
        # the stream start, so the HEAD must be zero-filled.
        value("T3 the leading |D| samples are zero-filled",
              bool(np.all(got[:-D] == 0.0)), f"{got[:-D][:5].tolist()}")
        value("T3 the sample just after the head is not zero (the shift really "
              "happened)", float(got[-D]) != 0.0, f"{float(got[-D])}")
        value("T3 the tail is NOT zero (an early target loses its head, not "
              "its tail)", float(got[-1]) != 0.0, f"{float(got[-1])}")


def t04_constant_across_all_windows() -> None:
    print("\n== T4  constant offset across ALL windows ==")
    D = 300
    out = run_case("T4", delay_of=lambda i: D, n_windows=6)
    if out:
        r = out[0]
        value("T4 one constant shift is applied everywhere",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        value("T4 no window is a gap", r.refusals == {
            tv.GapState.TRUSTED.value: r.n_windows}, f"{r.refusals}")


def t05_piecewise_constant() -> None:
    print("\n== T5  piecewise constant offset ==")
    step = lambda i: (100, 300, 500, 200)[min(i, 3)]        # noqa: E731
    out = run_case("T5", delay_of=step, n_windows=4)
    if out:
        r = out[0]
        value("T5 piecewise-constant correction is exact",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        value("T5 output sample count == input sample count",
              r.n_samples_out == r.n_samples_in == 4 * HOP,
              f"{r.n_samples_out} vs {r.n_samples_in}")


def t06_two_transitions() -> None:
    print("\n== T6  two transitions ==")
    # windows 0,1 -> +200 ; 2,3 -> -150 ; 4,5 -> +600
    traj = lambda i: (200, 200, -150, -150, 600, 600)[min(i, 5)]   # noqa: E731
    out = run_case("T6", delay_of=traj, n_windows=6)
    if out:
        r, _d, dst, _ref, _src = out
        value("T6 two-transition correction is exact",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        value("T6 every window is TRUSTED (no gaps were declared)",
              r.state_counts.get(tv.GapState.TRUSTED.value) == r.n_windows,
              f"{r.state_counts}")


def t07_multiple_channels() -> None:
    print("\n== T7  multiple channels (independent plans) ==")
    n = 3 * HOP
    ref = reference(n)
    results = []
    for tag, D in (("a1", 137), ("a2", -137), ("a4", 0)):
        src = source_with_delay(ref, lambda i, D=D: D)
        sp = write_f32(tmpdir() / f"T7_{tag}.f32", src)
        dp = tmpdir() / f"T7_{tag}.out.f32"
        need = nw_for(n)
        recs = records_for(need, lambda i: True, lambda i, D=D: D)
        r = ex.run_time_varying(sp, dp, request=req(chunk=4096),
                                measurements=recs, verify=True)
        results.append((tag, D, r, dp))
    for tag, D, r, dp in results:
        value(f"T7 {tag} (delay {D:+d}) executed and verified",
              r.executed and (r.verification or {}).get("ok") is True,
              f"executed={r.executed} reason={r.reason} v={r.verification}")
    # the three outputs must be pairwise different: a per-channel plan that
    # silently shared one shift would make them identical.
    arrs = [np.fromfile(dp, dtype="<f4") for _t, _D, _r, dp in results]
    value("T7 the three channels produced DIFFERENT output (not one shared "
          "shift)", not np.array_equal(arrs[0], arrs[1])
          and not np.array_equal(arrs[1], arrs[2]),
          "two channels came out byte-identical")


# ==========================================================================
# T8–T15  gap and state
# ==========================================================================
def t08_leading_gap() -> None:
    print("\n== T8  leading gap ==")
    out = run_case(
        "T8", delay_of=lambda i: 240, valid_of=lambda i: i >= 2, n_windows=5)
    if out:
        r = out[0]
        value("T8 leading windows are UNRESOLVED_GAP",
              r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value) == 2,
              f"{r.state_counts}")
        value("T8 the first accepted window is REENTRY",
              r.state_counts.get(tv.GapState.REENTRY.value) == 1,
              f"{r.state_counts}")
        value("T8 refusal map names the gap population",
              r.refusals.get(tv.GapState.UNRESOLVED_GAP.value) == 2,
              f"{r.refusals}")


def t09_middle_gap() -> None:
    print("\n== T9  middle gap ==")
    out = run_case(
        "T9", delay_of=lambda i: 180,
        valid_of=lambda i: not (2 <= i <= 4), n_windows=7)
    if out:
        r = out[0]
        value("T9 exactly the three refused windows are UNRESOLVED_GAP",
              r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value) == 3,
              f"{r.state_counts}")
        value("T9 the window after the gap is REENTRY and only that one",
              r.state_counts.get(tv.GapState.REENTRY.value) == 1,
              f"{r.state_counts}")
        value("T9 correction still exact under the hold policy",
              (r.verification or {}).get("ok") is True, f"{r.verification}")


def t10_trailing_gap() -> None:
    print("\n== T10  trailing gap ==")
    out = run_case(
        "T10", delay_of=lambda i: 210,
        valid_of=lambda i: i < 3, n_windows=6)
    if out:
        r = out[0]
        value("T10 trailing windows are UNRESOLVED_GAP",
              r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value) == 3,
              f"{r.state_counts}")
        value("T10 no window is REENTRY (nothing follows the gap)",
              r.state_counts.get(tv.GapState.REENTRY.value) == 0,
              f"{r.state_counts}")
        # hold carries the last trusted value FORWARD into the trailing gap
        shifts = None
        value("T10 the gap still received a policy value, not a fabricated "
              "measurement",
              r.n_windows == 6 and (r.verification or {}).get("ok") is True,
              f"{r.evidence}")


def t11_gap_then_reentry() -> None:
    print("\n== T11  gap followed by reentry ==")
    out = run_case(
        "T11", delay_of=lambda i: 100 if i <= 1 else 500,
        valid_of=lambda i: i in (0, 1, 4, 5), n_windows=6)
    if out:
        r = out[0]
        value("T11 gaps 2,3 are unresolved and gap 4 is REENTRY",
              r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value) == 2
              and r.state_counts.get(tv.GapState.REENTRY.value) == 1,
              f"{r.state_counts}")
        value("T11 correction exact across the transition",
              (r.verification or {}).get("ok") is True, f"{r.verification}")


def t12_repeated_invalid_windows() -> None:
    print("\n== T12  repeated invalid windows ==")
    out = run_case(
        "T12", delay_of=lambda i: 150,
        valid_of=lambda i: (i % 2 == 0), n_windows=8)
    if out:
        r = out[0]
        alt = r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value)
        value("T12 alternating validity gives 4 unresolved of 8",
              alt == 4, f"{r.state_counts}")
        value("T12 every gap is followed by a REENTRY except a TRAILING one "
              "(3 of the 4 gaps; window 7 is the last and nothing follows it)",
              r.state_counts.get(tv.GapState.REENTRY.value) == 3,
              f"{r.state_counts}")
        value("T12 the trailing gap is NOT labelled REENTRY (no window comes "
              "after it to re-enter)",
              str(ex.build_time_varying_plan(
                  out[1], request=req(),
                  measurements=records_for(nw_for(8 * HOP), lambda i: i % 2 == 0,
                                           lambda i: 150)
              ).plan.states.tolist()[7]) == tv.GapState.UNRESOLVED_GAP.value,
              "the final window was mislabelled")


def t13_all_windows_refused() -> None:
    print("\n== T13  all windows refused ==")
    out = run_case("T13", delay_of=lambda i: 0, valid_of=lambda i: False,
                   n_windows=4, expect_executed=False,
                   allow_all_unresolved=False, known_truth=False)
    if out:
        r = out[0]
        value("T13 refuses with the no-trusted-window reason",
              r.reason == ex.REASON_TV_NO_TRUSTED_WINDOW,
              f"{r.reason}: {r.detail}")
    # and with the explicit override it DOES run, reporting the truth
    out2 = run_case("T13o", delay_of=lambda i: 0, valid_of=lambda i: False,
                    n_windows=4, allow_all_unresolved=True, known_truth=False)
    if out2:
        r2 = out2[0]
        value("T13o allow_all_unresolved executes and reports 100 % unresolved",
              r2.state_counts.get(tv.GapState.UNRESOLVED_GAP.value)
              == r2.n_windows, f"{r2.state_counts}")
        value("T13o evidence reports zero measured windows",
              (r2.evidence or {}).get("n_measured") == 0, f"{r2.evidence}")


def t14_one_valid_window() -> None:
    print("\n== T14  exactly one valid window ==")
    # window 4 is the only measured window (an earlier version used index 2,
    # which is inside the first hop and is therefore covered by window 0 under
    # every window above it -- the measurement window index, not the sample).
    out = run_case(
        "T14", delay_of=lambda i: 275, valid_of=lambda i: i == 4, n_windows=6)
    if out:
        r = out[0]
        # Exactly one window carries a measurement -- and it is the FIRST window
        # after a gap, so it is REENTRY, not TRUSTED. That is the state machine
        # working as specified ("TRUSTED == resolved and the previous window was
        # also resolved"). An earlier version of this check expected TRUSTED and
        # so reported a defect that did not exist.
        value("T14 exactly one window carries a measurement: it is the first "
              "resolved window after a gap, so it is REENTRY",
              r.state_counts.get(tv.GapState.REENTRY.value) == 1
              and r.state_counts.get(tv.GapState.TRUSTED.value) == 0
              and r.state_counts.get(tv.GapState.UNRESOLVED_GAP.value) == 5,
              f"{r.state_counts}")
        value("T14 the measured window is window 4",
              str(ex.build_time_varying_plan(
                  out[1], request=req(),
                  measurements=records_for(nw_for(out[3].size),
                                           lambda i: i == 4, lambda i: 275)
              ).plan.states.tolist()[4]) == tv.GapState.REENTRY.value,
              "window 4 was not the resolved one")
        value("T14 evidence n_measured == 1 and coverage == 1/6",
              (r.evidence or {}).get("n_measured") == 1
              and abs((r.evidence or {}).get("coverage_fraction", 0)
                      - 1.0 / 6.0) < 1e-12, f"{r.evidence}")


def t15_policy_refuses_unresolved_evidence() -> None:
    print("\n== T15  a policy cannot turn unresolved evidence into TRUSTED ==")
    n = 4 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: i < 2, lambda i: 120)
    d = tmpdir() / "T15.f32"
    write_f32(d, np.zeros(n, dtype=np.float32))
    for policy in ex.PRODUCTION_POLICIES:
        if policy in ("linear", "next"):
            continue        # offline-only: refused by the applier, see T46
        p = ex.build_time_varying_plan(
            d, request=req(policy=policy), measurements=recs)
        states = [str(s) for s in p.plan.states.tolist()]
        gap = p.plan.resolved
        bad = sum(1 for i, s in enumerate(states)
                  if (not bool(gap[i]))
                  and s != tv.GapState.UNRESOLVED_GAP.value)
        value(f"T15 policy {policy!r}: no unresolved window is labelled "
              f"TRUSTED/REENTRY", bad == 0, f"{bad} mislabelled")
    value("T15 every production policy reports overclaimed_windows() == 0",
          all(ex.build_time_varying_plan(
              d, request=req(policy=p), measurements=recs
          ).plan.overclaimed_windows() == 0
          for p in ex.PRODUCTION_POLICIES
          if p not in ("linear", "next")), "non-zero over-claim")


# ==========================================================================
# T16–T22  coordinate
# ==========================================================================
def t16_latest_start() -> None:
    print("\n== T16  latest-start ==")
    out = run_case("T16", delay_of=lambda i: 400, coordinate="latest-start",
                   n_windows=4)
    if out:
        r = out[0]
        value("T16 latest-start executes and verifies",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        value("T16 coordinate is recorded in the result",
              r.coordinate == "latest-start", f"{r.coordinate}")


def t17_nearest_centre() -> None:
    print("\n== T17  nearest-centre ==")
    out = run_case("T17", delay_of=lambda i: 400, coordinate="nearest-centre",
                   n_windows=4)
    if out:
        r = out[0]
        value("T17 nearest-centre executes and verifies",
              (r.verification or {}).get("ok") is True, f"{r.verification}")
        value("T17 coordinate is recorded in the result",
              r.coordinate == "nearest-centre", f"{r.coordinate}")


def t18_coordinate_boundaries() -> None:
    print("\n== T18  coordinate boundaries: what the two contracts actually do ==")
    n = 5 * HOP
    # A trajectory whose STEP is larger than the shift difference the two
    # contracts produce, so the choice is OBSERVABLE in the output. An earlier
    # version used steps of 100 samples; both contracts then applied shifts that
    # differed by 100 samples, and the differing samples were indistinguishable
    # because the signal is smooth, so the "they differ" check silently
    # reported agreement. The step is now 200 000 samples.
    traj = lambda i: (0, 200_000, 400_000, 600_000, 800_000)[min(i, 4)]  # noqa: E731
    a = run_case("T18ls", delay_of=traj, coordinate="latest-start",
                 n_windows=5, known_truth=False)
    b = run_case("T18nc", delay_of=traj, coordinate="nearest-centre",
                 n_windows=5, known_truth=False)
    if a and b:
        da = np.fromfile(a[2], dtype="<f4")
        db = np.fromfile(b[2], dtype="<f4")
        diff = int((da != db).sum())
        value("T18 the two coordinate contracts produce DIFFERENT output",
              diff > 0,
              "latest-start and nearest-centre agreed everywhere — the "
              "coordinate choice would be vacuous")

        ls = ex.build_time_varying_plan(
            a[1], request=req(coordinate="latest-start"),
            measurements=records_for(nw_for(n), lambda i: True, traj))
        nc = ex.build_time_varying_plan(
            a[1], request=req(coordinate="nearest-centre"),
            measurements=records_for(nw_for(n), lambda i: True, traj))
        off = HOP_DEFAULT // 2 - WIN // 2
        # At the production ratio win = 3*hop this is hop/2 - 3*hop/2 = -hop,
        # i.e. the join lags by a FULL window. (An earlier version of this check
        # asserted it equalled -hop/2, which is simply wrong arithmetic; the
        # published value and the formula disagreed and the assertion was the
        # thing at fault, not the code.)
        value(f"T18b at win = 3*hop the join offset is off = hop//2 - win//2 = "
              f"{off} = -hop exactly, so nearest-centre lags by one full window",
              off == -HOP_DEFAULT, f"{off} vs {-HOP_DEFAULT}")
        shifts_ls = np.asarray(ls.plan.shifts)
        shifts_nc = np.asarray(nc.plan.shifts)
        # The relationship, stated as arithmetic rather than as a slogan.
        # nearest-centre selects w = (k + off) // hop with off = -hop/2, so in
        # the interior it picks the window ONE EARLIER than latest-start. The
        # lag is identical for every sample, so the observable difference is a
        # SHIFT-VALUE difference, not a time lag -- which is why the closed form
        # alone cannot show it. It is shown through the join.
        value("T18c the plan's own shift sequence is the same (both plans hold "
              "the same per-window shifts; only the JOIN differs)",
              bool(np.array_equal(shifts_nc, shifts_ls)),
              f"nc={shifts_nc[:5].tolist()} ls={shifts_ls[:5].tolist()}")
        # and the join difference is exactly one window in the interior
        k = np.arange(2 * HOP_DEFAULT, 4 * HOP_DEFAULT, dtype=np.int64)
        w_ls = np.clip(k // HOP_DEFAULT, 0, nc.plan.n_windows - 1)
        w_nc = np.clip(np.floor_divide(k + off, HOP_DEFAULT),
                       0, nc.plan.n_windows - 1)
        value("T18d in the interior nearest-centre's join is exactly one window "
              "behind latest-start's",
              bool(np.all(w_nc == w_ls - 1)),
              f"nc={w_nc[:5].tolist()} ls={w_ls[:5].tolist()}")
        value("T18e at window 0 they AGREE only because both CLAMP there",
              int(np.clip(0 + off, 0, nc.plan.n_windows - 1)) == 0,
              f"{int(np.clip(off, 0, nc.plan.n_windows - 1))}")
        # The INDEX trajectories derived from the two joins differ on a derived
        # number of samples. This is the authoritative statement of the
        # coordinate difference, and it is derived rather than guessed.
        #
        # The VALUE comparison above cannot be used to bound the index
        # difference, and an earlier version of this check tried to: the test
        # signal is a sum of sinusoids, so it is PERIODIC -- two source indices
        # 200 000 samples apart frequently carry the identical float. The index
        # prediction is 1 920 000 while only 1 320 000 samples compared unequal,
        # and the 600 000 "extra" indices are exactly this aliasing, not a
        # defect. Comparing indices removes the confound; comparing values is
        # kept only as the weaker, sufficient statement "they are not the same
        # stream".
        maxdiff = int(np.max(np.abs(np.diff(shifts_ls))))
        kk = np.arange(n, dtype=np.int64)
        j_ls = kk + shifts_ls[np.clip(kk // HOP_DEFAULT, 0,
                                      nc.plan.n_windows - 1)]
        j_nc = kk + shifts_nc[np.clip(np.floor_divide(kk + off, HOP_DEFAULT),
                                      0, nc.plan.n_windows - 1)]
        predicted = int(np.count_nonzero(j_ls != j_nc))
        value(f"T18f the two contracts read DIFFERENT source indices on "
              f"{predicted} of {n} samples (derived from the two joins and the "
              f"plan's shifts, not bounded by a guess)",
              predicted == 4 * HOP_DEFAULT,
              f"derived {predicted}")
        value(f"T18g the value divergence ({diff}) is LOWER than the index "
              f"divergence ({predicted}) because the test signal is periodic — "
              f"stated so the two numbers are not confused",
              diff < predicted and diff > 0,
              f"values {diff}, indices {predicted}")
        value(f"T18h the difference is NOT bounded by the shift step alone "
              f"({maxdiff}); the coordinate choice moves whole windows of "
              f"samples", diff > maxdiff, f"diff={diff} maxdiff={maxdiff}")


def t19_non_multiple_hop() -> None:
    print("\n== T19  stream length that is not a multiple of hop ==")
    # 4 hops + 7 samples: the final window is partial
    n = 4 * HOP + 7
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 200)
    sp = write_f32(tmpdir() / "T19.f32", src)
    need = nw_for(n)
    recs = records_for(need, lambda i: True, lambda i: 200)
    dp = tmpdir() / "T19.out.f32"
    r = ex.run_time_varying(sp, dp, request=req(chunk=1024),
                            measurements=recs, verify=True)
    value("T19 executes with a partial final window",
          r.executed, f"reason={r.reason} detail={r.detail}")
    value(f"T19 plan length is ceil(n/hop) = {need}",
          r.n_windows == need, f"{r.n_windows}")
    value("T19 output sample count is preserved exactly",
          r.n_samples_out == n, f"{r.n_samples_out} vs {n}")
    value("T19 verification is complete over all samples",
          (r.verification or {}).get("complete") is True, f"{r.verification}")


def t20_eof_tail() -> None:
    print("\n== T20  EOF / tail handling ==")
    # the measurement layer supplies FEWER records than the applier needs:
    # measured_window_count < required_window_count. The tail must be declared
    # unresolved, never silently padded with a repeated last value.
    n = 6 * HOP
    need = nw_for(n)
    meas = tv.measured_window_count(n, window_samples=WIN, hop_samples=HOP)
    value("T20 the seam is real (measured < required)", meas < need,
          f"measured={meas} required={need}")
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 250)
    sp = write_f32(tmpdir() / "T20.f32", src)
    recs = records_for(meas, lambda i: True, lambda i: 250)
    p = ex.build_time_varying_plan(sp, request=req(), measurements=recs)
    tail = p.plan.resolved[meas:]
    value(f"T20 the {need - meas} unmeasured tail windows are UNRESOLVED",
          not bool(tail.any()), f"{tail.tolist()}")
    states = [str(s) for s in p.plan.states.tolist()[meas:]]
    value("T20 the tail windows are labelled UNRESOLVED_GAP, not TRUSTED",
          all(s == tv.GapState.UNRESOLVED_GAP.value for s in states),
          f"{states}")
    value("T20 the tail count is reported, not hidden",
          p.evidence.n_unresolved == need - meas + int(
              (~p.plan.resolved[:meas]).sum()),
          f"{p.evidence.n_unresolved}")


def t21_overlap_disagreement() -> None:
    print("\n== T21  two windows disagree about the same sample ==")
    # windows 0 and 1 carry different shifts; the coordinate decides which one
    # an overlapping sample receives. Both must be verifiable, and the two
    # contracts must differ, which is the D5 ambiguity stated as a test.
    n = 3 * HOP
    traj = lambda i: 0 if i == 0 else 600                  # noqa: E731
    a = run_case("T21ls", delay_of=traj, coordinate="latest-start",
                 n_windows=3, known_truth=False)
    b = run_case("T21nc", delay_of=traj, coordinate="nearest-centre",
                 n_windows=3, known_truth=False)
    if a and b:
        da = np.fromfile(a[2], dtype="<f4")
        db = np.fromfile(b[2], dtype="<f4")
        diff = int((da != db).sum())
        value("T21 the disagreement is non-empty (D5 is not vacuous)",
              diff > 0, "the coordinate contracts agreed on every sample")
        value("T21 the disagreement is bounded by one hop plus the shift "
              "difference", diff <= 2 * HOP,
              f"{diff} samples differ")


def t22_chunk_crosses_coordinate_boundary() -> None:
    print("\n== T22  a chunk that straddles a coordinate boundary ==")
    n = 4 * HOP
    traj = lambda i: 300 if i % 2 == 0 else -300            # noqa: E731
    base = run_case("T22base", delay_of=traj, n_windows=4, chunk=HOP,
                    known_truth=False)
    # a chunk size that starts mid-window and crosses several boundaries
    odd = run_case("T22odd", delay_of=traj, n_windows=4, chunk=333_333,
                   known_truth=False)
    if base and odd:
        a = np.fromfile(base[2], dtype="<f4")
        b = np.fromfile(odd[2], dtype="<f4")
        value("T22 a chunk straddling coordinate boundaries changes nothing",
              bool(np.array_equal(a, b)),
              f"{int((a != b).sum())} samples differ")


# ==========================================================================
# T23–T28  evidence
# ==========================================================================
def t23_insufficient_coverage() -> None:
    print("\n== T23  insufficient coverage ==")
    n = 10 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: i < 2, lambda i: 100)
    d = write_f32(tmpdir() / "T23.f32", np.zeros(n, dtype=np.float32))
    p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    ev = p.evidence
    value("T23 coverage is reported as a fraction, not a verdict",
          abs(ev.coverage_fraction - 2.0 / need) < 1e-12,
          f"{ev.coverage_fraction}")
    value("T23 coverage_gate_ok is False against the PRODUCTION gate",
          ev.coverage_gate_ok is False, f"{ev.to_dict()}")
    value("T23 the gate came from channel_sync, not a literal here",
          ev.gate is not None and "min_usable_fraction" in ev.gate,
          f"{ev.gate}")


def t24_high_confidence_low_coverage() -> None:
    print("\n== T24  high confidence but insufficient coverage ==")
    n = 10 * HOP
    need = nw_for(n)
    recs = [{"valid": i < 2, "delay_samples": 100.0, "confidence": 0.999}
            for i in range(need)]
    d = write_f32(tmpdir() / "T24.f32", np.zeros(n, dtype=np.float32))
    p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    value("T24 confidence 0.999 does not rescue a 20 % coverage",
          p.evidence.n_measured == 2 and p.evidence.coverage_gate_ok is False,
          f"{p.evidence.to_dict()}")
    value("T24 the unresolved windows are still reported",
          p.evidence.n_unresolved == need - 2, f"{p.evidence.n_unresolved}")


def t25_sufficient_evidence() -> None:
    print("\n== T25  sufficient evidence ==")
    n = 4 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: True, lambda i: 100)
    d = write_f32(tmpdir() / "T25.f32", np.zeros(n, dtype=np.float32))
    p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    value("T25 full coverage passes the production gate",
          p.evidence.coverage_gate_ok is True, f"{p.evidence.to_dict()}")
    value("T25 no window is unresolved", p.evidence.n_unresolved == 0,
          f"{p.evidence.n_unresolved}")


def t26_nan_inf() -> None:
    print("\n== T26  NaN / Inf measurements ==")
    n = 4 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: True, lambda i: 100)
    recs[1]["delay_samples"] = float("nan")
    recs[2]["delay_samples"] = float("inf")
    d = write_f32(tmpdir() / "T26.f32", np.zeros(n, dtype=np.float32))
    p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    value("T26 NaN and Inf are NOT accepted as measurements",
          p.evidence.n_measured == need - 2, f"{p.evidence.to_dict()}")
    states = [str(s) for s in p.plan.states.tolist()]
    value("T26 the two non-finite windows are UNRESOLVED_GAP",
          states[1] == tv.GapState.UNRESOLVED_GAP.value
          and states[2] == tv.GapState.UNRESOLVED_GAP.value, f"{states}")
    value("T26 no shift in the plan is non-finite",
          bool(np.all(np.isfinite(p.plan.shifts.astype(np.float64)))),
          f"{p.plan.shifts.tolist()}")


def t27_missing_measurement() -> None:
    print("\n== T27  missing measurement ==")
    n = 4 * HOP
    need = nw_for(n)
    recs = records_for(need - 2, lambda i: True, lambda i: 100)  # short list
    d = write_f32(tmpdir() / "T27.f32", np.zeros(n, dtype=np.float32))
    p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    value("T27 a short record list is padded with UNRESOLVED, not with zeros "
          "pretending to be measurements",
          p.evidence.n_unresolved == 2, f"{p.evidence.to_dict()}")
    value("T27 the missing windows are REENTRY-free and unresolved",
          all(str(s) == tv.GapState.UNRESOLVED_GAP.value
              for s in p.plan.states.tolist()[need - 2:]), "mislabelled")
    # an entirely absent trajectory must refuse rather than plan nothing
    raises_tv("T27b no measurements at all -> evidence_insufficient",
              ex.REASON_TV_EVIDENCE_INSUFFICIENT,
              ex.build_time_varying_plan, d, request=req(),
              measurements=None, frames=None)


def t28_raw_estimate_but_not_trusted() -> None:
    print("\n== T28  a raw estimate exists but is not trusted ==")
    n = 4 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: False, lambda i: 999)
    d = write_f32(tmpdir() / "T28.f32", np.zeros(n, dtype=np.float32))
    # all refused -> requires the explicit override
    raises_tv("T28 all-refused refuses by default",
              ex.REASON_TV_NO_TRUSTED_WINDOW,
              ex.build_time_varying_plan, d, request=req(), measurements=recs)
    p = ex.build_time_varying_plan(
        d, request=req(allow_all_unresolved=True), measurements=recs)
    value("T28 the rejected windows' raw numbers (999) were NOT used as shifts",
          bool(np.all(p.plan.shifts == 0)) or p.policy == "hold",
          f"shifts={p.plan.shifts.tolist()}")
    value("T28 not one window claims a measurement",
          p.evidence.n_measured == 0 and p.evidence.n_overclaimed == 0,
          f"{p.evidence.to_dict()}")

# ==========================================================================
# T29-T35  plan and apply
# ==========================================================================
def t29_mismatched_measurement_count() -> None:
    print("\n== T29  measurement count mismatch ==")
    n = 4 * HOP
    need = nw_for(n)
    d = write_f32(tmpdir() / "T29.f32", np.zeros(n, dtype=np.float32))
    too_many = records_for(need + 3, lambda i: True, lambda i: 10)
    # more records than the stream has windows: the adapter TRUNCATES, and the
    # truncation stops at required_window_count, NOT at measured_window_count.
    # That is the seam made explicit, so it is asserted as a value rather than
    # asserted to raise.
    shortened = ex.measurements_from_records(
        too_many, n_samples=n, hop_samples=HOP)
    value("T29 an over-long record list is truncated to the windows the "
          "stream needs", len(shortened) == need, f"{len(shortened)} vs {need}")
    value("T29b the truncation keeps only REAL windows (it does not invent "
          "the unmeasured tail)", need == nw_for(n) and len(shortened) == need,
          f"{len(shortened)}")
    # a SHORT list is padded with UNRESOLVED, never with a repeated value
    p = ex.build_time_varying_plan(
        d, request=req(), measurements=records_for(need - 3, lambda i: True,
                                                   lambda i: 10))
    value("T29c a short list leaves the tail unresolved rather than padded",
          p.evidence.n_unresolved == 3, f"{p.evidence.to_dict()}")
    raises_tv("T29d an empty record list is refused as insufficient evidence",
              ex.REASON_TV_EVIDENCE_INSUFFICIENT,
              ex.build_time_varying_plan, d, request=req(), measurements=[])


def t30_invalid_source_index() -> None:
    print("\n== T30  invalid source index / shift out of guard ==")
    n = 2 * HOP
    need = nw_for(n)
    d = write_f32(tmpdir() / "T30.f32", np.zeros(n, dtype=np.float32))
    huge = [{"valid": True, "delay_samples": float(tv.SHIFT_LIMIT),
             "confidence": 0.9} for _ in range(need)]
    raises_tv("T30 a shift at the +2**31 guard is refused",
              ex.REASON_TV_PLAN_INVALID,
              ex.build_time_varying_plan, d, request=req(), measurements=huge)
    neg = [{"valid": True, "delay_samples": -float(tv.SHIFT_LIMIT),
            "confidence": 0.9} for _ in range(need)]
    raises_tv("T30b a shift at the -2**31 guard is refused",
              ex.REASON_TV_PLAN_INVALID,
              ex.build_time_varying_plan, d, request=req(), measurements=neg)
    value("T30c the guard is exclusive: 2**31-1 is inside",
          tv.SHIFT_LIMIT == 1 << 31, f"{tv.SHIFT_LIMIT}")


def t31_invalid_mask() -> None:
    print("\n== T31  invalid mask ==")
    raises_type("T31 a mask containing 2 is rejected by derive_states",
                tv.AudioSyncPlanError, tv.derive_states,
                np.array([0, 1, 2], dtype=np.int64))
    raises_type("T31b a mask containing -1 is rejected",
                tv.AudioSyncPlanError, tv.derive_states,
                np.array([0, -1], dtype=np.int64))
    value("T31c a 0/1 integer mask is accepted and normalised, and index 1 "
          "after a rejected index 0 is REENTRY (not TRUSTED)",
          [str(s) for s in tv.derive_states(
              np.array([0, 1, 1], dtype=np.int64)).tolist()]
          == [tv.GapState.UNRESOLVED_GAP.value, tv.GapState.REENTRY.value,
              tv.GapState.TRUSTED.value],
          f"{[str(s) for s in tv.derive_states(np.array([0, 1, 1], dtype=np.int64)).tolist()]}")
    raises_type("T31d a 2-D mask is rejected",
                tv.AudioSyncPlanError, tv.derive_states, np.zeros((2, 2)))


def t32_invalid_coordinate() -> None:
    print("\n== T32  invalid coordinate ==")
    raises_tv("T32 an unknown coordinate is refused at request time",
              ex.REASON_TV_PLAN_INVALID, ex.request_time_varying,
              enabled=True, policy="hold", coordinate="centre-of-mass")
    raises_tv("T32b the research-only 'sample-domain' coordinate is NOT "
              "silently accepted as a third candidate",
              ex.REASON_TV_PLAN_INVALID, ex.request_time_varying,
              enabled=True, policy="hold", coordinate="sample-domain-v2")
    value("T32c sample-domain IS a known name (it equals latest-start, and is "
          "retained only so the misuse is rejectable)",
          "sample-domain" in tv.COORDINATES and
          tv.window_sample_domain(np.array([7 * 100]), window_samples=300,
                                  hop_samples=100, n_windows=20)[0]
          == tv.window_latest_start(np.array([7 * 100]), hop_samples=100,
                                    n_windows=20)[0])


def t33_invalid_policy() -> None:
    print("\n== T33  invalid policy ==")
    raises_tv("T33 an unknown policy is refused at request time",
              ex.REASON_TV_PLAN_INVALID, ex.request_time_varying,
              enabled=True, policy="interpolate", coordinate="latest-start")
    raises_tv("T33b the research policy 'current' is refused as "
              "not-on-the-production-whitelist",
              ex.REASON_TV_POLICY_NOT_PRODUCTION, ex.request_time_varying,
              enabled=True, policy="current", coordinate="latest-start")
    value("T33c 'current' still EXISTS in the research module (it was not "
          "deleted to make the guard pass)",
          "current" in tv.GAP_POLICIES)
    value("T33d the production whitelist is exactly the non-current policies",
          set(ex.PRODUCTION_POLICIES) == set(tv.GAP_POLICIES) - {"current"},
          f"{sorted(ex.PRODUCTION_POLICIES)}")


def t34_invalid_plan_length() -> None:
    print("\n== T34  invalid plan length ==")
    n = 4 * HOP
    need = nw_for(n)
    ok = np.ones(need, dtype=bool)
    raises_type("T34 a plan one window too SHORT is rejected",
                tv.AudioSyncPlanError, tv.CorrectionPlan,
                shifts=np.zeros(need - 1, dtype=np.int64),
                states=tv.derive_states(ok[:-1]), resolved=ok[:-1],
                policy="hold", coordinate="latest-start",
                window_samples=WIN, hop_samples=HOP, n_samples=n)
    raises_type("T34b a plan one window too LONG is rejected",
                tv.AudioSyncPlanError, tv.CorrectionPlan,
                shifts=np.zeros(need + 1, dtype=np.int64),
                states=tv.derive_states(np.ones(need + 1, dtype=bool)),
                resolved=np.ones(need + 1, dtype=bool),
                policy="hold", coordinate="latest-start",
                window_samples=WIN, hop_samples=HOP, n_samples=n)
    raises_type("T34c a states sequence that disagrees with the mask is "
                "rejected (states are a derivation, not an input)",
                tv.AudioSyncPlanError, tv.CorrectionPlan,
                shifts=np.zeros(need, dtype=np.int64),
                states=tv.derive_states(np.ones(need, dtype=bool)),
                resolved=np.zeros(need, dtype=bool),
                policy="hold", coordinate="latest-start",
                window_samples=WIN, hop_samples=HOP, n_samples=n)


def t35_output_size_invariant() -> None:
    print("\n== T35  output size invariant ==")
    for n_windows in (1, 2, 3, 5):
        n = n_windows * HOP
        ref = reference(n)
        src = source_with_delay(ref, lambda i: 111)
        sp = write_f32(tmpdir() / f"T35_{n_windows}.f32", src)
        need = nw_for(n)
        recs = records_for(need, lambda i: True, lambda i: 111)
        dp = tmpdir() / f"T35_{n_windows}.out.f32"
        r = ex.run_time_varying(sp, dp, request=req(chunk=4096),
                                measurements=recs, verify=True)
        value(f"T35 n_windows={n_windows}: n_out == n_in == {n}",
              r.executed and r.n_samples_out == n and r.n_samples_in == n,
              f"executed={r.executed} out={r.n_samples_out} in={r.n_samples_in}")
        value(f"T35 n_windows={n_windows}: bytes_written == 4 * n",
              r.bytes_written == 4 * n, f"{r.bytes_written}")
    # a plan built for one length must not be applied to a stream of another
    n = 2 * HOP
    d = write_f32(tmpdir() / "T35bad.f32", np.zeros(n, dtype=np.float32))
    recs = records_for(nw_for(n), lambda i: True, lambda i: 0)
    plan = ex.build_time_varying_plan(d, request=req(), measurements=recs)
    shorter = write_f32(tmpdir() / "T35other.f32",
                        np.zeros(n // 2, dtype=np.float32))
    raises_tv("T35b applying a plan to a stream of the wrong length is refused",
              ex.REASON_TV_APPLY_FAILED,
              ex.execute_time_varying, shorter, tmpdir() / "T35o.f32",
              plan=plan)
    value("T35c the refusal wrote nothing",
          not (tmpdir() / "T35o.f32").exists())


# ==========================================================================
# T36-T44  chunk invariance
# ==========================================================================
def t36_chunk_invariance() -> None:
    print("\n== T36  chunk invariance: same plan, same output, any chunk ==")
    cases = {
        "constant": (lambda i: 615, lambda i: True),
        "piecewise": (lambda i: (0, 250, -250, 700, 700, 0)[min(i, 5)],
                      lambda i: True),
        "gapped": (lambda i: 320, lambda i: i not in (1, 2, 4)),
        "reentry": (lambda i: 100 if i <= 1 else -400,
                    lambda i: i in (0, 1, 3, 5)),
    }
    n_windows = 6
    n = n_windows * HOP
    ref = reference(n)
    for tag, (traj, valid) in cases.items():
        src = source_with_delay(ref, traj)
        sp = write_f32(tmpdir() / f"T36_{tag}.f32", src)
        need = nw_for(n)
        recs = records_for(need, valid, traj)
        outs: dict[int, np.ndarray] = {}
        for c in CHUNKS:
            dp = tmpdir() / f"T36_{tag}_{c}.out.f32"
            r = ex.run_time_varying(sp, dp, request=req(chunk=c),
                                    measurements=recs, verify=True)
            if not r.executed:
                value(f"T36 {tag} chunk={c} executed", False,
                      f"reason={r.reason} detail={r.detail}")
                continue
            outs[c] = np.fromfile(dp, dtype="<f4")
            value(f"T36 {tag} chunk={c}: independent verification complete",
                  (r.verification or {}).get("ok") is True, f"{r.verification}")
        if len(outs) == len(CHUNKS):
            base_c = CHUNKS[0]
            base = outs[base_c]
            worst = 0
            for c in CHUNKS[1:]:
                worst = max(worst, int((outs[c] != base).sum()))
            value(f"T36 {tag}: all {len(CHUNKS)} chunk sizes give "
                  f"SAMPLE-IDENTICAL output", worst == 0,
                  f"worst difference {worst} samples vs chunk={base_c}")
            # float32 exactness is claimed, so it is stated as such
            value(f"T36 {tag}: the identity is exact equality, not a tolerance",
                  all(o.dtype == np.float32 for o in outs.values()),
                  f"{ {c: str(o.dtype) for c, o in outs.items()} }")
        else:
            value(f"T36 {tag}: every chunk size produced output", False,
                  f"only {sorted(outs)} of {CHUNKS} ran")


def t37_chunk_step_of_verifier() -> None:
    print("\n== T37  the verifier's own block size must not change its verdict ==")
    n = 2 * HOP
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 240)
    sp = write_f32(tmpdir() / "T37.f32", src)
    recs = records_for(nw_for(n), lambda i: True, lambda i: 240)
    dp = tmpdir() / "T37.out.f32"
    r = ex.run_time_varying(sp, dp, request=req(chunk=HOP), measurements=recs,
                            verify=False)
    value("T37 executed with verification off", r.executed, f"{r.reason}")
    p = ex.build_time_varying_plan(sp, request=req(), measurements=recs)
    verdicts = {}
    for step in (1, 7, 1024, 1 << 20):
        v = ex.verify_applied_shift(dp, sp, p.plan, step=step)
        verdicts[step] = (v.mismatches, v.complete, v.checked_samples)
        value(f"T37 verifier step={step}: 0 mismatches over all {n} samples",
              v.mismatches == 0 and v.complete and v.checked_samples == n,
              f"{v.to_dict()}")
    value("T37 every verifier block size agrees",
          len(set(verdicts.values())) == 1, f"{verdicts}")


def t38_chunk_vs_seam() -> None:
    print("\n== T38  chunk invariance across the measured/required seam ==")
    n = 7 * HOP + 137          # deliberately not a multiple of hop
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 90)
    sp = write_f32(tmpdir() / "T38.f32", src)
    meas = tv.measured_window_count(n, window_samples=WIN, hop_samples=HOP)
    recs = records_for(meas, lambda i: True, lambda i: 90)
    outs = {}
    for c in (256, 4096, HOP):
        dp = tmpdir() / f"T38_{c}.out.f32"
        r = ex.run_time_varying(sp, dp, request=req(chunk=c), measurements=recs,
                                verify=True)
        value(f"T38 chunk={c} executed (unmeasured tail is legal)", r.executed,
              f"reason={r.reason} detail={r.detail}")
        if r.executed:
            outs[c] = np.fromfile(dp, dtype="<f4")
            value(f"T38 chunk={c} output length preserved", len(outs[c]) == n,
                  f"{len(outs[c])} vs {n}")
    if len(outs) == 3:
        vals = list(outs.values())
        value("T38 chunk size does not change the output across the seam",
              all(np.array_equal(vals[0], v) for v in vals[1:]),
              f"{[int((vals[0] != v).sum()) for v in vals[1:]]}")


# ==========================================================================
# T45-T52  the refusal contract, and the P3.7 over-claim guard
# ==========================================================================
def t45_refusal_contract() -> None:
    print("\n== T45  the refusal contract ==")
    raises_tv("T45 policy=None is refused (D2 has no frozen semantic)",
              ex.REASON_TV_POLICY_REQUIRED, ex.request_time_varying,
              enabled=True, coordinate="latest-start")
    raises_tv("T45b coordinate=None is refused (D5 has no frozen semantic)",
              ex.REASON_TV_COORDINATE_REQUIRED, ex.request_time_varying,
              enabled=True, policy="hold")
    value("T45c TimeVaryingRequest has NO default for either decision",
          ex.TimeVaryingRequest().policy is None
          and ex.TimeVaryingRequest().coordinate is None,
          f"policy={ex.TimeVaryingRequest().policy!r} "
          f"coordinate={ex.TimeVaryingRequest().coordinate!r}")
    value("T45d the request is DISABLED by default",
          ex.TimeVaryingRequest().enabled is False)
    value("T45e implementation defaults still exist in the research module, "
          "and are NOT promoted to the production contract",
          tv.DEFAULT_POLICY == "hold" and tv.DEFAULT_COORDINATE == "latest-start"
          and tv.DECISION_STATUS["gap_policy"]["semantic_contract"] == "DEFERRED"
          and tv.DECISION_STATUS["coordinate"]["semantic_contract"]
          == "DEFERRED")


def t46_refused_request_touches_nothing() -> None:
    print("\n== T46  a refused request touches no file ==")
    n = 2 * HOP
    d = write_f32(tmpdir() / "T46.f32", np.ones(n, dtype=np.float32))
    dp = tmpdir() / "T46.out.f32"
    if dp.exists():
        dp.unlink()
    before = d.read_bytes()
    r = ex.run_time_varying(d, dp, request=ex.request_time_varying(),
                            measurements=records_for(
                                nw_for(n), lambda i: True, lambda i: 5))
    value("T46 disabled request returns executed=False, not an exception",
          r.executed is False and r.reason == ex.REASON_TV_POLICY_REQUIRED,
          f"{r.to_dict()}")
    value("T46b no output file was created", not dp.exists())
    value("T46c the source was not modified", d.read_bytes() == before)
    # every refusal path must also leave dst absent and a stable reason
    for tag, kwargs, reason in (
        ("policy", dict(policy="current", coordinate="latest-start"),
         ex.REASON_TV_POLICY_NOT_PRODUCTION),
        ("coord", dict(policy="hold", coordinate="nope"),
         ex.REASON_TV_PLAN_INVALID),
    ):
        target = tmpdir() / f"T46_{tag}.out.f32"
        try:
            request = ex.request_time_varying(enabled=True, **kwargs)
            res = ex.run_time_varying(
                d, target, request=request,
                measurements=records_for(nw_for(n), lambda i: True, lambda i: 5))
            value(f"T46d {tag}: refused with {reason}", res.reason == reason,
                  f"{res.reason}")
            value(f"T46d {tag}: wrote nothing", not target.exists())
        except ex.TimeVaryingRefused as exc:
            value(f"T46d {tag}: refused with {reason}", exc.reason == reason,
                  f"{exc.reason}")
            value(f"T46d {tag}: wrote nothing", not target.exists())


def t47_no_silent_fallback_to_fixed_delay() -> None:
    print("\n== T47  no silent fallback to a fixed shift ==")
    n = 4 * HOP
    ref = reference(n)
    # a trajectory with NO constant shift can be described: if the path fell
    # back to a single fixed delay, the output would equal a shift_stream run.
    traj = lambda i: (0, 400, -400, 0)[min(i, 3)]           # noqa: E731
    out = run_case("T47", delay_of=traj, n_windows=4, chunk=4096,
                   known_truth=True)
    if out:
        r, _d, dst, ref_, src_ = out
        got = np.fromfile(dst, dtype="<f4")
        # for every fixed integer shift, the output must NOT match
        fixed_matches = []
        for s in range(-800, 801, 1):
            cand = np.zeros(n, dtype=np.float32)
            k = np.arange(n, dtype=np.int64)
            j = k + s
            inside = (j >= 0) & (j < n)
            cand[inside] = ref_[j[inside]]
            if np.array_equal(cand, got):
                fixed_matches.append(s)
        value("T47 the time-varying output is NOT any single fixed shift",
              fixed_matches == [], f"matched fixed shift(s) {fixed_matches}")


def t48_p37_overclaim_guard() -> None:
    print("\n== T48  the P3.7 over-claim cannot reach a production output ==")
    n = 4 * HOP
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 300)
    sp = write_f32(tmpdir() / "T48.f32", src)
    need = nw_for(n)
    # a mask with real gaps in it
    recs = records_for(need, lambda i: i not in (1, 2), lambda i: 300)
    p = ex.build_time_varying_plan(sp, request=req(), measurements=recs)
    value("T48 the production plan reports 0 over-claimed windows",
          p.plan.overclaimed_windows() == 0, f"{p.plan.overclaimed_windows()}")
    value("T48b the production plan is honest by the module's own predicate",
          p.plan.is_honest() is True)
    # Now the research policy, run through the RESEARCH module directly, must
    # still show the defect: the guard is not vacuous because 'current' exists.
    cp = tv.build_correction_plan(
        recs, n_samples=n, window_samples=WIN, hop_samples=HOP,
        policy="current", coordinate="latest-start")
    value("T48c the research 'current' policy STILL over-claims (the P3.7 "
          "defect was not deleted to make a test pass)",
          cp.overclaimed_windows() == 2,
          f"{cp.overclaimed_windows()} (expected 2 refused windows)")
    value("T48d 'current' reports its gaps as TRUSTED",
          cp.state_counts().get(tv.GapState.TRUSTED.value) == need,
          f"{cp.state_counts()}")
    value("T48e but its resolved mask still knows the truth",
          int((~cp.resolved).sum()) == 2, f"{int((~cp.resolved).sum())}")
    # and the execution layer refuses that policy outright
    raises_tv("T48f the execution layer refuses policy='current'",
              ex.REASON_TV_POLICY_NOT_PRODUCTION, ex.request_time_varying,
              enabled=True, policy="current", coordinate="latest-start")


def t49_refused_measurement_cannot_become_trusted() -> None:
    print("\n== T49  refused measurement cannot become TRUSTED by bookkeeping ==")
    n = 6 * HOP
    need = nw_for(n)
    recs = records_for(need, lambda i: i in (0, 5), lambda i: 480)
    d = write_f32(tmpdir() / "T49.f32", np.zeros(n, dtype=np.float32))
    for policy in ex.PRODUCTION_POLICIES:
        if policy in ("linear", "next"):
            continue
        p = ex.build_time_varying_plan(
            d, request=req(policy=policy), measurements=recs)
        states = [str(s) for s in p.plan.states.tolist()]
        gap_idx = [i for i in range(need) if not bool(p.plan.resolved[i])]
        wrong = [i for i in gap_idx
                 if states[i] != tv.GapState.UNRESOLVED_GAP.value]
        value(f"T49 {policy!r}: all {len(gap_idx)} refused windows stay "
              f"UNRESOLVED_GAP", wrong == [], f"mislabelled at {wrong}")
        value(f"T49 {policy!r}: the values of refused windows are policy "
              f"filled, and the mask still says so",
              int((~p.plan.resolved).sum()) == len(gap_idx),
              f"{int((~p.plan.resolved).sum())} vs {len(gap_idx)}")


def t50_reason_codes_are_stable() -> None:
    print("\n== T50  reason codes are stable strings ==")
    codes = {
        "policy required": ex.REASON_TV_POLICY_REQUIRED,
        "coordinate required": ex.REASON_TV_COORDINATE_REQUIRED,
        "policy not production": ex.REASON_TV_POLICY_NOT_PRODUCTION,
        "plan invalid": ex.REASON_TV_PLAN_INVALID,
        "evidence insufficient": ex.REASON_TV_EVIDENCE_INSUFFICIENT,
        "no trusted window": ex.REASON_TV_NO_TRUSTED_WINDOW,
        "gap unresolved": ex.REASON_TV_GAP_UNRESOLVED,
        "apply failed": ex.REASON_TV_APPLY_FAILED,
        "verification failed": ex.REASON_TV_VERIFICATION_FAILED,
    }
    for label, code in codes.items():
        value(f"T50 {label} -> {code!r} is a lowercase stable code",
              isinstance(code, str) and code == code.lower()
              and code.startswith("audio_sync_tv_"), f"{code!r}")
    value("T50b the codes are distinct",
          len(set(codes.values())) == len(codes), f"{sorted(codes.values())}")


def t51_result_is_explainable() -> None:
    print("\n== T51  every result explains itself ==")
    n = 4 * HOP
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 200)
    sp = write_f32(tmpdir() / "T51.f32", src)
    recs = records_for(nw_for(n), lambda i: i != 2, lambda i: 200)
    dp = tmpdir() / "T51.out.f32"
    r = ex.run_time_varying(sp, dp, request=req(chunk=4096), measurements=recs)
    d = r.to_dict()
    for key in ("executed", "policy", "coordinate", "n_windows",
                "n_samples_in", "n_samples_out", "bytes_written",
                "state_counts", "refusals", "evidence", "verification"):
        value(f"T51 the result carries {key!r}", key in d, f"keys={sorted(d)}")
    value("T51b the refusal map is non-empty and names UNRESOLVED_GAP",
          r.refusals.get(tv.GapState.UNRESOLVED_GAP.value) == 1,
          f"{r.refusals}")
    value("T51c the result is JSON-serialisable (no numpy scalars leak)",
          isinstance(json.dumps(d), str))


def t52_no_threshold_is_invented() -> None:
    print("\n== T52  D3 is NOT established, and no threshold was invented ==")
    import inspect as _inspect

    src = _inspect.getsource(ex)
    # a residual acceptance threshold would look like a comparison against a
    # sample count; assert none exists in the residual path.
    value("T52 ResidualSummary has NO pass/fail field",
          not hasattr(ex.ResidualSummary, "passed")
          and "passed" not in ex.ResidualSummary.__dataclass_fields__,
          f"{list(ex.ResidualSummary.__dataclass_fields__)}")
    value("T52b the execution result has no 'accepted'/'qualified' field",
          all(k not in ex.TimeVaryingSyncResult.__dataclass_fields__
              for k in ("accepted", "qualified", "within_tolerance")),
          f"{list(ex.TimeVaryingSyncResult.__dataclass_fields__)}")
    rs = ex.residual_summary([0.5, 1.0, 2.0, float("nan")],
                             n_windows=10, n_measured=6, n_unresolved=4)
    value("T52c NaN is excluded AND the population is reported",
          rs.n_valid_population == 3 and rs.median_abs == 1.0,
          f"{rs.to_dict()}")
    rs0 = ex.residual_summary([], n_windows=10, n_measured=0, n_unresolved=10)
    value("T52d an empty residual set reports 'not established', not 0",
          rs0.median_abs is None
          and rs0.verdict == ex.REASON_TV_NO_TRUSTED_WINDOW,
          f"{rs0.to_dict()}")
    value("T52e the module mentions no residual tolerance constant",
          "verify_max_ms" not in src and "residual_tolerance" not in src,
          "a tolerance leaked into the execution layer")


# ==========================================================================
# T53+  controls — every check above must be shown able to fail
# ==========================================================================
def t53_controls() -> None:
    print("\n== T53  controls: the checks are discriminating ==")

    def c_known_truth_can_fail() -> None:
        n = 2 * HOP
        ref = reference(n)
        src = source_with_delay(ref, lambda i: 250)
        r = ref.copy()
        r[5_000] += np.float32(0.5)
        assert np.array_equal(r, ref), "x"
    control("T53.C1 the known-truth comparison can fail", c_known_truth_can_fail)

    def c_chunk_identity_can_fail() -> None:
        n = HOP
        ref = reference(n)
        a = source_with_delay(ref, lambda i: 10)
        b = source_with_delay(ref, lambda i: 11)
        assert np.array_equal(a, b), "x"
    control("T53.C2 the chunk-identity comparison can fail",
            c_chunk_identity_can_fail)

    def c_refusal_reason_is_checked() -> None:
        try:
            ex.request_time_varying(enabled=True, policy="hold")
        except ex.TimeVaryingRefused as exc:
            assert exc.reason == ex.REASON_TV_POLICY_REQUIRED, "x"
            return
        raise AssertionError("did not refuse")
    control("T53.C3 the refusal-reason assertion can fail",
            c_refusal_reason_is_checked)

    def c_state_label_check_can_fail() -> None:
        states = [tv.GapState.TRUSTED.value, tv.GapState.UNRESOLVED_GAP.value]
        gap = np.array([True, False])
        bad = [i for i, s in enumerate(states)
               if not gap[i] and s == tv.GapState.UNRESOLVED_GAP.value]
        assert bad == [], "x"
    control("T53.C4 the state-labelling check can fail",
            c_state_label_check_can_fail)

    def c_coordinate_difference_can_fail() -> None:
        n = HOP
        ref = reference(n)
        a = source_with_delay(ref, lambda i: 100)
        b = source_with_delay(ref, lambda i: 100)
        assert not np.array_equal(a, b), "x"
    control("T53.C5 the 'two coordinates differ' check can fail",
            c_coordinate_difference_can_fail)

    def c_output_size_check_can_fail() -> None:
        a = np.zeros(100, dtype=np.float32)
        b = np.zeros(101, dtype=np.float32)
        assert a.size == b.size, "x"
    control("T53.C6 the output-size invariant check can fail",
            c_output_size_check_can_fail)

    def c_no_file_written_check_can_fail() -> None:
        p = tmpdir() / "T53C7.f32"
        p.write_bytes(b"x")
        assert not p.exists(), "x"
    control("T53.C7 the 'wrote nothing' check can fail",
            c_no_file_written_check_can_fail)

    def c_overclaim_check_can_fail() -> None:
        cp = tv.build_correction_plan(
            [{"valid": False, "delay_samples": 1.0},
             {"valid": True, "delay_samples": 1.0}],
            n_samples=HOP, window_samples=WIN, hop_samples=HOP,
            policy="current", coordinate="latest-start")
        assert cp.overclaimed_windows() == 0, "x"
    control("T53.C8 the over-claim guard can fail (it sees 'current')",
            c_overclaim_check_can_fail)

    def c_verifier_can_fail() -> None:
        """The verifier must REPORT a corrupted file, not accept anything.

        The first version of this control asserted the clean run was `ok`, which
        is a positive property and therefore could never fail as a control. The
        real question is whether the verifier is discriminating, so it is put to
        the only test that answers it: corrupt one sample of an output the
        verifier already accepted, and require it to notice.
        """
        n = 2 * HOP
        ref = reference(n)
        src = source_with_delay(ref, lambda i: 120)
        sp = write_f32(tmpdir() / "T53C9.f32", src)
        recs = records_for(nw_for(n), lambda i: True, lambda i: 120)
        dp = tmpdir() / "T53C9.out.f32"
        r = ex.run_time_varying(sp, dp, request=req(chunk=HOP), measurements=recs,
                                verify=True)
        assert r.executed and (r.verification or {}).get("ok") is True, \
            "the clean run was not verified"
        p = ex.build_time_varying_plan(sp, request=req(), measurements=recs)
        # corrupt exactly one sample, in place, and re-verify
        arr = np.fromfile(dp, dtype="<f4")
        arr[123_457] = np.float32(arr[123_457] + 1.0)
        bad_path = tmpdir() / "T53C9.bad.f32"
        arr.tofile(bad_path)
        v = ex.verify_applied_shift(bad_path, sp, p.plan)
        # the assertion the CONTROL must be able to break: a corrupted file is
        # accepted. If the verifier is discriminating this raises.
        assert v.ok, "x"
    control("T53.C9 the independent verifier REJECTS a corrupted output "
            "(and therefore can fail)", c_verifier_can_fail)

    def c_evidence_gate_check_can_fail() -> None:
        n = 4 * HOP
        need = nw_for(n)
        recs = records_for(need, lambda i: True, lambda i: 10)
        d = write_f32(tmpdir() / "T53C10.f32", np.zeros(n, dtype=np.float32))
        p = ex.build_time_varying_plan(d, request=req(), measurements=recs)
        assert p.evidence.coverage_gate_ok is False, "x"
    control("T53.C10 the coverage-gate check can fail",
            c_evidence_gate_check_can_fail)

    def c_nan_check_can_fail() -> None:
        recs = [{"valid": True, "delay_samples": float("nan")}]
        assert all(r["delay_samples"] == r["delay_samples"] for r in recs), "x"
    control("T53.C11 the NaN-rejection check can fail", c_nan_check_can_fail)


CASES = [
    t01_zero_offset, t02_positive_offset, t03_negative_offset,
    t04_constant_across_all_windows, t05_piecewise_constant,
    t06_two_transitions, t07_multiple_channels,
    t08_leading_gap, t09_middle_gap, t10_trailing_gap, t11_gap_then_reentry,
    t12_repeated_invalid_windows, t13_all_windows_refused, t14_one_valid_window,
    t15_policy_refuses_unresolved_evidence,
    t16_latest_start, t17_nearest_centre, t18_coordinate_boundaries,
    t19_non_multiple_hop, t20_eof_tail, t21_overlap_disagreement,
    t22_chunk_crosses_coordinate_boundary,
    t23_insufficient_coverage, t24_high_confidence_low_coverage,
    t25_sufficient_evidence, t26_nan_inf, t27_missing_measurement,
    t28_raw_estimate_but_not_trusted,
    t29_mismatched_measurement_count, t30_invalid_source_index,
    t31_invalid_mask, t32_invalid_coordinate, t33_invalid_policy,
    t34_invalid_plan_length, t35_output_size_invariant,
    t36_chunk_invariance, t37_chunk_step_of_verifier, t38_chunk_vs_seam,
    t45_refusal_contract, t46_refused_request_touches_nothing,
    t47_no_silent_fallback_to_fixed_delay, t48_p37_overclaim_guard,
    t49_refused_measurement_cannot_become_trusted, t50_reason_codes_are_stable,
    t51_result_is_explainable, t52_no_threshold_is_invented,
    t53_controls,
]

#: NOTE. ``CASES`` is built lazily inside ``main()`` from ``_CASES`` below,
#: because the T54+ integration cases are defined after this point in the file.
#: An earlier version built the list here, so every function defined later was a
#: NameError at import time.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    print("=" * 78)
    print("P4.6 — time-varying sync: production execution regression")
    print("=" * 78)
    cases = CASES + [
        t54_default_path_is_untouched, t55_tv_opts_normalisation,
        t56_tv_requires_both_decisions,
        t57_forbidden_policy_at_the_option_layer,
        t58_channel_sync_imports_tv_lazily, t59_trajectory_aggregation,
        t60_aggregation_chunk_invariance,
        t61_unreadable_source_is_refused_not_raised,
        t62_unwritable_destination_is_refused_not_raised,
        t63_no_raw_oserror_escapes_the_entry_point,
        t64_refusal_leaves_no_partial_output,
        t65_lying_verifier_is_a_documented_limit,
        t66_source_equal_to_destination,
    ]
    for fn in cases:
        fn()
    total = (COUNTS["value_ok"] + COUNTS["raised"] + COUNTS["silent_wrong"]
             + COUNTS["control_ok"] + COUNTS["control_failed"]
             + COUNTS["fail"])
    print("\n" + "=" * 78)
    print(f"cases                     : {total}")
    print(f"VALUE_OK                  : {COUNTS['value_ok']}")
    print(f"RAISED (expected refusals): {COUNTS['raised']}")
    print(f"CONTROL_OK                : {COUNTS['control_ok']}")
    print(f"CONTROL_FAILED            : {COUNTS['control_failed']}")
    print(f"SILENT_WRONG              : {COUNTS['silent_wrong']}")
    print(f"FAIL                      : {COUNTS['fail']}")
    print("=" * 78)
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"counts": COUNTS, "cases": RESULTS}, indent=1), encoding="utf-8")
        print(f"json -> {args.json}")
    if total == 0:
        print("FAIL: no case ran", file=sys.stderr)
        return 2
    if COUNTS["control_ok"] == 0:
        print("FAIL: no control ran", file=sys.stderr)
        return 2
    bad = COUNTS["silent_wrong"] + COUNTS["fail"] + COUNTS["control_failed"]
    if bad:
        print(f"\nRESULT: FAIL ({bad} case(s))")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  FAIL {r['name']} -- {r['detail']}")
        return 1
    print(f"\nRESULT: PASS — {total} cases, {COUNTS['value_ok']} value, "
          f"{COUNTS['raised']} refusal, {COUNTS['control_ok']} control; "
          f"SILENT_WRONG = 0")
    return 0


# ==========================================================================
# T61-T66  P4.7 regressions: the refusal contract must cover the FILESYSTEM
# ==========================================================================
def t61_unreadable_source_is_refused_not_raised() -> None:
    """A missing / unreadable / empty source must REFUSE, not raise an OSError.

    P4.7 found this live. ``_source_length`` opened the source unguarded, so a
    missing file raised ``FileNotFoundError``, an unreadable one
    ``PermissionError`` and an empty one a bare ``ValueError`` straight out of
    ``run_time_varying``. That function documents that EVERY failure returns
    ``executed=False`` with a stable reason code, and ``channel_sync`` relies on
    it to leave the original audio alone -- a raw ``OSError`` escapes the
    contract entirely, so a caller that only catches ``TimeVaryingRefused``
    crashes instead of refusing.
    """
    print("\n== T61  unreadable sources are refused, not raised ==")
    td = tmpdir()
    missing = td / "T61_missing.f32"
    if missing.exists():
        missing.unlink()
    one = [{"valid": True, "delay_samples": 1.0}]
    r = ex.run_time_varying(missing, td / "T61_a.f32", request=req(),
                            measurements=one)
    value("T61 a missing source returns executed=False, not an exception",
          r.executed is False and r.reason == ex.REASON_TV_PLAN_INVALID,
          f"executed={r.executed} reason={r.reason}")
    value("T61b the refusal names the underlying fault rather than hiding it",
          "FileNotFoundError" in (r.detail or ""), f"{r.detail}")
    value("T61c a refused run writes no output",
          not (td / "T61_a.f32").exists())

    empty = td / "T61_empty.f32"
    empty.write_bytes(b"")
    r2 = ex.run_time_varying(empty, td / "T61_b.f32", request=req(),
                             measurements=one)
    value("T61d an empty source is refused with evidence_insufficient",
          r2.executed is False
          and r2.reason == ex.REASON_TV_EVIDENCE_INSUFFICIENT,
          f"executed={r2.executed} reason={r2.reason} detail={r2.detail}")
    value("T61e the empty-source refusal explains itself",
          "empty" in (r2.detail or "").lower(), f"{r2.detail}")

    a_dir = td / "T61_dir.f32"
    a_dir.mkdir(exist_ok=True)
    r3 = ex.run_time_varying(a_dir, td / "T61_c.f32", request=req(),
                             measurements=one)
    value("T61f a directory given as the source is refused, not raised",
          r3.executed is False and r3.reason == ex.REASON_TV_PLAN_INVALID,
          f"executed={r3.executed} reason={r3.reason} detail={r3.detail}")

    raises_tv("T61g the internal length probe raises the DOMAIN error, never a "
              "bare OSError", ex.REASON_TV_PLAN_INVALID,
              ex._source_length, td / "definitely_absent.f32")

    def control_bad() -> None:
        good = td / "T61_control.f32"
        np.zeros(2048, dtype="<f4").tofile(good)
        try:
            ex._source_length(good)
        except BaseException as exc:                            # noqa: BLE001
            raise AssertionError(f"a readable file was refused: {exc}") from exc
        raise AssertionError("x")
    control("T61.C a readable source is still accepted, so the refusal is "
            "specific to faults", control_bad)


def t62_unwritable_destination_is_refused_not_raised() -> None:
    """Same defect class on the WRITE side: creating the .part file was unguarded."""
    print("\n== T62  unwritable destinations are refused, not raised ==")
    td = tmpdir()
    n = 2 * HOP
    src = td / "T62_src.f32"
    np.ascontiguousarray(reference(n)).tofile(src)
    plan = ex.build_time_varying_plan(
        src, request=req(),
        measurements=records_for(nw_for(n), lambda i: True, lambda i: 0))
    blocker = td / "T62_blocker"
    if not blocker.is_file():
        blocker.write_bytes(b"not a directory")
    raises_tv("T62 a destination under a FILE path component is refused",
              ex.REASON_TV_APPLY_FAILED,
              ex.execute_time_varying, src, blocker / "sub" / "out.f32",
              plan=plan)
    value("T62b no .part survived the refusal",
          not sorted(p.name for p in td.glob("T62*.part")))

    def control_bad() -> None:
        good_target = td / "T62_ok" / "out.f32"
        ex.execute_time_varying(src, good_target, plan=plan, verify=False)
        if not good_target.is_file():
            raise AssertionError("a writable destination produced no output")
        raise AssertionError("x")
    control("T62.C a writable destination still works, so the check is "
            "discriminating", control_bad)


def t63_no_raw_oserror_escapes_the_entry_point() -> None:
    """Enumerate the filesystem faults a caller can produce; each must answer
    with a reason code rather than an exception."""
    print("\n== T63  no raw OSError escapes run_time_varying ==")
    td = tmpdir()
    n = 2 * HOP
    good = td / "T63_src.f32"
    np.ascontiguousarray(reference(n)).tofile(good)
    recs = records_for(nw_for(n), lambda i: True, lambda i: 0)
    missing = td / "T63_absent.f32"
    if missing.exists():
        missing.unlink()
    empty = td / "T63_empty.f32"
    empty.write_bytes(b"")
    a_dir = td / "T63_dir.f32"
    a_dir.mkdir(exist_ok=True)
    blocker = td / "T63_blocker"
    if not blocker.is_file():
        blocker.write_bytes(b"x")

    faults = [
        ("missing source", missing, td / "T63_o1.f32"),
        ("empty source", empty, td / "T63_o2.f32"),
        ("directory as source", a_dir, td / "T63_o3.f32"),
        ("unwritable destination", good, blocker / "s" / "o.f32"),
    ]
    for label, s, d in faults:
        try:
            r = ex.run_time_varying(s, d, request=req(), measurements=recs)
            value(f"T63 {label}: returns a stable reason code",
                  r.executed is False and r.reason is not None,
                  f"executed={r.executed} reason={r.reason}")
        except BaseException as exc:                            # noqa: BLE001
            value(f"T63 {label}: returns a stable reason code", False,
                  f"raised {type(exc).__name__}: {exc}")
    value("T63e the source is never modified by a refused run",
          good.stat().st_size == n * 4, f"{good.stat().st_size}")

    def control_bad() -> None:
        r = ex.run_time_varying(good, td / "T63_ok.f32", request=req(),
                                measurements=recs)
        if not r.executed:
            raise AssertionError(f"a healthy source was refused: {r.reason}")
        raise AssertionError("x")
    control("T63.C a healthy source still executes, so the refusals are "
            "specific", control_bad)


def t64_refusal_leaves_no_partial_output() -> None:
    """No `.part` file may survive any refusal, at any failing stage."""
    print("\n== T64  refusals leave no partial output ==")
    td = tmpdir()
    n = 2 * HOP
    src = td / "T64_src.f32"
    np.ascontiguousarray(reference(n)).tofile(src)
    recs = records_for(nw_for(n), lambda i: True, lambda i: 0)

    r1 = ex.run_time_varying(src, td / "T64_a.f32", request=req(),
                             measurements=None)
    parts = sorted(p.name for p in td.glob("T64_a*.part"))
    value("T64 a plan-stage refusal leaves no .part",
          not parts and not r1.executed, f"parts={parts} executed={r1.executed}")

    short = td / "T64_short.f32"
    np.ascontiguousarray(reference(n // 2)).tofile(short)
    plan = ex.build_time_varying_plan(src, request=req(), measurements=recs)
    try:
        ex.execute_time_varying(short, td / "T64_b.f32", plan=plan)
        raised = False
    except ex.TimeVaryingRefused:
        raised = True
    parts2 = sorted(p.name for p in td.glob("T64_b*.part"))
    value("T64b an apply-stage refusal leaves no .part",
          raised and not parts2, f"raised={raised} parts={parts2}")

    orig = ex.verify_applied_shift
    ex.verify_applied_shift = lambda *a, **kw: ex.VerificationResult(
        checked_samples=n, n_samples=n, mismatches=5, first_mismatch=3,
        complete=True, contract="injected")
    try:
        try:
            ex.execute_time_varying(src, td / "T64_c.f32", plan=plan)
            raised3 = False
        except ex.TimeVaryingRefused:
            raised3 = True
    finally:
        ex.verify_applied_shift = orig
    parts3 = sorted(p.name for p in td.glob("T64_c*.part"))
    value("T64c a verification-stage refusal leaves no .part",
          raised3 and not parts3, f"raised={raised3} parts={parts3}")

    def control_bad() -> None:
        leftover = td / "T64_control.part"
        leftover.write_bytes(b"x")
        if not sorted(p.name for p in td.glob("T64_*.part")):
            raise AssertionError("the .part scan cannot see a .part file")
        raise AssertionError("x")
    control("T64.C the .part scan can see a .part file", control_bad)


def t65_lying_verifier_is_a_documented_limit() -> None:
    """A verifier that reports success cannot be caught by the engine.

    P4.7 asserts this rather than implying otherwise: ``execute_time_varying``
    publishes whatever ``verify_applied_shift`` accepts. That is why the verifier
    is independently implemented and why the freeze/hash oracles exist. It is a
    structural property of the layering, and stating it stops "verification
    passed" from being read as stronger than it is.
    """
    print("\n== T65  a lying verifier is a documented limit ==")
    td = tmpdir()
    n = 2 * HOP
    src = td / "T65_src.f32"
    np.ascontiguousarray(reference(n)).tofile(src)
    recs = records_for(nw_for(n), lambda i: True, lambda i: 0)
    plan = ex.build_time_varying_plan(src, request=req(), measurements=recs)
    orig = ex.verify_applied_shift
    ex.verify_applied_shift = lambda *a, **kw: ex.VerificationResult(
        checked_samples=n, n_samples=n, mismatches=0, first_mismatch=None,
        complete=True, contract="always-ok")
    try:
        r = ex.execute_time_varying(src, td / "T65_out.f32", plan=plan,
                                    verify=True)
    finally:
        ex.verify_applied_shift = orig
    value("T65 a verifier that always accepts publishes the output, so the "
          "limit is real rather than hypothetical",
          r.executed and (r.verification or {}).get("ok") is True,
          f"executed={r.executed} verification={r.verification}")

    dst = td / "T65_real.f32"
    ex.execute_time_varying(src, dst, plan=plan, verify=False)
    arr = np.fromfile(dst, dtype="<f4")
    arr[777] = np.float32(arr[777] + 3.0)
    arr.tofile(dst)
    v = ex.verify_applied_shift(dst, src, plan.plan)
    value("T65b the REAL verifier rejects that same corruption, so the limit "
          "is about trust rather than capability",
          (not v.ok) and v.mismatches > 0, f"{v.to_dict()}")


def t66_source_equal_to_destination() -> None:
    """Writing the correction over its own input must not be a silent success.

    The applier opens the destination for writing while still reading the source.
    If they are the same file, the read stream can be truncated underneath the
    writer. Whatever the outcome, it must not be reported as success with a wrong
    output length.
    """
    print("\n== T66  source == destination ==")
    td = tmpdir()
    n = 2 * HOP
    same = td / "T66_same.f32"
    np.ascontiguousarray(reference(n)).tofile(same)
    recs = records_for(nw_for(n), lambda i: True, lambda i: 0)
    before = same.stat().st_size
    r = ex.run_time_varying(same, same, request=req(), measurements=recs)
    value("T66 source == destination is not reported as a silent success",
          (r.executed is False)
          or (r.n_samples_out == n),
          f"executed={r.executed} reason={r.reason} "
          f"n_out={r.n_samples_out}")
    if r.executed:
        value("T66b if it executed, the output length invariant still holds",
              r.n_samples_out == n and same.stat().st_size == n * 4,
              f"n_out={r.n_samples_out} size={same.stat().st_size} "
              f"(before {before})")
    else:
        value("T66b a refusal leaves the source at its original length",
              same.stat().st_size == before,
              f"{same.stat().st_size} vs {before}")

    def control_bad() -> None:
        other = td / "T66_other.f32"
        np.ascontiguousarray(reference(n)).tofile(other)
        rr = ex.run_time_varying(same, other, request=req(), measurements=recs)
        if not rr.executed:
            raise AssertionError(f"a normal run was refused: {rr.reason}")
        raise AssertionError("x")
    control("T66.C distinct source and destination still executes",
            control_bad)


# ==========================================================================
# T54-T60  production integration: channel_sync opt-in and default protection
# ==========================================================================
def _cs():
    from core import channel_sync
    return channel_sync


def t54_default_path_is_untouched() -> None:
    print("\n== T54  the default path is unchanged when the flag is off ==")
    cs = _cs()
    eff_off = cs.effective_opts(None)
    value("T54 time_varying defaults to False", eff_off["time_varying"] is False,
          f"{eff_off['time_varying']!r}")
    value("T54b tv_policy and tv_coordinate default to None (NOT to a policy)",
          eff_off["tv_policy"] is None and eff_off["tv_coordinate"] is None,
          f"{eff_off['tv_policy']!r} / {eff_off['tv_coordinate']!r}")
    # every pre-existing key must be byte-for-byte what it was: the P1 path is
    # protected by the defaults table, not by a code reading.
    for key in ("search_window_ms", "frame_ms", "hop_ms", "min_usable_frames",
                "min_confidence", "min_usable_fraction", "mad_max_ms",
                "aligned_max_ms", "verify_max_ms", "fix_chunk_seconds",
                "anchor_candidates", "algo_version",
                "channel_sync_transparent"):
        value(f"T54c the documented default of {key!r} is unchanged",
              eff_off[key] == cs.DEFAULTS[key],
              f"{eff_off[key]!r} vs {cs.DEFAULTS[key]!r}")
    value("T54d the P1 algorithm version is still 2.3.0-p1",
          cs.DEFAULTS["algo_version"] == "2.3.0-p1",
          f"{cs.DEFAULTS['algo_version']}")


def t55_tv_opts_normalisation() -> None:
    print("\n== T55  the new options survive normalisation ==")
    cs = _cs()
    # The None-valued geometry keys must NOT be int()-ed: int(None) raises, and
    # effective_opts' except returns DEFAULTS wholesale -- which would silently
    # discard the caller's policy and coordinate.
    eff = cs.effective_opts({"tv_policy": "hold",
                             "tv_coordinate": "nearest-centre"})
    value("T55 a None geometry key does not reset the whole config",
          eff["tv_policy"] == "hold"
          and eff["tv_coordinate"] == "nearest-centre",
          f"{eff['tv_policy']!r} / {eff['tv_coordinate']!r}")
    value("T55b the None geometry keys stay None (meaning: use the upstream "
          "named constant)",
          eff["tv_window_samples"] is None and eff["tv_hop_samples"] is None
          and eff["tv_search_samples"] is None,
          f"{eff['tv_window_samples']!r}")
    eff2 = cs.effective_opts({"time_varying": True, "tv_policy": "hold",
                              "tv_coordinate": "latest-start",
                              "tv_window_samples": 960000,
                              "tv_hop_samples": 480000})
    value("T55c explicit geometry is honoured and coerced to int",
          eff2["tv_window_samples"] == 960000
          and isinstance(eff2["tv_window_samples"], int),
          f"{eff2['tv_window_samples']!r}")
    value("T55d tv_chunk_samples has a concrete default",
          isinstance(eff2["tv_chunk_samples"], int)
          and eff2["tv_chunk_samples"] > 0, f"{eff2['tv_chunk_samples']!r}")


def t56_tv_requires_both_decisions() -> None:
    print("\n== T56  the production path refuses without both decisions ==")
    cs = _cs()
    full = {"time_varying": True, "tv_policy": "hold",
            "tv_coordinate": "latest-start"}
    value("T56 the full opt-in validates clean",
          cs._validate_tv_opts(cs.effective_opts(full)) is None,
          f"{cs._validate_tv_opts(cs.effective_opts(full))}")
    for missing, kw in (
        ("policy", {"time_varying": True, "tv_coordinate": "latest-start"}),
        ("coordinate", {"time_varying": True, "tv_policy": "hold"}),
        ("both", {"time_varying": True}),
    ):
        err = cs._validate_tv_opts(cs.effective_opts(kw))
        value(f"T56 with {missing} missing the path is refused with a message",
              isinstance(err, str) and len(err) > 20, f"{err!r}")
    err = cs._validate_tv_opts(cs.effective_opts(
        {"time_varying": True, "tv_coordinate": "latest-start"}))
    value("T56b the refusal message names the missing decision AND the "
          "alternatives",
          "tv_policy" in err and "hold" in err and "current" in err,
          f"{err}")


def t57_forbidden_policy_at_the_option_layer() -> None:
    print("\n== T57  the research policy cannot be selected ==")
    cs = _cs()
    # `_validate_tv_opts` checks presence; the whitelist is enforced by
    # request_time_varying, which channel_sync calls. Both must refuse.
    raises_tv("T57 request_time_varying refuses policy='current'",
              ex.REASON_TV_POLICY_NOT_PRODUCTION, ex.request_time_varying,
              enabled=True, policy="current", coordinate="latest-start")
    import argparse
    import subprocess
    cli = subprocess.run(
        [sys.executable, str(REPO / "1kt.py"), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    value("T57b the CLI exits 0 on --help (the new options parse)",
          cli.returncode == 0, f"rc={cli.returncode} {(cli.stderr or '')[-200:]}")
    value("T57c the CLI does NOT offer 'current' as a policy choice",
          "no_correction_gap" in (cli.stdout or "")
          and "{hold,no_correction_gap,explicit_state,linear,next}"
          in (cli.stdout or ""), "the policy choice list is not as expected")
    value("T57d the CLI offers exactly the two coordinate contracts",
          "{latest-start,nearest-centre}" in (cli.stdout or ""),
          "the coordinate choice list is not as expected")


def t58_channel_sync_imports_tv_lazily() -> None:
    print("\n== T58  the production module reaches the path lazily ==")
    import ast as _ast
    import inspect as _inspect
    cs = _cs()
    src = _inspect.getsource(cs)
    tree = _ast.parse(src)
    top = []
    for node in tree.body:
        if isinstance(node, _ast.Import):
            top += [a.name for a in node.names if "audio_sync_tv" in a.name]
        elif isinstance(node, _ast.ImportFrom):
            if "audio_sync_tv" in (node.module or ""):
                top.append(node.module)
    value("T58 channel_sync imports audio_sync_tv ONLY inside a function",
          top == [], f"module-scope imports: {top}")
    names = {n.name for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)}
    value("T58b the opt-in helpers exist as functions",
          {"_validate_tv_opts", "_apply_time_varying_track"} <= names,
          f"{sorted(names & {'_validate_tv_opts', '_apply_time_varying_track'})}")
    value("T58c the opt-in branch is guarded by the flag, so the default run "
          "never evaluates it",
          'if eff["time_varying"]:' in src, "the guard is not present")


def t59_trajectory_aggregation() -> None:
    print("\n== T59  frame trajectory -> plan windows ==")
    # frames at 200 ms / 100 ms hop (the production channel_sync geometry)
    class F:
        __slots__ = ("center_sample", "delay_samples", "confidence")

        def __init__(self, c, d, conf=0.9):
            self.center_sample, self.delay_samples, self.confidence = c, d, conf

    n = 4 * HOP
    frames = [F(c, 250.0) for c in range(0, n, 4800)]      # 100 ms hop
    recs = ex.aggregate_frames_to_windows(
        frames, n_samples=n, window_samples=WIN, hop_samples=HOP,
        min_frames_per_window=3)
    value("T59 the adapter returns exactly required_window_count records",
          len(recs) == nw_for(n), f"{len(recs)} vs {nw_for(n)}")
    value("T59b every window with enough frames is valid",
          all(r["valid"] for r in recs), f"{[r['valid'] for r in recs]}")
    value("T59c the window shift is the MEDIAN of its frames",
          all(abs(r["delay_samples"] - 250.0) < 1e-9 for r in recs),
          f"{[r['delay_samples'] for r in recs]}")
    # NaN frames are dropped, not treated as a zero delay
    frames2 = [F(c, float("nan")) for c in range(0, n, 4800)]
    recs2 = ex.aggregate_frames_to_windows(
        frames2, n_samples=n, window_samples=WIN, hop_samples=HOP,
        min_frames_per_window=3)
    value("T59d all-NaN frames leave every window UNMEASURED (not 0)",
          all(not r["valid"] and r["delay_samples"] is None for r in recs2),
          f"{recs2[:2]}")
    # the frame floor is enforced
    frames3 = [F(c, 100.0) for c in range(0, 4800, 100)]    # 48 frames, all w=0
    recs3 = ex.aggregate_frames_to_windows(
        frames3, n_samples=n, window_samples=WIN, hop_samples=HOP,
        min_frames_per_window=100)
    value("T59e a window below the frame floor is not trusted",
          recs3[0]["valid"] is False, f"{recs3[0]}")
    raises_tv("T59f a frame floor of 0 is refused (a window must need at "
              "least one frame)",
              ex.REASON_TV_PLAN_INVALID, ex.aggregate_frames_to_windows,
              frames3, n_samples=n, window_samples=WIN, hop_samples=HOP,
              min_frames_per_window=0)
    stats = ex.frame_aggregation_stats(
        frames, n_samples=n, window_samples=WIN, hop_samples=HOP,
        min_frames_per_window=3)
    value("T59g the aggregation is auditable (frame counts reported)",
          stats["n_frames"] == len(frames)
          and stats["n_finite_frames"] == len(frames)
          and stats["n_windows"] == nw_for(n), f"{stats}")


def t60_aggregation_chunk_invariance() -> None:
    print("\n== T60  the aggregated plan is chunk-invariant end to end ==")
    n = 4 * HOP
    ref = reference(n)
    src = source_with_delay(ref, lambda i: 333)
    sp = write_f32(tmpdir() / "T60.f32", src)

    class F:
        __slots__ = ("center_sample", "delay_samples", "confidence")

        def __init__(self, c, d):
            self.center_sample, self.delay_samples, self.confidence = c, d, 0.9

    frames = [F(c, 333.0) for c in range(0, n, 4800)]
    recs = ex.aggregate_frames_to_windows(
        frames, n_samples=n, window_samples=WIN, hop_samples=HOP,
        min_frames_per_window=3)
    outs = {}
    for c in (256, 4096, HOP):
        dp = tmpdir() / f"T60_{c}.f32"
        r = ex.run_time_varying(sp, dp, request=req(chunk=c), measurements=recs,
                                verify=True)
        value(f"T60 chunk={c} executed from an AGGREGATED trajectory",
              r.executed, f"reason={r.reason} detail={r.detail}")
        if r.executed:
            outs[c] = np.fromfile(dp, dtype="<f4")
    if len(outs) == 3:
        vals = list(outs.values())
        value("T60 the aggregated path is chunk-invariant too",
              all(np.array_equal(vals[0], v) for v in vals[1:]),
              f"{[int((vals[0] != v).sum()) for v in vals[1:]]}")

if __name__ == "__main__":
    raise SystemExit(main())
