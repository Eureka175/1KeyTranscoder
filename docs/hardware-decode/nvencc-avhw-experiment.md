# NVEncC `--avhw` Experiment Line (Hardware Decode Phase 2)

`research/rigaya-nvencc-avhw` · base `main` @ `15cf218`

> **Scope.** This phase asks one question and only one:
> **if the NVEncC `--avhw` reader is changed, can Sony `GPU decode → GPU encode`
> be made correct?**
>
> It is *not* a re-run of Phase 1's "Sony loses 3 frames" result. That result
> (`docs/hardware-decode/root-cause.md` RC-2/RC-3) is taken as given, and nothing
> in this document re-proves it. What is new here is **where** in the tool the
> frames are lost, **why** the reader-side patch candidates do or do not work,
> and what the evidence says about whether the reader is worth fixing at all.
>
> Nothing in `1KeyTranscoder` production code was modified. No frames were
> padded, dropped or re-stamped to make counts agree. No frame count was used on
> its own as a correctness test.

---

## 0. Bottom line

| Question | Answer | Confidence |
|---|---|---|
| **A. Can it be fixed by reader *parameters* alone?** | **No.** There is no NVEncC option that changes this code path. `--avsync`, `--input-option`, `--seek`, `--trim`, `--input-analyze`, `--allow-other-negative-pts`, `--offset-video-dts-advance` were all checked against the source that consumes them; none of them reaches the filter that drops the frames. | `Confirmed` (source) |
| **B. Can it be fixed by a source patch?** | **Yes, plausibly — 18 added lines in 2 files.** The drop is a single guarded `break` in `NVEncCore`, not a decoder limitation. The patch is written and stored in this branch, but **its end-to-end A/B on a patched binary was NOT executed** — see §9. | `Confirmed` (mechanism) / `Unverified` (patched binary) |
| **C. Does a fix keep `GPU decode → GPU encode`?** | **Yes — structurally it must.** The patch touches only which *already-decoded GPU frames* are forwarded to the encoder. It adds no host-side copy, no `hwdownload`, no software decoder, no CPU pipe. | `Confirmed` (source) |
| **Is the reader worth fixing?** | **Conditionally yes for a private fork; no as an upstream request.** See §12. | `Likely` |

**The headline correction to the Phase 1 model.** Phase 1 located the defect in
"the rigaya `--avhw` reader". That is right about *where the user sees it*, but
wrong about *which reader*: the frame loss happens in **NVEncC's hardware
output stage, after a CUVID decode that is itself complete and correct**. In this
phase's own trace, **all 30 video packets reach NVDEC and NVDEC emits all 30
pictures in correct display order, including the three that were previously
reported as "dropped by the hardware reader"**. NVEncC then discards them on a
timestamp test. This distinction is what makes a patch possible at all — a
decoder that never produced the pictures could not be patched in the reader.

---

## 1. Environment

| Item | Value |
|---|---|
| Host OS | Windows 11 x64 (26200) |
| CPU | Intel Core Ultra 9 285H (6P+10E, 16C/16T) |
| GPU | **1 ×** NVIDIA GeForce RTX 5070 Laptop GPU, 4608 cores, 1545 MHz base, PCIe5x16 |
| VRAM | 8151 MiB |
| Driver | 616.56 |
| NVEncC under test | 9.31 (r4047), 2026-08-08, VC 1944/Win, `NVENC API v13.1`, `CUDA 11.8` |
| NVEncC runtime it reports | `NVENC API 13.1, CUDA 13.4, schedule mode: auto` |
| FFmpeg (measurement only) | bundled `tools/ffmpeg.exe` **9.0.1** — never the `PATH` build |
| ffprobe (measurement only) | bundled `tools/ffprobe.exe` **9.0.1** |
| NVEncC source under review | `rigaya/NVEnc` tag **9.31**, commit `2cb9d81` |
| Source worktree | `F:\1KT-avhw\third_party\NVEncC` (never in the repo) |
| Measurement worktree | `F:\1KT-avhw\work\hwdecode2` |

**Single-GPU host.** `--check-device` reports exactly one CUDA device;
`--device 1` fails with `Invalid Device Id = 1`. The task's
`device 0 / device 1 / multi-GPU` dimension **cannot be exercised here** and is
recorded as untested in §11 rather than approximated.

---

## 2. Reproducer

