# 3. Test Corpus

> Source of truth: `docs/hardware-decode/test-results/corpus.json`
> Raw evidence: `work/hwdecode/raw/corpus-raw.json`,
> `work/hwdecode/raw/container-facts.json`,
> `work/hwdecode/raw/leading-picture.json`
> Tooling: `work/hwdecode/scan_corpus.py`, `refacts.py`, `leading.py`

## 3.1 How a file was classified as Sony

Filenames were **never** used. Each candidate was decoded as a container
and classified from three independent, machine-readable signals:

| Signal | Source | Strength |
|---|---|---|
| `major_brand` / `compatible_brands` contains `XAVC` | `ffprobe -show_format` | definitive |
| a data track with sample entry `rtmd` is present | `ffprobe -show_streams` | definitive |
| NonRealTimeMeta sidecar declares `manufacturer="Sony"` | `<base>M01.XML` | corroborating |

`major_brand=XAVC` was present on **all 151** included files, and an
`rtmd` track on **all 151** as well — the two signals never disagreed.

## 3.2 Scan scope

```
Scan roots      F:\1KeyTranscoder\testsets   (recursive)
Suffixes        .mp4 .mov .m4v .mkv .mxf .mts .m2ts .avi .webm
Candidates      181 video files
Sony            151   (83.4%)   -> the corpus under investigation
Non-Sony         30   (16.6%)   -> excluded, listed in corpus.json
```

