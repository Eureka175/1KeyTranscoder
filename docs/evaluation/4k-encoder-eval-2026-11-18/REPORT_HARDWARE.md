# 4K 原生 硬件编码器全矩阵（30 s 多场景）

4 个硬件编码器 × UHQ/HQ/SMALL/FAST × 7 条 30 s 只读切片（夜/昼/活动/室内噪点）。

## 测试素材（只读源切片，30 s）

| 片段 | 来源 | 分辨率 | 编码 | fps | 源码率 Mbps |
|---|---|---|---|---|---|
| day_action4_pov | 昼 | 3840×2880 | hevc/yuv420p10le | 29.97 | 112.0 |
| day_daily_canon | 昼 | 3840×2160 | h264/yuv420p | 29.97 | 100.2 |
| day_daily_sony | 昼 | 3840×2160 | hevc/yuv420p10le | 59.97 | 150.4 |
| event_sony_422 | 昼 | 3840×2160 | h264/yuv422p10le | 50.03 | 201.4 |
| indoor_static_noisy_20261117_30s | 昼 | 3840×2160 | hevc/yuv420p10le | 59.94 | 45.0 |
| night_action4_pov | 夜 | 3840×2880 | hevc/yuv420p10le | 29.97 | 66.1 |
| night_dance_iso | 夜 | 3840×2160 | hevc/yuv420p10le | 59.94 | 75.1 |

### 解码路径与编码格式（重要）

| 片段 | 软件解码回退的编码器 | 原因/说明 |
|---|---|---|

* Action4 两条片段（30 fps HEVC）全程硬件解码；其余 5 条片段在 4 个硬件编码器上都退到软件解码。
* QSV 对 `event_sony_422`（H.264 4:2:2 10-bit）编码时降为 4:2:0/10；NVENC HEVC 保留 4:2:2/10。
* `day_daily_canon` 是 8-bit H.264 源：NVENC/QSV 的 HEVC 输出为 4:2:0/8。
* 因此**速度横向比较以 Action4 两条硬解片段为准**；其余片段的速度受软件解码限制，不能代表编码器本身。

## day_action4_pov

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 64.7 Mb / VMAF 99.99 / 9 fps | 49.1 Mb / VMAF 99.98 / 10 fps | 30.1 Mb / VMAF 99.96 / 10 fps | 40.6 Mb / VMAF 99.98 / 13 fps |
| QSV HEVC | 48.4 Mb / VMAF 99.98 / 8 fps | 39.9 Mb / VMAF 99.96 / 12 fps | 26.6 Mb / VMAF 99.91 / 13 fps | 37.5 Mb / VMAF 99.96 / 14 fps |
| NVENC AV1 | 54.8 Mb / VMAF 99.99 / 4 fps | 42.1 Mb / VMAF 99.97 / 9 fps | 19.5 Mb / VMAF 99.87 / 11 fps | 31.8 Mb / VMAF 99.96 / 11 fps |
| QSV AV1 | 77.7 Mb / VMAF 99.99 / 6 fps | 41.9 Mb / VMAF 99.96 / 8 fps | 12.3 Mb / VMAF 99.31 / 11 fps | 21.7 Mb / VMAF 99.77 / 11 fps |

## day_daily_canon

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 40.3 Mb / VMAF 99.84 / 7 fps | 28.0 Mb / VMAF 99.76 / 9 fps | 16.3 Mb / VMAF 99.66 / 9 fps | 23.9 Mb / VMAF 99.75 / 16 fps |
| QSV HEVC | 21.1 Mb / VMAF 99.69 / 7 fps | 16.9 Mb / VMAF 99.62 / 13 fps | 11.3 Mb / VMAF 99.46 / 13 fps | 15.1 Mb / VMAF 99.57 / 16 fps |
| NVENC AV1 | 35.4 Mb / VMAF 99.87 / 8 fps | 25.4 Mb / VMAF 99.75 / 12 fps | 10.5 Mb / VMAF 99.51 / 14 fps | 17.5 Mb / VMAF 99.70 / 14 fps |
| QSV AV1 | 33.8 Mb / VMAF 99.76 / 8 fps | 15.5 Mb / VMAF 99.57 / 10 fps | 5.5 Mb / VMAF 98.88 / 12 fps | 11.0 Mb / VMAF 99.40 / 12 fps |

