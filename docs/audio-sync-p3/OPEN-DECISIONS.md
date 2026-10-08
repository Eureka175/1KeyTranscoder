# Audio Sync P3 — Open Decisions

> ## ⚠️ WRITTEN IN P3.9 — ONE ROW BELOW IS NOW FALSE, TEXT UNALTERED
>
> This register was written before P3.10 existed. P3.10, P3.11 and P3.12 have
> since run and are recorded here as a **dated addendum** at the end
> (`§ Addendum — what P3.10–P3.12 settled`), because this register is a live
> decision log and a reader acting on a false row is the failure mode that
> matters. **No line above the addendum is rewritten.**
>
> The one materially false row, in "Decisions that are NOT open":
>
> | row | still true? |
> |---|---|
> | *"treat the corrected output as validated on real audio — **DECIDED — no** — it has never been applied to real audio"* | **the reason given is false.** P3.10 applied the plan to real audio and re-measured on both corpora; P3.11 did it on all six channel pairs. The **decision** (`DECIDED — no`) still stands, but for a different reason: *acceptance* is not established, not *applicability*. |
>
> D1–D7 all remain `OPEN`. None was closed by P3.10–P3.12; two were reinforced
> with measured numbers. D4's own "what is known" figures reproduce exactly in
> the P3.10–P3.12 runs.
>
> **For current status read [`P3-RESEARCH-OVERVIEW.md`](P3-RESEARCH-OVERVIEW.md).**

**This file is a register, not an argument.** It lists every decision the P3 research line
has surfaced and deliberately did **not** take, what each decision actually controls, what
the evidence can and cannot say about it, and who has to decide.

Nothing here is ranked, scored, or recommended. If a sentence below helps a reader pick an
option, it is a defect in this file.

---

## D1 — Which safe-gap policy ships

| | |
|---|---|
| **Status** | `OPEN` — deliberately not taken |
| **Owner** | whoever owns the product trade-off between uncorrected alignment and invented structure |
| **Blocks** | any integration whatsoever; there is no default policy in production |

**The candidates** (from `work/p3/p38/semantics/gap_semantics.py`):

| policy | what it does inside a gap |
|---|---|
| `current` | keeps the measurement-derived shift; its `resolved` flags claim the gap windows are measured |
| `hold` | carries the last trusted shift, and states `UNRESOLVED_GAP` |
| `no_correction_gap` | refuses to correct inside the gap |
| `explicit_state_as_output` | a **companion** to the above, not a rival: it is what to emit when a correction cannot be justified |
| `linear` / `next` | research-only, declared `OFFLINE_ONLY` in the engine |

**What research established:**

- `hold` and `current` produce **identical shifts** on real corpora except at leading gaps —
  2 windows at 10 s hop, 8 at 2.5 s hop. They differ in what they *claim*, not in what they
  *do*. Ranking them against each other would compare an epistemic statement with a
  physical one.
- `current`'s state machine is **correct** (132/132, 0 fake `TRUSTED`); only its `resolved`
  flags over-claim, on 1 of 22. This is a **semantic over-claim, not an algorithmic error**.
- The metric that would have ranked the policies — `policy_jump` / `artificial_jump` — is
  **policy-invariant** at two-sided boundaries: 8 136 policy-pairs, **0 disagreements**.
  The strongest reason no ranking is offered is that the ranking instrument cannot see a
  difference.
- At a real change, the estimator **refuses** the straddling windows, so the plan carries
  the pre-change shift through them and switches at the next window. **No candidate policy
  changes that.** The step magnitude is not a policy question.

**What research cannot say:** whether uncorrected alignment inside a gap is worse than
invented structure. That depends on what the audio is for, and a concert master and a
sync-reference track do not want the same answer.

**See:** `docs/audio-sync-p3/safe-gap-design.md`, `docs/audio-sync-p3/safe-gap-policy.md`,
`work/p3/P3.8-PRODUCTION-SEMANTICS-REPORT.md` §1.6, `work/p3/p39/prototype-design-options.md`.

---

## D2 — The `window_of` convention

| | |
|---|---|
| **Status** | `OPEN` — `KNOWN AMBIGUITY`, recorded in P3.8 and deliberately unresolved |
| **Owner** | product / engineering jointly; it is a timing-semantics decision |
| **Blocks** | the meaning of every historical plan, and the validation design's interpretability |

The code implements `window_of(n) = n // hop_samples` — the **latest-starting** window whose
start is at or before `n`. A **nearest-centre** rule is equally deterministic and disagrees
with it at every point tested (t = 10, 20, 29.99, 30, 30.01, 39.99, 40, 40.01 s). Nothing in
the repository records that the first was *chosen*; the docstring that might have explained
it describes a third thing the code does not do.

