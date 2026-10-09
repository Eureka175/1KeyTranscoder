# Audio Sync — the production contract

**This is the single normative entry for what Audio Sync is allowed to do in
production.** Everything in it is checked against the code at the commit named in
[`documentation-provenance.md`](documentation-provenance.md); where a document and
the code disagree, **the code is the contract** and the disagreement is recorded
in [`known-limitations.md`](known-limitations.md).

Machine-readable sources, in order of authority:

```text
core/audio_models.py :: SYNC_STATUS_PATH            the state machine
core/audio_models.py :: correction_eligibility      the decision
core/audio_sync.py   :: FixedDelayPlan              the correction plan
core/channel_sync.py :: DEFAULTS / effective_opts   the evidence gate
1kt.py               :: --channel-sync / --audio-plan   the entry points
```

---

## 1. Scope: what is enabled, and what is not

The name "Audio Sync" covers **three** paths with three different enablement
states. Collapsing them is the error this document exists to prevent.

| path | grain | production entry | state |
|---|---|---|---|
| **P1 fixed delay** `core/channel_sync.py` → `core/sync_fix.shift_stream` | one integer shift per track | `--channel-sync`, `--channel-sync-transparent` | **ENABLED** (opt-in) |
| **P2 constant offset** `core/audio_sync.py` → `AudioChannel.sync` → timeline | one integer offset per channel | `--audio-plan` + an explicit `sync.reference` | **ENABLED** (opt-in) |
| **P4 time-varying** `core/audio_sync_tv.py` | one shift per window | none | **NOT ENABLED** — imported by no production module, asserted by test |

```text
ENSEMBLE: the validated fixed integer delay is in production.
          The per-window time-varying correction is not.
```

The per-window path is off **structurally**, not by a flag: no production module
imports it, and that is asserted from the import statements (not from a text
search) in three suites. Adopting `core/audio_sync_tv.CorrectionPlan` would also
decide D2 and D5, because its constructor takes `policy=` and `coordinate=` —
see §7.

### Not enabled, and not to be described as enabled

```text
drift correction        NOT STARTED
resampling              NOT STARTED
time-stretch            NOT STARTED
adaptive coarse-to-fine NO ADVANTAGE DEMONSTRATED (P3.12) — not adopted
fractional correction   NOT NEEDED (P3.6) — not adopted
new estimator           NOT STARTED
```

`algo_version` is **`2.3.0-p1`** on both enabled paths.
`core/audio_sync_tv.ALGO_VERSION` is `2.4.0-p4` and belongs to the disabled path.

---

## 2. State vocabulary

Nine members, all pre-existing. **No new member was minted by P1–P4.4.**

```text
not_processed  not_applicable  already_aligned  success  low_confidence
non_constant   out_of_range    recheck_failed   failed
```

| status | value trusted | may correct | is "unresolved" | reason code if refused |
|---|---|---|---|---|
| `success` | **yes** | **yes** | no | — |
| `already_aligned` | yes | no | no | `sync_already_aligned` |
| `not_processed` | no | no | no | `sync_not_processed` |
| `not_applicable` | no | no | no | `sync_not_applicable` |
| `low_confidence` | no | no | no | `sync_low_confidence` |
| `non_constant` | no | no | no | `sync_non_constant` |
| `out_of_range` | no | no | no | `sync_out_of_range` |
| `recheck_failed` | no | no | no | `sync_recheck_failed` |
| `failed` | no | no | no | `sync_failed` |

Three properties are asserted by test (`test_production_semantics.py`, SM.1–SM.4):

```text
every member has exactly one row in SYNC_STATUS_PATH
nothing may enter the correction path unless its value is trusted
no state is simultaneously trusted and unresolved
SUCCESS is the ONLY correcting member
```

`already_aligned` is ineligible **without being distrusted**: the value is
trusted, there is simply no correction to apply.

### The brief's five names, mapped onto real code

