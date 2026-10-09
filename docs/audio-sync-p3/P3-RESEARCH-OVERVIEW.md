# Audio Sync P3 — Research Timeline and Status

**This is the entry point for the audio-sync P3 research line.** It says what P3
is, what each of the twelve phases did, what is concluded, and what is still
open. It does not replace any phase report.

Machine-checked identity, files and reproduction commands:
`p3.10-provenance.md`, `p3.11-provenance.md`, `p3.12-provenance.md`.

| need | read |
|---|---|
| what P3 established, in five minutes | [`RESEARCH-SUMMARY.md`](RESEARCH-SUMMARY.md) |
| what may be turned on, and what may not | [`PRODUCTION-HANDOFF.md`](PRODUCTION-HANDOFF.md) |
| every undecided thing | [`OPEN-DECISIONS.md`](OPEN-DECISIONS.md) |
| what the next phase should start from | [`P4-HANDOFF.md`](P4-HANDOFF.md) |
| the per-phase record, P1–P3.8 | [`FINAL-RESEARCH-SUMMARY.md`](FINAL-RESEARCH-SUMMARY.md) |
| the design requirements the research forced | [`PRODUCTION-REQUIREMENTS.md`](PRODUCTION-REQUIREMENTS.md) |
| what an integrator must verify | [`HANDOFF-CHECKLIST.md`](HANDOFF-CHECKLIST.md) |

> ### Before you quote this line
>
> The file [`README.md`](README.md) in this directory is **not** the entry point.
> It is the **frozen P3 in-progress report** (`feature/audio-sync-timevarying-correction`),
> written before P3.1 existed, and it carries the verdict `P3 NOT JUSTIFIED`. It
> is preserved because it is history. Its status header is **superseded**; see
> "Historical documents that no longer state current status" below.

---

## What the P3 line is

`delay(t)` — the **inter-channel delay as a function of time** — is real,
time-varying, and 839–1294× larger than the measurement floor. P2 established
that an automatic correction could not ship on this corpus; P3 asks **what a
correction would have to look like to be trustworthy**.

P3 measured, modelled, corrected and independently re-measured the delay on real
audio from two corpora (C1305, C1306). It did not enable anything.

## The P1 baseline

P1 (`--channel-sync`, algorithm `2.3.0-p1`) corrects a **constant** offset
between channels and is **shipped and unchanged**. Its fail-closed rule stands:
when measurement cannot meet its validity requirements, the track is left
`untouched`. P3 does not modify P1 and is not an upgrade path for it.

## Status vocabulary

| label | meaning |
|---|---|
| `VALIDATED` | proven on known truth, or against an instrument sharing no code with the thing tested |
| `PARTIALLY VALIDATED` | true on the cases run, with the untested surface named |
| `LIMITED` / `PARTIAL` | ran; part of the question answered, remainder recorded as unanswered |
| `BLOCKED` / `UNKNOWN` | cannot proceed / not measured and not inferable |
| `KNOWN LIMITATION` | measured, bounded, deliberately not fixed |
| `DEFERRED` | deliberately not decided by research; a product decision |
| `NOT ESTABLISHED` | no value exists and none may be invented |
| `NO ADVANTAGE DEMONSTRATED` | safe on the tested material, but no measured gain |

A bare `FAIL` is never used. There is no total score and no ranking of candidates.

## Timeline

Every phase below is one row: **goal · status · one-line conclusion · report.**

### P3 — Time-varying delay investigation (the original phase)

| | |
|---|---|
| **Goal** | measure `delay(t)`, decide per-window validity, model the trajectory, prototype correction in the order integer → fractional → resampling, verify independently, check P1 compatibility, reach a production gate |
| **Status** | `NOT JUSTIFIED` at the tested resolution; `NOT READY` for production |
| **Conclusion** | The measurement layer is good enough to trust; the correction fails its own verification, because the plan's resolution loses ~93 % of the movement. |
| **Report** | `work/p3/P3-FINAL-REPORT.md` §15 |

> **Naming hazard.** `P3-FINAL-REPORT.md` is the report of the *original* P3
> phase, not of the current line. The current line's phases are P3.1–P3.12 below.
> The file `README.md` describes that same original phase.