**Why it was not resolved:** the two conventions are **symmetric in error magnitude** — one
applies a measurement 15 s early, the other the same magnitude late — so the choice is
whether a correction should **anticipate** a change or **lag** it. And changing it silently
alters **every historical plan**, therefore every P3.3–P3.8 real-corpus number.

**What research cannot say:** which direction of error is preferable, because that is the
same product question as D1 wearing different clothes.

**See:** `work/p3/p38/coordinator/window-center-semantics.md` §C4,
`work/p3/p39/window-coordinate-contract.md`.

---

## D3 — The 15 s attribution offset

| | |
|---|---|
| **Status** | `OPEN` — recorded as an `OBSERVED SEMANTIC MISMATCH`, **not fixed** |
| **Owner** | product, with a measurement-cost input from engineering |
| **Blocks** | nothing mechanically; it bounds the accuracy of any claim the correction makes |

A window index names a window by its start; its measurement is centred 15 s later; the plan
applies that measurement from the block's start. So a measurement describing time `T` is
applied from `T − 15 s` — 10 s against the block's midpoint. Measured through the real
`measure_window` to 0.0000 samples against an analytic model, and shown to be a property of
the **grid**, not the fixture: identical for a hard step, a linear ramp, white content and
band-limited content.

**Bound:** one hop, `10 s`, **at the production ratio `win = 3·hop` and only there**. For
`win > 3·hop` the offset exceeds one hop. (An earlier version of the P3.8 audit stated this
as a general bound; the independent reviewer refuted it with 26 violations across 36
(win, hop) pairs. The correction is recorded inline in the audit document.)

**Why it was not fixed:** reducing it means a shorter window or a denser hop, and both
change the measurement's own validity population — which was the subject of P3.2 and P3.5.
That is a measurement decision, not a correction decision.

---

## D4 — Is the correction worth its end-to-end cost?

| | |
|---|---|
| **Status** | `OPEN` — the cost is not measured |
| **Owner** | product |
| **Blocks** | any claim that this is deployable, interactive, or batch-feasible |

**What is known:** measurement dominates. The recorded runs took **3988 s for 478 windows
(C1305)** and **1596 s for 414 windows (C1306)** on raw float streams — roughly **3–8 s per
30 s window**. That is a research figure on uncompressed input, not a production figure.

**What is `UNKNOWN`:** decode + measure + correct + encode, end to end. It has never been
measured through the production pipeline.

**A related `UNKNOWN`:** the estimator's refusal rate. C1305 ch3→ch4 is **51.7 %** valid
windows; other pairs are 19–21 %. Those refusals are the estimator working correctly, but
the coverage they leave is a product question that no phase has answered.

---

## D5 — What a correction is allowed to *claim*

| | |
|---|---|
| **Status** | `OPEN` — this is the phase's central question and it has no technical answer |
| **Owner** | product |
| **Blocks** | the shape of the production API |

Is the correction's purpose **alignment as a physical goal**, or **alignment as a claimed
guarantee**? The candidates answer differently, and the difference is not measurable from
inside the code:

- under a physical goal, `hold` and `current` are near-equivalent (they produce the same
  shifts) and the state machine is bookkeeping;
- under a claimed guarantee, the state machine **is** the product, `current`'s over-claim is
  a real defect, and `explicit_state_as_output` stops being a companion and becomes a
  requirement.

**What research established, and it is the sharpest result of the line:** the real delay
inside a gap is **not observable**, not merely imprecise. Raising the measurement precision
does nothing — `σ_meas` from 0 to 2.0 moves the answer almost not at all. There is no
estimator, no window length, and no policy that recovers it. So the honest production object
must be able to say **"not measured"**, and whether that is acceptable is a product
decision, not an engineering one.

---

## D6 — Whether an `UNRESOLVED_GAP` is representable downstream

| | |
|---|---|
| **Status** | `OPEN` — depends on consumers outside this repository |
| **Owner** | whoever owns the downstream format / API |
| **Blocks** | `explicit_state_as_output` entirely |

If a downstream consumer cannot represent uncertainty — if the only thing it can accept is a
number per sample — then `explicit_state_as_output` is **unavailable regardless of its
merits**, and D1 collapses to a choice among the others. Nobody has looked at the consumers.

---

## D7 — Whether the implementation gets a durable home

| | |
|---|---|
| **Status** | `OPEN` — a repository-layout decision |
| **Owner** | repository owner |
| **Blocks** | the reproducibility of every P3 result, eventually |

The entire P3.8 implementation lives under `work/`, which is **gitignored**.
`docs/audio-sync-p3/provenance.md` records the SHA-256 of every participating file, which
proves which bytes a result came from and does **not** preserve them. If `work/` is deleted,
the implementation is gone and the hashes are a tombstone, not a backup.

During P3.8's own audit the engine was rewritten at least four times, once into a state that
failed `ast.parse`. That is why provenance was pinned at all — and it is also why a hash of
a file nobody backs up is a weak guarantee.

---

