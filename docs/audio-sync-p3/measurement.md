# P3 — Measurement Layer

> Stage 1 of Audio Sync P3. What the measurement pipeline actually does, in what units,
> with which signs, and with what it cannot do. Everything here is implemented in
> `work/p3/measure.py` and is exercised by `work/p3/synthetic_kat.py`.

## 1. Input

| | |
| --- | --- |
| Source | raw mono float32, 48 kHz — `work/p2/out/raw/<stem>_a<ch>.f32`, reused from P2 |
| Access | `core.sync_estimate.RawStream`, seek + bounded read, imported **read-only** |
| Memory | O(window). The largest allocation is one window plus its FFT; no whole track is materialised |
| Why not the MP4 | the raw extracts already exist, and re-decoding 40–50 GiB per experiment would add cost and a second failure surface with no benefit |

## 2. Units

Units are named, not implied. A delay is never written as a bare number.

| Quantity | Unit | Definition |
| --- | --- | --- |
| `estimated_delay_samples` | samples of the analysis rate | the primary quantity |
| `estimated_delay_ms` | ms | `samples * 1000 / fs` |
| `fs` | Hz | 48 000, recorded per measurement |
| one sample | **0.0208333 ms** | `1000/48000`. This is a *quantisation step*, not an uncertainty |
| `bw_hz` | Hz | width of the coherent band, `analysis_band()` |
| `peak_width_samples` | samples | −6 dB full width of the correlation peak |
| `peak_width_expected` | samples | `fs / bw_hz` — the `1/B` prediction the measured width is checked against |

P2's `0.024 ms floor` was a wrong divisor; the correct step is `0.020833 ms`. P3 uses the
corrected figure and derives `bw_hz` rather than assuming a bandwidth.

## 3. Sign

```text
delay > 0   <=>   the target arrives LATER than the reference
correction  <=>   out[n] = in[n + delay]        (advance the target)
```

Verified end to end, not merely documented: synthetic delays of `0, ±1, ±137, ±1200`
samples are recovered with the correct sign and magnitude, and the correction rule is
implemented by `apply_correction()` in the test module so the two can be checked against
each other.

## 4. Window geometry

```text
reference window   [start,                 start + n_win)
target window      [start - search,        start + n_win + search)
```

The target window is read `search` samples **earlier**, so for every lag in
`[-search, +search]` the two windows compare the same region of the programme. This is
what makes the search range a real, trustworthy parameter: a narrow search is legitimate
because the overlap is controlled by construction.

This is deliberately different from the P2 instrument, which analysed a pre-cropped buffer
with a fixed wide search. P2's own record found that at ±25 ms a true ±11500-sample delay
was reported in-range as −359 or −561 samples; that failure mode comes from the geometry,
and this geometry removes it.

The taper matters and is not cosmetic. The reference window and the *aligned part* of the
target window carry the **same** Hann taper, so the common taper cancels in the
cross-spectrum and the delay is unchanged, while the target's ±search shoulders — which
have no counterpart in the reference — go to zero instead of forming a rectangular
discontinuity. Without it the cross-spectrum phase is scattered by the non-overlapping
edges: `std(phase) = 1.81 rad` over the full band, still `0.102 rad` after de-rotation,
with outliers at π.

## 5. Correlation

Linear (non-circular) correlation by zero-padded FFT with a PHAT weighting that has an
explicit noise-floor guard:

```text
R[k] = conj(A[k]) * B[k]
W[k] = R[k] / max(|R[k]|, eps_abs)        eps_abs = phat_floor_rel * max|R|
c    = irfft(W, n_fft)                     n_fft >= len(ref) + len(tgt)
```

`n_fft` is derived once, by `_fft_len()`, and **every** frequency-domain consumer in the
module uses that same length. Letting the bandwidth estimate and the phase fit derive
their own length was a real defect: the band mask landed on the wrong frequencies and
produced a `+1355`-sample bias on a pair whose true delay was 0. A frequency grid is part
of a measurement's units.

The lag-to-delay mapping is explicit: `c[k]` is the correlation at lag `k - lag0`, so
`delay = lag0 + index - lag0 = index`.

## 6. Peak detection

