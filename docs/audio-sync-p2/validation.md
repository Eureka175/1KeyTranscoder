# Audio Sync P2 — Validation

> Long-duration validation of any P2 correction on real material, and the
> production-entry verdict. Source: `work/p2/agent-j-validation.md`, with
> data in `work/p2/out/agent_j/`.
>
> **Bottom line: the production gate is not met — 4 PASS, 4 FAIL, 2 PENDING — and
> the failures are exactly the conditions under which a correction would be safe to
> ship.**

---

## 1. The decisive measurement

A correction plan was built from 120 s windows and applied. Measuring the corrected
output at the **same** geometry is close to tautological (34 measurements, 34 free
shifts). So the same output files were re-measured at **30 s windows — a grid the
plan never saw** — with the uncorrected signal measured on that same grid as a fair
baseline.

| file | pair | before 120 s | before 30 s | **after 120 s** | **after 30 s** | after/before (30 s) |
|---|---|---|---|---|---|---|
| C1305 | ch3-ch4 | 26.250 | 26.417 | **0.000** | **42.917** | **1.62×** |
| C1305 | ch1-ch4 | 17.333 | 20.042 | 32.812 | 33.042 | 1.65× |
| C1305 | ch2-ch4 | 13.021 | 19.167 | 24.938 | 26.646 | 1.39× |
| C1305 | ch1-ch3 | 15.438 | 17.729 | **0.000** | **25.021** | 1.41× |
| C1305 | ch2-ch3 | 14.875 | 17.875 | 9.688 | 28.604 | 1.60× |
| C1306 | ch3-ch4 | 2.708 | 18.833 | **0.000** | **18.833** | **1.00× (no gain)** |
| C1306 | ch1-ch4 | 3.854 | 12.583 | 5.188 | 14.417 | 1.15× |
| C1306 | ch2-ch4 | 3.833 | 12.729 | 5.188 | 14.562 | 1.14× |
| C1306 | ch1-ch3 | 5.458 | 19.188 | 0.000 | 17.562 | 0.92× |
| C1306 | ch2-ch3 | 5.500 | 19.708 | 0.062 | 18.729 | 0.95× |

**At the geometry that produced the plan the residual is exactly zero. At a 4×
finer geometry the identical output carries 0.92×–1.65× the original residual span
and up to 1.65× the original peak residual.**

For C1306's `ch3-ch4`, the "2.708 ms of movement" the correction set out to remove
was almost entirely an artefact of the 120 s smoothing: at 30 s the real movement is
**18.833 ms before and 18.833 ms after**. The corrective work removed nothing there
and added boundary steps.

This is the brief's category **(b) — residual reduced because the estimator is
chasing measurement artefacts** — and it is reported as a **failure, not a
success**. It also makes geometry-independence of verification mandatory: the same
output measures 0.000 ms on one grid and 42.9 ms on the next.

## 1.1 Precise attribution of the failure (round 9)

Two completed agents appeared to disagree about C1306 `ch3-ch4` — Agent H classified
it `p2_piecewise_integer` (correctable), Agent J measured that correcting it removed
nothing. Investigating that:

**They measured identical data.** Both: median **2.8542 ms**, span **2.7083 ms**,
same 120 s / 120 s geometry, `search_ms = 250`, 34 points. So the disagreement is
about the **decision**, not the measurement.

**But the underlying series is decisive.** J's per-window delays for C1306
`ch3-ch4`:

```
4.292 4.438 4.479 4.396 4.396 4.625 4.417 4.396 4.792 4.792 4.792 5.333
3.750 2.854 2.833 ... (26 windows flat at 2.833-2.854) ... 2.625 2.645 2.625
```

and the plan derived from it has **18 boundaries** with shifts
`[206, 213, 215, 211, 222, 212, 211, 230, 256, 180, 137, 136, 137, 136, 126, 127,
126, 127]` — a total span of **130 samples (2.708 ms)**.

**So the plan models 2.708 ms of movement, while J's own 30 s reference measures
18.833 ms of movement on the same material.** The residual that "reappears" at 30 s
is movement the plan **was never able to see**, because it was built at 120 s
resolution.

**Corrected conclusion.** The negative result is attributable to the **plan's
resolution**, not to piecewise-integer correction as a mechanism:

> "A per-window integer-shift plan built on a 120 s grid leaves the sub-grid
> movement uncorrected, so the residual is unchanged or worse on a finer grid."

That is **not** the same as "per-window integer shifting cannot work on this
material", which is **not established**. What *is* established is that no validated
criterion exists for deciding when a pair's delay is well defined enough to plan
from — and this case demonstrates the practical cost of that gap.

An earlier draft of this chapter attributed the failure to an unreliable tracker.
That was wrong: **J's plan came from J's own corrector**, and its 2.85 ms median
matches the validated measurement. Recorded because the mis-attribution is
instructive — the tracker unreliability below is a real but *separate* defect.

