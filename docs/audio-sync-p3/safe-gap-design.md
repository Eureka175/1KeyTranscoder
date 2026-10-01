# Audio Sync P3.7 — Safe Gap Semantics Under Irresolvable Uncertainty

**Status: research-only design. No production change is authorised.**
`production = NOT READY`. `production integration = DEFERRED`.

**Validated:** semantics `63/63` · known-truth matrix 132 cases · state machine **`VALIDATED`**
(Q0–Q5 at 12 chunk sizes) · three chunk-dependency regressions · `current` reproduces the
frozen plans with 0 mismatch.

P3.7 does not try to recover the hidden trajectory. P3.6 established that it cannot be
recovered, and that no policy can change the magnitude of the step across a large gap — it
can only choose which edge that step lands on. P3.7 therefore asks the only question that
remains answerable:

> When the system knows it does not know the in-gap delay, how should it represent that,
> so that the unknown is not quietly converted into an invented trajectory?

---

## 0. The sharpest result, in two lines

```text
the applied correction can be IDENTICAL between two policies
the epistemic claim cannot
```

On the audio fixture, `hold` and `current` produce **byte-identical shift sequences** across a
gap containing a genuine 174-sample delay change — the previous value carried for four
windows, then one jump. They differ **only** in that `hold` labels those windows
`UNRESOLVED_GAP` (plus one `REENTRY`) while `current` labels every window `TRUSTED`. On the
real corpora the same two policies differ in applied shift on **0.4–0.5 %** of windows — the
leading gap only.

So **the labelling fix is separable from the numerical behaviour**, which is why it is the
one change the production proposal treats as prioritised.

---

## 1. What P3.6 established, and what it forbids

| P3.6 finding | consequence for design |
| --- | --- |
| The in-gap delay is unobservable | no production semantic may depend on knowing it |
| `policy_jump` / `artificial_jump` are policy-invariant at two-sided boundaries | boundary-jump magnitude is not a policy choice and cannot be optimised |
| A hidden discontinuity costs the full step under every carry policy; `linear` halves it **when the discontinuity is mid-gap** and **doubles the error when it sits at the closing edge** | the sign of the `linear`-vs-carry comparison depends on an unobservable position |
| Gap duration does not predict jump size | a duration threshold gates the wrong variable |
| `Σ_meas` is immaterial | more measurement precision does not help |
| P3.4's supporting inference is **rejected**: in-range estimates are nearer the *arrival* than the *carry* (46.0 % vs 36.4 % pooled) | "the carry is the culprit" is not established, and must not be treated as root cause |

**P3.4 is inherited in split form, and the split must not be re-merged:**

```text
gap policy = dominant residual source        STANDS   (direct, same-axis error evidence)
carried shift is itself the culprit          REJECTED (its own criterion does not license it)
```

---

## 2. Gap semantics

A correction decision is **not** a trajectory estimate. Every candidate therefore emits two
things per window:

```text
shift_samples   the integer correction applied
resolved        True  where the applied shift is backed by an accepted measurement
                False where the policy is filling a gap
```

`resolved=False` is the machine-readable form of explicit uncertainty. **It is not an error
state — it is the honest state.** A policy that never emits it is claiming precision it does
not have.

### 2.1 Window states

| state | meaning |
| --- | --- |
| `TRUSTED` | the window's own measurement was accepted |
| `UNRESOLVED_GAP` | the window was refused; the shift is a policy decision, not a measurement |
| `REENTRY` | the first accepted window after a gap; where the gap decision meets reality |

### 2.2 Stream states

```text
TRUSTED ──measurement refused──▶ UNRESOLVED_GAP ──trusted measurement returns──▶ REENTRY ──▶ TRUSTED
```

| state | allowed | forbidden | output semantics |
| --- | --- | --- | --- |
| `TRUSTED` | apply the window's own `rint(delay)` | inventing a neighbouring value | resolved measurement |
| `UNRESOLVED_GAP` | hold / fill 0 / interpolate-under-stated-assumption | reporting `resolved = True` | **explicit unresolved** |
| `REENTRY` | resume applying measurements | retroactively amending the gap | resolved measurement, boundary flagged |

**Validated end to end:** Q0–Q5 all PASS at 12 chunk sizes, comparing the exact state
sequence, the exact shift sequence and every boundary — not merely the final state.

### 2.3 State is a derivation, not a per-chunk decision

