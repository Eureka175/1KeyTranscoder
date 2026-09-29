# P3 — Trajectory Modelling

> Stage 5 of Audio Sync P3. Source: `work/p3/trajectory.py`; output
> `work/p3/experiments/<stem>/trajectory.json`.
>
> **Status: implemented, results pending the Stage 4 runs.** The method and its guards are
> fixed here; the numbers are filled in from the completed runs and are not anticipated.

## 1. The three layers

```text
Layer A   raw measurements       every window, valid or not     `raw_measurements_*.jsonl`
Layer B   validated measurements Layer A minus refused windows  the only fit input
Layer C   fitted trajectory      a model + parameters + residual + cost
```

`trajectory.py` reports `layer_a_windows`, `layer_b_windows` and `layer_b_fraction` for every
pair, so how much of the timeline the trajectory is actually built from is a visible number
rather than an assumption. A trajectory fitted to 40 % of a recording is a different object
from one fitted to 99 %, and the file says which it is.

## 2. Candidate models

| Model | Parameters | Notes |
| --- | --- | --- |
| `constant` | 1 | the null hypothesis. Any model that cannot beat it is not a trajectory |
| `poly1`, `poly2`, `poly3` | 2, 3, 4 | fitted on a centred and scaled time axis: an unscaled 4800 s axis with degree 3 is close to singular in float64 |
| `piecewise_constant` | n_seg (+ boundaries) | median per equal-count segment |
| `piecewise_linear` | 2·n_seg | continuous, knots plus per-segment slope |

Segments are **equal-count**, not equal-time: on a recording where validity varies, equal
time would give some segments almost no data and let them be fitted by two points.

## 3. Selection: not R²

R² rewards parameters and says nothing about whether the residual is above the noise. Every
model is reported with:

| Metric | Why |
| --- | --- |
| residual RMS (samples and ms) | the scale of what is left |
| residual P95 | robust to a few bad windows |
| residual max | what a worst-case window would cost |
| residual mean (signed) | a systematic bias is different from scatter |
| AIC, BIC | penalised fit, BIC used for selection |
| parameter count | reported so complexity is visible, never hidden |
| stability | the model refitted on each half; the predictions are compared at the shared midpoint |

### The guard against fitting noise

A model is **not eligible to win** unless its residual RMS exceeds the measurement floor
(one sample at this stage). Below the floor the comparison between models is meaningless —
anything can fit a straight line through five points — so those models are excluded from
selection. The floor used is a *lower bound*: the honest per-window uncertainty is larger,
and it is Stage 2's open item.

### Stability

`midpoint_gap` (the two half-fits disagreeing at their shared midpoint) is **reported, not
gated**. The corpus is non-stationary by construction, so a large gap is information about
the material rather than a defect in the model. Gating on it would be assuming the answer.

## 4. What the trajectory must be able to answer

The brief's audit questions, and where each is answered:

| Question | Where |
| --- | --- |
| why correct? | `selection.improvement_ratio` against `constant`, plus the residual table |
| when? | segment boundaries are time positions in the plan, not indices into a fit |
| by how much? | segment values, in samples, converted to ms |
| on what evidence? | Layer B fraction, per-window validity, probe span |
| which windows were refused? | `invalid_reasons` histogram and the gaps in the plan |
| which model produced it? | `selection.selected_kind` / `selected_n_seg` |

## 5. Open, and stated as open

1. **The trajectory cannot be finer than the window.** A 30 s window averages motion inside
   it; the fit inherits that smoothing. The plan built from it cannot recover it either.
2. **No per-window uncertainty is used as a weight.** Every Layer B window counts equally,
   so a marginally-valid window influences the fit as much as a strong one. Weighting by a
   calibrated σ is the natural next step and is blocked on Stage 2's open item.
3. **Model class is not established.** Selecting the lowest BIC is not the same as
   establishing a class, and P2 already recorded that distinction. The result here will be a
   *best model on this material*, reported with its residual, not a claim about what the
   movement physically is.
