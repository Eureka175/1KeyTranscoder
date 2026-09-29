# Audio Sync P2.1 — Fine Resolution

Stage 4 deliverable. This closes the one substantive item the previous phase left
open. It was left open because the only attempt bundled two changes together (a shorter
window **and** a narrower search range) and therefore tested neither.

The item is split into two levels, and level 2 is only permitted if level 1 succeeds.

---

## 1. What the pending question actually was

> "A 120 s analysis window averages the delay over 120 s, so a finer grid of delay
> measurements may be obtainable simply by shortening the analysis window — while
> keeping the historically validated ±250 ms search range."

Stated as a controlled experiment, the changed variable is the **analysis window
length**, and everything else is held fixed. That is what level 1 does.

## 2. Level 1 — window experiment at a fixed ±250 ms search range

Script: `work/p2/scripts/p21_stage4_windows.py`
Result: `work/p2/out/fine-resolution/stage4_windows.json`

**Controlled variables:** the search range (±250 ms = ±12000 samples), the estimator
(`p2_track.gcc_phat_peaks`), the base signal (one 150 s segment of real C1305 CH3 from
t = 600 s), and the delay-profile construction.
**Changed variable:** the analysis window length — W0 = 120 s (historical), W1 = 45 s,
W2 = 15 s.

Ground truth is an **exact integer-sample delay profile** applied to the real audio, so
the truth is representable at every sample and no fractional-delay assumption enters
the comparison. The profile is flat everywhere except a ±60 s region around the window
centre, so the same displacement lies inside every window.

| Profile | Window | Truth | Estimate | Error (samples) | Error (ms) | Ratio |
| --- | --- | --- | --- | --- | --- | --- |
| constant 137 | W0 120 s | 137 | 137.0 | **0.0** | 0.000 | 21.179 |
| constant 137 | W1 45 s | 137 | 137.0 | **0.0** | 0.000 | 1.623 |
| constant 137 | W2 15 s | 137 | 137.0 | **0.0** | 0.000 | 1.925 |
| constant 1200 | W0 120 s | 1200 | 1200.0 | **0.0** | 0.000 | 21.168 |
| constant 1200 | W1 45 s | 1200 | 1200.0 | **0.0** | 0.000 | 1.623 |
| constant 1200 | W2 15 s | 1200 | 1200.0 | **0.0** | 0.000 | 1.923 |
| plateau 400 (held ±60 s) | W0 120 s | 400 | 400.0 | **0.0** | 0.000 | 21.163 |
| plateau 400 (held ±60 s) | W1 45 s | 400 | 400.0 | **0.0** | 0.000 | 1.623 |
| plateau 400 (held ±60 s) | W2 15 s | 400 | 400.0 | **0.0** | 0.000 | 1.923 |
| ramp 0 → 2400 over ±60 s | W0 120 s | 1200 | 458.0 | −742.0 | −15.458 | 1.155 |
| ramp 0 → 2400 over ±60 s | W1 45 s | 1200 | 923.0 | −277.0 | −5.771 | 1.065 |
| ramp 0 → 2400 over ±60 s | W2 15 s | 1200 | 1354.0 | **+154.0** | +3.208 | 1.720 |
| step +1200 at centre | W0 120 s | 1200 | 0.0 | −1200.0 | −25.000 | 1.345 |
| step +1200 at centre | W1 45 s | 1200 | 0.0 | −1200.0 | −25.000 | 1.107 |
| step +1200 at centre | W2 15 s | 1200 | 1200.0 | **0.0** | 0.000 | 1.043 |

### What this establishes

**(a) There is no instrument-error problem, and no bias, at any window length.**
All nine constant-delay cells are exact to **0.0 samples** at 120 s, 45 s and 15 s. The
estimator is unbiased; shortening the window does not introduce error, and lengthening
it does not average a constant away.

**(b) The real limit is window averaging, and it is severe.**
* Ramp: at a true delay of 1200 samples the 120 s window reports 458 (−742 samples,
  −15.5 ms), the 45 s window reports 923 (−277), the 15 s window reports 1354 (+154).
  The **120 s window was understating a steadily changing delay by 62 %** of the change.
* Step: with a +1200-sample step exactly at the window centre, the 120 s and 45 s
  windows both report **0** — an error of a full 25 ms, i.e. a discontinuity is reported
  as if it were not there — while the 15 s window reports +1200 exactly.

**(c) Therefore the useful time resolution of a delay trajectory is set by the window
length, not by the sample rate.** This is the correct restatement of the pending item,
and it is why the earlier phase's correction prototype failed on an unseen grid: a plan
built on a 120 s grid cannot see sub-120 s movement, and a 120 s window near a step can
report neither side of it.

**(d) The `ratio` flag is not a validity criterion here.** On the ramp the 15 s window
has the *worst* ratio (1.720 is better than 1.155, but the constant cells at 45 s and
15 s sit at 1.62/1.92 with `ratio = 21` at 120 s) — and on the step the *correct* 15 s
answer has the worst ratio of the three (1.043). Correct answers do not track `ratio`.
This is an independent, second confirmation of the earlier retraction.

## 3. Level 1b — the real corpus at the finer geometry

