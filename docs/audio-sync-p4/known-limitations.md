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

`tests/audio_sync_p311/test_coordinates.py` is **BLOCKED**, and has been since
P4.2:

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

---

## 5. Known limitations of what IS enabled

| limitation | state |
|---|---|
| the enabled path measures **every** stream, including tracks it then refuses | **KNOWN LIMITATION** — measured on a real 4 s 4-mono C1305 clip: 1.234 s wall (0.308 s per source second), traced peak 13.15 MB, RSS delta +65.9 MB. Not addressed; recorded so a future phase has a baseline |
| the enabled path's memory is **not** P4's figure | P4 measured the plan/applier path at 9.10 MB peak. `run_channel_sync` is a different path with a 1.44 M-sample anchor segment. **The two numbers are not comparable and neither is quoted as the other** |
| `shift_stream` carries no guard of its own | **BY DESIGN** — the guard is at every caller. A static audit enumerates the callers and exercises each one; the finding is left visible in the audit output rather than filtered out |
| `AudioSyncResult` has no field for the plan-window population | open, owner D8 — see `T5.4b` |
| the C1306 ch3→ch2 instrument disagreement | recorded in P3.11, **not averaged** |

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
| `README.md` (this directory) | the P4-era status block says the feature is OFF and the status is `READY FOR PRODUCTION POLICY`. **True of P4**; superseded by P4.2–P4.4, and a current-status block now precedes it |
| `P4-FINAL-REPORT.md` | states `core/audio_plan.py` byte-identical to HEAD. True of P4/P4.2; **P4.3 changed that file** to carry evidence through the plan boundary |

---

## 9. Evidence that does not live in this repository

The raw P3/P4 research tree (`work/`) is **gitignored** — 4700 files, 214 GB,
almost entirely regenerable measurement data and real camera media.

| what | where it is | is a tracked substitute available? |
|---|---|---|
| `work/p3/P3-FINAL-REPORT.md` and the P3.1–P3.8 per-phase reports | local only | **yes** — `../audio-sync-p3/FINAL-RESEARCH-SUMMARY.md` (P1–P3.8) and `P3-RESEARCH-OVERVIEW.md` |
| `work/p3/p310`, `p311`, `p312` reports | local only | **yes** — `p3.10/p3.11/p3.12-provenance.md` pin identity, files and reproduction commands |
| `work/p4.1`/`p4.2`/`p4.3`/`p4.4` harnesses and JSON | local only | **partly** — the conclusions and numbers are in the phase documents; the harness sources are not tracked |
| the P2 corpus (104 GB) and the real candidates | local only | **yes** for the conclusion: the frozen 137-corpus CSV is tracked with a recorded hash, and the P1 guard reproduces against it |
| the empirical p3 corpus (24 GB) | local only | **no** — by design; the conclusions, hashes and reproduction commands are tracked instead |

**The dependency is stated rather than hidden**: a maintainer can read every
conclusion and every number from the repository, but re-running the P3 harnesses
requires the local tree. Where a document cites a `work/` path, that path is
`work/`-relative and is **not** presented as a repository API.

Full per-artifact classification:
`work/p3-p4-doc-consolidation/inventory.json`.
