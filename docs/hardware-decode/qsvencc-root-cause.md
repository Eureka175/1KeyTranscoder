# QSVEncC Sony 3-Frame Loss — Source-Level Root Cause

`research/rigaya-qsvencc-avhw` · base `main` @ `15cf218` · worktree `F:\1KT-qsv`

Subject: **QSVEncC 8.26 (r4504)**, source pinned at `rigaya/QSVEnc` `b14c965`,
built locally from that revision, on Intel Arc 140T / driver `32.0.101.8974`.

> **Why this document exists.** Phase 1 established *that* `QSVEncC --avhw`
> emits `n − 3` on Sony XAVC while `--avsw` and FFmpeg are exact. It stopped at
> "reader bug / decoder bug / VPP bug". This document goes to the statement
> level: file, class, function, condition — and then proves it by building the
> encoder and instrumenting it, rather than by reading alone.

---

## 0. Verdict

```
ROOT CAUSE:  Confirmed
PATCH:        Runtime Proven (PoC, built and A/B measured)
VERSION SCOPE: Runtime-proven on QSVEncC 8.26 pinned revision;
               NOT yet a general claim for later releases.
NEXT:         integration (decision to adopt upstream, or keep avoiding --avhw)
```

> **Branch close-out.** The consolidated provenance record for this patch —
> patch sha256, clean-apply proof against a freshly cloned pristine baseline at
> `b14c965`, tested-binary hash, build deviations, A/B matrix, and the claim
> boundary above — is [`qsvencc-patch.md`](qsvencc-patch.md). The recommendations
> in §9 were written when the patch was a fresh PoC; §9 now carries its final
> status inline. Nothing measured in this document is retracted.

| Question | Answer | Confidence |
|---|---|---|
| Which stage loses the 3 frames? | **MFX decode output admission**, inside the pipeline task | `Confirmed` |
| Which file? | `QSVPipeline/qsv_pipeline_ctrl.h` | `Confirmed` |
| Which class? | `PipelineTaskMFXDecode` | `Confirmed` |
| Which function? | `sendBitstream()` | `Confirmed` |
| Which condition? | the frame-admission filter on `surfDecOut` (line 1136-1138) | `Confirmed` |
| How many frames does it drop? | exactly 3 = the count of pictures presented before the first packet's PTS | `Confirmed` |
| Is it the same as the NVEncC defect? | **Same conceptual bug, different code and different file** | `Confirmed` |
| Does a patch restore `n`? | **Yes — and the result is byte-identical to `--avsw`** | `Confirmed` |

**One-line statement of the defect:**

> `PipelineTaskMFXDecode` treats the **presentation timestamp of the first fed
> packet** as the output-start boundary for hardware decode, and discards every
> decoded picture that is presented before it. Sony XAVC is precisely the stream
> shape where that assumption is false, because its leading pictures are
> presented before the first keyframe.

---

## 1. Method — why a build was necessary

Reading the source produced two plausible candidates and one contradiction that
static analysis could not settle:

* `FramePosList::setPocAndFix()` verifiably **pops** pictures
  (`rgy_input_avcodec.h`), and the count matched — but it behaves identically for
  `--avsw`, which does **not** lose the frames;
* `PipelineTaskMFXDecode::sendBitstream()` has a PTS-admission filter whose
  `m_decFrameOutCount > 0` clause appeared to let all but the first frame
  through — so it looked like it could drop **at most one**, not three.

Neither reading explained the measured `n − 3`. The question was therefore
settled by building the pinned revision and instrumenting it.

**Build** (`work/qsvbuild/`): MSVC 14.51, `ReleaseStatic|x64`, OneVPL built from
the vendored `libvpl` submodule with Ninja, `ffmpeg_lgpl` reused from the
NVEncC experiment line. Three local, documented deviations, none of which touch
the code under study:

| Deviation | Reason |
|---|---|
| `ENABLE_AVISYNTH_READER 0`, `ENABLE_VAPOURSYNTH_READER 0` | SDKs not installed; both readers are `#if`-guarded and unused here |
| `ENABLE_OPENVINO 0` | runtime not installed; backs AI VPP filters only |
| `BuildVPL.bat` prebuild bypassed | hardcodes the VS2022 CMake generator; OneVPL was built separately instead |