## Decisions that are NOT open

Recorded so the register is not misread as "everything is undecided":

| decision | status | basis |
|---|---|---|
| fold the P3 time-varying correction into the P1 constant path | **DECIDED — no** | P3.2 correction `NOT VALIDATED`; P1 stays as shipped |
| enable any P3 correction by default | **DECIDED — no** | no flag exists; the module is unreachable from `1kt.py` |
| fractional (sub-sample) correction | **DECIDED — not needed** | P3.6 |
| resampling-based correction | **DECIDED — not justified** | P3.6 |
| treat the corrected output as validated on real audio | **DECIDED — no** | it has never been applied to real audio |
| re-open the P3.4 "gap policy is the dominant residual source" finding | **DECIDED — no, it stands** | direct same-axis evidence; only its *supporting inference* was rejected |

---

## Addendum — what P3.10–P3.12 settled

**Appended during the P3 documentation cleanup. Everything above this line is
frozen P3.9 text and is unchanged.** This section exists because a decision log
that a reader acts on must not carry a false row, and because P3.10–P3.12 added
two decisions the register did not contain.

### D1–D7 status after P3.10–P3.12

| id | status | what the later phases added |
|---|---|---|
| D1 gap policy | **still `OPEN`** | P3.11 measured all five arms on real PCM across six pairs and **still offers no ranking**, for the original reason: the nomination metric is policy-invariant (0 of 8 136 pairs). P3.11's addition is that `no_correction_gap` is nearly a **no-op** on low-coverage pairs, so the choice decides whether most of the stream is corrected at all. |
| D2 `window_of` | **still `OPEN`** | P3.11 measured the real-PCM effect of `latest-start` vs `nearest-centre`:**index** agreement 0.21 %, **applied-shift value** agreement 56.94 % / 36.37 %. It also established that `sample-domain` is **not a third candidate** — it is identical to `latest-start`, because overlapping 30 s/10 s windows make the latest covering index `pos // hop`. Verified in `tests/audio_sync_p311/test_coordinates.py` (71 PASS / 0 FAIL). |
| D3 15 s offset | **still `OPEN`** | Unchanged, still `OBSERVED SEMANTIC MISMATCH`. Its bound (one hop at `win = 3·hop`, **and only there**) is unchanged. |
| D4 end-to-end cost | **still `OPEN`** | The register's own figures reproduce: **3988 s / 478 windows** (C1305), **1596 s / 414 windows** (C1306). What is new: the measured refusal rate across all six pairs is **19.5 %–74.4 %** valid; the **PCM apply stage in isolation** runs at **439–474 MB/s with peak RSS flat at 110 MiB**; and P3.12 measured that an adaptive measurement strategy costs **2–10×** the exhaustive pass rather than saving anything — so measurement is not merely dominant, it is not reducible by the route tested. |
| D5 what a correction may claim | **still `OPEN`** | P3.12 independently reinforced the load-bearing fact: an excursion **shorter than the measurement window** is averaged away at any refinement depth. Refinement changes *where* windows are placed, not *how long* they are, so it cannot recover in-gap truth either. |
| D6 downstream representability | **still `OPEN`** | Unchanged. Still nobody has looked at the consumers. |
| D7 a durable home for the implementation | **still `OPEN`, and now more urgent** | P3.10–P3.12 added ~3700 more files and ~25 GiB under `work/`, all gitignored. The P3 cleanup phase quarantined only *unreferenced* files into `work/archive/pending-delete/audio-sync-p3/`; it did **not** and cannot make the implementation durable. If `work/` is deleted, P3 remains unreproducible and the provenance hashes remain a tombstone. |

### D8 — Whether any P3 correction is enabled at all

| | |
|---|---|
| **Status** | `OPEN` — no flag exists; nothing was enabled |
| **Owner** | product |
| **Blocks** | every other production question, trivially |

The register's "not open" table says *enable any P3 correction by default —
`DECIDED — no`*. That remains true. **What P3.10–P3.12 changed is the reason it
is easy:** it is not that the correction is unproven end to end — it is now
`VALIDATED` end to end on six real pairs — but that coverage is **19.5–74.4 %**,
so most of the timeline would be **policy-filled while 100 % of samples are
rewritten**, and no residual acceptance threshold exists to say that is
acceptable.

That is a product judgement, not a research one, and P3 deliberately stops short
of it.

### Entry that is not open after P3.12

```text
adaptive coarse-to-fine measurement   NO ADVANTAGE DEMONSTRATED  (P3.12)
fractional / resampling correction    CLOSED, NOT JUSTIFIED      (P3.6, unchanged)
```

P3.12 is **not** recorded as a failed idea. It is recorded as: *the current
tested formulation provides no demonstrated efficiency advantage.* Its safety
half succeeded — `MISSED_TRANSITION = 0` on known ground truth — and the reason
for the efficiency failure is measured, not assumed.
