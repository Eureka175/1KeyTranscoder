# Audio Sync P3.6 — Safe Gap Policy under Irresolvable Measurement Uncertainty

**Status: research evidence only. This document does not authorise a production change.**
`production = NOT READY` is unchanged by P3.6 (§78).

P3.6 asks a narrower question than P3.1–P3.5 did:

> When the true delay inside a gap cannot be measured, **which gap-filling behaviour avoids
> creating a larger artificial correction error than the uncorrected state?**

It is not a second-geometry study (P3.5 failed at that and P3.6 does not revisit it) and
it is not a correction redesign.

---

## 1. What "the current gap policy" actually is

Established by the Stage 1 audit (`work/p3/p36/policy_audit.md`), and it is worth stating
because the name overlaps with something else:

| Layer | Code | Behaviour in a gap |
| --- | --- | --- |
| **Production** | `core/sync_fix.py::shift_stream` | **No gap policy exists.** One scalar delay per track; no per-window plan, no gap. |
| **P3 prototype** | `work/p3/correct.py::build_plan` | Piecewise-constant plan with an explicit invalid-region policy. |

So a gap policy is **not a shipped behaviour**. It becomes reachable only if a
time-varying plan is ever authorised, which has not happened. Production currently refuses
non-constant delays outright (`non_constant`, asserted by the P1 guard).

The prototype's policy — called `current` throughout P3.6 — is `INVALID_EDGE`:

- an **accepted** window applies `shift = rint(delay)`, the correct action;
- a **refused** window carries the **previous** accepted window's shift;
- a **leading** gap (no accepted window before it) takes the **first** accepted window's
  shift instead of 0;
- segments are merged when shift and validity match; the plan is piecewise-constant with a
  20 ms crossfade at boundaries.

Every frozen P3 artifact was built with `edge`, verified at all five call sites and in the
plans themselves (`"invalid_policy": "edge"`).

---

## 2. Why the real corpus cannot answer the question

**There is no ground truth inside a gap — that is what a gap is.** The estimator's
refusal does not erase the value (`measure.py` stores `delay_samples` for every window and
merely flags `valid = False`), but a refused estimate is not truth.

P3.4 got as far as the evidence allows: comparing the plan's carried shift against the
refused windows' own estimates, **58.6 – 67.5 %** of those estimates fall inside the range
spanned by their two bounding accepted windows (at P3.4's ±2-sample tolerance; see §6 for
why the tolerance matters). That bounds the problem. It cannot decide it.

P3.6 therefore measures error where truth is **constructed**, and takes from the real
corpus only its *structure*: gap lengths, movement amplitudes, acceptance rates.

---

## 3. The real gap structure (measured, not assumed)

From the four frozen plans (`work/p3/p36/real_gap_structure.json`):

| case | windows | refused | gap runs | median gap | longest gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| C1305 10 s | 478 | 231 (48.3 %) | 55 | 20 s | **530 s** |
| C1305 2.5 s | 1911 | 931 (48.7 %) | 93 | 10 s | **530 s** |
| C1306 10 s | 414 | 106 (25.6 %) | 35 | 20 s | 180 s |
| C1306 2.5 s | 1654 | 422 (25.5 %) | 52 | 9 s | 175 s |

Two properties govern everything downstream:

1. **Heavy tail.** Most gaps are 1–2 windows, but C1305 contains gaps of 43 and 53
   windows — **430 s and 530 s of unobserved audio**. A carry policy there is asserting
   that the delay did not change over nine minutes.
2. **The hop rescales the count, not the duration.** C1305's longest gap is 530 s at
   *both* hops (53 × 10 s, 212 × 2.5 s). Any statement about gap length must state its hop.

---

## 4. The measured behaviour of each candidate

Five policies, at a fixed definition (`work/p3/p36/policies.py`; 55/55 selftest,
`work/p3/p36/synthetic.py` 31/31):

| policy | in a gap |
| --- | --- |
| `current` | carry previous; leading gap takes the first accepted shift |
| `left_hold` | carry previous; leading gap has nothing to carry → 0 |
| `right_hold` | carry next; trailing gap has nothing to carry → 0 |
| `linear` | interpolate between the bounding accepted shifts |
| `no_correction` | shift 0 everywhere in the gap |

### 4.1 On real gap masks with constructed truth

`current`, `left_hold` and `right_hold` are **numerically indistinguishable on real
corpora** — identical to the sample on every case and every amplitude tested. Real gaps in
these corpora are predominantly interior, and all carry policies agree on an interior gap.
They separate only under the deliberately adversarial "step inside *every* gap" placement.

### 4.2 Error on the gap audio (max observed, samples; 48 = 1 ms)

**Discontinuity inside the longest real gap** (C1305 10 s, gap 530 s):

| step amplitude | `current` | `left_hold` | `right_hold` | `linear` | `no_correction` |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 50 | 50 | 50 | **25** | 50 |
| 142 | 141 | 141 | 142 | **70** | 142 |
| 400 | 401 | 401 | 402 | **200** | 400 |
| 670 | 670 | 670 | 670 | **336** | 670 |
| 1188 | 1188 | 1188 | 1188 | **595** | 1188 |

