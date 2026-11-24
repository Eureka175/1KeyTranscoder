# 1KeyTranscoder

[English](README.md) | **简体中文**

[![Version](https://img.shields.io/badge/version-0.9.23-blue)](VERSION)
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

## DJI Action 分段录制

DJI Action 相机录到约 16 GB 会切出新文件。`--dji-join`（**默认关闭**）检测
连续分段，用一趟 MP4Box 无损连接（`-unalign-cat` 保证视频严格 CFR，保留
`djmd`/`dbgi`/`tmcd`），再把连接后的完整素材交给普通编码管线。

```powershell
python 1kt.py --input D:\footage --output D:\archive --dji-join
python 1kt.py ... --dji-join --dry-run           # 只检测分组，不连接不编码
```

连接是渐进的：第一组先连接好才开始编码，之后每组都在上一组编码期间后台
连接；该组编码交付后立即删除它的 joined 源。峰值落盘只有“当前组 + 下一组”，
不是全部组之和。`-flat` 让 MP4Box 直接写目标文件（不再生成同体积临时文件），
所以 60 GB 的组只占约 60 GB，而不是约 120–180 GB。暂存树在
`<output>/.dji_join_work_*`，全部成功后删除；失败或
`--dji-join-keep-temp` 时保留（keep-temp 会保留所有 joined 组，需按总和预留
空间）。详见 [DJI 分段录制](docs/dji-segments.md)。

## 无损 remux（Sony XAVC / DJI）

Sony 相机按 128 MiB 录制 chunk 写 `mdat`，短片会在 chunk 尾部留下未使用空间。
`--remux` 用原生轨道复制重写容器：

```powershell
python 1kt.py --input "D:\SonyClips" --output "D:\SonyClips_remux" --remux
```

- 视频 / PCM 音频 / `rtmd` 数据轨原样复制；
- 保留 `nrtm`、Lens profile、vendor `uuid`、`tref/cdsc`、XAVC brand；
- 校验等级跟随 `--check`（`basic` / `advanced` / `full`）；full 增加
  Gyroflow + 深度自检，PSNR/SSIM 因 stream copy 记为不适用；
- 同时复制 XML 边车；非 Sony 文件明确拒绝。

加 `--dji-join` 后，remux 模式也可处理 DJI 分段录制，且完全不编码视频：

```powershell
python 1kt.py --input "D:\DJI" --output "D:\DJI_remux" --remux --dji-join
```

- 先无损连接 DJI 分段；
- 每个 joined 文件（以及未分组的 DJI 单文件）都重建为干净容器，
  视频/音频/`djmd`/`dbgi`/`tmcd` 原样复制；
- 校验等级跟随 `--check`（`basic` / `advanced` / `full`）；full 的质量指标
  因视频是 stream copy 而单列为不适用；
- XML 边车会复制，joined 输出的边车名跟随 joined 文件名。

两条 remux 路径都遵循 `--jobs`（`auto` 复用主流程自适应调度；`N` 为并发
remux 任务数）。校验是异步的，只有一个 verifier 槽位；等待 `--check` 不占用
remux 任务槽位。`--no-metadata` 下仍保持无损 remux，但校验降为纯容器/帧数
检查；校验失败会删除产物并自动重试 remux 一次，第二次失败则标记文件失败。

`20261122扫街` 53 个文件实测：**19.67 GB → 17.88 GB，节省 1.79 GB（9.09%）**，
单文件节省 1.4%–40.2%。

## 输出格式

- 默认 **10-bit**，不论源深度；`--output-depth {8,10,12}` 可覆盖；
- 色度默认跟随源（4:2:0 / 4:2:2 / 4:4:4），`--output-chroma {420,422,444}`
  可显式指定；
- NVENC HEVC 支持真正的 4:4:4；QSV / AV1 对不支持的手动色度明确报错；
- **mono 不支持**（源和输出都不支持），会给出明确错误。

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

批处理支持断点续跑。失败文件写入 `<output>/logs/failed_files.json`，可用
`--retry-list` 重试。

运行日志（含每文件日志和看板 JSON）统一位于 `<output>/logs/`，输出目录自带
完整日志；NVENC/QSV 的原始编码日志位于 `<output>/.1ktwork/<job>/`，成功后
随工作目录清理，不再残留在成品目录。

## 测试

```powershell
python tests\full_autotest.py --level unit
python tests\full_autotest.py --level toolchain
python tests\full_autotest.py --level full
```

## 文档

- [v0.9.23 发布说明](docs/release_notes_v0.9.23.md)
- [v0.9.22 发布说明](docs/release_notes_v0.9.22.md)
- [v0.9.21 发布说明](docs/release_notes_v0.9.21.md)
- [v0.9.20 发布说明](docs/release_notes_v0.9.20.md)
- [v0.9.19 发布说明](docs/release_notes_v0.9.19.md)
- [v0.9.18 发布说明](docs/release_notes_v0.9.18.md)
- [v0.9.17 发布说明](docs/release_notes_v0.9.17.md)
- [v0.9.16 发布说明](docs/release_notes_v0.9.16.md)
- [v0.9.15 发布说明](docs/release_notes_v0.9.15.md)
- [v0.9.14 发布说明](docs/release_notes_v0.9.14.md)
- [v0.9.12 发布说明](docs/release_notes_v0.9.12.md)
- [DJI Action 分段录制](docs/dji-segments.md)
- [硬件缩放验证](docs/design/hardware_scaling_validation.md)
- [硬件缩放可行性](docs/design/hardware_scaling_feasibility.md)
- [音频计划指南](docs/audio_plan.md)
- [x265 档位设计](docs/x265-profile-design.md)
- [硬件解码](docs/hardware-decode/README.md)
- [4K 编码器评估归档（2026-11-16，硬件 + x265 / SVT-AV1）](docs/evaluation/4k-encoder-eval-2026-11-16/README.md)

## 许可

LGPL-3.0-or-later。见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。







