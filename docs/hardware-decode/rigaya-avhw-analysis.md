# rigaya `--avhw` Source Archaeology — Phase 2

> **Branch**: `research/rigaya-avhw-source` (worktree `F:\1KeyTranscoder-rigaya-research`, from `main` @ `15cf218`)
> **Phase**: 2 — locate the exact rigaya source path that discards Sony's
> leading pictures.
> **Production code status**: **unmodified.** Nothing in this worktree's
> production tree was changed; `git diff main...HEAD --stat` touches only
> `docs/` and `work/`.
> **Companion documents**: [`README.md`](README.md) ·
> [`root-cause.md`](root-cause.md) · [`divergence.md`](divergence.md) ·
> [`investigation.md`](investigation.md)

> **BRANCH CLOSED — SOURCE ARCHAEOLOGY ONLY.** This document is the *source-level*
> analysis. It was written **before** a patched binary existed, so its
> "patch feasibility" and "recommendation" sections carry the state of that
> moment: they argue by model and by `git apply --check`, and they say the patch
> is **not runtime-validated**. That is a statement about *this phase's*
> environment (no CUDA toolkit, no NVDEC headers), not about the patch.
>
> The patch this document proposes was subsequently **built and runtime
> validated** in the sibling branch `research/rigaya-nvencc-avhw`. See §Close-out
> at the end of this file for the disposition of every claim, and the
> `nvencc-patch.md` provenance record on that branch. Nothing in this document is
> retracted; its confidence grades are preserved as written and are restated
> with their final status in §Close-out.

---

## Observed behavior

Phase 1 established the *what*. Phase 2 re-measured it end to end before
touching any source, because a source-level claim is only worth as much as
the reproduction it explains.

**Reproduction** (Phase 1 §Reproduction, re-run in this phase):

```powershell
$nv  = 'F:\1KeyTranscoder\tools\NVEncC_9.31_x64\NVEncC64.exe'
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'   # 30 frames

& $nv -i $src --avsw -c raw --output-res 64x64 -o NUL   # encoded 30 frames
& $nv -i $src --avhw -c raw --output-res 64x64 -o NUL   # encoded 27 frames
```

| Fixture | codec | container samples | `--avsw` | `--avhw` | deficit |
|---|---|---|---|---|---|
| `20260903_C1170.MP4` | HEVC Main10 4:2:0 | 30 | 30 | **27** | 3 |
| `20260823_C0886.MP4` | HEVC Main10 4:2:0 | 360 | 360 | **357** | 3 |
| `C9037.MP4` | H.264 High 4:2:2 10-bit | 195 | 195 | **193** | 2 |
| `ctrl-x265.mp4` (FFmpeg x265 re-encode of C1170) | HEVC | 30 | 30 | 30 | 0 |
| `DJI_20260830095031_0009_D.MP4` (non-Sony) | HEVC | 105 | 105 | 105 | 0 |

Frame-level verification (`work/verify_truncation.py`, the Phase 1
`divergence.md` §7.1 tolerant tier, 8×8 luma block means):

```
20260903_C1170  avsw=30 avhw=27  deficit=3
  HW[i] == SW[i+3] for 27/27 frames (within 2 codes, worst diff 2)
  removed display-order indices: [0, 1, 2]
  frames of SW absent from HW entirely: SW[0], SW[1], SW[2]
  extra frames in HW: 0
```

So it is a **strict head truncation of exactly the leading pictures**, with
no reorder, no duplication and no timestamp-only change. That reproduces
Phase 1 RC-2/RC-3 exactly, on freshly measured data.

### The measurement that redirected this phase

Two observations from the shipped binary made the Phase 1 working hypothesis
("the reader skips until the first keyframe") untenable. Both come from the
tool's own debug/trace output, not from reading source.

**(1) The reader does not drop anything.** `--log-level debug` prints the
reader's own frame/keyframe accounting, and it is *identical* for `--avsw`
and `--avhw`:

```
avsw:    found first key frame: timestamp 3003 (0.05005), offset 0
avcuvid: found first key frame: timestamp 3003 (0.05005), offset 0
```

`offset 0` is the value the reader stores at
`rgy_input_avcodec.cpp:3430` (`m_trimParam.offset = i_samples`) — i.e. the
number of packets the "skip until first keyframe" branch discarded. It is
**zero on every fixture**, including H.264 (`timestamp 2002, offset 0`) and
the controls. That branch never fires for Sony material.

**(2) The hardware decoder is exact.** `--log-level trace` prints one line
per frame the NVDEC parser hands to the display callback
(`CuvidDecode::DecPictureDisplay`):

| Fixture | frames NVDEC displays | frames the encoder writes |
|---|---|---|
| `20260903_C1170.MP4` | **30** | 27 |
| `C9037.MP4` | **195** | 193 |
| `20260823_C0886.MP4` | **360** | 357 |
| `DJI_..._0009_D.MP4` | **105** | 105 |

The decoder emits **every** frame, in correct display order, including the
leading pictures:

```
cuvid: DecPictureDisplay idx: 2, 0
cuvid: DecPictureDisplay idx: 1, 1001
cuvid: DecPictureDisplay idx: 3, 2002
cuvid: DecPictureDisplay idx: 0, 3003     <-- the IRAP
cuvid: DecPictureDisplay idx: 6, 4004
```

**The decode is perfect. The loss is downstream of the decoder, inside
rigaya's pipeline.** That is the finding that located the real source path —
and it contradicts the Phase 1 hypothesis, which is recorded here rather
than quietly dropped.

## Relevant source path

Pinned source: `rigaya/NVEnc` **tag `9.31`** (matches the shipped
`NVEncC (x64) 9.31 (r4047) by rigaya, Aug 8 2026`), cloned to
`work/rigaya-src/NVEnc` (not committed — see §Upstream record).

