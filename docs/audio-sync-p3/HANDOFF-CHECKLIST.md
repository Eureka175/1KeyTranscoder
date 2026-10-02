# Audio Sync P3 — Handoff Checklist

**A checklist a reviewer can walk. The form of every item is: "if you are about to integrate
this, verify X."**

Sections 1 and 2 are **reproduced**, not written here: §1 is the twelve-item reviewer checklist
from `work/p3/p38/coordinator/implementation-handoff.md` §4, and §2 is that document's own
statement of what it deliberately does not specify plus the open decisions. §3 is new to P3.9.
Nothing in §1 was invented, reordered by importance, or added to.

**Read this before §1: the document the checklist comes from is a proposal, not an
instruction.** Its own first three lines:

> **This is a proposal, not an implementation.** Nothing in production was changed.
> **Do not treat this document as an instruction to integrate.**

And the standing state of the line:

```text
production = NOT READY
a plan has never been applied to real audio and re-measured = UNKNOWN
no policy chosen, no policy ranked, no policy recommended
```

---

## 1. The twelve requirements a reviewer should require (reproduced verbatim in substance)

Every line is a thing that has already gone wrong once in this repository. **None is
theoretical.**

- [ ] **1. A feature flag with default OFF, and an audit showing the OFF path is
      byte-identical.**
      *Why:* the only acceptable way to ship a correction whose policy is undecided.
      *Note for a reviewer:* the current implementation needs no flag — it lives outside
      `core/` entirely, so the default path is unchanged **because the code is unreachable**
      (`production-semantics.md` §1). If it is ever wired in, this item becomes live.

- [ ] **2. `window_samples` and `hop_samples` passed as separate parameters from named
      constants.**
      *Why:* conflating them caused an out-of-bounds read. Measured symptom: a 64-entry plan
      built for a 4-window stream; numpy returned adjacent memory rather than raising;
      a debug print showed `shift_arr[16] == 1` while the loop read `shift_arr[1] == 0`
      (`production-semantics.md` §6.1).

- [ ] **3. The stream object is passed through, never materialised.**
      *Why:* materialising silently costs `n` bytes of peak memory. Related measured defect:
      `np.asarray(src)` on a `RawStream` does not fail — it walks the sequence protocol,
      producing **correct values with no error** at **86× (10 s)** and **173× (60 s)** the cost
      of the production path (`production-semantics.md` §5.2).

- [ ] **4. The shift range is validated before any signed cast.**
      *Why:* an unsigned overflow aligned audio **5 samples the wrong way, with no error** —
      the single most serious defect of this line, because the result is indistinguishable from
      a correct one. Three two's-complement paths made the old guard impossible to trip
      (`production-semantics.md` §6.5).

- [ ] **5. A chunk inside a gap must have both endpoints, or raise.**
      *Why:* the previous behaviour invented plausible values —
      `[100, 100, 100, 100]` where the truth was `[167, 233, 300, 367]`, silently wrong with no
      error. The defect then **recurred** for the gap endpoint values after the extent fix
      (`production-semantics.md` §6.4).

- [ ] **6. A truncated record set must be declared, or raise.**
      *Why:* a truncated mask was indistinguishable from a refusal —
      `[0]` where the truth was `[400]`, a plausible number from a window the chunk cannot see
      (`production-semantics.md` §6.6). Fixed by adding `GapContext.stream_end` and making
      `stream_has_trust` **demanded rather than defaulted**.

- [ ] **7. Chunk-size invariance asserted at the byte level, at ≥3 chunk sizes, including one
      that puts a chunk boundary inside a segment.**
      *Why:* a correlation-based check would pass a 40-sample disagreement — that is a measured
      number, from a clamp that made the two paths disagree at window boundaries on a 60 s
      stream (`production-semantics.md` §5.3).

- [ ] **8. Every fixture has a zero case, and each fixture is shown to discriminate.**
      *Why:* three "different" fixtures were once bit-identical, and **298 stale results were
      silently reused**. The pair matters: **a sweep can be silently measuring nothing, and a
      cache can silently hide that it was**
      (`work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` §4.0).

- [ ] **9. The P1 guard is re-run.**
      *Why:* the constant-offset path must not regress. Recorded state: **27 PASS / 0 FAIL**
      (`work/p2/scripts/p1_regression_guard.py`).

