# 4K 编码器评估归档（2026-11-19）

> 2026-11-19 08:00–17:26 长测：12:00 前不跑 NVENC；硬件 112 输出、软件 42 输出、对齐 18/18。

## 摘要

- 硬件：16 cells / 112 输出 / 3h34m，全 rc=0；与 11-18 同片段复现：最大码率差 0.057 Mbps、最大 VMAF 差 0.0052。
- 软件：x265 FAST/HQ/SMALL + SVT FAST/HQ/SMALL，42 输出，全 rc=0；x265 HQ 34.1 Mbps/98.34/1.87fps。
- 对齐：6 编码器 × 3 源 × FAST = 18/18 PASS。
- 归档结论：夜间高噪点/颗粒用 x265 HQ 作质量母版；AV1 不用于归档；x265 SMALL 仅作体积受限交付。
- 暂停：17:26 用户要求暂停；报告在暂停后补齐，编码矩阵已全部完成。

## 文件

| 路径 | 大小 | SHA-256（前 12） |
|---|---:|---|
| `alignment\matrix.json` | 24242 B | `5a8c50fdab97` |
| `alignment\REPORT.md` | 972 B | `a09190a61265` |
| `charts\00_contact_sheet.png` | 778690 B | `ba7cb1de287f` |
| `charts\01_rate_quality_by_clip.png` | 440412 B | `1cc8fd6b014e` |
| `charts\02_rate_quality_aggregate.png` | 108933 B | `e331bbfc6f22` |
| `charts\03_speed_quality.png` | 134142 B | `ab5ad27fc160` |
| `charts\04_rate_control_wall.png` | 180453 B | `c11d2d6889cf` |
| `charts\05_hq_heatmaps.png` | 201748 B | `ab2de83d3c72` |
| `charts\06_night_vs_day.png` | 88592 B | `1f12af0589cf` |
| `charts\07_preset_ladder.png` | 220165 B | `794e7e654cc4` |
| `charts\08_efficiency_vs_nvenc.png` | 71698 B | `668b6723abd2` |
| `charts\09_qsv_icq_effect.png` | 128140 B | `6a953cf9d355` |
| `charts\4K_20261119图表合集.pdf` | 1385087 B | `a1da1e6e3804` |
| `charts\REPORT_CHARTS.md` | 1211 B | `759c5d3f8249` |
| `metrics\metrics_hardware.csv` | 21683 B | `5bf319a7d5d0` |
| `metrics\metrics_hardware.json` | 101338 B | `693c952cdee0` |
| `metrics\metrics_software.csv` | 3877 B | `07fcc5d5eb41` |
| `metrics\metrics_software.json` | 12501 B | `2f4425c40d80` |
| `provenance\param_backup_MANIFEST.json` | 1518 B | `22cec93a5f73` |
| `provenance\PAUSED.md` | 1296 B | `62973da9421b` |
| `provenance\rate_control_wall.txt` | 16354 B | `8fc8f196bcd0` |
| `provenance\test_clip_library_MANIFEST.json` | 2825 B | `4e2c950a0ff0` |
| `REPORT_4K_EVALUATION.md` | 2859 B | `4d5848943aca` |
| `REPORT_HARDWARE.md` | 13623 B | `634a015204c4` |
| `REPORT_LONGTEST.md` | 4629 B | `75db4cfe731d` |
| `REPORT_SOFTWARE.md` | 7172 B | `3ae89883493d` |

校验：`SHA256SUMS.txt`；原始输出不入库（`work/hw4k_matrix_20261119/runs/`、`work/sw4k_matrix_20261119/runs/`）。
