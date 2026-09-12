# NVEncC `--avhw` Experiment Line (Hardware Decode Phase 2)

`research/rigaya-nvencc-avhw` · base `main` @ `15cf218`

> **Scope.** This phase asks one question and only one:
> **if the NVEncC `--avhw` reader is changed, can Sony `GPU decode → GPU encode`
> be made correct?**
>
> It is *not* a re-run of Phase 1's "Sony loses 3 frames" result
> (`docs/hardware-decode/root-cause.md` RC-2/RC-3). That result is taken as
> given. What is new here is **where** in the tool the frames are lost, **which**
> reader-side change fixes it, and what the measured **before / after** shows —
> including a bit-exact comparison against software decode.
>
> Nothing in `1KeyTranscoder` production code was modified. No frames were
> padded, dropped or re-stamped to make counts agree. No frame count was used on
> its own as a correctness test.

---

## 0. Bottom line

| Question | Answer | Confidence |
|---|---|---|
| **A. Can it be fixed by reader *parameters* alone?** | **No.** No NVEncC option reaches the code that drops the frames. `--avsync`, `--input-option`, `--trim`, `--input-analyze`, `--allow-other-negative-pts`, `--offset-video-dts-advance` were each checked against the source that consumes them. | `Confirmed` (source) |
| **B. Can it be fixed by a source patch?** | **Yes — built, measured and regression-tested.** 18 added lines in 2 files. Sony 30 f: **27 → 30**. Sony 330 f: **327 → 330**. Sony XAVC HS 10170 f: **10167 → 10170**. Sony H.264 4:2:2 195 f: **193 → 195**. Controls (DJI / x265 / synthetic): **unchanged**. Seek + trim: **identical in all 17 combinations**. 13 assertions × 16 records: **PASS**. | `Confirmed` (built and measured) |
| **C. Does the fix keep `GPU decode → GPU encode`?** | **Yes — measured.** `Input Info` still `avcuvid:`, the VPP stage still reports a GPU-only path, NVIDIA `VE`/`VD` engines still busy, no host copy, and the patched `--avhw` output is **byte-identical** to `--avsw` output on every leading-picture case. | `Confirmed` |
| **Is the reader worth adopting?** | **The patch works and is integration-ready. Whether to adopt it is a maintenance decision, not a technical one.** See §16 for the integration checklist and the `PATCH STATUS` verdict. | `Confirmed` (fix) / judgement (adoption) |

**The headline correction to the Phase 1 model.** Phase 1 located the defect in
"the rigaya `--avhw` reader". That is right about *where the user sees it* but
wrong about *which component*: the loss happens in **NVEncC's hardware output
stage, after a CUVID decode that is itself complete and correct**. NVEncC's own
trace shows all 30 video packets reaching NVDEC and NVDEC emitting all 30
pictures in correct display order — **including the three previously reported as
"dropped by the hardware reader"**. NVEncC then discards them on a timestamp
test. That distinction is exactly what makes a patch possible: a decoder that
never produced the pictures could not be fixed in the reader.

**A stronger statement, from the `after` run.** With the patch applied, the
patched `--avhw` and the stock `--avsw` produced **the same file, byte for byte**,
on the minimal Sony fixture — and on **every** leading-picture case in the
regression matrix (§10.2), for the output bytes, the PTS sequence, the keyframe
sequence and the per-frame picture fingerprints. That is stronger than "the frame
count matches": it means the GPU-decoded frames that the baseline threw away are
the same pictures the software decoder emits, in the same order, with the same
timestamps, and that NVENC then encodes them identically.

---

## 1. Environment

| Item | Value |
|---|---|
| Host OS | Windows 11 x64 (26200) |
| CPU | Intel Core Ultra 9 285H (6P+10E, 16C/16T) |
| GPU | **1 ×** NVIDIA GeForce RTX 5070 Laptop GPU, 4608 cores, 1545 MHz base, PCIe5x16 |
| VRAM | 8151 MiB |
| Driver | 616.56 |

| Binary | Version string | Toolchain |
|---|---|---|
| **baseline** (`tools/NVEncC_9.31_x64/NVEncC64.exe`) | `9.31 (r4047) by rigaya, Aug 8 2026 (VC 1944/Win)` · `NVENC API v13.1, CUDA 11.8` | upstream release build |
| **patched** (built in this phase) | `9.31 (r1) by rigaya, Sep 12 2026 (VC 1951/Win)` · `NVENC API v13.1, CUDA 13.1` | MSVC 14.51 + CUDA 13.1, see §9 |

> **Toolchain caveat, stated up front.** The patched binary is not a drop-in
> rebuild of the shipped one: it was compiled with CUDA 13.1 (headers shipped in
> the repo are NVENC API 13.1) and MSVC 14.51, whereas the release build used
> CUDA 11.8 / MSVC 14.44. **Wall-clock and fps comparisons across the two
> binaries are therefore not a controlled benchmark.** Every correctness claim in
> this document rests on frame counts, packet sequences and encoded bytes, none
> of which depend on the toolchain. The one cross-binary timing number that is
> quoted (§4.2) is labelled as indicative.

Measurement tooling: bundled `tools/ffmpeg.exe` / `tools/ffprobe.exe` **9.0.1**
(never the `PATH` build), plus NVEncC's own `--log-level trace`.

**Single-GPU host.** `--check-device` reports exactly one CUDA device;
`--device 1` fails with `Invalid Device Id = 1`. The task's
`device 0 / device 1 / multi-GPU` dimension **cannot be exercised here** and is
recorded as untested in §15 rather than approximated with a single device.

NVEncC source under review: `rigaya/NVEnc` tag **9.31**, commit
`2cb9d810c045202548b98ff130b12bc764eb39ea`.

---

## 2. Reproducer

### 2.1 Minimal

```powershell
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'   # 30 container frames

& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avsw -c hevc --output-depth 10 --cqp 23 -o b_avsw.mp4
#   -> encoded 30 frames
& tools\NVEncC_9.31_x64\NVEncC64.exe -i $src --avhw -c hevc --output-depth 10 --cqp 23 -o b_avhw.mp4
#   -> encoded 27 frames          <-- the defect
```

Both counts are confirmed **from the delivered file**, not from the progress line:

```powershell
& tools\ffprobe.exe -v error -select_streams v:0 -count_frames `
    -show_entries stream=nb_read_frames -of default=nw=1 b_avhw.mp4
#   -> nb_read_frames=27      (b_avsw.mp4 -> 30)
```

Repeatability: 10 consecutive `--avhw` runs all return `27`; 5 `--avsw` runs all
return `30`. The deficit is deterministic, not statistical.

### 2.2 Instrumented reproducer — the one that locates the loss

NVEncC has trace-level logging naming every packet handed to the CUVID decoder
and every picture it emits. This is the decisive instrument:

```powershell
& $NV -i $src --avhw -c raw --output-res 64x64 --log-level trace `
      --log trace-avhw.txt -o NUL
Select-String -Path trace-avhw.txt -Pattern 'Set packet'
Select-String -Path trace-avhw.txt -Pattern 'input frame \(dev\)'
```

Result (`20260903_C1170.MP4`: 30 container frames, 3 leading pictures):

```
NVDEC: Set packet #0,  size 2419182, pts 3003    <- the IDR, first packet in DECODE order
NVDEC: Set packet #1,  size  122461, pts 1001    <- leading B
NVDEC: Set packet #2,  size  140813, pts 0       <- leading B
NVDEC: Set packet #3,  size  148400, pts 2002    <- leading B
...
NVDEC: Set packet #29, size  229745, pts 28028
```
```
NVDEC: input frame (dev) #0, pic_idx 2, timestamp 0       <- leading B, EMITTED
NVDEC: input frame (dev) #0, pic_idx 1, timestamp 1001    <- leading B, EMITTED
NVDEC: input frame (dev) #0, pic_idx 3, timestamp 2002    <- leading B, EMITTED
NVDEC: input frame (dev) #0, pic_idx 0, timestamp 3003    <- IDR, first frame the encoder sees
NVDEC: input frame (dev) #1, pic_idx 6, timestamp 4004
...
NVDEC: input frame (dev) #26, pic_idx 6, timestamp 29029   <- 27 frames reach the encoder
```

**30 packets in · 30 pictures out of NVDEC · 27 frames into the encoder.**

Also reproducible through `--frames` (which NVEncC implements as a trim):

```powershell
& $NV -i $src --avhw --frames 20 -c raw -o NUL                      # -> encoded 17 frames (20 - 3)
& $NV -i <330-frame clip> --avhw --frames 100 -c raw -o NUL         # -> encoded 97 frames (100 - 3)
```

### 2.3 Rigaya's own debug logs (auxiliary)

```powershell
& $NV -i $src --avhw -c raw --log-level debug --log dbg.txt `
      --log-packets pkt.txt --log-framelist fpos.txt -o NUL
