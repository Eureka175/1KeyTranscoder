# Audio Sync P4 — Production Policy & Controlled Integration

**Entry point for this phase.** P3 (`../audio-sync-p3/`) is the **research** line
and is frozen. P4 is the **production** line.

---

## One-paragraph status

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
| [`P4-FINAL-REPORT.md`](P4-FINAL-REPORT.md) | the 22-section final report |

Architecture note added at [`../design/architecture.md`](../design/architecture.md) §6.8.

---

## What would change the answer

The path from here is ordered, and the last step is the only one that changes
production behaviour:

```text
1.  D5  what a correction may claim            -> shapes the API
2.  D6  can a consumer represent uncertainty?  -> can remove an option
3.  D1  which gap policy ships                 -> selects the fill rule
4.  D2  which coordinate contract              -> selects the join
5.  D3  residual acceptance                    -> sets any gate
6.  wire it in: one call site, one place decides the shift, inside the try
7.  add the flag: default OFF + byte-identical OFF-path audit
8.  measure the full pipeline
9.  full regression, including a live --channel-sync run
10. D8  enablement
```

Steps 6–9 cannot start meaningfully before 1–5, because 1–5 determine what is
being built.
