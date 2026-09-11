# Hardware Decode Investigation — Structured Report

> **Branch**: `research/hardware-decode-adaptation` (from `main` @ `f721b1f`)
> **Phase**: 1 — investigation, verification, root cause, design.
> **Production code status**: **unmodified.** No production decode, encode,
> mux, preservation or channel-sync path was changed.
> **Companion documents**: [`corpus.md`](corpus.md) ·
> [`ground-truth.md`](ground-truth.md) · [`qsv.md`](qsv.md) ·
> [`nvdec.md`](nvdec.md) · [`divergence.md`](divergence.md) ·
> [`root-cause.md`](root-cause.md) · [`design.md`](design.md) ·
> [`implementation-plan.md`](implementation-plan.md)

---

# 1. Executive Summary

**The problem as reported.** Sony video that is hardware-decoded through
QSV/NVDEC runs to completion and exits normally, but the resulting frame
count does not match software decode.

**What is actually happening.** Two completely different failure modes
were found, and neither is "QSV/NVDEC decodes Sony video incorrectly":

1. **FFmpeg's own QSV and NVDEC decoders are correct.** Across the whole
   Sony corpus — 151 files, 188,475 frames — every successful hardware
   decode produced a **bit-identical picture sequence, PTS sequence and
   keyframe sequence** to software decode. There were **zero** frame
   drops, duplicates, reorders, PTS errors, flush losses or drain
   failures. `qsv_native`: 146/146 pass. `nvdec_cuvid`: 151/151 pass.

2. **The frame loss lives in the rigaya reader layer**, in the
   `--avhw` reader of NVEncC (`avcuvid`) and QSVEncC (`avqsv`) — the
   hardware reader both of 1KeyTranscoder's hardware backends would use.
   It drops frames on **100 % of the Sony corpus**: exactly the 3 coded
   pictures that precede the first keyframe in presentation order for
   XAVC HS (146 files) and exactly 2 for H.264 4:2:2 (5 files).

**Which material is affected.** All of it — but only through the rigaya
reader. The mechanism is a structural property of Sony XAVC: every clip
carries an edit list whose presentation start is not a keyframe, and
codes leading pictures before its first IRAP.

**Is QSV the same as NVDEC?** No.

| | FFmpeg `-hwaccel qsv` | FFmpeg `-hwaccel cuda` | rigaya `--avhw` (either tool) |
|---|---|---|---|
| Sony XAVC HS (HEVC Main10 4:2:0) | ✅ 146/146 exact | ✅ 146/146 exact | ❌ −3 frames, 151/151 |
| Sony H.264 High 4:2:2 10-bit | ❌ no decoder (`rc=69`) | ✅ 5/5 exact | ❌ −2 frames |
| Silent frame loss? | never | never | **always** |

The two hardware decoders do **not** cover the same formats, and the
rigaya failure hits both equally — because it is a *reader* defect, not a
decoder defect. FFmpeg over the same silicon is exact, which is the
cleanest possible proof that the silicon is not at fault.

**Is it widespread?** The rigaya defect is 100 % systematic. The FFmpeg
defect count is **zero**. The user-reported symptom therefore does not
reproduce through FFmpeg hwaccel at all, and is fully explained by the
rigaya reader.

**Most credible root cause — `Confirmed`:**
`rigaya --avhw frame count = container_samples − pictures_before_first_keyframe`.
The edit list is *not* the trigger (proven: a Matroska remux with no edit
list still loses 3; an x265 re-encode with the same edit list loses 0).

**Also `Confirmed`: 1KeyTranscoder cannot currently produce this bug.**
Hardware decode is hard-coded off (`--avsw` literal at
`encoders/nvencc.py:107` / `encoders/qsvencc.py:102`), and 8/8 real
production deliverables were frame-exact against their sources.

**Recommendation.** Do **not** enable the rigaya `--avhw` reader. If
hardware decode is wanted, drive it through FFmpeg `-hwaccel`, which is
measured exact, with format-level capability routing. Fix the verification
holes regardless (they exist today in the software-only path).

**Feasibility verdict (the purpose of this phase).**

