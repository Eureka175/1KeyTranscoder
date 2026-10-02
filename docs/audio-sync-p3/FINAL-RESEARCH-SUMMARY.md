# Audio Sync P3 — Final Research Summary

**One section per phase, P1 through P3.8. Nothing in this file is a recommendation, a ranking,
or a claim of success for the line as a whole.**

This document is a summary of research that is *incomplete on its own terms*. It records,
in the same voice as the underlying documents:

- one phase whose stated goal was **not achieved** (P3.5);
- one phase that is explicitly `PARTIAL` (P3.3) and one that is `PARTIALLY VALIDATED` (P3.8);
- one headline figure from P3.4 that is a **truncation artefact** and carries
  `do_not_quote: true`;
- several claims that were **retracted or corrected during the line**, by the line itself;
- a correction result that is **negative on the material measured**.

If this file reads as an unbroken chain of successes, it has failed. It is written to be read
alongside the per-phase documents, not instead of them.

---

## How to read the statuses

| label | meaning in this line |
|---|---|
| `VALIDATED` | proven on known truth, or against an instrument that shares no code with the thing being tested |
| `PARTIALLY VALIDATED` | true on the cases run, with the untested surface named |
| `PARTIAL` | the phase ran, part of its question is answered, and the remainder is recorded as unanswered |
| `BLOCKED` | cannot proceed without something outside the phase's control |
| `UNKNOWN` | not measured, and not inferable from what was measured |
| `KNOWN LIMITATION` | measured, bounded, and deliberately not fixed |
| `SUPPORTED` | positive evidence exists; the decisive test has not been run |
| `PLAUSIBLE` | consistent with the evidence; not distinguished from alternatives |
| `INCONCLUSIVE` | two mechanisms remain inseparable |
| `REJECTED` | tested and the claim did not survive |

A bare `FAIL` is never used. Where a suite reports failures, the failures are classified and
named.

## Coverage, including what this summary did not read

Read in full for this summary: `docs/audio-sync-p3/{README,measurement,validity,movement-resolution,validity-calibration,set-level-validity,planner-hard-gate,correction,rejected-window-measurement,second-measurement-geometry,safe-gap-policy,safe-gap-design,production-semantics,OPEN-DECISIONS}.md`;
`work/p3/{P3-FINAL-REPORT,P3.2-FINAL-REPORT,P3.3-FORENSIC-REPORT,P3.6-SAFE-GAP-POLICY-REPORT,P3.8-PRODUCTION-SEMANTICS-REPORT}.md`;
`work/p3/p38/reports/{critical-evidence-closure,failure_injection.json}`;
`work/p3/p38/agent_a_closure/suite.json`;
`work/p3/p38/coordinator/{implementation-handoff,evidence-freeze}.md`;
`work/p3/p38/semantics/gap_semantics.py` (the decision, plan and policy sections);
`work/p3/p34/gap_residual.py` (the truncation site);
`docs/audio-sync-p2/README.md`, `docs/audio-sync-p2/p1-baseline.md` (selected regions),
`work/p2/scripts/p1_regression_guard.py` (selected regions);
`work/p3/P3-FINAL-REPORT.md`, `work/p3/P3.2-FINAL-REPORT.md`.

**Not read:** `docs/audio-sync-p3/{trajectory,long-duration,synthetic-validation,synthetic-controls,c1306-validation,provenance}.md`;
`work/p3/P3.4-DIRECT-GAP-REPORT.md`; `work/p3/P3.5-SECOND-GEOMETRY-REPORT.md`;
`work/p3/p38/{coordinator/release-notes.md,reports/*.md other than those named}`;
`work/p3/p34/**` other than the truncation line; `work/p2/**` other than the three files
named. Where a fact from one of those appears below it is sourced through a document that is
on the read list, and the route is stated.

**Suite results quoted below are quoted from files that record them.** No experiment was
re-run for this summary, and no number in it was produced by this summary.

---

## P1 — Constant-offset channel sync

### Goal

Detect and correct a constant inter-channel offset in a multi-channel recording, and fail
closed — leave the track `untouched` — when the offset cannot be measured to the path's own
validity requirements.

### Method

A constant-offset estimator feeding an integer per-track shift. The path is shipped and in
production. Its regression guard (`work/p2/scripts/p1_regression_guard.py`) checks the corpus
decisions and a known-answer harness with an injected constant offset.

### Evidence

- `work/p2/scripts/p1_regression_guard.py` — the guard itself. On the corpus leg it asserts
  that the shipped path reports `non_constant` for CH1, CH2 and CH4; on the known-answer leg
  it asserts that a constant injected offset is `applied`, that a track was actually shifted,
  and that the shift magnitude matches the injection.
- `docs/audio-sync-p2/p1-baseline.md` — what the shipped, unmodified path decides on this
  material, and both guard results separately: the **historical observed** `27 PASS / 0 FAIL`
  (recorded before a test-artefact loss) and the **current** `27 PASS / 0 FAIL`, exit code 0,
  against recovered baseline artefacts.
- P1 regression guard **27 PASS / 0 FAIL** — the figure quoted by the P3 reports as the
  standing check that the constant-offset path has not regressed.

### Result

**Constant-offset correction = `VALIDATED` and shipped.** Guard **27 PASS / 0 FAIL**.

On this corpus, however, P1's own shipped behaviour is to refuse the material: all of CH1,
CH2 and CH4 are reported `non_constant`, and **no track reported `fixed`**
(`docs/audio-sync-p2/p1-baseline.md`). That refusal is P2's subject, not a P1 defect.

### Rejected assumptions

Not a claim about P1's machinery: the P1 regression guard's *inputs* were destroyed by a
P2.1 cleanup command and rebuilt by two full channel-sync runs; the original bytes are
**UNRECOVERABLE**, and the historical and current `27 PASS / 0 FAIL` results are kept
separate rather than merged (`docs/audio-sync-p2/README.md` §8). While the inputs were
missing the guard reported `0 PASS / 3 FAIL`, all three `MISSING INPUT` — **0 checks failing
on their merits**.

### Remaining limitation

The guard is deliberately **not** in `tests/selftest/`, because the framework's only
vocabulary is `PASS`/`FAIL` and an absent 50 GB corpus fixture would render exactly like a
functional regression (`docs/audio-sync-p2/README.md` §8). It is an on-demand check, and so
it does not run unless someone runs it.

---

## P2 — Why an automatic correction cannot ship on this corpus

### Goal

Determine whether the delay P1 refuses as `non_constant` is real, what it is made of, and
whether the production path should correct it.

### Method

A 120 s window measurement instrument over two recordings, a trajectory-model comparison, an
audit of the production confidence metric, and a correction prototype verified on a grid it
had not seen. Every claim graded by an evidence tier; final decisions permitted only on
Tiers 1–3.

### Evidence

- `docs/audio-sync-p2/README.md` — the closure entry point: the question, the findings, the
  decisions, the limits, the retractions, and the re-entry conditions. Its §7 is a table of
  **withdrawn claims**; its §6 lists the known limitations; its §10 states how this project's
  measurements fail — nine instruments or interpretations built, used to support a
  conclusion, and then found wrong, most producing complete and self-consistent numbers.
- `work/p2/final-evidence-audit.md` (referenced by `README.md` §5 as the full grading) —
  **not read for this summary**; the tier definitions and the summary verdicts are quoted
  from `README.md`.

### Result

P2 closed with a **negative** decision, not a deferred one:

- The corpus **does** contain large, real, time-varying inter-channel delay: 12 of 12
  trajectories across two recordings are non-constant, with movement **839–1294×** the
  1-sample (0.0208 ms) floor.
- It is **not** a common sample-clock drift (`NOT SUPPORTED BY CURRENT EVIDENCE` — tested and
  rejected, not merely unproven) and **not** a smooth linear or nonlinear ramp.
- **No per-window validity criterion survived validation.** This is named as the blocking gap.
- **No trajectory model class was established.**
- Correction benefit is not demonstrated: the previous prototype zeroed its residual only at
  its own geometry, and on an unseen grid carried **0.92×–1.65×** the original span with
  **+5.5 to +14.2 dB** boundary jumps. The prototype is **`REJECTED` as a product**.
- **`Confidence production fix = NO`. `Time-varying correction = NO`. `Resampling = NO`.
  `Production core changes = NONE`. P2 = CLOSED.**

`untouched` is recorded as the correct outcome, not as missing work: P1's fail-closed
behaviour prevents an erroneous measurement from being converted into an erroneous audio
edit, and the missing validity criterion forbids a *desired* action rather than licensing a
wrong one.

### Rejected assumptions

Seven claims were **RETRACTED** or not reproduced, and the distinction between the two is
kept: `NOT REPRODUCED` / `UNRESOLVED` are not collapsed into "false"
(`docs/audio-sync-p2/README.md` §7).

