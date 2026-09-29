# P1 Baseline on the P2 Corpus

> What the **shipped, unmodified** P1 path decides on this material. This is the
> regression baseline that any future change to `core/` must preserve, and it is
> also the answer to "does P2 need to do anything here?".
>
> Source: Agent J's runs, `work/p2/out/agent_j/p1_full_20260919_C13{05,06}.json`.
> P1 was invoked read-only; **no production code was modified**.
> Inspect with `python work/p2/scripts/show_p1_baseline.py agent_j/p1_full_20260919_C1305.json`.
>
> **Status update (final session).** Agent J's original artefacts were destroyed by
> the P2.1 cleanup and have been **recovered** by two authorised full channel-sync
> runs. The measurements in §1 are unchanged and were reproduced exactly; see §4.0
> for the historical-vs-current separation and §7 for the recovery record.

## 1. Result — identical on both files

```
status       : measure_failed
detail       : all measurable targets failed:
               CH1 non_constant; CH2 non_constant; CH4 non_constant
anchor_stream: 2            (CH3)
algo_version : 2.3.0-p1
sync_mode    : transcode
```

Both C1305 and C1306 produce exactly this. The anchor is **CH3** (the first entry of
P1's candidate list `[2, 3, 0, 1]` that correlates), and every other healthy track —
CH1, CH2, CH4 — is measured against it and rejected as `non_constant`.

## 2. Why this matters

1. **P1 already leaves the audio untouched.** The per-track decisions are all
   `untouched`, and the file-level status is `measure_failed`, whose contract is
   "original audio unchanged + warning" (`core/channel_sync.py:387-398`).
   **So the correct P2 outcome — do not correct — is already what the shipped code
   does.**
2. **P1 reaches it for a weaker reason.** P1 rejects because its gates cannot
   express shape: Agent B measured that the MAD and ppm gates are effectively
   inert, `spread` does all the work, and a 0.67 ms bimodal trajectory passes as
   `constant`. P2 rejects because the delay is **not a well-defined scalar** —
   the mobile pairs carry multiple arrivals and the winner flips between windows.
   Same action, better-founded justification.
3. **It confirms the inter-round conclusion independently.** Round 1 predicted from
   the trajectory evidence that P1's `non_constant → untouched` was "right for the
   wrong reason"; the full P1 run shows exactly that, on real material, with no
   P2 involvement.

## 3. What this implies for the production gate

The task's §19 checklist requires ten conditions before `core/` may be modified.
Two of them are now directly answerable:

| condition | state | evidence |
|---|---|---|
| "real corpus proves time-varying delay exists" | **PASS** | 2.7–27.0 ms spans, 130–1294× the 0.02083 ms one-sample floor, two files |
| "P1 does not regress" | **trivially PASS** | nothing in `core/` has changed; the baseline above is the thing to preserve |
| "model selection has evidence" | **FAIL** | Agent H: best non-overfitting fit still leaves 0.88–2.74 ms RMS, 40–130× the floor; no model established |
| "estimator has a definite uncertainty" | **PENDING** | prototype exists; self-test not yet passing |
| "correction prototype passes" | **PENDING** | prototype exists; not yet reported |
| "long-material residual significantly reduced" | **N/A / FAIL** | there is nothing to correct — the only rigid pair is already aligned to 0.021–0.074 ms |

**Conclusion:** the gate is not met, and on this evidence it *should* not be met.
The honest deliverable is the diagnosis, and the honest production change is
**none**.

## 4. How to re-run this baseline

```
python work/p2/scripts/p1_regression_guard.py --json work/p2/out/p1_guard_recovery.json
```

Exit code 0 = all checks pass.

### 4.0 Historical result vs current result — two separate numbers

| | result | what it is |
|---|---|---|
| **HISTORICAL OBSERVED RESULT** | `27 PASS / 0 FAIL` | Agent J's run, observed in full, before the artefact loss. **Not reproducible as an artefact** — the record was destroyed. Recorded here as history, and deliberately **not** carried forward as the current status. |
| **CURRENT RESULT** | `27 PASS / 0 FAIL`, exit code 0 | re-run this session against the recovered baseline artefacts (`work/p2/out/p1_guard_recovery.json`). |

The two agree, but they are **independent** results and must not be collapsed into
one number. If a future run differs, the difference is the finding — the guard's
thresholds and expectations were **not** adjusted to reproduce the historical figure.

The log of the current run is `work/p2/out/p1_guard_recovery.log`.

It checks both halves:

**A. Corpus regression** — per-track decisions from the shipped P1 on both real
files: `status == measure_failed`, `anchor_stream == 2`, `algo_version == 2.3.0-p1`,
4 per-track rows, every non-anchor track `untouched`, refusal reason
`non_constant`, and **no track reported `fixed`**.

**B. Known-answer positive controls** (from `agent_j/p1_harness_selftest.json`),
proving the baseline is not vacuous:

| control | result |
|---|---|
| constant offset +1200 samples injected | `status = applied`, `fixed_shifts = {1: 1200}` |
| two-sided offsets | `fixed_shifts = {1: 1200, 3: −600}` — both directions |
| already-aligned input | `status = already_aligned`, **nothing shifted** |
| transparent mode, aligned input | **`SHA256(in) == SHA256(out)`** (`8e587168b804…`), output is a file copy |
| silent track | stays `untouched` |
| harness verdict | `pass` |

### 4.1 Why the guard is not in `tests/` — decided, not deferred

The objective named `tests/channel_sync/` as the regression home. **Adding a file
under `tests/` is a production change**, and that directory does not exist and must
not be created: this repository is not pytest-based.

This question has now been **decided** rather than left pending. The answer is
**do not promote**, and the full argument is in
[`p1-guard-promotion.md`](p1-guard-promotion.md). The short form:

* the framework's only vocabulary is `PASS`/`FAIL` (`tests/selftest/paths.py:60`), so
  an **absent corpus fixture would render as a functional failure** — re-creating,
  inside the project's own green/red signal, exactly the ambiguity that made this
  session necessary;
* the corpus half needs the private 50 GB material plus git-excluded artefacts, and
  costs 739 s of measured wall time against a `--level unit` budget documented as
  "seconds";
* section B's controls are **already covered** inside the suite on committed
  fixtures (`C15`–`C29` in `tests/selftest/suites/hardware.py`), so the only
  non-redundant part is the part that carries the corpus dependency.

The earlier justification — *"do not promote until the §19 gate is met"* — is
**withdrawn**: that gate existed to authorise a P2 **production** change, and P2 is
closed with no production change, so it is moot rather than unmet. The decision above
survives on operational grounds, which is a stronger reason.

The canonical invocation is therefore the documented on-demand command in §4, and
`p1-guard-promotion.md` §8 lists the exact conditions that would reverse this.

## 5. How the baseline was produced

Agent J ran the shipped P1 with its own known-answer harness
(`work/p2/out/agent_j/p1_harness_selftest.json`, verdict `pass`) over a real C1305
clip (600 s from t = 1800 s) and over both full files. Artefacts retained:

```
work/p2/out/agent_j/p1_full_20260919_C13{05,06}.json          summaries
work/p2/out/agent_j/p1_full_20260919_C13{05,06}_work/         per-track reports
work/p2/out/agent_j/p1_harness_selftest.json                  the KAT record
work/p2/out/agent_j/p1_round1_vs_round2_diff.json             0 field-level differences
work/p2/out/agent_j/p1_kat_src.mov (+ aligned/constant/two-sided outputs)
```

## 6. The one thing to watch

`anchor_stream = 2` (CH3). Round 1 established that **CH3 is itself not rigid** —
`CH3↔CH4` is the most mobile pair in both files. So P1 on this material measures
every track against a **moving** reference, which means:

* the reported per-track delays are relative to something that is not stable;
* a hypothetical P1 that *did* accept these tracks would propagate CH3's own
  movement into every other track with the same sign.

This is Agent B's "star topology cannot attribute motion" finding, confirmed here on
real data. It is an argument for treating the anchor choice as part of any future
P2 work, and it is **not** a defect that P2 needs to fix — since P1 refuses
everything here, the effect never reaches the audio.

## 7. Artefact loss and recovery

The P1 baseline artefacts were destroyed by an accidental relative-path cleanup
during the P2.1 session, and the guard ran with three missing inputs until this
session. The record is kept deliberately, in full.

### 7.1 The loss

```text
event:        work/p2/out/ removed by a P2.1 cleanup using a relative path
survivor:     work/p2/out/raw/  (8 x mono f32 corpus extracts) — intact
destroyed:    work/p2/out/agent_j/  — both per-track channel_sync_*.json reports,
              both summary JSONs, and p1_harness_selftest.json
classification: TEST ARTIFACT LOSS
functional regression: NOT ESTABLISHED — no production code was involved, ever
guard state while missing: 0 PASS / 3 FAIL, all three = ARTIFACT_MISSING,
              and 0 checks failing on their merits
```

**The loss is not reinterpreted as a pass.** The three failures were missing files,
not failed assertions, and the historical `27 PASS / 0 FAIL` was never presented as
the current state.

### 7.2 The recovery

Two authorised full channel-sync runs, one per existing baseline corpus, plus a
rebuild of the known-answer control record. Nothing else was run.

| run | source | wall time | status | anchor | detail |
|---|---|---|---|---|---|
| 1 | `20260919_C1305.MP4` (49.82 GiB) | 402.5 s | `measure_failed` | 2 (CH3) | `all measurable targets failed: CH1 non_constant; CH2 non_constant; CH4 non_constant` |
| 2 | `20260919_C1306.MP4` (43.13 GiB) | 336 s | `measure_failed` | 2 (CH3) | same |

The recovered decisions are **identical to the historical record in §1**, and the
KAT rebuild reproduced the historical transparent-mode file hash **byte-exactly**
(`SHA256 = 8e587168b804bffb79cc7963e3c3e6493c74a0796c124cc2968ce9d665dd4787`,
matching the `8e587168b804…` recorded before the loss). That hash match is the
strongest available statement about the recovery: it is a reconstruction to the byte,
not a plausible-looking re-run.

### 7.3 Artefact status

| artefact | status |
|---|---|
| `agent_j/p1_full_20260919_C13{05,06}_work/channel_sync_*.json` | **RESTORED** (re-derived) |
| `agent_j/p1_full_20260919_C13{05,06}.json` | **RESTORED** (re-derived) |
| `agent_j/p1_harness_selftest.json` | **RESTORED** (re-derived, `_verdict: pass`) |
| original bytes of the lost files | **UNRECOVERABLE** — Recycle Bin empty; shadow copies need elevation |

Records: `work/p2/p1-artifact-recovery.md`, `work/p2/p1-recovery-run-{1,2}.json`,
`work/p2/p1-artifact-manifest.json` (verdict `COMPLETE`, 5 entries, 0 problems).

## 8. The §19 gate — corrected status

The old §3 table blocked promotion behind the §19 condition checklist. After
closure, four of those conditions are gates on **shipping a P2 correction** and are
therefore `N/A — not exercised, by decision` rather than outstanding:

| condition | status now |
|---|---|
| 1 real corpus proves time-varying delay exists | PASS |
| 2 calibrated estimator | N/A — no P2 estimator artefact exists or will |
| 3 model selection has evidence | N/A — gates a correction that will not ship |
| 4 correction prototype passes whole-timeline verification | N/A — same |
| 5 long-duration residual significantly reduced | N/A — nothing is being corrected |
| 6 verification independent of the plan's geometry | N/A — same |
| 7 **no P1 regression** | **PASS — and continuously checkable again via the restored guard** |
| 8 fallback honoured | PASS for P1 |
| 9 determinism, O(window) memory, counts preserved | PASS |
| 10 integration constraints | N/A — no P2 code to integrate |

`validation.md` §5 carries the full evidence table. Condition 7 is the only one still
doing live work, which is why the guard is the durable part of this chapter.