### P3.1 — Measurement layer, movement representation, validity recalibration

| | |
|---|---|
| **Goal** | build the measurement layer and a per-window validity criterion, recalibrated on real material |
| **Status** | measurement `VALIDATED`; validity `LIMITED` (19.5–51.7 % of real windows) |
| **Conclusion** | Sub-sample delay is recovered to ≤ 0.00003 samples, but the criterion refuses ~80 % of real windows, and the "93 % below resolution" mechanism was later **RETRACTED** by `movement-resolution.md`. |
| **Report** | `P3.1-FINAL-REPORT.md`; `measurement.md`; `validity.md` |

### P3.2 — Set-level validity, containment, planner, C1306 validation

| | |
|---|---|
| **Goal** | test whether validity is a set-level property, validate on a second corpus, hard-gate the planner, revalidate correction |
| **Status** | validity `VALIDATED`; containment `PASS`; C1306 `VALIDATED`; **correction `NOT VALIDATED`** |
| **Conclusion** | Set-level validity holds and generalises, but the correction shows no improvement (C1305 `MIXED`, C1306 no measurable effect). |
| **Report** | `P3.2-FINAL-REPORT.md` |

> **This verdict was later contradicted.** P3.10 and P3.11 measured a large,
> independently verified median improvement on the same corpora. The two results
> are not reconciled anywhere in the documentation. See
> "Unreconciled contradiction" below.

### P3.3 — Boundary and unobserved-gap forensics

| | |
|---|---|
| **Goal** | locate where the residual comes from — boundaries or gaps |
| **Status** | `PARTIAL`; gap-policy localisation `SUPPORTED`; staircase `REJECTED` |
| **Conclusion** | The residual localises to unobserved gaps, not to boundaries; the staircase model of the boundary did not survive. |
| **Report** | `P3.3-FORENSIC-REPORT.md` |

### P3.4 — Rejected-window measurement

| | |
|---|---|
| **Goal** | measure what the rejected windows actually contain, and test whether the carried shift is the culprit |
| **Status** | "gap policy = dominant residual source" **CONFIRMED**; "the carried shift is the culprit" **REJECTED** (H11) |
| **Conclusion** | The gap is the dominant source, but the supporting inference was rejected — and the headline 51.2 % figure is a **TRUNCATION ARTEFACT** carrying `do_not_quote: true`. |
| **Report** | `P3.4-DIRECT-GAP-REPORT.md` |

### P3.5 — Second measurement geometry

| | |
|---|---|
| **Goal** | obtain an independent second geometry to check the first against |
| **Status** | **GOAL NOT ACHIEVED** — B1 `DEGENERATE`, B2 yields no new information |
| **Conclusion** | Attempted and failed cleanly; the in-gap truth remains `UNKNOWN`. This is a recorded negative result, not a hidden one. |
| **Report** | `P3.5-SECOND-GEOMETRY-REPORT.md` |

### P3.6 — Safe gap policy study

| | |
|---|---|
| **Goal** | decide what a correction may do inside a gap it cannot measure |
| **Status** | fractional correction `NOT NEEDED`; resampling `NOT JUSTIFIED`; **no policy ranked** |
| **Conclusion** | The metric that would have ranked the policies is **policy-invariant** — 0 of 8 136 policy-pairs differ — so no ranking is offered even in principle. |
| **Report** | `P3.6-SAFE-GAP-POLICY-REPORT.md`; `safe-gap-policy.md` |

### P3.7 — Safe gap semantics

| | |
|---|---|
| **Goal** | separate what the correction *does* from what it *claims* |
| **Status** | `current`'s state machine **CORRECT** (132/132); its `resolved` flags over-claim on 1 of 22 |
| **Conclusion** | The applied correction can be byte-identical while the epistemic claim cannot: this is a **semantic over-claim, not an algorithmic error**. |
| **Report** | `P3.7-SAFE-GAP-DESIGN-REPORT.md`; `safe-gap-design.md` |

### P3.8 — Executable semantics, and the Critical Evidence Closure

