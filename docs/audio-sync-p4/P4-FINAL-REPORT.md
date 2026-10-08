# P4 — FINAL REPORT
## Audio Sync: Production Policy & Controlled Integration

| | |
|---|---|
| phase | **P4** — Production Policy & Controlled Integration |
| branch | `research/audio-sync-p3-production-semantics` |
| base commit | `8bb229764f551955db2c392e4b2bee6bfd7e9418` |
| pushed / merged / tagged | **NO / NO / NO** |
| production default behaviour | **UNCHANGED** |
| time-varying production feature | **OFF** |
| overall | **READY FOR PRODUCTION POLICY** |

---

# 1. P3 Frozen Baseline

P3.1–P3.12 are **frozen**. P4 modified **no P3 report and no P3 artifact**.
`docs/audio-sync-p4/evidence-freeze.md` is P4's index of what may be quoted and
from which file.

| phase | verdict | P4's use |
|---|---|---|
| **P1** | `VALIDATED`, shipped | the regression floor: **27 PASS / 0 FAIL** re-run this session |
| **P3.10** | `E2E MECHANISM VALIDATED` | first real measurement → plan → **actual PCM apply** → independent verify. Config window 30 s / hop 10 s / search ±3000. **One pair per corpus** (`ch3→ch4`); the other two measured only |
| **P3.11** | six pairs `E2E complete` | all six pairs through measure → plan → apply → independent verify, 962/962 and 832/832 verify points, 0 contract disagreements |
| **P3.12** | `NO ADVANTAGE DEMONSTRATED` | adaptive coarse-to-fine costs **2–10×**; not wired in, not re-opened |

**The P3.11 residual tail claim was corrected by its own addendum, and P4 uses
the corrected figures.** The unfiltered "max 1064.141 → 1796.063 = +69 %" is a
**refused-measurement artefact**; on the `both_valid` population C1305 ch3→ch4's
maximum goes `737.69 → 771.16` (**+4.5 %**), and **five of six pairs improve in
every statistic**.

`evidence-freeze.md` §7b records **ten places** where a P3 summary range does not
reproduce from that phase's own detail table. They are recorded, not resolved, and
**none is load-bearing for a P4 decision**.

---

# 2. Production Policy

**Not selected. `DEFERRED`.** The full matrix is `policy-matrix.md`; this is the
conclusion.

| decision | candidates | status | owner |
|---|---|---|---|
| **gap policy** | `current` (baseline) / `hold` / `no_correction_gap` / `explicit_state` / `linear` (offline) | **DEFERRED** | product (D1, D5) |
| **coordinate policy** | `latest-start` / `nearest-centre` (`sample-domain` is **not** a candidate) | **DEFERRED** | product + engineering (D2) |
| **enablement** | none / gated default-OFF / default ON | **gated default-OFF BUILT; ON DEFERRED** | product (D8) |

The measured facts that constrain the choice, and the reason no ranking is offered:

```text
hold and explicit_state produce BIT-IDENTICAL corrected PCM on all six pairs
    - they differ only in what they claim. P4 reproduced both.

no_correction_gap is nearly a NO-OP at low coverage
    - C1305 ch3->ch1 after median 1130.000 against a before median of 1136.599

current reports 0 unresolved windows where the same mask yields 109-388

the instrument that would rank these CANNOT SEE A DIFFERENCE
    - policy_jump / artificial_jump is policy-invariant at two-sided boundaries:
      0 of 8136 policy-pairs differ, max 5.0 samples,
      0 events over 50/100/200/500-sample thresholds

coverage is 19.5-74.4 % of windows, but 100 % of samples are rewritten
```

`NO POLICY IS RANKED. NO POLICY IS CALLED BEST. NO POLICY IS SELECTED.`

---

# 3. Coordinate Contract

**`DEFERRED`, with `latest-start` as the implementation default for
reproducibility and stated as such.**

The brief's three questions are answered separately in `coordinate-decision.md`,
because `measurement time ≠ apply time` and `window index ≠ physical correction
coordinate`:

```text
MEASUREMENT position   window index w, read as the START of the analysis window
                       measurement_start_sample(w)  = w * hop
                       measurement_center_sample(w) = w * hop + window/2

APPLICATION position   the OUTPUT SAMPLE index k, through a declared join
                       output[k] = input[k + S[join(k)]]

OUTPUT coordinate      k in [0, N), N = input sample count; out-of-range reads
                       zero-fill; output length == input length
```

### Measured, in actual sample coordinates

| quantity | C1305 | C1306 |
|---|---:|---:|
| **index** agreement | **0.2080168660074959 %** | **0.24011561086431897 %** |
| **value** agreement | **0.5694050873644835** (56.94 %) | **0.3636936312095548** (36.37 %) |
| first **index** divergence | sample **480 000** = 10.0 s | sample **480 000** = 10.0 s |
| first **value** divergence | sample **960 000** = 20.0 s | sample **960 000** = 20.0 s |
| max positional difference | **1 index** (10.000 ms) | **1 index** (10.000 ms) |
| max abs shift difference | **732 samples** | **211 samples** |
| value-disagreement zones | **73**, each 1–15 hops | **63**, each 1–19 hops |

**The two divergences are 480 000 samples = 10 s apart, and that is the whole
result:** the lag only becomes visible where the shift sequence changes. Block 0
and block 1 are identical; the first applied-value difference is at block 2,
sample 960 000.

