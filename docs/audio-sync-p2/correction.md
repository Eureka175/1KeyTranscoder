# Audio Sync P2 — Correction

> The correction prototype, what it verifiably does, and why the project does not
> apply it. Prototype: `tools/p2/correction/`. **Not imported by the shipped
> program; no production code is modified.**
>
> Sources: Agent E's strategy research (`work/p2/agent-e-correction.md`), the
> prototype's own self-test run directly by the main agent, Agent I's decision
> outputs in `work/p2/out/agent_i_*.json`, and Agent J's independent-geometry
> validation (`validation.md`).

---

## 1. The correction ladder, and which rungs the evidence justifies

| rung | strategy | justified when | measured status |
|---|---|---|---|
| **0** | `untouched` / `drift_uncertain` | evidence insufficient | **the outcome actually taken on this corpus** |
| **1** | single integer shift (P1, unchanged) | the trajectory is constant | **exact** — −348 dB against the ideal aligned result |
| **2** | event-anchored piecewise integer shift | the trajectory is stepwise with detectable events | **exact** for stepwise trajectories at 10 s / 1 s / 100 ms blocks |
| 3 | bounded-phase fractional (static, 9-tap polyphase) | a sub-sample residual matters — **only if tracks are summed** | the tracks are **never summed** (four independent `soun` tracks), so **not needed** |
| 3v | continuously varying phase | a smooth monotone trajectory is proven | **not justified** — no smooth trajectory found |
| 4 | phase vocoder / WSOLA | never here | **forbidden** — decouples pitch from duration, which an SRO couples; and there is no SRO |

**No resampling.** A genuine sampling-rate offset would require it, and there is
none: a linear fit is worse than a constant for `CH1↔CH4`, and `D(τ)` log-log slope
is 0.09–0.43 against the ≈2 an SRO gives.

**The brief's premise that resampling must change the sample count is false** —
`y[n] = x(n − d(n))` with a continuous `d(n)` achieves SRO correction with sample
count, `stts`, `mdhd` and `tkhd` untouched. And `preservation/validate.py:291-301`
compares per-track `stts` sums with **exact equality**, so a sample-count change
would fail `--check basic` regardless.

## 2. What the prototype verifiably does

Its known-answer self-test passes in full, and it **aborts before touching real
audio** if any check fails. Verified by running it:

```
[PASS] identity          zero plan -> 288000 samples byte-identical (sha 818e9503d633)
[PASS] rung1_exact       constant shift == out[n]=in[n+r] bit-exactly, r in
                         {-777,-1,0,1,5,960,2400} x chunk in {0.25,1,60}s, 0 differing samples
[PASS] rung2_exact       2-event stepwise plan, seams at 1.5 s and 3.7 s crossing a
                         0.7 s chunk: bit-exact vs ideal, 0 differing samples
[PASS] zerofill          out-of-range reads zero-filled at both ends, no wrap-around
[PASS] count             output sample count == input for every plan shape and chunk size
[PASS] sign d=-137/+211/-1855   positive = target later, convention
                         out[n] = in[n+delay] verified against core/sync_fix.shift_stream
[PASS] gcc d in {0,±137,+1855,-1750,+2400}   measured exactly, err 0.0
[PASS] phase d in {±0.05..±0.37, +12.37, +137.37}  residual exact to 0.0000 sample, r2 1.0000
[PASS] tracker d=±600    median exact over 199/199 usable frames
selftest: ALL PASS
```

Two properties deserve emphasis:

* **Bit-exactness on identity input** — a zero plan reproduces the input
  byte-for-byte (SHA256 verified). This is the transparency guarantee the shipped
  P1 also makes, and it is the property that must never regress.
* **The prototype is aware of the loudest artefact in the study.** Its
  `onesample_flip` test confirms a 1-sample boundary flip is **+12.00 dB** above
  signal RMS at the file's largest 2-sample difference — consistent with Agent E's
  independent measurement of **+13.7 dB** on music-like material. The mechanism is
  identical; the magnitude is set by the local waveform.

## 3. What the prototype decided on the corpus

Both C1305 pairs were **refused**:

| pair | rung | action | reason | confidence |
|---|---|---|---|---|
| `ch1-ch2` | 0 | `untouched` | `p2_needs_resampling_not_supported` | 0.0 |
| `ch3-ch4` | 0 | `untouched` | `p2_needs_resampling_not_supported` | 0.0 |

**The action is right and the reason is wrong.**

* *Right action:* refusing is correct on this material, for the reasons in
  `investigation.md` §6.3.
* *Wrong reason:* the project established that **no sampling-rate offset exists**
  (`investigation.md` §5), so "needs resampling" is a category error here. The
  correct reason is that the delay is **not a well-defined scalar** — the mobile
  pairs carry multiple arrivals whose winner flips between windows.

The same reason was returned for **both** a rigid pair and a moving pair, which is
itself a defect: the two have entirely different evidence behind them and should not
share a justification. This is a concrete, fixable reporting bug — and it matters,
because the reason string is what a human reads to decide whether to trust the
result.

## 4. The decisive negative result

**Applying rung 2 with a per-window plan makes the material worse on any grid the
plan did not see** (Agent J, `validation.md` §1):

| file | pair | before 120 s | **after 120 s** | before 30 s | **after 30 s** | after/before |
|---|---|---|---|---|---|---|
| C1305 | ch3-ch4 | 26.250 | **0.000** | 26.417 | **42.917** | **1.62×** |
| C1305 | ch1-ch3 | 15.438 | **0.000** | 17.729 | **25.021** | 1.41× |
| C1306 | ch3-ch4 | 2.708 | **0.000** | 18.833 | **18.833** | **1.00× (no gain)** |

