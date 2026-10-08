# P4 — Stage 3: coordinate decision preparation

**Status: analysis complete, decision `DEFERRED`.** This document prepares the
`window_of` decision (P3 `OPEN-DECISIONS.md` D2). **It does not take it.**

The P4 brief requires three questions answered separately, in actual sample
coordinates, with every conversion written as a formula:

```text
1. what defines a MEASUREMENT position?
2. what defines a CORRECTION APPLICATION position?
3. what defines an OUTPUT SAMPLE coordinate?
```

and forbids two substitutions:

```text
measurement time  !=  apply time
window index      !=  physical correction coordinate
```

---

## 0. Sources

| item | source |
|---|---|
| the three candidate definitions, already derived | `work/p3/p39/window-coordinate-contract.md` — `DESIGNED` as analysis, `OPEN` as decision |
| real per-pair mapping and applied shifts | `work/p3/p311/coordinate/20260919_C130{5,6}_ch3_to_*.json` |
| zone/divergence summary | `work/p3/p311/coordinate/ground-truth-mapping.json` |
| agreement definitions | `work/p3/p311/coordinate/agreement-definitions.json` |
| geometry | `ground-truth-mapping.json` §geometry |

`work/p3/p39/window-coordinate-contract.md` is a **P3.9 design analysis**, not a
P4 invention. P4 reuses its definitions verbatim and adds the P3.11 real-PCM
measurements that did not exist when it was written.

**Geometry, fixed, not a policy variable:**

```text
sample_rate     = 48 000 Hz
hop_samples  h  =   480 000      (10 s)
window_samples W = 1 440 000     (30 s)
search          =     3 000 samples
ratio           = W = 3h
```

---

## 1. What defines a MEASUREMENT position

### 1.1 The definition

A window index `w` names a window **by its start**.

```text
measurement_start_sample(w)  = w · h
measurement_end_sample(w)    = w · h + W
measurement_center_sample(w) = w · h + W/2 = w · h + 720 000
```

In seconds, at `h = 10 s`, `W = 30 s`:

```text
measurement_start_s(w)  = w · 10
measurement_center_s(w) = w · 10 + 15          (P3.9 F3)
measurement_end_s(w)    = w · 10 + 30
```

### 1.2 It is a property of the grid, not of a fixture

P3.8 verified this through the real `measure_window` against an analytic model to
`max|real − model| = 0.0000` samples, and showed it is **identical for a hard
step, a linear ramp, white content and band-limited content**. It is arithmetic.

### 1.3 The analysis window and the apply block are DIFFERENT intervals

This is the first place `measurement time != apply time` must be enforced:

| quantity | interval | length |
|---|---|---|
| the **analysis window** that produced the number | `[w·h, w·h + W)` | `W` = 30 s |
| the **apply block** that number drives | `[w·h, (w+1)·h)` | `h` = 10 s |

They share a left endpoint and differ in length by 3×. **A 30 s measurement is
applied over a 10 s block.** Nothing in the repository records that this was
*chosen*; it is the geometry the frozen plans were built with
(`work/p3/correct.py` `build_plan`).

### 1.4 Consequence: the 15 s attribution offset

```text
a measurement describing time T is applied from T - 15 s
```

Bound, in its corrected form: the offset is bounded by **one hop
(10 s = 480 000 samples) at the production ratio `W = 3·h`, and only there.**
For `W > 3·h` the offset exceeds one hop — an earlier P3.8 audit stated the
general claim and an independent reviewer refuted it with **26 violations across
36 (win, hop) pairs**.

**D3 remains `OPEN`. P4 does not fix it, and does not re-open it.** Recorded here
because any statement about a correction's timing must carry it.

---

## 2. What defines a CORRECTION APPLICATION position

### 2.1 The definition

Three planes must be kept distinct. Writing `k` for an output sample index and
`w` for a window index:

```text
PLANE 1  window index               w  ∈ [0, n_windows)
PLANE 2  applied shift value        S[w]  (integer samples, signed)
PLANE 3  output sample coordinate   k  ∈ [0, N)

the join:            w = join(k)
the correction:      output[k] = input[k + S[join(k)]]
```

**`k` is the physical correction coordinate. `w` is a name. `S[w]` is the
shift.** The brief's prohibition — `window index != physical correction
coordinate` — is exactly the requirement that these three are not conflated.

### 2.2 The two candidate joins

With `h = 480 000`, `W = 1 440 000`:

**Contract A — `latest-start`** (implemented):

```text
w_A(k)      = k // h
block b     = [b·h, (b+1)·h)
block b takes S[b]
```

**Contract B — `nearest-centre`** (observed, not selected):

```text
w_B(k)      = argmin_w |k − (w·h + W/2)|
derived, at W = 3h:
    block  b = 0     -> S[0]        (the unconstrained answer would be w = −1)
    block  b >= 1    -> S[b − 1]
```

`work/p3/p39/window-coordinate-contract.md` §2 also records a second reading
(B2, nearest centre to the block's midpoint). **At the production ratio both
readings give the same applied map**; they diverge only at a finer hop
(`hop = 2.5 s`: B1 gives `0.5923 / 0.4340`, B2 gives `0.4228 / 0.2641`).

> A single agreement number cannot represent an ambiguous contract. Where P4
> reports one, it names which reading.

### 2.3 Arithmetic consequence, stated as a formula

```text
A applies window w's shift from   w·h
B applies window w's shift from   (w+1)·h
```

**B is A lagged by exactly one hop = 480 000 samples = 10 s**, for every `w >= 1`.

---

## 3. What defines an OUTPUT SAMPLE coordinate

### 3.1 The definition

`k ∈ [0, N)` is the index into the corrected output stream, `N` = the number of
samples in the input stream. The output has **the same length as the input**;
`shift_stream`'s contract is `out[n] = in[n + shift]` with out-of-range reads
zero-filled and **output sample count == input sample count**. The per-window
applier keeps that property (`ceil(N/h)` windows, every sample addressed).

So:

```text
output[k]   = input[k + S[join(k)]]        for k inside the plan's coverage
output[k]   = 0 (zero-filled)              where k + S[join(k)] leaves [0, N)
```

### 3.2 Every sample is addressed — this is why coverage matters

| Corpus | N (samples) | plan windows | measured windows | seam |
|---|---:|---:|---:|---:|
| C1305 | 230 750 520 | **481** | 478 | **3** |
| C1306 | 199 903 704 | **417** | 414 | **3** |

```text
measure.window_starts  ->  floor((N - W)/h) + 1      (only FULL windows)
applier needs          ->  ceil(N/h)                 (every sample addressable)
```

Two independent confirmations of this seam exist and they are consistent:

* `work/p3/p38/agent_b_closure/out/b_window_of.txt` — for C1305,
  `apply_needs = 481`, `max_window_of = 480`, `n_starts = 478`,
  `window_of_exceeds_plan = true`. The same file shows a naive (measure-length)
  plan **raising** `AudioSyncContextError: shift plan has 6 windows but the
  stream needs 4 at hop_samples=480000`, while the correctly-sized plan does not
  raise. **Neither layer is wrong — the interface was missing.**
* independently, P3.10/P3.11 refused the unpadded plan and used 481 / 417.

**So a sample in the tail 3 windows of the plan has no measurement of its own.**
That region is not a coordinate question; it is a gap, and it is governed by the
gap policy. It is named here because a reader comparing "478 measured" with
"481 windows" is looking at the seam, not at a defect.

### 3.3 The raw mapping is not total without a clip

`w_B(0)` unconstrained is `−1`, which is not a window index. The raw
nearest-centre mapping **returns a negative index at block 0** and must be
clipped explicitly:

```text
w_B_raw(0)  = -1        <- out of range
w_B(0)      = clip(-1, 0, n_windows-1) = 0
```

Under `latest-start` no clip is needed inside coverage, but **beyond** the plan's
coverage `w_A(k) = k // h` exceeds the last window index and is **clipped to
`n_windows − 1`** — a stated choice, not an accident. Both clips are asserted in
`tests/audio_sync_p311/test_coordinates.py` (T3, T2).

