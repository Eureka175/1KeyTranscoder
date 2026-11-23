# 4K 编码器评估归档（2026-11-18 长测）

> 7 条 30 s 片段（原 6 + 室内高噪点）、4 硬件编码器 × 4 挡、x265 FAST/HQ、SVT-AV1 FAST/HQ/SMALL。
> 关键变化：QSV ICQ 下调后的码率/质量提升；硬件 8-bit profile 覆盖修复。

状态：**已完成**（2026-11-18，长程测试窗口内）。

## 摘要

- 硬件：112 输出，216.4 min，全 rc=0；QVBR 平均（Mbps/VMAF）：NVENC HEVC 43.2/98.76（UHQ）、QSV HEVC 25.9/98.50、NVENC AV1 42.2/98.85、QSV AV1 42.7/98.68。
- QSV ICQ 下调（同 6 片段）：HEVC UHQ 19.4/98.65→24.7/98.78，HQ 15.7/98.53→19.5/98.65，SMALL 7.4/97.80→12.9/98.39，FAST 12.0/98.29→18.3/98.59；AV1 各挡亦提升。
- 软件：x265 HQ 34.1/98.34/1.6fps，FAST 17.9/98.13/8.2fps；SVT-AV1 HQ 18.3/98.44/7.0fps，SMALL 14.7/98.36/7.0fps，FAST 16.0/98.25/11.7fps。
- 对齐矩阵 18/18 PASS；新增硬件 8-bit 计划修复（main/main10 动态选择）。

## 文件

| 路径 | 大小 | SHA-256（前 12） |
|---|---:|---|
| `alignment\matrix.json` | 24241 B | `dc15742713cd` |
| `alignment\REPORT.md` | 952 B | `e9f5ac8162a1` |
| `charts\hardware_aggregate_rate_quality.png` | 85051 B | `7de84ac8006d` |
| `charts\hardware_vmaf_by_clip.png` | 44068 B | `1bc974565b13` |
| `charts\software_all_encoders_rate_quality.png` | 101720 B | `9303a897fe4e` |
| `charts\software_speed_hq.png` | 34446 B | `f610110c3ef7` |
| `metrics\metrics_hardware.csv` | 21685 B | `5515e4735189` |
| `metrics\metrics_hardware.json` | 101333 B | `1ee6872e5720` |
| `metrics\metrics_software.csv` | 3240 B | `be95bc524021` |
| `metrics\metrics_software.json` | 10415 B | `abc4e2cdfdf9` |
| `provenance\param_backup_MANIFEST.json` | 1518 B | `22cec93a5f73` |
| `provenance\rate_control_wall.txt` | 16354 B | `ad4ec2e070a9` |
| `provenance\test_clip_library_MANIFEST.json` | 2825 B | `4e2c950a0ff0` |
| `REPORT_4K_EVALUATION.md` | 2828 B | `56fe5bb7766e` |
| `REPORT_HARDWARE.md` | 13614 B | `6f0156d748cd` |
| `REPORT_LONGTEST.md` | 4917 B | `683f6bb84a49` |
| `REPORT_SOFTWARE.md` | 6887 B | `700a855900cb` |

校验：`SHA256SUMS.txt`；原始视频输出在 `work/hw4k_matrix_20261118/runs/` 与 `work/sw4k_matrix_20261118/runs/`（不入库）。

## 最终图表包

* [00_contact_sheet.png](charts/final/00_contact_sheet.png)
* [01_rate_quality_by_clip.png](charts/final/01_rate_quality_by_clip.png)
* [02_rate_quality_aggregate.png](charts/final/02_rate_quality_aggregate.png)
* [03_speed_quality.png](charts/final/03_speed_quality.png)
* [04_rate_control_wall.png](charts/final/04_rate_control_wall.png)
* [05_hq_heatmaps.png](charts/final/05_hq_heatmaps.png)
* [06_night_vs_day.png](charts/final/06_night_vs_day.png)
* [07_preset_ladder.png](charts/final/07_preset_ladder.png)
* [08_efficiency_vs_nvenc.png](charts/final/08_efficiency_vs_nvenc.png)
* [09_qsv_icq_effect.png](charts/final/09_qsv_icq_effect.png)
* [图表 PDF](charts/final/4K_20261118图表合集.pdf)
* [图表索引](charts/final/REPORT_CHARTS.md)

## 肉眼复核（用户实测，2026-11-18）

- 夜间高噪点/颗粒：**x265 明显优于 NVENC**（用户肉眼 A/B）；
- VMAF 排序相反：x265 HQ 37.1 Mbps/94.53 vs NVENC HEVC UHQ 63.6 Mbps/96.31；
- 归档结论修正：质量母版用 **x265 HQ**（x265 UHQ 待验证），NVENC HEVC UHQ 退居生产力/干净素材/快速归档；
- x265 SMALL 仅作体积受限交付，不作母版。
