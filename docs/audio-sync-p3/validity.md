# P3 — Per-Window Validity Criterion

> Stage 2 of Audio Sync P3: the first technical gate. Implemented in `work/p3/measure.py`,
> exercised by the negative controls in `work/p3/synthetic_kat.py`.
>
> **Status: the criterion rejects every negative control without rejecting a single
> positive one. It is not yet calibrated as an error bar, and one of its rules is known to
> be too weak.**

## 1. Why this gate exists at all

P2's blocking finding was not "the delay cannot be measured". It was:

> No per-window validity criterion survived validation, so there is no defensible rule for
> deciding **when** a measured delay may be acted upon.

Without that rule the chain `wrong measurement → trajectory → correction → worse audio` has
no barrier in it. The asymmetry is deliberate and permanent:

```text
false accept  ->  a wrong number enters the trajectory  ->  a wrong edit reaches the audio
false reject  ->  a window is left untouched             ->  the audio is unchanged
```

P2 rejected the second-peak ratio because a purpose-built single-arrival control falsified
it. P3 therefore treats every validity input as a hypothesis with a negative control, and
does not accept "it separates the corpus beautifully" as evidence — P2 had exactly that,
and it was worthless.

## 2. What every window reports

Each `WindowMeasurement` must answer three questions, and the JSON carries all of them:

```text
what was measured?   estimated_delay_samples / estimated_delay_ms
why believe it?      peak_over_noise, boundary_distance, bw_hz, peak_width vs 1/B,
                     probe credibility, probe spread, interp_curvature
why refuse it?       valid=False + one reason code + a human-readable detail
```

Reason codes, in the order they are checked:

| Reason | Meaning |
| --- | --- |
| `silent` | either track below −60 dBFS |
| `no_peak` | the correlation surface is identically zero |
| `window_too_short` | too few non-zero samples for the geometry |
| `edge_pinned` | the peak sits on the search rim, so the true delay may be outside |
| `weak_peak` | `peak/max < 0.25` or `peak/noise < 6` |
| `narrowband` | coherent band width < 800 Hz, so the peak is too broad to place |
| `interp_vertex_at_edge` | the fine-grid vertex sits at the edge of the fitted range |
| `diffuse_peak` | measured −6 dB width exceeds the `fs/B` prediction by more than 50 % |
| `intra_window_unstable` | the sub-window probes are not credible, or neither tight nor monotone |

## 3. The inputs, and what each one is for

### 3.1 `peak_over_noise` — is there a peak at all

The peak of the correlation surface divided by a robust (MAD-based) noise floor of the
surface itself, with the peak excluded from the floor estimate. `min_peak_over_noise = 6`.

This is the input that rejects pure-noise pairs. Measured on the controls:

| Case | `peak_over_noise` | Outcome |
| --- | --- | --- |
| signal present, in band | 394 – 42 509 | accept |
| independent noise between the two channels | 6.3 | **reject** |
| true delay 12000 vs ±1500 search | 6.1 | **reject** |
| single tone | 2.8 | **reject** |

The margin between 2.8 and 394 is wide, and the two cases at ≈ 6 are the ones that make
the threshold a real decision rather than a formality. It is set at 6 and it is the one
number in this file most likely to need revisiting on real material.

### 3.2 `boundary_distance` — is the search range wide enough

If the peak sits on the rim, any in-range number is a lower bound on the true delay, not a
measurement of it. The check is a refusal, never a widening: the brief's requirement is
that a narrow search is *legitimate*, and it is — the window geometry makes every lag in
range a full-overlap comparison — but a delay outside the range must be **reported as
unmeasurable, not reported at the rim**. P2 measured a narrow search returning 2658 for a
true 11500; that is the failure this prevents.

### 3.3 `bw_hz` and the peak width — can the peak be placed at all

Phase-only correlation gives a peak of width ≈ `1/B`. `bw_hz` is measured from the
cross-spectrum (magnitude ≥ 5 % of peak), `peak_width_expected = fs/bw_hz` is the
prediction, and `diffuse_peak` fires when the measured width exceeds it by more than 50 %.