| # | Site | Role in the defect |
|---|---|---|
| 1 | `NVEncCore/rgy_input_avcodec.cpp:3280` `RGYInputAvcodec::getSample()` | packet demux; **not** the drop site |
| 2 | `NVEncCore/rgy_input_avcodec.cpp:3405` `if (!bTreatFirstPacketAsKeyframe && !gotFirstKeyframe && !keyframe)` | "skip until first keyframe"; **does not fire** on Sony (offset 0) |
| 3 | `NVEncCore/CuvidDecode.cpp:353` `CuvidDecode::DecPictureDisplay()` | **emits all frames** — proves the decoder is exonerated |
| 4 | `NVEncCore/NVEncPipeline.h:1550` `m_hwDecFirstPts = bitstream.pts()` | records the PTS of the **first packet fed to the decoder** (the first IRAP) |
| 5 | **`NVEncCore/NVEncPipeline.h:1665-1667`** | **THE DROP SITE** |
| 6 | `NVEncCore/NVEncPipeline.h:1679-1681` | second (dead) head-drop loop |

### The drop site

`NVEncPipeline.h:1400` `class PipelineTaskNVDecode` →
`getOutputFrame()` (lines 1585-1727), original 9.31 lines 1599-1684:

```cpp
int istart = 0;
std::vector<CUVIDPARSERDISPINFO> dispInfoList;
while (m_state == RGY_STATE_RUNNING) {
    ...
    dispInfoList.push_back(dispInfo);
    // 一度でもフレームが出ている場合は、それ以降のフレームはチェックをskipする (wrap対策)
    if (m_gotFrameAfterFirstPts) break;                                     // :1653

    // OpenGOP等でキーフレームより前のフレームのptsで出てくることがあるのを調整
    if (dispInfo.timestamp >= m_hwDecFirstPts || m_hwDecFirstPts == AV_NOPTS_VALUE) {
        if (dispInfoList.size() > 1) {
            const bool lastFrameIsOverFirstPts = dispInfo.timestamp > m_hwDecFirstPts;
            const int targetStart = (int)dispInfoList.size() - (lastFrameIsOverFirstPts ? 2 : 1);
            for (; istart < targetStart; istart++) {                        // :1665  <-- DROP
                m_dec->frameQueue()->releaseFrame(&dispInfoList[istart]);    // :1666  <-- DROP
            }                                                               // :1667
            if (targetStart >= 0 && lastFrameIsOverFirstPts) {
                dispInfoList[targetStart].timestamp = m_hwDecFirstPts;       // pattern-A rewrite
            }
        }
        m_gotFrameAfterFirstPts = true;
        break;
    }
    // m_hwDecFirstPtsより前のフレームはdropするしかない
    for (; istart < (int)dispInfoList.size() - 1; istart++) {                // :1679  (dead)
        m_dec->frameQueue()->releaseFrame(&dispInfoList[istart]);            // :1680  (dead)
    }
}
for (; istart < (int)dispInfoList.size(); istart++) {                        // :1685  emission
    ...surfDecOut->setTimestamp(dispInfo.timestamp); ...
    m_outQeueue.push_back(...);
}
```

`istart` is a running "how many of `dispInfoList` have been consumed"
cursor. Advancing it past `targetStart` at line 1665 means the final
emission loop at line 1685 **starts at `istart` and therefore never emits
those entries**. `releaseFrame()` returns the decoder buffer to NVDEC; the
frame is not merely skipped, its slot is recycled.

### Frame/packet state at the drop

Trace for `20260903_C1170.MP4` (`--avhw`), decode order into the decoder:

```
NVDEC: Set packet #0, size 2419182, pts 3003   <-- IDR, AV_PKT_FLAG_KEY
NVDEC: Set packet #1, size  122461, pts 1001   <-- leading picture
NVDEC: Set packet #2, size  140813, pts 0      <-- leading picture
NVDEC: Set packet #3, size  148400, pts 2002   <-- leading picture
NVDEC: Set packet #4, size  518798, pts 7007
```

`m_hwDecFirstPts` is set from `Set packet #0` → **3003**. NVDEC then emits in
display order, so `getOutputFrame()`'s buffering loop accumulates

```
dispInfoList = [ts 0, ts 1001, ts 2002, ts 3003]
```

and stops at the first entry satisfying `timestamp >= 3003`. At that moment
`dispInfoList.size() == 4`, `lastFrameIsOverFirstPts == false` (3003 is not
`> 3003`), so `targetStart == 4 - 1 == 3`. The drop loop runs
`istart: 0 → 3`, releasing exactly the three leading pictures. The final
loop then emits from `istart == 3`: the IRAP and everything after it.

The trace confirms this directly — the frames that are *never* released
downstream, and the ones that reach the PTS stage, both start at the IRAP:

```
NVDEC: input frame (dev) #0, pic_idx 2, timestamp 0      \  these four share
NVDEC: input frame (dev) #0, pic_idx 1, timestamp 1001    |  decOutFrames #0
NVDEC: input frame (dev) #0, pic_idx 3, timestamp 2002    |  (one call to
NVDEC: input frame (dev) #0, pic_idx 0, timestamp 3003   /   getOutputFrame())
NVDEC: Free input frame pic_idx 0, timestamp 3003        <-- first release
...
CHECKPTS: check_pts(0/0): nOutEstimatedPts 0, outPtsSource 12, outDuration 4
CHECKPTS: check_pts(0):   estimetaed orig_pts 3003, framePos 0
```

`CheckPTS` — the stage immediately downstream — sees its **first** frame as
`orig_pts 3003`. The three leading pictures never arrive there. `DECPTS`
counts: 30 `DecPictureDisplay`, 30 `input frame (dev)`, 27 emitted.

## Exact trigger

