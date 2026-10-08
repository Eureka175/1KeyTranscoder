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