**This rule had to be recalibrated against real material, and the first version was
wrong.** It began as `min_bw_hz = 800`, flat. On C1305 that refused **58 % of real
windows** — the median coherent band on this corpus is **708 Hz** — while the synthetic
suite passed 28/28. A criterion that rejects most of a corpus is a broken criterion, not a
strict one.

Timing precision is governed by bandwidth **and** signal-to-noise together: the peak width
is `fs/B`, and the peak must clear the surface noise to be placed at all. The rule is now
conditional:

| Condition | Verdict |
| --- | --- |
| `band < 150 Hz` | **REJECT** (`narrowband`) — there is not enough band to place a peak at all |
| `band < 800 Hz` **and** `peak/noise < 15` | **REJECT** (`narrowband`) — a broad peak that does not clear the noise floor |
| `band < 800 Hz` with a strong peak | **ACCEPT** — a broad peak, with its width reported |

Effect on the C1305 sample: valid rate **12.2 % → 31.1 %**, and `narrowband` rejections
**275 → 8**. Three synthetic controls were changed to match what the criterion actually
claims (§4): a genuinely information-poor 30–110 Hz band must be refused, and a 500 Hz band
with a strong peak must be **accepted**.

Note the honesty requirement this creates: the measured width is also the honest statement
of how precisely the *peak* can be located (± half a width — about ±33 samples, ±0.7 ms, at
the corpus median band), which is why every window now reports `uncertainty_samples`.

### 3.4 `interp_vertex_at_edge` — the largest single rejection on real material

The fine-grid vertex must be interior to the fitted range, and the range is **scaled to the
measured peak width** (`0.6 × width`, floored at 4 samples, capped at 64). A fixed ±4-sample
grid was the first attempt and it rejected 78 % of real windows, because a 65-sample-wide
real peak is not a 8-sample-wide synthetic one.

At the corrected width it is still the dominant rejection: **52 of 90** sampled windows. That
is a real finding rather than a tuning failure. When the surface carries more than one
comparable arrival, `argmax` selects one location and the parabolic vertex lands tens of
samples away from it — i.e. **the delay in those windows is not a well-defined scalar**, and
the window is refused rather than reported. On this material roughly **58 % of windows are
genuinely ambiguous** by this signature, which is consistent with P2's independent finding
that 90–99 % of windows are ambiguous on every moving pair.

The consequence for a correction is direct and is stated in `correction.md`: a plan can only
be built on the windows that survive, and the gaps are explicit.

### 3.4 The sub-window probe — is the delay a well-defined scalar *in this window*

Five overlapping probe windows at 50 % of the window length, each measured by the same
validated method. The probe answers **two different questions**, and conflating them was a
real bug:

| Probe behaviour | Meaning | Verdict |
| --- | --- | --- |
| tight (`span <= max(fs/B, 2)` samples) | the delay is static across the window | **accept** |
| strictly monotone, span exceeding 4× the probe uncertainty | the delay moves smoothly across the window | **accept** |
| neither | the delay is not a well-defined scalar here | **reject** |

An earlier version rejected on span alone. It threw away `const_+1200` — a case measured to
**0.00000 samples of error** — because a real ramp of 12.28 samples across a 30 s window
exceeded a threshold derived from the peak width. **Motion is not instability.** The
opposite error followed immediately: an intermediate "span ≤ 6 × largest step" rule was
almost always true, so it accepted independent noise in which no delay exists. What
separates the two is *shape* (monotone vs erratic) plus *credibility of the probes
themselves*.

### 3.5 Probe credibility — the rule that actually rejects noise

A probe is only allowed to testify about the shape of the delay if the probe is itself a
credible measurement (`peak_over_noise >= 20`). This is what rejects both noise controls:
all five of their probes are individually worthless, so their 1800-sample "spread" is not
evidence of motion, it is evidence of nothing.

This is the single most important rule in the criterion, and it is the one P2 lacked.

