# P3 — Long-Duration Measurement

> Stage 4 of Audio Sync P3: the whole-timeline measurement on real material. Produced by
> `work/p3/run_long.py`; records in `work/p3/experiments/<stem>/`.
>
> Configuration: 30 s window, 10 s hop, ±31.25 ms search (1500 samples), Hann taper,
> reference CH3. Layer A (`raw_measurements_*.jsonl`) keeps **every** window, valid or not,
> with its reason.

## 1. Why a whole timeline, and why 30 s / 10 s

P2 measured on a 120 s grid and found that its own plan left sub-grid motion uncorrected;
the lesson recorded there is that **the plan's resolution is part of the design**. A 30 s
window with a 10 s hop gives 478 windows over an 80-minute recording and a 3× overlap, so
a section is described by three windows rather than one — which is what makes an isolated
bad window detectable as an isolated bad window.

The trade is explicit and is not hidden: a 30 s window *averages* any motion inside it. The
sub-window probe measures whether the window is scalar-valued; it does not recover when
inside the window the delay was what. §4 states the consequence for the trajectory.

## 2. Per-pair output

Each pair produces

```text
experiments/<stem>/raw_measurements_<ref>_to_<tgt>.jsonl   Layer A, one record per window
experiments/<stem>/measurement_summary.json                coverage + delay statistics
experiments/<stem>/run_meta.json                           version, commit, config, sources
```

`measurement_summary.json` answers the brief's list directly: total windows, valid windows,
invalid windows, valid fraction, delay min / max / median / p95, the delay span, the reason
histogram, the longest invalid run in windows and seconds, and a crude monotonicity count
(sign changes of the increment).

Nothing is deleted. An invalid window is a record with `valid=false` and a `reason`, because
"why can this section not be measured?" is a result.

## 3. Results

C1305, reference CH3, 478 windows × 3 targets, **3 988 s**, completed without error.

| pair | windows | valid | valid % | invalid reasons | delay median | span |
| --- | --- | --- | --- | --- | --- | --- |
| ch3→ch1 | 478 | 93 | 19.5 % | `interp_vertex_at_edge` 266, `narrowband` 81, `intra_window_unstable` 38 | −23.774 ms | **11.149 ms** |
| ch3→ch2 | 478 | 102 | 21.3 % | `interp_vertex_at_edge` 266, `narrowband` 77, `intra_window_unstable` 33 | −23.869 ms | **1.376 ms** |
| ch3→ch4 | 478 | 247 | 51.7 % | `interp_vertex_at_edge` 194, `intra_window_unstable` 28, `narrowband` 9 | +6.075 ms | **24.744 ms** |

**C1306 was not measured.** The single run answers the correction question for this material
(see `correction.md` §6), and a second run would not change that answer.

Two things in this table matter more than the ranges:

1. **Validity is 19.5–51.7 %.** A majority of the timeline is not measurable by this
   criterion, and the dominant rejection is `interp_vertex_at_edge` (266/478 on two pairs) —
   which means the correlation surface carries more than one comparable arrival and the delay
   is **not a well-defined scalar** in those windows. P2 independently found 90–99 % of
   windows ambiguous on every moving pair; P3 finds 48–80 % on this one. The two agree in
   kind, at different resolutions.
2. **`ch3→ch2` moves 1.376 ms while the same windows measure 18.771 ms of movement on a
   different geometry** (stage 6). The 30 s window is averaging away most of the motion, and
   that single comparison is what decides the correction verdict.

## 4. What the window length costs

A 30 s window reports the delay of no instant when the delay moves. With 3× overlap the
trajectory still localises a change to within ~10 s, but the *value* attached to a window is
an average over its 30 s. Three consequences, stated so they are not rediscovered:

1. **A ramp inside a window biases that window toward its own average.** The bias is bounded
   by half the motion across the window, and the motion across 30 s is a measurable
   quantity here (see the sub-window probe's span), so the bias is reportable rather than
   assumed small.
2. **A trajectory fitted to Layer B inherits that smoothing.** Any correction built from it
   cannot be finer than the window.
3. **The verification grid must therefore be finer than the plan's grid**, or the
   verification is measuring the plan against itself. That is the P2 failure mode, and
   `correct.py` enforces a different geometry for verification by construction.
