# P4 — Stage 4: gap policy decision preparation

**Status: semantics specified and validated in research; policy `DEFERRED`.**
This document prepares P3 `OPEN-DECISIONS.md` D1 and D5. **It selects nothing.**

---

## 0. Sources

| item | source |
|---|---|
| state machine, candidates, safety invariants, chunk-invariance finding | `docs/audio-sync-p3/safe-gap-design.md` (P3.7) — `63/63` semantics, 132-case known-truth matrix, state machine **`VALIDATED`** (Q0–Q5 at 12 chunk sizes) |
| the five arms measured on real PCM | `work/p3/p311/reports/coordinate-comparison.md` §"stage D" |
| residual split by supplying state | `work/p3/p311/reports/residual-by-state.md` |
| why no candidate is ranked | `OPEN-DECISIONS.md` D1; `HANDOFF-CHECKLIST.md` §2.1 |

---

## 1. The central constraint

```text
the in-gap delay is UNOBSERVABLE, not merely imprecise
```

`σ_meas` from 0 to 2.0 moves the answer almost not at all. There is **no
estimator, no window length and no policy** that recovers it. P3.12 reinforced
this independently: an excursion **shorter than the measurement window** is
averaged away at any refinement depth, because refinement changes *where* windows
are placed, not *how long* they are.

Therefore a gap policy is **not** an estimation strategy. It is a decision about
what to do with a value nobody measured, and about what to say while doing it.

```text
The system is allowed to not know.
It is not allowed to pretend.
```

---

## 2. The three states

### 2.1 Definitions

| state | meaning |
|---|---|
| `TRUSTED` | the window's own measurement was accepted |
| `UNRESOLVED_GAP` | the window was refused; the shift is a **policy decision, not a measurement** |
| `REENTRY` | the first accepted window after a gap; where the gap decision meets reality |

The stream state machine:

```text
TRUSTED ──measurement refused──▶ UNRESOLVED_GAP ──trusted measurement returns──▶ REENTRY ──▶ TRUSTED
```

### 2.2 What each state permits and forbids

| state | allowed | forbidden | output semantics |
|---|---|---|---|
| `TRUSTED` | apply the window's own `rint(delay)` | inventing a neighbouring value | resolved measurement |
| `UNRESOLVED_GAP` | hold / fill 0 / interpolate-under-stated-assumption | **reporting `resolved = True`** | **explicit unresolved** |
| `REENTRY` | resume applying measurements | retroactively amending the gap | resolved measurement, boundary flagged |

### 2.3 What each state actually means in the production object

```text
TRUSTED          shift_samples is backed by an ACCEPTED measurement of THIS window
UNRESOLVED_GAP   shift_samples is NOT backed by any measurement of this window;
                 it came from a declared policy. The value may be perfectly
                 reasonable and the state still says "nobody measured this".
REENTRY          shift_samples is backed by an accepted measurement; the flag
                 exists because the PRECEDING window was not, so a consumer can
                 find the boundary without reconstructing the mask.
```

`REENTRY` is **not** a third kind of value. It is `TRUSTED` with a boundary
marker, and it matters because it is where a gap decision stops and measurement
resumes.

### 2.4 State is a function of the resolved mask — a hard requirement

P3.7's most concrete engineering finding:

> With chunk size 1 the **shift** sequence is exactly chunk-invariant while a
> per-chunk **state** is not: a one-window chunk cannot know a gap preceded it,
> so it cannot label itself `REENTRY`. That is a property of *state*, not a
> policy defect. **State must therefore be computed as a pure function of the
> resolved mask over the whole stream, after the shifts are decided.** Doing so
> makes chunk invariance automatic and removes an entire bug class.

So, as a contract:

```text
state[i] = f(resolved_mask, i)

and explicitly NOT:
    state[i] = f(shifts[i-1], previous_chunk, global mutable state)
```

The brief's prohibition — `state = f(resolved_mask)`, never
`state = f(chunk-local history)` — is this finding, and it is adopted as a
production invariant, not a suggestion.

---

## 3. The candidates, and exactly what each does inside a gap

### 3.1 Definitions

| policy | shift inside a gap | leading gap | trailing gap | online? | context needed |
|---|---|---|---|---|---|
| `hold` | carry the last trusted shift | → `0` | carries | yes | the carry value |
| `no_correction_gap` | apply shift `0` | `0` | `0` | yes | **none** |
| `explicit_state` | **identical shift to `hold`**; unresolved state is first-class output | as `hold` | as `hold` | yes | the carry value |
| `linear` | interpolate between the bounding trusted shifts | → next | → previous | **no — offline only** | gap extent + both endpoint values |
| `next` | use the next trusted shift | uses next | → `0` | **no — offline only** | next value |
| `current` | shipped `INVALID_EDGE`; forward carry, leading gap takes the first trusted shift | first value | carries | yes | carry or first value |