### 3.4 `sample-domain` is not a third join

```text
sample-domain  ->  the largest window whose measurement window covers the sample
```

Measurement windows are 30 s on a 10 s hop, so **three** cover any sample. The
largest covering index is `k // h`, i.e. `latest-start`. **Identical function.**

`ground-truth-mapping.json` records `agree: 230 750 520, disagree: 0` for C1305
and `agree: 199 903 704, disagree: 0` for C1306
(`latest_vs_sample_domain`). **It must not be reported as a third candidate.**

---

## 4. FIRST DIVERGENCE, in actual sample coordinates

### 4.1 Index divergence — the mapping from sample to window

Measured over the whole real stream. Source: `ground-truth-mapping.json`.

| Corpus | block-0 region | first divergence | last divergence | zone | samples |
|---|---|---|---:|---|---:|
| C1305 | `[0, 480 000)` | **sample 480 000 = 10.0 s** | 230 750 519 | 1 zone, value `1` | 230 270 520 |
| C1306 | `[0, 480 000)` | **sample 480 000 = 10.0 s** | 199 903 703 | 1 zone, value `1` | 199 423 704 |

Both zones have `value = 1`: the index difference is **exactly one window**, and
`max_abs_index_difference = 1` everywhere.

Explicitly, at the divergence boundary:

```text
k = 480 000 = 1 · h       (block 1)
w_A(480 000) = 480 000 // 480 000 = 1
w_B(480 000)                      = 0
difference                        = 1
```

The agreement is exactly `480 000` samples = `1 · h` = **the first hop**, on both
corpora — the largest possible agreement for these geometries.

### 4.2 Value divergence — the mapping from sample to applied shift

A different sample position, because it depends on where the plan's shift changes
rather than on where the index changes:

| Corpus | first value divergence | max abs shift difference | disagreeing samples | zones | zone length (hops) |
|---|---:|---:|---:|---:|---|
| C1305 | **sample 960 000 = 20.0 s** | **732 samples** | 99 360 000 | 73 | 1 … 15 |
| C1306 | **sample 960 000 = 20.0 s** | **211 samples** | 127 200 000 | 63 | 1 … 19 |

**The two divergences are 480 000 samples = 10 s apart, and that is the whole
result.** Index divergence begins at 10.0 s; applied-value divergence begins one
hop later at 20.0 s, because the lag only becomes visible where the shift
sequence changes.

The applied shifts at the plan grid, read from the real record
(`20260919_C1305_ch3_to_ch4.json`), first five blocks:

| block `b` | sample `b·h` | `w_A` | `S[w_A]` | `w_B` | `S[w_B]` |
|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | **0** | 0 (clipped from −1) | **0** |
| 1 | 480 000 | 1 | **0** | 0 | **0** |
| 2 | 960 000 | 2 | **732** | 1 | **0** |
| 3 | 1 440 000 | 3 | **717** | 2 | **732** |
| 4 | 1 920 000 | 4 | **716** | 3 | **717** |

So:

* blocks 0–1 are **identical** — that is the 480 000-sample index agreement;
* at **block 2, sample 960 000**, `latest-start` applies **732** while
  `nearest-centre` applies **0**;
* at **block 3, sample 1 440 000**, `nearest-centre` applies the **732** that
  `latest-start` applied one hop earlier.

**`w_B` is exactly `w_A − 1` for every `b >= 1`, at every one of the 481 block
starts.** The wrap is not occasional: `max_abs_index_difference = 1` everywhere
and the zone histogram has a single entry.

The maximum shift difference of **732 samples** is `max|S[w] − S[w−1]|` over the
plan — an arithmetic property of the shift sequence, not a bound on the
coordinate change in general.

