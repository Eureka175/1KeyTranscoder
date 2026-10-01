# Audio Sync P3.8 — Executable Gap Semantics

**Status: research implementation. `core/` is NOT modified.**
`production = NOT READY`. `policy choice = DEFERRED`. `production integration = DEFERRED`.

This document records the executable form of the P3.7 design
(`docs/audio-sync-p3/safe-gap-design.md`), the single production insertion point it targets,
and the three silent defects found while building it.

---

## 1. What was built and where

| artifact | location |
| --- | --- |
| semantics engine | `work/p3/p38/semantics/gap_semantics.py` (selftest **94 PASS / 0 FAIL**) |
| architecture audit + integration point | `work/p3/p38/coordinator/architecture-audit.md` |
| frozen-plan reproduction gate | `work/p3/p38/reproduce_frozen.py` → `real/reproduction.json` |

It lives **outside the production tree** on purpose. That is a stronger default-path
guarantee than a feature flag: code that is not wired into `core/` cannot change `core/`'s
behaviour. The P1 constant-offset path, `AudioPlan`, and every existing audio surface remain
byte-for-byte as they were, and `core/` reports no diff.

### 1.1 The integration point

`core/channel_sync.py:839-851` — the per-track correction loop:

```python
for i in to_fix:
    e = used_est.get(i, ests[i])
    shift = int(np.rint(e.delay_samples))
    try:
        sync_fix.shift_stream(arrs[i], fixed_raw[i],
                              delay_samples=e.delay_samples, ...)
```

Every property a time-varying correction needs is already in scope there: the whole source
stream (`arrs[i]`), a per-track destination, the frame trajectory (`e.frames`), per-track
failure isolation (`try/except`), the recheck, and the rollback path
(`rows[i]["decision"] = "untouched"`). A time-varying variant is therefore a **drop-in
replacement at one call site**, with nothing above or beside it needing to change.

`AudioPlan` is deliberately **not** the place for this. `core/audio_models.py` documents it as
*declarative, does not execute* — the boundary between the data model and execution logic.
Putting epistemic state in the plan would cross that boundary and touch the Phase 2/4 contract.

---

## 2. The two dimensions, kept separate

```python
CorrectionDecision(shift_samples=615, state=GapState.TRUSTED)
CorrectionDecision(shift_samples=615, state=GapState.UNRESOLVED_GAP)
```

These are both constructible and both meaningful. P3.7 proved the applied correction can be
**byte-identical** between two policies while the knowledge claim differs, so:

- `shift_samples` and `state` are **separate frozen fields**, not one nullable number;
- there is **no sentinel** — a gap decision carries an explicit state, never `shift = None`;
- `REENTRY` is its own state, so a caller is never left to infer what happened at a boundary;
- a `measurement`-sourced decision that claims `UNRESOLVED_GAP` raises an invariant error.

## 3. State is a derivation, not a per-chunk decision

```python
def derive_states(resolved: np.ndarray) -> np.ndarray:
    # resolved[i] False                  -> UNRESOLVED_GAP
    # resolved[i] True and i-1 was a gap -> REENTRY
    # resolved[i] True otherwise         -> TRUSTED
```

This is the **single** definition of the state sequence, and it is a pure function of the
resolved mask — no chunking, no previous-window access, no mutable cursor. P3.7 showed a
per-chunk state cannot be chunk-invariant, because a one-window chunk cannot know a gap
preceded it; deriving from the mask removes that entire class of bug and keeps chunk
invariance automatic.

`CorrectionPlan` re-derives the states in its own constructor and **refuses to exist** if the
supplied states disagree with the mask, so an inconsistent plan cannot be constructed.

## 4. Bounded, explicit context

A chunk cannot infer stream-level facts, so `GapContext` supplies them and nothing is read
implicitly:

| field | why |
| --- | --- |
| `accepted` mask | leading/trailing classification is a *global* fact |
| `carry_value` / `next_value` | a chunk begins or ends inside a gap and cannot read them from its own records |
| `gap_endpoints` | a chunk ending inside a gap cannot see that gap's right endpoint |
| `gap_extent` | any fill whose weight depends on position *within* the gap needs the gap's global extent |

All bounded — no whole-stream dependency — but it must be **given**. `left_value` and
`right_value` consult these rather than indexing `shifts[-1]`, which in numpy is a **silent
wrap** that returns the chunk's last window: a plausible number from the wrong place.

## 5. Application mirrors production

`apply_shift_plan` reproduces every property of `core/sync_fix.py::shift_stream` that matters:

- sample values copied bit-for-bit — no filtering, no interpolation, no arithmetic;
- one output chunk in memory, so **peak memory is independent of stream duration**;
- **no inter-chunk state**: a sample's shift is a pure function of its window index;
- out-of-range reads zero-filled; output sample count == input count;
- chunks are split internally at window boundaries, so emitted bytes are identical
  regardless of `chunk_windows`.

---

## 6. Silent defects found while building this

All of these produced plausible numbers and no exception. They are recorded because that is
the pattern this line of work keeps hitting.

### 6.1 `hop_samples` conflated with `window_samples`

A window is 30 s wide but advances by the hop (10 s or 2.5 s), so width and stride are
different quantities. Treating them as one meant a 64-entry plan was built for a 4-window
stream and indexed past its end — in numpy an **out-of-bounds read that returns adjacent
memory** rather than raising. The symptom was a debug print showing `shift_arr[16] == 1` while
the loop read `shift_arr[1] == 0`. They are now separate parameters, and the plan length is
checked for **equality** with the required window count rather than merely a lower bound.

