# v0.9.11 发布说明 — QSV 缩放与 Sony AVC 保留修复

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.11`；`VERSION` = `0.9.11`）  
> **基线**：`v0.9.10`  
> **日期**：2026-11-08

## 修复

### QSV 缩放参数

QSV 不接受公开算法名 `lanczos`：

```text
--vpp-resize algo=lanczos   -> QSV 拒绝整个编码
```

现在对外仍为 `--resize-algo lanczos`，内部映射为 QSV/NVENC 均支持的 `lanczos3`。

### Sony AVC 保留路径

AVC Sony `rtmd` 素材的保留输出曾把视频轨复位为：

```text
video.timescale 50000 -> 5000
video.stts      1000  -> 100
```

新增 `isobmf.patch_track_timescale()`，在 GPAC 重建后按源视频 timescale
恢复 mdhd/stts/ctts/elst，随后再执行时长修复。

## 验证

- Unit：1083 PASS / 2 FAIL（Sony 语料缺失）。
- C0155 AVC Sony：
  - NVENC / QSV / x265 / SVT-AV1 全部 **44 PRESERVED / 0 MODIFIED**；
  - Gyroflow PASS。
- FX30 AVC Sony QSV：**44 PRESERVED / 0 MODIFIED**。
- QSV `--scale 0.5` / `--resize 1600x900`：PASS。

## 长测结论

v0.9.10 长测 34 例，修正 R12 断言后 **24 PASS / 10 FAIL**。  
其中：

- Sony AVC 保留失败：本版修复；
- QSV lanczos 失败：本版修复；
- C1305 长片结构保留通过；Gyroflow 在超长导出上出现外部内存/JSON 失败；
- L1 超时与 F0 失败由长测输出占满工作盘引起，测试脚本已改为验证后清理输出。

## 包

```text
1KeyTranscoder-v0.9.11-win64-selfcontained.zip
```
