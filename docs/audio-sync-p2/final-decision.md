# Audio Sync P2.1 — Final Decision

Stage 7 deliverable. This is the production decision, the evidence it rests on, and the
conditions under which it should be reopened.

---

## 1. Current corpus evidence

| | C1305 (49.82 GiB) | C1306 (43.13 GiB) |
| --- | --- | --- |
| duration | 4807.3 s | 4164.7 s |
| channel-pair span, historical 120 s geometry | 0.104 / 15.438 / 14.875 / 17.333 / 13.021 / 26.250 ms | 0.021 / 5.458 / 5.500 / 3.854 / 3.833 / 2.708 ms |
| channel-pair span, **this session's 15 s geometry** | 0.292 / **17.875** / **17.854** / **20.042** / **18.938** / **26.958 ms** | 0.146 / **19.625** / **20.062** / **17.479** / **17.479** / **22.312 ms** |
| usable windows | 320 / 320 per pair | 277 / 277 per pair |
| rim-pinned windows | **0** | **0** |

Two independent recordings, two different camera bodies' worth of material, and the
movement is present in both. Every historical span is reproduced or exceeded at the
finer geometry — nothing was lost, and C1306's movement in particular is **far larger**
than the 120 s geometry had suggested (22.312 ms span on `ch3-ch4` versus 2.708 ms).

That last point is the most important new number in this session and it needs to be
stated carefully, because it looks like a contradiction and is not one. §4 of
`docs/audio-sync-p2/fine-resolution.md` established that a constant delay measures
**identically** at 120 s, 45 s and 15 s (error 0.0 samples, six cells). So a difference
between the two geometries is **not** instrument error — it is a direct measure of how
much the delay moved *inside* the 120 s windows. On C1306 `ch3-ch4` that is 19.6 ms of
movement that the historical geometry reported as 2.7 ms. The historical figure was not
wrong; it was an average of a signal that does not have an average.

## 2. Measurement capability

| Property | Status | Where |
| --- | --- | --- |
| unit audit | **PASS** — no variable carries a unit other than its name | `work/p2/unit-audit.md` |
| sign audit | **PASS**, measured both directions | `measurement-integrity.md` §4 |
| boundary audit | **PASS** — exact at ±11998 of ±12000, `pinned = 0`; the rim itself verified at ±11500 | §5, `stage4_narrow.json` |
| refinement audit | **PASS** — the integer path has no refinement; the refinement path exists and was graded separately | §6 |
| known-answer validation | **7/7 exact** on the instrument, **11/11 exact** on the ±250 ms range, **8/8 on fractional delays to ≤0.00005 samples** | `stage2.json`, `stage4_narrow.json`, `fractional-kat.json` |
| sub-sample support | **SUPPORTED for measurement** — `phase_slope_fine` recovers fractional delays to ≤0.00005 samples (≤0.02 µs) against an independently constructed known answer; two independent implementations agree. The 1-sample floor applies to the integer path only. **No fractional CORRECTION exists** (§6.1, §10) | §6, §10 |
| real-time resolution | set by the **analysis window**, not the sample rate | `fine-resolution.md` §2 |

Instrument status: **INSTRUMENT_VALIDATED** for integer-precision, full-rate,
wide-window measurement of a delay at a stated window length.

## 3. Fine-resolution result

```
Window experiment (short window, +-250 ms fixed) .... SUPPORTED
Narrow-search experiment ........................... SUPPORTED AS A NEGATIVE RESULT
Overall pending item ............................... CLOSED
```

Shortening the analysis window from 120 s to 15 s at the unchanged ±250 ms search range
gives 8× finer time resolution with **no bias** (constant delays exact at every length)
and no rim pinning on either file. Narrowing the search range is **refused** on measured
grounds: at ±25 ms a true ±11500-sample delay is silently reported as −359 and −561
samples, and both narrow ranges false-reject at their own rims.

## 4. Trajectory result — model comparison

