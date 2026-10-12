# P4 — validation record

Every number here was produced in this session, by a command named in the row.
Nothing is inherited from a P3 summary.

---

## 0. Summary

| # | validation | result | command |
|---|---|---|---|
| V1 | P4 production regression suite | **180 cases, 0 silent-wrong, 0 fail** | `python tests/audio_sync_p4/test_production_sync.py` |
| V2 | P3.11 corrected-PCM reproduction | **30 / 30 arms byte-for-byte** | `python work/p4/verify_p311_reproduction.py` |
| V3 | P3.11 plan state counts | **30 / 30 match** | same, above |
| V4 | existing unit regression | **626 PASS / 0 FAIL** | `python tests/full_autotest.py --level unit` |
| V4b | existing toolchain regression | **649 PASS / 0 FAIL** | `python tests/full_autotest.py --level toolchain` |
| V5 | P1 guard | **27 PASS / 0 FAIL** | `python work/p2/scripts/p1_regression_guard.py` |
| V6 | memory control (positive, touched) | **discriminating, 3/3 sizes** | `python work/p4/memory_and_performance.py` |
| V7 | bounded memory | peak **9.10 MB** over a **120×** stream range | same |
| V8 | long-run stability | **2 420 000** decisions, traced live growth **+0.01 MB**, growth **bounded** | same |
| V9 | coordinate tests (P3.11, tracked) | **71 PASS / 0 FAIL** | `python tests/audio_sync_p311/test_coordinates.py` |
| V10 | nine user-owned files unchanged | **9 / 9 hash MATCH** | `python work/p4/hash_verify.py` |
| V11 | three P1 production files unchanged | **3 / 3 hash MATCH** | same |
| V12 | P4 implementation hashes recorded | 4 files | same |

---

## V1 — P4 production regression suite

```text
cases                     : 180
VALUE_OK                  : 119
RAISED (expected errors)  : 49
CONTROL_OK                : 12
CONTROL_FAILED            : 0
SILENT_WRONG              : 0
FAIL                      : 0
RESULT: PASS
```

Runs from **tracked code only**. Nothing under `work/` is imported — the P3.11
coordinate test cannot satisfy that (see `current-state-audit.md` §6), so P4
does not copy its approach.

### The suite's structure

| test | what it establishes |
|---|---|
| **T1** state semantics | the Q0-style sequence exactly; all-refused is legal; `REENTRY` at front, back and between two gaps; state is a pure function of the mask; 0/1 masks accepted, a mask containing `2` refused |
| **T2** decision object | the same shift with two states is constructible; contradictions unrepresentable; NaN/±Inf/huge refused; the exact `±(2**31 − 1)` boundary accepted |
| **T3** plan invariants | length equals `ceil(n/hop)` exactly (short **and** long refused); states contradicting the mask refused; mask derived from stated validity, never assumed; provenance in the object |
| **T4** coordinate contract | `sample-domain` **is** `latest-start` on both production plan sizes; raw nearest-centre returns `−1` at block 0 and the contracted form clips it; first index divergence at sample 480 000; the seam is 3 / 3 / 2 / 2 by definition |
| **T5** gap policy | `hold` carries; `no_correction_gap` zeroes; `explicit_state` is bit-identical to `hold`; `current` reports no gap and its over-claim is **counted**; leading-gap behaviour differs between `hold` and `current` and **only** there; offline-only policies refused |
| **T6** apply mechanics | output length == input length; byte count == `n × itemsize`; single-block output equals an independent per-sample reference; wrong-length stream refused; zero out-of-range reads |
| **T7** chunk invariance | **8 chunk sizes** give a byte-identical output, including one that splits a coordinate zone; every online policy; `RawStream` and path inputs agree |
| **T8** failure safety | missing measurement / missing endpoint / invalid mask / NaN / Inf / huge shift / truncated set — each raises; a **refused** record's returned number is **not** used as a shift |
| **T9** default path | no production module references the new module; `shift_stream` still takes a **scalar**; P1's `algo_version` still `2.3.0-p1`; `1kt.py` and `audio_plan.py` clean; the import list is stdlib + numpy + the estimator only |
| **T10** geometry | the frozen P3 config asserts exactly; both corpora are non-multiples of hop so the seam is 3; the `n mod hop == 0` branch gives 2 |
| **T11** long run | 120 000 windows; all three states occur; `REENTRY` equals an **independently counted** transition count |
| **T12** closed-form equivalence | the applier's inlined join equals the public `window_index` on **3 367 probes** per coordinate; the segment-boundary formula is exact; the block applier equals the independent per-sample reference at a chunk size that interleaves segment and chunk boundaries |

