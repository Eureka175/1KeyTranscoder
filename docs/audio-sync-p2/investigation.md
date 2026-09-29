# Audio Sync P2 — Investigation

> How the project established what the time-varying delay in the real Sony corpus
> actually is, what was rejected, and why no correction is applied.
>
> **Status: research complete, no production change.** Branch
> `feature/audio-sync-p2-drift`, production code untouched. Nothing in this
> document is implemented in `core/`.
>
> This chapter follows the required shape: observed behaviour → corpus → evidence →
> measurement uncertainty → rejected hypotheses → confirmed model.

---

## 1. Observed behaviour

P1 (shipped, `algo 2.3.0-p1`) handles constant or near-constant channel offsets: it
measures a fixed delay, applies a single integer sample shift, and rechecks the
result. On long Sony material it reports `non_constant` for most channel pairs and
leaves those tracks `untouched`.

Before P2, that outcome was an open question: is the material genuinely
time-varying, is P1 failing to measure it, or is "non_constant" the right answer for
a reason P1 cannot express?

The starting observation, from earlier audio-only work on two one-hour recordings,
was a **group-to-group relative delay** that appeared to move by 10–20 ms over the
session, with `CH1`/`CH2` internally consistent and `CH3`/`CH4` internally
consistent. Linear fits on that movement produced large ppm figures, which were
initially read as possible clock drift.

## 2. Corpus

| | C1305 | C1306 |
|---|---|---|
| file | `20260919_C1305.MP4` | `20260919_C1306.MP4` |
| size | 49.82 GiB | 43.13 GiB |
| duration | 4807.3025 s | 4164.6605 s |
| video | HEVC `hvc1`, 288,150 frames, tb 1/60000 | same |
| audio | 4 × `pcm_s24be` mono 48 kHz, sample entry `ipcm` | same |
| samples/track | 230,750,520 | 199,903,704 |
| extra | `rtmd` data stream | same |

Stream indices: 0 video, 1–4 audio (CH1–CH4), 5 `rtmd`.

Both files are **consecutive takes from the same camera with the same
microphones**. Their audio tracks have **identical sample counts** and share a
single 48 kHz timebase.

Handling: hard-linked into `work/p2/corpus/` (never copied); decoded once, all four
streams in a single ffmpeg invocation, to sample-aligned raw `f32`
(`scripts/extract_corpus.py`, ~14 s per file). `pcm_s24be` → `float32` is lossless.
Read back through `core.sync_estimate.RawStream` (bounded window, O(window) memory).

### 2.1 Two facts that constrain every hypothesis

1. **All four tracks share one clock domain.** Independent crystals are not
   representable, and neither is frame-rate reconciliation: PCM audio is
   sample-timed, not frame-timed. A `LongGOP`-related mechanism is therefore
   excluded by construction, not by measurement.
2. **CH4 is 11 dB quieter** than CH1/CH2 (−29 dBFS vs −16 dBFS) and CH3 sits
   between them, which is a clue to microphone placement (§6).

## 3. Evidence

All measurements below use full-rate, ±250 ms, overlap-normalised GCC-PHAT
(`scripts/fullrate_delay.py`), an estimator that **proves itself by injecting known
delays into real corpus audio and recovering them exactly** (±1855, −1750, +2400,
+1200, +900, 0 samples → error **0.0**, and `pinned = 0` for every row).

### 3.1 The time-varying delay is real and large

Canonical spans over the whole recording:

| pair | C1305 span | C1306 span | ratio |
|---|---|---|---|
| **CH1↔CH2** | **0.104 ms** | **0.021 ms** | 5.0× |
| CH1↔CH3 | 15.438 ms | 5.458 ms | 2.8× |
| CH2↔CH3 | 14.875 ms | 5.500 ms | 2.7× |
| CH1↔CH4 | 17.333 ms | 3.854 ms | 4.5× |
| CH2↔CH4 | 13.021 ms | 3.833 ms | 3.4× |
| **CH3↔CH4** | **26.250 ms** | **2.708 ms** | **9.7×** |

**The measurement floor is 0.024 ms RMS** (established on the rigid control pair
`CH1↔CH2`, and by a known-answer synthetic test recovering a fractional delay of
23.37 samples to 0.0008 samples). The movement on the mobile pairs is therefore
**110× to 1090× the floor** — it is not measurement noise.

### 3.2 The movement is a staircase, not a drift, and the headline number decomposes

The single most important correction to the headline figure (Agent H,
`agent-h-model.md` §6.4). C1305 `CH3↔CH4` is **not one movement**:

```
full-file span                              26.250 ms   (714 .. -546 samples)
after removing the last 8 points            14.354 ms   (714 ..   25 samples)
head span, ignoring a dip to +25            10.458 ms   (714 ..  212 samples)
span of the 8 tail points alone             23.292 ms   (572 .. -546 samples)
```

* The first **3660 s** hold a corroborated staircase — six transitions
  (714→609→531→456→292→282→262→212) followed by a **2640 s plateau** at 212–213
  samples — with **every hop confirmed by an independent estimate of the same
  pair**.
