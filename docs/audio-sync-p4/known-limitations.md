# Audio Sync — known limitations, failures and open items

**One place for everything that is not green.** A maintainer should be able to
read this file alone and know exactly what is unresolved, what failed, what was
never run, and what is a deliberate product deferral rather than a defect.

Status words are used strictly:

```text
FAIL            ran, and did not pass          — with the scope of the failure
BLOCKED         could not run, and why         — with the distinction from FAIL
XFAIL           a known contract gap, held open on purpose, NOT a pass
NOT ESTABLISHED no value exists and none may be invented
DEFERRED        a product decision deliberately not taken
KNOWN LIMITATION measured, bounded, deliberately not fixed
NOT RUN         not executed in the current environment
```

Nothing below is softened. Where a number is quoted it is the number that was
measured, in the scope it was measured in.

---

## 1. `work/p3/p37/known_truth.py` — 21 FAIL, PRE-EXISTING
```text
status   : RESOLVED in P4.5 — root cause confirmed as definitional.
           The 21 FAILs are RETAINED; nothing was deleted to get green.
           See docs/audio-sync-p3/p3.7-discrepancy-audit.md
```

> **P4.5 outcome.** The discrepancy was a **denominator/definition conflict**, not
> an algorithm problem. `known_truth.py` derives the expected state sequence from
> the **measurement mask** (the independent oracle the policy cannot see) — that
> is definition **1a**, giving `current` = **1/22** and **111/132** overall. The
> `132/132` in the documents is definition **1b**, which derives the expectation
> from the **policy's own claimed mask**; because the engine defines
> `state == derive_states(resolved)`, 1b is satisfied **by construction** for
> every policy and cannot detect an over-claim. The documents' quoted
> `True/True/False` block matches a recomputed 1b block exactly, while the
> unchanged reproducer prints `False/False/False`.
>
> **Corrected reading:** `current` does not merely fail the `resolved` check — it
> fails the state-sequence and fake-`TRUSTED` checks too, labelling **195**
> refused windows `TRUSTED` in 21 of 22 cases. So `0 fake TRUSTED` is **not**
> true of `current`. The *character* of the finding is unchanged
> (**semantic over-claim, not algorithmic error**), but its extent as stated in
> the P4 documents was too small. Both statistics are kept and labelled; the
> better-looking one is not selected. `D2` remains `DEFERRED`.

The original record of the conflict, preserved verbatim below.
```text
status   : FAIL (21 of 132 cases), exit 2
scope    : the `current` gap policy's row of the known-truth matrix
           state sequence correct : 1/22
           no fake TRUSTED        : 1/22
           `resolved` flags       : 1/22
           all five other policies: 22/22 on every check
```

### It is not a P3-P4 regression, and that was proven rather than argued

* The script imports **nothing** from the repository. Its only imports are
  stdlib, numpy and its own sibling module `work/p3/p37/semantics.py`.
* Run from an **isolated copy** in a temporary directory with no repository on
  `sys.path`, it reproduces **identically** — same 21 failures, same table.
* It therefore cannot have been affected by P4.3 or P4.4, which changed only
  files under `core/`, `production/`, `tests/audio_sync_p4/` and `docs/`.

### The discrepancy, recorded not resolved

`docs/audio-sync-p4/gap-policy-decision.md` §3.2 records `current` as

```text
state sequence matches the mask-derived expectation : 132/132 correct
never labels a refused window TRUSTED               : 132/132 correct
`resolved` flags backed by a measurement            : 1/22
```

