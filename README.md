# 1KeyTranscoder

<p align="center">
  <img src="logos/Primary%20Logo.png" alt="1KeyTranscoder Primary Logo" width="360">
</p>

> Recursive, resumable Windows batch transcoder for camera archives, with Sony XAVC / DJI
> metadata preservation and a structured PCM audio pipeline.

[![Version](https://img.shields.io/badge/version-0.8.0-blue)](VERSION)
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](#requirements)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Release](https://img.shields.io/badge/release-v0.8.0-informational)](https://github.com/Eureka175/1KeyTranscoder/releases/tag/v0.8.0)

## Overview

1KeyTranscoder is a command-line batch transcoder for Windows, built for archiving camera
footage at scale. It walks an input tree, transcodes every clip with a hardware (NVEncC /
QSVEncC) or software (x265 / SVT-AV1) backend, and writes a mirrored output tree as
`.MP4` files. Batches are resumable: completed work is detected and skipped, failures are
recorded per file and can be re-run selectively.

Its distinguishing feature is metadata preservation. Sony XAVC clips carry per-frame
gyroscope and lens data (`rtmd`), lens profiles (`nrtm`) and vendor `uuid` boxes; DJI
action-camera clips carry `djmd` motion quaternions, `dbgi` and `tmcd`. Both are copied
through the transcode by a dedicated preservation pipeline and verified afterwards
(payload checksums, structure checks, and optional Gyroflow consumer-side quaternion
validation), instead of being silently dropped by a generic transcoder.

It also ships a multi-channel audio delay compensation pass (`--channel-sync`) for
multi-mic camera layouts, and — since v0.7.1 — an internal PCM audio pipeline
(source/stream/track/channel modelling, routing, WAV export, and N→1 mixing with gain and
clipping policy). v0.8.0 connects that pipeline to the production output: an explicit
`--audio-plan` JSON can select/reorder channels, ask for format-aware alignment, and pull
in external recordings found next to each clip. Without `--audio-plan` the default audio
path is unchanged.

Target use case: a single workstation turning multi-hour Sony / DJI shoots into archive
copies without losing the motion and lens metadata that post-processing tools
(Gyroflow, editors) need.

## Features

### Video / transcoding

- Hardware backends: NVEncC (HEVC, AV1), QSVEncC (HEVC, AV1); software backends: SVT-AV1
  and x265 (HEVC).
- With no `--encoder`, the backend is auto-selected by capability probe:
  NVENC → QSV → x265. `--no-hw-autoselect` pins x265.
- A mismatch between `--config`'s `encoder` field and `--encoder` is a hard error, not a
  silent override.
- Presets `uhq` / `hq` / `small` / `fast` / `all`, defined in per-backend JSON profile
  files; capability-based downgrade (4:2:2 → 10-bit 4:2:0 → 8-bit 4:2:0) with a loud
  WARNING, or skipping with `--no-downgrade`.

### Preservation / archival

- Sony pipeline: `rtmd` / `nrtm` / vendor `uuid` payloads carried through re-encoding
  with strength-parameterised post-checks.
- DJI pipeline: `djmd` / `dbgi` / `tmcd` copied natively, payload `sha256` recorded,
  optional Gyroflow per-frame quaternion validation.
- Other sources: classic single-pass path (video + audio only), stated explicitly in the
  log.
- `--check basic|advanced|full` post-encode verification, plus PSNR/SSIM sampling at
  `full` on short clips.

### Audio

- Default audio behaviour is unchanged from earlier releases: streams are copied through
  (`-c:a copy`; the preservation pipelines copy audio inside the container).
- `--channel-sync`: per-file GCC-PHAT delay measurement across independent mono PCM
  tracks, corrected by integer sample shifts only.
- `--channel-sync-transparent`: pre-editing mode that stream-copies video and all
  non-audio streams and only re-generates audio tracks that need correction.
- v0.7.1 adds an internal PCM pipeline: source/stream/track/channel model, channel
  selection and mapping, unified timeline with EOF/offset handling, float32 PCM reading,
  routing, WAV export, and N→1 mixing with linear gain and clipping policy. See
  [Audio Processing](#audio-processing) for scope and boundaries.
- v0.8.0 adds format-aware audio handling on top of that pipeline: PCM input is allowed
  to enter alignment by default while compressed input is passed through untouched
  (decode → PCM → align → re-encode only when alignment is asked for explicitly), output
  codec/bitrate are inherited from the source unless overridden, and external
  recordings named after the clip (`clip001.wav`, `clip001_01.aac`, …) are discovered
  and appended as tracks that follow the same mapping rules.

### Throughput / operations

- `--jobs 1|N|auto` scheduling; `--dry-run` to probe and print commands without encoding.
- `--retry-list failed_files.json` to re-run only failed items, optionally on another
  backend.
- `--experimental-multihw`: experimental NVENC + QSV dual-backend pool (video quality
  consistency across backends is explicitly not guaranteed).
- Logging to `error.log` / `warn.log` / `total.log` / `debug.log`, batch-scoped
  environment version records, and `logs/failed_files.json` failure detail files.
- Progress dashboard plus worker window for interactive runs, or `--headless` for
  unattended operation; `watchfolder.py` / `start.bat` for polling batches.

### Hardware decode (v0.7.0)

- `--hw-decode off|auto|require`, default `off` (software decode; byte-identical
  behaviour to v0.6.2).
- `auto` uses hardware readers only for runtime-proven `(backend, codec, chroma, depth)`
  combinations and warns on every downgrade; `require` errors out instead of falling
  back.
- A frame-count integrity gate plus reader-identity assertion is always active when
  hardware decode is in use; `--hw-decode-verify` adds per-frame ordered fingerprint
  comparison.
- See [Hardware Decode](#hardware-decode) — the patched research binaries are **not**
  part of the release package, so hardware decode is unavailable in a release
  installation by design.

## Current Status

Current release: **v0.8.0** (`VERSION` = `0.8.0`, tag `v0.8.0`).

| Area | Status |
|---|---|
| Core transcoding (NVENC / QSV / x265 / SVT-AV1, HEVC + AV1) | Available |
| Sony `rtmd` / `nrtm` / `uuid` preservation | Available |
| DJI `djmd` / `dbgi` / `tmcd` preservation | Available |
| Post-encode verification (`--check basic\|advanced\|full`) | Available |
| `--channel-sync` (integer-sample delay correction, P1) | Available |
| `--channel-sync-transparent` (video stream copy) | Available |
| Hardware decode (`--hw-decode`, default `off`) | Available in source installs; **not usable in the release package** (patched binaries excluded by design) |
| Audio model (source / stream / track / channel) | Available (internal layer) |
| Audio selection and channel mapping (`AudioMapSpec`, dry-run) | Available (internal layer, no CLI) |
| PCM routing (1 output channel ← 1 source channel) | Available (internal layer, no CLI) |
| WAV export (PCM16 / PCM24 / PCM32 / float32) | Available (internal API, no CLI) |
| PCM mixing (N→1, linear gain, clipping detection) | Available (internal API, no CLI) |
| Arbitrary-reference delay correction | Available as an internal API; reachable from the CLI through `--audio-plan`'s `sync.reference` |
| Audio execution-path resolution (`NONE` / `STREAM_COPY` / `PCM_ROUTE` / `PCM_MIX`) | Available (Phase 4A; internal API) |
| Selective MP4 audio retention (keep / drop / reorder original audio streams) | Available through the CLI (`--audio-plan`). Channel-filter and mixing outputs are **refused** rather than faked as stream copy |
| Audio encoding (AAC / Opus / PCM / FLAC) of routed or mixed PCM | Available through the CLI (`--audio-plan`) |
| Final output composition (video + encoded audio → MP4) | Wired into the production entry point for the classic software path (`1kt.py --encoder x265\|svtav1`, opt-in via `--audio-plan`). Hardware backends, Sony and DJI preservation **reject** an audio plan explicitly rather than ignoring it |
| Format-aware alignment (PCM default on, compressed default off, explicit compressed → decode/re-encode with warning) | Available through the CLI (`--audio-plan`: `alignment`, `sync.reference`) |
| Codec / bitrate inheritance (`manual > source > encoder default`) | Available through the CLI (`--audio-plan`: `encode`) |
| External audio discovery (`clip001.wav`, `clip001_01.aac`, …) and mapping-driven track layout | Available through the CLI (`--audio-plan`: `external`, `mapping`) |
| Automatic cross-file synchronization (content matching) | Not implemented |
| Drift correction / resampling / loudness | Not implemented |
| Audio CLI flags beyond `--audio-plan` (`--audio-tracks`, `--audio-map`, `--audio-codec`) | Not implemented |

The audio model, selection layer, timeline, retention, encoding, format policy and
external-audio handling are unit- and integration-tested. v0.8.0 wires format-aware
alignment, codec inheritance and external audio into the same production entry point,
still opt-in through the single `--audio-plan` flag (no new CLI argument was added);
the default path is not changed. The only audio surface reachable from `1kt.py` is
`production.output`, and anything below that is an explicit API call such as
`core.audio_process.run_audio_render()`,
`core.audio_retention.build_audio_retention()`,
`core.audio_encode.encode_audio_from_plan()` or
`core.output_compose.OutputComposer.compose()`.

## Requirements

- **Windows 10 / 11** (x64). Development and verification are performed on Windows;
  no other platform is tested.
- **Python 3.11+** — no `pip` dependencies are required for the core tool. `numpy`
  (and `scipy`, for `--channel-sync`) is optional: if it is missing, the features that
  need it are skipped with a WARNING and transcoding is unaffected.
- **External tools**, called as separate executables:
  - `ffmpeg` / `ffprobe` — 9.0.1 (bundled in the release package). The project requires
    its bundled build; older PATH versions lack the AV1 features used.
  - `NVEncC` 9.31 and/or `QSVEncC` 8.26 — for the hardware backends.
  - GPAC / MP4Box — container rebuilding and metadata preservation. Behaviour is bound
    to GPAC 26.02; upgrading requires a regression run.
  - Gyroflow (optional) — consumer-side quaternion validation for `--check advanced|full`.
- **GPU driver** matching the hardware backend in use (NVIDIA for NVENC, Intel for QSV).

Every tool path can be overridden: `--tool-nvencc`, `--tool-qsvencc`, `--ffmpeg`,
`--ffprobe`, `--gpac-dir`, `--gyroflow`.

> The project's own `tools/` directory (ffmpeg / NVEncC / QSVEncC / GPAC) is
> `.gitignore`d and is the single shared toolchain used by the build and test runs.
> Keep it in one place and never copy, junction, or symlink it into other working trees;
> pass explicit paths instead.

## Installation

### From the release package (self-contained)

The self-contained package bundles ffmpeg/ffprobe 9.0.1, NVEncC 9.31, QSVEncC 8.26 and
GPAC 26.02, so no separate toolchain setup is needed: unzip, install Python 3.11+ and a
GPU driver, and run `1kt.py` from the extracted directory.

The currently published package is **v0.6.1** — v0.7.0 and v0.7.1 are published as GitHub
releases without a binary asset, and their features (hardware decode, the v0.7.1 audio
pipeline) are source-only. To use v0.7.1, run from a git checkout:

- [v0.6.1 package](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.6.1/1KeyTranscoder-v0.6.1-win64-selfcontained.zip)
  ([SHA256](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.6.1/1KeyTranscoder-v0.6.1-win64-selfcontained.zip.sha256),
  [release manifest](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.6.1/release-manifest.json))
- All releases: <https://github.com/Eureka175/1KeyTranscoder/releases>

The package allowlist is defined by `release/build_release.py`: runtime code plus the
toolchain binaries, and no `docs/`, `testsets/`, `work/` or `dist/` content.

### From source

```powershell
git clone https://github.com/Eureka175/1KeyTranscoder.git
cd 1KeyTranscoder

# Place ffmpeg/ffprobe, NVEncC, QSVEncC and GPAC under tools/, or point at existing
# installations with --ffmpeg / --ffprobe / --tool-nvencc / --tool-qsvencc / --gpac-dir.

python 1kt.py --version
```

`python 1kt.py --version` prints the version from `VERSION`. No package installation step
is required; the entry points are run directly from the checkout.

## Usage

Transcode a tree with the auto-selected hardware backend and the default `hq` preset:

```powershell
python 1kt.py --input D:\footage --output D:\archive
```

Common variations:

```powershell
# Explicit backend and profile, quality aligned with the NVENC profile
python 1kt.py --input D:\footage --output D:\archive --encoder qsv --config qsv_aligned.json --preset hq

# Every preset (uhq / hq / small / fast)
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc --preset all

# Software backends
python 1kt.py --input D:\footage --output D:\archive --encoder x265 --preset hq
python 1kt.py --input D:\footage --output D:\archive --encoder svtav1 --preset hq

# Per-file multi-channel delay compensation
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc --channel-sync

# Pre-editing pass: stream-copy video, correct only the audio tracks that need it
python 1kt.py --input D:\footage --output D:\archive --channel-sync-transparent

# Unattended run: no dashboard window, everything goes to logs
python 1kt.py --input D:\footage --output D:\archive --headless
```

Backends: `x265`, `svtav1`, `nvenc`, `qsv`, `nvenc-av1`, `qsv-av1`. `--help` lists every
flag. Output files keep the source directory structure and are written with an `.MP4`
extension.

Source routing is automatic and is stated in the log for every file: Sony XAVC clips take
the Sony preservation pipeline, DJI clips take the DJI pipeline, and everything else takes
the classic single-pass path. AV1 backends preserve Sony `rtmd` / `nrtm` / `uuid` payloads
but deliberately do not write the XAVC brand (`av01` instead), because XAVC defines only
H.264 and HEVC.

Long-running batches are resumable: re-running the same command skips completed outputs.
`--dry-run` probes and prints the commands without encoding.

## Audio Processing

v0.7.1 introduces a structured PCM audio pipeline (`core/audio_*.py`). Its purpose is to
make audio sources addressable as data — which channel of which stream of which source —
so that selection, mapping, routing, mixing and export can be expressed and tested
independently of any single ffmpeg invocation. v0.8.0 connects that pipeline to the
production output so the same model decides what the delivered `.MP4` contains.

### Model

```text
Source          one physical origin (a media file or an external WAV)
  └── Stream    one audio stream inside that source
        └── Channel   one physical channel position (0-based)

Track           a logical input track = (stream, set of channels)
Output track    a logical output unit, e.g. output 0 ← camera:s2:c2
```

A channel is identified by all three coordinates — `{source_id}:s{stream}:c{channel}` —
so identically named channels from different sources never collide. A 4-channel stream
becomes one track, but its four `AudioChannel` objects remain individually selectable; four
mono streams become four tracks.

### Supported

- Audio source / stream / track / channel modelling, built from each source's existing
  ffprobe result (no extra probing).
- Selection (which source channels are kept) and channel mapping (output order), with a
  dry-run execution spec and per-output traceability.
- Audio timeline (`AudioTimeline`) as the single authority for duration, EOF and offsets,
  with deterministic silence fill for short sources and no hidden resampling.
- Float32 PCM reading via ffmpeg with an identity channel map, so channels are never
  silently reordered or downmixed.
- Routing: 1 output channel ← 1 source channel, no sample arithmetic.
- WAV export in PCM16 / PCM24 / PCM32 / float32.
- Mixing: N source channels → 1 output channel, linear gain (0 dB = 1.0), float32
  accumulation, peak and clipping statistics, and an explicit clipping policy
  (`detect` / `hard_clip` / `error`). Mixing does not normalise automatically:
  `1.0 + 1.0 = 2.0` is preserved and counted as a clipping event.
- Fixed integer sample offsets produced by `channel_sync` reports are applied along the
  unified timeline; delays are not re-estimated here.

### Input organisation covered by tests

| Layout | Representation |
|---|---|
| Single 4-channel stream | 1 source / 1 stream / 1 track / 4 channels |
| 2 × stereo | 1 source / 2 streams / 2 tracks |
| 4 × mono | 1 source / 4 streams / 4 tracks |
| External WAV (mono / stereo / 4CH) | separate source with its own timeline |
| Mixed sources | e.g. camera 4CH + recorder stereo, or camera + external WAV |

### Boundaries

- The default production audio path is unchanged. Without `--audio-plan`, audio streams
  are still copied (`-c:a copy`; container-level copy in the preservation pipelines).
- The PCM chain runs only when the plan calls for it; nothing else on the production path
  enters it.
- Selection and mapping produce a dry-run spec only — no filtergraph is generated and no
  ffmpeg command is executed by the planning layer.
- `-map`-level specification of "N source channels → 1 output channel" is still rejected
  as `audio_mix_not_supported`, because ffmpeg argv cannot express sample-level
  summation. That rejection describes the `-map` path; the PCM path implements the same
  semantics in `core/audio_mix.py`. The two coexist by design.
- Not implemented in v0.8.0: resampling, drift correction, loudness normalisation / AGC /
  limiter / EQ / noise reduction / time-stretch, automatic (content-based) cross-file
  synchronisation, codec capability / quality-preset / VBR policy frameworks, rendering
  multiple mix buses in one pass, and audio CLI flags beyond `--audio-plan`.
- The 4-channel wireless-mic layout assumes independent mono tracks; stereo and mono
  layouts are deliberately not aligned by `--channel-sync`.

Implementation details — module responsibilities, invariants, reason codes, and the
specific internal APIs that are not stable public interfaces — are documented in
[Architecture](docs/design/architecture.md) §6.1 (v0.7.1) and §6.6 (v0.8.0), and in the
[v0.7.1 release notes](docs/release_notes_v0.7.1.md) plus the
[v0.8.0 release notes](docs/release_notes_v0.8.0.md).

### Explicit audio output (`--audio-plan`)

`--audio-plan <file.json>` is opt-in and the only audio CLI surface. Without it nothing
below is reachable. An empty request (for example only an `encode` block) is treated as
"not enabled", so the default path is structurally untouched.

```json
{
  "version": 1,
  "encode":    { "format": "opus", "bitrate": "96k" },
  "channels":  { "select": ["source:s1:c0", "clip001.wav:s0:c0"] },
  "alignment": "auto",
  "sync":      { "reference": "source:s1:c0" },
  "mapping":   { "mode": "grouped", "group_size": 2 },
  "external":  {},
  "note":      "wireless mic CH1 -> Opus, external recorder appended"
}
```

| Key | Meaning | Default |
|---|---|---|
| `encode` | Output codec (`aac` / `opus` / `pcm` / `flac`) and `bitrate` (lossy only) | inherited from the source |
| `channels.select` / `exclude` / `map` | Which channels are kept and in what output order | all, input order |
| `alignment` | `auto` / `enabled` / `disabled`; `auto` enables alignment for PCM input and leaves compressed input untouched | `auto` |
| `sync.reference` | The channel alignment is measured against. **Never guessed** — without it, alignment is permitted but nothing is shifted | none |
| `mapping` | Output stream layout for external audio: `source` / `independent` / `grouped` (+`group_size`) | follow each input's own streams |
| `external` | Discover audio files named after the clip in the same directory and append them | not enabled |

Behaviour worth knowing:

- **PCM input keeps PCM output**; compressed input keeps its own codec and bitrate. A
  manual `encode` overrides that, per output stream.
- **Compressed input is never decoded just to align it.** Asking for alignment explicitly
  emits a warning and takes the decode → PCM → align → re-encode path.
- **A bitrate on a lossless output is refused**, not ignored.
- **Mapping is preserved**: alignment and re-encoding never reorder, merge or drop
  channels. Multi-channel external files are split/grouped only as the `mapping` key asks,
  and a partial group keeps its remaining channels (`3CH` + `group_size 2` → `2CH + 1CH`).
- **Once alignment actually shifts samples, every output stream is rendered on one shared
  window** — stream copying cannot express a shift, so it is dropped for that output.
- External audio must be named after the clip: `clip001.wav`, `clip001_01.aac`,
  `clip001-rec.wav` match `clip001.MP4`; `clip001abc.wav` and `random_clip001.wav` do not.
  All matches are appended in a deterministic order (exact stem, then numeric index, then
  other suffixes; numeric runs compare as numbers, so `clip001_2` precedes `clip001_10`).
- `--audio-plan` is supported on the classic software path (`--encoder x265` / `svtav1`);
  hardware backends and the Sony/DJI preservation paths reject it explicitly (exit code 2)
  instead of ignoring it, and it conflicts with `--channel-sync` (also exit code 2).

## Hardware Decode

`--hw-decode off|auto|require` was integrated in v0.7.0 and merged into `main` on
2026-09-14. The default is `off`.

| Policy | Behaviour |
|---|---|
| `off` (default) | Software decode. Hardware decoding is not attempted; behaviour matches v0.6.2 byte for byte. |
| `auto` | Use hardware readers only when the input matches the runtime-proven allowlist `(backend, codec, chroma, depth)`; otherwise software decode. Every downgrade emits a WARNING with a reason code into the run logs and report. |
| `require` | Hardware decode is mandatory. If unavailable, the file fails (`require_unmet`); it never downgrades silently. |

The allowlist is a closed set in which every entry references a measurement. Profiles
outside it resolve to `not_proven` and software decode. Currently proven: NVENC HEVC
4:2:0 10-bit, NVENC H.264 4:2:2 10-bit, QSV HEVC 4:2:0 10-bit.

When hardware decode is active, a frame-count integrity gate is always enabled: five-way
frame accounting plus a reader-identity assertion read from the tool log (never inferred
from the command line, because QSVEncC silently constructs `avsw` when hardware is
unavailable). A failing artifact is discarded, reported, and re-run with software decode.
`--hw-decode-verify` adds `sequence`-level verification by comparing ordered per-frame
fingerprints of hardware and software decode, at the cost of one extra decode pass.

Temporal windows and hardware decode are mutually exclusive: with a time seek the two
rigaya readers are not equivalent (same frame count and PTS, different pictures), so the
router refuses hardware decode with `seek_not_equivalent` and uses software decode.
`--trim` is reader-equivalent and unrestricted. These parameters are not exposed as
`1kt.py` flags; the router reacts to them when a temporal window is passed to the readers.

Two boundaries matter when reading results:

- **Hardware decode is unavailable in a release installation.** The patched binaries are a
  research build that must not be redistributed; they live under `tools/avhw/`, which the
  release allowlist excludes. In a release package `--hw-decode auto` therefore downgrades
  to software decode with `not_proven`, and `require` fails. This is designed behaviour,
  not a defect.
- **The patches are version-bound.** The NVEncC patch is verified only on 9.31
  (`2cb9d810`); the QSVEncC patch is runtime-proven on the pinned 8.26 revision only
  (8.27–8.30 untested).

The correctness matrix for this feature is `tests/hwdecode/` (83 cases, classes A–K),
with entry points in `docs/hardware-decode/README.md`. It requires a real GPU and the
patched binaries and is **not** part of `tests/full_autotest.py --level full`.

## Testing

Three levels, run from the repository root:

```powershell
python tests\full_autotest.py --level unit        # L1: pure logic and deterministic logic (about 1-1.5 min)
python tests\full_autotest.py --level toolchain   # L2: + tool versions, machine capabilities, flag allowlist (about 1 min, no encoding)
python tests\full_autotest.py --level full        # L3: + real pipeline integration and fault injection (about 10-15 min)
python tests\full_autotest.py --level all         # same as --level full
```

The frozen baseline recorded for the current release (v0.8.0) is:

```text
L1 unit           590 PASS / 0 FAIL
L3 --level full   856 PASS / 0 FAIL   (unit 590 + toolchain 16 + full 250)
```

These numbers are assertion counts from the automated regression suite at the release
freeze. They are not a new full production transcoding benchmark: the release was cut
without a fresh end-to-end encode/transcode campaign on production material.

Previous baselines, for comparison:

```text
v0.7.1 freeze                  unit 390 / full  509
after v0.7.1 tag (Phase 4A/4B/4C)  unit 511 / full  728
```

Any change must be re-checked for new failures.

### Explicit audio output (`--audio-plan`)

The default audio path is unchanged: without `--audio-plan` the tool still does
`-map 0` + `-c:a copy`. With an explicit plan (JSON) the classic software path
(x265 / SVT-AV1) encodes or retains the audio you select and composes it with the
video, which is stream-copied and therefore bit-identical to the default path.

```json
{
  "version": 1,
  "encode": { "format": "opus", "bitrate": "96k" },
  "channels": { "select": ["source:s1:c0", "source:s2:c0"] },
  "external": {}
}
```

Channel ids use the existing audio identity (`<source>:s<stream>:c<channel>`); external
files use their file name as the source id (`clip001.wav:s0:c0`). Leaving `channels` out
(and not asking for `external`, `alignment` or `mapping`) means "select everything",
which makes the plan a default plan: the execution path resolves to `NONE` and nothing
changes. Unknown keys, unknown versions and unknown formats are errors, and hardware
backends, Sony and DJI preservation reject an audio plan explicitly rather than ignoring
it. See [Explicit audio output](#explicit-audio-output---audio-plan) above for the full
key list and behaviour.

`tests/full_autotest.py` is a thin compatibility entry point: the CLI, exit code and
report format are unchanged, while the implementation lives in the `tests/selftest/`
package, split by responsibility.

```text
tests/selftest/
├── runner/          CLI (argparse), suite registry, executor
├── fixtures/        reusable material: deterministic PCM, AudioPlan builders, real media
├── assertions/      reusable audio observation primitives (impulse maps, hashes)
├── suites/          the tests themselves, one module per area
│   ├── core.py                  color / caps / hw planning / probe / classifier / GPAC
│   ├── codecs.py                x265 P0, AV1
│   ├── channel_sync.py          delay compensation (pure logic + algorithm)
│   ├── audio_model.py           audio track / channel / plan model
│   ├── audio_selection.py       sources, selection, channel mapping
│   ├── audio_timeline.py        timeline, render policy, EOF, sync offset direction
│   ├── audio_route.py           channel routing, WAV export, chunk invariance
│   ├── audio_mix.py             PCM mixing, graph equivalence
│   ├── audio_sync.py            arbitrary-reference delay correction
│   ├── audio_retention.py       Phase 4A: execution path + selective MP4 retention
│   ├── audio_encode.py          Phase 4B: PCM → encoded audio (EncodedAudioOutput)
│   ├── production_output.py     Phase 4C: --audio-plan through the real 1kt.py entry
│   ├── audio_format.py          v0.8.0: format policy, alignment, codec inheritance
│   ├── audio_external.py        v0.8.0: external discovery rules + mapping matrix
│   ├── audio_integration.py     L3 audio integration on real material
│   ├── pipeline.py              L3 full pipeline + fault injection
│   ├── hardware.py              L3 channel-sync end to end
│   ├── toolchain.py             L2 tool / capability probing
│   └── cli.py                   CLI contract
├── reporting/       result aggregation and report writing
└── (production coordination lives outside this package: `core/audio_request.py`,
    `core/audio_format.py`, `core/audio_external.py`,
    `core/audio_output_structure.py`, `production/output.py`)
```

`paths` is the only module holding mutable test state (`RESULTS` / `CURRENT_LEVEL`).
Legacy names (`full_autotest.SUITES`, `full_autotest.record`, …) still forward to the
real implementation, and no suite display name changed, so report filtering by name
keeps working.

Scheduled hardware-decode verification is separate:

```powershell
python -m tests.hwdecode.harness provenance      # binary/patch identity; mismatch = FAIL
python -m tests.hwdecode.harness check-matrix    # documentation vs matrix drift check
python -m tests.hwdecode.harness run --phase 1   # A toolchain + B routing
python -m tests.hwdecode.harness summary         # aggregate gate
```

Targeted self-checks: `python tests\run_selfcheck.py --encoder nvenc|qsv|x265` and
`python -m preservation.selfcheck <original> <final> <log_dir>`.

## Project Structure

```text
1KeyTranscoder/
├── 1kt.py                  Main CLI entry point (orchestration)
├── watchfolder.py          Polling batch entry point
├── start.bat               Double-click launcher
├── VERSION                 Single source of the version number (0.8.0)
├── *.json                  Encoder profile / scaling configurations
├── core/                   Runtime core: pipeline, probing, planning, audio, dashboard
├── encoders/               Encoder backends, capability probing, hardware decode, integrity gate
├── preservation/           Sony / DJI metadata preservation, validation, quality sampling
├── production/             Cross-domain orchestration: audio output → final container
├── release/                Release packaging and package verification tools
├── tests/                  Automated test suites (incl. tests/hwdecode/)
├── docs/                   Design, evaluation, release and reference documentation
├── logos/                  Brand assets
├── licenses/               GNU GPL v3 text (referenced by LGPL-3.0)
├── olddocs/                Archived documents and code snapshots
├── LICENSE                 GNU LGPL v3 text
└── NOTICE                  Copyright and SPDX identifier
```

| Path | Purpose |
|---|---|
| `1kt.py` | Command-line entry point; argument parsing and per-file orchestration. |
| `core/` | Backend-agnostic runtime logic: configuration, probing, source classification, scaling, batching, channel sync, the v0.7.1 audio modules and the v0.8.0 format/external-audio policy modules, logging, dashboard. |
| `encoders/` | Backend implementations (NVEncC, QSVEncC, x265, SVT-AV1), capability tables, hardware-decode routing, integrity gate. |
| `preservation/` | Sony and DJI preservation pipelines, container/ISO-BMFF handling, validation checkers, quality sampling. |
| `production/` | Cross-domain orchestration: turns a video artifact plus an audio plan into the final container. Belongs to neither the audio nor the video domain. |
| `release/` | `build_release.py` (allowlist-based packaging) and `verify_package.py`. |
| `tests/` | `full_autotest.py` (thin entry point: unit / toolchain / full), the `tests/selftest/` runner + fixtures + suites, targeted self-checks, fixtures, and the hardware-decode matrix in `tests/hwdecode/`. |
| `docs/` | Design documents, evaluation reports, release notes, third-party reference archive. |
| `logos/` | Primary logo, symbol mark and word mark. |
| `licenses/` | GNU GPL v3 text incorporated by reference by LGPL-3.0. |
| `olddocs/` | The project's single archive location for superseded documents and code snapshots. |

Local-only directories (`tools/`, `testsets/`, `work/`, `logs/`, `dist/`) are not tracked
by git; `dist/` holds release artifacts built locally.

## Documentation

- [Documentation index](docs/README.md) — classified index of everything under `docs/`.
- [Architecture](docs/design/architecture.md) — end-to-end data flow, invariants, module map. Start here to understand how the code runs.
- [v0.7.1 release notes](docs/release_notes_v0.7.1.md) — the audio model and PCM pipeline, phase by phase.
- [v0.8.0 release notes](docs/release_notes_v0.8.0.md) — arbitrary-reference delay correction, Phase 4A/4B/4C output integration, and Phase 5 format-aware alignment + external audio. Published with tag `v0.8.0`.
- [v0.6.1 release notes](docs/release_notes_v0.6.1.md) — channel-sync streaming memory fix and AV1 colour metadata fix.
- [Channel-sync P1 design](docs/design/channel_sync_p1.md) — algorithm, thresholds, degradation rules, fixture calibration.
- [Hardware decode deliverables](docs/hardware-decode/README.md) — integration test matrix, final report, patches, toolchain provenance.
- [Evaluation reports](docs/evaluation/) — per-backend production-readiness and AV1 calibration studies.
- [Third-party reference archive](docs/reference/README.md) — archived vendor documentation and upstream sources.
- [Archive status index](olddocs/README.md) — read the status banners before citing archived conclusions.

## Release

Current release: **v0.8.0**

- [GitHub release v0.8.0](https://github.com/Eureka175/1KeyTranscoder/releases/tag/v0.8.0)
  — published with a self-contained Windows package.
- [Release notes v0.8.0](docs/release_notes_v0.8.0.md) — frozen with the tag; the release
  notes file is not rewritten after publication.
- The GitHub release entries are published with GitHub's "pre-release" flag set; v0.8.0 is
  nevertheless the current release of the project, matching `VERSION` and the `v0.8.0` tag.

Older releases with downloadable self-contained packages:

| Version | Download | Notes |
|---|---|---|
| v0.8.0 | [zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.8.0/1KeyTranscoder-v0.8.0-win64-selfcontained.zip) | Format-aware alignment, codec inheritance, external audio discovery + mapping. |
| v0.6.1 | [zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.6.1/1KeyTranscoder-v0.6.1-win64-selfcontained.zip) | Channel-sync streaming memory fix; package still available. |
| v0.5.1 | [zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.5.1/1KeyTranscoder-v0.5.1-win64-selfcontained.zip) | AV1 line (software + hardware AV1). |
| v0.4.2 | [zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.4.2/1KeyTranscoder-v0.4.2-win64-selfcontained.zip) | HEVC/265 line. |

No package was published for v0.7.0 or v0.7.1; those features are available from a source
checkout of `main`.

## Roadmap

Directions currently defined by the existing design and release documentation. No dates
are assigned.

| Stage | Item |
|---|---|
| Later | Automatic cross-file synchronization — deriving delays across files rather than per file. |
| Later | Drift correction — correcting slowly varying offsets (currently detected as `non_constant` and refused) instead of constant integer shifts. |
| Later | Resampling and loudness handling — deliberately absent; sample rates must match and no dynamics processing exists. |

Selective MP4 audio retention (Phase 4A), audio encode/mux integration (Phase 4B),
production integration (Phase 4C) and format-aware alignment plus external audio
(Phase 5) are implemented and shipped in v0.8.0; see the
[v0.8.0 release notes](docs/release_notes_v0.8.0.md).

## Limitations

- **Platform**: Windows only; no other platform is tested.
- **Audio CLI**: `--audio-plan` is the only audio flag. Everything else in the audio
  pipeline is reachable only through the internal API.
- **Audio plan availability**: `--audio-plan` works on the classic software path
  (`--encoder x265` / `svtav1`). Hardware backends and the Sony/DJI preservation pipelines
  reject it with exit code 2 rather than ignoring it, and it cannot be combined with
  `--channel-sync`.
- **Alignment**: only constant integer sample offsets exist. A reference channel must be
  named explicitly — the tool never guesses one, so "alignment: auto" on PCM material only
  makes the pipeline eligible to align. Compressed input requires an explicit request, is
  decoded and re-encoded, and cannot be aligned by shifting packet timestamps.
- **Output structure**: external audio follows the `mapping` key exactly. A group that is
  not a whole input stream is rebuilt from PCM, and once alignment shifts samples every
  output stream is rendered (stream copying cannot carry a shift).
- **Offset model**: only constant integer sample offsets are supported. Drift correction,
  resampling and time-stretch do not exist; a slow drift is detected and the track is left
  untouched rather than corrected.
- **Channel sync scope**: `--channel-sync` requires at least three independent mono PCM
  tracks at 48 or 96 kHz with linear PCM sample formats; 44.1 kHz and compressed formats
  are explicitly refused, and stereo/mono layouts are not aligned by default. The only
  long-run validated configuration is 4 tracks / 48 kHz / single-threaded; `--jobs auto`,
  10-minute Sony/DJI material and `--experimental-multihw` long runs are not validated.
- **Hardware decode**: unavailable in release installations by design; the proven
  allowlist is a closed set of three combinations; `--seek` is mutually exclusive with it;
  the measured benefit is CPU headroom, not single-job speedup.
- **AV1 output**: always 4:2:0; Sony sources keep their metadata but do not get an XAVC
  brand; inputs below 1080p are not processed by default; the 4K60 UHQ preset is a
  reference setting, not a practical production setting.
- **Preservation**: non-Sony, non-DJI sources are transcoded without metadata (video and
  audio only). For DJI, the MJPEG cover image and `udta` are dropped because GPAC 26.02
  cannot address them; this is logged explicitly.
- **Playback**: Sony 4:2:2 output is HEVC Rext, which only NVIDIA 50-series GPUs can
  hardware-decode; distribute a 4:2:0 copy instead.
- **Quality alignment**: the QSV profile aligned to the NVENC profile is calibrated, not
  identical; high-motion material remains roughly 1 dB below NVENC, which is a hardware
  ceiling rather than a configuration issue.
- **Stability of internal interfaces**: several v0.7.1 audio objects are explicitly marked
  internal or reserved and are not guaranteed to keep their current semantics; the
  authoritative list is in
  [architecture.md](docs/design/architecture.md) §6.1 and
  [release_notes_v0.7.1.md](docs/release_notes_v0.7.1.md) §32.- **Encoder profile values**: the numbers in the `*.json` profiles are measured
  calibrations. Changing them requires a test-set regression run.

## Contributions

Issues and pull requests are welcome. There is no contributor guide yet.

- Keep changes reproducible: run `python tests\full_autotest.py --level unit` at minimum,
  and `--level full` before submitting anything that touches the pipeline. No pull request
  may introduce a new FAIL.
- Changes that touch preservation, container handling or encoder profiles must state the
  material and tool versions used for verification.
- Do not modify the encoder profile JSON values or the brand/logo assets without an
  explicit reason recorded in the change.
- Documentation lives in `docs/` and follows the classification described in
  [docs/README.md](docs/README.md); the root README stays user-facing.

## Brand Assets

The repository provides three brand assets under `logos/`. They are used as-is; the files
are not renamed or modified.

### Primary Logo

Symbol mark plus word mark. Used for the README header, project presentation, release
pages and documentation covers.

```markdown
<p align="center">
  <img src="logos/Primary%20Logo.png" alt="1KeyTranscoder Primary Logo" width="360">
</p>
```

### Symbol Mark

Mark only, without text. Intended for application icons, avatars, favicons and other
small-size contexts.

```markdown
<img src="logos/Symbol%20Mark.png" alt="1KeyTranscoder Symbol Mark" width="120">
```

### Word Mark

The `1KeyTranscoder` wordmark only. Intended for documentation, UI headers and
horizontally constrained layouts.

```markdown
<img src="logos/Word%20Mark.png" alt="1KeyTranscoder Word Mark" width="280">
```

No monochrome, app-icon, favicon, stacked or motion variants exist in this repository.

## License

**GNU Lesser General Public License v3.0 or later (LGPL-3.0-or-later).**

- [LICENSE](LICENSE) — GNU LGPL v3 text.
- [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt) — GNU GPL v3 text, incorporated by
  reference by LGPL-3.0.
- [NOTICE](NOTICE) — copyright and SPDX identifier.

Third-party tools (NVEncC, QSVEncC, GPAC, ffmpeg, Gyroflow) are invoked as separate
executables and are distributed under their own licences; they are not part of this
project's codebase.
