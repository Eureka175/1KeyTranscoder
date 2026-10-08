# P4 — policy recommendation

**This document contains no product decision.** It states what engineering can
support, what it cannot, and what the tradeoffs are. Every product decision below
is marked `REQUIRED`.

The brief's §39 forbids the words *winner*, *best* and *obviously correct* here
unless the product requirement says so. **None of them appears.**

---

## 0. The separation this document exists to preserve

```text
engineering correctness        !=   product policy
```

P3 proved the mechanism works. That does **not** mean it should be on. The four
engineering results and the four product questions are **different kinds of
statement**:

| engineering result (established) | product question (open) |
|---|---|
| measurement is reproducible and exact against a model | is the corrected output good enough? |
| the apply reproduces the plan to within 2 samples | should a gap be carried, zeroed, or declared? |
| six real pairs run end to end | should a correction anticipate or lag a change? |
| the state machine is honest | does any consumer accept an uncertainty? |

**Nothing in the left column answers anything in the right column.**

---

## 1. Engineering recommendation

Stated as recommendations **about engineering**, not about product behaviour.

### R-1. Keep the time-varying path OFF until D1, D2 and D5 are answered

| | |
|---|---|
| **Why** | the answer to "which gap policy" *is* the fill rule; with no answer, most of the timeline would be filled by a policy nobody selected **while 100 % of samples are rewritten** |
| **Risk of ignoring this** | a shipped default nobody chose, on a stream where 79–81 % of windows have no measurement, is the one outcome the whole P3 line exists to prevent |
| **Cost of compliance** | zero — the module is already unwired and nothing changes |
| **Evidence** | coverage 19.5–74.4 %; `current` reports 0 unresolved where the mask has 388 |

### R-2. Do not treat this as an upgrade path for P1

| | |
|---|---|
| **Why** | P1's fixed-offset correction is `VALIDATED` on 137 real A7M5 files with `residual = 0.00 sample` and **27 PASS / 0 FAIL**, and `OPEN-DECISIONS.md` already records `DECIDED — no` on folding the two together |
| **Risk of ignoring this** | regressing a validated production path for an undecided one |
| **Cost of compliance** | zero — P1 is untouched |

### R-3. Keep `latest-start` as the implementation default, for reproducibility only

| | |
|---|---|
| **Why** | it is the contract every P3.3–P3.8 real-corpus number was produced under, and P4 reproduces the P3.11 corrected PCM **byte-for-byte** under it (30/30 arms) |
| **What this is NOT** | not a selection. Changing the default silently alters every historical plan |
| **Risk of ignoring this** | every historical P3 number becomes uninterpretable, and the byte-exact regression anchor is lost |
| **Mitigation built in** | the join is a named, declared parameter; `nearest-centre` is implemented and reproduced byte-exactly too, so changing the answer is a one-line declared change with its consequence known |

### R-4. Separate the correction value from the claim in any shipped API

| | |
|---|---|
| **Why** | P3.11 measured that a residual whose shift came from a `G` window **measures the policy's carry rule, not the estimator's accuracy**: on C1305 ch3→ch1, `T` p95 = 13.83 samples against `G` p95 = 1120.73 |
| **Risk of ignoring this** | *"mixing the two populations into one median is what makes a partially corrected stream look fully corrected"* |
| **Cost of compliance** | zero — already implemented (`CorrectionDecision`, `derive_states`) |

### R-5. Do not re-open the closed research questions to make this easier

| | |
|---|---|
| **Why** | `P4-HANDOFF.md` §2 names seven settled or failed items, and the brief's §61 names four more |
| **Specifically** | adaptive coarse-to-fine (`NO ADVANTAGE DEMONSTRATED`, and it costs **2–10×**, not less); fractional correction (`NOT NEEDED`); resampling (`NOT JUSTIFIED`); a second measurement geometry (attempted, failed); in-gap trajectory reconstruction (**the value is unobservable — no estimator recovers it**) |
| **Risk of ignoring this** | spending the phase's budget re-deriving a negative result |

### R-6. Treat the P3 record's summary ranges as leads, not facts

| | |
|---|---|
| **Why** | building `evidence-freeze.md` found **ten** places where a P3 summary figure does not reproduce from that phase's own detail table (§7b) |
| **Risk of ignoring this** | a production decision justified by a number its own artifact contradicts |
| **Cost of compliance** | low — the raw artifacts exist and were used here |