### 2.1 Minimal (already known — reproduced, not re-derived)

```powershell
$NV  = 'F:\1KeyTranscoder\tools\NVEncC_9.31_x64\NVEncC64.exe'
$src = 'F:\1KeyTranscoder\testsets\20260903\A7M5\20260903_C1170.MP4'   # 30 container frames

& $NV -i $src --avsw -c hevc --output-depth 10 --cqp 23 -o out_avsw.mp4
#   -> encoded 30 frames

& $NV -i $src --avhw -c hevc --output-depth 10 --cqp 23 -o out_avhw.mp4
#   -> encoded 27 frames          <-- the defect
```

Both counts are confirmed from the delivered file, not from the tool's own
progress line:

```powershell
& tools\ffprobe.exe -v error -select_streams v:0 -count_frames `
    -show_entries stream=nb_read_frames -of default=nw=1 out_avhw.mp4
#   -> nb_read_frames=27      (out_avsw.mp4 -> 30)
```

Repeatability: 10 consecutive `--avhw` runs ⇒ `27,27,27,27,27,27,27,27,27,27`;
5 `--avsw` runs ⇒ `30,30,30,30,30`. The deficit is deterministic, not statistical.

### 2.2 Instrumented reproducer — the one that actually locates the loss

NVEncC has trace-level logging that names every packet handed to the CUVID
decoder and every picture it emits. This is the decisive instrument:

```powershell
& $NV -i $src --avhw -c raw --output-res 64x64 --log-level trace `
      --log trace-avhw.txt -o NUL
Select-String -Path trace-avhw.txt -Pattern 'Set packet'
Select-String -Path trace-avhw.txt -Pattern 'input frame \(dev\)'
```

Result (`20260903_C1170.MP4`, 30 container frames, 3 leading pictures):

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
NVDEC: input frame (dev) #26, pic_idx 6, timestamp 29029   <- 27 frames total reach the encoder
```

**30 packets in · 30 pictures out of NVDEC · 27 frames into the encoder.**

Also reproducible with `--frames` — and the `--frames` case is a useful sanity
check because the deficit does not scale with the request:

```powershell
& $NV -i $src --avhw --frames 20  -c raw -o NUL   # -> encoded 17 frames   (20 - 3)
& $NV -i <330-frame clip> --avhw --frames 100 -c raw -o NUL  # -> encoded 97 frames (100 - 3)
```

### 2.3 Rigaya's own debug logs (auxiliary)

```powershell
& $NV -i $src --avhw -c raw --log-level debug --log dbg.txt `
      --log-packets pkt.txt --log-framelist fpos.txt -o NUL
```

* `--log-packets` contains **all 30 video packets** for both `--avhw` and
  `--avsw`; the two logs are byte-identical outside timestamps.
* `--log-framelist` for `--avhw` starts at `poc 0, I, pts 3003` — consistent
  with the loss being upstream of the encoder.
* `avcuvid: found first key frame: timestamp 3003, offset 3` and
  `avcuvid: adjust trim by offset 3` are produced **identically** by `--avsw`
  and `--avhw`, so the reader's leading-picture bookkeeping is *not* what differs.

---

## 3. Root cause — located in source

Three code paths matter. All line numbers are `rigaya/NVEnc` @ 9.31 (`2cb9d81`).

### 3.1 The drop itself — `NVEncCore/NVEncPipeline.h`, `PipelineTaskNVDecode::getOutputFrame()`

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
packet in decode order is the IDR, and the IDR's *presentation* timestamp is
3003 (its decode timestamp is −2002). Because the first three pictures display
*before* the IDR, their timestamps are `0, 1001, 2002` — all strictly below
3003. `m_hwDecFirstPts` is therefore not "the start of the presentation" (which
is 0), it is "the PTS of the first packet", and it rejects exactly the leading
pictures.

The comment in the source says the filter exists for OpenGOP and for `--seek`
(sample A: `Beauty_3840x2160_120fps_420_8bit_HEVC_MP4.mp4`, `--seek 6.66667`;
sample B: `720p - AVC - MP2 2.0 - ZDF HD.ts`). **Both named cases are seeks.**
The filter was extended to the non-seek case, where its premise — "the decoder
is emitting frames before the requested start" — does not hold.

### 3.2 A second, independent drop — `NVEncCore/rgy_input_avcodec.h`, `FramePosList::setPocAndFix()`