* The last **940 s** is a separate regime, swinging between +12 and −11.4 ms on a
  ~20 s cadence. **It supplies the four largest hops and 80 % of the total
  variation**, and three-point closure there fails by up to **23.042 ms** against a
  0.2 ms floor.

**So the movement that could justify a correction is the 10.46 ms staircase in the
first 3660 s — not the published 26.25 ms.**

### 3.3 The delay is frequency-flat

Measured independently in five bands spanning 100–8000 Hz at seven probe times
(`scripts/band_test.py`, overlap-normalised): the pairs agree to within ~0.1 ms
(`CH1↔CH2`), ~0.6 ms (`CH3↔CH4`) and ~1 ms (`CH1↔CH3`).

This is the signature of a **genuine time offset**, and it rules out dispersion or
multipath-within-a-window as the explanation for the movement.

### 3.4 The movement is event-driven

On the fine trajectory (60 s windows / 20 s hops, 238 points):

| pair | quiet hops (<2× floor) | episodes (>20× floor) | movement carried by quiet hops |
|---|---|---|---|
| CH1↔CH2 | 233/237 (98.3 %) | **0** | 1.27 ms |
| CH1↔CH3 | 191/237 (80.6 %) | 15 | 1.59 ms |
| CH3↔CH4 | 197/237 (83.1 %) | 15 | **1.17 ms against a 27.0 ms span** |

**~96 % of the `CH3↔CH4` movement is carried by ~15 discrete episodes**, and the
largest single-hop changes are 700–900× the floor. A continuous drift would
distribute its change across every interval.

### 3.5 The movement is partly level-dependent, but not explained by it

The measured delay correlates **−0.82…−0.94 with programme level** on every track —
a real content-dependent measurement bias. Regressing it out:

| pair | span | level R² | residual span after removing level |
|---|---|---|---|
| CH1↔CH2 | 0.096 ms | **0.841** | 0.054 ms |
| CH1↔CH3 | 7.661 ms | 0.334 | 8.391 ms |
| CH3↔CH4 | 26.197 ms | 0.448 | **25.087 ms** |

The bias explains **84 % of the tiny `CH1↔CH2` variation**, but leaves **25.1 of
26.2 ms** of `CH3↔CH4`. **The bias is real; the movement is not explained by it.**

### 3.6 The two files differ by 10×, and the only variable is content

Same camera, same microphones, same configuration, same clock. Every moving pair
moves **2.7–9.7× more in C1305 than in C1306**, while the rigid pair is essentially
unchanged (0.104 vs 0.021 ms).

**This is the strongest single piece of evidence against an equipment or clock
explanation and for a content/acoustics explanation.**

## 4. Measurement uncertainty

| contribution | magnitude | how established |
|---|---|---|
| known-answer synthetic | **0.017 µs** | 23.37-sample fractional delay → 0.0008 sample error |
| real-material floor (`CH1↔CH2`) | **0.024 ms RMS**, 0.021–0.133 ms span | rigid control pair, both files, three geometries |
| known-delay injection on real audio | **0.0 samples** | ±1855 / −1750 / +2400 / ±1200 / +900 / 0 |
| level/programme-dependent bias | explains ≤ 46 % of moving-pair variance | regression over 40 windows |
| search-window truncation | **fails completely beyond the window** | delays > ±1200 samples not recovered |
| peak-position uncertainty (broad peak) | peak/second ratio 1.0–1.4 on moving pairs vs **48.9** on the rigid pair | top-peak inspection |

### 4.1 Eight failed instruments, and why they matter

This project's central methodological result is that **most of its measurement
mistakes produced plausible numbers**. Every one of the following was built, used to
produce a conclusion, and then found wrong:

| instrument / interpretation | failure |
|---|---|
| `subsample_delay.py` v1 | omitted a unit conversion → every delay quantised to 8 samples; output complete, self-consistent, wrong |
| `band_test.py` v1 | correlation surface not normalised by overlap → answer scaled with the search-window width |
| `two_arrivals.py` | returned +99.92 ms for a known −0.08 ms control |
| `two_arrivals_v2.py` | returned +50.2 ms for a known +12.345 ms input; its own self-test caught it |
| `wide_delay.py` | buffer-length bookkeeping; the self-test caught it every time |
| second-peak ratio | looked perfectly separated on the corpus; **validly falsified** against a purpose-built single-arrival control |
| sub-window reproducibility | separated 1- vs 2-arrival on the control but flagged 17 % of single-arrival windows as unstable |
| cross-group fixture comparison | compared pairs that **share no source signal** — a methodologically wrong test, not a numerical bug |

Two rules emerged and are now enforced:

> **A measurement instrument must prove itself on a known answer before touching
> real data.**
>
> **A validity metric must be tested against a known-truth negative control, and the
> negative control must be validated first.** A clean separation on the target data
> is not evidence.

## 5. Rejected hypotheses

