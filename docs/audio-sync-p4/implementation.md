# P4 — Stage 6/7: production API, implementation and feature gate

**Status: implemented, feature-gated, default OFF, not wired into production.**

| item | value |
|---|---|
| module | `core/audio_sync_tv.py` (tracked) |
| algorithm version | `2.4.0-p4` |
| production call sites | **none** |
| default behaviour | **unchanged** |
| feature gate | **absent by construction** — see §1 |

---

## 1. Why there is no flag to set

The brief requires a feature-gated implementation with the default path
byte-identical, and `HANDOFF-CHECKLIST.md` §1 item 1 requires it in a specific
form:

> **A feature flag with default OFF, and an audit showing the OFF path is
> byte-identical.** *Why:* the only acceptable way to ship a correction whose
> policy is undecided.

`production-semantics.md` §1 records the stronger option P4 takes:

> It lives **outside the production tree** on purpose. That is a stronger
> default-path guarantee than a feature flag: **code that is not wired into
> `core/` cannot change `core/`'s behaviour.**

So P4's gate is **structural, not a runtime flag**:

```text
core/audio_sync_tv.py exists and is importable
BUT
core/channel_sync.py, core/sync_fix.py, core/sync_estimate.py, 1kt.py and
core/audio_plan.py contain NO reference to it — asserted by test, not promised
```

A runtime flag can be switched on by configuration, environment, or a mistake.
**Code that no production module imports cannot run.** When a product decision
authorises enablement, the flag becomes necessary — and at that point
`HANDOFF-CHECKLIST.md` §1 item 1 becomes live, exactly as its note says:

> the current implementation needs no flag — it lives outside `core/` entirely…
> **If it is ever wired in, this item becomes live.**

### 1.1 What P4 does *not* do

```text
does NOT touch core/channel_sync.py
does NOT touch core/sync_fix.py
does NOT touch core/sync_estimate.py
does NOT touch core/audio_plan.py
does NOT touch 1kt.py
does NOT change AudioPlan
does NOT change the P1 algorithm version (still 2.3.0-p1)
does NOT add a CLI flag
does NOT change any default
```

`AudioPlan = None` still takes the original path, because `AudioPlan` is not
involved at all. `PRODUCTION-REQUIREMENTS.md`: *"`AudioPlan` is deliberately
**not** the place for this"* — it is declarative and does not execute.

---

## 2. The API

### 2.1 Correction value and epistemic state are separate

```python
@dataclass(frozen=True, slots=True)
class CorrectionDecision:
    shift_samples: int        # the integer correction applied
    state: GapState           # what is claimed about it
    source: str | None = None # derived from `state` when omitted
```

The brief's required shape, exactly:

```python
CorrectionDecision(shift_samples=..., state=GapState.TRUSTED)
CorrectionDecision(shift_samples=..., state=GapState.UNRESOLVED_GAP)
CorrectionDecision(shift_samples=..., state=GapState.REENTRY)
```

**The two states with the same value are both constructible and distinguishable**
— that is the whole requirement. `PRODUCTION-REQUIREMENTS.md` U1:

> **The failure mode this requirement exists to eliminate:**
> `number exists == measurement exists`.

### 2.2 Contradictions are unrepresentable, not merely documented

| construction | result |
|---|---|
| `CorrectionDecision(615, TRUSTED)` | allowed; `source="measurement"` derived |
| `CorrectionDecision(615, TRUSTED, "policy")` | **raises** `AudioSyncPlanError` |
| `CorrectionDecision(615, UNRESOLVED_GAP, "measurement")` | **raises** `AudioSyncPlanError` |
| `CorrectionDecision(615, REENTRY, "policy")` | **raises** `AudioSyncPlanError` |
| `CorrectionDecision(nan, TRUSTED)` | **raises** `AudioSyncPlanError` |
| `CorrectionDecision(1 << 31, TRUSTED)` | **raises** `AudioSyncPlanError` |

