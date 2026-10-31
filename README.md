# 1KeyTranscoder

<p align="center">
  <img src="logos/Primary%20Logo.png" alt="1KeyTranscoder Primary Logo" width="360">
</p>

> Recursive, resumable Windows batch transcoder for camera archives: Sony XAVC / DJI
> metadata preservation, a structured PCM audio pipeline, channel-delay repair, and an
> archive-repair mode that can verify and fix already-produced deliverables.

[![Version](https://img.shields.io/badge/version-0.8.11-blue)](VERSION)
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](#requirements)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

## Contents

- [Overview](#overview)
- [What's new in 0.8.11](#whats-new-in-0811-pre-release)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Encoders and presets](#encoders-and-presets)
- [Metadata preservation](#metadata-preservation)
- [Audio](#audio)
- [Archive repair](#archive-repair)
- [Output, logs and verification](#output-logs-and-verification)
- [Hardware decode](#hardware-decode)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Documentation](#documentation)
- [Release](#release)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Brand assets](#brand-assets)
- [License](#license)

## Overview

1KeyTranscoder is a command-line batch transcoder for Windows built for archiving camera
footage at scale. It walks an input tree, transcodes every clip with a hardware
(NVEncC / QSVEncC) or software (x265 / SVT-AV1) backend, and writes a mirrored output tree
of `.MP4` files. Batches are **resumable** — completed outputs are detected and skipped —
and failures are recorded per file so they can be retried selectively.

Its distinguishing feature is **metadata preservation**. Sony XAVC clips carry per-frame
gyroscope and lens data (`rtmd`), lens profiles (`nrtm`) and vendor `uuid` boxes; DJI
action-camera clips carry `djmd` motion data, `dbgi` and `tmcd`. These are copied through
the transcode by a dedicated preservation pipeline and verified afterwards (payload
checksums, structure checks, optional Gyroflow consumer-side validation) instead of being
silently dropped by a generic transcoder.

On top of that the project ships:

- a **multi-channel delay compensation** pass for multi-mic PCM layouts (`--channel-sync`,
  `--channel-sync-transparent`, optional grouped anchors and precision mode);
- a **structured PCM audio pipeline** with explicit `--audio-plan` output (selection,
  mapping, format-aware alignment, external audio, AAC/Opus/PCM/FLAC encoding);
- an **archive-repair mode** (`1kt.py --repair`, or the standalone `archive_repair.py`)
  that checks and fixes deliverables that already exist — audio offsets, metadata
  placement, container conformance — without re-encoding video.

Target use case: a single workstation turning multi-hour Sony / DJI shoots into archive
copies that keep the motion and lens metadata tools such as Gyroflow and NLEs need.

## What's new in 0.8.11 (pre-release)

- **Long-take audio alignment is now part of the default path** for 3–8 channel
  PCM sources (no new CLI switch). When the constant-delay path is rejected,
  the whole file falls back to a time-varying pass that implements the research
  round-3 algorithm exactly: `drift_rebuild` pass-1 (2-min weighted-median
  anchors -> 7-anchor median filter -> 30-sample/anchor clamp) + `fine_delay`
  residual (60 s windows, 5-min sliding median, fractional 10k-grid apply).
  Stereo groups are bound with rigid-confirmation gates and keep their
  intra-group offset; polarity is detected and flipped per group; multichannel
  layouts (stereo/quad/5.1/7.1 in one stream) are handled per channel without
  downmixing. Failures fall back safely to the unaligned original audio.
- **Acceptance on two real 80-minute takes is PASS**: curve layer
  median/p90/max = **0/0/0 samples** against an independently re-run reference
  pipeline; audio layer vs the K deliverables = **0/0/0 on CH1/CH3/CH4**
  (CH2 differs by the intentional 4-sample stereo-group decision).
- **Transparent mode can now produce 50 GiB containers.** The old Sony restore
  rewrote the whole file for every metadata step (measured: `-ref` alone
  17 min for <1 GiB). The new `preservation/fast_restore.py` rebuilds the
  container in one sequential read/write pass (no GPAC rewrite): real 50 GiB
  C1305 stage mux 258.7 s, restore **99.1 s**, end-to-end transparent command
  **12.8 min** with the Sony checker reporting 37/37 items preserved and 20/20
  independent payload/structure checks PASS.
- **`--audio-plan` has highest priority**: a plan always wins over automatic
  channel-sync; transparent mode yields to it with an INFO line instead of
  refusing both flags.
- Fixes: multichannel decoder file-handle race (CH3/CH4 could decode as all
  zeros), `ref_of` on bound groups with a fixed representative, empty
  `fixed_files` for mono-multistream layouts, 16-bit `ipcm` sample-entry false
  positive that downgraded transparent remux to a byte copy, and GPAC temp
  files now live on the media volume (the 50 GiB remux previously hit ENOSPC
  on C:).

See [v0.8.11 release notes](docs/release_notes_v0.8.11.md). The previous
release (v0.8.8) added archive repair to the main program; its notes are
[here](docs/release_notes_v0.8.8.md).

## Requirements

- **Windows 10 / 11 (x64)**. Development and verification are performed on Windows; no
  other platform is tested.
- **Python 3.11+**. No `pip` dependencies are required for the core tool. `numpy` and
  `scipy` are optional and only needed by `--channel-sync` / audio analysis; when absent
  the features that need them are skipped with a WARNING and transcoding is unaffected.
- **External tools**, invoked as separate executables:
  - `ffmpeg` / `ffprobe` — the bundled build is the supported one;
  - `NVEncC` 9.31 and/or `QSVEncC` 8.26 for the hardware backends;
  - `GPAC / MP4Box` 26.02 for container rebuilding and metadata preservation;
  - Gyroflow (optional) for consumer-side quaternion validation at `--check advanced|full`.
- A GPU driver matching the hardware backend in use (NVIDIA for NVENC, Intel for QSV).

Every tool path can be overridden with `--tool-nvencc`, `--tool-qsvencc`, `--ffmpeg`,
`--ffprobe`, `--gpac-dir` and `--gyroflow`.

## Installation

### From the release package (self-contained)

The self-contained package bundles ffmpeg/ffprobe, NVEncC, QSVEncC and GPAC, so no separate
toolchain setup is needed: unzip, install Python 3.11+ and a GPU driver, then run `1kt.py`
from the extracted directory.

Build a package for the current source tree with:

```powershell
python release\build_release.py --out-dir dist
```

The builder refuses to build from a dirty tree, from a branch other than `main`, or when
`VERSION` disagrees with the `v<version>` tag. On a feature branch pass `--dev-build`; the
manifest then records `development_build: true` so the artifact cannot be mistaken for an
official release. Validate an archive with:

```powershell
python release\verify_package.py dist\1KeyTranscoder-v<version>-win64-selfcontained.zip `
    --extract-dir "%TEMP%\1KT-clean"
```

The v0.8.11 **pre-release** is published with a self-contained package (attached to
the GitHub release). Earlier 0.8.1–0.8.8 did not ship a package; build one from the
source with the command above.

### From source

```powershell
git clone https://github.com/Eureka175/1KeyTranscoder.git
cd 1KeyTranscoder

# Put ffmpeg/ffprobe, NVEncC, QSVEncC and GPAC under tools\, or pass explicit paths.
python 1kt.py --version
```

No installation step is required: the entry points run directly from the checkout.

## Quick start

```powershell
# Auto-select backend (NVENC -> QSV -> x265) at the default HQ preset
python 1kt.py --input D:\footage --output D:\archive

# Explicit backends / presets
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc --preset hq
python 1kt.py --input D:\footage --output D:\archive --encoder x265  --preset uhq
python 1kt.py --input D:\footage --output D:\archive --encoder svtav1 --preset small

# 4-channel mono PCM delay compensation (integer-sample shifts, video re-encoded)
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc --channel-sync

# Pre-editing pass: stream-copy video, rebuild only the audio tracks that need a shift
python 1kt.py --input D:\footage --output D:\archive --channel-sync-transparent

# Archive repair: fix already-produced deliverables into a staging directory
python 1kt.py --repair --input D:\archive --output D:\repaired --jobs 8

# Full-disk repair over date-named top-level folders (all YYYYMMDD directories)
python 1kt.py --repair --input I:\ --output D:\repaired --repair-dirs "^\d{8}$" --jobs 8

# Unattended run: no dashboard window, everything goes to logs
python 1kt.py --input D:\footage --output D:\archive --headless
```

Long batches are resumable: re-running the same command skips completed outputs.
`--dry-run` probes and prints the commands without encoding. `--retry-list
logs\failed_files.json` re-runs only failed items, optionally on another backend.

For a polling workflow, use `watchfolder.py` or double-click `start.bat`
(edit its `INPUT` / `OUTPUT` / `ENCODER` / `PRESET` / `SYNC` variables first):

```powershell
python watchfolder.py --input D:\incoming --output D:\archive --encoder nvenc `
    --interval 300 --channel-sync --channel-sync-groups --headless
```

## Encoders and presets

| Backend | Codec | Notes |
|---|---|---|
| `nvenc` | HEVC | NVIDIA NVENC via NVEncC |
| `nvenc-av1` | AV1 | NVIDIA NVENC AV1 |
| `qsv` | HEVC | Intel Quick Sync via QSVEncC |
| `qsv-av1` | AV1 | Intel QSV AV1 |
| `x265` | HEVC | Software, explicit manual option |
| `svtav1` | AV1 | Software |

- With no `--encoder`, the backend is chosen by capability probe: **NVENC → QSV → x265**.
  Hardware paths never fall back to software encoding.
- Presets are `uhq`, `hq`, `small`, `fast` (default `hq`). They are four production
  strategies — quality ceiling, production quality, storage efficiency, throughput — not
  a single quality ladder. Values live in the per-backend `*.json` profiles; see
  [x265 profile design](docs/x265-profile-design.md).
- A mismatch between `--config`'s `encoder` field and `--encoder` is a hard error.
- Capability-based downgrade (for example 4:2:2 → 10-bit 4:2:0 → 8-bit 4:2:0) logs a loud
  WARNING; `--no-downgrade` skips instead.
- AV1 output is always 4:2:0. Sony metadata is preserved but the XAVC brand is not written
  (AV1 is outside the XAVC specification).

## Metadata preservation

Source routing is automatic and logged for every file:

| Source | Pipeline |
|---|---|
| Sony XAVC (data track tagged `rtmd`) | Sony preservation: `rtmd` / `nrtm` / vendor `uuid` carried through re-encoding, verified afterwards |
| DJI (Action series) | DJI preservation: `djmd` / `dbgi` / `tmcd` copied natively, payload `sha256` recorded, optional Gyroflow validation |
| DJI Pocket / drone | By default metadata is **not** preserved (normal transcode path); `--preserve-metadata` overrides |
| Everything else | Classic single-pass path (video + audio only) |

- `--no-metadata` disables vendor metadata preservation for Sony and DJI. It is a policy
  switch, not a decode switch: output validity, stream integrity and failure detection are
  unchanged.
- `--check basic|advanced|full` controls post-encode verification:
  - `basic` — core metadata and stream facts (DJI: track manifest + payload sha256);
  - `advanced` — full structure validation and Gyroflow quaternion comparison when
    Gyroflow is installed;
  - `full` — detailed self-check (64 items) including per-track timebase/duration and
    payload head/tail checks.
- Codec/pixel-format downgrades required by encoder capability are logged with a WARNING;
  use `--no-downgrade` to skip such files, and verify the delivered format with `ffprobe`
  when it matters for playback.

## Audio

**Default path:** audio streams are copied (`-c:a copy`; container-level copy in the
preservation pipelines). Nothing below is active unless explicitly requested.

### Channel delay compensation

- `--channel-sync` — per-file GCC-PHAT delay measurement across independent mono PCM
  tracks, corrected by **integer-sample shifts only**. Requires at least three independent
  mono PCM tracks at 48 or 96 kHz; 44.1 kHz and compressed formats are refused; 2CH/1CH
  layouts are not aligned. 4CH material with only two healthy tracks (the 2CH-silent case)
  is skipped by default.
- `--channel-sync-transparent` — pre-editing mode: video and all non-audio streams are
  stream-copied; only audio tracks that need correction are rebuilt. Fully aligned input
  produces a byte-identical output. The other channel-sync flags (`--channel-sync-groups`,
  `--channel-sync-accurate`, `--channel-sync-tv*`) apply in this mode too.
- `--channel-sync-groups` — grouped anchors (opt-in). Handles 4-channel material where
  CH1/CH2 and CH3/CH4 are two unrelated microphones. Additive only: no existing correction
  is lost or changed. `start.bat` enables it by default.
- `--channel-sync-accurate` — precision mode (opt-in): lowers the minimum correction from
  5 samples toward 1–2 samples, each tier accepted only if the independent post-shift
  residual proves it.
- `--channel-sync-tv` — per-window time-varying correction (research/opt-in; accepted by
  both the encoded and the transparent path). It requires explicit
  `--channel-sync-tv-policy` and `--channel-sync-tv-coordinate`; missing either is a
  refusal, not a silent default.

### Explicit audio output (`--audio-plan`)

`--audio-plan <plan.json>` is the only audio-output CLI surface. Without it the default
path is structurally untouched. It can select/exclude/reorder channels, choose the output
codec (`aac` / `opus` / `pcm` / `flac`), request format-aware alignment with an explicit
`sync.reference`, and discover external recordings named after the clip (`clip001.wav`,
`clip001_01.aac`, …).

```json
{
  "version": 1,
  "encode":   { "format": "opus", "bitrate": "96k" },
  "channels": { "select": ["source:s1:c0", "clip001.wav:s0:c0"] },
  "alignment": "auto",
  "sync":     { "reference": "source:s1:c0" },
  "external": {}
}
```

PCM input keeps PCM output; compressed input keeps its own codec/bitrate unless overridden.
Compressed input is never decoded just to align it — an explicit alignment request emits a
warning and takes the decode → PCM → align → re-encode path. `--audio-plan` cannot be
combined with `--channel-sync`. Full guide, worked examples and per-backend support:
[docs/audio_plan.md](docs/audio_plan.md).

## Archive repair

Repair treats an **already-produced deliverable** as an independent object: it re-measures
audio, restores metadata placement, fixes container conformance, and re-verifies the result
— without re-encoding video.

### Main-program batch mode (recommended)

```powershell
python 1kt.py --repair --input I:\ --output D:\repaired --repair-dirs "^\d{8}$" --jobs 8
```

| Option | Meaning |
|---|---|
| `--repair-dirs REGEX` | Only traverse top-level directories whose name fully matches (e.g. `^\d{8}$`); required for whole-disk input |
| `--repair-json DIR` | Report directory (default: inside `--output`) |
| `--repair-scratch DIR` | Temporary directory (default: `--output\_scratch`) |
| `--repair-resume` | Resume: read `_progress.jsonl`, skip completed files with a present output |
| `--repair-retry-failed N` | Serial precision-mode retry rounds for failed files (default 1, 0 = off) |
| `--repair-limit N` | Process at most N files (smoke tests) |
| `--repair-fix-layout` | Also restore moov-first layout (MP4Box `-inter`, slower) |
| `--repair-no-container-fix` | Skip container-level fixes (tkhd/sample-entry/durations) |
| `--repair-no-groups` | Disable grouped anchors (on by default, matching `start.bat`) |
| `--repair-accurate` | Enable precision mode (1→2→5 sample tiers) |
| `--repair-opt K=V` | Override a channel-sync option, e.g. `--repair-opt verify_max_rel=0.2` |

The command writes `_summary.json`, `_summary.md`, a per-file
`archive-repair-report.json/md`, and a `_progress.jsonl` for resume. Exit code is 1 only
when a file really failed; `PARTIAL`/`UNREADABLE` inputs are reported honestly without
breaking the batch.

**Acceptance gates.** A repaired file is accepted only when: `validate.compare` reports no
MODIFIED/MISSING; the video packet streamhash equals the source; and, for the container-only
path, the output size equals the source. Files that fail a gate are retried serially with
precision mode; unresolved files are reported as `FAILED`.

**What is repaired:** per-track integer audio shifts (with residual re-measurement),
metadata placement (`brand` / `tref` / `nrtm` / `uuid`), tkhd flags and alternate groups,
inconsistent PCM sample entries (using `pcmC` as truth), duration/timebase consistency, and
optionally moov-first layout.

**Verdicts:** `REPAIRED`, `NOOP`, `FAILED`; check verdicts include `PASS`, `FAIL`, `WARN`,
`SKIP`, `UNMEASURABLE`, `PARTIAL` (half-finished `.part.` intermediates) and `UNREADABLE`
(missing moov / zero-filled). Only changed files are written to the staging directory.

### Standalone CLI and write-back

```powershell
# Inspect only (source files are read-only; temporary audio goes to --scratch)
python archive_repair.py check --input D:\archive --json D:\reports --jobs 8

# Repair to a staging directory (default; originals untouched)
python archive_repair.py repair --input D:\archive --output D:\repaired --jobs 8

# Write repaired files back in place (requires all three flags)
python archive_repair.py repair --input D:\archive --output D:\repaired `
    --in-place --backup D:\backup --yes
```

Full check list, metadata comparison rules and known limitations:
[docs/archive-repair.md](docs/archive-repair.md).

## Output, logs and verification

- Output mirrors the input tree with an `.MP4` extension. Existing outputs are skipped, so
  a re-run resumes the batch.
- Work scratch lives under `<output>\.1ktwork`; it is removed after each successful job
  unless `--keep-work` is given.
- Logs go to `logs/`: `error.log` and `warn.log` are always written; `total.log` is added at
  `--log-level info` (default), `debug.log` at `debug`. `--fresh-log` clears
  `total.log`/`debug.log` at startup.
- `logs\failed_files.json` records per-file failures with detail. The progress dashboard is
  shown in interactive runs; `--headless` disables it.
- `--check basic|advanced|full` verifies the encode result; `--jobs auto|1|N` controls
  scheduling; `--experimental-multihw` pools NVENC+QSV (quality consistency across
  backends is explicitly not guaranteed).

## Hardware decode

Two independent decode routes, one per encoder family:

| Encoders | Decode route |
|---|---|
| `x265`, `svtav1` | FFmpeg native NVDEC/QSV decode, `hwdownload` + format conversion, then `libx265`/`libsvtav1` in the same process |
| `nvenc`, `qsv` | The encoder's own reader: Sony → `--avsw`; non-Sony → `--avhw` |

Sony material deliberately uses software decoding on the rigaya path: the hardware reader
was measured to lose leading pictures on XAVC open-GOP timelines silently (`rc=0`, a
playable file, 27 of 30 frames). Every `--avhw` result is gated on frame count plus
reader-identity checks; a failing artifact is deleted and the format rung is retried once
with `--avsw` — never delivered short.

The retired switches `--hw-decode`, `--hw-decode-verify` and `--no-hw-autoselect` no longer
exist. Research builds under `tools/avhw/` are not required by production. The
hardware-decode correctness matrix lives in `tests/hwdecode/`; its entry points and
two-gate reporting (production vs research) are described in
[docs/hardware-decode/README.md](docs/hardware-decode/README.md).

## Testing

```powershell
python tests\full_autotest.py --level unit        # L1: logic and deterministic behaviour
python tests\full_autotest.py --level toolchain   # L2: + tools, capabilities, flag allowlist
python tests\full_autotest.py --level full        # L3: + real pipeline integration and fault injection
```

Targeted checks:

```powershell
python tests\run_selfcheck.py --encoder nvenc|qsv|x265
python -m preservation.selfcheck <original> <final> <log_dir>
```

Scheduled hardware-decode verification is separate:

```powershell
python -m tests.hwdecode.harness provenance
python -m tests.hwdecode.harness check-matrix
python -m tests.hwdecode.harness run --phase 1
python -m tests.hwdecode.harness summary
```

`summary` reports two gates: **production** (runs on the deployed binaries) and
**research** (needs the patched research builds and is reported as blocked when they are
absent). Missing material or tools are reported as skips, never counted as passes.

## Project structure

```text
1KeyTranscoder/
├── 1kt.py                  Main CLI: transcode and repair orchestration
├── archive_repair.py       Standalone archive check/repair CLI
├── watchfolder.py          Polling batch entry point
├── start.bat               Double-click launcher (edit its variables)
├── VERSION                 Single source of the version number
├── *.json                  Encoder profile / scaling configurations
├── core/                   Runtime core: pipeline, probing, planning, audio, dashboard
├── encoders/               Encoder backends, capability probing, hardware decode, gate
├── preservation/           Sony / DJI metadata preservation, validation, quality
├── production/             Cross-domain orchestration: audio output → final container
├── release/                Release packaging and package verification
├── tests/                  Automated suites (incl. tests/hwdecode/)
├── docs/                   Design, evaluation, release and reference documentation
├── logos/                  Brand assets
├── licenses/               GNU GPL v3 text (referenced by LGPL-3.0)
├── olddocs/                Archived documents and code snapshots
├── LICENSE                 GNU LGPL v3 text
└── NOTICE                  Copyright and SPDX identifier
```

Local-only, untracked directories: `tools/` (shared toolchain), `testsets/` (test media),
`work/` (scratch/reports), `logs/`, `dist/` (release artifacts), `data/` (local corpora).

## Documentation

- [Documentation index](docs/README.md) — classified index of everything under `docs/`.
- [Architecture](docs/design/architecture.md) — end-to-end data flow, invariants, module map.
- [Archive repair](docs/archive-repair.md) — checks, one-click repair, batch mode, gates,
  limitations.
- [Audio plan guide](docs/audio_plan.md) — explicit audio output, worked examples,
  external-audio naming, per-backend support.
- [Channel-sync design](docs/design/channel_sync_p1.md) — algorithm, thresholds,
  degradation rules.
- [Audio-sync P4](docs/audio-sync-p4/README.md) — production policy, grouped anchors,
  time-varying research and deferred decisions.
- [Hardware decode deliverables](docs/hardware-decode/README.md) — integration matrix,
  final report, patches, provenance.
- [x265 profile design](docs/x265-profile-design.md) — why the four profiles exist and how
  they were calibrated.
- [v0.8.11 release notes](docs/release_notes_v0.8.11.md) — default long-take audio
  alignment (reference round-3), 50 GiB transparent-mode fast restore.
- [v0.8.8 release notes](docs/release_notes_v0.8.8.md) — archive repair in the main
  program, autonomous batches, integrity gates.
- [v0.8.0 release notes](docs/release_notes_v0.8.0.md) — audio plan, format-aware
  alignment, external audio.

## Release

Current source version: **0.8.11** (`VERSION`, tag `v0.8.11`, published as a
GitHub **pre-release**).

- Self-contained packages are produced by `release/build_release.py`; see
  [Installation](#installation).
- The v0.8.11 pre-release package is attached to
  <https://github.com/Eureka175/1KeyTranscoder/releases/tag/v0.8.11>.
- `VERSION`, the git tag and the builder must agree: the builder refuses
  mismatches unless a development build is explicitly requested.

## Limitations

- **Platform**: Windows 10/11 x64 only.
- **Channel sync**: 3–8 channel PCM sources are aligned automatically; 2CH/1CH and
  compressed formats are not aligned, and more than 8 channels require
  `--channel-sync alignment=enabled`. Constant offsets use the fixed path; otherwise the
  whole file falls back to time-varying alignment (reference round-3 algorithm) or, on
  failure, to the unaligned original audio. Resampling and loudness are not part of the
  pipeline.
- **Audio plan**: `--audio-plan` cannot be combined with `--channel-sync`; Sony/DJI
  preservation rejects a plan per file instead of ignoring it. External audio must be named
  after the clip.
- **Metadata**: non-Sony/DJI sources keep no vendor metadata. DJI Pocket/drone sources are
  not preserved by default (use `--preserve-metadata` to override; `--no-metadata` wins if
  both are given). The MJPEG cover image and `udta` are dropped from DJI files because GPAC
  26.02 cannot address them.
- **AV1**: always 4:2:0; Sony metadata is preserved but no XAVC brand is written.
- **Pixel format**: format downgrades required by encoder capability are logged with a
  WARNING (for example 4:2:2 → 10-bit 4:2:0 → 8-bit 4:2:0); `--no-downgrade` skips such
  files instead. Check the output's pixel format with `ffprobe` when a delivery format is
  mandatory.
- **Hardware decode**: Sony material always uses software decode on the rigaya path; some
  formats are refused by the hardware (measured per machine); a time seek is not
  seek-equivalent between readers. Research builds are not used in production.
- **Repair**: silent / `non_constant` / `low_confidence` tracks that the quality gate
  refuses cannot be fixed (`AUD-04` WARN); zero-filled files without a moov cannot be
  recovered (`UNREADABLE`). Parallel repair can hit transient corruption; the integrity
  gates catch it and the serial retry resolves it, otherwise the file is reported as
  `FAILED`.
- **Playback**: codec and pixel-format choices follow the selected backend and its
  capability downgrade rules; verify the delivered format with `ffprobe` before assuming
  broad hardware playback.
- **QSV quality**: the profile aligned to NVENC is calibrated, not identical; high-motion
  material remains roughly 1 dB below NVENC (hardware ceiling).
- **Internal APIs**: several audio objects are explicitly internal/reserved and may change.
- **Profiles**: the numbers in the `*.json` profiles are measured calibrations; changing
  them requires a test regression run.

## Contributing

Issues and pull requests are welcome.

- Run `python tests\full_autotest.py --level unit` at minimum; run `--level full` before
  submitting anything touching the pipeline. No pull request may introduce a new FAIL.
- Changes touching preservation, container handling or encoder profiles must state the
  material and tool versions used for verification.
- Do not change the encoder profile JSON values or the brand/logo assets without an
  explicit, recorded reason.
- Documentation lives in `docs/`; the root README stays user-facing.

## Brand assets

Three assets under `logos/`, used as-is:

```markdown
<p align="center">
  <img src="logos/Primary%20Logo.png" alt="1KeyTranscoder Primary Logo" width="360">
</p>
<img src="logos/Symbol%20Mark.png" alt="1KeyTranscoder Symbol Mark" width="120">
<img src="logos/Word%20Mark.png" alt="1KeyTranscoder Word Mark" width="280">
```

## License

**GNU Lesser General Public License v3.0 or later (LGPL-3.0-or-later).**

- [LICENSE](LICENSE) — GNU LGPL v3 text.
- [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt) — GNU GPL v3 text, incorporated by
  reference by LGPL-3.0.
- [NOTICE](NOTICE) — copyright and SPDX identifier.

Third-party tools (NVEncC, QSVEncC, GPAC, ffmpeg, Gyroflow) are invoked as separate
executables and are distributed under their own licences; they are not part of this
project's codebase.
