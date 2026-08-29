# 1KeyTranscoder

Recursive, resumable Windows batch encoder with Sony camera-metadata
preservation. Only **x265** is implemented; NVENC/QSV/VCE are future
backends behind `encoders/base.py`.

**Main entry point: `1kt.py`** (replaces `x265_archive.py`, which
remains temporarily as migration reference only).

## Layout

```
1kt.py            MAIN: CLI, batch loop, resume, exec, postprobe
x265_archive.py          legacy entry point (reference only, do not extend)
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
  base.py                EncoderBackend protocol + pixel-format mapping
  x265.py                PARAM_MAP, x265 value formatting, FFmpeg command
                         (full output + video-only preservation intermediate)
preservation/
  pipeline.py            Sony pipeline: extract -> rebuild -> validate
  sony.py                SonyPreservationBackend (rtmd/nrtm/uuid bundle)
  gpac.py                GPAC/MP4Box subprocess wrapper (container backend)
  isobmf.py              ISO-BMFF box walker / uuid byte patcher /
                         tkhd+elst duration repair
  validate.py            ORIGINAL vs FINAL structural comparison
  gyroflow.py            Gyroflow headless consumer validation
  poc_video.py           POC video/audio backends (reference)
```

## Sony metadata preservation

Sony XAVC sources (detected by the `rtmd` data stream) automatically go
through the preservation pipeline instead of a plain FFmpeg remux:

```
source -> probe + scaling (core/, unchanged)
       -> GPAC demux (bundle: metadata/manifest.json, tracks/, boxes/)
       -> FFmpeg libx265 (scaled production params) -> video/encoded.mov
       -> MP4Box -new: video + per-track audio container copy + per-track
          rtmd container copy (native ISOBMFF copies, exact source timing)
       -> payload verification + tref/cdsc + nrtm meta + brands
       -> uuid byte patch (GPAC cannot write vendor uuid boxes)
       -> <basename>.MP4 (uppercase extension, XAVC brand) + report.json
       -> Gyroflow headless validation (auto-detected, or --gyroflow)
```

Preserved: rtmd timed KLV track (verbatim samples, exact 1001/60000
timing), tref/cdsc, file-level `nrtm` meta (Lens profile item when
present + NonRealTimeMeta XML), PROF/USMT and any other vendor uuid
boxes, track timescales and presentation durations.

Container rules discovered during bring-up (do not regress):

- The video intermediate for muxing is **MOV, not MKV**: GPAC's MKV
  reader quantizes timestamps to milliseconds, breaking exact
  1001/60000 rtmd alignment.
- The movie timescale must equal the **source mvhd timescale** (A7M5
  60000, A7M4 90000 — not necessarily the video track timescale) and
  must be passed to **every** MP4Box rewrite (`gpac.movie_timescale`),
  or GPAC resets it to the first track's media timescale.
- **Reconstruction never uses NHML.** GPAC 26.02 recomputes every
  track's tkhd/elst durations at 600-tick precision whenever an
  NHML-imported track is present (360360@60000 → 360300), regardless
  of `-timescale` / `:moovts` / `:timescale`; the rtmd track is
  instead container-copied from the source (`-add src#<trackID>`),
  which keeps the exact source timing through the whole chain
  (`-ref`/`-set-meta`/`-add-item`/`-set-xml`/`-flat`/`-brand`).
- `#audio` in MP4Box copies only the FIRST audio track; the pipeline
  adds every source audio track by its track ID (4× mono stays 4× mono).
- `isobmf.patch_track_durations()` remains available as a Level-3
  fallback for other GPAC versions; it is not part of the normal
  pipeline anymore.

A run fails (output not delivered) if structural validation reports
critical MISSING/MODIFIED items, or if Gyroflow cannot parse the same
metadata from the output as from the original.

Requires GPAC at `C:\Program Files\GPAC` (or `--gpac-dir`).

sony_poc.py is the original standalone POC and is kept for reference;
it is not a second main program.

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
python 1kt.py --input <dir> --output <dir> [--preset uhq|hq|small|fast|all]
                     [--config x265.json] [--scaling-config x265_scaling.json]
                     [--ffmpeg ...] [--ffprobe ...]
                     [--gpac-dir "C:\Program Files\GPAC"] [--gyroflow ...]
                     [--dry-run]
```

Sony intermediates (bundle, encoded.mov, stage/final, report.json) live
under `<output>\.1ktwork\<job-id>\` for inspection and resume.

Dry-run (no encoding, full audit trail):

```
python 1kt.py --input testsets --output out_dry --preset all --dry-run
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

