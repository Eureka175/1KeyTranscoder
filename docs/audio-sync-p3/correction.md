# P3 — Correction Prototype

> Stage 6 of Audio Sync P3. Source: `work/p3/correct.py`. **This is a prototype, not
> production.** At the time of writing nothing here has touched `core/`, and the production
> gate (Stage 11) has not been reached.
>
> The order is deliberate and is not skipped: **integer → fractional → resampling.** This
> document covers the integer stage, which is the only stage that has been built.

## 1. Three layers, and why the separation is load-bearing

```text
Layer A   raw measurements        every window, valid or not          -> never read downstream
Layer B   validated measurements  Layer A minus refused windows       -> the only plan input
Layer C   fitted trajectory       a model fitted to Layer B           -> stage 5
```

`plan` is built from **Layer B only**. A window the validity criterion refused contributes a
gap, never a number. This is the barrier that P2 identified as missing, and without it the
chain `wrong measurement → trajectory → correction → worse audio` has nothing in it.

## 2. `build_plan` — the decisions it makes explicit

| Decision | Implementation | Why |
| --- | --- | --- |
| delay → shift direction | `shift = -round(delay)` | `delay > 0` means the target arrives LATE, so it must be ADVANCED. `out[n] = in[n + shift]` |
| time resolution | one segment per window, `hop_s` long | the plan cannot be finer than its measurements, and pretending otherwise is how a corrector gets a residual it did not earn |
| invalid regions | explicit gap, `shift = 0` (`untouched`) or the last valid shift carried (`hold`) | the brief requires an explicit strategy; nothing is interpolated across a gap unless that was chosen |
| segment merging | neighbours with the same shift are merged, `n_shift_changes` counted honestly | so "how many boundaries did we create?" is a measured number, not a segment count |
| auditability | every segment keeps `window_id`, `center_s`, `valid`, `reason` | the plan must answer: why correct, when, by how much, on what evidence, and which windows were refused |

## 3. `apply_plan` — the correction itself

```text
out[n] = in[n + shift(n)]        out-of-range reads are zeros, sample count preserved
```

* **Chunked**, O(chunk) memory. A 230-million-sample track is never materialised.
* **Crossfaded at every boundary.** Where the shift changes, the two reads are mixed on a
  linear ramp over `crossfade_ms` (default 20 ms). P2 measured boundary artefacts of up to
  **+14.2 dB** at hard segment edges, and an earlier independent measurement put a 1-sample
  boundary flip at **+13.7 dB above signal RMS**. The crossfade exists because that is a
  measured problem, not a hypothetical one.
* **Sample count is preserved exactly**, and the report records the output byte count
  against the expected byte count so a truncation cannot pass unnoticed.

## 4. Known-answer selftest

Run before the prototype is allowed near real audio. `python work/p3/correct.py selftest`:

| Case | What it proves | Result |
| --- | --- | --- |
| identity plan | an all-zero plan is bit-exact (`max|diff| = 0.000e+00`) | **PASS** |
| constant `+137` | exact integer advance, trailing zeros, count preserved | **PASS** |
| constant `−137` | exact integer delay, leading zeros, count preserved | **PASS** |
| step `100 → 200`, 1 boundary | equals the exact shift *away from the crossfade*, and the crossfade region is a genuine blend lying between the two shifted signals | **PASS** |
| three segments, 2 boundaries | same, with the boundary count checked | **PASS** |
| invalid-gap policy | the policy is visible in the plan: `hold` gives `[-500, -500, -520]`, `untouched` gives `[-500, 0, -520]` | **PASS** |

The step and multi-segment cases are checked by **property** (exact away from the boundary,
a blend inside it) rather than against a re-implementation of the crossfade. A reference
that copies the implementation compares the code with itself and passes even when both are
wrong.

## 5. Verification, and the trap it is built around

P2's prototype zeroed its residual **at the geometry that produced the plan** — a
self-fulfilling measurement — and on a grid it had never seen carried 0.92×–1.65× the
original span. `verify()` therefore:

1. re-measures the **uncorrected** signal on the verification grid, not just the corrected
   one, so the comparison is like for like;
2. uses a verification grid **finer than the plan's hop** (default 5 s against a 10 s hop,
   and a different window length), so the plan cannot have been fitted to it;
3. reports the **span ratio** and the RMS ratio, not a single residual;
4. additionally runs a **structurally different** integer measurement
   (`reference_integer_measure`: plain cross-correlation of the band-passed signals, no
   PHAT weighting, no interpolation, no validity model) on the same grid, so a claim that
   the residual shrank has to survive a method that shares no code with the one that built
   the plan.

## 6. Result on real material — the prototype fails its own verification

C1305 `ch3→ch2`, 129 segments, 87 crossfades, sample count preserved exactly
(230 750 520 samples), 22 s. Verified on a 10 s grid the plan never saw:

| measure | before | after | ratio |
| --- | --- | --- | --- |
| main instrument, span | 18.771 ms | 18.771 ms | **1.000** |
| main instrument, RMS | 3.786 ms | 3.808 ms | 1.006 |
| **independent** integer cross-correlation, span | 1179 samples | 978 samples | **0.830** |

**No improvement.** One method reports nothing changed; the structurally different method
reports a 17 % change it cannot call an improvement. The correction neither helps nor
measurably harms, and it rewrites 100 % of the samples to achieve that.

### 6.1 A plan defect that the audit caught

Under the first invalid-gap policy a **leading** invalid gap took shift 0 while the first
valid window carried a large shift, so the plan contained a **1192-sample (24.8 ms) step** —
from a pair whose measured delay moves only 1.4 ms. It was caught because the plan reports
`shift_span_samples`: **1192 against a measured span of 66**.

An explicit `edge` policy (a leading gap takes the first valid shift rather than zero) fixed
it: **`shift_span 1192 → 66` samples**, matching the measured span exactly. This is the
clearest demonstration in P3 of why the plan must be auditable — the defective plan was
silent in the audio and loud in the JSON.

### 6.2 Why it fails, and it is P2's finding one order of magnitude finer

The plan's measured span is **1.376 ms**; the span at the verification geometry is
**18.771 ms**. The plan can only model what its grid resolves, and **~93 % of the actual
movement is below its resolution.** P2 recorded exactly this from a 120 s grid:

> "A per-window integer-shift plan built on a 120 s grid leaves the sub-grid movement
> uncorrected, so the residual is unchanged or worse on a finer grid."

P3 reproduces it at 30 s / 10 s, with a measurement instrument whose *measurement* is four
orders of magnitude more precise and whose *resolution* is still nowhere near enough.

## 7. What is *not* claimed

1. **The correction is not validated as a product.** It failed its own verification on the
   one recording measured end to end (§6).
2. **No fractional work has been done.** Sub-sample delays are rounded to integers; the
   rounding residual is bounded by half a sample (0.0104 ms) and is reported through the
   measured residual, not assumed away.
3. **No resampling has been done, and none is justified.** Resampling needs a persistent
   systematic slope, which the trajectory did not establish — and the integer plan already
   fails for a resolution reason a finer corrector cannot see.
4. **Boundary artefacts are mitigated, not eliminated.** A crossfade removes the step; it
   does not create signal that was not there. `max|delta|` and the fraction of samples
   changed are reported so the intervention is visible in the numbers (100 % here).
