# P4 — Stage 5: residual acceptance framework

**Verdict: `RESIDUAL ACCEPTANCE THRESHOLD = NOT ESTABLISHED`.**

This document does **not** invent a threshold. `P4-HANDOFF.md` §5 and
`HANDOFF-CHECKLIST.md` §4 prohibit it, and D3 records why:

> what residual counts as good enough, on which statistic, on which corpus? —
> **product**. `NOT ESTABLISHED` on both corpora. **Inventing one is prohibited,
> and raising one so a named corpus passes is the single worst defect available
> here.**

What this document does instead is build the **structure** a threshold would sit
inside, so that when the product owner names one, it is a single declared number
plugged into an existing gate — and so that **the gate is useful before any
number exists**.

---

## 1. Why a single scalar is the wrong instrument here

### 1.1 The residual population is not one population

P3.11 `residual-by-state.md`. `T` = the window's own measurement supplied the
shift; `G` = an `UNRESOLVED_GAP` carry; `R` = first trusted window after a gap.

| state | n (C1305 ch3→ch1) | median | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| T | 57 | **4.804** | 13.13 | 13.83 | 20.17 |
| G | 388 | 20.000 | 764.95 | **1120.73** | 1533.36 |
| R | 36 | 7.221 | 113.84 | 142.49 | 340.78 |

> A point whose shift came from a `G` window was NOT measured at that point, so a
> residual there **measures the policy's carry rule, not the estimator's
> accuracy**. Mixing the two populations into one median is what makes a
> partially corrected stream look fully corrected.

A single threshold applied to a mixed population would be measuring two unrelated
things at once. **Any accepted threshold must name its population.**

### 1.2 The unfiltered tail contains measurements the estimator refused

P3.11 measured that two of P3.10's "worsened" tail figures were **refused
measurements, not correction errors**. The decisive case, C1305 ch3→ch4 at
`t = 20 s`:

| quantity | value |
|---|---:|
| `before` | −1064.14 samples — `valid = False`, `reason = intra_window_unstable` |
| applied shift from the plan | **+732** |
| `expected = before − applied` | −1796.14 |
| `after` | −1796.06 — also `valid = False` |
| **`excess = after − expected`** | **+0.078 samples** |

**A residual gate that reads the raw grid will fail a correct correction.** The
gate must read `excess`, or a filtered population, or both.

### 1.3 "Measure harder" is not available

The in-gap delay is **unobservable**, not merely imprecise: `σ_meas` from 0 to 2.0
barely moves the answer, and no estimator, window length or policy recovers it.
P3.12 reinforced this — a sub-window excursion is averaged away at any refinement
depth.

So no threshold can be met inside a gap by improving measurement. A threshold can
only constrain **what is claimed** and **how much of the stream is verified**.

---

## 2. The measured error budget, from P3.10 / P3.11 / CAL-1..CAL-4

Every component P4 could find, with its source. **This is the whole budget; no
term below is estimated.**

| # | component | measured value | source |
|---|---|---|---|
| E1 | **estimator variance across runs** | measurement **bit-identical** to the frozen P3 run, every compared field over 478 windows | P3.10 §1 |
| E2 | **estimator accuracy vs analytic truth** | `max|real − model| = 0.0000` samples | P3.8, quoted `HANDOFF-CHECKLIST` §3.2 |
| E3 | **planner integer rounding** | each carrying segment's shift is within **0.5 samples** of its window's delay; a set of values each within 0.5 differs in span by at most **1.0 sample** (`SPAN_TOLERANCE_SAMPLES = 1.0`) | `planner-hard-gate.md` §4 |
| E4 | **apply mechanics** | `excess` max over `both_valid` across all six pairs: **0.31–1.96 samples**; `points < 0.5 sample` = **480/481** (C1305) and **415/416** (C1306) | P3.11 addendum, `residual-audit.md` |
| E5 | **apply mechanics under the alternative coordinate** | `excess` max becomes **732 samples** — but that is the coordinate lag, not an audio error (see §2.1) | `coordinate-decision.md` §7.2 |
| E6 | **independent verifier variance** | fresh estimator, different frame grid, reads only PCM: **5 of 6 pairs within ±3 samples**; the sixth at **+70 samples / 1.458 ms** with `fine_fit_r2 = −0.21` | P3.11 §6.5 |
| E7 | **verifier-vs-verifier disagreement** | the C1306 ch3→ch2 case above — **recorded, not averaged, not resolved** | P3.11 §6.5 |
| E8 | **the noise floor** | median `excess` **0.0017–0.0217 samples** — far below one sample | P3.11 §16 item 8 |
| E9 | **uncertainty model quality** | `uncertainty_samples = max(0.3, 0.5·peak_width/√peak_over_noise)` is a **physical bound, not a calibrated σ**; the fitted residual sd (11.07 samples) exceeds the probe scatter median (1.95) by **5.7×** → `UNCERTAINTY CALIBRATION = PARTIAL` | `PRODUCTION-REQUIREMENTS.md` §5 |

