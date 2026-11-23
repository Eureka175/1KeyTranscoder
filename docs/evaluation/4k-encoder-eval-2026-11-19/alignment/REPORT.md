# 对齐测试矩阵结果（2026-11-19）

6 编码器 × 3 源类型 × FAST 挡，10 s 片段；全部 18 项格式断言 PASS。

| 源 | nvenc | qsv | x265 | nvenc-av1 | qsv-av1 | svtav1 |
|---|---|---|---|---|---|---|
| canon_8bit_10s | yuv420p | yuv420p | yuv420p | yuv420p10le | yuv420p10le | yuv420p10le |
| dji_action4_10s | yuv420p10le | yuv420p10le | yuv420p10le | yuv420p10le | yuv420p10le | yuv420p10le |
| generic_422_10s | yuv422p10le | yuv420p10le | yuv422p10le | yuv420p10le | yuv420p10le | yuv420p10le |

## 备注

* 本机 QSV（Arc 140T）caps `422=False`，4:2:2 源按策略转 420/10；
* AV1 三后端统一 420/10（8-bit Canon 源也输出 10-bit）；
* 硬件 8-bit profile 覆盖缺陷已修复：nvenc/qsv 的 Canon 输出为 main/8-bit；
* 元数据策略单元/真实源判定已覆盖；完整 Sony/DJI 保留 E2E 需真实语料，标 BLOCKED。

原始 JSON：`work/alignment_matrix_20261119_20261119/matrix.json`