`sample-domain` is **proven** to be the same function as `latest-start`
(`disagree: 0` on both corpora) and is therefore **not a third candidate**. P4
refuses it when `window < hop`.

### A new finding, recorded without a policy conclusion

Under `nearest-centre`, `excess = after − (before − applied)` reaches **732
samples** where `latest-start` gives **0.615** — because `excess` is computed
against the plan's `S[b]` while the sample receives `S[b−1]`.

```text
this is NOT an audio defect
it means the plan's per-window shift indexing is part of the apply contract,
not metadata
```

**A P4 verifier must compute its expectation from the shift actually applied at
that sample**, or it will report a 732-sample "excess" that no audio exhibits.

---

# 4. Gap Policy

**`DEFERRED`.** Semantics are **specified and validated**; the policy is not
selected. Full analysis in `gap-policy-decision.md`.

### The three states

| state | meaning | allowed | forbidden |
|---|---|---|---|
| `TRUSTED` | the window's own measurement was accepted | apply the window's own `rint(delay)` | inventing a neighbouring value |
| `UNRESOLVED_GAP` | the window was refused; the shift is a **policy decision, not a measurement** | hold / fill 0 / interpolate-under-stated-assumption | **reporting `resolved = True`** |
| `REENTRY` | the first accepted window after a gap | resume applying measurements | retroactively amending the gap |

### The three options, stated exactly

```text
HOLD            gap: shift remains the previous trusted shift (0 if none);
                     state = UNRESOLVED_GAP
NO CORRECTION   gap: correction disabled, applied shift = 0;
                     state = UNRESOLVED_GAP
INTERPOLATION   gap: numerical interpolation between the bounding trusted shifts;
                     state REMAINS UNRESOLVED_GAP
                interpolation != measurement
                an interpolated value MUST NOT be labelled TRUSTED
```

### Why no candidate can be preferred on this evidence

The candidates separate **only by which kind of situation they fail in**, and the
discriminator — where a discontinuity sits inside the gap — is exactly what the
gap conceals. `linear` **halves** the error when the discontinuity is mid-gap and
**doubles** it when it sits at the closing edge. The sign of the comparison
depends on an unobservable position.

P3.4's split finding is inherited **in split form and must not be re-merged**:

```text
gap policy = dominant residual source      STANDS   (direct, same-axis evidence)
carried shift is itself the culprit        REJECTED (its own criterion does not
                                                     license it)
```

### And one correction to a common misreading

**`current` is not algorithmically wrong.** Its state machine is `CORRECT`
(**132/132**, 0 fake `TRUSTED`); its `resolved` flags over-claim on **1 of 22**.
That is a **semantic over-claim, not an algorithmic error.** P4 models the
exception explicitly rather than hiding it: `plan.overclaimed_windows()` reports
the count (0 for every other policy), so the behaviour is countable and refusable.

---

# 5. Residual Acceptance

**`RESIDUAL ACCEPTANCE THRESHOLD = NOT ESTABLISHED`.** No threshold was invented,
none was raised, and no `PASS if residual < X` statement appears anywhere in P4.

P4 built the **gate** instead, exactly as the brief's §14 suggests:

### Structural invariants — a violation is a FAILURE, never a low score

| id | invariant | P4 status |
|---|---|---|
| A1 | apply mechanics exact against the plan | **ENFORCED** — 30/30 arms byte-exact; `excess ≤ 1.96 samples` on `both_valid` |
| A2 | state is honest | **ENFORCED** — `overclaimed_windows()` counted and reported |
| A3 | no unmeasured window is `TRUSTED` | **ENFORCED** — counts equal an independently derived expectation |
| A4 | independent measurement available | **RETAINED** — second estimator, different frame grid, reads only PCM |
| A5 | no invalid context silently used | **ENFORCED** — 49 raise-cases, 0 silent-wrong |
| A6 | chunk invariance | **ENFORCED** — byte-identical at 8 chunk sizes |
| A7 | uncertainty is not error | **ENFORCED** — type-level separation |
| A8 | default path unchanged | **ENFORCED** — no production module references the module |

### No-silent-error rules — the brief's §36, permanent

```text
N1  experiment_count == 0  -> NOT PASS        (suite exits 2)
N2  apply_count == 0       -> NOT PASS        (ShiftPlanStats.apply_count)
N3  verify_count == 0      -> NOT PASS
N4  a check that cannot fail -> reported as a finding, not a pass
N5  "it did not raise"     -> not a finding; assert the VALUE
N6  an empty run           -> exit 2
```

### The error budget, with sources

| component | measured |
|---|---|
| estimator variance across runs | measurement **bit-identical** to the frozen P3 run, 478/478 windows |
| estimator vs analytic truth | `max\|real − model\| = 0.0000` samples |
| planner integer rounding | ≤ **0.5 samples** per segment; span tolerance **1.0 sample** |
| **apply mechanics** | `excess` max **0.31–1.96 samples** across all six pairs (`both_valid`) |
| independent verifier | **5 of 6 pairs within ±3 samples**; the sixth **+70 samples / 1.458 ms**, `fine_fit_r2 = −0.21` |
| noise floor | median `excess` **0.0017–0.0217 samples** |
| calibrated per-window σ | **NOT ESTABLISHED** — `UNCERTAINTY CALIBRATION = PARTIAL` |

### Why the threshold is not established, and what would establish it