| Path | Feasible? | Evidence |
|---|---|---|
| FFmpeg **QSV** for Sony XAVC HS (HEVC Main10 4:2:0) | ✅ **YES** | 146/146 runs produced a picture sequence bit-identical to software decode, with identical PTS and keyframe sequences |
| FFmpeg **QSV** for Sony H.264 High 4:2:2 10-bit | ❌ **NO** | no Arc decoder exists; fails loudly with `rc=69` and 0 frames — never silently |
| FFmpeg **NVDEC** for the entire corpus | ✅ **YES** | 151/151 bit-identical, both codecs, both chroma formats, 30 → 11,280 frames |
| rigaya **`--avhw`** reader (NVEncC `avcuvid` / QSVEncC `avqsv`) | ❌ **NO** | deterministic frame loss on 151/151 files; the loss is predicted exactly by `container_samples − pictures_before_first_keyframe`, validated on 297/297 measured runs |
| 1KeyTranscoder **as shipped** | ✅ frame-exact (software decode) | hardware decode is unreachable; 8/8 real production deliverables `delta = 0` |
| All-I / 8-bit / 1080p / 4:2:2 HEVC Sony material | ⚠️ **UNCONFIRMED** | no such material exists in this environment |

So hardware decode **is feasible**, but only through FFmpeg and only with
per-format capability routing — a single "enable hardware decode" switch
would be wrong in both directions. Feasibility here means frame-sequence
identity across 188,475 frames, not throughput.

---

# 2. Environment

| Component | Value |
|---|---|
| OS | Windows 11, build 26200 (`Windows-10-10.0.26200-SP0`) |
| CPU | Intel Core Ultra 9 285H (6P + 10E, 16C / 16T, up to 5.09 GHz) |
| iGPU (QSV) | Intel Arc 140T GPU (16 GB), PCI `8086:7d51`, driver `32.0.101.8974` |
| dGPU (NVDEC/NVENC) | NVIDIA GeForce RTX 5070 Laptop, 8 GB, driver `616.56`, compute 12.0, PCIe 5×16 |
| CUDA runtime | CUDA 13.4 (rigaya); `cuvid` via `nvcuvid.dll` (FFmpeg) |
| FFmpeg / ffprobe | **9.0.1-full_build-www.gyan.dev**, libavcodec 63.1.101, libavutil 61.1.101 — **`F:\1KeyTranscoder\tools\ffmpeg.exe`** |
| FFmpeg build flags | `--enable-cuvid --enable-nvdec --enable-libvpl --enable-d3d11va --enable-dxva2 --enable-ffnvcodec` |
| ⚠ not used | the `PATH` build, FFmpeg **8.0.1** — explicitly excluded by instruction |
| NVEncC | 9.31 (r4047), NVENC API v13.1 |
| QSVEncC | 8.26 (r4504), Intel Media SDK API v2.16 |
| GPAC / MP4Box | 26.02-rev0-g118e60a9-master |
| Python | 3.11.9 |
| 1KeyTranscoder | branch `research/hardware-decode-adaptation`, base commit `f721b1fc442129342875b79cacf4d5aa562cdde6` (`main`) |
| Production baseline referenced | packaged v0.6.1 run at `F:\1KT-clean\` (same FFmpeg/GPAC/NVEncC versions) |

FFmpeg hwaccel methods advertised: `cuda vaapi dxva2 qsv d3d11va opencl
vulkan d3d12va amf`. Hardware decoders available for this content:
`hevc_qsv`, `h264_qsv`, `hevc_cuvid`, `h264_cuvid` (plus `*_d3d11va` via
the generic mechanism).

---

# 3. Test Corpus

151 Sony files, 188,475 container frames, 64.87 GiB, all 3840×2160,
all LongGOP, all 10-bit. Two recording modes only.

| | XAVC HS (Group A) | XAVC S H.264 (Group B) |
|---|---|---|
| Files | 146 | 5 |
| Codec / profile | HEVC Main 10 @L5.1 | H.264 High 4:2:2 @L5.1 |
| Pixel format | `yuv420p10le` | `yuv422p10le` |
| FPS | 60000/1001 | 30000/1001 |
| Max GOP | 60 | 30 |
| Camera | ILCE-7M5 (137 confirmed by sidecar) | ILCE-7M4 / A7M5 |
| `ctts` reorder depth | 5 frames | 3 frames |
| `elst.media_time` | 2002 (2 frames) | 1001 (1 frame) |
| Pictures before first keyframe | **3** | **2** |

Sony identity was established from container/brand signals and the
NonRealTimeMeta sidecar, never from filenames. 30 non-Sony files (DJI,
one H.264 timelapse) were excluded but retained as negative controls.

**Not covered by the corpus** (and therefore not concluded on): XAVC S-I /
All-I, 8-bit XAVC, 1080p, 4:2:2 HEVC, HDR. Full detail in
[`corpus.md`](corpus.md).

---

# 4. Ground Truth Method

Software decode of every file, packets fully consumed, decoder drained to
EOF:

```
ffmpeg -i SRC -map 0:v:0 -an -sn -dn -fps_mode passthrough \
       -vf scale=64:64:flags=bilinear,format=gray,showinfo \
       -f rawvideo -pix_fmt gray -s 64x64 -