`current` is listed **because it must be audited, not because it is a
candidate.**

### 3.2 The precise form of the `current` finding — do not overstate it

The 132-case known-truth matrix separates three checks that must not be merged:

| check | `current` |
|---|---|
| state sequence matches the mask-derived expectation | **132/132 correct** |
| never labels a refused window `TRUSTED` | **132/132 correct** |
| `resolved` flags backed by a measurement | **1/22** (only the no-gap case) |

```text
state sequence correct in every case    : True
no fake TRUSTED window in any case      : True
`resolved` flags correct in every case  : False
```

**`current`'s state machine is correct.** What fails is the separate `resolved`
claim.

> **`current` claims more knowledge than the evidence supports** — a semantic
> over-claim, not an algorithmic error, and not a claim that `current` is unsafe
> in every respect.

On the real corpora this is the whole gap-policy difference between `current` and
`hold`: P3.7 measured the two policies as differing in applied shift on
**0.4–0.5 %** of windows — **the leading gap only** — while `.git`-level
comparison of the produced PCM shows `4 of 5 arms` distinct, with `hold` and
`explicit_state` sharing a hash.

### 3.3 The three options the brief asks about, stated exactly

#### Hold

```text
gap:
    shift remains the previous trusted shift    (0 if there is none to carry)
    state = UNRESOLVED_GAP
    at the first trusted window after the gap, state = REENTRY
```

*Invents no new value* — it repeats a value that was measured. That value is
stale by construction, and **staleness is not measurable from inside the gap.**

#### No correction

```text
gap:
    correction disabled — applied shift = 0
    state = UNRESOLVED_GAP
```

The value `0` is **itself an assertion**: that the correct shift is none. It is
the only candidate that asserts *nothing about the material* (it neither carries
nor interpolates), and simultaneously the one with the lowest correction
coverage.

#### Interpolation

```text
gap:
    numerical interpolation between the bounding trusted shifts
    state REMAINS UNRESOLVED_GAP
```

```text
interpolation != measurement
an interpolated value MUST NOT be labelled TRUSTED
```

This is the sharpest of the three prohibitions. `linear` changes the shift at
every interior window, and every one of those changes is **synthesised**. The
value can be perfectly smooth and entirely invented:

> Model complexity cannot manufacture ground truth that the recording does not
> contain.

`linear` and `next` are declared **`OFFLINE_ONLY`** in the engine, because both
need a right endpoint that a streaming implementation has not reached yet.

---

## 4. Why the candidate fails is decided by something unobservable

The candidates separate **only by which kind of situation they fail in**, and the
discriminator is where a discontinuity sits inside the gap:

| property | hold | next | no_correction_gap | explicit_state | linear | current |
|---|---|---|---|---|---|---|
| no internal oscillation (I1) | yes | yes | yes | yes | yes | yes |
| explicit uncertainty (I2) | yes | yes | yes | yes | yes | **no** |
| no direction reversal (I3) | yes | yes | yes | yes | yes | yes |
| deterministic (I5) | yes | yes | yes | yes | yes | yes |
| chunk invariant **with no context** (I6) | no | no | **yes** | no | no | no |
| online-capable | yes | **no** | yes | yes | **no** | yes |
| correct on a quiet gap | yes | yes | **no** | yes | yes | yes |
| correct when the discontinuity is at the gap's closing edge | yes | — | no | yes | **no** | yes |
| correct when the discontinuity is mid-gap | no | — | no | no | **yes** | no |
| invents no in-gap structure | no | no | **yes** | no | no | yes |

```text
NO COLUMN DOMINATES.
Each is the better answer in a different row, and the rows that separate them
are decided by the position of a discontinuity inside the gap — which is not
observable.
```

Specifically, from P3.6:

```text
A hidden discontinuity costs the full step under every carry policy;
`linear` halves it WHEN the discontinuity is mid-gap and DOUBLES the error
when it sits at the closing edge.
```

So the sign of the `linear`-vs-carry comparison depends on an unobservable
position. That is why interpolation cannot be shown to be better, and also
cannot be shown to be worse.

---

## 5. Measured behaviour on the real corpora

One shared resolved mask, one shared coordinate contract (`latest-start`). Only
the in-gap rule varies. All figures from
`work/p3/p311/reports/coordinate-comparison.md` §"stage D".

