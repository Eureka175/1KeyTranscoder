# Audio Sync P4 — Production Policy & Controlled Integration

**Entry point for this phase.** P3 (`../audio-sync-p3/`) is the **research** line
and is frozen. P4 is the **production** line.

---

## ⚠️ Current status first — the P4 text below is P4's own record

Everything from **"One-paragraph status"** down to the end of this file describes
**P4 alone**, written when P4 finished. Its claims were true of P4. Four phases
have happened since, and two of its lines are superseded. Read this block for the
current state; read the rest as P4's history, which is preserved unaltered.

```text
P4.1  D6 downstream audit                       COMPLETE — D6 = BLOCKED (record not rewritten)
P4.2  evidence-integrity fix                    COMPLETE — the adoption gate now covers the ADOPTED estimate
P4.3  production-semantics decision closure     COMPLETE — D1 implemented; D2/D5 DEFERRED; D6 revalidated
P4.4  production enablement                     COMPLETE — validated FIXED-DELAY path ENABLED
```

### What is actually enabled now

| path | entry | state |
|---|---|---|
| **P1 fixed delay** (one integer shift per track) | `--channel-sync`, `--channel-sync-transparent` | **ENABLED** (opt-in) |
| **P2 constant offset** (one offset per channel) | `--audio-plan` + explicit `sync.reference` | **ENABLED** (opt-in) |
| **P4 time-varying** (one shift per window) | none | **NOT ENABLED** — imported by no production module |

```text
Default path          UNCHANGED — measured: 0 calls into any PCM entry point
Drift correction      NOT ENABLED
Resampling            NOT ENABLED
D2 gap policy         DEFERRED
D3 residual           NOT ESTABLISHED
D5 coordinate         DEFERRED
Algorithm changes     0
```

**Read the two superseded lines below accordingly:**

* *"…verified it … and **did not enable it**"* and *"the overall status is
  `READY FOR PRODUCTION POLICY`"* — true of P4. P4.4 enabled the **validated
  fixed-delay subset**; the per-window module is still imported by nothing, so
  *"the time-varying production feature: OFF"* in the status table **remains
  correct**.
* The **Memory / Performance / Compatibility** rows describe the P4 module's own
  path. The enabled path's cost is different and is recorded in
  [`known-limitations.md`](known-limitations.md) §5.

### Read next

| need | document |
|---|---|
| **the normative contract — what production may do** | [`production-contract.md`](production-contract.md) |
| what is open, failing, blocked, XFAIL | [`known-limitations.md`](known-limitations.md) |
| every suite and its current status | [`validation.md`](validation.md) → the P4.1–P4.4 roll-up |
| what P4.2 fixed and why it was live | [`p4.2-evidence-integrity.md`](p4.2-evidence-integrity.md) |
| how evidence travels, and where it stops | [`p4.3-production-semantics.md`](p4.3-production-semantics.md) |
| what D1 decided, and what it did not | [`d1-uncertainty-contract.md`](d1-uncertainty-contract.md) §9 |
| what P4.4 enabled, and why the rest stays off | [`p4.4-production-enablement.md`](p4.4-production-enablement.md) |
| **how the frozen baseline is identified across platforms** | [`p4.5-baseline-integrity.md`](p4.5-baseline-integrity.md) |
| **why the P3.7 matrix reported two different totals** | [`../audio-sync-p3/p3.7-discrepancy-audit.md`](../audio-sync-p3/p3.7-discrepancy-audit.md) |
| **what the time-varying capability now does, and how it refuses** | [`p4.6-time-varying-production.md`](p4.6-time-varying-production.md) |
| **the six real corpus pairs and the two independent oracles** | [`p4.6-real-corpus.md`](p4.6-real-corpus.md) |
| **what actually reaches apply on real material, and what does not** | [`p4.7-production-reachability.md`](p4.7-production-reachability.md) |
| **which measurement window/hop geometry is usable on real material without hiding delay change** | [`p4.8-measurement-geometry.md`](p4.8-measurement-geometry.md) |
| **whether the short 20260903/20260904 clips suit the fixed-delay path** | [`p4.9a-short-clip-revalidation.md`](p4.9a-short-clip-revalidation.md) |
| **what P4.9A changed, and what it proved it did not** | [`p4.9a-provenance.md`](p4.9a-provenance.md) |
| **how large the measurement and correction error actually is against known truth** | [`p4.9b-residual-calibration.md`](p4.9b-residual-calibration.md) |
| **what P4.9B changed, and what it proved it did not** | [`p4.9b-provenance.md`](p4.9b-provenance.md) |
| **whether this machine's material can supply a real wireless ground truth, and what to record if not** | [`p4.10-real-source-qualification.md`](p4.10-real-source-qualification.md) |
| **the recording procedure that would supply one** | [`p4.10-acquisition-protocol.md`](p4.10-acquisition-protocol.md) |
| **what P4.8 changed, and what it proved it did not** | [`p4.8-provenance.md`](p4.8-provenance.md) |
| the P4.1 / D6 audit | [`downstream-audit.md`](downstream-audit.md) |
| where a promoted document came from | [`documentation-provenance.md`](documentation-provenance.md) |
| the P3 research line | [`../audio-sync-p3/P3-RESEARCH-OVERVIEW.md`](../audio-sync-p3/P3-RESEARCH-OVERVIEW.md) |

