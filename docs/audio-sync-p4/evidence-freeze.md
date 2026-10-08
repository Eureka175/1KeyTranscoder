# P4 — Stage 1: P3 evidence freeze

**The P3 research line (P3.1–P3.12) is frozen. This document is P4's
authoritative index of what may be quoted from it, and nothing in P3 is
rewritten to create it.**

This file does **not** replace the P3 reports. It records, for each claim P4
relies on, the exact artifact and the exact figure, so that a P4 statement can
be traced to a P3 measurement rather than to a summary.

Sources are declared with their path. Where a P3 report was later **corrected**,
the correction is named and the corrected figure is the one P4 uses.

---

## 1. P1 baseline — the production behaviour that must not move

| item | value | source |
|---|---|---|
| status | `VALIDATED`, production-shipped | `docs/audio-sync-p3/PRODUCTION-HANDOFF.md` |
| corpus | 137 real A7M5 | as recorded there |
| result | 11/11 fixed-delay correction, residual `0.00 sample` | as recorded there |
| guard | **27 PASS / 0 FAIL** | `work/p2/scripts/p1_regression_guard.py`, quoted in `HANDOFF-CHECKLIST.md` §1 item 9 |
| production module | `core/channel_sync.py`, `core/sync_fix.py`, `core/sync_estimate.py` — `algo 2.3.0-p1` | `core/channel_sync.py` line 116 |

P1's correction is **a single scalar** applied to the whole track:
`shift_stream(src, dst, delay_samples=<scalar>, ...)` — `core/sync_fix.py` line 38.

`HANDOFF-CHECKLIST.md` §3.4.1 states why this matters to P4:

> `shift_stream` takes a scalar `delay_samples`. The whole trajectory is
> collapsed to **one number** before the call. **A per-window plan changes that
> signature**, which is why this is not a drop-in replacement.

---

## 2. P3.10 — first real PCM apply

| item | value |
|---|---|
| what ran | real measurement → plan → **ACTUAL PCM apply** → independent verification |
| configuration | window `30 s`, hop `10 s`, search `±3000` samples |
| not | synthetic; not frozen-plan replay; not plan-only |
| report | `work/p3/p310/FINAL-REPORT.md`; operational record `work/p3/p310/production-test-report.md` |
| provenance | `docs/audio-sync-p3/p3.10-provenance.md` |

P3.10's contribution to P4 is **the apply mechanics and the sign convention**:

```text
shift = +rint(delay);   out[n] = in[n + shift]
```

settled empirically in P3.10 (`work/p3/p310/sign_after_shift.py`), and reused
unchanged by P3.11's audit. Independent of P3.10, the shipped P1 path already
uses the identical convention — `core/sync_fix.py` line 47 docstring:
`out[n] = in[n + rint(delay)]`. **The two agree.** P4 does not re-derive it.

---

## 2b. P3.10 scope — what it did and did not cover

`work/p3/p310/FINAL-REPORT.md`, `production-test-report.md`.

| item | P3.10 |
|---|---|
| corpora | C1305, C1306 |
| pairs **measured** | all three targets: ch3→ch1, ch3→ch2, ch3→ch4 |
| pairs **planned, applied, verified** | **ch3→ch4 only** |
| status | `E2E MECHANISM VALIDATED` (both corpora) |
| self-assessed | `COMPLETE` for the primary question; `PARTIAL` for production readiness |

So P3.10's end-to-end claim is **one pair per corpus**. The six-pair claim is
P3.11's, and it is P3.11 that closes the gap.

Excess — the part the plan did not account for — measured in P3.10:

| corpus | points | median | p95 | max | points < 0.5 sample |
|---|---:|---:|---:|---:|---:|
| C1305 | 481 | 0.0027 | 0.095 | 0.615 | **480 / 481** |
| C1306 | 416 | 0.0017 | 0.057 | 3.236 | **415 / 416** |

### 2b.1 The tail `correct.py` leaves uncovered

P3.10 measured and recorded, and did **not** fix:

```text
C1305 declared coverage ends at 229 440 000 of 230 750 520
      -> 1 310 520 samples (27.302 s) uncovered
C1306 -> 24.660 s uncovered
```

`correct.py::shift_at` **clamps** past the last segment, so those seconds
silently receive the last shift. P3.10 states this rather than repairing it.
**P4 inherits it as a recorded defect, not as a P4 task** — changing
`work/p3/correct.py` would alter every historical plan.

---

## 3. P3.11 — six real pairs, end to end

### 3.1 The six pairs

```text
C1305:  ch3→ch1   ch3→ch2   ch3→ch4
C1306:  ch3→ch1   ch3→ch2   ch3→ch4
```

All six reached `E2E complete`: measurement, plan, actual PCM apply and
independent verification each carry non-zero counts.
Source: `work/p3/p311/reports/final-result-matrix.md` §"Required view";
per-pair status and hashes: `work/p3/status/status-20261008-1018.json`
(`status: COMPLETE`, `completed: 6, failed: 0, blocked: 0`).

### 3.2 Counts (the anti-silent-pass figures)

| Corpus | Pair | Measure | Plan | Apply | Verify |
|---|---|---:|---:|---|---:|
| C1305 | ch3→ch1 | 478/478 | 481 w | 923 002 080 B | 962/962 |
| C1305 | ch3→ch2 | 478/478 | 481 w | 923 002 080 B | 962/962 |
| C1305 | ch3→ch4 | 478/478 | 481 w | 923 002 080 B | 962/962 |
| C1306 | ch3→ch1 | 414/414 | 417 w | 799 614 816 B | 832/832 |
| C1306 | ch3→ch2 | 414/414 | 417 w | 799 614 816 B | 832/832 |
| C1306 | ch3→ch4 | 414/414 | 417 w | 799 614 816 B | 832/832 |

The unpadded plan was **REFUSED** by the applier in every pair, and the
sample-domain contract held at `1 171 323 checked, 0 bad` (C1305) and
`1 014 740 checked, 0 bad` (C1306).

### 3.3 Residual, all points, policy `hold`, coordinate `latest-start`

Samples. Source: `work/p3/p311/reports/residual-audit.md`,
`work/p3/p311/reports/final-result-matrix.md`.

| Corpus | Pair | median before → after | p95 before → after | max before → after |
|---|---|---|---|---|
| C1305 | ch3→ch1 | 1136.599 → 12.034 | 1555.670 → 1119.440 | 1649.311 → 1533.360 |
| C1305 | ch3→ch2 | 1143.852 → 6.574 | 1541.000 → 422.000 | 1640.000 → 1375.820 |
| C1305 | ch3→ch4 | 296.476 → 4.233 | 621.544 → 734.240 | 1064.141 → 1796.063 |
| C1306 | ch3→ch1 | 1191.827 → 14.586 | 1256.259 → 337.470 | 1728.612 → 1702.927 |
| C1306 | ch3→ch2 | 1236.400 → 8.464 | 1260.976 → 403.430 | 1728.620 → 1673.504 |
| C1306 | ch3→ch4 | 136.387 → 3.000 | 233.777 → 105.885 | 549.606 → 757.016 |

