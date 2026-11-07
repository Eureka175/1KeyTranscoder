# 1KeyTranscoder

[English](README.md) | **简体中文**

[![Version](https://img.shields.io/badge/version-0.9.9-blue)](VERSION)
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](#运行要求)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Windows 相机素材批量转码器。保留 Sony / DJI 元数据，修复多声道音频时差，
支持硬件缩放，并可检查和修复已有成品。

## 运行要求

- Windows 10/11 x64。
- Python 3.11+。
- `numpy` / `scipy` 用于声道同步和音频分析。
- NVIDIA NVENC、Intel QSV，或软件 x265 / SVT-AV1。
- 自包含包已包含 FFmpeg、NVEncC、QSVEncC 和 GPAC。

## 快速开始

```powershell
python 1kt.py --input D:\footage --output D:\archive --preset hq
python 1kt.py --input D:\footage --output D:\archive --encoder nvenc
python 1kt.py --input D:\footage --output D:\archive --encoder qsv --preset uhq
```

不指定 `--encoder` 时，按 **NVENC -> QSV -> x265** 顺序自动选择。
硬件后端不会回退到软件编码。

## 输入与后端

| 输入 | 处理 |
|---|---|
| Sony XAVC（`rtmd`） | 保留 `rtmd`、`nrtm` 和厂商 `uuid` box |
| DJI Action（`djmd`/`dbgi`/`tmcd`） | 保留运动与时间元数据 |
| DJI Pocket / 无人机 | 默认不保留元数据；使用 `--preserve-metadata` |
| 其它相机文件 | 标准转码 |

| 后端 | 编码 |
|---|---|
| `nvenc`, `nvenc-av1` | NVEncC 的 HEVC / AV1 |
| `qsv`, `qsv-av1` | QSVEncC 的 HEVC / AV1 |
| `x265`, `svtav1` | 软件 HEVC / AV1 |

预设：`uhq`、`hq`、`small`、`fast`。

## 缩放

- `--resize WxH`：精确输出分辨率（偶数尺寸），不修正源长宽比。
- `--scale FACTOR`：按长边倍率缩放并保持源长宽比（`0.5`、`0.25`、`2` 等）；
  输出尺寸取偶数。
- `--resize-algo`：`bilinear`、`bicubic`、`lanczos`；默认 **lanczos**。
- `--resize` 与 `--scale` 互斥。都不指定时输出分辨率不变。
- NVENC：`--vpp-resize` + `--output-res`。
- QSV：同上，另加 `--vpp-resize-mode quality`。
- x265 / SVT-AV1 默认硬件解码，并在该路径使用 `scale_cuda` 或 `vpp_qsv` 缩放；
  无硬件解码时回退 FFmpeg CPU 缩放。
- `--repair`、`--channel-sync-transparent` 会拒绝这两个选项。

```powershell
python 1kt.py ... --encoder nvenc --scale 0.5     # 4K -> 1080p
python 1kt.py ... --encoder qsv --scale 2         # 1080p -> 4K
python 1kt.py ... --encoder x265 --resize 1920x1080
```

## 音频

- 3–8 个独立 PCM 声道默认自动对齐（48/96 kHz）。
- 长镜头在延迟非恒定时使用时变对齐。
- `--audio-plan JSON` 选择、映射、排序、对齐和编码音频。
- 压缩音频、PCM 与压缩音频混合的文件需要显式计划。

## 检查与修复

```powershell
python 1kt.py ... --check basic
python 1kt.py ... --check advanced
python 1kt.py ... --check full
python 1kt.py --input D:\deliverables --output D:\fixed --repair
```

`basic` 检查核心元数据和流信息；`advanced` 增加完整结构检查与 Gyroflow 校验；
`full` 增加详细自检和 PSNR/SSIM 抽样。

批处理支持断点续跑。失败文件写入 `logs/failed_files.json`，可用
`--retry-list` 重试。

## 测试

```powershell
python tests\full_autotest.py --level unit
python tests\full_autotest.py --level toolchain
python tests\full_autotest.py --level full
```

## 文档

- [v0.9.9 发布说明](docs/release_notes_v0.9.9.md)
- [硬件缩放验证](docs/design/hardware_scaling_validation.md)
- [硬件缩放可行性](docs/design/hardware_scaling_feasibility.md)
- [音频计划指南](docs/audio_plan.md)
- [x265 档位设计](docs/x265-profile-design.md)
- [硬件解码](docs/hardware-decode/README.md)

## 许可

LGPL-3.0-or-later。见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。




