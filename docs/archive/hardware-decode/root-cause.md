# 8. Root Cause Analysis

> Each entry follows **Observation → Evidence → Hypothesis → Experiment →
> Result → Conclusion**, and carries an explicit confidence label:
> `Confirmed` / `Likely` / `Unconfirmed` / `Rejected`.
>
> Nothing in this document is inferred from a frame *count* alone. Every
> conclusion is anchored in a per-frame fingerprint, a container-level
> measurement, or a controlled experiment.

---

## RC-1 — Where the reported divergence actually lives

**Observation.** The reported symptom is "Sony video decodes through
QSV/NVDEC, runs to completion, exits normally, but the final frame count
differs from software decode."

**Evidence.**

1. `git grep` over the whole tree (`work/hwdecode/notes/static-analysis.md`
   §1) finds **no reachable hardware decode path** in 1KeyTranscoder.
   Decode is hard-coded to software at exactly two literals:
   `encoders/nvencc.py:107` and `encoders/qsvencc.py:102`, both emitting
   `["--avsw", "--video-track", "1", ...]`. No FFmpeg call site passes
   `-hwaccel`, `*_cuvid` or `*_qsv`; `encoders/caps.py:9-13` states the
   design intent explicitly.
2. Native FFmpeg QSV and NVDEC decode of the corpus is **frame-exact**:
   see [`qsv.md`](qsv.md) and [`nvdec.md`](nvdec.md). Every Sony HEVC
   file produced a bit-identical picture sequence to software decode.
3. The **rigaya `--avhw` reader** — the hardware reader both hardware
   backends would use — loses frames on every Sony file
   (§RC-2 below).

**Conclusion — `Confirmed`.** The reported frame-count divergence does
**not** originate in FFmpeg's QSV/NVDEC decoders and **cannot** originate
in 1KeyTranscoder as currently written, because 1KeyTranscoder never
enables hardware decode. The divergence belongs to the **rigaya reader
layer** (`avcuvid` / `avqsv`), which is reachable from 1KeyTranscoder by
flipping one token, and which the project's own
`docs/design/hardware_backend_design.md` §4.3 already pre-authorises as a
future optimisation. This investigation therefore treats the rigaya
`--avhw` reader as *the* hardware-decode path under review.

---

## RC-2 — The frame loss is a fixed head truncation, not random drops

