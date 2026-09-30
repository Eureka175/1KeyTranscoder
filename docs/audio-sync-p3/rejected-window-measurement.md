# Rejected-Window Measurement

**Phase: P3.4. Status: `direct gap residual = MEASURED`. `unobserved-gap / invalid-region
policy = CONFIRMED` as the dominant residual source, with the quality tiering quantified.**

---

## 1. Why P3.3 needed this, and why the premise turned out to be wrong

P3.3 left the mechanism `SUPPORTED` rather than `CONFIRMED` for one stated reason: the
residual on **refused** windows was not measurable, because the window-level gate refused to
measure there. That was the phase's own framing, and it was wrong.

**Stage 1's finding.** `work/p3/measure.py::measure_window` computes and stores
`estimated_delay_samples` at line 80 and sets `valid = True` only at line 232. A refusal is a
**flag on a finished measurement**, not a suppression of one. Every refused window in all four
artifacts already carries:

```text
delay_samples, peak_width, peak_over_noise, peak_rel, interp_at_grid_edge,
bw_hz, probe_step_max, boundary_distance, width_expected, motion_monotone
```

| dataset | windows | refused | refused **with** a delay | refused **with** peak metrics |
| --- | ---: | ---: | ---: | ---: |
| C1305 2.5 s | 1911 | 931 | **931** | 931 |
| C1305 10 s | 478 | 231 | **231** | 231 |
| C1306 2.5 s | 1654 | 422 | **422** | 422 |
| C1306 10 s | 414 | 106 | **106** | 106 |

So no diagnostic estimator had to be built, no synthetic diagnostic validation was needed,
and nothing had to be un-gated. Three of the brief's requirements were already satisfied
*structurally*:

* **same axis** (§13–14) — the value is not derived from anything; it **is** the record
  `build_plan` read. There is no second measurement pass, so there is no axis to align. This
  is what makes the number below admissible where P3.3's `correct.py::verify` comparison was
  not.
* **no gate removal** (§10, §52) — `valid` keeps its meaning. The refused record is read *in
  addition to* the verdict, never instead of it.
* **rejected ≠ no data** (§12) — already true in the artifacts.

What was missing was the analysis. P3.3 measured the residual only on accepted windows, where
`shift = round(delay)` makes it zero **by construction**.

## 2. The quantity and its quality

    direct_error(w) = stored_delay(w) − plan_shift(w)

Same axis, same sign convention, same window. No conversion anywhere.

A refused estimate is not ground truth, so every row carries a tier, and the tier boundaries
are **percentiles of the accepted population of the same corpus** (brief §26) rather than
numbers chosen here:

| tier | definition |
| --- | --- |
| `production_valid` | the gate accepted it |
| `refused_quality_ok` | refused, vertex interior, `peak_over_noise` ≥ the accepted p10 |
| `refused_vertex_at_edge` | refused with `interp_at_grid_edge` set |
| `refused_low_quality` | refused, vertex interior, `peak_over_noise` below the accepted p10 |

### Does the tiering separate? Yes, over four orders of magnitude

| tier | C1305 2.5 s | C1305 10 s | C1306 2.5 s |
| --- | ---: | ---: | ---: |
| `production_valid` median \|err\| | **0.25** | **0.27** | **0.26** |
| `refused_quality_ok` median | 1.80 | 2.92 | 2.44 |
| `refused_vertex_at_edge` median | 7.98 | 8.42 | 3.00 |
| `refused_low_quality` median | **655.29** | **657.96** | **21.36** |

`refused_low_quality` windows are wrong by hundreds of samples — those are the narrowband and
weak-peak refusals, and their estimates are noise. `refused_quality_ok` windows are good to
**1.8–2.9 samples**. Nothing is compared quantitatively without its tier.

## 3. The direct gap residual

| | C1305 2.5 s | C1305 10 s | C1306 2.5 s |
| --- | ---: | ---: | ---: |
| carrying segments, median \|err\| | 0.25 | 0.27 | 0.26 |
| **gap segments, median \|err\|** | **8.15** | **10.0** | — |
| gap segments, p95 | 759.09 | 756.0 | — |
| gap segments, max | 1145.9 | 789.7 | — |
| gap segments **quality_ok**, median | **1.80** | 2.92 | 2.44 |
| gap segments quality_ok, p95 | 8.27 | 372.6 | 21.8 |

Two readings, and both matter:

1. **The gap residual has a heavy tail**: median 8–10 samples but p95 ≈ 756. Most gap windows
   are nearly right and a minority are wrong by hundreds.
2. **For `quality_ok` gap windows the residual collapses to 1.8 samples** with a p95 of 8.3.
   So the tail lives almost entirely in `refused_vertex_at_edge` and `refused_low_quality` —
   the tiers the window gate exists to refuse.