The trigger is the conjunction of three conditions, all structural:

1. **Display order differs from decode order at the head.** NVDEC emits in
   display order; the stream's first IRAP is *later* in presentation order
   than some other pictures. Formally:
   `∃ i : PTS[i] < PTS[first IRAP]` — i.e.
   `pictures_before_first_keyframe > 0`.
2. **Those pictures are *before* the first packet's PTS.** Because packets
   are fed to the decoder in decode order and the first packet is the IRAP,
   `m_hwDecFirstPts == PTS[first IRAP]`. Condition 1 therefore implies
   `leading PTS < m_hwDecFirstPts`.
3. **The leading pictures arrive in the same dequeue batch as the IRAP**, so
   `dispInfoList.size() > 1` when the `timestamp >= m_hwDecFirstPts` test
   first succeeds. If only the IRAP were buffered, `size() == 1` and the
   drop block is skipped entirely.

When all three hold, `targetStart = size - 1` and the drop loop consumes
every buffered frame except the last, i.e. **exactly the leading pictures**.

The deficit is therefore

```
avhw_frames = container_samples − pictures_before_first_keyframe
```

which is **identical to the Phase 1 empirical predictor** — now derived from
source rather than fitted to measurements. Phase 1 validated that predictor
on 297/297 measured runs; this phase explains why it has that form.

### Why the reader's keyframe gate is a decoy

`rgy_input_avcodec.cpp:3405` looks like the obvious culprit and is not:

```cpp
if (!bTreatFirstPacketAsKeyframe && !m_Demux.video.gotFirstKeyframe && !keyframe) {
    av_packet_unref(pkt.get());
    i_samples++;
    continue;                                  // <-- would drop packets
}
```

For Sony material the **first packet in decode order is already the IRAP**
(`Set packet #0, pts 3003`, `flags 1` → `AV_PKT_FLAG_KEY`), so this branch is
never entered and `i_samples` stays 0. It is reachable, it looks right, and
it is not the bug. Recording this is the main methodological point of this
phase: the Phase 1 hypothesis pointed here, and measurement refuted it.

Note also that this gate runs on `--avsw` too — the same `getSample()`
serves both readers (`rgy_input.cpp:629-692` constructs `RGYInputAvcodec`
for `RGY_INPUT_FMT_AVHW` **and** `RGY_INPUT_FMT_AVSW`). A defect that fires
only under `--avhw` cannot live in code that both paths share.

## Root cause

**`PipelineTaskNVDecode::getOutputFrame()` conflates two different
quantities:**

* `m_hwDecFirstPts` is the PTS of the **first packet handed to the
  decoder** — in decode order that is the first IRAP
  (`NVEncPipeline.h:1550-1552`).
* the loop at line 1659 treats it as **the PTS of the first frame that
  should be presented**, and discards everything below it.

Those two are equal only when the stream has no pictures preceding its first
IRAP in presentation order. Sony XAVC violates that assumption by design:
every clip codes 3 (HEVC) or 2 (H.264 4:2:2) pictures before its first IRAP
in presentation order. The code has a *partial* awareness of this — the
comment at line 1656 names the "OpenGOP" case — and its mitigation is to
keep **one** buffered frame (`size - 1`) and relabel it (pattern A). It has
no mechanism for keeping **several genuine leading pictures**, so it discards
them.

The result is a silent, deterministic, 100 %-reproducible head truncation
whose size equals the number of leading pictures.

Confidence: **`Confirmed`** for the drop site and mechanism. The decoder is
proven exact by its own callback log (30/30, 195/195, 360/360, 105/105); the
reader is proven not to drop by its own `offset 0` log on every fixture; the
arithmetic `targetStart = size - 1` is reproduced on three codecs/sizes; and
a frame-accounting model of the exact loop reproduces the measured output
counts (see §PoC).

> **One caveat, stated plainly.** The rigaya FFmpeg libraries bundled in
> `NVEncC64.exe` are statically linked and no matching symbol server/debug
> build was available, so the drop site was not confirmed by a live
> breakpoint. It is confirmed by three independent, mutually consistent
> lines of evidence: (a) the decoder callback trace shows every frame is
> produced, (b) the downstream `CheckPTS` stage never sees the leading
> pictures, and (c) a faithful re-implementation of the loop reproduces the
> exact measured counts for all five fixtures, including both controls. A
> runtime confirmation requires the debug build that §Patch feasibility
> shows is blocked in this environment.

## Why FFmpeg succeeds

FFmpeg's `-hwaccel cuda` / `-hwaccel qsv` path (Phase 1: 151/151 and 146/146
bit-identical to software) never applies this heuristic. The relevant
differences, in the terms this investigation requires:

| Aspect | FFmpeg `-hwaccel` | rigaya `--avhw` |
|---|---|---|
| Frame delivery | `avcodec_send_packet` / `avcodec_receive_frame`, drained to EOF | decode thread → `cuvidParseVideoData` → display callback → `FrameQueue` |
| Ordering | outputs in decoder order with `best_effort_timestamp`; **no reordering filter by default** | NVDEC display order; pipeline then re-sorts/normalises timestamps itself |
| First-frame handling | none — every returned frame is forwarded | `getOutputFrame()` discards frames below the first packet's PTS |
| Edit list | timestamp shift only (`-ignore_editlist 1` changes `first_pts` 0 → 2002, count stays 360) | not the trigger (Phase 1 RC-4, re-confirmed) |
| Leading pictures | decoded and **emitted** | decoded, then **discarded downstream** |

The decisive asymmetry: FFmpeg has *no* stage that compares a decoded
frame's PTS against a "first PTS" and drops the frame if it is lower. rigaya
does, and that stage is reachable only on the hardware path — the software
path (`RGYInputAvcodec::LoadNextFrameInternal`, `rgy_input_avcodec.cpp:3890`)
has no equivalent, which is why `--avsw` is exact on the same file through
the same reader.