A discontinuity the policy cannot see produces an in-gap error **equal to the step size**
under every carry policy and under `no_correction`. `linear` splits it and lands at
roughly half. This is the irreducible cost: the information is not in the measurements.

### 4.3 Where `no_correction` is best — the ZERO control

| trajectory | gap | `current` | `left_hold` | `right_hold` | `linear` | `no_correction` |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| constant (all gap classes) | any | 0–1 | 0–1 | 0–1 | 0–1 | **0** |
| quiet_then_move | 5 windows | 0 | 0 | 0 | 0 | **0** |
| slow_ramp | 20 windows | 29 | 29 | 29 | **1** | 302 |
| reversal | 5 windows | 6 | 6 | 5 | 6 | **432** |
| step | 10 windows | 576 | 576 | 577 | **262** | 577 |

`no_correction` is **exactly optimal on a quiet gap and worst-possible on a moving one**,
because it discards both the measurement and the movement. `linear` is best on smooth
ramps (it tracks them) and on discontinuities (it halves the step), but is not better than
carry on a step that lands at a gap edge.

### 4.4 The trade-off, stated without a ranking

```text
current / left_hold   right on constant and slow-movement gaps
                      wrong by the full step when a discontinuity hides in a gap
                      cannot degrade below the step size, but will reach it

linear                halves the discontinuity error; tracks smooth movement
                      can be worse than carry when the discontinuity is at an edge

no_correction         perfect when the delay is genuinely ~0
                      error equals the full delay magnitude otherwise
                      the only policy whose worst case is UNBOUNDED by the trajectory
```

Forty-eight samples is one millisecond. A 670-sample error is **14 ms**; the worst
`no_correction` case observed was 65 340 samples, **1.36 s**.

### 4.5 `linear` is not uniformly the safer choice — the sign flips with gap position

An appealing simplification is "carry when the gap is narrow, interpolate when it is wide".
**The measurements do not support it.** The comparison between `linear` and a carry policy
changes sign depending on **where inside the gap the discontinuity falls**:

| where the discontinuity falls | better policy | measured |
| --- | --- | --- |
| at the gap's **closing edge** (adjacent to an accepted window) | **carry** | `current` error **0** vs `linear` **600** at amp 1200; and 1 vs 594 at amp 1188 |
| in the gap **interior** | **`linear`** | ~**half** the carry error at every gap length from 1 to 20 windows |

At a 10 s `step` with the discontinuity on the closing edge, a forward carry inherits the
previous accepted window's value, which is still current — so it is *exactly right*, while
`linear` interpolates toward the far side of a step and lands halfway up it. Move that same
discontinuity into the middle of the gap and the ranking reverses.

```text
gap length (windows)   1      2      3      5     10     20
current              1200   1200   1200   1200   1200   1200
linear                600    400    600    600    545    571     (step at gap midpoint)
```

**The discriminator is unobservable.** Position of a discontinuity inside a gap is exactly
what a gap conceals, so neither policy can be selected on evidence — only on an assumption
about where movement tends to happen relative to refusals.

### 4.6 Measurement precision does not help

Sweeping the synthetic observation noise over σ = 0.0 / 0.76 / 2.0 samples moves the in-gap
error by **a few samples out of 100–1900**. The gap problem is **deterministic given the gap
structure**: it is missing information, not noisy information, and no improvement in
estimator precision removes it. This forecloses an otherwise attractive direction.

---

## 5. The design constraint this supports

The evidence supports one constraint and one caution.

**Constraint.** Inside a gap the error is **not observable**. Any policy commits to a
value the data cannot check. Therefore an unresolved-gap correction cannot be *verified*
in the field — only bounded by assumption. A design that assumes its gap policy is correct
because it validates well on the corpus is validating the wrong thing.

**Caution, and it is specific.** The intuitive fix — "only carry across *short* gaps, and
reset across long ones" — is **not supported by the measurements**. C1306 has 175–180 s
gaps and never exceeds a 79-sample shipped-policy jump; C1305 has 530 s gaps and reaches
670. **Gap duration does not predict jump size.** The predictor is how far the delay moved
while the policy was blind, which is exactly the quantity no measurement can supply. A
duration threshold would therefore be a threshold on the wrong variable.

