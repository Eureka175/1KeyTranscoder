# 4K 编码器评估总报告（硬件全矩阵 + x265 / SVT-AV1）

本报告汇总三组测试：

1. **硬件全矩阵**：4 硬件编码器 × 4 挡位 × 7 条 30 s 片段（112 输出，3.6 h）→ `work/hw4k_matrix_20261118/REPORT.md`
2. **x265**：FAST/HQ 7 片段 → `work/software_eval_20261118/REPORT.md`
3. **SVT-AV1**：FAST/HQ/SMALL 7 片段 → `work/software_eval_20261118/REPORT.md`

## 素材

* Action4 夜 POV（4K 4:3 10-bit HEVC）、舞团夜高 ISO（4K60 10-bit HEVC）
* Action4 昼 POV、202610 白天（Sony 10-bit HEVC / Canon 8-bit H.264）
* I:\2026.09.24 活动（Sony 4:2:2 10-bit H.264）
* 室内静态高噪点 rec709（20261117，新增 30 s 片段）

## 核心结论

1. **速度**：NVENC 最快（FAST 硬解片段 49–94 encoder fps），QSV 次之（48–84）；x265 FAST 5.8–14.3，x265 HQ 0.7–3.1；SVT-AV1 FAST 3.3–23，HQ 2.6–12；x265 UHQ ~0.24 fps、SVT UHQ ~0.02 fps，4K 重挡位不可生产。
2. **画质/码率**：UHQ/HQ 最高 VMAF 为 NVENC AV1；NVENC HEVC 紧随；QSV 下调 ICQ 后码率与 VMAF 均明显上升（见硬件总结）。**但夜间高噪点/颗粒场景经肉眼复核为 x265 明显更好，VMAF 与颗粒保真不一致，归档结论以肉眼为准。**
3. **软件效率**：x265 FAST 用 0.44–0.93× NVENC HEVC 的码率达到接近画质；SVT-AV1 FAST/HQ 用 0.33–0.84× 码率达到接近/相当画质，但速度慢 3–10 倍（相对硬件）。
4. **撞墙**：NVENC 在 4K 高复杂度场景（舞团夜、Action4 昼、室内噪点）顶到动态 VBV cap（mean/cap 82–100%）；QSV 本轮仍未撞。
5. **推荐（已按肉眼复核修正）**：批量/实时 4K 用 NVENC HEVC FAST/HQ；**质量归档（夜间/高噪点/颗粒）用 x265 HQ（母版）**，时间完全不受限可评估 x265 UHQ；x265 SMALL 仅作体积受限交付；AV1 不用于归档。

## 硬件全矩阵摘要（全片段平均，Mbps / VMAF）

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| NVENC HEVC | 43.2 / 98.76 | 30.2 / 98.62 | 15.6 / 98.30 | 23.9 / 98.51 |
| NVENC AV1 | 42.2 / 98.85 | 35.2 / 98.67 | 13.5 / 98.30 | 23.3 / 98.56 |
| QSV HEVC | 25.9 / 98.50 | 19.9 / 98.35 | 11.5 / 98.03 | 18.1 / 98.28 |
| QSV AV1 | 42.7 / 98.68 | 18.5 / 98.27 | 5.7 / 97.06 | 11.1 / 97.86 |

## 软件摘要（完整 30 s 平均，Mbps / VMAF / pipeline fps）

| Encoder | UHQ | HQ | SMALL | FAST |
|---|---|---|---|---|
| x265 | — | 34.1 / 98.34 / 1.6 | — | 17.9 / 98.13 / 8.2 |
| SVT-AV1 | — | 18.3 / 98.44 / 7.0 | 14.7 / 98.36 / 7.0 | 16.0 / 98.25 / 11.7 |

## 文件

* 硬件矩阵：`work/hw4k_matrix_20261118/REPORT.md` / `metrics.csv`；原始输出 `runs/`
* 软件矩阵：`work/software_eval_20261118/REPORT.md` / `metrics_software.csv`；原始输出 `work/sw4k_matrix_20261118/runs/`（x265 + SVT-AV1）
* 只读切片：`work/hw4k_matrix_20261118/clips/`（7×30 s）+ `extra_clips/`（9 条备用素材）
* 码控撞墙明细：`work/hw4k_matrix/rc_wall.txt`


## 肉眼复核（用户实测，2026-11-18）

- 夜间高噪点片段：x265 噪点/颗粒保留明显优于 NVENC；
- VMAF 排序相反（x265 HQ 37.1/94.53 vs NVENC UHQ 63.6/96.31），说明 VMAF 不适合作为颗粒保真判据；
- 质量归档母版改为 **x265 HQ**，x265 UHQ 待验证，SMALL 不作母版。
