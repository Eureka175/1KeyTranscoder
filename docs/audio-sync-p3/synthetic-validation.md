# P3 — Synthetic Known-Answer Validation

> Stage 3 of Audio Sync P3: the suite that decides whether the measurement layer may be
> pointed at real material. Source: `work/p3/synthetic_kat.py`, results in
> `work/p3/out/synthetic_kat.json`, console log in `work/p3/out/kat_stdout.txt`.

## 1. What the suite asks

Two questions, deliberately kept apart:

1. **Accuracy** — for a delay we constructed ourselves, does the estimator return it?
2. **Validity discrimination** — does the estimator *reject* the windows where it cannot
   know the answer?

Question 2 is the one P2 could not answer, and it is the question that decides whether a
correction can ever be safe. The suite therefore reports a **confusion matrix**, not a
success rate: every case declares in advance whether the correct verdict is ACCEPT or
REJECT, and a run that accepts everything scores 0 on the reject half.

## 2. Fixtures, and why they are built the way they are

| Fixture | Construction | Used for |
| --- | --- | --- |
| broadband programme-like | FFT-masked noise, 150–7000 Hz, spectral tilt, deterministic seed | accuracy on material resembling real content |
| exact integer delay | index shift | exact by definition |
| fractional delay | windowed-sinc time-domain resampler (64 taps, Blackman) | accuracy on fractional delays |
| **exact phase-ramp delay** | `Y(f) = X(f)·exp(-j2πfd/fs)`, applied in the frequency domain | **diagnosis** — see `diag_fractional.py` |
| narrowband | 40–300 Hz mask | must be REJECTED |
| periodic tone | single sine at 4410 Hz | must be REJECTED |
| independent noise | two disjoint noise realisations | must be REJECTED |
| out of range | a real delay of 12000 samples against a 1500-sample search | must be REJECTED |
| two arrivals | 0.6–0.9 amplitude second arrival, 240–13500 samples away | see §4 |
| silence | zeros | must be REJECTED |
| fixture self-test | an integer-valued delay routed through the *sinc* path, compared with an exact integer shift | proves the fractional fixture is not misplaced |

The window is always cut by the estimator's own `read_window`, from whole streams, never
by hand. Three separate defects were introduced by hand-cutting windows, and none of them
can exist once the real path is the only path.

## 3. The ten defects this suite caught

Every one produced plausible, self-consistent numbers. None would have been visible in a
corpus run, because a corpus has no truth to compare against.

| # | Defect | How it presented | How it was caught |
| --- | --- | --- | --- |
| 1 | the fixture *compensated* for its own injected delay, so the two windows covered different parts of the programme | the estimator returned the injected delay with the **wrong sign** — error exactly `−d` | a `+137` case returning `err = −136.995` |
| 2 | the fixture cancelled the delay entirely (same bug, one layer down) | every constant case measured **0.0**, error exactly `−d` | the `−d` pattern is not a rounding artefact |
| 3 | windowed-sinc decomposition used `floor`/`ceil` inconsistently, applying `+0.25` as `−0.75` | fractional cases off by a whole sample | fractional errors near ±1.0 |
| 4 | target slice origin used `−s` instead of `+s` | a systematic `+1355`-sample bias | the bias was constant across every case, including a matched pair whose truth was 0 |
| 5 | probe/matched-pair construction compared two windows reading different regions | a constant offset that survived every fix | same signal for every injected delay |
| 6 | no analysis taper: the target window's ±search shoulders formed a rectangular discontinuity the reference never saw | `std(phase) = 1.81 rad`, still `0.102 rad` after de-rotation, outliers at π | phase statistics, not the answer |
| 7 | bandwidth estimate and phase fit derived their own FFT length, different from the correlation's | the band mask landed on the wrong frequencies → a transform artefact read as a delay of `+1355` | a matched pair must give exactly 0 |
| 8 | `df` written as `N/fs` (seconds) where the phase-step model needs `fs/N` (Hz) | constant offset, scaled by `(N/fs)² ≈ 7.6e3` | the offset was constant and geometry-linked |
| 9 | de-rotation pivot omitted `origin`, so the "residual" was the whole lag | estimate `= 2·pivot − lag`; at `pivot = 1500` a true `1637` came back as `1574.7` | offset cases only — invisible at `pivot == lag`, which is what a matched pair exercises |
| 10 | 99 %-energy bandwidth included bins with a few percent of the peak | the phase fit reported lag `1574.7` for a true `1637`; several cases returned half the true delay | replacing it with a magnitude threshold removed exactly those bins |

