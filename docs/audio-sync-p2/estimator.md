# Audio Sync P2 — Estimator Prototype

> Status of the P2 estimator prototype, its validated measurement performance, and
> the one property it does not yet have. Prototype:
> `tools/p2/estimate_trajectory.py`. **Not imported by the shipped program; no
> production code is modified.**
>
> Verified by the main agent running the prototype's own self-test directly, not
> from a report.

---

## 1. What it is

A standalone, deterministic, bounded-memory estimator that consumes 4-channel audio
and emits delay **evidence** — including a calibrated uncertainty — in the schema
requested:

```json
{"groups": [], "pairs": [], "trajectory": [], "model": "", "confidence": 0,
 "uncertainty_ms": 0}
```

It measures; it does not correct. It never modifies PTS. It refuses to emit a
result it cannot validate.

## 2. Measurement performance — validated on known answers

The prototype runs a known-answer self-test by default and refuses to measure real
data if it fails. Measured results (`--selftest`, on real C1305 audio):

### Integer delays recovered exactly, to and beyond the search rim

| injected | measured | error |
|---|---|---|
| 0 | 0.0000 | 0.0000 |
| ±1 | ±1.0000 | 0.0000 |
| +593 (+12.354 ms) | +593.0000 | 0.0000 |
| −456 (−9.500 ms) | −456.0000 | 0.0000 |
| +1000 (+20.833 ms) | +1000.0000 | 0.0000 |
| **±1200 (±25.000 ms)** | **±1200.0000** | **0.0000** |
| **+1855 (+38.646 ms)** | **+1855.0000** | **0.0000** |
| **−1750 (−36.458 ms)** | **−1750.0000** | **0.0000** |
| **±2400 (±50.000 ms)** | **±2400.0000** | **0.0000** |

**All zero error.** For comparison, the main agent's own instrument failed beyond
±1200 samples, and that limitation was itself documented as a finding
(`investigation.md` §4). This prototype has no such limit up to ±2400.

### Fractional delays recovered sub-sample

| injected | measured | error |
|---|---|---|
| +23.370 | +23.37500 | +0.00500 (0.1 µs) |
| −12.345 | −12.34375 | +0.00125 (0.0 µs) |
| +0.500 | +0.50000 | 0.00000 |
| −0.250 | −0.25000 | 0.00000 |
| +101.750 | +101.75000 | 0.00000 |
| −100.600 | −100.59375 | +0.00625 (0.1 µs) |

### Two further properties that matter

* **A delay beyond the search rim is declined, not mis-reported.** Injected
  +14400 samples → result reported as declined/pinned. This is the correct
  behaviour and it is the failure mode that produced two separate bugs in the main
  agent's instruments.
* **The answer does not move when the window length changes** (10 s → +1500.0000,
  20 s → +1500.0000). This is an explicit regression guard for the round-1
  un-normalised-correlation-surface bug, where the answer scaled linearly with the
  search-window width.

## 3. The property it does not have: calibrated uncertainty

> ## ⚠ ROUND-10 CORRECTION — the prototype cannot measure real data at all
>
> Running it without its self-test calibration exposes a **hard gate defect**:
>
> ```
> python tools/p2/estimate_trajectory.py --stem 20260919_C1305 --pairs 1-2 \
>        --seg-s 120 --step-s 120 --search-ms 250 --no-selftest --quiet
>
> pair      N  ok  cover  median_ms  span_ms  unc_ms  model
> ch1-ch2  40   0  0.000          -        -       -  undetermined
> ```
>
> **0 usable windows out of 40 on the most rigid pair in the file** — a pair the
> main agent's independent instruments measure at a scatter of **≤ 0.33 samples**.
> Every one of the 40 rows is refused with `reason = peak_below_noise`, and the
> gate's own input is uncomputed:
>
> ```
> peak_over_noise = 0.0000     k_eff_bins = 0     curvature = 7.27e-13
> noise_floor     = 0.2531     ratio      = 1.6309  n_peaks = 455
> ```
>
> `k_eff_bins = 0` with `peak_over_noise = 0.0000` is the signature of a gate input
> that was never computed, not of a measurement below threshold — the correlation
> peak is clearly present (`ratio` 1.58–2.70 across all rows).
>
> **Consequences:**
>
> 1. The prototype **cannot be used on real data** in this configuration.
> 2. With the self-test enabled, the run **aborts before** reaching real data, so
>    **the two paths together leave the prototype unable to measure anything.**
> 3. **The "8.9× under-estimate" below is therefore unreliable as stated** — it is
>    produced by the same self-test that also reports this defect, and the
>    `[G]` check's own "observed scatter of 14.08 samples" could not be reproduced:
>    the main agent measured **0.00–0.33 samples** across eight regions of C1305
>    (2 s windows, 40 per region). A 14.08-sample scatter on a pair that measures
>    0.10 samples is not credible as a property of the material.
>
> **What this does NOT invalidate:** §2's measurement performance, which is from
> known-answer injections and is unambiguous.
>
> **What it changes:** the gate item "the estimator has a definite uncertainty" is
> still **FAIL/PENDING**, but for a simpler reason than miscalibration — **the
> prototype does not run on real data.** Fixing the gate is now the prerequisite,
> and the calibration question cannot be settled until it is fixed.
>
> Reported as found. This is the ninth defect of this kind in the project, and it
> is the same species as the rest: a plausible-looking refusal that is actually a
> code path that never executed.

