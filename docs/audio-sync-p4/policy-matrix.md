# P4 — Stage 2: production policy matrix

**This is a decision aid. It selects nothing.**

Two independent product decisions are on the table, plus a third that P4 raises
because the evidence forces it:

```text
A.  GAP POLICY        - what a correction does where no measurement exists    (D1, D5)
B.  COORDINATE POLICY - which window a sample is corrected by                 (D2)
C.  FEATURE ENABLEMENT- whether the capability may exist in production at all  (D8)
```

Every figure below is a P3 measurement, cited to its artifact in
`evidence-freeze.md`. No policy is ranked, no policy is called a winner, and no
policy is called best. Numbers that look like an argument are followed by the
evidence that undercuts them, deliberately.

---

## 0. The frame every option sits inside

Three facts constrain all of them and are not themselves policy choices.

### 0.1 Coverage is 18.7 %–73.1 % — and 19.5 %–74.4 % is a DIFFERENT number

**This is a trap and it is stated first for that reason.** P3 reports coverage in
two populations that are not the same and are not interchangeable. Both are real,
both are correct, and neither may be substituted for the other.

| quantity | population | C1305 | C1306 |
|---|---|---|---|
| **measurement coverage** | `valid` windows of the **478 / 414 measurement** windows | **19.5 % / 21.3 % / 51.7 %** | **19.6 % / 19.6 % / 74.4 %** |
| **plan coverage** | summed `T+U+R` states over the **481 / 417 plan** windows, policy `hold` | 18.7 % / 20.6 % / 50.7 % | 18.7 % / 18.7 % / 73.1 % |

Sources: measurement coverage, P3.11 `FINAL-REPORT.md` §5; plan coverage,
`work/p3/p311/reports/final-result-matrix.md`.

The denominators differ (478/414 measured windows vs 481/417 plan windows), and
the plan population includes the **3 tail windows that have no measurement at
all**. A reader who substitutes one for the other is comparing different things.

`OPEN-DECISIONS.md` D4 quotes **19.5 %–74.4 % valid** — the measurement
population. `P4-HANDOFF.md` and `PRODUCTION-HANDOFF.md` quote the same.

**P4 rule: every time coverage is quoted, the population is named.** Where one
figure is required, P4 uses **19.5–74.4 % (measurement)** because that is the
figure the decision register carries.

### 0.1b Plan-window state counts, policy `hold`

| Corpus | Pair | valid | unresolved | coverage |
|---|---|---:|---:|---:|
| C1305 | ch3→ch1 | 90 | 388 | 18.7 % |
| C1305 | ch3→ch2 | 99 | 379 | 20.6 % |
| C1305 | ch3→ch4 | 244 | 234 | 50.7 % |
| C1306 | ch3→ch1 | 78 | 336 | 18.7 % |
| C1306 | ch3→ch2 | 78 | 336 | 18.7 % |
| C1306 | ch3→ch4 | 305 | 109 | 73.1 % |

`OPEN-DECISIONS.md` D4 records the refusal rate across all six pairs as
**19.5 %–74.4 % valid**; the per-pair figures above are the plan-level view.

**Every sample is rewritten regardless.** The measured span per pair is 481 or
417 windows and the applier addresses every sample
(`ceil(n/hop)`, and the unpadded plan is refused). So a choice made on 20 % of
the timeline determines what happens to 100 % of the samples.

### 0.2 The in-gap delay is UNOBSERVABLE, not merely imprecise

`OPEN-DECISIONS.md` D5:

> the real delay inside a gap is **not observable**, not merely imprecise.
> Raising the measurement precision does nothing — `σ_meas` from 0 to 2.0 moves
> the answer almost not at all. There is no estimator, no window length, and no
> policy that recovers it.

`P4-HANDOFF.md` §2 states the same as a prohibition:

> Any P4 proposal that amounts to "measure harder inside the gap" is already
> answered.

P3.12 reinforced it independently: an excursion **shorter than the measurement
window** is averaged away at any refinement depth, because refinement changes
*where* windows are placed, not *how long* they are.

**Consequence for this matrix: no option below is scored by accuracy inside a
gap, because no option can be. The choice is what to *do* with a value nobody
measured.**

### 0.3 The instrument that would have ranked the policies cannot see a difference