## day_daily_sony

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 25.0 Mb / VMAF 99.15 / 8 fps | 18.7 Mb / VMAF 99.03 / 10 fps | 12.9 Mb / VMAF 98.84 / 10 fps | 17.4 Mb / VMAF 99.01 / 18 fps |
| QSV HEVC | 15.2 Mb / VMAF 98.99 / 9 fps | 13.0 Mb / VMAF 98.89 / 16 fps | 9.7 Mb / VMAF 98.61 / 16 fps | 12.4 Mb / VMAF 98.81 / 21 fps |
| NVENC AV1 | 30.0 Mb / VMAF 99.32 / 10 fps | 25.6 Mb / VMAF 99.25 / 14 fps | 11.9 Mb / VMAF 98.99 / 16 fps | 17.3 Mb / VMAF 99.16 / 16 fps |
| QSV AV1 | 22.1 Mb / VMAF 98.99 / 11 fps | 13.1 Mb / VMAF 98.66 / 12 fps | 6.0 Mb / VMAF 97.43 / 13 fps | 9.4 Mb / VMAF 98.37 / 15 fps |

## event_sony_422

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 36.7 Mb / VMAF 99.11 / 7 fps | 23.2 Mb / VMAF 99.03 / 10 fps | 14.3 Mb / VMAF 98.67 / 9 fps | 21.4 Mb / VMAF 98.80 / 15 fps |
| QSV HEVC | 13.1 Mb / VMAF 99.00 / 33 fps | 10.4 Mb / VMAF 98.96 / 40 fps | 7.2 Mb / VMAF 98.84 / 40 fps | 9.0 Mb / VMAF 98.92 / 78 fps |
| NVENC AV1 | 32.5 Mb / VMAF 99.17 / 9 fps | 34.0 Mb / VMAF 99.07 / 13 fps | 10.1 Mb / VMAF 98.93 / 16 fps | 17.9 Mb / VMAF 99.06 / 16 fps |
| QSV AV1 | 32.2 Mb / VMAF 99.02 / 40 fps | 9.2 Mb / VMAF 98.77 / 39 fps | 3.5 Mb / VMAF 98.05 / 40 fps | 6.1 Mb / VMAF 98.58 / 75 fps |

## indoor_static_noisy_20261117_30s

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 45.0 Mb / VMAF 97.01 / 8 fps | 25.3 Mb / VMAF 96.75 / 10 fps | 3.7 Mb / VMAF 96.30 / 10 fps | 13.3 Mb / VMAF 96.62 / 19 fps |
| QSV HEVC | 32.9 Mb / VMAF 96.78 / 7 fps | 22.0 Mb / VMAF 96.58 / 15 fps | 3.1 Mb / VMAF 95.93 / 18 fps | 16.7 Mb / VMAF 96.39 / 20 fps |
| NVENC AV1 | 45.4 Mb / VMAF 97.14 / 10 fps | 41.8 Mb / VMAF 96.88 / 14 fps | 11.8 Mb / VMAF 96.36 / 16 fps | 27.4 Mb / VMAF 96.79 / 16 fps |
| QSV AV1 | 46.4 Mb / VMAF 96.94 / 10 fps | 11.5 Mb / VMAF 96.36 / 12 fps | 1.4 Mb / VMAF 95.46 / 14 fps | 6.5 Mb / VMAF 96.16 / 16 fps |

## night_action4_pov

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 27.2 Mb / VMAF 99.93 / 9 fps | 21.1 Mb / VMAF 99.87 / 12 fps | 14.5 Mb / VMAF 99.66 / 12 fps | 19.3 Mb / VMAF 99.77 / 18 fps |
| QSV HEVC | 18.4 Mb / VMAF 99.83 / 10 fps | 16.0 Mb / VMAF 99.76 / 15 fps | 11.8 Mb / VMAF 99.48 / 16 fps | 15.3 Mb / VMAF 99.70 / 21 fps |
| NVENC AV1 | 33.1 Mb / VMAF 99.95 / 9 fps | 31.3 Mb / VMAF 99.97 / 12 fps | 15.5 Mb / VMAF 99.85 / 13 fps | 21.3 Mb / VMAF 99.94 / 13 fps |
| QSV AV1 | 24.6 Mb / VMAF 99.88 / 9 fps | 14.9 Mb / VMAF 99.64 / 10 fps | 5.8 Mb / VMAF 97.49 / 11 fps | 8.8 Mb / VMAF 98.55 / 13 fps |

