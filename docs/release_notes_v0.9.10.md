# v0.9.10 发布说明 — 落盘最小化补全

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.10`；`VERSION` = `0.9.10`）  
> **基线**：`v0.9.9`  
> **日期**：2026-11-07

## 本版内容

- 补全 classic 硬件路径的 per-job 落盘清理：
  - 交付成功、postprobe 完成后删除 `<job>` 临时目录；
  - Sony/DJI 路径原有清理保持不变；
  - `--keep-work` 仍可保留；
  - 失败任务仍保留，便于断点排查。
- 实测成功后 `.1ktwork/` 仅保留 `caps/` 能力缓存，不再残留 channel-sync 临时目录。
- 既有落盘最小化措施确认仍然生效：
  - Sony 保留路径不生成 NHML；
  - `samples.bin` 哈希完成后立即删除；
  - 保留报告复制到 `logs/preserve_reports/` 后再 GC；
  - MP4Box 趟数合并。

## 验证

- Unit：1083 PASS / 2 FAIL（Sony 语料缺失）。
- 经典 NVENC 成功路径实测：`.1ktwork/` 仅剩 `caps/`。
- 包完整性：63/63 PASS。

## 包

```text
1KeyTranscoder-v0.9.10-win64-selfcontained.zip
```

**完整变更记录**：`git log v0.9.9..v0.9.10`。
