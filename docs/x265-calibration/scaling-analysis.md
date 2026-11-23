# Scaling Analysis — Observed Rule Behaviour Against Source Properties

**Observational only.** This dataset varies **no** parameter rule. Every dynamic
value is a deterministic function of source metadata (see
[`parameter-analysis.md`](parameter-analysis.md) §4). Therefore:

> **Correlation is not causation here, and parameter *effects* cannot be measured
> from this dataset at all.** What follows describes *how the rules behaved*, not
> how parameters affect quality or speed.

---

## 1. The complete rule set as actually executed

| Rule | Formula | Cap/clamp | Verification |
|---|---|---|---|
| `merange` | `clamp(half_up(57 · √(pixels / (3840·2160))), 16, 92)` | 16–92 | 1839/1839 |
| `rc-lookahead` | `min(ceil(fps × 1.5), 200)` | 200 | 1839/1839 |
| `gop-lookahead` | `min(ceil(fps × 0.1), 200)` | 200 | 1839/1839 |
| `min-keyint` | `ceil(fps × 0.15)` | none | 1839/1839 |
| `vbv-maxrate` | `half_up(clamp(OB × target_ratio, OB × min_ratio, OB × max_ratio))` | by class | 1839/1839 |
| `vbv-bufsize` | `min(half_up(maxrate × 3.0), 240000)` | 240 000 kbit/s | 1839/1839 |
| `source_class` | threshold rule on `normalized_ob` | low < 0.12 ≤ normal < 0.25 ≤ high | 1839/1839 |
| `keyint`, `bframes`, `ref`, `crf` | constant (`600`, `6`, `4`, `21`) | — | — |

`normalized_ob` was recomputed from raw metadata with **0 mismatches** against the
recorded value.

### Which clamps actually fired

| Mechanism | Fired |
|---|---|
| `vbv-bufsize` CPB clamp (240 000) | **353 / 1638** (21.5 %) |
| `vbv-maxrate` min/max ratio clamp | **never** — the target branch always won |
| `rc-lookahead` cap (200) | only at high fps |
| `merange` clamps (16 / 92) | observed values 17 and 57 at the low end; 92 at the high end for 4K |

Per-class VBV targets (from `effective_params_audit`):

| `source_class` | `target_ratio` | `min_ratio` | `max_ratio` |
|---|---:|---:|---:|
| `HIGH_BITRATE_LONG_GOP` | 0.50 | 0.35 | 0.65 |
| `NORMAL_LONG_GOP` | 0.75 | 0.60 | 0.90 |
| `LOW_BITRATE_LONG_GOP` | 0.75 | 0.60 | 0.90 |

---

## 2. Provenance context

| Setting | Value |
|---|---|
| Encoder | `libx265 4.3+6-9ddc216` via `ffmpeg 9.0.1-full_build-www.gyan.dev` |
| Preset | `HQ` ×1839 |
| Backend | `x265` software ×1839 |
| `config_hash` | `0c522f47316bf7…` (constant; equals on-disk `x265.json`) |
| `scaling_config_hash` | `0cdc44cfa3ec4d…` (constant; equals on-disk `x265_scaling.json`) |
| Host | `DESKTOP-LG5H5AR` ×1839 |
| Jobs | `JOBS=1`, scan interval 60 s (`RUN-TEST.bat`) |

Because both hashes are constant across the entire window, **the rule set never
changed** — there was no mid-window config drift, which is the precondition the
collection README required.

---

## 3. Coverage of the rule input space

This is the part that matters for a future calibration: which regions of the input
space did the rules actually visit?

| Dimension | Coverage | Note |
|---|---|---|
| Resolution | 3840×2160 (majority), 1920×1080 | plus isolated odd geometries (e.g. 3840×6826) |
| fps | 23.976 / 25 / 29.97 / 30 / 50 / 59.94 / 119.88 | `rc-lookahead` visited 7 distinct values, `gop-lookahead` 4, `min-keyint` 6 |
| `merange` | 4 values (57, 29, 40, 17) | i.e. only **4 distinct pixel-rate buckets** were exercised |
| `vbv-maxrate` | 1494 distinct values | well covered — a near-continuum |
| `vbv-bufsize` | 1149 distinct, clamp binding 21.5 % | clamped region is well represented |
| `source_class` | `HIGH_BITRATE_LONG_GOP`, `NORMAL_LONG_GOP`, `LOW_BITRATE_LONG_GOP` | all three exercised |
| chroma | 4:2:0 (successes) and **4:2:2 (142 records, 100 % failure)** | **zero successful 4:2:2 encodings** |
| bit depth | 8 and 10 | — |
| CRF | 21 only | no CRF sweep |
| preset | `HQ` only | no preset comparison |