### 3.4 **The addendum correction — P4 must use these, not §3.3's tail**

`work/p3/p311/reports/residual-acceptance-addendum.md` corrects
`FINAL-REPORT.md`. The residual grid kept **every** returned delay, including
delays from windows the estimator had **refused**.

Decisive example, C1305 ch3→ch4 at `t = 20 s`:

| quantity | value |
|---|---:|
| `before` | −1064.14 samples — `valid = False`, `reason = intra_window_unstable` |
| applied shift from the plan | **+732** |
| expected `= before − applied` | −1796.14 |
| `after` | −1796.06 |
| **`excess = after − expected`** | **+0.078 samples** |

So the 1796-sample "residual" is a measurement the estimator had **already
declared it could not make**, at a point where the applier did exactly what it
was told.

Under the `both_valid` filter — the only population where "residual" means
*remaining misalignment* — source
`work/p3/p311/reports/residual-acceptance-filters.md`:

| Corpus | Pair | n kept | before med | after med | before p95 | after p95 | before max | after max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| C1305 | ch3→ch1 | 99 | 1140.879 | **4.539** | 1155.82 | **1036.86** | 1226.34 | **1139.03** |
| C1305 | ch3→ch2 | 107 | 1145.606 | **3.534** | 1172.64 | **14.06** | 1378.14 | **1130.37** |
| C1305 | ch3→ch4 | 218 | 292.744 | **2.225** | 622.12 | **15.68** | 737.69 | **771.16** |
| C1306 | ch3→ch1 | 74 | 1238.307 | **7.034** | 1252.03 | **104.22** | 1259.07 | **131.12** |
| C1306 | ch3→ch2 | 107 | 1242.583 | **6.854** | 1258.92 | **112.65** | 1262.86 | **1020.98** |
| C1306 | ch3→ch4 | 268 | 136.652 | **2.430** | 229.46 | **10.27** | 262.17 | **51.56** |

**Five of six pairs improve in every statistic on this population.** The sixth,
C1305 ch3→ch4, improves in median, p90, p95, p99 and mean; its maximum goes
`737.69 → 771.16`, i.e. **+4.5 %**, not the **+69 %** the unfiltered figure
implied.

**Excess on `both_valid`, across all six pairs: 0.31–1.96 samples.** Where the
plan's shift was applied, the output matches the plan to within two samples,
always. This is the single strongest mechanical result P3 produced.

### 3.5 The honest residual limitation

The filtered population is small exactly where the unfiltered numbers were
uninformative. Source: `residual-acceptance-addendum.md` §"The honest residual
limitation".

| Pair | measurable points | of grid | of plan coverage |
|---|---:|---:|---:|
| C1305 ch3→ch1 | 99 | 21 % | 26 % |
| C1305 ch3→ch2 | 107 | 22 % | 28 % |
| C1305 ch3→ch4 | 218 | 45 % | 93 % |
| C1306 ch3→ch1 | 74 | 18 % | 22 % |
| C1306 ch3→ch2 | 107 | 26 % | 32 % |
| C1306 ch3→ch4 | 268 | 64 % | 98 % |

> **On the low-coverage pairs, the correction can only be verified on about a
> fifth of the stream.** That is the real limitation — not a worsening tail.

And the addendum's own consequence statement, quoted exactly:

```text
Full channel-pair E2E       VALIDATED
Independent verification    VALIDATED
Coordinate semantics        AMBIGUOUS / DEFERRED
Gap policy                  CHARACTERIZED / DEFERRED
Residual threshold          NOT ESTABLISHED
Default path                UNCHANGED
Production correction       NOT READY (enablement) / READY FOR POLICY DECISION
```

### 3.6 Coverage and plan states

`T/U/R` = TRUSTED / UNRESOLVED_GAP / REENTRY. Under `hold`.
Source: `work/p3/p311/reports/final-result-matrix.md`.

| Corpus | Pair | valid | unresolved | coverage | plan states `T/U/R` |
|---|---|---:|---:|---:|---|
| C1305 | ch3→ch1 | 90 | 388 | 18.7 % | 57/388/36 |
| C1305 | ch3→ch2 | 99 | 379 | 20.6 % | 66/379/36 |
| C1305 | ch3→ch4 | 244 | 234 | 50.7 % | 193/234/54 |
| C1306 | ch3→ch1 | 78 | 336 | 18.7 % | 43/336/38 |
| C1306 | ch3→ch2 | 78 | 336 | 18.7 % | 43/336/38 |
| C1306 | ch3→ch4 | 305 | 109 | 73.1 % | 274/109/34 |

### 3.7 Residual split by the state that supplied the shift

`T` = the window's own measurement supplied the shift. `G` = UNRESOLVED_GAP,
a carried value. `R` = first trusted window after a gap.
Source: `work/p3/p311/reports/residual-by-state.md`.

The load-bearing row — C1305 ch3→ch1:

| state | n | median | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| T | 57 | 4.804 | 13.13 | 13.83 | 20.17 |
| G | 388 | 20.000 | 764.95 | 1120.73 | 1533.36 |
| R | 36 | 7.221 | 113.84 | 142.49 | 340.78 |

> A point whose shift came from a `G` window was NOT measured at that point, so
> a residual there measures the policy's carry rule, not the estimator's
> accuracy. **Mixing the two populations into one median is what makes a
> partially corrected stream look fully corrected.**

This is why P4 treats **state as part of the correction object**, not as
reporting metadata.

### 3.8 Independent instrument

Second estimator, different frame grid (200 ms frames, 100 ms hop, 30 s anchor,
8 kHz coarse search), reading only PCM. `core.sync_estimate` — **tracked
production code**, which is what makes it usable as a P4 cross-check.

| Corpus | Pair | before (samples) | after (samples) | after (ms) | conf before → after |
|---|---|---:|---:|---:|---|
| C1305 | ch3→ch1 | −1143.000 | −2.000 | −0.042 | 0.763 → 0.762 |
| C1305 | ch3→ch2 | −1148.000 | −3.000 | −0.062 | 0.634 → 0.634 |
| C1305 | ch3→ch4 | 264.000 | −2.000 | −0.042 | 0.577 → 0.612 |
| C1306 | ch3→ch1 | −1110.000 | −2.000 | −0.042 | 0.767 → 0.765 |
| C1306 | ch3→ch2 | −1255.000 | 70.000 | 1.458 | 0.761 → 0.760 |
| C1306 | ch3→ch4 | 137.000 | 2.000 | 0.042 | 0.735 → 0.680 |