---

## 5. APPLICATION ZONES

### 5.1 Index zones

One zone per corpus, from sample `h` to the end:

```text
C1305:  [480 000, 230 750 519]   230 270 520 samples   value 1
C1306:  [480 000, 199 903 703]   199 423 704 samples   value 1
```

### 5.2 Value zones — where the applied shift differs

| Corpus | zones | zone length (hops) | samples |
|---|---:|---|---:|
| C1305 | **73** | 1 … 15 | 99 360 000 |
| C1306 | **63** | 1 … 19 | 127 200 000 |

`agreement-definitions.json` states the mechanism exactly:

> **VALUE agreement** … differs only where the plan's shift changes, so the
> disagreement is a **band around each shift change**.

So the value zones are **not** the index zone. They are a union of bands, one per
place the shift sequence moves, and their count tracks the number of distinct
applied values: **36–75 distinct values per plan**. The measured zone counts
(73 and 63) are of that order, as expected.

The two headline agreement figures are therefore both correct and neither
substitutes for the other:

```text
INDEX agreement   C1305 0.2080168660074959 %   C1306 0.24011561086431897 %
VALUE agreement   C1305 0.5694050873644835     C1306 0.3636936312095548
```

The `OPEN-DECISIONS.md` addendum rounds the value figures to
**56.94 % / 36.37 %**; the P4 brief writes "≈ 57 % / ≈ 36 %". **P4 uses the
unrounded values above** and names which figure it means every time.

---

## 6. BOUNDARY BEHAVIOUR

Every boundary below was checked against the record, not assumed.

| # | boundary | `latest-start` | `nearest-centre` | evidence |
|---|---|---|---|---|
| 1 | `k = 0`, block 0 | `w = 0`, `S[0]` | raw `w = −1`, **clipped to 0** | asserted, `test_coordinates.py` T3 |
| 2 | `k = h/2 = 240 000` | no change — A is constant on `[0, h)` | **no change** — for `W = 3h` B1 and B2 both put all of block 0 with `S[0]` | B owns `[w·h + h/2, …)` for `w >= 1` only |
| 3 | `k = h = 480 000` | `w` steps 0 → 1 | `w` stays **0** | first index divergence; `ground-truth-mapping.json` |
| 4 | `k = 960 000 = 2h` | applies `S[2] = 732` | applies `S[1] = 0` | first **value** divergence |
| 5 | `k = N_coverage = 481·h = 230 880 000` (C1305) | `k // h = 481` **exceeds** the last index 480 → **clipped to 480** | clipped to 480 | `test_coordinates.py` T2 asserts the clip explicitly |
| 6 | `k >= N` | zero-filled read, output length preserved | same | `shift_stream` contract |
| 7 | stream shorter than one hop | seam **0**; both mappings agree on the whole stream | same | P3.9: "seam 0 for any stream at most one hop long" |
| 8 | stream `0` samples | `n_starts = 1`, `apply_needs = 0`, no window claimed | same | `b_window_of.txt` |
| 9 | stream `1` sample | `n_starts = 1`, `apply_needs = 1`, `max_window_of = 0` | same | `b_window_of.txt` |

Boundary 5 is the one a reader is most likely to get wrong: **the closed form and
the clipped trajectory are different functions beyond coverage, and the clip is
the contract.**

---

## 7. THE REAL-PCM EFFECT

Same measurement records, same policy (`hold`), same resolved mask. Only the join
differs. Source: `coordinate-comparison.md`.