**Observation.** Hardware decode yields fewer frames, and the deficit
looked like it might be a tail/flush problem ("decoder delayed frames not
drained").

**Evidence.**

`work/hwdecode/rigaya_reader.py`, encoder removed from the loop
(`-c raw`, output to `NUL`), NVEncC 9.31 and QSVEncC 8.26:

| File | container | NVEncC `--avsw` | NVEncC `--avhw` | QSVEncC `--avsw` | QSVEncC `--avhw` |
|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 30 | **27** | 30 | **27** |
| `20260904_C1193.MP4` | 90 | 90 | **87** | 90 | **87** |
| `20260904_C1196.MP4` | 120 | 120 | **117** | 120 | **117** |
| `20260903_C1158.MP4` | 150 | 150 | **147** | 150 | **147** |
| `20260823_C0886.MP4` | 360 | 360 | **357** | 360 | **357** |

The deficit is **3 frames in every case, independent of clip length**.
A lost tail or an un-drained queue would scale with content; a constant
offset points at the head of the stream.

**Hypothesis.** The missing frames are all at the *start*, and the
hardware reader is discarding leading pictures.

**Experiment.** Per-frame fingerprints
(`work/hwdecode/rigaya_divergence.py`). Both readers were run through the
identical output configuration (`-c raw --output-depth 10 --output-csp
yuv420`), so the only variable is the reader. The raw YUV of each was fed
through the same canonical fingerprint graph.

**Result** (`20260903_C1170.MP4`, presented in reader output order):

| index | `--avsw` (30) | `--avhw` (27) |
|---|---|---|
| 0 | `bafd6bcfe2e40fc7` | `034377538fc88b1f` |
| 1 | `9ee0c5717e649507` | `f33b1562f5727a00` |
| 2 | `f847344579c2fd3f` | `506191e416ffb23e` |
| 3 | `034377538fc88b1f` | `6d67686f2c4c618a` |
| 4 | `f33b1562f5727a00` | `953f6f612387850c` |
| 5 | `506191e416ffb23e` | `0d432bf6d9e9f57a` |

- `--avhw[0] == --avsw[3]`, `--avhw[1] == --avsw[4]`, … → the hardware
  stream **starts three frames later**.
- Set arithmetic: `in_avsw_not_avhw` = exactly the first three
  fingerprints; `in_avhw_not_avsw` = **empty**.
- `avsw[0]` is **not** found anywhere later in `avhw` → the frames are
  gone, not reordered.

**Conclusion — `Confirmed`.** The divergence is a **3-frame head
truncation**: `FRAME_DROP`, localised to the first three presented
pictures. It is **not** `FLUSH_LOSS`, **not** `REORDER_DELAY_ERROR`,
**not** `FRAME_REORDER`, **not** `PTS_ERROR`, and **not** `FRAME_DUPLICATE`.

> Methodology note. Comparing the *FFmpeg* decode stream to the *rigaya*
> raw stream byte-exactly gives disjoint fingerprints — a tooling
> artefact, not a finding. rigaya round-trips `yv12(10bit) → p010 →
> yv12(10bit)` (its own `Input Info`/`Vpp Filters` lines), which shifts
> luma by ±1 code. Measured on the same file: 25/30 frames match exactly,
> **30/30 within 2 codes**, and the temporal sequence tracks 1:1. All
> cross-pipeline conclusions therefore use the ±1-tolerant comparison,
> and only same-pipeline comparisons (`avsw` vs `avhw` through the same
> output path) use byte-exact hashes.

---

## RC-3 — The exact number of lost frames is predictable from the container

**Observation.** 3 frames lost for XAVC HS. The project's own earlier
work (`docs/design/hardware_backend_design.md` §5.2) independently
recorded **2** frames lost on A7M4 H.264 4:2:2 (195 → 193). Two different
constants for two different recording modes suggests a formula.

**Hypothesis.** The reader drops exactly the pictures whose composition
time precedes the first keyframe's composition time — i.e. the
**leading pictures**. The natural predictor is:

```
pictures_before_first_keyframe
    = |{ i : PTS[i] < PTS[first sync sample] }|

with  DTS[i] = cumsum(stts)[i]      (decode time)
      PTS[i] = DTS[i] + ctts[i]     (composition time)
```

**Experiment.** `work/hwdecode/mp4struct.py::leading_picture_analysis`
computes this from container boxes alone (moov only, no decode) for every
corpus file. `work/hwdecode/leading.py` ran it over all 151.

**Result.**

| File | samples | first sync PTS | pictures before first keyframe | predicted `--avhw` | **measured NVEncC** | **measured QSVEncC** |
|---|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 5005 | 3 | 27 | 27 ✅ | 27 ✅ |
| `20260903_C1083.MP4` | 330 | 5005 | 3 | 327 | 327 ✅ | 327 ✅ |
| `20260823_C0886.MP4` | 360 | 5005 | 3 | 357 | 357 ✅ | 357 ✅ |
| `20260903_C1169.MP4` | 10170 | 5005 | 3 | 10167 | — | 10167 ✅ |
| `晴天室外…亮暗切换.MP4` | 11280 | 5005 | 3 | 11277 | — | 11277 ✅ |
| `C9037.MP4` (H.264) | 195 | 3003 | 2 | 193 | 193 ✅ | refused † |

† QSVEncC has no H.264 High 4:2:2 10-bit decoder; it refuses rather than
substituting (see below).

**Full-corpus validation** (`work/hwdecode/rigaya_reader.py`, 302 runs,
`work/hwdecode/validate_predictor.py`):

| | NVEncC `--avhw` | QSVEncC `--avhw` |
|---|---|---|
| Files measured | 151 | 146 |
| **Predictor correct** | **151 / 151 (100 %)** | **146 / 146 (100 %)** |
| Predictor wrong | 0 | 0 |
| Capability refusals | 0 | 5 (H.264 4:2:2) |
| Measured loss distribution | `{3: 146, 2: 5}` | `{3: 146}` |
| Hardware reader confirmed in `Input Info` | `avcuvid` 151/151 | `avqsv` 146/146 |

The measured loss distribution reproduces the corpus structure exactly:
146 XAVC HS files lose 3, 5 H.264 files lose 2. The `Input Info` line
confirms every one of those runs really used the hardware reader, so the
result is not contaminated by a silent software fallback.

Corpus-wide the predictor is **constant and non-zero for all 151 files**:

```
pictures_before_first_keyframe:  {3: 146, 2: 5}
predicted_rigaya_avhw_loss:      {3: 146, 2: 5}
```

**Data-quality notes.** Two runs produced no usable measurement and were
re-executed rather than reported: `20260904_C1184.MP4` (NVEncC) aborted
mid-decode with `Break in task NVDEC: unknown error`, `rc=1`, 1400 of
1407 frames, while a second GPU job was running concurrently; on a quiet
re-run it returned **1407**, the predicted value. The 5 QSV refusals are
genuine and were **not** repaired away — a re-run fails identically with
`avqsv: codec h264(yuv422p10le) unable to decode by qsv.`

**Conclusion — `Confirmed`.** Hardware `--avhw` frame loss is a
**deterministic function of container structure**, not of the decoder,
the driver, the clip length, or the content:

```
avhw_frames = container_samples - pictures_before_first_keyframe
```

Validated on **297 / 297** successfully measured runs across the whole
corpus. It affects **100 % of the Sony corpus**, because every Sony XAVC
clip puts its first keyframe after the presentation start.

---

## RC-4 — The trigger is the bitstream's leading pictures, not the edit list

**Observation.** Sony clips carry a non-zero `elst.media_time` (2 frames
for XAVC HS). The obvious suspect is edit-list handling.

This matters because it changes the fix: an edit-list problem can be
fixed by `-ignore_editlist`/`:noedit`; a leading-picture problem cannot.

**Experiment.** `work/hwdecode/experiment_container.py` builds container
variants of one Sony clip and measures the hardware reader on each.
V2 and V3 are *stream copies* (identical bitstream, different container);
V4/V5 are *fresh encodes* (different bitstream).

| # | Variant | elst `media_time` | ctts | `--avsw` | `--avhw` | Δ |
|---|---|---|---|---|---|---|
| V1 | Sony original MP4 | 2002 | yes | 30 | 27 | **−3** |
| V2 | remux → **Matroska** (no edit list at all) | *none* | *none* | 30 | 27 | **−3** |
| V3 | remux → MP4 (ffmpeg) | 2002 | yes | 30 | 27 | **−3** |
| V4 | **x265 re-encode** → MP4 | 2002 | yes | 30 | 30 | **0** |
| V5 | synthetic `testsrc2` HEVC → MP4 | 2002 | yes | 120 | 120 | **0** |
| V6 | DJI original (non-Sony, no edit list, no ctts) | 0 | no | 105 | 105 | **0** |

**Result.** The loss **follows the bitstream, not the container**:

- V2 removes the edit list entirely (Matroska has none) and still loses 3.
- V3 re-muxes to MP4, keeping a 2002 edit list, and still loses 3.
- V4 keeps **both** a 2002 edit list **and** `ctts` and loses **0**.

V4 is the controlled discriminator: it has the same edit list and the
same `ctts` offsets as V1, yet shows no loss. The only difference is that
x265's stream starts with its IDR as the **first picture in presentation
order**, so there are no leading pictures to drop
(`pictures_before_first_keyframe == 0`), whereas Sony's stream presents 3
pictures before its IDR.

**Conclusion — `Confirmed`.** The edit list is **not** the trigger. The
trigger is that Sony's XAVC bitstream has **coded pictures that precede
the first keyframe in presentation order**. The hardware reader (or the
rigaya reader around it) will only begin output at the first IRAP, so
those leading pictures are never emitted.

**Corollary — `Rejected`.** "The fix is to disable the edit list"
(`:noedit`, `-ignore_editlist 1`, `--avsync forcecfr`). This is rejected
by V2: a container with no edit list still loses exactly 3 frames. It is
also independently rejected by the project's earlier experiments
(`hardware_backend_design.md` §5.2).

---

## RC-5 — Why FFmpeg's own hwaccel does *not* lose these frames

**Observation.** FFmpeg's QSV/NVDEC decode of the same files is exact,
even though it faces the same leading pictures.

**Evidence.**

```
$ ffmpeg -hwaccel cuda -i 20260823_C0886.MP4 ... -f null -
[hevc @ ...] Selecting decoder 'hevc' because of requested hwaccel method cuda
[hevc @ ...] Format cuda chosen by get_format().
[hevc @ ...] Format cuda requires hwaccel hevc_nvdec initialisation.
[hevc @ ...] Loaded lib: nvcuvid.dll
```

FFmpeg feeds **all** packets to the decoder and drains it to EOF; the
leading pictures are decoded and emitted because the IDR they reference
is already in the decoder when they are decoded (decode order puts the
IDR first). FFmpeg applies the edit list only as a *timestamp* shift
(confirmed: `-ignore_editlist 1` on `20260823_C0886.MP4` changes only
`first_pts` 0 → 2002, frame count stays 360), so it never needs to
discard a picture.

**Conclusion — `Confirmed`.** The difference is in the **reader**, not the
decoder silicon: FFmpeg's hwaccel path preserves leading pictures, the
rigaya `avhw` path does not. This is the direct answer to "is the problem
in the decoder or in the integration layer?" — it is in the **reader /
integration layer**, and it is demonstrated by the fact that two
different code paths over the *same* hardware decoder disagree.

---

## RC-6 — The `hevc (native)` mapping line is not evidence of software decode

**Observation.** With `-hwaccel cuda` FFmpeg prints
`Stream #0:0 -> #0:0 (hevc (native) -> rawvideo (native))`, which reads
like a software decoder. An investigation that trusted this string would
have concluded "hardware decode never happened" and gone down a false
path.

**Experiment.** `-loglevel debug` on the same command.

**Result.** `Selecting decoder 'hevc' because of requested hwaccel method
cuda` → `Format cuda requires hwaccel hevc_nvdec initialisation` →
`Loaded lib: nvcuvid.dll`. In FFmpeg's generic `-hwaccel` mechanism the
native decoder wrapper is attached to a hardware device context and
delegates slice decoding to NVDEC; `native` in that line names the
*parser/wrapper*, not the compute path.

**Conclusion — `Confirmed`.** Decoder identity was therefore established
from the `hwaccel ... initialisation` / `Loaded lib:` log lines, not from
the stream-mapping string. `work/hwdecode/fingerprint.py` captures those
lines as `hwaccel_evidence` and sets `hwaccel_engaged` so no result in
this investigation rests on the ambiguous label.

---

## RC-7 — Software decode emits a genuine edit-list warning

**Observation.** Software decode of Sony clips logs a demuxer warning.

**Evidence.** On every H.264-group file and on HEVC files whose
`media_time` falls before the keyframe:

```
[in#0] st: 0 edit list: 1 Missing key frame while searching for timestamp: 1001
[in#0] st: 0 edit list 1 Cannot find an index entry before timestamp: 1001.
```

**Conclusion — `Confirmed` (as an observation), consequence `Unconfirmed`.**
The warning is a direct, independent confirmation of the RC-3 structure:
the edit list asks for a presentation start that is not a keyframe. What
is **not** established is whether this warning ever causes FFmpeg itself
to drop a picture — in this corpus software decode always returned the
full container sample count, so no evidence of that was found. It is
recorded as a risk for the design (see `design.md` §integrity), not as a
proven defect.

---

## RC-8 — Rejected and open hypotheses

| Hypothesis | Verdict | Basis |
|---|---|---|
| Edit list / priming causes the loss | **Rejected** | V2 (no edit list) still loses 3; V4 (edit list present) loses 0 |
| `ctts` / B-frame reorder causes the loss | **Rejected** | V4 has the same `ctts` offsets and loses 0; `--avhw` output is not reordered, only shortened |
| Tail/flush/EOS problem (`FLUSH_LOSS`, `REORDER_DELAY_ERROR`) | **Rejected** | Divergence is at index 0; `in_avhw_not_avsw` is empty; every earlier frame matches exactly |
| Duplication or reordering | **Rejected** | `avhw` output is a strict prefix of `avsw` after dropping 3 |
| PTS/timestamp rewrite | **Rejected** | Frames are absent, not merely restamped; counts differ, not just timestamps |
| QSV/NVDEC silicon is at fault | **Rejected** | FFmpeg over the same silicon is bit-exact; QSV and NVDEC lose the *same* 3 frames through rigaya |
| Driver version / driver bug | **Unconfirmed — no evidence found** | Both vendors' drivers (Intel 32.0.101.8974, NVIDIA 616.56) lose identically through rigaya and are exact through FFmpeg. A driver fault would not be vendor-symmetric nor reader-specific. |
| FFmpeg version specific | **Unconfirmed** | All FFmpeg results are FFmpeg **9.0.1** (`tools/ffmpeg.exe`). FFmpeg 8.0.1 (on `PATH`) was deliberately not used. |
| Frame count difference is what actually matters | **Rejected** | An equal count can still hide reorder/duplication; conversely the real defect here changes the count. Both were measured. |

## RC-9 — Confidence summary

| Statement | Confidence |
|---|---|
| FFmpeg QSV/NVDEC decode of Sony XAVC HS is picture-exact | `Confirmed` (corpus-wide, bit-identical) |
| QSV has no H.264 High 4:2:2 10-bit decode; it fails loudly (rc=69) | `Confirmed` |
| rigaya `--avhw` drops exactly the leading pictures | `Confirmed` (fingerprint-level) |
| The loss equals `pictures_before_first_keyframe` | `Confirmed` (predicted and measured) |
| The edit list is not the trigger | `Confirmed` (V2/V4) |
| The defect is in the reader/integration layer, not the decoder silicon | `Confirmed` |
| 1KeyTranscoder's production path is frame-exact today | `Confirmed` (8/8 production deliverables, container-level) |
| Whether the root cause is rigaya's reader logic or the way it drives libavcodec/cuvid | `Likely` (reader-level) / `Unconfirmed` (exact line of code — would need a debugger or a rigaya source build) |
| Behaviour on All-I / 8-bit / 1080p / 4:2:2 HEVC Sony material | `Unconfirmed` (no such material in the corpus) |
