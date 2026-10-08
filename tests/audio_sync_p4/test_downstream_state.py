"""P4.1 — D6 downstream-uncertainty regression suite.

Runs from TRACKED CODE ONLY (imports nothing from ``work/``). Tests the contract
stated in ``docs/audio-sync-p4/d1-uncertainty-contract.md`` against the **real
production objects** — not against copies.

Two kinds of case, and they are never mixed:

  * **current-behavior cases** — assert what the code does TODAY. These pass, and
    they are what makes the finding below evidence rather than an opinion.
  * **contract cases** — assert what the D1 contract REQUIRES. Where the code
    does not satisfy them yet, they are registered as ``XFAIL`` **with the reason
    and the owning decision**, and counted separately. When the D1 patch lands,
    they must flip to XPASS and be promoted — the suite says so out loud rather
    than being edited to fit.

The dangerous-loss case is the centre of the suite: a track that is 48.6 % policy
filled must not be reported as fully measured.

Run::

    python tests/audio_sync_p4/test_downstream_state.py

Exit 0 only if the suite ran, at least one case ran, and no case FAILED.
An empty run exits 2 — the P3 silent-pass rule.
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

RESULTS: list[dict] = []
COUNTS = {"pass": 0, "fail": 0, "xfail": 0, "xpass": 0, "control_ok": 0,
          "control_failed": 0}


def _rec(name: str, kind: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "kind": kind, "ok": bool(ok),
                    "detail": detail})
    COUNTS[kind] = COUNTS.get(kind, 0) + 1
    label = {"pass": "PASS", "fail": "FAIL", "xfail": "XFAIL",
             "xpass": "XPASS", "control_ok": "PASS", "control_failed": "FAIL"}[kind]
    print(f"  [{label}] {name}")
    if detail:
        print(f"          {detail}")


def value(name: str, ok: bool, detail: str = "") -> None:
    _rec(name, "pass" if ok else "fail", ok, detail)


def xfail(name: str, owner: str, reason: str) -> None:
    """A contract case the code does not satisfy yet. NOT a pass.

    ``owner`` names the decision that must land before this can flip, so the
    expectation cannot be quietly dropped.
    """
    _rec(name, "xfail", False, f"OWNER={owner}: {reason}")


def xfail_check(name: str, ok_now: bool, owner: str, reason: str) -> None:
    """Register a contract case, and detect the moment it starts passing.

    If the underlying behavior already changed, this reports ``XPASS`` — which is
    a *failure* of the suite's bookkeeping, not a success, because it means the
    XFAIL list is stale and must be promoted deliberately.
    """
    if ok_now:
        _rec(name, "xpass", True,
             f"OWNER={owner}: the contract now holds — PROMOTE this case to a "
             f"real assertion and update d1-uncertainty-contract.md")
    else:
        _rec(name, "xfail", False, f"OWNER={owner}: {reason}")


def control(name: str, bad_fn) -> None:
    try:
        bad_fn()
    except BaseException:                              # noqa: BLE001
        _rec(name, "control_ok", True)
        return
    _rec(name, "control_failed", False,
         "the check did NOT fail on a deliberately broken input")


# ==========================================================================
# 1. producer: the state exists and is exact
# ==========================================================================
def t1_producer() -> None:
    print("\n== T1  producer: state exists, exact, and honest ==")
    from core import audio_sync_tv as tv

    mask = np.array([1, 1, 0, 0, 1], dtype=bool)
    shifts = np.array([100, 100, 0, 0, 120], dtype=np.int64)
    plan = tv.build_correction_plan(
        resolved=mask, shifts=shifts, n_samples=5 * 480_000,
        window_samples=1_440_000, hop_samples=480_000, policy="hold")

    value("T1.1 all three states are produced",
          set(map(str, plan.states)) == {"TRUSTED", "UNRESOLVED_GAP", "REENTRY"},
          f"states={list(map(str, plan.states))}")
    value("T1.2 REENTRY is distinct from TRUSTED",
          str(plan.states[4]) == "REENTRY" and str(plan.states[0]) == "TRUSTED")
    value("T1.3 no window claims a measurement it lacks",
          plan.overclaimed_windows() == 0)

    a = tv.CorrectionDecision(100, tv.GapState.TRUSTED)
    b = tv.CorrectionDecision(100, tv.GapState.UNRESOLVED_GAP)
    value("T1.4 same shift, different state, distinguishable",
          a.shift_samples == b.shift_samples and a.resolved != b.resolved,
          f"shift {a.shift_samples}=={b.shift_samples}, "
          f"resolved {a.resolved} vs {b.resolved}")

    def break_it() -> None:
        assert a.resolved == b.resolved, "x"
    control("T1.C the resolved-distinction check can fail", break_it)


# ==========================================================================
# 2. production model: what it can and cannot represent TODAY
# ==========================================================================
def t2_model() -> None:
    print("\n== T2  production model: current capability ==")
    from core.audio_models import AudioSyncResult, SyncStatus

    members = [m.value for m in SyncStatus]
    value("T2.1 SyncStatus is a closed vocabulary of 9 members",
          len(members) == 9, f"{members}")
    value("T2.2 no member means 'measured but not trusted'",
          not any(k in m for m in members
                  for k in ("unresolved", "gap", "partial", "reentry")),
          "this is the CURRENT state, not the contract")

    r = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=615)
    value("T2.3 SUCCESS + a number is representable and reads as applied",
          r.applied and r.offset_samples == 615,
          "AudioSyncResult(status=success, offset_samples=615).applied=True")
    value("T2.4 AudioSyncResult has no coverage field today",
          not any(hasattr(r, k) for k in
                  ("coverage", "measured_windows", "total_windows",
                   "measurement_coverage", "states", "resolved")),
          f"fields={list(r.__dict__)}")

    def break_it() -> None:
        assert hasattr(r, "measured_windows"), "x"
    control("T2.C the capability probe can fail", break_it)


# ==========================================================================
# 3. apply gate: no IMPLICIT_TRUST
# ==========================================================================
def t3_gate() -> None:
    print("\n== T3  apply gate: a number without a status is refused ==")
    from core.audio_models import AudioSyncResult, SyncStatus
    from core.audio_timeline import _effective_offset, SyncApplication

    ok = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=-600)
    off, raw = _effective_offset(ok, SyncApplication.APPLY)
    value("T3.1 a success status applies its offset", off == -600,
          f"offset={off}")

    for st in (SyncStatus.FAILED, SyncStatus.LOW_CONFIDENCE,
               SyncStatus.NON_CONSTANT, SyncStatus.OUT_OF_RANGE,
               SyncStatus.RECHECK_FAILED, SyncStatus.NOT_PROCESSED,
               SyncStatus.ALREADY_ALIGNED):
        bad = AudioSyncResult(status=st, offset_samples=999)
        o, _ = _effective_offset(bad, SyncApplication.APPLY)
        value(f"T3.2 status={st.value} + a number is REFUSED (no implicit trust)",
              o == 0, f"offset_samples=999 -> applied {o}")
        if o != 0:
            break

    off_none, _ = _effective_offset(ok, SyncApplication.NONE)
    value("T3.3 SyncApplication.NONE forces zero (no double shift)",
          off_none == 0)

    def break_it() -> None:
        assert off == 999, "x"
    control("T3.C the gate assertion can fail", break_it)


# ==========================================================================
# 4. aggregation: conservative, and its limit
# ==========================================================================
def t4_aggregation() -> None:
    print("\n== T4  track aggregation ==")
    from core.audio_models import SyncStatus
    import inspect
    from core.audio_models import AudioTrack
    src = inspect.getsource(AudioTrack.refresh_sync)
    value("T4.1 SUCCESS requires ALL channels to succeed",
          "all(s is SyncStatus.SUCCESS" in src)
    value("T4.2 any NOT_PROCESSED channel makes the track NOT_PROCESSED",
          "any(s is SyncStatus.NOT_PROCESSED" in src)
    value("T4.3 the fallback takes a NON-success member (conservative)",
          "if s is not SyncStatus.SUCCESS" in src)


# ==========================================================================
# 5. THE DANGEROUS-LOSS CASE
# ==========================================================================
def t5_dangerous_loss() -> None:
    print("\n== T5  dangerous loss: a 48.6 % policy-filled track ==")
    from core.audio_models import AudioSyncResult, SyncStatus, ChannelSyncReport
    from core.audio_timeline import _effective_offset, SyncApplication

    # the split P3.11 recorded for C1305 ch3->ch4
    windows, unresolved, trusted, reentry = 481, 234, 193, 54
    coverage = (windows - unresolved) / windows

    row = {
        "stream": 3, "decision": "fixed", "reason": None,
        "shift_samples": 296, "delay_samples": 296.476, "delay_ms": 6.1766,
        "confidence": 0.62, "constant": True, "warnings": [],
        "usable_frames": 592, "polarity": 1, "drift_ppm": 2.0,
        "fractional_part_samples": 0.476, "storage_dtype": "f32",
        "rms_dbfs": -18.0,
    }
    rep = ChannelSyncReport({"status": "applied", "detail": "",
                             "anchor_stream": 2, "channels": [row],
                             "algo_version": "2.3.0-p1"})
    res = rep.resolve(row)

    value("T5.1 a partially-measured row resolves to SUCCESS",
          res.status is SyncStatus.SUCCESS,
          f"status={res.status.value!r}")
    value("T5.2 ...and reads as applied",
          bool(res.applied), f"applied={res.applied}")

    off, _ = _effective_offset(res, SyncApplication.APPLY)
    value("T5.3 the whole track is shifted (correct — applying is not the defect)",
          off == 296, f"offset={off} for a track that is {coverage:.1%} measured")

    # THE FINDING
    discoverable = (getattr(res, "measurement_coverage", None) is not None
                    or getattr(res, "measured_windows", None) is not None)
    xfail_check(
        "T5.4 CONTRACT: a consumer can discover that 234/481 windows "
        "(48.6 %) were policy-filled",
        discoverable, owner="D1",
        reason="AudioSyncResult exposes no coverage field, so a consumer "
               "reading status/applied/offset_samples cannot tell this track "
               "from a fully measured one. This is the D6 finding. It flips "
               "when the D1 patch adds measured_windows/total_windows.")

    xfail_check(
        "T5.5 CONTRACT: a success with unresolved windows is nameable "
        "distinctly from a full success",
        any(k in m.value for m in SyncStatus
            for k in ("partial", "unresolved")),
        owner="D1",
        reason="SyncStatus has no member for 'succeeded, partially measured'; "
               "AudioTrack.refresh_sync therefore has no member to aggregate "
               "and must report SUCCESS.")

    # the counter-check that keeps the finding honest
    value("T5.6 the loss is LATENT: today's production cannot produce this input",
          "delay_samples=e.delay_samples" in _channel_sync_source(),
          "core/channel_sync.py applies ONE scalar shift per track, so a "
          "partially-measured track cannot occur until the time-varying path "
          "is wired in")

    def break_it() -> None:
        assert discoverable, "x"
    control("T5.C the coverage-discovery probe can fail", break_it)


def _channel_sync_source() -> str:
    import inspect
    from core import channel_sync
    return inspect.getsource(channel_sync)


# ==========================================================================
# 6. state-neutral layers must STAY state-neutral (brief section 38 R-6)
# ==========================================================================
def t6_neutral_layers() -> None:
    print("\n== T6  state-neutral layers must stay state-free ==")
    import inspect
    import dataclasses

    from core.output_compose import AudioInputRef
    f = [x.name for x in dataclasses.fields(AudioInputRef)]
    value("T6.1 AudioInputRef carries no sync/epistemic state",
          not any(k in x for x in f for k in ("sync", "state", "resolved",
                                              "coverage")),
          f"fields={f}")

    from core import sync_fix
    sig = inspect.signature(sync_fix.shift_stream)
    value("T6.2 shift_stream still takes a SCALAR delay_samples",
          "delay_samples" in sig.parameters
          and sig.parameters["delay_samples"].kind
          is inspect.Parameter.KEYWORD_ONLY)

    from core.channel_sync import DEFAULTS
    value("T6.3 the P1 algorithm version is unchanged",
          DEFAULTS["algo_version"] == "2.3.0-p1", f"{DEFAULTS['algo_version']}")

    from core import audio_sync_tv
    src = inspect.getsource(audio_sync_tv)
    value("T6.4 the P4 module still imports no production model",
          "audio_models" not in src and "output_compose" not in src,
          "no import of AudioSyncResult / SyncStatus / AudioInputRef")

    from core import audio_models
    value("T6.5 the production model does not import the P4 module",
          "audio_sync_tv" not in inspect.getsource(audio_models))


# ==========================================================================
# 7. serialization where it applies
# ==========================================================================
def t7_serialization() -> None:
    print("\n== T7  serialization round-trip, where it applies ==")
    from core.audio_models import AudioSyncResult, SyncStatus
    import json as _json

    r = AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=615,
                        offset_ms=12.8125, quality=0.62,
                        algo_version="2.3.0-p1", source="channel_sync_report")
    d = r.to_dict()
    back = AudioSyncResult.from_dict(_json.loads(_json.dumps(d)))
    value("T7.1 status survives object -> dict -> JSON -> object",
          back.status is r.status, f"{r.status.value!r} -> {back.status.value!r}")
    value("T7.2 offset_samples survives exactly",
          back.offset_samples == 615, f"{back.offset_samples!r}")

    from core.audio_timeline import ChannelTimeline
    ct = ChannelTimeline(channel_id="ch4", source_id="s", stream_index=3,
                         channel_index=3, offset_samples=296,
                         raw_offset_samples=296.476, sync_status="success",
                         sync_application="apply")
    ctd = ct.to_dict()
    value("T7.3 ChannelTimeline serialises its sync status",
          ctd.get("sync_status") == "success", f"{ctd.get('sync_status')!r}")
    # `sync_application` is emitted ONLY when it differs from the default
    # (audio_timeline.py:578). Asserting its presence for the default value
    # would be asserting the wrong contract — so both branches are checked.
    value("T7.4 sync_application is omitted when it is the default 'apply'",
          "sync_application" not in ctd,
          "to_dict() omits it for the default; asserting presence here would "
          "encode a contract the code does not have")
    ct_none = ChannelTimeline(channel_id="ch4", source_id="s", stream_index=3,
                              channel_index=3, offset_samples=0,
                              sync_status="already_aligned",
                              sync_application="none")
    cnd = ct_none.to_dict()
    value("T7.4b sync_application IS emitted when it is non-default 'none'",
          cnd.get("sync_application") == "none",
          f"{cnd.get('sync_application')!r}")

    from core import audio_sync_tv
    value("T7.5 the P4 plan deliberately has NO serializer (legal design)",
          not hasattr(audio_sync_tv.CorrectionPlan, "to_dict"),
          "STATE NOT REQUIRED PAST LAYER: the producer. A planning-time object "
          "consumed in-process needs none; adding one to satisfy a test would "
          "be the failure mode P4.1 section 24 forbids.")

    def break_it() -> None:
        assert d.get("status") == "definitely_not_a_status", "x"
    control("T7.C the round-trip assertion can fail", break_it)


def t8_live_drop_points() -> None:
    """The LIVE defects: coverage is computed, then discarded.

    Found by adversarial review of P4.1's first conclusion, independently
    re-verified, and recorded here so it cannot be lost with `work/`.

    Scope note: these are **defects in shipped code**, not part of the D6
    contract. They are asserted so that a future fix flips them, and so nobody
    has to rediscover them. They are NOT fixed here.
    """
    print("\n== T8  live drop points: coverage computed, then dropped ==")
    import inspect

    from core import channel_sync as cs
    src = inspect.getsource(cs)

    # 1. coverage is computed from a frame count and a total
    value("T8.1 channel_sync computes a measurement coverage fraction",
          "coverage = " in src and "min_usable_fraction" in src,
          "coverage = e.usable_frames / max(1, len(e.frames)) at ~:706")

    # 2. the coverage branch adopts a wide estimate on frames+confidence only
    value("T8.2 the adopted wide estimate is NOT re-checked for coverage",
          "ew.usable_frames >= eff[\"min_usable_frames\"]" in src
          and "coverage" not in src.split("ew.usable_frames >=")[1][:200],
          "the branch that fires BECAUSE coverage < 0.6 accepts on "
          "usable_frames >= 5 and confidence >= 0.3, and never re-applies the "
          "fraction gate to what it adopts")

    # 3. coverage reaches no report row
    value("T8.3 coverage is written into no report row",
          '"coverage"' not in src and "'coverage'" not in src,
          "plain_row (~:490) and est_row (~:516) have no coverage key, so a "
          "consumer cannot recover the fraction from the report")

    # 4. min_audio_seconds / frame geometry make a low usable_frames count
    #    impossible under a re-checked gate
    from core.channel_sync import DEFAULTS
    windows_min = (DEFAULTS["min_audio_seconds"] * 1000.0) / DEFAULTS["hop_ms"]
    frames_needed = windows_min * DEFAULTS["min_usable_fraction"]
    value("T8.4 the geometry makes the shipped 5-frame track impossible under "
          "the gate",
          DEFAULTS["min_usable_fraction"] == 0.6 and frames_needed > 5,
          f"min_audio_seconds={DEFAULTS['min_audio_seconds']} / hop_ms="
          f"{DEFAULTS['hop_ms']} -> >= {windows_min:.0f} windows -> a re-checked "
          f"0.6 gate needs >= {frames_needed:.0f} usable frames. The shipped "
          f"baseline contains a fixed/applied track with usable_frames = 5.")

    # 5. the real-corpus fixture, if present
    csv_path = (REPO / "tests" / "fixtures" / "channel_sync"
                / "a7m5_real_137_baseline.csv")
    if csv_path.is_file():
        import csv as _csv
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(_csv.DictReader(fh))
        fixed = [r for r in rows if (r.get("decision") or "") == "fixed"]
        uf = []
        for r in fixed:
            try:
                uf.append(int(float(r.get("usable_frames") or 0)))
            except (TypeError, ValueError):
                pass
        low = [x for x in uf if x < int(frames_needed)]
        value("T8.5 the shipped baseline contains partly-evidenced applied "
              "tracks",
              bool(fixed) and bool(low),
              f"{len(fixed)} fixed rows, usable_frames={sorted(uf)}; "
              f"{len(low)} below the {frames_needed:.0f} a re-checked gate would "
              f"require")
        value("T8.6 every one of those rows reports reason = (empty)",
              all(not (r.get("reason") or "").strip() for r in fixed),
              "so the fallback is indistinguishable from a fully-evidenced fix")
    else:
        print("  [SKIP] the baseline CSV is absent — a skip is NOT a pass")

    # 6. the P2 path computes a denominator and never uses it
    from core import audio_sync as asy
    asrc = inspect.getsource(asy)
    value("T8.7 the P2 cross-file path computes total_frames",
          "total_frames = len(pair.frames)" in asrc,
          "core/audio_sync.py:967")
    value("T8.8 ...and does NOT copy it into the sync result",
          "total_frames=self.total_frames" not in asrc
          and "total_frames=out.total_frames" not in asrc,
          "the AudioSyncResult construction copies usable_frames only, so the "
          "denominator dies one line after it becomes available")
    value("T8.9 ...and the P2 path has no coverage-fraction gate at all",
          "min_usable_fraction" not in asrc,
          "it gates on absolute counts (usable_frames < 5, confidence < 0.3) "
          "only, then sets SUCCESS")

    value("T8.10 nothing in the repository READS the P2 denominator",
          _no_reader_of_p2_total_frames(),
          "grep shows total_frames appears in core/audio_sync.py only as a "
          "field, a serialiser and an assignment")

    def break_it() -> None:
        assert "min_usable_fraction" not in src, "x"
    control("T8.C the coverage-computation probe can fail", break_it)


def _no_reader_of_p2_total_frames() -> bool:
    """True if no production file reads PairSyncEstimate.total_frames.

    Only ``core/audio_sync.py`` itself names the attribute; ``total_frames``
    elsewhere in the repo belongs to *video* frame counts and is unrelated.
    """
    import re
    hits = []
    for p in sorted((REPO / "core").glob("*.py")) + \
            sorted((REPO / "production").glob("*.py")):
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"[.\[]\s*total_frames", txt):
            line = txt[:m.start()].count("\n") + 1
            hits.append(f"{p.name}:{line}")
    # exclude the file that merely holds/serialises/assigns its own field
    return not [h for h in hits if not h.startswith("audio_sync.py:")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("=" * 78)
    print("P4.1 — D6 downstream-uncertainty regression")
    print("=" * 78)

    t1_producer()
    t2_model()
    t3_gate()
    t4_aggregation()
    t5_dangerous_loss()
    t6_neutral_layers()
    t7_serialization()
    t8_live_drop_points()

    n_cases = len(RESULTS)
    print("\n" + "=" * 78)
    print(f"cases            : {n_cases}")
    print(f"PASS             : {COUNTS['pass']}")
    print(f"FAIL             : {COUNTS['fail']}")
    print(f"XFAIL (contract) : {COUNTS['xfail']}   <- D6 finding, owned by D1")
    print(f"XPASS (stale)    : {COUNTS['xpass']}   <- must be promoted")
    print(f"CONTROL_OK       : {COUNTS['control_ok']}")
    print(f"CONTROL_FAILED   : {COUNTS['control_failed']}")
    print("=" * 78)

    if n_cases == 0:
        print("FAIL: no case ran — an empty run is not a pass", file=sys.stderr)
        return 2
    if COUNTS["pass"] == 0:
        print("FAIL: no current-behavior case ran", file=sys.stderr)
        return 2
    if COUNTS["control_ok"] == 0:
        print("FAIL: no control was demonstrated discriminating",
              file=sys.stderr)
        return 2

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"cases": n_cases, "counts": COUNTS, "results": RESULTS},
            indent=1), encoding="utf-8")
        print(f"json -> {args.json}")

    # XPASS means the XFAIL list is stale -> the suite must be updated, not
    # silently allowed to pass.
    bad = COUNTS["fail"] + COUNTS["control_failed"] + COUNTS["xpass"]
    if bad:
        print(f"\nRESULT: FAIL ({bad} case(s))")
        for r in RESULTS:
            if (not r["ok"] and r["kind"] == "fail") or r["kind"] in (
                    "control_failed", "xpass"):
                print(f"  {r['kind'].upper()} {r['name']}")
                if r["detail"]:
                    print(f"       {r['detail']}")
        return 1

    print(f"\nRESULT: PASS — {COUNTS['pass']} behaviors hold, "
          f"{COUNTS['xfail']} contract gap(s) OPEN and owned by D1, "
          f"{COUNTS['control_ok']} controls discriminating")
    print("NOTE: XFAIL cases are NOT passes. They are the D6 finding, held "
          "open so they cannot be forgotten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