This is a **reader/pipeline** difference over identical decoder silicon;
Phase 1 RC-5 reached the same conclusion from frame fingerprints alone.

## Minimal patch hypothesis

Remove the discard. Keep the buffered frames and let the existing emission
loop at line 1685 output them in display order. The complete patch — verified
with `git apply --check` against a pristine tag-`9.31` checkout — is:

```diff
diff --git a/NVEncCore/NVEncPipeline.h b/NVEncCore/NVEncPipeline.h
--- a/NVEncCore/NVEncPipeline.h
+++ b/NVEncCore/NVEncPipeline.h
@@ -1662,10 +1662,20 @@ protected:
                     // 最終フレームがFirstPtsを超えていたらそのひとつ前からデコード
                     const bool lastFrameIsOverFirstPts = dispInfo.timestamp > m_hwDecFirstPts;
                     const int targetStart = (int)dispInfoList.size() - (lastFrameIsOverFirstPts ? 2 : 1);
-                    for (; istart < targetStart; istart++) {
-                        m_dec->frameQueue()->releaseFrame(&dispInfoList[istart]);
-                    }
-                    if (targetStart >= 0 && lastFrameIsOverFirstPts) {
+                    // ここで以前は istart を targetStart まで進めて
+                    // dispInfoList[istart] を releaseFrame していた。
+                    // しかし捨てられていたのは「最初のIRAPより表示順が前にある正当な
+                    // ピクチャ」(leading picture) である。Sony XAVC では HEVC 3枚 /
+                    // H.264 4:2:2 2枚あり、real で単調増加する PTS を持つ。
+                    // m_hwDecFirstPts は「デコーダに最初に投入したパケット(=最初のIRAP)の
+                    // pts」であって「最初に提示されるフレームのpts」ではないため、
+                    // 表示順でその前にあるフレームは正当な出力対象である。
+                    // よって istart は進めず、下の最終ループで表示順どおりに出力する。
+                    //
+                    // パターンAのtimestamp修正は、以前と同じく「バッファ窓の2枚目以降」に限る。
+                    // targetStart == 0 で書き換えると、保持した正当な leading picture まで
+                    // m_hwDecFirstPts に書き換えてしまい、PTSが重複する。
+                    if (targetStart > 0 && lastFrameIsOverFirstPts) {
                         // パターンAなので、最初のフレームのtimestampを修正する
                         dispInfoList[targetStart].timestamp = m_hwDecFirstPts;
                     }
@@ -1673,12 +1683,11 @@ protected:
                 m_gotFrameAfterFirstPts = true;
                 break;
             }
-            // m_hwDecFirstPtsより前のフレームがたくさん出てきてしまうことがある
-            // m_hwDecFirstPtsより前のフレームはdropするしかない (そうしないとデコードがフレームバッファ不足で止まってしまう)
-            // 最後のフレームは、m_hwDecFirstPtsが出てこない場合に備えて残しておく
-            for (; istart < (int)dispInfoList.size() - 1; istart++) {
-                m_dec->frameQueue()->releaseFrame(&dispInfoList[istart]);
-            }
+            // ここには到達しない。最初のIRAPに到達した時点で上の分岐が
+            // m_gotFrameAfterFirstPts を立てて break するため、先頭側でフレームを捨てる
+            // 経路は存在しない。
+            // (旧コードはここで m_hwDecFirstPts より前のフレームを releaseFrame しており、
+            //  それも Sony XAVC の leading picture を失わせる一因だった)
             if (m_stopwatch) m_stopwatch->add(0, 1);
         }
         if (m_stopwatch) m_stopwatch->set(0);
```

Effect on the two changed lines: the `releaseFrame` loop (3 statements) is
removed entirely, and the pattern-A guard narrows from `targetStart >= 0` to
`targetStart > 0`. Net: **19 insertions / 10 deletions, all comments except
those two lines.**

Two details matter and both were established by modelling, not by intuition:

1. **Simply deleting the `releaseFrame` calls is not enough** — `istart` must
   not advance either. An early attempt that kept `istart = targetStart` and
   only skipped the `releaseFrame` calls still produced 27/30, because the
   emission loop starts at `istart`.
2. **The pattern-A rewrite must be narrowed to `targetStart > 0`.** With the
   leading pictures preserved, `targetStart` is 0 in the normal Sony case; an
   unguarded rewrite would relabel the first *legitimate* leading picture to
   `m_hwDecFirstPts`, colliding with the IRAP's timestamp. Written as
   `targetStart > 0` the rewrite is provably a no-op for a genuine leading
   picture (`dispInfoList[targetStart].timestamp < m_hwDecFirstPts` implies
   the rewrite is a no-op), so existing pattern-A behaviour is preserved
   exactly.

The complete patch, verified to apply to a pristine 9.31 tree:

```
work/poc-patches/0001-minimal-leading-picture-fix.patch
```

## Patch feasibility

**The patch is 2 lines of behaviour change in 1 file, in a header-only
class, with no API, ABI, data-structure or concurrency change.**

| Question | Answer |
|---|---|
| Files touched | 1 (`NVEncCore/NVEncPipeline.h`) |
| Net change | −2 statements, +1 condition change, 1 dead loop removed (`git diff --stat`: 20 insertions / 11 deletions, comments included) |
| Public API / ABI | none changed |
| New state | none |
| Threading / locking | unchanged (`m_outQeueue` push path, `videoQualityMetric` untouched) |
| Memory / buffer risk | none introduced: frames that were released are now converted to surfaces via the *existing* `m_workSurfs.addSurface()` path and freed on the normal lifecycle |
| Compile-time risk | **low** — pure deletion plus a narrowed condition in a file that already compiles; the patch was verified with `git apply --check` on a pristine `9.31` checkout |
| Upstreamability | plausible — this is a genuine rigaya bug, independent of 1KeyTranscoder |

