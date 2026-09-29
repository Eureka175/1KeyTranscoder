# Audio Sync P3 — Long-Duration Time-Varying Delay Correction

**STATUS: research complete for this corpus — `P3 NOT JUSTIFIED` at this resolution;
production gate NOT reached; `core/` unmodified.**

> P2 is closed and is not reopened by this work. P3 asks a different question, on the
> same material, with long-duration testing as a first-class instrument rather than a
> cost to avoid.
>
> Branch `feature/audio-sync-timevarying-correction`, created from the P2 closure commit
> `bd8f1f0`. **No production file was touched at any point** (`git status --porcelain core/`
> is empty). No push, no merge.

## The result in one paragraph

The measurement layer works — sub-sample delay is recovered to **≤ 0.00003 samples** on a
fixture whose answer is known by construction, and the per-window validity criterion refuses
every negative control and no positive one. One recording (C1305, 80 minutes) was then
measured end to end with a criterion recalibrated on real material. The **correction still
fails its own verification**: on the best-behaved pair, verified on a grid the plan never
saw, the residual span ratio is **1.000** by the main instrument and **0.830** by a
structurally independent one — no improvement, from a rewrite of 100 % of the samples. The
mechanism is specific: the plan's span is 1.376 ms while the same windows carry **18.771 ms**
of movement at a different geometry, so **~93 % of the motion is below the plan's
resolution**. That is P2's finding, reproduced one order of magnitude finer.

---

## 1. Scope

```text
IN  : measure delay(t) reliably -> decide per-window validity -> model the trajectory
      -> prototype correction in the order integer -> fractional -> resampling
      -> verify independently -> check P1 compatibility -> production gate
OUT : fixing clock drift (a hypothesis, not the objective), reopening P2,
      touching core/ before the gate, benchmarks, the 137 sweep
```

The research object is **`delay(t)`, the inter-channel delay as a function of time** —
not clock drift, not sample-rate mismatch, not thermal drift. Any physical
interpretation must be earned by independent evidence, and none is assumed here.

## 2. Relationship to P2

```text
P2  = why an automatic correction cannot be shipped on this corpus, today
P3  = what a correction would have to look like to be trustworthy
```

P2's conclusions stand and are not revised by P3. If P3 produces evidence that
contradicts a P2 claim, the P2 documents stay exactly as written and the difference is
recorded as new evidence here — never by editing history.

What P3 inherits from P2, as *findings* rather than as open questions:

| P2 finding | Consequence for P3 |
| --- | --- |
| The delay is real, time-varying, and far above the floor (839–1294× one sample) | The motion is worth measuring; precision was never the obstacle |
| No per-window validity criterion survived | **P3's first technical gate.** No correction work may begin before it exists |
| No trajectory model class was established | The trajectory stage must be evidence-driven, not assumed |
| A per-window integer plan built on a 120 s grid leaves sub-grid motion uncorrected | The plan's *resolution* is part of the design, not an implementation detail |
| The second-peak ratio looked perfect on the corpus and was falsified by a control | Every validity input must beat a known-truth **negative** control before it is trusted |

## 3. Current approach

Strict stage order, and no step may be skipped:

```text
Stage 1  measurement layer            <- done enough to measure, one calibration defect open
Stage 2  per-window validity          <- criterion present; negative-control validation pending
Stage 3  synthetic known-answer       <- suite built and running; has already caught 10 defects
Stage 4  long-duration measurement    <- not started
Stage 5  trajectory                   <- not started
Stage 6  integer correction           <- not started
Stage 7  fractional correction        <- only if integer is insufficient
Stage 8  resampling                   <- only if a persistent systematic slope demands it
Stage 9  independent verification
Stage 10 P1 compatibility
Stage 11 production gate
Stage 12 final decision
```

The estimator never touches audio. Measurement and correction are separate objects, and
the correction prototype does not exist yet.

## 4. Current evidence

Everything below is reproducible from `work/p3/` and is recorded with the run that
produced it. Nothing in this section rests on a P2 number.

### 4.1 Established

| Claim | Evidence |
| --- | --- |
| A linear (non-circular) PHAT correlation with an explicit noise-floor guard measures the delay of a window, with the search range as a real parameter rather than a fixed ±250 ms | `measure.py`; the target window is read `search` samples earlier so every lag in range is a full-overlap comparison |
| Delay sign convention holds end to end: `delay > 0` = target arrives later, correction is `out[n] = in[n + d]` | synthetic known answers recovered with the correct sign at `0, ±1, ±137, ±1200` samples |
| Sub-sample delay is recoverable, and **exactly**, by fine-grid interpolation of the correlation peak | **≤ 0.00003 samples** against an exactly-constructed fractional delay (frequency-domain phase ramp, no interpolation filter); constant and fractional delays from 0.25 to 1200 samples all within 0.0001 samples |
| A fixture self-test can detect a mis-built fractional fixture | routing an integer-valued delay through the windowed-sinc construction and comparing against an exact integer shift gives 0.00000 samples, as it must |
| A per-window validity criterion rejects **every** negative control while rejecting **no** positive one | silence, independent noise, an out-of-range delay, 300 Hz-limited content and a pure tone are all refused; 20/20 positives accepted, 0 false rejects |
| The synthetic suite has real discriminating power | it has already falsified **ten** instrumentation defects that all produced plausible numbers (see `synthetic-validation.md`) |

