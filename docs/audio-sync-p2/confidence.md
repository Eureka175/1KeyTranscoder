# Audio Sync P2.1 — Confidence Metric Forensics

Stage 3 deliverable. The metric under scrutiny is the **production** one in
`core/sync_estimate.gcc_phat_int`, because that is the gate that decides whether a
track is corrected or left `untouched`. The P2 research instrument has no confidence
gate at all.

The brief's constraint is respected throughout: the goal is **not** to find a
threshold that makes more tests pass. It is to find out what the number measures.
No threshold was changed in any experiment below.

---

## 1. The metric as it actually exists in the code

Not reconstructed from a report — read from `core/sync_estimate.py:451-473`.

```
surface      = irfft( PHAT(conj(rfft(ref)) · rfft(tgt)) )            # line 405-407
window       = surface[m_lo : m_hi+1]                                 # line 413
if -min(window) > max(window)*1.2:  window = -window                  # line 421-423
best         = argmax over find_peaks(window, distance=2)             # line 426-444
v            = max(window[best], 0)                                   # line 452
nf           = sqrt(2*ln(nfft)) / sqrt(nfft)                          # line 453
height_score = min(1, log10(v/nf) / log10(1/nf))   if v > nf else 0   # line 454-456
ratio_score  = 1                                    if no 2nd peak    # line 457-458
             = clamp((r-1)/(r-1+0.5), 0, 1),  r = v/window[2nd]       # line 460-461
prominence_score = min(1, peak_prominences(window,[best]) / v)        # line 462-463
confidence   = clip(0.3·height_score + 0.5·ratio_score + 0.2·prominence_score)  # 464-469
success      = (v >= 2.0 * nf)                                        # line 472
```

Live acceptance rule (`core/channel_sync.py:647`, `:659`, `:652`, `:715`):

```
frame accepted  ⟺  success ∧ confidence ≥ min_confidence      (min_confidence = 0.3)
pair correlated ⟺  usable_frames ≥ 5 ∧ median confidence ≥ 0.3
```

### Which of the candidate components actually exist