`policy_jump` / `artificial_jump`, two-sided boundaries:
**0 of 8 136 policy-pairs differ**, maximum observed **5.0 samples**,
**0 / 0 / 0 / 0** events over 50 / 100 / 200 / 500-sample thresholds.

P4 does not substitute a different metric. A substitute chosen after seeing the
data would be exactly the failure mode `HANDOFF-CHECKLIST.md` §4 prohibits.

---

## A. GAP POLICY

### A.1 The candidates

| # | policy | shift inside a gap | epistemic state it may claim | corrected-PCM distinct from |
|---|---|---|---|---|
| A1 | `current` (baseline only) | keep the measurement-derived shift | claims gap windows `TRUSTED` | — |
| A2 | `hold` | carry the last trusted shift; `0` where there is nothing to carry | `UNRESOLVED_GAP` | A1, A3, A5 |
| A3 | `no_correction_gap` | `0` across every gap | `UNRESOLVED_GAP` | A1, A2, A5 |
| A4 | `explicit_state` | **identical shifts to `hold`** | `UNRESOLVED_GAP`, state first-class | identical to A2 |
| A5 | `linear` (not a candidate) | numerical interpolation | `OFFLINE_ONLY` reference | A1, A2, A3 |

`current` is included **only as the baseline the other four are measured
against.** It is not a candidate P4 proposes to keep or remove.

### A.2 Measured behaviour, per candidate

All five arms share **one resolved mask** and **one coordinate contract
(`latest-start`)**. Only the in-gap rule varies.

#### State counts produced

| Corpus / Pair | policy | TRUSTED | UNRESOLVED_GAP | REENTRY |
|---|---|---:|---:|---:|
| C1305 ch3→ch1 | `current` | **481** | **0** | **0** |
| C1305 ch3→ch1 | `hold`/`no_correction_gap`/`explicit_state` | 57 | 388 | 36 |
| C1305 ch3→ch2 | `current` | **481** | **0** | **0** |
| C1305 ch3→ch2 | the three | 66 | 379 | 36 |
| C1305 ch3→ch4 | `current` | **481** | **0** | **0** |
| C1305 ch3→ch4 | the three | 193 | 234 | 54 |
| C1306 ch3→ch1 | `current` | **481→417** | **0** | **0** |
| C1306 ch3→ch1 | the three | 43 | 336 | 38 |
| C1306 ch3→ch2 | `current` | **417** | **0** | **0** |
| C1306 ch3→ch2 | the three | 43 | 336 | 38 |
| C1306 ch3→ch4 | `current` | **417** | **0** | **0** |
| C1306 ch3→ch4 | the three | 274 | 109 | 34 |

`current` reports **zero** unresolved windows on every pair while the same
resolved mask yields 109–388 unresolved windows under the other arms. That is
the over-claim `HANDOFF-CHECKLIST.md` §3.6.3 requires to be described accurately:
**the state machine is `CORRECT` (132/132, 0 fake `TRUSTED`); the `resolved`
flags over-claim on 1 of 22.** A semantic over-claim, not an algorithmic error.

#### Residual, all points