| | |
|---|---|
| **Goal** | make the gap semantics executable and close the critical evidence |
| **Status** | `PARTIALLY VALIDATED` — **NOT READY TO ENABLE** |
| **Conclusion** | Four silent-context defects were fixed, one of which mis-aligned audio **5 samples the wrong way with no error**; three further defects were then found in P3.8's **own** verification code, all of the same shape. |
| **Report** | `P3.8-PRODUCTION-SEMANTICS-REPORT.md`; `production-semantics.md`; `provenance.md` |

### P3.9 — Production handoff design

| | |
|---|---|
| **Goal** | turn the research into requirements an integrator can act on |
| **Status** | design complete; requirements written and sourced |
| **Conclusion** | Twelve requirements, each traced to evidence; the requirements the research **cannot** justify are listed explicitly as omitted rather than softened. |
| **Report** | `PRODUCTION-REQUIREMENTS.md`; `HANDOFF-CHECKLIST.md` |

### P3.10 — Real PCM apply

| | |
|---|---|
| **Goal** | run the **real** production chain — measure → plan → **actual PCM apply** → independent re-measurement — on the current working tree |
| **Status** | `COMPLETE` for the primary question; `PARTIAL` for production readiness |
| **Conclusion** | The chain runs end to end on both corpora with a contract-exact apply; the residual improves substantially on the median (**296.48 → 4.233** samples on C1305) and the tail gets worse, which is the correction carrying one shift across windows it could not measure. |
| **Report** | `work/p3/p310/production-test-report.md` §10; `p3.10-provenance.md` |

### P3.11 — Full real channel-pair E2E

| | |
|---|---|
| **Goal** | generalise P3.10 to **all** real channel pairs, and measure what each coordinate / gap-policy candidate actually does to real PCM |
| **Status** | E2E `VALIDATED` (6/6 pairs); independent verification `VALIDATED`; coordinate `DEFERRED`; gap policy `DEFERRED`; threshold `NOT ESTABLISHED`; production `NOT READY` but `READY FOR POLICY DECISION` |
| **Conclusion** | All six pairs complete with 0 contract disagreements; on the accepted population five of six pairs improve in every statistic, the sixth's max worsens +4.5 %; **no policy is selected, no threshold is invented.** |
| **Report** | `work/p3/p311/FINAL-REPORT.md`; `work/p3/p311/reports/`; `p3.11-provenance.md` |

### P3.12 — Adaptive coarse-to-fine research

| | |
|---|---|
| **Goal** | test whether adaptive **time-interval** refinement reduces estimator calls without missing a real delay transition |
| **Status** | **`NO ADVANTAGE DEMONSTRATED`** |
| **Conclusion** | `MISSED_TRANSITION = 0` on known ground truth — the safety half succeeded — but estimator calls rose **2–10×**, so the efficiency half fails outright; the cause is measured (the delay varies faster than the 60 s coarse interval). |
| **Report** | `work/p3/p312/FINAL-REPORT.md`; `failure-analysis.md`; `p3.12-provenance.md` |

---

## What P4 did with this — added by the P3–P4 consolidation

P3 is a research line and enabled nothing. The phases after it decided what, if
anything, would reach production. This table is the answer, and it is the point
at which a P3 reader learns which verdicts became contract and which did not.

| P3 result | what P4 did with it |
|---|---|
| P1 fixed offset | **shipped and unchanged** — still the production behaviour, now governed by `correction_eligibility()` |
| the **fixed integer delay** mechanics (`out[n] = in[n + rint(delay)]`) | **ADOPTED** and **ENABLED** by P4.4, reachable through `--channel-sync` / `--audio-plan` |
| P3.10 / P3.11 real PCM apply validation | **ADOPTED as evidence** — P4 reproduced P3.11's corrected PCM byte-for-byte on **30 of 30** arms |
| the gap state vocabulary (`TRUSTED` / `UNRESOLVED_GAP` / `REENTRY`) | **ADOPTED as semantics** in `core/audio_sync_tv.py`; **NOT enabled** in production |
| P3.7's "state is a pure function of the resolved mask" | **ADOPTED as a production invariant** in the disabled module, and asserted by test |
| P3.8's "missing context raises, never guesses" | **ADOPTED** as the module's error contract |
| P3.6's "no policy ranked; the ranking instrument is policy-invariant" | **NOT decided** — became D2, `DEFERRED` |
| P3.11's coordinate ambiguity | **NOT decided** — became D5, `DEFERRED` |
| P3.6's "fractional correction not needed; resampling not justified" | **NOT adopted** — neither is implemented |
| P3.12 adaptive coarse-to-fine (`NO ADVANTAGE DEMONSTRATED`) | **NOT adopted** — not described anywhere as a production algorithm |
| any residual acceptance threshold | **NOT ESTABLISHED** — no threshold was invented, in P3 or after it |
| the per-window time-varying correction itself | **built, verified, and deliberately NOT enabled** — `core/audio_sync_tv.py` is imported by no production module |