### The P4 line, in order

| phase | question | verdict |
|---|---|---|
| **P4** | build the per-window time-varying corrector as tracked production code, verify it, and **not** enable it | `COMPLETE` — 30/30 byte-exact reproduction of P3.11's corrected PCM; feature OFF |
| **P4.1** | can `UNRESOLVED_GAP` reach a production consumer? | `D6 = BLOCKED` — no downstream representation, **and** the same failure already live at track granularity |
| **P4.2** | fix that live defect without inventing a threshold | `COMPLETE` — the gate now applies to the estimate actually **adopted**; 35 regression cases; 5/6 real pairs move to `LOW_CONFIDENCE`, the 74.4 % pair still succeeds |
| **P4.3** | make the evidence semantics a production **contract** | `COMPLETE` — D1 implemented and propagated; D2/D5 recorded as `DEFERRED` with implementation defaults kept **separate** from semantic contracts |
| **P4.4** | enable the validated subset, safely | `COMPLETE` — one `correction_eligibility()` decision, a plan object that cannot carry a refused offset, fixed-delay **ENABLED** |
| **P4.5** | can the frozen validation asset be identified across platforms, and why did two P3.7 totals disagree? | `COMPLETE` — baseline identity is now line-ending independent (`sha256-lf-normalized-v1`, fixture **unchanged**); the P3.7 conflict is a **definitional** one (check 1a `111/132` vs check 1b `132/132`), **`D2` still `DEFERRED`**, no production code touched |
| **P4.6** | can the per-window capability built in P4 actually be executed, without deciding D2/D5 for the product? | `COMPLETE` — an **explicit opt-in** path that refuses when `policy` or `coordinate` is unspecified; it plans, applies and independently verifies per-window correction, is chunk-invariant and flat at ~173 MiB; **D2/D5/D3 all still open**, fixed-delay and default paths unchanged |

---

## One-paragraph status

> **P4.6 note.** The paragraph below is the P4-era status and is preserved as
> written. Two of its statements are now superseded and are corrected here rather
> than edited in place. (a) "imported by no production module" — P4.6 added
> `core/audio_sync_tv_exec.py` and wired `channel_sync.py` to it **from function
> scope**, so the path is now reachable by an explicit request; the default run
> still never evaluates the branch, and that is asserted rather than assumed.
> (b) "Default OFF is structural, not a flag" (§3 below) — the opt-in is now a
> flag, so the property is re-stated: `time_varying` defaults to `False`, every
> pre-existing option is asserted unchanged key by key, no report row carries a
> `time_varying` block on a default run, and a 15-check end-to-end run confirms
> the default path takes the shipped `shift_stream` call unchanged.

P4 built a per-window, time-varying audio delay corrector as tracked production
code (`core/audio_sync_tv.py`), verified it by reproducing **P3.11's corrected PCM
byte-for-byte on 30 of 30 real-corpus arms**, and **did not enable it**. It is
imported by **no production module**, so the default path is unchanged
structurally rather than by a flag. The gap policy, the coordinate contract and
the residual acceptance threshold are **product decisions that P4 deliberately did
not take**. The overall status is:

```text
READY FOR PRODUCTION POLICY
```

---

## Status table