The "0.000 ms" exists **only at the plan's own geometry**. At a 4× finer grid the
identical output carries 0.92×–1.65× the original residual span, with boundary
jumps up to **+14.2 dB**.

For C1306's `ch3-ch4` the movement the correction set out to remove was almost
entirely an artefact of 120 s smoothing: the real movement is 18.833 ms before and
18.833 ms after. **The work removed nothing and added boundary steps.**

**This is why no correction ships.** It is not that the fixer is broken — §2 shows
it is bit-exact and correctly signed. It is that **the plan is built from a
measurement that does not hold up on an independent geometry.**

### 4.1 What the negative result does and does not establish (round 9)

Investigating an apparent disagreement with Agent H (which classified C1306
`ch3-ch4` as correctable) produced a sharper attribution:

* H and J measured **identical** before-data (median 2.8542 ms, span 2.7083 ms, same
  120 s geometry) — so they disagree on the decision, not the measurement;
* the plan J derived models **2.708 ms over 18 boundaries**, while J's own 30 s
  reference measures **18.833 ms** of movement on the same material.

**So the plan could not see the movement it was meant to remove**: it was built at
120 s resolution. The correct statement is therefore

> **A per-window integer-shift plan built on a 120 s grid leaves sub-grid movement
> uncorrected, so the residual is unchanged or worse on a finer grid.**

and **not** "per-window integer shifting cannot work on this material" — that is
**not established**. See `validation.md` §1.1.

### 4.2 Two separate defects, reported as found

* **Agent I's independent tracker** disagrees with the validated reference on
  **15 of 40 windows, max 1143 samples** (`agent_i_crosscheck_c1305.json`). It is
  therefore **not a trustworthy plan source** — though it did *not* cause the
  negative result above, since that plan came from Agent J's own corrector.
* **The refusal reason is wrong and uninformative.** Both C1305 pairs return
  `p2_needs_resampling_not_supported`, and a rigid pair and a moving pair receive
  the **same** reason. There is no SRO in this material, so the reason is a category
  error, and collapsing two different evidence situations into one string removes
  exactly the information a reader needs.

## 5. The integration traps, all verified in source

1. **Never introduce a new `decision` value.** `core/audio_models.py:2111`
   `_row_status` recognises only `fixed`, `already_aligned`, `anchor`, `untouched`
   (plus six legacy reasons); anything else falls through to
   `SyncStatus.NOT_PROCESSED`. Agent H's rule emits
   `decision = "p2_piecewise_integer"`, which would report a correctly aligned
   track as *not processed*. Keep `decision = "fixed"` and carry P2 meaning in the
   **reason** code.
2. **Do not add `p2_*` reasons to `_ESTIMATION_FAIL_REASONS`**
   (`core/channel_sync.py:133-136`) — one refused track would drag the whole file
   to `measure_failed`, breaking the per-track independent-failure contract.
3. **Leave `P2Drift.offset_samples = None`.** `core/audio_timeline.py` accepts only
   a single integer offset, so a floating time-varying value would be `rint`-ed and
   silently degrade P2 to P1 — the exact "looks corrected but is not" failure the
   brief forbids.
4. **Sign convention.** The prototype applies `out[n] = in[n + r]`, matching
   `core/sync_fix.shift_stream`, and its sign tests confirm
   `positive = target arrives later`. Agent E's resampling formulation uses
   `y[n] = x(n − d(n))`. The two are consistent but expressed oppositely; any
   integration must state which it is using.
5. **The tracks are never summed.** Verified: `channel_sync.py` step 7 writes one
   intermediate file per track, and `preservation/validate.py` asserts four
   independent `soun` tracks. So sub-sample precision is not required for the
   current output shape, and comb filtering cannot occur. If a downmix is ever
   added, rung 3 becomes relevant.
6. **Transparent mode** must keep producing a byte-identical output for
   already-aligned input; a P2 that changes anything must not fire there.

## 6. Assessment against the production gate

| condition | state |
|---|---|
| correction prototype passes its own known-answer tests | **PASS** — bit-exact, correctly signed, sample count preserved |
| correction prototype passes **whole-timeline verification on an independent geometry** | **FAIL** — 0.92×–1.65× the original residual |
| long-duration residual significantly reduced | **FAIL** |
| no audio artefacts | **FAIL** — boundary jumps up to +14.2 dB |
| decision reasons are accurate | **FAIL** — "needs resampling" on material with no SRO |

**The prototype is a sound instrument attached to an unsound plan.** That is worth
stating precisely, because it means the work is not wasted: if a correctable pair is
ever found — one that is rigid and offset beyond `aligned_max_ms` — rungs 1 and 2 are
already implemented, bit-exact, and sign-verified, and only the planner needs
replacing.

## 7. What would change the outcome

* A pair that is **rigid** and **offset beyond tolerance**. None exists in this
  corpus: the only rigid pair is `CH1↔CH2` at 0.074 ms (C1305) / 0.073 ms (C1306),
  inside `aligned_max_ms = 0.05 ms`… by a margin of about 1.5×, so it is
  `already_aligned` and P1 handles it.
* A **validated criterion** for "is this delay well defined". None has passed a
  known-truth negative control yet (see the research record).
* Evidence that the C1305 last-940 s tail is **real movement** rather than a
  measurement failure. That would restore the 26.25 ms figure and materially change
  the picture for that pair.