### Can it be built and run here? **No — environment-blocked.**

A full NVEncC build was attempted. What is present and what is missing:

| Requirement | Status |
|---|---|
| MSVC | ✅ VS 18 Build Tools, toolset 14.51.36231 |
| Windows SDK | ✅ 10.0.26100.0 |
| meson + ninja | ✅ installed (`meson 1.12.0`, `ninja 1.13.2`) |
| rigaya FFmpeg SDK | ✅ `ffmpeg_dlls_for_hwenc_20250830.7z` (41.9 MB), pre-staged |
| **`nvcc` (CUDA compiler)** | ❌ **absent** — `CUDA\v13.1` contains only `bin/` + `lib/` (runtime), no toolkit headers or compiler; no `nvcc.exe` anywhere on the machine |
| **`nvcuvid` / NVDEC SDK headers** | ❌ absent (`cuviddec.h`, `nvcuvid.h`) |
| AviSynth+ SDK / VapourSynth SDK | ❌ absent |

NVEncC's `meson.build` declares `dependency('cuda', version: '>=10.0',
required: true)` and compiles `*.cu` files (jITify/NVRTC kernels), so a
build requires installing the full multi-GB CUDA Toolkit plus two third-party
SDKs. **No runtime patch validation was therefore possible.** This is stated
as a limitation, not worked around, and no attempt was made to substitute a
different tool for the measurement.

### What *was* validated instead

A standalone C++ model of the exact loop
(`work/model_getoutputframe.cpp`, built with the available MSVC into
`work/m4.exe`) was run against the **real** NVDEC display order and the
**real** `m_hwDecFirstPts` extracted from the shipped binary's own trace:

```
=== rigaya NVEnc 9.31 PipelineTaskNVDecode::getOutputFrame model ===

Sony XAVC HS 30f (3003)      firstPts=3003  ndec= 30 | orig 27 released=3 | PATCHED 30 released=0
Sony XAVC S H264 195f (2002) firstPts=2002  ndec=195 | orig 193 released=2 | PATCHED 195 released=0
x265 control (0)             firstPts=   0  ndec= 30 | orig 30 released=0 | PATCHED 30 released=0
```

* the model reproduces the **measured** original counts exactly (27, 193);
* the patched model produces 30 and 195 — i.e. **equal to `--avsw`**;
* the patched output PTS sequence is strictly monotonic in every case;
* the control case is **unchanged** by the patch (30 → 30), confirming the
  patch is a no-op where `pictures_before_first_keyframe == 0`.

This is a strong but **not equivalent** substitute for running the patched
binary. It validates the patch's logic against real decoder output; it does
not exercise thread scheduling, surface lifetimes or the muxer.

## Regression risks

| # | Risk | Assessment | Mitigation |
|---|---|---|---|
| R1 | **Pattern A regresses.** The rewrite existed because a decoder may report a frame with the *wrong* (earlier) PTS that should be relabelled. | **Medium, contained.** The rewrite is preserved verbatim, only narrowed to `targetStart > 0`. Because `targetStart > 0` exactly means "the buffered window holds at least one frame before the target", this is the window the original code was written for. | Only a runtime test on the documented pattern-A sample (`Beauty_3840x2160_120fps_420_8bit_HEVC_MP4.mp4 --seek 6.66667`) can fully close this. It is **not available in this environment**. |
| R2 | **Pattern B (`720p ZDF HD.ts` head) changes output.** The dead loop at 1679-1681 was written for "many frames before the keyframe". | **Low.** Analysis shows the loop is unreachable: the only exit from the buffering loop that reaches it is fully covered by the `timestamp >= m_hwDecFirstPts` branch above it, which always `break`s. Status: `Likely` (reasoned), `Unconfirmed` (not executed). | Both loops removed together; behaviour is driven solely by the branch whose semantics are unchanged except for not discarding. |
| R3 | **Frame-buffer exhaustion.** The original comment warns that dropping is needed "so the decoder doesn't stall for lack of frame buffers". | **Low.** Only frames already dequeued into `dispInfoList` are retained — at most a handful — and they are converted to surfaces on the existing path in the same call. No dequeue is delayed. | Verify with a long 4K clip (≥ 10 000 frames) on the patched build. |
| R4 | **Downstream timestamp guard.** `PipelineTaskCheckPTS::sendFrame` (line 2454-2457) rewrites a frame whose PTS is below its predecessor. | **Low, and checked.** The model shows the patched PTS sequence is strictly increasing (0, 1001, 2002, 3003, 4004, …), so that branch is not reached. | Covered by the model; must be re-checked at runtime. |
| R5 | **Seek / trim interaction.** `m_trimParam.offset` is 0 on this corpus, but a seekable stream could re-enter the same code with `offset > 0`. | **Low but real.** Not measured. | Test `--seek` and `--trim` on Sony material on the patched build. |
| R6 | **Upstream divergence.** Carrying a local rigaya patch adds maintenance cost and drifts from official releases. | **Process risk, not a correctness risk.** | Prefer reporting upstream; pin an exact rigaya tag and keep the patch as a tracked file. |
| R7 | **Other rigaya tools.** `QSVEncC`'s `avqsv` reader shows the identical deficit (Phase 1: 146/146). The two tools share the reader but have separate pipeline headers. | **The same class of defect likely exists in the QSVEncC/VCEEncC pipeline headers and must be re-located there** — the patch above is NVEncC-only. | Locate the corresponding `getOutputFrame()` in the QSVEncC tree before claiming a fix for `--avhw` in general. |

## Recommendation

1. **Promote the root cause to `Confirmed`** and correct the Phase 1
   hypothesis. `root-cause.md` RC-1/RC-2 describe the loss as belonging to
   the "rigaya reader layer" and RC-9 marks the exact line as `Unconfirmed`.
   The loss is in fact in the **hardware-decode pipeline task**
   (`PipelineTaskNVDecode`), not in the demuxer/reader
   (`RGYInputAvcodec`), and the exact line is now identified.
2. **Keep `--avsw` as the default in 1KeyTranscoder unchanged.** Phase 1's
   operational recommendation stands and is unaffected by this analysis.
3. **Do not depend on the PoC patch for production.** It is unvalidated at
   runtime here, and R1/R5/R7 are open.
4. **Report upstream first.** This is a clean, small, self-contained rigaya
   bug with an exact reproduction (two commands), a predicted count formula
   (`container_samples − pictures_before_first_keyframe`), and a two-line
   fix. Upstreaming is cheaper and more durable than carrying a fork.
5. **Validate before any adoption**, in this order:
   a. build a debug NVEncC from tag 9.31 on a machine with the CUDA Toolkit;
   b. reproduce `20260903_C1170` → 30 / 27 / **30**;
   c. run the three Phase 1 controls (long XAVC HS, H.264 4:2:2, x265
      re-encode) and confirm XAVC HS and H.264 both reach `container_samples`
      while the control is unchanged;
   d. run the pattern-A sample to close R1, `--seek`/`--trim` to close R5,
      and a ≥ 10 000-frame clip to close R3;
   e. only then locate and patch the QSVEncC/VCEEncC equivalents (R7).
6. **FFmpeg `-hwaccel` remains the recommended hardware path** for
   1KeyTranscoder (Phase 1 §12) — it is proven frame-exact and requires no
   third-party patch. This phase strengthens that recommendation rather than
   changing it: the defect is now known to be in rigaya's pipeline code, not
   in the hardware.

### Verdict

```text
FIXABLE_SMALL_PATCH
```

Basis: one file, one header-only class, two lines of behaviour change plus
removal of an unreachable loop; the patch applies cleanly to pristine tag
9.31 and reproduces the target counts in a validated frame-accounting model of
the exact loop. It is **not yet runtime-validated** because no CUDA compiler
or NVDEC headers exist in this environment — that, and the open risks
R1/R3/R5/R7, are what stand between `FIXABLE_SMALL_PATCH` and a shippable fix.

---

## Upstream record

| Field | Value |
|---|---|
| Repository | `https://github.com/rigaya/NVEnc` |
| Tag / commit | `9.31` (matches `NVEncC (x64) 9.31 (r4047) by rigaya, Aug 8 2026`) |
| Version string | `VER_STR_FILEVERSION "9.31"` (`NVEncCore/rgy_version.h`) |
| Local clone | `work/rigaya-src/NVEnc` (shallow, tags `9.31 9.33 9.34 9.35`) — **not committed** |
| Patch | `work/poc-patches/0001-minimal-leading-picture-fix.patch` (3 907 bytes) |
| Patched reference | `work/poc-patches/NVEncPipeline.h.patched` |
| Apply | `git -C work/rigaya-src/NVEnc apply work/poc-patches/0001-minimal-leading-picture-fix.patch` |
| Build instructions | `work/rigaya-src/NVEnc/Build.en.md` §Windows — VS 2022 + CUDA ≥ 10.1 + AviSynth+ SDK + VapourSynth SDK, then `ffmpeg_lgpl.7z` from `rigaya/ffmpeg_dlls_for_hwenc` (releases `20250830`), then MSBuild `DebugStatic` / `RelStatic` |

