# Audio Sync P3 — Production Requirements

**Every requirement below is derived from research that already exists in this repository. None
was invented for this document. Each carries the phase that forces it.**

This is a requirements document, not a design and not a plan to integrate. It does not propose
a policy, rank a policy, or recommend enabling anything. Where the research cannot support a
requirement, the requirement is **omitted** and its omission is stated in the closing section
rather than dressed up as something that *can* be sourced.

Standing context that every requirement inherits:

```text
production = NOT READY        (unchanged by every phase P3.1-P3.8)
correction enabled by default = NO
a plan has never been applied to real audio and re-measured = UNKNOWN  (P3.8)
```

---

## 1. Explicit uncertainty

### Requirement U1 — a production object must be able to express `TRUSTED`, `UNRESOLVED_GAP` and `REENTRY`

**Forced by: P3.7.** Reproduced and extended at the implementation level by **P3.8**.

**A bare `shift_samples` is insufficient.** The production object must carry **two separate
things** per window:

```text
shift_samples   the integer correction applied
state           the epistemic claim: TRUSTED / UNRESOLVED_GAP / REENTRY
```

The three states, as defined in `docs/audio-sync-p3/safe-gap-design.md` §2.1:

| state | meaning | allowed | forbidden | output semantics |
|---|---|---|---|---|
| `TRUSTED` | the window's own measurement was accepted | apply the window's own `rint(delay)` | inventing a neighbouring value | resolved measurement |
| `UNRESOLVED_GAP` | the window was refused; the shift is a policy decision, not a measurement | hold / fill 0 / interpolate-under-stated-assumption | reporting `resolved = True` | **explicit unresolved** |
| `REENTRY` | the first accepted window after a gap; where the gap decision meets reality | resume applying measurements | retroactively amending the gap | resolved measurement, boundary flagged |

**The failure mode this requirement exists to eliminate: `number exists == measurement exists`.**

The number being present is not evidence that a measurement exists, and the research has this
three times over:

- **The estimator sets `valid` and leaves `delay_samples` populated.** A refusal is a **flag on
  a finished measurement**, not a suppression of one
  (`docs/audio-sync-p3/rejected-window-measurement.md` §1). A caller that reads
  `delay_samples` without checking `valid` therefore gets a plausible number.
- **The plausible numbers are inside the true range.** At a change the windows that straddle it
  are refused, and the numbers they would have reported — `0.1346` and `960.0642` on the step
  fixture — both sit inside `[0, 960]`. `rint(0.1346) = 0` and `rint(960.0642) = 960`, so **one
  of the two would have matched the right answer by accident**. What matters is that neither
  reached the plan, because `valid` was honoured (`work/p3/p38/reports/critical-evidence-closure.md`
  §B2; `docs/audio-sync-p3/production-semantics.md` §1.6).
- **A refusal has a shape, and it is not uniform.** A window refused *before* the estimator ran
  carries `None`; one refused *after* it ran carries a number and `valid=False`
  (`3000.0006` edge-pinned, `0.1346` intra-window-unstable). `WindowMeasurement` and
  `WindowRecord` **are not interchangeable**, and the conversion raises `KeyError` rather than
  silently reading a missing field as absent (`critical-evidence-closure.md` §B2a).

**The requirement is derived from measurement, not from caution.** P3.7 showed that the applied
correction can be **byte-identical** between two policies while the epistemic claim differs —
`hold` and `current` produce identical shift sequences across a gap containing a genuine
174-sample delay change and differ **only** in what they label those windows. That is why the
two dimensions are **separate frozen fields, not one nullable number**.

Concrete properties the object must have, all of them already implemented and tested in
`work/p3/p38/semantics/gap_semantics.py`:

| property | why | source |
|---|---|---|
| `shift_samples` and `state` are **separate fields** | the two policies above are both constructible and both meaningful | P3.8 §2 |
| **no sentinel** — a gap decision carries an explicit state, never `shift = None` | a nullable number cannot distinguish "0-shift measurement" from "no measurement" | P3.8 §2 |
| `REENTRY` is **its own state** | a caller is never left to infer what happened at a boundary | P3.8 §2 |
| a `measurement`-sourced decision that claims `UNRESOLVED_GAP` **raises an invariant error** | makes the contradiction unrepresentable rather than merely documented | P3.8 §2 (`gap_semantics.py`, `CorrectionDecision.__post_init__`) |
| the state sequence is a **pure derivation of the resolved mask**, re-derived in the plan constructor, which **refuses to exist** if the supplied states disagree | an inconsistent plan cannot be constructed | P3.8 §3 |
| the epistemic state is **reported separately from the shift** | "the two dimensions are never merged" | `work/p3/p38/coordinator/implementation-handoff.md` §3 step 4 |

