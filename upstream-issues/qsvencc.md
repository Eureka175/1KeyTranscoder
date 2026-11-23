# `--avhw` silently drops open-GOP leading pictures (Sony XAVC HEVC: N−3)

## Summary

With `--avhw`, QSVEncC silently delivers fewer frames than the container holds,
on any stream whose first IRAP is **not** first in presentation order. There is
no error, no warning, `rc = 0`, and the output file plays normally.

* Sony XAVC HS (HEVC Main10 4:2:0, LongGOP) → **N − 3**
* `--avsw` on the same files is exact, and FFmpeg's `hevc_qsv` on the **same
  silicon** is exact — so the decoder is not at fault; the pipeline discards
  frames the decoder already produced correctly.

The number of lost frames is not arbitrary: it is exactly the number of pictures
presented before the first packet fed to the decoder.

## Environment

| | |
|---|---|
| QSVEncC | `QSVEncC (x64) 8.26 (r4504) by rigaya, Aug  8 2026 12:17:31 (VC 1944/Win)` |
| sha256 | `107e5f0d3256316293e3650fcd3c4fa1daaf64218dee2f9f3742e5a0489968b3` (10 688 512 bytes) |
| Source revision used for the analysis | commit `b14c9652b34b998351516cc63c86eb990c01c7c8` (= 8.26) |
| OS / GPU | Windows 11 (26200) · Intel Arc 140T (16 GB) · driver `32.0.101.8974` |
| Input material | Sony A7M5, XAVC HS, HEVC Main10 4:2:0, 3840×2160, 60000/1001 fps, LongGOP, ~150 Mbps |

## Reproduction

Minimal — no encoding needed:

```powershell
# clip with 30 container samples and 3 leading pictures
QSVEncC64.exe -i <sony-xavc-hs>.MP4 --avhw -c raw --output-res 64x64 -o NUL
# -> encoded 27 frames

QSVEncC64.exe -i <sony-xavc-hs>.MP4 --avsw -c raw --output-res 64x64 -o NUL
# -> encoded 30 frames
```

## Observed vs expected

| Case | Container samples | Leading pictures | `--avhw` | `--avsw` |
|---|---|---|---|---|
| Sony XAVC HS | 30 | 3 | **27** | 30 |
| DJI HEVC control | 105 | 0 | 105 | 105 |

Predictor, which held for every case measured:

```text
avhw_frames = container_samples − pictures_presented_before_first_IRAP
```

## Root cause

`QSVPipeline/qsv_pipeline_ctrl.h`, class `PipelineTaskMFXDecode`, function
`sendBitstream()` — the frame-admission filter on `surfDecOut`:

```cpp
        if (surfDecOut != nullptr && lastSyncP != nullptr
            // 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する
            && (!m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

> The comment line above is quoted **verbatim from your source** so that the
> statement can be located by text search. Its meaning, for reference: *"the
> first frame may carry data from before the fed frame because of an OpenGOP
> B-frame, so in that case ignore the frame."*

`m_firstPts` is the presentation timestamp of the **first packet fed to the
decoder** — in decode order, the first IRAP — and the filter treats it as the
output-start boundary. Every MFX output presented before it is rejected.

The `m_decFrameOutCount > 0` clause looks like it should let all but the first
frame through, but `m_decFrameOutCount` only advances when a frame is
**accepted**, so while the three leading pictures are being rejected the counter
stays at 0 and **all three** are rejected, not one.

That comment states the intent honestly, and for a genuine OpenGOP artefact the
reasoning is correct. On XAVC those pictures are not artefacts: they are real,
decodable pictures that follow the IRAP in decode order and are displayed before
it.

An instrumented build of the pinned revision traces the whole path:

```text
container samples                                 30
  -> reader packets read                          30     [READER] EOF frameNum=30
  -> FramePosList after setPocAndFix              28     [READER] EOF frameNum=28
       (3 pictures REMOVED: pts 0, 1001, 2002)
  -> MFX decode real outputs                      30     [MFXDEC] surf=1 lastSync=1  x30
       of which REJECTED by the admission filter   3
       of which ACCEPTED                          27
  -> encoder input                                27
  -> encoded frames                               27     "encoded 27 frames"
```

Per-frame trace of the three rejections — all three arrive with
`decFrameOutCount=0`, which is why the counter clause never rescues them:

```text
[MFXDEC] out ts=0     firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=1001  firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=2002  firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
```

**MFX decoded all 30 pictures.** The loss happens after the decoder, in the
pipeline task.

## Suggested fix

15 added lines across 2 files, one of which replaces an existing line (net +14).
The two blocks below are the complete change; nothing else in either function is
touched.

### 1. `QSVPipeline/rgy_input.h` — expose the existing `--seek` value to the pipeline

Find `getInputCodec()` in the input class and insert the new accessor
**after it**:

```cpp
    RGY_CODEC getInputCodec() {
        return m_inputVideoInfo.codec;
    }

    // Read start position (first = --seek, second = --seekto).
    // For hardware decode the first fed packet's PTS may only be treated
    // as the output start position when a seek was actually requested.
    std::pair<float, float> getSeekParam() const {
        return m_seek;
    }