### 2.1 The one number that looks like a defect and is not

Under `nearest-centre`, `excess = after − (before − applied)` reaches **732
samples** where `latest-start` gives **0.615**.

```text
excess is computed against the plan's per-window shift S[b].
Under nearest-centre the sample at block b receives S[b-1].
So excess = S[b-1] - S[b], whose maximum over the plan is exactly
max|S[w-1] - S[w]| = 732 samples.
```

**This is not an audio defect. It means the plan's per-window shift indexing is
part of the apply contract, not metadata.** Any P4 verifier computes its
expectation from the shift **actually applied at that sample**, under the declared
join — otherwise it reports a 732-sample "excess" that no audio exhibits.

### 2.2 What the budget does and does not support

```text
SUPPORTED     apply mechanics reproduce the plan to within 2 samples, always
SUPPORTED     the estimator is reproducible to the bit and exact against a model
SUPPORTED     the noise floor is far below one sample
SUPPORTED     a second, independent instrument agrees within 3 samples on 5 of 6
NOT SUPPORTED any statement that the corrected output is "good enough" for any purpose
NOT SUPPORTED a calibrated per-window uncertainty
UNRESOLVED    one instrument disagreement (E7)
UNRESOLVED    whether the single worsened pair is material or a coverage artefact
```

---

## 3. The gate P4 builds — structural, not a number

The brief asks whether acceptance can be formed from:

```text
structural invariants + independent verification + coverage rules + no-silent-error rules
```

**Yes — and P4 implements exactly that.** Each condition below is executable and
already covered by `tests/audio_sync_p4/test_production_sync.py`.

### 3.1 Hard invariants — a violation is a FAILURE, never a low score

| id | invariant | how it is checked | P4 status |
|---|---|---|---|
| **A1** | apply mechanics exact against the plan | `excess ≤ 2 samples` on every point inside coverage; plus byte-level reproduction of the P3.11 corrected PCM | **ENFORCED** — 15/15 streaming arms reproduced byte-for-byte |
| **A2** | state is honest | `overclaimed_windows() == 0` for every policy except the historical `current`, whose over-claim is counted and reported | **ENFORCED** |
| **A3** | no unmeasured window is `TRUSTED` | state counts must equal an independently derived expectation from the mask | **ENFORCED** — 120 000-window run, transitions counted independently |
| **A4** | independent measurement available | a second estimator, different frame grid, reading only PCM, must produce a number | **RETAINED** — `core.sync_estimate` is tracked production code |
| **A5** | no invalid context silently used | every required context is demanded; absence raises `AudioSyncContextError` | **ENFORCED** — 49 raise-cases, 0 silent-wrong |
| **A6** | chunk invariance | byte-identical output at every chunk size, including boundaries inside a segment | **ENFORCED** — 8 chunk sizes, every online policy |
| **A7** | uncertainty is not error | `UNRESOLVED_GAP` never raises; a missing context always does | **ENFORCED** — type-level separation |
| **A8** | default path unchanged | `core/channel_sync.py`, `core/sync_fix.py`, `core/sync_estimate.py` and `1kt.py` must not reference the new module | **ENFORCED** |

### 3.2 Coverage rules — report, never average away

```text
C1  measurement coverage must be reported per pair, with its population named
    (measurement windows vs plan windows — see policy-matrix.md §0.1)
C2  the state counts T/U/R must be reported per pair
C3  the accepted-population count must be reported alongside every residual claim
C4  a residual statistic quoted without its population is not a valid report
```

Measured coverage, measurement population (`FINAL-REPORT.md` §5):

| Pair | valid of measured | coverage |
|---|---:|---:|
| C1305 ch3→ch1 | 93 / 478 | **19.5 %** |
| C1305 ch3→ch2 | 102 / 478 | 21.3 % |
| C1305 ch3→ch4 | 247 / 478 | 51.7 % |
| C1306 ch3→ch1 | 81 / 414 | 19.6 % |
| C1306 ch3→ch2 | 81 / 414 | 19.6 % |
| C1306 ch3→ch4 | 308 / 414 | **74.4 %** |

And the honest limitation, in P3.11's own words: **on the low-coverage pairs the
correction can be verified on about a fifth of the stream.**

```text
P4 does NOT set a minimum coverage.
It reports coverage, and requires the report to name the population.
Whether 19.5 % is acceptable is D1/D5, not an engineering call.
```

### 3.3 No-silent-error rules — carried forward as permanent requirements

From the brief's §36 and the P3 defect record. Each is implemented as a gate that
**can fail**:

```text
N1  experiment_count == 0        -> NOT PASS
N2  apply_count == 0             -> NOT PASS
N3  verify_count == 0            -> NOT PASS
N4  a check that cannot fail     -> reported as a finding, not a pass
N5  "it did not raise"           -> not a finding; assert the VALUE
N6  an empty run                 -> exit 2
```