```

- **Frames counted by decoding**, not by `ffprobe -count_frames`. The
  project's `count_frames` returns *packets* (`nb_read_packets`) while its
  docstring says "decoded packet count"; the distinction was kept explicit
  throughout.
- **`-fps_mode passthrough` is mandatory** — otherwise FFmpeg itself may
  insert or drop frames to reach CFR and the instrument would create the
  divergence it measures.
- **Default edit-list handling**, and a `-ignore_editlist 1` control run
  (changes only `first_pts` 0 → 2002 on `20260823_C0886.MP4`; count stays
  360) — this is what licenses the statement that the edit list affects
  timestamps, not frame integrity, in the software path.
- **Per-frame fingerprint**: canonical 64×64 gray plane, sha1 per frame
  (exact tier) plus an 8×8 block-mean signature (tolerant tier).
- Software decode emitted **188,475 frames = the container sample count
  for all 151 files**. Decode warnings: only the edit-list warning
  discussed in §8.

Detail: [`ground-truth.md`](ground-truth.md).

---

# 5. QSV Results

**151 files, `-hwaccel qsv -hwaccel_output_format qsv -c:v <codec>_qsv`
+ `hwdownload,format=p010le`.**

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

Passing means **bit-identical picture + PTS + keyframe sequences**, not
merely equal counts.

**The 5 `DECODER_ERROR`s are H.264 High 4:2:2 10-bit capability
refusals**, not losses: `[h264_qsv] Error querying IO surface: unsupported
(-3)`, exit code 69, zero frames, immediate and loud. This confirms the
project's note that Arc has no H.264 10-bit hardware decode.

**Association with media parameters.** The only separating parameter is
`(codec, profile, chroma)`. Resolution, fps, GOP length, bit depth, edit
list, `ctts` depth and clip length (30 … 10,170 frames) show **no**
association with any failure — because QSV either decodes bit-exactly or
refuses to start; no partial decode was ever observed.

Detail: [`qsv.md`](qsv.md).

---

# 6. NVDEC Results

**151 files, `-hwaccel cuda -c:v <codec>_cuvid`.**

| Classification | Files |
|---|---|
| **PASS** | **151** |
| all other classes | **0** |

Every file — both codecs, both chroma formats, both edit-list shapes,
30 to 10,170 frames — produced a sequence identical to software decode.

One apparent failure (`20260903_C1084.MP4`, 0 frames) was diagnosed as a
defect in the **investigation tooling** (an over-strict `showinfo`/stdout
merge raising `KeyError`), fixed, and re-run to PASS. It is documented
rather than quietly dropped.

**Methodological warning that mattered:** `-hwaccel cuda` prints
`Stream #0:0 -> #0:0 (hevc (native) -> rawvideo (native))`, which reads
like software decode. Debug logging proves otherwise
(`Format cuda requires hwaccel hevc_nvdec initialisation`,
`Loaded lib: nvcuvid.dll`). Decoder identity in this report is asserted
from those lines, never from the mapping string.

Detail: [`nvdec.md`](nvdec.md).

---

# 7. First-Divergence Analysis

## 7.1 Level 1 vs Level 2 (FFmpeg software vs FFmpeg QSV/NVDEC)

**No first divergence exists.** Across 151 files there is not a single
frame index where the hardware picture differs from the software picture.
`sequence_identical = true`, `missing_total = 0`, `extra_total = 0`,
`duplicated_pictures = 0`, `pts_divergence = null` for every passing run.

