# D1 — Explicit Uncertainty Production Contract (specification)

**Status: SPECIFIED, AND IMPLEMENTED BY P4.3 FOR THE FRAME-DOMAIN POPULATION.**
**Policy-neutral by construction.** See §9 for the implementation record; §8 is
the P4.1 statement of what was, at the time, not yet applied.

This document defines **what the production contract for epistemic uncertainty
must say**. It does not choose a gap policy, does not choose a coordinate
contract, and does not enable anything.

It is written because P4.1's D6 audit found the precise gap it fills:
`work/p4.1/state-loss-repro.md`.

> **D6 outcome: `BLOCKED`.** The audit found that the same failure mode already
> exists in shipped code at **track** granularity, independent of the time-varying
> path — `core/channel_sync.py` computes measurement coverage, gates on it,
> adopts a fallback whose coverage is never re-checked, and writes
> `decision="fixed", reason=None`. See `downstream-audit.md` §F5.
>
> This document's contract is **unchanged** by that finding; the finding adds a
> second, **live** drop point that the contract alone does not fix.

---

## 0. What this contract is, and what it is not

```text
IS      a statement of which epistemic states are part of the production
        contract, how they are named, serialized, logged and made visible
IS NOT  a decision about which gap policy produces those states
IS NOT  a coordinate-contract decision
IS NOT  an enablement decision
```

### 0.1 Policy-neutrality, stated as a rule (brief §34)

```text
state = UNRESOLVED_GAP   does NOT mean `hold`
state = UNRESOLVED_GAP   does NOT mean `no_correction_gap`
state = UNRESOLVED_GAP   means: this value was not measured
```

Two different policies that fill the same windows produce the **same** contract
state. That is correct and required: the contract reports *how much is
uncertain*; D1's sibling decisions (D2/D5) decide *what to do about it*.

A contract that leaked a policy name into the state would make D5 undecidable,
because the state would already encode its answer.

---

## 1. The vocabulary

Three states, already defined in P3 and implemented in P4. **No new state is
introduced** — inventing one would be a new product decision.

| state | meaning | what it claims | what it forbids |
|---|---|---|---|
| `TRUSTED` | this window's own measurement was accepted | a measurement supports this value | inventing a neighbouring value |
| `UNRESOLVED_GAP` | this window was refused; the value came from a policy | **nothing about the material** | claiming a measurement |
| `REENTRY` | first accepted window after a gap | a measurement supports this value, and the preceding window did not | retroactively amending the gap |

### 1.1 The one-sentence contract

> A correction value and its epistemic state travel together; a consumer must
> never be able to obtain one without the other.

That sentence is `PRODUCTION-REQUIREMENTS.md` U1, restated at production scope.

---

## 2. The contract's required surface

### 2.1 Requirement C-1 — the production object can express coverage

The production sync result must be able to state **how much of the track was
measured**, not only that it was corrected.

```text
MUST expose, for a corrected track:
    measured_windows : int      (windows whose value is backed by a measurement)
    total_windows    : int      (windows in the plan)
    -> derived: coverage = measured_windows / total_windows, in [0, 1]
MUST distinguish "not stated" from "0 %":
    an absent coverage is None, never 0
```

**Why `None` and not `0`:** `0` asserts "nothing was measured", which is a
claim. `None` asserts "this path does not state coverage", which is true of
every existing path. Collapsing them would make every un-updated caller look
like a total failure — the same sentinel error U1 prohibits.

### 2.2 Requirement C-2 — a success with holes is nameable

```text
a track that succeeded AND has unresolved windows MUST have a status
    distinct from a fully measured success
```

Without this, the aggregation in `AudioTrack.refresh_sync()` has no member to
take, which is the mechanism by which the loss currently happens.

### 2.3 Requirement C-3 — value and state are not separable

```text
MUST NOT be possible to obtain the shift without the state
MUST NOT be possible to obtain the state without knowing which policy filled
    the unresolved windows
```

The P4 producer already satisfies this (`CorrectionPlan.decision()` returns both
together; `CorrectionDecision` refuses contradictory construction).

### 2.4 Requirement C-4 — no sentinel

```text
a decision MUST NOT use null/None to mean "no measurement"
a nullable number cannot distinguish "0-shift measurement" from
    "no measurement"   (PRODUCTION-REQUIREMENTS.md U1)
```

### 2.5 Requirement C-5 — uncertainty is not error

```text
UNRESOLVED_GAP is a VALID state; it MUST NOT raise
an invalid execution MUST raise a distinct error type
```

Both already implemented in P4 and asserted by test (`T8.13`).

---

## 3. Serialization