| old claim | status | replacement |
|---|---|---|
| Sub-sample support "NOT SUPPORTED" | **RETRACTED** | validated fractional measurement, ≤0.02 µs |
| `tools/p2/estimate_trajectory.py` "INVALID INSTRUMENTATION" | **RETRACTED** | re-run passes 8/8 self-test sections |
| "Uncertainty under-predicts real scatter by ~8.9×" | **RETRACTED** | no quantitative shortfall claim |
| "Observed scatter 14.08 samples" | **RETRACTED** | scatter ≤0.33 samples on plateaus |
| "Edge/false peak scores unexpectedly high confidence" | **NOT REPRODUCED** | highest confidence belongs to a correct answer |
| **Second-peak ratio as a validity criterion** | **RETRACTED** | falsified by a purpose-built single-arrival control |
| Correction prototype "verified at 0.000 ms residual" | **RETRACTED** | prototype `REJECTED` as a product |
| "110–1090× the 0.024 ms floor" | **RETRACTED** | wrong divisor; **839–1294×** the 0.0208 ms floor |

The confidence metric is a finding in its own right: on an 8-case known-truth matrix it
**never once separated a right answer from a wrong one**, and it rejected six correct
measurements. It separates wideband from narrowband content.

### Remaining limitation

`KNOWN LIMITATION`s, quoted: the 17.5–27.0 ms range is an **OBSERVED ESTIMATED RANGE**, not a
verified physical absolute delay; no validity criterion exists; no model class is
established; the integer instrument's resolution is 1 sample (0.0208 ms); the C1305
`CH3↔CH4` last ~940 s tail is unresolved and carries most of the variation; and the
historical 120 s-geometry spans are **window averages**, not the movement itself.

---

## P3.1 — Measurement layer, movement representation, validity recalibration

### Goal

Answer the question P2 left blocking — *when may a measured delay be acted upon* — and locate
the mechanism by which the correction loses movement, if it does.

### Method

A linear (non-circular) PHAT correlation with an explicit noise-floor guard; the target
window read `search` samples earlier so every lag in range is a full-overlap comparison;
fine-grid interpolation of the correlation peak for sub-sample delay; five intra-window
sub-window probes; a validity criterion with per-window reason codes; and an independent
truth series (`truth-cache.json`) computed with no PHAT weighting, compared layer by layer
against every artifact in the chain.

### Evidence

- `docs/audio-sync-p3/measurement.md` — units, sign, window geometry, correlation, refinement,
  precision, and the limits. Sub-sample accuracy **≤ 0.00003 samples** on an exactly
  constructed fractional delay (a frequency-domain phase ramp, no interpolation filter); the
  phase-increment path returns the residual scaled by **≈0.455** and is retained as a
  recorded diagnostic only.
- `docs/audio-sync-p3/validity.md` — the reason codes and what each input is for. The
  criterion rejects **every** negative control (silence, independent noise, an out-of-range
  delay, 30–110 Hz content, a single tone) and rejects **no** positive one (20/20 accepted,
  0 false rejects).
- `docs/audio-sync-p3/movement-resolution.md` — the six-layer comparison for `CH3→CH2` over
  478 windows: layers 1 to 7 agree **to within 0.05 samples**; layers 2 and 8 carry 924 and
  901 samples because they include the 376 windows the plan excludes.
- `docs/audio-sync-p3/validity-calibration.md` — the calibration table and the C1305 results:
  valid rates **19.5 / 21.3 / 51.7 %**, with `interp_vertex_at_edge` dominant on every pair.
- `work/p3/P3-FINAL-REPORT.md` — the frozen verdict and the re-entry conditions.

### Result

**Measurement = `VALIDATED`** (≤0.00003 samples on a constructed fixture). **Movement
representation = `VALIDATED`.** **Validity = `LIMITED`** — it refuses every negative control
but admits only 19.5–51.7 % of real windows, and its thresholds were calibrated *on* this
corpus.

Two findings of the phase:

1. **The plan did not lose the movement.** Layers 1–7 carry the same span to within 0.05
   samples. What happened is that the movement **was never in the windows the plan was
   allowed to use**: the accepted set spans 48 samples (1.000 ms) with lag-1 autocorrelation
   **+0.8581**, while the refused set spans 924 samples (19.250 ms) with **+0.3916** and
   steps of up to 856 samples between adjacent windows — statistically indistinguishable
   from the white-noise reference (−0.006). **The refused set is not a trajectory.**
2. **CH3→CH4 is the pair that moves** — 247 accepted windows (51.7 %), truth span 1185
   samples (24.688 ms), lag-1 **+0.9269** — and **P3 never corrected it**:
   `work/p3/correction/20260919_C1305/` contains correction artifacts for `ch3_to_ch2` only.
   The one pair with well-measured, continuous, 24.7 ms of real movement was measured and
   then not corrected.

### Rejected assumptions

**The mechanism paragraph of `work/p3/P3-FINAL-REPORT.md` §11 and of `README.md` was
retracted by P3.1 itself.** The claim was:

> the plan's span is 1.376 ms while the same windows carry 18.771 ms of movement at a
> different geometry, so ~93 % of the motion is below the plan's resolution.

It was self-consistent, supported by numbers P3 actually measured, and **wrong**. P3.1 Stage 1
was commissioned to test it with a single independent instrument and one reference series,
designed to fail loudly. Both source documents are left **unedited**; the retraction is
recorded in `movement-resolution.md`, which states outright that it **supersedes** those two
passages. The corrected mechanism is the one in `Result` above.

A second reading was corrected in the same phase: the **rival-ratio statistic** (94.1 % of
windows above 0.7, median 0.861) was measured on the **unweighted, 60-sample-masked**
correlation, so it counts smeared shoulder mass as rival arrivals — mass that PHAT weighting
suppresses. The double-arrival fixture measures where that matters: a secondary arrival at
**−6 dB** is accepted at every separation from 10 to 400 samples with at most **4.5 samples
of error**, and the gate refuses only at **0 dB**. The statistic's shape is right and it is
the right diagnostic; **its magnitude was read as stronger evidence than it is**.

### Remaining limitation

`UNCERTAINTY CALIBRATION = PARTIAL`. The probe scatter is validated as an uncertainty
*channel* on known truth, but the fitted residual sd (11.07 samples) exceeds the probe
scatter median (1.95 samples) by **5.7×**, so weighted trajectory fitting on it is **not**
justified. Also recorded: `min_peak_over_noise = 6` has no real-material support; the
criterion is **window-local** and never tests the admitted set as a series; and the
two-arrival case that makes the delay a genuine blend — a second arrival whose correlation
enters the overlap — has **no fixture**, so the criterion's behaviour there is **unmeasured**,
not measured-and-fine. `CH3→CH1`'s accepted set was independently measured at lag-1
**+0.0444** — white noise in time — and nothing in the criterion detected it.

---

## P3.2 — Set-level validity, C1306 cross-corpus validation, planner hard gate, correction revalidation

### Goal

Close the structural hole P3.1 named: a window can be crisp, high-confidence and correct
*within itself* while the sequence of such windows describes nothing. Build a set-level
criterion on C1305, validate it on C1306, and gate the planner hard enough that a defect
cannot be applied.

### Method

`work/p3/p32/set_validity.py` reporting lag-1/lag-2 autocorrelation, `distinct_value_ratio`,
step-size distribution over a stated deadband, reversal rate and big-step clustering, with
decision clauses and no threshold stacking. Eight ground-truth fixtures **rendered into
audio**, so the estimator sees a signal whose two arrivals genuinely switch. Multi-resolution
containment as a threshold-free replacement for the reversal-rate clause. A planner hard gate
that raises `PlanRejected` and exits 3. Then C1306 measured for the first time, and both
corpora run through the frozen stack.

### Evidence

- `docs/audio-sync-p3/set-level-validity.md` — the eight fixtures, the decision clauses, and
  the C1305 calibration table.
- `docs/audio-sync-p3/planner-hard-gate.md` — the checks, the derived 1.0-sample tolerance,
  the P0–P5 accept tests and the P6a–P6e rejections, **all 11 pass**.
- `docs/audio-sync-p3/validity-calibration.md` and `docs/audio-sync-p3/c1306-validation.md`
  (the latter cited, not read here) — C1305 calibrates, C1306 validates.
- `work/p3/p32/validation_manifest.json` (read-only, unmodified) — cited by
  `work/p3/P3.2-FINAL-REPORT.md` §8 for the frozen versions: window validity `p3-stage4-1`,
  set validity `p32-set-validity-1`, containment `p32-multires-1`, trajectory `p3-stage5-1`,
  planner `p32-planner-1`, correction `p3-correction-1`.
- `work/p3/P3.2-FINAL-REPORT.md` — §3–§11, §18.
- P1 regression guard **27 PASS / 0 FAIL** (quoted by both P3.2 and P3.8 reports).

