# 5. QSV Results

> Deliverable: `docs/hardware-decode/test-results/qsv-results.json`
> Raw fingerprints: `work/hwdecode/raw/fp/<slug>/qsv_*.json`
> Modes: `work/hwdecode/fingerprint.py` → `MODES["qsv_*"]`
> Driver: Intel Arc 140T, `8086:7d51`, driver `32.0.101.8974`

## 5.1 How QSV was driven

QSV hardware decode was reached **only through FFmpeg `-hwaccel`**, i.e.
the same decoder stack a production integration would use. Four command
shapes were measured:

| Mode | Command | Purpose |
|---|---|---|
| `qsv_dec` | `-hwaccel qsv -c:v <codec>_qsv` + `hwdownload,format=p010le` | explicit QSV decoder, explicit download |
| **`qsv_native`** | `-hwaccel qsv -hwaccel_output_format qsv -c:v <codec>_qsv` + `hwdownload,format=p010le` | **primary mode** — hardware surfaces all the way |
| `qsv_auto` | `-hwaccel qsv` (decoder auto-selected) + `hwdownload` | checks auto-selection |
| `qsv_swout` | `-hwaccel qsv -c:v <codec>_qsv -hwaccel_output_format p010` | FFmpeg-side download (contrast) |

A first attempt to run `-hwaccel qsv` without `hwdownload` failed with
`Error reinitializing filters! ... -40 (Function not implemented)`:
QSV deliveries are `AV_PIX_FMT_QSV` frames and swscale cannot touch them.
This is recorded because it is a real integration trap: **QSV has no
"automatic software output" mode**, unlike CUDA. `hwdownload` is
mandatory, and it must be paired with a depth-matched software format
(`p010le` for 10-bit — `nv12` is rejected).

## 5.2 Decoder identity — did QSV actually run?

Evidence is captured per mode by `work/hwdecode/verify_decoders.py` using
`-loglevel debug` (`work/hwdecode/raw/decoder-identity.json`). For QSV the
decisive lines are the device binding:

```
[D3D11VA @ ...] Using device 8086:7d51 (Intel(R) Arc(TM) 140T GPU (16GB)).
Stream #0:0 -> #0:0 (hevc (hevc_qsv) -> rawvideo (native))
```

Verified for **every** mode used in this report:

| Mode | verdict | decoder in stream mapping |
|---|---|---|
| `sw` | SOFTWARE | `hevc (native)` |
| `sw_named` | SOFTWARE | `hevc (native)` |
| `qsv_auto` | **HARDWARE** | `hevc (hevc_qsv)` |
| `qsv_dec` | **HARDWARE** | `hevc (hevc_qsv)` |
| `qsv_native` | **HARDWARE** | `hevc (hevc_qsv)` |
| `qsv_swout` | **HARDWARE** | `hevc (hevc_qsv)` |
| `nvdec_auto` | **HARDWARE** | `hevc (native)` ← misleading label |
| `nvdec_cuvid` | **HARDWARE** | `hevc (hevc_cuvid)` |
| `nvdec_cuvid_hw` | **HARDWARE** | `hevc (hevc_cuvid)` |
| `d3d11va_auto` / `d3d11va_native` / `dxva2_auto` | **HARDWARE** | `hevc (native)` |
| `h264 qsv_native` | HARDWARE attempted, `rc=69` | `h264 (h264_qsv)` |
| `h264 nvdec_cuvid` | **HARDWARE** | `h264 (h264_cuvid)` |

No mode in this report silently decoded in software while claiming
hardware.

## 5.3 Corpus results