It is blocked by: no reference for "good enough" (D5); no calibrated σ; the
accepted population being 74–107 of 481/416 points on four of six pairs; one
unresolved instrument disagreement; and the in-gap population being **unscoreable
at all** because the value there is unobservable.

Five concrete experiments would establish it — **R1** a controlled fixture with
known time-varying delay, **R2** an accepted-population coverage sweep, **R3** a
calibrated per-window σ, **R4** a minimum-coverage requirement, **R5** a product
statement of purpose. **P4 did none of them**, because R1–R3 change the
measurement configuration and `P4-HANDOFF.md` §2 forbids changing it inside an
integration.

---

# 6. Production API

`core/audio_sync_tv.py`, algorithm version **`2.4.0-p4`**. Full detail in
`implementation.md`.

```python
CorrectionDecision(shift_samples=615, state=GapState.TRUSTED)
CorrectionDecision(shift_samples=615, state=GapState.UNRESOLVED_GAP)
CorrectionDecision(shift_samples=615, state=GapState.REENTRY)
```

**The correction value and the epistemic state are two separate frozen fields,
not one nullable number.** The same shift with two states is constructible and
meaningful — which is the whole requirement, because a nullable number cannot
distinguish *"0-shift measurement"* from *"no measurement"*.

### Contradictions are unrepresentable

| construction | result |
|---|---|
| `(615, TRUSTED)` | allowed |
| `(615, TRUSTED, "policy")` | **raises** |
| `(615, UNRESOLVED_GAP, "measurement")` | **raises** |
| `(nan, …)`, `(±inf, …)`, `(1<<31, …)` | **raises** |
| any state contradicting the mask | **raises** at plan construction |

### State is a function of the resolved mask — never of chunk-local history

```python
def derive_states(resolved):
    # False                      -> UNRESOLVED_GAP
    # True and prev was a gap    -> REENTRY
    # True otherwise             -> TRUSTED
```

**The only definition.** No chunking, no `shifts[-1]`, no `previous_chunk`, no
global mutable state, no mutable cursor. Verified: `REENTRY` equals an
**independently counted** transition count over 121 000 windows.

### Context is demanded, not defaulted

```python
GapContext(stream_has_trust, gap_endpoints, gap_extent, stream_end)
```

Missing context **raises** `AudioSyncContextError`. There is no guess default, no
silent fallback, no chunk-local assumption.

### The join is a declared, versioned parameter

```python
COORDINATES = ("latest-start", "nearest-centre", "sample-domain")
```

dispatched only in `window_index()`. P3.9's dual-coordinate bookkeeping contract,
implemented: every record carries both coordinates and the declared join.

---

# 7. Implementation

| item | value |
|---|---|
| module | `core/audio_sync_tv.py` — 44 909 bytes, tracked |
| tests | `tests/audio_sync_p4/test_production_sync.py` — 58 351 bytes, tracked |
| production call sites | **none** |
| dependencies | numpy, stdlib, and `core.sync_estimate`'s stream reader **only** |
| forbidden dependencies | none of `encoders` / `preservation` / hardware / Sony / DJI / `audio_plan` |

### Four implementation rules, each from a measured failure

| rule | the failure it prevents |
|---|---|
| closed-form join + segment splitting | materialising one int64 per sample costs **1.8 GB** at C1305 length — P3.11 measured **3632 MiB** that way against **110 MiB** for the engine path |
| read per segment, never `np.asarray(stream)` | it **does not fail**; it walks the sequence protocol at **86× / 173×** the cost |
| `chunk_samples` validated with an explicit `None` test | `if chunk_samples` treats `0` as "use the default" |
| segment end uses the **exact** join boundary | a one-sample fallback degenerates to O(n) whenever `nearest-centre` lags — measured: a 2 s apply did not finish in 300 s |

### Four real defects the suite found in P4's own code

1. `CorrectionDecision` defaulted `source="policy"`, so the brief's **own example** raised. A default that makes correct usage fail is a defect.
2. `chunk_samples=0` silently used the default.
3. The O(n) segment-boundary defect above — **invisible in the test suite**, found only on 923 MB of real audio at the alternative coordinate.
4. `current` was classified offline-only, which is **wrong**: P3 documents it as runnable and P3.11 applied it on real PCM.

### `AudioPlan` is not involved

`AudioPlan` is declarative and does not execute. P4 did not touch it, so the
brief's "`AudioPlan = None` keeps the original path" holds trivially.

---

# 8. Default Path Protection

**The gate is structural, not a runtime flag.**

```text
core/audio_sync_tv.py exists and is importable
BUT
core/channel_sync.py, core/sync_fix.py, core/sync_estimate.py,
core/audio_plan.py, 1kt.py contain NO reference to it
- asserted by test, not promised
```

A runtime flag can be switched on by configuration, environment or a mistake.
**Code that no production module imports cannot run.** `production-semantics.md`
§1 calls this *"a stronger default-path guarantee than a feature flag"*, and P4
took that option. `HANDOFF-CHECKLIST.md` §1 item 1 anticipates exactly this: *"If
it is ever wired in, this item becomes live."* It was not wired in, so no flag was
needed and none was added.

### Evidence

| check | result |
|---|---|
| existing unit regression, after P4's changes | **626 PASS / 0 FAIL** |
| P1 guard | **27 PASS / 0 FAIL** |
| the three P1 production files, SHA-256 | **3 / 3 MATCH** — byte-identical to HEAD |
| the nine user-owned modifications, SHA-256 | **9 / 9 MATCH** — nothing attributed, touched or reverted |
| `shift_stream` signature | still takes a **scalar** `delay_samples` |
| `algo_version` | still `2.3.0-p1` |
| `core/audio_sync_tv` references outside itself | **0** |

