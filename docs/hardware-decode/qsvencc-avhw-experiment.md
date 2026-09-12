# QSVEncC `--avhw` — Behaviour, Failure Modes and Root Cause

`research/rigaya-qsvencc-avhw` · base `main` @ `15cf218` · worktree `F:\1KT-qsv`

> **Scope.** Read-only investigation. No production code in `main` or in the
> 1KeyTranscoder tree was touched; every experiment ran in the separate
> worktree `F:\1KT-qsv` on branch `research/rigaya-qsvencc-avhw`. Nothing here
> proposes changing the shipping default backend.

> **Follow-up available.** The open question this document records as
> `Unconfirmed` — which statement removes the three frames — has since been
> resolved by an instrumented from-source build, and a PoC patch has been
> measured. See [`qsvencc-root-cause.md`](qsvencc-root-cause.md).
> This document's measurements stand as written; §6.4 and §7 are updated inline.

> **The one distinction this document exists to make.** Four outcomes look
> similar in a log and are completely different in consequence:
> **capability refusal**, **reader truncation**, **software fallback** and
> **genuine hardware decode**. Conflating the first with the second is the
> specific error this investigation is written to prevent: an `unsupported`
> message is **not** a `frame drop`.

---

## 0. Question and answer

| # | Question | Verdict | Confidence |
|---|---|---|---|
| 1 | What does QSVEncC `--avhw` really do on Sony XAVC? | Runs the **`avqsv` hardware reader**, decodes on the Arc 140T, and **emits 3 fewer pictures than the container holds** | `Confirmed` |
| 2 | What does it do on DJI? | Runs the **same** `avqsv` hardware reader and is **frame-exact** | `Confirmed` |
| 3 | FFmpeg QSV on the same clips? | **Frame-exact on every clip**, both codecs | `Confirmed` |
| 4 | Are the four outcomes distinguishable? | **Yes** — by two independent facts: the reader-identity line and the frame count | `Confirmed` |
| 5 | Does the QSV reader show the same Sony head-truncation as NVEncC? | **Yes — identical, 3 frames, same clips** | `Confirmed` |
| 6 | Do the two readers share logic? | **Yes — the reader source is byte-for-byte identical** | `Confirmed` |
| 7 | Is a minimal patch feasible? | The defect is in **one shared file**, but a correct fix needs a **decode-path-aware** change, not a one-token revert | `Likely` |
| 8 | Is the hardware decode → QSV encode GPU pipeline preserved? | **Yes — confirmed under `--avhw`, and absent under `--avsw`** | `Confirmed` |
| 9 | Does `--avhw` buy throughput on this machine? | **No.** It buys **CPU headroom** (2.7 % vs 40.9 % CPU) | `Confirmed` |

**Headline.** On this machine, for this corpus, `--avhw` is a **correctness
regression that also happens to be the only configuration that keeps the
picture path on the GPU** — and on 5 of 151 Sony files it does not run at all,
for a reason that has nothing to do with any of that.

---

## 1. Environment and exact subject

Nothing in this document is version-agnostic, so the subject is pinned.

| Item | Value |
|---|---|
| CPU | Intel Core Ultra 9 285H (6P+10E), Arrow Lake |
| GPU | **Intel Arc 140T**, `8086:7d51` |
| Driver | `32.0.101.8974` |
| VPL / Media SDK | QuickSyncVideo API **v2.17** (`mfx-gen`, `d3d11`) |
| QSVEncC | **8.26 (r4504)**, built 2026-08-08 |
| QSVEncC source | `rigaya/QSVEnc` @ **`b14c965`** (matches 8.26 exactly) |
| NVEncC | **9.31**, built 2026-08-08 |
| NVEncC source | `rigaya/NVEncC` @ `2cb9d81` |
| FFmpeg | **9.0.1**, `--enable-libvpl --enable-ffnvcodec` |

The source revision was not guessed. `QSVEncC_version.h` and
`QSVPipeline/rgy_version.h` were read from candidate commits and the header
declaring `VER_STR_FILEVERSION "8.26"` was selected:

```
PINNED: b14c9652b34b998351516cc63c86eb990c01c7c8 2026-08-08 21:14:16 +0900
#define VER_STR_FILEVERSION          "8.26"
```

Both rigaya encoders shipped on the same day, which is what makes the
reader-source comparison in §6 meaningful.

---

## 2. The four outcomes, defined operationally

The point of this section is that each class is assigned from **two
independent facts**, never from one. A count alone cannot distinguish
"refused" from "ran correctly on a shorter clip"; a log line alone cannot
distinguish "hardware ran" from "hardware ran and lost frames".

| Class | Reader-identity line | Frames emitted | Output file | Loud? |
|---|---|---|---|---|
| **CAPABILITY_REFUSAL** | *never printed* — fails before a reader exists | **none** | **not created** | yes, `rc != 0` |
| **READER_TRUNCATION** | `avqsv` | **n − k**, contiguous head loss | created | **no — exits 0** |
| **SOFTWARE_FALLBACK** | `avsw` **after** `--avhw` was requested | full | created | only via a warning |
| **TRUE_HW_DECODE** | `avqsv` | **all n** | created | n/a |

`n` = container sample count. `k` = pictures preceding the first sync sample
in presentation order.

The identity line is the `Input Info` line of rigaya's own report:

```
Input Info     avqsv: H.265/HEVC, 3840x2160, 60000/1001 fps     <- hardware reader
Input Info     avsw: hevc(yv12(10bit))->nv12 [AVX2], 3840x2160  <- software reader
```

**Methodological warning.** `Input Info` is emitted at *info* level. Running
with `--log-level error` suppresses it, and the reader identity then reads as
`UNKNOWN`. An experiment that used `--log-level error` would have reported
"cannot tell which reader ran" — or worse, assumed. All results below were
re-taken at `--log-level info`.