| Area | Status |
|---|---|
| P1 constant offset | **VALIDATED** (27 PASS / 0 FAIL, re-run) |
| Time-varying measurement | **VALIDATED** (P3, frozen) |
| Real PCM apply | **VALIDATED** (30/30 byte-exact) |
| Six channel pairs | **VALIDATED** |
| Independent verification | **VALIDATED** (one disagreement recorded, not averaged) |
| Gap semantics | **VALIDATED** |
| Gap policy | **DEFERRED** — `DECISION_REQUIRED` |
| Coordinate policy | **DEFERRED** — `DECISION_REQUIRED` |
| Residual acceptance | **NOT ESTABLISHED** — `DECISION_REQUIRED` |
| Error handling | **PASS** (49 raise-cases, 0 silent-wrong) |
| Chunk invariance | **PASS** (byte-level, 8 chunk sizes) |
| Memory | **PASS** (9.10 MB peak, flat across a 120× stream range) |
| Performance | **PARTIAL** (apply measured; pipeline unmeasured) |
| Default path | **PASS** (structurally unchanged) |
| Compatibility | **PARTIAL** (all checks answerable without wiring) |
| **Time-varying production feature** | **OFF** |

**P4.6 additions to the table above** (the rows above are the P4-era record and are
left as written):

| Area | Status |
|---|---|
| Time-varying **execution** | **COMPLETE** — plans, applies, independently verifies; chunk-invariant; ~173 MiB flat from 30 s to 10 min |
| Time-varying **opt-in** | **ENABLED** — `--channel-sync-tv` + policy + coordinate; refuses with a stable reason code if either is unspecified |
| Default / fixed-delay path | **UNCHANGED** — asserted by instrumentation, not by reading code |
| Real corpus (C1305/C1306, 6 pairs) | **PASS** for execution and mechanics — 6/6 bit-exact against the frozen P3.11 PCM |
| Real corpus **reachability** | **PARTIAL** — the per-track time-varying step is not reachable through `run_channel_sync` because the production evidence gate refuses that material first |
| A7M5 raw media | **NOT RUN** — the source media is absent from this machine |
| Memory / performance | **PASS** — flat peak working set, linear throughput, no partial output on failure |
| Gap policy (D2) / Coordinate (D5) / Residual (D3) | **still `DEFERRED` / `DEFERRED` / `NOT ESTABLISHED`** |

Detailed matrix: [`production-readiness.md`](production-readiness.md).

---

## The five things worth knowing

### 1. The strongest result: a byte-exact reproduction

P3.11 stored a SHA-256 for every corrected PCM stream it produced. P4 reproduces
**30 of 30** of them exactly — all six channel pairs, **both** coordinate
contracts, every online gap policy — with the source corpus hash verified before
each comparison.

```text
arms run            : 30
BIT-EXACT MATCH     : 30
MISMATCH            : 0
state counts checked: 30 (0 mismatched)
```

`413E0205…` for C1305 ch3→ch4 is the hash **P3.10** recorded. Three independent
reproductions from different code.

### 2. The correction value and the epistemic state are separate

```python
CorrectionDecision(615, GapState.TRUSTED)          # measured
CorrectionDecision(615, GapState.UNRESOLVED_GAP)   # a policy filled the gap
```

The same number, two honest meanings. A nullable number cannot distinguish
*"0-shift measurement"* from *"no measurement"*. Contradictions — a
policy-sourced `TRUSTED`, a measurement-sourced `UNRESOLVED_GAP`, NaN, an
out-of-range shift — are **unrepresentable**, not merely discouraged.

**`UNRESOLVED_GAP` is a valid state, not an error.** It never raises. Errors raise.

### 3. Default OFF is structural, not a flag

```text
core/audio_sync_tv.py exists and is importable
BUT no production module references it — asserted by test
```

A runtime flag can be switched on by configuration, environment or a mistake.
Code that nothing imports cannot run. `core/channel_sync.py`, `core/sync_fix.py`,
`core/sync_estimate.py`, `core/audio_plan.py` and `1kt.py` are **byte-identical to
HEAD** (3/3 hashes verified), and the nine user-owned modifications are **9/9
unchanged**.

### 4. Nothing about the product policy was decided