```
file                              software   QSV    NVDEC   first divergence
20260903_C1169.MP4                  10170   10170   10170   none
晴天室外…多场景亮暗切换.MP4           11280   11280   11280   none
20260823_C0886.MP4                    360     360     360   none
C9037.MP4 (H.264 4:2:2)               195       0*    195   none
```
\* QSV has no decoder for this format.

## 7.2 Level 2 vs Level 3 (FFmpeg NVDEC vs rigaya `avhw`)

The only divergence in the investigation:

| file | software | QSV `--avhw` | NVDEC `--avhw` | first divergence | type |
|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 27 | 27 | **index 0** | `FRAME_DROP`, head, 3 |
| `20260904_C1193.MP4` | 90 | 87 | 87 | index 0 | `FRAME_DROP`, head, 3 |
| `20260904_C1196.MP4` | 120 | 117 | 117 | index 0 | `FRAME_DROP`, head, 3 |
| `20260903_C1158.MP4` | 150 | 147 | 147 | index 0 | `FRAME_DROP`, head, 3 |
| `20260823_C0886.MP4` | 360 | 357 | 357 | index 0 | `FRAME_DROP`, head, 3 |
| `C9037.MP4` (H.264) | 195 | 193 | 193 | index 0 | `FRAME_DROP`, head, 2 |

Fingerprint evidence (`20260903_C1170.MP4`, both readers driven through
one tool and one output configuration, so the reader is the only
variable):

```
software / --avsw (30)            --avhw (27)
  0  bafd6bcfe2e40fc7               0  034377538fc88b1f   == avsw[3]
  1  9ee0c5717e649507               1  f33b1562f5727a00   == avsw[4]
  2  f847344579c2fd3f               2  506191e416ffb23e   == avsw[5]
  3  034377538fc88b1f               3  6d67686f2c4c618a
```

`in_avsw_not_avhw` = exactly the first three frames;
`in_avhw_not_avsw` = ∅; `avsw[0]` appears nowhere in `avhw`.

Therefore, explicitly:

| Question | Answer |
|---|---|
| Did a frame disappear? | Yes — 3, at the **head** |
| Is it only a PTS offset? | No — picture content differs |
| Display/decode order confusion? | No — both are presentation order |
| Delayed frames not drained? | **No** — that truncates the tail |
| Reordering or duplication? | No — strict prefix relationship |

Detail and the cross-pipeline ±1 LSB caveat:
[`divergence.md`](divergence.md).

---

# 8. Root Cause Analysis

Full reasoning, with Observation / Evidence / Hypothesis / Experiment /
Result / Conclusion per item, is in [`root-cause.md`](root-cause.md).
Summary of the chain:

| ID | Statement | Confidence |
|---|---|---|
| RC-1 | The defect is in the rigaya reader layer; FFmpeg QSV/NVDEC are exact; 1KeyTranscoder has no reachable hardware decode path | `Confirmed` |
| RC-2 | The loss is a fixed **head truncation** of the leading pictures, not a tail/flush/drain problem | `Confirmed` |
| RC-3 | Loss = `pictures_before_first_keyframe`, predicted from container boxes alone; validated on **297/297** measured runs (NVEncC `--avhw` 151/151, QSVEncC `--avhw` 146/146); corpus-wide the loss is 3 (146 HEVC files) / 2 (5 H.264 files) | `Confirmed` |
| RC-4 | The **edit list is not the trigger**; the bitstream's leading pictures are. V2 (Matroska, no edit list) still loses 3; V4 (x265 re-encode, same edit list + `ctts`) loses 0 | `Confirmed` |
| RC-5 | FFmpeg preserves those leading pictures over the same silicon, so it is a reader defect, not a decoder defect | `Confirmed` |
| RC-6 | `hevc (native)` in FFmpeg's stream mapping does **not** mean software decode | `Confirmed` |
| RC-7 | Software decode logs `edit list: N Missing key frame while searching for timestamp` on these clips; consequence unproven | observation `Confirmed`, consequence `Unconfirmed` |
| RC-8 | Rejected hypotheses (edit list, `ctts`, tail/flush, reorder, duplication, PTS rewrite, silicon, driver) | `Rejected` |