### 3.6 `interp_vertex_at_edge` — is the interpolation being pulled

The fine-grid vertex must be interior to the fitted ±4-sample range. If it is not, the true
peak is outside the range being fitted and the reported value is an extrapolation artefact.
Caught on the `d = 1199.25` case of the exact phase-ramp fixture, where the peak position
was mislocated by the coarse search.

## 4. Negative controls and their verdicts

Every one of these must be REJECT. All of them are.

| Control | Verdict | Reason | What it proves |
| --- | --- | --- | --- |
| silence | REJECT | `silent` | level guard |
| independent noise between channels — **no delay exists** | REJECT | `intra_window_unstable` (5/5 probes below credibility) | the probe-credibility rule works |
| true delay 12000 samples vs ±1500 search | REJECT | `intra_window_unstable` | an out-of-range delay is not reported at the rim |
| 30–110 Hz limited content | REJECT | `narrowband` (80 Hz band: hard floor) | an information-poor band is refused outright |
| single 220 Hz tone | REJECT | `weak_peak` | periodicity is refused rather than aliased |

And the positive half: **accepted with 0 false rejects**, including constant delays of
`±1`, `±137`, `±1200` samples, fractional delays down to 0.25 samples, a white-noise pair,
a window containing a smooth 26.7-sample ramp, three two-arrival mixtures whose second
arrival sits inside the search range, and — added after the recalibration — a **500 Hz band
with a strong peak**, which must be ACCEPTED because a broad peak is a usable measurement,
not an unusable one.

## 4.1 The uncertainty this criterion now reports

Every window carries `uncertainty_samples` and `uncertainty_ms`:

```text
uncertainty = max(0.3, 0.5 * peak_width / sqrt(peak_over_noise))     samples
```

Its basis: the vertex of a correlation peak cannot be located better than the peak's own
width divided by its signal-to-noise ratio, and half the width is the hard floor. At the
corpus median (743 Hz band, peak/noise ≈ 33) that is about **5.6 samples ≈ 0.12 ms**.

**It is a physical bound, not a calibrated σ.** Calibrating it needs a known-truth repeat at
several bandwidths, which has not been run. It exists because Stage 5 must be able to ask
whether a fitted trajectory is doing anything more than fitting this noise, and because a
broad-peak measurement must not look as trustworthy as a sharp one.

## 5. What is *not* validated

1. **No error bar.** `probe_sigma_samples` is deliberately conservative (a fine-grid step,
   floored at 1 % of the probe's search margin) and is a placeholder, not a calibrated
   uncertainty. A trajectory fit needs a real per-window σ, and P2's retracted `~8.9×`
   claim is a warning about asserting one without a control.
2. **The `min_peak_over_noise = 6` threshold has no real-material support.** Both controls
   near 6 are the *reason* for the value, and they are synthetic. If real windows cluster
   near it, the threshold — not the material — needs rethinking.
3. **The motion rule is only tested on synthetic ramps inside one window.** No fixture yet
   contains a specified ramp plus noise plus a moving second arrival.
4. **`probe_sigma` does not model coherence.** A window whose phase is coherent over 1 kHz
   and noise over the rest will be scored as if all of its band were usable.
5. **The two-arrival behaviour is delimited but not formalised.** A second arrival at
   0.35–0.9 amplitude inside the search range does not disturb the measurement (it is
   attenuated ~1/6000 inside the overlap); a second arrival 9× the search range away does
   not either. What *is* not yet measured is a second arrival whose correlation actually
   enters the overlap — the case that makes the delay a genuine blend. That case has no
   fixture yet, so the criterion's behaviour on it is **unmeasured**, not measured-and-fine.

## 6. Consequence for the project

The gate exists, it is falsifiable, and it currently refuses every case where the answer
cannot be known. That is enough to permit Stage 4 (long-duration measurement) — but it is
**not** enough to permit Stage 6 (correction), because a correction needs the *numerical*
uncertainty, not just the accept/reject verdict. §5.1 and §5.2 are the work that stands
between the two.