An environment-wide search for further Sony material found a second
tree, `F:\1KT-clean\` (1,411 GB, a packaged v0.6.1 production run). It
contains **no additional Sony source footage** — its `run/inputs` and
`run/in/*` clips (`20260903_C1154.MP4`, `20260904_C1197.MP4`) are
byte-size identical copies of files already in `testsets/`, and
everything else is generated output. It is used in
[`investigation.md`](investigation.md) §10 as production evidence, not
as corpus.

## 3.3 Corpus composition

The corpus is **two homogeneous recording modes**. That is a finding in
itself: there is no All-I, no 8-bit, and no 1080p material to test.

| | Group A | Group B |
|---|---|---|
| Files | **146** | **5** |
| Codec | HEVC | H.264/AVC |
| Profile | Main 10 | High 4:2:2 |
| Level | 153 (5.1) | 51 (5.1) |
| Pixel format | `yuv420p10le` | `yuv422p10le` |
| Bit depth | 10 | 10 |
| Chroma | 4:2:0 | 4:2:2 |
| Resolution | 3840×2160 | 3840×2160 |
| Frame rate | 60000/1001 (59.94p) | 30000/1001 (29.97p) |
| `has_b_frames` | 2 | 1 |
| Max GOP | 60 frames (1.0 s) | 30 frames (1.0 s) |
| LongGOP / All-I | **LongGOP** | **LongGOP** |
| Sample entry | `hvc1` | `avc1` |
| Media timescale | 60000 | 30000 |
| Camera | ILCE-7M5 (137), unknown (9) | ILCE-7M4 / ILCE-7M5 |
| XAVC mode string | `HEVC_3840_2160_M10P@L51HT` | — |
| Typical directory | `testsets/20260903/A7M5`, `testsets/20260904` | `testsets/a7m4_4k30p_264_hi422p_xavcs` |

### Group A is XAVC HS

137 of the 146 were confirmed by their NonRealTimeMeta sidecar:

```xml
<VideoFormat>
  <VideoFrame videoCodec="HEVC_3840_2160_M10P@L51HT"
              captureFps="59.94p" formatFps="59.94p"/>
  <VideoLayout pixel="3840" numOfVerticalLine="2160" aspectRatio="16:9"/>
</VideoFormat>
<Device manufacturer="Sony" modelName="ILCE-7M5" serialNo="07538140"/>
```

`videoCodec` is Sony's own machine-readable recording-mode statement:
HEVC, 3840×2160, Main10, `@L51HT` (High Tier), 59.94p. This is the
authority used for "XAVC HS", not the directory name.

The remaining 9 Group-A files (`testsets/adjust`, `stress`, `validate`)
are the same encoding but were renamed/relabelled during earlier test
work and have no sidecar; `ffprobe` shows them as identical HEVC Main 10
`yuv420p10le` 3840×2160 60000/1001.

### Colour / dynamic range

| `CaptureGammaEquation` | Files |
|---|---|
| `rec709` | 130 |
| `s-log3-cine` | 7 |
| (no sidecar) | 14 |

All corpus files are SDR-ish 4:2:0/4:2:2 10-bit; no HLG/PQ material.

### Audio and metadata tracks

| Property | Files |
|---|---|
| 4 audio tracks (quad LPCM 24-bit) | 138 |
| 1 audio track | 13 |
| `rtmd` metadata track | 151 |
| XAVC proxy substream declared | 137 |

## 3.4 Container structure — the decisive part

Every file was parsed at the box level (`work/hwdecode/mp4struct.py`,
moov-only reads, no decode). The result is remarkably uniform:

| Structure | Value | Files |
|---|---|---|
| `stts` sample delta | `1001` ticks, constant | **151 / 151** |
| `ctts` present | yes | **151 / 151** |
| `ctts` max composition offset | 5005 (5 frames) | 146 |
| `ctts` max composition offset | 3003 (3 frames) | 5 |
| `ctts` distinct offsets | `{0,1001,2002,3003,5005}` | 146 |
| `stss` absent (All-I) | never — every file is LongGOP | 0 / 151 |
| Edit list present | yes | **151 / 151** |
| `elst.media_time` | 2002 (2 frames) | 146 |
| `elst.media_time` | 1001 (1 frame) | 5 |
| Pictures preceding the first keyframe in presentation order | **3** | 146 |
| Pictures preceding the first keyframe in presentation order | **2** | 5 |

Two structural facts matter for everything that follows:

1. **Every clip carries an edit list whose `media_time` is non-zero**
   (2 frames for XAVC HS, 1 frame for the H.264 group). This is Sony's
   "priming" handling: the presentation timeline starts *inside* the
   coded media, not at its first sample.

2. **The keyframe is not at the presentation start.** Decoding the first
   30 samples of `20260903_C1170.MP4` shows the compressed packet order
   (`ffprobe -show_packets`):

   ```
   decode  pts   dts    flags
   0       3003  -2002  K     <- the IDR is the 4th picture in PRESENTATION order
   1       1001  -1001  -
   2          0      0  -     <- three pictures precede the keyframe
   3       2002   1001  -
   4       7007   2002  -
   ...
   ```

   The first keyframe sits at composition time 5005 ticks while the
   lowest composition time in the file is 2002 — so **3 coded pictures
   are presented before the first sync sample**. This is exactly why
   FFmpeg prints, on software decode of these files:

   ```
   [in#0] st: 0 edit list: 1 Missing key frame while searching for timestamp: 1001
   [in#0] st: 0 edit list 1 Cannot find an index entry before timestamp: 1001.
   ```

   This property is present in **151 / 151** files. It is the mechanism
   behind the frame-count divergence documented in
   [`root-cause.md`](root-cause.md).

## 3.5 What the corpus does *not* cover

Documented so no conclusion is over-claimed:

| Not present | Consequence |
|---|---|
| XAVC S-I / All-I | The `stss`-absent case is untested; a decoder that only emits from a keyframe would be exact here, so All-I is expected to be safe — **unverified**. |
| 8-bit XAVC | Untested. |
| 1080p / non-4K | Untested. |
| 4:2:2 HEVC (XAVC HS 422) | Untested; only H.264 4:2:2 exists here. |
| HLG / PQ HDR | Untested. |
| Broken/truncated Sony containers | Not part of this corpus. |

## 3.6 Non-Sony material (excluded)

30 files were deliberately excluded. They are DJI Action 4 / Air 3S
clips (HEVC Main 10, 3840×2160 and 3840×2880) plus one 6000×4000 H.264
timelapse. They carry no `XAVC` brand, no `rtmd` track and no Sony
sidecar. They are retained in `corpus.json` under `excluded_non_sony`
because they are useful **negative controls**: the DJI clip used in the
root-cause experiment has no edit list and no `ctts`, and the hardware
reader is exact on it.