- [ ] **10. Memory re-measured with a positive control that actually allocates.**
      *Why:* a memory check that measures nothing reported PASS **three separate times in this
      phase**. F10: every case errored, the verdict fell through to `UNKNOWN`, and the script
      **exited 0**. F11: the baseline included the check's own source array, so the full-output
      control read 0.00 MB and the PASS **could not fail**
      (`work/p3/P3.8-PRODUCTION-SEMANTICS-REPORT.md` §2).

- [ ] **11. An operational rollback tested as a path, not assumed.**
      *Why:* the identity plan is the revert; it should be exercised rather than described.

- [ ] **12. No threshold, fixture or validity criterion adjusted to make a corpus pass.**
      *Why:* the standing rule of this line. It has been enforced against the line's own
      convenience repeatedly — a flat 800 Hz bandwidth rule was replaced by a conditional one
      only because the *evidence* changed, and the reversal-rate clause was **retired** rather
      than re-tuned when C1305 exposed it, with the reason recorded in the function docstring
      as well as in prose.

---

## 2. What the handoff document deliberately does not specify

**These are preconditions, not engineering decisions.** A reviewer should confirm that each is
still open and that nothing in the proposed change silently closes it.

- [ ] **2.1 Which policy ships — still undecided.**
      `current`, `hold`, `no_correction_gap` and `explicit-state-as-output` remain **UNRANKED**.
      Choosing between uncorrected alignment and invented structure is a product trade-off, and
      this research line is **not entitled to make it**. The strongest reason no ranking is
      offered is that the instrument that would have produced one **cannot see a difference
      between the policies**: `policy_jump` / `artificial_jump` is policy-invariant at
      two-sided boundaries — **0 of 8 136 policy-pairs differ**, maximum observed **5.0
      samples**, and **0 / 0 / 0 / 0** events over 50 / 100 / 200 / 500-sample thresholds.
      In `OPEN-DECISIONS.md` this is **D1**, status `OPEN` — deliberately not taken.

- [ ] **2.2 The `window_of` convention — still unresolved** (see §3.2 below, where it is also a
      P3.9 item).

- [ ] **2.3 Whether to fix the 15 s attribution offset — still undecided** (see §3.2 below).

**And one structural fact from the handoff that must not be lost in a summary:** *the engine is
untracked.* `docs/audio-sync-p3/provenance.md` records its hash, which **proves which bytes a
result came from and does not preserve them**. If `work/` is deleted, the implementation is gone
and the evidence becomes **a tombstone**. That is the largest remaining structural weakness of
this work, and it is a repository-layout decision (`OPEN-DECISIONS.md` D7).

---

## 3. P3.9 items

### 3.1 The three open decisions must be closed **first**

If you are about to integrate this, verify that the following are closed **before** the
integration is reviewed at all. None of them is an engineering decision.

- [ ] **D1 — which safe-gap policy ships.** Status `OPEN` — deliberately not taken.
      **Owner:** whoever owns the product trade-off between uncorrected alignment and invented
      structure. **Blocks: any integration whatsoever; there is no default policy in
      production.** What research cannot say: **whether uncorrected alignment inside a gap is
      worse than invented structure. That depends on what the audio is for, and a concert
      master and a sync-reference track do not want the same answer.**
      See `docs/audio-sync-p3/OPEN-DECISIONS.md` D1.

- [ ] **D5 — what a correction is allowed to *claim*.** Status `OPEN` — this is the line's
      central question and it has no technical answer. **Owner:** product. **Blocks the shape of
      the production API.** Is the correction's purpose **alignment as a physical goal**, or
      **alignment as a claimed guarantee**? Under the first, `hold` and `current` are
      near-equivalent and the state machine is bookkeeping; under the second, the state machine
      **is** the product, `current`'s over-claim is a real defect, and `explicit_state_as_output`
      stops being a companion and becomes a requirement.
      See `OPEN-DECISIONS.md` D5.

- [ ] **D6 — whether an `UNRESOLVED_GAP` is representable downstream.** Status `OPEN` — depends
      on consumers **outside this repository**. **Owner:** whoever owns the downstream format /
      API. **Blocks `explicit_state_as_output` entirely.** If a downstream consumer cannot
      represent uncertainty — if the only thing it can accept is a number per sample — then
      `explicit_state_as_output` is **unavailable regardless of its merits**, and D1 collapses
      to a choice among the others. **Nobody has looked at the consumers.**
      See `OPEN-DECISIONS.md` D6.