Five of six land within **3 samples** of alignment; C1306 ch3→ch2 lands at
**70 samples / 1.458 ms**. That one is recorded as measured, not averaged away.

---

## 4. P3.11 coordinate finding

Source: `work/p3/p311/reports/coordinate-comparison.md`,
`work/p3/p311/coordinate/agreement-definitions.json`.

| contract | mapping | status |
|---|---|---|
| `latest-start` | `w = pos // hop` — shipped, and what `gap_semantics.apply_shift_plan` computes | IMPLEMENTED |
| `nearest-centre` | `w = (pos - win/2 + hop/2) // hop`, clipped to the plan | OBSERVED ONLY, NOT SELECTED |
| `sample-domain` | **NOT A THIRD CANDIDATE** — identical to `latest-start` | proven |

Why `sample-domain` is not independent: measurement windows are 30 s on a 10 s
hop, so **three** cover any sample; the largest covering index is `pos // hop`,
which is `latest-start`. Overlapping coverage at `win = 3·hop` makes the
identity arithmetic, not empirical.

### 4.1 The two agreement figures, and why both exist

`agreement-definitions.json` states the distinction that P4 must not collapse:

> **INDEX agreement:** does a sample read the same WINDOW under both contracts?
> At `win = 3*hop` the two mapping functions differ by one index for every block
> except the first three, so this figure is near zero. **VALUE agreement:** does
> a sample receive the same APPLIED SHIFT? This differs only where the plan's
> shift changes, so the disagreement is a band around each shift change.

| Corpus | plan windows | samples | index agree | index agreement | first index divergence | max positional diff |
|---|---:|---:|---:|---:|---:|---:|
| C1305 (all 3 pairs) | 481 | 230 750 520 | 480 000 | **0.21 %** | 10.0 s | 1 index (10.000 ms) |
| C1306 (all 3 pairs) | 417 | 199 903 704 | 480 000 | **0.24 %** | 10.0 s | 1 index (10.000 ms) |

Applied-shift **value** agreement, measured on ch3→ch4
(`coordinate/agreement-definitions.json`):

| Corpus | windows | value agreement | disagree samples | zones | zone length (hops) | first value divergence | max abs shift diff |
|---|---:|---:|---:|---:|---|---:|---:|
| C1305 | 481 | **0.5694050873644835** (56.94 %) | 99 360 000 | 73 | 1 … 15 | 20.0 s | **732 samples** |
| C1305 | 478 | 0.5694050873644835 | 99 360 000 | 73 | 1 … 15 | 20.0 s | 732 samples |
| C1306 | 417 | **0.3636936312095548** (36.37 %) | 127 200 000 | 63 | 1 … 19 | 20.0 s | **211 samples** |
| C1306 | 414 | 0.3636936312095548 | 127 200 000 | 63 | 1 … 19 | 20.0 s | 211 samples |

The `OPEN-DECISIONS.md` addendum rounds these to **56.94 % / 36.37 %**; the
`docs/audio-sync-p3/` overview and the P4 brief use "≈ 57 % / ≈ 36 %". Same
measurement. **P4 uses the unrounded values above.**

### 4.2 The coordinate choice is not metadata — measured on real PCM

Both arms, same measurement records, same policy `hold`, same resolved mask.
Source: `coordinate-comparison.md` §"The REAL PCM effect".

| Corpus | Pair | arm | after median | p95 before → after | max before → after |
|---|---|---|---|---|---|
| C1305 | ch3→ch1 | latest-start | 12.034 | 1555.670 → 1119.440 | 1649.311 → 1533.360 |
| C1305 | ch3→ch1 | nearest-centre | 12.000 | 1555.670 → 1119.622 | 1649.311 → 1533.360 |
| C1305 | ch3→ch2 | latest-start | 6.574 | 1541.000 → 422.000 | 1640.000 → 1375.820 |
| C1305 | ch3→ch2 | nearest-centre | 6.000 | 1541.000 → 423.045 | 1640.000 → 1375.820 |
| **C1305** | **ch3→ch4** | **latest-start** | **4.233** | 621.544 → **734.240** | 1064.141 → **1796.063** |
| **C1305** | **ch3→ch4** | **nearest-centre** | **3.253** | 621.544 → **734.240** | 1064.141 → **1064.141** |
| C1306 | ch3→ch1 | latest-start | 14.586 | 1256.259 → 337.470 | 1728.612 → 1702.927 |
| C1306 | ch3→ch1 | nearest-centre | 14.345 | 1256.259 → 351.708 | 1728.612 → 1702.927 |
| C1306 | ch3→ch2 | latest-start | 8.464 | 1260.976 → 403.430 | 1728.620 → 1673.504 |
| C1306 | ch3→ch2 | nearest-centre | 8.464 | 1260.976 → 427.540 | 1728.620 → 1673.504 |
| C1306 | ch3→ch4 | latest-start | 3.000 | 233.777 → 105.885 | 549.606 → 757.016 |
| C1306 | ch3→ch4 | nearest-centre | 2.773 | 233.777 → 105.885 | 549.606 → 757.016 |

Control: the two arms share one measurement, so every `before` matches — all
six `YES`.

**The one place the arms materially diverge is C1305 ch3→ch4's maximum**:
`latest-start` raises it to 1796.063; `nearest-centre` leaves it at the
before-value 1064.141. Both are recorded. **Neither is ranked here.** P4 does
not select a coordinate contract.

---

## 5. P3.11 gap-policy finding

Five arms measured on real PCM, six pairs, **one shared resolved mask**, one
shared coordinate contract (`latest-start`). Only the in-gap rule varies.
Source: `coordinate-comparison.md` §"stage D".

### 5.1 What each arm states and does

| policy | inside a gap | state |
|---|---|---|
| `current` | keeps the measurement-derived shift; its `resolved` flags claim gap windows are **measured** | reports gaps as `TRUSTED` |
| `hold` | carries the last trusted shift forward, `0` where there is nothing to carry | `UNRESOLVED_GAP` |
| `no_correction_gap` | `0` across every gap | `UNRESOLVED_GAP` |
| `explicit_state` | **identical shifts to `hold`** — state made first-class | `UNRESOLVED_GAP` |
| `linear` | numerical interpolation | research-only, `OFFLINE_ONLY`, **not a candidate** |

### 5.2 Distinct corrected-PCM hashes

| Corpus | Pair | distinct corrected-PCM sha256 | arms sharing a sha256 |
|---|---:|---|
| all six | — | **4 of 5 arms** | `explicit_state` + `hold` |

