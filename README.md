# 1KeyTranscoder

<p align="center">
  <img src="logos/Primary%20Logo.png" alt="1KeyTranscoder Primary Logo" width="360">
</p>

> Recursive, resumable Windows batch transcoder for camera archives, with Sony XAVC / DJI
> metadata preservation and a structured PCM audio pipeline.

[![Version](https://img.shields.io/badge/version-0.7.1-blue)](VERSION)
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](#requirements)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Release](https://img.shields.io/badge/release-v0.7.1-informational)](https://github.com/Eureka175/1KeyTranscoder/releases/tag/v0.7.1)

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
clipping policy). The PCM pipeline is an internal capability layer in v0.7.1: it does not
change the default audio path or add CLI flags.

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

Current release: **v0.7.1** (`VERSION` = `0.7.1`, tag `v0.7.1`).

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
| Arbitrary-reference delay correction | Implemented on `main`; **not part of the v0.7.1 release** and not documented in its release notes |
| Audio execution-path resolution (`NONE` / `STREAM_COPY` / `PCM_ROUTE` / `PCM_MIX`) | Implemented on `main` (Phase 4A; internal API) |
| Selective MP4 audio retention (keep / drop / reorder original audio streams) | Implemented on `main` (Phase 4A; internal API, no CLI). Channel-filter and mixing outputs are **refused** rather than faked as stream copy |
| Audio encoding (AAC / PCM / FLAC) of routed or mixed PCM | Implemented on `main` (Phase 4B; internal API, no CLI) |
| Final output composition (video + encoded audio → MP4) | Implemented on `main` (Phase 4B; `core/output_compose.py`, internal API). Video is always stream-copied; the composer never re-encodes it |
| Automatic cross-file synchronization | Not implemented |
| Drift correction / resampling | Not implemented |
| Audio CLI flags (`--audio-tracks`, `--audio-map`, `--audio-codec`) | Not implemented |

"Internal layer" means the code exists, is unit- and integration-tested, and is reached
only by explicit API calls such as `core.audio_process.run_audio_render()`,
`core.audio_retention.build_audio_retention()`,
`core.audio_encode.encode_audio_from_plan()` or
`core.output_compose.OutputComposer.compose()`. The default
production path is not changed and no new command-line flag is exposed.

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
independently of any single ffmpeg invocation.

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

- The default production audio path is unchanged. Audio streams are still copied
  (`-c:a copy`; container-level copy in the preservation pipelines). No new CLI flags were
  added in v0.7.1.
- The PCM chain runs only when called explicitly (`run_audio_render()`); nothing on the
  production path calls it.
- Selection and mapping produce a dry-run spec only — no filtergraph is generated and no
  ffmpeg command is executed by the planning layer.
- `-map`-level specification of "N source channels → 1 output channel" is still rejected
  as `audio_mix_not_supported`, because ffmpeg argv cannot express sample-level
  summation. That rejection describes the `-map` path; the PCM path implements the same
  semantics in `core/audio_mix.py`. The two coexist by design.
- Deferred (not available in v0.7.1): selective MP4 audio retention, audio
  encoding/muxing into MP4, resampling, drift correction, automatic cross-file
  synchronisation, loudness normalisation / AGC / limiter / EQ / noise reduction /
  time-stretch, audio CLI flags, DAW-style editing, and rendering multiple mix buses in
  one pass.
- The 4-channel wireless-mic layout assumes independent mono tracks; stereo and mono
  layouts are deliberately not aligned by `--channel-sync`.

Implementation details — module responsibilities, invariants, reason codes, and the
specific internal APIs that are not stable public interfaces — are documented in
[Architecture](docs/design/architecture.md) §6.1 and in the
[v0.7.1 release notes](docs/release_notes_v0.7.1.md).

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

The frozen baseline recorded for the current release (v0.7.1, after the RC fixes) is:

```text
L1 unit           390 PASS / 0 FAIL
L3 --level full   509 PASS / 0 FAIL   (unit 390 + toolchain 16 + full 103)
```

These numbers are assertion counts from the automated regression suite at the release
freeze. They are not a new full production transcoding benchmark: the release was cut
without a fresh end-to-end encode/transcode campaign on production material.

On current `main` the L1 suite reports 486 PASS / 0 FAIL and `--level full` reports
678 PASS / 0 FAIL, the difference being work merged after the v0.7.1 tag
(arbitrary-reference audio delay correction, then Phase 4A selective MP4 audio
retention, then Phase 4B audio encoding and output composition). Any change must be
re-checked for new failures.

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
│   ├── audio_integration.py     L3 audio integration on real material
│   ├── pipeline.py              L3 full pipeline + fault injection
│   ├── hardware.py              L3 channel-sync end to end
│   ├── toolchain.py             L2 tool / capability probing
│   └── cli.py                   CLI contract
├── reporting/       result aggregation and report writing
└── (production coordination lives outside this package: `core/audio_encode.py`,
    `core/output_compose.py`)
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
├── VERSION                 Single source of the version number (0.7.1)
├── *.json                  Encoder profile / scaling configurations
├── core/                   Runtime core: pipeline, probing, planning, audio, dashboard
├── encoders/               Encoder backends, capability probing, hardware decode, integrity gate
├── preservation/           Sony / DJI metadata preservation, validation, quality sampling
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
| `core/` | Backend-agnostic runtime logic: configuration, probing, source classification, scaling, batching, channel sync, the v0.7.1 audio modules, logging, dashboard. |
| `encoders/` | Backend implementations (NVEncC, QSVEncC, x265, SVT-AV1), capability tables, hardware-decode routing, integrity gate. |
| `preservation/` | Sony and DJI preservation pipelines, container/ISO-BMFF handling, validation checkers, quality sampling. |
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
- [Next-cycle release notes](docs/release_notes_next.md) — arbitrary-reference audio delay correction (implemented on `main`, unpublished).
- [v0.6.1 release notes](docs/release_notes_v0.6.1.md) — channel-sync streaming memory fix and AV1 colour metadata fix.
- [Channel-sync P1 design](docs/design/channel_sync_p1.md) — algorithm, thresholds, degradation rules, fixture calibration.
- [Hardware decode deliverables](docs/hardware-decode/README.md) — integration test matrix, final report, patches, toolchain provenance.
- [Evaluation reports](docs/evaluation/) — per-backend production-readiness and AV1 calibration studies.
- [Third-party reference archive](docs/reference/README.md) — archived vendor documentation and upstream sources.
- [Archive status index](olddocs/README.md) — read the status banners before citing archived conclusions.

## Release

Current release: **v0.7.1**

- [GitHub release v0.7.1](https://github.com/Eureka175/1KeyTranscoder/releases/tag/v0.7.1)
  — published as a GitHub release without a binary asset.
- [Release notes v0.7.1](docs/release_notes_v0.7.1.md) — frozen with the tag; the release
  notes file is not rewritten after publication.
- `v0.7.1-rc1` is the release candidate tag and points at the same commit as `v0.7.1`; it
  is not a separate release.
- The GitHub release entries are published with GitHub's "pre-release" flag set; v0.7.1 is
  nevertheless the current release of the project, matching `VERSION` and the `v0.7.1` tag.

Older releases with downloadable self-contained packages:

| Version | Download | Notes |
|---|---|---|
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
| Phase 4A | Selective MP4 audio retention — keeping chosen audio tracks in the MP4 container instead of the current whole-stream copy. |
| Phase 4B | Audio encode / mux integration — encoding the rendered PCM and writing it into the output container, replacing the WAV-only output kind. |
| Later | Automatic cross-file synchronization — deriving delays across files rather than per file. |
| Later | Drift correction — correcting slowly varying offsets (currently detected as `non_constant` and refused) instead of constant integer shifts. |

Arbitrary-reference delay correction has been implemented on `main` after the v0.7.1 tag
and is documented in [release_notes_next.md](docs/release_notes_next.md); no version number
has been assigned to it.

## Limitations

- **Platform**: Windows only; no other platform is tested.
- **Audio CLI**: the v0.7.1 audio pipeline has no command-line interface; it is reachable
  only through the internal API.
- **Audio output**: PCM results can be rendered to WAV but not yet encoded and muxed into
  the output MP4; selective audio retention in MP4 is not implemented.
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
  [release_notes_v0.7.1.md](docs/release_notes_v0.7.1.md) §32.
- **Encoder profile values**: the numbers in the `*.json` profiles are measured
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
