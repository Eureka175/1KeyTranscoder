# v0.9.9 发布说明 — classic 流策略统一

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.9`；`VERSION` = `0.9.9`）  
> **基线**：`v0.9.8`  
> **日期**：2026-11-07

## 本版内容

- classic 软件路径（x265 / SVT-AV1）使用同一流策略：
  - 主视频 + 音频 + 字幕；
  - data / attached-picture 流不再按编码器分叉。
- AV1 `stts` data 轨问题：
  - 根因：FFmpeg MP4 muxer 无法为 `codec=none` 的 data 流建立 sample entry；
  - x265 的 MOV 中间容器曾保留该轨，AV1 的 MP4 中间容器无法保留；
  - 现在两条 classic 路径都明确丢弃 data/attached 流，输出流清单一致。
- Sony/DJI 保留管线不受影响，元数据仍由保留管线重建。
- 详细调查：[`design/av1_stts_data_track.md`](design/av1_stts_data_track.md)。

## 验证

- Unit：1083 PASS / 2 FAIL（Sony 语料缺失）。
- Toolchain：1104 PASS / 2 FAIL（Sony 语料缺失）。
- 同一 4K 源实测：
  - x265 classic：1920×1080，视频 + 音频；
  - SVT-AV1 classic：1920×1080，视频 + 音频。

## 包

```text
1KeyTranscoder-v0.9.9-win64-selfcontained.zip
```

**完整变更记录**：`git log v0.9.8..v0.9.9`。
