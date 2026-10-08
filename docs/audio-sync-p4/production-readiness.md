# P4 — production readiness matrix

**Status vocabulary only. No score, no total, no percentage.** From
`P3-RESEARCH-OVERVIEW.md`: *"A bare `FAIL` is never used. There is no total score
and no ranking of candidates."*

Allowed values:

```text
PASS              demonstrated, with the evidence named
PARTIAL           demonstrated on a subset, with the subset named
BLOCKED           a named condition prevents it
NOT ESTABLISHED   no evidence exists either way
KNOWN LIMITATION  demonstrated, and the limitation is measured and stated
DEFERRED          ready to decide, not decided
```

Every `Evidence` cell names an artifact produced or re-run **in this session**,
or a P3 artifact with its path. No status below is inherited from a summary.

---

## 1. The matrix

| Area | Status | Evidence |
|---|---|---|
| **P1 constant offset** | **PASS** | `work/p2/scripts/p1_regression_guard.py` → **27 PASS / 0 FAIL**, including the known-answer positive controls (constant offset corrected at the injected magnitude; two-sided offset corrected both ways; `already_aligned` shifts nothing; transparent mode `SHA256(in) == SHA256(out)`). Unchanged by P4: `core/sync_fix.py`, `core/channel_sync.py`, `core/sync_estimate.py` are byte-identical to HEAD. |
| **Measurement (P3 estimator)** | **PASS** (P3) | `docs/audio-sync-p4/evidence-freeze.md` §2/§3. Reproducible to the bit against the frozen P3 run, 478/478 windows, every compared field; `max\|real − model\| = 0.0000` samples against an analytic model. **Not re-run by P4** — P3 froze it and `P4-HANDOFF.md` §2 forbids revisiting it. |
| **State semantics** | **PASS** | `tests/audio_sync_p4/test_production_sync.py` T1, T3, T5, T11: the Q0-style sequence exact; all-refused legal; `REENTRY` at front/back/between; state is a pure function of the mask; `REENTRY` equals an **independently counted** transition count over 120 000 windows; contradictions raised. 30/30 state counts reproduce the P3.11 plan records. |
| **Real PCM apply** | **PASS** | `work/p4/verify_p311_reproduction.py` → **30/30 arms byte-for-byte identical to the P3.11 corrected PCM**, with each source corpus SHA-256 verified before comparison. |
| **Six channel pairs** | **PASS** | Same run: C1305 and C1306 × `ch3→ch1`, `ch3→ch2`, `ch3→ch4`, under **both** `latest-start` and `nearest-centre`, all byte-exact. Plus 18 further arms for `current`, `explicit_state`, `no_correction_gap`. |
| **Independent verification** | **PASS** | Two independent instruments: (a) the P3.11 stored hashes above — produced by different code, in an earlier session; (b) `core.sync_estimate`, tracked production code on a different frame grid, retained as the A4 invariant. **One disagreement recorded and not averaged** (`residual-acceptance.md` §2 E7). |
| **Gap policy** | **DEFERRED** | Five arms fully characterised on real PCM and **reproduced byte-exactly by P4** (`policy-matrix.md` §A). **No policy selected.** `DECISION_REQUIRED: D1, D5`. The instrument that would rank them is policy-invariant (0 of 8 136 pairs). |
| **Coordinate policy** | **DEFERRED** | Both contracts implemented, exercised and **reproduced byte-exactly**; `sample-domain` shown to be the same function as `latest-start` and therefore not a candidate. **`latest-start` is the implementation default for reproducibility, not as a selection.** `DECISION_REQUIRED: D2`. |
| **Residual acceptance** | **NOT ESTABLISHED** | `residual-acceptance.md`. No threshold exists and none was invented. The **structural gate is established and enforced** (A1–A8, N1–N6); the scalar threshold is `DECISION_REQUIRED: D3`. |
| **Error handling** | **PASS** | 49 raise-cases, **0 silent-wrong**: missing measurement, missing endpoint, invalid mask, NaN, Inf, huge shift, truncated record set, wrong-length stream, wrong-length plan, unknown policy, unknown coordinate, non-plan argument, zero/negative chunk size. `UNRESOLVED_GAP` never raises — it is a valid epistemic state, separated at the type level. |
| **Chunk invariance** | **PASS** | Byte-level, **8 chunk sizes** including one that splits a coordinate zone, for every online policy; `RawStream` and path inputs agree. Matches the P3.11 result (29 checks over 8 sizes). |
| **Memory** | **PASS** | Control **discriminating at 3/3 sizes** (67.1 / 268.4 / 1073.7 MB observed for 64 / 256 / 1024 MB requested) and released. Stream grows **45×** → traced peak grows **1.18×** (9.10 MB, flat). No per-sample coordinate label. |
| **Long-run stability** | **PASS** | **726 000** decisions walked; `REENTRY` matches an independent count; **traced live growth +0.00 MB**. RSS released growth +6.52 MB, attributed to allocator behaviour and reported rather than claimed as zero. |
| **Performance** | **PARTIAL** | Apply measured: **671–814 MB/s**, flat across chunk sizes, plan construction 0.05–0.11 s per 100 000 windows. `PARTIAL` because **end-to-end cost is UNMEASURED** — decode + measure + correct + encode has never been measured through the production pipeline (`OPEN-DECISIONS.md` D4). |
| **Default path** | **PASS** | No production module references `core/audio_sync_tv.py`; `shift_stream` still takes a **scalar**; P1's `algo_version` still `2.3.0-p1`; `AudioPlan` untouched; the module's import list is stdlib + numpy + `core.sync_estimate` only. Existing regression: **626 PASS / 0 FAIL**. |
| **Compatibility** | **PARTIAL** | Verified: P1 constant correction **27/0**; existing unit suite **626/0**; the three user-modified production files byte-unchanged; no dependency on `encoders` / `preservation` / hardware / Sony / DJI; no `Video` or `AudioPlan` coupling. **Not verified:** a live `--channel-sync` run through hardware decode, `multihw`, `batch`, `watchfolder`, resume, or an actual transcode with the new module wired in — because **it is not wired in**. |
| **Documentation** | **PASS** | This directory: `README.md`, `evidence-freeze.md`, `policy-matrix.md`, `coordinate-decision.md`, `gap-policy-decision.md`, `residual-acceptance.md`, `implementation.md`, `validation.md`, `production-readiness.md`, `policy-recommendation.md`, `provenance.md`, `P4-FINAL-REPORT.md`. |
| **Provenance** | **PASS** | `provenance.md`: branch, HEAD, base commit, implementation hashes, corpus hashes, configuration frozen, policy/coordinate/algorithm version strings, and the P3.11 apply hashes P4 reproduces. |
| **Fixture hashes / anti-tamper** | **PASS** | The nine user-owned modifications hashed at P4 start and at P4 end; the four corpora hashed before every comparison in V2 (30/30 matched). |