The controlled experiment that separates the candidates:

| Variant | edit list | `ctts` | `--avsw` | `--avhw` | Δ |
|---|---|---|---|---|---|
| V1 Sony original MP4 | 2002 | yes | 30 | 27 | **−3** |
| V2 remux → **Matroska** (no edit list exists) | none | none | 30 | 27 | **−3** |
| V3 remux → MP4 (stream copy) | 2002 | yes | 30 | 27 | **−3** |
| V4 **x265 re-encode** → MP4 | 2002 | yes | 30 | 30 | **0** |
| V5 synthetic `testsrc2` HEVC → MP4 | 2002 | yes | 120 | 120 | **0** |
| V6 DJI original (non-Sony) | 0 | no | 105 | 105 | **0** |

V1/V2/V3 share one bitstream and all lose 3 regardless of container.
V4 keeps the same edit list *and* `ctts` and loses nothing, because x265
emits no pictures before its IDR.

---

# 9. QSV vs NVDEC Comparison

## 9.1 Three-level comparison (as required)

Same five clips, four ways. "software" is the container sample count,
which software decode reproduces exactly on every file.

| File | container / **software** | **Level 2** FFmpeg QSV | **Level 2** FFmpeg NVDEC | **Level 3** 1KeyTranscoder *as shipped* | **Level 3** rigaya `--avhw` (the path that would be enabled) |
|---|---|---|---|---|---|
| `20260903_C1170.MP4` | 30 | 30 ✅ | 30 ✅ | 30 ✅ | **27** ❌ |
| `20260904_C1193.MP4` | 90 | 90 ✅ | 90 ✅ | — | **87** ❌ |
| `20260904_C1196.MP4` | 120 | 120 ✅ | 120 ✅ | — | **117** ❌ |
| `20260823_C0886.MP4` | 360 | 360 ✅ | 360 ✅ | 360 ✅ † | **357** ❌ |
| `C9037.MP4` (H.264 4:2:2) | 195 | 0 ❌ capability | 195 ✅ | 195 ✅ † | 193 ❌ (NVEncC) / refused (QSVEncC) |

† Level 3 "as shipped" numbers come from the project's documented frame-exact
results and from the 8 real production deliverables measured in §10
(`delta = 0` on all of them).

Reading of the required decision table:

```
software             : exact on 151/151
FFmpeg QSV           : exact on 146/146 decodable, refuses 5 (capability)
FFmpeg NVDEC         : exact on 151/151
1KeyTranscoder today : exact  (software decode, decode unreachable)
rigaya --avhw        : -3 on 151/151
```

Because `FFmpeg QSV == software` and `FFmpeg NVDEC == software`, the
problem is **not** in FFmpeg/QSV/NVDEC. Because `rigaya --avhw !=
software` while `rigaya --avsw == software`, the problem is **not** the
hardware decoder either — it is the rigaya **reader layer** that drives
it. And because the shipped project never enables that reader, the
problem is **not** in 1KeyTranscoder's current integration.

## 9.2 QSV vs NVDEC head to head

| Question | Answer |
|---|---|
| Same problem? | **No.** QSV's only failure is a capability refusal; NVDEC has no failures. |
| Same divergence? | **No divergence at all** for either, on files they can decode. |
| Same files fail? | **No.** QSV fails on 5 files NVDEC decodes fine; NVDEC fails on none. |
| Each one's own issue? | **QSV**: no H.264 High 4:2:2 10-bit decoder (`rc=69`). **NVDEC**: none found. |
| Shared issue? | Only the **rigaya reader** failure, which is shared with NVEncC and is not decoder-specific. |

Coverage (this machine):

| Format | QSV | NVDEC |
|---|---|---|
| HEVC Main10 4:2:0 10-bit | ✅ | ✅ |
| H.264 High 4:2:2 10-bit | ❌ capability | ✅ |
| HEVC 4:2:2 / H.264 4:2:2 8-bit / All-I / 8-bit XAVC | not in corpus — unverified | |

This asymmetry is directly actionable: a single "hardware decode" switch
is wrong. Format-level capability routing is required.

---

# 10. 1KeyTranscoder Integration Analysis

**Decoder entry point.** There is no decoder module. Decode is a literal
flag block on the encoder command:

- `encoders/nvencc.py:107` — `["--avsw", "--video-track", "1", "-c", codec, "--output-depth", depth, *profile_args]`
- `encoders/qsvencc.py:102` — the same shape

`encoders/caps.py:9-13` states the intent: *"Decode-side capabilities are
NOT modeled: hardware encode paths always decode with `--avsw`
(software)."* No `PARAM_MAP` entry exposes any reader flag, no CLI option
exists (`1kt.py:922-1043`), and `nvenc.json`'s `"avhw": true` is inert
(the guard list containing it is never referenced). **Hardware decode is
unreachable.**

**Packet feeding / `receive_frame` loop / flush.** There is none.
Searched for and not found: `send_packet`, `receive_frame`, `EAGAIN`,
`avcodec_flush`, PyAV, rawvideo piping, `-flush_packets`. The decoder is a
child process; drain is implicit in its exit. The only EOS signals are the
return code and the progress counters. This is acceptable for a CLI-driven
design — **provided** the chosen CLI drains correctly, which FFmpeg does
and rigaya `--avhw` does not honour for leading pictures.

**Hardware frame transfer / frame ownership.** No `hwdownload` anywhere;
no hardware frames enter the project. Pixel-format and chroma decisions
live in `encoders/base.py:25-40`, `svtav1.py:99-102` and
`hw.py::plan_initial_format` — encode-side only, no decode-side model.

**Timestamps.** No in-process PTS/DTS read or rewrite. Container-level
only: `isobmf.patch_track_durations` / `patch_movie_duration`,
`_stts_sum`. Note `_stts_sum` is **`ctts`-blind** (`isobmf` has no `ctts`
handling at all) — harmless today because `stts` is constant on this
corpus, but it is a latent timing defect adjacent to frame sequencing.

**Verification surface — this is the real integration risk.**

| Hole | Location | Consequence |
|---|---|---|
| The 1:1 gate compares **packets**, not decoded frames | `batch_hw.py:336-363` via `probe.py:316-333` | cannot see a decoder that consumed N packets and emitted N−k frames |
| The most direct signal is discarded | rigaya's `encoded N frames` parsed at `hw.py:217`, read at `1kt.py:175`, never gated | the cheapest decode-drop detector is thrown away |
| Frame count is non-fatal at the default check level | `validate.py:213-214` records it via `eq("video.frame_count", src.nb_frames, out.nb_frames)`; `eq` (`validate.py:141-145`) marks a difference **MODIFIED**; the `critical_modified` whitelist (`validate.py:452-477`) lists `video.timescale`, `video.track_duration`, `video.stts`, `video.elst`, `video.codec`, `video.color_*` — but **not `video.frame_count`** — and `structural_success` is `not critical_missing and not critical_modified` (`validate.py:482`) | a frame-count divergence yields `structural_success=True` and `exit 0` at `--check basic` (the level the real COLD runs used). Note the asymmetry: a *MISSING* `video.frame_count` would be critical (`validate.py:443-449` catches every `MISSING` item under `video.*`), but the divergence case is *MODIFIED*, which is not |
| A rejected output is not invalidated | `hw_encode_with_fallback` leaves it; `_encoded_ok` (`pipeline.py:175-196`) accepts ≥1 packet | a re-run reuses a frame-count-rejected intermediate and skips the gate |
| Quality check never fails on count | `quality.py:285-297` tolerates ±2 and >2 becomes SKIP | the only site decoding both files never asserts |
| VFR handling is asymmetric between backends | rigaya `--avsync forcecfr` vs FFmpeg `-fps_mode passthrough` | guarantees a count divergence on any source that trips `detect_vfr` |

**Production evidence (Level 3, real artifacts).** Eight deliverables
from the packaged v0.6.1 run at `F:\1KT-clean\run\out\COLD-*` were
measured against their sources by container structure:

| Source | samples | Deliverables | Result |
|---|---|---|---|
| `20260904_C1197.MP4` | 570 | COLD-03 (HEVC), 04/05/06/10 (AV1), 07 (HEVC), 08 (transparent) | **all delta = 0** |
| `20260903_C1154.MP4` | 720 | COLD-09 (HEVC) | **delta = 0** |

So the current production pipeline — which decodes in software by
construction — is **frame-exact**, and its frame-accounting holes have
not yet caused a shipping defect. They are latent.

