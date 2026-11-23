# `--avhw` silently drops open-GOP leading pictures (Sony XAVC: N−3 for HEVC, N−2 for H.264 4:2:2)

## Summary

With `--avhw`, NVEncC silently delivers fewer frames than the container holds, on
any stream whose first IRAP is **not** first in presentation order. There is no
error, no warning, `rc = 0`, and the output file plays normally.

* Sony XAVC HS (HEVC Main10 4:2:0, LongGOP) → **N − 3**
* Sony XAVC S (H.264 High 4:2:2 10-bit, LongGOP) → **N − 2**
* `--avsw` on the same files is exact, and FFmpeg is exact on the same material.



## Environment

| | |
|---|---|
| NVEncC | `NVEncC (x64) 9.31 (r4047) by rigaya, Aug  8 2026 12:55:12 (VC 1944/Win)` |
| sha256 | `4c043e7b9c572f8ed809b33729db46a48c06e0595c92b0fe43491c805299f595` (161 058 304 bytes) |
| Source revision used for the analysis | tag `9.31`, commit `2cb9d810c045202548b98ff130b12bc764eb39ea` |
| OS / GPU | Windows 11 (26200) · NVIDIA RTX 5070 Laptop 8 GB · driver 616.56 |
| Input material | Sony A7M5, XAVC HS, HEVC Main10 4:2:0, 3840×2160, 60000/1001 fps, LongGOP, ~150 Mbps |

## Reproduction

Minimal — no encoding needed, it just decodes and discards:

```powershell
# clip with 30 container samples and 3 leading pictures
NVEncC64.exe -i 20260903_C1170.MP4 --avhw -c raw --output-res 64x64 -o NUL
# -> encoded 27 frames

NVEncC64.exe -i 20260903_C1170.MP4 --avsw -c raw --output-res 64x64 -o NUL
# -> encoded 30 frames
```

The shortfall scales with the clip, so it is not a startup artefact:

```powershell
NVEncC64.exe -i <330-frame clip>   --avhw --frames 100 -c raw -o NUL          # -> encoded 97 frames
NVEncC64.exe -i <10170-frame clip> --avhw -c raw --output-res 64x64 -o NUL   # -> encoded 10167 frames
```

## Observed vs expected

| Clip | Container samples | Leading pictures | `--avhw` encoded | `--avsw` encoded |
|---|---|---|---|---|
| Sony XAVC HS, 30 f | 30 | 3 | **27** | 30 |
| Sony XAVC HS, 330 f | 330 | 3 | **327** | 330 |
| Sony XAVC HS, 10 170 f | 10 170 | 3 | **10 167** | 10 170 |
| Sony XAVC S, H.264 4:2:2, 195 f | 195 | 2 | **193** | 195 |
| DJI HEVC control | 105 | 0 | 105 | 105 |
| x265 re-encode control | 30 | 0 | 30 | 30 |
| synthetic `testsrc2` control | 60 | 0 | 60 | 60 |

Predictor, which held for every case measured:

```text
avhw_frames = container_samples − pictures_presented_before_first_IRAP
```

## Root cause

`NVEncCore/NVEncPipeline.h`, `PipelineTaskNVDecode::getOutputFrame()`.

`m_hwDecFirstPts` is the PTS of the first packet **fed to the decoder** — in
decode order, that is the first IRAP — **not** the first PTS that should be
**presented**. The output loop buffers decoded pictures until one satisfies
`dispInfo.timestamp >= m_hwDecFirstPts`, then advances its running cursor
`istart` past the earlier entries and calls `m_dec->frameQueue()->releaseFrame()`
on them, so the emission loop below never emits them.

On Sony XAVC the two quantities differ by the leading-picture count: those
pictures follow the IRAP in **decode** order but precede it in **presentation**
order, so their PTS is below `m_hwDecFirstPts` and they are exactly what the loop
discards. The decoder itself produced them correctly — a decoder-callback trace
shows every picture being emitted (30/30, 195/195, 360/360, 105/105 on the
corresponding clips).

The filter's only legitimate purpose is honouring `--seek`: when a seek *is*
requested, pictures before the requested position should indeed be dropped.

## Suggested fix

18 added lines, 2 files, 0 deletions. The two blocks below are the complete
change; nothing else in either function is touched.

### 1. `NVEncCore/rgy_input.h` — expose the existing `--seek` value to the pipeline

Find `getInputDOVIProfile()` in the input class and insert the new accessor
**after it, before `protected:`**:

```cpp
    virtual RGYDOVIProfile getInputDOVIProfile() {
        return RGY_DOVI_PROFILE_UNSET;
    }

    // Read start position (first = --seek, second = --seekto).
    // For hardware decode the first packet's pts may only be treated as the output
    // start position when a seek was actually performed, so expose it to the pipeline.
    std::pair<float, float> getSeekParam() const {
        return m_seek;
    }
```

The added lines are the accessor (3 lines) plus its comment, after a blank
separator line — 7 added lines in total, matching the original patch size.
`m_seek` already exists and is read-only here.

### 2. `NVEncCore/NVEncPipeline.h` — apply the filter only when a seek was requested

In `PipelineTaskNVDecode::getOutputFrame()`, find this block:

```cpp
            if (m_gotFrameAfterFirstPts) {
                break;
            }
            // OpenGOP等でキーフレームより前のフレームのptsで出てくることがあるのを調整
```

> The comment line above is quoted **verbatim from your source** so that the
> block can be located by text search. Its meaning, for reference:
> *"adjust for frames that may arrive with a pts earlier than the keyframe's, as
> happens with OpenGOP."*