### Requirement U2 — do not let a policy claim more knowledge than the evidence supports

**Forced by: P3.7.**

The precise form of the finding, which must not be overstated
(`docs/audio-sync-p3/safe-gap-design.md` §4.0, 132-case known-truth matrix):

| check | `current` |
|---|---|
| state sequence matches the mask-derived expectation | **132/132 correct** |
| never labels a refused window `TRUSTED` | **132/132 correct** |
| `resolved` flags backed by a measurement | **1/22** (only the no-gap case) |

**`current`'s state machine is `CORRECT` and no fake `TRUSTED` window occurs in any case.**
What fails is the separate `resolved` claim, which over-claims on **1 of 22**. The accurate
statement, in the research's own words, is:

> **`current` claims more knowledge than the evidence supports** — a **semantic over-claim, not
> an algorithmic error**, and not a claim that `current` is unsafe in every respect.

That table and that sentence are the required reading for this requirement: **P3.7 §4.0**,
`docs/audio-sync-p3/safe-gap-design.md`.

Invariant **I2** is the requirement in testable form: *"a window without a trusted measurement
must not be reported as resolved."* **`current` FAILS it on all four real plans; all candidates
PASS it.** On the real corpora the consequence is quantified — **P3.8 §7.1**,
`docs/audio-sync-p3/production-semantics.md`: `current` reports all **231 / 931 / 106 / 422**
gap windows as resolved measurements, while candidates report the same windows unresolved, and
`hold` and `current` differ in applied shift on only **2 / 8 / 2 / 8** windows (0.4–0.5 %) —
**the leading gap only.** Everywhere else the two apply **identical corrections** while making
**different knowledge claims**.

---

## 2. Gap policy contract

### Requirement G1 — the input is a measurement sequence plus a valid mask; the output is a shift sequence plus a state sequence plus provenance

**Forced by: P3.7** (the semantics) and **P3.8** (the executable form).

```text
IN   : measurement sequence (per-window records with start_sample, delay_samples, valid, reason)
       + the valid mask, DERIVED from measurement validity rather than supplied
OUT  : shift sequence        (what is applied)
       + state sequence      (what is claimed)
       + provenance          (where each shift came from, and what the plan was built under)
```

**The mask must be derived, not accepted as an argument.** `critical-evidence-closure.md` §B2
records this as a property test rather than a convention: the mask is derived from measurement
validity, asserted directly **and by mutating `valid` and observing the mask change**.

