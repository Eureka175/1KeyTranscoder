# 6. NVDEC Results

> Deliverable: `docs/hardware-decode/test-results/nvdec-results.json`
> Raw fingerprints: `work/hwdecode/raw/fp/<slug>/nvdec_*.json`
> Modes: `work/hwdecode/fingerprint.py` → `MODES["nvdec_*"]`
> Device: NVIDIA GeForce RTX 5070 Laptop, driver 616.56, compute 12.0

## 6.1 How NVDEC was driven

| Mode | Command | Purpose |
|---|---|---|
| **`nvdec_cuvid`** | `-hwaccel cuda -c:v <codec>_cuvid` (system-memory output) | **primary mode** |
| `nvdec_auto` | `-hwaccel cuda` (decoder auto-selected) | checks auto-selection |
| `nvdec_cuvid_hw` | `-hwaccel cuda -hwaccel_output_format cuda -c:v <codec>_cuvid` + `hwdownload,format=p010le` | explicit hardware surfaces |

## 6.2 Decoder identity — "native" does not mean software

`-hwaccel cuda` prints:

```
Stream #0:0 -> #0:0 (hevc (native) -> rawvideo (native))
```

which **looks like software decode**. It is not. At `-loglevel debug`:

```
[vist#0:0/hevc @ ...] Selecting decoder 'hevc' because of requested hwaccel method cuda
[hevc @ ...] Format cuda chosen by get_format().
[hevc @ ...] Format cuda requires hwaccel hevc_nvdec initialisation.
[hevc @ ...] Loaded lib: nvcuvid.dll
[hevc @ ...] Loaded sym: cuvidCreateDecoder
[hevc @ ...] Loaded sym: cuvidDecodePictureAsync
...
```

FFmpeg's generic `-hwaccel` mechanism attaches a CUDA device context to
the *native* decoder wrapper, which then delegates slice decoding to
NVDEC. `native` names the parser/wrapper, not the compute path.

**This is a methodological trap worth recording**: an investigation that
trusted the stream-mapping string would have concluded "hardware decode
never engaged" and reached the opposite root cause. All NVDEC results in
this report are backed by the `hwaccel ... initialisation` /
`Loaded lib: nvcuvid.dll` evidence, captured automatically by
`work/hwdecode/verify_decoders.py`.

## 6.3 Corpus results

| Classification | Files |
|---|---|
| **PASS** | **151** |
| DECODER_ERROR | 0 |
| FRAME_DROP | 0 |
| FRAME_DUPLICATE | 0 |
| FRAME_REORDER | 0 |
| PTS_ERROR | 0 |
| FLUSH_LOSS | 0 |
| REORDER_DELAY_ERROR | 0 |
| DEMUX_MISMATCH | 0 |
| PROJECT_PIPELINE_ERROR | 0 |
| UNKNOWN | 0 |

`nvdec_cuvid`: **151 / 151 PASS**, zero failures of any class.
`nvdec_auto`: identical results on every file measured.

Again, PASS means the decoded picture sequence, the PTS sequence and the
keyframe/picture-type sequence were all **identical to the software
ground truth**, not merely equal in length.

Notably NVDEC succeeds on the **H.264 High 4:2:2 10-bit** group
(5/5) where QSV has no decoder at all — so on this machine the two
hardware decoders do **not** cover the same formats.

## 6.4 Correlation with media parameters

| Parameter | Values present | NVDEC outcome |
|---|---|---|
| Codec | HEVC / H.264 | PASS 151/151 |
| Chroma | 4:2:0 / 4:2:2 | PASS 151/151 |
| Bit depth | 10 (both groups) | PASS |
| Profile | Main 10 / High 4:2:2 | PASS |
| GOP | LongGOP | no effect |
| Edit list | `media_time` 2002 / 1001 | no effect — leading pictures preserved |
| `ctts` reorder depth | 5 / 3 | no effect — drain complete |
| Clip length | 30 … 10 170 frames | no effect |
| Resolution | 3840×2160 | no effect |

**No media parameter correlates with any NVDEC failure, because there
were no NVDEC failures.** The decoder handles the exact structure that
breaks the rigaya reader: Sony's leading pictures that precede the first
keyframe.

## 6.5 The one anomaly, and why it is not a finding

During the corpus run one file (`20260903_C1084.MP4`, `nvdec_cuvid`)
recorded 0 frames. Investigation showed the cause was in the
investigation tooling, not the decoder: the run raised
`exception: 'pts_time'` from an over-strict merge between the `showinfo`
line stream and the raw frame stream at the tail. It was fixed
(`fingerprint.py`, all per-frame accesses made tolerant, plus a new
`frames_without_showinfo` counter) and the mode was re-run.

It is documented deliberately: "my tool crashed" and "the decoder
failed" must never be conflated, and the file in question is now a
**PASS**.

## 6.6 Throughput

| Mode | Observations |
|---|---|
| `nvdec_cuvid` (system-memory output) | ~0.7× software fps single-process |
| `nvdec_auto` | comparable |
| `nvdec_cuvid_hw` (explicit `cuda` surfaces + `hwdownload`) | fastest configuration measured, up to ~1.2× software |

The explicit-surface variant is the only decode configuration measured
faster than the software decoder on this machine for 4K60 HEVC Main10.
See [`implementation-plan.md`](implementation-plan.md) §17.7.

## 6.7 Verdict

- NVDEC via FFmpeg is **frame-sequence correct on 100 % of the Sony
  corpus** (151/151), bit-identical to software decode, across both
  codecs, both chroma formats, both edit-list shapes and all clip lengths.
- It covers formats QSV cannot (H.264 High 4:2:2 10-bit).
- It has **no frame-integrity risk** on this corpus.
- It is the only hardware decoder here that can beat software decode, and
  only in the explicit `-hwaccel_output_format cuda` + `hwdownload`
  configuration.
- As with QSV, the frame loss reported by the project's earlier work
  belongs to the **rigaya `avcuvid` reader**, not to FFmpeg's NVDEC
  integration: rigaya `--avhw` loses exactly the leading pictures on
  151/151 files ([`root-cause.md`](root-cause.md) RC-2/RC-3), while
  FFmpeg over the same silicon loses none.