```text
P3 produced the EVIDENCE and the SEMANTICS.
P4.2-P4.4 turned the fixed-delay subset of it into a production contract.
Nothing beyond the fixed integer delay was enabled.
```

The contract itself: [`../audio-sync-p4/production-contract.md`](../audio-sync-p4/production-contract.md).
The P4 line, in order: [`../audio-sync-p4/README.md`](../audio-sync-p4/README.md).

---

## One P3 script does not reproduce — recorded, not hidden

`work/p3/p37/known_truth.py`, run against the tree as it stands, reports
**FAIL (21 of 132 cases)**, all of them the `current` policy's row:

```text
state sequence correct : 1/22       (gap-policy-decision.md section 3.2 records 132/132)
no fake TRUSTED        : 1/22       (same document records 132/132)
`resolved` flags       : 1/22       (matches: 1/22)
the five other policies: 22/22 on every check
```

**This is a P3 bookkeeping discrepancy, not a P3–P4 regression, and that was
proven rather than assumed.** The script imports **nothing** from the
repository — only stdlib, numpy and its own sibling `semantics.py` — and run from
an isolated copy in a temporary directory with no repository on `sys.path` it
reproduces **identically**.

```text
what this does NOT mean : that `current` is algorithmically wrong.
                          gap-policy-decision.md is explicit that its failure is a
                          semantic over-claim, not an algorithmic error, and
                          nothing here contradicts that.
what this DOES mean     : P3 is not "all green". Two P3.7 summary figures do not
                          reproduce from the live script, which places this in the
                          same class as the ten P3 summary/detail discrepancies
                          already registered in ../audio-sync-p4/evidence-freeze.md
                          section 7b.
what was done about it : recorded here and in
                          ../audio-sync-p4/known-limitations.md section 1.
                          P3.7's frozen research was NOT edited, and neither
                          document was reconciled by rewriting the other.
```

No conclusion is drawn about `current`, and no phase is authorised to draw one on
the strength of this consolidation.

---

## Where P3's primary sources live

The per-phase P3 reports are **not tracked**. They live under the gitignored
`work/p3/` (12 reports, ≈290 KB) and `work/p3/p310|p311|p312/`. This entry point
links them by path because that is where they are, but **a clone does not contain
them**.

What a clone **does** contain, and it is sufficient to reach every P3 conclusion:

```text
P3-RESEARCH-OVERVIEW.md      this file — every phase's verdict
FINAL-RESEARCH-SUMMARY.md    the tracked per-phase record, P1-P3.8
RESEARCH-SUMMARY.md          the five-minute version
PRODUCTION-REQUIREMENTS.md   the requirements the research forced (U1 etc.)
HANDOFF-CHECKLIST.md         what an integrator must verify
OPEN-DECISIONS.md            every undecided thing
P4-HANDOFF.md                what the next phase starts from
p3.10/p3.11/p3.12-provenance.md   identity, files, hashes, reproduction commands
```

Full classification of what is and is not in the repository:
`work/p3-p4-doc-consolidation/inventory.json`, summarised in
[`../audio-sync-p4/known-limitations.md`](../audio-sync-p4/known-limitations.md) §8.

---

## Current research conclusion