**Byte-identity is the strongest form available here**, and it is satisfied in the
strongest possible sense: the bytes implementing P1's validated correction are
unchanged, so P1's behaviour cannot have moved.

---

# 9. C1305

230 750 520 samples, 923 002 080 bytes, 48 kHz float32, reference CH3.
`n mod hop = 350 520` → seam **3**.

| Pair | P3.11 `dst_sha256` | P4 result |
|---|---|---|
| ch3→ch1 | `6E4D5A22D9E05CCD35352C41A7D6538BB22ECBBC7EC1933D75D9ACC8A3726E2E` | **MATCH** |
| ch3→ch2 | `30259ACDD7957A2E28860BB0D85599DC0A635D8DA3421CE0C7AB59019C1D25C4` | **MATCH** |
| ch3→ch4 | `413E0205BB5622EA838F98339BD6E0FDAE1235AFCE5B6BBE93C60747B5D9B20C` | **MATCH** |

`nearest-centre` × `hold`, all three pairs — **also MATCH**
(`7FF13089…`, `32AF26A9…`, `7C27E252…`).

Plan state counts for ch3→ch4: `TRUSTED 193 / UNRESOLVED_GAP 234 / REENTRY 54` —
reproduced exactly.

`413E0205…` is **the same hash P3.10 recorded**. P3.10 → P3.11 → **P4**: three
independent reproductions from different code.

---

# 10. C1306

199 903 704 samples, 799 614 816 bytes, reference CH3.
`n mod hop = 223 704` → seam **3**.

| Pair | P3.11 `dst_sha256` | P4 result |
|---|---|---|
| ch3→ch1 | `03F63C2388366CD4A2A640E0C655222805ADBB56B8DAE78F48B677934462FDDF` | **MATCH** |
| ch3→ch2 | `569B7D706686CE34110ABD321FE04B19938C9343610A7C3C8A4781F57B900ADC` | **MATCH** |
| ch3→ch4 | `642B1B2197F99DF4F6AEEB2569437E1F12D61E727D606E518E111A4553A3A4F4` | **MATCH** |

`nearest-centre` × `hold` — **also MATCH** (`04D8E1EF…`, `BBCD8FC3…`, `925EB432…`).

Plan state counts for ch3→ch4: `TRUSTED 274 / UNRESOLVED_GAP 109 / REENTRY 34` —
reproduced exactly.

### The one result that is not clean

The independent instrument disagrees on C1306 ch3→ch2: main verifier median
**8.46 samples** against **+70.000 samples** (`fine_fit_r2 = −0.21`). P3.11 §6.5
states it is *"reported as a disagreement between instruments, not resolved by
preferring one."* **P4 records it and does not average it**, and it is never used
to claim agreement.

---

# 11. Six-Pair E2E

All six pairs, `latest-start` × `hold`, **byte-for-byte identical to the P3.11
corrected PCM**:

| Corpus | Pair | result |
|---|---|---|
| C1305 | ch3→ch1 | **MATCH** |
| C1305 | ch3→ch2 | **MATCH** |
| C1305 | ch3→ch4 | **MATCH** |
| C1306 | ch3→ch1 | **MATCH** |
| C1306 | ch3→ch2 | **MATCH** |
| C1306 | ch3→ch4 | **MATCH** |

```text
arms with a recorded P3.11 hash : 36
arms run                        : 30
BIT-EXACT MATCH                 : 30
MISMATCH                        : 0
aborted (error)                 : 0
state counts checked            : 30 (0 mismatched)
```

**All six pairs, both coordinate contracts, and every online gap policy where
P3.11 recorded a hash** — 30 arms. Before every comparison the **recorded source
SHA-256 was checked against the corpus on disk**, so a wrong-input comparison could
not pass.

**Six `linear` arms were deliberately NOT reproduced:** that policy needs a right
endpoint, P4's streaming contract refuses it, and reproducing it would require the
1.8 GB per-sample coordinate array. They are printed as excluded and **not counted
as passes**.

`explicit_state` reproduces `hold`'s hash exactly on every pair — P3.11's
"bit-identical PCM, only the claim differs" is confirmed by a different
implementation.

---

# 12. Independent Verification

Two independent instruments, neither sharing code with the other.

| # | instrument | what it establishes |
|---|---|---|
| **1** | the **P3.11 stored `dst_sha256` records** — produced by P3.11's engine, in an earlier session, from different code | the P4 applier produces **the same audio, bit for bit** |
| **2** | **`core.sync_estimate`** — tracked production code, 200 ms frames / 100 ms hop / 30 s anchor / 8 kHz coarse search, reading only PCM | an independent measurement of the corrected output |

**Instrument 1 is the strongest check P4 can make**, because the expected value is
not something P4 computes — it is a stored production record.

**Instrument 2 is the standing A4 invariant**, retained rather than re-invented:
any claim that a correction is valid must survive an instrument sharing no code
with the one that produced it.

Result from instrument 2, from the P3.11 record: **5 of 6 pairs within ±3
samples** (a sixteenth of a millisecond between two structurally different
estimators); one disagreement recorded and not resolved.

