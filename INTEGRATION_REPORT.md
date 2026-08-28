# 1KeyTranscoder Integration Report — Phase 2 (GPAC-native timing)

Date: 2026-08-28. Toolchain: GPAC 26.02-rev0-g118e60a9-master
(`C:\Program Files\GPAC`), ffmpeg/ffprobe in `tools\`, Python 3.14,
Gyroflow 1.6.3 (`D:\Gyroflow-windows64\Gyroflow.exe`), x265 preset HQ
(production scaled params, not POC ultrafast).

This phase replaces the previous NHML-based reconstruction with a
fully GPAC-native one, switches the final output to `<basename>.MP4`,
fixes multi-audio-track copying, and re-runs both acceptance corpora.

## A. Final architecture

```
1keytransc.py            MAIN (single entry point): CLI, input discovery,
                         output root, resume/skip, probe/classify/scale,
                         backend selection, classic + Sony pipelines,
                         CSV/logging orchestration
core/                    models, probe (unchanged), source_classifier,
                         scaling, config, logging_utils
encoders/base.py         EncoderBackend protocol + pixel-format mapping
encoders/x265.py         PARAM_MAP, x265 serialization, FFmpeg commands
                         (full output + video-only intermediate)
preservation/models.py   PreservationBundle: metadata tracks, audio
                         tracks, uuid boxes, nrtm, movie/video timescales
preservation/sony.py     SonyPreservationBackend: bundle extract,
                         direct-copy verification, tref/cdsc, nrtm meta
preservation/gpac.py     GPAC/MP4Box container backend
                         (movie_timescale threaded through every rewrite)
preservation/isobmf.py   box walker + uuid insertion patch +
                         patch_track_durations() (Level-3 fallback,
                         NOT used by the normal pipeline)
preservation/pipeline.py run_sony_pipeline(): extract -> encode ->
                         native-copy mux -> reconstruct -> uuid ->
                         validate -> Gyroflow
preservation/validate.py ORIGINAL vs FINAL comparison incl. strict
                         timing regression guards
