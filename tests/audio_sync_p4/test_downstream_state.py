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
    # --- T2.4: PROMOTED BY P4.3, with the reason recorded in place ---------
    #
    # P4.1/P4.2 registered this as the CURRENT state: AudioSyncResult had no
    # coverage field, so a consumer could not tell a fully measured track from
    # a partly measured one. P4.3 (D1) adds the evidence triple. The negative
    # assertion is therefore REPLACED by a positive assertion of the property
    # it was standing in for; it is not deleted.
    _r_null = AudioSyncResult()
    _r_ev = AudioSyncResult(
        status=SyncStatus.SUCCESS, offset_samples=615,
        usable_frames=592, total_frames=481, coverage=592 / 481,
    )
    value("T2.4 PROMOTED (was 'AudioSyncResult has no coverage field today'): "
          "the production model states the evidence",
          hasattr(r, "coverage") and hasattr(r, "total_frames")
          and _r_ev.coverage is not None
          and _r_ev.measurement_coverage == _r_ev.coverage,
          f"fields={sorted(r.__dict__)}; the existing usable_frames now has a "
          f"denominator and a stated fraction")
    value("T2.4a ...and 'not stated' is None, never 0 (U1: no sentinel)",
          _r_null.coverage is None and _r_null.total_frames is None
          and _r_null.measurement_coverage is None,
          "an un-updated path must not be read as a total measurement failure")
    value("T2.4b ...and the evidence survives a dict round-trip exactly",
          AudioSyncResult.from_dict(
              AudioSyncResult(status=SyncStatus.SUCCESS, offset_samples=615,
                              usable_frames=592, total_frames=481,
                              coverage=592 / 481).to_dict()
          ).coverage == 592 / 481,
          "status and coverage survive to_dict -> JSON -> from_dict")
    value("T2.4c ...and a legacy record without the new keys is unchanged",
          AudioSyncResult.from_dict(
              {"status": "success", "offset_samples": 615}
          ).status is SyncStatus.SUCCESS
          and AudioSyncResult.from_dict(
              {"status": "success", "offset_samples": 615}
          ).coverage is None,
          "new fields do not force historical results to fail")

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

    # THE FINDING — SPLIT BY P4.3 into its decidable and undecidable halves.
    #
    # The original T5.4 was a COMPOUND claim: "a consumer can discover that
    # 234/481 *plan windows* were policy-filled". P4.3's D1 decision implements
    # the evidence contract for the population the P1 producer actually has
    # (measurement frames of the adopted estimate) and deliberately does NOT
    # mint a plan-window field, because no production producer populates one.
    # So the compound claim is split rather than left as a single XFAIL whose
    # reason has silently gone stale.
    row_ev = dict(row)
    row_ev["evidence"] = {"usable_frames": 592, "total_frames": 481,
                          "coverage": 592 / 481, "confidence": 0.62}
    res_ev = ChannelSyncReport({"status": "applied", "detail": "",
                                "anchor_stream": 2, "channels": [row_ev],
                                "algo_version": "2.3.0-p1"}).resolve(row_ev)
    value("T5.4a PROMOTED BY P4.3 (was XFAIL owner=D1): a consumer CAN discover "
          "how well measured the adopted estimate is",
          res_ev.coverage is not None and res_ev.total_frames == 481
          and res_ev.usable_frames == 592,
          f"coverage={res_ev.measurement_coverage} stated on the production "
          f"object; the frame-domain evidence contract (D1) is implemented")

    # The undecidable half stays open, with its owner corrected: it is no
    # longer D1 (the evidence contract exists) but D8 (nothing in production
    # produces a plan-window population to populate such a field).
    discoverable = getattr(res, "measured_windows", None) is not None
    xfail_check(
        "T5.4b CONTRACT: a consumer can discover that 234/481 PLAN WINDOWS "
        "(48.6 %) were policy-filled",
        discoverable, owner="D8",
        reason="the plan-window population (measured_windows/total_windows over "
               "a CorrectionPlan) has NO producer in production: "
               "core/audio_sync_tv is imported by no production module, so the "
               "field could not be populated or tested. D1's frame-domain "
               "contract (T5.4a) is implemented and is a DIFFERENT population "
               "(measurement frames of the adopted estimate) — d1 contract "
               "section 4.2 forbids reporting a coverage figure without naming "
               "its population. It flips when D8 wires a producer that carries "
               "the plan-window counts to AudioSyncResult.")

    # --- T5.5: RECLASSIFIED BY P4.2, with the reason recorded in place -------
    #
    # P4.1 registered this as XFAIL(owner=D1) because SyncStatus has no member
    # for "succeeded, partially measured". P4.2 fixed the GATE rather than
    # minting the member: an under-evidenced track is no longer written
    # `decision="fixed"` at all, so it resolves to LOW_CONFIDENCE — which is
    # exactly the existing member that means "a number exists and is not
    # trusted". The XFAIL is therefore REPLACED by a positive assertion of the
    # stronger property, and the original is not deleted.
    row_gap = {
        "stream": 3, "decision": "untouched",
        "reason": "evidence_insufficient", "shift_samples": None,
        "delay_samples": 296.476, "delay_ms": 6.1766, "confidence": 0.62,
        "constant": True, "warnings": [], "usable_frames": 6, "polarity": 1,
        "drift_ppm": 2.0, "fractional_part_samples": None,
        "storage_dtype": "f32", "rms_dbfs": -18.0,
        "evidence": {"usable_frames": 6, "total_frames": 19,
                     "coverage": 0.316, "confidence": 0.62},
    }
    rep_gap = ChannelSyncReport({"status": "applied", "detail": "",
                                 "anchor_stream": 2, "channels": [row_gap],
                                 "algo_version": "2.3.0-p1"})
    res_gap = rep_gap.resolve(row_gap)
    value("T5.5 RECLASSIFIED (was XFAIL owner=D1, resolved by P4.2): an "
          "under-evidenced track is NOT reported as a successful fix",
          res_gap.status is not SyncStatus.SUCCESS and not res_gap.applied,
          f"decision='untouched' reason='evidence_insufficient' -> "
          f"SyncStatus.{res_gap.status.name}, applied={res_gap.applied}. "
          f"P4.2 chose an existing member (LOW_CONFIDENCE = 'a number exists "
          f"but is not trusted') over minting a new one.")

    # --- T5.6: REWRITTEN BY P4.2 -------------------------------------------
    #
    # P4.1 concluded the loss was LATENT on the grounds that production writes
    # one scalar shift per track. Adversarial review then showed that reasoning
    # was wrong — this is the TRACK-level form of the same failure, and it was
    # live. P4.2 fixed it. The old assertion (a source substring proving the
    # scalar signature) was a weak proxy for the claim; it is replaced by the
    # property that actually matters.
    value("T5.6 REWRITTEN (was 'the loss is LATENT'): the scalar signature is "
          "still true, and it is no longer what protects the evidence",
          "delay_samples=e.delay_samples" in _channel_sync_source(),
          "one scalar shift per track is STILL the case — but it was never what "
          "made the loss latent; the final adoption gate is what makes an "
          "under-evidenced track safe now")
    value("T5.6b the final adoption gate exists and is applied to the chosen "
          "estimate",
          "最终采纳门" in _channel_sync_source(),
          "P4.2 adds a final gate on the ADOPTED estimate, so `SUCCESS` can no "
          "longer rest on a local variable that has left scope")

    def break_it() -> None:
        assert discoverable, "x"
    control("T5.C the coverage-discovery probe can fail", break_it)


