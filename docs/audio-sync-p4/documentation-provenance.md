# Audio Sync P3–P4 — documentation provenance

**What was promoted, what was only indexed, and what deliberately stayed out of
the repository.**

This file is the record for the **documentation consolidation** phase. It is not
the provenance of the P3/P4 *work* — that is
[`p4.1-provenance.md`](p4.1-provenance.md), [`p4.2-provenance.md`](p4.2-provenance.md),
[`p4.3-provenance.md`](p4.3-provenance.md), [`p4.4-provenance.md`](p4.4-provenance.md)
and [`provenance.md`](provenance.md).

```text
base HEAD before consolidation : bb056a1250d17d2ee75633b46c5aed84514ed0a2
branch                        : research/audio-sync-p3-production-semantics
remote                        : https://github.com/Eureka175/1KeyTranscoder.git
deletions                     : 0
historical reports rewritten  : 0
production code changed       : 0
```

---

## 1. Documents created by this phase

| destination | source | content preservation |
|---|---|---|
| `docs/audio-sync-p4/production-contract.md` | authored here, from the **code** and the phase reports | new normative document; every status name and reason code read out of `core/audio_models.SYNC_STATUS_PATH`, `ELIGIBILITY_REASON_BY_STATUS` and `core/channel_sync.DEFAULTS` |
| `docs/audio-sync-p4/known-limitations.md` | authored here, from the phase reports and audit outputs | new; consolidates the non-green items that were previously only in per-phase documents |
| `docs/audio-sync-p4/documentation-provenance.md` | this file | new |

## 2. Documents modified by this phase

| destination | change | history preserved? |
|---|---|---|
| `docs/audio-sync-p4/README.md` | a **current-status block** prepended (P4.1–P4.4, what is enabled, the production-history table, and a "read next" index) | **yes** — every line of the P4-era text below it is untouched, and the two superseded lines are called out rather than edited |
| `docs/audio-sync-p4/validation.md` | a **P4.1–P4.4 roll-up** appended | **yes** — the P4 record above the append is untouched. V9's `71 PASS` result is left standing as history and its current `BLOCKED` state is recorded as a new row |
| `docs/audio-sync-p3/P3-RESEARCH-OVERVIEW.md` | three sections added: what P4 did with each P3 result; the P3.7 non-reproduction; where P3's primary sources live. Plus a marked update note under the conclusion block | **yes** — no phase verdict was altered; the one stale line is annotated, not replaced |
| `docs/audio-sync-p3/README.md` | four navigation lines added to the existing **banner** | **yes** — the frozen text below the banner is untouched |
| `README.md` (root) | one index entry for the P4.4 work | **yes** — additive |
| `docs/README.md` | index rows for the new documents | **yes** — additive |

**Nothing was rewritten, deleted, or had a verdict changed.** No `FAIL` was
removed, no `DEFERRED` became `PASS`, and no experimental number was altered.

---

## 3. What was promoted from `work/` into `docs/`

| source (`work/`, gitignored) | how its permanent content reached `docs/` |
|---|---|
| `work/p4.4/FINAL-REPORT.md` | its **permanent** content — the contract, the state table, the failure modes, the default-path measurement, the validation numbers — was folded into `production-contract.md`, `validation.md` and `known-limitations.md`. The file itself is not copied |
| `work/p4.4/repository-audit.json` | the three-path enablement table and the correction-entry-point enumeration → `production-contract.md` §1 and §3 |
| `work/p4.1/FINAL-REPORT.md`, `work/p4.1/D6-RESULT.md` | already have a tracked canonical form in `downstream-audit.md`; **not** re-promoted |
| `work/p4.2/FINAL-REPORT.md` | already canonical as `p4.2-evidence-integrity.md`; **not** re-promoted |
| `work/p4.3/decision-inventory.json` | the decision table and the decision-ID collision → `production-contract.md` §7 and `p4.3-production-semantics.md`; **not** copied |

**Rule applied:** a `work/` artifact was promoted only when its content was
**permanent and had no tracked canonical form**. Where a tracked document already
carried it, the artifact was **indexed**, not duplicated — the brief's
prohibition on two near-identical formal documents with no canonical marker.

## 4. What deliberately stayed out, and why

| artifact | size / count | why it is not in the repository |
|---|---|---|
| `work/p3/` measurement data (`experiments/`, `correction/`, `out/`, `synthetic/`, `archive/`) | 2190 files / 24 GB | regenerable measurement data. The repository carries the conclusions, the provenance hashes and the reproduction commands |
| `work/p2/` corpus and KAT media | 274 files / 104 GB | real camera media. The tracked substitutes are the frozen 137-corpus CSV (with its recorded hash) and the P1 guard |
| `work/p3/P3-FINAL-REPORT.md` + `P3.1`–`P3.8` reports | 12 files / ≈290 KB | **not promoted**: large, historical, and their content is carried by the tracked `FINAL-RESEARCH-SUMMARY.md` (P1–P3.8) and by `P3-RESEARCH-OVERVIEW.md`. Promoting twelve historical reports would create a second canonical set — the exact duplication the brief forbids. **The dependency is now stated explicitly in the P3 entry point** instead |
| `work/p4.4/clip_c1305.mp4` | 2.3 MB | media; regenerable in one `ffmpeg` call from the corpus |
| phase harnesses (`work/p4*/**/*.py`, ≈20 scripts) | small | **indexed, not promoted**: they are harnesses, not repository API. Where a document cites one, it says which `work/` path and what it produced. Promoting them would present experiment tooling as a supported interface — the brief forbids exactly that |
| phase JSON outputs (audits, performance, real-material) | small | the **numbers** are recorded in `validation.md`; the raw JSON is regenerable by the named command |
| scratch (`_smoke.py`, `_c*.txt`, `_diag*.py`, `_memprobe*.py`) | small | one-off probes and commit-message drafts; the diagnostics that produced a conclusion are quoted in the phase reports |
| everything else under `work/` | 4700 files / 214 GB total | not P3–P4 documentation |