So `hold` and `explicit_state` are **bit-identical in output**: they differ only
in what they claim. That is `OPEN-DECISIONS.md` D5 in one row.

### 5.3 Residual per policy, all points

`no_correction_gap` is very close to a **no-op**: on C1305 ch3→ch1 its after
median is **1130.000** against a before median of 1136.599.

| Corpus | Pair | policy | after median | after p95 | after max |
|---|---|---|---:|---:|---:|
| C1305 | ch3→ch1 | `current` | 9.495 | 441.00 | 771.74 |
| C1305 | ch3→ch1 | `hold` | 12.034 | 1119.44 | 1533.36 |
| C1305 | ch3→ch1 | `no_correction_gap` | **1130.000** | 1555.67 | 1649.31 |
| C1305 | ch3→ch1 | `explicit_state` | 12.034 | 1119.44 | 1533.36 |
| C1305 | ch3→ch1 | `linear` | 10.667 | 484.73 | 771.74 |
| C1305 | ch3→ch2 | `current` | 6.000 | 392.80 | 485.00 |
| C1305 | ch3→ch2 | `hold` | 6.574 | 422.00 | 1375.82 |
| C1305 | ch3→ch2 | `no_correction_gap` | **1135.029** | 1541.00 | 1640.00 |
| C1305 | ch3→ch2 | `explicit_state` | 6.574 | 422.00 | 1375.82 |
| C1305 | ch3→ch2 | `linear` | 6.927 | 392.80 | 485.00 |
| C1305 | ch3→ch4 | `current` | 4.233 | 732.66 | 1796.06 |
| C1305 | ch3→ch4 | `hold` | 4.233 | 734.24 | 1796.06 |
| C1305 | ch3→ch4 | `no_correction_gap` | **35.555** | 602.00 | 1796.06 |
| C1305 | ch3→ch4 | `explicit_state` | 4.233 | 734.24 | 1796.06 |
| C1305 | ch3→ch4 | `linear` | 4.400 | 712.06 | 1796.06 |
| C1306 | ch3→ch1 | `current` | 14.536 | 309.89 | 683.93 |
| C1306 | ch3→ch1 | `hold` | 14.586 | 337.47 | 1702.93 |
| C1306 | ch3→ch1 | `no_correction_gap` | **1126.344** | 1255.62 | 1728.61 |
| C1306 | ch3→ch1 | `explicit_state` | 14.586 | 337.47 | 1702.93 |
| C1306 | ch3→ch1 | `linear` | 14.199 | 284.26 | 683.93 |
| C1306 | ch3→ch2 | `current` | 8.292 | 302.49 | 658.52 |
| C1306 | ch3→ch2 | `hold` | 8.464 | 403.43 | 1673.50 |
| C1306 | ch3→ch2 | `no_correction_gap` | **1137.667** | 1256.28 | 1728.62 |
| C1306 | ch3→ch2 | `explicit_state` | 8.464 | 403.43 | 1673.50 |
| C1306 | ch3→ch2 | `linear` | 10.103 | 276.25 | 658.52 |
| C1306 | ch3→ch4 | `current` | 3.000 | 105.88 | 759.98 |
| C1306 | ch3→ch4 | `hold` | 3.000 | 105.88 | 757.02 |
| C1306 | ch3→ch4 | `no_correction_gap` | **4.114** | 227.73 | 549.61 |
| C1306 | ch3→ch4 | `explicit_state` | 3.000 | 105.88 | 757.02 |
| C1306 | ch3→ch4 | `linear` | 3.000 | 107.13 | 763.02 |

### 5.4 The invalidity of the nomination instrument

`OPEN-DECISIONS.md` D1 and `HANDOFF-CHECKLIST.md` §2.1 record the reason no
policy is ranked: `policy_jump` / `artificial_jump` is **policy-invariant at
two-sided boundaries** — **0 of 8 136 policy-pairs differ**, maximum observed
**5.0 samples**, and **0 / 0 / 0 / 0** events over 50 / 100 / 200 / 500-sample
thresholds.

> The strongest reason no ranking is offered is that the ranking instrument
> **cannot see a difference between the policies.**

**P4 does not re-derive a ranking and does not invent a substitute metric.**

---

## 6. P3.12 — adaptive coarse-to-fine

Source: `work/p3/p312/FINAL-REPORT.md`, `comparison.md`, `performance.md`,
`provenance.json`.

```text
final verdict:  NO ADVANTAGE DEMONSTRATED
safety half:    MISSED_TRANSITION = 0 on known ground truth
cost:           estimator calls ↑, runtime ↑  (measured 2-10x the exhaustive pass)
```

`provenance.json` records the shipped formulation as
`v2 "p312-adaptive-2 (dense local grid + edge windows; the shipped version)"`,
refinement axis **`TIME INTERVAL ONLY -- [-3000,+3000] is never bisected`**.

`OPEN-DECISIONS.md` addendum records it precisely:

> P3.12 is **not** recorded as a failed idea. It is recorded as: *the current
> tested formulation provides no demonstrated efficiency advantage.* Its safety
> half succeeded — `MISSED_TRANSITION = 0` on known ground truth — and the
> reason for the efficiency failure is measured, not assumed.

`P4-HANDOFF.md` §4 also records the defect that matters most to P4's own test
design:

> P3.12's `MISSED_TRANSITION` metric asked whether the true delay varies inside a
> reference block, and a perfect step makes every block internally constant, so
> it **could never fire** while reporting zero misses on a fixture containing a
> 320-sample step.

**P4 consequence: the adaptive path is NOT wired into production, NOT
re-opened, and NOT used to justify changing the measurement configuration.**

---

## 7. Findings P4 is forbidden to "resolve"

Carried forward verbatim in substance from `P4-HANDOFF.md` §5 and
`HANDOFF-CHECKLIST.md` §4:

```text
do not invent a residual acceptance threshold
do not raise a threshold so a named corpus passes
do not select a gap policy or a coordinate contract on the research's behalf
do not enable anything by default before the D1-D4 decisions
do not treat the reference grid as ground truth - it is the same estimator's
    exhaust, so a shared bias is invisible in it
do not rewrite a historical P3 report to match a later result
do not describe P3.12 as a failed idea
```

Specific quotation hazards confirmed and re-checked this session:

| hazard | what P4 must not do |
|---|---|
| P3.4 `51.2 %` neighbour-range figure | `do_not_quote: true`; truncation artefact (641 persisted vs 818 aggregated). Correct figure is P3.4's own aggregate **59.5 %**, and it must carry its band **~30–68 %** |
| "`current` is algorithmically wrong" | FALSE. Its state machine is `CORRECT` (**132/132**, 0 fake `TRUSTED`); its `resolved` flags over-claim on **1 of 22**. Semantic over-claim, not algorithmic error |
| "the tail gets worse on both CH3→CH4 pairs" | **CORRECTED.** C1306 ch3→ch4's max improves (`262.17 → 51.56`); C1305 ch3→ch4 worsens by **4.5 %** on the measurable population |
| `sample-domain` as a third coordinate candidate | FALSE. It is identical to `latest-start` |
| "`core/` is unmodified" | Invalid as an unconditional statement. Scoped form only; see `current-state-audit.md` §4 |
| P3.2 `NOT VALIDATED` vs P3.10/P3.11 measured improvement | **UNRECONCILED. Recorded, not settled.** See §8 |

---

## 7b. Discrepancy register — found while building this freeze, recorded not resolved

Extracting the numbers above surfaced internal inconsistencies **inside the P3
record**. P4's rule is the research line's rule: a later phase is not entitled to
settle a contradiction by rewriting either side. Every item below is recorded with
both values, and **P4 relies on neither side of a disputed figure for a production
decision.**

### 7b.1 P3.10 vs P3.11 coordinate figures — claimed as a reproduction, numerically different

P3.11 `FINAL-REPORT.md` §7 states *"P3.10 reported the **value** figure
(57.17 % / 36.69 %, 73 / 63 zones) and P3.11 **reproduces it**"*. The two sets of
numbers are not equal:

| quantity | P3.10 | P3.11 | agree? |
|---|---:|---:|---|
| C1305 value agreement | 57.17 % | **56.94 %** | differs at 3rd significant figure |
| C1306 value agreement | 36.69 % | **36.37 %** | differs at 3rd significant figure |
| zones | 73 / 63 | 73 / 63 | **yes** |
| C1305 max abs shift difference | **670** | **732** | **differs** |
| C1306 max abs shift difference | **79** | **211** | **differs materially** |
| first disagreement | **30.0 s** | **20.0 s** | **differs** |

`work/p3/p311/coordinate/agreement-definitions.json` records
`first_disagreement_s: 20.0`, so P3.10's `30.0 s` is the outlier.

**P4 uses the P3.11 values and the raw JSON**, because they are the later
measurement and the raw artifact agrees with them. **P4 does not declare P3.10
wrong.**

### 7b.2 Median-improvement factor ranges that do not follow from the printed medians

P3.11 §6.2 states the median improves *"by a factor of 25–145 everywhere"*. The
§6.1 medians it refers to give, as before ÷ after:
`1136.599/12.034 = 94.4`, `1143.852/6.574 = 174.0`,
`296.476/4.233 = 70.0`, `1191.827/14.586 = 81.7`,
`1236.400/8.464 = 146.1`, `136.387/3.000 = 45.5` — i.e. **45.5–174.0**, not
25–145.

Separately, `docs/audio-sync-p3/RESEARCH-SUMMARY.md` states the same fact for the
**`both_valid`** population as *"all six medians improve 56×–324×"*. From
`residual-acceptance-filters.md`: `1140.879/4.539 = 251.3`,
`1145.606/3.534 = 324.2`, `292.744/2.225 = 131.6`, `1238.307/7.034 = 176.1`,
`1242.583/6.854 = 181.3`, `136.652/2.430 = 56.2` — i.e. **56.2–324.2**, which
matches. **The `both_valid` range is the one that reproduces**; the §6.2 range
does not, and §6.2 does not label its population.

The P4 brief's *"improvement ≈ 56×–324×"* is therefore the **`both_valid`**
figure, and it is correct as such.

### 7b.3 "Two pairs improved in every statistic" vs §6.2's own table

§6.2 says two pairs improved in every statistic. §6.2's direction table shows
**four** all-IMPROVED rows (C1305 ch3→ch1, C1305 ch3→ch2, C1306 ch3→ch1,
C1306 ch3→ch2), and `residual-audit.md` prints the identical table. The addendum's
*"five of six"* is a **third** count, for the `both_valid` population.

### 7b.4 The `9–30 samples` tail range

P3.11 §6.4 and §15 state the measured population's p95 is **9–30 samples**. The
full artifact (`residual-by-state.md`) gives TRUSTED-group p95 values of
13.83, 18.47, 13.41 (C1305) and **117.98**, **135.53**, 9.19 (C1306). **Two of
six lie outside 9–30.** §6.4 also prints only four of the six pairs.

### 7b.5 The `177–1121 samples` gap range

P3.11 §6.4 and §15 state the gap population's p95 is **177–1121 samples**. The
G-group p95 values in `residual-by-state.md` are
1120.73, 440.34, 756.47, 417.20, 488.24, 538.19 — i.e. **417.2–1120.7**.
`177` appears in that artifact only as C1306 ch3→ch1's **p90** (177.18).

### 7b.6 "no-op on three pairs" vs "nearly a no-op on the low-coverage pairs"

§8 finding 1 names three pairs; §15 item 5 says "low-coverage pairs", which by
§5's coverage table includes a fourth (C1306 ch3→ch2) that §8's table does not
print at all. **§8's gap-policy table is missing C1306 ch3→ch2 entirely**, while
its own prose claims "five arms" and byte-identical PCM "on all six pairs".
`work/p3/p311/reports/gap-policy-by-pair.json` (109 641 B) is the machine-readable
source that must hold the missing arms; **P4 does not read a conclusion out of the
missing cell.**

### 7b.7 P3.12's "v2" names two different runs

P3.12 `FINAL-REPORT.md` §7/§11 gives v2 as **1126 calls, ratio 0.43**;
`comparison.md` / `performance.md` give v2 as **948 calls, ratio 0.51** and call
the 1126-call run **v2final**. The verdict is the same either way (calls rose), so
**P4's use of P3.12 is unaffected** — but a P4 statement will say
"2–10× the exhaustive pass" and will not name a v2 call count.

### 7b.8 Defect-count ambiguity

P3.11 §13 says "fifteen defects"; the addendum says "the other fifteen", implying
sixteen. P3.10 §12 names three and then says the family "appeared three more
times". **P4 does not quote a defect count.**

### 7b.9 Instrument disagreement, explicitly unresolved

C1306 ch3→ch2: main verifier median **8.46 samples** vs second instrument
**+70.000 samples** (`fine_fit_r2 = −0.21`). P3.11 §6.5: *"reported as a
disagreement between instruments, not resolved by preferring one."*
**P4 records it and does not average it.** In `policy-matrix.md` and the final
report it is the one row where the independent verifier disagrees, and it is
never used to claim agreement.

### 7b.10 What P4 concludes from this register

```text
no P3 report is edited
no figure is silently preferred
where P4 needs a number it uses the raw artifact or the later measurement,
    and says which
no disputed figure is load-bearing for a production decision
```