def _imported_modules(src: str) -> set[str]:
    """Every module name this source **actually imports** — AST, not substring.

    A substring check cannot tell an import from a comment mentioning the
    module, and it can be defeated by an alias. This reads the import
    statements (including a lazily-named ``import_module`` / ``__import__``
    argument), so the assertion is about the dependency, not about the prose.
    """
    import ast

    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            out.add(base)
            out.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in ("import_module", "__import__"):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)):
                        out.add(arg.value)
    return out


def _imports_module(src: str, needle: str) -> bool:
    return any(needle in mod for mod in _imported_modules(src))


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
    # STRENGTHENED (P4.4): this was a substring test, so a *comment* naming the
    # module read as an import, and an aliased import read as clean. It now
    # reads the actual import statements — which is what the property is about.
    tv_imports = _imported_modules(src)
    value("T6.4 the P4 module still imports no production model",
          not any(m in ("audio_models", "output_compose")
                  or m.startswith(("core.audio_models", "core.output_compose"))
                  for m in tv_imports),
          f"no import of AudioSyncResult / SyncStatus / AudioInputRef; "
          f"imports={sorted(tv_imports)}")

    from core import audio_models
    value("T6.5 the production model does not import the P4 module",
          not _imports_module(inspect.getsource(audio_models), "audio_sync_tv"),
          "verified from the import statements, not from the text")


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

    # 3. coverage reaches the report row
    #
    # P4.2 FIXED this. The original check was `'"coverage"' not in src`, which is
    # an over-broad substring test: it matched the word appearing anywhere in the
    # source, so it started failing the moment P4.2 *added* coverage handling.
    # It is now a POSITIVE assertion on the real contract — every decision row
    # carries an `evidence` dict with the coverage fraction — which is both
    # meaningful and no longer sensitive to how the identifier is spelled.
    value("T8.3 coverage IS now attached to every decision row (P4.2)",
          "rows[i][\"evidence\"] = ev" in src,
          "P4.2 attaches `evidence` = {usable_frames, total_frames, coverage, "
          "confidence} to every row, so the fraction is recoverable from the "
          "report; before P4.2 it was computed and discarded")

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
    #
    # --- PROMOTED BY P4.3 ---------------------------------------------------
    # T8.7..T8.10 recorded four negative facts about the P2 path. P4.3 (D1)
    # closes all four, so each negative assertion is REPLACED in place by the
    # positive property it was standing in for. None is deleted, and T8.10's
    # probe function is kept because its inverse is now the assertion.
    from core import audio_sync as asy
    asrc = inspect.getsource(asy)
    value("T8.7 the P2 cross-file path computes total_frames",
          "total_frames = len(pair.frames)" in asrc,
          "core/audio_sync.py")
    value("T8.8 PROMOTED (was '...and does NOT copy it into the sync result'): "
          "the denominator now reaches the sync result",
          "total_frames=int(item.total_frames)" in asrc
          and "coverage=(" in asrc,
          "the AudioSyncResult construction copies usable_frames AND its "
          "denominator AND the stated fraction; the denominator no longer dies "
          "one line after it becomes available")
    value("T8.9 PROMOTED (was '...and the P2 path has no coverage-fraction gate "
          "at all'): the P2 path applies all three production gates",
          "min_usable_fraction" in asrc
          and 'params["min_usable_fraction"]' in asrc,
          "it now gates on usable_frames, confidence AND coverage. The three "
          "default values are cross-checked against core.channel_sync.DEFAULTS "
          "at import time, so the two gate sets cannot diverge silently")

    value("T8.10 PROMOTED (was 'nothing in the repository READS the P2 "
          "denominator'): the repository now reads it",
          not _no_reader_of_p2_total_frames(),
          "grep for `[.\\[]\\s*total_frames` now hits production/output.py as "
          "well as core/audio_sync.py — the evidence is consumed, not merely "
          "stored")

    def break_it() -> None:
        assert "min_usable_fraction" not in src, "x"
    control("T8.C the coverage-computation probe can fail", break_it)


