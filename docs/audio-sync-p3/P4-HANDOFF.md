# Audio Sync P3 → P4 Handoff

**P3 research is frozen.**

No further P3 research is running or planned. P3.1–P3.12 are complete, each with
a recorded verdict. Nothing in P3 is production-enabled, and no P3 phase chose a
policy, a coordinate contract, or a threshold.

This document is the contract between the finished research line and the next
phase.

---

## 1. P4 should start with a policy decision, not with code

The research is complete enough to decide. It is not complete enough to
implement without deciding. Concretely, P4 should open by obtaining answers to
these four questions, in this order, **from the product owner**:

| # | question | owner | why research cannot answer it |
|---|---|---|---|
| D1 | inside a gap, is uncorrected alignment worse than invented structure? | product | A concert master and a sync-reference track do not want the same answer. The metric that would have ranked the candidates is policy-invariant (0 of 8 136 pairs), so research has **no** ranking to offer. |
| D2 | which `window_of` convention is correct — `latest-start` or `nearest-centre`? | product + engineering | Both are deterministic and symmetric in error magnitude. The choice is whether a correction should **anticipate** a change or **lag** it. |
| D3 | what residual counts as good enough, on which statistic, on which corpus? | product | `NOT ESTABLISHED` on both corpora. Inventing one is prohibited, and raising one so a named corpus passes is the single worst defect available here. |
| D4 | may P3 correction be enabled behind a flag at all? | product | No flag exists. Coverage is 19.5–74.4 %, so most of the timeline would be policy-filled **while 100 % of samples are rewritten**. |

`docs/audio-sync-p3/OPEN-DECISIONS.md` holds the full register, including the
decisions that are **not** open.

## 2. What P4 must not redo

Unless new direct evidence appears, do not revisit:

```text
estimator theory / the measurement layer          settled: VALIDATED to 0.00003 samples
adaptive coarse-to-fine refinement                settled: NO ADVANTAGE DEMONSTRATED (P3.12)
gap hidden-trajectory reconstruction              settled: the in-gap delay is UNOBSERVABLE,
                                                  not merely imprecise (P3.6/P3.7, D5)
fractional (sub-sample) correction                settled: NOT NEEDED
resampling-based correction                       settled: NOT JUSTIFIED
a second measurement geometry                      attempted and failed (P3.5, GOAL NOT ACHIEVED)
calibrating the validity criterion on C1305       known to be a fit, not a calibration
```

The in-gap unobservability result is the important one: **no estimator, no window
length and no policy recovers the delay inside a gap.** Raising measurement
precision does nothing — `σ_meas` from 0 to 2.0 barely moves the answer. Any P4
proposal that amounts to "measure harder inside the gap" is already answered.

## 3. What P4 inherits that is safe to build on

| asset | where | status |
|---|---|---|
| the real E2E chain, six pairs | `work/p3/p311/production_chain_p311.py`, `run_matrix.py` | `VALIDATED` |
| the gap-semantics engine | `work/p3/p38/semantics/gap_semantics.py` | `PARTIALLY VALIDATED`, executable |
| the production requirements | `docs/audio-sync-p3/PRODUCTION-REQUIREMENTS.md` | written and sourced per requirement |
| the integrator checklist | `docs/audio-sync-p3/HANDOFF-CHECKLIST.md` | 12 requirements + standing prohibitions |
| the coordinate tests | `tests/audio_sync_p311/test_coordinates.py` | 71 PASS / 0 FAIL, tracked |

**Caution:** the implementation lives under gitignored `work/`. Hashes in the
provenance documents prove *which* bytes produced a result; they do not preserve
them. If `work/` is deleted, P3 is not reproducible and the hashes become a
tombstone. D7 in `OPEN-DECISIONS.md` records this and it is unresolved. **If P4
needs the P3 implementation to survive, moving it somewhere tracked is the first
task, not a later one.**

## 4. The defect family P4 must not inherit

A **silent pass** — a check that reports success while measuring nothing —
recurred in P3.8, P3.9, P3.10, P3.11 and P3.12. The worst instance could not
fail by construction: P3.12's `MISSED_TRANSITION` metric asked whether the true
delay varies inside a reference block, and a perfect step makes every block
internally constant, so it **could never fire** while reporting zero misses on a
fixture containing a 320-sample step.

Fourteen defects were found in P3.12's own code. **None raised an error at the
point of failure.** Every one was caught by checking against something
independent.

Therefore, for P4:

```text
every check must be demonstrated discriminating before it is trusted
every count must be compared to an independently derived expectation
"the test passed" is not evidence until the test has been shown able to fail
```

## 5. Standing prohibitions, carried forward

```text
do not invent a residual acceptance threshold
do not raise a threshold so a named corpus passes
do not select a gap policy or a coordinate contract on the research's behalf
do not enable anything by default before the D1–D4 decisions
do not treat the reference grid as ground truth — it is the same estimator's
    exhaust, so a shared bias is invisible in it
do not rewrite a historical P3 report to match a later result
do not describe P3.12 as a failed idea: it is a formulation with no demonstrated
    efficiency advantage, and its safety half succeeded
```

## 6. What is explicitly not carried forward

```text
no policy recommendation        research declined to rank
no threshold                    NOT ESTABLISHED
no coordinate selection         DEFERRED
no default enablement           NOT ENABLED
no push / merge / tag           the branch was never pushed
```

## 7. Repository state at the freeze

| item | value |
|---|---|
| branch | `research/audio-sync-p3-production-semantics` |
| upstream | none — never pushed |
| production code touched by P3 | **none** |
| user working-tree modifications | 9 tracked, untouched by every P3 phase |
| new tags | none |

See `work/audio-sync-cleanup/current-state-freeze.md` for the verbatim snapshot.
