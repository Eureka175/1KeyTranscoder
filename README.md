# 1KeyTranscoder

递归、可断点续跑的 Windows 批量归档转码器，带 **Sony 相机元数据保留**
（rtmd 逐帧陀螺仪/镜头数据、nrtm 镜头配置文件、厂商 uuid box 全量保留）。
编码后端为 **NVEncC / QSVEncC 硬件编码**（消费定稿档位 JSON，解码恒软解）；
x265 仅作为手动选项（`--encoder x265`），硬件路径永不自动回退软件。

**主入口：`1kt.py`**。

## 依赖

| 组件 | 说明 |
|---|---|
| GPAC / MP4Box | `C:\Program Files\GPAC`（或 `--gpac-dir`）——容器重建与元数据保留的核心 |
| NVEncC / QSVEncC | `tools/NVEncC_*/NVEncC64.exe`、`tools/QSVEncC_*/QSVEncC64.exe`（或 `--tool-*`） |
| ffmpeg / ffprobe | 探测与 x265 路径使用（PATH 或 `--ffmpeg/--ffprobe`） |
| Gyroflow（可选） | 消费端校验（`--check advanced/full` 时使用；未安装则跳过并提示） |

## 快速开始

```powershell
# NVENC, HQ 档, 默认验证强度 (basic)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset hq

# QSV 后端
python 1kt.py --input D:\素材 --output D:\归档 --encoder qsv --preset hq

# 全档位 (UHQ/HQ/SMALL/FAST)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset all

# 无人值守 (不弹看板窗口, 全部落日志)
python 1kt.py ... --headless
```

Sony XAVC 源（检测 rtmd 数据流）自动走**元数据保留管线**；非 Sony 素材
走经典单趟路径（**按策略丢弃全部元数据**，仅保留视频+音频，日志显式
声明）。输出为与原文件名相同的 `.MP4`（保留目录结构）。

## 编码后验证：`--check basic|advanced|full`

独立的 check 模块（`preservation/checker.py`）对**所有后端**（含 x265）
的 Sony 输出生效：

| 强度 | 内容 | 耗时 |
|---|---|---|
| `basic`（默认） | 仅必要核心元数据：时间线/轨清单/rtmd 载荷 sha256+时序+tref+timecode | 最省 |
| `advanced` | 完整结构校验（含 lens/XML/uuid）+ Gyroflow 消费端校验 | 中 |
| `full` | advanced + 64 项详细自检（逐轨 stts/stsz/stsd/elst、rtmd 首尾字节、uuid 清单）；先探测 Gyroflow，未安装则提示并跳过消费端对比 | 最全 |

任何 critical MISSING/MODIFIED 或 Gyroflow FAIL 都会使该文件判定失败。

## 降级与报错处理（不弹窗）

- **能力预判降级**：硬件不支持源格式时直接降级编码（4:2:2 → 10bit
  4:2:0 → 8bit 4:2:0），显著 WARNING + 三处记录（log/CSV/report）；
  `--no-downgrade` 时改为跳过该文件。
- **运行时失败**：不弹任何窗口——工作窗口打印 + 逐级 WARNING + 自动
  降级梯重试；读不了容器时先走 MP4Box strip 回退；全部失败则该文件
  failed，批处理继续。
- **失败记录**：`logs/failed_files.json`（含**简要错误摘要**与指向
  `logs/failed_details/<job>.txt` 的独立详情文件）。
- **失败重跑**：
  ```powershell
  python 1kt.py --input <dir> --output <dir> --retry-list logs\failed_files.json --encoder x265
  ```

## 并行调度

- `--jobs 1`（默认）单线程；
- `--jobs N` 固定并发；
- `--jobs auto` **自适应调度**：按波次实测聚合编码吞吐，动态增减工作
  数（增长 >3% 加一路、衰减 <7% 回撤到历史最优、每 6 波上探一次），
  无写死预算表，安全上限按机器 CPU 核数推导；
- `--experimental-multihw` **实验性**双后端并行（NVENC+QSV）。启用即
  打印声明：**无法保证视频编码质量一致性**。路由 v1：4:2:2 → NVENC，
  其余轮流，两端各自自适应。

## 双窗口 UI

- 非 headless 运行时自动打开两个窗口：
  1. **进度看板**（nvidia-smi 风格独立控制台，1.5s 刷新：文件/后端/
     状态/帧数 fps/耗时/汇总计数），批处理结束时自动关闭；
  2. **工作信息窗口**（主控制台：命令、WARNING、错误），全部同步落
     `logs/total.log` 与 `logs/files/<文件>_<档位>.log`。
- 状态数据始终写入 `logs/dashboard.json`（`--headless` 时也写，可外部
  监控）。

## watchfolder / 启动器

```powershell
# 双击 start.bat（编辑其中的 INPUT/OUTPUT/ENCODER 等变量）
# 或命令行:
python watchfolder.py --input <dir> --output <dir> --encoder nvenc --preset hq --check basic --jobs 1 --interval 60
```

每轮把主程序作为子进程跑一遍（续跑逻辑使重复轮询近零开销）；
`--once` 单轮（测试用）。

## 内部测试与自检

```powershell
# 主程序输出 + 内置校验 + 64 项详细 Sony 元数据对比, log 入盘
python tests\run_selfcheck.py --encoder nvenc
python tests\run_selfcheck.py --encoder qsv

# 独立详细对比 CLI
python -m preservation.selfcheck <original> <final> <log_dir>
```

## 目录结构

```
1kt.py                  主入口 (thin orchestrator)
core/
  batch_hw.py           硬件批量处理/降级梯/失败记录/自适应调度
  caps.py 探测缓存      hw.py 执行器/失败分类   dashboard* 进度看板
  probe/scaling/classifier/config/logging_utils/paths/postprobe
encoders/
  nvencc.py qsvencc.py x265.py
preservation/
  pipeline.py           Sony 保留管线编排
  checker.py            独立验证模块 (basic/advanced/full)
  selfcheck.py          64 项详细对比
  sony/gpac/isobmf/validate/gyroflow
watchfolder.py start.bat
tests/                  run_selfcheck.py + 详细对比 CLI
docs/                   hardware_backend_design.md / implementation_report.md
```

## 已知限制与说明

- 非 Sony 素材按策略丢弃元数据（DJI 专线未实现，预留独立路径）；
- QSVEncC 8.26 无 `tu/tu_level/mbrc/output_buf_mb` 对应旗标（白名单
  跳过 + WARNING，工具升级后自动启用）；
- VFR 素材自动 `--avsync forcecfr` 规范化为最近有理速率 CFR（WARNING
  记录）；
- 降级链与回退路径的完整故障演练见 `docs/implementation_report.md`。

## 回滚

```powershell
git checkout pre_S1S5     # S1-S5 前基线
git checkout pre_ui       # UI/调度改造前基线
git tag -l                # pre_S1S5 / post_S1S5 / pre_ui / post_1kt_ui
```
