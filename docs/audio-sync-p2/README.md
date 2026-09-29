# Audio Sync P2

**STATUS: CLOSED**

> Final closure session. This README is the single entry point to the whole P2
> investigation. It states the question, the findings, the decisions, the limits, what
> was withdrawn, and the conditions under which the work should be reopened.
>
> **P2 changed no production code, and the evidence says it should not.** The correct
> outcome for the material investigated is to leave the audio `untouched`; P2's
> deliverable is the diagnosis and a validated measurement instrument.
>
> **Audio Sync is now closed as a line of work.** P1 keeps its existing fail-closed
> behaviour and its regression guard is operational again; P2 is archived and awaits
> evidence, not development. See `p1-guard-promotion.md` for the guard decision and
> `p1-baseline.md` §7 for the artifact-loss and recovery record.

---

## 1. The question

P1 handles a near-constant inter-channel offset reliably. For a relative delay that
changes over a long recording, it refuses the track as `non_constant` and leaves it
`untouched`. P2 asked whether that refusal is correct — i.e. whether the changing delay
is real, what it is made of, and whether the production path should correct it.

## 2. Scope

| In scope | Out of scope |
| --- | --- |
| Establish a reliable measurement of time-varying delay | Any change to PTS / container timing |
| Decide whether the variation is constant / piecewise / linear / nonlinear | Fractional or resampling correction |
| Decide whether a corrector is justified | Video, other audio workstreams (`x265`, hardware decode) |
| Audit the gates that decide `fixed` vs `untouched` | Redesigning the P1 gate to make tests pass |

Discipline throughout: **evidence before model, model before estimator, estimator before
correction, prototype before production.** No experiment was run without first stating
what it would falsify.

## 3. Core findings

1. **The corpus does contain large, real, time-varying inter-channel delay.** 12 of 12
   channel-pair trajectories across two independent recordings are non-constant, with
   movement **839–1294× the 1-sample (0.0208 ms) measurement floor**. 597 of 597 windows
   measured usable, none rim-pinned.
2. **It is not a common sample-clock drift.** No trajectory is monotone (141–204 sign
   changes per 319 increments), and the implied linear rates differ by up to **three
   orders of magnitude within a single file** (−3773.70 ppm vs +20.87 ppm on C1305). A
   common-mode clock error cancels in inter-channel measurement by construction, so
   drift would require equal rates across pairs.
3. **It is not a smooth linear or nonlinear ramp.** A cubic barely improves on a linear
   fit; the movement resolves into discrete events with long plateaus.
4. **P1 already refuses this material, and refusing is the correct fail-closed
   behaviour** — because no per-window validity criterion survived validation. A
   corrector that acts on an unchecked measurement would silently corrupt aligned audio.
5. **The confidence metric is measurably not a correctness score.** It separates
   wideband from narrowband content; on an 8-case known-truth matrix it never once
   separated a right answer from a wrong one, and it rejected six correct measurements.

## 4. Final decision

| Item | Status |
| --- | --- |
| Audio Sync P1 | Existing production behaviour retained (`untouched` when measurement cannot meet its validity requirements) |
| P2 research | **CLOSED** |
| Time-varying delay | **OBSERVED** — as an estimated range, see §6 |
| Clock drift | **NOT SUPPORTED BY CURRENT EVIDENCE** (tested and rejected, not merely unproven) |
| Linear drift | **NOT SUPPORTED** (tested and rejected) |
| Piecewise trajectory | Lowest-residual model, but **no model class established** |
| Nonlinear / smooth trajectory | **NOT SUPPORTED** |
| Confidence production fix | **NO** |
| Time-varying correction | **NO** |
| Resampling | **NO** |
| Production core changes | **NONE** |
| Long-duration tests — P2.1 research phase | **0** |
| Long-duration tests — final session | **2** (authorised P1 baseline artifact recovery only) |
| Other long-duration tests (final session) | **0** |
| Full 137 regression | **NOT RERUN** |
| New real files | **0** |
| P1 regression | **27 PASS / 0 FAIL** (fresh independent run, exit code 0) |
| P1 baseline artifact | **RESTORED** — re-derived by two full channel-sync runs; original bytes UNRECOVERABLE |
| P1 regression guard | **OPERATIONAL**, kept out of `tests/` by decision |
| Archive | **COMPLETE** (with the artifact-loss item recorded, not hidden) |

`NOT SUPPORTED` is used above only where a hypothesis was **tested and failed**.
Where a question was simply not settled — the model class, the validity criterion — the
table says so instead.

