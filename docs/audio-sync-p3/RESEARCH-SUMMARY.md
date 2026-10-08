# Audio Sync P3 — RESEARCH-SUMMARY

**Purpose: what a maintainer gets from P3 in five minutes, without reading 74 KB.**
Nothing here is new evidence. Every claim is a pointer to a report that already
contains it. Where this file and a phase report disagree, **the phase report is
right and this file is wrong.**

The complete per-phase record is `FINAL-RESEARCH-SUMMARY.md` (P1–P3.8). The
P3.9–P3.12 record is in `p3.10-provenance.md`, `p3.11-provenance.md`,
`p3.12-provenance.md` and the phase reports under `work/p3/`.

---

## What was proven

| Claim | Status | Evidence |
|---|---|---|
| Sub-sample delay can be measured to **≤ 0.00003 samples** against a fixture whose answer is known by construction | `VALIDATED` | `work/p3/out/synthetic_kat.json`; `measurement.md` |
| The delay is **real, time-varying and far above the noise floor** — 839–1294× one sample | `VALIDATED` | P2 → P3, `long-duration.md` |
| `measure → plan → apply → independent re-measurement` runs on **real PCM** | `VALIDATED` | P3.10 `production-test-report.md` §10; P3.11 `FINAL-REPORT.md` §15 |
| It generalises to **all six real channel pairs**, not just `ch3→ch4` | `VALIDATED` | P3.11 §4/§5: 962/962 and 832/832 verify points, 6/6 pairs |
| The apply reproduces the plan **bit-exactly**, to a fraction of a sample | `VALIDATED` | P3.10 §6 excess: **480/481** and **415/416** points below 0.5 sample |
| The residual falls **substantially** where the plan measured | **improvement observed** | P3.10: median 296.48 → **4.233** (C1305), 136.39 → **3.000** (C1306). P3.11 `both_valid`: all six medians improve 56×–324× |
| Independent verification, by a **separate estimator in a separate process reading only PCM** | `VALIDATED` | P3.11 §6.5: 5 of 6 pairs within ±3 samples; one recorded `DISAGREEMENT` |
| Uncertainty is **explicit and representable** — `TRUSTED` / `UNRESOLVED_GAP` / `REENTRY` | `VALIDATED` | `gap_semantics.py`; `PRODUCTION-REQUIREMENTS.md` U1 |
| The delay inside a gap is **unobservable, not merely imprecise** | `VALIDATED` | `OPEN-DECISIONS.md` D5: `σ_meas` 0 → 2.0 barely moves the answer |
| A short measurement window **does** measure delay — exactly, 30 s down to 1 s | `PLAUSIBLE` | P3.12 `probe_window_length.py` |
| Every synthetic fixture construction is correct, checked against an independent implementation | `VALIDATED` | P3.12 `verify_delay_path.py` 25/25; generator selftest 37/37 |

## What was disproven

| Claim | Verdict | Evidence |
|---|---|---|
| A per-window **validity criterion** can be calibrated on synthetic material alone | **REJECTED** | P3.1/P3.2: it refused every negative control and still accepted only 19.5–51.7 % of real windows |
| The second-peak ratio is a usable ambiguity signal | **REJECTED** | P2 → P3.1: looked perfect on the corpus, falsified by a control |
| The P3.4 "gap policy is the dominant residual source" **supporting inference** | **REJECTED** (the finding stands) | P3.4/−; `OPEN-DECISIONS.md` "Decisions that are NOT open" |
| A **duration threshold** on gaps predicts jump size | **REJECTED** | `PRODUCTION-REQUIREMENTS.md` §5: C1306 has 175–180 s gaps and never exceeds a 79-sample jump; C1305 has a 530 s gap and reaches 670 |
| The `policy_jump` / `artificial_jump` metric can **rank gap policies** | **REJECTED** — policy-invariant, 0 of 8 136 policy-pairs differ | `PRODUCTION-REQUIREMENTS.md` §5 |
| A **fractional / resampling** correction is needed | **REJECTED** — the noise floor is far below one sample | P3.6; P3.11 §16 item 8 |
| Adaptive coarse-to-fine refinement **saves estimator calls** | **REJECTED** — calls rose 2–10× | P3.12 `FINAL-REPORT.md` §17 |
| `current` — the shipped gap policy — can be trusted to report what it measured | **REJECTED** — it reports 0 unresolved where the mask has 388 | P3.11 §15 |

