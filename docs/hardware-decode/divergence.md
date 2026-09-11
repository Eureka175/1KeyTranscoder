# 7. First-Divergence Analysis

> Why this file exists: `software = 3600, hardware = 3599` proves only
> that two integers differ. It does **not** prove which frame moved, or
> whether the frame moved at all. Every statement here is derived from
> per-frame fingerprints, never from counts.
>
> Tooling: `work/hwdecode/fingerprint.py` (fingerprints),
> `work/hwdecode/compare.py` (first-divergence + classification),
> `work/hwdecode/rigaya_divergence.py` (rigaya-specific divergence).
> Raw data: `work/hwdecode/raw/div-C1170.json`,
> `work/hwdecode/raw/exp-container-*.json`,
> `docs/hardware-decode/test-results/comparison.json`.

## 7.1 Fingerprint method

Each decoder path emits one canonical stream:

```
decoded frame -> [hwdownload -> format=p010le]   (hardware paths only)
              -> scale=64:64:flags=bilinear
              -> format=gray
              -> showinfo            (index, pts, pict_type, iskey, adler32)
              -> rawvideo to stdout  (4096 bytes/frame -> sha1)
```

Two tiers are recorded per frame:

| Tier | Value | Use |
|---|---|---|
| exact | `sha1` of the 4096-byte 64×64 gray plane | comparisons **within** one pipeline |
| tolerant | `sig8`: 8×8 block means, 128 hex chars | comparisons **across** pipelines |

The exact tier is byte-exact and therefore only valid when both sides
share the same colour conversion. Across a conversion boundary
(rigaya's `yv12(10bit) → p010 → yv12(10bit)` round-trip) the same picture
shifts by ±1 luma code and the hash flips. Measured: 25/30 frames equal
exactly, **30/30 within 2 codes**. Using only the exact tier there would
have produced a spectacular and completely false "every frame differs"
finding; the tolerant tier is what makes cross-pipeline claims safe.

Cost: 4 KiB/frame instead of 8–12 MiB/frame, so all 151 files × 4 modes
were fingerprinted end to end rather than sampled.

## 7.2 Level 2 (FFmpeg QSV / NVDEC) vs Level 1 (software)

**Result: no first divergence exists.** Every Sony HEVC file that decoded
successfully produced a **bit-identical picture sequence** and an
identical PTS sequence to software decode. `sequence_identical = true`,
`missing_total = 0`, `extra_total = 0`, `duplicated_pictures = 0`,
`pts_divergence = null`.

Representative windows — there is nothing to show, which is the point:

```
file                         software  QSV   NVDEC  first divergence
20260903_C1169.MP4 (10170 f)    10170  10170  10170  none
晴天室外…多场景亮暗切换.MP4 (11280 f)  11280  11280  n/a   none
20260823_C0886.MP4 (360 f)        360    360    360  none
C9037.MP4 (195 f, H.264 422)      195      0*   195  none (QSV has no decoder)
```

\* QSV refuses H.264 High 4:2:2 10-bit outright — a capability failure,
not a silent loss (see §7.4).

## 7.3 Level 3 (rigaya `--avhw`) — the divergence that does exist

The only real divergence found in this investigation.

### Sample: `testsests/20260903/A7M5/20260903_C1170.MP4` (30 frames)

Both readers driven by the *same* tool through the *same* output path, so
the comparison is byte-exact and the only variable is the reader.

```
software / --avsw (30 frames)        --avhw (27 frames)
index  fingerprint                   index  fingerprint
  0    bafd6bcfe2e40fc7                0    034377538fc88b1f
  1    9ee0c5717e649507                1    f33b1562f5727a00
  2    f847344579c2fd3f                2    506191e416ffb23e
  3    034377538fc88b1f                3    6d67686f2c4c618a
  4    f33b1562f5727a00                4    953f6f612387850c
  5    506191e416ffb23e                5    0d432bf6d9e9f57a
```

Interpretation, in the exact terms the investigation requires:

| Question | Answer | Basis |
|---|---|---|
| Did a frame disappear? | **Yes**, exactly 3 | `in_avsw_not_avhw` = 3 fingerprints; `in_avhw_not_avsw` = ∅ |
| Did a frame shift earlier? | No | there is no reordering |
| Does the missing frame appear later? | **No** | `avsw[0]` is not found at any index of `avhw` |
| Is it only a PTS offset? | **No** | picture content differs, not just timestamps |
| Decode order vs display order? | No | both streams are in presentation order |
| Delayed frames not drained? | **No** | a drain defect truncates the *tail*; this truncates the *head* |
| Which frames? | **the first three presented pictures** | `avhw[i] == avsw[i+3]` for all i |

### Divergence table (rigaya, both tools)

| file | container | software | QSV `--avhw` | NVDEC `--avhw` | first divergence | type |
|---|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 30 | 27 | 27 | **index 0** | `FRAME_DROP` (head, 3) |
| `20260904_C1193.MP4` | 90 | 90 | 87 | 87 | index 0 | `FRAME_DROP` (head, 3) |
| `20260904_C1196.MP4` | 120 | 120 | 117 | 117 | index 0 | `FRAME_DROP` (head, 3) |
| `20260903_C1158.MP4` | 150 | 150 | 147 | 147 | index 0 | `FRAME_DROP` (head, 3) |
| `20260823_C0886.MP4` | 360 | 360 | 357 | 357 | index 0 | `FRAME_DROP` (head, 3) |
| `C9037.MP4` (H.264) | 195 | 195 | 193 | 193 | index 0 | `FRAME_DROP` (head, 2) |

The full-corpus rigaya run is in
`docs/hardware-decode/test-results/comparison.json → rigaya_reader`.

## 7.4 Non-divergence findings that must not be mislabelled

These were investigated and are **not** frame-sequence defects:

| Observation | Correct classification | Not |
|---|---|---|
| QSV returns `rc=69`, 0 frames, on the 5 H.264 High 4:2:2 10-bit files | `DECODER_ERROR` — capability (`Error querying IO surface: unsupported (-3)`) | `FRAME_DROP` |
| FFmpeg software decode logs `edit list: N Missing key frame while searching for timestamp` | demuxer warning, `Unconfirmed` consequence | proven frame loss |
| `--avhw` output is a strict prefix of `--avsw` (never a superset) | `FRAME_DROP` | `FRAME_DUPLICATE` |
| 4:2:2 outputs lose chroma resolution when a hardware encoder converts to 4:2:0 | format planning, not frame integrity | frame-count defect |
| AV1 production deliverables carry `elst media_time = 0` while HEVC ones keep `2002` | presentation-timing difference in the muxer | frame-count defect |

## 7.5 Divergence windows for the corpus

Because Level 2 produced **zero** divergences, `comparison.json` contains
an empty `first_divergence` window for every Sony file — the classifier
records `PASS` before computing one. Divergence windows therefore exist
only for the rigaya path, and are reproduced in full in §7.3 and in
`work/hwdecode/raw/div-C1170.json` (complete fingerprint arrays for all
three streams, plus per-frame PTS and picture type for the software
decode).