### Result

- **Set-level validity = `VALIDATED`.** 8/8 fixtures, **zero false accepts, zero false
  rejects**. On C1305 the criterion **rejects the real pair CH3→CH1** (lag-1 +0.0662) and
  accepts CH3→CH2 and CH3→CH4 — the first time in this project that a criterion rejected a
  real accepted set for a stated structural reason. On C1306 the primary pair is `VALID`
  (lag-1 +0.9822); **cross-corpus agreement 2/3 pairs; C1306 cross-corpus validation =
  `PASS`**.
- **Containment = `VALIDATED`**, and it transferred *more strongly* to the corpus it had
  never seen: C1305 155/214 inside (72.4 %) against **C1306 292/292 inside (100.0 %)**.
- **Planner = `VALIDATED`** (11/11, tolerance **derived** from integer rounding, not chosen) and
  **planner-only transfer = `VALIDATED`** with the estimator absent from the call.
- **C1305 correction = `MIXED`** (RMS −12 %, span +19 %); **C1306 correction = no measurable
  effect** (span ratio 1.0000 on both instruments at both hops, RMS 0.9910 / 1.0101 — noise
  with opposite signs). **`Dual-corpus generalization = LIMITED`.**
- **`P3.2 correction = NOT VALIDATED`.** `PRODUCTION = NOT READY`.
- **Boundary audit = NOT RUN**, and reported as a gap rather than worked around: the plan
  files the audit needs were not persisted by the runs that produced the results, so the
  audit has no plan to read. The mechanism behind C1305's span widening remained **`UNKNOWN`**.

### Rejected assumptions

- **The reversal-rate clause was removed, and the corpus removed it.** It fired correctly on
  the synthetic switching controls and also **falsely rejected S2** — a fixture carrying one
  genuine 120-sample discontinuity. On C1305 it rejected all three real pairs. The correct
  response was to diagnose rather than to move the threshold, and the diagnosis found the
  statistic cannot carry a decision here: on CH3→CH4's accepted windows the median |step| is
  **1.7 samples** against an estimator window-to-window residual of **11–55 samples**, so
  per-step movement is *below the noise floor* and the sign of each step is set by noise; a
  white-noise Monte Carlo gives **0.500 ± 0.058**, so the 0.55 threshold sat **0.9 σ from a
  coin flip**. The clause is `RETIRED` and recorded as such in the manifest.
- **`S4` is a `FALSE ACCEPT` and is recorded as one, not repaired.** Direct measurement showed
  raising a rival from absent to 6 dB *above* the primary moves the estimator's answer by
  **6 samples**, independent of separation— so the fixtures' rival could not form an
  independent peak, `S3` reads INVALID because its series is a jitter field rather than
  because a delay hopped, and **synthetic negative-control coverage is `INCOMPLETE`**.
- **"11–55 samples measurement floor" = `RETRACTED` / NOT A NOISE FLOOR.** It came from fitting
  a line to a *moving* region. Measured against the fine grid over the same audio,
  repeatability is **0.76 samples** (C1305 0.68, C1306 0.85).
- **A frozen artifact's configuration was wrong and the frozen-config gate caught it.** The
  first C1306 launch used `--search-ms 31.25` (±1500) where C1305 used ±3000; the run was
  killed at window 261 and relaunched. Worse, **P3.1's C1305 2.5 s artifact used search 1500**
  against a frozen requirement of 3000, which made `baselines_to_beat.c1305_ch3_to_ch4_2p5s`
  (`1.0034 / 0.901`) **`HISTORICAL / PROVENANCE-INCOMPATIBLE`**. Re-measured, the accepted
  span is **26.704 ms instead of 11.090 ms** — a factor of 2.4 — the plan carries 1282 samples
  instead of 532, and the correction widens the span by **56 % instead of 0.3 %**.
  `validation_manifest.json` was **not edited**: the width it freezes was correct when
  written; what was wrong was an artifact produced a phase earlier.

### Remaining limitation

**That supporting control no longer supports the four-bad-windows explanation.** P3.1 §6.5
used the 2.5 s hop as the control that made "the 24.744 ms is four marginally-decided windows"
credible; at the frozen width the finer hop gives **26.704 ms — wider than the coarse hop**,
not narrower. The 10 s measurement is unaffected (it used 3000 throughout), but the
four-window attribution should now be read as **unsupported rather than established**.

Also remaining: the criterion has **no gap clause**, yet CH3→CH4 spans a 540 s hole on C1305
and 190 s on C1306 in which the delay demonstrably moves; `CH3→CH1`'s disagreement between
corpora is classified as a corpus property, which **retires it as a standing negative
control**; and the criterion is **not robust to trimming** — CH3→CH1 flips to VALID once its
2 % most extreme windows are removed.

---

## P3.3 — Boundary and unobserved-gap forensics

### Goal

Answer exactly one question: **why can this piecewise-constant correction lower an RMS while
widening a span?** P3.2 had named the mechanism `UNKNOWN`.

### Method

Rebuild each plan from the frozen measurements rather than re-running P3.2's driver; prove
the rebuilt plans are what was applied with a stated identity — for every boundary,
`residual_jump == meas_delta − plan_delta`; then classify every boundary, measure how far the
measured delay wanders inside each constant-shift run, and use C1306 as the low-amplitude
control.

### Evidence

- `work/p3/P3.3-FORENSIC-REPORT.md` — §4 the three-layer decomposition, §5–§7 the boundary
  audits at both hops on both corpora, §8 the staircase audit, §9 the convergent evidence.
- `docs/audio-sync-p3/correction.md` and `docs/audio-sync-p3/movement-resolution.md` — the
  plans and the chain the phase audited.
- Identity gate: **`PASS` on all four cases, 1 726 boundaries** (206 / 567 / 264 / 689).
  Set-level validity `VALID` on all four; planner hard gate `PASS` on all four.

### Result

**`P3.3 = PARTIAL`.**

- **Boundary mechanism = `SUPPORTED`** (unobserved-gap / invalid-region policy). **Every one
  of the seven largest residual jumps on C1305 10 s is class B5**, and the top two are a
  **670** and a **664**-sample plan change across a 10 s gap where the measurement moved
  **0.5** and **1.0** samples. The same boundaries appear at both hops with nearly the same
  magnitudes (t ≈ 97.5, 700, 1290, 4170, 4330), so they are not a grid artefact.
- **The mechanism is localised to invalid-region boundaries, and that localisation is the
  phase's positive finding:** the residual is **~0 by construction on carrying segments**
  (1.0 sample span, 0.29 samples RMS), so the widening *cannot* come from the windows where
  the plan carries a measurement. It must live in the windows where the plan carries none.
- **The staircase hypothesis was `REJECTED`**, measured directly with no synthetic fixture:
  across all four cases the wander inside constant-shift runs is **at most 1.1 %** of the
  trajectory span on the C1305 pair, and even its worst case (C1306 2.5 s) is 8.2 % of a
  2.95 ms trajectory. The plan's runs are too short relative to the trajectory's smoothness
  for a staircase to accumulate.
- **The finer grid does not fix it.** C1305 2.5 s has **45.5 %** clean boundaries against
  **14.6 %** and a 3.3× smaller median jump, yet its published span ratio is *worse*
  (**1.5634** against **1.1861**), because the same unmeasured boundaries dominate at both
  hops. The brief's hypothesis that 2.5 s is worse *because there are more boundaries* is
  **not supported**.
- **`B4 correction-boundary artifact = PLAUSIBLE`** (48 / 72 boundaries at the 10 s hops, 12 /
  13 at 2.5 s — real but inconsistent). **`E integer quantization = REJECTED`** (the plan
  matches its input span to ≤0.87 samples; quantisation is ±0.5).
- The phase was `SUPPORTED` rather than `CONFIRMED` **for a stated reason**: the one
  measurement that would close it — the residual on **refused** windows — was not available,
  because the window-level gate refused to measure there. The document says outright that
  naming a mechanism `CONFIRMED` on convergent indirect evidence when the direct measurement
  is missing is exactly the error P2 and P3 kept making.

### Rejected assumptions

- `staircase approximation = REJECTED as a material contributor` — direct measurement, ≤1.1 %
  of span.
- `E integer quantization = REJECTED`.
- The brief's §23 hypothesis ("2.5 s is worse because there are more boundaries") — **not
  supported**; each boundary is smaller and far more of them are clean.
- The `correct.py::verify` series **cannot** be differenced against the plan: it measures the
  corrected audio on its own grid with a different `read_window` alignment, so its values are
  **not on the same axis** as the plan's segments. This is why P3.4's framing was needed.

### Remaining limitation