### 3.1 What must survive a serialization boundary

| object | boundary crossed | must survive |
|---|---|---|
| `AudioSyncResult` | report → model → plan → timeline | `status`, coverage fields, `policy` |
| `ChannelTimeline` | timeline → report | `sync_status`, `sync_application` |
| `AudioChannelRef` | plan model → report | `sync_status` |

```text
MUST:  status and coverage survive to_dict -> JSON -> from_dict
MUST:  a round-trip preserve the coverage value EXACTLY, including None
MUST NOT: a round-trip turn None into 0, or a float into a rounded int
```

### 3.2 What must NOT be serialized

```text
MUST NOT: embed per-window state in the audio bytes
MUST NOT: embed it in a codec private field (AAC / Opus / PCM)
MUST NOT: require a new MP4 box
```

Rationale, from the requirements document rather than from caution
(`PRODUCTION-REQUIREMENTS.md` §5):

> | "an `UNRESOLVED_GAP` must be propagated to the downstream consumer" |
> **Depends on consumers outside this repository… The requirement cannot be
> written until that is known.**

The document **declines** to require container-level propagation. D1 inherits
that decision and does not reverse it.

### 3.3 `STATE NOT REQUIRED PAST LAYER`

```text
the producer (core/audio_sync_tv.CorrectionPlan) needs NO serializer.
```

It is a planning-time object, consumed in-process. Recorded explicitly so that
"no `to_dict`" is not read as a defect (brief §24).

### 3.4 Correction to the basis for 3.2

An earlier draft of this contract said the requirements document *"deliberately
declines"* to require container-level propagation. **Adversarial review corrected
that, and the correction is right:** the requirement is **omitted for lack of
information** — D6 is `OPEN`, not `DECIDED — no`, and
`PRODUCTION-REQUIREMENTS.md` §5 promises only that omissions are "stated as
omitted rather than softened".

The **conclusion stands** — no requirement obliges the MP4 to carry the state, and
neither **G1** (`:138-148`, which fixes the policy contract's OUT as *shift
sequence + state sequence + provenance*) nor **G2** (`:168-188`, which binds
*"any interface, report or schema that surfaces a gap fill"* to say
**not measured**) reaches the container.

**But the reason is weaker than "decided", and the scope of G2 is wider than
"a production object".** G2 in particular binds *any* interface that surfaces a
gap fill — so the contract must satisfy G2 wherever such a surface exists, not
only on `AudioSyncResult`.

---

## 4. Logging

### 4.1 What a production run must be able to say

Per task, at minimum:

```text
sync enabled              : bool
policy                    : the gap policy by name, or "none"
coordinate                : the join by name
measurement count         : windows backed by a measurement
trusted count             : TRUSTED windows
unresolved count          : UNRESOLVED_GAP windows
reentry count             : REENTRY windows
coverage                  : measured / total, or "not stated"
error count               : invalid executions (distinct from unresolved)
```

### 4.2 What logging must NOT do

```text
MUST NOT: print every window (the brief's §44: 不要高频打印所有窗口)
MUST NOT: report unresolved windows as errors
MUST NOT: report a coverage figure without naming its population
    (plan windows vs measurement windows vs residual grid — three different
     denominators, see policy-matrix.md §0.1)
MUST NOT: log a bare "sync OK" for a partially-measured track
```

### 4.3 The line that must exist

```text
a partially-measured track MUST produce a log line naming its coverage.
```

A run that corrects a track which is 80.7 % policy-filled and logs only
`applied` has reproduced the D6 defect in the log.

---

## 5. Consumer visibility

Derived from `work/p4.1/consumer-classification.md`.

| class | requirement |
|---|---|
| **A state-aware** (readiness gate, diagnostics, policy layer) | **MUST** be able to learn coverage from the production model |
| **B state-neutral executors** (PCM, apply) | **MUST NOT** be required to know state — a resolved integer is the correct interface |
| **C artifact writers** (encoder, WAV, composer, container) | **MUST NOT** be required to carry state |
| **D product-facing** (CLI, report) | **MUST** surface uncertainty when coverage < 1; **MUST NOT** need to for coverage == 1 |

### 5.1 The asymmetry this encodes

```text
the contract pushes uncertainty UP (into the model, the report, the log)
it does not push it DOWN (into execution)
```

That is the brief's §18 distinction — execution correctness vs epistemic
provenance — written as a contract.

---

## 6. The minimal patch this contract implies

**Design only. Not applied.**

Target: **one existing object, two explicit optional fields, one member.**
No duplicated model, no second `AudioPlan`, no second `Timeline`, no second sync
state (brief §29).