| the brief's term | what exists in code |
|---|---|
| `SUCCESS` / trusted | `SyncStatus.SUCCESS` — the only correcting member |
| `LOW_CONFIDENCE` | `SyncStatus.LOW_CONFIDENCE` — *a number exists and is not trusted* |
| `UNRESOLVED_GAP` | **not** a `SyncStatus`. It is a per-window state in `core/audio_sync_tv.GapState` (`TRUSTED` / `UNRESOLVED_GAP` / `REENTRY`), on the **disabled** path |
| `REENTRY` | likewise a `GapState`, not a `SyncStatus` |
| `FAILURE` | the family `non_constant` / `out_of_range` / `recheck_failed` / `failed` |

> **This mapping is a real distinction, not pedantry.** A consumer looking for
> `UNRESOLVED_GAP` among `SyncStatus` members will not find it, because the track-
> level model has no window. That gap is exactly what P4.1's D6 audit recorded as
> `BLOCKED` and what P4.3 left open as `T5.4b` (owner D8). It is **not** a
> documentation error to be smoothed over.

---

## 3. `correction_eligibility()` — the decision

```python
core.audio_models.correction_eligibility(
    sync, *, application=None, gate=None
) -> CorrectionEligibility
```

One predicate. Before P4.4 the question was answered by **each consumer
separately**, and **none of them consulted the evidence**:

| consumer | decided from (before P4.4) | after |
|---|---|---|
| `core/audio_timeline._effective_offset` | `status == SUCCESS` | delegates |
| `core/audio_sync.apply_sync_result` | `status == SUCCESS` | delegates |
| `production/output.apply_alignment` | `status == "success"` | builds a `FixedDelayPlan` |

`sync` may be an `AudioSyncResult`, an `AudioChannelRef`, or a report `dict` —
the same three carriers the rest of the audio layer already accepted.

### 3.1 Necessary conditions

`eligible is True` requires **all** of:

1. the status is known and `SYNC_STATUS_PATH[status]["may_enter_correction"]`;
2. an offset is present, finite, and inside the ±2³¹ integer-shift guard
   (`SYNC_OFFSET_LIMIT`);
3. **if the result states evidence**, that evidence satisfies the production
   gate (§4);
4. the caller did not pass `SyncApplication.NONE`.

Otherwise `eligible is False` and `reason` is one of:

```text
sync_not_processed      sync_not_applicable    sync_already_aligned
sync_low_confidence     sync_non_constant      sync_out_of_range
sync_recheck_failed     sync_failed            sync_status_unknown
sync_offset_missing     sync_offset_invalid
sync_evidence_insufficient                      sync_application_none
```

Every state-derived code comes from `ELIGIBILITY_REASON_BY_STATUS`, which is
asserted to have exactly the same members as the non-correcting statuses — so a
new status cannot be added without an eligibility answer.

### 3.2 The invariant

```text
eligible is True   =>  offset_samples is an int
eligible is False  =>  offset_samples is None
```

**`refused` and `holding a usable number` is unrepresentable.** This is not a
check that a later reader could skip; the number is simply absent from the
verdict. It is the structural form of
`docs/audio-sync-p3/PRODUCTION-REQUIREMENTS.md` **U1**, whose named failure mode
is *"a number exists == a measurement exists"*.

`FixedDelayPlan` carries the same property: `offsets()` contains only eligible
entries, and `refusal_reasons()` explains every other one.

### 3.3 `offset_samples` vs `raw_offset_samples`

| field | meaning | may be used to move a sample? |
|---|---|---|
| `CorrectionEligibility.offset_samples` | the integer shift **permitted** | **yes** — and it is `None` when not eligible |
| `CorrectionEligibility.raw_offset_samples` | the number as it exists, including on a refused result | **no — audit and reporting only** |
| `AudioSyncResult.offset_samples` | the measured/decided value on the model | only through the eligibility gate |
| `PairSyncEstimate.offset_samples` | the P2 estimate | only through the eligibility gate |
| `PairSyncEstimate.eligibility_reason` | why a target was refused | reporting |

The same split appears in `core/audio_timeline._effective_offset`, which returns
`(applied_offset, raw_float)`: the first is `0` whenever the verdict is not
eligible, the second still carries the measured number so a report can say
*"measured, not applied"*.

A refused **correction** therefore reaches the bytes: an ineligible result makes
`shift_stream` produce output **bit-identical to the source**.

