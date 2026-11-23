# 1KeyTranscoder v0.9.17 发布说明

> 2026-11-21 · 修复 NVENC 日志残留、日志目录位置与 dashboard 计时

## 修复

### 1. NVENC/QSV 原始编码日志不再残留在成品目录

硬件路径此前把编码器 stdout 写在成品旁边的
`<stem>.part.mov.nvencc.log`（QSV 为 `.qsvencc.log`），成功交付后仍然保留，
导致输出目录里每个视频都多一个日志文件。

现在原始日志写入该任务的 `.1ktwork/<job>/encode.<label>.log`：

- 成功：工作目录随任务 GC 清理，**成品目录只留成品和 `logs/`**；
- 失败：工作目录保留，原始日志可供排查。

### 2. 日志目录移入输出目录

`logs/` 默认位置从“输入目录的父目录”改为 **`<output>/logs/`**：

- `total.log`、`preprobe.csv`、`postprobe.csv`、`failed_files.json`、
  `preserve_reports/`、`files/` 都在输出目录内；
- 每个输出目录自带完整日志，不再污染输入目录的父级；
- 旧输出目录中的历史日志不会被自动移动或删除。

### 3. dashboard 实际开始时间 / 编码耗时

此前所有任务在排队时就被写入同一个 `started` 时间戳，`elapsed` 从该排队
时刻计算，因此越靠后的任务耗时看起来越长（实际是队列等待时间）。

现在：

- 任务入队时写 `admitted`；
- 真正开始处理时写 `started`，并记录 `queue_sec`；
- `elapsed` 只从 `started` 计算，运行中也会持续更新；
- 未真正开始的任务 `elapsed=null`，不再伪造耗时。

## 验证

- 小样本 NVENC（3 个 4K 10s 片段、`--jobs 1`）：
  - 输出目录只有成品 `.MP4`、`logs/`、`.1ktwork/caps/`；
  - 无任何 `*.nvencc.log` 残留；
  - dashboard 三条任务 `started` 不同，`elapsed` 分别为实际编码耗时；
- 单元测试：**1133 PASS / 2 环境性 FAIL**（真实 Sony 语料缺失）。

## 影响范围

- 日志位置变化会影响按旧路径读取日志/失败列表的脚本；新路径为
  `<output>/logs/`。
- 已存在的旧输出目录不会被重排；如需归位请手动处理。
- 输出视频、编码参数、元数据策略均未改变。