There is **no sentinel**. `PRODUCTION-REQUIREMENTS.md`: *"a gap decision carries
an explicit state, never `shift = None`"*, because *"a nullable number cannot
distinguish '0-shift measurement' from 'no measurement'"*.

### 2.3 State is a function of the resolved mask

```python
def derive_states(resolved: np.ndarray) -> np.ndarray:
    # resolved[i] == False                    -> UNRESOLVED_GAP
    # resolved[i] == True and i-1 was a gap   -> REENTRY
    # resolved[i] == True otherwise           -> TRUSTED
```

**This is the only definition of the state sequence.** No chunking, no
previous-window access, no mutable cursor, no global state. P3.7 established that
a per-chunk state cannot be chunk-invariant — a one-window chunk cannot know a
gap preceded it, so it cannot label itself `REENTRY`. Deriving from the mask
removes that entire bug class and makes chunk invariance automatic.

Verified on 120 000 windows: `REENTRY` count equals an **independently counted**
`~resolved[:-1] & resolved[1:]` transition count, exactly.

### 2.4 Explicit context, demanded not defaulted

```python
@dataclass(frozen=True, slots=True)
class GapContext:
    stream_has_trust: bool
    gap_endpoints: tuple[int, int] | None = None
    gap_extent: tuple[int, int] | None = None
    stream_end: int | None = None
```

These are exactly the bounded quantities P3.7 measured as necessary and
sufficient per stream and per gap.

```text
if a policy needs an endpoint and none was supplied -> AudioSyncContextError
if a mask disagrees with stream_has_trust           -> AudioSyncContextError
if a plan's length is not required_window_count     -> AudioSyncPlanError
if context is truncated                             -> refused by length, never
                                                       read as a refusal
```

`PRODUCTION-REQUIREMENTS.md` G4: *"required context must be present, not merely
available in principle… **A caller that cannot supply them gets a refusal — which
is the point.**"*

### 2.5 The coordinate contract is a declared, named parameter

```python
COORDINATES = ("latest-start", "nearest-centre", "sample-domain")
```

The **only** dispatch point is `window_index(coordinate, pos, ...)`. Every record
carries `plan.coordinate`, so the join is a versioned artifact rather than an
implicit consequence of an integer division — P3.9's dual-coordinate contract,
implemented.

| coordinate | `join(k)` | status |
|---|---|---|
| `latest-start` | `k // hop` | **implementation default**; what P3 applied |
| `nearest-centre` | `(k + hop//2 - win//2) // hop`, clipped | implemented, selectable, **not selected** |
| `sample-domain` | *the same function as `latest-start`* | retained **to be excludable**, not as a third option |

`sample-domain` refuses at construction when `window < hop`, because
non-overlapping windows have no covering window to be authoritative about.

### 2.6 The plan carries its own provenance

```python
@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    shifts, states, resolved, policy, coordinate,
    window_samples, hop_samples, n_samples, search_samples,
    version, stream_has_trust, gap_extent, gap_endpoints,
    offline_only, mask_is_authoritative
```

`PRODUCTION-REQUIREMENTS.md` G1: *"Provenance must be part of the object, not a
log line beside it."*

### 2.7 The plan refuses to exist when it is inconsistent

| supplied | result |
|---|---|
| length ≠ `ceil(n/hop)` | **raises** — length is checked for **equality**, never a lower bound |
| states that contradict the mask | **raises** — the state sequence is a derivation, not an input |
| `stream_has_trust` contradicting the mask | **raises** |
| an online policy marked `offline_only` | **raises** |
| unknown policy or coordinate | **raises** |
| mask containing anything but 0/1/False/True | **raises** |
| a valid record with a non-finite delay | **raises** |
| a record with no `valid` field | **raises** — the mask must be **derived** |

### 2.8 `overclaimed_windows()` — making the one historical exception visible

`current` reports gap windows as `TRUSTED`. P3.7 measured that as a **semantic
over-claim, not an algorithmic error** (`132/132` correct state sequence, `0`
fake `TRUSTED`, `resolved` flags wrong on `1/22`).