From the 15 s trajectories on C1305 (`work/p2/scripts/p21_stage6_models.py`; full tables
in `work/p2/out/fine-resolution/stage6_models_*.json`). Judged on residual, BIC,
complexity and increment structure — not on R². Every cell is cross-checked against the
sweep's own `summary.span_ms`, so a unit slip cannot pass silently.

| Pair | Span (ms) | × floor | constant fit rms | linear fit rms | cubic rms | piecewise-120 s rms | sign flips | monotone |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CH1↔CH2 | 0.292 | 14 | 0.037 | 0.033 | 0.030 | 0.026 | 89 | no |
| CH3↔CH4 | **26.958** | **1294** | 6.921 | 4.534 | 4.471 | **3.634** | 199 | no |
| CH1↔CH3 | **17.875** | **858** | 3.171 | 3.036 | 2.986 | **2.577** | 171 | no |
| CH1↔CH4 | **20.042** | **962** | 4.478 | 2.045 | 1.910 | **1.237** | 189 | no |
| CH2↔CH3 | **17.854** | **857** | 2.814 | 2.814 | 2.812 | **2.432** | 156 | no |
| CH2↔CH4 | **18.937** | **909** | 3.836 | 2.016 | 1.686 | **1.465** | 176 | no |

| Model | Verdict | Basis |
| --- | --- | --- |
| **constant** | **REJECTED** | residual 0.037–6.921 ms against 0.292–26.958 ms of range; the movement is 857–1294× the 0.0208 ms measurement floor, so it cannot be scatter |
| **linear** (clock drift) | **REJECTED** | see §4.1 — the rate is not one rate |
| **piecewise constant** | **BEST-SUPPORTED**, but not usable as a model class | lowest residual of the four (0.026–3.634 ms), yet 15–24 hops larger than one sample, i.e. 15–24 breaks over 4800 s — and the residual is still 65–280× the floor |
| **nonlinear / smooth (cubic)** | **REJECTED as primary** | a cubic barely improves on linear (e.g. 4.534 → 4.471 ms), so smooth curvature does not absorb the structure |

### 4.1 Does the evidence support clock drift?

**NO.**

The decisive discriminator is the **rate**, not the residual. A sample-rate mismatch
integrates into a monotone ramp: every increment has the same sign, the increments are
small and uniform, and the total is proportional to elapsed time. Measured here:

* **not monotone on any pair, in either file** — every one of the 12 channel-pair
  trajectories changes sign repeatedly (156–199 sign flips in 319 increments on C1305,
  141–204 in 276 on C1306);
* the implied linear rates are mutually inconsistent in a way a single clock cannot
  produce. C1305: −3773.70, +658.27, −2875.05, +20.87, −2355.45 ppm, predicted
  end-to-end drift **+0.100 ms to −18.057 ms**. C1306: −390.49, +951.67, +377.80,
  +999.46, +277.75 ppm, predicted drift **+0.004 ms to +4.138 ms**. Two channels of the
  *same* recorder, sampled by the *same* clock, cannot drift against each other at
  −3773 ppm and +20.87 ppm simultaneously. **A common-mode clock error cancels in every
  inter-channel measurement by construction**, so if the movement were clock drift the
  rates would have to be equal across pairs — and they differ by up to three orders of
  magnitude;
* `ch3-ch4` even **reverses** across the file (min −11.54 ms, max +15.42 ms on C1305).
  A rate that changes sign is not a rate.

The measurement is good enough to answer this: `fine-resolution.md` §2(a) and §5 show an
exact, unbiased 1-sample floor, and the observed movement is three orders of magnitude
above it. So the rejection is **not** a sensitivity problem.

### 4.2 The offset/rate distinction

Every offset in this corpus is **large** (17–27 ms against a 0.0208 ms floor) and every
rate is **non-constant**. Offsets are fixable, in principle, by a time-varying
correction. Rates are only fixable by resampling, and only if the rate is constant.
Since the rate is rejected as constant, §6.2 applies, not resampling.

### 4.3 The C1306 geometry effect