### Why no correction

1. The clock-drift hypothesis does not hold, so the resampling family is out.
2. No trajectory **model class** is established.
3. No per-window **validity criterion** exists — the blocking gap, independent of the model.
4. Correction **benefit is not demonstrated**: the previous phase's prototype zeroed the
   residual only at its own geometry (a self-fulfilling measurement) and on an unseen
   grid carried 0.92×–1.65× the original span with +5.5 to +14.2 dB boundary jumps.
5. Even a perfect piecewise plan would leave **3.634 ms of a 26.958 ms span** — about
   half — and **174× the integer correction quantum**. Less than half the movement is
   removable, and nothing can check whether a given window's measurement was right.
6. P1 compatibility is unproven: `preservation/validate.py` requires exact `stts`
   equality, so any sample-count change fails `--check basic`.

### Why `untouched` is a safe outcome, not missing work

> When P1 cannot measure to its validity requirements it leaves the track `untouched`,
> which prevents an erroneous measurement from being automatically converted into an
> erroneous audio edit. That is **fail-closed behaviour under the current evidence**, not
> an unimplemented feature. The missing validity criterion forbids a *desired* action
> rather than licensing a wrong one.

## 5. Evidence hierarchy

Every claim in these documents is graded. Final decisions may rest only on Tiers 1–3.

| Tier | Definition |
| --- | --- |
| **T1** | directly validated synthetic measurement — instrument proved on a known answer, fixture independently constructed |
| **T2** | cross-check with an independently constructed measurement (two instruments or two constructions agreeing) |
| **T3** | historical real-material observation, reproduced, with provenance and limits stated |
| **T4** | prototype-derived observation without independent validation — **may not support a decision** |
| **T5** | retracted / invalidated result — historical record only |

Full grading of every conclusion: `work/p2/final-evidence-audit.md`.

## 6. Known limitations

1. **The 17.5–27.0 ms range is an OBSERVED ESTIMATED RANGE, not a verified physical
   absolute delay.** The extent is well supported; individual per-window absolute values
   come from an instrument with **no per-window validity criterion** and should not be
   treated as verified ground truth.
2. **No validity criterion exists.** Every candidate tested was falsified. This is the
   single biggest limitation and the reason no corrector ships.
3. **No model class is established.** The movement is discrete with 15–24 breaks over
   4800 s.
4. **The integer instrument's resolution is 1 sample = 0.0208 ms.** A fractional
   *measurement* capability does exist (≤0.02 µs, independently validated), but no
   fractional *correction* does — production corrects in whole samples.
5. **The C1305 `CH3↔CH4` last ~940 s tail is unresolved** and carries most of the
   variation.
6. **The historical 120 s-geometry spans are window averages**, not the movement itself.
   A constant delay measures identically at every window length; a changing one does not.
7. **The P1 regression guard is operational again** — its inputs were destroyed and
   have been recovered; see §8. It remains an **on-demand** check, deliberately not
   wired into `tests/`; see `p1-guard-promotion.md`.

## 6.1 The final decision, in one paragraph

> The current corpus contains an evident time-varying inter-channel delay relationship.
> The current evidence does not support attributing it to a common sample-clock drift,
> and is not sufficient to justify automatic time-varying correction or resampling. The
> current measurement system also cannot provide adequate per-window validity assurance.
> Therefore the production path implements neither time-varying correction nor
> resampling, and P1 retains its existing fail-closed behaviour.

The qualifiers **current corpus**, **current evidence** and **current instrument** are
load-bearing: this is not a claim about all material in perpetuity. It is the honest
scope of what was measured.

## 7. What was withdrawn

Two conclusions were published and later withdrawn. Both came from treating an artifact
as final while work on it was still in progress.

| Old claim | Status | Reason | Current replacement |
| --- | --- | --- | --- |
| Sub-sample support "NOT SUPPORTED" | **RETRACTED** | the audit covered the correction path and skipped `phase_slope_fine`, a measurement function | Validated fractional measurement, ≤0.02 µs, 8/8 on a known answer |
| `tools/p2/estimate_trajectory.py` "INVALID INSTRUMENTATION" | **RETRACTED** | it was tested while still being written; the report grew 18 984 → 46 367 bytes afterwards | Re-run passes 8/8 self-test sections |
| "Uncertainty under-predicts real scatter by ~8.9×" | **RETRACTED** | derived from that same in-progress prototype | No quantitative shortfall claim |
| "Observed scatter 14.08 samples" | **RETRACTED** | same source; direct measurement gives 0.00–0.33 samples | Scatter ≤0.33 samples on plateaus |
| "Edge/false peak scores unexpectedly high confidence" | **NOT REPRODUCED** | targeted reproduction failed; the highest confidence belongs to a correct answer | Unresolved; not carried forward, not asserted false |
| Second-peak ratio as a validity criterion | **RETRACTED** | falsified by a purpose-built single-arrival control | No ratio-based criterion |
| Correction prototype "verified at 0.000 ms residual" | **RETRACTED** | the measurement was taken at the plan's own geometry | Prototype **REJECTED** as a product |
| "110–1090× the 0.024 ms floor" | **RETRACTED** | wrong divisor; `1000/48000 = 0.02083` | **839–1294×** the 0.0208 ms floor |