### The anti-silent-pass gates

The suite exits **2** (not 0) when `cases == 0`, `VALUE_OK == 0`,
`RAISED == 0`, or `CONTROL_OK == 0`. The brief's §36 requirement —
`experiment_count == 0` must not PASS, `apply_count == 0` must not PASS,
`verify_count == 0` must not PASS — is implemented as:

```text
N1-N3  non-zero counts required, in the suite's own gate and in
       ShiftPlanStats.apply_count
N4     every fixture family carries a CONTROL that demonstrates the check
       can fail; 12 controls, all demonstrated
N5     the suite asserts VALUES; "it did not raise" is never a finding
N6     an empty run exits 2
```

### Four real defects the suite found in the P4 code itself

Recorded because the P3 line's rule is that the defect is usually in the checker —
and here the checker was right:

| # | defect | how it was caught |
|---|---|---|
| 1 | `CorrectionDecision` defaulted `source="policy"`, so the brief's own example `CorrectionDecision(615, GapState.TRUSTED)` **raised**. A default that makes correct usage fail is a defect | T2.1 |
| 2 | `chunk_samples=0` was silently treated as "use the default", because the code tested truthiness instead of `None` | T6.8 |
| 3 | The segment-end formula used a "at least one sample" fallback, which degenerates to one-sample segments whenever `nearest-centre` lags — turning the apply loop into **O(n)**. Measured symptom: a 2-second apply did not finish in 300 seconds | the reproduction verifier timed out |
| 4 | `current` was classified **offline-only**, which is wrong: P3 documents it as runnable and P3.11 applied it on real PCM. Its leading-gap rule needs only the *first* trusted index | T5.12/T5.14 |

Defect 3 is the one worth naming: it was **invisible in the test suite** and only
appeared when the code was run against 923 MB of real audio at the alternative
coordinate.

---

## V2/V3 — P3.11 corrected-PCM reproduction

The strongest single check P4 can make. 36 `*_apply_meta.json` records exist under
`work/p3/p311/apply/`; each carries a `dst_sha256` produced by P3.11's own engine.

```text
P3.11 apply records found        : 36
arms with a recorded hash        : 30
arms run                         : 30
BIT-EXACT MATCH                  : 30
MISMATCH                         : 0
aborted (error)                  : 0
state counts checked             : 30 (0 mismatched)
RESULT: PASS
```

And in every arm the **recorded source SHA-256 was checked against the corpus on
disk** before comparing, so a wrong-input comparison could not pass.

### Arms covered

| group | coverage | result |
|---|---|---|
| `latest-start` × `hold` | all six channel pairs | 6/6 byte-exact |
| `nearest-centre` × `hold` | all six channel pairs | 6/6 byte-exact |
| `current`, `explicit_state`, `no_correction_gap` | real-corpus arms where P3.11 recorded a hash | 18/18 byte-exact |
| plan state counts `T/U/R` | **every arm** against the P3.11 plan JSON | 30/30 match |

### What this proves

```text
the P4 applier produces the SAME AUDIO as P3.11's research engine, bit for bit
the latest-start contract is implemented identically
the nearest-centre contract is implemented identically
every gap policy's shift sequence is identical
the state derivation reproduces P3.11's own plan records exactly
```

### The six arms deliberately NOT reproduced

`linear` arms are **excluded on purpose**. That policy needs a **right endpoint**,
so P4's streaming contract refuses it — reproducing it would require materialising
the whole per-sample coordinate array, which is the **1.8 GB / 3632 MiB** path
P3.11 measured. The verifier prints them as
`OFFLINE-ONLY arms deliberately NOT reproduced` and does **not** count them as
passes.

### Hash anchor

The `latest-start` × `hold` set includes
`413E0205BB5622EA838F98339BD6E0FDAE1235AFCE5B6BBE93C60747B5D9B20C` for C1305
ch3→ch4 — **the same hash P3.10 recorded**, which is how P3.11 demonstrated
cross-batch reproduction. P4 now reproduces it a third time, from different code.

---

## V4 — existing unit regression

```text
SUMMARY: 626 PASS / 0 FAIL (74.9s)
```

Run **after** `core/audio_sync_tv.py` and `tests/audio_sync_p4/` were added. This
is the check that the new module did not perturb the existing suites — the three
user-modified test files among them. `tests/selftest/suites/core.py` and
`toolchain.py` are the user's uncommitted modifications and both participate.