`B6 UNKNOWN` is **56/206 (27 %)** and **158/567 (28 %)** of C1305's boundaries and is
attributed to nothing. **No percentage split between B, C and D is offered**, and the report
states none should be inferred. The `edge` policy has **never been compared against
alternatives on real data**. And **why C1305's trajectory is 8.4× C1306's is still
unexplained** — recorded as the more interesting upstream question.

---

## P3.4 — Rejected-window measurement

### Goal

Supply the one measurement P3.3 said was missing: the correction residual **on refused
windows**, where the plan actually has no measurement.

### Method

Read the delay the measurement layer already stored for refused windows — a refusal is a
**flag on a finished measurement**, not a suppression of one — and difference it against the
plan's carried shift on the same axis. Every row tiered by a quality definition whose
boundaries are percentiles of the accepted population of the same corpus, not numbers chosen
in the phase.

### Evidence

- `docs/audio-sync-p3/rejected-window-measurement.md` — §1 the structural finding, §2 the
  tier table, §3 the direct gap residual, §4 the two hotspots, §5 the mechanism
  classification.
- `work/p3/p34/gap_residual.json` (referenced by the doc; the truncation site read directly
  in `work/p3/p34/gap_residual.py:320,329`).
- `docs/audio-sync-p3/safe-gap-policy.md` §6–§6.3 — the independent recomputation, three
  routes agreeing.
- `work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` §10.3b, §10.5–§10.5c.
- `docs/audio-sync-p3/movement-resolution.md` §2 — the independent truth series used by the
  tier boundary derivation.

### Result

- **`unobserved-gap / invalid-region policy = CONFIRMED` as the dominant residual source.**
  The basis is deliberately narrow and is stated as such: **direct same-axis measurement** —
  the refused estimate differs from the plan's carried shift by **92.5 samples** at t = 700 s
  and by hundreds elsewhere; median gap residual **8–10 samples** against **0.25–0.30** on
  carrying segments; gap p95 ≈ 756. P3.3's `SUPPORTED` is upgraded to `CONFIRMED`, **with a
  stated limit**.
- The tiering separates over four orders of magnitude: `production_valid` median |err|
  **0.25–0.27**; `refused_quality_ok` **1.80–2.92**; `refused_vertex_at_edge` **3.00–8.42**;
  `refused_low_quality` **21.36–657.96**.
- **The limit is carried by a case where the instrument points the other way.** At t = 100 s
  on C1305 10 s the refused window *agrees* with the plan (717.2 against 721) and both sit
  ~115 samples from the next accepted value, because **the delay really does step inside the
  gap**. Two of P3.3's largest boundary jumps are of this kind.

### Rejected assumptions

**The `51.2 %` neighbour-range figure is a truncation artefact and must not be quoted.** It is
recorded with `do_not_quote: true`.

`work/p3/p34/gap_residual.py` stores a **display-truncated** list:

```python
gap_runs.sort(key=lambda g: -abs(g["plan_error_at_start"]))   # line 320 -- DESCENDING
result["gap_runs"] = gap_runs[:60]                            # line 329
```

So the persisted `gap_runs` is the **top 60 runs by |plan error|** while the aggregate
`gap_run_verdicts` covers **all** runs. C1305 2.5 s has 91 runs, so **31 runs holding 177
refused windows are absent (641 persisted vs 818 aggregated)**. Because the sort puts the
largest errors first, the dropped runs are disproportionately those where the carried value
was *closest* to its own diagnostic — the **inside** ones, **159 of the 177** missing
windows. Summing the persisted list therefore **deflates** the inside fraction:

```text
487/331 = 59.5 %   P3.4's aggregate over ALL runs   <- CORRECT
328/313 = 51.2 %   the truncated top-60 subset      <- TRUNCATION ARTEFACT, do not quote
```

This is a **biased** answer, not merely an incomplete one, and anyone recomputing the number
from the persisted artifacts will hit it. The artefact is recorded in
`work/p3/p36/reports/p34-neighbour-verification.json` under `known_truncation_artefact` with
`do_not_quote: true`.

**"The carried shift is itself the culprit" was `REJECTED` (Agent B's H11).** P3.4 §9 argued
that "an estimate inside that range cannot be the error's source; the plan's carried shift
is". **The carried shift IS one endpoint of that range**, so inside-ness says nothing about an
estimate's distance to it. Tested directly, pooled over four cases (906 inside windows at
±2): **36.4 % nearer the carry, 46.0 % nearer the arrival** — and **the direction flips by
corpus**: C1306 leans carry, **C1305 leans arrival, and C1305 is where the 670/664-sample
events live**. Within a 3-sample deadband, **43.4 % of refused windows are within that
deadband of neither endpoint**, and only 10.4 % / 13.8 % uniquely support carry / arrival.

**A claim that including a 4th case "widens the band" was `WRONG`.** P3.4's prose band covers
**3 of its 4 cases** (C1306 10 s is absent from its table). C1306 10 s is **60.64 %** inside,
which falls **inside** the 3-case range of **58.60–67.47 %**, so the outside band is
**numerically unchanged at 32.5–41.4 %**. Including the fourth case changes nothing
numerically; the fair criticism is a **presentation gap, not a reproduction failure**.

And the line corrected itself here **repeatedly**: P3.6 reported P3.4's figure as
unreproducible **twice, with two different explanations, and both were wrong**
(`work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` §10.5):

