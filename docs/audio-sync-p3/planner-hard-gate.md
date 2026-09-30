# Planner Hard Gate

**Phase: P3.2 Stages 5–6. Status: `PLANNER = VALIDATED`.**

---

## 1. The boundary, enforced

```text
estimator          ->  validated measurements
set-level criterion ->  is the accepted set one trajectory
planner            ->  faithful representation of that trajectory
```

`work/p3/p32/planner.py` converts a trajectory into a piecewise-constant integer shift
schedule and does nothing else. It does not decide whether a window's delay was ambiguous,
which arrival is real, or whether the estimator was right. Putting a second, hidden validity
model inside the correction path is precisely what P3.2 exists to avoid, and the brief's §11
names it.

## 2. Why the gate is hard rather than logged

P3.1 found three plan defects by *reading a number in a log*:

* a **1192-sample** leading-gap step from a pair whose measured delay moves 1.4 ms;
* a **sign inversion** that doubled the real residual on CH3→CH4 (span ratio 1.8488), found
  only by a purpose-built fixture after P3 had already reported "no improvement";
* a span comparison that was printed but never enforced.

Every one was visible in `shift_span_samples` and invisible in the audio. A gate that prints
and continues is a gate that a later caller ignores, so:

> `build_validated_plan` raises `PlanRejected`; `main` exits **3**; nothing downstream may
> apply the plan.

## 3. What is checked

| check | what it catches |
| --- | --- |
| every segment has `start_sample`, `end_sample`, `shift_samples`, `valid` | malformed plan |
| `shift_samples` is an `int` | a plan that is not piecewise-constant-integer |
| segments ordered, non-overlapping, non-empty | a plan that does not cover the timeline |
| segment gaps reported (not failed) | unplanned audio; the final segment legitimately ends early |
| **span**: carrying segments vs input trajectory | the 1192-sample artifact, and any lost movement |
| every carrying segment equals its own window's rounded delay | a plan that is not a representation |
| **sign**: `shift × delay > 0` | P3's inversion, which doubled a real residual |
| monotonicity | **deliberately NOT checked** — a time-varying delay reverses |

## 4. Where the tolerance comes from

Not chosen. Each carrying segment's shift is `round(delay)`, so it is within **0.5 samples**
of its own window's delay. The span of a set of values each within 0.5 of its input differs
from the input span by at most **1.0 sample** — 0.5 at each end.

```text
SPAN_TOLERANCE_SAMPLES = 1.0
```

Stated in samples rather than milliseconds because integer rounding is a sample-domain
operation.

## 5. Known-trajectory tests, estimator absent (P0–P5)

`python work/p3/p32/planner.py selftest`

| test | trajectory | expected span | plan span | result |
| --- | --- | ---: | ---: | --- |
| P0 | constant 1140 | 0.0 | 0.0 | accepted |
| P1 | monotonic ramp 0→120 | 120.0 | 120.0 | accepted |
| P2 | single step 100→220 | 120.0 | 120.0 | accepted |
| P3 | multi-step 0 / 50 / 10 | 50.0 | 50.0 | accepted |
| P4 | discontinuous 0→300→0 | 300.0 | 300.0 | accepted |
| P5 | invalid gap of 3 windows | 110.0 | 110.0 | accepted |

P2–P4 matter because they are the cases a naive planner would mangle: a step, a reversal,
and a genuine discontinuity. A planner that enforced monotone shifts would reject P3 and P4.

## 6. Malformed plans, all rejected (P6)

| injected defect | rejection |
| --- | --- |
| span wrong by 360 samples | `span mismatch: the trajectory spans 70.0 samples and the plan's carrying segments span 430.0 ... against a tolerance of 1.0` |
| sign inverted | `8 carrying segments do not equal their own window's rounded delay` |
| segments overlapping | `segment 2 starts at 480000 before segment 1 ends at 960000: overlap` |
| non-integer shift | `segment 1 shift is float, not an integer: the plan is piecewise-CONSTANT INTEGER` |
| empty plan | `plan has no segments` |

**All 11 tests pass** (P0–P5 accept, P6a–P6e reject).

## 7. Real data

C1305 CH3→CH4 through the gate:

```json
{ "ok": true, "failures": [],
  "input_trajectory_span_samples": 1187.701949713476,
  "output_plan_span_samples": 1188.0,
  "span_difference_samples": 0.2980502865239032,
  "span_tolerance_samples": 1.0,
  "n_carrying_segments": 216,
  "carrying_segments_not_equal_to_their_window": 0,
  "carrying_segments_with_usable_sign": 216,
  "monotonicity_required": false }
```

`work/p3/p32/planner/gate_20260919_C1305_ch3_to_ch4.json`.

## 8. Planner-only transfer (Stage 6)

P0–P5 run inside `build_validated_plan` with no estimator anywhere in the call. P3.1's
full-chain fixture measured 96.7 % recovery at a realistic span but could not separate
planner error from estimator error; this separates them, and the planner's transfer is
**exact to rounding** — 0.298 samples of difference on a 1187.7-sample trajectory, against a
1.0-sample tolerance derived from rounding alone.

## 9. Limitations

1. **The gate validates representation, not correctness.** A trajectory that is wrong will
   produce a plan that faithfully represents it. That is the intended boundary, and it means
   the gate is only as good as the set-level criterion upstream of it.
2. **`carrying_segments_not_equal_to_their_window` tolerates 0.5 samples.** A plan could in
   principle shift every segment by 0.4 samples and pass. The span check would catch a
   systematic drift; a uniform offset of <0.5 samples would not, and does not matter.
3. **The gate does not check the audio.** Sample count, duration and artifact checks belong
   to the correction stage, not here.
