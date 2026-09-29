# Audio Sync P2 — Trajectory Model

> What the time-varying delay looks like as a signal, which model classes were
> tested, and why none is established. Sources: `work/p2/agent-h-model.md`,
> `work/p2/synthesis.md` §5, and the main agent's independent measurements.
>
> **No production code is modified, and this chapter does not specify one.**

---

## 1. The question

Given a per-pair delay trajectory measured over an hour, which model class
describes it?

```
M0  constant              d(t) = d0
M1  linear                d(t) = d0 + r·t                    (the clock-drift hypothesis)
M2  piecewise constant    d(t) = d_k on interval k
M3  piecewise linear      d(t) = d_k + r_k·(t − t_k)
M4  smooth nonlinear      spline / low-order polynomial
```

The answer decides what correction, if any, is even meaningful: M0 → one integer
shift; M2 → segment-wise integer shifts; M1 → resampling; M4 → nothing defensible.

## 2. Fitted results

### 2.1 Main-agent fits, C1305 canonical data

`work/p2/scripts/model_select.py`; residual is after the fit.

| model | CH1↔CH2 (control) | CH1↔CH3 | CH1↔CH4 | CH3↔CH4 |
|---|---|---|---|---|
| M0 constant | RMS 0.031, max 0.103 | RMS 1.966, max 8.111 | RMS 1.895, max 20.110 | RMS 7.190, max 16.772 |
| **M1 linear** | RMS 0.024, max 0.078 | RMS 1.757, max 7.220 | **RMS 1.894, max 20.176** | RMS 4.259, max 13.786 |
| M2 piecewise-const, k=4 | RMS 0.013, max 0.052 | RMS **1.176**, max 6.533 | RMS **1.110**, max **9.090** | RMS 2.752, max 18.362 |
| M3 piecewise-linear, k=4 | RMS 0.011 | — | — | RMS **2.432** |
| BIC-best | M3 (fitting noise) | M2 | M2 | M3 |

### 2.2 Agent H's independent fits

Agent H reports the best **non-overfitting** moving-class fit (M2) leaves
**0.88–2.74 ms RMS** on the moving pairs, while M1 leaves 1.88–4.08 ms with a
12.86 ms maximum residual on a 26.25 ms span. It also found M3 needed constraining
to `min_seg = 3` because on 40 points it reached **24 parameters** and "fitted the
trajectory".

### 2.3 A rule defect that had to be corrected

`model_select.py` originally let BIC choose the knot count. On the **control pair**
`CH1↔CH2` — whose entire signal is 0.104 ms, ~5× the measurement floor — BIC
preferred a **10-parameter piecewise-linear** model. That is fitting noise.

**Agent H measured this systematically: BIC selects a 6–12-parameter piecewise
linear model for every real pair including both controls — 12 failures out of 12.**
Agent H therefore reports BIC but never lets it decide.

## 3. Verdict by model

* **M0 constant — accepted for `CH1↔CH2` only.** Span 0.104 ms (C1305) / 0.021 ms
  (C1306), zero movement episodes. Agent H rejects the alternative model classes by
  **three independent mechanisms**, not one threshold:
  1. **materiality** — span 5 / 1 samples against a 24-sample (0.5 ms) gate, with a
     **26×** margin to the nearest moving pair. The gate is deliberately *more
     conservative* than P1's own rule (Agent B: P1 rejects at 1.01 ms, accepts
     0.67 ms), so this rule **cannot introduce a correction P1 would have refused**;
  2. **event magnitude** — top hop 4 samples against a 20-sample requirement;
  3. **boundedness** — displacement ratio 0.200 / 0.000 against a ≤ 0.5 bound.

* **M1 linear — REJECTED**, on four independent grounds:
  1. for `CH1↔CH4` a linear fit is **worse than a constant** (ΔBIC **+5.4**), and its
     20.176 ms maximum residual essentially equals the 20.110 ms of the constant fit
     — the line explains nothing;
  2. for `CH3↔CH4` it leaves 4.259 ms RMS and a 13.786 ms maximum against a
     26.25 ms peak-to-peak signal;
  3. the structure function `D(τ)` has log-log slope **0.09–0.43** for short lags —
     bounded movement; a true rate offset gives ≈ 2;
  4. the large ppm figures originally reported (−190,000 ppm fitted over the file)
     are an artefact of fitting a line to piecewise structure.

* **M2 piecewise constant — best supported, still not established.** It beats linear
  and constant on every moving pair by ΔBIC 60–240, and it is the only
  correction-relevant class the evidence supports. But it leaves **0.88–2.74 ms
  RMS**, which is **40–130×** the 0.021 ms floor.

* **M3 piecewise linear — marginal.** Best BIC on `CH3↔CH4` (RMS 2.432 ms) but with
  10 parameters on 238 points, and it over-fits the control at 12/12.

* **M4 smooth nonlinear — not supported.** ~96 % of the `CH3↔CH4` movement is
  carried by ~15 discrete episodes; the "quiet" hops accumulate **1.17 ms against a
  27.0 ms span**.

## 4. The decisive structural finding: the headline number is two regimes

The published C1305 `CH3↔CH4` span of **26.250 ms is not one movement**
(Agent H §6.4, arithmetically verified):

```
full-file span                              26.250 ms   (714 .. -546 samples)
after removing the last 8 points            14.354 ms   (714 ..   25 samples)
head span, ignoring a dip to +25            10.458 ms   (714 ..  212 samples)
span of the 8 tail points alone             23.292 ms   (572 .. -546 samples)
```