The pattern matters more than any single item: **the same phase that found
"reporting only the median is choosing the flattering statistic" also printed
summary ranges that its own detail tables do not reproduce.** P4 treats
summary-level ranges in the P3 record as **leads**, and re-derives from the
artifact or omits the claim.

---

## 8. The unresolved contradiction

`work/audio-sync-cleanup/commit-msg.txt` records it, and the cleanup pass
declined to settle it:

> P3.2's `NOT VALIDATED` correction verdict and P3.10/P3.11's measured large
> median improvement on the same corpora are unreconciled. A cleanup phase is
> not entitled to settle it by rewriting either report.

**P4 classification: OPEN, recorded, not P4's to close.** P4 relies on
P3.10/P3.11's *measured* numbers for the mechanical claims (apply reproduces the
plan to within 1.96 samples) and does **not** rely on either verdict for a
production decision.

---

## 9. Implementation facts P4 must honour

Extracted from `docs/audio-sync-p3/production-semantics.md`,
`PRODUCTION-REQUIREMENTS.md` and `HANDOFF-CHECKLIST.md`. These are **binding**:
each one already went wrong once in this repository. All were re-verified against
the tracked production source in this session where a tracked file is named.

### 9.1 The production insertion point

```text
core/channel_sync.py lines 839-851   the per-track correction loop
core/sync_fix.py    line 38          shift_stream(src, dst, *,
                                         delay_samples, sample_rate,
                                         storage_dtype, chunk_seconds)
```

Verified in the tracked tree. `shift_stream` takes a **scalar**
`delay_samples`; the whole trajectory is collapsed to one number before the call.
`PRODUCTION-REQUIREMENTS.md` G1 records that a per-window variant is therefore
**a drop-in replacement at one call site** — and `HANDOFF-CHECKLIST.md` §3.4.1
records that it **is not a drop-in replacement** because it changes that
signature. Both statements are true of different things: the *call site* is one
place; the *signature* changes.

### 9.2 `AudioPlan` is not the place for this

`production-semantics.md` §1: *"`AudioPlan` is deliberately **not** the place for
this."* `core/audio_models.py` documents it as **declarative, does not execute**.
P4 keeps `AudioPlan` untouched. The brief's "`AudioPlan = None` keeps the original
path" is preserved by not touching `AudioPlan` at all.

### 9.3 One place decides the shift, and it is inside the `try`

`HANDOFF-CHECKLIST.md` §3.4.2. In the tracked source, `core/channel_sync.py`
line 843 computes `shift = int(np.rint(e.delay_samples))` **outside** the `try:`
that begins at line 844; that value is used **only for reporting**
(`rows[i]["shift_samples"]`), while the value the audio is actually corrected
with comes from `e.delay_samples` passed into `shift_stream` on line 846.

**P4 requirement:** a per-window integration keeps the property that **one place
decides the shift and it is inside the `try`** — so `int(np.rint(nan))` raising
`ValueError` is caught rather than escaping the single-track fallback.

### 9.4 No silent context substitution — the phase's central defect family

`production-semantics.md` §6.7:

> A chunk silently substituting a guess for context it cannot see.

The four recorded instances:

| # | defect | symptom |
|---|---|---|
| 1 | `shifts[-1]` used to mean "the previous window" | numpy returns the **chunk's last window** — a plausible number from the wrong place |
| 2 | a shift range validated **after** a signed cast | **audio aligned 5 samples the wrong way, no error, no warning** — the most serious defect of the line |
| 3 | a gap endpoint not supplied | invented `[100, 100, 100, 100]` where the truth was `[167, 233, 300, 367]` |
| 4 | a truncated mask read as if it spanned the stream | `[0]` where the truth was `[400]` |

> **The fix is never a better default — it is to make the required context
> explicit and mandatory, so absence raises.**

### 9.5 Named constraints carried into the production design

| id | constraint | measured reason |
|---|---|---|
| X1 | feature flag, **default OFF**, and an audit showing the OFF path is **byte-identical** | the only acceptable way to ship a correction whose policy is undecided |
| X2 | `window_samples` and `hop_samples` as **separate parameters from named constants** | conflating them caused an out-of-bounds read: a 64-entry plan for a 4-window stream, numpy returned adjacent memory rather than raising |
| X3 | one place decides the shift, inside the `try` | §9.3 |
| X4 | the stream is **passed through, never materialised** | `np.asarray(src)` on a `RawStream` **does not fail** — it walks the sequence protocol: correct values, **86× (10 s) and 173× (60 s)** the cost |
| X5 | chunk invariance asserted at the **byte** level, ≥3 chunk sizes, **one putting a boundary inside a segment** | a correlation-based check would have passed a measured **40-sample** disagreement |
| X6 | every fixture has a **zero case** and is shown to **discriminate** | three "different" fixtures were once bit-identical and **298 stale results were silently reused** |
| X7 | the P1 guard is re-run | recorded **27 PASS / 0 FAIL** |
| X8 | memory re-measured **with a positive control that actually allocates** | a memory check reported PASS **three times** while measuring nothing; in one case every case errored, the verdict fell through to `UNKNOWN`, and the script **exited 0** |
| X9 | rollback tested **as a path**, not assumed | the identity plan is the revert |
| X10 | no threshold, fixture or criterion adjusted to make a corpus pass | the standing rule of the line |
| X11 | any validity claim must survive an instrument **sharing no code** with the one that produced it | the reference grid is the same estimator's exhaust, so a shared bias is invisible in it |
| X12 | `core/` re-verified, not assumed | see `current-state-audit.md` §4 |

### 9.6 The memory-control trap, in detail

`production-semantics.md` §5.1, both failure modes:

1. the first measurement charged the **source array's own allocation** to the
   call, because the baseline was taken before the source was built;
2. the control used `np.zeros`, which is **calloc-backed and stays lazily
   untouched** — so an untouched allocation reports a **zero** working-set delta
   and the control **measures nothing**.

**P4's memory test must therefore allocate with a touched, non-zero-filled array
for its positive control.**

### 9.7 Reference numbers P4 will be compared against

| quantity | value | source |
|---|---|---|
| PCM apply throughput, engine path | **439–474 MB/s** | P3.11 §11 |
| PCM apply, engine-applied in isolation | 1.40–2.72 s, **309–547 MB/s** | P3.11 §11 |
| peak RSS, engine apply | **110 MiB, flat** | P3.11 §11 |
| research gather path (for contrast) | 10.28–12.14 s, 72–74 MB/s, 185 MiB | P3.11 §11 |
| measurement | **4.3–4.7 s per 30 s window**, 97–99 % of total wall time | P3.11 §11 |
| planning | 2.6–3.0 s | P3.11 §11 |
| verification | 187.6–254.5 s for 962 points, ~0.22 s/point | P3.11 §11 |
| P3.11's own 3632 MiB run | **its own code**, not the engine: it materialised one int64 coordinate label per sample (1.8 GB at C1305 length) | P3.11 §11 |