The prototype computes a per-pair uncertainty and calibrates it against injected
delays in real audio, fitting
`var = 0.1695 × (9.236e-09 / κ) + 0.2030` on 8 measured observations. Across
conditions the model's residual RMS runs 0.00–1.08 samples (0.00–22.54 µs).

**Its own independent calibration check reports:**

```
[G] independent check: does sigma predict the scatter of a real pair?
    40 windows of 2s on the rigid CH1-CH2 control:
      observed scatter 14.0802 samples (293.3 us)
      model sigma        1.5837 samples
      ratio 8.89   FAIL (outside 0.25-4x)
```

**The model under-predicts by ~8.9× — but see the correction above: neither figure
can be trusted until the gate defect is fixed.**

### 3.1 Why, and why this is the *right* failure

The model is fitted on **injected constant delays** in real audio. Such a pair
carries measurement noise and content non-stationarity — but **not** the variability
that comes from *real* inter-channel structure, which per the investigation includes
multiple arrivals whose winner flips between windows (`TWO-ARRIVAL-FINDING.md`).

So the calibration measures the wrong thing: it estimates the uncertainty of
measuring a *clean* delay on this material, while the quantity being reported is the
uncertainty of a delay that may itself be ambiguous. **The 8.9× shortfall is
approximately the size of the effect the whole investigation has been chasing.**

This is a genuine, quantified open defect — and it is the failure mode the project
most needed to catch, because an under-stated uncertainty is exactly what would let
a downstream decision-maker treat an ambiguous delay as solid.

### 3.2 What it does *not* invalidate

* The raw measurement performance in §2 is unaffected — every known-answer test
  passes.
* The refusal behaviour is correct: the prototype **declines to measure real data**
  while its uncertainty is uncalibrated. That is the brief's fallback rule operating
  as intended.

## 4. Assessment against the production gate

| condition | state |
|---|---|
| estimator exists with per-pair uncertainty, coverage-in-time, per-frame relative ambiguity | **prototype satisfies the interface** |
| measurement accuracy | **PASS** — exact to ±2400 samples, sub-sample fractional |
| safe failure mode (decline rather than mis-report) | **PASS** — it declines, and it refuses to run while unvalidated |
| determinism, O(window) memory | **PASS by construction** (no RNG; `RawStream` seek+read); measured peak 542 MB tracemalloc for a 120 s full-rate window |
| **runs on real data at all** | **FAIL** — 0/40 windows, `peak_below_noise`, `k_eff_bins = 0` |
| **calibrated uncertainty** | **FAIL / unassessable** until the gate defect is fixed |

**So the gate condition "the estimator has a definite uncertainty" is FAIL.** The
measurement core is sound; the prototype is blocked by a gate defect, and the
calibration question cannot be settled until it runs.

## 5. What would fix it

1. **Calibrate against real inter-channel pairs, not injected copies.** Use the
   rigid `CH1↔CH2` control as the reference distribution for "no ambiguity" and
   require the model to match its observed scatter; then check the moving pairs
   exceed it.
2. **Model the ambiguity channel explicitly.** The current model has noise and
   content terms; it needs a term for "the tracked peak changed identity", which
   the per-window rival-peak ratio is a candidate observable for — subject to the
   validation caveat that the ratio has so far failed its own negative control
   (`VALIDITY-CRITERION.md` §9.3).
3. **Re-run the [G] check until the ratio falls inside the acceptance band**, and
   keep the check as a permanent gate rather than a one-off.

## 6. Honest summary

The estimator is the most solid artefact P2 produced: it recovers injected delays
exactly across the full ±50 ms range, sub-sample on fractional delays, declines
out-of-range inputs, and is immune to the window-width bug that broke an earlier
instrument. **It is not shippable**, because its stated uncertainty under-predicts
real scatter by ~8.9×, and it correctly refuses to run on real data until that is
fixed.

For the production decision this changes nothing: with the model class unestablished
and every correction prototype failing independent-geometry verification
(`validation.md`), no estimator could justify a correction on this material anyway.