## night_dance_iso

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 63.6 Mb / VMAF 96.31 / 8 fps | 46.3 Mb / VMAF 95.90 / 10 fps | 17.1 Mb / VMAF 95.03 / 10 fps | 31.7 Mb / VMAF 95.62 / 18 fps |
| QSV HEVC | 31.9 Mb / VMAF 95.21 / 10 fps | 20.9 Mb / VMAF 94.72 / 17 fps | 10.9 Mb / VMAF 94.03 / 16 fps | 20.4 Mb / VMAF 94.58 / 20 fps |
| NVENC AV1 | 64.5 Mb / VMAF 96.48 / 9 fps | 46.3 Mb / VMAF 95.77 / 14 fps | 15.1 Mb / VMAF 94.58 / 16 fps | 29.9 Mb / VMAF 95.34 / 16 fps |
| QSV AV1 | 61.9 Mb / VMAF 96.15 / 9 fps | 23.1 Mb / VMAF 94.89 / 12 fps | 5.2 Mb / VMAF 92.80 / 14 fps | 13.9 Mb / VMAF 94.21 / 15 fps |

## 全片段平均（码率 / VMAF / pipeline fps）

| Encoder | Preset | 视频 Mbps | VMAF | PSNR | SSIM | pipeline fps | encoder fps |
|---|---|---|---|---|---|---|---|
| NVENC HEVC | UHQ | 43.22 | 98.763 | 46.67 | 0.98698 | 7.9 | 21.9 |
| NVENC HEVC | HQ | 30.24 | 98.619 | 45.74 | 0.98439 | 10.2 | 22.0 |
| NVENC HEVC | SMALL | 15.58 | 98.301 | 44.56 | 0.98044 | 9.9 | 19.1 |
| NVENC HEVC | FAST | 23.95 | 98.505 | 45.33 | 0.98301 | 16.8 | 49.3 |
| QSV HEVC | UHQ | 25.86 | 98.496 | 45.22 | 0.98367 | 12.0 | 35.4 |
| QSV HEVC | HQ | 19.86 | 98.355 | 44.70 | 0.98189 | 18.4 | 45.6 |
| QSV HEVC | SMALL | 11.51 | 98.035 | 43.78 | 0.97844 | 18.7 | 43.6 |
| QSV HEVC | FAST | 18.05 | 98.277 | 44.46 | 0.98111 | 27.1 | 77.7 |
| NVENC AV1 | UHQ | 42.24 | 98.846 | 47.14 | 0.98846 | 8.5 | 26.9 |
| NVENC AV1 | HQ | 35.22 | 98.665 | 46.35 | 0.98689 | 12.4 | 64.6 |
| NVENC AV1 | SMALL | 13.49 | 98.298 | 44.38 | 0.98058 | 14.3 | 62.4 |
| NVENC AV1 | FAST | 23.27 | 98.565 | 45.49 | 0.98407 | 14.3 | 94.0 |
| QSV AV1 | UHQ | 42.67 | 98.677 | 46.59 | 0.98739 | 13.5 | 44.7 |
| QSV AV1 | HQ | 18.45 | 98.265 | 44.80 | 0.98201 | 14.6 | 44.0 |
| QSV AV1 | SMALL | 5.66 | 97.060 | 42.42 | 0.97321 | 16.4 | 45.1 |
| QSV AV1 | FAST | 11.08 | 97.863 | 43.67 | 0.97862 | 22.5 | 78.3 |

## 纯硬解速度（仅 Action4 夜/昼 POV，avhw）

| Encoder | Preset | pipeline fps | encoder fps |
|---|---|---|---|
| NVENC HEVC | UHQ | 8.8 | 21.9 |
| NVENC HEVC | HQ | 11.0 | 22.0 |
| NVENC HEVC | SMALL | 10.8 | 19.1 |
| NVENC HEVC | FAST | 15.6 | 49.3 |
| QSV HEVC | UHQ | 8.7 | 35.4 |
| QSV HEVC | HQ | 13.8 | 45.6 |
| QSV HEVC | SMALL | 14.3 | 43.6 |
| QSV HEVC | FAST | 17.8 | 77.7 |
| NVENC AV1 | UHQ | 6.8 | 26.9 |
| NVENC AV1 | HQ | 10.4 | 64.6 |
| NVENC AV1 | SMALL | 11.9 | 62.4 |
| NVENC AV1 | FAST | 11.9 | 94.0 |
| QSV AV1 | UHQ | 7.8 | 44.7 |
| QSV AV1 | HQ | 8.9 | 44.0 |
| QSV AV1 | SMALL | 10.9 | 45.1 |
| QSV AV1 | FAST | 12.1 | 78.3 |