**But the C1305 / C1306 contrast must not be over-read, and this is a P3.6 finding.** The
obvious gloss — "C1306 is the same mechanism at smaller amplitude" — is **not supported**.
Decomposing each plan's total absolute step into the part at gap edges and the part inside
accepted runs (Agent B's attack A5, `work/p3/p36/agent_b/attacks.json`):

| case | sum &#124;step&#124; at gap edges | sum &#124;step&#124; inside accepted runs | max step at edge | max step inside runs |
| --- | ---: | ---: | ---: | ---: |
| C1305 10 s | 1974 | 552 | **670** | 25 |
| C1305 2.5 s | 2889 | 814 | **763** | 15 |
| C1306 10 s | 242 | **743** | 79 | 54 |
| C1306 2.5 s | 366 | **994** | 79 | 9 |

On C1305 the correction's large steps are overwhelmingly at gap edges — the gap-policy
mechanism dominates. **On C1306 they are not: most of its step budget sits inside accepted
runs**, where no gap policy applies and the plan is following measurements. C1306 is
therefore **not a low-amplitude copy of C1305**; its residual structure is different in
kind. All four decompositions returned an `A5` verdict of `PLAUSIBLE`, i.e. this is
supporting evidence rather than a decision.

```text
observable      : which windows were refused, for how long, and the shifts either side
unobservable    : the delay inside the gap
therefore       : policy error cannot be measured post hoc, only bounded by a prior
                  about how far a delay moves in a given time
```

---

## 6. A correction to how one borrowed number should be read

P3.4's neighbour-range figure (58.6 – 67.5 % inside) **reproduces exactly** on all four
cases when recomputed per refused window at P3.4's own ±2-sample tolerance. A first P3.6
check wrongly reported it as unreproducible by summing the wrong field; that retraction is
recorded in `work/p3/p36/reports/safety-envelope.md` §8 and the flawed script is kept with
a `RETRACTED` flag.

The new evidence is that **the tolerance does most of the work**:

| case | ±0 samples | ±2 (P3.4) | ±3 | ±5 |
| --- | ---: | ---: | ---: | ---: |
| C1305 10 s | 23.1 % | 58.6 % | 65.6 % | 67.7 % |
| C1305 2.5 s | 30.9 % | 59.5 % | 63.7 % | 69.4 % |
| C1306 10 s | 26.6 % | 60.6 % | 73.4 % | 77.7 % |
| C1306 2.5 s | 33.9 % | 67.5 % | 78.7 % | 84.3 % |

At zero tolerance only 23–34 % of refused estimates lie inside their neighbours' range.
The ±2 tolerance is defensible — estimator repeatability is 0.76 samples (P3.2) and
integer rounding alone contributes up to 1 — but the conclusion "the plan's carried shift
is usually the outlier" is **SUPPORTED at ±2 and much weaker at ±0**. The figure should be
quoted with its tolerance attached.

### 6.1 A truncation trap that produces a false "correction"

P3.6 reported this figure as unreproducible **twice**, with two different explanations, and
**both were wrong**; Agent B found the real cause. It is recorded here because anyone
recomputing the number from the persisted artifacts will hit it.

`work/p3/p34/gap_residual.py:329` stores a **display-truncated** list:

```python
gap_runs.sort(key=lambda g: -abs(g["plan_error_at_start"]))   # line 320 — DESCENDING
result["gap_runs"] = gap_runs[:60]                            # line 329
```

So the persisted `gap_runs` is the **top 60 runs by |plan error|**, while the aggregate
`gap_run_verdicts` covers **all** runs. C1305 2.5 s has 91 runs, so 31 runs holding 177
refused windows are absent (641 persisted vs 818 aggregated).

**Summing the persisted list gives a biased answer, not merely an incomplete one.** Because
the sort puts the largest errors first, the dropped runs are disproportionately those where
the carried value was *closest* to its own diagnostic — the **inside** ones, 159 of the 177
missing windows. The subset therefore **deflates** the inside fraction:

```text
487/331 = 59.5 %   P3.4's aggregate over ALL runs   <- CORRECT
328/313 = 51.2 %   the truncated top-60 subset      <- TRUNCATION ARTEFACT, do not quote
```

The artefact is recorded with `do_not_quote: true` in
`work/p3/p36/reports/p34-neighbour-verification.json`.

The only fair criticism of P3.4 on this figure is narrower: its prose band covers **3 of its
4 cases** (C1306 10 s is absent from its table), and including the fourth widens the outside
band from "33–41 %" to **32.5–41.4 %**. A presentation gap, not a reproduction failure.

---

## 7. What P3.6 does not establish

- **No best policy.** No policy is scored, ranked or recommended. The candidate policies
  are distinguishable only by which *kind* of trajectory they fail on, and the choice
  between them is a choice about a prior on in-gap movement, not a measurement.
- **No theoretical bound.** Every number is a maximum observed over the scenarios run. A
  case that was not run could be worse.
- **No production threshold.** The 50/100/200/500-sample bins are forensic.
- **No method to verify a gap decision in the field.** That is the finding, not a gap in
  the work.
- **No resolution of the in-gap truth on the real corpus.** It remains `UNKNOWN`, as P3.5
  left it.

---

## 8. Cross-references

| Document | Content |
| --- | --- |
| `work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` | the full P3.6 report with all cross-checks |
| `work/p3/P3.6-NIGHT-RUN-SUMMARY.md` | experiment ledger, agent status, git state |
| `work/p3/p36/policy_audit.md` | Stage 1 audit of the shipped policy |
| `work/p3/p36/reports/safety-envelope.md` | the empirical envelope, all 298 scenarios |
| `work/p3/p36/reports/real-policy-comparison.md` | observable behaviour on C1305 / C1306 |
| `docs/audio-sync-p3/measurement.md` … | the P3.1–P3.5 layer this builds on |
