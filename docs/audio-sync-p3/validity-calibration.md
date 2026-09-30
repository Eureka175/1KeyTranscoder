# Validity Calibration

**Phase: P3.1. Status: `LIMITED` — CH3→CH2 and CH3→CH4 are calibrated on C1305; CH3→CH1
has a defect; C1306 has not been measured, so no threshold in this document has left its
calibration corpus.**

The brief's rule governs this phase: **C1305 calibrates, C1306 validates, and the two are
never jointly tuned.** Every number below is labelled with its corpus for that reason.

Scope note: `valid fraction` appears throughout as a *reported* quantity, never as an
objective. The brief's §26 forbids raising it on its own, and in P3.1 it was never raised
by any change to a threshold. Where a threshold was moved, it was moved because the old
one refused windows whose delay an independent instrument confirmed was well-defined, and
the change is recorded with its before/after effect.

---

## 1. The criterion as calibrated

Config: `n_win = 1 440 000` (30 s), `search = 3000`, `hop = 480 000` (10 s), taper Hann,
`n_probe = 5`, `probe_alpha = 0.5`. All thresholds are C1305 numbers.

| gate | threshold | source |
| --- | --- | --- |
| silent | rms < −60 dBFS | both tracks |
| short | < half the reference window non-zero | — |
| edge | integer peak ≥ 4 samples from the search rim | — |
| weak peak | `peak/max` ≥ 0.25 and `peak/noise` ≥ 6.0 | — |
| narrowband, hard | coherent bandwidth ≥ **150 Hz** | synthetic: below this no peak can be placed at all |
| narrowband, soft | bandwidth ≥ 800 Hz **unless** `peak/noise` ≥ 15.0 | C1305: the corpus median coherent band is 708 Hz, so the flat 800 Hz rule refused 58 % of real windows while the synthetic suite passed 28/28 |
| diffuse | peak width ≤ 1.5 × (1/B) + 2 | — |
| interior vertex | \|interp − int peak\| ≤ 0.9 × half | — |
| intra-window | see §2 | the probe machinery |

The narrowband history is worth keeping visible because it is the one threshold in this
project that was tightened by evidence rather than loosened by convenience: a **flat**
800 Hz rule looked principled, passed every synthetic case, and silently discarded the
majority of real material. Replacing it with a bandwidth/SNR **conditional** took the
valid rate from 12.2 % to 31.1 % **and** cut `narrowband` refusals from 275 to 8. A
criterion that refuses most of the corpus is a bad criterion, not a strict one.

---

## 2. The intra-window test

`probe_step_max` is the largest step between the delays of `n_probe = 5` sub-windows of
`probe_alpha = 0.5` × 30 s each, spread across the window. It answers two questions that
were once conflated and must not be:

* an **erratic** probe means the window's delay is not a well-defined scalar — refuse;
* a **monotone** probe means the delay moves smoothly across the window — accept.

The distinction is drawn by *shape*, not by span. An intermediate version tested
"span ≤ 6 × largest step", which is arithmetically true for almost any 5-point set and
therefore accepted everything; the tell was that independent noise between two channels,
whose delay does not exist, passed with a 4314-sample probe span.

---

## 3. C1305 calibration results

| pair | valid | rate | `interp_vertex_at_edge` | `intra_window_unstable` | `narrowband` |
| --- | ---: | ---: | ---: | ---: | ---: |
| CH3→CH1 | 93/478 | 19.5 % | 266 | 38 | 81 |
| CH3→CH2 | 102/478 | 21.3 % | 266 | 33 | 77 |
| CH3→CH4 | 247/478 | 51.7 % | 194 | 28 | 9 |

`interp_vertex_at_edge` dominates on every pair, and §4 of
`movement-resolution.md` shows what it means: the fine-grid vertex leaves the fitted range
whenever the correlation surface has more than one comparable maximum. It is the
criterion correctly reporting a window with no single scalar delay, not an instrumentation
failure. P3 read it as the latter.

### 3.1 The decision boundary, measured directly

Every threshold above is an indirect proxy for one question, which the double-arrival
fixture (`work/p3/p31/double-arrival/double-arrival.json`) now answers directly. A
secondary arrival of controlled level was placed `sep` samples after the primary, in the
corpus's own band:

| sep | sep (ms) | accepted | strongest accepted secondary | error at that level |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 0.21 | 8/8 | **0 dB** | +5.00 samples (+0.104 ms) |
| 25 | 0.52 | 8/8 | **0 dB** | +12.50 samples (+0.260 ms) |
| 50 | 1.04 | 7/8 | −3 dB | −4.60 samples |
| 100 | 2.08 | 7/8 | −3 dB | −2.77 samples |
| 150 | 3.12 | 7/8 | −3 dB | −2.05 samples |
| 250 | 5.21 | 7/8 | −3 dB | −1.73 samples |
| 400 | 8.33 | 7/8 | −3 dB | −0.71 samples |

**Every one of the 7 refusals in the 56-cell grid is at 0 dB** — two arrivals of equal
strength — and every refusal reason is `intra_window_unstable`. There are no false
refusals and no false accepts anywhere in the grid. At **−6 dB** the fixture is accepted at
every separation with an error of at most **4.5 samples (0.094 ms)**.

### 3.2 This reclassifies the corpus's rival-ratio statistic

P3.1 §4 reported a rival ratio above 0.7 in **94.1 %** of C1305 windows, median 0.861, and
P3's own summary for the accepted set was median 0.891. The fixture says a secondary
arrival at −1.3 dB (ratio 0.86) would be measured to a few samples.

