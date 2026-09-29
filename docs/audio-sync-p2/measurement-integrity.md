# Audio Sync P2.1 — Measurement Integrity

Stage 1 deliverable. Everything in this document was read out of the shipped code or
measured; nothing is inferred from a report or from a filename. The instrument was
validated against known answers in Stage 2 **before** any corpus reasoning, and the
known-answer results are in §8.

Two instruments exist and they must not be confused:

| Instrument | Path | Gate | Used for |
| --- | --- | --- | --- |
| **Production estimator** | `core/sync_estimate.py` → `core/channel_sync.py` | composite confidence + constancy gates | the decision to `fix` / `untouched` |
| **P2 research instrument** | `work/p2/scripts/p2_track.py` `gcc_phat_peaks`, driven by `work/p2/scripts/fullrate_delay.py` | none (ratio recorded) | all corpus delay numbers in `docs/audio-sync-p2/` |

They share the **raw correlation primitive** concept and the sign convention, but they
are separate code. A confidence finding in one is not automatically a finding in the
other; §7 keeps them apart deliberately.

---

## 1. Measurement architecture

```
container (Sony XAVC HS)  →  ffmpeg  →  4 × mono raw float32 @ 48 kHz
                                            work/p2/out/raw/<stem>_a{1..4}.f32
                                                    ↓
                                      core.sync_estimate.RawStream
                                  (bounded seek+read window reader, O(window))
                                                    ↓
                          ┌─────────────────────────┴──────────────────────────┐
                          ↓                                                    ↓
              preprocess: subtract window mean                preprocess: subtract window mean
              normalise by window RMS                        normalise by window RMS
                          ↓                                                    ↓
              nfft = next_fast_len(F + L − 1)                same
              G = conj(rfft(ref)) · rfft(tgt)                same
              PHAT: G / (|G| + 1e-3·max|G| + 1e-12)          same
              surface = irfft(G)                             surface = irfft(G)
                          ↓                                                    ↓
              crop surface[m_lo : m_hi+1]                    crop the same way
                          ↓                                                    ↓
              polarity flip if −min > 1.2·max                same
                          ↓                                                    ↓
              find_peaks(distance=2) → argmax                find_peaks(distance=2) → argmax
                          ↓                                                    ↓
              delay = m_lo + argmax − lag0                   delay = m_lo + argmax − lag0
                          ↓                                                    ↓
              confidence (3 components) + success gate       ratio = best / second
                          ↓                                                    ↓
              accepted = success ∧ conf ≥ 0.3                usable = not pinned to rim
```

The two branches diverge **only after** the peak is located. Everything up to and
including `delay = m_lo + argmax − lag0` is the same arithmetic, and both use
`lag0` in the same way. That is why the sign audit in §4 covers both at once.

## 2. Execution pipeline with units (production path)

Every row: input → variable → operation → output, with the unit made explicit.
`SR = sample_rate` (48000 in the corpus); `SR_c = coarse_rate` (8000);
`q = SR / SR_c` (= 6).

