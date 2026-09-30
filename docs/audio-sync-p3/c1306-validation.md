# C1306 Validation

**Phase: P3.2 Stage 4. Status: `C1306 = PASS` for the primary pair.**

---

## 1. The role split

```text
C1305  ->  calibrate   (the criterion's thresholds were fixed on it)
C1306  ->  validate    (the criterion is applied unchanged; nothing is re-tuned)
```

The brief's §6 forbids the fail-then-adjust loop, and a held-out corpus is worthless the
moment it is used to choose a parameter. Every threshold in
`work/p3/p32/set_validity.py` was frozen on C1305 and applied to C1306 exactly as it stood.

## 2. Measurement

C1306 had never been measured in any phase. This is its first pass.

| | C1306 | C1305 |
| --- | ---: | ---: |
| samples | 199 903 704 | 230 750 520 |
| duration | 4164.7 s | 4807.3 s |
| windows | 414 | 478 |
| reference | CH3 (stream 2) | CH3 (stream 2) |
| window / hop | 30 s / 10 s | 30 s / 10 s |
| search | ±3000 samples | ±3000 samples |
| measurement elapsed | 1596 s | 3988 s |

Raw tracks reused from `work/p2/out/raw/`; no decode, mux or encode was run.

**One configuration error was caught before it produced a result.** The first launch used
`run_long.py`'s default `--search-ms 31.25` (±1500 samples) where C1305 used ±3000. The
search width is not a cosmetic parameter — P3 recorded that it has never been validated for
narrow widths, and the same reason code counts depend on it. The run was killed after 261
windows and relaunched at ±3000, so the two corpora are measured by the same instrument.

## 3. Window-level results

| pair | C1306 accepted | C1305 accepted | C1306 invalid mix |
| --- | ---: | ---: | --- |
| CH3→CH1 | 81/414 (19.6 %) | 93/478 (19.5 %) | `interp_vertex_at_edge` 183, `narrowband` 118, `intra_window_unstable` 32 |
| CH3→CH2 | 81/414 (19.6 %) | 102/478 (21.3 %) | `interp_vertex_at_edge` 184, `narrowband` 114, `intra_window_unstable` 35 |
| CH3→CH4 | **308/414 (74.4 %)** | 247/478 (51.7 %) | `interp_vertex_at_edge` 85, `intra_window_unstable` 15, `narrowband` 5 |

The invalid-reason **mix** matches C1305 closely: `interp_vertex_at_edge` dominates on every
pair, and `narrowband` is the second reason on the two noisy pairs. That is the criterion
behaving identically on material it has never seen, which is a stronger statement than the
verdict alone.

## 4. The frozen verdicts

| pair | C1306 lag1 | C1305 lag1 | C1306 set | C1305 set | agree |
| --- | ---: | ---: | --- | --- | --- |
| CH3→CH1 | **+0.8586** | **+0.0662** | VALID | INVALID | **no** |
| CH3→CH2 | +0.8113 | +0.8046 | VALID | VALID | **yes** |
| CH3→CH4 | **+0.9822** | +0.9305 | VALID | VALID | **yes** |

Accepted spans and step structure:

| pair | C1306 span | C1305 span | C1306 median \|step\| | C1305 median \|step\| |
| --- | ---: | ---: | ---: | ---: |
| CH3→CH1 | 283.0 samples (5.897 ms) | 535.1 (11.149 ms) | 5.99 | 2.7 |
| CH3→CH2 | 288.5 samples (6.009 ms) | 66.1 (1.376 ms) | 5.06 | 3.0 |
| CH3→CH4 | 141.4 samples (2.947 ms) | 1187.7 (24.744 ms) | 1.94 | 1.7 |

## 5. Verdict

```text
CH3→CH4  (primary)  : VALID   -- same as C1305
CH3→CH2             : VALID   -- same as C1305     (lag1 +0.8113 vs +0.8046)
CH3→CH1             : VALID   -- C1305 said INVALID
cross-corpus agreement : 2/3 pairs
C1306 cross-corpus validation : PASS
```

Per the brief's §5, the gate is the primary pair and the overall verdict:

> the frozen criterion reaches the same verdict on C1306 as on C1305 for the primary pair.

## 6. The disagreement, classified rather than waved away

The brief's §6 requires a C1306 failure to be classified before any criterion change is
considered. The one disagreement is CH3→CH1, and it is informative rather than alarming:

* **It is not a criterion failure.** On C1306, CH3→CH1's accepted set has lag-1
  autocorrelation **+0.8586** with a median step of 5.99 samples over 81 windows — a
  coherent, continuous trajectory by every statistic the criterion computes. The criterion
  says VALID because the set *is* valid. Rejecting it would be the error.
* **It is not a measurement failure.** The invalid-reason mix on this pair is nearly
  identical between corpora, so the instrument behaved the same way.
* **It is a corpus property.** The same pair on C1305 produced a *white-noise* accepted set
  (+0.0662) and on C1306 produces a coherent one. CH3→CH1's usability is not a property of
  the criterion; it is a property of the recording.

So the honest statement is not "the criterion generalises on 2 of 3 pairs" but:

> the criterion's **inputs** differ between corpora on CH3→CH1 — the accepted set is
> incoherent on one recording and coherent on the other — and the criterion reports each
> correctly.

This also retires CH3→CH1 as a permanent negative control. It was a natural one on C1305
and is not one on C1306, and P3.1's use of it as a standing example of "individually
plausible, collectively incoherent" should be read as a C1305 observation rather than a
property of the pair.

## 7. What C1306 adds beyond the verdict

**CH3→CH4 is the best-behaved pair on both corpora, and markedly better on C1306:**
74.4 % window-valid against 51.7 %, lag-1 **+0.9822** against +0.9305, and a much tighter
accepted span (2.947 ms against 24.744 ms). The 24.744 ms figure on C1305 was shown in
P3.1 §6.5 to be four marginally-decided windows; C1306, measured independently with the
same instrument, produces a span one eighth the size with a *higher* acceptance rate. That
is corroboration of P3.1's finding from a different recording rather than another argument
about the same one.

## 8. Provenance

```
measurement   work/p3/p32/experiments/20260919_C1306/{run_meta,measurement_summary}.json
              work/p3/p32/experiments/20260919_C1306/raw_measurements_ch3_to_ch{1,2,4}.jsonl
criterion     work/p3/p32/set_validity.py  (frozen; reversal-rate clause RETIRED)
application   work/p3/p32/validate_c1306.py
result        work/p3/p32/c1306/validity.json
calibration   work/p3/p32/c1305/validity.json
elapsed       1596 s measurement, 0 s analysis (0 analyses -- no parameter was chosen)
```

## 9. Limitations

1. **Containment has not been run on C1306.** The 2.5 s-grid measurement for CH3→CH4 was
   launched after this document; the containment result is a separate corroboration and is
   reported separately.
2. **`narrowband` refusals are higher on C1306's two noisy pairs** (114–118 against 77–81 on
   C1305). The soft bandwidth clause is the one threshold P3 moved for real material, and
   its behaviour differs across corpora. This is measured and reported; it is not a reason
   to change it, and under the role split it cannot be.
3. **One corpus pair is only 19.6 % valid on both corpora.** The criterion's effective
   sample size on CH3→CH1 and CH3→CH2 is ~80 windows, so their set-level verdicts rest on
   fewer observations than CH3→CH4's.