The phase's most concrete engineering finding. With chunk size 1 the **shift** sequence is
exactly chunk-invariant while a per-chunk **state** is not: a one-window chunk cannot know a
gap preceded it, so it cannot label itself `REENTRY`. That is a property of *state*, not a
policy defect. State must therefore be computed as a **pure function of the resolved mask**
over the whole stream, after the shifts are decided. Doing so makes chunk invariance
automatic and removes an entire bug class.

---

## 3. Candidates

| candidate | gap behaviour | border rule | online? | context needed |
| --- | --- | --- | --- | --- |
| `hold` | carry the previous trusted shift | leading gap → 0 | **yes** | carry value |
| `next` | use the next trusted shift | trailing gap → 0 | **no — offline only** | next value |
| `no_correction_gap` | apply shift 0 | both → 0 | **yes** | **none** |
| `explicit_uncertain` | identical shift to `hold`, but the unresolved state is first-class output | as `hold` | **yes** | carry value |
| `linear` | interpolate between the bounding trusted shifts | leading → next; trailing → previous | **no — offline only** | gap extent + endpoints |
| `current` | shipped `INVALID_EDGE`; forward carry, leading gap takes the first trusted shift | as described | yes | carry or first value |

`current` is included because it must be audited, not because it is a candidate.

No spline, Kalman filter, particle filter, ML model or high-order extrapolation is
admitted. Model complexity cannot manufacture ground truth that the recording does not
contain.

---

## 4. Safety invariants

| id | invariant | measured result on the four real plans |
| --- | --- | --- |
| **I1** | no unsupported internal oscillation (no new extremum inside a gap) | all six PASS |
| **I2** | a window without a trusted measurement must not be reported as resolved | **`current` FAILS on all four**; all candidates PASS |
| **I3** | no direction reversal inside a gap | all six PASS |
| **I5** | deterministic: same input → same output | all six PASS |
| **I6** | chunk invariance | all six PASS **once bounded context is supplied** |

### 4.0 The precise form of the `current` finding — do not overstate it

The known-truth matrix (132 cases) separates three checks that must not be merged:

| check | `current` |
| --- | --- |
| state sequence matches the mask-derived expectation | **132/132 correct** |
| never labels a refused window `TRUSTED` | **132/132 correct** |
| `resolved` flags backed by a measurement | **1/22** (only the no-gap case) |

```text
state sequence correct in every case    : True
no fake TRUSTED window in any case      : True
`resolved` flags correct in every case  : False
```

**`current`'s state machine is correct.** What fails is the separate `resolved` claim. The
accurate statement is:

> **`current` claims more knowledge than the evidence supports** — a semantic over-claim, not
> an algorithmic error, and not a claim that `current` is unsafe in every respect.

### 4.1 Two invariants were redefined after measurement, and that is part of the result

**I3** was first stated as *"every shift change must be attributable to a trusted
boundary"*. That is **violated by `linear` by construction**, since an interpolating policy
changes the shift at every interior window — and reporting it as a defect would be wrong,
because the change *is* supported by the two trusted endpoints. I3 was narrowed to *"no
direction reversal inside a gap"*, and the redefinition is recorded rather than hidden.

**I6** proved to be the most informative invariant, for a reason that is architectural
rather than numerical — see §5.

---

## 5. The chunk-invariance finding: an architectural constraint

I6 is defined as: deciding the stream in chunks must produce the same result as deciding it
whole. It **failed** for four of five candidates when each chunk was decided independently,
and the failures were diagnostic. Six successive bugs were exposed, each a real property of
the policies rather than of the test:

1. A gap **straddling a chunk boundary** is a *trailing* gap to the left chunk and a
   *leading* gap to the right chunk, so both wrote 0 where the whole stream carries the
   previous value.
2. Classifying "is the neighbour trusted?" by **index arithmetic** is wrong when that
   neighbour lies in another chunk. Classification must test **actual trust**.
3. A chunk beginning inside a gap reading `shift[a-1]` at local index 0 hits numpy's
   `shift[-1]`, which **silently resolves to the last window of the chunk** — a plausible
   number from the wrong place.
4. A chunk containing **no** accepted window is not the same as a stream containing none;
   the all-refused path zeroed genuine gap windows.
5. A chunk ending **inside** a gap cannot see that gap's right endpoint; `right_value` read
   the next chunk-local window instead (measured: 127 wrong windows on C1305 2.5 s).
6. `linear`'s weight is computed from a window's position **within the gap**, so a
   local-index formula cannot reproduce it.