| hypothesis | status | decisive evidence |
|---|---|---|
| Stable **linear clock drift** (SRO) | **REJECTED** | a linear fit to `CH1↔CH4` is *worse than a constant* (ΔBIC +5.4) and leaves a 20.2 ms maximum residual on a 20.1 ms signal; `D(τ)` log-log slope is 0.09–0.43, not the ≈2 an SRO gives |
| **Independent sample clocks** | **REJECTED** | all four tracks share one timebase with identical sample counts |
| **Frame-rate / LongGOP reconciliation** | **REJECTED** | PCM audio is sample-timed |
| **`Group A ↔ Group B`** two-group structure | **REJECTED** | the brief's *within-group* pair `CH3↔CH4` is the **most** mobile of all six — 252× the `CH1↔CH2` span |
| **Resampling is required** | **REJECTED** | no SRO; and the premise that resampling must change the sample count is itself false |
| **The movement is a measurement artefact** | **REJECTED** | 110–1090× the floor; survives removal of the level term; frequency-flat; 10× different in two takes of the same setup |
| **The movement is entirely a level artefact** | **REJECTED** | level explains 84 % of the 0.096 ms `CH1↔CH2` variation but leaves 25.1 of 26.2 ms of `CH3↔CH4` |
| **P1's `non_constant` is a P1 defect** | **REJECTED** | on this material `untouched` is the *correct* outcome |
| **The C1305 tail is a search-window artefact** | **REJECTED** | re-measured at ±30/±60/±120/±250 ms; answers identical to three decimals |
| **Resampling / phase vocoder** | **REJECTED as a strategy** | a phase vocoder decouples pitch from duration, which an SRO couples; and there is no SRO here |

## 6. Confirmed model — and what remains unknown

### 6.1 What is established

1. **There is no clock problem.** Relative motion between clock-sharing tracks is
   ≤ 0.13 ms over 80 minutes. Nothing here justifies resampling.
2. **The delay is real, large, and time-varying**: 2.7–26.3 ms, 110–1090× the
   measurement floor, present in two independent recordings.
3. **It is not linear and not smooth.** It is a staircase plus discrete events;
   ~96 % of the `CH3↔CH4` movement is carried by ~15 episodes, and the "quiet"
   hops accumulate only 1.17 ms.
4. **It is frequency-flat**, so it is a genuine time offset.
5. **The two files differ 2.7–9.7×, with content as the only variable.**
6. **`CH1↔CH2` is the only rigid pair** (0.021–0.104 ms, zero episodes).
7. **The published C1305 `CH3↔CH4` span of 26.250 ms is two regimes**, of which the
   corroborated part is a **10.46 ms staircase** in the first 3660 s.

### 6.2 What is *not* established, and must not be claimed

* **What the movement physically is.** The evidence is consistent with microphone
  geometry and source movement (a 3–26 ms change is ~1–9 m of path-length change,
  ordinary for a live event; CH4 is 11 dB quieter, consistent with a distant or
  off-axis microphone). It is also consistent with a wireless link of variable
  latency. **Neither is proven.** A single 48 kHz clock domain makes an electronic
  origin unnecessary but not impossible.
* **Whether the C1305 last-940 s tail is real movement or a measurement failure.**
  The observable is identical either way, and it supplies 11.9 ms of the 26.25 ms
  headline. Agent H declines to decide, and so does this document.
* **Whether the movement should be corrected at all.** For a genuine
  multi-microphone capture, aligning the inter-microphone delay **changes the
  recording** rather than repairing it. That is a product decision, not a
  measurement one.

### 6.3 Consequence for correction

Because the delay is real but its interpretation is unsettled, and because no
criterion for "is this delay well defined?" has yet survived its own validation:

> **No automatic correction is applied.** The correct outcome on this corpus is
> `untouched`, which is also what P1 already does — for a different and weaker
> reason. P2's contribution is the *diagnosis*, not a rewrite of the audio.

The one exception the evidence supports in principle is a pair that is both
**rigid** and **offset beyond tolerance**: piecewise integer shifting is *exact* for
stepwise trajectories (measured −348 dB against ideal), and would be the correct
instrument. `CH1↔CH2` is rigid but already aligned to 0.021–0.074 ms, so there is
nothing to correct on this material.

## 7. Reproducing this chapter

```
python work/p2/scripts/extract_corpus.py --input <C1305.mp4> --outdir work/p2/out/raw --channels 4
python work/p2/scripts/fullrate_delay.py --stem 20260919_C1305 --seg-s 120 --step-s 120 --search-ms 250
python work/p2/scripts/fullrate_delay.py --stem 20260919_C1306 --seg-s 120 --step-s 120 --search-ms 250
python work/p2/scripts/band_test.py --stem 20260919_C1305 --pairs 3-4,1-4,1-3,1-2
python work/p2/scripts/model_select.py --pair ch3-ch4
python work/p2/scripts/episode_map.py
python work/p2/scripts/level_regression.py 20260919_C1305
```

Every one of those scripts validates itself before producing a number; several exit
non-zero rather than report an unvalidated result.
