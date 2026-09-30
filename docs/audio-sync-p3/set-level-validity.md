# Set-Level Validity

**Phase: P3.2. Status: `SET-LEVEL VALIDITY = PROTOTYPE VALIDATED ON C1305; C1306 NOT YET
MEASURED`.**

---

## 1. Why a window-level criterion is not enough

P3.1 reached this from three directions and left it as the only structural hole:

* the window criterion tests each window in isolation and never tests the admitted set as
  a series (`work/p3/P3.1-FINAL-REPORT.md` §7);
* on C1305 **CH3→CH1** the *accepted* windows have lag-1 autocorrelation **+0.0444** —
  white noise in time — and P3 fitted a `poly3` to them and reported 0.760 ms RMS (§5);
* on **CH3→CH4** the only material difference between a 10 s and a 2.5 s grid was **4
  windows** the criterion was marginally sure about, and those 4 carried 22.476 of the
  24.744 ms of apparent movement (§6.5).

A window can be crisp, high-confidence and correct *within itself* while the sequence of
such windows describes nothing. That is the gap.

## 2. Why the obvious criterion is wrong

`abs(d[i] − d[i−1]) < threshold` cannot be the answer, and the P3.2 brief's §12 says so.
P3.1 measured that the real delay carries genuine discontinuities: at the quiet region's
1 s hop, **20 of 599 steps still exceed 200 samples**. A step-size threshold either
rejects real motion or accepts switching, depending only on where it is set.

## 3. What was built

`work/p3/p32/set_validity.py`. Statistics reported for every accepted set:

| statistic | what it is for |
| --- | --- |
| `lag1_autocorr`, `lag2_autocorr` | does the series have memory from one window to the next |
| `distinct_value_ratio` | levels used ÷ span covered — a switching estimate visits few levels, motion fills a continuum |
| `step_sizes` | median / p90 / max, over a stated deadband |
| `reversal_rate` | **reported, not decisive** — see §5 |
| `big_step_clustering` | whether large steps are adjacent (physical events) or scattered (independent per window) |

Decision clauses after §5: `lag1_autocorr < 0.20` → invalid; `distinct_value_ratio <
0.010` with span > 20 samples → invalid; fewer than 3 accepted windows → invalid.

## 4. Synthetic fixtures with ground truth

Eight fixtures, each rendered into audio so the estimator sees a signal that genuinely has
two arrivals switching rather than a delay series with edited numbers. The secondary sits
120 samples (2.50 ms) after the primary at **−6 dB**, which P3.1's double-arrival fixture
measured as *accepted at every separation with ≤ 4.5 samples of error* — so the window gate
cannot see it, which is the point.

| fixture | truth | window gate | set verdict |
| --- | --- | --- | --- |
| S0 smooth single trajectory | valid | 40/40 | **VALID** ✓ |
| S1 smooth + −20 dB unrelated signal | valid | 40/40 | **VALID** ✓ |
| S2 one genuine 120-sample discontinuity | valid | 38/40 | **VALID** ✓ |
| S3 secondary switching at random per window | invalid | 38/40 | **INVALID** ✓ |
| S4 secondary on for two solid blocks | invalid | 39/40 | **INVALID** ✓ |
| S5 smooth with a 10-window gap | valid | 35/40 | **VALID** ✓ |
| S6 smooth with 2 isolated pulled windows | valid | 40/40 | **VALID** ✓ |
| S7 two arrivals at equal level | invalid | **0/40** | **INVALID** ✓ |

**8/8, zero false accepts, zero false rejects.**

Two of these are worth naming. **S4** is the case a lag-1 test alone passes: block-wise
switching drops the autocorrelation only to +0.172, close enough to the threshold to be
uncomfortable, and the reversal rate is what catches it. **S6** is the case a
contamination-sensitive criterion would falsely reject: two isolated windows pulled to the
other arrival leave the majority able to establish the trajectory, and the criterion must
not over-react — reversal rate on it is 0.182.

## 5. The reversal-rate clause was removed, and the corpus removed it

The first version included `reversal_rate > 0.55 → invalid`. It fired correctly on S3 and
S4. It also **falsely rejected S2** — a fixture carrying one genuine 120-sample
discontinuity — because a real trajectory is otherwise smooth, so its steps are a few
samples of estimator jitter and jitter flips sign at random. A **3-sample deadband** fixed
that, justified by P3.1's measurement of the estimator at 0.001 samples of error on a
constant known delay.

Applying the criterion to C1305 then rejected **all three real pairs**, including
CH3→CH4. The correct response was not to move the threshold, so two diagnostics were run
(`contamination.py`, `multiresolution.py`) and they established that the statistic cannot
carry a decision in the regime the corpus occupies:

* on CH3→CH4's accepted windows the median |step| is **1.7 samples** while the estimator's
  window-to-window residual is **11–55 samples**. Per-step movement is *below the
  measurement noise floor*, so the sign of each step is set by noise and the reversal rate
  climbs toward the noise value whatever the delay does.
* a white-noise Monte Carlo at these sample sizes gives `reversal_rate = 0.500 ± 0.058`, so
  the 0.55 threshold sat **0.9 σ from a coin flip**.
* the 670-sample step that triggered the rejection spanned a **540 s gap** between accepted
  windows, and across the pair the correlation between gap length and |step| is
  **r = +0.788**.

| gap between accepted windows | n | median \|step\| | max \|step\| |
| --- | ---: | ---: | ---: |
| 0–15 s | 193 | 1.67 | 25 |
| 15–60 s | 46 | 2.43 | 179 |
| 60–200 s | 6 | 2.40 | 670 |
| > 200 s | 1 | 663.70 | 664 |

The large steps are the delay moving during unmeasured stretches — real motion, not
switching. `reversal_rate` stays reported because it is informative where per-step
movement exceeds the noise; it is not a decision clause.

## 6. What replaces it: multi-resolution containment

No deadband, no threshold, no sample-size penalty. P3.1 §6.5 measured CH3→CH4 at two hop
settings and found the shared windows **bit identical** (sd 0.000 ms, r = 1.0000), which
gives a test with no free parameter:

> a real delay measured on a 30 s window at a 10 s hop must lie inside the envelope of the
> 30 s measurements at a 2.5 s hop over the **same audio**.

If the coarse value tracks one arrival and the fine values another, or if the coarse window
averages two arrivals into a value no fine window takes, the coarse point falls outside.

**CH3→CH4, 214 coarse windows with ≥ 3 fully-contained accepted fine windows:**

| | |
| --- | ---: |
| inside the fine envelope | **155 (72.4 %)** |
| below it | 32 |
| above it | 27 |
| fine envelope width, median | **1.8 samples (0.038 ms)** |
| fine envelope width, max | 26 samples |
| overshoot where outside, median | 0.0 samples |
| overshoot where outside, **max** | **2.9 samples** |

The coarse evaluation lies within **2.9 samples** of a **1.8-sample-wide** envelope of
independent measurements of the same audio. The accepted set is coherent across
resolutions.

## 7. C1305 calibration

Thresholds frozen from §4 and **not re-tuned**:

| pair | windows accepted | accepted span | lag1 | levels/span | set verdict | P3.1's number |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| **CH3→CH1** | 93/478 (19.5 %) | 535.1 samples (11.149 ms) | **+0.0662** | 0.0673 | **INVALID** | +0.0444 |
| CH3→CH2 | 102/478 (21.3 %) | 66.1 samples (1.376 ms) | +0.8046 | 0.5905 | **VALID** | +0.8581 |
| CH3→CH4 | 247/478 (51.7 %) | 1187.7 samples (24.744 ms) | +0.9305 | 0.0632 | **VALID** | +0.9269 |

**The natural negative control worked.** CH3→CH1 was not constructed to fail — P3.1
measured its accepted-set autocorrelation at +0.0444 independently — and the set-level
criterion rejects it on exactly that statistic. This is the first time in this project that
a criterion has been shown to reject a real accepted set for a stated structural reason.

**One fragility, reported rather than hidden:** CH3→CH1 flips to VALID once its 2 % most
extreme windows are trimmed. Its incoherence lives in a few outliers rather than being
uniform. The verdict on the untrimmed set is the one that counts, but a criterion that
changes its answer on a 2 % trim should not be described as robust, and it is not described
as such here.

## 8. Limitations

1. **C1306 has not been measured.** No threshold in this document has left its calibration
   corpus. That is Stage 4 and it gates everything downstream.
2. **The containment test has been run on one pair only** (CH3→CH4), because a fine-grid
   measurement exists for that pair only. `--hop-s 2.5 --targets 0,1,3` would extend it.
3. **The fixtures' positives move 3 samples per window, the corpus moves 1.7.** That gap is
   what let the reversal-rate clause pass the synthetic suite and fail the corpus. The
   synthetic positives should be regenerated at corpus-realistic step-to-noise ratios, and
   §5's diagnostics are the reason to believe it matters.
4. **The criterion has no clause about gaps.** CH3→CH4 spans gaps up to 540 s, and §5 shows
   large steps concentrate there. Whether a set with a 540 s hole should be called one
   trajectory is a design question P3.2 has not answered.