**Faithfulness check — the build reproduces the shipped behaviour exactly:**

```
shipped 8.26 : --avsw 30, --avhw 27
local   8.26 : --avsw 30, --avhw 27
```

Instrumentation is gated behind `RGY_AVHW_TRACE`, so the binary is inert unless
the variable is set.

---

## 2. The ledger — where `n → n − 3` actually happens

Instrumented run on `A_sony_copy.mp4` (30 container samples, 3 leading
pictures), output traced at every stage of the path:

```
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

The bottleneck is unambiguously the **admission filter**: MFX decoded **all 30**
pictures, and the pipeline threw 3 of them away.

### 2.1 The exact decision, per frame

Raw instrumented output (first real MFX outputs). `count` is
`m_decFrameOutCount`, i.e. how many frames have been *emitted* so far:

```
[MFXDEC] out ts=0     firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=1001  firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=2002  firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=3003  firstPts=3003 gotFirstPts=1 decFrameOutCount=0 lastSync=1 surf=1
[MFXDEC] out ts=4004  firstPts=3003 gotFirstPts=1 decFrameOutCount=1 lastSync=1 surf=1
[MFXDEC] out ts=5005  firstPts=3003 gotFirstPts=1 decFrameOutCount=2 lastSync=1 surf=1
```

with the filter condition evaluated against each:

| MFX output ts | `m_firstPts` | `m_decFrameOutCount` | `m_firstPts <= ts` | decision |
|---|---|---|---|---|
| **0** | 3003 | 0 | false | **REJECT** |
| **1001** | 3003 | 0 | false | **REJECT** |
| **2002** | 3003 | 0 | false | **REJECT** |
| 3003 | 3003 | 0 | true | accept |
| 4004 | 3003 | 1 | true | accept |
| 5005 | 3003 | 2 | true | accept |

`m_decFrameOutCount` stays **0** across all three rejections, because it is only
incremented when a frame is actually **emitted** (`qsv_pipeline_ctrl.h:1157`:
`taskSurf.frame()->setInputFrameId(m_decFrameOutCount++)`). The
`|| m_decFrameOutCount > 0` escape clause therefore never fires while the losses
are happening — which is why the static reading of "it can drop at most one" was
wrong. It drops as many as are presented before `m_firstPts`.

### 2.2 Why `m_firstPts` is 3003 and not 0

`m_firstPts` is the PTS of the **first bitstream fed to MFX**, taken at
line 1066-1069:

```cpp
} else if (!m_gotFirstPts) {
    m_firstPts = (int64_t)inputBitstream->TimeStamp;
    m_gotFirstPts = true;
}
```
(`qsv_pipeline_ctrl.h:1066-1069`)

The reader feeds packets in **decode order**, and the first packet is the IDR
with `pts=3003` (after libavcodec's `Offset DTS by 2002 to make first pts zero`).
The leading B-pictures carry `pts 0, 1001, 2002` and are presented *before* it.
MFX emits them in display order, so they arrive first and are all rejected.

**This is a property of the stream shape, not of the content:**

```
leading pictures = #{ i : PTS[i] < PTS[decode-order first packet] }
```

which is 3 for XAVC HS, 2 for Sony H.264 4:2:2, and **0 for DJI** — exactly
matching the measured losses and non-losses.

---

## 3. The exact code

### 3.1 The defect

`QSVPipeline/qsv_pipeline_ctrl.h`, class `PipelineTaskMFXDecode`, function
`sendBitstream()`, lines 1136-1138:

```cpp
if (surfDecOut != nullptr && lastSyncP != nullptr
    // 最初のフレームはOpenGOPのBフレームのために投入フレーム以前のデータの場合があるので、その場合はフレームを無視する
    && (!m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

`m_firstPts` = 1066-1069, `m_decFrameOutCount` incremented at 1157.

The comment states the intent honestly: *"the first frame may carry data from
before the fed frame because of an OpenGOP B-frame, so ignore the frame in that
case."* For an OpenGOP artefact that is right. For XAVC it is wrong, because
those pictures are not artefacts — they are real, decodable, and displayed.

### 3.2 Why the sink is reached at all

Sony XAVC packets are fed in decode order, IDR first, so nothing is filtered on
the way in. The `--avsw` path is fed the **same** packets, which is why the
loss is specific to the hardware task rather than to the reader.

---

## 4. Shared root cause with NVEncC? — Case A, with a precise boundary

The task asked for one of three verdicts. The answer is **Case A**, stated
carefully, because the two implementations are *not* the same code.

| | NVEncC 9.31 | QSVEncC 8.26 |
|---|---|---|
| File | `NVEncCore/NVEncPipeline.h` | `QSVPipeline/qsv_pipeline_ctrl.h` |
| Class | `PipelineTaskNVDecode` | `PipelineTaskMFXDecode` |
| Function | `getOutputFrame()` | `sendBitstream()` |
| Anchor state | `m_hwDecFirstPts` | `m_firstPts` |
| "already done" flag | `m_gotFrameAfterFirstPts` | `m_decFrameOutCount > 0` |
| Mechanism | bulk list-trim inside a seek-adjust loop | per-output admission test |
| Frames below anchor | released in a loop, last kept | each individually rejected |
| Shared file? | **No.** `NVEncPipeline.h` is NVEncC-only; `qsv_pipeline_ctrl.h` is QSVEncC-only | |

**Verdict — the two code bases are separate; the bug is common.**

Both encoders independently implement the same incorrect rule:

> treat `PTS(first fed packet)` as the hardware-decode output-start boundary and
> discard everything presented before it.

Because it is the same *rule*, it produces identical symptoms on identical
input (3 frames on XAVC HS, 2 on XAVC H.264 4:2:2, 0 on DJI). Because it is
*different code*, the fixes are not interchangeable — and this document does
**not** claim they should be unified without evidence. The QSV fix below is
derived from the QSV code, and the `--seek` regression test exists specifically
to check it did not quietly change a different behaviour.

An earlier hypothesis that the two readers share `rgy_input_avcodec.*` is
correct **only for the reader** (those files are byte-identical). The defect is
not in the reader at all — it is one layer further in, in the vendor-specific
pipeline task.

---

## 5. PoC patch

`work/qsvbuild/poc_patch.py` — two ASCII edits, no behaviour change outside the
target condition.

### 5.1 Expose the seek state to the pipeline

`QSVPipeline/rgy_input.h` (base class `RGYInput`, alongside `m_seek`):

```cpp
// Read start position (first = --seek, second = --seekto).
// For hardware decode the first fed packet's PTS may only be treated
// as the output start position when a seek was actually requested.
std::pair<float, float> getSeekParam() const {
    return m_seek;
}
```

### 5.2 Gate the rejection on an actual seek

`QSVPipeline/qsv_pipeline_ctrl.h`:

```cpp
// POINT OF FIX (docs/hardware-decode/qsvencc-root-cause.md):
// For streams whose first keyframe is not first in presentation
// order (XAVC), the leading pictures are always presented before
// the first fed packet, so their PTS is < m_firstPts. They are
// decodable (they follow the keyframe in decode order), so they
// must be kept. Only pictures before a REQUESTED seek position
// should be dropped, hence the filter is applied only when seeking.
const bool bSeeked = (m_input != nullptr && m_input->getSeekParam().first > 0.0f);
if (surfDecOut != nullptr && lastSyncP != nullptr
    && (!bSeeked || !m_gotFirstPts || m_firstPts <= surfDecOutTimestamp || m_decFrameOutCount > 0)) {
```

**Why this is the minimal correct shape.** The filter's only legitimate purpose
is to honour `--seek`. With no seek requested, no picture is "before the start",
so the test must not run. The rejection set becomes empty exactly when
`m_input->getSeekParam().first <= 0.0f`.

**Known limitation of the PoC:** a seek requested as exactly `--seek 0` is
indistinguishable from no seek, because the predicate is `first > 0.0f`. That
is the same convention the NVEncC fix uses; it is called out here rather than
hidden. It is not observable in the validation below.

---

## 6. Validation — 7 families

### 6.0 Frame counts (A/B, shipped vs patched)

| Case | samples | `--avsw` | `--avhw` shipped | `--avhw` patched | verdict |
|---|---|---|---|---|---|
| Sony container copy | 30 | 30 | **27** | **30** | ✅ restored |
| Sony C1170 4K60 | 30 | 30 | **27** | **30** | ✅ restored |
| Sony C0886 4K60 360f | 360 | 360 | **357** | **360** | ✅ restored |
| Sony C1158 4K60 150f | 150 | 150 | **147** | **150** | ✅ restored |
| Sony H.264 4:2:2 10-bit | 195 | 195 | refusal | refusal | ✅ unchanged |
| Sony H.264 4:2:2 10-bit #2 | 195 | 195 | refusal | refusal | ✅ unchanged |
| DJI control 105f | 105 | 105 | 105 | 105 | ✅ unchanged |
| DJI control 297f | 297 | 297 | 297 | 297 | ✅ unchanged |
| x265 re-encode control | 30 | 30 | 30 | 30 | ✅ unchanged |
| synthetic testsrc2 control | 60 | 60 | 60 | 60 | ✅ unchanged |

Reader identity on every patched run is still `avqsv` — the fix does not
silently route to software. `--avsw` frame counts are unchanged in every row.

### 6.1 Frame fingerprint (the decisive test)

Per-frame SHA-256 of the raw output, same geometry and same rigaya VPP chain on
both sides, so the comparison is a valid same-pipeline comparison:

| Comparison | frames | identical |
|---|---|---|
| **patched `--avhw` vs `--avsw`** | 30 vs 30 | **True** |
| shipped `--avhw` vs `--avsw` | 27 vs 30 | False — `test[0]` maps to `ref[3]` |

The patched hardware path does not merely produce the right **count**; it
produces the **same pictures, in the same order**, as the software path. The
shipped path is confirmed once more to be a strict 3-frame head truncation.

### 6.2 PTS

Frame identity above is established on the decoded picture stream; the raw
output is written frame by frame, so an identical ordered hash sequence also
means the emitted picture order (and therefore the presentation sequence handed
to the encoder) matches. No PTS rewriting was introduced: the patch only stops
discarding outputs, and `PipelineTaskCheckPTS` — the stage that rewrites
timestamps — is untouched.

### 6.3 Seek regression

The gate exists to protect `--seek`, so this is the critical regression test:

| `--seek` | shipped `--avhw` | patched `--avhw` | same |
|---|---|---|---|
| 1.0 s | 237 | 237 | ✅ |
| 2.5 s | 177 | 177 | ✅ |
| 4.0 s | 57 | 57 | ✅ |

Frames before a requested seek position are still dropped, exactly as before.

### 6.4 Trim

| `--trim` | `--avsw` | `--avhw` shipped | `--avhw` patched |
|---|---|---|---|
| 0:29 | 27 | 27 | 27 |
| 3:29 | 27 | 27 | 27 |
| 10:20 | 11 | 11 | 11 |

Unchanged. (`--trim` on its own drops the leading pictures in *both* readers;
that is the separate behaviour characterised in §7 and is out of scope here.)

### 6.5 Capability refusal — still a refusal, still not a frame drop

H.264 High 4:2:2 10-bit is **unaffected**: `avqsv: codec h264(yuv422p10le)
unable to decode by qsv.`, no reader constructed, no output file, non-zero exit.
The patch does not and cannot turn a capability refusal into a decode. This is
the Phase-1 distinction holding under modification: **`unsupported` is still
not `frame drop`.**

### 6.6 DJI and lead-free controls

Zero leading pictures ⇒ the filter had nothing to reject ⇒ patched output is
byte-for-byte the same behaviour as shipped (105/105, 297/297, 30/30, 60/60).

### 6.7 GPU pipeline preserved

The patch removes *rejections*; it does not add a download. Decode surfaces
still flow `MFXDEC-MFXVPP` under `--avhw`, and the debug log still shows
`avqsv` with the encoder consuming video-memory surfaces. No CPU round-trip is
introduced.

---

## 7. A second, independent site — characterised, and NOT part of this loss

There is a second place in the code base that discards leading pictures, and it
must not be conflated with the one above.

`QSVPipeline/rgy_input_avcodec.h:654-671`, class `FramePosList`, function
`setPocAndFix()`:

```cpp
for (; m_nextFixNumIndex < nSortFixedSize; m_nextFixNumIndex++) {
    if (m_list[m_nextFixNumIndex].data.pts < m_firstKeyframePts //ソートの先頭のptsが塚下キーフレームの先頭のptsよりも小さいことがある(opengop)
        && m_nextFixNumIndex <= 16) { //wrap arroundの場合は除く
        //これはフレームリストから取り除く
        m_list.pop();
        ...
```

with `m_firstKeyframePts` set at `:321` from the first pushed packet.

**Measured behaviour on C1170 — identical for both readers:**

```
[READER] EOF frameNum=30 fixedNum=0
[FPL] ctx=checkPtsStatus entry nSortedSize=30 listSize=30 firstKeyframePts=3003 sortFixed=13
[FPL]   REMOVE idx=0 pts=0    firstKeyframePts=3003
[FPL]   REMOVE idx=0 pts=1001 firstKeyframePts=3003
[FPL]   REMOVE idx=0 pts=2002 firstKeyframePts=3003
[READER] EOF frameNum=28 fixedNum=27
```

| | `--avsw` | `--avhw` shipped |
|---|---|---|
| FPL removals | **3** | **3** |
| MFX/pipeline drops | 0 | **3** |
| encoded | **30** | **27** |

**Conclusion — `Confirmed` by measurement:** this site removes the same three
pictures from the frame-position list in **both** readers, therefore it is
**not** the cause of the `--avhw`-only loss. It is a separate latent defect
(an OpenGOP heuristic that assumes the first keyframe is first in presentation
order), it is not exercised differently by the patch, and the patch does not
touch it. It is recorded because §2's ledger would otherwise appear to contain
two competing explanations for the same three frames, and because anyone
patching this area needs to know that **fixing it alone would not have changed
the `--avhw` frame count**.

---

## 8. Answering the original question

> Find the concrete source file, function and condition where the Sony 3 frames
> are lost.

```
file      : QSVPipeline/qsv_pipeline_ctrl.h
class     : PipelineTaskMFXDecode
function  : sendBitstream()
condition : (surfDecOut != nullptr && lastSyncP != nullptr
             && (!m_gotFirstPts || m_firstPts <= surfDecOutTimestamp
                 || m_decFrameOutCount > 0))
state     : m_firstPts  (set at :1066-1069 from the first fed packet's PTS)
            m_decFrameOutCount (incremented at :1157 only on emit)
effect    : rejects every MFX output presented before m_firstPts, because
            the counter cannot advance while rejections are ongoing
```

Stage in the ledger where `n → n − 3` first appears: **MFX decode output
admission**, after MFX has already decoded all 30 pictures.

---

## 9. Status and next steps

```
ROOT CAUSE:  Confirmed      (instrumented, exact file/function/condition)
PATCH:       Runtime Proven (2-edit PoC, built from pinned 8.26, A/B measured)
             - Sony XAVC HS : n-3  -> n        (4/4 cases)
             - fingerprints : patched avhw == avsw, 30/30 identical
             - --seek       : unchanged (3/3)
             - --trim       : unchanged (3/3)
             - 4:2:2 refusal: unchanged (still a refusal)
             - controls     : unchanged (4/4)
SCOPE:       Runtime-proven on QSVEncC 8.26 pinned revision (b14c965);
             NOT yet a general claim for later releases (8.27-8.30 exist
             and were not examined).
NEXT:        integration
```

**Recommended, in order:**

1. **Do not use *stock* `--avhw` for Sony XAVC.** The defect is silent and exits
   0 — the worst failure mode. With the patch applied the defect is gone on the
   measured corpus, but the patch is a maintained fork, not a shipped fix.
2. **If this is to be upstreamed to rigaya**, the patch is small and
   self-contained (`rgy_input.h` + `qsv_pipeline_ctrl.h`), but it should be
   offered as a **QSVEncC-specific** change with the `--seek` regression test
   attached, not as a "port of the NVEncC fix" — §4 shows the code paths are
   different.
3. Separately evaluate the `FramePosList::setPocAndFix` heuristic in §7. It is
   a real latent defect that currently happens not to change the output count;
   it should not be bundled into this fix, and it must not be assumed to have
   been fixed by it.
4. No production change to 1KeyTranscoder follows from this document. Nothing
   in `main` or in the project was modified.

**Final status of these recommendations (branch close-out).**

| # | Status |
|---|---|
| 1 | **Upheld.** Stock `--avhw` stays out; the patched path is an integration candidate only. Software decode remains the default. |
| 2 | **Upheld, and the shape is fixed:** the patch is QSVEncC-specific, seek-gated, and ships with the `--seek` regression. |
| 3 | **Upheld.** `FramePosList::setPocAndFix` is unpatched on every branch and is recorded as an independent follow-up issue in `docs/hardware-decode/research-conclusion.md`. |
| 4 | **Upheld.** No production change was made by this branch or by any of the four research branches. |

The §9 status block's version boundary stands and is restated in
[`qsvencc-patch.md`](qsvencc-patch.md): *runtime-proven on QSVEncC 8.26 pinned
revision; not yet a general claim for later releases.*

### Boundaries of this result

* Verified on **one machine** (Arc 140T, driver 32.0.101.8974) and one pinned
  source revision (`b14c965`, = 8.26). Later QSVEncC releases (8.27-8.30 exist)
  may have changed this code; that was not checked.
* The build used three documented deviations (§1). None touch the decoder,
  reader, pipeline or timestamp logic, and the unpatched build reproduces the
  shipped 27/30 exactly — but a fully faithful build was not produced.
* The PoC is **not** proposed as-is for production: the `--seek 0` edge case in
  §5.2 is unhandled, and no long-run or multi-stream soak was performed.
* Only the `--avhw` Sony class was investigated. Other rigaya readers
  (AVI/raw/AVS/VPY — the last two disabled in this build) were not examined.

---

## 10. Reproduction

Worktree `F:\1KT-qsv` · branch `research/rigaya-qsvencc-avhw`

| Artifact | Purpose |
|---|---|
| `third_party/QSVEnc` | `rigaya/QSVEnc` pinned at `b14c965` (= 8.26) |
| `work/qsvbuild/setup_build.ps1` | stage ffmpeg/libass/SDK environment |
| `work/qsvbuild/build.ps1` | MSVC `ReleaseStatic|x64` build, full log |
| `work/qsvbuild/instrument.py` | byte-safe instrumentation (trace-gated) |
| `work/qsvbuild/poc_patch.py` | the 2-edit PoC patch |
| `work/qsvavhw/trace_ledger.py` | parse the ledger, print accept/reject per frame |
| `work/qsvavhw/trace/*.trace.txt` | raw instrumented traces |
| `work/qsvavhw/ab_validate.ps1` | 10-case A/B frame-count matrix |
| `work/qsvavhw/ab_fingerprint.ps1` | fingerprint + seek + trim validation |
| `work/qsvavhw/ab/ab.json`, `ab/fp_seek_trim.json` | machine-readable results |
| `third_party/QSVEnc/_build/x64/ReleaseStatic/QSVEncC64.exe` | instrumented + patched build |

```powershell
# build
cd F:\1KT-qsv\work\qsvbuild
.\setup_build.ps1
.\build.ps1 -Config ReleaseStatic -Platform x64

# instrumented trace (ledger)
$env:RGY_AVHW_TRACE = '1'
& F:\1KT-qsv\third_party\QSVEnc\_build\x64\ReleaseStatic\QSVEncC64.exe `
    -i <sony.mp4> --avhw -c raw --output-res 256x144 -o NUL 2> trace.txt
Remove-Item Env:\RGY_AVHW_TRACE
python F:\1KT-qsv\work\qsvavhw\trace_ledger.py trace.txt

# A/B validation
.\ab_validate.ps1
.\ab_fingerprint.ps1
```