Not every claim is `RETRACTED`: items that were **simply not confirmed** are marked
`NOT REPRODUCED` or `UNRESOLVED`. Collapsing those into "false" would overstate what was
actually learned.

## 8. Historical artifacts

```
work/p2/            research tree — reports, scripts, machine-readable outputs
work/p2/out/raw/    8 × mono float32 48 kHz corpus extracts (6.4 GiB) — INTACT
tools/p2/           prototypes, NOT imported by the shipped program
```

### Test artifact loss (recorded, not hidden) — now recovered

A P2.1 cleanup command used a relative path and deleted `work/p2/out/` except
`out/raw/`. Classification: **TEST ARTIFACT LOSS** — *not* a regression; no production
code was involved.

| Asset | State |
| --- | --- |
| Raw corpus extracts | **SURVIVED**, used throughout |
| All P2.1 result JSON | **REGENERATED**, stable on re-run |
| `work/p2/out/agent_j/` (P1 per-track reports + known-answer record) | **RESTORED** by two authorised full channel-sync runs (402.5 s C1305, 336 s C1306); original bytes **UNRECOVERABLE** |
| P1 guard, while its inputs were missing | `0 PASS / 3 FAIL`, all three = `MISSING INPUT`, **0 checks failing on their merits** |

```text
HISTORICAL OBSERVED RESULT = 27 PASS / 0 FAIL   (observed in full, before the loss)
CURRENT RESULT             = 27 PASS / 0 FAIL   (fresh independent run, exit code 0)
```

The two are **independent results and are kept separate.** The guard's thresholds and
expectations were not adjusted to reproduce the historical figure. No PASS was
fabricated, and the loss was never reinterpreted as a pass.

The recovery is anchored by a byte-level check: rebuilding the known-answer control
record reproduced the historical transparent-mode hash **exactly**
(`SHA256 = 8e587168b804bffb79cc7963e3c3e6493c74a0796c124cc2968ce9d665dd4787`).

The guard is **operational** and is invoked on demand:

```text
python work/p2/scripts/p1_regression_guard.py --json work/p2/out/p1_guard_recovery.json
```

**It is deliberately not in `tests/selftest/suites/`.** The reason is operational, not
paperwork: the framework's only vocabulary is `PASS`/`FAIL`
(`tests/selftest/paths.py:60`), so an absent 50 GB corpus fixture would render exactly
like a functional regression — re-creating inside the project's own green/red signal the
ambiguity this session had to repair. Full argument, plus the conditions that would
reverse the decision: `p1-guard-promotion.md`.

### Prototype status

| Prototype | Status |
| --- | --- |
| `work/p2/scripts/p2_track.py`, `fullrate_delay.py` | **VALIDATED** — the instrument every P2 number rests on |
| `work/p2/scripts/p21_*.py` | **VALIDATED** |
| `work/p2/scripts/p1_regression_guard.py` | **VALIDATED — OPERATIONAL** (27 PASS / 0 FAIL on the recovered inputs; not promoted into `tests/`, by decision) |
| `work/p2/scripts/p1_recover_artifacts.py` | **VALIDATED** — administrative recovery driver for the two lost P1 baseline artefacts (not a research tool) |
| `tools/p2/estimate_trajectory.py` | **EXPERIMENTAL** — passes its own 8/8 self-test; non-load-bearing |
| `tools/p2/correction/*` | **REJECTED** — its own verifier failed its own outputs |
| early harnesses (`arrivals.py`, `two_arrivals*.py`, `wide_delay.py`, `band_test.py` v1, `subsample_delay.py` v1, `finer_plan*.py`) | **RETRACTED** — each falsified by its own self-test or a known-answer control |

Prototypes are now **historical research artifacts**, not a development backlog.

## 9. Chapters