P4 also added an **internal** independent check: `slow_reference()` in the test
suite materialises the per-sample window index and gathers directly, deliberately
sharing no code with the block applier. It agrees on every fixture and every chunk
size.

---

# 13. Failure Safety

**49 raise-cases, 0 silent-wrong.** Every input class the brief's §27 names:

| input | result |
|---|---|
| missing measurement | **raises** |
| missing endpoint | **raises** |
| invalid mask (value 2) | **raises** |
| NaN shift | **raises** |
| Inf shift | **raises** |
| huge shift (`10**12`, `1<<31`) | **raises** — checked **before** any signed cast, without `np.abs` |
| negative index / out-of-range window | **raises** |
| truncated record set | **raises by length**, never read as a refusal |
| truncated context | **raises** |
| chunk split | **byte-identical**, never a silent divergence |
| EOF / SOF (zero-length, 1-sample stream) | handled by definition; out-of-range reads zero-fill |

### The one that matters most

A measurement record that was **refused** can still carry a number — the estimator
returned a value and flagged `valid = False`. **That number must never be used as
a shift.** P4 asserts the VALUE:

```text
records: [ (valid, 500), (REFUSED, 999), (valid, 100), (valid, 100) ]
shifts:  [ 500, 500, 100, 100 ]       <- the 999 is NOT used
state:   [ TRUSTED, UNRESOLVED_GAP, TRUSTED, TRUSTED ]
```

### Uncertainty is not error — enforced at the type level

```text
UNRESOLVED_GAP   a VALID epistemic state.
                 The plan is correct; the execution succeeded; the system is
                 stating what it does not know. It NEVER raises.
ERROR            an INVALID execution.
                 AudioSyncError and its subclasses. It ALWAYS raises.
```

`test T8.13` asserts `UNRESOLVED_GAP` is **not** an `AudioSyncError`.

---

# 14. Chunk Invariance

**Byte-level, at 8 chunk sizes, including one that puts a chunk boundary inside a
segment.** Every online policy.

```text
chunk sizes proved: n (whole), hop, 3*hop, 3*hop+7, hop/3, 4096, 999, 1
```

`3*hop + 7` splits both a segment and a coordinate zone; `1` is a single sample
per block. **All give an identical SHA-256.**

### Why it is automatic rather than tested-in

P3.7's finding, adopted as an architectural constraint: *state must be computed as
a pure function of the resolved mask over the whole stream.* With chunk size 1 the
**shift** sequence is chunk-invariant but a per-chunk **state** is not — a
one-window chunk cannot know a gap preceded it, so it cannot label itself
`REENTRY`. Deriving from the mask removes that entire bug class.

P4 additionally verifies that applying from a **`RawStream` object** and from a
**path** give identical bytes.

---

# 15. Memory

**Control first, because P3's memory check passed three times while measuring
nothing.**

| requested | RSS delta | ratio | traced delta | ratio |
|---:|---:|---:|---:|---:|
| 64 MB | 67.1 MB | 1.001 | 67.1 MB | 1.000 |
| 256 MB | 268.4 MB | 1.000 | 268.4 MB | 1.000 |
| 1024 MB | 1073.7 MB | 1.000 | 1073.7 MB | 1.000 |

all three **measurable**, every control **released**.

**This step first reported `discriminating: False`, and that is the important
part of the record.** The Windows `GetProcessMemoryInfo` path returns **0 bytes**
here — a silent measurement failure of exactly the F11 family that
`HANDOFF-CHECKLIST.md` §1 item 10 exists to catch. It was replaced with `psutil`,
and `tracemalloc` added as a second independent instrument.

### Bounded by the block, not by the stream

| stream | samples | traced peak above baseline | RSS delta |
|---:|---:|---:|---:|
| 11.5 MB | 2 880 000 | 9.10 MB | +1.04 MB |
| 57.6 MB | 14 400 000 | 9.10 MB | +0.00 MB |
| 345.6 MB | 86 400 000 | 9.10 MB | +0.00 MB |
| **1382.4 MB** | **345 600 000** | **9.10 MB** | **+0.00 MB** |

```text
stream grows 120.0x   ->   traced peak grows 1.00x
no per-sample coordinate label, no full materialisation
```

### Long run

```text
2 420 000 decisions walked over 20 iterations
REENTRY vs independent count : 25928 vs 25928  -> MATCH
traced live growth           : +0.01 MB      (flat)
RSS slope 1st half           : +1.341 MB per iteration
RSS slope 2nd half           : +0.141 MB per iteration   <- ~10x lower
unbounded growth detected    : rss=False, traced=False
```

**The growth is bounded, and that is demonstrated rather than asserted.** A
"first vs last" comparison cannot separate a leak from allocator retention. What
separates them is whether the growth **plateaus**, so the test splits the run in
half and compares slopes.

Three quantities are separated, and only one answers the question:

```text
RSS held      the harness's own objects        not a leak
RSS released  allocator behaviour              noisy, plateaus
traced live   real live Python/numpy objects   +0.01 MB over 2.42M decisions
```

The first version of this test **retained one plan per iteration** and reported
~11 MB per iteration as a possible leak. That was the harness's own objects. The
finding is recorded because it is the same defect P3 records for its first
measurement — **a memory check that charges the harness's arrays to the code under
test.**

---

# 16. Performance

**Apply only. Measurement and verification are NOT in this path** — the brief's §31
requirement that verifier cost not be reported as correction cost.

