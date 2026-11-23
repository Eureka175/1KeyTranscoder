# 4K 评估图表包

数据源：硬件矩阵 96 输出 + x265 20 输出 + SVT-AV1 18 输出。
所有曲线使用完整 30 s 片段；x265 UHQ 10 s 样本不进入曲线。

* 图表合集 PDF：[`4K评估图表合集.pdf`](4K评估图表合集.pdf)
* 缩略总览：![contact](00_contact_sheet.png)

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

## 夜 vs 昼 VMAF（HQ/FAST）

![06_night_vs_day.png](06_night_vs_day.png)

## 挡位梯度（码率/VMAF）

![07_preset_ladder.png](07_preset_ladder.png)

## 相对 NVENC HEVC 的效率

![08_efficiency_vs_nvenc.png](08_efficiency_vs_nvenc.png)