---

## 4. The evidence gate

Thresholds are **derived, never literal**, and have one source per path:

```text
core/channel_sync.DEFAULTS   min_usable_frames    = 5
                             min_confidence       = 0.3
                             min_usable_fraction  = 0.6
```

* `ChannelSyncReport.evidence_gate` reads the gate the **report states** (the
  producer writes the values actually in force, so an `opts` override is not
  re-interpreted by the defaults), falling back to `effective_opts()`.
* `core/audio_sync._ESTIMATE_DEFAULTS` carries the same three values, and
  `_assert_gate_matches_production()` compares them against
  `core.channel_sync.DEFAULTS` **at import time**. A divergence raises rather
  than letting the "same calibration" comment quietly become false — which is
  exactly what had happened before P4.3.

### Evidence propagation, and where it stops

```text
channel_sync report row   rows[i]["evidence"] = {usable_frames, total_frames,
                                                 coverage, confidence}
        v
AudioSyncResult           usable_frames, total_frames, coverage,
                          measurement_coverage (property)
        v
AudioTrack                sync_coverage  (MIN over channels — the worst one wins)
        v
AudioChannelRef           sync_coverage, sync_status, sync_offset_*
        v
AudioPlan.sync_of         coverage carried
        v
AlignmentOutcome          evidence[channel] = {..., eligible, eligibility_reason}
                          + a log line naming the coverage of a partially
                            measured corrected track
        v
ChannelTimeline           offset_samples = THE GATED VALUE, raw_offset_samples
        |                 = the number as measured     <-- SAFE TERMINATION
        v
audio_mix / audio_route / PCM / encoder / composer
                          state-free, and that is ASSERTED
```

`None` means **not stated**, never `0`. Collapsing them would make every
un-updated caller look like a total failure — the same sentinel error U1
prohibits. A result that does not state coverage is therefore **not
retroactively failed**: `evidence_checked` is `False` and the verdict is
unchanged.

`ChannelTimeline` is the point where a resolved integer is the *correct*
interface: the PCM and composition layers must not re-interpret `SyncStatus`,
`coverage` or `confidence`, and `AudioInputRef` is asserted to carry none of
them — so a future change that pushes state *down* into execution is caught.

---

## 5. Behaviour when not eligible

```text
missing evidence          ->  refused, reason named, NO offset returned
insufficient evidence     ->  refused (sync_evidence_insufficient)
LOW_CONFIDENCE            ->  refused (sync_low_confidence)
under-evidenced SUCCESS   ->  the resolver downgrades it on sight
UNRESOLVED_GAP            ->  cannot claim TRUSTED at all
invalid state             ->  refused (sync_status_unknown)
invalid offset            ->  refused (sync_offset_invalid)
invalid timeline / plan   ->  the existing error model RAISES (unchanged)
```

Nothing guesses, and nothing silently continues with a guessed correction.

### Fail-closed details worth stating

* **The resolver guard.** A report row that asserts `decision="fixed"` **while
  stating** coverage below the gate is downgraded to `low_confidence` with
  reason `evidence_insufficient`. This fires for historical or foreign rows —
  the current producer can no longer emit one.
* **The producer's own gate.** `run_channel_sync` never writes `fixed` unless the
  estimate it **actually adopted** passed all three checks, and the shifted
  track is independently re-measured (`recheck_residual`) before the row keeps
  its `shift_samples`.
* **`shift_stream` has no guard of its own, by design.** It is the mechanism; the
  guard lives at every caller, and the callers are enumerated and exercised.

---

## 6. The default path

```text
no --channel-sync, no --audio-plan  ->  audio handled exactly as before
```

Measured, not asserted: with every PCM entry point instrumented,
`produce_audio_output(plan=None, ...)` makes

```text
run_channel_sync 0   estimate_sync 0   apply_sync_result 0
resolve_timeline 0   AudioPCMReader 0                    total 0
wall 0.035 ms        file touched: no
```

It returns before constructing a reader, a timeline or a plan. The eligibility
predicate costs ≈2 µs per call and is not reached on this path at all.