## V4b — existing toolchain regression (L2)

```text
########## 测试深度 TOOLCHAIN ##########
SUMMARY: 649 PASS / 0 FAIL (92.9s)
```

L2 adds real tool-version probing, `--check-features` capability parsing, the
known-flags whitelist, Gyroflow/GPAC availability, and — relevant to the user's
concurrent work — **file-level metadata** (`nrtm` meta across a re-mux) and the
`tref-cdsc` cross-remux cases. Both pass, which is the evidence that P4 left the
user's `core/batch_hw.py` container-preservation fix working.

L3 (`full`) was **not** run: it performs real encoder pipelines (NVENC/QSV) and
takes 10–15 minutes, and **P4's module is not reachable from any of it** — the
`--level toolchain` run already exercises the audio-sync selftest suites and the
file-level metadata path. Running L3 would test the encoder backends P4 did not
touch, not P4. Recorded as a deliberate choice rather than an oversight.

---

## V5 — P1 guard

```text
SUMMARY: 27 PASS / 0 FAIL
```

Matches the figure P3 recorded (`HANDOFF-CHECKLIST.md` §1 item 9). Includes the
known-answer positive controls, so the baseline is **not vacuous**:
constant offset corrected with the injected magnitude, two-sided offset corrected
in both directions, `already_aligned` shifts nothing, and transparent mode gives
`SHA256(in) == SHA256(out)` for aligned input.

---

## V6–V8 — memory, performance, long-run

### V6 the control is discriminating

| requested | RSS delta | ratio | traced delta | ratio |
|---:|---:|---:|---:|---:|
| 64 MB | 67.1 MB | 1.001 | 67.1 MB | 1.000 |
| 256 MB | 268.4 MB | 1.000 | 268.4 MB | 1.000 |
| 1024 MB | 1073.7 MB | 1.000 | 1073.7 MB | 1.000 |

all three **measurable**, and every control **released** back.

**This step first reported `discriminating: False`** and that is the important
part of the record: the Windows `GetProcessMemoryInfo` path tried here returns
**0 bytes** — a silent measurement failure of exactly the F11 family
`HANDOFF-CHECKLIST.md` §1 item 10 exists to catch. It was replaced with `psutil`,
and `tracemalloc` added as a second, independent instrument.

### V7 bounded memory

**Full scale** (`--json work/p4/memory-performance.json`):

| stream | samples | traced peak above baseline | RSS delta |
|---:|---:|---:|---:|
| 11.5 MB | 2 880 000 | 9.10 MB | +1.04 MB |
| 57.6 MB | 14 400 000 | 9.10 MB | +0.00 MB |
| 345.6 MB | 86 400 000 | 9.10 MB | +0.00 MB |
| **1382.4 MB** | **345 600 000** | **9.10 MB** | **+0.00 MB** |

```text
stream grows 120.0x   ->   traced peak grows 1.00x
```

Quick mode (45× range) gave the same 9.10 MB flat peak. **The applier's memory is
bounded by the block, not by the stream** — which is the property P3.11 lost at
3632 MiB by materialising one int64 coordinate label per sample.

### V8 long-run stability

```text
iterations                  : 20
decisions walked            : 2 420 000  (121 000 per iteration)
REENTRY vs independent count: 25928 vs 25928  -> MATCH
traced live growth          : +0.01 MB      (flat)
RSS released growth         : +13.48 MB     (allocator retention)
RSS held growth             : +5.41 MB      (retained-plan cost)
RSS slope, 1st half         : +1.341 MB per iteration
RSS slope, 2nd half         : +0.141 MB per iteration   <- ~10x lower
traced slope, 1st half      : +0.0008 MB per iteration
traced slope, 2nd half      : +0.0005 MB per iteration
unbounded growth detected   : rss=False, traced=False
```

**The growth is bounded, and that is demonstrated rather than asserted.** A
"first vs last" comparison cannot separate a leak from allocator retention — both
grow early. What separates them is whether the growth **plateaus**, so the test
splits the run in half and compares the slopes. RSS slope falls ~10× between the
halves while traced live memory stays flat at +0.01 MB over 2.42 M decisions.

Two findings recorded rather than hidden:

1. The first version of this test **retained one plan per iteration** and reported
   ~11 MB of growth per iteration as a possible leak. That growth was entirely the
   harness's own retained objects — a plan is ~11 MB at this window count. A memory
   check that charges the harness's arrays to the code under test is the same
   defect P3 records for its first measurement.