| Corpus | Pair | arm | after median | after p95 | after max |
|---|---|---|---:|---:|---:|
| C1305 | ch3→ch1 | latest-start | 12.034 | 1119.440 | 1533.360 |
| C1305 | ch3→ch1 | nearest-centre | 12.000 | 1119.622 | 1533.360 |
| C1305 | ch3→ch2 | latest-start | 6.574 | 422.000 | 1375.820 |
| C1305 | ch3→ch2 | nearest-centre | 6.000 | 423.045 | 1375.820 |
| **C1305** | **ch3→ch4** | **latest-start** | **4.233** | **734.240** | **1796.063** |
| **C1305** | **ch3→ch4** | **nearest-centre** | **3.253** | **734.240** | **1064.141** |
| C1306 | ch3→ch1 | latest-start | 14.586 | 337.470 | 1702.927 |
| C1306 | ch3→ch1 | nearest-centre | 14.345 | 351.708 | 1702.927 |
| C1306 | ch3→ch2 | latest-start | 8.464 | 403.430 | 1673.504 |
| C1306 | ch3→ch2 | nearest-centre | 8.464 | 427.540 | 1673.504 |
| C1306 | ch3→ch4 | latest-start | 3.000 | 105.885 | 757.016 |
| C1306 | ch3→ch4 | nearest-centre | 2.773 | 105.885 | 757.016 |

Control: `shared_before_control.identical_measurement_so_before_should_match = true`,
and every `before` median matches exactly.

### 7.1 The divergence that matters, isolated

The one material real-PCM divergence is **C1305 ch3→ch4's maximum**, and the
record shows exactly why. Both arms measured the same points.

| quantity | latest-start | nearest-centre |
|---|---:|---:|
| after max | **1796.063145291734** | **1064.1407554116545** |
| after min | 0.0 | 0.006752953136128781 |
| max_abs_delta vs before | 731.9223898800794 | **0.0** |
| max_abs_ratio | 1.687805993857402 | **1.0** |
| tail verdict on max | **WORSENED** | **UNCHANGED** |
| **excess_inside_coverage max** | **0.6150136686810583** | **732.0** |
| excess p99 | 0.21327387510218607 | 91.69077622686062 |
| excess mean | 0.02008595357814256 | 6.785343104727171 |

Read the last three rows together with §4.2.

`latest-start`'s 1796.063 is the **refused-window artefact**
`evidence-freeze.md` §3.4 documents: the estimator returned a number for a window
it had declared `valid = False, reason = intra_window_unstable`. Its
`excess` max of **0.615 samples** says the applier did what it was told.

`nearest-centre` leaves that point at 1064.141 — the before value — because the
lag places a *different* window's shift there.

### 7.2 A consequence the summary tables do not show

**`nearest-centre`'s excess max is `732.0` where `latest-start`'s is `0.615`.**

`excess = after − (before − applied)`. Under `latest-start` the sample at block
`b` receives `S[b]`, so `excess ≈ 0`. **Under `nearest-centre` the sample at
block `b >= 1` receives `S[b−1]`, while `expected` is still computed from the
plan's `S[b]`** — so `excess` is `S[b−1] − S[b]`, and its maximum over the plan
is `max|S[w−1] − S[w]| = 732` samples.

```text
this is NOT a defect in nearest-centre's audio
this IS a consequence of the coordinate choice:
    the plan's per-window shift indexing is part of the apply contract,
    not metadata
```

**P4 records this as a new finding and draws no policy conclusion from it.** It
is neutral evidence: it says the two contracts are not interchangeable *for
verification or reporting*, whichever is chosen. A P4 verifier must compute its
expectation from the shift **actually applied at that sample**, under whichever
join is declared, or it will report a 732-sample "excess" that no audio exhibits.

### 7.3 Which arm had the better tail

```text
C1305 ch3->ch4 after max:   latest-start 1796.063   nearest-centre 1064.141
C1306 ch3->ch1 after p95:   latest-start  337.470   nearest-centre  351.708
C1306 ch3->ch2 after p95:   latest-start  403.430   nearest-centre  427.540
C1305 ch3->ch1 after p95:   latest-start 1119.440   nearest-centre 1119.622
C1305 ch3->ch2 after p95:   latest-start  422.000   nearest-centre  423.045
```