```text
gap policy         DEFERRED   owner: product          (D1, D5)
coordinate policy  DEFERRED   owner: product + eng    (D2)
residual threshold NOT ESTABLISHED  owner: product    (D3)
enablement         NOT ENABLED      owner: product    (D8)
downstream uncertainty             owner: format/API  (D6)
```

`latest-start` is the implementation default **for reproducibility** — every
P3.3–P3.8 real-corpus number was produced under it — and that is **not** a
selection. See [`policy-recommendation.md`](policy-recommendation.md), which
separates engineering recommendations from product decisions by construction.

### 5. Two things P4 found that P3 did not

**a. The plan's per-window shift indexing is part of the apply contract, not
metadata.** Under `nearest-centre`, `excess = after − (before − applied)` reaches
**732 samples** where `latest-start` gives **0.615** — because the sample at block
`b` receives `S[b−1]` while `excess` is computed against `S[b]`. That is **not an
audio defect**; it means a verifier must compute its expectation from the shift
actually applied at that sample.

**b. Ten P3 summary ranges do not reproduce from P3's own detail tables.**
Recorded with both values in [`evidence-freeze.md`](evidence-freeze.md) §7b,
including the coordinate figures (P3.10's `670`/`79` samples and `30.0 s` against
P3.11's `732`/`211` and `20.0 s`). None is load-bearing for a P4 decision.

---

## How to run everything

```powershell
# the regression suite -- TRACKED CODE ONLY, no corpus needed
python tests/audio_sync_p4/test_production_sync.py

# byte-exact reproduction of the P3.11 corrected PCM -- needs the corpus
python work/p4/verify_p311_reproduction.py --json work/p4/p311-reproduction.json

# memory, performance, long-run stability
python work/p4/memory_and_performance.py --json work/p4/memory-performance.json

# hash re-verification of everything P4 must not have changed
python work/p4/hash_verify.py

# the regressions P4 must not have broken
python tests/full_autotest.py --level unit
python work/p2/scripts/p1_regression_guard.py
```

**Clean-run requirements**, stated so nothing is overstated:

| command | needs |
|---|---|
| the P4 suite | **nothing beyond the repo and numpy** |
| the reproduction verifier | the real corpus (`work/p2/out/raw/*.f32`) and `work/p3/p311/` |
| memory / performance | nothing external |
| hash verify | nothing external |
| `--level unit` | nothing external |
| the P1 guard | the real corpus |

The **first** command is the one that runs anywhere. P4's tests import nothing
from `work/` — deliberately, because the tracked P3.11 coordinate test cannot run
on a clone for exactly that reason (`current-state-audit.md` §6).

---

## Documents

| file | purpose |
|---|---|
| [`README.md`](README.md) | this file — entry point and status |
| [`evidence-freeze.md`](evidence-freeze.md) | what may be quoted from P3, and from which artifact; the discrepancy register |
| [`policy-matrix.md`](policy-matrix.md) | the three decision matrices with real measured evidence |
| [`coordinate-decision.md`](coordinate-decision.md) | the three coordinate questions answered separately, in real sample coordinates |
| [`gap-policy-decision.md`](gap-policy-decision.md) | state semantics and the three gap options stated exactly |
| [`residual-acceptance.md`](residual-acceptance.md) | the error budget, the structural gate, and why no threshold exists |
| [`implementation.md`](implementation.md) | the production API, the applier, and the feature gate |
| [`validation.md`](validation.md) | every validation, with its command and its numbers |
| [`production-readiness.md`](production-readiness.md) | the status matrix — status words only, no score |
| [`policy-recommendation.md`](policy-recommendation.md) | engineering recommendations vs product decisions |
| [`provenance.md`](provenance.md) | identity, hashes, reproduction anchors, unmodified-file verification |
| [`static-audit.md`](static-audit.md) | forbidden patterns, silent exceptions, dependency direction, memory — all checks, all outputs |
| [`p4.6-time-varying-production.md`](p4.6-time-varying-production.md) | **P4.6** — the explicit opt-in time-varying production path: entry point, refusal contract, evidence/state, gap and coordinate tables, PCM apply |
| [`p4.6-real-corpus.md`](p4.6-real-corpus.md) | **P4.6** — the six real C1305/C1306 pairs, the two independent oracles, and the reachability limitation |
| [`p4.7-production-reachability.md`](p4.7-production-reachability.md) | **P4.7** — the long-run production test matrix: three separated conclusions (mechanics / reachable / refused), the measured frame-geometry calibration finding, and the three product defects found and fixed |
| [`p4.8-measurement-geometry.md`](p4.8-measurement-geometry.md) | **P4.8** — the measurement-geometry calibration: four window/hop geometries measured on synthetic and real material, the three separated questions (validity / reachability / correctness), the transition-resolution cost, and why the shipped geometry is left unchanged |
| [`p4.8-provenance.md`](p4.8-provenance.md) | **P4.8** — what the phase changed (one test file, plus documentation), what it proved it did **not** change (no `core/`, `1kt.py` or `production/` diff), the matrix reconciliation, and the decision record |
| [`p4.9a-short-clip-revalidation.md`](p4.9a-short-clip-revalidation.md) | **P4.9A** — the short-clip fixed-delay revalidation over `testsets/20260903` + `20260904`: inventory and channel identity, production measurement and gate outcome per pair, within-clip and between-clip stability, real correction with independent residual, and a known-delay positive control |
| [`p4.9a-provenance.md`](p4.9a-provenance.md) | **P4.9A** — corpus read-only proof, the production code reused (no new parser, estimator or algorithm), why the independent verifier is not the estimator in disguise, and the verifier defect that was found and fixed |
| [`p4.9b-residual-calibration.md`](p4.9b-residual-calibration.md) | **P4.9B** — controlled residual calibration: known-truth end-to-end residuals (R1), accepted-population sensitivity to each gate term (R2), the estimator's error against truth versus bandwidth and SNR (R3), and the independent-verifier disagreement (R4) |
| [`p4.9b-provenance.md`](p4.9b-provenance.md) | **P4.9B** — what was reused from production rather than rewritten, how the three corpora are kept apart, the one verifier defect that changed a published number and the check on how far it reached, and the decision record |
| [`p4.10-real-source-qualification.md`](p4.10-real-source-qualification.md) | **P4.10** — real-source qualification: a full probe of every media container on the machine, truth grading (G0/G1/G2/G3) with the one proposed anchor rejected, a measured demonstration that the production corpus's only eligible channel pair is internally coincident, a cross-method agreement check on real stereo audio, and an executable acquisition protocol |
| [`p4.10-provenance.md`](p4.10-provenance.md) | **P4.10** — what was read rather than recomputed, the cross-phase verifier dependency stated rather than assumed, and the decision record |
| [`p4.10-acquisition-protocol.md`](p4.10-acquisition-protocol.md) | **P4.10** — the executable acquisition protocol: equipment and wiring, recording format, test signal, reference events, duration and repeats with their reasons, how the truth is established by construction, reference-path calibration, expected truth resolution, the operator-note fields, and the acceptance and failure conditions |
| [`P4-FINAL-REPORT.md`](P4-FINAL-REPORT.md) | the 22-section final report |

Architecture note added at [`../design/architecture.md`](../design/architecture.md) §6.8.

---

## What would change the answer

> **P4.6 note.** Steps 6–9 below were carried out in P4.6 **without** settling
> steps 1–5, by making the two open decisions mandatory arguments the path refuses
> to supply. The capability ships as an explicit opt-in and the product semantics
> stay open.

```text
1.  D5  what a correction may claim            -> STILL OPEN (DEFERRED)
2.  D6  can a consumer represent uncertainty?  -> STILL OPEN
3.  D1  which gap policy ships                 -> STILL OPEN (D2 = DEFERRED)
4.  D2  which coordinate contract              -> STILL OPEN (D5 = DEFERRED)
5.  D3  residual acceptance                    -> STILL OPEN (NOT ESTABLISHED)
6.  wire it in: one call site, one place decides the shift  -> DONE (P4.6)
7.  add the flag: default OFF + byte-identical OFF-path audit -> DONE (P4.6)
8.  measure the full pipeline                  -> DONE (P4.6)
9.  full regression, including a live --channel-sync run -> DONE (P4.6)
10. D8  enablement                             -> NOT DONE (needs 1-5 first)
```

Step 10 remains gated on 1–5: the **opt-in** is enabled, but no **default** can be
chosen until the product decides what a gap means and which window owns a sample.
P4.6 removed the "cannot build it yet" blocker; it did not remove the "cannot
decide it for the product" blocker.