| measurement | value |
|---|---|
| apply, 60 s stream | **682–702 MB/s** |
| apply, **5.0 h stream** (864 000 000 samples, 1800 windows) | **744.6 MB/s**, RSS delta **−0.3 MB** |
| chunk sizes 480 k / 120 k / 1.92 M / 100 k samples | 675 / 863 / 682 / 795 MB/s — **flat** |
| plan construction | **0.048–0.052 s per 100 000 windows** |
| state derivation | **~0.0002 s per 100 000 windows** |
| traced peak allocation | **9.10 MB**, flat |

### Against the P3.11 reference numbers

| path | throughput | peak | source |
|---|---|---|---|
| P3.11 engine apply, isolation | 309–547 MB/s | **110 MiB flat** | P3.11 §11 |
| P3.11 research gather | 72–74 MB/s | 185 MiB | P3.11 §11 |
| P3.11's own 3632 MiB run | — | 3632 MiB | P3.11 §11 |
| **P4 apply** | **675–863 MB/s** | **9.10 MB traced**, flat | this session |

**P4's apply is not slower than the P3.11 engine path, and its memory is bounded
by the block.** The 72.6 MB/s `nearest-centre` figure in the P3.11 record is the
research gather's cost, not the engine's — P4 implements the *same* coordinate at
682–783 MB/s because it does not materialise the coordinate array.

### The number that is still missing

**End-to-end cost through decode + measure + correct + encode has never been
measured** (`OPEN-DECISIONS.md` D4). Measurement alone is **4.3–4.7 s per 30 s
window**, 97–99 % of total wall time — so **the correction is not the expensive
part**, and the pipeline's cost is dominated by something P4 did not measure
because the module is not wired in.

---

# 17. Compatibility

| item | status |
|---|---|
| **P1 constant correction** | **PASS** — 27 PASS / 0 FAIL; the three production files byte-identical to HEAD |
| **P3 time-varying correction** | **PASS** — 30/30 arms byte-exact |
| **`AudioPlan`** | **UNTOUCHED** — not referenced at all |
| **external audio** | **UNTOUCHED** — `core/sync_estimate` unchanged by hash |
| **channel-sync** | **UNTOUCHED** — `core/channel_sync.py` unchanged by hash; does not import the module |
| **Sony / DJI preservation** | **UNTOUCHED** — no dependency in either direction; the module imports neither |
| **video encoders** | **UNTOUCHED** — no dependency |
| **hardware decode routing** | **UNCHANGED** — no dependency; the module never forces software decode because it is not in any pipeline |
| **existing unit suite** | **626 PASS / 0 FAIL** |
| **resume / batch / watchfolder / multihw** | **NOT DIRECTLY TESTED** — they exercise `1kt.py` and `core/channel_sync.py`, both unchanged by hash, and the existing unit suite passes; but **no live end-to-end run was performed**, because the module is not wired in |

### The audio-sync ↔ video boundary

Verified by parsing the module's AST and asserting its **import list** is exactly
`{stdlib, numpy, core.sync_estimate}`. Scanning docstrings instead would have
produced a false failure — the module deliberately *names* the things it must not
depend on.

```text
Audio domain  ||  cross-domain orchestration  ||  Video domain
```

The new module sits entirely inside the audio domain. It does not import
`encoders` internals, `preservation` internals, a hardware backend, or Sony
metadata. **Sony metadata preservation and time-varying sync policy are not
coupled in either direction.**

### The honest gap

The brief's §33 asks for `batch` / `watchfolder` / `multihw` / resume to be
verified. **They are not**, and cannot be, without wiring the module in — which is
exactly what is not authorised. What *is* verified is the property that makes them
safe today: **nothing in those paths can reach the new code**, because nothing
imports it.

---

# 18. Regression

| suite | command | result |
|---|---|---|
| **P4 production regression** | `python tests/audio_sync_p4/test_production_sync.py` | **180 cases, 0 silent-wrong, 0 fail** |
| **existing unit** | `python tests/full_autotest.py --level unit` | **626 PASS / 0 FAIL** |
| **existing toolchain (L2)** | `python tests/full_autotest.py --level toolchain` | **649 PASS / 0 FAIL** |
| **P1 guard** | `python work/p2/scripts/p1_regression_guard.py` | **27 PASS / 0 FAIL** |
| **P3.11 reproduction** | `python work/p4/verify_p311_reproduction.py` | **30/30 byte-exact, 30/30 state counts** |
| **P3.11 coordinate tests** | `python tests/audio_sync_p311/test_coordinates.py` | **71 PASS / 0 FAIL** |
| **memory / performance** | `python work/p4/memory_and_performance.py` | **PASS** |
| **hash re-verification** | `python work/p4/hash_verify.py` | **12 / 12 MATCH** |
| **P3.8 / P3.10 / P3.12 suites** | in `work/`, **not re-run by P4** | see note |
| **L3 (`--level full`)** | real encoder pipelines | **not run, deliberately** — see note |

### L2 is the level that matters for P4

`--level toolchain` adds real tool probing and — relevant to the user's
concurrent work — **file-level metadata** (`nrtm` meta across a re-mux) and the
`tref-cdsc` cross-remux cases. **Both pass**, which is the evidence that P4 left
the user's `core/batch_hw.py` container-preservation fix working.

**L3 (`--level full`) was not run.** It performs real NVENC/QSV pipelines and
takes 10–15 minutes, and **P4's module is not reachable from any of it**. Running
it would test encoder backends P4 did not touch, not P4. Recorded as a deliberate
choice rather than an oversight.