```cpp
// NVEncCore/rgy_input_avcodec.h:654-671
for (; m_nextFixNumIndex < nSortFixedSize; m_nextFixNumIndex++) {
    if (m_list[m_nextFixNumIndex].data.pts < m_firstKeyframePts //ソートの先頭のptsが塚下キーフレームの先頭のptsよりも小さいことがある(opengop)
        && m_nextFixNumIndex <= 16) { //wrap arroundの場合は除く
        //これはフレームリストから取り除く
        m_list.pop();                       // <-- removes the frame-position entry
        m_nextFixNumIndex--;
        nSortFixedSize--;
    }
```

with `m_firstKeyframePts` set in `add()`:

```cpp
// NVEncCore/rgy_input_avcodec.h:320-322
if (m_firstKeyframePts == AV_NOPTS_VALUE && (pos.flags & AV_PKT_FLAG_KEY) && nIndex == 0) {
    m_firstKeyframePts = m_list[nIndex].data.pts;
}
```

This path affects the **frame-position list**, not the emitted frames, and it is
in shared reader code (not NVEncC-specific). It explains why `--log-framelist`
and the `N frames, End of file` input declaration shrink to 27 alongside the
output. It is *not* the reason the encoder sees 27 frames — §2.2 shows the
encoder's input is already 27 while the list still legitimately holds the
leading pictures' positions.

It is recorded here because it is the second place in the same tool that treats
"PTS of the first keyframe" as "start of presentation", and a reader-only patch
that ignores it will leave the reported input frame count inconsistent with the
delivered frame count — a real hazard for any downstream verification that uses
`N frames, End of file`.

### 3.3 The third suspect — ruled out

`getSample()` (`NVEncCore/rgy_input_avcodec.cpp:3405-3408`) does drop packets
that precede the first keyframe:

```cpp
if (!bTreatFirstPacketAsKeyframe && !m_Demux.video.gotFirstKeyframe && !keyframe) {
    av_packet_unref(pkt.get());
    i_samples++;
    continue;
}
```

For Sony's stream the IDR **is** the first packet in decode order and
`AV_PKT_FLAG_KEY` is set (`pkt.txt` row 1: `stream 0, hevc, 3003, -2002, 1001, 1, 714752`),
so this branch never fires on the corpus. It is a standing hazard for
`waitKeyAfterSwitch` / PMT-follow streams, not this defect. Phase 1 read this
branch as the cause; §2.2 disproves that reading empirically.

---

## 4. Experiment results

### 4.1 Core matrix (baseline NVEncC 9.31)

`encoded` = rigaya's own `encoded N frames`; `output` = independent
`ffprobe -count_frames` on the delivered MP4. `in` = container frame count.

| case | reader | in | leading pics | `encoded` | `output` | Δ | first PTS | last PTS | key idx | runtime s | fps | CPU % | GPU % | VE % | VD % | VRAM peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Sony 30 f (minimal) | `--avhw` | 30 | 3 | **27** | **27** | **−3** | 0 | 104104 | 0 | 1.99 | 43.7 | 2.8 | – | – | – | 1604 |
| Sony 30 f (minimal) | `--avsw` | 30 | 3 | 30 | 30 | 0 | 0 | 116116 | 0 | 2.48 | 28.2 | 17.1 | 1.0 | – | – | 1102 |
| Sony 330 f | `--avhw` | 330 | 3 | **327** | **327** | **−3** | 0 | 1305304 | 0 | 4.07 | 120.3 | 5.7 | 5.3 | 58.3 | 24.0 | 1610 |
| Sony 330 f | `--avsw` | 330 | 3 | 330 | 330 | 0 | 0 | 1317316 | 0 | 6.15 | 78.7 | 36.4 | 4.6 | 45.8 | – | 1091 |
| Sony H.264 4:2:2 | `--avhw` | 195 | 2 | **193** | **193** | **−2** | 0 | 768768 | 0 | 3.00 | 108.4 | 5.6 | – | – | – | 1854 |
| Sony H.264 4:2:2 | `--avsw` | 195 | 2 | 195 | 195 | 0 | 0 | 776776 | 0 | 3.35 | 87.6 | 37.7 | 3.5 | 31.5 | – | 1094 |
| **DJI control** | `--avhw` | 105 | **0** | **105** | **105** | **0** | 0 | 416416 | 0 | 2.53 | 80.3 | 5.1 | 6.0 | – | 15.0 | 1975 |
| DJI control | `--avsw` | 105 | 0 | 105 | 105 | 0 | 0 | 416416 | 0 | 6.43 | 19.7 | 15.1 | 1.2 | 17.2 | – | 1285 |