**Provenance must be part of the object, not a log line beside it.** The plan type carries the
policy name, the shifts, the resolved mask the states were derived from ("kept so the derivation
is auditable rather than implicit"), the window count, the version, `requires_future`,
`offline_only`, and the gap runs (`gap_semantics.py::CorrectionPlan`).

**The plan length is checked for equality with the required window count, not merely a lower
bound.** The reason is a measured defect, not tidiness: treating `hop_samples` and
`window_samples` as one quantity built a 64-entry plan for a 4-window stream and indexed past
its end — in numpy an **out-of-bounds read that returns adjacent memory rather than raising**.
The symptom was a debug print showing `shift_arr[16] == 1` while the loop read `shift_arr[1] == 0`
(`production-semantics.md` §6.1).

**This section's two prohibitions follow directly from the two defects below.**

### Requirement G2 — inside a gap the correct word is **not measured**, never **estimated**

**Forced by: P3.6** (the evidence) and **P3.7, P3.8** (the semantics).

P3.6 established that the in-gap error is **not observable**: any policy commits to a value the
data cannot check, so an unresolved-gap correction cannot be *verified* in the field — only
bounded by assumption. **A design that assumes its gap policy is correct because it validates
well on the corpus is validating the wrong thing.** Raising measurement precision does nothing:
sweeping σ_meas over 0.0 / 0.76 / 2.0 moves the in-gap error by a few samples out of 100–1 900.
The obstacle is **missing information, not noisy information**.

Therefore, in any interface, report or schema that surfaces a gap fill:

| forbidden | required |
|---|---|
| "estimated delay" for a gap window | **"not measured"** |
| a number presented as a measurement | **explicit unresolved**, with the policy that produced the number named |
| a `resolved = True` flag on a window with no accepted measurement | `resolved = False`, `state = UNRESOLVED_GAP` |

**A policy that never emits the unresolved state is claiming precision it does not have.** In
P3.7's own words, `resolved=False` "is not an error state — it is the honest state".

**And the requirement is bounded so it does not become an excuse for refusing everything.** It
is scoped to gaps whose endpoints *exist*: on a stream with no accepted window at all there is
nothing to interpolate between and no endpoint is missing (`production-semantics.md` §6.4).

### Requirement G3 — the streaming contract, stated exactly

**Forced by: P3.8.**

```text
out[n] = in[n + shift(window_of(n))]      zero-filled where the read leaves the stream
```

- sample values copied **bit-for-bit** — no filtering, no interpolation, no arithmetic;
- output length == input length;
- **byte-identical across `chunk_windows`**;
- **peak memory one output chunk**, not one output stream;
- out-of-range reads zero-filled;
- chunks split internally at window boundaries.

**This is the contract, and a clamp that contradicts it must be reverted even when it looks
principled.** A source-window-bounded clamp — "a window should only place samples it covers" —
was written, looked defensible, and made the two paths disagree at window boundaries: measured
**40 differing samples on a 60 s stream**. It is now a regression in the selftest
(`production-semantics.md` §5.3).

### Requirement G4 — required context must be **present**, not merely available in principle

**Forced by: P3.7** (`shifts[-1]`) and **P3.8** (§6.4, §6.5, §6.6).

The bounded context a streaming implementation genuinely needs, per stream and per gap:

```text
the stream's accepted mask             (or: is a trusted window ahead / behind?)
the current gap's two endpoint VALUES  (2 integers)
the current gap's current global EXTENT (2 integers)
```

Every quantity is bounded — no whole-stream dependency — so a streaming implementation is
possible, **but only if the context is explicitly supplied. A chunk cannot infer it.**

| forbidden | required |
|---|---|
| `shifts[-1]` to mean "the previous window's shift" | the carry value passed as a **named argument** |
| a chunk-local fallback for anything stream-level | **raise** when the required context is absent |
| a truncated mask read as if it spanned the stream | a declared `stream_end`, and a `stream_has_trust` that is **demanded rather than defaulted** |

**The rule, stated as behaviour rather than as documentation, is P3.7's: *required context must
be present, not merely available in principle.*** `GapContext` exists so that a gap carries
endpoint values and stream-relative facts **as arguments** rather than by peeking at
neighbouring windows. **A caller that cannot supply them gets a refusal — which is the point.**

**The scope is precise, and the precision matters.** The guard is scoped to gaps whose
endpoints exist, and it is **precisely about chunks**: whole-stream decisions populate both maps
themselves from the shifts they have just computed, so a caller deciding the full stream is not
asked for context it can already see. Two guard versions written on the way to this were **too
broad and fired on legitimate leading and trailing gaps**, which is why the rule is now stated
as *"the mask is authoritative where it reaches; where it does not reach, `stream_end` decides
whether that silence is the stream's end or a slice"*, rather than *"a missing neighbour is an
error"* (`production-semantics.md` §6.6).

---

## 3. Context requirement

### Requirement C1 — anything needing context must have it passed explicitly

**A guess default, a silent fallback, and a chunk-local assumption are all forbidden.**

**Forced by: P3.7** (the `shifts[-1]` wrap) and **P3.8** (§6.4, §6.5, §6.6) — the defect family,
stated once in `production-semantics.md` §6.7:

> **A chunk silently substituting a guess for context it cannot see.**

Every instance produced a plausible number and no exception. **The fix is never a better
default — it is to make the required context explicit and mandatory, so absence raises.**

### The four instances of this defect family, and where each one bit

| # | instance | where it bit | what it produced |
|---|---|---|---|
| 1 | **`shifts[-1]`** | a chunk beginning inside a gap reading `shift[a-1]` at local index 0 hits numpy's `shift[-1]`, which resolves to the **last window of the chunk** | a plausible number from the wrong place; P3.7 §5.3 |
| 2 | **the integer overflow** | the magnitude guard ran `int(np.abs(arr).max())` on a plan *after* a signed cast | **audio returned aligned 5 samples the WRONG WAY, no error, no warning** — indistinguishable from a correct result; `production-semantics.md` §6.5 |
| 3 | **the missing gap endpoint** | a chunk lying inside a gap, with no global gap extent supplied, fell back to the **chunk-local** extent | `[100, 100, 100, 100]` where the truth was **`[167, 233, 300, 367]`** — silently wrong, no error; `production-semantics.md` §6.4 |
| 4 | **the truncated context mask** | `is_trusted()` returned `False` both for "this window was refused" and for "this window is outside the mask I was given" | `[0]` where the truth was **`[400]`** — a plausible number from a window it cannot see; `production-semantics.md` §6.6 |

Instance 3 **recurred immediately after being fixed once**: testing the extent fix, the **same
defect appeared for the gap endpoint values** — a chunk that does not itself hold a gap's
bounding trusted windows was reading a chunk-local *interior* window as if it were an endpoint.
It now raises unless the endpoints are supplied or genuinely in view.

Instance 4's blast radius was measured, not assumed: confined to the two **offline-only**
policies (`linear`, `next`); the four carry-style policies were **exact at every look-ahead
radius**.

### The evidence that this family is closed, and its scope

**`work/p3/p38/failure_injection.py` — 30 cases, 0 silent-wrong**, outcome split recorded in
`work/p3/p38/reports/failure_injection.json`:

| outcome | count |
|---|---|
| `VALUE_OK` — no error, correct value | 13 |
| `RAISED` — the expected error type | 17 |
| `SILENT_WRONG` — no error, wrong value | **0** |
| `UNEXPECTED` — wrong error type | **0** |

**Every case asserts the resulting value, never merely "did not raise"** — which is the standard
this requirement is written to. The covered surface includes: missing left endpoint, missing
right endpoint, chunk inside a gap with and without context, empty gap, single-window gap,
all-refused stream, mask/record length mismatch, negative context base, NaN and Inf delays, NaN
and non-integer and 2-D and absurd-magnitude shift plans, integral float plans, zero-length
plans, plan too short, plan too long, exact plan, hop not dividing the stream, empty stream,
non-positive `window_samples` / `hop_samples` / `sample_rate`, a plan whose states disagree with
its mask, unknown policy, and shifts large enough to push every read out of range.

**The scope limits of that evidence, which must travel with it:**

- **A suite that passes is not a suite that is complete.** P3.8 records that the earlier *"30
  cases, zero silent-wrong"* did **not** cover integer-overflow boundaries, a defaulted
  `GapContext.base`, or `window < hop`.
- **Three of the four instances were found by an adversarial reader, not by the module's own
  tests.**
- **The verification code was itself untrustworthy three times** (F9, F10, F11): a selftest that
  exited 0 without testing, a memory check whose every case errored and whose verdict fell
  through to `UNKNOWN` while the script **exited 0**, and a baseline that made the control
  **unable to fail**. All three share one shape: **a check that reports success while measuring
  nothing.**

### Requirement C2 — validate the shift range **before** any signed cast, and **without `np.abs`**

**Forced by: P3.8** §6.5 — the single most serious defect of the line, because the result is
indistinguishable from a correct one.

```text
np.array([2**64 - 5], dtype=np.uint64)
    -> .astype(int64) WRAPS to -5   ->  |−5| = 5  ->  passes the guard
    -> the stream is returned aligned 5 samples the WRONG WAY, no error, no warning

np.array([-2**63], dtype=int64)          # INT64_MIN
    -> np.abs(INT64_MIN) is itself NEGATIVE (two's-complement overflow)
    -> the comparison can never be true  ->  passes

np.array([1e30])                         # float path
    -> .astype(int64) is undefined       ->  silently becomes INT64_MIN
```

Required: a float plan is range-checked **as float**; an unsigned plan is range-checked **as
unsigned** (it cannot be negative); a signed plan gets **explicit lower and upper bounds**. All
three cases must raise, and the exact boundary must be verified (±2³¹ accepted, ±2³¹+1 raises).

### Requirement C3 — a truncated record set must be **declared, or raise**

**Forced by: P3.8** §6.6. A truncated mask was **indistinguishable from a refusal**, so a chunk
treated a window it could not see exactly as one it knew to be refused. This is the same
requirement as G4 seen from the caller's side; it is repeated here because it is an input
contract rather than an internal one.

---

## 4. Cross-cutting requirements forced by the research

These are not a fourth topic. They are requirements the three sections above cannot be
implemented without, and each has a phase behind it.

| # | requirement | forced by | what it prevents |
|---|---|---|---|
| X1 | a feature flag with **default OFF**, and an audit showing the OFF path is **byte-identical** | P3.8 §4 | shipping a correction whose policy is undecided |
| X2 | `window_samples` and `hop_samples` passed as **separate** parameters from **named constants** | P3.8 §6.1 | the out-of-bounds read that returned adjacent memory |
| X3 | **one place decides the shift, and it is inside the `try`** | `implementation-handoff.md` §1 | `int(np.rint(nan))` raising `ValueError` outside the `try` at line 844, so the single-track fallback cannot catch it |
| X4 | the stream object is **passed through, never materialised** | P3.8 §5.2 | a silent **173×** slowdown from `np.asarray` walking the sequence protocol — correct values, no error |
| X5 | chunk-size invariance asserted at the **byte** level, at ≥3 chunk sizes, **including one that puts a chunk boundary inside a segment** | `implementation-handoff.md` §4 | a correlation-based check passing a **40-sample** disagreement |
| X6 | every fixture has a **zero case**, and each fixture is shown to **discriminate** | `implementation-handoff.md` §4 | three "different" fixtures were once bit-identical, and **298 stale results were silently reused** |
| X7 | the P1 guard is re-run | P1, P2 | the constant-offset path regressing; guard **27 PASS / 0 FAIL** |
| X8 | memory re-measured with a **positive control that actually allocates** | P3.8 F10, F11 | a memory check that **measures nothing** reporting PASS three separate times in one phase |
| X9 | an operational **rollback tested as a path**, not assumed | `implementation-handoff.md` §4 | a revert that exists only in the design |
| X10 | no threshold, fixture or validity criterion adjusted to make a corpus pass | the whole line | the standing rule, enforced in every phase |
| X11 | any claim that a correction is valid must survive an instrument **sharing no code** with the one that produced it | P3, P3.2 | a self-fulfilling measurement at the plan's own geometry |
| X12 | `core/` must be re-verified as **unmodified** rather than assumed | P3.9 repository state | the "core untouched" premise of the P3 handoff being false in the working tree |

**X11 in its measured form.** P3's prototype failed its own verification: the main instrument
reported span ratio **1.000** and RMS **1.006**; the structurally independent integer
cross-correlation reported **0.830**. No improvement from either, from a rewrite of **100 %** of
the samples. In P3.2, both instruments agreed the span **widened** (1.1348 and 1.5116 against
1.1861 and 1.5634) while RMS and span disagreed on direction — and the outcome is classified
`MIXED`, not from the more flattering of the two.

**X12 in its measured form is in `HANDOFF-CHECKLIST.md`**, because it is a verification step
rather than a design requirement.

---

## 5. Requirements the research cannot yet justify

**This section is deliberately not empty, and it is deliberately short. Everything listed here
was considered and could not be sourced, so it is stated as omitted rather than softened into
something that can be.**

| tempting requirement | why it is **omitted** |
|---|---|
| "a gap policy must not be applied across gaps longer than *N* seconds" | **P3.6 §5 rejected the instrument.** C1306 has 175–180 s gaps and never exceeds a **79**-sample shipped-policy jump; C1305 has a 530 s gap and reaches **670**. **Gap duration does not predict jump size** — the predictor is how far the delay moved while the policy was blind, which is exactly the quantity no measurement can supply. A duration threshold would threshold the **wrong variable**, so no such requirement can be written. |
| "cap the shift change at a boundary to a bounded step" | **No phase measured what a bound should be, and P3.6 measured that the boundary step is not a policy quantity.** Its magnitude is set by the accepted windows on either side; the metric that would have tested a policy's contribution to it (`policy_jump` / `artificial_jump`) is **policy-invariant**: **0 of 8 136 policy-pairs differ**, maximum observed **5.0 samples**, and **0 / 0 / 0 / 0** events over 50 / 100 / 200 / 500-sample thresholds. **Switching gap policy would not remove the 670 / 664 / 180 / 118-sample real boundary jumps.** There is no sourced basis for a cap value. |
| "the correction must reduce the residual span by *X* %" | **The correction does not reduce it.** P3's prototype: **no improvement** (span ratio 1.000 / 0.830). P3.2: C1305 **MIXED** (RMS −12 %, span **+19 %**); C1306 **no measurable effect** (1.0000 / 1.0000). There is no measured improvement to set a threshold against. |
| "a per-window uncertainty must be reported with the shift" | **The uncertainty that exists is not an error bar.** `uncertainty_samples = max(0.3, 0.5·peak_width/√peak_over_noise)` is a **physical bound, not a calibrated σ**; the fitted residual sd (11.07 samples) exceeds the probe scatter median (1.95 samples) by **5.7×**, so it contains structure the probe does not see. **`UNCERTAINTY CALIBRATION = PARTIAL`** and weighted fitting on it is **not justified**. A requirement to report a calibrated σ would require a measurement nobody has taken (a known-truth repeat at several bandwidths). |
| "the 15 s attribution offset must be reduced below one hop" | **Not a correction decision.** Reducing it means a shorter window or a denser hop, and **both change the measurement's own validity population** — which was the subject of P3.2 and P3.5. It is recorded as an `OBSERVED SEMANTIC MISMATCH` and accepted, not resolved. |
| "an `UNRESOLVED_GAP` must be propagated to the downstream consumer" | **Depends on consumers outside this repository, and nobody has looked at them.** `OPEN-DECISIONS.md` D6: if a downstream consumer can only accept a number per sample, `explicit_state_as_output` is **unavailable regardless of its merits**. The requirement cannot be written until that is known. |
| "pick a policy and default to it" | **Explicitly out of scope for this research line.** `OPEN-DECISIONS.md` D1 is `OPEN` and deliberately not taken; it is a product trade-off between uncorrected alignment and invented structure, and **research established that the metric which would have ranked the policies cannot see a difference between them** (0 of 8 136 pairs). |
| "enable the correction behind the flag for a trial" | **Nothing in the research licenses it.** No plan has ever been applied to real audio and re-measured (`UNKNOWN`), end-to-end cost including decode and encode is `UNKNOWN`, and 19–51.7 % window validity means most of the timeline would be left unmeasured **while the rest is rewritten**. |

**Also omitted, and it is the largest omission:** *any* requirement stating that the correction
improves alignment on real material. The research does not support it on either corpus — C1305
`MIXED`, C1306 no measurable effect — and P3's own verdict is `P3 NOT JUSTIFIED` at this
resolution. A requirements document that asserted it would be the single worst defect available
here.

---

## 6. What is decided, and what is not

Quoted from `OPEN-DECISIONS.md`, so this document is not misread as "everything is undecided".

**Not open — decided:**

| decision | status | basis |
|---|---|---|
| fold the P3 time-varying correction into the P1 constant path | **DECIDED — no** | P3.2 correction `NOT VALIDATED`; P1 stays as shipped |
| enable any P3 correction by default | **DECIDED — no** | no flag exists; the module is unreachable from `1kt.py` |
| fractional (sub-sample) correction | **DECIDED — not needed** | P3.6 |
| resampling-based correction | **DECIDED — not justified** | P3.6 |
| treat the corrected output as validated on real audio | **DECIDED — no** | it has never been applied to real audio |
| re-open the P3.4 "gap policy is the dominant residual source" finding | **DECIDED — no, it stands** | direct same-axis evidence; only its *supporting inference* was rejected |

**Open — and no requirement here depends on a resolution:**

| id | decision | blocks |
|---|---|---|
| D1 | which safe-gap policy ships | **any integration whatsoever**; there is no default policy in production |
| D2 | the `window_of` convention (latest-start vs nearest-centre) | the meaning of every historical plan; **changing it silently alters every P3.3–P3.8 real-corpus number** |
| D3 | the 15 s attribution offset | nothing mechanically; it bounds the accuracy of any claim the correction makes |
| D4 | is the correction worth its end-to-end cost | any claim that this is deployable, interactive or batch-feasible |
| D5 | what a correction is allowed to *claim* | the shape of the production API |
| D6 | whether an `UNRESOLVED_GAP` is representable downstream | `explicit_state_as_output` entirely |
| D7 | whether the implementation gets a durable home | the reproducibility of every P3 result, eventually |

**Nothing in sections 1–4 requires D1 or D2 to be resolved first.** That is not a claim that
they are unimportant; it is a statement about the requirements as written. D1 blocks
*integration*, not *specification*, and `HANDOFF-CHECKLIST.md` records it as a precondition a
reviewer must see closed before an integration is reviewed.