**A second trap, recorded because it silently corrupts measurements.**
`QSVEncC -c raw` does **not** write headerless raw video. It writes
**Y4M**:

```
YUV4MPEG2 W256 H144 F60000:1001 Ip A1:1 C420jpeg XYSCSS=420JPEG
FRAME\n<payload>FRAME\n<payload>...
```

Two more traps follow from this, both of which produced wrong numbers here
before being caught:

* `--output-res 256x256` **preserves aspect ratio** and produced
  **256×160**, and the declared chroma was **4:2:2** despite
  `--output-csp yuv420`. Slicing frames by the *requested* geometry is
  therefore wrong; the frame layout must be derived from the file. The tooling
  in `work/qsvavhw/y4mhash2.js` reads the Y4M header and confirms payload
  size by requiring uniform marker gaps.

---

## 3. Corpus and method

### 3.1 Clips

13 clips, chosen to span every class rather than to be large. The frozen
list is `work/qsvavhw/clipset.txt`.

| Group | Clips | Expected class |
|---|---|---|
| Sony XAVC HS, HEVC Main10 4:2:0, 59.94p | 4 | READER_TRUNCATION |
| Sony XAVC S, H.264 High 4:2:2 10-bit, 29.97p | **all 5** | CAPABILITY_REFUSAL |
| DJI (Action 4 / Air 3S), HEVC Main10 4:2:0 | 4 | TRUE_HW_DECODE |

The DJI clips are the essential control: they are **not** Sony material, have
`media_time = 0`, carry **no `ctts` box at all**, and code **zero** pictures
before their first keyframe.

### 3.2 The predictor

Frame loss is predicted from container boxes alone, with no decode
(`work/qsvavhw/mp4struct.py`, moov-only reads):

```
pictures_before_first_keyframe
    = |{ i : PTS[i] < PTS[first sync sample] }|

    DTS[i] = cumsum(stts)[i]
    PTS[i] = DTS[i] + ctts[i]

predicted_avhw_frames = n_samples − pictures_before_first_keyframe
```

### 3.3 Five measurements per clip

| Measurement | Tool | Purpose |
|---|---|---|
| container facts | `mp4struct.py` | the prediction, and the edit list |
| `--avsw` frames + reader | QSVEncC | same-encoder software control |
| `--avhw` frames + reader | QSVEncC | **the subject** |
| FFmpeg software frames | `ffmpeg -f null` | independent ground truth |
| **FFmpeg QSV frames** | `ffmpeg -hwaccel qsv -c:v hevc_qsv` | **capability-separating control** |

FFmpeg counts are taken with the null muxer so FFmpeg itself reports the
count; this avoids every raw-geometry ambiguity in §2.

---

## 4. Question 1 and 2 — Sony and DJI

Full matrix, 13 clips, all five measurements per clip.

| Clip | samples | leading | `--avsw` | `--avhw` | reader | FFmpeg sw | FFmpeg QSV | Class |
|---|---|---|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 3 | **30** | **27** | `avqsv` | 30 | 30 | READER_TRUNCATION |
| `20260903_C1158.MP4` | 150 | 3 | 150 | **147** | `avqsv` | 150 | 150 | READER_TRUNCATION |
| `20260903_C1177.MP4` | 150 | 3 | 150 | **147** | `avqsv` | 150 | 150 | READER_TRUNCATION |
| `20260903_C1093.MP4` | 180 | 3 | 180 | **177** | `avqsv` | 180 | 180 | READER_TRUNCATION |
| `C9037.MP4` | 195 | 2 | 195 | — | **REFUSED** | 195 | **0** | CAPABILITY_REFUSAL |
| `C9073.MP4` | 150 | 2 | 150 | — | **REFUSED** | 150 | **0** | CAPABILITY_REFUSAL |
| `C9088.MP4` | 165 | 2 | 165 | — | **REFUSED** | 165 | **0** | CAPABILITY_REFUSAL |
| `C9110.MP4` | 630 | 2 | 630 | — | **REFUSED** | 630 | **0** | CAPABILITY_REFUSAL |
| `20260823_C0887.MP4` | 195 | 2 | 195 | — | **REFUSED** | 195 | **0** | CAPABILITY_REFUSAL |
| `DJI_..._0070_D.MP4` | 16 | **0** | 16 | **16** | `avqsv` | 16 | 16 | TRUE_HW_DECODE |
| `DJI_..._0081_D.MP4` | 286 | **0** | 286 | **286** | `avqsv` | 286 | 286 | TRUE_HW_DECODE |
| `DJI_..._0073_D.MP4` | 297 | **0** | 297 | **297** | `avqsv` | 297 | 297 | TRUE_HW_DECODE |
| `DJI_..._0009_D.MP4` | 105 | **0** | 105 | **105** | `avqsv` | 105 | 105 | TRUE_HW_DECODE |

**Predictor: 8/8 correct, 0 wrong** (the 5 refusals are excluded because no
hardware decode happened, so there is no frame count to predict — this
exclusion is the whole point of §5).

### 4.1 The loss is a head truncation, not a drop, and not a tail problem

Frame counts alone cannot say *which* pictures vanished. Per-frame SHA-256 of
every emitted picture (`frame_experiment2.ps1`, `sony_C1170`, 256×144,
identical output geometry across all four runs):

| index | B `--avsw` | C `--avhw` |
|---|---|---|
| 0 | `26f33d2620c7ee44` | `90f30ba4ff324dd4` |
| 1 | `0823e50857121a72` | `a84987b761e596f3` |
| 2 | `feb305ca2e39c8f5` | `3686092a4d65ab96` |
| 3 | `90f30ba4ff324dd4` | `f5cbc12824f49b9d` |
| 4 | `a84987b761e596f3` | `a348361b36923bba` |
| 5 | `3686092a4d65ab96` | `f3d164d95bc0186c` |