| Corpus / Pair | policy | after median | after p95 | after max |
|---|---|---:|---:|---:|
| C1305 ch3→ch1 | `current` | 9.495 | 441.00 | 771.74 |
| C1305 ch3→ch1 | `hold` | 12.034 | 1119.44 | 1533.36 |
| C1305 ch3→ch1 | `no_correction_gap` | **1130.000** | 1555.67 | 1649.31 |
| C1305 ch3→ch1 | `explicit_state` | 12.034 | 1119.44 | 1533.36 |
| C1305 ch3→ch1 | `linear` | 10.667 | 484.73 | 771.74 |
| C1305 ch3→ch2 | `current` | 6.000 | 392.80 | 485.00 |
| C1305 ch3→ch2 | `hold` | 6.574 | 422.00 | 1375.82 |
| C1305 ch3→ch2 | `no_correction_gap` | **1135.029** | 1541.00 | 1640.00 |
| C1305 ch3→ch2 | `explicit_state` | 6.574 | 422.00 | 1375.82 |
| C1305 ch3→ch2 | `linear` | 6.927 | 392.80 | 485.00 |
| C1305 ch3→ch4 | `current` | 4.233 | 732.66 | 1796.06 |
| C1305 ch3→ch4 | `hold` | 4.233 | 734.24 | 1796.06 |
| C1305 ch3→ch4 | `no_correction_gap` | 35.555 | 602.00 | 1796.06 |
| C1305 ch3→ch4 | `explicit_state` | 4.233 | 734.24 | 1796.06 |
| C1305 ch3→ch4 | `linear` | 4.400 | 712.06 | 1796.06 |
| C1306 ch3→ch1 | `current` | 14.536 | 309.89 | 683.93 |
| C1306 ch3→ch1 | `hold` | 14.586 | 337.47 | 1702.93 |
| C1306 ch3→ch1 | `no_correction_gap` | **1126.344** | 1255.62 | 1728.61 |
| C1306 ch3→ch1 | `explicit_state` | 14.586 | 337.47 | 1702.93 |
| C1306 ch3→ch1 | `linear` | 14.199 | 284.26 | 683.93 |
| C1306 ch3→ch2 | `current` | 8.292 | 302.49 | 658.52 |
| C1306 ch3→ch2 | `hold` | 8.464 | 403.43 | 1673.50 |
| C1306 ch3→ch2 | `no_correction_gap` | **1137.667** | 1256.28 | 1728.62 |
| C1306 ch3→ch2 | `explicit_state` | 8.464 | 403.43 | 1673.50 |
| C1306 ch3→ch2 | `linear` | 10.103 | 276.25 | 658.52 |
| C1306 ch3→ch4 | `current` | 3.000 | 105.88 | 759.98 |
| C1306 ch3→ch4 | `hold` | 3.000 | 105.88 | 757.02 |
| C1306 ch3→ch4 | `no_correction_gap` | 4.114 | 227.73 | 549.61 |
| C1306 ch3→ch4 | `explicit_state` | 3.000 | 105.88 | 757.02 |
| C1306 ch3→ch4 | `linear` | 3.000 | 107.13 | 763.02 |

**Read this table with §3.4 of `evidence-freeze.md` open.** These are
**unfiltered** rows, so they include windows the estimator had refused. A large
value in this table measures the material, not the correction.

#### Direction per statistic

| Corpus / Pair | policy | median | p90 | p95 | max | mean |
|---|---|---|---|---|---|---|
| C1305 ch3→ch1 | `current` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1305 ch3→ch1 | `hold` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1305 ch3→ch1 | `no_correction_gap` | IMPROVED | IMPROVED | UNCHANGED | UNCHANGED | IMPROVED |
| C1305 ch3→ch2 | `current` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1305 ch3→ch2 | `hold` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1305 ch3→ch2 | `no_correction_gap` | IMPROVED | UNCHANGED | UNCHANGED | UNCHANGED | IMPROVED |
| C1305 ch3→ch4 | `current` | IMPROVED | WORSENED | WORSENED | WORSENED | IMPROVED |
| C1305 ch3→ch4 | `hold` | IMPROVED | WORSENED | WORSENED | WORSENED | IMPROVED |
| C1305 ch3→ch4 | `no_correction_gap` | IMPROVED | IMPROVED | IMPROVED | WORSENED | IMPROVED |
| C1305 ch3→ch4 | `linear` | IMPROVED | IMPROVED | WORSENED | WORSENED | IMPROVED |
| C1306 ch3→ch1 | `current` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1306 ch3→ch1 | `hold` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1306 ch3→ch1 | `no_correction_gap` | IMPROVED | IMPROVED | IMPROVED | UNCHANGED | IMPROVED |
| C1306 ch3→ch2 | `current` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1306 ch3→ch2 | `hold` | IMPROVED | IMPROVED | IMPROVED | IMPROVED | IMPROVED |
| C1306 ch3→ch2 | `no_correction_gap` | IMPROVED | IMPROVED | IMPROVED | UNCHANGED | IMPROVED |
| C1306 ch3→ch4 | `current` | IMPROVED | IMPROVED | IMPROVED | WORSENED | IMPROVED |
| C1306 ch3→ch4 | `hold` | IMPROVED | IMPROVED | IMPROVED | WORSENED | IMPROVED |
| C1306 ch3→ch4 | `no_correction_gap` | IMPROVED | IMPROVED | IMPROVED | UNCHANGED | IMPROVED |
| C1306 ch3→ch4 | `linear` | IMPROVED | IMPROVED | IMPROVED | WORSENED | IMPROVED |

