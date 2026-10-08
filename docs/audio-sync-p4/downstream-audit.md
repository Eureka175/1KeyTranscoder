# P4.1 — Downstream consumer audit (D6)

**Publishable record of the D6 examination.** Full working detail is under
`work/p4.1/`; this document is the tracked artifact.

| | |
|---|---|
| phase | **P4.1** — Downstream Consumer Audit |
| base commit | `a12e0c55ebfd81034e0da1ec2cf0087d4b79c6dd` |
| production code modified | **NO** — proposal only |
| user-owned files | **9 / 9 byte-identical** |
| **D6 result** | **PARTIAL** |

---

## 1. What D6 asked

`docs/audio-sync-p3/OPEN-DECISIONS.md` D6:

> **Whether an `UNRESOLVED_GAP` is representable downstream** — Status `OPEN`,
> depends on consumers **outside this repository**. … If a downstream consumer
> cannot represent uncertainty — if the only thing it can accept is a number per
> sample — then `explicit_state_as_output` is **unavailable regardless of its
> merits**, and D1 collapses to a choice among the others. **Nobody has looked at
> the consumers.**

**Two corrections P4.1 makes to the question itself:**

1. **The decisive consumers are inside this repository.** External consumers only
   matter *after* the internal production model can receive the state at all.
   Looking outward first would have been looking in the wrong place.
2. **The feared constraint does not hold.** The consumers that *execute* audio
   accept only a number, and that is **correct** — they are state-neutral
   executors by design. The consumers that would need to state uncertainty (the
   model, the report, the readiness gate) can accept more. The gap is on the
   **producer/model** side, and it is a field, not an architecture.

---

## 2. The chain, from the code

```text
  [research / P4  -  NOT WIRED IN]
  CorrectionPlan  (core/audio_sync_tv.py)      per-WINDOW, TRUSTED/UNRESOLVED_GAP/REENTRY
        |
        X   no data path, no target field, no enum member
        |
  ------------------------------------------------------------------
  [production]
  channel_sync report row  (core/channel_sync.py)    one decision, one shift, per TRACK
        v
  AudioSyncResult + SyncStatus  (core/audio_models.py:636, :150)   9 members, no window axis
        v
  AudioTrack.refresh_sync()  (core/audio_models.py:1153)   most-conservative aggregation
        v
  AudioChannelRef  (core/audio_plan.py:181)          sync_status string
        v
  ChannelTimeline  (core/audio_timeline.py:468)      geometry + sync_status + sync_application
        v
  _effective_offset()  (core/audio_timeline.py:233)  apply / none
        v
  PCM render -> encoder -> AudioInputRef -> OutputComposer -> MP4
```

---

## 3. Findings

### F1 — The production sync model has no per-window axis

`AudioSyncResult` (`core/audio_models.py:636`), 14 fields, none for coverage:
`status, offset_samples, offset_ms, quality, anchor, reason, drift_ppm, constant,
polarity, usable_frames, rms_dbfs, algo_version, source, warnings`.

`quality` is the estimator's confidence **in the delay**, not a statement about
how much of the track was measured. `usable_frames` counts estimator frames, not
plan windows. `warnings` is free text a consumer cannot branch on.

### F2 — `SyncStatus` cannot express a *partial* measurement

All nine members (`core/audio_models.py:150`): `not_processed`,
`not_applicable`, `already_aligned`, `success`, `low_confidence`, `non_constant`,
`out_of_range`, `recheck_failed`, `failed`.

**Precision correction, made after adversarial review.** An earlier draft said
"no member meaning *measured but not trusted*". That is **overstated**:
`LOW_CONFIDENCE` is exactly a number that exists and is distrusted, and the apply
gate refuses it (`audio_timeline.py:251`). What no member expresses is
**"trusted for some windows, not others"** — i.e. **partial**.

A window-level `UNRESOLVED_GAP` is a **partial** condition **inside** an otherwise
successful track. There is no member for it, and therefore
`AudioTrack.refresh_sync()` has no member to aggregate to.

### F3 — A 48.6 % policy-filled track reports `SUCCESS` with no discoverable coverage

