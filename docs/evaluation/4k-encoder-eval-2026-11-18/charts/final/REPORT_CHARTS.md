# 4K 评估图表包

数据源：硬件矩阵 112 输出（7 片段）+ 软件矩阵 35 输出（x265 14 / SVT-AV1 21）。

* 合集 PDF：[`4K_20261118图表合集.pdf`](4K_20261118图表合集.pdf)
* 缩略总览：![contact](00_contact_sheet.png)
所有曲线使用完整 30 s 片段；x265 UHQ 10 s 样本不进入曲线。

## 各片段码率-质量曲线（6 编码器 × 挡位）

![01_rate_quality_by_clip.png](01_rate_quality_by_clip.png)

## 全片段平均码率-质量

![02_rate_quality_aggregate.png](02_rate_quality_aggregate.png)

## 速度-质量 & 纯硬解 encoder fps

![03_speed_quality.png](03_speed_quality.png)

## 码控撞墙（实际 vs cap）

![04_rate_control_wall.png](04_rate_control_wall.png)

## HQ 挡位 VMAF/码率热图

![05_hq_heatmaps.png](05_hq_heatmaps.png)

## 夜 vs 非夜 VMAF（HQ/FAST；含室内噪点）

![06_night_vs_day.png](06_night_vs_day.png)

## 挡位梯度（码率/VMAF）

![07_preset_ladder.png](07_preset_ladder.png)

## 相对 NVENC HEVC 的效率

![08_efficiency_vs_nvenc.png](08_efficiency_vs_nvenc.png)

## QSV ICQ 下调效果（旧→新）

![09_qsv_icq_effect.png](09_qsv_icq_effect.png)


## 重要注记（2026-11-18 肉眼复核）

- 夜间高噪点/颗粒场景，肉眼判断 **x265 明显优于 NVENC**；
- 01/02/05/08 等基于 VMAF 的图在噪点场景与肉眼排序不一致，VMAF 仅作参考；
- 归档选型以肉眼颗粒保真为准，不以本图包 VMAF 排名直接定案。
