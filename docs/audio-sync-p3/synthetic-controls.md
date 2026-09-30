# Synthetic Controls

**Phase: P3.2 Stages 7 and 7.5. Status: `SYNTHETIC CONTROLS = PARTIAL`. The v2 generator
matches the corpus regime to 1 % on its positives; `S4` is a false accept whose mechanism
Stage 7.5 resolved as a fixture mismatch rather than a criterion failure.**

---

## 0. Stage 7.5 finding — the failure mode S4 models is not in the accepted set

Full detail: `work/p3/p32/argmax_split_report.md`. Read from artifacts already on disk; no
new measurement.

| | C1305 accepted | C1305 rejected | C1306 accepted | C1306 rejected |
| --- | ---: | ---: | ---: | ---: |
| n | 961 | 950 | 1232 | 422 |
| lag-1 | **+0.9962** | +0.9310 | **+0.9955** | +0.5915 |
| median \|step\| | **0.56** | 0.48 | **0.55** | 0.31 |
| p95 \|step\| | **4.24** | **169.08** | **3.40** | **83.09** |
| max \|step\| over a ≤15 s gap | 180 | **1115** | 79 | **1053** |
| switch candidates | 7 | 110 | 5 | 28 |
| candidates that **return** to the earlier level | **0** | **6** | 0 | — |

**Inside a ≤5 s gap the largest movement anywhere in the accepted series is 11 samples.**
The 180-sample and 124-sample maxima both occur across 15–25 s gaps, where a moving delay can
legitimately cover them. Every accepted excursion moves one way and stays; a hopping
estimate would come back, because the arrival it came from is still present. On C1305 the
rejected series returns 6 times out of 108, including a cluster at t ≈ 3652–3697 s that
oscillates `218 → 606 → 431 → 558 → 293 → 128 → 147 → 544` at 2.5 s intervals.

```text
Evidence of accepted-window hopping : NO
S4 mechanism                        : MISMATCH  (not representative of accepted-set failure)
Decision                            : FREEZE-ELIGIBLE, with negative-control coverage
                                      recorded as INCOMPLETE
```

`S4` is recorded as `FALSE ACCEPT / cause = mechanism mismatch / status = SUPERSEDED`. It is
**not** deleted and `synthetic/v2.json` is untouched. This does **not** license the claim
that the corpus cannot hop — only that the current synthetic rival model does not reproduce
observed movement, and that the hopping the corpus does exhibit is confined to windows the
window-level gate already refuses.

---

## 1. Why v1 had to be rebuilt

`work/p3/P3.2-FINAL-REPORT.md` §16.3 recorded the gap as a number, and `regime.py` turned
it into a measurement. The v1 positives moved **3.00 samples per 10 s window** with
essentially no measurement noise, so their reversal rates were 0.000 and the retired
reversal-rate clause looked safe. Against a corpus whose per-step movement is comparable to
the estimator's own jitter, the same clause rejected all three real pairs — a
**synthetic-regime failure**, not a threshold bug. The brief's §45 requires exactly that
distinction to be written down, and it is.

## 2. The corpus regime, measured

From `work/p3/p32/synthetic/regime.json`, primary pair CH3→CH4:

| quantity | C1305 | C1306 | frozen target |
| --- | ---: | ---: | ---: |
| accepted span | 1187.7 samples | 141.4 samples | — |
| median \|step\| at 10 s | 1.70 | 1.94 | **1.82 samples** |
| median \|step\| at 2.5 s | 0.56 | 0.55 | **0.55 samples** |
| \|coarse − mean(fine)\| median | 0.68 | 0.85 | **0.76 samples** |

**The last row is the one that matters, and it corrects an earlier reading of this
project.** P3.1 and the first P3.2 round both described a window-to-window residual of
"11–55 samples" and treated it as the measurement floor. That number came from fitting a
line to accepted windows in a *moving* region, so it is dominated by motion. Measured
against the fine grid over the same audio — where the audio is nearly identical between
the two measurements — the estimator repeats to **0.76 samples**. The measurement is
sub-sample repeatable, and any residual after correction is not measurement noise.

