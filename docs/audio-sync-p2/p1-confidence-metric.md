# P1 Confidence Metric — finding and proposed fix

> **This is a P1 finding, not a P2 change.** It is documented because it is the
> single highest-value concrete defect found during the P2 investigation, and it
> stands on its own merits regardless of what happens to P2.
>
> **Nothing here is implemented.** `core/` is untouched.
>
> Evidence: `work/p2/agent-b-estimator-audit.md` §7 and §9.4, with reproducible
> scripts and JSON in `work/p2/`.

## 1. The defect

`core/sync_estimate.py` computes a per-frame `confidence` and rejects frames below
`min_confidence = 0.3`. Agent B's audit measured what that number actually does,
and it is **not a measurement-quality score**.

### 1.1 It is a bandwidth detector

The confidence formula (`core/sync_estimate.py:453-472`) is

```
height_score     = min(1, log10(v/nf) / log10(1/nf))      # absolute peak height
ratio_score      = (r-1)/(r-1+0.5)                        # best/second peak
prominence_score = prominence / v
confidence       = 0.3*height + 0.5*ratio + 0.2*prominence
```

with `nf = sqrt(2 ln nfft / nfft)` — a threshold that assumes unit whitened
magnitude **in every frequency bin**. That assumption holds for white noise and
fails for anything musical.

Measured consequences (Agent B, `b_e2_bandwidth.json`, `b_e9_families.json`):

* a 440 Hz tone is measured at the **correct** coarse lag with
  `height_score = 0.000`;
* `noise 300–1000 Hz` returns delay **888 — exactly the true value — with
  0 of 599 usable frames**;
* setting `min_confidence = 0.0` **does not change** the zero-usable result,
  because the frames are killed by the separate absolute gate
  `success = v >= 2.0 * nf` (`sync_estimate.py:472`).

So the metric silently encodes **a bandwidth requirement**, and content with energy
concentrated below roughly 1–2 kHz is discarded even when it is measured correctly.

### 1.2 It admits window-edge artefacts at high confidence

When narrowband frames are rejected, the frames that *do* pass can be spurious
peaks at the search-window rim. Agent B measured, on an archetype speech signal,
11 usable frames with **median error +2754 samples (57 ms)** and a spread of
**7615 samples (158 ms)**, only 9 % within one sample — while max frame confidence
reached **0.803**, comfortably above the 0.3 gate. `boundary_pileup` misses these
because they sit just inside its rim threshold.

**The failure is therefore bidirectional: correct measurements are rejected, and
incorrect ones are accepted.**

### 1.3 The sub-scores do less than the formula suggests

* `ratio_score` is **decorative in the fine stage**: the rule
  `|peak2 − peak1| >= 8` (`sync_estimate.py:445-449`) can almost never be satisfied
  within a ±8-sample search window, so `ratio = 1.000` unconditionally across all
  six content families measured. A frame with **no identifiable second peak** is
  awarded **maximum** ratio credit.
* `confidence` is **not monotone** in the direction a weight needs: a frame with
  two nearly equal peaks and a tall maximum can outscore an unambiguous one.

## 2. Why this matters beyond P2

The metric is used by the **shipped** P1 path to decide which frames are usable,
which feeds coverage, the constancy gates, and therefore the per-track decision on
every multi-mono-PCM file the tool processes.

**On a narrowband recording, P1 may report `low_confidence` for tracks it has in
fact measured correctly** — a silent loss of useful alignment, not a corruption.
And on a marginal recording it may accept rim artefacts.

Neither failure is a data-safety issue: P1's contract is to leave audio untouched
when unsure, and it does. The cost is **lost function**, plus **misleading
diagnostics**.

## 3. Proposed fix — stated as a direction, not a patch

The evidence supports three changes, in order of value:

1. **Replace the absolute peak-height gate with a relative one.**
   `success = v >= 2*nf` should become a threshold expressed relative to the
   *observed* correlation surface (e.g. peak prominence over the local surface
   level), which does not presuppose a flat spectrum. Agent B demonstrated that the
   delay is already correct at the coarse stage on the affected content, so this is
   a gate change, not an algorithmic one.
2. **Make the second-peak test relative and always defined.**
   Remove the `>= 8`-sample separation requirement in the fine stage and instead
   compute the best rival peak **relative to the search window**, so
   `ratio_score` stops defaulting to 1.0. Keep the value on rejected frames —
   the round-1 audit notes this discarded data is the most valuable field in the
   structure.
3. **Re-derive `min_confidence` after (1) and (2).** The current 0.3 is calibrated
   against a formula whose semantics change.

### 3.1 How to validate the fix

* Agent B's synthetic content families (`b_e9_families.py`) give the
  bandwidth-dependence directly: the fix must make the 440 Hz tone and the
  300–1000 Hz noise **usable** while leaving white noise usable.
* A known-answer injection test on real audio (`work/p2/scripts/e2e_real_known_delay.py`)
  must continue to recover injected delays exactly.
* The rim-artefact case must be **rejected**, not admitted — i.e. the fix must not
  simply lower thresholds.
* Both corpus files must produce **the same per-track decisions as the baseline**
  (`docs/audio-sync-p2/p1-baseline.md`), since P1 refuses everything there anyway.

### 3.2 Why this is safe to propose

It changes **which frames are considered usable**, and nothing about how a
correction is applied. `shift_stream`'s "copy sample values bit-exactly, no
filtering, no interpolation" contract is untouched. The worst case of a
mis-calibrated confidence is the current behaviour: refuse and leave audio alone.

## 4. Status and honesty notes

| item | state |
|---|---|
| defect established with measurements | **yes** — Agent B, reproducible |
| reproduced by the main agent independently | **partially** — the round-5 work reproduced the *rim-preferring* behaviour of wide searches on real audio (§ `ROUND5-FINDINGS.md`) but did not re-measure the bandwidth dependence |
| fix implemented | **no** — and it must not be, until the §19 gate is met |
| fix validated | **not attempted** |

**Do not treat this chapter as a specification to implement.** It records a measured
defect and a direction; the project's rule is that production changes follow
evidence, prototypes, and validation, none of which has happened for this fix.