151 Sony files decoded with `qsv_native` (plus control modes on a
subset). Classifications use the taxonomy in
[`README.md`](README.md#classification-taxonomy).

| Classification | Files |
|---|---|
| **PASS** | **146** |
| DECODER_ERROR | 5 |
| FRAME_DROP | 0 |
| FRAME_DUPLICATE | 0 |
| FRAME_REORDER | 0 |
| PTS_ERROR | 0 |
| FLUSH_LOSS | 0 |
| REORDER_DELAY_ERROR | 0 |
| DEMUX_MISMATCH | 0 |
| PROJECT_PIPELINE_ERROR | 0 |
| UNKNOWN | 0 |

**PASS is not a count comparison.** For every one of the 146 passing
files the QSV picture sequence was **bit-identical** to the software
ground-truth sequence (`sequence_identical = true`), including the PTS
sequence and the picture-type/keyframe sequence.

Controls measured on a 5-file subset (`qsv_dec`, `qsv_auto`,
`qsv_swout`): identical PASS results, i.e. the outcome does not depend on
how the download is expressed.

## 5.4 The 5 failures are a capability limit, and they fail loudly

All 5 failures are the **H.264 High 4:2:2 10-bit** group
(`a7m4_4k30p_264_hi422p_xavcs`, `a7m5_4k30p_264_hi422p_xavcs`).

```
$ tools/ffmpeg.exe -hwaccel qsv -c:v h264_qsv -i C9037.MP4 ... -f null -
[D3D11VA @ ...] Using device 8086:7d51 (Intel(R) Arc(TM) 140T GPU (16GB)).
[h264_qsv @ ...] Error querying IO surface: unsupported (-3)
[dec:h264_qsv @ ...] Error submitting packet to decoder: Function not implemented
...
Conversion failed!     (exit code 69, zero frames produced)
```

| Property | Value |
|---|---|
| Classification | `DECODER_ERROR` |
| Subtype | capability, not frame loss |
| Exit code | 69 |
| Frames produced | 0 |
| Silent? | **No** — fails hard, immediately |
| Affected | 5 / 151 (3.3 %), exactly the H.264 4:2:2 10-bit group |

This confirms and sharpens the project's own capability note
(`hardware_backend_design.md` §5.5: "H.264 10bit 全 x（无硬编硬解）").
Because it fails loudly with zero frames, it is **benign from a data
integrity standpoint** and is handled by ordinary capability planning —
the dangerous failure mode would be a quiet short decode, which QSV did
not exhibit on this corpus.

The gap is confirmed independently by the **rigaya QSV reader**, which
refuses the same format rather than silently substituting software:

```
$ QSVEncC64.exe -i C9037.MP4 --avhw -c raw --output-res 64x64 -o NUL
avqsv: codec h264(yuv422p10le) unable to decode by qsv.
failed to initialize file reader(s).
QSVEncC.exe finished with error!          (exit code 1)
```

Two independent implementations, same conclusion: the Arc H.264 decoder
does not implement High 4:2:2 10-bit. (Note this is *not* the silent
`avqsv → avsw` substitution the project warned about in
`hardware_backend_design.md` §5.6 — for this format it errors out. The
silent substitution was not observed anywhere in this investigation, but
the design still asserts the effective reader rather than assuming it.)

## 5.5 Correlation with media parameters

| Parameter | Values present | QSV outcome |
|---|---|---|
| Codec | HEVC | PASS 146/146 |
| Codec | H.264 | DECODER_ERROR 5/5 |
| Chroma | 4:2:0 | PASS 146/146 |
| Chroma | 4:2:2 | DECODER_ERROR 5/5 |
| Bit depth | 10 | both groups are 10-bit; depth alone does not separate |
| Profile | Main 10 | PASS 146/146 |
| Profile | High 4:2:2 | DECODER_ERROR 5/5 |
| GOP | LongGOP 60 (HEVC) / 30 (H.264) | no effect |
| Edit list | `media_time` 2002 / 1001 | no effect on QSV |
| Clip length | 30 … 10 170 frames | no effect |
| Resolution | 3840×2160, uniform | cannot be correlated |
| `ctts` reorder depth | 5 (HEVC) / 3 (H.264) | no effect |

**The only separating parameter is `(codec, chroma, profile)`** — i.e.
whether the Arc hardware decoder implements the format at all. No
parameter correlates with a *partial* decode, because no partial decode
was observed: QSV is either bit-exact or refuses to start.

## 5.6 Throughput

Controlled single-process measurement (see
[`implementation-plan.md`](implementation-plan.md) §17.7 for the full
table): QSV decode with `hwdownload` runs at roughly **0.5–0.6× the
software decoder's fps** for 4K60 HEVC Main10 on this machine. The
`hwdownload` transfer dominates. This is a material finding for the
design: on this hardware QSV decode is not a throughput optimisation for
this content class.

## 5.7 Verdict

- QSV hardware decode via FFmpeg is **frame-sequence correct** for every
  Sony format it supports (146/146, bit-identical).
- It has **no silent-loss behaviour** on this corpus: zero `FRAME_DROP`,
  zero `FLUSH_LOSS`, zero `PTS_ERROR`.
- Its only failure is an **explicit capability refusal** for H.264
  High 4:2:2 10-bit, which must be handled by capability planning, not by
  a fallback after the fact.
- It is **slower than software decode** for 4K60 HEVC Main10 on this
  machine. Its value would be CPU headroom for parallel encoding, not
  decode speed.
- The QSV-related frame loss described in the project's earlier report
  belongs to the **rigaya `avqsv` reader**, not to FFmpeg's QSV
  integration. See [`root-cause.md`](root-cause.md) RC-2/RC-3: the
  rigaya reader loses exactly the leading pictures on 146/146 HEVC files.