| # | Stage | Input (unit) | Variable | Operation | Output (unit) | Boundary behaviour |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | source open | path | `RawStream` | seek+read only inside `read()` | samples | empty file → `ValueError`; size not a multiple of itemsize → `ValueError`; never maps the file |
| 2 | coarse frame geometry | `frame_ms` (ms), `SR_c` (Hz) | `f_c = round(frame_ms·SR_c/1000)` | — | coarse samples | `frame = f_c·q` full-rate samples |
| 3 | coarse hop geometry | `hop_ms` (ms), `SR_c` | `h_c = round(hop_ms·SR_c/1000)` | — | coarse samples | `hop = h_c·q` |
| 4 | coarse search half-width | `search_window_ms` (ms), `SR_c` | `s_c = ceil(search_window_ms·SR_c/1000)` | — | coarse samples | 80 ms → 640 coarse = 3840 full-rate = ±80 ms |
| 5 | frame count | `n` (samples) | `n_frames = (n−frame)//hop + 1` | — | windows | `n < frame` → 0 frames → `insufficient_frames` |
| 6 | decimation of the reference frame | full-rate samples | `_coarse_slice(ref, c0, c0+f_c, q)` | `scipy.signal.decimate(q, ftype="fir", zero_phase=True)` with `_DECIMATE_MARGIN = 16` coarse samples of margin each side | `f_c` coarse samples | out-of-stream region zero-filled; the margin is trimmed after filtering so no transient reaches the output |
| 7 | decimation of the target window | full-rate samples | `_coarse_slice(tgt, c0−s_c, c0+f_c+s_c, q)` | same | `f_c + 2·s_c` coarse samples | same |
| 8 | coarse correlation | 2 × coarse arrays | `gcc_phat_int`, `lag0 = s_c` | PHAT + irfft as in §1 | surface (dimensionless) | any window RMS `< rms_gate` → fail |
| 9 | coarse peak | surface | `best` | `find_peaks(distance=2)` then argmax over peaks | coarse index | `window.size < 3` → fail |
| 10 | coarse delay | coarse index | `g.delay_samples = m_lo + best − lag0` | arithmetic | **coarse samples** | search band intersected with `[0, L−F]`, so it is always one contiguous interval and cannot wrap |
| 11 | coarse → full-rate centre | coarse samples | `center = round(g.delay_samples) · q` | multiply by `q` | **full-rate samples** | `round` is exact here because the coarse surface has integer resolution |
| 12 | full-rate target window | full-rate samples | `tgt_f = read_f64(tgt, k·hop + center − 8, frame + 16)` | `_FULL_SEARCH_HALF = 8` (`w_half`) | `frame + 16` samples | zero-filled outside the stream |
| 13 | full-rate correlation | 2 × full-rate arrays | `gcc_phat_int`, `lag0 = w_half − center`, search `center ± w_half` | PHAT + irfft | surface | `F == 0 or L < F` → fail |
| 14 | full-rate peak/delay | surface | `g2.delay_samples = m_lo + best − lag0` | arithmetic | **full-rate samples, integer** | stays integer — there is no sub-sample step anywhere on this path |
| 15 | frame acceptance | confidence, RMS | `success ∧ conf ≥ min_confidence` | — | accept / `nan` | rejected frames are **kept** in `frames` with `delay_samples = nan` |
| 16 | global delay | accepted frame delays | `global_delay = median(delays[good])` | nan-aware median | full-rate samples | `usable == 0` → whole pair is `nan` |
| 17 | reported milliseconds | samples | `delay_ms = delay_samples · 1000 / SR` | — | **ms** | only conversion point from samples to ms |
| 18 | trajectory statistics | accepted frames | `mad_ms`, `spread_samples`, `step_ms`, `drift_ppm`, `drift_total_ms` | MAD / range / max adjacent diff / `polyfit(center_sample, delay, 1)` | ms, samples, ms, ppm, ms | `drift_ppm = slope·1e6` where slope is **samples per sample**; `drift_total_ms = slope·span·1000/SR` |
| 19 | constancy gate | stats + thresholds | `classify_constant` | `mad ≤ 1.0 ms ∧ spread_ms ≤ 1.0 ms ∧ ¬drift_bad` | bool | `drift_bad = |ppm| > 5 ∧ |drift_total_ms| > 0.1` |
| 20 | integer correction | `delay_samples` | `shift_stream` | `shift = rint(delay_samples)`; `out[n] = in[n + shift]` | samples | sample count preserved exactly; out-of-range filled with zeros |
| 21 | post-fix recheck | anchor + fixed stream | `recheck_residual` | ±5 ms local GCC then phase slope | **ms** | printed/shifted sample values are never altered by the estimate; residual gate 0.05 ms |

### 2.1 The diagnostic refinement stage (stage 4 of the production estimator)

Reached only when `usable ≥ min_usable_frames (5)` **and** `n ≥ 4096`:

| Input | Variable | Operation | Output | Boundary |
| --- | --- | --- | --- | --- |
| `anchor_segment_seconds` (s), `n` | `anchor_len = min(round(anchor_segment_seconds·SR), n)`, `a0 = (n−anchor_len)//2` | centre the anchor segment | samples | segment is taken from the **middle of the whole stream**, not near the measured frames |
| `global_delay` (samples) | `d_int = floor(global_delay + 0.5)` | deterministic integer rounding for negative values too | samples | — |
| two anchor segments | `phase_slope_fine(ref_seg, tgt_seg, band_hz=(200, 8000))` | weighted LS on in-band phase, weight `w = |G(f)|`, `Δτ = −Σ w f φ / (2π Σ w f²)` | `delta` in **samples** | `L < 4096` → `nan`; empty band → `nan`; `denom ≤ 1e-300` → `nan` |
| `d_int`, `delta` | `fine_delay_samples = d_int + delta` | sum | samples | reported as `fine_delay_ms`; **`fine_delay_samples` drives nothing** — no fractional correction exists |

## 3. Unit audit

Full table in `work/p2/unit-audit.md`. The three places where a unit changes, and the
conversion used in each:

| Boundary | In | Conversion | Out |
| --- | --- | --- | --- |
| ms → coarse samples (`search_window_ms`) | ms | `ceil(x · SR_c / 1000)` | coarse samples |
| coarse samples → full-rate samples | coarse | `· q` where `q = SR/SR_c` | samples |
| samples → ms (reporting, gates) | samples | `· 1000 / SR` | ms |
| seconds → samples (`anchor_segment_seconds`) | s | `round(x · SR)` | samples |
| ms → samples (`recheck_residual` `search_ms`) | ms | `ceil(x · SR / 1000)` | samples |

Status: **consistent**. Every delay that reaches a gate or a report is in full-rate
samples, and the only sample↔ms conversions are the two shown. `drift_ppm` is
samples-per-sample × 1e6, which is a dimensionless ratio — correct for a clock-drift
question, and it is a *slope of delay*, not a media-time error.

## 4. Sign audit

Project convention, stated in three independent places in the source
(`sync_estimate.py:27`, `sync_fix.py:5`, `p2_track.py:20`):

> `delay > 0` = the **target** arrives LATER than the reference. Correction is
> `out[n] = in[n + delay]`.

Derivation, not experience. With `lag0 = ref_start − tgt_start`, the correlation
surface index `m` satisfies `c[m] = Σ_n ref[n]·tgt[n+m]`, and the peak sits at
`m = lag0 + delay`, so `m_lo + best − lag0` is the delay — positive when the target is
late. Both instruments use this expression literally:

* `p2_track.gcc_phat_peaks` — line 127, `delay = float(m_lo + best - lag0)`
* `sync_estimate.gcc_phat_int` — line 451, `delay_samples = m_lo + int(best) − lag0`

And the correction is consistent with it: `shift_stream` writes `out[n] = in[n+shift]`,
which pulls the late target earlier by exactly `delay`. So the chain
*construction → peak location → reported delay → correction* uses one sign
throughout, and it was confirmed by measurement in Stage 2, not by reading:
`+1200` and `−1200` injections returned `+1200.0` and `−1200.0` (S03/S04), and
`+1 / −1` returned `+1.0 / −1.0` (S01/S02).

**Answer to the question the brief requires be settled without relying on experience:**
a `+1200`-sample injection is reported as `+1200`, and the correction moves the track
*forward* by 1200 samples.

Independent corroboration on the production path: the recorded positive controls give
`+1200 → fixed_shifts = {1: +1200}` and a two-sided control `→ {1: +1200, 3: −600}`,
i.e. the production fixer consumed the signed value with the same meaning.

## 5. Boundary audit

| Boundary | Value | Behaviour | Evidence |
| --- | --- | --- | --- |
| search lower/upper bound (P2 instrument) | ±`search_ms` | peak band intersected with `[0, L−F]`; contiguous, no wrap | S07/S08 exact at ±11998 with ±12000 range |
| array boundary in the fixture | padding `m = s + |d| + 16` | reference is never at an array edge, so no zero padding can be mistaken for signal | constructor is a single expression per case |
| window boundary (production, full-rate stage) | `tgt_f` window `center ± 8` samples | `read_f64` zero-fills outside the stream | code path 12 |
| window boundary (P2 instrument) | `tgt` window `st − s … st + seg + s` | a window whose target read comes back short is marked `edge` and **excluded**, not repaired | `fullrate_delay.py:115` |
| peak boundary (rim pinning) | `abs(abs(delay) − s) < 2` | flagged `pinned` and excluded from statistics | Stage 4 level 2 |
| edge handling (production) | `boundary_pileup(frames, search_samples) > 0.5` | triggers a wide-window re-measure to distinguish `out_of_range` | `channel_sync.py:774` |
| out-of-range at track level | `abs(delay_samples) > search_samples` | `out_of_range`, track untouched | `channel_sync.py:801` |
| `min_confidence` gate | default 0.3 | **secondary**: `success = v ≥ 2·nf` rejects first | Stage 3 |

The ±250 ms range is confirmed to behave as documented, including at its rim
(`±11500` samples = ±239.6 ms: exact, `pinned = 0`).

## 6. Precision audit

* **Coarse stage**: delay in coarse samples; one coarse sample = `q` full-rate samples
  = 125 µs at 48 kHz. The coarse stage only narrows the search to ±8 full-rate samples.
* **Full-rate stage**: the correlation surface has **integer** resolution
  (`find_peaks` on a sampled surface). There is no parabolic interpolation, no
  quadratic fit, and no upsampling. `g2.delay_samples` is an integer.