Set arithmetic, `B` vs `C`:

```
missing = 3   { 26f33d2620c7ee44, 0823e50857121a72, feb305ca2e39c8f5 }
extra   = 0
C[0] == B[3]      -> test0_matches_ref_index = 3
contiguous        = True
```

`--avhw[i] == --avsw[i+3]` for all `i`, `extra = ∅`, and the last three
hashes are identical on both sides. Therefore:

* it is **not** `FLUSH_LOSS` — the tail is intact and identical;
* it is **not** `FRAME_REORDER` or `FRAME_DUPLICATE` — the emitted stream is a
  strict contiguous suffix;
* it is **not** `PTS_ERROR` — pictures are absent, not restamped;
* it **is** a **3-frame head truncation**: `FRAME_DROP`, localised to the
  first three presented pictures.

Cross-checked at the window level: with `--trim 3:32` (a 3-frame window)
`--avhw` again emitted exactly 3 fewer pictures, and its emitted sequence
again started 3 positions into `--avsw`'s. The deficit is **invariant to the
requested window**.

### 4.2 On DJI the same reader is exact

All four DJI clips: `--avsw` == `--avhw` == FFmpeg sw == FFmpeg QSV, and
`pictures_before_first_keyframe == 0` on every one. The reader is not broken
in general — it is broken on the structure Sony encodes. This is the control
that makes the Sony result attributable to the *stream structure* rather than
to "QSV is broken".

---

## 5. Question 4 — capability refusal vs everything else

This is the distinction the investigation exists for, so it is demonstrated
two ways.

### 5.1 The refusal fails before a reader exists

`--avhw` on `C9037.MP4` (H.264 High 4:2:2 10-bit), complete output:

```
--------------------------------------------------------------------------------
probe6\c9037.y4m
--------------------------------------------------------------------------------
avqsv: codec h264(yuv422p10le) unable to decode by qsv.
failed to initialize file reader(s).

QSVEncC.exe finished with error!
exit=-31
```

| Property | Value |
|---|---|
| Output file created | **No** — `Get-ChildItem probe6\c9037.y4m` finds nothing |
| Frames emitted | **0** |
| Reader-identity line | **never printed** |
| Exit code | **−31** (non-zero) |
| Affected | **5 / 5** H.264 High 4:2:2 10-bit clips |

There is **no `Input Info` line in the output at all**, because the failure
occurs while the reader is still being constructed. Nothing was decoded and
nothing was written. **A refusal cannot be a frame drop: there are no frames.**

Contrast with the truncation case, which exits **0** while silently losing 3
pictures. That asymmetry is the operational danger, and it runs the opposite
way to intuition: **the loud failure is the safe one.**

### 5.2 The refusal is a pre-flight capability table, not a decode failure

The message is produced by a **table lookup performed before any decode**:

`QSVPipeline/rgy_input_avcodec.cpp:565`
```cpp
RGY_CODEC RGYInputAvcodec::checkHWDecoderAvailable(AVCodecID id, AVPixelFormat pixfmt, const CodecCsp *HWDecCodecCsp) {
    for (int i = 0; i < _countof(HW_DECODE_LIST); i++) {
        if (HW_DECODE_LIST[i].avcodec_id == id) {
            auto rgy_codec = HW_DECODE_LIST[i].rgy_codec;
            if (HWDecCodecCsp->count(rgy_codec) > 0) {
                const auto rgy_csp = csp_avpixfmt_to_rgy(pixfmt);
                auto& csp_list = HWDecCodecCsp->at(rgy_codec);
                if (std::find(csp_list.begin(), csp_list.end(), rgy_csp) != csp_list.end()) {
                    return rgy_codec;
                }
            }
            return RGY_CODEC_UNKNOWN;   // -> error path below
        }
    }
    return RGY_CODEC_UNKNOWN;
}
```

and the error is raised at `rgy_input_avcodec.cpp:2079-2088`:

```cpp
if (m_inputVideoInfo.codec == RGY_CODEC_UNKNOWN
    //wmv3はAdvanced Profile (3)のみの対応
    || (m_Demux.video.stream->codecpar->codec_id == AV_CODEC_ID_WMV3 && m_Demux.video.stream->codecpar->profile != 3)) {
    if (m_inputVideoInfo.type == RGY_INPUT_FMT_AVHW) {
        //HWデコードが指定されている場合にはエラー終了する
        AddMessage(RGY_LOG_ERROR, _T("codec %s(%s) unable to decode by " DECODER_NAME ".\n"),
            char_to_tstring(avcodec_get_name(m_Demux.video.stream->codecpar->codec_id)).c_str(),
            char_to_tstring(av_get_pix_fmt_name((AVPixelFormat)m_Demux.video.stream->codecpar->format)).c_str());
        return RGY_ERR_INVALID_CODEC;
    }
}
```

Two consequences follow directly from the code, and both matter:

1. **With `--avhw` the codec/csp miss is a hard error.** There is no
   fall-through to software. `m_inputVideoInfo.type == RGY_INPUT_FMT_AVHW`
   selects an explicit `return RGY_ERR_INVALID_CODEC`.
2. The `CodecCsp` table is **not hardcoded** — it is produced by probing the
   VPL driver with `MFXVideoDECODE_Query` per candidate pixel format
   (`qsv_query.cpp:502` `CheckDecFeaturesInternal`, via
   `MakeDecodeFeatureList` → `getHWDecCodecCsp`). A format the driver will not
   admit is simply absent from the table, and a format absent from the table
   is refused.