### 6.2 The sign convention, resolved by fixture rather than by reading docs

Production's convention (`core/sync_fix.py`, `core/sync_estimate.py`) is:

```text
delay > 0  ==  the target arrives LATE  ==  fix is +rint(delay)
alignment  ==  out[n] = in[n + shift]
```

A plain correlation argmax returns the **negative** of that: for `B[n] = A[n-D]` the argmax
of `sum(a[L:] * b[:n-L])` is `L = -D`. The P3.7 audio demonstration used the raw lag directly
and so reported `-37` for a `+37`-sample delay.

**Its state assertions could not detect this**, because state depends on *which* windows were
refused and not on the sign of their delay. The residuals in that demo were wrong; the state
findings were not. A fixture with both orientations now pins the convention permanently:

| case | true delay | raw argmax | aligning shift |
| --- | ---: | ---: | ---: |
| target late by D | **+137** | −137 | **+137** |
| target early by D | **−137** | +137 | **−137** |

### 6.3 Three wrong expectations in one fixture

Slice arithmetic in the constant-shift test was wrong three separate times, each of which
would have reported correct code as failing. The contract is now asserted per sample —
`out[n] == in[n + shift(window_of(n))]`, zero-filled out of range — because that is the only
formulation that is not itself an arithmetic claim.

---

### 6.4 A chunk with missing context guessed instead of raising — twice

**Found by the failure-injection suite**, and it is the most serious defect of the phases so
far because it produced a **wrong value with no error**.

A chunk lying inside a gap, with no global gap extent supplied, fell back to the
**chunk-local** extent:

```text
whole-stream decision      [167, 233, 300, 367]
chunk-local fallback       [100, 100, 100, 100]     <- silently WRONG, no error
```

`linear` now raises `AudioSyncContextError` when the global extent is absent. Then, testing
that fix, the **same defect appeared for the gap endpoint values**: a chunk that does not
itself hold a gap's bounding trusted windows was reading a chunk-local *interior* window as
if it were an endpoint. It now raises unless the endpoints are supplied or genuinely in view.

Both are the same principle P3.7 established for `shifts[-1]`, stated as behaviour rather
than as documentation: **required context must be present, not merely available in
principle.** A wrong number with no error is the one outcome this design must not produce.

The requirement is scoped to gaps whose endpoints *exist* — on a stream with no accepted
window at all there is nothing to interpolate between and no endpoint is missing.

Whole-stream decisions populate both maps themselves from the shifts they have just computed,
so a caller deciding the full stream is not asked for context it can already see. The guard is
precisely about **chunks**.

---

## 6a. Failure injection: 30 cases, zero silent-wrong

Every case asserts the **resulting value**, never merely "did not raise" (§91):

| outcome | count |
| --- | ---: |
| `VALUE_OK` — no error, correct value | 13 |
| `RAISED` — the expected error type | 17 |
| `SILENT_WRONG` — no error, wrong value | **0** |
| `UNEXPECTED` — wrong error type | **0** |

Covered: missing left endpoint, missing right endpoint, chunk inside a gap with and without
context, empty gap, single-window gap, all-refused stream, mask/record length mismatch,
negative context base, NaN and Inf delays, NaN and non-integer and 2-D and absurd-magnitude
shift plans, integral float plans, zero-length plans, plan too short, plan too long, exact
plan, hop not dividing the stream, empty stream, non-positive `window_samples` /
`hop_samples` / `sample_rate`, a plan whose states disagree with its mask, unknown policy,
and shifts large enough to push every read out of range.

---

## 7. Frozen-plan reproduction gate: PASS

The production-shaped `current` policy reproduces all four frozen P3.3 plans
**window-for-window with 0 mismatches**:

| case | windows | refused | gaps | `current` vs frozen |
| --- | ---: | ---: | ---: | --- |
| C1305 10 s | 478 | 231 | 55 | **0 mismatches** |
| C1305 2.5 s | 1911 | 931 | 93 | **0 mismatches** |
| C1306 10 s | 414 | 106 | 35 | **0 mismatches** |
| C1306 2.5 s | 1654 | 422 | 52 | **0 mismatches** |

So the executable semantics have **not drifted** from the shipped behaviour.

### 7.1 `hold` versus `current` on real corpora

| case | windows differing in shift | where | `current` unresolved | candidates unresolved |
| --- | ---: | --- | ---: | ---: |
| C1305 10 s | **2** (0.4 %) | windows 0–1 | **0** | 231 |
| C1305 2.5 s | **8** (0.4 %) | windows 0–7 | **0** | 931 |
| C1306 10 s | **2** (0.5 %) | windows 0–1 | **0** | 106 |
| C1306 2.5 s | **8** (0.5 %) | windows 0–7 | **0** | 422 |

**The only numerical difference is the leading gap**, exactly as P3.7 measured. Everywhere
else the two policies apply **identical corrections** while making **different knowledge
claims** — `current` reports all 231 / 931 / 106 / 422 gap windows as resolved measurements.
This is the P3.7 finding reproduced on real material at the implementation level.

---

## 8. What this does not do

- **No product policy choice.** `hold + explicit state`, `no_correction_gap` and
  `explicit-state-as-output` remain **UNRANKED**. Selecting among them is a product trade-off
  about how much uncorrected alignment is acceptable in exchange for never inventing
  structure, and the measurement evidence does not decide it.
- **No `core/` change.** The semantics are not wired in.
- **No claim about correction correctness.** The true in-gap delay is still unknown; the
  reproduction gate validates *implementation consistency with the research semantics*, not
  the correctness of any correction.