### Why the P3.8/P3.10/P3.12 suites were not re-run

They test the **frozen research implementation** under `work/p3/`, which P4 did
not modify — verified by the fact that P4 wrote nothing under `work/p3/`. Their
subject is unchanged, so re-running them would re-confirm a frozen result rather
than test P4's changes. **The suites whose subject P4 *did* change were re-run**:
the existing unit suite, the P1 guard, the P3.11 coordinate tests, and P4's own.

### The anti-silent-pass gate

```text
the suite exits 2 when cases == 0, VALUE_OK == 0, RAISED == 0, or CONTROL_OK == 0
```

**12 controls, all demonstrated discriminating.** Every fixture family carries one,
because *"'the test passed' is not evidence until the test has been shown able to
fail."*

---

# 19. Provenance

Full detail in `provenance.md`.

| item | value |
|---|---|
| P4 base commit | `8bb229764f551955db2c392e4b2bee6bfd7e9418` |
| branch | `research/audio-sync-p3-production-semantics` |
| pushed / merged / tagged | **NO / NO / NO** |
| `VERSION` | **unchanged** (`0.8.1` on disk, tag `v0.8.0` unmoved) |
| implementation | `core/audio_sync_tv.py` — `2B66AE4EB4396384A6BB57742E35BB9E98D62B7457AA71AC055A485563D501A0` |
| tests | `tests/audio_sync_p4/test_production_sync.py` — `78523D57801F91F798D4139E720F93F560B09CF048B555B5057842F98D41A952` |
| algorithm version | `2.4.0-p4` |
| coordinate contract version | `latest-start` / `nearest-centre` / `sample-domain`(= latest-start) |
| policy contract version | 6 policies, 4 online / 2 offline |
| configuration | window 1 440 000 / hop 480 000 / search ±3 000 — **frozen, asserted, unchanged** |
| corpus hashes | 8 files, recorded; **re-verified before all 30 comparisons** |
| reproduction anchors | 30 P3.11 `dst_sha256` values |

### Files P4 must not have changed — re-verified at P4 end

```text
9 user-owned tracked modifications   -> 9/9 SHA-256 MATCH
3 P1 production files                -> 3/3 SHA-256 MATCH
every file under docs/audio-sync-p3/ -> untouched
work/p3/, work/p2/, work/archive/    -> untouched
```

### The structural improvement P4 made

`P4-HANDOFF.md` §3 warned that if P4 needed the P3 implementation to survive,
moving it somewhere tracked was the first task. **P4 did not move `work/`** —
that is D7, a repository-layout decision owned by the repository owner, and moving
~25 GiB of research artifacts is not P4's call. **P4 removed the dependency
instead:**

```text
P4's module and tests are TRACKED and import NOTHING from work/
P4's reproduction anchors are P3.11 hashes quoted in TRACKED documents
=> if work/ were deleted tomorrow, P4's implementation, its 180-case suite and
   its verification targets would all survive
=> P3's own implementation would not. That remains D7, OPEN.
```

---

# 20. Production Readiness

Full matrix in `production-readiness.md`. **Status words only — no score.**

| Area | Status |
|---|---|
| P1 constant offset | **PASS** |
| Measurement | **PASS** |
| State semantics | **PASS** |
| Real PCM apply | **PASS** |
| Six channel pairs | **PASS** |
| Independent verification | **PASS** (one disagreement recorded) |
| Gap semantics | **PASS** |
| Gap policy | **DEFERRED** |
| Coordinate policy | **DEFERRED** |
| Residual acceptance | **NOT ESTABLISHED** |
| Error handling | **PASS** |
| Chunk invariance | **PASS** |
| Memory | **PASS** |
| Long-run stability | **PASS** |
| Performance | **PARTIAL** — apply measured; pipeline unmeasured |
| Default path | **PASS** |
| Compatibility | **PARTIAL** — everything answerable without wiring is done |
| Documentation | **PASS** |
| Provenance | **PASS** |

```text
Fixed offset:              VALIDATED
Time-varying measurement:  VALIDATED
Real PCM apply:            VALIDATED
Six-pair E2E:              VALIDATED
Independent verify:        VALIDATED
Gap semantics:             VALIDATED
Gap policy:                DEFERRED
Coordinate:                DEFERRED
Residual acceptance:       NOT ESTABLISHED
Default path:              PASS
Production feature:        OFF

Overall:                   READY FOR PRODUCTION POLICY
```

**Not `READY FOR CONTROLLED PRODUCTION`** — that needs an approved policy.
**Not `PARTIAL` or `BLOCKED`** — nothing P4 was asked to build is incomplete.

---

# 21. Remaining Unknowns