Full per-artifact classification with an `action` for each:
`work/p3-p4-doc-consolidation/inventory.json`.

---

## 5. `.gitignore`

```text
read in full      : yes (57 lines)
rules changed     : NONE
rules added       : NONE
rules removed     : NONE
```

Verified with `git check-ignore -v`:

```text
docs/audio-sync-p3/README.md                            -> not ignored
docs/audio-sync-p4/README.md                            -> not ignored
tests/audio_sync_p4/test_production_eligibility.py      -> not ignored
work/p4.4/FINAL-REPORT.md   -> .gitignore:39:work/       ignored, as intended
work/                       -> .gitignore:39:work/       ignored, as intended
```

Two existing rules that matter here, both verified rather than assumed:

* `.gitignore:4` is `*.csv` and line 7 is `!tests/fixtures/channel_sync/*.csv` —
  the exception that keeps the **frozen P1 baseline** tracked. Confirmed with
  `git ls-files tests/fixtures/channel_sync/` (three files, tracked).
* `.gitignore:39` is `work/`, which is what keeps 214 GB out of the repository.
  **The whole directory was not added to tracking**, and no rule was weakened to
  let any part of it in.

No `.gitignore` change was needed, so none was made.

### `.gitattributes` — audited, and left unchanged

Read as part of the same audit. It contains one rule,
`*.patch text eol=lf`, written for exactly the failure mode this phase then
found elsewhere: with `core.autocrlf=true` git re-encodes text files to LF at
commit time, which silently invalidates any hash recorded against the working
tree.

The frozen channel-sync baseline has **no such rule**, and its recorded hash
(`36242616062FFEC2…`, `docs/hardware-decode/final-report.md` HD-G06) describes
the **CRLF** form while the repository stores the **LF** form. The consequence,
the two hashes, and why it was recorded rather than fixed are in
[`known-limitations.md`](known-limitations.md) §7.

**No rule was added.** Choosing between `eol=lf` and `eol=crlf` for that fixture
changes what every checkout writes, and would invalidate the recorded hash on one
platform or the other — a content decision outside a documentation task, and
exactly the kind of thing the brief says to **record and stop on** rather than
fix in passing.

---

## 6. Suggested files that were deliberately NOT created

The brief lists a suggested structure and says it is not a requirement to create
every file mechanically. Where a suggested file would have duplicated an existing
canonical document, it was not created — and that decision is recorded here
rather than left as an omission.

| suggested | decision |
|---|---|
| `docs/audio-sync-p3/README.md` as a **new** entry point | **NOT created.** The file exists and is a **frozen historical report** carrying a real `P3 NOT JUSTIFIED` verdict. Overwriting it would rewrite history. The P3 entry point already exists and the repository's own banner designates it: `P3-RESEARCH-OVERVIEW.md`. The frozen README keeps its text and gained navigation lines in its banner |
| `docs/audio-sync-p4/research-history.md` | **NOT created.** Would duplicate `P4-FINAL-REPORT.md` plus the per-phase documents. The README gained a production-history table instead — an index, not a second record |
| `docs/audio-sync-p4/evidence-integrity.md` | **NOT created.** `p4.2-evidence-integrity.md` is that document |
| `docs/audio-sync-p4/production-enablement.md` | **NOT created.** `p4.4-production-enablement.md` is that document |
| `docs/audio-sync-p4/state-machine.md` | **NOT created.** The state machine is defined **once**, in code (`core/audio_models.SYNC_STATUS_PATH`), and documented in `p4.4-production-enablement.md` §2 and `p4.3-production-semantics.md`. A third file would be a copy that can drift from the code; `production-contract.md` §2 carries the normative table and points at the code |
| `docs/audio-sync-p4/provenance.md` (new) | **NOT created.** The existing `provenance.md` is P4's and the four `p4.x-provenance.md` files cover the rest. This file covers the **consolidation**, which is a different subject |

---

## 7. Canonical map

```text
P3 entry          docs/audio-sync-p3/P3-RESEARCH-OVERVIEW.md
P3 history        docs/audio-sync-p3/README.md  (frozen, banner-navigated)
                  docs/audio-sync-p3/FINAL-RESEARCH-SUMMARY.md

P4 entry          docs/audio-sync-p4/README.md  (current-status block + P4 history)

CONTRACT          docs/audio-sync-p4/production-contract.md      <- the one place
P4 history        docs/audio-sync-p4/P4-FINAL-REPORT.md
                  docs/audio-sync-p4/README.md  (the P4-era half)
P4.1              docs/audio-sync-p4/downstream-audit.md
P4.2              docs/audio-sync-p4/p4.2-evidence-integrity.md
P4.3              docs/audio-sync-p4/p4.3-production-semantics.md
D1                docs/audio-sync-p4/d1-uncertainty-contract.md
P4.4              docs/audio-sync-p4/p4.4-production-enablement.md

VALIDATION        docs/audio-sync-p4/validation.md      (P4 record + P4.1-4.4 roll-up)
OPEN ITEMS        docs/audio-sync-p4/known-limitations.md
PROVENANCE        docs/audio-sync-p4/provenance.md + p4.1/p4.2/p4.3/p4.4-provenance.md
                  docs/audio-sync-p4/documentation-provenance.md  (this file)
```

Every entry above is a **tracked** path. No canonical document lives only inside
the gitignored `work/` tree.
