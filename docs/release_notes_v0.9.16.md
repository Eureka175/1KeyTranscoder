# 1KeyTranscoder v0.9.16 发布说明

> 2026-11-20 · 生产质量参数应用：NVENC +2、x265 +2

## 本次变更

将 2026-11-20 q2 验证过的 **+2 质量参数**正式写入生产配置：

| 配置 | 原值 | v0.9.16 |
|---|---|---|
| NVENC HEVC `qvbr` UHQ/HQ/SMALL/FAST | 23/25/28/26 | **25/27/30/28** |
| NVENC AV1 `qvbr` UHQ/HQ/SMALL/FAST | 26/30/38/34 | **28/32/40/36** |
| x265 `crf` UHQ/HQ/SMALL/FAST | 21/23/25/24 | **23/25/27/26** |

QSV 参数不变（继续使用 11-18 的 ICQ 20/21/23/22 与 22/25/31/27）。

## 依据

2026-11-20 q2 关键 cell 测试（`work/longtest_20261120_q2/REPORT.md`）：

- NVENC +2：同 7 片段平均码率下降 **13–23%**，VMAF 仅降 **0.02–0.10**；
- Action4 昼 NVENC FAST/HQ 撞墙基本解决（mean/cap 91→68%、88→66%）；
- 室内噪点 NVENC HEVC/AV1 UHQ 仍撞 cap（约 99–100%），需要后续 +3/+4 或调整 cap；
- x265 HQ +2：Action4 昼 / 室内噪点 / 舞团夜码率下降 21.5–51.6%，撞墙消除；
  舞团夜 VMAF 损失约 0.878，属于画质/体积取舍。

原值完整备份：`work/param_backup_20261120_original/`。  
+2 快照来源：`work/param_backup_20261120_q2/`。

## 说明

- 本次没有重新跑完整矩阵；应用依据是 q2 关键 cell 数据。
- 室内静态高噪点的 UHQ 硬撞墙仍未彻底解决；需要更大 +3/+4 或提高 cap。
- 输出格式策略沿用 v0.9.15：默认 10-bit、色度跟随输入，支持
  `--output-depth` / `--output-chroma` 手动覆盖。