Defects 6–10 were all in the *same function*, and each fix left the others in place: the
estimator passed its own self-test at every intermediate stage. What finally separated
them was an **independent construction** (the exact phase ramp, which satisfies the
estimator's model by construction and contains no filter) plus an **independent method**
(peak interpolation vs phase slope). Everything else was guesswork.

## 4. Result

```text
estimator 3.0.0-dev   n_win = 30 s   search = ±1500 samples   hop = 10 s   taper = hann

matrix of 28 cases:   should accept 23/23     should reject 5/5
                      false accept 0          false reject 0
                      exit code 0
median |error| over accepted cases:  2.8e-5 samples  (0.58 µs)
max    |error| over accepted cases:  0.169 samples   (3.5 µs)
fractional: 8 cases, max |err vs nearest integer| = 0.50 samples
```

### 4.1 Accuracy

Constant delays of `0, ±1, ±137, ±1200` samples and fractional delays of
`±0.25, ±0.5, ±0.75, 23.37, −12.345` samples are all recovered to ≤ 0.17 samples of error,
with a median of 2.8e-5. A white-noise pair, the easiest possible case, is exact. The three
fixture self-tests return 0.00000 samples, confirming the fractional construction is not
misplaced by even one sample.

The two entries above 0.1 samples (`const_−0.750` at 0.169, `const_−0.500` at 0.151) sit
above the sub-0.001 floor of the rest, and §5.3 attributes them to the sinc fixture rather
than to the estimator: on the exact phase-ramp fixture the same delays are exact to
0.00003 samples.

### 4.2 Validity discrimination

| Negative control | Verdict | Reason |
| --- | --- | --- |
| silence | REJECT | `silent` |
| independent noise between channels (no delay exists) | REJECT | `intra_window_unstable` — all 5 sub-windows below peak/noise 20, so their 1817-sample spread is not evidence |
| true delay 12000 samples against a ±1500 search | REJECT | `intra_window_unstable` — same mechanism |
| 300 Hz-limited content | REJECT | `narrowband` (band 260 Hz) |
| single 220 Hz tone | REJECT | `weak_peak` (peak/noise 2.8) |

The mechanism that rejects the two noise cases is worth naming, because it is the one
that matters for corpus work: **a sub-window probe is only allowed to testify about the
shape of the delay if the probe itself is a credible measurement.** Independent noise
produces five individually worthless probes whose spread looks like enormous motion; the
gate refuses to read motion out of them.

## 5. Honest corrections to this suite

### 5.1 Three controls were declared wrongly, and it was fixed in the open

`reject_two_arrivals_sep{400,1000,240}` were built as comparable two-arrival mixtures and
declared REJECT. They are **ACCEPT**, and the estimator was right:

* measurement: `300.0062`, `299.9955`, `299.9887` against a true first-arrival delay of
  `300.0` — errors of 0.011 samples or less;
* a second arrival at amplitude 0.35–0.9 is attenuated by about `1/6000` inside the search
  window (its correlation contribution falls outside the overlap), so it contributes
  nothing and the delay genuinely **is** well defined;
* the probes' spread is 0.002–0.012 samples.

The declarations were corrected to ACCEPT with the truth of the arrival that is actually
measurable, and renamed `two_arrivals_within_search_*`. The record of the mis-declaration is
kept here rather than quietly dropped: changing a declared truth after seeing the answer is
how a suite stops meaning anything, so it has to be visible.

The lesson is the one P2 learned from the second-peak ratio: **a control is only a control
if its truth is established independently of the thing it tests.**

### 5.2 The two-arrival case that *is* a control

`accept_two_arrivals_far_apart` (half-amplitude second arrival 13500 samples away, 9× the
search range) measures `299.9964` — error 0.0036 samples. Together with §5.1 it delimits
the rule: what matters is not the amplitude ratio but whether the second arrival enters the
overlap at all. That rule is not yet formalised, and it is listed as open work.

### 5.3 Fixture-attributable residuals

On the **exact phase ramp** fixture — the same delay values, no interpolation filter — the
peak-interpolation path is exact to 0.00003 samples on every case. So the 0.15–0.17-sample
residuals in §4.1 are attributable to the **windowed-sinc fixture**: a 64-tap Blackman-
windowed filter has a small phase ripple, and at half a sample of shift the sinc's own
response is worth about a fifth of a sample. That is a fixture limitation, and it is the
reason the exact phase ramp exists.

## 6. What the suite does not establish

1. **Nothing about real material.** A synthetic pass is permission to measure, not
   evidence about the corpus.
2. **Nothing about windows containing motion.** The intra-window probe's motion rule is
   exercised, but no fixture contains a *specified* ramp inside a single window; that is
   Stage 4/5 work.
3. **Nothing about uncertainty calibration.** `probe_sigma_samples` is conservative by
   construction and is not yet a real error bar.
4. **Nothing about long-run stability.** One window at a time is all that is tested here.

## 7. How to reproduce

```text
python work/p3/synthetic_kat.py        # ~17 min: 33 cases, ~30 s each
python work/p3/show_kat.py             # the matrix as a table
python work/p3/diag_fractional.py      # accuracy against the exact phase-ramp fixture
```

The exit code is 0 only when every matrix verdict matches its declared truth. It is
currently **0**: `23/23 accept, 5/5 reject, 0 false accept, 0 false reject`.