Two things to read from this table:

1. **The deficit is exactly the leading-picture count, in both codecs**
   (3 for XAVC HS, 2 for XAVC S H.264 High 4:2:2) and **zero** where there are no
   leading pictures (DJI). This reproduces the Phase 1 predictor and confirms it
   is still the right model.
2. **`encoded` and the independently measured `output` agree in every row.**
   NVEncC is not lying about how many frames it produced; the loss is upstream
   of the counter, and is therefore a *decode-side* loss, not a mux/write loss.

`–` means the field is not reported: for `--avhw` runs NVEncC does not print the
`CPU: … GPU: … VE: … VD: … GPUClock: … VEClock: …` line at all (it prints only
`CPULoad:`) — that string appears only on the software-decode path. VRAM is a
`nvidia-smi memory.used` peak sampled during the run and is only meaningful for
uncontended runs (all rows above are sequential).

### 4.2 Throughput (indicative, not a benchmark)

Same machine, same encode settings, uncontended, sequential:

| clip | reader | frames out | wall s | fps | CPU % |
|---|---|---|---|---|---|
| Sony 330 f (4K60, 330 container) | `--avhw` | 327 | 4.07 | 120.3 | 5.7 |
| Sony 330 f (4K60, 330 container) | `--avsw` | 330 | 6.15 | 78.7 | 36.4 |
| Sony 10170 f → first 3000 | `--avhw` | 2997 | 39.61 | 78.1 | **7.4** |
| Sony 10170 f → first 3000 | `--avsw` | 2997 | 37.22 | 83.0 | **36.5** |
| Sony 7830 f → first 3000 | `--avhw` | 2997 | 39.63 | 78.0 | **7.4** |
| Sony 7830 f → first 3000 | `--avsw` | 2997 | 40.40 | 76.2 | **34.7** |

Two honest readings, and they differ:

* **CPU is the robust result.** `--avhw` costs **~5× less CPU** (7.4 % vs 36.5 %)
  for the same delivered output. This is stable across clip lengths and is the
  real, durable reason to consider hardware decode.
* **Speed is not.** On a short clip warm-up dominates and `--avhw` looks 1.5×
  faster; on a 3000-frame view the two are **within noise of each other**
  (78.1 vs 83.0 fps, and the second pair reverses to 78.0 vs 76.2). The Phase 1
  note stands and is reinforced: **do not argue for hardware decode on
  throughput.** The argument is CPU headroom for concurrent encodes.

VRAM is comparable (1.6 GiB vs 1.1 GiB at 4K60 on an 8 GiB part); the extra is
CUVID decode surfaces.

Note that the short-clip "faster" row is also the *wrong* row: `--avhw` currently
wins on speed on small clips partly by doing less work.

A bounded 18000-frame (5 min) view of the same clips was attempted and the
measurement process died with `STATUS_STACK_BUFFER_OVERRUN`
(`0xC0000409`, exit `-1073740791`) with no partial output. This is recorded as a
**tooling outcome, not a decoder result** — it was not investigated and must not
be read as evidence in either direction. The 3000-frame views above are the
longest valid measurements obtained in this phase.

### 4.3 Container / bitstream variants

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

Notes that matter more than the counts:

* **A vs B** re-confirms Phase 1 RC-4 independently: the edit list is not the
  trigger. A Matroska remux with no edit list at all loses the same 3.
* **C and D are the controls that make the patch safe.** Where there are no
  leading pictures, `--avhw` is exact — which is also why a patch that only
  changes behaviour in the "frames below the first packet's PTS" case cannot
  affect ordinary media.
* **E is a negative control**: once the leading pictures are removed from the
  container, `--avsw` loses them too (both readers report 27). The loss tracks
  the *pictures*, not the reader.
* **F is the positive control, and it is the one that isolates the timestamp
  test.** It is two Sony files concatenated with `-itsoffset 1.0`, so the first
  video packet is an IDR at `pts 60000` while three pictures remain at
  `57998 / 56997 / 58999`. Here `--avsw` **also** loses 3 — because the shared
  frame-position path (§3.2) prunes on the same "below the first keyframe"
  rule — which is exactly what the code predicts and what makes §3.1 and §3.2
  two views of one mistake rather than two unrelated bugs.