2. The corrected test separates three different quantities, and **only one of them
   answers the question**:

```text
RSS held      the harness's own objects        not a leak
RSS released  allocator behaviour              noisy, plateaus
traced live   real live Python/numpy objects   +0.01 MB over 2.42M decisions
```

### Performance

**Full scale:**

| measurement | value |
|---|---|
| apply, 60 s stream | 682–702 MB/s |
| apply, **5.0 h stream** (864 000 000 samples, 1800 windows) | **744.6 MB/s**, RSS delta **−0.3 MB** |
| chunk sizes 480 k / 120 k / 1.92 M / 100 k | 675 / 863 / 682 / 795 MB/s — flat |
| plan construction | 0.048–0.052 s per 100 000 windows |
| state derivation | ~0.0002 s per 100 000 windows |

**Measurement and verification are absent from these numbers.** The brief's §31
requires that the verifier's cost not be reported as the correction's cost; this
stage measures apply only. For scale, P3.11 measured measurement at **4.3–4.7 s
per 30 s window**, 97–99 % of total wall time — i.e. **the correction is not the
expensive part.**

---

## V9 — P3.11 coordinate tests (tracked)

```text
71 PASS / 0 FAIL
```

Run to confirm the P3.11 coordinate contract is unchanged by P4's work. **This
test cannot run on a clone** — it imports from gitignored `work/p3/p311/`
(`current-state-audit.md` §6). It runs here because `work/` is present. **P4
relies on its own T4/T12 for the same properties, not on this test.**

---

## What is NOT validated

Stated so the record is not read as broader than it is.

| item | status |
|---|---|
| anything about correction *quality* | **NOT VALIDATED** — no acceptance threshold exists (`residual-acceptance.md`) |
| the in-gap delay | **UNOBSERVABLE** — no test can validate a value nobody can measure |
| end-to-end production cost (decode + measure + correct + encode) | **UNMEASURED** — `OPEN-DECISIONS.md` D4 |
| a calibrated per-window uncertainty | **NOT ESTABLISHED** |
| the C1306 ch3→ch2 instrument disagreement (+70 vs +8.46 samples) | **UNRESOLVED** — recorded, not averaged |
| behaviour on real material outside C1305 / C1306 | **UNTESTED** |
| the `linear` / `next` offline arms | **NOT REPRODUCED** — refused by design |
| `core/channel_sync.py` integration | **NOT DONE** — no production wiring |
| downstream representability of `UNRESOLVED_GAP` | **UNKNOWN** — `OPEN-DECISIONS.md` D6 |

---

# P4.1 – P4.4 — current validation roll-up

**Appended by the P3–P4 documentation consolidation.** Everything above this line
is P4's own record and is **unchanged**. This section states the current status of
the whole P4 line, so a maintainer does not have to reconcile four phase reports
by hand.

Machine-checked identity for the append:
[`documentation-provenance.md`](documentation-provenance.md) ·
[`known-limitations.md`](known-limitations.md).

## Where each suite stands