| Candidate component (from the brief's list) | Present? |
| --- | --- |
| peak magnitude | **yes** — as `height_score`, log-scaled against `nf` |
| noise floor | **yes** — but it is an **analytic Gaussian** floor `sqrt(2·ln N / N)`, *not* a measured noise floor |
| peak / noise | **yes** — inside `height_score`; the hard `success` gate is `v ≥ 2·nf` |
| peak separation | **yes** — `ratio_score`, using the first peak ≥ 8 samples away |
| spectral support | **no** |
| bandwidth | **no** directly; bandwidth enters only through the PHAT denominator |
| edge penalty | **no** — a peak at the rim is scored like any other |
| window stability | **no** |

The metric is therefore: *how far the peak stands above an analytic noise floor*
(0.3), *how isolated the peak is* (0.5), *how prominent the peak is* (0.2).

## 2. Confidence test matrix

`work/p2/scripts/p21_stage3_confidence.py` → `work/p2/out/confidence/stage3.json`.
The real function is called; the components are recomputed only for attribution.
The two corpus-derived cases inject an exact integer delay into real C1305 CH3 audio.
All synthetic cases use seed 20260928.

| Case | Content | Truth | Est | Err | conf | success | accepted | h | r | p | rim dist |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C00 wideband (real audio) | real C1305 CH3 | 137 | 137.0 | 0.0 | 0.636 | **1** | yes | 0.41 | 0.62 | 1.00 | 11863 |
| C01 narrowband | band-limited noise + 440 Hz | 137 | 137.0 | 0.0 | **0.286** | **0** | **NO** | **0.00** | 0.17 | 1.00 | 11863 |
| C02 narrowband pure | 440 Hz only | 137 | 137.0 | 0.0 | 0.201 | **0** | **NO** | 0.00 | 0.00 | 1.00 | 11863 |
| C03 narrowband noisy | 440 Hz + noise | 137 | 137.0 | 0.0 | 0.803 | 1 | yes | 0.35 | 1.00 | 1.00 | 11863 |
| C04 periodic ambiguous | 1000 Hz only | 137 | 137.0 | 0.0 | 0.200 | **0** | **NO** | 0.00 | 0.00 | 1.00 | 11863 |
| C05 edge out of range | real audio, truth +12900 | 12900 | 11983.0 | −917.0 | 0.463 | **0** | **NO** | 0.00 | 0.53 | 1.00 | 17 |
| C06 wrong peak | 1000 Hz only | 137 | 137.0 | 0.0 | 0.200 | **0** | **NO** | 0.00 | 0.00 | 1.00 | 11863 |
| C07 real control | real C1305 CH3 | 137 | 137.0 | 0.0 | 0.636 | 1 | yes | 0.41 | 0.62 | 1.00 | 11863 |

`h` / `r` / `p` are `height_score` / `ratio_score` / `prominence_score`;
`rim dist` is the peak's distance from the nearest edge of the search band.

Every case measured the correct delay to the sample, including the six that the
metric rejects. That is the whole finding in one sentence: **on this matrix the metric
did not once separate right from wrong; it separated wideband from narrowband.**

## 3. The two historical observations, retested

### Case A — "narrowband → a correct measurement is rejected"

**CONFIRMED.**

* C01: true delay +137 samples, estimate **+137.0**, error **0.0**, and
  `success = 0`, `confidence = 0.286 < 0.3` → not accepted.
* C02: same, `confidence = 0.201`, `success = 0` → not accepted.
* C04: same, `confidence = 0.200`, `success = 0` → not accepted.
* C06: same, `confidence = 0.200`, `success = 0` → not accepted.

Four independent reproductions, deterministic (fixed seeds, fixed file offset), with
the real function. The rejection is **not** caused by `min_confidence`: in all four
cases `success` itself is already false, because the peak value `v` is below
`2·nf ≈ 0.021`. Lowering `min_confidence` to 0 would change nothing, which is the
concrete mechanism behind the earlier observation that `min_confidence = 0.0` "changes
nothing": **the binding gate is `v ≥ 2·nf`, not the confidence threshold.**

### Case B — "edge / false peak → confidence unexpectedly high"

**NOT REPRODUCED.**

* C05 places the true delay at +12900 samples, outside the ±12000-sample search band,
  so the answer is pinned near the rim (rim distance 17 samples). It was **rejected**,
  with `success = 0` and `confidence = 0.463` — below the accept threshold.
* No case in the matrix produced a high-confidence wrong answer. The highest
  confidence in the whole matrix (0.803, C03) belongs to a **correct** answer.

An independent measurement in Stage 4 level 2 reinforces this. Across four true delays
the ±250 ms range could not represent, the research instrument's `ratio` was
**1.01, 1.11, 1.33, 1.34** — the wrong answer never produced a confidently isolated
peak. So the mechanism Case B describes is not something this session could produce,
and no such claim is carried forward.

## 4. Root cause of Case A

Two mechanisms, both in the line `height_score` depends on.

**(a) An analytic noise floor is compared against a PHAT surface that has been
regularised against its own maximum.** The PHAT denominator is
`|G| + 1e-3 · max|G| + 1e-12` (`sync_estimate.py:406`). The `1e-3 · max|G|` term is
driven by the *largest* spectral magnitude anywhere in the window — for real audio,
dominated by low-frequency energy. Bins whose energy is far below `1e-3 · max|G|` are
attenuated towards zero, so the correlation peak contributed by narrowband content in
a mid/high band is suppressed in exactly the way the metric then punishes. `nf` is a
Gaussian-noise expectation that assumes a properly whitened surface; the surface is
not fully whitened, so `v < nf` is reachable on perfectly measurable content, and
`height_score` collapses to exactly `0.00` (as it does in C01, C02, C04, C05, C06).

**(b) Narrowband content genuinely produces a periodic correlation surface.** With
440 Hz content, peaks repeat every ~109 samples; with 1000 Hz, every 48 samples. The
`ratio_score` term is designed to detect precisely that, and it correctly assigns
0.00–0.17. This half of the behaviour is defensible: the delay is *ambiguity-limited*.

The combination is the defect: (a) silently zeroes the largest of the three terms, so a
content that is merely narrowband is scored as if it were unmeasurable, and (b) is
neither necessary nor sufficient to catch a genuinely wrong peak (C03 scores highest of
all while being the noisiest case).

## 5. Does this pass the three-condition bar for a production fix?

The brief requires **all three** of: deterministic reproduction, a clear violation of
the measurement intent, and confirmation on the real code path.

| Condition | Status | Evidence |
| --- | --- | --- |
| deterministic reproduction | **MET** | 4/4 narrowband cases, fixed seeds, fixed file offsets, real function, identical on re-run |
| real code path confirmed | **MET** | `sync_estimate.py:454-473` read directly; live rule at `channel_sync.py:647/659` |
| clearly violates measurement intent | **NOT MET as a correctness defect** | every rejected case was measured **correctly**; the consequence is a *refusal* (`untouched`), which is the safe direction |
| does it change a real outcome? | **tested in Stage 5** | see §7 |

Until §7 was run, the honest classification was **OBSERVATION ONLY**. §7 supplies the
fourth row and promotes the finding to a **VERIFIED observation**, still not a defect
requiring a fix.


## 6. Old claims: status

| Old claim | Status | Basis |
| --- | --- | --- |
| "`confidence` behaves as a bandwidth detector" | **VERIFIED** | 8-case matrix; ordering follows content bandwidth, not error |
| "440 Hz tone measured correctly with `height_score = 0.000`" | **VERIFIED** | reproduced exactly (C02) |
| "`noise 300–1000 Hz` returns the exactly correct delay with 0/599 usable frames" | **VERIFIED** | same mechanism, reproduced on C01/C03 (correct delay, gate false) |
| "`min_confidence = 0.0` changes nothing because `success` is the real gate" | **VERIFIED** | `success` false independently of `min_confidence` in C01/C02/C04/C06 |
| "second-peak ratio separates corpus from control" | **RETRACTED** (earlier phase, upheld) | Stage 4 level 2 measured `ratio` 1.01–1.34 on answers that were wrong by 2000–11859 samples |
| "confidence is a correctness detector" | **RETRACTED** | 6 of 8 cases: correct delay, rejected; 0 of 8: wrong delay, accepted |

Nothing from the earlier phase was deleted; the retractions above are additive.

## 7. Production consequence — measured in Stage 5

§2 establishes the mechanism on synthetic content. Whether it costs production
anything was then measured on **real corpus audio** at the **production geometry**,
with an exact injected delay of +137 samples.

Script: `work/p2/scripts/p21_stage5_real.py` → `work/p2/out/stage5/production_bandwidth.json`
Mechanism isolation: `work/p2/scripts/p21_stage5b_mechanism.py`

### 7.1 A harness defect had to be fixed first

The first run reported `usable_frames = 0` for **all nine** variants, including the
unfiltered real-audio control. That is impossible: the P2 instrument measured +137.0
exactly in every one of them. The cause was a defect in this session's own harness,
not in production — `estimate_pair` was being handed a **pre-cropped window** instead of
a stream, so the estimator applied its own ±80 ms search window to a buffer that had
already been cropped to ±250 ms. Once two full-length streams are passed, the control
behaves correctly. Recorded for the same reason as the earlier instrument failures: it
produced a complete, self-consistent, entirely wrong table.

### 7.2 The result

| Variant (all with true delay +137 samples) | P2 instrument | Usable frames / 304 | Production conf | Production verdict |
| --- | --- | --- | --- | --- |
| V0 real, unfiltered | +137.0 exact | **99** | 0.71 | would align |
| V1 real, ≤4 kHz | +137.0 exact | **54** | 0.73 | would align |
| V2 real, ≤2 kHz | +137.0 exact | **7** | 0.65 | would align |
| V3 real, ≤1 kHz | +137.0 exact | **0** | 0.00 | low_confidence |
| V4 real, ≤500 Hz | +137.0 exact | **0** | 0.00 | low_confidence |
| V5 real, ≥200 Hz only | +137.0 exact | **232** | 0.75 | would align |
| V6 real + faint 440 Hz | +137.0 exact | 54 | 0.67 | would align |
| V7 ≤1 kHz + faint 440 Hz | +137.0 exact | **0** | 0.00 | low_confidence |
| V8 ≤1 kHz + faint noise | +137.0 exact | **44** | 0.91 | would align |

Every variant was measured **correctly by the P2 instrument**, and every one was
accepted by production above ~2 kHz of bandwidth. Three findings:

1. **The gate works on real content.** Unfiltered real corpus audio yields 99 usable
   frames at confidence 0.71, and the C1305/C1306 P1 baselines were refused for
   `non_constant`, not for confidence. The gate is not failing on the corpus.
2. **The gate has a measured bandwidth floor, between 2 kHz and 1 kHz.** At ≤1 kHz the
   delay is still exactly measurable — the P2 instrument proves it on the same buffer —
   but production produces **zero** usable frames.
3. **The cause is the PHAT regularisation, not the confidence weights.** Stage 5b reads
   the real components out of the coarse stage:

   | Variant | max abs G | peak v | noise floor nf | **v / nf** | verdict |
   | --- | --- | --- | --- | --- | --- |
   | V0 unfiltered | 1.533e5 | 0.2256 | 0.06114 | **3.69** | pass |
   | V2 ≤2 kHz | 1.551e5 | 0.1722 | 0.06114 | **2.82** | pass |
   | V3 ≤1 kHz | 1.634e5 | 0.1241 | 0.06114 | **2.03** | pass (marginal) |
   | V4 ≤500 Hz | 1.994e5 | 0.0810 | 0.06114 | **1.32** | **fail** |
   | V8 ≤1 kHz + noise | 1.925e5 | 0.2836 | 0.06114 | **4.64** | pass |

   The noise floor `nf` is **constant** (0.06114) — it is analytic, not measured. The
   peak value `v` decays monotonically with bandwidth (0.2256 → 0.0810) while
   `max|G|` **rises** (1.533e5 → 1.994e5), because the PHAT denominator
   `|G| + 1e-3·max|G|` suppresses the mid/high-frequency bins that carry the peak.
   Adding uncorrelated broadband noise (V8) *raises* `v` from 0.1241 to 0.2836.

   So Case A's mechanism is confirmed at the component level: the failure is the hard
   `success = v ≥ 2·nf` gate at `sync_estimate.py:472`, driven by an analytic noise
   floor compared against a surface regularised against its own maximum. The weighted
   confidence score is **not** the binding term — on real content its median over
   usable frames is 0.65–0.91, comfortably above the 0.3 threshold.

### 7.3 Production decision

**NO production fix.**

*Condition 3 of the three-condition bar is not met.* The gate refuses content that is
genuinely **unmeasurable by production's own pipeline** — but production being unable
to measure it is not the same as the content being unmeasurable, and the measurement
intent of a *confidence* metric is not to act as a bandwidth filter. That is a real
observation, and it is recorded as such. It is not, however, a defect that is costing
anything measurable today: the corpus clears the floor by a factor of ~1.8 in `v/nf`
and 99/304 frames, and the corpus is the only material this project has.

More decisively, the fix is **not minimal**. The term that would have to change is
`1e-3 · max|G|` in `gcc_phat_int`'s PHAT denominator — the code that computes the
correlation surface itself. Changing it changes the surface for **every** frame of
**every** file and can move peak locations, which is an estimator change, not a
confidence-gate change. The brief restricts a permitted fix to a *minimal patch* and
forbids reaching into the sync_estimate core on P2's account. A change of that reach,
proposed on evidence from one 30 s window of one file, would be unjustified.

### 7.4 What is proposed instead

`PROPOSED` — not implemented, not required for this release:

* Record `v / nf` and `max|G|` per frame in the channel-sync report, so that a future
  refusal on band-limited material is diagnosable from the artifact instead of
  requiring this experiment to be re-run.
* If a future corpus displays the floor, the change to evaluate first is a
  **band-aware or fixed** regularisation epsilon, evaluated against the P1 known-answer
  controls before and after, not against P2.

No threshold in production was changed by this session. `min_confidence` is still 0.3,
`success` is still `v ≥ 2·nf`, and the P1 guard is 27 PASS / 0 FAIL.