### 5.3 The capability table as the driver reports it

`QSVEncC64.exe --check-features`, decode section, verbatim:

```
Supported Decode features:

        H.264  HEVC   MPEG2  VP8    VP9    AV1    VVC
yuv420  8bit  10bit   8bit         10bit  10bit
yuv422        10bit                10bit
yuv444        12bit                12bit
```

The **H.264 column of the `yuv422` row is empty.** HEVC and VP9 have
`yuv422 10bit`; H.264 has nothing. This is the capability refusal stated as a
table, and it is the *only* thing separating the 5 H.264 files from the 146
HEVC ones.

Cross-checked against rigaya's own published device reports already in the
tree — the same column is empty on **every** Intel platform on record:

| Device | H.264 `yuv422` | Source |
|---|---|---|
| Arc 140T (this machine) | *(empty)* | measured here |
| Arc **B580** (Xe2, Battlemage) | *(empty)* | `docs/reference/qsv/QSVEnc_BMG_Arc_B580_Win.txt` |
| Arc **A380** (Xe-HPG, DG2) | *(empty)* | `docs/reference/qsv/QSVEnc_DG2_Arc_A380_Win.txt` |
| Core Ultra 5 245K (ARL) | *(empty)* | `docs/reference/qsv/QSVEnc_ARL_u5_245K_Win.txt` |

**Scope of the claim, stated precisely.** What is `Confirmed` is that *the
QSV decode path on this machine and on three other Intel platforms refuses
H.264 4:2:2 10-bit, and that this refusal is a pre-flight capability
determination*. What is **not** claimed is whether the limit is Xe2 silicon or
the VPL/MSDK stack that exposes it: every observation here goes through the
same library, so the two cannot be separated by this evidence. This
investigation deliberately does **not** call it a silicon fact.

### 5.4 The bypass does not exist in 8.26

The source contains a debug switch intended to skip exactly this check
(`rgy_cmd.cpp:12992`):

```cpp
if (IS_OPTION("skip-hwdec-check")) {
    ctrl->skipHWDecodeCheck = true;
    return 0;
}
```

which would install a permissive all-formats table (`qsv_query.cpp:1521`).
It is **undocumented** (absent from `--help`) and in 8.26 it has **no
effect** — the refusal is byte-identical with and without it, and the decode
capability matrix is unchanged:

```
--skip-hwdec-check --avhw -i C9037.MP4
avqsv: codec h264(yuv422p10le) unable to decode by qsv.     <- unchanged
QSVEncC.exe finished with error!                             rc=-31
```

Recorded because it is a tempting "just force it" path that would silently do
nothing. It also means the refusal cannot be converted into a reader test on
this build.

### 5.5 Software fallback exists, and it is a different mechanism

Tri-state summary for an H.264 4:2:2 file:

| Configuration | Outcome | Exit |
|---|---|---|
| `--avsw` | decodes fine, `avsw` reader, 195/195 frames | 0 |
| `--avhw` | **CAPABILITY_REFUSAL** — no reader, no frames, no output | −31 |
| `--avhw` on a *supported* format | **READER_TRUNCATION** or **TRUE_HW_DECODE** | 0 |

The `avqsv → avsw` substitution that the project previously warned about
(`docs/design/hardware_backend_design.md` §5.6) is **a separate and much
narrower path**. In the source it is reachable only when header extraction
fails (`rgy_input_avcodec.cpp:2137-2160`):

```cpp
if (sts == RGY_ERR_MORE_DATA
    && (m_Demux.video.bsfcCtx || m_Demux.video.bUseHEVCmp42AnnexB)
    && !m_Demux.video.hdr10plusMetadataCopy
    && !m_Demux.video.doviRpuMetadataCopy) {
    if (m_inputVideoInfo.codec != RGY_CODEC_UNKNOWN) { //hwデコードを使用していた場合
        AddMessage(RGY_LOG_WARN, _T("Failed to get header for hardware decoder, switching to software decoder...\n"));
```

and it **emits an explicit warning**. Two things follow:

* **SOFTWARE_FALLBACK was not observed anywhere in this investigation.** It is
  documented here as a code path with a named trigger, not as a measured
  outcome — no result in this document rests on it.
* The fallback is **capability-driven only for header failure**. For a codec
  the capability table rejects, §5.2 shows the code returns
  `RGY_ERR_INVALID_CODEC` instead. The two must not be merged into one
  "fallback" story.

---

## 6. Questions 5 and 6 — is the QSV reader the NVEncC reader?

The prior investigation proved the same 3-frame Sony loss under NVEncC
`--avcuvid`. The question here is whether QSVEncC merely behaves alike or
shares the code.

### 6.1 The source files are byte-for-byte identical

```
195A3311FEFDBA53F332D2BE925183D4625730FCF9FB37745A4803AB754586CD
    QSVEnc/QSVPipeline/rgy_input_avcodec.cpp      (229345 bytes)
195A3311FEFDBA53F332D2BE925183D4625730FCF9FB37745A4803AB754586CD
    NVEncC/NVEncCore/rgy_input_avcodec.cpp        (229345 bytes)

EFD88A9EFA7CFFA56B13C717EA1D179FCEFD265FCB4B2B25EBE456467B920496
    QSVEnc/QSVPipeline/rgy_input_avcodec.h        (58828 bytes)
EFD88A9EFA7CFFA56B13C717EA1D179FCEFD265FCB4B2B25EBE456467B920496
    NVEncC/NVEncCore/rgy_input_avcodec.h          (58828 bytes)
```

Same SHA-256, same byte count, **zero differing lines**. The hardware reader
is a single shared implementation compiled into both encoders; the encoder
identity is a compile-time macro:

```cpp
m_readerName = (m_Demux.video.HWDecodeDeviceId.size() > 0) ? _T("av" DECODER_NAME) : _T("avsw");
```

with `DECODER_NAME` = `qsv` for QSVEncC and `cuvid` for NVEncC. That is why
the two tools print different reader names while sharing one code path, and
why the identical `qsv_pipeline.h` comments still refer to `CUVID` and
`NVDecode` (lines 893-894) — a leftover from the shared lineage.

**Conclusion — `Confirmed`.** The QSV reader does **not** merely resemble the
NVEncC reader; it **is** the same source. A truncation defect in one is
necessarily present in the other, which is exactly what was measured: 3
frames, same clips, same position. The prior NVEncC finding transfers to QSV
and vice versa, and any fix must be made once, in the shared file.

### 6.2 The obvious suspect in the shared reader is not the trigger

The shared reader contains a gate that discards packets before the first
keyframe (`rgy_input_avcodec.cpp:3400-3408`):

```cpp
const bool keyframe = (pkt->flags & AV_PKT_FLAG_KEY) != 0 || pos.pict_type == AV_PICTURE_TYPE_I;
//最初のキーフレームを取得するまではスキップする
if (!bTreatFirstPacketAsKeyframe && !m_Demux.video.gotFirstKeyframe && !keyframe) {
    av_packet_unref(pkt.get());
    i_samples++;
    continue;
}
```

This is the natural explanation for a Sony head truncation and it is
**rejected by measurement** on this clip. From `ffprobe -show_packets`,
packet 0 is already the keyframe:

```
i=0  pts=3003  dts=-2002  flags=K__   <- first packet IS the keyframe
i=1  pts=1001  dts=-1001  flags=___
i=2  pts=0     dts=0      flags=___
i=3  pts=2002  dts=1001   flags=___
```

and the reader's own debug log agrees:

```
avqsv: found first key frame: timestamp 3003 (0.05005), offset 0
```

`offset 0` means **nothing was dropped by this gate.** Sony's leading
pictures are *after* the IDR in decode order and *before* it in presentation
order — exactly the case the reader anticipates a few lines later
(`:3434-3438`), where it merely counts them:

```cpp
} else if (auto timestamp = (pkt->pts == AV_NOPTS_VALUE) ? pkt->dts : pkt->pts;
           timestamp != AV_NOPTS_VALUE && timestamp < m_Demux.video.streamFirstKeyPts) {
    // OpenGOP等で、最初のキーフレームより前にBフレームがある場合がある
    m_trimParam.offset++;
```

which fires exactly 3 times, producing the `adjust trim by offset 3` seen in
the log. The frames are *counted*, not discarded, so this path is not the
removal site either.

### 6.3 Causes ruled out by experiment

Each candidate was tested, not argued away.

| Candidate | Test | Result | Verdict |
|---|---|---|---|
| Keyframe gate at `:3405` | `ffprobe` packet flags + reader log | first packet is the keyframe; `offset 0` | **Rejected** |
| Trim range arithmetic (`m_trimParam.offset`) | `--trim 0:30`, `3:29`, `0:26`, `6:29`, `10:29` | `--avhw` and `--avsw` return **identical** counts for every window — the offset layer is shared, not the differentiator | **Rejected** |
| VPP resize stage | `--avhw` at **native** resolution, no `--output-res` | still 27 vs 30 | **Rejected** |
| Predecode read-ahead window | `--input-analyze` 0 / 1 / 5 / 30 | **27 in every case** | **Rejected** |
| Decoder silicon | FFmpeg `hevc_qsv` over the same silicon, same clip | **30/30**, and bit-identical to software | **Rejected** |
| Content / clip length | 30, 150, 150, 180 frames | always exactly 3 (always = the leading count) | **Rejected** |

The last row is the sharpest: **FFmpeg's `hevc_qsv` drives the same Arc
decoder to a frame-exact result on the same file, while rigaya's `avqsv`
reader loses 3.** The decoder is therefore exonerated by a controlled A/B on
identical input, and the defect is in the reader/integration layer.

### 6.4 Superseded — the removal point has since been localised

> **Update.** This section previously recorded the exact removal statement as
> `Unconfirmed`. It has since been **found, instrumented and patched**. See
> [`qsvencc-root-cause.md`](qsvencc-root-cause.md).
>
> It is **not** in the reader. `PipelineTaskMFXDecode::sendBitstream()`
> (`QSVPipeline/qsv_pipeline_ctrl.h:1136-1138`) rejects every MFX output whose
> presentation timestamp is earlier than the first *fed* packet's PTS while
> `m_decFrameOutCount` is still 0 — and that counter cannot advance while
> rejections are ongoing, so it rejects **all** such pictures, not one.
> MFX had already decoded all 30.
>
> A 2-edit PoC patch restores `--avhw` to `n` with the output byte-identical to
> `--avsw` (30/30 per-frame SHA-256), leaving `--seek`, `--trim`, the DJI
> controls and the H.264 4:2:2 capability refusal unchanged.
>
> The original reasoning is kept below because it is still correct as far as it
> goes, and because the reader-side `FramePosList` removal it describes is a
> **real second defect** — it fires identically in both readers and therefore
> does not cause the `--avhw`-only loss. See `qsvencc-root-cause.md` §7.

The exact statement that removes the three pictures was **not** identified at
the time of Phase 1. `getSample()` builds a complete 30-entry frame-position
list and `m_trimParam.offset` only *counts* the leading pictures; the loss
appears after that, in the handoff from the reader to the hardware decode
pipeline. Consistent with this, the reader's own EOF line reports **28** frames
where the container holds 30, so part of the deficit is already present at read
time and part is not.

---

## 7. Question 7 — minimal patch feasibility

