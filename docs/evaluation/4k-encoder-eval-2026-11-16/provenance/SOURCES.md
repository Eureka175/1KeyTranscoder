# 素材来源与只读声明

本评估使用用户明确授权、**以只读方式**访问的素材；所有裁剪/探测均写入 `work/`，源目录未改动。

## 主矩阵 6 条 30 s 片段

| 片段 | 来源文件 | 窗口 | 分辨率/编码/fps | 帧数 | 片段 sha256（前 16） |
|---|---|---|---|---|---|
| night_action4_pov（Action4 夜 POV） | F:\Pictures\2026\202611\20261114（DJI Action4 分段录制） | 30 s 干净片段；源自 2026-11-15 评估的 clips30，原始段号未保留 | 3840x2880 / hevc / 29.97 | 898 | fc97a5dd11884caa… |
| day_action4_pov（Action4 昼 POV） | F:\Pictures\2026\202611\20261115（DJI Action4 分段录制） | 30 s 干净片段；源自 2026-11-15 评估的 clips30，原始段号未保留 | 3840x2880 / hevc / 29.97 | 898 | c61f39d408aa058a… |
| night_dance_iso（舞团夜高 ISO） | F:\Pictures\2026\202609\20260919舞团\20260919_C1305.MP4 | 600–630 s | 3840x2160 / hevc / 59.94 | 1823 | 9de443f366eccd59… |
| day_daily_sony（日常 Sony 10-bit） | F:\Pictures\2026\202610\20261025\20261025_C1515.MP4 | 30–60 s | 3840x2160 / hevc / 59.974 | 1797 | 4914e35471cacfcb… |
| day_daily_canon（日常 Canon 8-bit） | F:\Pictures\2026\202610\20261025\A_0001C049A261025_1633145C_CANON.MP4 | 20–50 s | 3840x2160 / h264 / 29.97 | 899 | b8985e49ea841c73… |
| event_sony_422（活动 Sony 4:2:2） | I:\2026.09.24\FX3\C0524.MP4 | 300–330 s | 3840x2160 / h264 / 50.033 | 1525 | f2c0faedaca7e012… |

> Action4 夜/昼 POV 的切片来自 2026-11-15 的评估工作目录（`work/encoder_eval/clips30/`）；当时未记录对应的 DJI 分段文件号，因此这里只保留到“日期目录 + 片段文件 sha256”一级。

## 备用切片（2026-11-16 并行裁剪，9 条）

| 名称 | 来源 | 起点 | 长度 | MB |
|---|---|---|---|---|
| night_dance_iso_b | F:\Pictures\2026\202609\20260919舞团\20260919_C1306.MP4 | 600 s | 30 s | 272.1 |
| night_dance_iso_c | F:\Pictures\2026\202609\20260919舞团\20260919_C1305.MP4 | 1800 s | 30 s | 270.8 |
| day_pocket_202610 | F:\Pictures\2026\202610\20261025\DJI_20261025160559_0088_D.MP4 | 20 s | 30 s | 428.7 |
| day_canon_b_202610 | F:\Pictures\2026\202610\20261025\A_0001C049A261025_1633145C_CANON.MP4 | 60 s | 30 s | 358.1 |
| day_sony_20261018 | F:\Pictures\2026\202610\20261018\20261018_C1456.MP4 | 10 s | 30 s | 535.9 |
| day_action4_202611_0025 | F:\Pictures\2026\202611\20261115\DJI_20261115164247_0025_D.MP4 | 60 s | 30 s | 380.1 |
| event_a7m4 | I:\2026.09.24\A7M4\C0152.MP4 | 300 s | 30 s | 727.9 |
| event_fx30 | I:\2026.09.24\FX30\20260922_fx30_2_9765.MP4 | 300 s | 30 s | 727.6 |
| event_nx800 | I:\2026.09.24\HXR-NX800\CA0230.MP4 | 300 s | 30 s | 724.1 |

## 原始输出位置（体积大，不入库）

* 硬件：`work/hw4k_matrix/runs/<preset>/<encoder>/out/`（16 作业 / 96 视频）
* x265：`work/sw4k_matrix/runs/<preset>/x265/out/`（4 挡位，UHQ 为 2×10 s）
* SVT-AV1：`work/svt4k_matrix/runs/<preset>/svtav1/out/`（3 挡位）

上述目录已被 `.gitignore` 覆盖（`work/`），但归档中的指标、报告、图表和脚本足以复核结论；重跑方法见 `README.md`。