The last row is the P4 warning: **P4 must not materialise a per-sample coordinate
label.** The coordinate decision document §0 records the same 1.6–1.8 GB figure
from the P3.11 test docstring.

### 9.8 Test evidence P4 inherits

| suite | result |
|---|---|
| P1 regression guard | **27 PASS / 0 FAIL** |
| P3.7 state machine Q0–Q5 | all rows PASS |
| P3.8 semantics selftest | **100 PASS / 0 FAIL** |
| P3.8 overlap geometry | **26 PASS / 0 FAIL** |
| P3.8 failure injection | **30 cases, 0 silent-wrong**, 13 `VALUE_OK` / 17 `RAISED` |
| P3.10 regression | **26 PASS / 0 FAIL** |
| P3.11 coordinate tests | **71 PASS / 0 FAIL** |
| P3.11 adversarial | **447 PASS / 0 FAIL** (13 fixtures) |
| P3.11 chunk invariance | **29 PASS / 0 FAIL** — `chunk_windows` ∈ {1,2,7,31,128,256,1024,4096}; state sequence, shift sequence, output PCM sha256 and the independent residual identical for every chunk size and every policy |
| P3.11 regression | **70 PASS / 0 FAIL** |
| P3.11 cross-batch reproduction | **6 of 6 matched** |

One internal conflict, recorded not resolved: `production-semantics.md` §1 states
the semantics selftest is **94 PASS / 0 FAIL** while `provenance.md` records
**100 PASS / 0 FAIL** for the same file. Both are recorded; **P4 quotes neither.**

### 9.9 The `hold`-vs-`current` measured difference, exactly

`production-semantics.md` §7: *"The only numerical difference is the leading
gap."* P3.7 measured the two policies as differing in applied shift on
**0.4–0.5 %** of windows on the real corpora. That is consistent with
`coordinate-comparison.md`'s distinct-hash result (4 of 5 arms) only if the
leading-gap windows are a small fraction — and they are: the `T/U/R` tables show
`current` reporting **0** unresolved where `hold` reports **109–388**, so the
*full* plan differs in state on every gap window while the *applied shift*
differs only where the leading gap forced a different value.

**Both statements are correct and describe different things: state differs
everywhere a gap exists; the applied number differs only at the leading gap.**

### 9.10 P3.10's `ch3_to_ch4`-only E2E and the driver-supplied adapter

`work/p3/p310/FINAL-REPORT.md` §11 records that `gap_semantics.apply_shift_plan`
rejects a `str`/`Path` source:

```text
"src must be an ndarray, or expose .shape and .read_into for the streamed path"
```

and **ships no adapter**. P3.10 supplied one **in its driver**, not in the engine.

**P4 consequence:** the production path opens its own stream and passes a stream
object in — it does not rely on an adapter existing, and it does not add one to
research code.

---

## 10. Frozen identity — hashes and commits P4 pins

### 10.1 Repository identity for each phase

| phase | provenance doc | generated (UTC) | HEAD at phase start | pushed / merged / tagged |
|---|---|---|---|---|
| P3 base | `docs/audio-sync-p3/provenance.md` | `2026-10-02T10:12:32Z` | `2e96f7399fd3545fdd08f5315183db7aef7faac5` | none / — / — |
| P3.10 | `p3.10-provenance.md` | `2026-10-02T13:04:34Z` | `0e3000abca26dc77a24b097435893433a5bdd888` | **NO / NO / NO** |
| P3.11 | `p3.11-provenance.md` | not stated | `48ef2aee7ca8e4a690f14213fdf9b0d2de1b7eed` | NO / NO / NO |
| P3.12 | `p3.12-provenance.md` | not stated | `d9182b4b8107c4129a6a8bba6b8a328604a13f2a` | NO / NO / NO |
| P4 start | `work/p4/current-state-audit.md` | — | `8bb229764f551955db2c392e4b2bee6bfd7e9418` | NO / NO / NO |

The P3 branch is in **no remote ref** and **no tag points at it**.

### 10.2 Production files P3 hashed as UNTOUCHED

| file | bytes | SHA-256 (from `provenance.md` / `p3.10-provenance.md`) |
|---|---:|---|
| `core/sync_fix.py` | 5 912 | `6D3F75B403C56CAF78190BFCFEA3954ABC7CD15E11B6737B833E6080CF8F79C2` |
| `core/channel_sync.py` | 47 060 | `14283AB7215FF592E8ADD766963195DB303408F5BF0A70E3526B20D28ADE1BAB` |
| `core/sync_estimate.py` | 31 622 | `773C96A746DFFFE7A2A1852B059AF51B0FD51A84CAEA182E5A22403D31BDDF3E` |

These are the **P1 guard hashes**. P4 re-hashes these three files before and after
every change to prove the default path is untouched; see `implementation.md`.

### 10.3 The nine user-owned modifications, hashed

`p3.10-provenance.md` records these as a tamper check, with the note *"These are
**pre-existing user work**, not P3 … They were not modified, reverted, stashed or
reset by P3.10."*

| file | bytes | SHA-256 |
|---|---:|---|
| `1kt.py` | 88 050 | `4CAB92CA9509C163A1F213898D060AE7CD42BCB4611A4EC9B941E55DC54AD857` |
| `VERSION` | 6 | `AAF6721DC37C40B4233ACCFC081F25776FF3BD1D2A4B9D5CEA760D1C2CF86665` |
| `core/batch_hw.py` | 93 782 | `C40A51BE1A8F12A366FF7F01300892D62E3D96AEE20C409D2B1AA54FE1DCB43B` |
| `preservation/checker.py` | 7 732 | `E54A012675FD66BBE0760A769D8F862565B168EC1B6B9D8B2229412089428C42` |
| `preservation/gpac.py` | 31 909 | `C9EE74764E05A1AC00C6D0FE7103934384EF0CBD311DFDD5B4D9E261770825E1` |
| `preservation/validate.py` | 21 359 | `6F7B8F9E82CD8F16CA8D46C06C24B48869C3DF6795F40FE165EB532A875A5EFF` |
| `tests/selftest/runner/engine.py` | 6 152 | `DBD8D5775983D3623C9DAC1199BBE4F9A780F8DF3D491231776288E8EE045CD8` |
| `tests/selftest/suites/core.py` | 24 996 | `DEE050C35B3CDFE9AE9750783D792ECEDB853755E57A81DAE9AA65AE3ACD1824` |
| `tests/selftest/suites/toolchain.py` | 10 397 | `BD765F60F61E60A339A502FCDC5F09D1907D8F6664EAD38C6E082844DB917205` |