Assessed against the real source. There are two separate defects and they do
not have the same fix.

### 7.1 The capability refusal — **do not patch**

Correct behaviour is already implemented: it detects the unsupported format
before decoding, fails loudly, writes nothing, and exits non-zero. It is
benign from a data-integrity standpoint. The only thing missing is that
1KeyTranscoder should route around it at plan time (`caps.py`), rather than
discovering it at run time. No rigaya change is warranted.

### 7.2 The Sony head truncation — now localised and patched

> **Update.** This section previously rated the patch `Likely` and listed the
> unlocalised removal site as the blocking unknown. That unknown is resolved:
> the defect is at `QSVPipeline/qsv_pipeline_ctrl.h:1136-1138`, a 2-edit PoC
> patch exists and has been **built and A/B measured**. Full analysis:
> [`qsvencc-root-cause.md`](qsvencc-root-cause.md).
>
> Status: `ROOT CAUSE: Confirmed` · `PATCH: Runtime Proven (PoC)`.
> `--avhw` goes `n-3 → n` on four Sony cases with per-frame SHA-256 output
> identical to `--avsw`; `--seek`, `--trim`, DJI controls and the H.264 4:2:2
> refusal are all unchanged.
>
> The reasoning below is retained as the Phase-1 assessment, including the two
> predictions that turned out to be **wrong** — corrected inline.

Favourable facts:

* **~~One file.~~ One *repository*.** Corrected: the defect is **not** in
  `rgy_input_avcodec.*` (the byte-identical shared reader) but in
  `qsv_pipeline_ctrl.h`, which is QSVEncC-only. NVEncC has the same *rule* in
  `NVEncPipeline.h`. The two fixes are not interchangeable.
* **The prediction is exact.** `pictures_before_first_keyframe` is computed
  from `stts`/`ctts`/`stss` alone (no decode), and matched 8/8 measured cases
  here, on top of the previously validated 297/297. A fix can be *asserted*
  against a container-derived expectation rather than eyeballed.
* **The shape of the bug is known.** A strict contiguous head loss is the
  easiest divergence to detect and the easiest to state as an invariant.

Facts that made this expensive, now resolved:

* **~~The removal site is not localised.~~** It is: the MFX output admission
  filter. The candidate in the reader (`:3405`) was confirmed **not** the
  trigger, and the reader's `FramePosList::setPocAndFix` removal turned out to
  fire identically in both readers and therefore not to be the cause.
* **~~The correct fix is decode-path-aware.~~** Correct in substance, and
  cheaper than feared: no packet reordering was needed. MFX already emitted all
  30 pictures in display order — the pipeline was discarding them *after*
  decode. The fix only stops the discarding, gated on `--seek`.
* **The real difficulty was measurement, not the edit.** The static reading
  ("at most one frame can be dropped") was wrong, and only a from-source build
  with instrumentation settled it. The 2-edit patch is small; proving it was
  not.

**Patch shape that was actually required** (see `qsvencc-root-cause.md` §5):

1. Expose the reader's `--seek` parameter to the pipeline (`rgy_input.h`).
2. Apply the leading-picture rejection **only when a seek was requested**
   (`qsv_pipeline_ctrl.h`).
3. Verify with per-frame identity against `--avsw`, not counts — plus `--seek`
   as the regression guard for the gate that was added.

**Interim recommendation, unchanged from the prior investigation and
reinforced here:** do not use **stock** rigaya `--avhw` for Sony XAVC. Where
hardware decode is wanted, either drive it through FFmpeg `-hwaccel` — which is
frame-exact on every clip measured (§4), including H.264 4:2:2 10-bit, which QSV
refuses outright but NVDEC handles (`docs/hardware-decode/nvdec.md`) — or use the
**patched** QSVEncC path recorded in [`qsvencc-patch.md`](qsvencc-patch.md),
which restores `n` frames with output identical to `--avsw` on the pinned 8.26
revision. The patched route is an integration candidate, not a default, and the
version boundary in `qsvencc-patch.md` §4 (Q1) applies.

---

## 8. Question 8 — is the decode → encode GPU pipeline preserved?

Yes under `--avhw`. This is the property that makes `--avhw` interesting at
all, and it is demonstrated by the allocation chains rather than assumed from
the word "hardware".

### 8.1 rigaya: `--avhw` removes the upload stage

| `--avhw` | `--avsw` |
|---|---|
| `AllocFrames: Id: 0, MFXDEC-MFXVPP, type: external,dxvadec,dec,vppin` | `AllocFrames: Id: 0, INPUT-MFXVPP, type: external,dxvaproc,vppin` |
| `AllocFrames: Id: 1, MFXVPP-MFXENCODE, type: external,dxvadec,enc,vppout` | `AllocFrames: Id: 1, MFXVPP-MFXENCODE, type: external,dxvadec,enc,vppout` |
| — | `QSVAllocatorD3D11::AllocImpl create 9 textures, 9 staging textures` |

The distinguishing fact is the **producer of the first stage**:

* `--avhw`: **`MFXDEC-MFXVPP`** — the decoder's surfaces feed VPP directly.
* `--avsw`: **`INPUT-MFXVPP`** — decoded CPU frames must be **uploaded** to
  D3D11 surfaces (`staging textures`) before VPP.

Both paths then converge on `MFXVPP-MFXENCODE` carrying GPU surfaces, and both
share one D3D11 device:

```
MFXDEC:  Got HW device handle: 00000208AF809ED0.
MFXVPP:  Got HW device handle: 00000208AF809ED0.
MFXDEC:  set HW device handle 00000208AF809ED0 to encode session.
```

So: **the encode stage is always on the GPU; only `--avhw` keeps the picture
path on the GPU all the way from decode.** The pipeline is preserved — and the
`CPU: 2.7` vs `CPULoad: 40.9` in §9 is its signature.