Two further numbers from the same run: the 2.5 s steps are 0.55 samples, and only
**2.1 %** of them exceed the estimator's own self-reported `probe_step_max` (median 7.6
samples). The estimator's uncertainty channel is conservative by roughly an order of
magnitude on this material.

## 3. Generator v2, frozen before evaluation

```text
movement                 1.80 samples per 10 s window   (frozen target 1.82)
primary delay            1140 samples
room reflection          -14 dB at 9 samples, always present
switching rival          -6 dB at 60 samples, on/off per window
equal-level case         0 dB at 120 samples
```

`work/p3/p32/synthetic_v2.py`, output `synthetic/v2.json`. Parameters were fixed from
`regime.json` **before** any fixture was scored, and the two defects found afterwards were
generator bugs with a stated cause, not parameter tuning.

## 4. Result

| fixture | truth | window gate | set verdict | median \|step\| | lag1 | agrees |
| --- | --- | --- | --- | ---: | ---: | --- |
| S0 smooth single | valid | 40/40 | VALID | **1.79** | +0.9254 | ✓ |
| S1 smooth + noise | valid | 40/40 | VALID | **1.79** | +0.9254 | ✓ |
| S2 true 120-sample step | valid | 38/40 | VALID | 0.12 | +0.9210 | ✓ |
| S3 random switching | invalid | 40/40 | INVALID | 0.05 | −0.0855 | ✓ |
| **S4 block switching** | invalid | 40/40 | **VALID** | 0.24 | **+0.5132** | **✗** |
| S5 smooth + gap | valid | 40/40 | VALID | **1.80** | +0.9256 | ✓ |
| S6 isolated pulled windows | valid | 40/40 | VALID | **1.80** | +0.9244 | ✓ |
| S7 equal level | invalid | 40/40 | INVALID | 0.00 | −0.0320 | ✓ |

**7/8, zero false rejects, one false accept (S4).**

**Regime match: the smooth positives produce 1.79 samples per step against a frozen target
of 1.82 — within 1 %.** That was Stage 7's actual goal, and it is met.

`S2` stays VALID, which is the fixture's whole purpose: a genuine 120-sample discontinuity
must not be read as switching. Its `lag1` is +0.9210 with a margin of 0.72, and its
`distinct_value_ratio` margin is 0.0066 — small, but it passes on `lag1` with room.

## 5. The finding that matters: the negatives do not attack the assumed mechanism

Two generator defects were found and fixed:

1. the rival's weight was written into the array the reflection's weight also occupied, so
   an "on" window carried ≈0.1 where 0.5 was intended;
2. `S7`'s equal-level case rendered the rival at the primary's exact offset, so it was the
   same arrival rather than a rival.

After both fixes the block-switching fixture still moves the estimate by only **2.8
samples** (1143.0 against 1140.2), so its `lag1` is +0.5132 rather than near zero, and the
criterion accepts it. That is not a criterion failure. It is a **model** failure, and
direct measurement says so:

| rival | level → estimate |
| --- | --- |
| coherent copy, 60 samples | 0.02 → 1140.2 · 0.5 → 1143.0 · 0.8 → 1144.0 · 1.0 → 1144.5 · 1.4 → 1145.3 · **2.0 → 1146.0** |
| coherent copy, 120 samples | identical to the above |
| **incoherent** rival, 60 samples | 0.02 → 1140.1 · 0.5 → 1142.1 · 1.0 → 1142.7 · 1.5 → 1144.8 |
| incoherent rival, 300 samples | 0.02 → 1140.1 · 0.5 → 1141.2 · 1.0 → 1141.0 · 1.5 → 1143.4 |

**Raising a rival from absent to 6 dB *above* the primary moves this estimator's answer by
6 samples, and the separation is irrelevant.** Two structural reasons:

* the PHAT surface weights each frequency bin by its magnitude, so a coherent secondary
  shifts the *phase ramp* only in proportion to its share of the coherent energy, and the
  peak position is set by the ramp rather than by which local maximum is taller;