**Separate defect, still real:** Agent I's independent tracker disagrees with the
validated reference on **15 of 40 windows (max 1143 samples)** and is therefore not
a trustworthy plan source; and Agent I's decision files return the *same* reason
(`p2_needs_resampling_not_supported`) for a rigid pair and a moving pair. Both are
reported in `correction.md` §3.

### 1.2 The resolution question — ATTEMPTED, NOT ANSWERED

§1.1's claim is falsifiable, and testing it decides whether the §19 gate failure is a
**resolution** artefact or **fundamental** to integer shifting on this material. It
was attempted three times across rounds 11–12 and **remains open**.

Design (sound — keep it): plan on a 30 s grid, validate on a 10 s grid the plan never
saw, baseline the uncorrected signal on that same grid, and treat
`after/before < 0.5` as "resolution was the problem".

**The blocker, stated precisely:**

> A finer plan needs a **narrow measurement window**, and **no instrument in this
> project is validated at narrow search widths.** `fullrate_delay.py` validates
> ±250 ms by known-answer injection; at ±8 ms the same instrument reports +3.9 ms
> for an injected +12.5 ms and −0.3 ms for an injected +50 ms.

Two bounded routes forward:

1. validate a measurement instrument at the narrow widths a finer plan implies; or
2. **plan from a finer *window* geometry while still measuring at ±250 ms** —
   shorten the analysis window rather than narrow the search. This sidesteps the
   blocker entirely and is probably the cheaper route.

**Consequence for the verdict: the §19 gate failure stays unclassified.** It is not
established whether it is a resolution artefact or fundamental. `FINAL-REPORT.md`
§15 carries this as the top open item.

Full record, including the design and every finding: 
`work/p2/scripts/FINER_PLAN_EXPERIMENT.md`. Findings the attempts did produce, all
verified:

* the correct `shift_stream` form — `out[n] = in[n + shift]`, positive = advance,
  trailing zeros — matching `core/sync_estimate.py`'s `delay > 0 = target arrives
  later`;
* **injection semantics must be stated explicitly.** `shift_stream(x, -d)` is the
  *delayed* target (`y[n] = x[n − d]`); injecting `shift_stream(x, +d)` advances it,
  so asserting a positive measurement is self-contradictory and yields the exact
  negative of the truth (+137 → −137). Two drafts fell into this and were rejected
  by their own checks;
* `arrivals.top_peaks` requires the target array to be **exactly `len(ref) + 2s`**,
  with `lag0 = s` — passing a slice of a longer buffer makes the correlation surface
  contain a large non-overlapping region and the tracker returns the wrong peak;
* a self-test that hard-codes its own search width while `main()` reads one from the
  command line silently validates a *different* configuration. Fixing that is what
  exposed the blocker.

Two harnesses were deleted rather than repaired: the first had been kept with a
"BROKEN" docstring for traceability, and the second was corrupted while being
rewritten through shell string manipulation, collapsing it to one line. A corrupted
artefact is worse than none, and both removals are recorded in
`FINER_PLAN_EXPERIMENT.md`.

## 2. Categorising every outcome

| category | finding |
|---|---|
| **(a) a real, correctable defect was fixed** | **no instance found on this material.** No pair's residual is reduced on a grid the plan never saw. The only place a genuinely correctable defect exists is a *static* offset, which P1's existing single integer shift already handles exactly — and this corpus's `CH1↔CH2` does not even need it (0.104 ms / 0.021 ms). |
| **(b) artefact chasing** | `CH1↔CH2`, and the entire naive result at its own geometry: **40 correction boundaries and 27.6 % of the timeline rewritten on a pair with zero movement episodes**, plus a 0.000 ms residual that becomes 42.9 ms on the next grid resolution. |
| **(c) correctly left alone** | **everything P1 leaves `untouched`.** On both files P1 refuses all three targets and returns `measure_failed`. Given that the movement is real, level-independent and event-driven, and that the four tracks are never summed, **this refusal is the correct production outcome.** |

## 3. Artifacts and safety checks

* Integer-exactness known-answer tests pass (T2 exact outside the unrecoverable
  band); bit-exactness on identity input verified (T3/T4).
* All four corrected outputs have **exact** sample counts and durations; no PTS
  modification.
* Determinism: no RNG anywhere; every path uses `RawStream` seek+read, largest
  allocation one 120 s window plus its FFT — **memory O(window) confirmed**.
* **Boundary artifacts: up to +14.2 dB jumps** at correction boundaries
  (Agent J §8). That is consistent with Agent E's independent measurement that a
  1-sample boundary flip is the loudest artefact in the study (**+13.7 dB above
  signal RMS**), and it is a second, independent reason the naive corrector fails.

## 4. Level-bias sensitivity

The round-1 hazard was that part of any "improvement" could be the estimator chasing
its measured level-dependent bias (delay correlates −0.82…−0.94 with programme
level). Agent J fitted `delay ~ a + b·level` on the same windows used for the delay
comparison. The conclusion reinforces §1: the apparent improvement does not survive
an independent geometry, so it is not necessary to attribute it to the level term —
it is an artefact of the plan's own smoothing grid.

**The rule this leaves behind: any claimed residual reduction must be demonstrated
on a measurement geometry the plan never saw, and must be shown to exceed the
level-dependent bias floor.**

## 5. Production-entry verdict

The ten conditions are **not written down anywhere in the repository** — they come
from the task statement. Agent J reconstructed them from `BRIEF.md` and the round-2
brief, marking the four that round-1 `synthesis.md` names verbatim.

| # | condition | verdict | evidence |
|---|---|---|---|
| 1 | real corpus proves a time-varying inter-channel delay exists | **PASS** | all six pairs reproduced digit-for-digit on both files at two independent geometries; spans 0.021–26.42 ms against a 0.021 ms floor |
| 2 | a calibrated estimator (per-pair uncertainty, coverage-in-time, relative ambiguity) | **PENDING** | the *evidence base* satisfies this (round 1 reproduced exactly); the *P2 estimator artifact* was not testable this round |
| 3 | model selection has evidence — a class established, not merely lowest BIC | **FAIL** | 90–99 % of windows are ambiguous on every moving pair; 8–114 discrete events at 30 s resolution; no model class established |
| 4 | the correction prototype passes its own whole-timeline verification | **FAIL** | verified on a grid it never saw, the residual is **0.92×–1.65×** the original, with up to +14.2 dB boundary jumps |
| 5 | long-duration residual significantly reduced on real material | **FAIL** | reduced to exactly 0.000 ms only at the plan's own geometry; at 30 s, 1.62× / 1.41× / 1.00× |
| 6 | verification covers the whole timeline and is independent of the plan's geometry | **FAIL** | the prototype has no verification; P1's own `recheck_residual` covers 0.83 % of an hour, and the round-1 near-miss (0.646 ms deviating entirely outside that window) is reproduced |
| 7 | **no P1 regression** | **PASS** | 0 field-level differences across 4 rows × 18 keys versus the round-1 probe; injected +1200/−600 fixed exactly; aligned → `already_aligned`; transparent `SHA256(in) == SHA256(out)`; silent → `untouched/silent_track`; low-confidence → `untouched/low_confidence` |
| 8 | fallback honoured — insufficient evidence → `untouched`, never a plausible-looking correction | **PASS for P1, FAIL for the prototype** | P1 refuses all three targets on both files and returns `measure_failed`; the naive prototype has no gate and rewrote 95.6 % of the timeline |
| 9 | determinism, memory O(window), sample count / duration / PTS preserved | **PASS** | no RNG; T3/T4 bit-exactness; `RawStream` throughout; exact sample counts and durations; no PTS change |
| 10 | integration constraints (opt-in, `algo_version` unchanged, `decision` vocabulary unchanged, `p2_*` not in `_ESTIMATION_FAIL_REASONS`, `offset_samples` `None`, transparent excludes P2, `--check basic` passes) | **PENDING** | design-only; no P2 code exists to test. The two structural facts the constraints rest on were verified: `preservation/validate.py:291-301` really does compare `stts` sums with exact equality |

**Verdict: 4 PASS, 4 FAIL, 2 PENDING. The gate is not met**, and the failures are
not engineering polish — conditions 3–6 are exactly the conditions that make a
correction safe to ship, and the long-duration evidence says the per-window
integer-shift approach **cannot meet them on this material**.

## 6. P1 regression baseline

Established independently and recorded in `p1-baseline.md`: on both corpus files P1
returns `measure_failed` with detail
`all measurable targets failed: CH1 non_constant; CH2 non_constant; CH4 non_constant`,
anchored on CH3, `algo_version 2.3.0-p1`. **It leaves all audio untouched**, which is
the correct outcome — reached for a weaker reason than P2's (P1's gates cannot
express shape), but reached.

Positive controls confirm the baseline is not vacuous: with injected constant delays
of +1200 and −600 samples, and with a two-sided offset, P1 **does** correct — and in
transparent mode on already-aligned input it produces a **byte-identical** output
(`SHA256(in) == SHA256(out)`).

## 7. What this means for the objective

The production path must **not** be modified. That is not a deferral; it is the
finding:

* the movement in this material is real but its interpretation is unsettled
  (`investigation.md` §6.2);
* no model class is established;
* the only correction instrument the evidence supports is integer shifting, and
  applying it on a per-window basis **makes things worse** on a finer grid;
* and the shipped P1 already produces the correct outcome.

The supported deliverable is the **diagnosis**: a whole-timeline, per-pair report
carrying a calibrated uncertainty, an explicit statement of where the delay is not
well defined, and **no automatic correction**.