| # | suite / check | status | command |
|---|---|---|---|
| C1 | `tests/audio_sync_p4/test_production_sync.py` (P4) | **PASS** — 182 cases, SILENT_WRONG 0, 14 controls | `python tests/audio_sync_p4/test_production_sync.py` |
| C2 | `tests/audio_sync_p4/test_downstream_state.py` (P4.1 / D6) | **PASS with 1 XFAIL** — 66 cases, 0 FAIL, 1 XFAIL (owner D8), 7 controls | `python tests/audio_sync_p4/test_downstream_state.py` |
| C3 | `tests/audio_sync_p4/test_evidence_integrity.py` (P4.2) | **PASS** — 35 cases, 3 controls | `python tests/audio_sync_p4/test_evidence_integrity.py` |
| C4 | `tests/audio_sync_p4/test_production_semantics.py` (P4.3) | **PASS** — 60 cases, 8 controls | `python tests/audio_sync_p4/test_production_semantics.py` |
| C5 | `tests/audio_sync_p4/test_production_eligibility.py` (P4.4) | **PASS** — 58 cases, 9 controls | `python tests/audio_sync_p4/test_production_eligibility.py` |
| C6 | `tests/full_autotest.py --level unit` | **PASS** — 626 PASS / 0 FAIL | same |
| C7 | `tests/full_autotest.py --level toolchain` | **PASS** — 649 PASS / 0 FAIL | same |
| C8 | `work/p2/scripts/p1_regression_guard.py` | **PASS** — 27 PASS / 0 FAIL | same |
| C9 | P1 frozen 137-corpus replay through the production resolver | **PASS** — 548 rows, **0** status changes | `python work/p4.4/p1-audit/audit_baseline.py` |
| C10 | P1 frozen corpus eligibility | **PASS** — 11 / 11 `fixed` rows eligible (capability retained); 0 refusals carrying an offset | same |
| C11 | frozen fixture integrity | **PASS** — sha256 `36242616062FFEC2…` MATCH, `edited: false` → replaced in P4.5 by the canonical-content check (C26) | same, and `work/p4.2/p1-audit/audit_baseline.py` |
| C12 | real A7M5 known-answer material | **PASS** — 24 checks, 24 PASS, 0 FAIL | `python work/p4.4/real_material_validation.py` |
| C13 | C1305 / C1306 eligibility audit | **PASS** — 6 pairs, 5 refused, **1 eligible** (74.4 %), 0 refusals carrying an offset | `python work/p4.4/real_corpus_audit.py` |
| C14 | P4.4 static audit | **PASS** — 973 functions, **0** unreviewed, 7/7 guard checks | `python work/p4.4/static_audit.py` |
| C15 | P4.3 successor-family static audit | **PASS** — 41 modules, 0 unreviewed, 3/3 executable adjudications | `python work/p4.3/static_family_audit.py` |
| C16 | P4.2 defect-family static audit (re-run) | **PASS** — 51 modules, 64 gates, 0 unreviewed, 9/9 fixed-site | `python work/p4.2/static_audit.py` |
| C17 | default-path instrumentation | **PASS** — **0** PCM calls, 0.035 ms, no file touched | `python work/p4.4/performance.py` |
| C18 | enabled-path cost | **PASS (measured and reported)** — 1.234 s / 4 s clip, traced peak 13.15 MB | same |
| C19 | `work/p3/p310/regression.py` | **PASS** — 26 PASS / 0 FAIL | same |
| C20 | `work/p3/p311/regression.py` | **PASS** — 70 PASS / 0 FAIL | same |
| C21 | `work/p3/p38/semantics/selftest.py` | **PASS** — 100 PASS / 0 FAIL | same |
| C22 | `work/p3/p38/agent_a_closure/failure_injection.py` | **PASS (after a memory-blocked retry)** — 59 PASS / 0 FAIL | same |
| C23 | `work/p3/p37/state_machine.py` | **PASS** — `STATE MACHINE = VALIDATED` | same |
| C24 | `work/p3/p37/known_truth.py` | **FAIL — 21 cases, PRE-EXISTING** → root cause confirmed in P4.5 (C30, definitional); the 21 FAILs are retained | same |
| C25 | `tests/audio_sync_p311/test_coordinates.py` | **BLOCKED — machine memory** | same |

## P4.5 — baseline integrity and P3.7 forensics

**Executed against the tree that became the P4.5 commits, after the P4.5 code and
tests were written.** The rows above describe the earlier `bb056a1` tree and are
unchanged; these are new measurements.

| # | suite / check | status | command |
|---|---|---|---|
| C26 | `tests/audio_sync_p4/test_baseline_hash_portability.py` (P4.5 Part A) | **PASS** — 65 cases, 10 controls, 0 FAIL, **0 skipped** | `python tests/audio_sync_p4/test_baseline_hash_portability.py` |
| C27 | frozen-fixture canonical content identity | **PASS** — `canonical_content_sha256 = 7B1FB2DCAEECBA4B…` for **both** the CRLF worktree and the LF blob; content change 0, row count change 0, field change 0 | `python work/p4.5/audit_repository.py` |
| C28 | P1 audit harnesses on the new contract | **PASS** — `work/p4.2`, `p4.3`, `p4.4` `p1-audit/audit_baseline.py` all run to completion with `verdict: MATCH`; fail-closed preserved | `python work/p4.4/p1-audit/audit_baseline.py` |
| C29 | P1 regression guard (re-run) | **PASS** — 27 PASS / 0 FAIL, exit 0 | `python work/p2/scripts/p1_regression_guard.py` |
| C30 | `tests/audio_sync_p37/test_p37_known_truth_summary.py` (P4.5 Part B) | **PASS** — 24 cases, 6 controls, 0 FAIL, 0 skipped | `python tests/audio_sync_p37/test_p37_known_truth_summary.py` |
| C31 | P3.7 independent case-level verification | **PASS (measurement)** — check 1a `111/132`, check 1b `132/132` (tautological `132/132` by construction), 195 over-claims, 0 under-claims | `python work/p4.5/p37-forensics/independent_verifier.py` |
| C32 | P3.7 reproducer determinism | **PASS** — `known_truth.py` rewrites its matrix **byte-identically** (`F46AB9E3…` before and after); exit 2, 21 FAILs | `python work/p4.5/p37-forensics/reproduce.py` |
| C33 | P4 suites re-run after the P4.5 changes | **PASS** — `test_production_sync` 182/0 (SILENT_WRONG 0), `test_downstream_state` 58 + 1 XFAIL (D8), `test_evidence_integrity` 35/0, `test_production_semantics` 60/0, `test_production_eligibility` 58/0 | each suite directly |
| C34 | `tests/full_autotest.py --level unit` (re-run) | **PASS** — 626 PASS / 0 FAIL | same |
| C35 | `tests/audio_sync_p311/test_coordinates.py` (re-run) | **BLOCKED — machine memory**, unchanged and pre-existing: `numpy._ArrayMemoryError: Unable to allocate 1.72 GiB for an array with shape (230880000,)` | same |

