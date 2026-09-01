# 1KeyTranscoder

递归、可断点续跑的 Windows 批量归档转码器，带 **Sony / DJI 双相机元数据保留**：
- **Sony XAVC**（rtmd 数据流）：逐帧陀螺仪/镜头数据（rtmd）、镜头配置文件
  （nrtm）、厂商 uuid box（PROF/USMT）全量保留；
- **DJI**（djmd 数据流，Osmo Action 系列 / 无人机）：djmd 运动四元数 +
  dbgi + tmcd 时码原生保留，Gyroflow 逐帧消费端校验。

编码后端为 **NVEncC / QSVEncC 硬件编码**（解码恒软解，硬件路径永不回退软件）；
x265 为手动高压缩选项。**主入口：`1kt.py`。**

> 📚 文档索引见 [docs/README.md](docs/README.md)；评估汇总与决策见
> [docs/FINAL_REPORT.md](docs/FINAL_REPORT.md)。

## 编码器矩阵

| 后端 | 状态 | 档位 JSON | 定位 |
|---|---|---|---|
| NVEncC（NVIDIA NVENC HEVC） | ✅ 生产默认 | `nvenc.json` | 主后端。本机 5070 Laptop：HQ 4K60 ≈ 23fps，4:2:2 直编 Rext 保真 |
| QSVEncC（Intel QSV HEVC） | ✅ 生产 | `qsv.json` / **`qsv_aligned.json`** | 第二后端。`qsv_aligned.json` 为按 NVENC 同档质量标定的对齐版（见下） |
| x265（软件 HEVC） | ✅ 手动高压缩档（P0 修复完成） | `x265.json` + `x265_scaling.json` | 质量优先冷归档 / 4:2:2 保真唯一软件路径（吞吐受限，缩放规则仍 PROVISIONAL，见 `work/x265_test/`） |
| VCEEncC（AMD VCE HEVC） | 预留（JSON 已备未接） | `vce.json` | AMD 机器扩展 |
| AV1（NVENC/QSV/SVT） | 📋 评估完成、待实施 | 草案见调参指南 | 仅限非 XAVC 经典路径（XAVC 合规决策） |

> ⚠️ 档位 JSON 内的数值为作者实测标定值，请勿改动；调参须以测试集回归
> 与 `tests/full_autotest.py` 为依据。

## 快速开始

```powershell
# NVENC, HQ 档, 默认验证强度 (basic)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset hq

# QSV 后端（质量与 NVENC 对齐版）
python 1kt.py --input D:\素材 --output D:\归档 --encoder qsv --config qsv_aligned.json --preset hq

# 全档位 (UHQ/HQ/SMALL/FAST)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset all

# x265 手动高压缩档（软件，慢）
python 1kt.py --input D:\素材 --output D:\归档 --encoder x265 --preset hq

# 无人值守 (不弹看板窗口, 全部落日志)
python 1kt.py ... --headless
```

**三条自动路径**：Sony XAVC（rtmd）→ 元数据保留管线；DJI（djmd）→ DJI
保留管线（视频重编码 + djmd/dbgi/tmcd 原生复制 + 载荷 sha256 + Gyroflow
四元数校验；mjpeg 封面与 udta 因 GPAC 26.02 不可寻址而丢弃并显式记录）；
其余素材 → 经典单趟（按策略仅视频+音频，日志显式声明）。输出为同名
`.MP4`，保留目录结构。

## 依赖

| 组件 | 说明 |
|---|---|
| GPAC / MP4Box | `C:\Program Files\GPAC`（或 `--gpac-dir`）——容器重建与元数据保留核心（**行为绑定 26.02**，升级须回归） |
| NVEncC / QSVEncC | `tools/NVEncC_9.31_x64/`、`tools/QSVEncC_8.26_x64/`（或 `--tool-*`） |
| ffmpeg / ffprobe | 9.0.1 gyan full（tools/ 自带，内置 libx265/libsvtav1/libvmaf） |
| Gyroflow（可选） | 消费端校验（`--check advanced/full`；未安装则提示并跳过） |

## 编码后验证：`--check basic|advanced|full`

