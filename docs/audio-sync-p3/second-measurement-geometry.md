# Second Measurement Geometry

**Phase: P3.5. Status: `PARTIAL`. Both candidates for Geometry B failed the brief's §76 hard
gate. No second geometry was established, so the disputed windows remain unresolved.**

---

## 1. What P3.5 needed

P3.4 left a specific question: **33–41 % of refused estimates disagree with the accepted
windows that bound their gap.** Some fraction is genuine in-gap movement, some is the
estimator losing the arrival. Separating them needs a structurally different measurement of
the same audio, which is what Geometry B was for, with two hard gates before any corpus
conclusion: **§76** — does B add information at all; **§77** — can it separate estimator
failure from genuine movement on known truth.

## 2. Candidate B1 — the reciprocal: DEGENERATE

The first candidate was to exchange the reference and target roles and measure CH3 relative
to CH4, expecting `B(t) ≈ −A(t)`.

It returned **`|A + B| = 0.000` samples on all 80 audited real windows and on every synthetic
case**, to machine precision. That is not agreement, it is an algebraic identity: two windows
of equal length carrying the same symmetric Hann taper give a correlation that is **even
about the peak**, so exchanging the roles can only mirror it.

A measurement forced to agree cannot detect that A lost the arrival — the one thing §76
requires. **B1 fails the gate**, and it fails it for a structural reason that no amount of
data would have changed.

## 3. Candidate B2 — time-shifted placement: SIGN UNRESOLVED

The second candidate reads the target `shift = 60 000` samples (1.25 s) earlier, so the taper
rides asymmetrically on the signal and the reciprocity symmetry is broken. What was measured:

| synthetic case | true delay | `B_raw − A` |
| --- | ---: | ---: |
| T0 constant 1140 | 1140.00 | **0.000** |
| T1 ramp 0→120 | 50.00 | **−0.41** |
| T4 fast ramp 0→900 | 371.38 | **−3.08** |

**`B_raw` already tracks the delay.** The difference from A is 0.000 on a constant and grows
with the movement across the shift (−0.41 samples for a 120-sample ramp, −3.08 for a 900-sample
ramp) — which is the movement over 1.25 s, exactly what two windows 1.25 s apart should
disagree by if both are correct.

> **B2 therefore carries essentially no information beyond A.** It is A re-measured 1.25 s
> away, on audio that shares 96 % of its support. It cannot say whether A lost an arrival,
> because it fails in the same windows for the same reasons.

**B2 fails §76 as well.**

### An unresolved defect I am reporting rather than hiding

The in-script conversion produced values inconsistent with the fresh probe: the probe measured
`B_raw = 1140.00` for T0 where the script reported `B_delay = 61140.00` — a factor of −1
apart. I changed the sign constant twice and cleared a stale `__pycache__`, and the two paths
still disagreed on the same computation, so **I stopped rather than keep flipping a sign until
an output looked right.** That is the same discipline P3.1 applied to its own mirrored
conventions, and it is the reason this phase is `PARTIAL` rather than `COMPLETE`.

The defect does not affect the §3 conclusion: `B_raw − A ≈ 0` is measured directly on raw
values and needs no conversion. It affects only the display of B on A's axis, and it is
recorded here so that a later phase does not inherit a silently wrong offset.

## 4. Why both candidates failed, in one sentence

> A second geometry can only be independent if it observes the disputed quantity with
> **different temporal support**. Both candidates keep the frozen 30 s window and the frozen
> ±3000 search, so they observe the same correlation peak over nearly the same audio: one is
> algebraically forced to agree, the other differs only by the time offset.

Genuine independence requires changing something the freeze fixes — a different window length,
a non-overlapping measurement, or a different band — and that is a **configuration change**,
which the freeze forbids without a new phase. The library does contain one ready-made
alternative (`_probe_subwindows` measures five 15 s sub-windows), but at 15 s the peak is
twice as wide and its reliability has never been characterised.

## 5. What P3.5 does and does not change

| | |
| --- | --- |
| P3.4's `unobserved-gap / invalid-region policy = CONFIRMED` | **unchanged**; not refined, not weakened |
| P3.4's `staircase = REJECTED` | **unchanged** |
| P3.4's quality tiering | **unchanged** and unaffected |
| the 33–41 % disagreement | **still unresolved** |
| a second measurement geometry | **NOT ESTABLISHED** |

P3.4's report is not modified. Nothing about the gap mechanism was invalidated; what failed is
the instrument P3.5 hoped to build, not the finding it was meant to test.

## 6. Limitations

1. **No second geometry exists at the frozen configuration.** Establishing one requires
   either a window-length change (config, needs a new phase) or accepting the 15 s probe
   sub-windows whose reliability is uncharacterised.
2. **The B2 axis sign is unresolved** (§3).
3. **Only 40 windows per case were audited** (stratified: 8 large, 8 medium, 8 small, 16
   random, fixed seed `20261010`, selected before any B value was computed). The strata are
   recorded in `work/p3/p35/{c1305,c1306}/disputes_*.json`, so no window was chosen after
   seeing its result.
4. **The disputed-window conclusion rests on A alone**, which is precisely the thing under
   question. P3.5 did not improve on P3.4 here.

## 7. Provenance

```text
branch    research/audio-sync-p3-second-geometry
script    work/p3/p35/second_geometry.py
outputs   work/p3/p35/synthetic/geometry_validation.json
          work/p3/p35/{c1305,c1306}/disputes_*.json
          work/p3/p35/disputes/summary.json
frozen    work/p3/p32/validation_manifest.json -- read-only, unmodified
history   P3.4 report, P3.3 report, P3.2 conclusions -- unmodified
core/     untouched        P1  27 PASS / 0 FAIL
```
