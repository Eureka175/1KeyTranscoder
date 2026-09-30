# Movement-Scale Resolution

**Phase: P3.1. Status: `MOVEMENT REPRESENTATION = VALIDATED`, with one defect found and
located. This document supersedes §11's mechanism paragraph of
`work/p3/P3-FINAL-REPORT.md` and the "one paragraph" result summary of
`docs/audio-sync-p3/README.md`. Neither of those files is edited; the difference is
recorded here.**

---

## 1. Why this document exists

P3 closed with a specific mechanical explanation for its negative correction result:

> the plan's span is 1.376 ms while the same windows carry 18.771 ms of movement at a
> different geometry, so ~93 % of the motion is below the plan's resolution.

That sentence is self-consistent, is supported by numbers P3 actually measured, and is
**wrong**. P3.1 Stage 1 was commissioned to test it, and the test was designed to fail
loudly rather than to confirm: a single independent instrument, one reference series,
compared against every artifact.

The claim is retracted. What replaces it is below.

---

## 2. The answer to "which layer loses movement"

There are six layers between the raw audio and the corrected audio. Here is what each
carries, for the pair P3 corrected (`CH3 → CH2`, reference CH3, 478 windows of 30 s at a
10 s hop, C1305):

| # | layer | instrument | span | fraction of the widest |
| --- | --- | --- | --- | --- |
| 0 | raw audio | — | — | — |
| 1 | measurement, all windows | `measure_window` (`raw_measurements_ch3_to_ch2.jsonl`) | 66.05 samples = 1.376 ms | 7 % |
| 2 | **independent truth, all windows** | argmax lag, both windows read from the same source sample, no PHAT | **924 samples = 19.250 ms** | 100 % |
| 3 | validated (gate accepted) | `measure_window`, `valid == true` | 66.05 samples = 1.376 ms | 7 % |
| 4 | **independent truth, gate accepted** | as layer 2, restricted to accepted windows | **48 samples = 1.000 ms** | **5 %** |
| 5 | trajectory (`piecewise_constant`, 6 segments) | `trajectory.py` | 66.05 samples = 1.376 ms | 7 % |
| 6 | plan (`shift = −round(delay)`, `edge`) | `build_plan` | 66 samples = 1.375 ms | 7 % |
| 7 | applied correction | `apply_plan` | 66 samples = 1.375 ms | 7 % |
| 8 | verification `before` | `correct.py::verify` | 901 samples = 18.771 ms | 98 % |

**Layers 1 to 7 agree to within 0.05 samples.** Nothing between the measurement and the
applied correction loses any movement at all. Layers 2 and 8 carry 924 and 901 samples
because they include the 376 windows that layers 3 to 7 exclude.

The one-sentence answer: **the plan did not lose the movement; the movement was never in
the windows the plan was allowed to use.** And layers 2 and 4 together show that the
windows it was allowed to use contain 1.000 ms, not 19.250 ms:

| set | n | span | lag-1 autocorr | mean step per 10 s | max step |
| --- | --- | --- | --- | --- | --- |
| CH3→CH2 accepted | 102 | 48 samples (1.000 ms) | **+0.8581** | 1.17 samples | 46 |
| CH3→CH2 refused | 376 | 924 samples (19.250 ms) | +0.3916 | 65.04 samples | 856 |
| white-noise reference, refused sd | — | — | −0.006 | 176.0 samples | — |

A relative displacement of two microphones sampled every 10 s is continuous: high
lag-1 autocorrelation and small steps. The accepted series is exactly that. The refused
series steps by up to 856 samples — 6.1 m of relative travel in 10 s — between adjacent
windows, and its autocorrelation sits at the white-noise reference. The refused set is
not a trajectory.

The estimator reached the same conclusion from inside, on evidence this analysis did not
use. Its five sub-window probes measure the delay five times *within* each 30 s window:

| region | `probe_step_max` median | `probe_step_max` p90 | valid rate |
| --- | --- | --- | --- |
| first 3600 s | 3.33 samples | 9.00 samples | 27.8 % |
| last 1200 s | **283.34 samples** | **680.40 samples** | **1.7 %** |

A delay that moves 283 samples inside a single 30 s window and 856 samples between
windows is not being tracked badly. It has stopped being a single number.

### The structural cause

`ambiguity.py` measured the rival arrival in every window: the strongest local maximum
more than 60 samples from the winner.

```
rival ratio (runner-up / winner), 60-sample mask, no PHAT weighting:
  all 478 windows      median 0.861   p90 0.972   frac(>0.7) = 0.941
  accepted 102 windows median 0.891   p90 0.988   frac(>0.7) = 0.951
  refused  376 windows median 0.856   p90 0.959   frac(>0.7) = 0.939
```