The `no_correction_gap` row is the one that looks superficially attractive —
more "IMPROVED" cells than any other arm. **It is not an improvement.** Its
median only moves because it applies **zero** to a stream whose before-median is
large; on C1305 ch3→ch1 its after median is **1130.000** against a before median
of **1136.599**. It is very close to a **no-op**, and the direction column is
comparing a nearly-unchanged number against a changed one. That is the
`median-only` trap in its purest form.

#### Distinct corrected PCM

| Corpus / Pair | distinct corrected-PCM sha256 | arms sharing a hash |
|---|---:|---|
| all six | **4 of 5 arms** | `explicit_state` + `hold` |

### A.3 What each candidate would give a product owner

#### A1 `current` — keep the measurement-derived shift, claim everything `TRUSTED`

| | |
|---|---|
| **Advantages** | it is the only arm whose applied shifts are the measurements themselves, so no value is invented; on the unfiltered table it has the best after-median on 5 of 6 pairs; it is the shipped reference the other four are defined against |
| **Risks** | it asserts `TRUSTED` on **109–388 windows per pair that no measurement supports**; a consumer that trusts the flag is misled on 18.7–80.7 % of the timeline |
| **Failure modes** | a downstream reader cannot distinguish a measured span from a carried one; the same plan read by two consumers yields two different beliefs |
| **Coverage behaviour** | claims 100 % coverage on every pair; supports 18.7–73.1 % |
| **Tail behaviour** | best unfiltered after-max on 5 of 6 pairs |
| **Uncertainty behaviour** | **states no uncertainty at all.** This is the arm's defining property |

Note the pairing that `HANDOFF-CHECKLIST.md` §3.6.3 insists on: `current`'s
**state machine** is correct; only its `resolved` flags over-claim.

#### A2 `hold` — carry the last trusted shift, declare `UNRESOLVED_GAP`

| | |
|---|---|
| **Advantages** | invents no new value — it repeats a value that *was* measured; declares the gap honestly; exercised end-to-end on all six pairs in production form; its output is bit-identical to `explicit_state`, so making the state first-class later costs no audio change |
| **Risks** | the carried value is stale by construction; on C1305 ch3→ch4 its after-p95 is `734.24` vs `current`'s `732.66` and its after-max `1796.06` vs `771.74` — worse tails on the unfiltered view |
| **Failure modes** | a long gap holds a shift that is no longer true, and the state says "unresolved" but the number looks authoritative |
| **Coverage behaviour** | declares 109–388 unresolved windows per pair; the only honest count available |
| **Tail behaviour** | worse than `current` on 5 of 6 unfiltered after-max figures |
| **Uncertainty behaviour** | explicit `UNRESOLVED_GAP` per window, plus `REENTRY` at 34–54 windows per pair |

#### A3 `no_correction_gap` — apply `0` across every gap