```

The four added lines are the `getSeekParam()` accessor plus its comment; `m_seek`
already exists and is read-only here.

### 2. `QSVPipeline/qsv_pipeline_ctrl.h` — apply the rejection only when a seek was requested

In `PipelineTaskMFXDecode::sendBitstream()`, find this statement:

```cpp
        if (surfDecOut != nullptr && lastSyncP != nullptr
            // 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する
            && (!m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

(The Japanese comment line is quoted verbatim from your source so the statement
can be located; its translation is given under **Root cause** above.)

and replace it with:

```cpp
        // For streams whose first keyframe is not first in presentation order
        // (XAVC), the leading pictures are always presented before the first fed
        // packet, so their PTS is smaller than m_firstPts. They are decodable --
        // they follow the keyframe in decode order -- so they must be kept.
        // Only pictures before a REQUESTED seek position should be dropped, so
        // the rejection below is applied only when a seek was actually requested;
        // the comment below is right about genuine OpenGOP artefacts, not XAVC.
        const bool bSeeked = (m_input != nullptr && m_input->getSeekParam().first > 0.0f);
        if (surfDecOut != nullptr && lastSyncP != nullptr
            // 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する
            && (!bSeeked || !m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

The one Japanese line inside the replacement is the original comment, left
untouched so that only the condition actually changes.

When a seek **was** requested the original rejection runs verbatim, which is why
the seek behaviour is unchanged by this fix.

## Verification

A local build of the pinned revision **reproduces the shipped behaviour
exactly**, which is what makes the instrumented result trustworthy:

```text
shipped 8.26 : --avsw 30, --avhw 27
local   8.26 : --avsw 30, --avhw 27
```

With only this change applied:

| Check | Result |
|---|---|
| Sony XAVC HS | 27 → **30**; per-frame SHA-256 **identical to `--avsw`** (30/30) |
| DJI / control material | unchanged |
| Seek regression | 3/3 identical before and after |
| Trim regression | 3/3 identical before and after |
| A/B cases total | 10, all pass |

## What I did not test

* **Only QSVEncC 8.26 (`b14c965`) was examined.** Releases 8.27 – 8.30 exist and
  were not inspected; `qsv_pipeline_ctrl.h` may have changed, so the condition
  has to be re-located on any other revision rather than assumed.
  Stated as my own claim boundary: *runtime-proven on the pinned 8.26 revision;
  not yet a general claim for later releases.*
* Material exercised: Sony XAVC HS (HEVC Main10 4:2:0) in MP4, plus DJI HEVC
  controls. **No All-I / XAVC S-I, no 8-bit, no 1080p and no VFR material was
  available.**
* The mechanism *predicts* that any stream whose first IRAP is not first in
  presentation order is affected; that is an inference from the mechanism,
  consistent with every measurement made, not a measurement over all material.
* The test binary was a local build (avs/vpy readers and OpenVINO disabled,
  OneVPL built separately), so cross-binary wall-clock numbers are not
  controlled. All correctness claims above are counts, PTS, keyframe indices and
  per-frame hashes, which are toolchain-independent.

## Related but separate: H.264 4:2:2 10-bit is a capability refusal

This is **not** the same problem and the fix does not change it, but it is worth
stating so the two are not conflated:

```text
avqsv: codec h264(yuv422p10le) unable to decode by qsv.
rc = -31, no reader constructed, no output file
```

A loud refusal is fine behaviour. The bug in this report is the opposite kind:
silent, `rc = 0`, plausible-looking output. On this machine the two coexist in the
same binary on adjacent files, which is exactly why "unsupported" and "frame
drop" should stay distinguishable in the log.

## Related but separate: `FramePosList::setPocAndFix()`

The shared reader (`rgy_input_avcodec.h`, also used by NVEncC and VCEEncC) has a
frame-position **table** defect: `setPocAndFix()` prunes entries whose PTS
precedes `m_firstKeyframePts` (bounded by `<= 16` for PTS wraparound). On the same
Sony files it removes exactly the leading pictures — see the
`FramePosList after setPocAndFix 28` line in the trace above.

It is **not** the cause of the frame loss: it fires identically on `--avsw` and
`--avhw` (the reader self-report and `--log-framelist` are byte-identical on
both), and `--avsw` delivers every frame. Its effect is that the reader's own
`N frames, End of file` and `--log-framelist` **under-report**, which degrades the
original-duration lookup and telecine flags. It is a *table* defect, not a
*delivery* defect, and is deliberately not bundled into the fix above. Happy to
file it separately with samples if that is useful.