### Resource block during the P4.5 test run — recorded as BLOCKED, not FAIL

The first P4.5 attempt at the L1 unit level reported two failures while host free
memory was **1.01 GB of 31.5 GB** (AfterFX 5.0 GB, memory compression 4.25 GB):

```text
FAIL [unit] audio mix invariance v0.7.1
     OSError: [WinError 1455] 页面文件太小，无法完成操作。   (ERROR_COMMITMENT_LIMIT)
FAIL [unit] sync.恒定偏移 [0, 1, 7, 60, 1000, -1, -7, -60, -1000] 全部 sample-exact
     measured={0:0, 1:1, 7:0, 60:0, 1000:1000, -1:-1, -7:-7, -60:-60, -1000:-1000}
```

Neither is reported as a code failure, and neither was worked around by relaxing
a test:

* the run was repeated with `OPENBLAS_NUM_THREADS=1` / `OMP_NUM_THREADS=1`, which
  reduces BLAS worker memory and changes **no assertion**;
* `tests/audio_sync_p4/test_evidence_integrity.py`, which failed on the first
  attempt with `OpenBLAS error: Memory allocation still failed after 10 retries`,
  then passed **35/0**;
* the unit level then passed **626 PASS / 0 FAIL**;
* the sync row was investigated separately rather than assumed benign:
  `work/p4.5/diagnose_sync_offset.py` rebuilds that exact fixture and measures
  every channel — **3 consecutive runs, all 10 channels sample-exact**, including
  the two offsets (`7`, `60`) that the pressured run reported as `0`.

Conclusion: **resource-induced, not reproducible, no code change.** The P4.5
changes touch no file the sync path loads, and `tests/selftest/suites/audio_sync.py`
and `core/audio_sync.py` are unmodified by this phase.

### How these were produced, stated exactly

Rows **C1–C25** were **executed in this repository during the P4.1–P4.4 work**,
against the tree that became commit `bb056a1`. The three P4.4 commits changed
only the files they name, and no code was edited after those runs, so the results
apply to `bb056a1`.

**They were not re-executed during the documentation consolidation.** The
consolidation changed only `docs/`; a docs-only change cannot alter a test
outcome, but that is a reason, not a measurement, and it is recorded as such
rather than presented as a fresh run.

**P4.5 did re-execute them.** Rows C26–C35 are P4.5 measurements, and C33/C34
are fresh re-runs of the C1–C5/C6 suites on the post-P4.5 tree. Every re-run
agreed with its `bb056a1` value:

```text
test_production_sync       182 cases, 0 FAIL, SILENT_WRONG 0, 14 controls   (was 182)
test_downstream_state       58 PASS, 1 XFAIL (D8), 0 FAIL, 7 controls      (unchanged)
test_evidence_integrity     35 PASS, 3 controls                            (unchanged)
test_production_semantics   60 PASS, 8 controls                            (unchanged)
test_production_eligibility 58 PASS, 9 controls                            (unchanged)
tests/full_autotest.py --level unit        626 PASS / 0 FAIL               (unchanged)
```

P4.5 changed no production file, so agreement was expected; it is recorded as a
measurement rather than asserted as an expectation. The one exception is C11,
whose *contract* P4.5 deliberately replaced — see C26 and
[`p4.5-baseline-integrity.md`](p4.5-baseline-integrity.md).