The two are not in conflict; they are different measurements. The 0.861 was taken from the
**unweighted, 60-sample-masked** correlation, so it counts smeared shoulder mass as rival
arrivals — mass that PHAT weighting then suppresses. **The 0.861 does not mean 94 % of
windows have a competing arrival at −1.3 dB.** It means the *unweighted* surface has a
secondary bump of that height, which is a weaker statement and was read as a stronger one
in the earlier section. Corrected there by cross-reference.

The useful part of the statistic survives: it is the right *shape* of diagnostic, and it
is what the gate's own `interp_vertex_at_edge` reason is detecting.

### 3.3 The remaining defect: the criterion is window-local

The fixture's cells are each *individually* crisp. Every failure the gate catches is a
within-window failure. A delay that is crisp inside every window but lands on a
**different arrival in different windows** passes every current test. Combined with
§4.2 (CH3→CH1's accepted set is white noise in time), the gap is not a tuning problem but
a missing test: **the criterion tests each window in isolation and never tests the admitted
set as a series.**

### What the gate admitted, checked against an instrument that shares no code with it

`work/p3/p31/truth-cache.json` holds an independent truth series for all 478 windows on
all three pairs: both windows read from the same source sample, correlated without PHAT
weighting, argmax over lag.

| pair | accepted: truth span | accepted: lag-1 autocorr | accepted: mean step / 10 s | refused: truth span | refused: lag-1 autocorr |
| --- | ---: | ---: | ---: | ---: | ---: |
| CH3→CH1 | 529 (11.021 ms) | **+0.0444** | 11.05 | 898 | +0.5771 |
| CH3→CH2 | 48 (1.000 ms) | +0.8581 | 1.17 | 924 | +0.3916 |
| CH3→CH4 | 1185 (24.688 ms) | **+0.9269** | 8.87 | 1297 | +0.7950 |

Reference values for the same statistic: a synthetic 1 ms drift plus 0.25 ms of noise
gives +0.43; white noise with the refused windows' sd gives −0.006.

---

## 4. Two findings, one of them a defect

### 4.1 The gate is correct on CH3→CH2 and CH3→CH4

On CH3→CH2 the accepted set is continuous (+0.8581) and the refused set is not (+0.3916),
with steps of up to 856 samples between adjacent windows. On CH3→CH4 both sets look
plausible at the pair level (+0.9269 / +0.7950) — the refused windows have larger steps
because they are noisier, not because they are incoherent.

The gate's refusals therefore agree with a test that does not share a line of code with
it. That is the strongest statement this phase can make about the criterion, and it is
the reason valid fraction was left alone.

### 4.2 DEFECT — CH3→CH1's accepted set is not ordered in time

`+0.0444` on the accepted windows is the autocorrelation of **white noise**. The gate
admitted 93 windows whose true delays do not form a trajectory, and nothing in the
criterion tests for that, because every current test looks at one window at a time.

The consequence is not hypothetical: on CH3→CH1 the P3 trajectory stage fitted a model to
that set and reported `poly3` with 0.760 ms RMS — a number describing the fit of a
polynomial to noise. `interp_vertex_at_edge` did the same to 266 of the 376 refusals on
CH3→CH2, but there the *accepted* set survived; on CH3→CH1 it did not.

This is Stage 4's work and it is not done. The shape of the fix is a **set-level** test —
does the admitted subsequence have temporal structure — and it must be calibrated on
C1305 and validated on C1306, not fitted on either alone.

---

## 5. Cross-corpus validation

**Not performed.** `work/p2/out/raw/20260919_C1306_a{1..4}.f32` are present and intact
(799 614 816 B each), so the run needs no re-extraction. It was deliberately not started:
§4.2 changes what the criterion should be, and validating a criterion that is about to be
revised would produce a number with no meaning.

The two corpora are known to differ in at least one respect, from P1's own work: C1306 is
46 308 475 606 B against C1305's 53 489 618 518 B, and its channel-sync run took 336 s
against 402.5 s. Whether their coherent bandwidths, SNR and ambiguity structure match is
**unknown** — that is precisely what Stage 7 measures, and it cannot be assumed from
duration.

### What must be compared when it is run

| property | C1305 value (calibration) | C1306 (validation) |
| --- | --- | --- |
| relative duration | 4807.30 s | ? (shorter) |
| coherent bandwidth, median | 708 Hz (soft rule), 846 Hz accepted | ? |
| `peak/noise`, accepted median | 71.01 | ? |
| second-arrival ratio, median | 0.861 | ? |
| `probe_step_max`, median | 2.36 accepted / 282.69 refused | ? |
| valid rate | 19.5 / 21.3 / 51.7 % | ? |
| invalid reason mix | `interp_vertex_at_edge` 266 of 376 | ? |
| truth span, accepted | 48 / 529 / 1185 samples | ? |

**A threshold that fails on C1306 is evidence that it does not generalise, and the
response is to record that — not to re-tune until C1306 passes.**

---

## 6. Provenance

```
corpus     20260919_C1305   230 750 520 samples   4807.30 s   478 windows @ 30 s/10 s
truth      work/p3/p31/geometry/truth-cache.json   (451 s full-rate pass, cached)
measure    work/p3/experiments/20260919_C1305/raw_measurements_ch3_to_ch{1,2,4}.jsonl
continuity work/p3/p31/geometry/continuity.json
ambiguity  work/p3/p31/geometry/ambiguity-test.json
scripts    work/p3/p31/{faildecomp,excursion,ambiguity,continuity,noisefloor,noisechannel}.py
```