**Why these three and not others.** D1 and D5 shape the API and block integration outright; D6
can silently remove one of the candidates from D1. D2, D3, D4 and D7 are recorded in
`OPEN-DECISIONS.md` and are not preconditions of a *review* — they are preconditions of a
*measurement* (D2, D3), a *claim* (D4), and a *durable implementation* (D7). A reviewer should
read all seven; only these three gate the review.

**And the reason D1 in particular cannot be worked around by shipping a default:** the standing
`DECIDED` list already says **no default** — "enable any P3 correction by default | **DECIDED —
no** | no flag exists; the module is unreachable from `1kt.py`".

### 3.2 The reserved question of `window_of`, and the 15 s attribution offset

Both were reserved by the P3.8 handoff. Neither is resolved. Verify both before relying on any
coordinate in a plan.

- [ ] **`window_of` — `KNOWN AMBIGUITY`, unresolved by decision.**
      The code implements `window_of(n) = n // hop_samples` — the **latest-starting** window
      whose start is at or before `n`. A **nearest-centre** rule is equally deterministic and
      **disagrees with it at every point tested** (t = 10, 20, 29.99, 30, 30.01, 39.99, 40,
      40.01 s). **Nothing in the repository records that the first was *chosen***; the docstring
      that might have explained it describes **a third thing the code does not do**.
      **Why it was not resolved:** the two conventions are **symmetric in error magnitude** —
      one applies a measurement 15 s early, the other the same magnitude late — so the choice is
      whether a correction should **anticipate** a change or **lag** it. And **changing it
      silently alters every historical plan, therefore every P3.3–P3.8 real-corpus number.**
      `OPEN-DECISIONS.md` D2. See `work/p3/p38/coordinator/window-center-semantics.md` §C4.

- [ ] **The 15 s attribution offset — `OBSERVED SEMANTIC MISMATCH`, accepted, not resolved.**
      A window index names a window by its **start**; its measurement is centred **15 s later**;
      the plan applies that measurement **from the block's start**. So a measurement describing
      time `T` is applied from `T − 15 s` — **10 s against the block's midpoint**. Measured
      through the real `measure_window` to **0.0000 samples** against an analytic model, and
      shown to be a property of **the grid, not the fixture**: identical for a hard step, a
      linear ramp, white content and band-limited content.

      **Verify the bound in its corrected form, because an earlier version of the P3.8 audit
      stated it wrongly.** The offset is bounded by **one hop (10 s = 480 000 samples) at the
      production ratio `win = 3·hop`, and only there.** For `win > 3·hop` **the offset exceeds
      one hop** — the independent reviewer refuted the general claim with **26 violations
      across 36 (win, hop) pairs**, and the correction is recorded inline in the audit document.

      **Why it was not fixed:** reducing it means a shorter window or a denser hop, and **both
      change the measurement's own validity population** — which was the subject of P3.2 and
      P3.5. **That is a measurement decision, not a correction decision.**
      `OPEN-DECISIONS.md` D3.

- [ ] **If either is touched, verify the consequence is stated rather than discovered.** A change
      to `window_of` changes the meaning of every historical plan and therefore invalidates every
      P3.3–P3.8 real-corpus number; a change to the window or hop changes the validity
      population. Neither may be changed inside an integration.

### 3.3 `core/` is **not** clean — the "core untouched" premise must be **re-verified**, not assumed

If you are about to integrate this, **do not** begin from the premise that `core/` is unmodified.
As of P3.9's start the working tree shows **nine tracked modifications**:

```text
1kt.py, VERSION, core/batch_hw.py, preservation/checker.py, preservation/gpac.py,
preservation/validate.py, tests/selftest/runner/engine.py,
tests/selftest/suites/core.py, tests/selftest/suites/toolchain.py
```

- [ ] **3.3.1 Verify `core/` yourself, from the tree, in this session:**

      ```powershell
      git status --porcelain core/
      ```

      It is **not empty**: it reports ` M core/batch_hw.py`. Measured for this checklist:

      ```text
      git diff --stat -- core/batch_hw.py
       core/batch_hw.py | 62 +++++++++++++++++++++++++++++++++++++++++++++++-
       1 file changed, 61 insertions(+), 1 deletion(-)
      ```