### V9's status changed, recorded rather than rewritten

Section V9 above records `tests/audio_sync_p311/test_coordinates.py` as
**71 PASS / 0 FAIL**. It was true when P4 ran it. Between P4.2 and P4.7 it was
**BLOCKED by machine memory** (a 1.72 GiB allocation in P3.11 research code, with
3.8 GB free of 31.5 GB). The earlier result is left standing as history; the
current state is row C25.

```text
status now : the block is LIFTED. The test runs and passes — 71 PASS / 0 FAIL
             in ~8 s — first again during P4.8, and re-run by P4.11 at HEAD
             463b3e0 with the same result. Nothing in the harness or the test was
             changed to lift it; the host had the memory.
```

A resource block is still never counted as a pass retroactively: the block is kept
as history *and* the current PASS is recorded next to it, which is the only honest
way to report "it works now" without rewriting what happened then.

## What is NOT validated (updated)

The table at the end of the P4 section is unchanged and still stands, with three
entries updated by later phases:

| item | then (P4) | now |
|---|---|---|
| `core/channel_sync.py` integration | **NOT DONE** — no production wiring | **DONE and ENABLED** — `--channel-sync`, governed by `correction_eligibility()` |
| downstream representability of `UNRESOLVED_GAP` | **UNKNOWN** — D6 | **BLOCKED** (P4.1's record, not rewritten); the frame-domain half is implemented, the plan-window half is `T5.4b`, XFAIL, owner D8 |
| anything about correction *quality* | **NOT VALIDATED** — no threshold | **still `NOT ESTABLISHED`** — no threshold was invented, and production eligibility does **not** depend on one |

Still not validated, unchanged: the in-gap delay (`UNOBSERVABLE`), a calibrated
per-window σ, the C1306 ch3→ch2 instrument disagreement, behaviour outside
C1305/C1306, the offline `linear` / `next` arms.

Added by P4.4 and still open: the enabled path's cost profile (§5 of
[`known-limitations.md`](known-limitations.md)) — it measures every stream,
including the ones it then refuses.


---

# V10 — P4 closure roll-up (P4.6 – P4.11)

Written at HEAD `463b3e0`, on the branch
`research/audio-sync-p3-production-semantics`. **Every row below was executed in
this session**, not copied from a previous phase's record. The four sections above
are P4's own history and are left as written; this is the current state.

## Where each suite stands at HEAD `463b3e0`

| # | suite | command | result |
|---|---|---|---|
| 1 | `test_time_varying_exec` | `python tests/audio_sync_p4/test_time_varying_exec.py` | **PASS** — 380 cases, 337 value / 23 refusal / 20 control, `SILENT_WRONG 0` |
| 2 | `test_production_sync` | `python tests/audio_sync_p4/test_production_sync.py` | **PASS** — 184 cases, 121 value / 49 raise / 14 control, `SILENT_WRONG 0` |
| 3 | `test_production_semantics` | `python tests/audio_sync_p4/test_production_semantics.py` | **PASS** — 63 cases, 10 controls |
| 4 | `test_production_eligibility` | `python tests/audio_sync_p4/test_production_eligibility.py` | **PASS** — 58 cases, 9 controls |
| 5 | `test_evidence_integrity` | `python tests/audio_sync_p4/test_evidence_integrity.py` | **PASS** — 35 cases, 3 controls |
| 6 | `test_downstream_state` | `python tests/audio_sync_p4/test_downstream_state.py` | **PASS** — 58 behaviours, **1 contract gap OPEN (owner D8)**, 7 controls. The suite states that XFAIL cases are not passes |
| 7 | `test_baseline_hash_portability` | `python tests/audio_sync_p4/test_baseline_hash_portability.py` | **PASS** — 65 cases, 10 controls |
| 8 | `test_coordinates` (P3.11) | `python tests/audio_sync_p311/test_coordinates.py` | **PASS** — **71 PASS / 0 FAIL**. The memory block recorded above is lifted |
| 9 | `test_p37_known_truth_summary` | `python tests/audio_sync_p37/test_p37_known_truth_summary.py` | **PASS** — 24 cases, 6 controls; its **21 historical FAILs are retained** |
| 10 | P1 regression guard | `python work/p2/scripts/p1_regression_guard.py` | **PASS** — 27 PASS / 0 FAIL |
| 11 | acquisition tooling E2E | `python tests/audio_sync_acquisition/test_e2e.py` | **PASS** — **33 value / 9 control / 0 FAIL / 0 CONTROL_FAILED** on 23 generated fixtures |
| 12 | signal reproducibility | `python tests/audio_sync_acquisition/generate_signal.py --verify` | **PASS** — re-derived and byte-identical |
| 13 | P4.9B R1 known-truth chain | `python work/p4.9b/r1_run.py --controls` | **PASS** — 15 cases, 3 `CONTROL_FIRED`, 14 applied / 1 refused, mechanics EXACT on all 14 |
| 14 | P4.9A short-clip sweep | `python work/p4.9a-short/sweep.py --pairs "2-0 2-1 2-3"` | **PASS** — reproduced 381 PASS / 30 FAIL / 323 refusals, gate OK 88, all `CH3→CH4` |

