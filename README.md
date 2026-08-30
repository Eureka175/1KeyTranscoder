# 1KeyTranscoder

递归、可断点续跑的 Windows 批量归档转码器，带 **Sony 相机元数据保留**
（rtmd 逐帧陀螺仪/镜头数据、nrtm 镜头配置文件、厂商 uuid box 全量保留）。
编码后端为 **NVEncC / QSVEncC 硬件编码**（消费定稿档位 JSON，解码恒软解）；
x265 为手动高压缩选项（`--encoder x265`），硬件路径永不自动回退软件。

**主入口：`1kt.py`。**

> 📚 文档已整理分类，见 [docs/README.md](docs/README.md) 索引；
> 三份评估（AV1 可行性、AV1 调参、x265 生产就绪）的汇总结论见
> [docs/FINAL_REPORT.md](docs/FINAL_REPORT.md)。

## 编码器矩阵

| 后端 | 状态 | 档位 JSON | 定位 |
|---|---|---|---|
| NVEncC（NVIDIA NVENC HEVC） | ✅ 生产默认 | `nvenc.json` | 主后端（本机 RTX 5070 Laptop 实测 4K60 > 实时） |
| QSVEncC（Intel QSV HEVC） | ✅ 生产默认 | `qsv.json` | 第二后端 / 双后端并行 |
| x265（软件 HEVC） | ⚠️ 手动高压缩档 | `x265.json` + `x265_scaling.json` | 质量优先的冷归档（吞吐受限，详见评估） |
| VCEEncC（AMD VCE HEVC） | 预留（JSON 已备未接） | `vce.json` | AMD 机器扩展 |
| **AV1**（NVENC/QSV/SVT） | 📋 评估完成、**待实施** | 草案见调参指南 | 仅限非 XAVC 经典路径（XAVC 合规决策，见下） |

> ⚠️ **档位 JSON（nvenc/qsv/vce/x265 及 x265_scaling）内的数值与速度注记
> 均为作者实测标定值，请勿改动**；调参须以测试集回归为依据。

## 快速开始

```powershell
# NVENC, HQ 档, 默认验证强度 (basic)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset hq

# QSV 后端
python 1kt.py --input D:\素材 --output D:\归档 --encoder qsv --preset hq

# 全档位 (UHQ/HQ/SMALL/FAST)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset all

# x265 手动高压缩档（软件，慢）
python 1kt.py --input D:\素材 --output D:\归档 --encoder x265 --preset hq

# 无人值守 (不弹看板窗口, 全部落日志)
python 1kt.py ... --headless
```

Sony XAVC 源（检测 rtmd 数据流）自动走**元数据保留管线**；DJI 素材
（检测 djmd 数据流：Osmo Action 系列 / 无人机）走 **DJI 保留管线**
（视频重编码 + djmd/dbgi/tmcd 原生保留 + 载荷 sha256 校验 + Gyroflow
逐帧四元数消费端校验；mjpeg 封面与 udta 因 GPAC 不可寻址而丢弃并显式
记录）；其余非 Sony 素材走经典单趟路径（**按策略丢弃全部元数据**，
仅保留视频+音频，日志显式声明）。输出为与原文件名相同的 `.MP4`
（保留目录结构）。

## 依赖

| 组件 | 说明 |
|---|---|
| GPAC / MP4Box | `C:\Program Files\GPAC`（或 `--gpac-dir`）——容器重建与元数据保留的核心 |
| NVEncC / QSVEncC | `tools/NVEncC_*/NVEncC64.exe`、`tools/QSVEncC_*/QSVEncC64.exe`（或 `--tool-*`） |
| ffmpeg / ffprobe | 探测与 x265 路径使用（PATH 或 `--ffmpeg/--ffprobe`；tools/ 自带 gyan 9.0.1 全量版，内置 libx265 4.3） |
| Gyroflow（可选） | 消费端校验（`--check advanced/full` 时使用；未安装则跳过并提示） |

## 编码后验证：`--check basic|advanced|full`