**Result: `SUPPORTED`.**

The sweep takes the same estimator and the same ±250 ms search range, tiles the real
corpus with **15 s windows on a 15 s step** (no overlap, no leapfrog tracking — every
window is measured independently, so a wrong window cannot propagate), and reports a
trajectory per channel pair. Full numbers and per-window rows:
`work/p2/out/fine-resolution/sweep_20260919_C1305_15s.json` and
`sweep_20260919_C1306_15s.json`; the reassessment is in
`docs/audio-sync-p2/final-decision.md` §4.

* **15 s is a valid tracker step for this corpus.** Zero rim-pinned windows across
  both files and all six channel pairs, so no window was silently truncated.
* **The level-1 result transfers to real data**: the historical 120 s geometry was
  measuring a window-averaged delay; the 15 s geometry measures the delay at 8× finer
  time resolution over the same timeline, with no change of instrument and no change of
  search range.

## 4. Level 2 — narrowing the search range (permitted only after level 1)

Script: `work/p2/scripts/p21_stage4_narrow.py`
Result: `work/p2/out/fine-resolution/stage4_narrow.json`

The search range is the second thing the earlier attempt changed, and it is the one
that invalidated that attempt. It is re-validated here from scratch against known
answers on real audio, at three ranges — one wide, one medium, one narrow — with
deliberately chosen true delays **outside** each narrow range to see whether an
out-of-range delay fails loudly or returns a plausible wrong number.

| Search range | In-range exact | False rejections in range | Out-of-range rejected | Silent wrong answers |
| --- | --- | --- | --- | --- |
| **±250 ms** | **11 / 11** | **0** | — (no out-of-range case exists) | **0** |
| ±60 ms | 7 / 9 | 2 (the two ±2880 = rim values) | 2 / 2 | 0 |
| ±25 ms | 5 / 7 | 2 (the two ±1200 = rim values) | 4 / 4 | 0 |

Detail worth keeping:

* ±250 ms is exact at **±11500 samples (±239.6 ms)**, `pinned = 0` — the rim behaves.
* Every range rejects a delay it cannot represent, so the failure mode is loud rather
  than silent, which is the property that was missing from the earlier attempt.
* But the narrower ranges produce **wrong in-range numbers**: at ±25 ms a true
  ±11500-sample delay is reported as **−359** and **−561** samples, and at ±60 ms as
  **2658** — all with the honest ratio below 1.34, meaning nothing in that instrument
  flags them.
* Narrower ranges also reject *at their own rim*: at ±25 ms, a true ±1200 (exactly the
  rim) is rejected as pinned. A range can therefore not be sized to the movement it is
  meant to measure.

## 5. Precision and sub-sample support

* **Precision achieved by this experiment:** 1 sample = **0.0208 ms** at 48 kHz, on every
  accepted window. Level 1 shows this is genuinely achievable and unbiased — nine cells
  exact to 0.0.
* **Sub-sample support: SUPPORTED, and it is not what limits this experiment.**
  `gcc_phat_peaks` is integer-precision by construction, so Stage 2's S05/S06 (±0.5
  sample) remain **NOT APPLICABLE to it** and were not forced to pass. But a fractional
  **measurement** capability does exist on the refinement path
  (`sync_estimate.phase_slope_fine`), and Stage 2.1 graded it against an independently
  constructed known answer (DFT phase-ramp resampling, not the usual time-domain sinc
  kernel): error **≤0.00005 samples (≤0.02 µs)** across six fractional cases, with integer
  inputs round-tripping exactly. `measurement-integrity.md` §6 has the table. **An
  earlier draft of this document said sub-sample support was NOT PRESENT — that was
  wrong**, and that section records how the error arose.
* **Why it does not change the result.** The corpus movement is 17–27 ms = 816–1296
  samples. The integer instrument's 1-sample floor is **0.04 %** of the smallest span
  under discussion. The binding limit here is **window averaging** (§2), three orders of
  magnitude larger, and the absent validity criterion
  (`final-decision.md` §6.1) larger still. Fractional measurement would refine a quantity
  already resolved ~1000× over.

## 6. Final status

```
Fine-resolution window experiment .......... SUPPORTED
Fine-resolution narrow-search experiment ... SUPPORTED AS A NEGATIVE RESULT
                                              (+-250 ms retained; narrower ranges
                                               produce wrong in-range numbers)
Overall fine-resolution pending item ....... CLOSED
```

The pending item is closed in the following precise sense:

* a **finer geometry is available** and validated, by shortening the analysis window
  at the unchanged ±250 ms search range;
* the finer geometry **changes no conclusion**: it makes the time-varying delay
  *better* resolved, and the time-varying delay was already established. It does not
  create evidence of clock drift, and it does not make correction viable — §2(b) shows
  that a shorter window is *more* faithful to a real step, which is exactly what makes
  the earlier correction prototype's failure attributable to geometry rather than to
  instrument error;
* **narrowing the search range is refused**, on measured grounds, not on caution.

Stop condition reached (brief §30/§63 case B is *not* triggered): the window experiment
did improve resolution, so no further algorithm variation was attempted. No new
window shape, filter, FFT size, interpolation or search range was swept looking for a
better number.