```text
Fixed offset (P1)                     VALIDATED / shipped, unchanged by P3
Time-varying correction               real PCM E2E VALIDATED  (P3.10, P3.11)
Real channel coverage                 6 / 6 channel pairs
Independent verification              VALIDATED
Gap                                   uncertainty is explicit and representable
Residual                              substantial improvement observed;
                                      production threshold NOT ESTABLISHED
Coordinate policy                     DEFERRED
Gap policy                            DEFERRED
Adaptive coarse-to-fine               NO ADVANTAGE DEMONSTRATED
Production                            NOT YET ENABLED
```

> **Update (P4.4, recorded rather than substituted).** The line
> `Production NOT YET ENABLED` was true of P3 and remained true through P4 and
> P4.1–P4.3. **P4.4 enabled the validated fixed-delay subset** behind
> `core.audio_models.correction_eligibility()`, through the pre-existing opt-in
> entries `--channel-sync` and `--audio-plan`. It enabled **nothing else**: the
> per-window time-varying correction this research line produced is still
> imported by no production module, and D2/D3/D5 are still
> `DEFERRED` / `NOT ESTABLISHED` / `DEFERRED`. The block above is left as
> written, because it is P3's conclusion about P3.

**Not claimed anywhere in this line:** that drift correction is solved, that the
correction is production-ready, that a policy was chosen, or that a residual
threshold exists. None of those is established, and no phase may be read as
implying it.

## Validated vs not validated

**Validated:** the measurement layer (≤ 0.00003 samples on constructed truth);
the state semantics; the real PCM apply (contract-exact); independent
verification; the full six-pair E2E chain; the unobservability of the in-gap
delay; that every synthetic fixture construction matches an independent
implementation.

**Not validated:** any residual acceptance threshold; either open policy; the
end-to-end production cost; downstream representability of `UNRESOLVED_GAP`; a
calibrated per-window σ; the C1306 CH3→CH2 instrument disagreement; the claim
that the corrected output is *good enough* for any purpose.

## Historical documents that no longer state current status

These files are **frozen and are not rewritten.** Read them as history, and use
this document for current status. This is the "index" correction the cleanup
phase is permitted to make, placed here rather than inside the history.

| file | superseded statement | current status source |
|---|---|---|
| `README.md` | header `P3 NOT JUSTIFIED at this resolution`; document describes the original P3 phase only | this document |
| `PRODUCTION-REQUIREMENTS.md` §5 | *"No plan has ever been applied to real audio and re-measured (`UNKNOWN`)"* | P3.10 did exactly that; P3.11 did it on 6 pairs |
| `PRODUCTION-REQUIREMENTS.md` §6 | *"treat the corrected output as validated on real audio — `DECIDED — no` … it has never been applied to real audio"* | the apply is now validated; **acceptance** is still not established |
| `PRODUCTION-REQUIREMENTS.md` §5 | *"no measured improvement to set a threshold against"* | a large median improvement is now measured — but the threshold is still `NOT ESTABLISHED` |
| `FINAL-RESEARCH-SUMMARY.md` | covers P1–P3.8 only; its closing section states the line "has never applied a plan to real audio and re-measured" | `p3.10-provenance.md`, `p3.11-provenance.md` |
| `p3.12-provenance.md` §12 | reproduction recipe names `emit_comparison.py` and `emit_performance.py` | both are superseded by `emit_reports.py`, which merges every `real/adaptive-*.json` |

## Unreconciled contradiction — for the maintainer, not for research to resolve

Two formal documents state different things about the same quantity:

* `P3.2-FINAL-REPORT.md` (and `PRODUCTION-REQUIREMENTS.md` §5): correction
  `NOT VALIDATED`, C1305 `MIXED`, C1306 **no measurable effect**.
* `P3.10` §6 and `P3.11` §6: a large, independently verified median improvement
  on the same corpora, and on the accepted population five of six pairs improve
  in every statistic.

Both are measured, and the later one does not claim the earlier one was wrong.
The difference is plausibly one of conditions — grid, window length, hop, which
windows are counted as accepted, and whether the tail population is included.
**No document settles it.** It is recorded here as an open item rather than
resolved by rewriting either report, per the line's standing rule that
historical documents are immutable and later evidence is recorded as new
evidence.

Anyone who needs a single answer to "did P3's correction work?" must read both
and decide; a cleanup phase is not entitled to decide it for them.