| | |
|---|---|
| **Advantages** | the in-gap applied value is a constant, so nothing about the material is asserted; on the direction table it shows more IMPROVED cells than any other arm |
| **Risks** | it is **almost a no-op**: at 80.7 % coverage-less windows it leaves the stream essentially uncorrected, so it is not a correction policy at all on those pairs |
| **Failure modes** | the correction silently stops being applied for most of the timeline while still writing a full corrected stream; a user sees "sync ran" and a near-uncorrected file |
| **Coverage behaviour** | same honest unresolved counts as A2 |
| **Tail behaviour** | improves p95 on C1305 ch3→ch4 (`602.00` vs `hold`'s `734.24`) but its after-max there is still `1796.06` |
| **Uncertainty behaviour** | honest state, but the *value* `0` is itself an assertion — that the correct shift is none |

The measured no-op character is in `OPEN-DECISIONS.md` D1's addendum:
`no_correction_gap` is **nearly a no-op on low-coverage pairs**, so the choice
decides **whether most of the stream is corrected at all**.

#### A4 `explicit_state` — same shifts as `hold`, state made first-class

| | |
|---|---|
| **Advantages** | bit-identical audio to A2 while making the epistemic state a first-class output; the only arm whose *claim* matches its *behaviour*; costs nothing in audio terms over A2 |
| **Risks** | **`OPEN-DECISIONS.md` D6 is entirely unresolved**: if a downstream consumer cannot represent uncertainty, this option is **unavailable regardless of its merits**, and D1 collapses to a choice among the others. **Nobody has looked at the consumers** |
| **Failure modes** | built, and then dropped at the boundary because nothing downstream accepts a state |
| **Coverage behaviour** | as A2 |
| **Tail behaviour** | as A2 — byte-identical PCM |
| **Uncertainty behaviour** | this is the only arm that *is* an uncertainty behaviour |

#### A5 `linear` — numerical interpolation (not a candidate)

| | |
|---|---|
| **Advantages** | none claimed here; it exists as an `OFFLINE_ONLY` reference |
| **Risks** | it **manufactures** a value where none was measured and, if labelled `TRUSTED`, would be the exact silent-wrong P3 spent twelve phases eliminating |
| **Failure modes** | interpolation ≠ measurement. A consumer reading the number cannot tell it was invented |
| **Coverage behaviour** | as A2 |
| **Tail behaviour** | best after-p95 on C1306 ch3→ch2 (`276.25`) and best after-max on C1305 ch3→ch1 (`771.74`) — **and this is precisely why it is dangerous.** It looks good because it smooths over unmeasured regions |
| **Uncertainty behaviour** | must never be labelled `TRUSTED` |

`HANDOFF-CHECKLIST.md` §2.1 and `OPEN-DECISIONS.md` D1 both place `linear` /
`next` as research-only, declared `OFFLINE_ONLY` in the engine.

### A.4 The measured facts that constrain the choice

```text
1. hold and explicit_state produce BIT-IDENTICAL corrected PCM on all six pairs.
   They differ only in what they claim.                            (4 of 5 arms)

2. no_correction_gap is very close to a no-op on low-coverage pairs.
   C1305 ch3->ch1 after median 1130.000 vs before 1136.599.

3. current reports 0 unresolved windows where the same mask yields 109-388.

4. The instrument that would rank these cannot see a difference:
   0 of 8136 policy-pairs, max 5.0 samples, 0 events at 4 thresholds.

5. The in-gap value is UNOBSERVABLE. No policy recovers it.

6. Every sample is rewritten regardless of coverage.
```

### A.5 What P4 does with this

```text
GAP POLICY = DEFERRED
FINAL PRODUCT POLICY = DEFERRED
DECISION_REQUIRED = D1, and D5 which shapes D1's answer
```

P4 does **not** pick A1, A2, A3 or A4. It builds the mechanism so that **any** of
them can be selected without a code change, and ships it **OFF**.

---

## B. COORDINATE POLICY

### B.1 The candidates

| # | contract | mapping | status |
|---|---|---|---|
| B1 | `latest-start` | `w = pos // hop` — the latest-starting window at or before `pos` | IMPLEMENTED (shipped) |
| B2 | `nearest-centre` | `w = (pos - win/2 + hop/2) // hop`, clipped to the plan | OBSERVED ONLY, NOT SELECTED |
| B3 | `sample-domain` | the largest window whose measurement window covers the sample | **NOT A THIRD CANDIDATE — identical to B1** |

**B3 must not be reported as a third option.** Measurement windows are 30 s on a
10 s hop, so three cover any sample; the largest covering index is `pos // hop`,
which is B1. This is arithmetic (overlap at `win = 3·hop`), asserted on every
fixture and both production plan sizes by
`tests/audio_sync_p311/test_coordinates.py`.

### B.2 Measured effect

#### Index agreement — does a sample read the same *window*?

| Corpus | windows | samples | agree | agreement | first divergence | max index diff |
|---|---:|---:|---:|---:|---:|---:|
| C1305 | 481 | 230 750 520 | 480 000 | **0.21 %** | 10.0 s | 1 index (10.000 ms) |
| C1306 | 417 | 199 903 704 | 480 000 | **0.24 %** | 10.0 s | 1 index (10.000 ms) |

The agreement is `480 000` samples on both corpora — exactly the first hop — and
`first divergence = 10.0 s` is that same boundary. From block 1 onward the two
mappings **never** agree.

#### Value agreement — does a sample get the same *applied shift*?

| Corpus | value agreement | disagree samples | zones | zone length (hops) | first divergence | max abs shift diff |
|---|---:|---:|---:|---|---:|---:|
| C1305 | **0.5694050873644835** (56.94 %) | 99 360 000 | 73 | 1 … 15 | 20.0 s | **732 samples** |
| C1306 | **0.3636936312095548** (36.37 %) | 127 200 000 | 63 | 1 … 19 | 20.0 s | **211 samples** |

#### The real-PCM divergence

| Corpus | Pair | arm | after median | after p95 | after max |
|---|---|---|---:|---:|---:|
| C1305 | ch3→ch1 | latest-start | 12.034 | 1119.440 | 1533.360 |
| C1305 | ch3→ch1 | nearest-centre | 12.000 | 1119.622 | 1533.360 |
| C1305 | ch3→ch2 | latest-start | 6.574 | 422.000 | 1375.820 |
| C1305 | ch3→ch2 | nearest-centre | 6.000 | 423.045 | 1375.820 |
| **C1305** | **ch3→ch4** | **latest-start** | **4.233** | **734.240** | **1796.063** |
| **C1305** | **ch3→ch4** | **nearest-centre** | **3.253** | **734.240** | **1064.141** |
| C1306 | ch3→ch1 | latest-start | 14.586 | 337.470 | 1702.927 |
| C1306 | ch3→ch1 | nearest-centre | 14.345 | 351.708 | 1702.927 |
| C1306 | ch3→ch2 | latest-start | 8.464 | 403.430 | 1673.504 |
| C1306 | ch3→ch2 | nearest-centre | 8.464 | 427.540 | 1673.504 |
| C1306 | ch3→ch4 | latest-start | 3.000 | 105.885 | 757.016 |
| C1306 | ch3→ch4 | nearest-centre | 2.773 | 105.885 | 757.016 |

Control: both arms share one measurement, so every `before` matches — all six
`YES`.

### B.3 What each gives a product owner

#### B1 `latest-start`

| | |
|---|---|
| **Advantages** | it is the shipped contract and the one every P3.3–P3.8 real-corpus number was produced under; changing it **silently alters every historical plan**; within the plan's coverage it is exactly `pos // hop`, so the closed form and the materialised trajectory provably agree |
| **Risks** | the measurement is centred **15 s** after the window's start while the plan applies it from the block's start, so a measurement describing time `T` is applied from `T − 15 s`; bounded by one hop (**10 s = 480 000 samples**) **at `win = 3·hop` and only there** — for `win > 3·hop` the offset exceeds one hop |
| **Failure modes** | the correction **anticipates** a change by up to one hop |
| **Coverage** | applied to all 481 / 417 windows |
| **Tail** | raises C1305 ch3→ch4's after-max to **1796.063** — the single worst tail figure in the P3.11 coordinate comparison |
| **Uncertainty** | unaffected; this is a mapping, not a state |

#### B2 `nearest-centre`

| | |
|---|---|
| **Advantages** | equally deterministic; symmetric in error magnitude — it **lags** a change rather than anticipating it; on C1305 ch3→ch4 it leaves the after-max at the before-value **1064.141** instead of 1796.063 |
| **Risks** | changes **99 360 000 (C1305) / 127 200 000 (C1306)** applied sample values; disagreement is organised into **73 / 63 zones** of 1–19 hops; **max abs shift difference 732 / 211 samples** |
| **Failure modes** | the raw mapping returns a **negative index at block 0** (unclamped), so the implementation must clip explicitly or read out of bounds — a P3-recorded defect class |
| **Coverage** | applied to all windows, differently |
| **Tail** | better on C1305 ch3→ch4's max, worse on C1306 ch3→ch1's and ch3→ch2's p95 |
| **Uncertainty** | unaffected |

Neither direction is preferable on the evidence. `OPEN-DECISIONS.md` D2 frames
the choice exactly:

> the choice is whether a correction should **anticipate** a change or **lag**
> it.

### B.4 The measured facts that constrain the choice

```text
1. sample-domain is NOT a third candidate; it IS latest-start.   (proven)

2. The two mappings differ by at most ONE window index, and one index
   is one hop = 10 s of position in the shift trajectory.

3. Index agreement 0.21 % / 0.24 %; value agreement 56.94 % / 36.37 %.
   The two figures answer different questions and neither substitutes
   for the other.

4. The choice moves up to 732 / 211 samples of applied shift and up to
   127 200 000 samples of output.

5. The only material real-PCM divergence found is C1305 ch3->ch4's
   after-max: 1796.063 (latest-start) vs 1064.141 (nearest-centre).
```

### B.5 What P4 does with this

```text
COORDINATE POLICY = DEFERRED
DECISION_REQUIRED = D2 (product + engineering jointly)
```

`latest-start` is treated as the **implementation default**, because it is what
the P3 chain actually applied and changing it would invalidate every P3.3–P3.8
real-corpus number. **That is a statement about reproducibility, not a selection.**
The production design makes the mapping an explicit, named contract so a future
decision can change it in one place, with the consequence stated rather than
discovered.

---

## C. FEATURE ENABLEMENT

| # | option | meaning |
|---|---|---|
| C1 | `D8 = no` / absent | no time-varying correction path exists in production |
| C2 | default **OFF**, explicit opt-in | the path exists, is gated, and cannot run unless asked for |
| C3 | default ON | the path runs for every eligible job |

### C.1 Evidence

| consideration | measured value |
|---|---|
| coverage if enabled | **18.7–73.1 %** of windows have a measurement |
| samples rewritten | **100 %** — `ceil(n/hop)` windows, every sample addressed |
| what fills the rest | **policy-dependent**, and the policy is `DEFERRED` |
| end-to-end cost | **UNKNOWN** through the production pipeline. `OPEN-DECISIONS.md` D4: *"decode + measure + correct + encode, end to end. It has never been measured through the production pipeline."* |
| measurement cost alone | **3988 s / 478 windows** (C1305), **1596 s / 414 windows** (C1306) — ~3–8 s per 30 s window on uncompressed input |
| PCM apply cost in isolation | **439–474 MB/s**, peak RSS flat at **110 MiB** |
| adaptive measurement | **2–10×** the exhaustive pass — no reduction available by the route tested |
| residual acceptance | **NOT ESTABLISHED** |

### C.2 The standing decision this must respect

`OPEN-DECISIONS.md` "Decisions that are NOT open":

| decision | status |
|---|---|
| enable any P3 correction by default | **DECIDED — no** — no flag exists; the module is unreachable from `1kt.py` |
| fold the P3 time-varying correction into the P1 constant path | **DECIDED — no** |

`HANDOFF-CHECKLIST.md` §1 item 1 states what would make C2 legitimate:

> **A feature flag with default OFF, and an audit showing the OFF path is
> byte-identical.** *Why:* the only acceptable way to ship a correction whose
> policy is undecided.

And its note for a reviewer, which P4 changes the status of:

> the current implementation needs no flag — it lives outside `core/` entirely,
> so the default path is unchanged **because the code is unreachable**. **If it
> is ever wired in, this item becomes live.**

### C.3 What P4 does with this

```text
C1  -> superseded by building the capability, but its OBSERVABLE consequence
       (default behaviour unchanged) is preserved exactly
C2  -> THIS IS WHAT P4 BUILDS
C3  -> DECISION_REQUIRED, product owner, and NOT taken
```

**Default remains OFF. The default path must be byte-identical.** If no product
decision arrives, `production default enablement = DEFERRED` is the legal and
expected endpoint.

---

## D. Summary of the three matrices

| decision | candidates | P4 status | owner |
|---|---|---|---|
| **A** gap policy | A1 `current` (baseline) / A2 `hold` / A3 `no_correction_gap` / A4 `explicit_state` / A5 `linear` (offline) | **DEFERRED** — mechanism built, selection not made | product (D1, D5) |
| **B** coordinate policy | B1 `latest-start` / B2 `nearest-centre` (B3 not a candidate) | **DEFERRED** — mechanism parameterised, default preserved for reproducibility | product + engineering (D2) |
| **C** enablement | C1 none / C2 gate, default OFF / C3 default ON | **C2 BUILT. C3 DEFERRED** | product (D8) |

```text
NO POLICY IS RANKED. NO POLICY IS CALLED BEST. NO POLICY IS SELECTED.
The recommendation document (policy-recommendation.md) states what engineering
can and cannot support, and marks every product decision REQUIRED.
```