| 强度 | Sony（rtmd） | DJI（djmd） |
|---|---|---|
| `basic`（默认） | 时间线/轨清单/rtmd 载荷 sha256+时序+tref+timecode | 轨道清单 + djmd/dbgi/tmcd 载荷 sha256/size/样本数 + 音频流 + 帧数 |
| `advanced` | + lens/XML/uuid 完整结构 + Gyroflow 消费端 | + Gyroflow 逐帧四元数（type-2 机型/镜头配置 + type-3 org_quat/stab_quat） |
| `full` | + 详细自检（逐项 PASS/FAIL 落盘）+ **PSNR/SSIM 质量抽样** | + 逐轨时基/媒体时长、载荷首尾 32 字节、ffprobe 流级事实 + **PSNR/SSIM 质量抽样** |

任何 critical MISSING/MODIFIED 或 Gyroflow FAIL 都会使该文件判定失败。

**PSNR/SSIM 质量抽样**（`full` 级，防花屏/出错，不是质量门槛）：
源文件名 sha256 确定性 **10 取 1**，仅 **≤60s 短视频**；`setpts=N`
帧索引对齐（规避容器 timebase 失配）；阈值 psnr ≥25dB、ssim ≥0.80、
垃圾帧（psnr<12dB）占比 ≤2%，达标外判定该文件失败（经典路径在
交付前拦截）。阈值可经各档位 JSON 的 `quality_check` 节调整；
结果落盘 `quality_<名>.json` + 批次汇总 `logs/quality_samples.csv`。

**环境版本记录**：每批次启动时收集软件/驱动版本
（ffmpeg/NVEncC/QSVEncC/GPAC/Gyroflow + GPU 驱动）→
`logs/env_versions.json` + `env_versions.csv`，供编码行为复现。

## 质量对齐（NVENC ↔ QSV）

`qsv_aligned.json` 为 QSV 档位按 **NVENC 同档三指标参照**标定的对齐版
（VMAF v0.6.1 主指标 + SSIM/PSNR 辅，双片段验证轮）：

| 档位 | 原 icq | 对齐 icq |
|---|---|---|
| UHQ | 21 | 20 |
| HQ | 22 | 21 |
| SMALL | 26 | 23 |
| FAST | 24 | 22 |

标定报告与全量数据见 `work/quality_align/align_report.md`（可复跑脚本
`align.py`）。注意：Arc 无 Lookahead/EncTools（官方确认），对齐后的 QSV
在高运动素材上仍低 NVENC ~1dB——这是硬件天花板，非配置问题；
`--experimental-multihw` 混跑建议使用对齐版配置以缩小跨后端质量差。

## 降级与报错处理（不弹窗）

- **能力预判降级**：4:2:2 → 10bit 4:2:0 → 8bit 4:2:0，显著 WARNING +
  三处记录（log/CSV/report）；`--no-downgrade` 时改为跳过；
- **运行时失败**：自动降级梯重试；读不了容器先走 MP4Box strip 回退；
  全失败则该文件 failed，批处理继续；
- **失败记录**：`logs/failed_files.json` + 独立详情文件；`--retry-list`
  支持换后端重跑（可接受 failed_files.json 或纯文本路径清单）。

## 并行调度

- `--jobs 1`（默认）/ `--jobs N` 固定并发 / `--jobs auto` 自适应
  （波次实测聚合吞吐动态调整，无写死预算表）；
- `--experimental-multihw` 实验性双后端并行（NVENC+QSV，**质量一致性
  不保证**——见质量对齐节）；
- x265 路径顺序执行。

## 双窗口 UI / watchfolder

非 headless 运行自动打开进度看板（nvidia-smi 风格，1.5s 刷新）+ 工作信息
窗口；状态数据恒写 `logs/dashboard.json`。`start.bat` /
`python watchfolder.py --input <dir> --output <dir> --encoder nvenc ...`
可做轮询批处理（续跑逻辑使重复轮询近零开销）。

## 自动化测试（三级深度）

```powershell
python tests\full_autotest.py --level unit        # L1 纯逻辑 (50 项, 秒级, 零外部依赖)
python tests\full_autotest.py --level toolchain   # L2 + 工具版本/实机能力/旗标白名单 (~13s)
python tests\full_autotest.py --level full        # L3 + 真实管线集成 + 故障注入 (~3 分钟)
python tests\full_autotest.py --level all         # 等同 full
```

- **L1 unit**：color token 表、caps 解析、格式规划、失败分类、flag 构造、
  probe/paths、源分类、缩放引擎、gpac parse_info、dji facts；
- **L2 toolchain**：真实工具版本、`--check-features` 实机能力、
  known_flags 白名单、Gyroflow/GPAC 探测；
