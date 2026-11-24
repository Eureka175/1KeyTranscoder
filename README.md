# 1KeyTranscoder

**English** | [简体中文](README.zh-CN.md)

[![Version](https://img.shields.io/badge/version-0.9.23-blue)](VERSION)
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](#requirements)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Windows batch transcoder for camera archives. It preserves Sony and DJI
metadata, repairs multi-channel audio timing, supports hardware scaling, and
can verify or repair existing deliverables.

## Requirements

- Windows 10/11 x64.
- Python 3.11+.
- `numpy` / `scipy` for channel sync and audio analysis.
- NVIDIA NVENC, Intel QSV, or software x265 / SVT-AV1.
- A self-contained package includes FFmpeg, NVEncC, QSVEncC and GPAC.

## Quick start

```powershell
python 1kt.py --input D:\footage --output D:\archive --preset hq
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc
python 1kt.py --input D:\footage --output D:\archive --encoder qsv --preset uhq
```

Without `--encoder`, the backend is selected in order: **NVENC -> QSV -> x265**.
Hardware backends never fall back to software encoding.

## Inputs and backends

| Input | Handling |
|---|---|
| Sony XAVC (`rtmd`) | Preserves `rtmd`, `nrtm` and vendor `uuid` boxes |
| DJI Action (`djmd`/`dbgi`/`tmcd`) | Preserves motion and timing metadata |
| DJI Pocket / drones | Metadata off by default; use `--preserve-metadata` |
| Other camera files | Standard transcode |

| Backend | Codec |
|---|---|
| `nvenc`, `nvenc-av1` | HEVC / AV1 via NVEncC |
| `qsv`, `qsv-av1` | HEVC / AV1 via QSVEncC |
| `x265`, `svtav1` | Software HEVC / AV1 |

Presets: `uhq`, `hq`, `small`, `fast`.

## DJI Action split recordings

DJI Action cameras split long recordings near 16 GB. `--dji-join` (off by
default) detects contiguous segments and joins them losslessly in one MP4Box
pass (`-unalign-cat` keeps exact CFR video; `djmd`/`dbgi`/`tmcd` preserved),
then runs the normal pipeline on the joined source.

```powershell
python 1kt.py --input D:\footage --output D:\archive --dji-join
python 1kt.py ... --dji-join --dry-run           # detect groups only
```

Joining is progressive: the first group is joined before encoding starts, each
next group is joined in the background while the previous one is encoding, and
a delivered group's joined source is deleted immediately. Peak disk use is the
current group plus the next one, not every group at once. `-flat` makes
MP4Box write directly to the destination (no full-size temp file), so a 60 GB
joined group costs ~60 GB, not ~120-180 GB. Staging lives under
`<output>/.dji_join_work_*`; the tree is removed after a fully successful run
and kept on failure or with `--dji-join-keep-temp` (which also keeps every
joined group, so plan for the full sum). See
[DJI split recordings](docs/dji-segments.md).

## Lossless remux (Sony XAVC / DJI)

Sony cameras allocate each clip's media data in 128 MiB chunks; a short clip
leaves unused tail space inside `mdat`. `--remux` rewrites the container
losslessly:

```powershell
python 1kt.py --input "D:\SonyClips" --output "D:\SonyClips_remux" --remux
```

- Video / PCM audio / `rtmd` data track are copied bit-for-bit;
- `nrtm`, Lens profile, vendor `uuid`, `tref/cdsc` and XAVC brands are preserved;
- validation follows `--check` (`basic` / `advanced` / `full`); full adds
  Gyroflow + detailed selfcheck; PSNR/SSIM is N/A because video is stream-copied;
- XML sidecars are copied; non-Sony files are rejected explicitly.

With `--dji-join`, remux mode also handles DJI split recordings without any
video encode:

```powershell
python 1kt.py --input "D:\DJI" --output "D:\DJI_remux" --remux --dji-join
```

- split groups are joined losslessly first;
- each joined file (and each ungrouped DJI file) is rebuilt as a fresh
  container with video/audio/`djmd`/`dbgi`/`tmcd` copied bit-for-bit;
- validation follows `--check` (`basic` / `advanced` / `full`); full quality
  metrics are reported as not applicable because the video is stream-copied;
- XML sidecars are copied and joined-sidecar names follow the joined output.

Both remux paths honor `--jobs` (`auto` uses the same adaptive scheduler as
the main pipeline; `N` sets the number of concurrent remux jobs). Validation
is asynchronous with one verifier slot: waiting for `--check` does not consume
a remux job slot. `--no-metadata` keeps remux lossless but reduces validation
to container/frame checks. A failed validation removes the artifact and
retries the remux once; a second failure marks the file failed.

Measured on 53 Sony clips: **19.67 GB -> 17.88 GB, 1.79 GB saved (9.09%)**,
per-file savings 1.4%–40.2%.

## Output format

- Default output is **10-bit**, regardless of source depth; use
  `--output-depth {8,10,12}` to override.
- Chroma follows the source (4:2:0 / 4:2:2 / 4:4:4) where the backend supports
  it; `--output-chroma {420,422,444}` overrides explicitly.
- NVENC HEVC supports true 4:4:4 output; QSV/AV1 reject unsupported manual
  chroma loudly.
- **mono is not supported** (source or output); it fails with a clear error.

## Scaling

- `--resize WxH`: exact output resolution (even dimensions); does not preserve
  the source aspect ratio.
- `--scale FACTOR`: multiplies the long edge and preserves the source aspect ratio
  (`0.5`, `0.25`, `2`, ...); output dimensions are rounded to even values.
- `--resize-algo`: `bilinear`, `bicubic`, `lanczos`; default **lanczos**.
- `--resize` and `--scale` are mutually exclusive. Without either option the output
  resolution is unchanged.
- NVENC: `--vpp-resize` with `--output-res`.
- QSV: same plus `--vpp-resize-mode quality`.
- x265 / SVT-AV1 default to hardware decode and scale in that path through
  `scale_cuda` or `vpp_qsv`; FFmpeg CPU scaling is the fallback when hardware
  decode is unavailable.
- `--repair` and `--channel-sync-transparent` reject both options.

```powershell
python 1kt.py ... --encoder nvenc --scale 0.5     # 4K -> 1080p
python 1kt.py ... --encoder qsv --scale 2         # 1080p -> 4K
python 1kt.py ... --encoder x265 --resize 1920x1080
```

## Audio

- 3–8 independent PCM channels are aligned automatically (48/96 kHz).
- Long takes use time-varying alignment when the delay is not constant.
- `--audio-plan JSON` selects, maps, orders, aligns and encodes audio.
- Compressed audio and mixed PCM/compressed files require an explicit plan.

## Verification and repair

```powershell
python 1kt.py ... --check basic
python 1kt.py ... --check advanced
python 1kt.py ... --check full
python 1kt.py --input D:\deliverables --output D:\fixed --repair
```

`basic` checks core metadata and stream facts; `advanced` adds full structural
and Gyroflow checks; `full` adds detailed self-checks and a PSNR/SSIM sample.

Batches are resumable. Failed files are written to
`<output>/logs/failed_files.json` for retry with `--retry-list`.
Runtime logs (including per-file logs and the dashboard JSON) live under
`<output>/logs/`, so each output directory is self-contained; the temporary
NVENC/QSV raw encoder logs live in `<output>/.1ktwork/<job>/` and are removed
on success.

## Tests

```powershell
python tests\full_autotest.py --level unit
python tests\full_autotest.py --level toolchain
python tests\full_autotest.py --level full
```

Real-feature tests require `testsets/` and `F:\Pictures\2026`; without them,
environment-only checks report FAIL.

## Documentation

- [Release notes v0.9.23](docs/release_notes_v0.9.23.md)
- [Release notes v0.9.22](docs/release_notes_v0.9.22.md)
- [Release notes v0.9.21](docs/release_notes_v0.9.21.md)
- [Release notes v0.9.20](docs/release_notes_v0.9.20.md)
- [Release notes v0.9.19](docs/release_notes_v0.9.19.md)
- [Release notes v0.9.18](docs/release_notes_v0.9.18.md)
- [Release notes v0.9.17](docs/release_notes_v0.9.17.md)
- [Release notes v0.9.16](docs/release_notes_v0.9.16.md)
- [Release notes v0.9.15](docs/release_notes_v0.9.15.md)
- [Release notes v0.9.14](docs/release_notes_v0.9.14.md)
- [Release notes v0.9.12](docs/release_notes_v0.9.12.md)
- [DJI Action split recordings](docs/dji-segments.md)
- [Hardware scaling validation](docs/design/hardware_scaling_validation.md)
- [Hardware scaling feasibility](docs/design/hardware_scaling_feasibility.md)
- [Audio plan guide](docs/audio_plan.md)
- [x265 profile design](docs/x265-profile-design.md)
- [Hardware decode](docs/hardware-decode/README.md)
- [4K encoder evaluation archive (2026-11-16, hardware + x265 / SVT-AV1)](docs/evaluation/4k-encoder-eval-2026-11-16/README.md)

## License

LGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).