In **94.1 %** of windows a rival arrival reaches at least 0.7 of the winner's height, at
a median separation of 138 samples. This is a live recording in a reverberant room; every
window contains a second arrival nearly as strong as the first. When the two are
comparable the argmax is free to choose either, and it does.

`interp_vertex_at_edge` — 266 of the 376 refusals — is the validity criterion reporting
exactly this. P3 read that reason code as an instrumentation or resolution problem. It is
the criterion correctly identifying a window with no scalar delay.

> **Correction added in P3.1 §7.** The 0.861 figure is measured on the **unweighted**,
> 60-sample-masked correlation, so it counts smeared shoulder mass as rival arrivals —
> mass that PHAT weighting suppresses. The double-arrival fixture measures where that
> actually matters: a secondary arrival at **−6 dB** is accepted at every separation from
> 10 to 400 samples with an error of at most **4.5 samples (0.094 ms)**, and the gate
> refuses *only* at **0 dB**, i.e. two arrivals of equal strength. So 0.861 does **not**
> mean 94 % of windows contain a competing arrival at −1.3 dB. The statistic's shape is
> right and it is the right diagnostic; its magnitude was read as stronger evidence than
> it is. The claim this section needs — that the refused windows are not a trajectory —
> does not depend on it and is carried by the continuity test above.

---

## 3. The defect that does exist

The chain from measurement to correction is faithful, and the fixture that proved it
also measured its transfer function:

| injected ramp span (known) | span emitted by the chain | recovered |
| --- | --- | --- |
| 48 samples (1.000 ms) | 34.0 samples | 0.708 |
| 300 samples (6.250 ms) | 213.7 samples | 0.712 |
| 600 samples (12.500 ms) — full chain | 580.0 samples | **0.967** |

Two independent spans, the same ratio to three digits at the small scales. The 0.71 factor
is a *measurement* effect at small spans, not a planner effect: at a realistic movement
scale the full chain recovers 96.7 ∕ %, and the plan's emitted span matches its input
trajectory span to within rounding (66.0 vs 66.05 on CH3→CH2; 1188.0 vs 1187.7 on
CH3→CH4). `build_plan` now reports that comparison as a hard self-check and refuses to
emit a plan that fails it.

### 3.1 The constraint that does bind: the movement's timescale

Measured independently of the estimator, by recomputing the truth series at the **same
30 s window** and changing only the hop (`work/p3/p31/timescale/timescale.json`):

| hop | mean \|step\| (quiet region) | lag-1 ac | mean \|step\| (volatile region) | lag-1 ac |
| ---: | ---: | ---: | ---: | ---: |
| 10.0 s | 5.39 | 0.8454 | 185.49 | 0.4688 |
| 5.0 s | 2.76 | 0.9224 | 101.76 | 0.7023 |
| 2.5 s | 1.38 | 0.9613 | 64.35 | 0.8193 |
| 1.0 s | 0.56 | 0.9845 | 25.50 | 0.9290 |

Mean |step| tracks the hop ratio (1.826, 1.590, 2.523 against hop ratios 2.0, 2.0, 2.5)
and autocorrelation climbs toward +1: **the delay is a continuous function of time and it
is being resolved, not sampled.** `sd` and `span` are unchanged across a 10× change in
hop, which is what a continuous function looks like when it is resolved rather than what
noise looks like when it is averaged.

But the step distribution has a persistent heavy tail at every hop — 13 of 59 steps
exceed 200 samples at a 10 s hop, and **20 of 599 still do at a 1 s hop**, with a maximum
step of **1095 samples (22.8 ms) at every hop**. Finer sampling does not touch those. They
are real discontinuities.

So the honest description is: **a continuous, well-resolved trajectory carrying a minority
of real jumps.** A piecewise-constant shift schedule with one value per 10 s cannot express
a discontinuity, which is why a correctly-signed correction on CH3→CH4 removes 12 % of the
RMS (the smooth majority, which the plan does track) while the span ratio stays at 1.186
(the extremes, which it does not).

The direct test is a finer hop: `--hop-s 2.5` gives four times the schedule resolution.

---

## 4. What is excluded, mechanism by mechanism

The brief asked for the failure mechanisms to be separated rather than lumped. Each was
tested against an instrument that does not depend on the estimator's model:

| mechanism | verdict | test that decides it |
| --- | --- | --- |
| **A. measurement resolution** | **excluded** | The estimator places a delay to 8e-5 samples on a known fixture and its probe scatter is 1.95 samples on accepted corpus windows. The rival is 0.86 of the winner at 138 samples' separation — no plausible resolution shortfall reaches that far. `fixture_diag.py`, `noise-channel.json` |
| **B. validity gate** | **excluded as a fault; working** | It refused exactly the windows whose delay is not a scalar, using an intra-window statistic (probe scatter) independent of the continuity test, and the two agree. `continuity.py` |
| **C. trajectory model** | **excluded** | `piecewise_constant` leaves 0.168 ms RMS against a 0.231 ms per-window noise floor. The model is already at the noise. |
| **D. quantisation** | **excluded** | ≤ 0.5 sample = 0.0104 ms against a 1.376 ms span: ≤ 0.8 %. |
| **E. plan construction / edge policy** | **excluded for the span** | Plan span 66.0 vs validated span 66.1: ratio 1.000. `correct.py::selftest` covers the policies separately. |
| **F. reference mapping** | **excluded** | Same pair, same reference, same direction, byte-identical `read_window` geometry. `work/p3/p31_geometry_audit.md` §2. |
| **G. invalid-region handling** | **excluded for the span; open for correctness** | The `edge` policy carries the nearest valid shift across gaps. That is defensible for the span and unproven as a signal-processing choice; Stage 8 owns it. |

---

## 5. The measurement noise floor

The `noisechannel.py` fixture validates the estimator's own `probe_step_max` as a
per-window uncertainty channel against a known answer: over 20 repeat measurements of a
fixture whose delay is exactly 1140 samples, the estimator returned 1140.000 with an
error standard deviation of 8e-5 samples and a probe scatter of 0.008 samples.

Applied to the corpus accepted windows:

| quantity | CH3→CH1 | CH3→CH2 | CH3→CH4 |
| --- | --- | --- | --- |
| accepted n | 93 | 102 | 247 |
| truth span, accepted | 529 samples (11.021 ms) | 48 samples (1.000 ms) | 1185 samples (24.688 ms) |
| truth lag-1 autocorr, accepted | **+0.0444** | +0.8581 | **+0.9269** |
| truth mean step per 10 s, accepted | 11.05 | 1.17 | 8.87 |
| `probe_step_max` median (first 3600 s) | — | 2.36 | — |
| fitted residual sd, quiet region | 16.83 samples (0.351 ms) | 11.07 samples (0.231 ms) | 55.16 samples (1.149 ms) |
| span / residual sd, quiet region | 2.9 | 4.3 | 5.0 |

**`UNCERTAINTY CALIBRATION = PARTIAL`.** The probe scatter is validated as an uncertainty
*channel* on known truth, but it is not yet a calibrated σ for corpus windows: the fitted
residual sd (11.07 samples) exceeds the probe scatter median (1.95 samples) by a factor
of 5.7, so the residuals contain structure the probe does not see. Weighted trajectory
fitting on this number is **not** justified, and the brief's §30 applies.

---

## 6. Two findings that redirect P3.1

**CH3→CH4 is the pair that moves.** 247 accepted windows (51.7 %), truth span 1185
samples (24.688 ms), lag-1 autocorrelation +0.9269, mean step 8.87 samples per 10 s, and
a residual of 12.9 samples against an 1185-sample span — a signal-to-noise ratio of about
92. Its plan carries `shift_span_samples = 1188` against a measured span of 1187.7:
ratio **1.0002**. This is a movement the estimator can track, in a quantity large enough
that no resolution argument is needed.

**P3 never corrected it.** `work/p3/correction/20260919_C1305/` contains correction
artifacts for `ch3_to_ch2` only. The one pair with well-measured, continuous, 24.7 ms of
real movement was measured and then not corrected. Stage 9 of the P3.1 brief — "integer
correction re-test" — selects this pair, and it is the highest-value experiment available
in this phase.

**CH3→CH1 fails differently.** Its *accepted* series has lag-1 autocorrelation +0.0444:
the windows the gate admits do not form a continuous trajectory either. So the gate is
admitting a set that is not ordered in time, which no continuity test currently guards
against. This is a validity defect, not a measurement defect, and Stage 4 owns it.

---

## 7. Provenance

```
branch  feature/audio-sync-timevarying-correction
HEAD    b4582b6   core/ unmodified (git status --porcelain core/ empty)
input   work/p2/out/raw/20260919_C1305_a{1..4}.f32   (reused; no re-extract)
corpus  20260919_C1305  230 750 520 samples  4807.30 s  478 windows
truth   work/p3/p31/geometry/truth-cache.json   (full-rate pass, 451 s, cached)
```

Scripts and their outputs are tabulated in `work/p3/p31_geometry_audit.md` §1. Every
number above is reproducible from them.