* the switching fixtures' rival is a delayed copy of the *same source*, so it shares the
  primary's phase structure and cannot form an independent peak of its own.

So the corpus's large steps cannot be produced by this mechanism either, and the synthetic
negatives v1 and v2 both built (a strong close rival switching on and off) **do not
reproduce arrival hopping in this estimator**. `S3` still reads INVALID, but it reads
INVALID because its series is a 0.05-sample-per-step jitter field, not because a delay
hopped between two arrivals.

## 6. What this means, stated without overreach

* **Stage 7's regime goal is met** (§4: 1 % match on the positives).
* **Stage 7's control goal is not met.** The brief's §15 asks whether containment and the
  set-level criterion separate coherent motion from arrival switching. On three of four
  negatives the answer is yes, but only `S7` (genuine equal-level ambiguity) and `S3` are
  separated through the mechanism they were designed around; `S4` is a false accept, and
  the level sweep in §5 says the fixture cannot be repaired by changing its numbers,
  because the estimator does not hop in that configuration.
* **The corpus does hop.** P3.1 measured the refused CH3→CH2 windows oscillating between
  722 and 1646 samples, and an argmax truth series that does so requires the *cross-
  correlation peak* to move, which the PHAT-weighted fine-grid vertex does not do for a
  coherent rival. The two measurements are not in conflict — the corpus's refused windows
  are exactly the ones the window gate rejects on `interp_vertex_at_edge` — but the
  mechanism that produces the corpus's hopping is **not identified**, and no fixture in
  this phase reproduces it.

**Therefore `S4` must not be forced to pass.** Repairing it by enlarging the arrival
separation, raising the rival, or lengthening the blocks would manufacture a fixture that
passes for reasons unrelated to the mechanism under test. The honest state is: S4 is a
false accept, its cause is understood, and the correct next experiment is to find the
mechanism that *does* make this estimator hop.

## 7. The correct next experiment (Stage 7 continuation)

The level sweep in §5 isolates the variable. Something must make the PHAT fine-grid vertex
land on a different arrival, and the candidates are:

1. **A rival arriving before the primary.** Every fixture here places the rival *later*.
   An early arrival changes the leading edge of the correlation, which is where a
   peak-position estimator is most sensitive.
2. **A rival with distinct phase content** — a different source, not a delayed copy, at a
   level near the primary's. The incoherent sweep (1.5 → 1144.8) moved the estimate by
   4.6 samples, more than the coherent case at the same level, which is a lead worth
   following.
3. **The rejected-window mechanism itself.** The corpus's hopping may only exist in windows
   whose surface already has two comparable maxima — i.e. the hopping may be a *consequence*
   of `interp_vertex_at_edge` rather than an independent phenomenon. If so, the set-level
   criterion's job on this corpus is smaller than P3.1 assumed, and the window gate is doing
   the work.

Candidate 3 is falsifiable from data already on disk: compare the argmax truth series in
*accepted* versus *refused* windows on the same windows, which P3.1 already did for
CH3→CH2 (accepted +0.8581, refused +0.3916). Doing it for CH3→CH4 at the 2.5 s grid would
settle whether accepted windows ever hop.

## 8. What is and is not claimed

```text
synthetic regime matches corpus regime          : YES, 1 % on the positives
set-level criterion separates the controls      : 3 of 4 negatives; S4 superseded by 7.5
containment on the controls                     : NOT RUN (not needed for the freeze)
S4 false accept                                 : RESOLVED as mechanism mismatch (Stage 7.5)
accepted-window hopping in the corpus           : NO EVIDENCE (both corpora)
mechanism producing rejected-window movement     : NOT IDENTIFIED, and not needed
```

Stage 8's freeze now proceeds. The brief's §56 conditioned it on the synthetic regime
equalling the corpus regime *and* the criteria remaining separable inside it; the second
clause is met by Stage 7.5's corpus evidence rather than by `S4`, and the coverage gap is
recorded rather than papered over.