**No new feature flag was introduced.** `--channel-sync` and `--audio-plan`
already were the explicit, default-OFF production path; the missing piece was a
contract, not a switch.

---

## 7. Still undecided — and deliberately not decided here

| id | subject | state | implementation default | semantic contract |
|---|---|---|---|---|
| **D2** (canonical P4 `D1`) | gap policy — what fills a window nobody measured | **DEFERRED** | `hold` | not frozen |
| **D3** | residual acceptance threshold | **NOT ESTABLISHED** | none exists | — |
| **D5** (canonical P4 `D2`) | coordinate contract — which join maps output sample → window | **DEFERRED** | `latest-start` | not frozen |

`core/audio_sync_tv.DECISION_STATUS` records the implementation default and the
semantic contract as **separate fields**, and a test asserts they stay distinct,
so "the default is X" cannot be read as "X is the product contract".

Reasons, in one line each:

* **D2** — no candidate dominates. The candidates separate only by which
  situation they fail in, and the discriminating rows are decided by the
  position of a discontinuity inside the gap, which is **not observable**.
* **D3** — no reference for "good enough" exists, and no calibrated per-window σ.
  **Production eligibility does not depend on a residual bound**, so the
  absence of a threshold does not block the enabled path.
* **D5** — choosing answers *"should a correction anticipate a change or lag
  it?"*, a product question. `latest-start` is the implementation default **for
  reproducibility**: it is the contract every P3.3–P3.8 real-corpus number was
  produced under.

> None of these is a documentation gap. They are open **product** decisions, and
> the code carries them as such.

**Decision-ID collision.** This brief, the canonical P4 corpus and
`d1-uncertainty-contract.md` use three different numberings for D1/D2/D5; the
P4.1 document swapped two of them. The full reconciliation is in
[`p4.3-production-semantics.md`](p4.3-production-semantics.md). No historical
document was rewritten to remove the collision.

---

## 8. What is validated, and by what

| claim | evidence | status |
|---|---|---|
| the fixed integer delay direction | `out[n] == in[n + rint(delay)]`, pinned by `tests/selftest/suites/audio_timeline.py` and re-derived in P4.4 | PASS |
| SUCCESS requires sufficient evidence | P4.2's gate on the adopted estimate + P4.3's resolver guard + P4.4's predicate | PASS |
| LOW_CONFIDENCE cannot silently correct | E2/E7 in `test_production_eligibility.py`; `_effective_offset` returns 0 | PASS |
| an unresolved value cannot become trusted | `CorrectionDecision(..., TRUSTED, source="policy")` raises | PASS |
| refusal reaches the bytes | the applier produces a bit-identical copy | PASS |
| real PCM correction on real A7M5 material | 4 known-answer cases, independent cross-correlation, residual 0 | PASS |
| the default path is untouched | 0 instrumented PCM calls | PASS |
| the per-window path is unreachable | AST import check across core/, production/, 1kt.py | PASS |

Per-suite counts, commands and the items that are **not** green are in
[`validation.md`](validation.md).

---

## 9. Where a maintainer goes next

| question | document |
|---|---|
| what did P3 research, phase by phase? | [`../audio-sync-p3/P3-RESEARCH-OVERVIEW.md`](../audio-sync-p3/P3-RESEARCH-OVERVIEW.md) |
| what is the P4 line, and in what order did it happen? | [`README.md`](README.md) |
| what exactly did P4.2 fix, and why was it live? | [`p4.2-evidence-integrity.md`](p4.2-evidence-integrity.md) |
| how does evidence travel, and where does it stop? | [`p4.3-production-semantics.md`](p4.3-production-semantics.md) §7 |
| what did D1 decide, and what did it not? | [`d1-uncertainty-contract.md`](d1-uncertainty-contract.md) §9 |
| what is enabled, and why is the rest not? | [`p4.4-production-enablement.md`](p4.4-production-enablement.md) |
| what is open, failing or blocked? | [`known-limitations.md`](known-limitations.md) |
| how do I reproduce any number here? | [`validation.md`](validation.md) |
| where did a promoted document come from? | [`documentation-provenance.md`](documentation-provenance.md) |