**P4 re-verifies these nine hashes at the start and the end of P4.** A mismatch is
a reportable event, not something to repair.

### 10.4 Corpus hashes (4 files recorded by P3.12 `provenance.json`; the other 4 by P3)

| corpus | channel | bytes | SHA-256 |
|---|---|---:|---|
| C1305 | ch1 | 923 002 080 | `A1BEFEFCBE9A1AFC6621DC941C4914E1E7A66D10FC065ABA397353D38CD6221F` |
| C1305 | ch2 | 923 002 080 | `31DF1572AEEB4020D68AF6DE092146A496434991ABB542D8BD11EE2E55860E6B` |
| C1305 | ch3 (reference) | 923 002 080 | `360A9F4D61A37E68DFF67CEFDF9ABBF74D3D694C15272CABDC7DCCC9A814F462` |
| C1305 | ch4 | 923 002 080 | `E70BBCBFFF4904B4C25B24FEC23D554B804CC913166EC3AD5D72BC76A19A46ED` |
| C1306 | ch1 | 799 614 816 | `313EB8D37744CBFAC5950F86484A539068B9BFFE9E323ECC80EE5CB53A5D0F47` |
| C1306 | ch2 | 799 614 816 | `8B11A078AAD73EF8DB93DC57416864BC765502CB039264EA6D5A1A4745C0F266` |
| C1306 | ch3 (reference) | 799 614 816 | `3970DD19B83DDD2D68B2D1F51D833EA9B686DEF85139C5697ABBD9BCF3B629A6` |
| C1306 | ch4 | 799 614 816 | `3E468FAC4BC0349856A100FD01C82825E74E4E876ABDDD4A0E4948BBFD65312E` |

```text
P3.11 measures n mod hop:
    C1305  n = 230 750 520, hop = 480 000  ->  350 520   (non-zero)
    C1306  n = 199 903 704, hop = 480 000  ->  223 704   (non-zero)
so both corpora take the seam = 3 case, not the seam = 2 case.
```

### 10.5 Frozen configuration — NOT a P4 variable

```text
sample rate    48 000 Hz, float32, 1 channel
window          1 440 000 samples = 30.0 s
hop               480 000 samples = 10.0 s
search                   ±3 000 samples = 62.5 ms
residual grid     5 s window, 10 s hop, aligned to the correction block boundaries
ratio             W = 3·hop  -- the production coefficient, and the only ratio
                  at which the 15 s offset is bounded by one hop
```

`measure_resumable.py` **asserts** `n_win == 1_440_000`, `hop == 480_000`,
`search == 3000`, *"so a silent default change cannot make a run quietly measure
something else."*

**P4 keeps these. `P4-HANDOFF.md` §2 and `HANDOFF-CHECKLIST.md` §3.2 both forbid
changing the window or the hop inside an integration.**

### 10.6 Key artifact hashes for the P3.11 corrected PCM

These are the six `apply_sha256` values, and they are the P4 regression's
**strongest single check**: a P4 run that reproduces a P3.11 apply is proven to
have produced the identical corrected PCM.

| Corpus | Pair | apply bytes | SHA-256 |
|---|---|---:|---|
| C1305 | ch3→ch1 | 923 002 080 | `6E4D5A22D9E05CCD35352C41A7D6538BB22ECBBC7EC1933D75D9ACC8A3726E2E` |
| C1305 | ch3→ch2 | 923 002 080 | `30259ACDD7957A2E28860BB0D85599DC0A635D8DA3421CE0C7AB59019C1D25C4` |
| C1305 | ch3→ch4 | 923 002 080 | `413E0205BB5622EA838F98339BD6E0FDAE1235AFCE5B6BBE93C60747B5D9B20C` |
| C1306 | ch3→ch1 | 799 614 816 | `03F63C2388366CD4A2A640E0C655222805ADBB56B8DAE78F48B677934462FDDF` |
| C1306 | ch3→ch2 | 799 614 816 | `569B7D706686CE34110ABD321FE04B19938C9343610A7C3C8A4781F57B900ADC` |
| C1306 | ch3→ch4 | 799 614 816 | `642B1B2197F99DF4F6AEEB2569437E1F12D61E727D606E518E111A4553A3A4F4` |

`413E0205…` for C1305 ch3→ch4 is **the same hash P3.10 recorded**, which is how
P3.11 demonstrated cross-batch reproduction (6 of 6 matched).

### 10.7 Version strings

| semantic | version | source |
|---|---|---|
| P1 production algorithm | `2.3.0-p1` | `core/channel_sync.py` line 116 |
| P3.8 gap semantics engine | `p38-semantics-1` | `work/p3/p38/semantics/gap_semantics.py` |
| P3 measurement layer | `3.0.0-dev` | `work/p3/measure.py` |
| P3.2 planner / plan format | `p3-correction-1` | `work/p3/correct.py` |
| P3.12 adaptive | `p312-adaptive-1` / `p312-adaptive-2` (shipped) | `work/p3/p312/provenance.json` |
| `VERSION` on disk | `0.8.1` (uncommitted bump; tag `v0.8.0`) | `VERSION`, `git tag -l "v0.8*"` |

### 10.8 Engine hashes

| artifact | bytes | SHA-256 |
|---|---:|---|
| `work/p3/p38/semantics/gap_semantics.py` (the applier P3.10/P3.11 used) | 47 836 | `CE10F3FB68484CFE95B4C5D184A01EC351FDCE78C0DCFBCCA1209B7F0931283D` |
| `work/p3/p311/production_chain_p311.py` | 53 222 | not pinned in a tracked document |

**The engine is untracked.** `provenance.md`: *"It does not make the
implementation durable. A hash of a gitignored file cannot restore that file. If
`work/` is deleted, the evidence is gone and these hashes are a tombstone, not a
backup."*

**P4 does not import it.** §6 of `current-state-audit.md` records why: the tracked
test that does import it cannot run on a clone.

---

## 11. Freeze verdict

```text
P1 baseline                    FROZEN - VALIDATED, must not regress
P3.10 apply mechanics + sign   FROZEN - VALIDATED
P3.11 six-pair E2E             FROZEN - VALIDATED
P3.11 residual tail claim      FROZEN - CORRECTED by its own addendum
P3.11 coordinate contract      FROZEN - AMBIGUOUS / DEFERRED (D2)
P3.11 gap policy               FROZEN - CHARACTERIZED / DEFERRED (D1)
P3.12 adaptive                  FROZEN - NO ADVANTAGE DEMONSTRATED
residual acceptance threshold  FROZEN - NOT ESTABLISHED (D3)
default production behaviour   FROZEN - UNCHANGED (D8)
```

No P3 report was modified to produce this file. Every figure above is copied
from a named artifact whose path is given inline.