```text
core/audio_models.py :: AudioSyncResult
    + measured_windows: int | None = None
    + total_windows:    int | None = None
    + policy:           str | None = None      # WHICH policy filled the gaps
    @property measurement_coverage -> float | None

core/audio_models.py :: SyncStatus
    + PARTIALLY_MEASURED = "partially_measured"

core/audio_models.py :: AudioTrack.refresh_sync
    + one branch: SUCCESS + coverage < 1 -> PARTIALLY_MEASURED
```

### 6.1 Why `policy` is part of the contract and not cosmetics

P3.11 measured that `hold` and `explicit_state` produce **bit-identical corrected
PCM** and differ only in what they claim. A contract that records coverage but not
the policy cannot distinguish them either — and the whole point of the two-field
design is that the claim is as important as the value.

### 6.2 Feature-gating

```text
every new field defaults to None
`None` means "not stated" -> every existing path keeps today's behaviour exactly
only the time-varying path populates them
```

This satisfies the brief's §45 (*feature-gated, minimal, policy-neutral, default
behaviour unchanged*) **by construction**, without adding a flag or a CLI option.

### 6.3 What the patch must NOT do

```text
MUST NOT: change `applied` for existing paths
MUST NOT: change `SyncStatus.SUCCESS`'s meaning for existing paths
MUST NOT: add a member that any existing consumer must now handle
    (`PARTIALLY_MEASURED` is unreachable unless the new fields are populated)
MUST NOT: touch AudioTimeline, PCM, audio_mix, encoder, composer, container
MUST NOT: add a flag, a CLI option, or a second model
```

---

## 7. Verification this contract requires

The brief's §38 asks for regression coverage. Written as required tests, not as
implemented ones:

| # | test | asserts |
|---|---|---|
| R-1 | state preservation | a decided state reaches the production object unchanged in meaning |
| R-2 | state flattening | `TRUSTED` and `UNRESOLVED_GAP` with the same shift stay distinguishable across every boundary they cross |
| R-3 | shift/state distinction | the same `shift_samples` with two states is representable and does not compare equal semantically |
| R-4 | serialization round-trip | `object -> dict -> JSON -> object` preserves status and coverage exactly, including `None` |
| R-5 | dangerous state loss | a partially-measured track is **not** reported as a fully measured one |
| R-6 | safe state termination | the PCM / encoder / composer / container layers remain state-free **and that is asserted**, so a future change that pushes state down is caught |
| R-7 | no-silent-report | `shift != None` with `state = UNRESOLVED_GAP` must never yield `resolved=True` or `measurement_valid=True` without a stated semantic |
| R-8 | anti-silent-success | `consumer_count == 0` / `trace_count == 0` / `state_events == 0` must be `BLOCKED`, never `PASS` |

R-5 and R-8 are the two that matter most, and both are currently **failing by
construction** because the field does not exist.

---

## 8. Status of D1

> **Superseded in part by §9 (P4.3, appended). This section is the P4.1 record
> and is left as written.** Where it says "NOT APPLIED" / "NOT IMPLEMENTED",
> §9 states what P4.3 applied and what it deliberately did not.

```text
state vocabulary                    DEFINED (P3, unchanged)
required surface (C-1 .. C-5)       DEFINED
serialization rules                 DEFINED
logging rules                       DEFINED
consumer visibility rules           DEFINED
minimal patch                       DESIGNED, NOT APPLIED
required tests (R-1 .. R-8)         ENUMERATED, NOT IMPLEMENTED

coordinate policy                   DEFERRED  (D2)
gap policy                          DEFERRED  (D5)
enablement                          DEFERRED  (D8)
```

### 8.1 Why the patch is not applied in P4.1

Three independent reasons, any one of which would be sufficient:

1. the brief's §45 — *"默认先只提出 patch design。实际改生产代码必须等 D6 证据
   明确证明需要"* — and applying it requires the D6 evidence to be reviewed
   first, which is what this phase produces;
2. the field cannot be **populated** until the time-varying path is wired in, and
   wiring it in is **D8**;
3. P4.1's scope is `docs/audio-sync-p4/`, `work/p4.1/` and `tests/`, so a
   production edit is out of scope for this phase by the brief's §1.

### 8.2 The ordering, restated

```text
D6   proves the gap and states the minimal contract          <- this phase
D1   defines the uncertainty production contract             <- this document
D2   coordinate contract                                     DECISION REQUIRED
D5   gap policy                                              DECISION REQUIRED
D8   wire in + populate + enable                             later
```

**D1 can be COMPLETE while D2, D5 and D8 remain deferred.** That is the brief's
§51 outcome, and it is where P4.1 lands.

---