## What remains unknown

1. **Residual acceptance threshold.** No phase invented one. `NOT ESTABLISHED` on both corpora. Not a research question.
2. **Coordinate contract.** `latest-start` is what the engine does; whether that is *right* is undecided (D2). The 15 s attribution offset is an `OBSERVED SEMANTIC MISMATCH` (D3), bounded by one hop at `win = 3·hop` **and only there**.
3. **Gap policy.** Five arms characterized on real PCM; none selected (D1). On the low-coverage pairs `no_correction_gap` is nearly a no-op, so the choice decides whether most of the stream is corrected at all.
4. **One instrument disagreement.** C1306 CH3→CH2: +70 vs +8.5 samples, `fine_fit_r2 = −0.21`. Recorded, not resolved. Needs a third method or a controlled fixture.
5. **The tail.** Where the plan did not measure, residual p95 is **177–1121 samples** against **9–30** where it did. The median hides this completely.
6. **End-to-end cost** (D4). Measurement dominates at 4.3–4.7 s/window. Decode + measure + correct + encode has never been measured.
7. **Downstream representability** of `UNRESOLVED_GAP` (D6). Nobody has looked at the consumers.
8. **Whether the one worsened pair is material or coverage.** A per-pair coverage sweep would separate them.
9. **A calibrated per-window σ.** `UNCERTAINTY CALIBRATION = PARTIAL`; the existing figure is a physical bound, not an error bar.

## What is production-safe

```text
P1 fixed-offset correction            already shipped, unchanged by P3
P3 measurement layer                  validated on known truth
P3 state semantics                    TRUSTED / UNRESOLVED_GAP / REENTRY representable
P3 real PCM apply                     contract-exact
P3 independent verification           separate instrument, separate process
```

## What is explicitly deferred

```text
gap policy                            DEFERRED      (no ranking offered; the metric cannot see a difference)
coordinate policy                     DEFERRED
residual acceptance threshold         NOT ESTABLISHED
default enablement                    NOT ENABLED   (no flag exists; unreachable from 1kt.py)
adaptive coarse-to-fine               NO ADVANTAGE DEMONSTRATED
fractional / resampling correction    CLOSED, NOT JUSTIFIED
```

## The three results that are easy to get wrong

**1. "The residual improved" is true of the median and false of the tail.** On
C1305 the p95 went 621.54 → 734.24 and the max 1064.14 → 1796.06. That is not a
defect — it is the correction carrying one shift across windows it could not
measure. Reporting only the median is choosing the flattering statistic.

**2. Two of P3.10's "worsened" tail figures were refused measurements, not
correction errors.** At C1305 CH3→CH4 t=20 s the `after` residual is −1796
samples while the **excess is 0.078 samples** — the estimator flagged that window
`intra_window_unstable` and the applier did exactly what the plan said. On the
population where both sides are accepted, **five of six pairs improve in every
statistic**; the sixth's max worsens **+4.5 %**, not +69 %. See
`work/p3/p311/reports/residual-acceptance-addendum.md`.

**3. P3.12 is not a failed idea.** It achieved `MISSED_TRANSITION = 0` on known
ground truth. It is that the *current tested formulation provides no demonstrated
efficiency advantage*. The cause is measured, not assumed: on C1305 across 16
consecutive 10 s probes the delay runs 495–727 samples in 10–50 sample steps, so
it changes faster than the 60 s coarse interval and no root is ever accepted.
At 10 s resolution the exhaustive grid has no redundancy to remove.

## Where the honesty cost something

P3.12's safety metric — the one thing that was supposed to make adaptive
refinement trustworthy — **could never fire as first written**. It asked whether
the true delay varies inside a reference block, and a perfect step makes every
block internally constant. It reported zero misses on a fixture with a 320-sample
step. A safety metric that cannot fail certifies the thing it was meant to test.
This is the same defect family (silent pass) that recurred in P3.8, P3.9, P3.10,
P3.11 and P3.12. Treat any check added to this codebase as suspect until it has
been **demonstrated discriminating**.