- [ ] **3.3.2 Do not attribute that change to P3, and do not touch it.**
      `core/batch_hw.py` carries a **container-preservation fix**: a Sony `nrtm` file-level
      `meta` surviving a per-track re-mux; measured **14 014 IMU samples in, 0 out before the
      fix**. It is **someone else's concurrent tracked work, not part of P3.** It must not be
      touched, stashed, reset, or cleaned by this line.

- [ ] **3.3.3 Retire the phrase "`core/` is unmodified" as an unconditional statement.**
      **That phrase — and its variants "`core/` untouched" and "`git status --porcelain core/`
      is empty" — is invalidated as an unconditional statement by the working tree above.**
      It appears in the P3 record as a standing premise: `docs/audio-sync-p3/README.md` §6
      prints `git status --porcelain core/    (empty)`, `production-semantics.md` opens with
      "`core/` is NOT modified", `P3.3-FORENSIC-REPORT.md` §14 records "core/ untouched", and
      P3.2, P3.6, P3.7 and P3.8 all rest on the same premise.
      **Those statements were true when written and are not true of the working tree now.**
      The correct form is scoped and checkable: *"`core/channel_sync.py`,
      `core/sync_estimate.py` and `core/sync_fix.py` are unmodified by this line"* — and even
      that must be re-verified rather than quoted.
      **The consequence for the P3 handoff: its "core untouched" premise must be re-verified,
      not assumed.**

- [ ] **3.3.4 Note that one of the nine modifications is a pre-existing user modification and
      must not be re-attributed.** `tests/selftest/suites/core.py` still has its **2026-09-26**
      mtime and **remains a pre-existing user modification. Do not attribute it to P3.**
      `work/p3/p38/coordinator/evidence-freeze.md` recorded its own working tree with three
      ` M` entries at mtime 2026-09-26 — `preservation/gpac.py`,
      `tests/selftest/runner/engine.py`, `tests/selftest/suites/core.py` — and stated they are
      *"never touched, stashed, reset or cleaned by this research line"*. That statement still
      holds; **the tree around it has grown.**

- [ ] **3.3.5 Re-verify the evidence hashes if any P3 number is re-quoted.**
      The engine is untracked, so `git status` is **blind** to changes in it.
      `evidence-freeze.md` exists precisely because **only the recorded hashes can show that it
      changed**, and it says outright that **any later report that quotes a result must quote
      the hash row it was produced under.** `work/p3/p38/agent_a_closure/suite.json` carries
      `engine_hashes_before` / `engine_hashes_after` and `engine_changed_during_run: false` for
      the same reason.

- [ ] **3.3.6 Verify nothing was deleted.** This line's rule is that it **moves** files to
      `work/archive/pending-delete/` and records them; it never removes them. P3.8 moved
      **334 files / 410.4 MiB** and listed each with its original path, size, SHA-256 and a
      copy-pasteable restore command in `work/archive/ARCHIVE-MANIFEST-P38.md`. A file is
      quarantined only if **no number in any report depends on it surviving.**

### 3.4 The integration point itself — verify the two properties the handoff singles out

If you are about to integrate this, verify both. They come from
`implementation-handoff.md` §1 and are the reason the change is **not a drop-in**.

- [ ] **3.4.1 `shift_stream` takes a scalar `delay_samples`.** The whole trajectory is collapsed
      to **one number** before the call. **A per-window plan changes that signature**, which is
      why this is not a drop-in replacement.

- [ ] **3.4.2 Line 843 is outside the `try:` at 844.** `int(np.rint(...))` on a `nan` raises
      `ValueError` and is **not** caught by the single-track fallback below it. Line 843's result
      is also only used for reporting (`rows[i]["shift_samples"]`), so the value the audio is
      actually corrected with comes from `e.delay_samples` inside `shift_stream`. **A per-window
      integration should keep that property: one place decides the shift, and it is inside the
      `try`.**

### 3.5 Verify the seam between the measurement grid and the apply grid