`work/` is gitignored by project convention, so the third-party tree and the
patch are **not** committed; this section plus the patch text quoted above
are the durable record.

## Reproduction (all commands used in this phase)

```powershell
$nv  = 'F:\1KeyTranscoder\tools\NVEncC_9.31_x64\NVEncC64.exe'
$FF  = 'F:\1KeyTranscoder\tools\ffmpeg.exe'
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'

# --- 1. the defect, encoder removed from the loop ---------------------------
& $nv -i $src --avsw -c raw --output-res 64x64 -o NUL          # encoded 30
& $nv -i $src --avhw -c raw --output-res 64x64 -o NUL          # encoded 27

# --- 2. reader accounting: proves the reader drops nothing ------------------
& $nv -i $src --avhw -c raw --output-res 64x64 --log-level debug -o NUL |
    Select-String 'found first key frame'                      # offset 0

# --- 3. decoder accounting: proves NVDEC emits every frame ------------------
& $nv -i $src --avhw -c raw --output-res 64x64 --log-level trace -o NUL |
    Out-File work\raw\hw-trace.txt
(Select-String -Path work\raw\hw-trace.txt -Pattern 'DecPictureDisplay').Count   # 30
(Select-String -Path work\raw\hw-trace.txt -Pattern 'Free input frame').Count    # 27

# --- 4. packet list (reader -> decoder), decode order -----------------------
& $nv -i $src --avsw -c raw --output-res 64x64 --log-packets work\raw\sw -o NUL

# --- 5. frame-level head-truncation proof ----------------------------------
& $nv -i $src --avsw -c raw --output-res 64x64 --output-csp yuv420 --output-depth 8 -o work\raw\yuv\sw.yuv
& $nv -i $src --avhw -c raw --output-res 64x64 --output-csp yuv420 --output-depth 8 -o work\raw\yuv\hw.yuv
python work\verify_truncation.py work\raw\yuv\sw.yuv work\raw\yuv\hw.yuv 64

# --- 6. frame-accounting model: original vs patched -------------------------
# (MSVC) cl /nologo /std:c++17 /EHsc /Fo:model.obj /Fe:model.exe work\model_getoutputframe.cpp
.\work\m4.exe
```