### R-7. Do not wire the path in without a rolling-back path and a live pipeline measurement

| | |
|---|---|
| **Why** | end-to-end cost through decode + measure + correct + encode **has never been measured** (`OPEN-DECISIONS.md` D4); measurement alone is 4.3–4.7 s per 30 s window |
| **Risk of ignoring this** | a pipeline that is technically correct and operationally unusable |
| **What P4 provides** | the apply is measured (671–814 MB/s) and bounded; the unknown is everything around it |

---

## 2. GAP POLICY — the candidates, with their tradeoffs

Each entry gives advantages, risks, failure modes, coverage, tail behaviour and
uncertainty behaviour. **No entry is recommended over another.**

### A1 `current` — keep the measurement-derived shift, claim everything `TRUSTED`

| | |
|---|---|
| **Advantages** | the applied shifts *are* the measurements, so nothing is invented; best unfiltered after-median on 5 of 6 pairs; the shipped reference the other arms are defined against |
| **Risks** | asserts `TRUSTED` on **109–388 windows per pair that no measurement supports** |
| **Failure modes** | a downstream reader cannot distinguish a measured span from a carried one |
| **Coverage** | claims 100 %; supports 18.7–73.1 % |
| **Tail** | best unfiltered after-max on 5 of 6 pairs |
| **Uncertainty** | **states none.** That is the arm's defining property |
| **Accuracy note** | its state *machine* is `CORRECT` (132/132, 0 fake `TRUSTED`); only the `resolved` flags over-claim. **A semantic over-claim, not an algorithmic error** |

### A2 `hold` — carry the last trusted shift, declare `UNRESOLVED_GAP`

| | |
|---|---|
| **Advantages** | invents no new value — it repeats a value that *was* measured; declares the gap honestly; exercised end-to-end on all six pairs; **bit-identical audio to `explicit_state`**, so making the state first-class later costs no audio change |
| **Risks** | the carried value is stale by construction, and staleness inside a gap is **not measurable** |
| **Failure modes** | a long gap holds a shift that is no longer true while the number looks authoritative |
| **Coverage** | declares 109–388 unresolved windows per pair |
| **Tail** | worse than `current` on 5 of 6 unfiltered after-max figures |
| **Uncertainty** | explicit `UNRESOLVED_GAP` per window, plus `REENTRY` at 34–54 windows per pair |

### A3 `no_correction_gap` — apply `0` across every gap