| File | Content |
| --- | --- |
| `README.md` | this file — the single entry point |
| `measurement-integrity.md` | execution pipeline with units, unit/sign/boundary/precision audits, instrument limits, known-answer validation |
| `confidence.md` | what the production confidence metric actually measures, its reproduction, root cause, and why no fix ships |
| `fine-resolution.md` | the window-vs-search-range experiment that closed the fine-resolution pending item |
| `investigation.md` | corpus, evidence, uncertainty, rejected hypotheses, confirmed model |
| `trajectory-model.md` | model classes, the two-regime decomposition, and why no model is established |
| `estimator.md` | the estimator prototype and its **retracted** uncertainty claim |
| `correction.md` | the correction ladder, what the prototype proved, and the result that stops it shipping |
| `validation.md` | long-duration validation and the production-entry verdict |
| `p1-baseline.md` | what the shipped P1 decides on this corpus, the historical-vs-current guard results, and the artifact loss/recovery record |
| `p1-guard-promotion.md` | whether the P1 regression guard belongs in `tests/` — the decision, the evidence, and the reversal conditions |
| `p1-confidence-metric.md` | the confidence finding — a finding, **not** a specification to implement |
| `final-decision.md` | the P2.1 production decision, with its corrections and unresolved items |

Research record (not shipped, under `work/p2/`):

| File | Content |
| --- | --- |
| `P2-FINAL-CLOSURE.md` | the final session: recovery runs, guard result, archive closure, final status |
| `P2-CLOSURE-REPORT.md` | the previous closure session: evidence consolidation, artifact-loss classification, archive |
| `p1-artifact-recovery.md` | what was lost, what each guard failure meant, and how it was recovered |
| `p1-recovery-run-1.json`, `p1-recovery-run-2.json` | per-run provenance: command, timings, return code, decisions, artifact hashes |
| `p1-artifact-manifest.json` | recovered-artifact manifest with SHA256 and verification verdict |
| `P21-FINAL-REPORT.md` | the P2.1 measurement-integrity session, 14 sections |
| `final-evidence-audit.md` | every conclusion graded by evidence tier, plus the evidence hierarchy |
| `archive-manifest.json` | machine-readable archive record, prototype statuses, retracted claims |
| `session-baseline.md`, `unit-audit.md`, `short-tests.json` | the P2.1 baseline, unit table and test log |

## 10. How this project's measurements fail

Nine instruments or interpretations were built, used to support a conclusion, and then
found wrong. Most produced **plausible, complete, self-consistent numbers** — the
failure mode is not a crash, it is a number that looks fine.

Three rules are now enforced:

1. **An instrument must prove itself on a known answer before it is pointed at real
   data.** The canonical case: a missing unit conversion quantised every delay to 8
   samples while producing an entirely reasonable-looking trajectory.
2. **A validity metric must be tested against a known-truth negative control, and the
   control must itself be validated first.** The second-peak ratio separated the corpus
   beautifully — no overlap at all — and was still meaningless.
3. **An artifact is not final while work on it is still in progress.** Both retractions
   in §7 came from violating this one.

## 11. Future re-entry conditions

P2 is closed. It should be reopened **only** if new evidence arrives. These are the
specific signals, not a schedule:

| # | Signal | What it would change |
| --- | --- | --- |
| 1 | A **monotone delay ramp** in a new recording — every increment the same sign, fitted rate stable across sub-intervals | Supplies the missing sample-rate-mismatch evidence; makes resampling discussable |
| 2 | An **independent** measurement of a sample-rate mismatch (a clock spec, an SRO measurement, a third-party tool) | Corroborates drift without depending on this delay instrument |
| 3 | **Reproducible systematic movement** across a balanced multi-body corpus with a consistent shape | Would establish a model class |
| 4 | A **validity criterion** that survives a known-truth negative control | The blocking gap; without it no corrector is trustworthy |
| 5 | A corpus that actually hits the **≤1 kHz bandwidth floor** of the confidence gate | Turns a documented observation into an actionable defect with real material behind it |
| 6 | A controlled short test demonstrating **correction benefit** plus **P1 compatibility** | The remaining two preconditions |

Note on a former criterion: "a validated fractional-delay instrument" **already exists**
(≤0.02 µs) and meeting it did **not** change the decision — the movement is 816–1296
samples, so precision was never the obstacle.

Absent these, the production state stays as it is:

```
drift correction = NOT IMPLEMENTED
resampling       = NOT IMPLEMENTED
P1               = untouched on material it cannot measure to validity
P2               = CLOSED — awaiting evidence, not development
```