### 5.1 The five arms on C1305 ch3→ch4 and C1306 ch3→ch4

| Corpus | policy | after median | after p95 | after max | unresolved |
|---|---|---:|---:|---:|---:|
| C1305 ch3→ch4 | `current` | 4.233 | 732.66 | 1796.06 | **0** |
| C1305 ch3→ch4 | `hold` | 4.233 | 734.24 | 1796.06 | 234 |
| C1305 ch3→ch4 | `no_correction_gap` | **35.555** | 602.00 | 1796.06 | 234 |
| C1305 ch3→ch4 | `explicit_state` | 4.233 | 734.24 | 1796.06 | 234 |
| C1305 ch3→ch4 | `linear` | 4.400 | 712.06 | 1796.06 | 234 |
| C1306 ch3→ch4 | `current` | 3.000 | 105.88 | 759.98 | **0** |
| C1306 ch3→ch4 | `hold` | 3.000 | 105.88 | 757.02 | 109 |
| C1306 ch3→ch4 | `no_correction_gap` | 4.114 | 227.73 | 549.61 | 109 |
| C1306 ch3→ch4 | `explicit_state` | 3.000 | 105.88 | 757.02 | 109 |
| C1306 ch3→ch4 | `linear` | 3.000 | 107.13 | 763.02 | 109 |

### 5.2 `no_correction_gap` is very close to a no-op

| Corpus | Pair | before median | `no_correction_gap` after median | `hold` after median |
|---|---|---:|---:|---:|
| C1305 | ch3→ch1 | 1136.599 | **1130.000** | 12.034 |
| C1305 | ch3→ch2 | 1143.852 | **1135.029** | 6.574 |
| C1305 | ch3→ch4 | 296.476 | **35.555** | 4.233 |
| C1306 | ch3→ch1 | 1191.827 | **1126.344** | 14.586 |
| C1306 | ch3→ch2 | 1236.400 | **1137.667** | 8.464 |
| C1306 | ch3→ch4 | 136.387 | **4.114** | 3.000 |

On the four low-coverage pairs, `no_correction_gap` leaves the median almost
exactly where it started. **The choice therefore decides whether most of the
stream is corrected at all** — it is not a refinement of the correction, it is
close to a decision not to make one except on measured windows.

This is why `no_correction_gap` shows the most "IMPROVED" cells in the direction
table: it is comparing a nearly-unchanged number with a changed one. **A
direction table read without this row is a trap.**

### 5.3 `hold` and `explicit_state` are the same audio

```text
distinct corrected-PCM sha256: 4 of 5 arms on all six pairs
arms sharing a sha256:         explicit_state + hold
```

So the "make the state first-class" option costs **zero audio change** relative
to `hold`. That is the entire content of D5's "companion, not a rival":

* under a **physical** reading (alignment is the goal), `hold` and `current` are
  near-equivalent and the state machine is bookkeeping;
* under a **claimed-guarantee** reading, the state machine **is** the product,
  `current`'s over-claim is a real defect, and `explicit_state` stops being a
  companion and becomes a requirement.

P4 does not choose between these readings. It builds the object both can use.

### 5.4 Residual by the state that supplied the shift

`work/p3/p311/reports/residual-by-state.md`. `T` = the window's own measurement;
`G` = an UNRESOLVED_GAP carry; `R` = first trusted window after a gap.

The load-bearing row, C1305 ch3→ch1:

| state | n | median | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| T | 57 | **4.804** | 13.13 | 13.83 | 20.17 |
| G | 388 | 20.000 | 764.95 | **1120.73** | 1533.36 |
| R | 36 | 7.221 | 113.84 | 142.49 | 340.78 |

> A point whose shift came from a `G` window was NOT measured at that point, so a
> residual there measures the policy's carry rule, not the estimator's accuracy.
> **Mixing the two populations into one median is what makes a partially
> corrected stream look fully corrected.**

**Consequence P4 implements:** the state must travel with the value, because the
value alone cannot be interpreted.

---

## 6. Context must be explicit — and its absence must be an error

P3.7 measured the exact streaming requirement. Per stream, and per gap:

```text
the stream's accepted mask              (or: is a trusted window ahead / behind?)
the current gap's two endpoint VALUES   (2 integers)
the current gap's global extent         (2 integers)
```

Every quantity is **bounded**, so streaming is possible — **but only if the
context is explicitly supplied. A chunk cannot infer it.**

### 6.1 The six defects that established this

Each was a real property of the policies, exposed by chunk invariance:

1. a gap **straddling a chunk boundary** is *trailing* to the left chunk and
   *leading* to the right, so both wrote `0` where the whole stream carries the
   previous value;
2. classifying "is the neighbour trusted?" by **index arithmetic** is wrong when
   that neighbour is in another chunk — classification must test **actual
   trust**;
3. a chunk beginning inside a gap reading `shift[a-1]` at local index 0 hits
   numpy's `shift[-1]`, which **silently resolves to the last window of the
   chunk** — a plausible number from the wrong place;
4. a chunk containing **no** accepted window is not the same as a stream
   containing none;
5. a chunk ending **inside** a gap cannot see that gap's right endpoint;
   `right_value` read the next chunk-local window instead — measured:
   **127 wrong windows on C1305 at 2.5 s**;
6. `linear`'s weight is computed from a window's position **within the gap**, so
   a local-index formula cannot reproduce it.

### 6.2 The production rule

```text
if the policy needs a left endpoint, a right endpoint or a gap extent,
it MUST be passed explicitly.

if that context is MISSING:
    ERROR -- not a guess, not a default, not zero
```

P3.8 recorded the same defect class from the other direction, in the shipped
engine's ancestor: a truncated mask was indistinguishable from a refusal —
`[0]` where the truth was `[400]`, *a plausible number from a window the chunk
cannot see*. Fixed by adding `GapContext.stream_end` and making
`stream_has_trust` **demanded rather than defaulted**.

And the endpoint defect before it: the previous behaviour invented plausible
values — `[100, 100, 100, 100]` where the truth was `[167, 233, 300, 367]`,
**silently wrong with no error**.

### 6.3 `no_correction_gap` needs none of it

It is chunk-invariant **with no context at all**, because it writes `0` in every
gap regardless of position and has no edge case. This is the one clean
architectural advantage any candidate holds, and it is recorded as a property,
not as a reason to choose it.

---

## 7. Uncertainty is not error

The brief requires this distinction to be explicit. P4 adopts it as a **type-level
separation**, not a convention:

```text
UNRESOLVED_GAP     a VALID epistemic state.
                   The plan is correct. The execution succeeded.
                   The system is stating what it does not know.

ERROR              an INVALID execution.
                   Missing context, invalid mask, non-finite value,
                   out-of-range shift, missing endpoint, truncated record set.
                   The plan must not be produced at all.
```

Consequences P4 implements:

| situation | classification | required behaviour |
|---|---|---|
| a window the estimator refused | `UNRESOLVED_GAP` | produce a decision, mark it unresolved, continue |
| a gap in a leading or trailing position | `UNRESOLVED_GAP` | as above, with the declared policy's value |
| a policy needs an endpoint that was not supplied | **`ERROR`** | **raise**; never guess |
| a mask shorter than the stream | **`ERROR`** | **raise**; never treat truncation as refusal |
| a non-finite or out-of-range shift | **`ERROR`** | **raise**; never cast silently |
| a chunk that cannot see its context | **`ERROR`** | **raise**; never infer |

```text
a policy that never emits UNRESOLVED_GAP is claiming precision it does not have
an implementation that treats UNRESOLVED_GAP as a failure is discarding a valid plan
an implementation that returns a number where context was missing is the defect
    this entire research line exists to eliminate
```

---

## 8. Verdict

```text
D1  which safe-gap policy ships          OPEN -- not selected by P4
D5  what a correction is allowed to claim OPEN -- shapes the API, not decided
D6  whether UNRESOLVED_GAP is representable downstream
                                          OPEN -- depends on consumers OUTSIDE
                                          this repository. Nobody has looked at them

gap policy            = DEFERRED
gap SEMANTICS         = SPECIFIED and VALIDATED (research)
production default    = OFF
```

**`DECISION_REQUIRED: D1`, `D5`, `D6`.**

The question P4 cannot answer, in P3's own words:

> whether uncorrected alignment inside a gap is worse than invented structure.
> That depends on what the audio is for, and a concert master and a
> sync-reference track do not want the same answer.

### What P4 does instead

P4 implements the **mechanism**, parameterised by policy, with:

```text
correction value and epistemic state as SEPARATE fields
state derived as a pure function of the resolved mask
explicit, demanded context -- missing context raises
UNRESOLVED_GAP distinct from ERROR at the type level
every candidate selectable without a code change
the whole path OFF by default
```

so that whichever of D1/D5/D6 is answered, the answer is a **declared parameter**
rather than a rewrite — and so that **no unapproved policy runs in production**,
including by accident.