The same comparison at the 120 s geometry produced spreads of only 2.7–5.5 ms for C1306,
which is why the previous phase hedged on that file. At the 15 s geometry the same file
shows 17.5–22.3 ms. Both are correct measurements of a delay that moves *inside* the
120 s window: `fine-resolution.md` §2(a) established that a constant delay measures
identically at every window length, so a geometry difference is a measurement of
intra-window movement, not of instrument error. The historical numbers were averages of
a signal that does not have an average — which is itself the clearest possible statement
of the core finding.

## 5. Confidence result

| Question | Answer |
| --- | --- |
| Does the metric track correctness? | **NO** — in an 8-case known-truth matrix it separated wideband from narrowband, never right from wrong; all 8 measured the correct delay |
| Historical Case A (narrowband → correct measurement rejected) | **CONFIRMED**, 4/4, deterministically |
| Historical Case B (edge/false peak → unexpectedly high confidence) | **NOT REPRODUCED**; retracted from further use |
| Root cause | the hard gate `success = v ≥ 2·nf` at `sync_estimate.py:472`, with an analytic noise floor compared against a surface regularised by `1e-3·max\|G\|`; `nf` is constant at 0.06114 while `v` falls 0.2256 → 0.0810 as bandwidth narrows |
| Does it cost production anything on real material? | **NO** — unfiltered real corpus audio: 99/304 usable frames, confidence 0.71; the corpus clears the floor by ~1.8× in `v/nf` |
| Is a fix minimal? | **NO** — the term that would have to change is the PHAT denominator, which changes the correlation surface for every frame of every file |

## 6. Production decision

```
Confidence fix:            NO
Time-varying correction:   NO
Resampling:                NO
```

### 6.1 Time-varying correction — NO

All six of the brief's §42 preconditions are **not** simultaneously met:

| Precondition | Status |
| --- | --- |
| instrument validated | **MET** |
| fine resolution validated | **MET** |
| time-varying signal reliable | **MET** — 597/597 windows usable, 0 pinned, two files |
| trajectory model justified | **NOT MET** — no model class is established; the movement is discrete with many breaks and repeated sign changes, and no stability criterion survives |
| correction benefit demonstrated | **NOT MET** — the previous phase's prototype reduced the residual to exactly 0.000 ms *at its own geometry only* (a self-fulfilling measurement) and carried 0.92×–1.65× the original span on an unseen 30 s grid, with boundary jumps of +5.5 to +14.2 dB |
| P1 compatibility demonstrated | **NOT MET** — no correction path exists that does not alter sample counts, and `preservation/validate.py:291-301` requires exact `stts` equality |

A correction also needs a **validity criterion** — a rule for when its own measurement is
trustworthy — because a corrector that acts on a wrong delay is worse than no corrector.
No criterion survived validation: the second-peak ratio was falsified against a
purpose-built single-arrival control, the sub-window reproducibility criterion flagged
17 % of single-arrival windows as unstable, and `ratio` in this session scored 1.01–1.34
on answers that were wrong by 2000–11859 samples. **A corrector without a validity
criterion would silently corrupt aligned audio.** The project's fallback —
`untouched` — means the missing criterion forbids a desired action rather than licensing
a wrong one, which is why the current production state is correct.

### 6.2 Resampling — NO

The three-layer proof the brief requires fails at layer 2:

```
measured variation            -> ESTABLISHED (17-27 ms, 900-1300x the floor)
clock / sample-rate mismatch  -> NOT ESTABLISHED (rate is not constant; signs reverse)
need continuous correction    -> NOT ESTABLISHED (follows from layer 2)
```

Layer 2 fails on its own terms: a sample-rate mismatch produces a monotone ramp, and the
measured increments are not monotone. No resampling work was designed or attempted.

### 6.3 Confidence fix — NO

The gate is correct on the corpus (99/304 frames, conf 0.71) and the previous phase's P1
refusals were triggered by **spread**, not by confidence. The over-reach below ~1 kHz is
a **VERIFIED observation** and is recorded in `docs/audio-sync-p2/confidence.md` §7,
along with what should be done if a future corpus actually hits the floor. It is not
worth changing the correlation surface of every frame to fix a case the project's only
material does not exhibit.

