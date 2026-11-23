# 2026-11-19 全矩阵测试 PAUSED

- Paused at: 2026-11-19 17:26 (+08:00)
- Reason: 用户要求暂停
- Test processes: none
- Keep-awake: stopped
- Power settings: restored normal

## 已完成

| 项 | 结果 |
|---|---|
| unit gate | 1131 PASS / 2 FAIL（均为 testsets 语料缺失的环境性 FAIL） |
| 硬件矩阵 | 16 cells / 112 输出 / 12:03–15:37，全 rc=0；报告/指标已生成 |
| 对齐矩阵 | 18/18 PASS（`work/alignment_matrix_20261119/matrix.json`） |
| 软件矩阵 | x265 FAST/HQ/SMALL + SVT FAST/HQ/SMALL，6 cells / 42 输出，全 rc=0；metrics 42 行已生成 |

## 未完成/未跑

- 软件报告、图表、总报告、归档（数据已齐，仅分析/文档步骤未做）
- x265 UHQ 2×10 s 样本（P1，未启动）
- 两个长片 C0070 / C0524（不在本轮矩阵内）
- 真实 Sony/DJI 保留 E2E（testsets 缺失，BLOCKED）
- QSV 4:2:2 capable GPU 路径（本机 caps 422=False，BLOCKED）

## 续跑方式

1. 无需重跑编码；先补软件报告/图表/归档：
   ```powershell
   python work\split_sw_metrics_20261119.py
   python work\report_software_20261119.py
   ```
2. 如需 x265 UHQ 样本：用 `work/alignment_smoke` 的 2 条 10 s 片段单独跑。
3. 需要用 11-19 数据与 11-18 交叉校验后再决定归档。