| Quantity | Meaning |
| --- | --- |
| `peak_score` | peak / max of the surface |
| `noise_score` | robust noise floor (MAD-based, peak excluded) / max |
| `peak_over_noise` | peak / noise floor — the primary sharpness signal |
| `boundary_distance` | samples between the peak and the search rim → `edge_pinned` if 0 |
| `peak_width_samples` | −6 dB full width, linearly interpolated |
| `peak_width_expected` | `fs / bw_hz` |
| `second_peak_ratio`, `second_peak_lag` | kept as diagnostics only — **not** used as a validity criterion (P2 retracted exactly that use) |

## 7. Sub-sample refinement

Two structurally different methods exist. They share only the FFT of their inputs.

### 7.1 Chosen: fine-grid interpolation of the correlation peak

The PHAT-weighted cross-spectrum is evaluated on a fine grid of lags around the integer
peak (`interp_half_samples = 4` samples, `interp_grid = 128` points), and the vertex is
refined by parabolic interpolation. Evaluated in chunks and restricted to `analysis_band`,
because the naive dense form is 128 GiB.

Measured against an **exactly constructed** fractional delay — a frequency-domain phase
ramp `Y(f) = X(f)·exp(-j2πfd/fs)`, which satisfies the estimator's own model by
construction and contains no interpolation filter anywhere — this method is exact to
**≤ 0.00003 samples** on every case whose vertex is interior to the fine grid, across
`0, ±0.25, ±0.5, ±0.75, ±1.25, ±12.345, 23.37, 137, 137.5, 1200` samples.

### 7.2 Retained as a diagnostic only: phase-increment least squares

`phase_slope_refine()` fits the slope of the cross-spectrum phase. On the same exact
fixture it returns the residual **scaled by ≈0.455** — a true residual of `+1.0` samples
came back as `+0.454`, a true `+137.0` as `+199.24`. It is therefore **not** allowed to
set the delay; its value is recorded in `phase_delay_diagnostic` so that its error stays
visible instead of being silently deleted. Its history is kept in full because it cost
real time and the failure mode is instructive:

| Attempt | Failure |
| --- | --- |
| de-rotate by a small candidate, `mod 2π`, then fit | wraps once the per-bin step nears π; constant `+0.1875`-sample bias on a zero-delay pair |
| de-rotate by the integer peak, then `np.unwrap` and fit | unwrapping a phase spanning many turns turns the wrap count into a slope: `+1355` samples |
| estimate the whole lag from raw increments | correct in principle, but at this bin spacing a 1500-sample lag already gives a 0.078 rad step, so a few hundred samples of residual leaves no margin |
| de-rotate by the peak position and add the residual | the arithmetic is right, the residual is biased by ≈0.455 |

## 8. Coherent band

`analysis_band()` returns the frequency range where the cross-spectrum magnitude is within
`rel = 5 %` of its peak. This is a **magnitude threshold, not an energy band**. A 99 %-energy
band includes bins carrying a few percent of the peak, where the phase is essentially
noise: with that definition the phase estimator reported a lag of `1574.7` for a true
`1637`. The distinction is between "the bin carries energy" and "the bin carries
information about the delay".

## 9. Precision and its limits

| Statement | Value |
| --- | --- |
| Sub-sample accuracy on an exact fixture | ≤ 0.00003 samples (≤ 0.6 µs) |
| Integer delay accuracy on synthetic material | exactly 0 samples across the matrix |
| Median absolute error over the 33-case suite | ~3e-5 samples |
| Width of a phase-only peak at ~4.3 kHz bandwidth | ~11 samples — so the *integer* peak alone is worth about ±0.5 sample, which is why interpolation is not optional |

Known limits, stated so they are not rediscovered:

1. **A window measures an average.** A 30 s window containing motion reports the delay of
   no instant. The sub-window probe is the intended detector, and the trajectory residual
   is the intended check; neither is validated yet.
2. **Two arrivals inside the search range make the delay a blend.** The estimator measures
   the stronger arrival correctly (see `synthetic-validation.md`), and the probe's spread
   is what reveals that a second arrival exists — but "correctly measures the winner" is
   not the same as "the delay is well defined".
3. **Low coherence is fatal and is detected, not corrected.** Peak/noise ≈ 6 cases are
   retained, ≈ 2.8 are rejected. Nothing is repaired.
4. **The analysis band sets a floor on uncertainty** that has not yet been turned into a
   per-window error bar. `probe_sigma_samples` is a conservative placeholder.