## 7. P1 impact

**NONE.** No production file was modified in this session:

```
core/channel_sync.py   NOT MODIFIED   (mtime 2026/9/11 21:02:47)
core/sync_estimate.py  NOT MODIFIED   (mtime 2026/9/11 21:05:55)
core/sync_fix.py       NOT MODIFIED   (mtime 2026/9/11 21:04:56)
```

The 27-check P1 regression guard passes **27 PASS / 0 FAIL**, with both corpus files at
`measure_failed`, anchor CH3, reason `non_constant`, `algo_version 2.3.0-p1`, and no
track reported `fixed`. This is the same result as before this session started, so the
P1 baseline is intact and no P2 finding has moved it.

## 8. An incident that must be recorded

During this session a cleanup command was intended to remove a stray directory tree
created by a path-resolution bug in one of this session's own scripts. It ran with a
relative path and removed `work/p2/out/` — **except** the raw corpus extracts, which
survived. Everything this session derived was regenerated from its scripts and is
byte-reproducible.

**Not recoverable within this session's test budget:** the previous phase's
`work/p2/out/agent_j/` P1 baseline artifacts — the per-track `channel_sync_*.json`
reports for both files and `p1_harness_selftest.json`. Those came from a full
channel-sync run over both 40–50 GiB files, which the brief forbids re-running, so the
guard's corpus-regression section and its known-answer section will now report those
paths as missing. The **findings** they supported are not lost — they are recorded in
`docs/audio-sync-p2/p1-baseline.md`, in `work/p2/agent-j-validation.md`, and in this
session's own `work/p2/out/p21_guard_before.json`, which was written before the
incident and shows the complete 27 PASS run. Rebuilding the artifacts is listed as a
follow-up in §9, not done here.

## 9. Recommended follow-up (not executed — outside this session's budget)

1. Rebuild `work/p2/out/agent_j/` by re-running the P1 baseline driver over both files,
   so the guard's section A and B have their inputs again. This is a **long-duration**
   run (~2 × 15 min plus the known-answer harness) and was deliberately not started.
2. Persist `v/nf` and `max|G|` per frame in the channel-sync report (proposed in
   `confidence.md` §7.4) so a future band-limited refusal is diagnosable from the
   artifact.
3. Promote `work/p2/scripts/p1_regression_guard.py` into `tests/selftest/suites/` once
   the guard's inputs are restored and a release decision needs it — **not** now, and
   not as `tests/channel_sync/`.

## 10. Corrections to this session's own earlier conclusions

Two conclusions were published in this session's first pass and are now **withdrawn**.
Both were caused by the same underlying mistake: treating an artifact as final while
work on it was still in progress.

### 10.1 "Sub-sample support: NOT SUPPORTED" — WITHDRAWN

The first pass audited the *correction* path, found that `shift_stream` rounds to an
integer, and concluded that sub-sample support did not exist. That skipped
`phase_slope_fine`, a measurement function read earlier in the same session.

Graded against an independently constructed known answer (DFT phase-ramp resampling
rather than the usual time-domain sinc kernel), it recovers fractional delays to
**≤0.00005 samples (≤0.02 µs)** across six cases, 8/8 including integers. Two independent
implementations agree. **A fractional-delay measurement instrument does exist and works.**

What does not exist is a fractional **correction**, and that distinction is the one the
original sentence should have drawn. The decision is unchanged, because the movement is
816–1296 samples and one integer sample is 0.04 % of it.

### 10.2 "Agent G's prototype is INVALID INSTRUMENTATION" — WITHDRAWN

The first pass classified `tools/p2/estimate_trajectory.py` as invalid instrumentation on
the strength of a `--no-selftest` run reporting `k_eff_bins = 0` / `peak_below_noise` on
0/40 windows. **That prototype was still being written at the time.** Its report later
grew from 18 984 to 46 367 bytes and it wrote further outputs (`trajectory_*.json`,
`g_selftest.txt`) hours after that run.