The live run reports **1/22** on the first two as well. This is a P3.7
bookkeeping discrepancy of the class already registered in
`docs/audio-sync-p4/evidence-freeze.md` §7b (ten P3 summary ranges that do not
reproduce from P3's own detail tables).

**No conclusion is drawn about `current`.** `gap-policy-decision.md` is explicit
that `current`'s failure is a **semantic over-claim, not an algorithmic error**,
and nothing here contradicts that. P3.7's frozen research was **not edited**, and
the two documents were **not** reconciled by rewriting either.

*(P4.5 superseded the "no conclusion" line above by establishing the root cause —
see the P4.5 block at the top of this section. The frozen research and the 21
FAILs are still not edited.)*

> **This also means P3 is not "all green".** The P3 line's verdicts stand on the
> per-phase reports and the tracked summaries; this one script's `current` row
> does not reproduce from the P3.7 summary, and that is stated rather than
> averaged away.

---

## 2. Blocked by machine resources — distinguished from real failures

> **RESOLVED — the block is lifted, and that is recorded rather than deleted.**
> `tests/audio_sync_p311/test_coordinates.py` **runs and passes**:
> **71 PASS / 0 FAIL in ~8 s**, re-run most recently in P4.11 at HEAD `463b3e0`
> and reported by P4.8 before that. It first ran again during P4.8, so the block
> below describes **P4.2–P4.7**, not the current tree. Nothing was changed in the
> harness or the test to lift it: the host simply had the memory. Per this file's
> own rule, a retry that succeeds is the evidence that the earlier block was
> environmental, and **a resource block is never reported as a pass** — so the
> block is kept as history and the current PASS is recorded next to it.

`tests/audio_sync_p311/test_coordinates.py` **was BLOCKED**, from P4.2 until P4.8:

```text
error      : numpy._ArrayMemoryError: Unable to allocate 1.72 GiB
host free  : 3.8 GB of 31.5 GB at the time of the first record
allocation : work/p3/p311/production_chain_p311.py::coord_labels —
             P3.11 research code that materialises int64 per sample by design
touched by : nothing in P3-P4.4
```

During P4.4 host free memory fell as low as **0.44 GB of 31.5 GB** (the machine's
own AfterFX / MATLAB / PyCharm / node sessions). At that point two harnesses
were blocked, and the block is evidenced rather than asserted:

| harness | at 0.44 GB free | after recovery |
|---|---|---|
| `work/p4.4/real_material_validation.py --only silent_ch4` | `MemoryError`, then `Unable to allocate **11.0 MiB**` | **PASS 7/7** |
| `work/p4.4/performance.py` | `std::bad_alloc` inside an FFT | **PASS** (0 default-path calls) |
| `python -c "import scipy"` | `SystemError: error return without exception set` | imports normally |
| `work/p3/p38/agent_a_closure/failure_injection.py` | raised with the message *"host memory pressure, not an assertion failure — the run is not evidence and must be repeated with memory available"* | **PASS 59/0** |

An **11 MiB** allocation failing is what identifies the condition as host
pressure rather than a code defect: the estimator's bounded reader works in
1 MiB blocks, and its largest allocation on this material is the **1.44 M-sample
(30 s @ 48 kHz) anchor segment** — a fixed, designed size, not one proportional
to the file.

**A resource block is never reported as a pass**, and a retry that succeeds is
recorded as the evidence that the earlier block was environmental.

---

## 3. `T5.4b` — XFAIL, owner D8

```text
case   : a consumer can discover that 234/481 PLAN WINDOWS were policy-filled
status : XFAIL  (NOT a pass)
owner  : D8 (corrected from D1 by P4.3)
```

The plan-window population has **no producer in production**: the only object
that can count plan windows is `core/audio_sync_tv.CorrectionPlan`, and it is
imported by nothing. A field nothing can populate could not be tested, and the
contract itself forbids reporting a coverage figure without naming its population
(`d1-uncertainty-contract.md` §4.2).

P4.3 **split** the original compound claim rather than leaving a stale reason:
`T5.4a` was promoted to a positive assertion (the **frame-domain** evidence
contract exists and is discoverable), and `T5.4b` stays open on the window
population. No XFAIL was deleted.

It flips when D8 wires a producer that carries plan-window counts.

---

## 4. Product decisions that remain open

| id | subject | state | why it is open |
|---|---|---|---|
| **D2** | gap policy | **DEFERRED** | no candidate dominates; the discriminating rows depend on a discontinuity position **inside the gap**, which is not observable |
| **D3** | residual acceptance | **NOT ESTABLISHED** | no reference for "good enough"; no calibrated per-window σ. **No threshold was invented, raised or relaxed**, and production eligibility does not depend on one |
| **D5** | coordinate contract | **DEFERRED** | choosing answers *anticipate or lag*, a product question. `latest-start` is an implementation default **for reproducibility**, not a selected contract |
| — | enablement of the **per-window** path | **NOT TAKEN** | needs D2 and D5 |
| — | downstream representability (`D6`) | P4.1's **BLOCKED** record **not rewritten** | consumers outside this repository have not been inventoried |

None of these is a documentation gap, and none of them blocks the enabled
fixed-delay path.

### 4.1 What P4.6–P4.11 added to this table, without moving any status

| id | state | what the later phases did to the *reason*, not the decision |
|---|---|---|
| **D2** | **still `DEFERRED`** | P4.11's acquisition tooling now refuses to run a time-varying analysis unless a policy is named, so the deferral is enforced by construction rather than by convention |
| **D3** | **still `NOT ESTABLISHED`** | P4.9B executed the R1–R4 study that this table's reason refers to. It measured the synthetic residual exactly (0 samples on constant integer delays) and confirmed that **`uncertainty_samples` is not emitted by any production module at all** — the field exists only in P3 research documents. The blocker is now narrower and specific: there is no accuracy measurement on **real** material with an independent truth, and no population large enough to set a quantile. See `p4.9b-residual-calibration.md` §6 |
| **D5** | **still `DEFERRED`** | unchanged; the same explicit-argument enforcement applies |
| — | enablement of the **per-window** path | **NOT TAKEN** — unchanged |
| — | **real wireless ground truth** | **`BLOCKED`** — and this is now an enumerated data gap rather than an open question. P4.10 probed **193 containers / 173 with audio / 148 multi-mono** on this machine, graded them **G0 27 / G1 166 / G2 0 / G3 0**, and rejected the one candidate a file name suggested (it has no audio stream at all). It also measured *why* P4.9A's `CH3→CH4` zero means nothing about a wireless link: those two streams are the same feed, internally coincident (median lag 0.06 samples, range 4.55 over 109 resolved clips) while cross-group correlation is ≈0. **P4.11 built and tested the tooling that runs the moment 16 takes exist.** |

**No status in the main table moved.** What changed is the quality of the reason
each one carries: `BLOCKED` now names the evidence that must be created, and D3's
gap is enumerated item by item instead of being described as "no reference for good
enough".

---

## 5. Known limitations of what IS enabled

| limitation | state |
|---|---|
| the enabled path measures **every** stream, including tracks it then refuses | **KNOWN LIMITATION** — measured on a real 4 s 4-mono C1305 clip: 1.234 s wall (0.308 s per source second), traced peak 13.15 MB, RSS delta +65.9 MB. Not addressed; recorded so a future phase has a baseline |
| the enabled path's memory is **not** P4's figure | P4 measured the plan/applier path at 9.10 MB peak. `run_channel_sync` is a different path with a 1.44 M-sample anchor segment. **The two numbers are not comparable and neither is quoted as the other** |
| `shift_stream` carries no guard of its own | **BY DESIGN** — the guard is at every caller. A static audit enumerates the callers and exercises each one; the finding is left visible in the audit output rather than filtered out |
| `AudioSyncResult` has no field for the plan-window population | open, owner D8 — see `T5.4b` |
| the C1306 ch3→ch2 instrument disagreement | recorded in P3.11, **not averaged** |
| `--channel-sync-tv` on the **transparent** path | **KNOWN LIMITATION** — accepted since 2026-10-29 (before that the three flags were silently dropped in `transparent_main`; the encoded path always forwarded them). The TV applier writes the same per-track PCM and reuses the same remux stage, but the *combination* has **no dedicated video regression** (`p4.6-real-corpus.md` §7: "structural reasoning, not a measured video regression"). Production reachability on the C1305/C1306 long takes remains 0/6 whole files, so this limitation is about the mechanism, not a recovered fix |

---

## 6. Unreconciled historical contradiction (P3, for the maintainer)

Two formal P3 documents state different things about the same quantity:

```text
P3.2-FINAL-REPORT.md / PRODUCTION-REQUIREMENTS.md §5
    correction NOT VALIDATED; C1305 MIXED; C1306 no measurable effect

P3.10 §6 / P3.11 §6
    a large, independently verified median improvement on the same corpora;
    on the accepted population five of six pairs improve in every statistic
```

Both are measured. The later one does not claim the earlier was wrong. The
difference is plausibly one of conditions (grid, window length, hop, which
windows count as accepted, whether the tail is included). **No document settles
it, and this consolidation does not settle it either** — the P3 entry point
records it as an open item, consistent with the line's rule that historical
documents are immutable and later evidence is recorded as new evidence.

---

## 7. The frozen fixture's recorded hash is LINE-ENDING DEPENDENT
```text
status   : RESOLVED in P4.5 — the acceptance test is now line-ending independent.
           The recorded historical raw hash is RETAINED, not replaced.
           See docs/audio-sync-p4/p4.5-baseline-integrity.md
```

> **P4.5 outcome.** The raw-byte comparison was replaced by an explicit versioned
> contract, `sha256-lf-normalized-v1`, implemented in the tracked module
> `tests/audio_sync_p4/baseline_identity.py`. `raw_byte_sha256` (line-ending
> dependent) is now reported for provenance only; `canonical_content_sha256`
> (line-ending independent) is the acceptance test. Both checkout forms normalise
> to `7B1FB2DCAEECBA4BD170973C336F470D0E7B6746A551E41A88D9032D3A6B464B`.
> The three `work/p4.*/p1-audit/audit_baseline.py` harnesses now import that
> contract, and `tests/audio_sync_p4/test_baseline_hash_portability.py` (65 cases,
> 10 controls) is the permanent regression. Fail-closed is **retained**: a real
> content change still aborts with exit 4. The fixture itself was not edited
> (content change 0, row count change 0, field change 0).

Found by the documentation consolidation when it read the pushed file back from
GitHub and hashed it. **Pre-existing and not caused by any P3/P4 phase.** At the
time it was found it was **NOT fixed** — the fix is an authorised content
decision, not a documentation one — and P4.5 is that authorisation.

```text
working tree (this machine)   55123 bytes   549 CRLF    sha256 36242616062FFEC2…
committed blob / on GitHub    54574 bytes     0 CRLF    sha256 7B1FB2DCAEECBA4B…
difference                    549 bytes   = exactly 549 CRLF -> LF conversions
```

`.gitattributes` exists and pins **`*.patch text eol=lf`** with this rationale,
written for exactly this failure mode:

> *"With `core.autocrlf=true` … git re-encodes text files to LF at commit time,
> which silently changed the patch hashes and made … fail on a clean checkout.
> Force LF so the recorded hashes stay valid everywhere."*

The frozen channel-sync baseline has **no such rule**, so:

| environment | what the file looks like | recorded hash matches? |
|---|---|---|
| this machine — `core.autocrlf=true`, CRLF checkout | 549 CRLF | **yes** (`36242616062FFEC2…`) |
| a clean Linux/macOS checkout, or any blob-level read | 0 CRLF | **no** (`7B1FB2DCAEECBA4B…`) |

### What this affects

`docs/hardware-decode/final-report.md` HD-G06 records the hash as
`36242616062ffec2`, and `work/p4.2/`, `work/p4.3/` and `work/p4.4/`'s
`p1-audit/audit_baseline.py` all assert exactly that prefix. On a checkout where
the file lands as LF they would **`ABORT: fixture hash mismatch; … nothing below
can be concluded` and return exit 4** — which is the P4.2 discipline working as
designed: the audit refuses to conclude anything from a fixture it cannot
identify, rather than proceeding on a near-miss.

**Every P1/P4 audit result recorded in this repository was produced on this
machine, where the hash matches.** That is stated so the results are not read as
platform-independent.

### Why it was not fixed at the time (superseded by P4.5)

Any fix is a content decision outside a documentation task's authority:

1. add a `.gitattributes` rule for the fixture — changes what **every** checkout
   writes, and would invalidate the recorded CRLF hash unless `eol=crlf` is
   chosen, which in turn changes what a Linux clone stores;
2. re-record the hash in `docs/hardware-decode/final-report.md` — that is a
   recorded HD-G06 result from another phase, and revising it is not this task's
   call;
3. change the audit scripts — they are P4-owned tracked tests whose assertion is
   the point.

Recorded, not resolved. A future phase with authority over the fixture's
provenance should choose one and say so.

---

## 8. Documents that no longer state current status

These are **frozen and are not rewritten.** The index correction lives in
[`../audio-sync-p3/P3-RESEARCH-OVERVIEW.md`](../audio-sync-p3/P3-RESEARCH-OVERVIEW.md),
not inside the history.

| file | superseded statement |
|---|---|
| `../audio-sync-p3/README.md` | header `P3 NOT JUSTIFIED`; describes the original P3 phase only |
| `../audio-sync-p3/PRODUCTION-REQUIREMENTS.md` §5/§6 | *"no plan has ever been applied to real audio and re-measured"* — P3.10 and P3.11 did exactly that |
| `../audio-sync-p3/FINAL-RESEARCH-SUMMARY.md` | covers P1–P3.8 only; its closing section predates the real-PCM apply |
| `README.md` (this directory) | the P4-era status block says the feature is OFF and the status is `READY FOR PRODUCTION POLICY`. **True of P4**; superseded by P4.2–P4.4, and a current-status block now precedes it. **Updated in P4.11**: that block now names all eleven later phases, lists the four superseded lines and the three superseded *statuses*, and points at `validation.md` -> V10 |
| `P4-FINAL-REPORT.md` | states `core/audio_plan.py` byte-identical to HEAD. True of P4/P4.2; **P4.3 changed that file** to carry evidence through the plan boundary. It is also a **P4-era document covering P4–P4.4 only** — the current validation roll-up is `validation.md` -> V10, not this report |
| `production-readiness.md` §3 | the overall verdict `READY FOR PRODUCTION POLICY` is **unchanged and still correct** — P4.5–P4.11 moved no status in that table. Recorded so a reader does not look for a later revision that does not exist |
| `validation.md` (top half) | the V1–V9 record is P4's own; its V9 row and the *"What is NOT validated"* table are superseded by the **V10** roll-up appended in P4.11. The top half is not rewritten |

---

## 9. Evidence that does not live in this repository

The raw P3/P4 research tree (`work/`) is **gitignored** — 4700 files, 214 GB,
almost entirely regenerable measurement data and real camera media.

| what | where it is | is a tracked substitute available? |
|---|---|---|
| `work/p3/P3-FINAL-REPORT.md` and the P3.1–P3.8 per-phase reports | local only | **yes** — `../audio-sync-p3/FINAL-RESEARCH-SUMMARY.md` (P1–P3.8) and `P3-RESEARCH-OVERVIEW.md` |
| `work/p3/p310`, `p311`, `p312` reports | local only | **yes** — `p3.10/p3.11/p3.12-provenance.md` pin identity, files and reproduction commands |
| `work/p4.1`/`p4.2`/`p4.3`/`p4.4` harnesses and JSON | local only | **partly** — the conclusions and numbers are in the phase documents; the harness sources are not tracked |
| `work/p4.6`–`work/p4.8` harnesses, `results.jsonl` and matrices | local only | **partly** — same shape: the conclusions are tracked, the 5 571 MB of P4.6 artifacts and the P4.8 append-only results are not |
| `work/p4.9a-short` (manifest, `results.jsonl`, `derived.json`) | local only | **partly** — the numbers are in `p4.9a-short-clip-revalidation.md`; the harness is not tracked |
| `work/p4.9b` (R1–R4 harnesses, per-frame vectors) | local only | **partly** — same; `p4.9b-residual-calibration.md` carries the tables and the limits |
| `work/p4.10-source` (193-container inventory, truth-anchor audit) | local only | **partly** — `p4.10-real-source-qualification.md` carries the graded counts and the channel-forensics table |
| `work/p4.11-acquisition` fixtures (1.4 GB of generated media) | local only | **yes, and this one is different — see below** |
| **the acquisition tooling itself** | **TRACKED** — `tests/audio_sync_acquisition/` | **yes** — this is the first P4 research/tooling phase whose code is in the repository rather than only under `work/`. It generates its own signal on demand and its fixture generator (`make_fixtures.py`) is tracked too, so the test suite rebuilds its own 23 fixtures from source |
| **the acquisition fixture generator** | **TRACKED** — `tests/audio_sync_acquisition/make_fixtures.py` | **yes** — added at P4 closure. Without it `test_e2e.py` could not run on a clean clone: its 23 fixtures were built by an untracked `work/` script, and a test whose inputs cannot be rebuilt is not a test. It writes ~1.4 GB into the gitignored `work/`; the generator is tracked, its output is not |
| the P2 corpus (104 GB) and the real candidates | local only | **yes** for the conclusion: the frozen 137-corpus CSV is tracked with a recorded hash, and the P1 guard reproduces against it |
| the empirical p3 corpus (24 GB) | local only | **no** — by design; the conclusions, hashes and reproduction commands are tracked instead |

**The dependency is stated rather than hidden**: a maintainer can read every
conclusion and every number from the repository, but re-running the P3 harnesses
requires the local tree. Where a document cites a `work/` path, that path is
`work/`-relative and is **not** presented as a repository API.

Full per-artifact classification:
`work/p3-p4-doc-consolidation/inventory.json`.

---

## 14. Long-take default alignment (`docs/design/long_take_default_alignment.md`) — 2026-10-30

Implementation is complete for S1–S6 + S3/A6 + S5 partial; the acceptance
judgement against the K deliverables is **not green** and has two independent
causes. Numbers and reproduction commands live in
`docs/design/long_take_impl_checkpoint.md`; this section states the status
strictly.

### 14.1 DONE (verified, not a pass claim for acceptance)

```text
status   : implemented + unit/integration green
- default auto long-take path: constant-first, whole-file time-varying fallback,
  stereo group binding, round-2 residual, polarity, long-take report block;
- multichannel PCM layouts (stereo/quad/5.1/7.1 in one stream): per-channel
  decode via built-in AudioPCMReader, output re-interleaved per source stream
  with audio_wav.write_wav, structure preserved (T5/T6 in
  work/long_take/s3/multichannel_check.py);
- S5 partial: residual windows and stereo-binding candidate pairs are measured
  in parallel (thread pool, deterministic order); continuity tracker itself is
  sequential by definition;
- preservation/gpac.py: MP4Box temp files pinned to the media volume
  (`.gpactmp`) — without it a 50 GiB remux fails with ENOSPC on C:.
Unit: 1008 PASS / 2 FAIL (both are environment-only: no real Sony corpus).
Integration (synthetic): ALL PASS after the multichannel refactor.
```

### 14.2 ACCEPTANCE — NOT MET (measured, real 80 min files)

```text
status   : FAIL against the task-book tolerances as written
curve layer (median |Δ| ≤1 / p90 ≤5 / max ≤50 samples, vs K research fine curve)
  C1306 CH3  1.81 / 15.2 / 54        CH4  2.00 / 15.6 / 27
  C1305 CH3  5.88 / 45.1 / 62        CH4 11.00 / 45.0 / 88
audio layer (median ≤2 / p90 ≤10 / ≥90% of 5 min blocks ≤5 samples, ours vs K)
  C1306 CH3  3 / 9 / 77% ≤5          CH4  3 / 6.5 / 77% ≤5
  C1305 CH3  5 / 28 / 55% ≤5         CH4  8 / 116 / 35% ≤5
reason   : K's own measured-vs-applied consistency (research doc §12.3) is
           median 5–23 / p90 17–63 samples — the tolerances are below the
           content-ambiguity noise floor of the materials themselves. Our
           round-1 curve is bit-identical to K pass1 (0/0/0 over 4808 points);
           all remaining difference is round-2 residual on ambiguous content.
decision : pending user (tighten residual replication vs relax tolerance to the
           measured noise floor).
```

### 14.3 K ORACLE INCONSISTENCY — blocks the C1305 acceptance verdict

```text
status   : BLOCKED on a defect in the reference data, not in our code
C1306 restored deliverable (2026-10-25) matches K's research fine curve
   (measured-vs-pass1: median 2.7 / p90 18–43).
C1305 restored deliverable (2026-10-29, four days later) instead matches the
   old GLOBAL LINEAR solution: solve/drift a=1015.09, b=-0.012 ppm
   (measured-vs-drift median 3.1 / p90 18.9 / max 37.9), while measured-vs-pass1
   is median 5.7–10 / p90 79–81.
=> the C1305 restored audio was almost certainly rebuilt from the wrong
   intermediate (old global-aligned audio) during a late restore; it is not the
   fine audio described by K research doc §12/§16.
decision : pending user — either re-export the C1305 restored deliverable from
           the fine audio, or accept the research fine curve JSON as the C1305
           oracle (recorded here so this is never silently "passed").
```

### 14.4 OTHER OPEN ITEMS

```text
A1 regression corpus (226 clips / 584 tracks)     : NOT RUN / corpus not
   available on this machine; task book §8.2 allows a hold-out set with the
   gap stated — the 4 synthetic integration cases + real 80 min files are what
   was actually run. The regression that WAS run and classified (2026-10-30):
   unit 1011 PASS / 2 FAIL (both environment-only: real Sony corpus absent);
   L3 full run — 13 environment-only FAILs (missing `testsets/` real media:
   Sony/DJI/A7M5), the legacy C15–C32 channel-sync expectations updated for
   the new defaults (anchor CH1→CH8, any-non-constant → whole-file TV, TV
   failure → unaligned original; mechanism tests pin the old anchor order),
   one pre-existing failure re-labelled (C32 sparse-speech evidence gate fails
   identically on the HEAD baseline), and two real bugs found by the run were
   fixed: `ref_of` UnboundLocalError on bound-group + fixed representative, and
   `fixed_files` empty for mono-multistream layouts. Also fixed a 16-bit `ipcm`
   sample-entry false positive that made transparent remux fall back to a byte
   copy (C17).
Transparent 50 GiB remux (container layer)        : KNOWN LIMITATION (pre-
   existing). GPAC rewrites the whole file for every edit; `-ref` was measured
   at 14 h without finishing on the 50 GiB C1305 stage, so the user's transparent
   command cannot produce a finished container for these files. K avoided it
   with a sequential fast_restore copy; porting that into production is a
   container task, decision pending. Audio-domain acceptance was therefore run
   through the same run_channel_sync (transparent=False).
Remux failure terminal status                     : DEFERRED. Remux failure +
   fallback copy still reports status=applied with file_copied=true (log says
   "byte copy"). Whether this must become verify_failed is a product decision.
Boundary bias of the K-parity anchor convention   : KNOWN LIMITATION. Anchors
   are placed at the 2-minute bin start (K pass1 convention) to keep round-1
   bit-identical; under extreme linear drift this biases by ~slope×60 s
   (synthetic T2 0.5 sample/s = 173 ppm shows ~8 samples residual; K real
   material measures ≤0.04 ppm).
>8 channels                                       : DEFERRED by decision — only
   via `--audio-plan alignment=enabled`.
```