## 夜景 vs 白天（全片段平均）

| Encoder | Preset | 夜 Mbps/VMAF | 昼 Mbps/VMAF |
|---|---|---|---|
| NVENC HEVC | UHQ | 45.4/98.12 | 42.3/99.02 |
| NVENC HEVC | HQ | 33.7/97.89 | 28.9/98.91 |
| NVENC HEVC | SMALL | 15.8/97.34 | 15.5/98.69 |
| NVENC HEVC | FAST | 25.5/97.69 | 23.3/98.83 |
| QSV HEVC | UHQ | 25.2/97.52 | 26.1/98.89 |
| QSV HEVC | HQ | 18.4/97.24 | 20.4/98.80 |
| QSV HEVC | SMALL | 11.4/96.75 | 11.6/98.55 |
| QSV HEVC | FAST | 17.8/97.14 | 18.1/98.73 |
| NVENC AV1 | UHQ | 48.8/98.21 | 39.6/99.10 |
| NVENC AV1 | HQ | 38.8/97.87 | 33.8/98.98 |
| NVENC AV1 | SMALL | 15.3/97.21 | 12.8/98.73 |
| NVENC AV1 | FAST | 25.6/97.64 | 22.4/98.93 |
| QSV AV1 | UHQ | 43.2/98.02 | 42.4/98.94 |
| QSV AV1 | HQ | 19.0/97.27 | 18.2/98.66 |
| QSV AV1 | SMALL | 5.5/95.15 | 5.7/97.83 |
| QSV AV1 | FAST | 11.4/96.38 | 10.9/98.46 |

## 码率控制撞墙（均值/峰值 vs cap）

按片段计算后汇总；判定看**任一片段**是否贴 cap。

| Encoder | Preset | 平均 mean/cap | 最高 mean/cap | 最高 p95/cap | 最高峰值/cap | 撞墙片段数 | 判定 |
|---|---|---|---|---|---|---|---|
| NVENC HEVC | UHQ | 62% | 100% | 104% | 104% | 3 | 撞墙 |
| NVENC HEVC | HQ | 53% | 88% | 103% | 110% | 2 | 撞墙 |
| NVENC HEVC | SMALL | 41% | 90% | 107% | 111% | 1 | 撞墙 |
| NVENC HEVC | FAST | 49% | 91% | 106% | 114% | 1 | 撞墙 |
| QSV HEVC | UHQ | 26% | 48% | 64% | 67% | 0 | 未撞 |
| QSV HEVC | HQ | 25% | 50% | 67% | 70% | 0 | 未撞 |
| QSV HEVC | SMALL | 29% | 66% | 93% | 93% | 0 | 接近 |
| QSV HEVC | FAST | 30% | 62% | 83% | 90% | 0 | 接近 |
| NVENC AV1 | UHQ | 61% | 100% | 120% | 131% | 2 | 撞墙 |
| NVENC AV1 | HQ | 61% | 92% | 104% | 106% | 2 | 撞墙 |
| NVENC AV1 | SMALL | 35% | 58% | 74% | 77% | 0 | 未撞 |
| NVENC AV1 | FAST | 48% | 71% | 83% | 85% | 0 | 接近 |
| QSV AV1 | UHQ | 42% | 78% | 112% | 125% | 0 | 接近 |
| QSV AV1 | HQ | 23% | 52% | 82% | 94% | 0 | 未撞 |
| QSV AV1 | SMALL | 14% | 31% | 44% | 53% | 0 | 未撞 |
| QSV AV1 | FAST | 18% | 36% | 55% | 64% | 0 | 未撞 |

撞墙明细（mean/cap≥80%）：