**The resulting requirement, which is bounded:**

```text
an exact streaming implementation needs, per stream and per gap:

    the stream's accepted mask             (or: is a trusted window ahead / behind?)
    the current gap's two endpoint VALUES  (2 integers)
    the current gap's global extent        (2 integers)
```

Every quantity is **bounded**. There is no need to know the whole stream, so a streaming
implementation is possible — but only if the context is **explicitly supplied**. A chunk
cannot infer it.

**And `no_correction_gap` needs none of it.** It is chunk-invariant with no context at all,
because it writes 0 in every gap regardless of position, and it has no edge case.

---

## 6. Why no candidate is ranked

Compliance counts are deliberately not totalled. `hold` passes five invariants,
`no_correction_gap` passes five, and calling either "5/5" and the other "5/5" says nothing —
while calling one *better* would be a ranking the evidence does not support. The comparison
below is property-level.

| property | hold | next | no_correction_gap | explicit_uncertain | linear | current |
| --- | --- | --- | --- | --- | --- | --- |
| no internal oscillation (I1) | yes | yes | yes | yes | yes | yes |
| explicit uncertainty (I2) | yes | yes | yes | yes | yes | **no** |
| no direction reversal (I3) | yes | yes | yes | yes | yes | yes |
| deterministic (I5) | yes | yes | yes | yes | yes | yes |
| chunk invariant with no context (I6) | no | no | **yes** | no | no | no |
| online-capable | yes | **no** | yes | yes | **no** | yes |
| correct on a quiet gap | yes | yes | **no** | yes | yes | yes |
| correct when the discontinuity is at the gap's closing edge | yes | — | no | yes | **no** | yes |
| correct when the discontinuity is mid-gap | no | — | no | no | **yes** | no |
| invents no in-gap structure | no | no | **yes** | no | no | yes |

**No column dominates.** Each is the better answer in a different row, and the rows that
separate them are decided by the position of a discontinuity inside the gap — which is not
observable.

---

## 7. Coverage and the cost of conservatism

Safety and coverage are reported separately and never compressed into one number.

| candidate | unresolved windows (C1305 10 s / 2.5 s) | C1306 10 s / 2.5 s | windows left uncorrected rather than carried |
| --- | ---: | ---: | --- |
| `hold` / `explicit_uncertain` | 231 / 931 | 106 / 422 | 0 (carries) |
| `next` | 231 / 931 | 106 / 422 | 0 (carries) |
| `no_correction_gap` | 231 / 931 | 106 / 422 | **all gap windows** |
| `linear` | 231 / 931 | 106 / 422 | 0 (interpolates) |
| `current` | 0 (claims resolved) | 0 | 0 (carries) |

`no_correction_gap` has the lowest correction coverage of any candidate and the highest
safety compliance on the invariants that concern invented structure. **That trade is stated,
not netted off.** It is not a defect and it is not an advantage; it is what the policy is.

---

## 8. What is not established

- **No candidate is proven safe.** All verdicts are "satisfies the tested invariants on the
  tested cases". Nothing is a mathematical guarantee, and the word is not used.
- **The invariants are not complete.** They constrain *invented structure*; they say nothing
  about whether a policy is close to the truth, because the truth is unavailable.
- **The real-corpus replays carry no error measurement.** The in-gap delay is unknown, so
  `jumpMax` and the landmark shifts are observable quantities, not errors. In particular
  `jumpMax` is **identical for all six policies** on every case (670 / 763 / 79 / 79 samples)
  — the value is set by the accepted windows, exactly as P3.6 established.
- **The synthetic known-truth arm for P3.7 is not yet run.** The invariants were validated on
  hand-built reversals and on the real plans, but a P3.7-owned known-truth matrix over
  trajectory × gap profile × policy has not been produced.
- **The state machine is specified, not validated end to end.** §2.2 defines the states and
  transitions; a state-machine test suite (Q0–Q5, leading / middle / trailing gaps, restart
  invariance) is specified but not yet executed.

---

## 9. Design constraint, in one paragraph

Inside a gap the error is not observable, so any correction commits to a value the data
cannot check. The candidates are separable only by **which kind of situation they fail in**,
and the discriminator — where a discontinuity sits inside the gap — is exactly what the gap
conceals. No production semantic may therefore depend on knowing the hidden trajectory, and
no policy may report a gap fill as though it were a measurement. **The system is allowed to
not know. It is not allowed to pretend.**