Reproduced on the **real** C1305 ch3→ch4 plan (481 windows: 193 `TRUSTED` /
234 `UNRESOLVED_GAP` / 54 `REENTRY` — P3.11's recorded split):

```text
ChannelSyncReport.resolve(row) -> status='success', offset_samples=296.0
AudioSyncResult.applied        -> True
coverage discoverable          -> None
per-window state discoverable  -> False
_effective_offset()            -> 296, applied to the WHOLE track
```

A consumer asking *"was this track fixed?"* gets **True**. It has no way to ask
*"how much of it was measured?"*

**Severity, from all six pairs:**

| corpus | pair | unresolved |
|---|---|---:|
| C1305 | ch3→ch1 | **80.7 %** |
| C1305 | ch3→ch2 | **78.8 %** |
| C1305 | ch3→ch4 | **48.6 %** |
| C1306 | ch3→ch1 | **80.6 %** |
| C1306 | ch3→ch2 | **80.6 %** |
| C1306 | ch3→ch4 | **26.1 %** |

**Not one pair is fully measured.** Under the default `hold` policy a
partially-measured track is the **normal** case, not an edge case.

### F4 — It is the exact failure mode requirement U1 names

`docs/audio-sync-p3/PRODUCTION-REQUIREMENTS.md:63`:

> **The failure mode this requirement exists to eliminate:**
> `number exists == measurement exists`.

F3 is the **track-level form** of that failure: a track-level `SUCCESS` plus a
number, with no statement of how much of it was measured.

### F5 — The loss is LIVE at track granularity, not merely armed

**This finding was corrected by adversarial review, and the correction was
verified independently in this session.** The first version of this audit said
*"production writes one scalar shift per whole track, so a partially-measured
track cannot occur today"*. That is true of the **shift** and **false of the
evidence behind it**.

`core/channel_sync.py`:

```text
:706-709   coverage = e.usable_frames / max(1, len(e.frames))
:749       elif coverage < eff["min_usable_fraction"]:      (0.6)
:751-767       adopts the WIDE estimate on usable_frames >= 5 and
               confidence >= 0.3 -- and NEVER re-checks coverage on it
:818           rows[i] = est_row(..., "fixed", None)
               -> SyncStatus.SUCCESS, reason is None
```

**The gate that fired is not applied to the estimate that satisfies it**, and
`coverage` is written into **no** row (`plain_row` `:490-514`, `est_row`
`:516-565`).

Shipped real-corpus evidence — `tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`,
all 11 `fixed` rows: `usable_frames` = **5, 12, 17, 22, 44, 48, 61, 63, 108, 137,
179**, `reason` empty on every one. Geometry (`min_audio_seconds = 2.0` at `:79`,
`frame_ms = 200` / `hop_ms = 100` at `:85-86`) means a ≥ 2 s track yields
**≥ 19 windows**, so a re-checked `min_usable_fraction = 0.6` (`:91`) demands
**≥ 12 usable frames**. **`usable_frames = 5` is arithmetically impossible under a
re-checked gate.**

Two further drop points, both verified:

```text
core/audio_sync.py:967        total_frames = len(pair.frames)   computed
core/audio_sync.py:684        serialised
core/audio_sync.py:1069-1085  NOT copied into AudioSyncResult; nothing reads it
production/output.py:439-442  keeps statuses/offsets, drops usable_ and total_frames
```

The P2 path also has **no coverage gate at all** (`audio_sync.py:980-996` gates on
absolute counts only) and sets `SUCCESS` at `:1010-1011`.

```text
per-WINDOW form  : unreachable today (shift_stream is scalar)
track-LEVEL form : LIVE — "corrected, and we do not say how much was measured"
```

### F5b — A partial-ness signal already reaches a consumer

`result_scope` ∈ `{file, partial}` is written into the report
(`channel_sync.py:425`) and consumed by `1kt.py:1577`. Track-level, and dropped by
`ChannelSyncReport.__init__` while surviving on the raw `.report` dict — but it
**does** reach the product surface. A mechanism for partial-ness already exists;
it carries no measurement quantity.

### F6 — There is NO `IMPLICIT_TRUST` defect in the apply path

The brief's risk #2 is `if shift is not None: apply()`. **The production code does
not do that.** `core/audio_timeline.py:233-254` requires an explicit status:

```python
if _sync_status(sync) != SyncStatus.SUCCESS.value:
    return 0, raw           # 未测量/失败/已对齐 -> 不施加任何移位
```

Seven non-success statuses were probed, each carrying `offset_samples=999`; **all
seven were refused**. The production code already distrusts a number without a
status — P4's value/state separation is the idiom it already uses, just at track
resolution instead of window resolution.

### F7 — The downstream chain that is state-free is CORRECTLY state-free

| layer | verdict |
|---|---|
| PCM render / `audio_mix` | consumes a resolved integer — correct |
| `EncodedAudioOutput` | codec identity only — correct |
| `AudioInputRef` / `OutputComposer` | a `-map` selector only — correct, and it **cannot** re-infer state because it never sees a shift |
| MP4 container | **not required** — `PRODUCTION-REQUIREMENTS.md` §5 omits the propagation requirement for **lack of information** (D6 is `OPEN`, not `DECIDED — no`), and neither G1 nor G2 reaches the container |

**No change is proposed to any of these.** Asserted by test T6.1–T6.5 so a future
change that pushes state downward is caught.

### F8 — One real contract correction

`ChannelTimeline.to_dict()` emits `sync_application` **only when it is
non-default** (`audio_timeline.py:578`). P4.1's first test asserted it was always
present, which would have encoded a contract the code does not have. Both
branches are now asserted. Recorded because the defect was in the checker.

---

## 4. The four D6 classifications, each with its evidence

### D6-A — explicit state survives where required

**NOT YET.** The producer knows everything; the production model cannot receive it.
Blocked on the D1 patch.

### D6-B — state is intentionally terminated at a safe boundary

**ACHIEVABLE, and it is the right resolution.** The requirements document does not
ask for container-level propagation; U1 asks that **a production object** express
the states. So the state may terminate at the production sync model as a
*summary*, touching nothing below the timeline.

### D6-C — dangerous state loss

**PRESENT, and LIVE at track granularity** (corrected from "latent" after
adversarial review — see F5). Evidence: `work/p4.1/state-loss-repro.md`,
`work/p4.1/consumer_fixture.py` (11 checks, 1 FAIL), and
`tests/audio_sync_p4/test_downstream_state.py` T5.4/T5.5 (held open as XFAIL
owned by D1).

### D6-D — architecture cannot represent required uncertainty

**REFUTED.** The required uncertainty is representable by **one existing object
plus two optional fields and one enum member**. `work/p4.1/state-ownership.md`
shows every layer's requirement is satisfiable without redesigning
`AudioTimeline`, `AudioPlan`, the PCM pipeline or `OutputComposer`.

---

## 5. Serialization

`object → dict → JSON → object` is **required only where the architecture crosses
a serialization boundary**:

| object | serializer | state across it | required? |
|---|---|---|---|
| `CorrectionPlan` | **none** | in-memory only | **no** → `STATE NOT REQUIRED PAST LAYER: the producer` |
| `AudioSyncResult` | `to_dict`/`from_dict` (`audio_models.py:705`) | status — **round-trip verified** | **yes** |
| `ChannelTimeline` | `to_dict` (`audio_timeline.py:562`) | `sync_status` — **verified** | **yes** |
| `AudioChannelRef` | `audio_plan.py:181-240` | `sync_status` | **yes** |

**No serializer was added to `CorrectionPlan`.** The brief's §24 names that
temptation; P4.1 records the legal conclusion instead of adding fields so a test
can pass.

---

## 6. D6 Decision

```text
D6 = BLOCKED
```

### Why `BLOCKED` and not `PASS`

A production consumer **cannot** today distinguish *"corrected with a measured
value"* from *"corrected with a policy-filled value"* on a partially-measured
track — and such tracks are **already shipping**: `channel_sync` computes
measurement coverage, gates on it, adopts a fallback whose coverage is never
re-checked, and writes `decision="fixed", reason=None`. The shipped real-corpus
baseline contains an `applied` track with **5 usable frames** where the gate's own
arithmetic requires **≥ 12** (F5).

### Why `BLOCKED` and not `PARTIAL`

`PARTIAL` would mean "carry on with the work that does not depend on the missing
piece". That is **not** available, because the missing piece is no longer a
future-wiring concern:

```text
the loss is live in production NOW
the fix is a production change
P4.1's scope forbids production changes (the brief's section 1)
=> the phase cannot close it, and cannot honestly report progress toward it
```

Per the brief's §37, a failed D6 must mark `production enablement = BLOCKED` while
the audit, contract proposal, risk documentation, tests and readiness analysis
**continue** — and they did. The blocker is narrowly: *a live track-level
epistemic loss cannot be fixed from this phase.*

### Why not `DANGEROUS LOSS` alone as the whole answer

Two further things are true and must not be lost:

1. **The architecture CAN represent the required uncertainty** (F7 / D6-D
   refuted) — one existing object plus two optional fields and one enum member.
2. **The safe termination boundary is identified** — the production sync model,
   as a summary (D6-B achievable).

### Consequence

```text
D6 = BLOCKED  ->  D1 stands as a SPECIFICATION (exactly what closes D6)
              ->  D8 (enablement) = BLOCKED
              ->  and separately: the live track-level loss is a production
                  defect independent of D8, which P4.1 could not fix in scope
```

Per the brief's §50, `BLOCKED` means: **state the blocker clearly and do not
pretend D1 was completed as an implementation.** D1's contract is specified and
its patch is designed; neither is applied.

---

## 7. What would clear the blocker

Two groups, and the distinction matters: the first is contract work (D1's scope),
the second is **live defects** the audit surfaced as a side effect.

```text
(A)  D1 contract patch
        AudioSyncResult + measured_windows, total_windows, policy
        SyncStatus      + PARTIALLY_MEASURED
        refresh_sync()  + one branch

(B)  live drop points
        audio_sync.py:1069-1085   copy total_frames (currently unread)
        channel_sync.py:706-709   write coverage into the report row
        channel_sync.py:751-767   re-check coverage on the adopted wide
                                  estimate before adopting it   [the defect that
                                  makes the loss live]

(C)  T5.4 and T5.5 flip from XFAIL to PASS
        (the suite reports XPASS and exits 1 if they start passing without
         being promoted, so this cannot be skipped silently)

(D)  a partially-measured track reports coverage < 1 to a consumer
```

**(A), (B) and (D) are production changes and are out of P4.1's scope** — the
brief's §1 restricts P4.1 to `docs/audio-sync-p4/`, `work/p4.1/` and `tests/`.
All are **proposed, not applied**.

`(B)` is **not** part of the D6 contract. It is a defect in shipped code that this
audit found as a side effect, recorded because suppressing it would be exactly the
"hide the finding" behaviour the brief forbids — and left unfixed because closing
it changes production behaviour and needs its own authorisation and regression.

---

## 8. Verification

| # | validation | result |
|---|---|---|
| W1 | D6 suite | **53 cases — 45 PASS, 0 FAIL, 2 XFAIL (owner D1), 6 controls discriminating** |
| W2 | P4 suite unaffected | **180 cases, 0 silent-wrong** |
| W3 | state trace | **24 probes, 12 cannot-represent, 4 boundary-fatal** |
| W4 | consumer fixture | **11 checks, 1 FAIL — the finding** |
| W5 | real corpus | **6/6 pairs reproduce P3.11's `T/U/R`** |
| W6 | hash re-verification | **14 / 14 MATCH** |

Detailed record: `work/p4.1/verification.md`.

---

## 9. Artifacts

| path | content |
|---|---|
| `docs/audio-sync-p4/d1-uncertainty-contract.md` | the contract D6 says is missing |
| `docs/audio-sync-p4/p4.1-provenance.md` | identity, hashes, reproduction |
| `tests/audio_sync_p4/test_downstream_state.py` | the D6 regression suite |
| `work/p4.1/current-state-freeze.md` | Stage-0 freeze and ownership split |
| `work/p4.1/downstream-architecture-audit.md` | the chain, from the code |
| `work/p4.1/state-ownership.md` | per-layer ownership table |
| `work/p4.1/consumer-classification.md` | A / B / C / D consumers |
| `work/p4.1/state-loss-repro.md` | the six required fields of the loss |
| `work/p4.1/runtime-trace.md` | the 24 probes and the visibility boundary |
| `work/p4.1/synthetic-consumer-test.md` | the fixtures and the falsifiability assertion |
| `work/p4.1/real-corpus-state-audit.md` | all six pairs |
| `work/p4.1/D6-RESULT.md` | the structured D6 result |
| `work/p4.1/verification.md` | every validation with its command |
| `work/p4.1/FINAL-REPORT.md` | the phase report |