> Note on a misleading log token: `dxvadec` appears in both paths, including
> inside `--avsw`'s `MFXVPP-MFXENCODE` line. It is a QSV memory-type tag in
> shared rigaya code, **not** evidence that the software reader decoded on the
> GPU. Trusting it would have produced a false "hardware decode was used"
> claim for `--avsw`.

### 8.2 FFmpeg: the same property, stated explicitly

```
[graph -1 input from stream 0:0] w:3840 h:2160 pixfmt:qsv tb:1/60000 ...
[hevc_qsv @ ...] Decoder: output is video memory surface
[hevc_qsv @ ...] Encoder: input is video memory surface
```

with `-hwaccel qsv -hwaccel_output_format qsv -c:v hevc_qsv` and **no
`hwdownload` anywhere in the filter graph**. This is the configuration that
keeps surfaces on the GPU *and* is frame-exact — which is precisely why
FFmpeg, not the rigaya reader, is the viable route to hardware decode.

---

## 9. Throughput — what `--avhw` actually buys

180-frame Sony clip, 4K60 HEVC Main10, encoded to HEVC via QSV at 1920×1080,
3 repeats (`perf_compare.ps1`). Single-process, laptop, **not a benchmark**.

| Configuration | Frames | Wall (s), 3 runs | Encoder-internal CPU | GPU |
|---|---|---|---|---|
| rigaya `--avsw` | 180 | 3.48 / 3.44 / 3.30 | `CPULoad: 40.9` | 41 % |
| rigaya `--avhw` | **177** | 3.55 / 3.04 / 3.18 | `CPU: 2.7` | **99 %** |
| FFmpeg sw → `hevc_qsv` | 180 | 3.35 / 3.25 / 2.99 | — | — |
| FFmpeg `qsv` → `hevc_qsv` | 180 | 9.45 / 2.09 / 2.13 | — | — |

Reading, with the caveat that one 3-second clip cannot support a strong claim:

* **`--avhw` is not a throughput win here.** Wall times overlap across all
  four configurations. This agrees with the earlier QSV finding
  (`qsv.md` §5.6) that on this machine QSV decode does not beat software for
  4K60 HEVC Main10.
* **`--avhw` *is* a CPU win, and a large one.** `CPU: 2.7` vs `CPULoad: 40.9`
  — ~15× less CPU — with GPU utilisation rising 41 % → 99 %. That is the
  upload stage disappearing, and it is the honest justification for hardware
  decode: **CPU headroom for concurrent encodes, not faster decode.**
* The 9.45 s first FFmpeg qsv run is a cold-start outlier and is shown rather
  than trimmed; 2.09/2.13 s is the representative value.

The value proposition is real, and it is bought with a silent 3-frame loss on
every Sony clip. That is the trade this document exists to make explicit.

---

## 10. Verdict summary