preservation/gyroflow.py Gyroflow headless consumer validation
```

Flow:

```
Original Sony MOV/MP4
  -> probe + scaling (core/, unchanged)
  -> preservation bundle (GPAC demux; immutable source manifest)
  -> FFmpeg + scaled x265 -> video/encoded.mov (MOV intermediate)
  -> MP4Box -timescale <src mvhd> -new final:
       encoded video + every source audio track (#trackID) + rtmd
       track (#trackID) as native ISOBMFF container copies
  -> payload verification (sha256) + tref/cdsc + nrtm meta + brands
  -> -flat + uuid byte patch (GPAC cannot write vendor uuid boxes)
  -> <basename>.MP4  ->  structural validation -> Gyroflow gate
```

## B. GPAC version

`MP4Box - GPAC version 26.02-rev0-g118e60a9-master`
(`gpac -version`: `gpac - GPAC command line filter engine - version
26.02-rev0-g118e60a9-master`).

## C. Exact GPAC commands used

```
MP4Box -timescale 60000 -new <stage>
       -add "<encoded>.mov#video"
       -add "<source>#2" -add "<source>#3" -add "<source>#4" -add "<source>#5"   # per audio track
       -add "<source>#6"                                                        # rtmd track
MP4Box -timescale 60000 -ref 6:cdsc:1 <stage>
MP4Box -timescale 60000 -set-meta nrtm <stage>
MP4Box -timescale 60000 -add-item "<lens>.bin:name=Lens profile:mime=..." <stage>
MP4Box -timescale 60000 -set-xml <nrtm.xml> <stage>
MP4Box -timescale 60000 -flat <stage>
MP4Box -timescale 60000 -brand XAVC:<minor> -ab <compat...> <stage>
```

(A7M4: `-timescale 90000`, video track #1, audio #2, rtmd #3.)
Inspection: `-diso -std`, `-raw <id>`, `-nhml <id>`, `-dump-xml`,
`-dump-item`. Verified against `MP4Box -h import` / `-h general` of the
installed version (not assumed from another version).

## D. Native GPAC timing solution

The mandated investigation matrix (script
`work/timing/timing_matrix.py`, evidence `metadata_forensics/
timing_investigation.md`, `work/timing/timing_matrix.tsv`) tested, on
the real A7M5 source (mvhd 60000/360360, stts 360×1001, elst
media_time 2002):

| mechanism | result |
|---|---|
| `-timescale 60000` (every mutating command) | required for A7M4 (movie 90000 ≠ video 30000); harmless when equal |
| `-add <src>#<trackID>` native track copy (rtmd + audio) | **exact timing through the entire chain** (E16/E17/E18: mvhd/tkhd 360360, stts [[360,1001]], elst preserved at every step incl. `-ref`/`-set-meta`/`-add-item`/`-set-xml`/`-flat`/`-brand`) |
| `:moovts=60000` on `-add` | did NOT prevent the truncation below (E5) |
| `:timescale=60000` on `-add` | did NOT prevent it (E6) |
| `-add rtmd.nhml:lastsampdur=1001` | corrupts NHML timing (interpreted @1000: stts got 1×60060) — raw-media option, not for NHML (E7) |
| NHML last-sample `duration="1001"` attribute | fixes the rtmd track natively (E8) — moot, NHML is no longer the reconstruction vehicle |

**Bisection result**: extraction and the encoded MOV intermediate are
exact; the FIRST timing error entered only at the NHML import step:
with any NHML-imported track present, GPAC 26.02 recomputes every
track's tkhd/elst/mvhd duration at 600-tick precision
(360360 @60000 → 360300) no matter which of the native timing options
above is used (E3–E6, E12–E13). Native ISOBMFF track copy
(`-add src#<trackID>`) does not go through that path and keeps source
timing exact (E16–E18).

**Adopted solution (Level 1)**: the rtmd and every audio track are
container-copied from the source in the single `-new` command; the
source mvhd timescale is threaded through every subsequent mutating
command. No NHML import, no duration patch. `:sampdur`/`:lastsampdur`
are not used (raw-media options, actively harmful for NHML).

## E. Is custom ISO-BMFF patching still required?

Yes, but narrower than before:

1. **uuid boxes** — required. `mp4box -hx uuid` and `gpac -hx uuid`
   both return nothing: GPAC 26.02 has no mechanism to create vendor
   `uuid` boxes (PROF/USMT/BE7ACFCB...). Verbatim extracted bytes are
   re-inserted at their structural context by
   `isobmf.insert_uuid_boxes()` (moov/trak-tail/EOF insertions only;
   faststart layouts rejected; dedup against uuid boxes GPAC already
   carries over during track import — the runs skipped 5 and 2 already-
   carried boxes respectively).
2. **tkhd/elst duration repair** — NO LONGER REQUIRED on this GPAC
   version. `isobmf.patch_track_durations()` remains available as a
   Level-3 fallback but is not called by the normal pipeline; the
   validate.compare() timing guards would detect any regression if a
   future GPAC version re-introduces the truncation.

## F. A7M5 (`testsets/a7m5_4k60p_265_10bit420_150m_xavchs_4ch/20260823_C0886.MP4`)

4K60, HEVC Main10 4:2:0, 360 frames, 4× mono 24-bit BE PCM, rtmd.

| check | result |
|---|---|
| video | hevc, **yuv420p10le**, 3840×2160, 360 frames, 60000/1001, timescale 60000, tkhd 360360, stts [[360,1001]], elst durations match |
| audio | **4 × pcm_s24be mono (ipcm), 48 kHz, 24-bit, BE** — 4 independent streams kept (not merged/converted), each 48000/288288/360360 |
| rtmd | 360 × 19456 B, sha256 payload match, timescale 60000, mdhd/tkhd 360360, stts [[360,1001]], elst [[360360,0]], timecode 01:43:55:40 |
| nrtm | preserved; Lens profile item 7903 B sha256 match + NonRealTimeMeta XML sha256 match |
| uuid | PROF ×1, USMT ×7 (vide + 4×soun + meta + moov), unknown BE7ACFCB ×1 at root — all verbatim, correct context |
| timing | mvhd 60000/360360 exact; all regression guards PRESERVED |
| **Gyroflow** | **PASS** — 12012 IMU samples, Sony ILCE-7M5, camera identifier/lens/frame-rate/readout/lens-profile/lens-positions all identical to the original; IMU span identical (6.0055 s both) → no IMU-vs-video-duration warning |
| deliverable | `20260823_C0886.MP4` (74 MB vs 128 MB source), decodes cleanly (video + all audio) |

1 MODIFIED item, non-critical and pre-existing: `nrtm.lens_profile.
item_type` `00000000` → `mime` (GPAC `-add-item` cannot write a null
item_type; payload bytes identical).

## G. A7M4 (`testsets/a7m4_4k30p_264_hi422p_xavcs/C9037.MP4`)

4K30, H.264 High 4:2:2 10-bit, 195 frames, 1× stereo 16-bit BE PCM,
rtmd variant (11264 B/sample, 30000 timescale, movie timescale 90000).

| check | result |
|---|---|
| video | hevc, **yuv422p10le** (4:2:2 10-bit kept), 3840×2160, 195 frames, 30000/1001, timescale 30000, tkhd 585585, stts [[195,1001]]; elst media_time 1001→2002 = encoder priming offset, compensated (presentation duration exact) |
| audio | 1 × pcm_s16be stereo (twos), 48 kHz, copy |
| rtmd | 195 × **11264 B** (size not assumed), sha256 payload match, timescale 30000, mdhd 195195, tkhd 585585, elst [[585585,0]], timecode 04:41:10:28 |
| nrtm | XML preserved; **no Lens Profile item — nothing fabricated** (name/size 0 both sides) |
| uuid | PROF ×1, USMT ×4, unknown BE7ACFCB ×1 at root — verbatim |
| timing | mvhd 90000/585585 exact (movie ≠ video timescale handled correctly) |
| **Gyroflow** | **PASS** — 13013 IMU samples, Sony ILCE-7M4, identifier and timeline match |
| deliverable | `C9037.MP4` (35.9 MB), decodes cleanly |

Summary: **PRESERVED=41, MODIFIED=0, MISSING=0, UNKNOWN=0**.

## H. Final filename examples

`20260823_C0886.MP4` → `20260823_C0886.MP4` (unchanged basename,
uppercase extension, no `_comp`/`_encoded`/`_transcoded` suffix).
`C9037.MP4` → `C9037.MP4`. Output root is separate from the input
root; relative directory structure is preserved; sources can never be
overwritten (the output-inside-input check rejects such layouts).
Internal brand: source XAVC brand + compatible brands (chosen for Sony
metadata / Premiere / Gyroflow compatibility; the uppercase `.MP4`
name is an external convention, not a brand statement).

## I. Remaining limitations

1. **Catalyst Browse/Prepare untested** — Gyroflow (same rtmd/nrtm
   telemetry-parser family) is the validated consumer. Residual deltas
   vs the camera original: video `mdhd` carries the encoder-priming
   offset (compensated by elst; presentation duration exact);
   `nrtm.lens_profile.item_type` is `mime` instead of `00000000`
   (GPAC `-add-item` limitation).
2. **MediaInfo CLI not installed** (GUI build only) — the automated
   gate is ffprobe + MP4Box(-diso/-raw/-dump-xml/-dump-item) +
   Gyroflow headless export.
3. **Gyroflow gate is auto-detected** (`D:\Gyroflow-windows64`,
   `C:\Program Files\Gyroflow`, or `--gyroflow`); when absent the
   consumer gate is skipped (logged) but the structural gate still
   applies.
4. **DJI and other cameras** keep the classic direct-encode path
   (known pre-existing loss of djmd/dbgi/tmcd/udta metadata — out of
   scope, no DJI backend added this phase).
5. **Audio policy**: stream copy only (per-track container copy). If a
   target container cannot carry a source audio format, the run fails
   explicitly instead of re-encoding.
6. GPAC `_opt` path quirk: paths embedded in MP4Box option strings are
   rewritten relative to the process cwd; input/output on a different
   drive than the cwd raises ValueError (same-drive layouts
   unaffected).

## Regression guards added this phase (validate.compare)

`timeline.movie_timescale`, `timeline.movie_duration`,
`video.timescale`, `video.track_duration`, `video.stts`, `video.elst`
(segment durations critical; encoder-priming media_time shift
documented), `audio.streams` (codec/rate/depth/channels per stream),
`audio.tracks` (per-track entry/timescale/durations/sample count),
`rtmd.track_duration`, `rtmd.elst` — any MODIFIED/MISSING on these
fails the run. `rtmd.timescale`/`stts`/`sample_count`/`payload_sha256`
and the audio track count were already critical. A future one-tick
drift, truncated final sample, or changed track duration will fail
validation instead of being silently tolerated.

## Resume-safety fixes this phase

- A reusable `encoded.mov` must pass an ffprobe validation (≥1 video
  packet); partial files from interrupted encodes are re-encoded, not
  trusted (verified live: an interrupted A7M4 run resumed correctly).
- A cached `report.json` from a FAILED run no longer blocks a retry
  (the final output is rebuilt).
- Old POC manifests (no `audio_tracks`) trigger re-extraction, because
  `#audio` copies only the FIRST audio track — multi-track audio
  requires the per-track copy list.