* indoor_static_noisy_20261117_30s / NVENC AV1 / UHQ: mean 100% / p95 120% / peak 131%
* indoor_static_noisy_20261117_30s / NVENC HEVC / UHQ: mean 100% / p95 104% / peak 104%
* night_dance_iso / NVENC AV1 / UHQ: mean 99% / p95 125% / peak 127%
* night_dance_iso / NVENC HEVC / UHQ: mean 98% / p95 104% / peak 107%
* indoor_static_noisy_20261117_30s / NVENC AV1 / HQ: mean 92% / p95 104% / peak 106%
* day_action4_pov / NVENC HEVC / FAST: mean 91% / p95 106% / peak 114%
* day_action4_pov / NVENC HEVC / SMALL: mean 90% / p95 107% / peak 111%
* day_action4_pov / NVENC HEVC / HQ: mean 88% / p95 103% / peak 110%
* day_action4_pov / NVENC HEVC / UHQ: mean 82% / p95 96% / peak 101%
* night_dance_iso / NVENC AV1 / HQ: mean 81% / p95 97% / peak 97%
* night_dance_iso / NVENC HEVC / HQ: mean 81% / p95 92% / peak 98%

## 主要发现

* UHQ 全片段平均 VMAF 最高：NVENC AV1 (98.846)；最低：QSV HEVC (98.496)。
* HQ 全片段平均 VMAF 最高：NVENC AV1 (98.665)；最低：QSV AV1 (98.265)。
* SMALL 全片段平均 VMAF 最高：NVENC HEVC (98.301)；最低：QSV AV1 (97.060)。
* FAST 全片段平均 VMAF 最高：NVENC AV1 (98.565)；最低：QSV AV1 (97.863)。
* UHQ 全片段平均码率最低：QSV HEVC (25.9 Mbps)；最高：NVENC HEVC (43.2 Mbps)。
* HQ 全片段平均码率最低：QSV AV1 (18.5 Mbps)；最高：NVENC AV1 (35.2 Mbps)。
* SMALL 全片段平均码率最低：QSV AV1 (5.7 Mbps)；最高：NVENC HEVC (15.6 Mbps)。
* FAST 全片段平均码率最低：QSV AV1 (11.1 Mbps)；最高：NVENC HEVC (23.9 Mbps)。
* NVENC HQ：AV1 相对 HEVC 码率 116%，VMAF 98.665 vs 98.619。
* HQ：QSV HEVC 相对 NVENC HEVC 码率 66%，VMAF 98.355 vs 98.619。
* 按片段 mean/cap≥80% 的格子共 11 个。
  * indoor_static_noisy_20261117_30s / nvenc-av1 / uhq: mean/cap≈100%，p95/cap≈120%，peak/cap≈131%
  * indoor_static_noisy_20261117_30s / nvenc / uhq: mean/cap≈100%，p95/cap≈104%，peak/cap≈104%
  * night_dance_iso / nvenc-av1 / uhq: mean/cap≈99%，p95/cap≈125%，peak/cap≈127%
  * night_dance_iso / nvenc / uhq: mean/cap≈98%，p95/cap≈104%，peak/cap≈107%
  * indoor_static_noisy_20261117_30s / nvenc-av1 / hq: mean/cap≈92%，p95/cap≈104%，peak/cap≈106%
  * day_action4_pov / nvenc / fast: mean/cap≈91%，p95/cap≈106%，peak/cap≈114%
  * day_action4_pov / nvenc / small: mean/cap≈90%，p95/cap≈107%，peak/cap≈111%
  * day_action4_pov / nvenc / hq: mean/cap≈88%，p95/cap≈103%，peak/cap≈110%

## 图表

![aggregate](charts/aggregate_rate_quality.png)
![by clip](charts/vmaf_by_clip.png)

## 方法学与限制

* 7 条 30 s 只读切片：Action4 夜 POV、舞团夜高 ISO、Action4 昼 POV、202610 白天（Sony 10-bit / Canon 8-bit）、I:2026.09.24 活动（Sony 4:2:2 10-bit）、室内静态高噪点 rec709（20261117）。
* 4 硬件编码器 × 4 挡位；每作业一次处理 7 条片段。
* 画质：全片段（30 s）VMAF/PSNR/SSIM，1280 宽等比缩放，硬件解码。
* 码率：仅视频包；cap 来自 1kt 产品日志（NVENC 动态 VBV、QSV 静态表）。
* 速度：encoder fps 来自编码器自身日志；pipeline fps 含探测/复核/封装开销。
* 片段来自真实素材的不同卷/相机；分辨率/编码/fps 不同（4:3 与 16:9、8/10-bit、4:2:2），横向比较以“同片段内相对排序”为主。
* 本矩阵只覆盖硬件编码器；x265/SVT-AV1 软件编码另测（4K 软件极慢）。