| 强度 | Sony（rtmd） | DJI（djmd） |
|---|---|---|
| `basic`（默认） | 仅必要核心元数据：时间线/轨清单/rtmd 载荷 sha256+时序+tref+timecode | 轨道清单 + djmd/dbgi/tmcd 载荷 sha256/size/样本数 + 音频流 + 帧数 |
| `advanced` | 完整结构校验（含 lens/XML/uuid）+ Gyroflow 消费端校验 | + Gyroflow 逐帧四元数消费端校验（type-2 机型/镜头配置 + type-3 org_quat/stab_quat） |
| `full` | advanced + 64 项详细自检；先探测 Gyroflow，未安装则提示并跳过 | + 逐轨时基/媒体时长、载荷首尾 32 字节、ffprobe 流级事实（含 data 轨 tags） |

任何 critical MISSING/MODIFIED 或 Gyroflow FAIL 都会使该文件判定失败。

## 降级与报错处理（不弹窗）

- **能力预判降级**：硬件不支持源格式时直接降级编码（4:2:2 → 10bit
  4:2:0 → 8bit 4:2:0），显著 WARNING + 三处记录（log/CSV/report）；
  `--no-downgrade` 时改为跳过。
- **运行时失败**：自动降级梯重试；读不了容器时先走 MP4Box strip 回退；
  全部失败则该文件 failed，批处理继续。
- **失败记录**：`logs/failed_files.json`（硬件路径）+ 独立详情文件；
  `--retry-list` 支持换后端重跑。

## 并行调度

- `--jobs 1`（默认）单线程；`--jobs N` 固定并发；
- `--jobs auto` 自适应调度（波次实测聚合吞吐，动态增减工作数）；
- `--experimental-multihw` 实验性双后端并行（NVENC+QSV）。
- x265 路径为顺序执行（软件编码已吃满 CPU 线程，并行边际收益小）。

## 双窗口 UI / watchfolder

非 headless 运行自动打开进度看板（nvidia-smi 风格，1.5s 刷新）+ 工作信息
窗口；状态数据恒写 `logs/dashboard.json`。双击 `start.bat` 或
`python watchfolder.py --input <dir> --output <dir> --encoder nvenc ...`
可做轮询批处理（续跑逻辑使重复轮询近零开销）。

## 文档导航

```
docs/
├── README.md            分类索引（每份文件一行说明）
├── FINAL_REPORT.md      ★ 最终报告：三份评估的汇总结论与路线图
├── design/              设计文档（硬件后端设计 / 实施报告 / 集成报告）
├── evaluation/          评估报告（x265 生产就绪 / AV1 可行性 / AV1 调参指南 / 265 详细度）
└── reference/           第三方一手资料存档（x265 / SVT-AV1 / NVENC / QSV / VCE）
```

## 关键决策记录

1. **AV1 与 XAVC 边界**：XAVC 标准只定义 H.264/HEVC；保留 XAVC brand 的
   AV1 文件是伪标准产物，NLE/Catalyst/机身消费链不认。→ **AV1 不默认集成
   元数据保留管线**，仅服务经典路径（非 Sony 素材）；XAVC 素材恒用 HEVC。
   详见 `docs/evaluation/av1_feasibility_report.md` ★ 决策节。
2. **x265 定位**：代码层生产可用；档位参数经官方文档逐项核查，存在已知
   改进项（FAST 档 rd 与 psy-rd、info 可复现性等），修复+验证留档后作为
   手动高压缩档发布。详见 `docs/evaluation/x265_production_assessment.md`。
3. **档位数值权威性**：各编码器 JSON 的档位数值与速度注记为作者实测标定，
   一切调参须以测试集回归为依据。

## 内部测试与自检

```powershell
python tests\run_selfcheck.py --encoder nvenc
python tests\run_selfcheck.py --encoder qsv
python tests\run_selfcheck.py --encoder x265   # 软件档端到端
python -m preservation.selfcheck <original> <final> <log_dir>
```

## 已知限制与说明

- 非 Sony 素材按策略丢弃元数据（DJI 专线未实现，预留独立路径）；
- QSVEncC 8.26 无 `tu/tu_level/mbrc/output_buf_mb` 对应旗标（白名单
  跳过 + WARNING，工具升级后自动启用）；
- VFR 素材自动 `--avsync forcecfr` 规范化为最近有理速率 CFR（WARNING
  记录）；
- 降级链与回退路径的完整故障演练见 `docs/design/implementation_report.md`。

## 回滚

```powershell
git checkout pre_S1S5     # S1-S5 前基线
git checkout pre_ui       # UI/调度改造前基线
git tag -l                # pre_S1S5 / post_S1S5 / pre_ui / post_1kt_ui
```