## Artifacts produced by this phase

| Path | Role |
|---|---|
| `docs/hardware-decode/rigaya-avhw-analysis.md` | this report |
| `work/poc-patches/0001-minimal-leading-picture-fix.patch` | the PoC patch (applies to pristine 9.31) |
| `work/poc-patches/NVEncPipeline.h.patched` | patched file for reference |
| `work/model_getoutputframe.cpp` | standalone model of the exact loop |
| `work/verify_truncation.py` | tolerant signature head-truncation proof |
| `work/cmp_yuv.py` | signature alignment/offset search |
| `work/make_poc_patch.py` | patch generator |
| `work/raw/*-trace.txt` | raw `--log-level trace` runs, five fixtures |
| `work/rigaya-src/NVEnc` | rigaya source at tag 9.31 (not committed) |

## Confidence summary

| Statement | Confidence |
|---|---|
| NVDEC emits **all** frames, in display order, including leading pictures | **`Confirmed`** (callback count 30/30, 195/195, 360/360, 105/105) |
| `RGYInputAvcodec::getSample()` does **not** drop packets on Sony material | **`Confirmed`** (`offset 0` on all fixtures; identical for `--avsw`) |
| The loss is in `PipelineTaskNVDecode::getOutputFrame()`, `NVEncPipeline.h:1665-1667` | **`Confirmed`** (decoder exact; `CheckPTS` first frame is the IRAP; arithmetic + model reproduce all measured counts) |
| `m_hwDecFirstPts` is the first **packet's** PTS, misused as the first **presented** PTS | **`Confirmed`** (`NVEncPipeline.h:1550`; `Set packet #0, pts 3003`) |
| Deficit `= pictures_before_first_keyframe`, because `targetStart = size − 1` | **`Confirmed`** (matches Phase 1's 297/297 predictor) |
| Why x265 re-encode does not trigger: `pictures_before_first_keyframe == 0`, so the drop block is skipped | **`Confirmed`** (measured: control 30/30; model: patched == original) |
| A two-line patch restores `container_samples` frames with monotonic PTS | **`Likely`** (validated by model against real decoder output; **not** runtime-validated) |
| The same defect exists in the QSVEncC/VCEEncC pipelines | **`Unconfirmed`** — must be re-located per tool |
| Pattern A / pattern B behaviour is preserved | **`Unconfirmed`** (reasoned; reference samples unavailable) |

---

# Close-out (research branch `research/rigaya-avhw-source`)

Added when this branch was closed out. **No new experiment was run for this
section.** It records the final disposition of the claims above, marks the three
statements that must **not** be made, and points at where each claim was
independently validated after this document was written.

## C1. What this branch was for, and what it is not

| | |
|---|---|
| Question | *Where* in rigaya's own source do Sony's leading pictures disappear, and is the shared reader responsible? |
| Method | source read + the shipped binary's own `--log-level debug` / `trace` / `--log-packets` / `--log-framelist` output + a standalone model of the exact loop |
| **Not** in scope | building or running a patched binary (impossible here: no `nvcc`, no NVDEC headers — §Patch feasibility) |

The branch's deliverable is therefore **code-level attribution with an explicit
evidence ceiling**, not a validated fix. The fix was validated later, on a
different branch, on a different machine configuration.

## C2. Proven — as confirmed statements

These are the sentences this branch is entitled to assert. Each was derived here
and none has been weakened since.

| # | Statement | Evidence in this document | Final status |
|---|---|---|---|
| P1 | The **shared reader is not the frame-loss root cause**. `rgy_input_avcodec.cpp/.h` is the demuxer for `--avsw` and `--avhw` alike, and a defect that fires only under `--avhw` cannot live in code both paths share. | `rgy_input.cpp:629-692` constructs `RGYInputAvcodec` for both formats; the keyframe gate at `:3405` logs `offset 0` on every fixture for both readers | **Stands**, and was independently re-confirmed at frame-hash level by the QSVEncC line (`docs/hardware-decode/qsvencc-root-cause.md` §6.2) |
| P2 | **NVEncC's hardware output-stage filtering is one independent root cause.** `PipelineTaskNVDecode::getOutputFrame()` (`NVEncCore/NVEncPipeline.h`) treats `m_hwDecFirstPts` — the PTS of the first packet *fed to the decoder*, i.e. the first IRAP — as the first PTS that should be *presented*, and discards everything below it. | Decoder callback log (`DecPictureDisplay` 30/30, 195/195, 360/360, 105/105) shows every picture is produced; `CheckPTS`'s first frame is the IRAP; the loop arithmetic `targetStart = size − 1` reproduces all measured counts | **Stands.** Later runtime-proven: the frames are recovered by the patch below, and the patched output is byte-identical to `--avsw` |
| P3 | **QSVEncC's pipeline admission filter is a second, independent root cause**, in different code. `PipelineTaskMFXDecode::sendBitstream()` (`QSVPipeline/qsv_pipeline_ctrl.h`) rejects every MFX output presented before the first fed packet's PTS. | This document only *predicted* it (R7: "the same class of defect likely exists in the QSVEncC pipeline headers and must be re-located there"). It was then located and patched on `research/rigaya-qsvencc-avhw`. | **Stands as a prediction that was subsequently confirmed** — but the confirmation lives on the QSVEncC branch, not here |
| P4 | **`FramePosList::setPocAndFix` is an independent metadata-table issue.** It removes entries whose PTS precedes the first keyframe from the reader's frame-position table (`<= 16` anti-wraparound bound). | `nvencc-second-path-analysis.md` §4-§7: the same prune fires **identically** for `--avsw` and `--avhw` (`--log-framelist` byte-identical, `trim=3` on both), and `--avsw` still delivers all frames | **Stands.** It is a table/reporting defect, not a frame-delivery defect |
| P5 | **The trigger is a stream-shape property, not a codec and not a container.** `avhw_frames = container_samples − pictures_before_first_keyframe`. | Measured on 5 fixtures here; Phase 1 validated the same predictor on 297/297 runs | **Stands** for the measured corpus (XAVC HS, XAVC S H.264 4:2:2, x265, MKV, synthetic, DJI) |

## C3. Must NOT be claimed

Stated explicitly because each of these is a tempting over-read of this
document's contents.

| # | Do not claim | Why |
|---|---|---|
| N1 | **That `setPocAndFix` caused the observed frame loss.** | Directly refuted by measurement: the prune fires identically on `--avsw` (which delivers every frame) and on `--avhw`. It removes *table entries*, not delivered frames. It is a separate defect and is **not** part of any patch. `nvencc-second-path-analysis.md` §8 gives four independent reasons it must stay out of the fix. |
| N2 | **That all rigaya versions, or all rigaya tools, have the same bug.** | Only two pinned revisions were examined: `rigaya/NVEnc` tag `9.31` (`2cb9d81`) and `rigaya/QSVEnc` `b14c965` (= 8.26). Nothing was checked between or after those revisions. The rule is common to the two implementations; *"checked on two pinned revisions"* is the claim, not *"all versions"*. |
| N3 | **That all codecs / containers / Sony material are affected.** | Measured on XAVC HS (HEVC Main10 4:2:0, 3 leading pictures) and XAVC S (H.264 High 4:2:2 10-bit, 2 leading pictures), in MP4 and MKV, plus controls. All-I, 8-bit and 1080p material was not exercised. The conditional statement "any stream whose first IRAP is not first in presentation order is affected" is an *inference from the mechanism*, consistent with every measurement here — not a measured result over all Sony media. |
| N4 | **That this branch's patch was runtime-validated.** | It was not, in this environment. That claim belongs to `research/rigaya-nvencc-avhw`, whose build and regression matrix are recorded there. |
| N5 | **That the fixture E/F crop is a frame-loss defect.** | It is `AV_PKT_FLAG_DISCARD` on the leading samples, honoured by `getSample()`, and FFmpeg's own decode agrees (27, not 30). Recorded in `nvencc-second-path-analysis.md` §5. |

## C4. Where each claim was later validated

This branch deliberately does not extend its own experiments. The runtime
evidence for the fixes lives on the sibling branches:

| Claim from this branch | Validated at | Result |
|---|---|---|
| NVEncC output-stage drop site (P2) | `research/rigaya-nvencc-avhw` — built patch + 13 assertions × 16 records, seek/trim matrix, long-run sweep | Sony `n−3 → n`; controls unchanged; output byte-identical to `--avsw` |
| QSVEncC admission filter (P3) | `research/rigaya-qsvencc-avhw` — instrumented build, per-frame SHA-256 A/B | Sony `n−3 → n`; `avqsv` reader retained; refusal and controls unchanged |
| `setPocAndFix` non-impact (P4) | `nvencc-second-path-analysis.md` (this branch) + `qsvencc-root-cause.md` §7 | Confirmed on both tools independently |
| CPU economics of the fix | `research/hwdecode-e2e-benchmark` | 2.5–2.7× less CPU per frame; not faster |

## C5. Provenance of the two patches, re-verified at close-out

Re-checked when this branch was closed, against **freshly cloned pristine
upstream checkouts** (not against the build trees):

| | NVEncC | QSVEncC |
|---|---|---|
| Upstream | `rigaya/NVEnc` tag `9.31`, commit `2cb9d810c045202548b98ff130b12bc764eb39ea` (`VER_STR_FILEVERSION "9.31"`) | `rigaya/QSVEnc` commit `b14c9652b34b998351516cc63c86eb990c01c7c8` (`VER_STR_FILEVERSION "8.26"`) |
| Patch file | `0001-avhw-keep-leading-pictures.patch` | `0001-avhw-keep-leading-pictures.patch` |
| Patch sha256 | `53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc` | `5621ebb0f0277743a3bb5f18176134a134857d72e8a67f7f452c63dd08e53d98` |
| Files touched | 2 (`NVEncCore/NVEncPipeline.h` +11, `NVEncCore/rgy_input.h` +7) | 2 (`QSVPipeline/qsv_pipeline_ctrl.h` +10/−1, `QSVPipeline/rgy_input.h` +6) |
| Deletions | 0 | 1 line replaced (the condition) |
| `git apply --check` on pristine | **exit 0** | **exit 0** |
| Patched file hashes equal the built tree | **yes**, byte-for-byte (`daed88a7…`, `e03e828e…`) | **yes**, byte-for-byte once the tested tree's instrumentation-only lines and one stray blank line are excluded |

Both patches touch research/build trees only. Neither is applied to, or required
by, anything in `main`.

## C6. Close-out verdict

```text
research/rigaya-avhw-source
  verdict   : SOURCE ARCHAEOLOGY COMPLETE — READY TO ARCHIVE
  delivers  : code-level attribution for both root causes, the reader
              exoneration, the setPocAndFix separation, and the predictor's
              derivation from source
  does not  : validate a patch, claim version or corpus generality, or
              propose any production change
  fix status: out of scope here; see research/rigaya-nvencc-avhw and
              research/rigaya-qsvencc-avhw
```

The one operational recommendation in this document that was **superseded** is
§Recommendation item 6 ("FFmpeg `-hwaccel` remains the recommended hardware
path"), which was written when no patched rigaya build existed. The final
cross-branch position is recorded in `docs/hardware-decode/research-conclusion.md`.
Item 2 — *keep `--avsw` as 1KeyTranscoder's default* — was **not** superseded:
hardware decode stays non-default until an integration session validates it.