and replace it with:

```cpp
            if (m_gotFrameAfterFirstPts) {
                break;
            }
            // With no seek requested, the first packet's pts must not be treated as the
            // "output start position". In a stream like XAVC, whose first keyframe is
            // not first in presentation order, the leading pictures are presented
            // before that keyframe, so their pts is always smaller than the first
            // packet's pts and the filter below would discard all of them. Those
            // pictures follow the keyframe in decode order, so the decoder does output
            // them; only frames before a REQUESTED seek should be dropped.
            if (m_input->getSeekParam().first <= 0.0f) {
                m_gotFrameAfterFirstPts = true;
                break;
            }
            // OpenGOP等でキーフレームより前のフレームのptsで出てくることがあるのを調整
```

The last line is again the original comment, left untouched so that the
replacement is a pure insertion.

When a seek **was** requested the original filter runs verbatim, which is why the
seek behaviour is unchanged by this fix.

## Verification

A build from the pinned revision with only this change:

| Case | Container | `--avhw` before | `--avhw` after | vs `--avsw` |
|---|---|---|---|---|
| Sony XAVC HS 30 f | 30 | 27 | **30** | byte-identical |
| Sony XAVC HS 330 f | 330 | 327 | **330** | byte-identical |
| Sony XAVC HS 10 170 f | 10 170 | 10 167 | **10 170** | byte-identical |
| Sony H.264 4:2:2 195 f | 195 | 193 | **195** | byte-identical |
| DJI / x265 / synthetic controls | — | unchanged | unchanged | byte-identical |

* "Byte-identical" means the same output MP4 sha256, the same ordered
  `(pts, dts, flags, size)` packet sequence, the same keyframe index, and
  identical per-frame Y/U/V fingerprints against `--avsw`.
* Seek + trim: 17/17 combinations identical between the stock and the changed
  binary, across two clips (5.5 s and ~169 s) covering `--seek`, `--frames`,
  `--trim` and `--seek … --trim`.
* Still GPU-resident: `Input Info` reports `avcuvid:`, the VPP filter is
  `copyDtoD` (device-to-device), and both the VE (encoder) and VD (decoder)
  engines are busy. No `hwdownload`, no host round-trip, no software decoder.

## What I did not test

* Only NVEncC **9.31** (`2cb9d81`) was examined.
* Material exercised: Sony XAVC HS (HEVC Main10 4:2:0) and Sony XAVC S
  (H.264 High 4:2:2 10-bit), in MP4 and MKV, plus DJI HEVC and x265/synthetic
  controls. **No All-I / XAVC S-I, no 8-bit, no 1080p and no VFR material was
  available.**
* The mechanism *predicts* that any stream whose first IRAP is not first in
  presentation order is affected. That is an inference from the mechanism,
  consistent with every measurement made — it is not a measurement over all
  material.
* Single-GPU host. `--device 1`, parallel encode and `--split-enc` were not
  tested.
* The test binary was a local build (avs/vpy readers disabled, generated CUDA
  MSBuild shim, FFmpeg linked dynamically), so cross-binary wall-clock numbers
  are not controlled. Every correctness claim above is counts / PTS / keyframes /
  fingerprints / bytes, which are toolchain-independent.

## Two separate things, not part of this fix

**1. `--frames N` and `--trim` are short by the leading-picture offset.** This is
pre-existing, is a property of the reader shared by `--avsw` and `--avhw`, and is
not changed by this fix. It has three regimes, measured on a 360-sample XAVC HS
clip (`leading_pictures = 3`):

| Request | Delivered | Note |
|---|---|---|
| `N ≤ leading_pictures` (1, 2, 3) | **360** | request ignored, whole clip delivered |
| `leading < N < container` (4, 10, 30, 100) | `N − 3` → 1, 7, 27, 97 | the regular case |
| `N ≥ container` (18 000 asked of an 11 280-sample clip) | **11 280** | clamped to EOF, shortfall disappears |
| control clip, `leading_pictures = 0` | `N` exactly | 1/3/10/30/100 all exact |

(The first, second and fourth rows used the 360-sample clip; the third needed a
longer clip, since it requires `N ≥ container`.)

`--avhw` and `--avsw` agree on all three regimes (identical sha256), which is why
this is reported as a semantic quirk rather than as part of the frame-loss bug.
It is listed only so that a `--frames 3` test is not mistaken for the defect
above.

Note also that `--seek` behaves differently: stock and changed `--avhw` are
byte-identical to each other on seek, but the hardware reader and `--avsw` pick
different pictures after a seek. That is a separate, unexplained reader-semantics
difference, unchanged by this fix and outside its scope.

**2. `FramePosList::setPocAndFix()` — a separate, lower-severity defect, not part
of this fix.** In the shared reader (`rgy_input_avcodec.h`), the frame-position
table prunes entries whose PTS precedes `m_firstKeyframePts` (bounded by `<= 16`
for PTS wraparound). On the same Sony files it removes exactly the leading
pictures — 3 entries for XAVC HS, 2 for H.264 4:2:2, 0 on every control — so the
reader's own `N frames, End of file` and `--log-framelist` **under-report**.

It is *not* the cause of the frame loss: it fires **identically** on `--avsw` and
`--avhw` (`--log-framelist` is byte-identical on every fixture), and `--avsw`
delivers every frame. It is a *table* defect, not a *delivery* defect, and it
degrades the original-duration lookup, telecine flags and the reader's
self-report rather than the output. Happy to file it separately with samples if
that is useful.