### The dominant confound

`merange`, resolution and pixel rate are **algebraically linked**
(`ρ(merange, pixel_rate) = 0.9996`). Likewise `rc-lookahead`, `gop-lookahead`,
`min-keyint` all track `fps` (`ρ = 0.999999`), and `vbv-maxrate` tracks `OB`
(`ρ = 0.952`) with `vbv-bufsize` an exact function of `vbv-maxrate`.

> The strongest performance correlation in the corpus — `encode_fps` vs `merange`
> (R² ≈ 0.84) — is also its **most confounded model**. The `merange` coefficient
> is a *resolution* coefficient wearing a parameter's name. It must not be
> reported as a `merange` effect.

---

## 4. Verification-gate behaviour (why 633 successes have no ratio)

The verification gate was **not** duration-gated and **not** governed by the
`quality_check` block in `x265.json`. It correlates almost perfectly with the
source **file extension**:

| Extension | `passed` | `skipped` | `not_run` (failures) |
|---|---:|---:|---:|
| `.mov` | **807** | 0 | 0 |
| `.mp4` | 191 | **633** | 208 |

Cramér's V = 0.788, χ² = 1013.1, p ≈ 2.5e-222, n = 1631.

Consequences:

* **Unsafe:** treating the n=998 `encode_fps` / `compression_ratio` population as
  representative of the whole corpus; extrapolating it to overall throughput;
  comparing `.mov` with `.mp4` families.
* **Safe:** all n=1631 `wall_time_sec` / `output_size` / output-bitrate analysis;
  all n=1839 rule and failure analysis; within-extension results.

Two candidate explanations were tested and **falsified**: a duration ≤ 60 s gate
(18 `passed` records exceed 60 s, max 216.4 s) and a hash-based sampling rule (no
hash rule exceeds chance). The residual within-`.mp4` rule is **not identified**
and is reported as unexplained rather than guessed.

---

## 5. What could be calibrated next — nothing was changed

No candidate below has been run, and **no production configuration may be changed
as part of this study**. They are recorded so the next controlled phase has a
starting point.

| ID | Hypothesis | Parameter varied | Control | Metric | Min n | Held fixed | Confound to avoid |
|---|---|---|---:|---|---:|---|---|
| P1 | the `merange` rule trajectory is conservative for high-pixel-rate 4K | `merange`: rule value vs 0.5× | rule value | `encode_fps`, PSNR/SSIM | 32 paired | resolution, fps, duration, CRF 21, preset | **must use the same clip in both arms** — `merange` derives from pixel count |
| P2 | the VBV target ratio is mis-set (the ratio clamps never fired) | `target_ratio` 0.35 / 0.50 / 0.65 | 0.50 | bitrate, size, PSNR/SSIM | 30 | resolution, fps, CRF | stratify by `OB` quartile (69 969 / 93 189 / 132 363 kbps) |
| P3 | the 240 000 CPB clamp costs quality on very-high-rate sources | CPB cap vs uncapped `3 × maxrate` | capped | size, PSNR/SSIM | 24 paired | `vbv-maxrate`, CRF | select sources where `3×round(OB·target) > 240000` |
| P4 | throughput determinants beyond `OB` | none (measurement study) | — | `encode_fps` with a **newly captured** complexity proxy (per-frame QP) | 30 | one duration band, 4K/29.97 | `OB` collinearity; needs data the history lacks |
| P5 | the Sony 4:2:2 path is unvalidated (**0 successes in the whole corpus**) | none (regression test) | matched 4:2:0 | encode completes, output valid | 10 | resolution | **blocked until `FAIL-ORCH-001` is fixed** |
| P6 | the verification gate's coverage is a data-collection gap | none (measurement) | — | ratio/metric capture rate | ≥60 | stratified by duration bucket | must not inherit file order |

P5 is the strongest candidate: the corpus has **no successful 4:2:2 encode** and
**no `INTRA_LIKE` class** at all, so two intended cells of the calibration matrix
are empty.

---

## 6. Explicit non-findings

This dataset **cannot** support:

* **any parameter A/B** — the rules were never varied;
* **any quality analysis** — no PSNR / SSIM / VMAF / file-size-vs-quality metric
  exists in any of the 1839 records (field-scanned);
* **encoder or hardware comparison** — one encoder, one host, one preset;
* **generalisation** — a single corpus (`校团宣`), one CRF, one preset;
* **a thermal/ordering claim** — within-day first-half vs second-half comparison
  is 8 slower / 7 faster (a coin flip over 15 comparisons), and no temperature
  data exists anywhere;
* **within-source repeatability** — n = 0 (no source was encoded twice under
  identical rules with both outcomes recorded).
