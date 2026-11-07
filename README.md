# 1KeyTranscoder

**English** | [简体中文](README.zh-CN.md)

[![Version](https://img.shields.io/badge/version-0.9.6-blue)](VERSION)
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

## Hardware scaling

Scaling is opt-in and uses the encoder's GPU VPP path.

```powershell
# Default kernel: spline36
python 1kt.py --input D:\footage --output D:\archive `
    --encoder nvenc --scale 1920x1080

# Explicit kernel
python 1kt.py --input D:\footage --output D:\archive `
    --encoder qsv --scale 2560x1440 --resize-algo lanczos4
```

- `--scale WxH` (alias `--resize WxH`): target output size; even dimensions required.
- `--resize-algo`: `bilinear`, `bicubic`, `spline16`, `spline36`, `spline64`,
  `lanczos3`, `lanczos4`; default **spline36**.
- NVENC: `--vpp-resize algo=... --output-res WxH`.
- QSV: same plus `--vpp-resize-mode quality`.
- Scaling is available on NVENC/QSV only. Sony and DJI preservation paths are supported.
- Software backends, `--repair`, and `--channel-sync-transparent` reject `--scale`.
- Without `--scale`, output resolution is unchanged.

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

Batches are resumable. Failed files are written to `logs/failed_files.json` for
retry with `--retry-list`.

## Tests

```powershell
python tests\full_autotest.py --level unit
python tests\full_autotest.py --level toolchain
python tests\full_autotest.py --level full
```

Real-feature tests require `testsets/` and `F:\Pictures\2026`; without them,
environment-only checks report FAIL.

## Documentation

- [Release notes v0.9.6](docs/release_notes_v0.9.6.md)
- [Hardware scaling validation](docs/design/hardware_scaling_validation.md)
- [Hardware scaling feasibility](docs/design/hardware_scaling_feasibility.md)
- [Audio plan guide](docs/audio_plan.md)
- [x265 profile design](docs/x265-profile-design.md)
- [Hardware decode](docs/hardware-decode/README.md)

## License

LGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