`nearest-centre` is better on one figure and worse on three, by margins of 1–4 %,
plus one large margin (1796 → 1064). **This is not a ranking.** Both
`P3.9` and `P3.11` declined to rank, and the reason given is that the two are
**symmetric in error magnitude** — one applies a measurement a hop early, the
other the same magnitude late. A single corpus's maximum moving is not evidence
about which direction of error is preferable.

---

## 8. THE THREE QUESTIONS ANSWERED

### 8.1 What defines a measurement position?

```text
a WINDOW INDEX w, interpreted as the START of the analysis window:
    measurement_start_sample(w)  = w · h
    measurement_center_sample(w) = w · h + W/2
not the block, not the sample, not the apply position.
```

### 8.2 What defines a correction application position?

```text
the OUTPUT SAMPLE INDEX k, through a declared join:
    join(k) -> w          (the mapping under decision)
    output[k] = input[k + S[join(k)]]
the plan carries one shift per window; the block a shift drives is [w·h, (w+1)·h)
under latest-start and [(w+1)·h, (w+2)·h) under nearest-centre.
```

### 8.3 What defines an output sample coordinate?

```text
k ∈ [0, N), N = the input sample count.
The output has the same length as the input; out-of-range reads zero-fill.
The plan must cover ceil(N/h) windows so that every k is addressed;
a plan of floor((N-W)/h)+1 windows leaves a seam of W/h - 1 = 2..3 windows.
```

### 8.4 The three are not interchangeable

| substitution the brief forbids | why it fails, measured |
|---|---|
| `measurement time ≈ apply time` | a 30 s measurement drives a 10 s block; a measurement describing `T` is applied from `T − 15 s` |
| `window index ≈ physical correction coordinate` | index agreement is `0.21 % / 0.24 %` while value agreement is `56.94 % / 36.37 %`; the two planes move different sets of samples, at different positions (10.0 s vs 20.0 s) and different counts (230 270 520 vs 99 360 000) |

---

## 9. THIRD OPTION: the dual-coordinate bookkeeping contract

`work/p3/p39/window-coordinate-contract.md` §2 defines a third contract **C**,
and P4 records it because it is the one option that does not require taking D2:

```text
C is not a rival assignment rule. It is a BOOKKEEPING contract: keep
measurement_time (the analysis window that produced a number) and apply_time
(where that number is used) as separate, explicit fields on every record, and
never collapse them.

    measurement_start_s, measurement_center_s, measurement_end_s
    apply_start_s, apply_end_s
    measurement_window_id, apply_window_id
    join: <the declared rule that maps apply position -> measurement window>
```

Its properties, stated as P3.9 stated them:

* it **moves no sample** by itself;
* it makes the join **a declared, versioned artifact instead of an implicit
  consequence of an integer division**;
* if `join = latest-start`, its arithmetic **is** A's;
* it **does not remove the 15 s offset** — it removes the *implicitness*, so the
  offset becomes a recorded per-record quantity instead of an inference from
  arithmetic;
* **it does not decide D2.**

**This is what P4 implements as the production contract shape**: the join is a
named, declared, versioned parameter, and every record carries both coordinates.
Which join is the *default* remains `DEFERRED`.

---

## 10. DECISION STATUS

```text
D2  window_of convention                       OPEN
coordinate policy                              DEFERRED
implementation default                         latest-start
reason for that default                        REPRODUCIBILITY, not preference:
    it is the contract every P3.3-P3.8 real-corpus number was produced under,
    and changing it silently alters every historical plan
```

What P4 did **not** do:

```text
did not select latest-start as the product policy
did not select nearest-centre
did not report sample-domain as a third candidate
did not rank the two arms
did not fix the 15 s attribution offset
did not re-open D3
did not change window or hop
```

**`DECISION_REQUIRED: D2`** — owner: product + engineering jointly. The question,
in the terms P3 framed it:

> should a correction **anticipate** a change, or **lag** it?

P4 builds the mechanism so either answer is a declared parameter rather than a
code change, and states the consequence of each in
`policy-matrix.md` §B.3.