---

## 2. The four statuses that are not `PASS`, expanded

### 2.1 `Gap policy — DEFERRED`

```text
what is done:     five arms characterised and reproduced byte-exactly
what is missing:  a product decision on whether uncorrected alignment inside a
                  gap is worse than invented structure
why engineering cannot answer it:
    a concert master and a sync-reference track do not want the same answer
    the metric that would rank the candidates cannot see a difference
    (0 of 8136 policy-pairs; max 5.0 samples; 0 events at 4 thresholds)
```

### 2.2 `Coordinate policy — DEFERRED`

```text
what is done:     both contracts implemented, selectable, exercised on real
                  PCM and reproduced byte-for-byte
what is missing:  a decision on whether a correction should anticipate a
                  change or lag it
why it is not taken:  the two are symmetric in error magnitude; changing the
                  default silently alters every historical plan and therefore
                  every P3.3-P3.8 real-corpus number
```

### 2.3 `Residual acceptance — NOT ESTABLISHED`

```text
what is done:     the structural gate (A1-A8, N1-N6); the error budget from
                  P3.10/P3.11/CAL-1..CAL-4; an explicit list of what would
                  establish a threshold (R1-R5)
what is missing:  any statement that the corrected output is "good enough"
why:              it is a product property, and no experiment in this repository
                  can make that judgement
```

### 2.4 `Performance — PARTIAL` and `Compatibility — PARTIAL`

```text
Performance: the apply is measured and fast. The PIPELINE is not measured,
             because the module is not wired into it. Wiring it in would be the
             production change a product decision authorises.
Compatibility: every check answerable WITHOUT wiring the module in is done.
             The rest cannot be answered without doing the thing that is not
             authorised.
```

---

## 3. The overall verdict

```text
Fixed offset                       VALIDATED
Time-varying measurement           VALIDATED
Real PCM apply                     VALIDATED
Six-pair E2E                       VALIDATED
Independent verify                 VALIDATED  (one disagreement recorded)
Gap semantics                      VALIDATED
Gap policy                         DEFERRED
Coordinate                         DEFERRED
Residual acceptance                NOT ESTABLISHED
Default path                       PASS
Production feature                 OFF

Overall                            READY FOR PRODUCTION POLICY
```

`READY FOR PRODUCTION POLICY` — not `READY FOR CONTROLLED PRODUCTION`, because
that requires an approved policy, and not `PARTIAL` or `BLOCKED`, because nothing
P4 was asked to build is incomplete.

---

## 4. What would move each status

| area | to move it | owner |
|---|---|---|
| Gap policy | answer D1 and D5 | product |
| Coordinate policy | answer D2 | product + engineering |
| Residual acceptance | answer D3; then R1–R5 as needed | product (+ engineering for R1–R3) |
| Performance → PASS | measure the full pipeline **after** enablement is authorised | engineering |
| Compatibility → PASS | run a live `--channel-sync` with the module wired in, through hardware decode, batch and watchfolder | engineering, gated on the policy decision |
| Production feature → ON | approve D8 **and** D1 **and** D2 **and** D5 | product |

### 4.1 There is a hard ordering here

```text
D5  (what a correction may claim)  shapes the API
D1  (which gap policy ships)       selects the fill rule
D2  (which coordinate contract)    selects the join
D3  (residual acceptance)          sets any gate
D6  (downstream representability)  can remove `explicit_state` from D1 entirely
D8  (enablement)                   is last, and is the only one that changes
                                   production behaviour
```

**D8 cannot be answered before D1/D2/D5, because the answer to D8 is implemented
by the others.** And D6 can remove an option from D1 — *if a downstream consumer
cannot represent uncertainty, `explicit_state` is unavailable regardless of its
merits, and D1 collapses to a choice among the others.* **Nobody has looked at the
consumers.**

---

## 5. What is deliberately not claimed

```text
NOT claimed: that the correction is production-ready
NOT claimed: that a policy was chosen
NOT claimed: that a residual threshold exists
NOT claimed: that the output is good enough for any purpose
NOT claimed: that the time-varying path is faster end to end than not using it
NOT claimed: that this is an upgrade path for P1
NOT claimed: that any check in this phase is complete
             ("a suite that passes is not a suite that is complete")
```