Not run in this session, and **not** counted as passes:

```text
full_autotest --level full   NOT RUN — drives hardware/video suites, outside P4
```

## What the two re-runs established

Rows 13 and 14 are re-measurements of the headline P4.9A/P4.9B results, run to
confirm the published numbers still reproduce rather than copying them.

**P4.9A reproduced exactly**: 411 cases → `PASS 381 / FAIL 30`, gate OK **88** (all
`CH3→CH4`), verifier `AGREE 145 / DISAGREE 30 / NOT_COMPARABLE 6 /
VERIFIER_NOT_QUALIFIED 230`, `CH3→CH4` offset p50 **0.0** with range 3.0 samples,
block-lag range p50 0.467 / p90 116.455, 117 cases ≤2 samples and 41 >10. Every
published figure matched.

**P4.9B R1 reproduced exactly**: 15 cases, all PASS; estimate error **0.000** and
known-truth residual **−0.000** on every constant-delay case; mechanics EXACT on
all 14 applied cases; the gap/reentry case refused correctly; all three controls
fired.

## The P4.6 – P4.11 record, and what each changed

| phase | what it established | production effect |
|---|---|---|
| P4.6 | the per-window path is executable as an **explicit opt-in** that refuses without a named policy and coordinate | entry added; default path unchanged |
| P4.7 | what actually reaches apply on real material — the evidence gate refuses the long corpus before the time-varying step | **3 defects found and fixed** in the production reachability gate |
| P4.8 | no measurement geometry dominates: the shipped 200/100 ms is best on drifting material, 2000/1000 is best on real material, at an 18× transition-resolution cost | **none** — geometry is not a production knob |
| P4.9A | the short clips suit the fixed-delay path, and the correct number of applied corrections there is **zero** | **none** |
| P4.9B | known-truth residual is exactly 0 on constant integer delays; `uncertainty_samples` is not emitted by any production module | **none** |
| P4.10 | **no source on this machine carries an independent delay truth** (G0 27 / G1 166 / G2 0 / G3 0), and P4.9A's `CH3→CH4` zero is a property of the wiring, not a link measurement | **none** |
| P4.11 | the acquisition protocol is executable, tested and tracked | **none** |

Across P4.6–P4.11 the only production change is P4.7's reachability fix, which
**restricts** what may proceed (`core/audio_sync_tv_exec.py`, `core/channel_sync.py`).
Every other phase is measurement, tooling or documentation.

## What is validated now that was not before

| item | then | now |
|---|---|---|
| error size against a known truth | not measured | **0 samples** on constant integer delays, up to 700 on within-window transitions — bounded by the step size, measured on 15 fixtures |
| which gate term binds | unknown | measured per corpus (P4.9B R2): `min_usable_fraction` on short clips, the **finite-frame count itself** on long clips |
| estimator error vs bandwidth and SNR | unknown | ≤ 1 sample over 10 233 frames across 5 bands × 3 SNRs; and the narrowest bands yield **zero** measurable frames because they stay outside the estimator's own band |
| real wireless ground truth | assumed absent | **proven** absent on this machine, with the reason measured rather than inferred |

## What is still not validated

Unchanged from the sections above, plus what the later phases added:

```text
in-gap delay                                  UNOBSERVABLE
a calibrated per-window sigma                 NOT AVAILABLE — no production field emits one
C1306 ch3->ch2 instrument disagreement        UNRESOLVED
behaviour outside C1305/C1306                 NOT MEASURED
offline linear / next arms                    NOT RUN
real wireless latency accuracy                BLOCKED — no independent truth exists yet
D3 residual acceptance                        NOT ESTABLISHED
the enabled path's cost profile               KNOWN LIMITATION (known-limitations.md section 5)
```