One adjacent observation, not a frame-count defect: the AV1 deliverables
carry `elst media_time = 0` while the HEVC deliverables and the source
carry `2002`, i.e. the AV1 mux path drops the priming edit. Frame counts
are identical; presentation timing differs by ~33 ms.

---

# 11. Scope of Problem

```
RIGAYA READER (--avhw: avcuvid / avqsv)
  AFFECTED:  100% of the Sony corpus
             XAVC HS   HEVC Main10 4:2:0 10-bit, 4K59.94p, LongGOP  -> -3 frames
             XAVC S    H.264 High 4:2:2 10-bit, 4K29.97p,  LongGOP  -> -2 frames
  MECHANISM: leading pictures that precede the first keyframe are dropped
  DEPENDS ON: nothing else measured -- not length, not GOP, not ctts,
              not the edit list, not the container

FFMPEG QSV (-hwaccel qsv)
  AFFECTED:  none for frame integrity
  CAPABILITY GAP: H.264 High 4:2:2 10-bit -> rc=69, loud failure (5 files)
  UNVERIFIED: All-I, 8-bit, 1080p, 4:2:2 HEVC (no corpus material)

FFMPEG NVDEC (-hwaccel cuda)
  AFFECTED:  none -- 151/151 exact

1KEYTRANSCODER
  AFFECTED:  none today (hardware decode unreachable; 8/8 production
             deliverables frame-exact)
  LATENT:    verification holes in §10 that would let a future hardware
             path ship a wrong frame count at exit 0

NOT CONFIRMED / UNKNOWN:
  All-I / XAVC S-I, 8-bit XAVC, 1080p, 4:2:2 HEVC, HDR, other drivers,
  other FFmpeg versions, truncated or damaged Sony containers.
```

---

# 12. Recommended Fix

*(Design intent only — Phase 1 does not implement.)*

1. **Never use the rigaya `--avhw` reader for Sony XAVC.** It is
   deterministic, predictable, and wrong on 100 % of the corpus.
   `--avsw` must stay the rigaya default.
2. **If hardware decode is adopted, drive it through FFmpeg
   `-hwaccel`**, which is proven bit-exact for both QSV and NVDEC, and
   keep `--avsw` rigaya paths untouched. This changes the decoder
   provider, not the decoder silicon.
3. **Make decode a stage with a contract**, not an encoder flag: a
   `FrameSource` abstraction with `plan / command / verify`, reusing the
   existing `build_args` seam and the existing `EncoderBackend` idiom.
4. **Validate before decoding, not after.** The container predicts the
   loss exactly (`pictures_before_first_keyframe`). A hardware plan that
   cannot promise `container_samples` frames must be rejected up front.
   This is free (one `moov` parse) and it is the check that would have
   caught the whole defect.
5. **Reconcile four frame counts**, including rigaya's
   `encoded N frames`, which is currently parsed and discarded.
6. **Format-level capability routing**, not one hardware switch: QSV
   HEVC/4:2:0 only; H.264 4:2:2 to NVDEC or software.
7. **Fallback must be automatic but never silent** — reason codes into
   the log, CSV and `report.json`, and the effective reader asserted from
   the tool's own `Input Info` line.
8. **Fix the verification holes regardless of whether hardware decode
   ever ships** (§10): `count_frames` semantics, the non-fatal
   `video.frame_count`, the discarded encoder-reported count, the resume
   hole, and the `forcecfr` / `passthrough` asymmetry.
9. **Do not pad, drop, or rewrite PTS to make counts agree.** A mismatch
   fails the file.
10. **Adopt hardware decode for feasibility-and-headroom reasons, not on
    a speed claim.** Feasibility is proven: FFmpeg QSV and NVDEC are
    frame-exact on this corpus. Indicative throughput (not a benchmark,
    one long clip, thermally loaded machine) was software ≈71 fps,
    QSV ≈60 fps, NVDEC ≈93–148 fps — so NVDEC is faster and QSV is not.
    The durable reason to adopt is freeing CPU for concurrent encodes,
    decided per vendor and per format.

Architecture: [`design.md`](design.md). Phased steps, acceptance
criteria, performance budget and risks:
[`implementation-plan.md`](implementation-plan.md).