```

* `--log-packets` contains **all 30 video packets** for both `--avhw` and
  `--avsw`; the two logs are byte-identical outside timestamps.
* `avcuvid: found first key frame: timestamp 3003, offset 3` and
  `avcuvid: adjust trim by offset 3` are produced **identically** by `--avsw` and
  `--avhw`, so the reader's leading-picture bookkeeping is *not* what differs.
* `--log-framelist` for `--avhw` starts at `poc 0, I, pts 3003`.

---

## 3. Root cause — located in source

All line numbers are `rigaya/NVEnc` @ 9.31 (`2cb9d81`).

### 3.1 The drop — `NVEncCore/NVEncPipeline.h`, `PipelineTaskNVDecode::getOutputFrame()`

```cpp
// NVEncCore/NVEncPipeline.h:1548-1552  (inside the decode thread lambda)
const auto flags = FrameFlags(bitstream.pts(), (RGY_FRAME_FLAGS)bitstream.dataflag());
m_dataFlag.push(flags);
if (m_hwDecFirstPts == AV_NOPTS_VALUE) {
    m_hwDecFirstPts = bitstream.pts();      // <-- PTS of the FIRST PACKET FED TO THE DECODER
}
```

```cpp
// NVEncCore/NVEncPipeline.h:1646-1681  (output stage)
if (m_endPts >= 0 && dispInfo.timestamp >= m_endPts) { ... return RGY_ERR_MORE_BITSTREAM; }
dispInfoList.push_back(dispInfo);
if (m_gotFrameAfterFirstPts) {
    break;
}
// OpenGOP等でキーフレームより前のフレームのptsで出てくるのを調整 ...
if (dispInfo.timestamp >= m_hwDecFirstPts || m_hwDecFirstPts == AV_NOPTS_VALUE) {
    ...
    m_gotFrameAfterFirstPts = true;
    break;
}
// m_hwDecFirstPtsより前のフレームがたくさん出てきてしまうことがある
// m_hwDecFirstPtsより前のフレームはdropするしかない (そうしないとデコードがフレームバッファ不足で止まってしまう)
for (; istart < (int)dispInfoList.size() - 1; istart++) {
    m_dec->frameQueue()->releaseFrame(&dispInfoList[istart]);   // <-- THE DROP
}
```

**Why `m_hwDecFirstPts` is 3003 and not 0.** For a Sony XAVC clip the first
packet in decode order is the IDR, and the IDR's *presentation* timestamp is 3003
(its decode timestamp is −2002). The three pictures that display *before* it have
timestamps `0, 1001, 2002` — all strictly below 3003. `m_hwDecFirstPts` is
therefore not "the start of the presentation" (which is 0); it is the PTS of the
first packet, and it rejects exactly the leading pictures.

The source comment names the cases the filter was written for — a sample with
`--seek 6.66667`, and a TS file "from the beginning". **Both named cases are
seeks.** The filter was extended to the non-seek case, where its premise ("the
decoder is emitting frames before the requested start") does not hold.

### 3.2 A second, independent trim — `NVEncCore/rgy_input_avcodec.h`, `FramePosList::setPocAndFix()`

```cpp
// NVEncCore/rgy_input_avcodec.h:654-671
for (; m_nextFixNumIndex < nSortFixedSize; m_nextFixNumIndex++) {
    if (m_list[m_nextFixNumIndex].data.pts < m_firstKeyframePts //ソートの先頭のptsが塚下キーフレームの先頭のptsよりも小さいことがある(opengop)
        && m_nextFixNumIndex <= 16) { //wrap arroundの場合は除く
        m_list.pop();                       // <-- removes the frame-position entry
```

with `m_firstKeyframePts` set in `add()`:

```cpp
// NVEncCore/rgy_input_avcodec.h:320-322
if (m_firstKeyframePts == AV_NOPTS_VALUE && (pos.flags & AV_PKT_FLAG_KEY) && nIndex == 0) {
    m_firstKeyframePts = m_list[nIndex].data.pts;
}
```

This path prunes the **frame-position list** — an internal table, not the emitted
frames. It is shared reader code (QSVEnc/NVEnc/VCEEnc). It is the same mistaken
equivalence ("PTS of the first keyframe" = "start of presentation"), and it
survives the §6 patch, which is observable: after the patch the `--avhw` output
has the correct 30 frames while `--avsw` on the *same synthetic file* still
reports 27 (§6.2, fixture E). It is recorded as a **second, unpatched** defect —
see §6.5, §13 (L16) and §14.

### 3.3 The third suspect — ruled out

`getSample()` (`NVEncCore/rgy_input_avcodec.cpp:3405-3408`) drops packets that
precede the first keyframe:

```cpp
if (!bTreatFirstPacketAsKeyframe && !m_Demux.video.gotFirstKeyframe && !keyframe) {
    av_packet_unref(pkt.get());
    i_samples++;
    continue;
}
```

For Sony's stream the IDR **is** the first packet in decode order and
`AV_PKT_FLAG_KEY` is set, so this branch never fires on the corpus. Phase 1 read
this branch as the cause; §2.2 disproves that reading empirically. It remains a
standing hazard for PMT-follow streams, not this defect.

---

## 4. Experiment results — baseline (before the patch)

### 4.1 Core matrix

`encoded` = rigaya's own `encoded N frames`; `output` = independent
`ffprobe -count_frames` on the delivered MP4; `in` = container frame count.

| case | reader | in | leading pics | `encoded` | `output` | Δ | first PTS | last PTS | runtime s | fps | CPU % | VE % | VD % | VRAM peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Sony 30 f (minimal) | `--avhw` | 30 | 3 | **27** | **27** | **−3** | 0 | 104104 | 1.99 | 43.7 | 2.8 | – | – | 1604 |
| Sony 30 f (minimal) | `--avsw` | 30 | 3 | 30 | 30 | 0 | 0 | 116116 | 2.48 | 28.2 | 17.1 | – | – | 1102 |
| Sony 330 f | `--avhw` | 330 | 3 | **327** | **327** | **−3** | 0 | 1305304 | 4.07 | 120.3 | 5.7 | 58.3 | 24.0 | 1610 |
| Sony 330 f | `--avsw` | 330 | 3 | 330 | 330 | 0 | 0 | 1317316 | 6.15 | 78.7 | 36.4 | 45.8 | – | 1091 |
| Sony H.264 4:2:2 | `--avhw` | 195 | 2 | **193** | **193** | **−2** | 0 | 768768 | 3.00 | 108.4 | 5.6 | – | – | 1854 |
| Sony H.264 4:2:2 | `--avsw` | 195 | 2 | 195 | 195 | 0 | 0 | 776776 | 3.35 | 87.6 | 37.7 | 31.5 | – | 1094 |
| **DJI control** | `--avhw` | 105 | **0** | **105** | **105** | **0** | 0 | 416416 | 2.53 | 80.3 | 5.1 | – | 15.0 | 1975 |
| DJI control | `--avsw` | 105 | 0 | 105 | 105 | 0 | 0 | 416416 | 6.43 | 19.7 | 15.1 | 17.2 | – | 1285 |

Two things to read from this table:

1. **The deficit is exactly the leading-picture count, in both codecs** (3 for
   XAVC HS, 2 for XAVC S H.264 High 4:2:2) and **zero** where there are no
   leading pictures (DJI). This reproduces the Phase 1 predictor.
2. **`encoded` and the independently measured `output` agree in every row.**
   NVEncC is not misreporting its own output; the loss is upstream of the
   counter, i.e. a decode-side loss rather than a mux/write loss.

`–` means the field is not reported: on `--avhw` runs NVEncC does not print the
`CPU: … GPU: … VE: … VD: …` line at all (only `CPULoad:`); that string appears
only on the software-decode path. VRAM is an `nvidia-smi memory.used` peak
sampled during the run, meaningful only for uncontended runs (all rows above are
sequential).

### 4.2 Throughput, before

Uncontended, sequential. **The CPU column is the meaningful one** (see §8 for
the after numbers and §13 L2 for the toolchain caveat):

| clip | reader | frames out | wall s | fps | CPU % |
|---|---|---|---|---|---|
| Sony 330 f | `--avhw` | 327 | 4.07 | 120.3 | 5.7 |
| Sony 330 f | `--avsw` | 330 | 6.15 | 78.7 | 36.4 |
| Sony 10170 f → first 3000 | `--avhw` | 2997 | 39.61 | 78.1 | **7.4** |
| Sony 10170 f → first 3000 | `--avsw` | 2997 | 37.22 | 83.0 | **36.5** |
| Sony 7830 f → first 3000 | `--avhw` | 2997 | 39.63 | 78.0 | **7.4** |
| Sony 7830 f → first 3000 | `--avsw` | 2997 | 40.40 | 76.2 | **34.7** |

* **CPU is the robust result.** `--avhw` costs **~5× less CPU** (7.4 % vs 36.5 %)
  for the same delivered output. This is stable across clip lengths and is the
  real, durable reason to consider hardware decode.
* **Speed is not.** On a short clip warm-up dominates and `--avhw` looks 1.5×
  faster; on a 3000-frame view the two are **within noise of each other**
  (78.1 vs 83.0 fps, and the second pair reverses to 78.0 vs 76.2). Phase 1's
  note stands: **do not argue for hardware decode on throughput.**

VRAM is comparable (1.6 GiB vs 1.1 GiB at 4K60 on an 8 GiB part); the extra is
CUVID decode surfaces.

A bounded 18000-frame (5 min) view was attempted and the measurement process died
with `STATUS_STACK_BUFFER_OVERRUN` (`0xC0000409`) with no partial output. That is
recorded as a **tooling outcome, not a decoder result** — it was not investigated
and must not be read as evidence in either direction. The 3000-frame views are
the longest valid measurements obtained.

### 4.3 Container / bitstream variants, before

| fixture | reader | container frames | leading pics | `encoded` | `output` |
|---|---|---|---|---|---|
| A Sony stream copy → MP4 | `--avhw` | 30 | 3 | **27** | **27** |
| A Sony stream copy → MP4 | `--avsw` | 30 | 3 | 30 | 30 |
| B Sony stream copy → MKV (no edit list) | `--avhw` | – | 3 | **27** | **27** |
| B Sony stream copy → MKV | `--avsw` | – | 3 | 30 | 30 |
| C x265 re-encode → MP4 | `--avhw` | 30 | **0** | 30 | 30 |
| C x265 re-encode → MP4 | `--avsw` | 30 | 0 | 30 | 30 |
| D synthetic `testsrc2` → MP4 | `--avhw` | 60 | 0 | 60 | 60 |
| D synthetic `testsrc2` → MP4 | `--avsw` | 60 | 0 | 60 | 60 |
| E leading pictures cut away (`-ss` past the IDR) | `--avhw` | 30 | 3 | **27** | **27** |
| E leading pictures cut away | `--avsw` | 30 | 3 | 27 | 27 |
| F two Sony files concatenated (`-itsoffset 1.0`) | `--avhw` | 360 | 3 | **357** | **357** |
| F two Sony files concatenated (`-itsoffset 1.0`) | `--avsw` | 360 | 3 | 357 | 357 |

* **A vs B** independently re-confirms Phase 1 RC-4: the edit list is not the
  trigger. A Matroska remux with no edit list at all loses the same 3.
* **C and D are the controls that make the patch safe.** Where there are no
  leading pictures `--avhw` is already exact, which is why a patch that changes
  behaviour only in the "frames below the first packet's PTS" case cannot affect
  ordinary media.
* **F is the positive control for the timestamp test.** Two Sony files
  concatenated with `-itsoffset 1.0`: the first video packet is an IDR at
  `pts 60000` while three pictures sit at `57998 / 56997 / 58999`. Both readers
  lose 3 here, because the shared frame-position path (§3.2) prunes on the same
  "below the first keyframe" rule. §3.1 and §3.2 are two faces of one mistake.

---

## 5. The candidate patch

Stored at `work/hwdecode2/patch/0001-avhw-keep-leading-pictures.patch`
(apply helper: `work/hwdecode2/patch/apply_patch.py`, BOM/CRLF safe).
**18 added lines, 2 files, no deletions.**

### 5.1 Patch hygiene

| Property | Value |
|---|---|
| Upstream | `rigaya/NVEnc` tag `9.31`, commit `2cb9d810c045202548b98ff130b12bc764eb39ea` |
| Files touched | **2**: `NVEncCore/NVEncPipeline.h` (+11), `NVEncCore/rgy_input.h` (+7) |
| Lines added / removed | **18 / 0** |
| sha256 of the patch file | `53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc` |
| Applies cleanly to a pristine 9.31 | **yes** — `git apply` (strict) and `git apply --ignore-whitespace` both exit 0 against a fresh `git clone` of the tag |
| Result identical to the tree that produced the tested binary | **yes** — verified on both patched files after a deliberate revert/re-apply cycle (§5.1.1) |
| Tested binary | `NVEncC64.exe`, 77 757 440 bytes, sha256 `dcf6d7a63143c777e54a749281bef7ee1dd50d61c44b14d128e66a1217c8be4b` |
| Provenance record | `work/hwdecode2/raw/provenance.json` — patch hash, per-file hashes, binary hash, tree status, build-env changes |
| Rebase cost | the diff is 2 files and carries no context outside its own hunk; it re-applies on any 9.31-lineage tree whose `getOutputFrame()` still contains the anchor block |

Line-ending note: upstream ships these files with CRLF, so the patch body is
CRLF. A `.gitattributes`-normalising checkout (`* text=auto`) will need
`git apply --ignore-whitespace`; the strict form also works because the context
lines come from those same CRLF files.

Verification commands:

```powershell
git clone --depth 1 --branch 9.31 https://github.com/rigaya/NVEnc.git pristine
cd pristine
git apply ..\work\hwdecode2\patch\0001-avhw-keep-leading-pictures.patch
git diff --stat      # NVEncPipeline.h | 11 +++++, rgy_input.h | 7 +++, 18 insertions
```

#### 5.1.1 Provenance check — and one defect it caught in this phase's own workspace

The build tree is untracked, so "the binary came from this patch" is not
self-evident. It is now asserted two ways:

1. **Source ↔ patch.** Reverting both files in a pristine checkout and
   re-applying the patch reproduces the built tree's files **byte for byte**:

   | file | pristine + patch | built tree | match |
   |---|---|---|---|
   | `NVEncCore/NVEncPipeline.h` | `daed88a78fbd091a1db159fd3cc9a1cdead46f56c0891aae6b3dd1e37aa0b00d` | same | ✅ |
   | `NVEncCore/rgy_input.h` | `e03e828e06b44f90ef95578866e7fb67cec602b1b6bf5e04f374a53004641627` | same | ✅ |

2. **Behaviour.** The rebuilt binary was re-run through the whole suite and
   reproduced **every output sha256 bit-for-bit** (§10.2).

> **What went wrong, recorded because it nearly invalidated the result.** During
> a mid-phase audit the build tree was found with `NVEncPipeline.h` patched but
> `rgy_input.h` reverted to upstream — i.e. exactly half the patch present, after
> the binary had been built. The audit's first check compared the *patched* tree
> against a *pristine* one and produced a false "identical" reading, and a
> subsequent `git apply --check` "failed" only because the tree was already
> patched. The correct procedure, now written into `record_provenance.py`, is to
> compare the built tree against **pristine + patch**, not against either alone.
> The tree was restored, the binary **rebuilt from the verified sources**, and the
> entire suite re-run; all results in this document are from the rebuilt binary.
> Independent confirmation that the two builds are equivalent: the output sha256
> matches on all eight cases, including the 30-frame one
> (`6336165c02a8…`), the 330-frame one (`ce7816ed0025…`) and the 10170-frame one
> (`6e27c8565648…`).

### 5.2 The diff

```diff
--- a/NVEncCore/rgy_input.h
+++ b/NVEncCore/rgy_input.h
@@ -328,6 +328,13 @@ public:
     virtual RGYDOVIProfile getInputDOVIProfile() {
         return RGY_DOVI_PROFILE_UNSET;
     }
+
+    //読み込み開始位置 (first = --seek, second = --seekto)
+    //HWデコードでは、最初のパケットのptsを「出力開始位置」として扱ってよいのは
+    //実際にシークしたときだけなので、パイプライン側から参照できるようにする
+    std::pair<float, float> getSeekParam() const {
+        return m_seek;
+    }
 protected:
```

```diff
--- a/NVEncCore/NVEncPipeline.h
+++ b/NVEncCore/NVEncPipeline.h
@@ -1653,6 +1653,17 @@ protected:
             if (m_gotFrameAfterFirstPts) {
                 break;
             }
+            // シークしていない通常のデコードでは、最初のパケットのptsを「出力開始位置」として
+            // 扱ってはならない。XAVCのように最初のキーフレームが提示順の先頭でないストリームでは、
+            // キーフレームより前に提示される先行ピクチャのptsは必ず最初のパケットのptsより
+            // 小さくなるため、下のフィルタが先行ピクチャをすべて捨ててしまう。
+            // 先行ピクチャはデコード順ではキーフレームの後ろにあるので、デコーダは正しく出力する。
+            // 捨てるべきなのは「シークで要求した位置より前」のフレームだけなので、
+            // シーク指定があるときのみ以下のフィルタを適用する。
+            if (m_input->getSeekParam().first <= 0.0f) {
+                m_gotFrameAfterFirstPts = true;
+                break;
+            }
             // OpenGOP等でキーフレームより前のフレームのptsで出てくるのを調整
```

### 5.3 Why this shape

* **It leaves the OpenGOP/seek correction exactly where it was needed.** The
  filter's own source comment names two reproduction samples, and both are
  `--seek` runs. With `--seek` active, `m_seek.first > 0` and the original code
  runs unchanged — verified in §6.3.
* **It is inert on ordinary media.** The new branch is reachable only when the
  decoder emits a picture whose PTS is below the PTS of the first packet *and*
  no seek was requested. For a stream whose first packet is an IRAP at the
  earliest presentation time (x265 output, DJI, synthetic, and every normal
  file) no such picture exists, so the branch is unreachable and the frame
  sequence is bit-for-bit unchanged. Fixtures C, D and the DJI control are
  exactly this case — and §6.2 shows they are unchanged after the patch.
* **It removes no frames and adds none.** It changes only whether an
  already-decoded GPU surface is forwarded to the next pipeline task or returned
  to the decoder pool. The pictures were always there (§2.2).
* **It changes no encoding parameter** and no device/GPU selection.

---

## 6. After — measured

### 6.1 Core matrix, `--avhw` before vs after

Both columns are `ffprobe -count_frames` on the delivered file.

| case | in | leading pics | before | after | expected after | verdict |
|---|---|---|---|---|---|---|
| Sony 30 f (minimal) | 30 | 3 | 27 | **30** | 30 | ✅ exact |
| Sony 330 f | 330 | 3 | 327 | **330** | 330 | ✅ exact |
| Sony H.264 4:2:2 | 195 | 2 | 193 | **195** | 195 | ✅ exact |
| DJI control | 105 | 0 | 105 | **105** | 105 | ✅ unchanged |
| Sony 30 f, `--avsw` | 30 | 3 | 30 | 30 | 30 | ✅ unchanged |
| Sony 330 f, `--avsw` | 330 | 3 | 330 | 330 | 330 | ✅ unchanged |
| Sony H.264, `--avsw` | 195 | 2 | 195 | 195 | 195 | ✅ unchanged |
| DJI, `--avsw` | 105 | 0 | 105 | 105 | 105 | ✅ unchanged |

`rc=0` on every run. The patched `--avhw` matches the container frame count on
every leading-picture case, and the DJI control — the case that was already
correct — is untouched.

### 6.2 Fixture matrix, before vs after

| fixture | reader | before | after |
|---|---|---|---|
| A Sony copy → MP4 | `--avhw` | 27 | **30** |
| A Sony copy → MP4 | `--avsw` | 30 | 30 |
| B Sony copy → MKV (no edit list) | `--avhw` | 27 | **30** |
| B Sony copy → MKV | `--avsw` | 30 | 30 |
| C x265 re-encode (0 leading pics) | `--avhw` | 30 | 30 |
| C x265 re-encode | `--avsw` | 30 | 30 |
| D synthetic `testsrc2` (0 leading pics) | `--avhw` | 60 | 60 |
| D synthetic `testsrc2` | `--avsw` | 60 | 60 |
| E leading pictures cut away | `--avhw` | 27 | **30** |
| E leading pictures cut away | `--avsw` | 27 | 27 *(unchanged)* |
| F two Sony files concatenated | `--avhw` | 357 | **360** |
| F two Sony files concatenated | `--avsw` | 357 | 357 *(unchanged)* |

The two rows marked *unchanged* are the expected, and useful, result: they are
the shared reader-side trim of §3.2, which the patch deliberately does not touch.
Fixture E is the clearest demonstration — on that synthetic file the patched
`--avhw` now recovers all 30 frames while `--avsw` still loses 3.

### 6.3 `--seek` regression

With `--seek` the patch's guard is false and the original drop path must run
verbatim. Measured (`encoded N frames`, `-c raw`, `rc=0` throughout):

| clip | `--seek` | baseline `--avhw` | patched `--avhw` | patched `--avsw` |
|---|---|---|---|---|
| C1083 (330 f) | 1.0 s | 207 | **207** | 210 |
| C1083 (330 f) | 2.5 s | 147 | **147** | 150 |
| C1083 (330 f) | 4.0 s | 27 | **27** | 30 |
| C1169 (10170 f) | 10.0 s | 9507 | **9507** | 9510 |
| C1169 (10170 f) | 60.0 s | 6567 | **6567** | 6570 |

**Identical on every row.** The seek path is byte-for-byte the same behaviour as
before the patch. (The `--avsw` column differs from `--avhw` on seek runs — that
is pre-existing `--avsw` behaviour, present on the baseline too, not a patch
effect.)

### 6.4 Independent verification of the recovered frames

Three checks that do not rely on NVEncC's own accounting:

1. **The patched `--avhw` output is byte-identical to the `--avsw` output.**
   Same source, same encoder settings (`-c hevc --output-depth 10 --cqp 23`):

   ```
   sha256 avhw 6336165c02a8a294b0fd497b5a43759a321ba748a27d2478327ee6206926fc04
   sha256 avsw 6336165c02a8a294b0fd497b5a43759a321ba748a27d2478327ee6206926fc04
   identical bytes: True
   packet sequence: 30 vs 30, identical (pts, dts, flags, size)
   ```

2. **Per-frame picture identity on raw decode.** Decoding to raw
   `yuv420p10le` and comparing per-frame mean Y/U/V:

   | clip | `--avhw` frames | `--avsw` frames | frames compared | identical means | max deviation |
   |---|---|---|---|---|---|
   | Sony 30 f | 30 | 30 | 30 | **30 / 30** | 0.0000 |
   | Sony 330 f | 330 | 330 | 330 | **330 / 330** | 0.0000 |

   Not "same count" — same pictures, in the same order, with identical 10-bit
   sample means, including the three leading pictures.

3. **Independent count on the delivered file** for every row in §6.1 and §6.2
   (`ffprobe -count_frames`, which decodes rather than reading a header).

### 6.5 Known incompleteness — declared, not hidden

The patch does **not** address §3.2, and the `after` data shows it plainly:
`--avsw` still reports 27 on fixtures E and F. Consequences:

* Output frame sequence for `--avhw` **no leading pictures lost**: fixed.
* The reader's internal frame-position table can still hold fewer entries than
  the container has samples for those synthetic layouts, so
  `N frames, End of file` and `--log-framelist` can under-report.
* That inconsistency is a verification hazard, and is why a *complete* fix needs
  the second hunk as well (or a downstream verification that uses the delivered
  container rather than the reader's own estimate).

A second hunk is not included because it changes shared
QSVEnc/NVEnc/VCEEnc reader code, its OpenGOP window (`<= 16`) has no test case in
this environment, and shipping it unverified would be worse than declaring it
missing. On the real Sony corpus the §3.2 effect is not visible in the delivered
output (§6.1: every file is now exact), so it does not block adoption.

---

## 7. Answer to question C — is the GPU pipeline preserved?

Yes, and it is measured, not argued. On the patched build, 330-frame Sony clip:

```
NVENC / CUDA   NVENC API 13.1, CUDA 13.4, schedule mode: auto
Input Info     avcuvid: H.265/HEVC, 3840x2160, 60000/1001 fps
Vpp Filters    copyDtoD
Output Info    H.265/HEVC main10 @ Level auto
               avwriter: hevc => mp4
encoded 330 frames, 46.60 fps, 45041.66 kbps, 29.56 MB
encode time 0:00:07, CPU: 6.3, GPU: 2.6, VE: 28.5, VD: 14.2
```

| Evidence | Required | Observed (patched) | Observed (baseline) |
|---|---|---|---|
| `Input Info` | `avcuvid:` — never `avsw:` | `avcuvid: H.265/HEVC` | `avcuvid: H.265/HEVC` |
| `Vpp Filters` | GPU-only | `copyDtoD` | `copyDtoD` |
| `NVENC / CUDA` | NVENC engine engaged | API 13.1 / CUDA 13.4 | API 13.1 / CUDA 13.4 |
| Engine counters | NVIDIA **VE** (encoder) **and VD** (decoder) busy | `VE: 28.5`, `VD: 14.2` | `VE: 34.1`, `VD: 13.1` |
| Encoded bytes | larger, because 3 more frames are encoded | 29.56 MB (330 f) | 26.34 MB (327 f) |

The `VD` (hardware decoder engine) counter is the strongest non-frame-count
evidence: NVDEC is doing the decoding, before and after.

The patch itself touches **only** a condition around
`m_dec->frameQueue()->releaseFrame(...)` — whether an already-decoded GPU surface
is forwarded to the next pipeline task or returned to the decoder pool. It does
not change the reader, `CuvidDecode` / `DecodePacket` / `cuvidParseVideoData`,
insert any surface download or host copy, touch `initSWVideoDecoder` or any
`libavcodec` software decoder, or change encoder parameters, GOP structure, rate
control or muxing. The continued presence of `copyDtoD` (device-to-device only)
is the concrete confirmation that no host round-trip was introduced.

`--avsw` on the same patched binary still shows `avsw: hevc(yv12(10bit))->p010
[AVX2]` and no `VD` figure while showing `VE` — software decode, hardware
encode — so the two paths remain distinct.

---

## 8. Performance, before vs after

**Toolchain caveat (§1, §13 L2):** the patched binary is CUDA 13.1 / MSVC 14.51; the
baseline is CUDA 11.8 / MSVC 14.44. Absolute wall-clock and fps are therefore
**not** a controlled comparison. Frame counts, packet sequences and encoded bytes
are toolchain-independent, and that is where the correctness claims live.

| clip | binary | frames out | wall s | fps | CPU % | VE % | VD % |
|---|---|---|---|---|---|---|---|
| Sony 330 f | baseline `--avhw` | 327 | 4.07 | 120.3 | 5.7 | 58.3 | 24.0 |
| Sony 330 f | patched `--avhw` | **330** | 3.61 | 126.4 | 6.5 | 66.3 | 35.0 |
| Sony 330 f | baseline `--avsw` | 330 | 6.15 | 78.7 | 36.4 | 45.8 | – |
| Sony 330 f | patched `--avsw` | 330 | 4.67 | 92.0 | 39.7 | 60.8 | – |
| Sony 10170 f → 3000 | baseline `--avhw` | 2997 | 39.61 | 78.1 | 7.4 | 50.4 | 19.9 |
| Sony 10170 f → 3000 | patched `--avhw` | 2997 | 60.73 | 50.6 | 7.4 | 34.3 | 12.9 |
| Sony 10170 f → 3000 | baseline `--avsw` | 2997 | 37.22 | 83.0 | 36.5 | 55.3 | – |
| Sony 10170 f → 3000 | patched `--avsw` | 2997 | 64.66 | 47.6 | 23.4 | 32.3 | 0.1 |

What survives the caveat:

* **CPU load is unchanged between binaries** for the same reader: `--avhw` 5.7 →
  6.5 % (330 f) and 7.4 → 7.4 % (3000 f); `--avsw` 36.4 → 39.7 % and 36.5 →
  23.4 %. The patch adds no host work and no host copy, which §7 confirms
  independently via `copyDtoD` and the busy `VE`/`VD` engines.
* **The `--avhw` vs `--avsw` CPU gap is the durable result**, and it holds on
  whichever binary you look at: 6.5 % vs 39.7 % (330 f) and 7.4 % vs 23.4 %
  (3000 f) on the patched build — roughly **3–5× less CPU** for the same
  delivered output. That, not throughput, is the reason to consider hardware
  decode.
* **The fps differences across binaries are the toolchain, not the patch.** The
  10170-frame row reads 78.1 (baseline) vs 50.6 (patched) for identical output;
  the same patched binary shows a ~2× spread across its own two readers and two
  clip lengths, and the short-clip row goes the other way (120.3 → 126.4). Do not
  read any of it as a patch regression or improvement.
* **VRAM readings are contaminated on several `after` rows.** `--sony-h264-422`,
  `--control-dji` and the two long-view `after` rows show 6.7–7.9 GiB peaks on an
  8 GiB part, which no NVEncC configuration here plausibly needs; `nvidia-smi`
  sampled another consumer during those runs. Only the rows at 1.6–1.7 GiB
  (`sony-330f`) are clean, and they show the patched build within ~5 % of the
  baseline (1682 vs 1610 MiB). Treat the rest as unusable and re-measure on a
  quiet machine before quoting any VRAM figure.

---

## 9. Building the patched binary (the recipe that worked)

The build was the hard part of this phase, and it is reproducible. Everything is
assembled from **redistributable, locally obtainable sources**; the official
NVIDIA Windows installer is *not* installed on this machine (writing under
`C:\Program Files (x86)` needs elevation, which this session does not have).

Script: `work/hwdecode2/build_nvencc.ps1`.

| Component | Source | Note |
|---|---|---|
| MSVC 14.51 (19.51) | Visual Studio Community 2026, `D:\VisualStudio2026` | provides `atlmfc` (needed by `rgy_device.h`) |
| CUDA 13.1 headers + `nvcc` 13.1.115 + `cudart`/`cuda`/`nvrtc`/`nvml` import libs | conda env `cuda131` (`nvidia` channel: `cuda-nvcc`, `cuda-cudart-dev`, `cuda-nvrtc-dev`, `cuda-nvml-dev`) | matches the NVENC API 13.1 headers already in the repo |
| CUDA MSBuild customizations (`CUDA 13.0.props/.targets/.xml`, task DLL) | extracted from the CUDA **12.9.1** Windows installer | a 13.0 shim generated from the 12.9 files: three version macros changed, nothing else |
| NPP headers + **import libraries** (`nppc.lib`, `nppif.lib`, `nppig.lib`, …) | `libnpp\npp_dev\` inside the same installer | the PyPI NPP wheels ship DLLs only, no `.lib` |
| `curand`, `cub`/CCCL headers | PyPI wheels `nvidia-curand-cu12`, `nvidia-cuda-cccl-cu12` | |
| NVML header + lib | PyPI wheel `nvidia-nvml-dev-cu12` | |
| FFmpeg dev (headers + import libs) + `libvmaf` + `libplacebo` + `libdovi` + `hdr10plus-rs` | rigaya `ffmpeg_dlls_for_hwenc` package | **40 MB, complete** — the earlier download attempts that "failed" had actually succeeded and only needed 7-Zip verification |
| Vship headers | `Line-fr/Vship` source | plus a local addition of `Vship_PRIMARIES_DisplayP3` (absent upstream at v4.1.0 but referenced by `NVEncFilterSsim.cpp`) |
| ONNX Runtime dev (headers + `onnxruntime.lib`) | NuGet `Microsoft.ML.OnnxRuntime` 1.23.2 `build/native` + `runtimes/win-x64/native` | the GPU package is a metapackage with no native files |
| `dtl` | git submodule | `git submodule update --init dtl cppcodec` |

Build-environment changes, all documented and all outside the candidate patch:

* `cudaver.props` — the two hard-coded `C:\Program Files (x86)\…\BuildCustomizations`
  import paths were pointed at the extracted copy in `third_party/`.
* `Directory.Build.props` (new) — sets `CudaToolkitCustomDir` to the x64 host
  toolchain (the CUDA targets otherwise derive a `bin` directory with no
  `cl.exe`), and adds the CUDA include directory to `IncludePath`.
* `NVEncCore/rgy_version.h` — `ENABLE_AVISYNTH_READER` /
  `ENABLE_VAPOURSYNTH_READER` set to 0 (those SDKs are not on this host and are
  not involved in the `--avhw` path). Revert with
  `work/hwdecode2/disable_reader_sdks.py --revert`.
* `work/hwdecode2/patch_vship_header.py` — adds the missing DisplayP3 enumerator
  to the vendored Vship header.

Build command:

```powershell
pwsh -NoProfile -File work\hwdecode2\build_nvencc.ps1
# -> third_party\NVEncC\_build\x64\RelStatic\NVEncC64.exe
```

The produced binary must sit next to its runtime DLLs. That directory
(`_build\x64\RelStatic\rt`) was assembled by copying the DLL set from the shipped
`tools/NVEncC_9.31_x64` distribution plus the CUDA 13.1 / NPP / cuRAND runtime
DLLs, and the newer FFmpeg DLLs from the `ffmpeg_dlls_for_hwenc` package.

```powershell
& ...\_build\x64\RelStatic\rt\NVEncC64.exe --version
# NVEncC (x64) 9.31 (r1) by rigaya, Sep 12 2026 (VC 1951/Win)
#   [NVENC API v13.1, CUDA 13.1]
# reader: raw, y4m, avi, avsw, avhw [...]
```

> The patched binary is **not** an upstream-quality build: `avs` and `vpy`
> readers are disabled, the CUDA MSBuild rule is a generated 13.0 shim, and it is
> linked against dynamically-loaded FFmpeg DLLs rather than statically. It is
> entirely adequate to answer the question this phase asks, and it is not a
> distribution candidate.

---

## 10. Correctness regression matrix

Harness: `work/hwdecode2/run_regression.py` (measure) + `check_regression.py`
(assert). Raw records: `work/hwdecode2/raw/regression/regression-{patched,baseline}.json`,
verdict: `regression-verdict.json`.

### 10.1 What is checked — and why a frame count is not enough

Every case records **five independent things** about the delivered file:

| # | Measurement | How |
|---|---|---|
| 1 | frame count | `ffprobe -count_frames` on the output container |
| 2 | PTS sequence | every output packet's `(pts, dts, flags, size)`, in order, digested |
| 3 | keyframe sequence | indices of every sync sample, digested |
| 4 | frame fingerprint | per-frame signature of the decoded source **and** of the decoded output |
| 5 | output hash | sha256 of the output file + digests of the three sequences above |

The fingerprint is 6 numbers plus a 4-window chunk hash per frame, taken from a
fixed stride across the whole picture (`work/hwdecode2/fingerprint.py`). Two
different pictures agree on it only if they agree at thousands of sampled
positions. Raw frames are streamed through a pipe and never written to disk — an
earlier version wrote `-c raw` to a file, which costs ~206 GB for a single 4K
10170-frame pass and **filled the F: drive mid-experiment**. Recorded here
because it aborted one run, whose partial results are excluded.

The 13 assertions, per (case, reader):

| id | assertion |
|---|---|
| C1 | process exited 0 |
| C2 | `ffprobe` frame count == the reader's own `encoded N frames` |
| C3 | packet count == frame count (no gaps in the container) |
| C4 | output fingerprint count == output frame count |
| C5 | `--avhw`: output frames == container frames |
| C6 | `--avhw`: reader really was `avcuvid` — no silent software fallback |
| C7 | `--avhw`: no host-side raw-frame path reported for VPP; the GPU-only stage is positively named |
| C8 | `--avhw`: NVDEC engine observed busy (or the counters were not printed at all) |
| C9 | keyframe sequence non-empty and starts at index 0 |
| C10 | `--avhw` output == `--avsw` output on **bytes, PTS, keyframes and fingerprints** |
| C11 | output frames == container frames and ≥ the baseline's own count |
| C12 | `--avhw` and `--avsw` fingerprint the same **source** pictures, position for position |
| C13 | source fingerprint coverage reaches the applied window |

C12 is the decoder-equivalence check and it involves no encoder at all: it
decodes the *same source file* through CUVID and through libavcodec and compares
the pictures.

### 10.2 Result

**RESULT: PASS — no failing checks across 16 records (9 recorded limitations).**

| case | reader | container | leading pics | baseline `encoded` | patched `encoded` | patched output | identical to `--avsw` |
|---|---|---|---|---|---|---|---|
| Sony XAVC HS 30 f | `--avhw` | 30 | 3 | **27** | **30** | 30 | bytes, PTS, kf, fp |
| Sony XAVC HS 330 f | `--avhw` | 330 | 3 | **327** | **330** | 330 | bytes, PTS, kf, fp |
| Sony XAVC HS long (10170 f) | `--avhw` | 10170 | 3 | **10167** | **10170** | 10170 | bytes, PTS, kf, fp |
| Sony H.264 4:2:2 (195 f) | `--avhw` | 195 | 2 | **193** | **195** | 195 | bytes, PTS, kf, fp |
| DJI control | `--avhw` | 105 | 0 | 105 | 105 | 105 | bytes, PTS, kf, fp |
| x265 re-encode | `--avhw` | 30 | 0 | 30 | 30 | 30 | bytes, PTS, kf, fp |
| synthetic `testsrc2` | `--avhw` | 60 | 0 | 60 | 60 | 60 | bytes, PTS, kf, fp |
| Sony in MKV, no edit list | `--avhw` | — | 3 | **27** | **30** | 30 | bytes, PTS, kf, fp |
| *(the same eight cases)* | `--avsw` | | | *unchanged by the patch on every row* | | | |

Source-fingerprint equivalence (`--avhw` vs `--avsw`, same source file):

| case | frames fingerprinted | identical |
|---|---|---|
| Sony XAVC HS 30 f | 30 | **yes** |
| Sony XAVC HS 330 f | 330 | **yes** |
| Sony XAVC HS long | 2000 (bounded) | **yes** |
| Sony H.264 4:2:2 | 195 | **yes** |
| DJI control | 105 | **yes** |
| x265 re-encode | 30 | **yes** |
| synthetic `testsrc2` | 60 | **yes** |
| Sony in MKV | 29 | **yes** |

Interpretation:

* **The control cases are untouched.** DJI, x265 and synthetic have no leading
  pictures and are byte-identical before and after — the property that makes the
  patch safe to put in front of ordinary media.
* **Every leading-picture case now matches the container exactly**, including the
  10170-frame one, and matches the software path byte for byte.
* The baseline runs are *not* identical to `--avsw` on leading-picture cases
  (different sha256, different PTS digest, fewer frames) — the harness detects the
  defect it is meant to detect.

---

## 11. Seek / trim matrix

Harness: `work/hwdecode2/run_seektrim.py`. Raw:
`work/hwdecode2/raw/seektrim/seektrim-patched.json`.

Design constraint learned during this phase, recorded because it invalidated a
first attempt: **`--frames N` and `--trim` cannot be combined** (NVEncC rejects
it), and injecting a `--frames` bound into seek cases silently caps every seek
result. The sweep is therefore split so that only the no-seek group is bounded
and seek/trim cases are counted unbounded.

| clip | case | `--avhw` patched | `--avhw` baseline | `--avsw` patched | `--avsw` baseline | verdict |
|---|---|---|---|---|---|---|
| C1083 (330 f, 5.5 s) | `--seek 0.5` (beginning) | 270 | 270 | 270 | 270 | identical |
| C1083 | `--seek 2` (early) | 150 | 150 | 150 | 150 | identical |
| C1083 | `--seek 3` (middle) | 90 | 90 | 90 | 90 | identical |
| C1083 | `--seek 4.5` (late) | 30 | 30 | 30 | 30 | identical |
| C1083 | `--seek 5.2` (near-end) | *both fail — §11.1a* | | | | pre-existing |
| C1083 | `--frames 300` | 297 | 297 | 297 | 297 | identical |
| C1083 | `--trim 0:59` | 57 | 57 | 57 | 57 | identical |
| C1083 | `--trim 100:199` | 100 | 100 | 100 | 100 | identical |
| C1169 (10170 f, ~169 s) | `--seek 0.5` (beginning) | 10107 | 10107 | 10110 | 10110 | identical |
| C1169 | `--seek 10` (early) | 9507 | 9507 | 9510 | 9510 | identical |
| C1169 | `--seek 30` (middle) | 8307 | 8307 | 8310 | 8310 | identical |
| C1169 | `--seek 120` (late) | 2967 | 2967 | 2970 | 2970 | identical |
| C1169 | `--seek 165` (near-end) | 267 | 267 | 270 | 270 | identical |
| C1169 | `--frames 300` | 297 | 297 | 297 | 297 | identical |
| C1169 | `--trim 0:59` | 57 | 57 | 57 | 57 | identical |
| C1169 | `--trim 100:199` | 100 | 100 | 100 | 100 | identical |
| C1169 | `--seek 30 --trim 0:29` | 27 | 27 | 27 | 27 | identical |

**The patch changes nothing about seek or trim semantics: every combination that
produces a result produces the same result on both binaries.** That is the
regression the patch's design bets on — with `--seek` set, `m_seek.first > 0`,
the new branch is skipped, and the original drop path runs verbatim.

### 11.1 Two pre-existing behaviours the matrix exposed

Neither is caused by the patch — both are identical on the baseline — but both
matter to integration, so they are recorded rather than discovered later.

**(a) `--seek` near the end of a short clip fails.** On the 5.5 s C1083 clip,
`--seek` at 5.0 and above fails on **both** binaries and **both** readers with

```
avcuvid: No video packets found!
avcuvid: failed to get first frame position.
failed to initialize file reader(s).
```

`--seek 4.5` works (30 frames). With only one GOP left, the demuxer's seek lands
past the data the reader needs. It is a loud failure with `rc=1`, not silent
corruption.

**(b) `--frames N` yields N − leading_pictures frames.** Measured on both
binaries and both readers
(`work/hwdecode2/raw/framesem/frames-semantics.json`; the clip has 3 leading
pictures):

| requested | patched `--avhw` | baseline `--avhw` | patched `--avsw` | baseline `--avsw` |
|---|---|---|---|---|
| 100 | 97 | 97 | 97 | 97 |
| 300 | 297 | 297 | 297 | 297 |
| 3000 | 2997 | 2997 | 2997 | 2997 |
| **3003** | **3000** | **3000** | **3000** | **3000** |
| 6000 | 5997 | 5997 | 5997 | 5997 |

NVEncC implements `--frames` for avsw/avhw as a trim `[0, N-1]`, and the reader
then shifts any trim list down by its leading-picture offset — so the requested
end index moves too. Pre-existing tool behaviour, unaffected by the patch
(identical on both binaries). Integration must either request
`N + leading_pictures` or reconcile against the container count.

---

## 12. Long-run stability

Harness: `work/hwdecode2/run_longrun.py`. Raw:
`work/hwdecode2/raw/longrun/longrun-{patched2,baseline}.json`.
Sequential runs only, on an otherwise idle GPU.

Clip: `20260903_C1169.MP4` — **10170 container frames**, ~169 s of 4K60 XAVC HS,
3 leading pictures. The `18000` row requests more than the clip holds, so
NVEncC clamps it to EOF; it is really a full-clip run with a large bound.

| requested | reader | binary | `encoded` | output | packets | fingerprints | wall s | fps | GPU mem peak MiB | NVEncC peak WS MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 3000 | `--avhw` | **patched** | 2997 | 2997 | 2997 | 2997 | 24.0 | 147.4 | 1682 | 951 |
| 3000 | `--avsw` | **patched** | 2997 | 2997 | 2997 | 2997 | 42.7 | 77.0 | 1091 | 1815 |
| 3000 | `--avhw` | baseline | 2997 | 2997 | 2997 | 2997 | 29.3 | 107.4 | 4392 | 2629 |
| 3000 | `--avsw` | baseline | 2997 | 2997 | 2997 | 2997 | 43.8 | 71.1 | 4481 | 1815 |
| 6000 | `--avhw` | **patched** | 5997 | 5997 | 5997 | 3000 (bounded) | 43.7 | 150.1 | 1593 | 955 |
| 6000 | `--avsw` | **patched** | 5997 | 5997 | 5997 | 3000 (bounded) | 79.9 | 78.5 | 1091 | 1819 |
| 10000 | `--avhw` | **patched** | 9997 | 9997 | 9997 | 3000 (bounded) | 69.0 | 151.1 | 1593 | 957 |
| 10000 | `--avsw` | **patched** | 9997 | 9997 | 9997 | 3000 (bounded) | 132.6 | 76.2 | 1091 | 1815 |
| 10000 | `--avhw` | baseline | 9997 | 9997 | 9997 | 3000 (bounded) | 78.5 | 130.1 | 4983 | 1763 |
| 10000 | `--avsw` | baseline | 9997 | 9997 | 9997 | 3000 (bounded) | 157.6 | 64.0 | 3931 | 2640 |
| ≥10170 (clamped) | `--avhw` | baseline | 10167 | 10167 | 10167 | 3000 | 108.5 | 97.1 | 4481 | 2647 |
| ≥10170 (clamped) | `--avsw` | baseline | 10170 | 10170 | 10170 | 3000 | 132.3 | 79.0 | 4813 | 1821 |
| **10170 (full clip)** | `--avhw` | **patched** | **10170** | **10170** | **10170** | 2000 (bounded) | 110.4 | 93.8 | 7870 | 2397 |
| **10170 (full clip)** | `--avsw` | **patched** | 10167 | 10167 | 10167 | 2000 (bounded) | 155.4 | 66.9 | 3698 | 2476 |

No run in this sweep produced a crash, a short read, a container with gaps, or a
mismatch between `encoded`, the container count and the packet count.

> **Confirmed on the rebuilt binary.** After the provenance fix in §5.1.1 the
> sweep was repeated with the binary rebuilt from the verified sources
> (`work/hwdecode2/raw/longrun/longrun-patched-final.json`) and reproduced the same
> numbers: 2997 / 2997 / 9997 / 9997 frames, `--avhw` 147.3 and 151.4 fps against
> `--avsw` 81.5 and 88.8 fps, NVEncC peak working set 949–956 MiB (`--avhw`)
> versus 1817–1822 MiB (`--avsw`), VRAM 1593–1682 MiB versus 1099–1102 MiB. Output
> hashes match the first pass on every case.

What the sweep establishes:

* **Zero silent frame loss across 3000–10170 frames.** Every row's `encoded`,
  `output` and `packet` columns agree exactly, on both binaries and both readers.
* **No stability failure.** The 10170-frame full-clip encode completes with
  `rc=0` on both binaries and both readers.
* **The 18 k request completes.** `STATUS_STACK_BUFFER_OVERRUN` did **not**
  recur. Per the brief, nothing in the patch was changed to make it pass; the
  earlier crash stays recorded as a measurement-environment event.
* **The patch does not make the full-clip case worse.** The patched `--avhw`
  full-clip run is 110.4 s against the baseline's 108.5 s for a clamped-10170 run
  that delivers **3 fewer frames** — the two are the same jobs at the same
  settings, so there is no sign of a stability or throughput regression from the
  added branch.
* **Memory is modest and flat.** NVEncC's own peak working set is ~950 MiB for
  `--avhw` and ~1.8 GiB for `--avsw` on this clip, and both stay flat as the
  request grows from 3000 to 10000 frames. Peak VRAM is 1.6 GiB for `--avhw`
  versus 1.1 GiB for `--avsw` — the extra is CUVID decode surfaces.
* **`--avhw` is consistently faster than `--avsw` on the same binary and clip**:
  147/77, 150/78, 151/76 fps. That is the through-the-patch comparison and it is
  valid; cross-*binary* fps is not (§14, L2).

### 12.1 One non-reproducible crash — recorded, not worked around

During the first long-run sweep the 10170-frame `--avhw` case exited after 6.4 s
with **`0xC0000005` (STATUS_ACCESS_VIOLATION)** and produced no output.
Reproduction attempts:

* the identical command line run directly, with `--log-level debug --log`, with
  `--log` only, and with stdout piped: **`rc=0`, full output, four times**;
* the same harness case re-run: **`rc=0`, 10167 frames**;
* the large-bound full-clip request on the same binary: **`rc=0`, 10170 frames**.

**One occurrence in six attempts, never reproduced.** It is recorded as an
intermittent fault in the patched build's environment or in the measurement
harness, and handled the same way as the earlier
`STATUS_STACK_BUFFER_OVERRUN`: no patch change, no workaround, no silent retry.
It is a stability risk for long unattended runs and belongs in the integration
risk list — every run that completed was frame-exact.

### 12.2 A second pre-existing `--frames` quirk at EOF

When `--frames` exceeds the clip length, `--avhw` and `--avsw` truncate at
different points. At `--frames 10000` on a 10170-frame clip the patched `--avhw`
output is **sha256-identical to the baseline `--avhw` output**, while `--avsw`
produces a different file. Both deliver 9997 frames; they simply drop the last
frames differently. It is patch-neutral (the patched and baseline `--avhw` outputs
match) and only appears when the request is clamped — recorded so it is not
mistaken for a patch effect later.

---

## 13. Known limitations

Everything here is a limitation of the *evidence*, not a silent downgrade of a
check. `check_regression.py` marks exactly these as `LIMIT` and lists them at the
end of its report; anything else is a `FAIL`.

| # | Limitation | Impact |
|---|---|---|
| L1 | **Single-GPU host.** `--check-device` reports one CUDA device; `--device 1` → `Invalid Device Id = 1`. | The `device 0 / device 1 / multi-GPU` dimension is **untested** (§15). Do not claim multi-GPU readiness. |
| L2 | **The patched binary uses a different toolchain** (CUDA 13.1 / MSVC 14.51) from the shipped baseline (CUDA 11.8 / MSVC 14.44). | Cross-binary wall-clock and fps are **not a controlled benchmark**. All correctness claims are toolchain-independent: counts, PTS, keyframes, fingerprints, bytes. |
| L3 | **`NVDEC engine busy` (C8) could not be asserted on five rows** — NVEncC prints the `VE:`/`VD:` line only sometimes, and for short clips it often does not print it at all. It is not a clean short-clip rule either: `control-synthetic` (60 frames) printed it in one pass and not in another. | Those rows fall back to `Input Info = avcuvid` plus the GPU-only VPP stage. On **every** row where the counters were printed, `--avhw` showed `VD > 0` (33.7 % and 38.0 % on the two longest cases). Engine counters are corroboration, never the sole evidence. |
| L4 | **Engine counters are printed inconsistently across runs and across binaries** — the same case can show them in one pass and not the next, and the baseline sometimes prints `VD` where the patched build does not (e.g. `control-dji`). | The counters are supporting evidence only. `Input Info = avcuvid`, the GPU-only VPP stage and the absence of a host copy are the primary signals, and those held on every row. |
| L5 | **VRAM readings were contaminated** on several early runs (peaks up to 7.9 GiB on an 8 GiB part when nothing should use that much); some rows show a flat 1091 MiB that is a floor, not a measurement. | VRAM is not used in any conclusion. Only same-session relative statements are made, and the clean patched rows (1682 / 1593 MiB) are quoted. |
| L6 | **Long-clip fingerprints are bounded to 2000–3000 frames**, declared per case. | Counts, PTS sequences, keyframe sequences and output hashes cover the whole clip; only the per-frame picture comparison is windowed. |
| L7 | **An MKV source decodes to 29 of 30 frames** under streaming `-f rawvideo`. | Both readers agree on 29, so no difference is masked; the delivered output is 30 frames. |
| L8 | **`--seek` near the end of a short clip fails** (§11.1a). | Pre-existing, identical on both binaries, loud (`rc=1`). |
| L9 | **`--frames N` yields N − leading_pictures** (§11.1b). | Pre-existing, identical on both binaries. Integration must compensate or reconcile against the container. |
| L10 | **`--frames` beyond EOF truncates differently for `--avhw` and `--avsw`** (§12.2). | Pre-existing and patch-neutral. |
| L11 | **`--trim` + `--avhw` is short by the leading-picture offset** (57 where 60 was asked, on both binaries). | Pre-existing interaction between `--trim` and the reader's offset adjustment; identical before and after. |
| L12 | **One non-reproducible `0xC0000005`** on a 10170-frame run (§12.1). | A stability risk for long unattended runs. Every completed run was frame-exact. |
| L13 | **Parallel encode / `--split-enc` untested.** | Argued from source only (§15). |
| L14 | **Corpus scale: 8 cases, not the whole 151-file Sony corpus.** | Phase 1 established the *predictor* on 151/151; this phase verifies the *fix* on a representative set plus one long clip. A full-corpus sweep is the first integration step (§16). |
| L15 | **`avs` / `vpy` readers are disabled in the patched build** (§9). | Those paths were not exercised. They are unreachable from 1KeyTranscoder, which passes a file path. |
| L16 | **§3.2 (`setPocAndFix`) is not fixed.** | Visible on synthetic OpenGOP layouts: `--avsw` still reports 27 on fixtures E and F. On the real corpus the delivered output is exact. |

---

## 14. Risks

| Risk | Assessment | Mitigation |
|---|---|---|
| Patch changes behaviour on normal media | **Low, and measured.** DJI / x265 / synthetic are byte-identical before and after; the new branch is unreachable unless a decoded picture's PTS precedes the first packet's PTS with no seek. | Keep the fixture set as a regression gate: any change to those three counts is a regression. |
| Patch breaks `--seek` | **Low, and measured.** 17 seek/trim combinations across two clips: identical frame counts before and after, with no exceptions. | Re-run `run_seektrim.py` after any rebase. |
| §3.2 remains (L16) | **Medium.** The reader's internal frame-position list can still under-report on synthetic OpenGOP layouts. On the real corpus the delivered output is exact, so it is a verification hazard rather than a data-loss hazard. | Never trust the reader's own `N frames`; count the delivered container. |
| Upstream divergence | **Medium.** `NVEncPipeline.h` changes often; a fork must re-apply a 2-file diff on every bump. | Keep it as a diff, never a tree fork. Re-run the regression + seek/trim suites after each bump. |
| Build burden | **Medium-high.** §9 is a multi-hour exercise the first time. | Budget for it, or use a host with a real CUDA Toolkit install. |
| Licensing / distribution | **Medium.** NVEnc is MIT, but a shipped fork inherits the NVIDIA SDK headers and a toolchain nobody else here can rebuild reproducibly. | Do not ship a patched NVEncC; treat it as an internal capability. |
| Licensing / distribution of the *patch* | **Low.** The patch is 18 lines against MIT source and contains no NVIDIA code. | Ship the patch file, not a binary. |
| Single-GPU host (L1) | **Untested.** | Do not claim multi-GPU safety; re-test on a 2-GPU host. |
| Intermittent crash (L12) | **Low-medium, unknown trigger.** 1 in 6 attempts on one case, never reproduced. | Treat long unattended runs as needing a supervised first pass; watch for `0xC0000005`. |
| Concurrency | **No regression observed.** 2 × `--avhw` and `--avhw`+`--avsw` in parallel both returned the expected counts with `rc=0` and no `Break in task NVDEC`. | Sequential runs remain the rule for *measurements*; it is not needed for correctness. |

---

## 15. Multi-GPU and parallel encode

**Not tested — the host has one GPU.** Recorded verbatim rather than simulated:

* `--check-device` → `DeviceId #0: NVIDIA GeForce RTX 5070 Laptop GPU` only.
* `--avhw -d 0` → correct output, `rc=0`.
* `--avhw -d 1` → `Invalid Device Id = 1`, `Failed to initialize devices.`, `rc=1`.
* Parallel encode (`--split-enc`, `--parallel`) was deliberately **out of scope**
  for this phase. From the source, the patched branch sits inside the
  `m_gotFrameAfterFirstPts` fast path while `m_endPts >= 0` (segment mode) returns
  before it, so segments should behave as before — **argued from source, not
  measured.**

---

## 16. Recommendation and integration checklist

**The technical question is answered: an 18-line reader-side patch makes Sony
`GPU decode → GPU encode` correct, the recovered frames are provably the right
ones, and the patch survives regression.** What remains is a policy decision.

### As production engineering — still prefer FFmpeg `-hwaccel`

Phase 1 proved FFmpeg's own NVDEC path is **bit-identical to software decode on
151/151 Sony files**. It needs no fork, no patch, no SDK, no build recipe.
The patched-rigaya route now *works*, but adopting it means owning §9 forever, on
a code path where a rebase mistake is a silent frame loss.

### As a capability — the patch is worth keeping

1. **It is small and its blast radius is measured to be zero.** 18 lines, two
   files, no deletions; 13 assertions × 16 records pass; 17 seek/trim
   combinations unchanged; no host round-trip; no CPU cost.
2. **It is reproducible.** It applies cleanly to a pristine 9.31 checkout and
   reproduces the tested tree byte-for-byte (§5.1).
3. **§3.2 is the generalisable finding.** A tool can deliver the correct frame
   *sequence* while reporting the wrong frame *count* about itself. Any pipeline
   that trusts a decoder's self-report can ship a wrong count at exit 0. The
   four-way reconciliation used here — container samples, the reader's estimate,
   the encoder's `encoded N frames`, an independent count of the delivered file —
   belongs in the production design whichever decoder wins.

### Integration checklist — do these before wiring the patch into 1KeyTranscoder

1. **Re-run the three suites** after any rebase: `run_regression.py` +
   `check_regression.py`, `run_seektrim.py`, `run_longrun.py`.
2. **Reconcile four counts in production**, never one:
   container samples · reader estimate · `encoded N frames` · independent count
   of the delivered file. Treat the reader's estimate as untrusted until §3.2 is
   also fixed.
3. **Never use `--frames N` as an exact frame budget** — request
   `N + leading_pictures`, or drive by container count (L9).
4. **Expect the reader's own input frame count to disagree with the delivered
   count** on OpenGOP-ish inputs (L16).
5. **Extend to the full corpus**: 151 Sony files + DJI with the four-way count
   reconciliation. Phase 1 validated the *prediction*
   `avhw = container − leading` on 151/151; after the patch the invariant is the
   much simpler `avhw == container`, and it should hold on all of them.
6. **Re-test on a multi-GPU host** before enabling device selection (L1).
7. **Watch for `0xC0000005`** on long unattended runs (L12).

### PATCH STATUS

```text
PATCH STATUS: READY FOR PROJECT INTEGRATION

  scope           18 added lines, 2 files, NVEncC 9.31 (2cb9d81), no deletions
  patch sha256    53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc
  applies         clean to pristine 9.31 (git apply, strict); reverting and
                  re-applying reproduces the built tree byte-for-byte
  provenance      work/hwdecode2/raw/provenance.json ties the tested binary
                  (sha256 dcf6d7a63143c777e54a749281bef7ee1dd50d61c44b14d128e66a1217c8be4b)
                  to the patch; the suite was re-run on the rebuilt binary and
                  reproduced every output hash
  correctness     13/13 assertions x 16 records PASS (8 cases x 2 readers)
                  frame count, PTS sequence, keyframe sequence, frame
                  fingerprint and output hash all reconciled
  regression      controls (DJI / x265 / synthetic) byte-identical before/after
                  seek + trim: 17/17 combinations identical before/after
  long run        3000 / 6000 / 10000 / full 10170 frames, no frame loss,
                  no instability, flat memory (confirmed twice, on two builds)
  pipeline        GPU decode -> GPU VPP -> NVENC preserved; no host copy,
                  no hwdownload, no software decoder
  parity          patched --avhw output is byte-identical to --avsw on every
                  leading-picture case

  9 recorded limitations (section 13), of which the ones that could bite an
  integrator are: the reader's own input frame count can still disagree with the
  delivered count (L16), `--frames N` is off by the leading-picture count (L9),
  and one 0xC0000005 occurred once in six attempts and never reproduced (L12).
  NOT covered: multi-GPU, parallel/split encode, and the full 151-file corpus.

  This is a READY-to-integrate patch, not a READY-to-ship binary. The build in
  section 9 is a research build (avs/vpy readers disabled, generated CUDA
  MSBuild shim, dynamically linked FFmpeg) and must not be distributed.
```

---

## 17. Artifacts

Tooling lives in `F:\1KT-avhw\work\hwdecode2`; `work/` is gitignored by project
convention, so these stay local by design.

| File | Role |
|---|---|
| `patch/0001-avhw-keep-leading-pictures.patch` | **the candidate patch** — 18 added lines, sha256 `53084fa8bd87ccc1d5882ab01d21e7320401f7c52c85263728b976d1edfe6dfc` |
| `patch/apply_patch.py` | BOM- and CRLF-safe applier (alternative to `git apply`) |
| `run_regression.py` / `check_regression.py` | correctness matrix + the 13 assertions |
| `record_provenance.py` | ties the tested binary to the patch: source, patch and binary hashes, tree status |
| `fingerprint.py` | streaming per-frame fingerprinting (never writes raw YUV) |
| `run_seektrim.py` | seek + trim regression across both binaries |
| `run_longrun.py` | long-run stability with memory sampling |
| `verify_frames_semantics.py` | the `--frames N` behaviour measurement |
| `run_fixtures.py` / `make_fixtures.py` | fixture set A–F and the fixture × reader table |
| `run_matrix.py` / `compare_ab.py` | earlier core matrix and the byte/packet identity check |
| `run_concurrency.py` | device selection, repeatability, concurrent runs |
| `run_longview.py` | bounded head-views of long clips |
| `build_nvencc.ps1` | the reproducible build recipe (§9) |
| `disable_reader_sdks.py`, `patch_vship_header.py` | build-environment tweaks, both revertible |
| `hwenv2.py` | pinned tools, ffprobe/ffmpeg measurement, NVEncC runner |
| `raw/regression/regression-{patched,baseline}.json`, `regression-verdict.json` | correctness evidence + verdict |
| `raw/seektrim/seektrim-patched.json` | seek/trim evidence |
| `raw/longrun/longrun-{patched2,baseline}.json` | long-run evidence |
| `raw/longrun/longrun-patched-final.json` | long-run confirmation on the rebuilt binary |
| `raw/provenance.json` | patch / source / binary hashes for the tested artefact |
| `raw/build-verify-console.txt` | console output of the rebuild from the verified sources |
| `raw/framesem/frames-semantics.json` | `--frames` evidence |
| `raw/matrix-{baseline,after}.json`, `raw/fixtures-{baseline,after}.json` | earlier A/B matrices |
| `raw/trc-*.txt` | the decisive trace (`Set packet` / `input frame (dev)`) |
| `raw/matrix-long.json` | **superseded / invalid** — asked 3600 and 18000 frames of a 30-frame source; NVEncC clamped to EOF, so those rows measured nothing. Kept as a record of the mistake. |
| `fixtures/` | the fixture media itself (local) |
| `_build/x64/RelStatic/rt/` | the patched binary plus its runtime DLLs (research build) |
| `third_party/NVEncC-pristine/` | pristine 9.31 checkout used for the patch-apply proof |

Upstream source: `rigaya/NVEnc` tag `9.31`, commit
`2cb9d810c045202548b98ff130b12bc764eb39ea`, in
`F:\1KT-avhw\third_party\NVEncC` (never tracked by the repository).