P4 does not let a policy silently violate the invariant. It models the exception
explicitly:

```python
plan.mask_is_authoritative   # False only for `current`
plan.overclaimed_windows()   # how many windows claim a measurement they lack
plan.is_honest()             # overclaimed_windows() == 0
```

Every other policy is `0`. `current` reports its exact over-claim count, so the
behaviour is **countable, loggable and refusable** instead of hidden.

### 2.9 Offline-only policies cannot run in the streaming path

```python
GAP_POLICIES = {
    "hold": False, "no_correction_gap": False,
    "explicit_state": False, "current": False,     # online
    "linear": True, "next": True,                  # need a RIGHT endpoint
}
```

`apply_shift_plan` refuses a plan marked `offline_only`, and building one without
an explicit `GapContext` raises. `linear` additionally demands endpoint **values**
via `GapContext.demand_endpoints`, which raises when they are absent.

**Recorded honestly:** `current` was initially classified offline in this
implementation and that was **wrong** — P3 documents it as a runnable policy and
P3.11 applied it on real PCM. The classification is now "does it need a right
endpoint?", and `current`'s leading-gap rule needs only the **first** trusted
index, which the mask gives in one bounded pass.

---

## 3. The applier

```python
apply_shift_plan(src, dst, plan, *, storage_dtype="f32",
                 chunk_samples=None, progress=None) -> ShiftPlanStats
```

Contract, unchanged from P1 and P3:

```text
out[k] = in[k + S[join(k)]]      join from plan.coordinate
out-of-range reads zero-filled
output sample count == input sample count
```

### 3.1 The four implementation rules that came from measured failures

| rule | the failure it prevents |
|---|---|
| compute `join` by **closed form** and split the block at window-change boundaries | materialising one int64 coordinate label per sample costs **1.8 GB** at the C1305 length — P3.11 measured **3632 MiB** peak that way, against **110 MiB flat** for the engine path |
| read from the stream per segment, never `np.asarray(stream)` | `np.asarray` on a `RawStream` **does not fail** — it walks the sequence protocol: correct values at **86× (10 s) and 173× (60 s)** the cost |
| `chunk_samples` validated with an explicit `None` test | `if chunk_samples` treats `0` as "use the default", so an invalid request was silently accepted |
| the segment end uses the **exact** join boundary | a "at least one sample" fallback degenerates to one-sample segments whenever `nearest-centre` lags, turning the loop into **O(n)** — measured: a 2 s apply became minutes |

### 3.2 What the applier returns

```python
ShiftPlanStats(
    n_samples_in, n_samples_out, n_blocks,
    n_measurements, n_trusted, n_unresolved, n_reentry,
    coordinate, policy, bytes_written, apply_count, n_overclaimed,
)
```

`apply_count` is non-zero by construction, so a **silent no-op cannot report
success**. This is the brief's N2 requirement
(`apply_count == 0 -> NOT PASS`) built into the return value.

---

## 4. Measured performance

From `work/p4/memory_and_performance.py`. **These are apply-only numbers.
Measurement and verification are NOT in this path** — the brief's §31 warning
that verifier cost must not be reported as correction cost.

| measurement | value |
|---|---|
| apply throughput, 60 s stream | **671–814 MB/s** |
| apply throughput, 5 h stream | **551–745 MB/s** |
| chunk-size sensitivity (480 k / 120 k / 1.92 M / 100 k samples) | 700 / 814 / 718 / 750 MB/s — **flat** |
| traced peak allocation, apply | **9.10 MB**, and **does not scale** with stream length |
| stream scale in the bounded-memory test | **45×** |
| traced peak growth over that 45× | **1.18×** |
| plan construction | **0.05–0.11 s per 100 000 windows** |
| state derivation | **~0.0001 s per 100 000 windows** |
| decisions walked in the long run | **726 000** |
| long-run traced live growth | **+0.00 MB** |

The comparison the brief asks for, since P3 measured it:

| path | throughput | peak RSS | source |
|---|---|---|---|
| P3.11 engine apply, in isolation | 309–547 MB/s | **110 MiB flat** | P3.11 §11 |
| P3.11 research gather | 72–74 MB/s | 185 MiB | P3.11 §11 |
| P3.11's own 3632 MiB run | — | 3632 MiB | P3.11 §11 |
| **P4 apply (this module)** | **671–814 MB/s** | **9.10 MB traced peak**, flat | this session |

**P4's apply is not slower than the P3.11 engine path, and its memory is bounded
by the block rather than the stream.** The 72.6 MB/s nearest-centre figure in the
P3.11 record is the research gather's cost, not the engine's — P4's implementation
of the *same* coordinate reaches 671–815 MB/s because it does not materialise the
coordinate array.

### 4.1 The memory control, and why it matters

The brief's §29 and `HANDOFF-CHECKLIST.md` §1 item 10 require *"a positive control
that actually allocates"*, because P3's memory check reported PASS **three times
while measuring nothing**.

P4's first attempt **failed that requirement** and the harness reported
`control is discriminating: False`. The cause was real: the Windows
`GetProcessMemoryInfo` path this session tried returns **0 bytes** — a silent
measurement failure of exactly the F11 family. It was replaced by `psutil`, and
`tracemalloc` is used as a second, independent instrument.

Raw measurements are reported in `work/p4/memory-performance.json` rather than
summarised, so a reader can check them.

One finding worth carrying forward, because it nearly produced a false leak
report: a long-run test that retains one plan per iteration shows **~11 MB of RSS
growth per iteration** and that is entirely the harness's own retained objects.
The corrected test distinguishes:

```text
RSS held      = the harness's retained objects    (not a leak)
RSS released  = allocator behaviour               (noisy; +6.5 MB here)
traced live   = real live Python/numpy objects    (+0.00 MB)
```

**The check that discriminates is `traced live`, not RSS.**

---

## 5. Where the production wiring would go, when authorised

Recorded so the integration is a decision rather than a discovery.
`production-semantics.md` §1 and `HANDOFF-CHECKLIST.md` §3.4:

| item | fact |
|---|---|
| the call site | `core/channel_sync.py` lines 839–851, the per-track correction loop |
| why it is one place | the whole trajectory is collapsed to one number before `shift_stream`; a per-window plan changes that **signature**, which is why it is **not a drop-in replacement** |
| the line-843 hazard | `int(np.rint(e.delay_samples))` currently sits **outside** the `try:` that begins at line 844. `int(np.rint(nan))` raises `ValueError` and is **not** caught by the single-track fallback below it |
| the required property | **one place decides the shift, and it is inside the `try`** |
| the tail seam | the measurement layer produces `floor((n-win)/hop)+1` windows, the applier needs `ceil(n/hop)`. On both corpora the seam is **3**. The tail must be declared unmeasured — **never** padded, truncated, or filled by repeating a shift |

**None of this is done in P4.** It is the shape of the change a product decision
would authorise.

---

## 6. What is verified

`tests/audio_sync_p4/test_production_sync.py` — **180 cases, 0 silent-wrong, 0
fail**, exit 0, from tracked code only.

```text
VALUE_OK        119    asserts a resulting value
RAISED           49    asserts a specific error type
CONTROL_OK       12    demonstrates a check CAN fail
CONTROL_FAILED    0
SILENT_WRONG      0
```

`work/p4/verify_p311_reproduction.py` — **30/30 arms reproduce the P3.11
corrected PCM byte-for-byte**, and **30/30 plan state counts match** the P3.11
plan records. See `validation.md`.

---

## 7. API verdict

```text
correction value and epistemic state separated      DONE
state = f(resolved mask), no chunk-local history    DONE
explicit context; absence raises                    DONE
UNRESOLVED_GAP distinct from ERROR                  DONE
every candidate selectable without a code change     DONE
plan carries its own provenance                     DONE
default path structurally unchanged                 DONE, asserted by test
production wiring                                   NOT DONE, by design
```