Re-run this session: `--selftest` completes **exit 0, 8 of 8 sections passing** —
integer delays ±2400 exact, fractional delays to 0.00625 samples, an out-of-range delay
correctly declined rather than mis-reported, ramps recovered to 0.12 samples median
error, geometry independence across 10 s/20 s windows, and a stated cross-check against
the project's validated `p2_track.gcc_phat_peaks` agreeing to **0.000 samples**.

The prototype is therefore a genuine instrument. It is still not a source of any number
in these deliverables — those come from `fullrate_delay.py` / `p2_track.gcc_phat_peaks`,
independently validated — but the *reason* for excluding it is now "not load-bearing",
not "known broken".

### 10.3 Recorded as UNRESOLVED — the round-1 +250 ms claim

Agent G's report §4 attributes a constant ≈**+250 ms** offset to the round-1 reference
trajectories (`ch1-ch3` `−226.396` vs `+23.824`, and every pair shifted by ≈+250 ms). A
+250 ms shift is exactly the ±250 ms search radius, which is the signature of the
already-documented unnormalised-surface defect in the earlier `arrivals.py` harness.

**This session could neither reproduce nor refute it.** The script that produced the
comparison (`work/p2/scripts/g_reconcile_geometry.py`) is not on disk, and the reference
files it names (`fullrate_c1306.json`,
`fullrate_20260919_C1305_fullrate120s.json`) do not exist in the repository, so the
provenance of those `−250` medians cannot be established.

What *can* be established, and is what the P2 conclusion actually rests on:

* Agent G's own medians (`+23.824`, `+28.233`, `+29.562`…) agree with this session's
  validated instrument to **≤0.02 ms**;
* Agent G separately reports the round-1 **spans** agreeing with its own to 0.0–7.9 %,
  and those spans are the quantities reproduced independently here;
* so Agent G, the validated instrument, and the recorded round-1 spans all agree on the
  span. Only the **absolute median** of one round-1 artifact is in question, and no
  conclusion in this session uses a round-1 absolute median.

Status: **UNRESOLVED**, flagged so that it is not mistaken for settled. It must be
resolved before any round-1 absolute delay value is quoted as fact — which is a
condition on the previous phase's numbers, not on this session's.

## 11. Future re-entry criteria

P2 correction is closed. It should be reopened **only** if new evidence arrives, and
these are the specific signals that would do it:

| # | Signal | Why it would change the decision |
| --- | --- | --- |
| 1 | **A monotone delay ramp** in a new recording: every increment the same sign, and the fitted rate stable across sub-intervals | This is the missing layer-2 evidence for sample-rate mismatch, and resampling becomes discussable |
| 2 | An **independent** measurement of a sample-rate mismatch (a board's clock spec, an SRO measurement against a reference, a third-party tool) | Would corroborate layer 2 without depending on this delay instrument |
| 3 | **Reproducible systematic movement** in a balanced corpus (many files, both brands of body), the same shape within a family | Would establish a model class, which is the failing precondition in §6.1 |
| 4 | A **validity criterion** that survives a known-truth negative control | Without it no corrector can be trusted; this is the blocking gap, independent of the model |
| 5 | A **validated fractional-delay instrument** | **ALREADY SATISFIED and NOT sufficient** — one exists (`phase_slope_fine`, and independently `tools/p2/estimate_trajectory.py`) and both measure fractional delays to ~0.02 µs. This was listed as a re-entry criterion; it has been met and it did **not** change the decision, because the movement is 816–1296 samples and the integer floor is 0.04 % of it. What is missing is not precision |
| 6 | A corpus that actually hits the **≤1 kHz bandwidth floor** | Would turn `confidence.md` §7's observation into an actionable defect with real material behind it |

Reopening requires **evidence**, not a decision to reopen. The default state stays
`untouched`, which is safe by construction.