| # | unknown | why it is unknown | what would close it |
|---|---|---|---|
| 1 | **which gap policy ships** | a product trade-off between uncorrected alignment and invented structure; the ranking instrument is policy-invariant | D1 + D5, product |
| 2 | **which coordinate contract** | the two are symmetric in error magnitude; the choice is anticipate vs lag | D2, product + engineering |
| 3 | **residual acceptance** | no reference for "good enough"; no calibrated σ; the accepted population is ~20 % on four pairs | D3, plus R1–R3 |
| 4 | **end-to-end pipeline cost** | never measured through the production pipeline; the module is not wired in | an authorised integration + measurement |
| 5 | **downstream representability of `UNRESOLVED_GAP`** | depends on consumers **outside this repository**; **nobody has looked at them** | D6, whoever owns the downstream format |
| 6 | **the C1306 ch3→ch2 instrument disagreement** | +70 vs +8.46 samples, `fine_fit_r2 = −0.21`; neither instrument is privileged | a third method or a controlled fixture (R1) |
| 7 | **a calibrated per-window uncertainty** | would need a known-truth repeat at several bandwidths that nobody has run | R3 |
| 8 | **whether the one worsened pair is material or a coverage artefact** | not separated | a per-pair coverage sweep (R2) |
| 9 | **behaviour on material outside C1305 / C1306** | only two corpora were ever measured | more corpora |
| 10 | **the 15 s attribution offset** | a measurement decision, not a correction decision; fixing it changes the validity population | D3 in the P3 register, still open |
| 11 | **`work/` durability (D7)** | the P3 implementation is gitignored | a repository-layout decision, repository owner |
| 12 | **the P3.2 ↔ P3.10/P3.11 contradiction** | recorded by the cleanup pass, resolved by nobody | not P4's to close; recorded in `evidence-freeze.md` §8 |

### And the ten P3 summary discrepancies

`evidence-freeze.md` §7b records **ten** places where a P3 summary range does not
reproduce from that phase's own detail table — including the coordinate figures
(P3.10's `670`/`79` samples and `30.0 s` against P3.11's `732`/`211` and
`20.0 s`), the median-improvement range, and the tail ranges. They are recorded
with both values. **P4 relies on neither side of any disputed figure for a
production decision**, and re-derives from raw artifacts or omits the claim.

---

# 22. Product Decision Boundary

**This is the phase's most important output.**

```text
engineering correctness   !=   product policy
```

P4 proved the mechanism works. **That does not mean it should be on.**

### What engineering established

```text
the measurement is reproducible to the bit and exact against a model
the apply reproduces the plan to within 1.96 samples, always
six real channel pairs run end to end, byte-for-byte identical to P3.11
the state machine is honest: no unmeasured window claims a measurement
chunk invariance, bounded memory and loud failure are all demonstrated
```

### What engineering cannot establish

```text
whether the corrected output is good enough for any purpose
whether uncorrected alignment inside a gap is worse than invented structure
whether a correction should anticipate a change or lag it
whether any consumer can represent an uncertainty
```

**None of the second list follows from any item of the first.**

### The decisions, and the ordering

| id | question | owner | order |
|---|---|---|---|
| **D5** | what is a correction allowed to *claim*? | product | **first** — shapes the API |
| **D6** | can a downstream consumer represent `UNRESOLVED_GAP`? | downstream format/API owner | **second** — can remove an option from D1 |
| **D1** | which gap policy ships? | product | third |
| **D2** | which coordinate contract? | product + engineering | third |
| **D3** | what residual is acceptable? | product | fourth |
| **D8** | is enablement authorised? | product | **last** — the only one that changes production behaviour |

**D8 cannot be answered before D1/D2/D5, because the answer to D8 is implemented by
the others.** And D6 can remove an option from D1: *if a downstream consumer cannot
represent uncertainty, `explicit_state` is unavailable regardless of its merits,
and D1 collapses to a choice among the others.*

### P4's boundary, stated plainly

```text
P4 did NOT choose a gap policy
P4 did NOT choose a coordinate contract
P4 did NOT invent a residual threshold
P4 did NOT enable anything
P4 did NOT change any default behaviour
P4 did NOT wire the module into production
P4 did NOT push, merge, or tag
P4 did NOT modify any historical P3 report
P4 did NOT touch the user's nine modifications
```

### And what P4 did instead

```text
built the mechanism so that ANY of those answers is a declared parameter
    rather than a rewrite
proved it correct against P3's own stored outputs, bit for bit
made the default path structurally unreachable
built the gate that detects silent wrongness
documented every tradeoff without picking a side
left every product decision open, and named its owner
```

### Final status

```text
P4 engineering infrastructure      = COMPLETE

  180-case regression, 0 silent-wrong
  30/30 byte-exact reproduction of the P3.11 corrected PCM, both coordinate
      contracts, every online gap policy
  626/0 existing unit, 27/0 P1 guard, 71/0 P3.11 coordinates
  memory bounded across a 120x stream range, growth bounded, control discriminating
  12/12 hash re-verification: nothing P4 must not have changed, changed

production default enablement      = DEFERRED  (requires D1, D2, D5, D8)

Overall                            = READY FOR PRODUCTION POLICY
```

**This is a legal and expected endpoint.** `P4-HANDOFF.md` and the brief's §40 both
name it as such.

---

## Appendix — artifacts

| path | what |
|---|---|
| `core/audio_sync_tv.py` | the production module (tracked) |
| `tests/audio_sync_p4/test_production_sync.py` | 180-case regression (tracked) |
| `docs/audio-sync-p4/` | this phase's documentation (13 documents) |
| `work/p4/verify_p311_reproduction.py` | the byte-exact reproduction verifier |
| `work/p4/memory_and_performance.py` | memory / performance / long-run harness |
| `work/p4/hash_verify.py` | hash re-verification of everything P4 must not change |
| `work/p4/current-state-audit.md` | Stage 0 audit |
| `work/p4/p311-reproduction.json` | 30 per-arm reproduction results |
| `work/p4/memory-performance.json` | the memory / performance report |