| | |
|---|---|
| **Advantages** | the in-gap value asserts nothing about the material; the **only** policy that is chunk-invariant with **no context at all**; shows the most "IMPROVED" cells in the direction table |
| **Risks** | it is **very close to a no-op** at low coverage — C1305 ch3→ch1 after-median **1130.000** against a before-median of **1136.599** |
| **Failure modes** | the correction silently stops being applied for most of the timeline while still writing a full corrected stream: a user sees "sync ran" and a near-uncorrected file |
| **Coverage** | same honest unresolved counts as A2, but the lowest **correction** coverage of any arm |
| **Tail** | improves p95 on C1305 ch3→ch4 (602.00 vs `hold`'s 734.24); after-max there still 1796.06 |
| **Uncertainty** | honest state; but the value `0` is itself an assertion — that the correct shift is none |

**The `median-only` trap applies to this arm more than any other.** Its direction
column looks best because it is comparing a nearly-unchanged number with a changed
one.

### A4 `explicit_state` — identical shifts to `hold`, state first-class

| | |
|---|---|
| **Advantages** | **bit-identical audio to `hold`** (verified on all six pairs, and reproduced byte-exactly by P4) while making the epistemic state a first-class output; the only arm whose *claim* matches its *behaviour* |
| **Risks** | **D6 is entirely unresolved**: if a downstream consumer cannot represent uncertainty, this option is **unavailable regardless of its merits**, and D1 collapses to a choice among the others |
| **Failure modes** | built, then dropped at the boundary because nothing downstream accepts a state |
| **Coverage** | as A2 |
| **Tail** | as A2 — byte-identical PCM |
| **Uncertainty** | this is the only arm that *is* an uncertainty behaviour |

### A5 `linear` — numerical interpolation (**not a candidate**)

| | |
|---|---|
| **Advantages** | none claimed |
| **Risks** | it **manufactures** a value where none was measured; labelled `TRUSTED` it would be the exact silent-wrong P3 spent twelve phases eliminating |
| **Failure modes** | **interpolation != measurement**, and a consumer reading the number cannot tell it was invented |
| **Coverage** | as A2 |
| **Tail** | best after-p95 on C1306 ch3→ch2 (276.25) and best after-max on C1305 ch3→ch1 (771.74) — **and that is precisely why it is dangerous**: it looks good because it smooths over unmeasured regions |
| **Uncertainty** | must never be labelled `TRUSTED`. Declared `OFFLINE_ONLY` |

### A6 What the measured evidence says about the choice, and what it does not

```text
SAYS:      hold and explicit_state produce bit-identical audio; they differ only
           in what they claim
SAYS:      no_correction_gap is nearly a no-op at low coverage, so the choice
           decides whether most of the stream is corrected at all
SAYS:      current reports 0 unresolved where the mask has 388
SAYS:      every sample is rewritten regardless of coverage
DOES NOT:  say which is better
DOES NOT:  offer a ranking - the instrument is policy-invariant
           (0 of 8136 policy-pairs, max 5.0 samples)
```

> **`DECISION_REQUIRED: D1`** — *inside a gap, is uncorrected alignment worse than
> invented structure?* **Owner: product.** And **`D5`** — *is the correction's
> purpose alignment as a physical goal, or alignment as a claimed guarantee?* —
> which determines whether the question is even about the audio.

**D6 is a precondition, not a parallel question:** if no downstream consumer can
represent `UNRESOLVED_GAP`, A4 is off the table before D1 is asked.

---

## 3. COORDINATE POLICY — the candidates, with their tradeoffs

### B1 `latest-start`

| | |
|---|---|
| **Advantages** | the shipped contract and the one every P3.3–P3.8 real-corpus number was produced under; P4 reproduces the P3.11 corrected PCM byte-for-byte under it; within plan coverage it is exactly `pos // hop`, so the closed form and the materialised trajectory provably agree |
| **Risks** | a measurement describing time `T` is applied from `T − 15 s`; bounded by one hop (**10 s = 480 000 samples**) **at `win = 3·hop` and only there** |
| **Failure modes** | the correction **anticipates** a change by up to one hop |
| **Coverage** | applied to all 481 / 417 windows |
| **Tail** | raises C1305 ch3→ch4's after-max to **1796.063** — the worst tail figure in the coordinate comparison; and that figure is the refused-window artefact `evidence-freeze.md` §3.4 documents |
| **Verification consequence** | `excess` stays at **0.615 samples**, so a verifier's expectation computed from the plan agrees with reality |

### B2 `nearest-centre`

| | |
|---|---|
| **Advantages** | equally deterministic; symmetric in error magnitude — it **lags** a change rather than anticipating it; on C1305 ch3→ch4 it leaves the after-max at the before-value **1064.141** instead of 1796.063 |
| **Risks** | changes **99 360 000 (C1305) / 127 200 000 (C1306)** applied sample values; disagreement is organised into **73 / 63 zones** of 1–19 hops; max abs shift difference **732 / 211 samples**; changing the default silently alters every historical plan |
| **Failure modes** | the raw mapping returns a **negative index at block 0** (unclamped), so an implementation must clip explicitly or read out of bounds — a P3-recorded defect class |
| **Coverage** | applied to all windows, differently |
| **Tail** | better on C1305 ch3→ch4's max; worse on C1306 ch3→ch1's and ch3→ch2's p95 |
| **Verification consequence** | `excess` becomes **732 samples** because the plan's per-window shift indexing is part of the apply contract. **Not an audio defect** — a verifier must compute its expectation from the shift actually applied at that sample |

### B3 `sample-domain` — **not a candidate**

Identical to B1. Measurement windows are 30 s on a 10 s hop, so three cover any
sample and the largest covering index is `pos // hop`. P4 **refuses** it when
`window < hop`, because then no window may cover an earlier sample. Reporting it
as a third option would be reporting a duplicate.

### B4 What the measured evidence says

```text
SAYS:      the two mappings differ by at most ONE window index
SAYS:      one index is one hop = 10 s of position in the shift trajectory
SAYS:      index agreement 0.21 % / 0.24 %; VALUE agreement 56.94 % / 36.37 %
           - two different questions, neither substituting for the other
SAYS:      the choice moves up to 732 / 211 samples of applied shift and up to
           127 200 000 output samples
SAYS:      P4 reproduces BOTH contracts byte-for-byte, so switching is a
           declared parameter change, not a rewrite
DOES NOT:  say which direction of error is preferable
```

> **`DECISION_REQUIRED: D2`** — *should a correction anticipate a change, or lag
> it?* **Owner: product + engineering jointly.** It is a timing-semantics
> decision, and neither direction is preferable on the evidence.

---

## 4. ENABLEMENT

| option | what it means | P4 status |
|---|---|---|
| no time-varying path in production | today's observable behaviour | **superseded by building the capability, with its observable consequence preserved exactly** |
| path exists, **default OFF**, explicit opt-in | the module is tracked and importable, nothing calls it | **BUILT** |
| default ON | runs for every eligible job | **REQUIRED, not taken** |

```text
the default path is unchanged because no production module imports the new one
- asserted by test, not promised
```

`HANDOFF-CHECKLIST.md` §1 item 1 notes: *"If it is ever wired in, this item
becomes live."* **P4 did not wire it in, so no flag is needed and none was
added.** A runtime flag can be switched on by configuration or a mistake; code
that nothing imports cannot run.

### 4.1 What enabling would require, in order

```text
1.  D5  - what a correction may claim            -> shapes the API
2.  D6  - is UNRESOLVED_GAP representable?       -> can remove A4 from D1
3.  D1  - which gap policy ships                 -> selects the fill rule
4.  D2  - which coordinate contract              -> selects the join
5.  D3  - residual acceptance                    -> sets any gate
6.  the wiring: one call site, one place decides the shift, inside the try
7.  the flag: default OFF, plus an audit that the OFF path is byte-identical
8.  a live pipeline measurement (decode + measure + correct + encode)
9.  a full regression, including a real --channel-sync run
10. D8  - enablement                             -> the only step that changes
                                                    production behaviour
```

**Steps 6–9 are engineering work that cannot start meaningfully before 1–5,
because 1–5 determine what is being built.**

---

## 5. The residual acceptance recommendation

```text
RECOMMENDED (engineering):  adopt the structural gate now
                            (A1-A8 invariants + N1-N6 no-silent-error rules)
NOT RECOMMENDED:            inventing a scalar threshold
NOT RECOMMENDED:            setting one on the unfiltered population, which
                            contains measurements the estimator refused
NOT RECOMMENDED:            setting one across both corpora without naming it,
                            because the per-pair totals differ by more than 10x
```

**Why the structural gate is recommendable now and the scalar is not:** the gate
tests properties that are true or false independently of what the audio is for —
apply mechanics, state honesty, chunk invariance, loud failure. A threshold tests
a property that **depends entirely on what the audio is for**, which is `D5`.

See `residual-acceptance.md` §4 for the constraints any future threshold must
satisfy and §5.2 for the five experiments (R1–R5) that would establish one.

---

## 6. Recommendation summary

```text
Engineering recommendation:
    - implement the contract as built: value and state separate,
      state derived from the mask, context demanded, plan self-validating
    - keep it OFF until D1 / D2 / D5 are answered
    - keep P1 exactly as shipped
    - adopt the structural acceptance gate now
    - do not invent a residual threshold
    - do not re-open the closed research questions
    - measure the pipeline before promising anything about speed

Product decision:
    D1  gap policy                         REQUIRED
    D2  coordinate contract                REQUIRED
    D3  residual acceptance                REQUIRED
    D5  what a correction may claim        REQUIRED  (shapes D1)
    D6  downstream representability        REQUIRED  (can remove an option)
    D8  enablement                         REQUIRED  (last, and the only one
                                                      that changes behaviour)
```

**No product decision is taken here.** P4's endpoint without those answers is

```text
PRODUCTION IMPLEMENTATION = READY FOR POLICY APPROVAL
production default enablement = DEFERRED
```

which `P4-HANDOFF.md` and the brief's §40 both name as a legal and expected final
state.
