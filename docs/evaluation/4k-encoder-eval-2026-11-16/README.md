# 4K 编码器评估归档（2026-11-16）

> **状态：已完成 / 权威实测记录。** 本目录是 2026-11-16 的 4K 原生编码器评估的轻量归档：报告、指标、图表、脚本、素材与环境溯源。原始视频输出体积大，保留在 `work/`（不入库），清单见 `RAW_OUTPUTS.md`。

## 元数据

| 项 | 值 |
|---|---|
| 日期 | 2026-11-16 |
| 产品版本 | v0.9.14（工作树；见 `provenance/ENVIRONMENT.md`） |
| GPU / CPU | RTX 5070 Laptop 8 GB / Intel Core Ultra 9 285H |
| 素材 | 用户授权只读：Action4 夜/昼 POV、20260919 舞团夜、202610 日常、I:2026.09.24 活动 |
| 主矩阵 | 6 条 30 s 4K 片段 × 4 硬件编码器 × 4 挡位 = 96 输出（2.6 h） |
| 软件对照 | x265 FAST/HQ/SMALL 全量 + UHQ 2×10 s；SVT-AV1 FAST/HQ/SMALL 全量；UHQ 实测不可行 |
| 原始输出 | 硬件 96 视频；x265 20；SVT-AV1 18 |

## 结论摘要

* **画质最高**：NVENC AV1（UHQ 41.7 Mbps / 99.13；HQ 34.1 / 98.96）；NVENC HEVC 紧随（42.9 / 99.06；31.0 / 98.93）。
* **QSV**：码率显著更低（HQ 15.7 Mbps）但 VMAF 也更低（98.53）；静态 cap 从未成为约束。
* **速度**：硬解片段 encoder fps：NVENC AV1 93.8 > QSV 82–84 > NVENC HEVC 49.2；软件 x265 FAST 5.8–14.3、SVT FAST 3.3–23、x265/SVT HQ 约 1–12。
* **软件效率**：SVT-AV1 HQ 优于 x265 HQ（19.0 Mbps / 98.73 / 7.9 fps vs 33.1 / 98.68 / 2.0 fps）；4K 重挡位不具生产可行性（x265 UHQ ≈0.24 fps；SVT UHQ ≈0.02 fps）。
* **码控撞墙**：集中在 NVENC + 高复杂度 4K（Action4 昼 82–91%；舞团夜 UHQ 98–99%）；QSV 全部未撞；软件侧 x265 HQ 在 Action4 昼触顶 103%。

## 推荐

1. 实时/批量 4K：NVENC HEVC FAST/HQ；要 AV1 用 NVENC AV1。
2. 离线归档追求码率效率：SVT-AV1 HQ（比 x265 HQ 快且更省）；x265 UHQ 4K 不建议。
3. QSV 适合码率/功耗敏感、可接受画质损失场景。

## 方法与口径

* 每条片段 30 s，夜/昼与相机/编码混编；VMAF/PSNR/SSIM 用完整片段、1280 宽等比缩放、硬件解码计算。
* 码率只统计视频包；cap 来自产品日志（NVENC 动态 VBV、QSV 静态表、x265 CRF+VBV、SVT 类 CRF）。
* 速度包含产品管线的探测/复核/封装开销（pipeline fps）；纯硬解 encoder fps 只在 Action4 两条片段上可比。
* 4 条片段因产品硬件解码完整性门限回退软件解码，只影响速度、不影响码率/画质；详见 `REPORT_HARDWARE.md`。

## 归档内容