`test_production_sync.py` implements N1–N6 and reports
`VALUE_OK / RAISED / CONTROL_OK / CONTROL_FAILED / SILENT_WRONG` separately.
**Every fixture family in the suite carries a CONTROL that demonstrates the check
can fail.**

---

## 4. If a threshold is later authorised

The gate is designed so a threshold is a **declared constant plus a named
population**, not a new mechanism:

```text
RESIDUAL_THRESHOLD = <number>        # NOT SET. Product decision.
RESIDUAL_POPULATION = "both_valid"   # must be named with it
RESIDUAL_STATISTIC  = "p95"          # must be named with it
```

Constraints P4 records for whoever sets it:

1. **It may not be set on the unfiltered population** — that population contains
   refused measurements (§1.2) and would fail a correct correction.
2. **It may not be set on a mixed T/G population** — §1.1.
3. **`no_correction_gap` must not be scored by it naively.** That arm is nearly a
   no-op on low-coverage pairs: on C1305 ch3→ch1 its after-median is
   **1130.000** against a before-median of **1136.599**, and a naive
   "residual went down" reading of it compares a nearly-unchanged number with a
   changed one.
4. **It must be stated with its corpus.** P3.11's totals differ per pair by more
   than an order of magnitude; a single number across both corpora would be
   fitted to whichever corpus was named.
5. **Raising it so a named corpus passes is prohibited**, and is named in
   `P4-HANDOFF.md` §5 as *the single worst defect available here*.

---

## 5. What is missing, and what would establish it

The brief requires that an insufficiency be explained, not merely asserted.

### 5.1 Why the threshold is not established

| gap | why it blocks a threshold |
|---|---|
| **no reference for "good enough"** | the corrected output's correctness is a product property (what the audio is *for*), and `OPEN-DECISIONS.md` D5 records that it has no technical answer |
| **no calibrated σ** | E9: the uncertainty model is a physical bound, not a calibration. A threshold expressed in units the estimator cannot calibrate is a guess |
| **the accepted population is small where it matters** | 74–107 of 481/416 points on four of six pairs (§3.2) |
| **one instrument disagreement is unresolved** | E7 |
| **the in-gap population cannot be scored at all** | the value there is unobservable, so a residual inside a gap measures the policy, not the correction |

### 5.2 The experiments that would establish it

Stated as concrete, bounded work — **not performed in P4**, and not P4's to
authorise, because each changes the measurement configuration:

| # | experiment | what it would settle |
|---|---|---|
| **R1** | a **controlled fixture** with a known time-varying delay at the production geometry, pushed through the real apply, measured by both instruments | replaces E7's disagreement with a truth; would separate "verifier is wrong" from "material is ambiguous" |
| **R2** | an **accepted-population coverage sweep** — vary the validity criterion and record coverage vs residual per pair | separates "the one worsened pair is material" from "it is a coverage artefact" (P3.11 §16 names this as the way to separate them) |
| **R3** | a **calibrated per-window σ** — a known-truth repeat at several bandwidths | replaces E9's bound with a calibration |
| **R4** | a **minimum-coverage requirement**, chosen by the product owner | converts coverage from a report into a gate |
| **R5** | a **product statement of purpose** for the corrected output | makes "good enough" well-defined at all |

**R1–R3 are engineering work that P4 did not do because P3.12's lesson applies:
do not re-open a settled question, and do not change the measurement configuration
inside an integration.** R4 and R5 are product decisions.

### 5.3 What P4 did instead

```text
built the gate
enforced the eight structural invariants (A1-A8)
enforced the six no-silent-error rules (N1-N6)
made coverage and state counts unavoidable in the report
reproduced the P3.11 corrected PCM byte-for-byte as the mechanics proof
left RESIDUAL_THRESHOLD unset, and said why
```

---

## 6. Verdict

```text
Residual acceptance threshold     NOT ESTABLISHED
Structural invariants             ESTABLISHED AND ENFORCED  (A1-A8)
Independent verification          RETAINED, not a threshold (A4)
Coverage rules                    ESTABLISHED (report-only, C1-C4)
No-silent-error rules              ESTABLISHED AND ENFORCED  (N1-N6)
Apply-mechanics bound              ESTABLISHED: excess <= 1.96 samples (both_valid)
Calibrated per-window uncertainty  NOT ESTABLISHED
```

**The gate is usable today.** A run that satisfies A1–A8 has been proven to
reproduce its plan exactly, to have measured what it claims to have measured, to
be chunk-invariant, and to have failed loudly rather than quietly. A run that
does not is rejected.

**What the gate cannot do is tell you the output is good enough**, because that
is a product judgement and no experiment in this repository can make it.

```text
DECISION_REQUIRED: D3 (residual acceptance)
                  and D5 (what a correction is allowed to claim),
                  which determines whether a residual threshold is even the
                  right instrument.
```