* **Head, first 3660 s:** a corroborated staircase — six transitions
  (714→609→531→456→292→282→262→212), then a **2640 s plateau** at 212–213 samples —
  with **every hop confirmed by an independent estimate of the same pair**.
* **Tail, last 940 s:** a separate regime swinging between +12 and −11.4 ms on a
  ~20 s cadence, supplying **the four largest hops and 80 % of the total variation**,
  where three-point closure fails by up to **23.042 ms** against a 0.2 ms floor.

**The movement that could justify a correction is the 10.46 ms staircase in the
first 3660 s — not the published 26.25 ms.**

### 4.1 The tail is not a search-window artefact

A natural suspicion is that a wide search window selects different peaks. Agent H
re-measured at **±30 / ±60 / ±120 / ±250 ms**, with a self-test recovering injected
delays exactly at *every* width, and the answers are **identical to three decimals**
(−11.229, −11.375, −9.896, −9.604 ms at t = 4500, 4620, 4020, 4140 s).

So the tail is a stable measurement of either a genuinely bimodal signal or a stable
failure. **Agent H declines to decide which, and so does this document.** It supplies
**11.9 ms of the 26.25 ms headline**.

*(A main-agent attempt to attribute the instability to the wide window was
withdrawn: its test used windows centred at zero lag, which for `CH1↔CH3`
(+23.8 ms) excluded the true peak. Agent H's test is the valid one.)*

## 5. Agent H's decision rule

Seven ordered gates, every step O(N) on the `frames` list P1 already produces —
**no new audio I/O**:

```
1. COVERAGE            coverage < 0.60, or a usable-frame gap > 120 s  -> REFUSE
2. CORROBORATION       every material hop (|Δd| > 20 samples) must be seen by
                       some alternating path through the other 3 channels
                       -> else REFUSE      [an un-corroborated hop is a localised
                                            mis-measurement, not a movement]
3. MATERIALITY         span <= max(24, 6*floor, 0.5 ms)  -> CONSTANT
4. EVENT STRUCTURE     sustained steps, top-hop share, quiet-hop noise bound
                       -> PIECEWISE CONSTANT
5. BOUNDEDNESS         D(tau) saturation or displacement ratio <= 0.5
                       -> SMOOTH NONLINEAR -> REFUSE
6. LINE                D(tau) slope > 1.4 short / > 1.2 long, no saturation
                       -> LINEAR -> REFUSE (report; resampling would be correct)
7. otherwise           -> UNSTRUCTURED -> REFUSE
```

Its measured behaviour on the corpus:

| file | pair | decision | reason |
|---|---|---|---|
| C1305 | `ch1-ch2` | `fixed` | `constant` |
| C1305 | `ch3-ch4` | `untouched` | `p2_nonlinear_uncorrectable` |
| C1305 | `ch1-ch3`, `ch2-ch3`, `ch2-ch4` | `untouched` | `p2_drift_uncertain` |
| C1305 | `ch1-ch4` | `untouched` | `p2_nonlinear_uncorrectable` |
| C1306 | `ch1-ch2` | `fixed` | `constant` |
| C1306 | `ch3-ch4` | **`p2_piecewise_integer`** | `p2_piecewise_constant_correctable` |
| C1306 | the rest | `untouched` | `p2_nonlinear_uncorrectable` |

### 5.1 An integration defect in that output

`decision = "p2_piecewise_integer"` **cannot be read by the host.**
`core/audio_models.py:2111` `_row_status` recognises only `fixed`, `already_aligned`,
`anchor`, `untouched` (plus the six legacy reasons); anything else falls through to
`SyncStatus.NOT_PROCESSED`. **A track P2 correctly aligned would be reported as not
processed.**

The fix is to keep `decision = "fixed"` and carry the new semantics in the **reason**
code — which is what Agent F specified: *new `reason` codes, never new `decision`
values*.

### 5.2 Two rule defects Agent H states rather than hides

* fixture `F3b` should refuse rather than report `p2_nonlinear_uncorrectable`;
* fixtures `X2`/`X3` under σ ≥ 0.29 samples return `unstructured` instead of
  `drift_uncertain`.

And one limitation proven rather than assumed: `closure_test.py` asserts in its own
self-test that a **common-mode** channel step is invisible to alternating paths — so
a real single-channel latency event would *not* be caught by gate 2.

## 6. Why no model is established

Every candidate leaves residuals **40–130× the measurement floor** on the moving
pairs. The honest, sufficient claim is the weaker one:

> **The movement is not linear, not smooth, and dominated by discrete events.**

That claim is enough to rule out resampling (M1 rejected) and to rule out continuous
correction (M4 unsupported), but **not** enough to select a model that a correction
could be built on. The gate condition "model selection has evidence" therefore
**FAILS** (see `validation.md` §5).

## 7. What would falsify the current position

* A pair with a **stable, single-arrival** structure and a **large** offset beyond
  `aligned_max_ms` — none exists in this corpus. Such a pair would be correctable by
  a single integer shift, and M0 would be established for it.
* An ambiguity criterion that **passes a validated known-truth negative control** —
  none has yet (see the research record).
* A demonstration that the C1305 last-940 s tail is **real movement** rather than
  measurement failure — that would restore the 26.25 ms figure and make the
  trajectory genuinely bimodal rather than two regimes.