### 4.2 Open

| Item | State |
| --- | --- |
| Per-window numerical uncertainty | **OPEN.** `probe_sigma_samples` is a conservative placeholder, not a calibrated error bar. Stage 6 needs a real σ, so this is the gate between Stage 5 and Stage 6. |
| Multi-arrival behaviour inside the search range | **UNMEASURED.** Three controls were originally declared REJECT and were wrong (the second arrival is attenuated ~1/6000 inside the overlap, so the first arrival's delay is well defined). The declaration is corrected; the re-run is recorded. |
| `min_peak_over_noise = 6` on real material | **UNCALIBRATED.** Its only support is two synthetic controls sitting at ≈ 6. |
| Trajectory, correction, verification, production | Not started, by design |

**No long-duration run has been performed yet**, and that is now a scheduling choice rather
than a blocker: Stage 3's gate is passed, so Stage 4 is permitted. It is deliberately taken
as the next step rather than mixed into this one, because the synthetic suite's ten caught
defects are the reason to trust a corpus run and not to interleave it with calibration work.

## 5. Current limitations

1. **No per-window error bar.** The criterion gives an accept/reject verdict, not a
   calibrated uncertainty. A trajectory fit needs the latter, so this is the gate between
   Stage 5 and Stage 6.
2. **The validity thresholds are calibrated on synthetic material only.** `peak/noise = 6`
   has two synthetic controls near it and nothing else behind it.
3. **Window length trades time resolution against measurement precision.** A 30 s window
   averages intra-window motion, and the motion of interest is of the same order. The
   sub-window probe detects that a window is not scalar-valued; it does not recover *when*
   inside the window the delay was what.
4. **The 17–27 ms range from P2 remains an OBSERVED ESTIMATED RANGE**, not a verified
   physical absolute delay. P3 must not upgrade that evidence level without new independent
   support.
5. **Nothing here is about real material yet.** A synthetic pass is permission to measure.

## 6. Production status

```text
core/channel_sync.py     unmodified
core/sync_estimate.py    unmodified
core/sync_fix.py         unmodified
git status --porcelain core/    (empty)
```

No production work is authorised before Stage 11. Nothing in P3 so far has needed it.

## 7. Test status

| Stage | Artefact | State |
| --- | --- | --- |
| Measurement | `work/p3/measure.py` | built; **validated** on the exact phase-ramp fixture (≤ 0.00003 samples) |
| Synthetic known-answer | `work/p3/synthetic_kat.py`, `work/p3/out/synthetic_kat.json`, `work/p3/show_kat.py` | built; 33 cases; 20/20 accept, 0 false rejects, all negative controls refused |
| Fractional diagnosis | `work/p3/diag_fractional.py` | built; separates estimator error from fixture error |
| Long-duration | — | **not started** — next step |

Long-duration testing is authorised in P3 and is the point of it. It has not been used yet
because the synthetic gate had to pass first — using it earlier would have produced numbers
that no one could interpret, and the ten defects the synthetic suite caught are the reason
to believe that.

## 8. Documents

| File | Content |
| --- | --- |
| `README.md` | this file — scope, approach, evidence, limitations, status |
| `measurement.md` | the measurement pipeline: units, sign, window geometry, correlation, refinement, precision, limits |
| `validity.md` | the per-window validity criterion, its recalibration on real material, and what is not validated |
| `synthetic-validation.md` | the 29-case known-answer suite and the ten instrumentation defects it caught |
| `trajectory.md` | Layer A/B/C separation, candidate models, and selection by BIC |
| `long-duration.md` | the C1305 end-to-end measurement: coverage, ranges, and what the window length costs |
| `correction.md` | the integer prototype, its audit, and the verification that fails |

Research record (not shipped, under `work/p3/`):

| File | Content |
| --- | --- |
| `P3-FINAL-REPORT.md` | the 17-section final report and the frozen verdict |
| `experiments/<stem>/` | Layer A measurements, summary, run provenance |
| `correction/<stem>/` | plan, apply record, verification |
| `out/` | KAT result and log, calibration samples, P1 guard re-run |

## 9. Frozen verdict

```text
Measurement:  VALIDATED   <=0.00003 samples on a constructed fractional fixture
Validity:     LIMITED     rejects all negative controls; accepts only 19.5-51.7 % of real
                          windows; thresholds calibrated ON this corpus
Trajectory:   LIMITED     best model leaves 0.168-1.32 ms RMS against a ~0.12 ms bound
Correction:   NEGATIVE on this material — no improvement on an unseen grid, from either
                          measurement path, at 100 % of samples rewritten
Production:   NOT READY
```

The re-entry conditions — a plan at the resolution of the motion, a criterion calibrated off
this corpus, a calibrated per-window σ, a genuine two-arrival fixture, and a second
recording measured end to end — are listed in `work/p3/P3-FINAL-REPORT.md` §17.