| attempt | claimed cause | verdict |
|---|---|---|
| 1 | "the stored counts are per-run, not per-window" | **WRONG** — every persisted run satisfies `inside + outside == n_refused`, 0 mismatches in 196 runs |
| 2 | "a different quantity, not a contradiction" | **WRONG** — same quantity, truncated subset |
| **3 (Agent B's)** | **the persisted `gap_runs` list is truncated** | **CORRECT** |

So this one figure drew **three** wrong statements from the line's own verification, in three
different directions: unreproducible, a different quantity, and a widening that does not
occur. Three independent routes then agreed on the correct value: P3.4's own stored verdicts,
Agent B rebuilt from the raw measurements (matching all 196 persisted run values, 0
mismatches), and the coordinator's `verify_p34_authoritative.py`.

### Remaining limitation

**The tolerance supplies half the headline and the figure must never be quoted without it.**
Pooled over 1 473 refused windows in 227 bounded gaps: **±0 = 30.4 %**, ±0.5 = 38.8 %,
**±1 = 53.2 %**, **±2 = 61.5 %**, ±3 = 68.4 %, ±5 = 73.5 % — **the slack supplies 31 of the
61.5 points**, and the median exceedance beyond zero tolerance is only **2.29 samples**. The
endorsed reading is that **±1** is what the measurement supports (repeatability 0.76 samples
plus at most 1 sample of rounding) and **±2** is defensible only on the separate argument
that a 30 s window integrates *moving* audio; **the honest figure is the band ~30–68 %; a
single "59.5 %" is not defensible.**

What rescues the qualitative claim is that the falsifiable null runs the other way: a straight
ramp between the two neighbour values plus noise at the measured repeatability would put
**99.9 %** inside at ±2 and **75.6 %** even at ±0. The observed 61.5 % / 30.4 % sit **far
below** those, so the refused estimates track their neighbours' range **worse than smooth
interpolation would**. Both facts must be reported together; each alone misleads.

Also: `refused_low_quality` estimates are **unusable** (655 samples median error) and are
excluded from every quantitative claim; the in-gap truth for the 33–41 % of refused estimates
outside their neighbours' range is **not independently established**; and Agent B's standing
objection is recorded — the refused estimates come from the **same estimator, on the same
axis, with the same failure modes** as the accepted ones, so using one to judge the other
assumes an independence that has not been established.

---

## P3.5 — Second measurement geometry

### Goal

Separate **genuine in-gap movement** from **estimator failure** in the disputed windows, using
a structurally different measurement of the same audio — "Geometry B" — with two hard gates
before any corpus conclusion: **§76**, does B add information at all; **§77**, can it separate
estimator failure from genuine movement on known truth.

### Method

Two candidates, both keeping the frozen 30 s window and the frozen ±3000 search. **B1**: swap
the reference and target roles and expect `B(t) ≈ −A(t)`. **B2**: read the target `shift =
60 000` samples (1.25 s) earlier so the Hann taper rides asymmetrically and the reciprocity
symmetry is broken. 40 windows per case audited, stratified (8 large, 8 medium, 8 small, 16
random, fixed seed `20261010`, selected **before** any B value was computed).

### Evidence

- `docs/audio-sync-p3/second-measurement-geometry.md` — the measurement, the defect report,
  the limitations.
- `work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` §2 — the P3.5 baseline as P3.6 inherited it.
- `docs/audio-sync-p3/safe-gap-policy.md` §7 — the in-gap truth still `UNKNOWN` at the end of
  P3.6.
- Outputs named by the doc: `work/p3/p35/second_geometry.py`,
  `work/p3/p35/synthetic/geometry_validation.json`,
  `work/p3/p35/{c1305,c1306}/disputes_*.json`, `work/p3/p35/disputes/summary.json`
  (**not opened for this summary**; the values below are quoted from the document, which is
  on the read list).

### Result

**The phase did not achieve its goal. `Status: PARTIAL`. Neither candidate passed the §76
hard gate, so no second geometry was established and the disputed windows remain
unresolved.** This is written as the Result because it is the Result; there is no second
instrument at the end of it.

**B1 — the reciprocal: `DEGENERATE`.** It returned `|A + B| = 0.000` samples on **all 80
audited real windows** and on every synthetic case, to machine precision. That is not
agreement, it is an **algebraic identity**: two windows of equal length carrying the same
symmetric Hann taper give a correlation that is **even about the peak**, so exchanging the
roles can only mirror it. A measurement forced to agree cannot detect that A lost the arrival
— the one thing §76 requires — and it fails for a **structural reason no amount of data would
have changed**.

**B2 — time-shifted placement: no new information.**

| synthetic case | true delay | `B_raw − A` |
|---|---:|---:|
| T0 constant 1140 | 1140.00 | **0.000** |
| T1 ramp 0→120 | 50.00 | **−0.41** |
| T4 fast ramp 0→900 | 371.38 | **−3.08** |

`B_raw` already tracks the delay; the difference grows with the movement across the 1.25 s
shift, which is what two windows that far apart *should* disagree by if both are correct. **B2
is A re-measured 1.25 s away, on audio that shares 96 % of its support**, and it fails in the
same windows for the same reasons. **B2 fails §76 as well.** Gap truth remained **`UNKNOWN`**.

The single sentence the document offers for why both failed: a second geometry can only be
independent if it observes the disputed quantity with **different temporal support**, and both
candidates keep the frozen window and the frozen search.

### Rejected assumptions

- **The premise that a second geometry can be independent while the window length and search
  range stay frozen — `REJECTED` by both candidates.** Genuine independence requires changing
  something the freeze fixes: a different window length, a non-overlapping measurement, or a
  different band — and that is a **configuration change, which the freeze forbids without a
  new phase**.
- The library does contain one ready-made alternative (`_probe_subwindows`, five 15 s
  sub-windows), and P3.5 explicitly **declines to treat it as a solution**: at 15 s the peak
  is twice as wide and its reliability has **never been characterised**.

**A defect the phase reports rather than hides:** the in-script conversion produced values
inconsistent with the fresh probe — the probe measured `B_raw = 1140.00` for T0 where the
script reported `B_delay = 61140.00`, a factor of −1 apart. The author changed the sign
constant twice and cleared a stale `__pycache__`, the two paths still disagreed on the same
computation, and **stopped rather than keep flipping a sign until an output looked right**.
That decision is why the phase is `PARTIAL` rather than `COMPLETE`. The defect does not affect
the §3 conclusion, which is measured on raw values and needs no conversion; it affects only
the display of B on A's axis, and it is recorded so a later phase does not inherit a silently
wrong offset.

### Remaining limitation

- **No second geometry exists at the frozen configuration.**
- **The B2 axis sign is unresolved.**
- **Only 40 windows per case were audited.**
- **The disputed-window conclusion rests on A alone, which is precisely the thing under
  question.** P3.5 did not improve on P3.4 here.

What P3.5 did **not** change, and it says so in a table: P3.4's `unobserved-gap /
invalid-region policy = CONFIRMED` is **unchanged**; P3.4's `staircase = REJECTED` is
**unchanged**; P3.4's quality tiering is **unchanged**; the 33–41 % disagreement is **still
unresolved**. Nothing about the gap mechanism was invalidated; what failed is the instrument
P3.5 hoped to build, not the finding it was meant to test.

---

## P3.6 — Safe gap policy under irresolvable measurement uncertainty

### Goal

A deliberately narrower question than P3.1–P3.5: when the true delay inside a gap cannot be
measured, **which gap-filling behaviour avoids creating a larger artificial correction error
than the uncorrected state?**

### Method

Five policies at a fixed definition (`work/p3/p36/policies.py`, 55/55 selftest;
`work/p3/p36/synthetic.py`, 31/31): **298 known-truth synthetic scenarios** in four families
(C1 182 general, C2 24 placement, C3 32 real gap masks with constructed truth, C4 60
discontinuity inside a real gap), run through a restart-safe runner, **plus** an
observable-behaviour comparison on the two real corpora, **plus** two independent research
agents with non-overlapping write scopes. The corpus-realistic regime is **frozen before any
policy result**, by a script that reads no P3.6 result.

### Evidence

- `work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` — §4.00 the freeze, §4.1 the metric result, §4.2
  the in-gap error table, §4.3 the worst cases, §5.1 the counter-example, §6–§7 the real
  corpora, §9 the envelope, §10 the cross-check, §10.5–§10.5c, §11 the assumptions, §12 the
  unknowns.
- `docs/audio-sync-p3/safe-gap-policy.md` — the same results in document form.
- `work/p3/p36/policy_audit.md`, `work/p3/p36/reports/{real-policy-comparison,policy-behavior-matrix,safety-envelope,agent-cross-check}.md`,
  `work/p3/p36/agent_{a,b}/report.md`, `work/p3/p36/real_gap_profile.json`,
  `work/p3/p36/synthetic/coordinator_results.json` — all named by the report; the four
  reports in `p36/reports/` and the two agent reports were **not opened** for this summary.

### Result

- **`fractional = NOT NEEDED`** and **`resampling = NOT JUSTIFIED`** — both `DECIDED` and both
  carried into `OPEN-DECISIONS.md` as decisions that are **not** open. Nothing in the evidence
  justifies either; the integer plan already fails for a resolution reason a finer corrector
  cannot see.
- **The decisive negative result: the nomination metric cannot see the difference.** The
  metric the P3.6 brief nominated as the known-truth safety measure — `policy_jump` /
  `artificial_jump` — is **policy-invariant at two-sided boundaries**, by algebra: every
  policy leaves accepted windows at their own `rint(delay)`, so `shift_before` and
  `shift_after` are fixed by the measurements and **`artificial_jump = policy_jump −
  true_move` is identical for all five policies**. Verified: **0 of 8 136 policy-pairs
  differ**, and **0 of 4** real cases show any divergence in the boundary-jump series. The
  maximum `artificial_jump` observed anywhere is **5.0 samples**, on a problem whose errors
  reach **3 271 samples**, with **0 / 0 / 0 / 0** events over 50 / 100 / 200 / 500-sample
  thresholds for **every** policy (denominator 31 858 gap windows per policy). **The analysis
  was therefore re-run with `error_in_gap` as the primary metric, and the wrong metric's
  failure is documented rather than deleted.**
- **How badly the wrong metric would have misled, quantified.** Of the **1 010 scenarios with
  a two-sided boundary**, the count where `artificial_jump < 10` samples **while** the in-gap
  error exceeds 200 samples: `current` **164**, `left_hold` **164**, `right_hold` **197**,
  `linear` **88**, **`no_correction` 517** — worst case **3271.7 samples of in-gap error
  accompanying an `artificial_jump` of 1.9**. A study using that metric as *the* safety measure
  would have scored **`no_correction` among the safer policies while its in-gap error is the
  largest of the five**, inverting the ranking it was meant to produce.
- **A hidden discontinuity costs the full step.** C1305's longest real gap (530 s): step
  amplitude 670 → in-gap max error **670** under every carry policy and under `no_correction`;
  **`linear` 336**. No policy in the set avoids this; `linear` splits it in half.
- **Gap duration does not predict jump size.** C1306 has 175–180 s gaps and never exceeds a
  **79**-sample shipped-policy jump; C1305 has a 530 s gap and reaches **670**. A duration
  threshold would threshold the wrong variable.
- **More measurement precision does not help.** Sweeping observation noise over σ = 0.0 / 0.76
  / 2.0 moves the in-gap error by a few samples out of 100–1 900. The gap problem is missing
  information, not noisy information.
- **`gap policy = dominant residual source` `STANDS`** on its magnitude evidence (medians 3–10
  samples, p95 314–759, against 0.25–0.30 on carrying windows) — direct, same-axis,
  independent of the neighbour-range rule.
- **No policy is scored, ranked or recommended.** The candidates are separable only by which
  *kind* of trajectory they fail on, which is a choice about a prior on in-gap movement.

### Rejected assumptions

- **`policy_jump` / `artificial_jump` as the known-truth safety metric — `REJECTED` by
  construction and verified exhaustively** (§4.1 above). The metric is not useless — it is the
  right instrument for a policy that manufactures a step where the truth did not move — but it
  is **not an in-gap error measure**.
- **"Carry when the gap is narrow, interpolate when it is wide" — not supported.** The
  comparison between `linear` and a carry policy **changes sign depending on where inside the
  gap the discontinuity falls**: at a gap's **closing edge** a forward carry is exactly right
  (`current` error **0** vs `linear` **600** at amp 1200), and in the gap **interior**
  `linear` is about **half** the carry error. **The discriminator is unobservable.**
- **A generator defect that made part of the sweep measure nothing.** Agent A found by probing
  the generator that `slow_ramp`, `medium_ramp` and `fast_ramp` were the **same formula**
  (`amp * t / T`) and therefore **bit-identical** at any fixed window count: a sweep that
  believed it was varying movement rate across three classes was varying nothing. Fixed,
  verified numerically (amp = 142: final values **71.0 / 142.0 / 142.0**), pinned by three
  fixtures, and **all 298 coordinator results re-run**.
- **A second defect followed immediately and is the more dangerous one.** After the fix, the
  re-run reported `298 PASS in 0.1 s` — it had **silently reused every result from the
  defective generator**, because `experiment_id` was unchanged and the runner had no way to
  know the inputs behind that id had moved. `runner.py` now records a `generator_hash` and
  refuses to reuse a cached result whose generator changed (selftest **11/11**, including a
  fixture that changes the hash and asserts the re-run happens). The pair is stated together:
  **a sweep can be silently measuring nothing, and a cache can silently hide that it was.**
- **"σ = 0.76 produced ~3× more in-gap error than σ = 0.0" — retracted.** A pre-revision
  artefact; Agent A retracted it itself after the generator fix.
- **`no_correction` is not a hedge, and `right_hold` is not a safer carry.** Worst in-gap error
  observed: `current` / `left_hold` **2376 samples (49.5 ms)**; **`right_hold` 110 484 samples
  (2301.8 ms)**; `linear` **1189**; **`no_correction` 110 484 (2301.8 ms)**. `no_correction`
  is **exactly optimal on a quiet gap and worst-possible on a moving one**, and it is the only
  policy whose worst case is **unbounded by the trajectory's step size and instead by its full
  magnitude**.
- **Agent B's attack A3 — "the finer hop explains the worse span ratio via boundary count" —
  `REJECTED`**: total |residual jump| is not conserved between hops (ratio 1.6×). Attacks A1
  and A4 returned `INCONCLUSIVE`; A2 and A5 `PLAUSIBLE`. No averaging was done and no majority
  was chosen; the one agent/coordinator conflict (a 1-sample linear rounding case) was resolved
  **by reading the frozen implementation**, not by preferring an agent — and `policies.py` was
  right while the agent's harness was wrong.

### Remaining limitation

- **No best policy. No theoretical bound** — every number is a maximum *observed*; the envelope
  JSON carries `"kind": "empirical_max_observed"` so it cannot be mistaken for a bound.
- **No method to verify a gap decision in the field.** That is the finding, not a gap in the
  work: the quantity a policy needs is the quantity a gap withholds.
- **The in-gap truth on the real corpus remains `UNKNOWN`.**
- **`A7` is the assumption most likely to be wrong and it is the one that matters:** "in-gap
  movement is independent of the gap's existence" is `UNVERIFIED`. If refusal and movement are
  correlated — and the criterion refuses *unstable* windows — then real gaps contain **more**
  movement than synthetic gaps of the same length, and every in-gap error reported is a
  **lower bound**, not an over-estimate. It cannot be settled from the corpus, because settling
  it requires the in-gap truth.
- **The `>200 s` gap-duration bin is `LOW SAMPLE` (n = 4)**; no distributional conclusion is
  drawn from it, and it is still the bin the largest real events live in.
- **C1306 is not a low-amplitude copy of C1305.** On C1305 the large steps are overwhelmingly
  at gap edges (Σ|step| **1974** at edges vs **552** inside accepted runs); **on C1306 most of
  the step budget sits inside accepted runs** (242 vs **743**), where no gap policy applies at
  all. The corpora differ **in kind**.
- **Audibility is not modelled.** A 670-sample (14 ms) error's audibility was not measured; the
  20 ms crossfade converts a plan step into a ramp but does not change the alignment error.

---

## P3.7 — Safe gap semantics under irresolvable uncertainty

### Goal

P3.6 established that the hidden trajectory cannot be recovered and that no policy can change
the magnitude of the step across a large gap — only which edge it lands on. P3.7 therefore
asks the only question that remains answerable: **when the system knows it does not know the
in-gap delay, how should it represent that, so that the unknown is not quietly converted into
an invented trajectory?**

### Method

Six candidate policies emitting **two** things per window — `shift_samples` and a `resolved`
flag — with the state sequence defined as a **pure function of the resolved mask** and
re-derived in the plan constructor, which refuses to exist if the supplied states disagree.
Safety invariants I1, I2, I3(r), I5, I6 tested on a 132-case known-truth matrix, on the four
frozen real plans, and on an audio fixture. I6 (chunk invariance) tested at **12 chunk sizes**,
comparing the exact state sequence, the exact shift sequence and every boundary.

### Evidence

- `docs/audio-sync-p3/safe-gap-design.md` — §0 the two-line result, §1 what P3.6 forbids, §2
  the states, §4 the invariants, **§4.0 the precise form of the `current` finding**, §4.1 the
  two invariants redefined after measurement, §5 the chunk-invariance constraint, §6 the
  property table, §7 coverage, §8 what is not established.
- `docs/audio-sync-p3/safe-gap-policy.md` — the P3.6 layer it builds on.
- `work/p3/p37/{production-design-proposal.md,state_machine.py,semantics.py,known_truth.py}` and
  `work/p3/p37/{state-machine/q0_q5.json,synthetic/known_truth_matrix.json}` — named by the
  design document and by `work/p3/P3.8-PRODUCTION-SEMANTICS-REPORT.md`; **not opened** for this
  summary.

### Result

**The sharpest result of the phase, in the document's own two lines:**

```text
the applied correction can be IDENTICAL between two policies
the epistemic claim cannot
```

On the audio fixture, `hold` and `current` produce **byte-identical shift sequences** across a
gap containing a genuine **174-sample** delay change — the previous value carried for four
windows, then one jump. They differ **only** in that `hold` labels those windows
`UNRESOLVED_GAP` (plus one `REENTRY`) while `current` labels every window `TRUSTED`. On the
real corpora the same two policies differ in applied shift on **0.4–0.5 %** of windows — the
leading gap only. **The labelling fix is therefore separable from the numerical behaviour.**

- **`current`'s state machine is `CORRECT`** — see `Rejected assumptions` for the exact form,
  because this is the finding most likely to be overstated.
- **The chunk-invariance finding is architectural, not numerical.** I6 **failed** for four of
  five candidates when each chunk was decided independently, and the failures were diagnostic:
  six successive bugs, each a real property of the policies. The decisive one: with chunk size
  1 the **shift** sequence is exactly chunk-invariant while a per-chunk **state** cannot be,
  because a one-window chunk cannot know a gap preceded it and so cannot label itself
  `REENTRY`. State must be computed as a pure function of the resolved mask over the whole
  stream.
- **The resulting context requirement is bounded:** per stream and per gap, an exact streaming
  implementation needs the stream's accepted mask, the current gap's two endpoint **values**,
  and the current gap's global **extent**. Nothing unbounded — but it must be **given**, since
  a chunk cannot infer it.
- **And `no_correction_gap` needs none of it** — chunk-invariant with no context at all.
- **No column dominates.** Each candidate is the better answer in a different row, and the rows
  that separate them are decided by the position of a discontinuity inside the gap — which is
  not observable. Compliance counts are **deliberately not totalled**.

### Rejected assumptions

**The precise form of the `current` finding — do not overstate it.** The 132-case known-truth
matrix separates three checks that must not be merged
(`docs/audio-sync-p3/safe-gap-design.md` §4.0):

| check | `current` |
|---|---|
| state sequence matches the mask-derived expectation | **132/132 correct** |
| never labels a refused window `TRUSTED` | **132/132 correct** |
| `resolved` flags backed by a measurement | **1/22** (only the no-gap case) |

```text
state sequence correct in every case    : True
no fake TRUSTED window in any case      : True
`resolved` flags correct in every case  : False
```

**`current`'s state machine is `CORRECT`.** What fails is the separate `resolved` claim, which
over-claims on **1 of 22**. This is a **semantic over-claim, NOT an algorithmic error** — and
not a claim that `current` is unsafe in every respect.

Two further rejections and one redefinition, all measured:

- **I2 is `current`'s failure and nobody else's:** "a window without a trusted measurement must
  not be reported as resolved" — **`current` FAILS on all four real plans**; all candidates
  PASS.
- **P3.4's supporting inference is `REJECTED`** (§6.3 above): in-range estimates are nearer the
  *arrival* than the *carry* (46.0 % vs 36.4 % pooled), so **"the carry is the culprit" is not
  established and must not be treated as root cause**. P3.4 is inherited **in split form, and
  the split must not be re-merged**:

```text
gap policy = dominant residual source        STANDS   (direct, same-axis error evidence)
carried shift is itself the culprit          REJECTED (its own criterion does not license it)
```

- **Two invariants were redefined after measurement, and that is part of the result.** **I3**
  was first stated as *"every shift change must be attributable to a trusted boundary"* — which
  **`linear` violates by construction**, since an interpolating policy changes the shift at
  every interior window, and reporting it as a defect would be wrong because the change *is*
  supported by the two trusted endpoints. I3 was narrowed to *"no direction reversal inside a
  gap"*, and the redefinition is **recorded rather than hidden**. **I6** proved the most
  informative invariant for an architectural reason (§5).

### Remaining limitation

- **No candidate is proven safe.** All verdicts are "satisfies the tested invariants on the
  tested cases". Nothing is a mathematical guarantee, and the word is not used.
- **The invariants are not complete.** They constrain *invented structure*; they say nothing
  about whether a policy is close to the truth, because the truth is unavailable.
- **The real-corpus replays carry no error measurement.** In particular `jumpMax` is
  **identical for all six policies** on every case (**670 / 763 / 79 / 79** samples) — the value
  is set by the accepted windows, exactly as P3.6 established.
- **The design document's own §8 lists two things as unfinished at the time it was written:**
  the P3.7-owned synthetic known-truth arm, and end-to-end validation of the state machine
  (Q0–Q5 is *specified* there and not yet executed). **Both were closed in P3.8** — the matrix
  is recorded in P3.8 as 132 cases and Q0–Q5 as PASS at 12 chunk sizes. The design document was
  not edited, so a reader of it alone would see the earlier state; this summary records the
  later one.
- **Coverage and safety are reported separately and never compressed into one number.**
  `no_correction_gap` has the lowest correction coverage of any candidate and the highest
  safety compliance on the invariants that concern invented structure. **That trade is stated,
  not netted off. It is not a defect and it is not an advantage; it is what the policy is.**

---

## P3.8 — Executable gap semantics, and the Critical Evidence Closure

### Goal

Turn P3.7's validated gap semantics into a **production-shaped, testable implementation**, and
then close the untested surface two independent reviewers found: validate the production
window/hop configuration (A), form a real estimator → measurement → plan → apply chain (B), and
pin the window/centre coordinate contract (C).

### Method

A semantics engine outside the production tree with a selftest, an engine-level failure
injection, a frozen-plan reproduction gate, a new overlap-geometry suite for the production
geometry `window ≠ hop`, a window-grid seam measurement, a memory check **with a positive
control that actually allocates**, an independent adversarial reviewer (Agent B) and an
independent closure implementer (Agent A) with separate write scopes, a hash-pinned evidence
freeze, and a quarantine-not-delete archive pass.

### Evidence

- `docs/audio-sync-p3/production-semantics.md` — the semantics as built: the two dimensions kept
  separate, state as a derivation, bounded explicit context, application mirroring production,
  and the defect record (§6, §6a, §6.5, §6.6, §6.7, §7.1).
- `work/p3/P3.8-PRODUCTION-SEMANTICS-REPORT.md` — §1 the suites, §1.4 the seam, §1.6 what the
  estimator does at a change, §2 the defects, §3 what is not established, §4 the repository
  state.
- `work/p3/p38/reports/critical-evidence-closure.md` — the A/B/C closure with per-section
  statuses, §B2a the E2E failure-injection failures, §B3 the seam, §B5 the offset across
  models, §C4 the `KNOWN AMBIGUITY`.
- `work/p3/p38/agent_a_closure/suite.json` — the closure suite, with per-module totals, the
  engine hashes before and after, and both failures recorded with `classification: REJECTED`.
- `work/p3/p38/failure_injection.py` and `work/p3/p38/reports/failure_injection.json` — the
  engine-level injection: **`n_cases: 30`, `n_silent_wrong_or_unexpected: 0`**, outcome split
  `VALUE_OK 13 / RAISED 17`.
- `work/p3/p38/semantics/gap_semantics.py` — the decision and plan types read directly:
  `CorrectionDecision(shift_samples, state, window_id, center_s, source, after_gap_at)` with
  the invariant that a `measurement`-sourced decision cannot be `UNRESOLVED_GAP`;
  `CorrectionPlan` re-deriving the states and raising if they disagree with the mask;
  `OFFLINE_ONLY = frozenset({"next", "linear"})`.
- `work/p3/p38/coordinator/implementation-handoff.md` and `evidence-freeze.md` — the handoff
  specification and the hash baseline.
- `docs/audio-sync-p3/provenance.md` — **tracked**; the SHA-256 of every participating file.

Suite results recorded at the end of the phase:

```
P3.8 engine selftest                     100 PASS / 0 FAIL
P3.8 engine failure injection             30 cases, 0 silent-wrong  (VALUE_OK 13 / RAISED 17)
P3.8 frozen-plan reproduction gate        PASS, 0 mismatches, 4 plans
P3.8 overlap geometry (window != hop)     26 PASS / 0 FAIL
P3.8 window-grid seam                     measured; 478 vs 481 on C1305
P3.8 memory (with working controls)       22.0 MB flat from 120 s to 600 s
Agent A closure suite                     262 PASS / 2 FAIL (the 2 ARE the seam)
P1 regression guard                       27 PASS / 0 FAIL
project unit suite                        626 PASS / 0 FAIL
project toolchain suite                   642 PASS / 0 FAIL
```

### Result

**Overall verdict: `PARTIALLY VALIDATED` — NOT READY TO ENABLE.** The semantics
infrastructure is ready, the state machine is validated, chunk safety is validated, the
production window geometry is validated, and the default path is provably unchanged. **The
estimator chain is `PARTIALLY VALIDATED` on synthetic fixtures and has never been applied to
real audio.** Policy selection is `DEFERRED`; the `window_of` convention is a `KNOWN
AMBIGUITY`. `production integration = DEFERRED`.

- **A = production window/hop: `VALIDATED`.** **30.0 s window / 10.0 s hop** confirmed from code
  and from all four `run_meta.json`; `center_s(w) = w·10 + 15` s; the 2.5 s-hop runs are a
  finer-hop control, **not** a production configuration.
- **B = estimator → measurement → plan → apply: `PARTIALLY VALIDATED`**, because a real
  integration fact had to be found and declared before the chain could run at all.
- **C = `window_of` / `center_s` contract: `VALIDATED`, with one `KNOWN AMBIGUITY`.**
- **Frozen-plan reproduction gate `PASS`, 0 mismatches, 4 plans** — the executable semantics
  have **not drifted** from the shipped behaviour.
- **The classification of the 2 failures is the point.** They are the **measured seam**, and
  they are `classification: REJECTED` in the artifact — asserted as **expected failures rather
  than hidden**.
- **`hold` versus `current` on real corpora:** the only numerical difference is the leading gap
  — **2 windows at 10 s hop, 8 at 2.5 s hop** (0.4–0.5 %). Everywhere else the two policies
  apply **identical corrections** while making **different knowledge claims**: `current`
  reports all **231 / 931 / 106 / 422** gap windows as resolved measurements. This is the P3.7
  finding reproduced on real material at the implementation level.

### Rejected assumptions

- **Four silent-context defect classes were found and fixed, and one of them is the most
  serious defect of the line.** An unsigned / `INT64_MIN` / `1e30` shift previously
  **mis-aligned audio 5 samples the wrong way with no error raised** — the magnitude guard was
  `int(np.abs(arr).max()) > MAX_ABS_SHIFT_SAMPLES`, and two two's-complement paths made it
  impossible to trip:

```text
np.array([2**64 - 5], dtype=np.uint64)
    -> .astype(int64) WRAPS to -5   ->  |−5| = 5  ->  passes
    -> the stream is returned aligned 5 samples the WRONG WAY, no error, no warning

np.array([-2**63], dtype=int64)          # INT64_MIN
    -> np.abs(INT64_MIN) is itself NEGATIVE (overflow)
    -> the comparison can never be true  ->  passes

np.array([1e30])                         # float path
    -> .astype(int64) is undefined       ->  silently becomes INT64_MIN
```

  The guard now runs **before** any signed cast and **without `np.abs`**; all three cases raise,
  and the exact boundary is verified (±2³¹ accepted, ±2³¹+1 raises).
- **The defect family, stated once:** *a chunk silently substituting a guess for context it
  cannot see.* Four instances — the `shifts[-1]` wrap, the missing gap endpoint, the integer
  overflow, and the truncated context mask. **Every instance produced a plausible number and no
  exception. The fix is never a better default — it is to make the required context explicit
  and mandatory, so absence raises.** Three of the four were found by an adversarial reader,
  **not by the module's own tests**.
- **Three further defects were found in P3.8's *own verification code*, all the same shape: a
  check that reported success while measuring nothing.**

| # | defect | disposition |
|---|---|---|
| **F9** | `selftest.py` with no argument printed its docstring and **exited 0** | fixed |
| **F10** | `memory_check.py` rewrote its own helper with a handle `GetProcessMemoryInfo` rejects — every case errored, the verdict fell through to `UNKNOWN`, and the script **exited 0** | fixed |
| **F11** | the memory check's baseline included its own source array, so the full-output control read 0.00 MB and the PASS **could not fail** | fixed |

  **F9–F11 appeared three times in the verification code rather than in the code being
  verified**, and the report calls this the strongest argument in the phase against treating a
  green suite as evidence on its own.
- **A claim that was wrong and then fixed: peak memory was not duration-independent.** The
  module docstring and an earlier revision of the document asserted it; **Agent B measured it
  false** and the coordinator reproduced it — the output was built as one full-length array, so
  peak RSS tracked the *output size*. Agent B measured **10.66 → 119.56 MB** across a 60×
  duration sweep against `shift_stream` sitting **flat at ~10 MB** — a regression against the
  exact property P1 was built to guarantee. Fixed by adding a streamed output path. Two wrong
  turns are recorded because both would have shipped silently: charging the source array's own
  allocation to the call, and a `np.zeros` control that is calloc-backed so an untouched
  allocation reports a **zero** working-set delta — **the control measured nothing.**
- **A second silent failure, a 173× slowdown.** `np.asarray(src)` on a `RawStream` **does not
  fail** — it walks the sequence protocol one sample at a time. Correct values, no error:
  **86×** slower at 10 s (0.352 s vs 0.003 s) and **173×** at 60 s (3.073 s vs 0.018 s). Fixed
  by handling the stream shape explicitly.
- **A "principled" clamp was reverted.** A source-window-bounded clamp looked correct — a
  window should only place samples it covers — but it **contradicts `out[n] == in[n + shift]`**
  and made the two paths disagree at window boundaries (**40 differing samples on a 60 s
  stream**). The stored rule is the contract; the clamp is now a regression in the selftest.
- **The `nan` that had been recorded as a possible two-phase blocker was a caller error.** The
  prior probe passed equal-length reference and target windows with `lag0 = 0`, collapsing the
  admissible lag set to one sample; the estimator refused **correctly**. The `nan` is
  **signal-independent** — white noise, band-limited noise, an impulse train and a tone all
  reproduce it — so the earlier explanation ("an impulse train is spectrally periodic") **does
  not survive**. The phase's own words: **the estimator was working correctly the whole time.**
- **The chain could not be *formed*, which is less flattering than "unverified".** The
  measurement layer emits only windows that fit fully; the applier needs a shift for every
  sample. On C1305 that is **478 measured against 481 needed**; on C1306 **414 against 417**.
  **The plan as produced cannot be handed to the applier.** Neither layer is wrong — both do
  what they document — but the interface was missing, and describing the previous state as
  merely "unverified" was too generous: it was **unformable**.
- **An earlier fixture put every window in the wrong place.** A version of the offset script
  built the fixture on a window-relative time base and passed a relative index as `start`,
  making `center_s` meaningless; **its first-run numbers were not evidence and were
  discarded.** The independent reviewer independently hit a different version of the same
  problem in its own fixture and also moved to the absolute base.
- **Two claims in the phase's own audit documents were overstated** and were corrected inline
  with the correction recorded: a bound generalised beyond its actual condition, and a "differs
  at every point" that held only for the rule the prose named, not the one the script computed.
  The corrected bound: the 15 s offset is bounded by one hop **only at `win = 3·hop`**, the
  production ratio; for `win > 3·hop` the offset **exceeds one hop** (26 violations across 36
  `(win, hop)` pairs).
- **Six of the 50 E2E failure-injection checks failed, and all six are mis-specified
  expectations in the test, not engine behaviour** — each classified `REJECTED` rather than
  silently relaxed. The substantive result inside them is kept: the `hold`-versus-`current`
  difference is **asymmetric across stream edges** — they differ at a **leading** gap and
  **agree** at middle and trailing gaps, which is sharper than "leading gap only" and comes
  from an independent fixture. Note also the scope limit P3.8 records on itself: the earlier
  suite's *"30 cases, zero silent-wrong"* did **not** cover integer-overflow boundaries, a
  defaulted `GapContext.base`, or `window < hop`. **A suite that passes is not the same as a
  suite that is complete.**

### Remaining limitation

The phase's own §3 table, quoted:

| # | not established | classification |
|---|---|---|
| 1 | a plan has never been applied to real audio and re-measured | `UNKNOWN` |
| 2 | end-to-end cost including decode and encode (measurement alone ~3–8 s per 30 s window) | `UNKNOWN` |
| 3 | the `window_of` convention (latest-start vs nearest-centre; symmetric in error, disagree at every tested point) | `KNOWN AMBIGUITY` |
| 4 | the 15 s attribution offset is accepted, not resolved | `OBSERVED SEMANTIC MISMATCH` |
| 5 | the estimator's refusal rate on material other than two concerts | `KNOWN LIMITATION` |
| 6 | no policy ranking; the four options are UNRANKED | `DEFERRED` by design |
| 7 | the implementation is untracked (`work/` is gitignored); hashes are a tombstone, not a backup | `KNOWN LIMITATION` |

Two of these deserve their numbers. The estimator's refusal rate leaves a coverage gap that is
a product question no phase has answered: C1305 ch3→ch4 is **51.7 %** valid windows and other
pairs are **19–21 %** — the refusals are the estimator working correctly, but the timeline they
leave unmeasured is most of the timeline. And the cost is measured only on raw float streams:
**3 988 s for 478 windows (C1305)** and **1 596 s for 414 windows (C1306)**.

**The largest remaining structural weakness of the whole line:** the entire P3.8
implementation lives under gitignored `work/`. `docs/audio-sync-p3/provenance.md` records the
SHA-256 of every participating file, which **proves which bytes a result came from and does
not preserve them**. If `work/` is deleted the implementation is gone and the hashes are a
tombstone, not a backup. During P3.8's own audit the engine was rewritten at least four times,
once into a state that failed `ast.parse`. That is why provenance was pinned at all — and it is
also why a hash of a file nobody backs up is a weak guarantee.

---

## The line, stated once

```text
P1    constant-offset correction            VALIDATED and shipped; guard 27 PASS / 0 FAIL
P2    correction on this corpus             NOT SUPPORTED; P2 CLOSED — no validity criterion
                                            survived, no model class established
P3.1  measurement / movement representation  VALIDATED; VALIDITY LIMITED (19.5-51.7 %);
                                            the "93 % below resolution" mechanism RETRACTED
P3.2  set-level validity, containment,
      planner, C1306 validation             VALIDATED / PASS / VALIDATED;
                                            correction NOT VALIDATED; generalization LIMITED
P3.3  boundary forensics                    PARTIAL; gap-policy localisation SUPPORTED;
                                            staircase REJECTED
P3.4  rejected-window measurement           gap policy = dominant residual source CONFIRMED;
                                            "the carried shift is the culprit" REJECTED (H11);
                                            the 51.2 % figure is a TRUNCATION ARTEFACT
P3.5  second measurement geometry           GOAL NOT ACHIEVED — B1 DEGENERATE,
                                            B2 no new information; in-gap truth UNKNOWN
P3.6  safe gap policy                       fractional = NOT NEEDED; resampling = NOT JUSTIFIED;
                                            the nomination metric is policy-invariant (0 of
                                            8 136 pairs differ); no policy ranked
P3.7  safe gap semantics                    the applied correction can be identical while the
                                            epistemic claim cannot; `current`'s state machine
                                            CORRECT (132/132), its `resolved` flags over-claim
                                            on 1 of 22 — a SEMANTIC OVER-CLAIM, not an
                                            algorithmic error
P3.8  executable semantics + closure        PARTIALLY VALIDATED — NOT READY TO ENABLE;
                                            four silent-context defects fixed, one of which
                                            mis-aligned audio 5 samples the wrong way with no
                                            error; three further defects in P3.8's OWN
                                            verification code, all of the same shape
------------------------------------------------------------------------------
production = NOT READY
no policy ranked, no policy recommended, no threshold adjusted to make a corpus pass
```

**What the line is actually evidence of, stated without a silver lining.** It built a
measurement instrument that works, a validity criterion that refuses almost everything, and a
correction whose own verification it failed. It located the mechanism precisely and could not
measure it. It built an executable semantics layer that is correct about what it does not know,
and **it has never applied a plan to real audio and re-measured**. Every phase that could
correct itself did, repeatedly and in public, and that is the strongest thing in the record —
but self-correction is a property of the process, not a result. The results are the ones in the
tables above.