- [ ] **3.5.1 The two layers disagree on how many windows a stream needs, and the plan as
      produced cannot be handed to the applier.**

      ```text
      measure.window_starts  ->  floor((n - n_win)/hop) + 1   windows (only full windows)
      apply_shift_plan       ->  ceil(n / hop)                windows (every sample addressable)
      ```

      Measured: **C1305 478 measured vs 481 needed (seam 3)**; **C1306 414 vs 417 (seam 3)**;
      seam **0** for any stream at most one hop long, then settling at `win/hop − 1`.
      The tail must be declared `valid=False` rather than given a value. **Neither layer is
      wrong — the interface was missing.**

- [ ] **3.5.2 Treat the two recorded suite failures as the seam, not as defects, and check that
      they are still classified that way.** `work/p3/p38/agent_a_closure/suite.json` records
      both with `classification: "REJECTED"` and a note that each **is EXPECTED to fail: it is
      the measured seam**. Agent A closure suite: **262 PASS / 2 FAIL — the 2 ARE the seam.**

### 3.6 Verify the evidence you are relying on, before you rely on it

- [ ] **3.6.1 Do not quote the P3.4 `51.2 %` neighbour-range figure.** It is a **truncation
      artefact**, recorded with `do_not_quote: true`. `work/p3/p34/gap_residual.py:329` stores
      `gap_runs[:60]` **after** a descending sort on line 320, dropping **31 runs / 177 windows
      (641 persisted vs 818 aggregated)**. Summing it gives a **biased** answer, not merely an
      incomplete one. **The correct figure is P3.4's own aggregate over all runs: 59.5 %.** And
      it must be **quoted with its tolerance**: the honest band is **~30–68 %**, and a single
      "59.5 %" is **not defensible**.

- [ ] **3.6.2 Do not re-merge the split P3.4 finding.**

      ```text
      gap policy = dominant residual source        STANDS   (direct, same-axis error evidence)
      carried shift is itself the culprit          REJECTED (its own criterion does not license it)
      ```

- [ ] **3.6.3 Do not describe `current` as algorithmically wrong.** Its state machine is
      **`CORRECT`** — **132/132** state sequence, **0 fake `TRUSTED`**. What fails is its
      `resolved` flags, which over-claim on **1 of 22**. That is a **semantic over-claim, NOT an
      algorithmic error.**

- [ ] **3.6.4 Do not promote a suite result into a claim the suite cannot carry.** P3.8's own
      audit records that its earlier *"30 cases, zero silent-wrong"* did **not** cover
      integer-overflow boundaries, a defaulted `GapContext.base`, or `window < hop`:
      **a suite that passes is not a suite that is complete.** And three of P3.8's defects were
      in its **own verification code** — **a check that reports success while measuring
      nothing.**

- [ ] **3.6.5 Do not treat "it did not raise" as a finding.** P3.8's injection asserts the
      **resulting value** in every case — `VALUE_OK 13 / RAISED 17 / SILENT_WRONG 0 /
      UNEXPECTED 0`, **30 cases**. The same standard applies to any new check.

---

## 4. Standing prohibitions for whoever integrates

- [ ] Do **not** delete anything. This line **moves** files to
      `work/archive/pending-delete/` and records them; it never removes them. **Deletions: 0.**
- [ ] Do **not** modify `core/`, `production/`, `encoders/`, `1kt.py`, `tests/`, or any P3.1–P3.8
      report. P3.8's record states `core/`, `production/`, `encoders/` and `1kt.py` are
      **byte-identical to the branch base** — and §3.3 of this checklist is what you must do
      about that sentence before quoting it.
- [ ] Do **not** edit `work/p3/p32/validation_manifest.json`. Every phase states it is
      **read-only and unmodified**; P3.2 explains why it was not amended even when an input
      artifact was found to be provenance-incompatible.
- [ ] Do **not** adjust a threshold, fixture or expectation to make a corpus pass.
- [ ] Do **not** push, merge or tag as part of this work. P3.8's HEAD is in **no remote ref** and
      **no tag points at it**.
- [ ] Do **not** rank the policies, recommend enabling anything, or present a plausible number
      that no artifact supports. The line's own record of that last failure mode is
      `work/p3/P3.6-SAFE-GAP-POLICY-REPORT.md` §8.1: two window spans were given in a draft that
      **were not taken from any artifact**, and were **withdrawn**.
