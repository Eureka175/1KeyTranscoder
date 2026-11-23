# 4K 软件编码器评估：x265 / SVT-AV1（30 s 多场景）

与 `work/hw4k_matrix_20261119` 硬件矩阵使用同一批 7 条 30 s 只读切片。

## 范围与限制（先看）

* x265：FAST/HQ/SMALL 完整 7 片段；UHQ 未跑（4K UHQ 约 0.24 fps，单条 >1 h）。
* SVT-AV1：FAST/HQ/SMALL 完整 7 片段；UHQ 未跑（历史实测约 0.02 fps）。
* 软件重挡位/未跑项不与 30 s 完整片段直接比较。
* 软件编码使用产品默认参数；x265 为 `slow`（HQ/UHQ/SMALL 同一 preset，不同 CRF/质量参数）。

## 软件矩阵逐片段结果（30 s 完整片段）

### day_action4_pov

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 57.4 Mb / VMAF 99.97 / 0.8 fps | 31.3 Mb / VMAF 99.84 / 0.4 fps | 37.9 Mb / VMAF 99.92 / 5.6 fps |
| SVT-AV1 | — | 37.9 Mb / VMAF 99.96 / 4.4 fps | 28.9 Mb / VMAF 99.92 / 4.6 fps | 34.3 Mb / VMAF 99.93 / 6.9 fps |

### day_daily_canon

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 25.1 Mb / VMAF 99.63 / 2.7 fps | 14.2 Mb / VMAF 99.43 / 2.6 fps | 19.3 Mb / VMAF 99.48 / 14.0 fps |
| SVT-AV1 | — | 15.9 Mb / VMAF 99.63 / 8.3 fps | 13.2 Mb / VMAF 99.57 / 8.1 fps | 15.2 Mb / VMAF 99.53 / 17.4 fps |

### day_daily_sony

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 16.9 Mb / VMAF 99.01 / 2.3 fps | 8.9 Mb / VMAF 98.32 / 2.3 fps | 10.9 Mb / VMAF 98.43 / 12.6 fps |
| SVT-AV1 | — | 10.7 Mb / VMAF 98.98 / 7.5 fps | 9.4 Mb / VMAF 98.93 / 7.5 fps | 11.1 Mb / VMAF 98.71 / 10.8 fps |

### event_sony_422

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 28.3 Mb / VMAF 98.94 / 1.7 fps | 7.3 Mb / VMAF 98.57 / 2.3 fps | 9.4 Mb / VMAF 98.67 / 11.5 fps |
| SVT-AV1 | — | 7.7 Mb / VMAF 98.90 / 11.8 fps | 6.3 Mb / VMAF 98.86 / 11.2 fps | 7.6 Mb / VMAF 98.72 / 22.5 fps |

### indoor_static_noisy_20261117_30s

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 40.2 Mb / VMAF 96.34 / 2.1 fps | 3.6 Mb / VMAF 96.07 / 4.4 fps | 8.1 Mb / VMAF 96.26 / 15.5 fps |
| SVT-AV1 | — | 14.0 Mb / VMAF 96.68 / 11.1 fps | 11.3 Mb / VMAF 96.62 / 11.0 fps | 9.5 Mb / VMAF 96.52 / 23.4 fps |

### night_action4_pov

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 33.6 Mb / VMAF 99.97 / 0.7 fps | 14.2 Mb / VMAF 99.58 / 0.8 fps | 16.9 Mb / VMAF 99.48 / 7.5 fps |
| SVT-AV1 | — | 13.3 Mb / VMAF 99.80 / 2.5 fps | 10.9 Mb / VMAF 99.67 / 2.6 fps | 12.5 Mb / VMAF 99.60 / 3.2 fps |

### night_dance_iso

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 37.1 Mb / VMAF 94.53 / 2.8 fps | 17.5 Mb / VMAF 94.17 / 4.0 fps | 22.7 Mb / VMAF 94.66 / 13.8 fps |
| SVT-AV1 | — | 28.6 Mb / VMAF 95.15 / 10.7 fps | 23.2 Mb / VMAF 94.93 / 10.8 fps | 21.8 Mb / VMAF 94.78 / 20.9 fps |

## 全片段平均（完整 30 s，x265 UHQ 除外）

| Encoder | Preset | 视频 Mbps | VMAF | PSNR | SSIM | pipeline fps |
|---|---|---|---|---|---|---|
| x265 | HQ | 34.08 | 98.341 | 44.86 | 0.98514 | 1.9 |
| x265 | SMALL | 13.86 | 97.997 | 43.58 | 0.98007 | 2.4 |
| x265 | FAST | 17.86 | 98.129 | 43.64 | 0.97987 | 11.5 |
| SVT-AV1 | HQ | 18.31 | 98.443 | 44.40 | 0.98269 | 8.0 |
| SVT-AV1 | SMALL | 14.73 | 98.358 | 44.05 | 0.98180 | 8.0 |
| SVT-AV1 | FAST | 16.01 | 98.255 | 43.96 | 0.98139 | 15.0 |

## 与硬件编码器对照（FAST / HQ，视频 Mbps / VMAF）

### FAST

