# x265 Archive Encoder

Recursive, resumable Windows batch encoder. Only **x265** is implemented;
NVENC/QSV/VCE are future backends behind `encoders/base.py`.

## Layout

```
x265_archive.py          orchestration (CLI, batch loop, resume, exec, postprobe)
x265.json                base 4K60 profiles: UHQ / HQ / SMALL / FAST (authoritative)
x265_scaling.json        all source-dependent scaling rules (PROVISIONAL values)
core/
  models.py              SourceInfo, SourceClassification, ScalingContext, EffectiveParams
  probe.py               probe_source() (metadata-only, unchanged) + SourceInfo adapter
  source_classifier.py   metadata-only source-efficiency classification
  scaling.py             ScalingEngine + ceil_expression (no encoder syntax here)
  config.py              explicit config loading, executable discovery
  logging_utils.py       loggers + CSV field lists / row builders
encoders/
  base.py                EncoderBackend protocol
  x265.py                PARAM_MAP, x265 value formatting, FFmpeg command
```

## Configuration flow

```
x265.json (base profile) ─┐
                          ├─> ScalingEngine ─> EffectiveParams ─> X265Backend ─> ffmpeg
ffprobe ─> SourceInfo ─> SourceClassifier ─> ScalingContext ─┘
                     x265_scaling.json (rules)
```

- `x265.json` is the authoritative base; its numerical values are unchanged.
- `x265_scaling.json` holds every scaling number: reference geometry,
  classification thresholds, per-parameter rules, dynamic VBV ratios.
- Scaling can be disabled entirely with `"enabled": false` (legacy static
  behavior: FPS-evaluated lookaheads with 200-frame cap, static VBV).

## Scaling model

Reference: 3840×2160 @ 59.94 fps.

- `spatial_factor    = sqrt((W·H) / (3840·2160))`
- `temporal_factor   = fps / 59.94`
- `pixel_rate_factor = (W·H·fps) / (3840·2160·59.94)`

Parameter rules (`param_rules`) select a mode per base-profile key;
everything not listed stays **fixed** (crf, rd, psy-*, aq-*, etc. are
never scaled):

| mode         | meaning                                             |
|--------------|-----------------------------------------------------|
| `fixed`      | keep base value (default)                           |
| `fps`        | evaluate `FR*` against source fps, optional `cap`   |
| `sqrt_pixels`| `base * spatial_factor`, optional `min`/`max` clamp |
| `pixel_rate` | `base * pixel_rate_factor`, optional `min`/`max`    |

Current non-fixed rules: `rc_lookahead` (fps, cap 200), `gop_lookahead`
(fps, cap 200), `min_keyint` (fps, uncapped), `merange` (sqrt_pixels,
clamped 16–92). The LA cap exists to bound x265 memory on high-FPS
sources (target machine: 32 GB RAM).

## Source classification (metadata-only)

`normalized_ob = OB_bits/sec / (width · height · fps)` (bits per
pixel-frame) — an efficiency indicator, **not** GOP detection.

- `< low_max (0.12)` → `LOW_BITRATE_LONG_GOP`
- `>= high_min (0.25)` → `HIGH_BITRATE_LONG_GOP`
- between → `NORMAL_LONG_GOP`
- codec in `intra_like_codecs` → `INTRA_LIKE` (future class; H.264/HEVC
  never map here yet)

## Dynamic VBV

Per profile × per source class: `min_ratio / target_ratio / max_ratio`
(+ `bufsize_factor`, default 3.0):

```
final_maxrate = clamp(OB·target_ratio, OB·min_ratio, OB·max_ratio)  [kbps]
bufsize       = round(final_maxrate · bufsize_factor)               [kbps]
```

Semantics: **CRF is the primary quality control; `vbv-maxrate` is a
local bitrate ceiling; `vbv-bufsize` is the VBV buffer constraint.**
maxrate is not an average bitrate and alone does not guarantee an
output size. Missing class rule → static base-profile VBV (logged).

## Usage

```
python x265_archive.py --input <dir> --output <dir> [--preset uhq|hq|small|fast|all]
                       [--config x265.json] [--scaling-config x265_scaling.json]
                       [--ffmpeg ...] [--ffprobe ...] [--dry-run]
```

Dry-run (no encoding, full audit trail):

```
python x265_archive.py --input testsets --output out_dry --preset all --dry-run
```

Per file this logs SOURCE / CLASSIFICATION / SCALING / SCALED_PARAM
(base → calculated → cap/clamp → final) / COMMAND / EFFECTIVE_X265, and
appends to `logs\scaling.csv` beside the pre-existing
preprobe/postprobe CSVs (whose schemas are unchanged).

## Provisional values (calibration candidates, NOT final)

- `classification.thresholds` (0.12 / 0.25)
- every `dynamic_vbv` ratio and `bufsize_factor`
- `param_rules.merange` clamp (16–92)
- LA caps (200) are production-intent but configurable
- `INTRA_LIKE` ratios are placeholders; no genuine All-I source exists yet