## 9. P4.3 implementation record (appended; §1–§7 unchanged)

**This section is new in P4.3. Nothing above was rewritten.**

### 9.1 What was applied, and why `measured_windows` became `total_frames`

§6 designed the patch as `measured_windows` / `total_windows` / `policy` — the
**plan-window** population. Implementing it revealed that D1 has two candidate
populations, and that only one of them has a producer:

| population | who produces it today | status |
|---|---|---|
| **measurement frames** of the adopted estimate | `core/channel_sync.py` writes `rows[i]["evidence"]` (P4.2) | produced, gated, **and dropped one layer later** |
| **plan windows** of a `CorrectionPlan` | `core/audio_sync_tv.CorrectionPlan` — imported by **no** production module | **no producer** |

§4.2 already forbids reporting a coverage figure without naming its population.
Adding `measured_windows` now would create a second quantity named "coverage"
with no producer to populate it, so P4.3 applied the **frame-domain** half and
left the window-domain half open.

Applied, therefore:

```text
core/audio_models.py :: AudioSyncResult
    + total_frames: int | None = None
    + coverage:     float | None = None
    + @property measurement_coverage -> float | None
      (stated value, else derived from usable/total, else None — never 0)

core/audio_models.py :: AudioTrack
    + sync_coverage: float | None = None     (min over channels, conservative)

core/audio_plan.py :: AudioChannelRef
    + sync_coverage: float | None = None     (product-facing traceability)

core/audio_models.py :: ChannelSyncReport
    + evidence_gate  (read from the report; else core.channel_sync.effective_opts)
    + the SUCCESS => evidence-satisfied guard in resolve()

core/channel_sync.py
    + the report states the gate it applied ("evidence_gate")
    + the recheck-failure row keeps its evidence (it was rebuilt and lost it)

production/output.py :: AlignmentOutcome
    + evidence: dict[channel_id, {usable_frames, total_frames, coverage,
                                  confidence, evidence_sufficient, applied}]
    + a log line naming the coverage of a partially measured corrected track
```

The names match `core.audio_sync.PairSyncEstimate`, which **already** carried
`usable_frames` and `total_frames`. The same measurement fact is not allowed two
names in two sync models.

### 9.2 No new `SyncStatus` member was needed

§6 proposed `PARTIALLY_MEASURED`. It is still not needed, and P4.3 did not mint
it: with the gate applied to the adopted estimate, an under-evidenced track is
never written `fixed` in the first place, so it resolves to the existing
`LOW_CONFIDENCE` — "a number exists and is not trusted". A guard in
`ChannelSyncReport.resolve` reclassifies a *stale or foreign* row that asserts
`fixed` while stating sub-gate coverage, using the **existing** reason code
`evidence_insufficient` from P4.2.

### 9.3 The backward-compatibility rule, made precise

```text
coverage is None   ->  "not stated"  ->  today's behaviour, exactly
coverage is stated ->  compared against the gate the REPORT names
coverage < gate while claiming SUCCESS -> the contradiction is removed
```

`None` is never converted to `0` in either direction, and `to_dict()` omits an
unstated field entirely, so an existing path's serialised output is
**byte-identical** (asserted: suite P.3, P.4). Replayed over the frozen
137-corpus baseline: **548 rows, 0 resolved status changes**.

### 9.4 Which requirements of §7 are now met

| # | test | status |
|---|---|---|
| R-1 | state preservation | **met** (suite C.1–C.4) |
| R-2 | state flattening | **met** (S3.3, S8.1: `source` distinguishes policy from measurement) |
| R-3 | shift/state distinction | **met** (S8.3: a policy-sourced TRUSTED is unconstructible) |
| R-4 | serialization round-trip | **met** (T2.4b: exact, including `None`) |
| R-5 | dangerous state loss | **met for the frame population** (S5.2); the window population is T5.4b, XFAIL, owner D8 |
| R-6 | safe state termination | **met** (C.5, EX-1: asserted and executed) |
| R-7 | no-silent-report | **met** (S2, S7) |
| R-8 | anti-silent-success | **met** (empty run = exit 2 in every P4 suite) |

### 9.5 What P4.3 did NOT do

```text
did not add measured_windows / total_windows / policy   (no producer)
did not mint PARTIALLY_MEASURED                          (not needed)
did not choose a gap policy (D5) or a coordinate (D2)
did not enable anything (D8)
did not change any algorithm, threshold or correction
did not rewrite section 1 .. section 8 of this document
```

The full record, with the decision-ID reconciliation between this document's
numbering and the P4.3 brief's, is
[`p4.3-production-semantics.md`](p4.3-production-semantics.md).