* **Every reported delay on every accepted frame is an integer number of samples.**
  1 sample = 20.833 µs at 48 kHz. This is the instrument's real floor, and the
  earlier claim of "0.024 ms floor" is 1/0.0208 ms ≈ *one sample* — i.e. it is the
  quantisation step, not a measurement uncertainty, and it was described correctly.
* **No code path produces a fractional delay for CORRECTION.** `fine_delay_samples`
  computes `d_int + delta` where `delta` can be fractional, but it feeds `fine_delay_ms`
  and `recheck_residual` only; `shift_stream` floors to `rint` and
  `core/audio_timeline.py` accepts only a single integer offset.
* **Sub-sample support status — CORRECTED (this document's original claim was wrong).**
  It said "NOT PRESENT in the measurement path". That conclusion came from auditing the
  *correction* path while skipping a *measurement* function already read earlier in the
  same session: `phase_slope_fine` (`sync_estimate.py:479-536`) is a working
  fractional-delay estimator.

  Stage 2.1 graded it against an independent fixture whose known answer is built by DFT
  phase-ramp resampling — deliberately a different construction from the time-domain
  sinc kernel such code usually uses, so a shared construction bug cannot hide:

  | truth (samples) | integer instrument | error | phase-slope | error | error (µs) |
  | --- | --- | --- | --- | --- | --- |
  | 0.500 | 0.0 | −0.500 | **+0.49999** | −0.00001 | 0.00 |
  | −0.250 | 0.0 | +0.250 | **−0.25001** | −0.00001 | 0.00 |
  | 23.370 | 23.0 | −0.370 | **+23.36998** | −0.00002 | 0.01 |
  | −12.345 | −12.0 | +0.345 | **−12.34495** | +0.00005 | 0.02 |
  | 101.750 | 102.0 | +0.250 | **+101.74999** | −0.00001 | 0.01 |
  | −100.600 | −101.0 | −0.400 | **−100.59996** | +0.00004 | 0.00 |
  | 137.0 | 137.0 | 0.000 | **+137.00000** | 0.00000 | 0.00 |
  | −137.0 | −137.0 | 0.000 | **−137.00000** | 0.00000 | 0.00 |

  **SUPPORTED.** Integer inputs round-trip exactly (and return the NaN degeneracy, since
  a perfectly aligned pair has zero in-band residual); fractional inputs are recovered to
  **≤0.00005 samples (≤0.02 µs)**. A second, independent implementation agrees:
  `tools/p2/estimate_trajectory.py`'s phase-slope stage reports 0.00000–0.00625 samples
  against its own known fractional injections.

  Two things this does **not** change. The integer instrument's 1-sample = 20.833 µs
  floor still governs every per-frame delay on the validated path, and S05/S06 remain
  NOT APPLICABLE *to that instrument*. And no fractional **correction** exists: the
  capability is measurable but unused, and `final-decision.md` §2 and §10 record why
  that is not the binding constraint.

## 6.1 Prototype sanity — re-adjudicated

`tools/p2/estimate_trajectory.py` was classified **INVALID INSTRUMENTATION** in an
earlier draft of this document, on the strength of a `--no-selftest` run that produced
`k_eff_bins = 0` / `peak_below_noise` on 0/40 windows. That classification is
**WITHDRAWN**, because the prototype was still being edited while that run was made: its
report grew from 18 984 to 46 367 bytes and it wrote further outputs (`trajectory_*.json`,
`g_selftest.txt`) hours after the run in question.

Re-run this session: `python tools/p2\estimate_trajectory.py --selftest` completes with
**exit 0, 8 of 8 sections passing** — integer delays ±2400 exact, fractional delays to
0.00625 samples, an out-of-range delay correctly declined, ramps recovered to 0.12
samples median error, geometry independence across 10 s/20 s windows, and — the section
that matters most for this session — a cross-check stating agreement with the project's
already-validated `p2_track.gcc_phat_peaks` to **0.000 samples** on injected delays.

That is a genuine instrument with a genuine self-test, and the earlier
`peak_below_noise` gate evidently was a defect that has since been fixed. It is still
**not** used as a source of any number in this session's deliverables: those come from
`fullrate_delay.py` / `p2_track.gcc_phat_peaks`, validated independently in §8. Its
claim of agreement with that instrument is corroborating, not load-bearing.

What remains open and unfalsified: §4 of its report attributes a constant ≈+250 ms offset
to the round-1 reference trajectories. The script that produced that comparison
(`g_reconcile_geometry.py`) is not on disk and the reference files it names do not exist
in the repository, so this session could neither reproduce nor refute it. It is recorded
as **UNRESOLVED** in `final-decision.md` §10. It does not affect any span in this
document, which are reproduced independently.

## 7. Confidence and gate audit (which instrument is being gated)

| | Production estimator | P2 research instrument |
| --- | --- | --- |
| gate on acceptance | `success ∧ confidence ≥ min_confidence` | none |
| confidence formula | 3 components, §Stage 3 | n/a |
| ambiguity metric | `ratio_score` from the second peak (feeds confidence) | `ratio = best/second`, recorded, **used only as a flag in earlier analysis** |
| rim handling | `boundary_pileup` then wide re-measure | `pinned` exclusion |
| sensitivity to window length | frames are 200 ms with 100 ms hop, fixed | chosen per experiment |

The `ratio` threshold of 1.3 that earlier analysis used to call a window "ambiguous"
belongs to the **research** instrument, not to production. Stage 4 level 2 measured
what that flag is worth: across true delays that the search range could not see, the
observed `ratio` was **1.01 – 1.34** — it does not separate a wrong in-range peak from
a right one in this situation, which is consistent with the earlier retraction of the
ratio criterion and adds an independent reason to distrust it.

## 8. Synthetic / known-answer validation

Stage 2 (`work/p2/scripts/p21_stage2_synthetic.py`, results in
`work/p2/out/synth-instrument/stage2.json`). Fixture base is a real 120 s segment of
C1305 CH3 at a fixed offset, zero-padded by `s + |d| + 16` samples each side; the
target is `tgt[n] = pad[n − d]`, so the truth is exact and the sign is produced by one
expression. No RNG is used in any fixture.

| Fixture | Truth (samples) | Estimate | Error | Ratio | Pinned |
| --- | --- | --- | --- | --- | --- |
| S00 zero | 0 | 0.0 | 0.0 | 20.358 | no |
| S01 +1 | +1 | +1.0 | 0.0 | 20.358 | no |
| S02 −1 | −1 | −1.0 | 0.0 | 20.358 | no |
| S03 +1200 | +1200 | +1200.0 | 0.0 | 20.358 | no |
| S04 −1200 | −1200 | −1200.0 | 0.0 | 20.358 | no |
| S07 lower rim−2 | −11998 | −11998.0 | 0.0 | 20.358 | no |
| S08 upper rim−2 | +11998 | +11998.0 | 0.0 | 20.358 | no |
| S05/S06 ±0.5 sample | — | — | — | — | **NOT APPLICABLE** |

Stage 4 level 1 re-tested the same instrument on a different base segment and window
geometry with 22 further known-answer cells (`work/p2/out/fine-resolution/`): all
constant-delay cells exact, all 11 cells of the ±250 ms range check exact
(`stage4_narrow.json`).

### Instrument status

**INSTRUMENT_VALIDATED** — for the integer-precision, full-rate, wide-window
measurement it is used for. Unit audit: consistent. Sign audit: consistent, measured
both directions. Boundary audit: exact at ±11998 of ±12000, no pinning.
Refinement audit: there is no refinement on this path, and none is claimed.

## 9. Instrument limitations (what it cannot do)

1. **Integer resolution.** No fractional delay is measurable. Anything below 1 sample
   (20.8 µs) is invisible; claims of sub-sample structure in real corpus data cannot
   come from this instrument.
2. **Window averaging — the real limit.** A long window returns a
   correlation-weighted average of the delay over the window, and near a
   discontinuity it can return a value that lies at **neither** side. Measured in
   Stage 4 level 1: for a +1200-sample step exactly at the window centre, the 120 s
   and 45 s windows reported `0` (error −1200 samples = −25 ms) while the 15 s window
   reported `+1200` exactly. The useful time resolution of a delay trajectory is
   therefore set by the **window length**, not by the sample rate.
3. **No validity criterion.** Nothing in the instrument distinguishes a correct peak
   from a wrong one in the ordinary case. `ratio` was tested and failed (§7). This is
   why P2 has no defensible "when to correct" rule, and why the fallback
   (`untouched`) is the correct production state.
4. **Independent windows only.** `fullrate_delay.py` measures each window
   independently, so a wrong window cannot propagate — but nor is there any tracking
   continuity to help. `p2_track.track_pair` offers leapfrog tracking with a ±6 ms
   window; that mode was **not** used for any number in this document, because a
   tracker that follows its own previous answer cannot report a step it failed to see.
5. **`p2_track`'s track mode carries an unvalidated window assumption** and its
   `ambiguous` flag (`ratio < 1.3`) is retracted as a validity criterion, per §7 and
   the earlier phase's negative control.

## 10. Prototype sanity (Stage 1E)