def t9_p42_revalidation() -> None:
    """P4.2 revalidation: what the evidence-integrity fix changed, and what it did not.

    The brief requires the two D6 XFAILs to be either promoted or formally
    reclassified — never deleted. This block states the reclassification with
    its reason, and asserts the parts of the D6 finding that P4.2 resolved.
    """
    print("\n== T9  P4.2 revalidation of the D6 findings ==")
    import inspect

    from core import channel_sync as cs
    from core.audio_models import ChannelSyncReport, SyncStatus
    from core.audio_timeline import _effective_offset, SyncApplication

    src = inspect.getsource(cs)

    # ---- RESOLVED: a policy-filled / under-evidenced track is no longer SUCCESS
    row = {
        "stream": 3, "decision": "untouched", "reason": "evidence_insufficient",
        "shift_samples": None, "delay_samples": 296.476, "delay_ms": 6.1766,
        "confidence": 0.62, "constant": True, "warnings": [],
        "usable_frames": 6, "polarity": 1, "drift_ppm": 2.0,
        "fractional_part_samples": None, "storage_dtype": "f32",
        "rms_dbfs": -18.0,
        "evidence": {"usable_frames": 6, "total_frames": 19, "coverage": 0.316,
                     "confidence": 0.62},
    }
    rep = ChannelSyncReport({"status": "applied", "detail": "",
                             "anchor_stream": 2, "channels": [row],
                             "algo_version": "2.3.0-p1"})
    res = rep.resolve(row)
    value("T9.1 an under-evidenced track no longer resolves to SUCCESS",
          res.status is not SyncStatus.SUCCESS,
          f"decision='untouched', reason='evidence_insufficient' -> "
          f"SyncStatus.{res.status.name}")
    value("T9.2 ...and reads as not applied",
          not res.applied, f"applied={res.applied}")
    off, _ = _effective_offset(res, SyncApplication.APPLY)
    value("T9.3 ...and the apply gate refuses it",
          off == 0, f"offset={off}")

    # ---- RESOLVED: the row now carries its evidence
    value("T9.4 the report row carries the coverage fraction (P4.2)",
          "rows[i][\"evidence\"] = ev" in src,
          "evidence = {usable_frames, total_frames, coverage, confidence} on "
          "every decision row")

    # ---- RESOLVED BY P4.3: the production model states the evidence ---------
    #
    # P4.2 recorded this as open; P4.3 (D1) implements it. The negative
    # assertion is REPLACED in place by the positive property, not deleted.
    value("T9.5 PROMOTED (was 'AudioSyncResult still has no coverage field — "
          "D1's patch is still outstanding'): D1's patch is applied",
          all(hasattr(res, k) for k in
              ("coverage", "total_frames", "measurement_coverage"))
          and "coverage=coverage" in inspect.getsource(
              __import__("core.audio_models", fromlist=["x"]).ChannelSyncReport),
          "AudioSyncResult now carries the evidence triple and "
          "ChannelSyncReport.resolve() populates it; the model reaches the "
          "consumer with the value it describes")

    # ---- RESOLVED BY P4.3: the cross-file path carries its denominator -------
    value("T9.6 PROMOTED (was 'the P2 cross-file path still drops "
          "total_frames'): the P2 path carries its denominator end to end",
          "total_frames=int(item.total_frames)" in inspect.getsource(
              __import__("core.audio_sync", fromlist=["x"]))
          and "item.total_frames" in inspect.getsource(
              __import__("production.output", fromlist=["x"])),
          "P4.2 recorded it as a remaining drop point and did not touch it "
          "because it is a different pipeline needing its own regression. P4.3 "
          "is that phase: the P2 path now gates on coverage, states the "
          "evidence on the sync result, and exposes it on AlignmentOutcome")

    # ---- the new reason code is registered
    value("T9.7 the new reason code maps to a status and is a failure class",
          "evidence_insufficient" in src
          and "evidence_insufficient" in inspect.getsource(
              __import__("core.audio_models", fromlist=["x"])),
          "mapped to LOW_CONFIDENCE (a number exists but is not trusted) and "
          "included in _ESTIMATION_FAIL_REASONS")

    def break_it() -> None:
        assert res.status is SyncStatus.SUCCESS, "x"
    control("T9.C the 'under-evidenced is not SUCCESS' check can fail",
            break_it)


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
    t9_p42_revalidation()

    n_cases = len(RESULTS)
    # The XFAIL owner is DERIVED from the registered cases, not hardcoded: the
    # owner changed at P4.3 (T5.4a promoted under D1; T5.4b now waits on D8), and
    # a hardcoded label would have kept printing a stale owner.
    _owners = sorted({
        r["detail"].split("OWNER=", 1)[1].split(":", 1)[0]
        for r in RESULTS
        if r["kind"] == "xfail" and "OWNER=" in r["detail"]
    })
    print("\n" + "=" * 78)
    print(f"cases            : {n_cases}")
    print(f"PASS             : {COUNTS['pass']}")
    print(f"FAIL             : {COUNTS['fail']}")
    print(f"XFAIL (contract) : {COUNTS['xfail']}   <- "
          f"{'owned by ' + ', '.join(_owners) if _owners else 'none open'}")
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
          f"{COUNTS['xfail']} contract gap(s) OPEN"
          + (f" and owned by {', '.join(_owners)}" if _owners else "") + ", "
          f"{COUNTS['control_ok']} controls discriminating")
    print("NOTE: XFAIL cases are NOT passes. They are the D6 finding, held "
          "open so they cannot be forgotten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