| 片段 | NVENC HEVC | NVENC AV1 | QSV HEVC | QSV AV1 | x265 | SVT-AV1 |
|---|---|---|---|---|---|---|
| day_action4_pov | 40.6 / 99.98 | 31.8 / 99.96 | 37.5 / 99.96 | 21.7 / 99.77 | 37.9 / 99.92 | 34.3 / 99.93 |
| day_daily_canon | 23.9 / 99.75 | 17.5 / 99.70 | 15.1 / 99.57 | 11.0 / 99.40 | 19.3 / 99.48 | 15.2 / 99.53 |
| day_daily_sony | 17.4 / 99.01 | 17.3 / 99.16 | 12.4 / 98.81 | 9.4 / 98.37 | 10.9 / 98.43 | 11.1 / 98.71 |
| event_sony_422 | 21.4 / 98.80 | 17.9 / 99.06 | 9.0 / 98.92 | 6.1 / 98.58 | 9.4 / 98.67 | 7.6 / 98.72 |
| indoor_static_noisy_20261117_30s | 13.3 / 96.62 | 27.4 / 96.79 | 16.7 / 96.39 | 6.5 / 96.16 | 8.1 / 96.26 | 9.5 / 96.52 |
| night_action4_pov | 19.3 / 99.77 | 21.3 / 99.94 | 15.3 / 99.70 | 8.8 / 98.55 | 16.9 / 99.48 | 12.5 / 99.60 |
| night_dance_iso | 31.7 / 95.62 | 29.9 / 95.34 | 20.4 / 94.58 | 13.9 / 94.21 | 22.7 / 94.66 | 21.8 / 94.78 |

### HQ

| 片段 | NVENC HEVC | NVENC AV1 | QSV HEVC | QSV AV1 | x265 | SVT-AV1 |
|---|---|---|---|---|---|---|
| day_action4_pov | 49.1 / 99.98 | 42.1 / 99.97 | 39.9 / 99.96 | 41.9 / 99.96 | 57.4 / 99.97 | 37.9 / 99.96 |
| day_daily_canon | 28.0 / 99.76 | 25.4 / 99.75 | 16.9 / 99.62 | 15.5 / 99.57 | 25.1 / 99.63 | 15.9 / 99.63 |
| day_daily_sony | 18.7 / 99.03 | 25.6 / 99.25 | 13.0 / 98.89 | 13.1 / 98.66 | 16.9 / 99.01 | 10.7 / 98.98 |
| event_sony_422 | 23.2 / 99.03 | 34.0 / 99.07 | 10.4 / 98.96 | 9.2 / 98.77 | 28.3 / 98.94 | 7.7 / 98.90 |
| indoor_static_noisy_20261117_30s | 25.3 / 96.75 | 41.8 / 96.88 | 22.0 / 96.58 | 11.5 / 96.36 | 40.2 / 96.34 | 14.0 / 96.68 |
| night_action4_pov | 21.1 / 99.87 | 31.3 / 99.97 | 16.0 / 99.76 | 14.9 / 99.64 | 33.6 / 99.97 | 13.3 / 99.80 |
| night_dance_iso | 46.3 / 95.90 | 46.3 / 95.77 | 20.9 / 94.72 | 23.1 / 94.89 | 37.1 / 94.53 | 28.6 / 95.15 |

## 速度（pipeline fps，完整片段；越高越快）

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 9.5 | 10.1 | 9.8 | 16.0 |
| NVENC AV1 | 8.9 | 12.1 | 14.5 | 14.0 |
| QSV HEVC | 15.9 | 16.4 | 17.1 | 23.7 |
| QSV AV1 | 12.4 | 13.3 | 15.2 | 17.7 |
| x265 | 0.2–0.3（10 s 样本） | 1.9 | 2.4 | 11.5 |
| SVT-AV1 | — | 8.0 | 8.0 | 15.0 |

* x265 UHQ 4K 实测约 0.24 fps；SVT-AV1 UHQ 4K 实测约 0.02 fps。
* NVENC/QSV 编码器速度包含解码/复核/封装开销（pipeline fps）；纯硬解片段见硬件报告。

## 软件编码的码率上限（CRF + VBV）

mean/cap ≥80% 的格子：5 个。
* x265 / HQ / day_action4_pov: mean/cap≈103%（cap 56.0 Mbps）
* x265 / SMALL / day_action4_pov: mean/cap≈93%（cap 33.6 Mbps）
* x265 / HQ / indoor_static_noisy_20261117_30s: mean/cap≈89%（cap 45.0 Mbps）
* SVT-AV1 / SMALL / day_action4_pov: mean/cap≈86%（cap 33.6 Mbps）
* x265 / FAST / day_action4_pov: mean/cap≈85%（cap 44.8 Mbps）

## 主要发现

* FAST：码率最低 QSV AV1 (11.1 Mbps / VMAF 97.86)；VMAF 最高 NVENC AV1 (23.3 Mbps / 98.56)。
* HQ：码率最低 SVT-AV1 (18.3 Mbps / VMAF 98.44)；VMAF 最高 NVENC AV1 (35.2 Mbps / 98.67)。
* SMALL：码率最低 QSV AV1 (5.7 Mbps / VMAF 97.06)；VMAF 最高 SVT-AV1 (14.7 Mbps / 98.36)。
* 软件编码（x265/SVT-AV1）在多数场景可用明显更低码率接近硬件编码画质，但速度慢 3–50 倍；4K 实时/批量生产仍应使用 NVENC/QSV。
* 夜间高 ISO 舞团片段对所有编码器都困难（VMAF 92–96）；SVT-AV1 HQ 在该片段上效率最好（28.6 Mbps / 95.15）。
* x265 重挡位在 4K 下不具生产可行性；x265 UHQ 的码率甚至触顶 VBV cap（day_action4_pov 106%）。

## 图表

![all encoders](charts/all_encoders_rate_quality.png)
![speed](charts/speed_hq.png)