- **L3 full**：Sony/DJI/经典 × NVENC/QSV 真实管线（basic+full check）、
  截断文件/尾部垃圾/断点续跑/retry-list 故障注入、strip 机制本体。
  输入在 `work/autotest/` 自建副本（testsets 只读），报告
  `work/autotest/autotest_report.{json,md}`，退出码 0=全过。

另有定向自检：`python tests\run_selfcheck.py --encoder nvenc|qsv|x265`、
`python -m preservation.selfcheck <original> <final> <log_dir>`。

## 文档导航

```
docs/
├── README.md            分类索引
├── FINAL_REPORT.md      ★ 四份评估汇总结论与路线图
├── design/              设计文档：硬件后端设计 / 实施报告(含 DJI §15) / 集成报告 / HEVC 4:2:2 Rext 播放兼容性
├── evaluation/          评估：HEVC 生产就绪度(重写版) / x265 生产就绪 / AV1 可行性 / AV1 调参 / SVT-AV1 归档
└── reference/           第三方一手资料存档（x265 / SVT-AV1 / NVENC / QSV / VCE）

olddocs/                 历史档案存档（各阶段代码快照 / 被取代的旧脚本），详见 olddocs/README.md
```

## 关键决策记录

1. **AV1 与 XAVC 边界**：XAVC 标准只定义 H.264/HEVC，保留 XAVC brand 的
   AV1 文件是伪标准产物 → AV1 不默认集成保留管线，XAVC 素材恒用 HEVC。
2. **DJI 专线**：djmd 即运动数据载体（Gyroflow 官方支持 Action 4/5/6、
   Avata、Neo）；`MP4Box -diso` XML 对 DJI 文件解析失败 → 轨道枚举全部
   走 `-info` 文本解析；mjpeg 封面/udta GPAC 不可寻址，按策略丢弃。
3. **HEVC 生产就绪度**：硬件双后端"有条件生产就绪"（15/15 验收 +
   色彩元数据端到端 + 质量对齐）；4:2:2 输出为 Rext，硬解仅 Blackwell，
   归档定位为"压缩归档副本"（母版标准是 FFV1/ProRes）。
4. **x265 定位**：手动高压缩档。P0 修复已落地并回归（FAST rd 2→3 激活
   psy-rd、info=false 可复现、删除 no-strong-intra-smoothing、level 6.2 +
   CPB 钳位 240Mbit）；DJI 素材走同构保留管线（djmd 原生保留）；
   缩放规则仍 PROVISIONAL。详见 `work/x265_test/x265_test_report.md`。
5. **档位数值权威性**：JSON 数值为作者实测标定，调参须回归测试集。

## 已知限制与说明

- DJI：mjpeg 封面与 udta 丢弃（GPAC 26.02 不可寻址，日志显式）；机内
  Rocksteady/EIS 开启的素材无运动数据（djmd 存在但四元数为空，校验按
  两侧相等通过）；
- QSV：`lookahead(--la-depth)` 在 Arc 全系无效（LA 全 x，旗标被接受但
  特性不生效）；驱动/QSVEncC 版本对需钉住（6557/6559 曾有批量编码回归史）；
- Sony 4:2:2 成品 = HEVC Rext，播放硬解仅 NVIDIA 50 系，其余需软解播放器
  （VLC/mpv）；分发请出 4:2:0 副本；
- 非 Sony 非 DJI 素材按策略丢弃元数据（仅视频+音频）；
- VFR 素材自动 `--avsync forcecfr` 规范化（WARNING 记录）；
- 经典路径无 1:1 帧闸门（不误杀 VFR）；Sony/DJI 路径有。

## 许可证

**GNU Lesser General Public License v3.0 或更高版本（LGPL-3.0-or-later）**，
全部开源。详见 [`LICENSE`](LICENSE)。

> 第三方工具（NVEncC/QSVEncC、GPAC、ffmpeg、Gyroflow）以独立可执行文件
> 形式调用，各按其自身许可证分发，不并入本项目。

## 回滚

```powershell
git checkout pre_S1S5              # S1-S5 前基线
git tag -l                         # pre_S1S5 / post_S1S5 / pre_ui / post_1kt_ui
                                   # post_adaptive / post_hw_fulltest / post_color_meta
                                   # post_dji / post_dji_checklevels / post_quality_align
                                   # post_autotest / post_x265 / v0.4.0 / v0.4.1
```

> 分支约定: `main` 仅保留 HEVC/265 实现; **AV1 (svtav1/nvenc-av1/qsv-av1)
> 实现在 `av1` 分支** (`git checkout av1`, 含 post_av1 / post_av1_calib /
> v0.5.0 tag)。