| 归档内路径 | 内容 | 大小 | SHA-256（前 12） |
|---|---|---:|---|
| `charts/00_contact_sheet.png` | 图表缩略总览 | 694.5 KB | `d268c4855d8e` |
| `charts/01_rate_quality_by_clip.png` | 各片段码率-质量曲线 | 372.2 KB | `2794e13af3ff` |
| `charts/02_rate_quality_aggregate.png` | 全片段平均码率-质量 | 98.1 KB | `7c5fc11e53cb` |
| `charts/03_speed_quality.png` | 速度-质量 & 纯硬解 encoder fps | 127.4 KB | `0cbf465024f8` |
| `charts/04_rate_control_wall.png` | 码控撞墙（实际 vs cap） | 172.8 KB | `2c99301d81ce` |
| `charts/05_hq_heatmaps.png` | HQ 挡位 VMAF/码率热图 | 175.7 KB | `06b8ac902dbc` |
| `charts/06_night_vs_day.png` | 夜 vs 昼 VMAF | 87.9 KB | `5da2cf510963` |
| `charts/07_preset_ladder.png` | 挡位梯度 | 202.2 KB | `1934480202c3` |
| `charts/08_efficiency_vs_nvenc.png` | 相对 NVENC HEVC 的效率 | 71.1 KB | `209b16659a8a` |
| `charts/4K评估图表合集.pdf` | 8 张图表 PDF 合集 | 1.1 MB | `d2c5a95dc1e2` |
| `charts/hardware_aggregate_rate_quality.png` | 旧硬件报告引用的平均率-质量图 | 76.0 KB | `d04fcbec8ba9` |
| `charts/hardware_vmaf_by_clip.png` | 旧硬件报告引用的逐片段 VMAF 图 | 38.5 KB | `96cff709afb5` |
| `charts/REPORT_CHARTS.md` | 图表索引 | 1.0 KB | `74e3e6813e46` |
| `charts/software_all_encoders_rate_quality.png` | 旧软件报告引用的全编码器率-质量图 | 95.3 KB | `53767a264ffe` |
| `charts/software_speed_hq.png` | 旧软件报告引用的 HQ 速度图 | 34.2 KB | `76f801400e3d` |
| `metrics/metrics_hardware.csv` | 硬件 96 输出逐条指标 | 22.1 KB | `4f6230395417` |
| `metrics/metrics_hardware.json` | 同上（JSON） | 97.5 KB | `0844cbb13218` |
| `metrics/metrics_software.csv` | 软件 38 输出逐条指标 | 3.3 KB | `69df80c34930` |
| `metrics/metrics_software.json` | 同上（JSON） | 10.9 KB | `08a86b77d81d` |
| `provenance/clip_provenance.json` | 6 条主片段 sha256/参数 | 3.4 KB | `60548be59bc9` |
| `provenance/ENVIRONMENT.md` | 环境与版本 | 1.4 KB | `83fe971a5571` |
| `provenance/extra_clips_manifest.json` | 9 条备用切片清单 | 1.9 KB | `b8192c416e83` |
| `provenance/rate_control_wall.txt` | 码控撞墙分析原始输出 | 13.8 KB | `0330d9e30978` |
| `provenance/SOURCES.md` | 素材来源与只读声明 | 3.1 KB | `fd58f1bce595` |
| `provenance/state_hardware.jsonl` | 硬件作业状态（16 条） | 15.1 KB | `5775abe6888c` |
| `provenance/state_software_svtav1.jsonl` | SVT-AV1 作业状态 | 2.6 KB | `8a36a33aba0d` |
| `provenance/state_software_x265.jsonl` | x265 作业状态 | 3.1 KB | `52e5b07acda6` |
| `RAW_OUTPUTS.md` | 原始视频输出清单（本地路径） | 8.3 KB | `5bc718804b73` |
| `REPORT_4K_EVALUATION.md` | 总报告（含图表链接） | 3.2 KB | `a60545983025` |
| `REPORT_HARDWARE.md` | 硬件全矩阵详细报告 | 13.0 KB | `52c9be86aac9` |
| `REPORT_SOFTWARE.md` | x265 / SVT-AV1 软件报告 | 6.7 KB | `507bbe838412` |
| `scripts/analyze_hardware.py` | 硬件指标/VMAF 分析 | 11.2 KB | `87d730f80356` |
| `scripts/analyze_software.py` | 软件指标/VMAF 分析 | 11.4 KB | `55602517e2b8` |
| `scripts/make_charts_4k.py` | 图表生成 | 21.3 KB | `bc81e4dbde10` |
| `scripts/rate_control_wall.py` | 码控撞墙分析 | 3.3 KB | `095552173741` |
| `scripts/report_software.py` | 软件 + 总报告生成 | 15.9 KB | `5c148af23aa5` |
| `scripts/run_hw_matrix.py` | 硬件矩阵驱动器 | 4.2 KB | `2878aaecd6ed` |
| `scripts/run_svt_matrix.py` | SVT-AV1 矩阵驱动器 | 3.5 KB | `e152b1ebefd3` |
| `scripts/run_x265_matrix.py` | x265 矩阵驱动器 | 3.5 KB | `0df094912876` |

> `SHA256SUMS.txt` 覆盖除自身与 `provenance/files.json`（逐文件清单）之外的全部文件；清单与校验和用于确认归档未被篡改。

## 复现

```powershell
# 1) 恢复/准备 6 条片段（归档不含视频；见 provenance/SOURCES.md）
# 2) 硬件矩阵（16 作业）
python work/hw4k_matrix/run_hw_matrix.py
# 3) x265：
python work/sw4k_matrix/run_x265_matrix.py fast hq small uhq
#    注意 x265 UHQ 在 4K 上约 0.24 fps；归档使用 2×10 s 样本
# 4) SVT-AV1：
python work/svt4k_matrix/run_svt_matrix.py fast hq small
# 5) 指标 / 撞墙 / 报告 / 图表
python work/hw4k_matrix/analyze.py
python work/hw4k_matrix/rc_wall.py
python work/analyze_sw.py work/sw4k_matrix
python work/analyze_sw.py work/svt4k_matrix
python work/report_software.py
python work/make_charts_4k.py
```

> 脚本按 `F:\1KeyTranscoder` 布局编写，并假设 `work/` 下的输入切片存在；归档中的副本用于方法复核，不保证脱离本机路径可直接运行。

## 相关

* 详细硬件报告：[REPORT_HARDWARE.md](REPORT_HARDWARE.md)
* 软件报告：[REPORT_SOFTWARE.md](REPORT_SOFTWARE.md)
* 总报告：[REPORT_4K_EVALUATION.md](REPORT_4K_EVALUATION.md)
* 图表索引：[charts/REPORT_CHARTS.md](charts/REPORT_CHARTS.md)
* 素材与环境：[provenance/SOURCES.md](provenance/SOURCES.md)、[provenance/ENVIRONMENT.md](provenance/ENVIRONMENT.md)

## 限制

* 6 条片段来自不同相机/编码/分辨率（4:3 与 16:9、8/10-bit、4:2:2），横向比较以同片段相对排序为主。
* 4 条片段（60 fps HEVC、H.264 8-bit、H.264 4:2:2）在产品管线中回退软件解码；其速度受解码限制，不能代表编码器原生速度。
* x265 UHQ 仅 2×10 s 样本；SVT UHQ 未完成（实测 0.02 fps），不作完整片段结论。