---

## 5. Answer to question C — is the GPU pipeline preserved?

Yes, and the reason is structural rather than measured: the candidate patch
changes **a condition around `m_dec->frameQueue()->releaseFrame(...)`** — that
is, whether an already-decoded GPU surface is forwarded to the next pipeline
task or returned to the decoder's pool. It does not:

* change the reader (`avcuvid` is still what opens the file),
* change `CuvidDecode` / `DecodePacket` / `cuvidParseVideoData`,
* insert a surface download, a host copy, or an `hwdownload`,
* touch `NVEncCore::initSWVideoDecoder` or any `libavcodec` software decoder,
* change encoder parameters, GOP structure, rate control or muxing.

Evidence that the GPU path is what actually runs (collected per run, from the
tool's own declarations — not from frame counts):

| Evidence line | Required value | Observed |
|---|---|---|
| `Input Info` | `avcuvid: …` (never `avsw:`) | `avcuvid: H.265/HEVC, 3840x2160, 60000/1001 fps` on every `--avhw` row |
| `Vpp Filters` | GPU-only (`copyDtoD`) | `copyDtoD` |
| `Output Info` | `avwriter: hevc => mp4` from NVENC | present |
| `NVENC / CUDA` | NVENC API engaged | `NVENC API 13.1, CUDA 13.4, schedule mode: auto` |
| Engine counters | NVIDIA **VE** (encoder) and **VD** (decoder) busy | 330 f run: `VE: 58.3`, `VD: 24.0` |
| `Input Buffers` | CUDA surfaces, not host frames | `CUDA, 16 frames` |

The `VD` (hardware decoder engine) counter being non-zero on the `--avhw` run is
the strongest single piece of non-frame-count evidence: the NVDEC engine is
doing the decoding. `--avsw` shows no `VD` figure on the same clip while showing
`VE` (NVENC) — software decode, hardware encode, exactly as designed.

---

## 6. Candidate patch

Stored at `work/hwdecode2/patch/0001-avhw-keep-leading-pictures.patch`
(apply helper: `work/hwdecode2/patch/apply_patch.py`). **18 added lines, 2 files,
no deletions, no behaviour change to normal files.**

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

### Why this shape

* **It keeps the OpenGOP/seek correction exactly where it was needed.** The
  filter's own source comment names two reproduction samples, and both are
  `--seek` runs. With `--seek` active, `m_seek.first > 0` and the original code
  runs unchanged.
* **It is inert on ordinary media.** The new branch fires only when the decoder
  emits a picture whose PTS is below the PTS of the first packet *and* no seek
  was requested. For a stream whose first packet is an IRAP at the earliest
  presentation time (x265 output, DJI, synthetic, and every normal file), no
  such picture exists, so the branch is unreachable and the frame sequence is
  bit-for-bit unchanged. Fixtures C, D and the DJI control are exactly this case:
  they are already exact today and the patch does not touch them.
* **It removes no frames and adds none.** It affects only which decoded frames
  are released back to the decoder pool versus forwarded downstream. The decoded
  picture sequence is untouched — the pictures were always there (§2.2).
* **It changes no encoding parameter** and no device/GPU selection.

### Known incompleteness (declared, not hidden)

The patch does **not** address §3.2 (`FramePosList::setPocAndFix`). Consequences,
stated plainly:

* Output frame sequence: fixed.
* Reported input frame count (`N frames, End of file`) and `--log-framelist`:
  **still pruned** for leading-picture streams, so those numbers can still read
  27 while the encoder emits 30.
* That inconsistency is a verification hazard and is the reason a *complete*
  fix needs the second hunk as well (or a downstream verification that uses the
  delivered container rather than the reader's own estimate).

A complete second hunk is not included here because it changes shared
QSVEnc/NVEnc/VCEEnc reader code and its OpenGOP handling (the `<= 16` window)
could not be validated without the patched binary. Writing it unverified would
be worse than declaring it missing.

---

## 7. Risks

| Risk | Assessment | Mitigation |
|---|---|---|
| Patch changes behaviour on normal media | **Low.** Unreachable unless a decoded picture's PTS precedes the first packet's PTS with no seek. Controls C/D/DJI are unaffected by construction. | Keep the fixture set in CI: any change to C/D/DJI counts is a regression. |
| Patch breaks `--seek` handling | **Low.** With `--seek`, `m_seek.first > 0` and the original path runs verbatim; segment/parallel encode sets `m_endPts`, handled before this code. | Re-run the named OpenGOP sample from the source comment (`--seek 6.66667`) before shipping. |
| Patch masks a genuine second defect | **Medium.** §3.2 remains. A stream that loses frames for a *different* reason would now be silently reported as complete. | Use container-derived frame counts in verification (Phase 1 S1–S3), never the reader's own estimate. |
| Upstream divergence | **Medium.** Any fork must track rigaya's `NVEncPipeline.h`, which changes frequently. | Keep the patch as a 2-file diff and re-apply on each upstream bump; do not fork the whole tree. |
| Licensing/distribution | **Medium.** NVEnc is MIT, but the build needs the NVIDIA Video Codec SDK headers (redistributable) plus CUDA headers/libs; a shipped fork inherits that. | Nobody ships a patched NVEncC unless they are willing to own the build. |
| Single-GPU host | **Untested here.** Device selection and multi-GPU interactions were not exercised. | Do not claim multi-GPU safety; re-test on a 2-GPU host before enabling. |
| Concurrency | **No regression observed.** 2×`--avhw` and `--avhw`+`--avsw` in parallel both returned the expected 27 / 27 and 27 / 30 with `rc=0` and no `Break in task NVDEC` — Phase 1's one-off abort did not reproduce in 12 sequential + 4 concurrent runs. | Keep the no-two-heavy-GPU-jobs rule for *measurements*; it is not needed for correctness here. |

---

## 8. Multi-GPU and parallel encode

**Not tested — the host has one GPU.** Recorded verbatim rather than simulated:

* `--check-device` → `DeviceId #0: NVIDIA GeForce RTX 5070 Laptop GPU` only.
* `--avhw -d 0` → `encoded 27 frames`, `rc=0`.
* `--avhw -d 1` → `Invalid Device Id = 1`, `Failed to initialize devices.`, `rc=1`.
* Parallel encode (`--split-enc`, `--parallel`) was deliberately **out of scope**
  for this phase: the instruction was not to fold the multihw refactor into this
  work, and `m_endPts` handling sits *before* the patched code but its
  interaction cannot be validated on a single-GPU host.

What the source says, for a future run: the patched branch is inside the
`m_gotFrameAfterFirstPts` fast path, and `m_endPts >= 0` (segment mode) returns
before it. Parallel-encode segments therefore behave as before.

---

## 9. What was NOT done (and why) — required reading

The instruction to produce an A/B (`before` / `after`) with the patch applied was
**attempted and not completed**. Stating this precisely matters more than
appearing finished.

**Attempted.** A full source build of NVEncC 9.31 from `rigaya/NVEnc @ 2cb9d81`
was set up (script: `work/hwdecode2/build_nvencc.ps1`) with a toolchain assembled
entirely from redistributable sources, deliberately avoiding the multi-GB NVIDIA
installer:

| Component | Source used | Result |
|---|---|---|
| MSVC | Visual Studio BuildTools 18, MSVC 14.51.36256 | OK |
| CUDA 12.9 headers / `cuda.lib` / `cudart.lib` | conda env `cudanvcc` (`nvidia` channel) | OK |
| `nvcc` 12.9.86 | conda env `cudanvcc` | OK (`nvcc -V` verified) |
| NPP headers + DLLs | PyPI wheel `nvidia-npp-cu12` (win_amd64) | OK |
| NVRTC header + lib | PyPI wheel `nvidia-cuda-nvrtc-cu12` | header/DLL OK |
| FFmpeg dev (headers + import libs) | conda env `ffdev` (conda-forge ffmpeg 9.0.1) | OK |
| rigaya's `ffmpeg_lgpl` + PyPI CUDA wheels + `cudatk` conda env | `third_party/` | OK |

**Blocker.** `NVEnc.sln` builds CUDA through the Visual Studio CUDA
build-customization rule; `cudaver.props` imports
`$(CUDA_PATH)\..\..\MSBuild\Microsoft.Cpp\v4.0\BuildCustomizations\CUDA 12.9.props`,
which exists only in the official NVIDIA Windows installer. It is **not** present
in the conda `cuda-nvcc` / `cuda-toolkit` packages, not in the PyPI wheels, and
the NVIDIA download endpoint for the installer is unreachable from this host
(`developer.download.nvidia.com` redirects to a `.cn` mirror that does not serve
this host's requests). MSBuild consequently fails with
`error MSB4019: 找不到导入的项目 "…\CUDA 12.9.props"` after `NVEncSDK` and
`tinyxml2` build successfully. Replacing the CUDA rule with 85 hand-written
`nvcc` invocations plus a hand-written link line for a 300+ file project was
judged out of proportion for this phase.

**Therefore:**

* The `after` half of the A/B does **not** exist.
* §7's claim that the patch is inert on ordinary media is argued from the code and
  supported by the pre-patch controls C/D/DJI (which are already exact) — it is
  **not** an observation of the patched binary.
* Nothing in this document should be read as "the patched tool was measured".

**The build is one dependency away.** On a host that can reach
`developer.download.nvidia.com` (or that has any CUDA Toolkit 12.x+ installed),
`work/hwdecode2/build_nvencc.ps1` should complete as written, and then:

```powershell
python work/hwdecode2/patch/apply_patch.py <NVEncC source root>
# rebuild, then:
python work/hwdecode2/run_matrix.py   --tag after --binary patched --reader avhw --reader avsw
python work/hwdecode2/run_fixtures.py after patched
```

Expected `after` result, stated in advance so it can be falsified:
`sony-min-30f --avhw → 30`, `sony-330f --avhw → 330`, `sony-h264-422 --avhw → 195`,
`control-dji` unchanged at 105, `A_sony_copy --avhw → 30`, and every `--avsw` row
unchanged. If any of those `--avsw` rows moves, the patch is wrong.

### 9.1 Verification that *was* completed

To be clear about what is and is not inference:

| Claim | Basis in this phase |
|---|---|
| the decoder feeds 30 packets and emits 30 pictures | NVEncC's own trace (`Set packet` ×30, `input frame (dev)` ×27 with the 3 leading ones present) |
| the loss is downstream of the decoder | same trace: the 3 leading pictures appear in the `input frame (dev)` stream and are absent from `encoded N frames` |
| the loss equals the leading-picture count | core matrix + fixture matrix, 2 codecs, 0-loss controls |
| the tool's own count and the delivered file agree | independent `ffprobe -count_frames` on every row |
| GPU decode → GPU encode is what runs | `avcuvid` + `copyDtoD` + NVIDIA `VE`/`VD` engine counters (§5) |
| the patch is inert on ordinary media | argued from the guard condition; **controls C/D/DJI are exact today and the patch does not reach their code path** |

---

## 10. Performance summary

Indicative only, uncontended, sequential, thermally loaded laptop — **not** a
benchmark, and not the basis of any verdict.

| clip | reader | frames out | wall s | fps | CPU % | VRAM peak MiB |
|---|---|---|---|---|---|---|
| Sony 30 f | `--avhw` | 27 | 1.99 | 43.7 | 2.8 | 1604 |
| Sony 30 f | `--avsw` | 30 | 2.48 | 28.2 | 17.1 | 1102 |
| Sony 330 f | `--avhw` | 327 | 4.07 | 120.3 | 5.7 | 1610 |
| Sony 330 f | `--avsw` | 330 | 6.15 | 78.7 | 36.4 | 1091 |
| Sony H.264 195 f | `--avhw` | 193 | 3.00 | 108.4 | 5.6 | 1854 |
| Sony H.264 195 f | `--avsw` | 195 | 3.35 | 87.6 | 37.7 | 1094 |
| DJI 105 f | `--avhw` | 105 | 2.53 | 80.3 | 5.1 | 1975 |
| DJI 105 f | `--avsw` | 105 | 6.43 | 19.7 | 15.1 | 1285 |
| Sony 10170 f → 3000 | `--avhw` | 2997 | 39.61 | 78.1 | 7.4 | – |
| Sony 10170 f → 3000 | `--avsw` | 2997 | 37.22 | 83.0 | 36.5 | – |

The 3000-frame rows are the representative ones; see §4.2 for why the CPU
difference survives and the speed difference does not.

---

## 11. Artifacts

Tooling (all under `F:\1KT-avhw\work\hwdecode2`, `work/` is gitignored by project
convention so these stay local):

| File | Role |
|---|---|
| `hwenv2.py` | pinned tools, ffprobe/ffmpeg measurement, NVEncC runner, VRAM sampler |
| `run_matrix.py` | core + long experiment matrix, resumable JSON output |
| `make_fixtures.py` | builds fixtures A–F from pinned sources |
| `run_fixtures.py` | fixture × reader outcome table |
| `run_longview.py` | bounded head-views of genuinely long Sony clips |
| `run_concurrency.py` | device selection, repeatability, concurrent runs |
| `build_nvencc.ps1` | reproducible build recipe (blocked at §9) |
| `patch/0001-avhw-keep-leading-pictures.patch` | the candidate patch (18 added lines) |
| `patch/apply_patch.py` | BOM- and CRLF-safe applier |
| `raw/matrix-baseline.json` | core matrix, full metrics |
| `raw/matrix-longview.json` | long-clip head views (3000 frames) |
| `raw/matrix-long.json` | **superseded / invalid** — asked 3600 and 18000 frames of a 30-frame source; NVEncC clamped to EOF, so those rows measured nothing and are kept only as a record of the mistake |
| `raw/fixtures-baseline.json` | fixture outcomes |
| `raw/concurrency.json` | device + concurrency probes |
| `raw/trc-*.txt` | the decisive trace (`Set packet` / `input frame (dev)`) |
| `raw/dbg-*.txt`, `raw/pkt-*.txt`, `raw/fpos-*.txt` | NVEncC debug, packet and framelist logs |
| `raw/*.log`, `raw/*.mp4` | per-run debug logs and delivered outputs |
| `fixtures/fixtures-report.json` | fixture container facts |
| `fixtures/` | the fixture media itself (local) |

Upstream source: `rigaya/NVEnc` tag `9.31`, commit
`2cb9d810c045202548b98ff130b12bc764eb39ea`, cloned into
`F:\1KT-avhw\third_party\NVEncC`.

---

## 12. Recommendation

**Do not adopt the rigaya `--avhw` reader in 1KeyTranscoder, patched or not.**

The experiment changes the *explanation* but not the *conclusion*:

1. **The defect is fixable, and it is small.** The previous phase could only say
   "the rigaya reader drops leading pictures — exact line unknown". That is now
   known: an 18-line change in `NVEncPipeline.h` / `rgy_input.h`, in a code path
   that is provably unreachable for media whose first packet is the IDR at the
   earliest presentation time.

2. **But fixing it means owning a fork of a media tool.** The patch is not
   upstreamable as-is (`m_hwDecFirstPts` exists for a reason; rigaya would need
   to accept the seek-vs-non-seek distinction, on a code path where a regression
   is a silent frame loss). A private fork of NVEncC carries a build burden
   (§9), an NVIDIA SDK dependency, and a permanent re-apply cost on every
   upstream bump — for a change whose only benefit over the alternative is
   avoiding an FFmpeg call.

3. **The alternative is strictly better for this project.** Phase 1 proved
   FFmpeg's own NVDEC path is **bit-identical to software decode on 151/151 Sony
   files**. It needs no fork, no patch, no SDK, and it is already the mechanism
   `1KeyTranscoder` would have to understand for QSV anyway. Choosing the
   patched-rigaya route means maintaining a fork *and* still implementing the
   format-level capability routing.

4. **The one thing worth taking from this phase is the verification rule.**
   §3.2 shows that a tool can deliver the right frame *sequence* while reporting
   the wrong frame *count* about itself. A pipeline that reconciles
   `container samples`, the reader's estimate, the encoder's `encoded N frames`
   and an independent count of the delivered file — as this phase's matrix does
   — is worth more than any reader patch. That belongs in the production design
   regardless of which decoder wins.

**If hardware decode via NVEncC is nonetheless required** (e.g. to keep a single
toolchain for NVDEC + NVENC), then the order of work is:

1. Build the patched binary and run the A/B in §9. Do not ship on the strength of
   this document alone.
2. Extend the patch to §3.2 so the reported input count matches the delivered
   count, or make downstream verification ignore the reader's estimate entirely.
3. Re-run the full 151-file Sony corpus plus DJI with the four-way count
   reconciliation, and re-test on a multi-GPU host before enabling device
   selection.