## 4. The sharper test: whose value is the outlier?

For every gap bounded by accepted windows on both sides, compare each refused estimate against
the range spanned by its two bounding accepted windows. A refused estimate inside that range
cannot be the source of the error — the plan's carried shift is.

| | C1305 2.5 s | C1305 10 s | C1306 2.5 s |
| --- | ---: | ---: | ---: |
| gap runs bounded on both sides | 91 | 53 | 50 |
| refused estimates **inside** the neighbours' range | 487 | 109 | 253 |
| refused estimates **outside** | 331 | 77 | 122 |
| **inside fraction** | **59.5 %** | **58.6 %** | **67.5 %** |

**Roughly 60–68 % of refused estimates are consistent with the delay simply moving between
its neighbours** — which is exactly what the `edge` policy fails to do when it carries one
neighbour's shift across the gap.

### Two hotspots, and they show opposite failure modes

**t = 700 s, C1305 10 s — the plan is wrong:**

```text
t=  660.0  delay= 615.0  valid=True   shift= 615  err=   0.0
t=  670.0  delay= 614.8  valid=True   shift= 615  err=  -0.2
t=  680.0  delay= 616.9  valid=False  shift= 615  err=   1.9   <- refused, and right
t=  690.0  delay= 522.5  valid=False  shift= 615  err= -92.5   <- refused, and right
t=  700.0  delay= 529.7  valid=True   shift= 530  err=  -0.3
```

The refused diagnostics report **522.5** where the plan carries **615** — a **92.5-sample**
error. The refused estimate tracks the delay through the transition; the carried shift does
not. This is the mechanism, directly measured on the same axis.

**t = 100 s, C1305 10 s — the diagnostic is the misleading one:**

```text
t=  80.0  delay= 720.7  valid=True   shift= 721  err=  -0.3
t=  90.0  delay= 717.2  valid=False  shift= 721  err=  -3.8   <- refused, agrees with plan
t= 100.0  delay= 603.3  valid=True   shift= 603  err=   0.3
```

Here the refused window agrees with the plan and both sit ~115 samples from the next accepted
value, because the delay really does step inside the gap. A refused estimate that agrees with
the plan is not evidence against it, and P3.3's top-two boundary jumps (670 and 664 samples at
t = 4170 and 4330 in the 10 s grid) are of this kind.

## 5. Mechanism classification

```text
unobserved-gap / invalid-region policy = CONFIRMED   as the dominant residual source
  basis: direct same-axis measurement; the refused estimate differs from the plan's
         carried shift by up to 92.5 samples at t=700 s and by hundreds elsewhere;
         median gap residual 8-10 samples against 0.25 on carrying segments.
  limit: CONFIRMED as "the plan's carried shift is where the error is", NOT as
         "the error is always X samples". Two of P3.3's largest boundary jumps
         (t = 100, and the 670/664 pair) are genuine in-gap steps where the
         refused estimate AGREES with the plan.

diagnostic estimate reliability        = QUANTIFIED
  production_valid      0.25 samples median
  refused_quality_ok    1.8-2.9 samples median
  refused_vertex_at_edge ~3-8 samples median, p95 ~750
  refused_low_quality   ~655 samples median -- unusable
```

**P3.3's `SUPPORTED` is upgraded to `CONFIRMED`, with a stated limit.** The upgrade is earned
by the direct measurement; the limit is earned by t = 100 s, where the same instrument shows
the plan agreeing with the refused estimate and the movement being real.

**P3.3's staircase rejection is unaffected** and is not re-opened.

## 6. Limitations

1. **`refused_low_quality` estimates are unusable** (655 samples median error). They are
   reported and excluded from every quantitative claim.
2. **The in-gap truth for the 33–41 % of refused estimates outside their neighbours' range is
   not independently established.** A second structurally different estimator was not run; the
   tier analysis serves that role and is weaker than a second instrument.
3. **The `err = delay − shift` identity is exact by construction on carrying segments**, so the
   0.25-sample median there is a consistency check, not evidence of accuracy. The evidence is
   the gap column.
4. **No alternative gap policy was tested.** P3.4 measures; it does not redesign (brief §48).

## 7. Provenance

```text
branch    research/audio-sync-p3-rejected-window-measurement
script    work/p3/p34/gap_residual.py
output    work/p3/p34/{c1305,c1306}/{10s,2p5s}/gap_residual.json
          work/p3/p34/diagnostics/summary.json
inputs    four existing measurement JSONL files; NO new measurement, NO new estimator
frozen    work/p3/p32/validation_manifest.json -- read-only, unmodified
core/     untouched
```