| Statement | Confidence |
|---|---|
| QSVEncC `--avhw` runs a genuine QSV hardware reader (`avqsv`) on Arc 140T | `Confirmed` (identity line + GPU 99 % + surfaces) |
| It loses exactly the 3 leading pictures on every Sony XAVC HS clip measured | `Confirmed` (per-frame SHA-256, contiguous suffix) |
| It is exact on every DJI clip measured | `Confirmed` (4/4, `leading = 0`) |
| The loss equals `pictures_before_first_keyframe` | `Confirmed` (8/8 here + 297/297 previously) |
| The QSV and NVEncC readers are the **same source file**, byte-for-byte | `Confirmed` (identical SHA-256) |
| H.264 High 4:2:2 10-bit is a **capability refusal**, not a frame drop | `Confirmed` (no reader, 0 frames, no output, rc=−31, pre-flight table) |
| The refusal cannot be bypassed in 8.26 (`--skip-hwdec-check` is inert) | `Confirmed` |
| Several plausible causes are **not** the trigger | `Confirmed` (keyframe gate, trim, VPP, read-ahead, decoder, content) |
| FFmpeg QSV over the same silicon is frame-exact | `Confirmed` (13/13 clips) |
| `--avhw` preserves the decode → encode GPU pipeline | `Confirmed` (allocation chain + FFmpeg's own statement) |
| `--avhw` reduces CPU ~15× without improving wall time | `Confirmed` (single clip; indicative) |
| **The exact code statement that removes the frames** | **`Confirmed`** — `PipelineTaskMFXDecode::sendBitstream()`, `qsv_pipeline_ctrl.h:1136-1138`; see [`qsvencc-root-cause.md`](qsvencc-root-cause.md) |
| Whether software fallback (`avqsv → avsw`) ever fires on this corpus | `Unconfirmed` — not observed; code path requires header-extraction failure |
| Whether the H.264 4:2:2 limit is silicon or the VPL/MSDK stack | `Unconfirmed` — not separable with this evidence |
| Minimal patch feasibility | **`Runtime Proven`** — 2-edit PoC built and A/B measured: `--avhw` `n-3 → n`, output byte-identical to `--avsw`, seek/trim/refusal/controls unchanged |

### The distinction, restated

```
capability refusal   ->  0 frames,  no output file,  rc != 0,  no reader
reader truncation    ->  n-3 frames, output exists,  rc == 0,  silently wrong
software fallback    ->  n frames via avsw after --avhw was asked for
true hardware decode ->  n frames via avqsv
```

**`unsupported` is not `frame drop`.** One is a capability fact that fails
safely and early; the other is a silent integrity defect that fails while
reporting success. On this machine the two coexist in the same tool, in the
same corpus, on adjacent files — which is exactly why they must never be
counted together.

---

## 11. Reproduction

Worktree: `F:\1KT-qsv` · branch `research/rigaya-qsvencc-avhw` · base `15cf218`

| Artifact | Purpose |
|---|---|
| `work/qsvavhw/mp4struct.py` | container oracle: leading pictures, edit list, prediction |
| `work/qsvavhw/y4mhash2.js` | Y4M frame layout + per-frame SHA-256 (geometry derived, not assumed) |
| `work/qsvavhw/matrix.ps1` | the 5-measurement behaviour matrix, 13 clips |
| `work/qsvavhw/frame_experiment2.ps1` | 4-way frame attribution (FFmpeg sw / avsw / avhw / FFmpeg QSV) |
| `work/qsvavhw/perf_compare.ps1` | decode→encode throughput and CPU |
| `work/qsvavhw/compare_readers.ps1` | NVEncC vs QSVEncC reader source identity |
| `work/qsvavhw/packets.py` | decode-order packet flags (keyframe gate test) |
| `work/qsvavhw/matrix/full.matrix.json` | machine-readable matrix results |
| `work/qsvavhw/frames/sony_C1170.result.json` | frame-hash evidence |
| `work/qsvavhw/qsv_check_features.txt` | the decode capability matrix |
| `work/qsvavhw/clipset.txt` | frozen clip list |
| `third_party/QSVEnc` | rigaya/QSVEnc pinned at `b14c965` (= 8.26) |

Rebuild the matrix:

```powershell
cd F:\1KT-qsv\work\qsvavhw
$clips = Get-Content clipset.txt | Where-Object { $_ -ne "" }
.\matrix.ps1 -Tag full -Clips $clips
```

### Measurement traps encountered (kept deliberately)

Every one of these produced a wrong number before being caught, and each is
recorded so the next run does not repeat it:

1. `--log-level error` suppresses `Input Info`, hiding the reader identity.
2. `-c raw` emits **Y4M**, not rawvideo; treating it as raw mislocates every
   frame boundary.
3. `--output-res 256x256` **preserves aspect ratio** → 256×160, and the actual
   chroma was 4:2:2 despite `--output-csp yuv420`.
4. Under PowerShell, `$Input` is an automatic variable: a bound value is
   silently swallowed and filenames become empty.
5. A bare `-` as an FFmpeg output target does not reach FFmpeg; `-f null NUL`
   is required, otherwise FFmpeg aborts instantly and reports no count.
6. `-vf` placed before `-i` → FFmpeg `EINVAL`, no frames, misleading message.
7. DJI clips carry an attached MJPEG thumbnail stream that also attracts
   `-hwaccel`; `-map 0:v:0` with an explicit decoder is required.
8. `dxvadec` in an allocation line does **not** mean the GPU decoded.

---

## 12. Relationship to the existing investigation

This document **confirms and extends** `docs/hardware-decode/` with source-level
and per-frame evidence; it does not overturn any of it.

| Prior finding | This investigation |
|---|---|
| QSVEncC `--avhw` loses 3 frames on 146/146 Sony files (`root-cause.md` RC-2/RC-3) | **Confirmed** at frame-hash level; the loss is a contiguous head truncation |
| The defect is in the reader layer, not the silicon (RC-5) | **Confirmed** by A/B against FFmpeg QSV on the same silicon |
| The edit list is not the trigger (RC-4) | Consistent — `--trim` and edit-list variants change nothing; the trigger tracks stream structure |
| H.264 High 4:2:2 10-bit is a loud capability limit (`qsv.md` §5.4) | **Confirmed and sharpened**: it is a pre-flight table lookup that never constructs a reader, produces zero frames, and writes no file |
| The exact rigaya line was `Unconfirmed` | **Now `Confirmed`**: it is in the vendor pipeline task, not the shared reader — `PipelineTaskMFXDecode::sendBitstream()`, `qsv_pipeline_ctrl.h:1136-1138` |
| **Shared reader logic with NVEncC** | **New and `Confirmed`**: one byte-identical source file, so the two encoders cannot diverge on this bug |

---

## 13. Recommendation

1. **Do not ship *stock* `--avhw` for Sony XAVC.** It exits 0 while silently
   dropping the first 3 pictures of every clip — the worst failure mode in the
   set. The **patched** path does not do this on the measured corpus
   ([`qsvencc-patch.md`](qsvencc-patch.md)), but it is a maintained fork and
   remains an integration candidate, not a default.
2. **Do route around the H.264 4:2:2 10-bit capability gap at plan time.**
   The refusal is safe, but it is a capability fact available before the run
   (the decode matrix in §5.3 is queryable); discovering it as a run-time
   error is unnecessary. On this machine, H.264 4:2:2 10-bit has **no QSV
   decoder** but NVDEC handles it.
3. **If hardware decode is wanted as an independent path, use FFmpeg
   `-hwaccel`** — frame-exact on every clip measured here, with
   `-hwaccel_output_format qsv` preserving the GPU pipeline. Use it as the
   **reference/fallback**, not as an FFmpeg-decodes-to-CPU-then-pipe primary
   architecture: the raw-pipe round trip gives back most of the CPU saving
   (`e2e-benchmark.md` §6).
4. **If `--avhw` is revisited, fix it once per tool** and verify with per-frame
   identity against software decode, with `pictures_before_first_keyframe` as
   the oracle. A count check alone would have passed a truncated stream. (The
   two rigaya tools do **not** share the defect site — only the shared *rule*
   and the byte-identical reader.)
5. **Treat the GPU-pipeline property as the real prize, not speed.** `--avhw`
   cut CPU from 40.9 % to 2.7 % without improving wall time; the honest
   benefit is headroom for concurrent encodes. The end-to-end benchmark
   confirms this at production scale (2.5–2.7× less CPU per frame, no speed
   gain).
